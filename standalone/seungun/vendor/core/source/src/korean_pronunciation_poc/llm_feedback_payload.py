from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import PocConfig
from .errors import PipelineStage
from .reference_comparison import (
    PopulationId,
    ReferenceComparisonResult,
    ReferencePopulationComparison,
)
from .schemas import AnalysisResult, FiniteFloat


EvidenceCategory = Literal[
    "measurement",
    "reference_comparison",
    "coverage",
    "status",
]
EvidenceValue = bool | FiniteFloat | None
Percentile = Annotated[float, Field(ge=0.0, le=100.0, allow_inf_nan=False)]


class _InternalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LlmEvidenceItem(_InternalModel):
    evidence_id: str = Field(pattern=r"^EV_[A-Z0-9_]+$")
    category: EvidenceCategory
    metric_name: str = Field(min_length=1)
    value: EvidenceValue
    unit: str = Field(min_length=1)
    reference_population: PopulationId | None = None
    reference_count: int | None = Field(default=None, ge=0)
    reference_utterance_count: int | None = Field(default=None, ge=0)
    reference_speaker_count: int | None = Field(default=None, ge=0)
    reference_median: FiniteFloat | None = None
    delta_from_median: FiniteFloat | None = None
    percentile: Percentile | None = None
    interpretation_scope: str = Field(min_length=1)


class LlmAvailableFeedback(_InternalModel):
    rule_id: str = Field(min_length=1)
    message_ko: str = Field(min_length=1)
    level: Literal["info", "suggestion", "warning"]
    evidence_ids: tuple[str, ...] = Field(min_length=1)


class LlmModelProvenance(_InternalModel):
    role: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: str | None = None
    revision: str | None = None


class LlmProvenanceSummary(_InternalModel):
    analysis_schema_version: str = Field(min_length=1)
    platform_role: Literal["mac_smoke", "dlpc_formal", "test", "unknown"]
    operating_system: str = Field(min_length=1)
    architecture: str = Field(min_length=1)
    python_version: str = Field(min_length=1)
    models: tuple[LlmModelProvenance, ...]


class LlmFeedbackPayload(_InternalModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    analysis_id: UUID
    script: str = Field(min_length=1)
    display_pronunciation: str | None
    measurement_summary: tuple[LlmEvidenceItem, ...]
    reference_comparisons: tuple[LlmEvidenceItem, ...]
    available_feedback: tuple[LlmAvailableFeedback, ...]
    limitations: tuple[str, ...] = Field(min_length=1)
    prohibited_claims: tuple[str, ...] = Field(min_length=1)
    provenance_summary: LlmProvenanceSummary

    @model_validator(mode="after")
    def validate_evidence_links(self) -> LlmFeedbackPayload:
        evidence_ids = [
            item.evidence_id
            for item in (
                *self.measurement_summary,
                *self.reference_comparisons,
            )
        ]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_id values must be unique")
        available = set(evidence_ids)
        invalid = sorted(
            evidence_id
            for feedback in self.available_feedback
            for evidence_id in feedback.evidence_ids
            if evidence_id not in available
        )
        if invalid:
            raise ValueError(
                f"available_feedback references unknown evidence_id: {invalid}"
            )
        return self


@dataclass(frozen=True)
class _MeasurementSpec:
    evidence_id: str
    metric_name: str
    unit: str
    category: EvidenceCategory
    interpretation_scope: str


_OBJECTIVE_SCOPE = "객관적 측정값이며 품질·정확도·점수가 아닙니다."
_REFERENCE_SCOPE = (
    "참조 표본 내 기술적 위치이며 백분위가 높다고 품질이 더 좋은 것은 아닙니다."
)
_COVERAGE_SCOPE = "F0 측정 가능성·coverage 지표이며 품질 지표가 아닙니다."
_MEASUREMENT_SPECS = (
    _MeasurementSpec(
        "EV_DURATION",
        "utterance_duration",
        "s",
        "measurement",
        _OBJECTIVE_SCOPE,
    ),
    _MeasurementSpec(
        "EV_SPEAKING_RATE",
        "speaking_rate",
        "syllables/s",
        "measurement",
        _OBJECTIVE_SCOPE,
    ),
    _MeasurementSpec(
        "EV_ARTICULATION_RATE",
        "articulation_rate",
        "syllables/s",
        "measurement",
        _OBJECTIVE_SCOPE,
    ),
    _MeasurementSpec(
        "EV_PAUSE_COUNT",
        "internal_pause_count",
        "count",
        "measurement",
        _OBJECTIVE_SCOPE,
    ),
    _MeasurementSpec(
        "EV_PAUSE_DURATION",
        "internal_pause_total_duration",
        "s",
        "measurement",
        _OBJECTIVE_SCOPE,
    ),
    _MeasurementSpec(
        "EV_F0_RANGE",
        "f0_semitone_percentile_range",
        "semitone",
        "measurement",
        _OBJECTIVE_SCOPE,
    ),
    _MeasurementSpec(
        "EV_INTENSITY_RANGE",
        "speech_intensity_percentile_range",
        "dB",
        "measurement",
        _OBJECTIVE_SCOPE,
    ),
    _MeasurementSpec(
        "EV_F0_COVERAGE",
        "speech_f0_valid_ratio",
        "ratio",
        "coverage",
        _COVERAGE_SCOPE,
    ),
)
_REFERENCE_PREFIX = {
    "current_announcer": "ANNOUNCER",
    "all_anchor_sample": "ALL_ANCHOR",
}
_REFERENCE_METRIC_TOKEN = {
    "speaking_rate": "SPEAKING_RATE",
    "articulation_rate": "ARTICULATION_RATE",
    "internal_pause_count_per_10_syllables": "PAUSE_COUNT_PER_10_SYLLABLES",
    "internal_pause_duration_per_10_syllables": ("PAUSE_DURATION_PER_10_SYLLABLES"),
    "f0_semitone_percentile_range": "F0_RANGE",
    "speech_intensity_percentile_range": "INTENSITY_RANGE",
    "speech_f0_valid_ratio": "F0_COVERAGE",
}
_FEEDBACK_EVIDENCE = {
    "REPORT_DURATION": ("EV_DURATION",),
    "REPORT_SPEAKING_RATE": ("EV_SPEAKING_RATE",),
    "REPORT_F0_RANGE": ("EV_F0_RANGE",),
    "F0_UNAVAILABLE": ("EV_F0_RANGE", "EV_F0_COVERAGE"),
    "ALIGNMENT_UNAVAILABLE": ("EV_ALIGNMENT_STATUS",),
}


def _feature_value(result: AnalysisResult, name: str) -> float | None:
    feature = next(
        (feature for feature in result.acoustic_features if feature.name == name),
        None,
    )
    if feature is None:
        return None
    value = feature.summary.get("value")
    return float(value) if value is not None else None


def _measurement_evidence(
    result: AnalysisResult,
) -> list[LlmEvidenceItem]:
    evidence = [
        LlmEvidenceItem(
            evidence_id=spec.evidence_id,
            category=spec.category,
            metric_name=spec.metric_name,
            value=_feature_value(result, spec.metric_name),
            unit=spec.unit,
            interpretation_scope=spec.interpretation_scope,
        )
        for spec in _MEASUREMENT_SPECS
    ]
    alignment_unavailable = any(
        feedback.rule_id == "ALIGNMENT_UNAVAILABLE" for feedback in result.feedback
    ) or any(
        error.stage in {PipelineStage.ALIGNMENT, PipelineStage.TEXTGRID}
        for error in result.errors
    )
    if alignment_unavailable:
        evidence.append(
            LlmEvidenceItem(
                evidence_id="EV_ALIGNMENT_STATUS",
                category="status",
                metric_name="alignment_available",
                value=False,
                unit="boolean",
                interpretation_scope=(
                    "정렬 기반 측정의 사용 가능 상태이며 발음 품질 판정이 아닙니다."
                ),
            )
        )
    return evidence


def _population(
    result: ReferenceComparisonResult,
    population_id: PopulationId,
) -> ReferencePopulationComparison:
    try:
        return next(
            item for item in result.comparisons if item.population_id == population_id
        )
    except StopIteration as exc:
        raise ValueError(
            f"required reference population is missing: {population_id}"
        ) from exc


def _reference_evidence(
    result: ReferenceComparisonResult,
) -> list[LlmEvidenceItem]:
    evidence: list[LlmEvidenceItem] = []
    for population_id in ("current_announcer", "all_anchor_sample"):
        population = _population(result, population_id)
        prefix = _REFERENCE_PREFIX[population_id]
        for metric in population.metrics:
            token = _REFERENCE_METRIC_TOKEN[metric.metric_name]
            is_coverage = metric.metric_name == "speech_f0_valid_ratio"
            evidence.append(
                LlmEvidenceItem(
                    evidence_id=f"EV_{prefix}_{token}_COMPARISON",
                    category="coverage" if is_coverage else "reference_comparison",
                    metric_name=metric.metric_name,
                    value=metric.user_value,
                    unit=metric.unit,
                    reference_population=population.population_id,
                    reference_count=metric.reference_count,
                    reference_utterance_count=population.utterance_count,
                    reference_speaker_count=population.unique_speaker_count,
                    reference_median=metric.reference_median,
                    delta_from_median=metric.delta_from_median,
                    percentile=metric.percentile,
                    interpretation_scope=(
                        _COVERAGE_SCOPE if is_coverage else _REFERENCE_SCOPE
                    ),
                )
            )
    return evidence


def _available_feedback(
    result: AnalysisResult,
    evidence_by_id: dict[str, LlmEvidenceItem],
) -> tuple[LlmAvailableFeedback, ...]:
    available: list[LlmAvailableFeedback] = []
    for feedback in result.feedback:
        mapped = _FEEDBACK_EVIDENCE.get(feedback.rule_id)
        if mapped is None or not all(
            evidence_id in evidence_by_id for evidence_id in mapped
        ):
            continue
        if feedback.rule_id.startswith("REPORT_") and any(
            evidence_by_id[evidence_id].value is None for evidence_id in mapped
        ):
            continue
        if (
            feedback.rule_id == "ALIGNMENT_UNAVAILABLE"
            and evidence_by_id["EV_ALIGNMENT_STATUS"].value is not False
        ):
            continue
        available.append(
            LlmAvailableFeedback(
                rule_id=feedback.rule_id,
                message_ko=feedback.message_ko,
                level=feedback.level,
                evidence_ids=mapped,
            )
        )
    return tuple(available)


def _provenance_summary(result: AnalysisResult) -> LlmProvenanceSummary:
    runtime = result.provenance.runtime
    return LlmProvenanceSummary(
        analysis_schema_version=result.schema_version,
        platform_role=runtime.platform_role,
        operating_system=runtime.operating_system,
        architecture=runtime.architecture,
        python_version=runtime.python_version,
        models=tuple(
            LlmModelProvenance(
                role=model.role,
                name=model.name,
                version=model.version,
                revision=model.revision,
            )
            for model in result.provenance.models
        ),
    )


def build_llm_feedback_payload(
    analysis: AnalysisResult,
    comparison: ReferenceComparisonResult,
) -> LlmFeedbackPayload:
    if analysis.analysis_id != comparison.analysis_id:
        raise ValueError("analysis and reference comparison IDs must match")
    current = _population(comparison, "current_announcer")
    all_sample = _population(comparison, "all_anchor_sample")
    measurements = tuple(_measurement_evidence(analysis))
    references = tuple(_reference_evidence(comparison))
    evidence_by_id = {item.evidence_id: item for item in (*measurements, *references)}
    return LlmFeedbackPayload(
        analysis_id=analysis.analysis_id,
        script=analysis.original_script,
        display_pronunciation=analysis.display_pronunciation,
        measurement_summary=measurements,
        reference_comparisons=references,
        available_feedback=_available_feedback(analysis, evidence_by_id),
        limitations=(
            (
                "현직 아나운서 참조 집단은 독립 화자 "
                f"{current.unique_speaker_count}명뿐입니다."
            ),
            (
                "전체 참조 집단은 독립 화자 "
                f"{all_sample.unique_speaker_count}명뿐입니다."
            ),
            "참조 분포에는 서로 다른 대본이 포함되어 있습니다.",
            "발음 정확도와 음소 오류는 측정하지 않았습니다.",
            "이 결과는 전문가 품질 점수가 아닙니다.",
            "F0 coverage는 품질이 아니라 측정 가능성 지표입니다.",
        ),
        prohibited_claims=(
            "발음이 정확하다 또는 부정확하다고 단정하는 표현",
            "억양이 좋다 또는 나쁘다고 판정하는 표현",
            "아나운서보다 우수하다 또는 열등하다고 비교하는 표현",
            "합격·불합격 판정",
            "0~100 점수 또는 종합 점수",
            "측정하지 않은 음소 오류를 단정하는 표현",
        ),
        provenance_summary=_provenance_summary(analysis),
    )


def _deterministic_json(payload: LlmFeedbackPayload) -> str:
    return (
        json.dumps(
            payload.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def save_llm_feedback_payload(
    payload: LlmFeedbackPayload,
    config: PocConfig,
) -> Path:
    project_root = config.paths.project_root.resolve(strict=True)
    output_root = config.paths.output_dir.resolve(strict=False)
    if not output_root.is_relative_to(project_root):
        raise ValueError("output_dir must remain inside project_root")
    output_dir = output_root / "llm_inputs" / str(payload.analysis_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved_dir = output_dir.resolve(strict=True)
    if not resolved_dir.is_relative_to(output_root):
        raise ValueError("LLM payload output escaped output_dir")
    target = resolved_dir / "feedback_input.json"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=".feedback-input-",
            suffix=".tmp",
            dir=resolved_dir,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(_deterministic_json(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_llm_feedback_payload(path: Path) -> LlmFeedbackPayload:
    return LlmFeedbackPayload.model_validate_json(path.read_text(encoding="utf-8"))
