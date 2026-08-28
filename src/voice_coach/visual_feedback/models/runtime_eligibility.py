"""Immutable automated eligibility contracts for user-runtime visual feedback.

The contracts in this module are deliberately separate from the P6/P8 v1
engineering-summary validators.  A v1 non-promotional receipt, a synthetic
artifact, or a digest that does not resolve through every lineage edge cannot
mint either a runtime-eligibility receipt or an attempt-reliability receipt.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ...pronunciation_attempts.phone_window import DensePhoneWindowEvidence
from ..errors import VisualFeedbackContractError
from .artifacts import VisualCalibrationArtifact, VisualClaimCatalog
from .binding import SameAttemptBindingReceipt
from .enums import CalibrationScope, UpstreamReliabilityStatus
from .evidence import SeungunEvidenceSnapshot, UpstreamPhoneAnchor
from .integrity import (
    require_boolean,
    require_exact_keys,
    require_integer,
    require_mapping,
    require_non_empty_string,
    require_safe_identifier,
    require_sha256,
    sha256_document,
)
from .p8_locked_evaluation import P8_LOCKED_SOURCE_LISTING_SHA256
from .p8_evaluation_completeness import (
    P8_EVALUATION_EXCLUSION_POLICY,
    require_p8_evaluation_exclusion_policy,
    validate_p8_evaluation_completeness,
)

VISUAL_RUNTIME_PIPELINE_IDENTITY_SCHEMA_VERSION = (
    "voice_coach.visual_runtime_pipeline_identity.v3"
)
P6_AUTOMATED_PROMOTION_RECEIPT_SCHEMA_VERSION = (
    "voice_coach.p6_automated_promotion_receipt.v4"
)
P8_AUTOMATED_PROMOTION_RECEIPT_SCHEMA_VERSION = (
    "voice_coach.p8_automated_promotion_receipt.v4"
)
VISUAL_RUNTIME_BUNDLE_SCHEMA_VERSION = "voice_coach.visual_runtime_bundle.v2"
AUTOMATED_VISUAL_RUNTIME_ELIGIBILITY_RECEIPT_SCHEMA_VERSION = (
    "voice_coach.automated_visual_runtime_eligibility_receipt.v2"
)
ATTEMPT_VISUAL_RELIABILITY_RECEIPT_SCHEMA_VERSION = (
    "voice_coach.attempt_visual_reliability_receipt.v3"
)

RUNTIME_VISUAL_CLAIM_POLICY_SCHEMA_VERSION = "voice_coach.visual_claim_policy.v1"
RUNTIME_VISUAL_DETECTION_SCHEMA_VERSION = "voice_coach.runtime_visual_detection.v3"
RUNTIME_VIDEO_QC_DECISION_SCHEMA_VERSION = (
    "voice_coach.runtime_video_qc_decision.v2"
)
RUNTIME_AV_SYNC_DECISION_SCHEMA_VERSION = (
    "voice_coach.runtime_av_sync_decision.v2"
)
P6_RUNTIME_EVIDENCE_BUNDLE_SCHEMA_VERSION = (
    "voice_coach.p6_runtime_evidence_bundle.v1"
)

_P6_SOURCE_DATASET_ID = "aihub_68"
_P8_SOURCE_DATASET_ID = "aihub_538"
_P6_PROMOTION_STATUS = "geometry_and_phone_reference_calibrated"
_P8_PROMOTION_STATUS = "locked_evaluation_zero_violations"
_RUNTIME_ELIGIBILITY_STATUS = "eligible"
_ATTEMPT_RELIABILITY_STATUS = "eligible_for_visual_supplement"
_REQUIRED_FORBIDDEN_IDENTIFIER_TOKENS = frozenset(
    {"airflow", "diagnosis", "glottis", "medical", "tongue", "velum"}
)


def _require_safe_identifier_tuple(value: object, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise VisualFeedbackContractError(f"{name} must be an array")
    result = tuple(
        require_safe_identifier(item, f"{name}[]") for item in value
    )
    if len(set(result)) != len(result):
        raise VisualFeedbackContractError(f"{name} must not contain duplicates")
    return result


def _validate_self_digest(
    document: Mapping[str, object], digest_field: str, name: str
) -> str:
    digest = require_sha256(document[digest_field], f"{name}.{digest_field}")
    unsigned = {key: value for key, value in document.items() if key != digest_field}
    if digest != sha256_document(unsigned):
        raise VisualFeedbackContractError(f"{name} digest differs")
    return digest


def media_capture_contract_sha256(contract_id: str) -> str:
    """Digest a media/container contract independently from visual observation."""

    identifier = require_safe_identifier(contract_id, "media_capture_contract_id")
    return sha256_document(
        {
            "schema_version": "voice_coach.media_capture_contract_reference.v1",
            "contract_id": identifier,
        }
    )


def visual_observation_contract_sha256(contract_id: str) -> str:
    """Digest a visible-lip observation contract independently from admission."""

    identifier = require_safe_identifier(
        contract_id, "visual_observation_contract_id"
    )
    return sha256_document(
        {
            "schema_version": "voice_coach.visual_observation_contract_reference.v1",
            "contract_id": identifier,
        }
    )


@dataclass(frozen=True, slots=True)
class VisualRuntimePipelineIdentity:
    """Digest-pinned identity for every visual runtime transformation."""

    tracker_revision: str
    tracker_sha256: str
    tracker_adapter_revision: str
    tracker_adapter_sha256: str
    decoder_revision: str
    decoder_sha256: str
    landmark_model_revision: str
    landmark_model_sha256: str
    normalizer_revision: str
    normalizer_sha256: str
    cue_extractor_revision: str
    cue_extractor_sha256: str
    video_qc_measurement_revision: str
    video_qc_measurement_policy_sha256: str
    video_qc_measurement_code_sha256: str
    video_qc_decision_policy_revision: str
    video_qc_decision_policy_sha256: str
    video_qc_decision_code_sha256: str
    av_sync_measurement_revision: str
    av_sync_measurement_policy_sha256: str
    av_sync_measurement_code_sha256: str
    av_sync_decision_policy_revision: str
    av_sync_decision_policy_sha256: str
    av_sync_decision_code_sha256: str
    phone_window_code_revision: str
    phone_window_code_sha256: str
    phone_window_policy_revision: str
    phone_window_policy_sha256: str
    media_capture_contract_id: str
    media_capture_contract_sha256: str
    visual_observation_contract_id: str
    visual_observation_contract_sha256: str
    population_id_sha256: str
    context_contract_sha256: str
    identity_sha256: str
    schema_version: str = VISUAL_RUNTIME_PIPELINE_IDENTITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != VISUAL_RUNTIME_PIPELINE_IDENTITY_SCHEMA_VERSION:
            raise VisualFeedbackContractError(
                "visual runtime pipeline identity schema differs"
            )
        for name in (
            "tracker_revision",
            "tracker_adapter_revision",
            "decoder_revision",
            "landmark_model_revision",
            "normalizer_revision",
            "cue_extractor_revision",
            "video_qc_measurement_revision",
            "video_qc_decision_policy_revision",
            "av_sync_measurement_revision",
            "av_sync_decision_policy_revision",
            "phone_window_code_revision",
            "phone_window_policy_revision",
            "media_capture_contract_id",
            "visual_observation_contract_id",
        ):
            require_safe_identifier(getattr(self, name), name)
        for name in (
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
            "media_capture_contract_sha256",
            "visual_observation_contract_sha256",
            "population_id_sha256",
            "context_contract_sha256",
            "identity_sha256",
        ):
            require_sha256(getattr(self, name), name)
        if self.media_capture_contract_sha256 != media_capture_contract_sha256(
            self.media_capture_contract_id
        ):
            raise VisualFeedbackContractError(
                "media capture contract digest differs"
            )
        if (
            self.visual_observation_contract_sha256
            != visual_observation_contract_sha256(
                self.visual_observation_contract_id
            )
        ):
            raise VisualFeedbackContractError(
                "visual observation contract digest differs"
            )
        if self.identity_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError(
                "visual runtime pipeline identity digest differs"
            )

    @classmethod
    def issue(
        cls,
        *,
        tracker_revision: str,
        tracker_sha256: str,
        tracker_adapter_revision: str,
        tracker_adapter_sha256: str,
        decoder_revision: str,
        decoder_sha256: str,
        landmark_model_revision: str,
        landmark_model_sha256: str,
        normalizer_revision: str,
        normalizer_sha256: str,
        cue_extractor_revision: str,
        cue_extractor_sha256: str,
        video_qc_measurement_revision: str,
        video_qc_measurement_policy_sha256: str,
        video_qc_measurement_code_sha256: str,
        video_qc_decision_policy_revision: str,
        video_qc_decision_policy_sha256: str,
        video_qc_decision_code_sha256: str,
        av_sync_measurement_revision: str,
        av_sync_measurement_policy_sha256: str,
        av_sync_measurement_code_sha256: str,
        av_sync_decision_policy_revision: str,
        av_sync_decision_policy_sha256: str,
        av_sync_decision_code_sha256: str,
        phone_window_code_revision: str,
        phone_window_code_sha256: str,
        phone_window_policy_revision: str,
        phone_window_policy_sha256: str,
        media_capture_contract_id: str,
        visual_observation_contract_id: str,
        population_id_sha256: str,
        context_contract_sha256: str,
    ) -> VisualRuntimePipelineIdentity:
        unsigned = {
            "schema_version": VISUAL_RUNTIME_PIPELINE_IDENTITY_SCHEMA_VERSION,
            "tracker_revision": tracker_revision,
            "tracker_sha256": tracker_sha256,
            "tracker_adapter_revision": tracker_adapter_revision,
            "tracker_adapter_sha256": tracker_adapter_sha256,
            "decoder_revision": decoder_revision,
            "decoder_sha256": decoder_sha256,
            "landmark_model_revision": landmark_model_revision,
            "landmark_model_sha256": landmark_model_sha256,
            "normalizer_revision": normalizer_revision,
            "normalizer_sha256": normalizer_sha256,
            "cue_extractor_revision": cue_extractor_revision,
            "cue_extractor_sha256": cue_extractor_sha256,
            "video_qc_measurement_revision": video_qc_measurement_revision,
            "video_qc_measurement_policy_sha256": (
                video_qc_measurement_policy_sha256
            ),
            "video_qc_measurement_code_sha256": video_qc_measurement_code_sha256,
            "video_qc_decision_policy_revision": (
                video_qc_decision_policy_revision
            ),
            "video_qc_decision_policy_sha256": (
                video_qc_decision_policy_sha256
            ),
            "video_qc_decision_code_sha256": video_qc_decision_code_sha256,
            "av_sync_measurement_revision": av_sync_measurement_revision,
            "av_sync_measurement_policy_sha256": (
                av_sync_measurement_policy_sha256
            ),
            "av_sync_measurement_code_sha256": av_sync_measurement_code_sha256,
            "av_sync_decision_policy_revision": (
                av_sync_decision_policy_revision
            ),
            "av_sync_decision_policy_sha256": av_sync_decision_policy_sha256,
            "av_sync_decision_code_sha256": av_sync_decision_code_sha256,
            "phone_window_code_revision": phone_window_code_revision,
            "phone_window_code_sha256": phone_window_code_sha256,
            "phone_window_policy_revision": phone_window_policy_revision,
            "phone_window_policy_sha256": phone_window_policy_sha256,
            "media_capture_contract_id": media_capture_contract_id,
            "media_capture_contract_sha256": media_capture_contract_sha256(
                media_capture_contract_id
            ),
            "visual_observation_contract_id": visual_observation_contract_id,
            "visual_observation_contract_sha256": (
                visual_observation_contract_sha256(
                    visual_observation_contract_id
                )
            ),
            "population_id_sha256": population_id_sha256,
            "context_contract_sha256": context_contract_sha256,
        }
        return cls(
            tracker_revision=tracker_revision,
            tracker_sha256=tracker_sha256,
            tracker_adapter_revision=tracker_adapter_revision,
            tracker_adapter_sha256=tracker_adapter_sha256,
            decoder_revision=decoder_revision,
            decoder_sha256=decoder_sha256,
            landmark_model_revision=landmark_model_revision,
            landmark_model_sha256=landmark_model_sha256,
            normalizer_revision=normalizer_revision,
            normalizer_sha256=normalizer_sha256,
            cue_extractor_revision=cue_extractor_revision,
            cue_extractor_sha256=cue_extractor_sha256,
            video_qc_measurement_revision=video_qc_measurement_revision,
            video_qc_measurement_policy_sha256=(
                video_qc_measurement_policy_sha256
            ),
            video_qc_measurement_code_sha256=video_qc_measurement_code_sha256,
            video_qc_decision_policy_revision=(
                video_qc_decision_policy_revision
            ),
            video_qc_decision_policy_sha256=video_qc_decision_policy_sha256,
            video_qc_decision_code_sha256=video_qc_decision_code_sha256,
            av_sync_measurement_revision=av_sync_measurement_revision,
            av_sync_measurement_policy_sha256=av_sync_measurement_policy_sha256,
            av_sync_measurement_code_sha256=av_sync_measurement_code_sha256,
            av_sync_decision_policy_revision=av_sync_decision_policy_revision,
            av_sync_decision_policy_sha256=av_sync_decision_policy_sha256,
            av_sync_decision_code_sha256=av_sync_decision_code_sha256,
            phone_window_code_revision=phone_window_code_revision,
            phone_window_code_sha256=phone_window_code_sha256,
            phone_window_policy_revision=phone_window_policy_revision,
            phone_window_policy_sha256=phone_window_policy_sha256,
            media_capture_contract_id=media_capture_contract_id,
            media_capture_contract_sha256=unsigned[
                "media_capture_contract_sha256"
            ],
            visual_observation_contract_id=visual_observation_contract_id,
            visual_observation_contract_sha256=unsigned[
                "visual_observation_contract_sha256"
            ],
            population_id_sha256=population_id_sha256,
            context_contract_sha256=context_contract_sha256,
            identity_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "tracker_revision": self.tracker_revision,
            "tracker_sha256": self.tracker_sha256,
            "tracker_adapter_revision": self.tracker_adapter_revision,
            "tracker_adapter_sha256": self.tracker_adapter_sha256,
            "decoder_revision": self.decoder_revision,
            "decoder_sha256": self.decoder_sha256,
            "landmark_model_revision": self.landmark_model_revision,
            "landmark_model_sha256": self.landmark_model_sha256,
            "normalizer_revision": self.normalizer_revision,
            "normalizer_sha256": self.normalizer_sha256,
            "cue_extractor_revision": self.cue_extractor_revision,
            "cue_extractor_sha256": self.cue_extractor_sha256,
            "video_qc_measurement_revision": self.video_qc_measurement_revision,
            "video_qc_measurement_policy_sha256": (
                self.video_qc_measurement_policy_sha256
            ),
            "video_qc_measurement_code_sha256": (
                self.video_qc_measurement_code_sha256
            ),
            "video_qc_decision_policy_revision": (
                self.video_qc_decision_policy_revision
            ),
            "video_qc_decision_policy_sha256": (
                self.video_qc_decision_policy_sha256
            ),
            "video_qc_decision_code_sha256": self.video_qc_decision_code_sha256,
            "av_sync_measurement_revision": self.av_sync_measurement_revision,
            "av_sync_measurement_policy_sha256": (
                self.av_sync_measurement_policy_sha256
            ),
            "av_sync_measurement_code_sha256": (
                self.av_sync_measurement_code_sha256
            ),
            "av_sync_decision_policy_revision": (
                self.av_sync_decision_policy_revision
            ),
            "av_sync_decision_policy_sha256": (
                self.av_sync_decision_policy_sha256
            ),
            "av_sync_decision_code_sha256": self.av_sync_decision_code_sha256,
            "phone_window_code_revision": self.phone_window_code_revision,
            "phone_window_code_sha256": self.phone_window_code_sha256,
            "phone_window_policy_revision": self.phone_window_policy_revision,
            "phone_window_policy_sha256": self.phone_window_policy_sha256,
            "media_capture_contract_id": self.media_capture_contract_id,
            "media_capture_contract_sha256": self.media_capture_contract_sha256,
            "visual_observation_contract_id": self.visual_observation_contract_id,
            "visual_observation_contract_sha256": (
                self.visual_observation_contract_sha256
            ),
            "population_id_sha256": self.population_id_sha256,
            "context_contract_sha256": self.context_contract_sha256,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "identity_sha256": self.identity_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> VisualRuntimePipelineIdentity:
        document = require_mapping(value, "visual_runtime_pipeline_identity")
        require_exact_keys(
            document,
            {
                "schema_version",
                "tracker_revision",
                "tracker_sha256",
                "tracker_adapter_revision",
                "tracker_adapter_sha256",
                "decoder_revision",
                "decoder_sha256",
                "landmark_model_revision",
                "landmark_model_sha256",
                "normalizer_revision",
                "normalizer_sha256",
                "cue_extractor_revision",
                "cue_extractor_sha256",
                "video_qc_measurement_revision",
                "video_qc_measurement_policy_sha256",
                "video_qc_measurement_code_sha256",
                "video_qc_decision_policy_revision",
                "video_qc_decision_policy_sha256",
                "video_qc_decision_code_sha256",
                "av_sync_measurement_revision",
                "av_sync_measurement_policy_sha256",
                "av_sync_measurement_code_sha256",
                "av_sync_decision_policy_revision",
                "av_sync_decision_policy_sha256",
                "av_sync_decision_code_sha256",
                "phone_window_code_revision",
                "phone_window_code_sha256",
                "phone_window_policy_revision",
                "phone_window_policy_sha256",
                "media_capture_contract_id",
                "media_capture_contract_sha256",
                "visual_observation_contract_id",
                "visual_observation_contract_sha256",
                "population_id_sha256",
                "context_contract_sha256",
                "identity_sha256",
            },
            "visual_runtime_pipeline_identity",
        )
        string_fields = (
            "schema_version",
            "tracker_revision",
            "tracker_adapter_revision",
            "decoder_revision",
            "landmark_model_revision",
            "normalizer_revision",
            "cue_extractor_revision",
            "video_qc_measurement_revision",
            "video_qc_decision_policy_revision",
            "av_sync_measurement_revision",
            "av_sync_decision_policy_revision",
            "phone_window_code_revision",
            "phone_window_policy_revision",
            "media_capture_contract_id",
            "visual_observation_contract_id",
        )
        digest_fields = (
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
            "media_capture_contract_sha256",
            "visual_observation_contract_sha256",
            "population_id_sha256",
            "context_contract_sha256",
            "identity_sha256",
        )
        values: dict[str, str] = {
            name: require_non_empty_string(
                document[name], f"visual_runtime_pipeline_identity.{name}"
            )
            for name in string_fields
        }
        values.update(
            {
                name: require_sha256(
                    document[name], f"visual_runtime_pipeline_identity.{name}"
                )
                for name in digest_fields
            }
        )
        return cls(**values)


@dataclass(frozen=True, slots=True)
class AutomatedP6PromotionReceipt:
    """Automated P6 result that is distinct from the non-promotional v1 receipt."""

    source_revision: str
    dataset_manifest_sha256: str
    p6_bundle_sha256: str
    geometry_bundle_sha256: str
    phone_reference_bundle_sha256: str
    phone_reference_reload_receipt_sha256: str
    phone_reference_producer_sha256: str
    runtime_parity_receipt_sha256: str
    geometry_artifact_sha256: str
    geometry_reload_receipt_sha256: str
    development_partition_sha256: str
    calibration_partition_sha256: str
    mapping_sha256: str
    threshold_protocol_sha256: str
    calibration_artifact_sha256: str
    catalog_sha256: str
    policy_sha256: str
    decision_policy_evidence_receipt_sha256: str
    pipeline_identity_sha256: str
    media_capture_contract_id: str
    media_capture_contract_sha256: str
    visual_observation_contract_id: str
    visual_observation_contract_sha256: str
    population_id_sha256: str
    context_contract_sha256: str
    actor_disjoint: bool
    attempt_disjoint: bool
    metric_gate_passed: bool
    fresh_process_reload_passed: bool
    status: str
    reason_codes: tuple[str, ...]
    receipt_sha256: str
    source_dataset_id: str = _P6_SOURCE_DATASET_ID
    schema_version: str = P6_AUTOMATED_PROMOTION_RECEIPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P6_AUTOMATED_PROMOTION_RECEIPT_SCHEMA_VERSION:
            raise VisualFeedbackContractError(
                "P6 automated promotion receipt schema differs"
            )
        if self.source_dataset_id != _P6_SOURCE_DATASET_ID:
            raise VisualFeedbackContractError("P6 automated source dataset differs")
        require_safe_identifier(self.source_revision, "source_revision")
        require_safe_identifier(
            self.media_capture_contract_id, "media_capture_contract_id"
        )
        require_safe_identifier(
            self.visual_observation_contract_id,
            "visual_observation_contract_id",
        )
        for name in (
            "dataset_manifest_sha256",
            "p6_bundle_sha256",
            "geometry_bundle_sha256",
            "phone_reference_bundle_sha256",
            "phone_reference_reload_receipt_sha256",
            "phone_reference_producer_sha256",
            "runtime_parity_receipt_sha256",
            "geometry_artifact_sha256",
            "geometry_reload_receipt_sha256",
            "development_partition_sha256",
            "calibration_partition_sha256",
            "mapping_sha256",
            "threshold_protocol_sha256",
            "calibration_artifact_sha256",
            "catalog_sha256",
            "policy_sha256",
            "decision_policy_evidence_receipt_sha256",
            "pipeline_identity_sha256",
            "media_capture_contract_sha256",
            "visual_observation_contract_sha256",
            "population_id_sha256",
            "context_contract_sha256",
            "receipt_sha256",
        ):
            require_sha256(getattr(self, name), name)
        if self.media_capture_contract_sha256 != media_capture_contract_sha256(
            self.media_capture_contract_id
        ):
            raise VisualFeedbackContractError("P6 media capture digest differs")
        if (
            self.visual_observation_contract_sha256
            != visual_observation_contract_sha256(
                self.visual_observation_contract_id
            )
        ):
            raise VisualFeedbackContractError(
                "P6 visual observation digest differs"
            )
        if self.p6_bundle_sha256 != sha256_document(
            {
                "schema_version": P6_RUNTIME_EVIDENCE_BUNDLE_SCHEMA_VERSION,
                "geometry_bundle_sha256": self.geometry_bundle_sha256,
                "phone_reference_bundle_sha256": (
                    self.phone_reference_bundle_sha256
                ),
            }
        ):
            raise VisualFeedbackContractError("P6 aggregate evidence digest differs")
        for name in (
            "actor_disjoint",
            "attempt_disjoint",
            "metric_gate_passed",
            "fresh_process_reload_passed",
        ):
            if require_boolean(getattr(self, name), name) is not True:
                raise VisualFeedbackContractError(
                    "P6 automated promotion evidence is incomplete"
                )
        if self.status != _P6_PROMOTION_STATUS:
            raise VisualFeedbackContractError("P6 automated promotion status differs")
        if self.reason_codes:
            raise VisualFeedbackContractError(
                "promoted P6 receipt cannot contain blocking reasons"
            )
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError(
                "P6 automated promotion receipt digest differs"
            )

    @classmethod
    def _issue_from_validated_evidence(
        cls,
        *,
        source_revision: str,
        dataset_manifest_sha256: str,
        geometry_bundle_sha256: str,
        phone_reference_bundle_sha256: str,
        phone_reference_reload_receipt_sha256: str,
        phone_reference_producer_sha256: str,
        runtime_parity_receipt_sha256: str,
        geometry_artifact_sha256: str,
        geometry_reload_receipt_sha256: str,
        development_partition_sha256: str,
        calibration_partition_sha256: str,
        mapping_sha256: str,
        threshold_protocol_sha256: str,
        calibration_artifact_sha256: str,
        catalog_sha256: str,
        policy_sha256: str,
        decision_policy_evidence_receipt_sha256: str,
        pipeline_identity: VisualRuntimePipelineIdentity,
    ) -> AutomatedP6PromotionReceipt:
        """Issue only after the P6 producer validator has checked real evidence."""

        p6_bundle_sha256 = sha256_document(
            {
                "schema_version": P6_RUNTIME_EVIDENCE_BUNDLE_SCHEMA_VERSION,
                "geometry_bundle_sha256": geometry_bundle_sha256,
                "phone_reference_bundle_sha256": phone_reference_bundle_sha256,
            }
        )
        unsigned = {
            "schema_version": P6_AUTOMATED_PROMOTION_RECEIPT_SCHEMA_VERSION,
            "source_dataset_id": _P6_SOURCE_DATASET_ID,
            "source_revision": source_revision,
            "dataset_manifest_sha256": dataset_manifest_sha256,
            "p6_bundle_sha256": p6_bundle_sha256,
            "geometry_bundle_sha256": geometry_bundle_sha256,
            "phone_reference_bundle_sha256": phone_reference_bundle_sha256,
            "phone_reference_reload_receipt_sha256": (
                phone_reference_reload_receipt_sha256
            ),
            "phone_reference_producer_sha256": phone_reference_producer_sha256,
            "runtime_parity_receipt_sha256": runtime_parity_receipt_sha256,
            "geometry_artifact_sha256": geometry_artifact_sha256,
            "geometry_reload_receipt_sha256": geometry_reload_receipt_sha256,
            "development_partition_sha256": development_partition_sha256,
            "calibration_partition_sha256": calibration_partition_sha256,
            "mapping_sha256": mapping_sha256,
            "threshold_protocol_sha256": threshold_protocol_sha256,
            "calibration_artifact_sha256": calibration_artifact_sha256,
            "catalog_sha256": catalog_sha256,
            "policy_sha256": policy_sha256,
            "decision_policy_evidence_receipt_sha256": (
                decision_policy_evidence_receipt_sha256
            ),
            "pipeline_identity_sha256": pipeline_identity.identity_sha256,
            "media_capture_contract_id": (
                pipeline_identity.media_capture_contract_id
            ),
            "media_capture_contract_sha256": (
                pipeline_identity.media_capture_contract_sha256
            ),
            "visual_observation_contract_id": (
                pipeline_identity.visual_observation_contract_id
            ),
            "visual_observation_contract_sha256": (
                pipeline_identity.visual_observation_contract_sha256
            ),
            "population_id_sha256": pipeline_identity.population_id_sha256,
            "context_contract_sha256": pipeline_identity.context_contract_sha256,
            "actor_disjoint": True,
            "attempt_disjoint": True,
            "metric_gate_passed": True,
            "fresh_process_reload_passed": True,
            "status": _P6_PROMOTION_STATUS,
            "reason_codes": [],
        }
        return cls(
            source_revision=source_revision,
            dataset_manifest_sha256=dataset_manifest_sha256,
            p6_bundle_sha256=p6_bundle_sha256,
            geometry_bundle_sha256=geometry_bundle_sha256,
            phone_reference_bundle_sha256=phone_reference_bundle_sha256,
            phone_reference_reload_receipt_sha256=(
                phone_reference_reload_receipt_sha256
            ),
            phone_reference_producer_sha256=phone_reference_producer_sha256,
            runtime_parity_receipt_sha256=runtime_parity_receipt_sha256,
            geometry_artifact_sha256=geometry_artifact_sha256,
            geometry_reload_receipt_sha256=geometry_reload_receipt_sha256,
            development_partition_sha256=development_partition_sha256,
            calibration_partition_sha256=calibration_partition_sha256,
            mapping_sha256=mapping_sha256,
            threshold_protocol_sha256=threshold_protocol_sha256,
            calibration_artifact_sha256=calibration_artifact_sha256,
            catalog_sha256=catalog_sha256,
            policy_sha256=policy_sha256,
            decision_policy_evidence_receipt_sha256=(
                decision_policy_evidence_receipt_sha256
            ),
            pipeline_identity_sha256=pipeline_identity.identity_sha256,
            media_capture_contract_id=(
                pipeline_identity.media_capture_contract_id
            ),
            media_capture_contract_sha256=(
                pipeline_identity.media_capture_contract_sha256
            ),
            visual_observation_contract_id=(
                pipeline_identity.visual_observation_contract_id
            ),
            visual_observation_contract_sha256=(
                pipeline_identity.visual_observation_contract_sha256
            ),
            population_id_sha256=pipeline_identity.population_id_sha256,
            context_contract_sha256=pipeline_identity.context_contract_sha256,
            actor_disjoint=True,
            attempt_disjoint=True,
            metric_gate_passed=True,
            fresh_process_reload_passed=True,
            status=_P6_PROMOTION_STATUS,
            reason_codes=(),
            receipt_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_dataset_id": self.source_dataset_id,
            "source_revision": self.source_revision,
            "dataset_manifest_sha256": self.dataset_manifest_sha256,
            "p6_bundle_sha256": self.p6_bundle_sha256,
            "geometry_bundle_sha256": self.geometry_bundle_sha256,
            "phone_reference_bundle_sha256": self.phone_reference_bundle_sha256,
            "phone_reference_reload_receipt_sha256": (
                self.phone_reference_reload_receipt_sha256
            ),
            "phone_reference_producer_sha256": (
                self.phone_reference_producer_sha256
            ),
            "runtime_parity_receipt_sha256": self.runtime_parity_receipt_sha256,
            "geometry_artifact_sha256": self.geometry_artifact_sha256,
            "geometry_reload_receipt_sha256": (
                self.geometry_reload_receipt_sha256
            ),
            "development_partition_sha256": self.development_partition_sha256,
            "calibration_partition_sha256": self.calibration_partition_sha256,
            "mapping_sha256": self.mapping_sha256,
            "threshold_protocol_sha256": self.threshold_protocol_sha256,
            "calibration_artifact_sha256": self.calibration_artifact_sha256,
            "catalog_sha256": self.catalog_sha256,
            "policy_sha256": self.policy_sha256,
            "decision_policy_evidence_receipt_sha256": (
                self.decision_policy_evidence_receipt_sha256
            ),
            "pipeline_identity_sha256": self.pipeline_identity_sha256,
            "media_capture_contract_id": self.media_capture_contract_id,
            "media_capture_contract_sha256": self.media_capture_contract_sha256,
            "visual_observation_contract_id": self.visual_observation_contract_id,
            "visual_observation_contract_sha256": (
                self.visual_observation_contract_sha256
            ),
            "population_id_sha256": self.population_id_sha256,
            "context_contract_sha256": self.context_contract_sha256,
            "actor_disjoint": self.actor_disjoint,
            "attempt_disjoint": self.attempt_disjoint,
            "metric_gate_passed": self.metric_gate_passed,
            "fresh_process_reload_passed": self.fresh_process_reload_passed,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> AutomatedP6PromotionReceipt:
        document = require_mapping(value, "p6_automated_promotion_receipt")
        expected_fields = {
            "schema_version",
            "source_dataset_id",
            "source_revision",
            "dataset_manifest_sha256",
            "p6_bundle_sha256",
            "geometry_bundle_sha256",
            "phone_reference_bundle_sha256",
            "phone_reference_reload_receipt_sha256",
            "phone_reference_producer_sha256",
            "runtime_parity_receipt_sha256",
            "geometry_artifact_sha256",
            "geometry_reload_receipt_sha256",
            "development_partition_sha256",
            "calibration_partition_sha256",
            "mapping_sha256",
            "threshold_protocol_sha256",
            "calibration_artifact_sha256",
            "catalog_sha256",
            "policy_sha256",
            "decision_policy_evidence_receipt_sha256",
            "pipeline_identity_sha256",
            "media_capture_contract_id",
            "media_capture_contract_sha256",
            "visual_observation_contract_id",
            "visual_observation_contract_sha256",
            "population_id_sha256",
            "context_contract_sha256",
            "actor_disjoint",
            "attempt_disjoint",
            "metric_gate_passed",
            "fresh_process_reload_passed",
            "status",
            "reason_codes",
            "receipt_sha256",
        }
        require_exact_keys(document, expected_fields, "p6_automated_promotion_receipt")
        return cls(
            schema_version=require_non_empty_string(
                document["schema_version"],
                "p6_automated_promotion_receipt.schema_version",
            ),
            source_dataset_id=require_safe_identifier(
                document["source_dataset_id"],
                "p6_automated_promotion_receipt.source_dataset_id",
            ),
            source_revision=require_safe_identifier(
                document["source_revision"],
                "p6_automated_promotion_receipt.source_revision",
            ),
            dataset_manifest_sha256=require_sha256(
                document["dataset_manifest_sha256"],
                "p6_automated_promotion_receipt.dataset_manifest_sha256",
            ),
            p6_bundle_sha256=require_sha256(
                document["p6_bundle_sha256"],
                "p6_automated_promotion_receipt.p6_bundle_sha256",
            ),
            geometry_bundle_sha256=require_sha256(
                document["geometry_bundle_sha256"],
                "p6_automated_promotion_receipt.geometry_bundle_sha256",
            ),
            phone_reference_bundle_sha256=require_sha256(
                document["phone_reference_bundle_sha256"],
                "p6_automated_promotion_receipt.phone_reference_bundle_sha256",
            ),
            phone_reference_reload_receipt_sha256=require_sha256(
                document["phone_reference_reload_receipt_sha256"],
                "p6_automated_promotion_receipt.phone_reference_reload_receipt_sha256",
            ),
            phone_reference_producer_sha256=require_sha256(
                document["phone_reference_producer_sha256"],
                "p6_automated_promotion_receipt.phone_reference_producer_sha256",
            ),
            runtime_parity_receipt_sha256=require_sha256(
                document["runtime_parity_receipt_sha256"],
                "p6_automated_promotion_receipt.runtime_parity_receipt_sha256",
            ),
            geometry_artifact_sha256=require_sha256(
                document["geometry_artifact_sha256"],
                "p6_automated_promotion_receipt.geometry_artifact_sha256",
            ),
            geometry_reload_receipt_sha256=require_sha256(
                document["geometry_reload_receipt_sha256"],
                "p6_automated_promotion_receipt.geometry_reload_receipt_sha256",
            ),
            development_partition_sha256=require_sha256(
                document["development_partition_sha256"],
                "p6_automated_promotion_receipt.development_partition_sha256",
            ),
            calibration_partition_sha256=require_sha256(
                document["calibration_partition_sha256"],
                "p6_automated_promotion_receipt.calibration_partition_sha256",
            ),
            mapping_sha256=require_sha256(
                document["mapping_sha256"],
                "p6_automated_promotion_receipt.mapping_sha256",
            ),
            threshold_protocol_sha256=require_sha256(
                document["threshold_protocol_sha256"],
                "p6_automated_promotion_receipt.threshold_protocol_sha256",
            ),
            calibration_artifact_sha256=require_sha256(
                document["calibration_artifact_sha256"],
                "p6_automated_promotion_receipt.calibration_artifact_sha256",
            ),
            catalog_sha256=require_sha256(
                document["catalog_sha256"],
                "p6_automated_promotion_receipt.catalog_sha256",
            ),
            policy_sha256=require_sha256(
                document["policy_sha256"],
                "p6_automated_promotion_receipt.policy_sha256",
            ),
            decision_policy_evidence_receipt_sha256=require_sha256(
                document["decision_policy_evidence_receipt_sha256"],
                "p6_automated_promotion_receipt.decision_policy_evidence_receipt_sha256",
            ),
            pipeline_identity_sha256=require_sha256(
                document["pipeline_identity_sha256"],
                "p6_automated_promotion_receipt.pipeline_identity_sha256",
            ),
            media_capture_contract_id=require_safe_identifier(
                document["media_capture_contract_id"],
                "p6_automated_promotion_receipt.media_capture_contract_id",
            ),
            media_capture_contract_sha256=require_sha256(
                document["media_capture_contract_sha256"],
                "p6_automated_promotion_receipt.media_capture_contract_sha256",
            ),
            visual_observation_contract_id=require_safe_identifier(
                document["visual_observation_contract_id"],
                "p6_automated_promotion_receipt.visual_observation_contract_id",
            ),
            visual_observation_contract_sha256=require_sha256(
                document["visual_observation_contract_sha256"],
                "p6_automated_promotion_receipt.visual_observation_contract_sha256",
            ),
            population_id_sha256=require_sha256(
                document["population_id_sha256"],
                "p6_automated_promotion_receipt.population_id_sha256",
            ),
            context_contract_sha256=require_sha256(
                document["context_contract_sha256"],
                "p6_automated_promotion_receipt.context_contract_sha256",
            ),
            actor_disjoint=require_boolean(
                document["actor_disjoint"],
                "p6_automated_promotion_receipt.actor_disjoint",
            ),
            attempt_disjoint=require_boolean(
                document["attempt_disjoint"],
                "p6_automated_promotion_receipt.attempt_disjoint",
            ),
            metric_gate_passed=require_boolean(
                document["metric_gate_passed"],
                "p6_automated_promotion_receipt.metric_gate_passed",
            ),
            fresh_process_reload_passed=require_boolean(
                document["fresh_process_reload_passed"],
                "p6_automated_promotion_receipt.fresh_process_reload_passed",
            ),
            status=require_safe_identifier(
                document["status"], "p6_automated_promotion_receipt.status"
            ),
            reason_codes=_require_safe_identifier_tuple(
                document["reason_codes"],
                "p6_automated_promotion_receipt.reason_codes",
            ),
            receipt_sha256=require_sha256(
                document["receipt_sha256"],
                "p6_automated_promotion_receipt.receipt_sha256",
            ),
        )


@dataclass(frozen=True, slots=True)
class AutomatedP8PromotionReceipt:
    """Automated locked evaluation and zero-violation claim replay result."""

    locked_source_revision: str
    locked_manifest_sha256: str
    read_zero_seal_sha256: str
    locked_evaluation_report_sha256: str
    aggregate_metrics_sha256: str
    zero_violation_replay_receipt_sha256: str
    p8_bundle_sha256: str
    p6_promotion_receipt_sha256: str
    calibration_artifact_sha256: str
    catalog_sha256: str
    policy_sha256: str
    pipeline_identity_sha256: str
    media_capture_contract_id: str
    media_capture_contract_sha256: str
    visual_observation_contract_id: str
    visual_observation_contract_sha256: str
    population_id_sha256: str
    context_contract_sha256: str
    partition_sealed_before_read: bool
    partition_disjoint: bool
    evaluation_completed: bool
    claim_policy_replay_completed: bool
    admitted_item_count: int
    paired_item_count: int
    evaluated_item_count: int
    evaluation_exclusion_count: int
    evaluation_exclusion_policy: str
    qc_decision_lineage_valid_item_count: int
    ready_sync_decision_lineage_valid_case_count: int
    case_count: int
    ready_case_count: int
    violation_count: int
    status: str
    reason_codes: tuple[str, ...]
    receipt_sha256: str
    locked_source_dataset_id: str = _P8_SOURCE_DATASET_ID
    schema_version: str = P8_AUTOMATED_PROMOTION_RECEIPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P8_AUTOMATED_PROMOTION_RECEIPT_SCHEMA_VERSION:
            raise VisualFeedbackContractError(
                "P8 automated promotion receipt schema differs"
            )
        if self.locked_source_dataset_id != _P8_SOURCE_DATASET_ID:
            raise VisualFeedbackContractError("P8 locked source dataset differs")
        require_safe_identifier(self.locked_source_revision, "locked_source_revision")
        require_safe_identifier(
            self.media_capture_contract_id, "media_capture_contract_id"
        )
        require_p8_evaluation_exclusion_policy(
            self.evaluation_exclusion_policy
        )
        require_safe_identifier(
            self.visual_observation_contract_id,
            "visual_observation_contract_id",
        )
        for name in (
            "locked_manifest_sha256",
            "read_zero_seal_sha256",
            "locked_evaluation_report_sha256",
            "aggregate_metrics_sha256",
            "zero_violation_replay_receipt_sha256",
            "p8_bundle_sha256",
            "p6_promotion_receipt_sha256",
            "calibration_artifact_sha256",
            "catalog_sha256",
            "policy_sha256",
            "pipeline_identity_sha256",
            "media_capture_contract_sha256",
            "visual_observation_contract_sha256",
            "population_id_sha256",
            "context_contract_sha256",
            "receipt_sha256",
        ):
            require_sha256(getattr(self, name), name)
        if self.media_capture_contract_sha256 != media_capture_contract_sha256(
            self.media_capture_contract_id
        ):
            raise VisualFeedbackContractError("P8 media capture digest differs")
        if (
            self.visual_observation_contract_sha256
            != visual_observation_contract_sha256(
                self.visual_observation_contract_id
            )
        ):
            raise VisualFeedbackContractError(
                "P8 visual observation digest differs"
            )
        if self.read_zero_seal_sha256 == self.locked_manifest_sha256:
            raise VisualFeedbackContractError(
                "P8 read-zero seal and locked manifest digests must differ"
            )
        if self.locked_manifest_sha256 == P8_LOCKED_SOURCE_LISTING_SHA256:
            raise VisualFeedbackContractError(
                "P8 promotion must bind the actual locked source manifest"
            )
        for name in (
            "partition_sealed_before_read",
            "partition_disjoint",
            "evaluation_completed",
            "claim_policy_replay_completed",
        ):
            if require_boolean(getattr(self, name), name) is not True:
                raise VisualFeedbackContractError(
                    "P8 automated promotion evidence is incomplete"
                )
        admitted, paired, evaluated, exclusions = (
            validate_p8_evaluation_completeness(
                admitted_item_count=self.admitted_item_count,
                paired_item_count=self.paired_item_count,
                evaluated_item_count=self.evaluated_item_count,
                evaluation_exclusion_count=self.evaluation_exclusion_count,
                allow_empty=False,
            )
        )
        if exclusions != 0 or admitted != paired or paired != evaluated:
            raise VisualFeedbackContractError(
                "P8 promotion requires complete zero-exclusion evaluation"
            )
        if require_integer(self.case_count, "case_count", minimum=1) != evaluated:
            raise VisualFeedbackContractError(
                "P8 promotion case count differs from evaluated count"
            )
        if (
            require_integer(
                self.qc_decision_lineage_valid_item_count,
                "qc_decision_lineage_valid_item_count",
                minimum=0,
            )
            != evaluated
        ):
            raise VisualFeedbackContractError(
                "P8 QC decision lineage count differs"
            )
        ready = require_integer(
            self.ready_case_count, "ready_case_count", minimum=0
        )
        if ready > evaluated or require_integer(
            self.ready_sync_decision_lineage_valid_case_count,
            "ready_sync_decision_lineage_valid_case_count",
            minimum=0,
        ) != ready:
            raise VisualFeedbackContractError(
                "P8 ready sync decision lineage count differs"
            )
        if require_integer(self.violation_count, "violation_count", minimum=0) != 0:
            raise VisualFeedbackContractError(
                "P8 promotion requires zero claim-policy violations"
            )
        if self.status != _P8_PROMOTION_STATUS:
            raise VisualFeedbackContractError("P8 automated promotion status differs")
        if self.reason_codes:
            raise VisualFeedbackContractError(
                "promoted P8 receipt cannot contain blocking reasons"
            )
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError(
                "P8 automated promotion receipt digest differs"
            )

    @classmethod
    def _issue_from_persisted_locked_evaluation(
        cls,
        *,
        locked_source_revision: str,
        locked_manifest_sha256: str,
        read_zero_seal_sha256: str,
        locked_evaluation_report_sha256: str,
        aggregate_metrics_sha256: str,
        zero_violation_replay_receipt_sha256: str,
        p8_bundle_sha256: str,
        p6_promotion_receipt_sha256: str,
        calibration_artifact_sha256: str,
        catalog_sha256: str,
        policy_sha256: str,
        pipeline_identity: VisualRuntimePipelineIdentity,
        admitted_item_count: int,
        paired_item_count: int,
        evaluated_item_count: int,
        evaluation_exclusion_count: int,
        evaluation_exclusion_policy: str,
        qc_decision_lineage_valid_item_count: int,
        ready_sync_decision_lineage_valid_case_count: int,
        case_count: int,
        ready_case_count: int,
    ) -> AutomatedP8PromotionReceipt:
        """Issue only after the persisted P8 producer validator has passed."""

        unsigned = {
            "schema_version": P8_AUTOMATED_PROMOTION_RECEIPT_SCHEMA_VERSION,
            "locked_source_dataset_id": _P8_SOURCE_DATASET_ID,
            "locked_source_revision": locked_source_revision,
            "locked_manifest_sha256": locked_manifest_sha256,
            "read_zero_seal_sha256": read_zero_seal_sha256,
            "locked_evaluation_report_sha256": locked_evaluation_report_sha256,
            "aggregate_metrics_sha256": aggregate_metrics_sha256,
            "zero_violation_replay_receipt_sha256": (
                zero_violation_replay_receipt_sha256
            ),
            "p8_bundle_sha256": p8_bundle_sha256,
            "p6_promotion_receipt_sha256": p6_promotion_receipt_sha256,
            "calibration_artifact_sha256": calibration_artifact_sha256,
            "catalog_sha256": catalog_sha256,
            "policy_sha256": policy_sha256,
            "pipeline_identity_sha256": pipeline_identity.identity_sha256,
            "media_capture_contract_id": (
                pipeline_identity.media_capture_contract_id
            ),
            "media_capture_contract_sha256": (
                pipeline_identity.media_capture_contract_sha256
            ),
            "visual_observation_contract_id": (
                pipeline_identity.visual_observation_contract_id
            ),
            "visual_observation_contract_sha256": (
                pipeline_identity.visual_observation_contract_sha256
            ),
            "population_id_sha256": pipeline_identity.population_id_sha256,
            "context_contract_sha256": pipeline_identity.context_contract_sha256,
            "partition_sealed_before_read": True,
            "partition_disjoint": True,
            "evaluation_completed": True,
            "claim_policy_replay_completed": True,
            "admitted_item_count": admitted_item_count,
            "paired_item_count": paired_item_count,
            "evaluated_item_count": evaluated_item_count,
            "evaluation_exclusion_count": evaluation_exclusion_count,
            "evaluation_exclusion_policy": evaluation_exclusion_policy,
            "qc_decision_lineage_valid_item_count": (
                qc_decision_lineage_valid_item_count
            ),
            "ready_sync_decision_lineage_valid_case_count": (
                ready_sync_decision_lineage_valid_case_count
            ),
            "case_count": case_count,
            "ready_case_count": ready_case_count,
            "violation_count": 0,
            "status": _P8_PROMOTION_STATUS,
            "reason_codes": [],
        }
        return cls(
            locked_source_revision=locked_source_revision,
            locked_manifest_sha256=locked_manifest_sha256,
            read_zero_seal_sha256=read_zero_seal_sha256,
            locked_evaluation_report_sha256=locked_evaluation_report_sha256,
            aggregate_metrics_sha256=aggregate_metrics_sha256,
            zero_violation_replay_receipt_sha256=(
                zero_violation_replay_receipt_sha256
            ),
            p8_bundle_sha256=p8_bundle_sha256,
            p6_promotion_receipt_sha256=p6_promotion_receipt_sha256,
            calibration_artifact_sha256=calibration_artifact_sha256,
            catalog_sha256=catalog_sha256,
            policy_sha256=policy_sha256,
            pipeline_identity_sha256=pipeline_identity.identity_sha256,
            media_capture_contract_id=(
                pipeline_identity.media_capture_contract_id
            ),
            media_capture_contract_sha256=(
                pipeline_identity.media_capture_contract_sha256
            ),
            visual_observation_contract_id=(
                pipeline_identity.visual_observation_contract_id
            ),
            visual_observation_contract_sha256=(
                pipeline_identity.visual_observation_contract_sha256
            ),
            population_id_sha256=pipeline_identity.population_id_sha256,
            context_contract_sha256=pipeline_identity.context_contract_sha256,
            partition_sealed_before_read=True,
            partition_disjoint=True,
            evaluation_completed=True,
            claim_policy_replay_completed=True,
            admitted_item_count=admitted_item_count,
            paired_item_count=paired_item_count,
            evaluated_item_count=evaluated_item_count,
            evaluation_exclusion_count=evaluation_exclusion_count,
            evaluation_exclusion_policy=evaluation_exclusion_policy,
            qc_decision_lineage_valid_item_count=(
                qc_decision_lineage_valid_item_count
            ),
            ready_sync_decision_lineage_valid_case_count=(
                ready_sync_decision_lineage_valid_case_count
            ),
            case_count=case_count,
            ready_case_count=ready_case_count,
            violation_count=0,
            status=_P8_PROMOTION_STATUS,
            reason_codes=(),
            receipt_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "locked_source_dataset_id": self.locked_source_dataset_id,
            "locked_source_revision": self.locked_source_revision,
            "locked_manifest_sha256": self.locked_manifest_sha256,
            "read_zero_seal_sha256": self.read_zero_seal_sha256,
            "locked_evaluation_report_sha256": (
                self.locked_evaluation_report_sha256
            ),
            "aggregate_metrics_sha256": self.aggregate_metrics_sha256,
            "zero_violation_replay_receipt_sha256": (
                self.zero_violation_replay_receipt_sha256
            ),
            "p8_bundle_sha256": self.p8_bundle_sha256,
            "p6_promotion_receipt_sha256": self.p6_promotion_receipt_sha256,
            "calibration_artifact_sha256": self.calibration_artifact_sha256,
            "catalog_sha256": self.catalog_sha256,
            "policy_sha256": self.policy_sha256,
            "pipeline_identity_sha256": self.pipeline_identity_sha256,
            "media_capture_contract_id": self.media_capture_contract_id,
            "media_capture_contract_sha256": self.media_capture_contract_sha256,
            "visual_observation_contract_id": self.visual_observation_contract_id,
            "visual_observation_contract_sha256": (
                self.visual_observation_contract_sha256
            ),
            "population_id_sha256": self.population_id_sha256,
            "context_contract_sha256": self.context_contract_sha256,
            "partition_sealed_before_read": self.partition_sealed_before_read,
            "partition_disjoint": self.partition_disjoint,
            "evaluation_completed": self.evaluation_completed,
            "claim_policy_replay_completed": self.claim_policy_replay_completed,
            "admitted_item_count": self.admitted_item_count,
            "paired_item_count": self.paired_item_count,
            "evaluated_item_count": self.evaluated_item_count,
            "evaluation_exclusion_count": self.evaluation_exclusion_count,
            "evaluation_exclusion_policy": self.evaluation_exclusion_policy,
            "qc_decision_lineage_valid_item_count": (
                self.qc_decision_lineage_valid_item_count
            ),
            "ready_sync_decision_lineage_valid_case_count": (
                self.ready_sync_decision_lineage_valid_case_count
            ),
            "case_count": self.case_count,
            "ready_case_count": self.ready_case_count,
            "violation_count": self.violation_count,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> AutomatedP8PromotionReceipt:
        document = require_mapping(value, "p8_automated_promotion_receipt")
        expected_fields = {
            "schema_version",
            "locked_source_dataset_id",
            "locked_source_revision",
            "locked_manifest_sha256",
            "read_zero_seal_sha256",
            "locked_evaluation_report_sha256",
            "aggregate_metrics_sha256",
            "zero_violation_replay_receipt_sha256",
            "p8_bundle_sha256",
            "p6_promotion_receipt_sha256",
            "calibration_artifact_sha256",
            "catalog_sha256",
            "policy_sha256",
            "pipeline_identity_sha256",
            "media_capture_contract_id",
            "media_capture_contract_sha256",
            "visual_observation_contract_id",
            "visual_observation_contract_sha256",
            "population_id_sha256",
            "context_contract_sha256",
            "partition_sealed_before_read",
            "partition_disjoint",
            "evaluation_completed",
            "claim_policy_replay_completed",
            "admitted_item_count",
            "paired_item_count",
            "evaluated_item_count",
            "evaluation_exclusion_count",
            "evaluation_exclusion_policy",
            "qc_decision_lineage_valid_item_count",
            "ready_sync_decision_lineage_valid_case_count",
            "case_count",
            "ready_case_count",
            "violation_count",
            "status",
            "reason_codes",
            "receipt_sha256",
        }
        require_exact_keys(document, expected_fields, "p8_automated_promotion_receipt")
        return cls(
            schema_version=require_non_empty_string(
                document["schema_version"],
                "p8_automated_promotion_receipt.schema_version",
            ),
            locked_source_dataset_id=require_safe_identifier(
                document["locked_source_dataset_id"],
                "p8_automated_promotion_receipt.locked_source_dataset_id",
            ),
            locked_source_revision=require_safe_identifier(
                document["locked_source_revision"],
                "p8_automated_promotion_receipt.locked_source_revision",
            ),
            locked_manifest_sha256=require_sha256(
                document["locked_manifest_sha256"],
                "p8_automated_promotion_receipt.locked_manifest_sha256",
            ),
            read_zero_seal_sha256=require_sha256(
                document["read_zero_seal_sha256"],
                "p8_automated_promotion_receipt.read_zero_seal_sha256",
            ),
            locked_evaluation_report_sha256=require_sha256(
                document["locked_evaluation_report_sha256"],
                "p8_automated_promotion_receipt.locked_evaluation_report_sha256",
            ),
            aggregate_metrics_sha256=require_sha256(
                document["aggregate_metrics_sha256"],
                "p8_automated_promotion_receipt.aggregate_metrics_sha256",
            ),
            zero_violation_replay_receipt_sha256=require_sha256(
                document["zero_violation_replay_receipt_sha256"],
                "p8_automated_promotion_receipt.zero_violation_replay_receipt_sha256",
            ),
            p8_bundle_sha256=require_sha256(
                document["p8_bundle_sha256"],
                "p8_automated_promotion_receipt.p8_bundle_sha256",
            ),
            p6_promotion_receipt_sha256=require_sha256(
                document["p6_promotion_receipt_sha256"],
                "p8_automated_promotion_receipt.p6_promotion_receipt_sha256",
            ),
            calibration_artifact_sha256=require_sha256(
                document["calibration_artifact_sha256"],
                "p8_automated_promotion_receipt.calibration_artifact_sha256",
            ),
            catalog_sha256=require_sha256(
                document["catalog_sha256"],
                "p8_automated_promotion_receipt.catalog_sha256",
            ),
            policy_sha256=require_sha256(
                document["policy_sha256"],
                "p8_automated_promotion_receipt.policy_sha256",
            ),
            pipeline_identity_sha256=require_sha256(
                document["pipeline_identity_sha256"],
                "p8_automated_promotion_receipt.pipeline_identity_sha256",
            ),
            media_capture_contract_id=require_safe_identifier(
                document["media_capture_contract_id"],
                "p8_automated_promotion_receipt.media_capture_contract_id",
            ),
            media_capture_contract_sha256=require_sha256(
                document["media_capture_contract_sha256"],
                "p8_automated_promotion_receipt.media_capture_contract_sha256",
            ),
            visual_observation_contract_id=require_safe_identifier(
                document["visual_observation_contract_id"],
                "p8_automated_promotion_receipt.visual_observation_contract_id",
            ),
            visual_observation_contract_sha256=require_sha256(
                document["visual_observation_contract_sha256"],
                "p8_automated_promotion_receipt.visual_observation_contract_sha256",
            ),
            population_id_sha256=require_sha256(
                document["population_id_sha256"],
                "p8_automated_promotion_receipt.population_id_sha256",
            ),
            context_contract_sha256=require_sha256(
                document["context_contract_sha256"],
                "p8_automated_promotion_receipt.context_contract_sha256",
            ),
            partition_sealed_before_read=require_boolean(
                document["partition_sealed_before_read"],
                "p8_automated_promotion_receipt.partition_sealed_before_read",
            ),
            partition_disjoint=require_boolean(
                document["partition_disjoint"],
                "p8_automated_promotion_receipt.partition_disjoint",
            ),
            evaluation_completed=require_boolean(
                document["evaluation_completed"],
                "p8_automated_promotion_receipt.evaluation_completed",
            ),
            claim_policy_replay_completed=require_boolean(
                document["claim_policy_replay_completed"],
                "p8_automated_promotion_receipt.claim_policy_replay_completed",
            ),
            admitted_item_count=require_integer(
                document["admitted_item_count"],
                "p8_automated_promotion_receipt.admitted_item_count",
                minimum=1,
            ),
            paired_item_count=require_integer(
                document["paired_item_count"],
                "p8_automated_promotion_receipt.paired_item_count",
                minimum=1,
            ),
            evaluated_item_count=require_integer(
                document["evaluated_item_count"],
                "p8_automated_promotion_receipt.evaluated_item_count",
                minimum=1,
            ),
            evaluation_exclusion_count=require_integer(
                document["evaluation_exclusion_count"],
                "p8_automated_promotion_receipt.evaluation_exclusion_count",
                minimum=0,
            ),
            evaluation_exclusion_policy=require_p8_evaluation_exclusion_policy(
                document["evaluation_exclusion_policy"]
            ),
            qc_decision_lineage_valid_item_count=require_integer(
                document["qc_decision_lineage_valid_item_count"],
                "p8_automated_promotion_receipt.qc_decision_lineage_valid_item_count",
                minimum=0,
            ),
            ready_sync_decision_lineage_valid_case_count=require_integer(
                document["ready_sync_decision_lineage_valid_case_count"],
                "p8_automated_promotion_receipt.ready_sync_decision_lineage_valid_case_count",
                minimum=0,
            ),
            case_count=require_integer(
                document["case_count"],
                "p8_automated_promotion_receipt.case_count",
                minimum=1,
            ),
            ready_case_count=require_integer(
                document["ready_case_count"],
                "p8_automated_promotion_receipt.ready_case_count",
                minimum=0,
            ),
            violation_count=require_integer(
                document["violation_count"],
                "p8_automated_promotion_receipt.violation_count",
                minimum=0,
            ),
            status=require_safe_identifier(
                document["status"], "p8_automated_promotion_receipt.status"
            ),
            reason_codes=_require_safe_identifier_tuple(
                document["reason_codes"],
                "p8_automated_promotion_receipt.reason_codes",
            ),
            receipt_sha256=require_sha256(
                document["receipt_sha256"],
                "p8_automated_promotion_receipt.receipt_sha256",
            ),
        )


@dataclass(frozen=True, slots=True)
class RuntimeClaimBinding:
    """One exact catalog binding copied into the frozen runtime policy."""

    approved_claim_id: str
    action_id: str
    renderer_key: str
    cue_id: str

    def __post_init__(self) -> None:
        for name in (
            "approved_claim_id",
            "action_id",
            "renderer_key",
            "cue_id",
        ):
            require_safe_identifier(getattr(self, name), name)

    def to_json_object(self) -> dict[str, object]:
        return {
            "approved_claim_id": self.approved_claim_id,
            "action_id": self.action_id,
            "renderer_key": self.renderer_key,
            "cue_id": self.cue_id,
        }

    @classmethod
    def from_json_object(cls, value: object) -> RuntimeClaimBinding:
        document = require_mapping(value, "runtime_claim_binding")
        require_exact_keys(
            document,
            {"approved_claim_id", "action_id", "renderer_key", "cue_id"},
            "runtime_claim_binding",
        )
        return cls(
            approved_claim_id=require_safe_identifier(
                document["approved_claim_id"], "runtime_claim_binding.approved_claim_id"
            ),
            action_id=require_safe_identifier(
                document["action_id"], "runtime_claim_binding.action_id"
            ),
            renderer_key=require_safe_identifier(
                document["renderer_key"], "runtime_claim_binding.renderer_key"
            ),
            cue_id=require_safe_identifier(
                document["cue_id"], "runtime_claim_binding.cue_id"
            ),
        )


@dataclass(frozen=True, slots=True)
class RuntimeVisualClaimPolicy:
    """Immutable claim policy whose allowlists must equal the catalog bindings."""

    policy_revision: str
    allowed_cue_ids: tuple[str, ...]
    allowed_renderer_keys: tuple[str, ...]
    allowed_claim_bindings: tuple[RuntimeClaimBinding, ...]
    forbidden_identifier_tokens: tuple[str, ...]
    policy_sha256: str
    schema_version: str = RUNTIME_VISUAL_CLAIM_POLICY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != RUNTIME_VISUAL_CLAIM_POLICY_SCHEMA_VERSION:
            raise VisualFeedbackContractError("runtime claim policy schema differs")
        require_safe_identifier(self.policy_revision, "policy_revision")
        if not self.allowed_cue_ids or not self.allowed_renderer_keys:
            raise VisualFeedbackContractError(
                "runtime claim policy allowlists must not be empty"
            )
        for values, name in (
            (self.allowed_cue_ids, "allowed_cue_ids"),
            (self.allowed_renderer_keys, "allowed_renderer_keys"),
            (self.forbidden_identifier_tokens, "forbidden_identifier_tokens"),
        ):
            for item in values:
                require_safe_identifier(item, f"{name}[]")
            if len(set(values)) != len(values):
                raise VisualFeedbackContractError(f"{name} must not contain duplicates")
        if not self.allowed_claim_bindings:
            raise VisualFeedbackContractError(
                "runtime claim policy bindings must not be empty"
            )
        if len(set(self.allowed_claim_bindings)) != len(self.allowed_claim_bindings):
            raise VisualFeedbackContractError(
                "runtime claim policy bindings must not contain duplicates"
            )
        if not _REQUIRED_FORBIDDEN_IDENTIFIER_TOKENS.issubset(
            {token.lower() for token in self.forbidden_identifier_tokens}
        ):
            raise VisualFeedbackContractError(
                "runtime claim policy forbidden targets are incomplete"
            )
        require_sha256(self.policy_sha256, "policy_sha256")
        if self.policy_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("runtime claim policy digest differs")

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "policy_revision": self.policy_revision,
            "allowed_cue_ids": list(self.allowed_cue_ids),
            "allowed_renderer_keys": list(self.allowed_renderer_keys),
            "allowed_claim_bindings": [
                binding.to_json_object() for binding in self.allowed_claim_bindings
            ],
            "forbidden_identifier_tokens": list(self.forbidden_identifier_tokens),
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "policy_sha256": self.policy_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> RuntimeVisualClaimPolicy:
        document = require_mapping(value, "runtime_visual_claim_policy")
        require_exact_keys(
            document,
            {
                "schema_version",
                "policy_revision",
                "allowed_cue_ids",
                "allowed_renderer_keys",
                "allowed_claim_bindings",
                "forbidden_identifier_tokens",
                "policy_sha256",
            },
            "runtime_visual_claim_policy",
        )
        raw_bindings = document["allowed_claim_bindings"]
        if not isinstance(raw_bindings, list):
            raise VisualFeedbackContractError(
                "runtime claim policy bindings must be an array"
            )
        return cls(
            schema_version=require_non_empty_string(
                document["schema_version"], "runtime_visual_claim_policy.schema_version"
            ),
            policy_revision=require_safe_identifier(
                document["policy_revision"],
                "runtime_visual_claim_policy.policy_revision",
            ),
            allowed_cue_ids=_require_safe_identifier_tuple(
                document["allowed_cue_ids"],
                "runtime_visual_claim_policy.allowed_cue_ids",
            ),
            allowed_renderer_keys=_require_safe_identifier_tuple(
                document["allowed_renderer_keys"],
                "runtime_visual_claim_policy.allowed_renderer_keys",
            ),
            allowed_claim_bindings=tuple(
                RuntimeClaimBinding.from_json_object(item) for item in raw_bindings
            ),
            forbidden_identifier_tokens=_require_safe_identifier_tuple(
                document["forbidden_identifier_tokens"],
                "runtime_visual_claim_policy.forbidden_identifier_tokens",
            ),
            policy_sha256=require_sha256(
                document["policy_sha256"],
                "runtime_visual_claim_policy.policy_sha256",
            ),
        )


@dataclass(frozen=True, slots=True)
class VisualRuntimeBundle:
    """Frozen, fully cross-bound inputs eligible for user-runtime evaluation."""

    bundle_id: str
    revision: str
    pipeline_identity: VisualRuntimePipelineIdentity
    p6_promotion_receipt: AutomatedP6PromotionReceipt
    p8_promotion_receipt: AutomatedP8PromotionReceipt
    calibration_artifact: VisualCalibrationArtifact
    claim_catalog: VisualClaimCatalog
    claim_policy: RuntimeVisualClaimPolicy
    bundle_sha256: str
    schema_version: str = VISUAL_RUNTIME_BUNDLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != VISUAL_RUNTIME_BUNDLE_SCHEMA_VERSION:
            raise VisualFeedbackContractError("visual runtime bundle schema differs")
        require_safe_identifier(self.bundle_id, "bundle_id")
        require_safe_identifier(self.revision, "revision")
        require_sha256(self.bundle_sha256, "bundle_sha256")
        if self.calibration_artifact.scope is not CalibrationScope.CALIBRATED:
            raise VisualFeedbackContractError(
                "visual runtime calibration must use calibrated scope"
            )
        if self.claim_catalog.scope is not CalibrationScope.CALIBRATED:
            raise VisualFeedbackContractError(
                "visual runtime claim catalog must use calibrated scope"
            )
        self._validate_cross_digests()
        if self.bundle_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("visual runtime bundle digest differs")

    def _validate_cross_digests(self) -> None:
        identity = self.pipeline_identity
        artifact = self.calibration_artifact
        catalog = self.claim_catalog
        policy = self.claim_policy
        p6_receipt = self.p6_promotion_receipt
        p8_receipt = self.p8_promotion_receipt

        if (
            artifact.capture_contract_id
            != identity.visual_observation_contract_id
        ):
            raise VisualFeedbackContractError(
                "runtime calibration observation contract differs"
            )
        if artifact.population_id_sha256 != identity.population_id_sha256:
            raise VisualFeedbackContractError(
                "runtime calibration population digest differs"
            )
        if p6_receipt.calibration_artifact_sha256 != artifact.artifact_sha256:
            raise VisualFeedbackContractError("P6 calibration artifact digest differs")
        if p8_receipt.calibration_artifact_sha256 != artifact.artifact_sha256:
            raise VisualFeedbackContractError("P8 calibration artifact digest differs")
        if p8_receipt.catalog_sha256 != catalog.catalog_sha256:
            raise VisualFeedbackContractError("P8 claim catalog digest differs")
        if p8_receipt.policy_sha256 != policy.policy_sha256:
            raise VisualFeedbackContractError("P8 claim policy digest differs")
        if p6_receipt.catalog_sha256 != catalog.catalog_sha256:
            raise VisualFeedbackContractError("P6B claim catalog digest differs")
        if p6_receipt.policy_sha256 != policy.policy_sha256:
            raise VisualFeedbackContractError("P6B claim policy digest differs")
        if p8_receipt.p6_promotion_receipt_sha256 != p6_receipt.receipt_sha256:
            raise VisualFeedbackContractError("P8 does not bind the exact P6 receipt")
        for receipt_name, receipt in (("P6", p6_receipt), ("P8", p8_receipt)):
            if receipt.pipeline_identity_sha256 != identity.identity_sha256:
                raise VisualFeedbackContractError(
                    f"{receipt_name} pipeline identity digest differs"
                )
            if (
                receipt.media_capture_contract_id
                != identity.media_capture_contract_id
                or receipt.media_capture_contract_sha256
                != identity.media_capture_contract_sha256
            ):
                raise VisualFeedbackContractError(
                    f"{receipt_name} media capture contract differs"
                )
            if (
                receipt.visual_observation_contract_id
                != identity.visual_observation_contract_id
                or receipt.visual_observation_contract_sha256
                != identity.visual_observation_contract_sha256
            ):
                raise VisualFeedbackContractError(
                    f"{receipt_name} visual observation contract differs"
                )
            if receipt.population_id_sha256 != identity.population_id_sha256:
                raise VisualFeedbackContractError(
                    f"{receipt_name} population digest differs"
                )
            if receipt.context_contract_sha256 != identity.context_contract_sha256:
                raise VisualFeedbackContractError(
                    f"{receipt_name} context contract digest differs"
                )

        catalog_bindings = {
            (
                claim.approved_claim_id,
                claim.action_id,
                claim.renderer_key,
                claim.cue_id,
            )
            for claim in catalog.claims
        }
        policy_bindings = {
            (
                binding.approved_claim_id,
                binding.action_id,
                binding.renderer_key,
                binding.cue_id,
            )
            for binding in policy.allowed_claim_bindings
        }
        if policy_bindings != catalog_bindings:
            raise VisualFeedbackContractError(
                "runtime claim policy and catalog bindings differ"
            )
        if {claim.cue_id for claim in catalog.claims} - set(policy.allowed_cue_ids):
            raise VisualFeedbackContractError(
                "runtime catalog contains a cue outside policy"
            )
        if {claim.renderer_key for claim in catalog.claims} - set(
            policy.allowed_renderer_keys
        ):
            raise VisualFeedbackContractError(
                "runtime catalog contains a renderer outside policy"
            )

    @classmethod
    def issue(
        cls,
        *,
        bundle_id: str,
        revision: str,
        pipeline_identity: VisualRuntimePipelineIdentity,
        p6_promotion_receipt: AutomatedP6PromotionReceipt,
        p8_promotion_receipt: AutomatedP8PromotionReceipt,
        calibration_artifact: VisualCalibrationArtifact,
        claim_catalog: VisualClaimCatalog,
        claim_policy: RuntimeVisualClaimPolicy,
    ) -> VisualRuntimeBundle:
        unsigned = {
            "schema_version": VISUAL_RUNTIME_BUNDLE_SCHEMA_VERSION,
            "bundle_id": bundle_id,
            "revision": revision,
            "pipeline_identity": pipeline_identity.to_json_object(),
            "p6_promotion_receipt": p6_promotion_receipt.to_json_object(),
            "p8_promotion_receipt": p8_promotion_receipt.to_json_object(),
            "calibration_artifact": calibration_artifact.to_json_object(),
            "claim_catalog": claim_catalog.to_json_object(),
            "claim_policy": claim_policy.to_json_object(),
        }
        return cls(
            bundle_id=bundle_id,
            revision=revision,
            pipeline_identity=pipeline_identity,
            p6_promotion_receipt=p6_promotion_receipt,
            p8_promotion_receipt=p8_promotion_receipt,
            calibration_artifact=calibration_artifact,
            claim_catalog=claim_catalog,
            claim_policy=claim_policy,
            bundle_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "bundle_id": self.bundle_id,
            "revision": self.revision,
            "pipeline_identity": self.pipeline_identity.to_json_object(),
            "p6_promotion_receipt": self.p6_promotion_receipt.to_json_object(),
            "p8_promotion_receipt": self.p8_promotion_receipt.to_json_object(),
            "calibration_artifact": self.calibration_artifact.to_json_object(),
            "claim_catalog": self.claim_catalog.to_json_object(),
            "claim_policy": self.claim_policy.to_json_object(),
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "bundle_sha256": self.bundle_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> VisualRuntimeBundle:
        document = require_mapping(value, "visual_runtime_bundle")
        require_exact_keys(
            document,
            {
                "schema_version",
                "bundle_id",
                "revision",
                "pipeline_identity",
                "p6_promotion_receipt",
                "p8_promotion_receipt",
                "calibration_artifact",
                "claim_catalog",
                "claim_policy",
                "bundle_sha256",
            },
            "visual_runtime_bundle",
        )
        return cls(
            schema_version=require_non_empty_string(
                document["schema_version"], "visual_runtime_bundle.schema_version"
            ),
            bundle_id=require_safe_identifier(
                document["bundle_id"], "visual_runtime_bundle.bundle_id"
            ),
            revision=require_safe_identifier(
                document["revision"], "visual_runtime_bundle.revision"
            ),
            pipeline_identity=VisualRuntimePipelineIdentity.from_json_object(
                document["pipeline_identity"]
            ),
            p6_promotion_receipt=AutomatedP6PromotionReceipt.from_json_object(
                document["p6_promotion_receipt"]
            ),
            p8_promotion_receipt=AutomatedP8PromotionReceipt.from_json_object(
                document["p8_promotion_receipt"]
            ),
            calibration_artifact=VisualCalibrationArtifact.from_json_object(
                document["calibration_artifact"]
            ),
            claim_catalog=VisualClaimCatalog.from_json_object(
                document["claim_catalog"]
            ),
            claim_policy=RuntimeVisualClaimPolicy.from_json_object(
                document["claim_policy"]
            ),
            bundle_sha256=require_sha256(
                document["bundle_sha256"], "visual_runtime_bundle.bundle_sha256"
            ),
        )


@dataclass(frozen=True, slots=True)
class AutomatedVisualRuntimeEligibilityReceipt:
    """Deterministic authorization result for one exact runtime bundle."""

    runtime_bundle_sha256: str
    pipeline_identity_sha256: str
    p6_bundle_sha256: str
    p6_promotion_receipt_sha256: str
    p8_bundle_sha256: str
    p8_promotion_receipt_sha256: str
    calibration_artifact_sha256: str
    catalog_sha256: str
    policy_sha256: str
    status: str
    reason_codes: tuple[str, ...]
    receipt_sha256: str
    schema_version: str = (
        AUTOMATED_VISUAL_RUNTIME_ELIGIBILITY_RECEIPT_SCHEMA_VERSION
    )

    def __post_init__(self) -> None:
        if (
            self.schema_version
            != AUTOMATED_VISUAL_RUNTIME_ELIGIBILITY_RECEIPT_SCHEMA_VERSION
        ):
            raise VisualFeedbackContractError(
                "automated visual runtime eligibility receipt schema differs"
            )
        for name in (
            "runtime_bundle_sha256",
            "pipeline_identity_sha256",
            "p6_bundle_sha256",
            "p6_promotion_receipt_sha256",
            "p8_bundle_sha256",
            "p8_promotion_receipt_sha256",
            "calibration_artifact_sha256",
            "catalog_sha256",
            "policy_sha256",
            "receipt_sha256",
        ):
            require_sha256(getattr(self, name), name)
        if self.status != _RUNTIME_ELIGIBILITY_STATUS:
            raise VisualFeedbackContractError(
                "automated visual runtime eligibility status differs"
            )
        if self.reason_codes:
            raise VisualFeedbackContractError(
                "eligible runtime receipt cannot contain blocking reasons"
            )
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError(
                "automated visual runtime eligibility receipt digest differs"
            )

    @classmethod
    def issue(
        cls, bundle: VisualRuntimeBundle
    ) -> AutomatedVisualRuntimeEligibilityReceipt:
        unsigned = _runtime_eligibility_unsigned(bundle)
        return cls(
            runtime_bundle_sha256=bundle.bundle_sha256,
            pipeline_identity_sha256=bundle.pipeline_identity.identity_sha256,
            p6_bundle_sha256=bundle.p6_promotion_receipt.p6_bundle_sha256,
            p6_promotion_receipt_sha256=(
                bundle.p6_promotion_receipt.receipt_sha256
            ),
            p8_bundle_sha256=bundle.p8_promotion_receipt.p8_bundle_sha256,
            p8_promotion_receipt_sha256=(
                bundle.p8_promotion_receipt.receipt_sha256
            ),
            calibration_artifact_sha256=(
                bundle.calibration_artifact.artifact_sha256
            ),
            catalog_sha256=bundle.claim_catalog.catalog_sha256,
            policy_sha256=bundle.claim_policy.policy_sha256,
            status=_RUNTIME_ELIGIBILITY_STATUS,
            reason_codes=(),
            receipt_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "runtime_bundle_sha256": self.runtime_bundle_sha256,
            "pipeline_identity_sha256": self.pipeline_identity_sha256,
            "p6_bundle_sha256": self.p6_bundle_sha256,
            "p6_promotion_receipt_sha256": self.p6_promotion_receipt_sha256,
            "p8_bundle_sha256": self.p8_bundle_sha256,
            "p8_promotion_receipt_sha256": self.p8_promotion_receipt_sha256,
            "calibration_artifact_sha256": self.calibration_artifact_sha256,
            "catalog_sha256": self.catalog_sha256,
            "policy_sha256": self.policy_sha256,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> AutomatedVisualRuntimeEligibilityReceipt:
        document = require_mapping(
            value, "automated_visual_runtime_eligibility_receipt"
        )
        expected_fields = {
            "schema_version",
            "runtime_bundle_sha256",
            "pipeline_identity_sha256",
            "p6_bundle_sha256",
            "p6_promotion_receipt_sha256",
            "p8_bundle_sha256",
            "p8_promotion_receipt_sha256",
            "calibration_artifact_sha256",
            "catalog_sha256",
            "policy_sha256",
            "status",
            "reason_codes",
            "receipt_sha256",
        }
        require_exact_keys(
            document,
            expected_fields,
            "automated_visual_runtime_eligibility_receipt",
        )
        digest_fields = expected_fields - {
            "schema_version",
            "status",
            "reason_codes",
        }
        digests = {
            name: require_sha256(
                document[name],
                f"automated_visual_runtime_eligibility_receipt.{name}",
            )
            for name in digest_fields
        }
        return cls(
            schema_version=require_non_empty_string(
                document["schema_version"],
                "automated_visual_runtime_eligibility_receipt.schema_version",
            ),
            status=require_safe_identifier(
                document["status"],
                "automated_visual_runtime_eligibility_receipt.status",
            ),
            reason_codes=_require_safe_identifier_tuple(
                document["reason_codes"],
                "automated_visual_runtime_eligibility_receipt.reason_codes",
            ),
            **digests,
        )


def _runtime_eligibility_unsigned(bundle: VisualRuntimeBundle) -> dict[str, object]:
    return {
        "schema_version": AUTOMATED_VISUAL_RUNTIME_ELIGIBILITY_RECEIPT_SCHEMA_VERSION,
        "runtime_bundle_sha256": bundle.bundle_sha256,
        "pipeline_identity_sha256": bundle.pipeline_identity.identity_sha256,
        "p6_bundle_sha256": bundle.p6_promotion_receipt.p6_bundle_sha256,
        "p6_promotion_receipt_sha256": (
            bundle.p6_promotion_receipt.receipt_sha256
        ),
        "p8_bundle_sha256": bundle.p8_promotion_receipt.p8_bundle_sha256,
        "p8_promotion_receipt_sha256": (
            bundle.p8_promotion_receipt.receipt_sha256
        ),
        "calibration_artifact_sha256": bundle.calibration_artifact.artifact_sha256,
        "catalog_sha256": bundle.claim_catalog.catalog_sha256,
        "policy_sha256": bundle.claim_policy.policy_sha256,
        "status": _RUNTIME_ELIGIBILITY_STATUS,
        "reason_codes": [],
    }


def validate_automated_visual_runtime_eligibility(
    *,
    bundle: VisualRuntimeBundle,
    receipt: AutomatedVisualRuntimeEligibilityReceipt,
) -> AutomatedVisualRuntimeEligibilityReceipt:
    """Require the eligibility receipt to be the deterministic result for bundle."""

    expected = AutomatedVisualRuntimeEligibilityReceipt.issue(bundle)
    if receipt != expected:
        raise VisualFeedbackContractError(
            "runtime eligibility receipt does not resolve to the supplied bundle"
        )
    return receipt


@dataclass(frozen=True, slots=True)
class AttemptVisualReliabilityReceipt:
    """Cross-bind one reliable visual decision to one exact Seungun attempt."""

    attempt_id_sha256: str
    runtime_bundle_sha256: str
    runtime_eligibility_receipt_sha256: str
    binding_receipt_sha256: str
    detection_receipt_sha256: str
    phone_anchor_sha256: str
    video_qc_candidate_receipt_sha256: str
    video_qc_receipt_sha256: str
    video_qc_decision_policy_revision: str
    video_qc_decision_policy_sha256: str
    video_qc_decision_code_sha256: str
    av_sync_candidate_receipt_sha256: str
    av_sync_receipt_sha256: str
    av_sync_offset_receipt_sha256: str
    av_sync_decision_policy_revision: str
    av_sync_decision_policy_sha256: str
    av_sync_decision_code_sha256: str
    phone_window_decode_receipt_sha256: str
    phone_window_tracking_receipt_sha256: str
    phone_window_receipt_sha256: str
    decoder_code_sha256: str
    tracking_code_sha256: str
    phone_window_code_sha256: str
    phone_window_policy_revision: str
    phone_window_policy_sha256: str
    media_capture_contract_id: str
    media_capture_contract_sha256: str
    visual_observation_contract_id: str
    visual_observation_contract_sha256: str
    calibration_artifact_sha256: str
    catalog_sha256: str
    policy_sha256: str
    pipeline_identity_sha256: str
    status: str
    reason_codes: tuple[str, ...]
    receipt_sha256: str
    schema_version: str = ATTEMPT_VISUAL_RELIABILITY_RECEIPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ATTEMPT_VISUAL_RELIABILITY_RECEIPT_SCHEMA_VERSION:
            raise VisualFeedbackContractError(
                "attempt visual reliability receipt schema differs"
            )
        require_safe_identifier(
            self.phone_window_policy_revision,
            "phone_window_policy_revision",
        )
        require_safe_identifier(
            self.video_qc_decision_policy_revision,
            "video_qc_decision_policy_revision",
        )
        require_safe_identifier(
            self.av_sync_decision_policy_revision,
            "av_sync_decision_policy_revision",
        )
        require_safe_identifier(
            self.media_capture_contract_id,
            "media_capture_contract_id",
        )
        require_safe_identifier(
            self.visual_observation_contract_id,
            "visual_observation_contract_id",
        )
        for name in (
            "attempt_id_sha256",
            "runtime_bundle_sha256",
            "runtime_eligibility_receipt_sha256",
            "binding_receipt_sha256",
            "detection_receipt_sha256",
            "phone_anchor_sha256",
            "video_qc_candidate_receipt_sha256",
            "video_qc_receipt_sha256",
            "video_qc_decision_policy_sha256",
            "video_qc_decision_code_sha256",
            "av_sync_candidate_receipt_sha256",
            "av_sync_receipt_sha256",
            "av_sync_offset_receipt_sha256",
            "av_sync_decision_policy_sha256",
            "av_sync_decision_code_sha256",
            "phone_window_decode_receipt_sha256",
            "phone_window_tracking_receipt_sha256",
            "phone_window_receipt_sha256",
            "decoder_code_sha256",
            "tracking_code_sha256",
            "phone_window_code_sha256",
            "phone_window_policy_sha256",
            "media_capture_contract_sha256",
            "visual_observation_contract_sha256",
            "calibration_artifact_sha256",
            "catalog_sha256",
            "policy_sha256",
            "pipeline_identity_sha256",
            "receipt_sha256",
        ):
            require_sha256(getattr(self, name), name)
        if self.media_capture_contract_sha256 != media_capture_contract_sha256(
            self.media_capture_contract_id
        ):
            raise VisualFeedbackContractError(
                "attempt media capture contract digest differs"
            )
        if (
            self.visual_observation_contract_sha256
            != visual_observation_contract_sha256(
                self.visual_observation_contract_id
            )
        ):
            raise VisualFeedbackContractError(
                "attempt visual observation contract digest differs"
            )
        if self.status != _ATTEMPT_RELIABILITY_STATUS:
            raise VisualFeedbackContractError(
                "attempt visual reliability status differs"
            )
        if self.reason_codes:
            raise VisualFeedbackContractError(
                "reliable attempt receipt cannot contain blocking reasons"
            )
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError(
                "attempt visual reliability receipt digest differs"
            )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "attempt_id_sha256": self.attempt_id_sha256,
            "runtime_bundle_sha256": self.runtime_bundle_sha256,
            "runtime_eligibility_receipt_sha256": (
                self.runtime_eligibility_receipt_sha256
            ),
            "binding_receipt_sha256": self.binding_receipt_sha256,
            "detection_receipt_sha256": self.detection_receipt_sha256,
            "phone_anchor_sha256": self.phone_anchor_sha256,
            "video_qc_candidate_receipt_sha256": (
                self.video_qc_candidate_receipt_sha256
            ),
            "video_qc_receipt_sha256": self.video_qc_receipt_sha256,
            "video_qc_decision_policy_revision": (
                self.video_qc_decision_policy_revision
            ),
            "video_qc_decision_policy_sha256": (
                self.video_qc_decision_policy_sha256
            ),
            "video_qc_decision_code_sha256": (
                self.video_qc_decision_code_sha256
            ),
            "av_sync_candidate_receipt_sha256": (
                self.av_sync_candidate_receipt_sha256
            ),
            "av_sync_receipt_sha256": self.av_sync_receipt_sha256,
            "av_sync_offset_receipt_sha256": self.av_sync_offset_receipt_sha256,
            "av_sync_decision_policy_revision": (
                self.av_sync_decision_policy_revision
            ),
            "av_sync_decision_policy_sha256": (
                self.av_sync_decision_policy_sha256
            ),
            "av_sync_decision_code_sha256": self.av_sync_decision_code_sha256,
            "phone_window_decode_receipt_sha256": (
                self.phone_window_decode_receipt_sha256
            ),
            "phone_window_tracking_receipt_sha256": (
                self.phone_window_tracking_receipt_sha256
            ),
            "phone_window_receipt_sha256": self.phone_window_receipt_sha256,
            "decoder_code_sha256": self.decoder_code_sha256,
            "tracking_code_sha256": self.tracking_code_sha256,
            "phone_window_code_sha256": self.phone_window_code_sha256,
            "phone_window_policy_revision": self.phone_window_policy_revision,
            "phone_window_policy_sha256": self.phone_window_policy_sha256,
            "media_capture_contract_id": self.media_capture_contract_id,
            "media_capture_contract_sha256": self.media_capture_contract_sha256,
            "visual_observation_contract_id": self.visual_observation_contract_id,
            "visual_observation_contract_sha256": (
                self.visual_observation_contract_sha256
            ),
            "calibration_artifact_sha256": self.calibration_artifact_sha256,
            "catalog_sha256": self.catalog_sha256,
            "policy_sha256": self.policy_sha256,
            "pipeline_identity_sha256": self.pipeline_identity_sha256,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> AttemptVisualReliabilityReceipt:
        document = require_mapping(value, "attempt_visual_reliability_receipt")
        expected_fields = {
            "schema_version",
            "attempt_id_sha256",
            "runtime_bundle_sha256",
            "runtime_eligibility_receipt_sha256",
            "binding_receipt_sha256",
            "detection_receipt_sha256",
            "phone_anchor_sha256",
            "video_qc_candidate_receipt_sha256",
            "video_qc_receipt_sha256",
            "video_qc_decision_policy_revision",
            "video_qc_decision_policy_sha256",
            "video_qc_decision_code_sha256",
            "av_sync_candidate_receipt_sha256",
            "av_sync_receipt_sha256",
            "av_sync_offset_receipt_sha256",
            "av_sync_decision_policy_revision",
            "av_sync_decision_policy_sha256",
            "av_sync_decision_code_sha256",
            "phone_window_decode_receipt_sha256",
            "phone_window_tracking_receipt_sha256",
            "phone_window_receipt_sha256",
            "decoder_code_sha256",
            "tracking_code_sha256",
            "phone_window_code_sha256",
            "phone_window_policy_revision",
            "phone_window_policy_sha256",
            "media_capture_contract_id",
            "media_capture_contract_sha256",
            "visual_observation_contract_id",
            "visual_observation_contract_sha256",
            "calibration_artifact_sha256",
            "catalog_sha256",
            "policy_sha256",
            "pipeline_identity_sha256",
            "status",
            "reason_codes",
            "receipt_sha256",
        }
        require_exact_keys(
            document, expected_fields, "attempt_visual_reliability_receipt"
        )
        digest_fields = expected_fields - {
            "schema_version",
            "status",
            "reason_codes",
            "phone_window_policy_revision",
            "video_qc_decision_policy_revision",
            "av_sync_decision_policy_revision",
            "media_capture_contract_id",
            "visual_observation_contract_id",
        }
        digests = {
            name: require_sha256(
                document[name], f"attempt_visual_reliability_receipt.{name}"
            )
            for name in digest_fields
        }
        return cls(
            schema_version=require_non_empty_string(
                document["schema_version"],
                "attempt_visual_reliability_receipt.schema_version",
            ),
            status=require_safe_identifier(
                document["status"], "attempt_visual_reliability_receipt.status"
            ),
            reason_codes=_require_safe_identifier_tuple(
                document["reason_codes"],
                "attempt_visual_reliability_receipt.reason_codes",
            ),
            phone_window_policy_revision=require_safe_identifier(
                document["phone_window_policy_revision"],
                "attempt_visual_reliability_receipt.phone_window_policy_revision",
            ),
            video_qc_decision_policy_revision=require_safe_identifier(
                document["video_qc_decision_policy_revision"],
                "attempt_visual_reliability_receipt.video_qc_decision_policy_revision",
            ),
            av_sync_decision_policy_revision=require_safe_identifier(
                document["av_sync_decision_policy_revision"],
                "attempt_visual_reliability_receipt.av_sync_decision_policy_revision",
            ),
            media_capture_contract_id=require_safe_identifier(
                document["media_capture_contract_id"],
                "attempt_visual_reliability_receipt.media_capture_contract_id",
            ),
            visual_observation_contract_id=require_safe_identifier(
                document["visual_observation_contract_id"],
                "attempt_visual_reliability_receipt.visual_observation_contract_id",
            ),
            **digests,
        )


def issue_attempt_visual_reliability(
    *,
    bundle: VisualRuntimeBundle,
    eligibility_receipt: AutomatedVisualRuntimeEligibilityReceipt,
    binding_receipt: SameAttemptBindingReceipt,
    snapshot: SeungunEvidenceSnapshot,
    expected_index: int,
    media_capture_contract_id: str,
    detection_receipt: object,
    video_qc_receipt: object,
    av_sync_receipt: object,
    phone_window_receipt: object,
) -> AttemptVisualReliabilityReceipt:
    """Validate all attempt evidence and issue the only reliable-attempt state."""

    validate_automated_visual_runtime_eligibility(
        bundle=bundle, receipt=eligibility_receipt
    )
    if (
        require_safe_identifier(
            media_capture_contract_id, "media_capture_contract_id"
        )
        != bundle.pipeline_identity.media_capture_contract_id
    ):
        raise VisualFeedbackContractError(
            "attempt media capture contract differs"
        )
    anchor = _validate_snapshot_binding(
        snapshot=snapshot,
        binding_receipt=binding_receipt,
        expected_index=expected_index,
    )
    detection = _validate_runtime_detection_receipt(
        detection_receipt,
        binding_receipt=binding_receipt,
        bundle=bundle,
    )
    video_qc = _validate_runtime_video_qc_receipt(
        video_qc_receipt,
        binding_receipt=binding_receipt,
        detection=detection,
        bundle=bundle,
    )
    av_sync = _validate_runtime_av_sync_receipt(
        av_sync_receipt,
        binding_receipt=binding_receipt,
        detection=detection,
        bundle=bundle,
    )
    if detection.video_qc_receipt_sha256 != video_qc["decision_receipt_sha256"]:
        raise VisualFeedbackContractError(
            "detection does not bind the validated video QC receipt"
        )
    if detection.av_sync_receipt_sha256 != av_sync["decision_receipt_sha256"]:
        raise VisualFeedbackContractError(
            "detection does not bind the validated A/V sync receipt"
        )
    if phone_window_receipt != detection.phone_window_evidence:
        raise VisualFeedbackContractError(
            "phone-window evidence does not resolve to typed detection"
        )
    phone_window = _validate_runtime_phone_window_receipt(
        phone_window_receipt,
        binding_receipt=binding_receipt,
        detection_receipt_sha256=detection.detection_receipt_sha256,
        phone_anchor_sha256=anchor.anchor_ref,
        video_qc_receipt_sha256=video_qc["decision_receipt_sha256"],
        av_sync_receipt_sha256=av_sync["decision_receipt_sha256"],
        av_sync_offset_receipt_sha256=av_sync["offset_receipt_sha256"],
        context_contract_sha256=bundle.pipeline_identity.context_contract_sha256,
        pipeline_identity=bundle.pipeline_identity,
    )
    unsigned = {
        "schema_version": ATTEMPT_VISUAL_RELIABILITY_RECEIPT_SCHEMA_VERSION,
        "attempt_id_sha256": binding_receipt.attempt_id_sha256,
        "runtime_bundle_sha256": bundle.bundle_sha256,
        "runtime_eligibility_receipt_sha256": eligibility_receipt.receipt_sha256,
        "binding_receipt_sha256": binding_receipt.receipt_sha256,
        "detection_receipt_sha256": detection.detection_receipt_sha256,
        "phone_anchor_sha256": anchor.anchor_ref,
        "video_qc_candidate_receipt_sha256": video_qc[
            "candidate_receipt_sha256"
        ],
        "video_qc_receipt_sha256": video_qc["decision_receipt_sha256"],
        "video_qc_decision_policy_revision": video_qc[
            "decision_policy_revision"
        ],
        "video_qc_decision_policy_sha256": video_qc[
            "decision_policy_sha256"
        ],
        "video_qc_decision_code_sha256": video_qc[
            "decision_code_sha256"
        ],
        "av_sync_candidate_receipt_sha256": av_sync[
            "candidate_receipt_sha256"
        ],
        "av_sync_receipt_sha256": av_sync["decision_receipt_sha256"],
        "av_sync_offset_receipt_sha256": av_sync["offset_receipt_sha256"],
        "av_sync_decision_policy_revision": av_sync[
            "decision_policy_revision"
        ],
        "av_sync_decision_policy_sha256": av_sync[
            "decision_policy_sha256"
        ],
        "av_sync_decision_code_sha256": av_sync["decision_code_sha256"],
        "phone_window_decode_receipt_sha256": phone_window[
            "decode_receipt_sha256"
        ],
        "phone_window_tracking_receipt_sha256": phone_window[
            "tracking_receipt_sha256"
        ],
        "phone_window_receipt_sha256": phone_window["runtime_receipt_sha256"],
        "decoder_code_sha256": phone_window["decoder_code_sha256"],
        "tracking_code_sha256": phone_window["tracking_code_sha256"],
        "phone_window_code_sha256": phone_window["phone_window_code_sha256"],
        "phone_window_policy_revision": phone_window[
            "phone_window_policy_revision"
        ],
        "phone_window_policy_sha256": phone_window[
            "phone_window_policy_sha256"
        ],
        "media_capture_contract_id": (
            bundle.pipeline_identity.media_capture_contract_id
        ),
        "media_capture_contract_sha256": (
            bundle.pipeline_identity.media_capture_contract_sha256
        ),
        "visual_observation_contract_id": (
            bundle.pipeline_identity.visual_observation_contract_id
        ),
        "visual_observation_contract_sha256": (
            bundle.pipeline_identity.visual_observation_contract_sha256
        ),
        "calibration_artifact_sha256": bundle.calibration_artifact.artifact_sha256,
        "catalog_sha256": bundle.claim_catalog.catalog_sha256,
        "policy_sha256": bundle.claim_policy.policy_sha256,
        "pipeline_identity_sha256": bundle.pipeline_identity.identity_sha256,
        "status": _ATTEMPT_RELIABILITY_STATUS,
        "reason_codes": [],
    }
    return AttemptVisualReliabilityReceipt(
        attempt_id_sha256=binding_receipt.attempt_id_sha256,
        runtime_bundle_sha256=bundle.bundle_sha256,
        runtime_eligibility_receipt_sha256=eligibility_receipt.receipt_sha256,
        binding_receipt_sha256=binding_receipt.receipt_sha256,
        detection_receipt_sha256=detection.detection_receipt_sha256,
        phone_anchor_sha256=anchor.anchor_ref,
        video_qc_candidate_receipt_sha256=video_qc[
            "candidate_receipt_sha256"
        ],
        video_qc_receipt_sha256=video_qc["decision_receipt_sha256"],
        video_qc_decision_policy_revision=video_qc[
            "decision_policy_revision"
        ],
        video_qc_decision_policy_sha256=video_qc[
            "decision_policy_sha256"
        ],
        video_qc_decision_code_sha256=video_qc["decision_code_sha256"],
        av_sync_candidate_receipt_sha256=av_sync[
            "candidate_receipt_sha256"
        ],
        av_sync_receipt_sha256=av_sync["decision_receipt_sha256"],
        av_sync_offset_receipt_sha256=av_sync["offset_receipt_sha256"],
        av_sync_decision_policy_revision=av_sync[
            "decision_policy_revision"
        ],
        av_sync_decision_policy_sha256=av_sync[
            "decision_policy_sha256"
        ],
        av_sync_decision_code_sha256=av_sync["decision_code_sha256"],
        phone_window_decode_receipt_sha256=phone_window[
            "decode_receipt_sha256"
        ],
        phone_window_tracking_receipt_sha256=phone_window[
            "tracking_receipt_sha256"
        ],
        phone_window_receipt_sha256=phone_window["runtime_receipt_sha256"],
        decoder_code_sha256=phone_window["decoder_code_sha256"],
        tracking_code_sha256=phone_window["tracking_code_sha256"],
        phone_window_code_sha256=phone_window["phone_window_code_sha256"],
        phone_window_policy_revision=phone_window[
            "phone_window_policy_revision"
        ],
        phone_window_policy_sha256=phone_window[
            "phone_window_policy_sha256"
        ],
        media_capture_contract_id=(
            bundle.pipeline_identity.media_capture_contract_id
        ),
        media_capture_contract_sha256=(
            bundle.pipeline_identity.media_capture_contract_sha256
        ),
        visual_observation_contract_id=(
            bundle.pipeline_identity.visual_observation_contract_id
        ),
        visual_observation_contract_sha256=(
            bundle.pipeline_identity.visual_observation_contract_sha256
        ),
        calibration_artifact_sha256=bundle.calibration_artifact.artifact_sha256,
        catalog_sha256=bundle.claim_catalog.catalog_sha256,
        policy_sha256=bundle.claim_policy.policy_sha256,
        pipeline_identity_sha256=bundle.pipeline_identity.identity_sha256,
        status=_ATTEMPT_RELIABILITY_STATUS,
        reason_codes=(),
        receipt_sha256=sha256_document(unsigned),
    )


def validate_attempt_visual_reliability(
    *,
    receipt: AttemptVisualReliabilityReceipt,
    bundle: VisualRuntimeBundle,
    eligibility_receipt: AutomatedVisualRuntimeEligibilityReceipt,
    binding_receipt: SameAttemptBindingReceipt,
    snapshot: SeungunEvidenceSnapshot,
    expected_index: int,
    media_capture_contract_id: str,
    detection_receipt: object,
    video_qc_receipt: object,
    av_sync_receipt: object,
    phone_window_receipt: object,
) -> AttemptVisualReliabilityReceipt:
    """Reload and reproduce an attempt receipt from all of its bound evidence."""

    expected = issue_attempt_visual_reliability(
        bundle=bundle,
        eligibility_receipt=eligibility_receipt,
        binding_receipt=binding_receipt,
        snapshot=snapshot,
        expected_index=expected_index,
        media_capture_contract_id=media_capture_contract_id,
        detection_receipt=detection_receipt,
        video_qc_receipt=video_qc_receipt,
        av_sync_receipt=av_sync_receipt,
        phone_window_receipt=phone_window_receipt,
    )
    if receipt != expected:
        raise VisualFeedbackContractError(
            "attempt reliability receipt does not resolve to supplied evidence"
        )
    return receipt


def _validate_snapshot_binding(
    *,
    snapshot: SeungunEvidenceSnapshot,
    binding_receipt: SameAttemptBindingReceipt,
    expected_index: int,
) -> UpstreamPhoneAnchor:
    if snapshot.reliability_status is not UpstreamReliabilityStatus.SUPPORTED:
        raise VisualFeedbackContractError(
            "unsupported Seungun evidence cannot authorize visual reliability"
        )
    if snapshot.attempt_id_sha256 != binding_receipt.attempt_id_sha256:
        raise VisualFeedbackContractError("Seungun snapshot attempt differs")
    if snapshot.binding_receipt_sha256 != binding_receipt.receipt_sha256:
        raise VisualFeedbackContractError("Seungun snapshot binding differs")
    if snapshot.prompt_id_sha256 != binding_receipt.prompt_id_sha256:
        raise VisualFeedbackContractError("Seungun snapshot prompt differs")
    if snapshot.prompt_revision != binding_receipt.prompt_revision:
        raise VisualFeedbackContractError("Seungun snapshot prompt revision differs")
    if snapshot.expected_phone_sha256 != binding_receipt.expected_phone_sha256:
        raise VisualFeedbackContractError("Seungun expected phone digest differs")
    anchor = snapshot.anchor(expected_index)
    if anchor is None:
        raise VisualFeedbackContractError("Seungun phone anchor is unavailable")
    if not anchor.is_error or anchor.start_ms is None or anchor.end_ms is None:
        raise VisualFeedbackContractError(
            "visual reliability requires a timed upstream error anchor"
        )
    return anchor


def _validate_runtime_detection_receipt(
    value: object,
    *,
    binding_receipt: SameAttemptBindingReceipt,
    bundle: VisualRuntimeBundle,
) -> object:
    from ...pronunciation_attempts.adapters.runtime import (
        RuntimeVisualDetectionEvidence,
    )

    if type(value) is not RuntimeVisualDetectionEvidence:
        raise VisualFeedbackContractError(
            "typed runtime visual detection evidence is required"
        )
    if value.schema_version != RUNTIME_VISUAL_DETECTION_SCHEMA_VERSION:
        raise VisualFeedbackContractError("runtime visual detection schema differs")
    if value.detection_receipt_sha256 != sha256_document(
        value.unsigned_json_object()
    ):
        raise VisualFeedbackContractError("runtime visual detection digest differs")
    if value.binding_receipt_sha256 != binding_receipt.receipt_sha256:
        raise VisualFeedbackContractError("visual detection binding differs")
    if value.container_sha256 != binding_receipt.video_source_sha256:
        raise VisualFeedbackContractError("visual detection video source differs")
    if value.model_sha256 != bundle.pipeline_identity.landmark_model_sha256:
        raise VisualFeedbackContractError("visual detection model digest differs")
    if (
        getattr(value.landmark_evidence, "evidence_sha256", None)
        != value.landmark_evidence_sha256
        or getattr(value.landmark_evidence, "model_sha256", None)
        != value.model_sha256
        or getattr(value.landmark_evidence, "container_sha256", None)
        != value.container_sha256
    ):
        raise VisualFeedbackContractError(
            "runtime visual landmark object lineage differs"
        )
    if (
        value.video_qc_candidate_receipt_sha256 is None
        or value.video_qc_receipt_sha256 is None
        or value.av_sync_candidate_receipt_sha256 is None
        or value.av_sync_receipt_sha256 is None
        or value.av_sync_offset_receipt_sha256 is None
        or value.phone_window_evidence is None
    ):
        raise VisualFeedbackContractError(
            "runtime visual detection decision lineage is incomplete"
        )
    return value


def _validate_runtime_video_qc_receipt(
    value: object,
    *,
    binding_receipt: SameAttemptBindingReceipt,
    detection: Mapping[str, object],
    bundle: VisualRuntimeBundle,
) -> Mapping[str, str]:
    from ...pronunciation_attempts.adapters.calibrated_runtime_decisions import (
        RuntimeVideoQcDecision,
    )
    from ...pronunciation_attempts.adapters.video_qc import (
        VideoQcCandidateEvidence,
    )

    candidate = detection.video_qc_evidence
    decision = detection.video_qc_decision
    if (
        type(candidate) is not VideoQcCandidateEvidence
        or type(decision) is not RuntimeVideoQcDecision
        or type(value) is not RuntimeVideoQcDecision
        or value != decision
    ):
        raise VisualFeedbackContractError(
            "typed runtime video QC candidate and decision are required"
        )
    if decision.schema_version != RUNTIME_VIDEO_QC_DECISION_SCHEMA_VERSION:
        raise VisualFeedbackContractError("runtime video QC schema differs")
    identity = bundle.pipeline_identity
    if (
        candidate.status != "candidate_unvalidated"
        or candidate.container_sha256 != binding_receipt.video_source_sha256
        or candidate.landmark_evidence_sha256
        != detection.landmark_evidence_sha256
        or candidate.landmark_model_sha256 != identity.landmark_model_sha256
        or candidate.measurement_revision
        != identity.video_qc_measurement_revision
        or candidate.policy_receipt_sha256
        != identity.video_qc_measurement_policy_sha256
        or candidate.measurement_adapter_code_sha256
        != identity.video_qc_measurement_code_sha256
        or candidate.receipt_sha256
        != detection.video_qc_candidate_receipt_sha256
    ):
        raise VisualFeedbackContractError(
            "runtime video QC raw candidate lineage differs"
        )
    if (
        decision.attempt_id_sha256 != binding_receipt.attempt_id_sha256
        or decision.binding_receipt_sha256 != binding_receipt.receipt_sha256
        or decision.container_sha256 != candidate.container_sha256
        or decision.landmark_evidence_sha256
        != candidate.landmark_evidence_sha256
        or decision.landmark_model_sha256 != candidate.landmark_model_sha256
        or decision.source_candidate_receipt_sha256
        != candidate.receipt_sha256
        or decision.source_candidate_provenance_receipt_sha256
        != candidate.provenance_receipt_sha256
        or decision.measurement_revision != candidate.measurement_revision
        or decision.measurement_policy_sha256
        != candidate.policy_receipt_sha256
        or decision.measurement_adapter_code_sha256
        != candidate.measurement_adapter_code_sha256
        or decision.observed_metrics != candidate.metrics
        or decision.decision_policy_revision
        != identity.video_qc_decision_policy_revision
        or decision.decision_policy_sha256
        != identity.video_qc_decision_policy_sha256
        or decision.decision_adapter_code_sha256
        != identity.video_qc_decision_code_sha256
        or decision.receipt_sha256 != detection.video_qc_receipt_sha256
    ):
        raise VisualFeedbackContractError(
            "runtime video QC decision lineage differs"
        )
    if decision.status != "passed":
        raise VisualFeedbackContractError("runtime video QC did not pass")
    if decision.reason_codes:
        raise VisualFeedbackContractError(
            "passed runtime video QC cannot contain reasons"
        )
    return {
        "candidate_receipt_sha256": candidate.receipt_sha256,
        "decision_receipt_sha256": decision.receipt_sha256,
        "decision_policy_revision": decision.decision_policy_revision,
        "decision_policy_sha256": decision.decision_policy_sha256,
        "decision_code_sha256": decision.decision_adapter_code_sha256,
    }


def _validate_runtime_av_sync_receipt(
    value: object,
    *,
    binding_receipt: SameAttemptBindingReceipt,
    detection: Mapping[str, object],
    bundle: VisualRuntimeBundle,
) -> Mapping[str, str]:
    from ...pronunciation_attempts.adapters.calibrated_runtime_decisions import (
        RuntimeAvSyncDecision,
    )
    from ...pronunciation_attempts.adapters.lip_audio_av_sync import (
        AvSyncCandidateEvidence,
    )
    from ...pronunciation_attempts.phone_window import PolicyValidatedAvSyncOffset

    candidate = detection.av_sync_candidate_evidence
    decision = detection.av_sync_decision
    offset = detection.av_sync_evidence
    if (
        type(candidate) is not AvSyncCandidateEvidence
        or type(decision) is not RuntimeAvSyncDecision
        or type(offset) is not PolicyValidatedAvSyncOffset
        or type(value) is not RuntimeAvSyncDecision
        or value != decision
    ):
        raise VisualFeedbackContractError(
            "typed runtime A/V candidate, decision, and offset are required"
        )
    if decision.schema_version != RUNTIME_AV_SYNC_DECISION_SCHEMA_VERSION:
        raise VisualFeedbackContractError("runtime A/V sync schema differs")
    identity = bundle.pipeline_identity
    if (
        candidate.status != "candidate_unvalidated"
        or candidate.audio_source_sha256 != binding_receipt.audio_source_sha256
        or candidate.video_source_sha256 != binding_receipt.video_source_sha256
        or candidate.landmark_evidence_sha256
        != detection.landmark_evidence_sha256
        or candidate.landmark_model_sha256 != identity.landmark_model_sha256
        or candidate.measurement_revision != identity.av_sync_measurement_revision
        or candidate.measurement_policy_sha256
        != identity.av_sync_measurement_policy_sha256
        or candidate.measurement_adapter_code_sha256
        != identity.av_sync_measurement_code_sha256
        or candidate.receipt_sha256
        != detection.av_sync_candidate_receipt_sha256
    ):
        raise VisualFeedbackContractError(
            "runtime A/V sync raw candidate lineage differs"
        )
    if (
        decision.attempt_id_sha256 != binding_receipt.attempt_id_sha256
        or decision.binding_receipt_sha256 != binding_receipt.receipt_sha256
        or decision.audio_source_sha256 != candidate.audio_source_sha256
        or decision.video_source_sha256 != candidate.video_source_sha256
        or decision.landmark_evidence_sha256
        != candidate.landmark_evidence_sha256
        or decision.landmark_model_sha256 != candidate.landmark_model_sha256
        or decision.source_candidate_receipt_sha256
        != candidate.receipt_sha256
        or decision.source_candidate_lineage_receipt_sha256
        != candidate.lineage_receipt_sha256
        or decision.measurement_revision != candidate.measurement_revision
        or decision.measurement_policy_sha256
        != candidate.measurement_policy_sha256
        or decision.measurement_adapter_code_sha256
        != candidate.measurement_adapter_code_sha256
        or decision.observed_offset_ms != candidate.candidate_offset_ms
        or decision.observed_correlation != candidate.correlation
        or decision.observed_anchors != candidate.anchors
        or decision.decision_policy_revision
        != identity.av_sync_decision_policy_revision
        or decision.decision_policy_sha256
        != identity.av_sync_decision_policy_sha256
        or decision.decision_adapter_code_sha256
        != identity.av_sync_decision_code_sha256
        or decision.receipt_sha256 != detection.av_sync_receipt_sha256
    ):
        raise VisualFeedbackContractError(
            "runtime A/V sync decision lineage differs"
        )
    if decision.status != "synchronized":
        raise VisualFeedbackContractError("runtime A/V sync did not pass")
    if decision.reason_codes:
        raise VisualFeedbackContractError(
            "synchronized runtime A/V receipt cannot contain reasons"
        )
    if (
        offset.attempt_id_sha256 != binding_receipt.attempt_id_sha256
        or offset.binding_receipt_sha256 != binding_receipt.receipt_sha256
        or offset.audio_source_sha256 != candidate.audio_source_sha256
        or offset.video_source_sha256 != candidate.video_source_sha256
        or offset.landmark_evidence_sha256
        != candidate.landmark_evidence_sha256
        or offset.source_candidate_receipt_sha256 != candidate.receipt_sha256
        or offset.measurement_policy_sha256
        != candidate.measurement_policy_sha256
        or offset.decision_policy_revision != decision.decision_policy_revision
        or offset.decision_policy_sha256 != decision.decision_policy_sha256
        or offset.decision_adapter_code_sha256
        != decision.decision_adapter_code_sha256
        or offset.av_sync_decision_receipt_sha256 != decision.receipt_sha256
        or offset.offset_ms != decision.observed_offset_ms
        or offset.receipt_sha256 != detection.av_sync_offset_receipt_sha256
    ):
        raise VisualFeedbackContractError(
            "policy-validated A/V offset lineage differs"
        )
    return {
        "candidate_receipt_sha256": candidate.receipt_sha256,
        "decision_receipt_sha256": decision.receipt_sha256,
        "offset_receipt_sha256": offset.receipt_sha256,
        "decision_policy_revision": decision.decision_policy_revision,
        "decision_policy_sha256": decision.decision_policy_sha256,
        "decision_code_sha256": decision.decision_adapter_code_sha256,
    }


def _validate_runtime_phone_window_receipt(
    value: object,
    *,
    binding_receipt: SameAttemptBindingReceipt,
    detection_receipt_sha256: str,
    phone_anchor_sha256: str,
    video_qc_receipt_sha256: str,
    av_sync_receipt_sha256: str,
    av_sync_offset_receipt_sha256: str,
    context_contract_sha256: str,
    pipeline_identity: VisualRuntimePipelineIdentity,
) -> Mapping[str, str]:
    """Resolve the complete typed decode -> tracking -> runtime DAG.

    A loose mapping or a synthetic top-level digest is deliberately
    insufficient: every executable self-pin and the configured policy identity
    must resolve through the concrete typed evidence emitted by production.
    """

    if type(value) is not DensePhoneWindowEvidence:
        raise VisualFeedbackContractError(
            "dense typed phone-window evidence is required"
        )
    decode = value.decode_receipt
    tracking = value.tracking_receipt
    runtime = value.runtime_receipt
    try:
        decode_type = type(decode)
        tracking_type = type(tracking)
        runtime_type = type(runtime)
        if decode_type.from_json_object(decode.to_json_object()) != decode:
            raise ValueError
        if tracking_type.from_json_object(tracking.to_json_object()) != tracking:
            raise ValueError
        if runtime_type.from_json_object(runtime.to_json_object()) != runtime:
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        raise VisualFeedbackContractError(
            "phone-window typed receipt reload failed"
        ) from None

    expected = {
        "attempt_id_sha256": binding_receipt.attempt_id_sha256,
        "binding_receipt_sha256": binding_receipt.receipt_sha256,
        "detection_receipt_sha256": detection_receipt_sha256,
        "phone_anchor_sha256": phone_anchor_sha256,
        "video_qc_receipt_sha256": video_qc_receipt_sha256,
        "av_sync_receipt_sha256": av_sync_receipt_sha256,
        "context_contract_sha256": context_contract_sha256,
    }
    runtime_values = {
        name: getattr(runtime, name) for name in expected
    }
    if runtime_values != expected:
        raise VisualFeedbackContractError(
            "runtime phone-window lineage differs"
        )
    if (
        decode.attempt_id_sha256 != binding_receipt.attempt_id_sha256
        or decode.binding_receipt_sha256 != binding_receipt.receipt_sha256
        or decode.container_sha256 != binding_receipt.video_source_sha256
        or decode.detection_receipt_sha256 != detection_receipt_sha256
        or decode.phone_anchor_sha256 != phone_anchor_sha256
        or decode.av_sync_receipt_sha256 != av_sync_receipt_sha256
        or decode.av_sync_offset_receipt_sha256
        != av_sync_offset_receipt_sha256
        or decode.context_contract_sha256 != context_contract_sha256
        or tracking.model_sha256 != pipeline_identity.landmark_model_sha256
        or getattr(value.landmark_evidence, "evidence_sha256", None)
        != tracking.landmark_evidence_sha256
        or getattr(value.landmark_evidence, "model_sha256", None)
        != tracking.model_sha256
    ):
        raise VisualFeedbackContractError(
            "phone-window decode/tracking evidence lineage differs"
        )
    if decode.decoder_code_sha256 != pipeline_identity.decoder_sha256:
        raise VisualFeedbackContractError(
            "phone-window decoder executable digest differs"
        )
    if tracking.tracking_code_sha256 != pipeline_identity.cue_extractor_sha256:
        raise VisualFeedbackContractError(
            "phone-window tracking executable digest differs"
        )
    if runtime.phone_window_code_sha256 != pipeline_identity.phone_window_code_sha256:
        raise VisualFeedbackContractError(
            "phone-window runtime executable digest differs"
        )
    if (
        runtime.phone_window_policy_revision
        != pipeline_identity.phone_window_policy_revision
        or runtime.phone_window_policy_sha256
        != pipeline_identity.phone_window_policy_sha256
        or decode.decode_policy_sha256
        != pipeline_identity.phone_window_policy_sha256
    ):
        raise VisualFeedbackContractError(
            "phone-window semantic policy identity differs"
        )
    return {
        "decode_receipt_sha256": decode.receipt_sha256,
        "tracking_receipt_sha256": tracking.receipt_sha256,
        "runtime_receipt_sha256": runtime.receipt_sha256,
        "decoder_code_sha256": decode.decoder_code_sha256,
        "tracking_code_sha256": tracking.tracking_code_sha256,
        "phone_window_code_sha256": runtime.phone_window_code_sha256,
        "phone_window_policy_revision": runtime.phone_window_policy_revision,
        "phone_window_policy_sha256": runtime.phone_window_policy_sha256,
    }


def load_visual_runtime_bundle(value: object) -> VisualRuntimeBundle:
    """Strictly deserialize and cross-validate one runtime bundle."""

    return VisualRuntimeBundle.from_json_object(value)


def load_automated_visual_runtime_eligibility(
    value: object, *, bundle: VisualRuntimeBundle
) -> AutomatedVisualRuntimeEligibilityReceipt:
    """Strictly deserialize an eligibility receipt and resolve it to its bundle."""

    receipt = AutomatedVisualRuntimeEligibilityReceipt.from_json_object(value)
    return validate_automated_visual_runtime_eligibility(
        bundle=bundle, receipt=receipt
    )


__all__ = [
    "ATTEMPT_VISUAL_RELIABILITY_RECEIPT_SCHEMA_VERSION",
    "AUTOMATED_VISUAL_RUNTIME_ELIGIBILITY_RECEIPT_SCHEMA_VERSION",
    "P6_AUTOMATED_PROMOTION_RECEIPT_SCHEMA_VERSION",
    "P8_AUTOMATED_PROMOTION_RECEIPT_SCHEMA_VERSION",
    "RUNTIME_AV_SYNC_DECISION_SCHEMA_VERSION",
    "P6_RUNTIME_EVIDENCE_BUNDLE_SCHEMA_VERSION",
    "RUNTIME_VIDEO_QC_DECISION_SCHEMA_VERSION",
    "RUNTIME_VISUAL_CLAIM_POLICY_SCHEMA_VERSION",
    "RUNTIME_VISUAL_DETECTION_SCHEMA_VERSION",
    "VISUAL_RUNTIME_BUNDLE_SCHEMA_VERSION",
    "VISUAL_RUNTIME_PIPELINE_IDENTITY_SCHEMA_VERSION",
    "AttemptVisualReliabilityReceipt",
    "AutomatedP6PromotionReceipt",
    "AutomatedP8PromotionReceipt",
    "AutomatedVisualRuntimeEligibilityReceipt",
    "RuntimeClaimBinding",
    "RuntimeVisualClaimPolicy",
    "VisualRuntimeBundle",
    "VisualRuntimePipelineIdentity",
    "issue_attempt_visual_reliability",
    "load_automated_visual_runtime_eligibility",
    "load_visual_runtime_bundle",
    "media_capture_contract_sha256",
    "validate_attempt_visual_reliability",
    "validate_automated_visual_runtime_eligibility",
    "visual_observation_contract_sha256",
]
