"""The production visual contract is compact and dataset-independent."""

from dataclasses import replace

import pytest

from voice_coach.visual_feedback.errors import VisualFeedbackContractError
from voice_coach.pronunciation_attempts.adapters.production_visual_sidecar import (
    ProductionVisualSidecarAdapter,
)
from voice_coach.visual_feedback.models.artifacts import (
    CalibrationRule,
    VisualCalibrationArtifact,
    VisualClaim,
    VisualClaimCatalog,
)
from voice_coach.visual_feedback.models.enums import CalibrationScope, DeviationDirection
from voice_coach.visual_feedback.models.integrity import sha256_document
from voice_coach.visual_feedback.models.runtime_contract import (
    P6CalibrationReceipt,
    P8EvaluationReceipt,
    RuntimeClaimBinding,
    RuntimeVisualClaimPolicy,
    VisualRuntimeBundle,
    VisualRuntimePipelineIdentity,
    load_visual_runtime_bundle,
)


def _bundle() -> VisualRuntimeBundle:
    population = "a" * 64
    identity = VisualRuntimePipelineIdentity.issue(
        landmark_model_sha256="b" * 64,
        video_qc_decision_policy_sha256="c" * 64,
        av_sync_decision_policy_sha256="d" * 64,
        media_capture_contract_id="same_attempt_av_v1",
        visual_observation_contract_id="visible_lip_v1",
        population_id_sha256=population,
    )
    calibration = VisualCalibrationArtifact.issue(
        artifact_id="lip_calibration_v1",
        revision="v1",
        scope=CalibrationScope.CALIBRATED,
        capture_contract_id="visible_lip_v1",
        population_id_sha256=population,
        rules=(CalibrationRule(
            cue_id="lip_aperture_ratio", measurement_unit="ratio",
            expected_phones=("ㅅ",), context_id="phone_center",
            reference_center=0.2, reference_scale=0.1, support_minimum=0.0,
            support_maximum=1.0, feedback_minimum_absolute_deviation=1.0,
        ),),
    )
    claim = VisualClaim(
        action_id="reduce_lip_opening", approved_claim_id="lip_opening_high",
        renderer_key="lip_opening", cue_id="lip_aperture_ratio",
        expected_phones=("ㅅ",), context_id="phone_center",
        direction=DeviationDirection.ABOVE_REFERENCE,
    )
    catalog = VisualClaimCatalog.issue(
        catalog_id="lip_claims_v1", revision="v1",
        scope=CalibrationScope.CALIBRATED, claims=(claim,),
    )
    binding = RuntimeClaimBinding(
        approved_claim_id=claim.approved_claim_id, action_id=claim.action_id,
        renderer_key=claim.renderer_key, cue_id=claim.cue_id,
    )
    unsigned_policy = {
        "schema_version": "voice_coach.visual_claim_policy.v1",
        "policy_revision": "v1",
        "allowed_cue_ids": [claim.cue_id],
        "allowed_renderer_keys": [claim.renderer_key],
        "allowed_claim_bindings": [binding.to_json_object()],
        "forbidden_identifier_tokens": [
            "airflow", "diagnosis", "glottis", "medical", "tongue", "velum"
        ],
    }
    policy = RuntimeVisualClaimPolicy(
        policy_revision="v1", allowed_cue_ids=(claim.cue_id,),
        allowed_renderer_keys=(claim.renderer_key,),
        allowed_claim_bindings=(binding,),
        forbidden_identifier_tokens=(
            "airflow", "diagnosis", "glottis", "medical", "tongue", "velum"
        ), policy_sha256=sha256_document(unsigned_policy),
    )
    p6 = P6CalibrationReceipt.issue(
        revision="v1", pipeline_identity=identity,
        calibration_artifact=calibration, calibration_case_count=12,
    )
    p8 = P8EvaluationReceipt.issue(
        revision="v1", p6_receipt=p6, claim_catalog=catalog,
        claim_policy=policy, evaluation_case_count=8, violation_count=0,
    )
    return VisualRuntimeBundle.issue(
        bundle_id="visual_runtime_v1", revision="v1", pipeline_identity=identity,
        p6_receipt=p6, p8_receipt=p8, calibration_artifact=calibration,
        claim_catalog=catalog, claim_policy=policy,
    )


def test_bundle_round_trip_contains_no_dataset_or_locked_partition_fields() -> None:
    bundle = _bundle()
    assert load_visual_runtime_bundle(bundle.to_json_object()) == bundle
    text = repr(bundle.to_json_object()).lower()
    for forbidden in ("dataset", "archive", "partition", "speaker", "media_path"):
        assert forbidden not in text


def test_production_sidecar_accepts_only_the_new_strictly_reloaded_bundle() -> None:
    bundle = _bundle()
    sidecar = ProductionVisualSidecarAdapter(runtime_bundle=bundle)
    assert sidecar.runtime_bundle_sha256 == bundle.bundle_sha256


def test_p8_requires_zero_violation_evaluation() -> None:
    bundle = _bundle()
    document = bundle.p8_receipt.to_json_object()
    document["violation_count"] = 1
    document["receipt_sha256"] = sha256_document(
        {key: value for key, value in document.items() if key != "receipt_sha256"}
    )
    with pytest.raises(VisualFeedbackContractError, match="violations"):
        P8EvaluationReceipt.from_json_object(document)


def test_bundle_rejects_p6_artifact_substitution() -> None:
    bundle = _bundle()
    with pytest.raises(VisualFeedbackContractError):
        replace(bundle, p6_receipt=replace(
            bundle.p6_receipt, calibration_artifact_sha256="e" * 64,
            receipt_sha256="f" * 64,
        ))
