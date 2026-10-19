"""MFA 3.4.1 process-local queue completion adapter; thread/USE_MP/one-job only."""
import importlib
import os
import queue
import sys
import threading
import time
from pathlib import Path

from montreal_forced_aligner import config
from montreal_forced_aligner.utils import KaldiProcessWorker

class _Done:
    __slots__ = ("job_name",)
    def __init__(self, job_name):
        self.job_name = job_name

class _Fatal:
    __slots__ = ("error",)
    def __init__(self, error):
        self.error = error

class _Worker(KaldiProcessWorker):
    def run(self):
        # super.run enqueues every result/Exception before returning; FIFO then carries Done.
        try:
            super().run()
        except BaseException as exc:
            self.stopped.set()
            self.return_q.put(_Fatal(exc))
        finally:
            self.return_q.put(_Done(self.job_name))

def run_kaldi_function(function, arguments, stopped=None, total_count=None, *, _queue_capacity=10000):
    """Clone the supported MFA3.4.1 thread/MP branch; only completion wait changes."""
    from montreal_forced_aligner.utils import tqdm
    if not config.USE_MP or not config.USE_THREADING or config.NUM_JOBS != 1:
        raise RuntimeError("QUEUE_ADAPTER_UNVALIDATED_MODE")
    if stopped is None:
        stopped = threading.Event()
    error_dict = {}
    return_queue = queue.Queue(_queue_capacity)
    callback_interval = 10
    num_done = 0
    last_update = 0
    pbar = None
    progress_callback = None
    if not config.QUIET and total_count:
        pbar = tqdm(total=total_count, maxinterval=0)
        progress_callback = pbar.update
    update_time = time.time()
    procs = []
    for args in arguments:
        f = function(args)
        p = _Worker(args.job_name, return_queue, f, stopped)
        procs.append(p)
        p.start()
    finished_count = 0
    seen_done = set()
    normal_completion = False
    try:
        while finished_count < len(procs):
            try:
                # The Done item wakes this same FIFO consumer without one final 1 s timeout.
                # Timeout remains an abnormal-path liveness check, not the normal terminator.
                result = return_queue.get(timeout=1)
                if isinstance(result, _Done):
                    if result.job_name in seen_done or result.job_name not in {p.job_name for p in procs}:
                        raise RuntimeError("QUEUE_ADAPTER_DUPLICATE_OR_UNKNOWN_DONE")
                    seen_done.add(result.job_name)
                    finished_count += 1
                    return_queue.task_done()
                    continue
                if isinstance(result, _Fatal):
                    error_dict["fatal"] = RuntimeError("QUEUE_ADAPTER_WORKER_BASEEXCEPTION:" + repr(result.error))
                    stopped.set()
                    continue
                if isinstance(result, Exception):
                    error_dict[getattr(result, "job_name", 0)] = result
                    stopped.set()
                    continue
                if stopped.is_set():
                    continue
                yield result
                if progress_callback is not None:
                    if isinstance(result, int):
                        num_done += result
                    else:
                        num_done += 1
                    if time.time() - update_time >= callback_interval:
                        if num_done - last_update > 0:
                            progress_callback(num_done - last_update)
                            last_update = num_done
                        update_time = time.time()
                return_queue.task_done()
            except queue.Empty:
                # A completed producer must have enqueued Done before its thread exits.
                if all(not p.is_alive() for p in procs):
                    error_dict["main_thread"] = RuntimeError("QUEUE_ADAPTER_MISSING_COMPLETION")
                    stopped.set()
                    break
                continue
            except Exception as e:
                stopped.set()
                error_dict["main_thread"] = e
                break
        normal_completion = True
    finally:
        if not normal_completion:
            stopped.set()
            # A closed consumer must free bounded queue slots so a producer/Done
            # cannot remain blocked forever in Queue.put.
            deadline = time.monotonic() + 120
            while any(p.is_alive() for p in procs):
                try:
                    return_queue.get(timeout=0.1)
                except queue.Empty:
                    pass
                if time.monotonic() > deadline:
                    raise TimeoutError("QUEUE_ADAPTER_CANCEL_DRAIN_TIMEOUT")
        for p in procs:
            p.join(timeout=120)
            if p.is_alive():
                raise TimeoutError("QUEUE_ADAPTER_WORKER_JOIN_TIMEOUT")
            del p.function
        del procs
        del return_queue
        del stopped
        del arguments
    if error_dict:
        for v in error_dict.values():
            raise v
    if pbar is not None and num_done > last_update:
        progress_callback(num_done - last_update)
    if pbar is not None:
        pbar.refresh()
        pbar.close()

ALIASES = (
    "montreal_forced_aligner.utils",
    "montreal_forced_aligner.corpus.base",
    "montreal_forced_aligner.corpus.acoustic_corpus",
    "montreal_forced_aligner.alignment.mixins",
    "montreal_forced_aligner.alignment.base",
    "montreal_forced_aligner.alignment.pretrained",
)
def install():
    """Called only by candidate resident worker after existing MFA startup guards."""
    import importlib.metadata as md
    if md.version("Montreal-Forced-Aligner") != "3.4.1":
        raise RuntimeError("QUEUE_ADAPTER_MFA_VERSION")
    from montreal_forced_aligner import utils
    original = utils.run_kaldi_function
    modules = [importlib.import_module(name) for name in ALIASES]
    for module in modules:
        if getattr(module, "run_kaldi_function", None) is not original:
            raise RuntimeError("QUEUE_ADAPTER_ALIAS_MISMATCH:" + module.__name__)
    for module in modules:
        module.run_kaldi_function = run_kaldi_function
    return {"module_paths": {m.__name__: str(Path(m.__file__).resolve()) for m in modules},
            "aliases": list(ALIASES), "original": repr(original), "candidate": repr(run_kaldi_function)}
