"""Fail-closed R03 Pareto/diversity reference-bank static contract.

This module is deliberately read-only.  It validates a production selection
*contract* but never opens candidate audio, runs a model/GPU, computes an
objective, selects a candidate, writes a bank, or reads an R04 label.

The runtime order is security-relevant:

1. validate pinned C1/static sources, schema, and preregistration template;
2. recompute and match the completed R00 public/aggregate report;
3. deep-validate the completed R01 private candidate generation;
4. deep-validate the completed R02 hard-gate commit and eligible partition;
5. require an approved, immutable R03 preregistration with no TBD value;
6. only then permit a separate executor to resolve eligible D1 candidate data.

The canonical configuration has no R01/R02 completion artifacts and contains
``TBD_PREREGISTRATION`` for every operational choice not supported by the
paper or implementation plan.  It therefore fails before candidate data or
output paths can be resolved.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import importlib.util
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
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CANDIDATE_ID_RE = re.compile(r"^r01c_[0-9a-f]{32}$")
SENTENCE_IDS = tuple(f"R00-{index:02d}" for index in range(1, 21))
SOURCE_KINDS = ("human_recording", "cosyvoice_tts")
SPEED_BUCKETS = ("slow", "neutral", "fast")
PITCH_BUCKETS = ("low", "neutral", "high")

TOP_LEVEL_KEYS = {
    "schema_version",
    "experiment",
    "canonical_sources",
    "predecessor_r00",
    "predecessor_r01",
    "predecessor_r02",
    "preregistration",
    "selection_contract",
    "blindness_and_cherry_pick_controls",
    "lineage_contract",
    "privacy_and_publication",
    "execution_order",
    "outputs",
}

SOURCE_PATHS = (
    "artifacts/reference/r00/r00_sentence_card_v1.validation.json",
    "configs/experiments/r01_human_cosyvoice_candidates_v1.yaml",
    "configs/experiments/r02_candidate_hard_gate_v1.yaml",
    "configs/reference/r00_sentence_card_v1.schema.json",
    "configs/reference/r01_candidate_manifest_v1.schema.json",
    "configs/reference/r02_hard_gate_report_v1.schema.json",
    "configs/reference/r02_gate_preregistration_v1.template.json",
    "configs/reference/r03_reference_bank_manifest_v1.schema.json",
    "configs/reference/r03_pareto_diversity_preregistration_v1.template.json",
    "docs/IMPLEMENTATION_PLAN_KO.md",
    "docs/paper/EXPERIMENTAL_MANUSCRIPT_KO.md",
    "docs/한국어 AI 보이스 코칭을 위한 폐쇄루프 TTS 아키텍처.pdf",
    "scripts/prepare_r01_human_cosyvoice_candidates.py",
    "scripts/prepare_r02_candidate_hard_gate.py",
    "scripts/validate_r00_sentence_card.py",
    "src/voice_coach/references/r01_candidate_contract.py",
    "src/voice_coach/references/r02_hard_gate_contract.py",
)

EXPECTED_EXECUTION_ORDER = (
    "validate_r03_static_config_sources_schema_and_prereg_template",
    "deep_validate_r00_valid_completion",
    "deep_validate_r01_completed_candidate_generation",
    "deep_validate_r02_completed_hard_gate",
    "validate_frozen_r03_preregistration_without_tbd",
    "resolve_r02_eligible_candidate_aggregate_and_lineage_only",
    "verify_source_blind_selection_view_and_immutable_lineage",
    "stop_ready_for_separate_selection_execution",
)

EXPECTED_SELECTOR_ALLOWLIST = (
    "candidate_id",
    "sentence_id",
    "target_text_sha256",
    "r02_eligible_row_sha256",
    "objective_vector",
    "speed_bucket",
    "pitch_bucket",
    "blinded_speaker_cluster_id_sha256",
)

EXPECTED_SELECTOR_DENYLIST = (
    "source_kind",
    "source_attestation_sha256",
    "human_or_tts",
    "model_name",
    "r04_label",
    "r04_score",
    "mos",
    "cmos",
    "learner_outcome",
    "learning_gain",
    "retention",
    "publication_preference",
)

FORBIDDEN_RECURSIVE_FIELDS = (
    "speaker_name",
    "legal_name",
    "raw_speaker_id",
    "source_path",
    "audio_path",
    "recording_path",
    "target_text",
    "transcript",
    "free_text_note",
    "r04_label",
    "r04_score",
    "mos",
    "cmos",
    "learner_outcome",
    "learning_gain",
    "retention",
)

BANK_TOP_LEVEL_KEYS = {
    "schema_version",
    "bank_id",
    "status",
    "r00_card_sha256",
    "r01_commit_sha256",
    "r02_commit_sha256",
    "r03_config_sha256",
    "r03_preregistration_sha256",
    "privacy_zone",
    "publication_allowed",
    "r04_blind_evaluation_completed",
    "selection_blinded_to_source_kind",
    "selection_blinded_to_r04_and_outcomes",
    "sentences",
}

BANK_SENTENCE_KEYS = {
    "sentence_id",
    "eligible_candidate_count",
    "selected_count",
    "source_balance_verified_after_blind_selection",
    "speaker_diversity_verified",
    "speed_diversity_verified",
    "pitch_diversity_verified",
    "selected_candidates",
}

BANK_CANDIDATE_KEYS = {
    "candidate_id",
    "target_text_sha256",
    "audio_object_id_sha256",
    "audio_sha256",
    "source_kind",
    "source_attestation_sha256",
    "speaker_id_sha256",
    "speed_bucket",
    "pitch_bucket",
    "r02_eligible_row_sha256",
    "objective_vector_sha256",
    "pareto_member",
    "immutable_lineage_verified",
}

AGGREGATE_KEYS = {
    "schema_version",
    "status",
    "privacy_zone",
    "r03_commit_sha256",
    "sentence_count",
    "selected_candidate_count",
    "selected_count_histogram",
    "source_kind_counts",
    "minimum_distinct_speakers_per_sentence",
    "minimum_distinct_speed_buckets_per_sentence",
    "minimum_distinct_pitch_buckets_per_sentence",
    "individual_candidate_rows_present",
    "candidate_ids_present",
    "speaker_ids_present",
    "audio_hashes_present",
    "r04_blind_evaluation_completed",
    "publication_allowed",
}

PREREG_TOP_LEVEL_KEYS = {
    "schema_version",
    "preregistration_id",
    "status",
    "registered_at_utc",
    "registered_by_role",
    "frozen_before_r02_eligible_data_access",
    "r00_card_sha256",
    "r01_config_sha256",
    "r02_config_sha256",
    "objective_contract",
    "normalization_contract",
    "pareto_contract",
    "diversity_contract",
    "selection_contract",
    "blindness_contract",
    "approval",
}

PREREG_SECTION_KEYS = {
    "objective_contract": {
        "objective_ids",
        "naturalness_measurement_source",
        "prosody_fit_measurement_source",
        "pedagogical_clarity_measurement_source",
        "diversity_measurement_source",
        "direction_by_objective",
        "missing_value_policy",
    },
    "normalization_contract": {
        "method",
        "fit_population",
        "fit_scope",
        "clipping_policy",
        "constant_objective_policy",
    },
    "pareto_contract": {
        "dominance_definition",
        "epsilon_or_tolerance",
        "tie_policy",
        "frontier_shortfall_policy",
        "frontier_overflow_policy",
    },
    "diversity_contract": {
        "distance_metric",
        "distance_feature_allowlist",
        "feature_weights",
        "minimum_separation",
        "cluster_or_subsampling_algorithm",
        "speaker_distance_policy",
        "speed_distance_policy",
        "pitch_distance_policy",
    },
    "selection_contract": {
        "seed",
        "candidate_order_policy",
        "selected_count_policy",
        "source_balance_tie_break_policy",
        "determinism_replay_count",
    },
    "blindness_contract": {
        "selector_source_kind_blind",
        "selector_source_attestation_blind",
        "selector_r04_label_blind",
        "selector_mos_cmos_blind",
        "selector_learning_outcome_blind",
        "source_join_only_after_selection",
        "outcome_driven_reselection_allowed",
    },
    "approval": {
        "decision",
        "protocol_sha256",
        "signed_receipt_sha256",
    },
}


class R03ContractError(ValueError):
    """Raised before an unsafe, unregistered, or unsupported R03 action."""

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
            raise R03ContractError("YAML mapping keys must be strings")
        if key in result:
            raise R03ContractError(f"duplicate YAML key: {key}")
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
    r00_validator_load_count: int = 0
    r00_deep_validation_count: int = 0
    r00_public_or_aggregate_evidence_read_count: int = 0
    r00_raw_audio_read_count: int = 0
    r00_row_level_read_count: int = 0
    r01_completion_path_resolution_count: int = 0
    r01_completion_read_attempt_count: int = 0
    r01_completion_read_count: int = 0
    r01_registry_read_count: int = 0
    r01_manifest_read_count: int = 0
    r01_audio_read_count: int = 0
    r02_contract_load_count: int = 0
    r02_completion_path_resolution_count: int = 0
    r02_completion_read_attempt_count: int = 0
    r02_completion_read_count: int = 0
    r02_registry_read_count: int = 0
    r02_report_read_count: int = 0
    r02_lineage_read_count: int = 0
    r02_quarantine_partition_validation_count: int = 0
    selection_quarantine_row_read_count: int = 0
    preregistration_path_resolution_count: int = 0
    preregistration_read_attempt_count: int = 0
    preregistration_read_count: int = 0
    eligible_candidate_data_path_resolution_count: int = 0
    eligible_candidate_data_read_count: int = 0
    objective_metric_computation_count: int = 0
    pareto_computation_count: int = 0
    diversity_computation_count: int = 0
    selection_count: int = 0
    audio_read_count: int = 0
    audio_write_count: int = 0
    model_access_count: int = 0
    gpu_access_count: int = 0
    r04_label_read_count: int = 0
    bank_manifest_read_count: int = 0
    bank_manifest_write_count: int = 0
    aggregate_write_count: int = 0
    publication_count: int = 0
    output_path_resolution_count: int = 0
    output_count: int = 0

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
class R00Evidence:
    report_sha256: str
    report_snapshot: FileSnapshot


@dataclass(frozen=True)
class R01Evidence:
    commit_sha256: str
    snapshots: tuple[FileSnapshot, ...]
    r02_config: Mapping[str, Any]
    r02_config_sha256: str
    delegated_r01_evidence: object
    delegated_audit: object


@dataclass(frozen=True)
class R02Evidence:
    commit_sha256: str
    report_sha256: str
    snapshots: tuple[FileSnapshot, ...]


@dataclass(frozen=True)
class PreflightResult:
    status: str
    config_sha256: str
    r00_report_sha256: str
    r01_commit_sha256: str
    r02_commit_sha256: str
    preregistration_sha256: str
    audit: Mapping[str, int]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise R03ContractError(f"{name} must be a mapping")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise R03ContractError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise R03ContractError(f"{name} must be a non-empty string")
    return value


def _integer(value: object, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise R03ContractError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise R03ContractError(f"{name} must be >= {minimum}")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise R03ContractError(f"{name} must be boolean")
    return value


def _sha256(value: object, name: str) -> str:
    text = _string(value, name)
    if not SHA256_RE.fullmatch(text):
        raise R03ContractError(f"{name} must be a lowercase SHA-256")
    return text


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise R03ContractError(f"{name} drift: expected {expected!r}, got {actual!r}")


def _expect_keys(
    value: Mapping[str, Any],
    expected: set[str],
    name: str,
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise R03ContractError(f"{name} keys drift; missing={missing}, extra={extra}")


def _reject_nonfinite_tree(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise R03ContractError(f"{name} contains non-finite number")
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
            raise R03ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise R03ContractError(f"non-finite JSON constant prohibited: {value}")


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
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            keys.add(str(key))
            keys.update(_collect_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_collect_keys(item))
    return keys


def _relative_string(value: object, name: str) -> str:
    text = _string(value, name)
    pure = PurePosixPath(text)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in ("", ".", "..") for part in pure.parts)
        or "\\" in text
    ):
        raise R03ContractError(f"{name} must be a safe workspace-relative path")
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
    for index, part in enumerate(PurePosixPath(relative).parts):
        current = current / part
        is_leaf = index == len(PurePosixPath(relative).parts) - 1
        try:
            info = current.lstat()
        except FileNotFoundError:
            if not require_leaf:
                return root / relative
            raise R03ContractError(f"{name} missing: {relative}") from None
        if stat.S_ISLNK(info.st_mode):
            raise R03ContractError(f"{name} symlink prohibited: {relative}")
        if not is_leaf and not stat.S_ISDIR(info.st_mode):
            raise R03ContractError(f"{name} parent is not a directory: {relative}")
        if is_leaf and require_leaf and not stat.S_ISREG(info.st_mode):
            raise R03ContractError(f"{name} must be a regular file: {relative}")
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
            raise R03ContractError(f"{name} is not a regular file: {relative}")
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise R03ContractError(f"{name} changed before open: {relative}")
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
        raise R03ContractError(f"{name} changed while reading: {relative}")
    data = b"".join(chunks)
    digest = hashlib.sha256(data).hexdigest()
    return data, FileSnapshot(
        relative_path=relative,
        sha256=digest,
        size=len(data),
        device=after.st_dev,
        inode=after.st_ino,
        mtime_ns=after.st_mtime_ns,
    )


def _verify_snapshot(root: Path, snapshot: FileSnapshot, name: str) -> None:
    _, current = _read_file(root, snapshot.relative_path, name)
    if current != snapshot:
        raise R03ContractError(
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
        raise R03ContractError(f"{name} invalid JSON: {exc}") from exc
    payload = _mapping(value, name)
    _reject_nonfinite_tree(payload, name)
    return payload


def load_json(path: Path) -> Mapping[str, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise R03ContractError(f"cannot read JSON {path}: {exc}") from exc
    return _load_json_bytes(data, str(path))


def load_config(path: Path) -> Mapping[str, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise R03ContractError(f"cannot read config {path}: {exc}") from exc
    return _load_config_bytes(data)


def _load_config_bytes(data: bytes) -> Mapping[str, Any]:
    try:
        loaded = yaml.load(data.decode("utf-8"), Loader=_UniqueKeyLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise R03ContractError(f"invalid YAML config: {exc}") from exc
    config = _mapping(loaded, "config")
    _reject_nonfinite_tree(config, "config")
    return config


def _validate_bank_schema(schema: Mapping[str, Any]) -> None:
    _expect(
        schema.get("$schema"),
        "https://json-schema.org/draft/2020-12/schema",
        "bank schema dialect",
    )
    _expect(schema.get("type"), "object", "bank schema root type")
    _expect(schema.get("additionalProperties"), False, "bank schema strictness")
    _expect(
        set(_list(schema.get("required"), "bank schema required")),
        BANK_TOP_LEVEL_KEYS,
        "bank schema required fields",
    )
    properties = _mapping(schema.get("properties"), "bank schema properties")
    _expect(set(properties), BANK_TOP_LEVEL_KEYS, "bank schema root properties")
    definitions = _mapping(schema.get("$defs"), "bank schema definitions")
    sentence = _mapping(definitions.get("sentenceBank"), "sentenceBank schema")
    candidate = _mapping(
        definitions.get("selectedCandidate"),
        "selectedCandidate schema",
    )
    _expect(sentence.get("additionalProperties"), False, "sentence strictness")
    _expect(candidate.get("additionalProperties"), False, "candidate strictness")
    _expect(
        set(_list(sentence.get("required"), "sentence required")),
        BANK_SENTENCE_KEYS,
        "sentence required fields",
    )
    _expect(
        set(_list(candidate.get("required"), "candidate required")),
        BANK_CANDIDATE_KEYS,
        "candidate required fields",
    )


def _validate_prereg_template(template: Mapping[str, Any]) -> None:
    _expect_keys(template, PREREG_TOP_LEVEL_KEYS, "preregistration template")
    for section_name, expected_keys in PREREG_SECTION_KEYS.items():
        _expect_keys(
            _mapping(template.get(section_name), section_name),
            expected_keys,
            f"preregistration template {section_name}",
        )
    _expect(
        template.get("schema_version"),
        "voice_coach.r03_pareto_diversity_preregistration.v1",
        "preregistration schema version",
    )
    _expect(
        template.get("frozen_before_r02_eligible_data_access"),
        True,
        "preregistration freeze ordering",
    )
    blindness = _mapping(
        template.get("blindness_contract"),
        "preregistration blindness",
    )
    expected_blind = {
        "selector_source_kind_blind": True,
        "selector_source_attestation_blind": True,
        "selector_r04_label_blind": True,
        "selector_mos_cmos_blind": True,
        "selector_learning_outcome_blind": True,
        "source_join_only_after_selection": True,
        "outcome_driven_reselection_allowed": False,
    }
    _expect(dict(blindness), expected_blind, "preregistration blindness")
    objectives = _mapping(
        template.get("objective_contract"),
        "preregistration objectives",
    )
    _expect(
        tuple(_list(objectives.get("objective_ids"), "objective IDs")),
        ("naturalness", "prosody_fit", "pedagogical_clarity", "diversity"),
        "preregistration objective IDs",
    )
    if not _contains_tbd(template):
        raise R03ContractError(
            "preregistration template must expose unresolved choices as TBD"
        )
    for section in (
        "objective_contract",
        "normalization_contract",
        "pareto_contract",
        "diversity_contract",
        "selection_contract",
        "approval",
    ):
        if not _contains_tbd(_mapping(template.get(section), section)):
            raise R03ContractError(f"{section} must fail closed with TBD")


def _validate_config_values(config: Mapping[str, Any]) -> None:
    _expect_keys(config, TOP_LEVEL_KEYS, "config")
    _expect(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "schema_version")

    experiment = _mapping(config.get("experiment"), "experiment")
    _expect(experiment.get("id"), "r03_reference_bank_selection_v1", "experiment id")
    _expect(
        experiment.get("stage"),
        "wp7_r03_pareto_diversity_reference_bank",
        "experiment stage",
    )
    _expect(
        experiment.get("implementation_status"),
        STATIC_STATUS,
        "implementation status",
    )
    for field in (
        "actual_candidate_data_access_allowed",
        "actual_audio_read_allowed",
        "actual_audio_write_allowed",
        "actual_model_access_allowed",
        "actual_gpu_access_allowed",
        "actual_metric_computation_allowed",
        "actual_selection_allowed",
        "actual_bank_write_allowed",
        "r04_label_access_allowed",
        "publication_allowed",
        "completion_claim_allowed",
    ):
        _expect(experiment.get(field), False, f"experiment.{field}")

    r00 = _mapping(config.get("predecessor_r00"), "predecessor_r00")
    _expect(r00.get("deep_validation_required"), True, "R00 deep validation")
    _expect(r00.get("required_status"), "valid", "R00 status")
    _expect(r00.get("required_sentence_count"), 20, "R00 sentence count")
    _expect(
        r00.get("required_completion_claim_allowed"),
        True,
        "R00 completion",
    )
    _expect(
        r00.get("raw_audio_or_row_read_allowed"),
        False,
        "R00 raw/row boundary",
    )
    for field in (
        "card_sha256",
        "schema_sha256",
        "validator_sha256",
        "validation_report_sha256",
    ):
        _sha256(r00.get(field), f"predecessor_r00.{field}")

    r01 = _mapping(config.get("predecessor_r01"), "predecessor_r01")
    _expect(r01.get("deep_validation_required"), True, "R01 deep validation")
    _expect(r01.get("validator_owner"), "r02_hard_gate_contract", "R01 validator")
    _expect(r01.get("required_status"), "completed", "R01 status")
    _expect(r01.get("required_sentence_count"), 20, "R01 sentence count")
    _expect(r01.get("candidate_count_minimum"), 160, "R01 minimum candidates")
    _expect(r01.get("candidate_count_maximum"), 320, "R01 maximum candidates")
    _expect(r01.get("required_r02_hard_gate_status"), "not_run", "R01 R02 status")
    _expect(
        r01.get("publication_allowed_must_be_false"),
        True,
        "R01 publication boundary",
    )
    for field in ("config_sha256", "contract_sha256"):
        _sha256(r01.get(field), f"predecessor_r01.{field}")
    r01_completion_sha = _string(
        r01.get("completion_commit_sha256"),
        "predecessor_r01.completion_commit_sha256",
    )
    if not r01_completion_sha.startswith("TBD_"):
        _sha256(
            r01_completion_sha,
            "predecessor_r01.completion_commit_sha256",
        )

    r02 = _mapping(config.get("predecessor_r02"), "predecessor_r02")
    _expect(r02.get("deep_validation_required"), True, "R02 deep validation")
    _expect(r02.get("required_status"), "completed", "R02 status")
    _expect(r02.get("required_sentence_count"), 20, "R02 sentence count")
    _expect(r02.get("eligible_only_for_r03"), True, "R02 eligible-only")
    _expect(
        r02.get("quarantine_access_by_r03_allowed"),
        False,
        "R02 quarantine boundary",
    )
    _expect(
        r02.get("publication_allowed_must_be_false"),
        True,
        "R02 publication boundary",
    )
    for field in ("config_sha256", "contract_sha256"):
        _sha256(r02.get(field), f"predecessor_r02.{field}")
    completion_sha = _string(
        r02.get("completion_commit_sha256"),
        "predecessor_r02.completion_commit_sha256",
    )
    if not completion_sha.startswith("TBD_"):
        _sha256(completion_sha, "predecessor_r02.completion_commit_sha256")

    prereg = _mapping(config.get("preregistration"), "preregistration")
    _expect(
        prereg.get("placeholder"),
        TBD_PREREGISTRATION,
        "preregistration placeholder",
    )
    _expect(
        prereg.get("required_schema_version"),
        "voice_coach.r03_pareto_diversity_preregistration.v1",
        "preregistration schema",
    )
    _expect(
        prereg.get("required_status"),
        "approved_and_frozen",
        "preregistration status",
    )
    _expect(
        prereg.get("must_be_frozen_before_r02_eligible_data_access"),
        True,
        "preregistration order",
    )
    _expect(
        prereg.get("unresolved_fields_must_fail_closed"),
        True,
        "preregistration TBD policy",
    )

    selection = _mapping(config.get("selection_contract"), "selection_contract")
    _expect(
        tuple(_list(selection.get("sentence_ids"), "sentence_ids")),
        SENTENCE_IDS,
        "sentence IDs",
    )
    count = _mapping(selection.get("selected_per_sentence"), "selected count")
    _expect(count.get("minimum"), 3, "selected minimum")
    _expect(count.get("maximum"), 5, "selected maximum")
    objectives = _mapping(selection.get("objectives"), "objectives")
    _expect(
        tuple(_list(objectives.get("names_from_paper"), "objective names")),
        ("naturalness", "prosody_fit", "pedagogical_clarity", "diversity"),
        "paper-backed objective names",
    )
    for section_name in (
        "objectives",
        "normalization",
        "pareto",
        "deterministic_selection",
    ):
        section = _mapping(selection.get(section_name), section_name)
        if not _contains_tbd(section):
            raise R03ContractError(
                f"{section_name} must retain TBD until preregistration"
            )
    diversity = _mapping(selection.get("diversity"), "diversity")
    for field in (
        "distance_metric",
        "distance_feature_allowlist",
        "feature_weights",
        "minimum_separation",
        "cluster_or_subsampling_algorithm",
        "speaker_distance_policy",
        "speed_distance_policy",
        "pitch_distance_policy",
    ):
        _expect(
            diversity.get(field),
            TBD_PREREGISTRATION,
            f"diversity.{field}",
        )
    _expect(
        diversity.get("minimum_distinct_speakers_per_sentence"),
        2,
        "speaker diversity floor",
    )
    _expect(
        diversity.get("minimum_distinct_speed_buckets_per_sentence"),
        2,
        "speed diversity floor",
    )
    _expect(
        diversity.get("minimum_distinct_pitch_buckets_per_sentence"),
        2,
        "pitch diversity floor",
    )

    blind = _mapping(
        config.get("blindness_and_cherry_pick_controls"),
        "blindness_and_cherry_pick_controls",
    )
    _expect(
        tuple(_list(blind.get("selector_input_allowlist"), "selector allowlist")),
        EXPECTED_SELECTOR_ALLOWLIST,
        "selector allowlist",
    )
    _expect(
        tuple(_list(blind.get("selector_input_denylist"), "selector denylist")),
        EXPECTED_SELECTOR_DENYLIST,
        "selector denylist",
    )
    for field in (
        "source_kind_join_only_after_selection",
        "outcome_driven_reselection_allowed",
        "r04_feedback_driven_reselection_allowed",
        "posthoc_objective_or_threshold_change_allowed",
    ):
        expected = field == "source_kind_join_only_after_selection"
        _expect(blind.get(field), expected, f"blindness.{field}")
    _expect(
        tuple(
            _list(
                blind.get("per_sentence_sources_required_after_join"),
                "required sources",
            )
        ),
        SOURCE_KINDS,
        "required sources",
    )
    _expect(
        blind.get("maximum_per_sentence_source_count_difference"),
        1,
        "source balance difference",
    )

    lineage = _mapping(config.get("lineage_contract"), "lineage_contract")
    for field, expected in (
        ("exact_join_to_r02_lineage_required", True),
        ("audio_content_read_for_lineage_check_allowed", False),
        ("quarantine_rows_allowed", False),
        ("duplicate_candidate_allowed", False),
    ):
        _expect(lineage.get(field), expected, f"lineage.{field}")

    privacy = _mapping(
        config.get("privacy_and_publication"),
        "privacy_and_publication",
    )
    expected_privacy = {
        "bank_manifest_zone": "D1_pseudonymized_restricted_experiment",
        "selection_trace_zone": "D1_pseudonymized_restricted_experiment",
        "aggregate_report_zone": "A1_aggregate_only",
        "r04_raw_label_zone": "not_accessible_to_r03",
        "public_zone_allowed_before_r04": False,
        "individual_candidate_rows_allowed_in_a1": False,
        "candidate_ids_allowed_in_a1": False,
        "speaker_ids_allowed_in_a1": False,
        "audio_hashes_allowed_in_a1": False,
        "source_or_audio_paths_allowed_anywhere": False,
        "r04_completion_required_before_publication_review": True,
        "publication_requires_separate_promotion": True,
        "forbidden_recursive_fields": list(FORBIDDEN_RECURSIVE_FIELDS),
    }
    _expect(dict(privacy), expected_privacy, "privacy/publication contract")

    _expect(
        tuple(_list(config.get("execution_order"), "execution_order")),
        EXPECTED_EXECUTION_ORDER,
        "execution order",
    )
    outputs = _mapping(config.get("outputs"), "outputs")
    _expect(outputs.get("no_overwrite"), True, "no-overwrite")
    _expect(outputs.get("atomic_publish_required"), True, "atomic publish")
    _expect(
        outputs.get("write_enabled_in_this_package"),
        False,
        "static package write boundary",
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
    """Validate static pins and schemas without resolving runtime artifacts."""

    root = workspace_root.resolve(strict=True)
    access = audit if audit is not None else AccessAudit()
    _reject_nonfinite_tree(config, "config")
    _validate_config_values(config)
    absolute_config = config_path if config_path.is_absolute() else root / config_path
    try:
        config_relative = absolute_config.relative_to(root).as_posix()
    except ValueError as exc:
        raise R03ContractError("R03 config must be inside workspace") from exc
    config_data, config_snapshot = _read_file(root, config_relative, "R03 config")
    if _load_config_bytes(config_data) != config:
        raise R03ContractError("R03 config changed between load and validation")
    config_sha256 = config_snapshot.sha256

    canonical = _mapping(config.get("canonical_sources"), "canonical_sources")
    _expect(
        canonical.get("access_scope"),
        "C1_public_or_aggregate_static_pins_only",
        "canonical source scope",
    )
    pins = _list(canonical.get("pins"), "canonical source pins")
    if len(pins) != len(SOURCE_PATHS):
        raise R03ContractError("canonical source pin count drift")
    source_snapshots: list[FileSnapshot] = []
    seen: set[str] = set()
    payload_by_path: dict[str, bytes] = {}
    for index, raw_pin in enumerate(pins):
        pin = _mapping(raw_pin, f"canonical_sources.pins[{index}]")
        _expect_keys(pin, {"path", "sha256"}, f"source pin {index}")
        relative = _relative_string(pin.get("path"), f"source pin {index} path")
        digest = _sha256(pin.get("sha256"), f"source pin {index} sha256")
        if relative in seen:
            raise R03ContractError(f"duplicate canonical source pin: {relative}")
        seen.add(relative)
        data, snapshot = _read_file(root, relative, f"source pin {relative}")
        access.source_pin_read_count += 1
        if snapshot.sha256 != digest:
            raise R03ContractError(
                f"canonical source SHA-256 drift: {relative}",
                stage="source_pin",
                audit=access,
            )
        source_snapshots.append(snapshot)
        payload_by_path[relative] = data
    if tuple(sorted(seen)) != tuple(sorted(SOURCE_PATHS)):
        raise R03ContractError("canonical source path set drift")

    outputs = _mapping(config.get("outputs"), "outputs")
    writable_outputs = [
        _relative_string(value, f"outputs.{field}")
        for field, value in outputs.items()
        if field.endswith("_path") and field != "bank_schema_path"
    ]
    if len(set(writable_outputs)) != len(writable_outputs):
        raise R03ContractError("R03 output paths must be unique")
    for output in writable_outputs:
        output_parts = PurePosixPath(output).parts
        for source in seen:
            source_parts = PurePosixPath(source).parts
            if (
                output_parts == source_parts
                or output_parts[: len(source_parts)] == source_parts
                or source_parts[: len(output_parts)] == output_parts
            ):
                raise R03ContractError(
                    f"R03 output/source overlap prohibited: {output} / {source}"
                )

    bank_schema = _load_json_bytes(
        payload_by_path["configs/reference/r03_reference_bank_manifest_v1.schema.json"],
        "R03 bank schema",
    )
    prereg_template = _load_json_bytes(
        payload_by_path[
            "configs/reference/r03_pareto_diversity_preregistration_v1.template.json"
        ],
        "R03 preregistration template",
    )
    _validate_bank_schema(bank_schema)
    _validate_prereg_template(prereg_template)

    snapshots_by_path = {
        snapshot.relative_path: snapshot for snapshot in source_snapshots
    }
    r01_predecessor = _mapping(config.get("predecessor_r01"), "predecessor_r01")
    r02_predecessor = _mapping(config.get("predecessor_r02"), "predecessor_r02")
    _expect(
        r01_predecessor.get("config_sha256"),
        snapshots_by_path[
            "configs/experiments/r01_human_cosyvoice_candidates_v1.yaml"
        ].sha256,
        "R01 predecessor config/source binding",
    )
    _expect(
        r01_predecessor.get("contract_sha256"),
        snapshots_by_path[
            "src/voice_coach/references/r01_candidate_contract.py"
        ].sha256,
        "R01 predecessor contract/source binding",
    )
    _expect(
        r02_predecessor.get("config_sha256"),
        snapshots_by_path["configs/experiments/r02_candidate_hard_gate_v1.yaml"].sha256,
        "R02 predecessor config/source binding",
    )
    _expect(
        r02_predecessor.get("contract_sha256"),
        snapshots_by_path[
            "src/voice_coach/references/r02_hard_gate_contract.py"
        ].sha256,
        "R02 predecessor contract/source binding",
    )
    r00_predecessor = _mapping(config.get("predecessor_r00"), "predecessor_r00")
    _expect(
        prereg_template.get("r00_card_sha256"),
        r00_predecessor.get("card_sha256"),
        "preregistration template R00 binding",
    )
    _expect(
        prereg_template.get("r01_config_sha256"),
        r01_predecessor.get("config_sha256"),
        "preregistration template R01 binding",
    )
    _expect(
        prereg_template.get("r02_config_sha256"),
        r02_predecessor.get("config_sha256"),
        "preregistration template R02 binding",
    )
    r02_source_config = _load_config_bytes(
        payload_by_path["configs/experiments/r02_candidate_hard_gate_v1.yaml"]
    )
    r02_r01 = _mapping(
        r02_source_config.get("predecessor_r01"),
        "pinned R02 predecessor_r01",
    )
    for r03_field, r02_field in (
        ("config_path", "config_path"),
        ("config_sha256", "config_sha256"),
        ("completion_commit_path", "completion_commit_path"),
        ("completion_commit_sha256", "completion_commit_sha256"),
        ("registry_path", "registry_path"),
        ("candidate_manifest_path", "candidate_manifest_path"),
    ):
        _expect(
            r01_predecessor.get(r03_field),
            r02_r01.get(r02_field),
            f"R03/R02 R01 predecessor binding {r03_field}",
        )
    r02_outputs = _mapping(r02_source_config.get("outputs"), "pinned R02 outputs")
    for r03_field, r02_field in (
        ("completion_commit_path", "completion_commit_path"),
        ("registry_path", "registry_path"),
        ("report_path", "gate_report_path"),
        ("blinded_execution_manifest_path", "blinded_execution_manifest_path"),
        ("eligible_manifest_path", "eligible_manifest_path"),
        ("quarantine_manifest_path", "quarantine_manifest_path"),
        ("lineage_sidecar_path", "source_lineage_sidecar_path"),
    ):
        _expect(
            r02_predecessor.get(r03_field),
            r02_outputs.get(r02_field),
            f"R03/R02 output binding {r03_field}",
        )

    prereg = _mapping(config.get("preregistration"), "preregistration")
    template_pin = next(
        snapshot
        for snapshot in source_snapshots
        if snapshot.relative_path
        == "configs/reference/r03_pareto_diversity_preregistration_v1.template.json"
    )
    _expect(
        prereg.get("template_sha256"),
        template_pin.sha256,
        "preregistration template pin",
    )

    policy_payload = {
        "selection_contract": config["selection_contract"],
        "blindness_and_cherry_pick_controls": config[
            "blindness_and_cherry_pick_controls"
        ],
        "lineage_contract": config["lineage_contract"],
        "privacy_and_publication": config["privacy_and_publication"],
        "execution_order": config["execution_order"],
    }
    policy_sha256 = hashlib.sha256(canonical_json_bytes(policy_payload)).hexdigest()
    return StaticValidation(
        config_sha256=config_sha256,
        policy_sha256=policy_sha256,
        source_snapshots=tuple(source_snapshots),
    )


def _load_module(
    *,
    workspace_root: Path,
    relative: str,
    module_name: str,
) -> ModuleType:
    path = _reject_symlink_chain(
        workspace_root,
        relative,
        module_name,
        require_leaf=True,
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise R03ContractError(f"cannot load {module_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise R03ContractError(f"cannot import {module_name}: {exc}") from exc
    return module


def deep_validate_r00(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> R00Evidence:
    """Recompute R00 and require an exact immutable report match."""

    r00 = _mapping(config.get("predecessor_r00"), "predecessor_r00")
    validator_relative = _relative_string(
        r00.get("validator_path"),
        "R00 validator path",
    )
    module = _load_module(
        workspace_root=workspace_root,
        relative=validator_relative,
        module_name="_voice_coach_r03_r00_validator",
    )
    audit.r00_validator_load_count += 1
    validate_bundle = getattr(module, "validate_bundle", None)
    if not callable(validate_bundle):
        raise R03ContractError(
            "R00 validator lacks validate_bundle",
            stage="r00",
            audit=audit,
        )
    try:
        computed = validate_bundle(
            workspace_root=workspace_root,
            card_relative_path=_relative_string(
                r00.get("card_path"),
                "R00 card path",
            ),
            schema_relative_path=_relative_string(
                r00.get("schema_path"),
                "R00 schema path",
            ),
        )
    except Exception as exc:
        raise R03ContractError(
            f"R00 deep validation failed: {exc}",
            stage="r00",
            audit=audit,
        ) from exc
    audit.r00_deep_validation_count += 1
    data_access = _mapping(computed.get("data_access"), "R00 data_access")
    audit.r00_public_or_aggregate_evidence_read_count += _integer(
        data_access.get("public_or_aggregate_evidence_files_read"),
        "R00 public/aggregate reads",
        minimum=0,
    )
    audit.r00_raw_audio_read_count += _integer(
        data_access.get("raw_audio_files_read"),
        "R00 raw audio reads",
        minimum=0,
    )
    audit.r00_row_level_read_count += _integer(
        data_access.get("row_level_files_read"),
        "R00 row reads",
        minimum=0,
    )
    if audit.r00_raw_audio_read_count or audit.r00_row_level_read_count:
        raise R03ContractError(
            "R00 validation crossed raw/row boundary",
            stage="r00",
            audit=audit,
        )
    report_relative = _relative_string(
        r00.get("validation_report_path"),
        "R00 report path",
    )
    report_data, report_snapshot = _read_file(
        workspace_root,
        report_relative,
        "R00 validation report",
    )
    if report_snapshot.sha256 != _sha256(
        r00.get("validation_report_sha256"),
        "R00 report SHA",
    ):
        raise R03ContractError(
            "R00 validation report SHA drift",
            stage="r00",
            audit=audit,
        )
    stored = _load_json_bytes(report_data, "R00 validation report")
    if canonical_json_bytes(stored) != canonical_json_bytes(_mapping(computed, "R00")):
        raise R03ContractError(
            "R00 stored report does not match recomputation",
            stage="r00",
            audit=audit,
        )
    _expect(stored.get("status"), "valid", "R00 status")
    checks = _mapping(stored.get("checks"), "R00 checks")
    _expect(checks.get("sentence_count"), 20, "R00 sentence count")
    completion = _mapping(stored.get("completion"), "R00 completion")
    _expect(
        completion.get("r00_completion_claim_allowed"),
        True,
        "R00 completion claim",
    )
    return R00Evidence(
        report_sha256=report_snapshot.sha256,
        report_snapshot=report_snapshot,
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
        except R03ContractError as exc:
            raise R03ContractError(
                str(exc),
                stage=f"{stage}_toctou",
                audit=audit,
            ) from exc
        audit.source_toctou_verification_count += 1


def _snapshot_from_delegate(value: object, name: str) -> FileSnapshot:
    """Convert the pinned R02 module's immutable snapshot without reopening data."""

    try:
        return FileSnapshot(
            relative_path=_string(getattr(value, "path"), f"{name}.path"),
            sha256=_sha256(getattr(value, "sha256"), f"{name}.sha256"),
            size=_integer(
                getattr(value, "size_bytes"),
                f"{name}.size_bytes",
                minimum=0,
            ),
            device=_integer(getattr(value, "device"), f"{name}.device"),
            inode=_integer(getattr(value, "inode"), f"{name}.inode"),
            mtime_ns=_integer(
                getattr(value, "modified_ns"),
                f"{name}.modified_ns",
                minimum=0,
            ),
        )
    except AttributeError as exc:
        raise R03ContractError(f"{name} snapshot interface drift") from exc


def _r02_contract_module(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> ModuleType:
    predecessor = _mapping(config.get("predecessor_r02"), "predecessor_r02")
    relative = _relative_string(predecessor.get("contract_path"), "R02 contract")
    module = _load_module(
        workspace_root=workspace_root,
        relative=relative,
        module_name="_voice_coach_r03_r02_contract",
    )
    audit.r02_contract_load_count += 1
    return module


def deep_validate_r01_completion(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    r02_module: ModuleType,
    audit: AccessAudit,
) -> R01Evidence:
    """Delegate R01 artifact validation to R02's pinned predecessor validator."""

    predecessor = _mapping(config.get("predecessor_r01"), "predecessor_r01")
    expected_commit_sha = _string(
        predecessor.get("completion_commit_sha256"),
        "R01 completion commit SHA",
    )
    if expected_commit_sha.startswith("TBD_"):
        raise R03ContractError(
            "R01 completion commit is TBD_R01_COMPLETION",
            stage="r01_completion",
            audit=audit,
        )
    _sha256(expected_commit_sha, "R01 completion commit SHA")
    audit.r01_completion_path_resolution_count += 1
    audit.r01_completion_read_attempt_count += 1
    commit_relative = _relative_string(
        predecessor.get("completion_commit_path"),
        "R01 completion commit",
    )
    try:
        commit_data, commit_snapshot = _read_file(
            workspace_root,
            commit_relative,
            "R01 completion commit",
        )
    except R03ContractError as exc:
        raise R03ContractError(
            "R01 completion commit is absent; R01 must complete before R02/R03",
            stage="r01_completion",
            audit=audit,
        ) from exc
    audit.r01_completion_read_count += 1
    if commit_snapshot.sha256 != expected_commit_sha:
        raise R03ContractError(
            "R01 completion commit SHA drift",
            stage="r01_completion",
            audit=audit,
        )
    commit = _load_json_bytes(commit_data, "R01 completion commit")
    _expect(
        commit.get("schema_version"),
        predecessor.get("required_commit_schema_version"),
        "R01 commit schema",
    )
    _expect(commit.get("status"), "completed", "R01 commit status")
    _expect(commit.get("sentence_count"), 20, "R01 sentence count")
    candidate_count = _integer(
        commit.get("candidate_count"),
        "R01 candidate count",
    )
    if not 160 <= candidate_count <= 320:
        raise R03ContractError(
            "R01 candidate count must be 160-320",
            stage="r01_completion",
            audit=audit,
        )
    _expect(
        commit.get("r02_hard_gate_status"),
        "not_run",
        "R01 R02 status",
    )
    _expect(commit.get("publication_allowed"), False, "R01 publication")

    validator = getattr(r02_module, "deep_validate_r01_completion", None)
    if not callable(validator):
        raise R03ContractError(
            "pinned R02 contract lacks deep_validate_r01_completion",
            stage="r01_completion",
            audit=audit,
        )
    r02_predecessor = _mapping(config.get("predecessor_r02"), "predecessor_r02")
    r02_config_relative = _relative_string(
        r02_predecessor.get("config_path"),
        "R02 config path",
    )
    r02_config_path = workspace_root / r02_config_relative
    r02_config = r02_module.load_config(r02_config_path)
    r02_delegate_audit = r02_module.AccessAudit()
    try:
        r02_static = r02_module.validate_static_config(
            workspace_root=workspace_root,
            config_path=r02_config_path,
            config=r02_config,
            audit=r02_delegate_audit,
        )
        r02_r00 = r02_module.deep_validate_r00(
            workspace_root=workspace_root,
            config=r02_config,
            static=r02_static,
            audit=r02_delegate_audit,
        )
        result = validator(
            workspace_root=workspace_root,
            config=r02_config,
            r00=r02_r00,
            audit=r02_delegate_audit,
        )
    except TypeError as exc:
        raise R03ContractError(
            f"R02 R01-validator signature drift: {exc}",
            stage="r01_completion",
            audit=audit,
        ) from exc
    except Exception as exc:
        raise R03ContractError(
            f"R01 deep validation failed: {exc}",
            stage="r01_completion",
            audit=audit,
        ) from exc
    try:
        delegated_snapshots = tuple(
            _snapshot_from_delegate(value, f"R01 delegated snapshot {index}")
            for index, value in enumerate(
                (
                    result.commit_snapshot,
                    result.registry_snapshot,
                    result.manifest_snapshot,
                    result.human_attestation_snapshot,
                    result.cosyvoice_attestation_snapshot,
                )
            )
        )
    except AttributeError as exc:
        raise R03ContractError(
            "R02 R01-evidence interface drift",
            stage="r01_completion",
            audit=audit,
        ) from exc
    if delegated_snapshots[0] != commit_snapshot:
        raise R03ContractError(
            "R01 delegated commit snapshot mismatch",
            stage="r01_completion",
            audit=audit,
        )
    audit.r01_registry_read_count += _integer(
        getattr(r02_delegate_audit, "r01_registry_read_count"),
        "R01 registry read count",
        minimum=1,
    )
    audit.r01_manifest_read_count += _integer(
        getattr(r02_delegate_audit, "r01_candidate_manifest_read_count"),
        "R01 manifest read count",
        minimum=1,
    )
    # The R02 predecessor validator validates metadata and SHA lineage only.
    # Any candidate-audio read would violate the R03 boundary.
    delegate_audio_reads = _integer(
        getattr(r02_delegate_audit, "d1_audio_read_count"),
        "R01 delegated audio read count",
        minimum=0,
    )
    audit.r01_audio_read_count += delegate_audio_reads
    if delegate_audio_reads:
        raise R03ContractError(
            "R01 deep validator opened candidate audio",
            stage="r01_completion",
            audit=audit,
        )
    return R01Evidence(
        commit_sha256=commit_snapshot.sha256,
        snapshots=delegated_snapshots,
        r02_config=_mapping(r02_config, "R02 delegated config"),
        r02_config_sha256=_sha256(
            getattr(r02_static, "config_sha256"),
            "R02 delegated config SHA",
        ),
        delegated_r01_evidence=result,
        delegated_audit=r02_delegate_audit,
    )


def deep_validate_r02_completion(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    r02_module: ModuleType,
    r01: R01Evidence,
    audit: AccessAudit,
) -> R02Evidence:
    """Deep-validate R02; only its eligible partition may reach selection."""

    predecessor = _mapping(config.get("predecessor_r02"), "predecessor_r02")
    expected_commit_sha = _string(
        predecessor.get("completion_commit_sha256"),
        "R02 completion commit SHA",
    )
    if expected_commit_sha.startswith("TBD_"):
        raise R03ContractError(
            "R02 completion commit SHA is unresolved",
            stage="r02_completion",
            audit=audit,
        )
    _sha256(expected_commit_sha, "R02 completion commit SHA")
    audit.r02_completion_path_resolution_count += 1
    audit.r02_completion_read_attempt_count += 1
    commit_relative = _relative_string(
        predecessor.get("completion_commit_path"),
        "R02 completion commit",
    )
    try:
        commit_data, commit_snapshot = _read_file(
            workspace_root,
            commit_relative,
            "R02 completion commit",
        )
    except R03ContractError as exc:
        raise R03ContractError(
            "R02 completion commit is absent; hard gate must complete before R03",
            stage="r02_completion",
            audit=audit,
        ) from exc
    audit.r02_completion_read_count += 1
    if commit_snapshot.sha256 != expected_commit_sha:
        raise R03ContractError(
            "R02 completion commit SHA drift",
            stage="r02_completion",
            audit=audit,
        )
    commit = _load_json_bytes(commit_data, "R02 completion commit")
    _expect(
        commit.get("schema_version"),
        predecessor.get("required_commit_schema_version"),
        "R02 commit schema",
    )
    _expect(commit.get("status"), "completed", "R02 commit status")
    candidate_count = _integer(
        commit.get("candidate_count"),
        "R02 candidate count",
        minimum=160,
    )
    if candidate_count > 320:
        raise R03ContractError(
            "R02 candidate count must be 160-320",
            stage="r02_completion",
            audit=audit,
        )
    _expect(
        commit.get("failed_candidates_preserved"),
        True,
        "R02 failed-candidate preservation",
    )
    _expect(commit.get("r03_input_pass_only"), True, "R02 pass-only boundary")
    _expect(commit.get("publication_allowed"), False, "R02 publication")

    validator = getattr(r02_module, "deep_validate_r02_completion", None)
    if not callable(validator):
        raise R03ContractError(
            "pinned R02 contract lacks deep_validate_r02_completion",
            stage="r02_completion",
            audit=audit,
        )
    try:
        result = validator(
            workspace_root=workspace_root,
            r02_config=r01.r02_config,
            r02_config_sha256=r01.r02_config_sha256,
            completion_commit_path=commit_relative,
            completion_commit_sha256=expected_commit_sha,
            r01=r01.delegated_r01_evidence,
            audit=r01.delegated_audit,
        )
    except TypeError as exc:
        raise R03ContractError(
            f"R02 completion-validator signature drift: {exc}",
            stage="r02_completion",
            audit=audit,
        ) from exc
    except Exception as exc:
        raise R03ContractError(
            f"R02 deep validation failed: {exc}",
            stage="r02_completion",
            audit=audit,
        ) from exc
    try:
        delegated_snapshots = tuple(
            _snapshot_from_delegate(value, f"R02 delegated snapshot {index}")
            for index, value in enumerate(
                (
                    result.commit_snapshot,
                    result.registry_snapshot,
                    result.report_snapshot,
                    result.blinded_execution_manifest_snapshot,
                    result.source_lineage_sidecar_snapshot,
                    result.eligible_manifest_snapshot,
                    result.quarantine_manifest_snapshot,
                )
            )
        )
        eligible_ids = tuple(result.eligible_candidate_ids)
        quarantined_ids = tuple(result.quarantined_candidate_ids)
    except AttributeError as exc:
        raise R03ContractError(
            "R02 completion-evidence interface drift",
            stage="r02_completion",
            audit=audit,
        ) from exc
    if delegated_snapshots[0] != commit_snapshot:
        raise R03ContractError(
            "R02 delegated commit snapshot mismatch",
            stage="r02_completion",
            audit=audit,
        )
    if set(eligible_ids).intersection(quarantined_ids):
        raise R03ContractError(
            "R02 eligible/quarantine partitions overlap",
            stage="r02_completion",
            audit=audit,
        )
    if len(eligible_ids) + len(quarantined_ids) != candidate_count:
        raise R03ContractError(
            "R02 partition count does not match candidate count",
            stage="r02_completion",
            audit=audit,
        )
    audit.r02_registry_read_count += 1
    audit.r02_report_read_count += 1
    audit.r02_lineage_read_count += 1
    audit.r02_quarantine_partition_validation_count += 1
    report_sha256 = delegated_snapshots[2].sha256
    return R02Evidence(
        commit_sha256=commit_snapshot.sha256,
        report_sha256=report_sha256,
        snapshots=delegated_snapshots,
    )


def validate_approved_preregistration(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> tuple[str, FileSnapshot]:
    prereg = _mapping(config.get("preregistration"), "preregistration")
    expected_sha = _string(
        prereg.get("approved_artifact_sha256"),
        "approved preregistration SHA",
    )
    if expected_sha == TBD_PREREGISTRATION:
        raise R03ContractError(
            "R03 Pareto/diversity choices are TBD_PREREGISTRATION",
            stage="preregistration",
            audit=audit,
        )
    _sha256(expected_sha, "approved preregistration SHA")
    audit.preregistration_path_resolution_count += 1
    audit.preregistration_read_attempt_count += 1
    relative = _relative_string(
        prereg.get("approved_artifact_path"),
        "approved preregistration path",
    )
    try:
        data, snapshot = _read_file(
            workspace_root,
            relative,
            "approved R03 preregistration",
        )
    except R03ContractError as exc:
        raise R03ContractError(
            f"approved R03 preregistration unavailable: {exc}",
            stage="preregistration",
            audit=audit,
        ) from exc
    audit.preregistration_read_count += 1
    if snapshot.sha256 != expected_sha:
        raise R03ContractError(
            "approved R03 preregistration SHA drift",
            stage="preregistration",
            audit=audit,
        )
    payload = _load_json_bytes(data, "approved R03 preregistration")
    _expect_keys(payload, PREREG_TOP_LEVEL_KEYS, "approved preregistration")
    for section_name, expected_keys in PREREG_SECTION_KEYS.items():
        _expect_keys(
            _mapping(payload.get(section_name), section_name),
            expected_keys,
            f"approved preregistration {section_name}",
        )
    _expect(
        payload.get("schema_version"),
        prereg.get("required_schema_version"),
        "approved preregistration schema",
    )
    _expect(payload.get("status"), "approved_and_frozen", "preregistration status")
    _expect(
        payload.get("frozen_before_r02_eligible_data_access"),
        True,
        "preregistration freeze ordering",
    )
    if _contains_tbd(payload):
        raise R03ContractError(
            "approved preregistration contains unresolved TBD",
            stage="preregistration",
            audit=audit,
        )
    r00 = _mapping(config.get("predecessor_r00"), "predecessor_r00")
    r01 = _mapping(config.get("predecessor_r01"), "predecessor_r01")
    r02 = _mapping(config.get("predecessor_r02"), "predecessor_r02")
    _expect(
        payload.get("r00_card_sha256"),
        r00.get("card_sha256"),
        "preregistration R00 binding",
    )
    _expect(
        payload.get("r01_config_sha256"),
        r01.get("config_sha256"),
        "preregistration R01 binding",
    )
    _expect(
        payload.get("r02_config_sha256"),
        r02.get("config_sha256"),
        "preregistration R02 binding",
    )
    objectives = _mapping(
        payload.get("objective_contract"),
        "approved objective contract",
    )
    _expect(
        tuple(_list(objectives.get("objective_ids"), "approved objective IDs")),
        ("naturalness", "prosody_fit", "pedagogical_clarity", "diversity"),
        "approved objective IDs",
    )
    blindness = _mapping(payload.get("blindness_contract"), "blindness contract")
    for field, expected in (
        ("selector_source_kind_blind", True),
        ("selector_source_attestation_blind", True),
        ("selector_r04_label_blind", True),
        ("selector_mos_cmos_blind", True),
        ("selector_learning_outcome_blind", True),
        ("source_join_only_after_selection", True),
        ("outcome_driven_reselection_allowed", False),
    ):
        _expect(blindness.get(field), expected, f"preregistration.{field}")
    approval = _mapping(payload.get("approval"), "preregistration approval")
    _expect(approval.get("decision"), "approved", "preregistration decision")
    _sha256(approval.get("protocol_sha256"), "preregistration protocol SHA")
    _sha256(approval.get("signed_receipt_sha256"), "preregistration receipt SHA")
    return snapshot.sha256, snapshot


def ensure_output_targets_absent(
    *,
    workspace_root: Path,
    outputs: Mapping[str, Any],
) -> None:
    """Reject symlinks and every pre-existing R03 output target."""

    for field, raw in outputs.items():
        if not field.endswith("_path") or field == "bank_schema_path":
            continue
        relative = _relative_string(raw, f"outputs.{field}")
        path = _reject_symlink_chain(
            workspace_root,
            relative,
            f"outputs.{field}",
            require_leaf=False,
        )
        if path.exists():
            raise R03ContractError(f"output target already exists: {relative}")


def validate_bank_manifest_payload(
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Validate a future D1 bank without opening any audio or R04 evidence."""

    _reject_nonfinite_tree(payload, "R03 bank")
    forbidden_seen = _collect_keys(payload).intersection(FORBIDDEN_RECURSIVE_FIELDS)
    if forbidden_seen:
        raise R03ContractError(
            f"R03 bank contains forbidden fields: {sorted(forbidden_seen)}"
        )
    _expect_keys(payload, BANK_TOP_LEVEL_KEYS, "R03 bank")
    _expect(
        payload.get("schema_version"),
        "voice_coach.r03_reference_bank_manifest.v1",
        "bank schema version",
    )
    bank_id = _string(payload.get("bank_id"), "bank_id")
    if not re.fullmatch(r"r03-private-reference-bank-[0-9a-f]{32}", bank_id):
        raise R03ContractError("bank_id format invalid")
    _expect(payload.get("status"), "r03_selected_pending_r04", "bank status")
    for field in (
        "r00_card_sha256",
        "r01_commit_sha256",
        "r02_commit_sha256",
        "r03_config_sha256",
        "r03_preregistration_sha256",
    ):
        _sha256(payload.get(field), field)
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_experiment",
        "bank privacy",
    )
    _expect(payload.get("publication_allowed"), False, "bank publication")
    _expect(
        payload.get("r04_blind_evaluation_completed"),
        False,
        "R04 completion",
    )
    _expect(
        payload.get("selection_blinded_to_source_kind"),
        True,
        "source blindness",
    )
    _expect(
        payload.get("selection_blinded_to_r04_and_outcomes"),
        True,
        "outcome blindness",
    )

    sentence_rows = _list(payload.get("sentences"), "bank sentences")
    if len(sentence_rows) != 20:
        raise R03ContractError("bank must contain exactly 20 sentences")
    seen_sentences: set[str] = set()
    seen_candidates: set[str] = set()
    total = 0
    count_histogram: Counter[int] = Counter()
    source_counts: Counter[str] = Counter()
    speaker_minimum = 1_000_000
    speed_minimum = 1_000_000
    pitch_minimum = 1_000_000
    for index, raw_sentence in enumerate(sentence_rows):
        sentence = _mapping(raw_sentence, f"sentences[{index}]")
        _expect_keys(sentence, BANK_SENTENCE_KEYS, f"sentences[{index}]")
        sentence_id = _string(sentence.get("sentence_id"), "sentence_id")
        if sentence_id not in SENTENCE_IDS or sentence_id in seen_sentences:
            raise R03ContractError(f"invalid/duplicate sentence_id: {sentence_id}")
        seen_sentences.add(sentence_id)
        eligible_count = _integer(
            sentence.get("eligible_candidate_count"),
            f"{sentence_id}.eligible_candidate_count",
            minimum=3,
        )
        if eligible_count > 16:
            raise R03ContractError(f"{sentence_id} eligible count exceeds 16")
        selected_count = _integer(
            sentence.get("selected_count"),
            f"{sentence_id}.selected_count",
        )
        if not 3 <= selected_count <= 5:
            raise R03ContractError(f"{sentence_id} must select 3-5 candidates")
        if selected_count > eligible_count:
            raise R03ContractError(f"{sentence_id} selected exceeds eligible")
        for field in (
            "source_balance_verified_after_blind_selection",
            "speaker_diversity_verified",
            "speed_diversity_verified",
            "pitch_diversity_verified",
        ):
            _expect(sentence.get(field), True, f"{sentence_id}.{field}")
        candidates = _list(
            sentence.get("selected_candidates"),
            f"{sentence_id}.selected_candidates",
        )
        if len(candidates) != selected_count:
            raise R03ContractError(f"{sentence_id} selected_count mismatch")
        local_sources: Counter[str] = Counter()
        local_speakers: set[str] = set()
        local_speeds: set[str] = set()
        local_pitches: set[str] = set()
        for row_index, raw_candidate in enumerate(candidates):
            candidate = _mapping(
                raw_candidate,
                f"{sentence_id}.selected_candidates[{row_index}]",
            )
            _expect_keys(
                candidate,
                BANK_CANDIDATE_KEYS,
                f"{sentence_id} candidate",
            )
            candidate_id = _string(candidate.get("candidate_id"), "candidate_id")
            if (
                not CANDIDATE_ID_RE.fullmatch(candidate_id)
                or candidate_id in seen_candidates
            ):
                raise R03ContractError(
                    f"invalid/duplicate candidate_id: {candidate_id}"
                )
            seen_candidates.add(candidate_id)
            for field in (
                "target_text_sha256",
                "audio_object_id_sha256",
                "audio_sha256",
                "source_attestation_sha256",
                "speaker_id_sha256",
                "r02_eligible_row_sha256",
                "objective_vector_sha256",
            ):
                _sha256(candidate.get(field), f"{candidate_id}.{field}")
            source = _string(candidate.get("source_kind"), "source_kind")
            if source not in SOURCE_KINDS:
                raise R03ContractError(f"{candidate_id} source invalid")
            speed = _string(candidate.get("speed_bucket"), "speed_bucket")
            pitch = _string(candidate.get("pitch_bucket"), "pitch_bucket")
            if speed not in SPEED_BUCKETS or pitch not in PITCH_BUCKETS:
                raise R03ContractError(f"{candidate_id} bucket invalid")
            _expect(candidate.get("pareto_member"), True, "pareto membership")
            _expect(
                candidate.get("immutable_lineage_verified"),
                True,
                "immutable lineage",
            )
            local_sources[source] += 1
            source_counts[source] += 1
            local_speakers.add(str(candidate["speaker_id_sha256"]))
            local_speeds.add(speed)
            local_pitches.add(pitch)
        if set(local_sources) != set(SOURCE_KINDS):
            raise R03ContractError(f"{sentence_id} must preserve both sources")
        if abs(local_sources[SOURCE_KINDS[0]] - local_sources[SOURCE_KINDS[1]]) > 1:
            raise R03ContractError(f"{sentence_id} source imbalance exceeds one")
        if len(local_speakers) < 2:
            raise R03ContractError(f"{sentence_id} speaker diversity below two")
        if len(local_speeds) < 2:
            raise R03ContractError(f"{sentence_id} speed diversity below two")
        if len(local_pitches) < 2:
            raise R03ContractError(f"{sentence_id} pitch diversity below two")
        speaker_minimum = min(speaker_minimum, len(local_speakers))
        speed_minimum = min(speed_minimum, len(local_speeds))
        pitch_minimum = min(pitch_minimum, len(local_pitches))
        total += selected_count
        count_histogram[selected_count] += 1
    if seen_sentences != set(SENTENCE_IDS):
        raise R03ContractError("bank sentence coverage drift")
    if not 60 <= total <= 100:
        raise R03ContractError("bank total must be 60-100")
    return {
        "status": "valid_private_bank_pending_r04",
        "sentence_count": 20,
        "selected_candidate_count": total,
        "selected_count_histogram": {
            str(key): value for key, value in sorted(count_histogram.items())
        },
        "source_kind_counts": dict(sorted(source_counts.items())),
        "minimum_distinct_speakers_per_sentence": speaker_minimum,
        "minimum_distinct_speed_buckets_per_sentence": speed_minimum,
        "minimum_distinct_pitch_buckets_per_sentence": pitch_minimum,
        "publication_allowed": False,
    }


def validate_bank_lineage_bindings(
    payload: Mapping[str, Any],
    *,
    r01_candidate_rows: Sequence[Mapping[str, Any]],
    r02_gate_report: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Bind every selected row to R01 immutable lineage and an R02 eligible row."""

    summary = validate_bank_manifest_payload(payload)
    r01_by_id: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(r01_candidate_rows):
        candidate_id = _string(row.get("candidate_id"), f"R01 row {index} ID")
        if candidate_id in r01_by_id:
            raise R03ContractError(f"duplicate R01 candidate ID: {candidate_id}")
        r01_by_id[candidate_id] = row
    eligible_ids = {
        _string(value, "R02 eligible candidate ID")
        for value in _list(
            r02_gate_report.get("eligible_candidate_ids"),
            "R02 eligible candidate IDs",
        )
    }
    result_by_id: dict[str, Mapping[str, Any]] = {}
    for index, raw_result in enumerate(
        _list(r02_gate_report.get("results"), "R02 results")
    ):
        result = _mapping(raw_result, f"R02 result {index}")
        candidate_id = _string(result.get("candidate_id"), "R02 result ID")
        if candidate_id in result_by_id:
            raise R03ContractError(f"duplicate R02 result ID: {candidate_id}")
        result_by_id[candidate_id] = result
    if not eligible_ids.issubset(result_by_id):
        raise R03ContractError("R02 eligible partition contains unknown result IDs")

    immutable_fields = (
        "target_text_sha256",
        "audio_object_id_sha256",
        "audio_sha256",
        "source_kind",
        "source_attestation_sha256",
        "speaker_id_sha256",
        "speed_bucket",
        "pitch_bucket",
    )
    selected_count = 0
    for raw_sentence in _list(payload.get("sentences"), "bank sentences"):
        sentence = _mapping(raw_sentence, "bank sentence")
        sentence_id = _string(sentence.get("sentence_id"), "bank sentence ID")
        for raw_candidate in _list(
            sentence.get("selected_candidates"),
            "selected candidates",
        ):
            selected = _mapping(raw_candidate, "selected candidate")
            candidate_id = _string(selected.get("candidate_id"), "candidate ID")
            if candidate_id not in eligible_ids:
                raise R03ContractError(
                    f"selected candidate is not R02-eligible: {candidate_id}"
                )
            if candidate_id not in r01_by_id:
                raise R03ContractError(
                    f"selected candidate lacks R01 lineage: {candidate_id}"
                )
            source = r01_by_id[candidate_id]
            _expect(
                source.get("sentence_id"),
                sentence_id,
                f"{candidate_id} sentence lineage",
            )
            for field in immutable_fields:
                _expect(
                    selected.get(field),
                    source.get(field),
                    f"{candidate_id}.{field} lineage",
                )
            result = result_by_id[candidate_id]
            _expect(
                result.get("sentence_id"),
                sentence_id,
                f"{candidate_id} R02 sentence",
            )
            _expect(
                result.get("overall_decision"),
                "eligible",
                f"{candidate_id} R02 decision",
            )
            result_sha256 = hashlib.sha256(canonical_json_bytes(result)).hexdigest()
            _expect(
                selected.get("r02_eligible_row_sha256"),
                result_sha256,
                f"{candidate_id} R02 row binding",
            )
            selected_count += 1
    _expect(
        selected_count,
        summary.get("selected_candidate_count"),
        "lineage selected count",
    )
    return {
        **summary,
        "immutable_lineage_binding_count": selected_count,
        "r02_eligible_only": True,
    }


def validate_a1_aggregate_payload(
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Reject any row-level identifier from a future A1 aggregate."""

    _reject_nonfinite_tree(payload, "R03 A1 aggregate")
    _expect_keys(payload, AGGREGATE_KEYS, "R03 A1 aggregate")
    forbidden = _collect_keys(payload).intersection(
        set(FORBIDDEN_RECURSIVE_FIELDS)
        | {
            "candidate_id",
            "candidate_ids",
            "speaker_id_sha256",
            "audio_sha256",
            "audio_object_id_sha256",
        }
    )
    if forbidden:
        raise R03ContractError(
            f"A1 aggregate contains row-level/forbidden fields: {sorted(forbidden)}"
        )
    _expect(
        payload.get("schema_version"),
        "voice_coach.r03_reference_bank_aggregate.v1",
        "aggregate schema",
    )
    _expect(payload.get("status"), "aggregate_pending_r04", "aggregate status")
    _expect(payload.get("privacy_zone"), "A1_aggregate_only", "aggregate privacy")
    _sha256(payload.get("r03_commit_sha256"), "aggregate commit SHA")
    _expect(payload.get("sentence_count"), 20, "aggregate sentence count")
    selected = _integer(
        payload.get("selected_candidate_count"),
        "aggregate selected count",
    )
    if not 60 <= selected <= 100:
        raise R03ContractError("aggregate selected count must be 60-100")
    histogram = _mapping(
        payload.get("selected_count_histogram"),
        "selected count histogram",
    )
    if set(histogram) != {"3", "4", "5"}:
        raise R03ContractError("aggregate histogram must contain 3/4/5")
    histogram_counts = {
        int(key): _integer(value, f"histogram.{key}", minimum=0)
        for key, value in histogram.items()
    }
    if sum(histogram_counts.values()) != 20:
        raise R03ContractError("aggregate histogram sentence total must be 20")
    if sum(key * value for key, value in histogram_counts.items()) != selected:
        raise R03ContractError("aggregate histogram selected total mismatch")
    source_counts_raw = _mapping(
        payload.get("source_kind_counts"),
        "aggregate source counts",
    )
    if set(source_counts_raw) != set(SOURCE_KINDS):
        raise R03ContractError("aggregate source counts must contain both sources")
    source_counts = {
        key: _integer(value, f"source_kind_counts.{key}", minimum=1)
        for key, value in source_counts_raw.items()
    }
    if sum(source_counts.values()) != selected:
        raise R03ContractError("aggregate source count total mismatch")
    for field in (
        "minimum_distinct_speakers_per_sentence",
        "minimum_distinct_speed_buckets_per_sentence",
        "minimum_distinct_pitch_buckets_per_sentence",
    ):
        _integer(payload.get(field), f"aggregate.{field}", minimum=2)
    for field in (
        "individual_candidate_rows_present",
        "candidate_ids_present",
        "speaker_ids_present",
        "audio_hashes_present",
        "r04_blind_evaluation_completed",
        "publication_allowed",
    ):
        _expect(payload.get(field), False, f"aggregate.{field}")
    return {
        "status": "valid_a1_aggregate_pending_r04",
        "sentence_count": 20,
        "selected_candidate_count": selected,
        "publication_allowed": False,
    }


def run_full_preflight(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> PreflightResult:
    """Run every read-only gate in the immutable order."""

    audit = AccessAudit()
    static = validate_static_config(
        workspace_root=workspace_root,
        config_path=config_path,
        config=config,
        audit=audit,
    )
    r00 = deep_validate_r00(
        workspace_root=workspace_root,
        config=config,
        audit=audit,
    )
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=static.source_snapshots,
        audit=audit,
        stage="static_source",
    )
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=(r00.report_snapshot,),
        audit=audit,
        stage="r00_report",
    )

    r02_module = _r02_contract_module(
        workspace_root=workspace_root,
        config=config,
        audit=audit,
    )
    r01 = deep_validate_r01_completion(
        workspace_root=workspace_root,
        config=config,
        r02_module=r02_module,
        audit=audit,
    )
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=r01.snapshots,
        audit=audit,
        stage="r01_completion",
    )
    r02 = deep_validate_r02_completion(
        workspace_root=workspace_root,
        config=config,
        r02_module=r02_module,
        r01=r01,
        audit=audit,
    )
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=r02.snapshots,
        audit=audit,
        stage="r02_completion",
    )
    prereg_sha256, prereg_snapshot = validate_approved_preregistration(
        workspace_root=workspace_root,
        config=config,
        audit=audit,
    )
    _verify_snapshots(
        workspace_root=workspace_root,
        snapshots=(prereg_snapshot,),
        audit=audit,
        stage="preregistration",
    )

    # Only this line crosses the eligible-data path-resolution boundary.
    audit.eligible_candidate_data_path_resolution_count += 1
    # The static package has no selection executor and never opens that data.
    outputs = _mapping(config.get("outputs"), "outputs")
    audit.output_path_resolution_count += sum(
        1
        for field in outputs
        if field.endswith("_path") and field != "bank_schema_path"
    )
    ensure_output_targets_absent(
        workspace_root=workspace_root,
        outputs=outputs,
    )
    raise R03ContractError(
        "all gates validated; a separate registered selection executor is required",
        stage="separate_selection_executor",
        audit=audit,
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
        "actual_audio_or_model_or_gpu_execution": False,
        "actual_metric_or_selection_execution": False,
        "bank_manifest_created": False,
        "r03_completion_claim_allowed": False,
        "publication_allowed": False,
    }


def failure_result(error: R03ContractError) -> Mapping[str, Any]:
    audit = error.audit if error.audit is not None else AccessAudit()
    return {
        "status": "failed_closed",
        "stage": error.stage,
        "error": str(error),
        **audit.as_dict(),
        "actual_audio_or_model_or_gpu_execution": False,
        "actual_metric_or_selection_execution": False,
        "bank_manifest_created": False,
        "r03_completion_claim_allowed": False,
        "publication_allowed": False,
    }
