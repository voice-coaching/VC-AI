"""Calibration-pending video QC measurements over decoded RGB and landmarks.

The adapter intentionally cannot issue a pass or reliability decision.  It
only emits deterministic measurement candidates that a separately frozen P6
calibration policy may later interpret and that P8 may subsequently evaluate.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

from ..models import Rgb24Sample
from .mediapipe_lip_landmarks import (
    MediaPipeLandmarkBatch,
    MediaPipeLipGeometry,
)


MEASUREMENT_REVISION = "video_qc_rgb24_luma_edge_lip_v1"
_SCHEMA_VERSION = "voice_coach.video_qc_candidate.v2"
_PROVENANCE_SCHEMA_VERSION = "voice_coach.video_qc_provenance.v2"
_REASON_CODES = frozenset(
    {
        "video_qc_single_face_coverage_insufficient",
        "video_qc_single_face_geometry_unavailable",
    }
)


class VideoQcRuntimeError(RuntimeError):
    """Stable, path-free failure raised before a QC candidate can be issued."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class VideoQcPolicy:
    """Measurement policy only; its values are not production pass criteria."""

    underexposed_luma_max_8bit: int
    overexposed_luma_min_8bit: int
    lip_roi_padding_fraction: float
    minimum_single_face_frames: int
    maximum_total_rgb_bytes: int

    def __post_init__(self) -> None:
        for name in (
            "underexposed_luma_max_8bit",
            "overexposed_luma_min_8bit",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= 255
            ):
                raise ValueError(f"{name} must be an integer in [0, 255]")
        if self.underexposed_luma_max_8bit >= self.overexposed_luma_min_8bit:
            raise ValueError("luma extraction thresholds must be ordered")
        _require_finite_range(
            self.lip_roi_padding_fraction,
            "lip_roi_padding_fraction",
            minimum=0.0,
            maximum=0.25,
        )
        for name in ("minimum_single_face_frames", "maximum_total_rgb_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")

    def to_json_object(self) -> dict[str, object]:
        return {
            "underexposed_luma_max_8bit": self.underexposed_luma_max_8bit,
            "overexposed_luma_min_8bit": self.overexposed_luma_min_8bit,
            "lip_roi_padding_fraction": self.lip_roi_padding_fraction,
            "minimum_single_face_frames": self.minimum_single_face_frames,
            "maximum_total_rgb_bytes": self.maximum_total_rgb_bytes,
        }

    @property
    def receipt_sha256(self) -> str:
        return _sha256_document(
            {
                "schema_version": "voice_coach.video_qc_measurement_policy.v1",
                **self.to_json_object(),
            }
        )


@dataclass(frozen=True, slots=True)
class VideoQcMetrics:
    """Raw, calibratable measurements; no field is a pass/fail conclusion."""

    frame_count: int
    single_face_frame_count: int
    multiple_face_frame_count: int
    single_face_coverage: float
    multiple_face_frame_fraction: float
    mean_luma_8bit: float
    underexposed_pixel_fraction: float
    overexposed_pixel_fraction: float
    mean_neighbor_luma_delta_8bit: float
    lip_roi_frame_count: int
    lip_roi_mean_luma_8bit: float | None
    lip_roi_underexposed_pixel_fraction: float | None
    lip_roi_overexposed_pixel_fraction: float | None
    lip_roi_mean_neighbor_luma_delta_8bit: float | None
    track_transition_coverage: float
    longest_single_face_run_fraction: float
    maximum_lip_center_step_normalized: float | None

    def __post_init__(self) -> None:
        for name in (
            "frame_count",
            "single_face_frame_count",
            "multiple_face_frame_count",
            "lip_roi_frame_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.frame_count <= 0:
            raise ValueError("frame_count must be positive")
        if (
            self.single_face_frame_count + self.multiple_face_frame_count
            > self.frame_count
        ):
            raise ValueError("face frame counts exceed frame_count")
        if self.lip_roi_frame_count != self.single_face_frame_count:
            raise ValueError("lip ROI count must equal single-face frame count")

        for name in (
            "single_face_coverage",
            "multiple_face_frame_fraction",
            "underexposed_pixel_fraction",
            "overexposed_pixel_fraction",
            "track_transition_coverage",
            "longest_single_face_run_fraction",
        ):
            _require_finite_range(
                getattr(self, name), name, minimum=0.0, maximum=1.0
            )
        for name in ("mean_luma_8bit", "mean_neighbor_luma_delta_8bit"):
            _require_finite_range(
                getattr(self, name), name, minimum=0.0, maximum=255.0
            )
        if not math.isclose(
            self.single_face_coverage,
            self.single_face_frame_count / self.frame_count,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("single-face coverage differs from counts")
        if not math.isclose(
            self.multiple_face_frame_fraction,
            self.multiple_face_frame_count / self.frame_count,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("multiple-face fraction differs from counts")

        lip_values = (
            self.lip_roi_mean_luma_8bit,
            self.lip_roi_underexposed_pixel_fraction,
            self.lip_roi_overexposed_pixel_fraction,
            self.lip_roi_mean_neighbor_luma_delta_8bit,
        )
        if self.lip_roi_frame_count == 0:
            if any(value is not None for value in lip_values):
                raise ValueError("lip ROI metrics require lip ROI frames")
            if self.maximum_lip_center_step_normalized is not None:
                raise ValueError("lip center step requires tracked lip frames")
        else:
            if any(value is None for value in lip_values):
                raise ValueError("lip ROI frames require complete ROI metrics")
            _require_finite_range(
                self.lip_roi_mean_luma_8bit,
                "lip_roi_mean_luma_8bit",
                minimum=0.0,
                maximum=255.0,
            )
            for name in (
                "lip_roi_underexposed_pixel_fraction",
                "lip_roi_overexposed_pixel_fraction",
            ):
                _require_finite_range(
                    getattr(self, name), name, minimum=0.0, maximum=1.0
                )
            _require_finite_range(
                self.lip_roi_mean_neighbor_luma_delta_8bit,
                "lip_roi_mean_neighbor_luma_delta_8bit",
                minimum=0.0,
                maximum=255.0,
            )
        if self.maximum_lip_center_step_normalized is not None:
            _require_finite_range(
                self.maximum_lip_center_step_normalized,
                "maximum_lip_center_step_normalized",
                minimum=0.0,
                maximum=math.sqrt(2.0),
            )

    def to_json_object(self) -> dict[str, object]:
        return {
            "frame_count": self.frame_count,
            "single_face_frame_count": self.single_face_frame_count,
            "multiple_face_frame_count": self.multiple_face_frame_count,
            "single_face_coverage": self.single_face_coverage,
            "multiple_face_frame_fraction": self.multiple_face_frame_fraction,
            "mean_luma_8bit": self.mean_luma_8bit,
            "underexposed_pixel_fraction": self.underexposed_pixel_fraction,
            "overexposed_pixel_fraction": self.overexposed_pixel_fraction,
            "mean_neighbor_luma_delta_8bit": (
                self.mean_neighbor_luma_delta_8bit
            ),
            "lip_roi_frame_count": self.lip_roi_frame_count,
            "lip_roi_mean_luma_8bit": self.lip_roi_mean_luma_8bit,
            "lip_roi_underexposed_pixel_fraction": (
                self.lip_roi_underexposed_pixel_fraction
            ),
            "lip_roi_overexposed_pixel_fraction": (
                self.lip_roi_overexposed_pixel_fraction
            ),
            "lip_roi_mean_neighbor_luma_delta_8bit": (
                self.lip_roi_mean_neighbor_luma_delta_8bit
            ),
            "track_transition_coverage": self.track_transition_coverage,
            "longest_single_face_run_fraction": (
                self.longest_single_face_run_fraction
            ),
            "maximum_lip_center_step_normalized": (
                self.maximum_lip_center_step_normalized
            ),
        }


@dataclass(frozen=True, slots=True)
class VideoQcCandidateEvidence:
    """Raw-media-free candidate record awaiting P6/P8 interpretation."""

    status: str
    asset_handle_id: str
    container_sha256: str
    video_stream_index: int
    sample_timestamps_us: tuple[int, ...]
    landmark_timestamps_ms: tuple[int, ...]
    decoded_rgb_evidence_sha256: str
    landmark_evidence_sha256: str
    landmark_model_sha256: str
    policy_receipt_sha256: str
    measurement_revision: str
    measurement_adapter_code_sha256: str
    metrics: VideoQcMetrics
    reason_codes: tuple[str, ...]
    provenance_receipt_sha256: str
    receipt_sha256: str
    schema_version: str = _SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != _SCHEMA_VERSION:
            raise ValueError("video QC schema version differs")
        if self.status not in {"candidate_unvalidated", "unavailable"}:
            raise ValueError("video QC status differs")
        for name in (
            "asset_handle_id",
            "container_sha256",
            "decoded_rgb_evidence_sha256",
            "landmark_evidence_sha256",
            "landmark_model_sha256",
            "policy_receipt_sha256",
            "measurement_adapter_code_sha256",
            "provenance_receipt_sha256",
            "receipt_sha256",
        ):
            _require_sha256(getattr(self, name), name)
        if (
            isinstance(self.video_stream_index, bool)
            or not isinstance(self.video_stream_index, int)
            or self.video_stream_index < 0
        ):
            raise ValueError("video_stream_index must be non-negative")
        if self.measurement_revision != MEASUREMENT_REVISION:
            raise ValueError("video QC measurement revision differs")
        if not isinstance(self.metrics, VideoQcMetrics):
            raise ValueError("metrics must be VideoQcMetrics")
        if not self.sample_timestamps_us or (
            len(self.sample_timestamps_us) != len(self.landmark_timestamps_ms)
            or len(self.sample_timestamps_us) != self.metrics.frame_count
        ):
            raise ValueError("video QC timestamp counts differ")
        previous_us: int | None = None
        for timestamp_us, timestamp_ms in zip(
            self.sample_timestamps_us,
            self.landmark_timestamps_ms,
            strict=True,
        ):
            if (
                isinstance(timestamp_us, bool)
                or not isinstance(timestamp_us, int)
                or timestamp_us < 0
                or isinstance(timestamp_ms, bool)
                or not isinstance(timestamp_ms, int)
                or timestamp_ms != timestamp_us // 1000
            ):
                raise ValueError("video QC timestamp lineage differs")
            if previous_us is not None and timestamp_us <= previous_us:
                raise ValueError("video QC timestamps must increase")
            previous_us = timestamp_us
        if any(
            not isinstance(reason, str) or reason not in _REASON_CODES
            for reason in self.reason_codes
        ) or len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("video QC reason code differs")
        if self.status == "candidate_unvalidated":
            if self.reason_codes or self.metrics.single_face_frame_count <= 0:
                raise ValueError("video QC candidate has invalid reasons or coverage")
        elif not self.reason_codes:
            raise ValueError("unavailable video QC evidence needs a reason")
        if self.provenance_receipt_sha256 != _sha256_document(
            self.provenance_json_object()
        ):
            raise ValueError("video QC provenance receipt differs")
        if self.receipt_sha256 != _sha256_document(self.unsigned_json_object()):
            raise ValueError("video QC evidence receipt differs")

    def provenance_json_object(self) -> dict[str, object]:
        return {
            "schema_version": _PROVENANCE_SCHEMA_VERSION,
            "asset_handle_id": self.asset_handle_id,
            "container_sha256": self.container_sha256,
            "video_stream_index": self.video_stream_index,
            "sample_timestamps_us": list(self.sample_timestamps_us),
            "landmark_timestamps_ms": list(self.landmark_timestamps_ms),
            "decoded_rgb_evidence_sha256": self.decoded_rgb_evidence_sha256,
            "landmark_evidence_sha256": self.landmark_evidence_sha256,
            "landmark_model_sha256": self.landmark_model_sha256,
            "policy_receipt_sha256": self.policy_receipt_sha256,
            "measurement_revision": self.measurement_revision,
            "measurement_adapter_code_sha256": (
                self.measurement_adapter_code_sha256
            ),
        }

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            **{
                key: value
                for key, value in self.provenance_json_object().items()
                if key != "schema_version"
            },
            "metrics": self.metrics.to_json_object(),
            "reason_codes": list(self.reason_codes),
            "provenance_receipt_sha256": self.provenance_receipt_sha256,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}


class VideoQcCandidateAdapter:
    """Measure frame, face, exposure, edge, ROI, and track candidates."""

    def __init__(self, *, policy: VideoQcPolicy) -> None:
        self._policy = policy

    def measure(
        self,
        *,
        sample: Rgb24Sample,
        landmarks: MediaPipeLandmarkBatch,
    ) -> VideoQcCandidateEvidence:
        if not isinstance(sample, Rgb24Sample) or not isinstance(
            landmarks, MediaPipeLandmarkBatch
        ):
            raise VideoQcRuntimeError("video_qc_lineage_mismatch")
        sample_timestamps_us = tuple(frame.timestamp_us for frame in sample.frames)
        landmark_timestamps_ms = tuple(
            frame.timestamp_ms for frame in landmarks.frames
        )
        if (
            sample.asset_handle_id != landmarks.asset_handle_id
            or sample.container_sha256 != landmarks.container_sha256
            or len(sample.frames) != len(landmarks.frames)
            or landmark_timestamps_ms
            != tuple(timestamp // 1000 for timestamp in sample_timestamps_us)
        ):
            raise VideoQcRuntimeError("video_qc_lineage_mismatch")
        total_rgb_bytes = sum(len(frame.rgb24) for frame in sample.frames)
        if total_rgb_bytes > self._policy.maximum_total_rgb_bytes:
            raise VideoQcRuntimeError("video_qc_resource_limit_exceeded")

        decoded_rgb_evidence_sha256 = _decoded_rgb_evidence_sha256(sample)
        metrics = self._measure_metrics(sample=sample, landmarks=landmarks)
        if metrics.single_face_frame_count == 0:
            status = "unavailable"
            reasons = ("video_qc_single_face_geometry_unavailable",)
        elif metrics.single_face_frame_count < self._policy.minimum_single_face_frames:
            status = "unavailable"
            reasons = ("video_qc_single_face_coverage_insufficient",)
        else:
            status = "candidate_unvalidated"
            reasons = ()
        provenance = {
            "schema_version": _PROVENANCE_SCHEMA_VERSION,
            "asset_handle_id": sample.asset_handle_id,
            "container_sha256": sample.container_sha256,
            "video_stream_index": sample.video_stream_index,
            "sample_timestamps_us": list(sample_timestamps_us),
            "landmark_timestamps_ms": list(landmark_timestamps_ms),
            "decoded_rgb_evidence_sha256": decoded_rgb_evidence_sha256,
            "landmark_evidence_sha256": landmarks.evidence_sha256,
            "landmark_model_sha256": landmarks.model_sha256,
            "policy_receipt_sha256": self._policy.receipt_sha256,
            "measurement_revision": MEASUREMENT_REVISION,
            "measurement_adapter_code_sha256": (
                video_qc_measurement_code_sha256()
            ),
        }
        unsigned = {
            "schema_version": _SCHEMA_VERSION,
            "status": status,
            **{key: value for key, value in provenance.items() if key != "schema_version"},
            "metrics": metrics.to_json_object(),
            "reason_codes": list(reasons),
            "provenance_receipt_sha256": _sha256_document(provenance),
        }
        return VideoQcCandidateEvidence(
            status=status,
            asset_handle_id=sample.asset_handle_id,
            container_sha256=sample.container_sha256,
            video_stream_index=sample.video_stream_index,
            sample_timestamps_us=sample_timestamps_us,
            landmark_timestamps_ms=landmark_timestamps_ms,
            decoded_rgb_evidence_sha256=decoded_rgb_evidence_sha256,
            landmark_evidence_sha256=landmarks.evidence_sha256,
            landmark_model_sha256=landmarks.model_sha256,
            policy_receipt_sha256=self._policy.receipt_sha256,
            measurement_revision=MEASUREMENT_REVISION,
            measurement_adapter_code_sha256=(
                video_qc_measurement_code_sha256()
            ),
            metrics=metrics,
            reason_codes=reasons,
            provenance_receipt_sha256=unsigned["provenance_receipt_sha256"],
            receipt_sha256=_sha256_document(unsigned),
        )

    def _measure_metrics(
        self,
        *,
        sample: Rgb24Sample,
        landmarks: MediaPipeLandmarkBatch,
    ) -> VideoQcMetrics:
        total_luma = 0
        total_pixels = 0
        total_underexposed = 0
        total_overexposed = 0
        total_edge_delta = 0
        total_edge_count = 0
        roi_luma = 0
        roi_pixels = 0
        roi_underexposed = 0
        roi_overexposed = 0
        roi_edge_delta = 0
        roi_edge_count = 0
        single_face_count = 0
        multiple_face_count = 0
        tracked_transitions = 0
        longest_run = 0
        current_run = 0
        previous_center: tuple[float, float] | None = None
        maximum_center_step: float | None = None

        for decoded, detected in zip(
            sample.frames, landmarks.frames, strict=True
        ):
            lumas = _rgb24_lumas(decoded.rgb24)
            total_luma += sum(lumas)
            total_pixels += len(lumas)
            total_underexposed += sum(
                value <= self._policy.underexposed_luma_max_8bit
                for value in lumas
            )
            total_overexposed += sum(
                value >= self._policy.overexposed_luma_min_8bit
                for value in lumas
            )
            edge_delta, edge_count = _edge_delta(
                lumas=lumas,
                width=decoded.width,
                height=decoded.height,
                bounds=(0, 0, decoded.width, decoded.height),
            )
            total_edge_delta += edge_delta
            total_edge_count += edge_count

            if detected.face_count > 1:
                multiple_face_count += 1
            geometry = detected.lip_geometry
            if geometry is None:
                current_run = 0
                previous_center = None
                continue
            single_face_count += 1
            current_run += 1
            longest_run = max(longest_run, current_run)
            bounds = _lip_roi_bounds(
                geometry=geometry,
                width=decoded.width,
                height=decoded.height,
                padding=self._policy.lip_roi_padding_fraction,
            )
            x0, y0, x1, y1 = bounds
            roi_values = tuple(
                lumas[y * decoded.width + x]
                for y in range(y0, y1)
                for x in range(x0, x1)
            )
            roi_luma += sum(roi_values)
            roi_pixels += len(roi_values)
            roi_underexposed += sum(
                value <= self._policy.underexposed_luma_max_8bit
                for value in roi_values
            )
            roi_overexposed += sum(
                value >= self._policy.overexposed_luma_min_8bit
                for value in roi_values
            )
            edge_delta, edge_count = _edge_delta(
                lumas=lumas,
                width=decoded.width,
                height=decoded.height,
                bounds=bounds,
            )
            roi_edge_delta += edge_delta
            roi_edge_count += edge_count

            center = (
                (geometry.left_corner_x + geometry.right_corner_x) / 2.0,
                (geometry.upper_lip_y + geometry.lower_lip_y) / 2.0,
            )
            if previous_center is not None:
                tracked_transitions += 1
                step = math.hypot(
                    center[0] - previous_center[0],
                    center[1] - previous_center[1],
                )
                maximum_center_step = (
                    step
                    if maximum_center_step is None
                    else max(maximum_center_step, step)
                )
            previous_center = center

        frame_count = len(sample.frames)
        transition_count = max(0, frame_count - 1)
        has_roi = roi_pixels > 0
        return VideoQcMetrics(
            frame_count=frame_count,
            single_face_frame_count=single_face_count,
            multiple_face_frame_count=multiple_face_count,
            single_face_coverage=single_face_count / frame_count,
            multiple_face_frame_fraction=multiple_face_count / frame_count,
            mean_luma_8bit=total_luma / total_pixels,
            underexposed_pixel_fraction=total_underexposed / total_pixels,
            overexposed_pixel_fraction=total_overexposed / total_pixels,
            mean_neighbor_luma_delta_8bit=(
                total_edge_delta / total_edge_count if total_edge_count else 0.0
            ),
            lip_roi_frame_count=single_face_count,
            lip_roi_mean_luma_8bit=roi_luma / roi_pixels if has_roi else None,
            lip_roi_underexposed_pixel_fraction=(
                roi_underexposed / roi_pixels if has_roi else None
            ),
            lip_roi_overexposed_pixel_fraction=(
                roi_overexposed / roi_pixels if has_roi else None
            ),
            lip_roi_mean_neighbor_luma_delta_8bit=(
                roi_edge_delta / roi_edge_count
                if has_roi and roi_edge_count
                else (0.0 if has_roi else None)
            ),
            track_transition_coverage=(
                tracked_transitions / transition_count if transition_count else 0.0
            ),
            longest_single_face_run_fraction=longest_run / frame_count,
            maximum_lip_center_step_normalized=maximum_center_step,
        )


def _decoded_rgb_evidence_sha256(sample: Rgb24Sample) -> str:
    return _sha256_document(
        {
            "schema_version": "voice_coach.decoded_rgb_sample_evidence.v1",
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


def _rgb24_lumas(rgb24: bytes) -> tuple[int, ...]:
    return tuple(
        (77 * rgb24[index] + 150 * rgb24[index + 1] + 29 * rgb24[index + 2] + 128)
        >> 8
        for index in range(0, len(rgb24), 3)
    )


def _lip_roi_bounds(
    *,
    geometry: MediaPipeLipGeometry,
    width: int,
    height: int,
    padding: float,
) -> tuple[int, int, int, int]:
    x0 = max(0, min(width - 1, math.floor((geometry.left_corner_x - padding) * width)))
    x1 = max(x0 + 1, min(width, math.ceil((geometry.right_corner_x + padding) * width)))
    y0 = max(0, min(height - 1, math.floor((geometry.upper_lip_y - padding) * height)))
    y1 = max(y0 + 1, min(height, math.ceil((geometry.lower_lip_y + padding) * height)))
    return x0, y0, x1, y1


def _edge_delta(
    *,
    lumas: tuple[int, ...],
    width: int,
    height: int,
    bounds: tuple[int, int, int, int],
) -> tuple[int, int]:
    x0, y0, x1, y1 = bounds
    total = 0
    count = 0
    for y in range(y0, y1):
        row = y * width
        for x in range(x0, x1):
            current = lumas[row + x]
            if x + 1 < x1:
                total += abs(current - lumas[row + x + 1])
                count += 1
            if y + 1 < y1:
                total += abs(current - lumas[row + width + x])
                count += 1
    return total, count


def _require_sha256(value: object, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field_name} must be a lowercase SHA-256")


def _require_finite_range(
    value: object,
    field_name: str,
    *,
    minimum: float,
    maximum: float,
) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not minimum <= float(value) <= maximum
    ):
        raise ValueError(f"{field_name} must be finite and in [{minimum}, {maximum}]")


def _sha256_document(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def video_qc_measurement_code_sha256() -> str:
    """Return the exact loaded measurement-adapter source digest."""

    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:
        raise ValueError("video QC measurement source is unavailable") from None


__all__ = [
    "MEASUREMENT_REVISION",
    "VideoQcCandidateAdapter",
    "VideoQcCandidateEvidence",
    "VideoQcMetrics",
    "VideoQcPolicy",
    "VideoQcRuntimeError",
    "video_qc_measurement_code_sha256",
]
