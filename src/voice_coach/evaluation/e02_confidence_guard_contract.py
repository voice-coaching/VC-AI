"""Fail-closed E02 confidence-boundary API guard contract.

The package validates static schemas and, in a future authorized run, immutable
S107 and C02 predecessor evidence.  It never copies a numeric S107 threshold
into E02 configuration, reads an E02 request, invokes an API, delivers advice
or references, calls a model/GPU/network/LLM, computes a metric, writes an
artifact, or claims deployment/completion.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from types import ModuleType
from typing import Any

import yaml  # type: ignore[import-untyped]


CONFIG_SCHEMA_VERSION = 1
STATIC_STATUS = "production_static_preflight_only"
TBD_S107_COMPLETION = "TBD_S107_COMPLETION"
TBD_C02_COMPLETION = "TBD_C02_COMPLETION"
TBD_E02_PREREGISTRATION = "TBD_E02_PREREGISTRATION"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
UTC_RE = re.compile(
    r"^20[0-9]{2}-(0[1-9]|1[0-2])-([0-2][0-9]|3[01])"
    r"T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$"
)

BLOCK_REASONS = (
    "out_of_domain",
    "low_confidence",
    "unsupported",
    "abstain_all",
)
SAFE_NEXT_ACTIONS = ("rerecord", "expert_review")

E02_STATIC_CONTRACT_SOURCE_PATHS = {
    "configs/coaching/e02_confidence_guard_input_v1.schema.json",
    "configs/coaching/e02_confidence_guard_api_response_v1.schema.json",
    "configs/coaching/e02_c02_coaching_bundle_commit_v1.schema.json",
    "configs/coaching/e02_confidence_guard_preregistration_v1.template.json",
    "configs/experiments/c02_deterministic_coaching_selection_v1.yaml",
    "configs/coaching/c02_diagnostic_issues_input_v1.schema.json",
    "configs/coaching/c02_coaching_selection_output_v1.schema.json",
    "src/voice_coach/coaching/c02_selection_contract.py",
    "configs/experiments/wavlm_s107_calibration_v1.yaml",
    "src/voice_coach/training/wavlm_s107_calibration_contract.py",
    "src/voice_coach/training/wavlm_s107_calibration_training.py",
}

SEUNGUN_AUDIO_CANONICAL_SOURCE_PATHS = {
    "seungun/README.md",
    "seungun/docs/CURRENT_PIPELINE_KO.md.md",
    "seungun/docs/WORK_LOG_AND_FINDINGS_KO.md",
    "seungun/docs/PIPELINE_RELIABILITY_IMPROVEMENT_PROPOSAL_KO.md",
    "seungun/configs/textgrid_target_policy.yaml",
    "seungun/configs/xlsr300m_full.yaml",
    "seungun/src/korean_phone_ctc/frozen_detector.py",
}

DATA_PRIVACY_BOUNDARY_SOURCE_PATHS = {"data/README.md"}

API_CONTRACT_SOURCE_PATHS = {
    "docs/api/endpoints.md",
    "docs/api/specification.md",
}

VISUAL_SIDECAR_ONLY_SOURCE_PATHS = {
    "docs/architecture/README_KO.md",
    "docs/architecture/architecture.md",
    "docs/architecture/component.md",
    "docs/architecture/directory.md",
    "docs/architecture/flow.md",
    "docs/architecture/state.md",
}

EXPECTED_SOURCE_SCOPES = {
    **dict.fromkeys(E02_STATIC_CONTRACT_SOURCE_PATHS, "e02_static_contract"),
    **dict.fromkeys(
        SEUNGUN_AUDIO_CANONICAL_SOURCE_PATHS,
        "seungun_audio_canonical",
    ),
    **dict.fromkeys(DATA_PRIVACY_BOUNDARY_SOURCE_PATHS, "data_privacy_boundary"),
    **dict.fromkeys(API_CONTRACT_SOURCE_PATHS, "api_contract"),
    **dict.fromkeys(VISUAL_SIDECAR_ONLY_SOURCE_PATHS, "visual_sidecar_only"),
}
SOURCE_PATHS = tuple(EXPECTED_SOURCE_SCOPES)

EXPECTED_EXECUTION_ORDER = (
    "validate_e02_static_config_sources_schemas_and_fail_closed_template",
    "require_exact_s107_completion_and_threshold_pins_before_path_resolution",
    "deep_validate_actual_s107_completion_and_threshold_without_copying_numeric_threshold",
    "require_exact_c02_completed_bundle_pin_before_path_resolution",
    "deep_recompute_actual_c02_deterministic_bundle_and_no_backfill_free_generation_guards",
    "validate_frozen_e02_ood_threshold_support_abstain_combination_policy",
    "stop_before_guard_input_request_api_coaching_reference_model_metric_or_output",
)

TOP_LEVEL_CONFIG_KEYS = {
    "schema_version",
    "experiment",
    "canonical_sources",
    "predecessor_s107",
    "predecessor_c02",
    "preregistration",
    "guard_contract",
    "api_privacy",
    "non_execution",
    "execution_order",
    "outputs",
}
CONFIG_SECTION_KEYS = {
    "experiment": {
        "id",
        "stage",
        "implementation_status",
        "actual_request_access_allowed",
        "actual_api_execution_allowed",
        "actual_coaching_or_reference_delivery_allowed",
        "actual_model_or_metric_execution_allowed",
        "actual_output_write_allowed",
        "production_release_allowed",
        "completion_claim_allowed",
    },
    "canonical_sources": {"access_scope", "pins"},
    "predecessor_s107": {
        "deep_validation_required",
        "actual_completed_output_required",
        "config_path",
        "config_sha256",
        "contract_path",
        "contract_sha256",
        "completion_validator_path",
        "completion_validator_sha256",
        "completion_validator_symbol",
        "completion_commit_path",
        "completion_commit_sha256",
        "required_commit_schema_version",
        "required_commit_status",
        "required_deep_status",
        "required_label_status",
        "required_registry_status",
        "threshold_artifact_name",
        "threshold_artifact_relative_path",
        "threshold_artifact_schema_version",
        "threshold_artifact_sha256",
        "threshold_policy_status",
        "required_threshold_selection_status",
        "numeric_threshold_copied_into_e02_config",
        "threshold_source",
        "abstain_all_selection_status_prohibited",
    },
    "predecessor_c02": {
        "deep_validation_required",
        "actual_completed_bundle_required",
        "config_path",
        "config_sha256",
        "contract_path",
        "contract_sha256",
        "bundle_schema_path",
        "bundle_schema_sha256",
        "completion_commit_path",
        "completion_commit_sha256",
        "required_commit_schema_version",
        "required_commit_status",
        "required_registry_schema_version",
        "required_registry_status",
        "required_diagnostic_input_schema_version",
        "required_selection_output_schema_version",
        "deterministic_recomputation_required",
        "blocked_top_k_backfill_allowed",
        "free_generation_llm_allowed",
        "prompt_or_completion_allowed",
        "network_access_allowed",
        "runtime_model_access_allowed",
        "gpu_access_allowed",
        "reference_generation_allowed",
        "production_release_allowed",
    },
    "preregistration": {
        "placeholder",
        "template_path",
        "template_sha256",
        "approved_artifact_path",
        "approved_artifact_sha256",
        "required_schema_version",
        "required_status",
        "must_be_frozen_before_guard_input_or_api_access",
        "unresolved_fields_must_fail_closed",
    },
    "guard_contract": {
        "automatic_block_conditions",
        "blocked_reason_precedence",
        "mixed_reason_resolution_id",
        "allowed_blocked_safe_next_actions",
        "threshold_source",
        "numeric_threshold_in_config_allowed",
        "ood_definition_id",
        "ood_threshold_or_rule_policy_sha256",
        "unsupported_definition_id",
        "abstain_all_definition_id",
        "blocked_automatic_coaching_advice_allowed",
        "blocked_automatic_reference_allowed",
        "blocked_automatic_corrective_action_allowed",
        "c02_blocked_top_k_backfill_allowed",
        "c02_free_generation_allowed",
        "pass_through_requires_all_registered_gates",
    },
    "api_privacy": {
        "response_schema_path",
        "response_schema_sha256",
        "input_schema_path",
        "input_schema_sha256",
        "blocked_reason_codes_allowed",
        "evidence_provenance_hashes_allowed",
        "safe_next_action_code_allowed",
        "model_score_allowed",
        "pii_allowed",
        "raw_path_allowed",
        "free_text_allowed",
        "advice_reference_or_corrective_payload_allowed_when_blocked",
        "forbidden_recursive_fields",
    },
    "non_execution": {
        "request_or_row_access_allowed",
        "api_invocation_allowed",
        "coaching_advice_delivery_allowed",
        "reference_delivery_allowed",
        "corrective_action_delivery_allowed",
        "model_access_allowed",
        "gpu_access_allowed",
        "metric_computation_allowed",
        "network_access_allowed",
        "llm_access_allowed",
        "free_generation_allowed",
        "output_write_allowed",
        "registry_or_commit_write_allowed",
        "production_or_publication_allowed",
    },
    "outputs": {
        "guard_input_schema_path",
        "api_response_schema_path",
        "guard_decision_path",
        "aggregate_report_path",
        "registry_path",
        "commit_path",
        "failure_report_path",
        "no_overwrite",
        "atomic_publish_required",
        "write_enabled_in_this_package",
    },
}

INPUT_KEYS = {
    "schema_version",
    "guard_input_id",
    "privacy_zone",
    "request_binding_sha256",
    "lineage",
    "assessments",
    "evidence_provenance",
    "inherited_safety",
    "binding_sha256",
}
INPUT_LINEAGE_KEYS = {
    "s107_completion_commit_sha256",
    "s107_threshold_policy_report_sha256",
    "c02_completion_commit_sha256",
    "c02_preregistration_sha256",
    "c02_diagnostic_input_sha256",
    "c02_selection_output_sha256",
    "e02_preregistration_sha256",
}
ASSESSMENT_KEYS = {
    "domain_status",
    "confidence_status",
    "support_status",
    "c02_selection_status",
    "c02_abstain_all",
}
PROVENANCE_KEYS = {
    "domain_assessment_receipt_sha256",
    "confidence_assessment_receipt_sha256",
    "support_assessment_receipt_sha256",
    "c02_selection_receipt_sha256",
}
INHERITED_SAFETY_KEYS = {
    "c02_blocked_top_k_no_backfill",
    "c02_free_generation_used",
    "c02_network_used",
    "c02_runtime_model_used",
    "automatic_advice_payload_present",
    "automatic_reference_payload_present",
    "automatic_corrective_action_payload_present",
}

RESPONSE_KEYS = {
    "schema_version",
    "response_id",
    "status",
    "privacy_zone",
    "request_binding_sha256",
    "lineage",
    "blocked",
    "blocked_reasons",
    "safe_next_action",
    "capability_decision",
    "evidence_provenance",
    "safety_receipt",
    "binding_sha256",
}
RESPONSE_LINEAGE_KEYS = {
    "guard_input_sha256",
    "s107_completion_commit_sha256",
    "s107_threshold_policy_report_sha256",
    "c02_completion_commit_sha256",
    "c02_selection_output_sha256",
    "e02_preregistration_sha256",
}
CAPABILITY_KEYS = {
    "automatic_coaching_advice_allowed",
    "automatic_reference_allowed",
    "automatic_corrective_action_allowed",
    "separate_renderer_required",
}
SAFETY_RECEIPT_KEYS = {
    "c02_no_backfill_inherited",
    "c02_free_generation_prohibited",
    "model_score_present",
    "pii_present",
    "raw_path_present",
    "free_text_present",
    "advice_payload_present",
    "reference_payload_present",
    "corrective_action_payload_present",
    "api_executed_by_static_package",
    "production_release_allowed",
}

C02_COMMIT_KEYS = {
    "schema_version",
    "bundle_id",
    "status",
    "privacy_zone",
    "c02_config",
    "s107",
    "preregistration",
    "diagnostic_input",
    "selection_output",
    "registry",
    "assurance",
    "binding_sha256",
}
FILE_RECORD_KEYS = {"path", "sha256", "size_bytes"}
REGISTRY_RECORD_KEYS = {"path", "sha256", "size_bytes", "status"}
C02_S107_KEYS = {
    "completion_commit_sha256",
    "threshold_policy_report_sha256",
}
C02_ASSURANCE_KEYS = {
    "actual_c02_selection_completed",
    "deterministic_recomputation_required",
    "blocked_top_k_backfill_allowed",
    "free_generation_llm_used",
    "prompt_or_completion_present",
    "network_accessed",
    "runtime_model_accessed",
    "gpu_accessed",
    "reference_generation_executed",
    "production_release_allowed",
}
C02_REGISTRY_KEYS = {
    "schema_version",
    "status",
    "c02_config_sha256",
    "c02_preregistration_sha256",
    "diagnostic_input_sha256",
    "selection_output_sha256",
    "s107_completion_commit_sha256",
    "s107_threshold_policy_report_sha256",
    "deterministic_catalog_only",
    "blocked_top_k_backfill_allowed",
    "free_generation_llm_used",
    "prompt_or_completion_present",
    "network_accessed",
    "runtime_model_accessed",
    "gpu_accessed",
    "reference_generation_executed",
    "production_release_allowed",
    "binding_sha256",
}

PREREG_KEYS = {
    "schema_version",
    "preregistration_id",
    "status",
    "registered_at_utc",
    "registered_by_role",
    "frozen_before_guard_input_or_api_access",
    "lineage",
    "domain_policy",
    "confidence_policy",
    "support_and_abstention_policy",
    "combination_policy",
    "api_privacy_policy",
    "approval",
    "binding_sha256",
}
PREREG_SECTION_KEYS = {
    "lineage": {
        "s107_completion_commit_sha256",
        "s107_threshold_policy_report_sha256",
        "c02_completion_commit_sha256",
        "c02_preregistration_sha256",
    },
    "domain_policy": {
        "ood_definition_id",
        "ood_definition_policy_sha256",
        "ood_threshold_or_rule_policy_sha256",
        "domain_assessment_receipt_required",
        "out_of_domain_blocks_all_automatic_correction",
    },
    "confidence_policy": {
        "threshold_source",
        "numeric_threshold_copied_into_e02",
        "threshold_policy_report_sha256",
        "confidence_assessment_contract_sha256",
        "below_threshold_blocks_all_automatic_correction",
    },
    "support_and_abstention_policy": {
        "unsupported_definition_id",
        "unsupported_policy_sha256",
        "abstain_all_definition_id",
        "abstain_all_policy_sha256",
        "unsupported_blocks_all_automatic_correction",
        "abstain_all_blocks_all_automatic_correction",
        "c02_blocked_top_k_backfill_allowed",
        "c02_free_generation_allowed",
    },
    "combination_policy": {
        "blocked_reason_precedence",
        "safe_next_action_by_reason",
        "mixed_reason_resolution_id",
        "allowed_blocked_safe_next_actions",
    },
    "api_privacy_policy": {
        "blocked_reason_codes_allowed",
        "evidence_provenance_hashes_allowed",
        "safe_next_action_code_allowed",
        "model_score_allowed",
        "pii_allowed",
        "raw_path_allowed",
        "free_text_allowed",
        "advice_payload_allowed_when_blocked",
        "reference_payload_allowed_when_blocked",
        "corrective_action_payload_allowed_when_blocked",
    },
    "approval": {"decision", "protocol_sha256", "signed_receipt_sha256"},
}

FORBIDDEN_API_FIELDS = {
    "name",
    "email",
    "phone",
    "address",
    "legal_name",
    "learner_id",
    "participant_id",
    "raw_attempt_id",
    "target_text",
    "transcript",
    "audio_path",
    "source_path",
    "model_path",
    "filename",
    "file_name",
    "free_text",
    "comment",
    "prompt",
    "completion",
    "generated_text",
    "llm_response",
    "score",
    "model_score",
    "confidence",
    "confidence_score",
    "probability",
    "logit",
    "threshold",
    "ece",
    "brier",
    "message",
    "message_ko",
    "advice",
    "reference",
    "corrective_action",
}


class E02ContractError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        stage: str = "static_contract",
        audit: AccessAudit | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.audit = audit


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise E02ContractError("YAML mapping keys must be strings")
        if key in result:
            raise E02ContractError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass
class AccessAudit:
    source_pin_read_count: int = 0
    source_toctou_verification_count: int = 0
    s107_completion_path_resolution_count: int = 0
    s107_completion_read_attempt_count: int = 0
    s107_completion_read_count: int = 0
    s107_validator_load_count: int = 0
    s107_deep_validation_count: int = 0
    s107_calibration_evidence_read_count: int = 0
    s107_threshold_report_read_count: int = 0
    s107_registry_read_count: int = 0
    c02_completion_path_resolution_count: int = 0
    c02_completion_read_attempt_count: int = 0
    c02_completion_read_count: int = 0
    c02_contract_load_count: int = 0
    c02_static_source_read_count: int = 0
    c02_c01_deep_validation_count: int = 0
    c02_preregistration_read_count: int = 0
    c02_diagnostic_input_read_count: int = 0
    c02_selection_output_read_count: int = 0
    c02_registry_read_count: int = 0
    c02_deep_recomputation_count: int = 0
    e02_preregistration_path_resolution_count: int = 0
    e02_preregistration_read_attempt_count: int = 0
    e02_preregistration_read_count: int = 0
    guard_input_path_resolution_count: int = 0
    guard_input_read_count: int = 0
    request_or_row_access_count: int = 0
    api_invocation_count: int = 0
    coaching_advice_delivery_count: int = 0
    reference_delivery_count: int = 0
    corrective_action_delivery_count: int = 0
    model_access_count: int = 0
    gpu_access_count: int = 0
    metric_computation_count: int = 0
    network_access_count: int = 0
    llm_access_count: int = 0
    free_generation_count: int = 0
    output_path_resolution_count: int = 0
    output_count: int = 0
    registry_write_count: int = 0
    commit_write_count: int = 0
    publication_count: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            key: int(value)
            for key, value in vars(self).items()
            if isinstance(value, int)
        }


@dataclass(frozen=True)
class FileSnapshot:
    relative_path: str
    sha256: str
    size: int
    device: int
    inode: int
    mtime_ns: int


@dataclass(frozen=True)
class StaticValidation:
    config_sha256: str
    policy_sha256: str
    source_snapshots: tuple[FileSnapshot, ...]


@dataclass(frozen=True)
class S107Evidence:
    commit_sha256: str
    threshold_report_sha256: str
    threshold_selection_status: str
    c02_contract_module: ModuleType
    c02_s107_evidence: Any


@dataclass(frozen=True)
class C02Evidence:
    commit_sha256: str
    config_sha256: str
    preregistration_sha256: str
    diagnostic_input_sha256: str
    selection_output_sha256: str
    registry_sha256: str
    selection_output: Mapping[str, Any]
    snapshots: tuple[FileSnapshot, ...]


@dataclass(frozen=True)
class E02Policy:
    sha256: str
    blocked_reason_precedence: tuple[str, ...]
    safe_next_action_by_reason: Mapping[str, str]
    mixed_reason_resolution_id: str
    payload: Mapping[str, Any]
    snapshot: FileSnapshot | None


@dataclass(frozen=True)
class PreflightResult:
    status: str
    config_sha256: str
    s107_commit_sha256: str
    s107_threshold_report_sha256: str
    c02_completion_commit_sha256: str
    c02_selection_output_sha256: str
    e02_preregistration_sha256: str
    audit: Mapping[str, int]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise E02ContractError(f"{name} must be a string-keyed mapping")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise E02ContractError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise E02ContractError(f"{name} must be a non-empty string")
    return value


def _integer(value: object, name: str, *, minimum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise E02ContractError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise E02ContractError(f"{name} must be >= {minimum}")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise E02ContractError(f"{name} must be boolean")
    return value


def _sha256(value: object, name: str) -> str:
    text = _string(value, name)
    if not SHA256_RE.fullmatch(text):
        raise E02ContractError(f"{name} must be a lowercase SHA-256")
    return text


def _safe_id(value: object, name: str) -> str:
    text = _string(value, name)
    if not SAFE_ID_RE.fullmatch(text):
        raise E02ContractError(f"{name} must be a bounded deterministic ID")
    return text


def _utc(value: object, name: str) -> str:
    text = _string(value, name)
    if not UTC_RE.fullmatch(text):
        raise E02ContractError(f"{name} must be second-precision UTC")
    try:
        parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise E02ContractError(f"{name} must be a valid UTC timestamp") from exc
    if parsed > datetime.now(timezone.utc):
        raise E02ContractError(f"{name} cannot be in the future")
    return text


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise E02ContractError(f"{name} drift: expected {expected!r}, got {actual!r}")


def _expect_keys(
    value: Mapping[str, Any],
    expected: set[str],
    name: str,
) -> None:
    actual = set(value)
    if actual != expected:
        raise E02ContractError(
            f"{name} keys drift; "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )


def _reject_nonfinite_tree(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise E02ContractError(f"{name} contains non-finite number")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_nonfinite_tree(item, f"{name}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_nonfinite_tree(item, f"{name}[{index}]")


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise E02ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise E02ContractError(f"non-finite JSON constant prohibited: {value}")


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    _reject_nonfinite_tree(value, "payload")
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def binding_sha256(value: Mapping[str, Any]) -> str:
    without_binding = {
        key: item for key, item in value.items() if key != "binding_sha256"
    }
    return hashlib.sha256(canonical_json_bytes(without_binding)).hexdigest()


def _contains_tbd(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith("TBD_")
    if isinstance(value, Mapping):
        return any(_contains_tbd(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_tbd(item) for item in value)
    return False


def _collect_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            keys.add(str(key))
            keys.update(_collect_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_collect_keys(item))
    return keys


def _reject_api_leakage(value: Mapping[str, Any], name: str) -> None:
    forbidden = _collect_keys(value).intersection(FORBIDDEN_API_FIELDS)
    if forbidden:
        raise E02ContractError(
            f"{name} contains score/PII/raw-path/free-text/payload fields: "
            f"{sorted(forbidden)}"
        )


def _relative_string(value: object, name: str) -> str:
    text = _string(value, name)
    pure = PurePosixPath(text)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in ("", ".", "..") for part in pure.parts)
        or "\\" in text
    ):
        raise E02ContractError(f"{name} must be a safe workspace-relative path")
    return text


def _reject_symlink_chain(
    root: Path,
    relative: str,
    name: str,
    *,
    require_leaf: bool,
) -> Path:
    relative = _relative_string(relative, name)
    current = root
    parts = PurePosixPath(relative).parts
    for index, part in enumerate(parts):
        current = current / part
        leaf = index == len(parts) - 1
        try:
            info = current.lstat()
        except FileNotFoundError:
            if require_leaf:
                raise E02ContractError(f"{name} missing: {relative}") from None
            return root / relative
        if stat.S_ISLNK(info.st_mode):
            raise E02ContractError(f"{name} symlink prohibited: {relative}")
        if not leaf and not stat.S_ISDIR(info.st_mode):
            raise E02ContractError(f"{name} parent is not a directory: {relative}")
        if leaf and require_leaf and not stat.S_ISREG(info.st_mode):
            raise E02ContractError(f"{name} must be a regular file: {relative}")
    return current


def _read_file(
    root: Path,
    relative: str,
    name: str,
) -> tuple[bytes, FileSnapshot]:
    path = _reject_symlink_chain(root, relative, name, require_leaf=True)
    before = path.stat(follow_symlinks=False)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise E02ContractError(f"{name} is not a regular file")
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise E02ContractError(f"{name} changed before open")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise E02ContractError(f"{name} changed while reading")
    data = b"".join(chunks)
    return data, FileSnapshot(
        relative_path=relative,
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        device=after.st_dev,
        inode=after.st_ino,
        mtime_ns=after.st_mtime_ns,
    )


def _verify_snapshot(root: Path, snapshot: FileSnapshot, name: str) -> None:
    _, current = _read_file(root, snapshot.relative_path, name)
    if current != snapshot:
        raise E02ContractError(
            f"{name} immutable snapshot drift: {snapshot.relative_path}"
        )


def _load_json_bytes(data: bytes, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_json_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise E02ContractError(f"{name} invalid JSON: {exc}") from exc
    payload = _mapping(value, name)
    _reject_nonfinite_tree(payload, name)
    return payload


def _load_config_bytes(data: bytes) -> Mapping[str, Any]:
    try:
        value = yaml.load(data.decode("utf-8"), Loader=_UniqueKeyLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise E02ContractError(f"invalid YAML config: {exc}") from exc
    payload = _mapping(value, "config")
    _reject_nonfinite_tree(payload, "config")
    return payload


def load_config(path: Path) -> Mapping[str, Any]:
    try:
        return _load_config_bytes(path.read_bytes())
    except OSError as exc:
        raise E02ContractError(f"cannot read config {path}: {exc}") from exc


def _read_exact_json(
    *,
    workspace_root: Path,
    relative: str,
    expected_sha256: str,
    name: str,
) -> tuple[Mapping[str, Any], FileSnapshot]:
    data, snapshot = _read_file(workspace_root, relative, name)
    if snapshot.sha256 != _sha256(expected_sha256, f"{name} expected SHA"):
        raise E02ContractError(f"{name} SHA-256 drift")
    return _load_json_bytes(data, name), snapshot


def _load_module(
    *,
    workspace_root: Path,
    relative: str,
    expected_sha256: str,
    module_name: str,
) -> ModuleType:
    path = _reject_symlink_chain(
        workspace_root,
        relative,
        module_name,
        require_leaf=True,
    )
    data, snapshot = _read_file(workspace_root, relative, module_name)
    if snapshot.sha256 != _sha256(expected_sha256, f"{module_name} SHA"):
        raise E02ContractError(f"{module_name} SHA-256 drift")
    module = ModuleType(module_name)
    module.__file__ = str(path)
    module.__package__ = ""
    sys.modules[module_name] = module
    try:
        exec(compile(data, str(path), "exec"), module.__dict__)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise E02ContractError(
            f"cannot import pinned module {relative}: {exc}"
        ) from exc
    return module


def _verify_snapshots(
    *,
    workspace_root: Path,
    snapshots: Sequence[FileSnapshot],
    audit: AccessAudit,
    stage: str,
) -> None:
    for snapshot in snapshots:
        try:
            _verify_snapshot(workspace_root, snapshot, stage)
        except E02ContractError as exc:
            raise E02ContractError(
                str(exc),
                stage=f"{stage}_toctou",
                audit=audit,
            ) from exc
        audit.source_toctou_verification_count += 1


def _validate_schema_shape(
    schema: Mapping[str, Any],
    *,
    root_keys: set[str],
    definitions: Mapping[str, set[str]],
    name: str,
) -> None:
    _expect(
        schema.get("$schema"),
        "https://json-schema.org/draft/2020-12/schema",
        f"{name} dialect",
    )
    _expect(schema.get("type"), "object", f"{name} root type")
    _expect(schema.get("additionalProperties"), False, f"{name} strictness")
    _expect(
        set(_list(schema.get("required"), f"{name} required")),
        root_keys,
        f"{name} required",
    )
    _expect(
        set(_mapping(schema.get("properties"), f"{name} properties")),
        root_keys,
        f"{name} properties",
    )
    defs = _mapping(schema.get("$defs"), f"{name} definitions")
    for definition, keys in definitions.items():
        value = _mapping(defs.get(definition), f"{name}.{definition}")
        _expect(
            value.get("additionalProperties"),
            False,
            f"{name}.{definition} strictness",
        )
        _expect(
            set(_list(value.get("required"), f"{name}.{definition} required")),
            keys,
            f"{name}.{definition} required",
        )
        _expect(
            set(
                _mapping(
                    value.get("properties"),
                    f"{name}.{definition} properties",
                )
            ),
            keys,
            f"{name}.{definition} properties",
        )


def _validate_input_schema(schema: Mapping[str, Any]) -> None:
    _validate_schema_shape(
        schema,
        root_keys=INPUT_KEYS,
        definitions={
            "lineage": INPUT_LINEAGE_KEYS,
            "assessments": ASSESSMENT_KEYS,
            "evidenceProvenance": PROVENANCE_KEYS,
            "inheritedSafety": INHERITED_SAFETY_KEYS,
        },
        name="E02 input schema",
    )


def _validate_response_schema(schema: Mapping[str, Any]) -> None:
    _validate_schema_shape(
        schema,
        root_keys=RESPONSE_KEYS,
        definitions={
            "lineage": RESPONSE_LINEAGE_KEYS,
            "capabilityDecision": CAPABILITY_KEYS,
            "evidenceProvenance": PROVENANCE_KEYS,
            "safetyReceipt": SAFETY_RECEIPT_KEYS,
        },
        name="E02 response schema",
    )


def _validate_c02_bundle_schema(schema: Mapping[str, Any]) -> None:
    _validate_schema_shape(
        schema,
        root_keys=C02_COMMIT_KEYS,
        definitions={
            "fileRecord": FILE_RECORD_KEYS,
            "registryRecord": REGISTRY_RECORD_KEYS,
            "s107": C02_S107_KEYS,
            "assurance": C02_ASSURANCE_KEYS,
        },
        name="E02 C02 bundle schema",
    )


def _validate_preregistration_structure(
    payload: Mapping[str, Any],
    *,
    name: str,
) -> None:
    _reject_nonfinite_tree(payload, name)
    _reject_api_leakage(payload, name)
    _expect_keys(payload, PREREG_KEYS, name)
    for section_name, keys in PREREG_SECTION_KEYS.items():
        section = _mapping(payload.get(section_name), f"{name}.{section_name}")
        _expect_keys(section, keys, f"{name}.{section_name}")


def _validate_preregistration_template(payload: Mapping[str, Any]) -> None:
    _validate_preregistration_structure(payload, name="E02 preregistration template")
    _expect(
        payload.get("schema_version"),
        "voice_coach.e02_confidence_guard_preregistration.v1",
        "E02 preregistration template schema",
    )
    _expect(
        payload.get("preregistration_id"),
        TBD_E02_PREREGISTRATION,
        "E02 preregistration template ID",
    )
    _expect(
        payload.get("frozen_before_guard_input_or_api_access"),
        False,
        "E02 preregistration template freeze",
    )
    confidence = _mapping(
        payload.get("confidence_policy"),
        "E02 template confidence policy",
    )
    _expect(
        confidence.get("threshold_source"),
        "deep_validated_actual_s107_threshold_policy_only",
        "E02 template threshold source",
    )
    _expect(
        confidence.get("numeric_threshold_copied_into_e02"),
        False,
        "E02 template numeric threshold copy",
    )
    support = _mapping(
        payload.get("support_and_abstention_policy"),
        "E02 template support policy",
    )
    _expect(
        support.get("c02_blocked_top_k_backfill_allowed"),
        False,
        "E02 template no-backfill",
    )
    _expect(
        support.get("c02_free_generation_allowed"),
        False,
        "E02 template free-generation",
    )
    if not _contains_tbd(payload):
        raise E02ContractError("E02 preregistration template must retain TBD values")


def _validate_config_values(config: Mapping[str, Any]) -> None:
    _expect_keys(config, TOP_LEVEL_CONFIG_KEYS, "E02 config")
    _expect(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "config schema")
    for section_name, keys in CONFIG_SECTION_KEYS.items():
        _expect_keys(
            _mapping(config.get(section_name), section_name),
            keys,
            section_name,
        )

    experiment = _mapping(config.get("experiment"), "experiment")
    _expect(experiment.get("id"), "e02_confidence_guard_v1", "experiment ID")
    _expect(
        experiment.get("stage"),
        "wp8_e02_confidence_outside_automatic_correction_guard",
        "experiment stage",
    )
    _expect(
        experiment.get("implementation_status"),
        STATIC_STATUS,
        "implementation status",
    )
    for field in CONFIG_SECTION_KEYS["experiment"] - {
        "id",
        "stage",
        "implementation_status",
    }:
        _expect(experiment.get(field), False, f"experiment.{field}")

    canonical = _mapping(config.get("canonical_sources"), "canonical sources")
    _expect(
        canonical.get("access_scope"),
        "C1_public_or_static_contract_pins_only",
        "canonical source scope",
    )
    pins = _list(canonical.get("pins"), "canonical source pins")
    if len(pins) != len(SOURCE_PATHS):
        raise E02ContractError("canonical source pin count drift")
    seen: set[str] = set()
    for index, raw_pin in enumerate(pins):
        pin = _mapping(raw_pin, f"source pin {index}")
        _expect_keys(pin, {"path", "scope", "sha256"}, f"source pin {index}")
        relative = _relative_string(pin.get("path"), f"source pin {index} path")
        if relative in seen:
            raise E02ContractError(f"duplicate source pin: {relative}")
        seen.add(relative)
        _expect(
            pin.get("scope"),
            EXPECTED_SOURCE_SCOPES.get(relative),
            f"source pin {index} scope",
        )
        _sha256(pin.get("sha256"), f"source pin {index} SHA")
    if seen != set(SOURCE_PATHS):
        raise E02ContractError("canonical source path set drift")

    s107 = _mapping(config.get("predecessor_s107"), "predecessor S107")
    for field in ("deep_validation_required", "actual_completed_output_required"):
        _expect(s107.get(field), True, f"S107.{field}")
    for field in (
        "config_path",
        "contract_path",
        "completion_validator_path",
        "completion_commit_path",
        "threshold_artifact_relative_path",
    ):
        _relative_string(s107.get(field), f"S107.{field}")
    for field in (
        "config_sha256",
        "contract_sha256",
        "completion_validator_sha256",
    ):
        _sha256(s107.get(field), f"S107.{field}")
    for field in ("completion_commit_sha256", "threshold_artifact_sha256"):
        value = _string(s107.get(field), f"S107.{field}")
        if value != TBD_S107_COMPLETION:
            _sha256(value, f"S107.{field}")
    _expect(
        s107.get("required_commit_schema_version"),
        "voice_coach.wavlm_s107_calibration_commit.v1",
        "S107 commit schema",
    )
    _expect(s107.get("required_commit_status"), "committed", "S107 commit status")
    _expect(s107.get("required_deep_status"), "valid", "S107 deep status")
    _expect(
        s107.get("required_label_status"),
        "silver_oracle_provisional",
        "S107 label status",
    )
    _expect(
        s107.get("required_registry_status"),
        "completed",
        "S107 registry status",
    )
    _expect(
        s107.get("completion_validator_symbol"),
        "validate_s107_commit",
        "S107 validator symbol",
    )
    _expect(
        s107.get("threshold_artifact_name"),
        "threshold_policy_report",
        "S107 threshold artifact name",
    )
    _expect(
        s107.get("threshold_artifact_relative_path"),
        "threshold_policy_report.json",
        "S107 threshold artifact path",
    )
    _expect(
        s107.get("threshold_artifact_schema_version"),
        "voice_coach.wavlm_s107_threshold_policy.v1",
        "S107 threshold artifact schema",
    )
    _expect(
        s107.get("threshold_policy_status"),
        "provisional_engineering_not_promotion",
        "S107 threshold policy status",
    )
    _expect(
        s107.get("required_threshold_selection_status"),
        "selected_provisional_engineering",
        "S107 threshold selection",
    )
    _expect(
        s107.get("numeric_threshold_copied_into_e02_config"),
        False,
        "S107 numeric threshold copy",
    )
    _expect(
        s107.get("threshold_source"),
        "deep_validated_actual_s107_threshold_policy_only",
        "S107 threshold source",
    )
    _expect(
        s107.get("abstain_all_selection_status_prohibited"),
        True,
        "S107 abstain-all status",
    )

    c02 = _mapping(config.get("predecessor_c02"), "predecessor C02")
    for field in (
        "deep_validation_required",
        "actual_completed_bundle_required",
        "deterministic_recomputation_required",
    ):
        _expect(c02.get(field), True, f"C02.{field}")
    for field in (
        "config_path",
        "contract_path",
        "bundle_schema_path",
        "completion_commit_path",
    ):
        _relative_string(c02.get(field), f"C02.{field}")
    for field in ("config_sha256", "contract_sha256", "bundle_schema_sha256"):
        _sha256(c02.get(field), f"C02.{field}")
    c02_commit_sha = _string(
        c02.get("completion_commit_sha256"),
        "C02 completion commit SHA",
    )
    if c02_commit_sha != TBD_C02_COMPLETION:
        _sha256(c02_commit_sha, "C02 completion commit SHA")
    _expect(
        c02.get("required_commit_schema_version"),
        "voice_coach.e02_c02_coaching_bundle_commit.v1",
        "C02 bundle schema",
    )
    _expect(c02.get("required_commit_status"), "committed", "C02 bundle status")
    _expect(
        c02.get("required_registry_schema_version"),
        "voice_coach.e02_c02_coaching_bundle_registry.v1",
        "C02 registry schema",
    )
    _expect(
        c02.get("required_registry_status"),
        "completed",
        "C02 registry status",
    )
    _expect(
        c02.get("required_diagnostic_input_schema_version"),
        "voice_coach.c02_diagnostic_issues_input.v1",
        "C02 diagnostic input schema",
    )
    _expect(
        c02.get("required_selection_output_schema_version"),
        "voice_coach.c02_coaching_selection_output.v1",
        "C02 selection output schema",
    )
    for field in (
        "blocked_top_k_backfill_allowed",
        "free_generation_llm_allowed",
        "prompt_or_completion_allowed",
        "network_access_allowed",
        "runtime_model_access_allowed",
        "gpu_access_allowed",
        "reference_generation_allowed",
        "production_release_allowed",
    ):
        _expect(c02.get(field), False, f"C02.{field}")

    prereg = _mapping(config.get("preregistration"), "preregistration")
    _expect(
        prereg.get("placeholder"),
        TBD_E02_PREREGISTRATION,
        "E02 preregistration placeholder",
    )
    for field in ("template_path", "approved_artifact_path"):
        _relative_string(prereg.get(field), f"preregistration.{field}")
    _sha256(prereg.get("template_sha256"), "E02 template SHA")
    approved_sha = _string(
        prereg.get("approved_artifact_sha256"),
        "E02 approved preregistration SHA",
    )
    if approved_sha != TBD_E02_PREREGISTRATION:
        _sha256(approved_sha, "E02 approved preregistration SHA")
    _expect(
        prereg.get("required_schema_version"),
        "voice_coach.e02_confidence_guard_preregistration.v1",
        "E02 preregistration schema",
    )
    _expect(
        prereg.get("required_status"),
        "approved_and_frozen",
        "E02 preregistration status",
    )
    for field in (
        "must_be_frozen_before_guard_input_or_api_access",
        "unresolved_fields_must_fail_closed",
    ):
        _expect(prereg.get(field), True, f"preregistration.{field}")

    guard = _mapping(config.get("guard_contract"), "guard contract")
    _expect(
        tuple(_list(guard.get("automatic_block_conditions"), "block conditions")),
        BLOCK_REASONS,
        "block conditions",
    )
    for field in (
        "blocked_reason_precedence",
        "mixed_reason_resolution_id",
        "ood_definition_id",
        "ood_threshold_or_rule_policy_sha256",
        "unsupported_definition_id",
        "abstain_all_definition_id",
    ):
        _expect(
            guard.get(field),
            "TBD_PREREGISTRATION",
            f"guard unresolved {field}",
        )
    _expect(
        tuple(
            _list(
                guard.get("allowed_blocked_safe_next_actions"),
                "safe next actions",
            )
        ),
        SAFE_NEXT_ACTIONS,
        "safe next actions",
    )
    _expect(
        guard.get("threshold_source"),
        "deep_validated_actual_s107_threshold_policy_only",
        "guard threshold source",
    )
    for field in (
        "numeric_threshold_in_config_allowed",
        "blocked_automatic_coaching_advice_allowed",
        "blocked_automatic_reference_allowed",
        "blocked_automatic_corrective_action_allowed",
        "c02_blocked_top_k_backfill_allowed",
        "c02_free_generation_allowed",
    ):
        _expect(guard.get(field), False, f"guard.{field}")
    _expect(
        guard.get("pass_through_requires_all_registered_gates"),
        True,
        "guard pass-through requirement",
    )

    privacy = _mapping(config.get("api_privacy"), "API privacy")
    for field in ("response_schema_path", "input_schema_path"):
        _relative_string(privacy.get(field), f"api_privacy.{field}")
    for field in ("response_schema_sha256", "input_schema_sha256"):
        _sha256(privacy.get(field), f"api_privacy.{field}")
    for field in (
        "blocked_reason_codes_allowed",
        "evidence_provenance_hashes_allowed",
        "safe_next_action_code_allowed",
    ):
        _expect(privacy.get(field), True, f"api_privacy.{field}")
    for field in (
        "model_score_allowed",
        "pii_allowed",
        "raw_path_allowed",
        "free_text_allowed",
        "advice_reference_or_corrective_payload_allowed_when_blocked",
    ):
        _expect(privacy.get(field), False, f"api_privacy.{field}")
    _expect(
        set(
            _list(
                privacy.get("forbidden_recursive_fields"),
                "forbidden API fields",
            )
        ),
        FORBIDDEN_API_FIELDS,
        "forbidden API fields",
    )

    non_execution = _mapping(config.get("non_execution"), "non execution")
    for field in CONFIG_SECTION_KEYS["non_execution"]:
        _expect(non_execution.get(field), False, f"non_execution.{field}")
    _expect(
        tuple(_list(config.get("execution_order"), "execution order")),
        EXPECTED_EXECUTION_ORDER,
        "execution order",
    )
    outputs = _mapping(config.get("outputs"), "outputs")
    for field in CONFIG_SECTION_KEYS["outputs"] - {
        "no_overwrite",
        "atomic_publish_required",
        "write_enabled_in_this_package",
    }:
        _relative_string(outputs.get(field), f"outputs.{field}")
    _expect(outputs.get("no_overwrite"), True, "output no-overwrite")
    _expect(outputs.get("atomic_publish_required"), True, "output atomic publish")
    _expect(
        outputs.get("write_enabled_in_this_package"),
        False,
        "output write boundary",
    )


def validate_static_config(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    audit: AccessAudit | None = None,
) -> StaticValidation:
    """Validate only C1/static pins and never resolve predecessor evidence."""

    root = workspace_root.resolve(strict=True)
    access = audit if audit is not None else AccessAudit()
    _validate_config_values(config)
    absolute_config = config_path if config_path.is_absolute() else root / config_path
    try:
        relative_config = absolute_config.relative_to(root).as_posix()
    except ValueError as exc:
        raise E02ContractError("E02 config must be inside workspace") from exc
    config_data, config_snapshot = _read_file(root, relative_config, "E02 config")
    if _load_config_bytes(config_data) != config:
        raise E02ContractError("E02 config changed between load and validation")

    canonical = _mapping(config.get("canonical_sources"), "canonical sources")
    pins = _list(canonical.get("pins"), "canonical source pins")
    if len(pins) != len(SOURCE_PATHS):
        raise E02ContractError("canonical source pin count drift")
    seen: set[str] = set()
    snapshots: list[FileSnapshot] = []
    payload_by_path: dict[str, bytes] = {}
    for index, raw_pin in enumerate(pins):
        pin = _mapping(raw_pin, f"source pin {index}")
        _expect_keys(pin, {"path", "scope", "sha256"}, f"source pin {index}")
        relative = _relative_string(pin.get("path"), f"source pin {index} path")
        expected_sha = _sha256(pin.get("sha256"), f"source pin {index} SHA")
        if relative in seen:
            raise E02ContractError(f"duplicate source pin: {relative}")
        seen.add(relative)
        _expect(
            pin.get("scope"),
            EXPECTED_SOURCE_SCOPES.get(relative),
            f"source pin {index} scope",
        )
        data, snapshot = _read_file(root, relative, f"source pin {relative}")
        access.source_pin_read_count += 1
        if snapshot.sha256 != expected_sha:
            raise E02ContractError(
                f"canonical source SHA-256 drift: {relative}",
                stage="source_pin",
                audit=access,
            )
        snapshots.append(snapshot)
        payload_by_path[relative] = data
    if tuple(sorted(seen)) != tuple(sorted(SOURCE_PATHS)):
        raise E02ContractError("canonical source path set drift")
    by_path = {snapshot.relative_path: snapshot for snapshot in snapshots}

    input_schema_path = "configs/coaching/e02_confidence_guard_input_v1.schema.json"
    response_schema_path = (
        "configs/coaching/e02_confidence_guard_api_response_v1.schema.json"
    )
    bundle_schema_path = (
        "configs/coaching/e02_c02_coaching_bundle_commit_v1.schema.json"
    )
    template_path = (
        "configs/coaching/e02_confidence_guard_preregistration_v1.template.json"
    )
    _validate_input_schema(
        _load_json_bytes(payload_by_path[input_schema_path], "E02 input schema")
    )
    _validate_response_schema(
        _load_json_bytes(
            payload_by_path[response_schema_path],
            "E02 response schema",
        )
    )
    _validate_c02_bundle_schema(
        _load_json_bytes(
            payload_by_path[bundle_schema_path],
            "E02 C02 bundle schema",
        )
    )
    _validate_preregistration_template(
        _load_json_bytes(
            payload_by_path[template_path],
            "E02 preregistration template",
        )
    )

    s107 = _mapping(config.get("predecessor_s107"), "predecessor S107")
    for field, expected_path in (
        ("config_path", "configs/experiments/wavlm_s107_calibration_v1.yaml"),
        (
            "contract_path",
            "src/voice_coach/training/wavlm_s107_calibration_contract.py",
        ),
        (
            "completion_validator_path",
            "src/voice_coach/training/wavlm_s107_calibration_training.py",
        ),
    ):
        _expect(s107.get(field), expected_path, f"S107.{field}")
        sha_field = field.replace("_path", "_sha256")
        _expect(
            s107.get(sha_field),
            by_path[expected_path].sha256,
            f"S107.{sha_field}/source binding",
        )

    c02 = _mapping(config.get("predecessor_c02"), "predecessor C02")
    for field, expected_path in (
        (
            "config_path",
            "configs/experiments/c02_deterministic_coaching_selection_v1.yaml",
        ),
        ("contract_path", "src/voice_coach/coaching/c02_selection_contract.py"),
        ("bundle_schema_path", bundle_schema_path),
    ):
        _expect(c02.get(field), expected_path, f"C02.{field}")
        sha_field = field.replace("_path", "_sha256")
        _expect(
            c02.get(sha_field),
            by_path[expected_path].sha256,
            f"C02.{sha_field}/source binding",
        )

    prereg = _mapping(config.get("preregistration"), "preregistration")
    _expect(prereg.get("template_path"), template_path, "E02 template path")
    _expect(
        prereg.get("template_sha256"),
        by_path[template_path].sha256,
        "E02 template/source binding",
    )
    privacy = _mapping(config.get("api_privacy"), "API privacy")
    _expect(privacy.get("input_schema_path"), input_schema_path, "input schema path")
    _expect(
        privacy.get("input_schema_sha256"),
        by_path[input_schema_path].sha256,
        "input schema/source binding",
    )
    _expect(
        privacy.get("response_schema_path"),
        response_schema_path,
        "response schema path",
    )
    _expect(
        privacy.get("response_schema_sha256"),
        by_path[response_schema_path].sha256,
        "response schema/source binding",
    )

    outputs = _mapping(config.get("outputs"), "outputs")
    _expect(
        outputs.get("guard_input_schema_path"),
        input_schema_path,
        "output/input schema binding",
    )
    _expect(
        outputs.get("api_response_schema_path"),
        response_schema_path,
        "output/response schema binding",
    )
    schema_output_fields = {
        "guard_input_schema_path",
        "api_response_schema_path",
    }
    output_paths = [
        _relative_string(value, f"outputs.{field}")
        for field, value in outputs.items()
        if field.endswith("_path") and field not in schema_output_fields
    ]
    if len(output_paths) != len(set(output_paths)):
        raise E02ContractError("E02 output paths must be unique")
    for output in output_paths:
        output_parts = PurePosixPath(output).parts
        for source in seen:
            source_parts = PurePosixPath(source).parts
            if (
                output_parts == source_parts
                or output_parts[: len(source_parts)] == source_parts
                or source_parts[: len(output_parts)] == output_parts
            ):
                raise E02ContractError(
                    f"E02 output/source overlap prohibited: {output} / {source}"
                )

    policy = {
        "predecessor_s107": config["predecessor_s107"],
        "predecessor_c02": config["predecessor_c02"],
        "preregistration": config["preregistration"],
        "guard_contract": config["guard_contract"],
        "api_privacy": config["api_privacy"],
        "non_execution": config["non_execution"],
        "execution_order": config["execution_order"],
    }
    return StaticValidation(
        config_sha256=config_snapshot.sha256,
        policy_sha256=hashlib.sha256(canonical_json_bytes(policy)).hexdigest(),
        source_snapshots=tuple(snapshots),
    )


def _copy_s107_audit(source: Any, target: AccessAudit) -> None:
    for field in (
        "s107_completion_path_resolution_count",
        "s107_completion_read_attempt_count",
        "s107_completion_read_count",
        "s107_validator_load_count",
        "s107_deep_validation_count",
        "s107_calibration_evidence_read_count",
        "s107_threshold_report_read_count",
        "s107_registry_read_count",
    ):
        setattr(target, field, int(getattr(source, field, 0)))


def deep_validate_s107(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> S107Evidence:
    """Deep-validate actual S107 only after exact pins are non-placeholder."""

    section = _mapping(config.get("predecessor_s107"), "predecessor S107")
    commit_sha = _string(
        section.get("completion_commit_sha256"),
        "S107 completion commit SHA",
    )
    threshold_sha = _string(
        section.get("threshold_artifact_sha256"),
        "S107 threshold report SHA",
    )
    if commit_sha == TBD_S107_COMPLETION or threshold_sha == TBD_S107_COMPLETION:
        raise E02ContractError(
            "S107 completion commit is TBD_S107_COMPLETION",
            stage="s107_completion",
            audit=audit,
        )
    _sha256(commit_sha, "S107 completion commit SHA")
    _sha256(threshold_sha, "S107 threshold report SHA")

    c02 = _mapping(config.get("predecessor_c02"), "predecessor C02")
    try:
        module = _load_module(
            workspace_root=workspace_root,
            relative=_relative_string(c02.get("contract_path"), "C02 contract path"),
            expected_sha256=_sha256(
                c02.get("contract_sha256"),
                "C02 contract SHA",
            ),
            module_name="_voice_coach_e02_pinned_c02_contract",
        )
    except E02ContractError as exc:
        raise E02ContractError(
            f"C02 contract unavailable for S107 validation: {exc}",
            stage="s107_completion",
            audit=audit,
        ) from exc
    audit.c02_contract_load_count += 1
    access_type = getattr(module, "AccessAudit", None)
    validator = getattr(module, "deep_validate_s107", None)
    if access_type is None or not callable(validator):
        raise E02ContractError(
            "pinned C02 S107 validator interface missing",
            stage="s107_completion",
            audit=audit,
        )
    c02_audit = access_type()
    try:
        evidence = validator(
            workspace_root=workspace_root,
            config={"predecessor_s107": dict(section)},
            audit=c02_audit,
        )
    except Exception as exc:
        _copy_s107_audit(c02_audit, audit)
        raise E02ContractError(
            f"S107 deep validation failed: {exc}",
            stage="s107_completion",
            audit=audit,
        ) from exc
    _copy_s107_audit(c02_audit, audit)
    _expect(evidence.commit_sha256, commit_sha, "S107 commit deep binding")
    _expect(
        evidence.threshold_report_sha256,
        threshold_sha,
        "S107 threshold report deep binding",
    )
    _expect(
        evidence.threshold_selection_status,
        section.get("required_threshold_selection_status"),
        "S107 selected threshold status",
    )
    return S107Evidence(
        commit_sha256=evidence.commit_sha256,
        threshold_report_sha256=evidence.threshold_report_sha256,
        threshold_selection_status=evidence.threshold_selection_status,
        c02_contract_module=module,
        c02_s107_evidence=evidence,
    )


def _validate_file_record(
    record: Mapping[str, Any],
    *,
    name: str,
    registry: bool = False,
) -> tuple[str, str, int]:
    _expect_keys(
        record,
        REGISTRY_RECORD_KEYS if registry else FILE_RECORD_KEYS,
        name,
    )
    relative = _relative_string(record.get("path"), f"{name} path")
    sha = _sha256(record.get("sha256"), f"{name} SHA")
    size = _integer(record.get("size_bytes"), f"{name} size", minimum=1)
    if registry:
        _expect(record.get("status"), "completed", f"{name} status")
    return relative, sha, size


def validate_c02_bundle_commit_payload(
    payload: Mapping[str, Any],
    *,
    s107: S107Evidence,
) -> None:
    """Validate the restricted C02 handoff structure without reading its files."""

    _reject_nonfinite_tree(payload, "C02 bundle commit")
    _expect_keys(payload, C02_COMMIT_KEYS, "C02 bundle commit")
    _expect(
        payload.get("schema_version"),
        "voice_coach.e02_c02_coaching_bundle_commit.v1",
        "C02 bundle schema",
    )
    bundle_id = _string(payload.get("bundle_id"), "C02 bundle ID")
    if not re.fullmatch(r"e02c02bundle_[0-9a-f]{32}", bundle_id):
        raise E02ContractError("invalid C02 bundle ID")
    _expect(payload.get("status"), "committed", "C02 bundle status")
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_experiment",
        "C02 bundle privacy zone",
    )
    for field in (
        "c02_config",
        "preregistration",
        "diagnostic_input",
        "selection_output",
    ):
        _validate_file_record(
            _mapping(payload.get(field), f"C02 bundle {field}"),
            name=f"C02 bundle {field}",
        )
    _validate_file_record(
        _mapping(payload.get("registry"), "C02 bundle registry"),
        name="C02 bundle registry",
        registry=True,
    )
    lineage = _mapping(payload.get("s107"), "C02 bundle S107")
    _expect_keys(lineage, C02_S107_KEYS, "C02 bundle S107")
    _expect(
        lineage.get("completion_commit_sha256"),
        s107.commit_sha256,
        "C02/S107 commit binding",
    )
    _expect(
        lineage.get("threshold_policy_report_sha256"),
        s107.threshold_report_sha256,
        "C02/S107 threshold binding",
    )
    assurance = _mapping(payload.get("assurance"), "C02 bundle assurance")
    _expect_keys(assurance, C02_ASSURANCE_KEYS, "C02 bundle assurance")
    for field in (
        "actual_c02_selection_completed",
        "deterministic_recomputation_required",
    ):
        _expect(assurance.get(field), True, f"C02 assurance.{field}")
    for field in C02_ASSURANCE_KEYS - {
        "actual_c02_selection_completed",
        "deterministic_recomputation_required",
    }:
        _expect(assurance.get(field), False, f"C02 assurance.{field}")
    _expect(
        payload.get("binding_sha256"),
        binding_sha256(payload),
        "C02 bundle binding",
    )


def _read_record_bytes(
    *,
    workspace_root: Path,
    record: Mapping[str, Any],
    name: str,
    registry: bool = False,
) -> tuple[bytes, FileSnapshot]:
    relative, sha, size = _validate_file_record(
        record,
        name=name,
        registry=registry,
    )
    data, snapshot = _read_file(workspace_root, relative, name)
    _expect(snapshot.sha256, sha, f"{name} SHA binding")
    _expect(snapshot.size, size, f"{name} size binding")
    return data, snapshot


def _read_record_json(
    *,
    workspace_root: Path,
    record: Mapping[str, Any],
    name: str,
    registry: bool = False,
) -> tuple[Mapping[str, Any], FileSnapshot]:
    data, snapshot = _read_record_bytes(
        workspace_root=workspace_root,
        record=record,
        name=name,
        registry=registry,
    )
    payload = _load_json_bytes(data, name)
    return payload, snapshot


def _validate_c02_registry(
    payload: Mapping[str, Any],
    *,
    commit: Mapping[str, Any],
    s107: S107Evidence,
) -> None:
    _reject_nonfinite_tree(payload, "C02 bundle registry")
    _expect_keys(payload, C02_REGISTRY_KEYS, "C02 bundle registry")
    _expect(
        payload.get("schema_version"),
        "voice_coach.e02_c02_coaching_bundle_registry.v1",
        "C02 registry schema",
    )
    _expect(payload.get("status"), "completed", "C02 registry status")
    bindings = (
        ("c02_config_sha256", "c02_config"),
        ("c02_preregistration_sha256", "preregistration"),
        ("diagnostic_input_sha256", "diagnostic_input"),
        ("selection_output_sha256", "selection_output"),
    )
    for registry_field, commit_field in bindings:
        record = _mapping(commit.get(commit_field), f"C02 commit {commit_field}")
        _expect(
            payload.get(registry_field),
            record.get("sha256"),
            f"C02 registry {registry_field} binding",
        )
    _expect(
        payload.get("s107_completion_commit_sha256"),
        s107.commit_sha256,
        "C02 registry S107 commit",
    )
    _expect(
        payload.get("s107_threshold_policy_report_sha256"),
        s107.threshold_report_sha256,
        "C02 registry S107 threshold",
    )
    _expect(
        payload.get("deterministic_catalog_only"),
        True,
        "C02 registry deterministic policy",
    )
    for field in (
        "blocked_top_k_backfill_allowed",
        "free_generation_llm_used",
        "prompt_or_completion_present",
        "network_accessed",
        "runtime_model_accessed",
        "gpu_accessed",
        "reference_generation_executed",
        "production_release_allowed",
    ):
        _expect(payload.get(field), False, f"C02 registry.{field}")
    _expect(
        payload.get("binding_sha256"),
        binding_sha256(payload),
        "C02 registry binding",
    )


def _c02_audit_value(audit: Any, field: str) -> int:
    return int(getattr(audit, field, 0))


def deep_validate_c02_bundle(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    s107: S107Evidence,
    audit: AccessAudit,
) -> C02Evidence:
    """Deep-recompute one exactly pinned actual C02 deterministic bundle."""

    section = _mapping(config.get("predecessor_c02"), "predecessor C02")
    expected_sha = _string(
        section.get("completion_commit_sha256"),
        "C02 completion commit SHA",
    )
    if expected_sha == TBD_C02_COMPLETION:
        raise E02ContractError(
            "C02 completion commit is TBD_C02_COMPLETION",
            stage="c02_completion",
            audit=audit,
        )
    _sha256(expected_sha, "C02 completion commit SHA")
    audit.c02_completion_path_resolution_count += 1
    audit.c02_completion_read_attempt_count += 1
    try:
        commit, commit_snapshot = _read_exact_json(
            workspace_root=workspace_root,
            relative=_relative_string(
                section.get("completion_commit_path"),
                "C02 completion commit path",
            ),
            expected_sha256=expected_sha,
            name="C02 completed bundle",
        )
    except E02ContractError as exc:
        raise E02ContractError(
            f"C02 completed bundle unavailable: {exc}",
            stage="c02_completion",
            audit=audit,
        ) from exc
    audit.c02_completion_read_count += 1
    try:
        validate_c02_bundle_commit_payload(commit, s107=s107)
    except E02ContractError as exc:
        raise E02ContractError(
            str(exc),
            stage="c02_completion",
            audit=audit,
        ) from exc

    module = s107.c02_contract_module
    access_type = getattr(module, "AccessAudit", None)
    validate_c02_static = getattr(module, "validate_static_config", None)
    deep_c01 = getattr(module, "deep_validate_c01", None)
    validate_input = getattr(module, "validate_diagnostic_input_payload", None)
    validate_output = getattr(module, "validate_coaching_output_payload", None)
    read_prereg = getattr(module, "_read_approved_preregistration", None)
    if (
        access_type is None
        or not callable(validate_c02_static)
        or not callable(deep_c01)
        or not callable(validate_input)
        or not callable(validate_output)
        or not callable(read_prereg)
    ):
        raise E02ContractError(
            "pinned C02 deep-recomputation interface missing",
            stage="c02_completion",
            audit=audit,
        )

    config_record = _mapping(commit.get("c02_config"), "C02 config record")
    _, c02_config_snapshot = _read_record_bytes(
        workspace_root=workspace_root,
        record=config_record,
        name="C02 effective config",
    )
    _expect(
        config_record.get("path"),
        section.get("config_path"),
        "C02 config path binding",
    )
    _expect(
        c02_config_snapshot.sha256,
        section.get("config_sha256"),
        "C02 config SHA binding",
    )
    c02_config = module.load_config(workspace_root / c02_config_snapshot.relative_path)
    c02_audit = access_type()
    try:
        static = validate_c02_static(
            workspace_root=workspace_root,
            config_path=workspace_root / c02_config_snapshot.relative_path,
            config=c02_config,
            audit=c02_audit,
        )
    except Exception as exc:
        raise E02ContractError(
            f"C02 static deep validation failed: {exc}",
            stage="c02_completion",
            audit=audit,
        ) from exc
    audit.c02_static_source_read_count += _c02_audit_value(
        c02_audit,
        "source_pin_read_count",
    )
    _expect(static.config_sha256, c02_config_snapshot.sha256, "C02 config deep hash")
    raw_static_snapshots = getattr(static, "source_snapshots", None)
    if not isinstance(raw_static_snapshots, (list, tuple)):
        raise E02ContractError(
            "C02 static validator omitted source snapshots",
            stage="c02_completion",
            audit=audit,
        )
    c02_static_snapshots: list[FileSnapshot] = []
    for index, snapshot in enumerate(raw_static_snapshots):
        try:
            c02_static_snapshots.append(
                FileSnapshot(
                    relative_path=_relative_string(
                        getattr(snapshot, "relative_path"),
                        f"C02 static snapshot {index} path",
                    ),
                    sha256=_sha256(
                        getattr(snapshot, "sha256"),
                        f"C02 static snapshot {index} SHA",
                    ),
                    size=_integer(
                        getattr(snapshot, "size"),
                        f"C02 static snapshot {index} size",
                        minimum=1,
                    ),
                    device=_integer(
                        getattr(snapshot, "device"),
                        f"C02 static snapshot {index} device",
                        minimum=0,
                    ),
                    inode=_integer(
                        getattr(snapshot, "inode"),
                        f"C02 static snapshot {index} inode",
                        minimum=0,
                    ),
                    mtime_ns=_integer(
                        getattr(snapshot, "mtime_ns"),
                        f"C02 static snapshot {index} mtime",
                        minimum=0,
                    ),
                )
            )
        except (AttributeError, E02ContractError) as exc:
            raise E02ContractError(
                f"invalid C02 static source snapshot {index}: {exc}",
                stage="c02_completion",
                audit=audit,
            ) from exc
    if len(c02_static_snapshots) != audit.c02_static_source_read_count:
        raise E02ContractError(
            "C02 static source snapshot/read count mismatch",
            stage="c02_completion",
            audit=audit,
        )

    c02_s107 = _mapping(c02_config.get("predecessor_s107"), "C02 S107 section")
    _expect(
        c02_s107.get("completion_commit_sha256"),
        s107.commit_sha256,
        "C02 config/S107 commit binding",
    )
    _expect(
        c02_s107.get("threshold_artifact_sha256"),
        s107.threshold_report_sha256,
        "C02 config/S107 threshold binding",
    )
    try:
        c01 = deep_c01(
            workspace_root=workspace_root,
            config=c02_config,
            audit=c02_audit,
        )
    except Exception as exc:
        raise E02ContractError(
            f"C02 C01 deep validation failed: {exc}",
            stage="c02_completion",
            audit=audit,
        ) from exc
    audit.c02_c01_deep_validation_count += 1

    prereg_record = _mapping(commit.get("preregistration"), "C02 prereg record")
    c02_prereg_config = _mapping(c02_config.get("preregistration"), "C02 prereg")
    _expect(
        prereg_record.get("path"),
        c02_prereg_config.get("approved_artifact_path"),
        "C02 preregistration path binding",
    )
    _expect(
        prereg_record.get("sha256"),
        c02_prereg_config.get("approved_artifact_sha256"),
        "C02 preregistration SHA binding",
    )
    try:
        policy = read_prereg(
            workspace_root=workspace_root,
            config=c02_config,
            c01=c01,
            s107=s107.c02_s107_evidence,
            audit=c02_audit,
        )
    except Exception as exc:
        raise E02ContractError(
            f"C02 preregistration deep validation failed: {exc}",
            stage="c02_completion",
            audit=audit,
        ) from exc
    audit.c02_preregistration_read_count += 1
    _expect(
        policy.sha256,
        prereg_record.get("sha256"),
        "C02 preregistration deep hash",
    )
    policy_snapshot = getattr(policy, "snapshot", None)
    if policy_snapshot is None:
        raise E02ContractError(
            "C02 preregistration deep validator omitted its file snapshot",
            stage="c02_completion",
            audit=audit,
        )
    prereg_snapshot = FileSnapshot(
        relative_path=str(policy_snapshot.relative_path),
        sha256=str(policy_snapshot.sha256),
        size=int(policy_snapshot.size),
        device=int(policy_snapshot.device),
        inode=int(policy_snapshot.inode),
        mtime_ns=int(policy_snapshot.mtime_ns),
    )
    _expect(
        prereg_snapshot.relative_path,
        prereg_record.get("path"),
        "C02 preregistration path/deep snapshot",
    )
    _expect(
        prereg_snapshot.sha256,
        prereg_record.get("sha256"),
        "C02 preregistration SHA/deep snapshot",
    )
    _expect(
        prereg_snapshot.size,
        prereg_record.get("size_bytes"),
        "C02 preregistration size/deep snapshot",
    )

    input_record = _mapping(commit.get("diagnostic_input"), "C02 diagnostic input")
    input_payload, input_snapshot = _read_record_json(
        workspace_root=workspace_root,
        record=input_record,
        name="C02 diagnostic input",
    )
    audit.c02_diagnostic_input_read_count += 1
    try:
        validate_input(
            input_payload,
            policy=policy,
            c01=c01,
            s107=s107.c02_s107_evidence,
        )
    except Exception as exc:
        raise E02ContractError(
            f"C02 diagnostic input deep validation failed: {exc}",
            stage="c02_completion",
            audit=audit,
        ) from exc
    _expect(
        input_payload.get("schema_version"),
        section.get("required_diagnostic_input_schema_version"),
        "C02 diagnostic input schema",
    )

    output_record = _mapping(commit.get("selection_output"), "C02 selection output")
    output_payload, output_snapshot = _read_record_json(
        workspace_root=workspace_root,
        record=output_record,
        name="C02 selection output",
    )
    audit.c02_selection_output_read_count += 1
    try:
        validate_output(
            output_payload,
            input_payload=input_payload,
            policy=policy,
            c01=c01,
            s107=s107.c02_s107_evidence,
            c02_config_sha256=static.config_sha256,
        )
    except Exception as exc:
        raise E02ContractError(
            f"C02 deterministic output recomputation failed: {exc}",
            stage="c02_completion",
            audit=audit,
        ) from exc
    _expect(
        output_payload.get("schema_version"),
        section.get("required_selection_output_schema_version"),
        "C02 selection output schema",
    )
    audit.c02_deep_recomputation_count += 1

    registry_record = _mapping(commit.get("registry"), "C02 registry record")
    registry_payload, registry_snapshot = _read_record_json(
        workspace_root=workspace_root,
        record=registry_record,
        name="C02 bundle registry",
        registry=True,
    )
    audit.c02_registry_read_count += 1
    _validate_c02_registry(registry_payload, commit=commit, s107=s107)

    c02_outputs = _mapping(c02_config.get("outputs"), "C02 outputs")
    _expect(
        output_record.get("path"),
        c02_outputs.get("selection_manifest_path"),
        "C02 selection output/config path",
    )
    _expect(
        registry_record.get("path"),
        c02_outputs.get("registry_path"),
        "C02 registry/config path",
    )
    _expect(
        section.get("completion_commit_path"),
        c02_outputs.get("commit_path"),
        "C02 commit/config path",
    )

    snapshots = (
        commit_snapshot,
        c02_config_snapshot,
        prereg_snapshot,
        input_snapshot,
        output_snapshot,
        registry_snapshot,
        *c02_static_snapshots,
    )
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=snapshots,
        audit=audit,
        stage="c02_completion",
    )
    return C02Evidence(
        commit_sha256=commit_snapshot.sha256,
        config_sha256=c02_config_snapshot.sha256,
        preregistration_sha256=prereg_snapshot.sha256,
        diagnostic_input_sha256=input_snapshot.sha256,
        selection_output_sha256=output_snapshot.sha256,
        registry_sha256=registry_snapshot.sha256,
        selection_output=output_payload,
        snapshots=snapshots,
    )


def validate_preregistration_payload(
    payload: Mapping[str, Any],
    *,
    s107: S107Evidence,
    c02: C02Evidence,
) -> E02Policy:
    """Validate a resolved policy without copying a numeric model threshold."""

    _validate_preregistration_structure(payload, name="E02 preregistration")
    if _contains_tbd(payload):
        raise E02ContractError("approved E02 preregistration contains TBD value")
    _expect(
        payload.get("schema_version"),
        "voice_coach.e02_confidence_guard_preregistration.v1",
        "E02 preregistration schema",
    )
    prereg_id = _string(payload.get("preregistration_id"), "E02 preregistration ID")
    if not re.fullmatch(r"e02prereg_[0-9a-f]{32}", prereg_id):
        raise E02ContractError("invalid E02 preregistration ID")
    _expect(payload.get("status"), "approved_and_frozen", "E02 prereg status")
    _utc(payload.get("registered_at_utc"), "E02 registered at")
    _safe_id(payload.get("registered_by_role"), "E02 registered role")
    _expect(
        payload.get("frozen_before_guard_input_or_api_access"),
        True,
        "E02 freeze order",
    )

    lineage = _mapping(payload.get("lineage"), "E02 prereg lineage")
    _expect(
        lineage.get("s107_completion_commit_sha256"),
        s107.commit_sha256,
        "E02 prereg S107 commit",
    )
    _expect(
        lineage.get("s107_threshold_policy_report_sha256"),
        s107.threshold_report_sha256,
        "E02 prereg S107 threshold",
    )
    _expect(
        lineage.get("c02_completion_commit_sha256"),
        c02.commit_sha256,
        "E02 prereg C02 commit",
    )
    _expect(
        lineage.get("c02_preregistration_sha256"),
        c02.preregistration_sha256,
        "E02 prereg C02 policy",
    )

    domain = _mapping(payload.get("domain_policy"), "E02 domain policy")
    _safe_id(domain.get("ood_definition_id"), "OOD definition ID")
    _sha256(domain.get("ood_definition_policy_sha256"), "OOD definition policy")
    _sha256(
        domain.get("ood_threshold_or_rule_policy_sha256"),
        "OOD threshold/rule policy",
    )
    for field in (
        "domain_assessment_receipt_required",
        "out_of_domain_blocks_all_automatic_correction",
    ):
        _expect(domain.get(field), True, f"E02 domain policy.{field}")

    confidence = _mapping(payload.get("confidence_policy"), "confidence policy")
    _expect(
        confidence.get("threshold_source"),
        "deep_validated_actual_s107_threshold_policy_only",
        "E02 threshold source",
    )
    _expect(
        confidence.get("numeric_threshold_copied_into_e02"),
        False,
        "E02 numeric threshold copy",
    )
    _expect(
        confidence.get("threshold_policy_report_sha256"),
        s107.threshold_report_sha256,
        "E02 confidence S107 report binding",
    )
    _sha256(
        confidence.get("confidence_assessment_contract_sha256"),
        "confidence assessment contract",
    )
    _expect(
        confidence.get("below_threshold_blocks_all_automatic_correction"),
        True,
        "below-threshold blocking",
    )

    support = _mapping(
        payload.get("support_and_abstention_policy"),
        "support/abstention policy",
    )
    _safe_id(support.get("unsupported_definition_id"), "unsupported definition")
    _sha256(support.get("unsupported_policy_sha256"), "unsupported policy")
    _safe_id(support.get("abstain_all_definition_id"), "abstain-all definition")
    _sha256(support.get("abstain_all_policy_sha256"), "abstain-all policy")
    for field in (
        "unsupported_blocks_all_automatic_correction",
        "abstain_all_blocks_all_automatic_correction",
    ):
        _expect(support.get(field), True, f"E02 support policy.{field}")
    for field in (
        "c02_blocked_top_k_backfill_allowed",
        "c02_free_generation_allowed",
    ):
        _expect(support.get(field), False, f"E02 support policy.{field}")

    combination = _mapping(payload.get("combination_policy"), "combination policy")
    precedence = tuple(
        _string(value, "blocked reason precedence")
        for value in _list(
            combination.get("blocked_reason_precedence"),
            "blocked reason precedence",
        )
    )
    if len(precedence) != len(BLOCK_REASONS) or set(precedence) != set(BLOCK_REASONS):
        raise E02ContractError(
            "blocked reason precedence must contain each registered reason once"
        )
    action_mapping_raw = _mapping(
        combination.get("safe_next_action_by_reason"),
        "safe next action mapping",
    )
    _expect(set(action_mapping_raw), set(BLOCK_REASONS), "safe action reason keys")
    action_mapping: dict[str, str] = {}
    for reason in BLOCK_REASONS:
        action = _string(action_mapping_raw.get(reason), f"safe action {reason}")
        if action not in SAFE_NEXT_ACTIONS:
            raise E02ContractError(f"unsafe next action for {reason}: {action}")
        action_mapping[reason] = action
    _expect(
        action_mapping["low_confidence"],
        "rerecord",
        "C02 low-confidence safe action inheritance",
    )
    _expect(
        action_mapping["unsupported"],
        "expert_review",
        "C02 unsupported safe action inheritance",
    )
    mixed_rule = _safe_id(
        combination.get("mixed_reason_resolution_id"),
        "mixed reason resolution",
    )
    _expect(
        tuple(
            _list(
                combination.get("allowed_blocked_safe_next_actions"),
                "allowed safe next actions",
            )
        ),
        SAFE_NEXT_ACTIONS,
        "allowed safe next actions",
    )

    privacy = _mapping(payload.get("api_privacy_policy"), "API privacy policy")
    for field in (
        "blocked_reason_codes_allowed",
        "evidence_provenance_hashes_allowed",
        "safe_next_action_code_allowed",
    ):
        _expect(privacy.get(field), True, f"E02 API privacy.{field}")
    for field in (
        "model_score_allowed",
        "pii_allowed",
        "raw_path_allowed",
        "free_text_allowed",
        "advice_payload_allowed_when_blocked",
        "reference_payload_allowed_when_blocked",
        "corrective_action_payload_allowed_when_blocked",
    ):
        _expect(privacy.get(field), False, f"E02 API privacy.{field}")
    approval = _mapping(payload.get("approval"), "E02 approval")
    _expect(approval.get("decision"), "approved", "E02 approval decision")
    _sha256(approval.get("protocol_sha256"), "E02 approval protocol")
    _sha256(approval.get("signed_receipt_sha256"), "E02 signed receipt")
    _expect(
        payload.get("binding_sha256"),
        binding_sha256(payload),
        "E02 preregistration binding",
    )
    canonical_sha = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return E02Policy(
        sha256=canonical_sha,
        blocked_reason_precedence=precedence,
        safe_next_action_by_reason=action_mapping,
        mixed_reason_resolution_id=mixed_rule,
        payload=payload,
        snapshot=None,
    )


def _read_e02_preregistration(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    s107: S107Evidence,
    c02: C02Evidence,
    audit: AccessAudit,
) -> E02Policy:
    section = _mapping(config.get("preregistration"), "E02 preregistration")
    expected_sha = _string(
        section.get("approved_artifact_sha256"),
        "E02 approved preregistration SHA",
    )
    if expected_sha == TBD_E02_PREREGISTRATION:
        raise E02ContractError(
            "E02 preregistration is TBD_E02_PREREGISTRATION",
            stage="e02_preregistration",
            audit=audit,
        )
    _sha256(expected_sha, "E02 approved preregistration SHA")
    audit.e02_preregistration_path_resolution_count += 1
    audit.e02_preregistration_read_attempt_count += 1
    try:
        payload, snapshot = _read_exact_json(
            workspace_root=workspace_root,
            relative=_relative_string(
                section.get("approved_artifact_path"),
                "E02 approved preregistration path",
            ),
            expected_sha256=expected_sha,
            name="E02 approved preregistration",
        )
    except E02ContractError as exc:
        raise E02ContractError(
            f"E02 preregistration unavailable: {exc}",
            stage="e02_preregistration",
            audit=audit,
        ) from exc
    audit.e02_preregistration_read_count += 1
    try:
        policy = validate_preregistration_payload(payload, s107=s107, c02=c02)
    except E02ContractError as exc:
        raise E02ContractError(
            str(exc),
            stage="e02_preregistration",
            audit=audit,
        ) from exc
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=(snapshot,),
        audit=audit,
        stage="e02_preregistration",
    )
    return E02Policy(
        sha256=snapshot.sha256,
        blocked_reason_precedence=policy.blocked_reason_precedence,
        safe_next_action_by_reason=policy.safe_next_action_by_reason,
        mixed_reason_resolution_id=policy.mixed_reason_resolution_id,
        payload=payload,
        snapshot=snapshot,
    )


def validate_guard_input_payload(
    payload: Mapping[str, Any],
    *,
    policy: E02Policy,
    s107: S107Evidence,
    c02: C02Evidence,
) -> Mapping[str, Any]:
    """Validate a score-free D1 guard input without I/O."""

    _reject_nonfinite_tree(payload, "E02 guard input")
    _reject_api_leakage(payload, "E02 guard input")
    _expect_keys(payload, INPUT_KEYS, "E02 guard input")
    _expect(
        payload.get("schema_version"),
        "voice_coach.e02_confidence_guard_input.v1",
        "E02 guard input schema",
    )
    input_id = _string(payload.get("guard_input_id"), "E02 guard input ID")
    if not re.fullmatch(r"e02in_[0-9a-f]{32}", input_id):
        raise E02ContractError("invalid E02 guard input ID")
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_api_guard",
        "E02 guard input privacy zone",
    )
    _sha256(payload.get("request_binding_sha256"), "request binding")
    lineage = _mapping(payload.get("lineage"), "E02 input lineage")
    _expect_keys(lineage, INPUT_LINEAGE_KEYS, "E02 input lineage")
    bindings = {
        "s107_completion_commit_sha256": s107.commit_sha256,
        "s107_threshold_policy_report_sha256": s107.threshold_report_sha256,
        "c02_completion_commit_sha256": c02.commit_sha256,
        "c02_preregistration_sha256": c02.preregistration_sha256,
        "c02_diagnostic_input_sha256": c02.diagnostic_input_sha256,
        "c02_selection_output_sha256": c02.selection_output_sha256,
        "e02_preregistration_sha256": policy.sha256,
    }
    for field, expected in bindings.items():
        _expect(lineage.get(field), expected, f"E02 input lineage.{field}")

    assessments = _mapping(payload.get("assessments"), "E02 assessments")
    _expect_keys(assessments, ASSESSMENT_KEYS, "E02 assessments")
    if assessments.get("domain_status") not in {
        "within_registered_domain",
        "out_of_registered_domain",
    }:
        raise E02ContractError("invalid E02 domain status")
    if assessments.get("confidence_status") not in {
        "at_or_above_s107_policy",
        "below_s107_policy",
    }:
        raise E02ContractError("invalid E02 confidence status")
    if assessments.get("support_status") not in {"supported", "unsupported"}:
        raise E02ContractError("invalid E02 support status")
    c02_status = _string(
        assessments.get("c02_selection_status"),
        "C02 selection status",
    )
    if c02_status not in {"selected", "abstained", "no_issues"}:
        raise E02ContractError("invalid C02 selection status")
    _expect(
        c02_status,
        c02.selection_output.get("status"),
        "E02 input/C02 selection status binding",
    )
    abstain_all = _boolean(assessments.get("c02_abstain_all"), "C02 abstain-all")
    ranked_window_count = _integer(
        c02.selection_output.get("ranked_window_count"),
        "C02 ranked window count",
        minimum=0,
    )
    selected_count = _integer(
        c02.selection_output.get("selected_count"),
        "C02 selected count",
        minimum=0,
    )
    blocked_count = _integer(
        c02.selection_output.get("blocked_count"),
        "C02 blocked count",
        minimum=0,
    )
    if selected_count + blocked_count != ranked_window_count:
        raise E02ContractError("C02 selection counts do not partition ranked window")
    if c02_status == "no_issues" and (
        ranked_window_count != 0 or selected_count != 0 or blocked_count != 0
    ):
        raise E02ContractError("C02 no-issues status/count mismatch")
    if c02_status == "selected" and selected_count == 0:
        raise E02ContractError("C02 selected status requires a selected item")
    expected_abstain_all = (
        c02_status == "abstained"
        and ranked_window_count > 0
        and selected_count == 0
        and blocked_count == ranked_window_count
    )
    if c02_status == "abstained" and not expected_abstain_all:
        raise E02ContractError("C02 abstained status/count mismatch")
    _expect(abstain_all, expected_abstain_all, "C02 abstain-all derivation")

    provenance = _mapping(
        payload.get("evidence_provenance"),
        "E02 input provenance",
    )
    _expect_keys(provenance, PROVENANCE_KEYS, "E02 input provenance")
    for field in PROVENANCE_KEYS:
        _sha256(provenance.get(field), f"E02 provenance.{field}")
    _expect(
        provenance.get("c02_selection_receipt_sha256"),
        c02.selection_output_sha256,
        "C02 selection provenance binding",
    )

    inherited = _mapping(payload.get("inherited_safety"), "E02 inherited safety")
    _expect_keys(inherited, INHERITED_SAFETY_KEYS, "E02 inherited safety")
    _expect(
        inherited.get("c02_blocked_top_k_no_backfill"),
        True,
        "C02 no-backfill inheritance",
    )
    for field in INHERITED_SAFETY_KEYS - {"c02_blocked_top_k_no_backfill"}:
        _expect(inherited.get(field), False, f"E02 inherited safety.{field}")
    _expect(
        payload.get("binding_sha256"),
        binding_sha256(payload),
        "E02 guard input binding",
    )
    return payload


def _blocked_reasons(
    assessments: Mapping[str, Any],
    *,
    policy: E02Policy,
) -> tuple[str, ...]:
    active: set[str] = set()
    if assessments.get("domain_status") == "out_of_registered_domain":
        active.add("out_of_domain")
    if assessments.get("confidence_status") == "below_s107_policy":
        active.add("low_confidence")
    if assessments.get("support_status") == "unsupported":
        active.add("unsupported")
    if assessments.get("c02_abstain_all") is True:
        active.add("abstain_all")
    return tuple(
        reason for reason in policy.blocked_reason_precedence if reason in active
    )


def derive_api_guard_response(
    input_payload: Mapping[str, Any],
    *,
    policy: E02Policy,
    s107: S107Evidence,
    c02: C02Evidence,
) -> Mapping[str, Any]:
    """Derive only a score-free guard envelope; never deliver correction."""

    validate_guard_input_payload(
        input_payload,
        policy=policy,
        s107=s107,
        c02=c02,
    )
    assessments = _mapping(input_payload.get("assessments"), "E02 assessments")
    reasons = _blocked_reasons(assessments, policy=policy)
    c02_status = _string(
        assessments.get("c02_selection_status"),
        "C02 selection status",
    )
    if reasons:
        status = "blocked"
        blocked = True
        safe_action: str | None = policy.safe_next_action_by_reason[reasons[0]]
        capabilities = {
            "automatic_coaching_advice_allowed": False,
            "automatic_reference_allowed": False,
            "automatic_corrective_action_allowed": False,
            "separate_renderer_required": False,
        }
    elif c02_status == "no_issues":
        status = "no_correction"
        blocked = False
        safe_action = None
        capabilities = {
            "automatic_coaching_advice_allowed": False,
            "automatic_reference_allowed": False,
            "automatic_corrective_action_allowed": False,
            "separate_renderer_required": False,
        }
    else:
        if c02_status != "selected":
            raise E02ContractError("non-selected C02 output must remain blocked")
        status = "allowed_for_separate_renderer"
        blocked = False
        safe_action = None
        capabilities = {
            "automatic_coaching_advice_allowed": True,
            "automatic_reference_allowed": True,
            "automatic_corrective_action_allowed": True,
            "separate_renderer_required": True,
        }

    input_sha = hashlib.sha256(canonical_json_bytes(input_payload)).hexdigest()
    identity = {
        "schema_version": "voice_coach.e02_confidence_guard_identity.v1",
        "guard_input_sha256": input_sha,
        "e02_preregistration_sha256": policy.sha256,
        "blocked_reasons": list(reasons),
        "status": status,
    }
    response_id = (
        "e02resp_" + hashlib.sha256(canonical_json_bytes(identity)).hexdigest()[:32]
    )
    response: dict[str, Any] = {
        "schema_version": "voice_coach.e02_confidence_guard_api_response.v1",
        "response_id": response_id,
        "status": status,
        "privacy_zone": "D1_pseudonymized_score_free_api_response",
        "request_binding_sha256": input_payload["request_binding_sha256"],
        "lineage": {
            "guard_input_sha256": input_sha,
            "s107_completion_commit_sha256": s107.commit_sha256,
            "s107_threshold_policy_report_sha256": s107.threshold_report_sha256,
            "c02_completion_commit_sha256": c02.commit_sha256,
            "c02_selection_output_sha256": c02.selection_output_sha256,
            "e02_preregistration_sha256": policy.sha256,
        },
        "blocked": blocked,
        "blocked_reasons": list(reasons),
        "safe_next_action": safe_action,
        "capability_decision": capabilities,
        "evidence_provenance": dict(input_payload["evidence_provenance"]),
        "safety_receipt": {
            "c02_no_backfill_inherited": True,
            "c02_free_generation_prohibited": True,
            "model_score_present": False,
            "pii_present": False,
            "raw_path_present": False,
            "free_text_present": False,
            "advice_payload_present": False,
            "reference_payload_present": False,
            "corrective_action_payload_present": False,
            "api_executed_by_static_package": False,
            "production_release_allowed": False,
        },
        "binding_sha256": "",
    }
    response["binding_sha256"] = binding_sha256(response)
    return response


def validate_api_guard_response(
    payload: Mapping[str, Any],
    *,
    input_payload: Mapping[str, Any],
    policy: E02Policy,
    s107: S107Evidence,
    c02: C02Evidence,
) -> Mapping[str, Any]:
    """Recompute and exactly compare a score-free API guard response."""

    _reject_nonfinite_tree(payload, "E02 API response")
    _reject_api_leakage(payload, "E02 API response")
    _expect_keys(payload, RESPONSE_KEYS, "E02 API response")
    _expect_keys(
        _mapping(payload.get("lineage"), "E02 response lineage"),
        RESPONSE_LINEAGE_KEYS,
        "E02 response lineage",
    )
    _expect_keys(
        _mapping(payload.get("capability_decision"), "E02 capabilities"),
        CAPABILITY_KEYS,
        "E02 capabilities",
    )
    _expect_keys(
        _mapping(payload.get("evidence_provenance"), "E02 response provenance"),
        PROVENANCE_KEYS,
        "E02 response provenance",
    )
    _expect_keys(
        _mapping(payload.get("safety_receipt"), "E02 safety receipt"),
        SAFETY_RECEIPT_KEYS,
        "E02 safety receipt",
    )
    expected = derive_api_guard_response(
        input_payload,
        policy=policy,
        s107=s107,
        c02=c02,
    )
    if dict(payload) != dict(expected):
        raise E02ContractError(
            "E02 API response differs from deterministic score-free guard"
        )
    return payload


def ensure_future_output_targets_absent(
    *,
    workspace_root: Path,
    outputs: Mapping[str, Any],
) -> None:
    """Enforce no-overwrite without creating or resolving runtime inputs."""

    _expect(outputs.get("no_overwrite"), True, "future no-overwrite")
    _expect(outputs.get("atomic_publish_required"), True, "future atomic publish")
    _expect(
        outputs.get("write_enabled_in_this_package"),
        False,
        "static package write boundary",
    )
    schema_fields = {"guard_input_schema_path", "api_response_schema_path"}
    seen: set[str] = set()
    for field, value in outputs.items():
        if not field.endswith("_path") or field in schema_fields:
            continue
        relative = _relative_string(value, f"outputs.{field}")
        if relative in seen:
            raise E02ContractError("duplicate future E02 output target")
        seen.add(relative)
        target = _reject_symlink_chain(
            workspace_root,
            relative,
            f"outputs.{field}",
            require_leaf=False,
        )
        if target.exists() or target.is_symlink():
            raise E02ContractError(f"no-overwrite target already exists: {relative}")


def run_full_preflight(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> PreflightResult:
    """Validate predecessor evidence and stop before any E02 request/API work."""

    audit = AccessAudit()
    static = validate_static_config(
        workspace_root=workspace_root,
        config_path=config_path,
        config=config,
        audit=audit,
    )
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=static.source_snapshots,
        audit=audit,
        stage="static_source",
    )
    s107 = deep_validate_s107(
        workspace_root=workspace_root,
        config=config,
        audit=audit,
    )
    c02 = deep_validate_c02_bundle(
        workspace_root=workspace_root,
        config=config,
        s107=s107,
        audit=audit,
    )
    policy = _read_e02_preregistration(
        workspace_root=workspace_root,
        config=config,
        s107=s107,
        c02=c02,
        audit=audit,
    )
    return PreflightResult(
        status="ready_for_separately_authorized_e02_api_guard_executor",
        config_sha256=static.config_sha256,
        s107_commit_sha256=s107.commit_sha256,
        s107_threshold_report_sha256=s107.threshold_report_sha256,
        c02_completion_commit_sha256=c02.commit_sha256,
        c02_selection_output_sha256=c02.selection_output_sha256,
        e02_preregistration_sha256=policy.sha256,
        audit=audit.as_dict(),
    )


def config_only_result(
    static: StaticValidation,
    audit: AccessAudit | None = None,
) -> Mapping[str, Any]:
    access = (
        audit
        if audit is not None
        else AccessAudit(source_pin_read_count=len(static.source_snapshots))
    )
    return {
        "status": "valid_config_only",
        "implementation_status": STATIC_STATUS,
        "config_sha256": static.config_sha256,
        "policy_sha256": static.policy_sha256,
        "source_pin_count": len(static.source_snapshots),
        **access.as_dict(),
        "actual_request_coaching_reference_or_api_execution": False,
        "actual_model_gpu_metric_network_llm_execution": False,
        "artifact_created": False,
        "e02_completion_claim_allowed": False,
        "production_release_allowed": False,
        "publication_allowed": False,
    }


def failure_result(error: E02ContractError) -> Mapping[str, Any]:
    audit = error.audit if error.audit is not None else AccessAudit()
    return {
        "status": "failed_closed",
        "stage": error.stage,
        "error": str(error),
        **audit.as_dict(),
        "actual_request_coaching_reference_or_api_execution": False,
        "actual_model_gpu_metric_network_llm_execution": False,
        "artifact_created": False,
        "e02_completion_claim_allowed": False,
        "production_release_allowed": False,
        "publication_allowed": False,
    }
