"""Expected-phone frontend backed by the pinned offline g2pK adapter.

g2pK is installed in a separate research virtualenv, so in the default
environment this adapter is legitimately unavailable.  It then raises
``BackendUnavailableError`` instead of falling back to written graphemes:
a written jamo stream is not a rule-applied expected pronunciation, and
substituting one for the other would silently change what is being scored.
"""

from __future__ import annotations

from pathlib import Path

from voice_coach.phonology.g2pk_expected import (
    EXPECTED_G2PK_VERSION,
    G2PKExpectedPhoneError,
    WordBoundaryPolicy,
    generate_g2pk_expected_phones,
)

from ..domain import ExpectedPhoneSequence, FailureCode
from ..errors import BackendUnavailableError

BACKEND_ID = "g2pk-expected-phones"
DEFAULT_ALLOWED_PUNCTUATION: tuple[str, ...] = (".",)
DEFAULT_MAXIMUM_CODEPOINTS = 200


class G2PKExpectedPhoneFrontend:
    """``ExpectedPhonePort`` implementation over ``generate_g2pk_expected_phones``."""

    def __init__(
        self,
        *,
        nltk_data_root: str | Path,
        descriptive: bool = False,
        group_vowels: bool = False,
        word_boundary_policy: WordBoundaryPolicy = "preserve",
        allowed_punctuation: tuple[str, ...] = DEFAULT_ALLOWED_PUNCTUATION,
        maximum_codepoints: int = DEFAULT_MAXIMUM_CODEPOINTS,
    ) -> None:
        self._nltk_data_root = Path(nltk_data_root)
        self._descriptive = descriptive
        self._group_vowels = group_vowels
        self._word_boundary_policy = word_boundary_policy
        self._allowed_punctuation = allowed_punctuation
        self._maximum_codepoints = maximum_codepoints

    def expected_phones(self, normalized_text: str) -> ExpectedPhoneSequence:
        try:
            result = generate_g2pk_expected_phones(
                normalized_text,
                descriptive=self._descriptive,
                group_vowels=self._group_vowels,
                word_boundary_policy=self._word_boundary_policy,
                allowed_punctuation=self._allowed_punctuation,
                maximum_codepoints=self._maximum_codepoints,
                nltk_data_root=self._nltk_data_root,
            )
        except (G2PKExpectedPhoneError, ImportError, OSError) as exc:
            raise BackendUnavailableError(
                FailureCode.EXPECTED_PHONES_UNAVAILABLE,
                f"g2pK expected-phone frontend unusable: {exc}",
            ) from exc
        return ExpectedPhoneSequence(
            tokens=result.phone_tokens,
            token_sha256=result.token_sha256,
            backend_id=BACKEND_ID,
            revision=result.g2pk_version,
            per_syllable_token_counts=None,
        )


class UnavailableExpectedPhoneFrontend:
    """Explicit stand-in for environments without the pinned g2pK stack.

    Declaring the gap is the point: any profile that requires expected phones
    fails closed with ``expected_phones_unavailable`` instead of scoring against
    written graphemes.
    """

    def __init__(self, detail: str | None = None) -> None:
        self._detail = detail or (
            "g2pK "
            f"{EXPECTED_G2PK_VERSION} is not installed in this environment"
        )

    def expected_phones(self, normalized_text: str) -> ExpectedPhoneSequence:
        raise BackendUnavailableError(
            FailureCode.EXPECTED_PHONES_UNAVAILABLE, self._detail
        )
