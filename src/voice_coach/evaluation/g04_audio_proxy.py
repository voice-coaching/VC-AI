"""Pure decision rules for the local-only G04 audio proxy.

The proxy keeps the two annotation roles separate. Target phones describe the
prompt's intended pronunciation, while realized phones describe the recording.
Consequently, a genuine learner error is not a target-annotation failure merely
because the target and the audio differ.

This module does not read audio, files, models, or environment variables. The
restricted-data runner owns those adapter responsibilities.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Any, Mapping, Sequence

from voice_coach.data.intervals import Interval, align_intervals
from voice_coach.data.intervals import normalize_phone_label


class AnnotationFidelity(StrEnum):
    """Allowed G04 annotation-fidelity outcomes."""

    CONSISTENT = "consistent"
    TARGET_MISMATCH = "target_annotation_mismatch"
    REALIZED_MISMATCH = "realized_annotation_mismatch"
    BOTH_MISMATCH = "both_annotations_mismatch"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class G04AudioProxyThresholds:
    """Pre-registered thresholds used by the engineering proxy."""

    minimum_model_confidence: float = 0.45
    minimum_target_source_similarity: float = 0.80
    minimum_realized_audio_similarity: float = 0.65
    minimum_any_audio_similarity: float = 0.45

    def __post_init__(self) -> None:
        for name, value in (
            ("minimum_model_confidence", self.minimum_model_confidence),
            (
                "minimum_target_source_similarity",
                self.minimum_target_source_similarity,
            ),
            (
                "minimum_realized_audio_similarity",
                self.minimum_realized_audio_similarity,
            ),
            ("minimum_any_audio_similarity", self.minimum_any_audio_similarity),
        ):
            if not isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be finite and in [0, 1]")


@dataclass(frozen=True, slots=True)
class G04AudioProxyDecision:
    """One privacy-neutral proxy decision."""

    annotation_fidelity: AnnotationFidelity
    target_source_is_consistent: bool
    realized_audio_is_consistent: bool
    abstained: bool
    reason: str


def normalize_phone_sequence(values: Sequence[str]) -> tuple[str, ...]:
    """Normalize phone labels and discard explicit word-boundary markers."""

    return tuple(
        normalized
        for value in values
        if (normalized := normalize_phone_label(str(value).strip()))
        and normalized not in {"|", " "}
    )


def normalized_sequence_similarity(
    left: Sequence[str],
    right: Sequence[str],
) -> float:
    """Return normalized Levenshtein similarity for two phone sequences."""

    normalized_left = normalize_phone_sequence(left)
    normalized_right = normalize_phone_sequence(right)
    denominator = max(len(normalized_left), len(normalized_right))
    if denominator == 0:
        return 1.0

    previous = list(range(len(normalized_right) + 1))
    for left_index, left_value in enumerate(normalized_left, start=1):
        current = [left_index]
        for right_index, right_value in enumerate(normalized_right, start=1):
            substitution_cost = 0 if left_value == right_value else 1
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + substitution_cost,
                )
            )
        previous = current
    distance = previous[-1]
    return max(0.0, 1.0 - distance / denominator)


def classify_annotation_fidelity(
    *,
    target_source_similarity: float,
    target_audio_similarity: float,
    realized_audio_similarity: float,
    model_confidence: float,
    thresholds: G04AudioProxyThresholds,
) -> G04AudioProxyDecision:
    """Classify target and realized annotations without merging their roles."""

    for name, value in (
        ("target_source_similarity", target_source_similarity),
        ("target_audio_similarity", target_audio_similarity),
        ("realized_audio_similarity", realized_audio_similarity),
        ("model_confidence", model_confidence),
    ):
        if not isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be finite and in [0, 1]")

    if model_confidence < thresholds.minimum_model_confidence:
        return G04AudioProxyDecision(
            annotation_fidelity=AnnotationFidelity.UNCERTAIN,
            target_source_is_consistent=False,
            realized_audio_is_consistent=False,
            abstained=True,
            reason="model_confidence_below_threshold",
        )

    target_is_consistent = (
        target_source_similarity >= thresholds.minimum_target_source_similarity
    )
    realized_is_consistent = (
        realized_audio_similarity >= thresholds.minimum_realized_audio_similarity
    )
    if (
        max(target_audio_similarity, realized_audio_similarity)
        < thresholds.minimum_any_audio_similarity
    ):
        return G04AudioProxyDecision(
            annotation_fidelity=AnnotationFidelity.UNCERTAIN,
            target_source_is_consistent=target_is_consistent,
            realized_audio_is_consistent=False,
            abstained=True,
            reason="audio_matches_neither_annotation_with_sufficient_support",
        )

    if target_is_consistent and realized_is_consistent:
        outcome = AnnotationFidelity.CONSISTENT
        reason = "target_matches_prompt_and_realized_matches_audio"
    elif not target_is_consistent and realized_is_consistent:
        outcome = AnnotationFidelity.TARGET_MISMATCH
        reason = "target_differs_from_prompt_but_realized_matches_audio"
    elif target_is_consistent and not realized_is_consistent:
        outcome = AnnotationFidelity.REALIZED_MISMATCH
        reason = "target_matches_prompt_but_realized_differs_from_audio"
    else:
        outcome = AnnotationFidelity.BOTH_MISMATCH
        reason = "target_differs_from_prompt_and_realized_differs_from_audio"

    return G04AudioProxyDecision(
        annotation_fidelity=outcome,
        target_source_is_consistent=target_is_consistent,
        realized_audio_is_consistent=realized_is_consistent,
        abstained=outcome is AnnotationFidelity.UNCERTAIN,
        reason=reason,
    )


def _record_from_alignment(alignment: Any) -> dict[str, Any]:
    starts = [
        interval.start
        for interval in (alignment.target, alignment.realized)
        if interval is not None
    ]
    ends = [
        interval.end
        for interval in (alignment.target, alignment.realized)
        if interval is not None
    ]
    return {
        "target_label": (
            alignment.target.label if alignment.target is not None else None
        ),
        "realized_label": (
            alignment.realized.label if alignment.realized is not None else None
        ),
        "start": min(starts),
        "end": max(ends),
        "overlap_seconds": alignment.overlap_seconds,
        "overlap_ratio": alignment.overlap_ratio,
        "operation": alignment.operation,
    }


def recompute_alignment_records(
    target_intervals: Sequence[Mapping[str, Any]],
    realized_intervals: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Recompute the canonical interval-overlap alignment."""

    targets = [
        Interval(
            start=float(interval["start"]),
            end=float(interval["end"]),
            label=str(interval["label"]),
        )
        for interval in target_intervals
    ]
    realized = [
        Interval(
            start=float(interval["start"]),
            end=float(interval["end"]),
            label=str(interval["label"]),
        )
        for interval in realized_intervals
    ]
    return tuple(
        _record_from_alignment(alignment)
        for alignment in align_intervals(
            targets,
            realized,
            tolerance_seconds=0.03,
            min_overlap_ratio=0.50,
            preserve_one_to_many=False,
        )
    )


def alignment_record_error_count(
    observed: Sequence[Mapping[str, Any]],
    recomputed: Sequence[Mapping[str, Any]],
    *,
    numeric_tolerance: float = 1e-9,
) -> int:
    """Count stored alignment rows that differ from canonical recomputation."""

    if numeric_tolerance < 0 or not isfinite(numeric_tolerance):
        raise ValueError("numeric_tolerance must be finite and non-negative")
    mismatches = abs(len(observed) - len(recomputed))
    for left, right in zip(observed, recomputed):
        for key in ("target_label", "realized_label", "operation"):
            left_value = left.get(key)
            right_value = right.get(key)
            if key.endswith("_label"):
                left_value = (
                    None
                    if left_value is None
                    else normalize_phone_label(str(left_value))
                )
                right_value = (
                    None
                    if right_value is None
                    else normalize_phone_label(str(right_value))
                )
            if left_value != right_value:
                mismatches += 1
                break
        else:
            numeric_keys = (
                "start",
                "end",
                "overlap_seconds",
                "overlap_ratio",
            )
            if any(
                abs(float(left[key]) - float(right[key])) > numeric_tolerance
                for key in numeric_keys
            ):
                mismatches += 1
    return mismatches


__all__ = [
    "AnnotationFidelity",
    "G04AudioProxyDecision",
    "G04AudioProxyThresholds",
    "alignment_record_error_count",
    "classify_annotation_fidelity",
    "normalize_phone_sequence",
    "normalized_sequence_similarity",
    "recompute_alignment_records",
]
