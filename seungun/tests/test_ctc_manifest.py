from korean_phone_ctc.data.ctc_manifest import (
    normalize_observed_intervals,
    prepare_ctc_record,
)


def test_normalize_observed_intervals_is_conservative() -> None:
    intervals = [
        {"label": ""},
        {"label": "-"},
        {"label": "ㄱ ㅏ"},
        {"label": "ㄴㅡㄴ"},
        {"label": "spn"},
    ]
    phones, error = normalize_observed_intervals(intervals)
    assert error is None
    assert phones == ["ㄱ", "ㅏ", "ㄴ", "ㅡ", "ㄴ", "spn"]
    assert normalize_observed_intervals([{"label": "가"}]) == (
        None,
        "unresolved_textgrid_label",
    )


def test_prepare_ctc_record_rejects_qc_and_long_audio() -> None:
    raw = {"id": "u1"}
    base = {"id": "u1", "qc_flags": ["bad"]}
    assert (
        prepare_ctc_record(base, raw, max_duration_seconds=20).exclusion_reason
        == "source_qc_flag"
    )
    base = {"id": "u1", "qc_flags": [], "duration_seconds": 21}
    assert (
        prepare_ctc_record(base, raw, max_duration_seconds=20).exclusion_reason
        == "audio_too_long"
    )


def test_prepare_ctc_record_builds_expected_fields() -> None:
    enriched = {
        "id": "u1",
        "speaker_id": "speaker-hash",
        "split": "locked_test",
        "duration_seconds": 1.5,
        "sample_rate_hz": 48_000,
        "pcm_sha256": "abc",
        "realized_phone_intervals": [{"label": "ㄱ"}, {"label": "ㅏ"}],
        "error_presence": True,
        "qc_flags": [],
    }
    raw = {
        "id": "u1",
        "source_paths": {"wav": "audio/u1.wav"},
        "annotation_counts": {"error_tags": 2},
        "json_metadata": {
            "RecordingMetadata": {"prompt": "가"},
            "SpeakerMetadata": {
                "language": "Chinese",
                "nationality": "China",
                "gender": "F",
                "proficiency": "Advance",
            },
            "EvaluationMetadata": {"PronunProfEval": 4},
        },
    }
    result = prepare_ctc_record(enriched, raw, max_duration_seconds=20)
    assert result.exclusion_reason is None
    assert result.record == {
        "utterance_id": "u1",
        "speaker_id": "speaker-hash",
        "split": "test",
        "source_split": "locked_test",
        "audio_path": "audio/u1.wav",
        "duration_seconds": 1.5,
        "sample_rate_hz": 48_000,
        "pcm_sha256": "abc",
        "prompt": "가",
        "phones": ["ㄱ", "ㅏ"],
        "error_presence": True,
        "error_tag_count": 2,
        "language": "Chinese",
        "nationality": "China",
        "gender": "F",
        "proficiency": "Advance",
        "pronunciation_score": 4,
    }
