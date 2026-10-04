"""One private event loop and HTTP pool per synchronous journal adapter."""
import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
import threading
import httpx


class JournalHttp:
    def __init__(self, base_url, headers):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever,
                                        name="canonical-journal-http", daemon=True)
        self._thread.start()

        async def create():
            return httpx.AsyncClient(base_url=base_url, headers=headers, trust_env=False,
                                     follow_redirects=False, timeout=httpx.Timeout(10, connect=3),
                                     limits=httpx.Limits(max_connections=2, max_keepalive_connections=2))

        try:
            self.client = asyncio.run_coroutine_threadsafe(create(), self._loop).result(timeout=5)
        except BaseException:
            self._stop()
            raise
        self._closed = False

    def run(self, operation, budget):
        async def bounded():
            return await asyncio.wait_for(operation, budget)
        future = asyncio.run_coroutine_threadsafe(bounded(), self._loop)
        try:
            return future.result(timeout=budget + 1)
        except FutureTimeout:
            future.cancel()
            raise TimeoutError() from None

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.run(self.client.aclose(), 5)
        finally:
            self._stop()

    def _stop(self):
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        if not self._thread.is_alive():
            self._loop.close()
