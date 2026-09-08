#!/usr/bin/env python3
"""Deep-dive on why error_presence=true items fail the manual alignment
audit far more often than error_presence=false items (59.55% vs 5.96%
in the first 240-item sample).

The stored automatic_phone_alignments only record the winning decision,
not near-miss candidates that fell below the align_intervals()
thresholds (tolerance_seconds=0.03, min_overlap_ratio=0.5). To see
whether deletions/insertions in error_presence=true items are a
tunable-parameter artifact or an inherent alignment-difficulty limit,
this recomputes align_intervals() directly from the stored
target/realized phone intervals under the default parameters and under
a deliberately looser candidate, and reports how many
deletions/insertions resolve into a match/substitution under looser
parameters -- separately for judged-correct and judged-incorrect
error_presence=true items.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from voice_coach.data.intervals import Interval, align_intervals  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worksheet", type=Path, required=True)
    parser.add_argument("--details", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def load_details(path: Path) -> dict[int, dict[str, Any]]:
    details: dict[int, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            details[int(row["audit_index"])] = row
    return details


def to_intervals(entries: list[dict[str, Any]]) -> list[Interval]:
    return [Interval(start=e["start"], end=e["end"], label=e["label"]) for e in entries]


def non_match_counts(alignments: list[Any]) -> dict[str, int]:
    counts = {"match": 0, "substitution": 0, "deletion": 0, "insertion": 0}
    for alignment in alignments:
        counts[alignment.operation] += 1
    return counts


PARAMETER_SETS = {
    "default": {"tolerance_seconds": 0.03, "min_overlap_ratio": 0.5},
    "looser": {"tolerance_seconds": 0.05, "min_overlap_ratio": 0.35},
    "much_looser": {"tolerance_seconds": 0.08, "min_overlap_ratio": 0.2},
}


def main() -> int:
    args = parse_args()
    with args.worksheet.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    details = load_details(args.details)

    groups: dict[str, list[dict[str, Any]]] = {
        "error_presence_true_correct": [],
        "error_presence_true_incorrect": [],
    }
    for row in rows:
        if row["error_presence"] != "true":
            continue
        key = (
            "error_presence_true_correct"
            if row["alignment_correct"] == "true"
            else "error_presence_true_incorrect"
        )
        groups[key].append(row)

    report: dict[str, Any] = {
        "schema_version": "voice_coach.error_presence_failure_pattern.v2",
        "parameter_sets": PARAMETER_SETS,
    }

    for label, group_rows in groups.items():
        per_parameter: dict[str, Any] = {}
        for param_label, params in PARAMETER_SETS.items():
            totals = {"match": 0, "substitution": 0, "deletion": 0, "insertion": 0}
            for row in group_rows:
                index = int(row["audit_index"])
                detail = details[index]
                alignments = align_intervals(
                    to_intervals(detail["target_phone_intervals"]),
                    to_intervals(detail["realized_phone_intervals"]),
                    tolerance_seconds=params["tolerance_seconds"],
                    min_overlap_ratio=params["min_overlap_ratio"],
                    preserve_one_to_many=False,
                )
                counts = non_match_counts(alignments)
                for key in totals:
                    totals[key] += counts[key]
            per_parameter[param_label] = totals
        report[label] = {
            "item_count": len(group_rows),
            "counts_by_parameter_set": per_parameter,
            "deletion_plus_insertion_reduction_vs_default": {
                param_label: (
                    per_parameter["default"]["deletion"]
                    + per_parameter["default"]["insertion"]
                    - per_parameter[param_label]["deletion"]
                    - per_parameter[param_label]["insertion"]
                )
                for param_label in PARAMETER_SETS
                if param_label != "default"
            },
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
