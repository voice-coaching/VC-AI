from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class AnalysisStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class ErrorCode(str, Enum):
    INVALID_SCRIPT = "invalid_script"
    INVALID_AUDIO = "invalid_audio"
    AUDIO_CONVERSION_FAILED = "audio_conversion_failed"
    G2P_FAILED = "g2p_failed"
    ALIGNMENT_FAILED = "alignment_failed"
    TEXTGRID_PARSE_FAILED = "textgrid_parse_failed"
    ACOUSTIC_ANALYSIS_FAILED = "acoustic_analysis_failed"
    DEPENDENCY_MISSING = "dependency_missing"
    MODEL_MISSING = "model_missing"
    INTERNAL_ERROR = "internal_error"


class PipelineStage(str, Enum):
    INPUT = "input"
    AUDIO = "audio"
    G2P = "g2p"
    ALIGNMENT = "alignment"
    TEXTGRID = "textgrid"
    ACOUSTIC = "acoustic"
    FEEDBACK = "feedback"
    STORAGE = "storage"
    DEPENDENCY = "dependency"
    INTERNAL = "internal"


class PipelineError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ErrorCode
    stage: PipelineStage
    message_ko: str = Field(min_length=1)
    recoverable: bool = False
    details: dict[str, JsonValue] = Field(default_factory=dict)


class PipelineFailure(Exception):
    """Pipeline exception carrying the stable error contract."""

    def __init__(self, error: PipelineError) -> None:
        super().__init__(error.message_ko)
        self.error = error
