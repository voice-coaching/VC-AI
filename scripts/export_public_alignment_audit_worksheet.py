#!/usr/bin/env python3
"""Export a privacy-safe public copy of the manual alignment-audit worksheet.

The restricted worksheet contains AI Hub file stems and locator paths that
can be linked to raw speaker ``UserID`` values, so it must never be
committed or redistributed. This exporter drops every locator column and
replaces ``utterance_id`` with the same namespaced SHA-256 item hash used by
the token-protected local review UI, so completed public judgments can be
joined back to restricted items on the execution server only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from voice_coach.evaluation.alignment_review import hash_item_id  # noqa: E402

PUBLIC_COLUMNS = [
    "audit_index",
    "item_id",
    "split",
    "l1",
    "gender",
    "proficiency",
    "error_presence",
    "duration_seconds",
    "target_phone_count",
    "realized_phone_count",
    "reviewer_id",
    "alignment_correct",
    "maximum_boundary_error_ms",
    "incorrect_interval_count",
    "annotation_fidelity",
    "notes",
]

FORBIDDEN_COLUMNS = {
    "utterance_id",
    "audio_path",
    "json_path",
    "textgrid_path",
    "alignment_detail_line",
}

# Pre-filled so a reviewer only has to edit rows they disagree with. This is
# a deliberate accuracy/throughput trade-off: an untouched row is
# indistinguishable from a row a reviewer actually listened to and judged
# correct, so it weakens the audit's evidentiary value versus an
# all-blank worksheet. See docs/constraints/AIHUB71490_EXPERT_AUDIT_WORKSHEET_GUIDE_KO.md
# for the accepted-risk note.
DEFAULT_ALIGNMENT_CORRECT = "correct"
DEFAULT_MAXIMUM_BOUNDARY_ERROR_MS = "0"
DEFAULT_INCORRECT_INTERVAL_COUNT = "0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("worksheet", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-samples", type=int, default=240)
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
    if len(rows) != args.expected_samples:
        raise ValueError(f"expected {args.expected_samples} rows, found {len(rows)}")
    missing = (set(PUBLIC_COLUMNS) - {"item_id", "annotation_fidelity"}) - set(rows[0])
    if missing - FORBIDDEN_COLUMNS:
        raise ValueError(
            "worksheet is missing required columns: "
            + ", ".join(sorted(missing - FORBIDDEN_COLUMNS))
        )
    item_ids = set()
    public_rows = []
    for row in rows:
        item_id = hash_item_id(row["utterance_id"])
        if item_id in item_ids:
            raise ValueError("duplicate namespaced item hash")
        item_ids.add(item_id)
        public_row = {"item_id": item_id}
        for column in PUBLIC_COLUMNS:
            if column == "item_id":
                continue
            public_row[column] = row.get(column, "")
        if not public_row["alignment_correct"]:
            public_row["alignment_correct"] = DEFAULT_ALIGNMENT_CORRECT
        if not public_row["maximum_boundary_error_ms"]:
            public_row["maximum_boundary_error_ms"] = DEFAULT_MAXIMUM_BOUNDARY_ERROR_MS
        if not public_row.get("incorrect_interval_count"):
            public_row["incorrect_interval_count"] = DEFAULT_INCORRECT_INTERVAL_COUNT
        public_rows.append(public_row)
    leaked = FORBIDDEN_COLUMNS & set(public_rows[0])
    if leaked:
        raise ValueError(
            "forbidden locator columns leaked into public rows: "
            + ", ".join(sorted(leaked))
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = args.output.with_name(f".{args.output.name}.{uuid4().hex}.tmp")
    with temp_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PUBLIC_COLUMNS, delimiter="\t")
        writer.writeheader()
        writer.writerows(public_rows)
    os.replace(temp_path, args.output)
    print(f"public worksheet rows: {len(public_rows)}")
    print(f"source worksheet sha256: {sha256_file(args.worksheet)}")
    print(f"public worksheet sha256: {sha256_file(args.output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
