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
from .aihub538_nonlocked_shards import (
    AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS,
)
from .aihub538_balanced_cohort import (
    AIHUB538_MULTI_ACTOR_ASSIGNMENT_RULE,
    AIHUB538_MULTI_MINIMUM_ACTORS_PER_PARTITION,
)
from .runtime_policy_artifact import (
    P8RuntimePolicyArtifact,
    VideoQcDecisionSemanticPolicy,
)


AIHUB538_DECISION_CALIBRATION_SCHEMA_VERSION = (
    "voice_coach.aihub538_decision_calibration_evidence.v1"
)
AIHUB538_MULTI_DECISION_CALIBRATION_SCHEMA_VERSION = (
    "voice_coach.aihub538_decision_calibration_evidence.v2"
)
AIHUB538_MULTI_DECISION_CALIBRATION_FAILURE_SCHEMA_VERSION = (
    "voice_coach.aihub538_decision_calibration_failure.v1"
)
AIHUB538_MULTI_DECISION_CALIBRATION_FAILURE_V2_SCHEMA_VERSION = (
    "voice_coach.aihub538_decision_calibration_failure.v2"
)
AIHUB538_DECISION_CALIBRATION_RELOAD_SCHEMA_VERSION = (
    "voice_coach.aihub538_decision_calibration_reload_receipt.v1"
)
AIHUB538_DECISION_CALIBRATION_PROTOCOL_SCHEMA_VERSION = (
    "voice_coach.aihub538_decision_calibration_protocol.v1"
)
AIHUB538_MULTI_DECISION_CALIBRATION_PROTOCOL_SCHEMA_VERSION = (
    "voice_coach.aihub538_decision_calibration_protocol.v2"
)
AIHUB538_MULTI_METADATA_BBOX_ELIGIBILITY_RULE = (
    "exclude_bounded_reversed_before_selection_then_exact_quota_revalidate_v1"
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
    source_partitions: tuple[str, ...] = ("TS174", "TL174")
    actor_split_rule: str = "sha256_domain_parity_v1"
    minimum_actor_count_per_partition: int = 1
    metadata_bbox_eligibility_rule: str = "strict_parse_no_exclusion_v1"
    schema_version: str = AIHUB538_DECISION_CALIBRATION_PROTOCOL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        expected_schema = (
            AIHUB538_MULTI_DECISION_CALIBRATION_PROTOCOL_SCHEMA_VERSION
            if self.actor_split_rule == AIHUB538_MULTI_ACTOR_ASSIGNMENT_RULE
            else AIHUB538_DECISION_CALIBRATION_PROTOCOL_SCHEMA_VERSION
        )
        if self.schema_version != expected_schema:
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
        if self.metadata_bbox_eligibility_rule not in {
            "strict_parse_no_exclusion_v1",
            AIHUB538_MULTI_METADATA_BBOX_ELIGIBILITY_RULE,
        }:
            raise VisualFeedbackContractError(
                "metadata bbox eligibility rule differs"
            )
        if (
            self.actor_split_rule == AIHUB538_MULTI_ACTOR_ASSIGNMENT_RULE
        ) != (
            self.metadata_bbox_eligibility_rule
            == AIHUB538_MULTI_METADATA_BBOX_ELIGIBILITY_RULE
        ):
            raise VisualFeedbackContractError(
                "metadata bbox eligibility scope differs"
            )
        for name in (
            "minimum_calibration_pass_fraction",
            "minimum_per_angle_pass_fraction",
        ):
            fraction = _fraction(getattr(self, name), name)
            if fraction <= 0.0:
                raise VisualFeedbackContractError(f"{name} must be positive")
        if self.source_partitions not in {
            ("TS174", "TL174"),
            AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS,
        }:
            raise VisualFeedbackContractError(
                "calibration source partitions differ"
            )
        if self.actor_split_rule not in {
            "sha256_domain_parity_v1",
            AIHUB538_MULTI_ACTOR_ASSIGNMENT_RULE,
        }:
            raise VisualFeedbackContractError("actor split rule differs")
        require_integer(
            self.minimum_actor_count_per_partition,
            "minimum_actor_count_per_partition",
            minimum=1,
        )

    def to_json_object(self) -> dict[str, object]:
        document = {
            "schema_version": self.schema_version,
            "protocol_revision": self.protocol_revision,
            "source_dataset_id": "aihub_538",
            "source_partitions": list(self.source_partitions),
            "locked_partition_accessed": False,
            "actor_split_rule": self.actor_split_rule,
            "attempt_split_rule": "inherits_actor_partition_no_cross_partition_attempt",
            "angle_selection_rule": (
                "lowest_label_selection_token_exact_quota_per_angle_v1"
                if self.actor_split_rule == AIHUB538_MULTI_ACTOR_ASSIGNMENT_RULE
                else "lowest_sha256_score_exact_quota_per_angle_v1"
            ),
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
        if self.actor_split_rule == AIHUB538_MULTI_ACTOR_ASSIGNMENT_RULE:
            document["minimum_actor_count_per_partition"] = (
                self.minimum_actor_count_per_partition
            )
            document.update(
                {
                    "metadata_bbox_eligibility_rule": (
                        self.metadata_bbox_eligibility_rule
                    ),
                    "metadata_unavailable_excluded_before_selection": True,
                    "post_exclusion_exact_quota_revalidated": True,
                    "hidden_skip_allowed": False,
                    "bbox_endpoint_repair_allowed": False,
                }
            )
        return document

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

MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL = (
    Aihub538DecisionCalibrationProtocol(
        protocol_revision=(
            "aihub538_ts174_tl174_ts175_tl175_ts176_tl176_"
            "runtime_decisions_v3"
        ),
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
        source_partitions=AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS,
        actor_split_rule=AIHUB538_MULTI_ACTOR_ASSIGNMENT_RULE,
        minimum_actor_count_per_partition=(
            AIHUB538_MULTI_MINIMUM_ACTORS_PER_PARTITION
        ),
        metadata_bbox_eligibility_rule=(
            AIHUB538_MULTI_METADATA_BBOX_ELIGIBILITY_RULE
        ),
        schema_version=(
            AIHUB538_MULTI_DECISION_CALIBRATION_PROTOCOL_SCHEMA_VERSION
        ),
    )
)


AIHUB538_QC_SOURCE_CANDIDATE_UNAVAILABLE_REASON_CODES = (
    "video_qc_single_face_geometry_unavailable",
    "video_qc_single_face_coverage_insufficient",
)
AIHUB538_QC_DERIVED_METRIC_UNAVAILABLE_REASON_CODES = (
    "video_qc_lip_edge_metric_unavailable",
    "video_qc_lip_center_step_metric_unavailable",
)
AIHUB538_QC_CANDIDATE_UNAVAILABLE_REASON_CODES = (
    AIHUB538_QC_SOURCE_CANDIDATE_UNAVAILABLE_REASON_CODES
    + AIHUB538_QC_DERIVED_METRIC_UNAVAILABLE_REASON_CODES
)
AIHUB538_QC_DECISION_FAILURE_REASON_CODES = (
    "video_qc_source_candidate_unavailable",
    "video_qc_single_face_coverage_below_policy",
    "video_qc_multiple_face_fraction_above_policy",
    "video_qc_mean_luma_below_policy",
    "video_qc_mean_luma_above_policy",
    "video_qc_underexposed_fraction_above_policy",
    "video_qc_overexposed_fraction_above_policy",
    "video_qc_lip_edge_delta_below_policy",
    "video_qc_track_transition_coverage_below_policy",
    "video_qc_single_face_run_below_policy",
    "video_qc_lip_center_step_above_policy",
)
# The tuple is an immutable schema table, not caller-controlled configuration.
# Each row is (reason, observed metric, comparison, policy threshold, margin).
AIHUB538_QC_THRESHOLD_DIAGNOSTIC_SPECS = (
    (
        "video_qc_single_face_coverage_below_policy",
        "single_face_coverage",
        "minimum",
        "minimum_single_face_coverage",
        MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL.qc_fraction_margin,
    ),
    (
        "video_qc_multiple_face_fraction_above_policy",
        "multiple_face_frame_fraction",
        "maximum",
        "maximum_multiple_face_frame_fraction",
        MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL.qc_fraction_margin,
    ),
    (
        "video_qc_mean_luma_below_policy",
        "mean_luma_8bit",
        "minimum",
        "minimum_mean_luma_8bit",
        MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL.qc_luma_margin_8bit,
    ),
    (
        "video_qc_mean_luma_above_policy",
        "mean_luma_8bit",
        "maximum",
        "maximum_mean_luma_8bit",
        MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL.qc_luma_margin_8bit,
    ),
    (
        "video_qc_underexposed_fraction_above_policy",
        "underexposed_pixel_fraction",
        "maximum",
        "maximum_underexposed_pixel_fraction",
        MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL.qc_fraction_margin,
    ),
    (
        "video_qc_overexposed_fraction_above_policy",
        "overexposed_pixel_fraction",
        "maximum",
        "maximum_overexposed_pixel_fraction",
        MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL.qc_fraction_margin,
    ),
    (
        "video_qc_lip_edge_delta_below_policy",
        "lip_roi_mean_neighbor_luma_delta_8bit",
        "minimum",
        "minimum_lip_roi_mean_neighbor_luma_delta_8bit",
        MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL.qc_edge_margin_8bit,
    ),
    (
        "video_qc_track_transition_coverage_below_policy",
        "track_transition_coverage",
        "minimum",
        "minimum_track_transition_coverage",
        MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL.qc_fraction_margin,
    ),
    (
        "video_qc_single_face_run_below_policy",
        "longest_single_face_run_fraction",
        "minimum",
        "minimum_longest_single_face_run_fraction",
        MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL.qc_fraction_margin,
    ),
    (
        "video_qc_lip_center_step_above_policy",
        "maximum_lip_center_step_normalized",
        "maximum",
        "maximum_lip_center_step_normalized",
        MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL.qc_center_step_margin,
    ),
)
AIHUB538_QC_UNBINNED_METRIC_REASON_BY_DECISION_REASON = (
    (
        "video_qc_lip_edge_delta_below_policy",
        "video_qc_lip_edge_metric_unavailable",
    ),
    (
        "video_qc_lip_center_step_above_policy",
        "video_qc_lip_center_step_metric_unavailable",
    ),
)
AIHUB538_QC_FAILURE_DIAGNOSTIC_PROTOCOL_SHA256 = sha256_document(
    {
        "schema_version": "voice_coach.aihub538_qc_failure_diagnostic_protocol.v1",
        "candidate_unavailable_reason_codes": list(
            AIHUB538_QC_CANDIDATE_UNAVAILABLE_REASON_CODES
        ),
        "source_candidate_unavailable_reason_codes": list(
            AIHUB538_QC_SOURCE_CANDIDATE_UNAVAILABLE_REASON_CODES
        ),
        "derived_metric_unavailable_reason_codes": list(
            AIHUB538_QC_DERIVED_METRIC_UNAVAILABLE_REASON_CODES
        ),
        "decision_failure_reason_codes": list(
            AIHUB538_QC_DECISION_FAILURE_REASON_CODES
        ),
        "threshold_checks": [
            {
                "reason_code": reason_code,
                "metric_name": metric_name,
                "comparison": comparison,
                "policy_threshold_name": policy_threshold_name,
                "diagnostic_margin": diagnostic_margin,
            }
            for (
                reason_code,
                metric_name,
                comparison,
                policy_threshold_name,
                diagnostic_margin,
            ) in AIHUB538_QC_THRESHOLD_DIAGNOSTIC_SPECS
        ],
        "unbinned_metric_reason_by_decision_reason": [
            {
                "decision_reason_code": decision_reason_code,
                "metric_unavailable_reason_code": metric_reason_code,
            }
            for decision_reason_code, metric_reason_code in (
                AIHUB538_QC_UNBINNED_METRIC_REASON_BY_DECISION_REASON
            )
        ],
        "angle_distinct_count_fields": [
            "development_source_candidate_available_count",
            "calibration_source_candidate_available_count",
        ],
        "optional_metric_missing_combination_fields": [
            "lip_edge_only_missing_count",
            "lip_center_step_only_missing_count",
            "both_optional_metrics_missing_count",
        ],
        "source_available_nullable_metric_invariants": {
            "lip_roi_mean_neighbor_luma_delta_8bit": "must_be_present",
            "maximum_lip_center_step_normalized": "may_be_missing",
        },
        "availability_semantics": {
            "source_candidate_available": (
                "status_eq_candidate_unvalidated"
            ),
            "overall_qc_available": (
                "source_candidate_available_and_all_required_derived_metrics_present"
            ),
            "source_unavailable_distinct": (
                "selected_minus_source_candidate_available"
            ),
            "derived_metric_unavailable_distinct": (
                "source_candidate_available_minus_overall_qc_available"
            ),
            "reason_counts": "overlapping_occurrence_counts",
        },
        "histogram_population_semantics": {
            "default": "source_candidate_available_count",
            "optional_metric": (
                "source_candidate_available_count_minus_metric_missing_count"
            ),
        },
        "signed_margin_semantics": {
            "minimum": "observed_minus_threshold",
            "maximum": "threshold_minus_observed",
        },
        "ordered_bins": [
            "failed_beyond_margin_count",
            "failed_within_margin_count",
            "passed_within_margin_count",
            "passed_beyond_margin_count",
        ],
        "boundary_semantics": {
            "failed_beyond_margin_count": "signed_margin_lt_negative_margin",
            "failed_within_margin_count": (
                "negative_margin_lte_signed_margin_lt_zero"
            ),
            "passed_within_margin_count": (
                "zero_lte_signed_margin_lte_positive_margin"
            ),
            "passed_beyond_margin_count": "positive_margin_lt_signed_margin",
        },
        "raw_observed_metric_values_serialized": False,
        "sample_level_statistics_serialized": False,
    }
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


_AIHUB538_CALIBRATION_FAILURE_STAGES = {
    "aihub538_development_candidate_availability_below_stop_policy": (
        "development_availability"
    ),
    "aihub538_calibration_per_angle_stop_policy_failed": "per_angle_stop_policy",
    "aihub538_calibration_global_stop_policy_failed": "global_stop_policy",
}


def _optional_sha256(value: object, name: str) -> str | None:
    if value is None:
        return None
    return require_sha256(value, name)


def _optional_fraction(value: object, name: str) -> float | None:
    if value is None:
        return None
    return _fraction(value, name)


@dataclass(frozen=True, slots=True)
class Aihub538DecisionCalibrationFailureArtifact:
    """Privacy-preserving aggregate proof of one multishard stop-gate failure."""

    reason_code: str
    failure_stage: str
    protocol_revision: str
    source_manifest_sha256: str
    admission_receipt_sha256: str
    admission_manifest_sha256: str
    admission_policy_sha256: str
    capacity_audit_receipt_sha256: str
    evaluation_protocol_sha256: str
    evaluator_code_sha256: str
    landmark_model_sha256: str
    video_qc_measurement_adapter_code_sha256: str
    av_sync_measurement_adapter_code_sha256: str
    video_qc_measurement_policy_artifact_sha256: str
    video_qc_measurement_policy_sha256: str
    av_sync_measurement_policy_artifact_sha256: str
    av_sync_measurement_policy_sha256: str
    decision_policies_derived: bool
    video_qc_decision_policy_artifact_sha256: str | None
    video_qc_decision_policy_sha256: str | None
    av_sync_decision_policy_artifact_sha256: str | None
    av_sync_decision_policy_sha256: str | None
    admitted_item_count: int
    eligible_item_count: int
    metadata_excluded_item_count: int
    development_selected_count: int
    calibration_selected_count: int
    development_measurement_count: int
    calibration_measurement_count: int
    decision_evaluated_count: int
    development_qc_available_count: int
    development_sync_available_count: int
    calibration_qc_available_count: int
    calibration_sync_available_count: int
    calibration_qc_passed_count: int
    calibration_sync_passed_count: int
    qc_lineage_valid_count: int
    sync_lineage_valid_count: int
    development_qc_available_fraction: float
    development_sync_available_fraction: float
    calibration_qc_pass_fraction: float | None
    calibration_sync_pass_fraction: float | None
    samples_per_angle_per_partition_threshold: int
    minimum_available_per_angle_threshold: int
    minimum_per_angle_pass_fraction_threshold: float
    minimum_global_pass_fraction_threshold: float
    angle_diagnostics: tuple[Aihub538CalibrationAngleEvidence, ...]
    artifact_sha256: str
    source_partitions: tuple[str, ...] = (
        AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS
    )
    status: str = "failed_closed"
    schema_version: str = (
        AIHUB538_MULTI_DECISION_CALIBRATION_FAILURE_SCHEMA_VERSION
    )

    def __post_init__(self) -> None:
        protocol = MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL
        if (
            self.schema_version
            != AIHUB538_MULTI_DECISION_CALIBRATION_FAILURE_SCHEMA_VERSION
            or self.status != "failed_closed"
            or self.source_partitions
            != AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS
            or self.protocol_revision != protocol.protocol_revision
            or self.evaluation_protocol_sha256 != protocol.protocol_sha256
        ):
            raise VisualFeedbackContractError(
                "AI Hub 538 calibration failure scope differs"
            )
        expected_stage = _AIHUB538_CALIBRATION_FAILURE_STAGES.get(
            self.reason_code
        )
        if expected_stage is None or self.failure_stage != expected_stage:
            raise VisualFeedbackContractError(
                "AI Hub 538 calibration failure reason differs"
            )
        require_safe_identifier(self.reason_code, "reason_code")
        require_safe_identifier(self.failure_stage, "failure_stage")
        require_safe_identifier(self.protocol_revision, "protocol_revision")
        for name in (
            "source_manifest_sha256",
            "admission_receipt_sha256",
            "admission_manifest_sha256",
            "admission_policy_sha256",
            "capacity_audit_receipt_sha256",
            "evaluation_protocol_sha256",
            "evaluator_code_sha256",
            "landmark_model_sha256",
            "video_qc_measurement_adapter_code_sha256",
            "av_sync_measurement_adapter_code_sha256",
            "video_qc_measurement_policy_artifact_sha256",
            "video_qc_measurement_policy_sha256",
            "av_sync_measurement_policy_artifact_sha256",
            "av_sync_measurement_policy_sha256",
            "artifact_sha256",
        ):
            require_sha256(getattr(self, name), name)
        decision_hashes = (
            self.video_qc_decision_policy_artifact_sha256,
            self.video_qc_decision_policy_sha256,
            self.av_sync_decision_policy_artifact_sha256,
            self.av_sync_decision_policy_sha256,
        )
        if not isinstance(self.decision_policies_derived, bool) or (
            self.decision_policies_derived != all(value is not None for value in decision_hashes)
        ) or (not self.decision_policies_derived and any(value is not None for value in decision_hashes)):
            raise VisualFeedbackContractError(
                "AI Hub 538 calibration failure decision lineage differs"
            )
        for index, value in enumerate(decision_hashes):
            if value is not None:
                require_sha256(value, f"decision_policy_sha256_{index}")

        count_names = (
            "admitted_item_count",
            "eligible_item_count",
            "metadata_excluded_item_count",
            "development_selected_count",
            "calibration_selected_count",
            "development_measurement_count",
            "calibration_measurement_count",
            "decision_evaluated_count",
            "development_qc_available_count",
            "development_sync_available_count",
            "calibration_qc_available_count",
            "calibration_sync_available_count",
            "calibration_qc_passed_count",
            "calibration_sync_passed_count",
            "qc_lineage_valid_count",
            "sync_lineage_valid_count",
        )
        for name in count_names:
            require_integer(getattr(self, name), name, minimum=0)
        if self.admitted_item_count != (
            self.eligible_item_count + self.metadata_excluded_item_count
        ) or self.eligible_item_count < (
            self.development_selected_count + self.calibration_selected_count
        ):
            raise VisualFeedbackContractError(
                "AI Hub 538 calibration failure admission totals differ"
            )
        if self.development_measurement_count != self.development_selected_count:
            raise VisualFeedbackContractError(
                "AI Hub 538 calibration failure development execution differs"
            )
        if (
            self.samples_per_angle_per_partition_threshold
            != protocol.samples_per_angle_per_partition
            or self.minimum_available_per_angle_threshold
            != protocol.minimum_available_per_angle
            or self.minimum_per_angle_pass_fraction_threshold
            != protocol.minimum_per_angle_pass_fraction
            or self.minimum_global_pass_fraction_threshold
            != protocol.minimum_calibration_pass_fraction
        ):
            raise VisualFeedbackContractError(
                "AI Hub 538 calibration failure thresholds differ"
            )
        require_integer(
            self.samples_per_angle_per_partition_threshold,
            "samples_per_angle_per_partition_threshold",
            minimum=1,
        )
        require_integer(
            self.minimum_available_per_angle_threshold,
            "minimum_available_per_angle_threshold",
            minimum=1,
        )
        _fraction(
            self.minimum_per_angle_pass_fraction_threshold,
            "minimum_per_angle_pass_fraction_threshold",
        )
        _fraction(
            self.minimum_global_pass_fraction_threshold,
            "minimum_global_pass_fraction_threshold",
        )
        if tuple(item.angle for item in self.angle_diagnostics) != (
            AIHUB538_TRAINING_ANGLES
        ) or any(
            item.development_selected_count
            != protocol.samples_per_angle_per_partition
            or item.calibration_selected_count
            != protocol.samples_per_angle_per_partition
            or item.calibration_qc_passed_count
            > item.calibration_qc_available_count
            or item.calibration_sync_passed_count
            > item.calibration_sync_available_count
            for item in self.angle_diagnostics
        ):
            raise VisualFeedbackContractError(
                "AI Hub 538 calibration failure angle coverage differs"
            )
        aggregate_fields = {
            "development_selected_count": "development_selected_count",
            "calibration_selected_count": "calibration_selected_count",
            "development_qc_available_count": "development_qc_available_count",
            "development_sync_available_count": "development_sync_available_count",
            "calibration_qc_available_count": "calibration_qc_available_count",
            "calibration_sync_available_count": "calibration_sync_available_count",
            "calibration_qc_passed_count": "calibration_qc_passed_count",
            "calibration_sync_passed_count": "calibration_sync_passed_count",
            "qc_lineage_valid_count": "qc_lineage_valid_count",
            "sync_lineage_valid_count": "sync_lineage_valid_count",
        }
        if any(
            getattr(self, total_name)
            != sum(getattr(item, row_name) for item in self.angle_diagnostics)
            for total_name, row_name in aggregate_fields.items()
        ):
            raise VisualFeedbackContractError(
                "AI Hub 538 calibration failure aggregate totals differ"
            )
        if (
            self.calibration_qc_passed_count
            > self.calibration_qc_available_count
            or self.calibration_sync_passed_count
            > self.calibration_sync_available_count
        ):
            raise VisualFeedbackContractError(
                "AI Hub 538 calibration failure pass totals differ"
            )
        expected_dev_qc = (
            self.development_qc_available_count
            / self.development_selected_count
        )
        expected_dev_sync = (
            self.development_sync_available_count
            / self.development_selected_count
        )
        if (
            self.development_qc_available_fraction != expected_dev_qc
            or self.development_sync_available_fraction != expected_dev_sync
        ):
            raise VisualFeedbackContractError(
                "AI Hub 538 calibration failure development fractions differ"
            )
        if self.failure_stage == "development_availability":
            if (
                self.decision_policies_derived
                or self.calibration_measurement_count != 0
                or self.decision_evaluated_count != 0
                or any(
                    getattr(self, name) != 0
                    for name in (
                        "calibration_qc_available_count",
                        "calibration_sync_available_count",
                        "calibration_qc_passed_count",
                        "calibration_sync_passed_count",
                        "qc_lineage_valid_count",
                        "sync_lineage_valid_count",
                    )
                )
                or self.calibration_qc_pass_fraction is not None
                or self.calibration_sync_pass_fraction is not None
                or not any(
                    item.development_qc_available_count
                    < protocol.minimum_available_per_angle
                    or item.development_sync_available_count
                    < protocol.minimum_available_per_angle
                    for item in self.angle_diagnostics
                )
            ):
                raise VisualFeedbackContractError(
                    "AI Hub 538 development failure diagnostic differs"
                )
        else:
            expected_qc_fraction = (
                self.calibration_qc_passed_count
                / self.calibration_selected_count
            )
            expected_sync_fraction = (
                self.calibration_sync_passed_count
                / self.calibration_selected_count
            )
            if (
                not self.decision_policies_derived
                or any(
                    item.development_qc_available_count
                    < protocol.minimum_available_per_angle
                    or item.development_sync_available_count
                    < protocol.minimum_available_per_angle
                    for item in self.angle_diagnostics
                )
                or self.calibration_measurement_count
                != self.calibration_selected_count
                or self.decision_evaluated_count
                != self.calibration_selected_count
                or self.calibration_qc_pass_fraction != expected_qc_fraction
                or self.calibration_sync_pass_fraction != expected_sync_fraction
            ):
                raise VisualFeedbackContractError(
                    "AI Hub 538 calibration failure global fractions differ"
                )
            per_angle_failed = any(
                _angle_stop_policy_failed(item, protocol)
                for item in self.angle_diagnostics
            )
            global_failed = (
                expected_qc_fraction
                < protocol.minimum_calibration_pass_fraction
                or expected_sync_fraction
                < protocol.minimum_calibration_pass_fraction
            )
            if (
                self.failure_stage == "per_angle_stop_policy"
                and not per_angle_failed
            ) or (
                self.failure_stage == "global_stop_policy"
                and (per_angle_failed or not global_failed)
            ):
                raise VisualFeedbackContractError(
                    "AI Hub 538 calibration failure stop reason is unproven"
                )
        if self.artifact_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError(
                "AI Hub 538 calibration failure artifact digest differs"
            )

    @classmethod
    def issue(cls, **values: object) -> Aihub538DecisionCalibrationFailureArtifact:
        draft = object.__new__(cls)
        for field_name, field_definition in cls.__dataclass_fields__.items():
            if field_name == "artifact_sha256":
                field_value: object = "0" * 64
            elif field_name in values:
                field_value = values[field_name]
            else:
                field_value = field_definition.default
            object.__setattr__(draft, field_name, field_value)
        return cls(
            **{
                name: getattr(draft, name)
                for name in draft.__dataclass_fields__
                if name != "artifact_sha256"
            },
            artifact_sha256=sha256_document(draft.unsigned_json_object()),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "reason_code": self.reason_code,
            "failure_stage": self.failure_stage,
            "source_dataset_id": "aihub_538",
            "source_partitions": list(self.source_partitions),
            "protocol_revision": self.protocol_revision,
            "source_manifest_sha256": self.source_manifest_sha256,
            "admission_receipt_sha256": self.admission_receipt_sha256,
            "admission_manifest_sha256": self.admission_manifest_sha256,
            "admission_policy_sha256": self.admission_policy_sha256,
            "capacity_audit_receipt_sha256": self.capacity_audit_receipt_sha256,
            "evaluation_protocol_sha256": self.evaluation_protocol_sha256,
            "evaluator_code_sha256": self.evaluator_code_sha256,
            "landmark_model_sha256": self.landmark_model_sha256,
            "video_qc_measurement_adapter_code_sha256": (
                self.video_qc_measurement_adapter_code_sha256
            ),
            "av_sync_measurement_adapter_code_sha256": (
                self.av_sync_measurement_adapter_code_sha256
            ),
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
            "decision_policies_derived": self.decision_policies_derived,
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
            "admitted_item_count": self.admitted_item_count,
            "eligible_item_count": self.eligible_item_count,
            "metadata_excluded_item_count": self.metadata_excluded_item_count,
            "development_selected_count": self.development_selected_count,
            "calibration_selected_count": self.calibration_selected_count,
            "development_measurement_count": self.development_measurement_count,
            "calibration_measurement_count": self.calibration_measurement_count,
            "decision_evaluated_count": self.decision_evaluated_count,
            "development_qc_available_count": self.development_qc_available_count,
            "development_sync_available_count": (
                self.development_sync_available_count
            ),
            "calibration_qc_available_count": self.calibration_qc_available_count,
            "calibration_sync_available_count": (
                self.calibration_sync_available_count
            ),
            "calibration_qc_passed_count": self.calibration_qc_passed_count,
            "calibration_sync_passed_count": self.calibration_sync_passed_count,
            "qc_lineage_valid_count": self.qc_lineage_valid_count,
            "sync_lineage_valid_count": self.sync_lineage_valid_count,
            "development_qc_available_fraction": (
                self.development_qc_available_fraction
            ),
            "development_sync_available_fraction": (
                self.development_sync_available_fraction
            ),
            "calibration_qc_pass_fraction": self.calibration_qc_pass_fraction,
            "calibration_sync_pass_fraction": self.calibration_sync_pass_fraction,
            "samples_per_angle_per_partition_threshold": (
                self.samples_per_angle_per_partition_threshold
            ),
            "minimum_available_per_angle_threshold": (
                self.minimum_available_per_angle_threshold
            ),
            "minimum_per_angle_pass_fraction_threshold": (
                self.minimum_per_angle_pass_fraction_threshold
            ),
            "minimum_global_pass_fraction_threshold": (
                self.minimum_global_pass_fraction_threshold
            ),
            "angle_diagnostics": [
                item.to_json_object() for item in self.angle_diagnostics
            ],
            "aggregate_only": True,
            "locked_partition_accessed": False,
            "promotion_authorized": False,
            "runtime_promotion_authorized": False,
            "raw_identifiers_serialized": False,
            "raw_paths_serialized": False,
            "transcripts_serialized": False,
            "actor_identifiers_serialized": False,
            "attempt_identifiers_serialized": False,
            "sample_identifiers_serialized": False,
            "sample_level_digests_serialized": False,
            "hmac_values_serialized": False,
        }

    def to_json_object(self) -> dict[str, object]:
        return {
            **self.unsigned_json_object(),
            "artifact_sha256": self.artifact_sha256,
        }

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> Aihub538DecisionCalibrationFailureArtifact:
        document = require_mapping(value, "aihub538_calibration_failure")
        expected_fields = set(cls.__dataclass_fields__) | {
            "source_dataset_id",
            "aggregate_only",
            "locked_partition_accessed",
            "promotion_authorized",
            "runtime_promotion_authorized",
            "raw_identifiers_serialized",
            "raw_paths_serialized",
            "transcripts_serialized",
            "actor_identifiers_serialized",
            "attempt_identifiers_serialized",
            "sample_identifiers_serialized",
            "sample_level_digests_serialized",
            "hmac_values_serialized",
        }
        require_exact_keys(document, expected_fields, "aihub538_calibration_failure")
        if (
            document["source_dataset_id"] != "aihub_538"
            or document["source_partitions"]
            != list(AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS)
            or require_boolean(document["aggregate_only"], "aggregate_only")
            is not True
        ):
            raise VisualFeedbackContractError(
                "AI Hub 538 calibration failure source boundary differs"
            )
        for name in (
            "locked_partition_accessed",
            "promotion_authorized",
            "runtime_promotion_authorized",
            "raw_identifiers_serialized",
            "raw_paths_serialized",
            "transcripts_serialized",
            "actor_identifiers_serialized",
            "attempt_identifiers_serialized",
            "sample_identifiers_serialized",
            "sample_level_digests_serialized",
            "hmac_values_serialized",
        ):
            if require_boolean(document[name], name) is not False:
                raise VisualFeedbackContractError(
                    "AI Hub 538 calibration failure privacy flags differ"
                )
        angle_values = document["angle_diagnostics"]
        if not isinstance(angle_values, list):
            raise VisualFeedbackContractError(
                "angle_diagnostics must be an array"
            )
        sha_names = (
            "source_manifest_sha256",
            "admission_receipt_sha256",
            "admission_manifest_sha256",
            "admission_policy_sha256",
            "capacity_audit_receipt_sha256",
            "evaluation_protocol_sha256",
            "evaluator_code_sha256",
            "landmark_model_sha256",
            "video_qc_measurement_adapter_code_sha256",
            "av_sync_measurement_adapter_code_sha256",
            "video_qc_measurement_policy_artifact_sha256",
            "video_qc_measurement_policy_sha256",
            "av_sync_measurement_policy_artifact_sha256",
            "av_sync_measurement_policy_sha256",
            "artifact_sha256",
        )
        optional_sha_names = (
            "video_qc_decision_policy_artifact_sha256",
            "video_qc_decision_policy_sha256",
            "av_sync_decision_policy_artifact_sha256",
            "av_sync_decision_policy_sha256",
        )
        count_names = (
            "admitted_item_count",
            "eligible_item_count",
            "metadata_excluded_item_count",
            "development_selected_count",
            "calibration_selected_count",
            "development_measurement_count",
            "calibration_measurement_count",
            "decision_evaluated_count",
            "development_qc_available_count",
            "development_sync_available_count",
            "calibration_qc_available_count",
            "calibration_sync_available_count",
            "calibration_qc_passed_count",
            "calibration_sync_passed_count",
            "qc_lineage_valid_count",
            "sync_lineage_valid_count",
            "samples_per_angle_per_partition_threshold",
            "minimum_available_per_angle_threshold",
        )
        return cls(
            reason_code=require_safe_identifier(
                document["reason_code"], "reason_code"
            ),
            failure_stage=require_safe_identifier(
                document["failure_stage"], "failure_stage"
            ),
            protocol_revision=require_safe_identifier(
                document["protocol_revision"], "protocol_revision"
            ),
            **{name: require_sha256(document[name], name) for name in sha_names},
            **{
                name: _optional_sha256(document[name], name)
                for name in optional_sha_names
            },
            decision_policies_derived=require_boolean(
                document["decision_policies_derived"],
                "decision_policies_derived",
            ),
            **{
                name: require_integer(document[name], name, minimum=0)
                for name in count_names
            },
            development_qc_available_fraction=_fraction(
                document["development_qc_available_fraction"],
                "development_qc_available_fraction",
            ),
            development_sync_available_fraction=_fraction(
                document["development_sync_available_fraction"],
                "development_sync_available_fraction",
            ),
            calibration_qc_pass_fraction=_optional_fraction(
                document["calibration_qc_pass_fraction"],
                "calibration_qc_pass_fraction",
            ),
            calibration_sync_pass_fraction=_optional_fraction(
                document["calibration_sync_pass_fraction"],
                "calibration_sync_pass_fraction",
            ),
            minimum_per_angle_pass_fraction_threshold=_fraction(
                document["minimum_per_angle_pass_fraction_threshold"],
                "minimum_per_angle_pass_fraction_threshold",
            ),
            minimum_global_pass_fraction_threshold=_fraction(
                document["minimum_global_pass_fraction_threshold"],
                "minimum_global_pass_fraction_threshold",
            ),
            angle_diagnostics=tuple(
                Aihub538CalibrationAngleEvidence.from_json_object(item)
                for item in angle_values
            ),
            source_partitions=tuple(document["source_partitions"]),
            status=require_safe_identifier(document["status"], "status"),
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
        )


@dataclass(frozen=True, slots=True)
class Aihub538AggregateReasonCount:
    """One fixed-catalog aggregate occurrence count, never a sample row."""

    reason_code: str
    occurrence_count: int

    def __post_init__(self) -> None:
        require_safe_identifier(self.reason_code, "reason_code")
        require_integer(self.occurrence_count, "occurrence_count", minimum=0)

    def to_json_object(self) -> dict[str, object]:
        return {
            "reason_code": self.reason_code,
            "occurrence_count": self.occurrence_count,
        }

    @classmethod
    def from_json_object(cls, value: object) -> Aihub538AggregateReasonCount:
        document = require_mapping(value, "aggregate_reason_count")
        require_exact_keys(
            document,
            {"reason_code", "occurrence_count"},
            "aggregate_reason_count",
        )
        return cls(
            reason_code=require_safe_identifier(
                document["reason_code"], "reason_code"
            ),
            occurrence_count=require_integer(
                document["occurrence_count"], "occurrence_count", minimum=0
            ),
        )


@dataclass(frozen=True, slots=True)
class Aihub538QcThresholdMarginHistogram:
    """Four coarse counts around one frozen threshold; no observed value leaves."""

    reason_code: str
    failed_beyond_margin_count: int
    failed_within_margin_count: int
    passed_within_margin_count: int
    passed_beyond_margin_count: int

    def __post_init__(self) -> None:
        require_safe_identifier(self.reason_code, "reason_code")
        for name in (
            "failed_beyond_margin_count",
            "failed_within_margin_count",
            "passed_within_margin_count",
            "passed_beyond_margin_count",
        ):
            require_integer(getattr(self, name), name, minimum=0)

    @property
    def population_count(self) -> int:
        return sum(
            getattr(self, name)
            for name in (
                "failed_beyond_margin_count",
                "failed_within_margin_count",
                "passed_within_margin_count",
                "passed_beyond_margin_count",
            )
        )

    @property
    def failure_count(self) -> int:
        return (
            self.failed_beyond_margin_count + self.failed_within_margin_count
        )

    def to_json_object(self) -> dict[str, object]:
        return {
            name: getattr(self, name) for name in self.__dataclass_fields__
        }

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> Aihub538QcThresholdMarginHistogram:
        document = require_mapping(value, "qc_threshold_margin_histogram")
        fields = set(cls.__dataclass_fields__)
        require_exact_keys(document, fields, "qc_threshold_margin_histogram")
        return cls(
            reason_code=require_safe_identifier(
                document["reason_code"], "reason_code"
            ),
            **{
                name: require_integer(document[name], name, minimum=0)
                for name in fields
                if name != "reason_code"
            },
        )


@dataclass(frozen=True, slots=True)
class Aihub538QcOptionalMetricMissingCombinations:
    """Distinct bitmask counts for the two nullable-typed QC metrics."""

    lip_edge_only_missing_count: int
    lip_center_step_only_missing_count: int
    both_optional_metrics_missing_count: int

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            require_integer(getattr(self, name), name, minimum=0)
        if (
            self.lip_edge_only_missing_count != 0
            or self.both_optional_metrics_missing_count != 0
        ):
            raise VisualFeedbackContractError(
                "source-available lip edge metric cannot be missing"
            )

    @property
    def distinct_missing_count(self) -> int:
        return (
            self.lip_edge_only_missing_count
            + self.lip_center_step_only_missing_count
            + self.both_optional_metrics_missing_count
        )

    @property
    def lip_edge_missing_count(self) -> int:
        return (
            self.lip_edge_only_missing_count
            + self.both_optional_metrics_missing_count
        )

    @property
    def lip_center_step_missing_count(self) -> int:
        return (
            self.lip_center_step_only_missing_count
            + self.both_optional_metrics_missing_count
        )

    def to_json_object(self) -> dict[str, object]:
        return {
            name: getattr(self, name) for name in self.__dataclass_fields__
        }

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> Aihub538QcOptionalMetricMissingCombinations:
        document = require_mapping(
            value,
            "qc_optional_metric_missing_combinations",
        )
        fields = set(cls.__dataclass_fields__)
        require_exact_keys(
            document,
            fields,
            "qc_optional_metric_missing_combinations",
        )
        return cls(
            **{
                name: require_integer(document[name], name, minimum=0)
                for name in fields
            }
        )


def _require_reason_catalog(
    rows: tuple[Aihub538AggregateReasonCount, ...],
    expected: tuple[str, ...],
    name: str,
) -> None:
    if any(type(row) is not Aihub538AggregateReasonCount for row in rows) or tuple(
        row.reason_code for row in rows
    ) != expected:
        raise VisualFeedbackContractError(f"{name} catalog differs")


def _require_histogram_catalog(
    rows: tuple[Aihub538QcThresholdMarginHistogram, ...],
    name: str,
) -> None:
    expected = tuple(
        item[0] for item in AIHUB538_QC_THRESHOLD_DIAGNOSTIC_SPECS
    )
    if rows and (
        any(type(row) is not Aihub538QcThresholdMarginHistogram for row in rows)
        or tuple(row.reason_code for row in rows) != expected
    ):
        raise VisualFeedbackContractError(f"{name} catalog differs")


def _require_reason_occurrence_coverage(
    rows: tuple[Aihub538AggregateReasonCount, ...],
    unavailable_count: int,
    name: str,
) -> None:
    """Validate overlapping reason occurrences against a distinct population."""

    values = tuple(item.occurrence_count for item in rows)
    if (
        any(value > unavailable_count for value in values)
        or (unavailable_count == 0 and any(values))
        or (unavailable_count > 0 and sum(values) < unavailable_count)
    ):
        raise VisualFeedbackContractError(f"{name} coverage differs")


def _reason_count_map(
    rows: tuple[Aihub538AggregateReasonCount, ...],
) -> dict[str, int]:
    return {item.reason_code: item.occurrence_count for item in rows}


def _require_partition_availability_reconciliation(
    *,
    reason_rows: tuple[Aihub538AggregateReasonCount, ...],
    optional_metric_missing_combinations: (
        Aihub538QcOptionalMetricMissingCombinations
    ),
    selected_count: int,
    overall_qc_available_count: int,
    source_candidate_available_count: int,
    name: str,
) -> dict[str, int]:
    if not (
        0
        <= overall_qc_available_count
        <= source_candidate_available_count
        <= selected_count
    ):
        raise VisualFeedbackContractError(
            f"{name} availability counts differ"
        )
    counts = _reason_count_map(reason_rows)
    source_unavailable_count = selected_count - source_candidate_available_count
    derived_unavailable_count = (
        source_candidate_available_count - overall_qc_available_count
    )
    _require_reason_occurrence_coverage(
        tuple(
            item
            for item in reason_rows
            if item.reason_code
            in AIHUB538_QC_SOURCE_CANDIDATE_UNAVAILABLE_REASON_CODES
        ),
        source_unavailable_count,
        f"{name} source unavailable reason",
    )
    if (
        type(optional_metric_missing_combinations)
        is not Aihub538QcOptionalMetricMissingCombinations
        or optional_metric_missing_combinations.distinct_missing_count
        != derived_unavailable_count
        or counts["video_qc_lip_edge_metric_unavailable"]
        != optional_metric_missing_combinations.lip_edge_missing_count
        or counts["video_qc_lip_center_step_metric_unavailable"]
        != optional_metric_missing_combinations.lip_center_step_missing_count
    ):
        raise VisualFeedbackContractError(
            f"{name} optional metric missing combinations differ"
        )
    return counts


def _expected_histogram_population(
    *,
    reason_code: str,
    source_candidate_available_count: int,
    candidate_reason_counts: dict[str, int],
) -> int:
    missing_reason_by_decision_reason = dict(
        AIHUB538_QC_UNBINNED_METRIC_REASON_BY_DECISION_REASON
    )
    missing_reason = missing_reason_by_decision_reason.get(reason_code)
    return source_candidate_available_count - (
        candidate_reason_counts.get(missing_reason, 0)
        if missing_reason is not None
        else 0
    )


@dataclass(frozen=True, slots=True)
class Aihub538AngleQcFailureDiagnostic:
    """Per-angle aggregate reason counts and threshold-relative histograms."""

    angle: str
    development_source_candidate_available_count: int
    calibration_source_candidate_available_count: int
    development_optional_metric_missing_combinations: (
        Aihub538QcOptionalMetricMissingCombinations
    )
    calibration_optional_metric_missing_combinations: (
        Aihub538QcOptionalMetricMissingCombinations
    )
    development_candidate_unavailable_reason_counts: tuple[
        Aihub538AggregateReasonCount, ...
    ]
    calibration_candidate_unavailable_reason_counts: tuple[
        Aihub538AggregateReasonCount, ...
    ]
    calibration_decision_failure_reason_counts: tuple[
        Aihub538AggregateReasonCount, ...
    ]
    development_threshold_margin_histograms: tuple[
        Aihub538QcThresholdMarginHistogram, ...
    ]
    calibration_threshold_margin_histograms: tuple[
        Aihub538QcThresholdMarginHistogram, ...
    ]

    def __post_init__(self) -> None:
        if self.angle not in AIHUB538_TRAINING_ANGLES:
            raise VisualFeedbackContractError(
                "AI Hub 538 QC diagnostic angle differs"
            )
        require_integer(
            self.development_source_candidate_available_count,
            "development_source_candidate_available_count",
            minimum=0,
        )
        require_integer(
            self.calibration_source_candidate_available_count,
            "calibration_source_candidate_available_count",
            minimum=0,
        )
        if (
            type(self.development_optional_metric_missing_combinations)
            is not Aihub538QcOptionalMetricMissingCombinations
            or type(self.calibration_optional_metric_missing_combinations)
            is not Aihub538QcOptionalMetricMissingCombinations
        ):
            raise VisualFeedbackContractError(
                "AI Hub 538 optional metric combination type differs"
            )
        _require_reason_catalog(
            self.development_candidate_unavailable_reason_counts,
            AIHUB538_QC_CANDIDATE_UNAVAILABLE_REASON_CODES,
            "development candidate reason",
        )
        _require_reason_catalog(
            self.calibration_candidate_unavailable_reason_counts,
            AIHUB538_QC_CANDIDATE_UNAVAILABLE_REASON_CODES,
            "calibration candidate reason",
        )
        _require_reason_catalog(
            self.calibration_decision_failure_reason_counts,
            AIHUB538_QC_DECISION_FAILURE_REASON_CODES,
            "calibration decision reason",
        )
        _require_histogram_catalog(
            self.development_threshold_margin_histograms,
            "development threshold histogram",
        )
        _require_histogram_catalog(
            self.calibration_threshold_margin_histograms,
            "calibration threshold histogram",
        )
        if bool(self.development_threshold_margin_histograms) != bool(
            self.calibration_threshold_margin_histograms
        ):
            raise VisualFeedbackContractError(
                "AI Hub 538 QC diagnostic partition histogram shape differs"
            )

    def to_json_object(self) -> dict[str, object]:
        return {
            "angle": self.angle,
            "development_source_candidate_available_count": (
                self.development_source_candidate_available_count
            ),
            "calibration_source_candidate_available_count": (
                self.calibration_source_candidate_available_count
            ),
            "development_optional_metric_missing_combinations": (
                self.development_optional_metric_missing_combinations.to_json_object()
            ),
            "calibration_optional_metric_missing_combinations": (
                self.calibration_optional_metric_missing_combinations.to_json_object()
            ),
            "development_candidate_unavailable_reason_counts": [
                item.to_json_object()
                for item in self.development_candidate_unavailable_reason_counts
            ],
            "calibration_candidate_unavailable_reason_counts": [
                item.to_json_object()
                for item in self.calibration_candidate_unavailable_reason_counts
            ],
            "calibration_decision_failure_reason_counts": [
                item.to_json_object()
                for item in self.calibration_decision_failure_reason_counts
            ],
            "development_threshold_margin_histograms": [
                item.to_json_object()
                for item in self.development_threshold_margin_histograms
            ],
            "calibration_threshold_margin_histograms": [
                item.to_json_object()
                for item in self.calibration_threshold_margin_histograms
            ],
        }

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> Aihub538AngleQcFailureDiagnostic:
        document = require_mapping(value, "angle_qc_failure_diagnostic")
        fields = set(cls.__dataclass_fields__)
        require_exact_keys(document, fields, "angle_qc_failure_diagnostic")

        def _array(name: str) -> list[object]:
            array = document[name]
            if not isinstance(array, list):
                raise VisualFeedbackContractError(f"{name} must be an array")
            return array

        return cls(
            angle=require_safe_identifier(document["angle"], "angle"),
            development_source_candidate_available_count=require_integer(
                document["development_source_candidate_available_count"],
                "development_source_candidate_available_count",
                minimum=0,
            ),
            calibration_source_candidate_available_count=require_integer(
                document["calibration_source_candidate_available_count"],
                "calibration_source_candidate_available_count",
                minimum=0,
            ),
            development_optional_metric_missing_combinations=(
                Aihub538QcOptionalMetricMissingCombinations.from_json_object(
                    document[
                        "development_optional_metric_missing_combinations"
                    ]
                )
            ),
            calibration_optional_metric_missing_combinations=(
                Aihub538QcOptionalMetricMissingCombinations.from_json_object(
                    document[
                        "calibration_optional_metric_missing_combinations"
                    ]
                )
            ),
            development_candidate_unavailable_reason_counts=tuple(
                Aihub538AggregateReasonCount.from_json_object(item)
                for item in _array(
                    "development_candidate_unavailable_reason_counts"
                )
            ),
            calibration_candidate_unavailable_reason_counts=tuple(
                Aihub538AggregateReasonCount.from_json_object(item)
                for item in _array(
                    "calibration_candidate_unavailable_reason_counts"
                )
            ),
            calibration_decision_failure_reason_counts=tuple(
                Aihub538AggregateReasonCount.from_json_object(item)
                for item in _array("calibration_decision_failure_reason_counts")
            ),
            development_threshold_margin_histograms=tuple(
                Aihub538QcThresholdMarginHistogram.from_json_object(item)
                for item in _array("development_threshold_margin_histograms")
            ),
            calibration_threshold_margin_histograms=tuple(
                Aihub538QcThresholdMarginHistogram.from_json_object(item)
                for item in _array("calibration_threshold_margin_histograms")
            ),
        )


@dataclass(frozen=True, slots=True)
class Aihub538DecisionCalibrationFailureArtifactV2:
    """V1-compatible summary plus typed, aggregate-only QC failure diagnostics."""

    failure_summary: Aihub538DecisionCalibrationFailureArtifact
    qc_diagnostic_protocol_sha256: str
    diagnostic_video_qc_decision_policy: P8RuntimePolicyArtifact | None
    angle_qc_diagnostics: tuple[Aihub538AngleQcFailureDiagnostic, ...]
    artifact_sha256: str
    schema_version: str = (
        AIHUB538_MULTI_DECISION_CALIBRATION_FAILURE_V2_SCHEMA_VERSION
    )

    def __post_init__(self) -> None:
        if (
            self.schema_version
            != AIHUB538_MULTI_DECISION_CALIBRATION_FAILURE_V2_SCHEMA_VERSION
            or type(self.failure_summary)
            is not Aihub538DecisionCalibrationFailureArtifact
            or self.qc_diagnostic_protocol_sha256
            != AIHUB538_QC_FAILURE_DIAGNOSTIC_PROTOCOL_SHA256
        ):
            raise VisualFeedbackContractError(
                "AI Hub 538 QC failure diagnostic scope differs"
            )
        require_sha256(
            self.qc_diagnostic_protocol_sha256,
            "qc_diagnostic_protocol_sha256",
        )
        if tuple(item.angle for item in self.angle_qc_diagnostics) != (
            AIHUB538_TRAINING_ANGLES
        ) or any(
            type(item) is not Aihub538AngleQcFailureDiagnostic
            for item in self.angle_qc_diagnostics
        ):
            raise VisualFeedbackContractError(
                "AI Hub 538 QC failure diagnostic angle coverage differs"
            )
        summary_by_angle = {
            item.angle: item for item in self.failure_summary.angle_diagnostics
        }
        development_only = (
            self.failure_summary.failure_stage == "development_availability"
        )
        if development_only:
            if self.diagnostic_video_qc_decision_policy is not None:
                raise VisualFeedbackContractError(
                    "development failure cannot contain a decision policy"
                )
        else:
            policy = self.diagnostic_video_qc_decision_policy
            if (
                type(policy) is not P8RuntimePolicyArtifact
                or policy.policy_kind != "video_qc_decision"
                or not isinstance(
                    policy.policy_document, VideoQcDecisionSemanticPolicy
                )
                or policy.policy_revision
                != f"{self.failure_summary.protocol_revision}.video_qc"
                or policy.policy_document.expected_measurement_policy_sha256
                != self.failure_summary.video_qc_measurement_policy_sha256
                or policy.policy_sha256
                != self.failure_summary.video_qc_decision_policy_sha256
                or policy.artifact_sha256
                != self.failure_summary.video_qc_decision_policy_artifact_sha256
            ):
                raise VisualFeedbackContractError(
                    "AI Hub 538 QC diagnostic decision policy differs"
                )
            expected_histogram_count = len(
                AIHUB538_QC_THRESHOLD_DIAGNOSTIC_SPECS
            )
            if any(
                len(diagnostic.development_threshold_margin_histograms)
                != expected_histogram_count
                or len(diagnostic.calibration_threshold_margin_histograms)
                != expected_histogram_count
                for diagnostic in self.angle_qc_diagnostics
            ):
                raise VisualFeedbackContractError(
                    "calibration failure must contain complete QC histograms"
                )
        for diagnostic in self.angle_qc_diagnostics:
            summary = summary_by_angle[diagnostic.angle]
            development_candidate_counts = (
                _require_partition_availability_reconciliation(
                    reason_rows=(
                        diagnostic.development_candidate_unavailable_reason_counts
                    ),
                    optional_metric_missing_combinations=(
                        diagnostic.development_optional_metric_missing_combinations
                    ),
                    selected_count=summary.development_selected_count,
                    overall_qc_available_count=(
                        summary.development_qc_available_count
                    ),
                    source_candidate_available_count=(
                        diagnostic.development_source_candidate_available_count
                    ),
                    name="development QC",
                )
            )
            calibration_candidate_counts = _reason_count_map(
                diagnostic.calibration_candidate_unavailable_reason_counts
            )
            decision_counts = {
                item.reason_code: item.occurrence_count
                for item in diagnostic.calibration_decision_failure_reason_counts
            }
            if development_only:
                if (
                    diagnostic.calibration_source_candidate_available_count != 0
                    or (
                        diagnostic.calibration_optional_metric_missing_combinations
                        .distinct_missing_count
                        != 0
                    )
                    or any(calibration_candidate_counts.values())
                    or any(decision_counts.values())
                    or diagnostic.development_threshold_margin_histograms
                    or diagnostic.calibration_threshold_margin_histograms
                ):
                    raise VisualFeedbackContractError(
                        "development QC failure diagnostic execution differs"
                    )
                continue
            calibration_candidate_counts = (
                _require_partition_availability_reconciliation(
                    reason_rows=(
                        diagnostic.calibration_candidate_unavailable_reason_counts
                    ),
                    optional_metric_missing_combinations=(
                        diagnostic.calibration_optional_metric_missing_combinations
                    ),
                    selected_count=summary.calibration_selected_count,
                    overall_qc_available_count=(
                        summary.calibration_qc_available_count
                    ),
                    source_candidate_available_count=(
                        diagnostic.calibration_source_candidate_available_count
                    ),
                    name="calibration QC",
                )
            )
            calibration_failed = (
                summary.calibration_selected_count
                - summary.calibration_qc_passed_count
            )
            source_unavailable_count = (
                summary.calibration_selected_count
                - diagnostic.calibration_source_candidate_available_count
            )
            if (
                decision_counts["video_qc_source_candidate_unavailable"]
                != source_unavailable_count
                or source_unavailable_count > calibration_failed
            ):
                raise VisualFeedbackContractError(
                    "calibration QC reason totals differ"
                )
            _require_reason_occurrence_coverage(
                tuple(
                    item
                    for item in (
                        diagnostic.calibration_decision_failure_reason_counts
                    )
                    if item.reason_code
                    != "video_qc_source_candidate_unavailable"
                ),
                calibration_failed - source_unavailable_count,
                "calibration QC threshold decision reason",
            )
            calibration_histograms = {
                item.reason_code: item
                for item in diagnostic.calibration_threshold_margin_histograms
            }
            unbinned_reasons = dict(
                AIHUB538_QC_UNBINNED_METRIC_REASON_BY_DECISION_REASON
            )
            for histogram in diagnostic.development_threshold_margin_histograms:
                if histogram.population_count != _expected_histogram_population(
                    reason_code=histogram.reason_code,
                    source_candidate_available_count=(
                        diagnostic.development_source_candidate_available_count
                    ),
                    candidate_reason_counts=development_candidate_counts,
                ):
                    raise VisualFeedbackContractError(
                        "development QC histogram population differs"
                    )
            for reason_code, histogram in calibration_histograms.items():
                if (
                    histogram.population_count
                    != _expected_histogram_population(
                        reason_code=reason_code,
                        source_candidate_available_count=(
                            diagnostic.calibration_source_candidate_available_count
                        ),
                        candidate_reason_counts=calibration_candidate_counts,
                    )
                    or histogram.failure_count
                    + calibration_candidate_counts.get(
                        unbinned_reasons.get(reason_code, ""),
                        0,
                    )
                    != decision_counts[reason_code]
                ):
                    raise VisualFeedbackContractError(
                        "calibration QC histogram totals differ"
                    )
        require_sha256(self.artifact_sha256, "artifact_sha256")
        if self.artifact_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError(
                "AI Hub 538 QC failure diagnostic digest differs"
            )

    @classmethod
    def issue(
        cls,
        *,
        failure_summary: Aihub538DecisionCalibrationFailureArtifact,
        diagnostic_video_qc_decision_policy: P8RuntimePolicyArtifact | None,
        angle_qc_diagnostics: tuple[Aihub538AngleQcFailureDiagnostic, ...],
    ) -> Aihub538DecisionCalibrationFailureArtifactV2:
        draft = object.__new__(cls)
        object.__setattr__(draft, "failure_summary", failure_summary)
        object.__setattr__(
            draft,
            "qc_diagnostic_protocol_sha256",
            AIHUB538_QC_FAILURE_DIAGNOSTIC_PROTOCOL_SHA256,
        )
        object.__setattr__(
            draft,
            "diagnostic_video_qc_decision_policy",
            diagnostic_video_qc_decision_policy,
        )
        object.__setattr__(draft, "angle_qc_diagnostics", angle_qc_diagnostics)
        object.__setattr__(draft, "artifact_sha256", "0" * 64)
        object.__setattr__(
            draft,
            "schema_version",
            AIHUB538_MULTI_DECISION_CALIBRATION_FAILURE_V2_SCHEMA_VERSION,
        )
        return cls(
            failure_summary=failure_summary,
            qc_diagnostic_protocol_sha256=(
                AIHUB538_QC_FAILURE_DIAGNOSTIC_PROTOCOL_SHA256
            ),
            diagnostic_video_qc_decision_policy=(
                diagnostic_video_qc_decision_policy
            ),
            angle_qc_diagnostics=angle_qc_diagnostics,
            artifact_sha256=sha256_document(draft.unsigned_json_object()),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": "failed_closed",
            "source_dataset_id": "aihub_538",
            "failure_summary": self.failure_summary.to_json_object(),
            "qc_diagnostic_protocol_sha256": (
                self.qc_diagnostic_protocol_sha256
            ),
            "diagnostic_video_qc_decision_policy": (
                self.diagnostic_video_qc_decision_policy.to_json_object()
                if self.diagnostic_video_qc_decision_policy is not None
                else None
            ),
            "angle_qc_diagnostics": [
                item.to_json_object() for item in self.angle_qc_diagnostics
            ],
            "aggregate_only": True,
            "promotion_authorized": False,
            "runtime_promotion_authorized": False,
            "locked_partition_accessed": False,
            "raw_identifiers_serialized": False,
            "raw_paths_serialized": False,
            "transcripts_serialized": False,
            "actor_identifiers_serialized": False,
            "attempt_identifiers_serialized": False,
            "sample_identifiers_serialized": False,
            "sample_level_digests_serialized": False,
            "sample_level_metric_values_serialized": False,
            "sample_level_statistics_serialized": False,
        }

    def to_json_object(self) -> dict[str, object]:
        return {
            **self.unsigned_json_object(),
            "artifact_sha256": self.artifact_sha256,
        }

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> Aihub538DecisionCalibrationFailureArtifactV2:
        document = require_mapping(value, "aihub538_calibration_failure_v2")
        expected_fields = {
            "schema_version",
            "status",
            "source_dataset_id",
            "failure_summary",
            "qc_diagnostic_protocol_sha256",
            "diagnostic_video_qc_decision_policy",
            "angle_qc_diagnostics",
            "aggregate_only",
            "promotion_authorized",
            "runtime_promotion_authorized",
            "locked_partition_accessed",
            "raw_identifiers_serialized",
            "raw_paths_serialized",
            "transcripts_serialized",
            "actor_identifiers_serialized",
            "attempt_identifiers_serialized",
            "sample_identifiers_serialized",
            "sample_level_digests_serialized",
            "sample_level_metric_values_serialized",
            "sample_level_statistics_serialized",
            "artifact_sha256",
        }
        require_exact_keys(document, expected_fields, "aihub538_calibration_failure_v2")
        if (
            document["status"] != "failed_closed"
            or document["source_dataset_id"] != "aihub_538"
            or require_boolean(document["aggregate_only"], "aggregate_only")
            is not True
        ):
            raise VisualFeedbackContractError(
                "AI Hub 538 QC failure diagnostic source boundary differs"
            )
        for name in (
            "promotion_authorized",
            "runtime_promotion_authorized",
            "locked_partition_accessed",
            "raw_identifiers_serialized",
            "raw_paths_serialized",
            "transcripts_serialized",
            "actor_identifiers_serialized",
            "attempt_identifiers_serialized",
            "sample_identifiers_serialized",
            "sample_level_digests_serialized",
            "sample_level_metric_values_serialized",
            "sample_level_statistics_serialized",
        ):
            if require_boolean(document[name], name) is not False:
                raise VisualFeedbackContractError(
                    "AI Hub 538 QC failure diagnostic privacy flags differ"
                )
        angle_values = document["angle_qc_diagnostics"]
        if not isinstance(angle_values, list):
            raise VisualFeedbackContractError(
                "angle_qc_diagnostics must be an array"
            )
        policy_value = document["diagnostic_video_qc_decision_policy"]
        return cls(
            failure_summary=(
                Aihub538DecisionCalibrationFailureArtifact.from_json_object(
                    document["failure_summary"]
                )
            ),
            qc_diagnostic_protocol_sha256=require_sha256(
                document["qc_diagnostic_protocol_sha256"],
                "qc_diagnostic_protocol_sha256",
            ),
            diagnostic_video_qc_decision_policy=(
                None
                if policy_value is None
                else P8RuntimePolicyArtifact.from_json_object(policy_value)
            ),
            angle_qc_diagnostics=tuple(
                Aihub538AngleQcFailureDiagnostic.from_json_object(item)
                for item in angle_values
            ),
            artifact_sha256=require_sha256(
                document["artifact_sha256"], "artifact_sha256"
            ),
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
        )

    @property
    def reason_code(self) -> str:
        return self.failure_summary.reason_code

    @property
    def failure_stage(self) -> str:
        return self.failure_summary.failure_stage

    @property
    def admitted_item_count(self) -> int:
        return self.failure_summary.admitted_item_count

    @property
    def eligible_item_count(self) -> int:
        return self.failure_summary.eligible_item_count

    @property
    def metadata_excluded_item_count(self) -> int:
        return self.failure_summary.metadata_excluded_item_count

    @property
    def calibration_qc_pass_fraction(self) -> float | None:
        return self.failure_summary.calibration_qc_pass_fraction

    @property
    def calibration_sync_pass_fraction(self) -> float | None:
        return self.failure_summary.calibration_sync_pass_fraction

    def __getattr__(self, name: str) -> object:
        """Expose the immutable v1 summary surface without duplicating fields."""

        if name in Aihub538DecisionCalibrationFailureArtifact.__dataclass_fields__:
            return getattr(self.failure_summary, name)
        raise AttributeError(name)


def _angle_stop_policy_failed(
    item: Aihub538CalibrationAngleEvidence,
    protocol: Aihub538DecisionCalibrationProtocol,
) -> bool:
    selected = item.calibration_selected_count
    return (
        item.development_selected_count
        != protocol.samples_per_angle_per_partition
        or selected != protocol.samples_per_angle_per_partition
        or item.calibration_qc_available_count
        < protocol.minimum_available_per_angle
        or item.calibration_sync_available_count
        < protocol.minimum_available_per_angle
        or item.qc_lineage_valid_count != selected
        or item.sync_lineage_valid_count != selected
        or item.calibration_qc_passed_count / selected
        < protocol.minimum_per_angle_pass_fraction
        or item.calibration_sync_passed_count / selected
        < protocol.minimum_per_angle_pass_fraction
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
    source_partitions: tuple[str, ...] = ("TS174", "TL174")
    admitted_item_count: int = 0
    metadata_excluded_item_count: int = 0
    status: str = "actual_media_calibration_evaluated"
    schema_version: str = AIHUB538_DECISION_CALIBRATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.source_partitions == ("TS174", "TL174"):
            expected_schema = AIHUB538_DECISION_CALIBRATION_SCHEMA_VERSION
        elif self.source_partitions == AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS:
            expected_schema = AIHUB538_MULTI_DECISION_CALIBRATION_SCHEMA_VERSION
        else:
            raise VisualFeedbackContractError(
                "AI Hub 538 calibration source partitions differ"
            )
        if self.schema_version != expected_schema:
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
            "admitted_item_count",
            "metadata_excluded_item_count",
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
        if self.source_partitions == AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS:
            if (
                self.admitted_item_count
                != self.eligible_item_count + self.metadata_excluded_item_count
                or self.metadata_excluded_item_count < 0
            ):
                raise VisualFeedbackContractError(
                    "AI Hub 538 metadata exclusion counts differ"
                )
        elif self.admitted_item_count != 0 or self.metadata_excluded_item_count != 0:
            raise VisualFeedbackContractError(
                "AI Hub 538 legacy metadata exclusion counts differ"
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
        source_partitions = values.get(
            "source_partitions",
            cls.__dataclass_fields__["source_partitions"].default,
        )
        for field_name, field_definition in cls.__dataclass_fields__.items():
            if field_name == "receipt_sha256":
                field_value: object = "0" * 64
            elif field_name in values:
                field_value = values[field_name]
            elif field_name == "schema_version" and source_partitions == (
                AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS
            ):
                field_value = AIHUB538_MULTI_DECISION_CALIBRATION_SCHEMA_VERSION
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
        document = {
            "schema_version": self.schema_version,
            "status": self.status,
            "source_dataset_id": "aihub_538",
            "source_partitions": list(self.source_partitions),
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
        if self.source_partitions == AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS:
            document.update(
                {
                    "admitted_item_count": self.admitted_item_count,
                    "metadata_excluded_item_count": (
                        self.metadata_excluded_item_count
                    ),
                    "metadata_bbox_eligibility_rule": (
                        AIHUB538_MULTI_METADATA_BBOX_ELIGIBILITY_RULE
                    ),
                    "metadata_unavailable_excluded_before_selection": True,
                    "post_exclusion_exact_quota_revalidated": True,
                    "hidden_skip_allowed": False,
                    "bbox_endpoint_repair_allowed": False,
                }
            )
        return document

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> Aihub538DecisionCalibrationEvidence:
        document = require_mapping(value, "aihub538_decision_calibration_evidence")
        schema_version = require_safe_identifier(
            document.get("schema_version"), "schema_version"
        )
        source_partitions_value = document.get("source_partitions")
        is_legacy = (
            schema_version == AIHUB538_DECISION_CALIBRATION_SCHEMA_VERSION
            and source_partitions_value == ["TS174", "TL174"]
        )
        is_multi = (
            schema_version == AIHUB538_MULTI_DECISION_CALIBRATION_SCHEMA_VERSION
            and source_partitions_value
            == list(AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS)
        )
        if not is_legacy and not is_multi:
            raise VisualFeedbackContractError(
                "AI Hub 538 decision calibration evidence schema boundary differs"
            )
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
        if is_multi:
            fields.update(
                {
                    "admitted_item_count",
                    "metadata_excluded_item_count",
                    "metadata_bbox_eligibility_rule",
                    "metadata_unavailable_excluded_before_selection",
                    "post_exclusion_exact_quota_revalidated",
                    "hidden_skip_allowed",
                    "bbox_endpoint_repair_allowed",
                }
            )
        require_exact_keys(document, fields, "aihub538_decision_calibration_evidence")
        if (
            document["source_dataset_id"] != "aihub_538"
            or document["source_partitions"]
            not in [
                ["TS174", "TL174"],
                list(AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS),
            ]
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
        if is_multi:
            true_fields += (
                "metadata_unavailable_excluded_before_selection",
                "post_exclusion_exact_quota_revalidated",
            )
            false_fields += (
                "hidden_skip_allowed",
                "bbox_endpoint_repair_allowed",
            )
            if document["metadata_bbox_eligibility_rule"] != (
                AIHUB538_MULTI_METADATA_BBOX_ELIGIBILITY_RULE
            ):
                raise VisualFeedbackContractError(
                    "metadata bbox eligibility rule differs"
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
        if is_multi:
            count_fields += (
                "admitted_item_count",
                "metadata_excluded_item_count",
            )
        return cls(
            schema_version=schema_version,
            status=require_safe_identifier(document["status"], "status"),
            source_partitions=tuple(
                require_safe_identifier(item, "source_partitions[]")
                for item in document["source_partitions"]
            ),
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
    "AIHUB538_MULTI_DECISION_CALIBRATION_SCHEMA_VERSION",
    "AIHUB538_MULTI_DECISION_CALIBRATION_FAILURE_SCHEMA_VERSION",
    "AIHUB538_MULTI_DECISION_CALIBRATION_FAILURE_V2_SCHEMA_VERSION",
    "AIHUB538_QC_CANDIDATE_UNAVAILABLE_REASON_CODES",
    "AIHUB538_QC_DECISION_FAILURE_REASON_CODES",
    "AIHUB538_QC_FAILURE_DIAGNOSTIC_PROTOCOL_SHA256",
    "AIHUB538_QC_THRESHOLD_DIAGNOSTIC_SPECS",
    "Aihub538AggregateReasonCount",
    "Aihub538AngleQcFailureDiagnostic",
    "Aihub538CalibrationAngleEvidence",
    "Aihub538DecisionCalibrationEvidence",
    "Aihub538DecisionCalibrationFailureArtifact",
    "Aihub538DecisionCalibrationFailureArtifactV2",
    "Aihub538DecisionCalibrationProtocol",
    "Aihub538DecisionCalibrationReloadReceipt",
    "Aihub538QcOptionalMetricMissingCombinations",
    "Aihub538QcThresholdMarginHistogram",
    "DEFAULT_AIHUB538_DECISION_CALIBRATION_PROTOCOL",
    "MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL",
]
