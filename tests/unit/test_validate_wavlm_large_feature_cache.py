from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

from voice_coach.reproducibility import wavlm_large_cache_validation as module


REPOSITORY_ROOT = Path(__file__).parents[2]
SCRIPT = REPOSITORY_ROOT / "scripts" / "validate_wavlm_large_feature_cache.py"
SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "validate_wavlm_large_feature_cache_test_module",
    SCRIPT,
)
assert SCRIPT_SPEC is not None and SCRIPT_SPEC.loader is not None
SCRIPT_MODULE = importlib.util.module_from_spec(SCRIPT_SPEC)
sys.modules[SCRIPT_SPEC.name] = SCRIPT_MODULE
SCRIPT_SPEC.loader.exec_module(SCRIPT_MODULE)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(root: Path, relative: Path | str, content: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _preprocessor() -> dict[str, Any]:
    return {
        "do_normalize": True,
        "feature_extractor_type": "Wav2Vec2FeatureExtractor",
        "feature_size": 1,
        "padding_side": "right",
        "padding_value": 0.0,
        "return_attention_mask": True,
        "sampling_rate": 16_000,
    }


def _generic_result() -> dict[str, Any]:
    split_counts = {
        "utterance": dict(module.INCLUDED_UTTERANCES_BY_SPLIT),
        "phone": {"train": 1_000_000, "dev": 1_233_039},
        "word": {"train": 200_000, "dev": 208_790},
    }
    return {
        "schema_version": module.GENERIC_VALIDATION_SCHEMA_VERSION,
        "status": "passed",
        "contract": {
            "full_run": True,
            "allowed_splits": list(module.ALLOWED_SPLITS),
            "expected_rows": dict(module.EXPECTED_ROWS),
            "hidden_size": module.HIDDEN_SIZE,
            "embedding_dimensions": dict(module.EXPECTED_DIMENSIONS),
            "storage_type": "float16",
        },
        "files": {
            "model": {
                "config_file_checked": True,
                "config_sha256": module.MODEL_CONFIG_SHA256,
                "checkpoint_match": True,
            },
            "cache": {
                kind: {
                    "rows_checked": rows,
                    "shards_checked": {"utterance": 17, "phone": 9, "word": 5}[kind],
                }
                for kind, rows in module.EXPECTED_ROWS.items()
            },
        },
        "scan": {
            "rows_checked": dict(module.EXPECTED_ROWS),
            "embedding_vectors_checked": dict(module.EXPECTED_ROWS),
            "embedding_values_checked": {
                kind: module.EXPECTED_ROWS[kind] * module.EXPECTED_DIMENSIONS[kind]
                for kind in module.EXPECTED_ROWS
            },
            "split_counts": split_counts,
            "forbidden_split_rows": {kind: 0 for kind in module.EXPECTED_ROWS},
            "nonfinite_embedding_values": {kind: 0 for kind in module.EXPECTED_ROWS},
            "all_zero_embedding_vectors": {kind: 0 for kind in module.EXPECTED_ROWS},
            "child_joins_checked": {
                "phone": module.EXPECTED_ROWS["phone"],
                "word": module.EXPECTED_ROWS["word"],
            },
            "utterance_join_entries": module.EXPECTED_ROWS["utterance"],
        },
        "memory": {
            "strategy": "one Parquet row group at a time",
            "full_embedding_matrix_materialized": False,
            "max_batch_rows": 2_048,
        },
        "errors": [],
    }


def _registry_entry(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _build_base_gate(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    pinned_files = {
        "config": _write(root, "synthetic/base/head-config.yaml", b"head config\n"),
        "trainer": _write(root, "synthetic/base/trainer.py", b"trainer source\n"),
        "head_model": _write(root, "synthetic/base/head.py", b"head source\n"),
        "labels": _write(root, "synthetic/base/labels.parquet", b"labels\n"),
        "cache_config": _write(
            root,
            "synthetic/base/cache-config.yaml",
            b"cache config\n",
        ),
        "encoder_config": _write(
            root,
            "synthetic/base/encoder-config.json",
            b"encoder config\n",
        ),
        "encoder_checkpoint": _write(
            root,
            "synthetic/base/encoder.bin",
            b"encoder checkpoint\n",
        ),
        "feature_report": _write(
            root,
            "synthetic/base/cache-report.json",
            b'{"status":"passed"}\n',
        ),
    }
    monkeypatch.setattr(
        module,
        "BASE_PLUS_HEAD_CONFIG_SHA256",
        _sha256(pinned_files["config"]),
    )
    monkeypatch.setattr(
        module,
        "BASE_PLUS_HEAD_TRAINER_SHA256",
        _sha256(pinned_files["trainer"]),
    )
    monkeypatch.setattr(
        module,
        "BASE_PLUS_HEAD_MODEL_SHA256",
        _sha256(pinned_files["head_model"]),
    )
    monkeypatch.setattr(
        module,
        "BASE_PLUS_EXPLICIT_LABEL_SHA256",
        _sha256(pinned_files["labels"]),
    )
    monkeypatch.setattr(
        module,
        "BASE_PLUS_CACHE_CONFIG_SHA256",
        _sha256(pinned_files["cache_config"]),
    )
    monkeypatch.setattr(
        module,
        "BASE_PLUS_MODEL_CONFIG_SHA256",
        _sha256(pinned_files["encoder_config"]),
    )
    monkeypatch.setattr(
        module,
        "BASE_PLUS_MODEL_CHECKPOINT_SHA256",
        _sha256(pinned_files["encoder_checkpoint"]),
    )

    shard_files = {
        kind: [
            _write(
                root,
                f"synthetic/base/{kind}-part-{index:05d}.parquet",
                f"{kind} shard {index}\n".encode(),
            )
            for index in range(module.EXPECTED_SHARDS[kind])
        ]
        for kind in module.EXPECTED_ROWS
    }
    head = root / module.BASE_PLUS_HEAD_REPORT_RELATIVE_PATH
    head.parent.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []
    registry_artifact_entries: dict[str, tuple[Path, str]] = {}
    for seed in module.REQUIRED_SEEDS:
        seed_files = {
            "checkpoint": _write(
                head.parent,
                f"seed_{seed}_checkpoint.bin",
                f"checkpoint {seed}\n".encode(),
            ),
            "phone_predictions": _write(
                head.parent,
                f"seed_{seed}_phone.json",
                f"phone {seed}\n".encode(),
            ),
            "utterance_predictions": _write(
                head.parent,
                f"seed_{seed}_utterance.json",
                f"utterance {seed}\n".encode(),
            ),
            "slice_metrics": _write(
                head.parent,
                f"seed_{seed}_slices.json",
                f"slices {seed}\n".encode(),
            ),
        }

        def artifact(value: Path) -> dict[str, str]:
            return {
                "path": value.relative_to(head.parent).as_posix(),
                "sha256": _sha256(value),
            }

        runs.append(
            {
                "seed": seed,
                "selection": {"dev_values_used_for_selection": False},
                "phone": {"predictions": artifact(seed_files["phone_predictions"])},
                "utterance": {
                    "predictions": artifact(seed_files["utterance_predictions"])
                },
                "artifacts": {
                    "checkpoint": artifact(seed_files["checkpoint"]),
                    "slice_metrics": artifact(seed_files["slice_metrics"]),
                },
            }
        )
        for suffix, value in seed_files.items():
            registry_name = (
                f"seed_{seed}_{suffix}"
                if suffix != "phone_predictions"
                else f"seed_{seed}_phone_predictions"
            )
            registry_artifact_entries[registry_name] = (value, _sha256(value))

    registry = (
        root
        / "artifacts"
        / "experiment_registry"
        / "synthetic-base-plus"
        / "metadata.json"
    )
    report = {
        "schema_version": module.BASE_PLUS_HEAD_REPORT_SCHEMA,
        "status": "completed",
        "experiment_id": module.BASE_PLUS_HEAD_EXPERIMENT_ID,
        "development_only": True,
        "encoder": {
            "variant": "base_plus",
            "id": module.BASE_PLUS_MODEL_ID,
            "revision": module.BASE_PLUS_MODEL_REVISION,
            "hidden_size": 768,
            "utterance_embedding_size": 1_536,
            "frozen": True,
            "source_config_sha256": module.BASE_PLUS_MODEL_CONFIG_SHA256,
            "source_checkpoint_sha256": module.BASE_PLUS_MODEL_CHECKPOINT_SHA256,
            "current_experiment_variant": "base_plus",
        },
        "sources": {
            "training_config_sha256": module.BASE_PLUS_HEAD_CONFIG_SHA256,
            "trainer_source_sha256": module.BASE_PLUS_HEAD_TRAINER_SHA256,
            "head_source_sha256": module.BASE_PLUS_HEAD_MODEL_SHA256,
            "explicit_utterance_labels_sha256": (
                module.BASE_PLUS_EXPLICIT_LABEL_SHA256
            ),
            "modeling_manifest_sha256": module.MODELING_MANIFEST_SHA256,
            "prompt_reservation_sha256": module.PROMPT_RESERVATION_SHA256,
            "cache_builder_config_sha256": module.BASE_PLUS_CACHE_CONFIG_SHA256,
            "cache_builder_sha256": module.GENERIC_BUILDER_SHA256,
            "encoder_config_sha256": module.BASE_PLUS_MODEL_CONFIG_SHA256,
            "encoder_checkpoint_sha256": module.BASE_PLUS_MODEL_CHECKPOINT_SHA256,
            "model_hashes_bound_by_validated_config_and_cache_report": True,
            "feature_cache_report_sha256": _sha256(pinned_files["feature_report"]),
        },
        "summary": {
            "seeds_completed": list(module.REQUIRED_SEEDS),
            "results_are_development_only": True,
            "locked_test_remains_unread": True,
            "stack_remains_unread": True,
            "calibration_remains_unread": True,
            "dev_inference_passes_per_seed": 1,
        },
        "leakage_controls": {
            "accepted_splits": list(module.ALLOWED_SPLITS),
            "stack_accessed": False,
            "calibration_accessed": False,
            "locked_test_accessed": False,
            "dev_used_for_selection": False,
            "train_inner_validation_used": True,
        },
        "cache_contract": {
            "full_cache_validated": True,
            "cache_report_status": "completed",
            "source_scan_complete": True,
            "smoke_cache_rejected": True,
            "every_shard_hash_validated": True,
            "every_shard_schema_validated": True,
            "every_shard_split_policy_validated": True,
            "every_shard_privacy_metadata_validated": True,
            "access_scope": "restricted_internal",
            "shareable": False,
            "speaker_overlap_train_dev": 0,
            "rows": dict(module.EXPECTED_ROWS),
            "split_rows": {
                kind: {
                    "train": rows - 1,
                    "dev": 1,
                }
                for kind, rows in module.EXPECTED_ROWS.items()
            },
            "shard_fingerprints": {
                kind: [
                    {
                        "name": f"part-{index:05d}.parquet",
                        "row_count": (
                            module.EXPECTED_ROWS[kind] // module.EXPECTED_SHARDS[kind]
                            + (
                                1
                                if index
                                < module.EXPECTED_ROWS[kind]
                                % module.EXPECTED_SHARDS[kind]
                                else 0
                            )
                        ),
                        "sha256": _sha256(path),
                    }
                    for index, path in enumerate(shard_files[kind])
                ]
                for kind in module.EXPECTED_ROWS
            },
        },
        "runs": runs,
        "experiment_registry": {
            "integrated": True,
            "t03_complete": True,
            "implementation_source_hashes_match_registry_inputs": True,
            "metrics_recorded": True,
            "artifacts_recorded": True,
            "registered_artifact_count": 13,
            "run_id": "synthetic-base-plus",
            "metadata_path": registry.relative_to(root).as_posix(),
        },
    }
    head.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    registry_artifacts = [
        {
            "name": name,
            **_registry_entry(root, path),
        }
        for name, (path, _hash) in registry_artifact_entries.items()
    ]
    registry_artifacts.append(
        {
            "name": "final_report",
            **_registry_entry(root, head),
        }
    )
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps(
            {
                "status": "completed",
                "failure": None,
                "run_id": "synthetic-base-plus",
                "seeds": {
                    f"experiment_seed_{seed}": seed for seed in module.REQUIRED_SEEDS
                },
                "inputs": {
                    "config": [_registry_entry(root, pinned_files["config"])],
                    "data": [
                        _registry_entry(root, pinned_files["feature_report"]),
                        _registry_entry(root, pinned_files["labels"]),
                        *[
                            _registry_entry(root, path)
                            for kind in module.EXPECTED_ROWS
                            for path in shard_files[kind]
                        ],
                    ],
                    "model": [
                        _registry_entry(root, pinned_files["trainer"]),
                        _registry_entry(root, pinned_files["head_model"]),
                    ],
                    "checkpoint": [],
                },
                "artifacts": registry_artifacts,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return head, registry


def _build_smoke_gate(
    root: Path,
    *,
    config: Path,
    builder: Path,
    dependency: Path,
    reviewer_cli: Path,
    reviewer_core: Path,
) -> tuple[Path, Path]:
    smoke_rows = dict(module.SMOKE_OBSERVED_ROWS)
    smoke_bytes = {"utterance": 426_002, "phone": 3_541_941, "word": 677_368}
    bytes_per_row = {kind: smoke_bytes[kind] / smoke_rows[kind] for kind in smoke_rows}
    smoke_report = root / module.SMOKE_REPORT_RELATIVE_PATH
    smoke_report.parent.mkdir(parents=True, exist_ok=True)
    smoke_report.write_text(
        json.dumps(
            {
                "schema_version": module.CACHE_REPORT_SCHEMA_VERSION,
                "status": "completed",
                "source": {
                    "experiment_config_sha256": _sha256(config),
                    "builder_sha256": _sha256(builder),
                    "builder_dependency_path": (
                        module.GENERIC_BUILDER_RELATIVE_PATH.as_posix()
                    ),
                    "builder_dependency_sha256": _sha256(dependency),
                    "preprocessor_config_sha256": module.MODEL_PREPROCESSOR_SHA256,
                },
                "model": {
                    "id": module.MODEL_ID,
                    "revision": module.MODEL_REVISION,
                    "config_sha256": module.MODEL_CONFIG_SHA256,
                    "checkpoint_sha256": module.MODEL_CHECKPOINT_SHA256,
                    "preprocessor_config_sha256": (module.MODEL_PREPROCESSOR_SHA256),
                    **module.REPORT_MODEL_ARCHITECTURE,
                    "frame_stride_samples": 320,
                    "receptive_field_samples": 400,
                    "parameter_count": 316_000_000,
                    "frozen": True,
                    "local_files_only": True,
                    "do_normalize": True,
                    "preprocessor_contract": {
                        "sampling_rate_hz": 16_000,
                        "do_normalize": True,
                        "return_attention_mask": True,
                        "source": "official_pinned_revision",
                        "source_url": module.PINNED_PREPROCESSOR_URL,
                        "config_sha256": module.MODEL_PREPROCESSOR_SHA256,
                        "local_preprocessor_config_present": True,
                        "local_preprocessor_config_validated": True,
                        "auto_feature_extractor_used": False,
                    },
                },
                "runtime": {
                    "cuda_autocast_enabled": True,
                    "cuda_autocast_precision": "bfloat16",
                    "model_output_dtype_policy": ("record_observed_dtype_at_runtime"),
                    "storage_precision": "float16",
                    "observed_model_output_dtypes": {"float32": 123},
                    "oom_count": 0,
                    "peak_process_rss_bytes": 3_000_000,
                },
                "policy": {
                    "allowed_splits": list(module.ALLOWED_SPLITS),
                    "evaluation_splits": list(module.EVALUATION_SPLITS),
                    "metric_evaluation_performed": False,
                    "oracle_alignment": True,
                    "source_manifest_columns_deserialized_before_split_filter": (
                        list(module.SOURCE_COLUMNS)
                    ),
                    "evaluation_manifest_annotations_may_be_deserialized_for_filtering": True,
                    "evaluation_audio_decoded": False,
                    "evaluation_rows_sent_to_encoder": False,
                    "evaluation_labels_used_for_training_or_metrics": False,
                },
                "configuration": {"limit": 100, "full_run": False},
                "progress": {
                    "included_rows_by_split": {"train": 100},
                    "chunk_count": 123,
                },
                "outputs": {
                    kind: {
                        "row_count": smoke_rows[kind],
                        "byte_count": smoke_bytes[kind],
                    }
                    for kind in smoke_rows
                },
                "resource_estimate": {
                    "method": (
                        "smoke compressed bytes per row scaled to audited full rows"
                    ),
                    "reference_rows": dict(module.EXPECTED_ROWS),
                    "observed_compressed_bytes_per_row": bytes_per_row,
                    "projected_compressed_bytes_by_kind": dict(
                        module.SMOKE_PROJECTED_BYTES_BY_KIND
                    ),
                    "projected_compressed_bytes_total": (
                        module.SMOKE_PROJECTED_BYTES_TOTAL
                    ),
                    "fixed_float16_embedding_bytes_by_kind": dict(
                        module.FIXED_FLOAT16_BYTES
                    ),
                    "fixed_float16_embedding_bytes_total": (
                        module.FIXED_FLOAT16_BYTES_TOTAL
                    ),
                    "fixed_embedding_estimate_excludes_metadata_and_parquet_overhead": True,
                    "safety_factor": module.RESOURCE_SAFETY_FACTOR,
                    "estimated_full_cache_upper_bytes": (
                        module.SMOKE_ESTIMATED_UPPER_BYTES
                    ),
                    "estimate_is_capacity_planning_not_measured_full_output": True,
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    smoke_review = root / module.SMOKE_REVIEW_RELATIVE_PATH
    smoke_review.parent.mkdir(parents=True, exist_ok=True)
    smoke_review.write_text(
        json.dumps(
            {
                "schema_version": module.SMOKE_REVIEW_SCHEMA_VERSION,
                "status": "approved",
                "approved": True,
                "full_run_approved": True,
                "review_type": "automated_engineering_gate",
                "human_expert_review": False,
                "smoke_report_path": module.SMOKE_REPORT_RELATIVE_PATH.as_posix(),
                "smoke_report_sha256": _sha256(smoke_report),
                "experiment_config_sha256": _sha256(config),
                "builder_sha256": _sha256(builder),
                "builder_dependency_sha256": _sha256(dependency),
                "reviewer_source": {
                    "cli_path": module.REVIEWER_CLI_RELATIVE_PATH.as_posix(),
                    "cli_sha256": _sha256(reviewer_cli),
                    "core_path": module.REVIEWER_CORE_RELATIVE_PATH.as_posix(),
                    "core_sha256": _sha256(reviewer_core),
                },
                "source_contract": {
                    "model_id": module.MODEL_ID,
                    "model_revision": module.MODEL_REVISION,
                    "preprocessor_config_sha256": (module.MODEL_PREPROCESSOR_SHA256),
                    "do_normalize": True,
                    "allowed_splits": list(module.ALLOWED_SPLITS),
                    "observed_output_splits": {
                        kind: ["train"] for kind in module.EXPECTED_ROWS
                    },
                    "allowed_split_coverage_complete": False,
                    "full_cache_validation_requires_all_allowed_splits": True,
                    "metric_evaluation_performed": False,
                    "evaluation_manifest_annotations_may_be_deserialized_for_filtering": True,
                    "evaluation_audio_decoded": False,
                    "evaluation_rows_sent_to_encoder": False,
                    "evaluation_labels_used_for_training_or_metrics": False,
                },
                "technical_checks": {
                    "report_status_completed": True,
                    "smoke_utterance_limit": 100,
                    "embedding_dimensions": {
                        "utterance_mean_std": module.EXPECTED_DIMENSIONS["utterance"],
                        "phone_mean": module.EXPECTED_DIMENSIONS["phone"],
                        "word_mean": module.EXPECTED_DIMENSIONS["word"],
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
                "resource_review": {
                    "approved": True,
                    "peak_rss_bytes": 3_000_000,
                    "estimated_full_cache_upper_bytes": (
                        module.SMOKE_ESTIMATED_UPPER_BYTES
                    ),
                    "max_row_group_rows": 100,
                    "safety_factor": module.RESOURCE_SAFETY_FACTOR,
                    "projected_compressed_bytes_total": (
                        module.SMOKE_PROJECTED_BYTES_TOTAL
                    ),
                    "observed_cache_bytes": sum(smoke_bytes.values()),
                },
                "validation_summary": {
                    "variant": "large",
                    "rows_checked": dict(smoke_rows),
                    "embedding_vectors_checked": dict(smoke_rows),
                    "source_rows_scanned": 100,
                    "source_scan_complete": False,
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return smoke_report, smoke_review


@dataclass
class Fixture:
    root: Path
    cache: Path
    report: Path
    config: Path
    builder: Path
    dependency: Path
    snapshot: Path
    blobs: Path
    model_config: Path
    checkpoint: Path
    preprocessor: Path
    head: Path
    registry: Path
    smoke_report: Path
    smoke_review: Path
    reviewer_cli: Path
    reviewer_core: Path
    output: Path
    generic: dict[str, Any]


def _fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Fixture:
    cache = tmp_path / module.CANONICAL_CACHE_RELATIVE_PATH
    cache.mkdir(parents=True)
    builder = _write(
        tmp_path,
        module.LARGE_BUILDER_RELATIVE_PATH,
        b'raise RuntimeError("validator must not execute the builder")\n',
    )
    dependency = _write(
        tmp_path,
        module.GENERIC_BUILDER_RELATIVE_PATH,
        b"# synthetic immutable generic builder\n",
    )
    reviewer_cli = _write(
        tmp_path,
        module.REVIEWER_CLI_RELATIVE_PATH,
        b"# synthetic reviewer CLI\n",
    )
    reviewer_core = _write(
        tmp_path,
        module.REVIEWER_CORE_RELATIVE_PATH,
        b"# synthetic reviewer core\n",
    )
    for relative in (
        module.VALIDATOR_CLI_RELATIVE_PATH,
        module.VALIDATOR_CORE_RELATIVE_PATH,
        module.GENERIC_VALIDATOR_CLI_RELATIVE_PATH,
        module.GENERIC_VALIDATOR_CORE_RELATIVE_PATH,
    ):
        _write(tmp_path, relative, f"# synthetic {relative.name}\n".encode())
    monkeypatch.setattr(
        module,
        "GENERIC_VALIDATOR_CLI_SHA256",
        _sha256(tmp_path / module.GENERIC_VALIDATOR_CLI_RELATIVE_PATH),
    )
    monkeypatch.setattr(
        module,
        "GENERIC_VALIDATOR_CORE_SHA256",
        _sha256(tmp_path / module.GENERIC_VALIDATOR_CORE_RELATIVE_PATH),
    )

    model_cache = module.MODEL_CACHE_RELATIVE_PATH
    snapshot = (
        tmp_path
        / model_cache
        / module.MODEL_REPOSITORY_NAME
        / "snapshots"
        / module.MODEL_REVISION
    )
    snapshot.mkdir(parents=True)
    blobs = tmp_path / model_cache / module.MODEL_REPOSITORY_NAME / "blobs"
    blobs.mkdir()
    model_config = snapshot / "config.json"
    checkpoint = snapshot / "pytorch_model.bin"
    preprocessor = snapshot / "preprocessor_config.json"
    model_config.write_text(
        json.dumps(module.MODEL_ARCHITECTURE, sort_keys=True),
        encoding="utf-8",
    )
    checkpoint.write_bytes(b"synthetic Large checkpoint\n")
    preprocessor.write_text(
        json.dumps(_preprocessor(), sort_keys=True),
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "LARGE_BUILDER_SHA256", _sha256(builder))
    monkeypatch.setattr(module, "GENERIC_BUILDER_SHA256", _sha256(dependency))
    monkeypatch.setattr(module, "REVIEWER_CLI_SHA256", _sha256(reviewer_cli))
    monkeypatch.setattr(module, "REVIEWER_CORE_SHA256", _sha256(reviewer_core))
    monkeypatch.setattr(module, "MODEL_CONFIG_SHA256", _sha256(model_config))
    monkeypatch.setattr(module, "MODEL_CHECKPOINT_SHA256", _sha256(checkpoint))
    monkeypatch.setattr(module, "MODEL_PREPROCESSOR_SHA256", _sha256(preprocessor))

    config_value = {
        "experiment": {
            "id": "aihub71490_wavlm_large_frozen_v1",
            "stage": "feature_cache",
            "development_only": True,
        },
        "model": {
            "id": module.MODEL_ID,
            "revision": module.MODEL_REVISION,
            "cache_dir": model_cache.as_posix(),
            "local_files_only": True,
            "frozen": True,
            "config_sha256": module.MODEL_CONFIG_SHA256,
            "checkpoint_sha256": module.MODEL_CHECKPOINT_SHA256,
            "preprocessor_config_sha256": module.MODEL_PREPROCESSOR_SHA256,
            "hidden_size": module.HIDDEN_SIZE,
            "do_normalize": True,
            "return_attention_mask": True,
            **module.CONFIG_MODEL_ARCHITECTURE,
        },
        "data": {
            "modeling_manifest_sha256": module.MODELING_MANIFEST_SHA256,
            "prompt_challenge_reservation": "manifests/prompt.json",
            "allowed_splits": list(module.ALLOWED_SPLITS),
            "forbidden_metric_splits": list(module.EVALUATION_SPLITS),
        },
        "features": {
            "cuda_autocast_precision": "bfloat16",
            "observed_model_output_dtype": "record_at_runtime",
            "storage_precision": "float16",
        },
        "output": {
            "root": module.CANONICAL_CACHE_RELATIVE_PATH.as_posix(),
            "report": module.CANONICAL_REPORT_RELATIVE_PATH.as_posix(),
        },
        "full_run_gate": {
            "resource_estimate_reference_rows": dict(module.EXPECTED_ROWS),
            "resource_estimate_safety_factor": module.RESOURCE_SAFETY_FACTOR,
        },
    }
    config = tmp_path / module.LARGE_CONFIG_RELATIVE_PATH
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        yaml.safe_dump(config_value, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "LARGE_CONFIG_SHA256", _sha256(config))

    head, registry = _build_base_gate(tmp_path, monkeypatch)
    smoke_report, smoke_review = _build_smoke_gate(
        tmp_path,
        config=config,
        builder=builder,
        dependency=dependency,
        reviewer_cli=reviewer_cli,
        reviewer_core=reviewer_core,
    )
    gate = {
        "base_plus_head_report_sha256": _sha256(head),
        "base_plus_head_registry_metadata_sha256": _sha256(registry),
        "smoke_review_sha256": _sha256(smoke_review),
        "approved_smoke_report_sha256": _sha256(smoke_report),
        "approved_config_sha256": _sha256(config),
        "approved_builder_sha256": _sha256(builder),
        "approved_builder_dependency_sha256": _sha256(dependency),
        "reviewer_cli_sha256": _sha256(reviewer_cli),
        "reviewer_core_sha256": _sha256(reviewer_core),
    }
    output_bytes = {
        "utterance": 70_000_000,
        "phone": 900_000_000,
        "word": 180_000_000,
    }
    bytes_per_row = {
        kind: output_bytes[kind] / module.EXPECTED_ROWS[kind]
        for kind in module.EXPECTED_ROWS
    }
    projected = {
        kind: int(
            __import__("math").ceil(bytes_per_row[kind] * module.EXPECTED_ROWS[kind])
        )
        for kind in module.EXPECTED_ROWS
    }
    projected_total = sum(projected.values())
    report_value = {
        "schema_version": module.CACHE_REPORT_SCHEMA_VERSION,
        "status": "completed",
        "source": {
            "modeling_manifest_sha256": module.MODELING_MANIFEST_SHA256,
            "prompt_reservation_sha256": module.PROMPT_RESERVATION_SHA256,
            "experiment_config_sha256": _sha256(config),
            "builder_sha256": _sha256(builder),
            "builder_dependency_path": module.GENERIC_BUILDER_RELATIVE_PATH.as_posix(),
            "builder_dependency_sha256": _sha256(dependency),
            "preprocessor_config_sha256": _sha256(preprocessor),
        },
        "model": {
            "id": module.MODEL_ID,
            "revision": module.MODEL_REVISION,
            "config_sha256": _sha256(model_config),
            "checkpoint_sha256": _sha256(checkpoint),
            "preprocessor_config_sha256": _sha256(preprocessor),
            **module.REPORT_MODEL_ARCHITECTURE,
            "frame_stride_samples": 320,
            "receptive_field_samples": 400,
            "parameter_count": 316_000_000,
            "frozen": True,
            "local_files_only": True,
            "do_normalize": True,
            "preprocessor_contract": {
                "sampling_rate_hz": 16_000,
                "do_normalize": True,
                "return_attention_mask": True,
                "source": "official_pinned_revision",
                "source_url": module.PINNED_PREPROCESSOR_URL,
                "config_sha256": _sha256(preprocessor),
                "local_preprocessor_config_present": True,
                "local_preprocessor_config_validated": True,
                "auto_feature_extractor_used": False,
            },
        },
        "runtime": {
            "storage_precision": "float16",
            "cuda_autocast_enabled": True,
            "cuda_autocast_precision": "bfloat16",
            "model_output_dtype_policy": "record_observed_dtype_at_runtime",
            "observed_model_output_dtypes": {"float32": 200_000},
            "oom_count": 0,
            "peak_allocated_bytes": 1_000_000,
            "peak_reserved_bytes": 2_000_000,
            "peak_process_rss_bytes": 3_000_000,
        },
        "policy": {
            "allowed_splits": list(module.ALLOWED_SPLITS),
            "evaluation_splits": list(module.EVALUATION_SPLITS),
            "evaluation_split_metric_access": "forbidden",
            "holistic_score_columns_read": [],
            "metric_evaluation_performed": False,
            "source_manifest_columns_deserialized_before_split_filter": list(
                module.SOURCE_COLUMNS
            ),
            "evaluation_manifest_annotations_may_be_deserialized_for_filtering": True,
            "evaluation_audio_decoded": False,
            "evaluation_rows_sent_to_encoder": False,
            "evaluation_labels_used_for_training_or_metrics": False,
        },
        "configuration": {
            "limit": None,
            "full_run": True,
            "full_run_gate": gate,
            "embedding_dimensions": {
                "utterance_mean_std": module.EXPECTED_DIMENSIONS["utterance"],
                "phone_mean": module.EXPECTED_DIMENSIONS["phone"],
                "word_mean": module.EXPECTED_DIMENSIONS["word"],
            },
            "frame_geometry_source": "pinned_local_wavlm_large_config",
        },
        "progress": {
            "source_rows_scanned": module.SOURCE_ROWS,
            "source_scan_complete": True,
            "source_rows_by_split": dict(module.SOURCE_ROWS_BY_SPLIT),
            "included_rows_by_split": dict(module.INCLUDED_UTTERANCES_BY_SPLIT),
            "exclusions": dict(module.EXPECTED_EXCLUSIONS),
            "utterance_rows_written": module.EXPECTED_ROWS["utterance"],
            "phone_rows_written": module.EXPECTED_ROWS["phone"],
            "word_rows_written": module.EXPECTED_ROWS["word"],
            "chunk_count": 200_000,
        },
        "frame_mapping_audit": {
            "validated_chunk_count": 200_000,
            "all_hidden_shapes_matched_pinned_geometry": True,
            "frame_stride_samples": 320,
            "receptive_field_samples": 400,
            "frame_center_mapping": "half_open_interval_by_frame_center",
        },
        "outputs": {
            kind: {
                "row_count": module.EXPECTED_ROWS[kind],
                "byte_count": output_bytes[kind],
            }
            for kind in module.EXPECTED_ROWS
        },
        "resource_estimate": {
            "method": "smoke compressed bytes per row scaled to audited full rows",
            "reference_rows": dict(module.EXPECTED_ROWS),
            "observed_compressed_bytes_per_row": bytes_per_row,
            "projected_compressed_bytes_by_kind": projected,
            "projected_compressed_bytes_total": projected_total,
            "fixed_float16_embedding_bytes_by_kind": dict(module.FIXED_FLOAT16_BYTES),
            "fixed_float16_embedding_bytes_total": (module.FIXED_FLOAT16_BYTES_TOTAL),
            "fixed_embedding_estimate_excludes_metadata_and_parquet_overhead": True,
            "safety_factor": module.RESOURCE_SAFETY_FACTOR,
            "estimated_full_cache_upper_bytes": int(
                __import__("math").ceil(projected_total * module.RESOURCE_SAFETY_FACTOR)
            ),
            "estimate_is_capacity_planning_not_measured_full_output": True,
        },
    }
    report = tmp_path / module.CANONICAL_REPORT_RELATIVE_PATH
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(report_value), encoding="utf-8")

    generic = _generic_result()
    monkeypatch.setattr(
        module,
        "validate_wavlm_feature_cache",
        lambda **kwargs: deepcopy(generic),
    )

    return Fixture(
        root=tmp_path,
        cache=cache,
        report=report,
        config=config,
        builder=builder,
        dependency=dependency,
        snapshot=snapshot,
        blobs=blobs,
        model_config=model_config,
        checkpoint=checkpoint,
        preprocessor=preprocessor,
        head=head,
        registry=registry,
        smoke_report=smoke_report,
        smoke_review=smoke_review,
        reviewer_cli=reviewer_cli,
        reviewer_core=reviewer_core,
        output=tmp_path / module.RECOMMENDED_OUTPUT_RELATIVE_PATH,
        generic=generic,
    )


def _validate(fixture: Fixture) -> dict[str, Any]:
    return module.validate_wavlm_large_feature_cache(
        cache_root=fixture.cache,
        report_path=fixture.report,
        config_path=fixture.config,
        builder_path=fixture.builder,
        base_plus_head_report=fixture.head,
        smoke_review=fixture.smoke_review,
        workspace_root=fixture.root,
        batch_rows=17,
    )


def _convert_snapshot_files_to_blob_symlinks(fixture: Fixture) -> None:
    for role, snapshot_file in (
        ("config", fixture.model_config),
        ("checkpoint", fixture.checkpoint),
        ("preprocessor", fixture.preprocessor),
    ):
        blob = fixture.blobs / f"{role}-{_sha256(snapshot_file)}"
        blob.write_bytes(snapshot_file.read_bytes())
        snapshot_file.unlink()
        snapshot_file.symlink_to(Path("../../blobs") / blob.name)


def _mutate_report(
    fixture: Fixture,
    path: tuple[str, ...],
    value: object,
) -> None:
    report = json.loads(fixture.report.read_text(encoding="utf-8"))
    cursor = report
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    fixture.report.write_text(json.dumps(report), encoding="utf-8")


def _refresh_reported_gate_hashes(fixture: Fixture) -> None:
    report = json.loads(fixture.report.read_text(encoding="utf-8"))
    gate = report["configuration"]["full_run_gate"]
    gate.update(
        {
            "base_plus_head_report_sha256": _sha256(fixture.head),
            "base_plus_head_registry_metadata_sha256": _sha256(fixture.registry),
            "smoke_review_sha256": _sha256(fixture.smoke_review),
            "approved_smoke_report_sha256": _sha256(fixture.smoke_report),
            "approved_config_sha256": _sha256(fixture.config),
            "approved_builder_sha256": _sha256(fixture.builder),
            "approved_builder_dependency_sha256": _sha256(fixture.dependency),
            "reviewer_cli_sha256": _sha256(fixture.reviewer_cli),
            "reviewer_core_sha256": _sha256(fixture.reviewer_core),
        }
    )
    fixture.report.write_text(json.dumps(report), encoding="utf-8")


def _codes(result: dict[str, Any]) -> set[str]:
    return {str(value["code"]) for value in result["errors"]}


def test_repository_dependency_source_pins_are_current() -> None:
    assert (
        _sha256(REPOSITORY_ROOT / module.LARGE_CONFIG_RELATIVE_PATH)
        == module.LARGE_CONFIG_SHA256
    )
    assert (
        _sha256(REPOSITORY_ROOT / module.LARGE_BUILDER_RELATIVE_PATH)
        == module.LARGE_BUILDER_SHA256
    )
    assert (
        _sha256(REPOSITORY_ROOT / module.GENERIC_BUILDER_RELATIVE_PATH)
        == module.GENERIC_BUILDER_SHA256
    )
    assert (
        _sha256(REPOSITORY_ROOT / module.REVIEWER_CLI_RELATIVE_PATH)
        == module.REVIEWER_CLI_SHA256
    )
    assert (
        _sha256(REPOSITORY_ROOT / module.REVIEWER_CORE_RELATIVE_PATH)
        == module.REVIEWER_CORE_SHA256
    )
    assert (
        _sha256(REPOSITORY_ROOT / module.GENERIC_VALIDATOR_CLI_RELATIVE_PATH)
        == module.GENERIC_VALIDATOR_CLI_SHA256
    )
    assert (
        _sha256(REPOSITORY_ROOT / module.GENERIC_VALIDATOR_CORE_RELATIVE_PATH)
        == module.GENERIC_VALIDATOR_CORE_SHA256
    )


def test_large_wrapper_passes_and_records_complete_aggregate_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    calls: list[dict[str, Any]] = []

    def generic_validation(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return deepcopy(fixture.generic)

    monkeypatch.setattr(module, "validate_wavlm_feature_cache", generic_validation)

    result = _validate(fixture)

    assert result["status"] == "passed"
    assert result["errors"] == []
    assert result["contract"]["fixed_float16_embedding_bytes_total"] == 6_106_118_144
    assert result["scan"]["rows_checked"] == module.EXPECTED_ROWS
    assert result["scan"]["embedding_values_checked"] == {
        "utterance": 347_826_176,
        "phone": 2_286_631_936,
        "word": 418_600_960,
    }
    assert result["source_scan"]["source_rows_scanned"] == 322_380
    assert result["source_scan"]["source_rows_by_split"] == (
        module.SOURCE_ROWS_BY_SPLIT
    )
    assert result["source_scan"]["included_rows_by_split"] == (
        module.INCLUDED_UTTERANCES_BY_SPLIT
    )
    assert result["source_scan"]["exclusions"] == module.EXPECTED_EXCLUSIONS
    assert result["full_run_gate"]["reviewer_cli_sha256"] == _sha256(
        fixture.reviewer_cli
    )
    assert (
        result["resource_estimate"]["meaningful_pre_run_capacity_gate"][
            "estimated_full_cache_upper_bytes"
        ]
        == 7_481_336_023
    )
    assert (
        result["resource_estimate"]["meaningful_pre_run_capacity_gate"][
            "actual_within_pre_run_upper"
        ]
        is True
    )
    assert (
        result["resource_estimate"]["full_report_legacy_self_projection"]["semantics"]
        == "measured_full_output_self_projection_non_independent_capacity_check"
    )
    checkpoint_source = result["inputs"]["source_hashes"]["model_checkpoint"]
    assert checkpoint_source["sha256"] == module.MODEL_CHECKPOINT_SHA256
    assert checkpoint_source["hash_validated_by_generic"] is True
    assert result["components"]["full_run_gate_validation"] == {
        "passed": True,
        "builder_code_executed": False,
        "base_plus_seeds_checked": 3,
        "base_plus_cache_shards_checked": 31,
        "base_plus_registry_artifacts_checked": 13,
        "large_v2_smoke_rows_checked": module.SMOKE_OBSERVED_ROWS,
    }
    assert result["privacy"]["aggregate_only_output"] is True
    assert len(calls) == 1
    assert calls[0]["expected_rows"] == module.EXPECTED_ROWS
    assert calls[0]["batch_rows"] == 17
    serialized = json.dumps(result)
    assert "audio_path" not in serialized
    assert ".env" not in serialized


def test_actual_huggingface_blob_symlink_layout_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    _convert_snapshot_files_to_blob_symlinks(fixture)

    result = _validate(fixture)

    assert result["status"] == "passed"
    hashes = result["inputs"]["source_hashes"]
    for role in ("model_config", "model_checkpoint", "preprocessor"):
        assert hashes[role]["leaf_symlink"] is True
        assert hashes[role]["resolved_path"].startswith(
            (
                module.MODEL_CACHE_RELATIVE_PATH
                / module.MODEL_REPOSITORY_NAME
                / "blobs"
            ).as_posix()
        )
        assert hashes[role]["sha256"]


@pytest.mark.parametrize("mode", ["escape", "dangling", "wrong_blobs"])
def test_huggingface_leaf_symlink_escape_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    original = fixture.model_config.read_bytes()
    fixture.model_config.unlink()
    if mode == "escape":
        target = _write(tmp_path, "synthetic/outside-config.json", original)
        fixture.model_config.symlink_to(
            Path(os.path.relpath(target, fixture.model_config.parent))
        )
    elif mode == "wrong_blobs":
        target = _write(
            tmp_path,
            "data/cache/huggingface/hub/models--other/blobs/config",
            original,
        )
        fixture.model_config.symlink_to(
            Path(os.path.relpath(target, fixture.model_config.parent))
        )
    else:
        fixture.model_config.symlink_to("../../blobs/missing-config")

    result = _validate(fixture)

    assert result["status"] == "failed"
    assert "large_model_contract_invalid" in _codes(result)


def test_huggingface_intermediate_directory_symlink_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    snapshots = fixture.snapshot.parent
    relocated = snapshots.with_name("snapshots-real")
    snapshots.rename(relocated)
    snapshots.symlink_to(relocated.name, target_is_directory=True)

    result = _validate(fixture)

    assert result["status"] == "failed"
    assert "large_model_contract_invalid" in _codes(result)
    assert "large_preprocessor_contract_invalid" in _codes(result)


def test_huggingface_blob_intermediate_symlink_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    original = fixture.model_config.read_bytes()
    fixture.model_config.unlink()
    real = fixture.blobs / "real"
    real.mkdir()
    (real / "config").write_bytes(original)
    (fixture.blobs / "alias").symlink_to("real", target_is_directory=True)
    fixture.model_config.symlink_to("../../blobs/alias/config")

    result = _validate(fixture)

    assert result["status"] == "failed"
    assert "large_model_contract_invalid" in _codes(result)


def test_pinned_hf_resolver_rejects_wrong_cache_or_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    config = yaml.safe_load(fixture.config.read_text(encoding="utf-8"))
    config["model"]["cache_dir"] = "model-cache"
    with pytest.raises(module.LargeCacheValidationError):
        module._resolve_pinned_hf_snapshot_file(
            root=fixture.root,
            config=config,
            filename="config.json",
            expected_sha256=module.MODEL_CONFIG_SHA256,
            role="Large model config",
        )
    config["model"]["cache_dir"] = module.MODEL_CACHE_RELATIVE_PATH.as_posix()
    config["model"]["revision"] = "wrong-revision"
    with pytest.raises(module.LargeCacheValidationError):
        module._resolve_pinned_hf_snapshot_file(
            root=fixture.root,
            config=config,
            filename="config.json",
            expected_sha256=module.MODEL_CONFIG_SHA256,
            role="Large model config",
        )


def test_builder_realistic_report_omits_non_emitted_architecture_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    report = json.loads(fixture.report.read_text(encoding="utf-8"))
    for key in ("feat_extract_norm", "feat_extract_activation", "hidden_act"):
        assert key not in report["model"]

    result = _validate(fixture)

    assert result["status"] == "passed"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("feat_extract_norm", "group"),
        ("feat_extract_activation", "relu"),
        ("hidden_act", "relu"),
    ],
)
def test_non_emitted_architecture_fields_remain_pinned_in_local_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: str,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    document = json.loads(fixture.model_config.read_text(encoding="utf-8"))
    document[key] = value
    fixture.model_config.write_text(json.dumps(document), encoding="utf-8")

    result = _validate(fixture)

    assert result["status"] == "failed"
    assert "large_model_contract_invalid" in _codes(result)


def test_emitted_report_architecture_subset_is_not_relaxed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    _mutate_report(fixture, ("model", "num_hidden_layers"), 23)

    result = _validate(fixture)

    assert result["status"] == "failed"
    assert "large_model_contract_invalid" in _codes(result)


@pytest.mark.parametrize(
    ("path", "value", "expected_code"),
    [
        (
            ("model", "preprocessor_contract", "do_normalize"),
            False,
            "large_preprocessor_contract_invalid",
        ),
        (
            ("model", "preprocessor_contract", "source_url"),
            "https://example.invalid/wrong",
            "large_preprocessor_contract_invalid",
        ),
        (
            ("source", "builder_dependency_sha256"),
            "0" * 64,
            "large_builder_dependency_invalid",
        ),
        (
            ("model", "id"),
            "example/wrong",
            "large_model_contract_invalid",
        ),
        (
            ("model", "checkpoint_sha256"),
            "0" * 64,
            "large_model_contract_invalid",
        ),
        (
            ("configuration", "embedding_dimensions", "phone_mean"),
            768,
            "large_model_contract_invalid",
        ),
        (
            ("runtime", "cuda_autocast_precision"),
            "float16",
            "large_runtime_contract_invalid",
        ),
        (
            ("runtime", "peak_allocated_bytes"),
            0,
            "large_runtime_contract_invalid",
        ),
        (
            ("runtime", "observed_model_output_dtypes"),
            {},
            "large_runtime_contract_invalid",
        ),
        (
            ("runtime", "oom_count"),
            1,
            "large_runtime_contract_invalid",
        ),
        (
            ("runtime", "oom_count"),
            False,
            "large_runtime_contract_invalid",
        ),
        (
            ("progress", "source_rows_scanned"),
            322_379,
            "large_source_scan_contract_invalid",
        ),
        (
            ("progress", "exclusions", "reserved_prompt"),
            module.EXPECTED_EXCLUSIONS["reserved_prompt"] - 1,
            "large_source_scan_contract_invalid",
        ),
        (
            ("progress", "source_rows_by_split", "train"),
            module.SOURCE_ROWS_BY_SPLIT["train"] - 1,
            "large_source_scan_contract_invalid",
        ),
        (
            ("policy", "evaluation_audio_decoded"),
            True,
            "large_evaluation_isolation_invalid",
        ),
        (
            ("frame_mapping_audit", "receptive_field_samples"),
            399,
            "large_frame_mapping_audit_invalid",
        ),
        (
            ("resource_estimate", "safety_factor"),
            1.5,
            "large_full_report_resource_formula_invalid",
        ),
        (
            (
                "resource_estimate",
                "observed_compressed_bytes_per_row",
                "phone",
            ),
            1.0,
            "large_full_report_resource_formula_invalid",
        ),
        (
            ("resource_estimate", "fixed_float16_embedding_bytes_total"),
            1,
            "large_full_report_resource_formula_invalid",
        ),
        (
            ("resource_estimate", "estimated_full_cache_upper_bytes"),
            1,
            "large_full_report_resource_formula_invalid",
        ),
    ],
)
def test_large_contract_tampering_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: tuple[str, ...],
    value: object,
    expected_code: str,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    _mutate_report(fixture, path, value)

    result = _validate(fixture)

    assert result["status"] == "failed"
    assert expected_code in _codes(result)


@pytest.mark.parametrize(
    "target",
    [
        "base_head",
        "registry",
        "smoke_report",
        "smoke_review",
        "reviewer_cli",
        "reviewer_core",
    ],
)
def test_every_external_full_gate_link_is_hash_checked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    path = {
        "base_head": fixture.head,
        "registry": fixture.registry,
        "smoke_report": fixture.smoke_report,
        "smoke_review": fixture.smoke_review,
        "reviewer_cli": fixture.reviewer_cli,
        "reviewer_core": fixture.reviewer_core,
    }[target]
    if target in {"base_head", "smoke_review"}:
        document = json.loads(path.read_text(encoding="utf-8"))
        document["tampered"] = True
        path.write_text(json.dumps(document), encoding="utf-8")
    else:
        with path.open("ab") as handle:
            handle.write(b"\ntampered")

    result = _validate(fixture)

    assert result["status"] == "failed"
    assert "large_full_run_gate_chain_invalid" in _codes(result)


def test_full_gate_report_field_tampering_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    _mutate_report(
        fixture,
        ("configuration", "full_run_gate", "reviewer_core_sha256"),
        "0" * 64,
    )

    result = _validate(fixture)

    assert result["status"] == "failed"
    assert "large_full_run_gate_chain_invalid" in _codes(result)


@pytest.mark.parametrize(
    ("target", "path", "value"),
    [
        ("head", ("summary", "results_are_development_only"), False),
        ("registry", ("status",), "failed"),
        (
            "smoke_review",
            ("technical_checks", "all_embeddings_finite_and_nonzero"),
            False,
        ),
        ("smoke_review", ("technical_checks", "oom_count"), False),
    ],
)
def test_independent_gate_semantics_fail_even_after_hashes_are_refreshed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    path: tuple[str, ...],
    value: object,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    document_path = {
        "head": fixture.head,
        "registry": fixture.registry,
        "smoke_review": fixture.smoke_review,
    }[target]
    document = json.loads(document_path.read_text(encoding="utf-8"))
    cursor = document
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    document_path.write_text(json.dumps(document), encoding="utf-8")
    _refresh_reported_gate_hashes(fixture)

    result = _validate(fixture)

    assert result["status"] == "failed"
    assert "large_full_run_gate_chain_invalid" in _codes(result)


def test_smoke_resource_formula_fails_after_review_and_gate_hash_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    smoke = json.loads(fixture.smoke_report.read_text(encoding="utf-8"))
    smoke["resource_estimate"]["projected_compressed_bytes_total"] += 1
    fixture.smoke_report.write_text(json.dumps(smoke), encoding="utf-8")
    review = json.loads(fixture.smoke_review.read_text(encoding="utf-8"))
    review["smoke_report_sha256"] = _sha256(fixture.smoke_report)
    fixture.smoke_review.write_text(json.dumps(review), encoding="utf-8")
    _refresh_reported_gate_hashes(fixture)

    result = _validate(fixture)

    assert result["status"] == "failed"
    assert "large_full_run_gate_chain_invalid" in _codes(result)


@pytest.mark.parametrize(
    "relative",
    [
        ".env.local",
        "data/raw/secret.txt",
        "data/restricted/secret.txt",
        "data/processed/stack_private.json",
        "data/processed/calibration_private.json",
        "data/processed/locked_test_private.json",
    ],
)
def test_registry_forbidden_input_is_rejected_before_hash_or_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    forbidden = _write(tmp_path, relative, b"must never be hashed or read\n")
    metadata = json.loads(fixture.registry.read_text(encoding="utf-8"))
    metadata["inputs"]["config"][0] = {
        "path": relative,
        "sha256": _sha256(forbidden),
        "size_bytes": forbidden.stat().st_size,
    }
    fixture.registry.write_text(json.dumps(metadata), encoding="utf-8")
    _refresh_reported_gate_hashes(fixture)
    real_sha256 = module.sha256_file

    def guarded_sha256(path: Path) -> str:
        if path.resolve() == forbidden.resolve():
            raise AssertionError("forbidden registry input was hashed")
        return real_sha256(path)

    monkeypatch.setattr(module, "sha256_file", guarded_sha256)

    result = _validate(fixture)

    assert result["status"] == "failed"
    assert "large_full_run_gate_chain_invalid" in _codes(result)
    assert result["privacy"]["raw_data_read"] is False
    assert result["privacy"]["environment_files_read"] is False
    assert relative not in json.dumps(result)


def test_full_output_must_fit_independent_pre_run_smoke_upper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    report = json.loads(fixture.report.read_text(encoding="utf-8"))
    report["outputs"]["utterance"]["byte_count"] = (
        module.SMOKE_ESTIMATED_UPPER_BYTES + 1
    )
    bytes_per_row = {
        kind: report["outputs"][kind]["byte_count"] / module.EXPECTED_ROWS[kind]
        for kind in module.EXPECTED_ROWS
    }
    projected = {
        kind: __import__("math").ceil(bytes_per_row[kind] * module.EXPECTED_ROWS[kind])
        for kind in module.EXPECTED_ROWS
    }
    projected_total = sum(projected.values())
    resource = report["resource_estimate"]
    resource["observed_compressed_bytes_per_row"] = bytes_per_row
    resource["projected_compressed_bytes_by_kind"] = projected
    resource["projected_compressed_bytes_total"] = projected_total
    resource["estimated_full_cache_upper_bytes"] = __import__("math").ceil(
        projected_total * module.RESOURCE_SAFETY_FACTOR
    )
    fixture.report.write_text(json.dumps(report), encoding="utf-8")

    result = _validate(fixture)

    assert result["status"] == "failed"
    assert "large_smoke_capacity_gate_invalid" in _codes(result)
    capacity = result["resource_estimate"]["meaningful_pre_run_capacity_gate"]
    assert capacity["estimated_full_cache_upper_bytes"] == 7_481_336_023
    assert capacity["actual_within_pre_run_upper"] is False


def test_preprocessor_file_tampering_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    fixture.preprocessor.write_text('{"do_normalize":false}', encoding="utf-8")

    result = _validate(fixture)

    assert result["status"] == "failed"
    assert "large_preprocessor_contract_invalid" in _codes(result)


def test_local_model_config_architecture_tampering_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    model_config = fixture.preprocessor.with_name("config.json")
    document = json.loads(model_config.read_text(encoding="utf-8"))
    document["num_hidden_layers"] = 23
    model_config.write_text(json.dumps(document), encoding="utf-8")

    result = _validate(fixture)

    assert result["status"] == "failed"
    assert "large_model_contract_invalid" in _codes(result)


def test_generic_dependency_file_tampering_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    fixture.dependency.write_text("# tampered dependency\n", encoding="utf-8")

    result = _validate(fixture)

    assert result["status"] == "failed"
    assert "large_builder_dependency_invalid" in _codes(result)


def test_generic_validator_source_tampering_is_rejected_before_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    source = fixture.root / module.GENERIC_VALIDATOR_CORE_RELATIVE_PATH
    source.write_text("# tampered generic validator\n", encoding="utf-8")

    result = _validate(fixture)

    assert result["status"] == "failed"
    assert "large_validator_source_chain_invalid" in _codes(result)
    assert result["components"]["generic_cache_validation"]["status"] == "not_run"


def test_generic_validator_failure_and_split_coverage_are_propagated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    fixture.generic["status"] = "failed"
    fixture.generic["errors"] = [{"code": "phone_shard_hash_mismatch", "count": 1}]
    fixture.generic["scan"]["split_counts"]["word"] = {
        "train": module.EXPECTED_ROWS["word"]
    }

    result = _validate(fixture)

    codes = _codes(result)
    assert result["status"] == "failed"
    assert "generic_full_cache_validation_failed" in codes
    assert "phone_shard_hash_mismatch" in codes
    assert "large_full_split_coverage_invalid" in codes


def test_core_rejects_noncanonical_cache_path_before_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    other_cache = tmp_path / "other-cache"
    other_cache.mkdir()

    with pytest.raises(
        module.LargeCacheValidationError,
        match="canonical path",
    ):
        module.validate_wavlm_large_feature_cache(
            cache_root=other_cache,
            report_path=fixture.report,
            config_path=fixture.config,
            builder_path=fixture.builder,
            base_plus_head_report=fixture.head,
            smoke_review=fixture.smoke_review,
            workspace_root=fixture.root,
        )


def test_general_canonical_input_leaf_symlink_remains_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    relocated = tmp_path / "synthetic" / "relocated-full-report.json"
    relocated.parent.mkdir(parents=True, exist_ok=True)
    fixture.report.rename(relocated)
    fixture.report.symlink_to(relocated)

    with pytest.raises(module.LargeCacheValidationError, match="symlink"):
        _validate(fixture)


@pytest.mark.parametrize(
    "payload",
    [
        '{"status": NaN}',
        '{"status": "completed", "status": "forged"}',
    ],
)
def test_strict_json_loader_rejects_nonfinite_or_duplicate_values(
    tmp_path: Path,
    payload: str,
) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(module.LargeCacheValidationError):
        module._load_json(path, role="synthetic strict JSON")


def test_strict_yaml_loader_rejects_nonfinite_values(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text("value: .nan\n", encoding="utf-8")

    with pytest.raises(module.LargeCacheValidationError):
        module._load_yaml(path, role="synthetic strict YAML")


def test_gate_json_nan_is_aggregate_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    review = fixture.smoke_review.read_text(encoding="utf-8")
    fixture.smoke_review.write_text(
        review[:-1] + ', "forged": NaN}',
        encoding="utf-8",
    )

    result = _validate(fixture)

    assert result["status"] == "failed"
    assert "large_full_run_gate_chain_invalid" in _codes(result)


def test_duplicate_key_in_head_gate_json_is_aggregate_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    original = fixture.head.read_text(encoding="utf-8")
    fixture.head.write_text(
        '{"status":"forged",' + original[1:],
        encoding="utf-8",
    )

    result = _validate(fixture)

    assert result["status"] == "failed"
    assert "large_full_run_gate_chain_invalid" in _codes(result)


def test_cli_turns_nonfinite_full_report_into_atomic_aggregate_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    original = fixture.report.read_text(encoding="utf-8")
    fixture.report.write_text(
        original[:-1] + ', "forged": NaN}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--workspace-root",
            str(fixture.root),
            "--cache-root",
            str(fixture.cache),
            "--report",
            str(fixture.report),
            "--config",
            str(fixture.config),
            "--builder",
            str(fixture.builder),
            "--base-plus-head-report",
            str(fixture.head),
            "--smoke-review",
            str(fixture.smoke_review),
            "--output",
            str(fixture.output),
        ],
    )

    assert SCRIPT_MODULE.main() == 1
    result = json.loads(fixture.output.read_text(encoding="utf-8"))
    assert result == module.failure_large_validation("large_validation_contract_error")
    assert not list(fixture.output.parent.glob(f".{fixture.output.name}.*.part"))


def test_cli_atomically_writes_only_the_pinned_aggregate_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / module.RECOMMENDED_OUTPUT_RELATIVE_PATH
    approved = {
        "schema_version": module.VALIDATION_SCHEMA_VERSION,
        "status": "passed",
        "scan": {
            "rows_checked": dict(module.EXPECTED_ROWS),
            "embedding_vectors_checked": dict(module.EXPECTED_ROWS),
        },
        "errors": [],
    }
    monkeypatch.setattr(
        SCRIPT_MODULE,
        "validate_wavlm_large_feature_cache",
        lambda **kwargs: approved,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--workspace-root",
            str(tmp_path),
            "--cache-root",
            "unused-cache",
            "--report",
            "unused-report",
            "--config",
            "unused-config",
            "--builder",
            "unused-builder",
            "--base-plus-head-report",
            "unused-head",
            "--smoke-review",
            "unused-smoke",
            "--output",
            str(output),
        ],
    )

    assert SCRIPT_MODULE.main() == 0
    assert json.loads(output.read_text(encoding="utf-8")) == approved
    assert not list(output.parent.glob(f".{output.name}.*.part"))


def test_cli_atomically_writes_aggregate_failure_and_exits_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / module.RECOMMENDED_OUTPUT_RELATIVE_PATH
    failed = module.failure_large_validation("synthetic_contract_failure")
    monkeypatch.setattr(
        SCRIPT_MODULE,
        "validate_wavlm_large_feature_cache",
        lambda **kwargs: failed,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--workspace-root",
            str(tmp_path),
            "--cache-root",
            "unused-cache",
            "--report",
            "unused-report",
            "--config",
            "unused-config",
            "--builder",
            "unused-builder",
            "--base-plus-head-report",
            "unused-head",
            "--smoke-review",
            "unused-smoke",
            "--output",
            str(output),
        ],
    )

    assert SCRIPT_MODULE.main() == 1
    assert json.loads(output.read_text(encoding="utf-8")) == failed
    assert not list(output.parent.glob(f".{output.name}.*.part"))


@pytest.mark.parametrize(
    "unsafe",
    [
        "../outside.json",
        "data/restricted/validation.json",
        "artifacts/reviews/wrong-name.json",
    ],
)
def test_cli_rejects_unsafe_or_unpinned_output_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe: str,
) -> None:
    output = tmp_path / unsafe
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--workspace-root",
            str(tmp_path),
            "--cache-root",
            "unused-cache",
            "--report",
            "unused-report",
            "--config",
            "unused-config",
            "--builder",
            "unused-builder",
            "--base-plus-head-report",
            "unused-head",
            "--smoke-review",
            "unused-smoke",
            "--output",
            str(output),
        ],
    )

    assert SCRIPT_MODULE.main() == 2
    assert not output.exists()


def test_cli_rejects_precreated_output_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / module.RECOMMENDED_OUTPUT_RELATIVE_PATH
    output.parent.mkdir(parents=True)
    target = tmp_path / "synthetic-target.json"
    target.write_text('{"untouched":true}\n', encoding="utf-8")
    output.symlink_to(target)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--workspace-root",
            str(tmp_path),
            "--cache-root",
            "unused-cache",
            "--report",
            "unused-report",
            "--config",
            "unused-config",
            "--builder",
            "unused-builder",
            "--base-plus-head-report",
            "unused-head",
            "--smoke-review",
            "unused-smoke",
            "--output",
            str(output),
        ],
    )

    assert SCRIPT_MODULE.main() == 2
    assert target.read_text(encoding="utf-8") == '{"untouched":true}\n'
    assert output.is_symlink()


def test_unique_atomic_temp_ignores_precreated_part_symlink(
    tmp_path: Path,
) -> None:
    output = tmp_path / module.RECOMMENDED_OUTPUT_RELATIVE_PATH
    output.parent.mkdir(parents=True)
    target = tmp_path / "synthetic-part-target.json"
    target.write_text('{"untouched":true}\n', encoding="utf-8")
    decoy = output.parent / f".{output.name}.precreated.part"
    decoy.symlink_to(target)

    module.write_large_json_atomic(output, {"status": "passed"})

    assert json.loads(output.read_text(encoding="utf-8")) == {"status": "passed"}
    assert target.read_text(encoding="utf-8") == '{"untouched":true}\n'
    assert decoy.is_symlink()


def test_cli_rejects_symlinked_output_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_artifacts = tmp_path / "real-artifacts"
    real_artifacts.mkdir()
    (tmp_path / "artifacts").symlink_to(real_artifacts, target_is_directory=True)
    output = tmp_path / module.RECOMMENDED_OUTPUT_RELATIVE_PATH
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--workspace-root",
            str(tmp_path),
            "--cache-root",
            "unused-cache",
            "--report",
            "unused-report",
            "--config",
            "unused-config",
            "--builder",
            "unused-builder",
            "--base-plus-head-report",
            "unused-head",
            "--smoke-review",
            "unused-smoke",
            "--output",
            str(output),
        ],
    )

    assert SCRIPT_MODULE.main() == 2
    assert not (real_artifacts / "reviews" / output.name).exists()
