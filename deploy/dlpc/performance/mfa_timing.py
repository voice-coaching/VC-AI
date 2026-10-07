"""Request-local inclusive MFA spans; no capture of text, audio or model data."""
from functools import wraps
import resource
import time


def attach(aligner, record):
    spans = record.setdefault('performanceSpans', {})
    methods = ('setup', 'setup_database', 'load_corpus', '_load_corpus_from_source_mp',
               'dictionary_setup', 'initialize_jobs', 'normalize_text',
               'generate_features', 'compile_train_graphs', 'align_utterances',
               'analyze_alignments', 'export_files', 'cleanup')
    record['performanceSpanSemantics'] = 'inclusive; nested spans must not be summed'
    record['performanceUnavailableMethods'] = []
    for name in methods:
        original = getattr(aligner, name, None)
        if not callable(original):
            record['performanceUnavailableMethods'].append(name)
            continue
        setattr(aligner, name, _wrapper(original, spans.setdefault(name, [])))


def _wrapper(original, observations):
    @wraps(original)
    def measured(*args, **kwargs):
        start, cpu = time.perf_counter(), time.process_time()
        before = resource.getrusage(resource.RUSAGE_SELF)
        try:
            return original(*args, **kwargs)
        finally:
            after = resource.getrusage(resource.RUSAGE_SELF)
            observations.append(dict(wallSeconds=time.perf_counter()-start,
                processCpuSeconds=time.process_time()-cpu,
                voluntaryContextSwitches=after.ru_nvcsw-before.ru_nvcsw,
                involuntaryContextSwitches=after.ru_nivcsw-before.ru_nivcsw,
                inputBlocks=after.ru_inblock-before.ru_inblock,
                outputBlocks=after.ru_oublock-before.ru_oublock,
                processLifetimePeakRssKiB=after.ru_maxrss))
    return measured


def begin():
    return time.perf_counter(), time.process_time()


def finish(record, name, started):
    record.setdefault('performanceSpans', {}).setdefault(name, []).append(dict(
        wallSeconds=time.perf_counter()-started[0], processCpuSeconds=time.process_time()-started[1]))
