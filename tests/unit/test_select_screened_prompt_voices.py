"""A bank must be built from the screening verdict, not from a hand-copied list.

The failure this guards against is quiet: someone screens twelve voices, four are
known to run away, and the bank is then assembled from a list typed out by hand that
still contains one of them. The generation run burns hours before anyone notices.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "select_screened", ROOT / "scripts" / "select_screened_prompt_voices.py"
)
select_screened = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules[_spec.name] = select_screened
_spec.loader.exec_module(select_screened)


def _candidate(voice_id: str, *, gender: str, age: str) -> dict:
    return {
        "prompt_voice_id": voice_id,
        "speaker_axis": voice_id,
        "audio_relative_path": f"{voice_id}.wav",
        "gender": gender,
        "age_band": age,
        "region": "Seoul",
        "length_seconds": 3.2,
        "prompt_transcript": "고기가 안 들어간 메뉴를 보여드릴게요.",
        "prompt_syllable_count": 16,
        "license_spdx": "AIHub-Terms",
        "rights_basis": "test fixture under DEV-R01-002",
        "source_dataset": "aihub-542",
        "third_party_speaker_cloning": True,
    }


def _write(tmp_path: Path, candidates: list[dict], usable: list[str]) -> tuple[Path, Path]:
    manifest_dir = tmp_path / "candidates"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    for entry in candidates:
        (manifest_dir / entry["audio_relative_path"]).write_bytes(b"RIFF-stub")
    manifest = manifest_dir / "prompt_voices.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "voice_coach.prompt_voice_manifest.v1",
                "decision_record": "DEV-R01-002",
                "prompt_voices": candidates,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report = tmp_path / "screening_report.json"
    report.write_text(
        json.dumps(
            {
                "sentence_id": "R00-01",
                "band_syllables_per_second": [3.846, 8.543],
                "usable_prompt_voice_ids": usable,
                "rows": [
                    {
                        "prompt_voice_id": entry["prompt_voice_id"],
                        "attempt": 0,
                        "inside_band": entry["prompt_voice_id"] in usable,
                        "speech_rate_syllables_per_second": 4.8,
                    }
                    for entry in candidates
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest, report


def test_a_voice_the_screening_rejected_never_enters_the_set(tmp_path: Path) -> None:
    candidates = [
        _candidate("v-runaway", gender="Female", age="10~19"),
        _candidate("v-ok-1", gender="Male", age="10~19"),
        _candidate("v-ok-2", gender="Male", age="20~29"),
    ]
    manifest, report = _write(tmp_path, candidates, ["v-ok-1", "v-ok-2"])

    selected = select_screened.select(
        manifest, report, tmp_path / "out", count=2
    )

    ids = {entry["prompt_voice_id"] for entry in selected["prompt_voices"]}
    assert ids == {"v-ok-1", "v-ok-2"}
    assert not (tmp_path / "out" / "v-runaway.wav").exists()


def test_the_screening_verdict_is_carried_into_the_set(tmp_path: Path) -> None:
    """A later reader must be able to see what each voice was screened at."""

    candidates = [_candidate("v-ok-1", gender="Male", age="10~19")]
    manifest, report = _write(tmp_path, candidates, ["v-ok-1"])

    selected = select_screened.select(manifest, report, tmp_path / "out", count=1)

    entry = selected["prompt_voices"][0]
    assert entry["screened_speech_rate_syllables_per_second"] == 4.8
    assert selected["screening_sentence_id"] == "R00-01"
    assert selected["screening_band_syllables_per_second"] == [3.846, 8.543]
    assert selected["candidates_screened"] == 1
    assert selected["candidates_usable"] == 1


def test_the_minority_gender_survives_the_cut(tmp_path: Path) -> None:
    """Four usable men and two usable women must not yield six men."""

    candidates = [
        _candidate(f"m{i}", gender="Male", age=f"{i}0~{i}9") for i in range(1, 7)
    ] + [_candidate(f"f{i}", gender="Female", age=f"{i}0~{i}9") for i in (4, 5)]
    usable = [entry["prompt_voice_id"] for entry in candidates]
    manifest, report = _write(tmp_path, candidates, usable)

    selected = select_screened.select(manifest, report, tmp_path / "out", count=6)

    genders = [entry["gender"] for entry in selected["prompt_voices"]]
    assert genders.count("Female") == 2
    assert genders.count("Male") == 4


def test_asking_for_more_than_screened_usable_fails_closed(tmp_path: Path) -> None:
    candidates = [_candidate("v-ok-1", gender="Male", age="10~19")]
    manifest, report = _write(tmp_path, candidates, ["v-ok-1"])

    with pytest.raises(select_screened.SelectionError, match="only 1"):
        select_screened.select(manifest, report, tmp_path / "out", count=4)


def test_an_empty_verdict_fails_closed(tmp_path: Path) -> None:
    candidates = [_candidate("v-runaway", gender="Female", age="10~19")]
    manifest, report = _write(tmp_path, candidates, [])

    with pytest.raises(select_screened.SelectionError, match="no usable voice"):
        select_screened.select(manifest, report, tmp_path / "out", count=1)


def test_speaker_names_are_still_never_stored(tmp_path: Path) -> None:
    candidates = [_candidate("v-ok-1", gender="Male", age="10~19")]
    manifest, report = _write(tmp_path, candidates, ["v-ok-1"])

    selected = select_screened.select(manifest, report, tmp_path / "out", count=1)

    assert selected["speaker_names_stored"] is False
    assert selected["decision_record"] == "DEV-R01-002"
