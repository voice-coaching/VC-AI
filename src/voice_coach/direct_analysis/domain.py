from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


class DirectFailure(Exception):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code, self.status = code, status


@dataclass(frozen=True)
class DirectInput:
    """No Backend IDs, user identity, remote URL or admission lease."""
    job_id: UUID
    event_id: UUID
    execution_id: UUID
    audio_asset_id: UUID
    script_text: str
    script_sha256: str
    audio_sha256: str
    file_size_bytes: int
    content_id: int | None
    mime_type: str = "audio/wav"
    is_legacy: bool = False
    visual_input: None = None
    is_direct: bool = True

    # Compatibility with the evidence association port, never fake DB integers.
    @property
    def analysis_id(self):
        return str(self.job_id)

    @property
    def recording_id(self):
        return str(self.audio_asset_id)


@dataclass(frozen=True)
class DirectAssociation:
    request_id: UUID
    execution_id: UUID
    analysis_id: str
    recording_id: str
    content_id: int | None
    canonical_analysis_id: str
    core_sha256: str

    def to_document(self):
        return dict(schemaVersion="voice-coaching.direct-association.v1",
            requestId=str(self.request_id), executionId=str(self.execution_id),
            jobId=self.analysis_id, audioAssetId=self.recording_id, contentId=self.content_id,
            canonicalAnalysisId=self.canonical_analysis_id, coreSha256=self.core_sha256)


@dataclass(frozen=True)
class Settings:
    root: Path
    enabled: bool = False
    maximum_bytes: int = 20 * 1024 * 1024
    spool_bytes: int = 2 * 1024 * 1024 * 1024
    maximum_jobs: int = 1000
    concurrency: int = 1
    maximum_pending: int = 3
    deadline_seconds: int = 900
    origins: tuple[str, ...] = ()
