#!/usr/bin/env python3
"""Score a completed AI Hub manual alignment-audit TSV worksheet."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
from uuid import uuid4


TRUE_VALUES = {"1", "true", "yes", "y", "pass", "correct", "정상"}
FALSE_VALUES = {"0", "false", "no", "n", "fail", "incorrect", "오류"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("worksheet", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-samples", type=int, default=240)
    parser.add_argument("--minimum-accuracy", type=float, default=0.95)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    with args.worksheet.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    required_columns = {
        "audit_index",
        "utterance_id",
        "reviewer_id",
        "alignment_correct",
        "maximum_boundary_error_ms",
        "notes",
    }
    if rows and required_columns - set(rows[0]):
        raise ValueError(
            "worksheet is missing required review columns: "
            + ", ".join(sorted(required_columns - set(rows[0])))
        )
    ids = [row["utterance_id"] for row in rows]
    audit_indices = [row["audit_index"] for row in rows]
    invalid = []
    invalid_boundaries = []
    missing_reviewers = []
    decisions = []
    boundary_errors = []
    for row in rows:
        if not row["reviewer_id"].strip():
            missing_reviewers.append(row["audit_index"])
        value = row["alignment_correct"].strip().lower()
        if value in TRUE_VALUES:
            decisions.append(True)
        elif value in FALSE_VALUES:
            decisions.append(False)
        else:
            invalid.append(row["audit_index"])
        try:
            boundary = float(row["maximum_boundary_error_ms"])
            if not math.isfinite(boundary) or boundary < 0:
                raise ValueError
            duration_text = row.get("duration_seconds", "").strip()
            if duration_text and boundary > float(duration_text) * 1_000:
                raise ValueError
            boundary_errors.append(boundary)
        except (TypeError, ValueError):
            invalid_boundaries.append(row["audit_index"])

    failed_checks = []
    if len(rows) != args.expected_samples:
        failed_checks.append("sample_count")
    if len(ids) != len(set(ids)):
        failed_checks.append("duplicate_utterance_id")
    if len(audit_indices) != len(set(audit_indices)):
        failed_checks.append("duplicate_audit_index")
    if invalid:
        failed_checks.append("incomplete_or_invalid_decisions")
    if missing_reviewers:
        failed_checks.append("missing_reviewer_id")
    if invalid_boundaries:
        failed_checks.append("incomplete_or_invalid_boundary_error")
    accuracy = sum(decisions) / len(decisions) if decisions else 0.0
    if not invalid and accuracy < args.minimum_accuracy:
        failed_checks.append("alignment_accuracy")

    # Phone-level accuracy: unlike alignment_accuracy above (utterance
    # pass/fail), this is not skewed by phone count per utterance, since it
    # counts wrong phone intervals rather than "did every phone pass".
    # Only present in worksheets exported with incorrect_interval_count
    # (scripts/review_alignment_audit.py export from a phone-level-aware
    # review); absent from legacy utterance-only worksheets.
    has_phone_level_columns = bool(rows) and {
        "incorrect_interval_count",
        "total_alignment_interval_count",
    } <= set(rows[0])
    phone_level_accuracy = None
    total_phone_intervals = 0
    total_incorrect_phone_intervals = 0
    invalid_phone_level_indices = []
    if has_phone_level_columns and not invalid:
        for row in rows:
            try:
                incorrect_count = int(row["incorrect_interval_count"])
                total_count = int(row["total_alignment_interval_count"])
                if incorrect_count < 0 or total_count < 0:
                    raise ValueError
                if incorrect_count > total_count:
                    raise ValueError
            except (TypeError, ValueError):
                invalid_phone_level_indices.append(row["audit_index"])
                continue
            total_phone_intervals += total_count
            total_incorrect_phone_intervals += incorrect_count
        if invalid_phone_level_indices:
            failed_checks.append("invalid_phone_level_counts")
        elif total_phone_intervals > 0:
            phone_level_accuracy = 1 - (
                total_incorrect_phone_intervals / total_phone_intervals
            )

    status = "passed" if not failed_checks else "failed"
    result = {
        "schema_version": "aihub71490.manual_alignment_audit.v1",
        "status": status,
        "worksheet_sha256": sha256_file(args.worksheet),
        "sample_count": len(rows),
        "valid_decision_count": len(decisions),
        "correct_count": sum(decisions),
        "alignment_accuracy": accuracy,
        "minimum_accuracy": args.minimum_accuracy,
        "phone_level_accuracy": {
            "value": phone_level_accuracy,
            "total_phone_intervals": total_phone_intervals,
            "total_incorrect_phone_intervals": total_incorrect_phone_intervals,
            "invalid_audit_indices": invalid_phone_level_indices,
        },
        "invalid_audit_indices": invalid,
        "missing_reviewer_indices": missing_reviewers,
        "invalid_boundary_error_indices": invalid_boundaries,
        "maximum_boundary_error_ms": {
            "maximum": max(boundary_errors) if boundary_errors else None,
        },
        "failed_checks": failed_checks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.part-{uuid4().hex}")
    try:
        temporary.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, args.output)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
