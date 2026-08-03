from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
import yaml  # type: ignore[import-untyped]

import voice_coach.evaluation.wavlm_comparison as comparison_module
from voice_coach.evaluation.wavlm_comparison import (
    ComparisonContractError,
    OUTCOMES,
    PREDICTION_SCHEMA_VERSION,
    SEEDS,
    compare_wavlm_frozen_heads,
    large_adoption_gate,
    paired_phone_macro_f1_bootstrap,
    phone_event_counts,
    sha256_file,
)
from voice_coach.evaluation.metrics import (
    auprc,
    icc_2_1,
    macro_f1,
    mae,
    quadratic_weighted_kappa,
    spearman_correlation,
)


MODEL_PATHS = {
    "trainer": "scripts/train_wavlm_frozen_head.py",
    "head": "src/voice_coach/models/wavlm_frozen_head.py",
}
MODEL_CONTENTS = {
    "trainer": b"# synthetic frozen-head trainer\n",
    "head": b"# synthetic frozen-head model\n",
}
TRAINER_SHA = hashlib.sha256(MODEL_CONTENTS["trainer"]).hexdigest()
HEAD_SHA = hashlib.sha256(MODEL_CONTENTS["head"]).hexdigest()
MANIFEST_SHA = "c" * 64
LABEL_SHA = "d" * 64
TRAINING_CONFIG_PATHS = {
    "base_plus": "configs/experiments/wavlm_base_plus_frozen_head_v1.yaml",
    "large": "configs/experiments/wavlm_large_frozen_head_v1.yaml",
}
TRAINING_CONFIG_CONTENTS = {
    "base_plus": b"experiment:\n  id: synthetic_base_plus_head\n",
    "large": b"experiment:\n  id: synthetic_large_head\n",
}
PREDICTION_METADATA = {
    b"voice_coach.schema_version": PREDICTION_SCHEMA_VERSION.encode(),
    b"voice_coach.development_only": b"true",
    b"voice_coach.oracle_alignment": b"true",
    b"voice_coach.contains_direct_identifiers": b"false",
    b"voice_coach.access_scope": b"restricted_internal",
    b"voice_coach.shareable": b"false",
}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _training_config_path(root: Path, variant: str) -> Path:
    return root / TRAINING_CONFIG_PATHS[variant]


def _training_config_sha256(variant: str) -> str:
    return hashlib.sha256(TRAINING_CONFIG_CONTENTS[variant]).hexdigest()


def _write_training_configs(root: Path) -> None:
    for variant, content in TRAINING_CONFIG_CONTENTS.items():
        path = _training_config_path(root, variant)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _write_model_sources(root: Path) -> None:
    for name, content in MODEL_CONTENTS.items():
        path = root / MODEL_PATHS[name]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _phone_arrays() -> dict[str, np.ndarray]:
    utterances = np.repeat(
        np.asarray([_hash(f"utterance-{index}") for index in range(6)]),
        3,
    )
    speakers = np.repeat(
        np.asarray(
            [
                _hash("speaker-0"),
                _hash("speaker-0"),
                _hash("speaker-1"),
                _hash("speaker-1"),
                _hash("speaker-2"),
                _hash("speaker-2"),
            ]
        ),
        3,
    )
    return {
        "utterance_ids": utterances,
        "speaker_ids": speakers,
        "phone_indices": np.tile(np.arange(3), 6),
        "target_labels": np.tile(np.asarray(["AA", "B", "C"]), 6),
        "targets": np.tile(np.asarray([0, 1, 0]), 6),
    }


def _utterance_arrays() -> dict[str, np.ndarray]:
    return {
        "utterance_ids": np.asarray(
            [_hash(f"utterance-{index}") for index in range(6)]
        ),
        "speaker_ids": np.asarray(
            [
                _hash("speaker-0"),
                _hash("speaker-0"),
                _hash("speaker-1"),
                _hash("speaker-1"),
                _hash("speaker-2"),
                _hash("speaker-2"),
            ]
        ),
        "targets": np.asarray(
            [
                [1, 2, 5],
                [2, 3, 4],
                [2, 3, 4],
                [3, 4, 3],
                [3, 4, 3],
                [4, 5, 2],
            ],
            dtype=np.float64,
        ),
    }


def _phone_predictions(variant: str, seed: int) -> np.ndarray:
    targets = _phone_arrays()["targets"].copy()
    flips: tuple[int, ...]
    if variant == "base_plus":
        flips = {
            42: (1, 6, 10, 17),
            43: (0, 4, 9, 13),
            44: (2, 7, 12, 16),
        }[seed]
    else:
        flips = {
            42: (6,),
            43: (4,),
            44: (12,),
        }[seed]
    targets[np.asarray(flips)] = 1 - targets[np.asarray(flips)]
    return targets


def _utterance_predictions(variant: str, seed: int) -> np.ndarray:
    targets = _utterance_arrays()["targets"]
    seed_offset = {42: -0.05, 43: 0.0, 44: 0.05}[seed]
    if variant == "base_plus":
        error = np.asarray(
            [
                [0.45, -0.35, -0.40],
                [-0.30, 0.40, 0.35],
                [0.35, -0.30, -0.35],
                [-0.40, 0.35, 0.30],
                [0.30, -0.40, -0.30],
                [-0.35, 0.30, 0.40],
            ]
        )
    else:
        error = np.asarray(
            [
                [0.10, -0.08, -0.09],
                [-0.07, 0.09, 0.08],
                [0.08, -0.07, -0.08],
                [-0.09, 0.08, 0.07],
                [0.07, -0.09, -0.07],
                [-0.08, 0.07, 0.09],
            ]
        )
    return np.clip(targets + error + seed_offset, 1.0, 5.0)


def _write_phone_prediction(
    path: Path,
    *,
    variant: str,
    seed: int,
) -> dict[str, Any]:
    identity = _phone_arrays()
    predictions = _phone_predictions(variant, seed)
    probabilities = np.where(predictions == 1, 0.8, 0.2)
    rows = [
        {
            "schema_version": PREDICTION_SCHEMA_VERSION,
            "development_only": True,
            "oracle_alignment": True,
            "seed": seed,
            "split": "dev",
            "utterance_id_sha256": str(identity["utterance_ids"][index]),
            "speaker_id_sha256": str(identity["speaker_ids"][index]),
            "phone_index": int(identity["phone_indices"][index]),
            "target_label": str(identity["target_labels"][index]),
            "y_true": int(identity["targets"][index]),
            "y_pred": int(predictions[index]),
            "error_probability": float(probabilities[index]),
            "decision_threshold": 0.5,
        }
        for index in range(len(predictions))
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(
        rows,
        schema=comparison_module._prediction_schema("phone"),
    )
    pq.write_table(table, path)
    return {
        "macro_f1": macro_f1(
            identity["targets"],
            predictions,
            labels=[0, 1],
        ),
        "auprc": auprc(
            identity["targets"],
            probabilities,
            labels=[0, 1],
            undefined="nan",
        ),
        "decision_threshold": 0.5,
    }


def _write_utterance_prediction(
    path: Path,
    *,
    variant: str,
    seed: int,
) -> dict[str, Any]:
    identity = _utterance_arrays()
    predictions = _utterance_predictions(variant, seed)
    rows = []
    for row_index in range(len(identity["utterance_ids"])):
        row: dict[str, Any] = {
            "schema_version": PREDICTION_SCHEMA_VERSION,
            "development_only": True,
            "oracle_alignment": True,
            "seed": seed,
            "split": "dev",
            "utterance_id_sha256": str(identity["utterance_ids"][row_index]),
            "speaker_id_sha256": str(identity["speaker_ids"][row_index]),
        }
        for outcome_index, outcome in enumerate(OUTCOMES):
            row[f"{outcome}_true"] = float(
                identity["targets"][row_index, outcome_index]
            )
            row[f"{outcome}_pred"] = float(predictions[row_index, outcome_index])
            row[f"{outcome}_observed"] = True
        rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(
        rows,
        schema=comparison_module._prediction_schema("utterance"),
    )
    pq.write_table(table, path)
    outcomes: dict[str, Any] = {}
    for outcome_index, outcome in enumerate(OUTCOMES):
        targets = identity["targets"][:, outcome_index]
        predicted = predictions[:, outcome_index]
        outcomes[outcome] = {
            "metrics": {
                "qwk": quadratic_weighted_kappa(
                    targets.astype(np.int64),
                    np.rint(predicted).astype(np.int64),
                    labels=[1, 2, 3, 4, 5],
                ),
                "spearman": spearman_correlation(targets, predicted),
                "mae": mae(targets, predicted),
                "icc_2_1": icc_2_1(np.column_stack((targets, predicted))),
            }
        }
    return outcomes


def _base_report(
    *,
    root: Path,
    variant: str,
    report_path: Path,
    registry_path: Path,
) -> dict[str, Any]:
    encoder_ids = {
        "base_plus": "microsoft/wavlm-base-plus",
        "large": "microsoft/wavlm-large",
    }
    runs = []
    for seed in SEEDS:
        seed_directory = report_path.parent / f"seed_{seed}"
        phone_path = seed_directory / "phone_dev_predictions.parquet"
        utterance_path = seed_directory / "utterance_dev_predictions.parquet"
        phone_metrics = _write_phone_prediction(
            phone_path,
            variant=variant,
            seed=seed,
        )
        outcomes = _write_utterance_prediction(
            utterance_path,
            variant=variant,
            seed=seed,
        )
        runs.append(
            {
                "seed": seed,
                "selection": {
                    "dev_values_used_for_selection": False,
                },
                "phone": {
                    "rows": 18,
                    "metric_scope": "primary_dev_single_final_pass",
                    "metrics": phone_metrics,
                    "predictions": {
                        "path": phone_path.relative_to(report_path.parent).as_posix(),
                        "sha256": sha256_file(phone_path),
                    },
                },
                "utterance": {
                    "rows": 6,
                    "metric_scope": "primary_dev_single_final_pass",
                    "outcomes": outcomes,
                    "predictions": {
                        "path": utterance_path.relative_to(
                            report_path.parent
                        ).as_posix(),
                        "sha256": sha256_file(utterance_path),
                    },
                },
            }
        )
    return {
        "schema_version": "voice_coach.wavlm_frozen_head_report.v1",
        "status": "completed",
        "development_only": True,
        "oracle_alignment": True,
        "deployable_alignment_claim_allowed": False,
        "encoder": {
            "variant": variant,
            "current_experiment_variant": variant,
            "id": encoder_ids[variant],
            "frozen": True,
        },
        "sources": {
            "training_config_sha256": _training_config_sha256(variant),
            "trainer_source_sha256": TRAINER_SHA,
            "head_source_sha256": HEAD_SHA,
            "modeling_manifest_sha256": MANIFEST_SHA,
            "explicit_utterance_labels_sha256": LABEL_SHA,
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
            "speaker_overlap_train_dev": 0,
            "access_scope": "restricted_internal",
            "shareable": False,
            "rows": {"utterance": 6, "phone": 18, "word": 12},
            "split_rows": {
                "utterance": {"train": 3, "dev": 3},
                "phone": {"train": 9, "dev": 9},
                "word": {"train": 6, "dev": 6},
            },
        },
        "utterance_label_contract": {
            "sha256": LABEL_SHA,
            "row_count": 6,
            "split_rows": {"train": 3, "dev": 3},
            "missing_labels": 0,
        },
        "leakage_controls": {
            "accepted_splits": ["train", "dev"],
            "stack_accessed": False,
            "calibration_accessed": False,
            "locked_test_accessed": False,
            "dev_used_for_selection": False,
            "dev_inference_passes_per_seed": 1,
            "train_inner_validation_used": True,
            "inner_validation_speaker_disjoint": True,
            "phone_threshold_selected_on_train_inner_validation": True,
            "early_stopping_selected_on_train_inner_validation": True,
            "primary_analysis_scope": "synthetic_primary",
            "raw_manifest_read": False,
            "raw_path_columns_read": [],
            "environment_files_read": [],
            "environment_files_registered": [],
        },
        "summary": {
            "seeds_completed": list(SEEDS),
            "results_are_development_only": True,
            "locked_test_remains_unread": True,
            "stack_remains_unread": True,
            "calibration_remains_unread": True,
            "dev_used_for_selection": False,
            "dev_inference_passes_per_seed": 1,
            "oracle_alignment": True,
            "promotion_decision_allowed": False,
        },
        "head_contract": {
            "phone": {"hidden_dimensions": [8]},
            "utterance": {"hidden_dimensions": [8]},
            "optimization": {"learning_rate": 0.001},
            "same_hyperparameters_for_every_seed": True,
            "task_rng_reinitialized_per_head": True,
            "optimizer": "AdamW",
        },
        "runtime": {
            "device": {
                "type": "cuda",
                "production_evidence_allowed": True,
            },
            "determinism": {
                "deterministic_algorithms": True,
                "cublas_workspace_config": ":4096:8",
                "configured_before_cuda_initialization": True,
                "fresh_process_contract_enforced": True,
            },
        },
        "data": {
            "inner_validation": {"speaker_overlap": 0},
            "phone": {"primary_rows": 18},
            "utterance": {"primary_rows": 6},
        },
        "experiment_registry": {
            "integrated": True,
            "t03_complete": True,
            "metrics_recorded": True,
            "artifacts_recorded": True,
            "implementation_source_hashes_match_registry_inputs": True,
            "runtime_packages_gpu_git_recorded": True,
            "seeds_recorded": True,
            "run_id": f"{variant}-synthetic",
            "metadata_path": registry_path.relative_to(root).as_posix(),
            "registered_artifact_count": 13,
        },
        "runs": runs,
    }


def _write_registry(
    *,
    root: Path,
    variant: str,
    report_path: Path,
    report: dict[str, Any],
    registry_path: Path,
) -> None:
    filler = [
        {
            "name": f"synthetic_artifact_{index}",
            "path": f"artifacts/synthetic-{index}.bin",
            "sha256": str(index).zfill(64),
        }
        for index in range(12)
    ]
    registry = {
        "schema_version": 1,
        "status": "completed",
        "failure": None,
        "run_id": report["experiment_registry"]["run_id"],
        "timestamps": {"ended": "2026-07-29T00:00:00+00:00"},
        "seeds": {f"experiment_seed_{seed}": seed for seed in SEEDS},
        "inputs": {
            "config": [
                {
                    "path": TRAINING_CONFIG_PATHS[variant],
                    "sha256": _training_config_sha256(variant),
                    "size_bytes": _training_config_path(root, variant).stat().st_size,
                }
            ],
            "model": [
                {
                    "path": MODEL_PATHS["trainer"],
                    "sha256": TRAINER_SHA,
                    "size_bytes": (root / MODEL_PATHS["trainer"]).stat().st_size,
                },
                {
                    "path": MODEL_PATHS["head"],
                    "sha256": HEAD_SHA,
                    "size_bytes": (root / MODEL_PATHS["head"]).stat().st_size,
                },
            ],
        },
        "metrics": [
            *[{"split": "dev", "step": seed} for seed in SEEDS],
            {"split": "dev", "step": None},
        ],
        "artifacts": [
            *filler,
            {
                "name": "final_report",
                "path": str(report_path),
                "sha256": sha256_file(report_path),
            },
        ],
    }
    _write_json(registry_path, registry)


def _repin_report(
    report_path: Path,
    registry_path: Path,
    report: dict[str, Any],
) -> None:
    _write_json(report_path, report)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    final_report = next(
        item for item in registry["artifacts"] if item["name"] == "final_report"
    )
    final_report["sha256"] = sha256_file(report_path)
    _write_json(registry_path, registry)


def _repin_prediction(
    workspace: dict[str, Any],
    *,
    variant: str,
    task: str,
    seed_index: int,
    table: pa.Table,
) -> None:
    report = workspace[variant]["report"]
    record = report["runs"][seed_index][task]["predictions"]
    path = workspace[variant]["report_path"].parent / record["path"]
    pq.write_table(table, path)
    record["sha256"] = sha256_file(path)
    _repin_report(
        workspace[variant]["report_path"],
        workspace[variant]["registry_path"],
        report,
    )


def _write_config(root: Path) -> Path:
    config = {
        "schema_version": 1,
        "experiment": {
            "id": "synthetic_wavlm_comparison",
            "stage": "frozen_head_comparison",
            "development_only": True,
            "oracle_alignment": True,
        },
        "inputs": {
            "base_plus_report": "artifacts/base/report.json",
            "large_report": "artifacts/large/report.json",
            "expected_variants": {
                "base_plus": {
                    "encoder_id": "microsoft/wavlm-base-plus",
                    "training_config_path": TRAINING_CONFIG_PATHS["base_plus"],
                    "training_config_sha256": _training_config_sha256("base_plus"),
                },
                "large": {
                    "encoder_id": "microsoft/wavlm-large",
                    "training_config_path": TRAINING_CONFIG_PATHS["large"],
                    "training_config_sha256": _training_config_sha256("large"),
                },
            },
            "expected_trainer_source_path": MODEL_PATHS["trainer"],
            "expected_trainer_source_sha256": TRAINER_SHA,
            "expected_head_source_path": MODEL_PATHS["head"],
            "expected_head_source_sha256": HEAD_SHA,
            "expected_modeling_manifest_sha256": MANIFEST_SHA,
            "expected_explicit_utterance_labels_sha256": LABEL_SHA,
            "expected_cache_rows": {
                "utterance": 6,
                "phone": 18,
                "word": 12,
            },
        },
        "comparison": {
            "seeds": list(SEEDS),
            "primary_scope": "primary_dev_paired",
            "effect_direction": "large_minus_base",
            "require_identical_prediction_order": True,
            "require_completed_source_registries": True,
            "allowed_metric_split": "dev",
            "forbidden_splits": ["stack", "calibration", "locked_test"],
        },
        "bootstrap": {
            "method": "paired_speaker_cluster_percentile",
            "n_resamples": 5000,
            "confidence_level": 0.95,
            "seed": 42,
            "p_value": "two_sided_zero_with_plus_one_correction",
        },
        "gate": {
            "metric": "phone_macro_f1",
            "absolute_improvement": 0.03,
            "rule": "point_estimate_gte_threshold",
        },
        "multiplicity": {
            "family": "utterance_icc_2_1_across_outcomes",
            "outcomes": list(OUTCOMES),
            "method": "holm",
            "alpha": 0.05,
        },
        "event_f1": {
            "unit": ("maximal_contiguous_positive_phone_index_run_within_utterance"),
            "match": "one_to_one_any_phone_index_overlap",
            "zero_event_policy": "undefined",
        },
        "privacy": {
            "aggregate_only_output": True,
            "input_access_scope": "restricted_internal",
            "shareable": False,
            "environment_files_forbidden": True,
            "raw_data_access_forbidden": True,
        },
        "output": {
            "generation_directory": "artifacts/comparison/generations",
            "commit_manifest": "artifacts/comparison/commit.json",
            "atomic_publish": True,
            "create_execution_receipt": True,
        },
    }
    path = root / "configs/comparison.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def synthetic_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    _write_training_configs(tmp_path)
    _write_model_sources(tmp_path)
    script_path = tmp_path / "scripts/compare_wavlm_frozen_heads.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("# synthetic comparison entrypoint\n", encoding="utf-8")
    module_path = tmp_path / "src/wavlm_comparison.py"
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text("# synthetic comparison module\n", encoding="utf-8")
    monkeypatch.setattr(comparison_module, "__file__", str(module_path))
    config_path = _write_config(tmp_path)
    sources: dict[str, Any] = {}
    for variant, directory_name in (
        ("base_plus", "base"),
        ("large", "large"),
    ):
        report_path = tmp_path / f"artifacts/{directory_name}/report.json"
        registry_path = (
            tmp_path / f"artifacts/registries/{directory_name}/metadata.json"
        )
        report = _base_report(
            root=tmp_path,
            variant=variant,
            report_path=report_path,
            registry_path=registry_path,
        )
        _write_json(report_path, report)
        _write_registry(
            root=tmp_path,
            variant=variant,
            report_path=report_path,
            report=report,
            registry_path=registry_path,
        )
        sources[variant] = {
            "report_path": report_path,
            "registry_path": registry_path,
            "report": report,
        }
    return {
        "root": tmp_path,
        "config_path": config_path,
        "script_path": script_path,
        **sources,
    }


def _compare(workspace: dict[str, Any]) -> dict[str, Any]:
    return compare_wavlm_frozen_heads(
        config_path=workspace["config_path"],
        workspace_root=workspace["root"],
        script_path=workspace["script_path"],
    )


def test_comparison_emits_only_aggregate_contract(
    synthetic_workspace: dict[str, Any],
) -> None:
    report = _compare(synthetic_workspace)

    assert report["status"] == "completed"
    assert report["contract"]["paired_identity"] == {
        "phone_ids_targets_speakers_equal": True,
        "utterance_ids_targets_speakers_equal": True,
        "cross_seed_equal": True,
    }
    assert (
        report["primary_comparison"]["paired_speaker_bootstrap"]["n_resamples"] == 5000
    )
    assert report["multiplicity"]["method"] == "holm"
    assert report["privacy"]["prediction_rows_written"] is False
    serialized = json.dumps(report)
    assert _hash("utterance-0") not in serialized
    assert _hash("speaker-0") not in serialized
    commit_path = synthetic_workspace["root"] / "artifacts/comparison/commit.json"
    commit = json.loads(commit_path.read_text(encoding="utf-8"))
    assert commit["status"] == "committed"
    assert (synthetic_workspace["root"] / commit["comparison_report"]["path"]).is_file()
    assert (synthetic_workspace["root"] / commit["execution_receipt"]["path"]).is_file()


def test_comparison_rejects_prediction_identity_mismatch(
    synthetic_workspace: dict[str, Any],
) -> None:
    report = synthetic_workspace["large"]["report"]
    phone_record = report["runs"][0]["phone"]["predictions"]
    phone_path = (
        synthetic_workspace["large"]["report_path"].parent / phone_record["path"]
    )
    table = pq.read_table(phone_path)
    values = table.column("utterance_id_sha256").to_pylist()
    values[:3] = [_hash("mismatched-utterance")] * 3
    index = table.column_names.index("utterance_id_sha256")
    changed = table.set_column(
        index,
        "utterance_id_sha256",
        pa.array(values),
    )
    pq.write_table(changed, phone_path)
    phone_record["sha256"] = sha256_file(phone_path)
    _repin_report(
        synthetic_workspace["large"]["report_path"],
        synthetic_workspace["large"]["registry_path"],
        report,
    )

    with pytest.raises(
        ComparisonContractError,
        match="paired prediction phone IDs differs",
    ):
        _compare(synthetic_workspace)


def test_comparison_rejects_leakage_claim(
    synthetic_workspace: dict[str, Any],
) -> None:
    report = synthetic_workspace["large"]["report"]
    report["leakage_controls"]["stack_accessed"] = True
    _repin_report(
        synthetic_workspace["large"]["report_path"],
        synthetic_workspace["large"]["registry_path"],
        report,
    )

    with pytest.raises(
        ComparisonContractError,
        match="leakage control stack_accessed differs",
    ):
        _compare(synthetic_workspace)


def test_comparison_rejects_prediction_hash_change(
    synthetic_workspace: dict[str, Any],
) -> None:
    report = synthetic_workspace["large"]["report"]
    phone_record = report["runs"][0]["phone"]["predictions"]
    phone_path = (
        synthetic_workspace["large"]["report_path"].parent / phone_record["path"]
    )
    phone_path.write_bytes(phone_path.read_bytes() + b"tampered")

    with pytest.raises(
        ComparisonContractError,
        match="phone prediction SHA-256 differs",
    ):
        _compare(synthetic_workspace)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("float_seed", "phone prediction Arrow schema differs"),
        ("float_phone_index", "phone prediction Arrow schema differs"),
        ("float_label", "phone prediction Arrow schema differs"),
        ("integer_policy", "phone prediction Arrow schema differs"),
        ("nonnullable_seed", "phone prediction Arrow schema differs"),
        ("extra_metadata", "phone prediction Arrow schema differs"),
        ("null_target_label", "phone prediction contains nulls"),
    ],
)
def test_comparison_rejects_phone_arrow_schema_tampering_before_numpy(
    synthetic_workspace: dict[str, Any],
    mutation: str,
    message: str,
) -> None:
    report = synthetic_workspace["large"]["report"]
    record = report["runs"][0]["phone"]["predictions"]
    path = synthetic_workspace["large"]["report_path"].parent / record["path"]
    table = pq.read_table(path)
    if mutation == "float_seed":
        index = table.schema.get_field_index("seed")
        table = table.set_column(
            index,
            "seed",
            pa.array([42.0] * table.num_rows, type=pa.float64()),
        )
    elif mutation == "float_phone_index":
        index = table.schema.get_field_index("phone_index")
        table = table.set_column(
            index,
            "phone_index",
            pa.array(
                table.column("phone_index").to_pylist(),
                type=pa.float32(),
            ),
        )
    elif mutation == "float_label":
        index = table.schema.get_field_index("y_true")
        table = table.set_column(
            index,
            "y_true",
            pa.array(table.column("y_true").to_pylist(), type=pa.float32()),
        )
    elif mutation == "integer_policy":
        index = table.schema.get_field_index("development_only")
        table = table.set_column(
            index,
            "development_only",
            pa.array([1] * table.num_rows, type=pa.int8()),
        )
    elif mutation == "nonnullable_seed":
        fields = list(table.schema)
        index = table.schema.get_field_index("seed")
        fields[index] = pa.field("seed", pa.int32(), nullable=False)
        table = pa.Table.from_arrays(
            table.columns,
            schema=pa.schema(fields, metadata=table.schema.metadata),
        )
    elif mutation == "extra_metadata":
        metadata = dict(table.schema.metadata or {})
        metadata[b"voice_coach.unexpected"] = b"true"
        table = table.replace_schema_metadata(metadata)
    else:
        values = table.column("target_label").to_pylist()
        values[0] = None
        index = table.schema.get_field_index("target_label")
        table = table.set_column(
            index,
            "target_label",
            pa.array(values, type=pa.string()),
        )
    _repin_prediction(
        synthetic_workspace,
        variant="large",
        task="phone",
        seed_index=0,
        table=table,
    )

    with pytest.raises(ComparisonContractError, match=message):
        _compare(synthetic_workspace)


@pytest.mark.parametrize(
    ("column", "arrow_type"),
    [
        ("pronunciation_score_pred", pa.float64()),
        ("pronunciation_score_observed", pa.int8()),
    ],
)
def test_comparison_rejects_utterance_arrow_type_tampering(
    synthetic_workspace: dict[str, Any],
    column: str,
    arrow_type: pa.DataType,
) -> None:
    report = synthetic_workspace["large"]["report"]
    record = report["runs"][0]["utterance"]["predictions"]
    path = synthetic_workspace["large"]["report_path"].parent / record["path"]
    table = pq.read_table(path)
    index = table.schema.get_field_index(column)
    table = table.set_column(
        index,
        column,
        pa.array(
            (
                [int(value) for value in table.column(column).to_pylist()]
                if pa.types.is_integer(arrow_type)
                else table.column(column).to_pylist()
            ),
            type=arrow_type,
        ),
    )
    _repin_prediction(
        synthetic_workspace,
        variant="large",
        task="utterance",
        seed_index=0,
        table=table,
    )

    with pytest.raises(
        ComparisonContractError,
        match="utterance prediction Arrow schema differs",
    ):
        _compare(synthetic_workspace)


def test_comparison_rejects_report_training_config_hash_mismatch(
    synthetic_workspace: dict[str, Any],
) -> None:
    report = synthetic_workspace["large"]["report"]
    report["sources"]["training_config_sha256"] = "e" * 64
    _repin_report(
        synthetic_workspace["large"]["report_path"],
        synthetic_workspace["large"]["registry_path"],
        report,
    )

    with pytest.raises(
        ComparisonContractError,
        match="large source training_config_sha256 differs",
    ):
        _compare(synthetic_workspace)


@pytest.mark.parametrize("mode", ["missing", "duplicate", "extra"])
def test_comparison_rejects_registry_config_input_cardinality(
    synthetic_workspace: dict[str, Any],
    mode: str,
) -> None:
    registry_path = synthetic_workspace["large"]["registry_path"]
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    original = registry["inputs"]["config"][0]
    if mode == "missing":
        registry["inputs"]["config"] = []
    elif mode == "duplicate":
        registry["inputs"]["config"].append(dict(original))
    else:
        registry["inputs"]["config"].append(
            {
                "path": "configs/experiments/unexpected.yaml",
                "sha256": "f" * 64,
                "size_bytes": 1,
            }
        )
    _write_json(registry_path, registry)

    with pytest.raises(
        ComparisonContractError,
        match="exactly one training config input",
    ):
        _compare(synthetic_workspace)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("sha256", "f" * 64, "registry training config SHA-256 differs"),
        ("size_bytes", 1, "registry training config size differs"),
        (
            "path",
            TRAINING_CONFIG_PATHS["base_plus"],
            "registry training config path differs",
        ),
    ],
)
def test_comparison_rejects_tampered_registry_config_metadata(
    synthetic_workspace: dict[str, Any],
    field: str,
    value: str | int,
    message: str,
) -> None:
    registry_path = synthetic_workspace["large"]["registry_path"]
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["inputs"]["config"][0][field] = value
    _write_json(registry_path, registry)

    with pytest.raises(
        ComparisonContractError,
        match=message,
    ):
        _compare(synthetic_workspace)


def test_comparison_rejects_extra_registry_config_metadata(
    synthetic_workspace: dict[str, Any],
) -> None:
    registry_path = synthetic_workspace["large"]["registry_path"]
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["inputs"]["config"][0]["unexpected"] = True
    _write_json(registry_path, registry)

    with pytest.raises(
        ComparisonContractError,
        match="registry training config metadata differs",
    ):
        _compare(synthetic_workspace)


def test_comparison_rejects_tampered_training_config_bytes(
    synthetic_workspace: dict[str, Any],
) -> None:
    path = _training_config_path(synthetic_workspace["root"], "large")
    path.write_bytes(path.read_bytes() + b"# tampered\n")

    with pytest.raises(
        ComparisonContractError,
        match="large expected training config bytes differ",
    ):
        _compare(synthetic_workspace)


def test_comparison_rejects_registry_config_path_escape(
    synthetic_workspace: dict[str, Any],
) -> None:
    registry_path = synthetic_workspace["large"]["registry_path"]
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["inputs"]["config"][0]["path"] = "../../outside.yaml"
    _write_json(registry_path, registry)

    with pytest.raises(
        ComparisonContractError,
        match="registry training config must stay inside workspace",
    ):
        _compare(synthetic_workspace)


@pytest.mark.parametrize("mode", ["missing", "duplicate", "extra"])
def test_comparison_rejects_registry_model_input_cardinality(
    synthetic_workspace: dict[str, Any],
    mode: str,
) -> None:
    registry_path = synthetic_workspace["large"]["registry_path"]
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if mode == "missing":
        registry["inputs"]["model"].pop()
    elif mode == "duplicate":
        registry["inputs"]["model"].append(dict(registry["inputs"]["model"][0]))
    else:
        registry["inputs"]["model"].append(
            {
                "path": "src/unexpected.py",
                "sha256": "f" * 64,
                "size_bytes": 1,
            }
        )
    _write_json(registry_path, registry)

    with pytest.raises(
        ComparisonContractError,
        match="registry model input count differs",
    ):
        _compare(synthetic_workspace)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("sha256", "f" * 64, "registry model SHA-256 differs"),
        ("size_bytes", 1, "registry model size differs"),
    ],
)
def test_comparison_rejects_tampered_registry_model_metadata(
    synthetic_workspace: dict[str, Any],
    field: str,
    value: str | int,
    message: str,
) -> None:
    registry_path = synthetic_workspace["large"]["registry_path"]
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["inputs"]["model"][0][field] = value
    _write_json(registry_path, registry)

    with pytest.raises(ComparisonContractError, match=message):
        _compare(synthetic_workspace)


def test_comparison_rejects_duplicate_registry_model_path(
    synthetic_workspace: dict[str, Any],
) -> None:
    registry_path = synthetic_workspace["large"]["registry_path"]
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["inputs"]["model"][0] = dict(registry["inputs"]["model"][1])
    _write_json(registry_path, registry)

    with pytest.raises(
        ComparisonContractError,
        match="registry model path set differs",
    ):
        _compare(synthetic_workspace)


def test_comparison_rejects_tampered_model_source_bytes(
    synthetic_workspace: dict[str, Any],
) -> None:
    path = synthetic_workspace["root"] / MODEL_PATHS["head"]
    path.write_bytes(path.read_bytes() + b"# tampered\n")

    with pytest.raises(
        ComparisonContractError,
        match="registry model SHA-256 differs",
    ):
        _compare(synthetic_workspace)


def test_comparison_rejects_registry_model_path_escape(
    synthetic_workspace: dict[str, Any],
) -> None:
    registry_path = synthetic_workspace["large"]["registry_path"]
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["inputs"]["model"][0]["path"] = "../../outside.py"
    _write_json(registry_path, registry)

    with pytest.raises(
        ComparisonContractError,
        match="registry model source must stay inside workspace",
    ):
        _compare(synthetic_workspace)


def test_comparison_rejects_duplicate_yaml_keys(
    synthetic_workspace: dict[str, Any],
) -> None:
    config_path = synthetic_workspace["config_path"]
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "\nschema_version: 1\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ComparisonContractError,
        match="duplicate YAML key",
    ):
        _compare(synthetic_workspace)


def test_comparison_rejects_nonfinite_yaml_anywhere(
    synthetic_workspace: dict[str, Any],
) -> None:
    config_path = synthetic_workspace["config_path"]
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "\npoison: .nan\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ComparisonContractError,
        match="comparison config contains a non-finite number",
    ):
        _compare(synthetic_workspace)


@pytest.mark.parametrize("kind", ["duplicate", "nonfinite"])
def test_comparison_rejects_strict_json_violations_in_head_report(
    synthetic_workspace: dict[str, Any],
    kind: str,
) -> None:
    report_path = synthetic_workspace["large"]["report_path"]
    original = report_path.read_text(encoding="utf-8")
    injected = '"status": "completed",' if kind == "duplicate" else '"poison": 1e999,'
    report_path.write_text(
        original.replace("{", "{" + injected, 1),
        encoding="utf-8",
    )
    registry_path = synthetic_workspace["large"]["registry_path"]
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    final_report = next(
        item for item in registry["artifacts"] if item["name"] == "final_report"
    )
    final_report["sha256"] = sha256_file(report_path)
    _write_json(registry_path, registry)

    expected = "duplicate JSON key" if kind == "duplicate" else "non-finite number"
    with pytest.raises(ComparisonContractError, match=expected):
        _compare(synthetic_workspace)


@pytest.mark.parametrize("kind", ["duplicate", "nonfinite"])
def test_comparison_rejects_strict_json_violations_in_registry(
    synthetic_workspace: dict[str, Any],
    kind: str,
) -> None:
    registry_path = synthetic_workspace["large"]["registry_path"]
    original = registry_path.read_text(encoding="utf-8")
    injected = '"status": "completed",' if kind == "duplicate" else '"poison": 1e999,'
    registry_path.write_text(
        original.replace("{", "{" + injected, 1),
        encoding="utf-8",
    )

    expected = "duplicate JSON key" if kind == "duplicate" else "non-finite number"
    with pytest.raises(ComparisonContractError, match=expected):
        _compare(synthetic_workspace)


@pytest.mark.parametrize(
    ("role", "forbidden_path"),
    [
        ("report", "raw-zone/report.json"),
        ("report", ".env_backup/report.json"),
        ("registry", "restricted_zone/metadata.json"),
        ("registry", "safe/.ENV-local/metadata.json"),
        ("prediction", "stack-zone/phone.parquet"),
        ("prediction", "safe/.envrc/phone.parquet"),
        ("commit", "calibration_zone/commit.json"),
        ("commit", "outputs/.env-secret/commit.json"),
        ("generation", "locked-zone/generations"),
        ("generation", "outputs/.environment/generations"),
    ],
)
def test_comparison_rejects_prefixed_forbidden_paths_for_every_role(
    synthetic_workspace: dict[str, Any],
    role: str,
    forbidden_path: str,
) -> None:
    if role in {"report", "commit", "generation"}:
        config = yaml.safe_load(
            synthetic_workspace["config_path"].read_text(encoding="utf-8")
        )
        if role == "report":
            config["inputs"]["base_plus_report"] = forbidden_path
        elif role == "commit":
            config["output"]["commit_manifest"] = forbidden_path
        else:
            config["output"]["generation_directory"] = forbidden_path
        synthetic_workspace["config_path"].write_text(
            yaml.safe_dump(config, sort_keys=False),
            encoding="utf-8",
        )
    elif role == "registry":
        report = synthetic_workspace["large"]["report"]
        report["experiment_registry"]["metadata_path"] = forbidden_path
        _repin_report(
            synthetic_workspace["large"]["report_path"],
            synthetic_workspace["large"]["registry_path"],
            report,
        )
    else:
        report = synthetic_workspace["large"]["report"]
        report["runs"][0]["phone"]["predictions"]["path"] = forbidden_path
        _repin_report(
            synthetic_workspace["large"]["report_path"],
            synthetic_workspace["large"]["registry_path"],
            report,
        )

    with pytest.raises(
        ComparisonContractError,
        match="violates privacy path policy",
    ):
        _compare(synthetic_workspace)


def test_comparison_rejects_environment_prefixed_config_before_read(
    synthetic_workspace: dict[str, Any],
) -> None:
    forbidden = synthetic_workspace["root"] / ".ENV_BACKUP"
    forbidden.write_bytes(synthetic_workspace["config_path"].read_bytes())

    with pytest.raises(
        ComparisonContractError,
        match="violates privacy path policy",
    ):
        compare_wavlm_frozen_heads(
            config_path=forbidden,
            workspace_root=synthetic_workspace["root"],
            script_path=synthetic_workspace["script_path"],
        )


@pytest.mark.parametrize(
    "target_kind",
    [
        "training_config",
        "trainer",
        "head",
        "registry_data",
        "registry_artifact",
        "prediction",
        "report",
    ],
)
def test_comparison_rejects_commit_collision_with_every_transitive_source(
    synthetic_workspace: dict[str, Any],
    target_kind: str,
) -> None:
    root = synthetic_workspace["root"]
    base_report = synthetic_workspace["base_plus"]["report"]
    registry_path = synthetic_workspace["base_plus"]["registry_path"]
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if target_kind == "training_config":
        target = root / TRAINING_CONFIG_PATHS["base_plus"]
    elif target_kind in {"trainer", "head"}:
        target = root / MODEL_PATHS[target_kind]
    elif target_kind == "registry_data":
        target = root / "data/processed/cache-report.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"synthetic cache report\n")
        registry["inputs"]["data"] = [
            {
                "path": target.relative_to(root).as_posix(),
                "sha256": sha256_file(target),
                "size_bytes": target.stat().st_size,
            }
        ]
        _write_json(registry_path, registry)
    elif target_kind == "registry_artifact":
        target = root / registry["artifacts"][0]["path"]
    elif target_kind == "prediction":
        relative = base_report["runs"][0]["phone"]["predictions"]["path"]
        target = synthetic_workspace["base_plus"]["report_path"].parent / relative
    else:
        target = synthetic_workspace["base_plus"]["report_path"]
    before = target.read_bytes() if target.exists() else None

    config = yaml.safe_load(
        synthetic_workspace["config_path"].read_text(encoding="utf-8")
    )
    config["output"]["commit_manifest"] = target.relative_to(root).as_posix()
    synthetic_workspace["config_path"].write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(
        ComparisonContractError,
        match="comparison output would overwrite an input",
    ):
        _compare(synthetic_workspace)

    if before is None:
        assert not target.exists()
    else:
        assert target.read_bytes() == before


@pytest.mark.parametrize("filename", ["report.json", "execution_receipt.json"])
def test_comparison_rejects_generation_file_collision_before_write(
    tmp_path: Path,
    filename: str,
) -> None:
    report = {
        "metrics": {},
        "primary_comparison": {},
        "multiplicity": {},
        "nonce": 1,
    }
    report_bytes = comparison_module._canonical_json_bytes(report, pretty=True)
    generation_id = hashlib.sha256(report_bytes).hexdigest()
    generation_directory = tmp_path / "generations"
    protected = generation_directory / generation_id / filename

    with pytest.raises(
        ComparisonContractError,
        match="comparison output would overwrite an input",
    ):
        comparison_module._publish_comparison_generation(
            workspace_root=tmp_path,
            generation_directory=generation_directory,
            commit_path=tmp_path / "commit.json",
            report=report,
            sources={},
            protected_paths={protected},
        )

    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("filename", ["report.json", "execution_receipt.json"])
def test_comparison_rejects_commit_generation_self_collision_before_write(
    tmp_path: Path,
    filename: str,
) -> None:
    report = {
        "metrics": {},
        "primary_comparison": {},
        "multiplicity": {},
        "nonce": 1,
    }
    report_bytes = comparison_module._canonical_json_bytes(report, pretty=True)
    generation_id = hashlib.sha256(report_bytes).hexdigest()
    generation_directory = tmp_path / "generations"
    commit_path = generation_directory / generation_id / filename

    with pytest.raises(
        ComparisonContractError,
        match="comparison output paths collide",
    ):
        comparison_module._publish_comparison_generation(
            workspace_root=tmp_path,
            generation_directory=generation_directory,
            commit_path=commit_path,
            report=report,
            sources={},
        )

    assert not list(tmp_path.rglob("*.json"))


def _minimal_comparison_report(nonce: int) -> dict[str, Any]:
    return {
        "metrics": {"nonce": nonce},
        "primary_comparison": {"nonce": nonce},
        "multiplicity": {"nonce": nonce},
    }


def _assert_comparison_commit_references_exist(root: Path, commit_path: Path) -> None:
    commit = json.loads(commit_path.read_text(encoding="utf-8"))
    for name in ("comparison_report", "execution_receipt"):
        record = commit[name]
        path = root / record["path"]
        assert path.is_file()
        assert sha256_file(path) == record["sha256"]
        assert path.stat().st_size == record["size_bytes"]


@pytest.mark.parametrize("file_fsync_index", [1, 2, 3])
def test_comparison_file_fsync_failure_preserves_previous_commit_without_orphans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    file_fsync_index: int,
) -> None:
    generation_directory = tmp_path / "generations"
    commit_path = tmp_path / "commit.json"
    comparison_module._publish_comparison_generation(
        workspace_root=tmp_path,
        generation_directory=generation_directory,
        commit_path=commit_path,
        report=_minimal_comparison_report(0),
        sources={},
    )
    previous_commit = commit_path.read_bytes()
    previous_files = {
        path.relative_to(tmp_path)
        for path in generation_directory.rglob("*")
        if path.is_file()
    }
    real_fsync = comparison_module.os.fsync
    calls = 0

    def fail_selected_file_fsync(descriptor: int) -> None:
        nonlocal calls
        if not stat.S_ISDIR(comparison_module.os.fstat(descriptor).st_mode):
            calls += 1
            if calls == file_fsync_index:
                raise OSError("injected file fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(comparison_module.os, "fsync", fail_selected_file_fsync)

    with pytest.raises(OSError, match="injected file fsync failure"):
        comparison_module._publish_comparison_generation(
            workspace_root=tmp_path,
            generation_directory=generation_directory,
            commit_path=commit_path,
            report=_minimal_comparison_report(1),
            sources={},
        )

    assert commit_path.read_bytes() == previous_commit
    assert {
        path.relative_to(tmp_path)
        for path in generation_directory.rglob("*")
        if path.is_file()
    } == previous_files
    assert not list(tmp_path.rglob("*.part-*"))
    _assert_comparison_commit_references_exist(tmp_path, commit_path)


@pytest.mark.parametrize("directory_fsync_index", [1, 2, 3])
def test_comparison_directory_fsync_failure_has_consistent_commit_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    directory_fsync_index: int,
) -> None:
    generation_directory = tmp_path / "generations"
    commit_path = tmp_path / "commit.json"
    comparison_module._publish_comparison_generation(
        workspace_root=tmp_path,
        generation_directory=generation_directory,
        commit_path=commit_path,
        report=_minimal_comparison_report(0),
        sources={},
    )
    previous_commit = commit_path.read_bytes()
    previous_files = {
        path.relative_to(tmp_path)
        for path in generation_directory.rglob("*")
        if path.is_file()
    }
    real_fsync_directory = comparison_module._fsync_directory
    calls = 0

    def fail_selected_directory_fsync(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == directory_fsync_index:
            raise OSError("injected directory fsync failure")
        real_fsync_directory(path)

    monkeypatch.setattr(
        comparison_module,
        "_fsync_directory",
        fail_selected_directory_fsync,
    )

    with pytest.raises(OSError, match="injected directory fsync failure"):
        comparison_module._publish_comparison_generation(
            workspace_root=tmp_path,
            generation_directory=generation_directory,
            commit_path=commit_path,
            report=_minimal_comparison_report(1),
            sources={},
        )

    if directory_fsync_index < 3:
        assert commit_path.read_bytes() == previous_commit
        assert {
            path.relative_to(tmp_path)
            for path in generation_directory.rglob("*")
            if path.is_file()
        } == previous_files
    else:
        assert commit_path.read_bytes() != previous_commit
        assert previous_files.issubset(
            {
                path.relative_to(tmp_path)
                for path in generation_directory.rglob("*")
                if path.is_file()
            }
        )
    assert not list(tmp_path.rglob("*.part-*"))
    _assert_comparison_commit_references_exist(tmp_path, commit_path)


@pytest.mark.parametrize("failure_target", ["receipt", "commit"])
def test_comparison_publish_failure_cleans_uncommitted_generation(
    synthetic_workspace: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    failure_target: str,
) -> None:
    real_replace = comparison_module.os.replace
    commit_path = synthetic_workspace["root"] / "artifacts/comparison/commit.json"

    def failing_replace(source: str | Path, destination: str | Path) -> None:
        target = Path(destination)
        should_fail = (
            target.name == "execution_receipt.json"
            if failure_target == "receipt"
            else target == commit_path
        )
        if should_fail:
            raise OSError("injected publication failure")
        real_replace(source, destination)

    monkeypatch.setattr(comparison_module.os, "replace", failing_replace)

    with pytest.raises(OSError, match="injected publication failure"):
        _compare(synthetic_workspace)

    assert not commit_path.exists()
    generation_root = synthetic_workspace["root"] / "artifacts/comparison/generations"
    assert not [path for path in generation_root.rglob("*") if path.is_file()]
    assert not list(synthetic_workspace["root"].rglob("*.part-*"))


def test_event_matching_tie_is_one_to_one() -> None:
    identity = comparison_module.PhoneIdentity(
        utterance_ids=np.asarray([_hash("u")] * 3),
        speaker_ids=np.asarray([_hash("s")] * 3),
        phone_indices=np.asarray([0, 1, 2]),
        target_labels=np.asarray(["AA", "B", "C"]),
        targets=np.asarray([1, 0, 1]),
    )

    counts = phone_event_counts(identity, np.asarray([1, 1, 1]))

    assert counts == {"tp": 1, "fp": 0, "fn": 1}


def test_gate_tie_passes_inclusive_threshold() -> None:
    assert large_adoption_gate(0.03) == (True, "passed")
    assert large_adoption_gate(np.nextafter(0.03, 0.0)) == (False, "failed")


def test_paired_ci_is_deterministic() -> None:
    identity = _phone_arrays()
    base = {seed: _phone_predictions("base_plus", seed) for seed in SEEDS}
    large = {seed: _phone_predictions("large", seed) for seed in SEEDS}

    def run() -> comparison_module.BootstrapResult:
        return paired_phone_macro_f1_bootstrap(
            speaker_ids=identity["speaker_ids"],
            targets=identity["targets"],
            base_predictions=base,
            large_predictions=large,
            n_resamples=5000,
            confidence_level=0.95,
            seed=42,
        )

    first = run()
    second = run()

    assert first == second
    assert first.n_valid_resamples == 5000
    assert first.confidence_lower <= first.confidence_upper
