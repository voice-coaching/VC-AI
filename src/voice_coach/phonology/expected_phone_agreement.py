"""Pure agreement statistics for two expected Korean phone sequences.

The comparison is deliberately descriptive.  The g2pK sequence is a
transcript-conditioned rule prediction and the AI Hub JSON sequence is an
expected annotation; neither input is an observed pronunciation or gold
accuracy label.  This module performs no corpus, split, filesystem, network,
alignment, or threshold work.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from numbers import Integral
from typing import cast
import unicodedata

from voice_coach.phonology.g2pk_expected import (
    EXPECTED_G2PK_VERSION,
    G2PKExpectedPhones,
)
from voice_coach.phonology.phones import KOREAN_COMPATIBILITY_JAMO


EXPECTED_PHONE_AGREEMENT_POLICY_ID = "voice_coach.expected_phone_agreement.v1"
EXPECTED_PHONE_AGREEMENT_HARD_MAX_CELLS = 1_000_000
G2PK_EXPECTED_SOURCE_ROLE = "expected_g2pk_transcript_conditioned"
AIHUB_EXPECTED_SOURCE_ROLE = "expected_aihub_json_annotation"

_CANONICAL_PHONE_SET = frozenset(KOREAN_COMPATIBILITY_JAMO)


class ExpectedPhoneAgreementError(ValueError):
    """Raised when an expected-phone input or resource policy is invalid."""


@dataclass(frozen=True, slots=True)
class ExpectedPhoneAgreementResult:
    """Privacy-minimal descriptive comparison without either phone sequence."""

    evaluation_policy_id: str
    g2pk_source_role: str
    aihub_source_role: str
    g2pk_version: str
    g2pk_descriptive: bool
    g2pk_group_vowels: bool
    g2pk_word_boundary_policy: str
    g2pk_allowed_punctuation_count: int
    g2pk_allowed_punctuation_sha256: str
    g2pk_expected_phone_count: int
    aihub_expected_phone_count: int
    max_alignment_cells: int
    alignment_cell_count: int
    levenshtein_distance: int
    normalized_edit_agreement_rate: float
    is_exact_match: bool
    result_digest_sha256: str

    def to_dict(self) -> dict[str, object]:
        """Return the exact JSON-safe public result fields."""

        return {
            "evaluation_policy_id": self.evaluation_policy_id,
            "g2pk_source_role": self.g2pk_source_role,
            "aihub_source_role": self.aihub_source_role,
            "g2pk_version": self.g2pk_version,
            "g2pk_descriptive": self.g2pk_descriptive,
            "g2pk_group_vowels": self.g2pk_group_vowels,
            "g2pk_word_boundary_policy": self.g2pk_word_boundary_policy,
            "g2pk_allowed_punctuation_count": (self.g2pk_allowed_punctuation_count),
            "g2pk_allowed_punctuation_sha256": (self.g2pk_allowed_punctuation_sha256),
            "g2pk_expected_phone_count": self.g2pk_expected_phone_count,
            "aihub_expected_phone_count": self.aihub_expected_phone_count,
            "max_alignment_cells": self.max_alignment_cells,
            "alignment_cell_count": self.alignment_cell_count,
            "levenshtein_distance": self.levenshtein_distance,
            "normalized_edit_agreement_rate": (self.normalized_edit_agreement_rate),
            "is_exact_match": self.is_exact_match,
            "result_digest_sha256": self.result_digest_sha256,
        }


def _positive_cell_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ExpectedPhoneAgreementError(
            "max_alignment_cells must be a positive integer"
        )
    normalized = int(value)
    if normalized <= 0 or normalized > EXPECTED_PHONE_AGREEMENT_HARD_MAX_CELLS:
        raise ExpectedPhoneAgreementError(
            "max_alignment_cells exceeds the supported resource contract"
        )
    return normalized


def _canonical_phone_tuple(
    value: object,
    *,
    field_name: str,
) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise ExpectedPhoneAgreementError(f"{field_name} must be an exact tuple")
    values = cast(tuple[object, ...], value)
    if not values:
        raise ExpectedPhoneAgreementError(f"{field_name} must not be empty")
    if any(type(token) is not str for token in values):
        raise ExpectedPhoneAgreementError(
            f"{field_name} must contain plain-string phone tokens"
        )
    tokens = cast(tuple[str, ...], values)
    if any(token not in _CANONICAL_PHONE_SET for token in tokens):
        raise ExpectedPhoneAgreementError(
            f"{field_name} contains a token outside the canonical phone policy"
        )
    return tokens


def _punctuation_digest(punctuation: tuple[str, ...]) -> str:
    return hashlib.sha256(
        json.dumps(
            punctuation,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _g2pk_token_digest(
    value: G2PKExpectedPhones,
    phone_tokens: tuple[str, ...],
) -> str:
    payload = {
        "allowed_punctuation": value.allowed_punctuation,
        "descriptive": value.descriptive,
        "g2pk_version": value.g2pk_version,
        "group_vowels": value.group_vowels,
        "phone_tokens": phone_tokens,
        "word_boundary_policy": value.word_boundary_policy,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _validate_g2pk_expected(
    value: object,
) -> tuple[G2PKExpectedPhones, tuple[str, ...]]:
    if type(value) is not G2PKExpectedPhones:
        raise ExpectedPhoneAgreementError(
            "g2pk_expected must be an exact G2PKExpectedPhones value"
        )
    expected = cast(G2PKExpectedPhones, value)
    if (
        type(expected.g2pk_version) is not str
        or expected.g2pk_version != EXPECTED_G2PK_VERSION
    ):
        raise ExpectedPhoneAgreementError(
            "g2pk_expected version differs from the pinned contract"
        )
    if type(expected.descriptive) is not bool:
        raise ExpectedPhoneAgreementError("g2pk_expected descriptive policy is invalid")
    if type(expected.group_vowels) is not bool:
        raise ExpectedPhoneAgreementError(
            "g2pk_expected group-vowels policy is invalid"
        )
    if (
        type(expected.word_boundary_policy) is not str
        or expected.word_boundary_policy != "drop"
    ):
        raise ExpectedPhoneAgreementError(
            "g2pk_expected must use the drop word-boundary policy"
        )
    if type(expected.allowed_punctuation) is not tuple:
        raise ExpectedPhoneAgreementError("g2pk_expected punctuation policy is invalid")
    punctuation = cast(tuple[object, ...], expected.allowed_punctuation)
    if any(
        type(character) is not str
        or len(cast(str, character)) != 1
        or not unicodedata.category(cast(str, character)).startswith("P")
        for character in punctuation
    ):
        raise ExpectedPhoneAgreementError("g2pk_expected punctuation policy is invalid")
    typed_punctuation = cast(tuple[str, ...], punctuation)
    if (
        len(set(typed_punctuation)) != len(typed_punctuation)
        or tuple(sorted(typed_punctuation)) != typed_punctuation
    ):
        raise ExpectedPhoneAgreementError("g2pk_expected punctuation policy is invalid")

    phone_tokens = _canonical_phone_tuple(
        expected.phone_tokens,
        field_name="g2pk_expected.phone_tokens",
    )
    if type(expected.phone_count) is not int or expected.phone_count != len(
        phone_tokens
    ):
        raise ExpectedPhoneAgreementError("g2pk_expected phone count is inconsistent")
    if (
        type(expected.word_boundary_count) is not int
        or expected.word_boundary_count != 0
    ):
        raise ExpectedPhoneAgreementError(
            "g2pk_expected contains an unexpected word boundary"
        )
    if type(
        expected.token_sha256
    ) is not str or expected.token_sha256 != _g2pk_token_digest(expected, phone_tokens):
        raise ExpectedPhoneAgreementError("g2pk_expected token digest is inconsistent")
    return expected, phone_tokens


def _levenshtein_distance(
    left: tuple[str, ...],
    right: tuple[str, ...],
) -> int:
    if len(right) > len(left):
        left, right = right, left

    previous = list(range(len(right) + 1))
    for left_index, left_token in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_token in enumerate(right, start=1):
            substitution_cost = 0 if left_token == right_token else 1
            current.append(
                min(
                    previous[right_index] + 1,
                    current[right_index - 1] + 1,
                    previous[right_index - 1] + substitution_cost,
                )
            )
        previous = current
    return previous[-1]


def _sequence_digest(tokens: tuple[str, ...]) -> str:
    return hashlib.sha256(
        json.dumps(
            tokens,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _result_digest(
    fields: dict[str, object],
    *,
    g2pk_token_sha256: str,
    aihub_token_sha256: str,
) -> str:
    payload = {
        **fields,
        "g2pk_token_sha256": g2pk_token_sha256,
        "aihub_token_sha256": aihub_token_sha256,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def evaluate_expected_phone_agreement(
    *,
    g2pk_expected: G2PKExpectedPhones,
    aihub_expected_phone_tokens: tuple[str, ...],
    max_alignment_cells: int,
) -> ExpectedPhoneAgreementResult:
    """Compute threshold-free unit-cost edit agreement for two expected sources.

    The normalized descriptive rate is
    ``1 - distance / max(sequence lengths)``.  It is symmetric and bounded in
    ``[0, 1]``; it is not an accuracy, observed-phone, or gold-label metric.
    """

    expected, g2pk_tokens = _validate_g2pk_expected(g2pk_expected)
    aihub_tokens = _canonical_phone_tuple(
        aihub_expected_phone_tokens,
        field_name="aihub_expected_phone_tokens",
    )
    maximum_cells = _positive_cell_limit(max_alignment_cells)
    alignment_cells = len(g2pk_tokens) * len(aihub_tokens)
    if alignment_cells > maximum_cells:
        raise ExpectedPhoneAgreementError(
            "expected-phone alignment exceeds max_alignment_cells"
        )

    distance = _levenshtein_distance(g2pk_tokens, aihub_tokens)
    denominator = max(len(g2pk_tokens), len(aihub_tokens))
    agreement_rate = 1.0 - (distance / denominator)
    if agreement_rate == 0.0:
        agreement_rate = 0.0
    punctuation_sha256 = _punctuation_digest(expected.allowed_punctuation)
    fields: dict[str, object] = {
        "evaluation_policy_id": EXPECTED_PHONE_AGREEMENT_POLICY_ID,
        "g2pk_source_role": G2PK_EXPECTED_SOURCE_ROLE,
        "aihub_source_role": AIHUB_EXPECTED_SOURCE_ROLE,
        "g2pk_version": expected.g2pk_version,
        "g2pk_descriptive": expected.descriptive,
        "g2pk_group_vowels": expected.group_vowels,
        "g2pk_word_boundary_policy": expected.word_boundary_policy,
        "g2pk_allowed_punctuation_count": len(expected.allowed_punctuation),
        "g2pk_allowed_punctuation_sha256": punctuation_sha256,
        "g2pk_expected_phone_count": len(g2pk_tokens),
        "aihub_expected_phone_count": len(aihub_tokens),
        "max_alignment_cells": maximum_cells,
        "alignment_cell_count": alignment_cells,
        "levenshtein_distance": distance,
        "normalized_edit_agreement_rate": agreement_rate,
        "is_exact_match": distance == 0,
    }
    digest = _result_digest(
        fields,
        g2pk_token_sha256=expected.token_sha256,
        aihub_token_sha256=_sequence_digest(aihub_tokens),
    )
    return ExpectedPhoneAgreementResult(
        evaluation_policy_id=EXPECTED_PHONE_AGREEMENT_POLICY_ID,
        g2pk_source_role=G2PK_EXPECTED_SOURCE_ROLE,
        aihub_source_role=AIHUB_EXPECTED_SOURCE_ROLE,
        g2pk_version=expected.g2pk_version,
        g2pk_descriptive=expected.descriptive,
        g2pk_group_vowels=expected.group_vowels,
        g2pk_word_boundary_policy=expected.word_boundary_policy,
        g2pk_allowed_punctuation_count=len(expected.allowed_punctuation),
        g2pk_allowed_punctuation_sha256=punctuation_sha256,
        g2pk_expected_phone_count=len(g2pk_tokens),
        aihub_expected_phone_count=len(aihub_tokens),
        max_alignment_cells=maximum_cells,
        alignment_cell_count=alignment_cells,
        levenshtein_distance=distance,
        normalized_edit_agreement_rate=agreement_rate,
        is_exact_match=distance == 0,
        result_digest_sha256=digest,
    )
