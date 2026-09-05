"""In-memory P6 lip trajectories with aggregate-only lineage receipts.

The mapped landmark arrays are derived biometric geometry.  They remain inside
the process and are represented in receipts only by a canonical digest.  This
model deliberately carries neither pronunciation truth nor corrective-action
truth; those require separately reviewed labels and promotion evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from ..errors import VisualFeedbackContractError
from .integrity import require_sha256, sha256_document
from .p6_geometry_schema import (
    AIHUB68_KEYPOINT_NAMES,
    MEDIAPIPE_LIP_CANDIDATE_INDICES,
)


P6_LIP_TRAJECTORY_RECEIPT_SCHEMA_VERSION = (
    "voice_coach.p6_lip_trajectory_receipt.v1"
)
P6_LIP_TRAJECTORY_REVISION = "p6_mapped_24point_shadow_v1"
P6_VISIBLE_LIP_FEATURE_IDS = (
    "mouth_width_fraction",
    "outer_aperture_ratio",
    "inner_aperture_ratio",
    "outer_area_ratio",
    "inner_area_ratio",
    "corner_height_asymmetry_ratio",
)


def _require_normalized_point(
    value: tuple[float, float], name: str
) -> tuple[float, float]:
    if not isinstance(value, tuple) or len(value) != 2:
        raise VisualFeedbackContractError(f"{name} must be an xy tuple")
    x, y = value
    if any(
        isinstance(coordinate, bool)
        or not isinstance(coordinate, (int, float))
        or not math.isfinite(float(coordinate))
        or not 0.0 <= float(coordinate) <= 1.0
        for coordinate in (x, y)
    ):
        raise VisualFeedbackContractError(f"{name} must be normalized")
    return float(x), float(y)


def _require_finite_point(
    value: tuple[float, float], name: str
) -> tuple[float, float]:
    if not isinstance(value, tuple) or len(value) != 2:
        raise VisualFeedbackContractError(f"{name} must be an xy tuple")
    x, y = value
    if any(
        isinstance(coordinate, bool)
        or not isinstance(coordinate, (int, float))
        or not math.isfinite(float(coordinate))
        for coordinate in (x, y)
    ):
        raise VisualFeedbackContractError(f"{name} must be finite")
    return float(x), float(y)


@dataclass(frozen=True, slots=True)
class P6LipCandidateFrame:
    """One timestamped 40-candidate observation before P6 mapping."""

    timestamp_ms: int
    face_count: int
    candidate_points_xy: tuple[tuple[float, float], ...] | None

    def __post_init__(self) -> None:
        if (
            isinstance(self.timestamp_ms, bool)
            or not isinstance(self.timestamp_ms, int)
            or self.timestamp_ms < 0
        ):
            raise VisualFeedbackContractError(
                "P6 trajectory timestamp must be non-negative"
            )
        if (
            isinstance(self.face_count, bool)
            or not isinstance(self.face_count, int)
            or self.face_count < 0
        ):
            raise VisualFeedbackContractError(
                "P6 trajectory face count must be non-negative"
            )
        if (self.face_count == 1) != (self.candidate_points_xy is not None):
            raise VisualFeedbackContractError(
                "P6 trajectory candidates require exactly one face"
            )
        if self.candidate_points_xy is None:
            return
        if len(self.candidate_points_xy) != len(
            MEDIAPIPE_LIP_CANDIDATE_INDICES
        ):
            raise VisualFeedbackContractError(
                "P6 trajectory candidate point count differs"
            )
        for index, point in enumerate(self.candidate_points_xy):
            _require_normalized_point(point, f"candidate_points_xy[{index}]")


@dataclass(frozen=True, slots=True)
class P6VisibleLipShape:
    """Visible two-dimensional measurements without phonetic interpretation."""

    mouth_width_fraction: float
    outer_aperture_ratio: float
    inner_aperture_ratio: float
    outer_area_ratio: float
    inner_area_ratio: float
    corner_height_asymmetry_ratio: float

    def __post_init__(self) -> None:
        for name in P6_VISIBLE_LIP_FEATURE_IDS:
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value < 0.0
            ):
                raise VisualFeedbackContractError(
                    f"P6 visible lip feature {name} is invalid"
                )
        if self.mouth_width_fraction > math.sqrt(2.0):
            raise VisualFeedbackContractError(
                "P6 visible mouth width exceeds normalized image bounds"
            )

    def to_json_object(self) -> dict[str, float]:
        return {
            name: float(getattr(self, name))
            for name in P6_VISIBLE_LIP_FEATURE_IDS
        }


@dataclass(frozen=True, slots=True)
class P6MappedLipFrame:
    """One mapped frame; the 24-point array must never enter a public receipt."""

    timestamp_ms: int
    face_count: int
    normalized_points_xy: tuple[tuple[float, float], ...] | None
    visible_shape: P6VisibleLipShape | None
    shape_change_rate_per_second: float | None

    def __post_init__(self) -> None:
        if (
            isinstance(self.timestamp_ms, bool)
            or not isinstance(self.timestamp_ms, int)
            or self.timestamp_ms < 0
        ):
            raise VisualFeedbackContractError(
                "P6 mapped timestamp must be non-negative"
            )
        if (
            isinstance(self.face_count, bool)
            or not isinstance(self.face_count, int)
            or self.face_count < 0
        ):
            raise VisualFeedbackContractError(
                "P6 mapped face count must be non-negative"
            )
        has_geometry = self.normalized_points_xy is not None
        if (self.face_count == 1) != has_geometry:
            raise VisualFeedbackContractError(
                "P6 mapped geometry requires exactly one face"
            )
        if has_geometry != (self.visible_shape is not None):
            raise VisualFeedbackContractError(
                "P6 mapped geometry and visible shape differ"
            )
        if self.normalized_points_xy is not None:
            if len(self.normalized_points_xy) != len(AIHUB68_KEYPOINT_NAMES):
                raise VisualFeedbackContractError(
                    "P6 mapped landmark count differs"
                )
            for index, point in enumerate(self.normalized_points_xy):
                _require_finite_point(
                    point, f"normalized_points_xy[{index}]"
                )
        if self.shape_change_rate_per_second is not None:
            value = self.shape_change_rate_per_second
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value < 0.0
                or not has_geometry
            ):
                raise VisualFeedbackContractError(
                    "P6 shape change rate is invalid"
                )

    def _digest_json_object(self) -> dict[str, object]:
        return {
            "timestamp_ms": self.timestamp_ms,
            "face_count": self.face_count,
            "normalized_points_xy": (
                None
                if self.normalized_points_xy is None
                else [list(point) for point in self.normalized_points_xy]
            ),
            "visible_shape": (
                None
                if self.visible_shape is None
                else self.visible_shape.to_json_object()
            ),
            "shape_change_rate_per_second": self.shape_change_rate_per_second,
        }


@dataclass(frozen=True, slots=True)
class P6LipTrajectory:
    """Same-attempt shadow trajectory bound to one frozen P6 mapping."""

    attempt_id_sha256: str
    binding_receipt_sha256: str
    video_source_sha256: str
    source_landmark_evidence_sha256: str
    geometry_artifact_sha256: str
    mapping_sha256: str
    frames: tuple[P6MappedLipFrame, ...]
    revision: str = P6_LIP_TRAJECTORY_REVISION

    def __post_init__(self) -> None:
        for name in (
            "attempt_id_sha256",
            "binding_receipt_sha256",
            "video_source_sha256",
            "source_landmark_evidence_sha256",
            "geometry_artifact_sha256",
            "mapping_sha256",
        ):
            require_sha256(getattr(self, name), name)
        if self.revision != P6_LIP_TRAJECTORY_REVISION:
            raise VisualFeedbackContractError(
                "P6 lip trajectory revision differs"
            )
        if not self.frames:
            raise VisualFeedbackContractError(
                "P6 lip trajectory must contain frames"
            )
        timestamps = tuple(frame.timestamp_ms for frame in self.frames)
        if any(
            current <= previous
            for previous, current in zip(timestamps, timestamps[1:])
        ):
            raise VisualFeedbackContractError(
                "P6 lip trajectory timestamps must strictly increase"
            )

    @property
    def single_face_frame_count(self) -> int:
        return sum(frame.face_count == 1 for frame in self.frames)

    @property
    def single_face_coverage(self) -> float:
        return self.single_face_frame_count / len(self.frames)

    @property
    def trajectory_values_sha256(self) -> str:
        return sha256_document(
            [frame._digest_json_object() for frame in self.frames]
        )

    def unsigned_receipt_json_object(self) -> dict[str, object]:
        """Return aggregate lineage without landmark or per-frame arrays."""

        return {
            "schema_version": P6_LIP_TRAJECTORY_RECEIPT_SCHEMA_VERSION,
            "revision": self.revision,
            "attempt_id_sha256": self.attempt_id_sha256,
            "binding_receipt_sha256": self.binding_receipt_sha256,
            "video_source_sha256": self.video_source_sha256,
            "source_landmark_evidence_sha256": (
                self.source_landmark_evidence_sha256
            ),
            "geometry_artifact_sha256": self.geometry_artifact_sha256,
            "mapping_sha256": self.mapping_sha256,
            "mapped_landmark_count": len(AIHUB68_KEYPOINT_NAMES),
            "visible_feature_ids": list(P6_VISIBLE_LIP_FEATURE_IDS),
            "temporal_feature_ids": ["shape_change_rate_per_second"],
            "frame_count": len(self.frames),
            "single_face_frame_count": self.single_face_frame_count,
            "single_face_coverage": self.single_face_coverage,
            "first_timestamp_ms": self.frames[0].timestamp_ms,
            "last_timestamp_ms": self.frames[-1].timestamp_ms,
            "trajectory_values_sha256": self.trajectory_values_sha256,
            "privacy_contract": (
                "aggregate_only_no_paths_ids_frames_or_landmark_arrays"
            ),
            "contains_pronunciation_truth": False,
            "contains_action_truth": False,
        }

    def receipt_json_object(self) -> dict[str, object]:
        unsigned = self.unsigned_receipt_json_object()
        return {**unsigned, "receipt_sha256": sha256_document(unsigned)}


__all__ = [
    "P6LipCandidateFrame",
    "P6LipTrajectory",
    "P6MappedLipFrame",
    "P6VisibleLipShape",
    "P6_LIP_TRAJECTORY_RECEIPT_SCHEMA_VERSION",
    "P6_LIP_TRAJECTORY_REVISION",
    "P6_VISIBLE_LIP_FEATURE_IDS",
]
