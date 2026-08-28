from pathlib import Path
import wave

import numpy as np

from voice_coach.data import (
    Interval,
    align_intervals,
    audit_manifest_records,
    parse_textgrid,
    pcm_sha256,
    stratified_group_split,
)


TEXTGRID = '''File type = "ooTextFile"
Object class = "TextGrid"

xmin = 0
xmax = 1
tiers? <exists>
size = 1
item []:
    item [1]:
        class = "IntervalTier"
        name = "phones"
        xmin = 0
        xmax = 1
        intervals: size = 3
        intervals [1]:
            xmin = 0
            xmax = 0.25
            text = "k"
        intervals [2]:
            xmin = 0.25
            xmax = 0.5
            text = ""
        intervals [3]:
            xmin = 0.5
            xmax = 1
            text = "a"
'''


def test_textgrid_parser_reads_interval_tier() -> None:
    tiers = parse_textgrid(TEXTGRID)
    assert tiers["phones"] == [
        Interval(0.0, 0.25, "k"),
        Interval(0.25, 0.5, ""),
        Interval(0.5, 1.0, "a"),
    ]


def test_temporal_alignment_preserves_substitution_deletion_and_insertion() -> None:
    targets = [
        Interval(0.0, 0.2, "k"),
        Interval(0.2, 0.4, "a"),
        Interval(0.4, 0.6, "n"),
    ]
    realized = [
        Interval(0.0, 0.2, "k"),
        Interval(0.2, 0.4, "o"),
        Interval(0.7, 0.8, "s"),
    ]
    operations = [
        result.operation
        for result in align_intervals(targets, realized, tolerance_seconds=0)
    ]
    assert operations == ["match", "substitution", "deletion", "insertion"]


def test_temporal_alignment_unique_mode_does_not_duplicate_neighbors() -> None:
    targets = [
        Interval(0.0, 0.1, "k"),
        Interval(0.1, 0.2, "a"),
        Interval(0.2, 0.3, "n"),
    ]
    realized = [
        Interval(0.0, 0.1, "k"),
        Interval(0.1, 0.2, "o"),
        Interval(0.2, 0.3, "n"),
    ]
    alignments = align_intervals(
        targets,
        realized,
        tolerance_seconds=0.03,
        preserve_one_to_many=False,
    )
    assert len(alignments) == 3
    assert [result.operation for result in alignments] == [
        "match",
        "substitution",
        "match",
    ]


def _write_wav(path: Path, samples: np.ndarray, *, extra_metadata: bool) -> None:
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(samples.astype("<i2").tobytes())
    if extra_metadata:
        payload = path.read_bytes()
        path.write_bytes(payload + b"JUNKmetadata-not-part-of-pcm")


def test_pcm_hash_ignores_trailing_riff_metadata(tmp_path: Path) -> None:
    samples = np.array([0, 1, -1, 300, -300], dtype=np.int16)
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    _write_wav(first, samples, extra_metadata=False)
    _write_wav(second, samples, extra_metadata=True)
    assert first.read_bytes() != second.read_bytes()
    assert pcm_sha256(first) == pcm_sha256(second)


def test_group_split_is_deterministic_and_speaker_disjoint() -> None:
    records = [
        {
            "speaker": f"s{speaker}",
            "language": language,
            "gender": gender,
            "error": index % 2,
        }
        for index, (speaker, language, gender) in enumerate(
            [
                (0, "zh", "f"),
                (0, "zh", "f"),
                (1, "zh", "m"),
                (2, "ja", "f"),
                (3, "ja", "m"),
                (4, "en", "f"),
                (5, "en", "m"),
                (6, "zh", "f"),
                (7, "ja", "m"),
                (8, "en", "f"),
            ]
        )
    ]
    kwargs = {
        "group_key": "speaker",
        "stratify_keys": ["language", "gender", "error"],
        "ratios": {"train": 0.6, "dev": 0.2, "test": 0.2},
        "seed": 17,
    }
    first = stratified_group_split(records, **kwargs)
    second = stratified_group_split(reversed(records), **kwargs)
    assert first == second
    assert set(first) == {f"s{index}" for index in range(9)}
    assert set(first.values()) == {"train", "dev", "test"}


def _manifest_record(
    record_id: str, speaker: str, split: str, pcm_hash: str
) -> dict:
    return {
        "id": record_id,
        "speaker_id": speaker,
        "split": split,
        "pcm_sha256": pcm_hash,
        "source_kind": "pronunciation",
        "source_paths": {"wav": "a.wav", "json": "a.json", "textgrid": "a.TextGrid"},
        "audio": {"duration_seconds": 1.0},
        "phone_intervals": [{"start": 0.0, "end": 0.9}],
        "qc": {"errors": {}},
    }


def test_manifest_audit_passes_disjoint_valid_records() -> None:
    report = audit_manifest_records(
        [
            _manifest_record("a", "s1", "train", "pcm1"),
            _manifest_record("b", "s2", "test", "pcm2"),
        ]
    )
    assert report["status"] == "passed"
    assert report["pairing_rate"] == 1.0


def test_manifest_audit_rejects_cross_split_pcm_and_speaker_overlap() -> None:
    report = audit_manifest_records(
        [
            _manifest_record("a", "s1", "train", "same"),
            _manifest_record("b", "s1", "test", "same"),
        ]
    )
    assert report["status"] == "failed"
    assert "cross_split_speaker_overlap" in report["failed_gates"]
    assert "cross_split_pcm_duplicate" in report["failed_gates"]


def test_temporal_alignment_unique_mode_prefers_true_overlap_over_tolerance_only_neighbor() -> None:
    targets = [
        Interval(0.33, 0.36, "ㅂ"),
        Interval(0.36, 0.39, "ㅓ"),
    ]
    realized = [
        Interval(0.33, 0.36, "ㅂ"),
        Interval(0.36, 0.39, "ㅓ"),
    ]

    alignments = align_intervals(
        targets,
        realized,
        tolerance_seconds=0.03,
        min_overlap_ratio=0.5,
        preserve_one_to_many=False,
    )

    assert [
        (
            result.target.label if result.target else None,
            result.realized.label if result.realized else None,
            result.operation,
        )
        for result in alignments
    ] == [
        ("ㅂ", "ㅂ", "match"),
        ("ㅓ", "ㅓ", "match"),
    ]
