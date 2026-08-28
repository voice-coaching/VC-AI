"""Automated visual runtime eligibility remains strict and fail-closed."""

from __future__ import annotations

from dataclasses import dataclass, FrozenInstanceError, replace
import json
from pathlib import Path

import pytest

from voice_coach.pronunciation_attempts.models import DecodedRgbFrame, Rgb24Sample
from voice_coach.pronunciation_attempts.adapters.calibrated_runtime_decisions import (
    CalibratedAvSyncDecisionAdapter,
    CalibratedVideoQcDecisionAdapter,
    decision_adapter_code_sha256,
)
from voice_coach.pronunciation_attempts.adapters.lip_audio_av_sync import (
    MEASUREMENT_REVISION as AV_SYNC_MEASUREMENT_REVISION,
    AvSyncAnchorCandidate,
    AvSyncCandidateEvidence,
    av_sync_measurement_code_sha256,
)
from voice_coach.pronunciation_attempts.adapters.runtime import (
    RuntimeVisualDetectionEvidence,
)
from voice_coach.pronunciation_attempts.adapters.video_qc import (
    MEASUREMENT_REVISION as VIDEO_QC_MEASUREMENT_REVISION,
    VideoQcCandidateEvidence,
    VideoQcMetrics,
    video_qc_measurement_code_sha256,
)
from voice_coach.pronunciation_attempts.phone_window import (
    DensePhoneWindowEvidence,
    PhoneWindowDecodePolicy,
    PhoneWindowDecodeReceipt,
    PhoneWindowDecodeRequest,
    PhoneWindowTrackingReceipt,
    PolicyValidatedAvSyncOffset,
    Rgb24PhoneWindow,
    RuntimePhoneWindowReceipt,
    decoded_rgb_sha256,
    phone_window_code_sha256,
)
from voice_coach.visual_feedback.adapters.p8_seal_store import P8SealStateStore
from voice_coach.visual_feedback.errors import VisualFeedbackContractError
from voice_coach.visual_feedback.models.artifacts import (
    CalibrationRule,
    VisualCalibrationArtifact,
    VisualClaim,
    VisualClaimCatalog,
)
from voice_coach.visual_feedback.models.enums import (
    CalibrationScope,
    DeviationDirection,
)
from voice_coach.visual_feedback.models.integrity import sha256_document
from voice_coach.visual_feedback.models.p6_geometry import (
    P6_GEOMETRY_RELOAD_SCHEMA_VERSION,
    P6GeometryArtifact,
    P6GeometryEvidenceBundle,
    P6GeometryMetricSummary,
    P6GeometryReloadReceipt,
    P6GeometrySliceMetric,
    P6GeometryThresholds,
    p6_mapping_parity_probe_sha256,
)
from voice_coach.visual_feedback.models.p6_geometry_schema import (
    AIHUB68_KEYPOINT_NAMES,
    MEDIAPIPE_LIP_CANDIDATE_INDICES,
)
from voice_coach.visual_feedback.models.p6_phone_reference import (
    P6_PHONE_REFERENCE_CAPTURE_CONTRACT_ID,
    P6PhoneReferenceEvidenceBundle,
)
from voice_coach.visual_feedback.models.p8_aggregate_metrics import (
    P8AggregateEvaluationMetrics,
)
from voice_coach.visual_feedback.models.p8_locked_evaluation import (
    P8AccessTransaction,
    P8LockedEvaluationReport,
    P8LockedEvaluationStatus,
    P8ReadZeroSeal,
)
from voice_coach.visual_feedback.models.p8_locked_source_manifest import (
    P8LockedSourceArchiveDigest,
    P8LockedSourceEvaluationResult,
    P8LockedSourceManifest,
)
from voice_coach.visual_feedback.models.p8_promotion import (
    P8ZeroViolationReplayReceipt,
)
from voice_coach.visual_feedback.models.runtime_eligibility import (
    AutomatedP6PromotionReceipt,
    AutomatedP8PromotionReceipt,
    AutomatedVisualRuntimeEligibilityReceipt,
    RuntimeVisualClaimPolicy,
    VisualRuntimeBundle,
    VisualRuntimePipelineIdentity,
    issue_attempt_visual_reliability,
    load_automated_visual_runtime_eligibility,
    load_visual_runtime_bundle,
    validate_attempt_visual_reliability,
)
from voice_coach.visual_feedback.models.runtime_policy_artifact import (
    AvSyncDecisionSemanticPolicy,
    P8RuntimePolicyArtifact,
    RuntimeDecisionPolicyEvidence,
    VideoQcDecisionSemanticPolicy,
)
from voice_coach.visual_feedback.testing import build_internal_contract_fixture
from voice_coach.visual_feedback.services.runtime_eligibility import (
    build_automated_p6_promotion_receipt,
    build_automated_p8_promotion_receipt,
    build_p8_zero_violation_replay_receipt,
)
from voice_coach.visual_feedback.services.p8_locked_evaluation import (
    P8LockedEvaluationService,
)

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_bundle_and_eligibility_round_trip_are_immutable() -> None:
    bundle = _runtime_bundle()
    restored_bundle = load_visual_runtime_bundle(bundle.to_json_object())
    receipt = AutomatedVisualRuntimeEligibilityReceipt.issue(restored_bundle)
    restored_receipt = load_automated_visual_runtime_eligibility(
        receipt.to_json_object(), bundle=restored_bundle
    )

    assert restored_bundle == bundle
    assert restored_receipt == receipt
    assert receipt.status == "eligible"
    assert receipt.p6_bundle_sha256 == bundle.p6_promotion_receipt.p6_bundle_sha256
    assert receipt.p8_bundle_sha256 == bundle.p8_promotion_receipt.p8_bundle_sha256
    with pytest.raises(FrozenInstanceError):
        receipt.status = "blocked"  # type: ignore[misc]


def test_promotion_receipts_have_no_public_boolean_only_issue_path() -> None:
    assert not hasattr(AutomatedP6PromotionReceipt, "issue")
    assert not hasattr(AutomatedP8PromotionReceipt, "issue")
    assert not hasattr(P8ZeroViolationReplayReceipt, "issue")
    assert not hasattr(RuntimeDecisionPolicyEvidence, "issue")


def test_wrong_phone_label_v1_bundle_is_rejected_as_runtime_input() -> None:
    document = json.loads(
        (
            ROOT
            / "artifacts/visual_feedback/p6/aihub68_phone_reference_bundle_v1.json"
        ).read_text(encoding="utf-8")
    )
    assert document["producer"]["source_selector"]["expected_phone"] == "ㅃ"

    with pytest.raises(VisualFeedbackContractError):
        P6PhoneReferenceEvidenceBundle.from_json_object(document)


def test_p6_builder_replays_real_geometry_thresholds_after_reload() -> None:
    runtime_bundle = _runtime_bundle()
    geometry_document = _p6_geometry_bundle(
        runtime_bundle.pipeline_identity
    ).to_json_object()
    artifact_document = geometry_document["artifact"]
    assert isinstance(artifact_document, dict)
    metrics = artifact_document["metrics"]
    assert isinstance(metrics, dict)
    metrics["calibration_coverage_fraction"] = 0.50
    _resign(artifact_document, "artifact_sha256")
    reload_document = geometry_document["reload_receipt"]
    assert isinstance(reload_document, dict)
    reload_document["artifact_sha256"] = artifact_document["artifact_sha256"]
    reload_document["artifact_document_sha256"] = sha256_document(
        artifact_document
    )
    _resign(reload_document, "receipt_sha256")
    _resign(geometry_document, "bundle_sha256")
    geometry_bundle = P6GeometryEvidenceBundle.from_json_object(geometry_document)

    with pytest.raises(VisualFeedbackContractError, match="coverage_below"):
        build_automated_p6_promotion_receipt(
            geometry_bundle=geometry_bundle,
            phone_reference_bundle=_p6_phone_reference_bundle(),
            calibration_artifact=runtime_bundle.calibration_artifact,
            pipeline_identity=runtime_bundle.pipeline_identity,
            **_decision_policy_builder_kwargs(),
        )


def test_p6_permission_scope_is_separate_and_mandatory() -> None:
    runtime_bundle = _runtime_bundle()
    geometry_document = _p6_geometry_bundle(
        runtime_bundle.pipeline_identity
    ).to_json_object()
    artifact_document = geometry_document["artifact"]
    assert isinstance(artifact_document, dict)
    artifact_document["rights_scope"] = "technical_metrics_only"
    _resign(artifact_document, "artifact_sha256")
    reload_document = geometry_document["reload_receipt"]
    assert isinstance(reload_document, dict)
    reload_document["artifact_sha256"] = artifact_document["artifact_sha256"]
    reload_document["artifact_document_sha256"] = sha256_document(
        artifact_document
    )
    _resign(reload_document, "receipt_sha256")
    _resign(geometry_document, "bundle_sha256")

    with pytest.raises(VisualFeedbackContractError, match="rights scope"):
        build_automated_p6_promotion_receipt(
            geometry_bundle=P6GeometryEvidenceBundle.from_json_object(
                geometry_document
            ),
            phone_reference_bundle=_p6_phone_reference_bundle(),
            calibration_artifact=runtime_bundle.calibration_artifact,
            pipeline_identity=runtime_bundle.pipeline_identity,
            **_decision_policy_builder_kwargs(),
        )


def test_p8_builder_uses_freshly_persisted_seal_report_and_metrics(
    tmp_path: Path,
) -> None:
    runtime_bundle = _runtime_bundle()
    seed = _p8_evidence_store(
        p6_receipt=runtime_bundle.p6_promotion_receipt,
        artifact=runtime_bundle.calibration_artifact,
        catalog=runtime_bundle.claim_catalog,
        policy=runtime_bundle.claim_policy,
        identity=runtime_bundle.pipeline_identity,
    )
    assert seed.source_manifest is not None
    assert seed.metrics is not None
    state_directory = tmp_path / "persisted-p8-evidence"
    state_directory.mkdir(mode=0o700)
    store = P8SealStateStore(state_directory)
    service = P8LockedEvaluationService(store)
    service.create_read_zero_seal(seed.seal)
    service.execute_locked_evaluation(
        expected_seal_sha256=seed.seal.seal_sha256,
        transaction_id="p8_runtime_access_001",
        consumed_at_utc="2026-08-28T01:01:00Z",
        report_id="p8_runtime_report_001",
        open_and_evaluate_aggregate=_p8_locked_result,
    )

    replay = build_p8_zero_violation_replay_receipt(
        evidence_store=P8SealStateStore(state_directory),
        p6_promotion_receipt=runtime_bundle.p6_promotion_receipt,
        calibration_artifact=runtime_bundle.calibration_artifact,
        claim_catalog=runtime_bundle.claim_catalog,
        claim_policy=runtime_bundle.claim_policy,
        pipeline_identity=runtime_bundle.pipeline_identity,
    )
    promotion = build_automated_p8_promotion_receipt(
        evidence_store=P8SealStateStore(state_directory),
        zero_violation_replay_receipt=replay,
        p6_promotion_receipt=runtime_bundle.p6_promotion_receipt,
        calibration_artifact=runtime_bundle.calibration_artifact,
        claim_catalog=runtime_bundle.claim_catalog,
        claim_policy=runtime_bundle.claim_policy,
        pipeline_identity=runtime_bundle.pipeline_identity,
    )

    assert promotion == runtime_bundle.p8_promotion_receipt
    assert promotion.locked_evaluation_report_sha256 == seed.report.report_sha256
    assert promotion.aggregate_metrics_sha256 == seed.metrics.metrics_sha256
    assert promotion.zero_violation_replay_receipt_sha256 == replay.receipt_sha256
    assert promotion.locked_manifest_sha256 == seed.source_manifest.manifest_sha256
    assert replay.locked_manifest_sha256 == seed.source_manifest.manifest_sha256
    assert promotion.locked_manifest_sha256 != seed.seal.source_listing_sha256


def test_p8_builder_rejects_persisted_metric_threshold_violation() -> None:
    runtime_bundle = _runtime_bundle()
    seed = _p8_evidence_store(
        p6_receipt=runtime_bundle.p6_promotion_receipt,
        artifact=runtime_bundle.calibration_artifact,
        catalog=runtime_bundle.claim_catalog,
        policy=runtime_bundle.claim_policy,
        identity=runtime_bundle.pipeline_identity,
    )
    assert seed.source_manifest is not None
    violating_metrics = P8AggregateEvaluationMetrics.issue(
        locked_source_manifest_sha256=seed.source_manifest.manifest_sha256,
        admitted_item_count=100,
        paired_item_count=100,
        evaluated_item_count=100,
        evaluation_exclusion_count=0,
        qc_decision_lineage_valid_item_count=100,
        ready_sync_decision_lineage_valid_case_count=80,
        successful_item_count=99,
        failed_item_count=1,
        decode_success_rate=0.99,
        supported_clip_count=90,
        supported_clip_below_single_face_threshold_count=0,
        aggregate_supported_slice_coverage=0.91,
        av_anchor_availability_rate=0.81,
        ready_case_count=80,
        maximum_ready_absolute_sync_offset_ms=119.0,
        claim_policy_violation_count=1,
        forbidden_claim_fixture_violation_count=0,
    )
    report_document = seed.report.to_json_object()
    report_document["aggregate_metrics_sha256"] = violating_metrics.metrics_sha256
    _resign(report_document, "report_sha256")
    violating_store = _InMemoryPersistedP8Evidence(
        seal=seed.seal,
        report=P8LockedEvaluationReport.from_json_object(report_document),
        source_manifest=seed.source_manifest,
        metrics=violating_metrics,
    )

    with pytest.raises(VisualFeedbackContractError, match="frozen thresholds"):
        build_p8_zero_violation_replay_receipt(
            evidence_store=violating_store,
            p6_promotion_receipt=runtime_bundle.p6_promotion_receipt,
            calibration_artifact=runtime_bundle.calibration_artifact,
            claim_catalog=runtime_bundle.claim_catalog,
            claim_policy=runtime_bundle.claim_policy,
            pipeline_identity=runtime_bundle.pipeline_identity,
        )


def test_p8_builder_rejects_pipeline_not_pinned_before_locked_read() -> None:
    runtime_bundle = _runtime_bundle()
    store = _p8_evidence_store(
        p6_receipt=runtime_bundle.p6_promotion_receipt,
        artifact=runtime_bundle.calibration_artifact,
        catalog=runtime_bundle.claim_catalog,
        policy=runtime_bundle.claim_policy,
        identity=runtime_bundle.pipeline_identity,
    )
    identity_document = runtime_bundle.pipeline_identity.to_json_object()
    identity_document["normalizer_sha256"] = _sha("different-normalizer")
    _resign(identity_document, "identity_sha256")
    different_identity = VisualRuntimePipelineIdentity.from_json_object(
        identity_document
    )

    with pytest.raises(VisualFeedbackContractError, match="normalizer_code"):
        build_p8_zero_violation_replay_receipt(
            evidence_store=store,
            p6_promotion_receipt=runtime_bundle.p6_promotion_receipt,
            calibration_artifact=runtime_bundle.calibration_artifact,
            claim_catalog=runtime_bundle.claim_catalog,
            claim_policy=runtime_bundle.claim_policy,
            pipeline_identity=different_identity,
        )


def test_p8_promotion_rejects_replay_receipt_from_different_evidence() -> None:
    runtime_bundle = _runtime_bundle()
    store = _p8_evidence_store(
        p6_receipt=runtime_bundle.p6_promotion_receipt,
        artifact=runtime_bundle.calibration_artifact,
        catalog=runtime_bundle.claim_catalog,
        policy=runtime_bundle.claim_policy,
        identity=runtime_bundle.pipeline_identity,
    )
    replay = build_p8_zero_violation_replay_receipt(
        evidence_store=store,
        p6_promotion_receipt=runtime_bundle.p6_promotion_receipt,
        calibration_artifact=runtime_bundle.calibration_artifact,
        claim_catalog=runtime_bundle.claim_catalog,
        claim_policy=runtime_bundle.claim_policy,
        pipeline_identity=runtime_bundle.pipeline_identity,
    )
    replay_document = replay.to_json_object()
    replay_document["ready_case_count"] = replay.ready_case_count - 1
    _resign(replay_document, "receipt_sha256")
    different_replay = P8ZeroViolationReplayReceipt.from_json_object(
        replay_document
    )

    with pytest.raises(VisualFeedbackContractError, match="persisted evidence"):
        build_automated_p8_promotion_receipt(
            evidence_store=store,
            zero_violation_replay_receipt=different_replay,
            p6_promotion_receipt=runtime_bundle.p6_promotion_receipt,
            calibration_artifact=runtime_bundle.calibration_artifact,
            claim_catalog=runtime_bundle.claim_catalog,
            claim_policy=runtime_bundle.claim_policy,
            pipeline_identity=runtime_bundle.pipeline_identity,
        )


@pytest.mark.parametrize(
    ("receipt_name", "legacy_schema", "legacy_status"),
    [
        (
            "p6_promotion_receipt",
            "voice_coach.p6_evidence_receipt.v1",
            "evidence_validated_not_promoted",
        ),
        (
            "p8_promotion_receipt",
            "voice_coach.p8_conformance_receipt.v1",
            "zero_violations_not_promoted",
        ),
    ],
)
def test_runtime_bundle_rejects_current_non_promotional_v1_receipts(
    receipt_name: str,
    legacy_schema: str,
    legacy_status: str,
) -> None:
    document = _runtime_bundle().to_json_object()
    receipt = document[receipt_name]
    assert isinstance(receipt, dict)
    receipt["schema_version"] = legacy_schema
    receipt["status"] = legacy_status
    _resign(receipt, "receipt_sha256")
    _resign(document, "bundle_sha256")

    with pytest.raises(VisualFeedbackContractError, match="schema differs"):
        load_visual_runtime_bundle(document)


@pytest.mark.parametrize(
    "synthetic_target", ["calibration_artifact", "claim_catalog"]
)
def test_runtime_bundle_rejects_synthetic_artifacts(
    synthetic_target: str,
) -> None:
    document = _runtime_bundle().to_json_object()
    target = document[synthetic_target]
    assert isinstance(target, dict)
    target["scope"] = "synthetic_contract_only"
    digest_field = (
        "artifact_sha256"
        if synthetic_target == "calibration_artifact"
        else "catalog_sha256"
    )
    _resign(target, digest_field)
    _resign(document, "bundle_sha256")

    with pytest.raises(VisualFeedbackContractError, match="calibrated scope"):
        load_visual_runtime_bundle(document)


@pytest.mark.parametrize(
    ("receipt_name", "field", "message"),
    [
        ("p6_promotion_receipt", "calibration_artifact_sha256", "P6 calibration"),
        ("p6_promotion_receipt", "pipeline_identity_sha256", "P6 pipeline"),
        (
            "p6_promotion_receipt",
            "media_capture_contract_sha256",
            "P6 media capture",
        ),
        (
            "p6_promotion_receipt",
            "visual_observation_contract_sha256",
            "P6 visual observation",
        ),
        ("p6_promotion_receipt", "population_id_sha256", "P6 population"),
        ("p6_promotion_receipt", "context_contract_sha256", "P6 context"),
        ("p8_promotion_receipt", "calibration_artifact_sha256", "P8 calibration"),
        ("p8_promotion_receipt", "catalog_sha256", "P8 claim catalog"),
        ("p8_promotion_receipt", "policy_sha256", "P8 claim policy"),
        ("p8_promotion_receipt", "pipeline_identity_sha256", "P8 pipeline"),
        (
            "p8_promotion_receipt",
            "media_capture_contract_sha256",
            "P8 media capture",
        ),
        (
            "p8_promotion_receipt",
            "visual_observation_contract_sha256",
            "P8 visual observation",
        ),
        ("p8_promotion_receipt", "population_id_sha256", "P8 population"),
        ("p8_promotion_receipt", "context_contract_sha256", "P8 context"),
    ],
)
def test_runtime_bundle_rejects_every_promotion_cross_digest_mismatch(
    receipt_name: str,
    field: str,
    message: str,
) -> None:
    document = _runtime_bundle().to_json_object()
    receipt = document[receipt_name]
    assert isinstance(receipt, dict)
    receipt[field] = _sha(f"wrong-{receipt_name}-{field}")
    _resign(receipt, "receipt_sha256")
    if receipt_name == "p6_promotion_receipt":
        p8_receipt = document["p8_promotion_receipt"]
        assert isinstance(p8_receipt, dict)
        p8_receipt["p6_promotion_receipt_sha256"] = receipt["receipt_sha256"]
        _resign(p8_receipt, "receipt_sha256")
    _resign(document, "bundle_sha256")

    with pytest.raises(VisualFeedbackContractError, match=message):
        load_visual_runtime_bundle(document)


@pytest.mark.parametrize(
    "field",
    [
        "actor_disjoint",
        "attempt_disjoint",
        "metric_gate_passed",
        "fresh_process_reload_passed",
    ],
)
def test_p6_incomplete_automatic_evidence_never_promotes(field: str) -> None:
    document = _runtime_bundle().to_json_object()
    p6_receipt = document["p6_promotion_receipt"]
    assert isinstance(p6_receipt, dict)
    p6_receipt[field] = False
    _resign(p6_receipt, "receipt_sha256")

    with pytest.raises(VisualFeedbackContractError, match="incomplete"):
        AutomatedP6PromotionReceipt.from_json_object(p6_receipt)


@pytest.mark.parametrize(
    "field",
    [
        "partition_sealed_before_read",
        "partition_disjoint",
        "evaluation_completed",
        "claim_policy_replay_completed",
    ],
)
def test_p8_incomplete_automatic_evidence_never_promotes(field: str) -> None:
    document = _runtime_bundle().to_json_object()
    p8_receipt = document["p8_promotion_receipt"]
    assert isinstance(p8_receipt, dict)
    p8_receipt[field] = False
    _resign(p8_receipt, "receipt_sha256")

    with pytest.raises(VisualFeedbackContractError, match="incomplete"):
        AutomatedP8PromotionReceipt.from_json_object(p8_receipt)


def test_p8_nonzero_violation_never_promotes_even_when_resigned() -> None:
    document = _runtime_bundle().to_json_object()
    p8_receipt = document["p8_promotion_receipt"]
    assert isinstance(p8_receipt, dict)
    p8_receipt["violation_count"] = 1
    _resign(p8_receipt, "receipt_sha256")

    with pytest.raises(VisualFeedbackContractError, match="zero"):
        AutomatedP8PromotionReceipt.from_json_object(p8_receipt)


def test_p8_promotion_rejects_missing_completeness_field() -> None:
    document = _runtime_bundle().p8_promotion_receipt.to_json_object()
    document.pop("admitted_item_count")

    with pytest.raises(VisualFeedbackContractError, match="fields differ"):
        AutomatedP8PromotionReceipt.from_json_object(document)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        (
            {
                "admitted_item_count": 101,
                "evaluation_exclusion_count": 1,
            },
            "zero-exclusion",
        ),
        ({"qc_decision_lineage_valid_item_count": 99}, "QC decision lineage"),
        (
            {"ready_sync_decision_lineage_valid_case_count": 79},
            "ready sync decision lineage",
        ),
    ],
)
def test_p8_promotion_rejects_resigned_partial_evaluation_or_lineage(
    updates: dict[str, object], message: str
) -> None:
    document = _runtime_bundle().p8_promotion_receipt.to_json_object()
    document.update(updates)
    _resign(document, "receipt_sha256")

    with pytest.raises(VisualFeedbackContractError, match=message):
        AutomatedP8PromotionReceipt.from_json_object(document)


def test_catalog_policy_binding_mismatch_never_loads() -> None:
    document = _runtime_bundle().to_json_object()
    policy = document["claim_policy"]
    assert isinstance(policy, dict)
    bindings = policy["allowed_claim_bindings"]
    assert isinstance(bindings, list)
    binding = bindings[0]
    assert isinstance(binding, dict)
    binding["cue_id"] = "different_visible_cue"
    _resign(policy, "policy_sha256")
    p6_receipt = document["p6_promotion_receipt"]
    assert isinstance(p6_receipt, dict)
    p6_receipt["policy_sha256"] = policy["policy_sha256"]
    _resign(p6_receipt, "receipt_sha256")
    p8_receipt = document["p8_promotion_receipt"]
    assert isinstance(p8_receipt, dict)
    p8_receipt["policy_sha256"] = policy["policy_sha256"]
    p8_receipt["p6_promotion_receipt_sha256"] = p6_receipt[
        "receipt_sha256"
    ]
    _resign(p8_receipt, "receipt_sha256")
    _resign(document, "bundle_sha256")

    with pytest.raises(VisualFeedbackContractError, match="bindings differ"):
        load_visual_runtime_bundle(document)


def test_claim_policy_requires_all_forbidden_target_classes() -> None:
    document = _runtime_bundle().to_json_object()
    policy = document["claim_policy"]
    assert isinstance(policy, dict)
    forbidden = policy["forbidden_identifier_tokens"]
    assert isinstance(forbidden, list)
    forbidden.remove("tongue")
    _resign(policy, "policy_sha256")

    with pytest.raises(VisualFeedbackContractError, match="incomplete"):
        RuntimeVisualClaimPolicy.from_json_object(policy)


@pytest.mark.parametrize(
    "identity_field",
    [
        "tracker_sha256",
        "tracker_adapter_sha256",
        "decoder_sha256",
        "landmark_model_sha256",
        "normalizer_sha256",
        "cue_extractor_sha256",
        "video_qc_measurement_policy_sha256",
        "video_qc_measurement_code_sha256",
        "video_qc_decision_policy_sha256",
        "video_qc_decision_code_sha256",
        "av_sync_measurement_policy_sha256",
        "av_sync_measurement_code_sha256",
        "av_sync_decision_policy_sha256",
        "av_sync_decision_code_sha256",
        "phone_window_code_sha256",
        "phone_window_policy_sha256",
        "population_id_sha256",
        "context_contract_sha256",
    ],
)
def test_pipeline_component_tampering_breaks_p6_p8_identity_binding(
    identity_field: str,
) -> None:
    document = _runtime_bundle().to_json_object()
    identity = document["pipeline_identity"]
    assert isinstance(identity, dict)
    identity[identity_field] = _sha(f"wrong-{identity_field}")
    _resign(identity, "identity_sha256")
    _resign(document, "bundle_sha256")

    expected = (
        "calibration population"
        if identity_field == "population_id_sha256"
        else "P6 pipeline"
    )
    with pytest.raises(VisualFeedbackContractError, match=expected):
        load_visual_runtime_bundle(document)


def test_eligibility_receipt_from_another_bundle_is_rejected() -> None:
    first = _runtime_bundle(revision="runtime_revision_one")
    second = _runtime_bundle(revision="runtime_revision_two")
    receipt = AutomatedVisualRuntimeEligibilityReceipt.issue(first)

    with pytest.raises(VisualFeedbackContractError, match="supplied bundle"):
        load_automated_visual_runtime_eligibility(
            receipt.to_json_object(), bundle=second
        )


def test_attempt_reliability_round_trip_binds_every_runtime_receipt() -> None:
    evidence = _attempt_evidence()
    receipt = issue_attempt_visual_reliability(**evidence)
    restored = type(receipt).from_json_object(receipt.to_json_object())

    assert restored == receipt
    assert restored.status == "eligible_for_visual_supplement"
    assert restored.binding_receipt_sha256 == evidence[
        "binding_receipt"
    ].receipt_sha256
    assert restored.phone_anchor_sha256 == evidence["snapshot"].phone_anchors[
        0
    ].anchor_ref
    assert validate_attempt_visual_reliability(
        receipt=restored, **evidence
    ) == receipt


def test_attempt_reliability_rejects_loose_phone_window_mapping() -> None:
    evidence = _attempt_evidence()
    dense = evidence["phone_window_receipt"]
    assert isinstance(dense, DensePhoneWindowEvidence)
    evidence["phone_window_receipt"] = dense.runtime_receipt.to_json_object()

    with pytest.raises(
        VisualFeedbackContractError,
        match="phone-window evidence does not resolve",
    ):
        issue_attempt_visual_reliability(**evidence)


@pytest.mark.parametrize(
    ("target", "field", "message"),
    [
        ("decode", "decoder_code_sha256", "decoder executable"),
        ("tracking", "tracking_code_sha256", "tracking executable"),
        ("runtime", "phone_window_code_sha256", "runtime executable"),
        ("policy", "phone_window_policy_sha256", "semantic policy"),
    ],
)
def test_attempt_reliability_rejects_synthetic_phone_window_lineage(
    target: str,
    field: str,
    message: str,
) -> None:
    evidence = _attempt_evidence()
    dense = evidence["phone_window_receipt"]
    assert isinstance(dense, DensePhoneWindowEvidence)
    tampered = _tampered_dense_phone_window(
        dense=dense,
        target=target,
        field=field,
    )
    evidence["phone_window_receipt"] = tampered
    detection = evidence["detection_receipt"]
    assert isinstance(detection, RuntimeVisualDetectionEvidence)
    evidence["detection_receipt"] = replace(
        detection,
        phone_window_evidence=tampered,
    )

    with pytest.raises(VisualFeedbackContractError, match=message):
        issue_attempt_visual_reliability(**evidence)


def test_attempt_reliability_rejects_wrong_media_capture_contract() -> None:
    evidence = _attempt_evidence()
    evidence["media_capture_contract_id"] = "different_media_contract_v1"

    with pytest.raises(VisualFeedbackContractError, match="media capture"):
        issue_attempt_visual_reliability(**evidence)


@pytest.mark.parametrize(
    ("document_name", "message"),
    [
        ("detection_receipt", "typed runtime visual detection"),
        ("video_qc_receipt", "typed runtime video QC"),
        ("av_sync_receipt", "typed runtime A/V"),
    ],
)
def test_attempt_reliability_rejects_loose_decision_mappings(
    document_name: str,
    message: str,
) -> None:
    evidence = _attempt_evidence()
    value = evidence[document_name]
    if isinstance(value, RuntimeVisualDetectionEvidence):
        evidence[document_name] = {
            **value.unsigned_json_object(),
            "detection_receipt_sha256": value.detection_receipt_sha256,
        }
    else:
        evidence[document_name] = value.to_json_object()

    with pytest.raises(VisualFeedbackContractError, match=message):
        issue_attempt_visual_reliability(**evidence)


def test_detection_must_bind_the_exact_qc_and_sync_receipts() -> None:
    evidence = _attempt_evidence()
    detection = evidence["detection_receipt"]
    assert isinstance(detection, RuntimeVisualDetectionEvidence)
    unsigned = detection.unsigned_json_object()
    unsigned["video_qc_receipt_sha256"] = _sha("other-qc")
    unsigned["av_sync_receipt_sha256"] = _sha("other-sync")
    with pytest.raises(ValueError, match="decision lineage"):
        replace(
            detection,
            video_qc_receipt_sha256=_sha("other-qc"),
            av_sync_receipt_sha256=_sha("other-sync"),
            detection_receipt_sha256=sha256_document(unsigned),
        )


def test_raw_qc_observations_are_digest_bound_before_decision() -> None:
    evidence = _attempt_evidence()
    detection = evidence["detection_receipt"]
    assert isinstance(detection, RuntimeVisualDetectionEvidence)
    candidate = detection.video_qc_evidence
    assert isinstance(candidate, VideoQcCandidateEvidence)
    with pytest.raises(ValueError, match="receipt differs"):
        replace(
            candidate,
            metrics=replace(candidate.metrics, mean_luma_8bit=101.0),
        )


def test_raw_sync_offset_correlation_and_anchors_are_digest_bound() -> None:
    evidence = _attempt_evidence()
    detection = evidence["detection_receipt"]
    assert isinstance(detection, RuntimeVisualDetectionEvidence)
    candidate = detection.av_sync_candidate_evidence
    assert isinstance(candidate, AvSyncCandidateEvidence)
    with pytest.raises(ValueError, match="receipt digest differs"):
        replace(candidate, candidate_offset_ms=31)


def test_attempt_requires_supported_timed_error_anchor() -> None:
    evidence = _attempt_evidence()
    snapshot = evidence["snapshot"]
    anchor = snapshot.phone_anchors[0]
    non_error_anchor = replace(
        anchor,
        error_score=0.1,
        is_error=False,
        anchor_ref=_sha("non-error-anchor"),
    )
    evidence["snapshot"] = replace(snapshot, phone_anchors=(non_error_anchor,))

    with pytest.raises(VisualFeedbackContractError, match="timed upstream error"):
        issue_attempt_visual_reliability(**evidence)


def _runtime_bundle(
    *,
    revision: str = "runtime_revision_one",
) -> VisualRuntimeBundle:
    phone_reference_bundle = _p6_phone_reference_bundle()
    producer = phone_reference_bundle.producer
    parity = producer.runtime_parity_receipt
    (
        video_qc_decision_policy,
        av_sync_decision_policy,
        decision_policy_evidence,
    ) = _decision_policy_evidence()
    identity = VisualRuntimePipelineIdentity.issue(
        tracker_revision=parity.production_worker_revision,
        tracker_sha256=parity.production_worker_sha256,
        tracker_adapter_revision="mediapipe_lip_landmark_adapter_v1",
        tracker_adapter_sha256=parity.production_adapter_sha256,
        decoder_revision="ffmpeg_decoder_v1",
        decoder_sha256=_sha("decoder"),
        landmark_model_revision="face_landmarker_2025_01",
        landmark_model_sha256=parity.model_sha256,
        normalizer_revision="lip_width_normalizer_v1",
        normalizer_sha256=_sha("normalizer"),
        cue_extractor_revision="runtime_phone_window_tracker_v2",
        cue_extractor_sha256=_sha("phone-window-tracking-code"),
        video_qc_measurement_revision=VIDEO_QC_MEASUREMENT_REVISION,
        video_qc_measurement_policy_sha256=_sha("video-qc-policy"),
        video_qc_measurement_code_sha256=(
            video_qc_measurement_code_sha256()
        ),
        video_qc_decision_policy_revision=(
            video_qc_decision_policy.policy_revision
        ),
        video_qc_decision_policy_sha256=(
            video_qc_decision_policy.policy_sha256
        ),
        video_qc_decision_code_sha256=decision_adapter_code_sha256(),
        av_sync_measurement_revision=AV_SYNC_MEASUREMENT_REVISION,
        av_sync_measurement_policy_sha256=_sha("av-sync-policy"),
        av_sync_measurement_code_sha256=av_sync_measurement_code_sha256(),
        av_sync_decision_policy_revision=av_sync_decision_policy.policy_revision,
        av_sync_decision_policy_sha256=av_sync_decision_policy.policy_sha256,
        av_sync_decision_code_sha256=decision_adapter_code_sha256(),
        phone_window_code_revision="phone_window_runtime_v2",
        phone_window_code_sha256=phone_window_code_sha256(),
        phone_window_policy_revision="selected_phone_rgb24_v1",
        phone_window_policy_sha256=_phone_window_policy().policy_sha256,
        media_capture_contract_id="local_allowlisted_video_aac_mp4_v2",
        visual_observation_contract_id=P6_PHONE_REFERENCE_CAPTURE_CONTRACT_ID,
        population_id_sha256=producer.population_id_sha256,
        context_contract_sha256=_sha("phone-center-context"),
    )
    artifact = producer.calibration_artifact
    catalog = producer.claim_catalog
    policy = producer.claim_policy
    p6_receipt = build_automated_p6_promotion_receipt(
        geometry_bundle=_p6_geometry_bundle(identity),
        phone_reference_bundle=phone_reference_bundle,
        calibration_artifact=artifact,
        pipeline_identity=identity,
        video_qc_decision_policy=video_qc_decision_policy,
        av_sync_decision_policy=av_sync_decision_policy,
        decision_policy_evidence=decision_policy_evidence,
    )
    p8_store = _p8_evidence_store(
        p6_receipt=p6_receipt,
        artifact=artifact,
        catalog=catalog,
        policy=policy,
        identity=identity,
    )
    replay_receipt = build_p8_zero_violation_replay_receipt(
        evidence_store=p8_store,
        p6_promotion_receipt=p6_receipt,
        calibration_artifact=artifact,
        claim_catalog=catalog,
        claim_policy=policy,
        pipeline_identity=identity,
    )
    p8_receipt = build_automated_p8_promotion_receipt(
        evidence_store=p8_store,
        zero_violation_replay_receipt=replay_receipt,
        p6_promotion_receipt=p6_receipt,
        calibration_artifact=artifact,
        claim_catalog=catalog,
        claim_policy=policy,
        pipeline_identity=identity,
    )
    return VisualRuntimeBundle.issue(
        bundle_id="user_visual_runtime_bundle_v1",
        revision=revision,
        pipeline_identity=identity,
        p6_promotion_receipt=p6_receipt,
        p8_promotion_receipt=p8_receipt,
        calibration_artifact=artifact,
        claim_catalog=catalog,
        claim_policy=policy,
    )


def _decision_policy_evidence() -> tuple[
    P8RuntimePolicyArtifact,
    P8RuntimePolicyArtifact,
    RuntimeDecisionPolicyEvidence,
]:
    video_qc = P8RuntimePolicyArtifact.issue(
        policy_kind="video_qc_decision",
        policy_revision="calibrated_video_qc_fixture_v1",
        policy_document=VideoQcDecisionSemanticPolicy(
            policy_revision="calibrated_video_qc_fixture_v1",
            expected_measurement_policy_sha256=_sha("video-qc-policy"),
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
        ),
    )
    av_sync = P8RuntimePolicyArtifact.issue(
        policy_kind="av_sync_decision",
        policy_revision="calibrated_av_sync_fixture_v1",
        policy_document=AvSyncDecisionSemanticPolicy(
            policy_revision="calibrated_av_sync_fixture_v1",
            expected_measurement_policy_sha256=_sha("av-sync-policy"),
            maximum_absolute_offset_ms=120,
            minimum_correlation=0.8,
            minimum_anchor_count=1,
            minimum_anchor_strength=0.8,
        ),
    )
    evidence = RuntimeDecisionPolicyEvidence._issue_from_validated_actual_evaluation(
        source_manifest_sha256=_sha("ts174-tl174-manifest-fixture"),
        admission_receipt_sha256=_sha("ts174-tl174-admission-fixture"),
        evaluation_protocol_sha256=_sha("decision-evaluation-fixture"),
        evaluator_code_sha256=_sha("decision-evaluator-fixture"),
        video_qc_policy=video_qc,
        av_sync_policy=av_sync,
        admitted_item_count=10,
        evaluated_item_count=10,
        actor_overlap_count=0,
        attempt_overlap_count=0,
        qc_decision_lineage_valid_item_count=10,
        sync_decision_lineage_valid_item_count=10,
        fresh_process_reload_receipt_sha256=_sha("decision-reload-fixture"),
    )
    return video_qc, av_sync, evidence


def _decision_policy_builder_kwargs() -> dict[str, object]:
    video_qc, av_sync, evidence = _decision_policy_evidence()
    return {
        "video_qc_decision_policy": video_qc,
        "av_sync_decision_policy": av_sync,
        "decision_policy_evidence": evidence,
    }


def _p6_geometry_bundle(
    identity: VisualRuntimePipelineIdentity,
) -> P6GeometryEvidenceBundle:
    del identity
    document = json.loads(
        (
            ROOT
            / "artifacts/visual_feedback/p6/aihub68_geometry_evidence_bundle_v2.json"
        ).read_text(encoding="utf-8")
    )
    return P6GeometryEvidenceBundle.from_json_object(document)


def _p6_phone_reference_bundle() -> P6PhoneReferenceEvidenceBundle:
    document = json.loads(
        (
            ROOT
            / "artifacts/visual_feedback/p6/aihub68_phone_reference_bundle_v2.json"
        ).read_text(encoding="utf-8")
    )
    return P6PhoneReferenceEvidenceBundle.from_json_object(document)


def _phone_window_policy() -> PhoneWindowDecodePolicy:
    return PhoneWindowDecodePolicy(
        policy_revision="selected_phone_rgb24_v1",
        expected_av_sync_policy_sha256=_sha("av-sync-policy"),
        context_contract_sha256=_sha("phone-center-context"),
        maximum_absolute_offset_ms=300,
        padding_before_ms=100,
        padding_after_ms=100,
        minimum_frame_count=2,
        maximum_frame_count=12,
        maximum_window_duration_ms=1_000,
        maximum_rgb_bytes=80 * 1024 * 1024,
    )


def _unused_synthetic_p6_geometry_bundle(
    identity: VisualRuntimePipelineIdentity,
) -> P6GeometryEvidenceBundle:
    thresholds = P6GeometryThresholds()
    mapping_indices = tuple(MEDIAPIPE_LIP_CANDIDATE_INDICES[:24])
    mapping_document = {
        "gold_keypoint_names": list(AIHUB68_KEYPOINT_NAMES),
        "mediapipe_indices": list(mapping_indices),
        "candidate_set": list(MEDIAPIPE_LIP_CANDIDATE_INDICES),
        "derivation": "development_only_rectangular_hungarian_mean_nle_v1",
    }
    metrics = P6GeometryMetricSummary(
        development_sample_count=250,
        development_detected_count=248,
        development_coverage_fraction=0.992,
        development_normalized_landmark_error=0.05,
        calibration_sample_count=250,
        calibration_detected_count=248,
        calibration_coverage_fraction=0.992,
        calibration_normalized_landmark_error=0.05,
        calibration_nle_p95=0.08,
        repeatability_group_count=25,
        repeatability_normalized_cue_rmse=0.10,
        slices=tuple(
            P6GeometrySliceMetric(
                dimension="camera",
                label=f"camera_{camera_index}",
                sample_count=50,
                detected_count=49,
                coverage_fraction=0.98,
                normalized_landmark_error=0.08,
            )
            for camera_index in range(1, 6)
        ),
    )
    artifact = P6GeometryArtifact.issue(
        source_revision="aihub68_revision_2026_08",
        dataset_manifest_sha256=_sha("p6-manifest"),
        population_id_sha256=identity.population_id_sha256,
        label_archive_sha256=_sha("p6-label-archive"),
        image_archive_sha256=_sha("p6-image-archive"),
        development_partition_sha256=_sha("p6-development-partition"),
        calibration_partition_sha256=_sha("p6-calibration-partition"),
        development_actor_count=6,
        calibration_actor_count=5,
        actor_overlap_count=0,
        attempt_overlap_count=0,
        tracker_revision=identity.tracker_revision,
        tracker_sha256=identity.tracker_sha256,
        model_sha256=identity.landmark_model_sha256,
        admission_adapter_sha256=_sha("p6-admission-adapter"),
        calibration_service_sha256=_sha("p6-calibration-service"),
        mapping_mediapipe_indices=mapping_indices,
        mapping_sha256=sha256_document(mapping_document),
        metrics=metrics,
        thresholds=thresholds,
        threshold_protocol_sha256=thresholds.threshold_protocol_sha256,
        thresholds_frozen_before_metric_access=True,
        split_frozen_before_metric_access=True,
        gate_status="passed",
        reason_codes=(),
        parity_probe_sha256=p6_mapping_parity_probe_sha256(mapping_indices),
    )
    reload_unsigned = {
        "schema_version": P6_GEOMETRY_RELOAD_SCHEMA_VERSION,
        "loader_revision": "p6_geometry_strict_loader_v2",
        "artifact_sha256": artifact.artifact_sha256,
        "artifact_document_sha256": sha256_document(artifact.to_json_object()),
        "mapping_sha256": artifact.mapping_sha256,
        "threshold_protocol_sha256": artifact.threshold_protocol_sha256,
        "parity_probe_sha256": artifact.parity_probe_sha256,
        "fresh_process": True,
        "hash_match": True,
        "mapping_hash_match": True,
        "threshold_hash_match": True,
        "output_parity": True,
        "network_isolation": "seccomp_socket_denied",
    }
    reload_receipt = P6GeometryReloadReceipt(
        loader_revision="p6_geometry_strict_loader_v2",
        artifact_sha256=artifact.artifact_sha256,
        artifact_document_sha256=sha256_document(artifact.to_json_object()),
        mapping_sha256=artifact.mapping_sha256,
        threshold_protocol_sha256=artifact.threshold_protocol_sha256,
        parity_probe_sha256=artifact.parity_probe_sha256,
        receipt_sha256=sha256_document(reload_unsigned),
    )
    return P6GeometryEvidenceBundle.issue(
        artifact=artifact,
        reload_receipt=reload_receipt,
    )


@dataclass(frozen=True, slots=True)
class _InMemoryPersistedP8Evidence:
    seal: P8ReadZeroSeal
    report: P8LockedEvaluationReport
    source_manifest: P8LockedSourceManifest | None
    metrics: P8AggregateEvaluationMetrics | None

    def load_read_zero_seal(self) -> P8ReadZeroSeal:
        return P8ReadZeroSeal.from_json_object(self.seal.to_json_object())

    def load_locked_evaluation_result(
        self,
    ) -> tuple[
        P8LockedEvaluationReport,
        P8LockedSourceManifest | None,
        P8AggregateEvaluationMetrics | None,
    ]:
        report = P8LockedEvaluationReport.from_json_object(
            self.report.to_json_object()
        )
        source_manifest = (
            P8LockedSourceManifest.from_json_object(
                self.source_manifest.to_json_object()
            )
            if self.source_manifest is not None
            else None
        )
        metrics = (
            P8AggregateEvaluationMetrics.from_json_object(
                self.metrics.to_json_object()
            )
            if self.metrics is not None
            else None
        )
        return report, source_manifest, metrics


def _p8_evidence_store(
    *,
    p6_receipt: AutomatedP6PromotionReceipt,
    artifact: VisualCalibrationArtifact,
    catalog: VisualClaimCatalog,
    policy: RuntimeVisualClaimPolicy,
    identity: VisualRuntimePipelineIdentity,
) -> _InMemoryPersistedP8Evidence:
    seal = P8ReadZeroSeal.issue(
        seal_id="p8_runtime_seal_001",
        protocol_revision="p8_runtime_protocol_v2",
        source_revision="aihub538_revision_2026_08",
        p6_evidence_receipt_sha256=p6_receipt.receipt_sha256,
        calibration_artifact_sha256=artifact.artifact_sha256,
        claim_catalog_sha256=catalog.catalog_sha256,
        claim_policy_sha256=policy.policy_sha256,
        media_capture_contract_id=identity.media_capture_contract_id,
        media_capture_contract_sha256=identity.media_capture_contract_sha256,
        visual_observation_contract_id=identity.visual_observation_contract_id,
        visual_observation_contract_sha256=(
            identity.visual_observation_contract_sha256
        ),
        decoder_code_sha256=identity.decoder_sha256,
        tracker_code_sha256=identity.tracker_sha256,
        tracker_adapter_code_sha256=identity.tracker_adapter_sha256,
        landmark_model_sha256=identity.landmark_model_sha256,
        normalizer_code_sha256=identity.normalizer_sha256,
        cue_extractor_code_sha256=identity.cue_extractor_sha256,
        video_qc_measurement_code_sha256=(
            identity.video_qc_measurement_code_sha256
        ),
        video_qc_measurement_policy_sha256=(
            identity.video_qc_measurement_policy_sha256
        ),
        video_qc_decision_code_sha256=identity.video_qc_decision_code_sha256,
        video_qc_decision_policy_revision=(
            identity.video_qc_decision_policy_revision
        ),
        video_qc_decision_policy_sha256=(
            identity.video_qc_decision_policy_sha256
        ),
        av_sync_measurement_code_sha256=(
            identity.av_sync_measurement_code_sha256
        ),
        av_sync_measurement_policy_sha256=(
            identity.av_sync_measurement_policy_sha256
        ),
        av_sync_decision_code_sha256=identity.av_sync_decision_code_sha256,
        av_sync_decision_policy_revision=(
            identity.av_sync_decision_policy_revision
        ),
        av_sync_decision_policy_sha256=(
            identity.av_sync_decision_policy_sha256
        ),
        phone_window_code_sha256=identity.phone_window_code_sha256,
        phone_window_policy_revision=identity.phone_window_policy_revision,
        phone_window_policy_sha256=identity.phone_window_policy_sha256,
        sealed_at_utc="2026-08-28T01:00:00Z",
    )
    transaction = P8AccessTransaction.issue(
        transaction_id="p8_runtime_access_001",
        seal=seal,
        consumed_at_utc="2026-08-28T01:01:00Z",
    )
    result = _p8_locked_result(transaction)
    source_manifest = result.source_manifest
    metrics = result.metrics
    report = P8LockedEvaluationReport.issue(
        report_id="p8_runtime_report_001",
        protocol_revision=seal.protocol_revision,
        transaction=transaction,
        status=P8LockedEvaluationStatus.COMPLETED,
        source_open_attempted=True,
        admitted_item_count=metrics.admitted_item_count,
        paired_item_count=metrics.paired_item_count,
        evaluated_item_count=metrics.evaluated_item_count,
        evaluation_exclusion_count=metrics.evaluation_exclusion_count,
        successful_item_count=metrics.successful_item_count,
        failed_item_count=metrics.failed_item_count,
        locked_source_manifest_sha256=source_manifest.manifest_sha256,
        aggregate_metrics_sha256=metrics.metrics_sha256,
        reason_codes=(),
    )
    return _InMemoryPersistedP8Evidence(
        seal=seal,
        report=report,
        source_manifest=source_manifest,
        metrics=metrics,
    )


def _p8_locked_result(
    transaction: P8AccessTransaction,
) -> P8LockedSourceEvaluationResult:
    source_manifest = P8LockedSourceManifest.issue(
        source_revision="aihub538_revision_2026_08",
        transaction=transaction,
        admitted_item_count=100,
        archives=(
            P8LockedSourceArchiveDigest(
                file_key="55114",
                partition_id="VS11",
                role="locked_media",
                byte_count=18_000,
                archive_sha256=_sha("p8-locked-media-archive"),
            ),
            P8LockedSourceArchiveDigest(
                file_key="55082",
                partition_id="VL11",
                role="locked_labels",
                byte_count=168,
                archive_sha256=_sha("p8-locked-label-archive"),
            ),
        ),
    )
    metrics = P8AggregateEvaluationMetrics.issue(
        locked_source_manifest_sha256=source_manifest.manifest_sha256,
        admitted_item_count=100,
        paired_item_count=100,
        evaluated_item_count=100,
        evaluation_exclusion_count=0,
        qc_decision_lineage_valid_item_count=100,
        ready_sync_decision_lineage_valid_case_count=80,
        successful_item_count=99,
        failed_item_count=1,
        decode_success_rate=0.99,
        supported_clip_count=90,
        supported_clip_below_single_face_threshold_count=0,
        aggregate_supported_slice_coverage=0.91,
        av_anchor_availability_rate=0.81,
        ready_case_count=80,
        maximum_ready_absolute_sync_offset_ms=119.0,
        claim_policy_violation_count=0,
        forbidden_claim_fixture_violation_count=0,
    )
    return P8LockedSourceEvaluationResult.issue(
        source_manifest=source_manifest,
        metrics=metrics,
    )


@dataclass(frozen=True, slots=True)
class _RuntimeLandmarkEvidence:
    evidence_sha256: str
    model_sha256: str
    container_sha256: str


def _attempt_evidence() -> dict[str, object]:
    bundle = _runtime_bundle()
    eligibility_receipt = AutomatedVisualRuntimeEligibilityReceipt.issue(bundle)
    fixture = build_internal_contract_fixture(repository_root=ROOT)
    binding = fixture.request.binding_receipt
    snapshot = fixture.evidence_adapter.resolve(
        analysis_ref=fixture.request.analysis_ref,
        binding_receipt=binding,
        video_handle=fixture.request.video_handle,
    )
    landmark_sha256 = _sha("runtime-landmarks")
    landmark_evidence = _RuntimeLandmarkEvidence(
        evidence_sha256=landmark_sha256,
        model_sha256=bundle.pipeline_identity.landmark_model_sha256,
        container_sha256=binding.video_source_sha256,
    )
    timestamps_us = (0, 100_000)
    metrics = VideoQcMetrics(
        frame_count=2,
        single_face_frame_count=2,
        multiple_face_frame_count=0,
        single_face_coverage=1.0,
        multiple_face_frame_fraction=0.0,
        mean_luma_8bit=100.0,
        underexposed_pixel_fraction=0.0,
        overexposed_pixel_fraction=0.0,
        mean_neighbor_luma_delta_8bit=10.0,
        lip_roi_frame_count=2,
        lip_roi_mean_luma_8bit=100.0,
        lip_roi_underexposed_pixel_fraction=0.0,
        lip_roi_overexposed_pixel_fraction=0.0,
        lip_roi_mean_neighbor_luma_delta_8bit=10.0,
        track_transition_coverage=1.0,
        longest_single_face_run_fraction=1.0,
        maximum_lip_center_step_normalized=0.01,
    )
    qc_provenance = {
        "schema_version": "voice_coach.video_qc_provenance.v2",
        "asset_handle_id": _sha("global-visual-asset"),
        "container_sha256": binding.video_source_sha256,
        "video_stream_index": 0,
        "sample_timestamps_us": list(timestamps_us),
        "landmark_timestamps_ms": [0, 100],
        "decoded_rgb_evidence_sha256": _sha("global-rgb"),
        "landmark_evidence_sha256": landmark_sha256,
        "landmark_model_sha256": bundle.pipeline_identity.landmark_model_sha256,
        "policy_receipt_sha256": (
            bundle.pipeline_identity.video_qc_measurement_policy_sha256
        ),
        "measurement_revision": VIDEO_QC_MEASUREMENT_REVISION,
        "measurement_adapter_code_sha256": (
            video_qc_measurement_code_sha256()
        ),
    }
    qc_candidate_unsigned = {
        "schema_version": "voice_coach.video_qc_candidate.v2",
        "status": "candidate_unvalidated",
        **{
            key: value
            for key, value in qc_provenance.items()
            if key != "schema_version"
        },
        "metrics": metrics.to_json_object(),
        "reason_codes": [],
        "provenance_receipt_sha256": sha256_document(qc_provenance),
    }
    qc_candidate = VideoQcCandidateEvidence(
        status="candidate_unvalidated",
        asset_handle_id=_sha("global-visual-asset"),
        container_sha256=binding.video_source_sha256,
        video_stream_index=0,
        sample_timestamps_us=timestamps_us,
        landmark_timestamps_ms=(0, 100),
        decoded_rgb_evidence_sha256=_sha("global-rgb"),
        landmark_evidence_sha256=landmark_sha256,
        landmark_model_sha256=bundle.pipeline_identity.landmark_model_sha256,
        policy_receipt_sha256=(
            bundle.pipeline_identity.video_qc_measurement_policy_sha256
        ),
        measurement_revision=VIDEO_QC_MEASUREMENT_REVISION,
        measurement_adapter_code_sha256=video_qc_measurement_code_sha256(),
        metrics=metrics,
        reason_codes=(),
        provenance_receipt_sha256=sha256_document(qc_provenance),
        receipt_sha256=sha256_document(qc_candidate_unsigned),
    )
    video_qc_policy, av_sync_policy, _ = _decision_policy_evidence()
    video_qc_receipt = CalibratedVideoQcDecisionAdapter(
        policy=video_qc_policy.policy_document.to_runtime_policy()
    ).decide(candidate=qc_candidate, binding_receipt=binding)

    anchors = (
        AvSyncAnchorCandidate(audio_ms=0, video_ms=30, strength=1.0),
    )
    sync_lineage = {
        "schema_version": "voice_coach.lip_audio_av_sync_lineage.v2",
        "asset_handle_id": _sha("global-visual-asset"),
        "audio_derivation_receipt_sha256": _sha("audio-derivation"),
        "audio_source_sha256": binding.audio_source_sha256,
        "video_source_sha256": binding.video_source_sha256,
        "audio_stream_index": 0,
        "audio_start_time_us": 0,
        "video_stream_index": 0,
        "video_start_time_us": 0,
        "landmark_evidence_sha256": landmark_sha256,
        "landmark_model_sha256": bundle.pipeline_identity.landmark_model_sha256,
        "measurement_policy_sha256": (
            bundle.pipeline_identity.av_sync_measurement_policy_sha256
        ),
        "measurement_revision": AV_SYNC_MEASUREMENT_REVISION,
        "measurement_adapter_code_sha256": (
            av_sync_measurement_code_sha256()
        ),
    }
    sync_candidate_unsigned = {
        "schema_version": "voice_coach.lip_audio_av_sync_candidate.v2",
        **{
            key: value
            for key, value in sync_lineage.items()
            if key != "schema_version"
        },
        "status": "candidate_unvalidated",
        "candidate_offset_ms": 30,
        "correlation": 0.95,
        "anchors": [anchor.to_json_object() for anchor in anchors],
        "reason_codes": [],
        "lineage_receipt_sha256": sha256_document(sync_lineage),
    }
    sync_candidate = AvSyncCandidateEvidence(
        asset_handle_id=_sha("global-visual-asset"),
        audio_derivation_receipt_sha256=_sha("audio-derivation"),
        audio_source_sha256=binding.audio_source_sha256,
        video_source_sha256=binding.video_source_sha256,
        audio_stream_index=0,
        audio_start_time_us=0,
        video_stream_index=0,
        video_start_time_us=0,
        landmark_evidence_sha256=landmark_sha256,
        landmark_model_sha256=bundle.pipeline_identity.landmark_model_sha256,
        measurement_policy_sha256=(
            bundle.pipeline_identity.av_sync_measurement_policy_sha256
        ),
        measurement_revision=AV_SYNC_MEASUREMENT_REVISION,
        measurement_adapter_code_sha256=av_sync_measurement_code_sha256(),
        status="candidate_unvalidated",
        candidate_offset_ms=30,
        correlation=0.95,
        anchors=anchors,
        reason_codes=(),
        lineage_receipt_sha256=sha256_document(sync_lineage),
        receipt_sha256=sha256_document(sync_candidate_unsigned),
    )
    av_sync_receipt = CalibratedAvSyncDecisionAdapter(
        policy=av_sync_policy.policy_document.to_runtime_policy()
    ).decide(candidate=sync_candidate, binding_receipt=binding)
    sync_offset = PolicyValidatedAvSyncOffset.issue_from_decision(
        decision=av_sync_receipt
    )
    detection_unsigned = {
        "schema_version": "voice_coach.runtime_visual_detection.v3",
        "binding_receipt_sha256": binding.receipt_sha256,
        "container_sha256": binding.video_source_sha256,
        "sample_timestamps_sha256": _sha("sample-timestamps"),
        "landmark_evidence_sha256": landmark_sha256,
        "model_sha256": bundle.pipeline_identity.landmark_model_sha256,
        "video_qc_candidate_receipt_sha256": qc_candidate.receipt_sha256,
        "video_qc_receipt_sha256": video_qc_receipt.receipt_sha256,
        "av_sync_candidate_receipt_sha256": sync_candidate.receipt_sha256,
        "av_sync_receipt_sha256": av_sync_receipt.receipt_sha256,
        "av_sync_offset_receipt_sha256": sync_offset.receipt_sha256,
    }
    detection_receipt_sha256 = sha256_document(detection_unsigned)
    phone_window_policy = _phone_window_policy()
    request = PhoneWindowDecodeRequest.issue(
        attempt_id_sha256=binding.attempt_id_sha256,
        binding_receipt_sha256=binding.receipt_sha256,
        container_sha256=binding.video_source_sha256,
        detection_receipt_sha256=detection_receipt_sha256,
        phone_anchor_sha256=snapshot.phone_anchors[0].anchor_ref,
        av_sync_receipt_sha256=av_sync_receipt.receipt_sha256,
        av_sync_offset_receipt_sha256=sync_offset.receipt_sha256,
        policy=phone_window_policy,
        video_start_ms=200,
        video_end_ms=400,
    )
    sample = Rgb24Sample(
        asset_handle_id=_sha("phone-window-asset"),
        container_sha256=binding.video_source_sha256,
        video_stream_index=0,
        frames=(
            DecodedRgbFrame(
                timestamp_us=200_000, width=1, height=1, rgb24=b"\x00\x00\x00"
            ),
            DecodedRgbFrame(
                timestamp_us=300_000, width=1, height=1, rgb24=b"\x01\x01\x01"
            ),
        ),
        ffmpeg_revision="ffmpeg_test_v1",
    )
    decode_receipt = PhoneWindowDecodeReceipt.issue(
        request=request,
        video_stream_index=0,
        selected_frame_indices=(0, 1),
        decoded_timestamps_us=(200_000, 300_000),
        width=1,
        height=1,
        decoded_rgb_sha256=decoded_rgb_sha256(sample),
        ffmpeg_revision=sample.ffmpeg_revision,
        decoder_code_sha256=bundle.pipeline_identity.decoder_sha256,
    )
    window = Rgb24PhoneWindow(
        request=request, sample=sample, decode_receipt=decode_receipt
    )
    phone_window_landmark_evidence = _RuntimeLandmarkEvidence(
        evidence_sha256=_sha("dense-phone-window-landmarks"),
        model_sha256=bundle.pipeline_identity.landmark_model_sha256,
        container_sha256=binding.video_source_sha256,
    )
    tracking_receipt = PhoneWindowTrackingReceipt.issue(
        window=window,
        landmark_evidence_sha256=phone_window_landmark_evidence.evidence_sha256,
        model_sha256=phone_window_landmark_evidence.model_sha256,
        tracking_code_sha256=bundle.pipeline_identity.cue_extractor_sha256,
    )
    runtime_receipt = RuntimePhoneWindowReceipt.issue(
        request=request,
        video_qc_receipt_sha256=video_qc_receipt.receipt_sha256,
        tracking_receipt=tracking_receipt,
        policy=phone_window_policy,
    )
    phone_window_receipt = DensePhoneWindowEvidence(
        decode_receipt=decode_receipt,
        tracking_receipt=tracking_receipt,
        runtime_receipt=runtime_receipt,
        landmark_evidence=phone_window_landmark_evidence,
    )
    detection_receipt = RuntimeVisualDetectionEvidence(
        binding_receipt_sha256=binding.receipt_sha256,
        container_sha256=binding.video_source_sha256,
        sample_timestamps_sha256=_sha("sample-timestamps"),
        landmark_evidence_sha256=landmark_sha256,
        model_sha256=bundle.pipeline_identity.landmark_model_sha256,
        video_qc_candidate_receipt_sha256=qc_candidate.receipt_sha256,
        video_qc_receipt_sha256=video_qc_receipt.receipt_sha256,
        av_sync_candidate_receipt_sha256=sync_candidate.receipt_sha256,
        av_sync_receipt_sha256=av_sync_receipt.receipt_sha256,
        av_sync_offset_receipt_sha256=sync_offset.receipt_sha256,
        detection_receipt_sha256=detection_receipt_sha256,
        landmark_evidence=landmark_evidence,
        video_qc_evidence=qc_candidate,
        video_qc_decision=video_qc_receipt,
        av_sync_candidate_evidence=sync_candidate,
        av_sync_decision=av_sync_receipt,
        av_sync_evidence=sync_offset,
        phone_window_evidence=phone_window_receipt,
    )
    return {
        "bundle": bundle,
        "eligibility_receipt": eligibility_receipt,
        "binding_receipt": binding,
        "snapshot": snapshot,
        "expected_index": 0,
        "media_capture_contract_id": (
            bundle.pipeline_identity.media_capture_contract_id
        ),
        "detection_receipt": detection_receipt,
        "video_qc_receipt": video_qc_receipt,
        "av_sync_receipt": av_sync_receipt,
        "phone_window_receipt": phone_window_receipt,
    }


def _tampered_dense_phone_window(
    *,
    dense: DensePhoneWindowEvidence,
    target: str,
    field: str,
) -> DensePhoneWindowEvidence:
    decode_document = dense.decode_receipt.to_json_object()
    if target == "decode":
        decode_document[field] = _sha("synthetic-decoder-code")
        _resign(decode_document, "receipt_sha256")
    elif target == "policy":
        decode_document["decode_policy_sha256"] = _sha(
            "synthetic-phone-window-policy"
        )
        _resign(decode_document, "receipt_sha256")
    decode = PhoneWindowDecodeReceipt.from_json_object(decode_document)

    tracking_document = dense.tracking_receipt.to_json_object()
    tracking_document["phone_window_decode_receipt_sha256"] = (
        decode.receipt_sha256
    )
    if target == "tracking":
        tracking_document[field] = _sha("synthetic-tracking-code")
    _resign(tracking_document, "receipt_sha256")
    tracking = PhoneWindowTrackingReceipt.from_json_object(tracking_document)

    runtime_document = dense.runtime_receipt.to_json_object()
    runtime_document["phone_window_decode_receipt_sha256"] = (
        decode.receipt_sha256
    )
    runtime_document["phone_window_tracking_receipt_sha256"] = (
        tracking.receipt_sha256
    )
    if target == "runtime":
        runtime_document[field] = _sha("synthetic-phone-window-code")
    elif target == "policy":
        runtime_document[field] = _sha("synthetic-phone-window-policy")
    _resign(runtime_document, "receipt_sha256")
    runtime = RuntimePhoneWindowReceipt.from_json_object(runtime_document)
    return DensePhoneWindowEvidence(
        decode_receipt=decode,
        tracking_receipt=tracking,
        runtime_receipt=runtime,
        landmark_evidence=dense.landmark_evidence,
    )


def _sha(label: str) -> str:
    return sha256_document({"fixture": label})


def _resign(document: dict[str, object], digest_field: str) -> None:
    unsigned = {key: value for key, value in document.items() if key != digest_field}
    document[digest_field] = sha256_document(unsigned)
