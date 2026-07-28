import json
import struct
import wave
from pathlib import Path

from korean_phone_ctc.data.textgrid_target_audit import (
    normalize_textgrid_label,
    run_textgrid_target_audit,
    split_known_phone_sequence,
)


def test_candidate_normalization_is_structural_only() -> None:
    assert normalize_textgrid_label("") == []
    assert normalize_textgrid_label("-") == []
    assert normalize_textgrid_label(" ㄱ ㅓ ") == ["ㄱ", "ㅓ"]
    assert normalize_textgrid_label("spn") == ["spn"]
    inventory = {"ㄱ", "ㅓ", "spn"}
    assert split_known_phone_sequence("ㄱㅓ", inventory) == (
        ["ㄱ", "ㅓ"],
        "known_concatenation",
    )
    assert split_known_phone_sequence("가", inventory) == (None, "unresolved")


def _write_partition(root: Path, partition: str, speaker: str) -> dict[str, Path]:
    directories = {
        modality: root / partition / modality
        for modality in ("wav", "json", "textgrid")
    }
    for directory in directories.values():
        directory.mkdir(parents=True)
    stem = f"{speaker}-RW001-1"
    with wave.open(str(directories["wav"] / f"{stem}.wav"), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(struct.pack("<320h", *([0] * 320)))
    payload = {
        "UserID": speaker,
        "SpeakerMetadata": {},
        "RecordingMetadata": {
            "prompt": "간",
            "RecordedTime": 0.3,
            "phonemic": {
                "phones": [
                    {"phone": "ㄱ", "start": 0, "end": 0.1},
                    {"phone": "ㅏ", "start": 0.1, "end": 0.2},
                    {"phone": "ㄴ", "start": 0.2, "end": 0.3},
                ],
                "error_tags": [],
            },
        },
        "EvaluationMetadata": {},
    }
    (directories["json"] / f"{stem}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    (directories["textgrid"] / f"{stem}.TextGrid").write_text(
        """File type = "ooTextFile"
Object class = "TextGrid"
item []:
    item [1]:
        class = "IntervalTier"
        name = "phones"
        intervals: size = 2
        intervals [1]:
            xmin = 0
            xmax = 0.2
            text = "ㄱ ㅏ"
        intervals [2]:
            xmin = 0.2
            xmax = 0.3
            text = "-"
""",
        encoding="utf-8",
    )
    return directories


def test_target_audit_detects_compounds_deletions_and_speaker_overlap(
    tmp_path: Path,
) -> None:
    partitions = {
        name: _write_partition(tmp_path, name, "speaker")
        for name in ("Training", "Validation")
    }
    report = run_textgrid_target_audit(
        partition_directories=partitions,
        output_dir=tmp_path / "artifacts",
        special_sample_limit=10,
        progress_every=0,
    )
    assert report["deletion_placeholder_count"] == 2
    assert report["compound_label_count"] == 2
    assert report["official_split_speaker_overlap_count"] == 1
    assert report["normalized_phone_inventory"] == ["ㄱ", "ㅏ"]
