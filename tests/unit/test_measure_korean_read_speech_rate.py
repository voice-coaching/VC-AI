"""The band decides which renderings count as speech, so its inputs must be strict.

The failure this guards against is a band widened by mislabelled boundaries or by
utterances whose transcript and audio disagree. Every filter below exists because
without it the band would admit a rendering no person could have produced.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "read_speech_rate", ROOT / "scripts" / "measure_korean_read_speech_rate.py"
)
rate = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules[_spec.name] = rate
_spec.loader.exec_module(rate)


def _label(
    *,
    text: str = "국물 냄새가 정말 구수해요",
    start: float = 0.0,
    end: float = 2.0,
    quality: str = "Good",
    speaker: str = "JHJ",
) -> dict:
    return {
        "전사정보": {"TransLabelText": text},
        "화자정보": {"SpeakerName": speaker, "Gender": "Male"},
        "파일정보": {"FileName": "a.wav", "FileLength": end + 1.0},
        "기타정보": {
            "QualityStatus": quality,
            "SpeechStart": start,
            "SpeechEnd": end,
        },
    }


def _zip(tmp_path: Path, labels: list[dict], name: str = "VL.zip") -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for index, payload in enumerate(labels):
            archive.writestr(
                f"VL/{index:05d}.json", json.dumps(payload, ensure_ascii=False)
            )
    return path


# -- what the rate is measured over -------------------------------------------


def test_the_rate_uses_marked_speech_not_file_length(tmp_path: Path) -> None:
    """Leading and trailing silence would make every speaker look slower."""

    # 11 syllables spoken between 0.5 s and 2.5 s: 5.5 syl/s, in a 3.5 s file.
    labels = [_label(start=0.5, end=2.5)]

    rates = rate.utterance_rates(_zip(tmp_path, labels))

    assert rates == pytest.approx([5.5])


def test_only_hangul_syllables_are_counted(tmp_path: Path) -> None:
    labels = [_label(text="가나다라마 ABC 123.", start=0.0, end=1.0)]

    assert rate.utterance_rates(_zip(tmp_path, labels)) == pytest.approx([5.0])


@pytest.mark.parametrize(
    "override",
    [
        {"quality": "Bad"},
        {"text": "네"},
        {"start": 2.0, "end": 2.0},
        {"start": 3.0, "end": 1.0},
    ],
)
def test_an_unusable_utterance_does_not_enter_the_band(
    tmp_path: Path, override: dict
) -> None:
    assert rate.utterance_rates(_zip(tmp_path, [_label(**override)])) == []


def test_a_malformed_boundary_is_skipped_rather_than_guessed(tmp_path: Path) -> None:
    broken = _label()
    broken["기타정보"]["SpeechEnd"] = "N/A"

    assert rate.utterance_rates(_zip(tmp_path, [broken])) == []


# -- the report ----------------------------------------------------------------


def test_the_band_is_a_percentile_pair_over_all_archives(tmp_path: Path) -> None:
    slow = [_label(start=0.0, end=2.2) for _ in range(50)]  # 5.0 syl/s
    fast = [_label(start=0.0, end=1.1) for _ in range(50)]  # 10.0 syl/s

    report = rate.measure([_zip(tmp_path, slow, "VL1.zip"), _zip(tmp_path, fast, "VL2.zip")])

    assert report["utterances"] == 100
    assert len(report["sources"]) == 2
    assert report["band"]["low_syllables_per_second"] == pytest.approx(5.0)
    assert report["band"]["high_syllables_per_second"] == pytest.approx(10.0)


def test_each_source_archive_is_pinned_by_digest(tmp_path: Path) -> None:
    """A band is only reproducible if the archives it came from are identified."""

    report = rate.measure([_zip(tmp_path, [_label()])])
    source = report["sources"][0]

    assert len(source["archive_sha256"]) == 64
    assert source["usable_utterances"] == 1


def test_nothing_speaker_level_reaches_the_report(tmp_path: Path) -> None:
    report = rate.measure([_zip(tmp_path, [_label(speaker="JHJ")])])

    assert "JHJ" not in json.dumps(report, ensure_ascii=False)
    assert report["speaker_level_data_stored"] is False


def test_an_archive_with_no_usable_row_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(rate.RateMeasurementError, match="no label row"):
        rate.measure([_zip(tmp_path, [_label(quality="Bad")])])


def test_an_absent_archive_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(rate.RateMeasurementError, match="absent"):
        rate.measure([tmp_path / "nope.zip"])
