"""Ports for admitted VC-BE analysis work.

No port takes a user ID, session ID, URL, raw consent document, or public file
path.  The worker accepts only an opaque object key and locally issued evidence.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re
from typing import Protocol, runtime_checkable

from .domain import AnalysisOutcome, AnalysisRequest


class BackendAnalysisError(RuntimeError):
    """Stable operation failure that is safe to return to VC-BE."""

    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class AnalysisAuthorization:
    """Worker-local authority obtained before the audio object is opened."""

    receipt_sha256: str
    policy_revision: str
    expires_at: datetime
    delete_on_completion: bool
    remote_egress_allowed: bool

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", self.receipt_sha256) is None:
            raise ValueError("authorization receipt must be a lower-case SHA-256")
        if not self.policy_revision or len(self.policy_revision) > 100:
            raise ValueError("authorization policy revision is invalid")
        if self.expires_at.tzinfo is None:
            raise ValueError("authorization expiry must be timezone-aware")


@dataclass(frozen=True, slots=True)
class MaterializedAudio:
    """A restricted local file exposed only during a storage context manager."""

    path: Path = field(repr=False)
    byte_count: int
    sha256: str

    def __post_init__(self) -> None:
        if not self.path.is_absolute() or self.byte_count < 0:
            raise ValueError("materialized audio metadata is invalid")
        if re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None:
            raise ValueError("materialized audio digest must be a lower-case SHA-256")


@dataclass(frozen=True, slots=True)
class SeungunAnalysis:
    """Authoritative audio outcome emitted by the Seungun-owned executor."""

    outcome: AnalysisOutcome
    summary_feedback: str | None
    pipeline_revision: str

    def __post_init__(self) -> None:
        if not self.pipeline_revision or len(self.pipeline_revision) > 100:
            raise ValueError("Seungun pipeline revision is invalid")
        if self.outcome is AnalysisOutcome.COACHING_READY and not self.summary_feedback:
            raise ValueError("coaching-ready Seungun output requires grounded feedback")


@runtime_checkable
class AnalysisAuthorizationPort(Protocol):
    """Resolve per-attempt consent/rights evidence before any media I/O."""

    def authorize(self, request: AnalysisRequest) -> AnalysisAuthorization: ...


@runtime_checkable
class RestrictedObjectStorePort(Protocol):
    """Materialise one authorised object into an ephemeral restricted workspace."""

    def materialize(
        self, request: AnalysisRequest, authorization: AnalysisAuthorization
    ) -> AbstractContextManager[MaterializedAudio]: ...


@runtime_checkable
class SeungunAnalysisPort(Protocol):
    """Run exactly one Seungun-owned audio analysis over admitted media."""

    def analyze(
        self, *, request: AnalysisRequest, audio: MaterializedAudio
    ) -> SeungunAnalysis: ...
