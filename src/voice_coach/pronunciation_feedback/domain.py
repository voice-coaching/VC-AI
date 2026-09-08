"""Pure contracts for the prompt -> audio -> feedback pipeline.

This module holds state, policy vocabulary, and result shapes only.  It must
not import filesystem, audio, model runtime, or network APIs so that the
contracts stay testable without any backend installed.

A missing measurement is never expressed as ``0.0``.  Every score carries a
:class:`MetricState` so that ``missing``, ``low_confidence``,
``out_of_distribution``, and ``backend_unavailable`` remain distinguishable
from an observed low score.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import math


class AttemptStatus(StrEnum):
    """Terminal states of a single ``run_attempt`` call."""

    COACHING_READY = "coaching_ready"
    COMPLETED_NO_ISSUE = "completed_no_issue"
    RERECORD_REQUIRED = "rerecord_required"
    UNCERTAIN = "uncertain"
    FAILED_CLOSED = "failed_closed"


class FailureCode(StrEnum):
    """Public reason codes.  ``None`` is reserved for successful attempts."""

    PROMPT_NOT_AVAILABLE = "prompt_not_available"
    PROMPT_CATALOG_UNAVAILABLE = "prompt_catalog_unavailable"
    PROMPT_INVALID = "prompt_invalid"
    EXPECTED_PHONES_UNAVAILABLE = "expected_phones_unavailable"
    AUDIO_INVALID = "audio_invalid"
    AUDIO_QC_FAILED = "audio_qc_failed"
    THRESHOLDS_UNAVAILABLE = "thresholds_unavailable"
    CONTENT_BACKEND_UNAVAILABLE = "content_backend_unavailable"
    PROMPT_MISMATCH = "prompt_mismatch"
    ALIGNMENT_LOW_CONFIDENCE = "alignment_low_confidence"
    S1_BACKEND_UNAVAILABLE = "s1_backend_unavailable"
    S1_REQUIRED_METRIC_MISSING = "s1_required_metric_missing"
    S2_BACKEND_UNAVAILABLE = "s2_backend_unavailable"
    S2_NOT_RUN = "s2_not_run"
    S1_S2_DISAGREEMENT = "s1_s2_disagreement"
    S1_S2_NOT_INDEPENDENT = "s1_s2_not_independent"
    ANALYSIS_LOW_CONFIDENCE = "analysis_low_confidence"
    ADVICE_NOT_ALLOWED = "advice_not_allowed"
    UNCALIBRATED_PROFILE = "uncalibrated_profile"
    COHORT_NOT_SUPPORTED = "cohort_not_supported"
    TARGET_LANGUAGE_NOT_SUPPORTED = "target_language_not_supported"


class TargetLanguage(StrEnum):
    """Instruction target language, distinct from a learner's first language."""

    KOREAN = "ko"
    ENGLISH = "en"


class MetricState(StrEnum):
    """Why a score is or is not usable.  Never collapse these into a number."""

    OBSERVED = "observed"
    MISSING = "missing"
    LOW_CONFIDENCE = "low_confidence"
    OUT_OF_DISTRIBUTION = "out_of_distribution"
    BACKEND_UNAVAILABLE = "backend_unavailable"


class AuditVerdict(StrEnum):
    """Independent S2 auditor verdicts."""

    APPROVED = "approved"
    DISAGREEMENT = "disagreement"
    UNCERTAIN = "uncertain"
    NOT_RUN = "not_run"


class AudioQcStatus(StrEnum):
    QC_PASSED = "qc_passed"
    QC_FAILED = "qc_failed"
    DECODE_FAILED = "decode_failed"


@dataclass(frozen=True, slots=True)
class PracticePrompt:
    """One practice sentence as presented to the learner.

    Carries no pronunciation judgement and no expected-phone contract; those
    are derived server-side into :class:`PromptSpec` from the same
    ``prompt_id``.
    """

    prompt_id: str
    display_text: str
    normalized_text: str
    normalized_text_sha256: str
    difficulty: str
    syllable_count: int
    word_count: int
    practice_targets: tuple[str, ...]
    license_spdx: str
    catalog_revision: str
    target_language: TargetLanguage


@dataclass(frozen=True, slots=True)
class SyllableSlot:
    """Grapheme-level anchor for learner-facing highlighting.

    ``onset``/``nucleus``/``coda`` are compatibility jamo of the *written*
    syllable.  They are not expected phones: no phonological rule has been
    applied here.
    """

    index: int
    char_start: int
    char_end: int
    syllable: str
    onset: str
    nucleus: str
    coda: str | None


@dataclass(frozen=True, slots=True)
class ExpectedPhoneSequence:
    """Expected (rule-applied) phone stream for the prompt."""

    tokens: tuple[str, ...]
    token_sha256: str
    backend_id: str
    revision: str
    per_syllable_token_counts: tuple[int, ...] | None = None


@dataclass(frozen=True, slots=True)
class PromptSpec:
    """Prompt fixed for one attempt, with the mappings feedback needs."""

    prompt_id: str
    prompt_version: str
    target_language: TargetLanguage
    normalized_text: str
    normalized_text_sha256: str
    syllables: tuple[SyllableSlot, ...]
    syllable_map_sha256: str
    expected_phones: ExpectedPhoneSequence | None
    expected_phone_state: MetricState

    def syllable(self, index: int) -> SyllableSlot | None:
        for slot in self.syllables:
            if slot.index == index:
                return slot
        return None


@dataclass(frozen=True, slots=True)
class AudioObservation:
    """Decode/QC outcome.  Holds identity and shape, never a public path."""

    status: AudioQcStatus
    audio_sha256: str | None
    duration_seconds: float | None
    sample_rate: int | None
    source_sample_rate: int | None
    source_channels: int | None
    decoder_id: str
    decoder_revision: str
    qc_failures: tuple[str, ...] = ()
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ReferenceExemplar:
    """One reference rendering of a prompt, from Loop A's candidate bank.

    A bank holds *several* exemplars on purpose.  Treating a single rendering as
    the answer would force one speaking style on the learner; the phonetically
    acceptable range is represented by the spread of the set (paper P6).

    ``license_spdx`` and ``rights_basis`` are required rather than optional: an
    exemplar whose redistribution basis is unknown cannot be played to a learner,
    so a bank that cannot state them yields no exemplars at all.
    """

    exemplar_id: str
    prompt_id: str
    generator_id: str
    generator_revision: str
    audio_sha256: str
    duration_seconds: float
    sample_rate: int
    speaker_axis: str
    speed_axis: str
    license_spdx: str
    rights_basis: str
    synthetic: bool
    #: Learner-facing disclosure. Required for synthetic speech.
    disclosure: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "exemplar_id",
            "prompt_id",
            "generator_id",
            "generator_revision",
            "audio_sha256",
            "speaker_axis",
            "speed_axis",
            "license_spdx",
            "rights_basis",
        ):
            if not getattr(self, name):
                raise ValueError(f"{name} must be a non-empty string")
        if self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if self.synthetic and not self.disclosure:
            raise ValueError("synthetic exemplars must carry a disclosure notice")


@dataclass(frozen=True, slots=True)
class ReferenceBank:
    """The exemplar set offered for one prompt, with its selection provenance."""

    prompt_id: str
    exemplars: tuple[ReferenceExemplar, ...]
    selection_id: str
    selection_rule: str
    diversity_axes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.prompt_id:
            raise ValueError("prompt_id must not be empty")
        if any(item.prompt_id != self.prompt_id for item in self.exemplars):
            raise ValueError("every exemplar must belong to this prompt")
        if len({item.exemplar_id for item in self.exemplars}) != len(self.exemplars):
            raise ValueError("exemplar ids must be unique")
        if self.exemplars and not self.selection_id:
            raise ValueError("a non-empty bank must name its selection")

    @property
    def is_empty(self) -> bool:
        return not self.exemplars


@dataclass(frozen=True, slots=True)
class AxisDistance:
    """Distance on one prosodic axis to the nearest exemplar on that axis."""

    axis: str
    distance: float | None
    state: MetricState
    nearest_exemplar_id: str | None = None


@dataclass(frozen=True, slots=True)
class MultiReferenceDistance:
    """``d(x, R) = min over exemplars`` and the exemplar that attained it.

    The minimum, not the mean: the learner is compared against the *closest*
    acceptable rendering, so a bank that spans several styles widens tolerance
    instead of penalising anyone who does not match one chosen voice.
    """

    minimum_distance: float | None
    nearest_exemplar_id: str | None
    state: MetricState
    exemplar_count: int
    axes: tuple[AxisDistance, ...] = ()

    def __post_init__(self) -> None:
        if self.exemplar_count < 0:
            raise ValueError("exemplar_count must not be negative")
        if self.state is MetricState.OBSERVED:
            if self.minimum_distance is None or self.nearest_exemplar_id is None:
                raise ValueError("an observed distance needs a value and an exemplar")
            if self.exemplar_count <= 0:
                raise ValueError("an observed distance needs at least one exemplar")
        elif self.minimum_distance is not None:
            # Never carry a number under a non-observed state: a consumer would
            # read it as a measurement.
            raise ValueError("a non-observed distance must not carry a value")


@dataclass(frozen=True, slots=True)
class SyllableSpan:
    """Time range attributed to one prompt syllable by an alignment backend."""

    syllable_index: int
    start_ms: int
    end_ms: int
    confidence: float
    state: MetricState = MetricState.OBSERVED


@dataclass(frozen=True, slots=True)
class AlignmentObservation:
    """Content agreement and per-syllable alignment for one attempt.

    Owned by the S1 side: ``backend_id`` here must differ from the S2 auditor
    backend, otherwise the independence receipt fails.
    """

    backend_id: str
    revision: str
    content_score: float | None
    content_state: MetricState
    alignment_confidence: float | None
    syllable_spans: tuple[SyllableSpan, ...] = ()


@dataclass(frozen=True, slots=True)
class S1Branch:
    """One S1 scoring axis with its own state and provenance."""

    name: str
    score: float | None
    confidence: float | None
    state: MetricState
    backend_id: str
    revision: str
    config_hash: str | None = None


@dataclass(frozen=True, slots=True)
class CtcSoftInformation:
    """Soft XLS-R/CTC evidence for one aligned prompt syllable.

    These values summarize the complete CTC posterior distribution on the
    token anchor frames. They are model evidence, not a calibrated probability
    that the learner pronounced the syllable correctly.
    """

    syllable_index: int
    target_syllable: str
    target_log_posterior: float
    gop_margin: float
    posterior_entropy: float
    target_is_top1: bool
    frame_count: int
    backend_id: str
    revision: str

    def __post_init__(self) -> None:
        if self.syllable_index < 0:
            raise ValueError("syllable_index must not be negative")
        if not self.target_syllable:
            raise ValueError("target_syllable must not be empty")
        if self.frame_count <= 0:
            raise ValueError("frame_count must be positive")
        if not self.backend_id or not self.revision:
            raise ValueError("soft information must name its backend and revision")
        values = (
            self.target_log_posterior,
            self.gop_margin,
            self.posterior_entropy,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("soft information values must be finite")
        if self.posterior_entropy < 0.0:
            raise ValueError("posterior_entropy must not be negative")


@dataclass(frozen=True, slots=True)
class S1ScoreBundle:
    """S1 output: per-axis scores plus the issue candidates they support.

    ``issues`` are candidates only.  They carry evidence tags and locations but
    no coaching authority; the gate decides whether any of them may be shown.
    """

    branches: tuple[S1Branch, ...]
    overall_confidence: float | None
    confidence_state: MetricState
    issues: tuple["DiagnosticIssueDraft", ...] = ()
    soft_information: tuple[CtcSoftInformation, ...] = ()

    def branch(self, name: str) -> S1Branch | None:
        for item in self.branches:
            if item.name == name:
                return item
        return None

    def observed_names(self) -> tuple[str, ...]:
        return tuple(
            item.name for item in self.branches if item.state is MetricState.OBSERVED
        )

    def backend_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.backend_id for item in self.branches))


@dataclass(frozen=True, slots=True)
class IndependenceReceipt:
    """Evidence that S2 did not reuse an S1 backend."""

    s1_backend_ids: tuple[str, ...]
    s2_backend_id: str
    independent: bool
    reason: str


@dataclass(frozen=True, slots=True)
class S2AuditResult:
    verdict: AuditVerdict
    backend_id: str
    revision: str
    content_score: float | None
    confidence: float | None
    independence: IndependenceReceipt | None


@dataclass(frozen=True, slots=True)
class DiagnosticIssueDraft:
    """Candidate issue before gate and advice allowlist checks."""

    issue_type: str
    syllable_index: int | None
    start_ms: int | None
    end_ms: int | None
    severity: float
    confidence: float
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FeedbackSpan:
    """Learner-facing location.  Only issued when all three anchors exist."""

    syllable_index: int
    char_start: int
    char_end: int
    start_ms: int
    end_ms: int
    syllable: str


@dataclass(frozen=True, slots=True)
class AdviceItem:
    issue_type: str
    advice_id: str
    message: str
    span: FeedbackSpan | None
    severity: float
    confidence: float
    soft_information: CtcSoftInformation | None = None


@dataclass(frozen=True, slots=True)
class AspectSummary:
    """One reported axis.  ``score is None`` means not measured, not zero."""

    name: str
    score: float | None
    state: MetricState


@dataclass(frozen=True, slots=True)
class LearnerFeedback:
    prompt_id: str
    display_text: str
    next_action: str
    advice: tuple[AdviceItem, ...]
    aspects: tuple[AspectSummary, ...]
    unlocated_issue_types: tuple[str, ...]
    notices: tuple[str, ...]
    #: Loop A exemplars offered for listening. Empty when no bank could state a
    #: rights basis for every exemplar — the gap is shown as a notice, never
    #: filled with an exemplar of unknown provenance.
    reference_audio: tuple[ReferenceExemplar, ...] = ()
    #: ``d(x, R)`` against those exemplars, or a non-observed state.
    reference_distance: MultiReferenceDistance | None = None


@dataclass(frozen=True, slots=True)
class StageReceipt:
    """Per-stage provenance kept out of the learner-facing payload."""

    stage: str
    backend_id: str | None
    revision: str | None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class AttemptResult:
    status: AttemptStatus
    failure_code: FailureCode | None
    reason: str
    prompt_id: str | None
    prompt_version: str | None
    analysis_profile: str
    audio_sha256: str | None
    learner_feedback: LearnerFeedback | None
    receipts: tuple[StageReceipt, ...] = field(default_factory=tuple)
    #: Track E vs Track R.  Defaults to the lower claim so that a result can
    #: never over-claim by omission.
    claim_scope: str = "engineering_provisional"

    @property
    def is_terminal_success(self) -> bool:
        return self.status in {
            AttemptStatus.COACHING_READY,
            AttemptStatus.COMPLETED_NO_ISSUE,
        }
