"""Privacy-safe aggregate metric document for fresh P8 threshold replay."""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import VisualFeedbackContractError
from .integrity import (
    require_exact_keys,
    require_finite_number,
    require_integer,
    require_mapping,
    require_safe_identifier,
    require_sha256,
    sha256_document,
)
from .p8_locked_evaluation import (
    P8LockedEvaluationThresholds,
    P8_LOCKED_EVALUATION_THRESHOLDS_SHA256,
    P8_LOCKED_SOURCE_LISTING_SHA256,
    P8_METRIC_SEMANTICS_VERSION,
)
from .p8_evaluation_completeness import (
    P8_EVALUATION_EXCLUSION_POLICY,
    require_p8_evaluation_exclusion_policy,
    validate_p8_evaluation_completeness,
)

P8_AGGREGATE_EVALUATION_METRICS_SCHEMA_VERSION = (
    "voice_coach.p8_aggregate_evaluation_metrics.v4"
)


@dataclass(frozen=True, slots=True)
class P8AggregateEvaluationMetrics:
    """Self-digested aggregates returned after the locked source-open boundary.

    ``qc_decision_lineage_valid_item_count`` counts only items whose raw QC
    candidate receipt was consumed by the frozen QC decision path to issue a
    typed pass/fail decision. ``ready_sync_decision_lineage_valid_case_count``
    counts only ready cases whose raw sync-anchor candidates were consumed by
    the frozen sync decision path to issue a typed ``synchronized`` decision.
    Raw candidates alone never contribute to successful or ready aggregates.
    """

    semantics_version: str
    evaluation_thresholds_sha256: str
    locked_source_manifest_sha256: str
    admitted_item_count: int
    paired_item_count: int
    evaluated_item_count: int
    evaluation_exclusion_count: int
    evaluation_exclusion_policy: str
    successful_item_count: int
    failed_item_count: int
    qc_decision_lineage_valid_item_count: int
    decode_success_rate: float
    supported_clip_count: int
    supported_clip_below_single_face_threshold_count: int
    aggregate_supported_slice_coverage: float
    av_anchor_availability_rate: float
    ready_case_count: int
    ready_sync_decision_lineage_valid_case_count: int
    maximum_ready_absolute_sync_offset_ms: float
    claim_policy_violation_count: int
    forbidden_claim_fixture_violation_count: int
    metrics_sha256: str
    schema_version: str = P8_AGGREGATE_EVALUATION_METRICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P8_AGGREGATE_EVALUATION_METRICS_SCHEMA_VERSION:
            raise VisualFeedbackContractError("P8 aggregate metric schema differs")
        if self.semantics_version != P8_METRIC_SEMANTICS_VERSION:
            raise VisualFeedbackContractError("P8 aggregate metric semantics differ")
        if (
            self.evaluation_thresholds_sha256
            != P8_LOCKED_EVALUATION_THRESHOLDS_SHA256
        ):
            raise VisualFeedbackContractError("P8 aggregate threshold pin differs")
        require_sha256(
            self.locked_source_manifest_sha256,
            "locked_source_manifest_sha256",
        )
        if self.locked_source_manifest_sha256 == P8_LOCKED_SOURCE_LISTING_SHA256:
            raise VisualFeedbackContractError(
                "P8 actual manifest cannot be the public source listing"
            )
        _, _, evaluated, _ = validate_p8_evaluation_completeness(
            admitted_item_count=self.admitted_item_count,
            paired_item_count=self.paired_item_count,
            evaluated_item_count=self.evaluated_item_count,
            evaluation_exclusion_count=self.evaluation_exclusion_count,
            allow_empty=False,
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
        qc_decision_lineage_valid = require_integer(
            self.qc_decision_lineage_valid_item_count,
            "qc_decision_lineage_valid_item_count",
            minimum=0,
        )
        if qc_decision_lineage_valid != evaluated:
            raise VisualFeedbackContractError(
                "P8 QC decision lineage coverage is incomplete"
            )
        supported = require_integer(
            self.supported_clip_count, "supported_clip_count", minimum=1
        )
        below_single_face = require_integer(
            self.supported_clip_below_single_face_threshold_count,
            "supported_clip_below_single_face_threshold_count",
            minimum=0,
        )
        if below_single_face > supported:
            raise VisualFeedbackContractError(
                "P8 supported single-face failure count exceeds denominator"
            )
        ready = require_integer(
            self.ready_case_count,
            "ready_case_count",
            minimum=1,
        )
        if ready > evaluated:
            raise VisualFeedbackContractError(
                "P8 ready case count exceeds evaluated count"
            )
        ready_sync_decision_lineage_valid = require_integer(
            self.ready_sync_decision_lineage_valid_case_count,
            "ready_sync_decision_lineage_valid_case_count",
            minimum=0,
        )
        if ready_sync_decision_lineage_valid != ready:
            raise VisualFeedbackContractError(
                "P8 ready sync decision lineage coverage is incomplete"
            )
        require_integer(
            self.claim_policy_violation_count,
            "claim_policy_violation_count",
            minimum=0,
        )
        require_integer(
            self.forbidden_claim_fixture_violation_count,
            "forbidden_claim_fixture_violation_count",
            minimum=0,
        )
        for field_name in (
            "decode_success_rate",
            "aggregate_supported_slice_coverage",
            "av_anchor_availability_rate",
        ):
            value = require_finite_number(getattr(self, field_name), field_name)
            if value < 0.0 or value > 1.0:
                raise VisualFeedbackContractError(
                    f"{field_name} must be between 0 and 1"
                )
        sync_offset = require_finite_number(
            self.maximum_ready_absolute_sync_offset_ms,
            "maximum_ready_absolute_sync_offset_ms",
        )
        if sync_offset < 0.0:
            raise VisualFeedbackContractError(
                "maximum_ready_absolute_sync_offset_ms must be non-negative"
            )
        require_sha256(self.metrics_sha256, "metrics_sha256")
        if self.metrics_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("P8 aggregate metrics digest differs")

    @classmethod
    def issue(
        cls,
        *,
        locked_source_manifest_sha256: str,
        admitted_item_count: int,
        paired_item_count: int,
        evaluated_item_count: int,
        evaluation_exclusion_count: int,
        successful_item_count: int,
        failed_item_count: int,
        qc_decision_lineage_valid_item_count: int,
        decode_success_rate: float,
        supported_clip_count: int,
        supported_clip_below_single_face_threshold_count: int,
        aggregate_supported_slice_coverage: float,
        av_anchor_availability_rate: float,
        ready_case_count: int,
        ready_sync_decision_lineage_valid_case_count: int,
        maximum_ready_absolute_sync_offset_ms: float,
        claim_policy_violation_count: int,
        forbidden_claim_fixture_violation_count: int,
    ) -> P8AggregateEvaluationMetrics:
        unsigned = {
            "schema_version": P8_AGGREGATE_EVALUATION_METRICS_SCHEMA_VERSION,
            "semantics_version": P8_METRIC_SEMANTICS_VERSION,
            "evaluation_thresholds_sha256": (
                P8_LOCKED_EVALUATION_THRESHOLDS_SHA256
            ),
            "locked_source_manifest_sha256": locked_source_manifest_sha256,
            "admitted_item_count": admitted_item_count,
            "paired_item_count": paired_item_count,
            "evaluated_item_count": evaluated_item_count,
            "evaluation_exclusion_count": evaluation_exclusion_count,
            "evaluation_exclusion_policy": P8_EVALUATION_EXCLUSION_POLICY,
            "successful_item_count": successful_item_count,
            "failed_item_count": failed_item_count,
            "qc_decision_lineage_valid_item_count": (
                qc_decision_lineage_valid_item_count
            ),
            "decode_success_rate": decode_success_rate,
            "supported_clip_count": supported_clip_count,
            "supported_clip_below_single_face_threshold_count": (
                supported_clip_below_single_face_threshold_count
            ),
            "aggregate_supported_slice_coverage": (
                aggregate_supported_slice_coverage
            ),
            "av_anchor_availability_rate": av_anchor_availability_rate,
            "ready_case_count": ready_case_count,
            "ready_sync_decision_lineage_valid_case_count": (
                ready_sync_decision_lineage_valid_case_count
            ),
            "maximum_ready_absolute_sync_offset_ms": (
                maximum_ready_absolute_sync_offset_ms
            ),
            "claim_policy_violation_count": claim_policy_violation_count,
            "forbidden_claim_fixture_violation_count": (
                forbidden_claim_fixture_violation_count
            ),
        }
        return cls(**unsigned, metrics_sha256=sha256_document(unsigned))

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "semantics_version": self.semantics_version,
            "evaluation_thresholds_sha256": self.evaluation_thresholds_sha256,
            "locked_source_manifest_sha256": self.locked_source_manifest_sha256,
            "admitted_item_count": self.admitted_item_count,
            "paired_item_count": self.paired_item_count,
            "evaluated_item_count": self.evaluated_item_count,
            "evaluation_exclusion_count": self.evaluation_exclusion_count,
            "evaluation_exclusion_policy": self.evaluation_exclusion_policy,
            "successful_item_count": self.successful_item_count,
            "failed_item_count": self.failed_item_count,
            "qc_decision_lineage_valid_item_count": (
                self.qc_decision_lineage_valid_item_count
            ),
            "decode_success_rate": self.decode_success_rate,
            "supported_clip_count": self.supported_clip_count,
            "supported_clip_below_single_face_threshold_count": (
                self.supported_clip_below_single_face_threshold_count
            ),
            "aggregate_supported_slice_coverage": (
                self.aggregate_supported_slice_coverage
            ),
            "av_anchor_availability_rate": self.av_anchor_availability_rate,
            "ready_case_count": self.ready_case_count,
            "ready_sync_decision_lineage_valid_case_count": (
                self.ready_sync_decision_lineage_valid_case_count
            ),
            "maximum_ready_absolute_sync_offset_ms": (
                self.maximum_ready_absolute_sync_offset_ms
            ),
            "claim_policy_violation_count": self.claim_policy_violation_count,
            "forbidden_claim_fixture_violation_count": (
                self.forbidden_claim_fixture_violation_count
            ),
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "metrics_sha256": self.metrics_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> P8AggregateEvaluationMetrics:
        document = require_mapping(value, "p8_aggregate_evaluation_metrics")
        require_exact_keys(
            document,
            {
                "schema_version",
                "semantics_version",
                "evaluation_thresholds_sha256",
                "locked_source_manifest_sha256",
                "admitted_item_count",
                "paired_item_count",
                "evaluated_item_count",
                "evaluation_exclusion_count",
                "evaluation_exclusion_policy",
                "successful_item_count",
                "failed_item_count",
                "qc_decision_lineage_valid_item_count",
                "decode_success_rate",
                "supported_clip_count",
                "supported_clip_below_single_face_threshold_count",
                "aggregate_supported_slice_coverage",
                "av_anchor_availability_rate",
                "ready_case_count",
                "ready_sync_decision_lineage_valid_case_count",
                "maximum_ready_absolute_sync_offset_ms",
                "claim_policy_violation_count",
                "forbidden_claim_fixture_violation_count",
                "metrics_sha256",
            },
            "p8_aggregate_evaluation_metrics",
        )
        return cls(
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
            semantics_version=require_safe_identifier(
                document["semantics_version"], "semantics_version"
            ),
            evaluation_thresholds_sha256=require_sha256(
                document["evaluation_thresholds_sha256"],
                "evaluation_thresholds_sha256",
            ),
            locked_source_manifest_sha256=require_sha256(
                document["locked_source_manifest_sha256"],
                "locked_source_manifest_sha256",
            ),
            admitted_item_count=require_integer(
                document["admitted_item_count"],
                "admitted_item_count",
                minimum=1,
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
            qc_decision_lineage_valid_item_count=require_integer(
                document["qc_decision_lineage_valid_item_count"],
                "qc_decision_lineage_valid_item_count",
                minimum=0,
            ),
            decode_success_rate=require_finite_number(
                document["decode_success_rate"], "decode_success_rate"
            ),
            supported_clip_count=require_integer(
                document["supported_clip_count"], "supported_clip_count", minimum=1
            ),
            supported_clip_below_single_face_threshold_count=require_integer(
                document["supported_clip_below_single_face_threshold_count"],
                "supported_clip_below_single_face_threshold_count",
                minimum=0,
            ),
            aggregate_supported_slice_coverage=require_finite_number(
                document["aggregate_supported_slice_coverage"],
                "aggregate_supported_slice_coverage",
            ),
            av_anchor_availability_rate=require_finite_number(
                document["av_anchor_availability_rate"],
                "av_anchor_availability_rate",
            ),
            ready_case_count=require_integer(
                document["ready_case_count"], "ready_case_count", minimum=1
            ),
            ready_sync_decision_lineage_valid_case_count=require_integer(
                document["ready_sync_decision_lineage_valid_case_count"],
                "ready_sync_decision_lineage_valid_case_count",
                minimum=0,
            ),
            maximum_ready_absolute_sync_offset_ms=require_finite_number(
                document["maximum_ready_absolute_sync_offset_ms"],
                "maximum_ready_absolute_sync_offset_ms",
            ),
            claim_policy_violation_count=require_integer(
                document["claim_policy_violation_count"],
                "claim_policy_violation_count",
                minimum=0,
            ),
            forbidden_claim_fixture_violation_count=require_integer(
                document["forbidden_claim_fixture_violation_count"],
                "forbidden_claim_fixture_violation_count",
                minimum=0,
            ),
            metrics_sha256=require_sha256(
                document["metrics_sha256"], "metrics_sha256"
            ),
        )

    def threshold_failure_reason_codes(
        self, thresholds: P8LockedEvaluationThresholds
    ) -> tuple[str, ...]:
        """Return deterministic failures against the exact pre-access mapping."""

        reasons: list[str] = []
        if self.evaluation_exclusion_count != 0:
            reasons.append("p8_evaluation_exclusion_detected")
        if self.decode_success_rate < thresholds.decode_success_minimum:
            reasons.append("p8_decode_success_below_threshold")
        if self.supported_clip_below_single_face_threshold_count != 0:
            reasons.append("p8_supported_clip_single_face_coverage_below_threshold")
        if (
            self.aggregate_supported_slice_coverage
            < thresholds.aggregate_supported_slice_coverage_minimum
        ):
            reasons.append("p8_aggregate_supported_slice_coverage_below_threshold")
        if (
            self.av_anchor_availability_rate
            < thresholds.av_anchor_availability_minimum
        ):
            reasons.append("p8_av_anchor_availability_below_threshold")
        if (
            self.maximum_ready_absolute_sync_offset_ms
            > thresholds.ready_absolute_sync_offset_maximum_ms
        ):
            reasons.append("p8_ready_sync_offset_above_threshold")
        if (
            self.claim_policy_violation_count
            > thresholds.claim_policy_violation_maximum
        ):
            reasons.append("p8_claim_policy_violation_detected")
        if (
            self.forbidden_claim_fixture_violation_count
            > thresholds.forbidden_claim_fixture_violation_maximum
        ):
            reasons.append("p8_forbidden_claim_fixture_violation_detected")
        return tuple(reasons)


__all__ = [
    "P8_AGGREGATE_EVALUATION_METRICS_SCHEMA_VERSION",
    "P8AggregateEvaluationMetrics",
]
