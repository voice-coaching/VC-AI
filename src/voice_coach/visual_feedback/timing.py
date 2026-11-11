"""Request-local inclusive wall spans; values contain no media or text."""
from contextlib import contextmanager
import time


class SpanTimer:
    def __init__(self):
        self.spans = {}

    @contextmanager
    def measure(self, name):
        started = time.monotonic()
        try:
            yield
        finally:
            row = self.spans.setdefault(name, dict(wallSeconds=0., calls=0))
            row['wallSeconds'] += time.monotonic() - started
            row['calls'] += 1
