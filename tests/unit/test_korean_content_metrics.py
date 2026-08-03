from __future__ import annotations

import pytest

from voice_coach.evaluation.korean_content_metrics import (
    KoreanContentMetricError,
    decide_content_mismatch,
    korean_content_metrics,
    levenshtein_distance,
    normalize_korean_transcript,
)


def test_normalization_matches_frozen_s201_order() -> None:
    decomposed_ga = "\u1100\u1161"
    normalized = normalize_korean_transcript(
        f"  {decomposed_ga},\tABC！  12  "
    )

    assert normalized == "가 abc 12"


def test_normalization_does_not_rewrite_digits_spelling_or_pronunciation() -> None:
    assert normalize_korean_transcript("10값 읽기") == "10값 읽기"


def test_strict_utf8_and_empty_reference_fail_closed() -> None:
    with pytest.raises(KoreanContentMetricError, match="strict UTF-8"):
        normalize_korean_transcript(b"\xff")
    with pytest.raises(KoreanContentMetricError, match="no word units"):
        korean_content_metrics("!!!", "안녕")


def test_levenshtein_and_rates_are_exact() -> None:
    assert levenshtein_distance(("가", "나"), ("가", "다", "나")) == 1

    metrics = korean_content_metrics("안녕 세상", "안녕 좋은 세상")

    assert metrics.wer.edit_distance == 1
    assert metrics.wer.reference_unit_count == 2
    assert metrics.wer.hypothesis_unit_count == 3
    assert metrics.wer.rate == 0.5
    assert metrics.cer.edit_distance == 2
    assert metrics.cer.reference_unit_count == 4
    assert metrics.cer.hypothesis_unit_count == 6
    assert metrics.cer.rate == 0.5


def test_metric_result_contains_no_transcript_content() -> None:
    metrics = korean_content_metrics("비공개 기준 원문", "비공개 가설 원문")
    serialized = str(metrics.to_dict())

    assert "비공개" not in serialized
    assert "원문" not in serialized


def test_threshold_decision_requires_explicit_valid_values() -> None:
    metrics = korean_content_metrics("안녕 세상", "안녕")

    decision = decide_content_mismatch(
        metrics,
        wer_threshold=0.4,
        cer_threshold=0.9,
    )

    assert decision.content_mismatch is True
    assert decision.wer_exceeds_threshold is True
    assert decision.cer_exceeds_threshold is False
    with pytest.raises(KoreanContentMetricError, match="wer_threshold"):
        decide_content_mismatch(
            metrics,
            wer_threshold=float("nan"),
            cer_threshold=0.5,
        )
