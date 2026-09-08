"""Pure fail-closed contracts for the blocked E01 phone-substitution family.

This module validates already-decoded JSON-like values only.  It does not read
audio, paths, environment variables, models, or policy artifacts; it does not
perform a splice, inference, metric computation, or publication.  Restricted
case and worker documents retain sensitive digests.  The A1 report exposes only
one digest for the private receipt bundle and count-derived aggregates.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Mapping, Sequence, cast


CASE_SCHEMA_VERSION = "voice_coach.e01.phone_case_sidecar.v2"
WORKER_SCHEMA_VERSION = "voice_coach.e01.phone_worker_receipt.v2"
REPORT_SCHEMA_VERSION = "voice_coach.e01.phone_paired_aggregate_report.v2"
RECEIPT_BUNDLE_SCHEMA_VERSION = "voice_coach.e01.phone_receipt_bundle.v2"

RELATION_ROLES = ("U_to_I", "U_to_T", "I_to_T")
POLICY_BINDING_FIELDS = (
    "preregistration_sha256",
    "phone_case_policy_sha256",
    "tolerance_policy_sha256",
    "minimum_support_policy_sha256",
    "invalid_case_policy_sha256",
    "u_to_i_relation_policy_sha256",
    "u_to_t_relation_policy_sha256",
    "i_to_t_relation_policy_sha256",
)
INVALID_REASON_CODES = (
    "verifier_unavailable",
    "metric_undefined",
    "preregistered_quality_guard",
    "other_preregistered",
)

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_CASE_ID_RE = re.compile(r"e01pc_[0-9a-f]{32}")
_MAX_SAMPLE_COUNT = 2_147_483_647
_MAX_AGGREGATE_COUNT = 1_000_000_000
_RATE_ABSOLUTE_TOLERANCE = 1e-12


class E01PhoneSubstitutionV2ContractError(ValueError):
    """Raised when a v2 static phone-substitution contract fails closed."""


@dataclass(frozen=True, slots=True)
class PhonePolicyBindings:
    """Exact preregistration and relation-policy identities for one case."""

    preregistration_sha256: str
    phone_case_policy_sha256: str
    tolerance_policy_sha256: str
    minimum_support_policy_sha256: str
    invalid_case_policy_sha256: str
    u_to_i_relation_policy_sha256: str
    u_to_t_relation_policy_sha256: str
    i_to_t_relation_policy_sha256: str


@dataclass(frozen=True, slots=True)
class ValidatedPhoneCase:
    """Minimal cross-document identity extracted from one restricted sidecar."""

    case_id: str
    sidecar_sha256: str
    policies: PhonePolicyBindings
    source_phone_label_sha256: str
    donor_phone_label_sha256: str
    source_audio_pcm_sha256: str
    edit_mask_sha256: str
    source_sample_count: int
    edit_mask_sample_count: int


@dataclass(frozen=True, slots=True)
class ValidatedPhoneWorkerReceipt:
    """Validated relation outcomes for one restricted U/I/T worker receipt."""

    case_id: str
    relation_statuses: tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class ValidatedPhoneAggregateReport:
    """Validated A1 aggregate fields with no restricted row-level identity."""

    private_receipt_bundle_sha256: str
    case_count: int
    relation_status_counts: tuple[tuple[int, int, int], ...]
    all_three_relations_passed_case_count: int
    any_invalid_relation_case_count: int
    decision: str


@dataclass(frozen=True, slots=True)
class ValidatedPhoneBundle:
    """End-to-end static validation summary for in-memory private documents."""

    case_count: int
    private_receipt_bundle_sha256: str
    decision: str
    actual_execution_performed_by_validator: bool = False
    paper_complete: bool = False


def canonical_json_bytes(value: Mapping[str, object]) -> bytes:
    """Return deterministic UTF-8 JSON bytes without performing any I/O."""

    if type(value) is not dict:
        raise E01PhoneSubstitutionV2ContractError(
            "canonical JSON root must be an exact dict"
        )
    try:
        return (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError) as exc:
        raise E01PhoneSubstitutionV2ContractError(
            "payload is not finite canonical JSON data"
        ) from exc


def _payload_sha256(value: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _exact_mapping(
    value: object,
    *,
    expected_keys: set[str],
    name: str,
) -> dict[str, object]:
    if type(value) is not dict:
        raise E01PhoneSubstitutionV2ContractError(f"{name} must be an exact object")
    result = cast(dict[object, object], value)
    if any(type(key) is not str for key in result):
        raise E01PhoneSubstitutionV2ContractError(f"{name} keys must be exact strings")
    observed_keys = cast(set[str], set(result))
    if observed_keys != expected_keys:
        raise E01PhoneSubstitutionV2ContractError(
            f"{name} keys differ; "
            f"missing={sorted(expected_keys - observed_keys)}, "
            f"extra={sorted(observed_keys - expected_keys)}"
        )
    return cast(dict[str, object], result)


def _exact_list(value: object, *, length: int, name: str) -> list[object]:
    if type(value) is not list or len(cast(list[object], value)) != length:
        raise E01PhoneSubstitutionV2ContractError(
            f"{name} must be an exact list of length {length}"
        )
    return cast(list[object], value)


def _expect_exact(value: object, expected: object, *, name: str) -> None:
    if type(value) is not type(expected) or value != expected:
        raise E01PhoneSubstitutionV2ContractError(f"{name} must equal {expected!r}")


def _exact_string(value: object, *, name: str) -> str:
    if type(value) is not str or not value:
        raise E01PhoneSubstitutionV2ContractError(
            f"{name} must be an exact non-empty string"
        )
    return value


def _sha256(value: object, *, name: str) -> str:
    digest = _exact_string(value, name=name)
    if _SHA256_RE.fullmatch(digest) is None or digest == "0" * 64:
        raise E01PhoneSubstitutionV2ContractError(
            f"{name} must be a nonzero lowercase SHA-256"
        )
    return digest


def _bounded_integer(
    value: object,
    *,
    minimum: int,
    maximum: int,
    name: str,
) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        raise E01PhoneSubstitutionV2ContractError(
            f"{name} must be an integer in [{minimum}, {maximum}]"
        )
    return value


def _nullable_rate(value: object, *, name: str) -> float | None:
    if value is None:
        return None
    if type(value) not in {int, float}:
        raise E01PhoneSubstitutionV2ContractError(
            f"{name} must be null or a finite number in [0, 1]"
        )
    result = float(cast(int | float, value))
    if not math.isfinite(result) or result < 0.0 or result > 1.0:
        raise E01PhoneSubstitutionV2ContractError(
            f"{name} must be null or a finite number in [0, 1]"
        )
    return 0.0 if result == 0.0 else result


def _expect_rate(
    observed: object,
    *,
    numerator: int,
    denominator: int,
    name: str,
) -> None:
    rate = _nullable_rate(observed, name=name)
    if denominator == 0:
        if rate is not None:
            raise E01PhoneSubstitutionV2ContractError(
                f"{name} must be null when denominator is zero"
            )
        return
    expected = numerator / denominator
    if rate is None or not math.isclose(
        rate,
        expected,
        rel_tol=0.0,
        abs_tol=_RATE_ABSOLUTE_TOLERANCE,
    ):
        raise E01PhoneSubstitutionV2ContractError(
            f"{name} does not match aggregate counts"
        )


def _validate_policy_bindings(value: object, *, name: str) -> PhonePolicyBindings:
    mapping = _exact_mapping(
        value,
        expected_keys=set(POLICY_BINDING_FIELDS),
        name=name,
    )
    values = {
        field: _sha256(mapping[field], name=f"{name}.{field}")
        for field in POLICY_BINDING_FIELDS
    }
    return PhonePolicyBindings(**values)


def validate_phone_case_sidecar(
    payload: Mapping[str, object],
) -> ValidatedPhoneCase:
    """Validate one frozen restricted case without resolving any material."""

    case = _exact_mapping(
        payload,
        expected_keys={
            "schema_version",
            "status",
            "privacy_zone",
            "access_scope",
            "case_id",
            "contains_locked_test",
            "contains_s203_expert_holdout",
            "contains_raw_audio",
            "contains_paths",
            "role_graph",
            "policy_bindings",
            "human_observation",
            "consent",
            "lineage",
            "restricted_material",
        },
        name="case",
    )
    _expect_exact(
        case["schema_version"],
        CASE_SCHEMA_VERSION,
        name="case.schema_version",
    )
    _expect_exact(
        case["status"],
        "frozen_before_execution",
        name="case.status",
    )
    _expect_exact(
        case["privacy_zone"],
        "D1_pseudonymized_restricted_non_locked",
        name="case.privacy_zone",
    )
    _expect_exact(
        case["access_scope"],
        "phone_executor_and_independent_verifier_only",
        name="case.access_scope",
    )
    case_id = _exact_string(case["case_id"], name="case.case_id")
    if _CASE_ID_RE.fullmatch(case_id) is None:
        raise E01PhoneSubstitutionV2ContractError("case.case_id format is invalid")
    for field in (
        "contains_locked_test",
        "contains_s203_expert_holdout",
        "contains_raw_audio",
        "contains_paths",
    ):
        _expect_exact(case[field], False, name=f"case.{field}")
    role_graph = _exact_list(case["role_graph"], length=3, name="case.role_graph")
    if tuple(role_graph) != RELATION_ROLES or any(
        type(role) is not str for role in role_graph
    ):
        raise E01PhoneSubstitutionV2ContractError(
            "case.role_graph must be U_to_I, U_to_T, I_to_T in order"
        )

    policies = _validate_policy_bindings(
        case["policy_bindings"],
        name="case.policy_bindings",
    )

    observation = _exact_mapping(
        case["human_observation"],
        expected_keys={
            "boundary_source",
            "source_boundary_annotation_sha256",
            "donor_boundary_annotation_sha256",
            "source_phone_label_sha256",
            "donor_phone_label_sha256",
            "source_and_donor_phone_differ",
            "observers_independent_from_s1",
        },
        name="case.human_observation",
    )
    _expect_exact(
        observation["boundary_source"],
        "human_observed_adjudicated_not_expected_g2p",
        name="case.human_observation.boundary_source",
    )
    _sha256(
        observation["source_boundary_annotation_sha256"],
        name="case.human_observation.source_boundary_annotation_sha256",
    )
    _sha256(
        observation["donor_boundary_annotation_sha256"],
        name="case.human_observation.donor_boundary_annotation_sha256",
    )
    source_phone_label_sha256 = _sha256(
        observation["source_phone_label_sha256"],
        name="case.human_observation.source_phone_label_sha256",
    )
    donor_phone_label_sha256 = _sha256(
        observation["donor_phone_label_sha256"],
        name="case.human_observation.donor_phone_label_sha256",
    )
    if source_phone_label_sha256 == donor_phone_label_sha256:
        raise E01PhoneSubstitutionV2ContractError(
            "source and donor observed phone labels must differ"
        )
    _expect_exact(
        observation["source_and_donor_phone_differ"],
        True,
        name="case.human_observation.source_and_donor_phone_differ",
    )
    _expect_exact(
        observation["observers_independent_from_s1"],
        True,
        name="case.human_observation.observers_independent_from_s1",
    )

    consent = _exact_mapping(
        case["consent"],
        expected_keys={
            "consent_scope_sha256",
            "source_derivative_processing_allowed",
            "donor_derivative_processing_allowed",
            "phone_splice_or_resynthesis_allowed",
            "public_audio_release_allowed",
        },
        name="case.consent",
    )
    _sha256(
        consent["consent_scope_sha256"],
        name="case.consent.consent_scope_sha256",
    )
    for field in (
        "source_derivative_processing_allowed",
        "donor_derivative_processing_allowed",
        "phone_splice_or_resynthesis_allowed",
    ):
        _expect_exact(consent[field], True, name=f"case.consent.{field}")
    _expect_exact(
        consent["public_audio_release_allowed"],
        False,
        name="case.consent.public_audio_release_allowed",
    )

    lineage = _exact_mapping(
        case["lineage"],
        expected_keys={
            "same_speaker_required",
            "same_session_required",
            "same_prompt_context_required",
            "source_speaker_lineage_sha256",
            "donor_speaker_lineage_sha256",
            "source_session_lineage_sha256",
            "donor_session_lineage_sha256",
            "source_prompt_context_lineage_sha256",
            "donor_prompt_context_lineage_sha256",
        },
        name="case.lineage",
    )
    for field in (
        "same_speaker_required",
        "same_session_required",
        "same_prompt_context_required",
    ):
        _expect_exact(lineage[field], True, name=f"case.lineage.{field}")
    lineage_pairs = (
        ("source_speaker_lineage_sha256", "donor_speaker_lineage_sha256"),
        ("source_session_lineage_sha256", "donor_session_lineage_sha256"),
        (
            "source_prompt_context_lineage_sha256",
            "donor_prompt_context_lineage_sha256",
        ),
    )
    for source_field, donor_field in lineage_pairs:
        source_digest = _sha256(
            lineage[source_field],
            name=f"case.lineage.{source_field}",
        )
        donor_digest = _sha256(
            lineage[donor_field],
            name=f"case.lineage.{donor_field}",
        )
        if source_digest != donor_digest:
            raise E01PhoneSubstitutionV2ContractError(
                f"case lineage mismatch between {source_field} and {donor_field}"
            )

    material = _exact_mapping(
        case["restricted_material"],
        expected_keys={
            "source_audio_pcm_sha256",
            "donor_audio_pcm_sha256",
            "source_region_sha256",
            "donor_region_sha256",
            "edit_mask_sha256",
            "source_sample_count",
            "edit_mask_sample_count",
        },
        name="case.restricted_material",
    )
    source_audio_pcm_sha256 = _sha256(
        material["source_audio_pcm_sha256"],
        name="case.restricted_material.source_audio_pcm_sha256",
    )
    _sha256(
        material["donor_audio_pcm_sha256"],
        name="case.restricted_material.donor_audio_pcm_sha256",
    )
    _sha256(
        material["source_region_sha256"],
        name="case.restricted_material.source_region_sha256",
    )
    _sha256(
        material["donor_region_sha256"],
        name="case.restricted_material.donor_region_sha256",
    )
    edit_mask_sha256 = _sha256(
        material["edit_mask_sha256"],
        name="case.restricted_material.edit_mask_sha256",
    )
    source_sample_count = _bounded_integer(
        material["source_sample_count"],
        minimum=2,
        maximum=_MAX_SAMPLE_COUNT,
        name="case.restricted_material.source_sample_count",
    )
    edit_mask_sample_count = _bounded_integer(
        material["edit_mask_sample_count"],
        minimum=1,
        maximum=_MAX_SAMPLE_COUNT,
        name="case.restricted_material.edit_mask_sample_count",
    )
    if edit_mask_sample_count >= source_sample_count:
        raise E01PhoneSubstitutionV2ContractError(
            "edit mask must leave at least one outside-mask sample"
        )

    return ValidatedPhoneCase(
        case_id=case_id,
        sidecar_sha256=_payload_sha256(case),
        policies=policies,
        source_phone_label_sha256=source_phone_label_sha256,
        donor_phone_label_sha256=donor_phone_label_sha256,
        source_audio_pcm_sha256=source_audio_pcm_sha256,
        edit_mask_sha256=edit_mask_sha256,
        source_sample_count=source_sample_count,
        edit_mask_sample_count=edit_mask_sample_count,
    )


def _validate_role_output(
    value: object,
    *,
    role: str,
    origin: str,
) -> tuple[str, str, int]:
    output = _exact_mapping(
        value,
        expected_keys={
            "role",
            "origin",
            "pcm_sha256",
            "outside_edit_mask_pcm_sha256",
            "sample_count",
        },
        name=f"receipt.role_outputs.{role}",
    )
    _expect_exact(output["role"], role, name=f"receipt.role_outputs.{role}.role")
    _expect_exact(
        output["origin"],
        origin,
        name=f"receipt.role_outputs.{role}.origin",
    )
    pcm_sha256 = _sha256(
        output["pcm_sha256"],
        name=f"receipt.role_outputs.{role}.pcm_sha256",
    )
    outside_sha256 = _sha256(
        output["outside_edit_mask_pcm_sha256"],
        name=f"receipt.role_outputs.{role}.outside_edit_mask_pcm_sha256",
    )
    sample_count = _bounded_integer(
        output["sample_count"],
        minimum=2,
        maximum=_MAX_SAMPLE_COUNT,
        name=f"receipt.role_outputs.{role}.sample_count",
    )
    return pcm_sha256, outside_sha256, sample_count


def _validate_independent_verifier(value: object) -> None:
    verifier = _exact_mapping(
        value,
        expected_keys={
            "verification_receipt_sha256",
            "s1_model_identity_sha256",
            "phone_verifier_model_identity_sha256",
            "s1_training_lineage_sha256",
            "phone_verifier_training_lineage_sha256",
            "lineage_policy",
            "s1_weights_accessed",
            "s1_features_accessed",
            "s1_predictions_accessed",
            "s1_thresholds_accessed",
            "trained_on_s1_outputs",
            "human_observed_boundaries_used",
        },
        name="receipt.independent_phone_verifier",
    )
    _sha256(
        verifier["verification_receipt_sha256"],
        name="receipt.independent_phone_verifier.verification_receipt_sha256",
    )
    s1_model = _sha256(
        verifier["s1_model_identity_sha256"],
        name="receipt.independent_phone_verifier.s1_model_identity_sha256",
    )
    phone_model = _sha256(
        verifier["phone_verifier_model_identity_sha256"],
        name=(
            "receipt.independent_phone_verifier.phone_verifier_model_identity_sha256"
        ),
    )
    s1_training = _sha256(
        verifier["s1_training_lineage_sha256"],
        name="receipt.independent_phone_verifier.s1_training_lineage_sha256",
    )
    phone_training = _sha256(
        verifier["phone_verifier_training_lineage_sha256"],
        name=(
            "receipt.independent_phone_verifier.phone_verifier_training_lineage_sha256"
        ),
    )
    if s1_model == phone_model or s1_training == phone_training:
        raise E01PhoneSubstitutionV2ContractError(
            "phone verifier model and training lineage must be independent from S1"
        )
    _expect_exact(
        verifier["lineage_policy"],
        "disjoint_from_s1_models_features_predictions_thresholds",
        name="receipt.independent_phone_verifier.lineage_policy",
    )
    for field in (
        "s1_weights_accessed",
        "s1_features_accessed",
        "s1_predictions_accessed",
        "s1_thresholds_accessed",
        "trained_on_s1_outputs",
    ):
        _expect_exact(
            verifier[field],
            False,
            name=f"receipt.independent_phone_verifier.{field}",
        )
    _expect_exact(
        verifier["human_observed_boundaries_used"],
        True,
        name="receipt.independent_phone_verifier.human_observed_boundaries_used",
    )


def _validate_stage_separation(value: object) -> None:
    stages = _exact_mapping(
        value,
        expected_keys={
            "s201_role",
            "s201_phone_gold_feature_or_target_used",
            "s201_score_used_for_phone_decision",
            "s202_role",
            "s202_used_as_phone_gold",
            "s202_phone_label_or_boundary_used",
            "s202_score_used_for_phone_decision",
        },
        name="receipt.stage_separation",
    )
    _expect_exact(
        stages["s201_role"],
        "content_consistency_routing_only",
        name="receipt.stage_separation.s201_role",
    )
    _expect_exact(
        stages["s202_role"],
        "independent_utterance_quality_auditor_not_phone_gold",
        name="receipt.stage_separation.s202_role",
    )
    for field in (
        "s201_phone_gold_feature_or_target_used",
        "s201_score_used_for_phone_decision",
        "s202_used_as_phone_gold",
        "s202_phone_label_or_boundary_used",
        "s202_score_used_for_phone_decision",
    ):
        _expect_exact(
            stages[field],
            False,
            name=f"receipt.stage_separation.{field}",
        )


def _validate_relation(
    value: object,
    *,
    relation_role: str,
    source_role: str,
    comparison_role: str,
    expectation: str,
    expected_policy_sha256: str,
) -> str:
    relation = _exact_mapping(
        value,
        expected_keys={
            "source_role",
            "comparison_role",
            "expectation",
            "relation_policy_sha256",
            "status",
            "invalid_reason_code",
        },
        name=f"receipt.relations.{relation_role}",
    )
    _expect_exact(
        relation["source_role"],
        source_role,
        name=f"receipt.relations.{relation_role}.source_role",
    )
    _expect_exact(
        relation["comparison_role"],
        comparison_role,
        name=f"receipt.relations.{relation_role}.comparison_role",
    )
    _expect_exact(
        relation["expectation"],
        expectation,
        name=f"receipt.relations.{relation_role}.expectation",
    )
    policy_sha256 = _sha256(
        relation["relation_policy_sha256"],
        name=f"receipt.relations.{relation_role}.relation_policy_sha256",
    )
    if policy_sha256 != expected_policy_sha256:
        raise E01PhoneSubstitutionV2ContractError(
            f"receipt.relations.{relation_role} policy binding mismatch"
        )
    status = _exact_string(
        relation["status"],
        name=f"receipt.relations.{relation_role}.status",
    )
    if status not in {"passed", "failed", "invalid"}:
        raise E01PhoneSubstitutionV2ContractError(
            f"receipt.relations.{relation_role}.status is invalid"
        )
    reason = relation["invalid_reason_code"]
    if status == "invalid":
        if type(reason) is not str or reason not in INVALID_REASON_CODES:
            raise E01PhoneSubstitutionV2ContractError(
                f"receipt.relations.{relation_role} invalid status needs "
                "a preregistered reason"
            )
    elif reason is not None:
        raise E01PhoneSubstitutionV2ContractError(
            f"receipt.relations.{relation_role} non-invalid status needs null reason"
        )
    return status


def validate_phone_worker_receipt(
    payload: Mapping[str, object],
    *,
    case: ValidatedPhoneCase,
) -> ValidatedPhoneWorkerReceipt:
    """Validate one restricted worker receipt against its frozen case."""

    if type(case) is not ValidatedPhoneCase:
        raise E01PhoneSubstitutionV2ContractError(
            "case must be an exact ValidatedPhoneCase"
        )
    receipt = _exact_mapping(
        payload,
        expected_keys={
            "schema_version",
            "status",
            "privacy_zone",
            "access_scope",
            "case_id",
            "case_sidecar_sha256",
            "worker_implementation_sha256",
            "runtime_identity_sha256",
            "policy_bindings",
            "role_outputs",
            "edit_mask",
            "invariants",
            "observed_phone_bindings",
            "independent_phone_verifier",
            "stage_separation",
            "relations",
            "publication_allowed",
        },
        name="receipt",
    )
    _expect_exact(
        receipt["schema_version"],
        WORKER_SCHEMA_VERSION,
        name="receipt.schema_version",
    )
    _expect_exact(
        receipt["status"],
        "completed_restricted_receipt",
        name="receipt.status",
    )
    _expect_exact(
        receipt["privacy_zone"],
        "D1_pseudonymized_restricted_non_locked",
        name="receipt.privacy_zone",
    )
    _expect_exact(
        receipt["access_scope"],
        "phone_executor_and_independent_verifier_only",
        name="receipt.access_scope",
    )
    _expect_exact(receipt["case_id"], case.case_id, name="receipt.case_id")
    _expect_exact(
        _sha256(
            receipt["case_sidecar_sha256"],
            name="receipt.case_sidecar_sha256",
        ),
        case.sidecar_sha256,
        name="receipt.case_sidecar_sha256",
    )
    _sha256(
        receipt["worker_implementation_sha256"],
        name="receipt.worker_implementation_sha256",
    )
    _sha256(
        receipt["runtime_identity_sha256"],
        name="receipt.runtime_identity_sha256",
    )
    receipt_policies = _validate_policy_bindings(
        receipt["policy_bindings"],
        name="receipt.policy_bindings",
    )
    if receipt_policies != case.policies:
        raise E01PhoneSubstitutionV2ContractError(
            "receipt policy bindings differ from frozen case"
        )

    outputs = _exact_mapping(
        receipt["role_outputs"],
        expected_keys={"U", "I", "T"},
        name="receipt.role_outputs",
    )
    u_pcm, u_outside, u_count = _validate_role_output(
        outputs["U"],
        role="U",
        origin="unmodified_source",
    )
    i_pcm, i_outside, i_count = _validate_role_output(
        outputs["I"],
        role="I",
        origin="identity_control_resynthesis",
    )
    t_pcm, t_outside, t_count = _validate_role_output(
        outputs["T"],
        role="T",
        origin="target_phone_treatment_resynthesis",
    )
    if u_pcm != case.source_audio_pcm_sha256:
        raise E01PhoneSubstitutionV2ContractError(
            "U PCM digest differs from frozen source"
        )
    if len({u_count, i_count, t_count, case.source_sample_count}) != 1:
        raise E01PhoneSubstitutionV2ContractError(
            "U, I, and T must retain the frozen source sample count"
        )
    if len({u_outside, i_outside, t_outside}) != 1:
        raise E01PhoneSubstitutionV2ContractError(
            "U, I, and T must be byte-identical outside the edit mask"
        )
    if t_pcm == u_pcm or t_pcm == i_pcm:
        raise E01PhoneSubstitutionV2ContractError(
            "targeted T PCM must differ from both U and I"
        )

    mask = _exact_mapping(
        receipt["edit_mask"],
        expected_keys={
            "edit_mask_sha256",
            "masked_sample_count",
            "outside_mask_sample_count",
        },
        name="receipt.edit_mask",
    )
    _expect_exact(
        _sha256(
            mask["edit_mask_sha256"],
            name="receipt.edit_mask.edit_mask_sha256",
        ),
        case.edit_mask_sha256,
        name="receipt.edit_mask.edit_mask_sha256",
    )
    masked_sample_count = _bounded_integer(
        mask["masked_sample_count"],
        minimum=1,
        maximum=_MAX_SAMPLE_COUNT,
        name="receipt.edit_mask.masked_sample_count",
    )
    outside_sample_count = _bounded_integer(
        mask["outside_mask_sample_count"],
        minimum=1,
        maximum=_MAX_SAMPLE_COUNT,
        name="receipt.edit_mask.outside_mask_sample_count",
    )
    if masked_sample_count != case.edit_mask_sample_count:
        raise E01PhoneSubstitutionV2ContractError(
            "receipt masked sample count differs from frozen case"
        )
    if masked_sample_count + outside_sample_count != case.source_sample_count:
        raise E01PhoneSubstitutionV2ContractError(
            "edit mask partition does not equal the source sample count"
        )

    invariants = _exact_mapping(
        receipt["invariants"],
        expected_keys={
            "same_sample_count_u_i_t",
            "u_i_outside_edit_mask_exact_identity",
            "u_t_outside_edit_mask_exact_identity",
            "i_t_outside_edit_mask_exact_identity",
            "automatic_clipping_or_normalization_applied",
        },
        name="receipt.invariants",
    )
    for field in (
        "same_sample_count_u_i_t",
        "u_i_outside_edit_mask_exact_identity",
        "u_t_outside_edit_mask_exact_identity",
        "i_t_outside_edit_mask_exact_identity",
    ):
        _expect_exact(invariants[field], True, name=f"receipt.invariants.{field}")
    _expect_exact(
        invariants["automatic_clipping_or_normalization_applied"],
        False,
        name="receipt.invariants.automatic_clipping_or_normalization_applied",
    )

    phone_bindings = _exact_mapping(
        receipt["observed_phone_bindings"],
        expected_keys={
            "u_source_phone_label_sha256",
            "i_identity_phone_label_sha256",
            "t_target_phone_label_sha256",
            "i_keeps_u_phone",
            "t_uses_donor_phone",
        },
        name="receipt.observed_phone_bindings",
    )
    u_phone = _sha256(
        phone_bindings["u_source_phone_label_sha256"],
        name="receipt.observed_phone_bindings.u_source_phone_label_sha256",
    )
    i_phone = _sha256(
        phone_bindings["i_identity_phone_label_sha256"],
        name="receipt.observed_phone_bindings.i_identity_phone_label_sha256",
    )
    t_phone = _sha256(
        phone_bindings["t_target_phone_label_sha256"],
        name="receipt.observed_phone_bindings.t_target_phone_label_sha256",
    )
    if (
        u_phone != case.source_phone_label_sha256
        or i_phone != case.source_phone_label_sha256
        or t_phone != case.donor_phone_label_sha256
    ):
        raise E01PhoneSubstitutionV2ContractError(
            "U/I/T observed-phone role binding mismatch"
        )
    _expect_exact(
        phone_bindings["i_keeps_u_phone"],
        True,
        name="receipt.observed_phone_bindings.i_keeps_u_phone",
    )
    _expect_exact(
        phone_bindings["t_uses_donor_phone"],
        True,
        name="receipt.observed_phone_bindings.t_uses_donor_phone",
    )

    _validate_independent_verifier(receipt["independent_phone_verifier"])
    _validate_stage_separation(receipt["stage_separation"])

    relations = _exact_mapping(
        receipt["relations"],
        expected_keys=set(RELATION_ROLES),
        name="receipt.relations",
    )
    relation_statuses = (
        _validate_relation(
            relations["U_to_I"],
            relation_role="U_to_I",
            source_role="U",
            comparison_role="I",
            expectation="identity_control_invariance",
            expected_policy_sha256=case.policies.u_to_i_relation_policy_sha256,
        ),
        _validate_relation(
            relations["U_to_T"],
            relation_role="U_to_T",
            source_role="U",
            comparison_role="T",
            expectation="targeted_phone_change",
            expected_policy_sha256=case.policies.u_to_t_relation_policy_sha256,
        ),
        _validate_relation(
            relations["I_to_T"],
            relation_role="I_to_T",
            source_role="I",
            comparison_role="T",
            expectation="targeted_phone_change",
            expected_policy_sha256=case.policies.i_to_t_relation_policy_sha256,
        ),
    )
    _expect_exact(
        receipt["publication_allowed"],
        False,
        name="receipt.publication_allowed",
    )
    return ValidatedPhoneWorkerReceipt(
        case_id=case.case_id,
        relation_statuses=relation_statuses,
    )


def _validate_relation_aggregate(
    value: object,
    *,
    expected_role: str,
    case_count: int,
) -> tuple[int, int, int]:
    aggregate = _exact_mapping(
        value,
        expected_keys={
            "relation_role",
            "case_count",
            "evaluable_count",
            "passed_count",
            "failed_count",
            "invalid_count",
            "minimum_support_required_count",
            "required_pass_count",
            "pass_rate",
            "decision",
        },
        name=f"report.relation_results.{expected_role}",
    )
    _expect_exact(
        aggregate["relation_role"],
        expected_role,
        name=f"report.relation_results.{expected_role}.relation_role",
    )
    observed_case_count = _bounded_integer(
        aggregate["case_count"],
        minimum=1,
        maximum=_MAX_AGGREGATE_COUNT,
        name=f"report.relation_results.{expected_role}.case_count",
    )
    if observed_case_count != case_count:
        raise E01PhoneSubstitutionV2ContractError(
            f"report.relation_results.{expected_role}.case_count mismatch"
        )
    evaluable = _bounded_integer(
        aggregate["evaluable_count"],
        minimum=0,
        maximum=case_count,
        name=f"report.relation_results.{expected_role}.evaluable_count",
    )
    passed = _bounded_integer(
        aggregate["passed_count"],
        minimum=0,
        maximum=case_count,
        name=f"report.relation_results.{expected_role}.passed_count",
    )
    failed = _bounded_integer(
        aggregate["failed_count"],
        minimum=0,
        maximum=case_count,
        name=f"report.relation_results.{expected_role}.failed_count",
    )
    invalid = _bounded_integer(
        aggregate["invalid_count"],
        minimum=0,
        maximum=case_count,
        name=f"report.relation_results.{expected_role}.invalid_count",
    )
    if passed + failed != evaluable or evaluable + invalid != case_count:
        raise E01PhoneSubstitutionV2ContractError(
            f"report.relation_results.{expected_role} counts are inconsistent"
        )
    minimum_support = _bounded_integer(
        aggregate["minimum_support_required_count"],
        minimum=1,
        maximum=_MAX_AGGREGATE_COUNT,
        name=(
            f"report.relation_results.{expected_role}.minimum_support_required_count"
        ),
    )
    required_pass = _bounded_integer(
        aggregate["required_pass_count"],
        minimum=0,
        maximum=evaluable,
        name=f"report.relation_results.{expected_role}.required_pass_count",
    )
    if evaluable > 0 and required_pass == 0:
        raise E01PhoneSubstitutionV2ContractError(
            f"report.relation_results.{expected_role}.required_pass_count "
            "must be positive when evaluable_count is positive"
        )
    _expect_rate(
        aggregate["pass_rate"],
        numerator=passed,
        denominator=evaluable,
        name=f"report.relation_results.{expected_role}.pass_rate",
    )
    decision = _exact_string(
        aggregate["decision"],
        name=f"report.relation_results.{expected_role}.decision",
    )
    expected_decision = (
        "blocked"
        if evaluable < minimum_support
        else ("passed" if passed >= required_pass else "failed")
    )
    if decision != expected_decision:
        raise E01PhoneSubstitutionV2ContractError(
            f"report.relation_results.{expected_role}.decision "
            "does not match counts and support"
        )
    return passed, failed, invalid


def _count_sha256_values(value: object) -> list[str]:
    if type(value) is str and _SHA256_RE.fullmatch(value):
        return [value]
    if type(value) is dict:
        result: list[str] = []
        for item in cast(dict[object, object], value).values():
            result.extend(_count_sha256_values(item))
        return result
    if type(value) is list:
        result = []
        for item in cast(list[object], value):
            result.extend(_count_sha256_values(item))
        return result
    return []


def validate_phone_aggregate_report(
    payload: Mapping[str, object],
) -> ValidatedPhoneAggregateReport:
    """Validate the leak-minimized A1 report and its count arithmetic."""

    report = _exact_mapping(
        payload,
        expected_keys={
            "schema_version",
            "status",
            "privacy_zone",
            "leakage_guard",
            "private_receipt_bundle_sha256",
            "case_count",
            "relation_results",
            "overall",
            "publication_allowed",
            "paper_complete",
        },
        name="report",
    )
    _expect_exact(
        report["schema_version"],
        REPORT_SCHEMA_VERSION,
        name="report.schema_version",
    )
    _expect_exact(
        report["status"],
        "aggregate_only_completed",
        name="report.status",
    )
    _expect_exact(
        report["privacy_zone"], "A1_aggregate_only", name="report.privacy_zone"
    )
    leakage = _exact_mapping(
        report["leakage_guard"],
        expected_keys={
            "contains_row_or_case_data",
            "contains_audio_or_path_data",
            "contains_speaker_or_session_data",
            "contains_phone_or_transcript_data",
            "contains_boundary_or_mask_data",
            "contains_restricted_hashes_other_than_bundle_digest",
            "contains_free_text",
        },
        name="report.leakage_guard",
    )
    for field in leakage:
        _expect_exact(
            leakage[field],
            False,
            name=f"report.leakage_guard.{field}",
        )
    bundle_sha256 = _sha256(
        report["private_receipt_bundle_sha256"],
        name="report.private_receipt_bundle_sha256",
    )
    observed_digest_values = _count_sha256_values(report)
    if observed_digest_values != [bundle_sha256]:
        raise E01PhoneSubstitutionV2ContractError(
            "A1 report may contain only the private receipt-bundle digest"
        )
    case_count = _bounded_integer(
        report["case_count"],
        minimum=1,
        maximum=_MAX_AGGREGATE_COUNT,
        name="report.case_count",
    )
    relation_values = _exact_list(
        report["relation_results"],
        length=3,
        name="report.relation_results",
    )
    relation_status_counts = tuple(
        _validate_relation_aggregate(
            relation_values[index],
            expected_role=relation_role,
            case_count=case_count,
        )
        for index, relation_role in enumerate(RELATION_ROLES)
    )

    overall = _exact_mapping(
        report["overall"],
        expected_keys={
            "case_count",
            "relation_count",
            "evaluable_relation_count",
            "passed_relation_count",
            "failed_relation_count",
            "invalid_relation_count",
            "all_three_relations_passed_case_count",
            "any_invalid_relation_case_count",
            "pass_rate",
            "decision",
        },
        name="report.overall",
    )
    observed_overall_case_count = _bounded_integer(
        overall["case_count"],
        minimum=1,
        maximum=_MAX_AGGREGATE_COUNT,
        name="report.overall.case_count",
    )
    if observed_overall_case_count != case_count:
        raise E01PhoneSubstitutionV2ContractError("report.overall.case_count mismatch")
    relation_count = _bounded_integer(
        overall["relation_count"],
        minimum=3,
        maximum=3 * _MAX_AGGREGATE_COUNT,
        name="report.overall.relation_count",
    )
    if relation_count != 3 * case_count:
        raise E01PhoneSubstitutionV2ContractError(
            "report.overall.relation_count must equal three times case_count"
        )
    passed_total = sum(counts[0] for counts in relation_status_counts)
    failed_total = sum(counts[1] for counts in relation_status_counts)
    invalid_total = sum(counts[2] for counts in relation_status_counts)
    evaluable_total = passed_total + failed_total
    expected_count_fields = {
        "evaluable_relation_count": evaluable_total,
        "passed_relation_count": passed_total,
        "failed_relation_count": failed_total,
        "invalid_relation_count": invalid_total,
    }
    for field, expected in expected_count_fields.items():
        observed = _bounded_integer(
            overall[field],
            minimum=0,
            maximum=relation_count,
            name=f"report.overall.{field}",
        )
        if observed != expected:
            raise E01PhoneSubstitutionV2ContractError(
                f"report.overall.{field} does not match relation aggregates"
            )
    all_three_passed = _bounded_integer(
        overall["all_three_relations_passed_case_count"],
        minimum=0,
        maximum=case_count,
        name="report.overall.all_three_relations_passed_case_count",
    )
    maximum_all_three_passed = min(counts[0] for counts in relation_status_counts)
    minimum_all_three_passed = max(0, passed_total - (2 * case_count))
    if not (minimum_all_three_passed <= all_three_passed <= maximum_all_three_passed):
        raise E01PhoneSubstitutionV2ContractError(
            "report.overall.all_three_relations_passed_case_count "
            "is incompatible with relation counts"
        )
    any_invalid = _bounded_integer(
        overall["any_invalid_relation_case_count"],
        minimum=0,
        maximum=case_count,
        name="report.overall.any_invalid_relation_case_count",
    )
    minimum_any_invalid = math.ceil(invalid_total / 3)
    maximum_any_invalid = min(case_count, invalid_total)
    if not minimum_any_invalid <= any_invalid <= maximum_any_invalid:
        raise E01PhoneSubstitutionV2ContractError(
            "report.overall.any_invalid_relation_case_count "
            "is incompatible with relation counts"
        )
    _expect_rate(
        overall["pass_rate"],
        numerator=passed_total,
        denominator=evaluable_total,
        name="report.overall.pass_rate",
    )
    relation_decisions = []
    for index, relation_role in enumerate(RELATION_ROLES):
        relation = cast(dict[str, object], relation_values[index])
        relation_decisions.append(
            _exact_string(
                relation["decision"],
                name=f"report.relation_results.{relation_role}.decision",
            )
        )
    expected_decision = (
        "blocked"
        if "blocked" in relation_decisions
        else ("failed" if "failed" in relation_decisions else "passed")
    )
    decision = _exact_string(overall["decision"], name="report.overall.decision")
    if decision != expected_decision:
        raise E01PhoneSubstitutionV2ContractError(
            "report.overall.decision does not match relation decisions"
        )
    _expect_exact(
        report["publication_allowed"],
        False,
        name="report.publication_allowed",
    )
    _expect_exact(report["paper_complete"], False, name="report.paper_complete")

    return ValidatedPhoneAggregateReport(
        private_receipt_bundle_sha256=bundle_sha256,
        case_count=case_count,
        relation_status_counts=relation_status_counts,
        all_three_relations_passed_case_count=all_three_passed,
        any_invalid_relation_case_count=any_invalid,
        decision=decision,
    )


def _private_receipt_bundle_sha256(
    receipt_payloads_by_case_id: Sequence[tuple[str, Mapping[str, object]]],
) -> str:
    ordered_receipts = [
        receipt
        for _, receipt in sorted(
            receipt_payloads_by_case_id,
            key=lambda item: item[0],
        )
    ]
    bundle: dict[str, object] = {
        "schema_version": RECEIPT_BUNDLE_SCHEMA_VERSION,
        "receipts": ordered_receipts,
    }
    return _payload_sha256(bundle)


def validate_phone_bundle(
    *,
    case_payloads: Sequence[Mapping[str, object]],
    receipt_payloads: Sequence[Mapping[str, object]],
    aggregate_report_payload: Mapping[str, object],
) -> ValidatedPhoneBundle:
    """Cross-check private cases/receipts against one leak-minimized report."""

    if type(case_payloads) is not list or not case_payloads:
        raise E01PhoneSubstitutionV2ContractError(
            "case_payloads must be a non-empty exact list"
        )
    if type(receipt_payloads) is not list or not receipt_payloads:
        raise E01PhoneSubstitutionV2ContractError(
            "receipt_payloads must be a non-empty exact list"
        )
    validated_cases: dict[str, ValidatedPhoneCase] = {}
    for payload in case_payloads:
        validated = validate_phone_case_sidecar(payload)
        if validated.case_id in validated_cases:
            raise E01PhoneSubstitutionV2ContractError("duplicate private case_id")
        validated_cases[validated.case_id] = validated
    if len(receipt_payloads) != len(validated_cases):
        raise E01PhoneSubstitutionV2ContractError(
            "private case and receipt counts differ"
        )

    validated_receipts: dict[str, ValidatedPhoneWorkerReceipt] = {}
    digest_inputs: list[tuple[str, Mapping[str, object]]] = []
    for payload in receipt_payloads:
        receipt_mapping = _exact_mapping(
            payload,
            expected_keys={
                "schema_version",
                "status",
                "privacy_zone",
                "access_scope",
                "case_id",
                "case_sidecar_sha256",
                "worker_implementation_sha256",
                "runtime_identity_sha256",
                "policy_bindings",
                "role_outputs",
                "edit_mask",
                "invariants",
                "observed_phone_bindings",
                "independent_phone_verifier",
                "stage_separation",
                "relations",
                "publication_allowed",
            },
            name="receipt",
        )
        case_id = _exact_string(receipt_mapping["case_id"], name="receipt.case_id")
        if case_id not in validated_cases:
            raise E01PhoneSubstitutionV2ContractError(
                "receipt has no matching private case"
            )
        if case_id in validated_receipts:
            raise E01PhoneSubstitutionV2ContractError(
                "duplicate private receipt case_id"
            )
        validated_receipts[case_id] = validate_phone_worker_receipt(
            receipt_mapping,
            case=validated_cases[case_id],
        )
        digest_inputs.append((case_id, receipt_mapping))
    if set(validated_receipts) != set(validated_cases):
        raise E01PhoneSubstitutionV2ContractError(
            "not every private case has exactly one receipt"
        )

    report = validate_phone_aggregate_report(aggregate_report_payload)
    if report.case_count != len(validated_cases):
        raise E01PhoneSubstitutionV2ContractError(
            "A1 case count differs from private receipt count"
        )
    bundle_sha256 = _private_receipt_bundle_sha256(digest_inputs)
    if report.private_receipt_bundle_sha256 != bundle_sha256:
        raise E01PhoneSubstitutionV2ContractError(
            "A1 private receipt-bundle digest mismatch"
        )

    derived_relation_counts: list[tuple[int, int, int]] = []
    for relation_index in range(3):
        statuses = [
            receipt.relation_statuses[relation_index]
            for receipt in validated_receipts.values()
        ]
        derived_relation_counts.append(
            (
                statuses.count("passed"),
                statuses.count("failed"),
                statuses.count("invalid"),
            )
        )
    if tuple(derived_relation_counts) != report.relation_status_counts:
        raise E01PhoneSubstitutionV2ContractError(
            "A1 relation counts differ from private receipts"
        )

    all_three_passed = sum(
        receipt.relation_statuses == ("passed", "passed", "passed")
        for receipt in validated_receipts.values()
    )
    any_invalid = sum(
        "invalid" in receipt.relation_statuses
        for receipt in validated_receipts.values()
    )
    if (
        all_three_passed != report.all_three_relations_passed_case_count
        or any_invalid != report.any_invalid_relation_case_count
    ):
        raise E01PhoneSubstitutionV2ContractError(
            "A1 cross-relation case counts differ from private receipts"
        )

    return ValidatedPhoneBundle(
        case_count=len(validated_cases),
        private_receipt_bundle_sha256=bundle_sha256,
        decision=report.decision,
    )
