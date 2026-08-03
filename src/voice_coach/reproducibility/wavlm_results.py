"""Strict, privacy-safe rendering of validated WavLM development results."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import tempfile
from typing import Any, NoReturn


HEAD_REPORT_SCHEMA = "voice_coach.wavlm_frozen_head_report.v1"
COMPARISON_REPORT_SCHEMA = "voice_coach.wavlm_frozen_head_comparison.v1"
COMPARISON_RECEIPT_SCHEMA = "voice_coach.wavlm_frozen_head_comparison_receipt.v1"
COMPARISON_COMMIT_SCHEMA = "voice_coach.wavlm_frozen_head_comparison_commit.v1"
OUTPUT_SCHEMA = "voice_coach.wavlm_results_table.v1"
OUTPUT_COMMIT_SCHEMA = "voice_coach.wavlm_results_table_commit.v1"
EXPECTED_SEEDS = (42, 43, 44)
OUTCOMES = (
    "pronunciation_score",
    "fluency_score",
    "comprehensibility_score",
)
PHONE_METRICS = ("macro_f1", "auprc", "event_f1")
UTTERANCE_METRICS = ("qwk", "spearman", "mae", "icc_2_1")
VARIANTS = ("base_plus", "large")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_JSON_BYTES = 32 * 1024 * 1024
FORBIDDEN_PATH_PARTS = frozenset(
    {
        "raw",
        "restricted",
        "stack",
        "calibration",
        "locked",
        "locked-test",
        "locked_test",
    }
)
FORBIDDEN_PATH_PREFIXES = (
    "raw",
    "restricted",
    "stack",
    "calibration",
    "locked",
)
EXPECTED_COMPARISON_SOURCE_PATHS = {
    "config": "configs/experiments/wavlm_frozen_head_comparison_v1.yaml",
    "comparison_script": "scripts/compare_wavlm_frozen_heads.py",
    "comparison_module": "src/voice_coach/evaluation/wavlm_comparison.py",
}


class ResultsValidationError(RuntimeError):
    """Raised when an input cannot support a manuscript result table."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class FileFingerprint:
    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class HeadValidation:
    variant: str
    report: Mapping[str, Any]
    registry: Mapping[str, Any]
    report_file: FileFingerprint
    registry_file: FileFingerprint
    artifacts_by_name: Mapping[str, FileFingerprint]
    protected_paths: frozenset[Path]


@dataclass(frozen=True)
class ComparisonCommit:
    manifest: Mapping[str, Any]
    receipt: Mapping[str, Any]
    report: Mapping[str, Any]
    manifest_file: FileFingerprint
    receipt_file: FileFingerprint
    report_file: FileFingerprint


def _fail(code: str) -> NoReturn:
    raise ResultsValidationError(code)


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _list(value: object, code: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(code)
    return value


def _string(value: object, code: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(code)
    return value


def _boolean(value: object, expected: bool, code: str) -> None:
    if value is not expected:
        _fail(code)


def _integer(value: object, code: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(code)
    if minimum is not None and value < minimum:
        _fail(code)
    return value


def _number(
    value: object,
    code: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(code)
    result = float(value)
    if not math.isfinite(result):
        _fail(code)
    if minimum is not None and result < minimum:
        _fail(code)
    if maximum is not None and result > maximum:
        _fail(code)
    return result


def _sha(value: object, code: str) -> str:
    result = _string(value, code)
    if SHA256_RE.fullmatch(result) is None:
        _fail(code)
    return result


def _same_float(
    actual: object,
    expected: float,
    code: str,
    *,
    tolerance: float = 1e-12,
) -> None:
    value = _number(actual, code)
    if not math.isclose(
        value,
        expected,
        rel_tol=tolerance,
        abs_tol=tolerance,
    ):
        _fail(code)


def _no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            _fail("json_duplicate_key")
        output[key] = value
    return output


def _reject_json_constant(_: str) -> None:
    _fail("json_nonfinite_number")


def _load_json(path: Path) -> Mapping[str, Any]:
    size = path.stat().st_size
    if size > MAX_JSON_BYTES:
        _fail("json_too_large")
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(
                handle,
                object_pairs_hook=_no_duplicate_object,
                parse_constant=_reject_json_constant,
            )
    except ResultsValidationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("json_read_failed")
    _reject_nonfinite_tree(value)
    return _mapping(value, "json_root_not_object")


def _reject_nonfinite_tree(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        _fail("json_nonfinite_number")
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_nonfinite_tree(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_nonfinite_tree(item)


def _path_parts_are_safe(path: Path) -> bool:
    for part in path.parts:
        lowered = part.casefold()
        if (
            lowered in FORBIDDEN_PATH_PARTS
            or any(
                lowered.startswith(f"{prefix}{separator}")
                for prefix in FORBIDDEN_PATH_PREFIXES
                for separator in ("_", "-")
            )
            or lowered.startswith(".env")
        ):
            return False
    return True


def _candidate_inside_workspace(
    root: Path,
    configured: str | Path,
    *,
    base: Path | None = None,
    must_exist: bool,
) -> Path:
    lexical = Path(configured)
    if not _path_parts_are_safe(lexical):
        _fail("forbidden_path")
    candidate = lexical if lexical.is_absolute() else (base or root) / lexical
    if candidate.is_symlink():
        _fail("symlink_path")
    try:
        resolved = candidate.resolve(strict=must_exist)
        relative = resolved.relative_to(root)
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        _fail("path_outside_workspace_or_missing")
    if not _path_parts_are_safe(relative):
        _fail("forbidden_path")
    return resolved


def _existing_file(
    root: Path,
    configured: str | Path,
    *,
    base: Path | None = None,
) -> Path:
    path = _candidate_inside_workspace(
        root,
        configured,
        base=base,
        must_exist=True,
    )
    if not path.is_file():
        _fail("input_not_regular_file")
    return path


def _output_file(root: Path, configured: str | Path) -> Path:
    path = _candidate_inside_workspace(
        root,
        configured,
        must_exist=False,
    )
    if path.exists() and (path.is_symlink() or not path.is_file()):
        _fail("output_not_regular_file")
    return path


def _fingerprint(path: Path) -> FileFingerprint:
    before = path.stat()
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        _fail("file_hash_failed")
    after = path.stat()
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
        _fail("file_changed_while_hashing")
    return FileFingerprint(
        path=path,
        sha256=digest.hexdigest(),
        size_bytes=after.st_size,
    )


def _validate_fingerprint_record(
    root: Path,
    record: object,
    *,
    base: Path | None,
    code_prefix: str,
) -> FileFingerprint:
    item = _mapping(record, f"{code_prefix}_record")
    path = _existing_file(
        root,
        _string(item.get("path"), f"{code_prefix}_path"),
        base=base,
    )
    actual = _fingerprint(path)
    if _sha(item.get("sha256"), f"{code_prefix}_sha256") != actual.sha256:
        _fail(f"{code_prefix}_hash_mismatch")
    if "size_bytes" in item:
        if (
            _integer(
                item.get("size_bytes"),
                f"{code_prefix}_size",
                minimum=0,
            )
            != actual.size_bytes
        ):
            _fail(f"{code_prefix}_size_mismatch")
    return actual


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _mapping_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _validate_binding(
    document: Mapping[str, Any],
    *,
    code_prefix: str,
) -> None:
    binding = _sha(document.get("binding_sha256"), f"{code_prefix}_binding")
    core = dict(document)
    del core["binding_sha256"]
    if binding != _mapping_sha256(core):
        _fail(f"{code_prefix}_binding_mismatch")


def _exact_file_record(
    root: Path,
    record: object,
    *,
    code_prefix: str,
) -> tuple[Mapping[str, Any], FileFingerprint]:
    item = _mapping(record, f"{code_prefix}_record")
    if set(item) != {"path", "sha256", "size_bytes"}:
        _fail(f"{code_prefix}_record_keys")
    return item, _validate_fingerprint_record(
        root,
        item,
        base=None,
        code_prefix=code_prefix,
    )


def _statistics_sha256(report: Mapping[str, Any]) -> str:
    return _mapping_sha256(
        {
            "metrics": report.get("metrics"),
            "primary_comparison": report.get("primary_comparison"),
            "multiplicity": report.get("multiplicity"),
        }
    )


def _validate_comparison_commit(
    root: Path,
    commit_path: Path,
) -> ComparisonCommit:
    manifest_file = _fingerprint(commit_path)
    manifest = _load_json(commit_path)
    if set(manifest) != {
        "schema_version",
        "status",
        "generation_id",
        "comparison_report",
        "execution_receipt",
        "statistics_sha256",
        "binding_sha256",
    }:
        _fail("comparison_commit_keys")
    if (
        manifest.get("schema_version") != COMPARISON_COMMIT_SCHEMA
        or manifest.get("status") != "committed"
    ):
        _fail("comparison_commit_contract")
    generation_id = _sha(
        manifest.get("generation_id"),
        "comparison_commit_generation_id",
    )
    _validate_binding(manifest, code_prefix="comparison_commit")
    report_record, report_file = _exact_file_record(
        root,
        manifest.get("comparison_report"),
        code_prefix="comparison_committed_report",
    )
    receipt_record, receipt_file = _exact_file_record(
        root,
        manifest.get("execution_receipt"),
        code_prefix="comparison_committed_receipt",
    )
    if (
        report_file.path.parent != receipt_file.path.parent
        or report_file.path.parent.name != generation_id
    ):
        _fail("comparison_commit_generation_paths")

    report = _load_json(report_file.path)
    receipt = _load_json(receipt_file.path)
    if set(receipt) != {
        "schema_version",
        "status",
        "generation_id",
        "comparison_report",
        "execution_sources",
        "statistics_sha256",
        "binding_sha256",
    }:
        _fail("comparison_receipt_keys")
    if (
        receipt.get("schema_version") != COMPARISON_RECEIPT_SCHEMA
        or receipt.get("status") != "completed"
        or receipt.get("generation_id") != generation_id
    ):
        _fail("comparison_receipt_contract")
    _validate_binding(receipt, code_prefix="comparison_receipt")
    if receipt.get("comparison_report") != report_record:
        _fail("comparison_receipt_report_binding")
    statistics_sha256 = _sha(
        manifest.get("statistics_sha256"),
        "comparison_commit_statistics_sha256",
    )
    if receipt.get(
        "statistics_sha256"
    ) != statistics_sha256 or statistics_sha256 != _statistics_sha256(report):
        _fail("comparison_statistics_receipt_mismatch")
    if receipt.get("execution_sources") != report.get("sources"):
        _fail("comparison_receipt_source_binding")
    if report_file.sha256 != generation_id:
        _fail("comparison_generation_id_mismatch")
    return ComparisonCommit(
        manifest=manifest,
        receipt=receipt,
        report=report,
        manifest_file=manifest_file,
        receipt_file=receipt_file,
        report_file=report_file,
    )


def _metric(
    metrics: Mapping[str, Any],
    name: str,
    *,
    code_prefix: str,
) -> float:
    if name in {"macro_f1", "auprc", "event_f1"}:
        bounds: tuple[float | None, float | None] = (0.0, 1.0)
    elif name in {"qwk", "spearman"}:
        bounds = (-1.0, 1.0)
    elif name == "mae":
        bounds = (0.0, 4.0)
    else:
        bounds = (None, None)
    return _number(
        metrics.get(name),
        f"{code_prefix}_{name}",
        minimum=bounds[0],
        maximum=bounds[1],
    )


def _aggregate(values: Sequence[float]) -> dict[str, float | int]:
    return {
        "mean": statistics.fmean(values),
        "sample_std": statistics.stdev(values),
        "valid_seeds": len(values),
    }


def _validate_aggregate(
    record: object,
    values: Sequence[float],
    *,
    code_prefix: str,
) -> None:
    item = _mapping(record, f"{code_prefix}_aggregate")
    expected = _aggregate(values)
    _same_float(item.get("mean"), float(expected["mean"]), f"{code_prefix}_mean")
    _same_float(
        item.get("sample_std"),
        float(expected["sample_std"]),
        f"{code_prefix}_sample_std",
    )
    if _integer(
        item.get("valid_seeds"), f"{code_prefix}_valid_seeds", minimum=0
    ) != len(values):
        _fail(f"{code_prefix}_valid_seeds")


def _require_head_contract(report: Mapping[str, Any], variant: str) -> None:
    expected_encoder = {
        "base_plus": ("base_plus", "microsoft/wavlm-base-plus"),
        "large": ("large", "microsoft/wavlm-large"),
    }[variant]
    if report.get("schema_version") != HEAD_REPORT_SCHEMA:
        _fail(f"{variant}_head_schema")
    if report.get("status") != "completed":
        _fail(f"{variant}_head_status")
    _boolean(report.get("development_only"), True, f"{variant}_head_dev_only")
    _boolean(report.get("oracle_alignment"), True, f"{variant}_head_oracle")
    _boolean(
        report.get("deployable_alignment_claim_allowed"),
        False,
        f"{variant}_head_deployable_claim",
    )
    encoder = _mapping(report.get("encoder"), f"{variant}_encoder")
    if (
        encoder.get("variant") != expected_encoder[0]
        or encoder.get("id") != expected_encoder[1]
    ):
        _fail(f"{variant}_encoder_identity")
    _boolean(encoder.get("frozen"), True, f"{variant}_encoder_frozen")

    cache = _mapping(report.get("cache_contract"), f"{variant}_cache_contract")
    for key in (
        "full_cache_validated",
        "source_scan_complete",
        "smoke_cache_rejected",
        "every_shard_hash_validated",
        "every_shard_schema_validated",
        "every_shard_split_policy_validated",
        "every_shard_privacy_metadata_validated",
    ):
        _boolean(cache.get(key), True, f"{variant}_cache_{key}")
    if cache.get("cache_report_status") != "completed":
        _fail(f"{variant}_cache_status")

    leakage = _mapping(report.get("leakage_controls"), f"{variant}_leakage")
    if leakage.get("accepted_splits") != ["train", "dev"]:
        _fail(f"{variant}_accepted_splits")
    for key in (
        "stack_accessed",
        "calibration_accessed",
        "locked_test_accessed",
        "dev_used_for_selection",
        "raw_manifest_read",
    ):
        _boolean(leakage.get(key), False, f"{variant}_{key}")
    for key in (
        "raw_path_columns_read",
        "environment_files_read",
        "environment_files_registered",
    ):
        if leakage.get(key) != []:
            _fail(f"{variant}_{key}")

    summary = _mapping(report.get("summary"), f"{variant}_summary")
    if summary.get("seeds_completed") != list(EXPECTED_SEEDS):
        _fail(f"{variant}_summary_seeds")
    for key in (
        "results_are_development_only",
        "locked_test_remains_unread",
        "stack_remains_unread",
        "calibration_remains_unread",
        "train_inner_validation_used",
        "oracle_alignment",
    ):
        _boolean(summary.get(key), True, f"{variant}_summary_{key}")
    for key in ("dev_used_for_selection", "promotion_decision_allowed"):
        _boolean(summary.get(key), False, f"{variant}_summary_{key}")
    if summary.get("dev_inference_passes_per_seed") != 1:
        _fail(f"{variant}_dev_inference_passes")


def _head_metric_values(
    report: Mapping[str, Any],
    variant: str,
) -> tuple[
    dict[int, dict[str, float]],
    dict[int, dict[str, dict[str, float]]],
    dict[str, object],
]:
    runs = _list(report.get("runs"), f"{variant}_runs")
    if [
        item.get("seed") if isinstance(item, Mapping) else None for item in runs
    ] != list(EXPECTED_SEEDS):
        _fail(f"{variant}_run_seeds")
    phone: dict[int, dict[str, float]] = {}
    utterance: dict[int, dict[str, dict[str, float]]] = {}
    artifact_records: dict[str, object] = {}
    for raw_run, seed in zip(runs, EXPECTED_SEEDS, strict=True):
        run = _mapping(raw_run, f"{variant}_seed_{seed}_run")
        selection = _mapping(
            run.get("selection"),
            f"{variant}_seed_{seed}_selection",
        )
        _boolean(
            selection.get("dev_values_used_for_selection"),
            False,
            f"{variant}_seed_{seed}_dev_selection",
        )
        phone_record = _mapping(
            run.get("phone"),
            f"{variant}_seed_{seed}_phone",
        )
        utterance_record = _mapping(
            run.get("utterance"),
            f"{variant}_seed_{seed}_utterance",
        )
        for task_record, task in (
            (phone_record, "phone"),
            (utterance_record, "utterance"),
        ):
            if task_record.get("metric_scope") != "primary_dev_single_final_pass":
                _fail(f"{variant}_seed_{seed}_{task}_scope")
            _integer(
                task_record.get("rows"),
                f"{variant}_seed_{seed}_{task}_rows",
                minimum=1,
            )
        phone_metrics = _mapping(
            phone_record.get("metrics"),
            f"{variant}_seed_{seed}_phone_metrics",
        )
        phone[seed] = {
            name: _metric(
                phone_metrics,
                name,
                code_prefix=f"{variant}_seed_{seed}",
            )
            for name in ("macro_f1", "auprc")
        }
        outcomes = _mapping(
            utterance_record.get("outcomes"),
            f"{variant}_seed_{seed}_outcomes",
        )
        if set(outcomes) != set(OUTCOMES):
            _fail(f"{variant}_seed_{seed}_outcome_set")
        utterance[seed] = {}
        for outcome in OUTCOMES:
            outcome_record = _mapping(
                outcomes.get(outcome),
                f"{variant}_seed_{seed}_{outcome}",
            )
            outcome_metrics = _mapping(
                outcome_record.get("metrics"),
                f"{variant}_seed_{seed}_{outcome}_metrics",
            )
            utterance[seed][outcome] = {
                name: _metric(
                    outcome_metrics,
                    name,
                    code_prefix=f"{variant}_seed_{seed}_{outcome}",
                )
                for name in UTTERANCE_METRICS
            }

        run_artifacts = _mapping(
            run.get("artifacts"),
            f"{variant}_seed_{seed}_artifacts",
        )
        artifact_records.update(
            {
                f"seed_{seed}_checkpoint": run_artifacts.get("checkpoint"),
                f"seed_{seed}_slice_metrics": run_artifacts.get("slice_metrics"),
                f"seed_{seed}_phone_predictions": phone_record.get("predictions"),
                f"seed_{seed}_utterance_predictions": utterance_record.get(
                    "predictions"
                ),
            }
        )

    summary = _mapping(report.get("summary"), f"{variant}_summary")
    phone_summary = _mapping(summary.get("phone"), f"{variant}_phone_summary")
    for metric_name in ("macro_f1", "auprc"):
        _validate_aggregate(
            phone_summary.get(metric_name),
            [phone[seed][metric_name] for seed in EXPECTED_SEEDS],
            code_prefix=f"{variant}_phone_{metric_name}",
        )
    utterance_summary = _mapping(
        summary.get("utterance"),
        f"{variant}_utterance_summary",
    )
    if set(utterance_summary) != set(OUTCOMES):
        _fail(f"{variant}_summary_outcome_set")
    for outcome in OUTCOMES:
        outcome_summary = _mapping(
            utterance_summary.get(outcome),
            f"{variant}_{outcome}_summary",
        )
        for metric_name in UTTERANCE_METRICS:
            _validate_aggregate(
                outcome_summary.get(metric_name),
                [utterance[seed][outcome][metric_name] for seed in EXPECTED_SEEDS],
                code_prefix=f"{variant}_{outcome}_{metric_name}",
            )
    return phone, utterance, artifact_records


def _validate_head_artifacts(
    root: Path,
    report_path: Path,
    artifact_records: Mapping[str, object],
    *,
    variant: str,
) -> dict[str, FileFingerprint]:
    if set(artifact_records) != {
        f"seed_{seed}_{name}"
        for seed in EXPECTED_SEEDS
        for name in (
            "checkpoint",
            "slice_metrics",
            "phone_predictions",
            "utterance_predictions",
        )
    }:
        _fail(f"{variant}_artifact_set")
    output: dict[str, FileFingerprint] = {}
    for name, record in artifact_records.items():
        output[name] = _validate_fingerprint_record(
            root,
            record,
            base=report_path.parent,
            code_prefix=f"{variant}_{name}",
        )
    return output


def _validate_registry(
    root: Path,
    registry: Mapping[str, Any],
    registry_path: Path,
    report: Mapping[str, Any],
    report_path: Path,
    report_fingerprint: FileFingerprint,
    head_artifacts: Mapping[str, FileFingerprint],
    *,
    variant: str,
) -> frozenset[Path]:
    if registry.get("schema_version") != 1 or registry.get("status") != "completed":
        _fail(f"{variant}_registry_incomplete")
    if registry.get("failure") is not None:
        _fail(f"{variant}_registry_failure")
    timestamps = _mapping(
        registry.get("timestamps"),
        f"{variant}_registry_timestamps",
    )
    if timestamps.get("ended") is None:
        _fail(f"{variant}_registry_not_ended")
    expected_seeds = {f"experiment_seed_{seed}": seed for seed in EXPECTED_SEEDS}
    if registry.get("seeds") != expected_seeds:
        _fail(f"{variant}_registry_seeds")

    report_registry = _mapping(
        report.get("experiment_registry"),
        f"{variant}_report_registry",
    )
    for key in (
        "integrated",
        "t03_complete",
        "metrics_recorded",
        "artifacts_recorded",
        "implementation_source_hashes_match_registry_inputs",
        "runtime_packages_gpu_git_recorded",
        "seeds_recorded",
    ):
        _boolean(report_registry.get(key), True, f"{variant}_registry_{key}")
    if report_registry.get("run_id") != registry.get("run_id"):
        _fail(f"{variant}_registry_run_id")
    reported_registry_path = _existing_file(
        root,
        _string(
            report_registry.get("metadata_path"),
            f"{variant}_registry_metadata_path",
        ),
    )
    if reported_registry_path != registry_path:
        _fail(f"{variant}_registry_metadata_path")
    expected_artifact_count = len(EXPECTED_SEEDS) * 4 + 1
    if (
        _integer(
            report_registry.get("registered_artifact_count"),
            f"{variant}_registered_artifact_count",
            minimum=0,
        )
        != expected_artifact_count
    ):
        _fail(f"{variant}_registered_artifact_count")

    sources = _mapping(report.get("sources"), f"{variant}_sources")
    required_source_hashes = (
        "training_config_sha256",
        "trainer_source_sha256",
        "head_source_sha256",
        "feature_cache_report_sha256",
        "explicit_utterance_labels_sha256",
        "modeling_manifest_sha256",
        "prompt_reservation_sha256",
        "cache_builder_config_sha256",
        "cache_builder_sha256",
        "encoder_config_sha256",
        "encoder_checkpoint_sha256",
    )
    for key in required_source_hashes:
        _sha(sources.get(key), f"{variant}_{key}")
    _boolean(
        sources.get("model_hashes_bound_by_validated_config_and_cache_report"),
        True,
        f"{variant}_model_hash_binding",
    )

    inputs = _mapping(registry.get("inputs"), f"{variant}_registry_inputs")
    protected_paths = {report_path, registry_path}
    for role, raw_records in inputs.items():
        role_name = _string(role, f"{variant}_registry_input_role")
        records = _list(
            raw_records,
            f"{variant}_registry_{role_name}_inputs",
        )
        for index, raw_record in enumerate(records):
            record = _mapping(
                raw_record,
                f"{variant}_registry_{role_name}_{index}",
            )
            if set(record) != {"path", "sha256", "size_bytes"}:
                _fail(f"{variant}_registry_{role_name}_{index}_record_keys")
            path = _candidate_inside_workspace(
                root,
                _string(
                    record.get("path"),
                    f"{variant}_registry_{role_name}_{index}_path",
                ),
                must_exist=False,
            )
            _sha(
                record.get("sha256"),
                f"{variant}_registry_{role_name}_{index}_sha",
            )
            _integer(
                record.get("size_bytes"),
                f"{variant}_registry_{role_name}_{index}_size",
                minimum=0,
            )
            protected_paths.add(path)
    config_inputs = _list(
        inputs.get("config"),
        f"{variant}_registry_config_inputs",
    )
    model_inputs = _list(
        inputs.get("model"),
        f"{variant}_registry_model_inputs",
    )
    data_inputs = _list(inputs.get("data"), f"{variant}_registry_data_inputs")
    if len(config_inputs) != 1 or len(model_inputs) != 2 or not data_inputs:
        _fail(f"{variant}_registry_input_cardinality")
    config_file = _validate_fingerprint_record(
        root,
        config_inputs[0],
        base=None,
        code_prefix=f"{variant}_registry_config",
    )
    if config_file.sha256 != sources["training_config_sha256"]:
        _fail(f"{variant}_training_config_hash_chain")
    model_files = [
        _validate_fingerprint_record(
            root,
            item,
            base=None,
            code_prefix=f"{variant}_registry_model_{index}",
        )
        for index, item in enumerate(model_inputs)
    ]
    if {item.sha256 for item in model_files} != {
        sources["trainer_source_sha256"],
        sources["head_source_sha256"],
    }:
        _fail(f"{variant}_source_hash_chain")
    for index, item in enumerate(data_inputs):
        data = _mapping(item, f"{variant}_registry_data_{index}")
        _sha(data.get("sha256"), f"{variant}_registry_data_{index}_sha")
        _integer(
            data.get("size_bytes"),
            f"{variant}_registry_data_{index}_size",
            minimum=0,
        )

    metrics = _list(registry.get("metrics"), f"{variant}_registry_metrics")
    if len(metrics) != len(EXPECTED_SEEDS) + 1:
        _fail(f"{variant}_registry_metric_count")
    steps: list[int | None] = []
    for index, raw_event in enumerate(metrics):
        event = _mapping(raw_event, f"{variant}_registry_metric_{index}")
        if event.get("split") != "dev":
            _fail(f"{variant}_registry_metric_split")
        step = event.get("step")
        if step is not None:
            step = _integer(step, f"{variant}_registry_metric_step")
        steps.append(step)
        values = _mapping(
            event.get("values"),
            f"{variant}_registry_metric_values",
        )
        for name, value in values.items():
            _string(name, f"{variant}_registry_metric_name")
            if value is not None:
                _number(value, f"{variant}_registry_metric_value")
    if steps != [*EXPECTED_SEEDS, None]:
        _fail(f"{variant}_registry_metric_steps")
    phone_values, utterance_values, _ = _head_metric_values(report, variant)
    for seed, raw_event in zip(EXPECTED_SEEDS, metrics[:3], strict=True):
        values = _mapping(
            _mapping(
                raw_event,
                f"{variant}_registry_seed_{seed}_event",
            ).get("values"),
            f"{variant}_registry_seed_{seed}_values",
        )
        for registry_name, report_name in (
            ("phone_macro_f1", "macro_f1"),
            ("phone_auprc", "auprc"),
        ):
            _same_float(
                values.get(registry_name),
                phone_values[seed][report_name],
                f"{variant}_registry_seed_{seed}_{registry_name}",
            )
        for outcome in OUTCOMES:
            for metric_name in UTTERANCE_METRICS:
                registry_name = f"{outcome}_{metric_name}"
                _same_float(
                    values.get(registry_name),
                    utterance_values[seed][outcome][metric_name],
                    f"{variant}_registry_seed_{seed}_{registry_name}",
                )
    summary_values = _mapping(
        _mapping(
            metrics[-1],
            f"{variant}_registry_summary_event",
        ).get("values"),
        f"{variant}_registry_summary_values",
    )
    report_phone_summary = _mapping(
        _mapping(report.get("summary"), f"{variant}_summary").get("phone"),
        f"{variant}_phone_summary",
    )
    for registry_prefix, report_name in (
        ("phone_macro_f1", "macro_f1"),
        ("phone_auprc", "auprc"),
    ):
        report_aggregate = _mapping(
            report_phone_summary.get(report_name),
            f"{variant}_{report_name}_summary",
        )
        for suffix in ("mean", "sample_std"):
            _same_float(
                summary_values.get(f"{registry_prefix}_{suffix}"),
                _number(
                    report_aggregate.get(suffix),
                    f"{variant}_{report_name}_{suffix}",
                ),
                f"{variant}_registry_{registry_prefix}_{suffix}",
            )

    registry_artifacts = _list(
        registry.get("artifacts"),
        f"{variant}_registry_artifacts",
    )
    if len(registry_artifacts) != expected_artifact_count:
        _fail(f"{variant}_registry_artifact_count")
    expected = dict(head_artifacts)
    expected["final_report"] = report_fingerprint
    actual_names: set[str] = set()
    for index, raw_artifact in enumerate(registry_artifacts):
        artifact = _mapping(
            raw_artifact,
            f"{variant}_registry_artifact_{index}",
        )
        name = _string(
            artifact.get("name"),
            f"{variant}_registry_artifact_name",
        )
        if name in actual_names or name not in expected:
            _fail(f"{variant}_registry_artifact_set")
        actual_names.add(name)
        actual = _validate_fingerprint_record(
            root,
            artifact,
            base=None,
            code_prefix=f"{variant}_registry_artifact_{name}",
        )
        if actual.path != expected[name].path or actual.sha256 != expected[name].sha256:
            _fail(f"{variant}_registry_artifact_hash_chain")
        protected_paths.add(actual.path)
    if actual_names != set(expected):
        _fail(f"{variant}_registry_artifact_set")

    final_report_item = next(
        item
        for item in registry_artifacts
        if isinstance(item, Mapping) and item.get("name") == "final_report"
    )
    final_report_path = _existing_file(
        root,
        _string(
            _mapping(final_report_item, "final_report_item").get("path"),
            "final_report_path",
        ),
    )
    if final_report_path != report_path:
        _fail(f"{variant}_registry_final_report_path")
    return frozenset(protected_paths)


def _validate_head(
    root: Path,
    report_path: Path,
    registry_path: Path,
    *,
    variant: str,
) -> HeadValidation:
    report_file = _fingerprint(report_path)
    registry_file = _fingerprint(registry_path)
    report = _load_json(report_path)
    registry = _load_json(registry_path)
    _require_head_contract(report, variant)
    _, _, artifact_records = _head_metric_values(report, variant)
    artifacts = _validate_head_artifacts(
        root,
        report_path,
        artifact_records,
        variant=variant,
    )
    protected_paths = _validate_registry(
        root,
        registry,
        registry_path,
        report,
        report_path,
        report_file,
        artifacts,
        variant=variant,
    )
    return HeadValidation(
        variant=variant,
        report=report,
        registry=registry,
        report_file=report_file,
        registry_file=registry_file,
        artifacts_by_name=artifacts,
        protected_paths=protected_paths,
    )


def _validate_comparison_contract(report: Mapping[str, Any]) -> None:
    if report.get("schema_version") != COMPARISON_REPORT_SCHEMA:
        _fail("comparison_schema")
    if report.get("status") != "completed":
        _fail("comparison_status")
    _string(report.get("experiment_id"), "comparison_experiment_id")
    _string(report.get("started_at_utc"), "comparison_started")
    _string(report.get("completed_at_utc"), "comparison_completed")
    _boolean(report.get("development_only"), True, "comparison_dev_only")
    _boolean(report.get("oracle_alignment"), True, "comparison_oracle")
    if report.get("comparison_scope") != "primary_dev_paired":
        _fail("comparison_scope")

    reproducibility = _mapping(
        report.get("reproducibility"),
        "comparison_reproducibility",
    )
    if set(reproducibility) != {
        "execution_receipt_created",
        "atomic_commit_manifest_created",
        "minimum_hash_chain_complete",
        "source_reports_registry_completed",
        "implementation_sources_hashed",
        "all_prediction_artifacts_hashed",
    }:
        _fail("comparison_reproducibility_keys")
    _boolean(
        reproducibility.get("execution_receipt_created"),
        True,
        "comparison_execution_receipt_created",
    )
    _boolean(
        reproducibility.get("atomic_commit_manifest_created"),
        True,
        "comparison_atomic_commit_manifest_created",
    )
    for key in (
        "minimum_hash_chain_complete",
        "source_reports_registry_completed",
        "implementation_sources_hashed",
        "all_prediction_artifacts_hashed",
    ):
        _boolean(
            reproducibility.get(key),
            True,
            f"comparison_reproducibility_{key}",
        )

    contract = _mapping(report.get("contract"), "comparison_contract")
    if contract.get("seeds") != list(EXPECTED_SEEDS):
        _fail("comparison_seeds")
    registry_contract = _mapping(
        contract.get("registry_completion"),
        "comparison_registry_completion",
    )
    for variant in VARIANTS:
        item = _mapping(
            registry_contract.get(variant),
            f"comparison_{variant}_registry",
        )
        if item.get("status") != "completed":
            _fail(f"comparison_{variant}_registry_status")
        _string(item.get("run_id"), f"comparison_{variant}_registry_run_id")
        if (
            _integer(
                item.get("registered_artifact_count"),
                f"comparison_{variant}_registered_artifact_count",
                minimum=0,
            )
            != 13
        ):
            _fail(f"comparison_{variant}_registered_artifact_count")
    paired = _mapping(
        contract.get("paired_identity"),
        "comparison_paired_identity",
    )
    for key in (
        "phone_ids_targets_speakers_equal",
        "utterance_ids_targets_speakers_equal",
        "cross_seed_equal",
    ):
        _boolean(paired.get(key), True, f"comparison_{key}")
    leakage = _mapping(contract.get("leakage"), "comparison_leakage")
    for key in (
        "stack_accessed",
        "calibration_accessed",
        "locked_test_accessed",
        "raw_data_read",
    ):
        _boolean(leakage.get(key), False, f"comparison_{key}")
    if leakage.get("environment_files_read") != []:
        _fail("comparison_environment_files_read")

    summary = _mapping(report.get("summary"), "comparison_summary")
    for key in (
        "results_are_development_only",
        "locked_test_remains_unread",
        "stack_remains_unread",
        "calibration_remains_unread",
    ):
        _boolean(summary.get(key), True, f"comparison_summary_{key}")


def _comparison_source_file(
    root: Path,
    item: object,
    *,
    code_prefix: str,
) -> FileFingerprint:
    return _validate_fingerprint_record(
        root,
        item,
        base=None,
        code_prefix=code_prefix,
    )


def _validate_comparison_sources(
    root: Path,
    comparison: Mapping[str, Any],
    heads: Mapping[str, HeadValidation],
) -> dict[str, FileFingerprint]:
    sources = _mapping(comparison.get("sources"), "comparison_sources")
    if set(sources) != {
        "config",
        "comparison_script",
        "comparison_module",
        "base_plus",
        "large",
    }:
        _fail("comparison_source_set")
    output: dict[str, FileFingerprint] = {}
    for name in ("config", "comparison_script", "comparison_module"):
        record = _mapping(
            sources.get(name),
            f"comparison_source_{name}_record",
        )
        if set(record) != {"path", "sha256", "size_bytes"}:
            _fail(f"comparison_source_{name}_record_keys")
        output[name] = _comparison_source_file(
            root,
            record,
            code_prefix=f"comparison_source_{name}",
        )
        expected_path = _existing_file(
            root,
            EXPECTED_COMPARISON_SOURCE_PATHS[name],
        )
        if output[name].path != expected_path:
            _fail(f"comparison_source_{name}_path")
    for variant in VARIANTS:
        source = _mapping(sources.get(variant), f"comparison_source_{variant}")
        head = heads[variant]
        report_item = _mapping(
            source.get("report"),
            f"comparison_source_{variant}_report",
        )
        if set(report_item) != {"path", "sha256", "size_bytes"}:
            _fail(f"comparison_source_{variant}_report_record_keys")
        report_file = _comparison_source_file(
            root,
            report_item,
            code_prefix=f"comparison_source_{variant}_report",
        )
        if (
            report_file.path != head.report_file.path
            or report_file.sha256 != head.report_file.sha256
        ):
            _fail(f"comparison_{variant}_report_hash_chain")
        registry_item = _mapping(
            source.get("registry"),
            f"comparison_source_{variant}_registry",
        )
        if set(registry_item) != {
            "path",
            "sha256",
            "size_bytes",
            "run_id",
            "status",
        }:
            _fail(f"comparison_source_{variant}_registry_record_keys")
        registry_file = _comparison_source_file(
            root,
            registry_item,
            code_prefix=f"comparison_source_{variant}_registry",
        )
        if (
            registry_file.path != head.registry_file.path
            or registry_file.sha256 != head.registry_file.sha256
        ):
            _fail(f"comparison_{variant}_registry_hash_chain")
        if (
            registry_item.get("run_id") != head.registry.get("run_id")
            or registry_item.get("status") != "completed"
        ):
            _fail(f"comparison_{variant}_registry_contract")
        registry_contract = _mapping(
            _mapping(comparison.get("contract"), "comparison_contract").get(
                "registry_completion"
            ),
            "comparison_registry_completion",
        )
        contract_item = _mapping(
            registry_contract.get(variant),
            f"comparison_{variant}_registry_completion",
        )
        report_registry = _mapping(
            head.report.get("experiment_registry"),
            f"{variant}_experiment_registry",
        )
        if contract_item.get("run_id") != head.registry.get(
            "run_id"
        ) or contract_item.get("registered_artifact_count") != report_registry.get(
            "registered_artifact_count"
        ):
            _fail(f"comparison_{variant}_registry_contract")

        prediction_sources = _mapping(
            source.get("predictions"),
            f"comparison_{variant}_predictions",
        )
        if set(prediction_sources) != {f"seed_{seed}" for seed in EXPECTED_SEEDS}:
            _fail(f"comparison_{variant}_prediction_seed_set")
        runs = _list(head.report.get("runs"), f"{variant}_runs")
        for run, seed in zip(runs, EXPECTED_SEEDS, strict=True):
            run_item = _mapping(run, f"{variant}_seed_{seed}_run")
            per_seed = _mapping(
                prediction_sources.get(f"seed_{seed}"),
                f"comparison_{variant}_seed_{seed}_predictions",
            )
            for task in ("phone", "utterance"):
                source_prediction = _mapping(
                    per_seed.get(task),
                    f"comparison_{variant}_seed_{seed}_{task}_prediction",
                )
                if set(source_prediction) != {
                    "path",
                    "sha256",
                    "size_bytes",
                    "rows",
                }:
                    _fail(f"comparison_{variant}_seed_{seed}_{task}_record_keys")
                actual = _comparison_source_file(
                    root,
                    source_prediction,
                    code_prefix=(f"comparison_{variant}_seed_{seed}_{task}_prediction"),
                )
                head_task = _mapping(
                    run_item.get(task),
                    f"{variant}_seed_{seed}_{task}",
                )
                head_prediction = _mapping(
                    head_task.get("predictions"),
                    f"{variant}_seed_{seed}_{task}_prediction",
                )
                expected = head.artifacts_by_name[f"seed_{seed}_{task}_predictions"]
                if actual.path != expected.path or actual.sha256 != expected.sha256:
                    _fail(f"comparison_{variant}_seed_{seed}_{task}_hash_chain")
                if _sha(
                    source_prediction.get("sha256"),
                    f"comparison_{variant}_{task}_sha",
                ) != _sha(
                    head_prediction.get("sha256"),
                    f"{variant}_{task}_sha",
                ) or _integer(
                    source_prediction.get("rows"),
                    f"comparison_{variant}_{task}_rows",
                    minimum=1,
                ) != _integer(
                    head_task.get("rows"),
                    f"{variant}_{task}_rows",
                    minimum=1,
                ):
                    _fail(f"comparison_{variant}_seed_{seed}_{task}_metadata_chain")
    return output


def _validate_event_metric(record: Mapping[str, Any], code_prefix: str) -> float:
    value = _metric(record, "event_f1", code_prefix=code_prefix)
    counts = _mapping(record.get("event_counts"), f"{code_prefix}_event_counts")
    if set(counts) != {"tp", "fp", "fn"}:
        _fail(f"{code_prefix}_event_count_set")
    true_positive = _integer(counts.get("tp"), f"{code_prefix}_tp", minimum=0)
    false_positive = _integer(counts.get("fp"), f"{code_prefix}_fp", minimum=0)
    false_negative = _integer(counts.get("fn"), f"{code_prefix}_fn", minimum=0)
    denominator = 2 * true_positive + false_positive + false_negative
    if denominator <= 0:
        _fail(f"{code_prefix}_event_denominator")
    expected = 2.0 * true_positive / denominator
    _same_float(value, expected, f"{code_prefix}_event_f1_recomputed")
    return value


def _validate_comparison_metrics(
    comparison: Mapping[str, Any],
    heads: Mapping[str, HeadValidation],
) -> tuple[
    dict[int, dict[str, dict[str, float]]],
    dict[int, dict[str, dict[str, dict[str, float]]]],
]:
    head_values = {
        variant: _head_metric_values(head.report, variant)[:2]
        for variant, head in heads.items()
    }
    metrics = _mapping(comparison.get("metrics"), "comparison_metrics")
    per_seed = _mapping(metrics.get("per_seed"), "comparison_per_seed")
    if set(per_seed) != {f"seed_{seed}" for seed in EXPECTED_SEEDS}:
        _fail("comparison_metric_seed_set")
    phone_output: dict[int, dict[str, dict[str, float]]] = {}
    utterance_output: dict[
        int,
        dict[str, dict[str, dict[str, float]]],
    ] = {}
    for seed in EXPECTED_SEEDS:
        seed_record = _mapping(
            per_seed.get(f"seed_{seed}"),
            f"comparison_seed_{seed}",
        )
        phone = _mapping(
            seed_record.get("phone"),
            f"comparison_seed_{seed}_phone",
        )
        if set(phone) != {"base_plus", "large", "large_minus_base"}:
            _fail(f"comparison_seed_{seed}_phone_set")
        phone_output[seed] = {}
        for variant in VARIANTS:
            record = _mapping(
                phone.get(variant),
                f"comparison_seed_{seed}_phone_{variant}",
            )
            values = {
                "macro_f1": _metric(
                    record,
                    "macro_f1",
                    code_prefix=f"comparison_seed_{seed}_{variant}",
                ),
                "auprc": _metric(
                    record,
                    "auprc",
                    code_prefix=f"comparison_seed_{seed}_{variant}",
                ),
                "event_f1": _validate_event_metric(
                    record,
                    f"comparison_seed_{seed}_{variant}",
                ),
            }
            head_phone = head_values[variant][0][seed]
            for metric_name in ("macro_f1", "auprc"):
                _same_float(
                    values[metric_name],
                    head_phone[metric_name],
                    f"comparison_seed_{seed}_{variant}_{metric_name}_head",
                    tolerance=1e-5,
                )
            phone_output[seed][variant] = values
        delta = _mapping(
            phone.get("large_minus_base"),
            f"comparison_seed_{seed}_phone_delta",
        )
        phone_output[seed]["large_minus_base"] = {}
        for metric_name in PHONE_METRICS:
            expected = (
                phone_output[seed]["large"][metric_name]
                - phone_output[seed]["base_plus"][metric_name]
            )
            _same_float(
                delta.get(metric_name),
                expected,
                f"comparison_seed_{seed}_phone_delta_{metric_name}",
            )
            phone_output[seed]["large_minus_base"][metric_name] = expected

        utterance = _mapping(
            seed_record.get("utterance"),
            f"comparison_seed_{seed}_utterance",
        )
        if set(utterance) != set(OUTCOMES):
            _fail(f"comparison_seed_{seed}_outcome_set")
        utterance_output[seed] = {}
        for outcome in OUTCOMES:
            outcome_record = _mapping(
                utterance.get(outcome),
                f"comparison_seed_{seed}_{outcome}",
            )
            if set(outcome_record) != {
                "base_plus",
                "large",
                "large_minus_base",
            }:
                _fail(f"comparison_seed_{seed}_{outcome}_variant_set")
            utterance_output[seed][outcome] = {}
            for variant in VARIANTS:
                record = _mapping(
                    outcome_record.get(variant),
                    f"comparison_seed_{seed}_{outcome}_{variant}",
                )
                values = {
                    metric_name: _metric(
                        record,
                        metric_name,
                        code_prefix=(f"comparison_seed_{seed}_{outcome}_{variant}"),
                    )
                    for metric_name in UTTERANCE_METRICS
                }
                for metric_name in UTTERANCE_METRICS:
                    _same_float(
                        values[metric_name],
                        head_values[variant][1][seed][outcome][metric_name],
                        (
                            f"comparison_seed_{seed}_{outcome}_{variant}_"
                            f"{metric_name}_head"
                        ),
                        tolerance=1e-5,
                    )
                utterance_output[seed][outcome][variant] = values
            delta_record = _mapping(
                outcome_record.get("large_minus_base"),
                f"comparison_seed_{seed}_{outcome}_delta",
            )
            delta_values: dict[str, float] = {}
            for metric_name in UTTERANCE_METRICS:
                expected = (
                    utterance_output[seed][outcome]["large"][metric_name]
                    - utterance_output[seed][outcome]["base_plus"][metric_name]
                )
                _same_float(
                    delta_record.get(metric_name),
                    expected,
                    (f"comparison_seed_{seed}_{outcome}_delta_{metric_name}"),
                )
                delta_values[metric_name] = expected
            utterance_output[seed][outcome]["large_minus_base"] = delta_values

    aggregate = _mapping(metrics.get("aggregate"), "comparison_aggregate")
    phone_aggregate = _mapping(
        aggregate.get("phone"),
        "comparison_phone_aggregate",
    )
    for metric_name in PHONE_METRICS:
        metric_record = _mapping(
            phone_aggregate.get(metric_name),
            f"comparison_phone_{metric_name}_aggregate",
        )
        for variant in (*VARIANTS, "large_minus_base"):
            _validate_aggregate(
                metric_record.get(variant),
                [phone_output[seed][variant][metric_name] for seed in EXPECTED_SEEDS],
                code_prefix=f"comparison_phone_{metric_name}_{variant}",
            )
    utterance_aggregate = _mapping(
        aggregate.get("utterance"),
        "comparison_utterance_aggregate",
    )
    if set(utterance_aggregate) != set(OUTCOMES):
        _fail("comparison_aggregate_outcome_set")
    for outcome in OUTCOMES:
        outcome_record = _mapping(
            utterance_aggregate.get(outcome),
            f"comparison_{outcome}_aggregate",
        )
        for metric_name in UTTERANCE_METRICS:
            metric_record = _mapping(
                outcome_record.get(metric_name),
                f"comparison_{outcome}_{metric_name}_aggregate",
            )
            for variant in (*VARIANTS, "large_minus_base"):
                _validate_aggregate(
                    metric_record.get(variant),
                    [
                        utterance_output[seed][outcome][variant][metric_name]
                        for seed in EXPECTED_SEEDS
                    ],
                    code_prefix=(f"comparison_{outcome}_{metric_name}_{variant}"),
                )
    return phone_output, utterance_output


def _bootstrap_values(
    record: object,
    *,
    code_prefix: str,
    expected_resamples: int = 5_000,
    minimum: float = -1.0,
    maximum: float = 1.0,
) -> dict[str, float | int]:
    item = _mapping(record, f"{code_prefix}_bootstrap")
    estimate = _number(
        item.get("estimate"),
        f"{code_prefix}_estimate",
        minimum=minimum,
        maximum=maximum,
    )
    lower = _number(
        item.get("confidence_lower"),
        f"{code_prefix}_lower",
        minimum=minimum,
        maximum=maximum,
    )
    upper = _number(
        item.get("confidence_upper"),
        f"{code_prefix}_upper",
        minimum=minimum,
        maximum=maximum,
    )
    if lower > upper:
        _fail(f"{code_prefix}_interval_order")
    confidence = _number(
        item.get("confidence_level"),
        f"{code_prefix}_confidence_level",
    )
    if not math.isclose(confidence, 0.95, abs_tol=1e-12):
        _fail(f"{code_prefix}_confidence_level")
    n_resamples = _integer(
        item.get("n_resamples"),
        f"{code_prefix}_n_resamples",
        minimum=1,
    )
    if n_resamples != expected_resamples:
        _fail(f"{code_prefix}_n_resamples")
    valid = _integer(
        item.get("n_valid_resamples"),
        f"{code_prefix}_n_valid_resamples",
        minimum=1,
    )
    if valid > n_resamples:
        _fail(f"{code_prefix}_n_valid_resamples")
    speakers = _integer(
        item.get("n_speakers"),
        f"{code_prefix}_n_speakers",
        minimum=2,
    )
    seed = _integer(item.get("seed"), f"{code_prefix}_seed")
    if seed != 42:
        _fail(f"{code_prefix}_seed")
    p_value = _number(
        item.get("p_value_two_sided_zero"),
        f"{code_prefix}_p_value",
        minimum=0.0,
        maximum=1.0,
    )
    return {
        "estimate": estimate,
        "confidence_lower": lower,
        "confidence_upper": upper,
        "confidence_level": confidence,
        "n_resamples": n_resamples,
        "n_valid_resamples": valid,
        "n_speakers": speakers,
        "seed": seed,
        "p_value_two_sided_zero": p_value,
    }


def _validate_primary_and_multiplicity(
    comparison: Mapping[str, Any],
    phone: Mapping[int, Mapping[str, Mapping[str, float]]],
    utterance: Mapping[
        int,
        Mapping[str, Mapping[str, Mapping[str, float]]],
    ],
) -> dict[str, Any]:
    primary = _mapping(
        comparison.get("primary_comparison"),
        "primary_comparison",
    )
    if (
        primary.get("metric") != "phone_macro_f1"
        or primary.get("effect") != "large_minus_base"
    ):
        _fail("primary_comparison_identity")
    bootstrap = _bootstrap_values(
        primary.get("paired_speaker_bootstrap"),
        code_prefix="primary",
    )
    _same_float(
        bootstrap["estimate"],
        statistics.fmean(
            phone[seed]["large_minus_base"]["macro_f1"] for seed in EXPECTED_SEEDS
        ),
        "primary_bootstrap_seed_aggregate",
    )
    gate = _mapping(primary.get("gate"), "primary_gate")
    threshold = _number(
        gate.get("threshold_absolute"),
        "primary_gate_threshold",
    )
    if not math.isclose(threshold, 0.03, abs_tol=1e-12):
        _fail("primary_gate_threshold")
    if gate.get("rule") != "point_estimate_gte_threshold":
        _fail("primary_gate_rule")
    point_passed = bool(bootstrap["estimate"] >= threshold)
    lower_passed = bool(bootstrap["confidence_lower"] >= threshold)
    _boolean(
        gate.get("point_estimate_passed"),
        point_passed,
        "primary_gate_point_decision",
    )
    _boolean(
        gate.get("confidence_lower_gte_threshold"),
        lower_passed,
        "primary_gate_confidence_decision",
    )
    expected_status = "passed" if point_passed else "failed"
    if gate.get("status") != expected_status:
        _fail("primary_gate_status")

    multiplicity = _mapping(
        comparison.get("multiplicity"),
        "comparison_multiplicity",
    )
    if (
        multiplicity.get("family") != "utterance_icc_2_1_across_outcomes"
        or multiplicity.get("method") != "holm"
    ):
        _fail("comparison_multiplicity_contract")
    alpha = _number(
        multiplicity.get("alpha"),
        "comparison_multiplicity_alpha",
    )
    if not math.isclose(alpha, 0.05, abs_tol=1e-12):
        _fail("comparison_multiplicity_alpha")
    comparisons = _mapping(
        multiplicity.get("comparisons"),
        "comparison_multiplicity_comparisons",
    )
    if set(comparisons) != set(OUTCOMES):
        _fail("comparison_multiplicity_outcomes")
    multiplicity_output: dict[str, Any] = {}
    raw_p_values: list[float] = []
    adjusted_p_values: list[float] = []
    for outcome in OUTCOMES:
        item = _mapping(
            comparisons.get(outcome),
            f"comparison_multiplicity_{outcome}",
        )
        effect = _number(
            item.get("effect"),
            f"comparison_multiplicity_{outcome}_effect",
            minimum=-1.0,
            maximum=1.0,
        )
        raw_p = _number(
            item.get("raw_p_value"),
            f"comparison_multiplicity_{outcome}_raw_p",
            minimum=0.0,
            maximum=1.0,
        )
        adjusted_p = _number(
            item.get("adjusted_p_value"),
            f"comparison_multiplicity_{outcome}_adjusted_p",
            minimum=0.0,
            maximum=1.0,
        )
        if adjusted_p + 1e-12 < raw_p:
            _fail(f"comparison_multiplicity_{outcome}_holm_order")
        rejected = adjusted_p <= alpha
        _boolean(
            item.get("rejected"),
            rejected,
            f"comparison_multiplicity_{outcome}_rejected",
        )
        outcome_bootstrap = _bootstrap_values(
            item.get("paired_speaker_bootstrap"),
            code_prefix=f"multiplicity_{outcome}",
            minimum=-2.0,
            maximum=2.0,
        )
        _same_float(
            effect,
            float(outcome_bootstrap["estimate"]),
            f"comparison_multiplicity_{outcome}_effect",
        )
        _same_float(
            effect,
            statistics.fmean(
                utterance[seed][outcome]["large_minus_base"]["icc_2_1"]
                for seed in EXPECTED_SEEDS
            ),
            f"comparison_multiplicity_{outcome}_seed_aggregate",
            tolerance=1e-10,
        )
        _same_float(
            raw_p,
            float(outcome_bootstrap["p_value_two_sided_zero"]),
            f"comparison_multiplicity_{outcome}_bootstrap_p",
        )
        raw_p_values.append(raw_p)
        adjusted_p_values.append(adjusted_p)
        multiplicity_output[outcome] = {
            "effect": effect,
            "raw_p_value": raw_p,
            "adjusted_p_value": adjusted_p,
            "rejected": rejected,
            "paired_speaker_bootstrap": outcome_bootstrap,
        }
    order = sorted(range(len(raw_p_values)), key=raw_p_values.__getitem__)
    expected_adjusted = [0.0] * len(raw_p_values)
    running_maximum = 0.0
    for rank, index in enumerate(order):
        candidate = min(1.0, (len(raw_p_values) - rank) * raw_p_values[index])
        running_maximum = max(running_maximum, candidate)
        expected_adjusted[index] = running_maximum
    for outcome, actual, expected in zip(
        OUTCOMES,
        adjusted_p_values,
        expected_adjusted,
        strict=True,
    ):
        _same_float(
            actual,
            expected,
            f"comparison_multiplicity_{outcome}_holm_adjustment",
        )

    summary = _mapping(comparison.get("summary"), "comparison_summary")
    _boolean(
        summary.get("large_adoption_gate_passed"),
        point_passed,
        "comparison_summary_gate_passed",
    )
    if summary.get("large_adoption_gate_status") != expected_status:
        _fail("comparison_summary_gate_status")
    return {
        "metric": "phone_macro_f1",
        "effect": "large_minus_base",
        "paired_speaker_bootstrap": bootstrap,
        "gate": {
            "threshold_absolute": threshold,
            "rule": "point_estimate_gte_threshold",
            "point_estimate_passed": point_passed,
            "confidence_lower_gte_threshold": lower_passed,
            "status": expected_status,
        },
        "multiplicity": {
            "family": "utterance_icc_2_1_across_outcomes",
            "method": "holm",
            "alpha": alpha,
            "comparisons": multiplicity_output,
        },
    }


def _validate_privacy(report: Mapping[str, Any]) -> None:
    privacy = _mapping(report.get("privacy"), "comparison_privacy")
    if set(privacy) != {
        "aggregate_only_output",
        "direct_identifiers_written",
        "pseudonymous_row_ids_written",
        "prediction_rows_written",
        "raw_data_read",
        "source_feature_caches_read",
        "environment_files_read",
        "input_access_scope",
        "shareable",
    }:
        _fail("comparison_privacy_keys")
    for key in (
        "direct_identifiers_written",
        "pseudonymous_row_ids_written",
        "prediction_rows_written",
        "raw_data_read",
        "source_feature_caches_read",
        "shareable",
    ):
        _boolean(privacy.get(key), False, f"comparison_privacy_{key}")
    _boolean(
        privacy.get("aggregate_only_output"),
        True,
        "comparison_privacy_aggregate_only_output",
    )
    if privacy.get("environment_files_read") != []:
        _fail("comparison_privacy_environment_files_read")
    if privacy.get("input_access_scope") != "restricted_internal":
        _fail("comparison_privacy_input_access_scope")


def _format_number(value: float) -> str:
    return f"{value:.6f}"


def _format_aggregate(values: Sequence[float]) -> str:
    aggregate = _aggregate(values)
    return (
        f"{_format_number(float(aggregate['mean']))} ± "
        f"{_format_number(float(aggregate['sample_std']))}"
    )


def _render_markdown(
    phone: Mapping[int, Mapping[str, Mapping[str, float]]],
    utterance: Mapping[
        int,
        Mapping[str, Mapping[str, Mapping[str, float]]],
    ],
    primary: Mapping[str, Any],
) -> str:
    labels = {
        "base_plus": "WavLM Base+",
        "large": "WavLM Large",
        "large_minus_base": "Large − Base+",
    }
    lines = [
        "# WavLM frozen-head development results",
        "",
        "> Oracle-alignment, development-only results. These values are not "
        "locked-test or deployable-alignment evidence.",
        "",
        "## Phone metrics",
        "",
        "| Model | Seed | Macro F1 | AUPRC | Event F1 |",
        "|---|---:|---:|---:|---:|",
    ]
    for variant in (*VARIANTS, "large_minus_base"):
        for seed in EXPECTED_SEEDS:
            values = phone[seed][variant]
            lines.append(
                f"| {labels[variant]} | {seed} | "
                f"{_format_number(values['macro_f1'])} | "
                f"{_format_number(values['auprc'])} | "
                f"{_format_number(values['event_f1'])} |"
            )
        lines.append(
            f"| {labels[variant]} | Mean ± sample SD | "
            f"{_format_aggregate([phone[seed][variant]['macro_f1'] for seed in EXPECTED_SEEDS])} | "
            f"{_format_aggregate([phone[seed][variant]['auprc'] for seed in EXPECTED_SEEDS])} | "
            f"{_format_aggregate([phone[seed][variant]['event_f1'] for seed in EXPECTED_SEEDS])} |"
        )

    lines.extend(
        [
            "",
            "## Utterance outcomes",
            "",
            "| Model | Outcome | Seed | QWK | Spearman | MAE | ICC(2,1) |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for variant in (*VARIANTS, "large_minus_base"):
        for outcome in OUTCOMES:
            for seed in EXPECTED_SEEDS:
                values = utterance[seed][outcome][variant]
                lines.append(
                    f"| {labels[variant]} | {outcome} | {seed} | "
                    f"{_format_number(values['qwk'])} | "
                    f"{_format_number(values['spearman'])} | "
                    f"{_format_number(values['mae'])} | "
                    f"{_format_number(values['icc_2_1'])} |"
                )
            lines.append(
                f"| {labels[variant]} | {outcome} | Mean ± sample SD | "
                f"{_format_aggregate([utterance[seed][outcome][variant]['qwk'] for seed in EXPECTED_SEEDS])} | "
                f"{_format_aggregate([utterance[seed][outcome][variant]['spearman'] for seed in EXPECTED_SEEDS])} | "
                f"{_format_aggregate([utterance[seed][outcome][variant]['mae'] for seed in EXPECTED_SEEDS])} | "
                f"{_format_aggregate([utterance[seed][outcome][variant]['icc_2_1'] for seed in EXPECTED_SEEDS])} |"
            )

    bootstrap = _mapping(
        primary.get("paired_speaker_bootstrap"),
        "output_primary_bootstrap",
    )
    gate = _mapping(primary.get("gate"), "output_primary_gate")
    lines.extend(
        [
            "",
            "## S103 Large − Base+ primary comparison",
            "",
            "| Metric | Paired estimate | 95% CI | Gate | Decision |",
            "|---|---:|---:|---:|---|",
            "| Phone macro F1 | "
            f"{_format_number(float(bootstrap['estimate']))} | "
            f"[{_format_number(float(bootstrap['confidence_lower']))}, "
            f"{_format_number(float(bootstrap['confidence_upper']))}] | "
            f"≥ {_format_number(float(gate['threshold_absolute']))} | "
            f"{gate['status']} |",
            "",
            "Bootstrap unit: speaker; repetitions: "
            f"{int(bootstrap['n_resamples']):,}; "
            f"valid repetitions: {int(bootstrap['n_valid_resamples']):,}.",
            "",
        ]
    )
    return "\n".join(lines)


def _safe_numeric_copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _safe_numeric_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_numeric_copy(item) for item in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("output_nonfinite_number")
        return value
    if isinstance(value, str) and value in {
        "phone_macro_f1",
        "large_minus_base",
        "point_estimate_gte_threshold",
        "passed",
        "failed",
        "utterance_icc_2_1_across_outcomes",
        "holm",
    }:
        return value
    _fail("output_nonaggregate_value")


def _stage_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _bytes_record(root: Path, path: Path, value: bytes) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(value).hexdigest(),
        "size_bytes": len(value),
    }


def _publish_immutable_text(path: Path, text: str) -> bool:
    encoded = text.encode("utf-8")
    if path.exists():
        if not path.is_file() or path.read_bytes() != encoded:
            _fail("output_generation_not_immutable")
        return False
    temporary = _stage_text(path, text)
    published = False
    try:
        os.replace(temporary, path)
        published = True
        try:
            _fsync_directory(path.parent)
        except BaseException:
            path.unlink(missing_ok=True)
            try:
                _fsync_directory(path.parent)
            except OSError:
                pass
            raise
        return True
    finally:
        temporary.unlink(missing_ok=True)
        if published and not path.exists():
            try:
                _fsync_directory(path.parent)
            except OSError:
                pass


def _remove_created_file(path: Path) -> None:
    if not path.exists():
        return
    path.unlink()
    try:
        _fsync_directory(path.parent)
    except OSError:
        pass


def _publish_outputs(
    root: Path,
    markdown_path: Path,
    json_path: Path,
    commit_path: Path,
    markdown: str,
    payload: Mapping[str, Any],
    protected_paths: Collection[Path] = (),
) -> Mapping[str, Any]:
    if len({markdown_path, json_path, commit_path}) != 3:
        _fail("output_paths_collide")
    json_text = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )
    markdown_bytes = markdown.encode("utf-8")
    json_bytes = json_text.encode("utf-8")
    generation_id = hashlib.sha256(markdown_bytes + b"\0" + json_bytes).hexdigest()
    markdown_generation = (
        markdown_path.parent / "generations" / generation_id / markdown_path.name
    )
    json_generation = json_path.parent / "generations" / generation_id / json_path.name
    if len({markdown_generation, json_generation, commit_path}) != 3:
        _fail("output_paths_collide")
    protected = set(protected_paths)
    if {markdown_generation, json_generation, commit_path} & protected:
        _fail("output_overwrites_input")
    markdown_record = _bytes_record(
        root,
        markdown_generation,
        markdown_bytes,
    )
    json_record = _bytes_record(root, json_generation, json_bytes)
    comparison_commit = _mapping(
        _mapping(payload.get("provenance"), "output_provenance").get(
            "comparison_commit"
        ),
        "output_comparison_commit",
    )
    commit: dict[str, Any] = {
        "schema_version": OUTPUT_COMMIT_SCHEMA,
        "status": "committed",
        "generation_id": generation_id,
        "outputs": {
            "markdown": markdown_record,
            "json": json_record,
        },
        "comparison_commit": comparison_commit,
    }
    commit["binding_sha256"] = _mapping_sha256(commit)
    commit_text = (
        json.dumps(
            commit,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )
    created: list[Path] = []
    commit_replaced = False
    try:
        if _publish_immutable_text(markdown_generation, markdown):
            created.append(markdown_generation)
        if _publish_immutable_text(json_generation, json_text):
            created.append(json_generation)
        commit_temporary = _stage_text(commit_path, commit_text)
        try:
            os.replace(commit_temporary, commit_path)
            commit_replaced = True
            _fsync_directory(commit_path.parent)
        finally:
            commit_temporary.unlink(missing_ok=True)
    except BaseException:
        if not commit_replaced:
            for path in reversed(created):
                _remove_created_file(path)
            for directory in {
                markdown_generation.parent,
                json_generation.parent,
            }:
                try:
                    directory.rmdir()
                    _fsync_directory(directory.parent)
                except OSError:
                    pass
        raise
    return commit


def load_committed_wavlm_results(
    *,
    commit_manifest_path: str | Path,
    workspace_root: str | Path,
) -> tuple[str, Mapping[str, Any], Mapping[str, Any]]:
    """Load only a fully committed, hash-matching Markdown/JSON generation."""

    root = Path(workspace_root).resolve(strict=True)
    commit_path = _existing_file(root, commit_manifest_path)
    commit = _load_json(commit_path)
    if set(commit) != {
        "schema_version",
        "status",
        "generation_id",
        "outputs",
        "comparison_commit",
        "binding_sha256",
    }:
        _fail("output_commit_keys")
    if (
        commit.get("schema_version") != OUTPUT_COMMIT_SCHEMA
        or commit.get("status") != "committed"
    ):
        _fail("output_commit_contract")
    generation_id = _sha(
        commit.get("generation_id"),
        "output_commit_generation_id",
    )
    _validate_binding(commit, code_prefix="output_commit")
    outputs = _mapping(commit.get("outputs"), "output_commit_outputs")
    if set(outputs) != {"markdown", "json"}:
        _fail("output_commit_output_set")
    markdown_record, markdown_file = _exact_file_record(
        root,
        outputs.get("markdown"),
        code_prefix="output_committed_markdown",
    )
    json_record, json_file = _exact_file_record(
        root,
        outputs.get("json"),
        code_prefix="output_committed_json",
    )
    if (
        markdown_file.path.parent.name != generation_id
        or json_file.path.parent.name != generation_id
    ):
        _fail("output_commit_generation_paths")
    markdown = markdown_file.path.read_text(encoding="utf-8")
    payload = _load_json(json_file.path)
    if (
        payload.get("schema_version") != OUTPUT_SCHEMA
        or payload.get("status") != "completed"
    ):
        _fail("output_payload_contract")
    expected_generation = hashlib.sha256(
        markdown.encode("utf-8") + b"\0" + json_file.path.read_bytes()
    ).hexdigest()
    if expected_generation != generation_id:
        _fail("output_generation_id_mismatch")
    if (
        markdown_record.get("sha256") != markdown_file.sha256
        or json_record.get("sha256") != json_file.sha256
    ):
        _fail("output_commit_hash_mismatch")
    comparison_record, comparison_file = _exact_file_record(
        root,
        commit.get("comparison_commit"),
        code_prefix="output_source_comparison_commit",
    )
    provenance = _mapping(
        payload.get("provenance"),
        "output_payload_provenance",
    )
    if provenance.get("comparison_commit") != comparison_record:
        _fail("output_comparison_commit_binding")
    if comparison_file.sha256 != comparison_record.get("sha256"):
        _fail("output_comparison_commit_hash")
    return markdown, payload, commit


def render_wavlm_results(
    *,
    base_report_path: str | Path,
    base_registry_path: str | Path,
    large_report_path: str | Path,
    large_registry_path: str | Path,
    comparison_commit_path: str | Path,
    output_markdown_path: str | Path,
    output_json_path: str | Path,
    output_commit_path: str | Path,
    workspace_root: str | Path,
) -> dict[str, Any]:
    """Validate committed dev evidence and transactionally render tables."""

    root = Path(workspace_root).resolve(strict=True)
    inputs = {
        "base_report": _existing_file(root, base_report_path),
        "base_registry": _existing_file(root, base_registry_path),
        "large_report": _existing_file(root, large_report_path),
        "large_registry": _existing_file(root, large_registry_path),
        "comparison_commit": _existing_file(root, comparison_commit_path),
    }
    output_markdown = _output_file(root, output_markdown_path)
    output_json = _output_file(root, output_json_path)
    output_commit = _output_file(root, output_commit_path)
    if any(
        output in inputs.values()
        for output in (output_markdown, output_json, output_commit)
    ):
        _fail("output_overwrites_input")

    heads = {
        "base_plus": _validate_head(
            root,
            inputs["base_report"],
            inputs["base_registry"],
            variant="base_plus",
        ),
        "large": _validate_head(
            root,
            inputs["large_report"],
            inputs["large_registry"],
            variant="large",
        ),
    }
    committed_comparison = _validate_comparison_commit(
        root,
        inputs["comparison_commit"],
    )
    comparison_file = committed_comparison.report_file
    comparison = committed_comparison.report
    for output in (output_markdown, output_json, output_commit):
        if output in {
            committed_comparison.report_file.path,
            committed_comparison.receipt_file.path,
        }:
            _fail("output_overwrites_input")
    _validate_comparison_contract(comparison)
    comparison_sources = _validate_comparison_sources(root, comparison, heads)
    _validate_privacy(comparison)
    phone, utterance = _validate_comparison_metrics(comparison, heads)
    primary = _validate_primary_and_multiplicity(comparison, phone, utterance)
    protected_paths = {
        *inputs.values(),
        committed_comparison.manifest_file.path,
        committed_comparison.report_file.path,
        committed_comparison.receipt_file.path,
        *(path for head in heads.values() for path in head.protected_paths),
        *(source.path for source in comparison_sources.values()),
    }
    if {
        output_markdown,
        output_json,
        output_commit,
    } & protected_paths:
        _fail("output_overwrites_input")

    payload: dict[str, Any] = {
        "schema_version": OUTPUT_SCHEMA,
        "status": "completed",
        "scope": {
            "development_only": True,
            "oracle_alignment": True,
            "locked_test_accessed": False,
            "stack_accessed": False,
            "calibration_accessed": False,
            "promotion_decision_allowed": False,
        },
        "provenance": {
            "base_plus_report_sha256": heads["base_plus"].report_file.sha256,
            "base_plus_registry_sha256": heads["base_plus"].registry_file.sha256,
            "large_report_sha256": heads["large"].report_file.sha256,
            "large_registry_sha256": heads["large"].registry_file.sha256,
            "comparison_report_sha256": comparison_file.sha256,
            "comparison_receipt_sha256": (committed_comparison.receipt_file.sha256),
            "comparison_commit": {
                "path": committed_comparison.manifest_file.path.relative_to(
                    root
                ).as_posix(),
                "sha256": committed_comparison.manifest_file.sha256,
                "size_bytes": committed_comparison.manifest_file.size_bytes,
            },
            "comparison_config_sha256": comparison_sources["config"].sha256,
            "comparison_script_sha256": comparison_sources["comparison_script"].sha256,
            "comparison_module_sha256": comparison_sources["comparison_module"].sha256,
            "hash_chain_validated": True,
        },
        "seeds": list(EXPECTED_SEEDS),
        "phone": _safe_numeric_copy(phone),
        "utterance": _safe_numeric_copy(utterance),
        "primary_comparison": _safe_numeric_copy(primary),
        "privacy": {
            "raw_data_read": False,
            "environment_files_read": False,
            "prediction_contents_read": False,
            "direct_or_pseudonymous_identifiers_exported": False,
            "artifact_paths_exported": False,
            "aggregate_only": True,
        },
    }
    markdown = _render_markdown(phone, utterance, primary)
    _publish_outputs(
        root,
        output_markdown,
        output_json,
        output_commit,
        markdown,
        payload,
        protected_paths=protected_paths,
    )
    return payload


__all__ = [
    "COMPARISON_REPORT_SCHEMA",
    "EXPECTED_SEEDS",
    "HEAD_REPORT_SCHEMA",
    "OUTPUT_SCHEMA",
    "OUTPUT_COMMIT_SCHEMA",
    "OUTCOMES",
    "ResultsValidationError",
    "load_committed_wavlm_results",
    "render_wavlm_results",
]
