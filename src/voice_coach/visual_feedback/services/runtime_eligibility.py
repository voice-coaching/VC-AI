"""Producer-bound builders for automated visual runtime eligibility."""

from __future__ import annotations

from typing import Protocol

from ..errors import VisualFeedbackContractError
from ..models.artifacts import VisualCalibrationArtifact, VisualClaimCatalog
from ..models.enums import CalibrationScope
from ..models.integrity import sha256_document
from ..models.p6_geometry import (
    P6GeometryArtifact,
    P6GeometryEvidenceBundle,
    P6GeometryThresholds,
)
from ..models.p6_phone_reference import P6PhoneReferenceEvidenceBundle
from ..models.p8_aggregate_metrics import P8AggregateEvaluationMetrics
from ..models.p8_locked_evaluation import (
    P8LockedEvaluationReport,
    P8LockedEvaluationStatus,
    P8ReadZeroSeal,
)
from ..models.p8_locked_source_manifest import P8LockedSourceManifest
from ..models.p8_promotion import (
    P8_AUTOMATED_PROMOTION_BUNDLE_SCHEMA_VERSION,
    P8ZeroViolationReplayReceipt,
)
from ..models.runtime_eligibility import (
    AutomatedP6PromotionReceipt,
    AutomatedP8PromotionReceipt,
    RuntimeVisualClaimPolicy,
    VisualRuntimePipelineIdentity,
)
from ..models.runtime_policy_artifact import (
    AvSyncDecisionSemanticPolicy,
    P8RuntimePolicyArtifact,
    RuntimeDecisionPolicyEvidence,
    VideoQcDecisionSemanticPolicy,
)
from .p6_phone_reference import (
    P6PhoneReferenceCalibrationError,
    validate_p6_phone_reference_producer,
)


class PersistedP8LockedEvidencePort(Protocol):
    """Freshly reload immutable P8 state after the one-shot evaluation."""

    def load_read_zero_seal(self) -> P8ReadZeroSeal: ...

    def load_locked_evaluation_result(
        self,
    ) -> tuple[
        P8LockedEvaluationReport,
        P8LockedSourceManifest | None,
        P8AggregateEvaluationMetrics | None,
    ]: ...


def build_automated_p6_promotion_receipt(
    *,
    geometry_bundle: P6GeometryEvidenceBundle,
    phone_reference_bundle: P6PhoneReferenceEvidenceBundle,
    calibration_artifact: VisualCalibrationArtifact,
    pipeline_identity: VisualRuntimePipelineIdentity,
    video_qc_decision_policy: P8RuntimePolicyArtifact,
    av_sync_decision_policy: P8RuntimePolicyArtifact,
    decision_policy_evidence: RuntimeDecisionPolicyEvidence,
) -> AutomatedP6PromotionReceipt:
    """Promote only strict geometry plus phone-conditioned P6B evidence."""

    geometry_artifact = geometry_bundle.artifact
    phone_reference = phone_reference_bundle.producer
    parity = phone_reference.runtime_parity_receipt
    _validate_p6_geometry_thresholds(geometry_artifact)
    geometry_bundle.reload_receipt.validate_artifact(geometry_artifact)
    if (
        geometry_artifact.rights_scope
        != "project_user_confirmed_derived_geometry_only"
    ):
        raise VisualFeedbackContractError("P6 restricted-data rights scope differs")
    if calibration_artifact.scope is not CalibrationScope.CALIBRATED:
        raise VisualFeedbackContractError(
            "P6 runtime calibration artifact must use calibrated scope"
        )
    phone_reference_bundle.reload_receipt.validate_producer(phone_reference)
    try:
        validate_p6_phone_reference_producer(
            producer=phone_reference,
            geometry_bundle=geometry_bundle,
            production_tracker_revision=pipeline_identity.tracker_revision,
            production_tracker_sha256=pipeline_identity.tracker_sha256,
            landmark_model_sha256=pipeline_identity.landmark_model_sha256,
        )
    except P6PhoneReferenceCalibrationError as error:
        raise VisualFeedbackContractError(
            "P6B producer validation failed"
        ) from error
    if phone_reference.calibration_artifact != calibration_artifact:
        raise VisualFeedbackContractError(
            "P6B calibration artifact does not resolve to producer evidence"
        )
    if (
        calibration_artifact.capture_contract_id
        != pipeline_identity.visual_observation_contract_id
        or phone_reference.capture_contract_id
        != pipeline_identity.visual_observation_contract_id
    ):
        raise VisualFeedbackContractError(
            "P6 visual observation contract differs"
        )
    if calibration_artifact.population_id_sha256 != geometry_artifact.population_id_sha256:
        raise VisualFeedbackContractError(
            "P6 geometry and runtime calibration populations differ"
        )
    if geometry_artifact.population_id_sha256 != pipeline_identity.population_id_sha256:
        raise VisualFeedbackContractError("P6 pipeline population digest differs")
    if geometry_artifact.model_sha256 != pipeline_identity.landmark_model_sha256:
        raise VisualFeedbackContractError("P6 landmark model digest differs")
    if (
        parity.production_worker_revision != pipeline_identity.tracker_revision
        or parity.production_worker_sha256 != pipeline_identity.tracker_sha256
        or parity.production_adapter_sha256
        != pipeline_identity.tracker_adapter_sha256
        or parity.model_sha256 != pipeline_identity.landmark_model_sha256
    ):
        raise VisualFeedbackContractError("P6B production parity lineage differs")
    _validate_runtime_decision_policy_evidence(
        video_qc_decision_policy=video_qc_decision_policy,
        av_sync_decision_policy=av_sync_decision_policy,
        evidence=decision_policy_evidence,
        pipeline_identity=pipeline_identity,
    )
    return AutomatedP6PromotionReceipt._issue_from_validated_evidence(
        source_revision=geometry_artifact.source_revision,
        dataset_manifest_sha256=geometry_artifact.dataset_manifest_sha256,
        geometry_bundle_sha256=geometry_bundle.bundle_sha256,
        phone_reference_bundle_sha256=phone_reference_bundle.bundle_sha256,
        phone_reference_reload_receipt_sha256=(
            phone_reference_bundle.reload_receipt.receipt_sha256
        ),
        phone_reference_producer_sha256=phone_reference.producer_sha256,
        runtime_parity_receipt_sha256=parity.receipt_sha256,
        geometry_artifact_sha256=geometry_artifact.artifact_sha256,
        geometry_reload_receipt_sha256=(
            geometry_bundle.reload_receipt.receipt_sha256
        ),
        development_partition_sha256=(
            geometry_artifact.development_partition_sha256
        ),
        calibration_partition_sha256=(
            geometry_artifact.calibration_partition_sha256
        ),
        mapping_sha256=geometry_artifact.mapping_sha256,
        threshold_protocol_sha256=geometry_artifact.threshold_protocol_sha256,
        calibration_artifact_sha256=calibration_artifact.artifact_sha256,
        catalog_sha256=phone_reference.claim_catalog.catalog_sha256,
        policy_sha256=phone_reference.claim_policy.policy_sha256,
        decision_policy_evidence_receipt_sha256=(
            decision_policy_evidence.receipt_sha256
        ),
        pipeline_identity=pipeline_identity,
    )


def validate_automated_p6_promotion_receipt(
    *,
    receipt: AutomatedP6PromotionReceipt,
    geometry_bundle: P6GeometryEvidenceBundle,
    phone_reference_bundle: P6PhoneReferenceEvidenceBundle,
    calibration_artifact: VisualCalibrationArtifact,
    pipeline_identity: VisualRuntimePipelineIdentity,
    video_qc_decision_policy: P8RuntimePolicyArtifact,
    av_sync_decision_policy: P8RuntimePolicyArtifact,
    decision_policy_evidence: RuntimeDecisionPolicyEvidence,
) -> AutomatedP6PromotionReceipt:
    """Reproduce a P6 promotion from the exact producer evidence."""

    expected = build_automated_p6_promotion_receipt(
        geometry_bundle=geometry_bundle,
        phone_reference_bundle=phone_reference_bundle,
        calibration_artifact=calibration_artifact,
        pipeline_identity=pipeline_identity,
        video_qc_decision_policy=video_qc_decision_policy,
        av_sync_decision_policy=av_sync_decision_policy,
        decision_policy_evidence=decision_policy_evidence,
    )
    if receipt != expected:
        raise VisualFeedbackContractError(
            "P6 promotion receipt does not resolve to producer evidence"
        )
    return receipt


def build_p8_zero_violation_replay_receipt(
    *,
    evidence_store: PersistedP8LockedEvidencePort,
    p6_promotion_receipt: AutomatedP6PromotionReceipt,
    calibration_artifact: VisualCalibrationArtifact,
    claim_catalog: VisualClaimCatalog,
    claim_policy: RuntimeVisualClaimPolicy,
    pipeline_identity: VisualRuntimePipelineIdentity,
) -> P8ZeroViolationReplayReceipt:
    """Freshly reload and replay the exact persisted P8 aggregate evidence."""

    seal, source_manifest, report, metrics = _load_and_validate_p8_evidence(
        evidence_store=evidence_store,
        p6_promotion_receipt=p6_promotion_receipt,
        calibration_artifact=calibration_artifact,
        claim_catalog=claim_catalog,
        claim_policy=claim_policy,
        pipeline_identity=pipeline_identity,
    )
    return P8ZeroViolationReplayReceipt._issue_from_validated_locked_evaluation(
        protocol_revision=seal.protocol_revision,
        read_zero_seal_sha256=seal.seal_sha256,
        locked_evaluation_report_sha256=report.report_sha256,
        locked_manifest_sha256=source_manifest.manifest_sha256,
        aggregate_metrics_sha256=metrics.metrics_sha256,
        p6_promotion_receipt_sha256=p6_promotion_receipt.receipt_sha256,
        calibration_artifact_sha256=calibration_artifact.artifact_sha256,
        catalog_sha256=claim_catalog.catalog_sha256,
        policy_sha256=claim_policy.policy_sha256,
        pipeline_identity_sha256=pipeline_identity.identity_sha256,
        admitted_item_count=metrics.admitted_item_count,
        paired_item_count=metrics.paired_item_count,
        evaluated_item_count=metrics.evaluated_item_count,
        evaluation_exclusion_count=metrics.evaluation_exclusion_count,
        evaluation_exclusion_policy=metrics.evaluation_exclusion_policy,
        evaluated_case_count=metrics.evaluated_item_count,
        ready_case_count=metrics.ready_case_count,
    )


def build_automated_p8_promotion_receipt(
    *,
    evidence_store: PersistedP8LockedEvidencePort,
    zero_violation_replay_receipt: P8ZeroViolationReplayReceipt,
    p6_promotion_receipt: AutomatedP6PromotionReceipt,
    calibration_artifact: VisualCalibrationArtifact,
    claim_catalog: VisualClaimCatalog,
    claim_policy: RuntimeVisualClaimPolicy,
    pipeline_identity: VisualRuntimePipelineIdentity,
) -> AutomatedP8PromotionReceipt:
    """Promote only persisted P8 evidence and its exact deterministic replay."""

    seal, source_manifest, report, metrics = _load_and_validate_p8_evidence(
        evidence_store=evidence_store,
        p6_promotion_receipt=p6_promotion_receipt,
        calibration_artifact=calibration_artifact,
        claim_catalog=claim_catalog,
        claim_policy=claim_policy,
        pipeline_identity=pipeline_identity,
    )
    expected_replay = P8ZeroViolationReplayReceipt._issue_from_validated_locked_evaluation(
        protocol_revision=seal.protocol_revision,
        read_zero_seal_sha256=seal.seal_sha256,
        locked_evaluation_report_sha256=report.report_sha256,
        locked_manifest_sha256=source_manifest.manifest_sha256,
        aggregate_metrics_sha256=metrics.metrics_sha256,
        p6_promotion_receipt_sha256=p6_promotion_receipt.receipt_sha256,
        calibration_artifact_sha256=calibration_artifact.artifact_sha256,
        catalog_sha256=claim_catalog.catalog_sha256,
        policy_sha256=claim_policy.policy_sha256,
        pipeline_identity_sha256=pipeline_identity.identity_sha256,
        admitted_item_count=metrics.admitted_item_count,
        paired_item_count=metrics.paired_item_count,
        evaluated_item_count=metrics.evaluated_item_count,
        evaluation_exclusion_count=metrics.evaluation_exclusion_count,
        evaluation_exclusion_policy=metrics.evaluation_exclusion_policy,
        evaluated_case_count=metrics.evaluated_item_count,
        ready_case_count=metrics.ready_case_count,
    )
    if zero_violation_replay_receipt != expected_replay:
        raise VisualFeedbackContractError(
            "P8 zero-violation replay does not resolve to persisted evidence"
        )
    p8_bundle_sha256 = sha256_document(
        {
            "schema_version": P8_AUTOMATED_PROMOTION_BUNDLE_SCHEMA_VERSION,
            "read_zero_seal": seal.to_json_object(),
            "locked_source_manifest": source_manifest.to_json_object(),
            "locked_evaluation_report": report.to_json_object(),
            "aggregate_metrics": metrics.to_json_object(),
            "zero_violation_replay_receipt": (
                zero_violation_replay_receipt.to_json_object()
            ),
        }
    )
    return AutomatedP8PromotionReceipt._issue_from_persisted_locked_evaluation(
        locked_source_revision=seal.source_revision,
        locked_manifest_sha256=source_manifest.manifest_sha256,
        read_zero_seal_sha256=seal.seal_sha256,
        locked_evaluation_report_sha256=report.report_sha256,
        aggregate_metrics_sha256=metrics.metrics_sha256,
        zero_violation_replay_receipt_sha256=(
            zero_violation_replay_receipt.receipt_sha256
        ),
        p8_bundle_sha256=p8_bundle_sha256,
        p6_promotion_receipt_sha256=p6_promotion_receipt.receipt_sha256,
        calibration_artifact_sha256=calibration_artifact.artifact_sha256,
        catalog_sha256=claim_catalog.catalog_sha256,
        policy_sha256=claim_policy.policy_sha256,
        pipeline_identity=pipeline_identity,
        admitted_item_count=metrics.admitted_item_count,
        paired_item_count=metrics.paired_item_count,
        evaluated_item_count=metrics.evaluated_item_count,
        evaluation_exclusion_count=metrics.evaluation_exclusion_count,
        evaluation_exclusion_policy=metrics.evaluation_exclusion_policy,
        qc_decision_lineage_valid_item_count=(
            metrics.qc_decision_lineage_valid_item_count
        ),
        ready_sync_decision_lineage_valid_case_count=(
            metrics.ready_sync_decision_lineage_valid_case_count
        ),
        case_count=metrics.evaluated_item_count,
        ready_case_count=metrics.ready_case_count,
    )


def validate_automated_p8_promotion_receipt(
    *,
    receipt: AutomatedP8PromotionReceipt,
    evidence_store: PersistedP8LockedEvidencePort,
    zero_violation_replay_receipt: P8ZeroViolationReplayReceipt,
    p6_promotion_receipt: AutomatedP6PromotionReceipt,
    calibration_artifact: VisualCalibrationArtifact,
    claim_catalog: VisualClaimCatalog,
    claim_policy: RuntimeVisualClaimPolicy,
    pipeline_identity: VisualRuntimePipelineIdentity,
) -> AutomatedP8PromotionReceipt:
    """Reproduce P8 promotion from freshly reloaded producer evidence."""

    expected = build_automated_p8_promotion_receipt(
        evidence_store=evidence_store,
        zero_violation_replay_receipt=zero_violation_replay_receipt,
        p6_promotion_receipt=p6_promotion_receipt,
        calibration_artifact=calibration_artifact,
        claim_catalog=claim_catalog,
        claim_policy=claim_policy,
        pipeline_identity=pipeline_identity,
    )
    if receipt != expected:
        raise VisualFeedbackContractError(
            "P8 promotion receipt does not resolve to persisted evidence"
        )
    return receipt


def _load_and_validate_p8_evidence(
    *,
    evidence_store: PersistedP8LockedEvidencePort,
    p6_promotion_receipt: AutomatedP6PromotionReceipt,
    calibration_artifact: VisualCalibrationArtifact,
    claim_catalog: VisualClaimCatalog,
    claim_policy: RuntimeVisualClaimPolicy,
    pipeline_identity: VisualRuntimePipelineIdentity,
) -> tuple[
    P8ReadZeroSeal,
    P8LockedSourceManifest,
    P8LockedEvaluationReport,
    P8AggregateEvaluationMetrics,
]:
    seal = evidence_store.load_read_zero_seal()
    report, source_manifest, metrics = (
        evidence_store.load_locked_evaluation_result()
    )
    if (
        report.status is not P8LockedEvaluationStatus.COMPLETED
        or source_manifest is None
        or metrics is None
    ):
        raise VisualFeedbackContractError(
            "P8 persisted locked evaluation is not completed"
        )
    if report.seal_sha256 != seal.seal_sha256:
        raise VisualFeedbackContractError("P8 report seal digest differs")
    if report.protocol_revision != seal.protocol_revision:
        raise VisualFeedbackContractError("P8 report protocol differs")
    if report.aggregate_metrics_sha256 != metrics.metrics_sha256:
        raise VisualFeedbackContractError("P8 report metrics digest differs")
    completeness_values = (
        "admitted_item_count",
        "paired_item_count",
        "evaluated_item_count",
        "evaluation_exclusion_count",
        "evaluation_exclusion_policy",
    )
    if any(
        getattr(report, name) != getattr(metrics, name)
        for name in completeness_values
    ):
        raise VisualFeedbackContractError(
            "P8 report/metrics completeness evidence differs"
        )
    if (
        source_manifest.admitted_item_count != metrics.admitted_item_count
        or source_manifest.evaluation_exclusion_policy
        != metrics.evaluation_exclusion_policy
    ):
        raise VisualFeedbackContractError(
            "P8 manifest/metrics completeness evidence differs"
        )
    if metrics.evaluation_exclusion_count != 0:
        raise VisualFeedbackContractError(
            "P8 evaluation exclusions cannot authorize promotion"
        )
    if (
        report.locked_source_manifest_sha256
        != source_manifest.manifest_sha256
    ):
        raise VisualFeedbackContractError("P8 report source manifest differs")
    if (
        metrics.locked_source_manifest_sha256
        != source_manifest.manifest_sha256
    ):
        raise VisualFeedbackContractError("P8 metrics source manifest differs")
    if (
        source_manifest.access_transaction_sha256
        != report.access_transaction_sha256
    ):
        raise VisualFeedbackContractError(
            "P8 source manifest transaction differs"
        )
    if source_manifest.source_revision != seal.source_revision:
        raise VisualFeedbackContractError("P8 source manifest revision differs")
    if source_manifest.dataset_id != seal.dataset_id:
        raise VisualFeedbackContractError("P8 source manifest dataset differs")
    if source_manifest.source_listing_sha256 != seal.source_listing_sha256:
        raise VisualFeedbackContractError("P8 source manifest listing differs")
    if metrics.threshold_failure_reason_codes(seal.evaluation_thresholds):
        raise VisualFeedbackContractError("P8 persisted metrics fail frozen thresholds")
    if seal.p6_evidence_receipt_sha256 != p6_promotion_receipt.receipt_sha256:
        raise VisualFeedbackContractError("P8 seal P6 receipt digest differs")
    if seal.calibration_artifact_sha256 != calibration_artifact.artifact_sha256:
        raise VisualFeedbackContractError("P8 seal calibration digest differs")
    if seal.claim_catalog_sha256 != claim_catalog.catalog_sha256:
        raise VisualFeedbackContractError("P8 seal catalog digest differs")
    if seal.claim_policy_sha256 != claim_policy.policy_sha256:
        raise VisualFeedbackContractError("P8 seal policy digest differs")
    if (
        p6_promotion_receipt.catalog_sha256 != claim_catalog.catalog_sha256
        or p6_promotion_receipt.policy_sha256 != claim_policy.policy_sha256
    ):
        raise VisualFeedbackContractError(
            "P8 claim evidence does not resolve to P6B"
        )
    if (
        seal.media_capture_contract_id
        != pipeline_identity.media_capture_contract_id
        or seal.media_capture_contract_sha256
        != pipeline_identity.media_capture_contract_sha256
        or seal.visual_observation_contract_id
        != pipeline_identity.visual_observation_contract_id
        or seal.visual_observation_contract_sha256
        != pipeline_identity.visual_observation_contract_sha256
    ):
        raise VisualFeedbackContractError("P8 seal capture contract pins differ")
    if calibration_artifact.scope is not CalibrationScope.CALIBRATED:
        raise VisualFeedbackContractError(
            "P8 runtime calibration must use calibrated scope"
        )
    if claim_catalog.scope is not CalibrationScope.CALIBRATED:
        raise VisualFeedbackContractError(
            "P8 runtime catalog must use calibrated scope"
        )
    if (
        calibration_artifact.capture_contract_id
        != pipeline_identity.visual_observation_contract_id
    ):
        raise VisualFeedbackContractError(
            "P8 runtime visual observation contract differs"
        )
    if calibration_artifact.population_id_sha256 != pipeline_identity.population_id_sha256:
        raise VisualFeedbackContractError("P8 runtime population digest differs")
    pipeline_pins = {
        "decoder_code_sha256": pipeline_identity.decoder_sha256,
        "tracker_code_sha256": pipeline_identity.tracker_sha256,
        "tracker_adapter_code_sha256": (
            pipeline_identity.tracker_adapter_sha256
        ),
        "landmark_model_sha256": pipeline_identity.landmark_model_sha256,
        "normalizer_code_sha256": pipeline_identity.normalizer_sha256,
        "cue_extractor_code_sha256": pipeline_identity.cue_extractor_sha256,
        "video_qc_measurement_code_sha256": (
            pipeline_identity.video_qc_measurement_code_sha256
        ),
        "video_qc_measurement_policy_sha256": (
            pipeline_identity.video_qc_measurement_policy_sha256
        ),
        "video_qc_decision_code_sha256": (
            pipeline_identity.video_qc_decision_code_sha256
        ),
        "video_qc_decision_policy_sha256": (
            pipeline_identity.video_qc_decision_policy_sha256
        ),
        "av_sync_measurement_code_sha256": (
            pipeline_identity.av_sync_measurement_code_sha256
        ),
        "av_sync_measurement_policy_sha256": (
            pipeline_identity.av_sync_measurement_policy_sha256
        ),
        "av_sync_decision_code_sha256": (
            pipeline_identity.av_sync_decision_code_sha256
        ),
        "av_sync_decision_policy_sha256": (
            pipeline_identity.av_sync_decision_policy_sha256
        ),
        "phone_window_code_sha256": pipeline_identity.phone_window_code_sha256,
        "phone_window_policy_sha256": (
            pipeline_identity.phone_window_policy_sha256
        ),
    }
    for name, expected_digest in pipeline_pins.items():
        if getattr(seal, name) != expected_digest:
            raise VisualFeedbackContractError(f"P8 seal {name} differs")
    if (
        seal.video_qc_decision_policy_revision
        != pipeline_identity.video_qc_decision_policy_revision
        or seal.av_sync_decision_policy_revision
        != pipeline_identity.av_sync_decision_policy_revision
    ):
        raise VisualFeedbackContractError(
            "P8 seal calibrated decision policy revisions differ"
        )
    if (
        seal.phone_window_policy_revision
        != pipeline_identity.phone_window_policy_revision
    ):
        raise VisualFeedbackContractError(
            "P8 seal phone-window policy revision differs"
        )
    return seal, source_manifest, report, metrics


def _validate_runtime_decision_policy_evidence(
    *,
    video_qc_decision_policy: P8RuntimePolicyArtifact,
    av_sync_decision_policy: P8RuntimePolicyArtifact,
    evidence: RuntimeDecisionPolicyEvidence,
    pipeline_identity: VisualRuntimePipelineIdentity,
) -> None:
    """Require actual-media calibration and exact production policy conversion."""

    if (
        video_qc_decision_policy.policy_kind != "video_qc_decision"
        or not isinstance(
            video_qc_decision_policy.policy_document,
            VideoQcDecisionSemanticPolicy,
        )
        or video_qc_decision_policy.policy_revision
        != pipeline_identity.video_qc_decision_policy_revision
        or video_qc_decision_policy.policy_sha256
        != pipeline_identity.video_qc_decision_policy_sha256
        or video_qc_decision_policy.policy_document.expected_measurement_policy_sha256
        != pipeline_identity.video_qc_measurement_policy_sha256
    ):
        raise VisualFeedbackContractError(
            "P6 video QC calibrated decision policy differs"
        )
    if (
        av_sync_decision_policy.policy_kind != "av_sync_decision"
        or not isinstance(
            av_sync_decision_policy.policy_document,
            AvSyncDecisionSemanticPolicy,
        )
        or av_sync_decision_policy.policy_revision
        != pipeline_identity.av_sync_decision_policy_revision
        or av_sync_decision_policy.policy_sha256
        != pipeline_identity.av_sync_decision_policy_sha256
        or av_sync_decision_policy.policy_document.expected_measurement_policy_sha256
        != pipeline_identity.av_sync_measurement_policy_sha256
    ):
        raise VisualFeedbackContractError(
            "P6 A/V sync calibrated decision policy differs"
        )
    # Conversion is part of admission: a semantic JSON artifact that cannot
    # recreate the exact composition-time dataclass is not a runtime policy.
    video_qc_runtime_policy = (
        video_qc_decision_policy.policy_document.to_runtime_policy()
    )
    av_sync_runtime_policy = (
        av_sync_decision_policy.policy_document.to_runtime_policy()
    )
    if (
        getattr(video_qc_runtime_policy, "policy_sha256", None)
        != video_qc_decision_policy.policy_sha256
        or getattr(av_sync_runtime_policy, "policy_sha256", None)
        != av_sync_decision_policy.policy_sha256
    ):
        raise VisualFeedbackContractError(
            "P6 calibrated decision runtime conversion differs"
        )
    if (
        evidence.video_qc_policy_artifact_sha256
        != video_qc_decision_policy.artifact_sha256
        or evidence.video_qc_policy_sha256
        != video_qc_decision_policy.policy_sha256
        or evidence.av_sync_policy_artifact_sha256
        != av_sync_decision_policy.artifact_sha256
        or evidence.av_sync_policy_sha256
        != av_sync_decision_policy.policy_sha256
    ):
        raise VisualFeedbackContractError(
            "P6 actual-media decision policy evidence differs"
        )


def _validate_p6_geometry_thresholds(artifact: P6GeometryArtifact) -> None:
    thresholds = artifact.thresholds
    metrics = artifact.metrics
    if thresholds != P6GeometryThresholds():
        raise VisualFeedbackContractError("P6 frozen geometry thresholds differ")
    failures: list[str] = []
    if artifact.actor_overlap_count != 0:
        failures.append("p6_actor_overlap_detected")
    if artifact.attempt_overlap_count != 0:
        failures.append("p6_attempt_overlap_detected")
    if artifact.calibration_actor_count < thresholds.minimum_calibration_actor_count:
        failures.append("p6_calibration_actor_count_below_threshold")
    if (
        metrics.calibration_sample_count
        < thresholds.minimum_evaluated_calibration_frame_count
    ):
        failures.append("p6_evaluated_frame_count_below_threshold")
    if metrics.calibration_coverage_fraction < thresholds.minimum_overall_coverage:
        failures.append("p6_overall_coverage_below_threshold")
    if metrics.calibration_normalized_landmark_error > thresholds.maximum_overall_nle:
        failures.append("p6_overall_nle_above_threshold")
    camera_slices = tuple(item for item in metrics.slices if item.dimension == "camera")
    if (
        len(camera_slices) != thresholds.required_camera_count
        or len({item.label for item in camera_slices})
        != thresholds.required_camera_count
    ):
        failures.append("p6_camera_slice_coverage_incomplete")
    if any(
        item.coverage_fraction < thresholds.minimum_camera_coverage
        for item in camera_slices
    ):
        failures.append("p6_camera_coverage_below_threshold")
    if any(
        item.normalized_landmark_error is None
        or item.normalized_landmark_error > thresholds.maximum_camera_nle
        for item in camera_slices
    ):
        failures.append("p6_camera_nle_above_threshold")
    if (
        metrics.repeatability_normalized_cue_rmse
        > thresholds.maximum_repeatability_normalized_cue_rmse
    ):
        failures.append("p6_repeatability_rmse_above_threshold")
    if artifact.gate_status != "passed" or artifact.reason_codes:
        failures.append("p6_geometry_gate_not_passed")
    if failures:
        raise VisualFeedbackContractError(
            f"P6 geometry threshold validation failed: {failures[0]}"
        )


__all__ = [
    "PersistedP8LockedEvidencePort",
    "build_automated_p6_promotion_receipt",
    "build_automated_p8_promotion_receipt",
    "build_p8_zero_violation_replay_receipt",
    "validate_automated_p6_promotion_receipt",
    "validate_automated_p8_promotion_receipt",
]
