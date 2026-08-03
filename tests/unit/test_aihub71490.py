import json
from pathlib import Path

from voice_coach.data import load_aihub71490_annotations


def test_load_aihub71490_annotations(tmp_path: Path) -> None:
    path = tmp_path / "label.json"
    path.write_text(
        json.dumps(
            {
                "RecordingMetadata": {
                    "phonemic": {
                        "words": [{"word": "안녕", "start": 0.1, "end": 0.8}],
                        "phones": [
                            {"phone": "ㅇ", "start": 0.1, "end": 0.2}
                        ],
                        "error_tags": [
                            {"error_tag": "종성", "start": 0.15, "end": 0.2}
                        ],
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = load_aihub71490_annotations(path)
    assert result["words"][0].label == "안녕"
    assert result["target_phones"][0].label == "ㅇ"
    assert result["error_tags"][0].label == "종성"
