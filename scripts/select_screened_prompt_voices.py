#!/usr/bin/env python3
"""Build a prompt set from the voices a screening run actually found usable.

Screening produces a verdict per voice; a bank must be built from that verdict and
not from a hand-copied list, or the two drift apart and nobody notices until the
generation run wastes hours on a voice that was known to fail.

Demographic spread is preserved where the screening allows it: the selection walks
the usable voices interleaved by the first component of `demographic_axis` (gender
in the 542 corpus), so asking for six from four men and two women returns both women
rather than six men.

Usage:
    select_screened_prompt_voices.py CANDIDATE_MANIFEST SCREENING_REPORT OUT_DIR
                                     [--speakers 6]
"""

from __future__ import annotations

import argparse
from itertools import zip_longest
import json
from pathlib import Path
import shutil
import sys


class SelectionError(RuntimeError):
    """Raised when a usable prompt set cannot be assembled."""


def interleave_by_group(entries: list[dict], *, key: str) -> list[dict]:
    """Order entries so consecutive picks change group, keeping group order stable."""

    groups: dict[str, list[dict]] = {}
    for entry in entries:
        groups.setdefault(str(entry.get(key, "")), []).append(entry)
    ordered: list[dict] = []
    for row in zip_longest(*(groups[name] for name in sorted(groups))):
        ordered.extend(item for item in row if item is not None)
    return ordered


def select(
    candidate_manifest: Path,
    screening_report: Path,
    output_dir: Path,
    *,
    count: int,
) -> dict:
    manifest = json.loads(candidate_manifest.read_text(encoding="utf-8"))
    report = json.loads(screening_report.read_text(encoding="utf-8"))
    usable_ids = set(report["usable_prompt_voice_ids"])
    if not usable_ids:
        raise SelectionError("the screening report lists no usable voice")

    rate_by_id = {
        row["prompt_voice_id"]: row.get("speech_rate_syllables_per_second")
        for row in report["rows"]
        if row.get("attempt") == 0 and row.get("inside_band")
    }
    usable = [
        entry
        for entry in manifest["prompt_voices"]
        if entry["prompt_voice_id"] in usable_ids
    ]
    if len(usable) < count:
        raise SelectionError(
            f"only {len(usable)} screened-usable voices, needed {count}"
        )
    # `gender` is the axis the corpus splits on and the one a single-gender bank
    # would collapse, so it is what the interleave protects.
    chosen = interleave_by_group(usable, key="gender")[:count]

    output_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    for entry in chosen:
        source = candidate_manifest.parent / entry["audio_relative_path"]
        destination = output_dir / entry["audio_relative_path"]
        if not destination.exists():
            shutil.copy2(source, destination)
        row = dict(entry)
        row["screened_speech_rate_syllables_per_second"] = rate_by_id.get(
            entry["prompt_voice_id"]
        )
        entries.append(row)

    selected = {
        "schema_version": manifest["schema_version"],
        "decision_record": manifest["decision_record"],
        "speaker_names_stored": False,
        "requested": count,
        "selected": len(entries),
        "audio_not_found": 0,
        "screened_from": candidate_manifest.name,
        "screening_report": screening_report.name,
        "screening_sentence_id": report.get("sentence_id"),
        "screening_band_syllables_per_second": report.get(
            "band_syllables_per_second"
        ),
        "candidates_screened": len(manifest["prompt_voices"]),
        "candidates_usable": len(usable),
        "prompt_voices": entries,
    }
    (output_dir / "prompt_voices.json").write_text(
        json.dumps(selected, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return selected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate_manifest", type=Path)
    parser.add_argument("screening_report", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--speakers", type=int, default=6)
    args = parser.parse_args()

    try:
        selected = select(
            args.candidate_manifest,
            args.screening_report,
            args.output_dir,
            count=args.speakers,
        )
    except (SelectionError, KeyError, OSError) as error:
        print(f"failed: {error}", file=sys.stderr)
        return 1
    print(
        f"selected {selected['selected']}/{selected['requested']} from "
        f"{selected['candidates_usable']} usable of "
        f"{selected['candidates_screened']} screened"
    )
    for entry in selected["prompt_voices"]:
        print(
            f"  {entry['prompt_voice_id']}  {entry['gender']:8s}"
            f" {entry['age_band']:8s}"
            f" {entry['screened_speech_rate_syllables_per_second']} syl/s"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
