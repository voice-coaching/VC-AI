"""Extract uncalibrated A/V sync-anchor candidates from lip motion and PCM.

This backend produces measurement candidates only.  It cannot issue a
reliability decision: P6 must calibrate the extraction policy and P8 must test
the frozen path before any candidate can support a visual claim.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import struct

from ..models import MediaProbe, RestrictedPcmAudio
from ..ports import RestrictedMediaStorePort
from .mediapipe_lip_landmarks import MediaPipeLandmarkBatch


MEASUREMENT_REVISION = "lip_audio_cross_correlation_v1"
_SCHEMA_VERSION = "voice_coach.lip_audio_av_sync_candidate.v2"
_LINEAGE_SCHEMA_VERSION = "voice_coach.lip_audio_av_sync_lineage.v2"
_REASON_CODES = frozenset(
    {
        "av_sync_landmark_coverage_insufficient",
        "av_sync_lip_motion_flat",
        "av_sync_audio_window_unavailable",
        "av_sync_anchor_candidates_absent",
    }
)


class AvSyncRuntimeError(RuntimeError):
    """Stable, path-free sync backend failure."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class LipAudioAvSyncPolicy:
    maximum_absolute_offset_ms: int
    offset_step_ms: int
    energy_window_ms: int
    minimum_landmark_frames: int
    maximum_anchor_count: int

    def __post_init__(self) -> None:
        for name in (
            "maximum_absolute_offset_ms",
            "offset_step_ms",
            "energy_window_ms",
            "minimum_landmark_frames",
            "maximum_anchor_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.offset_step_ms > self.maximum_absolute_offset_ms:
            raise ValueError("offset step must not exceed search range")

    def to_json_object(self) -> dict[str, int]:
        return {
            "maximum_absolute_offset_ms": self.maximum_absolute_offset_ms,
            "offset_step_ms": self.offset_step_ms,
            "energy_window_ms": self.energy_window_ms,
            "minimum_landmark_frames": self.minimum_landmark_frames,
            "maximum_anchor_count": self.maximum_anchor_count,
        }

    @property
    def policy_sha256(self) -> str:
        return _sha256_document(
            {
                "schema_version": "voice_coach.lip_audio_av_sync_policy.v1",
                **self.to_json_object(),
            }
        )


@dataclass(frozen=True, slots=True)
class AvSyncAnchorCandidate:
    audio_ms: int
    video_ms: int
    strength: float

    def __post_init__(self) -> None:
        if self.audio_ms < 0 or self.video_ms < 0:
            raise ValueError("sync anchor timestamps must be non-negative")
        if not math.isfinite(self.strength) or not 0.0 <= self.strength <= 1.0:
            raise ValueError("sync anchor strength must be in [0, 1]")

    def to_json_object(self) -> dict[str, object]:
        return {
            "audio_ms": self.audio_ms,
            "video_ms": self.video_ms,
            "strength": self.strength,
        }


@dataclass(frozen=True, slots=True)
class AvSyncCandidateEvidence:
    asset_handle_id: str
    audio_derivation_receipt_sha256: str
    audio_source_sha256: str
    video_source_sha256: str
    audio_stream_index: int
    audio_start_time_us: int
    video_stream_index: int
    video_start_time_us: int
    landmark_evidence_sha256: str
    landmark_model_sha256: str
    measurement_policy_sha256: str
    measurement_revision: str
    measurement_adapter_code_sha256: str
    status: str
    candidate_offset_ms: int | None
    correlation: float | None
    anchors: tuple[AvSyncAnchorCandidate, ...]
    reason_codes: tuple[str, ...]
    lineage_receipt_sha256: str
    receipt_sha256: str
    schema_version: str = _SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != _SCHEMA_VERSION:
            raise ValueError("A/V sync candidate schema differs")
        for name in (
            "asset_handle_id",
            "audio_derivation_receipt_sha256",
            "audio_source_sha256",
            "video_source_sha256",
            "landmark_evidence_sha256",
            "landmark_model_sha256",
            "measurement_policy_sha256",
            "measurement_adapter_code_sha256",
            "lineage_receipt_sha256",
            "receipt_sha256",
        ):
            if not _is_sha256(getattr(self, name)):
                raise ValueError(f"{name} must be lowercase SHA-256")
        if self.measurement_revision != MEASUREMENT_REVISION:
            raise ValueError("A/V sync measurement revision differs")
        for name in (
            "audio_stream_index",
            "audio_start_time_us",
            "video_stream_index",
            "video_start_time_us",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.status not in {"candidate_unvalidated", "unavailable"}:
            raise ValueError("A/V sync candidate status differs")
        if self.status == "candidate_unvalidated":
            if self.candidate_offset_ms is None or self.correlation is None:
                raise ValueError("sync candidate needs offset and correlation")
            if not math.isfinite(self.correlation) or not -1.0 <= self.correlation <= 1.0:
                raise ValueError("sync correlation must be in [-1, 1]")
            if not self.anchors or self.reason_codes:
                raise ValueError("sync candidate needs anchors and no failure reasons")
        elif (
            self.candidate_offset_ms is not None
            or self.correlation is not None
            or self.anchors
            or not self.reason_codes
        ):
            raise ValueError("unavailable sync evidence must be value-free")
        if (
            len(set(self.reason_codes)) != len(self.reason_codes)
            or any(reason not in _REASON_CODES for reason in self.reason_codes)
        ):
            raise ValueError("A/V sync reason code differs")
        if self.lineage_receipt_sha256 != _sha256_document(
            self.lineage_json_object()
        ):
            raise ValueError("sync lineage receipt differs")
        if self.receipt_sha256 != _sha256_document(self.unsigned_json_object()):
            raise ValueError("sync receipt digest differs")

    def lineage_json_object(self) -> dict[str, object]:
        return {
            "schema_version": _LINEAGE_SCHEMA_VERSION,
            "asset_handle_id": self.asset_handle_id,
            "audio_derivation_receipt_sha256": (
                self.audio_derivation_receipt_sha256
            ),
            "audio_source_sha256": self.audio_source_sha256,
            "video_source_sha256": self.video_source_sha256,
            "audio_stream_index": self.audio_stream_index,
            "audio_start_time_us": self.audio_start_time_us,
            "video_stream_index": self.video_stream_index,
            "video_start_time_us": self.video_start_time_us,
            "landmark_evidence_sha256": self.landmark_evidence_sha256,
            "landmark_model_sha256": self.landmark_model_sha256,
            "measurement_policy_sha256": self.measurement_policy_sha256,
            "measurement_revision": self.measurement_revision,
            "measurement_adapter_code_sha256": (
                self.measurement_adapter_code_sha256
            ),
        }

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            **{
                key: value
                for key, value in self.lineage_json_object().items()
                if key != "schema_version"
            },
            "status": self.status,
            "candidate_offset_ms": self.candidate_offset_ms,
            "correlation": self.correlation,
            "anchors": [anchor.to_json_object() for anchor in self.anchors],
            "reason_codes": list(self.reason_codes),
            "lineage_receipt_sha256": self.lineage_receipt_sha256,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}


class LipAudioAvSyncAdapter:
    """Cross-correlate visible lip aperture with local PCM energy."""

    def __init__(
        self,
        *,
        media_store: RestrictedMediaStorePort,
        policy: LipAudioAvSyncPolicy,
    ) -> None:
        self._media_store = media_store
        self._policy = policy

    def measure(
        self,
        *,
        audio: RestrictedPcmAudio,
        landmarks: MediaPipeLandmarkBatch,
        probe: MediaProbe,
    ) -> AvSyncCandidateEvidence:
        if (
            audio.asset_handle_id != probe.asset_handle_id
            or audio.derivation_receipt.container_sha256 != probe.container_sha256
            or landmarks.asset_handle_id != probe.asset_handle_id
            or landmarks.container_sha256 != probe.container_sha256
        ):
            raise AvSyncRuntimeError("av_sync_lineage_mismatch")

        visible = tuple(
            (frame.timestamp_ms, frame.lip_geometry.aperture_ratio)
            for frame in landmarks.frames
            if frame.lip_geometry is not None
        )
        receipt_context = {
            "schema_version": _LINEAGE_SCHEMA_VERSION,
            "asset_handle_id": probe.asset_handle_id,
            "audio_derivation_receipt_sha256": (
                audio.derivation_receipt.receipt_sha256
            ),
            "audio_source_sha256": audio.derivation_receipt.pcm_sha256,
            "video_source_sha256": probe.container_sha256,
            "audio_stream_index": probe.audio_stream.stream_index,
            "audio_start_time_us": probe.audio_stream.start_time_us,
            "video_stream_index": probe.video_stream.stream_index,
            "video_start_time_us": probe.video_stream.start_time_us,
            "landmark_evidence_sha256": landmarks.evidence_sha256,
            "landmark_model_sha256": landmarks.model_sha256,
            "measurement_policy_sha256": self._policy.policy_sha256,
            "measurement_revision": MEASUREMENT_REVISION,
            "measurement_adapter_code_sha256": (
                av_sync_measurement_code_sha256()
            ),
        }
        if len(visible) < self._policy.minimum_landmark_frames:
            return self._unavailable(
                reason="av_sync_landmark_coverage_insufficient",
                receipt_context=receipt_context,
            )

        pcm = self._read_pcm(audio)
        video_times = tuple(item[0] for item in visible)
        apertures = tuple(item[1] for item in visible)
        if _variance(apertures) <= 1e-12:
            return self._unavailable(
                reason="av_sync_lip_motion_flat",
                receipt_context=receipt_context,
            )

        audio_start_ms = _rounded_milliseconds(probe.audio_stream.start_time_us)
        candidates: list[
            tuple[
                float,
                int,
                tuple[int, ...],
                tuple[float, ...],
                tuple[float, ...],
            ]
        ] = []
        maximum = self._policy.maximum_absolute_offset_ms
        for offset_ms in range(
            -maximum,
            maximum + 1,
            self._policy.offset_step_ms,
        ):
            overlap = tuple(
                (video_ms, aperture, energy)
                for video_ms, aperture in zip(
                    video_times, apertures, strict=True
                )
                if (
                    energy := self._energy_at(
                    pcm,
                    absolute_audio_ms=video_ms - offset_ms,
                    audio_start_ms=audio_start_ms,
                    )
                )
                is not None
            )
            if len(overlap) < self._policy.minimum_landmark_frames:
                continue
            overlap_times = tuple(item[0] for item in overlap)
            overlap_apertures = tuple(item[1] for item in overlap)
            numeric_energies = tuple(float(item[2]) for item in overlap)
            correlation = _correlation(overlap_apertures, numeric_energies)
            if correlation is not None:
                candidates.append(
                    (
                        correlation,
                        offset_ms,
                        overlap_times,
                        overlap_apertures,
                        numeric_energies,
                    )
                )
        if not candidates:
            return self._unavailable(
                reason="av_sync_audio_window_unavailable",
                receipt_context=receipt_context,
            )

        correlation, offset_ms, anchor_times, anchor_apertures, energies = max(
            candidates,
            key=lambda item: (item[0], -abs(item[1]), -item[1]),
        )
        anchors = _anchor_candidates(
            video_times=anchor_times,
            apertures=anchor_apertures,
            energies=energies,
            offset_ms=offset_ms,
            maximum_anchor_count=self._policy.maximum_anchor_count,
        )
        if not anchors:
            return self._unavailable(
                reason="av_sync_anchor_candidates_absent",
                receipt_context=receipt_context,
            )
        unsigned = {
            "schema_version": _SCHEMA_VERSION,
            **{
                key: value
                for key, value in receipt_context.items()
                if key != "schema_version"
            },
            "status": "candidate_unvalidated",
            "candidate_offset_ms": offset_ms,
            "correlation": correlation,
            "anchors": [anchor.to_json_object() for anchor in anchors],
            "reason_codes": [],
            "lineage_receipt_sha256": _sha256_document(receipt_context),
        }
        return AvSyncCandidateEvidence(
            asset_handle_id=probe.asset_handle_id,
            audio_derivation_receipt_sha256=(
                audio.derivation_receipt.receipt_sha256
            ),
            audio_source_sha256=audio.derivation_receipt.pcm_sha256,
            video_source_sha256=probe.container_sha256,
            audio_stream_index=probe.audio_stream.stream_index,
            audio_start_time_us=probe.audio_stream.start_time_us,
            video_stream_index=probe.video_stream.stream_index,
            video_start_time_us=probe.video_stream.start_time_us,
            landmark_evidence_sha256=landmarks.evidence_sha256,
            landmark_model_sha256=landmarks.model_sha256,
            measurement_policy_sha256=self._policy.policy_sha256,
            measurement_revision=MEASUREMENT_REVISION,
            measurement_adapter_code_sha256=av_sync_measurement_code_sha256(),
            status="candidate_unvalidated",
            candidate_offset_ms=offset_ms,
            correlation=correlation,
            anchors=anchors,
            reason_codes=(),
            lineage_receipt_sha256=unsigned["lineage_receipt_sha256"],
            receipt_sha256=_sha256_document(unsigned),
        )

    def _read_pcm(self, audio: RestrictedPcmAudio) -> tuple[int, ...]:
        digest = hashlib.sha256()
        payload = bytearray()
        try:
            with self._media_store.open_derived_audio(audio) as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes):
                        raise AvSyncRuntimeError("av_sync_audio_read_failed")
                    payload.extend(chunk)
                    digest.update(chunk)
        except AvSyncRuntimeError:
            raise
        except Exception:
            raise AvSyncRuntimeError("av_sync_audio_read_failed") from None
        if (
            len(payload) != audio.byte_count
            or len(payload) % 2
            or digest.hexdigest() != audio.derivation_receipt.pcm_sha256
        ):
            raise AvSyncRuntimeError("av_sync_audio_integrity_mismatch")
        return tuple(value[0] for value in struct.iter_unpack("<h", payload))

    def _energy_at(
        self,
        pcm: tuple[int, ...],
        *,
        absolute_audio_ms: int,
        audio_start_ms: int,
    ) -> float | None:
        relative_ms = absolute_audio_ms - audio_start_ms
        half_window_samples = self._policy.energy_window_ms * 16_000 // 2_000
        center = relative_ms * 16
        start = center - half_window_samples
        end = center + half_window_samples
        if start < 0 or end > len(pcm) or end <= start:
            return None
        mean_square = sum(sample * sample for sample in pcm[start:end]) / (end - start)
        return math.sqrt(mean_square) / 32768.0

    @staticmethod
    def _unavailable(
        *, reason: str, receipt_context: dict[str, object]
    ) -> AvSyncCandidateEvidence:
        unsigned = {
            "schema_version": _SCHEMA_VERSION,
            **{
                key: value
                for key, value in receipt_context.items()
                if key != "schema_version"
            },
            "status": "unavailable",
            "candidate_offset_ms": None,
            "correlation": None,
            "anchors": [],
            "reason_codes": [reason],
            "lineage_receipt_sha256": _sha256_document(receipt_context),
        }
        return AvSyncCandidateEvidence(
            asset_handle_id=str(receipt_context["asset_handle_id"]),
            audio_derivation_receipt_sha256=str(
                receipt_context["audio_derivation_receipt_sha256"]
            ),
            audio_source_sha256=str(receipt_context["audio_source_sha256"]),
            video_source_sha256=str(receipt_context["video_source_sha256"]),
            audio_stream_index=int(receipt_context["audio_stream_index"]),
            audio_start_time_us=int(receipt_context["audio_start_time_us"]),
            video_stream_index=int(receipt_context["video_stream_index"]),
            video_start_time_us=int(receipt_context["video_start_time_us"]),
            landmark_evidence_sha256=str(
                receipt_context["landmark_evidence_sha256"]
            ),
            landmark_model_sha256=str(
                receipt_context["landmark_model_sha256"]
            ),
            measurement_policy_sha256=str(
                receipt_context["measurement_policy_sha256"]
            ),
            measurement_revision=str(receipt_context["measurement_revision"]),
            measurement_adapter_code_sha256=str(
                receipt_context["measurement_adapter_code_sha256"]
            ),
            status="unavailable",
            candidate_offset_ms=None,
            correlation=None,
            anchors=(),
            reason_codes=(reason,),
            lineage_receipt_sha256=unsigned["lineage_receipt_sha256"],
            receipt_sha256=_sha256_document(unsigned),
        )


def _anchor_candidates(
    *,
    video_times: tuple[int, ...],
    apertures: tuple[float, ...],
    energies: tuple[float, ...],
    offset_ms: int,
    maximum_anchor_count: int,
) -> tuple[AvSyncAnchorCandidate, ...]:
    centered_apertures = _center(apertures)
    centered_energies = _center(energies)
    raw_strengths = tuple(
        abs(aperture * energy)
        for aperture, energy in zip(
            centered_apertures,
            centered_energies,
            strict=True,
        )
    )
    maximum_strength = max(raw_strengths, default=0.0)
    if maximum_strength <= 0:
        return ()
    ranked = sorted(
        range(len(video_times)),
        key=lambda index: (-raw_strengths[index], video_times[index]),
    )[:maximum_anchor_count]
    anchors = tuple(
        AvSyncAnchorCandidate(
            audio_ms=video_times[index] - offset_ms,
            video_ms=video_times[index],
            strength=raw_strengths[index] / maximum_strength,
        )
        for index in sorted(ranked, key=video_times.__getitem__)
        if video_times[index] - offset_ms >= 0
    )
    return anchors


def _center(values: tuple[float, ...]) -> tuple[float, ...]:
    mean = sum(values) / len(values)
    return tuple(value - mean for value in values)


def _variance(values: tuple[float, ...]) -> float:
    centered = _center(values)
    return sum(value * value for value in centered) / len(centered)


def _correlation(
    left: tuple[float, ...], right: tuple[float, ...]
) -> float | None:
    centered_left = _center(left)
    centered_right = _center(right)
    left_norm = math.sqrt(sum(value * value for value in centered_left))
    right_norm = math.sqrt(sum(value * value for value in centered_right))
    if left_norm <= 1e-12 or right_norm <= 1e-12:
        return None
    result = sum(
        left_value * right_value
        for left_value, right_value in zip(
            centered_left,
            centered_right,
            strict=True,
        )
    ) / (left_norm * right_norm)
    return max(-1.0, min(1.0, result))


def _rounded_milliseconds(microseconds: int) -> int:
    magnitude = (abs(microseconds) + 500) // 1000
    return magnitude if microseconds >= 0 else -magnitude


def _sha256_document(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def av_sync_measurement_code_sha256() -> str:
    """Return the exact loaded measurement-adapter source digest."""

    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:
        raise ValueError("A/V sync measurement source is unavailable") from None


__all__ = [
    "AvSyncAnchorCandidate",
    "AvSyncCandidateEvidence",
    "AvSyncRuntimeError",
    "LipAudioAvSyncAdapter",
    "LipAudioAvSyncPolicy",
    "MEASUREMENT_REVISION",
    "av_sync_measurement_code_sha256",
]
