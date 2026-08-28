"""Fail-closed preregistration and CPU preflight for WP4 S106 fusion.

The config-only path validates static policy and pinned source hashes.  The
normal preflight deliberately validates the completed S104 evidence chain and
the finalized S105 adoption decision *before* it resolves or reads any stack
locator.  It never opens calibration or locked-test data, imports torch,
initializes CUDA, fits a fusion model, or reports a stack metric.

The pure guards in this module are also consumed by the future S106 runner:
exact feature allowlisting, label/realization leakage rejection, stable
speaker-fold assignment, OOF coverage, and hash-keyed component joins.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, cast
from uuid import uuid4

import yaml  # type: ignore[import-untyped]


CONFIG_SCHEMA_VERSION = 1
EXECUTION_LOCK_SCHEMA_VERSION = "voice_coach.wavlm_s106_execution_lock.v2"
S104_COMMIT_SCHEMA_VERSION = "voice_coach.wavlm_s104_adapter_commit.v1"
S104_REPORT_SCHEMA_VERSION = "voice_coach.wavlm_s104_adapter_report.v1"
S104_CONFIG_PATH = "configs/experiments/wavlm_s104_adapter_v2_current_code.yaml"
S104_CONFIG_SHA256 = "7a78e718954c45eb3ad03658b6da916a5c1163a51d683125daccd6c272be6434"
S104_VALIDATOR_MODULE_PATH = "src/voice_coach/training/wavlm_adapter_training.py"
S104_VALIDATOR_MODULE_SHA256 = (
    "a7b41d507385c7f45b102f2d1603ad4214f0247ad9e0e08c6a3640f2df6bed80"
)
S105_COMMIT_SCHEMA_VERSION = "voice_coach.wavlm_s105_hierarchy_commit.v1"
S105_REPORT_SCHEMA_VERSION = "voice_coach.wavlm_s105_hierarchy_comparison.v1"
S105_CONFIG_PATH = "configs/experiments/wavlm_s105_hierarchy_v5_stream_cardinality.yaml"
S105_CONFIG_SHA256 = "9805384e147aec2741cdfd3d97ecd3452c7c2c60dc26e0141f2ac4415ae39443"
S105_VALIDATOR_MODULE_PATH = "src/voice_coach/training/wavlm_hierarchy_training.py"
S105_VALIDATOR_MODULE_SHA256 = (
    "e2f7269c757ddee82f33de6787df466b29cbe9e1e2b1fed90130eaaec1c53ad1"
)
B07_COMMIT_SCHEMA_VERSION = "voice_coach.b07_gop_commit.v1"
B07_REGISTRY_SCHEMA_VERSION = "voice_coach.b07_gop_registry.v1"
B07_CONFIG_PATH = "configs/experiments/b07_gop_acoustic_posterior_v1.yaml"
B07_CONFIG_SHA256 = "9a2070ab1fa7e00009657d824cf41fae8adfde4654516419951855322413bdb0"
B07_COMMIT_SHA256 = "ff31a88e694d23c89996193646d7c062069c9e776b5ed72ef7fc354950f3907c"
B07_REGISTRY_SHA256 = "5db35282cab879b3a05fa5a6b5cd9a5cac00c5469215ffc77a6dc34abf95eb12"
B07_GENERATION_ID = "bc1a7eb174c6a8f69627292f30c9229235dcde8e10e69a7e07899847986d6e09"
B07_VALIDATOR_MODULE_PATH = "src/voice_coach/training/b07_gop_training.py"
B07_VALIDATOR_MODULE_SHA256 = (
    "891389afb3bcaca18ec13d077d4ecf193e3876a04e25f96cc034e7db54b387ea"
)
COMPONENTS = (
    "explicit",
    "s104_adapter",
    "s105_selected_hierarchy",
    "gop_acoustic_posterior",
)
SEEDS = (42, 43, 44)
ALLOWED_SPLITS = ("stack",)
FORBIDDEN_SPLITS = ("train", "dev", "calibration", "locked_test")
OOF_FOLD_COUNT = 5
OOF_NAMESPACE = "voice-coach.aihub71490.s106-stack-fold.v1"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PYTHON_SYMBOL_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*\.[A-Za-z_][A-Za-z0-9_]*$")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
EXACT_FEATURES = (
    "explicit__probability_mean",
    "explicit__logit_from_mean_probability",
    "explicit__margin_abs_2p_minus_1",
    "explicit__entropy_binary",
    "explicit__seed_probability_variance_population",
    "explicit__missing",
    "s104_adapter__probability_mean",
    "s104_adapter__logit_from_mean_probability",
    "s104_adapter__margin_abs_2p_minus_1",
    "s104_adapter__entropy_binary",
    "s104_adapter__seed_probability_variance_population",
    "s104_adapter__missing",
    "s105_selected_hierarchy__probability_mean",
    "s105_selected_hierarchy__logit_from_mean_probability",
    "s105_selected_hierarchy__margin_abs_2p_minus_1",
    "s105_selected_hierarchy__entropy_binary",
    "s105_selected_hierarchy__seed_probability_variance_population",
    "s105_selected_hierarchy__missing",
    "gop_acoustic_posterior__probability_mean",
    "gop_acoustic_posterior__logit_from_mean_probability",
    "gop_acoustic_posterior__margin_abs_2p_minus_1",
    "gop_acoustic_posterior__entropy_binary",
    "gop_acoustic_posterior__seed_probability_variance_population",
    "gop_acoustic_posterior__missing",
    "alignment__target_interval_confidence",
    "alignment__confidence_missing",
)
FORBIDDEN_EXACT_FEATURES = frozenset(
    {
        "reference_binary_error_label",
        "binary_error_label",
        "y",
        "y_true",
        "target_phone",
        "realized_phone",
        "realized_phoneme",
        "alignment_operation",
        "phone_alignment_operation",
        "error_tag",
        "error_type",
        "substitution_tag",
        "deletion_tag",
        "insertion_tag",
    }
)
FORBIDDEN_FEATURE_FRAGMENTS = (
    "label",
    "ground_truth",
    "realized",
    "operation",
    "error_tag",
    "error_type",
    "substitution",
    "deletion",
    "insertion",
)
ALLOWED_FEATURE_SEMANTICS = (
    "component_probability",
    "component_logit",
    "component_margin",
    "component_entropy",
    "preregistered_seed_uncertainty",
    "component_missingness",
    "target_interval_alignment_confidence",
    "alignment_confidence_missingness",
)
REPORTING_ALLOWED_EVIDENCE = (
    "oof_stack_predictions",
    "oof_aggregate_metrics",
    "component_controls",
    "component_disagreement",
    "missingness_summary",
    "selected_hyperparameters",
    "final_checkpoint_hash",
)
ARTIFACT_LAYOUT = (
    (
        "stack_component_matrix",
        "stack_component_matrix.parquet",
        "parquet",
        "D1_restricted_internal",
    ),
    (
        "speaker_fold_manifest",
        "speaker_fold_manifest.json",
        "json",
        "D1_restricted_internal",
    ),
    (
        "oof_predictions",
        "oof_predictions.parquet",
        "parquet",
        "D1_restricted_internal",
    ),
    (
        "oof_aggregate_report",
        "oof_aggregate_report.json",
        "json",
        "A1_aggregate",
    ),
    (
        "component_disagreement_report",
        "component_disagreement_report.json",
        "json",
        "A1_aggregate",
    ),
    (
        "final_fusion_checkpoint",
        "final_fusion_checkpoint.json",
        "json",
        "D1_restricted_internal",
    ),
    (
        "stack_only_report",
        "stack_only_report.json",
        "json",
        "A1_aggregate",
    ),
)


class S106ContractError(ValueError):
    """Raised before protected access when the S106 contract is not satisfied."""


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: yaml.SafeLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    output: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in output:
            raise S106ContractError(f"YAML contains duplicate key: {key!r}")
        output[key] = loader.construct_object(value_node, deep=deep)
    return output


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True, slots=True)
class PredecessorEvidence:
    stage: str
    commit_path: str
    commit_sha256: str
    generation_id: str
    report_path: str
    report_sha256: str
    selected_variant: str
    adoption_status: str | None
    registry_path: str | None = None
    registry_sha256: str | None = None
    projection_checkpoints: tuple[Mapping[str, Any], ...] = ()
    deep_audit: Mapping[str, Any] | None = None
    canonical_sources: Mapping[str, Mapping[str, Any]] | None = None


@dataclass(frozen=True, slots=True)
class StackSource:
    path: str
    sha256: str
    size_bytes: int
    split_value: str


ValidatorInvoker = Callable[
    [str, Path, Path],
    Mapping[str, Any],
]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise S106ContractError(f"{name} must be a mapping")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise S106ContractError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise S106ContractError(f"{name} must be a non-empty string")
    return value


def _integer(value: object, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise S106ContractError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise S106ContractError(f"{name} must be at least {minimum}")
    return value


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise S106ContractError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise S106ContractError(f"{name} must be finite")
    return result


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise S106ContractError(f"{name} must be boolean")
    return value


def _sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise S106ContractError(f"{name} must be a lowercase SHA-256")
    return value


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise S106ContractError(f"{name} must equal {expected!r}")


def _exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    name: str,
) -> None:
    if set(value) != expected:
        raise S106ContractError(f"{name} keys differ: expected {sorted(expected)!r}")


def _reject_nonfinite_tree(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise S106ContractError(f"{name} contains a non-finite number")
    if isinstance(value, Mapping):
        for nested in value.values():
            _reject_nonfinite_tree(nested, name)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_nonfinite_tree(nested, name)


def _json_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise S106ContractError(f"JSON contains duplicate key: {key!r}")
        output[key] = value
    return output


def load_json_strict(path: Path, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_json_object_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                S106ContractError(f"{name} contains {token}")
            ),
        )
    except S106ContractError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise S106ContractError(f"{name} is not valid UTF-8 JSON") from error
    _reject_nonfinite_tree(value, name)
    return _mapping(value, name)


def load_config(path: Path) -> dict[str, Any]:
    try:
        document = yaml.load(
            path.read_text(encoding="utf-8"),
            Loader=_UniqueKeyLoader,
        )
    except S106ContractError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise S106ContractError("S106 config is not valid UTF-8 YAML") from error
    return dict(_mapping(document, "S106 config"))


def sha256_file(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise S106ContractError(f"required regular file is absent: {path}")
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    after = path.stat()
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
        raise S106ContractError(f"file changed while hashing: {path}")
    return digest.hexdigest()


def canonical_json_bytes(
    value: Mapping[str, Any],
    *,
    pretty: bool,
) -> bytes:
    options: dict[str, Any] = {
        "ensure_ascii": False,
        "sort_keys": True,
        "allow_nan": False,
    }
    if pretty:
        options["indent"] = 2
    else:
        options["separators"] = (",", ":")
    return (json.dumps(value, **options) + "\n").encode("utf-8")


def _relative_path(value: object, name: str) -> str:
    text = _string(value, name)
    path = Path(text)
    if (
        path.is_absolute()
        or text != path.as_posix()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(part.startswith(".env") for part in path.parts)
    ):
        raise S106ContractError(f"{name} must be a safe workspace-relative path")
    return text


def _reject_symlink_chain(
    workspace_root: Path,
    relative: str,
    name: str,
) -> Path:
    candidate = workspace_root / relative
    current = workspace_root
    for part in Path(relative).parts:
        current /= part
        if current.is_symlink():
            raise S106ContractError(f"{name} cannot traverse a symlink")
    return candidate


def _resolve_file(
    workspace_root: Path,
    value: object,
    name: str,
) -> Path:
    relative = _relative_path(value, name)
    unresolved = _reject_symlink_chain(workspace_root, relative, name)
    candidate = unresolved.resolve(strict=True)
    try:
        candidate.relative_to(workspace_root)
    except ValueError as error:
        raise S106ContractError(f"{name} escapes the workspace") from error
    if not candidate.is_file() or candidate.is_symlink():
        raise S106ContractError(f"{name} is not a regular file")
    return candidate


def _resolve_output(
    workspace_root: Path,
    value: object,
    name: str,
) -> Path:
    relative = _relative_path(value, name)
    unresolved = _reject_symlink_chain(workspace_root, relative, name)
    candidate = unresolved.resolve(strict=False)
    try:
        candidate.relative_to(workspace_root)
    except ValueError as error:
        raise S106ContractError(f"{name} escapes the workspace") from error
    if candidate.exists() and (not candidate.is_file() or candidate.is_symlink()):
        raise S106ContractError(f"{name} must be a regular file")
    return candidate


def _workspace_relative(workspace_root: Path, path: Path) -> str:
    try:
        return path.relative_to(workspace_root).as_posix()
    except ValueError as error:
        raise S106ContractError("path is outside the workspace") from error


def validate_feature_columns(columns: Sequence[str]) -> None:
    """Require the exact preregistered feature sequence and reject label leakage."""

    if tuple(columns) != EXACT_FEATURES:
        raise S106ContractError(
            "fusion feature columns differ from the exact allowlist"
        )
    for column in columns:
        normalized = column.casefold()
        if normalized in FORBIDDEN_EXACT_FEATURES or any(
            fragment in normalized for fragment in FORBIDDEN_FEATURE_FRAGMENTS
        ):
            raise S106ContractError(f"forbidden fusion feature: {column}")


def assert_s106_split(split: str) -> None:
    """Allow only stack rows in S106."""

    if split != "stack":
        raise S106ContractError(f"S106 cannot access split: {split}")


def aggregate_component_probabilities(
    seed_probabilities: Mapping[int, float],
) -> Mapping[str, float]:
    """Create the frozen six-value component summary before any stack join."""

    if not seed_probabilities:
        return {
            "probability_mean": 0.5,
            "logit_from_mean_probability": 0.0,
            "margin_abs_2p_minus_1": 0.0,
            "entropy_binary": math.log(2.0),
            "seed_probability_variance_population": 0.0,
            "missing": 1.0,
        }
    if set(seed_probabilities) != set(SEEDS):
        raise S106ContractError("component seed set differs")
    probabilities = [
        _finite(seed_probabilities[seed], f"seed {seed} probability") for seed in SEEDS
    ]
    if any(value < 0.0 or value > 1.0 for value in probabilities):
        raise S106ContractError("component probability is outside [0, 1]")
    raw_mean = sum(probabilities) / len(probabilities)
    probability = min(max(raw_mean, 0.000001), 0.999999)
    variance = sum(
        (value - raw_mean) * (value - raw_mean) for value in probabilities
    ) / len(probabilities)
    logit = math.log(probability / (1.0 - probability))
    entropy = -(
        probability * math.log(probability)
        + (1.0 - probability) * math.log(1.0 - probability)
    )
    return {
        "probability_mean": probability,
        "logit_from_mean_probability": logit,
        "margin_abs_2p_minus_1": abs(2.0 * probability - 1.0),
        "entropy_binary": entropy,
        "seed_probability_variance_population": variance,
        "missing": 0.0,
    }


def alignment_confidence_features(
    confidence: float | None,
) -> Mapping[str, float]:
    """Encode only preregistered target-interval confidence and missingness."""

    if confidence is None:
        return {
            "target_interval_confidence": 0.0,
            "confidence_missing": 1.0,
        }
    value = _finite(confidence, "alignment confidence")
    if value < 0.0 or value > 1.0:
        raise S106ContractError("alignment confidence is outside [0, 1]")
    return {
        "target_interval_confidence": value,
        "confidence_missing": 0.0,
    }


def assign_speaker_fold(
    speaker_id_sha256: str,
    *,
    fold_count: int = OOF_FOLD_COUNT,
    namespace: str = OOF_NAMESPACE,
) -> int:
    """Assign one stable fold from a namespaced speaker hash only."""

    if not SHA256_PATTERN.fullmatch(speaker_id_sha256):
        raise S106ContractError("speaker_id_sha256 is invalid")
    if fold_count < 2:
        raise S106ContractError("fold_count must be at least two")
    if namespace != OOF_NAMESPACE:
        raise S106ContractError("OOF namespace differs from preregistration")
    digest = hashlib.sha256(
        f"{namespace}\0{speaker_id_sha256}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big") % fold_count


def validate_oof_assignments(
    rows: Iterable[Mapping[str, Any]],
    *,
    fold_count: int = OOF_FOLD_COUNT,
    namespace: str = OOF_NAMESPACE,
) -> Mapping[str, Any]:
    """Validate stable speaker-disjoint folds and exact row-level OOF coverage."""

    seen_rows: set[tuple[str, int]] = set()
    speaker_folds: dict[str, int] = {}
    fold_rows = {fold: 0 for fold in range(fold_count)}
    fold_speakers: dict[int, set[str]] = {fold: set() for fold in range(fold_count)}
    for index, row in enumerate(rows):
        assert_s106_split(_string(row.get("split"), f"rows[{index}].split"))
        utterance = _sha256(
            row.get("utterance_id_sha256"),
            f"rows[{index}].utterance_id_sha256",
        )
        speaker = _sha256(
            row.get("speaker_id_sha256"),
            f"rows[{index}].speaker_id_sha256",
        )
        phone_index = _integer(
            row.get("phone_index"),
            f"rows[{index}].phone_index",
            minimum=0,
        )
        fold = _integer(row.get("fold"), f"rows[{index}].fold", minimum=0)
        if fold >= fold_count:
            raise S106ContractError("OOF fold is out of range")
        expected_fold = assign_speaker_fold(
            speaker,
            fold_count=fold_count,
            namespace=namespace,
        )
        if fold != expected_fold:
            raise S106ContractError("OOF row differs from stable speaker assignment")
        previous = speaker_folds.setdefault(speaker, fold)
        if previous != fold:
            raise S106ContractError("one speaker appears in multiple OOF folds")
        row_key = (utterance, phone_index)
        if row_key in seen_rows:
            raise S106ContractError("OOF row key is duplicated")
        seen_rows.add(row_key)
        fold_rows[fold] += 1
        fold_speakers[fold].add(speaker)
    if not seen_rows:
        raise S106ContractError("OOF assignment is empty")
    all_speakers = set(speaker_folds)
    for fold in range(fold_count):
        validation_speakers = fold_speakers[fold]
        training_speakers = all_speakers - validation_speakers
        if (
            fold_rows[fold] == 0
            or not validation_speakers
            or not training_speakers
            or validation_speakers & training_speakers
        ):
            raise S106ContractError("OOF fold lacks a disjoint nonempty partition")
    return {
        "row_count": len(seen_rows),
        "speaker_count": len(all_speakers),
        "fold_rows": {str(key): value for key, value in fold_rows.items()},
        "fold_speakers": {str(key): len(value) for key, value in fold_speakers.items()},
        "speaker_overlap_maximum": 0,
        "every_row_exactly_one_oof_prediction": True,
    }


def validate_component_join(
    stack_rows: Mapping[tuple[str, int], str],
    component_rows: Mapping[str, Mapping[tuple[str, int], str]],
) -> Mapping[str, Any]:
    """Validate hash-key joins without exposing labels to the feature builder."""

    if tuple(component_rows) != COMPONENTS:
        raise S106ContractError("component order differs")
    for (utterance, phone_index), speaker in stack_rows.items():
        _sha256(utterance, "stack utterance_id_sha256")
        _integer(phone_index, "stack phone_index", minimum=0)
        _sha256(speaker, "stack speaker_id_sha256")
    if not stack_rows:
        raise S106ContractError("stack join universe is empty")
    missing: dict[str, int] = {}
    for component, rows in component_rows.items():
        unknown = set(rows) - set(stack_rows)
        if unknown:
            raise S106ContractError(f"{component} contains a non-stack row")
        for key, speaker in rows.items():
            _sha256(speaker, f"{component} speaker_id_sha256")
            if speaker != stack_rows[key]:
                raise S106ContractError(f"{component} speaker binding differs")
        missing[component] = len(stack_rows) - len(rows)
    return {
        "stack_row_count": len(stack_rows),
        "component_row_counts": {
            component: len(rows) for component, rows in component_rows.items()
        },
        "component_missing_rows": missing,
        "join_keys": ["utterance_id_sha256", "phone_index"],
        "speaker_key": "speaker_id_sha256",
        "labels_visible_to_feature_builder": False,
    }


def validate_config(config: Mapping[str, Any]) -> None:
    """Validate every static preregistration choice without protected access."""

    _exact_keys(
        config,
        {
            "schema_version",
            "experiment",
            "predecessors",
            "explicit_component",
            "components",
            "inputs",
            "data",
            "stack_projection",
            "seed_aggregation",
            "feature_contract",
            "oof",
            "reporting",
            "runtime",
            "implementation",
            "outputs",
        },
        "S106 config",
    )
    _expect(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "schema_version")

    experiment = _mapping(config["experiment"], "experiment")
    _expect(
        experiment.get("id"),
        "aihub71490_wavlm_s106_stack_fusion_v1",
        "experiment.id",
    )
    _expect(
        experiment.get("stage"),
        "s106_stack_only_confidence_aware_fusion",
        "experiment.stage",
    )
    _expect(experiment.get("task"), "phone_binary_error", "experiment.task")
    for name in (
        "stack_only",
        "development_only",
        "oracle_alignment",
        "silver_label",
    ):
        _expect(experiment.get(name), True, f"experiment.{name}")
    for name in ("promotion_decision_allowed", "gpu_training_allowed_in_preflight"):
        _expect(experiment.get(name), False, f"experiment.{name}")

    predecessors = _mapping(config["predecessors"], "predecessors")
    _expect(
        predecessors.get("validation_order"),
        ["s104", "s105", "b07"],
        "predecessors.validation_order",
    )
    _expect(
        predecessors.get("resolve_stack_only_after_all_valid"),
        True,
        "predecessors.resolve_stack_only_after_all_valid",
    )
    deep_audit = _mapping(
        predecessors.get("deep_audit_access"),
        "predecessors.deep_audit_access",
    )
    _expect(
        deep_audit.get("public_validator_call_order"),
        ["s104_direct", "s105_with_nested_s104", "b07"],
        "predecessors.deep_audit_access.public_validator_call_order",
    )
    _expect(
        _mapping(
            deep_audit.get("validator_invocation_counts"),
            "predecessors.deep_audit_access.validator_invocation_counts",
        ),
        {"s104": 2, "s105": 1, "b07": 1},
        "predecessors.deep_audit_access.validator_invocation_counts",
    )
    _expect(
        _mapping(
            deep_audit.get("s104"),
            "predecessors.deep_audit_access.s104",
        ),
        {
            "fit_metadata_read": True,
            "dev_truth_read": True,
            "per_seed_dev_predictions_read": True,
        },
        "predecessors.deep_audit_access.s104",
    )
    _expect(
        _mapping(
            deep_audit.get("s105"),
            "predecessors.deep_audit_access.s105",
        ),
        {
            "manifest_reservation_labels_read": True,
            "dev_predictions_read": True,
            "nested_s104_full_validator": True,
        },
        "predecessors.deep_audit_access.s105",
    )
    _expect(
        _mapping(
            deep_audit.get("b07"),
            "predecessors.deep_audit_access.b07",
        ),
        {
            "canonical_train_read": True,
            "canonical_dev_read": True,
            "stored_dev_predictions_read": True,
            "full_train_selection_dev_recomputed_required": True,
        },
        "predecessors.deep_audit_access.b07",
    )
    _expect(
        deep_audit.get("access_basis"),
        "pinned_public_validator_contract_inspection",
        "predecessors.deep_audit_access.access_basis",
    )
    s104 = _mapping(predecessors.get("s104"), "predecessors.s104")
    s105 = _mapping(predecessors.get("s105"), "predecessors.s105")
    b07 = _mapping(predecessors.get("b07"), "predecessors.b07")
    for stage, value, commit_schema, report_key, report_schema in (
        (
            "s104",
            s104,
            S104_COMMIT_SCHEMA_VERSION,
            "report_schema_version",
            S104_REPORT_SCHEMA_VERSION,
        ),
        (
            "s105",
            s105,
            S105_COMMIT_SCHEMA_VERSION,
            "comparison_report_schema_version",
            S105_REPORT_SCHEMA_VERSION,
        ),
    ):
        _relative_path(value.get("config"), f"predecessors.{stage}.config")
        _relative_path(
            value.get("commit_manifest"),
            f"predecessors.{stage}.commit_manifest",
        )
        _expect(
            value.get("commit_schema_version"),
            commit_schema,
            f"predecessors.{stage}.commit_schema_version",
        )
        _expect(
            value.get(report_key),
            report_schema,
            f"predecessors.{stage}.{report_key}",
        )
        validator = _string(
            value.get("independent_validator"),
            f"predecessors.{stage}.independent_validator",
        )
        if not PYTHON_SYMBOL_PATTERN.fullmatch(validator):
            raise S106ContractError(f"predecessors.{stage} validator is invalid")
        _expect(
            value.get("required_validator_status"),
            "valid",
            f"predecessors.{stage}.required_validator_status",
        )
        _expect(
            value.get("required_commit_status"),
            "committed",
            f"predecessors.{stage}.required_commit_status",
        )
        _expect(
            value.get("required_report_status"),
            "completed",
            f"predecessors.{stage}.required_report_status",
        )
    _expect(
        s104.get("required_selected_candidate"),
        "base_plus",
        "predecessors.s104.required_selected_candidate",
    )
    _expect(
        _sha256(
            s104.get("config_sha256"),
            "predecessors.s104.config_sha256",
        ),
        S104_CONFIG_SHA256,
        "predecessors.s104.config_sha256",
    )
    _expect(
        _relative_path(
            s104.get("config"),
            "predecessors.s104.config",
        ),
        S104_CONFIG_PATH,
        "predecessors.s104.config",
    )
    s104_validator_module = _mapping(
        s104.get("validator_module"),
        "predecessors.s104.validator_module",
    )
    _expect(
        _relative_path(
            s104_validator_module.get("path"),
            "predecessors.s104.validator_module.path",
        ),
        S104_VALIDATOR_MODULE_PATH,
        "predecessors.s104.validator_module.path",
    )
    _expect(
        _sha256(
            s104_validator_module.get("sha256"),
            "predecessors.s104.validator_module.sha256",
        ),
        S104_VALIDATOR_MODULE_SHA256,
        "predecessors.s104.validator_module.sha256",
    )
    _expect(
        _sha256(
            s105.get("config_sha256"),
            "predecessors.s105.config_sha256",
        ),
        S105_CONFIG_SHA256,
        "predecessors.s105.config_sha256",
    )
    _expect(
        _relative_path(
            s105.get("config"),
            "predecessors.s105.config",
        ),
        S105_CONFIG_PATH,
        "predecessors.s105.config",
    )
    s105_validator_module = _mapping(
        s105.get("validator_module"),
        "predecessors.s105.validator_module",
    )
    _expect(
        _relative_path(
            s105_validator_module.get("path"),
            "predecessors.s105.validator_module.path",
        ),
        S105_VALIDATOR_MODULE_PATH,
        "predecessors.s105.validator_module.path",
    )
    _expect(
        _sha256(
            s105_validator_module.get("sha256"),
            "predecessors.s105.validator_module.sha256",
        ),
        S105_VALIDATOR_MODULE_SHA256,
        "predecessors.s105.validator_module.sha256",
    )
    _relative_path(
        s105.get("comparison_report_relative_path"),
        "predecessors.s105.comparison_report_relative_path",
    )
    _expect(
        s105.get("control_variant"),
        "simple_head_control",
        "predecessors.s105.control_variant",
    )
    _expect(
        s105.get("candidate_variant"),
        "levelwise_multi_aspect_hierarchy",
        "predecessors.s105.candidate_variant",
    )
    _expect(
        s105.get("selection_rule"),
        "candidate_if_adoption_passed_else_control",
        "predecessors.s105.selection_rule",
    )
    _expect(
        s105.get("selected_variant_must_match_commit"),
        True,
        "predecessors.s105.selected_variant_must_match_commit",
    )
    for name in (
        "config",
        "commit_manifest",
        "registry",
        "validator_module",
    ):
        if name == "validator_module":
            record = _mapping(b07.get(name), f"predecessors.b07.{name}")
            _relative_path(record.get("path"), f"predecessors.b07.{name}.path")
            _sha256(record.get("sha256"), f"predecessors.b07.{name}.sha256")
        else:
            _relative_path(b07.get(name), f"predecessors.b07.{name}")
    b07_validator_module = _mapping(
        b07.get("validator_module"),
        "predecessors.b07.validator_module",
    )
    _expect(
        b07_validator_module.get("path"),
        B07_VALIDATOR_MODULE_PATH,
        "predecessors.b07.validator_module.path",
    )
    _expect(
        b07_validator_module.get("sha256"),
        B07_VALIDATOR_MODULE_SHA256,
        "predecessors.b07.validator_module.sha256",
    )
    _expect(
        _sha256(b07.get("config_sha256"), "predecessors.b07.config_sha256"),
        B07_CONFIG_SHA256,
        "predecessors.b07.config_sha256",
    )
    _expect(
        _relative_path(b07.get("config"), "predecessors.b07.config"),
        B07_CONFIG_PATH,
        "predecessors.b07.config",
    )
    _expect(
        _sha256(b07.get("commit_sha256"), "predecessors.b07.commit_sha256"),
        B07_COMMIT_SHA256,
        "predecessors.b07.commit_sha256",
    )
    _expect(
        _sha256(b07.get("registry_sha256"), "predecessors.b07.registry_sha256"),
        B07_REGISTRY_SHA256,
        "predecessors.b07.registry_sha256",
    )
    _expect(
        _sha256(b07.get("generation_id"), "predecessors.b07.generation_id"),
        B07_GENERATION_ID,
        "predecessors.b07.generation_id",
    )
    _expect(
        b07.get("commit_schema_version"),
        B07_COMMIT_SCHEMA_VERSION,
        "predecessors.b07.commit_schema_version",
    )
    _expect(
        b07.get("registry_schema_version"),
        B07_REGISTRY_SCHEMA_VERSION,
        "predecessors.b07.registry_schema_version",
    )
    _expect(
        b07.get("independent_validator"),
        "voice_coach.training.b07_gop_training.validate_b07_commit",
        "predecessors.b07.independent_validator",
    )
    _expect(
        b07.get("required_validator_status"),
        "valid",
        "predecessors.b07.required_validator_status",
    )
    _expect(
        b07.get("required_commit_status"),
        "committed",
        "predecessors.b07.required_commit_status",
    )
    _expect(
        b07.get("required_registry_status"),
        "completed",
        "predecessors.b07.required_registry_status",
    )
    _expect(
        b07.get("required_generation_artifact_count"),
        21,
        "predecessors.b07.required_generation_artifact_count",
    )
    _expect(
        b07.get("stack_projection"),
        "serialized_acoustic_and_error_checkpoint_only",
        "predecessors.b07.stack_projection",
    )
    _expect(
        b07.get("dev_prediction_reuse_allowed"),
        False,
        "predecessors.b07.dev_prediction_reuse_allowed",
    )

    explicit = _mapping(config["explicit_component"], "explicit_component")
    _expect(explicit.get("name"), "explicit", "explicit_component.name")
    _expect(explicit.get("status"), "frozen", "explicit_component.status")
    for role in ("report", "registry", "config", "implementation"):
        record = _mapping(explicit.get(role), f"explicit_component.{role}")
        _relative_path(record.get("path"), f"explicit_component.{role}.path")
        _sha256(record.get("sha256"), f"explicit_component.{role}.sha256")
    report_record = _mapping(explicit["report"], "explicit_component.report")
    _expect(
        report_record.get("required_status"),
        "completed",
        "explicit_component.report.required_status",
    )
    _expect(
        report_record.get("required_experiment_id"),
        "aihub71490_explicit_oracle_boundary_v1",
        "explicit_component.report.required_experiment_id",
    )
    registry_record = _mapping(explicit["registry"], "explicit_component.registry")
    _expect(
        registry_record.get("required_status"),
        "completed",
        "explicit_component.registry.required_status",
    )
    _expect(
        registry_record.get("required_artifact_count"),
        13,
        "explicit_component.registry.required_artifact_count",
    )
    _expect(explicit.get("seeds"), list(SEEDS), "explicit_component.seeds")
    _expect(
        explicit.get("required_checkpoint_artifacts"),
        [f"seed_{seed}_checkpoint" for seed in SEEDS],
        "explicit_component.required_checkpoint_artifacts",
    )

    components = _mapping(config["components"], "components")
    _expect(components.get("order"), list(COMPONENTS), "components.order")
    expected_sources = {
        "explicit": "explicit_component",
        "s104_adapter": "validated_s104_commit",
        "s105_selected_hierarchy": "validated_s105_commit_selected_variant",
        "gop_acoustic_posterior": "validated_b07_serialized_checkpoints",
    }
    for component in COMPONENTS:
        spec = _mapping(components.get(component), f"components.{component}")
        _expect(
            spec.get("source"),
            expected_sources[component],
            f"components.{component}.source",
        )
        _expect(
            spec.get("frozen_before_stack"),
            True,
            f"components.{component}.frozen_before_stack",
        )
        _expect(
            spec.get("checkpoint_selection"),
            "all_preregistered_seeds",
            f"components.{component}.checkpoint_selection",
        )
    _expect(
        _mapping(
            components["s105_selected_hierarchy"],
            "components.s105_selected_hierarchy",
        ).get("selection_rule"),
        "candidate_if_adoption_passed_else_control",
        "components.s105_selected_hierarchy.selection_rule",
    )

    inputs = _mapping(config["inputs"], "inputs")
    _relative_path(
        inputs.get("component_bundle_commit"),
        "inputs.component_bundle_commit",
    )
    _expect(
        inputs.get("component_bundle_schema_version"),
        "voice_coach.wavlm_s106_component_bundle_commit.v3",
        "inputs.component_bundle_schema_version",
    )
    for field in (
        "component_generator_commit",
        "component_generator_registry",
        "component_feature_freeze_receipt",
    ):
        _relative_path(inputs.get(field), f"inputs.{field}")
    _expect(
        inputs.get("component_generator_commit_schema_version"),
        "voice_coach.wavlm_s106_component_input_commit.v2",
        "inputs.component_generator_commit_schema_version",
    )
    _expect(
        inputs.get("component_generator_registry_schema_version"),
        "voice_coach.wavlm_s106_component_input_registry.v1",
        "inputs.component_generator_registry_schema_version",
    )
    _expect(
        inputs.get("component_feature_freeze_receipt_schema_version"),
        "voice_coach.wavlm_s106_component_feature_freeze.v1",
        "inputs.component_feature_freeze_receipt_schema_version",
    )
    _expect(inputs.get("required_status"), "committed", "inputs.required_status")
    identity_matrix = _mapping(
        inputs.get("feature_identity_matrix"),
        "inputs.feature_identity_matrix",
    )
    _expect(
        identity_matrix.get("exact_columns"),
        [
            "utterance_id_sha256",
            "speaker_id_sha256",
            "phone_index",
            "split",
            "alignment_confidence",
            "qc_flagged",
            "reserved_prompt",
        ],
        "inputs.feature_identity_matrix.exact_columns",
    )
    _expect(
        identity_matrix.get("format"),
        "parquet",
        "inputs.feature_identity_matrix.format",
    )
    _expect(
        identity_matrix.get("row_key"),
        ["utterance_id_sha256", "phone_index"],
        "inputs.feature_identity_matrix.row_key",
    )
    _expect(
        identity_matrix.get("labels_present"),
        False,
        "inputs.feature_identity_matrix.labels_present",
    )
    label_matrix = _mapping(inputs.get("label_matrix"), "inputs.label_matrix")
    _expect(
        label_matrix.get("exact_columns"),
        [
            "utterance_id_sha256",
            "speaker_id_sha256",
            "phone_index",
            "split",
            "reference_binary_error_label",
        ],
        "inputs.label_matrix.exact_columns",
    )
    _expect(label_matrix.get("format"), "parquet", "inputs.label_matrix.format")
    _expect(
        label_matrix.get("row_key"),
        ["utterance_id_sha256", "phone_index"],
        "inputs.label_matrix.row_key",
    )
    _expect(
        label_matrix.get("label_column"),
        "reference_binary_error_label",
        "inputs.label_matrix.label_column",
    )
    _expect(
        label_matrix.get("alignment_confidence_column"),
        None,
        "inputs.label_matrix.alignment_confidence_column",
    )
    _expect(
        label_matrix.get("opened_after_feature_freeze"),
        True,
        "inputs.label_matrix.opened_after_feature_freeze",
    )
    component_prediction = _mapping(
        inputs.get("component_prediction"),
        "inputs.component_prediction",
    )
    _expect(
        component_prediction.get("exact_columns"),
        [
            "utterance_id_sha256",
            "speaker_id_sha256",
            "phone_index",
            "split",
            "probability",
        ],
        "inputs.component_prediction.exact_columns",
    )
    _expect(
        component_prediction.get("format"),
        "parquet",
        "inputs.component_prediction.format",
    )
    _expect(
        component_prediction.get("row_key"),
        ["utterance_id_sha256", "phone_index"],
        "inputs.component_prediction.row_key",
    )
    _expect(
        component_prediction.get("probability_column"),
        "probability",
        "inputs.component_prediction.probability_column",
    )
    _expect(
        component_prediction.get("required_component_seed_file_count"),
        12,
        "inputs.component_prediction.required_component_seed_file_count",
    )

    data = _mapping(config["data"], "data")
    stack = _mapping(data.get("stack"), "data.stack")
    _relative_path(stack.get("manifest"), "data.stack.manifest")
    _sha256(stack.get("manifest_sha256"), "data.stack.manifest_sha256")
    _expect(stack.get("split_value"), "stack", "data.stack.split_value")
    _expect(
        stack.get("source_scope"),
        "R1_operational_restricted",
        "data.stack.source_scope",
    )
    _expect(
        stack.get("derived_output_scope"),
        "D1_restricted_internal",
        "data.stack.derived_output_scope",
    )
    for name in (
        "path_resolution_requires_valid_predecessors",
        "row_read_requires_valid_predecessors",
        "runner_filter_pushdown_required",
        "runner_reject_non_stack_rows_after_projection",
    ):
        _expect(stack.get(name), True, f"data.stack.{name}")
    _expect(
        stack.get("semantic_row_read_by_preflight"),
        False,
        "data.stack.semantic_row_read_by_preflight",
    )
    _expect(data.get("allowed_splits"), list(ALLOWED_SPLITS), "data.allowed_splits")
    _expect(
        data.get("forbidden_splits"),
        list(FORBIDDEN_SPLITS),
        "data.forbidden_splits",
    )
    for name in (
        "train_dev_reuse_for_fusion",
        "fusion_feature_train_read",
        "fusion_feature_dev_read",
        "calibration_access_allowed",
        "locked_test_access_allowed",
        "environment_files_allowed",
    ):
        _expect(data.get(name), False, f"data.{name}")
    _expect(
        data.get("predecessor_audit_access_excluded_from_fusion_reuse_counters"),
        True,
        "data.predecessor_audit_access_excluded_from_fusion_reuse_counters",
    )
    for name in ("exclude_qc_flagged", "exclude_reserved_prompts"):
        _expect(data.get(name), True, f"data.{name}")
    join = _mapping(data.get("join"), "data.join")
    _expect(
        join.get("row_keys"),
        ["utterance_id_sha256", "phone_index"],
        "data.join.row_keys",
    )
    _expect(
        join.get("speaker_key"),
        "speaker_id_sha256",
        "data.join.speaker_key",
    )
    for name in ("require_namespaced_sha256_ids", "exact_stack_universe"):
        _expect(join.get(name), True, f"data.join.{name}")
    _expect(
        join.get("duplicate_key_policy"),
        "fail",
        "data.join.duplicate_key_policy",
    )
    _expect(
        join.get("cross_component_speaker_mismatch_policy"),
        "fail",
        "data.join.cross_component_speaker_mismatch_policy",
    )
    _expect(
        join.get("component_missing_row_policy"),
        "explicit_missing_indicator",
        "data.join.component_missing_row_policy",
    )

    projection = _mapping(config["stack_projection"], "stack_projection")
    _expect(
        projection.get("status"),
        "production_generator_ready_blocked_on_s104_s105_commits",
        "stack_projection.status",
    )
    _expect(
        projection.get("predecessor_validation_before_stack_path"),
        ["s104", "s105", "b07"],
        "stack_projection.predecessor_validation_before_stack_path",
    )
    _expect(
        projection.get("component_order"),
        list(COMPONENTS),
        "stack_projection.component_order",
    )
    _expect(
        projection.get("prediction_file_count"),
        len(COMPONENTS) * len(SEEDS),
        "stack_projection.prediction_file_count",
    )
    _expect(
        projection.get("maximum_batch_rows"),
        8_192,
        "stack_projection.maximum_batch_rows",
    )
    for name in (
        "feature_identity_built_before_label_open",
        "feature_builder_receives_labels",
        "binary_error_label_used_by_projectors",
        "realized_phone_identity_used",
        "b07_dev_predictions_reused",
    ):
        expected = name == "feature_identity_built_before_label_open"
        _expect(projection.get(name), expected, f"stack_projection.{name}")
    interfaces = _mapping(
        projection.get("interfaces"),
        "stack_projection.interfaces",
    )
    _expect(
        interfaces,
        {
            "explicit": "committed_phone_pipeline_predict_proba",
            "s104_adapter": "committed_adapter_phone_head_on_target_intervals",
            "s105_selected_hierarchy": (
                "committed_selected_hierarchy_phone_head_on_target_intervals"
            ),
            "gop_acoustic_posterior": ("serialized_acoustic_then_error_checkpoint"),
        },
        "stack_projection.interfaces",
    )
    _expect(
        projection.get("forbidden_source_artifacts"),
        [
            "b07_dev_predictions.parquet",
            "s104_phone_dev_predictions.parquet",
            "s105_phone_dev_predictions.parquet",
        ],
        "stack_projection.forbidden_source_artifacts",
    )
    _expect(
        projection.get("current_blockers"),
        [
            "s104_validated_commit_absent",
            "s105_validated_commit_absent",
        ],
        "stack_projection.current_blockers",
    )

    aggregation = _mapping(config["seed_aggregation"], "seed_aggregation")
    _expect(
        aggregation.get("occurs_before_stack_matrix_join"),
        True,
        "seed_aggregation.occurs_before_stack_matrix_join",
    )
    _expect(
        aggregation.get("method"),
        "arithmetic_mean_probability",
        "seed_aggregation.method",
    )
    _expect(aggregation.get("seeds"), list(SEEDS), "seed_aggregation.seeds")
    _expect(
        aggregation.get("per_seed_channels_allowed"),
        False,
        "seed_aggregation.per_seed_channels_allowed",
    )
    _expect(
        aggregation.get("partial_seed_presence_policy"),
        "fail",
        "seed_aggregation.partial_seed_presence_policy",
    )
    clip = _list(aggregation.get("probability_clip"), "probability_clip")
    if len(clip) != 2 or not (0 < _finite(clip[0], "clip lower") < 0.5):
        raise S106ContractError("probability clip lower bound differs")
    if not (0.5 < _finite(clip[1], "clip upper") < 1):
        raise S106ContractError("probability clip upper bound differs")
    _expect(
        aggregation.get("logit_derived_after_aggregation"),
        True,
        "seed_aggregation.logit_derived_after_aggregation",
    )
    uncertainty = _mapping(aggregation.get("uncertainty"), "uncertainty")
    _expect(
        uncertainty.get("name"),
        "seed_probability_variance_population",
        "uncertainty.name",
    )
    _expect(
        uncertainty.get("computed_before_stack_matrix_join"),
        True,
        "uncertainty.computed_before_stack_matrix_join",
    )
    _expect(uncertainty.get("denominator"), "population", "uncertainty.denominator")

    features = _mapping(config["feature_contract"], "feature_contract")
    _expect(features.get("task"), "phone_binary_error", "feature_contract.task")
    feature_order = [
        _string(value, "feature_contract.exact_order")
        for value in _list(features.get("exact_order"), "feature_contract.exact_order")
    ]
    validate_feature_columns(feature_order)
    _expect(
        tuple(features.get("allowed_semantics", [])),
        ALLOWED_FEATURE_SEMANTICS,
        "feature_contract.allowed_semantics",
    )
    label = _mapping(features.get("label_isolation"), "label_isolation")
    _expect(
        label.get("training_target"),
        "reference_binary_error_label",
        "label_isolation.training_target",
    )
    _expect(
        label.get("target_storage"),
        "physically_separate_label_matrix",
        "label_isolation.target_storage",
    )
    _expect(
        label.get("target_allowed_as_feature"),
        False,
        "label_isolation.target_allowed_as_feature",
    )
    _expect(
        label.get("feature_builder_receives_labels"),
        False,
        "label_isolation.feature_builder_receives_labels",
    )
    _expect(
        label.get("final_fit_receives_labels_only_after_feature_freeze"),
        True,
        "label_isolation.final_fit_receives_labels_only_after_feature_freeze",
    )
    _expect(
        set(features.get("forbidden_exact_names", [])),
        set(FORBIDDEN_EXACT_FEATURES),
        "feature_contract.forbidden_exact_names",
    )
    _expect(
        tuple(features.get("forbidden_name_fragments", [])),
        FORBIDDEN_FEATURE_FRAGMENTS,
        "feature_contract.forbidden_name_fragments",
    )
    alignment = _mapping(features.get("alignment_confidence"), "alignment_confidence")
    _expect(
        alignment.get("source"),
        "reviewed_realized_independent_source_else_null",
        "alignment_confidence.source",
    )
    _expect(alignment.get("range"), [0.0, 1.0], "alignment_confidence.range")
    _expect(
        alignment.get("current_source_available"),
        False,
        "alignment_confidence.current_source_available",
    )
    _expect(
        alignment.get("absent_source_policy"),
        "force_null_and_missing_indicator",
        "alignment_confidence.absent_source_policy",
    )
    for name in (
        "realized_phone_independent",
        "operation_independent",
        "error_tag_independent",
    ):
        _expect(alignment.get(name), True, f"alignment_confidence.{name}")
    missingness = _mapping(features.get("missingness"), "feature_contract.missingness")
    expected_imputation = {
        "probability_imputation": 0.5,
        "logit_imputation": 0.0,
        "margin_imputation": 0.0,
        "entropy_imputation": 0.6931471805599453,
        "uncertainty_imputation": 0.0,
        "confidence_imputation": 0.0,
    }
    for name, imputed_value in expected_imputation.items():
        _expect(
            _finite(missingness.get(name), f"missingness.{name}"),
            imputed_value,
            f"missingness.{name}",
        )
    _expect(
        missingness.get("indicators_required"),
        True,
        "missingness.indicators_required",
    )

    oof = _mapping(config["oof"], "oof")
    _expect(oof.get("fold_count"), OOF_FOLD_COUNT, "oof.fold_count")
    _expect(
        oof.get("assignment_unit"),
        "speaker_id_sha256",
        "oof.assignment_unit",
    )
    _expect(oof.get("assignment_namespace"), OOF_NAMESPACE, "oof.assignment_namespace")
    _expect(
        oof.get("assignment_rule"),
        "sha256_namespace_plus_speaker_modulo_fold_count",
        "oof.assignment_rule",
    )
    for name in (
        "every_speaker_exactly_one_validation_fold",
        "every_row_exactly_one_oof_prediction",
        "nonempty_train_and_validation_required_per_fold",
        "fold_models_never_fit_validation_speaker_or_label",
    ):
        _expect(oof.get(name), True, f"oof.{name}")
    _expect(
        oof.get("train_validation_speaker_overlap_maximum"),
        0,
        "oof.train_validation_speaker_overlap_maximum",
    )
    selection = _mapping(oof.get("oof_selection"), "oof.oof_selection")
    _expect(
        selection.get("architecture"),
        "standardized_l2_logistic_regression",
        "oof.oof_selection.architecture",
    )
    _expect(selection.get("solver"), "lbfgs", "oof.oof_selection.solver")
    _expect(
        selection.get("l1_ratio"),
        0.0,
        "oof.oof_selection.l1_ratio",
    )
    _expect(
        selection.get("maximum_iterations"),
        2000,
        "oof.oof_selection.maximum_iterations",
    )
    _expect(
        selection.get("tolerance"),
        0.00000001,
        "oof.oof_selection.tolerance",
    )
    _expect(
        selection.get("class_weight"),
        None,
        "oof.oof_selection.class_weight",
    )
    _expect(
        selection.get("selection_scope"),
        "aggregate_oof_predictions_only",
        "oof.oof_selection.selection_scope",
    )
    _expect(selection.get("selection_passes"), 1, "oof.oof_selection.selection_passes")
    _expect(
        selection.get("standardization_fit_scope"),
        "fold_training_rows_only",
        "oof.oof_selection.standardization_fit_scope",
    )
    _expect(
        selection.get("regularization_c_grid"),
        [0.01, 0.1, 1.0, 10.0],
        "oof.oof_selection.regularization_c_grid",
    )
    threshold_grid = _mapping(
        selection.get("threshold_grid"),
        "oof.oof_selection.threshold_grid",
    )
    _expect(
        threshold_grid,
        {"start": 0.10, "stop": 0.90, "step": 0.05},
        "oof.oof_selection.threshold_grid",
    )
    _expect(
        selection.get("primary_metric"),
        "speaker_macro_phone_macro_f1",
        "oof.oof_selection.primary_metric",
    )
    _expect(
        selection.get("tie_break"),
        [
            "higher_primary_metric",
            "smaller_regularization_c",
            "threshold_closest_to_0_5",
            "lower_threshold",
        ],
        "oof.oof_selection.tie_break",
    )
    _expect(
        oof.get("controls"),
        [
            "explicit_only",
            "s104_adapter_only",
            "s105_selected_hierarchy_only",
            "gop_only",
            "unweighted_component_probability_mean",
        ],
        "oof.controls",
    )
    _expect(
        oof.get("control_and_disagreement_threshold"),
        0.5,
        "oof.control_and_disagreement_threshold",
    )
    final_fit = _mapping(oof.get("final_fit"), "oof.final_fit")
    _expect(
        final_fit.get("source"),
        "full_stack_after_oof_selection",
        "oof.final_fit.source",
    )
    _expect(
        final_fit.get("selected_architecture_and_hyperparameters_frozen"),
        True,
        "oof.final_fit.selected_architecture_and_hyperparameters_frozen",
    )
    _expect(final_fit.get("refit_count"), 1, "oof.final_fit.refit_count")
    for name in ("in_sample_metrics_computed", "in_sample_metrics_reported"):
        _expect(final_fit.get(name), False, f"oof.final_fit.{name}")
    _expect(
        final_fit.get("evidence_role"),
        "checkpoint_only",
        "oof.final_fit.evidence_role",
    )

    reporting = _mapping(config["reporting"], "reporting")
    _expect(
        tuple(reporting.get("allowed_evidence", [])),
        REPORTING_ALLOWED_EVIDENCE,
        "reporting.allowed_evidence",
    )
    forbidden_evidence = set(
        _list(reporting.get("forbidden_evidence"), "reporting.forbidden_evidence")
    )
    required_forbidden = {
        "full_stack_in_sample_metric",
        "train_metric",
        "dev_metric",
        "calibration_metric",
        "locked_test_metric",
    }
    _expect(forbidden_evidence, required_forbidden, "reporting.forbidden_evidence")
    _expect(
        reporting.get("result_status_before_s107"),
        "engineering_stack_only",
        "reporting.result_status_before_s107",
    )
    _expect(
        reporting.get("deployment_claim_allowed"),
        False,
        "reporting.deployment_claim_allowed",
    )

    runtime = _mapping(config["runtime"], "runtime")
    _expect(runtime.get("fusion_device"), "cpu", "runtime.fusion_device")
    _expect(runtime.get("cuda_allowed"), False, "runtime.cuda_allowed")
    _expect(runtime.get("deterministic"), True, "runtime.deterministic")
    _expect(runtime.get("sklearn_n_jobs"), 1, "runtime.sklearn_n_jobs")
    _expect(runtime.get("thread_count"), 1, "runtime.thread_count")
    _expect(
        runtime.get("environment_files_forbidden"),
        True,
        "runtime.environment_files_forbidden",
    )
    _expect(
        runtime.get("temporary_generation_cleanup_required"),
        True,
        "runtime.temporary_generation_cleanup_required",
    )

    implementation = _mapping(config["implementation"], "implementation")
    _expect(
        set(implementation),
        {
            "preflight_script",
            "training_script",
            "contract_module",
            "training_module",
            "component_projection_module",
            "component_generator_config",
            "component_generator_script",
            "component_generator_module",
            "project_manifest",
            "scorer_lock",
        },
        "implementation roles",
    )
    for role, record_value in implementation.items():
        record = _mapping(record_value, f"implementation.{role}")
        _relative_path(record.get("path"), f"implementation.{role}.path")
        _sha256(record.get("sha256"), f"implementation.{role}.sha256")

    outputs = _mapping(config["outputs"], "outputs")
    for name in (
        "execution_lock",
        "generation_root",
        "commit_manifest",
        "registry_root",
    ):
        _relative_path(outputs.get(name), f"outputs.{name}")
    run_id = _string(
        outputs.get("planned_registry_run_id"),
        "outputs.planned_registry_run_id",
    )
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise S106ContractError("planned registry run id is invalid")
    for name in ("atomic_publish", "content_addressed_generation"):
        _expect(outputs.get(name), True, f"outputs.{name}")
    _expect(
        outputs.get("unexpected_artifacts_allowed"),
        False,
        "outputs.unexpected_artifacts_allowed",
    )
    _expect(outputs.get("expected_artifact_count"), 7, "expected_artifact_count")
    expected_artifacts = [
        {
            "name": name,
            "relative_path": relative_path,
            "format": format_name,
            "access_scope": access_scope,
        }
        for name, relative_path, format_name, access_scope in ARTIFACT_LAYOUT
    ]
    _expect(
        _list(outputs.get("artifact_contract"), "artifact_contract"),
        expected_artifacts,
        "outputs.artifact_contract",
    )
    _expect(outputs.get("registry_schema_version"), 1, "registry_schema_version")
    _expect(
        outputs.get("commit_schema_version"),
        "voice_coach.wavlm_s106_stack_fusion_commit.v2",
        "outputs.commit_schema_version",
    )


def validate_implementation_sources(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Validate pinned S106 code without importing a predecessor or touching data."""

    implementation = _mapping(config["implementation"], "implementation")
    validated: dict[str, Any] = {}
    for role, raw_record in implementation.items():
        record = _mapping(raw_record, f"implementation.{role}")
        path = _resolve_file(
            workspace_root,
            record.get("path"),
            f"implementation.{role}.path",
        )
        expected = _sha256(
            record.get("sha256"),
            f"implementation.{role}.sha256",
        )
        actual = sha256_file(path)
        if actual != expected:
            raise S106ContractError(f"implementation.{role} SHA-256 differs")
        validated[role] = {
            "path": _workspace_relative(workspace_root, path),
            "sha256": actual,
            "size_bytes": path.stat().st_size,
        }
    return validated


def _default_validator_invoker(
    symbol: str,
    workspace_root: Path,
    config_path: Path,
) -> Mapping[str, Any]:
    module_name, function_name = symbol.rsplit(".", 1)
    try:
        module = importlib.import_module(module_name)
        function = getattr(module, function_name)
    except (ImportError, AttributeError) as error:
        raise S106ContractError(
            f"independent predecessor validator is unavailable: {symbol}"
        ) from error
    if not callable(function):
        raise S106ContractError(f"predecessor validator is not callable: {symbol}")
    try:
        result = function(
            workspace_root=workspace_root,
            config_path=config_path,
        )
    except Exception as error:
        raise S106ContractError(
            f"independent predecessor validation failed: {symbol}"
        ) from error
    return _mapping(result, f"{symbol} result")


def _read_commit_after_validator(
    *,
    workspace_root: Path,
    spec: Mapping[str, Any],
    stage: str,
    invoker: ValidatorInvoker,
) -> tuple[Mapping[str, Any], Path, str, Mapping[str, Any]]:
    config_path = _resolve_file(
        workspace_root,
        spec.get("config"),
        f"predecessors.{stage}.config",
    )
    expected_config_sha = spec.get("config_sha256")
    if (
        expected_config_sha is not None
        and sha256_file(config_path) != expected_config_sha
    ):
        raise S106ContractError(f"{stage} frozen config SHA-256 differs")
    validator_module = spec.get("validator_module")
    if validator_module is not None:
        module_record = _mapping(
            validator_module,
            f"predecessors.{stage}.validator_module",
        )
        module_path = _resolve_file(
            workspace_root,
            module_record.get("path"),
            f"predecessors.{stage}.validator_module.path",
        )
        if sha256_file(module_path) != module_record.get("sha256"):
            raise S106ContractError(f"{stage} validator module SHA-256 differs")
    commit_path = _resolve_file(
        workspace_root,
        spec.get("commit_manifest"),
        f"predecessors.{stage}.commit_manifest",
    )
    validator = _string(
        spec.get("independent_validator"),
        f"predecessors.{stage}.independent_validator",
    )
    validation = invoker(validator, workspace_root, config_path)
    if validation.get("status") != spec.get("required_validator_status"):
        raise S106ContractError(f"{stage} independent validator did not pass")
    commit_sha = sha256_file(commit_path)
    if (
        validation.get("commit_sha256") is not None
        and validation.get("commit_sha256") != commit_sha
    ):
        raise S106ContractError(f"{stage} validator commit binding differs")
    commit = load_json_strict(commit_path, f"{stage} commit")
    if commit.get("schema_version") != spec.get("commit_schema_version") or commit.get(
        "status"
    ) != spec.get("required_commit_status"):
        raise S106ContractError(f"{stage} completed commit status differs")
    generation_id = commit.get("generation_id")
    if not isinstance(generation_id, str) or not SHA256_PATTERN.fullmatch(
        generation_id
    ):
        raise S106ContractError(f"{stage} generation id is invalid")
    return commit, commit_path, commit_sha, validation


def _canonical_source_record(
    *,
    workspace_root: Path,
    relative_path: str,
    name: str,
) -> Mapping[str, Any]:
    path = _resolve_file(workspace_root, relative_path, name)
    return {
        "path": _workspace_relative(workspace_root, path),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _validate_canonical_source_record(
    *,
    workspace_root: Path,
    value: object,
    name: str,
    required_path: str | None = None,
    required_sha256: str | None = None,
) -> tuple[Mapping[str, Any], Path]:
    record = _mapping(value, name)
    _exact_keys(record, {"path", "sha256", "size_bytes"}, name)
    relative = _relative_path(record.get("path"), f"{name}.path")
    digest = _sha256(record.get("sha256"), f"{name}.sha256")
    size = _integer(record.get("size_bytes"), f"{name}.size_bytes", minimum=0)
    if required_path is not None and relative != required_path:
        raise S106ContractError(f"{name} canonical path differs")
    if required_sha256 is not None and digest != required_sha256:
        raise S106ContractError(f"{name} canonical SHA-256 differs")
    path = _resolve_file(workspace_root, relative, f"{name}.path")
    if sha256_file(path) != digest or path.stat().st_size != size:
        raise S106ContractError(f"{name} actual file binding differs")
    return {
        "path": relative,
        "sha256": digest,
        "size_bytes": size,
    }, path


def _validate_predecessor_canonical_sources_record(
    *,
    workspace_root: Path,
    stage: str,
    predecessor: Mapping[str, Any],
) -> Mapping[str, Mapping[str, Any]]:
    requirements = {
        "s104": {
            "config_path": S104_CONFIG_PATH,
            "config_sha256": S104_CONFIG_SHA256,
            "module_path": S104_VALIDATOR_MODULE_PATH,
            "module_sha256": S104_VALIDATOR_MODULE_SHA256,
        },
        "s105": {
            "config_path": S105_CONFIG_PATH,
            "config_sha256": S105_CONFIG_SHA256,
            "module_path": S105_VALIDATOR_MODULE_PATH,
            "module_sha256": S105_VALIDATOR_MODULE_SHA256,
        },
        "b07": {
            "config_path": B07_CONFIG_PATH,
            "config_sha256": B07_CONFIG_SHA256,
            "module_path": B07_VALIDATOR_MODULE_PATH,
            "module_sha256": B07_VALIDATOR_MODULE_SHA256,
        },
    }
    if stage not in requirements:
        raise S106ContractError("unsupported canonical predecessor stage")
    required = requirements[stage]
    sources = _mapping(
        predecessor.get("canonical_sources"),
        f"{stage} canonical sources",
    )
    _exact_keys(
        sources,
        {"config", "validator_module", "report"},
        f"{stage} canonical sources",
    )
    config_record, config_path = _validate_canonical_source_record(
        workspace_root=workspace_root,
        value=sources.get("config"),
        name=f"{stage} canonical config",
        required_path=cast(str, required["config_path"]),
        required_sha256=cast(str, required["config_sha256"]),
    )
    module_record, module_path = _validate_canonical_source_record(
        workspace_root=workspace_root,
        value=sources.get("validator_module"),
        name=f"{stage} canonical validator module",
        required_path=cast(str, required["module_path"]),
        required_sha256=cast(str, required["module_sha256"]),
    )
    report_record, report_path = _validate_canonical_source_record(
        workspace_root=workspace_root,
        value=sources.get("report"),
        name=f"{stage} canonical report",
    )
    if (
        predecessor.get("report_path") != report_record["path"]
        or predecessor.get("report_sha256") != report_record["sha256"]
    ):
        raise S106ContractError(f"{stage} canonical report predecessor binding differs")

    commit_path = _resolve_file(
        workspace_root,
        predecessor.get("commit_path"),
        f"{stage} canonical source commit",
    )
    if predecessor.get("commit_sha256") != sha256_file(commit_path):
        raise S106ContractError(f"{stage} canonical source commit binding differs")
    commit = load_json_strict(commit_path, f"{stage} canonical source commit")
    if stage in {"s104", "s105"}:
        generation_path = _relative_path(
            commit.get("generation_path"),
            f"{stage} canonical source generation path",
        )
        report_relative = (
            "experiment_report.json"
            if stage == "s104"
            else "ablation_comparison_report.json"
        )
        report_artifact_name = (
            "experiment_report" if stage == "s104" else "ablation_comparison_report"
        )
        expected_report_path = f"{generation_path}/{report_relative}"
        artifacts = _list(
            commit.get("artifacts"),
            f"{stage} canonical source commit artifacts",
        )
        matching_report_artifacts = [
            _mapping(value, f"{stage} report artifact")
            for value in artifacts
            if isinstance(value, Mapping) and value.get("name") == report_artifact_name
        ]
        if len(matching_report_artifacts) != 1:
            raise S106ContractError(f"{stage} report artifact binding differs")
        artifact = matching_report_artifacts[0]
        top_level_hash_valid = (
            stage != "s104"
            or commit.get("experiment_report_sha256") == report_record["sha256"]
        )
        if (
            report_record["path"] != expected_report_path
            or artifact.get("path") != report_relative
            or artifact.get("sha256") != report_record["sha256"]
            or artifact.get("size_bytes") != report_record["size_bytes"]
            or not top_level_hash_valid
        ):
            raise S106ContractError(f"{stage} canonical report commit binding differs")
    else:
        generation = _mapping(
            commit.get("generation"),
            "b07 canonical source generation",
        )
        generation_path = _relative_path(
            generation.get("path"),
            "b07 canonical source generation path",
        )
        expected_report_path = f"{generation_path}/aggregate_report.json"
        records = _list(
            generation.get("records"),
            "b07 canonical source generation records",
        )
        matching_report_artifacts = [
            _mapping(value, "b07 aggregate report record")
            for value in records
            if isinstance(value, Mapping)
            and value.get("path") == "aggregate_report.json"
        ]
        if len(matching_report_artifacts) != 1:
            raise S106ContractError("b07 report artifact binding differs")
        artifact = matching_report_artifacts[0]
        if (
            report_record["path"] != expected_report_path
            or artifact.get("sha256") != report_record["sha256"]
            or artifact.get("size_bytes") != report_record["size_bytes"]
        ):
            raise S106ContractError("b07 canonical report commit binding differs")

    # Close the read window: every canonical source must still be the exact
    # path/hash/size tuple recorded above after the commit/report checks.
    for name, record, path in (
        ("config", config_record, config_path),
        ("validator_module", module_record, module_path),
        ("report", report_record, report_path),
    ):
        if (
            sha256_file(path) != record["sha256"]
            or path.stat().st_size != record["size_bytes"]
        ):
            raise S106ContractError(
                f"{stage} canonical {name} changed during validation"
            )
    return {
        "config": config_record,
        "validator_module": module_record,
        "report": report_record,
    }


def validate_predecessor_canonical_sources(
    *,
    workspace_root: Path,
    execution_lock: Mapping[str, Any],
) -> Mapping[str, Mapping[str, Mapping[str, Any]]]:
    """Revalidate config, validator, and report sources from an S106 lock."""

    root = workspace_root.resolve(strict=True)
    predecessors = _mapping(
        execution_lock.get("predecessors"),
        "S106 execution lock predecessors",
    )
    _exact_keys(predecessors, {"s104", "s105", "b07"}, "S106 lock predecessors")
    return {
        stage: _validate_predecessor_canonical_sources_record(
            workspace_root=root,
            stage=stage,
            predecessor=_mapping(
                predecessors.get(stage),
                f"S106 execution lock {stage} predecessor",
            ),
        )
        for stage in ("s104", "s105", "b07")
    }


def _projection_checkpoint_records(
    *,
    workspace_root: Path,
    commit: Mapping[str, Any],
    stage: str,
    selected_variant: str,
) -> tuple[Mapping[str, Any], ...]:
    """Bind one label-free stack checkpoint per seed to a predecessor commit."""

    generation_path = _relative_path(
        commit.get("generation_path"),
        f"{stage} generation path",
    )
    artifacts = _list(commit.get("artifacts"), f"{stage} commit artifacts")
    by_name = {
        _string(
            _mapping(value, f"{stage} artifact").get("name"),
            f"{stage} artifact name",
        ): _mapping(value, f"{stage} artifact")
        for value in artifacts
    }
    output: list[Mapping[str, Any]] = []
    for seed in SEEDS:
        if stage == "s104":
            name = f"seed_{seed}_adapter_and_heads_checkpoint"
            expected_relative = f"seed_{seed}/adapter_and_heads.safetensors"
        elif stage == "s105":
            name = f"{selected_variant}_seed_{seed}_hierarchy_checkpoint"
            expected_relative = (
                f"{selected_variant}/seed_{seed}/hierarchy_head.safetensors"
            )
        else:
            raise S106ContractError("unsupported projection checkpoint stage")
        artifact = _mapping(by_name.get(name), f"{stage} projection checkpoint")
        relative = _relative_path(
            artifact.get("path"),
            f"{stage} projection checkpoint path",
        )
        if relative != expected_relative:
            raise S106ContractError(
                f"{stage} projection checkpoint relative path differs"
            )
        path = _resolve_file(
            workspace_root,
            f"{generation_path}/{relative}",
            f"{stage} seed {seed} projection checkpoint",
        )
        digest = sha256_file(path)
        if (
            artifact.get("sha256") != digest
            or artifact.get("size_bytes") != path.stat().st_size
        ):
            raise S106ContractError(f"{stage} projection checkpoint binding differs")
        output.append(
            {
                "seed": seed,
                "checkpoint": {
                    "path": _workspace_relative(workspace_root, path),
                    "sha256": digest,
                    "size_bytes": path.stat().st_size,
                },
            }
        )
    return tuple(output)


def _validate_s104_predecessor(
    *,
    workspace_root: Path,
    spec: Mapping[str, Any],
    invoker: ValidatorInvoker,
) -> PredecessorEvidence:
    commit, commit_path, commit_sha, validation = _read_commit_after_validator(
        workspace_root=workspace_root,
        spec=spec,
        stage="s104",
        invoker=invoker,
    )
    if (
        validation.get("development_only") is not True
        or validation.get("locked_test_accessed") is not False
    ):
        raise S106ContractError("S104 deep-validator evidence differs")
    generation_path = _resolve_file(
        workspace_root,
        f"{commit['generation_path']}/experiment_report.json",
        "S104 experiment report",
    )
    report = load_json_strict(generation_path, "S104 experiment report")
    leakage = _mapping(report.get("leakage"), "S104 report leakage")
    if (
        report.get("schema_version") != spec.get("report_schema_version")
        or report.get("status") != spec.get("required_report_status")
        or report.get("development_only") is not True
        or report.get("oracle_alignment") is not True
        or report.get("silver_label") is not True
        or report.get("promotion_decision_allowed") is not False
        or leakage.get("stack_accessed") is not False
        or leakage.get("calibration_accessed") is not False
        or leakage.get("locked_test_accessed") is not False
    ):
        raise S106ContractError("S104 report access or status contract differs")
    lock_record = _mapping(commit.get("execution_lock"), "S104 execution lock record")
    lock_path = _resolve_file(
        workspace_root,
        lock_record.get("path"),
        "S104 execution lock",
    )
    lock = load_json_strict(lock_path, "S104 execution lock")
    selected = _mapping(lock.get("selected_candidate"), "S104 selected candidate")
    selected_variant = _string(
        selected.get("variant"),
        "S104 selected candidate variant",
    )
    if selected_variant != spec.get("required_selected_candidate"):
        raise S106ContractError("S104 selected candidate differs from S103 branch")
    projection_checkpoints = _projection_checkpoint_records(
        workspace_root=workspace_root,
        commit=commit,
        stage="s104",
        selected_variant=selected_variant,
    )
    report_sha256 = sha256_file(generation_path)
    canonical_sources = {
        "config": _canonical_source_record(
            workspace_root=workspace_root,
            relative_path=S104_CONFIG_PATH,
            name="S104 canonical config",
        ),
        "validator_module": _canonical_source_record(
            workspace_root=workspace_root,
            relative_path=S104_VALIDATOR_MODULE_PATH,
            name="S104 canonical validator module",
        ),
        "report": _canonical_source_record(
            workspace_root=workspace_root,
            relative_path=_workspace_relative(workspace_root, generation_path),
            name="S104 canonical report",
        ),
    }
    evidence = PredecessorEvidence(
        stage="s104",
        commit_path=_workspace_relative(workspace_root, commit_path),
        commit_sha256=commit_sha,
        generation_id=cast(str, commit["generation_id"]),
        report_path=_workspace_relative(workspace_root, generation_path),
        report_sha256=report_sha256,
        selected_variant=selected_variant,
        adoption_status=None,
        projection_checkpoints=projection_checkpoints,
        deep_audit={
            "validator_status": "valid",
            "development_only": True,
            "locked_test_accessed": False,
            "fit_metadata_read": True,
            "dev_truth_read": True,
            "per_seed_dev_predictions_read": True,
            "access_basis": "pinned_public_validator_contract_inspection",
        },
        canonical_sources=canonical_sources,
    )
    _validate_predecessor_canonical_sources_record(
        workspace_root=workspace_root,
        stage="s104",
        predecessor={
            "commit_path": evidence.commit_path,
            "commit_sha256": evidence.commit_sha256,
            "report_path": evidence.report_path,
            "report_sha256": evidence.report_sha256,
            "canonical_sources": canonical_sources,
        },
    )
    return evidence


def _validate_s105_predecessor(
    *,
    workspace_root: Path,
    spec: Mapping[str, Any],
    invoker: ValidatorInvoker,
) -> PredecessorEvidence:
    commit, commit_path, commit_sha, validation = _read_commit_after_validator(
        workspace_root=workspace_root,
        spec=spec,
        stage="s105",
        invoker=invoker,
    )
    report_relative = _relative_path(
        spec.get("comparison_report_relative_path"),
        "S105 comparison report relative path",
    )
    report_path = _resolve_file(
        workspace_root,
        f"{commit['generation_path']}/{report_relative}",
        "S105 comparison report",
    )
    report = load_json_strict(report_path, "S105 comparison report")
    leakage = _mapping(report.get("leakage"), "S105 report leakage")
    gate = _mapping(report.get("adoption_gate"), "S105 adoption gate")
    if (
        report.get("schema_version") != spec.get("comparison_report_schema_version")
        or report.get("status") != spec.get("required_report_status")
        or report.get("development_only") is not True
        or report.get("oracle_alignment") is not True
        or report.get("silver_label") is not True
        or report.get("promotion_decision_allowed") is not False
        or leakage.get("stack_accessed") is not False
        or leakage.get("calibration_accessed") is not False
        or leakage.get("locked_test_accessed") is not False
    ):
        raise S106ContractError("S105 report access or status contract differs")
    adopted = _boolean(gate.get("adopt_candidate"), "S105 adopt_candidate")
    expected_status = "passed" if adopted else "failed"
    if gate.get("status") != expected_status:
        raise S106ContractError("S105 adoption status is inconsistent")
    control = _string(spec.get("control_variant"), "S105 control variant")
    candidate = _string(spec.get("candidate_variant"), "S105 candidate variant")
    if (
        report.get("control_variant") != control
        or report.get("candidate_variant") != candidate
    ):
        raise S106ContractError("S105 adoption variants differ")
    selected_variant = candidate if adopted else control
    if (
        commit.get("adoption_status") != expected_status
        or commit.get("selected_variant") != selected_variant
        or validation.get("adoption_status") != expected_status
        or validation.get("selected_variant") != selected_variant
        or validation.get("development_only") is not True
        or validation.get("stack_accessed") is not False
        or validation.get("calibration_accessed") is not False
        or validation.get("locked_test_accessed") is not False
    ):
        raise S106ContractError("S105 commit selected_variant differs from its gate")
    artifacts = _list(commit.get("artifacts"), "S105 commit artifacts")
    matching = [
        value
        for value in artifacts
        if isinstance(value, Mapping)
        and value.get("name") == "ablation_comparison_report"
    ]
    if len(matching) != 1:
        raise S106ContractError("S105 comparison report artifact binding is absent")
    record = _mapping(matching[0], "S105 comparison report artifact")
    if (
        record.get("sha256") != sha256_file(report_path)
        or Path(_string(record.get("path"), "S105 report artifact path")).name
        != report_path.name
    ):
        raise S106ContractError("S105 comparison report hash binding differs")
    projection_checkpoints = _projection_checkpoint_records(
        workspace_root=workspace_root,
        commit=commit,
        stage="s105",
        selected_variant=selected_variant,
    )
    report_sha256 = sha256_file(report_path)
    canonical_sources = {
        "config": _canonical_source_record(
            workspace_root=workspace_root,
            relative_path=S105_CONFIG_PATH,
            name="S105 canonical config",
        ),
        "validator_module": _canonical_source_record(
            workspace_root=workspace_root,
            relative_path=S105_VALIDATOR_MODULE_PATH,
            name="S105 canonical validator module",
        ),
        "report": _canonical_source_record(
            workspace_root=workspace_root,
            relative_path=_workspace_relative(workspace_root, report_path),
            name="S105 canonical report",
        ),
    }
    evidence = PredecessorEvidence(
        stage="s105",
        commit_path=_workspace_relative(workspace_root, commit_path),
        commit_sha256=commit_sha,
        generation_id=cast(str, commit["generation_id"]),
        report_path=_workspace_relative(workspace_root, report_path),
        report_sha256=report_sha256,
        selected_variant=selected_variant,
        adoption_status=expected_status,
        projection_checkpoints=projection_checkpoints,
        deep_audit={
            "validator_status": "valid",
            "development_only": True,
            "stack_accessed": False,
            "calibration_accessed": False,
            "locked_test_accessed": False,
            "manifest_reservation_labels_read": True,
            "dev_predictions_read": True,
            "nested_s104_full_validator": True,
            "access_basis": "pinned_public_validator_contract_inspection",
        },
        canonical_sources=canonical_sources,
    )
    _validate_predecessor_canonical_sources_record(
        workspace_root=workspace_root,
        stage="s105",
        predecessor={
            "commit_path": evidence.commit_path,
            "commit_sha256": evidence.commit_sha256,
            "report_path": evidence.report_path,
            "report_sha256": evidence.report_sha256,
            "canonical_sources": canonical_sources,
        },
    )
    return evidence


def _validate_b07_predecessor(
    *,
    workspace_root: Path,
    spec: Mapping[str, Any],
    invoker: ValidatorInvoker,
) -> PredecessorEvidence:
    """Run B07 full recomputation, then forbid its dev predictions as fusion input."""

    config_path = _resolve_file(
        workspace_root,
        spec.get("config"),
        "predecessors.b07.config",
    )
    if sha256_file(config_path) != spec.get("config_sha256"):
        raise S106ContractError("B07 frozen config SHA-256 differs")
    validator_module = _mapping(
        spec.get("validator_module"),
        "predecessors.b07.validator_module",
    )
    validator_module_path = _resolve_file(
        workspace_root,
        validator_module.get("path"),
        "predecessors.b07.validator_module.path",
    )
    if sha256_file(validator_module_path) != validator_module.get("sha256"):
        raise S106ContractError("B07 validator module SHA-256 differs")
    commit_path = _resolve_file(
        workspace_root,
        spec.get("commit_manifest"),
        "predecessors.b07.commit_manifest",
    )
    commit_sha = sha256_file(commit_path)
    if commit_sha != spec.get("commit_sha256"):
        raise S106ContractError("B07 completed commit SHA-256 differs")
    validator = _string(
        spec.get("independent_validator"),
        "predecessors.b07.independent_validator",
    )
    validation = invoker(validator, workspace_root, config_path)
    if (
        validation.get("status") != spec.get("required_validator_status")
        or validation.get("commit_sha256") != commit_sha
        or validation.get("generation_id") != spec.get("generation_id")
        or validation.get("artifact_count")
        != spec.get("required_generation_artifact_count")
        or validation.get("full_train_selection_dev_recomputed") is not True
        or validation.get("serialized_acoustic_checkpoint_replayed") is not True
        or validation.get("serialized_error_checkpoint_replayed") is not True
        or validation.get("dev_hyperparameter_access_count") != 0
        or validation.get("stack_access_count") != 0
        or validation.get("calibration_access_count") != 0
        or validation.get("locked_test_access_count") != 0
        or validation.get("raw_manifest_access_count") != 0
        or validation.get("environment_file_access_count") != 0
    ):
        raise S106ContractError("B07 independent validator result differs")
    commit = load_json_strict(commit_path, "B07 commit")
    generation = _mapping(commit.get("generation"), "B07 generation")
    records = _list(generation.get("records"), "B07 generation records")
    if (
        commit.get("schema_version") != spec.get("commit_schema_version")
        or commit.get("status") != spec.get("required_commit_status")
        or generation.get("id") != spec.get("generation_id")
        or generation.get("artifact_count")
        != spec.get("required_generation_artifact_count")
        or len(records) != spec.get("required_generation_artifact_count")
    ):
        raise S106ContractError("B07 commit generation binding differs")
    generation_path = _relative_path(
        generation.get("path"),
        "B07 generation path",
    )
    by_path = {
        _string(
            _mapping(value, "B07 generation record").get("path"),
            "B07 generation record path",
        ): _mapping(value, "B07 generation record")
        for value in records
    }
    registry_path = _resolve_file(
        workspace_root,
        spec.get("registry"),
        "predecessors.b07.registry",
    )
    registry_sha = sha256_file(registry_path)
    registry = load_json_strict(registry_path, "B07 registry")
    boundary = _mapping(registry.get("data_boundary"), "B07 data boundary")
    if (
        registry_sha != spec.get("registry_sha256")
        or validation.get("registry_sha256") != registry_sha
        or registry.get("contract_schema_version")
        != spec.get("registry_schema_version")
        or registry.get("status") != spec.get("required_registry_status")
        or registry.get("failure") is not None
        or registry.get("generation_id") != spec.get("generation_id")
        or any(
            boundary.get(name) != 0
            for name in (
                "stack_access_count",
                "calibration_access_count",
                "locked_test_access_count",
                "raw_manifest_access_count",
                "environment_file_access_count",
            )
        )
    ):
        raise S106ContractError("B07 registry status or access boundary differs")

    from voice_coach.training.wavlm_s106_component_projection import (
        S106ProjectionError,
        validate_b07_projection_checkpoints,
    )

    checkpoint_records: list[Mapping[str, Any]] = []
    for seed in SEEDS:
        documents: dict[str, Mapping[str, Any]] = {}
        seed_record: dict[str, Any] = {"seed": seed}
        for kind in ("acoustic", "error"):
            relative = f"seed_{seed}/{kind}_checkpoint.json"
            record = _mapping(
                by_path.get(relative),
                f"B07 {kind} checkpoint record",
            )
            path = _resolve_file(
                workspace_root,
                f"{generation_path}/{relative}",
                f"B07 seed {seed} {kind} checkpoint",
            )
            digest = sha256_file(path)
            if digest != record.get("sha256") or path.stat().st_size != record.get(
                "size_bytes"
            ):
                raise S106ContractError("B07 projection checkpoint binding differs")
            documents[kind] = load_json_strict(path, f"B07 {kind} checkpoint")
            seed_record[f"{kind}_checkpoint"] = {
                "path": _workspace_relative(workspace_root, path),
                "sha256": digest,
                "size_bytes": path.stat().st_size,
            }
        try:
            projection = validate_b07_projection_checkpoints(
                acoustic_checkpoint=documents["acoustic"],
                error_checkpoint=documents["error"],
                expected_seed=seed,
            )
        except S106ProjectionError as error:
            raise S106ContractError(
                "B07 serialized checkpoints are not stack-projection sufficient"
            ) from error
        seed_record["projection_validation"] = dict(projection)
        checkpoint_records.append(seed_record)
    aggregate_relative = "aggregate_report.json"
    aggregate_record = _mapping(
        by_path.get(aggregate_relative),
        "B07 aggregate report record",
    )
    aggregate_path = _resolve_file(
        workspace_root,
        f"{generation_path}/{aggregate_relative}",
        "B07 aggregate report",
    )
    aggregate = load_json_strict(aggregate_path, "B07 aggregate report")
    gate = _mapping(aggregate.get("adoption_gate"), "B07 adoption gate")
    if (
        sha256_file(aggregate_path) != aggregate_record.get("sha256")
        or aggregate_path.stat().st_size != aggregate_record.get("size_bytes")
        or aggregate.get("status") != "completed"
        or aggregate.get("deployable") is not False
        or aggregate.get("canonical_b03_metric_claimed") is not False
        or gate.get("passed") is not True
    ):
        raise S106ContractError("B07 aggregate adoption evidence differs")
    if (
        sha256_file(config_path) != spec.get("config_sha256")
        or sha256_file(commit_path) != commit_sha
        or sha256_file(registry_path) != registry_sha
    ):
        raise S106ContractError("B07 predecessor changed during validation")
    aggregate_sha256 = sha256_file(aggregate_path)
    canonical_sources = {
        "config": _canonical_source_record(
            workspace_root=workspace_root,
            relative_path=B07_CONFIG_PATH,
            name="B07 canonical config",
        ),
        "validator_module": _canonical_source_record(
            workspace_root=workspace_root,
            relative_path=B07_VALIDATOR_MODULE_PATH,
            name="B07 canonical validator module",
        ),
        "report": _canonical_source_record(
            workspace_root=workspace_root,
            relative_path=_workspace_relative(workspace_root, aggregate_path),
            name="B07 canonical report",
        ),
    }
    evidence = PredecessorEvidence(
        stage="b07",
        commit_path=_workspace_relative(workspace_root, commit_path),
        commit_sha256=commit_sha,
        generation_id=cast(str, generation["id"]),
        report_path=_workspace_relative(workspace_root, aggregate_path),
        report_sha256=aggregate_sha256,
        selected_variant="gop_acoustic_posterior",
        adoption_status="passed",
        registry_path=_workspace_relative(workspace_root, registry_path),
        registry_sha256=registry_sha,
        projection_checkpoints=tuple(checkpoint_records),
        deep_audit={
            "validator_status": "valid",
            "full_train_selection_dev_recomputed": True,
            "serialized_acoustic_checkpoint_replayed": True,
            "serialized_error_checkpoint_replayed": True,
            "canonical_train_read": True,
            "canonical_dev_read": True,
            "stored_dev_predictions_read": True,
            "fusion_component_reuse": False,
            "access_basis": "validator_return_and_pinned_public_contract",
        },
        canonical_sources=canonical_sources,
    )
    _validate_predecessor_canonical_sources_record(
        workspace_root=workspace_root,
        stage="b07",
        predecessor={
            "commit_path": evidence.commit_path,
            "commit_sha256": evidence.commit_sha256,
            "report_path": evidence.report_path,
            "report_sha256": evidence.report_sha256,
            "canonical_sources": canonical_sources,
        },
    )
    return evidence


def validate_predecessors(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    invoker: ValidatorInvoker = _default_validator_invoker,
) -> tuple[PredecessorEvidence, PredecessorEvidence, PredecessorEvidence]:
    """Validate S104, S105, then B07 without referencing the stack section."""

    predecessors = _mapping(config["predecessors"], "predecessors")
    if predecessors.get("validation_order") != ["s104", "s105", "b07"]:
        raise S106ContractError("predecessor validation order differs")
    s104 = _validate_s104_predecessor(
        workspace_root=workspace_root,
        spec=_mapping(predecessors["s104"], "predecessors.s104"),
        invoker=invoker,
    )
    s105 = _validate_s105_predecessor(
        workspace_root=workspace_root,
        spec=_mapping(predecessors["s105"], "predecessors.s105"),
        invoker=invoker,
    )
    b07 = _validate_b07_predecessor(
        workspace_root=workspace_root,
        spec=_mapping(predecessors["b07"], "predecessors.b07"),
        invoker=invoker,
    )
    return s104, s105, b07


def _validate_explicit_component(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
) -> Mapping[str, Any]:
    explicit = _mapping(config["explicit_component"], "explicit_component")
    records: dict[str, Any] = {}
    resolved: dict[str, Path] = {}
    for role in ("report", "registry", "config", "implementation"):
        record = _mapping(explicit[role], f"explicit_component.{role}")
        path = _resolve_file(
            workspace_root,
            record.get("path"),
            f"explicit_component.{role}.path",
        )
        digest = sha256_file(path)
        if digest != record.get("sha256"):
            raise S106ContractError(f"explicit {role} hash differs")
        resolved[role] = path
        records[role] = {
            "path": _workspace_relative(workspace_root, path),
            "sha256": digest,
            "size_bytes": path.stat().st_size,
        }
    report_spec = _mapping(explicit["report"], "explicit report spec")
    report = load_json_strict(resolved["report"], "explicit report")
    leakage = _mapping(report.get("leakage_controls"), "explicit leakage controls")
    if (
        report.get("status") != report_spec.get("required_status")
        or report.get("experiment_id") != report_spec.get("required_experiment_id")
        or report.get("development_only") is not True
        or report.get("oracle_alignment") is not True
        or leakage.get("stack_accessed") is not False
        or leakage.get("calibration_accessed") is not False
        or leakage.get("locked_test_accessed") is not False
    ):
        raise S106ContractError("explicit component status or split access differs")
    registry_spec = _mapping(explicit["registry"], "explicit registry spec")
    registry = load_json_strict(resolved["registry"], "explicit registry")
    artifacts = _list(registry.get("artifacts"), "explicit registry artifacts")
    if (
        registry.get("status") != registry_spec.get("required_status")
        or registry.get("failure") is not None
        or registry.get("run_id") != registry_spec.get("required_run_id")
        or len(artifacts) != registry_spec.get("required_artifact_count")
    ):
        raise S106ContractError("explicit registry status differs")
    by_name = {
        _string(
            _mapping(value, "explicit artifact").get("name"), "artifact name"
        ): _mapping(value, "explicit artifact")
        for value in artifacts
    }
    projection_checkpoints: list[Mapping[str, Any]] = []
    for seed, name in zip(
        SEEDS,
        explicit.get("required_checkpoint_artifacts", []),
        strict=True,
    ):
        record = _mapping(by_name.get(name), f"explicit artifact {name}")
        path = _resolve_file(
            workspace_root,
            record.get("path"),
            f"explicit artifact {name} path",
        )
        if sha256_file(path) != record.get(
            "sha256"
        ) or path.stat().st_size != record.get("size_bytes"):
            raise S106ContractError(f"explicit checkpoint {name} binding differs")
        projection_checkpoints.append(
            {
                "seed": seed,
                "checkpoint": {
                    "path": _workspace_relative(workspace_root, path),
                    "sha256": sha256_file(path),
                    "size_bytes": path.stat().st_size,
                },
            }
        )
    return {
        "name": "explicit",
        "status": "frozen",
        "seeds": list(SEEDS),
        "sources": records,
        "checkpoint_artifacts": list(explicit.get("required_checkpoint_artifacts", [])),
        "projection_checkpoints": projection_checkpoints,
    }


def _resolve_stack_contract(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
) -> StackSource:
    """Resolve/hash the stack locator; callers must validate predecessors first."""

    data = _mapping(config["data"], "data")
    stack = _mapping(data["stack"], "data.stack")
    path = _resolve_file(
        workspace_root,
        stack.get("manifest"),
        "data.stack.manifest",
    )
    digest = sha256_file(path)
    if digest != stack.get("manifest_sha256"):
        raise S106ContractError("stack manifest SHA-256 differs")
    return StackSource(
        path=_workspace_relative(workspace_root, path),
        sha256=digest,
        size_bytes=path.stat().st_size,
        split_value="stack",
    )


def _contract_digest(config: Mapping[str, Any], name: str) -> str:
    value = _mapping(config[name], name)
    return hashlib.sha256(canonical_json_bytes(value, pretty=False)).hexdigest()


def publish_immutable_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = canonical_json_bytes(value, pretty=True)
    if path.exists():
        if not path.is_file() or path.is_symlink() or path.read_bytes() != encoded:
            raise S106ContractError("existing S106 execution lock differs")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part-{uuid4().hex}")
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if not path.is_file() or path.is_symlink() or path.read_bytes() != encoded:
                raise S106ContractError(
                    "existing S106 execution lock differs"
                ) from None
    finally:
        temporary.unlink(missing_ok=True)


def prepare_s106_execution(
    *,
    workspace_root: Path,
    config_path: Path,
    output_path: Path | None = None,
    validator_invoker: ValidatorInvoker = _default_validator_invoker,
) -> tuple[Mapping[str, Any], Path]:
    """Publish a no-metric lock only after S104/S105 and source validation."""

    root = workspace_root.resolve(strict=True)
    resolved_config = config_path
    if not resolved_config.is_absolute():
        resolved_config = root / resolved_config
    resolved_config = resolved_config.resolve(strict=True)
    try:
        resolved_config.relative_to(root)
    except ValueError as error:
        raise S106ContractError("S106 config is outside the workspace") from error
    config_sha = sha256_file(resolved_config)
    config = load_config(resolved_config)
    validate_config(config)
    implementation = validate_implementation_sources(
        workspace_root=root,
        config=config,
    )

    # Critical ordering boundary: neither `data.stack` nor output is resolved
    # before all three independent validators and adoption evidence have passed.
    s104, s105, b07 = validate_predecessors(
        workspace_root=root,
        config=config,
        invoker=validator_invoker,
    )
    if any(value.deep_audit is None for value in (s104, s105, b07)):
        raise S106ContractError("predecessor deep-audit evidence is absent")
    if any(value.canonical_sources is None for value in (s104, s105, b07)):
        raise S106ContractError("predecessor canonical-source evidence is absent")
    explicit = _validate_explicit_component(
        workspace_root=root,
        config=config,
    )
    stack = _resolve_stack_contract(workspace_root=root, config=config)

    payload: dict[str, Any] = {
        "schema_version": EXECUTION_LOCK_SCHEMA_VERSION,
        "status": "ready",
        "experiment_id": _mapping(config["experiment"], "experiment")["id"],
        "stage": "s106_stack_only_confidence_aware_fusion",
        "config": {
            "path": _workspace_relative(root, resolved_config),
            "sha256": config_sha,
        },
        "implementation": implementation,
        "predecessors": {
            evidence.stage: {
                "commit_path": evidence.commit_path,
                "commit_sha256": evidence.commit_sha256,
                "generation_id": evidence.generation_id,
                "report_path": evidence.report_path,
                "report_sha256": evidence.report_sha256,
                "selected_variant": evidence.selected_variant,
                "adoption_status": evidence.adoption_status,
                "projection_checkpoints": list(evidence.projection_checkpoints),
                "deep_audit": dict(
                    cast(Mapping[str, Any], evidence.deep_audit),
                ),
                **(
                    {
                        "registry_path": evidence.registry_path,
                        "registry_sha256": evidence.registry_sha256,
                    }
                    if evidence.stage == "b07"
                    else {}
                ),
                "canonical_sources": {
                    role: dict(record)
                    for role, record in cast(
                        Mapping[str, Mapping[str, Any]],
                        evidence.canonical_sources,
                    ).items()
                },
            }
            for evidence in (s104, s105, b07)
        },
        "frozen_components": {
            "order": list(COMPONENTS),
            "explicit": explicit,
            "s104_adapter": {
                "source_commit_sha256": s104.commit_sha256,
                "selected_variant": s104.selected_variant,
                "seeds": list(SEEDS),
                "projection_checkpoints": list(s104.projection_checkpoints),
            },
            "s105_selected_hierarchy": {
                "source_commit_sha256": s105.commit_sha256,
                "selected_variant": s105.selected_variant,
                "adoption_status": s105.adoption_status,
                "seeds": list(SEEDS),
                "projection_checkpoints": list(s105.projection_checkpoints),
            },
            "gop_acoustic_posterior": {
                "source_commit_sha256": b07.commit_sha256,
                "source_registry_sha256": b07.registry_sha256,
                "generation_id": b07.generation_id,
                "selected_variant": b07.selected_variant,
                "adoption_status": b07.adoption_status,
                "seeds": list(SEEDS),
                "projection_checkpoints": list(b07.projection_checkpoints),
                "label_free_projection": True,
                "dev_prediction_reused": False,
            },
        },
        "stack_source": {
            "path": stack.path,
            "sha256": stack.sha256,
            "size_bytes": stack.size_bytes,
            "split_value": stack.split_value,
            "row_data_read_by_preflight": False,
        },
        "component_bundle_contract": {
            "path": _mapping(config["inputs"], "inputs")["component_bundle_commit"],
            "schema_version": _mapping(config["inputs"], "inputs")[
                "component_bundle_schema_version"
            ],
            "resolved_or_read_by_preflight": False,
        },
        "contract_hashes": {
            "seed_aggregation_sha256": _contract_digest(
                config,
                "seed_aggregation",
            ),
            "feature_contract_sha256": _contract_digest(
                config,
                "feature_contract",
            ),
            "oof_contract_sha256": _contract_digest(config, "oof"),
            "reporting_contract_sha256": _contract_digest(
                config,
                "reporting",
            ),
        },
        "access_ledger": {
            "preflight_cpu_only": True,
            "gpu_initialized": False,
            "fusion_fit_performed": False,
            "environment_files_read": [],
            "metrics_computed": False,
            "metrics_reported": False,
            "component_prediction_files_written": 0,
            "predecessor_deep_audit": {
                "public_validator_call_order": [
                    "s104_direct",
                    "s105_with_nested_s104",
                    "b07",
                ],
                "validator_invocation_counts": {
                    "s104": 2,
                    "s105": 1,
                    "b07": 1,
                },
                "train_or_fit_inputs_read": True,
                "dev_inputs_read": True,
                "dev_prediction_artifacts_read": True,
                "s104": dict(cast(Mapping[str, Any], s104.deep_audit)),
                "s105": dict(cast(Mapping[str, Any], s105.deep_audit)),
                "b07": dict(cast(Mapping[str, Any], b07.deep_audit)),
            },
            "fusion_input_access": {
                "stack_rows_read_by_preflight": False,
                "stack_labels_read_by_preflight": False,
                "fusion_feature_train_read": False,
                "fusion_feature_dev_read": False,
                "predecessor_dev_predictions_reused_as_components": False,
                "b07_dev_predictions_reused_as_component": False,
                "calibration_read": False,
                "locked_test_read": False,
            },
        },
        "next_action": (
            "future S106 runner may read only stack after revalidating this "
            "lock and all predecessor/source hashes"
        ),
    }
    outputs = _mapping(config["outputs"], "outputs")
    if output_path is None:
        destination = _resolve_output(
            root,
            outputs["execution_lock"],
            "outputs.execution_lock",
        )
    else:
        lexical_output = (
            output_path if output_path.is_absolute() else root / output_path
        )
        try:
            relative_output = lexical_output.relative_to(root).as_posix()
        except ValueError as error:
            raise S106ContractError(
                "output override is outside the workspace"
            ) from error
        destination = _resolve_output(root, relative_output, "output override")

    # Recheck the evidence edges immediately before the first output write.
    if (
        sha256_file(resolved_config) != config_sha
        or sha256_file(root / s104.commit_path) != s104.commit_sha256
        or sha256_file(root / s105.commit_path) != s105.commit_sha256
        or sha256_file(root / b07.commit_path) != b07.commit_sha256
        or b07.registry_path is None
        or b07.registry_sha256 is None
        or sha256_file(root / b07.registry_path) != b07.registry_sha256
        or sha256_file(root / stack.path) != stack.sha256
    ):
        raise S106ContractError("S106 preflight evidence changed before publication")
    validate_predecessor_canonical_sources(
        workspace_root=root,
        execution_lock=payload,
    )
    publish_immutable_json(destination, payload)
    return payload, destination


__all__ = [
    "ALLOWED_SPLITS",
    "ARTIFACT_LAYOUT",
    "COMPONENTS",
    "CONFIG_SCHEMA_VERSION",
    "EXACT_FEATURES",
    "EXECUTION_LOCK_SCHEMA_VERSION",
    "FORBIDDEN_EXACT_FEATURES",
    "FORBIDDEN_FEATURE_FRAGMENTS",
    "FORBIDDEN_SPLITS",
    "OOF_FOLD_COUNT",
    "OOF_NAMESPACE",
    "PredecessorEvidence",
    "S106ContractError",
    "SEEDS",
    "StackSource",
    "aggregate_component_probabilities",
    "alignment_confidence_features",
    "assert_s106_split",
    "assign_speaker_fold",
    "canonical_json_bytes",
    "load_config",
    "load_json_strict",
    "prepare_s106_execution",
    "publish_immutable_json",
    "sha256_file",
    "validate_component_join",
    "validate_config",
    "validate_feature_columns",
    "validate_implementation_sources",
    "validate_oof_assignments",
    "validate_predecessors",
    "validate_predecessor_canonical_sources",
]
