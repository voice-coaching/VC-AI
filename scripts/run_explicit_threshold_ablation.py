#!/usr/bin/env python3
"""Post-hoc train-only threshold ablation for the failed explicit baseline.

The ablation preserves the six reviewed phone inputs and balanced logistic
regression.  It partitions primary-scope train speakers into inner-train and
inner-validation groups by a deterministic hash, chooses C and threshold only
on inner-validation, refits C on all primary train rows, then makes one
all-dev prediction pass.  The primary dev subset is evaluated once; all-dev
is reported separately as an audit.  This exploratory result never overwrites
the predeclared B06 failure.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
from typing import Any
from uuid import uuid4

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from sklearn.metrics import confusion_matrix


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from train_explicit_baseline import (  # noqa: E402
    FORBIDDEN_SPLITS,
    PHONE_CATEGORICAL_FEATURES,
    PHONE_NUMERIC_FEATURES,
    REPOSITORY_ROOT,
    _as_ints,
    _as_strings,
    _feature_matrix,
    _primary_scope_mask,
    _registry_metric_value,
    load_config,
    load_feature_caches,
    macro_f1,
    auprc,
    make_phone_pipeline,
    phone_slice_metrics,
    sha256_file,
    utc_now,
    validate_cache_contract,
    write_json,
    write_pickle,
)
from voice_coach.experiments import experiment_run  # noqa: E402


REPORT_SCHEMA_VERSION = "voice_coach.explicit_threshold_ablation_report.v1"
CHECKPOINT_SCHEMA_VERSION = (
    "voice_coach.explicit_threshold_ablation_checkpoint.v1"
)
PREDICTION_SCHEMA_VERSION = (
    "voice_coach.explicit_threshold_ablation_predictions.v1"
)


def validate_ablation_config(config: Mapping[str, Any]) -> None:
    experiment = config.get("experiment")
    data = config.get("data")
    features = config.get("features")
    model = config.get("model")
    evaluation = config.get("evaluation")
    if not all(
        isinstance(item, Mapping)
        for item in (experiment, data, features, model, evaluation)
    ):
        raise ValueError("ablation config is missing required mappings")
    if list(experiment.get("seeds", [])) != [42, 43, 44]:
        raise ValueError("ablation seeds must be exactly [42, 43, 44]")
    if experiment.get("status") != "post_hoc_exploratory_b06_remains_failed":
        raise ValueError("ablation must be labeled post-hoc exploratory")
    if set(data.get("forbidden_splits", [])) != FORBIDDEN_SPLITS:
        raise ValueError("stack, calibration, and locked_test must be forbidden")
    if data.get("primary_analysis_scope") != {
        "l1": ["Chinese", "Japanese"],
        "gender": ["F", "M"],
    }:
        raise ValueError("primary scope must be Chinese/Japanese and F/M")
    phone_features = features.get("phone")
    if not isinstance(phone_features, Mapping):
        raise ValueError("phone feature mapping is missing")
    if tuple(phone_features.get("categorical", [])) != PHONE_CATEGORICAL_FEATURES:
        raise ValueError("categorical feature allowlist differs from baseline")
    if tuple(phone_features.get("numeric", [])) != PHONE_NUMERIC_FEATURES:
        raise ValueError("numeric feature allowlist differs from baseline")
    if model.get("family") != "logistic_regression":
        raise ValueError("ablation must retain logistic regression")
    if model.get("class_weight") != "balanced":
        raise ValueError("ablation must retain balanced class weights")
    if not model.get("c_grid"):
        raise ValueError("C grid must not be empty")
    fraction = float(model.get("inner_validation_speaker_fraction", 0.0))
    if not 0.0 < fraction < 0.5:
        raise ValueError("inner validation speaker fraction must lie in (0, .5)")
    if model.get("refit_selected_c_on_full_primary_train") is not True:
        raise ValueError("selected C must be refit on full primary train")
    if model.get("transfer_inner_validation_threshold_after_refit") is not True:
        raise ValueError("inner-validation threshold transfer must be explicit")
    if evaluation.get("primary_dev_evaluation_passes") != 1:
        raise ValueError("primary dev evaluation passes must equal one")
    if evaluation.get("post_hoc_exploratory") is not True:
        raise ValueError("evaluation must be marked post-hoc")
    if evaluation.get("promotion_decision_allowed") is not False:
        raise ValueError("post-hoc ablation cannot permit promotion")
    if evaluation.get("b06_status_must_remain") != "failed":
        raise ValueError("B06 status must remain failed")


def threshold_grid(config: Mapping[str, Any]) -> np.ndarray:
    document = config["model"]["threshold_grid"]
    start = float(document["start"])
    stop = float(document["stop"])
    step = float(document["step"])
    if not 0.0 < start < stop < 1.0 or step <= 0:
        raise ValueError("invalid threshold grid")
    steps = int(round((stop - start) / step))
    values = start + np.arange(steps + 1, dtype=np.float64) * step
    if not np.isclose(values[-1], stop):
        raise ValueError("threshold grid does not land on stop")
    return values


def _speaker_inner_validation(
    speaker_ids: np.ndarray,
    *,
    seed: int,
    fraction: float,
    namespace: str,
) -> np.ndarray:
    speakers = list(dict.fromkeys(speaker_ids.tolist()))
    if len(speakers) < 2:
        raise ValueError("inner split needs at least two train speakers")
    ranked: list[tuple[int, str]] = []
    for speaker in speakers:
        payload = f"{namespace}:{seed}:{speaker}".encode("utf-8")
        integer = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
        ranked.append((integer, speaker))
    ranked.sort()
    validation_count = min(
        len(speakers) - 1,
        max(1, round(len(speakers) * fraction)),
    )
    validation_speakers = {
        speaker for _, speaker in ranked[:validation_count]
    }
    return np.asarray(
        [speaker in validation_speakers for speaker in speaker_ids],
        dtype=np.bool_,
    )


def _binary_metrics(
    targets: np.ndarray,
    predictions: np.ndarray,
    probabilities: np.ndarray,
) -> dict[str, float]:
    return {
        "macro_f1": macro_f1(targets, predictions, labels=[0, 1]),
        "auprc": auprc(
            targets,
            probabilities,
            labels=[0, 1],
            undefined="nan",
        ),
    }


def _fast_macro_f1(targets: np.ndarray, predictions: np.ndarray) -> float:
    positive = targets == 1
    predicted_positive = predictions
    tp = int(np.sum(positive & predicted_positive))
    fp = int(np.sum(~positive & predicted_positive))
    fn = int(np.sum(positive & ~predicted_positive))
    tn = len(targets) - tp - fp - fn
    positive_denominator = 2 * tp + fp + fn
    negative_denominator = 2 * tn + fp + fn
    positive_f1 = 2 * tp / positive_denominator if positive_denominator else 0.0
    negative_f1 = 2 * tn / negative_denominator if negative_denominator else 0.0
    return (positive_f1 + negative_f1) / 2.0


def _best_threshold(
    targets: np.ndarray,
    probabilities: np.ndarray,
    grid: np.ndarray,
) -> tuple[float, float]:
    best_threshold = float(grid[0])
    best_f1 = -math.inf
    best_distance = math.inf
    for raw_threshold in grid:
        threshold = float(raw_threshold)
        score = _fast_macro_f1(targets, probabilities >= threshold)
        distance = abs(threshold - 0.5)
        if score > best_f1 or (
            np.isclose(score, best_f1) and distance < best_distance
        ):
            best_f1 = score
            best_threshold = threshold
            best_distance = distance
    return best_threshold, float(best_f1)


def _validate_prior_failure(
    base_report_path: Path,
    failure_analysis_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    base = json.loads(base_report_path.read_text(encoding="utf-8"))
    failure = json.loads(failure_analysis_path.read_text(encoding="utf-8"))
    if base.get("status") != "completed" or base.get("development_only") is not True:
        raise ValueError("base report must be completed and development-only")
    if base["summary"]["phone"]["smoke_gate_passed_by_mean"] is not False:
        raise ValueError("base B06 failure is not present")
    if failure.get("b06_status") != "failed_unchanged":
        raise ValueError("failure analysis does not preserve B06 failure")
    expected_hash = failure["sources"]["baseline_report_sha256"]
    if expected_hash != sha256_file(base_report_path):
        raise ValueError("failure analysis belongs to a different base report")
    leakage = base["leakage_controls"]
    if any(
        leakage.get(key) is not False
        for key in (
            "locked_test_accessed",
            "stack_accessed",
            "calibration_accessed",
        )
    ):
        raise ValueError("base report accessed a forbidden split")
    return base, failure


def _save_predictions(
    path: Path,
    *,
    table: pa.Table,
    dev_mask: np.ndarray,
    primary_scope: np.ndarray,
    seed: int,
    targets: np.ndarray,
    predictions: np.ndarray,
    probabilities: np.ndarray,
) -> dict[str, Any]:
    selected_table = table.filter(pa.array(dev_mask))
    primary = primary_scope[dev_mask]
    row_count = len(selected_table)
    output = pa.table(
        {
            "schema_version": [PREDICTION_SCHEMA_VERSION] * row_count,
            "post_hoc_exploratory": [True] * row_count,
            "oracle_alignment": [True] * row_count,
            "development_only": [True] * row_count,
            "seed": np.full(row_count, seed, dtype=np.int32),
            "utterance_id_sha256": selected_table.column(
                "utterance_id_sha256"
            ),
            "speaker_id_sha256": selected_table.column("speaker_id_sha256"),
            "phone_index": selected_table.column("phone_index"),
            "split": ["dev"] * row_count,
            "primary_scope": primary,
            "target_label": selected_table.column("target_label"),
            "y_true": targets.astype(np.int8),
            "y_pred": predictions.astype(np.int8),
            "error_probability": probabilities.astype(np.float64),
        }
    )
    output = output.replace_schema_metadata(
        {
            b"voice_coach.development_only": b"true",
            b"voice_coach.oracle_alignment": b"true",
            b"voice_coach.post_hoc_exploratory": b"true",
            b"voice_coach.b06_status": b"failed_unchanged",
        }
    )
    pq.write_table(output, path, compression="zstd")
    return {
        "path": path.name,
        "rows": output.num_rows,
        "primary_rows": int(primary.sum()),
        "sha256": sha256_file(path),
    }


def run_threshold_ablation(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(args.config)
    validate_ablation_config(config)
    if args.output_dir.exists():
        raise FileExistsError(f"output directory already exists: {args.output_dir}")
    base_report, _ = _validate_prior_failure(
        args.base_report,
        args.failure_analysis,
    )
    contract = validate_cache_contract(
        config=config,
        utterance_cache=args.utterance_cache,
        phone_cache=args.phone_cache,
        cache_report_path=args.cache_report,
    )
    _, phones = load_feature_caches(args.utterance_cache, args.phone_cache)
    primary_scope, outside_scope = _primary_scope_mask(phones, config)
    splits = _as_strings(phones, "split")
    train_primary = (splits == "train") & primary_scope
    dev_all = splits == "dev"
    dev_primary_in_all = primary_scope[dev_all]
    targets = _as_ints(phones, "binary_error_label")
    features = _feature_matrix(
        phones,
        categorical=PHONE_CATEGORICAL_FEATURES,
        numeric=PHONE_NUMERIC_FEATURES,
    )
    speakers = _as_strings(phones, "speaker_id_sha256")
    grid = threshold_grid(config)
    c_grid = [float(value) for value in config["model"]["c_grid"]]
    max_iter = int(config["model"]["max_iter"])
    fraction = float(config["model"]["inner_validation_speaker_fraction"])
    namespace = str(config["model"]["inner_split_hash_namespace"])

    temporary = args.output_dir.with_name(
        f".{args.output_dir.name}.part-{uuid4().hex}"
    )
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.mkdir()
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "running",
        "started_at_utc": utc_now(),
        "completed_at_utc": None,
        "experiment_id": config["experiment"]["id"],
        "post_hoc_exploratory": True,
        "b06_status": "failed_unchanged",
        "may_overwrite_predeclared_baseline": False,
        "promotion_decision_allowed": False,
        "development_only": True,
        "oracle_alignment": True,
        "forbidden_split_access": {
            "stack": False,
            "calibration": False,
            "locked_test": False,
        },
        "experiment_registry": {
            "integrated": False,
            "t03_complete": False,
            "reason": "use the registered CLI wrapper",
        },
        "isolation_contract": {
            "same_six_explicit_features": True,
            "same_balanced_logistic_family": True,
            "only_added_factor": (
                "speaker-disjoint inner validation selection of C and "
                "decision threshold"
            ),
            "c_and_threshold_selected_without_dev": True,
            "primary_dev_prediction_passes": 1,
            "all_dev_audit_from_same_prediction_pass": True,
            "refit_selected_c_on_full_primary_train": True,
            "threshold_transferred_after_refit": True,
        },
        "sources": {
            "config_sha256": sha256_file(args.config),
            "cache_report_sha256": sha256_file(args.cache_report),
            "utterance_cache_sha256": sha256_file(args.utterance_cache),
            "base_report_sha256": sha256_file(args.base_report),
            "failure_analysis_sha256": sha256_file(args.failure_analysis),
            "phone_shards": [
                {"path": path.name, "sha256": sha256_file(path)}
                for path in sorted(args.phone_cache.glob("part-*.parquet"))
            ],
        },
        "cache_contract": contract,
        "data": {
            "primary_scope": config["data"]["primary_analysis_scope"],
            "primary_train_rows": int(train_primary.sum()),
            "primary_train_speakers": len(set(speakers[train_primary].tolist())),
            "primary_dev_rows": int(dev_primary_in_all.sum()),
            "all_dev_audit_rows": int(dev_all.sum()),
            "outside_primary_scope_counts": outside_scope,
        },
        "threshold_grid": {
            "start": float(grid[0]),
            "stop": float(grid[-1]),
            "step": float(grid[1] - grid[0]),
            "count": int(len(grid)),
        },
        "base_failure": {
            "phone_macro_f1": base_report["summary"]["phone"]["macro_f1"]["mean"],
            "phone_auprc": base_report["summary"]["phone"]["auprc"]["mean"],
            "smoke_gate_passed": False,
            "engineering_gate_passed": False,
        },
        "runs": [],
    }
    try:
        for seed in config["experiment"]["seeds"]:
            seed = int(seed)
            seed_dir = temporary / f"seed_{seed}"
            seed_dir.mkdir()
            train_rows = np.flatnonzero(train_primary)
            train_speakers = speakers[train_primary]
            inner_val_local = _speaker_inner_validation(
                train_speakers,
                seed=seed,
                fraction=fraction,
                namespace=namespace,
            )
            inner_train_rows = train_rows[~inner_val_local]
            inner_val_rows = train_rows[inner_val_local]
            inner_train_speakers = set(speakers[inner_train_rows].tolist())
            inner_val_speakers = set(speakers[inner_val_rows].tolist())
            if inner_train_speakers & inner_val_speakers:
                raise ValueError("speaker leakage in inner split")
            for name, rows in (
                ("inner_train", inner_train_rows),
                ("inner_val", inner_val_rows),
            ):
                if len(rows) == 0 or set(np.unique(targets[rows]).tolist()) != {0, 1}:
                    raise ValueError(f"{name} lacks rows or a binary class")

            candidates: list[dict[str, Any]] = []
            for c_value in c_grid:
                model = make_phone_pipeline(
                    c_value=c_value,
                    max_iter=max_iter,
                    seed=seed,
                )
                model.fit(features[inner_train_rows], targets[inner_train_rows])
                probabilities = model.predict_proba(features[inner_val_rows])[:, 1]
                selected_threshold, selected_f1 = _best_threshold(
                    targets[inner_val_rows],
                    probabilities,
                    grid,
                )
                predictions = probabilities >= selected_threshold
                candidates.append(
                    {
                        "c": c_value,
                        "threshold": selected_threshold,
                        "inner_val_metrics": {
                            "macro_f1": selected_f1,
                            "auprc": auprc(
                                targets[inner_val_rows],
                                probabilities,
                                labels=[0, 1],
                                undefined="nan",
                            ),
                        },
                        "inner_val_confusion_matrix": confusion_matrix(
                            targets[inner_val_rows],
                            predictions,
                            labels=[0, 1],
                        ).tolist(),
                    }
                )
            selected = max(
                candidates,
                key=lambda item: (
                    item["inner_val_metrics"]["macro_f1"],
                    item["inner_val_metrics"]["auprc"],
                    -abs(item["threshold"] - 0.5),
                ),
            )
            final_model = make_phone_pipeline(
                c_value=float(selected["c"]),
                max_iter=max_iter,
                seed=seed,
            )
            final_model.fit(features[train_primary], targets[train_primary])

            # Exactly one prediction pass over all dev rows.  The primary
            # metric and all-dev audit are two views of this same output.
            all_dev_probabilities = final_model.predict_proba(
                features[dev_all]
            )[:, 1]
            all_dev_predictions = (
                all_dev_probabilities >= float(selected["threshold"])
            ).astype(np.int64)
            all_dev_targets = targets[dev_all]
            primary_targets = all_dev_targets[dev_primary_in_all]
            primary_predictions = all_dev_predictions[dev_primary_in_all]
            primary_probabilities = all_dev_probabilities[dev_primary_in_all]
            primary_metrics = _binary_metrics(
                primary_targets,
                primary_predictions,
                primary_probabilities,
            )
            all_dev_metrics = _binary_metrics(
                all_dev_targets,
                all_dev_predictions,
                all_dev_probabilities,
            )
            slices = phone_slice_metrics(
                phones,
                dev_all,
                all_dev_targets,
                all_dev_predictions,
                all_dev_probabilities,
                config["evaluation"]["slices"],
            )
            prediction_artifact = _save_predictions(
                seed_dir / "phone_dev_predictions.parquet",
                table=phones,
                dev_mask=dev_all,
                primary_scope=primary_scope,
                seed=seed,
                targets=all_dev_targets,
                predictions=all_dev_predictions,
                probabilities=all_dev_probabilities,
            )
            prediction_artifact["path"] = str(
                Path(f"seed_{seed}") / prediction_artifact["path"]
            )
            checkpoint = {
                "schema_version": CHECKPOINT_SCHEMA_VERSION,
                "post_hoc_exploratory": True,
                "b06_status": "failed_unchanged",
                "seed": seed,
                "features": list(
                    PHONE_CATEGORICAL_FEATURES + PHONE_NUMERIC_FEATURES
                ),
                "class_weight": "balanced",
                "selected_c": float(selected["c"]),
                "selected_inner_val_threshold": float(selected["threshold"]),
                "pipeline_refit_on_full_primary_train": final_model,
            }
            checkpoint_path = seed_dir / "checkpoint.pkl"
            write_pickle(checkpoint_path, checkpoint)
            slices_path = seed_dir / "slice_metrics.json"
            write_json(
                slices_path,
                {
                    "post_hoc_exploratory": True,
                    "b06_status": "failed_unchanged",
                    "scope": "all_dev_audit",
                    "seed": seed,
                    "phone": slices,
                },
            )
            report["runs"].append(
                {
                    "seed": seed,
                    "inner_split": {
                        "hash_namespace": namespace,
                        "target_validation_speaker_fraction": fraction,
                        "inner_train_rows": int(len(inner_train_rows)),
                        "inner_val_rows": int(len(inner_val_rows)),
                        "inner_train_speakers": len(inner_train_speakers),
                        "inner_val_speakers": len(inner_val_speakers),
                        "speaker_overlap": 0,
                    },
                    "selection": {
                        "selected_c": float(selected["c"]),
                        "selected_threshold": float(selected["threshold"]),
                        "selected_inner_val_metrics": selected[
                            "inner_val_metrics"
                        ],
                        "candidates": candidates,
                        "dev_was_used": False,
                    },
                    "primary_dev": {
                        "evaluation_passes": 1,
                        "rows": int(len(primary_targets)),
                        "metrics": primary_metrics,
                        "confusion_matrix": confusion_matrix(
                            primary_targets,
                            primary_predictions,
                            labels=[0, 1],
                        ).tolist(),
                    },
                    "all_dev_audit": {
                        "same_prediction_pass": True,
                        "rows": int(len(all_dev_targets)),
                        "metrics": all_dev_metrics,
                        "confusion_matrix": confusion_matrix(
                            all_dev_targets,
                            all_dev_predictions,
                            labels=[0, 1],
                        ).tolist(),
                    },
                    "artifacts": {
                        "checkpoint": {
                            "path": str(checkpoint_path.relative_to(temporary)),
                            "sha256": sha256_file(checkpoint_path),
                        },
                        "predictions": prediction_artifact,
                        "slice_metrics": {
                            "path": str(slices_path.relative_to(temporary)),
                            "sha256": sha256_file(slices_path),
                        },
                    },
                }
            )

        primary_f1 = np.asarray(
            [
                run["primary_dev"]["metrics"]["macro_f1"]
                for run in report["runs"]
            ],
            dtype=np.float64,
        )
        primary_auprc = np.asarray(
            [
                run["primary_dev"]["metrics"]["auprc"]
                for run in report["runs"]
            ],
            dtype=np.float64,
        )
        base_f1 = float(report["base_failure"]["phone_macro_f1"])
        report["summary"] = {
            "primary_dev_macro_f1": {
                "mean": float(primary_f1.mean()),
                "sample_std": float(primary_f1.std(ddof=1)),
            },
            "primary_dev_auprc": {
                "mean": float(primary_auprc.mean()),
                "sample_std": float(primary_auprc.std(ddof=1)),
            },
            "exploratory_macro_f1_delta_vs_predeclared_baseline": float(
                primary_f1.mean() - base_f1
            ),
            "post_hoc_exploratory": True,
            "b06_status": "failed_unchanged",
            "promotion_decision_allowed": False,
            "locked_test_remains_unread": True,
        }
        report["status"] = "completed"
        report["completed_at_utc"] = utc_now()
        write_json(temporary / "report.json", report)
        os.replace(temporary, args.output_dir)
        return report
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def run_registered_ablation(
    args: argparse.Namespace,
) -> tuple[dict[str, Any], Path]:
    config = load_config(args.config)
    validate_ablation_config(config)
    phone_shards = sorted(args.phone_cache.glob("part-*.parquet"))
    data_files = [
        args.utterance_cache,
        args.cache_report,
        *phone_shards,
        args.base_report,
        args.failure_analysis,
    ]
    registry = experiment_run(
        args.registry_root,
        run_id=args.run_id,
        config_files=args.config,
        data_files=data_files,
        seeds={
            f"experiment_seed_{seed}": int(seed)
            for seed in config["experiment"]["seeds"]
        },
        repo_path=REPOSITORY_ROOT,
    )
    with registry as active:
        report = run_threshold_ablation(args)
        try:
            metadata_path = active.metadata_path.resolve().relative_to(
                REPOSITORY_ROOT
            )
        except ValueError:
            metadata_path = active.metadata_path.resolve()
        report["experiment_registry"] = {
            "integrated": True,
            "t03_complete": False,
            "run_id": active.run_id,
            "metadata_path": metadata_path.as_posix(),
            "input_hashes_recorded": True,
            "metrics_recorded": False,
            "artifacts_recorded": False,
        }
        write_json(args.output_dir / "report.json", report)
        for run in report["runs"]:
            seed = int(run["seed"])
            active.log_metrics(
                {
                    "inner_val_macro_f1": _registry_metric_value(
                        run["selection"]["selected_inner_val_metrics"][
                            "macro_f1"
                        ]
                    ),
                    "selected_c": _registry_metric_value(
                        run["selection"]["selected_c"]
                    ),
                    "selected_threshold": _registry_metric_value(
                        run["selection"]["selected_threshold"]
                    ),
                },
                step=seed,
                split="inner_val",
            )
            active.log_metrics(
                {
                    "macro_f1": _registry_metric_value(
                        run["primary_dev"]["metrics"]["macro_f1"]
                    ),
                    "auprc": _registry_metric_value(
                        run["primary_dev"]["metrics"]["auprc"]
                    ),
                    "b06_remains_failed": True,
                    "promotion_allowed": False,
                },
                step=seed,
                split="dev_exploratory",
            )
            for name, artifact in run["artifacts"].items():
                active.add_artifact(
                    f"seed_{seed}_{name}",
                    args.output_dir / artifact["path"],
                    kind="checkpoint" if name == "checkpoint" else "evaluation",
                )
        active.log_metrics(
            {
                "macro_f1_mean": _registry_metric_value(
                    report["summary"]["primary_dev_macro_f1"]["mean"]
                ),
                "macro_f1_sample_std": _registry_metric_value(
                    report["summary"]["primary_dev_macro_f1"]["sample_std"]
                ),
                "exploratory_delta": _registry_metric_value(
                    report["summary"][
                        "exploratory_macro_f1_delta_vs_predeclared_baseline"
                    ]
                ),
                "b06_remains_failed": True,
                "promotion_allowed": False,
            },
            split="dev_exploratory",
        )
        report["experiment_registry"].update(
            {
                "t03_complete": True,
                "metrics_recorded": True,
                "artifacts_recorded": True,
                "registered_artifact_count": len(report["runs"]) * 3 + 1,
            }
        )
        write_json(args.output_dir / "report.json", report)
        active.add_artifact(
            "final_report",
            args.output_dir / "report.json",
            kind="evaluation_report",
        )
    return report, registry.metadata_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/experiments/explicit_threshold_ablation_v1.yaml"
        ),
    )
    parser.add_argument("--utterance-cache", type=Path, required=True)
    parser.add_argument("--phone-cache", type=Path, required=True)
    parser.add_argument("--cache-report", type=Path, required=True)
    parser.add_argument("--base-report", type=Path, required=True)
    parser.add_argument("--failure-analysis", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--registry-root",
        type=Path,
        default=Path("artifacts/experiment_registry"),
    )
    parser.add_argument("--run-id")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report, metadata_path = run_registered_ablation(args)
    print(
        json.dumps(
            {
                "status": report["status"],
                "post_hoc_exploratory": True,
                "b06_status": report["b06_status"],
                "macro_f1_mean": report["summary"][
                    "primary_dev_macro_f1"
                ]["mean"],
                "output_dir": str(args.output_dir),
                "registry_metadata": str(metadata_path),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
