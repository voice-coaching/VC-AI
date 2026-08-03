"""Aggregate-only, no-locked state machine for WP4 S108.

This module intentionally does not implement a locked-test runner.  It reads
only pinned source files, predecessor commit/registry/aggregate-report
metadata, and explicitly aggregate external attestations.  In particular, it
has no configured locked manifest locator and no API that accepts a locked
row, audio, label, or prediction path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any, cast
from uuid import uuid4

import yaml  # type: ignore[import-untyped]


CHECKLIST_SCHEMA_VERSION = "voice_coach.wavlm_s108_no_locked_checklist.v1"
RECEIPT_SCHEMA_VERSION = "voice_coach.wavlm_s108_no_locked_dry_run_receipt.v1"
HISTORICAL_CONFIG_SHA256S = frozenset(
    {
        "b59ddeb1354d8c72875269ce433830eb0482af9714a84f9ab104741331826d15",
        "9bd1c1b604b7081743934be290fc53f459bb391228ed194099309ae7c1c677f6",
        "0d6886715005ca7ece84e4b15ae46aead4aa6f1ad9510dd0298d48bc6c6eff4d",
        "4b5159cacbf7a1571f93f52c27b0bf8e1667eaf007cc4eef1ad320662d13c9e8",
    }
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
STAGES = ("s103", "s104", "s105", "s106", "s107")
ALLOWED_STATES = (
    "waiting_s103",
    "engineering_in_progress",
    "blocked_preregistration",
    "blocked_g04",
    "blocked_d07",
    "ready_for_locked_batch",
)
TERMINAL_STATES_FORBIDDEN = (
    "locked_batch_running",
    "research_candidate_failed",
    "research_candidate_passed",
    "deployment_pending",
    "deployment_approved",
    "deployment_rejected",
)
LOCKED_ZERO_COUNTS: Mapping[str, int] = {
    "locked_locator_resolution_count": 0,
    "locked_row_read_count": 0,
    "locked_audio_read_count": 0,
    "locked_label_read_count": 0,
    "locked_prediction_read_count": 0,
    "locked_access_count": 0,
}
STAGE_LAYOUT: Mapping[str, Mapping[str, Any]] = {
    "s104": {
        "artifact_count": 16,
        "report_name": "experiment_report",
        "report_schema": "voice_coach.wavlm_s104_adapter_report.v1",
        "report_status": "completed",
        "compact_json_trailing_newline": True,
    },
    "s105": {
        "artifact_count": 82,
        "report_name": "ablation_comparison_report",
        "report_schema": "voice_coach.wavlm_s105_hierarchy_comparison.v1",
        "report_status": "completed",
        "compact_json_trailing_newline": True,
    },
    "s106": {
        "artifact_count": 7,
        "report_name": "stack_only_report",
        "report_schema": "voice_coach.wavlm_s106_stack_report.v1",
        "report_status": "completed",
        "compact_json_trailing_newline": True,
    },
    "s107": {
        "artifact_count": 7,
        "report_name": "calibration_only_report",
        "report_schema": "voice_coach.wavlm_s107_calibration_report.v1",
        "report_status": "completed_provisional_engineering",
        "compact_json_trailing_newline": False,
    },
}


class S108DryRunError(ValueError):
    """Raised before any protected or locked-test resource can be accessed."""


class S108PublicationError(S108DryRunError):
    """Raised when append-only publication cannot be proven atomic."""


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: yaml.SafeLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise S108DryRunError(f"YAML contains duplicate key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True, slots=True)
class AggregateAccess:
    category: str
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class StageEvidence:
    stage: str
    status: str
    reason: str | None
    commit_sha256: str | None
    generation_id: str | None
    registry_sha256: tuple[str, ...]
    aggregate_report_sha256: str | None
    artifact_hashes: Mapping[str, str]
    validation_depth: str


@dataclass(frozen=True, slots=True)
class PublishedDryRun:
    generation_id: str
    generation_path: Path
    receipt_path: Path
    checklist_sha256: str
    checklist: Mapping[str, Any]
    created_generation: bool
    created_receipt: bool


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise S108DryRunError(f"{name} must be a mapping")
    return cast(Mapping[str, Any], value)


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise S108DryRunError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise S108DryRunError(f"{name} must be a non-empty string")
    return value


def _integer(value: object, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise S108DryRunError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise S108DryRunError(f"{name} must be at least {minimum}")
    return value


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise S108DryRunError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise S108DryRunError(f"{name} must be finite")
    return result


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise S108DryRunError(f"{name} must be boolean")
    return value


def _sha256(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or not SHA256_PATTERN.fullmatch(value)
        or value == "0" * 64
    ):
        raise S108DryRunError(f"{name} must be a nonzero lowercase SHA-256")
    return value


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise S108DryRunError(f"{name} must equal {expected!r}")


def _reject_nonfinite_tree(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise S108DryRunError(f"{name} contains a non-finite number")
    if isinstance(value, Mapping):
        for nested in value.values():
            _reject_nonfinite_tree(nested, name)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_nonfinite_tree(nested, name)


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise S108DryRunError(f"JSON contains duplicate key: {key!r}")
        result[key] = value
    return result


def canonical_json_bytes(value: Mapping[str, Any], *, pretty: bool) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    ).encode("utf-8") + (b"\n" if pretty else b"")


def _parse_json(data: bytes, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_json_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                S108DryRunError(f"{name} contains {token}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise S108DryRunError(f"{name} is not strict UTF-8 JSON") from exc
    result = _mapping(value, name)
    _reject_nonfinite_tree(result, name)
    return result


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _relative_string(value: object, name: str) -> str:
    relative = _string(value, name)
    path = Path(relative)
    if (
        path.is_absolute()
        or relative != path.as_posix()
        or not path.parts
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise S108DryRunError(f"{name} must be a normalized workspace-relative path")
    return relative


def _workspace_relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise S108DryRunError("path escaped the workspace") from exc


def _reject_symlink_chain(root: Path, relative: str, name: str) -> Path:
    relative = _relative_string(relative, name)
    current = root
    for part in Path(relative).parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise S108DryRunError(f"{name} cannot traverse a symlink")
    return root / relative


def _regular_file_bytes(path: Path, name: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise S108DryRunError(f"{name} is not a readable non-symlink file") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise S108DryRunError(f"{name} must be a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise S108DryRunError(f"{name} changed while it was read")
    try:
        current = path.lstat()
    except FileNotFoundError as exc:
        raise S108DryRunError(f"{name} disappeared after it was read") from exc
    if stat.S_ISLNK(current.st_mode) or (
        current.st_dev,
        current.st_ino,
        current.st_size,
        current.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise S108DryRunError(f"{name} changed while it was read")
    return b"".join(chunks)


def _secure_file(
    root: Path,
    value: object,
    name: str,
    *,
    must_exist: bool,
) -> Path:
    path = _reject_symlink_chain(root, _relative_string(value, name), name)
    if must_exist:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError as exc:
            raise S108DryRunError(f"{name} does not exist") from exc
        if not stat.S_ISREG(mode):
            raise S108DryRunError(f"{name} must be a regular non-symlink file")
    return path


def _secure_output(root: Path, value: object, name: str) -> Path:
    return _reject_symlink_chain(root, _relative_string(value, name), name)


def _config_file(root: Path, config_path: Path) -> Path:
    candidate = config_path if config_path.is_absolute() else root / config_path
    try:
        relative = candidate.relative_to(root).as_posix()
    except ValueError as exc:
        raise S108DryRunError("S108 config escaped the workspace") from exc
    return _secure_file(root, relative, "S108 config", must_exist=True)


def _exists_regular(root: Path, value: object, name: str) -> bool:
    path = _secure_file(root, value, name, must_exist=False)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(mode):
        raise S108DryRunError(f"{name} must be a regular non-symlink file")
    return True


def load_config(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise S108DryRunError("S108 config must be a regular non-symlink file")
    try:
        loaded = yaml.load(
            _regular_file_bytes(path, "S108 config").decode("utf-8"),
            Loader=_UniqueKeyLoader,
        )
    except UnicodeDecodeError as exc:
        raise S108DryRunError("S108 config must be UTF-8") from exc
    config = dict(_mapping(loaded, "S108 config"))
    _reject_nonfinite_tree(config, "S108 config")
    return config


def _validate_file_pin(
    *,
    root: Path,
    record: Mapping[str, Any],
    name: str,
) -> Mapping[str, Any]:
    if set(record) != {"path", "sha256"}:
        raise S108DryRunError(f"{name} file pin schema differs")
    path = _secure_file(root, record.get("path"), f"{name}.path", must_exist=True)
    data = _regular_file_bytes(path, name)
    expected = _sha256(record.get("sha256"), f"{name}.sha256")
    actual = _sha256_bytes(data)
    if actual != expected:
        raise S108DryRunError(f"{name} source hash differs")
    return {
        "path": _workspace_relative(root, path),
        "sha256": actual,
        "size_bytes": len(data),
    }


def _validate_source_pins(
    *,
    root: Path,
    config: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    validated: list[Mapping[str, Any]] = []
    predecessors = _mapping(config.get("predecessors"), "predecessors")
    for stage in STAGES:
        stage_config = _mapping(predecessors.get(stage), f"predecessors.{stage}")
        validated.append(
            _validate_file_pin(
                root=root,
                record=_mapping(stage_config.get("config"), f"{stage}.config"),
                name=f"{stage}.config",
            )
        )
        source_key = "validator_source" if stage == "s103" else "validator"
        source_config = _mapping(stage_config.get(source_key), f"{stage}.{source_key}")
        module = (
            source_config
            if stage == "s103"
            else _mapping(source_config.get("module"), f"{stage}.validator.module")
        )
        validated.append(
            _validate_file_pin(
                root=root,
                record=module,
                name=f"{stage}.validator_source",
            )
        )
        if stage != "s103":
            symbol = _string(source_config.get("symbol"), f"{stage}.validator.symbol")
            if not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_.]*\.[A-Za-z_][A-Za-z0-9_]*",
                symbol,
            ):
                raise S108DryRunError(f"{stage} validator symbol is invalid")
            _expect(
                source_config.get("required_status"),
                "valid",
                f"{stage}.validator.required_status",
            )
    implementation = _mapping(config.get("implementation"), "implementation")
    for name in (
        "preflight_script",
        "contract_module",
        "project_manifest",
        "scorer_lock",
    ):
        validated.append(
            _validate_file_pin(
                root=root,
                record=_mapping(implementation.get(name), f"implementation.{name}"),
                name=f"implementation.{name}",
            )
        )
    return tuple(validated)


def validate_static_config(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Validate all static policy and source pins without reading any artifact."""

    root = workspace_root.resolve(strict=True)
    if set(config) != {
        "schema_version",
        "experiment",
        "state_machine",
        "predecessors",
        "external_aggregate_evidence",
        "preregistration_contract",
        "frozen_binding_contract",
        "no_locked_boundary",
        "implementation",
        "outputs",
        "runtime",
    }:
        raise S108DryRunError("S108 config top-level schema differs")
    _expect(config.get("schema_version"), 1, "schema_version")
    experiment = _mapping(config.get("experiment"), "experiment")
    expected_experiment = {
        "id": "aihub71490_wavlm_s108_no_locked_dry_run_v1",
        "stage": "s108_aggregate_only_no_locked_dry_run",
        "dry_run_only": True,
        "aggregate_only": True,
        "result_metric_generation_allowed": False,
        "s108_completion_claim_allowed": False,
        "s108_score_allowed": False,
        "pending_marker_clear_allowed": False,
        "promotion_decision_allowed": False,
        "locked_batch_execution_implemented": False,
    }
    if dict(experiment) != expected_experiment:
        raise S108DryRunError("experiment fail-closed policy differs")
    state = _mapping(config.get("state_machine"), "state_machine")
    _expect(state.get("allowed_states"), list(ALLOWED_STATES), "allowed_states")
    _expect(
        state.get("terminal_result_states_forbidden"),
        list(TERMINAL_STATES_FORBIDDEN),
        "terminal_result_states_forbidden",
    )
    _expect(state.get("predecessor_order"), list(STAGES), "predecessor_order")
    _expect(
        state.get("external_gate_evaluation_order"),
        ["preregistration", "g04", "d07"],
        "external_gate_evaluation_order",
    )
    for key in (
        "stop_predecessor_validation_after_first_incomplete",
        "ready_requires_all_predecessors_and_external_gates",
    ):
        _expect(state.get(key), True, f"state_machine.{key}")

    predecessors = _mapping(config.get("predecessors"), "predecessors")
    _expect(predecessors.get("aggregate_only"), True, "predecessors.aggregate_only")
    _expect(
        predecessors.get("no_row_level_artifact_read"),
        True,
        "predecessors.no_row_level_artifact_read",
    )
    for stage in STAGES:
        item = _mapping(predecessors.get(stage), f"predecessors.{stage}")
        commit = _mapping(item.get("commit"), f"predecessors.{stage}.commit")
        _relative_string(commit.get("path"), f"{stage}.commit.path")
        _string(commit.get("schema_version"), f"{stage}.commit.schema_version")
        _expect(commit.get("required_status"), "committed", f"{stage}.commit.status")
        if stage == "s103":
            _sha256(commit.get("sha256"), "s103.commit.sha256")
            report = _mapping(item.get("report"), "s103.report")
            _sha256(report.get("sha256"), "s103.report.sha256")
            _expect(report.get("required_status"), "completed", "s103.report.status")
            _expect(
                report.get("required_selected_branch"),
                "base_plus",
                "s103 selected branch",
            )
            _expect(
                report.get("required_locked_test_accessed"),
                False,
                "s103 locked access",
            )
            registries = _list(item.get("registries"), "s103.registries")
            if len(registries) != 2:
                raise S108DryRunError("S103 must bind exactly two source registries")
            for index, value in enumerate(registries):
                registry = _mapping(value, f"s103.registries[{index}]")
                _relative_string(registry.get("path"), "S103 registry path")
                _sha256(registry.get("sha256"), "S103 registry SHA-256")
                _expect(
                    registry.get("required_status"),
                    "completed",
                    "S103 registry status",
                )
                _expect(
                    registry.get("required_artifact_count"),
                    13,
                    "S103 registry artifact count",
                )
        elif stage == "s104":
            progress = _mapping(item.get("progress_registry"), "s104.progress_registry")
            _relative_string(progress.get("path"), "S104 progress registry path")
            if progress.get("allowed_statuses") != ["running", "failed", "completed"]:
                raise S108DryRunError("S104 progress statuses differ")

    external = _mapping(
        config.get("external_aggregate_evidence"),
        "external_aggregate_evidence",
    )
    _expect(
        external.get("aggregate_only_schema_required"),
        True,
        "external aggregate policy",
    )
    _expect(
        external.get("identifiers_rows_audio_labels_predictions_forbidden"),
        True,
        "external content policy",
    )
    for section, key in (
        ("preregistration", "attestation_path"),
        ("g04", "progress_path"),
        ("g04", "attestation_path"),
        ("d07", "attestation_path"),
    ):
        _relative_string(
            _mapping(external.get(section), section).get(key),
            f"{section}.{key}",
        )
    g04 = _mapping(external.get("g04"), "g04")
    _expect(g04.get("expected_count"), 240, "G04 expected count")
    _expect(g04.get("minimum_correct_count"), 228, "G04 minimum correct")
    _expect(g04.get("minimum_accuracy"), 0.95, "G04 minimum accuracy")
    _expect(
        g04.get("reviewer_qualification_attestation_required"),
        True,
        "G04 reviewer attestation",
    )
    d07 = _mapping(external.get("d07"), "d07")
    for key, expected in (
        ("calibration_count", 800),
        ("locked_gold_minimum_count", 1),
        ("locked_gold_maximum_count", 1200),
        ("independent_rater_count", 2),
        ("adjudicator_count", 1),
        ("scalar_agreement_threshold", 0.70),
        ("taxonomy_agreement_threshold", 0.65),
    ):
        _expect(d07.get(key), expected, f"D07 {key}")

    prereg = _mapping(config.get("preregistration_contract"), "preregistration")
    _expect(prereg.get("unresolved_marker"), "TBD_PREREGISTRATION", "TBD marker")
    primary = _mapping(prereg.get("primary_icc"), "primary ICC")
    _expect(primary.get("outcome"), "TBD_PREREGISTRATION", "primary ICC outcome")
    _expect(primary.get("metric"), "icc_2_1", "primary ICC metric")
    _expect(primary.get("gate_minimum"), 0.70, "primary ICC gate")
    word = _mapping(prereg.get("word_and_primary_family"), "word primary family")
    _expect(word.get("decision"), "TBD_PREREGISTRATION", "word decision")
    _expect(
        word.get("word_pcc_without_independent_gold_forbidden"),
        True,
        "word PCC policy",
    )
    _expect(
        word.get("missing_word_may_not_count_as_success"),
        True,
        "missing word policy",
    )
    precision = _mapping(
        prereg.get("issued_phone_precision"),
        "issued phone precision",
    )
    _expect(precision.get("gate_minimum"), 0.90, "issued precision gate")
    for key in (
        "minimum_issued_phone_count",
        "minimum_issued_coverage",
        "ci_method",
        "confidence_level",
        "gate_statistic",
        "cluster_unit",
    ):
        _expect(precision.get(key), "TBD_PREREGISTRATION", f"precision {key}")
    subgroup = _mapping(prereg.get("subgroup_support"), "subgroup support")
    for key in (
        "minimum_speakers_per_group",
        "minimum_positive_phones_per_group",
        "minimum_negative_phones_per_group",
        "missing_group_policy",
    ):
        _expect(subgroup.get(key), "TBD_PREREGISTRATION", f"subgroup {key}")
    _expect(
        subgroup.get("primary_axes"),
        ["l1_group", "gender_group", "proficiency_group"],
        "subgroup primary axes",
    )
    one_shot = _mapping(prereg.get("one_shot_batch"), "one-shot batch")
    _expect(
        one_shot.get("includes_models"),
        ["b05_explicit_baseline", "s108_final_s1"],
        "one-shot models",
    )
    _expect(one_shot.get("access_count"), 1, "one-shot access count")
    _expect(
        one_shot.get("result_based_reselection_allowed"),
        False,
        "one-shot reselection",
    )
    _expect(
        one_shot.get("rerun_after_gate_failure_allowed"),
        False,
        "one-shot rerun",
    )

    binding = _mapping(config.get("frozen_binding_contract"), "binding contract")
    _expect(
        binding.get("required_hash_names"),
        [
            "s103_commit_sha256",
            "s104_commit_sha256",
            "s105_commit_sha256",
            "s106_commit_sha256",
            "s107_commit_sha256",
            "final_fusion_checkpoint_sha256",
            "calibrator_parameters_sha256",
            "threshold_policy_sha256",
            "source_chain_sha256",
        ],
        "required hash names",
    )
    _expect(
        binding.get("every_hash_must_be_lowercase_sha256"),
        True,
        "binding SHA policy",
    )
    _expect(
        binding.get("preregistration_hashes_must_match_validated_predecessors"),
        True,
        "binding match policy",
    )
    boundary = _mapping(config.get("no_locked_boundary"), "no-locked boundary")
    for key, expected in (
        ("locked_manifest_locator_present_in_config", False),
        ("locked_manifest_resolution_allowed", False),
        ("locked_row_read_allowed", False),
        ("locked_audio_read_allowed", False),
        ("locked_label_read_allowed", False),
        ("locked_prediction_read_allowed", False),
        ("locked_metric_computation_allowed", False),
    ):
        _expect(boundary.get(key), expected, f"no-locked {key}")
    _expect(
        boundary.get("required_access_counts"),
        dict(LOCKED_ZERO_COUNTS),
        "no-locked access counts",
    )
    outputs = _mapping(config.get("outputs"), "outputs")
    _expect(outputs.get("expected_artifact_count"), 1, "output artifact count")
    _expect(
        outputs.get("artifact_relative_path"),
        "no_locked_checklist.json",
        "checklist path",
    )
    _expect(
        outputs.get("generation_schema_version"),
        CHECKLIST_SCHEMA_VERSION,
        "checklist schema",
    )
    _expect(
        outputs.get("receipt_schema_version"),
        RECEIPT_SCHEMA_VERSION,
        "receipt schema",
    )
    for key in ("atomic_publish", "content_addressed_generation", "no_overwrite"):
        _expect(outputs.get(key), True, f"outputs.{key}")
    _expect(
        outputs.get("completed_registry_created"),
        False,
        "completed registry policy",
    )
    _expect(
        outputs.get("canonical_s108_commit_created"),
        False,
        "canonical commit policy",
    )
    _secure_output(root, outputs.get("generation_root"), "generation root")
    _secure_output(root, outputs.get("receipt_root"), "receipt root")
    runtime = _mapping(config.get("runtime"), "runtime")
    _expect(runtime.get("device"), "cpu", "runtime device")
    for key in (
        "cuda_allowed",
        "raw_audio_access_allowed",
        "environment_files_allowed",
    ):
        _expect(runtime.get(key), False, f"runtime.{key}")
    _expect(
        runtime.get("temporary_generation_cleanup_required"),
        True,
        "runtime cleanup",
    )
    _config_file(root, config_path)
    sources = _validate_source_pins(root=root, config=config)
    return {
        "status": "valid",
        "source_count": len(sources),
        "sources": list(sources),
        "protected_data_access_count": 0,
        **LOCKED_ZERO_COUNTS,
        "output_count": 0,
    }


class AggregateReader:
    """Reader constrained to explicit aggregate files recorded in the config."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._accesses: list[AggregateAccess] = []

    @property
    def accesses(self) -> tuple[AggregateAccess, ...]:
        return tuple(self._accesses)

    def exists(self, relative: object, name: str) -> bool:
        return _exists_regular(self.root, relative, name)

    def json(
        self,
        relative: object,
        *,
        category: str,
        name: str,
        expected_sha256: str | None = None,
    ) -> Mapping[str, Any]:
        path = _secure_file(self.root, relative, name, must_exist=True)
        data = _regular_file_bytes(path, name)
        digest = _sha256_bytes(data)
        if expected_sha256 is not None and digest != expected_sha256:
            raise S108DryRunError(f"{name} SHA-256 differs")
        self._accesses.append(
            AggregateAccess(
                category=category,
                path=_workspace_relative(self.root, path),
                sha256=digest,
                size_bytes=len(data),
            )
        )
        return _parse_json(data, name)

    def revalidate(self) -> None:
        for access in self._accesses:
            path = _secure_file(
                self.root,
                access.path,
                f"TOCTOU recheck {access.path}",
                must_exist=True,
            )
            data = _regular_file_bytes(path, f"TOCTOU recheck {access.path}")
            if len(data) != access.size_bytes or _sha256_bytes(data) != access.sha256:
                raise S108DryRunError(
                    f"aggregate input changed before publication: {access.path}"
                )


def _binding_sha256(
    document: Mapping[str, Any],
    *,
    trailing_newline: bool = False,
    compact: bool = True,
) -> str:
    unsigned = dict(document)
    unsigned.pop("binding_sha256", None)
    if compact:
        encoded = canonical_json_bytes(unsigned, pretty=False)
    else:
        encoded = json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    if trailing_newline:
        encoded += b"\n"
    return _sha256_bytes(encoded)


def _artifact_index(
    *,
    artifacts: object,
    expected_count: int,
    commit_schema: str,
    generation_id: object,
    stage: str,
    compact_json_trailing_newline: bool,
) -> Mapping[str, Mapping[str, Any]]:
    values = _list(artifacts, f"{stage} artifacts")
    if len(values) != expected_count:
        raise S108DryRunError(f"{stage} artifact count differs")
    by_name: dict[str, Mapping[str, Any]] = {}
    paths: set[str] = set()
    normalized: list[Mapping[str, Any]] = []
    for index, value in enumerate(values):
        item = _mapping(value, f"{stage} artifact[{index}]")
        required = {"name", "path", "sha256", "size_bytes"}
        if not required.issubset(item):
            raise S108DryRunError(f"{stage} artifact metadata is incomplete")
        name = _string(item.get("name"), f"{stage} artifact name")
        relative = _relative_string(item.get("path"), f"{stage} artifact path")
        _sha256(item.get("sha256"), f"{stage} artifact SHA-256")
        _integer(item.get("size_bytes"), f"{stage} artifact size", minimum=1)
        if name in by_name or relative in paths:
            raise S108DryRunError(f"{stage} artifact name/path is duplicated")
        by_name[name] = item
        paths.add(relative)
        normalized.append(item)
    generation_payload = canonical_json_bytes(
        {
            "schema_version": commit_schema,
            "artifacts": normalized,
        },
        pretty=False,
    )
    if compact_json_trailing_newline:
        generation_payload += b"\n"
    expected_generation = _sha256_bytes(generation_payload)
    if generation_id != expected_generation:
        raise S108DryRunError(f"{stage} generation identity differs")
    return by_name


def _validate_registry_record(
    *,
    reader: AggregateReader,
    record: Mapping[str, Any],
    stage: str,
) -> str:
    if not {"path", "sha256", "run_id", "status", "artifact_count"}.issubset(record):
        raise S108DryRunError(f"{stage} registry record is incomplete")
    _expect(record.get("status"), "completed", f"{stage} registry status")
    digest = _sha256(record.get("sha256"), f"{stage} registry SHA-256")
    document = reader.json(
        record.get("path"),
        category="predecessor_registry",
        name=f"{stage} registry",
        expected_sha256=digest,
    )
    _expect(document.get("schema_version"), 1, f"{stage} registry schema")
    _expect(document.get("run_id"), record.get("run_id"), f"{stage} registry run")
    _expect(document.get("status"), "completed", f"{stage} registry status")
    _expect(document.get("failure"), None, f"{stage} registry failure")
    artifacts = _list(document.get("artifacts"), f"{stage} registry artifacts")
    _expect(
        len(artifacts),
        record.get("artifact_count"),
        f"{stage} registry artifact count",
    )
    return digest


def _validate_s103(
    *,
    reader: AggregateReader,
    config: Mapping[str, Any],
) -> StageEvidence:
    stage = _mapping(_mapping(config["predecessors"], "predecessors")["s103"], "s103")
    commit_config = _mapping(stage.get("commit"), "s103.commit")
    commit = reader.json(
        commit_config.get("path"),
        category="predecessor_commit",
        name="S103 commit",
        expected_sha256=_sha256(commit_config.get("sha256"), "S103 commit SHA-256"),
    )
    _expect(
        commit.get("schema_version"), commit_config.get("schema_version"), "S103 schema"
    )
    _expect(commit.get("status"), "committed", "S103 status")
    _expect(
        commit.get("binding_sha256"),
        _binding_sha256(commit, trailing_newline=True, compact=False),
        "S103 commit binding",
    )
    generation_id = _sha256(commit.get("generation_id"), "S103 generation")
    report_record = _mapping(commit.get("comparison_report"), "S103 report record")
    report_config = _mapping(stage.get("report"), "s103.report")
    report_sha = _sha256(report_config.get("sha256"), "S103 report SHA-256")
    _expect(report_record.get("sha256"), report_sha, "S103 report binding")
    _expect(generation_id, report_sha, "S103 generation/report identity")
    report = reader.json(
        report_record.get("path"),
        category="predecessor_report",
        name="S103 aggregate report",
        expected_sha256=report_sha,
    )
    _expect(
        report.get("schema_version"),
        report_config.get("schema_version"),
        "S103 report schema",
    )
    _expect(report.get("status"), "completed", "S103 report status")
    summary = _mapping(report.get("summary"), "S103 summary")
    _expect(summary.get("large_adoption_gate_passed"), False, "S103 branch")
    _expect(summary.get("locked_test_remains_unread"), True, "S103 locked guard")
    _expect(summary.get("stack_remains_unread"), True, "S103 stack guard")
    _expect(summary.get("calibration_remains_unread"), True, "S103 calibration guard")
    registry_hashes: list[str] = []
    for value in _list(stage.get("registries"), "S103 registries"):
        item = _mapping(value, "S103 registry config")
        registry = reader.json(
            item.get("path"),
            category="predecessor_registry",
            name="S103 source registry",
            expected_sha256=_sha256(item.get("sha256"), "S103 registry SHA-256"),
        )
        _expect(registry.get("schema_version"), 1, "S103 registry schema")
        _expect(registry.get("run_id"), item.get("run_id"), "S103 registry run")
        _expect(registry.get("status"), "completed", "S103 registry status")
        _expect(registry.get("failure"), None, "S103 registry failure")
        _expect(
            len(_list(registry.get("artifacts"), "S103 registry artifacts")),
            13,
            "S103 registry artifact count",
        )
        registry_hashes.append(cast(str, item["sha256"]))
    return StageEvidence(
        stage="s103",
        status="valid",
        reason=None,
        commit_sha256=cast(str, commit_config["sha256"]),
        generation_id=generation_id,
        registry_sha256=tuple(registry_hashes),
        aggregate_report_sha256=report_sha,
        artifact_hashes={},
        validation_depth="aggregate_commit_registry_report_only",
    )


def _validate_stage_report(stage: str, report: Mapping[str, Any]) -> None:
    layout = STAGE_LAYOUT[stage]
    _expect(
        report.get("schema_version"), layout["report_schema"], f"{stage} report schema"
    )
    _expect(report.get("status"), layout["report_status"], f"{stage} report status")
    _expect(
        report.get("promotion_decision_allowed"),
        False,
        f"{stage} promotion policy",
    )
    if stage == "s104":
        _expect(report.get("development_only"), True, "S104 development status")
        leakage = _mapping(report.get("leakage"), "S104 leakage")
        _expect(leakage.get("stack_accessed"), False, "S104 stack access")
        _expect(leakage.get("calibration_accessed"), False, "S104 calibration access")
        _expect(leakage.get("locked_test_accessed"), False, "S104 locked access")
    elif stage == "s105":
        leakage = _mapping(report.get("leakage"), "S105 leakage")
        _expect(leakage.get("stack_accessed"), False, "S105 stack access")
        _expect(leakage.get("calibration_accessed"), False, "S105 calibration access")
        _expect(leakage.get("locked_test_accessed"), False, "S105 locked access")
        word = _mapping(report.get("word_supervision"), "S105 word supervision")
        _expect(word.get("word_loss_performed"), False, "S105 word loss")
        _expect(word.get("word_pcc_status"), "not_evaluable", "S105 word PCC")
    elif stage == "s106":
        leakage = _mapping(report.get("leakage"), "S106 leakage")
        _expect(leakage.get("stack_accessed"), True, "S106 stack access")
        _expect(leakage.get("train_accessed"), False, "S106 train access")
        _expect(leakage.get("dev_accessed"), False, "S106 dev access")
        _expect(leakage.get("calibration_accessed"), False, "S106 calibration access")
        _expect(leakage.get("locked_test_accessed"), False, "S106 locked access")
    elif stage == "s107":
        split = _mapping(report.get("split_access"), "S107 split access")
        _expect(split.get("train"), False, "S107 train access")
        _expect(split.get("dev"), False, "S107 dev access")
        _expect(split.get("stack"), False, "S107 stack access")
        _expect(split.get("calibration"), True, "S107 calibration access")
        _expect(split.get("locked_test"), False, "S107 locked access")
        _expect(report.get("s108_ready"), False, "S107 S108 readiness")


def _validate_stage(
    *,
    stage_name: str,
    reader: AggregateReader,
    config: Mapping[str, Any],
    previous: Mapping[str, StageEvidence],
) -> StageEvidence:
    stage = _mapping(
        _mapping(config["predecessors"], "predecessors")[stage_name],
        stage_name,
    )
    commit_config = _mapping(stage.get("commit"), f"{stage_name}.commit")
    if not reader.exists(commit_config.get("path"), f"{stage_name} commit"):
        reason = "commit_missing"
        if stage_name == "s104":
            progress = _mapping(stage.get("progress_registry"), "S104 progress")
            if reader.exists(progress.get("path"), "S104 progress registry"):
                registry = reader.json(
                    progress.get("path"),
                    category="predecessor_progress_registry",
                    name="S104 progress registry",
                )
                _expect(registry.get("schema_version"), 1, "S104 progress schema")
                _expect(
                    registry.get("run_id"), progress.get("run_id"), "S104 progress run"
                )
                status = registry.get("status")
                if status not in progress.get("allowed_statuses", []):
                    raise S108DryRunError("S104 progress status is invalid")
                reason = f"commit_missing_registry_{status}"
        return StageEvidence(
            stage=stage_name,
            status="incomplete",
            reason=reason,
            commit_sha256=None,
            generation_id=None,
            registry_sha256=(),
            aggregate_report_sha256=None,
            artifact_hashes={},
            validation_depth="aggregate_commit_registry_report_only",
        )
    commit = reader.json(
        commit_config.get("path"),
        category="predecessor_commit",
        name=f"{stage_name.upper()} commit",
    )
    commit_schema = _string(commit_config.get("schema_version"), "commit schema")
    _expect(commit.get("schema_version"), commit_schema, f"{stage_name} commit schema")
    _expect(commit.get("status"), "committed", f"{stage_name} commit status")
    generation_id = _sha256(commit.get("generation_id"), f"{stage_name} generation")
    binding = _sha256(commit.get("binding_sha256"), f"{stage_name} binding")
    layout = STAGE_LAYOUT[stage_name]
    compact_newline = cast(bool, layout["compact_json_trailing_newline"])
    _expect(
        binding,
        _binding_sha256(commit, trailing_newline=compact_newline),
        f"{stage_name} commit binding",
    )
    artifacts = _artifact_index(
        artifacts=commit.get("artifacts"),
        expected_count=cast(int, layout["artifact_count"]),
        commit_schema=commit_schema,
        generation_id=generation_id,
        stage=stage_name,
        compact_json_trailing_newline=compact_newline,
    )
    generation_path = _relative_string(
        commit.get("generation_path"),
        f"{stage_name} generation path",
    )
    if not generation_path.endswith(f"/generations/{generation_id}"):
        raise S108DryRunError(f"{stage_name} generation path differs")
    report_name = cast(str, layout["report_name"])
    if report_name not in artifacts:
        raise S108DryRunError(f"{stage_name} aggregate report artifact is absent")
    report_record = artifacts[report_name]
    report_relative = _relative_string(
        report_record.get("path"),
        f"{stage_name} report relative path",
    )
    report_path = f"{generation_path}/{report_relative}"
    report_sha = _sha256(report_record.get("sha256"), f"{stage_name} report SHA-256")
    report = reader.json(
        report_path,
        category="predecessor_report",
        name=f"{stage_name.upper()} aggregate report",
        expected_sha256=report_sha,
    )
    _expect(
        len(canonical_json_bytes(report, pretty=True)),
        report_record.get("size_bytes"),
        f"{stage_name} report size",
    )
    _validate_stage_report(stage_name, report)
    if stage_name == "s104":
        _expect(
            commit.get("experiment_report_sha256"),
            report_sha,
            "S104 commit report binding",
        )
    elif stage_name == "s105":
        _expect(
            commit.get("comparison_report_sha256"),
            report_sha,
            "S105 commit report binding",
        )

    registry_hashes: list[str] = []
    if stage_name == "s105":
        records = _list(commit.get("registries"), "S105 registries")
        if len(records) != 5:
            raise S108DryRunError("S105 must bind five registries")
        for value in records:
            registry_hashes.append(
                _validate_registry_record(
                    reader=reader,
                    record=_mapping(value, "S105 registry record"),
                    stage="s105",
                )
            )
    else:
        registry_hashes.append(
            _validate_registry_record(
                reader=reader,
                record=_mapping(commit.get("registry"), f"{stage_name} registry"),
                stage=stage_name,
            )
        )

    if stage_name == "s104":
        branch = _mapping(report.get("s103_branch_evidence"), "S104 S103 branch")
        _expect(
            branch.get("commit_sha256"),
            previous["s103"].commit_sha256,
            "S104 predecessor commit",
        )
    elif stage_name == "s105":
        predecessor = _mapping(commit.get("predecessor"), "S105 predecessor")
        _expect(
            predecessor.get("commit_sha256"),
            previous["s104"].commit_sha256,
            "S105 predecessor commit",
        )
    elif stage_name == "s107":
        predecessor = _mapping(commit.get("s106_commit"), "S107 S106 commit")
        _expect(
            predecessor.get("sha256"),
            previous["s106"].commit_sha256,
            "S107 predecessor commit",
        )
        _expect(commit.get("promotion_decision_allowed"), False, "S107 promotion")
        _expect(commit.get("s108_ready"), False, "S107 readiness")

    commit_access = next(
        access
        for access in reversed(reader.accesses)
        if access.category == "predecessor_commit"
        and access.path == commit_config.get("path")
    )
    return StageEvidence(
        stage=stage_name,
        status="valid",
        reason=None,
        commit_sha256=commit_access.sha256,
        generation_id=generation_id,
        registry_sha256=tuple(registry_hashes),
        aggregate_report_sha256=report_sha,
        artifact_hashes={
            name: cast(str, value["sha256"]) for name, value in artifacts.items()
        },
        validation_depth="aggregate_commit_registry_report_only",
    )


def validate_predecessors_aggregate(
    *,
    reader: AggregateReader,
    config: Mapping[str, Any],
) -> tuple[StageEvidence, ...]:
    """Validate predecessor evidence in order without opening row artifacts."""

    evidence: dict[str, StageEvidence] = {}
    if not reader.exists(
        _mapping(
            _mapping(config["predecessors"], "predecessors")["s103"],
            "s103",
        )["commit"]["path"],
        "S103 commit",
    ):
        evidence["s103"] = StageEvidence(
            stage="s103",
            status="incomplete",
            reason="commit_missing",
            commit_sha256=None,
            generation_id=None,
            registry_sha256=(),
            aggregate_report_sha256=None,
            artifact_hashes={},
            validation_depth="aggregate_commit_registry_report_only",
        )
        return tuple(evidence[name] for name in evidence)
    evidence["s103"] = _validate_s103(reader=reader, config=config)
    for stage in STAGES[1:]:
        result = _validate_stage(
            stage_name=stage,
            reader=reader,
            config=config,
            previous=evidence,
        )
        evidence[stage] = result
        if result.status != "valid":
            break
    return tuple(evidence[name] for name in evidence)


def _aggregate_attestation_flags(document: Mapping[str, Any], name: str) -> None:
    _expect(document.get("aggregate_only"), True, f"{name}.aggregate_only")
    for key in (
        "contains_identifiers",
        "contains_rows",
        "contains_audio",
        "contains_labels",
        "contains_predictions",
    ):
        _expect(document.get(key), False, f"{name}.{key}")
    _expect(document.get("locked_access_count"), 0, f"{name}.locked_access_count")


def _validate_preregistration(
    document: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    expected_hashes: Mapping[str, str] | None,
) -> None:
    external = _mapping(config["external_aggregate_evidence"], "external")
    section = _mapping(external["preregistration"], "preregistration evidence")
    _expect(
        document.get("schema_version"), section.get("schema_version"), "prereg schema"
    )
    _expect(document.get("status"), "frozen", "prereg status")
    _aggregate_attestation_flags(document, "preregistration")
    _expect(document.get("approved_by_research_lead"), True, "prereg approval")
    _expect(document.get("approved_before_locked_access"), True, "prereg timing")
    _expect(document.get("locked_results_unseen"), True, "prereg unseen")
    policy = _mapping(document.get("policy"), "prereg policy")
    contract = _mapping(config["preregistration_contract"], "prereg contract")
    primary = _mapping(policy.get("primary_icc"), "primary ICC policy")
    allowed_outcomes = _list(
        _mapping(contract["primary_icc"], "primary contract")["allowed_outcomes"],
        "allowed primary outcomes",
    )
    if primary.get("outcome") not in allowed_outcomes:
        raise S108DryRunError("primary ICC outcome was not preregistered")
    _expect(primary.get("metric"), "icc_2_1", "primary ICC metric")
    _expect(primary.get("gate_minimum"), 0.70, "primary ICC gate")
    word = _mapping(policy.get("word_and_primary_family"), "word policy")
    decision = word.get("decision")
    allowed = _list(
        _mapping(contract["word_and_primary_family"], "word contract")[
            "allowed_resolutions"
        ],
        "word resolutions",
    )
    if decision not in allowed:
        raise S108DryRunError("word/2-of-3 decision is unresolved")
    if decision == "independent_word_label_preserve_2_of_3":
        _expect(word.get("independent_word_gold_completed"), True, "word gold")
        _sha256(word.get("word_label_schema_sha256"), "word label schema hash")
        _expect(word.get("successes_required"), 2, "2-of-3 successes")
        _expect(
            word.get("primary_metrics"),
            ["phone_macro_f1", "word_pcc", "utterance_primary_icc_2_1"],
            "2-of-3 primary metrics",
        )
    else:
        _expect(word.get("approved_protocol_amendment"), True, "protocol amendment")
        _sha256(word.get("protocol_amendment_sha256"), "amendment SHA-256")
        metrics = _list(word.get("primary_metrics"), "amended primary metrics")
        if len(metrics) < 2 or "word_pcc" in metrics:
            raise S108DryRunError("amended primary family is invalid")
        successes = _integer(
            word.get("successes_required"),
            "amended success count",
            minimum=1,
        )
        if successes > len(metrics):
            raise S108DryRunError("amended success count exceeds metric count")
    precision = _mapping(policy.get("issued_phone_precision"), "precision policy")
    _expect(precision.get("gate_minimum"), 0.90, "precision gate")
    _integer(
        precision.get("minimum_issued_phone_count"),
        "minimum issued phone count",
        minimum=1,
    )
    coverage = _finite(
        precision.get("minimum_issued_coverage"),
        "minimum issued coverage",
    )
    if not 0 < coverage <= 1:
        raise S108DryRunError("minimum issued coverage must be in (0, 1]")
    precision_contract = _mapping(
        contract["issued_phone_precision"],
        "precision contract",
    )
    if precision.get("ci_method") not in precision_contract["allowed_ci_methods"]:
        raise S108DryRunError("precision CI method is not allowed")
    confidence = _finite(precision.get("confidence_level"), "confidence level")
    if not 0.90 <= confidence < 1:
        raise S108DryRunError("precision confidence level is invalid")
    if (
        precision.get("gate_statistic")
        not in precision_contract["allowed_gate_statistics"]
    ):
        raise S108DryRunError("precision gate statistic is not allowed")
    _expect(
        precision.get("cluster_unit"),
        "speaker_id_sha256",
        "precision cluster unit",
    )
    subgroup = _mapping(policy.get("subgroup_support"), "subgroup policy")
    for key in (
        "minimum_speakers_per_group",
        "minimum_positive_phones_per_group",
        "minimum_negative_phones_per_group",
    ):
        _integer(subgroup.get(key), key, minimum=1)
    allowed_missing = _list(
        _mapping(contract["subgroup_support"], "subgroup contract")[
            "allowed_missing_group_policies"
        ],
        "missing group policies",
    )
    if subgroup.get("missing_group_policy") not in allowed_missing:
        raise S108DryRunError("subgroup missing policy is unresolved")
    batch = _mapping(policy.get("one_shot_batch"), "one-shot policy")
    _expect(
        batch.get("includes_models"),
        ["b05_explicit_baseline", "s108_final_s1"],
        "one-shot models",
    )
    _expect(batch.get("access_count"), 1, "one-shot access")
    _expect(batch.get("result_based_reselection_allowed"), False, "reselection")
    _expect(batch.get("rerun_after_gate_failure_allowed"), False, "rerun")
    bindings = _mapping(document.get("frozen_hashes"), "prereg frozen hashes")
    if expected_hashes is None:
        raise S108DryRunError("preregistration cannot bind incomplete predecessors")
    if dict(bindings) != dict(expected_hashes):
        raise S108DryRunError("preregistration hash bindings differ")


def _validate_g04(document: Mapping[str, Any], *, config: Mapping[str, Any]) -> None:
    section = _mapping(
        _mapping(config["external_aggregate_evidence"], "external")["g04"],
        "G04 config",
    )
    _expect(
        document.get("schema_version"),
        section.get("attestation_schema_version"),
        "G04 attestation schema",
    )
    _expect(document.get("status"), "passed", "G04 status")
    _aggregate_attestation_flags(document, "G04")
    _expect(document.get("expected_count"), 240, "G04 expected count")
    _expect(document.get("completed_count"), 240, "G04 completed count")
    correct = _integer(document.get("correct_count"), "G04 correct count", minimum=0)
    if correct < 228 or correct > 240:
        raise S108DryRunError("G04 correct count does not pass 228/240")
    accuracy = _finite(document.get("alignment_accuracy"), "G04 accuracy")
    if accuracy < 0.95 or not math.isclose(accuracy, correct / 240, abs_tol=1e-12):
        raise S108DryRunError("G04 accuracy differs")
    _expect(document.get("scorer_status"), "passed", "G04 scorer status")
    _sha256(document.get("privacy_safe_report_sha256"), "G04 report SHA-256")
    _expect(
        document.get("reviewer_qualification_confirmed"),
        True,
        "G04 reviewer qualification",
    )
    _expect(document.get("research_lead_approved"), True, "G04 approval")


def _validate_d07(document: Mapping[str, Any], *, config: Mapping[str, Any]) -> None:
    section = _mapping(
        _mapping(config["external_aggregate_evidence"], "external")["d07"],
        "D07 config",
    )
    _expect(document.get("schema_version"), section.get("schema_version"), "D07 schema")
    _expect(document.get("status"), "completed", "D07 status")
    _aggregate_attestation_flags(document, "D07")
    _expect(document.get("calibration_count"), 800, "D07 calibration count")
    locked_gold_count = _integer(
        document.get("locked_gold_count"),
        "D07 sealed gold count",
        minimum=1,
    )
    if locked_gold_count > 1200:
        raise S108DryRunError("D07 sealed gold count exceeds 1,200")
    _expect(document.get("independent_rater_count"), 2, "D07 rater count")
    _expect(document.get("adjudicator_count"), 1, "D07 adjudicator count")
    _expect(
        document.get("two_original_ratings_retained"),
        True,
        "D07 original ratings",
    )
    _expect(document.get("adjudication_completed"), True, "D07 adjudication")
    scalar = _finite(document.get("scalar_agreement"), "D07 scalar agreement")
    taxonomy = _finite(document.get("taxonomy_agreement"), "D07 taxonomy agreement")
    if scalar < 0.70 and taxonomy < 0.65:
        raise S108DryRunError("D07 agreement gate failed")
    _expect(document.get("agreement_gate_passed"), True, "D07 agreement status")
    _expect(document.get("label_definition_frozen"), True, "D07 labels")
    _expect(document.get("model_results_unseen"), True, "D07 unseen model results")
    _expect(document.get("research_lead_approved"), True, "D07 approval")
    _sha256(document.get("calibration_gold_sha256"), "D07 calibration gold SHA-256")
    _sha256(document.get("sealed_locked_gold_sha256"), "D07 sealed gold SHA-256")


def _progress_snapshot(
    *,
    reader: AggregateReader,
    config: Mapping[str, Any],
) -> Mapping[str, Any]:
    section = _mapping(
        _mapping(config["external_aggregate_evidence"], "external")["g04"],
        "G04",
    )
    path = section.get("progress_path")
    if not reader.exists(path, "G04 progress aggregate"):
        return {"status": "missing", "completed_count": None, "correct_count": None}
    report = reader.json(
        path,
        category="external_progress_aggregate",
        name="G04 progress aggregate",
    )
    _expect(
        report.get("schema_version"),
        section.get("progress_schema_version"),
        "G04 progress schema",
    )
    _expect(report.get("expected_count"), 240, "G04 progress expected count")
    completed = _integer(
        report.get("completed_count"), "G04 progress completed", minimum=0
    )
    correct = _integer(report.get("correct_count"), "G04 progress correct", minimum=0)
    if completed > 240 or correct > completed:
        raise S108DryRunError("G04 progress counts are invalid")
    privacy = _mapping(report.get("privacy"), "G04 progress privacy")
    _expect(
        privacy.get("raw_paths_or_utterance_ids_exported"),
        False,
        "G04 progress privacy",
    )
    return {
        "status": str(report.get("status")),
        "completed_count": completed,
        "correct_count": correct,
        "remaining_count": 240 - completed,
    }


def _expected_hash_bindings(
    stage_evidence: Sequence[StageEvidence],
) -> Mapping[str, str] | None:
    by_stage = {item.stage: item for item in stage_evidence}
    if set(by_stage) != set(STAGES) or any(
        by_stage[stage].status != "valid" for stage in STAGES
    ):
        return None
    base: dict[str, str] = {}
    for stage in STAGES:
        base[f"{stage}_commit_sha256"] = _sha256(
            by_stage[stage].commit_sha256,
            f"{stage} commit hash",
        )
    base["final_fusion_checkpoint_sha256"] = _sha256(
        by_stage["s106"].artifact_hashes.get("final_fusion_checkpoint"),
        "final fusion checkpoint hash",
    )
    base["calibrator_parameters_sha256"] = _sha256(
        by_stage["s107"].artifact_hashes.get("calibrator_parameters"),
        "calibrator parameters hash",
    )
    base["threshold_policy_sha256"] = _sha256(
        by_stage["s107"].artifact_hashes.get("threshold_policy_report"),
        "threshold policy hash",
    )
    base["source_chain_sha256"] = _sha256_bytes(
        canonical_json_bytes(base, pretty=False)
    )
    return base


def evaluate_external_gates(
    *,
    reader: AggregateReader,
    config: Mapping[str, Any],
    expected_hashes: Mapping[str, str] | None,
) -> Mapping[str, Any]:
    external = _mapping(config["external_aggregate_evidence"], "external")
    output: dict[str, Any] = {}
    prereg = _mapping(external["preregistration"], "preregistration")
    prereg_path = prereg.get("attestation_path")
    if not reader.exists(prereg_path, "preregistration attestation"):
        output["preregistration"] = {
            "state": "blocked_preregistration",
            "reason": "attestation_missing",
        }
    else:
        try:
            document = reader.json(
                prereg_path,
                category="external_attestation_aggregate",
                name="preregistration attestation",
            )
            _validate_preregistration(
                document,
                config=config,
                expected_hashes=expected_hashes,
            )
        except S108DryRunError as exc:
            output["preregistration"] = {
                "state": "blocked_preregistration",
                "reason": str(exc),
            }
        else:
            output["preregistration"] = {"state": "ready", "reason": None}

    progress = _progress_snapshot(reader=reader, config=config)
    g04 = _mapping(external["g04"], "G04")
    g04_path = g04.get("attestation_path")
    if not reader.exists(g04_path, "G04 completion attestation"):
        output["g04"] = {
            "state": "blocked_g04",
            "reason": "attestation_missing",
            "progress": progress,
        }
    else:
        try:
            document = reader.json(
                g04_path,
                category="external_attestation_aggregate",
                name="G04 completion attestation",
            )
            _validate_g04(document, config=config)
        except S108DryRunError as exc:
            output["g04"] = {
                "state": "blocked_g04",
                "reason": str(exc),
                "progress": progress,
            }
        else:
            output["g04"] = {
                "state": "ready",
                "reason": None,
                "progress": progress,
            }

    d07 = _mapping(external["d07"], "D07")
    d07_path = d07.get("attestation_path")
    if not reader.exists(d07_path, "D07 completion attestation"):
        output["d07"] = {
            "state": "blocked_d07",
            "reason": "attestation_missing",
        }
    else:
        try:
            document = reader.json(
                d07_path,
                category="external_attestation_aggregate",
                name="D07 completion attestation",
            )
            _validate_d07(document, config=config)
        except S108DryRunError as exc:
            output["d07"] = {
                "state": "blocked_d07",
                "reason": str(exc),
            }
        else:
            output["d07"] = {"state": "ready", "reason": None}
    return output


def _overall_state(
    stages: Sequence[StageEvidence],
    external: Mapping[str, Any],
) -> str:
    by_stage = {item.stage: item for item in stages}
    if "s103" not in by_stage or by_stage["s103"].status != "valid":
        return "waiting_s103"
    if set(by_stage) != set(STAGES) or any(
        by_stage[stage].status != "valid" for stage in STAGES
    ):
        return "engineering_in_progress"
    for gate, state in (
        ("preregistration", "blocked_preregistration"),
        ("g04", "blocked_g04"),
        ("d07", "blocked_d07"),
    ):
        if _mapping(external.get(gate), gate).get("state") != "ready":
            return state
    return "ready_for_locked_batch"


def _stage_payload(value: StageEvidence) -> Mapping[str, Any]:
    return {
        "stage": value.stage,
        "status": value.status,
        "reason": value.reason,
        "commit_sha256": value.commit_sha256,
        "generation_id": value.generation_id,
        "registry_sha256": list(value.registry_sha256),
        "aggregate_report_sha256": value.aggregate_report_sha256,
        "validation_depth": value.validation_depth,
        "row_level_artifacts_opened": 0,
        "protected_data_access_count": 0,
        **LOCKED_ZERO_COUNTS,
    }


def build_no_locked_checklist(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Build one deterministic aggregate checklist without publishing it."""

    root = workspace_root.resolve(strict=True)
    resolved_config = _config_file(root, config_path)
    config_bytes = _regular_file_bytes(resolved_config, "S108 config")
    config_sha = _sha256_bytes(config_bytes)
    static_before = validate_static_config(
        workspace_root=root,
        config_path=resolved_config,
        config=config,
    )
    reader = AggregateReader(root)
    stages = validate_predecessors_aggregate(reader=reader, config=config)
    expected_hashes = _expected_hash_bindings(stages)
    external = evaluate_external_gates(
        reader=reader,
        config=config,
        expected_hashes=expected_hashes,
    )
    state = _overall_state(stages, external)
    blockers = [
        cast(str, _mapping(external[name], name)["state"])
        for name in ("preregistration", "g04", "d07")
        if _mapping(external[name], name)["state"] != "ready"
    ]
    if state == "engineering_in_progress":
        incomplete = [item.stage for item in stages if item.status != "valid"]
        next_stage = incomplete[0] if incomplete else STAGES[len(stages)]
    elif state == "waiting_s103":
        next_stage = "s103"
    else:
        next_stage = None
    reader.revalidate()
    static_after = validate_static_config(
        workspace_root=root,
        config_path=resolved_config,
        config=config,
    )
    if (
        static_before != static_after
        or _sha256_bytes(
            _regular_file_bytes(resolved_config, "S108 config final check")
        )
        != config_sha
    ):
        raise S108DryRunError("static sources changed before checklist creation")
    accesses = [
        {
            "category": item.category,
            "path": item.path,
            "sha256": item.sha256,
            "size_bytes": item.size_bytes,
        }
        for item in reader.accesses
    ]
    checklist: dict[str, Any] = {
        "schema_version": CHECKLIST_SCHEMA_VERSION,
        "status": "completed_no_locked_dry_run_only",
        "experiment_id": "aihub71490_wavlm_s108_no_locked_dry_run_v1",
        "dry_run_only": True,
        "aggregate_only": True,
        "overall_state": state,
        "next_engineering_stage": next_stage,
        "predecessor_order": list(STAGES),
        "predecessors": [_stage_payload(item) for item in stages],
        "external_gates": external,
        "blockers": blockers,
        "frozen_hash_bindings": (
            dict(expected_hashes) if expected_hashes is not None else None
        ),
        "ready_for_locked_batch": state == "ready_for_locked_batch",
        "locked_batch_launch_allowed_by_this_implementation": False,
        "locked_batch_execution_implemented": False,
        "one_shot_interface_status": "gate_only_not_implemented",
        "research_metrics_generated": False,
        "s108_completion_claim_allowed": False,
        "s108_score_allowed": False,
        "pending_marker_clear_allowed": False,
        "promotion_decision_allowed": False,
        "canonical_s108_commit_created": False,
        "completed_s108_registry_created": False,
        "result_states_reachable_in_dry_run": [],
        "terminal_result_states_forbidden": list(TERMINAL_STATES_FORBIDDEN),
        "access_audit": {
            "static_source_file_count": static_before["source_count"],
            "aggregate_file_read_count": len(accesses),
            "aggregate_files": accesses,
            "row_level_artifact_read_count": 0,
            "protected_data_access_count": 0,
            "raw_audio_access_count": 0,
            "environment_file_access_count": 0,
            **LOCKED_ZERO_COUNTS,
        },
        "sources": {
            "config_path": _workspace_relative(root, resolved_config),
            "config_sha256": config_sha,
            "source_pins": static_before["sources"],
        },
    }
    validate_checklist_document(checklist)
    return checklist


def validate_checklist_document(document: Mapping[str, Any]) -> None:
    """Deeply validate a published no-locked checklist without external writes."""

    _expect(
        document.get("schema_version"), CHECKLIST_SCHEMA_VERSION, "checklist schema"
    )
    _expect(
        document.get("status"),
        "completed_no_locked_dry_run_only",
        "checklist status",
    )
    _expect(document.get("dry_run_only"), True, "dry-run marker")
    _expect(document.get("aggregate_only"), True, "aggregate marker")
    state = document.get("overall_state")
    if state not in ALLOWED_STATES:
        raise S108DryRunError("checklist overall state is invalid")
    predecessors = _list(document.get("predecessors"), "checklist predecessors")
    stage_names = [
        item.get("stage") for item in predecessors if isinstance(item, Mapping)
    ]
    if stage_names != list(STAGES[: len(stage_names)]):
        raise S108DryRunError("checklist predecessor order differs")
    all_valid = len(predecessors) == len(STAGES) and all(
        _mapping(item, "predecessor").get("status") == "valid" for item in predecessors
    )
    for value in predecessors:
        item = _mapping(value, "predecessor")
        _expect(
            item.get("validation_depth"),
            "aggregate_commit_registry_report_only",
            "validation depth",
        )
        _expect(item.get("row_level_artifacts_opened"), 0, "row artifact reads")
        _expect(item.get("protected_data_access_count"), 0, "protected access")
        for key, expected in LOCKED_ZERO_COUNTS.items():
            _expect(item.get(key), expected, f"predecessor {key}")
    external = _mapping(document.get("external_gates"), "external gates")
    if set(external) != {"preregistration", "g04", "d07"}:
        raise S108DryRunError("external gate set differs")
    gates_ready = all(
        _mapping(external[name], name).get("state") == "ready"
        for name in ("preregistration", "g04", "d07")
    )
    expected_ready = all_valid and gates_ready
    _expect(
        document.get("ready_for_locked_batch"),
        expected_ready,
        "ready_for_locked_batch",
    )
    _expect(
        state == "ready_for_locked_batch",
        expected_ready,
        "ready state equivalence",
    )
    expected_blockers = [
        _mapping(external[name], name).get("state")
        for name in ("preregistration", "g04", "d07")
        if _mapping(external[name], name).get("state") != "ready"
    ]
    _expect(document.get("blockers"), expected_blockers, "blocker list")
    for key, expected in (
        ("locked_batch_launch_allowed_by_this_implementation", False),
        ("locked_batch_execution_implemented", False),
        ("research_metrics_generated", False),
        ("s108_completion_claim_allowed", False),
        ("s108_score_allowed", False),
        ("pending_marker_clear_allowed", False),
        ("promotion_decision_allowed", False),
        ("canonical_s108_commit_created", False),
        ("completed_s108_registry_created", False),
    ):
        _expect(document.get(key), expected, key)
    _expect(document.get("result_states_reachable_in_dry_run"), [], "result states")
    _expect(
        document.get("terminal_result_states_forbidden"),
        list(TERMINAL_STATES_FORBIDDEN),
        "forbidden result states",
    )
    audit = _mapping(document.get("access_audit"), "access audit")
    _expect(audit.get("row_level_artifact_read_count"), 0, "row-level reads")
    _expect(audit.get("protected_data_access_count"), 0, "protected reads")
    _expect(audit.get("raw_audio_access_count"), 0, "audio reads")
    _expect(audit.get("environment_file_access_count"), 0, "environment reads")
    files = _list(audit.get("aggregate_files"), "aggregate access ledger")
    _expect(audit.get("aggregate_file_read_count"), len(files), "aggregate read count")
    for key, expected in LOCKED_ZERO_COUNTS.items():
        _expect(audit.get(key), expected, f"access audit {key}")
    forbidden_categories = {
        "row",
        "audio",
        "label",
        "prediction",
        "locked_locator",
    }
    for value in files:
        item = _mapping(value, "aggregate access")
        if item.get("category") in forbidden_categories:
            raise S108DryRunError("non-aggregate access appeared in checklist")
        _relative_string(item.get("path"), "aggregate access path")
        _sha256(item.get("sha256"), "aggregate access SHA-256")
        _integer(item.get("size_bytes"), "aggregate access size", minimum=1)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_cleanup_temporary(path: Path, expected_parent: Path) -> None:
    if path.parent != expected_parent or not path.name.startswith(".part-"):
        raise S108PublicationError("refused unsafe temporary cleanup")
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise S108PublicationError("temporary generation has unsafe node type")
    entries = list(path.iterdir())
    for child in entries:
        if child.is_symlink() or not child.is_file():
            raise S108PublicationError("temporary generation contains unsafe node")
        child.unlink()
    path.rmdir()


def _ensure_directory(path: Path, root: Path, name: str) -> None:
    relative = _workspace_relative(root, path)
    _reject_symlink_chain(root, relative, name)
    path.mkdir(parents=True, exist_ok=True)
    _reject_symlink_chain(root, relative, name)
    if not path.is_dir() or path.is_symlink():
        raise S108PublicationError(f"{name} is not a safe directory")


def _publish_new_or_identical(path: Path, data: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".part-{uuid4().hex}"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
            created = True
        except FileExistsError:
            existing = _regular_file_bytes(path, f"existing {path.name}")
            if existing != data:
                raise S108PublicationError(f"refused to overwrite {path}")
            created = False
        finally:
            temporary.unlink(missing_ok=True)
        _fsync_directory(path.parent)
        return created
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def publish_no_locked_checklist(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    checklist: Mapping[str, Any],
    fault_injection: str | None = None,
) -> PublishedDryRun:
    """Atomically publish one content-addressed aggregate-only snapshot."""

    allowed_faults = {
        None,
        "after_temporary_write",
        "before_generation_publish",
        "after_generation_publish",
        "before_receipt_publish",
    }
    if fault_injection not in allowed_faults:
        raise S108PublicationError("unknown S108 fault-injection point")
    root = workspace_root.resolve(strict=True)
    resolved_config = _config_file(root, config_path)
    validate_static_config(
        workspace_root=root,
        config_path=resolved_config,
        config=config,
    )
    validate_checklist_document(checklist)
    outputs = _mapping(config["outputs"], "outputs")
    generation_root = _secure_output(
        root, outputs["generation_root"], "generation root"
    )
    receipt_root = _secure_output(root, outputs["receipt_root"], "receipt root")
    _ensure_directory(generation_root, root, "generation root")
    _ensure_directory(receipt_root, root, "receipt root")
    checklist_bytes = canonical_json_bytes(checklist, pretty=True)
    artifact_record = {
        "name": "no_locked_checklist",
        "path": "no_locked_checklist.json",
        "sha256": _sha256_bytes(checklist_bytes),
        "size_bytes": len(checklist_bytes),
        "format": "json",
        "access_scope": "A1_aggregate",
    }
    generation_id = _sha256_bytes(
        canonical_json_bytes(
            {
                "schema_version": CHECKLIST_SCHEMA_VERSION,
                "artifacts": [artifact_record],
            },
            pretty=False,
        )
    )
    destination = generation_root / generation_id
    temporary = generation_root / f".part-{uuid4().hex}"
    created_generation = False
    try:
        temporary.mkdir(mode=0o700)
        checklist_path = temporary / "no_locked_checklist.json"
        descriptor = os.open(
            checklist_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(checklist_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(temporary)
        if fault_injection == "after_temporary_write":
            raise S108PublicationError("injected fault after temporary write")
        if (
            _regular_file_bytes(checklist_path, "temporary checklist")
            != checklist_bytes
        ):
            raise S108PublicationError("temporary checklist differs")
        if fault_injection == "before_generation_publish":
            raise S108PublicationError("injected fault before generation publish")
        try:
            os.rename(temporary, destination)
            created_generation = True
            _fsync_directory(generation_root)
        except OSError as exc:
            if exc.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                raise
            existing = destination / "no_locked_checklist.json"
            if (
                destination.is_symlink()
                or not destination.is_dir()
                or list(item.name for item in destination.iterdir())
                != ["no_locked_checklist.json"]
                or _regular_file_bytes(existing, "existing checklist")
                != checklist_bytes
            ):
                raise S108PublicationError(
                    "content-addressed generation differs"
                ) from exc
            _safe_cleanup_temporary(temporary, generation_root)
        if fault_injection == "after_generation_publish":
            raise S108PublicationError("injected fault after generation publish")
        generation_checklist = destination / "no_locked_checklist.json"
        if (
            _regular_file_bytes(generation_checklist, "published checklist")
            != checklist_bytes
        ):
            raise S108PublicationError("published checklist differs")
        receipt: dict[str, Any] = {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "status": "published_no_locked_dry_run_only",
            "generation_id": generation_id,
            "generation_path": _workspace_relative(root, destination),
            "artifact": artifact_record,
            "config": {
                "path": _workspace_relative(root, resolved_config),
                "sha256": _sha256_bytes(
                    _regular_file_bytes(resolved_config, "S108 config")
                ),
            },
            "overall_state": checklist["overall_state"],
            "ready_for_locked_batch": checklist["ready_for_locked_batch"],
            "s108_completion_claim_allowed": False,
            "s108_score_allowed": False,
            "canonical_s108_commit_created": False,
            **LOCKED_ZERO_COUNTS,
        }
        receipt["binding_sha256"] = _binding_sha256(receipt)
        receipt_path = receipt_root / f"{generation_id}.json"
        if fault_injection == "before_receipt_publish":
            raise S108PublicationError("injected fault before receipt publish")
        created_receipt = _publish_new_or_identical(
            receipt_path,
            canonical_json_bytes(receipt, pretty=True),
        )
    except BaseException:
        _safe_cleanup_temporary(temporary, generation_root)
        raise
    return PublishedDryRun(
        generation_id=generation_id,
        generation_path=destination,
        receipt_path=receipt_path,
        checklist_sha256=cast(str, artifact_record["sha256"]),
        checklist=checklist,
        created_generation=created_generation,
        created_receipt=created_receipt,
    )


def validate_dry_run_receipt(
    *,
    workspace_root: Path,
    config_path: Path,
    receipt_path: Path,
) -> Mapping[str, Any]:
    """Read-only deep validator for one append-only S108 dry-run receipt."""

    root = workspace_root.resolve(strict=True)
    resolved_config = _config_file(root, config_path)
    config = load_config(resolved_config)
    validate_static_config(
        workspace_root=root,
        config_path=resolved_config,
        config=config,
    )
    if receipt_path.is_absolute():
        try:
            relative_receipt = receipt_path.relative_to(root).as_posix()
        except ValueError as exc:
            raise S108DryRunError("receipt escaped the workspace") from exc
        relative_receipt = _relative_string(relative_receipt, "receipt path")
    else:
        relative_receipt = _relative_string(str(receipt_path), "receipt path")
    receipt_file = _secure_file(root, relative_receipt, "receipt", must_exist=True)
    receipt_bytes = _regular_file_bytes(receipt_file, "receipt")
    receipt = _parse_json(receipt_bytes, "receipt")
    expected_keys = {
        "schema_version",
        "status",
        "generation_id",
        "generation_path",
        "artifact",
        "config",
        "overall_state",
        "ready_for_locked_batch",
        "s108_completion_claim_allowed",
        "s108_score_allowed",
        "canonical_s108_commit_created",
        *LOCKED_ZERO_COUNTS,
        "binding_sha256",
    }
    if set(receipt) != expected_keys:
        raise S108DryRunError("receipt schema differs")
    _expect(receipt.get("schema_version"), RECEIPT_SCHEMA_VERSION, "receipt schema")
    _expect(
        receipt.get("status"),
        "published_no_locked_dry_run_only",
        "receipt status",
    )
    generation_id = _sha256(receipt.get("generation_id"), "generation ID")
    _expect(
        receipt.get("binding_sha256"),
        _binding_sha256(receipt),
        "receipt binding",
    )
    for key, expected in (
        ("s108_completion_claim_allowed", False),
        ("s108_score_allowed", False),
        ("canonical_s108_commit_created", False),
    ):
        _expect(receipt.get(key), expected, key)
    for key, expected_count in LOCKED_ZERO_COUNTS.items():
        _expect(receipt.get(key), expected_count, f"receipt {key}")
    outputs = _mapping(config["outputs"], "outputs")
    generation_root = _secure_output(
        root, outputs["generation_root"], "generation root"
    )
    generation_path = _secure_output(root, receipt.get("generation_path"), "generation")
    if (
        generation_path.parent != generation_root
        or generation_path.name != generation_id
        or generation_path.is_symlink()
        or not generation_path.is_dir()
    ):
        raise S108DryRunError("receipt generation path differs")
    entries = list(generation_path.iterdir())
    if (
        len(entries) != 1
        or entries[0].name != "no_locked_checklist.json"
        or entries[0].is_symlink()
        or not entries[0].is_file()
    ):
        raise S108DryRunError("generation exact layout differs")
    artifact = _mapping(receipt.get("artifact"), "receipt artifact")
    if set(artifact) != {
        "name",
        "path",
        "sha256",
        "size_bytes",
        "format",
        "access_scope",
    }:
        raise S108DryRunError("receipt artifact schema differs")
    _expect(artifact.get("name"), "no_locked_checklist", "artifact name")
    _expect(artifact.get("path"), "no_locked_checklist.json", "artifact path")
    checklist_bytes = _regular_file_bytes(entries[0], "published checklist")
    _expect(_sha256_bytes(checklist_bytes), artifact.get("sha256"), "artifact SHA-256")
    _expect(len(checklist_bytes), artifact.get("size_bytes"), "artifact size")
    expected_generation = _sha256_bytes(
        canonical_json_bytes(
            {
                "schema_version": CHECKLIST_SCHEMA_VERSION,
                "artifacts": [artifact],
            },
            pretty=False,
        )
    )
    _expect(generation_id, expected_generation, "generation identity")
    checklist = _parse_json(checklist_bytes, "published checklist")
    validate_checklist_document(checklist)
    _expect(
        receipt.get("overall_state"),
        checklist.get("overall_state"),
        "receipt state",
    )
    _expect(
        receipt.get("ready_for_locked_batch"),
        checklist.get("ready_for_locked_batch"),
        "receipt readiness",
    )
    config_record = _mapping(receipt.get("config"), "receipt config")
    _expect(
        config_record.get("path"),
        _workspace_relative(root, resolved_config),
        "receipt config path",
    )
    receipt_config_sha256 = _sha256(
        config_record.get("sha256"),
        "receipt config SHA-256",
    )
    current_config_sha256 = _sha256_bytes(
        _regular_file_bytes(resolved_config, "S108 config")
    )
    historical_snapshot = receipt_config_sha256 != current_config_sha256
    if historical_snapshot and receipt_config_sha256 not in HISTORICAL_CONFIG_SHA256S:
        raise S108DryRunError("receipt config SHA-256 is not current or historical")
    return {
        "status": "valid",
        "generation_id": generation_id,
        "receipt_sha256": _sha256_bytes(receipt_bytes),
        "checklist_sha256": artifact["sha256"],
        "overall_state": checklist["overall_state"],
        "ready_for_locked_batch": checklist["ready_for_locked_batch"],
        "s108_completion_claim_allowed": False,
        "s108_score_allowed": False,
        "historical_snapshot": historical_snapshot,
        "row_level_artifact_read_count": 0,
        "protected_data_access_count": 0,
        **LOCKED_ZERO_COUNTS,
    }


def run_no_locked_dry_run(
    *,
    workspace_root: Path,
    config_path: Path,
    fault_injection: str | None = None,
) -> PublishedDryRun:
    root = workspace_root.resolve(strict=True)
    resolved_config = _config_file(root, config_path)
    config = load_config(resolved_config)
    checklist = build_no_locked_checklist(
        workspace_root=root,
        config_path=resolved_config,
        config=config,
    )
    return publish_no_locked_checklist(
        workspace_root=root,
        config_path=resolved_config,
        config=config,
        checklist=checklist,
        fault_injection=fault_injection,
    )


__all__ = [
    "ALLOWED_STATES",
    "AggregateAccess",
    "AggregateReader",
    "CHECKLIST_SCHEMA_VERSION",
    "LOCKED_ZERO_COUNTS",
    "PublishedDryRun",
    "RECEIPT_SCHEMA_VERSION",
    "S108DryRunError",
    "S108PublicationError",
    "StageEvidence",
    "build_no_locked_checklist",
    "canonical_json_bytes",
    "evaluate_external_gates",
    "load_config",
    "publish_no_locked_checklist",
    "run_no_locked_dry_run",
    "validate_checklist_document",
    "validate_dry_run_receipt",
    "validate_predecessors_aggregate",
    "validate_static_config",
]
