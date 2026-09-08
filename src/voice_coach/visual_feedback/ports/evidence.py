"""Port for resolving immutable seungun pronunciation evidence."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models.binding import (
    RestrictedVideoHandle,
    SameAttemptBindingReceipt,
    SeungunAnalysisReference,
)
from ..models.evidence import SeungunEvidenceSnapshot


@runtime_checkable
class SeungunPronunciationEvidencePort(Protocol):
    """Resolve and bind already-produced seungun evidence without rerunning it."""

    def resolve(
        self,
        *,
        analysis_ref: SeungunAnalysisReference,
        binding_receipt: SameAttemptBindingReceipt,
        video_handle: RestrictedVideoHandle,
    ) -> SeungunEvidenceSnapshot: ...


__all__ = ["SeungunPronunciationEvidencePort"]
