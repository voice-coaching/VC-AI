"""Fail-closed C02 deterministic ontology-only coaching selection contract.

The canonical package is static and read-only.  It validates C01 first, then
requires an exact actual S107 completion pin before resolving that commit.  A
future S107 completion is deep-recomputed before a frozen C02 preregistration
may authorize an exact threshold and evidence-support matrix.

No canonical command reads diagnostic issue rows, emits coaching, estimates an
effect, calls a model/GPU/network/LLM, writes output, or claims completion.
Pure payload functions exist for synthetic tests and a separately registered
future executor.  They can emit only byte-exact C01 catalog advice or explicit
``rerecord``/``expert_review`` abstentions; free-generated prose is impossible.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
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
TBD_PREREGISTRATION = "TBD_PREREGISTRATION"
TBD_S107_COMPLETION = "TBD_S107_COMPLETION"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
EVIDENCE_IDS = (
    "gold_label",
    "ctc_competing_phone",
    "gop_low",
    "duration_short",
    "duration_long",
    "alignment_low",
    "content_mismatch",
    "prosody_outlier",
)
RANKING_TIES = (
    "start_ms",
    "end_ms",
    "issue_type",
    "target",
    "observed",
    "evidence_ids",
    "issue_id_sha256",
)
PRIORITY_FORMULA = (
    "severity*calibrated_confidence*comprehensibility_impact*trainability"
)

TOP_LEVEL_KEYS = {
    "schema_version",
    "experiment",
    "canonical_sources",
    "predecessor_c01",
    "predecessor_s107",
    "preregistration",
    "selection_contract",
    "evidence_and_ontology_contract",
    "generation_safety",
    "privacy_and_access",
    "execution_order",
    "outputs",
}
CONFIG_SECTION_KEYS = {
    "experiment": {
        "id",
        "stage",
        "implementation_status",
        "actual_diagnostic_input_access_allowed",
        "actual_coaching_generation_allowed",
        "actual_effect_estimation_allowed",
        "actual_model_access_allowed",
        "actual_gpu_access_allowed",
        "actual_network_access_allowed",
        "actual_llm_access_allowed",
        "actual_output_write_allowed",
        "actual_registry_or_commit_write_allowed",
        "production_release_allowed",
        "completion_claim_allowed",
    },
    "canonical_sources": {"access_scope", "pins"},
    "predecessor_c01": {
        "deep_validation_required",
        "catalog_path",
        "catalog_sha256",
        "schema_path",
        "schema_sha256",
        "validator_path",
        "validator_sha256",
        "prioritizer_path",
        "prioritizer_sha256",
        "validation_report_path",
        "validation_report_sha256",
        "aggregate_source_path",
        "aggregate_source_sha256",
        "required_status",
        "required_ontology_id",
        "required_tag_count",
        "required_engineering_contract_valid",
        "research_completion_claim_must_be_false",
        "advice_status",
        "production_release_allowed_must_be_false",
        "privacy_safe_aggregate_read_count",
        "row_raw_locator_environment_identifier_read_count",
    },
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
        "registry_path",
        "required_registry_status",
        "threshold_artifact_name",
        "threshold_artifact_relative_path",
        "threshold_artifact_schema_version",
        "threshold_artifact_sha256",
        "required_threshold_selection_status",
        "required_deep_status",
        "required_label_status",
        "required_calibration_only",
        "calibration_accessed_by_deep_validator",
        "train_dev_stack_locked_test_access_must_be_false",
        "raw_audio_access_must_be_false",
        "gpu_initialized_must_be_false",
        "promotion_decision_allowed_must_be_false",
        "s108_ready_must_be_false",
        "threshold_policy_status",
        "coaching_claim_allowed_by_s107",
    },
    "preregistration": {
        "placeholder",
        "template_path",
        "template_sha256",
        "approved_artifact_path",
        "approved_artifact_sha256",
        "required_schema_version",
        "required_status",
        "must_be_frozen_before_diagnostic_issue_or_output_access",
        "unresolved_fields_must_fail_closed",
    },
    "selection_contract": {
        "priority_formula",
        "allowed_top_k",
        "default_top_k",
        "ranking_tie_breakers",
        "blocked_top_k_backfill_allowed",
        "priority_arithmetic",
        "priority_output_scale",
        "priority_rounding",
        "confidence_semantics",
        "issue_confidence_aggregation_from_s107_phone_probabilities",
        "exact_confidence_threshold",
        "threshold_comparison",
        "threshold_authorization_rule",
        "low_confidence_action",
        "unsupported_claim_action",
        "empty_issue_action",
    },
    "evidence_and_ontology_contract": {
        "allowed_evidence_ids",
        "issue_type_to_supported_evidence_ids",
        "minimum_supported_evidence_count",
        "phone_localized_claim_requires_positive_overlap_receipt",
        "no_target_phone_claim_policy_must_be_preserved",
        "other_and_unknown_always_unsupported",
        "catalog_advice_id_and_message_exact_match_required",
    },
    "generation_safety": {
        "mode",
        "free_generation_llm_allowed",
        "prompt_or_completion_field_allowed",
        "network_access_allowed",
        "runtime_model_access_allowed",
        "catalog_message_rewrite_allowed",
        "catalog_advice_id_rewrite_allowed",
        "fallback_generated_text_allowed",
        "production_release_allowed_before_c03",
    },
    "privacy_and_access": {
        "diagnostic_input_zone",
        "selection_output_zone",
        "aggregate_report_zone",
        "raw_audio_allowed",
        "raw_text_or_transcript_allowed",
        "direct_identifier_allowed",
        "free_text_allowed",
        "source_or_model_path_allowed_in_payloads",
        "prompt_or_completion_allowed",
        "individual_issue_rows_allowed_in_a1",
        "request_or_issue_ids_allowed_in_a1",
        "forbidden_recursive_fields",
    },
    "outputs": {
        "input_schema_path",
        "output_schema_path",
        "selection_manifest_path",
        "aggregate_report_path",
        "commit_path",
        "registry_path",
        "failure_report_path",
        "no_overwrite",
        "atomic_publish_required",
        "write_enabled_in_this_package",
    },
}
SOURCE_PATHS = (
    "artifacts/coaching/c01_korean_error_advice_ontology_v1.validation.json",
    "configs/coaching/korean_error_advice_ontology_v1.json",
    "configs/coaching/korean_error_advice_ontology_v1.schema.json",
    "configs/coaching/c02_coaching_selection_preregistration_v1.template.json",
    "configs/coaching/c02_diagnostic_issues_input_v1.schema.json",
    "configs/coaching/c02_coaching_selection_output_v1.schema.json",
    "configs/experiments/wavlm_s107_calibration_v1.yaml",
    "data/processed/aihub71490/manifests/aihub71490_data_card.json",
    "docs/constraints/C01_KOREAN_ERROR_ADVICE_ONTOLOGY_DATA_CARD_KO.md",
    "docs/IMPLEMENTATION_PLAN_KO.md",
    "docs/constraints/WAVLM_S107_CALIBRATION_READINESS_KO.md",
    "docs/paper/EXPERIMENTAL_MANUSCRIPT_KO.md",
    "scripts/prepare_wavlm_s107_calibration.py",
    "scripts/run_wavlm_s107_calibration.py",
    "scripts/validate_korean_error_advice_ontology.py",
    "src/voice_coach/coaching/ontology.py",
    "src/voice_coach/coaching/prioritizer.py",
    "src/voice_coach/training/wavlm_s107_calibration_contract.py",
    "src/voice_coach/training/wavlm_s107_calibration_training.py",
)
EXPECTED_EXECUTION_ORDER = (
    "validate_c02_static_config_sources_prereg_and_schemas",
    "deep_validate_c01_engineering_ontology_and_immutable_report",
    "require_exact_s107_completed_commit_pin_before_path_resolution",
    "deep_validate_actual_s107_completion_and_threshold_report",
    "validate_frozen_c02_preregistration_without_tbd",
    "stop_before_diagnostic_input_coaching_model_network_or_output_path",
)

INPUT_KEYS = {
    "schema_version",
    "input_id",
    "request_id_sha256",
    "c01_validation_report_sha256",
    "s107_completion_commit_sha256",
    "s107_threshold_policy_report_sha256",
    "c02_preregistration_sha256",
    "top_k",
    "confidence_threshold",
    "confidence_semantics",
    "privacy_zone",
    "issues",
}
ISSUE_KEYS = {
    "issue_id_sha256",
    "issue_type",
    "start_ms",
    "end_ms",
    "target",
    "observed",
    "severity",
    "calibrated_confidence",
    "comprehensibility_impact",
    "trainability",
    "evidence_ids",
    "s107_calibration_row_sha256",
    "claim_evidence_sha256",
    "target_overlap_evidence_sha256",
}
OUTPUT_KEYS = {
    "schema_version",
    "output_id",
    "status",
    "input_manifest_sha256",
    "c02_config_sha256",
    "c02_preregistration_sha256",
    "c01_validation_report_sha256",
    "s107_completion_commit_sha256",
    "s107_threshold_policy_report_sha256",
    "top_k",
    "confidence_threshold",
    "priority_formula",
    "ranking_tie_breakers",
    "ranked_window_count",
    "selected_count",
    "blocked_count",
    "action",
    "ranked_window",
    "generation_contract",
    "privacy_zone",
    "production_release_allowed",
}
DECISION_KEYS = {
    "rank",
    "issue_id_sha256",
    "issue_type",
    "priority_score_decimal",
    "action",
    "abstention_reason",
    "advice_id",
    "message_ko",
    "message_sha256",
    "claim_evidence_sha256",
}
GENERATION_KEYS = {
    "mode",
    "exact_catalog_message_only",
    "free_generation_llm_used",
    "prompt_or_completion_present",
    "network_accessed",
    "runtime_model_accessed",
}
PREREG_KEYS = {
    "schema_version",
    "preregistration_id",
    "status",
    "registered_at_utc",
    "registered_by_role",
    "frozen_before_diagnostic_issue_or_coaching_output_access",
    "c01_validation_report_sha256",
    "s107_completion_commit_sha256",
    "s107_threshold_policy_report_sha256",
    "selection_policy",
    "calibration_policy",
    "evidence_policy",
    "generation_safety",
    "approval",
}

FORBIDDEN_PAYLOAD_KEYS = {
    "name",
    "email",
    "phone",
    "address",
    "legal_name",
    "raw_user_id",
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
}


class C02ContractError(ValueError):
    """Raised before an unpinned, unsupported, unsafe, or generative action."""

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
            raise C02ContractError("YAML mapping keys must be strings")
        if key in result:
            raise C02ContractError(f"duplicate YAML key: {key}")
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
    c01_validator_load_count: int = 0
    c01_deep_validation_count: int = 0
    c01_validation_report_read_count: int = 0
    c01_privacy_safe_aggregate_read_count: int = 0
    c01_row_level_read_count: int = 0
    c01_raw_read_count: int = 0
    c01_locator_read_count: int = 0
    c01_environment_read_count: int = 0
    c01_identifier_read_count: int = 0
    s107_completion_path_resolution_count: int = 0
    s107_completion_read_attempt_count: int = 0
    s107_completion_read_count: int = 0
    s107_validator_load_count: int = 0
    s107_deep_validation_count: int = 0
    s107_calibration_evidence_read_count: int = 0
    s107_threshold_report_read_count: int = 0
    s107_registry_read_count: int = 0
    preregistration_path_resolution_count: int = 0
    preregistration_read_attempt_count: int = 0
    preregistration_read_count: int = 0
    diagnostic_input_path_resolution_count: int = 0
    diagnostic_input_read_count: int = 0
    coaching_selection_count: int = 0
    coaching_output_write_count: int = 0
    effect_estimation_count: int = 0
    model_access_count: int = 0
    gpu_access_count: int = 0
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
class C01Evidence:
    report_sha256: str
    report: Mapping[str, Any]
    catalog: Mapping[str, Any]
    snapshots: tuple[FileSnapshot, ...]


@dataclass(frozen=True)
class S107Evidence:
    commit_sha256: str
    threshold_report_sha256: str
    issue_threshold: float
    threshold_selection_status: str
    commit_snapshot: FileSnapshot
    threshold_snapshot: FileSnapshot


@dataclass(frozen=True)
class PreregistrationPolicy:
    sha256: str
    confidence_threshold: float
    confidence_semantics: str
    issue_confidence_aggregation: str
    minimum_supported_evidence_count: int
    evidence_by_issue_type: Mapping[str, frozenset[str]]
    payload: Mapping[str, Any]
    snapshot: FileSnapshot | None


@dataclass(frozen=True)
class PreflightResult:
    status: str
    config_sha256: str
    c01_report_sha256: str
    s107_commit_sha256: str
    s107_threshold_report_sha256: str
    preregistration_sha256: str
    audit: Mapping[str, int]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise C02ContractError(f"{name} must be a string-keyed mapping")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise C02ContractError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise C02ContractError(f"{name} must be a non-empty string")
    return value


def _integer(
    value: object,
    name: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise C02ContractError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise C02ContractError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise C02ContractError(f"{name} must be <= {maximum}")
    return value


def _number(
    value: object,
    name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise C02ContractError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise C02ContractError(f"{name} must be finite")
    if minimum is not None and result < minimum:
        raise C02ContractError(f"{name} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise C02ContractError(f"{name} must be <= {maximum}")
    return result


def _probability(value: object, name: str) -> float:
    return _number(value, name, minimum=0.0, maximum=1.0)


def _sha256(value: object, name: str) -> str:
    text = _string(value, name)
    if not SHA256_RE.fullmatch(text):
        raise C02ContractError(f"{name} must be a lowercase SHA-256")
    return text


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise C02ContractError(f"{name} drift: expected {expected!r}, got {actual!r}")


def _expect_keys(
    value: Mapping[str, Any],
    expected: set[str],
    name: str,
) -> None:
    actual = set(value)
    if actual != expected:
        raise C02ContractError(
            f"{name} keys drift; "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )


def _reject_nonfinite_tree(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise C02ContractError(f"{name} contains non-finite number")
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
            raise C02ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise C02ContractError(f"non-finite JSON constant prohibited: {value}")


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


def _contains_tbd(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith("TBD_")
    if isinstance(value, Mapping):
        return any(_contains_tbd(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_tbd(item) for item in value)
    return False


def _collect_keys(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            result.add(str(key))
            result.update(_collect_keys(item))
    elif isinstance(value, list):
        for item in value:
            result.update(_collect_keys(item))
    return result


def _reject_sensitive_payload(value: Mapping[str, Any], name: str) -> None:
    forbidden = _collect_keys(value).intersection(FORBIDDEN_PAYLOAD_KEYS)
    if forbidden:
        raise C02ContractError(
            f"{name} contains PII/raw/path/free-generation fields: {sorted(forbidden)}"
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
        raise C02ContractError(f"{name} must be a safe workspace-relative path")
    return text


def _reject_symlink_chain(
    root: Path,
    relative: str,
    name: str,
    *,
    require_leaf: bool,
) -> Path:
    relative = _relative_string(relative, name)
    parts = PurePosixPath(relative).parts
    current = root
    for index, part in enumerate(parts):
        current = current / part
        is_leaf = index == len(parts) - 1
        try:
            info = current.lstat()
        except FileNotFoundError:
            if not require_leaf:
                return root / relative
            raise C02ContractError(f"{name} missing: {relative}") from None
        if stat.S_ISLNK(info.st_mode):
            raise C02ContractError(f"{name} symlink prohibited: {relative}")
        if not is_leaf and not stat.S_ISDIR(info.st_mode):
            raise C02ContractError(f"{name} parent is not a directory: {relative}")
        if is_leaf and require_leaf and not stat.S_ISREG(info.st_mode):
            raise C02ContractError(f"{name} must be a regular file: {relative}")
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
            raise C02ContractError(f"{name} is not a regular file")
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise C02ContractError(f"{name} changed before open")
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
        raise C02ContractError(f"{name} changed while reading")
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
        raise C02ContractError(
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
        raise C02ContractError(f"{name} invalid JSON: {exc}") from exc
    payload = _mapping(value, name)
    _reject_nonfinite_tree(payload, name)
    return payload


def load_json(path: Path) -> Mapping[str, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise C02ContractError(f"cannot read JSON {path}: {exc}") from exc
    return _load_json_bytes(data, str(path))


def _load_config_bytes(data: bytes) -> Mapping[str, Any]:
    try:
        value = yaml.load(data.decode("utf-8"), Loader=_UniqueKeyLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise C02ContractError(f"invalid YAML config: {exc}") from exc
    config = _mapping(value, "config")
    _reject_nonfinite_tree(config, "config")
    return config


def load_config(path: Path) -> Mapping[str, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise C02ContractError(f"cannot read config {path}: {exc}") from exc
    return _load_config_bytes(data)


def _read_exact_json(
    *,
    workspace_root: Path,
    relative: str,
    expected_sha256: str,
    name: str,
) -> tuple[Mapping[str, Any], FileSnapshot]:
    data, snapshot = _read_file(workspace_root, relative, name)
    if snapshot.sha256 != _sha256(expected_sha256, f"{name} expected SHA"):
        raise C02ContractError(f"{name} SHA-256 drift")
    return _load_json_bytes(data, name), snapshot


def _load_module(
    *,
    workspace_root: Path,
    relative: str,
    module_name: str,
    expected_sha256: str,
) -> ModuleType:
    path = _reject_symlink_chain(
        workspace_root,
        relative,
        module_name,
        require_leaf=True,
    )
    data, snapshot = _read_file(workspace_root, relative, module_name)
    if snapshot.sha256 != _sha256(
        expected_sha256,
        f"{module_name} expected SHA",
    ):
        raise C02ContractError(f"{module_name} SHA-256 drift before import")
    module = ModuleType(module_name)
    module.__file__ = str(path)
    module.__package__ = ""
    sys.modules[module_name] = module
    try:
        exec(compile(data, str(path), "exec"), module.__dict__)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise C02ContractError(f"cannot import {module_name}: {exc}") from exc
    return module


def _validate_input_schema(schema: Mapping[str, Any]) -> None:
    _expect(
        schema.get("$schema"),
        "https://json-schema.org/draft/2020-12/schema",
        "input schema dialect",
    )
    _expect(schema.get("type"), "object", "input schema root")
    _expect(schema.get("additionalProperties"), False, "input schema strictness")
    _expect(
        set(_list(schema.get("required"), "input schema required")),
        INPUT_KEYS,
        "input schema required fields",
    )
    properties = _mapping(schema.get("properties"), "input schema properties")
    _expect(set(properties), INPUT_KEYS, "input schema root properties")
    definitions = _mapping(schema.get("$defs"), "input schema definitions")
    issue = _mapping(definitions.get("issue"), "input issue schema")
    _expect(issue.get("additionalProperties"), False, "input issue strictness")
    _expect(
        set(_list(issue.get("required"), "input issue required")),
        ISSUE_KEYS,
        "input issue required fields",
    )
    _expect(
        set(_mapping(issue.get("properties"), "input issue properties")),
        ISSUE_KEYS,
        "input issue properties",
    )
    evidence = _mapping(definitions.get("evidenceId"), "evidence ID schema")
    _expect(
        tuple(_list(evidence.get("enum"), "evidence ID enum")),
        EVIDENCE_IDS,
        "evidence ID enum",
    )


def _validate_output_schema(schema: Mapping[str, Any]) -> None:
    _expect(
        schema.get("$schema"),
        "https://json-schema.org/draft/2020-12/schema",
        "output schema dialect",
    )
    _expect(schema.get("type"), "object", "output schema root")
    _expect(schema.get("additionalProperties"), False, "output schema strictness")
    _expect(
        set(_list(schema.get("required"), "output schema required")),
        OUTPUT_KEYS,
        "output schema required fields",
    )
    properties = _mapping(schema.get("properties"), "output schema properties")
    _expect(set(properties), OUTPUT_KEYS, "output schema root properties")
    definitions = _mapping(schema.get("$defs"), "output schema definitions")
    decision = _mapping(definitions.get("decision"), "output decision schema")
    generation = _mapping(
        definitions.get("generationContract"),
        "generation contract schema",
    )
    _expect(decision.get("additionalProperties"), False, "decision strictness")
    _expect(generation.get("additionalProperties"), False, "generation strictness")
    _expect(
        set(_list(decision.get("required"), "decision required")),
        DECISION_KEYS,
        "decision required fields",
    )
    _expect(
        set(_mapping(decision.get("properties"), "decision properties")),
        DECISION_KEYS,
        "decision properties",
    )
    _expect(
        set(_list(generation.get("required"), "generation required")),
        GENERATION_KEYS,
        "generation required fields",
    )
    _expect(
        set(_mapping(generation.get("properties"), "generation properties")),
        GENERATION_KEYS,
        "generation properties",
    )


def _validate_preregistration_template(payload: Mapping[str, Any]) -> None:
    _reject_sensitive_payload(payload, "C02 preregistration template")
    _expect_keys(payload, PREREG_KEYS, "C02 preregistration template")
    _expect(
        payload.get("schema_version"),
        "voice_coach.c02_coaching_selection_preregistration.v1",
        "C02 preregistration schema",
    )
    _expect(
        payload.get("frozen_before_diagnostic_issue_or_coaching_output_access"),
        True,
        "C02 preregistration ordering",
    )
    _sha256(
        payload.get("c01_validation_report_sha256"),
        "preregistration C01 report SHA",
    )
    _expect(
        payload.get("s107_completion_commit_sha256"),
        TBD_S107_COMPLETION,
        "preregistration S107 completion placeholder",
    )
    _expect(
        payload.get("s107_threshold_policy_report_sha256"),
        TBD_S107_COMPLETION,
        "preregistration S107 threshold placeholder",
    )
    selection = _mapping(payload.get("selection_policy"), "prereg selection")
    _expect_keys(
        selection,
        {
            "priority_formula",
            "allowed_top_k",
            "default_top_k",
            "ranking_tie_breakers",
            "blocked_top_k_backfill_allowed",
            "priority_arithmetic",
            "priority_output_scale",
            "priority_rounding",
        },
        "prereg selection",
    )
    _expect(
        selection.get("priority_formula"),
        PRIORITY_FORMULA,
        "prereg priority formula",
    )
    _expect(
        tuple(_list(selection.get("allowed_top_k"), "prereg top-k")),
        (1, 3),
        "prereg top-k",
    )
    _expect(selection.get("default_top_k"), 1, "prereg default top-k")
    _expect(
        tuple(_list(selection.get("ranking_tie_breakers"), "prereg ties")),
        RANKING_TIES,
        "prereg ranking ties",
    )
    _expect(
        selection.get("blocked_top_k_backfill_allowed"),
        False,
        "prereg no-backfill",
    )
    _expect(
        selection.get("priority_arithmetic"),
        "base10_decimal_from_canonical_json_numbers",
        "prereg priority arithmetic",
    )
    _expect(selection.get("priority_output_scale"), 12, "priority output scale")
    _expect(
        selection.get("priority_rounding"),
        "decimal_round_half_even",
        "priority rounding",
    )
    calibration = _mapping(
        payload.get("calibration_policy"),
        "prereg calibration",
    )
    _expect_keys(
        calibration,
        {
            "confidence_semantics",
            "issue_confidence_aggregation_from_s107_phone_probabilities",
            "exact_confidence_threshold",
            "threshold_comparison",
            "threshold_authorization_rule",
            "low_confidence_action",
            "s107_provisional_threshold_accepted_without_preregistration",
        },
        "prereg calibration",
    )
    _expect(
        calibration.get("threshold_comparison"),
        "greater_than_or_equal",
        "threshold comparison",
    )
    _expect(
        calibration.get("low_confidence_action"),
        "rerecord",
        "low-confidence action",
    )
    _expect(
        calibration.get("s107_provisional_threshold_accepted_without_preregistration"),
        False,
        "S107 provisional threshold guard",
    )
    evidence = _mapping(payload.get("evidence_policy"), "prereg evidence")
    _expect_keys(
        evidence,
        {
            "allowed_evidence_ids",
            "issue_type_to_supported_evidence_ids",
            "minimum_supported_evidence_count",
            "positive_target_overlap_receipt_required_for_phone_localized_claim",
            "unsupported_claim_action",
            "unknown_issue_type_action",
            "other_issue_type_action",
        },
        "prereg evidence",
    )
    _expect(
        tuple(_list(evidence.get("allowed_evidence_ids"), "evidence IDs")),
        EVIDENCE_IDS,
        "evidence IDs",
    )
    _expect(
        evidence.get(
            "positive_target_overlap_receipt_required_for_phone_localized_claim"
        ),
        True,
        "target-overlap evidence",
    )
    _expect(
        evidence.get("unsupported_claim_action"),
        "expert_review",
        "unsupported action",
    )
    _expect(
        evidence.get("unknown_issue_type_action"),
        "expert_review",
        "unknown action",
    )
    _expect(
        evidence.get("other_issue_type_action"),
        "expert_review",
        "other action",
    )
    safety = _mapping(payload.get("generation_safety"), "prereg safety")
    _expect_keys(
        safety,
        {
            "message_source",
            "free_generation_llm_allowed",
            "prompt_or_completion_field_allowed",
            "network_access_allowed",
            "runtime_model_access_allowed",
            "catalog_message_rewrite_allowed",
            "catalog_advice_id_rewrite_allowed",
            "production_release_allowed_before_c03",
        },
        "prereg safety",
    )
    _expect(
        safety.get("message_source"),
        "exact_c01_catalog_entry_only",
        "message source",
    )
    for field in (
        "free_generation_llm_allowed",
        "prompt_or_completion_field_allowed",
        "network_access_allowed",
        "runtime_model_access_allowed",
        "catalog_message_rewrite_allowed",
        "catalog_advice_id_rewrite_allowed",
        "production_release_allowed_before_c03",
    ):
        _expect(safety.get(field), False, f"prereg safety.{field}")
    approval = _mapping(payload.get("approval"), "prereg approval")
    _expect_keys(
        approval,
        {"decision", "protocol_sha256", "signed_receipt_sha256"},
        "prereg approval",
    )
    if not _contains_tbd(payload):
        raise C02ContractError("preregistration template must retain TBD values")


def _validate_config_values(config: Mapping[str, Any]) -> None:
    _expect_keys(config, TOP_LEVEL_KEYS, "C02 config")
    _expect(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "schema_version")
    for section_name, expected_keys in CONFIG_SECTION_KEYS.items():
        _expect_keys(
            _mapping(config.get(section_name), section_name),
            expected_keys,
            section_name,
        )
    experiment = _mapping(config.get("experiment"), "experiment")
    _expect(
        experiment.get("id"),
        "c02_deterministic_coaching_selection_v1",
        "experiment ID",
    )
    _expect(
        experiment.get("stage"),
        "wp6_c02_deterministic_top1_top3_coaching",
        "experiment stage",
    )
    _expect(
        experiment.get("implementation_status"),
        STATIC_STATUS,
        "implementation status",
    )
    for field in (
        "actual_diagnostic_input_access_allowed",
        "actual_coaching_generation_allowed",
        "actual_effect_estimation_allowed",
        "actual_model_access_allowed",
        "actual_gpu_access_allowed",
        "actual_network_access_allowed",
        "actual_llm_access_allowed",
        "actual_output_write_allowed",
        "actual_registry_or_commit_write_allowed",
        "production_release_allowed",
        "completion_claim_allowed",
    ):
        _expect(experiment.get(field), False, f"experiment.{field}")

    c01 = _mapping(config.get("predecessor_c01"), "predecessor_c01")
    _expect(c01.get("deep_validation_required"), True, "C01 deep validation")
    for field in (
        "catalog_sha256",
        "schema_sha256",
        "validator_sha256",
        "prioritizer_sha256",
        "validation_report_sha256",
        "aggregate_source_sha256",
    ):
        _sha256(c01.get(field), f"predecessor_c01.{field}")
    _expect(c01.get("required_status"), "valid", "C01 status")
    _expect(
        c01.get("required_ontology_id"),
        "aihub71490-korean-error-advice-v1",
        "C01 ontology ID",
    )
    _expect(c01.get("required_tag_count"), 17, "C01 tag count")
    _expect(
        c01.get("required_engineering_contract_valid"),
        True,
        "C01 engineering contract",
    )
    _expect(
        c01.get("research_completion_claim_must_be_false"),
        True,
        "C01 research boundary",
    )
    _expect(c01.get("advice_status"), "engineering_draft", "C01 advice status")
    _expect(
        c01.get("production_release_allowed_must_be_false"),
        True,
        "C01 production boundary",
    )
    _expect(c01.get("privacy_safe_aggregate_read_count"), 1, "C01 aggregate reads")
    _expect(
        c01.get("row_raw_locator_environment_identifier_read_count"),
        0,
        "C01 protected reads",
    )

    s107 = _mapping(config.get("predecessor_s107"), "predecessor_s107")
    _expect(s107.get("deep_validation_required"), True, "S107 deep validation")
    _expect(
        s107.get("actual_completed_output_required"),
        True,
        "S107 actual completion",
    )
    for field in (
        "config_sha256",
        "contract_sha256",
        "completion_validator_sha256",
    ):
        _sha256(s107.get(field), f"predecessor_s107.{field}")
    for field in ("completion_commit_sha256", "threshold_artifact_sha256"):
        value = _string(s107.get(field), f"predecessor_s107.{field}")
        if value != TBD_S107_COMPLETION:
            _sha256(value, f"predecessor_s107.{field}")
    _expect(
        s107.get("completion_validator_symbol"),
        "validate_s107_commit",
        "S107 validator symbol",
    )
    _expect(
        s107.get("required_commit_schema_version"),
        "voice_coach.wavlm_s107_calibration_commit.v1",
        "S107 commit schema",
    )
    _expect(
        s107.get("threshold_artifact_name"),
        "threshold_policy_report",
        "S107 threshold artifact name",
    )
    _expect(
        s107.get("threshold_artifact_relative_path"),
        "threshold_policy_report.json",
        "S107 threshold path",
    )
    _expect(
        s107.get("threshold_artifact_schema_version"),
        "voice_coach.wavlm_s107_threshold_policy.v1",
        "S107 threshold schema",
    )
    _expect(
        s107.get("required_threshold_selection_status"),
        "selected_provisional_engineering",
        "S107 threshold selection status",
    )
    _expect(s107.get("required_deep_status"), "valid", "S107 deep status")
    _expect(
        s107.get("required_label_status"),
        "silver_oracle_provisional",
        "S107 label status",
    )
    for field in (
        "required_calibration_only",
        "calibration_accessed_by_deep_validator",
        "train_dev_stack_locked_test_access_must_be_false",
        "raw_audio_access_must_be_false",
        "gpu_initialized_must_be_false",
        "promotion_decision_allowed_must_be_false",
        "s108_ready_must_be_false",
    ):
        _expect(s107.get(field), True, f"S107.{field}")
    _expect(
        s107.get("threshold_policy_status"),
        "provisional_engineering_not_promotion",
        "S107 threshold status",
    )
    _expect(
        s107.get("coaching_claim_allowed_by_s107"),
        False,
        "S107 coaching boundary",
    )

    prereg = _mapping(config.get("preregistration"), "preregistration")
    _expect(
        prereg.get("placeholder"),
        TBD_PREREGISTRATION,
        "preregistration placeholder",
    )
    _sha256(prereg.get("template_sha256"), "preregistration template SHA")
    approved_sha = _string(
        prereg.get("approved_artifact_sha256"),
        "approved preregistration SHA",
    )
    if approved_sha != TBD_PREREGISTRATION:
        _sha256(approved_sha, "approved preregistration SHA")
    _expect(
        prereg.get("required_schema_version"),
        "voice_coach.c02_coaching_selection_preregistration.v1",
        "preregistration schema",
    )
    _expect(
        prereg.get("required_status"),
        "approved_and_frozen",
        "preregistration status",
    )
    _expect(
        prereg.get("must_be_frozen_before_diagnostic_issue_or_output_access"),
        True,
        "preregistration ordering",
    )
    _expect(
        prereg.get("unresolved_fields_must_fail_closed"),
        True,
        "preregistration TBD policy",
    )

    selection = _mapping(config.get("selection_contract"), "selection_contract")
    _expect(selection.get("priority_formula"), PRIORITY_FORMULA, "priority formula")
    _expect(
        tuple(_list(selection.get("allowed_top_k"), "allowed top-k")),
        (1, 3),
        "allowed top-k",
    )
    _expect(selection.get("default_top_k"), 1, "default top-k")
    _expect(
        tuple(_list(selection.get("ranking_tie_breakers"), "ranking ties")),
        RANKING_TIES,
        "ranking ties",
    )
    _expect(
        selection.get("blocked_top_k_backfill_allowed"),
        False,
        "no-backfill rule",
    )
    _expect(
        selection.get("priority_arithmetic"),
        "base10_decimal_from_canonical_json_numbers",
        "priority arithmetic",
    )
    _expect(selection.get("priority_output_scale"), 12, "priority scale")
    _expect(
        selection.get("priority_rounding"),
        "decimal_round_half_even",
        "priority rounding",
    )
    for field in (
        "confidence_semantics",
        "issue_confidence_aggregation_from_s107_phone_probabilities",
        "exact_confidence_threshold",
        "threshold_authorization_rule",
    ):
        _expect(
            selection.get(field),
            TBD_PREREGISTRATION,
            f"selection.{field}",
        )
    _expect(
        selection.get("threshold_comparison"),
        "greater_than_or_equal",
        "threshold comparison",
    )
    _expect(selection.get("low_confidence_action"), "rerecord", "low confidence")
    _expect(
        selection.get("unsupported_claim_action"),
        "expert_review",
        "unsupported action",
    )
    _expect(selection.get("empty_issue_action"), "none", "empty issue action")

    evidence = _mapping(
        config.get("evidence_and_ontology_contract"),
        "evidence contract",
    )
    _expect(
        tuple(_list(evidence.get("allowed_evidence_ids"), "evidence IDs")),
        EVIDENCE_IDS,
        "evidence IDs",
    )
    _expect(
        evidence.get("issue_type_to_supported_evidence_ids"),
        TBD_PREREGISTRATION,
        "evidence support matrix",
    )
    _expect(
        evidence.get("minimum_supported_evidence_count"),
        TBD_PREREGISTRATION,
        "minimum supported evidence",
    )
    for field in (
        "phone_localized_claim_requires_positive_overlap_receipt",
        "no_target_phone_claim_policy_must_be_preserved",
        "other_and_unknown_always_unsupported",
        "catalog_advice_id_and_message_exact_match_required",
    ):
        _expect(evidence.get(field), True, f"evidence.{field}")

    safety = _mapping(config.get("generation_safety"), "generation safety")
    _expect(
        safety.get("mode"),
        "deterministic_c01_ontology_allowlist_only",
        "generation mode",
    )
    for field in (
        "free_generation_llm_allowed",
        "prompt_or_completion_field_allowed",
        "network_access_allowed",
        "runtime_model_access_allowed",
        "catalog_message_rewrite_allowed",
        "catalog_advice_id_rewrite_allowed",
        "fallback_generated_text_allowed",
        "production_release_allowed_before_c03",
    ):
        _expect(safety.get(field), False, f"generation safety.{field}")

    privacy = _mapping(config.get("privacy_and_access"), "privacy")
    _expect(
        privacy.get("diagnostic_input_zone"),
        "D1_pseudonymized_restricted_experiment",
        "input privacy zone",
    )
    _expect(
        privacy.get("selection_output_zone"),
        "D1_pseudonymized_restricted_experiment",
        "output privacy zone",
    )
    _expect(
        privacy.get("aggregate_report_zone"),
        "A1_aggregate_only",
        "aggregate privacy zone",
    )
    for field in (
        "raw_audio_allowed",
        "raw_text_or_transcript_allowed",
        "direct_identifier_allowed",
        "free_text_allowed",
        "source_or_model_path_allowed_in_payloads",
        "prompt_or_completion_allowed",
        "individual_issue_rows_allowed_in_a1",
        "request_or_issue_ids_allowed_in_a1",
    ):
        _expect(privacy.get(field), False, f"privacy.{field}")
    _expect(
        set(_list(privacy.get("forbidden_recursive_fields"), "forbidden fields")),
        FORBIDDEN_PAYLOAD_KEYS,
        "forbidden field set",
    )
    _expect(
        tuple(_list(config.get("execution_order"), "execution order")),
        EXPECTED_EXECUTION_ORDER,
        "execution order",
    )
    outputs = _mapping(config.get("outputs"), "outputs")
    _expect(outputs.get("no_overwrite"), True, "output no-overwrite")
    _expect(outputs.get("atomic_publish_required"), True, "output atomic publish")
    _expect(
        outputs.get("write_enabled_in_this_package"),
        False,
        "output write boundary",
    )
    for field, value in outputs.items():
        if field.endswith("_path"):
            _relative_string(value, f"outputs.{field}")


def validate_static_config(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    audit: AccessAudit | None = None,
) -> StaticValidation:
    """Validate only immutable C1/static inputs and unresolved templates."""

    root = workspace_root.resolve(strict=True)
    access = audit if audit is not None else AccessAudit()
    _validate_config_values(config)
    absolute_config = config_path if config_path.is_absolute() else root / config_path
    try:
        config_relative = absolute_config.relative_to(root).as_posix()
    except ValueError as exc:
        raise C02ContractError("C02 config must be inside workspace") from exc
    config_data, config_snapshot = _read_file(root, config_relative, "C02 config")
    if _load_config_bytes(config_data) != config:
        raise C02ContractError("C02 config changed between load and validation")

    canonical = _mapping(config.get("canonical_sources"), "canonical_sources")
    _expect(
        canonical.get("access_scope"),
        "C1_public_or_aggregate_static_pins_only",
        "canonical source access scope",
    )
    pins = _list(canonical.get("pins"), "canonical source pins")
    if len(pins) != len(SOURCE_PATHS):
        raise C02ContractError("canonical source pin count drift")
    snapshots: list[FileSnapshot] = []
    payload_by_path: dict[str, bytes] = {}
    seen: set[str] = set()
    for index, raw_pin in enumerate(pins):
        pin = _mapping(raw_pin, f"canonical source pin {index}")
        _expect_keys(pin, {"path", "sha256"}, f"canonical source pin {index}")
        relative = _relative_string(pin.get("path"), f"source pin {index} path")
        expected_sha = _sha256(pin.get("sha256"), f"source pin {index} SHA")
        if relative in seen:
            raise C02ContractError(f"duplicate canonical source pin: {relative}")
        seen.add(relative)
        data, snapshot = _read_file(root, relative, f"source pin {relative}")
        access.source_pin_read_count += 1
        if snapshot.sha256 != expected_sha:
            raise C02ContractError(
                f"canonical source SHA-256 drift: {relative}",
                stage="source_pin",
                audit=access,
            )
        snapshots.append(snapshot)
        payload_by_path[relative] = data
    if tuple(sorted(seen)) != tuple(sorted(SOURCE_PATHS)):
        raise C02ContractError("canonical source path set drift")
    by_path = {snapshot.relative_path: snapshot for snapshot in snapshots}

    input_schema_path = "configs/coaching/c02_diagnostic_issues_input_v1.schema.json"
    output_schema_path = "configs/coaching/c02_coaching_selection_output_v1.schema.json"
    prereg_template_path = (
        "configs/coaching/c02_coaching_selection_preregistration_v1.template.json"
    )
    _validate_input_schema(
        _load_json_bytes(payload_by_path[input_schema_path], "C02 input schema")
    )
    _validate_output_schema(
        _load_json_bytes(payload_by_path[output_schema_path], "C02 output schema")
    )
    _validate_preregistration_template(
        _load_json_bytes(
            payload_by_path[prereg_template_path],
            "C02 preregistration template",
        )
    )

    c01 = _mapping(config.get("predecessor_c01"), "predecessor_c01")
    for path_field, sha_field, expected_path in (
        (
            "catalog_path",
            "catalog_sha256",
            "configs/coaching/korean_error_advice_ontology_v1.json",
        ),
        (
            "schema_path",
            "schema_sha256",
            "configs/coaching/korean_error_advice_ontology_v1.schema.json",
        ),
        (
            "validator_path",
            "validator_sha256",
            "src/voice_coach/coaching/ontology.py",
        ),
        (
            "prioritizer_path",
            "prioritizer_sha256",
            "src/voice_coach/coaching/prioritizer.py",
        ),
        (
            "validation_report_path",
            "validation_report_sha256",
            "artifacts/coaching/c01_korean_error_advice_ontology_v1.validation.json",
        ),
        (
            "aggregate_source_path",
            "aggregate_source_sha256",
            "data/processed/aihub71490/manifests/aihub71490_data_card.json",
        ),
    ):
        _expect(c01.get(path_field), expected_path, f"C01 {path_field}")
        _expect(
            c01.get(sha_field),
            by_path[expected_path].sha256,
            f"C01 {sha_field}/source binding",
        )

    s107 = _mapping(config.get("predecessor_s107"), "predecessor_s107")
    for path_field, sha_field, expected_path in (
        (
            "config_path",
            "config_sha256",
            "configs/experiments/wavlm_s107_calibration_v1.yaml",
        ),
        (
            "contract_path",
            "contract_sha256",
            "src/voice_coach/training/wavlm_s107_calibration_contract.py",
        ),
        (
            "completion_validator_path",
            "completion_validator_sha256",
            "src/voice_coach/training/wavlm_s107_calibration_training.py",
        ),
    ):
        _expect(s107.get(path_field), expected_path, f"S107 {path_field}")
        _expect(
            s107.get(sha_field),
            by_path[expected_path].sha256,
            f"S107 {sha_field}/source binding",
        )
    pinned_s107 = _load_config_bytes(
        payload_by_path["configs/experiments/wavlm_s107_calibration_v1.yaml"]
    )
    s107_outputs = _mapping(pinned_s107.get("outputs"), "pinned S107 outputs")
    _expect(
        s107.get("completion_commit_path"),
        s107_outputs.get("commit_manifest"),
        "C02/S107 commit path binding",
    )
    registry_root = _relative_string(
        s107_outputs.get("registry_root"),
        "pinned S107 registry root",
    )
    registry_id = _string(
        s107_outputs.get("planned_registry_run_id"),
        "pinned S107 registry ID",
    )
    expected_registry = f"{registry_root}/{registry_id}/metadata.json"
    _expect(
        s107.get("registry_path"),
        expected_registry,
        "C02/S107 registry path binding",
    )
    artifact_contract = _list(
        s107_outputs.get("artifact_contract"),
        "pinned S107 artifact contract",
    )
    threshold_rows = [
        _mapping(row, "S107 threshold artifact")
        for row in artifact_contract
        if isinstance(row, Mapping)
        and row.get("name") == s107.get("threshold_artifact_name")
    ]
    if len(threshold_rows) != 1:
        raise C02ContractError("S107 threshold artifact contract must be unique")
    _expect(
        threshold_rows[0].get("relative_path"),
        s107.get("threshold_artifact_relative_path"),
        "C02/S107 threshold path binding",
    )
    _expect(
        threshold_rows[0].get("access_scope"),
        "A1_aggregate",
        "S107 threshold report access scope",
    )

    prereg = _mapping(config.get("preregistration"), "preregistration")
    _expect(
        prereg.get("template_path"),
        prereg_template_path,
        "preregistration template path",
    )
    _expect(
        prereg.get("template_sha256"),
        by_path[prereg_template_path].sha256,
        "preregistration template/source binding",
    )
    outputs = _mapping(config.get("outputs"), "outputs")
    _expect(outputs.get("input_schema_path"), input_schema_path, "input schema path")
    _expect(
        outputs.get("output_schema_path"),
        output_schema_path,
        "output schema path",
    )

    schema_fields = {"input_schema_path", "output_schema_path"}
    writable_outputs = [
        _relative_string(value, f"outputs.{field}")
        for field, value in outputs.items()
        if field.endswith("_path") and field not in schema_fields
    ]
    if len(writable_outputs) != len(set(writable_outputs)):
        raise C02ContractError("C02 output paths must be unique")
    for output in writable_outputs:
        output_parts = PurePosixPath(output).parts
        for source in seen:
            source_parts = PurePosixPath(source).parts
            if (
                output_parts == source_parts
                or output_parts[: len(source_parts)] == source_parts
                or source_parts[: len(output_parts)] == output_parts
            ):
                raise C02ContractError(
                    f"C02 output/source overlap prohibited: {output} / {source}"
                )

    policy_payload = {
        "selection_contract": config["selection_contract"],
        "evidence_and_ontology_contract": config["evidence_and_ontology_contract"],
        "generation_safety": config["generation_safety"],
        "privacy_and_access": config["privacy_and_access"],
        "execution_order": config["execution_order"],
    }
    return StaticValidation(
        config_sha256=config_snapshot.sha256,
        policy_sha256=hashlib.sha256(canonical_json_bytes(policy_payload)).hexdigest(),
        source_snapshots=tuple(snapshots),
    )


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
        except C02ContractError as exc:
            raise C02ContractError(
                str(exc),
                stage=f"{stage}_toctou",
                audit=audit,
            ) from exc
        audit.source_toctou_verification_count += 1


def deep_validate_c01(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> C01Evidence:
    """Recompute C01 from its one allowed aggregate and match its stored report."""

    root = workspace_root.resolve(strict=True)
    section = _mapping(config.get("predecessor_c01"), "predecessor_c01")
    module = _load_module(
        workspace_root=root,
        relative=_relative_string(section.get("validator_path"), "C01 validator path"),
        module_name="_voice_coach_c02_pinned_c01_validator",
        expected_sha256=_sha256(
            section.get("validator_sha256"),
            "C01 validator SHA",
        ),
    )
    audit.c01_validator_load_count += 1
    validator = getattr(module, "validate_ontology_bundle", None)
    if not callable(validator):
        raise C02ContractError(
            "pinned C01 validator symbol missing",
            stage="c01_deep_validation",
            audit=audit,
        )
    try:
        computed = validator(
            workspace_root=root,
            catalog_relative_path=_relative_string(
                section.get("catalog_path"),
                "C01 catalog path",
            ),
            schema_relative_path=_relative_string(
                section.get("schema_path"),
                "C01 schema path",
            ),
        )
    except Exception as exc:
        raise C02ContractError(
            f"C01 deep validation failed: {exc}",
            stage="c01_deep_validation",
            audit=audit,
        ) from exc
    audit.c01_deep_validation_count += 1
    report = _mapping(computed, "computed C01 report")
    data_access = _mapping(report.get("data_access"), "C01 data access")
    audit.c01_privacy_safe_aggregate_read_count += _integer(
        data_access.get("privacy_safe_aggregate_files_read"),
        "C01 aggregate reads",
        minimum=0,
    )
    for report_field, audit_field in (
        ("row_level_files_read", "c01_row_level_read_count"),
        ("raw_files_read", "c01_raw_read_count"),
        ("locator_files_read", "c01_locator_read_count"),
        ("environment_files_read", "c01_environment_read_count"),
        ("identifier_values_read", "c01_identifier_read_count"),
    ):
        setattr(
            audit,
            audit_field,
            getattr(audit, audit_field)
            + _integer(data_access.get(report_field), f"C01 {report_field}", minimum=0),
        )
    if any(
        getattr(audit, field)
        for field in (
            "c01_row_level_read_count",
            "c01_raw_read_count",
            "c01_locator_read_count",
            "c01_environment_read_count",
            "c01_identifier_read_count",
        )
    ):
        raise C02ContractError(
            "C01 protected read count must remain zero",
            stage="c01_deep_validation",
            audit=audit,
        )
    _expect(
        audit.c01_privacy_safe_aggregate_read_count,
        section.get("privacy_safe_aggregate_read_count"),
        "C01 aggregate read count",
    )

    stored, report_snapshot = _read_exact_json(
        workspace_root=root,
        relative=_relative_string(
            section.get("validation_report_path"),
            "C01 validation report path",
        ),
        expected_sha256=_sha256(
            section.get("validation_report_sha256"),
            "C01 validation report SHA",
        ),
        name="C01 validation report",
    )
    audit.c01_validation_report_read_count += 1
    if dict(report) != dict(stored):
        raise C02ContractError(
            "C01 deep recomputation differs from immutable report",
            stage="c01_deep_validation",
            audit=audit,
        )
    _expect(stored.get("status"), section.get("required_status"), "C01 status")
    _expect(
        stored.get("ontology_id"),
        section.get("required_ontology_id"),
        "C01 ontology ID",
    )
    observed = _mapping(stored.get("observed_support"), "C01 observed support")
    _expect(
        observed.get("tag_count"),
        section.get("required_tag_count"),
        "C01 tag count",
    )
    completion = _mapping(stored.get("completion"), "C01 completion")
    _expect(
        completion.get("engineering_contract_valid"),
        section.get("required_engineering_contract_valid"),
        "C01 engineering contract",
    )
    _expect(
        completion.get("c01_research_completion_claimed"),
        False,
        "C01 research completion boundary",
    )
    advice_boundary = _mapping(stored.get("advice_boundary"), "C01 advice boundary")
    _expect(
        advice_boundary.get("advice_status"),
        section.get("advice_status"),
        "C01 advice status",
    )
    _expect(
        advice_boundary.get("production_release_allowed"),
        False,
        "C01 production boundary",
    )
    catalog, catalog_snapshot = _read_exact_json(
        workspace_root=root,
        relative=_relative_string(section.get("catalog_path"), "C01 catalog path"),
        expected_sha256=_sha256(section.get("catalog_sha256"), "C01 catalog SHA"),
        name="C01 catalog",
    )
    tags = _list(catalog.get("tags"), "C01 catalog tags")
    if len(tags) != _integer(section.get("required_tag_count"), "C01 tag count"):
        raise C02ContractError("C01 catalog tag count drift")
    tag_ids = [
        _string(_mapping(tag, "C01 catalog tag").get("id"), "C01 tag ID")
        for tag in tags
    ]
    if len(tag_ids) != len(set(tag_ids)):
        raise C02ContractError("C01 catalog tag IDs must be unique")
    snapshots = (report_snapshot, catalog_snapshot)
    _verify_snapshots(
        workspace_root=root,
        snapshots=snapshots,
        audit=audit,
        stage="c01_deep_validation",
    )
    return C01Evidence(
        report_sha256=report_snapshot.sha256,
        report=stored,
        catalog=catalog,
        snapshots=snapshots,
    )


def deep_validate_s107(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> S107Evidence:
    """Validate a future, exactly pinned S107 completion and threshold report."""

    root = workspace_root.resolve(strict=True)
    section = _mapping(config.get("predecessor_s107"), "predecessor_s107")
    commit_sha = _string(
        section.get("completion_commit_sha256"),
        "S107 completion commit SHA",
    )
    threshold_sha = _string(
        section.get("threshold_artifact_sha256"),
        "S107 threshold report SHA",
    )
    if commit_sha == TBD_S107_COMPLETION or threshold_sha == TBD_S107_COMPLETION:
        raise C02ContractError(
            "S107 completion commit is TBD_S107_COMPLETION",
            stage="s107_completion",
            audit=audit,
        )
    _sha256(commit_sha, "S107 completion commit SHA")
    _sha256(threshold_sha, "S107 threshold report SHA")

    audit.s107_completion_path_resolution_count += 1
    audit.s107_completion_read_attempt_count += 1
    try:
        commit, commit_snapshot = _read_exact_json(
            workspace_root=root,
            relative=_relative_string(
                section.get("completion_commit_path"),
                "S107 completion commit path",
            ),
            expected_sha256=commit_sha,
            name="S107 completion commit",
        )
    except C02ContractError as exc:
        raise C02ContractError(
            f"S107 completion unavailable: {exc}",
            stage="s107_completion",
            audit=audit,
        ) from exc
    audit.s107_completion_read_count += 1
    _expect(
        commit.get("schema_version"),
        section.get("required_commit_schema_version"),
        "S107 commit schema",
    )
    _expect(commit.get("status"), "committed", "S107 commit status")
    _expect(
        commit.get("label_status"),
        section.get("required_label_status"),
        "S107 commit label status",
    )
    _expect(
        commit.get("promotion_decision_allowed"),
        False,
        "S107 promotion boundary",
    )
    _expect(commit.get("s108_ready"), False, "S107 S108 boundary")

    module = _load_module(
        workspace_root=root,
        relative=_relative_string(
            section.get("completion_validator_path"),
            "S107 completion validator path",
        ),
        module_name="_voice_coach_c02_pinned_s107_validator",
        expected_sha256=_sha256(
            section.get("completion_validator_sha256"),
            "S107 completion validator SHA",
        ),
    )
    audit.s107_validator_load_count += 1
    symbol = _string(section.get("completion_validator_symbol"), "S107 symbol")
    validator = getattr(module, symbol, None)
    if not callable(validator):
        raise C02ContractError(
            "pinned S107 completion validator symbol missing",
            stage="s107_completion",
            audit=audit,
        )
    try:
        validation = _mapping(
            validator(
                workspace_root=root,
                config_path=root
                / _relative_string(section.get("config_path"), "S107 config path"),
            ),
            "S107 deep validation",
        )
    except Exception as exc:
        raise C02ContractError(
            f"S107 deep validation failed: {exc}",
            stage="s107_completion",
            audit=audit,
        ) from exc
    audit.s107_deep_validation_count += 1
    audit.s107_calibration_evidence_read_count += 1
    _expect(
        validation.get("status"),
        section.get("required_deep_status"),
        "S107 deep status",
    )
    _expect(
        validation.get("commit_sha256"),
        commit_snapshot.sha256,
        "S107 deep commit binding",
    )
    _expect(
        validation.get("label_status"),
        section.get("required_label_status"),
        "S107 deep label status",
    )
    _expect(validation.get("calibration_only"), True, "S107 calibration-only")
    _expect(validation.get("calibration_accessed"), True, "S107 calibration access")
    for field in (
        "train_accessed",
        "dev_accessed",
        "stack_accessed",
        "locked_test_accessed",
        "raw_audio_accessed",
        "gpu_initialized",
        "promotion_decision_allowed",
        "s108_ready",
    ):
        _expect(validation.get(field), False, f"S107 deep {field}")
    issue_threshold = _probability(
        validation.get("issue_threshold"),
        "S107 selected issue threshold",
    )

    generation_path = _relative_string(
        commit.get("generation_path"),
        "S107 generation path",
    )
    threshold_relative = (
        f"{generation_path}/"
        f"{_relative_string(section.get('threshold_artifact_relative_path'), 'S107 threshold filename')}"
    )
    artifact_rows = [
        _mapping(row, "S107 artifact record")
        for row in _list(commit.get("artifacts"), "S107 artifacts")
        if isinstance(row, Mapping)
        and row.get("name") == section.get("threshold_artifact_name")
    ]
    if len(artifact_rows) != 1:
        raise C02ContractError(
            "S107 threshold artifact record must be unique",
            stage="s107_completion",
            audit=audit,
        )
    threshold_record = artifact_rows[0]
    _expect(
        threshold_record.get("path"),
        section.get("threshold_artifact_relative_path"),
        "S107 threshold artifact relative path",
    )
    _expect(
        threshold_record.get("sha256"),
        threshold_sha,
        "S107 threshold artifact/expected SHA binding",
    )
    audit.s107_threshold_report_read_count += 1
    threshold_report, threshold_snapshot = _read_exact_json(
        workspace_root=root,
        relative=threshold_relative,
        expected_sha256=threshold_sha,
        name="S107 threshold policy report",
    )
    _expect(
        threshold_report.get("schema_version"),
        section.get("threshold_artifact_schema_version"),
        "S107 threshold report schema",
    )
    _expect(
        threshold_report.get("policy_status"),
        section.get("threshold_policy_status"),
        "S107 threshold policy status",
    )
    threshold_selection_status = _string(
        threshold_report.get("status"),
        "S107 threshold selection status",
    )
    _expect(
        threshold_selection_status,
        section.get("required_threshold_selection_status"),
        "S107 threshold selection status",
    )
    _expect(
        _probability(
            threshold_report.get("selected_issue_threshold"),
            "S107 threshold report threshold",
        ),
        issue_threshold,
        "S107 threshold/deep binding",
    )
    _expect(
        threshold_report.get("promotion_gate_use_allowed"),
        False,
        "S107 threshold promotion boundary",
    )
    _expect(
        threshold_report.get("s108_readiness_claim_allowed"),
        False,
        "S107 threshold S108 boundary",
    )
    registry = _mapping(commit.get("registry"), "S107 commit registry")
    _expect(
        registry.get("status"),
        section.get("required_registry_status"),
        "S107 registry status",
    )
    _expect(
        registry.get("sha256"),
        validation.get("registry_sha256"),
        "S107 registry/deep binding",
    )
    audit.s107_registry_read_count += 1
    snapshots = (commit_snapshot, threshold_snapshot)
    _verify_snapshots(
        workspace_root=root,
        snapshots=snapshots,
        audit=audit,
        stage="s107_completion",
    )
    return S107Evidence(
        commit_sha256=commit_snapshot.sha256,
        threshold_report_sha256=threshold_snapshot.sha256,
        issue_threshold=issue_threshold,
        threshold_selection_status=threshold_selection_status,
        commit_snapshot=commit_snapshot,
        threshold_snapshot=threshold_snapshot,
    )


def _catalog_tags(catalog: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    tags: dict[str, Mapping[str, Any]] = {}
    for index, raw_tag in enumerate(_list(catalog.get("tags"), "C01 catalog tags")):
        tag = _mapping(raw_tag, f"C01 catalog tag {index}")
        tag_id = _string(tag.get("id"), f"C01 catalog tag {index} ID")
        if not SAFE_ID_RE.fullmatch(tag_id) or len(tag_id) > 64:
            raise C02ContractError(f"unsafe C01 catalog tag ID: {tag_id}")
        if tag_id in tags:
            raise C02ContractError(f"duplicate C01 catalog tag ID: {tag_id}")
        tags[tag_id] = tag
    return tags


def validate_preregistration_payload(
    payload: Mapping[str, Any],
    *,
    c01_report_sha256: str,
    s107: S107Evidence,
    catalog: Mapping[str, Any],
) -> PreregistrationPolicy:
    """Validate a fully approved, frozen C02 policy without any unresolved value."""

    _reject_nonfinite_tree(payload, "C02 preregistration")
    _reject_sensitive_payload(payload, "C02 preregistration")
    _expect_keys(payload, PREREG_KEYS, "C02 preregistration")
    if _contains_tbd(payload):
        raise C02ContractError("approved C02 preregistration contains TBD value")
    _expect(
        payload.get("schema_version"),
        "voice_coach.c02_coaching_selection_preregistration.v1",
        "C02 preregistration schema",
    )
    preregistration_id = _string(
        payload.get("preregistration_id"),
        "C02 preregistration ID",
    )
    if not re.fullmatch(r"c02prereg_[0-9a-f]{32}", preregistration_id):
        raise C02ContractError("invalid C02 preregistration ID")
    _expect(payload.get("status"), "approved_and_frozen", "preregistration status")
    registered_at = _string(payload.get("registered_at_utc"), "registered_at_utc")
    if not re.fullmatch(
        r"20[0-9]{2}-(0[1-9]|1[0-2])-([0-2][0-9]|3[01])"
        r"T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z",
        registered_at,
    ):
        raise C02ContractError("registered_at_utc must be second-precision UTC")
    registered_by = _string(
        payload.get("registered_by_role"),
        "registered_by_role",
    )
    if not SAFE_ID_RE.fullmatch(registered_by) or len(registered_by) > 64:
        raise C02ContractError("registered_by_role must be a bounded role ID")
    _expect(
        payload.get("frozen_before_diagnostic_issue_or_coaching_output_access"),
        True,
        "preregistration freeze order",
    )
    _expect(
        payload.get("c01_validation_report_sha256"),
        _sha256(c01_report_sha256, "C01 report SHA"),
        "preregistration C01 report binding",
    )
    _expect(
        payload.get("s107_completion_commit_sha256"),
        s107.commit_sha256,
        "preregistration S107 commit binding",
    )
    _expect(
        payload.get("s107_threshold_policy_report_sha256"),
        s107.threshold_report_sha256,
        "preregistration S107 threshold binding",
    )
    _expect(
        s107.threshold_selection_status,
        "selected_provisional_engineering",
        "preregistration S107 threshold selection status",
    )

    selection = _mapping(payload.get("selection_policy"), "selection policy")
    _expect_keys(
        selection,
        {
            "priority_formula",
            "allowed_top_k",
            "default_top_k",
            "ranking_tie_breakers",
            "blocked_top_k_backfill_allowed",
            "priority_arithmetic",
            "priority_output_scale",
            "priority_rounding",
        },
        "selection policy",
    )
    _expect(selection.get("priority_formula"), PRIORITY_FORMULA, "priority formula")
    _expect(
        tuple(_list(selection.get("allowed_top_k"), "allowed top-k")),
        (1, 3),
        "allowed top-k",
    )
    _expect(selection.get("default_top_k"), 1, "default top-k")
    _expect(
        tuple(_list(selection.get("ranking_tie_breakers"), "ranking ties")),
        RANKING_TIES,
        "ranking tie breakers",
    )
    _expect(
        selection.get("blocked_top_k_backfill_allowed"),
        False,
        "blocked top-k backfill",
    )
    _expect(
        selection.get("priority_arithmetic"),
        "base10_decimal_from_canonical_json_numbers",
        "priority arithmetic",
    )
    _expect(selection.get("priority_output_scale"), 12, "priority output scale")
    _expect(
        selection.get("priority_rounding"),
        "decimal_round_half_even",
        "priority rounding",
    )

    calibration = _mapping(payload.get("calibration_policy"), "calibration policy")
    _expect_keys(
        calibration,
        {
            "confidence_semantics",
            "issue_confidence_aggregation_from_s107_phone_probabilities",
            "exact_confidence_threshold",
            "threshold_comparison",
            "threshold_authorization_rule",
            "low_confidence_action",
            "s107_provisional_threshold_accepted_without_preregistration",
        },
        "calibration policy",
    )
    confidence_semantics = _string(
        calibration.get("confidence_semantics"),
        "confidence semantics",
    )
    aggregation = _string(
        calibration.get("issue_confidence_aggregation_from_s107_phone_probabilities"),
        "issue confidence aggregation",
    )
    for value, name in (
        (confidence_semantics, "confidence semantics"),
        (aggregation, "issue confidence aggregation"),
    ):
        if not SAFE_ID_RE.fullmatch(value) or len(value) > 128:
            raise C02ContractError(f"{name} must be a bounded deterministic ID")
    threshold = _probability(
        calibration.get("exact_confidence_threshold"),
        "exact confidence threshold",
    )
    if Decimal(str(threshold)) != Decimal(str(s107.issue_threshold)):
        raise C02ContractError(
            "preregistered confidence threshold differs from S107 selection"
        )
    _expect(
        calibration.get("threshold_comparison"),
        "greater_than_or_equal",
        "threshold comparison",
    )
    _expect(
        calibration.get("threshold_authorization_rule"),
        "exactly_s107_selected_issue_threshold",
        "threshold authorization rule",
    )
    _expect(
        calibration.get("low_confidence_action"),
        "rerecord",
        "low-confidence action",
    )
    _expect(
        calibration.get("s107_provisional_threshold_accepted_without_preregistration"),
        False,
        "S107 provisional threshold guard",
    )

    evidence = _mapping(payload.get("evidence_policy"), "evidence policy")
    _expect_keys(
        evidence,
        {
            "allowed_evidence_ids",
            "issue_type_to_supported_evidence_ids",
            "minimum_supported_evidence_count",
            "positive_target_overlap_receipt_required_for_phone_localized_claim",
            "unsupported_claim_action",
            "unknown_issue_type_action",
            "other_issue_type_action",
        },
        "evidence policy",
    )
    _expect(
        tuple(_list(evidence.get("allowed_evidence_ids"), "allowed evidence IDs")),
        EVIDENCE_IDS,
        "allowed evidence IDs",
    )
    tags = _catalog_tags(catalog)
    raw_matrix = _mapping(
        evidence.get("issue_type_to_supported_evidence_ids"),
        "issue support matrix",
    )
    _expect(set(raw_matrix), set(tags), "issue support matrix tag IDs")
    support_matrix: dict[str, frozenset[str]] = {}
    for tag_id in sorted(tags):
        raw_ids = _list(raw_matrix.get(tag_id), f"support evidence for {tag_id}")
        ids: list[str] = []
        for raw_id in raw_ids:
            evidence_id = _string(raw_id, f"support evidence for {tag_id}")
            if evidence_id not in EVIDENCE_IDS:
                raise C02ContractError(
                    f"unsupported evidence ID for {tag_id}: {evidence_id}"
                )
            ids.append(evidence_id)
        if len(ids) != len(set(ids)):
            raise C02ContractError(f"duplicate support evidence for {tag_id}")
        advice = _mapping(tags[tag_id].get("advice"), f"C01 advice {tag_id}")
        if tag_id == "other":
            if ids:
                raise C02ContractError("other issue type support must be empty")
        elif advice.get("action") == "draft_coach" and not ids:
            raise C02ContractError(
                f"coachable issue support must not be empty: {tag_id}"
            )
        support_matrix[tag_id] = frozenset(ids)
    minimum_evidence = _integer(
        evidence.get("minimum_supported_evidence_count"),
        "minimum supported evidence count",
        minimum=1,
        maximum=len(EVIDENCE_IDS),
    )
    for tag_id, supported in support_matrix.items():
        if tag_id != "other" and supported and len(supported) < minimum_evidence:
            raise C02ContractError(
                f"support matrix cannot satisfy minimum evidence: {tag_id}"
            )
    _expect(
        evidence.get(
            "positive_target_overlap_receipt_required_for_phone_localized_claim"
        ),
        True,
        "phone overlap receipt rule",
    )
    for field in (
        "unsupported_claim_action",
        "unknown_issue_type_action",
        "other_issue_type_action",
    ):
        _expect(evidence.get(field), "expert_review", f"evidence policy.{field}")

    safety = _mapping(payload.get("generation_safety"), "generation safety")
    _expect_keys(
        safety,
        {
            "message_source",
            "free_generation_llm_allowed",
            "prompt_or_completion_field_allowed",
            "network_access_allowed",
            "runtime_model_access_allowed",
            "catalog_message_rewrite_allowed",
            "catalog_advice_id_rewrite_allowed",
            "production_release_allowed_before_c03",
        },
        "generation safety",
    )
    _expect(
        safety.get("message_source"),
        "exact_c01_catalog_entry_only",
        "message source",
    )
    for field in (
        "free_generation_llm_allowed",
        "prompt_or_completion_field_allowed",
        "network_access_allowed",
        "runtime_model_access_allowed",
        "catalog_message_rewrite_allowed",
        "catalog_advice_id_rewrite_allowed",
        "production_release_allowed_before_c03",
    ):
        _expect(safety.get(field), False, f"generation safety.{field}")
    approval = _mapping(payload.get("approval"), "approval")
    _expect_keys(
        approval,
        {"decision", "protocol_sha256", "signed_receipt_sha256"},
        "approval",
    )
    _expect(approval.get("decision"), "approved", "approval decision")
    _sha256(approval.get("protocol_sha256"), "approval protocol SHA")
    _sha256(approval.get("signed_receipt_sha256"), "approval receipt SHA")
    canonical_sha = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return PreregistrationPolicy(
        sha256=canonical_sha,
        confidence_threshold=threshold,
        confidence_semantics=confidence_semantics,
        issue_confidence_aggregation=aggregation,
        minimum_supported_evidence_count=minimum_evidence,
        evidence_by_issue_type=support_matrix,
        payload=payload,
        snapshot=None,
    )


def _read_approved_preregistration(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    c01: C01Evidence,
    s107: S107Evidence,
    audit: AccessAudit,
) -> PreregistrationPolicy:
    section = _mapping(config.get("preregistration"), "preregistration")
    expected_sha = _string(
        section.get("approved_artifact_sha256"),
        "approved preregistration SHA",
    )
    if expected_sha == TBD_PREREGISTRATION:
        raise C02ContractError(
            "C02 preregistration is TBD_PREREGISTRATION",
            stage="preregistration",
            audit=audit,
        )
    _sha256(expected_sha, "approved preregistration SHA")
    audit.preregistration_path_resolution_count += 1
    audit.preregistration_read_attempt_count += 1
    try:
        payload, snapshot = _read_exact_json(
            workspace_root=workspace_root,
            relative=_relative_string(
                section.get("approved_artifact_path"),
                "approved preregistration path",
            ),
            expected_sha256=expected_sha,
            name="approved C02 preregistration",
        )
    except C02ContractError as exc:
        raise C02ContractError(
            f"C02 preregistration unavailable: {exc}",
            stage="preregistration",
            audit=audit,
        ) from exc
    audit.preregistration_read_count += 1
    try:
        policy = validate_preregistration_payload(
            payload,
            c01_report_sha256=c01.report_sha256,
            s107=s107,
            catalog=c01.catalog,
        )
    except C02ContractError as exc:
        raise C02ContractError(
            str(exc),
            stage="preregistration",
            audit=audit,
        ) from exc
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=(snapshot,),
        audit=audit,
        stage="preregistration",
    )
    return PreregistrationPolicy(
        sha256=snapshot.sha256,
        confidence_threshold=policy.confidence_threshold,
        confidence_semantics=policy.confidence_semantics,
        issue_confidence_aggregation=policy.issue_confidence_aggregation,
        minimum_supported_evidence_count=policy.minimum_supported_evidence_count,
        evidence_by_issue_type=policy.evidence_by_issue_type,
        payload=payload,
        snapshot=snapshot,
    )


def _bounded_nullable_phone(value: object, name: str) -> str | None:
    if value is None:
        return None
    text = _string(value, name)
    if len(text) > 16 or any(ord(character) < 32 for character in text):
        raise C02ContractError(f"{name} must be a bounded phone label")
    if any(character in text for character in ("/", "\\", "\x00")):
        raise C02ContractError(f"{name} cannot contain path/control characters")
    return text


def validate_diagnostic_input_payload(
    payload: Mapping[str, Any],
    *,
    policy: PreregistrationPolicy,
    c01: C01Evidence,
    s107: S107Evidence,
) -> Mapping[str, Any]:
    """Validate one already-authorized D1 issue manifest without I/O."""

    _reject_nonfinite_tree(payload, "C02 diagnostic input")
    _reject_sensitive_payload(payload, "C02 diagnostic input")
    _expect_keys(payload, INPUT_KEYS, "C02 diagnostic input")
    _expect(
        payload.get("schema_version"),
        "voice_coach.c02_diagnostic_issues_input.v1",
        "diagnostic input schema",
    )
    input_id = _string(payload.get("input_id"), "diagnostic input ID")
    if not re.fullmatch(r"c02in_[0-9a-f]{32}", input_id):
        raise C02ContractError("invalid diagnostic input ID")
    _sha256(payload.get("request_id_sha256"), "request ID SHA")
    _expect(
        payload.get("c01_validation_report_sha256"),
        c01.report_sha256,
        "diagnostic input C01 binding",
    )
    _expect(
        payload.get("s107_completion_commit_sha256"),
        s107.commit_sha256,
        "diagnostic input S107 commit binding",
    )
    _expect(
        payload.get("s107_threshold_policy_report_sha256"),
        s107.threshold_report_sha256,
        "diagnostic input S107 threshold binding",
    )
    _expect(
        payload.get("c02_preregistration_sha256"),
        policy.sha256,
        "diagnostic input preregistration binding",
    )
    top_k = _integer(payload.get("top_k"), "top-k")
    if top_k not in (1, 3):
        raise C02ContractError("top-k must be exactly 1 or 3")
    threshold = _probability(
        payload.get("confidence_threshold"),
        "confidence threshold",
    )
    if Decimal(str(threshold)) != Decimal(str(policy.confidence_threshold)):
        raise C02ContractError("diagnostic confidence threshold binding differs")
    _expect(
        payload.get("confidence_semantics"),
        policy.confidence_semantics,
        "diagnostic confidence semantics",
    )
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_experiment",
        "diagnostic input privacy zone",
    )

    seen_issue_ids: set[str] = set()
    for index, raw_issue in enumerate(_list(payload.get("issues"), "issues")):
        issue = _mapping(raw_issue, f"issue {index}")
        _expect_keys(issue, ISSUE_KEYS, f"issue {index}")
        issue_id = _sha256(issue.get("issue_id_sha256"), f"issue {index} ID")
        if issue_id in seen_issue_ids:
            raise C02ContractError(f"duplicate issue ID at index {index}")
        seen_issue_ids.add(issue_id)
        issue_type = _string(issue.get("issue_type"), f"issue {index} type")
        if not SAFE_ID_RE.fullmatch(issue_type) or len(issue_type) > 64:
            raise C02ContractError(f"invalid issue type at index {index}")
        start_ms = _integer(issue.get("start_ms"), f"issue {index} start", minimum=0)
        end_ms = _integer(issue.get("end_ms"), f"issue {index} end", minimum=1)
        if end_ms <= start_ms:
            raise C02ContractError(f"issue {index} interval must be positive")
        _bounded_nullable_phone(issue.get("target"), f"issue {index} target")
        _bounded_nullable_phone(issue.get("observed"), f"issue {index} observed")
        for field in (
            "severity",
            "calibrated_confidence",
            "comprehensibility_impact",
            "trainability",
        ):
            _probability(issue.get(field), f"issue {index} {field}")
        raw_evidence = _list(issue.get("evidence_ids"), f"issue {index} evidence")
        if not raw_evidence:
            raise C02ContractError(f"issue {index} evidence cannot be empty")
        evidence_ids = [
            _string(value, f"issue {index} evidence ID") for value in raw_evidence
        ]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise C02ContractError(f"issue {index} evidence IDs must be unique")
        if any(value not in EVIDENCE_IDS for value in evidence_ids):
            raise C02ContractError(f"issue {index} evidence ID is not allowed")
        _sha256(
            issue.get("s107_calibration_row_sha256"),
            f"issue {index} S107 row SHA",
        )
        _sha256(
            issue.get("claim_evidence_sha256"),
            f"issue {index} claim evidence SHA",
        )
        overlap = issue.get("target_overlap_evidence_sha256")
        if overlap is not None:
            _sha256(overlap, f"issue {index} target overlap SHA")
    return payload


def _priority_decimal(issue: Mapping[str, Any]) -> Decimal:
    try:
        result = (
            Decimal(str(issue["severity"]))
            * Decimal(str(issue["calibrated_confidence"]))
            * Decimal(str(issue["comprehensibility_impact"]))
            * Decimal(str(issue["trainability"]))
        )
    except (InvalidOperation, KeyError) as exc:
        raise C02ContractError("invalid priority inputs") from exc
    if not result.is_finite() or result < 0 or result > 1:
        raise C02ContractError("priority score must be finite and in [0,1]")
    return result


def _priority_output(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)
    return format(quantized, ".12f")


def _issue_sort_key(issue: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        -_priority_decimal(issue),
        _integer(issue.get("start_ms"), "tie start", minimum=0),
        _integer(issue.get("end_ms"), "tie end", minimum=1),
        _string(issue.get("issue_type"), "tie issue type"),
        issue.get("target") or "",
        issue.get("observed") or "",
        tuple(sorted(_list(issue.get("evidence_ids"), "tie evidence IDs"))),
        _sha256(issue.get("issue_id_sha256"), "tie issue ID"),
    )


def _supported_catalog_decision(
    issue: Mapping[str, Any],
    *,
    policy: PreregistrationPolicy,
    catalog_tags: Mapping[str, Mapping[str, Any]],
) -> tuple[bool, Mapping[str, Any] | None]:
    issue_type = _string(issue.get("issue_type"), "issue type")
    tag = catalog_tags.get(issue_type)
    if tag is None or issue_type == "other":
        return False, None
    advice = _mapping(tag.get("advice"), f"C01 advice {issue_type}")
    if (
        advice.get("action") != "draft_coach"
        or advice.get("review_status") != "engineering_draft"
        or not isinstance(advice.get("advice_id"), str)
        or not isinstance(advice.get("message_ko"), str)
        or not advice.get("advice_id")
        or not advice.get("message_ko")
    ):
        return False, None
    supported_evidence = policy.evidence_by_issue_type.get(issue_type)
    if supported_evidence is None:
        return False, None
    actual_evidence = frozenset(
        _string(value, "issue evidence ID")
        for value in _list(issue.get("evidence_ids"), "issue evidence")
    )
    if (
        not actual_evidence.issubset(supported_evidence)
        or len(actual_evidence) < policy.minimum_supported_evidence_count
    ):
        return False, None
    localization = _string(
        tag.get("localization_policy"),
        f"C01 localization policy {issue_type}",
    )
    target = issue.get("target")
    overlap = issue.get("target_overlap_evidence_sha256")
    if localization == "phone_overlap_required":
        if target is None or overlap is None:
            return False, None
    elif localization in ("no_target_phone_claim", "prosodic_span_only"):
        if target is not None or overlap is not None:
            return False, None
    else:
        return False, None
    return True, advice


def derive_selection_payload(
    input_payload: Mapping[str, Any],
    *,
    policy: PreregistrationPolicy,
    c01: C01Evidence,
    s107: S107Evidence,
    c02_config_sha256: str,
) -> Mapping[str, Any]:
    """Pure deterministic top-1/top-3 selection with catalog-only messages."""

    validate_diagnostic_input_payload(
        input_payload,
        policy=policy,
        c01=c01,
        s107=s107,
    )
    config_sha = _sha256(c02_config_sha256, "C02 config SHA")
    tags = _catalog_tags(c01.catalog)
    top_k = _integer(input_payload.get("top_k"), "top-k")
    issues = [
        _mapping(issue, f"issue {index}")
        for index, issue in enumerate(_list(input_payload.get("issues"), "issues"))
    ]
    ordered = sorted(issues, key=_issue_sort_key)
    window = ordered[:top_k]
    decisions: list[Mapping[str, Any]] = []
    for rank, issue in enumerate(window, start=1):
        confidence = _probability(
            issue.get("calibrated_confidence"),
            "calibrated confidence",
        )
        action: str
        reason: str | None
        advice_id: str | None
        message: str | None
        message_sha: str | None
        if Decimal(str(confidence)) < Decimal(str(policy.confidence_threshold)):
            action = "rerecord"
            reason = "low_confidence"
            advice_id = None
            message = None
            message_sha = None
        else:
            supported, advice = _supported_catalog_decision(
                issue,
                policy=policy,
                catalog_tags=tags,
            )
            if not supported or advice is None:
                action = "expert_review"
                reason = "unsupported_claim"
                advice_id = None
                message = None
                message_sha = None
            else:
                action = "coach"
                reason = None
                advice_id = _string(advice.get("advice_id"), "catalog advice ID")
                message = _string(advice.get("message_ko"), "catalog advice message")
                message_sha = hashlib.sha256(message.encode("utf-8")).hexdigest()
        decisions.append(
            {
                "rank": rank,
                "issue_id_sha256": issue["issue_id_sha256"],
                "issue_type": issue["issue_type"],
                "priority_score_decimal": _priority_output(_priority_decimal(issue)),
                "action": action,
                "abstention_reason": reason,
                "advice_id": advice_id,
                "message_ko": message,
                "message_sha256": message_sha,
                "claim_evidence_sha256": issue["claim_evidence_sha256"],
            }
        )
    selected_count = sum(decision["action"] == "coach" for decision in decisions)
    blocked_count = len(decisions) - selected_count
    if not decisions:
        status = "no_issues"
        overall_action = "none"
    elif selected_count:
        status = "selected"
        overall_action = "coach"
    else:
        status = "abstained"
        overall_action = decisions[0]["action"]
    input_sha = hashlib.sha256(canonical_json_bytes(input_payload)).hexdigest()
    identity = {
        "schema_version": "voice_coach.c02_coaching_selection_identity.v1",
        "input_manifest_sha256": input_sha,
        "c02_config_sha256": config_sha,
        "c02_preregistration_sha256": policy.sha256,
        "ranked_window": decisions,
    }
    output_id = (
        "c02out_" + hashlib.sha256(canonical_json_bytes(identity)).hexdigest()[:32]
    )
    return {
        "schema_version": "voice_coach.c02_coaching_selection_output.v1",
        "output_id": output_id,
        "status": status,
        "input_manifest_sha256": input_sha,
        "c02_config_sha256": config_sha,
        "c02_preregistration_sha256": policy.sha256,
        "c01_validation_report_sha256": c01.report_sha256,
        "s107_completion_commit_sha256": s107.commit_sha256,
        "s107_threshold_policy_report_sha256": s107.threshold_report_sha256,
        "top_k": top_k,
        "confidence_threshold": policy.confidence_threshold,
        "priority_formula": PRIORITY_FORMULA,
        "ranking_tie_breakers": list(RANKING_TIES),
        "ranked_window_count": len(decisions),
        "selected_count": selected_count,
        "blocked_count": blocked_count,
        "action": overall_action,
        "ranked_window": decisions,
        "generation_contract": {
            "mode": "deterministic_c01_ontology_allowlist_only",
            "exact_catalog_message_only": True,
            "free_generation_llm_used": False,
            "prompt_or_completion_present": False,
            "network_accessed": False,
            "runtime_model_accessed": False,
        },
        "privacy_zone": "D1_pseudonymized_restricted_experiment",
        "production_release_allowed": False,
    }


def validate_coaching_output_payload(
    payload: Mapping[str, Any],
    *,
    input_payload: Mapping[str, Any],
    policy: PreregistrationPolicy,
    c01: C01Evidence,
    s107: S107Evidence,
    c02_config_sha256: str,
) -> Mapping[str, Any]:
    """Recompute and byte-semantically compare every output field."""

    _reject_nonfinite_tree(payload, "C02 coaching output")
    _reject_sensitive_payload(payload, "C02 coaching output")
    _expect_keys(payload, OUTPUT_KEYS, "C02 coaching output")
    _expect(
        payload.get("schema_version"),
        "voice_coach.c02_coaching_selection_output.v1",
        "coaching output schema",
    )
    output_id = _string(payload.get("output_id"), "coaching output ID")
    if not re.fullmatch(r"c02out_[0-9a-f]{32}", output_id):
        raise C02ContractError("invalid coaching output ID")
    decisions = _list(payload.get("ranked_window"), "ranked window")
    for index, raw_decision in enumerate(decisions):
        decision = _mapping(raw_decision, f"ranked decision {index}")
        _expect_keys(decision, DECISION_KEYS, f"ranked decision {index}")
    generation = _mapping(payload.get("generation_contract"), "generation contract")
    _expect_keys(generation, GENERATION_KEYS, "generation contract")
    expected = derive_selection_payload(
        input_payload,
        policy=policy,
        c01=c01,
        s107=s107,
        c02_config_sha256=c02_config_sha256,
    )
    if dict(payload) != dict(expected):
        raise C02ContractError(
            "coaching output differs from deterministic catalog-only derivation"
        )
    return payload


def ensure_future_output_targets_absent(
    *,
    workspace_root: Path,
    outputs: Mapping[str, Any],
) -> None:
    """Enforce future no-overwrite and symlink-safe output target policy."""

    _expect(outputs.get("no_overwrite"), True, "future no-overwrite")
    _expect(outputs.get("atomic_publish_required"), True, "future atomic publish")
    _expect(
        outputs.get("write_enabled_in_this_package"),
        False,
        "static package write boundary",
    )
    schema_fields = {"input_schema_path", "output_schema_path"}
    seen: set[str] = set()
    for field, value in outputs.items():
        if not field.endswith("_path") or field in schema_fields:
            continue
        relative = _relative_string(value, f"outputs.{field}")
        if relative in seen:
            raise C02ContractError("duplicate future C02 output target")
        seen.add(relative)
        target = _reject_symlink_chain(
            workspace_root,
            relative,
            f"outputs.{field}",
            require_leaf=False,
        )
        if target.exists() or target.is_symlink():
            raise C02ContractError(f"no-overwrite target already exists: {relative}")


def run_full_preflight(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> PreflightResult:
    """Run predecessor-first checks and stop before any D1 or coaching access."""

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
    c01 = deep_validate_c01(
        workspace_root=workspace_root,
        config=config,
        audit=audit,
    )
    s107 = deep_validate_s107(
        workspace_root=workspace_root,
        config=config,
        audit=audit,
    )
    prereg = _read_approved_preregistration(
        workspace_root=workspace_root,
        config=config,
        c01=c01,
        s107=s107,
        audit=audit,
    )
    # Deliberately do not resolve a diagnostic or output path or select advice.
    return PreflightResult(
        status="ready_for_separate_c02_selection_executor",
        config_sha256=static.config_sha256,
        c01_report_sha256=c01.report_sha256,
        s107_commit_sha256=s107.commit_sha256,
        s107_threshold_report_sha256=s107.threshold_report_sha256,
        preregistration_sha256=prereg.sha256,
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
        "actual_diagnostic_or_coaching_execution": False,
        "actual_effect_estimation": False,
        "actual_model_gpu_network_or_llm_execution": False,
        "artifact_created": False,
        "c02_completion_claim_allowed": False,
        "production_release_allowed": False,
        "publication_allowed": False,
    }


def failure_result(error: C02ContractError) -> Mapping[str, Any]:
    audit = error.audit if error.audit is not None else AccessAudit()
    return {
        "status": "failed_closed",
        "stage": error.stage,
        "error": str(error),
        **audit.as_dict(),
        "actual_diagnostic_or_coaching_execution": False,
        "actual_effect_estimation": False,
        "actual_model_gpu_network_or_llm_execution": False,
        "artifact_created": False,
        "c02_completion_claim_allowed": False,
        "production_release_allowed": False,
        "publication_allowed": False,
    }
