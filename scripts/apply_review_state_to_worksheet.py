#!/usr/bin/env python3
"""Write completed GUI/local-review judgments into a copy of the
original restricted worksheet, filling in its blank reviewer/judgment
columns without touching the pristine input file.

The token-protected review server never touches the worksheet TSV
directly; it stores every judgment, keyed by namespaced item hash, in a
separate review-state JSON. This script joins that state back onto the
original worksheet rows (by the same namespaced hash) and writes the
result to a NEW file. It never overwrites ``--worksheet`` in place: that
file's SHA-256 is pinned as a fixed input by the review state itself and
by project documentation, so mutating it would break the hash check the
next time the review server, ``status``, or ``export`` loads that state
against the worksheet. It requires exactly 240/240 completed reviews.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from voice_coach.evaluation.alignment_review import (  # noqa: E402
    ReviewDataset,
    ReviewStore,
    atomic_write_text,
)

WORKSHEET_COLUMNS = [
    "audit_index",
    "utterance_id",
    "split",
    "l1",
    "gender",
    "proficiency",
    "error_presence",
    "duration_seconds",
    "target_phone_count",
    "realized_phone_count",
    "audio_path",
    "json_path",
    "textgrid_path",
    "alignment_detail_line",
    "reviewer_id",
    "alignment_correct",
    "maximum_boundary_error_ms",
    "notes",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worksheet", type=Path, required=True)
    parser.add_argument("--details", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="path for the completed worksheet copy; must differ from --worksheet",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.output.resolve() == args.worksheet.resolve():
        raise SystemExit(
            "--output must not be the same path as --worksheet: the "
            "pristine worksheet's SHA-256 is pinned by the review state "
            "and by project docs, so it must never be overwritten"
        )
    dataset = ReviewDataset.load(
        worksheet=args.worksheet,
        details=args.details,
        dataset_root=args.dataset_root,
    )
    store = ReviewStore(args.state, dataset)
    total = len(dataset.items)
    completed = len(store.reviews)
    if completed != total or total == 0:
        raise SystemExit(
            f"refusing to write back an incomplete review: {completed}/{total}"
        )

    with args.worksheet.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = list(reader)
    if len(rows) != total:
        raise SystemExit("worksheet row count changed since dataset load")

    by_raw_id = {item.raw_utterance_id: item for item in dataset.items}
    updated_rows = []
    for row in rows:
        item = by_raw_id[row["utterance_id"].strip()]
        review = store.reviews[item.item_sha256]
        row = dict(row)
        row["reviewer_id"] = review["reviewer_id_sha256"]
        row["alignment_correct"] = str(review["alignment_correct"]).lower()
        row["maximum_boundary_error_ms"] = review["maximum_boundary_error_ms"]
        row["notes"] = review["notes"]
        updated_rows.append(row)

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=WORKSHEET_COLUMNS, delimiter="\t")
    writer.writeheader()
    writer.writerows(updated_rows)
    atomic_write_text(args.output, buffer.getvalue())

    print(f"rows written: {len(updated_rows)}/{total}")
    print(f"completed worksheet copy: {args.output}")
    print(f"pristine worksheet untouched: {args.worksheet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
