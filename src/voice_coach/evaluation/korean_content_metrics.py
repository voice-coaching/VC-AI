"""Privacy-minimal Korean transcript normalization and WER/CER primitives.

This module implements only the deterministic S201 text policy and edit
distances.  It does not run Whisper, read a dataset, retain normalized text in
its result, or choose a content-mismatch threshold.  Thresholds must be
supplied explicitly from a separately frozen preregistration.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
import unicodedata


class KoreanContentMetricError(ValueError):
    """Raised when a transcript or threshold violates the S201 contract."""


@dataclass(frozen=True, slots=True)
class EditRate:
    """Aggregate edit-distance evidence without transcript content."""

    edit_distance: int
    reference_unit_count: int
    hypothesis_unit_count: int
    rate: float

    def to_dict(self) -> dict[str, int | float]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class KoreanContentMetrics:
    """WER/CER for one pair, with no raw or normalized transcript values."""

    normalization_policy_id: str
    wer: EditRate
    cer: EditRate

    def to_dict(self) -> dict[str, object]:
        return {
            "normalization_policy_id": self.normalization_policy_id,
            "wer": self.wer.to_dict(),
            "cer": self.cer.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ContentMismatchDecision:
    """A decision made only from explicitly supplied frozen thresholds."""

    content_mismatch: bool
    wer_exceeds_threshold: bool
    cer_exceeds_threshold: bool
    wer_threshold: float
    cer_threshold: float

    def to_dict(self) -> dict[str, bool | float]:
        return asdict(self)


NORMALIZATION_POLICY_ID = "ko_s201_nfc_literal_v1"


def _strict_unicode(value: str | bytes, *, field_name: str) -> str:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise KoreanContentMetricError(f"{field_name} is not strict UTF-8") from exc
    if not isinstance(value, str):
        raise KoreanContentMetricError(f"{field_name} must be str or bytes")
    try:
        return value.encode("utf-8", errors="strict").decode("utf-8")
    except UnicodeError as exc:
        raise KoreanContentMetricError(
            f"{field_name} cannot be represented as strict UTF-8"
        ) from exc


def normalize_korean_transcript(
    value: str | bytes,
    *,
    field_name: str = "transcript",
) -> str:
    """Apply the exact ordered S201 normalization policy.

    The caller must not persist this returned string in an aggregate report.
    Digits, spelling, morphology, and pronunciation are intentionally left
    unchanged.
    """

    text = _strict_unicode(value, field_name=field_name)
    text = unicodedata.normalize("NFC", text)
    text = text.casefold()
    text = "".join(
        " " if unicodedata.category(character).startswith("P") else character
        for character in text
    )
    text = "".join(" " if character.isspace() else character for character in text)
    return " ".join(part for part in text.split(" ") if part)


def levenshtein_distance(
    reference: tuple[str, ...],
    hypothesis: tuple[str, ...],
) -> int:
    """Return exact Levenshtein distance using O(min(m, n)) memory."""

    if len(reference) < len(hypothesis):
        longer = hypothesis
        shorter = reference
    else:
        longer = reference
        shorter = hypothesis
    previous = list(range(len(shorter) + 1))
    for longer_index, longer_value in enumerate(longer, start=1):
        current = [longer_index]
        for shorter_index, shorter_value in enumerate(shorter, start=1):
            insertion = current[-1] + 1
            deletion = previous[shorter_index] + 1
            substitution = previous[shorter_index - 1] + (longer_value != shorter_value)
            current.append(min(insertion, deletion, substitution))
        previous = current
    return previous[-1]


def _edit_rate(
    reference: tuple[str, ...],
    hypothesis: tuple[str, ...],
    *,
    unit_name: str,
) -> EditRate:
    if not reference:
        raise KoreanContentMetricError(f"normalized reference has no {unit_name} units")
    distance = levenshtein_distance(reference, hypothesis)
    return EditRate(
        edit_distance=distance,
        reference_unit_count=len(reference),
        hypothesis_unit_count=len(hypothesis),
        rate=distance / len(reference),
    )


def korean_content_metrics(
    reference: str | bytes,
    hypothesis: str | bytes,
) -> KoreanContentMetrics:
    """Compute literal surface-form WER and CER without retaining text."""

    normalized_reference = normalize_korean_transcript(
        reference,
        field_name="reference",
    )
    normalized_hypothesis = normalize_korean_transcript(
        hypothesis,
        field_name="hypothesis",
    )
    reference_words = (
        tuple(normalized_reference.split(" ")) if normalized_reference else ()
    )
    hypothesis_words = (
        tuple(normalized_hypothesis.split(" ")) if normalized_hypothesis else ()
    )
    reference_characters = tuple(
        character for character in normalized_reference if character != " "
    )
    hypothesis_characters = tuple(
        character for character in normalized_hypothesis if character != " "
    )
    return KoreanContentMetrics(
        normalization_policy_id=NORMALIZATION_POLICY_ID,
        wer=_edit_rate(
            reference_words,
            hypothesis_words,
            unit_name="word",
        ),
        cer=_edit_rate(
            reference_characters,
            hypothesis_characters,
            unit_name="character",
        ),
    )


def decide_content_mismatch(
    metrics: KoreanContentMetrics,
    *,
    wer_threshold: float,
    cer_threshold: float,
) -> ContentMismatchDecision:
    """Apply the frozen S201 OR rule to caller-supplied thresholds."""

    thresholds: dict[str, float] = {}
    for name, value in (
        ("wer_threshold", wer_threshold),
        ("cer_threshold", cer_threshold),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
            or not 0 <= float(value) <= 1
        ):
            raise KoreanContentMetricError(f"{name} must be in [0, 1]")
        thresholds[name] = float(value)
    wer_exceeds = metrics.wer.rate > thresholds["wer_threshold"]
    cer_exceeds = metrics.cer.rate > thresholds["cer_threshold"]
    return ContentMismatchDecision(
        content_mismatch=wer_exceeds or cer_exceeds,
        wer_exceeds_threshold=wer_exceeds,
        cer_exceeds_threshold=cer_exceeds,
        wer_threshold=thresholds["wer_threshold"],
        cer_threshold=thresholds["cer_threshold"],
    )


__all__ = [
    "ContentMismatchDecision",
    "EditRate",
    "KoreanContentMetricError",
    "KoreanContentMetrics",
    "NORMALIZATION_POLICY_ID",
    "decide_content_mismatch",
    "korean_content_metrics",
    "levenshtein_distance",
    "normalize_korean_transcript",
]
