"""Fail-closed WP8 E01 metamorphic robustness static contract.

The module validates policy and future manifests/reports.  It never reads
audio, runs a perturbation or selected model, computes a metric, accesses the
locked test/S203 expert holdout, uses network/GPU, or writes an output.

Canonical full preflight validates C1 sources first and then requires an
actual, deeply validated S108 final selected-model completion.  Because that
completion SHA is unresolved, it stops before resolving the predecessor path.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

import yaml  # type: ignore[import-untyped]


CONFIG_SCHEMA_VERSION = 1
STATIC_STATUS = "production_static_preflight_only"
TBD_PREFIX = "TBD_"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_ID_RE = re.compile(r"^e01m_[0-9a-f]{32}$")
CASE_ID_RE = re.compile(r"^e01c_[0-9a-f]{32}$")
REPORT_ID_RE = re.compile(r"^e01r_[0-9a-f]{32}$")
PERTURBATION_IDS = ("silence", "gain", "duration", "f0", "phone")
OPERATIONS = {
    "silence": "add_leading_and_trailing_silence",
    "gain": "change_global_gain",
    "duration": "change_one_syllable_duration",
    "f0": "scale_global_f0",
    "phone": "synthesize_specific_phone_replacement",
}
RELATION_TYPES = {
    "invariant",
    "monotonic_nonincreasing",
    "monotonic_nondecreasing",
    "targeted_change",
}
PASS_THRESHOLD = 0.95

TOP_LEVEL_KEYS = {
    "schema_version",
    "experiment",
    "canonical_sources",
    "predecessor_s108",
    "evaluation_scope",
    "perturbation_contract",
    "pass_gate",
    "preregistration",
    "input_contract",
    "privacy_and_safety",
    "execution_order",
    "outputs",
    "runtime",
}

SOURCE_PATHS = (
    "configs/reference/e01_metamorphic_input_manifest_v1.schema.json",
    "configs/reference/e01_metamorphic_report_v1.schema.json",
    "configs/reference/e01_metamorphic_preregistration_v1.template.json",
    "src/voice_coach/evaluation/e01_metamorphic_contract.py",
    "scripts/prepare_e01_metamorphic_robustness.py",
    "configs/experiments/wavlm_s108_no_locked_dry_run_v1.yaml",
    "configs/experiments/s2_s202_independent_auditor_v1.yaml",
    "src/voice_coach/training/s2_s202_auditor_contract.py",
    "configs/experiments/s2_s203_expert_holdout_audit_v1.yaml",
    "src/voice_coach/training/s2_s203_holdout_contract.py",
    "docs/IMPLEMENTATION_PLAN_KO.md",
    "docs/EXECUTION_LOG_2026-07-28_KO.md",
    "docs/paper/EXPERIMENTAL_MANUSCRIPT_KO.md",
    "docs/paper/CLAIM_GAP_MAP_KO.md",
)

EXECUTION_ORDER = (
    "validate_e01_c1_config_sources_schemas_and_template",
    "deep_validate_s108_final_selected_model_completion",
    "validate_frozen_e01_preregistration_without_tbd",
    "stop_before_input_manifest_audio_model_or_output_path_resolution",
)

RUNTIME_FIELDS = (
    "predecessor_path_resolution_count",
    "predecessor_read_attempt_count",
    "predecessor_read_count",
    "preregistration_path_resolution_count",
    "preregistration_read_attempt_count",
    "preregistration_read_count",
    "input_path_resolution_count",
    "input_manifest_read_count",
    "input_case_read_count",
    "raw_audio_read_count",
    "perturbed_audio_write_count",
    "model_path_resolution_count",
    "model_weight_read_count",
    "model_inference_count",
    "network_access_count",
    "gpu_access_count",
    "metric_computation_count",
    "decision_count",
    "locked_test_read_count",
    "s203_expert_holdout_read_count",
    "output_path_resolution_count",
    "output_count",
    "publication_count",
)

INPUT_KEYS = {
    "schema_version",
    "manifest_id",
    "status",
    "privacy_zone",
    "split_role",
    "contains_locked_test",
    "contains_s203_expert_holdout",
    "contains_raw_audio",
    "contains_audio_or_model_paths",
    "s108_commit_sha256",
    "selected_model_identity_sha256",
    "preregistration_sha256",
    "deterministic_seed",
    "case_count",
    "cases",
}
CASE_KEYS = {
    "case_id",
    "source_item_id_sha256",
    "source_audio_object_id_sha256",
    "source_audio_sha256",
    "perturbation_id",
    "parameter_set_sha256",
    "expected_relation_id_sha256",
    "target_region_sha256",
}
REPORT_KEYS = {
    "schema_version",
    "report_id",
    "status",
    "privacy_zone",
    "contains_row_level_data",
    "contains_direct_or_pseudonymous_ids",
    "contains_free_text",
    "locked_test_access_count",
    "s203_expert_holdout_access_count",
    "config_sha256",
    "s108_commit_sha256",
    "selected_model_identity_sha256",
    "selected_checkpoint_sha256",
    "preregistration_sha256",
    "input_manifest_sha256",
    "metric_computation_external_to_static_package",
    "perturbation_results",
    "overall",
    "publication_allowed",
}
PERTURBATION_RESULT_KEYS = {
    "perturbation_id",
    "expectation_type",
    "case_count",
    "evaluable_count",
    "passed_count",
    "failed_count",
    "invalid_count",
    "pass_rate",
    "confidence_interval_lower",
    "confidence_interval_upper",
    "decision",
}
OVERALL_KEYS = {
    "case_count",
    "evaluable_count",
    "passed_count",
    "failed_count",
    "invalid_count",
    "metric",
    "operator",
    "threshold",
    "pass_rate",
    "gate_statistic_value",
    "decision",
}
FORBIDDEN_REPORT_FIELDS = {
    "row_id",
    "row_ids",
    "row_id_sha256",
    "item_id",
    "item_ids",
    "item_id_sha256",
    "case_id",
    "case_ids",
    "participant_id",
    "speaker_id",
    "audio_id",
    "source_audio_sha256",
    "audio_path",
    "model_path",
    "target_text",
    "transcript",
    "free_text",
    "free_text_note",
    "comment",
    "rows",
    "cases",
}


class E01ContractError(ValueError):
    """A fail-closed violation with stage and immutable access counters."""

    def __init__(
        self,
        message: str,
        *,
        stage: str = "static",
        audit: AccessAudit | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.audit = audit


class _UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate YAML keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> Mapping[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise E01ContractError("YAML keys must be strings")
        if key in result:
            raise E01ContractError(f"YAML contains duplicate key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass
class AccessAudit:
    source_pin_read_count: int = 0
    source_pin_verify_count: int = 0
    s108_deep_validation_count: int = 0
    predecessor_path_resolution_count: int = 0
    predecessor_read_attempt_count: int = 0
    predecessor_read_count: int = 0
    preregistration_path_resolution_count: int = 0
    preregistration_read_attempt_count: int = 0
    preregistration_read_count: int = 0
    input_path_resolution_count: int = 0
    input_manifest_read_count: int = 0
    input_case_read_count: int = 0
    raw_audio_read_count: int = 0
    perturbed_audio_write_count: int = 0
    model_path_resolution_count: int = 0
    model_weight_read_count: int = 0
    model_inference_count: int = 0
    network_access_count: int = 0
    gpu_access_count: int = 0
    metric_computation_count: int = 0
    decision_count: int = 0
    locked_test_read_count: int = 0
    s203_expert_holdout_read_count: int = 0
    output_path_resolution_count: int = 0
    output_count: int = 0
    publication_count: int = 0


@dataclass(frozen=True)
class FileSnapshot:
    path: str
    sha256: str
    device: int
    inode: int
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class StaticValidation:
    config_sha256: str
    policy_sha256: str
    source_snapshots: tuple[FileSnapshot, ...]


@dataclass(frozen=True)
class S108Evidence:
    commit_snapshot: FileSnapshot
    selected_model_identity_sha256: str
    selected_checkpoint_sha256: str


@dataclass(frozen=True)
class PreregistrationEvidence:
    snapshot: FileSnapshot
    document: Mapping[str, Any]


@dataclass(frozen=True)
class PreflightResult:
    status: str
    config_sha256: str
    s108_commit_sha256: str
    preregistration_sha256: str
    audit: Mapping[str, int]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise E01ContractError(f"{name} must be a mapping")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise E01ContractError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise E01ContractError(f"{name} must be a non-empty string")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise E01ContractError(f"{name} must be boolean")
    return value


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise E01ContractError(f"{name} must be an integer >= {minimum}")
    return value


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise E01ContractError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise E01ContractError(f"{name} must be finite")
    return result


def _nullable_rate(value: object, name: str) -> float | None:
    if value is None:
        return None
    result = _finite(value, name)
    if result < 0 or result > 1:
        raise E01ContractError(f"{name} must be in [0, 1]")
    return result


def _sha256(value: object, name: str) -> str:
    result = _string(value, name)
    if not SHA256_RE.fullmatch(result) or result == "0" * 64:
        raise E01ContractError(f"{name} must be a nonzero lowercase SHA-256")
    return result


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise E01ContractError(f"{name} must equal {expected!r}")


def _expect_keys(
    value: Mapping[str, Any],
    expected: set[str],
    name: str,
) -> None:
    if set(value) != expected:
        raise E01ContractError(
            f"{name} keys differ; missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


def _contains_tbd(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith(TBD_PREFIX)
    if isinstance(value, Mapping):
        return any(_contains_tbd(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_tbd(item) for item in value)
    return False


def _reject_nonfinite_tree(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise E01ContractError(f"{name} contains a non-finite number")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_nonfinite_tree(item, f"{name}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _reject_nonfinite_tree(item, f"{name}[{index}]")


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise E01ContractError(f"JSON contains duplicate key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise E01ContractError(f"JSON contains forbidden numeric constant: {value}")


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _binding_sha256(value: Mapping[str, Any]) -> str:
    unsigned = {key: item for key, item in value.items() if key != "binding_sha256"}
    return hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()


def _parse_json(data: bytes, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_json_pairs,
            parse_constant=_reject_json_constant,
        )
    except UnicodeDecodeError as exc:
        raise E01ContractError(f"{name} must be UTF-8 JSON") from exc
    except json.JSONDecodeError as exc:
        raise E01ContractError(f"{name} must be strict JSON") from exc
    result = _mapping(value, name)
    _reject_nonfinite_tree(result, name)
    return result


def _relative_string(value: object, name: str) -> str:
    result = _string(value, name)
    path = PurePosixPath(result)
    if (
        path.is_absolute()
        or result != path.as_posix()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise E01ContractError(f"{name} must be a normalized workspace-relative path")
    return result


def _reject_symlink_chain(root: Path, relative: str, name: str) -> Path:
    path = root
    for index, part in enumerate(PurePosixPath(relative).parts):
        path = path / part
        try:
            metadata = path.lstat()
        except FileNotFoundError as exc:
            raise E01ContractError(f"{name} does not exist") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise E01ContractError(f"{name} cannot traverse a symlink")
        if index < len(PurePosixPath(relative).parts) - 1 and not stat.S_ISDIR(
            metadata.st_mode
        ):
            raise E01ContractError(f"{name} parent must be a directory")
    return path


def _read_file(
    root: Path,
    relative: str,
    name: str,
    *,
    before_second_stat_hook: Callable[[], None] | None = None,
) -> tuple[bytes, FileSnapshot]:
    normalized = _relative_string(relative, f"{name} path")
    path = _reject_symlink_chain(root, normalized, name)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise E01ContractError(f"{name} is not a readable regular file") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise E01ContractError(f"{name} must be a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        if before_second_stat_hook is not None:
            before_second_stat_hook()
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if before_identity != after_identity:
        raise E01ContractError(f"{name} changed while it was read")
    try:
        current = path.lstat()
    except FileNotFoundError as exc:
        raise E01ContractError(f"{name} disappeared after it was read") from exc
    current_identity = (
        current.st_dev,
        current.st_ino,
        current.st_size,
        current.st_mtime_ns,
    )
    if current_identity != after_identity:
        raise E01ContractError(f"{name} changed while it was read")
    data = b"".join(chunks)
    return data, FileSnapshot(
        path=normalized,
        sha256=hashlib.sha256(data).hexdigest(),
        device=after.st_dev,
        inode=after.st_ino,
        size=after.st_size,
        mtime_ns=after.st_mtime_ns,
    )


def _verify_snapshot(root: Path, expected: FileSnapshot, name: str) -> None:
    _, observed = _read_file(root, expected.path, name)
    if observed != expected:
        raise E01ContractError(f"{name} changed after validation")


def load_config(path: Path) -> Mapping[str, Any]:
    if path.is_symlink():
        raise E01ContractError("E01 config may not be a symlink")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise E01ContractError("E01 config is not readable") from exc
    try:
        value = yaml.load(data.decode("utf-8"), Loader=_UniqueKeyLoader)
    except UnicodeDecodeError as exc:
        raise E01ContractError("E01 config must be UTF-8") from exc
    except yaml.YAMLError as exc:
        raise E01ContractError("E01 config must be unique-key YAML") from exc
    result = _mapping(value, "E01 config")
    _reject_nonfinite_tree(result, "E01 config")
    return result


def _validate_input_schema(schema: Mapping[str, Any]) -> None:
    _expect(
        schema.get("$id"),
        "voice_coach.e01_metamorphic_input_manifest.v1",
        "input schema ID",
    )
    _expect(schema.get("type"), "object", "input schema root type")
    _expect(schema.get("additionalProperties"), False, "input schema strictness")
    _expect(
        set(_list(schema.get("required"), "input schema required")),
        INPUT_KEYS,
        "input schema required fields",
    )
    properties = _mapping(schema.get("properties"), "input schema properties")
    _expect(set(properties), INPUT_KEYS, "input schema property names")
    for field, expected in (
        ("contains_locked_test", False),
        ("contains_s203_expert_holdout", False),
        ("contains_raw_audio", False),
        ("contains_audio_or_model_paths", False),
    ):
        _expect(
            _mapping(properties.get(field), f"input schema {field}").get("const"),
            expected,
            f"input schema {field}",
        )
    case_schema = _mapping(
        _mapping(schema.get("$defs"), "input definitions").get("case"),
        "case schema",
    )
    _expect(case_schema.get("additionalProperties"), False, "case strictness")
    _expect(
        set(_list(case_schema.get("required"), "case required")),
        CASE_KEYS,
        "case required fields",
    )
    _expect(
        set(_mapping(case_schema.get("properties"), "case properties")),
        CASE_KEYS,
        "case property names",
    )
    serialized = json.dumps(schema, sort_keys=True).casefold()
    for forbidden in (
        '"audio_path"',
        '"model_path"',
        '"raw_audio"',
        '"locked_test_row"',
        '"speaker_name"',
    ):
        if forbidden in serialized:
            raise E01ContractError(f"input schema exposes forbidden field {forbidden}")


def _validate_report_schema(schema: Mapping[str, Any]) -> None:
    _expect(
        schema.get("$id"),
        "voice_coach.e01_metamorphic_report.v1",
        "report schema ID",
    )
    _expect(schema.get("type"), "object", "report schema root type")
    _expect(schema.get("additionalProperties"), False, "report schema strictness")
    _expect(
        set(_list(schema.get("required"), "report schema required")),
        REPORT_KEYS,
        "report schema required fields",
    )
    properties = _mapping(schema.get("properties"), "report schema properties")
    _expect(set(properties), REPORT_KEYS, "report schema property names")
    for field, expected in (
        ("contains_row_level_data", False),
        ("contains_direct_or_pseudonymous_ids", False),
        ("contains_free_text", False),
        ("locked_test_access_count", 0),
        ("s203_expert_holdout_access_count", 0),
        ("publication_allowed", False),
    ):
        _expect(
            _mapping(properties.get(field), f"report schema {field}").get("const"),
            expected,
            f"report schema {field}",
        )
    overall = _mapping(
        _mapping(
            _mapping(schema.get("$defs"), "report definitions").get("overallResult"),
            "overall schema",
        ).get("properties"),
        "overall properties",
    )
    _expect(
        _mapping(overall.get("threshold"), "overall threshold").get("const"),
        PASS_THRESHOLD,
        "report pass threshold",
    )
    serialized = json.dumps(schema, sort_keys=True).casefold()
    for forbidden in (
        '"item_id"',
        '"case_id"',
        '"source_audio_sha256"',
        '"audio_path"',
        '"free_text"',
        '"rows"',
    ):
        if forbidden in serialized:
            raise E01ContractError(
                f"report schema exposes forbidden A1 field {forbidden}"
            )


def _validate_preregistration_template(
    document: Mapping[str, Any],
    *,
    draft: bool,
) -> None:
    _expect(
        document.get("schema_version"),
        "voice_coach.e01_metamorphic_preregistration.v1",
        "preregistration schema",
    )
    if draft:
        _expect(document.get("status"), "draft_not_approved", "draft status")
    else:
        _expect(document.get("status"), "approved_and_frozen", "approved status")
    population = _mapping(
        document.get("evaluation_population"),
        "preregistration population",
    )
    _expect(
        population.get("split_role"),
        "phase3_non_holdout_e01_only",
        "preregistration split role",
    )
    _expect(population.get("locked_test_allowed"), False, "locked-test policy")
    _expect(
        population.get("s203_expert_holdout_allowed"),
        False,
        "S203 holdout policy",
    )
    perturbations = _list(
        document.get("perturbations"),
        "preregistration perturbations",
    )
    if len(perturbations) != len(PERTURBATION_IDS):
        raise E01ContractError("preregistration must contain exactly five families")
    for index, (raw, expected_id) in enumerate(
        zip(perturbations, PERTURBATION_IDS, strict=True)
    ):
        perturbation = _mapping(raw, f"preregistration perturbation {index}")
        _expect(perturbation.get("perturbation_id"), expected_id, "perturbation ID")
        _expect(
            perturbation.get("operation"),
            OPERATIONS[expected_id],
            f"{expected_id} operation",
        )
        magnitude = _mapping(
            perturbation.get("exact_magnitude"),
            f"{expected_id} exact magnitude",
        )
        relation = _mapping(
            perturbation.get("expected_relation"),
            f"{expected_id} expected relation",
        )
        if draft:
            for key, value in magnitude.items():
                _expect(value, "TBD_PREREGISTRATION", f"{expected_id} magnitude {key}")
            for key, value in relation.items():
                _expect(value, "TBD_PREREGISTRATION", f"{expected_id} relation {key}")
        else:
            if _contains_tbd(magnitude) or _contains_tbd(relation):
                raise E01ContractError(
                    f"approved {expected_id} magnitude/relation remains TBD"
                )
            relation_type = _string(
                relation.get("type"),
                f"{expected_id} relation type",
            )
            if relation_type not in RELATION_TYPES:
                raise E01ContractError(
                    f"{expected_id} relation type is not an allowed contract type"
                )
    gate = _mapping(document.get("pass_gate"), "preregistration pass gate")
    _expect(
        gate.get("metric"),
        "metamorphic_expectation_pass_rate",
        "preregistration metric",
    )
    _expect(gate.get("operator"), "greater_than_or_equal", "gate operator")
    _expect(gate.get("threshold"), PASS_THRESHOLD, "gate threshold")
    for field in (
        "numerator_definition",
        "denominator_definition",
        "gate_statistic",
        "aggregation_scope",
        "case_weighting",
        "perturbation_weighting",
        "minimum_support",
        "invalid_case_policy",
        "missing_case_policy",
        "confidence_interval_method",
        "confidence_level",
        "multiplicity_control",
        "blocked_or_failed_rule",
    ):
        if draft:
            _expect(gate.get(field), "TBD_PREREGISTRATION", f"gate {field}")
        elif _contains_tbd(gate.get(field)):
            raise E01ContractError(f"approved gate {field} remains TBD")
    integrity = _mapping(
        document.get("execution_integrity"),
        "preregistration integrity",
    )
    for field in (
        "posthoc_magnitude_or_expectation_change_allowed",
        "result_based_case_exclusion_allowed",
        "locked_test_access_allowed",
        "s203_expert_holdout_access_allowed",
        "network_fallback_allowed",
    ):
        _expect(integrity.get(field), False, f"preregistration {field}")
    if draft and not _contains_tbd(document):
        raise E01ContractError("draft preregistration must expose TBD fields")
    if not draft and _contains_tbd(document):
        raise E01ContractError("approved preregistration contains TBD")


def _validate_experiment(config: Mapping[str, Any]) -> None:
    expected = {
        "id": "e01_metamorphic_robustness_v1",
        "stage": "wp8_e01_phase3_non_holdout_metamorphic_robustness",
        "implementation_status": STATIC_STATUS,
        "actual_audio_access_allowed": False,
        "actual_perturbation_execution_allowed": False,
        "actual_model_access_allowed": False,
        "actual_model_inference_allowed": False,
        "actual_metric_computation_allowed": False,
        "actual_decision_allowed": False,
        "actual_network_access_allowed": False,
        "actual_gpu_access_allowed": False,
        "locked_test_access_allowed": False,
        "s203_expert_holdout_access_allowed": False,
        "actual_output_write_allowed": False,
        "publication_allowed": False,
        "completion_claim_allowed": False,
    }
    _expect(
        dict(_mapping(config.get("experiment"), "experiment")), expected, "experiment"
    )


def _validate_predecessor(config: Mapping[str, Any]) -> None:
    predecessor = _mapping(config.get("predecessor_s108"), "S108 predecessor")
    _expect(predecessor.get("validation_order"), 1, "S108 order")
    _expect(predecessor.get("deep_validation_required"), True, "S108 deep validation")
    _relative_string(predecessor.get("delegate_config_path"), "delegate config")
    _sha256(predecessor.get("delegate_config_sha256"), "delegate config SHA")
    _relative_string(predecessor.get("delegate_contract_path"), "delegate contract")
    _sha256(predecessor.get("delegate_contract_sha256"), "delegate contract SHA")
    _relative_string(predecessor.get("completion_commit_path"), "S108 commit path")
    _expect(
        predecessor.get("completion_commit_sha256"),
        "TBD_S108_COMPLETION",
        "canonical S108 commit SHA",
    )
    _relative_string(predecessor.get("registry_path"), "S108 registry path")
    _expect(
        predecessor.get("required_schema_version"),
        "voice_coach.wavlm_s108_final_s1_commit.v1",
        "S108 schema",
    )
    _expect(predecessor.get("required_status"), "committed", "S108 status")
    _expect(
        predecessor.get("required_decision"),
        "research_candidate_passed",
        "S108 decision",
    )
    for field in (
        "selected_model_identity_sha256",
        "selected_checkpoint_sha256",
        "selected_calibrator_sha256",
        "selected_threshold_policy_sha256",
    ):
        _expect(
            predecessor.get(field),
            "TBD_S108_COMPLETION",
            f"canonical S108 {field}",
        )
    _expect(
        predecessor.get("aggregate_deep_validation_only"),
        True,
        "S108 aggregate boundary",
    )
    _expect(
        predecessor.get("locked_rows_or_audio_may_be_read_by_e01"),
        False,
        "S108 locked read boundary",
    )


def _validate_perturbation_config(config: Mapping[str, Any]) -> None:
    scope = _mapping(config.get("evaluation_scope"), "evaluation scope")
    _expect(scope.get("role"), "phase3_non_holdout_e01_only", "evaluation role")
    _expect(scope.get("s203_holdout_primary_metric"), False, "S203 metric role")
    for field in (
        "locked_test_allowed",
        "s203_expert_holdout_allowed",
        "train_or_calibration_reuse_allowed",
    ):
        _expect(scope.get(field), False, f"evaluation scope {field}")
    for field in (
        "source_population_pointer",
        "source_population_sha256",
        "sampling_frame",
        "sampling_algorithm",
        "sample_size",
        "minimum_cases_per_perturbation",
        "speaker_disjointness_policy",
        "prompt_balance_policy",
        "l1_proficiency_gender_slice_policy",
    ):
        _expect(scope.get(field), "TBD_PREREGISTRATION", f"scope {field}")

    contract = _mapping(
        config.get("perturbation_contract"),
        "perturbation contract",
    )
    _expect(
        contract.get("fixed_perturbation_ids"),
        list(PERTURBATION_IDS),
        "perturbation IDs",
    )
    for perturbation_id in PERTURBATION_IDS:
        perturbation = _mapping(
            contract.get(perturbation_id),
            f"perturbation {perturbation_id}",
        )
        _expect(
            perturbation.get("operation"),
            OPERATIONS[perturbation_id],
            f"{perturbation_id} operation",
        )
        for section in ("exact_magnitude", "expected_relation"):
            values = _mapping(
                perturbation.get(section),
                f"{perturbation_id} {section}",
            )
            if not values:
                raise E01ContractError(f"{perturbation_id} {section} is empty")
            for key, value in values.items():
                _expect(
                    value,
                    "TBD_PREREGISTRATION",
                    f"{perturbation_id} {section} {key}",
                )
    _expect(contract.get("background_noise_in_scope"), False, "noise scope")
    _expect(
        contract.get("undocumented_sixth_perturbation_allowed"),
        False,
        "sixth perturbation policy",
    )

    gate = _mapping(config.get("pass_gate"), "pass gate")
    _expect(gate.get("source_backed"), True, "gate source backing")
    _expect(gate.get("source_scope"), "phase3_non_holdout", "gate scope")
    _expect(
        gate.get("metric"),
        "metamorphic_expectation_pass_rate",
        "gate metric",
    )
    _expect(gate.get("operator"), "greater_than_or_equal", "gate operator")
    _expect(gate.get("threshold"), PASS_THRESHOLD, "gate threshold")
    for field in (
        "numerator_definition",
        "denominator_definition",
        "gate_statistic",
        "aggregation_scope",
        "case_weighting",
        "perturbation_weighting",
        "minimum_support",
        "invalid_case_policy",
        "missing_case_policy",
        "confidence_interval_method",
        "confidence_level",
        "multiplicity_control",
        "blocked_or_failed_rule",
    ):
        _expect(gate.get(field), "TBD_PREREGISTRATION", f"gate {field}")
    _expect(gate.get("pass_may_not_be_inferred_from_missing"), True, "missing gate")
    _expect(
        gate.get("posthoc_threshold_change_allowed"),
        False,
        "posthoc gate change",
    )


def _validate_remaining_policy(config: Mapping[str, Any]) -> None:
    preregistration = _mapping(config.get("preregistration"), "preregistration")
    _expect(preregistration.get("validation_order"), 2, "preregistration order")
    _expect(
        preregistration.get("placeholder"),
        "TBD_PREREGISTRATION",
        "preregistration marker",
    )
    _relative_string(preregistration.get("template_path"), "prereg template path")
    _sha256(preregistration.get("template_sha256"), "prereg template SHA")
    _relative_string(
        preregistration.get("approved_artifact_path"),
        "approved preregistration path",
    )
    _expect(
        preregistration.get("approved_artifact_sha256"),
        "TBD_PREREGISTRATION",
        "approved preregistration SHA",
    )
    for field in (
        "must_bind_s108_model_and_checkpoint",
        "must_resolve_all_magnitudes_and_expectations_before_input_access",
        "unresolved_fields_must_fail_closed",
    ):
        _expect(preregistration.get(field), True, f"preregistration {field}")

    input_contract = _mapping(config.get("input_contract"), "input contract")
    for field in ("schema_path", "manifest_path"):
        _relative_string(input_contract.get(field), f"input {field}")
    _sha256(input_contract.get("schema_sha256"), "input schema SHA")
    _expect(
        input_contract.get("manifest_sha256"),
        "TBD_PREREGISTRATION",
        "input manifest SHA",
    )
    _expect(
        input_contract.get("privacy_zone"),
        "D1_pseudonymized_restricted_non_locked",
        "input privacy zone",
    )
    for field in (
        "raw_audio_embedded_allowed",
        "audio_or_model_path_allowed",
        "locked_test_rows_allowed",
        "s203_expert_holdout_rows_allowed",
        "actual_read_enabled_in_this_package",
    ):
        _expect(input_contract.get(field), False, f"input {field}")

    privacy = _mapping(config.get("privacy_and_safety"), "privacy and safety")
    _expect(
        privacy.get("input_zone"),
        "D1_pseudonymized_restricted_non_locked",
        "privacy input zone",
    )
    _expect(privacy.get("report_zone"), "A1_aggregate_only", "privacy report zone")
    for field in (
        "raw_audio_allowed_in_manifest_or_report",
        "audio_path_allowed_in_manifest_or_report",
        "model_path_allowed_in_manifest_or_report",
        "direct_identifiers_allowed",
        "free_text_allowed",
        "row_or_pseudonymous_ids_allowed_in_report",
        "locked_test_access_allowed",
        "s203_expert_holdout_access_allowed",
        "network_fallback_allowed",
        "result_based_case_exclusion_allowed",
        "posthoc_magnitude_or_expectation_change_allowed",
        "actual_perturbed_audio_publication_allowed",
    ):
        _expect(privacy.get(field), False, f"privacy {field}")
    _expect(
        privacy.get("model_promotion_requires_separate_review"),
        True,
        "model promotion review",
    )
    _expect(config.get("execution_order"), list(EXECUTION_ORDER), "execution order")

    outputs = _mapping(config.get("outputs"), "outputs")
    for field in (
        "report_schema_path",
        "aggregate_report_path",
        "commit_path",
        "registry_path",
        "failure_report_path",
    ):
        _relative_string(outputs.get(field), f"output {field}")
    for field in ("no_overwrite", "atomic_completed_bundle_required", "no_symlink"):
        _expect(outputs.get(field), True, f"output {field}")
    _expect(outputs.get("write_enabled_in_this_package"), False, "output write")
    _expect(outputs.get("current_output_count"), 0, "output count")
    _expect(
        outputs.get("completion_claim_allowed"),
        False,
        "output completion claim",
    )

    runtime = _mapping(config.get("runtime"), "runtime")
    counts = _mapping(
        runtime.get("config_only_required_counts"),
        "config-only counters",
    )
    _expect(set(counts), set(RUNTIME_FIELDS), "runtime counter names")
    for field in RUNTIME_FIELDS:
        _expect(counts.get(field), 0, f"config-only {field}")
    _expect(runtime.get("cpu_only_static_validation"), True, "CPU-only static")
    for field in (
        "environment_file_read_allowed",
        "network_allowed",
        "model_allowed",
        "gpu_allowed",
        "actual_execution_allowed",
    ):
        _expect(runtime.get(field), False, f"runtime {field}")


def validate_static_config(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    audit: AccessAudit | None = None,
) -> StaticValidation:
    """Validate only C1/static sources, schemas, and unresolved policy."""

    active_audit = audit if audit is not None else AccessAudit()
    root = workspace_root.resolve(strict=True)
    if config_path.is_symlink():
        raise E01ContractError("E01 config may not be a symlink", audit=active_audit)
    _expect_keys(config, TOP_LEVEL_KEYS, "E01 top level")
    _expect(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "schema version")
    _validate_experiment(config)
    _validate_predecessor(config)
    _validate_perturbation_config(config)
    _validate_remaining_policy(config)

    canonical = _mapping(config.get("canonical_sources"), "canonical sources")
    _expect(
        canonical.get("access_scope"),
        "C1_code_config_docs_only",
        "canonical source scope",
    )
    pins = _list(canonical.get("pins"), "canonical source pins")
    if len(pins) != len(SOURCE_PATHS):
        raise E01ContractError("canonical source pin count differs", audit=active_audit)
    snapshots: list[FileSnapshot] = []
    for index, (raw, expected_path) in enumerate(zip(pins, SOURCE_PATHS, strict=True)):
        pin = _mapping(raw, f"source pin {index}")
        _expect_keys(pin, {"path", "sha256"}, f"source pin {index}")
        _expect(pin.get("path"), expected_path, f"source pin {index} path")
        expected_sha = _sha256(pin.get("sha256"), f"source pin {index} SHA")
        try:
            data, snapshot = _read_file(root, expected_path, f"source pin {index}")
        except E01ContractError as exc:
            exc.audit = active_audit
            raise
        active_audit.source_pin_read_count += 1
        _expect(snapshot.sha256, expected_sha, f"source pin {expected_path} SHA")
        snapshots.append(snapshot)
        if expected_path.endswith("input_manifest_v1.schema.json"):
            _validate_input_schema(_parse_json(data, "E01 input schema"))
        elif expected_path.endswith("metamorphic_report_v1.schema.json"):
            _validate_report_schema(_parse_json(data, "E01 report schema"))
        elif expected_path.endswith("preregistration_v1.template.json"):
            _validate_preregistration_template(
                _parse_json(data, "E01 preregistration template"),
                draft=True,
            )

    config_relative = config_path.resolve(strict=True).relative_to(root).as_posix()
    _, config_snapshot = _read_file(root, config_relative, "E01 config")
    policy = {
        key: config[key]
        for key in (
            "predecessor_s108",
            "evaluation_scope",
            "perturbation_contract",
            "pass_gate",
            "preregistration",
            "input_contract",
            "privacy_and_safety",
            "execution_order",
            "outputs",
            "runtime",
        )
    }
    return StaticValidation(
        config_sha256=config_snapshot.sha256,
        policy_sha256=hashlib.sha256(canonical_json_bytes(policy)).hexdigest(),
        source_snapshots=tuple(snapshots),
    )


def _collect_forbidden_report_fields(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key.casefold() in FORBIDDEN_REPORT_FIELDS:
                found.add(key)
            found.update(_collect_forbidden_report_fields(item))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            found.update(_collect_forbidden_report_fields(item))
    return found


def validate_input_manifest_payload(
    manifest: Mapping[str, Any],
    *,
    expected_s108_commit_sha256: str | None = None,
    expected_model_identity_sha256: str | None = None,
    expected_preregistration_sha256: str | None = None,
) -> int:
    """Validate a future D1 manifest without resolving audio or model paths."""

    _reject_nonfinite_tree(manifest, "E01 input manifest")
    _expect_keys(manifest, INPUT_KEYS, "E01 input manifest")
    _expect(
        manifest.get("schema_version"),
        "voice_coach.e01_metamorphic_input_manifest.v1",
        "input manifest schema",
    )
    manifest_id = _string(manifest.get("manifest_id"), "input manifest ID")
    if not MANIFEST_ID_RE.fullmatch(manifest_id):
        raise E01ContractError("input manifest ID is invalid")
    _expect(manifest.get("status"), "frozen", "input manifest status")
    _expect(
        manifest.get("privacy_zone"),
        "D1_pseudonymized_restricted_non_locked",
        "input manifest privacy",
    )
    _expect(
        manifest.get("split_role"),
        "phase3_non_holdout_e01_only",
        "input manifest split role",
    )
    for field in (
        "contains_locked_test",
        "contains_s203_expert_holdout",
        "contains_raw_audio",
        "contains_audio_or_model_paths",
    ):
        _expect(manifest.get(field), False, f"input manifest {field}")
    s108_sha = _sha256(manifest.get("s108_commit_sha256"), "input S108 SHA")
    model_sha = _sha256(
        manifest.get("selected_model_identity_sha256"),
        "input model identity SHA",
    )
    prereg_sha = _sha256(
        manifest.get("preregistration_sha256"),
        "input preregistration SHA",
    )
    if expected_s108_commit_sha256 is not None:
        _expect(s108_sha, expected_s108_commit_sha256, "input S108 binding")
    if expected_model_identity_sha256 is not None:
        _expect(model_sha, expected_model_identity_sha256, "input model binding")
    if expected_preregistration_sha256 is not None:
        _expect(
            prereg_sha,
            expected_preregistration_sha256,
            "input preregistration binding",
        )
    _integer(manifest.get("deterministic_seed"), "input deterministic seed")
    declared_count = _integer(manifest.get("case_count"), "input case count", minimum=1)
    cases = _list(manifest.get("cases"), "input cases")
    _expect(len(cases), declared_count, "input case count")
    seen_case_ids: set[str] = set()
    seen_semantic: set[tuple[str, str, str, str | None]] = set()
    observed_families: set[str] = set()
    for index, raw in enumerate(cases):
        case = _mapping(raw, f"input case {index}")
        _expect_keys(case, CASE_KEYS, f"input case {index}")
        case_id = _string(case.get("case_id"), f"case {index} ID")
        if not CASE_ID_RE.fullmatch(case_id):
            raise E01ContractError(f"case {index} ID is invalid")
        if case_id in seen_case_ids:
            raise E01ContractError(f"duplicate input case ID: {case_id}")
        seen_case_ids.add(case_id)
        item_sha = _sha256(
            case.get("source_item_id_sha256"),
            f"case {index} source item SHA",
        )
        _sha256(
            case.get("source_audio_object_id_sha256"),
            f"case {index} audio object SHA",
        )
        _sha256(case.get("source_audio_sha256"), f"case {index} audio SHA")
        perturbation_id = _string(
            case.get("perturbation_id"),
            f"case {index} perturbation",
        )
        if perturbation_id not in PERTURBATION_IDS:
            raise E01ContractError(f"case {index} perturbation is invalid")
        observed_families.add(perturbation_id)
        parameter_sha = _sha256(
            case.get("parameter_set_sha256"),
            f"case {index} parameter SHA",
        )
        _sha256(
            case.get("expected_relation_id_sha256"),
            f"case {index} relation SHA",
        )
        target = case.get("target_region_sha256")
        target_sha: str | None
        if target is None:
            target_sha = None
        else:
            target_sha = _sha256(target, f"case {index} target region SHA")
        if perturbation_id in {"duration", "phone"} and target_sha is None:
            raise E01ContractError(
                f"{perturbation_id} case requires an opaque target region"
            )
        if perturbation_id in {"silence", "gain", "f0"} and target_sha is not None:
            raise E01ContractError(
                f"{perturbation_id} case cannot expose a target region"
            )
        semantic = (item_sha, perturbation_id, parameter_sha, target_sha)
        if semantic in seen_semantic:
            raise E01ContractError(f"duplicate semantic input case at index {index}")
        seen_semantic.add(semantic)
    _expect(observed_families, set(PERTURBATION_IDS), "input perturbation coverage")
    return declared_count


def _validate_counts_and_rate(
    value: Mapping[str, Any],
    *,
    name: str,
) -> tuple[int, int, int, int, int, float | None]:
    case_count = _integer(value.get("case_count"), f"{name} case count")
    evaluable = _integer(value.get("evaluable_count"), f"{name} evaluable count")
    passed = _integer(value.get("passed_count"), f"{name} passed count")
    failed = _integer(value.get("failed_count"), f"{name} failed count")
    invalid = _integer(value.get("invalid_count"), f"{name} invalid count")
    _expect(evaluable, passed + failed, f"{name} evaluable count")
    _expect(case_count, evaluable + invalid, f"{name} case accounting")
    rate = _nullable_rate(value.get("pass_rate"), f"{name} pass rate")
    expected_rate = None if evaluable == 0 else passed / evaluable
    if rate is None:
        _expect(expected_rate, None, f"{name} null pass rate")
    elif expected_rate is None or not math.isclose(
        rate,
        expected_rate,
        rel_tol=0,
        abs_tol=1e-12,
    ):
        raise E01ContractError(f"{name} pass rate differs from aggregate counts")
    return case_count, evaluable, passed, failed, invalid, rate


def validate_report_payload(
    report: Mapping[str, Any],
    *,
    expected_config_sha256: str | None = None,
    expected_s108_commit_sha256: str | None = None,
    expected_model_identity_sha256: str | None = None,
    expected_checkpoint_sha256: str | None = None,
    expected_preregistration_sha256: str | None = None,
    expected_input_manifest_sha256: str | None = None,
    expected_relations: Mapping[str, str] | None = None,
) -> str:
    """Validate a future A1 aggregate report; no metric is computed here."""

    _reject_nonfinite_tree(report, "E01 report")
    forbidden = _collect_forbidden_report_fields(report)
    if forbidden:
        raise E01ContractError(
            f"E01 report contains forbidden A1 fields: {sorted(forbidden)}"
        )
    _expect_keys(report, REPORT_KEYS, "E01 report")
    _expect(
        report.get("schema_version"),
        "voice_coach.e01_metamorphic_report.v1",
        "report schema",
    )
    report_id = _string(report.get("report_id"), "report ID")
    if not REPORT_ID_RE.fullmatch(report_id):
        raise E01ContractError("report ID is invalid")
    _expect(report.get("status"), "completed", "report status")
    _expect(report.get("privacy_zone"), "A1_aggregate_only", "report privacy")
    for field in (
        "contains_row_level_data",
        "contains_direct_or_pseudonymous_ids",
        "contains_free_text",
        "publication_allowed",
    ):
        _expect(report.get(field), False, f"report {field}")
    _expect(report.get("locked_test_access_count"), 0, "locked-test access")
    _expect(
        report.get("s203_expert_holdout_access_count"),
        0,
        "S203 holdout access",
    )
    _expect(
        report.get("metric_computation_external_to_static_package"),
        True,
        "external metric computation marker",
    )
    bindings = {
        "config_sha256": _sha256(report.get("config_sha256"), "report config SHA"),
        "s108_commit_sha256": _sha256(
            report.get("s108_commit_sha256"),
            "report S108 SHA",
        ),
        "selected_model_identity_sha256": _sha256(
            report.get("selected_model_identity_sha256"),
            "report model identity SHA",
        ),
        "selected_checkpoint_sha256": _sha256(
            report.get("selected_checkpoint_sha256"),
            "report checkpoint SHA",
        ),
        "preregistration_sha256": _sha256(
            report.get("preregistration_sha256"),
            "report preregistration SHA",
        ),
        "input_manifest_sha256": _sha256(
            report.get("input_manifest_sha256"),
            "report input manifest SHA",
        ),
    }
    expected = {
        "config_sha256": expected_config_sha256,
        "s108_commit_sha256": expected_s108_commit_sha256,
        "selected_model_identity_sha256": expected_model_identity_sha256,
        "selected_checkpoint_sha256": expected_checkpoint_sha256,
        "preregistration_sha256": expected_preregistration_sha256,
        "input_manifest_sha256": expected_input_manifest_sha256,
    }
    for key, expected_value in expected.items():
        if expected_value is not None:
            _expect(bindings[key], expected_value, f"report {key} binding")

    raw_results = _list(
        report.get("perturbation_results"),
        "perturbation results",
    )
    if len(raw_results) != len(PERTURBATION_IDS):
        raise E01ContractError("report must contain exactly five perturbation results")
    totals = [0, 0, 0, 0, 0]
    for index, (raw, expected_id) in enumerate(
        zip(raw_results, PERTURBATION_IDS, strict=True)
    ):
        result = _mapping(raw, f"perturbation result {index}")
        _expect_keys(
            result,
            PERTURBATION_RESULT_KEYS,
            f"perturbation result {index}",
        )
        _expect(result.get("perturbation_id"), expected_id, "perturbation result ID")
        relation = _string(result.get("expectation_type"), f"{expected_id} relation")
        if relation not in RELATION_TYPES:
            raise E01ContractError(f"{expected_id} expectation type is invalid")
        if expected_relations is not None:
            _expect(
                relation,
                expected_relations[expected_id],
                f"{expected_id} preregistered relation",
            )
        (
            case_count,
            evaluable_count,
            passed_count,
            failed_count,
            invalid_count,
            _,
        ) = _validate_counts_and_rate(result, name=expected_id)
        totals[0] += case_count
        totals[1] += evaluable_count
        totals[2] += passed_count
        totals[3] += failed_count
        totals[4] += invalid_count
        lower = _nullable_rate(
            result.get("confidence_interval_lower"),
            f"{expected_id} CI lower",
        )
        upper = _nullable_rate(
            result.get("confidence_interval_upper"),
            f"{expected_id} CI upper",
        )
        if lower is not None and upper is not None and lower > upper:
            raise E01ContractError(f"{expected_id} confidence interval is reversed")
        decision = _string(result.get("decision"), f"{expected_id} decision")
        if decision not in {"passed", "failed", "blocked"}:
            raise E01ContractError(f"{expected_id} decision is invalid")

    overall = _mapping(report.get("overall"), "overall result")
    _expect_keys(overall, OVERALL_KEYS, "overall result")
    counts = _validate_counts_and_rate(overall, name="overall")
    _expect(tuple(totals), counts[:5], "overall aggregate counts")
    _expect(
        overall.get("metric"),
        "metamorphic_expectation_pass_rate",
        "overall metric",
    )
    _expect(overall.get("operator"), "greater_than_or_equal", "overall operator")
    _expect(overall.get("threshold"), PASS_THRESHOLD, "overall threshold")
    statistic = _nullable_rate(
        overall.get("gate_statistic_value"),
        "overall gate statistic",
    )
    decision = _string(overall.get("decision"), "overall decision")
    if decision == "passed":
        if statistic is None or statistic < PASS_THRESHOLD:
            raise E01ContractError(
                "passed report requires gate statistic >= fixed threshold"
            )
    elif decision == "failed":
        if statistic is None or statistic >= PASS_THRESHOLD:
            raise E01ContractError(
                "failed report requires gate statistic below fixed threshold"
            )
    elif decision == "blocked":
        if statistic is not None:
            raise E01ContractError("blocked report cannot carry a gate statistic")
    else:
        raise E01ContractError("overall decision is invalid")
    return decision


def _require_resolved_sha(
    value: object,
    name: str,
    *,
    stage: str,
    audit: AccessAudit,
) -> str:
    if isinstance(value, str) and value.startswith(TBD_PREFIX):
        raise E01ContractError(
            f"{name} is {value}",
            stage=stage,
            audit=audit,
        )
    try:
        return _sha256(value, name)
    except E01ContractError as exc:
        raise E01ContractError(str(exc), stage=stage, audit=audit) from exc


def _copy_delegate_audit(source: object, target: AccessAudit) -> None:
    metadata_attempts = int(
        getattr(source, "predecessor_metadata_read_attempt_count", 0)
    )
    metadata_reads = int(getattr(source, "predecessor_metadata_read_count", 0))
    artifact_reads = int(getattr(source, "predecessor_artifact_read_count", 0))
    target.predecessor_read_attempt_count += metadata_attempts + artifact_reads
    target.predecessor_read_count += metadata_reads + artifact_reads
    for source_name, target_name in (
        ("data_row_read_count", "input_case_read_count"),
        ("audio_read_count", "raw_audio_read_count"),
        ("model_path_resolution_count", "model_path_resolution_count"),
        ("model_weight_read_count", "model_weight_read_count"),
        ("model_download_count", "network_access_count"),
        ("network_access_count", "network_access_count"),
        ("gpu_access_count", "gpu_access_count"),
        ("metric_count", "metric_computation_count"),
        ("locked_test_read_count", "locked_test_read_count"),
        ("expert_holdout_read_count", "s203_expert_holdout_read_count"),
        ("output_path_resolution_count", "output_path_resolution_count"),
        ("output_count", "output_count"),
    ):
        setattr(
            target,
            target_name,
            int(getattr(target, target_name)) + int(getattr(source, source_name, 0)),
        )


def deep_validate_s108_final(
    *,
    root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> S108Evidence:
    """Deep-validate actual S108 aggregate completion and selected bindings."""

    stage = "s108_final"
    predecessor = _mapping(config.get("predecessor_s108"), "S108 predecessor")
    expected_commit_sha = _require_resolved_sha(
        predecessor.get("completion_commit_sha256"),
        "S108 completion commit SHA",
        stage=stage,
        audit=audit,
    )
    expected_model_sha = _require_resolved_sha(
        predecessor.get("selected_model_identity_sha256"),
        "S108 selected model identity SHA",
        stage=stage,
        audit=audit,
    )
    expected_checkpoint_sha = _require_resolved_sha(
        predecessor.get("selected_checkpoint_sha256"),
        "S108 selected checkpoint SHA",
        stage=stage,
        audit=audit,
    )
    expected_calibrator_sha = _require_resolved_sha(
        predecessor.get("selected_calibrator_sha256"),
        "S108 selected calibrator SHA",
        stage=stage,
        audit=audit,
    )
    expected_threshold_sha = _require_resolved_sha(
        predecessor.get("selected_threshold_policy_sha256"),
        "S108 selected threshold-policy SHA",
        stage=stage,
        audit=audit,
    )
    delegate_relative = _relative_string(
        predecessor.get("delegate_config_path"),
        "S108 delegate config path",
    )
    audit.predecessor_path_resolution_count += 1
    audit.predecessor_read_attempt_count += 1
    try:
        from voice_coach.training import (  # noqa: PLC0415
            s2_s202_auditor_contract as s202,
        )

        delegate_config = s202.load_config(root / delegate_relative)
        audit.predecessor_read_count += 1
        delegate_audit = s202.AccessAudit()
        delegated = s202.deep_validate_s108_final(
            root=root,
            config=delegate_config,
            audit=delegate_audit,
        )
    except Exception as exc:
        if "delegate_audit" in locals():
            _copy_delegate_audit(delegate_audit, audit)
        raise E01ContractError(
            f"S108 deep validation failed: {exc}",
            stage=stage,
            audit=audit,
        ) from exc
    _copy_delegate_audit(delegate_audit, audit)
    inner = getattr(delegated, "inner", None)
    snapshot_value = getattr(inner, "commit_snapshot", None)
    if snapshot_value is None:
        raise E01ContractError(
            "S108 delegate returned no commit snapshot",
            stage=stage,
            audit=audit,
        )
    snapshot = FileSnapshot(
        path=_relative_string(snapshot_value.path, "S108 commit snapshot path"),
        sha256=_sha256(snapshot_value.sha256, "S108 commit snapshot SHA"),
        device=int(snapshot_value.device),
        inode=int(snapshot_value.inode),
        size=int(snapshot_value.size),
        mtime_ns=int(snapshot_value.mtime_ns),
    )
    _expect(snapshot.sha256, expected_commit_sha, "S108 completion SHA")
    _expect(
        snapshot.path,
        predecessor.get("completion_commit_path"),
        "S108 completion path",
    )
    if expected_model_sha != expected_commit_sha:
        raise E01ContractError(
            "selected model identity must be the deeply validated S108 commit SHA",
            stage=stage,
            audit=audit,
        )
    data, observed = _read_file(root, snapshot.path, "S108 completion commit")
    audit.predecessor_read_attempt_count += 1
    audit.predecessor_read_count += 1
    _expect(observed, snapshot, "S108 commit snapshot")
    commit = _parse_json(data, "S108 completion commit")
    hashes = _mapping(commit.get("artifact_hashes"), "S108 artifact hashes")
    _expect(
        _sha256(hashes.get("final_fusion_checkpoint"), "S108 fusion checkpoint"),
        expected_checkpoint_sha,
        "S108 selected checkpoint",
    )
    _expect(
        _sha256(hashes.get("calibrator_parameters"), "S108 calibrator"),
        expected_calibrator_sha,
        "S108 selected calibrator",
    )
    _expect(
        _sha256(hashes.get("threshold_policy"), "S108 threshold policy"),
        expected_threshold_sha,
        "S108 selected threshold policy",
    )
    _verify_snapshot(root, snapshot, "S108 final commit")
    audit.s108_deep_validation_count = 1
    return S108Evidence(
        commit_snapshot=snapshot,
        selected_model_identity_sha256=expected_model_sha,
        selected_checkpoint_sha256=expected_checkpoint_sha,
    )


def validate_frozen_preregistration(
    *,
    root: Path,
    config: Mapping[str, Any],
    s108: S108Evidence,
    audit: AccessAudit,
) -> PreregistrationEvidence:
    """Validate fully resolved E01 policy before any D1 path resolution."""

    stage = "preregistration"
    contract = _mapping(config.get("preregistration"), "preregistration")
    expected_sha = _require_resolved_sha(
        contract.get("approved_artifact_sha256"),
        "E01 preregistration SHA",
        stage=stage,
        audit=audit,
    )
    relative = _relative_string(
        contract.get("approved_artifact_path"),
        "E01 preregistration path",
    )
    audit.preregistration_path_resolution_count += 1
    audit.preregistration_read_attempt_count += 1
    try:
        data, snapshot = _read_file(root, relative, "E01 preregistration")
    except E01ContractError as exc:
        raise E01ContractError(
            f"E01 preregistration unavailable: {exc}",
            stage=stage,
            audit=audit,
        ) from exc
    audit.preregistration_read_count += 1
    _expect(snapshot.sha256, expected_sha, "E01 preregistration SHA")
    document = _parse_json(data, "E01 preregistration")
    _expect(
        document.get("schema_version"),
        contract.get("required_schema_version"),
        "E01 preregistration schema",
    )
    _validate_preregistration_template(document, draft=False)
    _expect(
        document.get("s108_commit_sha256"),
        s108.commit_snapshot.sha256,
        "preregistration S108 binding",
    )
    _expect(
        document.get("selected_model_identity_sha256"),
        s108.selected_model_identity_sha256,
        "preregistration model binding",
    )
    _expect(
        document.get("selected_checkpoint_sha256"),
        s108.selected_checkpoint_sha256,
        "preregistration checkpoint binding",
    )
    integrity = _mapping(
        document.get("execution_integrity"),
        "preregistration integrity",
    )
    observed_binding = _sha256(
        integrity.get("binding_sha256"),
        "preregistration binding SHA",
    )
    unsigned = dict(document)
    unsigned_integrity = dict(integrity)
    unsigned_integrity.pop("binding_sha256", None)
    unsigned["execution_integrity"] = unsigned_integrity
    _expect(
        observed_binding,
        hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest(),
        "preregistration binding",
    )
    _verify_snapshot(root, snapshot, "E01 preregistration")
    return PreregistrationEvidence(snapshot=snapshot, document=document)


def run_full_preflight(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> PreflightResult:
    """Run read-only ordered readiness and stop before all runtime inputs."""

    root = workspace_root.resolve(strict=True)
    audit = AccessAudit()
    try:
        static = validate_static_config(
            workspace_root=root,
            config_path=config_path,
            config=config,
            audit=audit,
        )
        s108 = deep_validate_s108_final(root=root, config=config, audit=audit)
        preregistration = validate_frozen_preregistration(
            root=root,
            config=config,
            s108=s108,
            audit=audit,
        )
        for snapshot in static.source_snapshots:
            _verify_snapshot(root, snapshot, f"E01 source {snapshot.path}")
            audit.source_pin_verify_count += 1
    except E01ContractError as exc:
        if exc.audit is None:
            exc.audit = audit
        raise
    return PreflightResult(
        status="ready_for_separate_non_locked_execution",
        config_sha256=static.config_sha256,
        s108_commit_sha256=s108.commit_snapshot.sha256,
        preregistration_sha256=preregistration.snapshot.sha256,
        audit={
            key: int(value)
            for key, value in asdict(audit).items()
            if isinstance(value, int)
        },
    )


def _output_target(root: Path, relative: str, name: str) -> Path:
    normalized = _relative_string(relative, f"{name} path")
    path = root
    for part in PurePosixPath(normalized).parts:
        path = path / part
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return root / normalized
        if stat.S_ISLNK(metadata.st_mode):
            raise E01ContractError(f"{name} cannot traverse a symlink")
    return path


def ensure_output_targets_absent(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
) -> None:
    """Enforce no-overwrite for a future external atomic publisher."""

    root = workspace_root.resolve(strict=True)
    outputs = _mapping(config.get("outputs"), "outputs")
    for field in (
        "aggregate_report_path",
        "commit_path",
        "registry_path",
        "failure_report_path",
    ):
        relative = _relative_string(outputs.get(field), f"output {field}")
        target = _output_target(root, relative, f"output {field}")
        if target.exists():
            raise E01ContractError(f"output {field} already exists")


def validate_completed_bundle(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    before_final_verify_hook: Callable[[], None] | None = None,
) -> str:
    """Atomically validate a future A1 report/registry/commit bundle."""

    root = workspace_root.resolve(strict=True)
    static = validate_static_config(
        workspace_root=root,
        config_path=config_path,
        config=config,
    )
    outputs = _mapping(config.get("outputs"), "outputs")
    report_path = _relative_string(
        outputs.get("aggregate_report_path"),
        "E01 report path",
    )
    commit_path = _relative_string(outputs.get("commit_path"), "E01 commit path")
    registry_path = _relative_string(
        outputs.get("registry_path"),
        "E01 registry path",
    )
    commit_data, commit_snapshot = _read_file(root, commit_path, "E01 commit")
    commit = _parse_json(commit_data, "E01 commit")
    _expect_keys(
        commit,
        {
            "schema_version",
            "status",
            "config_sha256",
            "s108_commit_sha256",
            "selected_model_identity_sha256",
            "selected_checkpoint_sha256",
            "preregistration_sha256",
            "input_manifest_sha256",
            "decision",
            "report",
            "registry",
            "binding_sha256",
        },
        "E01 commit",
    )
    _expect(
        commit.get("schema_version"),
        "voice_coach.e01_metamorphic_commit.v1",
        "E01 commit schema",
    )
    _expect(commit.get("status"), "committed", "E01 commit status")
    _expect(commit.get("config_sha256"), static.config_sha256, "E01 config binding")
    _expect(
        _sha256(commit.get("binding_sha256"), "E01 commit binding"),
        _binding_sha256(commit),
        "E01 commit binding",
    )
    report_record = _mapping(commit.get("report"), "E01 report record")
    registry_record = _mapping(commit.get("registry"), "E01 registry record")
    _expect(report_record.get("path"), report_path, "E01 report path binding")
    _expect(registry_record.get("path"), registry_path, "E01 registry path binding")
    report_sha = _sha256(report_record.get("sha256"), "E01 report SHA")
    registry_sha = _sha256(registry_record.get("sha256"), "E01 registry SHA")

    report_data, report_snapshot = _read_file(root, report_path, "E01 report")
    _expect(report_snapshot.sha256, report_sha, "E01 report SHA")
    report = _parse_json(report_data, "E01 report")
    decision = validate_report_payload(
        report,
        expected_config_sha256=static.config_sha256,
        expected_s108_commit_sha256=_sha256(
            commit.get("s108_commit_sha256"),
            "commit S108 SHA",
        ),
        expected_model_identity_sha256=_sha256(
            commit.get("selected_model_identity_sha256"),
            "commit model identity SHA",
        ),
        expected_checkpoint_sha256=_sha256(
            commit.get("selected_checkpoint_sha256"),
            "commit checkpoint SHA",
        ),
        expected_preregistration_sha256=_sha256(
            commit.get("preregistration_sha256"),
            "commit preregistration SHA",
        ),
        expected_input_manifest_sha256=_sha256(
            commit.get("input_manifest_sha256"),
            "commit input manifest SHA",
        ),
    )
    _expect(commit.get("decision"), decision, "E01 commit decision")

    registry_data, registry_snapshot = _read_file(
        root,
        registry_path,
        "E01 registry",
    )
    _expect(registry_snapshot.sha256, registry_sha, "E01 registry SHA")
    registry = _parse_json(registry_data, "E01 registry")
    _expect_keys(
        registry,
        {
            "schema_version",
            "run_id",
            "status",
            "config_sha256",
            "artifacts",
            "binding_sha256",
        },
        "E01 registry",
    )
    _expect(
        registry.get("schema_version"),
        "voice_coach.e01_metamorphic_registry.v1",
        "E01 registry schema",
    )
    _expect(
        registry.get("run_id"),
        "e01-metamorphic-robustness-v1",
        "E01 registry run ID",
    )
    _expect(registry.get("status"), "completed", "E01 registry status")
    _expect(registry.get("config_sha256"), static.config_sha256, "registry config")
    _expect(
        _sha256(registry.get("binding_sha256"), "E01 registry binding"),
        _binding_sha256(registry),
        "E01 registry binding",
    )
    _expect(
        registry.get("artifacts"),
        [
            {
                "name": "aggregate_report",
                "path": report_path,
                "sha256": report_sha,
                "schema_version": "voice_coach.e01_metamorphic_report.v1",
            }
        ],
        "E01 registry artifacts",
    )

    if before_final_verify_hook is not None:
        before_final_verify_hook()
    for snapshot, name in (
        (commit_snapshot, "commit"),
        (report_snapshot, "report"),
        (registry_snapshot, "registry"),
        *[(item, f"source {item.path}") for item in static.source_snapshots],
    ):
        _verify_snapshot(root, snapshot, f"E01 completed bundle {name}")
    return decision


def _audit_dict(audit: AccessAudit) -> dict[str, int]:
    return {
        key: int(value)
        for key, value in asdict(audit).items()
        if isinstance(value, int)
    }


def config_only_result(
    static: StaticValidation,
    audit: AccessAudit,
) -> Mapping[str, Any]:
    return {
        "status": "valid_config_only",
        "config_sha256": static.config_sha256,
        "policy_sha256": static.policy_sha256,
        "source_pin_count": len(static.source_snapshots),
        **_audit_dict(audit),
        "actual_audio_perturbation_or_model_execution": False,
        "actual_metric_or_decision": False,
        "locked_test_accessed": False,
        "s203_expert_holdout_accessed": False,
        "report_created": False,
        "e01_completion_claim_allowed": False,
        "publication_allowed": False,
    }


def failure_result(error: E01ContractError) -> Mapping[str, Any]:
    audit = error.audit if error.audit is not None else AccessAudit()
    return {
        "status": "failed_closed",
        "stage": error.stage,
        "error": str(error),
        **_audit_dict(audit),
        "actual_audio_perturbation_or_model_execution": False,
        "actual_metric_or_decision": False,
        "locked_test_accessed": False,
        "s203_expert_holdout_accessed": False,
        "report_created": False,
        "e01_completion_claim_allowed": False,
        "publication_allowed": False,
    }
