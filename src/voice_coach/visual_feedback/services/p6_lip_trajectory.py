"""Build a 24-point shadow trajectory from a frozen P6 mapping.

This service measures only visible two-dimensional shape.  It does not select a
phone, diagnose pronunciation, or recommend an action.  Those decisions remain
behind the Seungun and human/promotion boundaries.
"""

from __future__ import annotations

import math

from ..models.binding import SameAttemptBindingReceipt
from ..models.p6_geometry import P6GeometryArtifact
from ..models.p6_geometry_schema import MEDIAPIPE_LIP_CANDIDATE_INDICES
from ..models.p6_lip_trajectory import (
    P6LipCandidateFrame,
    P6LipTrajectory,
    P6MappedLipFrame,
    P6VisibleLipShape,
)


class P6LipTrajectoryError(RuntimeError):
    """Path-free reason for rejecting one shadow trajectory."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def build_p6_lip_trajectory(
    *,
    binding_receipt: SameAttemptBindingReceipt,
    video_source_sha256: str,
    source_landmark_evidence_sha256: str,
    geometry_artifact: P6GeometryArtifact,
    candidate_indices: tuple[int, ...],
    candidate_frames: tuple[P6LipCandidateFrame, ...],
) -> P6LipTrajectory:
    """Map one same-attempt candidate sequence without emitting new truth."""

    if video_source_sha256 != binding_receipt.video_source_sha256:
        raise P6LipTrajectoryError("p6_trajectory_video_binding_mismatch")
    if candidate_indices != MEDIAPIPE_LIP_CANDIDATE_INDICES:
        raise P6LipTrajectoryError("p6_trajectory_candidate_schema_mismatch")
    if not candidate_frames:
        raise P6LipTrajectoryError("p6_trajectory_frames_missing")
    timestamps = tuple(frame.timestamp_ms for frame in candidate_frames)
    if any(
        current <= previous
        for previous, current in zip(timestamps, timestamps[1:])
    ):
        raise P6LipTrajectoryError("p6_trajectory_timestamps_invalid")

    candidate_offsets = {
        landmark_index: offset
        for offset, landmark_index in enumerate(candidate_indices)
    }
    try:
        mapping_offsets = tuple(
            candidate_offsets[index]
            for index in geometry_artifact.mapping_mediapipe_indices
        )
    except KeyError:
        raise P6LipTrajectoryError(
            "p6_trajectory_mapping_schema_mismatch"
        ) from None

    mapped_frames: list[P6MappedLipFrame] = []
    previous_points: tuple[tuple[float, float], ...] | None = None
    previous_timestamp_ms: int | None = None
    for frame in candidate_frames:
        if frame.candidate_points_xy is None:
            mapped_frames.append(
                P6MappedLipFrame(
                    timestamp_ms=frame.timestamp_ms,
                    face_count=frame.face_count,
                    normalized_points_xy=None,
                    visible_shape=None,
                    shape_change_rate_per_second=None,
                )
            )
            previous_points = None
            previous_timestamp_ms = None
            continue

        mapped_points = tuple(
            frame.candidate_points_xy[offset] for offset in mapping_offsets
        )
        normalized_points, visible_shape = _normalize_and_measure(mapped_points)
        change_rate = None
        if previous_points is not None and previous_timestamp_ms is not None:
            elapsed_seconds = (frame.timestamp_ms - previous_timestamp_ms) / 1000.0
            change_rate = _root_mean_square_displacement(
                previous_points, normalized_points
            ) / elapsed_seconds
        mapped_frames.append(
            P6MappedLipFrame(
                timestamp_ms=frame.timestamp_ms,
                face_count=frame.face_count,
                normalized_points_xy=normalized_points,
                visible_shape=visible_shape,
                shape_change_rate_per_second=change_rate,
                rate_context_timestamp_ms=previous_timestamp_ms if change_rate is not None else None,
            )
        )
        previous_points = normalized_points
        previous_timestamp_ms = frame.timestamp_ms

    return P6LipTrajectory(
        attempt_id_sha256=binding_receipt.attempt_id_sha256,
        binding_receipt_sha256=binding_receipt.receipt_sha256,
        video_source_sha256=video_source_sha256,
        source_landmark_evidence_sha256=source_landmark_evidence_sha256,
        geometry_artifact_sha256=geometry_artifact.artifact_sha256,
        mapping_sha256=geometry_artifact.mapping_sha256,
        frames=tuple(mapped_frames),
    )


def _normalize_and_measure(
    points: tuple[tuple[float, float], ...],
) -> tuple[tuple[tuple[float, float], ...], P6VisibleLipShape]:
    left_corner = points[0]
    right_corner = points[6]
    mouth_width = _distance(left_corner, right_corner)
    if mouth_width <= 1e-6:
        raise P6LipTrajectoryError("p6_trajectory_mouth_width_degenerate")
    center_x = (left_corner[0] + right_corner[0]) / 2.0
    center_y = (left_corner[1] + right_corner[1]) / 2.0
    normalized = tuple(
        (
            (point[0] - center_x) / mouth_width,
            (point[1] - center_y) / mouth_width,
        )
        for point in points
    )
    visible_shape = P6VisibleLipShape(
        mouth_width_fraction=mouth_width,
        outer_aperture_ratio=_distance(points[3], points[9]) / mouth_width,
        inner_aperture_ratio=_distance(points[15], points[21]) / mouth_width,
        outer_area_ratio=_polygon_area(points[:12]) / (mouth_width * mouth_width),
        inner_area_ratio=_polygon_area(points[12:]) / (mouth_width * mouth_width),
        corner_height_asymmetry_ratio=(
            abs(left_corner[1] - right_corner[1]) / mouth_width
        ),
    )
    return normalized, visible_shape


def _distance(
    first: tuple[float, float], second: tuple[float, float]
) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _polygon_area(points: tuple[tuple[float, float], ...]) -> float:
    doubled_area = sum(
        first[0] * second[1] - second[0] * first[1]
        for first, second in zip(points, points[1:] + points[:1])
    )
    return abs(doubled_area) / 2.0


def _root_mean_square_displacement(
    previous: tuple[tuple[float, float], ...],
    current: tuple[tuple[float, float], ...],
) -> float:
    squared_distance_sum = sum(
        (current_point[0] - previous_point[0]) ** 2
        + (current_point[1] - previous_point[1]) ** 2
        for previous_point, current_point in zip(previous, current, strict=True)
    )
    return math.sqrt(squared_distance_sum / len(current))


__all__ = ["P6LipTrajectoryError", "build_p6_lip_trajectory"]
