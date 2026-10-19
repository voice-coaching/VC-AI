from __future__ import annotations

from collections.abc import Callable

from .errors import AnalysisStatus, PipelineStage
from .schemas import AcousticFeature, AnalysisResult, FeedbackItem


def _find_feature(
    result: AnalysisResult,
    name: str,
) -> tuple[int, AcousticFeature] | None:
    return next(
        (
            (index, feature)
            for index, feature in enumerate(result.acoustic_features)
            if feature.name == name
        ),
        None,
    )


def _measurement_item(
    result: AnalysisResult,
    *,
    feature_name: str,
    summary_key: str,
    rule_id: str,
    render: Callable[[float], str],
) -> FeedbackItem | None:
    found = _find_feature(result, feature_name)
    if found is None:
        return None
    index, feature = found
    value = feature.summary.get(summary_key)
    if value is None:
        return None
    return FeedbackItem(
        rule_id=rule_id,
        message_ko=render(value),
        level="info",
        evidence_paths=[
            f"/acoustic_features/{index}/summary/{summary_key}",
        ],
    )


def _f0_unavailable(result: AnalysisResult) -> FeedbackItem | None:
    if _find_feature(result, "f0_semitone_percentile_range") is not None:
        return None
    f0 = _find_feature(result, "f0_hz")
    valid_ratio = _find_feature(result, "f0_valid_ratio")
    evidence_paths: list[str] = []
    if f0 is not None and valid_ratio is not None:
        f0_index, f0_feature = f0
        ratio_index, ratio_feature = valid_ratio
        raw_unavailable = (
            "median" in f0_feature.summary
            and not any(value is not None for value in f0_feature.summary.values())
            and ratio_feature.summary.get("value") == 0.0
        )
        if raw_unavailable:
            evidence_paths = [
                f"/acoustic_features/{f0_index}/summary/median",
                f"/acoustic_features/{ratio_index}/summary/value",
            ]
        else:
            evidence_paths = [
                f"/acoustic_features/{ratio_index}/summary/value",
            ]
    else:
        evidence_paths = [
            f"/errors/{index}/code"
            for index, error in enumerate(result.errors)
            if error.stage is PipelineStage.ACOUSTIC
        ]
    if not evidence_paths:
        return None
    return FeedbackItem(
        rule_id="F0_UNAVAILABLE",
        message_ko=(
            "유효 발화 구간 기준의 강건한 F0 범위를 계산할 수 없어 제공하지 "
            "않습니다. 이는 품질 판정이 아니라 객관적 분석 결과입니다."
        ),
        level="info",
        evidence_paths=evidence_paths,
    )


def _alignment_unavailable(result: AnalysisResult) -> FeedbackItem | None:
    related = [
        index
        for index, error in enumerate(result.errors)
        if error.stage in {PipelineStage.ALIGNMENT, PipelineStage.TEXTGRID}
    ]
    if not related:
        return None
    return FeedbackItem(
        rule_id="ALIGNMENT_UNAVAILABLE",
        message_ko=(
            "정렬 또는 TextGrid 결과가 없어 정렬 기반 측정값을 제공하지 "
            "않습니다. 이는 품질 판정이 아니라 객관적 분석 결과입니다."
        ),
        level="info",
        evidence_paths=[f"/errors/{index}/code" for index in related],
    )


def generate_feedback(result: AnalysisResult) -> list[FeedbackItem]:
    if result.status is AnalysisStatus.FAILED:
        return []

    candidates = [
        _measurement_item(
            result,
            feature_name="utterance_duration",
            summary_key="value",
            rule_id="REPORT_DURATION",
            render=lambda value: (
                f"전체 발화시간은 {value:.3f}초입니다. 이는 품질 판정이 아니라 "
                "객관적 분석 결과입니다."
            ),
        ),
        _measurement_item(
            result,
            feature_name="speaking_rate",
            summary_key="value",
            rule_id="REPORT_SPEAKING_RATE",
            render=lambda value: (
                "스크립트 표기 기준 음절/초로 계산한 말속도는 "
                f"{value:.3f}입니다. 이는 품질 판정이 아니라 객관적 분석 "
                "결과입니다."
            ),
        ),
        _measurement_item(
            result,
            feature_name="f0_semitone_percentile_range",
            summary_key="value",
            rule_id="REPORT_F0_RANGE",
            render=lambda value: (
                "유효 발화 구간에서 화자별 F0 중앙값을 기준으로 계산한 "
                f"10~90백분위 음높이 범위는 {value:.3f} semitone입니다. "
                "이는 품질 판정이 아니라 객관적 분석 결과입니다."
            ),
        ),
        _f0_unavailable(result),
        _alignment_unavailable(result),
    ]
    return [item for item in candidates if item is not None]
