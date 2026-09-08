"""Prompt selection must spread across speakers and never carry their names.

The corpus label holds a real speaker name. If it reached `speaker_axis`, it would
land in every exemplar row and every learner-facing record, so the tests here check
the digest boundary as hard as they check the selection logic.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import zipfile

import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "build_prompt_voices", ROOT / "scripts" / "build_prompt_voices_from_aihub542.py"
)
build_prompts = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules[_spec.name] = build_prompts
_spec.loader.exec_module(build_prompts)


def _label(
    *,
    speaker: str,
    file_name: str,
    gender: str = "Female",
    age: str = "30~39",
    length: float = 4.0,
    quality: str = "Good",
    sample_rate: str = "48000",
    channels: str = "1",
    directory: str | None = None,
    text: str = "고기가 안 들어간 메뉴를 보여드릴게요.",
) -> dict:
    return {
        "음성정보": {
            "SamplingRate": sample_rate,
            "NumberOfChannel": channels,
            "EncodingLaw": "SignedIntegerPCM",
        },
        "전사정보": {"TransLabelText": text},
        "화자정보": {
            "SpeakerName": speaker,
            "Gender": gender,
            "Age": age,
            "Region": "Gyeonggi",
        },
        "파일정보": {
            "FileName": file_name,
            "FileLength": length,
            **({"DirectoryPath": directory} if directory is not None else {}),
        },
        "기타정보": {"QualityStatus": quality},
    }


def _label_zip(tmp_path: Path, labels: list[dict]) -> Path:
    path = tmp_path / "VL.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for index, payload in enumerate(labels):
            archive.writestr(
                f"VL/{index:04d}.json",
                json.dumps(payload, ensure_ascii=False),
            )
    return path


def _audio_tree(tmp_path: Path, file_names: list[str]) -> Path:
    root = tmp_path / "audio"
    for name in file_names:
        path = root / "session01" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        t = np.arange(48_000 * 4) / 48_000
        sf.write(path, (0.2 * np.sin(2 * np.pi * 200 * t)).astype("float32"), 48_000)
    return root


# -- the name boundary --------------------------------------------------------


def test_the_speaker_name_never_reaches_the_manifest(tmp_path: Path) -> None:
    labels = [
        _label(speaker="LJS", file_name="a.wav"),
        _label(speaker="KIM", file_name="b.wav", gender="Male"),
    ]
    manifest = build_prompts.build(
        _audio_tree(tmp_path, ["a.wav", "b.wav"]),
        _label_zip(tmp_path, labels),
        tmp_path / "out",
        count=2,
    )

    text = json.dumps(manifest, ensure_ascii=False)
    assert "LJS" not in text
    assert "KIM" not in text
    assert manifest["speaker_names_stored"] is False


def test_the_prompt_file_is_named_by_digest(tmp_path: Path) -> None:
    manifest = build_prompts.build(
        _audio_tree(tmp_path, ["a.wav"]),
        _label_zip(tmp_path, [_label(speaker="LJS", file_name="a.wav")]),
        tmp_path / "out",
        count=1,
    )

    name = manifest["prompt_voices"][0]["audio_relative_path"]
    assert name == f"{build_prompts.speaker_digest('LJS')}.wav"
    assert not (tmp_path / "out" / "a.wav").exists()


def test_two_speakers_sharing_initials_stay_two_speakers(tmp_path: Path) -> None:
    """`SpeakerName` is initials, and 1,529 speakers do not have 1,529 of those.

    Merging them would hand one digest to two people and report speaker spread the
    bank does not have.
    """

    labels = [
        _label(speaker="JHJ", file_name="a.wav", directory="/0049_G1A2E7_JHJ"),
        _label(speaker="JHJ", file_name="b.wav", directory="/1204_G1A2E7_JHJ"),
    ]

    manifest = build_prompts.build(
        _audio_tree(tmp_path, ["a.wav", "b.wav"]),
        _label_zip(tmp_path, labels),
        tmp_path / "out",
        count=2,
    )

    axes = {entry["speaker_axis"] for entry in manifest["prompt_voices"]}
    assert len(axes) == 2
    assert manifest["selected"] == 2
    assert "JHJ" not in json.dumps(manifest, ensure_ascii=False)


def test_the_enrolment_number_is_what_identifies_a_speaker() -> None:
    same_initials = build_prompts.speaker_key(
        {"SpeakerName": "JHJ"}, {"DirectoryPath": "/0049_G1A2E7_JHJ"}
    )
    other_person = build_prompts.speaker_key(
        {"SpeakerName": "JHJ"}, {"DirectoryPath": "/1204_G1A2E7_JHJ"}
    )

    assert same_initials != other_person
    # Corpora without a directory still have to key on something.
    assert build_prompts.speaker_key({"SpeakerName": "JHJ"}, {}) == "JHJ"


def test_the_digest_is_namespaced_and_stable() -> None:
    first = build_prompts.speaker_digest("LJS")

    assert first == build_prompts.speaker_digest("LJS")
    assert first != build_prompts.speaker_digest("KIM")
    assert len(first) == 64


# -- selection ----------------------------------------------------------------


def test_speakers_are_spread_across_demographics(tmp_path: Path) -> None:
    """The corpus is ordered by speaker, so the first N would be one group."""

    labels = [
        _label(speaker=f"F{i}", file_name=f"f{i}.wav", gender="Female", age="30~39")
        for i in range(6)
    ] + [
        _label(speaker=f"M{i}", file_name=f"m{i}.wav", gender="Male", age="20~29")
        for i in range(6)
    ]
    files = [f"f{i}.wav" for i in range(6)] + [f"m{i}.wav" for i in range(6)]

    manifest = build_prompts.build(
        _audio_tree(tmp_path, files), _label_zip(tmp_path, labels), tmp_path / "out",
        count=4,
    )

    genders = {entry["gender"] for entry in manifest["prompt_voices"]}
    assert genders == {"Female", "Male"}


def test_six_speakers_over_six_age_bands_each_are_not_all_one_gender(
    tmp_path: Path,
) -> None:
    """The shape 542 actually has: six age bands per gender.

    Walking sorted (gender, age) groups exhausts one gender's bands before reaching
    the other, so a request for six returned six women from the real corpus.
    """

    bands = ["10~19", "20~29", "30~39", "40~49", "50~59", "60~69"]
    labels = []
    files = []
    for gender in ("Female", "Male"):
        for band in bands:
            name = f"{gender[0]}{band[:2]}.wav"
            labels.append(
                _label(
                    speaker=f"{gender[0]}{band[:2]}",
                    file_name=name,
                    gender=gender,
                    age=band,
                )
            )
            files.append(name)

    manifest = build_prompts.build(
        _audio_tree(tmp_path, files),
        _label_zip(tmp_path, labels),
        tmp_path / "out",
        count=6,
    )

    genders = [entry["gender"] for entry in manifest["prompt_voices"]]
    assert genders.count("Female") == 3
    assert genders.count("Male") == 3
    assert len({entry["age_band"] for entry in manifest["prompt_voices"]}) == 3


def test_the_group_order_alternates_gender() -> None:
    order = build_prompts.group_order(
        [("Female", "10~19"), ("Female", "20~29"), ("Male", "10~19"), ("Male", "20~29")]
    )

    assert [key[0] for key in order] == ["Female", "Male", "Female", "Male"]


def test_a_single_gender_corpus_still_orders_every_age_band() -> None:
    """VS1 is male-only; the interleave must not drop groups when there is one."""

    order = build_prompts.group_order(
        [("Male", "20~29"), ("Male", "10~19"), ("Male", "30~39")]
    )

    assert order == [("Male", "10~19"), ("Male", "20~29"), ("Male", "30~39")]


def test_one_utterance_per_speaker(tmp_path: Path) -> None:
    labels = [
        _label(speaker="A", file_name="a1.wav", length=4.0),
        _label(speaker="A", file_name="a2.wav", length=5.0),
        _label(speaker="B", file_name="b1.wav", gender="Male"),
    ]
    manifest = build_prompts.build(
        _audio_tree(tmp_path, ["a1.wav", "a2.wav", "b1.wav"]),
        _label_zip(tmp_path, labels),
        tmp_path / "out",
        count=2,
    )

    axes = [entry["speaker_axis"] for entry in manifest["prompt_voices"]]
    assert len(axes) == len(set(axes)) == 2


# -- prompt length is bounded by the prompt transcript ------------------------


def test_the_utterance_closest_to_the_preferred_length_wins_not_the_longest(
    tmp_path: Path,
) -> None:
    """"Longest available" is what made the model run past the end of the sentence.

    A 45-syllable prompt rendered a three-second R00 sentence as 12-18 seconds of
    audio, and CosyVoice warns for exactly this: the text to synthesise must not be
    shorter than half the prompt text.
    """

    labels = [
        # 20 syllables, at the top of the window.
        _label(
            speaker="A",
            file_name="long.wav",
            length=5.0,
            text="갓길 공간 남는 곳에 차를 조심히 멈춰 세웁니다.",
        ),
        # 16 syllables, the preferred length.
        _label(speaker="A", file_name="pref.wav", length=3.2),
    ]

    manifest = build_prompts.build(
        _audio_tree(tmp_path, ["long.wav", "pref.wav"]),
        _label_zip(tmp_path, labels),
        tmp_path / "out",
        count=1,
    )

    entry = manifest["prompt_voices"][0]
    assert entry["prompt_syllable_count"] == build_prompts.PREFERRED_PROMPT_SYLLABLES
    assert entry["length_seconds"] == 3.2


@pytest.mark.parametrize(
    "text",
    [
        "짧은 문장이다",
        "정말 아주 길고 긴 문장을 여기에 적어서 창을 확실히 넘겨 버리도록 만듭니다",
    ],
)
def test_a_transcript_outside_the_syllable_window_is_skipped(
    tmp_path: Path, text: str
) -> None:
    with pytest.raises(build_prompts.PromptSelectionError):
        build_prompts.build(
            _audio_tree(tmp_path, ["a.wav"]),
            _label_zip(tmp_path, [_label(speaker="A", file_name="a.wav", text=text)]),
            tmp_path / "out",
            count=1,
        )


def test_the_window_is_bounded_by_twice_the_shortest_r00_sentence() -> None:
    """The bound is derived from the sentences the bank renders, not chosen."""

    import json

    card = json.loads(
        (ROOT / "data/reference/r00/r00_sentence_card_v1.json").read_text(
            encoding="utf-8"
        )
    )
    shortest = min(item["syllable_count"] for item in card["sentences"])

    assert build_prompts.MAXIMUM_PROMPT_SYLLABLES <= 2 * shortest
    assert (
        build_prompts.MINIMUM_PROMPT_SYLLABLES
        <= build_prompts.PREFERRED_PROMPT_SYLLABLES
        <= build_prompts.MAXIMUM_PROMPT_SYLLABLES
    )


@pytest.mark.parametrize(
    "override",
    [
        {"quality": "Bad"},
        {"sample_rate": "16000"},
        {"channels": "2"},
        {"length": 1.0},
        {"length": 30.0},
    ],
)
def test_unusable_rows_are_skipped(tmp_path: Path, override: dict) -> None:
    labels = [_label(speaker="A", file_name="a.wav", **override)]

    with pytest.raises(build_prompts.PromptSelectionError):
        build_prompts.build(
            _audio_tree(tmp_path, ["a.wav"]),
            _label_zip(tmp_path, labels),
            tmp_path / "out",
            count=1,
        )


def test_asking_for_more_speakers_than_exist_fails_closed(tmp_path: Path) -> None:
    labels = [_label(speaker="A", file_name="a.wav")]

    with pytest.raises(build_prompts.PromptSelectionError, match="only 1"):
        build_prompts.build(
            _audio_tree(tmp_path, ["a.wav"]),
            _label_zip(tmp_path, labels),
            tmp_path / "out",
            count=4,
        )


def test_a_missing_audio_file_is_counted_not_guessed(tmp_path: Path) -> None:
    labels = [
        _label(speaker="A", file_name="present.wav"),
        _label(speaker="B", file_name="absent.wav", gender="Male"),
    ]

    manifest = build_prompts.build(
        _audio_tree(tmp_path, ["present.wav"]),
        _label_zip(tmp_path, labels),
        tmp_path / "out",
        count=2,
    )

    assert manifest["selected"] == 1
    assert manifest["audio_not_found"] == 1


def test_an_absent_audio_root_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(build_prompts.PromptSelectionError, match="audio root"):
        build_prompts.build(
            tmp_path / "nope",
            _label_zip(tmp_path, [_label(speaker="A", file_name="a.wav")]),
            tmp_path / "out",
            count=1,
        )


# -- rights are carried on every entry ---------------------------------------


def test_every_entry_states_its_rights_basis_and_cloning_flag(
    tmp_path: Path,
) -> None:
    manifest = build_prompts.build(
        _audio_tree(tmp_path, ["a.wav"]),
        _label_zip(tmp_path, [_label(speaker="A", file_name="a.wav")]),
        tmp_path / "out",
        count=1,
    )
    entry = manifest["prompt_voices"][0]

    assert entry["third_party_speaker_cloning"] is True
    assert "DEV-R01-002" in entry["rights_basis"]
    assert "not redistributed" in entry["rights_basis"]
    assert manifest["decision_record"] == "DEV-R01-002"


def test_a_directory_sharing_the_name_is_not_taken_for_audio(tmp_path: Path) -> None:
    """rglob matches directories too; copying one would fail late."""

    root = tmp_path / "audio"
    (root / "a.wav").mkdir(parents=True)

    assert build_prompts.locate_audio(root, {"file_name": "a.wav"}) is None


# -- reading the corpus archive in place --------------------------------------


def _audio_zip(tmp_path: Path, file_names: list[str], *, prefix: str) -> Path:
    """An archive shaped like the corpus: Korean directories, ASCII file names."""

    tree = _audio_tree(tmp_path / "staging", file_names)
    archive = tmp_path / "VS.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w") as handle:
        for name in file_names:
            handle.write(tree / "session01" / name, f"{prefix}/{name}")
    return archive


def test_a_speaker_is_extracted_from_the_archive_without_unpacking_it(
    tmp_path: Path,
) -> None:
    """36 GB of corpus for six files is not a copy this can afford to make."""

    archive = _audio_zip(tmp_path, ["a.wav"], prefix="VS1/1.남성/2400문장/0049_JHJ")

    manifest = build_prompts.build(
        archive,
        _label_zip(tmp_path, [_label(speaker="A", file_name="a.wav")]),
        tmp_path / "out",
        count=1,
    )

    produced = tmp_path / "out" / manifest["prompt_voices"][0]["audio_relative_path"]
    assert sf.info(str(produced)).samplerate == 48_000
    assert not (tmp_path / "out" / "a.wav").exists()


def test_the_archive_path_finds_members_under_any_directory_depth(
    tmp_path: Path,
) -> None:
    archive = _audio_zip(tmp_path, ["a.wav", "b.wav"], prefix="VS1/x/y/z/spk")

    manifest = build_prompts.build(
        archive,
        _label_zip(
            tmp_path,
            [
                _label(speaker="A", file_name="a.wav"),
                _label(speaker="B", file_name="b.wav", gender="Male"),
            ],
        ),
        tmp_path / "out",
        count=2,
    )

    assert manifest["selected"] == 2


def test_a_member_absent_from_the_archive_is_counted_not_guessed(
    tmp_path: Path,
) -> None:
    archive = _audio_zip(tmp_path, ["present.wav"], prefix="VS1/spk")

    manifest = build_prompts.build(
        archive,
        _label_zip(
            tmp_path,
            [
                _label(speaker="A", file_name="present.wav"),
                _label(speaker="B", file_name="absent.wav", gender="Male"),
            ],
        ),
        tmp_path / "out",
        count=2,
    )

    assert manifest["selected"] == 1
    assert manifest["audio_not_found"] == 1


def test_two_gendered_archives_are_pooled_into_one_prompt_set(tmp_path: Path) -> None:
    """542 splits its archives by gender, so one archive is one gender."""

    male = tmp_path / "male"
    female = tmp_path / "female"
    male_labels = _label_zip(
        male,
        [_label(speaker="M1", file_name="m1.wav", gender="Male", age="20~29")],
    )
    female_labels = _label_zip(
        female,
        [_label(speaker="F1", file_name="f1.wav", gender="Female", age="30~39")],
    )

    manifest = build_prompts.build(
        _audio_tree(male, ["m1.wav"]),
        male_labels,
        tmp_path / "out",
        count=2,
        extra_sources=[(_audio_tree(female, ["f1.wav"]), female_labels)],
    )

    assert {e["gender"] for e in manifest["prompt_voices"]} == {"Male", "Female"}
    assert manifest["selected"] == 2


def test_a_pooled_speaker_is_read_from_its_own_archive(tmp_path: Path) -> None:
    """Pooling two sources must not look for one source's file in the other."""

    male = tmp_path / "male"
    female = tmp_path / "female"

    manifest = build_prompts.build(
        _audio_zip(male, ["m1.wav"], prefix="VS1/1.남성/2400문장/0049_ABC"),
        _label_zip(male, [_label(speaker="M1", file_name="m1.wav", gender="Male")]),
        tmp_path / "out",
        count=2,
        extra_sources=[
            (
                _audio_zip(female, ["f1.wav"], prefix="VS5/2.여성/2400문장/1529_XYZ"),
                _label_zip(
                    female,
                    [_label(speaker="F1", file_name="f1.wav", gender="Female")],
                ),
            )
        ],
    )

    assert manifest["selected"] == 2
    assert manifest["audio_not_found"] == 0


def test_a_non_zip_file_is_not_accepted_as_an_audio_root(tmp_path: Path) -> None:
    plain = tmp_path / "notes.txt"
    plain.write_text("not a corpus", encoding="utf-8")

    with pytest.raises(build_prompts.PromptSelectionError, match="audio root"):
        build_prompts.open_audio_source(plain)
