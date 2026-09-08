from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
from typing import Any, Mapping, cast

from voice_coach.reproducibility.wavlm_cache import (
    ALLOWED_SPLITS,
    CACHE_REPORT_SCHEMA_VERSION,
    _Issues,
    _ScanState,
    _load_json,
    _load_yaml,
    _mapping,
    _safe_path,
    _validate_kind,
    sha256_file,
)


REVIEW_SCHEMA_VERSION = "voice_coach.wavlm_feature_cache_smoke_review.v1"
SMOKE_UTTERANCES = 100
KINDS = ("utterance", "phone", "word")
EVALUATION_SPLITS = frozenset({"stack", "calibration", "locked_test"})
SOURCE_COLUMNS = (
    "id",
    "speaker_id",
    "split",
    "audio_path",
    "prompt_sha256",
    "qc_flags",
    "word_intervals",
    "target_phone_intervals",
    "phone_alignments",
    "l1",
    "gender",
    "proficiency",
)
AUDITED_FULL_ROWS = {
    "utterance": 169_837,
    "phone": 2_233_039,
    "word": 408_790,
}
UTTERANCE_NAMESPACE = "voice-coach.aihub71490.utterance.v1"
KNOWN_CANONICAL_CACHE_ROOTS = (
    "data/processed/aihub71490/features/wavlm_base_plus_frozen_v1",
    "data/processed/aihub71490/features/wavlm_large_frozen_v1",
)
KNOWN_CANONICAL_REPORTS = (
    "data/processed/aihub71490/features/wavlm_base_plus_frozen_v1_report.json",
    "data/processed/aihub71490/features/wavlm_large_frozen_v1_report.json",
)
REVIEWER_CLI_RELATIVE_PATH = "scripts/review_wavlm_feature_cache_smoke.py"
REVIEWER_CORE_RELATIVE_PATH = "src/voice_coach/reproducibility/wavlm_smoke_review.py"
FULL_CACHE_VALIDATION_REQUIRES_ALL_ALLOWED_SPLITS = True
REPORTED_PROHIBITED_OUTPUT_COLUMNS = frozenset(
    {
        "id",
        "utterance_id",
        "raw_utterance_id",
        "audio_path",
        "json_path",
        "textgrid_path",
        "source_path",
        "source_file_stem",
        "file_stem",
        "word_label",
        "realized_label",
        "realized_phone_identity",
        "alignment_operation",
        "operation",
        "error_tag",
        "error_tags",
        "pronunciation_score",
        "fluency_score",
        "comprehensibility_score",
    }
)


@dataclass(frozen=True, slots=True)
class SmokeVariantContract:
    name: str
    experiment_id: str
    model_id: str
    revision: str
    hidden_size: int
    source_rows: int
    config_sha256: str
    builder_sha256: str
    model_config_sha256: str
    model_checkpoint_sha256: str
    do_normalize: bool
    preprocessor_url: str
    preprocessor_sha256: str | None = None
    builder_dependency_path: str | None = None
    builder_dependency_sha256: str | None = None
    require_explicit_source_semantics: bool = False
    audited_full_rows: Mapping[str, int] | None = None

    @property
    def full_rows(self) -> Mapping[str, int]:
        return self.audited_full_rows or AUDITED_FULL_ROWS


VARIANT_CONTRACTS: dict[str, SmokeVariantContract] = {
    "aihub71490_wavlm_base_plus_frozen_v1": SmokeVariantContract(
        name="base_plus",
        experiment_id="aihub71490_wavlm_base_plus_frozen_v1",
        model_id="microsoft/wavlm-base-plus",
        revision="4c66d4806a428f2e922ccfa1a962776e232d487b",
        hidden_size=768,
        source_rows=322_380,
        config_sha256=(
            "3641d04c974363d615cbc914a3e9d80eff39f9c0e64bc338aa2ff04ab9fef1ba"
        ),
        builder_sha256=(
            "2acd48d4f7dbb735dab96cdf4b9222a654627a96cf6a068539f0480839a56dde"
        ),
        model_config_sha256=(
            "fea6df1c2700a3954fc07e70588aecc9055eeb28db2ff57151a2db0d19180ed4"
        ),
        model_checkpoint_sha256=(
            "3bb273a6ace99408b50cfc81afdbb7ef2de02da2eab0234e18db608ce692fe51"
        ),
        do_normalize=False,
        preprocessor_url=(
            "https://huggingface.co/microsoft/wavlm-base-plus/blob/"
            "4c66d4806a428f2e922ccfa1a962776e232d487b/preprocessor_config.json"
        ),
    ),
    "aihub71490_wavlm_large_frozen_v1": SmokeVariantContract(
        name="large",
        experiment_id="aihub71490_wavlm_large_frozen_v1",
        model_id="microsoft/wavlm-large",
        revision="c1423ed94bb01d80a3f5ce5bc39f6026a0f4828c",
        hidden_size=1_024,
        source_rows=322_380,
        config_sha256=(
            "0191afdba13e38619ab6f2c24494f0766cf9f16a802575a132eda7dadcd9c4fd"
        ),
        builder_sha256=(
            "bfca5a5d065a7a55b3fce5671750dd4011f8a2b0e538d3c3a6aa8eab4972772f"
        ),
        model_config_sha256=(
            "a3d8fe831aaf63d725b54a8ac36f3549cd4365c5086774b2c89cabbc6f9e129d"
        ),
        model_checkpoint_sha256=(
            "fdee460e529396ddb2f8c8e8ce0ad74cfb747b726bc6f612e666c7c1e1963c9d"
        ),
        do_normalize=True,
        preprocessor_url=(
            "https://huggingface.co/microsoft/wavlm-large/blob/"
            "c1423ed94bb01d80a3f5ce5bc39f6026a0f4828c/"
            "preprocessor_config.json"
        ),
        preprocessor_sha256=(
            "60ca5a31e13f69ee2fbf147504c8676db5f6398fd7a6b12294341dff838edfcf"
        ),
        builder_dependency_path="scripts/build_wavlm_feature_cache.py",
        builder_dependency_sha256=(
            "2acd48d4f7dbb735dab96cdf4b9222a654627a96cf6a068539f0480839a56dde"
        ),
        require_explicit_source_semantics=True,
    ),
}


class SmokeReviewError(ValueError):
    """Raised when a reviewer invocation is unsafe or structurally unreadable."""


class UnsafeSmokeReviewPath(SmokeReviewError):
    """Raised before writing when a path crosses a reviewer safety boundary."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_int(value: object, *, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _is_positive_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) > 0
    )


def _safe_input(
    root: Path,
    configured: str | Path,
    *,
    role: str,
) -> tuple[Path, str]:
    candidate = Path(configured)
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_symlink():
        raise UnsafeSmokeReviewPath(f"{role} must not be a symlink")
    try:
        resolved, relative = _safe_path(root, candidate, role=role)
    except ValueError as exc:
        raise UnsafeSmokeReviewPath(f"{role} is unsafe") from exc
    return resolved, relative


def _reviewer_source_fingerprints(root: Path) -> dict[str, str]:
    cli, cli_relative = _safe_input(
        root,
        REVIEWER_CLI_RELATIVE_PATH,
        role="reviewer CLI",
    )
    core, core_relative = _safe_input(
        root,
        REVIEWER_CORE_RELATIVE_PATH,
        role="reviewer core",
    )
    if cli_relative != REVIEWER_CLI_RELATIVE_PATH:
        raise UnsafeSmokeReviewPath("reviewer CLI path differs from the pin")
    if core_relative != REVIEWER_CORE_RELATIVE_PATH:
        raise UnsafeSmokeReviewPath("reviewer core path differs from the pin")
    return {
        "cli_path": cli_relative,
        "cli_sha256": sha256_file(cli),
        "core_path": core_relative,
        "core_sha256": sha256_file(core),
    }


def safe_review_output_path(
    workspace_root: str | Path,
    output_path: str | Path,
) -> Path:
    root = Path(workspace_root).resolve()
    try:
        output, _ = _safe_path(
            root,
            output_path,
            role="review output",
            require_exists=False,
        )
    except ValueError as exc:
        raise UnsafeSmokeReviewPath("review output is unsafe") from exc
    if output.suffix.lower() != ".json" or (output.exists() and output.is_dir()):
        raise UnsafeSmokeReviewPath("review output must be a JSON file")
    canonical_caches = tuple(
        (root / value).resolve() for value in KNOWN_CANONICAL_CACHE_ROOTS
    )
    canonical_reports = {(root / value).resolve() for value in KNOWN_CANONICAL_REPORTS}
    if output in canonical_reports or any(
        output == cache or cache in output.parents for cache in canonical_caches
    ):
        raise UnsafeSmokeReviewPath("review output overlaps a pinned full output")
    return output


def failure_review(
    error_codes: Mapping[str, int] | list[dict[str, Any]],
) -> dict[str, Any]:
    if isinstance(error_codes, Mapping):
        errors = [
            {"code": str(code), "count": int(count)}
            for code, count in sorted(error_codes.items())
            if _is_int(count, minimum=1)
        ]
    else:
        errors = [
            {"code": str(item.get("code")), "count": int(item.get("count", 1))}
            for item in error_codes
            if isinstance(item, Mapping)
            and isinstance(item.get("code"), str)
            and _is_int(item.get("count", 1), minimum=1)
        ]
    if not errors:
        errors = [{"code": "smoke_review_failed", "count": 1}]
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "status": "failed",
        "approved": False,
        "full_run_approved": False,
        "review_type": "automated_engineering_gate",
        "human_expert_review": False,
        "privacy": {
            "aggregate_only_output": True,
            "row_identifiers_exported": False,
            "embedding_values_exported": False,
            "raw_data_read": False,
            "source_modeling_manifest_read": False,
            "environment_files_read": False,
        },
        "errors": errors,
    }


def _resolve_paths(
    *,
    workspace_root: Path,
    cache_root: str | Path,
    report_path: str | Path,
    config_path: str | Path,
    builder_path: str | Path,
    output_path: str | Path,
) -> tuple[Path, Path, Path, Path, Path, dict[str, str]]:
    cache, cache_relative = _safe_input(
        workspace_root,
        cache_root,
        role="smoke cache",
    )
    report, report_relative = _safe_input(
        workspace_root,
        report_path,
        role="smoke report",
    )
    config, config_relative = _safe_input(
        workspace_root,
        config_path,
        role="experiment config",
    )
    builder, builder_relative = _safe_input(
        workspace_root,
        builder_path,
        role="cache builder",
    )
    output = safe_review_output_path(workspace_root, output_path)
    canonical_caches = tuple(
        (workspace_root / value).resolve() for value in KNOWN_CANONICAL_CACHE_ROOTS
    )
    canonical_reports = {
        (workspace_root / value).resolve() for value in KNOWN_CANONICAL_REPORTS
    }
    if any(
        cache == canonical or canonical in cache.parents
        for canonical in canonical_caches
    ):
        raise UnsafeSmokeReviewPath("reviewer refuses a pinned full cache")
    if report in canonical_reports or any(
        canonical in report.parents for canonical in canonical_caches
    ):
        raise UnsafeSmokeReviewPath("reviewer refuses a pinned full report")
    if not cache.is_dir():
        raise UnsafeSmokeReviewPath("smoke cache must be a directory")
    if not report.is_file() or report.suffix.lower() != ".json":
        raise UnsafeSmokeReviewPath("smoke report must be a JSON file")
    if not config.is_file() or config.suffix.lower() not in {".yaml", ".yml"}:
        raise UnsafeSmokeReviewPath("experiment config must be YAML")
    if not builder.is_file() or builder.suffix.lower() != ".py":
        raise UnsafeSmokeReviewPath("cache builder must be Python")
    if cache == report or cache in report.parents:
        raise UnsafeSmokeReviewPath("smoke report must be outside the smoke cache")
    if output in {report, config, builder, cache} or cache in output.parents:
        raise UnsafeSmokeReviewPath("review output overlaps a reviewer input")
    return (
        cache,
        report,
        config,
        builder,
        output,
        {
            "cache": cache_relative,
            "report": report_relative,
            "config": config_relative,
            "builder": builder_relative,
        },
    )


def _validate_canonical_separation(
    *,
    root: Path,
    cache: Path,
    report: Path,
    output: Path,
    config_output: Mapping[str, Any],
) -> None:
    try:
        canonical_cache, _ = _safe_path(
            root,
            str(config_output.get("root")),
            role="canonical cache",
            require_exists=False,
        )
        canonical_report, _ = _safe_path(
            root,
            str(config_output.get("report")),
            role="canonical report",
            require_exists=False,
        )
    except ValueError as exc:
        raise UnsafeSmokeReviewPath("canonical output contract is unsafe") from exc
    if cache == canonical_cache or canonical_cache in cache.parents:
        raise UnsafeSmokeReviewPath("reviewer refuses the canonical full cache")
    if report == canonical_report or canonical_cache in report.parents:
        raise UnsafeSmokeReviewPath("reviewer refuses the canonical full report")
    if (
        output in {canonical_cache, canonical_report}
        or canonical_cache in output.parents
    ):
        raise UnsafeSmokeReviewPath("review output overlaps canonical full output")


def _frame_geometry(config_model: Mapping[str, Any]) -> tuple[int, int]:
    kernels = config_model.get("conv_kernel")
    strides = config_model.get("conv_stride")
    if (
        not isinstance(kernels, list)
        or not isinstance(strides, list)
        or len(kernels) != len(strides)
        or not kernels
        or any(not _is_int(value, minimum=1) for value in [*kernels, *strides])
    ):
        raise SmokeReviewError("convolution geometry is invalid")
    jump = 1
    receptive_field = 1
    for kernel, stride in zip(kernels, strides, strict=True):
        receptive_field += (int(kernel) - 1) * jump
        jump *= int(stride)
    return jump, receptive_field


def _validate_model_files(
    *,
    root: Path,
    contract: SmokeVariantContract,
    config_model: Mapping[str, Any],
    report_model: Mapping[str, Any],
    issues: _Issues,
) -> None:
    try:
        model_cache, _ = _safe_path(
            root,
            str(config_model.get("cache_dir")),
            role="model cache",
        )
    except ValueError as exc:
        raise SmokeReviewError("model cache is unsafe") from exc
    repository = "models--" + contract.model_id.replace("/", "--")
    snapshot = model_cache / repository / "snapshots" / contract.revision
    files = {
        "config": snapshot / str(config_model.get("config_file", "config.json")),
        "checkpoint": snapshot
        / str(config_model.get("checkpoint_file", "pytorch_model.bin")),
    }
    if contract.preprocessor_sha256 is not None:
        files["preprocessor"] = snapshot / "preprocessor_config.json"
    expected_hashes = {
        "config": contract.model_config_sha256,
        "checkpoint": contract.model_checkpoint_sha256,
        "preprocessor": contract.preprocessor_sha256,
    }
    for role, path in files.items():
        resolved = path.resolve()
        safe = path.is_file()
        try:
            resolved.relative_to(root)
        except ValueError:
            safe = False
        issues.check(safe, f"model_{role}_file_missing_or_unsafe")
        if not safe:
            continue
        actual_hash = sha256_file(path)
        issues.check(
            actual_hash == expected_hashes[role],
            f"model_{role}_hash_mismatch",
        )
        report_key = (
            "preprocessor_config_sha256" if role == "preprocessor" else f"{role}_sha256"
        )
        issues.check(
            report_model.get(report_key) == actual_hash,
            f"report_model_{role}_hash_mismatch",
        )
        config_key = (
            "preprocessor_config_sha256" if role == "preprocessor" else f"{role}_sha256"
        )
        if config_key in config_model:
            issues.check(
                config_model.get(config_key) == actual_hash,
                f"config_model_{role}_hash_mismatch",
            )
    preprocessor_path = files.get("preprocessor")
    if preprocessor_path is not None and preprocessor_path.is_file():
        document = _load_json(preprocessor_path)
        issues.check(
            document
            == {
                "do_normalize": True,
                "feature_extractor_type": "Wav2Vec2FeatureExtractor",
                "feature_size": 1,
                "padding_side": "right",
                "padding_value": 0.0,
                "return_attention_mask": True,
                "sampling_rate": 16_000,
            },
            "large_preprocessor_fields_mismatch",
        )


def _validate_preprocessor_contract(
    *,
    contract: SmokeVariantContract,
    config_model: Mapping[str, Any],
    report_model: Mapping[str, Any],
    issues: _Issues,
) -> None:
    preprocessor = report_model.get("preprocessor_contract")
    issues.check(
        isinstance(preprocessor, Mapping),
        "preprocessor_contract_missing",
    )
    if not isinstance(preprocessor, Mapping):
        return
    expected = {
        "sampling_rate_hz": 16_000,
        "do_normalize": contract.do_normalize,
        "return_attention_mask": True,
        "source": "official_pinned_revision",
        "source_url": contract.preprocessor_url,
        "auto_feature_extractor_used": False,
    }
    for key, value in expected.items():
        issues.check(
            preprocessor.get(key) == value,
            "preprocessor_contract_mismatch",
        )
    issues.check(
        config_model.get("do_normalize") is contract.do_normalize,
        "config_do_normalize_mismatch",
    )
    issues.check(
        report_model.get("do_normalize") is contract.do_normalize,
        "report_do_normalize_mismatch",
    )
    if contract.preprocessor_sha256 is not None:
        issues.check(
            preprocessor.get("config_sha256") == contract.preprocessor_sha256,
            "preprocessor_contract_hash_mismatch",
        )
        issues.check(
            preprocessor.get("local_preprocessor_config_present") is True
            and preprocessor.get("local_preprocessor_config_validated") is True,
            "preprocessor_local_validation_missing",
        )


def _validate_source_scan(
    *,
    contract: SmokeVariantContract,
    progress: Mapping[str, Any],
    policy: Mapping[str, Any],
    state: _ScanState,
    issues: _Issues,
) -> None:
    scanned = progress.get("source_rows_scanned")
    scanned_count = int(cast(int, scanned)) if _is_int(scanned) else -1
    issues.check(
        scanned_count >= SMOKE_UTTERANCES and scanned_count < contract.source_rows,
        "source_rows_scanned_invalid",
    )
    issues.check(
        progress.get("source_scan_complete") is False,
        "smoke_source_scan_must_be_incomplete",
    )
    source_counts = progress.get("source_rows_by_split")
    included_counts = progress.get("included_rows_by_split")
    exclusions = progress.get("exclusions")
    valid_source_counts = (
        isinstance(source_counts, Mapping)
        and bool(source_counts)
        and all(
            isinstance(key, str) and key and _is_int(value, minimum=1)
            for key, value in source_counts.items()
        )
    )
    valid_included_counts = (
        isinstance(included_counts, Mapping)
        and bool(included_counts)
        and all(
            isinstance(key, str) and key and _is_int(value, minimum=1)
            for key, value in included_counts.items()
        )
    )
    valid_exclusions = isinstance(exclusions, Mapping) and all(
        isinstance(key, str) and key and _is_int(value)
        for key, value in exclusions.items()
    )
    issues.check(valid_source_counts, "source_split_counts_invalid")
    issues.check(valid_included_counts, "included_split_counts_invalid")
    issues.check(valid_exclusions, "source_exclusion_counts_invalid")
    if not (valid_source_counts and valid_included_counts and valid_exclusions):
        return
    assert isinstance(source_counts, Mapping)
    assert isinstance(included_counts, Mapping)
    assert isinstance(exclusions, Mapping)
    source = {str(key): int(value) for key, value in source_counts.items()}
    included = {str(key): int(value) for key, value in included_counts.items()}
    excluded = {str(key): int(value) for key, value in exclusions.items()}
    issues.check(
        sum(source.values()) == scanned_count,
        "source_split_count_sum_mismatch",
    )
    issues.check(
        sum(included.values()) == SMOKE_UTTERANCES,
        "included_split_count_sum_mismatch",
    )
    issues.check(
        included == dict(state.split_counts["utterance"]),
        "included_split_counts_do_not_match_cache",
    )
    issues.check(
        set(included).issubset(ALLOWED_SPLITS),
        "included_forbidden_split",
    )
    issues.check(
        sum(excluded.values()) + SMOKE_UTTERANCES == scanned_count,
        "source_accounting_mismatch",
    )
    forbidden_source_rows = sum(
        count for split, count in source.items() if split not in ALLOWED_SPLITS
    )
    issues.check(
        excluded.get("split_not_allowed", 0) == forbidden_source_rows,
        "evaluation_split_exclusion_count_mismatch",
    )
    issues.check(
        policy.get("allowed_splits") == list(ALLOWED_SPLITS),
        "report_allowed_splits_mismatch",
    )
    issues.check(
        set(policy.get("evaluation_splits", [])) == EVALUATION_SPLITS,
        "report_evaluation_splits_mismatch",
    )
    issues.check(
        policy.get("evaluation_split_metric_access") == "forbidden"
        and policy.get("metric_evaluation_performed") is False
        and policy.get("holistic_score_columns_read") == [],
        "evaluation_metric_access_contract_invalid",
    )
    if contract.require_explicit_source_semantics:
        issues.check(
            policy.get("source_manifest_columns_deserialized_before_split_filter")
            == list(SOURCE_COLUMNS),
            "source_deserialization_contract_mismatch",
        )
        expected_semantics = {
            "evaluation_manifest_annotations_may_be_deserialized_for_filtering": True,
            "evaluation_audio_decoded": False,
            "evaluation_rows_sent_to_encoder": False,
            "evaluation_labels_used_for_training_or_metrics": False,
        }
        for key, expected in expected_semantics.items():
            issues.check(
                policy.get(key) is expected,
                "evaluation_source_semantics_mismatch",
            )


def _validate_resource_estimate(
    *,
    config_gate: Mapping[str, Any],
    report_resource: Mapping[str, Any],
    report_runtime: Mapping[str, Any],
    shard_reports: Mapping[str, Mapping[str, Any]],
    state: _ScanState,
    contract: SmokeVariantContract,
    issues: _Issues,
) -> dict[str, Any]:
    reference = config_gate.get("resource_estimate_reference_rows")
    safety_factor = config_gate.get("resource_estimate_safety_factor")
    valid_reference = (
        isinstance(reference, Mapping)
        and {kind: reference.get(kind) for kind in KINDS} == dict(contract.full_rows)
        and all(_is_int(reference.get(kind), minimum=1) for kind in KINDS)
    )
    issues.check(valid_reference, "resource_reference_rows_mismatch")
    valid_safety = (
        isinstance(safety_factor, (int, float))
        and not isinstance(safety_factor, bool)
        and math.isfinite(float(safety_factor))
        and float(safety_factor) >= 1
    )
    issues.check(valid_safety, "resource_safety_factor_invalid")
    peak_rss = report_runtime.get("peak_process_rss_bytes")
    issues.check(
        _is_int(peak_rss, minimum=1),
        "peak_process_rss_invalid",
    )
    if not valid_reference or not valid_safety:
        return {
            "approved": False,
            "peak_rss_bytes": (
                int(cast(int, peak_rss)) if _is_int(peak_rss, minimum=1) else 0
            ),
            "estimated_full_cache_upper_bytes": 0,
            "max_row_group_rows": 0,
        }
    assert isinstance(reference, Mapping)
    reference_rows = {kind: int(reference[kind]) for kind in KINDS}
    safety_factor_value = float(cast(float | int, safety_factor))
    peak_rss_bytes = int(cast(int, peak_rss))
    observed_bytes = {kind: int(shard_reports[kind]["bytes_checked"]) for kind in KINDS}
    observed_rows = {kind: int(state.rows_checked[kind]) for kind in KINDS}
    issues.check(
        all(observed_bytes[kind] > 0 and observed_rows[kind] > 0 for kind in KINDS),
        "resource_observation_empty",
    )
    bytes_per_row = {kind: observed_bytes[kind] / observed_rows[kind] for kind in KINDS}
    projected = {
        kind: math.ceil(bytes_per_row[kind] * reference_rows[kind]) for kind in KINDS
    }
    projected_total = sum(projected.values())
    estimated_upper = math.ceil(projected_total * safety_factor_value)
    dimensions = {
        "utterance": contract.hidden_size * 2,
        "phone": contract.hidden_size,
        "word": contract.hidden_size,
    }
    fixed_bytes = {kind: reference_rows[kind] * dimensions[kind] * 2 for kind in KINDS}
    expected_report = {
        "method": "smoke compressed bytes per row scaled to audited full rows",
        "reference_rows": reference_rows,
        "projected_compressed_bytes_by_kind": projected,
        "projected_compressed_bytes_total": projected_total,
        "fixed_float16_embedding_bytes_by_kind": fixed_bytes,
        "fixed_float16_embedding_bytes_total": sum(fixed_bytes.values()),
        "fixed_embedding_estimate_excludes_metadata_and_parquet_overhead": True,
        "safety_factor": safety_factor_value,
        "estimated_full_cache_upper_bytes": estimated_upper,
        "estimate_is_capacity_planning_not_measured_full_output": True,
    }
    for key, expected in expected_report.items():
        actual = report_resource.get(key)
        if isinstance(expected, float):
            matches = (
                isinstance(actual, (int, float))
                and not isinstance(actual, bool)
                and math.isclose(float(actual), expected, rel_tol=1e-12, abs_tol=1e-12)
            )
        else:
            matches = actual == expected
        issues.check(matches, "reported_resource_estimate_mismatch")
    reported_bytes_per_row = report_resource.get("observed_compressed_bytes_per_row")
    if not isinstance(reported_bytes_per_row, Mapping):
        issues.add("reported_resource_estimate_mismatch")
    else:
        for kind, expected in bytes_per_row.items():
            actual = reported_bytes_per_row.get(kind)
            issues.check(
                isinstance(actual, (int, float))
                and not isinstance(actual, bool)
                and math.isclose(
                    float(actual),
                    expected,
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                ),
                "reported_resource_estimate_mismatch",
            )
    max_row_group_rows = max(
        int(shard_reports[kind]["max_row_group_rows"]) for kind in KINDS
    )
    issues.check(
        1 <= max_row_group_rows <= 4_096,
        "max_row_group_rows_invalid",
    )
    return {
        "approved": True,
        "peak_rss_bytes": peak_rss_bytes,
        "estimated_full_cache_upper_bytes": estimated_upper,
        "max_row_group_rows": max_row_group_rows,
        "safety_factor": safety_factor_value,
        "projected_compressed_bytes_total": projected_total,
        "observed_cache_bytes": sum(observed_bytes.values()),
    }


def review_wavlm_feature_cache_smoke(
    *,
    cache_root: str | Path,
    report_path: str | Path,
    config_path: str | Path,
    builder_path: str | Path,
    output_path: str | Path,
    workspace_root: str | Path,
    batch_rows: int = 2_048,
) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    if not root.is_dir() or root.is_symlink():
        raise SmokeReviewError("workspace root is invalid")
    if not _is_int(batch_rows, minimum=1):
        raise SmokeReviewError("batch_rows must be a positive integer")
    reviewer_source = _reviewer_source_fingerprints(root)
    cache, report_file, config_file, builder_file, output_file, relative = (
        _resolve_paths(
            workspace_root=root,
            cache_root=cache_root,
            report_path=report_path,
            config_path=config_path,
            builder_path=builder_path,
            output_path=output_path,
        )
    )
    config = _load_yaml(config_file)
    experiment = _mapping(config.get("experiment"), "config.experiment")
    experiment_id = experiment.get("id")
    if not isinstance(experiment_id, str) or experiment_id not in VARIANT_CONTRACTS:
        raise SmokeReviewError("unsupported frozen-WavLM smoke experiment")
    contract = VARIANT_CONTRACTS[experiment_id]
    config_model = _mapping(config.get("model"), "config.model")
    config_data = _mapping(config.get("data"), "config.data")
    config_features = _mapping(config.get("features"), "config.features")
    config_output = _mapping(config.get("output"), "config.output")
    config_gate = _mapping(config.get("full_run_gate"), "config.full_run_gate")
    _validate_canonical_separation(
        root=root,
        cache=cache,
        report=report_file,
        output=output_file,
        config_output=config_output,
    )
    report = _load_json(report_file)
    report_source = _mapping(report.get("source"), "report.source")
    report_model = _mapping(report.get("model"), "report.model")
    report_runtime = _mapping(report.get("runtime"), "report.runtime")
    report_policy = _mapping(report.get("policy"), "report.policy")
    report_configuration = _mapping(
        report.get("configuration"),
        "report.configuration",
    )
    progress = _mapping(report.get("progress"), "report.progress")
    outputs = _mapping(report.get("outputs"), "report.outputs")
    report_resource = _mapping(
        report.get("resource_estimate"),
        "report.resource_estimate",
    )
    frame_audit = _mapping(
        report.get("frame_mapping_audit"),
        "report.frame_mapping_audit",
    )
    issues = _Issues()
    config_sha256 = sha256_file(config_file)
    builder_sha256 = sha256_file(builder_file)
    report_sha256 = sha256_file(report_file)

    issues.check(
        config_sha256 == contract.config_sha256,
        "experiment_config_pin_mismatch",
    )
    issues.check(
        builder_sha256 == contract.builder_sha256,
        "cache_builder_pin_mismatch",
    )
    issues.check(
        experiment.get("stage") == "feature_cache"
        and experiment.get("development_only") is True,
        "experiment_contract_mismatch",
    )
    expected_model = {
        "id": contract.model_id,
        "revision": contract.revision,
        "hidden_size": contract.hidden_size,
        "local_files_only": True,
        "frozen": True,
        "do_normalize": contract.do_normalize,
        "return_attention_mask": True,
    }
    for key, expected in expected_model.items():
        issues.check(
            config_model.get(key) == expected,
            "config_model_contract_mismatch",
        )
    issues.check(
        config_features.get("storage_precision") == "float16",
        "config_storage_precision_mismatch",
    )
    issues.check(
        config_gate.get("smoke_utterances") == SMOKE_UTTERANCES,
        "config_smoke_limit_mismatch",
    )
    issues.check(
        report.get("schema_version") == CACHE_REPORT_SCHEMA_VERSION,
        "cache_report_schema_mismatch",
    )
    issues.check(report.get("status") == "completed", "cache_report_not_completed")
    issues.check(
        report_configuration.get("limit") == SMOKE_UTTERANCES
        and not isinstance(report_configuration.get("limit"), bool)
        and report_configuration.get("full_run") is False,
        "cache_report_not_exact_smoke",
    )
    issues.check(
        report_configuration.get("full_run_gate") == {},
        "smoke_report_contains_full_run_gate",
    )
    writer = report_configuration.get("streaming_writer")
    if not isinstance(writer, Mapping):
        issues.add("report_streaming_writer_missing")
    else:
        for kind in KINDS:
            reported = writer.get(kind)
            issues.check(
                isinstance(reported, Mapping)
                and reported.get("shard_rows")
                == config_output.get(f"{kind}_shard_rows")
                and reported.get("row_group_rows")
                == config_output.get(f"{kind}_row_group_rows"),
                f"{kind}_report_streaming_writer_mismatch",
            )

    issues.check(
        report_source.get("experiment_config_sha256") == config_sha256,
        "report_config_hash_mismatch",
    )
    issues.check(
        report_source.get("builder_sha256") == builder_sha256,
        "report_builder_hash_mismatch",
    )
    manifest_sha = config_data.get("modeling_manifest_sha256")
    issues.check(
        report_source.get("modeling_manifest_sha256") == manifest_sha,
        "source_manifest_pin_mismatch",
    )
    prompt_file, _ = _safe_input(
        root,
        str(config_data.get("prompt_challenge_reservation")),
        role="prompt reservation",
    )
    issues.check(
        report_source.get("prompt_reservation_sha256") == sha256_file(prompt_file),
        "prompt_reservation_hash_mismatch",
    )
    if contract.builder_dependency_path is not None:
        dependency_file, _ = _safe_input(
            root,
            contract.builder_dependency_path,
            role="builder dependency",
        )
        issues.check(
            report_source.get("builder_dependency_path")
            == contract.builder_dependency_path
            and report_source.get("builder_dependency_sha256")
            == contract.builder_dependency_sha256
            and sha256_file(dependency_file) == contract.builder_dependency_sha256,
            "builder_dependency_pin_mismatch",
        )
        issues.check(
            report_source.get("preprocessor_config_sha256")
            == contract.preprocessor_sha256,
            "source_preprocessor_pin_mismatch",
        )

    for key, expected in (
        ("id", contract.model_id),
        ("revision", contract.revision),
        ("hidden_size", contract.hidden_size),
        ("config_sha256", contract.model_config_sha256),
        ("checkpoint_sha256", contract.model_checkpoint_sha256),
        ("frozen", True),
        ("local_files_only", True),
    ):
        issues.check(
            report_model.get(key) == expected,
            "report_model_contract_mismatch",
        )
    if contract.preprocessor_sha256 is not None:
        issues.check(
            report_model.get("preprocessor_config_sha256")
            == contract.preprocessor_sha256,
            "report_preprocessor_pin_mismatch",
        )
    issues.check(
        _is_int(report_model.get("parameter_count"), minimum=1),
        "model_parameter_count_invalid",
    )
    _validate_preprocessor_contract(
        contract=contract,
        config_model=config_model,
        report_model=report_model,
        issues=issues,
    )
    _validate_model_files(
        root=root,
        contract=contract,
        config_model=config_model,
        report_model=report_model,
        issues=issues,
    )

    issues.check(
        report_runtime.get("storage_precision") == "float16",
        "runtime_storage_precision_mismatch",
    )
    issues.check(
        report_runtime.get("oom_count") == 0
        and not isinstance(report_runtime.get("oom_count"), bool),
        "runtime_oom_count_nonzero",
    )
    if contract.name == "large":
        output_dtypes = report_runtime.get("observed_model_output_dtypes")
        issues.check(
            report_runtime.get("cuda_autocast_precision") == "bfloat16"
            and isinstance(output_dtypes, Mapping)
            and bool(output_dtypes)
            and all(_is_int(value, minimum=1) for value in output_dtypes.values()),
            "large_runtime_precision_contract_invalid",
        )
    else:
        issues.check(
            report_runtime.get("inference_precision") == "bfloat16",
            "base_runtime_precision_contract_invalid",
        )
    for key in (
        "contains_direct_identifiers",
        "contains_transcript_text",
        "contains_audio_bytes",
        "contains_realized_phone_identity",
        "contains_alignment_operation",
        "shareable",
    ):
        issues.check(
            report_policy.get(key) is False,
            f"report_policy_{key}_invalid",
        )
    issues.check(
        report_policy.get("oracle_alignment") is True
        and report_policy.get("access_scope") == "restricted_internal"
        and report_policy.get("utterance_id_namespace") == UTTERANCE_NAMESPACE,
        "report_policy_core_contract_invalid",
    )
    prohibited = report_policy.get("prohibited_output_columns")
    issues.check(
        isinstance(prohibited, list)
        and set(prohibited) == REPORTED_PROHIBITED_OUTPUT_COLUMNS,
        "report_prohibited_output_columns_invalid",
    )
    privacy_audit = report.get("privacy_audit")
    if not isinstance(privacy_audit, Mapping):
        issues.add("privacy_audit_missing")
    else:
        hits = privacy_audit.get("schema_prohibited_column_hits")
        issues.check(
            isinstance(hits, Mapping) and all(hits.get(kind) == [] for kind in KINDS),
            "reported_privacy_column_hits",
        )
        for key in (
            "raw_locator_values_exported",
            "raw_file_stem_values_exported",
            "realized_phone_values_exported",
            "alignment_operation_values_exported",
            "word_transcript_values_exported",
        ):
            issues.check(
                privacy_audit.get(key) is False,
                f"privacy_audit_{key}_invalid",
            )

    if any(cache.rglob("*.part")) or any(cache.parent.glob(f".{cache.name}.part-*")):
        issues.add("partial_publish_artifact_present")
    state = _ScanState()
    shard_reports: dict[str, Mapping[str, Any]] = {}
    for kind in KINDS:
        shard_reports[kind] = _validate_kind(
            kind=kind,
            cache_root=cache,
            output_report=outputs,
            config_output=config_output,
            hidden_size=contract.hidden_size,
            model=config_model,
            source=report_source,
            config_sha256=config_sha256,
            batch_rows=batch_rows,
            state=state,
            issues=issues,
        )
        expected_rows = (
            SMOKE_UTTERANCES if kind == "utterance" else state.rows_checked[kind]
        )
        issues.check(
            state.rows_checked[kind] == expected_rows and state.rows_checked[kind] > 0,
            f"{kind}_smoke_row_count_invalid",
        )
        issues.check(
            state.embedding_vectors_checked[kind] == state.rows_checked[kind],
            f"{kind}_embedding_vector_count_mismatch",
        )
        if kind != "utterance":
            issues.check(
                state.joins_checked[kind] == state.rows_checked[kind],
                f"{kind}_join_scan_incomplete",
            )
        observed_splits = set(state.split_counts[kind])
        issues.check(
            bool(observed_splits),
            f"{kind}_observed_output_splits_empty",
        )
        issues.check(
            all(bool(split) for split in observed_splits),
            f"{kind}_empty_split_present",
        )
        issues.check(
            observed_splits.issubset(ALLOWED_SPLITS),
            f"{kind}_forbidden_split_present",
        )
        issues.check(
            observed_splits.isdisjoint(EVALUATION_SPLITS),
            f"{kind}_evaluation_split_present",
        )
    issues.check(
        len(state.utterances) == SMOKE_UTTERANCES,
        "utterance_key_count_mismatch",
    )
    _validate_source_scan(
        contract=contract,
        progress=progress,
        policy=report_policy,
        state=state,
        issues=issues,
    )
    for kind, key in (
        ("utterance", "utterance_rows_written"),
        ("phone", "phone_rows_written"),
        ("word", "word_rows_written"),
    ):
        issues.check(
            progress.get(key) == state.rows_checked[kind],
            f"{kind}_progress_row_count_mismatch",
        )

    frame_stride, receptive_field = _frame_geometry(config_model)
    issues.check(
        report_configuration.get("sample_rate_hz") == 16_000
        and report_configuration.get("frame_stride_samples") == frame_stride
        and report_configuration.get("receptive_field_samples") == receptive_field,
        "report_frame_geometry_mismatch",
    )
    issues.check(
        progress.get("model_frame_count") == state.utterance_frame_count_total
        and progress.get("chunk_count") == state.utterance_chunk_count_total
        and _is_int(progress.get("padded_short_chunk_count"))
        and int(progress.get("padded_short_chunk_count", 0))
        <= state.utterance_chunk_count_total
        and _is_positive_number(progress.get("decoded_audio_seconds"))
        and math.isclose(
            float(cast(float | int, progress.get("decoded_audio_seconds"))),
            state.utterance_audio_duration_seconds_total,
            rel_tol=1e-9,
            abs_tol=1e-6,
        ),
        "report_frame_totals_mismatch",
    )
    issues.check(
        frame_audit.get("validated_chunk_count") == state.utterance_chunk_count_total
        and frame_audit.get("all_hidden_shapes_matched_pinned_geometry") is True
        and frame_audit.get("frame_stride_samples") == frame_stride
        and frame_audit.get("receptive_field_samples") == receptive_field
        and frame_audit.get("frame_center_mapping")
        == "half_open_interval_by_frame_center",
        "frame_mapping_audit_invalid",
    )
    if contract.name == "large":
        dimensions = report_configuration.get("embedding_dimensions")
        issues.check(
            dimensions
            == {
                "utterance_mean_std": contract.hidden_size * 2,
                "phone_mean": contract.hidden_size,
                "word_mean": contract.hidden_size,
            }
            and report_configuration.get("frame_geometry_source")
            == "pinned_local_wavlm_large_config",
            "large_embedding_dimension_report_mismatch",
        )

    resource_review = _validate_resource_estimate(
        config_gate=config_gate,
        report_resource=report_resource,
        report_runtime=report_runtime,
        shard_reports=shard_reports,
        state=state,
        contract=contract,
        issues=issues,
    )
    errors = issues.report()
    if errors:
        return failure_review(errors)

    observed_output_splits = {
        kind: [
            split
            for split in ALLOWED_SPLITS
            if state.split_counts[kind].get(split, 0) > 0
        ]
        for kind in KINDS
    }
    allowed_split_coverage_complete = all(
        set(splits) == set(ALLOWED_SPLITS) for splits in observed_output_splits.values()
    )
    source_contract: dict[str, Any] = {
        "model_id": contract.model_id,
        "model_revision": contract.revision,
        "preprocessor_config_sha256": contract.preprocessor_sha256,
        "do_normalize": contract.do_normalize,
        "allowed_splits": list(ALLOWED_SPLITS),
        "observed_output_splits": observed_output_splits,
        "allowed_split_coverage_complete": allowed_split_coverage_complete,
        "full_cache_validation_requires_all_allowed_splits": (
            FULL_CACHE_VALIDATION_REQUIRES_ALL_ALLOWED_SPLITS
        ),
        "metric_evaluation_performed": False,
        "evaluation_manifest_annotations_may_be_deserialized_for_filtering": True,
        "evaluation_audio_decoded": False,
        "evaluation_rows_sent_to_encoder": False,
        "evaluation_labels_used_for_training_or_metrics": False,
    }
    result: dict[str, Any] = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "status": "approved",
        "approved": True,
        "review_type": "automated_engineering_gate",
        "human_expert_review": False,
        "full_run_approved": True,
        "smoke_report_path": relative["report"],
        "smoke_report_sha256": report_sha256,
        "experiment_config_sha256": config_sha256,
        "builder_sha256": builder_sha256,
        "reviewer_source": reviewer_source,
        "source_contract": source_contract,
        "technical_checks": {
            "report_status_completed": True,
            "smoke_utterance_limit": SMOKE_UTTERANCES,
            "embedding_dimensions": {
                "utterance_mean_std": contract.hidden_size * 2,
                "phone_mean": contract.hidden_size,
                "word_mean": contract.hidden_size,
            },
            "all_reported_shard_sizes_and_sha256_match": True,
            "storage_dtype_float16": True,
            "pinned_preprocessor_config_validated": True,
            "all_embeddings_finite_and_nonzero": True,
            "prohibited_output_column_hits": [],
            "identifier_columns_are_lowercase_sha256": True,
            "frame_geometry_check_passed": True,
            "oom_count": 0,
        },
        "resource_review": resource_review,
        "validation_summary": {
            "variant": contract.name,
            "rows_checked": {kind: int(state.rows_checked[kind]) for kind in KINDS},
            "shards_checked": {
                kind: int(shard_reports[kind]["shards_checked"]) for kind in KINDS
            },
            "embedding_vectors_checked": {
                kind: int(state.embedding_vectors_checked[kind]) for kind in KINDS
            },
            "child_joins_checked": {
                kind: int(state.joins_checked[kind]) for kind in ("phone", "word")
            },
            "source_rows_scanned": int(progress["source_rows_scanned"]),
            "source_scan_complete": False,
        },
        "privacy": {
            "aggregate_only_output": True,
            "row_identifiers_exported": False,
            "embedding_values_exported": False,
            "raw_data_read": False,
            "source_modeling_manifest_read": False,
            "environment_files_read": False,
        },
        "reviewed_at_utc": _utc_now(),
        "limitations": [
            (
                "This is an automated engineering gate, not a model-quality, "
                "human-expert, or G0 alignment review."
            ),
            (
                "Approval covers the supplied 100-utterance smoke report and "
                "every shard bound by its hashes."
            ),
            (
                "A bounded, post-filter ordered smoke may observe only a "
                "nonempty subset of train/dev; observed coverage is recorded."
            ),
            (
                "Full-cache validation still requires every allowed split; a "
                "full Large run also requires the separately validated Base+ "
                "frozen-head completion gate."
            ),
        ],
    }
    if contract.builder_dependency_sha256 is not None:
        result["builder_dependency_sha256"] = contract.builder_dependency_sha256
    return result


__all__ = [
    "REVIEW_SCHEMA_VERSION",
    "SmokeReviewError",
    "SmokeVariantContract",
    "UnsafeSmokeReviewPath",
    "VARIANT_CONTRACTS",
    "failure_review",
    "review_wavlm_feature_cache_smoke",
    "safe_review_output_path",
]
