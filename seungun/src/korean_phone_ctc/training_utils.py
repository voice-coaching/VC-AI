"""Small deterministic helpers shared by CTC training and evaluation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .evaluation.phone_alignment import align_phones


def collapse_ctc_ids(
    ids: Sequence[int],
    *,
    blank_id: int = 0,
    padding_id: int = -100,
) -> list[int]:
    """Collapse CTC repeats while removing blanks and Trainer padding."""

    result: list[int] = []
    previous: int | None = None
    for token_id in ids:
        if token_id == padding_id:
            continue
        if token_id != previous and token_id != blank_id:
            result.append(token_id)
        previous = token_id
    return result


@dataclass(frozen=True)
class PhoneErrorMetrics:
    reference_phones: int
    substitutions: int
    deletions: int
    insertions: int
    empty_hypotheses: int
    utterances: int

    @property
    def per(self) -> float:
        if not self.reference_phones:
            return 0.0
        return (
            self.substitutions + self.deletions + self.insertions
        ) / self.reference_phones

    def as_dict(self) -> dict[str, float | int]:
        denominator = max(self.reference_phones, 1)
        return {
            "per": self.per,
            "reference_phones": self.reference_phones,
            "substitutions": self.substitutions,
            "substitution_rate": self.substitutions / denominator,
            "deletions": self.deletions,
            "deletion_rate": self.deletions / denominator,
            "insertions": self.insertions,
            "insertion_rate": self.insertions / denominator,
            "empty_hypotheses": self.empty_hypotheses,
            "empty_decode_rate": self.empty_hypotheses / max(self.utterances, 1),
            "utterances": self.utterances,
        }


def score_phone_sequences(
    references: Sequence[Sequence[str]],
    hypotheses: Sequence[Sequence[str]],
) -> tuple[PhoneErrorMetrics, Counter[tuple[str, str]]]:
    if len(references) != len(hypotheses):
        raise ValueError("reference and hypothesis counts differ")
    substitutions = deletions = insertions = empty = total = 0
    confusion: Counter[tuple[str, str]] = Counter()
    for reference, hypothesis in zip(references, hypotheses, strict=True):
        total += len(reference)
        empty += int(not hypothesis)
        for item in align_phones(reference, hypothesis):
            if item.operation == "substitution":
                substitutions += 1
                confusion[(item.reference or "", item.hypothesis or "")] += 1
            elif item.operation == "deletion":
                deletions += 1
                confusion[(item.reference or "", "<del>")] += 1
            elif item.operation == "insertion":
                insertions += 1
                confusion[("<ins>", item.hypothesis or "")] += 1
    return (
        PhoneErrorMetrics(
            reference_phones=total,
            substitutions=substitutions,
            deletions=deletions,
            insertions=insertions,
            empty_hypotheses=empty,
            utterances=len(references),
        ),
        confusion,
    )


def validate_manual_alignment_gate(payload: Mapping[str, Any]) -> None:
    """Reject training unless an independent audit explicitly passed."""

    status = str(payload.get("status", "")).casefold()
    accuracy = payload.get("alignment_accuracy", payload.get("accuracy"))
    if status != "passed":
        raise ValueError("manual alignment audit status is not passed")
    if not isinstance(accuracy, int | float) or accuracy < 0.95:
        raise ValueError("manual alignment audit accuracy is below 0.95")


def validate_training_gate(
    payload: Mapping[str, Any],
    *,
    mode: str,
    allow_ungated_diagnostic_smoke: bool = False,
    allow_ungated_internal_full: bool = False,
) -> None:
    """Enforce the independent manual-audit gate for every training mode.

    The two legacy keyword arguments remain temporarily for callers that need a
    clear migration error.  Neither diagnostic smoke nor an internal label may
    bypass the audit gate.
    """

    if allow_ungated_diagnostic_smoke or allow_ungated_internal_full:
        raise ValueError("ungated training overrides are disabled")
    if mode not in {"smoke", "full"}:
        raise ValueError("training mode must be smoke or full")

    validate_manual_alignment_gate(payload)
