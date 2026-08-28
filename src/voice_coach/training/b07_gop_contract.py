"""Fail-closed static contract for the B07 acoustic-posterior GOP experiment."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Final

import yaml  # type: ignore[import-untyped]

from voice_coach.features.gop import PHONE_INVENTORY


CONFIG_SCHEMA_VERSION: Final = 1
EXPERIMENT_ID: Final = "aihub71490_b07_gop_acoustic_posterior_v1"
SEEDS: Final = (42, 43, 44)
FOLD_COUNT: Final = 5
FOLD_NAMESPACE: Final = "voice-coach.aihub71490.b07-gop-speaker-fold.v1"
TEMPERATURES: Final = (0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0)
REGULARIZATION_CS: Final = (0.01, 0.1, 1.0, 10.0)
TEMPERATURE_VALIDATION_FOLDS: Final = (0,)
DETECTOR_VALIDATION_FOLDS: Final = (1, 2, 3, 4)
CATEGORICAL_FEATURES: Final = (
    "target_label",
    "previous_target_label",
    "next_target_label",
)
EXPLICIT_NUMERIC_FEATURES: Final = (
    "oracle_duration_seconds",
    "segment_rms",
    "segment_zero_crossing_rate",
)
GOP_NUMERIC_FEATURES: Final = (
    "target_log_posterior",
    "gop_margin",
    "posterior_entropy",
    "target_is_top1",
)
ALL_NUMERIC_FEATURES: Final = EXPLICIT_NUMERIC_FEATURES + GOP_NUMERIC_FEATURES
PRIMARY_L1: Final = ("Chinese", "Japanese")
PRIMARY_GENDER: Final = ("F", "M")
EXPECTED_ROWS: Final = {
    "train": {
        "total": 1_808_724,
        "match": 1_701_622,
        "error": 107_102,
        "primary_total": 1_776_049,
        "primary_match": 1_671_012,
        "primary_error": 105_037,
    },
    "dev": {
        "total": 424_315,
        "match": 387_203,
        "error": 37_112,
        "primary_total": 423_820,
        "primary_match": 386_747,
        "primary_error": 37_073,
    },
}
ARTIFACT_LAYOUT: Final = (
    "execution_lock.json",
    "seed_42/acoustic_checkpoint.json",
    "seed_42/error_checkpoint.json",
    "seed_42/inner_selection_trace.json",
    "seed_42/dev_predictions.parquet",
    "seed_42/metrics.json",
    "seed_42/slices.json",
    "seed_43/acoustic_checkpoint.json",
    "seed_43/error_checkpoint.json",
    "seed_43/inner_selection_trace.json",
    "seed_43/dev_predictions.parquet",
    "seed_43/metrics.json",
    "seed_43/slices.json",
    "seed_44/acoustic_checkpoint.json",
    "seed_44/error_checkpoint.json",
    "seed_44/inner_selection_trace.json",
    "seed_44/dev_predictions.parquet",
    "seed_44/metrics.json",
    "seed_44/slices.json",
    "aggregate_report.json",
    "paired_explicit_refit_control_comparison.json",
)
CANONICAL_SOURCE_PATHS: Final = {
    "explicit_baseline_config": "configs/experiments/explicit_baseline_v1.yaml",
    "wavlm_cache_contract": "src/voice_coach/reproducibility/wavlm_cache.py",
    "gop_feature_implementation": "src/voice_coach/features/gop.py",
    "b07_static_contract": "src/voice_coach/training/b07_gop_contract.py",
    "b07_training_implementation": "src/voice_coach/training/b07_gop_training.py",
    "experiment_registry_implementation": "src/voice_coach/experiments/registry.py",
    "b07_prepare_cli": "scripts/prepare_b07_gop_acoustic_posterior.py",
    "b07_train_cli": "scripts/train_b07_gop_acoustic_posterior.py",
    "project_metadata": "pyproject.toml",
    "scorer_dependency_lock": "requirements/scorer-lock-linux-aarch64-cu130.txt",
}
SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")


class B07ContractError(ValueError):
    """Raised before protected data access when the B07 contract is invalid."""


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
            raise B07ContractError(f"YAML contains duplicate key: {key!r}")
        output[key] = loader.construct_object(value_node, deep=deep)
    return output


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise B07ContractError(f"{name} must be a mapping")
    return value


def _sequence(value: object, name: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise B07ContractError(f"{name} must be a list")
    return value


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise B07ContractError(f"{name} must equal {expected!r}")


def _exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    name: str,
) -> None:
    if set(value) != expected:
        raise B07ContractError(
            f"{name} keys differ: expected {sorted(expected)!r}, got {sorted(value)!r}"
        )


def _sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise B07ContractError(f"{name} must be a lowercase SHA-256")
    return value


def _safe_relative_path(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise B07ContractError(f"{name} must be a non-empty path")
    path = Path(value)
    if (
        path.is_absolute()
        or value != path.as_posix()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(part.startswith(".env") for part in path.parts)
    ):
        raise B07ContractError(f"{name} must be a safe workspace-relative path")
    return value


def _reject_nonfinite_tree(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise B07ContractError(f"{name} contains a non-finite number")
    if isinstance(value, Mapping):
        for nested in value.values():
            _reject_nonfinite_tree(nested, name)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_nonfinite_tree(nested, name)


def load_config(path: Path) -> dict[str, Any]:
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except B07ContractError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise B07ContractError("B07 config is not valid UTF-8 YAML") from error
    document = dict(_mapping(value, "B07 config"))
    _reject_nonfinite_tree(document, "B07 config")
    return document


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise B07ContractError(f"JSON contains duplicate key: {key!r}")
        output[key] = value
    return output


def load_json_strict(path: Path, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_json_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                B07ContractError(f"{name} contains {token}")
            ),
        )
    except B07ContractError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise B07ContractError(f"{name} is not valid UTF-8 JSON") from error
    _reject_nonfinite_tree(value, name)
    return _mapping(value, name)


def canonical_json_bytes(value: object, *, pretty: bool = True) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
            allow_nan=False,
        )
        + ("\n" if pretty else "")
    ).encode()


def sha256_file(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise B07ContractError(f"required regular file is absent: {path}")
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
        raise B07ContractError(f"file changed while hashing: {path}")
    return digest.hexdigest()


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value, pretty=False)).hexdigest()


def threshold_values(config: Mapping[str, Any]) -> tuple[float, ...]:
    detector = _mapping(config.get("detector"), "detector")
    grid = _mapping(detector.get("threshold_grid"), "detector.threshold_grid")
    _expect(set(grid), {"start", "stop", "step", "stop_inclusive"}, "threshold keys")
    _expect(grid.get("start"), 0.2, "threshold.start")
    _expect(grid.get("stop"), 0.8, "threshold.stop")
    _expect(grid.get("step"), 0.05, "threshold.step")
    _expect(grid.get("stop_inclusive"), True, "threshold.stop_inclusive")
    start = Decimal(str(grid["start"]))
    stop = Decimal(str(grid["stop"]))
    step = Decimal(str(grid["step"]))
    values: list[float] = []
    current = start
    while current <= stop:
        values.append(float(current))
        current += step
    _expect(values[-1], float(stop), "inclusive threshold stop")
    return tuple(values)


def _validate_cache_contract(data: Mapping[str, Any]) -> None:
    wavlm = _mapping(data.get("wavlm_phone_cache"), "data.wavlm_phone_cache")
    explicit = _mapping(
        data.get("explicit_phone_cache"),
        "data.explicit_phone_cache",
    )
    _exact_keys(
        wavlm,
        {
            "path",
            "report_path",
            "report_sha256",
            "content_sha256",
            "content_hash_rule",
            "embedding_model",
            "embedding_dimension",
            "exact_columns",
            "feature_read_allowlist",
            "supervision_read_allowlist",
            "supervision_as_feature_forbidden",
        },
        "data.wavlm_phone_cache",
    )
    _exact_keys(
        explicit,
        {
            "path",
            "report_path",
            "report_sha256",
            "content_sha256",
            "content_hash_rule",
            "exact_columns",
        },
        "data.explicit_phone_cache",
    )
    _expect(
        wavlm.get("path"),
        "data/processed/aihub71490/features/wavlm_base_plus_frozen_v1/phone",
        "wavlm path",
    )
    _expect(
        wavlm.get("report_path"),
        "data/processed/aihub71490/features/wavlm_base_plus_frozen_v1_report.json",
        "wavlm report path",
    )
    _expect(
        wavlm.get("report_sha256"),
        "34da4c3ec48088b88e1e44763868cd15a21b7baaa06a2cb253338866262ef41d",
        "wavlm report SHA",
    )
    _expect(
        wavlm.get("content_sha256"),
        "af210e324fd941ee636b7abcb6ecbd604ebc7cedf16c5caa97a59ab0aade1b2c",
        "wavlm content SHA",
    )
    _sha256(wavlm.get("report_sha256"), "wavlm report SHA")
    _expect(wavlm.get("embedding_dimension"), 768, "embedding dimension")
    _expect(
        wavlm.get("content_hash_rule"),
        "sha256_canonical_name_row_count_shard_sha256_records",
        "wavlm content hash rule",
    )
    _expect(
        wavlm.get("embedding_model"),
        "microsoft/wavlm-base-plus",
        "embedding model",
    )
    _expect(
        tuple(_sequence(wavlm.get("exact_columns"), "wavlm exact columns")),
        (
            "utterance_id_sha256",
            "speaker_id_sha256",
            "split",
            "phone_index",
            "target_label",
            "embedding_mean",
            "binary_error_label",
            "l1",
            "gender",
            "proficiency",
        ),
        "wavlm exact columns",
    )
    _expect(
        tuple(
            _sequence(
                wavlm.get("feature_read_allowlist"),
                "wavlm feature read allowlist",
            )
        ),
        ("target_label", "embedding_mean", "l1", "gender", "proficiency"),
        "wavlm feature read allowlist",
    )
    _expect(
        tuple(
            _sequence(
                wavlm.get("supervision_read_allowlist"),
                "wavlm supervision allowlist",
            )
        ),
        ("binary_error_label",),
        "wavlm supervision allowlist",
    )
    _expect(wavlm.get("supervision_as_feature_forbidden"), True, "label isolation")
    _expect(
        explicit.get("path"),
        "data/processed/aihub71490/features/explicit_phone_train_dev_oracle_v1",
        "explicit path",
    )
    _expect(
        explicit.get("report_path"),
        "data/processed/aihub71490/features/explicit_phone_train_dev_oracle_v1_report.json",
        "explicit report path",
    )
    _expect(
        explicit.get("report_sha256"),
        "c11e7ed85461d9c623aad390ff65b94981c686aaa859494c07a7f569ed29e53a",
        "explicit report SHA",
    )
    _expect(
        explicit.get("content_sha256"),
        "c7356b43f95991ae7c6df7c92b9f2e7cdc3f6b29f951a43cc4297bed29d047e1",
        "explicit content SHA",
    )
    _expect(
        explicit.get("content_hash_rule"),
        "sha256_canonical_name_row_count_shard_sha256_records",
        "explicit content hash rule",
    )
    _sha256(explicit.get("report_sha256"), "explicit report SHA")
    _expect(
        tuple(_sequence(explicit.get("exact_columns"), "explicit exact columns")),
        (
            "utterance_id_sha256",
            "speaker_id_sha256",
            "split",
            "phone_index",
            "target_label",
            "binary_error_label",
            "previous_target_label",
            "next_target_label",
            "oracle_duration_seconds",
            "segment_rms",
            "segment_zero_crossing_rate",
            "l1",
            "gender",
            "proficiency",
        ),
        "explicit exact columns",
    )


def validate_config(config: Mapping[str, Any]) -> None:
    """Validate all preregistered values without resolving protected inputs."""

    _exact_keys(
        config,
        {
            "schema_version",
            "experiment",
            "canonical_sources",
            "data",
            "posterior",
            "detector",
            "evaluation",
            "resource_contract",
            "artifacts",
            "execution",
        },
        "B07 config",
    )
    _expect(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "schema_version")
    experiment = _mapping(config.get("experiment"), "experiment")
    _exact_keys(
        experiment,
        {
            "id",
            "stage",
            "task",
            "status",
            "development_only",
            "oracle_alignment",
            "deployable",
            "realized_phone_independent",
            "canonical_execution_allowed_by_preflight",
        },
        "experiment",
    )
    _expect(experiment.get("id"), EXPERIMENT_ID, "experiment.id")
    _expect(
        experiment.get("stage"),
        "b07_realized_free_gop_acoustic_posterior",
        "experiment.stage",
    )
    _expect(experiment.get("task"), "phone_binary_error", "experiment.task")
    _expect(experiment.get("development_only"), True, "development_only")
    _expect(experiment.get("oracle_alignment"), True, "oracle_alignment")
    _expect(experiment.get("deployable"), False, "deployable")
    _expect(
        experiment.get("realized_phone_independent"),
        True,
        "realized_phone_independent",
    )
    execution = _mapping(config.get("execution"), "execution")
    permission_pair = (
        experiment.get("canonical_execution_allowed_by_preflight"),
        execution.get("canonical_run_permitted_in_this_change"),
    )
    if permission_pair == (False, False):
        _expect(
            experiment.get("status"),
            "production_ready_not_executed",
            "static experiment.status",
        )
    elif permission_pair == (True, True):
        _expect(
            experiment.get("status"),
            "approved_for_canonical_execution",
            "approved experiment.status",
        )
    else:
        raise B07ContractError("mixed canonical execution permission mode is forbidden")

    sources = _mapping(config.get("canonical_sources"), "canonical_sources")
    _exact_keys(
        sources,
        set(CANONICAL_SOURCE_PATHS),
        "canonical_sources",
    )
    source_paths: list[str] = []
    for role, raw in sources.items():
        record = _mapping(raw, f"canonical_sources.{role}")
        _exact_keys(record, {"path", "sha256"}, f"canonical_sources.{role}")
        source_paths.append(
            _safe_relative_path(
                record.get("path"),
                f"canonical_sources.{role}.path",
            )
        )
        _expect(
            record.get("path"),
            CANONICAL_SOURCE_PATHS[role],
            f"canonical_sources.{role}.path",
        )
        _sha256(record.get("sha256"), f"canonical_sources.{role}.sha256")
    if len(source_paths) != len(set(source_paths)):
        raise B07ContractError("canonical source paths must be unique")

    data = _mapping(config.get("data"), "data")
    _exact_keys(
        data,
        {
            "allowed_splits",
            "fit_splits",
            "metric_splits",
            "forbidden_splits",
            "environment_files_allowed",
            "raw_manifest_access_allowed",
            "stack_access_allowed",
            "calibration_access_allowed",
            "locked_test_access_allowed",
            "wavlm_phone_cache",
            "explicit_phone_cache",
            "expected_rows",
            "exact_phone_inventory",
            "join",
            "forbidden_columns",
        },
        "data",
    )
    _expect(tuple(data.get("allowed_splits", ())), ("train", "dev"), "allowed splits")
    _expect(tuple(data.get("fit_splits", ())), ("train",), "fit splits")
    _expect(tuple(data.get("metric_splits", ())), ("dev",), "metric splits")
    _expect(
        tuple(data.get("forbidden_splits", ())),
        ("stack", "calibration", "locked_test"),
        "forbidden splits",
    )
    for key in (
        "environment_files_allowed",
        "raw_manifest_access_allowed",
        "stack_access_allowed",
        "calibration_access_allowed",
        "locked_test_access_allowed",
    ):
        _expect(data.get(key), False, f"data.{key}")
    _validate_cache_contract(data)
    _expect(
        dict(_mapping(data.get("expected_rows"), "expected rows")),
        EXPECTED_ROWS,
        "expected rows",
    )
    _expect(
        tuple(_sequence(data.get("exact_phone_inventory"), "inventory")),
        PHONE_INVENTORY,
        "phone inventory",
    )
    join = _mapping(data.get("join"), "data.join")
    _exact_keys(
        join,
        {
            "exact_key",
            "exact_universe_required",
            "order_only_join_forbidden",
            "key_arrays_must_match_exactly_in_each_streamed_batch",
            "row_order_mismatch_policy",
            "duplicate_key_policy",
            "exact_match_columns",
            "label_source",
        },
        "data.join",
    )
    _expect(
        tuple(join.get("exact_key", ())),
        ("utterance_id_sha256", "phone_index"),
        "join key",
    )
    _expect(join.get("exact_universe_required"), True, "exact universe")
    _expect(join.get("order_only_join_forbidden"), True, "order-only join")
    _expect(
        join.get("key_arrays_must_match_exactly_in_each_streamed_batch"),
        True,
        "streamed key equality",
    )
    _expect(
        join.get("row_order_mismatch_policy"),
        "fail_closed_before_embedding_feature_use",
        "row mismatch policy",
    )
    _expect(
        tuple(join.get("exact_match_columns", ())),
        ("speaker_id_sha256", "split", "target_label", "binary_error_label"),
        "exact join match columns",
    )
    _expect(join.get("duplicate_key_policy"), "fail", "duplicate key policy")
    _expect(join.get("label_source"), "explicit_phone_cache", "label source")
    forbidden_values = tuple(
        _sequence(data.get("forbidden_columns"), "forbidden columns")
    )
    _expect(
        forbidden_values,
        (
            "realized_phone",
            "realized_phone_identity",
            "realized_phone_id",
            "realized_phoneme",
            "realized_label",
            "phone_alignment_operation",
            "alignment_operation",
            "error_tag",
            "error_type",
            "substitution_tag",
            "deletion_tag",
            "insertion_tag",
            "raw_audio_path",
            "raw_locator",
            "source_path",
            "audio_path",
            "label_path",
        ),
        "forbidden columns",
    )
    forbidden_columns = set(forbidden_values)
    for fragment in ("realized", "alignment_operation", "error_tag", "audio_path"):
        if not any(fragment in value for value in forbidden_columns):
            raise B07ContractError(f"forbidden columns omit {fragment!r}")

    posterior = _mapping(config.get("posterior"), "posterior")
    _exact_keys(
        posterior,
        {
            "class_count",
            "prototype_fit_rows",
            "prototype_fit_scope",
            "row_normalization",
            "prototype_formula",
            "similarity",
            "prior",
            "logits",
            "fold_count",
            "fold_unit",
            "fold_namespace",
            "fold_assignment_rule",
            "seed_binding",
            "inner_validation_rule",
            "train_validation_speaker_overlap_maximum",
            "nonempty_training_and_validation_required_per_fold",
            "every_train_row_exactly_one_oof_feature",
            "every_class_required_in_each_training_complement",
            "temperature_grid",
            "temperature_selection",
            "exact_features",
            "final_refit",
        },
        "posterior",
    )
    _expect(posterior.get("class_count"), len(PHONE_INVENTORY), "class count")
    _expect(
        posterior.get("prototype_fit_rows"),
        "binary_error_label_equals_zero_only",
        "prototype rows",
    )
    _expect(posterior.get("prototype_fit_scope"), "primary_train_only", "fit scope")
    _expect(posterior.get("row_normalization"), "l2", "row normalization")
    _expect(
        posterior.get("prototype_formula"),
        "l2_normalize_mean_of_l2_normalized_rows",
        "prototype formula",
    )
    _expect(posterior.get("similarity"), "cosine", "similarity")
    _expect(posterior.get("prior"), "uniform", "prior")
    _expect(
        posterior.get("logits"),
        "cosine_divided_by_temperature",
        "posterior logits",
    )
    _expect(posterior.get("fold_count"), FOLD_COUNT, "fold count")
    _expect(posterior.get("fold_namespace"), FOLD_NAMESPACE, "fold namespace")
    _expect(posterior.get("fold_unit"), "speaker_id_sha256", "fold unit")
    _expect(
        posterior.get("fold_assignment_rule"),
        "sha256(namespace_colon_seed_colon_speaker)_first_8_bytes_big_endian_mod_5",
        "fold assignment",
    )
    _expect(
        posterior.get("seed_binding"),
        "each_evaluation_seed_binds_its_own_five_fold_assignment",
        "seed binding",
    )
    _expect(
        posterior.get("inner_validation_rule"),
        "each_fold_exactly_once_as_validation_other_four_as_training",
        "inner validation rule",
    )
    _expect(
        posterior.get("train_validation_speaker_overlap_maximum"),
        0,
        "speaker overlap",
    )
    for key in (
        "nonempty_training_and_validation_required_per_fold",
        "every_train_row_exactly_one_oof_feature",
        "every_class_required_in_each_training_complement",
    ):
        _expect(posterior.get(key), True, f"posterior.{key}")
    _expect(
        tuple(posterior.get("temperature_grid", ())),
        TEMPERATURES,
        "temperature grid",
    )
    selection = _mapping(
        posterior.get("temperature_selection"),
        "temperature selection",
    )
    _exact_keys(
        selection,
        {
            "split",
            "validation_fold_ids",
            "training_fold_ids",
            "evidence",
            "rows",
            "metric",
            "direction",
            "tie_break",
            "final_dev_temperature",
            "detector_outer_validation_nested_temperature",
        },
        "posterior.temperature_selection",
    )
    _expect(
        selection.get("split"),
        "primary_train_inner_speaker_validation",
        "temperature split",
    )
    _expect(
        tuple(selection.get("validation_fold_ids", ())),
        TEMPERATURE_VALIDATION_FOLDS,
        "temperature folds",
    )
    _expect(selection.get("rows"), "match_only", "temperature rows")
    _expect(
        selection.get("metric"),
        "multiclass_negative_log_likelihood",
        "temperature metric",
    )
    _expect(selection.get("tie_break"), "smaller_temperature", "temperature tie")
    _expect(selection.get("direction"), "minimize", "temperature direction")
    _expect(
        tuple(selection.get("training_fold_ids", ())),
        DETECTOR_VALIDATION_FOLDS,
        "temperature training folds",
    )
    _expect(
        selection.get("evidence"),
        "fold_0_oof_match_rows_only",
        "temperature evidence",
    )
    final_temperature = _mapping(
        selection.get("final_dev_temperature"),
        "final dev temperature",
    )
    _exact_keys(
        final_temperature,
        {"validation_fold_ids", "prototype_training_fold_ids"},
        "final dev temperature",
    )
    _expect(
        tuple(final_temperature.get("validation_fold_ids", ())),
        (0,),
        "final temperature validation folds",
    )
    _expect(
        tuple(final_temperature.get("prototype_training_fold_ids", ())),
        DETECTOR_VALIDATION_FOLDS,
        "final temperature prototype folds",
    )
    nested_temperature = _mapping(
        selection.get("detector_outer_validation_nested_temperature"),
        "nested temperature",
    )
    _exact_keys(
        nested_temperature,
        {
            "for_outer_fold_ids",
            "validation_fold_ids",
            "prototype_training_rule",
            "outer_validation_embedding_label_or_speaker_access",
        },
        "nested temperature",
    )
    _expect(
        tuple(nested_temperature.get("for_outer_fold_ids", ())),
        DETECTOR_VALIDATION_FOLDS,
        "nested outer folds",
    )
    _expect(
        tuple(nested_temperature.get("validation_fold_ids", ())),
        (0,),
        "nested temperature validation fold",
    )
    _expect(
        nested_temperature.get("prototype_training_rule"),
        "all_folds_except_temperature_fold_0_and_outer_validation_fold",
        "nested temperature prototype rule",
    )
    _expect(
        nested_temperature.get("outer_validation_embedding_label_or_speaker_access"),
        "forbidden",
        "nested temperature outer access",
    )
    _expect(
        tuple(posterior.get("exact_features", ())),
        GOP_NUMERIC_FEATURES,
        "GOP features",
    )
    posterior_refit = _mapping(posterior.get("final_refit"), "posterior final refit")
    _exact_keys(
        posterior_refit,
        {"prototypes", "dev_projection"},
        "posterior final refit",
    )
    _expect(
        posterior_refit.get("prototypes"),
        "all_primary_train_match_rows",
        "final prototype scope",
    )
    _expect(
        posterior_refit.get("dev_projection"),
        "selected_temperature_and_full_train_prototypes",
        "final dev projection",
    )

    detector = _mapping(config.get("detector"), "detector")
    _exact_keys(
        detector,
        {
            "estimator",
            "solver",
            "maximum_iterations",
            "tolerance",
            "categorical_features",
            "explicit_numeric_features",
            "gop_numeric_features",
            "preprocessing",
            "regularization_c_grid",
            "threshold_grid",
            "selection",
            "paired_control",
            "final_refit",
            "serialized_checkpoint_exact_fields",
        },
        "detector",
    )
    _expect(
        detector.get("estimator"),
        "balanced_l2_logistic_regression",
        "detector estimator",
    )
    _expect(detector.get("solver"), "lbfgs", "detector solver")
    _expect(detector.get("maximum_iterations"), 2000, "maximum iterations")
    _expect(detector.get("tolerance"), 0.00000001, "detector tolerance")
    _expect(
        tuple(detector.get("categorical_features", ())),
        CATEGORICAL_FEATURES,
        "categorical features",
    )
    _expect(
        tuple(detector.get("explicit_numeric_features", ())),
        EXPLICIT_NUMERIC_FEATURES,
        "explicit numeric features",
    )
    _expect(
        tuple(detector.get("gop_numeric_features", ())),
        GOP_NUMERIC_FEATURES,
        "GOP numeric features",
    )
    preprocessing = _mapping(detector.get("preprocessing"), "preprocessing")
    _exact_keys(
        preprocessing,
        {
            "numeric_imputation",
            "numeric_imputation_fit_scope",
            "numeric_standardization",
            "numeric_standardization_fit_scope",
            "zero_scale_replacement",
            "categorical_missing_token",
            "categorical_unknown_token",
            "categorical_vocabulary_fit_scope",
            "category_order",
        },
        "preprocessing",
    )
    _expect(preprocessing.get("numeric_imputation"), "median", "imputation")
    _expect(
        preprocessing.get("numeric_imputation_fit_scope"),
        "fold_training_rows_only",
        "imputation scope",
    )
    _expect(
        preprocessing.get("numeric_standardization"),
        "population_mean_and_scale",
        "standardization",
    )
    _expect(
        preprocessing.get("numeric_standardization_fit_scope"),
        "fold_training_rows_only",
        "standardization scope",
    )
    _expect(preprocessing.get("zero_scale_replacement"), 1.0, "zero scale")
    _expect(
        preprocessing.get("categorical_missing_token"),
        "__MISSING__",
        "missing token",
    )
    _expect(
        preprocessing.get("categorical_unknown_token"),
        "__UNKNOWN__",
        "unknown token",
    )
    _expect(
        preprocessing.get("categorical_vocabulary_fit_scope"),
        "fold_training_rows_only",
        "vocabulary scope",
    )
    _expect(preprocessing.get("category_order"), "sorted_utf8", "category order")
    _expect(
        tuple(detector.get("regularization_c_grid", ())),
        REGULARIZATION_CS,
        "C grid",
    )
    _expect(
        threshold_values(config),
        tuple(round(0.2 + 0.05 * index, 2) for index in range(13)),
        "threshold values",
    )
    detector_selection = _mapping(detector.get("selection"), "detector selection")
    _exact_keys(
        detector_selection,
        {
            "split",
            "validation_fold_ids",
            "upstream_temperature_selection_fold_ids",
            "upstream_temperature_selection_is_disjoint_from_detector_validation",
            "nested_acoustic_feature_rule",
            "per_outer_fold_candidate_fit",
            "candidate_probability_aggregation",
            "candidate_metric_scope",
            "candidate_fits_per_c",
            "class_weight",
            "class_weight_fit_scope",
            "threshold_application",
            "fold_rule",
            "each_detector_validation_fold_exactly_once",
            "temperature_role_fold_excluded_from_detector_metric",
            "train_validation_speaker_overlap_maximum",
            "metric",
            "metric_zero_division",
            "direction",
            "tie_break_order",
        },
        "detector selection",
    )
    _expect(
        detector_selection.get("split"),
        "primary_train_inner_speaker_validation",
        "detector selection split",
    )
    _expect(
        tuple(detector_selection.get("validation_fold_ids", ())),
        DETECTOR_VALIDATION_FOLDS,
        "detector validation folds",
    )
    _expect(
        tuple(detector_selection.get("upstream_temperature_selection_fold_ids", ())),
        TEMPERATURE_VALIDATION_FOLDS,
        "temperature isolation folds",
    )
    _expect(
        detector_selection.get(
            "upstream_temperature_selection_is_disjoint_from_detector_validation"
        ),
        True,
        "temperature isolation",
    )
    nested_rule = _mapping(
        detector_selection.get("nested_acoustic_feature_rule"),
        "nested acoustic feature rule",
    )
    _expect(
        dict(nested_rule),
        {
            "outer_validation_rows": (
                "prototype_fit_on_all_primary_match_train_except_outer_fold"
            ),
            "detector_training_row": (
                "prototype_fit_on_primary_match_train_except_outer_fold_and_that_rows_acoustic_fold"
            ),
            "detector_training_row_fold_equals_temperature_fold": (
                "prototype_fit_on_primary_match_train_except_outer_fold_and_temperature_fold"
            ),
            "temperature": (
                "outer_fold_specific_selected_on_fold_0_with_outer_fold_excluded"
            ),
            "every_prototype_class_nonempty_required": True,
            "outer_validation_speaker_embedding_target_and_binary_label_in_upstream_fit": (
                "forbidden"
            ),
            "outer_validation_speaker_in_preprocessing_or_detector_fit": "forbidden",
        },
        "nested acoustic feature rule",
    )
    per_fold = _mapping(
        detector_selection.get("per_outer_fold_candidate_fit"),
        "per outer fold candidate fit",
    )
    _expect(
        dict(per_fold),
        {
            "fold_1": [0, 2, 3, 4],
            "fold_2": [0, 1, 3, 4],
            "fold_3": [0, 1, 2, 4],
            "fold_4": [0, 1, 2, 3],
        },
        "per outer fold candidate fit",
    )
    _expect(
        detector_selection.get("candidate_probability_aggregation"),
        "row_pooled_outer_oof_folds_1_through_4",
        "candidate probability aggregation",
    )
    _expect(
        detector_selection.get("candidate_metric_scope"),
        "all_primary_train_rows_in_outer_validation_folds_1_through_4",
        "candidate metric scope",
    )
    _expect(detector_selection.get("candidate_fits_per_c"), 4, "candidate fits")
    _expect(detector_selection.get("class_weight"), "balanced", "class weight")
    _expect(
        detector_selection.get("class_weight_fit_scope"),
        "each_outer_fold_detector_training_labels_only",
        "class weight scope",
    )
    _expect(
        detector_selection.get("threshold_application"),
        "pooled_outer_oof_probabilities",
        "threshold application",
    )
    _expect(
        detector_selection.get("fold_rule"),
        "same_seed_bound_five_folds_as_acoustic_cross_fit",
        "detector fold rule",
    )
    for key in (
        "each_detector_validation_fold_exactly_once",
        "temperature_role_fold_excluded_from_detector_metric",
    ):
        _expect(detector_selection.get(key), True, f"detector selection {key}")
    _expect(
        detector_selection.get("train_validation_speaker_overlap_maximum"),
        0,
        "detector speaker overlap",
    )
    _expect(
        detector_selection.get("metric"),
        "phone_macro_f1",
        "detector metric",
    )
    _expect(detector_selection.get("metric_zero_division"), 0.0, "zero division")
    _expect(detector_selection.get("direction"), "maximize", "detector direction")
    _expect(
        tuple(detector_selection.get("tie_break_order", ())),
        ("smaller_c", "threshold_closest_to_0.5", "smaller_threshold"),
        "detector tie",
    )
    paired = _mapping(detector.get("paired_control"), "paired control")
    _exact_keys(
        paired,
        {
            "id",
            "role",
            "canonical_b03_claim_allowed",
            "canonical_b03_predictions_read",
            "same_train_dev_universe",
            "same_seed_folds_and_selection_contract",
            "features",
        },
        "paired control",
    )
    _expect(
        paired.get("id"),
        "within_run_explicit_feature_refit_control",
        "paired control id",
    )
    _expect(paired.get("canonical_b03_claim_allowed"), False, "B03 claim")
    _expect(paired.get("canonical_b03_predictions_read"), False, "B03 reads")
    _expect(
        paired.get("role"),
        "b03_feature_family_control_not_canonical_b03_checkpoint_or_metric",
        "paired control role",
    )
    _expect(paired.get("same_train_dev_universe"), True, "paired universe")
    _expect(
        paired.get("same_seed_folds_and_selection_contract"),
        True,
        "paired selection contract",
    )
    _expect(
        paired.get("features"),
        "categorical_plus_explicit_numeric",
        "paired features",
    )
    final_refit = _mapping(detector.get("final_refit"), "detector final refit")
    _expect(
        dict(final_refit),
        {
            "feature_rows": "all_primary_train_cross_fit_rows",
            "preprocessing_fit_scope": "all_primary_train_cross_fit_rows",
            "estimator_fit_scope": "all_primary_train_cross_fit_rows",
            "class_weight_fit_scope": "all_primary_train_labels",
        },
        "detector final refit",
    )
    checkpoint_fields = _mapping(
        detector.get("serialized_checkpoint_exact_fields"),
        "checkpoint fields",
    )
    _exact_keys(checkpoint_fields, {"acoustic", "error"}, "checkpoint fields")
    _expect(
        tuple(checkpoint_fields.get("acoustic", ())),
        (
            "inventory",
            "embedding_dimension",
            "selected_temperature",
            "full_primary_train_match_prototypes",
            "fold_namespace",
            "seed",
            "input_column_contract",
        ),
        "acoustic checkpoint fields",
    )
    _expect(
        tuple(checkpoint_fields.get("error", ())),
        (
            "feature_order",
            "categorical_feature_order",
            "categorical_vocabularies",
            "categorical_missing_token",
            "categorical_unknown_token",
            "numeric_feature_order",
            "numeric_imputation_medians",
            "numeric_standardization_means",
            "numeric_standardization_scales",
            "class_order",
            "coefficients",
            "intercept",
            "selected_c",
            "selected_threshold",
            "solver",
            "seed",
            "explicit_refit_control",
        ),
        "error checkpoint fields",
    )

    evaluation = _mapping(config.get("evaluation"), "evaluation")
    _exact_keys(
        evaluation,
        {
            "seeds",
            "primary_scope",
            "primary_metric",
            "secondary_metric",
            "seed_summary",
            "slices",
            "slice_phone_macro_f1_zero_division",
            "adoption_gate",
            "dev_access",
        },
        "evaluation",
    )
    _expect(tuple(evaluation.get("seeds", ())), SEEDS, "seeds")
    scope = _mapping(evaluation.get("primary_scope"), "primary scope")
    _expect(tuple(scope.get("l1", ())), PRIMARY_L1, "primary L1")
    _expect(tuple(scope.get("gender", ())), PRIMARY_GENDER, "primary gender")
    _expect(evaluation.get("primary_metric"), "phone_macro_f1", "primary metric")
    _expect(evaluation.get("secondary_metric"), "error_auprc", "secondary metric")
    _expect(
        dict(_mapping(evaluation.get("seed_summary"), "seed summary")),
        {
            "center": "arithmetic_mean",
            "dispersion": "sample_standard_deviation",
            "ddof": 1,
        },
        "seed summary",
    )
    _expect(
        tuple(evaluation.get("slices", ())),
        ("l1", "gender", "proficiency", "target_label"),
        "evaluation slices",
    )
    _expect(
        evaluation.get("slice_phone_macro_f1_zero_division"),
        0.0,
        "slice zero division",
    )
    gate = _mapping(evaluation.get("adoption_gate"), "adoption gate")
    _expect(
        dict(gate),
        {
            "metric": "mean_seed_phone_macro_f1",
            "operator": "greater_than_or_equal",
            "value": 0.6,
        },
        "adoption gate",
    )
    access = _mapping(evaluation.get("dev_access"), "dev access")
    _exact_keys(
        access,
        {
            "hyperparameter_selection_count",
            "metric_open_count_per_seed",
            "predict_call_count_per_seed",
            "fit_on_dev_forbidden",
            "one_prediction_pass_outputs",
        },
        "dev access",
    )
    _expect(access.get("hyperparameter_selection_count"), 0, "dev H")
    _expect(access.get("metric_open_count_per_seed"), 1, "dev M")
    _expect(access.get("predict_call_count_per_seed"), 1, "dev predict")
    _expect(access.get("fit_on_dev_forbidden"), True, "dev fit")
    _expect(
        dict(
            _mapping(
                access.get("one_prediction_pass_outputs"),
                "one prediction pass outputs",
            )
        ),
        {"all_dev_audit_rows": 424_315, "primary_metric_rows": 423_820},
        "one prediction pass outputs",
    )

    artifacts = _mapping(config.get("artifacts"), "artifacts")
    _exact_keys(
        artifacts,
        {
            "scope",
            "generation_root",
            "registry_path",
            "commit_path",
            "generation_id_rule",
            "immutable_generation",
            "completed_registry_before_commit",
            "failure_writes_registry_only",
            "commit_overwrite_forbidden",
            "exact_generation_layout",
            "schema_versions",
            "deep_validation",
        },
        "artifacts",
    )
    _expect(artifacts.get("scope"), "D1_restricted_internal", "artifact scope")
    _expect(
        artifacts.get("generation_id_rule"),
        "sha256_canonical_records",
        "generation ID rule",
    )
    output_paths = [
        _safe_relative_path(artifacts.get(key), f"artifacts.{key}")
        for key in ("generation_root", "registry_path", "commit_path")
    ]
    for key in ("generation_root", "registry_path", "commit_path"):
        _safe_relative_path(artifacts.get(key), f"artifacts.{key}")
    for source in source_paths:
        for output in output_paths:
            if (
                source == output
                or source.startswith(f"{output}/")
                or output.startswith(f"{source}/")
            ):
                raise B07ContractError("canonical source and output paths overlap")
    for cache_key in ("wavlm_phone_cache", "explicit_phone_cache"):
        cache = _mapping(data.get(cache_key), cache_key)
        for input_key in ("path", "report_path"):
            input_path = str(cache[input_key])
            for output in output_paths:
                if (
                    input_path == output
                    or input_path.startswith(f"{output}/")
                    or output.startswith(f"{input_path}/")
                ):
                    raise B07ContractError("input cache and output paths overlap")
    _expect(
        tuple(artifacts.get("exact_generation_layout", ())),
        ARTIFACT_LAYOUT,
        "artifact layout",
    )
    for key in (
        "immutable_generation",
        "completed_registry_before_commit",
        "failure_writes_registry_only",
        "commit_overwrite_forbidden",
    ):
        _expect(artifacts.get(key), True, f"artifacts.{key}")
    schema_versions = _mapping(
        artifacts.get("schema_versions"),
        "artifact schema versions",
    )
    _expect(
        dict(schema_versions),
        {
            "execution_lock": "voice_coach.b07_gop_execution_lock.v1",
            "acoustic_checkpoint": "voice_coach.b07_gop_acoustic_checkpoint.v1",
            "error_checkpoint": "voice_coach.b07_gop_error_checkpoint.v1",
            "selection_trace": "voice_coach.b07_gop_selection_trace.v1",
            "predictions": "voice_coach.b07_gop_dev_predictions.v1",
            "metrics": "voice_coach.b07_gop_metrics.v1",
            "slices": "voice_coach.b07_gop_slices.v1",
            "aggregate": "voice_coach.b07_gop_aggregate.v1",
            "paired": "voice_coach.b07_gop_paired_explicit_refit_control.v1",
            "registry": "voice_coach.b07_gop_registry.v1",
            "commit": "voice_coach.b07_gop_commit.v1",
        },
        "artifact schema versions",
    )
    deep = _mapping(artifacts.get("deep_validation"), "deep validation")
    _exact_keys(
        deep,
        {
            "cli_flag",
            "recompute_from_input_caches",
            "recompute_selection",
            "recompute_metrics_from_raw_predictions",
            "verify_all_artifact_hashes",
            "verify_registry_hash",
            "verify_commit_chain",
        },
        "deep validation",
    )
    _expect(deep.get("cli_flag"), "--validate-commit-only", "validator flag")
    for key in (
        "recompute_from_input_caches",
        "recompute_selection",
        "recompute_metrics_from_raw_predictions",
        "verify_all_artifact_hashes",
        "verify_registry_hash",
        "verify_commit_chain",
    ):
        _expect(deep.get(key), True, f"deep_validation.{key}")

    resource = _mapping(config.get("resource_contract"), "resource contract")
    _exact_keys(
        resource,
        {
            "parquet_access",
            "record_batch_rows",
            "embedding_arrow_access",
            "embedding_to_pylist_forbidden",
            "embedding_compute_dtype",
            "prototype_accumulator_dtype",
            "posterior_score_dtype",
            "full_embedding_matrix_materialization_forbidden",
            "maximum_embedding_rows_resident",
            "canonical_peak_rss_gate_gib",
            "deep_validator_uses_same_bounded_runner",
            "accelerator",
            "cuda_initialization_forbidden",
            "accelerator_probe_forbidden",
            "torch_import_forbidden",
            "cuda_visible_devices_required",
            "deterministic_cpu_thread_cap",
            "blas_openmp_thread_cap",
            "thread_cap_semantics",
            "sklearn_n_jobs",
            "oversubscription_forbidden",
            "registry_observations",
        },
        "resource contract",
    )
    _expect(
        resource.get("parquet_access"),
        "deterministic_sorted_shards_and_streamed_record_batches",
        "parquet access",
    )
    _expect(resource.get("record_batch_rows"), 8192, "batch rows")
    _expect(
        resource.get("embedding_arrow_access"),
        "fixed_size_list_values_buffer_to_numpy_reshape",
        "embedding Arrow access",
    )
    _expect(resource.get("embedding_to_pylist_forbidden"), True, "embedding pylist")
    _expect(resource.get("embedding_compute_dtype"), "float32", "embedding dtype")
    _expect(
        resource.get("prototype_accumulator_dtype"),
        "float64",
        "prototype accumulator dtype",
    )
    _expect(
        resource.get("posterior_score_dtype"),
        "float64",
        "posterior score dtype",
    )
    _expect(
        resource.get("full_embedding_matrix_materialization_forbidden"),
        True,
        "embedding materialization",
    )
    _expect(resource.get("maximum_embedding_rows_resident"), 8192, "resident rows")
    _expect(resource.get("deep_validator_uses_same_bounded_runner"), True, "validator")
    _expect(resource.get("canonical_peak_rss_gate_gib"), 8.0, "peak RSS gate")
    _expect(resource.get("accelerator"), "none", "accelerator")
    for key in (
        "cuda_initialization_forbidden",
        "accelerator_probe_forbidden",
        "torch_import_forbidden",
        "oversubscription_forbidden",
    ):
        _expect(resource.get(key), True, f"resource.{key}")
    _expect(
        resource.get("cuda_visible_devices_required"),
        "empty_string",
        "CUDA visibility",
    )
    _expect(resource.get("deterministic_cpu_thread_cap"), 4, "CPU thread cap")
    _expect(resource.get("blas_openmp_thread_cap"), 4, "BLAS thread cap")
    _expect(
        resource.get("thread_cap_semantics"),
        "per_native_threadpool_active_limit_not_process_thread_object_count",
        "thread cap semantics",
    )
    _expect(
        resource.get("sklearn_n_jobs"),
        "not_applicable_lbfgs_single_process",
        "sklearn n_jobs",
    )
    _expect(
        tuple(resource.get("registry_observations", ())),
        (
            "elapsed_seconds",
            "peak_rss_bytes",
            "configured_cpu_thread_cap",
            "observed_process_thread_count_at_start",
            "observed_process_thread_count_at_finish",
        ),
        "registry observations",
    )

    execution = _mapping(config.get("execution"), "execution")
    _exact_keys(
        execution,
        {
            "prepare_mode",
            "train_requires_flag",
            "validate_commit_only_reads_no_environment_files",
            "canonical_run_permitted_in_this_change",
            "permission_transition",
            "precommit_validation",
            "commit_only_validation",
        },
        "execution",
    )
    _expect(execution.get("prepare_mode"), "config_only", "prepare mode")
    _expect(
        execution.get("train_requires_flag"),
        "--execute-canonical",
        "train flag",
    )
    _expect(
        execution.get("validate_commit_only_reads_no_environment_files"),
        True,
        "validator environment reads",
    )
    transition = _mapping(
        execution.get("permission_transition"),
        "permission transition",
    )
    _expect(
        dict(transition),
        {
            "static_mode": "both_permission_flags_false",
            "actual_mode": (
                "both_permission_flags_true_and_status_approved_for_canonical_execution"
            ),
            "mixed_mode": "forbidden",
            "config_rehash_and_source_revalidation_required": True,
        },
        "permission transition",
    )
    _expect(
        dict(_mapping(execution.get("precommit_validation"), "precommit validation")),
        {
            "raw_prediction_metric_recompute": True,
            "selection_rule_recompute": True,
            "serialized_error_checkpoint_replay": True,
            "additional_dev_embedding_scan": False,
        },
        "precommit validation",
    )
    _expect(
        dict(
            _mapping(
                execution.get("commit_only_validation"),
                "commit-only validation",
            )
        ),
        {
            "full_bounded_train_selection_dev_recomputation": True,
            "serialized_acoustic_and_error_checkpoint_replay": True,
            "read_only": True,
        },
        "commit-only validation",
    )


def _resolve_regular_file(
    *,
    workspace_root: Path,
    relative: str,
    name: str,
) -> Path:
    _safe_relative_path(relative, name)
    candidate = workspace_root
    for part in Path(relative).parts:
        candidate /= part
        if candidate.is_symlink():
            raise B07ContractError(f"{name} cannot traverse a symlink")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(workspace_root)
    except ValueError as error:
        raise B07ContractError(f"{name} escapes workspace") from error
    if not resolved.is_file() or resolved.is_symlink():
        raise B07ContractError(f"{name} must be a regular non-symlink file")
    return resolved


def validate_implementation_sources(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
) -> tuple[Mapping[str, str], ...]:
    sources = _mapping(config.get("canonical_sources"), "canonical_sources")
    if not sources:
        raise B07ContractError("canonical_sources must not be empty")
    validated: list[Mapping[str, str]] = []
    for role in sorted(sources):
        record = _mapping(sources[role], f"canonical_sources.{role}")
        path_text = _safe_relative_path(
            record.get("path"),
            f"canonical_sources.{role}.path",
        )
        expected = _sha256(
            record.get("sha256"),
            f"canonical_sources.{role}.sha256",
        )
        path = _resolve_regular_file(
            workspace_root=workspace_root,
            relative=path_text,
            name=f"canonical_sources.{role}.path",
        )
        actual = sha256_file(path)
        if actual != expected:
            raise B07ContractError(
                f"canonical source hash mismatch for {role}: {actual} != {expected}"
            )
        validated.append({"role": role, "path": path_text, "sha256": actual})
    return tuple(validated)
