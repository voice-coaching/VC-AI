from __future__ import annotations

import csv
import json
import os
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Annotated, Literal, cast
from uuid import UUID

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from .config import PocConfig
from .schemas import AnalysisResult, FiniteFloat


PERCENTILE_METHOD: Literal["empirical_midrank_percentile_v1"] = (
    "empirical_midrank_percentile_v1"
)
CURRENT_ANNOUNCER_JOB = "현직아나운서"

PopulationId = Literal["current_announcer", "all_anchor_sample"]
MetricName = Literal[
    "speaking_rate",
    "articulation_rate",
    "internal_pause_count_per_10_syllables",
    "internal_pause_duration_per_10_syllables",
    "f0_semitone_percentile_range",
    "speech_intensity_percentile_range",
    "speech_f0_valid_ratio",
]
Percentile = Annotated[float, Field(ge=0.0, le=100.0, allow_inf_nan=False)]


class _InternalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReferenceMetricComparison(_InternalModel):
    metric_name: MetricName
    unit: str = Field(min_length=1)
    user_value: FiniteFloat | None
    reference_count: int = Field(ge=0)
    reference_speaker_count: int = Field(ge=0)
    reference_median: FiniteFloat | None
    reference_p10: FiniteFloat | None
    reference_p25: FiniteFloat | None
    reference_p75: FiniteFloat | None
    reference_p90: FiniteFloat | None
    percentile: Percentile | None
    delta_from_median: FiniteFloat | None
    within_iqr: bool | None
    within_p10_p90: bool | None
    percentile_method: Literal["empirical_midrank_percentile_v1"] = PERCENTILE_METHOD


class ReferencePopulationComparison(_InternalModel):
    population_id: PopulationId
    population_label: str = Field(min_length=1)
    utterance_count: int = Field(ge=0)
    unique_speaker_count: int = Field(ge=0)
    sex_composition: dict[str, int]
    age_composition: dict[str, int]
    job_composition: dict[str, int]
    metrics: tuple[ReferenceMetricComparison, ...]
    limitations: tuple[str, ...]


class ReferenceComparisonResult(_InternalModel):
    analysis_id: UUID
    result_path: Path
    comparisons: tuple[ReferencePopulationComparison, ...]
    measurement_warnings: tuple[str, ...]


@dataclass(frozen=True)
class ReferenceComparisonPaths:
    json_path: Path
    csv_path: Path


@dataclass(frozen=True)
class _MetricSpec:
    name: MetricName
    unit: str


@dataclass(frozen=True)
class _ReferenceRow:
    speaker_id: str
    job: str
    sex: str
    age: str
    values: dict[MetricName, float | None]


_METRIC_SPECS = (
    _MetricSpec("speaking_rate", "syllables/s"),
    _MetricSpec("articulation_rate", "syllables/s"),
    _MetricSpec(
        "internal_pause_count_per_10_syllables",
        "count/10 syllables",
    ),
    _MetricSpec(
        "internal_pause_duration_per_10_syllables",
        "s/10 syllables",
    ),
    _MetricSpec("f0_semitone_percentile_range", "semitone"),
    _MetricSpec("speech_intensity_percentile_range", "dB"),
    _MetricSpec("speech_f0_valid_ratio", "ratio"),
)
_SUMMARY_COLUMNS = {
    "speaker_id",
    "job",
    "sex",
    "age",
    "syllable_count",
    "speaking_rate",
    "articulation_rate",
    "internal_pause_count",
    "internal_pause_total_duration",
    "speech_f0_valid_ratio",
    "f0_semitone_percentile_range",
    "speech_intensity_percentile_range",
}
_CSV_FIELDS = (
    "analysis_id",
    "result_path",
    "population_id",
    "population_label",
    "utterance_count",
    "unique_speaker_count",
    "sex_composition",
    "age_composition",
    "job_composition",
    "limitations",
    "metric_name",
    "unit",
    "user_value",
    "reference_count",
    "reference_speaker_count",
    "reference_median",
    "reference_p10",
    "reference_p25",
    "reference_p75",
    "reference_p90",
    "percentile",
    "delta_from_median",
    "within_iqr",
    "within_p10_p90",
    "percentile_method",
    "measurement_warnings",
)


def empirical_midrank_percentile(
    user_value: float,
    reference_values: Sequence[float | None],
) -> float:
    if not isfinite(user_value):
        raise ValueError("user_value must be finite")
    values = [
        value for value in reference_values if value is not None and isfinite(value)
    ]
    if not values:
        raise ValueError("at least one finite reference value is required")
    lower_count = sum(value < user_value for value in values)
    tie_count = sum(value == user_value for value in values)
    percentile = 100.0 * (lower_count + 0.5 * tie_count) / len(values)
    if not 0.0 <= percentile <= 100.0:
        raise RuntimeError("empirical percentile escaped the 0 to 100 range")
    return percentile


def _optional_float(value: str | None, *, field: str) -> float | None:
    if value is None or not value.strip():
        return None
    parsed = float(value)
    if not isfinite(parsed):
        raise ValueError(f"{field} must be finite when present")
    return parsed


def _per_ten(
    value: float | None,
    syllable_count: float | None,
) -> float | None:
    if value is None or syllable_count is None or syllable_count <= 0.0:
        return None
    return value / syllable_count * 10.0


def _read_summary(path: Path) -> list[_ReferenceRow]:
    with (
        path.expanduser()
        .resolve(strict=True)
        .open(
            encoding="utf-8",
            newline="",
        ) as handle
    ):
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or ())
        missing = sorted(_SUMMARY_COLUMNS - columns)
        if missing:
            raise ValueError(f"reference summary is missing columns: {missing}")
        rows: list[_ReferenceRow] = []
        for raw in reader:
            speaker_id = raw["speaker_id"].strip()
            job = raw["job"].strip()
            sex = raw["sex"].strip()
            age = raw["age"].strip()
            if not all((speaker_id, job, sex, age)):
                raise ValueError("reference population metadata must not be empty")
            syllables = _optional_float(
                raw["syllable_count"],
                field="syllable_count",
            )
            pause_count = _optional_float(
                raw["internal_pause_count"],
                field="internal_pause_count",
            )
            pause_duration = _optional_float(
                raw["internal_pause_total_duration"],
                field="internal_pause_total_duration",
            )
            values: dict[MetricName, float | None] = {
                "speaking_rate": _optional_float(
                    raw["speaking_rate"],
                    field="speaking_rate",
                ),
                "articulation_rate": _optional_float(
                    raw["articulation_rate"],
                    field="articulation_rate",
                ),
                "internal_pause_count_per_10_syllables": _per_ten(
                    pause_count,
                    syllables,
                ),
                "internal_pause_duration_per_10_syllables": _per_ten(
                    pause_duration,
                    syllables,
                ),
                "f0_semitone_percentile_range": _optional_float(
                    raw["f0_semitone_percentile_range"],
                    field="f0_semitone_percentile_range",
                ),
                "speech_intensity_percentile_range": _optional_float(
                    raw["speech_intensity_percentile_range"],
                    field="speech_intensity_percentile_range",
                ),
                "speech_f0_valid_ratio": _optional_float(
                    raw["speech_f0_valid_ratio"],
                    field="speech_f0_valid_ratio",
                ),
            }
            rows.append(
                _ReferenceRow(
                    speaker_id=speaker_id,
                    job=job,
                    sex=sex,
                    age=age,
                    values=values,
                )
            )
    if not rows:
        raise ValueError("reference summary must contain at least one row")
    return rows


def _feature_value(result: AnalysisResult, name: str) -> float | None:
    feature = next(
        (feature for feature in result.acoustic_features if feature.name == name),
        None,
    )
    if feature is None:
        return None
    value = feature.summary.get("value")
    return float(value) if value is not None else None


def _user_values(result: AnalysisResult) -> dict[MetricName, float | None]:
    syllables = _feature_value(result, "syllable_count")
    return {
        "speaking_rate": _feature_value(result, "speaking_rate"),
        "articulation_rate": _feature_value(result, "articulation_rate"),
        "internal_pause_count_per_10_syllables": _per_ten(
            _feature_value(result, "internal_pause_count"),
            syllables,
        ),
        "internal_pause_duration_per_10_syllables": _per_ten(
            _feature_value(result, "internal_pause_total_duration"),
            syllables,
        ),
        "f0_semitone_percentile_range": _feature_value(
            result,
            "f0_semitone_percentile_range",
        ),
        "speech_intensity_percentile_range": _feature_value(
            result,
            "speech_intensity_percentile_range",
        ),
        "speech_f0_valid_ratio": _feature_value(
            result,
            "speech_f0_valid_ratio",
        ),
    }


def _composition(
    rows: Sequence[_ReferenceRow],
    attribute: Literal["sex", "age", "job"],
) -> dict[str, int]:
    by_speaker: dict[str, str] = {}
    for row in rows:
        value = getattr(row, attribute)
        existing = by_speaker.setdefault(row.speaker_id, value)
        if existing != value:
            raise ValueError(f"speaker metadata is inconsistent for {row.speaker_id}")
    return dict(sorted(Counter(by_speaker.values()).items()))


def _metric_comparison(
    spec: _MetricSpec,
    user_value: float | None,
    rows: Sequence[_ReferenceRow],
) -> ReferenceMetricComparison:
    available = [row for row in rows if row.values[spec.name] is not None]
    values = [
        value for row in available if (value := row.values[spec.name]) is not None
    ]
    if not values:
        return ReferenceMetricComparison(
            metric_name=spec.name,
            unit=spec.unit,
            user_value=user_value,
            reference_count=0,
            reference_speaker_count=0,
            reference_median=None,
            reference_p10=None,
            reference_p25=None,
            reference_p75=None,
            reference_p90=None,
            percentile=None,
            delta_from_median=None,
            within_iqr=None,
            within_p10_p90=None,
        )

    p10, p25, median, p75, p90 = (
        float(value) for value in np.percentile(values, [10, 25, 50, 75, 90])
    )
    percentile = (
        empirical_midrank_percentile(user_value, values)
        if user_value is not None
        else None
    )
    return ReferenceMetricComparison(
        metric_name=spec.name,
        unit=spec.unit,
        user_value=user_value,
        reference_count=len(values),
        reference_speaker_count=len({row.speaker_id for row in available}),
        reference_median=median,
        reference_p10=p10,
        reference_p25=p25,
        reference_p75=p75,
        reference_p90=p90,
        percentile=percentile,
        delta_from_median=(user_value - median if user_value is not None else None),
        within_iqr=(p25 <= user_value <= p75 if user_value is not None else None),
        within_p10_p90=(p10 <= user_value <= p90 if user_value is not None else None),
    )


def _population(
    *,
    population_id: PopulationId,
    label: str,
    rows: Sequence[_ReferenceRow],
    user_values: dict[MetricName, float | None],
) -> ReferencePopulationComparison:
    speaker_count = len({row.speaker_id for row in rows})
    return ReferencePopulationComparison(
        population_id=population_id,
        population_label=label,
        utterance_count=len(rows),
        unique_speaker_count=speaker_count,
        sex_composition=_composition(rows, "sex"),
        age_composition=_composition(rows, "age"),
        job_composition=_composition(rows, "job"),
        metrics=tuple(
            _metric_comparison(spec, user_values[spec.name], rows)
            for spec in _METRIC_SPECS
        ),
        limitations=(
            f"독립 화자 {speaker_count}명의 발화 표본에 한정된 예비 분포입니다.",
            "백분위와 중앙값 차이는 품질·정확도·점수가 아닙니다.",
        ),
    )


def build_reference_comparison(
    result: AnalysisResult,
    *,
    result_path: Path,
    summary_path: Path,
) -> ReferenceComparisonResult:
    rows = _read_summary(summary_path)
    current = [row for row in rows if row.job == CURRENT_ANNOUNCER_JOB]
    if not current:
        raise ValueError("current announcer reference population is empty")
    values = _user_values(result)
    missing = [name for name, value in values.items() if value is None]
    warnings = [
        (
            "speech_f0_valid_ratio는 발화 스타일이나 품질이 아니라 "
            "F0 측정 가능성·coverage 지표입니다."
        ),
        "백분위가 높다는 것은 품질이 더 좋다는 의미가 아닙니다.",
    ]
    if missing:
        warnings.append(
            "사용자 측정값이 없어 비교하지 않은 지표: " + ", ".join(missing)
        )
    return ReferenceComparisonResult(
        analysis_id=result.analysis_id,
        result_path=result_path.expanduser().resolve(strict=False),
        comparisons=(
            _population(
                population_id="current_announcer",
                label="현직 아나운서 표본",
                rows=current,
                user_values=values,
            ),
            _population(
                population_id="all_anchor_sample",
                label="AI Hub 앵커 전체 표본",
                rows=rows,
                user_values=values,
            ),
        ),
        measurement_warnings=tuple(warnings),
    )


def _json_cell(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _csv_text(result: ReferenceComparisonResult) -> str:
    with tempfile.SpooledTemporaryFile(
        mode="w+",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=_CSV_FIELDS,
            lineterminator="\n",
        )
        writer.writeheader()
        for population in result.comparisons:
            population_payload = population.model_dump(mode="json")
            for metric in population.metrics:
                metric_payload = metric.model_dump(mode="json")
                writer.writerow(
                    {
                        "analysis_id": str(result.analysis_id),
                        "result_path": str(result.result_path),
                        "population_id": population.population_id,
                        "population_label": population.population_label,
                        "utterance_count": population.utterance_count,
                        "unique_speaker_count": (population.unique_speaker_count),
                        "sex_composition": _json_cell(
                            population_payload["sex_composition"]
                        ),
                        "age_composition": _json_cell(
                            population_payload["age_composition"]
                        ),
                        "job_composition": _json_cell(
                            population_payload["job_composition"]
                        ),
                        "limitations": _json_cell(population_payload["limitations"]),
                        **metric_payload,
                        "measurement_warnings": _json_cell(
                            result.model_dump(mode="json")["measurement_warnings"]
                        ),
                    }
                )
        handle.seek(0)
        return handle.read()


def _json_text(result: ReferenceComparisonResult) -> str:
    return (
        json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _atomic_text(path: Path, text: str, *, prefix: str) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=prefix,
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_reference_comparison(
    result: ReferenceComparisonResult,
    config: PocConfig,
) -> ReferenceComparisonPaths:
    project_root = config.paths.project_root.resolve(strict=True)
    output_root = config.paths.output_dir.resolve(strict=False)
    if not output_root.is_relative_to(project_root):
        raise ValueError("output_dir must remain inside project_root")
    output_dir = output_root / "comparisons" / str(result.analysis_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved_dir = output_dir.resolve(strict=True)
    if not resolved_dir.is_relative_to(output_root):
        raise ValueError("comparison output escaped output_dir")
    paths = ReferenceComparisonPaths(
        json_path=resolved_dir / "reference_comparison.json",
        csv_path=resolved_dir / "reference_comparison.csv",
    )
    _atomic_text(
        paths.json_path,
        _json_text(result),
        prefix=".reference-comparison-json-",
    )
    _atomic_text(
        paths.csv_path,
        _csv_text(result),
        prefix=".reference-comparison-csv-",
    )
    return paths


def load_reference_comparison_json(path: Path) -> ReferenceComparisonResult:
    return ReferenceComparisonResult.model_validate_json(
        path.read_text(encoding="utf-8")
    )


def _optional_csv_float(value: str) -> float | None:
    return float(value) if value else None


def _optional_csv_bool(value: str) -> bool | None:
    if not value:
        return None
    if value not in {"True", "False"}:
        raise ValueError("CSV boolean must be True or False")
    return value == "True"


def load_reference_comparison_csv(path: Path) -> ReferenceComparisonResult:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("reference comparison CSV is empty")
    first = rows[0]
    population_order: list[str] = []
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        population_id = row["population_id"]
        if population_id not in grouped:
            population_order.append(population_id)
            grouped[population_id] = []
        grouped[population_id].append(row)
    populations: list[ReferencePopulationComparison] = []
    for population_id in population_order:
        population_rows = grouped[population_id]
        metadata = population_rows[0]
        metrics = tuple(
            ReferenceMetricComparison(
                metric_name=cast(MetricName, row["metric_name"]),
                unit=row["unit"],
                user_value=_optional_csv_float(row["user_value"]),
                reference_count=int(row["reference_count"]),
                reference_speaker_count=int(row["reference_speaker_count"]),
                reference_median=_optional_csv_float(row["reference_median"]),
                reference_p10=_optional_csv_float(row["reference_p10"]),
                reference_p25=_optional_csv_float(row["reference_p25"]),
                reference_p75=_optional_csv_float(row["reference_p75"]),
                reference_p90=_optional_csv_float(row["reference_p90"]),
                percentile=_optional_csv_float(row["percentile"]),
                delta_from_median=_optional_csv_float(row["delta_from_median"]),
                within_iqr=_optional_csv_bool(row["within_iqr"]),
                within_p10_p90=_optional_csv_bool(row["within_p10_p90"]),
                percentile_method=cast(
                    Literal["empirical_midrank_percentile_v1"],
                    row["percentile_method"],
                ),
            )
            for row in population_rows
        )
        populations.append(
            ReferencePopulationComparison(
                population_id=cast(PopulationId, population_id),
                population_label=metadata["population_label"],
                utterance_count=int(metadata["utterance_count"]),
                unique_speaker_count=int(metadata["unique_speaker_count"]),
                sex_composition=json.loads(metadata["sex_composition"]),
                age_composition=json.loads(metadata["age_composition"]),
                job_composition=json.loads(metadata["job_composition"]),
                metrics=metrics,
                limitations=tuple(json.loads(metadata["limitations"])),
            )
        )
    return ReferenceComparisonResult(
        analysis_id=UUID(first["analysis_id"]),
        result_path=Path(first["result_path"]),
        comparisons=tuple(populations),
        measurement_warnings=tuple(json.loads(first["measurement_warnings"])),
    )


def _metric_line(
    metrics: Mapping[MetricName, ReferenceMetricComparison],
    name: MetricName,
    label: str,
    display_unit: str,
) -> str:
    metric = metrics[name]
    if (
        metric.user_value is None
        or metric.reference_median is None
        or metric.percentile is None
    ):
        return f"- {label}: 비교 가능한 측정값 없음"
    return (
        f"- {label}: 사용자 {metric.user_value:.3f}{display_unit} / "
        f"표본 중앙값 {metric.reference_median:.3f}{display_unit} / "
        f"백분위 {metric.percentile:.1f}"
    )


def render_reference_summary(result: ReferenceComparisonResult) -> str:
    population = next(
        item for item in result.comparisons if item.population_id == "current_announcer"
    )
    all_sample = next(
        item for item in result.comparisons if item.population_id == "all_anchor_sample"
    )
    metrics = {metric.metric_name: metric for metric in population.metrics}
    lines = [
        "[현직 아나운서 표본 비교]",
        _metric_line(
            metrics,
            "speaking_rate",
            "말속도",
            " 음절/초",
        ),
        _metric_line(
            metrics,
            "articulation_rate",
            "조음속도",
            " 음절/초",
        ),
        _metric_line(
            metrics,
            "internal_pause_count_per_10_syllables",
            "내부 쉼",
            " 회/10음절",
        ),
        _metric_line(
            metrics,
            "internal_pause_duration_per_10_syllables",
            "내부 쉼 시간",
            " 초/10음절",
        ),
        _metric_line(
            metrics,
            "f0_semitone_percentile_range",
            "음높이 변화",
            "st",
        ),
        _metric_line(
            metrics,
            "speech_intensity_percentile_range",
            "음량 변화",
            "dB",
        ),
        _metric_line(
            metrics,
            "speech_f0_valid_ratio",
            "F0 측정 가능 프레임 비율",
            "",
        ),
        "",
        *result.measurement_warnings,
        "",
        (
            "이 결과는 현직 아나운서 "
            f"{population.unique_speaker_count}명과 전체 "
            f"{all_sample.unique_speaker_count}명으로 구성된"
        ),
        "AI Hub 샘플의 예비 참조 분포입니다.",
        "발음 정확도나 전문가 품질 점수가 아닙니다.",
    ]
    return "\n".join(lines)
