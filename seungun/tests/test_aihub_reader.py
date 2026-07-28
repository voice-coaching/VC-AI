import json

from korean_phone_ctc.data.aihub_reader import (
    PhoneInterval,
    parse_json_record,
    parse_textgrid_tiers,
)

TEXTGRID = """File type = "ooTextFile"
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
            text = "-"
"""


def test_parse_json_record() -> None:
    payload = {
        "UserID": "speaker",
        "SpeakerMetadata": {"language": "Thai"},
        "RecordingMetadata": {
            "prompt": "가",
            "RecordedTime": 0.5,
            "phonemic": {
                "phones": [{"phone": "ㄱ", "start": 0, "end": 0.2}],
                "error_tags": [{"error_tag": "초성", "start": 0, "end": 0.2}],
            },
        },
        "EvaluationMetadata": {"PronunProfEval": 3},
    }
    record = parse_json_record(
        json.dumps(payload, ensure_ascii=False).encode(), utterance_id="u1"
    )
    assert record.speaker_id == "speaker"
    assert record.phones == (PhoneInterval("ㄱ", 0.0, 0.2),)
    assert record.error_tags[0]["error_tag"] == "초성"


def test_parse_textgrid_phone_tier() -> None:
    assert parse_textgrid_tiers(TEXTGRID)["phones"] == [
        PhoneInterval("ㄱ", 0.0, 0.2),
        PhoneInterval("-", 0.2, 0.4),
    ]
