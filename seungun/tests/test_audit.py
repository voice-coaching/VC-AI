import json
import struct
import wave
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from korean_phone_ctc.data.audit import run_audit


def _json_payload(index: int, *, error: bool) -> bytes:
    payload = {
        "UserID": f"s{index % 2}",
        "SpeakerMetadata": {
            "gender": "F",
            "nationality": "Test",
            "language": "TestLanguage",
            "proficiency": "Intermediate",
        },
        "RecordingMetadata": {
            "prompt": "가",
            "RecordedTime": 0.4,
            "phonemic": {
                "phones": [
                    {"phone": "ㄱ", "start": 0.0, "end": 0.2},
                    {"phone": "ㅏ", "start": 0.2, "end": 0.4},
                ],
                "error_tags": (
                    [{"error_tag": "모음오류", "start": 0.2, "end": 0.4}]
                    if error
                    else []
                ),
            },
        },
        "EvaluationMetadata": {},
    }
    return json.dumps(payload, ensure_ascii=False).encode()


def _textgrid(*, error: bool) -> str:
    second = "ㅓ" if error else "ㅏ"
    return f'''File type = "ooTextFile"
Object class = "TextGrid"
item []:
    item [1]:
        class = "IntervalTier"
        name = "phones"
        intervals: size = 2
        intervals [1]:
            xmin = 0
            xmax = 0.2
            text = "ㄱ"
        intervals [2]:
            xmin = 0.2
            xmax = 0.4
            text = "{second}"
'''


def _wav(path: Path) -> bytes:
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(struct.pack("<320h", *([0] * 320)))
    return path.read_bytes()


def test_audit_detects_expected_json_and_observed_textgrid(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    root.mkdir()
    wav_payload = _wav(tmp_path / "sample.wav")
    with (
        ZipFile(root / "VS_pronunciation_sound.zip", "w", ZIP_DEFLATED) as audio,
        ZipFile(root / "VL_pronunciation_lab.zip", "w", ZIP_DEFLATED) as labels,
        ZipFile(root / "VL_pronunciation_tag.zip", "w", ZIP_DEFLATED) as tags,
    ):
        for index in range(3):
            stem = f"speaker-RW-{index}"
            error = index > 0
            audio.writestr(f"{stem}.wav", wav_payload)
            labels.writestr(f"{stem}.json", _json_payload(index, error=error))
            tags.writestr(f"{stem}.TextGrid", _textgrid(error=error))

    output = tmp_path / "artifacts"
    report = run_audit(
        root,
        output,
        partitions=("Validation",),
        sample_error_count=2,
        verify_crc=True,
    )
    assert report["matched_triplets"] == 3
    assert report["label_semantics"]["conclusion"] == (
        "json_expected_textgrid_observed"
    )
    assert report["label_semantics"]["ctc_target_allowed"] is False
    assert (output / "data_audit.json").is_file()
    assert (output / "sample_error_cases.jsonl").is_file()
