"""``d(x, R)`` against a reference bank, with the tolerance derived from the bank.

This is the adapter m3–m6 have been waiting for. Those branches stop at ``MISSING``
because a hertz value is a measurement and only a comparison makes it a score, and
the comparison the paper specifies is ``d(x, R) = min_r d(x, r)`` over an exemplar
set.

The step that is easy to get wrong is the last one: turning a distance into ``[0, 1]``
needs a tolerance, and picking a constant would invent the acceptable range that the
whole multi-reference design exists to avoid. So the tolerance is **derived from the
bank's own spread**. If four exemplars of the same sentence differ by 4 semitones,
then 4 semitones is what this sentence tolerates, as measured rather than assumed.
That is the paper's P6 claim made operational: the acceptable range *is* the spread
of acceptable renderings.

A bank with one exemplar therefore yields no score. It has no spread, and scoring
against it is exactly the single-reference-as-ground-truth failure the design rejects.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..domain import (
    AxisDistance,
    MetricState,
    MultiReferenceDistance,
    PromptSpec,
    ReferenceBank,
    ReferenceExemplar,
)
from ..multi_reference import PROSODY_AXES
from .acoustic_branches import AcousticBranchError, AcousticMeasurements

F0_AXIS = "f0"
DURATION_AXIS = "duration"
ENERGY_AXIS = "energy"
RHYTHM_AXIS = "rhythm"
#: A spread needs at least two exemplars to exist.
MINIMUM_EXEMPLARS_FOR_TOLERANCE = 2
#: Beyond the bank's spread the score decays linearly and reaches 0 at twice the
#: spread. Named because it is a policy, not a fact.
DECAY_POLICY = "linear_decay_to_double_tolerance"
#: Which pitch statistic the f0 axis compares. Recorded, not implied: a bank measured
#: under a different statistic is not comparable to one measured under this, and a
#: receipt that only said "f0" would hide that.
#:
#: This used to be absolute median pitch in semitones above 100 Hz, which is a
#: speaker's register rather than anything they chose. Comparing register to a
#: reference bank scores a learner on their voice: a bank of male exemplars puts every
#: woman outside its spread, and a bank spanning both genders has a spread near 18
#: semitones, which admits everyone and says nothing. Pitch *range* is what a speaker
#: controls — flattened intonation is a real L2 pattern and shows up here — and it is
#: comparable across registers by construction.
F0_MEASUREMENT = "voiced_pitch_range_semitones"


class ProsodyDistanceError(ValueError):
    """Raised when a distance cannot be computed within the contract."""


@dataclass(frozen=True, slots=True)
class AxisValues:
    """One recording reduced to the axes a bank can be compared on.

    ``None`` means the axis was not measurable for this recording, which is
    different from measuring zero.
    """

    #: Named for the statistic, not for the axis, so a reader cannot mistake it for
    #: the absolute register this used to hold.
    f0_range_semitones: float | None = None
    duration_seconds: float | None = None
    energy_loudness: float | None = None
    rhythm_npvi: float | None = None

    def get(self, axis: str) -> float | None:
        return {
            F0_AXIS: self.f0_range_semitones,
            DURATION_AXIS: self.duration_seconds,
            ENERGY_AXIS: self.energy_loudness,
            RHYTHM_AXIS: self.rhythm_npvi,
        }[axis]


def read_duration_seconds(audio_path: Path) -> float | None:
    """Duration straight from the container header. ``None`` if unreadable."""

    try:
        import soundfile as sf

        info = sf.info(str(audio_path))
    except (OSError, RuntimeError, ImportError):
        return None
    if not info.samplerate or info.frames <= 0:
        return None
    return float(info.frames) / float(info.samplerate)


def axis_values_from_measurements(
    measurements: AcousticMeasurements,
    *,
    duration_seconds: float | None = None,
    rhythm_npvi: float | None = None,
) -> AxisValues:
    """Project the acoustic worker's reply onto the comparison axes.

    ``duration_seconds`` and ``rhythm_npvi`` come from elsewhere — the interval
    tables — and stay ``None`` when that alignment is not available. Rhythm without
    intervals is not recoverable from the spectrum alone.
    """

    f0 = None
    if measurements.f0_observed():
        assert measurements.f0 is not None
        # The worker already reports this, and its own comment says why: a hertz
        # range would make a low-pitched speaker look monotone by construction.
        observed = measurements.f0.get("range_semitones")
        if observed is not None and float(observed) >= 0:
            f0 = float(observed)
    energy = None
    if measurements.energy_observed():
        assert measurements.energy is not None
        values = measurements.energy.get("values") or {}
        raw = values.get("loudness_sma3_amean")
        if raw is not None:
            energy = float(raw)
    return AxisValues(
        f0_range_semitones=f0,
        duration_seconds=duration_seconds,
        energy_loudness=energy,
        rhythm_npvi=rhythm_npvi,
    )


def derive_tolerance(values: Sequence[float]) -> float | None:
    """The bank's spread on one axis: the widest gap between two exemplars.

    ``None`` when fewer than two exemplars carry the axis, because a set of one has
    no spread and cannot say what counts as close.
    """

    usable = [float(value) for value in values]
    if len(usable) < MINIMUM_EXEMPLARS_FOR_TOLERANCE:
        return None
    spread = max(usable) - min(usable)
    if spread <= 0:
        # Every exemplar identical on this axis: the bank asserts no tolerance at
        # all, which is not a claim it can support from one repeated value.
        return None
    return spread


def score_from_distance(distance: float, tolerance: float) -> float:
    """Inside the bank's spread scores 1.0; outside it decays to 0 at twice."""

    if tolerance <= 0:
        raise ProsodyDistanceError("tolerance must be positive")
    if distance < 0:
        raise ProsodyDistanceError("distance must not be negative")
    if distance <= tolerance:
        return 1.0
    return max(0.0, 1.0 - (distance - tolerance) / tolerance)


#: Resolves an exemplar to a readable audio path. Loop A owns the storage layout, so
#: the adapter is given a resolver rather than guessing one.
ExemplarAudioResolver = Callable[[ReferenceExemplar], Path]


class AcousticProsodyDistance:
    """``ProsodyDistancePort`` backed by the pinned acoustic measurement bridge."""

    def __init__(
        self,
        *,
        backend,
        resolve_exemplar_audio: ExemplarAudioResolver,
        axes: tuple[str, ...] = PROSODY_AXES,
        backend_id: str = "acoustic-prosody-distance",
    ) -> None:
        unknown = [axis for axis in axes if axis not in PROSODY_AXES]
        if unknown:
            raise ProsodyDistanceError(f"unknown axes: {', '.join(sorted(unknown))}")
        self._backend = backend
        self._resolve = resolve_exemplar_audio
        self._axes = tuple(axes)
        self._backend_id = backend_id

    @property
    def backend_id(self) -> str:
        return self._backend_id

    @property
    def f0_measurement(self) -> str:
        """Which pitch statistic this adapter compares.

        Exposed so a receipt can record it. Two banks measured under different
        statistics produce tolerances that are not comparable, and a report that
        only said "f0" would not let a reader notice.
        """

        return F0_MEASUREMENT

    def _measure(self, audio_path: Path, *, duration_seconds: float | None) -> AxisValues:
        measurements = self._backend.measure(audio_path)
        if duration_seconds is None:
            # The learner side has no manifest to read a duration from, and leaving
            # it None would keep the duration axis MISSING forever — including for a
            # bank whose only diversity is speed. It is readable from the file.
            duration_seconds = read_duration_seconds(audio_path)
        return axis_values_from_measurements(
            measurements, duration_seconds=duration_seconds
        )

    def distance_to_bank(
        self,
        *,
        audio_path: Path,
        prompt_spec: PromptSpec,
        bank: ReferenceBank,
    ) -> MultiReferenceDistance:
        if bank.is_empty:
            return MultiReferenceDistance(
                minimum_distance=None,
                nearest_exemplar_id=None,
                state=MetricState.MISSING,
                exemplar_count=0,
            )
        if len(bank.exemplars) < MINIMUM_EXEMPLARS_FOR_TOLERANCE:
            # One exemplar cannot express a tolerance, and scoring against it would
            # make that single rendering the ground truth.
            return MultiReferenceDistance(
                minimum_distance=None,
                nearest_exemplar_id=None,
                state=MetricState.LOW_CONFIDENCE,
                exemplar_count=len(bank.exemplars),
            )

        try:
            learner = self._measure(audio_path, duration_seconds=None)
            exemplar_values: dict[str, AxisValues] = {}
            for exemplar in bank.exemplars:
                exemplar_values[exemplar.exemplar_id] = self._measure(
                    self._resolve(exemplar),
                    duration_seconds=exemplar.duration_seconds,
                )
        except (AcousticBranchError, OSError) as error:
            raise ProsodyDistanceError(
                f"the bank could not be measured: {error}"
            ) from error

        axes: list[AxisDistance] = []
        best_overall: tuple[float, str] | None = None
        for axis in self._axes:
            learner_value = learner.get(axis)
            present = {
                exemplar_id: values.get(axis)
                for exemplar_id, values in exemplar_values.items()
            }
            usable = {k: v for k, v in present.items() if v is not None}
            if learner_value is None or not usable:
                axes.append(
                    AxisDistance(axis=axis, distance=None, state=MetricState.MISSING)
                )
                continue
            nearest_id, nearest_value = min(
                usable.items(), key=lambda item: abs(learner_value - item[1])
            )
            distance = abs(learner_value - nearest_value)
            axes.append(
                AxisDistance(
                    axis=axis,
                    distance=distance,
                    state=MetricState.OBSERVED,
                    nearest_exemplar_id=nearest_id,
                )
            )
            if best_overall is None or distance < best_overall[0]:
                best_overall = (distance, nearest_id)

        if best_overall is None:
            return MultiReferenceDistance(
                minimum_distance=None,
                nearest_exemplar_id=None,
                state=MetricState.MISSING,
                exemplar_count=len(bank.exemplars),
                axes=tuple(axes),
            )
        return MultiReferenceDistance(
            minimum_distance=best_overall[0],
            nearest_exemplar_id=best_overall[1],
            state=MetricState.OBSERVED,
            exemplar_count=len(bank.exemplars),
            axes=tuple(axes),
        )


def axis_reference_scores(
    distance: MultiReferenceDistance,
    bank_axis_values: dict[str, Sequence[float]],
) -> dict[str, float]:
    """Per-axis ``[0, 1]`` agreement, for the axes whose tolerance is derivable.

    The output feeds ``build_prosody_branches`` and ``build_duration_pause_branches``
    as their ``*_reference_score``. Axes without a derivable tolerance are simply
    absent, which keeps those branches ``MISSING`` rather than scoring them against
    an assumed range.
    """

    scores: dict[str, float] = {}
    for axis_distance in distance.axes:
        if axis_distance.state is not MetricState.OBSERVED:
            continue
        if axis_distance.distance is None:
            continue
        tolerance = derive_tolerance(bank_axis_values.get(axis_distance.axis, ()))
        if tolerance is None:
            continue
        scores[axis_distance.axis] = score_from_distance(
            axis_distance.distance, tolerance
        )
    return scores


__all__ = [
    "DECAY_POLICY",
    "DURATION_AXIS",
    "ENERGY_AXIS",
    "F0_AXIS",
    "MINIMUM_EXEMPLARS_FOR_TOLERANCE",
    "RHYTHM_AXIS",
    "F0_MEASUREMENT",
    "AcousticProsodyDistance",
    "AxisValues",
    "ExemplarAudioResolver",
    "ProsodyDistanceError",
    "axis_reference_scores",
    "axis_values_from_measurements",
    "derive_tolerance",
    "read_duration_seconds",
    "score_from_distance",
]
