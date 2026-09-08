"""m4/m5 must measure honestly and refuse to turn a measurement into a score.

The real bridge is exercised against synthetic audio with a known F0, so the
measurement path is verified end to end without any dataset. The branch-building
rules are then checked for the failure that would quietly corrupt everything
downstream: inventing a hertz-to-score mapping when no reference bank exists.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from voice_coach.pronunciation_feedback.adapters.acoustic_branches import (
    DEFAULT_INTERPRETER_RELATIVE,
    ENERGY_BRANCH,
    F0_BRANCH,
    NATURALNESS_BRANCH,
    UTMOS_REASON_NO_RUNTIME,
    UTMOS_REASON_NO_WEIGHTS,
    WORKER_PROTOCOL_VERSION,
    AcousticBranchError,
    AcousticMeasurements,
    SubprocessAcousticBackend,
    build_prosody_branches,
    unavailable_naturalness_branch,
)
from voice_coach.pronunciation_feedback.domain import MetricState

ROOT = Path(__file__).resolve().parents[2]
INTERPRETER = ROOT / DEFAULT_INTERPRETER_RELATIVE
requires_acoustics = pytest.mark.skipif(
    not INTERPRETER.is_file(),
    reason="the pinned acoustics interpreter is not present",
)


def _tone(path: Path, *, hz: float, seconds: float = 1.0, sample_rate: int = 16_000) -> Path:
    t = np.arange(int(sample_rate * seconds)) / sample_rate
    envelope = 0.6 + 0.4 * np.sin(2 * np.pi * 2 * t)
    samples = (0.3 * np.sin(2 * np.pi * hz * t) * envelope).astype("float32")
    sf.write(path, samples, sample_rate)
    return path


def _silence(path: Path, *, seconds: float = 1.0, sample_rate: int = 16_000) -> Path:
    sf.write(path, np.zeros(int(sample_rate * seconds), dtype="float32"), sample_rate)
    return path


def _measurements(
    *,
    f0: dict | None = None,
    energy: dict | None = None,
) -> AcousticMeasurements:
    return AcousticMeasurements(
        backend_id="explicit-acoustics-bridge",
        revision="test-rev",
        f0=f0,
        energy=energy,
    )


# -- the real bridge, against audio with a known answer ----------------------


@requires_acoustics
def test_bridge_recovers_a_known_f0(tmp_path: Path) -> None:
    backend = SubprocessAcousticBackend(repository_root=ROOT)

    measured = backend.measure(_tone(tmp_path / "tone.wav", hz=200.0))

    assert measured.f0_observed()
    assert measured.f0 is not None
    assert measured.f0["unit"] == "hertz"
    assert measured.f0["median_hz"] == pytest.approx(200.0, abs=2.0)
    assert 0.0 < measured.f0["voiced_fraction"] <= 1.0


@requires_acoustics
def test_bridge_reports_egemaps_functionals(tmp_path: Path) -> None:
    backend = SubprocessAcousticBackend(repository_root=ROOT)

    measured = backend.measure(_tone(tmp_path / "tone.wav", hz=180.0))

    assert measured.energy_observed()
    assert measured.energy is not None
    assert measured.energy["feature_set"] == "eGeMAPSv02"
    assert "loudness_sma3_amean" in measured.energy["values"]


@requires_acoustics
def test_pitch_range_is_reported_in_semitones(tmp_path: Path) -> None:
    """Hertz range would make a low-pitched speaker look monotone by construction."""

    backend = SubprocessAcousticBackend(repository_root=ROOT)

    measured = backend.measure(_tone(tmp_path / "tone.wav", hz=200.0))

    assert measured.f0 is not None
    assert "range_semitones" in measured.f0
    assert measured.f0["range_semitones"] >= 0.0


@requires_acoustics
def test_silence_yields_no_f0_rather_than_zero_hertz(tmp_path: Path) -> None:
    backend = SubprocessAcousticBackend(repository_root=ROOT)

    measured = backend.measure(_silence(tmp_path / "silence.wav"))

    assert measured.f0 is not None
    assert measured.f0["state"] == "missing"
    assert measured.f0.get("reason") == "no_voiced_frame"
    assert "median_hz" not in measured.f0


@requires_acoustics
def test_absent_audio_fails_closed(tmp_path: Path) -> None:
    backend = SubprocessAcousticBackend(repository_root=ROOT)

    with pytest.raises(AcousticBranchError, match="audio file is unavailable"):
        backend.measure(tmp_path / "absent.wav")


# -- boundary failures --------------------------------------------------------


def test_missing_interpreter_fails_closed(tmp_path: Path) -> None:
    backend = SubprocessAcousticBackend(
        repository_root=ROOT, interpreter=tmp_path / "no-such-python"
    )

    with pytest.raises(AcousticBranchError, match="interpreter is unavailable"):
        backend.measure(tmp_path / "any.wav")


def test_missing_worker_fails_closed(tmp_path: Path) -> None:
    backend = SubprocessAcousticBackend(
        repository_root=ROOT, worker_script=tmp_path / "absent.py"
    )

    with pytest.raises(AcousticBranchError, match="worker script is unavailable"):
        backend.measure(tmp_path / "any.wav")


def test_relative_repository_root_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        SubprocessAcousticBackend(repository_root=Path("relative"))


def test_protocol_drift_fails_closed(tmp_path: Path) -> None:
    stub = tmp_path / "stub.py"
    stub.write_text(
        "import json,sys\n"
        "json.dump({'status':'ok','protocol_version':'v0','measurements':{}}, sys.stdout)\n",
        encoding="utf-8",
    )
    audio = _tone(tmp_path / "tone.wav", hz=150.0)
    import sys as _sys

    backend = SubprocessAcousticBackend(
        repository_root=ROOT,
        interpreter=Path(_sys.executable),
        worker_script=stub,
    )

    with pytest.raises(AcousticBranchError, match="protocol version differs"):
        backend.measure(audio)


def test_worker_reported_failure_is_surfaced(tmp_path: Path) -> None:
    stub = tmp_path / "stub.py"
    stub.write_text(
        "import json,sys\n"
        "json.dump({'status':'failed_closed','error':'runtime absent'}, sys.stdout)\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    import sys as _sys

    backend = SubprocessAcousticBackend(
        repository_root=ROOT,
        interpreter=Path(_sys.executable),
        worker_script=stub,
    )

    with pytest.raises(AcousticBranchError, match="runtime absent"):
        backend.measure(_tone(tmp_path / "tone.wav", hz=150.0))


def test_worker_request_declares_the_protocol_and_features(tmp_path: Path) -> None:
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
    import sys as _sys

    SubprocessAcousticBackend(
        repository_root=ROOT,
        interpreter=Path(_sys.executable),
        worker_script=stub,
    ).measure(_tone(tmp_path / "tone.wav", hz=150.0))

    request = json.loads(captured.read_text(encoding="utf-8"))
    assert request["protocol_version"] == WORKER_PROTOCOL_VERSION
    assert set(request["features"]) == {"f0_parselmouth", "egemaps_functionals"}


# -- a measurement is not a score --------------------------------------------


def test_measured_without_a_reference_carries_no_score() -> None:
    """Inventing a hertz-to-score curve here would invent the reference bank."""

    measured = _measurements(
        f0={"state": "observed", "median_hz": 200.0},
        energy={"state": "observed", "values": {"loudness_sma3_amean": 0.23}},
    )

    branches = build_prosody_branches(measured)

    for branch in branches:
        assert branch.score is None
        assert branch.state is MetricState.MISSING
        assert branch.config_hash == "no_reference_bank_measurement_only"


def test_a_reference_score_makes_the_branch_observed() -> None:
    measured = _measurements(
        f0={"state": "observed", "median_hz": 200.0},
        energy={"state": "observed", "values": {"loudness_sma3_amean": 0.23}},
    )

    branches = build_prosody_branches(
        measured, f0_reference_score=0.82, energy_reference_score=0.7
    )

    by_name = {branch.name: branch for branch in branches}
    assert by_name[F0_BRANCH].state is MetricState.OBSERVED
    assert by_name[F0_BRANCH].score == pytest.approx(0.82)
    assert by_name[ENERGY_BRANCH].score == pytest.approx(0.7)


def test_unmeasured_axis_stays_missing_even_with_a_reference() -> None:
    measured = _measurements(
        f0={"state": "missing", "reason": "no_voiced_frame"},
        energy={"state": "observed", "values": {"loudness_sma3_amean": 0.2}},
    )

    branches = build_prosody_branches(
        measured, f0_reference_score=0.9, energy_reference_score=0.9
    )

    by_name = {branch.name: branch for branch in branches}
    assert by_name[F0_BRANCH].state is MetricState.MISSING
    assert by_name[F0_BRANCH].score is None
    assert by_name[ENERGY_BRANCH].state is MetricState.OBSERVED


def test_absent_measurement_block_yields_a_missing_branch() -> None:
    branches = build_prosody_branches(_measurements(f0=None, energy=None))

    assert {branch.state for branch in branches} == {MetricState.MISSING}


@pytest.mark.parametrize("value", [-0.1, 1.1])
def test_out_of_range_reference_score_fails_closed(value: float) -> None:
    measured = _measurements(f0={"state": "observed", "median_hz": 200.0})

    with pytest.raises(AcousticBranchError, match="within"):
        build_prosody_branches(measured, f0_reference_score=value)


def test_every_branch_names_its_backend_and_revision() -> None:
    branches = build_prosody_branches(
        _measurements(f0={"state": "observed", "median_hz": 200.0})
    )

    for branch in branches:
        assert branch.backend_id
        assert branch.revision


# -- m7 absence is declared, not omitted -------------------------------------


def test_naturalness_is_reported_unavailable_rather_than_dropped() -> None:
    """Omitting it would let fusion renormalise as if naturalness were considered."""

    branch = unavailable_naturalness_branch()

    assert branch.name == NATURALNESS_BRANCH
    assert branch.state is MetricState.BACKEND_UNAVAILABLE
    assert branch.score is None


def test_the_two_m7_blockers_are_distinguishable() -> None:
    """"Install the package" and "obtain the weights" are different next actions."""

    assert UTMOS_REASON_NO_RUNTIME != UTMOS_REASON_NO_WEIGHTS
    no_runtime = unavailable_naturalness_branch(reason=UTMOS_REASON_NO_RUNTIME)
    no_weights = unavailable_naturalness_branch(reason=UTMOS_REASON_NO_WEIGHTS)

    assert no_runtime.config_hash == UTMOS_REASON_NO_RUNTIME
    assert no_weights.config_hash == UTMOS_REASON_NO_WEIGHTS
    # The installed runtime is the current state, so it is the default.
    assert unavailable_naturalness_branch().config_hash == UTMOS_REASON_NO_WEIGHTS


def test_an_undeclared_m7_reason_fails_closed() -> None:
    with pytest.raises(AcousticBranchError, match="unknown m7"):
        unavailable_naturalness_branch(reason="probably_fine")
