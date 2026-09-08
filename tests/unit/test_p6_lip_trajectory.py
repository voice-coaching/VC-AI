"""Shadow trajectory contracts do not promote unreviewed visual truth."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voice_coach.visual_feedback.errors import VisualFeedbackContractError
from voice_coach.visual_feedback.models.binding import (
    MediaTimebaseReceipt,
    SameAttemptBindingReceipt,
)
from voice_coach.visual_feedback.models.p6_geometry import P6GeometryArtifact
from voice_coach.visual_feedback.models.p6_geometry_schema import (
    MEDIAPIPE_LIP_CANDIDATE_INDICES,
)
from voice_coach.visual_feedback.models.p6_lip_trajectory import (
    P6LipCandidateFrame,
)
from voice_coach.visual_feedback.services.p6_lip_trajectory import (
    P6LipTrajectoryError,
    build_p6_lip_trajectory,
)


ROOT = Path(__file__).resolve().parents[2]


def _geometry_artifact() -> P6GeometryArtifact:
    document = json.loads(
        (
            ROOT
            / "artifacts/visual_feedback/p6/aihub68_geometry_artifact_v2.json"
        ).read_text(encoding="utf-8")
    )
    return P6GeometryArtifact.from_json_object(document)


def _binding_receipt() -> SameAttemptBindingReceipt:
    timebase = MediaTimebaseReceipt(
        clock_id_sha256="a" * 64,
        duration_ms=1_000,
        origin_offset_ms=0,
        transform_revision="identity_v1",
    )
    return SameAttemptBindingReceipt.issue(
        attempt_id_sha256="1" * 64,
        prompt_id_sha256="2" * 64,
        prompt_revision="prompt_v1",
        expected_phone_sha256="3" * 64,
        analysis_sha256="4" * 64,
        audio_source_sha256="5" * 64,
        audio_derivation_receipt_sha256="6" * 64,
        video_source_sha256="7" * 64,
        audio_timebase=timebase,
        video_timebase=timebase,
        upstream_reliability_receipt_sha256="8" * 64,
        consent_receipt_sha256="9" * 64,
        issued_at_utc="2026-09-04T00:00:00Z",
    )


def _candidate_points(
    *, offset_x: float = 0.0, aperture_delta: float = 0.0
) -> tuple[tuple[float, float], ...]:
    points_by_index = {
        index: (0.35 + offset * 0.002 + offset_x, 0.50)
        for offset, index in enumerate(MEDIAPIPE_LIP_CANDIDATE_INDICES)
    }
    artifact = _geometry_artifact()
    mapped = artifact.mapping_mediapipe_indices
    points_by_index[mapped[0]] = (0.25 + offset_x, 0.50)
    points_by_index[mapped[6]] = (0.75 + offset_x, 0.50)
    points_by_index[mapped[3]] = (0.50 + offset_x, 0.42 - aperture_delta)
    points_by_index[mapped[9]] = (0.50 + offset_x, 0.58 + aperture_delta)
    points_by_index[mapped[15]] = (0.50 + offset_x, 0.47 - aperture_delta)
    points_by_index[mapped[21]] = (0.50 + offset_x, 0.53 + aperture_delta)
    return tuple(
        points_by_index[index] for index in MEDIAPIPE_LIP_CANDIDATE_INDICES
    )


def test_builds_24_point_time_trajectory_without_creating_truth() -> None:
    artifact = _geometry_artifact()
    binding = _binding_receipt()
    trajectory = build_p6_lip_trajectory(
        binding_receipt=binding,
        video_source_sha256=binding.video_source_sha256,
        source_landmark_evidence_sha256="b" * 64,
        geometry_artifact=artifact,
        candidate_indices=MEDIAPIPE_LIP_CANDIDATE_INDICES,
        candidate_frames=(
            P6LipCandidateFrame(
                timestamp_ms=100,
                face_count=1,
                candidate_points_xy=_candidate_points(),
            ),
            P6LipCandidateFrame(
                timestamp_ms=140,
                face_count=1,
                candidate_points_xy=_candidate_points(aperture_delta=0.02),
            ),
        ),
    )

    assert len(trajectory.frames) == 2
    assert len(trajectory.frames[0].normalized_points_xy or ()) == 24
    assert trajectory.frames[0].normalized_points_xy[0] == pytest.approx(
        (-0.5, 0.0)
    )
    assert trajectory.frames[0].normalized_points_xy[6] == pytest.approx(
        (0.5, 0.0)
    )
    assert trajectory.frames[0].visible_shape.inner_aperture_ratio == pytest.approx(
        0.12
    )
    assert trajectory.frames[1].shape_change_rate_per_second > 0.0

    receipt = trajectory.receipt_json_object()
    assert receipt["mapped_landmark_count"] == 24
    assert len(receipt["visible_feature_ids"]) == 6
    assert receipt["contains_pronunciation_truth"] is False
    assert receipt["contains_action_truth"] is False
    assert "frames" not in receipt
    assert "normalized_points_xy" not in receipt


def test_translation_does_not_change_normalized_shape() -> None:
    artifact = _geometry_artifact()
    binding = _binding_receipt()
    trajectory = build_p6_lip_trajectory(
        binding_receipt=binding,
        video_source_sha256=binding.video_source_sha256,
        source_landmark_evidence_sha256="c" * 64,
        geometry_artifact=artifact,
        candidate_indices=MEDIAPIPE_LIP_CANDIDATE_INDICES,
        candidate_frames=(
            P6LipCandidateFrame(0, 1, _candidate_points()),
            P6LipCandidateFrame(33, 1, _candidate_points(offset_x=0.05)),
        ),
    )

    first_points = trajectory.frames[0].normalized_points_xy
    second_points = trajectory.frames[1].normalized_points_xy
    assert first_points is not None
    assert second_points is not None
    for first, second in zip(first_points, second_points, strict=True):
        assert first == pytest.approx(second)
    assert trajectory.frames[1].shape_change_rate_per_second == pytest.approx(0.0)


def test_detection_gap_resets_temporal_delta_and_is_reported() -> None:
    artifact = _geometry_artifact()
    binding = _binding_receipt()
    trajectory = build_p6_lip_trajectory(
        binding_receipt=binding,
        video_source_sha256=binding.video_source_sha256,
        source_landmark_evidence_sha256="d" * 64,
        geometry_artifact=artifact,
        candidate_indices=MEDIAPIPE_LIP_CANDIDATE_INDICES,
        candidate_frames=(
            P6LipCandidateFrame(0, 1, _candidate_points()),
            P6LipCandidateFrame(33, 0, None),
            P6LipCandidateFrame(66, 1, _candidate_points(aperture_delta=0.02)),
        ),
    )

    assert trajectory.single_face_coverage == pytest.approx(2 / 3)
    assert trajectory.frames[2].shape_change_rate_per_second is None


def test_rejects_candidate_order_that_differs_from_calibrated_schema() -> None:
    with pytest.raises(P6LipTrajectoryError) as caught:
        build_p6_lip_trajectory(
            binding_receipt=_binding_receipt(),
            video_source_sha256="7" * 64,
            source_landmark_evidence_sha256="e" * 64,
            geometry_artifact=_geometry_artifact(),
            candidate_indices=tuple(reversed(MEDIAPIPE_LIP_CANDIDATE_INDICES)),
            candidate_frames=(
                P6LipCandidateFrame(0, 1, _candidate_points()),
            ),
        )

    assert caught.value.reason_code == "p6_trajectory_candidate_schema_mismatch"


def test_rejects_candidate_frames_from_another_video_attempt() -> None:
    with pytest.raises(P6LipTrajectoryError) as caught:
        build_p6_lip_trajectory(
            binding_receipt=_binding_receipt(),
            video_source_sha256="f" * 64,
            source_landmark_evidence_sha256="e" * 64,
            geometry_artifact=_geometry_artifact(),
            candidate_indices=MEDIAPIPE_LIP_CANDIDATE_INDICES,
            candidate_frames=(
                P6LipCandidateFrame(0, 1, _candidate_points()),
            ),
        )

    assert caught.value.reason_code == "p6_trajectory_video_binding_mismatch"


def test_current_geometry_artifact_rejects_fabricated_truth_flags() -> None:
    path = ROOT / "artifacts/visual_feedback/p6/aihub68_geometry_artifact_v2.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["contains_pronunciation_truth"] = True
    document["contains_action_truth"] = True

    with pytest.raises(VisualFeedbackContractError, match="privacy/truth boundary"):
        P6GeometryArtifact.from_json_object(document)
