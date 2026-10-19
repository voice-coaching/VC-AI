from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .errors import AnalysisStatus, PipelineError


Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
NonNegativeFiniteFloat = Annotated[float, Field(ge=0, allow_inf_nan=False)]
PositiveFiniteFloat = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AnalysisRequest(ContractModel):
    script: str
    audio_path: Path
    source: Literal["microphone", "upload", "test"]


class TimeInterval(ContractModel):
    label: str
    start_s: NonNegativeFiniteFloat
    end_s: NonNegativeFiniteFloat

    @model_validator(mode="after")
    def validate_order(self) -> TimeInterval:
        if self.start_s > self.end_s:
            raise ValueError("start_s must not be later than end_s")
        return self


class AudioMetadata(ContractModel):
    duration_s: PositiveFiniteFloat
    sample_rate_hz: int = Field(gt=0)
    channels: int = Field(gt=0)
    encoding: str = Field(min_length=1)
    sha256: Sha256


class FeaturePoint(ContractModel):
    time_s: NonNegativeFiniteFloat
    value: FiniteFloat | None


class AcousticFeature(ContractModel):
    name: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    method: str | None = None
    points: list[FeaturePoint] = Field(default_factory=list)
    summary: dict[str, FiniteFloat | None] = Field(default_factory=dict)


class FeedbackItem(ContractModel):
    rule_id: str = Field(min_length=1)
    message_ko: str = Field(min_length=1)
    level: Literal["info", "suggestion", "warning"] = "info"
    evidence_paths: list[str] = Field(default_factory=list)


class PackageVersion(ContractModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)


class ModelVersion(ContractModel):
    role: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: str | None = None
    revision: str | None = None
    sha256: Sha256 | None = None


class RuntimeEnvironment(ContractModel):
    platform_role: Literal["mac_smoke", "dlpc_formal", "test", "unknown"]
    operating_system: str = Field(min_length=1)
    architecture: str = Field(min_length=1)
    python_version: str = Field(min_length=1)


class Provenance(ContractModel):
    created_at_utc: datetime
    git_commit: str = Field(min_length=1)
    dirty_worktree: bool
    packages: list[PackageVersion] = Field(default_factory=list)
    models: list[ModelVersion] = Field(default_factory=list)
    runtime: RuntimeEnvironment
    config_sha256: Sha256
    dependency_lock_sha256: Sha256

    @field_validator("created_at_utc")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at_utc must be timezone-aware")
        return value


class AnalysisResult(ContractModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    analysis_id: UUID = Field(default_factory=uuid4)
    status: AnalysisStatus
    original_script: str
    normalized_script: str | None = None
    display_pronunciation: str | None = None
    audio_metadata: AudioMetadata | None = None
    word_intervals: list[TimeInterval] = Field(default_factory=list)
    phone_intervals: list[TimeInterval] = Field(default_factory=list)
    acoustic_features: list[AcousticFeature] = Field(default_factory=list)
    feedback: list[FeedbackItem] = Field(default_factory=list)
    errors: list[PipelineError] = Field(default_factory=list)
    provenance: Provenance

    @model_validator(mode="after")
    def validate_result_consistency(self) -> AnalysisResult:
        if self.status is AnalysisStatus.SUCCESS and self.errors:
            raise ValueError("success results must not contain errors")
        if self.status in {AnalysisStatus.PARTIAL, AnalysisStatus.FAILED}:
            if not self.errors:
                raise ValueError(
                    "partial and failed results require at least one error"
                )

        intervals = [*self.word_intervals, *self.phone_intervals]
        if intervals and self.audio_metadata is None:
            raise ValueError("intervals require audio_metadata")
        if self.audio_metadata is not None:
            duration_s = self.audio_metadata.duration_s
            if any(interval.end_s > duration_s for interval in intervals):
                raise ValueError("interval must not extend beyond audio duration")
        return self
