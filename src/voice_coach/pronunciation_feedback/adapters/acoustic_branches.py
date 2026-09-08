"""m4/m5 explicit acoustic branches, and the reason a measurement is not yet a score.

`explicit.py` and the paper's Branch B produce *measurements*: an F0 median in
hertz, a loudness mean in the openSMILE scale. The gate and the fusion consume
*scores* in ``[0, 1]``. The step between them is not a normalisation constant — it
is a comparison against what an acceptable rendering sounds like, which in this
architecture is ``d(x, R)`` against a reference bank.

So this module deliberately stops short. It measures, and it builds an
:class:`S1Branch` that says ``MISSING`` whenever no reference distance was supplied.
Picking an arbitrary hertz-to-score curve here would silently invent the reference
that Loop A has not yet produced, and every downstream number would inherit it.

m7 (UTMOSv2) has a runtime here (``.venv-paper-mos``) but no weights: the fold
checkpoints and the ``facebook/wav2vec2-base`` encoder it wraps cannot be fetched in
this environment. It is represented by an explicit unavailable branch rather than
omitted, and the branch names which of the two preconditions failed.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping

from ..domain import MetricState, S1Branch

WORKER_PROTOCOL_VERSION = "voice_coach.acoustic_features_worker.v1"
DEFAULT_WORKER_RELATIVE = Path("scripts/acoustic_features_worker.py")
DEFAULT_INTERPRETER_RELATIVE = Path(".venv-paper-acoustics/bin/python")
DEFAULT_TIMEOUT_SECONDS = 120.0
F0_BRANCH = "f0"
ENERGY_BRANCH = "energy"
NATURALNESS_BRANCH = "naturalness"
#: Why m7 is unavailable. Two distinct preconditions fail separately, and which one
#: fails decides the next action: install a package, or obtain weights another way.
UTMOS_REASON_NO_RUNTIME = "no_utmos_runtime_installed"
UTMOS_REASON_NO_WEIGHTS = "utmos_runtime_installed_weights_unavailable"
UTMOS_UNAVAILABLE_REASONS = (UTMOS_REASON_NO_RUNTIME, UTMOS_REASON_NO_WEIGHTS)
_ALLOWED_ENVIRONMENT_KEYS = ("LANG", "LC_ALL", "PATH", "TMPDIR")


class AcousticBranchError(ValueError):
    """Raised when the acoustic worker or its reply breaks the contract."""


@dataclass(frozen=True, slots=True)
class AcousticMeasurements:
    """Raw measurements with units. Not scores."""

    backend_id: str
    revision: str
    f0: Mapping[str, Any] | None
    energy: Mapping[str, Any] | None

    def f0_observed(self) -> bool:
        return bool(self.f0 and self.f0.get("state") == "observed")

    def energy_observed(self) -> bool:
        return bool(self.energy and self.energy.get("state") == "observed")


class SubprocessAcousticBackend:
    """Runs the acoustic worker in the pinned acoustics interpreter."""

    def __init__(
        self,
        *,
        repository_root: str | Path,
        interpreter: str | Path | None = None,
        worker_script: str | Path | None = None,
        revision: str = "opensmile-2.6.0+parselmouth-0.4.7",
        backend_id: str = "explicit-acoustics-bridge",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        root = Path(repository_root)
        if not root.is_absolute():
            raise ValueError("repository_root must be absolute")
        self._root = root
        self._interpreter = Path(
            interpreter
            if interpreter is not None
            else root / DEFAULT_INTERPRETER_RELATIVE
        )
        self._worker = Path(
            worker_script if worker_script is not None else root / DEFAULT_WORKER_RELATIVE
        )
        self._revision = revision
        self._backend_id = backend_id
        self._timeout_seconds = timeout_seconds

    @property
    def backend_id(self) -> str:
        return self._backend_id

    @property
    def revision(self) -> str:
        return self._revision

    def _environment(self) -> dict[str, str]:
        return {
            key: os.environ[key]
            for key in _ALLOWED_ENVIRONMENT_KEYS
            if key in os.environ
        }

    def measure(self, audio_path: Path) -> AcousticMeasurements:
        if not self._interpreter.is_file():
            raise AcousticBranchError("the pinned acoustics interpreter is unavailable")
        if not self._worker.is_file():
            raise AcousticBranchError("the acoustic worker script is unavailable")
        request = json.dumps(
            {
                "protocol_version": WORKER_PROTOCOL_VERSION,
                "audio_path": str(audio_path),
                "features": ["f0_parselmouth", "egemaps_functionals"],
            },
            ensure_ascii=False,
        )
        try:
            completed = subprocess.run(
                [str(self._interpreter), str(self._worker)],
                input=request,
                capture_output=True,
                text=True,
                cwd=str(self._root),
                env=self._environment(),
                timeout=self._timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise AcousticBranchError("the acoustic worker timed out") from error
        except OSError as error:
            raise AcousticBranchError(
                "the acoustic worker could not be started"
            ) from error

        try:
            payload = json.loads(completed.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AcousticBranchError(
                "the acoustic worker returned invalid JSON"
            ) from error
        if not isinstance(payload, Mapping):
            raise AcousticBranchError("the acoustic worker returned a non-object")
        if completed.returncode != 0 or payload.get("status") != "ok":
            detail = payload.get("error") or "worker failed"
            raise AcousticBranchError(f"acoustic measurement unavailable: {detail}")
        if payload.get("protocol_version") != WORKER_PROTOCOL_VERSION:
            raise AcousticBranchError("the acoustic worker protocol version differs")
        measurements = payload.get("measurements")
        if not isinstance(measurements, Mapping):
            raise AcousticBranchError("the acoustic worker returned no measurements")
        return AcousticMeasurements(
            backend_id=self._backend_id,
            revision=self._revision,
            f0=measurements.get("f0_parselmouth"),
            energy=measurements.get("egemaps_functionals"),
        )


def build_prosody_branches(
    measurements: AcousticMeasurements,
    *,
    f0_reference_score: float | None = None,
    energy_reference_score: float | None = None,
) -> tuple[S1Branch, ...]:
    """Turn measurements into branches, scoring only where a reference exists.

    ``*_reference_score`` is the ``[0, 1]`` agreement a reference comparison already
    produced — typically ``1 - d(x, R)`` on that axis. Without it the branch reports
    the measurement's presence but carries **no score**, because there is nothing to
    be near or far from yet.
    """

    for name, value in (
        ("f0_reference_score", f0_reference_score),
        ("energy_reference_score", energy_reference_score),
    ):
        if value is not None and not 0.0 <= float(value) <= 1.0:
            raise AcousticBranchError(f"{name} must be within [0, 1]")

    branches: list[S1Branch] = []
    for branch_name, observed, reference in (
        (F0_BRANCH, measurements.f0_observed(), f0_reference_score),
        (ENERGY_BRANCH, measurements.energy_observed(), energy_reference_score),
    ):
        if not observed:
            branches.append(
                S1Branch(
                    name=branch_name,
                    score=None,
                    confidence=None,
                    state=MetricState.MISSING,
                    backend_id=measurements.backend_id,
                    revision=measurements.revision,
                )
            )
            continue
        if reference is None:
            # Measured, but no reference to compare against: a measurement is not
            # a score, and inventing the mapping would invent the reference.
            branches.append(
                S1Branch(
                    name=branch_name,
                    score=None,
                    confidence=None,
                    state=MetricState.MISSING,
                    backend_id=measurements.backend_id,
                    revision=measurements.revision,
                    config_hash="no_reference_bank_measurement_only",
                )
            )
            continue
        branches.append(
            S1Branch(
                name=branch_name,
                score=float(reference),
                confidence=1.0,
                state=MetricState.OBSERVED,
                backend_id=measurements.backend_id,
                revision=measurements.revision,
            )
        )
    return tuple(branches)


def unavailable_naturalness_branch(
    *, reason: str = UTMOS_REASON_NO_WEIGHTS
) -> S1Branch:
    """m7 cannot run here; the gap is declared rather than omitted.

    Omitting the branch would let fusion renormalise as though naturalness had been
    considered. Reporting it as unavailable keeps the absence visible.

    ``reason`` distinguishes the two preconditions, because they have different next
    actions. The runtime is now installed (``.venv-paper-mos``, ``utmosv2`` 1.3.0),
    so the current blocker is weights: both the UTMOSv2 fold checkpoints and the
    ``facebook/wav2vec2-base`` SSL encoder it wraps must be fetched, and large-file
    fetches from the model host return an empty body in this environment. Collapsing
    the two into one message would send someone to reinstall a package that is there.
    """

    if reason not in UTMOS_UNAVAILABLE_REASONS:
        raise AcousticBranchError(f"unknown m7 unavailability reason: {reason}")
    return S1Branch(
        name=NATURALNESS_BRANCH,
        score=None,
        confidence=None,
        state=MetricState.BACKEND_UNAVAILABLE,
        backend_id="utmosv2-absent",
        revision="n/a",
        config_hash=reason,
    )


__all__ = [
    "DEFAULT_INTERPRETER_RELATIVE",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_WORKER_RELATIVE",
    "ENERGY_BRANCH",
    "F0_BRANCH",
    "NATURALNESS_BRANCH",
    "UTMOS_REASON_NO_RUNTIME",
    "UTMOS_REASON_NO_WEIGHTS",
    "UTMOS_UNAVAILABLE_REASONS",
    "WORKER_PROTOCOL_VERSION",
    "AcousticBranchError",
    "AcousticMeasurements",
    "SubprocessAcousticBackend",
    "build_prosody_branches",
    "unavailable_naturalness_branch",
]
