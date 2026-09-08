"""Aggregate-only completeness rules for the locked P8 evaluation.

The contract intentionally carries counts and a frozen policy only.  It must
not contain source locators, item identifiers, prompts, speaker identifiers,
or per-item outcomes.
"""

from __future__ import annotations

from ..errors import VisualFeedbackContractError
from .integrity import require_integer


P8_EVALUATION_EXCLUSION_POLICY = (
    "fail_closed_on_any_admission_exclusion_v1"
)


def validate_p8_evaluation_completeness(
    *,
    admitted_item_count: int,
    paired_item_count: int,
    evaluated_item_count: int,
    evaluation_exclusion_count: int,
    allow_empty: bool,
) -> tuple[int, int, int, int]:
    """Validate aggregate exactly-once pairing/evaluation accounting.

    ``paired_item_count`` means distinct admissions having exactly one accepted
    media/label pairing. ``evaluated_item_count`` means those distinct pairs
    having exactly one aggregate evaluation. Missing pairs, multiple pairings,
    duplicate evaluations, or any other admission that cannot satisfy those
    definitions must be counted once in ``evaluation_exclusion_count``. Every
    admission is therefore accounted for exactly once as paired or excluded,
    and every accepted pair exactly once as evaluated. An exclusion is
    representable only so the caller can emit a durable fail-closed result; it
    can never satisfy a completed or promotion gate.
    """

    minimum_admitted = 0 if allow_empty else 1
    admitted = require_integer(
        admitted_item_count,
        "admitted_item_count",
        minimum=minimum_admitted,
    )
    paired = require_integer(
        paired_item_count,
        "paired_item_count",
        minimum=0,
    )
    evaluated = require_integer(
        evaluated_item_count,
        "evaluated_item_count",
        minimum=0,
    )
    exclusions = require_integer(
        evaluation_exclusion_count,
        "evaluation_exclusion_count",
        minimum=0,
    )
    if admitted != paired + exclusions:
        raise VisualFeedbackContractError(
            "P8 admission pairing/exclusion counts differ"
        )
    if paired != evaluated:
        raise VisualFeedbackContractError(
            "P8 paired/evaluated counts differ"
        )
    if not allow_empty and evaluated == 0:
        raise VisualFeedbackContractError(
            "P8 aggregate evaluation must evaluate at least one item"
        )
    return admitted, paired, evaluated, exclusions


def require_p8_evaluation_exclusion_policy(value: object) -> str:
    """Require the only policy under which exclusions may be represented."""

    if value != P8_EVALUATION_EXCLUSION_POLICY:
        raise VisualFeedbackContractError(
            "P8 evaluation exclusion policy differs"
        )
    return P8_EVALUATION_EXCLUSION_POLICY


__all__ = [
    "P8_EVALUATION_EXCLUSION_POLICY",
    "require_p8_evaluation_exclusion_policy",
    "validate_p8_evaluation_completeness",
]
