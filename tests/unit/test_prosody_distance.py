"""d(x, R) must take its tolerance from the bank, never from a chosen constant.

The end-to-end path runs on synthetic tones with known pitches, so a real bank, a
real measurement bridge and a real distance are exercised without any dataset. The
rules that matter are checked directly: a one-exemplar bank cannot score, and the
spread of the exemplars is what decides how far is too far.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from voice_coach.pronunciation_feedback.adapters.acoustic_branches import (
    DEFAULT_INTERPRETER_RELATIVE,
    AcousticMeasurements,
    SubprocessAcousticBackend,
)
from voice_coach.pronunciation_feedback.adapters.prosody_distance import (
    DURATION_AXIS,
    ENERGY_AXIS,
    F0_AXIS,
    F0_MEASUREMENT,
    MINIMUM_EXEMPLARS_FOR_TOLERANCE,
    AcousticProsodyDistance,
    AxisValues,
    ProsodyDistanceError,
    axis_reference_scores,
    axis_values_from_measurements,
    derive_tolerance,
    score_from_distance,
)
from voice_coach.pronunciation_feedback.domain import (
    MetricState,
    MultiReferenceDistance,
    PromptSpec,
    ReferenceBank,
    ReferenceExemplar,
    TargetLanguage,
)

ROOT = Path(__file__).resolve().parents[2]
INTERPRETER = ROOT / DEFAULT_INTERPRETER_RELATIVE
requires_acoustics = pytest.mark.skipif(
    not INTERPRETER.is_file(),
    reason="the pinned acoustics interpreter is not present",
)


def _tone(path: Path, *, hz: float, seconds: float = 1.0) -> Path:
    sample_rate = 16_000
    t = np.arange(int(sample_rate * seconds)) / sample_rate
    sf.write(path, (0.3 * np.sin(2 * np.pi * hz * t)).astype("float32"), sample_rate)
    return path


def _glide(
    path: Path, *, hz: float, semitone_span: float, seconds: float = 1.0
) -> Path:
    """A linear pitch glide of a stated width, starting at ``hz``.

    A constant tone has no pitch range, so it cannot exercise the axis at all: the
    f0 axis compares how far a speaker moved their pitch, not where it sat. The
    glide's instantaneous frequency rises by ``semitone_span`` over its length, and
    the phase is the integral of that so the sweep is continuous.
    """

    sample_rate = 16_000
    t = np.arange(int(sample_rate * seconds)) / sample_rate
    top = hz * 2.0 ** (semitone_span / 12.0)
    # Linear-in-time frequency sweep: phase = 2*pi*(f0*t + (f1-f0)*t^2/(2*T)).
    phase = 2 * np.pi * (hz * t + (top - hz) * t**2 / (2 * seconds))
    sf.write(path, (0.3 * np.sin(phase)).astype("float32"), sample_rate)
    return path


def _exemplar(index: int, *, prompt_id: str = "R00-01") -> ReferenceExemplar:
    return ReferenceExemplar(
        exemplar_id=f"ex{index}",
        prompt_id=prompt_id,
        generator_id="test-generator",
        generator_revision="rev-1",
        audio_sha256="a" * 64,
        duration_seconds=1.0,
        sample_rate=16_000,
        speaker_axis=f"spk{index}",
        speed_axis="neutral",
        license_spdx="CC0-1.0",
        rights_basis="test fixture",
        synthetic=True,
        disclosure="synthesised for testing",
    )


def _bank(count: int, *, prompt_id: str = "R00-01") -> ReferenceBank:
    return ReferenceBank(
        prompt_id=prompt_id,
        exemplars=tuple(_exemplar(i, prompt_id=prompt_id) for i in range(count)),
        selection_id="sel-1",
        selection_rule="test",
    )


def _prompt() -> PromptSpec:
    return PromptSpec(
        prompt_id="R00-01",
        prompt_version="v1",
        target_language=TargetLanguage.KOREAN,
        normalized_text="테스트",
        normalized_text_sha256="c" * 64,
        syllables=(),
        syllable_map_sha256="d" * 64,
        expected_phones=None,
        expected_phone_state=MetricState.MISSING,
    )


# -- units --------------------------------------------------------------------


def test_the_pitch_axis_compares_range_not_register() -> None:
    """Two speakers an octave apart who move their pitch alike must not differ here.

    Absolute register is not something a learner chose. Comparing it to a reference
    bank scores a woman against a male-only bank for having a woman's voice, and a
    bank spanning both genders has a ~18 semitone spread that admits everyone.
    """

    low_voice = axis_values_from_measurements(
        AcousticMeasurements(
            backend_id="b",
            revision="r",
            f0={"state": "observed", "median_hz": 95.0, "range_semitones": 7.0},
            energy=None,
        )
    )
    high_voice = axis_values_from_measurements(
        AcousticMeasurements(
            backend_id="b",
            revision="r",
            f0={"state": "observed", "median_hz": 240.0, "range_semitones": 7.0},
            energy=None,
        )
    )

    assert low_voice.get(F0_AXIS) == pytest.approx(7.0)
    assert high_voice.get(F0_AXIS) == pytest.approx(7.0)
    assert F0_MEASUREMENT == "voiced_pitch_range_semitones"


def test_a_flattened_delivery_is_distinguishable_from_a_varied_one() -> None:
    """The pattern the axis exists to catch: L2 intonation collapsing toward flat."""

    flat = axis_values_from_measurements(
        AcousticMeasurements(
            backend_id="b",
            revision="r",
            f0={"state": "observed", "median_hz": 120.0, "range_semitones": 1.2},
            energy=None,
        )
    )
    varied = axis_values_from_measurements(
        AcousticMeasurements(
            backend_id="b",
            revision="r",
            f0={"state": "observed", "median_hz": 120.0, "range_semitones": 9.5},
            energy=None,
        )
    )

    assert flat.get(F0_AXIS) < varied.get(F0_AXIS)


def test_a_reply_without_a_range_leaves_the_axis_unmeasured() -> None:
    """An older worker reply has no range field; that is absence, not zero."""

    values = axis_values_from_measurements(
        AcousticMeasurements(
            backend_id="b",
            revision="r",
            f0={"state": "observed", "median_hz": 120.0},
            energy=None,
        )
    )

    assert values.get(F0_AXIS) is None


def test_axis_values_keep_an_unmeasured_axis_none() -> None:
    values = axis_values_from_measurements(
        AcousticMeasurements(
            backend_id="b",
            revision="r",
            f0={"state": "missing", "reason": "no_voiced_frame"},
            energy={"state": "observed", "values": {"loudness_sma3_amean": 0.2}},
        )
    )

    assert values.f0_range_semitones is None
    assert values.energy_loudness == pytest.approx(0.2)
    assert values.rhythm_npvi is None


# -- the tolerance comes from the bank ---------------------------------------


def test_tolerance_is_the_spread_of_the_exemplars() -> None:
    assert derive_tolerance([10.0, 12.0, 14.0]) == pytest.approx(4.0)


def test_one_exemplar_has_no_spread() -> None:
    assert derive_tolerance([10.0]) is None
    assert derive_tolerance([]) is None
    assert MINIMUM_EXEMPLARS_FOR_TOLERANCE == 2


def test_identical_exemplars_assert_no_tolerance() -> None:
    """A repeated value is not evidence that the range is zero."""

    assert derive_tolerance([10.0, 10.0, 10.0]) is None


def test_inside_the_spread_scores_one() -> None:
    assert score_from_distance(0.0, 4.0) == pytest.approx(1.0)
    assert score_from_distance(4.0, 4.0) == pytest.approx(1.0)


def test_outside_the_spread_decays_to_zero_at_double() -> None:
    assert score_from_distance(6.0, 4.0) == pytest.approx(0.5)
    assert score_from_distance(8.0, 4.0) == pytest.approx(0.0)
    assert score_from_distance(20.0, 4.0) == pytest.approx(0.0)


def test_a_wider_bank_forgives_more() -> None:
    """The same learner scores higher against a bank that spans more styles."""

    narrow = score_from_distance(3.0, 2.0)
    wide = score_from_distance(3.0, 6.0)

    assert wide > narrow
    assert wide == pytest.approx(1.0)


@pytest.mark.parametrize("tolerance", [0.0, -1.0])
def test_non_positive_tolerance_fails_closed(tolerance: float) -> None:
    with pytest.raises(ProsodyDistanceError, match="tolerance"):
        score_from_distance(1.0, tolerance)


# -- the real bridge, end to end ---------------------------------------------


@requires_acoustics
def test_distance_to_a_real_bank_of_glides(tmp_path: Path) -> None:
    """The bank varies in how far the pitch moves, which is what the axis compares."""

    paths = {
        "ex0": _glide(tmp_path / "e0.wav", hz=180.0, semitone_span=3.0),
        "ex1": _glide(tmp_path / "e1.wav", hz=200.0, semitone_span=6.0),
        "ex2": _glide(tmp_path / "e2.wav", hz=220.0, semitone_span=9.0),
    }
    port = AcousticProsodyDistance(
        backend=SubprocessAcousticBackend(repository_root=ROOT),
        resolve_exemplar_audio=lambda exemplar: paths[exemplar.exemplar_id],
    )

    result = port.distance_to_bank(
        # A different register from every exemplar, but the same movement as ex1.
        audio_path=_glide(tmp_path / "learner.wav", hz=300.0, semitone_span=6.0),
        prompt_spec=_prompt(),
        bank=_bank(3),
    )

    assert result.state is MetricState.OBSERVED
    assert result.exemplar_count == 3
    f0 = next(axis for axis in result.axes if axis.axis == F0_AXIS)
    assert f0.state is MetricState.OBSERVED
    # Register differs by 6 semitones from ex1 and by 9 from ex2; movement matches
    # ex1. Matching on movement is the whole point of the change.
    assert f0.nearest_exemplar_id == "ex1"
    assert f0.distance < 1.5


@requires_acoustics
def test_a_flatter_learner_is_further_from_the_same_bank(tmp_path: Path) -> None:
    """Distance grows with how much less the learner moved their pitch."""

    paths = {
        "ex0": _glide(tmp_path / "e0.wav", hz=180.0, semitone_span=6.0),
        "ex1": _glide(tmp_path / "e1.wav", hz=200.0, semitone_span=8.0),
        "ex2": _glide(tmp_path / "e2.wav", hz=220.0, semitone_span=10.0),
    }
    port = AcousticProsodyDistance(
        backend=SubprocessAcousticBackend(repository_root=ROOT),
        resolve_exemplar_audio=lambda exemplar: paths[exemplar.exemplar_id],
    )

    def f0_distance(semitone_span: float) -> float:
        result = port.distance_to_bank(
            audio_path=_glide(
                tmp_path / f"l{semitone_span:.0f}.wav",
                hz=210.0,
                semitone_span=semitone_span,
            ),
            prompt_spec=_prompt(),
            bank=_bank(3),
        )
        axis = next(a for a in result.axes if a.axis == F0_AXIS)
        assert axis.distance is not None
        return axis.distance

    # 1 semitone of movement is far flatter than the bank; 6 matches ex0 exactly.
    assert f0_distance(1.0) > f0_distance(6.0)


# -- banks that cannot support a judgement -----------------------------------


def test_an_empty_bank_yields_missing(tmp_path: Path) -> None:
    port = AcousticProsodyDistance(
        backend=SubprocessAcousticBackend(repository_root=ROOT),
        resolve_exemplar_audio=lambda exemplar: tmp_path / "unused.wav",
    )

    result = port.distance_to_bank(
        audio_path=tmp_path / "learner.wav",
        prompt_spec=_prompt(),
        bank=ReferenceBank(
            prompt_id="R00-01", exemplars=(), selection_id="", selection_rule=""
        ),
    )

    assert result.state is MetricState.MISSING
    assert result.minimum_distance is None
    assert result.exemplar_count == 0


def test_a_single_exemplar_bank_is_low_confidence_not_a_score(tmp_path: Path) -> None:
    """Scoring against one rendering would make that rendering the ground truth."""

    port = AcousticProsodyDistance(
        backend=SubprocessAcousticBackend(repository_root=ROOT),
        resolve_exemplar_audio=lambda exemplar: tmp_path / "unused.wav",
    )

    result = port.distance_to_bank(
        audio_path=tmp_path / "learner.wav",
        prompt_spec=_prompt(),
        bank=_bank(1),
    )

    assert result.state is MetricState.LOW_CONFIDENCE
    assert result.minimum_distance is None
    assert result.exemplar_count == 1


def test_unknown_axis_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ProsodyDistanceError, match="unknown axes"):
        AcousticProsodyDistance(
            backend=SubprocessAcousticBackend(repository_root=ROOT),
            resolve_exemplar_audio=lambda exemplar: tmp_path / "x.wav",
            axes=("f0", "timbre"),
        )


def test_a_broken_backend_fails_closed(tmp_path: Path) -> None:
    class Broken:
        def measure(self, path: Path):
            raise OSError("disk gone")

    port = AcousticProsodyDistance(
        backend=Broken(), resolve_exemplar_audio=lambda exemplar: tmp_path / "x.wav"
    )

    with pytest.raises(ProsodyDistanceError, match="could not be measured"):
        port.distance_to_bank(
            audio_path=tmp_path / "learner.wav",
            prompt_spec=_prompt(),
            bank=_bank(3),
        )


# -- feeding the branches -----------------------------------------------------


def _distance(axis: str, value: float) -> MultiReferenceDistance:
    from voice_coach.pronunciation_feedback.domain import AxisDistance

    return MultiReferenceDistance(
        minimum_distance=value,
        nearest_exemplar_id="ex1",
        state=MetricState.OBSERVED,
        exemplar_count=3,
        axes=(
            AxisDistance(
                axis=axis,
                distance=value,
                state=MetricState.OBSERVED,
                nearest_exemplar_id="ex1",
            ),
        ),
    )


def test_axis_scores_are_produced_only_where_a_tolerance_exists() -> None:
    scores = axis_reference_scores(
        _distance(F0_AXIS, 1.0), {F0_AXIS: [10.0, 12.0, 14.0]}
    )

    assert scores == {F0_AXIS: pytest.approx(1.0)}


def test_an_axis_without_a_derivable_tolerance_produces_no_score() -> None:
    """Silence here keeps the branch MISSING instead of scoring against a guess."""

    assert axis_reference_scores(_distance(F0_AXIS, 1.0), {F0_AXIS: [10.0]}) == {}
    assert axis_reference_scores(_distance(F0_AXIS, 1.0), {}) == {}


def test_a_far_learner_scores_below_one() -> None:
    scores = axis_reference_scores(
        _distance(DURATION_AXIS, 3.0), {DURATION_AXIS: [1.0, 3.0]}
    )

    assert scores[DURATION_AXIS] == pytest.approx(0.5)


def test_a_missing_axis_is_skipped() -> None:
    from voice_coach.pronunciation_feedback.domain import AxisDistance

    distance = MultiReferenceDistance(
        minimum_distance=None,
        nearest_exemplar_id=None,
        state=MetricState.MISSING,
        exemplar_count=3,
        axes=(
            AxisDistance(axis=ENERGY_AXIS, distance=None, state=MetricState.MISSING),
        ),
    )

    assert axis_reference_scores(distance, {ENERGY_AXIS: [0.1, 0.5]}) == {}


# -- the learner's own duration ----------------------------------------------


def test_duration_is_readable_from_the_file(tmp_path: Path) -> None:
    path = _tone(tmp_path / "d.wav", hz=200.0, seconds=1.75)

    from voice_coach.pronunciation_feedback.adapters.prosody_distance import (
        read_duration_seconds,
    )

    assert read_duration_seconds(path) == pytest.approx(1.75, abs=0.01)


def test_an_unreadable_file_yields_no_duration(tmp_path: Path) -> None:
    from voice_coach.pronunciation_feedback.adapters.prosody_distance import (
        read_duration_seconds,
    )

    assert read_duration_seconds(tmp_path / "absent.wav") is None


@requires_acoustics
def test_the_duration_axis_is_observed_without_a_supplied_duration(
    tmp_path: Path,
) -> None:
    """The learner has no manifest, so the duration must come from the audio.

    Leaving it unmeasured kept this axis MISSING even for a bank whose only
    diversity is speed — the one axis such a bank can actually judge.
    """

    paths = {
        "ex0": _tone(tmp_path / "e0.wav", hz=200.0, seconds=1.0),
        "ex1": _tone(tmp_path / "e1.wav", hz=200.0, seconds=2.0),
        "ex2": _tone(tmp_path / "e2.wav", hz=200.0, seconds=3.0),
    }
    port = AcousticProsodyDistance(
        backend=SubprocessAcousticBackend(repository_root=ROOT),
        resolve_exemplar_audio=lambda exemplar: paths[exemplar.exemplar_id],
    )

    result = port.distance_to_bank(
        audio_path=_tone(tmp_path / "learner.wav", hz=200.0, seconds=2.1),
        prompt_spec=_prompt(),
        bank=_bank(3),
    )

    axis = next(a for a in result.axes if a.axis == DURATION_AXIS)
    assert axis.state is MetricState.OBSERVED
    # 2.1 s sits next to the 2.0 s exemplar, whose manifest duration is 1.0 s in the
    # fixture; the resolver's real file is what gets measured.
    assert axis.distance is not None
