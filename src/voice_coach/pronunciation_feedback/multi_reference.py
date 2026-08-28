"""Loop A support: reduce per-exemplar distances to ``d(x, R) = min_r d(x, r)``.

Keeping this reduction in one torch-free place matters because it encodes a
research claim, not a convenience: the learner is judged against the *closest*
acceptable rendering, so widening the exemplar set widens tolerance rather than
penalising anyone who does not match one chosen voice.

Two rules the reducer never bends:

* an empty bank yields ``MetricState.MISSING`` with no number — "we could not
  compare" must not read as "distance zero" or "distance large";
* a per-axis distance that no exemplar could supply stays ``None`` with its own
  state, so a missing F0 track cannot masquerade as perfect prosody.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .domain import (
    AxisDistance,
    MetricState,
    MultiReferenceDistance,
    ReferenceBank,
    ReferenceExemplar,
)

#: Prosodic axes the paper's ``d_prosody`` decomposes into (§5.1, m3–m6).
PROSODY_AXES: tuple[str, ...] = ("f0", "duration", "energy", "rhythm")


class MultiReferenceError(ValueError):
    """Raised when a per-exemplar distance set breaks the contract."""


@dataclass(frozen=True, slots=True)
class ExemplarDistance:
    """Distance from one learner take to one exemplar, per axis.

    ``axes`` maps an axis name to its distance, or to ``None`` when that axis
    could not be measured against this exemplar.
    """

    exemplar_id: str
    axes: Mapping[str, float | None]

    def __post_init__(self) -> None:
        if not self.exemplar_id:
            raise MultiReferenceError("exemplar_id must not be empty")
        unknown = set(self.axes) - set(PROSODY_AXES)
        if unknown:
            raise MultiReferenceError(
                "unknown prosody axis: " + ", ".join(sorted(unknown))
            )
        for axis, value in self.axes.items():
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise MultiReferenceError(f"{axis} distance must be a number")
            if value != value:
                raise MultiReferenceError(f"{axis} distance is NaN")
            if value < 0:
                raise MultiReferenceError(f"{axis} distance must not be negative")

    def overall(self) -> float | None:
        """Mean over the axes this exemplar could supply, or ``None``."""

        measured = [value for value in self.axes.values() if value is not None]
        if not measured:
            return None
        return sum(measured) / len(measured)


def reduce_to_minimum(
    *,
    bank: ReferenceBank,
    distances: Sequence[ExemplarDistance],
) -> MultiReferenceDistance:
    """Take the minimum over exemplars, per axis and overall."""

    known = {item.exemplar_id for item in bank.exemplars}
    for item in distances:
        if item.exemplar_id not in known:
            raise MultiReferenceError(
                f"distance refers to an exemplar outside the bank: {item.exemplar_id}"
            )
    if len({item.exemplar_id for item in distances}) != len(distances):
        raise MultiReferenceError("duplicate exemplar in the distance set")

    if bank.is_empty or not distances:
        return MultiReferenceDistance(
            minimum_distance=None,
            nearest_exemplar_id=None,
            state=MetricState.MISSING,
            exemplar_count=len(bank.exemplars),
            axes=tuple(
                AxisDistance(axis=axis, distance=None, state=MetricState.MISSING)
                for axis in PROSODY_AXES
            ),
        )

    axes: list[AxisDistance] = []
    for axis in PROSODY_AXES:
        candidates = [
            (item.axes[axis], item.exemplar_id)
            for item in distances
            if item.axes.get(axis) is not None
        ]
        if not candidates:
            axes.append(
                AxisDistance(axis=axis, distance=None, state=MetricState.MISSING)
            )
            continue
        value, exemplar_id = min(candidates, key=lambda pair: (pair[0], pair[1]))
        axes.append(
            AxisDistance(
                axis=axis,
                distance=float(value),
                state=MetricState.OBSERVED,
                nearest_exemplar_id=exemplar_id,
            )
        )

    overall = [
        (item.overall(), item.exemplar_id)
        for item in distances
        if item.overall() is not None
    ]
    if not overall:
        # Exemplars exist but no axis was measurable on any of them.
        return MultiReferenceDistance(
            minimum_distance=None,
            nearest_exemplar_id=None,
            state=MetricState.MISSING,
            exemplar_count=len(bank.exemplars),
            axes=tuple(axes),
        )
    minimum, nearest = min(overall, key=lambda pair: (pair[0], pair[1]))
    return MultiReferenceDistance(
        minimum_distance=float(minimum),
        nearest_exemplar_id=nearest,
        state=MetricState.OBSERVED,
        exemplar_count=len(bank.exemplars),
        axes=tuple(axes),
    )


class UnavailableReferenceBank:
    """``ReferenceBankPort`` stand-in for an environment with no rights-cleared bank.

    Declaring the gap is the point: Loop A is absent until D08/R03 produce a bank
    whose every exemplar can state a licence and a rights basis, and until then
    the learner sees a notice rather than an exemplar of unknown provenance.
    """

    def __init__(self, bank_id: str = "unavailable-reference-bank") -> None:
        self._bank_id = bank_id

    @property
    def bank_id(self) -> str:
        return self._bank_id

    def bank_for(
        self,
        *,
        prompt_id: str,
        speed_axis: str | None = None,
        speaker_axis: str | None = None,
    ) -> ReferenceBank:
        del speed_axis, speaker_axis
        return ReferenceBank(
            prompt_id=prompt_id,
            exemplars=(),
            selection_id="",
            selection_rule="no_rights_cleared_exemplar_available",
        )


def filter_rights_cleared(
    exemplars: Sequence[ReferenceExemplar],
    *,
    allowed_licenses: Sequence[str],
) -> tuple[ReferenceExemplar, ...]:
    """Keep only exemplars whose licence is on the allowed list.

    An exemplar is dropped rather than flagged: a learner-facing player has no
    way to represent "you may not hear this", so the safe representation is
    absence.
    """

    allowed = {value for value in allowed_licenses if value}
    if not allowed:
        return ()
    return tuple(item for item in exemplars if item.license_spdx in allowed)


__all__ = [
    "PROSODY_AXES",
    "ExemplarDistance",
    "MultiReferenceError",
    "UnavailableReferenceBank",
    "filter_rights_cleared",
    "reduce_to_minimum",
]
