#!/usr/bin/env python3
"""Measure how fast Korean read speech actually is, from labelled corpus metadata.

A generated reference is only usable if it says the whole sentence at a rate a
person could have said it at. Bounding that needs a number, and the number must not
be chosen: a band picked by eye would silently define which renderings are
"plausible" and therefore which learners get compared against what.

So the band is measured. AI Hub 542 is booth-recorded read speech with a transcript
and marked speech boundaries per utterance, which is exactly the population a
reference rendering is trying to imitate. Rate is syllables between ``SpeechStart``
and ``SpeechEnd`` — the label's own boundaries, so leading and trailing silence do
not enter the rate.

Only label archives are read. No audio is opened, and nothing per-speaker leaves
this script: the output is percentiles and a count.

Usage:
    measure_korean_read_speech_rate.py LABEL_ZIP [LABEL_ZIP ...] --out PATH
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

#: Hangul syllable block. The rate is per syllable, so Latin characters, digits and
#: punctuation are deliberately not counted — the transcript field is already
#: normalised to spoken form (`3시` appears as `세시`).
HANGUL_SYLLABLE = re.compile(r"[가-힣]")
REQUIRED_QUALITY = "Good"
#: Below this a rate is dominated by how the boundary was marked rather than by how
#: fast the speaker was.
MINIMUM_SYLLABLES = 5
#: Reported band. Not the extremes: a corpus of 400,000 utterances has mislabelled
#: boundaries in it, and the 0.5th and 99.5th percentiles keep those from setting
#: the bound while still admitting 99% of real read speech.
LOW_PERCENTILE = 0.5
HIGH_PERCENTILE = 99.5
REPORTED_PERCENTILES = (0.1, 0.5, 1.0, 5.0, 25.0, 50.0, 75.0, 95.0, 99.0, 99.5, 99.9)


class RateMeasurementError(RuntimeError):
    """Raised when no usable utterance was found."""


def utterance_rates(label_zip: Path) -> list[float]:
    """Syllables per second of marked speech, for every usable utterance."""

    rates: list[float] = []
    with zipfile.ZipFile(label_zip) as archive:
        for name in archive.namelist():
            if not name.lower().endswith(".json"):
                continue
            try:
                payload = json.loads(archive.read(name).decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError):
                continue
            other = payload.get("기타정보") or {}
            if other.get("QualityStatus") != REQUIRED_QUALITY:
                continue
            try:
                seconds = float(other["SpeechEnd"]) - float(other["SpeechStart"])
            except (KeyError, TypeError, ValueError):
                continue
            if seconds <= 0:
                continue
            text = (payload.get("전사정보") or {}).get("TransLabelText") or ""
            syllables = len(HANGUL_SYLLABLE.findall(text))
            if syllables < MINIMUM_SYLLABLES:
                continue
            rates.append(syllables / seconds)
    return rates


def percentile(sorted_rates: list[float], percent: float) -> float:
    if not sorted_rates:
        raise RateMeasurementError("no utterance to take a percentile of")
    index = int(percent / 100.0 * len(sorted_rates))
    return sorted_rates[min(len(sorted_rates) - 1, max(0, index))]


def measure(label_zips: list[Path]) -> dict:
    rates: list[float] = []
    sources: list[dict] = []
    for path in label_zips:
        if not path.is_file():
            raise RateMeasurementError(f"label archive is absent: {path.name}")
        found = utterance_rates(path)
        sources.append(
            {
                "archive_name": path.name,
                "archive_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "usable_utterances": len(found),
            }
        )
        rates.extend(found)
    if not rates:
        raise RateMeasurementError("no label row met the measurement criteria")
    rates.sort()
    return {
        "schema_version": "voice_coach.korean_read_speech_rate_band.v1",
        "unit": "hangul_syllables_per_second_of_marked_speech",
        "population": "aihub542_validation_read_speech_quality_good",
        "utterances": len(rates),
        "minimum_syllables_per_utterance": MINIMUM_SYLLABLES,
        "percentiles": {
            f"p{value}": round(percentile(rates, value), 4)
            for value in REPORTED_PERCENTILES
        },
        "band": {
            "low_percentile": LOW_PERCENTILE,
            "high_percentile": HIGH_PERCENTILE,
            "low_syllables_per_second": round(percentile(rates, LOW_PERCENTILE), 4),
            "high_syllables_per_second": round(percentile(rates, HIGH_PERCENTILE), 4),
        },
        "sources": sources,
        "speaker_level_data_stored": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("label_zips", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    try:
        report = measure(args.label_zips)
    except RateMeasurementError as error:
        print(f"failed: {error}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    band = report["band"]
    print(f"utterances={report['utterances']}")
    print(
        f"band p{band['low_percentile']}-p{band['high_percentile']} = "
        f"{band['low_syllables_per_second']:.3f}-"
        f"{band['high_syllables_per_second']:.3f} syl/s"
    )
    print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
