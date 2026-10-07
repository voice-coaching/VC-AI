"""Verify Backend-produced media receipt without inventing an audio/video clock map."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from functools import lru_cache

from jsonschema import Draft202012Validator

from .http_models import digest
from voice_coach.visual_feedback.parallel_analysis import MediaIntegrityFailure


@lru_cache(maxsize=1)
def _validator():
    path = Path(__file__).resolve().parents[3] / "docs/contracts/runpod_media_preparation_v1.schema.json"
    return Draft202012Validator(json.loads(path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class MediaPreparation:
    receipt_sha256: str
    source_sha256: str
    pcm_sha256: str
    video_sha256: str | None
    normalization_revision: str
    sync_status: str

    @classmethod
    def from_wire(cls, value: dict, *, audio_sha256: str, video_sha256: str | None):
        if type(value) is not dict or set(value) != {"receipt", "receiptSha256"}:
            raise MediaIntegrityFailure("MEDIA_PREPARATION_INVALID")
        receipt = value["receipt"]
        if not _validator().is_valid(receipt):
            raise MediaIntegrityFailure("MEDIA_PREPARATION_INVALID")
        if (value["receiptSha256"] != digest(receipt)
                or receipt["canonicalPcmSha256"] != audio_sha256
                or receipt["canonicalVideoSha256"] != video_sha256):
            raise MediaIntegrityFailure("MEDIA_PREPARATION_MISMATCH")
        return cls(value["receiptSha256"], receipt["sourceSha256"], audio_sha256, video_sha256,
                   receipt["normalizationRevision"], receipt["syncStatus"])

    def require_materialized(self, audio, video=None):
        if audio.sha256 != self.pcm_sha256 or (
                self.video_sha256 is None and video is not None) or (
                self.video_sha256 is not None and (video is None or video.sha256 != self.video_sha256)):
            raise MediaIntegrityFailure("MATERIALIZED_MEDIA_MISMATCH")
