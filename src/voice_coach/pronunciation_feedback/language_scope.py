"""Fail-closed target-language scope for training and inference contracts.

'target_language' describes the language being practised.  It must never be
inferred from AI Hub's 'language' field, which records the learner's L1 for
cohort stratification while every recording in dataset 71490 targets Korean.
"""

from __future__ import annotations

from .domain import FailureCode, TargetLanguage
from .errors import PromptUnavailableError


SUPPORTED_TARGET_LANGUAGES = frozenset(
    {TargetLanguage.KOREAN, TargetLanguage.ENGLISH}
)


def require_supported_target_language(
    target_language: TargetLanguage,
) -> TargetLanguage:
    """Return an allowed target language or reject the prompt before analysis."""

    if target_language not in SUPPORTED_TARGET_LANGUAGES:
        raise PromptUnavailableError(
            FailureCode.TARGET_LANGUAGE_NOT_SUPPORTED,
            f"target language is outside the ko/en scope: {target_language!s}",
        )
    return target_language


__all__ = ["SUPPORTED_TARGET_LANGUAGES", "require_supported_target_language"]
