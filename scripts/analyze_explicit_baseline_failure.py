#!/usr/bin/env python3
"""Create a privacy-safe aggregate failure analysis for the explicit baseline."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4

import numpy as np
import pyarrow.parquet as pq


SCHEMA_VERSION = "voice_coach.explicit_baseline_failure_analysis.v1"
QUANTILES = (0.0, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 1.0)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_number(value: float) -> float | None:
    return float(value) if math.isfinite(float(value)) else None


def _distribution(values: np.ndarray) -> dict[str, Any]:
    finite = np.asarray(values, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return {
            "count": 0,
            "mean": None,
            "sample_std": None,
            "quantiles": {},
        }
    quantiles = np.quantile(finite, QUANTILES)
    return {
        "count": int(finite.size),
        "mean": float(finite.mean()),
        "sample_std": (
            float(finite.std(ddof=1)) if finite.size > 1 else None
        ),
        "quantiles": {
            f"{quantile:.2f}": float(value)
            for quantile, value in zip(QUANTILES, quantiles, strict=True)
        },
    }


def _class_metrics(targets: np.ndarray, predictions: np.ndarray) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for label in (0, 1):
        true_positive = int(np.sum((targets == label) & (predictions == label)))
        false_positive = int(np.sum((targets != label) & (predictions == label)))
        false_negative = int(np.sum((targets == label) & (predictions != label)))
        support = int(np.sum(targets == label))
        precision = (
            true_positive / (true_positive + false_positive)
            if true_positive + false_positive
            else float("nan")
        )
        recall = true_positive / support if support else float("nan")
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else float("nan")
        )
        result[str(label)] = {
            "support": support,
            "predicted": int(np.sum(predictions == label)),
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "precision": _safe_number(precision),
            "recall": _safe_number(recall),
            "f1": _safe_number(f1),
        }
    return result


def _class_metrics_from_confusion(matrix: Sequence[Sequence[int]]) -> dict[str, Any]:
    values = np.asarray(matrix, dtype=np.int64)
    if values.shape != (2, 2):
        raise ValueError("binary confusion matrix must have shape [2, 2]")
    targets = np.concatenate(
        [
            np.full(int(values[label].sum()), label, dtype=np.int64)
            for label in (0, 1)
        ]
    )
    predictions = np.concatenate(
        [
            np.concatenate(
                [
                    np.full(int(values[true_label, pred_label]), pred_label)
                    for pred_label in (0, 1)
                ]
            )
            for true_label in (0, 1)
        ]
    )
    return _class_metrics(targets, predictions)


def _slice_summary(
    slice_document: Mapping[str, Any],
    *,
    min_group_n: int,
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for slice_name in ("l1", "gender", "proficiency", "target_phone"):
        document = slice_document.get(slice_name)
        if not isinstance(document, Mapping) or document.get("status") != "computed":
            output[slice_name] = {
                "status": "unavailable",
                "reason": (
                    document.get("reason")
                    if isinstance(document, Mapping)
                    else "missing"
                ),
            }
            continue
        groups = document.get("groups", [])
        eligible = [
            group
            for group in groups
            if isinstance(group, Mapping)
            and int(group.get("n_samples", 0)) >= min_group_n
            and group.get("macro_f1") is not None
        ]
        eligible.sort(key=lambda group: (float(group["macro_f1"]), str(group["value"])))
        output[slice_name] = {
            "status": "computed",
            "minimum_group_size": min_group_n,
            "eligible_group_count": len(eligible),
            "suppressed_small_group_count": len(groups) - len(eligible),
            "worst_groups": eligible[: min(5, len(eligible))],
            "best_group": eligible[-1] if eligible else None,
            "macro_f1_gap": (
                float(eligible[-1]["macro_f1"])
                - float(eligible[0]["macro_f1"])
                if eligible
                else None
            ),
        }
    return output


def _markdown(document: Mapping[str, Any]) -> str:
    lines = [
        "# Explicit baseline failure analysis",
        "",
        "이 문서는 사전 고정된 explicit baseline의 B06 실패를 설명하는 "
        "privacy-safe 집계 분석이다. 기존 F1 결과를 대체하거나 성공으로 "
        "재해석하지 않는다.",
        "",
        f"- B06 status: **{document['b06_status']}**",
        f"- Development-only: `{str(document['development_only']).lower()}`",
        f"- Oracle alignment: `{str(document['oracle_alignment']).lower()}`",
        f"- Minimum reported slice size: {document['minimum_slice_n']:,}",
        "",
    ]
    for seed in document["seeds"]:
        lines.extend(
            [
                f"## Seed {seed['seed']}",
                "",
                f"- Macro F1: {seed['phone']['macro_f1']:.6f}",
                f"- AUPRC: {seed['phone']['auprc']:.6f}",
                f"- Selected C: {seed['phone']['selected_c']}",
                "",
                "| class | support | precision | recall | F1 |",
                "|---:|---:|---:|---:|---:|",
            ]
        )
        for label, metric in seed["phone"][
            "class_metrics_primary_dev"
        ].items():
            lines.append(
                f"| {label} | {metric['support']:,} | "
                f"{metric['precision']:.4f} | {metric['recall']:.4f} | "
                f"{metric['f1']:.4f} |"
            )
        lines.extend(["", "### Worst slices", ""])
        for slice_name, value in seed["phone"]["slices"].items():
            if value["status"] != "computed" or not value["worst_groups"]:
                lines.append(f"- {slice_name}: unavailable")
                continue
            worst = value["worst_groups"][0]
            lines.append(
                f"- {slice_name}: `{worst['value']}` "
                f"(n={worst['n_samples']:,}, F1={worst['macro_f1']:.4f}, "
                f"AUPRC={worst['auprc']:.4f})"
            )
        lines.append("")
    lines.extend(
        [
            "## Interpretation",
            "",
            "- Balanced logistic regression substantially over-issued positive "
            "predictions at the fixed 0.5 threshold.",
            "- Ranking information remains above the positive prevalence, but "
            "the predeclared macro-F1 smoke and engineering gates both failed.",
            "- Any later threshold-selection experiment is post-hoc exploratory "
            "and cannot overwrite this failure.",
            "",
        ]
    )
    return "\n".join(lines)


def analyze(
    *,
    experiment_dir: Path,
    output_dir: Path,
    min_group_n: int = 100,
) -> dict[str, Any]:
    if min_group_n < 1:
        raise ValueError("min_group_n must be positive")
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    report_path = experiment_dir / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "completed":
        raise ValueError("baseline report is not completed")
    if report.get("development_only") is not True:
        raise ValueError("failure analysis accepts only development-only reports")
    leakage = report.get("leakage_controls", {})
    if any(
        leakage.get(key) is not False
        for key in (
            "locked_test_accessed",
            "stack_accessed",
            "calibration_accessed",
        )
    ):
        raise ValueError("baseline report accessed a forbidden split")
    phone_gate = report["summary"]["phone"]
    if phone_gate["smoke_gate_passed_by_mean"] is not False:
        raise ValueError("this analyzer is reserved for the failed B06 baseline")

    temporary = output_dir.with_name(f".{output_dir.name}.part-{uuid4().hex}")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.mkdir()
    try:
        document: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "analysis_type": "privacy_safe_aggregate_failure_analysis",
            "b06_status": "failed_unchanged",
            "post_hoc_threshold_ablation_may_not_overwrite_b06": True,
            "development_only": True,
            "oracle_alignment": True,
            "minimum_slice_n": min_group_n,
            "privacy": {
                "identifier_columns_read": False,
                "raw_paths_read": False,
                "only_prediction_labels_scores_and_existing_aggregate_slices": True,
                "small_slice_suppression_enabled": True,
            },
            "sources": {
                "baseline_report_sha256": sha256_file(report_path),
            },
            "seeds": [],
        }
        for run in report["runs"]:
            seed = int(run["seed"])
            seed_dir = experiment_dir / f"seed_{seed}"
            phone_path = seed_dir / "phone_dev_predictions.parquet"
            phone_table = pq.read_table(
                phone_path,
                columns=["y_true", "y_pred", "error_probability"],
            )
            targets = np.asarray(
                phone_table.column("y_true").to_numpy(), dtype=np.int64
            )
            predictions = np.asarray(
                phone_table.column("y_pred").to_numpy(), dtype=np.int64
            )
            probabilities = np.asarray(
                phone_table.column("error_probability").to_numpy(),
                dtype=np.float64,
            )
            utterance_path = seed_dir / "utterance_dev_predictions.parquet"
            utterance_columns = []
            for outcome in (
                "pronunciation_score",
                "fluency_score",
                "comprehensibility_score",
            ):
                utterance_columns.extend(
                    [
                        f"{outcome}_true",
                        f"{outcome}_pred",
                        f"{outcome}_observed",
                    ]
                )
            utterance_table = pq.read_table(
                utterance_path,
                columns=utterance_columns,
            )
            score_distributions: dict[str, Any] = {}
            for outcome in (
                "pronunciation_score",
                "fluency_score",
                "comprehensibility_score",
            ):
                observed = np.asarray(
                    utterance_table.column(f"{outcome}_observed").to_numpy(),
                    dtype=np.bool_,
                )
                truth = np.asarray(
                    utterance_table.column(f"{outcome}_true").to_numpy(),
                    dtype=np.float64,
                )
                predicted = np.asarray(
                    utterance_table.column(f"{outcome}_pred").to_numpy(),
                    dtype=np.float64,
                )
                score_distributions[outcome] = {
                    "observed_count": int(observed.sum()),
                    "missing_truth_count": int((~observed).sum()),
                    "truth": _distribution(truth[observed]),
                    "prediction_for_observed": _distribution(predicted[observed]),
                    "prediction_all_dev": _distribution(predicted),
                }
            slice_path = seed_dir / "slice_metrics.json"
            slices = json.loads(slice_path.read_text(encoding="utf-8"))["phone"]
            seed_result = {
                "seed": seed,
                "phone": {
                    "macro_f1": run["phone"]["metrics"]["macro_f1"],
                    "auprc": run["phone"]["metrics"]["auprc"],
                    "primary_metric_scope": "primary_dev",
                    "selected_c": run["phone"]["selected_c"],
                    "c_candidates": run["phone"]["candidates"],
                    "confusion_matrix": run["phone"]["confusion_matrix"],
                    "class_metrics_primary_dev": _class_metrics_from_confusion(
                        run["phone"]["confusion_matrix"]
                    ),
                    "class_metrics_all_dev_audit": _class_metrics(
                        targets, predictions
                    ),
                    "all_dev_positive_prevalence": float(
                        np.mean(targets == 1)
                    ),
                    "all_dev_predicted_positive_rate": float(
                        np.mean(predictions == 1)
                    ),
                    "all_dev_error_probability": _distribution(probabilities),
                    "slice_metric_scope": "all_dev_audit",
                    "slices": _slice_summary(
                        slices,
                        min_group_n=min_group_n,
                    ),
                },
                "utterance_score_distributions": score_distributions,
                "source_hashes": {
                    "phone_predictions_sha256": sha256_file(phone_path),
                    "utterance_predictions_sha256": sha256_file(utterance_path),
                    "slice_metrics_sha256": sha256_file(slice_path),
                },
            }
            document["seeds"].append(seed_result)

        json_path = temporary / "failure_analysis.json"
        markdown_path = temporary / "failure_analysis.md"
        json_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False)
            + "\n",
            encoding="utf-8",
        )
        markdown_path.write_text(_markdown(document), encoding="utf-8")
        document["outputs"] = {
            "markdown_sha256": sha256_file(markdown_path),
        }
        json_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False)
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output_dir)
        return document
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--min-group-n", type=int, default=100)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    document = analyze(
        experiment_dir=args.experiment_dir,
        output_dir=args.output_dir,
        min_group_n=args.min_group_n,
    )
    print(
        json.dumps(
            {
                "b06_status": document["b06_status"],
                "output_dir": str(args.output_dir),
                "seeds": [item["seed"] for item in document["seeds"]],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
