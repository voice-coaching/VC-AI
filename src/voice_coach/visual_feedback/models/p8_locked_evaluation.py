"""Immutable aggregate-only contracts for the one-shot P8 evaluation.

The models intentionally contain no filesystem locator, media name, speaker
identifier, annotation, or per-item result.  They pin the public AI Hub listing
entries and the frozen inputs that must exist before dataset 538 may be opened.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..errors import VisualFeedbackContractError
from .integrity import (
    require_boolean,
    require_exact_keys,
    require_finite_number,
    require_integer,
    require_mapping,
    require_safe_identifier,
    require_sha256,
    require_utc_timestamp,
    sha256_document,
)
from .p8_evaluation_completeness import (
    P8_EVALUATION_EXCLUSION_POLICY,
    require_p8_evaluation_exclusion_policy,
    validate_p8_evaluation_completeness,
)

P8_READ_ZERO_SEAL_SCHEMA_VERSION = "voice_coach.p8_read_zero_seal.v3"
P8_ACCESS_TRANSACTION_SCHEMA_VERSION = "voice_coach.p8_access_transaction.v1"
P8_LOCKED_EVALUATION_REPORT_SCHEMA_VERSION = (
    "voice_coach.p8_locked_evaluation_report.v3"
)

P8_LOCKED_DATASET_ID = "538"
P8_METRIC_SEMANTICS_VERSION = "voice_coach.p8_metric_semantics.v1"


@dataclass(frozen=True, slots=True)
class P8SourceListingDigestInput:
    """One aggregate public-listing value included in the P8 seal digest."""

    file_key: str
    partition_id: str
    role: str
    listed_size: str

    def __post_init__(self) -> None:
        require_safe_identifier(self.file_key, "file_key")
        require_safe_identifier(self.partition_id, "partition_id")
        require_safe_identifier(self.role, "role")
        require_safe_identifier(self.listed_size, "listed_size")

    def to_json_object(self) -> dict[str, str]:
        return {
            "file_key": self.file_key,
            "partition_id": self.partition_id,
            "role": self.role,
            "listed_size": self.listed_size,
        }

    @classmethod
    def from_json_object(cls, value: object) -> P8SourceListingDigestInput:
        document = require_mapping(value, "source_listing_digest_input")
        require_exact_keys(
            document,
            {"file_key", "partition_id", "role", "listed_size"},
            "source_listing_digest_input",
        )
        return cls(
            file_key=require_safe_identifier(
                document["file_key"], "source_listing_digest_input.file_key"
            ),
            partition_id=require_safe_identifier(
                document["partition_id"],
                "source_listing_digest_input.partition_id",
            ),
            role=require_safe_identifier(
                document["role"], "source_listing_digest_input.role"
            ),
            listed_size=require_safe_identifier(
                document["listed_size"],
                "source_listing_digest_input.listed_size",
            ),
        )


P8_LOCKED_SOURCE_LISTING_INPUTS = (
    P8SourceListingDigestInput(
        file_key="55114",
        partition_id="VS11",
        role="locked_media",
        listed_size="18_GB",
    ),
    P8SourceListingDigestInput(
        file_key="55082",
        partition_id="VL11",
        role="locked_labels",
        listed_size="168_MB",
    ),
)


def _source_listing_document(
    inputs: tuple[P8SourceListingDigestInput, ...],
) -> dict[str, object]:
    return {
        "dataset_id": P8_LOCKED_DATASET_ID,
        "entries": [entry.to_json_object() for entry in inputs],
    }


P8_LOCKED_SOURCE_LISTING_SHA256 = sha256_document(
    _source_listing_document(P8_LOCKED_SOURCE_LISTING_INPUTS)
)


@dataclass(frozen=True, slots=True)
class P8LockedEvaluationThresholds:
    """Pre-access P8 decision thresholds; a new mapping requires a new schema."""

    semantics_version: str
    decode_success_minimum: float
    supported_clip_single_face_coverage_minimum: float
    aggregate_supported_slice_coverage_minimum: float
    av_anchor_availability_minimum: float
    ready_absolute_sync_offset_maximum_ms: float
    claim_policy_violation_maximum: int
    forbidden_claim_fixture_violation_maximum: int

    def __post_init__(self) -> None:
        if self.semantics_version != P8_METRIC_SEMANTICS_VERSION:
            raise VisualFeedbackContractError("P8 metric semantics differ")
        expected_numbers = {
            "decode_success_minimum": 0.98,
            "supported_clip_single_face_coverage_minimum": 0.90,
            "aggregate_supported_slice_coverage_minimum": 0.90,
            "av_anchor_availability_minimum": 0.80,
            "ready_absolute_sync_offset_maximum_ms": 120.0,
        }
        for field_name, expected in expected_numbers.items():
            actual = require_finite_number(getattr(self, field_name), field_name)
            if actual != expected:
                raise VisualFeedbackContractError(
                    f"P8 frozen threshold {field_name} differs"
                )
        if self.claim_policy_violation_maximum != 0:
            raise VisualFeedbackContractError(
                "P8 claim-policy violation maximum must be 0"
            )
        if self.forbidden_claim_fixture_violation_maximum != 0:
            raise VisualFeedbackContractError(
                "P8 forbidden-claim violation maximum must be 0"
            )

    def to_json_object(self) -> dict[str, object]:
        return {
            "semantics_version": self.semantics_version,
            "decode_success_minimum": self.decode_success_minimum,
            "supported_clip_single_face_coverage_minimum": (
                self.supported_clip_single_face_coverage_minimum
            ),
            "aggregate_supported_slice_coverage_minimum": (
                self.aggregate_supported_slice_coverage_minimum
            ),
            "av_anchor_availability_minimum": self.av_anchor_availability_minimum,
            "ready_absolute_sync_offset_maximum_ms": (
                self.ready_absolute_sync_offset_maximum_ms
            ),
            "claim_policy_violation_maximum": (
                self.claim_policy_violation_maximum
            ),
            "forbidden_claim_fixture_violation_maximum": (
                self.forbidden_claim_fixture_violation_maximum
            ),
        }

    @classmethod
    def from_json_object(cls, value: object) -> P8LockedEvaluationThresholds:
        document = require_mapping(value, "p8_locked_evaluation_thresholds")
        require_exact_keys(
            document,
            {
                "semantics_version",
                "decode_success_minimum",
                "supported_clip_single_face_coverage_minimum",
                "aggregate_supported_slice_coverage_minimum",
                "av_anchor_availability_minimum",
                "ready_absolute_sync_offset_maximum_ms",
                "claim_policy_violation_maximum",
                "forbidden_claim_fixture_violation_maximum",
            },
            "p8_locked_evaluation_thresholds",
        )
        return cls(
            semantics_version=require_safe_identifier(
                document["semantics_version"], "semantics_version"
            ),
            decode_success_minimum=require_finite_number(
                document["decode_success_minimum"], "decode_success_minimum"
            ),
            supported_clip_single_face_coverage_minimum=require_finite_number(
                document["supported_clip_single_face_coverage_minimum"],
                "supported_clip_single_face_coverage_minimum",
            ),
            aggregate_supported_slice_coverage_minimum=require_finite_number(
                document["aggregate_supported_slice_coverage_minimum"],
                "aggregate_supported_slice_coverage_minimum",
            ),
            av_anchor_availability_minimum=require_finite_number(
                document["av_anchor_availability_minimum"],
                "av_anchor_availability_minimum",
            ),
            ready_absolute_sync_offset_maximum_ms=require_finite_number(
                document["ready_absolute_sync_offset_maximum_ms"],
                "ready_absolute_sync_offset_maximum_ms",
            ),
            claim_policy_violation_maximum=require_integer(
                document["claim_policy_violation_maximum"],
                "claim_policy_violation_maximum",
                minimum=0,
            ),
            forbidden_claim_fixture_violation_maximum=require_integer(
                document["forbidden_claim_fixture_violation_maximum"],
                "forbidden_claim_fixture_violation_maximum",
                minimum=0,
            ),
        )


P8_LOCKED_EVALUATION_THRESHOLDS = P8LockedEvaluationThresholds(
    semantics_version=P8_METRIC_SEMANTICS_VERSION,
    decode_success_minimum=0.98,
    supported_clip_single_face_coverage_minimum=0.90,
    aggregate_supported_slice_coverage_minimum=0.90,
    av_anchor_availability_minimum=0.80,
    ready_absolute_sync_offset_maximum_ms=120.0,
    claim_policy_violation_maximum=0,
    forbidden_claim_fixture_violation_maximum=0,
)
P8_LOCKED_EVALUATION_THRESHOLDS_SHA256 = sha256_document(
    P8_LOCKED_EVALUATION_THRESHOLDS.to_json_object()
)


class P8AccessAuthorizationState(str, Enum):
    """Durable state written before any locked source-open attempt."""

    CONSUMED = "consumed_before_source_open"


class P8LockedEvaluationStatus(str, Enum):
    """Observed terminal status for an aggregate-only one-shot evaluation."""

    COMPLETED = "completed_aggregate_only"
    FAILED_CLOSED = "failed_closed"
    NOT_EXECUTED = "authorization_consumed_without_source_open"


@dataclass(frozen=True, slots=True)
class P8ReadZeroSeal:
    """Self-digested proof that frozen inputs preceded dataset-538 access.

    The CLI builder fills every artifact, model, policy, and code SHA field from
    the exact bytes of a safely opened local regular file.  No source locator is
    retained in this aggregate-only document.
    """

    seal_id: str
    protocol_revision: str
    source_revision: str
    dataset_id: str
    source_listing_digest_inputs: tuple[P8SourceListingDigestInput, ...]
    source_listing_sha256: str
    evaluation_thresholds: P8LockedEvaluationThresholds
    evaluation_thresholds_sha256: str
    p6_evidence_receipt_sha256: str
    calibration_artifact_sha256: str
    claim_catalog_sha256: str
    claim_policy_sha256: str
    media_capture_contract_id: str
    media_capture_contract_sha256: str
    visual_observation_contract_id: str
    visual_observation_contract_sha256: str
    decoder_code_sha256: str
    tracker_code_sha256: str
    tracker_adapter_code_sha256: str
    landmark_model_sha256: str
    normalizer_code_sha256: str
    cue_extractor_code_sha256: str
    video_qc_measurement_code_sha256: str
    video_qc_measurement_policy_sha256: str
    video_qc_decision_code_sha256: str
    video_qc_decision_policy_revision: str
    video_qc_decision_policy_sha256: str
    av_sync_measurement_code_sha256: str
    av_sync_measurement_policy_sha256: str
    av_sync_decision_code_sha256: str
    av_sync_decision_policy_revision: str
    av_sync_decision_policy_sha256: str
    phone_window_code_sha256: str
    phone_window_policy_revision: str
    phone_window_policy_sha256: str
    sealed_at_utc: str
    access_count: int
    sealed_before_source_access: bool
    aggregate_only: bool
    seal_sha256: str
    schema_version: str = P8_READ_ZERO_SEAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P8_READ_ZERO_SEAL_SCHEMA_VERSION:
            raise VisualFeedbackContractError("P8 read-zero seal schema differs")
        require_safe_identifier(self.seal_id, "seal_id")
        require_safe_identifier(self.protocol_revision, "protocol_revision")
        require_safe_identifier(self.source_revision, "source_revision")
        require_safe_identifier(
            self.media_capture_contract_id, "media_capture_contract_id"
        )
        require_safe_identifier(
            self.visual_observation_contract_id,
            "visual_observation_contract_id",
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
        if self.dataset_id != P8_LOCKED_DATASET_ID:
            raise VisualFeedbackContractError("P8 locked dataset must be 538")
        if self.source_listing_digest_inputs != P8_LOCKED_SOURCE_LISTING_INPUTS:
            raise VisualFeedbackContractError("P8 locked source listing differs")
        require_sha256(self.source_listing_sha256, "source_listing_sha256")
        if self.source_listing_sha256 != P8_LOCKED_SOURCE_LISTING_SHA256:
            raise VisualFeedbackContractError("P8 source listing digest differs")
        if self.evaluation_thresholds != P8_LOCKED_EVALUATION_THRESHOLDS:
            raise VisualFeedbackContractError("P8 frozen thresholds differ")
        if (
            self.evaluation_thresholds_sha256
            != P8_LOCKED_EVALUATION_THRESHOLDS_SHA256
        ):
            raise VisualFeedbackContractError("P8 frozen threshold digest differs")
        for field_name in (
            "p6_evidence_receipt_sha256",
            "calibration_artifact_sha256",
            "claim_catalog_sha256",
            "claim_policy_sha256",
            "media_capture_contract_sha256",
            "visual_observation_contract_sha256",
            "decoder_code_sha256",
            "tracker_code_sha256",
            "tracker_adapter_code_sha256",
            "landmark_model_sha256",
            "normalizer_code_sha256",
            "cue_extractor_code_sha256",
            "video_qc_measurement_code_sha256",
            "video_qc_measurement_policy_sha256",
            "video_qc_decision_code_sha256",
            "video_qc_decision_policy_sha256",
            "av_sync_measurement_code_sha256",
            "av_sync_measurement_policy_sha256",
            "av_sync_decision_code_sha256",
            "av_sync_decision_policy_sha256",
            "phone_window_code_sha256",
            "phone_window_policy_sha256",
            "seal_sha256",
        ):
            require_sha256(getattr(self, field_name), field_name)
        if self.media_capture_contract_sha256 != sha256_document(
            {
                "schema_version": "voice_coach.media_capture_contract_reference.v1",
                "contract_id": self.media_capture_contract_id,
            }
        ):
            raise VisualFeedbackContractError(
                "P8 media capture contract digest differs"
            )
        if self.visual_observation_contract_sha256 != sha256_document(
            {
                "schema_version": (
                    "voice_coach.visual_observation_contract_reference.v1"
                ),
                "contract_id": self.visual_observation_contract_id,
            }
        ):
            raise VisualFeedbackContractError(
                "P8 visual observation contract digest differs"
            )
        require_utc_timestamp(self.sealed_at_utc, "sealed_at_utc")
        if require_integer(self.access_count, "access_count", minimum=0) != 0:
            raise VisualFeedbackContractError("P8 read-zero seal access_count must be 0")
        if require_boolean(
            self.sealed_before_source_access, "sealed_before_source_access"
        ) is not True:
            raise VisualFeedbackContractError(
                "P8 seal must precede all locked source access"
            )
        if require_boolean(self.aggregate_only, "aggregate_only") is not True:
            raise VisualFeedbackContractError("P8 state must be aggregate-only")
        if self.seal_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("P8 read-zero seal digest differs")

    @classmethod
    def issue(
        cls,
        *,
        seal_id: str,
        protocol_revision: str,
        source_revision: str,
        p6_evidence_receipt_sha256: str,
        calibration_artifact_sha256: str,
        claim_catalog_sha256: str,
        claim_policy_sha256: str,
        media_capture_contract_id: str,
        media_capture_contract_sha256: str,
        visual_observation_contract_id: str,
        visual_observation_contract_sha256: str,
        decoder_code_sha256: str,
        tracker_code_sha256: str,
        tracker_adapter_code_sha256: str,
        landmark_model_sha256: str,
        normalizer_code_sha256: str,
        cue_extractor_code_sha256: str,
        video_qc_measurement_code_sha256: str,
        video_qc_measurement_policy_sha256: str,
        video_qc_decision_code_sha256: str,
        video_qc_decision_policy_revision: str,
        video_qc_decision_policy_sha256: str,
        av_sync_measurement_code_sha256: str,
        av_sync_measurement_policy_sha256: str,
        av_sync_decision_code_sha256: str,
        av_sync_decision_policy_revision: str,
        av_sync_decision_policy_sha256: str,
        phone_window_code_sha256: str,
        phone_window_policy_revision: str,
        phone_window_policy_sha256: str,
        sealed_at_utc: str,
    ) -> P8ReadZeroSeal:
        unsigned = {
            "schema_version": P8_READ_ZERO_SEAL_SCHEMA_VERSION,
            "seal_id": seal_id,
            "protocol_revision": protocol_revision,
            "source_revision": source_revision,
            "dataset_id": P8_LOCKED_DATASET_ID,
            "source_listing_digest_inputs": [
                entry.to_json_object()
                for entry in P8_LOCKED_SOURCE_LISTING_INPUTS
            ],
            "source_listing_sha256": P8_LOCKED_SOURCE_LISTING_SHA256,
            "evaluation_thresholds": (
                P8_LOCKED_EVALUATION_THRESHOLDS.to_json_object()
            ),
            "evaluation_thresholds_sha256": (
                P8_LOCKED_EVALUATION_THRESHOLDS_SHA256
            ),
            "p6_evidence_receipt_sha256": p6_evidence_receipt_sha256,
            "calibration_artifact_sha256": calibration_artifact_sha256,
            "claim_catalog_sha256": claim_catalog_sha256,
            "claim_policy_sha256": claim_policy_sha256,
            "media_capture_contract_id": media_capture_contract_id,
            "media_capture_contract_sha256": media_capture_contract_sha256,
            "visual_observation_contract_id": visual_observation_contract_id,
            "visual_observation_contract_sha256": (
                visual_observation_contract_sha256
            ),
            "decoder_code_sha256": decoder_code_sha256,
            "tracker_code_sha256": tracker_code_sha256,
            "tracker_adapter_code_sha256": tracker_adapter_code_sha256,
            "landmark_model_sha256": landmark_model_sha256,
            "normalizer_code_sha256": normalizer_code_sha256,
            "cue_extractor_code_sha256": cue_extractor_code_sha256,
            "video_qc_measurement_code_sha256": (
                video_qc_measurement_code_sha256
            ),
            "video_qc_measurement_policy_sha256": (
                video_qc_measurement_policy_sha256
            ),
            "video_qc_decision_code_sha256": video_qc_decision_code_sha256,
            "video_qc_decision_policy_revision": (
                video_qc_decision_policy_revision
            ),
            "video_qc_decision_policy_sha256": (
                video_qc_decision_policy_sha256
            ),
            "av_sync_measurement_code_sha256": av_sync_measurement_code_sha256,
            "av_sync_measurement_policy_sha256": (
                av_sync_measurement_policy_sha256
            ),
            "av_sync_decision_code_sha256": av_sync_decision_code_sha256,
            "av_sync_decision_policy_revision": (
                av_sync_decision_policy_revision
            ),
            "av_sync_decision_policy_sha256": av_sync_decision_policy_sha256,
            "phone_window_code_sha256": phone_window_code_sha256,
            "phone_window_policy_revision": phone_window_policy_revision,
            "phone_window_policy_sha256": phone_window_policy_sha256,
            "sealed_at_utc": sealed_at_utc,
            "access_count": 0,
            "sealed_before_source_access": True,
            "aggregate_only": True,
        }
        return cls(
            seal_id=seal_id,
            protocol_revision=protocol_revision,
            source_revision=source_revision,
            dataset_id=P8_LOCKED_DATASET_ID,
            source_listing_digest_inputs=P8_LOCKED_SOURCE_LISTING_INPUTS,
            source_listing_sha256=P8_LOCKED_SOURCE_LISTING_SHA256,
            evaluation_thresholds=P8_LOCKED_EVALUATION_THRESHOLDS,
            evaluation_thresholds_sha256=(
                P8_LOCKED_EVALUATION_THRESHOLDS_SHA256
            ),
            p6_evidence_receipt_sha256=p6_evidence_receipt_sha256,
            calibration_artifact_sha256=calibration_artifact_sha256,
            claim_catalog_sha256=claim_catalog_sha256,
            claim_policy_sha256=claim_policy_sha256,
            media_capture_contract_id=media_capture_contract_id,
            media_capture_contract_sha256=media_capture_contract_sha256,
            visual_observation_contract_id=visual_observation_contract_id,
            visual_observation_contract_sha256=(
                visual_observation_contract_sha256
            ),
            decoder_code_sha256=decoder_code_sha256,
            tracker_code_sha256=tracker_code_sha256,
            tracker_adapter_code_sha256=tracker_adapter_code_sha256,
            landmark_model_sha256=landmark_model_sha256,
            normalizer_code_sha256=normalizer_code_sha256,
            cue_extractor_code_sha256=cue_extractor_code_sha256,
            video_qc_measurement_code_sha256=video_qc_measurement_code_sha256,
            video_qc_measurement_policy_sha256=(
                video_qc_measurement_policy_sha256
            ),
            video_qc_decision_code_sha256=video_qc_decision_code_sha256,
            video_qc_decision_policy_revision=(
                video_qc_decision_policy_revision
            ),
            video_qc_decision_policy_sha256=video_qc_decision_policy_sha256,
            av_sync_measurement_code_sha256=av_sync_measurement_code_sha256,
            av_sync_measurement_policy_sha256=av_sync_measurement_policy_sha256,
            av_sync_decision_code_sha256=av_sync_decision_code_sha256,
            av_sync_decision_policy_revision=av_sync_decision_policy_revision,
            av_sync_decision_policy_sha256=av_sync_decision_policy_sha256,
            phone_window_code_sha256=phone_window_code_sha256,
            phone_window_policy_revision=phone_window_policy_revision,
            phone_window_policy_sha256=phone_window_policy_sha256,
            sealed_at_utc=sealed_at_utc,
            access_count=0,
            sealed_before_source_access=True,
            aggregate_only=True,
            seal_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "seal_id": self.seal_id,
            "protocol_revision": self.protocol_revision,
            "source_revision": self.source_revision,
            "dataset_id": self.dataset_id,
            "source_listing_digest_inputs": [
                entry.to_json_object()
                for entry in self.source_listing_digest_inputs
            ],
            "source_listing_sha256": self.source_listing_sha256,
            "evaluation_thresholds": self.evaluation_thresholds.to_json_object(),
            "evaluation_thresholds_sha256": self.evaluation_thresholds_sha256,
            "p6_evidence_receipt_sha256": self.p6_evidence_receipt_sha256,
            "calibration_artifact_sha256": self.calibration_artifact_sha256,
            "claim_catalog_sha256": self.claim_catalog_sha256,
            "claim_policy_sha256": self.claim_policy_sha256,
            "media_capture_contract_id": self.media_capture_contract_id,
            "media_capture_contract_sha256": self.media_capture_contract_sha256,
            "visual_observation_contract_id": self.visual_observation_contract_id,
            "visual_observation_contract_sha256": (
                self.visual_observation_contract_sha256
            ),
            "decoder_code_sha256": self.decoder_code_sha256,
            "tracker_code_sha256": self.tracker_code_sha256,
            "tracker_adapter_code_sha256": self.tracker_adapter_code_sha256,
            "landmark_model_sha256": self.landmark_model_sha256,
            "normalizer_code_sha256": self.normalizer_code_sha256,
            "cue_extractor_code_sha256": self.cue_extractor_code_sha256,
            "video_qc_measurement_code_sha256": (
                self.video_qc_measurement_code_sha256
            ),
            "video_qc_measurement_policy_sha256": (
                self.video_qc_measurement_policy_sha256
            ),
            "video_qc_decision_code_sha256": self.video_qc_decision_code_sha256,
            "video_qc_decision_policy_revision": (
                self.video_qc_decision_policy_revision
            ),
            "video_qc_decision_policy_sha256": (
                self.video_qc_decision_policy_sha256
            ),
            "av_sync_measurement_code_sha256": (
                self.av_sync_measurement_code_sha256
            ),
            "av_sync_measurement_policy_sha256": (
                self.av_sync_measurement_policy_sha256
            ),
            "av_sync_decision_code_sha256": self.av_sync_decision_code_sha256,
            "av_sync_decision_policy_revision": (
                self.av_sync_decision_policy_revision
            ),
            "av_sync_decision_policy_sha256": (
                self.av_sync_decision_policy_sha256
            ),
            "phone_window_code_sha256": self.phone_window_code_sha256,
            "phone_window_policy_revision": self.phone_window_policy_revision,
            "phone_window_policy_sha256": self.phone_window_policy_sha256,
            "sealed_at_utc": self.sealed_at_utc,
            "access_count": self.access_count,
            "sealed_before_source_access": self.sealed_before_source_access,
            "aggregate_only": self.aggregate_only,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "seal_sha256": self.seal_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> P8ReadZeroSeal:
        document = require_mapping(value, "p8_read_zero_seal")
        require_exact_keys(
            document,
            {
                "schema_version",
                "seal_id",
                "protocol_revision",
                "source_revision",
                "dataset_id",
                "source_listing_digest_inputs",
                "source_listing_sha256",
                "evaluation_thresholds",
                "evaluation_thresholds_sha256",
                "p6_evidence_receipt_sha256",
                "calibration_artifact_sha256",
                "claim_catalog_sha256",
                "claim_policy_sha256",
                "media_capture_contract_id",
                "media_capture_contract_sha256",
                "visual_observation_contract_id",
                "visual_observation_contract_sha256",
                "decoder_code_sha256",
                "tracker_code_sha256",
                "tracker_adapter_code_sha256",
                "landmark_model_sha256",
                "normalizer_code_sha256",
                "cue_extractor_code_sha256",
                "video_qc_measurement_code_sha256",
                "video_qc_measurement_policy_sha256",
                "video_qc_decision_code_sha256",
                "video_qc_decision_policy_revision",
                "video_qc_decision_policy_sha256",
                "av_sync_measurement_code_sha256",
                "av_sync_measurement_policy_sha256",
                "av_sync_decision_code_sha256",
                "av_sync_decision_policy_revision",
                "av_sync_decision_policy_sha256",
                "phone_window_code_sha256",
                "phone_window_policy_revision",
                "phone_window_policy_sha256",
                "sealed_at_utc",
                "access_count",
                "sealed_before_source_access",
                "aggregate_only",
                "seal_sha256",
            },
            "p8_read_zero_seal",
        )
        inputs_value = document["source_listing_digest_inputs"]
        if isinstance(inputs_value, (str, bytes)) or not isinstance(
            inputs_value, Sequence
        ):
            raise VisualFeedbackContractError(
                "source_listing_digest_inputs must be an array"
            )
        inputs = tuple(
            P8SourceListingDigestInput.from_json_object(entry)
            for entry in inputs_value
        )
        return cls(
            seal_id=require_safe_identifier(document["seal_id"], "seal_id"),
            protocol_revision=require_safe_identifier(
                document["protocol_revision"], "protocol_revision"
            ),
            source_revision=require_safe_identifier(
                document["source_revision"], "source_revision"
            ),
            dataset_id=require_safe_identifier(document["dataset_id"], "dataset_id"),
            source_listing_digest_inputs=inputs,
            source_listing_sha256=require_sha256(
                document["source_listing_sha256"], "source_listing_sha256"
            ),
            evaluation_thresholds=P8LockedEvaluationThresholds.from_json_object(
                document["evaluation_thresholds"]
            ),
            evaluation_thresholds_sha256=require_sha256(
                document["evaluation_thresholds_sha256"],
                "evaluation_thresholds_sha256",
            ),
            p6_evidence_receipt_sha256=require_sha256(
                document["p6_evidence_receipt_sha256"],
                "p6_evidence_receipt_sha256",
            ),
            calibration_artifact_sha256=require_sha256(
                document["calibration_artifact_sha256"],
                "calibration_artifact_sha256",
            ),
            claim_catalog_sha256=require_sha256(
                document["claim_catalog_sha256"], "claim_catalog_sha256"
            ),
            claim_policy_sha256=require_sha256(
                document["claim_policy_sha256"], "claim_policy_sha256"
            ),
            media_capture_contract_id=require_safe_identifier(
                document["media_capture_contract_id"],
                "media_capture_contract_id",
            ),
            media_capture_contract_sha256=require_sha256(
                document["media_capture_contract_sha256"],
                "media_capture_contract_sha256",
            ),
            visual_observation_contract_id=require_safe_identifier(
                document["visual_observation_contract_id"],
                "visual_observation_contract_id",
            ),
            visual_observation_contract_sha256=require_sha256(
                document["visual_observation_contract_sha256"],
                "visual_observation_contract_sha256",
            ),
            decoder_code_sha256=require_sha256(
                document["decoder_code_sha256"], "decoder_code_sha256"
            ),
            tracker_code_sha256=require_sha256(
                document["tracker_code_sha256"], "tracker_code_sha256"
            ),
            tracker_adapter_code_sha256=require_sha256(
                document["tracker_adapter_code_sha256"],
                "tracker_adapter_code_sha256",
            ),
            landmark_model_sha256=require_sha256(
                document["landmark_model_sha256"], "landmark_model_sha256"
            ),
            normalizer_code_sha256=require_sha256(
                document["normalizer_code_sha256"], "normalizer_code_sha256"
            ),
            cue_extractor_code_sha256=require_sha256(
                document["cue_extractor_code_sha256"],
                "cue_extractor_code_sha256",
            ),
            video_qc_measurement_code_sha256=require_sha256(
                document["video_qc_measurement_code_sha256"],
                "video_qc_measurement_code_sha256",
            ),
            video_qc_measurement_policy_sha256=require_sha256(
                document["video_qc_measurement_policy_sha256"],
                "video_qc_measurement_policy_sha256",
            ),
            video_qc_decision_code_sha256=require_sha256(
                document["video_qc_decision_code_sha256"],
                "video_qc_decision_code_sha256",
            ),
            video_qc_decision_policy_revision=require_safe_identifier(
                document["video_qc_decision_policy_revision"],
                "video_qc_decision_policy_revision",
            ),
            video_qc_decision_policy_sha256=require_sha256(
                document["video_qc_decision_policy_sha256"],
                "video_qc_decision_policy_sha256",
            ),
            av_sync_measurement_code_sha256=require_sha256(
                document["av_sync_measurement_code_sha256"],
                "av_sync_measurement_code_sha256",
            ),
            av_sync_measurement_policy_sha256=require_sha256(
                document["av_sync_measurement_policy_sha256"],
                "av_sync_measurement_policy_sha256",
            ),
            av_sync_decision_code_sha256=require_sha256(
                document["av_sync_decision_code_sha256"],
                "av_sync_decision_code_sha256",
            ),
            av_sync_decision_policy_revision=require_safe_identifier(
                document["av_sync_decision_policy_revision"],
                "av_sync_decision_policy_revision",
            ),
            av_sync_decision_policy_sha256=require_sha256(
                document["av_sync_decision_policy_sha256"],
                "av_sync_decision_policy_sha256",
            ),
            phone_window_code_sha256=require_sha256(
                document["phone_window_code_sha256"],
                "phone_window_code_sha256",
            ),
            phone_window_policy_revision=require_safe_identifier(
                document["phone_window_policy_revision"],
                "phone_window_policy_revision",
            ),
            phone_window_policy_sha256=require_sha256(
                document["phone_window_policy_sha256"],
                "phone_window_policy_sha256",
            ),
            sealed_at_utc=require_utc_timestamp(
                document["sealed_at_utc"], "sealed_at_utc"
            ),
            access_count=require_integer(
                document["access_count"], "access_count", minimum=0
            ),
            sealed_before_source_access=require_boolean(
                document["sealed_before_source_access"],
                "sealed_before_source_access",
            ),
            aggregate_only=require_boolean(
                document["aggregate_only"], "aggregate_only"
            ),
            seal_sha256=require_sha256(document["seal_sha256"], "seal_sha256"),
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
        )


@dataclass(frozen=True, slots=True)
class P8AccessTransaction:
    """One durable authorization consumption, persisted before source open."""

    transaction_id: str
    seal_sha256: str
    dataset_id: str
    source_listing_sha256: str
    file_keys: tuple[str, ...]
    consumed_at_utc: str
    authorization_state: P8AccessAuthorizationState
    consumed_before_source_open: bool
    access_count: int
    aggregate_only: bool
    transaction_sha256: str
    schema_version: str = P8_ACCESS_TRANSACTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P8_ACCESS_TRANSACTION_SCHEMA_VERSION:
            raise VisualFeedbackContractError("P8 access transaction schema differs")
        require_safe_identifier(self.transaction_id, "transaction_id")
        require_sha256(self.seal_sha256, "seal_sha256")
        if self.dataset_id != P8_LOCKED_DATASET_ID:
            raise VisualFeedbackContractError("P8 access dataset must be 538")
        if self.source_listing_sha256 != P8_LOCKED_SOURCE_LISTING_SHA256:
            raise VisualFeedbackContractError("P8 access source listing differs")
        if self.file_keys != tuple(
            entry.file_key for entry in P8_LOCKED_SOURCE_LISTING_INPUTS
        ):
            raise VisualFeedbackContractError("P8 access file keys differ")
        require_utc_timestamp(self.consumed_at_utc, "consumed_at_utc")
        if self.authorization_state is not P8AccessAuthorizationState.CONSUMED:
            raise VisualFeedbackContractError("P8 access must be consumed")
        if require_boolean(
            self.consumed_before_source_open, "consumed_before_source_open"
        ) is not True:
            raise VisualFeedbackContractError(
                "P8 authorization must be consumed before source open"
            )
        if require_integer(self.access_count, "access_count", minimum=1) != 1:
            raise VisualFeedbackContractError("P8 access_count must be exactly 1")
        if require_boolean(self.aggregate_only, "aggregate_only") is not True:
            raise VisualFeedbackContractError("P8 state must be aggregate-only")
        require_sha256(self.transaction_sha256, "transaction_sha256")
        if self.transaction_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("P8 access transaction digest differs")

    @classmethod
    def issue(
        cls,
        *,
        transaction_id: str,
        seal: P8ReadZeroSeal,
        consumed_at_utc: str,
    ) -> P8AccessTransaction:
        unsigned = {
            "schema_version": P8_ACCESS_TRANSACTION_SCHEMA_VERSION,
            "transaction_id": transaction_id,
            "seal_sha256": seal.seal_sha256,
            "dataset_id": seal.dataset_id,
            "source_listing_sha256": seal.source_listing_sha256,
            "file_keys": [
                entry.file_key for entry in seal.source_listing_digest_inputs
            ],
            "consumed_at_utc": consumed_at_utc,
            "authorization_state": P8AccessAuthorizationState.CONSUMED.value,
            "consumed_before_source_open": True,
            "access_count": 1,
            "aggregate_only": True,
        }
        return cls(
            transaction_id=transaction_id,
            seal_sha256=seal.seal_sha256,
            dataset_id=seal.dataset_id,
            source_listing_sha256=seal.source_listing_sha256,
            file_keys=tuple(
                entry.file_key for entry in seal.source_listing_digest_inputs
            ),
            consumed_at_utc=consumed_at_utc,
            authorization_state=P8AccessAuthorizationState.CONSUMED,
            consumed_before_source_open=True,
            access_count=1,
            aggregate_only=True,
            transaction_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "transaction_id": self.transaction_id,
            "seal_sha256": self.seal_sha256,
            "dataset_id": self.dataset_id,
            "source_listing_sha256": self.source_listing_sha256,
            "file_keys": list(self.file_keys),
            "consumed_at_utc": self.consumed_at_utc,
            "authorization_state": self.authorization_state.value,
            "consumed_before_source_open": self.consumed_before_source_open,
            "access_count": self.access_count,
            "aggregate_only": self.aggregate_only,
        }

    def to_json_object(self) -> dict[str, object]:
        return {
            **self.unsigned_json_object(),
            "transaction_sha256": self.transaction_sha256,
        }

    @classmethod
    def from_json_object(cls, value: object) -> P8AccessTransaction:
        document = require_mapping(value, "p8_access_transaction")
        require_exact_keys(
            document,
            {
                "schema_version",
                "transaction_id",
                "seal_sha256",
                "dataset_id",
                "source_listing_sha256",
                "file_keys",
                "consumed_at_utc",
                "authorization_state",
                "consumed_before_source_open",
                "access_count",
                "aggregate_only",
                "transaction_sha256",
            },
            "p8_access_transaction",
        )
        file_keys_value = document["file_keys"]
        if isinstance(file_keys_value, (str, bytes)) or not isinstance(
            file_keys_value, Sequence
        ):
            raise VisualFeedbackContractError("file_keys must be an array")
        try:
            authorization_state = P8AccessAuthorizationState(
                require_safe_identifier(
                    document["authorization_state"], "authorization_state"
                )
            )
        except ValueError as error:
            raise VisualFeedbackContractError(
                "P8 access authorization_state differs"
            ) from error
        return cls(
            transaction_id=require_safe_identifier(
                document["transaction_id"], "transaction_id"
            ),
            seal_sha256=require_sha256(document["seal_sha256"], "seal_sha256"),
            dataset_id=require_safe_identifier(document["dataset_id"], "dataset_id"),
            source_listing_sha256=require_sha256(
                document["source_listing_sha256"], "source_listing_sha256"
            ),
            file_keys=tuple(
                require_safe_identifier(value, "file_keys[]")
                for value in file_keys_value
            ),
            consumed_at_utc=require_utc_timestamp(
                document["consumed_at_utc"], "consumed_at_utc"
            ),
            authorization_state=authorization_state,
            consumed_before_source_open=require_boolean(
                document["consumed_before_source_open"],
                "consumed_before_source_open",
            ),
            access_count=require_integer(
                document["access_count"], "access_count", minimum=1
            ),
            aggregate_only=require_boolean(
                document["aggregate_only"], "aggregate_only"
            ),
            transaction_sha256=require_sha256(
                document["transaction_sha256"], "transaction_sha256"
            ),
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
        )


@dataclass(frozen=True, slots=True)
class P8LockedEvaluationReport:
    """Self-digested aggregate report or fail-closed report skeleton."""

    report_id: str
    protocol_revision: str
    seal_sha256: str
    access_transaction_sha256: str
    dataset_id: str
    source_listing_sha256: str
    locked_source_manifest_sha256: str | None
    status: P8LockedEvaluationStatus
    source_open_attempted: bool
    access_count: int
    admitted_item_count: int
    paired_item_count: int
    evaluated_item_count: int
    evaluation_exclusion_count: int
    evaluation_exclusion_policy: str
    successful_item_count: int
    failed_item_count: int
    aggregate_metrics_sha256: str | None
    reason_codes: tuple[str, ...]
    aggregate_only: bool
    report_sha256: str
    schema_version: str = P8_LOCKED_EVALUATION_REPORT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P8_LOCKED_EVALUATION_REPORT_SCHEMA_VERSION:
            raise VisualFeedbackContractError(
                "P8 locked evaluation report schema differs"
            )
        require_safe_identifier(self.report_id, "report_id")
        require_safe_identifier(self.protocol_revision, "protocol_revision")
        require_sha256(self.seal_sha256, "seal_sha256")
        require_sha256(
            self.access_transaction_sha256, "access_transaction_sha256"
        )
        if self.dataset_id != P8_LOCKED_DATASET_ID:
            raise VisualFeedbackContractError("P8 report dataset must be 538")
        if self.source_listing_sha256 != P8_LOCKED_SOURCE_LISTING_SHA256:
            raise VisualFeedbackContractError("P8 report source listing differs")
        if self.locked_source_manifest_sha256 is not None:
            require_sha256(
                self.locked_source_manifest_sha256,
                "locked_source_manifest_sha256",
            )
            if self.locked_source_manifest_sha256 == self.source_listing_sha256:
                raise VisualFeedbackContractError(
                    "P8 report actual manifest cannot be the public listing"
                )
        require_boolean(self.source_open_attempted, "source_open_attempted")
        if require_integer(self.access_count, "access_count", minimum=1) != 1:
            raise VisualFeedbackContractError("P8 report access_count must be 1")
        admitted, paired, evaluated, exclusions = (
            validate_p8_evaluation_completeness(
                admitted_item_count=self.admitted_item_count,
                paired_item_count=self.paired_item_count,
                evaluated_item_count=self.evaluated_item_count,
                evaluation_exclusion_count=self.evaluation_exclusion_count,
                allow_empty=True,
            )
        )
        require_p8_evaluation_exclusion_policy(
            self.evaluation_exclusion_policy
        )
        successful = require_integer(
            self.successful_item_count, "successful_item_count", minimum=0
        )
        failed = require_integer(
            self.failed_item_count, "failed_item_count", minimum=0
        )
        if evaluated != successful + failed:
            raise VisualFeedbackContractError("P8 aggregate item counts differ")
        if self.aggregate_metrics_sha256 is not None:
            require_sha256(
                self.aggregate_metrics_sha256, "aggregate_metrics_sha256"
            )
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise VisualFeedbackContractError("reason_codes must be unique")
        for reason_code in self.reason_codes:
            require_safe_identifier(reason_code, "reason_codes[]")
        if require_boolean(self.aggregate_only, "aggregate_only") is not True:
            raise VisualFeedbackContractError("P8 report must be aggregate-only")
        self._validate_status_invariants(
            admitted=admitted,
            paired=paired,
            evaluated=evaluated,
            exclusions=exclusions,
        )
        require_sha256(self.report_sha256, "report_sha256")
        if self.report_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("P8 locked report digest differs")

    def _validate_status_invariants(
        self,
        *,
        admitted: int,
        paired: int,
        evaluated: int,
        exclusions: int,
    ) -> None:
        if self.status is P8LockedEvaluationStatus.COMPLETED:
            if (
                not self.source_open_attempted
                or evaluated == 0
                or admitted != paired
                or paired != evaluated
                or exclusions != 0
                or self.locked_source_manifest_sha256 is None
                or self.aggregate_metrics_sha256 is None
                or self.reason_codes
            ):
                raise VisualFeedbackContractError(
                    "completed P8 report invariants differ"
                )
            return
        if self.status is P8LockedEvaluationStatus.FAILED_CLOSED:
            if (
                not self.source_open_attempted
                or not self.reason_codes
            ):
                raise VisualFeedbackContractError(
                    "failed-closed P8 report invariants differ"
                )
            if (self.aggregate_metrics_sha256 is None) != (
                self.locked_source_manifest_sha256 is None
            ):
                raise VisualFeedbackContractError(
                    "failed-closed P8 manifest and metrics evidence differ"
                )
            if self.aggregate_metrics_sha256 is None:
                if (
                    admitted != 0
                    or paired != 0
                    or evaluated != 0
                    or exclusions != 0
                    or self.successful_item_count != 0
                    or self.failed_item_count != 0
                    or self.reason_codes
                    != ("p8_locked_source_evaluation_failed",)
                ):
                    raise VisualFeedbackContractError(
                        "failed-closed P8 report without metrics differs"
                    )
            elif evaluated == 0:
                raise VisualFeedbackContractError(
                    "failed-closed P8 report with metrics must evaluate items"
                )
            elif (
                (exclusions > 0)
                != ("p8_evaluation_exclusion_detected" in self.reason_codes)
            ):
                raise VisualFeedbackContractError(
                    "failed-closed P8 exclusion reason differs"
                )
            return
        if self.status is P8LockedEvaluationStatus.NOT_EXECUTED:
            if (
                self.source_open_attempted
                or admitted != 0
                or paired != 0
                or evaluated != 0
                or exclusions != 0
                or self.successful_item_count != 0
                or self.failed_item_count != 0
                or self.locked_source_manifest_sha256 is not None
                or self.aggregate_metrics_sha256 is not None
                or self.reason_codes != ("source_evaluation_not_executed",)
            ):
                raise VisualFeedbackContractError(
                    "not-executed P8 report invariants differ"
                )
            return
        raise VisualFeedbackContractError("P8 report status differs")

    @classmethod
    def issue(
        cls,
        *,
        report_id: str,
        protocol_revision: str,
        transaction: P8AccessTransaction,
        status: P8LockedEvaluationStatus,
        source_open_attempted: bool,
        admitted_item_count: int,
        paired_item_count: int,
        evaluated_item_count: int,
        evaluation_exclusion_count: int,
        successful_item_count: int,
        failed_item_count: int,
        locked_source_manifest_sha256: str | None,
        aggregate_metrics_sha256: str | None,
        reason_codes: tuple[str, ...],
    ) -> P8LockedEvaluationReport:
        unsigned = {
            "schema_version": P8_LOCKED_EVALUATION_REPORT_SCHEMA_VERSION,
            "report_id": report_id,
            "protocol_revision": protocol_revision,
            "seal_sha256": transaction.seal_sha256,
            "access_transaction_sha256": transaction.transaction_sha256,
            "dataset_id": transaction.dataset_id,
            "source_listing_sha256": transaction.source_listing_sha256,
            "locked_source_manifest_sha256": locked_source_manifest_sha256,
            "status": status.value,
            "source_open_attempted": source_open_attempted,
            "access_count": transaction.access_count,
            "admitted_item_count": admitted_item_count,
            "paired_item_count": paired_item_count,
            "evaluated_item_count": evaluated_item_count,
            "evaluation_exclusion_count": evaluation_exclusion_count,
            "evaluation_exclusion_policy": P8_EVALUATION_EXCLUSION_POLICY,
            "successful_item_count": successful_item_count,
            "failed_item_count": failed_item_count,
            "aggregate_metrics_sha256": aggregate_metrics_sha256,
            "reason_codes": list(reason_codes),
            "aggregate_only": True,
        }
        return cls(
            report_id=report_id,
            protocol_revision=protocol_revision,
            seal_sha256=transaction.seal_sha256,
            access_transaction_sha256=transaction.transaction_sha256,
            dataset_id=transaction.dataset_id,
            source_listing_sha256=transaction.source_listing_sha256,
            locked_source_manifest_sha256=locked_source_manifest_sha256,
            status=status,
            source_open_attempted=source_open_attempted,
            access_count=transaction.access_count,
            admitted_item_count=admitted_item_count,
            paired_item_count=paired_item_count,
            evaluated_item_count=evaluated_item_count,
            evaluation_exclusion_count=evaluation_exclusion_count,
            evaluation_exclusion_policy=P8_EVALUATION_EXCLUSION_POLICY,
            successful_item_count=successful_item_count,
            failed_item_count=failed_item_count,
            aggregate_metrics_sha256=aggregate_metrics_sha256,
            reason_codes=reason_codes,
            aggregate_only=True,
            report_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "report_id": self.report_id,
            "protocol_revision": self.protocol_revision,
            "seal_sha256": self.seal_sha256,
            "access_transaction_sha256": self.access_transaction_sha256,
            "dataset_id": self.dataset_id,
            "source_listing_sha256": self.source_listing_sha256,
            "locked_source_manifest_sha256": (
                self.locked_source_manifest_sha256
            ),
            "status": self.status.value,
            "source_open_attempted": self.source_open_attempted,
            "access_count": self.access_count,
            "admitted_item_count": self.admitted_item_count,
            "paired_item_count": self.paired_item_count,
            "evaluated_item_count": self.evaluated_item_count,
            "evaluation_exclusion_count": self.evaluation_exclusion_count,
            "evaluation_exclusion_policy": self.evaluation_exclusion_policy,
            "successful_item_count": self.successful_item_count,
            "failed_item_count": self.failed_item_count,
            "aggregate_metrics_sha256": self.aggregate_metrics_sha256,
            "reason_codes": list(self.reason_codes),
            "aggregate_only": self.aggregate_only,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "report_sha256": self.report_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> P8LockedEvaluationReport:
        document = require_mapping(value, "p8_locked_evaluation_report")
        require_exact_keys(
            document,
            {
                "schema_version",
                "report_id",
                "protocol_revision",
                "seal_sha256",
                "access_transaction_sha256",
                "dataset_id",
                "source_listing_sha256",
                "locked_source_manifest_sha256",
                "status",
                "source_open_attempted",
                "access_count",
                "admitted_item_count",
                "paired_item_count",
                "evaluated_item_count",
                "evaluation_exclusion_count",
                "evaluation_exclusion_policy",
                "successful_item_count",
                "failed_item_count",
                "aggregate_metrics_sha256",
                "reason_codes",
                "aggregate_only",
                "report_sha256",
            },
            "p8_locked_evaluation_report",
        )
        try:
            status = P8LockedEvaluationStatus(
                require_safe_identifier(document["status"], "status")
            )
        except ValueError as error:
            raise VisualFeedbackContractError("P8 report status differs") from error
        reason_codes_value = document["reason_codes"]
        if isinstance(reason_codes_value, (str, bytes)) or not isinstance(
            reason_codes_value, Sequence
        ):
            raise VisualFeedbackContractError("reason_codes must be an array")
        aggregate_metrics_value = document["aggregate_metrics_sha256"]
        if aggregate_metrics_value is not None:
            aggregate_metrics_value = require_sha256(
                aggregate_metrics_value, "aggregate_metrics_sha256"
            )
        locked_source_manifest_value = document[
            "locked_source_manifest_sha256"
        ]
        if locked_source_manifest_value is not None:
            locked_source_manifest_value = require_sha256(
                locked_source_manifest_value,
                "locked_source_manifest_sha256",
            )
        return cls(
            report_id=require_safe_identifier(document["report_id"], "report_id"),
            protocol_revision=require_safe_identifier(
                document["protocol_revision"], "protocol_revision"
            ),
            seal_sha256=require_sha256(document["seal_sha256"], "seal_sha256"),
            access_transaction_sha256=require_sha256(
                document["access_transaction_sha256"],
                "access_transaction_sha256",
            ),
            dataset_id=require_safe_identifier(document["dataset_id"], "dataset_id"),
            source_listing_sha256=require_sha256(
                document["source_listing_sha256"], "source_listing_sha256"
            ),
            locked_source_manifest_sha256=locked_source_manifest_value,
            status=status,
            source_open_attempted=require_boolean(
                document["source_open_attempted"], "source_open_attempted"
            ),
            access_count=require_integer(
                document["access_count"], "access_count", minimum=1
            ),
            admitted_item_count=require_integer(
                document["admitted_item_count"],
                "admitted_item_count",
                minimum=0,
            ),
            paired_item_count=require_integer(
                document["paired_item_count"],
                "paired_item_count",
                minimum=0,
            ),
            evaluated_item_count=require_integer(
                document["evaluated_item_count"],
                "evaluated_item_count",
                minimum=0,
            ),
            evaluation_exclusion_count=require_integer(
                document["evaluation_exclusion_count"],
                "evaluation_exclusion_count",
                minimum=0,
            ),
            evaluation_exclusion_policy=(
                require_p8_evaluation_exclusion_policy(
                    document["evaluation_exclusion_policy"]
                )
            ),
            successful_item_count=require_integer(
                document["successful_item_count"],
                "successful_item_count",
                minimum=0,
            ),
            failed_item_count=require_integer(
                document["failed_item_count"], "failed_item_count", minimum=0
            ),
            aggregate_metrics_sha256=aggregate_metrics_value,
            reason_codes=tuple(
                require_safe_identifier(value, "reason_codes[]")
                for value in reason_codes_value
            ),
            aggregate_only=require_boolean(
                document["aggregate_only"], "aggregate_only"
            ),
            report_sha256=require_sha256(document["report_sha256"], "report_sha256"),
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
        )


__all__ = [
    "P8_ACCESS_TRANSACTION_SCHEMA_VERSION",
    "P8_LOCKED_DATASET_ID",
    "P8_LOCKED_EVALUATION_REPORT_SCHEMA_VERSION",
    "P8_LOCKED_EVALUATION_THRESHOLDS",
    "P8_LOCKED_EVALUATION_THRESHOLDS_SHA256",
    "P8_LOCKED_SOURCE_LISTING_INPUTS",
    "P8_LOCKED_SOURCE_LISTING_SHA256",
    "P8_READ_ZERO_SEAL_SCHEMA_VERSION",
    "P8_METRIC_SEMANTICS_VERSION",
    "P8AccessAuthorizationState",
    "P8AccessTransaction",
    "P8LockedEvaluationReport",
    "P8LockedEvaluationStatus",
    "P8LockedEvaluationThresholds",
    "P8ReadZeroSeal",
    "P8SourceListingDigestInput",
]
