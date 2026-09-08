"""Prompt -> learner audio -> pronunciation feedback pipeline.

The delivered artifact is this in-process pipeline plus its CLI entrypoint
(``scripts/run_pronunciation_feedback.py``).  There is no HTTP service and no
session store: a re-record is another ``run_attempt`` call.

Default wiring status:

* prompt catalog (R00 sentence card) and audio QC are implemented;
* the expected-phone frontend needs the pinned offline g2pK stack;
* content/alignment, S1, and S2 backends are not connected, so a default
  attempt terminates as ``failed_closed`` with the specific missing backend
  rather than producing a substitute score.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .domain import (
    AspectSummary,
    AttemptResult,
    AttemptStatus,
    AudioObservation,
    AudioQcStatus,
    AuditVerdict,
    CtcSoftInformation,
    FailureCode,
    LearnerFeedback,
    MetricState,
    PracticePrompt,
    PromptSpec,
)
from .errors import (
    AudioRejectedError,
    BackendUnavailableError,
    ContractViolationError,
    PromptUnavailableError,
    PronunciationFeedbackError,
)
from .pipeline import PronunciationFeedbackPipeline
from .thresholds import UNCALIBRATED_PROFILE, ThresholdSet

if TYPE_CHECKING:
    from .ports import GroundedAdviceGeneratorPort

__all__ = [
    "UNCALIBRATED_PROFILE",
    "AspectSummary",
    "AttemptResult",
    "AttemptStatus",
    "AudioObservation",
    "AudioQcStatus",
    "AudioRejectedError",
    "AuditVerdict",
    "BackendUnavailableError",
    "ContractViolationError",
    "CtcSoftInformation",
    "FailureCode",
    "LearnerFeedback",
    "MetricState",
    "PracticePrompt",
    "PromptSpec",
    "PromptUnavailableError",
    "PronunciationFeedbackError",
    "PronunciationFeedbackPipeline",
    "ThresholdSet",
    "build_default_pipeline",
]


#: ``src/voice_coach/pronunciation_feedback/__init__.py`` -> repository root.
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
#: The R01 exemplar bank, if a generation run has produced one.
DEFAULT_REFERENCE_BANK = Path("artifacts/reference/r01/multi_speaker_v5/bank.json")
#: The licence the R01 exemplars were generated under.  Serving one means local
#: playback inside a research run, which is what the bank's rights basis
#: permits; it is not redistribution, and the learner is told the audio is
#: synthetic by ``SYNTHETIC_REFERENCE_NOTICE``.
DEFAULT_REFERENCE_LICENSES: tuple[str, ...] = ("AIHub-Terms",)


def build_default_pipeline(
    *,
    card_path: str | Path | None = None,
    threshold_dir: str | Path | None = None,
    analysis_profile: str = UNCALIBRATED_PROFILE,
    nltk_data_root: str | Path | None = None,
    repository_root: str | Path | None = None,
    connect_analysis_backends: bool = True,
    reference_bank_manifest: str | Path | None = None,
    reference_licenses: tuple[str, ...] = DEFAULT_REFERENCE_LICENSES,
    advice_generator: GroundedAdviceGeneratorPort | None = None,
    device: str = "cpu",
) -> PronunciationFeedbackPipeline:
    """Wire the adapters that exist today.

    ``nltk_data_root`` enables the pinned g2pK expected-phone frontend, which
    lives in its own virtualenv and is reached across a subprocess boundary.
    Without it the frontend is the explicit unavailable stand-in, which fails
    closed for any calibrated profile instead of scoring against written
    graphemes.

    ``connect_analysis_backends`` wires the three analysis ports.  The models
    behind them load lazily on first use, so building a pipeline to list prompts
    costs nothing; passing ``False`` is for callers that want the earlier
    fail-closed behaviour on purpose, such as a contract test asserting that an
    unconnected pipeline refuses to score.
    """

    from .adapters.advice import LegacyAdviceCatalog
    from .adapters.audio_intake import SoundfileAudioIntake
    from .adapters.catalog_r00 import DEFAULT_CARD_PATH, R00PromptCatalog
    from .adapters.expected_phones import UnavailableExpectedPhoneFrontend
    from .adapters.expected_phones_subprocess import (
        SubprocessG2PKExpectedPhoneFrontend,
    )
    from .adapters.threshold_store import DEFAULT_THRESHOLD_DIR, JsonThresholdStore

    root = Path(repository_root) if repository_root is not None else REPOSITORY_ROOT
    expected_phones = (
        SubprocessG2PKExpectedPhoneFrontend(
            nltk_data_root=nltk_data_root, repository_root=root
        )
        if nltk_data_root is not None
        else UnavailableExpectedPhoneFrontend()
    )

    content_alignment = None
    s1_scorer = None
    s2_auditor = None
    if connect_analysis_backends:
        from .adapters.ctc_acoustic_backend import CtcAcousticBackend
        from .adapters.ctc_content_alignment import CtcContentAlignment
        from .adapters.gop_s1_scorer import GopS1Scorer
        from .adapters.qwen_audio_auditor import QwenAudioAuditor

        # One acoustic backend serves both S1 stages: the spans a learner sees
        # and the scores that justify them have to come from the same frames.
        acoustic = CtcAcousticBackend(repository_root=root, device=device)
        content_alignment = CtcContentAlignment(acoustic, repository_root=root)
        s1_scorer = GopS1Scorer(
            backend=acoustic,
            content_alignment=content_alignment,
            repository_root=root,
        )
        # A different model family, so the audit can disagree for its own
        # reasons rather than reproducing S1's mistakes under another name.
        s2_auditor = QwenAudioAuditor(repository_root=root, device=device)

    manifest = Path(reference_bank_manifest or (root / DEFAULT_REFERENCE_BANK))
    reference_bank = None
    if manifest.is_file():
        from .adapters.reference_bank_store import JsonReferenceBankStore

        reference_bank = JsonReferenceBankStore(
            manifest_path=manifest, allowed_licenses=reference_licenses
        )

    return PronunciationFeedbackPipeline(
        prompt_catalog=R00PromptCatalog(card_path or DEFAULT_CARD_PATH),
        audio_intake=SoundfileAudioIntake(),
        threshold_store=JsonThresholdStore(threshold_dir or DEFAULT_THRESHOLD_DIR),
        advice_catalog=LegacyAdviceCatalog(),
        expected_phones=expected_phones,
        content_alignment=content_alignment,
        s1_scorer=s1_scorer,
        s2_auditor=s2_auditor,
        reference_bank=reference_bank,
        advice_generator=advice_generator,
        analysis_profile=analysis_profile,
    )
