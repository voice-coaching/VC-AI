"""A bank's declared diversity and its measured spread are different facts.

The report exists to catch the case where a contract says six prompt voices were
added and the recordings turn out to sit on top of each other anyway. So the tests
below care most about the states that are not `derivable`: they must stay
distinguishable from each other and from a tolerance of zero.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "bank_spread", ROOT / "scripts" / "report_reference_bank_spread.py"
)
spread = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules[_spec.name] = spread
_spec.loader.exec_module(spread)

from voice_coach.pronunciation_feedback.adapters.acoustic_branches import (  # noqa: E402
    AcousticBranchError,
    AcousticMeasurements,
)


class _Backend:
    """Returns a scripted pitch range, in semitones, per file name."""

    def __init__(self, by_name: dict[str, float | None]) -> None:
        self._by_name = by_name

    def measure(self, audio_path: Path) -> AcousticMeasurements:
        if audio_path.name not in self._by_name:
            raise AcousticBranchError("no measurement for this file")
        semitones = self._by_name[audio_path.name]
        return AcousticMeasurements(
            backend_id="stub",
            revision="stub-v1",
            f0=None
            if semitones is None
            else {
                "state": "observed",
                "median_hz": 120.0,
                "range_semitones": semitones,
            },
            energy={"state": "observed", "values": {"loudness_sma3_amean": 0.4}},
        )


def _bank(tmp_path: Path, exemplars: list[dict], *, seconds: float = 1.0) -> Path:
    audio = tmp_path / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    for exemplar in exemplars:
        name = Path(exemplar["audio_relative_path"]).name
        t = np.arange(int(24_000 * seconds)) / 24_000
        sf.write(audio / name, (0.2 * np.sin(2 * np.pi * 200 * t)), 24_000)
    manifest = tmp_path / "bank.json"
    manifest.write_text(
        json.dumps({"exemplars": exemplars}, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


def _exemplar(name: str, *, prompt: str = "R00-01", speaker: str, speed: str) -> dict:
    return {
        "exemplar_id": name,
        "prompt_id": prompt,
        "speaker_axis": speaker,
        "speed_axis": speed,
        "audio_relative_path": f"audio/{name}.wav",
    }


# -- what "no tolerance" means ------------------------------------------------


def test_one_exemplar_yields_no_tolerance_rather_than_zero(tmp_path: Path) -> None:
    """A bank of one has no spread, and zero spread would score every learner 0."""

    manifest = _bank(tmp_path, [_exemplar("a", speaker="s1", speed="neutral")])
    backend = _Backend({"a.wav": 5.0})

    report = spread.summarise(
        spread.measure_exemplars(manifest, backend=backend)
    )
    entry = report["prompts"][0]["axes"]["f0"]

    assert entry["tolerance"] is None
    assert entry["state"] == spread.TOO_FEW
    assert report["prompts_with_derivable_tolerance"]["f0"] == 0


def test_identical_exemplars_are_reported_as_no_spread_not_as_zero(
    tmp_path: Path,
) -> None:
    manifest = _bank(
        tmp_path,
        [
            _exemplar("a", speaker="s1", speed="neutral"),
            _exemplar("b", speaker="s2", speed="neutral"),
        ],
    )
    backend = _Backend({"a.wav": 5.0, "b.wav": 5.0})

    report = spread.summarise(spread.measure_exemplars(manifest, backend=backend))
    entry = report["prompts"][0]["axes"]["f0"]

    assert entry["tolerance"] is None
    assert entry["state"] == spread.NO_SPREAD
    assert entry["exemplars_carrying_axis"] == 2


def test_an_axis_no_exemplar_carries_is_distinguished_from_no_spread(
    tmp_path: Path,
) -> None:
    manifest = _bank(
        tmp_path,
        [
            _exemplar("a", speaker="s1", speed="neutral"),
            _exemplar("b", speaker="s2", speed="neutral"),
        ],
    )
    backend = _Backend({"a.wav": None, "b.wav": None})

    report = spread.summarise(spread.measure_exemplars(manifest, backend=backend))

    assert report["prompts"][0]["axes"]["f0"]["state"] == spread.UNMEASURED
    # rhythm needs the interval tables, which a bank manifest does not carry.
    assert report["prompts"][0]["axes"]["rhythm"]["state"] == spread.UNMEASURED


def test_two_speakers_make_the_pitch_axis_derivable(tmp_path: Path) -> None:
    """This is the property the multi-speaker bank exists to obtain."""

    manifest = _bank(
        tmp_path,
        [
            _exemplar("a", speaker="s1", speed="neutral"),
            _exemplar("b", speaker="s2", speed="neutral"),
        ],
    )
    backend = _Backend({"a.wav": 4.0, "b.wav": 9.0})

    report = spread.summarise(spread.measure_exemplars(manifest, backend=backend))
    entry = report["prompts"][0]["axes"]["f0"]

    assert entry["state"] == "derivable"
    # One exemplar moves its pitch across 4 semitones, the other across 9.
    assert entry["tolerance"] == pytest.approx(5.0)
    assert report["prompts"][0]["distinct_speakers"] == 2


def test_the_report_names_which_pitch_statistic_it_used(tmp_path: Path) -> None:
    """"f0" alone does not say whether the tolerance came from register or range."""

    manifest = _bank(tmp_path, [_exemplar("a", speaker="s1", speed="neutral")])

    report = spread.summarise(
        spread.measure_exemplars(manifest, backend=_Backend({"a.wav": 5.0}))
    )

    assert report["f0_measurement"] == "voiced_pitch_range_semitones"


# -- failures are counted, never averaged away --------------------------------


def test_an_absent_audio_file_is_a_failure_not_a_measurement(tmp_path: Path) -> None:
    manifest = _bank(tmp_path, [_exemplar("a", speaker="s1", speed="neutral")])
    (tmp_path / "audio" / "a.wav").unlink()

    rows = spread.measure_exemplars(manifest, backend=_Backend({"a.wav": 5.0}))
    report = spread.summarise(rows)

    assert rows[0]["error"] == "audio_absent"
    assert report["measurement_failures"] == 1
    assert report["prompts"][0]["measured"] == 0


def test_a_backend_failure_does_not_stop_the_other_exemplars(tmp_path: Path) -> None:
    manifest = _bank(
        tmp_path,
        [
            _exemplar("a", speaker="s1", speed="neutral"),
            _exemplar("b", speaker="s2", speed="neutral"),
            _exemplar("c", speaker="s3", speed="neutral"),
        ],
    )
    backend = _Backend({"a.wav": 4.0, "c.wav": 8.0})

    report = spread.summarise(spread.measure_exemplars(manifest, backend=backend))

    assert report["measurement_failures"] == 1
    assert report["prompts"][0]["measured"] == 2
    assert report["prompts"][0]["axes"]["f0"]["state"] == "derivable"


def test_an_empty_manifest_fails_closed(tmp_path: Path) -> None:
    manifest = tmp_path / "bank.json"
    manifest.write_text(json.dumps({"exemplars": []}), encoding="utf-8")

    with pytest.raises(spread.BankReportError, match="no exemplar"):
        spread.measure_exemplars(manifest, backend=_Backend({}))


# -- sentences are independent ------------------------------------------------


def test_each_sentence_gets_its_own_tolerance(tmp_path: Path) -> None:
    """Tolerance is per sentence: one sentence's spread cannot license another's."""

    manifest = _bank(
        tmp_path,
        [
            _exemplar("a", prompt="R00-01", speaker="s1", speed="neutral"),
            _exemplar("b", prompt="R00-01", speaker="s2", speed="neutral"),
            _exemplar("c", prompt="R00-02", speaker="s1", speed="neutral"),
        ],
    )
    backend = _Backend({"a.wav": 4.0, "b.wav": 9.0, "c.wav": 4.0})

    report = spread.summarise(spread.measure_exemplars(manifest, backend=backend))
    first, second = report["prompts"]

    assert first["axes"]["f0"]["state"] == "derivable"
    assert second["axes"]["f0"]["state"] == spread.TOO_FEW
    assert report["prompts_with_derivable_tolerance"]["f0"] == 1
    assert report["prompt_count"] == 2


def test_measuring_in_parallel_gives_the_same_rows(tmp_path: Path) -> None:
    exemplars = [
        _exemplar(name, speaker=f"s{index}", speed="neutral")
        for index, name in enumerate("abcdef")
    ]
    manifest = _bank(tmp_path, exemplars)
    backend = _Backend({f"{name}.wav": 3.0 + i for i, name in enumerate("abcdef")})

    serial = spread.measure_exemplars(manifest, backend=backend, workers=1)
    parallel = spread.measure_exemplars(manifest, backend=backend, workers=4)

    assert serial == parallel
