"""One request-owned CPU GOP task; join before resident reuse or cleanup."""
from concurrent.futures import ThreadPoolExecutor
import time


class GopTask:
    def __init__(self, marks, extract, vocabulary):
        self.marks, self.extract = marks, extract
        self.vocabulary = dict(vocabulary)
        self.pool = None
        self.future = None

    def start(self, state):
        if self.future is not None or state.get('native') is None or 'phone_contract' not in state:
            return
        metadata = [row.model_dump(mode='json') for row in state['phone_contract'].full_metadata]
        native = state['native']  # Forward has completed; logits/runs are read-only thereafter.
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='request-gop')
        try:
            self.future = self.pool.submit(self._run, native, metadata)
        except BaseException:
            self.close()
            raise

    def _run(self, native, metadata):
        self.marks['gop_start'] = time.perf_counter()
        try:
            return self.extract(native, metadata, self.vocabulary)
        except (ValueError, RuntimeError) as exc:
            # Preserve the parent's error classification and output contract.
            return {'revision': 'gop-ctc-af-sd-native-v1', 'status': 'UNAVAILABLE',
                    'errorCode': type(exc).__name__}
        finally:
            self.marks['gop_done'] = time.perf_counter()

    def result(self, state):
        self.start(state)
        return self.future.result() if self.future is not None else None

    def close(self):
        if self.pool is not None:
            # Python cannot cancel an executing CTC kernel. Join, or the existing
            # outer deadline guard terminates this entire resident generation.
            self.pool.shutdown(wait=True, cancel_futures=True)
            self.pool = None
