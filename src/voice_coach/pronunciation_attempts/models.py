"""Path-free contracts for one locally admitted audiovisual attempt.

The objects in this module deliberately contain no filesystem locator.  A local
adapter owns those locators and exposes only opaque, integrity-bound handles to
the application layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from fractions import Fraction
import hashlib
import json
import math
import re


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,191}$")
_TIME_BASE_PATTERN = re.compile(r"^[1-9][0-9]*/[1-9][0-9]*$")


class MediaErrorCode(str, Enum):
    """Stable, path-free failure codes for media boundary callers."""

    CONSENT_NOT_GRANTED = "consent_not_granted"
    CONSENT_ATTEMPT_MISMATCH = "consent_attempt_mismatch"
    CONSENT_PURPOSE_MISMATCH = "consent_purpose_mismatch"
    CONSENT_DATA_CATEGORY_MISMATCH = "consent_data_category_mismatch"
    RETENTION_POLICY_NOT_ALLOWED = "retention_policy_not_allowed"
    DELETE_ON_COMPLETION_REQUIRED = "delete_on_completion_required"
    REMOTE_EGRESS_NOT_ALLOWED = "remote_egress_not_allowed"
    CONSENT_NOT_YET_EFFECTIVE = "consent_not_yet_effective"
    CONSENT_EXPIRED = "consent_expired"
    NOFOLLOW_UNAVAILABLE = "nofollow_unavailable"
    SOURCE_OPEN_FAILED = "source_open_failed"
    SOURCE_NOT_REGULAR = "source_not_regular"
    SOURCE_EMPTY = "source_empty"
    SOURCE_TOO_LARGE = "source_too_large"
    SOURCE_READ_FAILED = "source_read_failed"
    SOURCE_CHANGED_DURING_COPY = "source_changed_during_copy"
    RESTRICTED_WORKSPACE_UNAVAILABLE = "restricted_workspace_unavailable"
    RESTRICTED_WRITE_FAILED = "restricted_write_failed"
    RESTRICTED_ASSET_UNAVAILABLE = "restricted_asset_unavailable"
    RESTRICTED_ASSET_MISMATCH = "restricted_asset_mismatch"
    RESTRICTED_ASSET_BUSY = "restricted_asset_busy"
    DERIVED_AUDIO_NOT_COMMITTED = "derived_audio_not_committed"
    CLEANUP_FAILED = "cleanup_failed"
    TOOL_UNAVAILABLE = "tool_unavailable"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_OUTPUT_LIMIT_EXCEEDED = "tool_output_limit_exceeded"
    TOOL_EXECUTION_FAILED = "tool_execution_failed"
    CONTAINER_INVALID = "container_invalid"
    CONTAINER_UNSUPPORTED = "container_unsupported"
    CONTAINER_POLICY_REJECTED = "container_policy_rejected"
    FRAME_TIMESTAMPS_INVALID = "frame_timestamps_invalid"
    VIDEO_DECODE_FAILED = "video_decode_failed"
    AUDIO_EXTRACTION_FAILED = "audio_extraction_failed"
    DERIVED_AUDIO_TOO_LARGE = "derived_audio_too_large"


class PronunciationAttemptMediaError(RuntimeError):
    """A fail-closed media error that never includes a source path or stderr."""

    def __init__(self, code: MediaErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


class VideoConsentDecision(str, Enum):
    """Consent state accepted by the local video admission boundary."""

    GRANTED = "granted"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class VideoConsentAuthorization:
    """Purpose-limited authorization checked before any source file access."""

    attempt_id_sha256: str
    consent_receipt_sha256: str
    decision: VideoConsentDecision
    purpose: str
    data_category: str
    retention_policy_id: str
    delete_on_completion: bool
    remote_egress_allowed: bool
    issued_at_utc: datetime
    expires_at_utc: datetime

    def __post_init__(self) -> None:
        _require_sha256(self.attempt_id_sha256, "attempt_id_sha256")
        _require_sha256(self.consent_receipt_sha256, "consent_receipt_sha256")
        if not isinstance(self.decision, VideoConsentDecision):
            raise ValueError("decision must be a VideoConsentDecision")
        _require_identifier(self.purpose, "purpose")
        _require_identifier(self.data_category, "data_category")
        _require_identifier(self.retention_policy_id, "retention_policy_id")
        if not isinstance(self.delete_on_completion, bool):
            raise ValueError("delete_on_completion must be a boolean")
        if not isinstance(self.remote_egress_allowed, bool):
            raise ValueError("remote_egress_allowed must be a boolean")
        _require_utc(self.issued_at_utc, "issued_at_utc")
        _require_utc(self.expires_at_utc, "expires_at_utc")
        if self.expires_at_utc <= self.issued_at_utc:
            raise ValueError("consent expiry must follow issue time")


@dataclass(frozen=True, slots=True)
class VideoAdmissionPolicy:
    """Injected local admission and consent limits."""

    maximum_source_bytes: int
    required_purpose: str
    required_data_category: str
    allowed_retention_policy_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_positive_integer(self.maximum_source_bytes, "maximum_source_bytes")
        _require_identifier(self.required_purpose, "required_purpose")
        _require_identifier(self.required_data_category, "required_data_category")
        if not self.allowed_retention_policy_ids:
            raise ValueError("allowed_retention_policy_ids must not be empty")
        for retention_policy_id in self.allowed_retention_policy_ids:
            _require_identifier(retention_policy_id, "allowed_retention_policy_ids[]")
        if len(set(self.allowed_retention_policy_ids)) != len(
            self.allowed_retention_policy_ids
        ):
            raise ValueError("allowed_retention_policy_ids must be unique")


@dataclass(frozen=True, slots=True)
class RestrictedVideoAsset:
    """Opaque reference to an immutable copy in restricted local storage."""

    handle_id: str
    attempt_id_sha256: str
    container_sha256: str
    byte_count: int
    consent_receipt_sha256: str

    def __post_init__(self) -> None:
        _require_sha256(self.handle_id, "handle_id")
        _require_sha256(self.attempt_id_sha256, "attempt_id_sha256")
        _require_sha256(self.container_sha256, "container_sha256")
        _require_positive_integer(self.byte_count, "byte_count")
        _require_sha256(self.consent_receipt_sha256, "consent_receipt_sha256")

    @property
    def source_sha256(self) -> str:
        """Compatibility name used by same-attempt binding receipts."""

        return self.container_sha256


@dataclass(frozen=True, slots=True)
class HttpRestrictedVideoAsset:
    """Opaque reference to an immutable copy in restricted local storage."""

    handle_id: str
    attempt_id_sha256: str
    container_sha256: str
    byte_count: int

    def __post_init__(self) -> None:
        _require_sha256(self.handle_id, "handle_id")
        _require_sha256(self.attempt_id_sha256, "attempt_id_sha256")
        _require_sha256(self.container_sha256, "container_sha256")
        _require_positive_integer(self.byte_count, "byte_count")

    @property
    def source_sha256(self) -> str:
        """Compatibility name used by same-attempt binding receipts."""

        return self.container_sha256


@dataclass(frozen=True, slots=True)
class AvContainerPolicy:
    """All media geometry, duration, frame, and subprocess limits are injected."""

    minimum_duration_ms: int
    maximum_duration_ms: int
    minimum_width: int
    maximum_width: int
    minimum_height: int
    maximum_height: int
    minimum_frames_per_second: float
    maximum_frames_per_second: float
    minimum_frame_count: int
    maximum_frame_count: int
    maximum_rgb_sample_frames: int
    maximum_rgb_sample_bytes: int
    maximum_pcm_bytes: int
    subprocess_timeout_seconds: float
    maximum_subprocess_output_bytes: int

    def __post_init__(self) -> None:
        integer_bounds = (
            (self.minimum_duration_ms, self.maximum_duration_ms, "duration_ms"),
            (self.minimum_width, self.maximum_width, "width"),
            (self.minimum_height, self.maximum_height, "height"),
            (self.minimum_frame_count, self.maximum_frame_count, "frame_count"),
        )
        for minimum, maximum, name in integer_bounds:
            _require_positive_integer(minimum, f"minimum_{name}")
            _require_positive_integer(maximum, f"maximum_{name}")
            if maximum < minimum:
                raise ValueError(f"maximum_{name} must be >= minimum_{name}")

        _require_positive_number(
            self.minimum_frames_per_second, "minimum_frames_per_second"
        )
        _require_positive_number(
            self.maximum_frames_per_second, "maximum_frames_per_second"
        )
        if self.maximum_frames_per_second < self.minimum_frames_per_second:
            raise ValueError(
                "maximum_frames_per_second must be >= minimum_frames_per_second"
            )

        for value, name in (
            (self.maximum_rgb_sample_frames, "maximum_rgb_sample_frames"),
            (self.maximum_rgb_sample_bytes, "maximum_rgb_sample_bytes"),
            (self.maximum_pcm_bytes, "maximum_pcm_bytes"),
            (
                self.maximum_subprocess_output_bytes,
                "maximum_subprocess_output_bytes",
            ),
        ):
            _require_positive_integer(value, name)
        if self.maximum_rgb_sample_frames > self.maximum_frame_count:
            raise ValueError(
                "maximum_rgb_sample_frames must not exceed maximum_frame_count"
            )
        _require_positive_number(
            self.subprocess_timeout_seconds, "subprocess_timeout_seconds"
        )


@dataclass(frozen=True, slots=True)
class VideoStreamProbe:
    """Strict allowlisted video-stream metadata from ffprobe."""

    stream_index: int
    codec_name: str
    width: int
    height: int
    frames_per_second_numerator: int
    frames_per_second_denominator: int
    frame_count: int
    time_base: str
    start_time_us: int
    duration_ms: int

    def __post_init__(self) -> None:
        _require_non_negative_integer(self.stream_index, "stream_index")
        if self.codec_name not in {"h264", "hevc"}:
            raise ValueError("video codec must be h264 or hevc")
        _require_positive_integer(self.width, "width")
        _require_positive_integer(self.height, "height")
        _require_positive_integer(
            self.frames_per_second_numerator, "frames_per_second_numerator"
        )
        _require_positive_integer(
            self.frames_per_second_denominator, "frames_per_second_denominator"
        )
        _require_positive_integer(self.frame_count, "frame_count")
        _require_time_base(self.time_base, "time_base")
        _require_integer(self.start_time_us, "start_time_us")
        _require_positive_integer(self.duration_ms, "duration_ms")

    @property
    def frames_per_second(self) -> float:
        return float(
            Fraction(
                self.frames_per_second_numerator,
                self.frames_per_second_denominator,
            )
        )


@dataclass(frozen=True, slots=True)
class AudioStreamProbe:
    """Strict AAC audio-stream metadata from ffprobe."""

    stream_index: int
    codec_name: str
    sample_rate_hz: int
    channels: int
    time_base: str
    start_time_us: int
    duration_ms: int

    def __post_init__(self) -> None:
        _require_non_negative_integer(self.stream_index, "stream_index")
        if self.codec_name != "aac":
            raise ValueError("audio codec must be aac")
        _require_positive_integer(self.sample_rate_hz, "sample_rate_hz")
        _require_positive_integer(self.channels, "channels")
        _require_time_base(self.time_base, "time_base")
        _require_integer(self.start_time_us, "start_time_us")
        _require_positive_integer(self.duration_ms, "duration_ms")


@dataclass(frozen=True, slots=True)
class MediaProbe:
    """Path-free, fully checked metadata for an admitted container."""

    asset_handle_id: str
    container_sha256: str
    format_name: str
    duration_ms: int
    video_stream: VideoStreamProbe
    audio_stream: AudioStreamProbe
    video_frame_timestamps_us: tuple[int, ...]
    ffprobe_revision: str

    def __post_init__(self) -> None:
        _require_sha256(self.asset_handle_id, "asset_handle_id")
        _require_sha256(self.container_sha256, "container_sha256")
        if self.format_name != "mp4":
            raise ValueError("format_name must be mp4")
        _require_positive_integer(self.duration_ms, "duration_ms")
        if not isinstance(self.video_stream, VideoStreamProbe):
            raise ValueError("video_stream must be a VideoStreamProbe")
        if not isinstance(self.audio_stream, AudioStreamProbe):
            raise ValueError("audio_stream must be an AudioStreamProbe")
        _require_identifier(self.ffprobe_revision, "ffprobe_revision")
        if len(self.video_frame_timestamps_us) != self.video_stream.frame_count:
            raise ValueError("video timestamp count must equal frame count")
        previous_timestamp: int | None = None
        for timestamp_us in self.video_frame_timestamps_us:
            _require_integer(timestamp_us, "video_frame_timestamps_us[]")
            if previous_timestamp is not None and timestamp_us <= previous_timestamp:
                raise ValueError("video frame timestamps must be strictly increasing")
            previous_timestamp = timestamp_us


@dataclass(frozen=True, slots=True)
class DecodedRgbFrame:
    """One bounded RGB24 frame held only inside the application process."""

    timestamp_us: int
    width: int
    height: int
    rgb24: bytes = field(repr=False)

    def __post_init__(self) -> None:
        _require_integer(self.timestamp_us, "timestamp_us")
        _require_positive_integer(self.width, "width")
        _require_positive_integer(self.height, "height")
        if not isinstance(self.rgb24, bytes):
            raise ValueError("rgb24 must be bytes")
        if len(self.rgb24) != self.width * self.height * 3:
            raise ValueError("rgb24 byte count differs from frame geometry")


@dataclass(frozen=True, slots=True)
class Rgb24Sample:
    """A policy-bounded temporal sample decoded without exposing its locator."""

    asset_handle_id: str
    container_sha256: str
    video_stream_index: int
    frames: tuple[DecodedRgbFrame, ...]
    ffmpeg_revision: str

    def __post_init__(self) -> None:
        _require_sha256(self.asset_handle_id, "asset_handle_id")
        _require_sha256(self.container_sha256, "container_sha256")
        _require_non_negative_integer(self.video_stream_index, "video_stream_index")
        _require_identifier(self.ffmpeg_revision, "ffmpeg_revision")
        if not self.frames:
            raise ValueError("RGB24 sample must contain at least one frame")
        timestamps = tuple(frame.timestamp_us for frame in self.frames)
        if any(current <= previous for previous, current in zip(timestamps, timestamps[1:])):
            raise ValueError("RGB24 sample timestamps must be strictly increasing")


@dataclass(frozen=True, slots=True)
class AudioDerivationReceipt:
    """Integrity receipt for embedded audio decoded to 16 kHz mono s16le."""

    container_sha256: str
    audio_stream_index: int
    tool_revision: str
    pcm_sha256: str
    sample_rate_hz: int
    channels: int
    source_time_base: str
    pcm_time_base: str
    sample_format: str
    receipt_sha256: str
    schema_version: str = "voice_coach.audio_derivation.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "voice_coach.audio_derivation.v1":
            raise ValueError("audio derivation schema version differs")
        _require_sha256(self.container_sha256, "container_sha256")
        _require_non_negative_integer(self.audio_stream_index, "audio_stream_index")
        _require_identifier(self.tool_revision, "tool_revision")
        _require_sha256(self.pcm_sha256, "pcm_sha256")
        if self.sample_rate_hz != 16_000:
            raise ValueError("derived PCM sample rate must be 16000 Hz")
        if self.channels != 1:
            raise ValueError("derived PCM must be mono")
        _require_time_base(self.source_time_base, "source_time_base")
        if self.pcm_time_base != "1/16000":
            raise ValueError("derived PCM time base must be 1/16000")
        if self.sample_format != "s16le":
            raise ValueError("derived PCM sample format must be s16le")
        _require_sha256(self.receipt_sha256, "receipt_sha256")
        if self.receipt_sha256 != _sha256_document(self.unsigned_json_object()):
            raise ValueError("audio derivation receipt digest differs")

    @classmethod
    def issue(
        cls,
        *,
        container_sha256: str,
        audio_stream_index: int,
        tool_revision: str,
        pcm_sha256: str,
        source_time_base: str,
    ) -> AudioDerivationReceipt:
        unsigned = {
            "schema_version": "voice_coach.audio_derivation.v1",
            "container_sha256": container_sha256,
            "audio_stream_index": audio_stream_index,
            "tool_revision": tool_revision,
            "pcm_sha256": pcm_sha256,
            "sample_rate_hz": 16_000,
            "channels": 1,
            "source_time_base": source_time_base,
            "pcm_time_base": "1/16000",
            "sample_format": "s16le",
        }
        return cls(
            container_sha256=container_sha256,
            audio_stream_index=audio_stream_index,
            tool_revision=tool_revision,
            pcm_sha256=pcm_sha256,
            sample_rate_hz=16_000,
            channels=1,
            source_time_base=source_time_base,
            pcm_time_base="1/16000",
            sample_format="s16le",
            receipt_sha256=_sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "container_sha256": self.container_sha256,
            "audio_stream_index": self.audio_stream_index,
            "tool_revision": self.tool_revision,
            "pcm_sha256": self.pcm_sha256,
            "sample_rate_hz": self.sample_rate_hz,
            "channels": self.channels,
            "source_time_base": self.source_time_base,
            "pcm_time_base": self.pcm_time_base,
            "sample_format": self.sample_format,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}


@dataclass(frozen=True, slots=True)
class RestrictedPcmAudio:
    """Opaque restricted artifact derived from the admitted container."""

    artifact_id: str
    asset_handle_id: str
    attempt_id_sha256: str
    byte_count: int
    sample_count: int
    derivation_receipt: AudioDerivationReceipt

    def __post_init__(self) -> None:
        _require_sha256(self.artifact_id, "artifact_id")
        _require_sha256(self.asset_handle_id, "asset_handle_id")
        _require_sha256(self.attempt_id_sha256, "attempt_id_sha256")
        _require_positive_integer(self.byte_count, "byte_count")
        _require_positive_integer(self.sample_count, "sample_count")
        if self.byte_count != self.sample_count * 2:
            raise ValueError("s16le byte count must be twice the sample count")
        if not isinstance(self.derivation_receipt, AudioDerivationReceipt):
            raise ValueError(
                "derivation_receipt must be an AudioDerivationReceipt"
            )


@dataclass(frozen=True, slots=True)
class RestrictedMediaDeletionReceipt:
    """Path-free evidence that one restricted workspace was removed."""

    asset_handle_id: str
    status: str
    deletion_receipt_sha256: str

    def __post_init__(self) -> None:
        _require_sha256(self.asset_handle_id, "asset_handle_id")
        if not isinstance(self.status, str) or self.status not in {
            "deleted",
            "already_deleted",
        }:
            raise ValueError("restricted deletion status is unknown")
        _require_sha256(self.deletion_receipt_sha256, "deletion_receipt_sha256")


def _sha256_document(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _require_sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be lowercase SHA-256")
    return value


def _require_identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be a safe identifier")
    return value


def _require_time_base(value: object, name: str) -> str:
    if not isinstance(value, str) or _TIME_BASE_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be a positive rational time base")
    return value


def _require_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _require_non_negative_integer(value: object, name: str) -> int:
    integer = _require_integer(value, name)
    if integer < 0:
        raise ValueError(f"{name} must be non-negative")
    return integer


def _require_positive_integer(value: object, name: str) -> int:
    integer = _require_integer(value, name)
    if integer <= 0:
        raise ValueError(f"{name} must be positive")
    return integer


def _require_positive_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be positive and finite")
    return number


def _require_utc(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError(f"{name} must be UTC")
    return value


__all__ = [
    "AudioDerivationReceipt",
    "AudioStreamProbe",
    "AvContainerPolicy",
    "DecodedRgbFrame",
    "MediaErrorCode",
    "MediaProbe",
    "PronunciationAttemptMediaError",
    "RestrictedMediaDeletionReceipt",
    "RestrictedPcmAudio",
    "RestrictedVideoAsset",
    "Rgb24Sample",
    "VideoAdmissionPolicy",
    "VideoConsentAuthorization",
    "VideoConsentDecision",
    "VideoStreamProbe",
]
