"""Experiment-only MFA future. Default OFF; no MFA/XLS-R computation changes."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Any, Callable
import time

class ParallelMfaCandidate:
    def __init__(self, *, enabled: bool, context: dict[str, Any],
                 mfa_call: Callable[..., Any], detector_call: Callable[..., Any],
                 event: Callable[[str, float], None]):
        self.enabled=enabled
        self.context=context
        self.mfa_call=mfa_call
        self.detector_call=detector_call
        self.event=event
        self.pool: ThreadPoolExecutor|None=None
        self.future: Future|None=None
        self.completed=False
        self.closed=False
    def __enter__(self):
        if self.enabled:
            self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix="mfa_request")
        return self
    def _run_mfa(self, args: tuple[Any,...]):
        self.event("mfa_start",time.perf_counter())
        try:
            return self.mfa_call(*args)
        finally:
            self.event("mfa_complete",time.perf_counter())
    def run_detector(self,*args,**kwargs):
        if self.enabled:
            if self.pool is None or self.future is not None:
                raise RuntimeError("MFA_FUTURE_STATE")
            required=("audio","script","analysis_id","cfg")
            if not all(k in self.context for k in required):
                raise RuntimeError("MFA_DEPENDENCY_NOT_READY")
            self.event("mfa_submit",time.perf_counter())
            params=tuple(self.context[k] for k in required)
            self.future=self.pool.submit(self._run_mfa,params)
            self.event("mfa_submitted",time.perf_counter())
        return self.detector_call(*args,**kwargs)
    def await_mfa(self,*args,**kwargs):
        if not self.enabled:
            return self.mfa_call(*args,**kwargs)
        if self.future is None or self.completed:
            raise RuntimeError("MFA_FUTURE_MISSING_OR_DUPLICATE")
        expected=tuple(self.context[k] for k in ("audio","script","analysis_id","cfg"))
        if len(args)!=4 or any(a is not b and a!=b for a,b in zip(args,expected,strict=True)):
            raise RuntimeError("MFA_REQUEST_IDENTITY_MISMATCH")
        self.event("mfa_barrier_start",time.perf_counter())
        try:
            value=self.future.result()
            self.completed=True
            return value
        finally:
            self.event("mfa_barrier_complete",time.perf_counter())
    def __exit__(self,exc_type,exc,tb):
        try:
            if self.pool is not None:
                self.pool.shutdown(wait=True,cancel_futures=False)
            if exc is None and self.future is not None:
                self.future.result()
        finally:
            self.closed=True
        return False
    @property
    def pending_futures(self)->int:
        return int(self.future is not None and not self.future.done())
