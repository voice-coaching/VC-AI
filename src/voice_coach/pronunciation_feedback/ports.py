"""Ports the pipeline depends on.

Each port is a narrow, intent-named protocol.  Implementations own their own
runtime concerns (file access, model residency, GPU).  A port that cannot serve
a request raises :class:`~voice_coach.pronunciation_feedback.errors.BackendUnavailableError`
rather than returning a placeholder value.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from .coaching_payload import CoachingPayload, GeneratedAdvice
from .domain import (
    AlignmentObservation,
    AudioObservation,
    ExpectedPhoneSequence,
    MultiReferenceDistance,
    PracticePrompt,
    PromptSpec,
    ReferenceBank,
    S1ScoreBundle,
    S2AuditResult,
)
from .grounded_advice import GroundedAdvicePlan, GroundedCoachingPayload
from .learner_model import LearnerState
from .reward import RewardSignal
from .thresholds import ThresholdSet


@runtime_checkable
class PromptCatalogPort(Protocol):
    """Owns which sentences may be practised and how they are displayed."""

    @property
    def revision(self) -> str: ...

    def list_prompts(
        self,
        *,
        difficulty: str | None = None,
        target: str | None = None,
        limit: int | None = None,
    ) -> tuple[PracticePrompt, ...]: ...

    def get_prompt(self, prompt_id: str) -> PracticePrompt: ...


@runtime_checkable
class ExpectedPhonePort(Protocol):
    """Rule-applied expected phone stream for a normalized prompt text."""

    def expected_phones(self, normalized_text: str) -> ExpectedPhoneSequence: ...


@runtime_checkable
class AudioIntakePort(Protocol):
    """Decode and quality-control one learner recording."""

    def observe(self, audio_path: Path) -> AudioObservation: ...


@runtime_checkable
class ContentAlignmentPort(Protocol):
    """S1-side content agreement and per-syllable alignment."""

    def analyze(
        self,
        *,
        audio_path: Path,
        prompt_spec: PromptSpec,
    ) -> AlignmentObservation: ...


@runtime_checkable
class ReferenceBankPort(Protocol):
    """Loop A: the exemplar set a learner may listen to for one prompt.

    Returning an empty bank is a valid, expected answer — it is how the pipeline
    represents "no exemplar has a stateable rights basis" without inventing one.
    """

    @property
    def bank_id(self) -> str: ...

    def bank_for(
        self,
        *,
        prompt_id: str,
        speed_axis: str | None = None,
        speaker_axis: str | None = None,
    ) -> ReferenceBank: ...


@runtime_checkable
class ProsodyDistancePort(Protocol):
    """``d(x, R) = min_r d(x, r)`` between a learner take and an exemplar set."""

    @property
    def backend_id(self) -> str: ...

    def distance_to_bank(
        self,
        *,
        audio_path: Path,
        prompt_spec: PromptSpec,
        bank: ReferenceBank,
    ) -> MultiReferenceDistance: ...


@runtime_checkable
class S1ScorerPort(Protocol):
    """Hybrid S1 scoring across explicit and learned branches."""

    def score(
        self,
        *,
        audio_path: Path,
        prompt_spec: PromptSpec,
        alignment: AlignmentObservation,
    ) -> S1ScoreBundle: ...


@runtime_checkable
class S2AuditorPort(Protocol):
    """Independent auditor.  Must not reuse an S1 backend id."""

    def audit(
        self,
        *,
        audio_path: Path,
        prompt_spec: PromptSpec,
        s1_backend_ids: tuple[str, ...],
    ) -> S2AuditResult: ...


@runtime_checkable
class RewardModelPort(Protocol):
    """Scores a *generated candidate*, not a learner take (Loop A).

    Deliberately separate from :class:`S2AuditorPort` even when one model serves
    both: the audit judges a learner's speech, the reward judges a synthesised
    reference, and conflating them lets the generator drift toward the auditor's
    blind spots. The returned signal carries its own independence receipt.
    """

    @property
    def backend_id(self) -> str: ...

    @property
    def revision(self) -> str: ...

    def reward_for(
        self,
        *,
        candidate_audio: Path,
        target_text: str,
        s1_backend_ids: tuple[str, ...],
    ) -> RewardSignal: ...


@runtime_checkable
class ThresholdSetPort(Protocol):
    """Frozen decision thresholds with their calibration provenance."""

    def load(self, analysis_profile: str) -> ThresholdSet: ...


@runtime_checkable
class LearnerModelPort(Protocol):
    """Loop C: the learner history that turns ``Trainability`` into a measurement.

    Identifiers are already hashed by the caller; this port never sees a raw one.
    ``retries_remaining`` exists so "record again" cannot loop forever.
    """

    @property
    def model_id(self) -> str: ...

    def state_for(self, learner_id_sha256: str) -> LearnerState: ...

    def retries_remaining(self, learner_id_sha256: str, prompt_id: str) -> int: ...


@runtime_checkable
class AdviceGeneratorPort(Protocol):
    """Turns a decided diagnosis into learner-facing wording.

    Sees only :class:`CoachingPayload` — no audio, no path — so it cannot score
    speech, and returns no span or score, so it cannot move a highlight or change
    a number. Its output must still pass ``validate_generated_advice``.
    """

    @property
    def generator_id(self) -> str: ...

    @property
    def revision(self) -> str: ...

    def generate(self, payload: CoachingPayload) -> tuple[GeneratedAdvice, ...]: ...


@runtime_checkable
class GroundedAdviceGeneratorPort(Protocol):
    """Selects a validated plan without generating learner-facing facts.

    Unlike the legacy free-prose generator contract, this port receives only
    an approved claim set and returns claim identifiers plus presentation
    switches.  The pipeline validates the plan and renders the final message
    locally, so a model cannot add an issue, phone, score, location, or
    articulatory instruction.
    """

    @property
    def generator_id(self) -> str: ...

    @property
    def revision(self) -> str: ...

    def generate(self, payload: GroundedCoachingPayload) -> GroundedAdvicePlan: ...


@runtime_checkable
class AdviceCatalogPort(Protocol):
    """Allowlist mapping an issue type to an approved advice message."""

    @property
    def catalog_id(self) -> str: ...

    def lookup(self, issue_type: str) -> tuple[str, str] | None: ...
