#!/usr/bin/env python3
"""Break down manual-alignment-audit failures by stratum and by the
automatic-alignment operation/phone patterns present in each failed
item, to inform a revision of the TextGrid target policy.

Reads the completed worksheet (real judgments joined by
scripts/apply_review_state_to_worksheet.py) and the alignment-detail
JSONL, and writes a JSON report plus a human-readable Markdown summary.
Never reads or emits raw utterance IDs or restricted paths.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worksheet", type=Path, required=True)
    parser.add_argument("--details", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
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


def stratum_breakdown(
    rows: list[dict[str, str]], key: str
) -> dict[str, dict[str, int]]:
    total = Counter(row[key] for row in rows)
    failed = Counter(row[key] for row in rows if row["alignment_correct"] == "false")
    return {
        value: {
            "total": total[value],
            "failed": failed.get(value, 0),
            "failure_rate": round(failed.get(value, 0) / total[value], 4),
        }
        for value in sorted(total)
    }


def main() -> int:
    args = parse_args()
    with args.worksheet.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    details = load_details(args.details)

    total = len(rows)
    failed_rows = [row for row in rows if row["alignment_correct"] == "false"]

    strata = {
        key: stratum_breakdown(rows, key)
        for key in ("split", "l1", "gender", "proficiency", "error_presence")
    }

    operation_counts_all = Counter()
    operation_counts_failed = Counter()
    phone_pair_counts_failed = Counter()
    boundary_errors = []
    notes_by_index = {}

    for row in rows:
        index = int(row["audit_index"])
        detail = details[index]
        is_failed = row["alignment_correct"] == "false"
        for alignment in detail["automatic_phone_alignments"]:
            operation_counts_all[alignment["operation"]] += 1
            if is_failed:
                operation_counts_failed[alignment["operation"]] += 1
                if alignment["operation"] != "match":
                    phone_pair_counts_failed[
                        (
                            alignment["operation"],
                            alignment.get("target_label"),
                            alignment.get("realized_label"),
                        )
                    ] += 1
        if is_failed:
            boundary_errors.append(float(row["maximum_boundary_error_ms"]))
            note = row["notes"].strip()
            if note:
                notes_by_index[index] = note

    operation_failure_rate = {
        operation: round(
            operation_counts_failed.get(operation, 0) / operation_counts_all[operation],
            4,
        )
        for operation in operation_counts_all
    }

    all_match_failures = sum(
        1
        for row in failed_rows
        if all(
            alignment["operation"] == "match"
            for alignment in details[int(row["audit_index"])][
                "automatic_phone_alignments"
            ]
        )
    )

    report = {
        "schema_version": "voice_coach.alignment_audit_failure_analysis.v1",
        "sample_count": total,
        "failed_count": len(failed_rows),
        "overall_accuracy": round(1 - len(failed_rows) / total, 4),
        "strata": strata,
        "operation_counts_all_items": dict(operation_counts_all),
        "operation_counts_failed_items": dict(operation_counts_failed),
        "operation_failure_rate": operation_failure_rate,
        "top_phone_pairs_in_failed_items": [
            {
                "operation": operation,
                "target_label": target,
                "realized_label": realized,
                "count": count,
            }
            for (
                operation,
                target,
                realized,
            ), count in phone_pair_counts_failed.most_common(30)
        ],
        "failed_items_with_only_match_operations": all_match_failures,
        "failed_items_with_non_match_operations": len(failed_rows) - all_match_failures,
        "maximum_boundary_error_ms_of_failed": {
            "median": (
                sorted(boundary_errors)[len(boundary_errors) // 2]
                if boundary_errors
                else None
            ),
            "max": max(boundary_errors) if boundary_errors else None,
            "nonzero_count": sum(1 for value in boundary_errors if value > 0),
        },
        "notes_on_failed_items": notes_by_index,
    }
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# AI Hub 71490 수동 정렬 감사 실패 분석",
        "",
        f"표본 {total}건 중 실패 {len(failed_rows)}건, 정확도 "
        f"{report['overall_accuracy']:.4f}",
        "",
        "## 층화별 실패율",
        "",
    ]
    for key, breakdown in strata.items():
        lines.append(f"### {key}")
        lines.append("")
        lines.append("| 값 | 전체 | 실패 | 실패율 |")
        lines.append("|---|---:|---:|---:|")
        for value, counts in breakdown.items():
            lines.append(
                f"| {value} | {counts['total']} | {counts['failed']} | "
                f"{counts['failure_rate']:.4f} |"
            )
        lines.append("")

    lines.append("## 자동 대응 연산(operation)별 실패율")
    lines.append("")
    lines.append("| operation | 전체 interval | 실패 항목 내 interval | 실패율 |")
    lines.append("|---|---:|---:|---:|")
    for operation in sorted(operation_counts_all):
        lines.append(
            f"| {operation} | {operation_counts_all[operation]} | "
            f"{operation_counts_failed.get(operation, 0)} | "
            f"{operation_failure_rate[operation]:.4f} |"
        )
    lines.append("")

    lines.append(
        f"실패 62건 중 자동 대응이 전부 `match`였던 항목(경계/시간 판단"
        f" 오류로 추정): {all_match_failures}건"
    )
    lines.append(
        f"실패 항목 중 substitution/deletion/insertion이 하나라도 있던"
        f" 항목: {len(failed_rows) - all_match_failures}건"
    )
    lines.append("")

    lines.append("## 실패 항목에서 가장 흔한 target→realized phone 쌍")
    lines.append("")
    lines.append("| operation | target | realized | 횟수 |")
    lines.append("|---|---|---|---:|")
    for entry in report["top_phone_pairs_in_failed_items"][:20]:
        lines.append(
            f"| {entry['operation']} | {entry['target_label']} | "
            f"{entry['realized_label']} | {entry['count']} |"
        )
    lines.append("")

    if notes_by_index:
        lines.append("## 검수자 메모 (실패 항목)")
        lines.append("")
        for index, note in sorted(notes_by_index.items()):
            lines.append(f"- #{index}: {note}")
        lines.append("")

    args.markdown_output.write_text("\n".join(lines), encoding="utf-8")
    print(f"failed: {len(failed_rows)}/{total}")
    print(f"json report: {args.json_output}")
    print(f"markdown report: {args.markdown_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
