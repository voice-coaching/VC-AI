from __future__ import annotations

import re
import unicodedata
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, JsonValue

from .config import AnalysisSettings
from .errors import ErrorCode, PipelineError, PipelineFailure, PipelineStage


class ScriptToken(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token_id: str
    text: str
    start_char: int
    end_char: int


class ScriptResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    original: str
    normalized: str
    tokens: list[ScriptToken]
    sha256: str


def _invalid_script(message_ko: str, **details: JsonValue) -> PipelineFailure:
    return PipelineFailure(
        PipelineError(
            code=ErrorCode.INVALID_SCRIPT,
            stage=PipelineStage.INPUT,
            message_ko=message_ko,
            details=details,
        )
    )


def prepare_script(
    text: str, *, settings: AnalysisSettings | None = None
) -> ScriptResult:
    active_settings = settings or AnalysisSettings()
    allowed_control_whitespace = {chr(9), chr(10), chr(13)}
    if any(
        unicodedata.category(character).startswith("C")
        and character not in allowed_control_whitespace
        for character in text
    ):
        raise _invalid_script("스크립트에 허용되지 않는 제어 문자가 있습니다.")

    normalized = " ".join(unicodedata.normalize("NFC", text).split())
    if not normalized:
        raise _invalid_script("스크립트가 비어 있습니다.")
    if len(normalized) > active_settings.max_script_chars:
        raise _invalid_script(
            "스크립트가 허용 길이를 초과했습니다.",
            length=len(normalized),
            max_script_chars=active_settings.max_script_chars,
        )

    tokens = [
        ScriptToken(
            token_id=f"w{index:04d}",
            text=match.group(),
            start_char=match.start(),
            end_char=match.end(),
        )
        for index, match in enumerate(re.finditer(r"\S+", normalized), start=1)
    ]
    return ScriptResult(
        original=text,
        normalized=normalized,
        tokens=tokens,
        sha256=sha256(normalized.encode("utf-8")).hexdigest(),
    )
