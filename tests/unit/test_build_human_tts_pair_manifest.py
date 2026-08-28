"""The pair must isolate synthesis, or the evaluator learns the wrong thing.

With 142 corpus speakers and a handful of prompt voices, "is this synthetic?" is
perfectly predicted by "is this one of the prompt voices?". An evaluator trained on
that shortcut scores every unseen speaker as human. Every test here defends the one
property that closes the shortcut: speaker and text are held fixed across the pair.
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
    "build_pairs", ROOT / "scripts" / "build_human_tts_pair_manifest.py"
)
build_pairs = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules[_spec.name] = build_pairs
_spec.loader.exec_module(build_pairs)


def _label(
    *,
    directory: str,
    file_name: str,
    text: str,
    gender: str = "Male",
    age: str = "20~29",
    quality: str = "Good",
    start: float = 0.2,
    end: float = 3.0,
) -> dict:
    return {
        "전사정보": {"TransLabelText": text},
        "화자정보": {"SpeakerName": directory.split("_")[-1], "Gender": gender, "Age": age},
        "파일정보": {"FileName": file_name, "DirectoryPath": directory},
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


#: 16 Hangul syllables, the preferred prompt length.
PROMPT_TEXT = "고기가 안 들어간 메뉴를 보여드릴게요."
#: 13 syllables, inside the target window.
TARGET_TEXT = "쌀과 콩을 깨끗한 그릇에 담아요."


def _speaker_labels(directory: str, **kwargs) -> list[dict]:
    return [
        _label(directory=directory, file_name=f"{directory}_1.wav", text=TARGET_TEXT, **kwargs),
        _label(directory=directory, file_name=f"{directory}_2.wav", text=PROMPT_TEXT, **kwargs),
    ]


# -- the property that closes the shortcut ------------------------------------


def test_the_pair_holds_speaker_and_text_fixed(tmp_path: Path) -> None:
    manifest = build_pairs.build(
        [_zip(tmp_path, _speaker_labels("0049_G1A2E7_JHJ"))], pairs_per_speaker=5
    )

    pair = manifest["pair_rows"][0]
    assert pair["text"] == TARGET_TEXT
    # The TTS side is prompted by the same speaker, so both sides share one axis.
    assert pair["speaker_axis"].startswith("aihub542-")
    assert manifest["pairing_rule"] == (
        "same_speaker_same_text_prompt_from_a_different_utterance"
    )


def test_the_prompt_is_never_the_paired_utterance(tmp_path: Path) -> None:
    """Prompting with the target hands the generator the recording it is judged against."""

    manifest = build_pairs.build(
        [_zip(tmp_path, _speaker_labels("0049_G1A2E7_JHJ"))], pairs_per_speaker=5
    )

    for pair in manifest["pair_rows"]:
        assert pair["tts_prompt_file_name"] != pair["human_file_name"]


def test_two_speakers_sharing_initials_stay_two_axes(tmp_path: Path) -> None:
    labels = _speaker_labels("0049_G1A2E7_JHJ") + _speaker_labels("1204_G1A2E7_JHJ")

    manifest = build_pairs.build([_zip(tmp_path, labels)], pairs_per_speaker=5)

    assert len({pair["speaker_axis"] for pair in manifest["pair_rows"]}) == 2


def test_the_speaker_axis_is_pseudonymous_and_the_file_names_are_declared(
    tmp_path: Path,
) -> None:
    """The corpus file name carries initials and cannot be dropped: it locates the
    audio. So the manifest declares that rather than claiming a pseudonymity it
    does not have — and `speaker_axis`, the field that travels downstream, is a
    digest."""

    manifest = build_pairs.build(
        [_zip(tmp_path, _speaker_labels("0049_G1A2E7_JHJ"))], pairs_per_speaker=5
    )

    for pair in manifest["pair_rows"]:
        assert "JHJ" not in pair["speaker_axis"]
        assert pair["speaker_axis"] == (
            "aihub542-" + build_pairs.speaker_digest("0049_G1A2E7_JHJ")[:16]
        )
    assert manifest["contains_corpus_file_names"] is True
    assert manifest["storage_zone"] == "R1_restricted_locator"
    assert manifest["speaker_axis_is_pseudonymous"] is True


# -- what does not become a pair ----------------------------------------------


def test_a_speaker_with_no_usable_prompt_yields_no_pair(tmp_path: Path) -> None:
    """One utterance cannot prompt itself, so that speaker contributes nothing."""

    labels = [
        _label(
            directory="0049_G1A2E7_JHJ",
            file_name="only.wav",
            text=TARGET_TEXT,
        )
    ]

    with pytest.raises(build_pairs.PairManifestError, match="no speaker"):
        build_pairs.build([_zip(tmp_path, labels)], pairs_per_speaker=5)


@pytest.mark.parametrize(
    "override",
    [
        {"quality": "Bad"},
        {"start": 3.0, "end": 3.0},
    ],
)
def test_an_unusable_label_never_becomes_a_pair(
    tmp_path: Path, override: dict
) -> None:
    labels = _speaker_labels("0049_G1A2E7_JHJ", **override)

    with pytest.raises(build_pairs.PairManifestError):
        build_pairs.build([_zip(tmp_path, labels)], pairs_per_speaker=5)


def test_a_target_outside_the_syllable_window_is_skipped(tmp_path: Path) -> None:
    labels = [
        # One syllable: too short to judge, so never a target.
        _label(directory="0049_G1A2E7_JHJ", file_name="a.wav", text="네"),
        _label(directory="0049_G1A2E7_JHJ", file_name="b.wav", text=TARGET_TEXT),
        _label(directory="0049_G1A2E7_JHJ", file_name="c.wav", text=PROMPT_TEXT),
    ]

    manifest = build_pairs.build([_zip(tmp_path, labels)], pairs_per_speaker=5)

    assert "네" not in {pair["text"] for pair in manifest["pair_rows"]}
    assert all(
        build_pairs.MINIMUM_TARGET_SYLLABLES
        <= pair["target_syllables"]
        <= build_pairs.MAXIMUM_TARGET_SYLLABLES
        for pair in manifest["pair_rows"]
    )


def test_pairs_per_speaker_is_respected(tmp_path: Path) -> None:
    labels = [
        _label(
            directory="0049_G1A2E7_JHJ",
            file_name=f"t{index}.wav",
            text=TARGET_TEXT,
        )
        for index in range(10)
    ] + [_label(directory="0049_G1A2E7_JHJ", file_name="p.wav", text=PROMPT_TEXT)]

    manifest = build_pairs.build([_zip(tmp_path, labels)], pairs_per_speaker=3)

    assert manifest["pairs"] == 3


def test_an_absent_archive_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(build_pairs.PairManifestError, match="absent"):
        build_pairs.build([tmp_path / "nope.zip"], pairs_per_speaker=5)
