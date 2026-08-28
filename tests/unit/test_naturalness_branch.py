"""m7 must score generated audio and refuse to score learner audio.

The real bridge runs against synthetic audio, so the whole path is verified without a
dataset. The branch rules then check the failure that would matter: showing a learner
a MOS, which answers "does this sound synthetic" rather than "is this pronounced
well".
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from voice_coach.pronunciation_feedback.adapters.naturalness_branch import (
    DEFAULT_INTERPRETER_RELATIVE,
    GENERATED_AUDIO,
    LEARNER_AUDIO,
    NATURALNESS_BRANCH,
    WORKER_PROTOCOL_VERSION,
    NaturalnessBranchError,
    NaturalnessMeasurement,
    SubprocessNaturalnessBackend,
    build_naturalness_branch,
    mos_to_unit_score,
)
from voice_coach.pronunciation_feedback.domain import MetricState

ROOT = Path(__file__).resolve().parents[2]
INTERPRETER = ROOT / DEFAULT_INTERPRETER_RELATIVE
requires_mos = pytest.mark.skipif(
    not INTERPRETER.is_file(), reason="the pinned MOS interpreter is not present"
)


def _tone(path: Path, *, hz: float = 180.0, seconds: float = 2.0) -> Path:
    sample_rate = 16_000
    t = np.arange(int(sample_rate * seconds)) / sample_rate
    sf.write(path, (0.3 * np.sin(2 * np.pi * hz * t)).astype("float32"), sample_rate)
    return path


def _measurement(payload: dict | None) -> NaturalnessMeasurement:
    return NaturalnessMeasurement(
        backend_id="utmosv2-mos-bridge", revision="test-rev", payload=payload
    )


def _observed(mos: float = 3.0) -> NaturalnessMeasurement:
    return _measurement(
        {
            "state": "observed",
            "unit": "mos_1_to_5",
            "mos_mean": mos,
            "mos_by_fold": {"0": mos},
            "folds": [0],
        }
    )


# -- the real bridge ----------------------------------------------------------


@requires_mos
def test_bridge_predicts_a_mos_on_the_defined_scale(tmp_path: Path) -> None:
    backend = SubprocessNaturalnessBackend(repository_root=ROOT)

    measured = backend.measure(_tone(tmp_path / "tone.wav"))

    assert measured.observed()
    assert measured.payload is not None
    assert measured.payload["unit"] == "mos_1_to_5"
    assert 1.0 <= measured.mos_mean <= 5.0
    assert measured.folds == (0,)


@requires_mos
def test_noise_scores_below_a_tone(tmp_path: Path) -> None:
    """A sanity check that the model is loaded and discriminating, not constant."""

    backend = SubprocessNaturalnessBackend(repository_root=ROOT)
    sample_rate = 16_000
    noise = 0.05 * np.random.RandomState(0).randn(sample_rate * 2)
    sf.write(tmp_path / "noise.wav", noise.astype("float32"), sample_rate)

    tone_mos = backend.measure(_tone(tmp_path / "tone.wav")).mos_mean
    noise_mos = backend.measure(tmp_path / "noise.wav").mos_mean

    assert noise_mos < tone_mos


@requires_mos
def test_absent_audio_fails_closed(tmp_path: Path) -> None:
    backend = SubprocessNaturalnessBackend(repository_root=ROOT)

    with pytest.raises(NaturalnessBranchError, match="audio file is unavailable"):
        backend.measure(tmp_path / "absent.wav")


# -- boundary failures --------------------------------------------------------


def test_missing_interpreter_fails_closed(tmp_path: Path) -> None:
    backend = SubprocessNaturalnessBackend(
        repository_root=ROOT, interpreter=tmp_path / "no-such-python"
    )

    with pytest.raises(NaturalnessBranchError, match="interpreter is unavailable"):
        backend.measure(tmp_path / "any.wav")


def test_relative_repository_root_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        SubprocessNaturalnessBackend(repository_root=Path("relative"))


def test_empty_fold_set_is_rejected() -> None:
    with pytest.raises(NaturalnessBranchError, match="at least one fold"):
        SubprocessNaturalnessBackend(repository_root=ROOT, folds=())


def test_unknown_device_is_rejected() -> None:
    with pytest.raises(NaturalnessBranchError, match="device"):
        SubprocessNaturalnessBackend(repository_root=ROOT, device="tpu")


def test_the_fold_set_is_part_of_the_revision() -> None:
    """A single fold and the five-fold mean are different numbers."""

    one = SubprocessNaturalnessBackend(repository_root=ROOT, folds=(0,)).revision
    five = SubprocessNaturalnessBackend(
        repository_root=ROOT, folds=(0, 1, 2, 3, 4)
    ).revision

    assert one != five
    assert one.endswith("folds0")
    assert five.endswith("folds0-1-2-3-4")


def test_protocol_drift_fails_closed(tmp_path: Path) -> None:
    import sys as _sys

    stub = tmp_path / "stub.py"
    stub.write_text(
        "import json,sys\n"
        "json.dump({'status':'ok','protocol_version':'v0','measurements':{}}, sys.stdout)\n",
        encoding="utf-8",
    )
    backend = SubprocessNaturalnessBackend(
        repository_root=ROOT, interpreter=Path(_sys.executable), worker_script=stub
    )

    with pytest.raises(NaturalnessBranchError, match="protocol version differs"):
        backend.measure(_tone(tmp_path / "tone.wav"))


def test_worker_request_declares_folds_and_device(tmp_path: Path) -> None:
    import sys as _sys

    captured = tmp_path / "request.json"
    stub = tmp_path / "stub.py"
    stub.write_text(
        "import json,sys\n"
        "from pathlib import Path\n"
        "req=json.loads(sys.stdin.read())\n"
        f"Path({str(captured)!r}).write_text(json.dumps(req))\n"
        "json.dump({'status':'ok','protocol_version':req['protocol_version'],"
        "'measurements':{}}, sys.stdout)\n",
        encoding="utf-8",
    )

    SubprocessNaturalnessBackend(
        repository_root=ROOT,
        interpreter=Path(_sys.executable),
        worker_script=stub,
        folds=(0, 2),
    ).measure(_tone(tmp_path / "tone.wav"))

    request = json.loads(captured.read_text(encoding="utf-8"))
    assert request["protocol_version"] == WORKER_PROTOCOL_VERSION
    assert request["folds"] == [0, 2]
    assert request["device"] == "cpu"


# -- the MOS scale ------------------------------------------------------------


def test_the_scale_endpoints_define_the_mapping() -> None:
    """1-5 has endpoints; hertz does not. That is why m7 may score and m4 may not."""

    assert mos_to_unit_score(1.0) == pytest.approx(0.0)
    assert mos_to_unit_score(5.0) == pytest.approx(1.0)
    assert mos_to_unit_score(3.0) == pytest.approx(0.5)


@pytest.mark.parametrize("value", [0.9, 5.1])
def test_a_mos_outside_the_scale_fails_closed(value: float) -> None:
    with pytest.raises(NaturalnessBranchError, match="1-5 scale"):
        mos_to_unit_score(value)


# -- population ---------------------------------------------------------------


def test_generated_audio_is_scored() -> None:
    branch = build_naturalness_branch(_observed(4.0), audio_population=GENERATED_AUDIO)

    assert branch.name == NATURALNESS_BRANCH
    assert branch.state is MetricState.OBSERVED
    assert branch.score == pytest.approx(0.75)


def test_learner_audio_is_out_of_distribution_not_scored() -> None:
    """A MOS on a learner answers "does this sound synthetic", not "is this good"."""

    branch = build_naturalness_branch(_observed(2.0), audio_population=LEARNER_AUDIO)

    assert branch.state is MetricState.OUT_OF_DISTRIBUTION
    assert branch.score is None
    assert branch.config_hash == "utmos_trained_on_synthesised_speech_only"


def test_the_population_must_be_declared_not_guessed() -> None:
    with pytest.raises(NaturalnessBranchError, match="unknown audio population"):
        build_naturalness_branch(_observed(), audio_population="probably_generated")


def test_an_unpredicted_recording_is_missing_with_its_reason() -> None:
    branch = build_naturalness_branch(
        _measurement({"state": "missing", "reason": "prediction_outside_mos_scale"})
    )

    assert branch.state is MetricState.MISSING
    assert branch.score is None
    assert branch.config_hash == "prediction_outside_mos_scale"


def test_an_absent_payload_is_missing() -> None:
    branch = build_naturalness_branch(_measurement(None))

    assert branch.state is MetricState.MISSING
    assert branch.config_hash == "no_prediction"


def test_the_branch_names_its_backend_and_revision() -> None:
    branch = build_naturalness_branch(_observed())

    assert branch.backend_id
    assert branch.revision
