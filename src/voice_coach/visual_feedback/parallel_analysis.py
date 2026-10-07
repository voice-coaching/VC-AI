"""Request-scoped audio/video orchestration independent of HTTP and model runtimes."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class VisualBranch:
    status: str
    value: Any = None
    reason: str | None = None


@dataclass(frozen=True)
class ParallelAnalysis:
    audio: Any
    visual: VisualBranch
    started: float
    audio_finished: float
    visual_finished: float


async def analyze_parallel(*, audio: Callable[[], Awaitable[Any]],
                           video: Callable[[], Awaitable[Any]] | None,
                           deadline: float, visual_timeout: float) -> ParallelAnalysis:
    """Feature extraction overlaps audio. Alignment-dependent scoring follows this join.

    Ports MUST propagate cancellation after killing/reaping their child processes.
    A to_thread call alone is not a cancellable decoder/model port. Drain both
    tasks before returning/raising so request-owned files are safe to release.
    """
    import math
    if (not math.isfinite(deadline) or not math.isfinite(visual_timeout)
            or visual_timeout <= 0 or deadline <= time.monotonic()):
        raise ValueError("INVALID_ANALYSIS_DEADLINE")
    started = time.monotonic()
    audio_end = visual_end = started

    async def run_audio():
        nonlocal audio_end
        try:
            return await audio()
        finally:
            audio_end = time.monotonic()

    async def run_video():
        nonlocal visual_end
        try:
            if video is None:
                return VisualBranch("NOT_PROVIDED")
            try:
                async with asyncio.timeout(min(visual_timeout, max(0, deadline - time.monotonic()))):
                    return VisualBranch("OBSERVED", await video())
            except TimeoutError:
                return VisualBranch("TIMEOUT", reason="VIDEO_TIMEOUT")
            except VisualObservationUnavailable as error:
                return VisualBranch("UNSCORABLE", reason=error.reason)
            except MediaIntegrityFailure:
                raise  # Never downgrade a different/changed video to normal fallback.
            except Exception:
                return VisualBranch("FAILED", reason="VIDEO_PROCESSING_FAILED")
        finally:
            visual_end = time.monotonic()

    a, v = asyncio.create_task(run_audio()), asyncio.create_task(run_video())
    try:
        async with asyncio.timeout(max(0, deadline - time.monotonic())):
            result, visual = await asyncio.gather(a, v)
        return ParallelAnalysis(result, visual, started, audio_end, visual_end)
    finally:
        for task in (a, v):
            if not task.done():
                task.cancel()
        drain = asyncio.gather(a, v, return_exceptions=True)
        # Repeated cancellation cannot allow live ports to outlive media cleanup.
        cancelled = False
        while not drain.done():
            try:
                await asyncio.shield(drain)
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError


class MediaIntegrityFailure(ValueError):
    pass


class VisualObservationUnavailable(ValueError):
    def __init__(self, reason: str):
        if reason not in {"NO_FACE", "MULTIPLE_FACES", "LOW_VISIBILITY", "SYNC_UNCERTAIN",
                          "UNSUPPORTED_ORIENTATION", "FRAME_LIMIT_EXCEEDED"}:
            raise ValueError("UNKNOWN_VISUAL_REASON")
        self.reason = reason
        super().__init__(reason)
