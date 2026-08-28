"""Visual feedback remains a fail-closed supplement to immutable Seungun evidence."""

from __future__ import annotations

from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from voice_coach.visual_feedback.adapters.artifacts import (
    JsonVisualCalibrationAdapter,
)
from voice_coach.visual_feedback.errors import (
    VisualArtifactUnavailableError,
    VisualFeedbackContractError,
)
from voice_coach.visual_feedback.models.binding import (
    ConsentReceipt,
    MediaTimebaseReceipt,
    SameAttemptBindingReceipt,
)
from voice_coach.visual_feedback.models.enums import (
    ConsentDecision,
    ExecutionScope,
    VisualCueState,
    VisualEvidenceRelation,
    VisualFeedbackStatus,
)
from voice_coach.visual_feedback.models.evidence import SyncAnchor
from voice_coach.visual_feedback.models.integrity import sha256_document
from voice_coach.visual_feedback.models.supplement import VisualFeedbackSupplement
from voice_coach.visual_feedback.testing import (
    INTERNAL_CAPTURE_CONTRACT_ID,
    build_internal_contract_fixture,
)

ROOT = Path(__file__).resolve().parents[2]


def test_frozen_seungun_port_preserves_korean_phone_and_score_semantics() -> None:
    fixture = build_internal_contract_fixture(repository_root=ROOT)

    snapshot = fixture.evidence_adapter.resolve(
        analysis_ref=fixture.request.analysis_ref,
        binding_receipt=fixture.request.binding_receipt,
        video_handle=fixture.request.video_handle,
    )

    assert snapshot.analysis_sha256 == fixture.request.analysis_ref.analysis_sha256
    assert snapshot.phone_anchors[0].expected_phone == "ㅏ"
    assert snapshot.phone_anchors[0].observed_candidate == "ㅓ"
    assert snapshot.phone_anchors[0].error_score == 0.91
    assert snapshot.phone_anchors[0].is_error is True
    assert not hasattr(snapshot, "analysis")


def test_same_attempt_mismatch_stops_before_processing_and_still_deletes() -> None:
    fixture = build_internal_contract_fixture(repository_root=ROOT)
    original = fixture.request.binding_receipt
    mismatch = SameAttemptBindingReceipt.issue(
        attempt_id_sha256=original.attempt_id_sha256,
        prompt_id_sha256=original.prompt_id_sha256,
        prompt_revision=original.prompt_revision,
        expected_phone_sha256=original.expected_phone_sha256,
        analysis_sha256=original.analysis_sha256,
        audio_source_sha256=original.audio_source_sha256,
        audio_derivation_receipt_sha256=(
            original.audio_derivation_receipt_sha256
        ),
        video_source_sha256=sha256_document({"fixture": "wrong-video"}),
        audio_timebase=original.audio_timebase,
        video_timebase=original.video_timebase,
        upstream_reliability_receipt_sha256=(
            original.upstream_reliability_receipt_sha256
        ),
        consent_receipt_sha256=original.consent_receipt_sha256,
        issued_at_utc=original.issued_at_utc,
    )

    result = fixture.coordinator.create_visual_feedback(
        replace(fixture.request, binding_receipt=mismatch)
    )

    assert result.status is VisualFeedbackStatus.DEPENDENCY_UNAVAILABLE
    assert result.supplemental_action_candidate is None
    assert fixture.video_store.access_count == 0
    assert result.media_deletion_receipt.status == "deleted"


def test_denied_consent_prevents_qc_tracking_sync_and_artifact_access() -> None:
    fixture = build_internal_contract_fixture(
        repository_root=ROOT,
        consent_decision=ConsentDecision.DENIED,
    )

    result = fixture.coordinator.create_visual_feedback(fixture.request)

    assert result.status is VisualFeedbackStatus.VISUAL_FAILED_CLOSED
    assert result.reason_codes == ("visual_consent_denied",)
    assert fixture.video_store.access_count == 0
    assert fixture.quality_adapter.observation_count == 0
    assert fixture.tracking_adapter.tracking_count == 0
    assert fixture.sync_adapter.measurement_count == 0
    assert result.media_deletion_receipt.status == "deleted"


def test_binding_issued_outside_consent_interval_fails_before_video_access() -> None:
    fixture = build_internal_contract_fixture(
        repository_root=ROOT,
        consent_issued_at_utc="2026-08-26T00:00:02Z",
        binding_issued_at_utc="2026-08-26T00:00:01Z",
    )

    result = fixture.coordinator.create_visual_feedback(fixture.request)

    assert result.status is VisualFeedbackStatus.VISUAL_FAILED_CLOSED
    assert result.reason_codes == ("binding_outside_consent_interval",)
    assert fixture.video_store.access_count == 0


def test_precomputed_tracker_and_sync_adapters_measure_only_injected_records() -> None:
    fixture = build_internal_contract_fixture(repository_root=ROOT)

    sync = fixture.sync_adapter.measure(
        video_handle=fixture.request.video_handle,
        audio_timebase=fixture.request.binding_receipt.audio_timebase,
        video_timebase=fixture.request.binding_receipt.video_timebase,
    )
    tracking = fixture.tracking_adapter.track(
        video_handle=fixture.request.video_handle,
        video_start_ms=200,
        video_end_ms=400,
    )

    assert sync.state is VisualCueState.OBSERVED
    assert sync.offset_ms == pytest.approx(0.0)
    assert sync.drift_parts_per_million == pytest.approx(0.0)
    aperture = next(cue for cue in tracking.cues if cue.cue_id == "lip_aperture_ratio")
    assert aperture.state is VisualCueState.OBSERVED
    assert aperture.value == pytest.approx(0.3)


def test_sync_adapter_rejects_a_different_audio_clock() -> None:
    fixture = build_internal_contract_fixture(repository_root=ROOT)
    original = fixture.request.binding_receipt.audio_timebase
    wrong_clock = MediaTimebaseReceipt(
        clock_id_sha256=sha256_document({"fixture": "wrong-audio-clock"}),
        duration_ms=original.duration_ms,
        origin_offset_ms=original.origin_offset_ms,
        transform_revision=original.transform_revision,
    )

    with pytest.raises(VisualFeedbackContractError):
        fixture.sync_adapter.measure(
            video_handle=fixture.request.video_handle,
            audio_timebase=wrong_clock,
            video_timebase=fixture.request.binding_receipt.video_timebase,
        )


@pytest.mark.parametrize(
    ("fixture_kwargs", "expected_status"),
    [
        ({"blur_score": 0.1}, VisualFeedbackStatus.VISUAL_RERECORD_REQUIRED),
        (
            {
                "sync_anchors": (
                    SyncAnchor(audio_ms=0, video_ms=100),
                    SyncAnchor(audio_ms=500, video_ms=500),
                    SyncAnchor(audio_ms=1_000, video_ms=900),
                )
            },
            VisualFeedbackStatus.VISUAL_UNCERTAIN,
        ),
    ],
)
def test_quality_and_sync_failures_never_publish_an_action(
    fixture_kwargs: dict[str, object],
    expected_status: VisualFeedbackStatus,
) -> None:
    fixture = build_internal_contract_fixture(
        repository_root=ROOT,
        **fixture_kwargs,
    )

    result = fixture.coordinator.create_visual_feedback(fixture.request)

    assert result.status is expected_status
    assert result.supplemental_action_candidate is None
    assert result.media_deletion_receipt.status == "deleted"


def test_synthetic_artifacts_are_ready_only_in_internal_contract_scope() -> None:
    internal = build_internal_contract_fixture(
        repository_root=ROOT,
        execution_scope=ExecutionScope.INTERNAL_CONTRACT_TEST,
    )
    user_runtime = build_internal_contract_fixture(
        repository_root=ROOT,
        execution_scope=ExecutionScope.USER_RUNTIME,
    )

    internal_result = internal.coordinator.create_visual_feedback(internal.request)
    user_result = user_runtime.coordinator.create_visual_feedback(user_runtime.request)

    assert internal_result.status is VisualFeedbackStatus.VISUAL_FEEDBACK_READY
    assert internal_result.evidence_relation is VisualEvidenceRelation.SUPPORTS_UPSTREAM
    assert internal_result.supplemental_action_candidate is not None
    assert user_result.status is VisualFeedbackStatus.VISUAL_UNCERTAIN
    assert user_result.reason_codes == ("synthetic_artifact_not_user_eligible",)
    assert user_result.supplemental_action_candidate is None


def test_visual_deviation_conflicting_with_upstream_is_not_averaged_away() -> None:
    fixture = build_internal_contract_fixture(
        repository_root=ROOT,
        upstream_is_error=False,
    )

    result = fixture.coordinator.create_visual_feedback(fixture.request)

    assert result.status is VisualFeedbackStatus.VISUAL_UNCERTAIN
    assert result.evidence_relation is VisualEvidenceRelation.CONFLICTS_WITH_UPSTREAM
    assert result.supplemental_action_candidate is None


def test_supplement_serialization_is_strict_canonical_and_path_free() -> None:
    fixture = build_internal_contract_fixture(repository_root=ROOT)
    result = fixture.coordinator.create_visual_feedback(fixture.request)

    payload = result.to_json()
    restored = VisualFeedbackSupplement.from_json(payload)

    assert restored == result
    assert payload == restored.to_json()
    assert "/home/" not in payload
    assert "video_path" not in payload
    document = json.loads(payload)
    document["unexpected"] = True
    with pytest.raises(VisualFeedbackContractError):
        VisualFeedbackSupplement.from_json(json.dumps(document))
    document.pop("unexpected")
    document["status"] = "invented_state"
    with pytest.raises(VisualFeedbackContractError):
        VisualFeedbackSupplement.from_json(json.dumps(document))


def test_receipt_tampering_is_rejected_by_self_digest() -> None:
    fixture = build_internal_contract_fixture(repository_root=ROOT)
    consent = fixture.request.consent_receipt.to_json_object()
    consent["decision"] = "denied"
    binding = fixture.request.binding_receipt.to_json_object()
    binding["prompt_revision"] = "tampered_revision"

    with pytest.raises(VisualFeedbackContractError):
        ConsentReceipt.from_json_object(consent)
    with pytest.raises(VisualFeedbackContractError):
        SameAttemptBindingReceipt.from_json_object(binding)


def test_calibration_loader_rejects_tampered_document_without_path_leak(
    tmp_path: Path,
) -> None:
    source = (
        ROOT / "configs/visual_feedback/internal_visual_calibration_v1.json"
    )
    document = json.loads(source.read_text(encoding="utf-8"))
    document["rules"][0]["reference_center"] = 0.2
    tampered = tmp_path / "sensitive-calibration-location.json"
    tampered.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    adapter = JsonVisualCalibrationAdapter(
        {INTERNAL_CAPTURE_CONTRACT_ID: tampered}
    )

    with pytest.raises(VisualArtifactUnavailableError) as caught:
        adapter.load(capture_contract_id=INTERNAL_CAPTURE_CONTRACT_ID)

    assert str(tampered) not in str(caught.value)


def test_deletion_failure_overrides_ready_and_removes_candidate(monkeypatch) -> None:
    fixture = build_internal_contract_fixture(repository_root=ROOT)

    def fail_delete(*, video_handle) -> None:  # noqa: ANN001
        del video_handle
        raise RuntimeError("internal deletion fixture failure")

    monkeypatch.setattr(fixture.video_store, "delete", fail_delete)

    result = fixture.coordinator.create_visual_feedback(fixture.request)

    assert result.status is VisualFeedbackStatus.VISUAL_FAILED_CLOSED
    assert "media_deletion_failed" in result.reason_codes
    assert result.supplemental_action_candidate is None
    assert result.media_deletion_receipt.status == "failed"


def test_internal_smoke_script_reports_only_contract_scope() -> None:
    spec = importlib.util.spec_from_file_location(
        "visual_contract_smoke",
        ROOT / "scripts/run_visual_feedback_contract_smoke.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    report = module.run_contract_smoke(repository_root=ROOT)

    assert report["status"] == "passed"
    assert report["scope"] == "synthetic_internal_contract_only"
    assert report["real_video_decoded"] is False
    assert report["landmark_model_invoked"] is False
    assert report["seungun_detector_invoked"] is False
    assert report["user_runtime_ready_claim_verified"] is False
