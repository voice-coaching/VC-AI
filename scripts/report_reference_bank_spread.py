#!/usr/bin/env python3
"""Measure a reference bank and report which axes it can actually derive.

``d(x, R)`` scores nothing until the bank has a spread to compare against, and the
spread is a property of the recordings rather than of the contract that ordered
them. A contract can declare that it added six prompt voices; only measurement can
say whether the resulting bank has pitch spread on the sentence a learner is about
to attempt.

So this reads a bank manifest, measures every exemplar through the pinned acoustics
bridge, and prints, per sentence and per axis, the tolerance ``derive_tolerance``
would return. An axis that reports ``none`` here will report ``MISSING`` to the
learner, and that is the honest outcome — not a zero.

Usage:
    report_reference_bank_spread.py BANK_MANIFEST [--workers N] [--json OUT]
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from voice_coach.pronunciation_feedback.adapters.acoustic_branches import (  # noqa: E402
    AcousticBranchError,
    SubprocessAcousticBackend,
)
from voice_coach.pronunciation_feedback.adapters.prosody_distance import (  # noqa: E402
    F0_MEASUREMENT,
    MINIMUM_EXEMPLARS_FOR_TOLERANCE,
    axis_values_from_measurements,
    derive_tolerance,
    read_duration_seconds,
)
from voice_coach.pronunciation_feedback.multi_reference import PROSODY_AXES  # noqa: E402

#: Reported instead of a tolerance, so a reader can tell an axis that was never
#: measured from one that was measured and turned out to have no spread.
UNMEASURED = "axis_not_measured"
TOO_FEW = "fewer_than_two_exemplars_carry_the_axis"
NO_SPREAD = "every_exemplar_identical_on_this_axis"


class BankReportError(RuntimeError):
    """Raised when the bank cannot be measured at all."""


def measure_exemplars(
    manifest_path: Path, *, backend, workers: int = 1
) -> list[dict]:
    """One row per exemplar: its cell, its axis values, and any failure."""

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    exemplars = manifest.get("exemplars")
    if not exemplars:
        raise BankReportError("the manifest holds no exemplar")

    def measure(exemplar: dict) -> dict:
        path = manifest_path.parent / exemplar["audio_relative_path"]
        row = {
            "exemplar_id": exemplar.get("exemplar_id"),
            "prompt_id": exemplar.get("prompt_id"),
            "speaker_axis": exemplar.get("speaker_axis"),
            "speed_axis": exemplar.get("speed_axis"),
            "values": {},
            "error": None,
        }
        if not path.is_file():
            # An absent file is a bank defect, not a measurement of zero.
            row["error"] = "audio_absent"
            return row
        try:
            measurements = backend.measure(path)
        except AcousticBranchError as error:
            row["error"] = str(error)
            return row
        values = axis_values_from_measurements(
            measurements, duration_seconds=read_duration_seconds(path)
        )
        row["values"] = {axis: values.get(axis) for axis in PROSODY_AXES}
        return row

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            rows = list(pool.map(measure, exemplars))
    else:
        rows = [measure(item) for item in exemplars]
    rows.sort(key=lambda item: (str(item["prompt_id"]), str(item["exemplar_id"])))
    return rows


def summarise(rows: list[dict]) -> dict:
    """Per sentence and per axis, the tolerance the bank supports."""

    by_prompt: dict[str, list[dict]] = {}
    for row in rows:
        by_prompt.setdefault(str(row["prompt_id"]), []).append(row)

    prompts: list[dict] = []
    for prompt_id in sorted(by_prompt):
        group = by_prompt[prompt_id]
        usable = [row for row in group if row["error"] is None]
        axes: dict[str, dict] = {}
        for axis in PROSODY_AXES:
            carried = [
                row["values"][axis]
                for row in usable
                if row["values"].get(axis) is not None
            ]
            tolerance = derive_tolerance(carried) if carried else None
            if tolerance is not None:
                state = "derivable"
            elif not carried:
                state = UNMEASURED
            elif len(carried) < MINIMUM_EXEMPLARS_FOR_TOLERANCE:
                state = TOO_FEW
            else:
                state = NO_SPREAD
            axes[axis] = {
                "exemplars_carrying_axis": len(carried),
                "tolerance": tolerance,
                "state": state,
            }
        prompts.append(
            {
                "prompt_id": prompt_id,
                "exemplars": len(group),
                "measured": len(usable),
                "distinct_speakers": len({row["speaker_axis"] for row in usable}),
                "distinct_speeds": len({row["speed_axis"] for row in usable}),
                "axes": axes,
            }
        )

    derivable = {
        axis: sum(
            1 for prompt in prompts if prompt["axes"][axis]["state"] == "derivable"
        )
        for axis in PROSODY_AXES
    }
    return {
        "schema_version": "voice_coach.reference_bank_spread_report.v1",
        # Which pitch statistic produced the f0 tolerances below. A tolerance derived
        # from absolute register and one derived from pitch range are both "f0" and
        # are not comparable, so the report names the statistic rather than implying
        # it from the axis key.
        "f0_measurement": F0_MEASUREMENT,
        "exemplars": len(rows),
        "measurement_failures": sum(1 for row in rows if row["error"] is not None),
        "prompts": prompts,
        "prompts_with_derivable_tolerance": derivable,
        "prompt_count": len(prompts),
    }


def _print(report: dict) -> None:
    print(
        f"exemplars={report['exemplars']} prompts={report['prompt_count']} "
        f"measurement_failures={report['measurement_failures']}"
    )
    header = "prompt      spk spd " + " ".join(f"{axis:>10s}" for axis in PROSODY_AXES)
    print(header)
    for prompt in report["prompts"]:
        cells = []
        for axis in PROSODY_AXES:
            entry = prompt["axes"][axis]
            tolerance = entry["tolerance"]
            cells.append(f"{tolerance:10.3f}" if tolerance is not None else f"{'—':>10s}")
        print(
            f"{prompt['prompt_id']:11s} {prompt['distinct_speakers']:2d} "
            f"{prompt['distinct_speeds']:2d}  " + " ".join(cells)
        )
    print("derivable tolerance per axis (out of "
          f"{report['prompt_count']} sentences):")
    for axis, count in report["prompts_with_derivable_tolerance"].items():
        print(f"  {axis:9s} {count}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    backend = SubprocessAcousticBackend(repository_root=root)
    try:
        rows = measure_exemplars(args.manifest, backend=backend, workers=args.workers)
    except BankReportError as error:
        print(f"failed: {error}", file=sys.stderr)
        return 1
    report = summarise(rows)
    _print(report)
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
