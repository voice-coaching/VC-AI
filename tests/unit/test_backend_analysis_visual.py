from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from voice_coach.backend_analysis.adapters import visual as visual_module
from voice_coach.backend_analysis.adapters.visual import (
    BackendVisualSettings,
    ProductionBackendVisualAnalysisAdapter,
)
from voice_coach.backend_analysis.domain import (
    AnalysisOutcome,
    AnalysisRequest,
    AnalysisVisualInput,
    PronunciationEvidence,
)
from voice_coach.backend_analysis.ports import (
    MaterializedAudio,
    MaterializedVideo,
    SeungunAnalysis,
)
from voice_coach.pronunciation_attempts.services.video_attempt_facade import (
    VideoAttemptResult,
    VideoAttemptStatus,
    VisualStageStatus,
    WorkspaceCleanupStatus,
)

from test_backend_analysis_s3_object_store import _request


def test_projects_only_approved_same_attempt_action(monkeypatch, tmp_path: Path) -> None:
    landmark = tmp_path / "face_landmarker.task"
    landmark.write_bytes(b"pinned-test-model")
    geometry = tmp_path / "geometry.json"
    geometry.write_bytes(b"closed-beta-geometry")
    geometry_artifact = object()
    release = SimpleNamespace(
        status="enabled",
        video_qc_decision_policy=object(),
        av_sync_decision_policy=object(),
        runtime_bundle=object(),
    )
    monkeypatch.setattr(
        visual_module,
        "load_production_visual_release",
        lambda path, **_kwargs: release,
    )
    monkeypatch.setattr(
        visual_module,
        "load_p6_geometry_artifact",
        lambda path, **_kwargs: geometry_artifact,
    )
    captured: dict[str, object] = {}

    class _Facade:
        def run(self, command: object) -> VideoAttemptResult:
            captured["command"] = command
            return VideoAttemptResult(
                status=VideoAttemptStatus.AUDIO_COMPLETED_VISUAL_SUPPLEMENTED,
                reason_codes=(),
                audio_result={"status": "coaching_ready"},
                visual_supplement={
                    "status": "visual_feedback_ready",
                    "evidence_relation": "supports_upstream",
                    "notices": ["closed_beta_lip_trajectory_observed"],
                    "cue_evidence": [
                        {
                            "cue_id": "inner_aperture_ratio",
                            "cue_state": "observed",
                            "video_start_ms": 100,
                            "video_end_ms": 200,
                            "measurement_value": 0.25,
                            "measurement_unit": "ratio",
                            "measurement_backend": {
                                "backend_id": "closed_beta_p6_lip_trajectory"
                            },
                            "calibration_artifact_sha256": "2" * 64,
                        }
                    ],
                    "supplemental_action_candidate": {
                        "approved_claim_id": "lip.aperture.low",
                        "renderer_key": "lip_aperture_hint",
                        "upstream_phone_anchor_ref": "f" * 64,
                    },
                },
                visual_stage_status=VisualStageStatus.COMPLETED,
                cleanup_status=WorkspaceCleanupStatus.COMPLETED,
            )

    def _build(**kwargs: object) -> _Facade:
        captured["build"] = kwargs
        return _Facade()

    monkeypatch.setattr(visual_module, "build_local_video_attempt_facade", _build)
    request = _visual_request()
    evidence = PronunciationEvidence(
        selected_phone="ㄱ",
        selected_expected_index=0,
        selected_start_ms=100,
        selected_end_ms=200,
        detector_score=0.91,
        operating_threshold=0.8,
        score_semantics="detector_ranking_score_not_calibrated_correctness_confidence",
        evidence_state="frozen_detector_threshold_passed",
    )
    analysis = SeungunAnalysis(
        outcome=AnalysisOutcome.COACHING_READY,
        summary_feedback="근거 기반 피드백",
        pronunciation_evidence=evidence,
        pipeline_revision="pipeline-v1",
        analysis_evidence={"schema_version": "korean_phone_ctc.pronunciation_analysis.v1"},
        expected_phones=("ㄱ", "ㅏ"),
        analysis_pcm_sha256="a" * 64,
    )
    adapter = ProductionBackendVisualAnalysisAdapter(
        BackendVisualSettings(
            repository_root=tmp_path,
            workspace_root=tmp_path / "visual-workspace",
            nltk_data_root=tmp_path,
            landmark_model_path=landmark,
            production_release_root=tmp_path / "release",
            closed_beta_geometry_artifact_path=geometry,
        )
    )

    supplement = adapter.analyze(
        request=request,
        audio=MaterializedAudio(tmp_path / "audio.wav", 4, "a" * 64),
        video=MaterializedVideo(tmp_path / "video.mp4", 12, "d" * 64),
        seungun=analysis,
    )

    assert supplement is not None
    assert supplement.selected_expected_index == evidence.selected_expected_index
    assert supplement.approved_claim_id == "lip.aperture.low"
    assert supplement.renderer_key == "lip_aperture_hint"
    assert supplement.evidence_relation == "supports_upstream"
    assert supplement.closed_beta_lip_observation == {
        "schemaVersion": "voice-coaching.closed-beta-lip-observation.v1",
        "status": "OBSERVED",
        "selectedExpectedIndex": 0,
        "videoStartMs": 100,
        "videoEndMs": 200,
        "geometryArtifactSha256": "2" * 64,
        "measurements": {
            "inner_aperture_ratio": {"value": 0.25, "unit": "ratio"}
        },
        "containsPronunciationTruth": False,
        "containsActionTruth": False,
    }
    assert captured["build"]["seungun_detector"] is None  # type: ignore[index]
    assert captured["build"]["seungun_runtime_port"] is not None  # type: ignore[index]
    assert (
        captured["build"]["closed_beta_lip_geometry_artifact"]  # type: ignore[index]
        is geometry_artifact
    )


def test_visual_consent_receipt_uses_the_handoff_timestamp_canonical_form() -> None:
    authority = visual_module._GrantConsentAuthority(_visual_request())

    authorization = authority.authorize(
        prompt_id="analysis-35",
        consent_reference="backend_signed_visual_grant_v1",
    )
    receipt = authority.receipt_for(authorization)

    assert receipt.issued_at_utc == "2026-09-02T00:00:00.000000Z"
    assert receipt.expires_at_utc == "2026-09-02T00:05:00.000000Z"


def test_cross_attempt_visual_digest_never_reaches_the_facade(
    monkeypatch, tmp_path: Path
) -> None:
    landmark = tmp_path / "face_landmarker.task"
    landmark.write_bytes(b"pinned-test-model")
    monkeypatch.setattr(
        visual_module,
        "load_production_visual_release",
        lambda path, **_kwargs: SimpleNamespace(status="enabled"),
    )
    monkeypatch.setattr(
        visual_module,
        "build_local_video_attempt_facade",
        lambda **kwargs: pytest.fail("cross-attempt visual reached facade"),
    )
    request = _visual_request()
    analysis = SeungunAnalysis(
        outcome=AnalysisOutcome.COACHING_READY,
        summary_feedback="근거 기반 피드백",
        pronunciation_evidence=PronunciationEvidence(
            selected_phone="ㄱ",
            selected_expected_index=0,
            selected_start_ms=100,
            selected_end_ms=200,
            detector_score=0.91,
            operating_threshold=0.8,
            score_semantics="detector_ranking_score_not_calibrated_correctness_confidence",
            evidence_state="frozen_detector_threshold_passed",
        ),
        pipeline_revision="pipeline-v1",
    )
    adapter = ProductionBackendVisualAnalysisAdapter(
        BackendVisualSettings(
            repository_root=tmp_path,
            workspace_root=tmp_path / "visual-workspace",
            nltk_data_root=tmp_path,
            landmark_model_path=landmark,
            production_release_root=tmp_path / "release",
        )
    )

    supplement = adapter.analyze(
        request=request,
        audio=MaterializedAudio(tmp_path / "audio.wav", 4, "a" * 64),
        video=MaterializedVideo(tmp_path / "video.mp4", 12, "c" * 64),
        seungun=analysis,
    )

    assert supplement is None


def test_rejects_symlinked_visual_workspace(monkeypatch, tmp_path: Path) -> None:
    landmark = tmp_path / "face_landmarker.task"
    landmark.write_bytes(b"pinned-test-model")
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    workspace = tmp_path / "visual-workspace"
    os.symlink(target, workspace)
    monkeypatch.setattr(
        visual_module,
        "load_production_visual_release",
        lambda path, **_kwargs: SimpleNamespace(status="enabled"),
    )

    with pytest.raises(ValueError, match="backend_visual_configuration_invalid"):
        ProductionBackendVisualAnalysisAdapter(
            BackendVisualSettings(
                repository_root=tmp_path,
                workspace_root=workspace,
                nltk_data_root=tmp_path,
                landmark_model_path=landmark,
                production_release_root=tmp_path / "release",
            )
        )


def _visual_request() -> AnalysisRequest:
    base = _request()
    visual = AnalysisVisualInput(
        object_key="recordings/analysis-video/00000000-0000-0000-0000-000000000001.mp4",
        sha256="d" * 64,
        mime_type="video/mp4",
        file_size_bytes=12,
        consent_receipt_sha256="e" * 64,
        consent_policy_revision="voice-video-processing-consent-v1",
    )
    grant = replace(
        base.authorization_grant,
        visual_object_key_sha256="1" * 64,
        visual_sha256=visual.sha256,
        visual_mime_type=visual.mime_type,
        visual_file_size_bytes=visual.file_size_bytes,
        visual_consent_receipt_sha256=visual.consent_receipt_sha256,
        visual_consent_policy_revision=visual.consent_policy_revision,
    )
    return replace(base, visual_input=visual, authorization_grant=grant)
