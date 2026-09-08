"""Frozen handling of malformed-but-bounded AI Hub 538 bbox observations."""

from __future__ import annotations

from .integrity import sha256_document


AIHUB538_BOUNDED_REVERSED_BBOX_OBSERVATION_POLICY = {
    "schema_version": (
        "voice_coach.aihub538_bounded_reversed_bbox_observation_policy.v1"
    ),
    "coordinate_interpretation": "creator_row_column_order_v1",
    "numeric_coordinates_must_be_finite": True,
    "coordinates_must_be_within_declared_frame_bounds": True,
    "usable_bbox_endpoints_must_be_strictly_increasing": True,
    "bounded_reversed_bbox_classification": "reversed_bbox_unavailable",
    "endpoint_reordering_allowed": False,
    "maximum_bounded_reversed_bbox_unavailable_count": None,
    "capacity_feasibility_uses_bbox_outcomes": False,
    "capacity_feasibility_uses_label_actor_attempt_angle_only": True,
    "paired_ts175_admission_retains_unavailable_counts": True,
    "actual_media_calibration_requires_usable_selected_observations": True,
    "actual_media_unavailable_observation_is_qc_failure": True,
    "ts174_behavior": "strict_reject_non_increasing_bbox",
    "promotion_authorized": False,
}
AIHUB538_BOUNDED_REVERSED_BBOX_OBSERVATION_POLICY_SHA256 = sha256_document(
    AIHUB538_BOUNDED_REVERSED_BBOX_OBSERVATION_POLICY
)


__all__ = [
    "AIHUB538_BOUNDED_REVERSED_BBOX_OBSERVATION_POLICY",
    "AIHUB538_BOUNDED_REVERSED_BBOX_OBSERVATION_POLICY_SHA256",
]
