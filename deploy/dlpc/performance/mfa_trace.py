"""Bounded request-local MFA timing; no text, audio, paths or model state in output."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json
import os
from pathlib import Path
import re
import resource
import threading
import time

CURRENT = ContextVar('mfa_observation', default=None)
METHODS = ('setup', 'setup_database', 'load_corpus', '_load_corpus_from_source_mp',
           'dictionary_setup', 'initialize_jobs', 'normalize_text', 'generate_features',
           'compile_train_graphs', 'align_utterances', 'analyze_alignments', 'export_files', 'cleanup',
           'initialize_database', 'setup_acoustic_model', '_load_corpus', 'get_tokenizers',
           'apply_phonological_rules', 'calculate_disambiguation', 'write_lexicon_information',
           'build_lexicon_compiler', 'mfcc', 'combine_feats', 'calc_cmvn',
           'generate_final_features', '_write_feats', 'create_corpus_split')
LIMIT = 256


def usage():
    own = resource.getrusage(resource.RUSAGE_SELF)
    child = resource.getrusage(resource.RUSAGE_CHILDREN)
    return dict(cpu=time.process_time(), childCpu=child.ru_utime + child.ru_stime,
        voluntary=own.ru_nvcsw, involuntary=own.ru_nivcsw, inputBlocks=own.ru_inblock,
        outputBlocks=own.ru_oublock, lifetimePeakRssKiB=own.ru_maxrss)


def resources():
    result = dict(affinity=None, threadEnvironment={}, cgroup={}, pssKiB=None)
    try:result['affinity'] = sorted(os.sched_getaffinity(0))
    except (OSError, AttributeError):pass
    for name in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        value = os.environ.get(name, '')
        result['threadEnvironment'][name] = int(value) if re.fullmatch(r'[0-9]{1,6}', value) else None
    # Namespace root only. Missing files are unknown; never infer unlimited resources.
    for name in ('cpu.max', 'cpu.stat', 'cpuset.cpus.effective', 'memory.max', 'memory.current'):
        try:
            value = (Path('/sys/fs/cgroup') / name).read_text()[:4096].strip()
            if re.fullmatch(r'[a-z0-9_,.\- \n]+', value):result['cgroup'][name] = value
        except OSError:pass
    result['cgroupScope'] = 'namespace-root counters; may include other services; ancestry not inferred'
    return result


class Trace:
    def __init__(self):
        self.origin = time.perf_counter()
        self.events = []
        self.local = threading.local()
        self.lock = threading.RLock()
        self.dropped = 0
        self.unavailable = []
        self.record = None
        self.folder = None

    @contextmanager
    def span(self, name):
        stack = getattr(self.local, 'stack', [])
        before, start = usage(), time.perf_counter() - self.origin
        with self.lock:
            if len(self.events) >= LIMIT:
                self.dropped += 1
                event = None
            else:
                event = dict(id=len(self.events), parentId=stack[-1] if stack else (0 if self.events else None),
                    name=name, startSeconds=start, endSeconds=None, wallSeconds=None,
                    exclusiveWallSeconds=None, status='OPEN')
                self.events.append(event)
        if event is not None:
            stack = list(stack) + [event['id']]
            self.local.stack = stack
        try:
            yield
        except BaseException:
            if event is not None:event['status'] = 'ERROR'
            raise
        finally:
            after, end = usage(), time.perf_counter() - self.origin
            if event is not None:
                with self.lock:
                    event.update(endSeconds=end, wallSeconds=end-start,
                        processCpuSeconds=after['cpu']-before['cpu'],
                        reapedChildCpuSeconds=after['childCpu']-before['childCpu'],
                        voluntaryContextSwitches=after['voluntary']-before['voluntary'],
                        involuntaryContextSwitches=after['involuntary']-before['involuntary'],
                        inputBlocks=after['inputBlocks']-before['inputBlocks'],
                        outputBlocks=after['outputBlocks']-before['outputBlocks'],
                        processLifetimePeakRssKiB=after['lifetimePeakRssKiB'])
                    if event['status'] == 'OPEN':event['status'] = 'OK'
                self.local.stack = stack[:-1]

    def report(self):
        with self.lock:
            events = [dict(event) for event in self.events]
        for event in events:
            if event['endSeconds'] is None:continue
            children = sorted((max(event['startSeconds'], c['startSeconds']),
                               min(event['endSeconds'], c['endSeconds'])) for c in events
                              if c['parentId'] == event['id'] and c['endSeconds'] is not None)
            covered, right = 0.0, event['startSeconds']
            for start, end in children:
                if end > max(start, right):covered += end-max(start, right)
                right = max(right, end)
            event['exclusiveWallSeconds'] = max(0.0, event['wallSeconds']-covered)
        return dict(revision='mfa-request-spans-v1', spans=events, droppedSpans=self.dropped,
            unavailableMethods=list(self.unavailable), resources=getattr(self, 'resource_snapshot', None),
            semantics='inclusive wall spans; exclusive subtracts union of direct-child intervals; CPU/IO counters overlap',
            complete=self.dropped == 0 and all(e['endSeconds'] is not None for e in events))


@contextmanager
def span(name):
    trace = CURRENT.get()
    if trace is None:
        yield
    else:
        with trace.span(name):yield


def bind(record, folder):
    trace = CURRENT.get()
    if trace is not None:
        trace.record, trace.folder = record, Path(folder)
        with trace.span('observation_setup'):trace.resource_snapshot = resources()


def attach(aligner):
    trace = CURRENT.get()
    if trace is None:return
    for name in METHODS:
        original = getattr(aligner, name, None)
        if callable(original):setattr(aligner, name, _wrapped(original, trace, name))
        else:trace.unavailable.append(name)


def _wrapped(original, trace, name):
    @wraps(original)
    def measured(*args, **kwargs):
        with trace.span(name):return original(*args, **kwargs)
    return measured


@contextmanager
def cli_context(command, *args):
    with span('cli_context_create'):context = command.make_context(*args)
    with context as value:yield value


def request_trace(original):
    @wraps(original)
    def measured(*args, **kwargs):
        trace = Trace()
        token = CURRENT.set(trace)
        try:
            with trace.span('worker_request'):return original(*args, **kwargs)
        finally:
            try:
                report = trace.report()
                if trace.record is not None:
                    trace.record['mfaPerformance'] = report
                    rid = trace.record.get('request_id', '')
                    if trace.folder is not None and re.fullmatch(r'g[1-4]_r[0-2]_[a-f0-9]{12}', rid):
                        try:
                            # Separate bounded diagnostics also survive a workflow error.
                            with (trace.folder / (rid + '_performance.json')).open('x') as stream:
                                json.dump(report, stream, separators=(',', ':'))
                        except OSError:report['diagnosticPersistence'] = 'FAILED'
            except Exception:
                if trace.record is not None:
                    trace.record['mfaPerformance'] = dict(revision='mfa-request-spans-v1',
                        complete=False, observationError='UNAVAILABLE')
            finally:
                CURRENT.reset(token)
                trace.record = None
    return measured
