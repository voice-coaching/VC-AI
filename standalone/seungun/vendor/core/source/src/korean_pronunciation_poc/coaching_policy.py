from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import PocConfig
from .llm_feedback_payload import LlmEvidenceItem, LlmFeedbackPayload
from .reference_comparison import (
    PopulationId,
    ReferenceComparisonResult,
    ReferenceMetricComparison,
    ReferencePopulationComparison,
)
from .schemas import AnalysisResult

if TYPE_CHECKING:
    from .llm_render_payload import LlmRenderPayload


PercentileBand = Literal[
    "lower_extreme",
    "lower_band",
    "middle_band",
    "upper_band",
    "upper_extreme",
]
DirectiveCategory = Literal["speed", "pause", "f0", "intensity"]
DirectiveId = Literal[
    "COMPARE_SLOWER_RETAKE",
    "COMPARE_FASTER_RETAKE",
    "COMPARE_SEMANTIC_PAUSE_RETAKE",
    "COMPARE_CONNECTED_RETAKE",
    "COMPARE_KEYWORD_EMPHASIS_RETAKE",
    "COMPARE_REDUCED_PITCH_VARIATION_RETAKE",
    "COMPARE_KEYWORD_INTENSITY_RETAKE",
    "COMPARE_EVEN_INTENSITY_RETAKE",
]
_Direction = Literal["lower", "upper"]


class _InternalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CoachingDirective(_InternalModel):
    directive_id: DirectiveId
    category: DirectiveCategory
    title_ko: str = Field(min_length=1)
    instruction_ko: str = Field(min_length=1)
    rationale_ko: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    comparison_population: PopulationId
    priority: int = Field(ge=1, le=4)
    interpretation_scope: str = Field(min_length=1)


class CoachingPolicyResult(_InternalModel):
    analysis_id: UUID
    directives: tuple[CoachingDirective, ...] = Field(max_length=3)
    measurement_notes: tuple[str, ...]
    limitations: tuple[str, ...] = Field(min_length=1)
    prohibited_claims: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_directives(self) -> CoachingPolicyResult:
        categories = [directive.category for directive in self.directives]
        if len(categories) != len(set(categories)):
            raise ValueError("each directive category may appear at most once")
        priorities = [directive.priority for directive in self.directives]
        if priorities != sorted(priorities):
            raise ValueError("directives must follow fixed priority order")
        text = " ".join(
            part
            for directive in self.directives
            for part in (
                directive.title_ko,
                directive.instruction_ko,
                directive.rationale_ko,
            )
        )
        if any(phrase in text for phrase in FORBIDDEN_DIRECTIVE_PHRASES):
            raise ValueError("directive contains a prohibited expression")
        return self


@dataclass(frozen=True)
class CoachingPayloadPaths:
    policy_path: Path
    render_path: Path


@dataclass(frozen=True)
class _DirectiveSpec:
    directive_id: DirectiveId
    category: DirectiveCategory
    title_ko: str
    instruction_ko: str
    priority: int


FORBIDDEN_DIRECTIVE_PHRASES = (
    "향상시키세요",
    "교정하세요",
    "더 좋은 발음",
    "올바른 억양",
    "부족합니다",
    "발음이 정확",
    "발음이 부정확",
    "아나운서보다 우수",
    "아나운서보다 열등",
    "합격",
    "불합격",
    "점수",
    "등급",
)
_INTERPRETATION_SCOPE = (
    "품질 판정이나 정답 교정이 아니라 현재 예비 참조 표본과 다른 낭독 "
    "버전을 녹음해 측정값을 비교하는 실험입니다."
)
_SPECS: dict[tuple[DirectiveCategory, _Direction], _DirectiveSpec] = {
    ("speed", "upper"): _DirectiveSpec(
        "COMPARE_SLOWER_RETAKE",
        "speed",
        "소요시간을 늘린 비교 녹음",
        (
            "현재 녹음보다 목표 소요시간을 약 10% 늘린 버전을 한 번 더 "
            "녹음하고, 말속도·조음속도와 문장 전달감을 비교하세요."
        ),
        1,
    ),
    ("speed", "lower"): _DirectiveSpec(
        "COMPARE_FASTER_RETAKE",
        "speed",
        "소요시간을 줄인 비교 녹음",
        (
            "현재 녹음보다 목표 소요시간을 약 10% 줄인 버전을 한 번 더 "
            "녹음하고, 말속도·조음속도와 문장 전달감을 비교하세요."
        ),
        1,
    ),
    ("pause", "lower"): _DirectiveSpec(
        "COMPARE_SEMANTIC_PAUSE_RETAKE",
        "pause",
        "의미 단위 쉼 비교 녹음",
        (
            "문장의 의미 단위 경계 한 곳을 정해 짧은 쉼을 넣은 버전을 "
            "녹음하고, 기존 버전과 말속도·쉼 위치를 비교하세요."
        ),
        2,
    ),
    ("pause", "upper"): _DirectiveSpec(
        "COMPARE_CONNECTED_RETAKE",
        "pause",
        "내부 쉼 연결 비교 녹음",
        (
            "가장 긴 내부 쉼 한 곳을 조금 짧게 연결한 버전을 녹음하고, "
            "기존 버전과 쉼 시간·조음속도를 비교하세요."
        ),
        2,
    ),
    ("f0", "lower"): _DirectiveSpec(
        "COMPARE_KEYWORD_EMPHASIS_RETAKE",
        "f0",
        "핵심 단어 음높이 비교 녹음",
        (
            "핵심 단어 한 개를 정해 음높이를 약간 다르게 강조한 버전을 "
            "녹음하고, 기존 버전과 F0 변화 폭을 비교하세요."
        ),
        3,
    ),
    ("f0", "upper"): _DirectiveSpec(
        "COMPARE_REDUCED_PITCH_VARIATION_RETAKE",
        "f0",
        "음높이 변화 폭 비교 녹음",
        (
            "동일 문장을 음높이 변화 폭을 조금 줄인 버전으로 녹음하고, "
            "기존 버전과 F0 변화 폭을 비교하세요."
        ),
        3,
    ),
    ("intensity", "lower"): _DirectiveSpec(
        "COMPARE_KEYWORD_INTENSITY_RETAKE",
        "intensity",
        "핵심 단어 음량 비교 녹음",
        (
            "핵심 단어 한 개의 음량만 약간 다르게 처리한 버전을 녹음하고, "
            "기존 버전과 발화 구간 음량 변화 폭을 비교하세요."
        ),
        4,
    ),
    ("intensity", "upper"): _DirectiveSpec(
        "COMPARE_EVEN_INTENSITY_RETAKE",
        "intensity",
        "음량 변화 폭 비교 녹음",
        (
            "문장 전체 음량 변화를 조금 고르게 한 버전을 녹음하고, "
            "기존 버전과 음량 변화 폭을 비교하세요."
        ),
        4,
    ),
}
_CATEGORY_METRICS: dict[DirectiveCategory, tuple[str, ...]] = {
    "speed": ("speaking_rate", "articulation_rate"),
    "pause": (
        "internal_pause_count_per_10_syllables",
        "internal_pause_duration_per_10_syllables",
    ),
    "f0": ("f0_semitone_percentile_range",),
    "intensity": ("speech_intensity_percentile_range",),
}


def classify_percentile(percentile: float) -> PercentileBand:
    if not 0.0 <= percentile <= 100.0:
        raise ValueError("percentile must be in the 0 to 100 range")
    if percentile < 10.0:
        return "lower_extreme"
    if percentile < 25.0:
        return "lower_band"
    if percentile <= 75.0:
        return "middle_band"
    if percentile <= 90.0:
        return "upper_band"
    return "upper_extreme"


def _population(
    comparison: ReferenceComparisonResult,
    population_id: PopulationId,
) -> ReferencePopulationComparison:
    try:
        return next(
            item
            for item in comparison.comparisons
            if item.population_id == population_id
        )
    except StopIteration as exc:
        raise ValueError(f"missing reference population: {population_id}") from exc


def _metrics(
    population: ReferencePopulationComparison,
) -> dict[str, ReferenceMetricComparison]:
    return {metric.metric_name: metric for metric in population.metrics}


def _directions(
    category: DirectiveCategory,
    metrics: dict[str, ReferenceMetricComparison],
) -> set[_Direction]:
    directions: set[_Direction] = set()
    for name in _CATEGORY_METRICS[category]:
        percentile = metrics[name].percentile
        if percentile is None:
            continue
        band = classify_percentile(percentile)
        if category == "speed":
            if band == "lower_extreme":
                directions.add("lower")
            elif band == "upper_extreme":
                directions.add("upper")
        else:
            if band in {"lower_extreme", "lower_band"}:
                directions.add("lower")
            elif band in {"upper_band", "upper_extreme"}:
                directions.add("upper")
    return directions


def _feature_value(analysis: AnalysisResult, name: str) -> float | None:
    feature = next(
        (item for item in analysis.acoustic_features if item.name == name),
        None,
    )
    if feature is None:
        return None
    value = feature.summary.get("value")
    return float(value) if value is not None else None


def _evidence_lookup(
    payload: LlmFeedbackPayload,
) -> dict[tuple[PopulationId, str], LlmEvidenceItem]:
    return {
        (item.reference_population, item.metric_name): item
        for item in payload.reference_comparisons
        if item.reference_population is not None
    }


def _directive(
    category: DirectiveCategory,
    direction: _Direction,
    population: ReferencePopulationComparison,
    evidence: dict[tuple[PopulationId, str], LlmEvidenceItem],
) -> CoachingDirective | None:
    items = [
        evidence[(population.population_id, name)]
        for name in _CATEGORY_METRICS[category]
        if (population.population_id, name) in evidence
        and evidence[(population.population_id, name)].percentile is not None
    ]
    if not items:
        return None
    spec = _SPECS[(category, direction)]
    return CoachingDirective(
        directive_id=spec.directive_id,
        category=category,
        title_ko=spec.title_ko,
        instruction_ko=spec.instruction_ko,
        rationale_ko=(
            f"{population.population_label}의 예비 참조 분포에서 관찰된 위치를 "
            "근거로 다른 낭독 버전을 비교하기 위한 지시입니다."
        ),
        evidence_ids=tuple(item.evidence_id for item in items),
        comparison_population=population.population_id,
        priority=spec.priority,
        interpretation_scope=_INTERPRETATION_SCOPE,
    )


def build_coaching_policy(
    analysis: AnalysisResult,
    comparison: ReferenceComparisonResult,
    feedback_payload: LlmFeedbackPayload,
) -> CoachingPolicyResult:
    if not (
        analysis.analysis_id == comparison.analysis_id == feedback_payload.analysis_id
    ):
        raise ValueError("analysis, comparison, and payload IDs must match")
    current = _population(comparison, "current_announcer")
    all_sample = _population(comparison, "all_anchor_sample")
    current_metrics = _metrics(current)
    all_metrics = _metrics(all_sample)
    evidence = _evidence_lookup(feedback_payload)
    directives: list[CoachingDirective] = []
    notes: list[str] = []

    for category in ("speed", "pause", "f0", "intensity"):
        current_directions = _directions(category, current_metrics)
        all_directions = _directions(category, all_metrics)
        if (
            len(current_directions) == 1
            and len(all_directions) == 1
            and current_directions != all_directions
        ):
            notes.append(
                f"{category} 지표는 두 참조 집단의 위치 방향이 상충하여 "
                "연습 지시를 생성하지 않았습니다."
            )
            continue
        selected_population = current
        directions = current_directions
        if all(
            current_metrics[name].percentile is None
            for name in _CATEGORY_METRICS[category]
        ):
            selected_population = all_sample
            directions = all_directions
        if len(directions) > 1:
            subject = (
                "말속도와 조음속도" if category == "speed" else f"{category} 관련 지표"
            )
            notes.append(
                f"{subject}의 참조 분포 위치 방향이 상충하여 연습 지시를 "
                "생성하지 않았습니다."
            )
            continue
        if not directions:
            continue
        direction = next(iter(directions))
        if (
            category == "pause"
            and direction == "upper"
            and _feature_value(analysis, "internal_pause_count") == 0.0
        ):
            notes.append(
                "내부 쉼이 0회이므로 쉼을 줄이는 연습 지시를 생성하지 않았습니다."
            )
            continue
        item = _directive(
            category,
            direction,
            selected_population,
            evidence,
        )
        if item is None:
            notes.append(
                f"{category} 지시에 연결할 유효한 evidence ID가 없어 "
                "연습 지시를 생성하지 않았습니다."
            )
            continue
        directives.append(item)

    coverage = _feature_value(analysis, "speech_f0_valid_ratio")
    if coverage is not None and coverage < 0.3:
        notes.append("유효 F0 프레임 비율이 낮아 음높이 비교 해석이 제한됩니다.")

    return CoachingPolicyResult(
        analysis_id=analysis.analysis_id,
        directives=tuple(directives[:3]),
        measurement_notes=tuple(notes),
        limitations=tuple(
            dict.fromkeys(
                (
                    *feedback_payload.limitations,
                    (
                        "모든 지시는 정답 교정이 아니라 현재 예비 표본과 다른 "
                        "버전을 녹음하여 비교하는 실험입니다."
                    ),
                )
            )
        ),
        prohibited_claims=feedback_payload.prohibited_claims,
    )


def _deterministic_json(payload: BaseModel) -> str:
    return (
        json.dumps(
            payload.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _write_temp(target_dir: Path, prefix: str, content: str) -> Path:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        prefix=prefix,
        suffix=".tmp",
        dir=target_dir,
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    return temporary


def save_coaching_payloads(
    policy: CoachingPolicyResult,
    render_payload: LlmRenderPayload,
    config: PocConfig,
) -> CoachingPayloadPaths:
    if policy.analysis_id != render_payload.analysis_id:
        raise ValueError("policy and render payload IDs must match")
    project_root = config.paths.project_root.resolve(strict=True)
    output_root = config.paths.output_dir.resolve(strict=False)
    if not output_root.is_relative_to(project_root):
        raise ValueError("output_dir must remain inside project_root")
    output_dir = output_root / "coaching" / str(policy.analysis_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved_dir = output_dir.resolve(strict=True)
    if not resolved_dir.is_relative_to(output_root):
        raise ValueError("coaching output escaped output_dir")
    policy_path = resolved_dir / "coaching_policy.json"
    render_path = resolved_dir / "llm_render_input.json"
    policy_temp: Path | None = None
    render_temp: Path | None = None
    try:
        policy_temp = _write_temp(
            resolved_dir,
            ".coaching-policy-",
            _deterministic_json(policy),
        )
        render_temp = _write_temp(
            resolved_dir,
            ".llm-render-input-",
            _deterministic_json(render_payload),
        )
        os.replace(policy_temp, policy_path)
        policy_temp = None
        os.replace(render_temp, render_path)
        render_temp = None
        return CoachingPayloadPaths(
            policy_path=policy_path,
            render_path=render_path,
        )
    finally:
        if policy_temp is not None:
            policy_temp.unlink(missing_ok=True)
        if render_temp is not None:
            render_temp.unlink(missing_ok=True)


def load_coaching_policy(path: Path) -> CoachingPolicyResult:
    return CoachingPolicyResult.model_validate_json(path.read_text(encoding="utf-8"))
