"""m7 naturalness as an S1 branch, and the population it is allowed to speak about.

Two things separate m7 from the other explicit branches.

It can score without a reference bank. A predicted MOS is already an opinion score on
a defined 1–5 scale, so ``(mos - 1) / 4`` is an affine map of the scale's own
endpoints — not the invented hertz-to-score curve that m4/m5 refuse to pick.

It is trained on synthesised and converted speech (BVCC/SOMOS), and the paper uses it
to gate *generated reference audio*. Running it on L2 learner recordings is out of
distribution, and this module reports that as
:attr:`MetricState.OUT_OF_DISTRIBUTION` rather than as a number a learner could be
shown. A low MOS for a learner would say "this recording sounds synthetic", which is
not what a pronunciation score means.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping

from ..domain import MetricState, S1Branch

WORKER_PROTOCOL_VERSION = "voice_coach.naturalness_mos_worker.v1"
DEFAULT_WORKER_RELATIVE = Path("scripts/naturalness_mos_worker.py")
DEFAULT_INTERPRETER_RELATIVE = Path(".venv-paper-mos/bin/python")
#: One 2-second clip takes about 6 s per fold on CPU, so five folds need headroom.
DEFAULT_TIMEOUT_SECONDS = 900.0
NATURALNESS_BRANCH = "naturalness"
MOS_MINIMUM = 1.0
MOS_MAXIMUM = 5.0
#: What the recording is. UTMOSv2's training population is synthesised speech.
GENERATED_AUDIO = "generated_reference_audio"
LEARNER_AUDIO = "learner_recording"
AUDIO_POPULATIONS = (GENERATED_AUDIO, LEARNER_AUDIO)
_ALLOWED_ENVIRONMENT_KEYS = ("LANG", "LC_ALL", "PATH", "TMPDIR")


class NaturalnessBranchError(ValueError):
    """Raised when the MOS worker or its reply breaks the contract."""


@dataclass(frozen=True, slots=True)
class NaturalnessMeasurement:
    """A predicted MOS with the folds that produced it."""

    backend_id: str
    revision: str
    payload: Mapping[str, Any] | None

    def observed(self) -> bool:
        return bool(self.payload and self.payload.get("state") == "observed")

    @property
    def mos_mean(self) -> float | None:
        if not self.observed():
            return None
        assert self.payload is not None
        return float(self.payload["mos_mean"])

    @property
    def folds(self) -> tuple[int, ...]:
        if not self.payload:
            return ()
        return tuple(int(fold) for fold in self.payload.get("folds", ()))


class SubprocessNaturalnessBackend:
    """Runs the MOS worker in the pinned `.venv-paper-mos` interpreter."""

    def __init__(
        self,
        *,
        repository_root: str | Path,
        interpreter: str | Path | None = None,
        worker_script: str | Path | None = None,
        folds: tuple[int, ...] = (0,),
        device: str = "cpu",
        revision: str = "utmosv2-1.3.0+fusion_stage3",
        backend_id: str = "utmosv2-mos-bridge",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        root = Path(repository_root)
        if not root.is_absolute():
            raise ValueError("repository_root must be absolute")
        if not folds:
            raise NaturalnessBranchError("at least one fold must be requested")
        if device not in ("cpu", "cuda"):
            raise NaturalnessBranchError("device must be cpu or cuda")
        self._root = root
        self._interpreter = Path(
            interpreter
            if interpreter is not None
            else root / DEFAULT_INTERPRETER_RELATIVE
        )
        self._worker = Path(
            worker_script
            if worker_script is not None
            else root / DEFAULT_WORKER_RELATIVE
        )
        self._folds = tuple(sorted(set(folds)))
        self._device = device
        self._revision = revision
        self._backend_id = backend_id
        self._timeout_seconds = timeout_seconds

    @property
    def backend_id(self) -> str:
        return self._backend_id

    @property
    def revision(self) -> str:
        # The fold set changes the number, so it belongs in the recorded revision.
        folds = "-".join(str(fold) for fold in self._folds)
        return f"{self._revision}+folds{folds}"

    def _environment(self) -> dict[str, str]:
        return {
            key: os.environ[key]
            for key in _ALLOWED_ENVIRONMENT_KEYS
            if key in os.environ
        }

    def measure(self, audio_path: Path) -> NaturalnessMeasurement:
        if not self._interpreter.is_file():
            raise NaturalnessBranchError("the pinned MOS interpreter is unavailable")
        if not self._worker.is_file():
            raise NaturalnessBranchError("the MOS worker script is unavailable")
        request = json.dumps(
            {
                "protocol_version": WORKER_PROTOCOL_VERSION,
                "audio_path": str(audio_path),
                "folds": list(self._folds),
                "device": self._device,
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
            raise NaturalnessBranchError("the MOS worker timed out") from error
        except OSError as error:
            raise NaturalnessBranchError(
                "the MOS worker could not be started"
            ) from error

        try:
            payload = json.loads(completed.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise NaturalnessBranchError(
                "the MOS worker returned invalid JSON"
            ) from error
        if not isinstance(payload, Mapping):
            raise NaturalnessBranchError("the MOS worker returned a non-object")
        if completed.returncode != 0 or payload.get("status") != "ok":
            detail = payload.get("error") or "worker failed"
            raise NaturalnessBranchError(f"naturalness unavailable: {detail}")
        if payload.get("protocol_version") != WORKER_PROTOCOL_VERSION:
            raise NaturalnessBranchError("the MOS worker protocol version differs")
        measurements = payload.get("measurements")
        if not isinstance(measurements, Mapping):
            raise NaturalnessBranchError("the MOS worker returned no measurements")
        return NaturalnessMeasurement(
            backend_id=self._backend_id,
            revision=self.revision,
            payload=measurements.get("naturalness_utmosv2"),
        )


def mos_to_unit_score(mos: float) -> float:
    """Map a 1–5 MOS onto ``[0, 1]`` using the scale's own endpoints."""

    if not MOS_MINIMUM <= mos <= MOS_MAXIMUM:
        raise NaturalnessBranchError("mos must be within the 1-5 scale")
    return (mos - MOS_MINIMUM) / (MOS_MAXIMUM - MOS_MINIMUM)


def build_naturalness_branch(
    measurement: NaturalnessMeasurement,
    *,
    audio_population: str = GENERATED_AUDIO,
) -> S1Branch:
    """Build m7, refusing to score a population the model was not trained on.

    ``audio_population`` is required to be stated rather than inferred. UTMOSv2 was
    trained on synthesised and converted speech; on a learner recording its number
    answers "does this sound synthetic", which is not a pronunciation judgement. That
    case is reported as ``OUT_OF_DISTRIBUTION`` with the measurement kept in the
    provenance, so a later calibration study can still use it.
    """

    if audio_population not in AUDIO_POPULATIONS:
        raise NaturalnessBranchError(f"unknown audio population: {audio_population}")
    if not measurement.observed():
        reason = "no_prediction"
        if measurement.payload:
            reason = str(measurement.payload.get("reason", reason))
        return S1Branch(
            name=NATURALNESS_BRANCH,
            score=None,
            confidence=None,
            state=MetricState.MISSING,
            backend_id=measurement.backend_id,
            revision=measurement.revision,
            config_hash=reason,
        )
    mos = measurement.mos_mean
    assert mos is not None
    if audio_population == LEARNER_AUDIO:
        return S1Branch(
            name=NATURALNESS_BRANCH,
            score=None,
            confidence=None,
            state=MetricState.OUT_OF_DISTRIBUTION,
            backend_id=measurement.backend_id,
            revision=measurement.revision,
            config_hash="utmos_trained_on_synthesised_speech_only",
        )
    return S1Branch(
        name=NATURALNESS_BRANCH,
        score=mos_to_unit_score(mos),
        confidence=1.0,
        state=MetricState.OBSERVED,
        backend_id=measurement.backend_id,
        revision=measurement.revision,
    )


__all__ = [
    "AUDIO_POPULATIONS",
    "DEFAULT_INTERPRETER_RELATIVE",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_WORKER_RELATIVE",
    "GENERATED_AUDIO",
    "LEARNER_AUDIO",
    "MOS_MAXIMUM",
    "MOS_MINIMUM",
    "NATURALNESS_BRANCH",
    "WORKER_PROTOCOL_VERSION",
    "NaturalnessBranchError",
    "NaturalnessMeasurement",
    "SubprocessNaturalnessBackend",
    "build_naturalness_branch",
    "mos_to_unit_score",
]
