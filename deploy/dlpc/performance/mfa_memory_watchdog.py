"""Bounded raw diagnostics and exact per-phase sampled peaks; gates stay mandatory."""
from collections import deque
import json
import os
from pathlib import Path
import re
import threading

LOG_BYTES = 8 * 1024**2
SAMPLE_COUNT = 256
PHASE_COUNT = 8


class BoundedWatchdog:
    def __init__(self, pgid, cfg, path, snapshot, violation):
        self.pgid, self.cfg, self.path = pgid, cfg, Path(path)
        self.snapshot, self.violation = snapshot, violation
        self.stop_event = threading.Event()
        self.error = None
        self.error_errno = None
        self.samples = deque(maxlen=SAMPLE_COUNT)
        self.sample_index = 0
        self.peaks = {}
        self.peak_tree_rss_bytes = 0
        self.lock = threading.Lock()
        self.phase = 'STARTING'
        self.thread = threading.Thread(target=self.run, name='memory-watchdog', daemon=False)
        self.thread.start()

    def observe(self, sample):
        phase = sample['phase']
        if phase not in ('STARTING', 'READY', 'DRAINING', 'control_demo') and not re.fullmatch(
                r'g[1-4]_r[0-2]_[a-f0-9]{12}', phase):
            raise RuntimeError('MEMORY_PHASE_INVALID')
        with self.lock:
            if phase not in self.peaks:
                if len(self.peaks) >= PHASE_COUNT:raise RuntimeError('MEMORY_PHASE_LIMIT')
                self.peaks[phase] = dict(rss_bytes=None, pss_bytes=None, uss_bytes=None)
            peak = self.peaks[phase]
            members = sample['members']
            for key in peak:
                if members and all(p.get(key) is not None for p in members):
                    value = sum(p[key] for p in members)
                    peak[key] = value if peak[key] is None else max(peak[key], value)
            self.peak_tree_rss_bytes = max(self.peak_tree_rss_bytes, sample['tree_rss_bytes'])
            self.samples.append(sample)

    def peak(self, phase):
        with self.lock:
            return dict(self.peaks.get(phase, dict(rss_bytes=None, pss_bytes=None, uss_bytes=None)))

    def run(self):
        stream = None
        try:
            stream = self.path.open('x')
            size = 0
            while not self.stop_event.is_set():
                detailed = self.sample_index % 4 == 0
                sample = self.snapshot(self.pgid, detail=detailed)
                self.sample_index += 1
                sample.update(phase=self.phase, detailed_sample=detailed)
                self.observe(sample)
                # Always apply the same numerical guard, even if diagnostics fail.
                failure = self.violation(sample, self.cfg)
                if failure:
                    self.error = failure
                    return
                raw = json.dumps(sample) + '\n'
                count = len(raw.encode())
                if count > LOG_BYTES:raise RuntimeError('MEMORY_SAMPLE_SIZE_LIMIT')
                if size + count > LOG_BYTES:
                    stream.close()
                    previous = self.path.with_name(self.path.name + '.previous')
                    if previous.is_symlink():raise RuntimeError('MEMORY_LOG_SYMLINK')
                    os.replace(self.path, previous)
                    stream = self.path.open('x')
                    size = 0
                stream.write(raw)
                stream.flush()
                size += count
                self.stop_event.wait(self.cfg['sample_interval_s'])
        except BaseException as error:
            if self.error is None:self.error = 'MEMORY_MEASUREMENT_FAILED:' + type(error).__name__
            self.error_errno = getattr(error, 'errno', None)
        finally:
            if stream is not None:
                try:stream.close()
                except OSError as error:
                    if self.error is None:self.error = 'MEMORY_MEASUREMENT_FAILED:OSError'
                    self.error_errno = error.errno

    def check(self):
        if self.error:raise RuntimeError(self.error)

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=3)
        if self.thread.is_alive():raise RuntimeError('watchdog did not stop')
