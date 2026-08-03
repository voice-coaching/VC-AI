"""Deterministic, label-only error-interval to target-phone mapping.

This module fixes the *rule* used for a future privacy-safe aggregate join.  It
does not read AI Hub rows and it does not turn gold error annotations into model
features.  The source intervals are labels/slices only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math

from .error_tags import ErrorTagId, normalize_error_tag


class ErrorPhoneMatchStatus(StrEnum):
    """Outcome of applying the deterministic localization rule."""

    MATCHED = "matched"
    UNMATCHED = "unmatched"
    NOT_PHONE_LOCALIZABLE = "not_phone_localizable"


PHONE_LOCALIZABLE_ERROR_TAGS = frozenset(
    {
        ErrorTagId.INITIAL_CONSONANT,
        ErrorTagId.FINAL_CONSONANT,
        ErrorTagId.VOWEL_ERROR,
        ErrorTagId.TENSIFICATION,
        ErrorTagId.NASALIZATION,
        ErrorTagId.ASPIRATION,
        ErrorTagId.LIQUIDIZATION,
        ErrorTagId.LIAISON_RULE,
        ErrorTagId.N_INSERTION,
        ErrorTagId.PALATALIZATION,
        ErrorTagId.VOWEL_INSERTION,
        ErrorTagId.PHONEME_DELETION,
    }
)


def _validate_interval(
    *,
    index: int,
    label: str,
    start_seconds: float,
    end_seconds: float,
    kind: str,
) -> None:
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError(f"{kind} index must be a non-negative integer")
    if not isinstance(label, str) or not label.strip():
        raise ValueError(f"{kind} label must be a non-empty string")
    if not math.isfinite(start_seconds) or not math.isfinite(end_seconds):
        raise ValueError(f"{kind} interval bounds must be finite")
    if start_seconds < 0.0 or end_seconds <= start_seconds:
        raise ValueError(
            f"{kind} interval must satisfy 0 <= start_seconds < end_seconds"
        )


@dataclass(frozen=True, slots=True)
class TargetPhoneInterval:
    """One target-phone interval in an already authorized annotation record."""

    index: int
    label: str
    start_seconds: float
    end_seconds: float

    def __post_init__(self) -> None:
        _validate_interval(
            index=self.index,
            label=self.label,
            start_seconds=self.start_seconds,
            end_seconds=self.end_seconds,
            kind="target phone",
        )


@dataclass(frozen=True, slots=True)
class ErrorTagInterval:
    """One source error-tag interval; multiple intervals are never collapsed."""

    index: int
    raw_label: str
    start_seconds: float
    end_seconds: float

    def __post_init__(self) -> None:
        _validate_interval(
            index=self.index,
            label=self.raw_label,
            start_seconds=self.start_seconds,
            end_seconds=self.end_seconds,
            kind="error tag",
        )


@dataclass(frozen=True, slots=True)
class ErrorPhoneAssignment:
    """Privacy-neutral assignment result without record or speaker identifiers."""

    source_error_index: int
    tag_id: ErrorTagId
    match_status: ErrorPhoneMatchStatus
    target_phone_index: int | None
    target_phone_label: str | None
    overlap_seconds: float


def _positive_overlap(
    error: ErrorTagInterval,
    target: TargetPhoneInterval,
) -> float:
    """Return half-open interval overlap, with boundary contact equal to zero."""

    return max(
        0.0,
        min(error.end_seconds, target.end_seconds)
        - max(error.start_seconds, target.start_seconds),
    )


def assign_error_tags_to_target_phones(
    error_intervals: list[ErrorTagInterval],
    target_intervals: list[TargetPhoneInterval],
) -> list[ErrorPhoneAssignment]:
    """Assign each localizable tag to one target by fixed overlap/tie rules.

    The maximum strictly-positive overlap wins.  Exact overlap ties use the
    smallest target-phone index and then the lexical target label.  Every source
    error interval produces exactly one output, so multiple tags on the same
    target remain multiple outputs.  ``other``, ``unknown``, prosodic, and
    non-target fluency tags never acquire a phone claim.
    """

    target_indices = [item.index for item in target_intervals]
    if len(target_indices) != len(set(target_indices)):
        raise ValueError("target phone indices must be unique")
    error_indices = [item.index for item in error_intervals]
    if len(error_indices) != len(set(error_indices)):
        raise ValueError("error tag indices must be unique")

    ordered_targets = sorted(
        target_intervals,
        key=lambda item: (item.index, item.label),
    )
    assignments: list[ErrorPhoneAssignment] = []
    for error in sorted(error_intervals, key=lambda item: item.index):
        tag_id = normalize_error_tag(error.raw_label)
        if tag_id not in PHONE_LOCALIZABLE_ERROR_TAGS:
            assignments.append(
                ErrorPhoneAssignment(
                    source_error_index=error.index,
                    tag_id=tag_id,
                    match_status=ErrorPhoneMatchStatus.NOT_PHONE_LOCALIZABLE,
                    target_phone_index=None,
                    target_phone_label=None,
                    overlap_seconds=0.0,
                )
            )
            continue

        candidates = [
            (overlap, target)
            for target in ordered_targets
            if (overlap := _positive_overlap(error, target)) > 0.0
        ]
        if not candidates:
            assignments.append(
                ErrorPhoneAssignment(
                    source_error_index=error.index,
                    tag_id=tag_id,
                    match_status=ErrorPhoneMatchStatus.UNMATCHED,
                    target_phone_index=None,
                    target_phone_label=None,
                    overlap_seconds=0.0,
                )
            )
            continue

        overlap, target = min(
            candidates,
            key=lambda item: (-item[0], item[1].index, item[1].label),
        )
        assignments.append(
            ErrorPhoneAssignment(
                source_error_index=error.index,
                tag_id=tag_id,
                match_status=ErrorPhoneMatchStatus.MATCHED,
                target_phone_index=target.index,
                target_phone_label=target.label,
                overlap_seconds=overlap,
            )
        )
    return assignments
