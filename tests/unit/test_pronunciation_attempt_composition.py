"""Composition-policy tests for the internal video pronunciation attempt."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voice_coach.pronunciation_attempts.adapters.calibrated_runtime_decisions import (
    CalibratedVideoQcDecisionPolicy,
)
from voice_coach.pronunciation_attempts.composition import (
    build_local_video_attempt_facade,
)
from voice_coach.visual_feedback.models.enums import UpstreamReliabilityStatus
from voice_coach.visual_feedback.models.p6_geometry import P6GeometryArtifact
from voice_coach.visual_feedback.adapters.production_visual_release import (
    load_production_visual_release,
)


class _UnusedDetector:
    """Build-only detector double; composition must not invoke it."""

    def analyze(self, *, audio_path: str | Path, expected_phones: object) -> object:
        del audio_path, expected_phones
        raise AssertionError("composition unexpectedly invoked the detector")


def test_current_frozen_seungun_is_supported_only_as_upstream_anchor(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    facade = build_local_video_attempt_facade(
        repository_root=repository_root,
        restricted_media_root=tmp_path / "media",
        restricted_audio_root=tmp_path / "audio",
        nltk_data_root=repository_root / ".nltk_data",
        seungun_detector=_UnusedDetector(),
    )

    handoff = facade._handoff_port  # noqa: SLF001 - composition wiring assertion
    receipt = handoff._reliability_receipt  # noqa: SLF001

    assert receipt.status is UpstreamReliabilityStatus.SUPPORTED
    assert receipt.policy_revision == (
        "seungun_final_pronunciation_detector_v1_locked_test_v1"
    )
    assert receipt.reason_codes == ()
    assert facade._reliability_port.__class__.__name__ == (  # noqa: SLF001
        "BlockedVisualReliabilityAdapter"
    )
    assert handoff._capture_contract_id == (  # noqa: SLF001
        "local_allowlisted_video_aac_mp4_v2"
    )
    assert facade._container_policy.maximum_width == 1920  # noqa: SLF001
    assert facade._container_policy.maximum_height == 1920  # noqa: SLF001
    assert facade._container_policy.maximum_rgb_sample_bytes == (  # noqa: SLF001
        64 * 1024 * 1024
    )
    assert facade._phone_window_decoder_port is facade._container_port  # noqa: SLF001
    assert facade._phone_window_detector_port is facade._detector_port  # noqa: SLF001


def test_composition_keeps_fixed_local_smoke_prompt_outside_r00(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    facade = build_local_video_attempt_facade(
        repository_root=repository_root,
        restricted_media_root=tmp_path / "media",
        restricted_audio_root=tmp_path / "audio",
        nltk_data_root=repository_root / ".venv-paper-g2pk/nltk_data",
        seungun_detector=_UnusedDetector(),
        fixed_local_prompt=(
            "LOCAL-TEST-RICE-CLEAN-V1",
            "쌀을 깨끗이 씻어요",
        ),
    )

    assert facade._prompt_port.__class__.__name__ == (  # noqa: SLF001
        "FixedLocalTrustedPromptAdapter"
    )


def test_composition_rejects_only_one_calibrated_decision_policy(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    qc_policy = CalibratedVideoQcDecisionPolicy(
        policy_revision="qc_test_v1",
        expected_measurement_policy_sha256="1" * 64,
        minimum_single_face_coverage=0.9,
        maximum_multiple_face_frame_fraction=0.0,
        minimum_mean_luma_8bit=20.0,
        maximum_mean_luma_8bit=230.0,
        maximum_underexposed_pixel_fraction=0.1,
        maximum_overexposed_pixel_fraction=0.1,
        minimum_lip_roi_mean_neighbor_luma_delta_8bit=1.0,
        minimum_track_transition_coverage=0.5,
        minimum_longest_single_face_run_fraction=0.8,
        maximum_lip_center_step_normalized=0.2,
    )

    with pytest.raises(ValueError, match="must be paired"):
        build_local_video_attempt_facade(
            repository_root=repository_root,
            restricted_media_root=tmp_path / "media",
            restricted_audio_root=tmp_path / "audio",
            nltk_data_root=repository_root / ".nltk_data",
            seungun_detector=_UnusedDetector(),
            video_qc_decision_policy=qc_policy,
        )


def test_composition_rejects_runtime_bundle_without_decision_policies(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[2]

    with pytest.raises(ValueError, match="requires calibrated QC"):
        build_local_video_attempt_facade(
            repository_root=repository_root,
            restricted_media_root=tmp_path / "media",
            restricted_audio_root=tmp_path / "audio",
            nltk_data_root=repository_root / ".nltk_data",
            seungun_detector=_UnusedDetector(),
            visual_runtime_bundle=object(),  # type: ignore[arg-type]
        )


def test_composition_wires_closed_beta_only_on_complete_production_state(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    release = load_production_visual_release(
        repository_root / "artifacts/visual_feedback/releases/production_visual"
    )
    geometry = P6GeometryArtifact.from_json_object(
        json.loads(
            (
                repository_root
                / "artifacts/visual_feedback/p6/aihub68_geometry_artifact_v2.json"
            ).read_text(encoding="utf-8")
        )
    )

    facade = build_local_video_attempt_facade(
        repository_root=repository_root,
        restricted_media_root=tmp_path / "media",
        restricted_audio_root=tmp_path / "audio",
        nltk_data_root=repository_root / ".nltk_data",
        seungun_detector=_UnusedDetector(),
        landmark_model_path=(
            repository_root / "models/mediapipe/face_landmarker.task"
        ),
        video_qc_decision_policy=release.video_qc_decision_policy,
        av_sync_decision_policy=release.av_sync_decision_policy,
        visual_runtime_bundle=release.runtime_bundle,
        closed_beta_lip_geometry_artifact=geometry,
    )

    detector = facade._detector_port._detector  # noqa: SLF001
    assert detector.__class__.__name__ == "ClosedBetaMediaPipeLipLandmarkAdapter"
    assert facade._visual_port.__class__.__name__ == (  # noqa: SLF001
        "ProductionVisualSidecarAdapter"
    )


def test_composition_rejects_closed_beta_without_production_runtime(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    geometry = P6GeometryArtifact.from_json_object(
        json.loads(
            (
                repository_root
                / "artifacts/visual_feedback/p6/aihub68_geometry_artifact_v2.json"
            ).read_text(encoding="utf-8")
        )
    )

    with pytest.raises(ValueError, match="requires production visual runtime"):
        build_local_video_attempt_facade(
            repository_root=repository_root,
            restricted_media_root=tmp_path / "media",
            restricted_audio_root=tmp_path / "audio",
            nltk_data_root=repository_root / ".nltk_data",
            seungun_detector=_UnusedDetector(),
            landmark_model_path=(
                repository_root / "models/mediapipe/face_landmarker.task"
            ),
            closed_beta_lip_geometry_artifact=geometry,
        )
