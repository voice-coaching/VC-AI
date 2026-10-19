from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from types import ModuleType
from typing import Any, Protocol, cast

from pydantic import BaseModel, ConfigDict

from .errors import ErrorCode, PipelineError, PipelineFailure, PipelineStage
from .script import ScriptResult


class G2pEngine(Protocol):
    def __call__(self, text: str, *, to_syl: bool) -> str: ...


class PronunciationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    display_hangul: str
    display_jamo: str
    per_token: list[PronunciationToken]
    # Diagnostic-only standalone conversions retain source-word evidence for
    # utterance-level cross-word phonological changes. They never replace the
    # production utterance-level pronunciation above.
    standalone_token_jamo: tuple[str, ...] = ()


class PronunciationToken(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token_id: str
    display_hangul: str
    display_jamo: str


class G2pService:
    def __init__(
        self,
        *,
        module_loader: Callable[[str], ModuleType | Any] = import_module,
    ) -> None:
        try:
            module = module_loader("g2pk")
            self._engine = cast(G2pEngine, module.G2p())
        except Exception as exc:
            raise PipelineFailure(
                PipelineError(
                    code=ErrorCode.DEPENDENCY_MISSING,
                    stage=PipelineStage.DEPENDENCY,
                    message_ko="g2pK 의존성을 불러올 수 없습니다.",
                    details={
                        "dependency": "g2pk",
                        "exception_type": type(exc).__name__,
                    },
                )
            ) from exc

    def convert(self, script: ScriptResult) -> PronunciationResult:
        try:
            display_hangul = self._engine(script.normalized, to_syl=True)
            display_jamo = self._engine(script.normalized, to_syl=False)
            standalone_token_jamo = tuple(
                self._engine(token.text, to_syl=False) for token in script.tokens
            )
            per_token = [
                PronunciationToken(
                    token_id=source_token.token_id,
                    display_hangul=hangul,
                    display_jamo=jamo,
                )
                for source_token, hangul, jamo in zip(
                    script.tokens,
                    display_hangul.split(),
                    display_jamo.split(),
                    strict=True,
                )
            ]
        except Exception as exc:
            raise PipelineFailure(
                PipelineError(
                    code=ErrorCode.G2P_FAILED,
                    stage=PipelineStage.G2P,
                    message_ko="한국어 발음열 변환에 실패했습니다.",
                    details={"exception_type": type(exc).__name__},
                )
            ) from exc

        return PronunciationResult(
            display_hangul=display_hangul,
            display_jamo=display_jamo,
            per_token=per_token,
            standalone_token_jamo=standalone_token_jamo,
        )
