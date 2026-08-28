"""The project may train or infer only Korean and English target speech."""

from __future__ import annotations

import pytest

from voice_coach.pronunciation_feedback.domain import FailureCode, TargetLanguage
from voice_coach.pronunciation_feedback.errors import PromptUnavailableError
from voice_coach.pronunciation_feedback.language_scope import (
    SUPPORTED_TARGET_LANGUAGES,
    require_supported_target_language,
)


def test_only_korean_and_english_are_allowed_target_languages() -> None:
    assert SUPPORTED_TARGET_LANGUAGES == {
        TargetLanguage.KOREAN,
        TargetLanguage.ENGLISH,
    }


def test_unknown_target_language_fails_before_analysis() -> None:
    with pytest.raises(PromptUnavailableError) as error:
        require_supported_target_language("fr")  # type: ignore[arg-type]

    assert error.value.code is FailureCode.TARGET_LANGUAGE_NOT_SUPPORTED


