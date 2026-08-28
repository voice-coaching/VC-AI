"""Aggregate-only contracts for AI Hub 538 runtime-decision calibration.

The contracts intentionally contain no archive member name, filesystem path,
transcript, actor token, attempt token, or sample-level digest.  Those values
exist only inside the restricted calibration process.  The serialized surface
binds immutable policy inputs and aggregate counts needed to prove that the
non-locked TS174/TL174 development and calibration populations were complete.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import VisualFeedbackContractError
from .aihub538_training_evidence import AIHUB538_TRAINING_ANGLES
from .integrity import (
    require_boolean,
    require_exact_keys,
    require_finite_number,
    require_integer,
    require_mapping,
    require_safe_identifier,
    require_sha256,
    sha256_document,
)


AIHUB538_DECISION_CALIBRATION_SCHEMA_VERSION = (
    "voice_coach.aihub538_decision_calibration_evidence.v1"
)
AIHUB538_DECISION_CALIBRATION_RELOAD_SCHEMA_VERSION = (
    "voice_coach.aihub538_decision_calibration_reload_receipt.v1"
)
AIHUB538_DECISION_CALIBRATION_PROTOCOL_SCHEMA_VERSION = (
    "voice_coach.aihub538_decision_calibration_protocol.v1"
)


def _fraction(value: object, name: str) -> float:
    number = require_finite_number(value, name)
    if not 0.0 <= number <= 1.0:
        raise VisualFeedbackContractError(f"{name} must be in [0, 1]")
    return number


@dataclass(frozen=True, slots=True)
class Aihub538DecisionCalibrationProtocol:
    """Outcome-independent split, quantile, margin, and stop policy."""

    protocol_revision: str
    samples_per_angle_per_partition: int
    minimum_available_per_angle: int
    lower_quantile: float
    upper_quantile: float
    qc_fraction_margin: float
    qc_luma_margin_8bit: float
    qc_edge_margin_8bit: float
    qc_center_step_margin: float
    sync_offset_margin_ms: int
    sync_correlation_margin: float
    sync_anchor_strength_margin: float
    minimum_calibration_pass_fraction: float
    minimum_per_angle_pass_fraction: float
    schema_version: str = AIHUB538_DECISION_CALIBRATION_PROTOCOL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != AIHUB538_DECISION_CALIBRATION_PROTOCOL_SCHEMA_VERSION:
            raise VisualFeedbackContractError(
                "AI Hub 538 calibration protocol schema differs"
            )
        require_safe_identifier(self.protocol_revision, "protocol_revision")
        sample_count = require_integer(
            self.samples_per_angle_per_partition,
            "samples_per_angle_per_partition",
            minimum=2,
        )
        available_count = require_integer(
            self.minimum_available_per_angle,
            "minimum_available_per_angle",
            minimum=2,
        )
        if available_count > sample_count:
            raise VisualFeedbackContractError(
                "minimum available count exceeds selected count"
            )
        lower = _fraction(self.lower_quantile, "lower_quantile")
        upper = _fraction(self.upper_quantile, "upper_quantile")
        if not 0.0 < lower < upper < 1.0:
            raise VisualFeedbackContractError("calibration quantiles are not ordered")
        for name in (
            "qc_fraction_margin",
            "qc_center_step_margin",
            "sync_correlation_margin",
            "sync_anchor_strength_margin",
        ):
            _fraction(getattr(self, name), name)
        for name in ("qc_luma_margin_8bit", "qc_edge_margin_8bit"):
            value = require_finite_number(getattr(self, name), name)
            if not 0.0 <= value <= 255.0:
                raise VisualFeedbackContractError(f"{name} is outside policy")
        require_integer(
            self.sync_offset_margin_ms,
            "sync_offset_margin_ms",
            minimum=0,
        )
        for name in (
            "minimum_calibration_pass_fraction",
            "minimum_per_angle_pass_fraction",
        ):
            fraction = _fraction(getattr(self, name), name)
            if fraction <= 0.0:
                raise VisualFeedbackContractError(f"{name} must be positive")

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "protocol_revision": self.protocol_revision,
            "source_dataset_id": "aihub_538",
            "source_partitions": ["TS174", "TL174"],
            "locked_partition_accessed": False,
            "actor_split_rule": "sha256_domain_parity_v1",
            "attempt_split_rule": "inherits_actor_partition_no_cross_partition_attempt",
            "angle_selection_rule": "lowest_sha256_score_exact_quota_per_angle_v1",
            "quantile_method": "linear_type7",
            "samples_per_angle_per_partition": (
                self.samples_per_angle_per_partition
            ),
            "minimum_available_per_angle": self.minimum_available_per_angle,
            "lower_quantile": self.lower_quantile,
            "upper_quantile": self.upper_quantile,
            "qc_fraction_margin": self.qc_fraction_margin,
            "qc_luma_margin_8bit": self.qc_luma_margin_8bit,
            "qc_edge_margin_8bit": self.qc_edge_margin_8bit,
            "qc_center_step_margin": self.qc_center_step_margin,
            "sync_offset_margin_ms": self.sync_offset_margin_ms,
            "sync_correlation_margin": self.sync_correlation_margin,
            "sync_anchor_strength_margin": self.sync_anchor_strength_margin,
            "minimum_calibration_pass_fraction": (
                self.minimum_calibration_pass_fraction
            ),
            "minimum_per_angle_pass_fraction": (
                self.minimum_per_angle_pass_fraction
            ),
            "threshold_inputs": "development_partition_only",
            "stop_policy_inputs_frozen_before_measurement": True,
            "post_observation_policy_edits_allowed": False,
        }

    @property
    def protocol_sha256(self) -> str:
        return sha256_document(self.to_json_object())


DEFAULT_AIHUB538_DECISION_CALIBRATION_PROTOCOL = (
    Aihub538DecisionCalibrationProtocol(
        protocol_revision="aihub538_ts174_tl174_runtime_decisions_v1",
        samples_per_angle_per_partition=8,
        minimum_available_per_angle=6,
        lower_quantile=0.10,
        upper_quantile=0.90,
        qc_fraction_margin=0.02,
        qc_luma_margin_8bit=5.0,
        qc_edge_margin_8bit=0.5,
        qc_center_step_margin=0.02,
        sync_offset_margin_ms=20,
        sync_correlation_margin=0.05,
        sync_anchor_strength_margin=0.05,
        minimum_calibration_pass_fraction=0.75,
        minimum_per_angle_pass_fraction=0.50,
    )
)


@dataclass(frozen=True, slots=True)
class Aihub538CalibrationAngleEvidence:
    """Per-angle aggregate without sample- or participant-level values."""

    angle: str
    development_selected_count: int
    calibration_selected_count: int
    development_qc_available_count: int
    development_sync_available_count: int
    calibration_qc_available_count: int
    calibration_sync_available_count: int
    calibration_qc_passed_count: int
    calibration_sync_passed_count: int
    qc_lineage_valid_count: int
    sync_lineage_valid_count: int

    def __post_init__(self) -> None:
        if self.angle not in AIHUB538_TRAINING_ANGLES:
            raise VisualFeedbackContractError("AI Hub 538 calibration angle differs")
        counts = {
            name: require_integer(getattr(self, name), name, minimum=0)
            for name in self.__dataclass_fields__
            if name != "angle"
        }
        development = counts["development_selected_count"]
        calibration = counts["calibration_selected_count"]
        for name in (
            "development_qc_available_count",
            "development_sync_available_count",
        ):
            if counts[name] > development:
                raise VisualFeedbackContractError(
                    "development availability exceeds selected count"
                )
        for name in (
            "calibration_qc_available_count",
            "calibration_sync_available_count",
            "calibration_qc_passed_count",
            "calibration_sync_passed_count",
            "qc_lineage_valid_count",
            "sync_lineage_valid_count",
        ):
            if counts[name] > calibration:
                raise VisualFeedbackContractError(
                    "calibration count exceeds selected count"
                )

    def to_json_object(self) -> dict[str, object]:
        return {
            name: getattr(self, name) for name in self.__dataclass_fields__
        }

    @classmethod
    def from_json_object(cls, value: object) -> Aihub538CalibrationAngleEvidence:
        document = require_mapping(value, "angle_evidence")
        fields = set(cls.__dataclass_fields__)
        require_exact_keys(document, fields, "angle_evidence")
        return cls(
            angle=require_safe_identifier(document["angle"], "angle"),
            **{
                name: require_integer(document[name], name, minimum=0)
                for name in fields
                if name != "angle"
            },
        )


@dataclass(frozen=True, slots=True)
class Aihub538DecisionCalibrationEvidence:
    """Self-digested aggregate result of actual-media calibration evaluation."""

    source_manifest_sha256: str
    admission_receipt_sha256: str
    identity_namespace_commitment_sha256: str
    evaluation_protocol_sha256: str
    evaluator_code_sha256: str
    video_qc_measurement_policy_artifact_sha256: str
    video_qc_measurement_policy_sha256: str
    av_sync_measurement_policy_artifact_sha256: str
    av_sync_measurement_policy_sha256: str
    video_qc_decision_policy_artifact_sha256: str
    video_qc_decision_policy_sha256: str
    av_sync_decision_policy_artifact_sha256: str
    av_sync_decision_policy_sha256: str
    eligible_item_count: int
    development_selected_count: int
    calibration_selected_count: int
    development_actor_count: int
    calibration_actor_count: int
    actor_overlap_count: int
    attempt_overlap_count: int
    qc_decision_lineage_valid_item_count: int
    sync_decision_lineage_valid_item_count: int
    angle_evidence: tuple[Aihub538CalibrationAngleEvidence, ...]
    receipt_sha256: str
    status: str = "actual_media_calibration_evaluated"
    schema_version: str = AIHUB538_DECISION_CALIBRATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != AIHUB538_DECISION_CALIBRATION_SCHEMA_VERSION:
            raise VisualFeedbackContractError(
                "AI Hub 538 decision calibration evidence schema differs"
            )
        if self.status != "actual_media_calibration_evaluated":
            raise VisualFeedbackContractError(
                "AI Hub 538 decision calibration evidence status differs"
            )
        for name in (
            "source_manifest_sha256",
            "admission_receipt_sha256",
            "identity_namespace_commitment_sha256",
            "evaluation_protocol_sha256",
            "evaluator_code_sha256",
            "video_qc_measurement_policy_artifact_sha256",
            "video_qc_measurement_policy_sha256",
            "av_sync_measurement_policy_artifact_sha256",
            "av_sync_measurement_policy_sha256",
            "video_qc_decision_policy_artifact_sha256",
            "video_qc_decision_policy_sha256",
            "av_sync_decision_policy_artifact_sha256",
            "av_sync_decision_policy_sha256",
            "receipt_sha256",
        ):
            require_sha256(getattr(self, name), name)
        for name in (
            "eligible_item_count",
            "development_selected_count",
            "calibration_selected_count",
            "development_actor_count",
            "calibration_actor_count",
            "actor_overlap_count",
            "attempt_overlap_count",
            "qc_decision_lineage_valid_item_count",
            "sync_decision_lineage_valid_item_count",
        ):
            require_integer(getattr(self, name), name, minimum=0)
        if (
            self.eligible_item_count <= 0
            or self.development_selected_count <= 0
            or self.calibration_selected_count <= 0
            or self.development_actor_count <= 0
            or self.calibration_actor_count <= 0
            or self.actor_overlap_count != 0
            or self.attempt_overlap_count != 0
            or self.qc_decision_lineage_valid_item_count
            != self.calibration_selected_count
            or self.sync_decision_lineage_valid_item_count
            != self.calibration_selected_count
        ):
            raise VisualFeedbackContractError(
                "AI Hub 538 decision calibration completeness differs"
            )
        if tuple(item.angle for item in self.angle_evidence) != (
            AIHUB538_TRAINING_ANGLES
        ):
            raise VisualFeedbackContractError(
                "AI Hub 538 calibration angle evidence is incomplete"
            )
        if sum(
            item.development_selected_count for item in self.angle_evidence
        ) != self.development_selected_count or sum(
            item.calibration_selected_count for item in self.angle_evidence
        ) != self.calibration_selected_count:
            raise VisualFeedbackContractError(
                "AI Hub 538 calibration angle totals differ"
            )
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError(
                "AI Hub 538 decision calibration evidence digest differs"
            )

    @classmethod
    def issue(cls, **values: object) -> Aihub538DecisionCalibrationEvidence:
        # Build an unsigned in-memory draft without weakening ``__post_init__``:
        # ordinary callers can never instantiate a zero-digest evidence object.
        draft = object.__new__(cls)
        for field_name, field_definition in cls.__dataclass_fields__.items():
            if field_name == "receipt_sha256":
                field_value: object = "0" * 64
            elif field_name in values:
                field_value = values[field_name]
            else:
                field_value = field_definition.default
            object.__setattr__(draft, field_name, field_value)
        return cls(
            **{
                field: getattr(draft, field)
                for field in draft.__dataclass_fields__
                if field != "receipt_sha256"
            },
            receipt_sha256=sha256_document(draft.unsigned_json_object()),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "source_dataset_id": "aihub_538",
            "source_partitions": ["TS174", "TL174"],
            "locked_partition_accessed": False,
            "source_manifest_sha256": self.source_manifest_sha256,
            "admission_receipt_sha256": self.admission_receipt_sha256,
            "identity_namespace_commitment_sha256": (
                self.identity_namespace_commitment_sha256
            ),
            "evaluation_protocol_sha256": self.evaluation_protocol_sha256,
            "evaluator_code_sha256": self.evaluator_code_sha256,
            "video_qc_measurement_policy_artifact_sha256": (
                self.video_qc_measurement_policy_artifact_sha256
            ),
            "video_qc_measurement_policy_sha256": (
                self.video_qc_measurement_policy_sha256
            ),
            "av_sync_measurement_policy_artifact_sha256": (
                self.av_sync_measurement_policy_artifact_sha256
            ),
            "av_sync_measurement_policy_sha256": (
                self.av_sync_measurement_policy_sha256
            ),
            "video_qc_decision_policy_artifact_sha256": (
                self.video_qc_decision_policy_artifact_sha256
            ),
            "video_qc_decision_policy_sha256": (
                self.video_qc_decision_policy_sha256
            ),
            "av_sync_decision_policy_artifact_sha256": (
                self.av_sync_decision_policy_artifact_sha256
            ),
            "av_sync_decision_policy_sha256": (
                self.av_sync_decision_policy_sha256
            ),
            "eligible_item_count": self.eligible_item_count,
            "development_selected_count": self.development_selected_count,
            "calibration_selected_count": self.calibration_selected_count,
            "development_actor_count": self.development_actor_count,
            "calibration_actor_count": self.calibration_actor_count,
            "actor_overlap_count": self.actor_overlap_count,
            "attempt_overlap_count": self.attempt_overlap_count,
            "qc_decision_lineage_valid_item_count": (
                self.qc_decision_lineage_valid_item_count
            ),
            "sync_decision_lineage_valid_item_count": (
                self.sync_decision_lineage_valid_item_count
            ),
            "angle_evidence": [item.to_json_object() for item in self.angle_evidence],
            "actual_media_decode_executed": True,
            "actual_landmark_tracker_executed": True,
            "actual_pcm_sync_measurement_executed": True,
            "measurement_to_decision_lineage_complete": True,
            "aggregate_only": True,
            "raw_paths_serialized": False,
            "transcripts_serialized": False,
            "actor_identifiers_serialized": False,
            "attempt_identifiers_serialized": False,
            "sample_level_digests_serialized": False,
            "runtime_promotion_authorized": False,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> Aihub538DecisionCalibrationEvidence:
        document = require_mapping(value, "aihub538_decision_calibration_evidence")
        fields = {
            "schema_version",
            "status",
            "source_dataset_id",
            "source_partitions",
            "locked_partition_accessed",
            "source_manifest_sha256",
            "admission_receipt_sha256",
            "identity_namespace_commitment_sha256",
            "evaluation_protocol_sha256",
            "evaluator_code_sha256",
            "video_qc_measurement_policy_artifact_sha256",
            "video_qc_measurement_policy_sha256",
            "av_sync_measurement_policy_artifact_sha256",
            "av_sync_measurement_policy_sha256",
            "video_qc_decision_policy_artifact_sha256",
            "video_qc_decision_policy_sha256",
            "av_sync_decision_policy_artifact_sha256",
            "av_sync_decision_policy_sha256",
            "eligible_item_count",
            "development_selected_count",
            "calibration_selected_count",
            "development_actor_count",
            "calibration_actor_count",
            "actor_overlap_count",
            "attempt_overlap_count",
            "qc_decision_lineage_valid_item_count",
            "sync_decision_lineage_valid_item_count",
            "angle_evidence",
            "actual_media_decode_executed",
            "actual_landmark_tracker_executed",
            "actual_pcm_sync_measurement_executed",
            "measurement_to_decision_lineage_complete",
            "aggregate_only",
            "raw_paths_serialized",
            "transcripts_serialized",
            "actor_identifiers_serialized",
            "attempt_identifiers_serialized",
            "sample_level_digests_serialized",
            "runtime_promotion_authorized",
            "receipt_sha256",
        }
        require_exact_keys(document, fields, "aihub538_decision_calibration_evidence")
        if (
            document["source_dataset_id"] != "aihub_538"
            or document["source_partitions"] != ["TS174", "TL174"]
            or require_boolean(
                document["locked_partition_accessed"],
                "locked_partition_accessed",
            )
            is not False
        ):
            raise VisualFeedbackContractError(
                "AI Hub 538 calibration source boundary differs"
            )
        true_fields = (
            "actual_media_decode_executed",
            "actual_landmark_tracker_executed",
            "actual_pcm_sync_measurement_executed",
            "measurement_to_decision_lineage_complete",
            "aggregate_only",
        )
        false_fields = (
            "raw_paths_serialized",
            "transcripts_serialized",
            "actor_identifiers_serialized",
            "attempt_identifiers_serialized",
            "sample_level_digests_serialized",
            "runtime_promotion_authorized",
        )
        if any(
            require_boolean(document[name], name) is not True for name in true_fields
        ) or any(
            require_boolean(document[name], name) is not False for name in false_fields
        ):
            raise VisualFeedbackContractError(
                "AI Hub 538 calibration privacy or execution flags differ"
            )
        angle_values = document["angle_evidence"]
        if not isinstance(angle_values, list):
            raise VisualFeedbackContractError("angle_evidence must be an array")
        sha_fields = (
            "source_manifest_sha256",
            "admission_receipt_sha256",
            "identity_namespace_commitment_sha256",
            "evaluation_protocol_sha256",
            "evaluator_code_sha256",
            "video_qc_measurement_policy_artifact_sha256",
            "video_qc_measurement_policy_sha256",
            "av_sync_measurement_policy_artifact_sha256",
            "av_sync_measurement_policy_sha256",
            "video_qc_decision_policy_artifact_sha256",
            "video_qc_decision_policy_sha256",
            "av_sync_decision_policy_artifact_sha256",
            "av_sync_decision_policy_sha256",
            "receipt_sha256",
        )
        count_fields = (
            "eligible_item_count",
            "development_selected_count",
            "calibration_selected_count",
            "development_actor_count",
            "calibration_actor_count",
            "actor_overlap_count",
            "attempt_overlap_count",
            "qc_decision_lineage_valid_item_count",
            "sync_decision_lineage_valid_item_count",
        )
        return cls(
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
            status=require_safe_identifier(document["status"], "status"),
            angle_evidence=tuple(
                Aihub538CalibrationAngleEvidence.from_json_object(item)
                for item in angle_values
            ),
            **{name: require_sha256(document[name], name) for name in sha_fields},
            **{
                name: require_integer(document[name], name, minimum=0)
                for name in count_fields
            },
        )


@dataclass(frozen=True, slots=True)
class Aihub538DecisionCalibrationReloadReceipt:
    """Proof that a separate process strictly reloaded the calibrated bundle."""

    calibration_evidence_receipt_sha256: str
    video_qc_decision_policy_artifact_sha256: str
    video_qc_decision_policy_sha256: str
    av_sync_decision_policy_artifact_sha256: str
    av_sync_decision_policy_sha256: str
    evaluator_code_sha256: str
    receipt_sha256: str
    schema_version: str = AIHUB538_DECISION_CALIBRATION_RELOAD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != AIHUB538_DECISION_CALIBRATION_RELOAD_SCHEMA_VERSION:
            raise VisualFeedbackContractError(
                "AI Hub 538 calibration reload schema differs"
            )
        for name in self.__dataclass_fields__:
            if name != "schema_version":
                require_sha256(getattr(self, name), name)
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError(
                "AI Hub 538 calibration reload receipt digest differs"
            )

    @classmethod
    def issue(
        cls,
        *,
        calibration_evidence: Aihub538DecisionCalibrationEvidence,
    ) -> Aihub538DecisionCalibrationReloadReceipt:
        values = {
            "calibration_evidence_receipt_sha256": (
                calibration_evidence.receipt_sha256
            ),
            "video_qc_decision_policy_artifact_sha256": (
                calibration_evidence.video_qc_decision_policy_artifact_sha256
            ),
            "video_qc_decision_policy_sha256": (
                calibration_evidence.video_qc_decision_policy_sha256
            ),
            "av_sync_decision_policy_artifact_sha256": (
                calibration_evidence.av_sync_decision_policy_artifact_sha256
            ),
            "av_sync_decision_policy_sha256": (
                calibration_evidence.av_sync_decision_policy_sha256
            ),
            "evaluator_code_sha256": calibration_evidence.evaluator_code_sha256,
        }
        unsigned = {
            "schema_version": AIHUB538_DECISION_CALIBRATION_RELOAD_SCHEMA_VERSION,
            **values,
            "fresh_process": True,
            "strict_typed_reload": True,
            "runtime_policy_conversion_passed": True,
            "aggregate_privacy_contract_passed": True,
            "status": "fresh_process_reload_passed",
            "runtime_promotion_authorized": False,
        }
        return cls(**values, receipt_sha256=sha256_document(unsigned))

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "calibration_evidence_receipt_sha256": (
                self.calibration_evidence_receipt_sha256
            ),
            "video_qc_decision_policy_artifact_sha256": (
                self.video_qc_decision_policy_artifact_sha256
            ),
            "video_qc_decision_policy_sha256": (
                self.video_qc_decision_policy_sha256
            ),
            "av_sync_decision_policy_artifact_sha256": (
                self.av_sync_decision_policy_artifact_sha256
            ),
            "av_sync_decision_policy_sha256": self.av_sync_decision_policy_sha256,
            "evaluator_code_sha256": self.evaluator_code_sha256,
            "fresh_process": True,
            "strict_typed_reload": True,
            "runtime_policy_conversion_passed": True,
            "aggregate_privacy_contract_passed": True,
            "status": "fresh_process_reload_passed",
            "runtime_promotion_authorized": False,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> Aihub538DecisionCalibrationReloadReceipt:
        document = require_mapping(value, "aihub538_calibration_reload_receipt")
        fields = {
            "schema_version",
            "calibration_evidence_receipt_sha256",
            "video_qc_decision_policy_artifact_sha256",
            "video_qc_decision_policy_sha256",
            "av_sync_decision_policy_artifact_sha256",
            "av_sync_decision_policy_sha256",
            "evaluator_code_sha256",
            "fresh_process",
            "strict_typed_reload",
            "runtime_policy_conversion_passed",
            "aggregate_privacy_contract_passed",
            "status",
            "runtime_promotion_authorized",
            "receipt_sha256",
        }
        require_exact_keys(document, fields, "aihub538_calibration_reload_receipt")
        for name in (
            "fresh_process",
            "strict_typed_reload",
            "runtime_policy_conversion_passed",
            "aggregate_privacy_contract_passed",
        ):
            if require_boolean(document[name], name) is not True:
                raise VisualFeedbackContractError(
                    "AI Hub 538 calibration reload did not pass"
                )
        if (
            document["status"] != "fresh_process_reload_passed"
            or require_boolean(
                document["runtime_promotion_authorized"],
                "runtime_promotion_authorized",
            )
            is not False
        ):
            raise VisualFeedbackContractError(
                "AI Hub 538 calibration reload state differs"
            )
        return cls(
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
            **{
                name: require_sha256(document[name], name)
                for name in cls.__dataclass_fields__
                if name != "schema_version"
            },
        )


__all__ = [
    "AIHUB538_DECISION_CALIBRATION_PROTOCOL_SCHEMA_VERSION",
    "AIHUB538_DECISION_CALIBRATION_RELOAD_SCHEMA_VERSION",
    "AIHUB538_DECISION_CALIBRATION_SCHEMA_VERSION",
    "Aihub538CalibrationAngleEvidence",
    "Aihub538DecisionCalibrationEvidence",
    "Aihub538DecisionCalibrationProtocol",
    "Aihub538DecisionCalibrationReloadReceipt",
    "DEFAULT_AIHUB538_DECISION_CALIBRATION_PROTOCOL",
]
