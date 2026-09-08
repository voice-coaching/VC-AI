"""Immutable contracts for host-derived, selected-phone video windows.

The untrusted attempt command never supplies any object in this module.  The
host creates a decode request only after resolving the exact phone anchor from
the already-bound Seungun handoff and a policy-validated A/V offset.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re

from .models import Rgb24Sample


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,191}$")

PHONE_WINDOW_DECODE_RECEIPT_SCHEMA_VERSION = (
    "voice_coach.phone_window_decode.v2"
)
PHONE_WINDOW_TRACKING_RECEIPT_SCHEMA_VERSION = (
    "voice_coach.phone_window_tracking.v2"
)
RUNTIME_PHONE_WINDOW_RECEIPT_SCHEMA_VERSION = (
    "voice_coach.runtime_phone_window.v2"
)


@dataclass(frozen=True, slots=True)
class PolicyValidatedAvSyncOffset:
    """Measured audio-to-video offset backed by a passed sync decision.

    ``offset_ms`` maps an absolute audio timestamp to an absolute video
    timestamp.  A raw correlation candidate cannot construct this contract.
    """

    attempt_id_sha256: str
    binding_receipt_sha256: str
    audio_source_sha256: str
    video_source_sha256: str
    landmark_evidence_sha256: str
    source_candidate_receipt_sha256: str
    measurement_policy_sha256: str
    decision_policy_revision: str
    decision_policy_sha256: str
    decision_adapter_code_sha256: str
    av_sync_decision_receipt_sha256: str
    offset_ms: int
    receipt_sha256: str
    status: str = "synchronized"
    schema_version: str = "voice_coach.policy_validated_av_sync_offset.v2"

    def __post_init__(self) -> None:
        if self.schema_version != "voice_coach.policy_validated_av_sync_offset.v2":
            raise ValueError("A/V sync offset schema differs")
        for name in (
            "attempt_id_sha256",
            "binding_receipt_sha256",
            "audio_source_sha256",
            "video_source_sha256",
            "landmark_evidence_sha256",
            "source_candidate_receipt_sha256",
            "measurement_policy_sha256",
            "decision_policy_sha256",
            "decision_adapter_code_sha256",
            "av_sync_decision_receipt_sha256",
            "receipt_sha256",
        ):
            _require_sha256(getattr(self, name), name)
        _require_identifier(
            self.decision_policy_revision, "decision_policy_revision"
        )
        if self.status != "synchronized":
            raise ValueError("A/V sync offset must be synchronized")
        _require_integer(self.offset_ms, "offset_ms")
        if self.receipt_sha256 != _sha256_document(self.unsigned_json_object()):
            raise ValueError("A/V sync offset receipt digest differs")

    @classmethod
    def issue_from_decision(
        cls,
        *,
        decision: object,
    ) -> PolicyValidatedAvSyncOffset:
        # Local import avoids making the pure phone-window contracts part of an
        # adapter/ports import cycle.
        from .adapters.calibrated_runtime_decisions import RuntimeAvSyncDecision

        if not isinstance(decision, RuntimeAvSyncDecision):
            raise ValueError("typed A/V sync decision is required")
        if decision.status != "synchronized" or decision.observed_offset_ms is None:
            raise ValueError("A/V sync decision did not synchronize")
        unsigned = {
            "schema_version": "voice_coach.policy_validated_av_sync_offset.v2",
            "attempt_id_sha256": decision.attempt_id_sha256,
            "binding_receipt_sha256": decision.binding_receipt_sha256,
            "audio_source_sha256": decision.audio_source_sha256,
            "video_source_sha256": decision.video_source_sha256,
            "landmark_evidence_sha256": decision.landmark_evidence_sha256,
            "source_candidate_receipt_sha256": (
                decision.source_candidate_receipt_sha256
            ),
            "measurement_policy_sha256": decision.measurement_policy_sha256,
            "decision_policy_revision": decision.decision_policy_revision,
            "decision_policy_sha256": decision.decision_policy_sha256,
            "decision_adapter_code_sha256": (
                decision.decision_adapter_code_sha256
            ),
            "av_sync_decision_receipt_sha256": decision.receipt_sha256,
            "offset_ms": decision.observed_offset_ms,
            "status": "synchronized",
        }
        return cls(
            attempt_id_sha256=decision.attempt_id_sha256,
            binding_receipt_sha256=decision.binding_receipt_sha256,
            audio_source_sha256=decision.audio_source_sha256,
            video_source_sha256=decision.video_source_sha256,
            landmark_evidence_sha256=decision.landmark_evidence_sha256,
            source_candidate_receipt_sha256=(
                decision.source_candidate_receipt_sha256
            ),
            measurement_policy_sha256=decision.measurement_policy_sha256,
            decision_policy_revision=decision.decision_policy_revision,
            decision_policy_sha256=decision.decision_policy_sha256,
            decision_adapter_code_sha256=decision.decision_adapter_code_sha256,
            av_sync_decision_receipt_sha256=decision.receipt_sha256,
            offset_ms=decision.observed_offset_ms,
            receipt_sha256=_sha256_document(unsigned),
        )

    @property
    def policy_sha256(self) -> str:
        """Compatibility name for the bound measurement policy digest."""

        return self.measurement_policy_sha256

    @property
    def av_sync_receipt_sha256(self) -> str:
        """Compatibility name for the exact passed decision receipt."""

        return self.av_sync_decision_receipt_sha256

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "attempt_id_sha256": self.attempt_id_sha256,
            "binding_receipt_sha256": self.binding_receipt_sha256,
            "audio_source_sha256": self.audio_source_sha256,
            "video_source_sha256": self.video_source_sha256,
            "landmark_evidence_sha256": self.landmark_evidence_sha256,
            "source_candidate_receipt_sha256": (
                self.source_candidate_receipt_sha256
            ),
            "measurement_policy_sha256": self.measurement_policy_sha256,
            "decision_policy_revision": self.decision_policy_revision,
            "decision_policy_sha256": self.decision_policy_sha256,
            "decision_adapter_code_sha256": self.decision_adapter_code_sha256,
            "av_sync_decision_receipt_sha256": (
                self.av_sync_decision_receipt_sha256
            ),
            "offset_ms": self.offset_ms,
            "status": self.status,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}


@dataclass(frozen=True, slots=True)
class PhoneWindowDecodePolicy:
    """Frozen context, padding, frame, and decompressed-byte bounds."""

    policy_revision: str
    expected_av_sync_policy_sha256: str
    context_contract_sha256: str
    maximum_absolute_offset_ms: int
    padding_before_ms: int
    padding_after_ms: int
    minimum_frame_count: int
    maximum_frame_count: int
    maximum_window_duration_ms: int
    maximum_rgb_bytes: int

    def __post_init__(self) -> None:
        _require_identifier(self.policy_revision, "policy_revision")
        _require_sha256(
            self.expected_av_sync_policy_sha256,
            "expected_av_sync_policy_sha256",
        )
        _require_sha256(self.context_contract_sha256, "context_contract_sha256")
        _require_positive_integer(
            self.maximum_absolute_offset_ms, "maximum_absolute_offset_ms"
        )
        for name in ("padding_before_ms", "padding_after_ms"):
            _require_non_negative_integer(getattr(self, name), name)
        for name in (
            "minimum_frame_count",
            "maximum_frame_count",
            "maximum_window_duration_ms",
            "maximum_rgb_bytes",
        ):
            _require_positive_integer(getattr(self, name), name)
        if self.maximum_frame_count < self.minimum_frame_count:
            raise ValueError("maximum_frame_count must not precede minimum_frame_count")

    @property
    def policy_sha256(self) -> str:
        return _sha256_document(self.to_json_object())

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": "voice_coach.phone_window_decode_policy.v1",
            "policy_revision": self.policy_revision,
            "expected_av_sync_policy_sha256": (
                self.expected_av_sync_policy_sha256
            ),
            "context_contract_sha256": self.context_contract_sha256,
            "maximum_absolute_offset_ms": self.maximum_absolute_offset_ms,
            "padding_before_ms": self.padding_before_ms,
            "padding_after_ms": self.padding_after_ms,
            "minimum_frame_count": self.minimum_frame_count,
            "maximum_frame_count": self.maximum_frame_count,
            "maximum_window_duration_ms": self.maximum_window_duration_ms,
            "maximum_rgb_bytes": self.maximum_rgb_bytes,
        }

@dataclass(frozen=True, slots=True)
class PhoneWindowDecodeRequest:
    """Host-issued local-video window derived from one immutable phone anchor."""

    attempt_id_sha256: str
    binding_receipt_sha256: str
    container_sha256: str
    detection_receipt_sha256: str
    phone_anchor_sha256: str
    av_sync_receipt_sha256: str
    av_sync_offset_receipt_sha256: str
    context_contract_sha256: str
    decode_policy_sha256: str
    video_start_ms: int
    video_end_ms: int
    minimum_frame_count: int
    maximum_frame_count: int
    maximum_rgb_bytes: int
    request_sha256: str
    schema_version: str = "voice_coach.phone_window_decode_request.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "voice_coach.phone_window_decode_request.v1":
            raise ValueError("phone-window decode request schema differs")
        for name in (
            "attempt_id_sha256",
            "binding_receipt_sha256",
            "container_sha256",
            "detection_receipt_sha256",
            "phone_anchor_sha256",
            "av_sync_receipt_sha256",
            "av_sync_offset_receipt_sha256",
            "context_contract_sha256",
            "decode_policy_sha256",
            "request_sha256",
        ):
            _require_sha256(getattr(self, name), name)
        _require_non_negative_integer(self.video_start_ms, "video_start_ms")
        _require_positive_integer(self.video_end_ms, "video_end_ms")
        if self.video_end_ms <= self.video_start_ms:
            raise ValueError("phone-window request must move forward")
        for name in (
            "minimum_frame_count",
            "maximum_frame_count",
            "maximum_rgb_bytes",
        ):
            _require_positive_integer(getattr(self, name), name)
        if self.maximum_frame_count < self.minimum_frame_count:
            raise ValueError("phone-window frame bounds differ")
        if self.request_sha256 != _sha256_document(self.unsigned_json_object()):
            raise ValueError("phone-window decode request digest differs")

    @classmethod
    def issue(
        cls,
        *,
        attempt_id_sha256: str,
        binding_receipt_sha256: str,
        container_sha256: str,
        detection_receipt_sha256: str,
        phone_anchor_sha256: str,
        av_sync_receipt_sha256: str,
        av_sync_offset_receipt_sha256: str,
        policy: PhoneWindowDecodePolicy,
        video_start_ms: int,
        video_end_ms: int,
    ) -> PhoneWindowDecodeRequest:
        if video_end_ms - video_start_ms > policy.maximum_window_duration_ms:
            raise ValueError("phone-window duration exceeds policy")
        unsigned = {
            "schema_version": "voice_coach.phone_window_decode_request.v1",
            "attempt_id_sha256": attempt_id_sha256,
            "binding_receipt_sha256": binding_receipt_sha256,
            "container_sha256": container_sha256,
            "detection_receipt_sha256": detection_receipt_sha256,
            "phone_anchor_sha256": phone_anchor_sha256,
            "av_sync_receipt_sha256": av_sync_receipt_sha256,
            "av_sync_offset_receipt_sha256": av_sync_offset_receipt_sha256,
            "context_contract_sha256": policy.context_contract_sha256,
            "decode_policy_sha256": policy.policy_sha256,
            "video_start_ms": video_start_ms,
            "video_end_ms": video_end_ms,
            "minimum_frame_count": policy.minimum_frame_count,
            "maximum_frame_count": policy.maximum_frame_count,
            "maximum_rgb_bytes": policy.maximum_rgb_bytes,
        }
        return cls(
            attempt_id_sha256=attempt_id_sha256,
            binding_receipt_sha256=binding_receipt_sha256,
            container_sha256=container_sha256,
            detection_receipt_sha256=detection_receipt_sha256,
            phone_anchor_sha256=phone_anchor_sha256,
            av_sync_receipt_sha256=av_sync_receipt_sha256,
            av_sync_offset_receipt_sha256=av_sync_offset_receipt_sha256,
            context_contract_sha256=policy.context_contract_sha256,
            decode_policy_sha256=policy.policy_sha256,
            video_start_ms=video_start_ms,
            video_end_ms=video_end_ms,
            minimum_frame_count=policy.minimum_frame_count,
            maximum_frame_count=policy.maximum_frame_count,
            maximum_rgb_bytes=policy.maximum_rgb_bytes,
            request_sha256=_sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "attempt_id_sha256": self.attempt_id_sha256,
            "binding_receipt_sha256": self.binding_receipt_sha256,
            "container_sha256": self.container_sha256,
            "detection_receipt_sha256": self.detection_receipt_sha256,
            "phone_anchor_sha256": self.phone_anchor_sha256,
            "av_sync_receipt_sha256": self.av_sync_receipt_sha256,
            "av_sync_offset_receipt_sha256": self.av_sync_offset_receipt_sha256,
            "context_contract_sha256": self.context_contract_sha256,
            "decode_policy_sha256": self.decode_policy_sha256,
            "video_start_ms": self.video_start_ms,
            "video_end_ms": self.video_end_ms,
            "minimum_frame_count": self.minimum_frame_count,
            "maximum_frame_count": self.maximum_frame_count,
            "maximum_rgb_bytes": self.maximum_rgb_bytes,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "request_sha256": self.request_sha256}


@dataclass(frozen=True, slots=True)
class PhoneWindowDecodeReceipt:
    """Exact bounded FFmpeg frame lineage for one host-issued phone window."""

    attempt_id_sha256: str
    binding_receipt_sha256: str
    container_sha256: str
    detection_receipt_sha256: str
    phone_anchor_sha256: str
    av_sync_receipt_sha256: str
    av_sync_offset_receipt_sha256: str
    context_contract_sha256: str
    decode_policy_sha256: str
    request_sha256: str
    video_stream_index: int
    video_start_ms: int
    video_end_ms: int
    selected_frame_indices: tuple[int, ...]
    decoded_timestamps_us: tuple[int, ...]
    width: int
    height: int
    decoded_rgb_sha256: str
    ffmpeg_revision: str
    decoder_code_sha256: str
    receipt_sha256: str
    schema_version: str = PHONE_WINDOW_DECODE_RECEIPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PHONE_WINDOW_DECODE_RECEIPT_SCHEMA_VERSION:
            raise ValueError("phone-window decode receipt schema differs")
        for name in (
            "attempt_id_sha256",
            "binding_receipt_sha256",
            "container_sha256",
            "detection_receipt_sha256",
            "phone_anchor_sha256",
            "av_sync_receipt_sha256",
            "av_sync_offset_receipt_sha256",
            "context_contract_sha256",
            "decode_policy_sha256",
            "request_sha256",
            "decoded_rgb_sha256",
            "decoder_code_sha256",
            "receipt_sha256",
        ):
            _require_sha256(getattr(self, name), name)
        _require_non_negative_integer(self.video_stream_index, "video_stream_index")
        _require_non_negative_integer(self.video_start_ms, "video_start_ms")
        _require_positive_integer(self.video_end_ms, "video_end_ms")
        if self.video_end_ms <= self.video_start_ms:
            raise ValueError("decoded phone window must move forward")
        if not self.selected_frame_indices or (
            len(self.selected_frame_indices) != len(self.decoded_timestamps_us)
        ):
            raise ValueError("decoded phone-window frame lineage differs")
        previous_index: int | None = None
        previous_timestamp: int | None = None
        for frame_index, timestamp_us in zip(
            self.selected_frame_indices,
            self.decoded_timestamps_us,
            strict=True,
        ):
            _require_non_negative_integer(frame_index, "selected_frame_indices[]")
            _require_integer(timestamp_us, "decoded_timestamps_us[]")
            if previous_index is not None and frame_index <= previous_index:
                raise ValueError("decoded phone-window indices must increase")
            if previous_timestamp is not None and timestamp_us <= previous_timestamp:
                raise ValueError("decoded phone-window timestamps must increase")
            previous_index = frame_index
            previous_timestamp = timestamp_us
        _require_positive_integer(self.width, "width")
        _require_positive_integer(self.height, "height")
        _require_identifier(self.ffmpeg_revision, "ffmpeg_revision")
        if self.receipt_sha256 != _sha256_document(self.unsigned_json_object()):
            raise ValueError("phone-window decode receipt digest differs")

    @classmethod
    def issue(
        cls,
        *,
        request: PhoneWindowDecodeRequest,
        video_stream_index: int,
        selected_frame_indices: tuple[int, ...],
        decoded_timestamps_us: tuple[int, ...],
        width: int,
        height: int,
        decoded_rgb_sha256: str,
        ffmpeg_revision: str,
        decoder_code_sha256: str,
    ) -> PhoneWindowDecodeReceipt:
        unsigned = {
            "schema_version": PHONE_WINDOW_DECODE_RECEIPT_SCHEMA_VERSION,
            "attempt_id_sha256": request.attempt_id_sha256,
            "binding_receipt_sha256": request.binding_receipt_sha256,
            "container_sha256": request.container_sha256,
            "detection_receipt_sha256": request.detection_receipt_sha256,
            "phone_anchor_sha256": request.phone_anchor_sha256,
            "av_sync_receipt_sha256": request.av_sync_receipt_sha256,
            "av_sync_offset_receipt_sha256": (
                request.av_sync_offset_receipt_sha256
            ),
            "context_contract_sha256": request.context_contract_sha256,
            "decode_policy_sha256": request.decode_policy_sha256,
            "request_sha256": request.request_sha256,
            "video_stream_index": video_stream_index,
            "video_start_ms": request.video_start_ms,
            "video_end_ms": request.video_end_ms,
            "selected_frame_indices": list(selected_frame_indices),
            "decoded_timestamps_us": list(decoded_timestamps_us),
            "width": width,
            "height": height,
            "decoded_rgb_sha256": decoded_rgb_sha256,
            "ffmpeg_revision": ffmpeg_revision,
            "decoder_code_sha256": decoder_code_sha256,
        }
        return cls(
            attempt_id_sha256=request.attempt_id_sha256,
            binding_receipt_sha256=request.binding_receipt_sha256,
            container_sha256=request.container_sha256,
            detection_receipt_sha256=request.detection_receipt_sha256,
            phone_anchor_sha256=request.phone_anchor_sha256,
            av_sync_receipt_sha256=request.av_sync_receipt_sha256,
            av_sync_offset_receipt_sha256=(
                request.av_sync_offset_receipt_sha256
            ),
            context_contract_sha256=request.context_contract_sha256,
            decode_policy_sha256=request.decode_policy_sha256,
            request_sha256=request.request_sha256,
            video_stream_index=video_stream_index,
            video_start_ms=request.video_start_ms,
            video_end_ms=request.video_end_ms,
            selected_frame_indices=selected_frame_indices,
            decoded_timestamps_us=decoded_timestamps_us,
            width=width,
            height=height,
            decoded_rgb_sha256=decoded_rgb_sha256,
            ffmpeg_revision=ffmpeg_revision,
            decoder_code_sha256=decoder_code_sha256,
            receipt_sha256=_sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "attempt_id_sha256": self.attempt_id_sha256,
            "binding_receipt_sha256": self.binding_receipt_sha256,
            "container_sha256": self.container_sha256,
            "detection_receipt_sha256": self.detection_receipt_sha256,
            "phone_anchor_sha256": self.phone_anchor_sha256,
            "av_sync_receipt_sha256": self.av_sync_receipt_sha256,
            "av_sync_offset_receipt_sha256": (
                self.av_sync_offset_receipt_sha256
            ),
            "context_contract_sha256": self.context_contract_sha256,
            "decode_policy_sha256": self.decode_policy_sha256,
            "request_sha256": self.request_sha256,
            "video_stream_index": self.video_stream_index,
            "video_start_ms": self.video_start_ms,
            "video_end_ms": self.video_end_ms,
            "selected_frame_indices": list(self.selected_frame_indices),
            "decoded_timestamps_us": list(self.decoded_timestamps_us),
            "width": self.width,
            "height": self.height,
            "decoded_rgb_sha256": self.decoded_rgb_sha256,
            "ffmpeg_revision": self.ffmpeg_revision,
            "decoder_code_sha256": self.decoder_code_sha256,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> PhoneWindowDecodeReceipt:
        """Parse only the complete v2 receipt; v1 and loose mappings fail closed."""

        document = _require_mapping(value, "phone_window_decode_receipt")
        _require_exact_keys(
            document,
            {
                "schema_version",
                "attempt_id_sha256",
                "binding_receipt_sha256",
                "container_sha256",
                "detection_receipt_sha256",
                "phone_anchor_sha256",
                "av_sync_receipt_sha256",
                "av_sync_offset_receipt_sha256",
                "context_contract_sha256",
                "decode_policy_sha256",
                "request_sha256",
                "video_stream_index",
                "video_start_ms",
                "video_end_ms",
                "selected_frame_indices",
                "decoded_timestamps_us",
                "width",
                "height",
                "decoded_rgb_sha256",
                "ffmpeg_revision",
                "decoder_code_sha256",
                "receipt_sha256",
            },
            "phone_window_decode_receipt",
        )
        return cls(
            schema_version=_require_identifier(
                document["schema_version"], "schema_version"
            ),
            attempt_id_sha256=_require_sha256(
                document["attempt_id_sha256"], "attempt_id_sha256"
            ),
            binding_receipt_sha256=_require_sha256(
                document["binding_receipt_sha256"], "binding_receipt_sha256"
            ),
            container_sha256=_require_sha256(
                document["container_sha256"], "container_sha256"
            ),
            detection_receipt_sha256=_require_sha256(
                document["detection_receipt_sha256"],
                "detection_receipt_sha256",
            ),
            phone_anchor_sha256=_require_sha256(
                document["phone_anchor_sha256"], "phone_anchor_sha256"
            ),
            av_sync_receipt_sha256=_require_sha256(
                document["av_sync_receipt_sha256"], "av_sync_receipt_sha256"
            ),
            av_sync_offset_receipt_sha256=_require_sha256(
                document["av_sync_offset_receipt_sha256"],
                "av_sync_offset_receipt_sha256",
            ),
            context_contract_sha256=_require_sha256(
                document["context_contract_sha256"], "context_contract_sha256"
            ),
            decode_policy_sha256=_require_sha256(
                document["decode_policy_sha256"], "decode_policy_sha256"
            ),
            request_sha256=_require_sha256(
                document["request_sha256"], "request_sha256"
            ),
            video_stream_index=_require_non_negative_integer(
                document["video_stream_index"], "video_stream_index"
            ),
            video_start_ms=_require_non_negative_integer(
                document["video_start_ms"], "video_start_ms"
            ),
            video_end_ms=_require_positive_integer(
                document["video_end_ms"], "video_end_ms"
            ),
            selected_frame_indices=_require_integer_tuple(
                document["selected_frame_indices"],
                "selected_frame_indices",
                non_negative=True,
            ),
            decoded_timestamps_us=_require_integer_tuple(
                document["decoded_timestamps_us"], "decoded_timestamps_us"
            ),
            width=_require_positive_integer(document["width"], "width"),
            height=_require_positive_integer(document["height"], "height"),
            decoded_rgb_sha256=_require_sha256(
                document["decoded_rgb_sha256"], "decoded_rgb_sha256"
            ),
            ffmpeg_revision=_require_identifier(
                document["ffmpeg_revision"], "ffmpeg_revision"
            ),
            decoder_code_sha256=_require_sha256(
                document["decoder_code_sha256"], "decoder_code_sha256"
            ),
            receipt_sha256=_require_sha256(
                document["receipt_sha256"], "receipt_sha256"
            ),
        )


@dataclass(frozen=True, slots=True)
class Rgb24PhoneWindow:
    """Decoded RGB frames plus the exact request and FFmpeg lineage receipt."""

    request: PhoneWindowDecodeRequest
    sample: Rgb24Sample = field(repr=False)
    decode_receipt: PhoneWindowDecodeReceipt

    def __post_init__(self) -> None:
        receipt = self.decode_receipt
        sample = self.sample
        if (
            not isinstance(self.request, PhoneWindowDecodeRequest)
            or not isinstance(sample, Rgb24Sample)
            or not isinstance(receipt, PhoneWindowDecodeReceipt)
            or receipt.request_sha256 != self.request.request_sha256
            or receipt.attempt_id_sha256 != self.request.attempt_id_sha256
            or receipt.binding_receipt_sha256
            != self.request.binding_receipt_sha256
            or receipt.container_sha256 != self.request.container_sha256
            or receipt.detection_receipt_sha256
            != self.request.detection_receipt_sha256
            or receipt.phone_anchor_sha256 != self.request.phone_anchor_sha256
            or receipt.av_sync_receipt_sha256
            != self.request.av_sync_receipt_sha256
            or receipt.av_sync_offset_receipt_sha256
            != self.request.av_sync_offset_receipt_sha256
            or receipt.context_contract_sha256
            != self.request.context_contract_sha256
            or receipt.decode_policy_sha256 != self.request.decode_policy_sha256
            or receipt.video_start_ms != self.request.video_start_ms
            or receipt.video_end_ms != self.request.video_end_ms
            or sample.container_sha256 != receipt.container_sha256
            or sample.video_stream_index != receipt.video_stream_index
            or tuple(frame.timestamp_us for frame in sample.frames)
            != receipt.decoded_timestamps_us
            or any(
                frame.width != receipt.width or frame.height != receipt.height
                for frame in sample.frames
            )
            or decoded_rgb_sha256(sample) != receipt.decoded_rgb_sha256
        ):
            raise ValueError("RGB24 phone-window lineage differs")


@dataclass(frozen=True, slots=True)
class PhoneWindowTrackingReceipt:
    """Bind dense landmark tracking to the decoded selected-phone frames."""

    attempt_id_sha256: str
    binding_receipt_sha256: str
    detection_receipt_sha256: str
    phone_anchor_sha256: str
    phone_window_decode_receipt_sha256: str
    landmark_evidence_sha256: str
    model_sha256: str
    sample_timestamps_sha256: str
    tracking_code_sha256: str
    status: str
    receipt_sha256: str
    schema_version: str = PHONE_WINDOW_TRACKING_RECEIPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PHONE_WINDOW_TRACKING_RECEIPT_SCHEMA_VERSION:
            raise ValueError("phone-window tracking receipt schema differs")
        for name in (
            "attempt_id_sha256",
            "binding_receipt_sha256",
            "detection_receipt_sha256",
            "phone_anchor_sha256",
            "phone_window_decode_receipt_sha256",
            "landmark_evidence_sha256",
            "model_sha256",
            "sample_timestamps_sha256",
            "tracking_code_sha256",
            "receipt_sha256",
        ):
            _require_sha256(getattr(self, name), name)
        if self.status != "observed":
            raise ValueError("phone-window tracking must be observed")
        if self.receipt_sha256 != _sha256_document(self.unsigned_json_object()):
            raise ValueError("phone-window tracking receipt digest differs")

    @classmethod
    def issue(
        cls,
        *,
        window: Rgb24PhoneWindow,
        landmark_evidence_sha256: str,
        model_sha256: str,
        tracking_code_sha256: str,
    ) -> PhoneWindowTrackingReceipt:
        request = window.request
        timestamps_sha256 = _sha256_document(
            {
                "video_stream_index": window.sample.video_stream_index,
                "timestamps_us": [
                    frame.timestamp_us for frame in window.sample.frames
                ],
                "ffmpeg_revision": window.sample.ffmpeg_revision,
            }
        )
        unsigned = {
            "schema_version": PHONE_WINDOW_TRACKING_RECEIPT_SCHEMA_VERSION,
            "attempt_id_sha256": request.attempt_id_sha256,
            "binding_receipt_sha256": request.binding_receipt_sha256,
            "detection_receipt_sha256": request.detection_receipt_sha256,
            "phone_anchor_sha256": request.phone_anchor_sha256,
            "phone_window_decode_receipt_sha256": (
                window.decode_receipt.receipt_sha256
            ),
            "landmark_evidence_sha256": landmark_evidence_sha256,
            "model_sha256": model_sha256,
            "sample_timestamps_sha256": timestamps_sha256,
            "tracking_code_sha256": tracking_code_sha256,
            "status": "observed",
        }
        return cls(
            attempt_id_sha256=request.attempt_id_sha256,
            binding_receipt_sha256=request.binding_receipt_sha256,
            detection_receipt_sha256=request.detection_receipt_sha256,
            phone_anchor_sha256=request.phone_anchor_sha256,
            phone_window_decode_receipt_sha256=(
                window.decode_receipt.receipt_sha256
            ),
            landmark_evidence_sha256=landmark_evidence_sha256,
            model_sha256=model_sha256,
            sample_timestamps_sha256=timestamps_sha256,
            tracking_code_sha256=tracking_code_sha256,
            status="observed",
            receipt_sha256=_sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "attempt_id_sha256": self.attempt_id_sha256,
            "binding_receipt_sha256": self.binding_receipt_sha256,
            "detection_receipt_sha256": self.detection_receipt_sha256,
            "phone_anchor_sha256": self.phone_anchor_sha256,
            "phone_window_decode_receipt_sha256": (
                self.phone_window_decode_receipt_sha256
            ),
            "landmark_evidence_sha256": self.landmark_evidence_sha256,
            "model_sha256": self.model_sha256,
            "sample_timestamps_sha256": self.sample_timestamps_sha256,
            "tracking_code_sha256": self.tracking_code_sha256,
            "status": self.status,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> PhoneWindowTrackingReceipt:
        """Parse only a complete v2 tracking receipt with its code pin."""

        document = _require_mapping(value, "phone_window_tracking_receipt")
        _require_exact_keys(
            document,
            {
                "schema_version",
                "attempt_id_sha256",
                "binding_receipt_sha256",
                "detection_receipt_sha256",
                "phone_anchor_sha256",
                "phone_window_decode_receipt_sha256",
                "landmark_evidence_sha256",
                "model_sha256",
                "sample_timestamps_sha256",
                "tracking_code_sha256",
                "status",
                "receipt_sha256",
            },
            "phone_window_tracking_receipt",
        )
        return cls(
            schema_version=_require_identifier(
                document["schema_version"], "schema_version"
            ),
            attempt_id_sha256=_require_sha256(
                document["attempt_id_sha256"], "attempt_id_sha256"
            ),
            binding_receipt_sha256=_require_sha256(
                document["binding_receipt_sha256"], "binding_receipt_sha256"
            ),
            detection_receipt_sha256=_require_sha256(
                document["detection_receipt_sha256"],
                "detection_receipt_sha256",
            ),
            phone_anchor_sha256=_require_sha256(
                document["phone_anchor_sha256"], "phone_anchor_sha256"
            ),
            phone_window_decode_receipt_sha256=_require_sha256(
                document["phone_window_decode_receipt_sha256"],
                "phone_window_decode_receipt_sha256",
            ),
            landmark_evidence_sha256=_require_sha256(
                document["landmark_evidence_sha256"],
                "landmark_evidence_sha256",
            ),
            model_sha256=_require_sha256(
                document["model_sha256"], "model_sha256"
            ),
            sample_timestamps_sha256=_require_sha256(
                document["sample_timestamps_sha256"],
                "sample_timestamps_sha256",
            ),
            tracking_code_sha256=_require_sha256(
                document["tracking_code_sha256"], "tracking_code_sha256"
            ),
            status=_require_identifier(document["status"], "status"),
            receipt_sha256=_require_sha256(
                document["receipt_sha256"], "receipt_sha256"
            ),
        )


@dataclass(frozen=True, slots=True)
class RuntimePhoneWindowReceipt:
    """Attempt-local observed decision consumed by runtime eligibility."""

    attempt_id_sha256: str
    binding_receipt_sha256: str
    detection_receipt_sha256: str
    phone_anchor_sha256: str
    video_qc_receipt_sha256: str
    av_sync_receipt_sha256: str
    context_contract_sha256: str
    phone_window_decode_receipt_sha256: str
    phone_window_tracking_receipt_sha256: str
    video_start_ms: int
    video_end_ms: int
    phone_window_code_sha256: str
    phone_window_policy_revision: str
    phone_window_policy_sha256: str
    status: str
    receipt_sha256: str
    schema_version: str = RUNTIME_PHONE_WINDOW_RECEIPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != RUNTIME_PHONE_WINDOW_RECEIPT_SCHEMA_VERSION:
            raise ValueError("runtime phone-window schema differs")
        for name in (
            "attempt_id_sha256",
            "binding_receipt_sha256",
            "detection_receipt_sha256",
            "phone_anchor_sha256",
            "video_qc_receipt_sha256",
            "av_sync_receipt_sha256",
            "context_contract_sha256",
            "phone_window_decode_receipt_sha256",
            "phone_window_tracking_receipt_sha256",
            "phone_window_code_sha256",
            "phone_window_policy_sha256",
            "receipt_sha256",
        ):
            _require_sha256(getattr(self, name), name)
        _require_identifier(
            self.phone_window_policy_revision, "phone_window_policy_revision"
        )
        _require_non_negative_integer(self.video_start_ms, "video_start_ms")
        _require_positive_integer(self.video_end_ms, "video_end_ms")
        if self.video_end_ms <= self.video_start_ms:
            raise ValueError("runtime phone window must move forward")
        if self.status != "observed":
            raise ValueError("runtime phone window must be observed")
        if self.receipt_sha256 != _sha256_document(self.unsigned_json_object()):
            raise ValueError("runtime phone-window receipt digest differs")

    @classmethod
    def issue(
        cls,
        *,
        request: PhoneWindowDecodeRequest,
        video_qc_receipt_sha256: str,
        tracking_receipt: PhoneWindowTrackingReceipt,
        policy: PhoneWindowDecodePolicy,
    ) -> RuntimePhoneWindowReceipt:
        if (
            tracking_receipt.attempt_id_sha256 != request.attempt_id_sha256
            or tracking_receipt.binding_receipt_sha256
            != request.binding_receipt_sha256
            or tracking_receipt.detection_receipt_sha256
            != request.detection_receipt_sha256
            or tracking_receipt.phone_anchor_sha256
            != request.phone_anchor_sha256
        ):
            raise ValueError("phone-window tracking and request lineage differ")
        if (
            request.decode_policy_sha256 != policy.policy_sha256
            or request.context_contract_sha256 != policy.context_contract_sha256
            or request.minimum_frame_count != policy.minimum_frame_count
            or request.maximum_frame_count != policy.maximum_frame_count
            or request.maximum_rgb_bytes != policy.maximum_rgb_bytes
            or request.video_end_ms - request.video_start_ms
            > policy.maximum_window_duration_ms
        ):
            raise ValueError("phone-window request and policy lineage differ")
        code_sha256 = phone_window_code_sha256()
        unsigned = {
            "schema_version": RUNTIME_PHONE_WINDOW_RECEIPT_SCHEMA_VERSION,
            "attempt_id_sha256": request.attempt_id_sha256,
            "binding_receipt_sha256": request.binding_receipt_sha256,
            "detection_receipt_sha256": request.detection_receipt_sha256,
            "phone_anchor_sha256": request.phone_anchor_sha256,
            "video_qc_receipt_sha256": video_qc_receipt_sha256,
            "av_sync_receipt_sha256": request.av_sync_receipt_sha256,
            "context_contract_sha256": request.context_contract_sha256,
            "phone_window_decode_receipt_sha256": (
                tracking_receipt.phone_window_decode_receipt_sha256
            ),
            "phone_window_tracking_receipt_sha256": (
                tracking_receipt.receipt_sha256
            ),
            "video_start_ms": request.video_start_ms,
            "video_end_ms": request.video_end_ms,
            "phone_window_code_sha256": code_sha256,
            "phone_window_policy_revision": policy.policy_revision,
            "phone_window_policy_sha256": policy.policy_sha256,
            "status": "observed",
        }
        return cls(
            attempt_id_sha256=request.attempt_id_sha256,
            binding_receipt_sha256=request.binding_receipt_sha256,
            detection_receipt_sha256=request.detection_receipt_sha256,
            phone_anchor_sha256=request.phone_anchor_sha256,
            video_qc_receipt_sha256=video_qc_receipt_sha256,
            av_sync_receipt_sha256=request.av_sync_receipt_sha256,
            context_contract_sha256=request.context_contract_sha256,
            phone_window_decode_receipt_sha256=(
                tracking_receipt.phone_window_decode_receipt_sha256
            ),
            phone_window_tracking_receipt_sha256=(
                tracking_receipt.receipt_sha256
            ),
            video_start_ms=request.video_start_ms,
            video_end_ms=request.video_end_ms,
            phone_window_code_sha256=code_sha256,
            phone_window_policy_revision=policy.policy_revision,
            phone_window_policy_sha256=policy.policy_sha256,
            status="observed",
            receipt_sha256=_sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "attempt_id_sha256": self.attempt_id_sha256,
            "binding_receipt_sha256": self.binding_receipt_sha256,
            "detection_receipt_sha256": self.detection_receipt_sha256,
            "phone_anchor_sha256": self.phone_anchor_sha256,
            "video_qc_receipt_sha256": self.video_qc_receipt_sha256,
            "av_sync_receipt_sha256": self.av_sync_receipt_sha256,
            "context_contract_sha256": self.context_contract_sha256,
            "phone_window_decode_receipt_sha256": (
                self.phone_window_decode_receipt_sha256
            ),
            "phone_window_tracking_receipt_sha256": (
                self.phone_window_tracking_receipt_sha256
            ),
            "video_start_ms": self.video_start_ms,
            "video_end_ms": self.video_end_ms,
            "phone_window_code_sha256": self.phone_window_code_sha256,
            "phone_window_policy_revision": self.phone_window_policy_revision,
            "phone_window_policy_sha256": self.phone_window_policy_sha256,
            "status": self.status,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> RuntimePhoneWindowReceipt:
        """Parse only a complete v2 runtime receipt with code and policy pins."""

        document = _require_mapping(value, "runtime_phone_window_receipt")
        _require_exact_keys(
            document,
            {
                "schema_version",
                "attempt_id_sha256",
                "binding_receipt_sha256",
                "detection_receipt_sha256",
                "phone_anchor_sha256",
                "video_qc_receipt_sha256",
                "av_sync_receipt_sha256",
                "context_contract_sha256",
                "phone_window_decode_receipt_sha256",
                "phone_window_tracking_receipt_sha256",
                "video_start_ms",
                "video_end_ms",
                "phone_window_code_sha256",
                "phone_window_policy_revision",
                "phone_window_policy_sha256",
                "status",
                "receipt_sha256",
            },
            "runtime_phone_window_receipt",
        )
        return cls(
            schema_version=_require_identifier(
                document["schema_version"], "schema_version"
            ),
            attempt_id_sha256=_require_sha256(
                document["attempt_id_sha256"], "attempt_id_sha256"
            ),
            binding_receipt_sha256=_require_sha256(
                document["binding_receipt_sha256"], "binding_receipt_sha256"
            ),
            detection_receipt_sha256=_require_sha256(
                document["detection_receipt_sha256"],
                "detection_receipt_sha256",
            ),
            phone_anchor_sha256=_require_sha256(
                document["phone_anchor_sha256"], "phone_anchor_sha256"
            ),
            video_qc_receipt_sha256=_require_sha256(
                document["video_qc_receipt_sha256"],
                "video_qc_receipt_sha256",
            ),
            av_sync_receipt_sha256=_require_sha256(
                document["av_sync_receipt_sha256"], "av_sync_receipt_sha256"
            ),
            context_contract_sha256=_require_sha256(
                document["context_contract_sha256"], "context_contract_sha256"
            ),
            phone_window_decode_receipt_sha256=_require_sha256(
                document["phone_window_decode_receipt_sha256"],
                "phone_window_decode_receipt_sha256",
            ),
            phone_window_tracking_receipt_sha256=_require_sha256(
                document["phone_window_tracking_receipt_sha256"],
                "phone_window_tracking_receipt_sha256",
            ),
            video_start_ms=_require_non_negative_integer(
                document["video_start_ms"], "video_start_ms"
            ),
            video_end_ms=_require_positive_integer(
                document["video_end_ms"], "video_end_ms"
            ),
            phone_window_code_sha256=_require_sha256(
                document["phone_window_code_sha256"],
                "phone_window_code_sha256",
            ),
            phone_window_policy_revision=_require_identifier(
                document["phone_window_policy_revision"],
                "phone_window_policy_revision",
            ),
            phone_window_policy_sha256=_require_sha256(
                document["phone_window_policy_sha256"],
                "phone_window_policy_sha256",
            ),
            status=_require_identifier(document["status"], "status"),
            receipt_sha256=_require_sha256(
                document["receipt_sha256"], "receipt_sha256"
            ),
        )


@dataclass(frozen=True, slots=True)
class DensePhoneWindowEvidence:
    """Raw-media-free receipt bundle with restricted in-memory landmark evidence."""

    decode_receipt: PhoneWindowDecodeReceipt
    tracking_receipt: PhoneWindowTrackingReceipt
    runtime_receipt: RuntimePhoneWindowReceipt
    landmark_evidence: object = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.decode_receipt, PhoneWindowDecodeReceipt)
            or not isinstance(self.tracking_receipt, PhoneWindowTrackingReceipt)
            or not isinstance(self.runtime_receipt, RuntimePhoneWindowReceipt)
            or self.tracking_receipt.phone_window_decode_receipt_sha256
            != self.decode_receipt.receipt_sha256
            or self.runtime_receipt.phone_window_decode_receipt_sha256
            != self.decode_receipt.receipt_sha256
            or self.runtime_receipt.phone_window_tracking_receipt_sha256
            != self.tracking_receipt.receipt_sha256
            or self.runtime_receipt.attempt_id_sha256
            != self.tracking_receipt.attempt_id_sha256
            or self.runtime_receipt.binding_receipt_sha256
            != self.tracking_receipt.binding_receipt_sha256
            or self.runtime_receipt.detection_receipt_sha256
            != self.tracking_receipt.detection_receipt_sha256
            or self.runtime_receipt.phone_anchor_sha256
            != self.tracking_receipt.phone_anchor_sha256
            or self.tracking_receipt.attempt_id_sha256
            != self.decode_receipt.attempt_id_sha256
            or self.tracking_receipt.binding_receipt_sha256
            != self.decode_receipt.binding_receipt_sha256
            or self.tracking_receipt.detection_receipt_sha256
            != self.decode_receipt.detection_receipt_sha256
            or self.tracking_receipt.phone_anchor_sha256
            != self.decode_receipt.phone_anchor_sha256
            or self.runtime_receipt.av_sync_receipt_sha256
            != self.decode_receipt.av_sync_receipt_sha256
            or self.runtime_receipt.context_contract_sha256
            != self.decode_receipt.context_contract_sha256
            or self.runtime_receipt.video_start_ms
            != self.decode_receipt.video_start_ms
            or self.runtime_receipt.video_end_ms
            != self.decode_receipt.video_end_ms
            or self.runtime_receipt.phone_window_policy_sha256
            != self.decode_receipt.decode_policy_sha256
        ):
            raise ValueError("dense phone-window evidence lineage differs")


def decoded_rgb_sha256(sample: Rgb24Sample) -> str:
    """Return a raw-media-free digest over exact frame bytes and timestamps."""

    return _sha256_document(
        {
            "schema_version": "voice_coach.decoded_rgb24_phone_window.v1",
            "asset_handle_id": sample.asset_handle_id,
            "container_sha256": sample.container_sha256,
            "video_stream_index": sample.video_stream_index,
            "ffmpeg_revision": sample.ffmpeg_revision,
            "frames": [
                {
                    "timestamp_us": frame.timestamp_us,
                    "width": frame.width,
                    "height": frame.height,
                    "rgb24_sha256": hashlib.sha256(frame.rgb24).hexdigest(),
                }
                for frame in sample.frames
            ],
        }
    )


def phone_window_code_sha256() -> str:
    """Hash the exact contract/runtime source that issues the top DAG receipt."""

    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:
        raise ValueError("phone-window source code is unavailable") from None


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


def _require_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) for key in value
    ):
        raise ValueError(f"{name} must be an object with string keys")
    return value


def _require_exact_keys(
    value: Mapping[str, object], expected: set[str], name: str
) -> None:
    if set(value) != expected:
        raise ValueError(f"{name} fields differ")


def _require_integer_tuple(
    value: object,
    name: str,
    *,
    non_negative: bool = False,
) -> tuple[int, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    validator = _require_non_negative_integer if non_negative else _require_integer
    return tuple(validator(item, f"{name}[]") for item in value)


__all__ = [
    "DensePhoneWindowEvidence",
    "PHONE_WINDOW_DECODE_RECEIPT_SCHEMA_VERSION",
    "PHONE_WINDOW_TRACKING_RECEIPT_SCHEMA_VERSION",
    "PhoneWindowDecodePolicy",
    "PhoneWindowDecodeReceipt",
    "PhoneWindowDecodeRequest",
    "PhoneWindowTrackingReceipt",
    "PolicyValidatedAvSyncOffset",
    "Rgb24PhoneWindow",
    "RUNTIME_PHONE_WINDOW_RECEIPT_SCHEMA_VERSION",
    "RuntimePhoneWindowReceipt",
    "decoded_rgb_sha256",
    "phone_window_code_sha256",
]
