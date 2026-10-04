"""Allowlisted pipeline timings. Never include payloads or exception messages."""
from datetime import UTC, datetime
import json
import sys
import time


class PipelineTiming:
    def __init__(self, *, analysis_id, request_id, execution_id, revision):
        self.identity = dict(analysisId=analysis_id, requestId=str(request_id),
                             executionId=str(execution_id), workerRevision=revision)
        self.name = None
        self.started = None

    def stage(self, name):
        self.finish("OK")
        self.name, self.started = name, time.monotonic()
        self._emit("START")

    def finish(self, code):
        if self.started is not None:
            self._emit("END", elapsedSeconds=time.monotonic() - self.started, code=code)
            self.name = self.started = None

    def _emit(self, phase, **metrics):
        print(json.dumps(dict(event="canonical_timing", stage=self.name, phase=phase,
                              utc=datetime.now(UTC).isoformat(), **self.identity, **metrics)),
              file=sys.stderr, flush=True)
