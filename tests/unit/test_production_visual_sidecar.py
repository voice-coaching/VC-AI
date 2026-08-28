"""Focused contract tests for the inactive production visual sidecar."""

from __future__ import annotations

from dataclasses import dataclass, replace
import importlib.util
from pathlib import Path
import sys

import pytest

from voice_coach.pronunciation_attempts.adapters.calibrated_runtime_decisions import (
    RUNTIME_AV_SYNC_DECISION_SCHEMA_VERSION,
    RUNTIME_VIDEO_QC_DECISION_SCHEMA_VERSION,
    RuntimeAvSyncDecision,
    RuntimeVideoQcDecision,
)
from voice_coach.pronunciation_attempts.adapters.lip_audio_av_sync import (
    MEASUREMENT_REVISION as AV_SYNC_MEASUREMENT_REVISION,
    AvSyncAnchorCandidate,
    AvSyncCandidateEvidence,
)
from voice_coach.pronunciation_attempts.adapters.mediapipe_lip_landmarks import (
    BACKEND_REVISION,
    MediaPipeFaceFrame,
    MediaPipeLandmarkBatch,
    MediaPipeLipGeometry,
)
from voice_coach.pronunciation_attempts.adapters.production_visual_sidecar import (
    ProductionVisualSidecarAdapter,
)
from voice_coach.pronunciation_attempts.adapters.runtime import (
    RuntimeVisualDetectionEvidence,
)
from voice_coach.pronunciation_attempts.adapters.video_qc import (
    MEASUREMENT_REVISION as VIDEO_QC_MEASUREMENT_REVISION,
    VideoQcCandidateEvidence,
    VideoQcMetrics,
)
from voice_coach.pronunciation_attempts.models import DecodedRgbFrame, Rgb24Sample
from voice_coach.pronunciation_attempts.phone_window import (
    DensePhoneWindowEvidence,
    PhoneWindowDecodeReceipt,
    PhoneWindowDecodeRequest,
    PhoneWindowTrackingReceipt,
    PolicyValidatedAvSyncOffset,
    Rgb24PhoneWindow,
    RuntimePhoneWindowReceipt,
    decoded_rgb_sha256,
)
from voice_coach.pronunciation_attempts.services.video_attempt_facade import (
    HostVisualHandoff,
    VisualDetection,
    VisualReliabilityDecision,
)
from voice_coach.visual_feedback.errors import VisualFeedbackContractError
from voice_coach.visual_feedback.models.binding import (
    ConsentReceipt,
    MediaTimebaseReceipt,
    RestrictedVideoHandle,
    UpstreamReliabilityReceipt,
)
from voice_coach.visual_feedback.models.enums import (
    ConsentDecision,
    UpstreamReliabilityStatus,
    VisualFeedbackStatus,
)
from voice_coach.visual_feedback.models.evidence import (
    SEUNGUN_ANALYSIS_SCHEMA_VERSION,
    SEUNGUN_SCORE_SEMANTICS,
)
from voice_coach.visual_feedback.models.integrity import sha256_document
from voice_coach.visual_feedback.models.runtime_eligibility import (
    AutomatedVisualRuntimeEligibilityReceipt,
)
from voice_coach.visual_feedback.models.supplement import VisualFeedbackSupplement
from voice_coach.visual_feedback.services.handoff import (
    SeungunAttemptBindingFacade,
    SeungunVisualHandoff,
)


def _load_runtime_contract_helpers():
    """Load the heavyweight contract fixture without requiring a tests package."""

    fixture_path = Path(__file__).with_name("test_visual_runtime_eligibility.py")
    specification = importlib.util.spec_from_file_location(
        "visual_runtime_eligibility_sidecar_fixture",
        fixture_path,
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    try:
        specification.loader.exec_module(module)
    finally:
        sys.modules.pop(specification.name, None)
    return module._phone_window_policy, module._runtime_bundle


_phone_window_policy, _runtime_bundle = _load_runtime_contract_helpers()


@dataclass(frozen=True, slots=True)
class _Attempt:
    adapter: ProductionVisualSidecarAdapter
    handoff: HostVisualHandoff
    detection: VisualDetection


def test_constructor_rejects_eligibility_for_a_different_bundle() -> None:
    bundle = _runtime_bundle(revision="sidecar_bundle_one")
    other = _runtime_bundle(revision="sidecar_bundle_two")

    with pytest.raises(
        VisualFeedbackContractError,
        match="does not resolve to the supplied bundle",
    ):
        ProductionVisualSidecarAdapter(
            runtime_bundle=bundle,
            eligibility_receipt=AutomatedVisualRuntimeEligibilityReceipt.issue(
                other
            ),
        )


def test_selected_phone_anchor_mismatch_is_unsupported() -> None:
    attempt = _attempt(expected_phone="ㅓ", center_aperture=0.9)
    mismatched = replace(attempt.handoff, selected_phone_expected_index=1)

    decision = attempt.adapter.evaluate(
        detection=attempt.detection,
        handoff=mismatched,
    )

    assert decision == VisualReliabilityDecision(
        supported=False,
        receipt=None,
        reason_codes=("visual_reliability_lineage_mismatch",),
    )


def test_counterfeit_candidate_decision_observation_is_unsupported() -> None:
    attempt = _attempt(expected_phone="ㅓ", center_aperture=0.9)
    evidence = attempt.detection.evidence
    assert isinstance(evidence, RuntimeVisualDetectionEvidence)
    original = evidence.video_qc_decision
    assert isinstance(original, RuntimeVideoQcDecision)
    counterfeit = object.__new__(RuntimeVideoQcDecision)
    for field_name in original.__dataclass_fields__:
        object.__setattr__(counterfeit, field_name, getattr(original, field_name))
    object.__setattr__(
        counterfeit,
        "observed_metrics",
        replace(original.observed_metrics, mean_luma_8bit=101.0),
    )
    tampered = VisualDetection(
        evidence=replace(evidence, video_qc_decision=counterfeit)
    )

    decision = attempt.adapter.evaluate(
        detection=tampered,
        handoff=attempt.handoff,
    )

    assert decision.supported is False
    assert decision.reason_codes == ("visual_reliability_lineage_mismatch",)


def test_catalog_unsupported_phone_returns_canonical_no_cue_supplement() -> None:
    attempt = _attempt(expected_phone="ㅇ", center_aperture=0.9)

    reliability = attempt.adapter.evaluate(
        detection=attempt.detection,
        handoff=attempt.handoff,
    )
    document = attempt.adapter.create(
        detection=attempt.detection,
        handoff=attempt.handoff,
        reliability=reliability,
    )
    supplement = VisualFeedbackSupplement.from_json_object(document)

    assert reliability.supported is True
    assert supplement.status is VisualFeedbackStatus.NO_SUPPORTED_LIP_CUE
    assert supplement.reason_codes == (
        "selected_phone_has_no_supported_lip_cue",
    )
    assert supplement.cue_evidence == ()
    assert supplement.supplemental_action_candidate is None
    assert supplement.media_deletion_receipt.status == (
        "not_applicable_no_raw_media"
    )
    assert "host_owns_media_cleanup" in supplement.notices


def test_center_cue_below_trigger_returns_no_action() -> None:
    bundle = _runtime_bundle(revision="sidecar_no_action")
    center = bundle.calibration_artifact.rules[0].reference_center
    attempt = _attempt(
        expected_phone="ㅓ",
        center_aperture=center,
        runtime_bundle=bundle,
    )

    reliability = attempt.adapter.evaluate(
        detection=attempt.detection,
        handoff=attempt.handoff,
    )
    document = attempt.adapter.create(
        detection=attempt.detection,
        handoff=attempt.handoff,
        reliability=reliability,
    )
    supplement = VisualFeedbackSupplement.from_json_object(document)

    assert supplement.status is VisualFeedbackStatus.NO_SUPPORTED_LIP_CUE
    assert supplement.reason_codes == (
        "no_actionable_visible_lip_deviation",
    )
    assert supplement.supplemental_action_candidate is None
    assert supplement.cue_evidence[0].measurement_value == pytest.approx(center)
    assert supplement.cue_evidence[0].calibrated_deviation == pytest.approx(0.0)


def test_ready_uses_only_deterministic_center_frame_and_serializes() -> None:
    attempt = _attempt(
        expected_phone="ㅓ",
        center_aperture=0.9,
        side_aperture=0.05,
    )

    reliability = attempt.adapter.evaluate(
        detection=attempt.detection,
        handoff=attempt.handoff,
    )
    first = attempt.adapter.create(
        detection=attempt.detection,
        handoff=attempt.handoff,
        reliability=reliability,
    )
    second = attempt.adapter.create(
        detection=attempt.detection,
        handoff=attempt.handoff,
        reliability=reliability,
    )
    supplement = VisualFeedbackSupplement.from_json_object(first)

    assert reliability.supported is True
    assert first == second
    assert supplement.status is VisualFeedbackStatus.VISUAL_FEEDBACK_READY
    assert supplement.cue_evidence[0].measurement_value == pytest.approx(0.9)
    assert supplement.supplemental_action_candidate is not None
    assert supplement.supplemental_action_candidate.action_id == (
        "reduce_visible_lip_opening"
    )
    assert supplement.media_deletion_receipt.status == (
        "not_applicable_no_raw_media"
    )
    serialized = supplement.to_json().lower()
    for forbidden in (
        "airflow",
        "diagnosis",
        "glottis",
        "medical",
        "tongue",
        "velum",
        "혀",
        "성문",
    ):
        assert forbidden not in serialized
    assert "free_text" not in serialized
    assert VisualFeedbackSupplement.from_json(supplement.to_json()) == supplement


def test_receipt_from_another_attempt_serializes_failed_closed() -> None:
    first = _attempt(expected_phone="ㅓ", center_aperture=0.9, tag="first")
    second = _attempt(expected_phone="ㅓ", center_aperture=0.9, tag="second")
    wrong = second.adapter.evaluate(
        detection=second.detection,
        handoff=second.handoff,
    )

    document = first.adapter.create(
        detection=first.detection,
        handoff=first.handoff,
        reliability=wrong,
    )
    supplement = VisualFeedbackSupplement.from_json_object(document)

    assert supplement.status is VisualFeedbackStatus.VISUAL_FAILED_CLOSED
    assert supplement.reason_codes == ("visual_supplement_lineage_mismatch",)
    assert supplement.supplemental_action_candidate is None
    assert supplement.media_deletion_receipt.status == (
        "not_applicable_no_raw_media"
    )
    assert VisualFeedbackSupplement.from_json(supplement.to_json()) == supplement


def _attempt(
    *,
    expected_phone: str,
    center_aperture: float,
    side_aperture: float | None = None,
    runtime_bundle=None,
    tag: str = "default",
) -> _Attempt:
    bundle = runtime_bundle or _runtime_bundle(revision=f"sidecar_{tag}")
    eligibility = AutomatedVisualRuntimeEligibilityReceipt.issue(bundle)
    adapter = ProductionVisualSidecarAdapter(
        runtime_bundle=bundle,
        eligibility_receipt=eligibility,
    )
    handoff = _handoff(
        bundle=bundle,
        expected_phone=expected_phone,
        tag=tag,
    )
    detection = _detection(
        bundle=bundle,
        handoff=handoff,
        center_aperture=center_aperture,
        side_aperture=(
            center_aperture if side_aperture is None else side_aperture
        ),
        tag=tag,
    )
    return _Attempt(adapter=adapter, handoff=handoff, detection=detection)


def _handoff(*, bundle, expected_phone: str, tag: str) -> HostVisualHandoff:
    attempt_id = _sha(f"attempt-{tag}")
    audio_timebase = MediaTimebaseReceipt(
        clock_id_sha256=_sha(f"audio-clock-{tag}"),
        duration_ms=1_000,
        origin_offset_ms=0,
        transform_revision="sidecar_test_clock_v1",
    )
    video_timebase = MediaTimebaseReceipt(
        clock_id_sha256=_sha(f"video-clock-{tag}"),
        duration_ms=1_000,
        origin_offset_ms=0,
        transform_revision="sidecar_test_clock_v1",
    )
    consent = ConsentReceipt.issue(
        attempt_id_sha256=attempt_id,
        decision=ConsentDecision.GRANTED,
        retention_policy_id="delete_on_completion_v1",
        issued_at_utc="2026-08-28T00:00:00Z",
        expires_at_utc="2026-08-29T00:00:00Z",
    )
    reliability = UpstreamReliabilityReceipt.issue(
        status=UpstreamReliabilityStatus.SUPPORTED,
        policy_revision="sidecar_seungun_reliability_v1",
    )
    video_handle = RestrictedVideoHandle(
        handle_id=_sha(f"video-handle-{tag}"),
        attempt_id_sha256=attempt_id,
        source_sha256=_sha(f"video-source-{tag}"),
        timebase_sha256=video_timebase.timebase_sha256,
    )
    payload = SeungunAttemptBindingFacade().bind_completed_analysis(
        analysis=_analysis(expected_phone=expected_phone, tag=tag),
        attempt_id_sha256=attempt_id,
        prompt_id_sha256=_sha(f"prompt-{tag}"),
        prompt_revision="sidecar_prompt_v1",
        expected_phones=(expected_phone,),
        audio_source_sha256=_sha(f"audio-source-{tag}"),
        audio_timebase=audio_timebase,
        video_handle=video_handle,
        video_timebase=video_timebase,
        reliability_receipt=reliability,
        consent_receipt=consent,
        issued_at_utc="2026-08-28T00:00:01Z",
        upstream_phone_expected_index=0,
        capture_contract_id=bundle.pipeline_identity.media_capture_contract_id,
        population_id_sha256=bundle.pipeline_identity.population_id_sha256,
        # Runtime entrypoint context and calibration measurement context are
        # deliberately separate.  The artifact remains scoped to phone_center.
        context_id="internal_video_cli",
        audio_derivation_receipt_sha256=_sha(f"audio-derivation-{tag}"),
    )
    return HostVisualHandoff(
        selected_phone_expected_index=0,
        binding_receipt=payload.request.binding_receipt,
        payload=payload,
    )


def _analysis(*, expected_phone: str, tag: str) -> dict[str, object]:
    observed = "ㅏ" if expected_phone != "ㅏ" else "ㅓ"
    return {
        "schema_version": SEUNGUN_ANALYSIS_SCHEMA_VERSION,
        "detector_version": "sidecar_contract_detector_v1",
        "detector_config_sha256": _sha(f"detector-config-{tag}"),
        "backbone_name": "sidecar_contract_backbone",
        "backbone_tree_sha256": _sha(f"backbone-{tag}"),
        "expected_phones": [expected_phone],
        "ctc_hypothesis": [observed],
        "insertions": [],
        "phones": [
            {
                "expected_index": 0,
                "expected_phone": expected_phone,
                "observed_candidate": observed,
                "alignment_operation": "substitution",
                "start_ms": 200,
                "end_ms": 400,
                "error_score": 0.9,
                "operating_threshold": 0.8,
                "is_error": True,
                "score_semantics": SEUNGUN_SCORE_SEMANTICS,
                "soft_features": {},
            }
        ],
        "notices": ["internal contract fixture"],
    }


def _detection(
    *,
    bundle,
    handoff: HostVisualHandoff,
    center_aperture: float,
    side_aperture: float,
    tag: str,
) -> VisualDetection:
    payload = handoff.payload
    assert isinstance(payload, SeungunVisualHandoff)
    binding = payload.request.binding_receipt
    identity = bundle.pipeline_identity
    global_landmarks = _landmarks(
        asset_handle_id=_sha(f"global-asset-{tag}"),
        container_sha256=binding.video_source_sha256,
        model_sha256=identity.landmark_model_sha256,
        timestamps_ms=(0, 200, 400),
        apertures=(0.2, 0.3, 0.4),
    )
    qc_candidate = _qc_candidate(
        landmarks=global_landmarks,
        identity=identity,
    )
    qc_decision = _qc_decision(
        candidate=qc_candidate,
        binding=binding,
        identity=identity,
    )
    sync_candidate = _sync_candidate(
        landmarks=global_landmarks,
        binding=binding,
        identity=identity,
    )
    sync_decision = _sync_decision(
        candidate=sync_candidate,
        binding=binding,
        identity=identity,
    )
    offset = PolicyValidatedAvSyncOffset.issue_from_decision(
        decision=sync_decision
    )
    detection_unsigned = {
        "schema_version": "voice_coach.runtime_visual_detection.v3",
        "binding_receipt_sha256": binding.receipt_sha256,
        "container_sha256": binding.video_source_sha256,
        "sample_timestamps_sha256": _sha(f"global-timestamps-{tag}"),
        "landmark_evidence_sha256": global_landmarks.evidence_sha256,
        "model_sha256": identity.landmark_model_sha256,
        "video_qc_candidate_receipt_sha256": qc_candidate.receipt_sha256,
        "video_qc_receipt_sha256": qc_decision.receipt_sha256,
        "av_sync_candidate_receipt_sha256": sync_candidate.receipt_sha256,
        "av_sync_receipt_sha256": sync_decision.receipt_sha256,
        "av_sync_offset_receipt_sha256": offset.receipt_sha256,
    }
    detection_sha256 = sha256_document(detection_unsigned)
    dense = _dense_phone_window(
        bundle=bundle,
        binding=binding,
        phone_anchor_sha256=_selected_anchor(payload),
        detection_receipt_sha256=detection_sha256,
        qc_receipt_sha256=qc_decision.receipt_sha256,
        sync_receipt_sha256=sync_decision.receipt_sha256,
        offset_receipt_sha256=offset.receipt_sha256,
        center_aperture=center_aperture,
        side_aperture=side_aperture,
        tag=tag,
    )
    evidence = RuntimeVisualDetectionEvidence(
        binding_receipt_sha256=binding.receipt_sha256,
        container_sha256=binding.video_source_sha256,
        sample_timestamps_sha256=detection_unsigned[
            "sample_timestamps_sha256"
        ],
        landmark_evidence_sha256=global_landmarks.evidence_sha256,
        model_sha256=identity.landmark_model_sha256,
        video_qc_candidate_receipt_sha256=qc_candidate.receipt_sha256,
        video_qc_receipt_sha256=qc_decision.receipt_sha256,
        av_sync_candidate_receipt_sha256=sync_candidate.receipt_sha256,
        av_sync_receipt_sha256=sync_decision.receipt_sha256,
        av_sync_offset_receipt_sha256=offset.receipt_sha256,
        detection_receipt_sha256=detection_sha256,
        landmark_evidence=global_landmarks,
        video_qc_evidence=qc_candidate,
        video_qc_decision=qc_decision,
        av_sync_candidate_evidence=sync_candidate,
        av_sync_decision=sync_decision,
        av_sync_evidence=offset,
        phone_window_evidence=dense,
    )
    return VisualDetection(evidence=evidence)


def _qc_candidate(*, landmarks: MediaPipeLandmarkBatch, identity):
    timestamps_us = tuple(frame.timestamp_ms * 1_000 for frame in landmarks.frames)
    metrics = VideoQcMetrics(
        frame_count=3,
        single_face_frame_count=3,
        multiple_face_frame_count=0,
        single_face_coverage=1.0,
        multiple_face_frame_fraction=0.0,
        mean_luma_8bit=100.0,
        underexposed_pixel_fraction=0.0,
        overexposed_pixel_fraction=0.0,
        mean_neighbor_luma_delta_8bit=10.0,
        lip_roi_frame_count=3,
        lip_roi_mean_luma_8bit=100.0,
        lip_roi_underexposed_pixel_fraction=0.0,
        lip_roi_overexposed_pixel_fraction=0.0,
        lip_roi_mean_neighbor_luma_delta_8bit=10.0,
        track_transition_coverage=1.0,
        longest_single_face_run_fraction=1.0,
        maximum_lip_center_step_normalized=0.01,
    )
    assert identity.video_qc_measurement_revision == VIDEO_QC_MEASUREMENT_REVISION
    provenance = {
        "schema_version": "voice_coach.video_qc_provenance.v2",
        "asset_handle_id": landmarks.asset_handle_id,
        "container_sha256": landmarks.container_sha256,
        "video_stream_index": 0,
        "sample_timestamps_us": list(timestamps_us),
        "landmark_timestamps_ms": [
            frame.timestamp_ms for frame in landmarks.frames
        ],
        "decoded_rgb_evidence_sha256": _sha("global-rgb"),
        "landmark_evidence_sha256": landmarks.evidence_sha256,
        "landmark_model_sha256": landmarks.model_sha256,
        "policy_receipt_sha256": identity.video_qc_measurement_policy_sha256,
        "measurement_revision": identity.video_qc_measurement_revision,
        "measurement_adapter_code_sha256": (
            identity.video_qc_measurement_code_sha256
        ),
    }
    unsigned = {
        "schema_version": "voice_coach.video_qc_candidate.v2",
        "status": "candidate_unvalidated",
        **{key: value for key, value in provenance.items() if key != "schema_version"},
        "metrics": metrics.to_json_object(),
        "reason_codes": [],
        "provenance_receipt_sha256": sha256_document(provenance),
    }
    return VideoQcCandidateEvidence(
        status="candidate_unvalidated",
        asset_handle_id=landmarks.asset_handle_id,
        container_sha256=landmarks.container_sha256,
        video_stream_index=0,
        sample_timestamps_us=timestamps_us,
        landmark_timestamps_ms=tuple(
            frame.timestamp_ms for frame in landmarks.frames
        ),
        decoded_rgb_evidence_sha256=provenance[
            "decoded_rgb_evidence_sha256"
        ],
        landmark_evidence_sha256=landmarks.evidence_sha256,
        landmark_model_sha256=landmarks.model_sha256,
        policy_receipt_sha256=identity.video_qc_measurement_policy_sha256,
        measurement_revision=identity.video_qc_measurement_revision,
        measurement_adapter_code_sha256=(
            identity.video_qc_measurement_code_sha256
        ),
        metrics=metrics,
        reason_codes=(),
        provenance_receipt_sha256=unsigned["provenance_receipt_sha256"],
        receipt_sha256=sha256_document(unsigned),
    )


def _qc_decision(*, candidate, binding, identity):
    unsigned = {
        "schema_version": RUNTIME_VIDEO_QC_DECISION_SCHEMA_VERSION,
        "attempt_id_sha256": binding.attempt_id_sha256,
        "binding_receipt_sha256": binding.receipt_sha256,
        "container_sha256": candidate.container_sha256,
        "landmark_evidence_sha256": candidate.landmark_evidence_sha256,
        "landmark_model_sha256": candidate.landmark_model_sha256,
        "source_candidate_receipt_sha256": candidate.receipt_sha256,
        "source_candidate_provenance_receipt_sha256": (
            candidate.provenance_receipt_sha256
        ),
        "measurement_revision": candidate.measurement_revision,
        "measurement_policy_sha256": candidate.policy_receipt_sha256,
        "measurement_adapter_code_sha256": (
            candidate.measurement_adapter_code_sha256
        ),
        "observed_metrics": candidate.metrics.to_json_object(),
        "decision_policy_revision": identity.video_qc_decision_policy_revision,
        "decision_policy_sha256": identity.video_qc_decision_policy_sha256,
        "decision_adapter_code_sha256": identity.video_qc_decision_code_sha256,
        "status": "passed",
        "reason_codes": [],
    }
    return RuntimeVideoQcDecision(
        attempt_id_sha256=binding.attempt_id_sha256,
        binding_receipt_sha256=binding.receipt_sha256,
        container_sha256=candidate.container_sha256,
        landmark_evidence_sha256=candidate.landmark_evidence_sha256,
        landmark_model_sha256=candidate.landmark_model_sha256,
        source_candidate_receipt_sha256=candidate.receipt_sha256,
        source_candidate_provenance_receipt_sha256=(
            candidate.provenance_receipt_sha256
        ),
        measurement_revision=candidate.measurement_revision,
        measurement_policy_sha256=candidate.policy_receipt_sha256,
        measurement_adapter_code_sha256=(
            candidate.measurement_adapter_code_sha256
        ),
        observed_metrics=candidate.metrics,
        decision_policy_revision=identity.video_qc_decision_policy_revision,
        decision_policy_sha256=identity.video_qc_decision_policy_sha256,
        decision_adapter_code_sha256=identity.video_qc_decision_code_sha256,
        status="passed",
        reason_codes=(),
        receipt_sha256=sha256_document(unsigned),
    )


def _sync_candidate(*, landmarks, binding, identity):
    assert identity.av_sync_measurement_revision == AV_SYNC_MEASUREMENT_REVISION
    anchors = (
        AvSyncAnchorCandidate(audio_ms=200, video_ms=200, strength=1.0),
        AvSyncAnchorCandidate(audio_ms=300, video_ms=300, strength=0.9),
        AvSyncAnchorCandidate(audio_ms=400, video_ms=400, strength=0.8),
    )
    lineage = {
        "schema_version": "voice_coach.lip_audio_av_sync_lineage.v2",
        "asset_handle_id": landmarks.asset_handle_id,
        "audio_derivation_receipt_sha256": (
            binding.audio_derivation_receipt_sha256
        ),
        "audio_source_sha256": binding.audio_source_sha256,
        "video_source_sha256": binding.video_source_sha256,
        "audio_stream_index": 0,
        "audio_start_time_us": 0,
        "video_stream_index": 0,
        "video_start_time_us": 0,
        "landmark_evidence_sha256": landmarks.evidence_sha256,
        "landmark_model_sha256": landmarks.model_sha256,
        "measurement_policy_sha256": identity.av_sync_measurement_policy_sha256,
        "measurement_revision": identity.av_sync_measurement_revision,
        "measurement_adapter_code_sha256": identity.av_sync_measurement_code_sha256,
    }
    unsigned = {
        "schema_version": "voice_coach.lip_audio_av_sync_candidate.v2",
        **{key: value for key, value in lineage.items() if key != "schema_version"},
        "status": "candidate_unvalidated",
        "candidate_offset_ms": 0,
        "correlation": 0.95,
        "anchors": [anchor.to_json_object() for anchor in anchors],
        "reason_codes": [],
        "lineage_receipt_sha256": sha256_document(lineage),
    }
    return AvSyncCandidateEvidence(
        asset_handle_id=landmarks.asset_handle_id,
        audio_derivation_receipt_sha256=binding.audio_derivation_receipt_sha256,
        audio_source_sha256=binding.audio_source_sha256,
        video_source_sha256=binding.video_source_sha256,
        audio_stream_index=0,
        audio_start_time_us=0,
        video_stream_index=0,
        video_start_time_us=0,
        landmark_evidence_sha256=landmarks.evidence_sha256,
        landmark_model_sha256=landmarks.model_sha256,
        measurement_policy_sha256=identity.av_sync_measurement_policy_sha256,
        measurement_revision=identity.av_sync_measurement_revision,
        measurement_adapter_code_sha256=identity.av_sync_measurement_code_sha256,
        status="candidate_unvalidated",
        candidate_offset_ms=0,
        correlation=0.95,
        anchors=anchors,
        reason_codes=(),
        lineage_receipt_sha256=unsigned["lineage_receipt_sha256"],
        receipt_sha256=sha256_document(unsigned),
    )


def _sync_decision(*, candidate, binding, identity):
    unsigned = {
        "schema_version": RUNTIME_AV_SYNC_DECISION_SCHEMA_VERSION,
        "attempt_id_sha256": binding.attempt_id_sha256,
        "binding_receipt_sha256": binding.receipt_sha256,
        "audio_source_sha256": candidate.audio_source_sha256,
        "video_source_sha256": candidate.video_source_sha256,
        "landmark_evidence_sha256": candidate.landmark_evidence_sha256,
        "landmark_model_sha256": candidate.landmark_model_sha256,
        "source_candidate_receipt_sha256": candidate.receipt_sha256,
        "source_candidate_lineage_receipt_sha256": (
            candidate.lineage_receipt_sha256
        ),
        "measurement_revision": candidate.measurement_revision,
        "measurement_policy_sha256": candidate.measurement_policy_sha256,
        "measurement_adapter_code_sha256": (
            candidate.measurement_adapter_code_sha256
        ),
        "observed_offset_ms": candidate.candidate_offset_ms,
        "observed_correlation": candidate.correlation,
        "observed_anchors": [
            anchor.to_json_object() for anchor in candidate.anchors
        ],
        "decision_policy_revision": identity.av_sync_decision_policy_revision,
        "decision_policy_sha256": identity.av_sync_decision_policy_sha256,
        "decision_adapter_code_sha256": identity.av_sync_decision_code_sha256,
        "status": "synchronized",
        "reason_codes": [],
    }
    return RuntimeAvSyncDecision(
        attempt_id_sha256=binding.attempt_id_sha256,
        binding_receipt_sha256=binding.receipt_sha256,
        audio_source_sha256=candidate.audio_source_sha256,
        video_source_sha256=candidate.video_source_sha256,
        landmark_evidence_sha256=candidate.landmark_evidence_sha256,
        landmark_model_sha256=candidate.landmark_model_sha256,
        source_candidate_receipt_sha256=candidate.receipt_sha256,
        source_candidate_lineage_receipt_sha256=(
            candidate.lineage_receipt_sha256
        ),
        measurement_revision=candidate.measurement_revision,
        measurement_policy_sha256=candidate.measurement_policy_sha256,
        measurement_adapter_code_sha256=(
            candidate.measurement_adapter_code_sha256
        ),
        observed_offset_ms=candidate.candidate_offset_ms,
        observed_correlation=candidate.correlation,
        observed_anchors=candidate.anchors,
        decision_policy_revision=identity.av_sync_decision_policy_revision,
        decision_policy_sha256=identity.av_sync_decision_policy_sha256,
        decision_adapter_code_sha256=identity.av_sync_decision_code_sha256,
        status="synchronized",
        reason_codes=(),
        receipt_sha256=sha256_document(unsigned),
    )


def _dense_phone_window(
    *,
    bundle,
    binding,
    phone_anchor_sha256: str,
    detection_receipt_sha256: str,
    qc_receipt_sha256: str,
    sync_receipt_sha256: str,
    offset_receipt_sha256: str,
    center_aperture: float,
    side_aperture: float,
    tag: str,
) -> DensePhoneWindowEvidence:
    policy = _phone_window_policy()
    assert policy.policy_sha256 == bundle.pipeline_identity.phone_window_policy_sha256
    request = PhoneWindowDecodeRequest.issue(
        attempt_id_sha256=binding.attempt_id_sha256,
        binding_receipt_sha256=binding.receipt_sha256,
        container_sha256=binding.video_source_sha256,
        detection_receipt_sha256=detection_receipt_sha256,
        phone_anchor_sha256=phone_anchor_sha256,
        av_sync_receipt_sha256=sync_receipt_sha256,
        av_sync_offset_receipt_sha256=offset_receipt_sha256,
        policy=policy,
        video_start_ms=100,
        video_end_ms=500,
    )
    frames = tuple(
        DecodedRgbFrame(
            timestamp_us=timestamp_ms * 1_000,
            width=1,
            height=1,
            rgb24=bytes((index, index, index)),
        )
        for index, timestamp_ms in enumerate((200, 300, 400), start=1)
    )
    sample = Rgb24Sample(
        asset_handle_id=_sha(f"dense-asset-{tag}"),
        container_sha256=binding.video_source_sha256,
        video_stream_index=0,
        frames=frames,
        ffmpeg_revision="sidecar_ffmpeg_test_v1",
    )
    decode = PhoneWindowDecodeReceipt.issue(
        request=request,
        video_stream_index=0,
        selected_frame_indices=(0, 1, 2),
        decoded_timestamps_us=tuple(frame.timestamp_us for frame in frames),
        width=1,
        height=1,
        decoded_rgb_sha256=decoded_rgb_sha256(sample),
        ffmpeg_revision=sample.ffmpeg_revision,
        decoder_code_sha256=bundle.pipeline_identity.decoder_sha256,
    )
    window = Rgb24PhoneWindow(
        request=request,
        sample=sample,
        decode_receipt=decode,
    )
    landmarks = _landmarks(
        asset_handle_id=sample.asset_handle_id,
        container_sha256=binding.video_source_sha256,
        model_sha256=bundle.pipeline_identity.landmark_model_sha256,
        timestamps_ms=(200, 300, 400),
        apertures=(side_aperture, center_aperture, side_aperture),
    )
    tracking = PhoneWindowTrackingReceipt.issue(
        window=window,
        landmark_evidence_sha256=landmarks.evidence_sha256,
        model_sha256=landmarks.model_sha256,
        tracking_code_sha256=bundle.pipeline_identity.cue_extractor_sha256,
    )
    runtime = RuntimePhoneWindowReceipt.issue(
        request=request,
        video_qc_receipt_sha256=qc_receipt_sha256,
        tracking_receipt=tracking,
        policy=policy,
    )
    return DensePhoneWindowEvidence(
        decode_receipt=decode,
        tracking_receipt=tracking,
        runtime_receipt=runtime,
        landmark_evidence=landmarks,
    )


def _landmarks(
    *,
    asset_handle_id: str,
    container_sha256: str,
    model_sha256: str,
    timestamps_ms: tuple[int, ...],
    apertures: tuple[float, ...],
) -> MediaPipeLandmarkBatch:
    width = 0.4
    return MediaPipeLandmarkBatch(
        asset_handle_id=asset_handle_id,
        container_sha256=container_sha256,
        backend_revision=BACKEND_REVISION,
        model_sha256=model_sha256,
        network_isolation="seccomp_socket_denied",
        frames=tuple(
            MediaPipeFaceFrame(
                timestamp_ms=timestamp_ms,
                face_count=1,
                lip_geometry=MediaPipeLipGeometry(
                    upper_lip_y=0.3,
                    lower_lip_y=0.3 + width * aperture,
                    left_corner_x=0.3,
                    right_corner_x=0.7,
                ),
            )
            for timestamp_ms, aperture in zip(
                timestamps_ms,
                apertures,
                strict=True,
            )
        ),
    )


def _selected_anchor(payload: SeungunVisualHandoff) -> str:
    from voice_coach.visual_feedback.adapters.seungun import parse_seungun_analysis

    anchors = parse_seungun_analysis(
        payload.source_record.analysis,
        analysis_ref=payload.source_record.reference,
    )
    return anchors[payload.request.upstream_phone_expected_index].anchor_ref


def _sha(label: str) -> str:
    return sha256_document({"production_visual_sidecar_fixture": label})
