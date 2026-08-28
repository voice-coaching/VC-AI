"""Stage C--H contracts driven by injected backends.

The real S1/S2 backends are not connected, so these doubles stand in for them.
They exist to pin the gate and composition rules, not to claim any model
quality: every score here is fixture data.
"""

from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from voice_coach.pronunciation_feedback import (
    UNCALIBRATED_PROFILE,
    AttemptStatus,
    FailureCode,
    PronunciationFeedbackPipeline,
    ThresholdSet,
)
from voice_coach.pronunciation_feedback.adapters.advice import LegacyAdviceCatalog
from voice_coach.pronunciation_feedback.adapters.audio_intake import (
    SoundfileAudioIntake,
)
from voice_coach.pronunciation_feedback.adapters.catalog_r00 import (
    DEFAULT_CARD_PATH,
    R00PromptCatalog,
)
from voice_coach.pronunciation_feedback.adapters.expected_phones import (
    UnavailableExpectedPhoneFrontend,
)
from voice_coach.pronunciation_feedback.domain import (
    AlignmentObservation,
    AuditVerdict,
    CtcSoftInformation,
    DiagnosticIssueDraft,
    ExpectedPhoneSequence,
    IndependenceReceipt,
    MetricState,
    MultiReferenceDistance,
    PromptSpec,
    ReferenceBank,
    ReferenceExemplar,
    S1Branch,
    S1ScoreBundle,
    S2AuditResult,
    SyllableSpan,
)
from voice_coach.pronunciation_feedback.errors import (
    BackendUnavailableError,
    PromptUnavailableError,
)
from voice_coach.pronunciation_feedback.grounded_advice import (
    GROUNDED_OUTPUT_SCHEMA_VERSION,
    CandidateDisclosure,
    GroundedAdvicePlan,
    GroundedAdviceStyle,
    GroundedPlanStatus,
)

CARD_PATH = Path(DEFAULT_CARD_PATH)
PROMPT_ID = "R00-01"
CALIBRATED_PROFILE = "test_calibrated"
S1_BRANCHES = ("content", "segmental", "duration")

pytestmark = pytest.mark.skipif(
    not CARD_PATH.is_file(), reason="R00 card asset is absent"
)


# ------------------------------------------------------------------ doubles


class FixedExpectedPhones:
    def expected_phones(self, normalized_text: str) -> ExpectedPhoneSequence:
        return ExpectedPhoneSequence(
            tokens=("ㅆ", "ㅏ", "ㄹ"),
            token_sha256="f" * 64,
            backend_id="fixture-expected-phones",
            revision="fixture-1",
        )


class FixedAlignment:
    backend_id = "fixture-s1-ctc"

    def __init__(
        self,
        *,
        content_score: float = 0.97,
        content_state: MetricState = MetricState.OBSERVED,
        alignment_confidence: float | None = 0.9,
        syllable_index: int = 0,
    ) -> None:
        self._content_score = content_score
        self._content_state = content_state
        self._alignment_confidence = alignment_confidence
        self._syllable_index = syllable_index

    def analyze(
        self, *, audio_path: Path, prompt_spec: PromptSpec
    ) -> AlignmentObservation:
        return AlignmentObservation(
            backend_id=self.backend_id,
            revision="fixture-1",
            content_score=self._content_score,
            content_state=self._content_state,
            alignment_confidence=self._alignment_confidence,
            syllable_spans=(
                SyllableSpan(
                    syllable_index=self._syllable_index,
                    start_ms=120,
                    end_ms=320,
                    confidence=0.9,
                ),
            ),
        )


class FixedS1Scorer:
    backend_id = "fixture-s1-wavlm"

    def __init__(
        self,
        *,
        issues: tuple[DiagnosticIssueDraft, ...] = (),
        observed: tuple[str, ...] = S1_BRANCHES,
        overall_confidence: float | None = 0.88,
        confidence_state: MetricState = MetricState.OBSERVED,
        soft_information: tuple[CtcSoftInformation, ...] = (),
    ) -> None:
        self._issues = issues
        self._observed = observed
        self._overall_confidence = overall_confidence
        self._confidence_state = confidence_state
        self._soft_information = soft_information

    def score(
        self,
        *,
        audio_path: Path,
        prompt_spec: PromptSpec,
        alignment: AlignmentObservation,
    ) -> S1ScoreBundle:
        branches = tuple(
            S1Branch(
                name=name,
                score=0.7 if name in self._observed else None,
                confidence=0.8 if name in self._observed else None,
                state=(
                    MetricState.OBSERVED
                    if name in self._observed
                    else MetricState.BACKEND_UNAVAILABLE
                ),
                backend_id=self.backend_id,
                revision="fixture-1",
            )
            for name in S1_BRANCHES
        )
        return S1ScoreBundle(
            branches=branches,
            overall_confidence=self._overall_confidence,
            confidence_state=self._confidence_state,
            issues=self._issues,
            soft_information=self._soft_information,
        )


class FixedS2Auditor:
    def __init__(
        self,
        *,
        verdict: AuditVerdict = AuditVerdict.APPROVED,
        confidence: float | None = 0.9,
        backend_id: str = "fixture-s2-whisper",
    ) -> None:
        self._verdict = verdict
        self._confidence = confidence
        self._backend_id = backend_id

    def audit(
        self,
        *,
        audio_path: Path,
        prompt_spec: PromptSpec,
        s1_backend_ids: tuple[str, ...],
    ) -> S2AuditResult:
        independent = self._backend_id not in s1_backend_ids
        return S2AuditResult(
            verdict=self._verdict,
            backend_id=self._backend_id,
            revision="fixture-1",
            content_score=0.95,
            confidence=self._confidence,
            independence=IndependenceReceipt(
                s1_backend_ids=s1_backend_ids,
                s2_backend_id=self._backend_id,
                independent=independent,
                reason=(
                    "distinct backend id"
                    if independent
                    else "s2 reuses an s1 backend id"
                ),
            ),
        )


class FixedThresholdStore:
    def __init__(self, threshold_set: ThresholdSet) -> None:
        self._threshold_set = threshold_set

    def load(self, analysis_profile: str) -> ThresholdSet:
        if analysis_profile != self._threshold_set.analysis_profile:
            raise BackendUnavailableError(
                FailureCode.THRESHOLDS_UNAVAILABLE,
                f"no artifact for {analysis_profile!r}",
            )
        return self._threshold_set


def _calibrated() -> ThresholdSet:
    return ThresholdSet(
        threshold_set_id="fixture-thresholds-v1",
        analysis_profile=CALIBRATED_PROFILE,
        calibrated=True,
        cohort="fixture-cohort",
        frozen_at="2026-08-06",
        dataset_id="fixture-dataset",
        manifest_sha256="a" * 64,
        min_content_score=0.8,
        min_alignment_confidence=0.6,
        min_s1_confidence=0.7,
        min_s2_confidence=0.7,
        min_issue_confidence=0.6,
        min_issue_severity=0.3,
        required_branches=S1_BRANCHES,
    )


def _coachable_issue(**overrides: object) -> DiagnosticIssueDraft:
    base = DiagnosticIssueDraft(
        issue_type="final_consonant_deletion",
        syllable_index=0,
        start_ms=120,
        end_ms=320,
        severity=0.7,
        confidence=0.85,
        evidence=("gop_low",),
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


@pytest.fixture
def recording(tmp_path: Path) -> Path:
    sample_rate = 16_000
    time = np.arange(sample_rate, dtype=np.float64) / sample_rate
    samples = (0.2 * np.sin(2 * np.pi * 200.0 * time)).astype(np.float32)
    path = tmp_path / "attempt.wav"
    sf.write(path, samples, sample_rate, subtype="PCM_16")
    return path


def _pipeline(
    *,
    thresholds: ThresholdSet | None = None,
    alignment: object | None = None,
    s1: object | None = None,
    s2: object | None = None,
    expected_phones: object | None = None,
    profile: str | None = None,
    reference_bank: object | None = None,
    prosody_distance: object | None = None,
    advice_generator: object | None = None,
) -> PronunciationFeedbackPipeline:
    threshold_set = thresholds or _calibrated()
    return PronunciationFeedbackPipeline(
        prompt_catalog=R00PromptCatalog(CARD_PATH),
        audio_intake=SoundfileAudioIntake(),
        threshold_store=FixedThresholdStore(threshold_set),
        advice_catalog=LegacyAdviceCatalog(),
        expected_phones=expected_phones or FixedExpectedPhones(),
        content_alignment=alignment or FixedAlignment(),
        s1_scorer=s1 or FixedS1Scorer(issues=(_coachable_issue(),)),
        s2_auditor=s2 or FixedS2Auditor(),
        reference_bank=reference_bank,
        prosody_distance=prosody_distance,
        advice_generator=advice_generator,
        analysis_profile=profile or threshold_set.analysis_profile,
    )


class FixedGroundedGenerator:
    generator_id = "fixture-hyperclova"
    revision = "fixture-model-commit"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def generate(self, payload):  # noqa: ANN001, ANN201 - protocol test double
        self.calls += 1
        if self.fail:
            raise RuntimeError("model failed")
        return GroundedAdvicePlan(
            schema_version=GROUNDED_OUTPUT_SCHEMA_VERSION,
            status=GroundedPlanStatus.RENDERED,
            issue_id=payload.issue_id,
            advice_id=payload.advice_id,
            selected_claim_ids=(payload.approved_claims[0].claim_id,),
            style=GroundedAdviceStyle.FOCUS,
            candidate_disclosure=(
                CandidateDisclosure.MODEL_CANDIDATE
                if payload.observed_candidate is not None
                else CandidateDisclosure.OMIT
            ),
        )


# --------------------------------------------------------------- prompt spec


def test_prepare_prompt_binds_version_to_card_text_and_phones() -> None:
    prompt, spec = _pipeline().prepare_prompt(PROMPT_ID)

    assert spec.prompt_id == PROMPT_ID
    assert spec.expected_phone_state is MetricState.OBSERVED
    assert prompt.normalized_text_sha256[:12] in spec.prompt_version
    assert spec.syllable_map_sha256[:12] in spec.prompt_version
    assert len(spec.syllables) == prompt.syllable_count
    assert spec.syllable(0) is not None


def test_target_language_gate_precedes_expected_phone_backend() -> None:
    base_catalog = R00PromptCatalog(CARD_PATH)
    unsupported_prompt = replace(
        base_catalog.get_prompt(PROMPT_ID),
        target_language="fr",  # type: ignore[arg-type]
    )
    backend_calls: list[str] = []

    class UnsupportedCatalog:
        revision = base_catalog.revision

        def get_prompt(self, prompt_id: str):  # noqa: ANN202 - local test double
            assert prompt_id == PROMPT_ID
            return unsupported_prompt

    class CountingExpectedPhones:
        def expected_phones(self, normalized_text: str) -> ExpectedPhoneSequence:
            backend_calls.append(normalized_text)
            return FixedExpectedPhones().expected_phones(normalized_text)

    pipeline = PronunciationFeedbackPipeline(
        prompt_catalog=UnsupportedCatalog(),  # type: ignore[arg-type]
        audio_intake=SoundfileAudioIntake(),
        threshold_store=FixedThresholdStore(_calibrated()),
        advice_catalog=LegacyAdviceCatalog(),
        expected_phones=CountingExpectedPhones(),
        analysis_profile=CALIBRATED_PROFILE,
    )

    with pytest.raises(PromptUnavailableError) as error:
        pipeline.prepare_prompt(PROMPT_ID)

    assert error.value.code is FailureCode.TARGET_LANGUAGE_NOT_SUPPORTED
    assert backend_calls == []


def test_calibrated_profile_fails_closed_without_expected_phones() -> None:
    pipeline = _pipeline(expected_phones=UnavailableExpectedPhoneFrontend())

    with pytest.raises(BackendUnavailableError) as error:
        pipeline.prepare_prompt(PROMPT_ID)

    assert error.value.code is FailureCode.EXPECTED_PHONES_UNAVAILABLE


def test_uncalibrated_profile_records_missing_expected_phones() -> None:
    pipeline = PronunciationFeedbackPipeline(
        prompt_catalog=R00PromptCatalog(CARD_PATH),
        audio_intake=SoundfileAudioIntake(),
        threshold_store=FixedThresholdStore(ThresholdSet.uncalibrated()),
        advice_catalog=LegacyAdviceCatalog(),
        expected_phones=UnavailableExpectedPhoneFrontend(),
        analysis_profile=UNCALIBRATED_PROFILE,
    )

    _, spec = pipeline.prepare_prompt(PROMPT_ID)

    assert spec.expected_phones is None
    assert spec.expected_phone_state is MetricState.BACKEND_UNAVAILABLE


# ------------------------------------------------------------------- attempt


def test_full_path_emits_located_advice(recording: Path) -> None:
    result = _pipeline().run_attempt(prompt_id=PROMPT_ID, audio_path=recording)

    assert result.status is AttemptStatus.COACHING_READY
    assert result.failure_code is None
    assert result.audio_sha256 is not None
    feedback = result.learner_feedback
    assert feedback is not None
    assert feedback.next_action == "coach"
    assert len(feedback.advice) == 1
    advice = feedback.advice[0]
    assert advice.advice_id == "ARTICULATION_RULE_017"
    assert advice.span is not None
    assert (advice.span.char_start, advice.span.char_end) == (0, 1)
    assert advice.span.syllable == "쌀"
    assert advice.span.start_ms == 120
    assert feedback.unlocated_issue_types == ()
    # The cohort notice names the split the thresholds were frozen on.  A fixed
    # string here would keep telling the learner about a cohort the loaded
    # threshold set has nothing to do with.
    assert any("fixture-cohort" in notice for notice in feedback.notices)
    assert any("engineering_provisional" in notice for notice in feedback.notices)
    assert result.claim_scope == "engineering_provisional"
    # Loop A is wired but no rights-cleared exemplar bank exists yet, so the
    # learner gets an explicit notice instead of an exemplar of unknown provenance.
    assert feedback.reference_audio == ()
    assert feedback.reference_distance is None
    assert any(
        "기준 음성 비교를 제공하지 않는다" in notice for notice in feedback.notices
    )
    assert [receipt.stage for receipt in result.receipts] == [
        "prompt_catalog",
        "prompt_spec",
        "audio_intake",
        "thresholds",
        "content_alignment",
        "s1_scorer",
        "s2_auditor",
        "gate",
    ]


def test_grounded_generator_selects_claim_but_pipeline_renders_message(
    recording: Path,
) -> None:
    generator = FixedGroundedGenerator()
    issue = _coachable_issue(evidence=("gop_low", "heard_syllable=사"))
    pipeline = _pipeline(
        s1=FixedS1Scorer(issues=(issue,)), advice_generator=generator
    )

    result = pipeline.run_attempt(prompt_id=PROMPT_ID, audio_path=recording)

    assert result.status is AttemptStatus.COACHING_READY
    assert generator.calls == 1
    assert result.learner_feedback is not None
    message = result.learner_feedback.advice[0].message
    catalog_message = LegacyAdviceCatalog().lookup(issue.issue_type)
    assert catalog_message is not None
    assert catalog_message[1] in message
    assert "확정한 전사가 아닙니다" in message
    receipt = next(item for item in result.receipts if item.stage == "advice_generator")
    assert receipt.backend_id == generator.generator_id
    assert receipt.revision == generator.revision
    assert receipt.detail == "validated_grounded_plan"


def test_grounded_generator_failure_falls_back_to_catalog(recording: Path) -> None:
    generator = FixedGroundedGenerator(fail=True)
    pipeline = _pipeline(advice_generator=generator)

    result = pipeline.run_attempt(prompt_id=PROMPT_ID, audio_path=recording)

    assert result.status is AttemptStatus.COACHING_READY
    assert result.learner_feedback is not None
    expected = LegacyAdviceCatalog().lookup("final_consonant_deletion")
    assert expected is not None
    assert result.learner_feedback.advice[0].message == expected[1]
    receipt = next(item for item in result.receipts if item.stage == "advice_generator")
    assert receipt.detail == "fallback=advice_catalog reason=RuntimeError"


def test_no_coached_issue_does_not_call_grounded_generator(recording: Path) -> None:
    generator = FixedGroundedGenerator()
    pipeline = _pipeline(
        s1=FixedS1Scorer(issues=()), advice_generator=generator
    )

    result = pipeline.run_attempt(prompt_id=PROMPT_ID, audio_path=recording)

    assert result.status is AttemptStatus.COMPLETED_NO_ISSUE
    assert generator.calls == 0
    assert not any(item.stage == "advice_generator" for item in result.receipts)


def test_segmental_advice_carries_typed_xlsr_soft_information(
    recording: Path,
) -> None:
    soft_information = CtcSoftInformation(
        syllable_index=0,
        target_syllable="쌀",
        target_log_posterior=-0.2,
        gop_margin=0.8,
        posterior_entropy=0.4,
        target_is_top1=True,
        frame_count=2,
        backend_id="fixture-s1-wav2vec2-xlsr",
        revision="fixture-1",
    )
    pipeline = _pipeline(
        s1=FixedS1Scorer(
            issues=(_coachable_issue(),),
            soft_information=(soft_information,),
        )
    )

    result = pipeline.run_attempt(prompt_id=PROMPT_ID, audio_path=recording)

    assert result.learner_feedback is not None
    advice = result.learner_feedback.advice[0]
    assert advice.soft_information == soft_information
    serialized = json.loads(json.dumps(asdict(advice), ensure_ascii=False))
    assert serialized["soft_information"]["target_syllable"] == "쌀"
    assert serialized["soft_information"]["posterior_entropy"] == 0.4
    receipt = next(item for item in result.receipts if item.stage == "s1_scorer")
    assert "soft_information=1" in (receipt.detail or "")


def test_issue_without_time_range_is_reported_without_a_span(
    recording: Path,
) -> None:
    pipeline = _pipeline(
        s1=FixedS1Scorer(issues=(_coachable_issue(start_ms=None, end_ms=None),))
    )

    result = pipeline.run_attempt(prompt_id=PROMPT_ID, audio_path=recording)

    assert result.status is AttemptStatus.COACHING_READY
    feedback = result.learner_feedback
    assert feedback is not None
    assert feedback.advice[0].span is None
    assert feedback.unlocated_issue_types == ("final_consonant_deletion",)


def test_unknown_syllable_index_does_not_invent_a_span(recording: Path) -> None:
    pipeline = _pipeline(
        s1=FixedS1Scorer(issues=(_coachable_issue(syllable_index=999),))
    )

    result = pipeline.run_attempt(prompt_id=PROMPT_ID, audio_path=recording)

    feedback = result.learner_feedback
    assert feedback is not None
    assert feedback.advice[0].span is None


def test_content_mismatch_blocks_pronunciation_judgement(recording: Path) -> None:
    pipeline = _pipeline(alignment=FixedAlignment(content_score=0.2))

    result = pipeline.run_attempt(prompt_id=PROMPT_ID, audio_path=recording)

    assert result.status is AttemptStatus.RERECORD_REQUIRED
    assert result.failure_code is FailureCode.PROMPT_MISMATCH
    feedback = result.learner_feedback
    assert feedback is not None
    assert feedback.advice == ()
    assert feedback.next_action == "rerecord"


def test_low_alignment_confidence_requests_rerecord(recording: Path) -> None:
    pipeline = _pipeline(alignment=FixedAlignment(alignment_confidence=0.1))

    result = pipeline.run_attempt(prompt_id=PROMPT_ID, audio_path=recording)

    assert result.status is AttemptStatus.RERECORD_REQUIRED
    assert result.failure_code is FailureCode.ALIGNMENT_LOW_CONFIDENCE


def test_missing_required_branch_is_uncertain_not_zero(recording: Path) -> None:
    pipeline = _pipeline(
        s1=FixedS1Scorer(
            issues=(_coachable_issue(),), observed=("content", "segmental")
        )
    )

    result = pipeline.run_attempt(prompt_id=PROMPT_ID, audio_path=recording)

    assert result.status is AttemptStatus.UNCERTAIN
    assert result.failure_code is FailureCode.S1_REQUIRED_METRIC_MISSING
    feedback = result.learner_feedback
    assert feedback is not None
    assert feedback.advice == ()
    duration = next(aspect for aspect in feedback.aspects if aspect.name == "duration")
    assert duration.score is None
    assert duration.state is MetricState.BACKEND_UNAVAILABLE


def test_s1_s2_disagreement_stops_automatic_coaching(recording: Path) -> None:
    pipeline = _pipeline(s2=FixedS2Auditor(verdict=AuditVerdict.DISAGREEMENT))

    result = pipeline.run_attempt(prompt_id=PROMPT_ID, audio_path=recording)

    assert result.status is AttemptStatus.UNCERTAIN
    assert result.failure_code is FailureCode.S1_S2_DISAGREEMENT


def test_s2_reusing_an_s1_backend_fails_closed(recording: Path) -> None:
    pipeline = _pipeline(s2=FixedS2Auditor(backend_id=FixedS1Scorer.backend_id))

    result = pipeline.run_attempt(prompt_id=PROMPT_ID, audio_path=recording)

    assert result.status is AttemptStatus.FAILED_CLOSED
    assert result.failure_code is FailureCode.S1_S2_NOT_INDEPENDENT
    assert result.learner_feedback is None


def test_issue_outside_the_advice_allowlist_is_not_coached(recording: Path) -> None:
    pipeline = _pipeline(
        s1=FixedS1Scorer(issues=(_coachable_issue(issue_type="pitch_range_narrow"),))
    )

    result = pipeline.run_attempt(prompt_id=PROMPT_ID, audio_path=recording)

    assert result.status is AttemptStatus.UNCERTAIN
    assert result.failure_code is FailureCode.ADVICE_NOT_ALLOWED


def test_issue_without_evidence_is_not_coached(recording: Path) -> None:
    pipeline = _pipeline(s1=FixedS1Scorer(issues=(_coachable_issue(evidence=()),)))

    result = pipeline.run_attempt(prompt_id=PROMPT_ID, audio_path=recording)

    assert result.status is AttemptStatus.COMPLETED_NO_ISSUE
    assert result.failure_code is None


def test_no_issue_completes_without_advice(recording: Path) -> None:
    pipeline = _pipeline(s1=FixedS1Scorer(issues=()))

    result = pipeline.run_attempt(prompt_id=PROMPT_ID, audio_path=recording)

    assert result.status is AttemptStatus.COMPLETED_NO_ISSUE
    feedback = result.learner_feedback
    assert feedback is not None
    assert feedback.advice == ()


def test_uncalibrated_profile_never_coaches(recording: Path) -> None:
    pipeline = _pipeline(
        thresholds=ThresholdSet.uncalibrated(), profile=UNCALIBRATED_PROFILE
    )

    result = pipeline.run_attempt(prompt_id=PROMPT_ID, audio_path=recording)

    assert result.status is AttemptStatus.UNCERTAIN
    assert result.failure_code is FailureCode.UNCALIBRATED_PROFILE
    feedback = result.learner_feedback
    assert feedback is not None
    assert feedback.advice == ()
    assert any("보정된 threshold" in notice for notice in feedback.notices)


def test_silent_recording_requests_rerecord_before_any_model(
    tmp_path: Path,
) -> None:
    path = tmp_path / "silent.wav"
    sf.write(path, np.zeros(16_000, dtype=np.float32), 16_000, subtype="PCM_16")

    result = _pipeline().run_attempt(prompt_id=PROMPT_ID, audio_path=path)

    assert result.status is AttemptStatus.RERECORD_REQUIRED
    assert result.failure_code is FailureCode.AUDIO_QC_FAILED
    assert [receipt.stage for receipt in result.receipts] == [
        "prompt_catalog",
        "prompt_spec",
        "audio_intake",
    ]


def test_absent_audio_file_fails_closed(tmp_path: Path) -> None:
    result = _pipeline().run_attempt(
        prompt_id=PROMPT_ID, audio_path=tmp_path / "absent.wav"
    )

    assert result.status is AttemptStatus.FAILED_CLOSED
    assert result.failure_code is FailureCode.AUDIO_INVALID


def test_unknown_prompt_fails_closed(recording: Path) -> None:
    result = _pipeline().run_attempt(prompt_id="R00-99", audio_path=recording)

    assert result.status is AttemptStatus.FAILED_CLOSED
    assert result.failure_code is FailureCode.PROMPT_NOT_AVAILABLE


def test_missing_threshold_artifact_fails_closed(recording: Path) -> None:
    pipeline = _pipeline(profile="no_such_profile")

    result = pipeline.run_attempt(prompt_id=PROMPT_ID, audio_path=recording)

    assert result.status is AttemptStatus.FAILED_CLOSED
    assert result.failure_code is FailureCode.THRESHOLDS_UNAVAILABLE


def test_unconnected_analysis_backends_fail_closed(recording: Path) -> None:
    pipeline = PronunciationFeedbackPipeline(
        prompt_catalog=R00PromptCatalog(CARD_PATH),
        audio_intake=SoundfileAudioIntake(),
        threshold_store=FixedThresholdStore(ThresholdSet.uncalibrated()),
        advice_catalog=LegacyAdviceCatalog(),
        expected_phones=FixedExpectedPhones(),
        analysis_profile=UNCALIBRATED_PROFILE,
    )

    result = pipeline.run_attempt(prompt_id=PROMPT_ID, audio_path=recording)

    assert result.status is AttemptStatus.FAILED_CLOSED
    assert result.failure_code is FailureCode.CONTENT_BACKEND_UNAVAILABLE
    assert result.learner_feedback is None


def test_rerecord_then_second_attempt_is_independent(recording: Path) -> None:
    mismatched = _pipeline(alignment=FixedAlignment(content_score=0.1))
    first = mismatched.run_attempt(prompt_id=PROMPT_ID, audio_path=recording)
    second = _pipeline().run_attempt(prompt_id=PROMPT_ID, audio_path=recording)

    assert first.status is AttemptStatus.RERECORD_REQUIRED
    assert second.status is AttemptStatus.COACHING_READY
    assert first.prompt_version == second.prompt_version
    assert first.audio_sha256 == second.audio_sha256


def test_calibrated_threshold_set_requires_provenance() -> None:
    with pytest.raises(ValueError):
        ThresholdSet(
            threshold_set_id="missing-provenance",
            analysis_profile=CALIBRATED_PROFILE,
            calibrated=True,
            cohort="fixture-cohort",
            min_content_score=0.8,
        )


def test_research_validated_claim_requires_a_human_gate_receipt() -> None:
    with pytest.raises(ValueError):
        ThresholdSet(
            threshold_set_id="fixture-thresholds-v1",
            analysis_profile=CALIBRATED_PROFILE,
            calibrated=True,
            cohort="fixture-cohort",
            frozen_at="2026-08-06",
            dataset_id="fixture-dataset",
            manifest_sha256="a" * 64,
            min_content_score=0.8,
            min_alignment_confidence=0.6,
            min_s1_confidence=0.7,
            min_s2_confidence=0.7,
            min_issue_confidence=0.6,
            required_branches=S1_BRANCHES,
            claim_scope="research_validated",
        )


def test_research_validated_feedback_drops_the_engineering_notice(
    recording: Path,
) -> None:
    validated = replace(
        _calibrated(),
        claim_scope="research_validated",
        human_gate_receipt="fixture-human-gate-2026-08-06",
    )
    result = _pipeline(thresholds=validated).run_attempt(
        prompt_id=PROMPT_ID, audio_path=recording
    )

    assert result.claim_scope == "research_validated"
    feedback = result.learner_feedback
    assert feedback is not None
    assert not any("engineering_provisional" in n for n in feedback.notices)


# ------------------------------------------------------------------- Loop A


class _LoopABank:
    """A bank port that returns a fixed exemplar set."""

    bank_id = "test-bank"

    def __init__(self, count: int) -> None:
        self._count = count

    def bank_for(self, *, prompt_id, speed_axis=None, speaker_axis=None):
        del speed_axis, speaker_axis
        exemplars = tuple(
            ReferenceExemplar(
                exemplar_id=f"ex{index}",
                prompt_id=prompt_id,
                generator_id="cosyvoice2-0.5b",
                generator_revision="074ca6dc9e80+eec1ae6c7987",
                audio_sha256="a" * 64,
                duration_seconds=2.0,
                sample_rate=24_000,
                speaker_axis=f"spk{index}",
                speed_axis="neutral",
                license_spdx="Apache-2.0",
                rights_basis="vendor demo asset",
                synthetic=True,
                disclosure="합성된 참조음입니다.",
            )
            for index in range(self._count)
        )
        return ReferenceBank(
            prompt_id=prompt_id,
            exemplars=exemplars,
            selection_id="sel-1" if exemplars else "",
            selection_rule="test",
        )


class _ThrowingBank:
    bank_id = "throwing-bank"

    def bank_for(self, *, prompt_id, speed_axis=None, speaker_axis=None):
        raise RuntimeError("the bank is being rebuilt")


class _FixedDistance:
    backend_id = "test-distance"

    def distance_to_bank(self, *, audio_path, prompt_spec, bank):
        del audio_path, prompt_spec
        return MultiReferenceDistance(
            minimum_distance=0.4,
            nearest_exemplar_id=bank.exemplars[0].exemplar_id,
            state=MetricState.OBSERVED,
            exemplar_count=len(bank.exemplars),
        )


class _ThrowingDistance:
    backend_id = "throwing-distance"

    def distance_to_bank(self, *, audio_path, prompt_spec, bank):
        raise OSError("the acoustics interpreter vanished")


def _stage(result, name: str):
    return next((item for item in result.receipts if item.stage == name), None)


def test_no_bank_port_leaves_the_response_unchanged(recording: Path) -> None:
    result = _pipeline().run_attempt(prompt_id=PROMPT_ID, audio_path=recording)

    assert result.status is not AttemptStatus.FAILED_CLOSED
    assert result.learner_feedback is not None
    assert result.learner_feedback.reference_audio == ()
    assert _stage(result, "reference_bank") is None


def test_exemplars_reach_the_learner(recording: Path) -> None:
    result = _pipeline(reference_bank=_LoopABank(2)).run_attempt(
        prompt_id=PROMPT_ID, audio_path=recording
    )

    assert result.learner_feedback is not None
    assert len(result.learner_feedback.reference_audio) == 2
    assert _stage(result, "reference_bank").detail == "exemplars=2"


def test_the_distance_is_recorded_when_both_ports_are_present(
    recording: Path,
) -> None:
    result = _pipeline(
        reference_bank=_LoopABank(3), prosody_distance=_FixedDistance()
    ).run_attempt(prompt_id=PROMPT_ID, audio_path=recording)

    assert result.learner_feedback is not None
    distance = result.learner_feedback.reference_distance
    assert distance is not None
    assert distance.state is MetricState.OBSERVED
    assert _stage(result, "prosody_distance").detail == "observed"


def test_an_empty_bank_skips_the_distance_without_failing(recording: Path) -> None:
    """Nothing to be near or far from, so there is no distance to report."""

    result = _pipeline(
        reference_bank=_LoopABank(0), prosody_distance=_FixedDistance()
    ).run_attempt(prompt_id=PROMPT_ID, audio_path=recording)

    assert result.status is not AttemptStatus.FAILED_CLOSED
    assert result.learner_feedback is not None
    assert result.learner_feedback.reference_audio == ()
    assert result.learner_feedback.reference_distance is None
    assert _stage(result, "reference_bank").detail == "exemplars=0"
    assert _stage(result, "prosody_distance") is None


def test_a_throwing_bank_does_not_fail_the_attempt(recording: Path) -> None:
    """The learner has already spoken; Loop A owes them their other feedback."""

    result = _pipeline(
        reference_bank=_ThrowingBank(), prosody_distance=_FixedDistance()
    ).run_attempt(prompt_id=PROMPT_ID, audio_path=recording)

    assert result.status is not AttemptStatus.FAILED_CLOSED
    assert result.learner_feedback is not None
    assert result.learner_feedback.reference_audio == ()
    receipt = _stage(result, "reference_bank")
    assert receipt is not None
    # Named without leaking the exception text into a learner-facing record.
    assert receipt.detail == "unavailable: RuntimeError"


def test_a_throwing_distance_keeps_the_exemplars(recording: Path) -> None:
    result = _pipeline(
        reference_bank=_LoopABank(2), prosody_distance=_ThrowingDistance()
    ).run_attempt(prompt_id=PROMPT_ID, audio_path=recording)

    assert result.status is not AttemptStatus.FAILED_CLOSED
    assert result.learner_feedback is not None
    assert len(result.learner_feedback.reference_audio) == 2
    assert result.learner_feedback.reference_distance is None
    assert _stage(result, "prosody_distance").detail == "unavailable: OSError"


def test_a_bank_without_a_distance_port_still_serves_exemplars(
    recording: Path,
) -> None:
    result = _pipeline(reference_bank=_LoopABank(1)).run_attempt(
        prompt_id=PROMPT_ID, audio_path=recording
    )

    assert result.learner_feedback is not None
    assert len(result.learner_feedback.reference_audio) == 1
    assert result.learner_feedback.reference_distance is None
    assert _stage(result, "prosody_distance") is None


def test_synthetic_exemplars_are_disclosed(recording: Path) -> None:
    result = _pipeline(reference_bank=_LoopABank(2)).run_attempt(
        prompt_id=PROMPT_ID, audio_path=recording
    )

    assert result.learner_feedback is not None
    assert all(item.synthetic for item in result.learner_feedback.reference_audio)
    assert all(item.disclosure for item in result.learner_feedback.reference_audio)
    assert len(result.learner_feedback.notices) >= 2
