from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .aihub71557_metrics_v2 import (
    METRIC_VERSION,
    Aihub71557MetricsV2Result,
    calculate_aihub71557_metrics_v2,
)
from .schemas import TimeInterval


REFERENCE_VERSION: Literal["aihub71557_prosody_reference_v1"] = (
    "aihub71557_prosody_reference_v1"
)
REFERENCE_AGGREGATION = (
    "utterance metrics -> per-speaker median -> speaker-balanced distribution"
)
REFERENCE_WINDOW_SOURCE: Literal["aihub_json_utterance_bounds"] = (
    "aihub_json_utterance_bounds"
)
APPROVED_REFERENCE_SHA256 = (
    "e230b1fe524b18d008d1d3007d0b098cd718c9704541a70478119a03273ee722"
)
DEFAULT_REFERENCE_GROUP: Literal["현직아나운서"] = "현직아나운서"
EXTRACTOR_COMPATIBILITY = "aihub71557_parity_fixture_v1"

ReferenceGroup = Literal["현직아나운서", "전직아나운서", "아나운서준비생", "전체"]
AnalysisWindowSource = Literal["mfa_speech_bounds"]
_METRIC_NAMES = (
    "speech_rate",
    "articulation_rate",
    "pause_count_per_10_syllables",
    "pause_duration_per_10_syllables",
    "f0_median_hz",
    "f0_range_st",
    "f0_coverage",
    "intensity_range_db",
)
_NON_SPEECH_LABELS = {"", "sil", "sp", "noise"}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Aihub71557ReferenceMetricStats(_Model):
    n: int = Field(ge=1)
    mean: float = Field(allow_inf_nan=False)
    std: float = Field(ge=0.0, allow_inf_nan=False)
    p10: float = Field(allow_inf_nan=False)
    p25: float = Field(allow_inf_nan=False)
    p50: float = Field(allow_inf_nan=False)
    p75: float = Field(allow_inf_nan=False)
    p90: float = Field(allow_inf_nan=False)


class Aihub71557ReferenceGroup(_Model):
    metrics: dict[str, Aihub71557ReferenceMetricStats]


class Aihub71557Reference(_Model):
    metric_version: Literal["aihub71557_compatible_metrics_v2"] = METRIC_VERSION
    reference_version: Literal["aihub71557_prosody_reference_v1"] = REFERENCE_VERSION
    reference_aggregation: str = REFERENCE_AGGREGATION
    reference_window_source: Literal["aihub_json_utterance_bounds"] = (
        REFERENCE_WINDOW_SOURCE
    )
    extractor_compatibility: str = EXTRACTOR_COMPATIBILITY
    file_path: Path
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    groups: dict[ReferenceGroup, Aihub71557ReferenceGroup]


class Aihub71557UserMetricsV2(_Model):
    metrics: Aihub71557MetricsV2Result
    analysis_start_s: float = Field(ge=0.0, allow_inf_nan=False)
    analysis_end_s: float = Field(ge=0.0, allow_inf_nan=False)
    analysis_window_source: AnalysisWindowSource = "mfa_speech_bounds"


class Aihub71557MetricComparison(_Model):
    metric_name: str
    user_value: float | None = Field(default=None, allow_inf_nan=False)
    reference_n: int = Field(ge=1)
    reference_p10: float = Field(allow_inf_nan=False)
    reference_p25: float = Field(allow_inf_nan=False)
    reference_p50: float = Field(allow_inf_nan=False)
    reference_p75: float = Field(allow_inf_nan=False)
    reference_p90: float = Field(allow_inf_nan=False)
    delta_from_p50: float | None = Field(default=None, allow_inf_nan=False)
    within_iqr: bool | None
    within_p10_p90: bool | None


class Aihub71557ReferenceComparison(_Model):
    metric_version: Literal["aihub71557_compatible_metrics_v2"] = METRIC_VERSION
    reference_version: Literal["aihub71557_prosody_reference_v1"] = REFERENCE_VERSION
    reference_aggregation: str = REFERENCE_AGGREGATION
    reference_group: ReferenceGroup
    analysis_window_source: AnalysisWindowSource
    reference_window_source: Literal["aihub_json_utterance_bounds"] = (
        REFERENCE_WINDOW_SOURCE
    )
    reference_file_path: Path
    reference_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    extractor_compatibility: str = EXTRACTOR_COMPATIBILITY
    metrics: tuple[Aihub71557MetricComparison, ...]


class Aihub71557V2Analysis(_Model):
    user_metrics: Aihub71557UserMetricsV2
    comparison: Aihub71557ReferenceComparison


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reference_group(payload: object) -> Aihub71557ReferenceGroup:
    if not isinstance(payload, dict):
        raise ValueError("reference group must be an object")
    metrics = {
        name: Aihub71557ReferenceMetricStats.model_validate(payload[name])
        for name in _METRIC_NAMES
        if name in payload
    }
    missing = sorted(set(_METRIC_NAMES) - set(metrics))
    if missing:
        raise ValueError(f"reference group is missing metrics: {missing}")
    return Aihub71557ReferenceGroup(metrics=metrics)


def load_aihub71557_reference(path: Path) -> Aihub71557Reference:
    reference_path = path.expanduser().resolve(strict=True)
    file_sha256 = _file_sha256(reference_path)
    if file_sha256 != APPROVED_REFERENCE_SHA256:
        raise ValueError("reference checksum does not match the approved v1 file")
    payload = json.loads(reference_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("reference JSON must be an object")
    if payload.get("aggregation") != REFERENCE_AGGREGATION:
        raise ValueError("reference aggregation does not match the approved contract")
    raw_groups = payload.get("groups")
    if not isinstance(raw_groups, dict):
        raise ValueError("reference JSON is missing groups")
    groups: dict[ReferenceGroup, Aihub71557ReferenceGroup] = {}
    for group in ("현직아나운서", "전직아나운서", "아나운서준비생", "전체"):
        if group not in raw_groups:
            raise ValueError(f"reference JSON is missing group: {group}")
        groups[group] = _reference_group(raw_groups[group])
    return Aihub71557Reference(
        file_path=reference_path,
        file_sha256=file_sha256,
        groups=groups,
    )


def mfa_speech_bounds(word_intervals: list[TimeInterval]) -> tuple[float, float]:
    speech = [
        interval
        for interval in word_intervals
        if interval.label not in _NON_SPEECH_LABELS
    ]
    if not speech:
        raise ValueError("mfa_speech_bounds_unavailable")
    return speech[0].start_s, speech[-1].end_s


def calculate_from_mfa_speech_bounds(
    wav_path: Path,
    *,
    text: str,
    word_intervals: list[TimeInterval],
) -> Aihub71557UserMetricsV2:
    start_s, end_s = mfa_speech_bounds(word_intervals)
    return Aihub71557UserMetricsV2(
        metrics=calculate_aihub71557_metrics_v2(
            wav_path,
            analysis_start_s=start_s,
            analysis_end_s=end_s,
            text=text,
        ),
        analysis_start_s=start_s,
        analysis_end_s=end_s,
    )


def build_aihub71557_reference_comparison(
    metrics: Aihub71557MetricsV2Result,
    reference: Aihub71557Reference,
    *,
    reference_group: ReferenceGroup,
    analysis_window_source: AnalysisWindowSource,
) -> Aihub71557ReferenceComparison:
    if metrics.metric_version != reference.metric_version:
        raise ValueError("metric versions must match")
    group = reference.groups[reference_group]
    comparisons = []
    for name in _METRIC_NAMES:
        stats = group.metrics[name]
        user_value = getattr(metrics, name)
        comparisons.append(
            Aihub71557MetricComparison(
                metric_name=name,
                user_value=user_value,
                reference_n=stats.n,
                reference_p10=stats.p10,
                reference_p25=stats.p25,
                reference_p50=stats.p50,
                reference_p75=stats.p75,
                reference_p90=stats.p90,
                delta_from_p50=(
                    user_value - stats.p50 if user_value is not None else None
                ),
                within_iqr=(
                    stats.p25 <= user_value <= stats.p75
                    if user_value is not None
                    else None
                ),
                within_p10_p90=(
                    stats.p10 <= user_value <= stats.p90
                    if user_value is not None
                    else None
                ),
            )
        )
    return Aihub71557ReferenceComparison(
        reference_group=reference_group,
        analysis_window_source=analysis_window_source,
        reference_file_path=reference.file_path,
        reference_file_sha256=reference.file_sha256,
        metrics=tuple(comparisons),
    )


def compare_from_mfa_speech_bounds(
    wav_path: Path,
    *,
    text: str,
    word_intervals: list[TimeInterval],
    reference_path: Path,
    reference_group: ReferenceGroup = DEFAULT_REFERENCE_GROUP,
) -> Aihub71557V2Analysis:
    user_metrics = calculate_from_mfa_speech_bounds(
        wav_path,
        text=text,
        word_intervals=word_intervals,
    )
    reference = load_aihub71557_reference(reference_path)
    return Aihub71557V2Analysis(
        user_metrics=user_metrics,
        comparison=build_aihub71557_reference_comparison(
            user_metrics.metrics,
            reference,
            reference_group=reference_group,
            analysis_window_source=user_metrics.analysis_window_source,
        ),
    )
