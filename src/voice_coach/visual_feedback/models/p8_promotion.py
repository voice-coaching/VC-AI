"""Deterministic P8 zero-violation replay receipt contract."""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import VisualFeedbackContractError
from .integrity import (
    require_exact_keys,
    require_integer,
    require_mapping,
    require_non_empty_string,
    require_safe_identifier,
    require_sha256,
    sha256_document,
)
from .p8_evaluation_completeness import (
    require_p8_evaluation_exclusion_policy,
    validate_p8_evaluation_completeness,
)
from .p8_locked_evaluation import P8_LOCKED_SOURCE_LISTING_SHA256

P8_ZERO_VIOLATION_REPLAY_RECEIPT_SCHEMA_VERSION = (
    "voice_coach.p8_zero_violation_replay_receipt.v3"
)
P8_AUTOMATED_PROMOTION_BUNDLE_SCHEMA_VERSION = (
    "voice_coach.p8_automated_promotion_bundle.v4"
)


@dataclass(frozen=True, slots=True)
class P8ZeroViolationReplayReceipt:
    """Bind a passing locked report to its exact zero-violation metrics."""

    protocol_revision: str
    read_zero_seal_sha256: str
    locked_evaluation_report_sha256: str
    locked_manifest_sha256: str
    aggregate_metrics_sha256: str
    p6_promotion_receipt_sha256: str
    calibration_artifact_sha256: str
    catalog_sha256: str
    policy_sha256: str
    pipeline_identity_sha256: str
    admitted_item_count: int
    paired_item_count: int
    evaluated_item_count: int
    evaluation_exclusion_count: int
    evaluation_exclusion_policy: str
    evaluated_case_count: int
    ready_case_count: int
    claim_policy_violation_count: int
    forbidden_claim_fixture_violation_count: int
    status: str
    reason_codes: tuple[str, ...]
    receipt_sha256: str
    schema_version: str = P8_ZERO_VIOLATION_REPLAY_RECEIPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P8_ZERO_VIOLATION_REPLAY_RECEIPT_SCHEMA_VERSION:
            raise VisualFeedbackContractError(
                "P8 zero-violation replay receipt schema differs"
            )
        require_safe_identifier(self.protocol_revision, "protocol_revision")
        for name in (
            "read_zero_seal_sha256",
            "locked_evaluation_report_sha256",
            "locked_manifest_sha256",
            "aggregate_metrics_sha256",
            "p6_promotion_receipt_sha256",
            "calibration_artifact_sha256",
            "catalog_sha256",
            "policy_sha256",
            "pipeline_identity_sha256",
            "receipt_sha256",
        ):
            require_sha256(getattr(self, name), name)
        if self.locked_manifest_sha256 == P8_LOCKED_SOURCE_LISTING_SHA256:
            raise VisualFeedbackContractError(
                "P8 replay must bind the actual locked source manifest"
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
        require_p8_evaluation_exclusion_policy(
            self.evaluation_exclusion_policy
        )
        if exclusions != 0 or admitted != paired or paired != evaluated:
            raise VisualFeedbackContractError(
                "P8 replay cannot promote an incomplete evaluation"
            )
        evaluated_case_count = require_integer(
            self.evaluated_case_count,
            "evaluated_case_count",
            minimum=1,
        )
        if evaluated_case_count != evaluated:
            raise VisualFeedbackContractError(
                "P8 replay evaluated count differs"
            )
        ready_count = require_integer(
            self.ready_case_count, "ready_case_count", minimum=1
        )
        if ready_count > evaluated:
            raise VisualFeedbackContractError(
                "P8 ready replay count exceeds evaluated count"
            )
        if require_integer(
            self.claim_policy_violation_count,
            "claim_policy_violation_count",
            minimum=0,
        ) != 0:
            raise VisualFeedbackContractError(
                "P8 claim-policy replay contains a violation"
            )
        if require_integer(
            self.forbidden_claim_fixture_violation_count,
            "forbidden_claim_fixture_violation_count",
            minimum=0,
        ) != 0:
            raise VisualFeedbackContractError(
                "P8 forbidden-claim replay contains a violation"
            )
        if self.status != "locked_replay_zero_violations":
            raise VisualFeedbackContractError(
                "P8 zero-violation replay status differs"
            )
        if self.reason_codes:
            raise VisualFeedbackContractError(
                "passing P8 replay receipt cannot contain reasons"
            )
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError(
                "P8 zero-violation replay receipt digest differs"
            )

    @classmethod
    def _issue_from_validated_locked_evaluation(
        cls,
        *,
        protocol_revision: str,
        read_zero_seal_sha256: str,
        locked_evaluation_report_sha256: str,
        locked_manifest_sha256: str,
        aggregate_metrics_sha256: str,
        p6_promotion_receipt_sha256: str,
        calibration_artifact_sha256: str,
        catalog_sha256: str,
        policy_sha256: str,
        pipeline_identity_sha256: str,
        admitted_item_count: int,
        paired_item_count: int,
        evaluated_item_count: int,
        evaluation_exclusion_count: int,
        evaluation_exclusion_policy: str,
        evaluated_case_count: int,
        ready_case_count: int,
    ) -> P8ZeroViolationReplayReceipt:
        """Issue only after the persisted locked-evaluation validator passes."""

        unsigned = {
            "schema_version": P8_ZERO_VIOLATION_REPLAY_RECEIPT_SCHEMA_VERSION,
            "protocol_revision": protocol_revision,
            "read_zero_seal_sha256": read_zero_seal_sha256,
            "locked_evaluation_report_sha256": locked_evaluation_report_sha256,
            "locked_manifest_sha256": locked_manifest_sha256,
            "aggregate_metrics_sha256": aggregate_metrics_sha256,
            "p6_promotion_receipt_sha256": p6_promotion_receipt_sha256,
            "calibration_artifact_sha256": calibration_artifact_sha256,
            "catalog_sha256": catalog_sha256,
            "policy_sha256": policy_sha256,
            "pipeline_identity_sha256": pipeline_identity_sha256,
            "admitted_item_count": admitted_item_count,
            "paired_item_count": paired_item_count,
            "evaluated_item_count": evaluated_item_count,
            "evaluation_exclusion_count": evaluation_exclusion_count,
            "evaluation_exclusion_policy": evaluation_exclusion_policy,
            "evaluated_case_count": evaluated_case_count,
            "ready_case_count": ready_case_count,
            "claim_policy_violation_count": 0,
            "forbidden_claim_fixture_violation_count": 0,
            "status": "locked_replay_zero_violations",
            "reason_codes": [],
        }
        return cls(
            protocol_revision=protocol_revision,
            read_zero_seal_sha256=read_zero_seal_sha256,
            locked_evaluation_report_sha256=locked_evaluation_report_sha256,
            locked_manifest_sha256=locked_manifest_sha256,
            aggregate_metrics_sha256=aggregate_metrics_sha256,
            p6_promotion_receipt_sha256=p6_promotion_receipt_sha256,
            calibration_artifact_sha256=calibration_artifact_sha256,
            catalog_sha256=catalog_sha256,
            policy_sha256=policy_sha256,
            pipeline_identity_sha256=pipeline_identity_sha256,
            admitted_item_count=admitted_item_count,
            paired_item_count=paired_item_count,
            evaluated_item_count=evaluated_item_count,
            evaluation_exclusion_count=evaluation_exclusion_count,
            evaluation_exclusion_policy=evaluation_exclusion_policy,
            evaluated_case_count=evaluated_case_count,
            ready_case_count=ready_case_count,
            claim_policy_violation_count=0,
            forbidden_claim_fixture_violation_count=0,
            status="locked_replay_zero_violations",
            reason_codes=(),
            receipt_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "protocol_revision": self.protocol_revision,
            "read_zero_seal_sha256": self.read_zero_seal_sha256,
            "locked_evaluation_report_sha256": (
                self.locked_evaluation_report_sha256
            ),
            "locked_manifest_sha256": self.locked_manifest_sha256,
            "aggregate_metrics_sha256": self.aggregate_metrics_sha256,
            "p6_promotion_receipt_sha256": self.p6_promotion_receipt_sha256,
            "calibration_artifact_sha256": self.calibration_artifact_sha256,
            "catalog_sha256": self.catalog_sha256,
            "policy_sha256": self.policy_sha256,
            "pipeline_identity_sha256": self.pipeline_identity_sha256,
            "admitted_item_count": self.admitted_item_count,
            "paired_item_count": self.paired_item_count,
            "evaluated_item_count": self.evaluated_item_count,
            "evaluation_exclusion_count": self.evaluation_exclusion_count,
            "evaluation_exclusion_policy": self.evaluation_exclusion_policy,
            "evaluated_case_count": self.evaluated_case_count,
            "ready_case_count": self.ready_case_count,
            "claim_policy_violation_count": self.claim_policy_violation_count,
            "forbidden_claim_fixture_violation_count": (
                self.forbidden_claim_fixture_violation_count
            ),
            "status": self.status,
            "reason_codes": list(self.reason_codes),
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> P8ZeroViolationReplayReceipt:
        document = require_mapping(value, "p8_zero_violation_replay_receipt")
        require_exact_keys(
            document,
            {
                "schema_version",
                "protocol_revision",
                "read_zero_seal_sha256",
                "locked_evaluation_report_sha256",
                "locked_manifest_sha256",
                "aggregate_metrics_sha256",
                "p6_promotion_receipt_sha256",
                "calibration_artifact_sha256",
                "catalog_sha256",
                "policy_sha256",
                "pipeline_identity_sha256",
                "admitted_item_count",
                "paired_item_count",
                "evaluated_item_count",
                "evaluation_exclusion_count",
                "evaluation_exclusion_policy",
                "evaluated_case_count",
                "ready_case_count",
                "claim_policy_violation_count",
                "forbidden_claim_fixture_violation_count",
                "status",
                "reason_codes",
                "receipt_sha256",
            },
            "p8_zero_violation_replay_receipt",
        )
        reasons = document["reason_codes"]
        if not isinstance(reasons, list):
            raise VisualFeedbackContractError("P8 replay reason_codes must be an array")
        return cls(
            schema_version=require_non_empty_string(
                document["schema_version"],
                "p8_zero_violation_replay_receipt.schema_version",
            ),
            protocol_revision=require_safe_identifier(
                document["protocol_revision"],
                "p8_zero_violation_replay_receipt.protocol_revision",
            ),
            read_zero_seal_sha256=require_sha256(
                document["read_zero_seal_sha256"],
                "p8_zero_violation_replay_receipt.read_zero_seal_sha256",
            ),
            locked_evaluation_report_sha256=require_sha256(
                document["locked_evaluation_report_sha256"],
                "p8_zero_violation_replay_receipt.locked_evaluation_report_sha256",
            ),
            locked_manifest_sha256=require_sha256(
                document["locked_manifest_sha256"],
                "p8_zero_violation_replay_receipt.locked_manifest_sha256",
            ),
            aggregate_metrics_sha256=require_sha256(
                document["aggregate_metrics_sha256"],
                "p8_zero_violation_replay_receipt.aggregate_metrics_sha256",
            ),
            p6_promotion_receipt_sha256=require_sha256(
                document["p6_promotion_receipt_sha256"],
                "p8_zero_violation_replay_receipt.p6_promotion_receipt_sha256",
            ),
            calibration_artifact_sha256=require_sha256(
                document["calibration_artifact_sha256"],
                "p8_zero_violation_replay_receipt.calibration_artifact_sha256",
            ),
            catalog_sha256=require_sha256(
                document["catalog_sha256"],
                "p8_zero_violation_replay_receipt.catalog_sha256",
            ),
            policy_sha256=require_sha256(
                document["policy_sha256"],
                "p8_zero_violation_replay_receipt.policy_sha256",
            ),
            pipeline_identity_sha256=require_sha256(
                document["pipeline_identity_sha256"],
                "p8_zero_violation_replay_receipt.pipeline_identity_sha256",
            ),
            admitted_item_count=require_integer(
                document["admitted_item_count"],
                "p8_zero_violation_replay_receipt.admitted_item_count",
                minimum=1,
            ),
            paired_item_count=require_integer(
                document["paired_item_count"],
                "p8_zero_violation_replay_receipt.paired_item_count",
                minimum=0,
            ),
            evaluated_item_count=require_integer(
                document["evaluated_item_count"],
                "p8_zero_violation_replay_receipt.evaluated_item_count",
                minimum=0,
            ),
            evaluation_exclusion_count=require_integer(
                document["evaluation_exclusion_count"],
                "p8_zero_violation_replay_receipt.evaluation_exclusion_count",
                minimum=0,
            ),
            evaluation_exclusion_policy=(
                require_p8_evaluation_exclusion_policy(
                    document["evaluation_exclusion_policy"]
                )
            ),
            evaluated_case_count=require_integer(
                document["evaluated_case_count"],
                "p8_zero_violation_replay_receipt.evaluated_case_count",
                minimum=1,
            ),
            ready_case_count=require_integer(
                document["ready_case_count"],
                "p8_zero_violation_replay_receipt.ready_case_count",
                minimum=1,
            ),
            claim_policy_violation_count=require_integer(
                document["claim_policy_violation_count"],
                "p8_zero_violation_replay_receipt.claim_policy_violation_count",
                minimum=0,
            ),
            forbidden_claim_fixture_violation_count=require_integer(
                document["forbidden_claim_fixture_violation_count"],
                "p8_zero_violation_replay_receipt.forbidden_claim_fixture_violation_count",
                minimum=0,
            ),
            status=require_safe_identifier(
                document["status"], "p8_zero_violation_replay_receipt.status"
            ),
            reason_codes=tuple(
                require_safe_identifier(
                    reason, "p8_zero_violation_replay_receipt.reason_codes[]"
                )
                for reason in reasons
            ),
            receipt_sha256=require_sha256(
                document["receipt_sha256"],
                "p8_zero_violation_replay_receipt.receipt_sha256",
            ),
        )


__all__ = [
    "P8_AUTOMATED_PROMOTION_BUNDLE_SCHEMA_VERSION",
    "P8_ZERO_VIOLATION_REPLAY_RECEIPT_SCHEMA_VERSION",
    "P8ZeroViolationReplayReceipt",
]
