"""Deterministic Levenshtein phone alignment."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

Operation = Literal["correct", "substitution", "deletion", "insertion"]


@dataclass(frozen=True)
class Alignment:
    operation: Operation
    reference: str | None
    hypothesis: str | None


def align_phones(
    reference: Sequence[str], hypothesis: Sequence[str]
) -> list[Alignment]:
    rows, columns = len(reference) + 1, len(hypothesis) + 1
    costs = [[0] * columns for _ in range(rows)]
    back: list[list[Operation | None]] = [[None] * columns for _ in range(rows)]
    for row in range(1, rows):
        costs[row][0] = row
        back[row][0] = "deletion"
    for column in range(1, columns):
        costs[0][column] = column
        back[0][column] = "insertion"
    priority = {"correct": 0, "substitution": 1, "deletion": 2, "insertion": 3}
    for row in range(1, rows):
        for column in range(1, columns):
            diagonal: Operation = (
                "correct"
                if reference[row - 1] == hypothesis[column - 1]
                else "substitution"
            )
            options = [
                (costs[row - 1][column - 1] + (diagonal != "correct"), diagonal),
                (costs[row - 1][column] + 1, "deletion"),
                (costs[row][column - 1] + 1, "insertion"),
            ]
            costs[row][column], back[row][column] = min(
                options, key=lambda item: (item[0], priority[item[1]])
            )
    result: list[Alignment] = []
    row, column = len(reference), len(hypothesis)
    while row or column:
        operation = back[row][column]
        if operation in {"correct", "substitution"}:
            result.append(
                Alignment(operation, reference[row - 1], hypothesis[column - 1])
            )
            row -= 1
            column -= 1
        elif operation == "deletion":
            result.append(Alignment(operation, reference[row - 1], None))
            row -= 1
        elif operation == "insertion":
            result.append(Alignment(operation, None, hypothesis[column - 1]))
            column -= 1
        else:
            raise RuntimeError("alignment backtrace is incomplete")
    result.reverse()
    return result
