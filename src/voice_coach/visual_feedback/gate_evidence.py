"""Fail-closed validation for privacy-safe P6 and P8 evidence summaries.

This module deliberately does not read dataset media, paths, actor identifiers, or
landmark sequences.  It validates frozen summaries and their exact digests.  A
successful receipt is evidence for the named validation boundary only; neither
receipt authorizes runtime visual feedback or promotes reliability.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .errors import VisualFeedbackContractError
from .models.artifacts import VisualCalibrationArtifact, VisualClaimCatalog
from .models.enums import (
    CalibrationScope,
    DeviationDirection,
    VisualCueState,
    VisualEvidenceRelation,
    VisualFeedbackStatus,
)
from .models.integrity import (
    require_boolean,
    require_exact_keys,
    require_finite_number,
    require_integer,
    require_mapping,
    require_non_empty_string,
    require_safe_identifier,
    require_sha256,
    require_text_token,
    sha256_document,
)
from .models.supplement import VisualFeedbackSupplement

P6_EVIDENCE_BUNDLE_SCHEMA_VERSION = "voice_coach.p6_evidence_bundle.v1"
P6_EVIDENCE_RECEIPT_SCHEMA_VERSION = "voice_coach.p6_evidence_receipt.v1"
P8_CONFORMANCE_BUNDLE_SCHEMA_VERSION = "voice_coach.p8_conformance_bundle.v1"
P8_CONFORMANCE_RECEIPT_SCHEMA_VERSION = "voice_coach.p8_conformance_receipt.v1"

_RIGHTS_SCHEMA_VERSION = "voice_coach.visual_dataset_rights_receipt.v1"
_SPLIT_SCHEMA_VERSION = "voice_coach.visual_dataset_split_receipt.v1"
_METRIC_SCHEMA_VERSION = "voice_coach.visual_calibration_metric_summary.v1"
_RELOAD_SCHEMA_VERSION = "voice_coach.visual_calibration_reload_receipt.v1"
_POLICY_SCHEMA_VERSION = "voice_coach.visual_claim_policy.v1"
_REPLAY_CASE_SCHEMA_VERSION = "voice_coach.visual_claim_replay_case.v1"
_FORBIDDEN_FIXTURE_SCHEMA_VERSION = "voice_coach.visual_forbidden_claim_fixture.v1"

P6_RIGHTS_RECEIPT_SCHEMA_VERSION = _RIGHTS_SCHEMA_VERSION
P6_SPLIT_RECEIPT_SCHEMA_VERSION = _SPLIT_SCHEMA_VERSION
P6_METRIC_SUMMARY_SCHEMA_VERSION = _METRIC_SCHEMA_VERSION
P6_RELOAD_RECEIPT_SCHEMA_VERSION = _RELOAD_SCHEMA_VERSION
P8_CLAIM_POLICY_SCHEMA_VERSION = _POLICY_SCHEMA_VERSION
P8_REPLAY_CASE_SCHEMA_VERSION = _REPLAY_CASE_SCHEMA_VERSION
P8_FORBIDDEN_FIXTURE_SCHEMA_VERSION = _FORBIDDEN_FIXTURE_SCHEMA_VERSION

_P6_SOURCE_DATASET_ID = "aihub_68"
_P8_SOURCE_DATASET_ID = "aihub_538"
_VISIBLE_CUE_ALLOWLIST = frozenset(
    {
        "lip_aperture_ratio",
        "lip_closure_duration_ratio",
        "lip_rounding_ratio",
        "lip_timing_offset_ms",
        "lip_width_ratio",
    }
)
_REQUIRED_FORBIDDEN_IDENTIFIER_TOKENS = frozenset(
    {
        "airflow",
        "diagnosis",
        "glottis",
        "medical",
        "tongue",
        "velum",
    }
)
_REQUIRED_REPLAY_SCENARIOS = frozenset(
    {
        "conflict_suppressed",
        "low_confidence_suppressed",
        "missing_suppressed",
        "out_of_distribution_suppressed",
        "ready_claim",
        "unsupported_slice_suppressed",
    }
)


def visual_gate_schema_summary(gate: str) -> dict[str, object]:
    """Return schema identifiers and field names without example evidence."""

    if gate == "p6":
        return {
            "gate": "p6",
            "bundle_schema_version": P6_EVIDENCE_BUNDLE_SCHEMA_VERSION,
            "receipt_schema_version": P6_EVIDENCE_RECEIPT_SCHEMA_VERSION,
            "source_dataset_id": _P6_SOURCE_DATASET_ID,
            "required_top_level_fields": [
                "schema_version",
                "source_dataset_id",
                "source_revision",
                "dataset_manifest_sha256",
                "capture_contract_id",
                "population_id_sha256",
                "rights_receipt",
                "split_receipt",
                "pipeline_identity",
                "metric_report",
                "calibration_artifact",
                "reload_receipt",
                "bundle_sha256",
            ],
            "nested_schema_versions": {
                "rights_receipt": P6_RIGHTS_RECEIPT_SCHEMA_VERSION,
                "split_receipt": P6_SPLIT_RECEIPT_SCHEMA_VERSION,
                "metric_report": P6_METRIC_SUMMARY_SCHEMA_VERSION,
                "reload_receipt": P6_RELOAD_RECEIPT_SCHEMA_VERSION,
            },
            "successful_receipt_status": "evidence_validated_not_promoted",
            "promotion_authorized": False,
            "privacy_contract": "aggregate_summary_only_no_paths_or_actor_ids",
        }
    if gate == "p8":
        return {
            "gate": "p8",
            "bundle_schema_version": P8_CONFORMANCE_BUNDLE_SCHEMA_VERSION,
            "receipt_schema_version": P8_CONFORMANCE_RECEIPT_SCHEMA_VERSION,
            "locked_source_dataset_id": _P8_SOURCE_DATASET_ID,
            "required_top_level_fields": [
                "schema_version",
                "protocol_revision",
                "locked_source_dataset_id",
                "locked_source_revision",
                "locked_manifest_sha256",
                "read_zero_seal_sha256",
                "p6_evidence_receipt",
                "calibration_artifact_sha256",
                "claim_catalog",
                "claim_policy",
                "forbidden_claim_fixtures",
                "replay_cases",
                "bundle_sha256",
            ],
            "nested_schema_versions": {
                "claim_policy": P8_CLAIM_POLICY_SCHEMA_VERSION,
                "replay_case": P8_REPLAY_CASE_SCHEMA_VERSION,
                "forbidden_claim_fixture": P8_FORBIDDEN_FIXTURE_SCHEMA_VERSION,
            },
            "required_replay_scenarios": sorted(_REQUIRED_REPLAY_SCENARIOS),
            "read_zero_seal_status": "digest_assertion_only",
            "successful_receipt_status": "zero_violations_not_promoted",
            "promotion_authorized": False,
            "privacy_contract": "serialized_visual_supplements_and_digests_only",
        }
    raise VisualFeedbackContractError("visual gate schema name is unknown")


@dataclass(frozen=True, slots=True)
class P6EvidenceValidationReceipt:
    """Digest-pinned P6 summary validation, never a reliability decision."""

    source_dataset_id: str
    source_revision: str
    dataset_manifest_sha256: str
    rights_receipt_sha256: str
    split_receipt_sha256: str
    metric_report_sha256: str
    calibration_artifact_sha256: str
    reload_receipt_sha256: str
    bundle_sha256: str
    status: str
    visual_reliability_authorized: bool
    reason_codes: tuple[str, ...]
    receipt_sha256: str
    schema_version: str = P6_EVIDENCE_RECEIPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P6_EVIDENCE_RECEIPT_SCHEMA_VERSION:
            raise VisualFeedbackContractError("P6 receipt schema differs")
        if self.source_dataset_id != _P6_SOURCE_DATASET_ID:
            raise VisualFeedbackContractError("P6 source dataset differs")
        require_safe_identifier(self.source_revision, "source_revision")
        for name in (
            "dataset_manifest_sha256",
            "rights_receipt_sha256",
            "split_receipt_sha256",
            "metric_report_sha256",
            "calibration_artifact_sha256",
            "reload_receipt_sha256",
            "bundle_sha256",
            "receipt_sha256",
        ):
            require_sha256(getattr(self, name), name)
        if self.status != "evidence_validated_not_promoted":
            raise VisualFeedbackContractError("P6 receipt status differs")
        if self.visual_reliability_authorized is not False:
            raise VisualFeedbackContractError(
                "P6 evidence receipt cannot authorize visual reliability"
            )
        if self.reason_codes != (
            "p8_independent_evaluation_required",
            "p8_claim_conformance_required",
        ):
            raise VisualFeedbackContractError("P6 receipt reason codes differ")
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("P6 receipt digest differs")

    @classmethod
    def issue(
        cls,
        *,
        source_revision: str,
        dataset_manifest_sha256: str,
        rights_receipt_sha256: str,
        split_receipt_sha256: str,
        metric_report_sha256: str,
        calibration_artifact_sha256: str,
        reload_receipt_sha256: str,
        bundle_sha256: str,
    ) -> P6EvidenceValidationReceipt:
        unsigned = {
            "schema_version": P6_EVIDENCE_RECEIPT_SCHEMA_VERSION,
            "source_dataset_id": _P6_SOURCE_DATASET_ID,
            "source_revision": source_revision,
            "dataset_manifest_sha256": dataset_manifest_sha256,
            "rights_receipt_sha256": rights_receipt_sha256,
            "split_receipt_sha256": split_receipt_sha256,
            "metric_report_sha256": metric_report_sha256,
            "calibration_artifact_sha256": calibration_artifact_sha256,
            "reload_receipt_sha256": reload_receipt_sha256,
            "bundle_sha256": bundle_sha256,
            "status": "evidence_validated_not_promoted",
            "visual_reliability_authorized": False,
            "reason_codes": [
                "p8_independent_evaluation_required",
                "p8_claim_conformance_required",
            ],
        }
        return cls(
            source_dataset_id=_P6_SOURCE_DATASET_ID,
            source_revision=source_revision,
            dataset_manifest_sha256=dataset_manifest_sha256,
            rights_receipt_sha256=rights_receipt_sha256,
            split_receipt_sha256=split_receipt_sha256,
            metric_report_sha256=metric_report_sha256,
            calibration_artifact_sha256=calibration_artifact_sha256,
            reload_receipt_sha256=reload_receipt_sha256,
            bundle_sha256=bundle_sha256,
            status="evidence_validated_not_promoted",
            visual_reliability_authorized=False,
            reason_codes=(
                "p8_independent_evaluation_required",
                "p8_claim_conformance_required",
            ),
            receipt_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_dataset_id": self.source_dataset_id,
            "source_revision": self.source_revision,
            "dataset_manifest_sha256": self.dataset_manifest_sha256,
            "rights_receipt_sha256": self.rights_receipt_sha256,
            "split_receipt_sha256": self.split_receipt_sha256,
            "metric_report_sha256": self.metric_report_sha256,
            "calibration_artifact_sha256": self.calibration_artifact_sha256,
            "reload_receipt_sha256": self.reload_receipt_sha256,
            "bundle_sha256": self.bundle_sha256,
            "status": self.status,
            "visual_reliability_authorized": self.visual_reliability_authorized,
            "reason_codes": list(self.reason_codes),
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> P6EvidenceValidationReceipt:
        document = require_mapping(value, "p6_evidence_receipt")
        require_exact_keys(
            document,
            {
                "schema_version",
                "source_dataset_id",
                "source_revision",
                "dataset_manifest_sha256",
                "rights_receipt_sha256",
                "split_receipt_sha256",
                "metric_report_sha256",
                "calibration_artifact_sha256",
                "reload_receipt_sha256",
                "bundle_sha256",
                "status",
                "visual_reliability_authorized",
                "reason_codes",
                "receipt_sha256",
            },
            "p6_evidence_receipt",
        )
        return cls(
            schema_version=require_non_empty_string(
                document["schema_version"], "p6_evidence_receipt.schema_version"
            ),
            source_dataset_id=require_safe_identifier(
                document["source_dataset_id"],
                "p6_evidence_receipt.source_dataset_id",
            ),
            source_revision=require_safe_identifier(
                document["source_revision"], "p6_evidence_receipt.source_revision"
            ),
            dataset_manifest_sha256=require_sha256(
                document["dataset_manifest_sha256"],
                "p6_evidence_receipt.dataset_manifest_sha256",
            ),
            rights_receipt_sha256=require_sha256(
                document["rights_receipt_sha256"],
                "p6_evidence_receipt.rights_receipt_sha256",
            ),
            split_receipt_sha256=require_sha256(
                document["split_receipt_sha256"],
                "p6_evidence_receipt.split_receipt_sha256",
            ),
            metric_report_sha256=require_sha256(
                document["metric_report_sha256"],
                "p6_evidence_receipt.metric_report_sha256",
            ),
            calibration_artifact_sha256=require_sha256(
                document["calibration_artifact_sha256"],
                "p6_evidence_receipt.calibration_artifact_sha256",
            ),
            reload_receipt_sha256=require_sha256(
                document["reload_receipt_sha256"],
                "p6_evidence_receipt.reload_receipt_sha256",
            ),
            bundle_sha256=require_sha256(
                document["bundle_sha256"], "p6_evidence_receipt.bundle_sha256"
            ),
            status=require_safe_identifier(
                document["status"], "p6_evidence_receipt.status"
            ),
            visual_reliability_authorized=require_boolean(
                document["visual_reliability_authorized"],
                "p6_evidence_receipt.visual_reliability_authorized",
            ),
            reason_codes=_safe_identifier_tuple(
                document["reason_codes"], "p6_evidence_receipt.reason_codes"
            ),
            receipt_sha256=require_sha256(
                document["receipt_sha256"], "p6_evidence_receipt.receipt_sha256"
            ),
        )


@dataclass(frozen=True, slots=True)
class P8ClaimConformanceReceipt:
    """Zero-violation replay receipt that remains non-promotional."""

    protocol_revision: str
    locked_source_dataset_id: str
    locked_source_revision: str
    locked_manifest_sha256: str
    read_zero_seal_sha256: str
    read_zero_seal_status: str
    p6_evidence_receipt_sha256: str
    p6_source_dataset_id: str
    p6_source_revision: str
    p6_dataset_manifest_sha256: str
    calibration_artifact_sha256: str
    catalog_sha256: str
    policy_sha256: str
    replay_sha256: str
    case_count: int
    ready_case_count: int
    violation_count: int
    status: str
    user_visual_claims_authorized: bool
    reason_codes: tuple[str, ...]
    bundle_sha256: str
    receipt_sha256: str
    schema_version: str = P8_CONFORMANCE_RECEIPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P8_CONFORMANCE_RECEIPT_SCHEMA_VERSION:
            raise VisualFeedbackContractError("P8 conformance receipt schema differs")
        require_safe_identifier(self.protocol_revision, "protocol_revision")
        if self.locked_source_dataset_id != _P8_SOURCE_DATASET_ID:
            raise VisualFeedbackContractError("P8 locked source dataset differs")
        require_safe_identifier(self.locked_source_revision, "locked_source_revision")
        if self.read_zero_seal_status != "digest_assertion_only":
            raise VisualFeedbackContractError("P8 read-zero seal status differs")
        if self.p6_source_dataset_id != _P6_SOURCE_DATASET_ID:
            raise VisualFeedbackContractError("P8 P6 source dataset differs")
        require_safe_identifier(self.p6_source_revision, "p6_source_revision")
        for name in (
            "locked_manifest_sha256",
            "read_zero_seal_sha256",
            "p6_evidence_receipt_sha256",
            "p6_dataset_manifest_sha256",
            "calibration_artifact_sha256",
            "catalog_sha256",
            "policy_sha256",
            "replay_sha256",
            "bundle_sha256",
            "receipt_sha256",
        ):
            require_sha256(getattr(self, name), name)
        require_integer(self.case_count, "case_count", minimum=1)
        require_integer(self.ready_case_count, "ready_case_count", minimum=1)
        if self.ready_case_count > self.case_count:
            raise VisualFeedbackContractError("P8 ready case count exceeds total")
        if self.violation_count != 0:
            raise VisualFeedbackContractError(
                "only zero-violation P8 receipts may be issued"
            )
        if self.status != "zero_violations_not_promoted":
            raise VisualFeedbackContractError("P8 conformance receipt status differs")
        if self.user_visual_claims_authorized is not False:
            raise VisualFeedbackContractError(
                "claim conformance alone cannot authorize user visual claims"
            )
        if self.reason_codes != (
            "engineering_conformance_only",
            "read_zero_seal_digest_assertion_not_generated",
            "separate_runtime_promotion_required",
        ):
            raise VisualFeedbackContractError("P8 receipt reason codes differ")
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("P8 conformance receipt digest differs")

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "protocol_revision": self.protocol_revision,
            "locked_source_dataset_id": self.locked_source_dataset_id,
            "locked_source_revision": self.locked_source_revision,
            "locked_manifest_sha256": self.locked_manifest_sha256,
            "read_zero_seal_sha256": self.read_zero_seal_sha256,
            "read_zero_seal_status": self.read_zero_seal_status,
            "p6_evidence_receipt_sha256": self.p6_evidence_receipt_sha256,
            "p6_source_dataset_id": self.p6_source_dataset_id,
            "p6_source_revision": self.p6_source_revision,
            "p6_dataset_manifest_sha256": self.p6_dataset_manifest_sha256,
            "calibration_artifact_sha256": self.calibration_artifact_sha256,
            "catalog_sha256": self.catalog_sha256,
            "policy_sha256": self.policy_sha256,
            "replay_sha256": self.replay_sha256,
            "case_count": self.case_count,
            "ready_case_count": self.ready_case_count,
            "violation_count": self.violation_count,
            "status": self.status,
            "user_visual_claims_authorized": self.user_visual_claims_authorized,
            "reason_codes": list(self.reason_codes),
            "bundle_sha256": self.bundle_sha256,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> P8ClaimConformanceReceipt:
        document = require_mapping(value, "p8_conformance_receipt")
        require_exact_keys(
            document,
            {
                "schema_version",
                "protocol_revision",
                "locked_source_dataset_id",
                "locked_source_revision",
                "locked_manifest_sha256",
                "read_zero_seal_sha256",
                "read_zero_seal_status",
                "p6_evidence_receipt_sha256",
                "p6_source_dataset_id",
                "p6_source_revision",
                "p6_dataset_manifest_sha256",
                "calibration_artifact_sha256",
                "catalog_sha256",
                "policy_sha256",
                "replay_sha256",
                "case_count",
                "ready_case_count",
                "violation_count",
                "status",
                "user_visual_claims_authorized",
                "reason_codes",
                "bundle_sha256",
                "receipt_sha256",
            },
            "p8_conformance_receipt",
        )
        return cls(
            schema_version=require_non_empty_string(
                document["schema_version"],
                "p8_conformance_receipt.schema_version",
            ),
            protocol_revision=require_safe_identifier(
                document["protocol_revision"],
                "p8_conformance_receipt.protocol_revision",
            ),
            locked_source_dataset_id=require_safe_identifier(
                document["locked_source_dataset_id"],
                "p8_conformance_receipt.locked_source_dataset_id",
            ),
            locked_source_revision=require_safe_identifier(
                document["locked_source_revision"],
                "p8_conformance_receipt.locked_source_revision",
            ),
            locked_manifest_sha256=require_sha256(
                document["locked_manifest_sha256"],
                "p8_conformance_receipt.locked_manifest_sha256",
            ),
            read_zero_seal_sha256=require_sha256(
                document["read_zero_seal_sha256"],
                "p8_conformance_receipt.read_zero_seal_sha256",
            ),
            read_zero_seal_status=require_safe_identifier(
                document["read_zero_seal_status"],
                "p8_conformance_receipt.read_zero_seal_status",
            ),
            p6_evidence_receipt_sha256=require_sha256(
                document["p6_evidence_receipt_sha256"],
                "p8_conformance_receipt.p6_evidence_receipt_sha256",
            ),
            p6_source_dataset_id=require_safe_identifier(
                document["p6_source_dataset_id"],
                "p8_conformance_receipt.p6_source_dataset_id",
            ),
            p6_source_revision=require_safe_identifier(
                document["p6_source_revision"],
                "p8_conformance_receipt.p6_source_revision",
            ),
            p6_dataset_manifest_sha256=require_sha256(
                document["p6_dataset_manifest_sha256"],
                "p8_conformance_receipt.p6_dataset_manifest_sha256",
            ),
            calibration_artifact_sha256=require_sha256(
                document["calibration_artifact_sha256"],
                "p8_conformance_receipt.calibration_artifact_sha256",
            ),
            catalog_sha256=require_sha256(
                document["catalog_sha256"],
                "p8_conformance_receipt.catalog_sha256",
            ),
            policy_sha256=require_sha256(
                document["policy_sha256"],
                "p8_conformance_receipt.policy_sha256",
            ),
            replay_sha256=require_sha256(
                document["replay_sha256"],
                "p8_conformance_receipt.replay_sha256",
            ),
            case_count=require_integer(
                document["case_count"],
                "p8_conformance_receipt.case_count",
                minimum=1,
            ),
            ready_case_count=require_integer(
                document["ready_case_count"],
                "p8_conformance_receipt.ready_case_count",
                minimum=1,
            ),
            violation_count=require_integer(
                document["violation_count"],
                "p8_conformance_receipt.violation_count",
                minimum=0,
            ),
            status=require_safe_identifier(
                document["status"], "p8_conformance_receipt.status"
            ),
            user_visual_claims_authorized=require_boolean(
                document["user_visual_claims_authorized"],
                "p8_conformance_receipt.user_visual_claims_authorized",
            ),
            reason_codes=_safe_identifier_tuple(
                document["reason_codes"],
                "p8_conformance_receipt.reason_codes",
            ),
            bundle_sha256=require_sha256(
                document["bundle_sha256"],
                "p8_conformance_receipt.bundle_sha256",
            ),
            receipt_sha256=require_sha256(
                document["receipt_sha256"],
                "p8_conformance_receipt.receipt_sha256",
            ),
        )


def validate_p6_evidence_bundle(value: object) -> P6EvidenceValidationReceipt:
    """Validate an AI Hub 68-derived, privacy-safe P6 summary bundle."""

    bundle = require_mapping(value, "p6_evidence_bundle")
    require_exact_keys(
        bundle,
        {
            "schema_version",
            "source_dataset_id",
            "source_revision",
            "dataset_manifest_sha256",
            "capture_contract_id",
            "population_id_sha256",
            "rights_receipt",
            "split_receipt",
            "pipeline_identity",
            "metric_report",
            "calibration_artifact",
            "reload_receipt",
            "bundle_sha256",
        },
        "p6_evidence_bundle",
    )
    if bundle["schema_version"] != P6_EVIDENCE_BUNDLE_SCHEMA_VERSION:
        raise VisualFeedbackContractError("P6 evidence bundle schema differs")
    if bundle["source_dataset_id"] != _P6_SOURCE_DATASET_ID:
        raise VisualFeedbackContractError("P6 evidence must be derived from AI Hub 68")
    source_revision = require_safe_identifier(
        bundle["source_revision"], "p6_evidence_bundle.source_revision"
    )
    manifest_sha256 = require_sha256(
        bundle["dataset_manifest_sha256"],
        "p6_evidence_bundle.dataset_manifest_sha256",
    )
    capture_contract_id = require_safe_identifier(
        bundle["capture_contract_id"], "p6_evidence_bundle.capture_contract_id"
    )
    population_sha256 = require_sha256(
        bundle["population_id_sha256"],
        "p6_evidence_bundle.population_id_sha256",
    )
    bundle_sha256 = _validate_self_digest(
        bundle, "bundle_sha256", "p6_evidence_bundle"
    )

    rights = _validate_p6_rights_receipt(
        bundle["rights_receipt"], source_revision=source_revision
    )
    split = _validate_p6_split_receipt(
        bundle["split_receipt"], source_revision=source_revision
    )
    _validate_pipeline_identity(bundle["pipeline_identity"])
    metric = _validate_p6_metric_report(bundle["metric_report"])
    artifact = VisualCalibrationArtifact.from_json_object(
        bundle["calibration_artifact"]
    )
    if artifact.scope is not CalibrationScope.CALIBRATED:
        raise VisualFeedbackContractError(
            "P6 calibration artifact must use calibrated scope"
        )
    if artifact.capture_contract_id != capture_contract_id:
        raise VisualFeedbackContractError("P6 capture contract differs")
    if artifact.population_id_sha256 != population_sha256:
        raise VisualFeedbackContractError("P6 population digest differs")
    artifact_cues = {rule.cue_id for rule in artifact.rules}
    if not artifact_cues.issubset(metric["allowed_cue_ids"]):
        raise VisualFeedbackContractError(
            "calibration artifact contains a cue absent from the metric report"
        )
    reload_receipt = _validate_p6_reload_receipt(
        bundle["reload_receipt"], artifact=artifact
    )

    return P6EvidenceValidationReceipt.issue(
        source_revision=source_revision,
        dataset_manifest_sha256=manifest_sha256,
        rights_receipt_sha256=rights,
        split_receipt_sha256=split,
        metric_report_sha256=metric["receipt_sha256"],
        calibration_artifact_sha256=artifact.artifact_sha256,
        reload_receipt_sha256=reload_receipt,
        bundle_sha256=bundle_sha256,
    )


def validate_p8_claim_conformance_bundle(
    value: object,
) -> P8ClaimConformanceReceipt:
    """Replay privacy-safe summaries against a frozen catalog and policy.

    Schema or semantic mismatch raises and therefore cannot mint a receipt.
    """

    bundle = require_mapping(value, "p8_conformance_bundle")
    require_exact_keys(
        bundle,
        {
            "schema_version",
            "protocol_revision",
            "locked_source_dataset_id",
            "locked_source_revision",
            "locked_manifest_sha256",
            "read_zero_seal_sha256",
            "p6_evidence_receipt",
            "calibration_artifact_sha256",
            "claim_catalog",
            "claim_policy",
            "forbidden_claim_fixtures",
            "replay_cases",
            "bundle_sha256",
        },
        "p8_conformance_bundle",
    )
    if bundle["schema_version"] != P8_CONFORMANCE_BUNDLE_SCHEMA_VERSION:
        raise VisualFeedbackContractError("P8 conformance bundle schema differs")
    protocol_revision = require_safe_identifier(
        bundle["protocol_revision"], "p8_conformance_bundle.protocol_revision"
    )
    if bundle["locked_source_dataset_id"] != _P8_SOURCE_DATASET_ID:
        raise VisualFeedbackContractError("P8 replay must use the AI Hub 538 role")
    locked_source_revision = require_safe_identifier(
        bundle["locked_source_revision"],
        "p8_conformance_bundle.locked_source_revision",
    )
    locked_manifest_sha256 = require_sha256(
        bundle["locked_manifest_sha256"],
        "p8_conformance_bundle.locked_manifest_sha256",
    )
    read_zero_seal_sha256 = require_sha256(
        bundle["read_zero_seal_sha256"],
        "p8_conformance_bundle.read_zero_seal_sha256",
    )
    if read_zero_seal_sha256 == locked_manifest_sha256:
        raise VisualFeedbackContractError("P8 seal and manifest digests must differ")
    bundle_sha256 = _validate_self_digest(
        bundle, "bundle_sha256", "p8_conformance_bundle"
    )
    p6_receipt = P6EvidenceValidationReceipt.from_json_object(
        bundle["p6_evidence_receipt"]
    )
    calibration_sha256 = require_sha256(
        bundle["calibration_artifact_sha256"],
        "p8_conformance_bundle.calibration_artifact_sha256",
    )
    if calibration_sha256 != p6_receipt.calibration_artifact_sha256:
        raise VisualFeedbackContractError("P8 calibration does not match P6 receipt")

    catalog = VisualClaimCatalog.from_json_object(bundle["claim_catalog"])
    if catalog.scope is not CalibrationScope.CALIBRATED:
        raise VisualFeedbackContractError("P8 claim catalog must use calibrated scope")
    policy = _validate_claim_policy(bundle["claim_policy"], catalog=catalog)
    _validate_forbidden_claim_fixtures(
        bundle["forbidden_claim_fixtures"],
        forbidden_tokens=policy["forbidden_tokens"],
    )

    replay_values = _require_array(bundle["replay_cases"], "replay_cases")
    if not replay_values:
        raise VisualFeedbackContractError("P8 replay cases must not be empty")
    cases = tuple(
        _validate_replay_case(
            item,
            calibration_sha256=calibration_sha256,
            catalog=catalog,
            allowed_claim_bindings=policy["allowed_claim_bindings"],
        )
        for item in replay_values
    )
    case_ids = [item["case_id_sha256"] for item in cases]
    if len(set(case_ids)) != len(case_ids):
        raise VisualFeedbackContractError("P8 replay case ids must be unique")
    replay_scenarios = {item["scenario"] for item in cases}
    if replay_scenarios != _REQUIRED_REPLAY_SCENARIOS:
        raise VisualFeedbackContractError(
            "P8 replay suppression scenario coverage is incomplete"
        )
    ready_count = sum(item["is_ready"] for item in cases)
    if ready_count < 1:
        raise VisualFeedbackContractError(
            "P8 conformance requires at least one ready replay case"
        )
    exercised_claim_ids = {
        item["approved_claim_id"]
        for item in cases
        if item["approved_claim_id"] is not None
    }
    catalog_claim_ids = {claim.approved_claim_id for claim in catalog.claims}
    if exercised_claim_ids != catalog_claim_ids:
        raise VisualFeedbackContractError(
            "P8 replay must exercise every frozen catalog claim exactly by id"
        )

    replay_sha256 = sha256_document(
        {
            "protocol_revision": protocol_revision,
            "locked_source_dataset_id": _P8_SOURCE_DATASET_ID,
            "locked_source_revision": locked_source_revision,
            "locked_manifest_sha256": locked_manifest_sha256,
            "read_zero_seal_sha256": read_zero_seal_sha256,
            "case_sha256": [item["case_sha256"] for item in cases],
        }
    )
    unsigned = {
        "schema_version": P8_CONFORMANCE_RECEIPT_SCHEMA_VERSION,
        "protocol_revision": protocol_revision,
        "locked_source_dataset_id": _P8_SOURCE_DATASET_ID,
        "locked_source_revision": locked_source_revision,
        "locked_manifest_sha256": locked_manifest_sha256,
        "read_zero_seal_sha256": read_zero_seal_sha256,
        "read_zero_seal_status": "digest_assertion_only",
        "p6_evidence_receipt_sha256": p6_receipt.receipt_sha256,
        "p6_source_dataset_id": p6_receipt.source_dataset_id,
        "p6_source_revision": p6_receipt.source_revision,
        "p6_dataset_manifest_sha256": p6_receipt.dataset_manifest_sha256,
        "calibration_artifact_sha256": calibration_sha256,
        "catalog_sha256": catalog.catalog_sha256,
        "policy_sha256": policy["policy_sha256"],
        "replay_sha256": replay_sha256,
        "case_count": len(cases),
        "ready_case_count": ready_count,
        "violation_count": 0,
        "status": "zero_violations_not_promoted",
        "user_visual_claims_authorized": False,
        "reason_codes": [
            "engineering_conformance_only",
            "read_zero_seal_digest_assertion_not_generated",
            "separate_runtime_promotion_required",
        ],
        "bundle_sha256": bundle_sha256,
    }
    return P8ClaimConformanceReceipt(
        protocol_revision=protocol_revision,
        locked_source_dataset_id=_P8_SOURCE_DATASET_ID,
        locked_source_revision=locked_source_revision,
        locked_manifest_sha256=locked_manifest_sha256,
        read_zero_seal_sha256=read_zero_seal_sha256,
        read_zero_seal_status="digest_assertion_only",
        p6_evidence_receipt_sha256=p6_receipt.receipt_sha256,
        p6_source_dataset_id=p6_receipt.source_dataset_id,
        p6_source_revision=p6_receipt.source_revision,
        p6_dataset_manifest_sha256=p6_receipt.dataset_manifest_sha256,
        calibration_artifact_sha256=calibration_sha256,
        catalog_sha256=catalog.catalog_sha256,
        policy_sha256=policy["policy_sha256"],
        replay_sha256=replay_sha256,
        case_count=len(cases),
        ready_case_count=ready_count,
        violation_count=0,
        status="zero_violations_not_promoted",
        user_visual_claims_authorized=False,
        reason_codes=(
            "engineering_conformance_only",
            "read_zero_seal_digest_assertion_not_generated",
            "separate_runtime_promotion_required",
        ),
        bundle_sha256=bundle_sha256,
        receipt_sha256=sha256_document(unsigned),
    )


def _validate_p6_rights_receipt(value: object, *, source_revision: str) -> str:
    receipt = require_mapping(value, "p6_rights_receipt")
    require_exact_keys(
        receipt,
        {
            "schema_version",
            "source_dataset_id",
            "source_revision",
            "admission_status",
            "purpose_scope",
            "derived_artifact_use",
            "raw_media_git_export",
            "raw_identifier_export",
            "receipt_sha256",
        },
        "p6_rights_receipt",
    )
    expected = {
        "schema_version": _RIGHTS_SCHEMA_VERSION,
        "source_dataset_id": _P6_SOURCE_DATASET_ID,
        "source_revision": source_revision,
        "admission_status": "admitted",
        "purpose_scope": "visual_geometry_calibration",
        "derived_artifact_use": "permitted",
        "raw_media_git_export": "prohibited",
        "raw_identifier_export": "prohibited",
    }
    if any(receipt.get(key) != expected_value for key, expected_value in expected.items()):
        raise VisualFeedbackContractError("P6 rights receipt is not eligible")
    return _validate_self_digest(receipt, "receipt_sha256", "p6_rights_receipt")


def _validate_p6_split_receipt(value: object, *, source_revision: str) -> str:
    receipt = require_mapping(value, "p6_split_receipt")
    require_exact_keys(
        receipt,
        {
            "schema_version",
            "source_dataset_id",
            "source_revision",
            "assignment_unit",
            "development_partition_sha256",
            "calibration_partition_sha256",
            "development_actor_count",
            "calibration_actor_count",
            "actor_overlap_count",
            "attempt_overlap_count",
            "frozen_before_metric_access",
            "receipt_sha256",
        },
        "p6_split_receipt",
    )
    if (
        receipt["schema_version"] != _SPLIT_SCHEMA_VERSION
        or receipt["source_dataset_id"] != _P6_SOURCE_DATASET_ID
        or receipt["source_revision"] != source_revision
        or receipt["assignment_unit"] != "actor_id"
    ):
        raise VisualFeedbackContractError("P6 split identity differs")
    development_sha256 = require_sha256(
        receipt["development_partition_sha256"],
        "p6_split_receipt.development_partition_sha256",
    )
    calibration_sha256 = require_sha256(
        receipt["calibration_partition_sha256"],
        "p6_split_receipt.calibration_partition_sha256",
    )
    if development_sha256 == calibration_sha256:
        raise VisualFeedbackContractError("P6 split partitions must differ")
    require_integer(
        receipt["development_actor_count"],
        "p6_split_receipt.development_actor_count",
        minimum=1,
    )
    require_integer(
        receipt["calibration_actor_count"],
        "p6_split_receipt.calibration_actor_count",
        minimum=1,
    )
    actor_overlap = require_integer(
        receipt["actor_overlap_count"],
        "p6_split_receipt.actor_overlap_count",
        minimum=0,
    )
    attempt_overlap = require_integer(
        receipt["attempt_overlap_count"],
        "p6_split_receipt.attempt_overlap_count",
        minimum=0,
    )
    if actor_overlap != 0 or attempt_overlap != 0:
        raise VisualFeedbackContractError("P6 split overlap must be zero")
    if require_boolean(
        receipt["frozen_before_metric_access"],
        "p6_split_receipt.frozen_before_metric_access",
    ) is not True:
        raise VisualFeedbackContractError("P6 split was not frozen before metrics")
    return _validate_self_digest(receipt, "receipt_sha256", "p6_split_receipt")


def _validate_pipeline_identity(value: object) -> None:
    identity = require_mapping(value, "p6_pipeline_identity")
    require_exact_keys(
        identity,
        {
            "tracker_revision",
            "tracker_sha256",
            "normalizer_revision",
            "normalizer_sha256",
            "cue_extractor_revision",
            "cue_extractor_sha256",
            "metric_protocol_sha256",
        },
        "p6_pipeline_identity",
    )
    for name in (
        "tracker_revision",
        "normalizer_revision",
        "cue_extractor_revision",
    ):
        require_safe_identifier(identity[name], f"p6_pipeline_identity.{name}")
    for name in (
        "tracker_sha256",
        "normalizer_sha256",
        "cue_extractor_sha256",
        "metric_protocol_sha256",
    ):
        require_sha256(identity[name], f"p6_pipeline_identity.{name}")


def _validate_p6_metric_report(value: object) -> dict[str, object]:
    report = require_mapping(value, "p6_metric_report")
    require_exact_keys(
        report,
        {
            "schema_version",
            "evaluated_sample_count",
            "landmark_annotation_point_count",
            "normalized_landmark_error",
            "missing_fraction",
            "coverage_fraction",
            "slice_report_sha256",
            "repeatability_report_sha256",
            "allowed_cue_ids",
            "contains_pronunciation_truth",
            "contains_action_truth",
            "report_sha256",
        },
        "p6_metric_report",
    )
    if report["schema_version"] != _METRIC_SCHEMA_VERSION:
        raise VisualFeedbackContractError("P6 metric report schema differs")
    require_integer(
        report["evaluated_sample_count"],
        "p6_metric_report.evaluated_sample_count",
        minimum=1,
    )
    if report["landmark_annotation_point_count"] != 24:
        raise VisualFeedbackContractError("P6 annotation must use the 24-point role")
    nle = require_finite_number(
        report["normalized_landmark_error"],
        "p6_metric_report.normalized_landmark_error",
    )
    if nle < 0:
        raise VisualFeedbackContractError("P6 landmark error must be non-negative")
    for name in ("missing_fraction", "coverage_fraction"):
        fraction = require_finite_number(report[name], f"p6_metric_report.{name}")
        if not 0.0 <= fraction <= 1.0:
            raise VisualFeedbackContractError("P6 metric fraction is outside [0,1]")
    require_sha256(
        report["slice_report_sha256"], "p6_metric_report.slice_report_sha256"
    )
    require_sha256(
        report["repeatability_report_sha256"],
        "p6_metric_report.repeatability_report_sha256",
    )
    cue_ids = frozenset(
        _safe_identifier_tuple(report["allowed_cue_ids"], "allowed_cue_ids")
    )
    if not cue_ids or not cue_ids.issubset(_VISIBLE_CUE_ALLOWLIST):
        raise VisualFeedbackContractError("P6 metric report contains unsupported cues")
    if require_boolean(
        report["contains_pronunciation_truth"],
        "p6_metric_report.contains_pronunciation_truth",
    ):
        raise VisualFeedbackContractError("P6 report must not create pronunciation truth")
    if require_boolean(
        report["contains_action_truth"],
        "p6_metric_report.contains_action_truth",
    ):
        raise VisualFeedbackContractError("P6 report must not create action truth")
    return {
        "allowed_cue_ids": cue_ids,
        "receipt_sha256": _validate_self_digest(
            report, "report_sha256", "p6_metric_report"
        ),
    }


def _validate_p6_reload_receipt(
    value: object, *, artifact: VisualCalibrationArtifact
) -> str:
    receipt = require_mapping(value, "p6_reload_receipt")
    require_exact_keys(
        receipt,
        {
            "schema_version",
            "artifact_sha256",
            "artifact_document_sha256",
            "loader_revision",
            "fresh_process",
            "hash_match",
            "output_parity",
            "receipt_sha256",
        },
        "p6_reload_receipt",
    )
    if receipt["schema_version"] != _RELOAD_SCHEMA_VERSION:
        raise VisualFeedbackContractError("P6 reload receipt schema differs")
    if receipt["artifact_sha256"] != artifact.artifact_sha256:
        raise VisualFeedbackContractError("P6 reloaded artifact digest differs")
    expected_document_sha256 = sha256_document(artifact.to_json_object())
    if receipt["artifact_document_sha256"] != expected_document_sha256:
        raise VisualFeedbackContractError("P6 reload document digest differs")
    require_safe_identifier(
        receipt["loader_revision"], "p6_reload_receipt.loader_revision"
    )
    for name in ("fresh_process", "hash_match", "output_parity"):
        if require_boolean(receipt[name], f"p6_reload_receipt.{name}") is not True:
            raise VisualFeedbackContractError("P6 reload evidence is incomplete")
    return _validate_self_digest(receipt, "receipt_sha256", "p6_reload_receipt")


def _validate_claim_policy(
    value: object, *, catalog: VisualClaimCatalog
) -> dict[str, object]:
    policy = require_mapping(value, "visual_claim_policy")
    require_exact_keys(
        policy,
        {
            "schema_version",
            "policy_revision",
            "allowed_cue_ids",
            "allowed_renderer_keys",
            "allowed_claim_bindings",
            "forbidden_identifier_tokens",
            "policy_sha256",
        },
        "visual_claim_policy",
    )
    if policy["schema_version"] != _POLICY_SCHEMA_VERSION:
        raise VisualFeedbackContractError("visual claim policy schema differs")
    require_safe_identifier(policy["policy_revision"], "policy_revision")
    cue_ids = frozenset(
        _safe_identifier_tuple(policy["allowed_cue_ids"], "allowed_cue_ids")
    )
    renderer_keys = frozenset(
        _safe_identifier_tuple(
            policy["allowed_renderer_keys"], "allowed_renderer_keys"
        )
    )
    if not cue_ids or not cue_ids.issubset(_VISIBLE_CUE_ALLOWLIST):
        raise VisualFeedbackContractError("claim policy contains a non-visible cue")
    if not renderer_keys:
        raise VisualFeedbackContractError("claim policy renderer allowlist is empty")
    forbidden_tokens = frozenset(
        token.lower()
        for token in _safe_identifier_tuple(
            policy["forbidden_identifier_tokens"], "forbidden_identifier_tokens"
        )
    )
    if not _REQUIRED_FORBIDDEN_IDENTIFIER_TOKENS.issubset(forbidden_tokens):
        raise VisualFeedbackContractError("claim policy forbidden targets are incomplete")

    raw_bindings = _require_array(
        policy["allowed_claim_bindings"], "allowed_claim_bindings"
    )
    bindings: set[tuple[str, str, str, str]] = set()
    for raw_binding in raw_bindings:
        binding = require_mapping(raw_binding, "allowed_claim_binding")
        require_exact_keys(
            binding,
            {"approved_claim_id", "action_id", "renderer_key", "cue_id"},
            "allowed_claim_binding",
        )
        item = tuple(
            require_safe_identifier(binding[name], f"allowed_claim_binding.{name}")
            for name in ("approved_claim_id", "action_id", "renderer_key", "cue_id")
        )
        if item in bindings:
            raise VisualFeedbackContractError("claim policy binding is duplicated")
        bindings.add(item)
    catalog_bindings = {
        (
            claim.approved_claim_id,
            claim.action_id,
            claim.renderer_key,
            claim.cue_id,
        )
        for claim in catalog.claims
    }
    if bindings != catalog_bindings:
        raise VisualFeedbackContractError("claim policy and catalog bindings differ")
    for claim in catalog.claims:
        if claim.cue_id not in cue_ids or claim.renderer_key not in renderer_keys:
            raise VisualFeedbackContractError("catalog entry is outside claim policy")
        identifiers = (
            claim.action_id,
            claim.approved_claim_id,
            claim.renderer_key,
            claim.cue_id,
        )
        if _contains_forbidden_token(identifiers, forbidden_tokens):
            raise VisualFeedbackContractError("catalog contains a forbidden target token")
    return {
        "allowed_claim_bindings": frozenset(bindings),
        "forbidden_tokens": forbidden_tokens,
        "policy_sha256": _validate_self_digest(
            policy, "policy_sha256", "visual_claim_policy"
        ),
    }


def _validate_forbidden_claim_fixtures(
    value: object, *, forbidden_tokens: frozenset[str]
) -> None:
    raw_fixtures = _require_array(value, "forbidden_claim_fixtures")
    if not raw_fixtures:
        raise VisualFeedbackContractError("forbidden claim fixtures must not be empty")
    covered_tokens: set[str] = set()
    fixture_ids: set[str] = set()
    for raw_fixture in raw_fixtures:
        fixture = require_mapping(raw_fixture, "forbidden_claim_fixture")
        require_exact_keys(
            fixture,
            {
                "schema_version",
                "fixture_id",
                "candidate_identifier",
                "expected_result",
                "fixture_sha256",
            },
            "forbidden_claim_fixture",
        )
        if fixture["schema_version"] != _FORBIDDEN_FIXTURE_SCHEMA_VERSION:
            raise VisualFeedbackContractError("forbidden fixture schema differs")
        fixture_id = require_safe_identifier(
            fixture["fixture_id"], "forbidden_claim_fixture.fixture_id"
        )
        if fixture_id in fixture_ids:
            raise VisualFeedbackContractError("forbidden fixture id is duplicated")
        fixture_ids.add(fixture_id)
        candidate = require_safe_identifier(
            fixture["candidate_identifier"],
            "forbidden_claim_fixture.candidate_identifier",
        )
        if fixture["expected_result"] != "rejected":
            raise VisualFeedbackContractError("forbidden fixture expectation differs")
        matched = {
            token for token in forbidden_tokens if token in candidate.lower()
        }
        if not matched:
            raise VisualFeedbackContractError("forbidden fixture was not rejected")
        covered_tokens.update(matched)
        _validate_self_digest(
            fixture, "fixture_sha256", "forbidden_claim_fixture"
        )
    if not _REQUIRED_FORBIDDEN_IDENTIFIER_TOKENS.issubset(covered_tokens):
        raise VisualFeedbackContractError("forbidden fixtures do not cover required targets")


def _validate_replay_case(
    value: object,
    *,
    calibration_sha256: str,
    catalog: VisualClaimCatalog,
    allowed_claim_bindings: frozenset[tuple[str, str, str, str]],
) -> dict[str, object]:
    case = require_mapping(value, "visual_claim_replay_case")
    require_exact_keys(
        case,
        {
            "schema_version",
            "case_id_sha256",
            "scenario",
            "expected_phone",
            "context_id",
            "registered_binding_ref",
            "registered_phone_anchor_ref",
            "supplement",
            "case_sha256",
        },
        "visual_claim_replay_case",
    )
    if case["schema_version"] != _REPLAY_CASE_SCHEMA_VERSION:
        raise VisualFeedbackContractError("P8 replay case schema differs")
    case_id = require_sha256(
        case["case_id_sha256"], "visual_claim_replay_case.case_id_sha256"
    )
    scenario = require_safe_identifier(
        case["scenario"], "visual_claim_replay_case.scenario"
    )
    if scenario not in _REQUIRED_REPLAY_SCENARIOS:
        raise VisualFeedbackContractError("P8 replay scenario is unknown")
    expected_phone = require_text_token(
        case["expected_phone"], "visual_claim_replay_case.expected_phone"
    )
    context_id = require_safe_identifier(
        case["context_id"], "visual_claim_replay_case.context_id"
    )
    binding_ref = _optional_sha256(
        case["registered_binding_ref"],
        "visual_claim_replay_case.registered_binding_ref",
    )
    anchor_ref = _optional_sha256(
        case["registered_phone_anchor_ref"],
        "visual_claim_replay_case.registered_phone_anchor_ref",
    )
    supplement = VisualFeedbackSupplement.from_json_object(case["supplement"])
    if supplement.upstream_binding_ref != binding_ref:
        raise VisualFeedbackContractError("P8 replay binding is not registered")
    if supplement.upstream_phone_anchor_ref != anchor_ref:
        raise VisualFeedbackContractError("P8 replay phone anchor is not registered")
    if supplement.media_deletion_receipt.status == "failed":
        raise VisualFeedbackContractError("P8 replay media deletion failed")

    calibrated_evidence_present = False
    for evidence in supplement.cue_evidence:
        if (
            evidence.calibration_artifact_sha256 is not None
            and evidence.calibration_artifact_sha256 != calibration_sha256
        ):
            raise VisualFeedbackContractError(
                "P8 replay evidence calibration differs"
            )
        if evidence.calibrated_deviation is not None:
            calibrated_evidence_present = True
    if calibrated_evidence_present:
        _require_loaded_artifact_receipts(supplement, catalog=catalog)

    candidate = supplement.supplemental_action_candidate
    approved_claim_id: str | None = None
    if candidate is not None:
        if len(candidate.supporting_visual_evidence_ids) != 1:
            raise VisualFeedbackContractError(
                "P8 action candidate must reference exactly one cue evidence"
            )
        claim = next(
            (
                item
                for item in catalog.claims
                if item.approved_claim_id == candidate.approved_claim_id
            ),
            None,
        )
        if claim is None:
            raise VisualFeedbackContractError("P8 candidate claim is absent from catalog")
        binding = (
            candidate.approved_claim_id,
            candidate.action_id,
            candidate.renderer_key,
            claim.cue_id,
        )
        if binding not in allowed_claim_bindings:
            raise VisualFeedbackContractError("P8 candidate is outside frozen policy")
        if candidate.catalog_revision != catalog.revision:
            raise VisualFeedbackContractError("P8 candidate catalog revision differs")
        if expected_phone not in claim.expected_phones or context_id != claim.context_id:
            raise VisualFeedbackContractError("P8 candidate phone context differs")
        evidence_id = candidate.supporting_visual_evidence_ids[0]
        evidence = next(
            (item for item in supplement.cue_evidence if item.evidence_id == evidence_id),
            None,
        )
        if evidence is None or evidence.cue_state is not VisualCueState.OBSERVED:
            raise VisualFeedbackContractError("P8 candidate evidence is not observed")
        if evidence.cue_id != claim.cue_id:
            raise VisualFeedbackContractError("P8 candidate cue differs from catalog")
        if (
            evidence.calibration_artifact_sha256 != calibration_sha256
            or evidence.calibrated_deviation is None
            or evidence.calibrated_deviation == 0.0
        ):
            raise VisualFeedbackContractError("P8 candidate calibration differs")
        direction = (
            DeviationDirection.ABOVE_REFERENCE
            if evidence.calibrated_deviation > 0
            else DeviationDirection.BELOW_REFERENCE
        )
        if claim.direction is not direction:
            raise VisualFeedbackContractError("P8 candidate direction differs")
        _require_loaded_artifact_receipts(supplement, catalog=catalog)
        approved_claim_id = candidate.approved_claim_id
    else:
        # Strict supplement parsing already forbids a candidate on every non-ready
        # status.  This explicit branch makes suppression part of this gate rather
        # than relying on downstream rendering behavior.
        if any(
            evidence.cue_state
            in {
                VisualCueState.MISSING,
                VisualCueState.LOW_CONFIDENCE,
                VisualCueState.OUT_OF_DISTRIBUTION,
                VisualCueState.BACKEND_UNAVAILABLE,
            }
            and evidence.calibrated_deviation is not None
            for evidence in supplement.cue_evidence
        ):
            raise VisualFeedbackContractError(
                "P8 suppressed evidence cannot contain calibrated deviation"
            )

    _validate_replay_scenario(
        scenario=scenario,
        supplement=supplement,
        candidate_present=candidate is not None,
    )

    case_sha256 = _validate_self_digest(
        case, "case_sha256", "visual_claim_replay_case"
    )
    return {
        "case_id_sha256": case_id,
        "case_sha256": case_sha256,
        "scenario": scenario,
        "is_ready": candidate is not None,
        "approved_claim_id": approved_claim_id,
    }


def _require_loaded_artifact_receipts(
    supplement: VisualFeedbackSupplement, *, catalog: VisualClaimCatalog
) -> None:
    calibration_loaded = any(
        receipt.backend_id == "visual_calibration_artifact"
        and receipt.status == "loaded"
        for receipt in supplement.reference_calibration_receipts
    )
    catalog_loaded = any(
        receipt.backend_id == "visual_claim_catalog"
        and receipt.backend_revision == catalog.revision
        and receipt.status == "loaded"
        for receipt in supplement.reference_calibration_receipts
    )
    if not calibration_loaded or not catalog_loaded:
        raise VisualFeedbackContractError("P8 artifact load receipts are incomplete")


def _validate_replay_scenario(
    *,
    scenario: str,
    supplement: VisualFeedbackSupplement,
    candidate_present: bool,
) -> None:
    if scenario == "ready_claim":
        if (
            not candidate_present
            or supplement.status is not VisualFeedbackStatus.VISUAL_FEEDBACK_READY
        ):
            raise VisualFeedbackContractError("P8 ready scenario did not emit one claim")
        return
    if candidate_present:
        raise VisualFeedbackContractError("P8 suppression scenario emitted a claim")
    states = {evidence.cue_state for evidence in supplement.cue_evidence}
    if scenario == "missing_suppressed":
        valid = VisualCueState.MISSING in states
    elif scenario == "low_confidence_suppressed":
        valid = VisualCueState.LOW_CONFIDENCE in states
    elif scenario == "out_of_distribution_suppressed":
        valid = (
            VisualCueState.OUT_OF_DISTRIBUTION in states
            and supplement.status is VisualFeedbackStatus.VISUAL_UNSUPPORTED
        )
    elif scenario == "conflict_suppressed":
        valid = (
            supplement.evidence_relation
            is VisualEvidenceRelation.CONFLICTS_WITH_UPSTREAM
            and supplement.status is VisualFeedbackStatus.VISUAL_UNCERTAIN
        )
    elif scenario == "unsupported_slice_suppressed":
        valid = (
            supplement.status is VisualFeedbackStatus.VISUAL_UNSUPPORTED
            and "unsupported_capture_slice" in supplement.reason_codes
        )
    else:  # guarded by the scenario allowlist before this function
        valid = False
    if not valid:
        raise VisualFeedbackContractError("P8 suppression scenario evidence differs")


def _contains_forbidden_token(
    identifiers: Sequence[str], forbidden_tokens: frozenset[str]
) -> bool:
    return any(
        token in identifier.lower()
        for identifier in identifiers
        for token in forbidden_tokens
    )


def _validate_self_digest(
    document: Mapping[str, object], digest_key: str, name: str
) -> str:
    digest = require_sha256(document[digest_key], f"{name}.{digest_key}")
    unsigned = {key: value for key, value in document.items() if key != digest_key}
    if digest != sha256_document(unsigned):
        raise VisualFeedbackContractError(f"{name} digest differs")
    return digest


def _require_array(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise VisualFeedbackContractError(f"{name} must be an array")
    return value


def _optional_sha256(value: object, name: str) -> str | None:
    if value is None:
        return None
    return require_sha256(value, name)


def _safe_identifier_tuple(value: object, name: str) -> tuple[str, ...]:
    values = _require_array(value, name)
    result = tuple(
        require_safe_identifier(item, f"{name}[]") for item in values
    )
    if len(set(result)) != len(result):
        raise VisualFeedbackContractError(f"{name} must not contain duplicates")
    return result


__all__ = [
    "P6_EVIDENCE_BUNDLE_SCHEMA_VERSION",
    "P6_EVIDENCE_RECEIPT_SCHEMA_VERSION",
    "P6_METRIC_SUMMARY_SCHEMA_VERSION",
    "P6_RELOAD_RECEIPT_SCHEMA_VERSION",
    "P6_RIGHTS_RECEIPT_SCHEMA_VERSION",
    "P6_SPLIT_RECEIPT_SCHEMA_VERSION",
    "P8_CONFORMANCE_BUNDLE_SCHEMA_VERSION",
    "P8_CONFORMANCE_RECEIPT_SCHEMA_VERSION",
    "P8_CLAIM_POLICY_SCHEMA_VERSION",
    "P8_FORBIDDEN_FIXTURE_SCHEMA_VERSION",
    "P8_REPLAY_CASE_SCHEMA_VERSION",
    "P6EvidenceValidationReceipt",
    "P8ClaimConformanceReceipt",
    "validate_p6_evidence_bundle",
    "validate_p8_claim_conformance_bundle",
    "visual_gate_schema_summary",
]
