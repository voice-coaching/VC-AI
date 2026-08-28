"""Stage A0--H orchestration for one practice attempt.

``run_attempt`` is stateless and synchronous: it takes a catalog prompt id and a
recording path, and returns exactly one terminal :class:`AttemptResult`.  A
re-record is a second call with the same prompt id; keeping attempt history is
the caller's job (this project ships no session store).

Ordering is deliberate — the cheap, model-free gates run first, so a wrong file
or a silent recording never reaches a GPU backend:

    prompt -> prompt spec -> audio QC -> thresholds -> content/alignment
           -> S1 -> S2 independence -> gate -> feedback composition
"""

from __future__ import annotations

from pathlib import Path

from .domain import (
    AttemptResult,
    AttemptStatus,
    AudioObservation,
    AudioQcStatus,
    DiagnosticIssueDraft,
    ExpectedPhoneSequence,
    FailureCode,
    MetricState,
    MultiReferenceDistance,
    PracticePrompt,
    PromptSpec,
    ReferenceBank,
    StageReceipt,
)
from .errors import PronunciationFeedbackError
from .grounded_advice import (
    build_grounded_coaching_payload,
    render_grounded_advice_message,
    validate_grounded_advice_plan,
)
from .language_scope import require_supported_target_language
from .feedback import compose_learner_feedback
from .gate import evaluate_gate
from .ports import (
    AdviceCatalogPort,
    AudioIntakePort,
    ContentAlignmentPort,
    ExpectedPhonePort,
    GroundedAdviceGeneratorPort,
    PromptCatalogPort,
    ProsodyDistancePort,
    ReferenceBankPort,
    S1ScorerPort,
    S2AuditorPort,
    ThresholdSetPort,
)
from .thresholds import UNCALIBRATED_PROFILE

_PROMPT_STAGE = "prompt_catalog"


class PronunciationFeedbackPipeline:
    """Composes the ports for a single practice attempt.

    ``require_expected_phones`` states whether a rule-applied expected phone
    sequence is mandatory.  It defaults to ``True`` for calibrated profiles: a
    written-grapheme mapping is not a pronunciation target, so scoring without
    the g2pK contract would silently change what is being measured.
    """

    def __init__(
        self,
        *,
        prompt_catalog: PromptCatalogPort,
        audio_intake: AudioIntakePort,
        threshold_store: ThresholdSetPort,
        advice_catalog: AdviceCatalogPort,
        expected_phones: ExpectedPhonePort | None = None,
        content_alignment: ContentAlignmentPort | None = None,
        s1_scorer: S1ScorerPort | None = None,
        s2_auditor: S2AuditorPort | None = None,
        reference_bank: ReferenceBankPort | None = None,
        prosody_distance: ProsodyDistancePort | None = None,
        advice_generator: GroundedAdviceGeneratorPort | None = None,
        analysis_profile: str = UNCALIBRATED_PROFILE,
        require_expected_phones: bool | None = None,
    ) -> None:
        self._prompt_catalog = prompt_catalog
        self._audio_intake = audio_intake
        self._threshold_store = threshold_store
        self._advice_catalog = advice_catalog
        self._expected_phones = expected_phones
        self._content_alignment = content_alignment
        self._s1_scorer = s1_scorer
        self._s2_auditor = s2_auditor
        self._reference_bank = reference_bank
        self._prosody_distance = prosody_distance
        self._advice_generator = advice_generator
        self._analysis_profile = analysis_profile
        self._require_expected_phones = (
            analysis_profile != UNCALIBRATED_PROFILE
            if require_expected_phones is None
            else require_expected_phones
        )

    # ---------------------------------------------------------------- prompts

    def list_prompts(
        self,
        *,
        difficulty: str | None = None,
        target: str | None = None,
        limit: int | None = None,
    ) -> tuple[PracticePrompt, ...]:
        """Stage A0: what the learner may be asked to read."""

        return self._prompt_catalog.list_prompts(
            difficulty=difficulty, target=target, limit=limit
        )

    def prepare_prompt(self, prompt_id: str) -> tuple[PracticePrompt, PromptSpec]:
        """Stage A0--A: fix the prompt and derive the model-side contract."""

        from .adapters.syllables import (
            decompose_syllables,
            syllable_map_sha256,
        )

        prompt = self._prompt_catalog.get_prompt(prompt_id)
        target_language = require_supported_target_language(prompt.target_language)
        slots = decompose_syllables(prompt.normalized_text)
        if not slots:
            raise PronunciationFeedbackError(
                FailureCode.PROMPT_INVALID,
                f"{prompt_id}: prompt has no Hangul syllable to anchor feedback",
            )
        map_digest = syllable_map_sha256(slots)

        expected: ExpectedPhoneSequence | None = None
        expected_state = MetricState.BACKEND_UNAVAILABLE
        if self._expected_phones is not None:
            try:
                expected = self._expected_phones.expected_phones(prompt.normalized_text)
                expected_state = MetricState.OBSERVED
            except PronunciationFeedbackError:
                if self._require_expected_phones:
                    raise
                expected_state = MetricState.BACKEND_UNAVAILABLE
        elif self._require_expected_phones:
            raise PronunciationFeedbackError(
                FailureCode.EXPECTED_PHONES_UNAVAILABLE,
                "profile requires expected phones but no frontend is configured",
            )

        revision = expected.revision if expected is not None else "no-expected-phones"
        prompt_version = (
            f"{prompt.catalog_revision}/{prompt.normalized_text_sha256[:12]}"
            f"/{map_digest[:12]}/{revision}"
        )
        spec = PromptSpec(
            prompt_id=prompt.prompt_id,
            prompt_version=prompt_version,
            target_language=target_language,
            normalized_text=prompt.normalized_text,
            normalized_text_sha256=prompt.normalized_text_sha256,
            syllables=slots,
            syllable_map_sha256=map_digest,
            expected_phones=expected,
            expected_phone_state=expected_state,
        )
        return prompt, spec

    # ---------------------------------------------------------------- attempt

    def run_attempt(
        self,
        *,
        prompt_id: str,
        audio_path: str | Path,
    ) -> AttemptResult:
        receipts: list[StageReceipt] = [
            StageReceipt(
                stage=_PROMPT_STAGE,
                backend_id="prompt_catalog",
                revision=self._prompt_catalog.revision,
            )
        ]

        try:
            prompt, spec = self.prepare_prompt(prompt_id)
        except PronunciationFeedbackError as exc:
            return self._failed(exc, receipts, prompt_id=prompt_id)
        receipts.append(
            StageReceipt(
                stage="prompt_spec",
                backend_id=(
                    spec.expected_phones.backend_id
                    if spec.expected_phones is not None
                    else None
                ),
                revision=(
                    spec.expected_phones.revision
                    if spec.expected_phones is not None
                    else None
                ),
                detail=f"expected_phones={spec.expected_phone_state.value}",
            )
        )

        path = Path(audio_path)
        if not path.is_file():
            return self._terminal(
                AttemptStatus.FAILED_CLOSED,
                FailureCode.AUDIO_INVALID,
                "audio file does not exist",
                receipts,
                prompt=prompt,
                spec=spec,
            )
        observation = self._audio_intake.observe(path)
        receipts.append(
            StageReceipt(
                stage="audio_intake",
                backend_id=observation.decoder_id,
                revision=observation.decoder_revision,
                detail=observation.status.value,
            )
        )
        if observation.status is AudioQcStatus.DECODE_FAILED:
            return self._terminal(
                AttemptStatus.FAILED_CLOSED,
                FailureCode.AUDIO_INVALID,
                "audio could not be decoded",
                receipts,
                prompt=prompt,
                spec=spec,
                observation=observation,
            )
        if observation.status is AudioQcStatus.QC_FAILED:
            return self._terminal(
                AttemptStatus.RERECORD_REQUIRED,
                FailureCode.AUDIO_QC_FAILED,
                "recording quality check failed: " + ", ".join(observation.qc_failures),
                receipts,
                prompt=prompt,
                spec=spec,
                observation=observation,
            )

        try:
            thresholds = self._threshold_store.load(self._analysis_profile)
        except PronunciationFeedbackError as exc:
            return self._failed(
                exc, receipts, prompt_id=prompt_id, spec=spec, observation=observation
            )
        receipts.append(
            StageReceipt(
                stage="thresholds",
                backend_id=thresholds.threshold_set_id,
                revision=thresholds.frozen_at,
                detail=f"calibrated={thresholds.calibrated} cohort={thresholds.cohort}",
            )
        )

        missing_backends = [
            name
            for name, port in (
                ("content_alignment", self._content_alignment),
                ("s1_scorer", self._s1_scorer),
                ("s2_auditor", self._s2_auditor),
            )
            if port is None
        ]
        if missing_backends:
            code = {
                "content_alignment": FailureCode.CONTENT_BACKEND_UNAVAILABLE,
                "s1_scorer": FailureCode.S1_BACKEND_UNAVAILABLE,
                "s2_auditor": FailureCode.S2_BACKEND_UNAVAILABLE,
            }[missing_backends[0]]
            return self._terminal(
                AttemptStatus.FAILED_CLOSED,
                code,
                "analysis backends not connected: " + ", ".join(missing_backends),
                receipts,
                prompt=prompt,
                spec=spec,
                observation=observation,
            )
        assert self._content_alignment is not None
        assert self._s1_scorer is not None
        assert self._s2_auditor is not None

        try:
            alignment = self._content_alignment.analyze(
                audio_path=path, prompt_spec=spec
            )
            receipts.append(
                StageReceipt(
                    stage="content_alignment",
                    backend_id=alignment.backend_id,
                    revision=alignment.revision,
                    detail=f"content={alignment.content_state.value}",
                )
            )
            bundle = self._s1_scorer.score(
                audio_path=path, prompt_spec=spec, alignment=alignment
            )
            receipts.append(
                StageReceipt(
                    stage="s1_scorer",
                    backend_id=",".join(bundle.backend_ids()),
                    revision=None,
                    detail=(
                        "observed="
                        + ",".join(bundle.observed_names())
                        + f" soft_information={len(bundle.soft_information)}"
                    ),
                )
            )
            s1_backend_ids = tuple(
                dict.fromkeys((alignment.backend_id, *bundle.backend_ids()))
            )
            audit = self._s2_auditor.audit(
                audio_path=path, prompt_spec=spec, s1_backend_ids=s1_backend_ids
            )
        except PronunciationFeedbackError as exc:
            return self._failed(
                exc, receipts, prompt_id=prompt_id, spec=spec, observation=observation
            )
        # The independence claim is the reason this stage is worth running, so
        # it belongs in the receipt: a reader should not have to infer it from
        # the absence of an ``s1_s2_not_independent`` failure.
        independence = (
            "independence=unstated"
            if audit.independence is None
            else f"independent={audit.independence.independent} "
            f"({audit.independence.reason})"
        )
        receipts.append(
            StageReceipt(
                stage="s2_auditor",
                backend_id=audit.backend_id,
                revision=audit.revision,
                detail=f"verdict={audit.verdict.value} {independence}",
            )
        )

        decision = evaluate_gate(
            alignment=alignment,
            bundle=bundle,
            audit=audit,
            issues=bundle.issues,
            thresholds=thresholds,
            advice_catalog=self._advice_catalog,
        )
        receipts.append(
            StageReceipt(
                stage="gate",
                backend_id=self._advice_catalog.catalog_id,
                revision=None,
                detail=decision.status.value,
            )
        )
        if decision.status is AttemptStatus.FAILED_CLOSED:
            return self._terminal(
                decision.status,
                decision.failure_code,
                decision.reason,
                receipts,
                prompt=prompt,
                spec=spec,
                observation=observation,
            )

        bank, distance = self._resolve_loop_a(
            prompt_id=prompt.prompt_id, spec=spec, audio_path=path, receipts=receipts
        )

        message_overrides = self._resolve_grounded_advice(
            spec=spec,
            coached_issues=decision.coached_issues,
            claim_scope=thresholds.claim_scope,
            receipts=receipts,
        )

        feedback = compose_learner_feedback(
            prompt=prompt,
            prompt_spec=spec,
            alignment=alignment,
            bundle=bundle,
            coached_issues=decision.coached_issues,
            advice_catalog=self._advice_catalog,
            thresholds=thresholds,
            next_action=decision.next_action,
            message_overrides=message_overrides,
            reference_bank=bank,
            reference_distance=distance,
        )
        return AttemptResult(
            status=decision.status,
            failure_code=decision.failure_code,
            reason=decision.reason,
            prompt_id=prompt.prompt_id,
            prompt_version=spec.prompt_version,
            analysis_profile=self._analysis_profile,
            audio_sha256=observation.audio_sha256,
            learner_feedback=feedback,
            receipts=tuple(receipts),
            claim_scope=thresholds.claim_scope,
        )

    # ---------------------------------------------------------- grounded LLM

    def _resolve_grounded_advice(
        self,
        *,
        spec: PromptSpec,
        coached_issues: tuple[DiagnosticIssueDraft, ...],
        claim_scope: str,
        receipts: list[StageReceipt],
    ) -> dict[str, str]:
        """Ask the optional local LLM to select claims, then render locally.

        This stage cannot change the gate decision.  Any transport, parsing,
        validation, or rendering failure is recorded by exception type only and
        returns no override, causing ``compose_learner_feedback`` to use the
        frozen catalog message.
        """

        if self._advice_generator is None or not coached_issues:
            return {}
        overrides: dict[str, str] = {}
        try:
            for index, issue in enumerate(coached_issues, start=1):
                entry = self._advice_catalog.lookup(issue.issue_type)
                if entry is None:
                    continue
                advice_id, catalog_message = entry
                payload = build_grounded_coaching_payload(
                    prompt_spec=spec,
                    issue=issue,
                    issue_id=f"issue-{index}",
                    advice_id=advice_id,
                    advice_message=catalog_message,
                    claim_scope=claim_scope,
                )
                plan = self._advice_generator.generate(payload)
                validate_grounded_advice_plan(payload=payload, plan=plan)
                overrides[issue.issue_type] = render_grounded_advice_message(
                    payload=payload, plan=plan
                )
        except Exception as error:  # noqa: BLE001 - catalog fallback is mandatory
            receipts.append(
                StageReceipt(
                    stage="advice_generator",
                    backend_id=self._advice_generator.generator_id,
                    revision=self._advice_generator.revision,
                    detail=f"fallback=advice_catalog reason={type(error).__name__}",
                )
            )
            return {}
        receipts.append(
            StageReceipt(
                stage="advice_generator",
                backend_id=self._advice_generator.generator_id,
                revision=self._advice_generator.revision,
                detail="validated_grounded_plan",
            )
        )
        return overrides

    # ------------------------------------------------------------------ loopA

    def _resolve_loop_a(
        self,
        *,
        prompt_id: str,
        spec: PromptSpec,
        audio_path: Path,
        receipts: list[StageReceipt],
    ) -> tuple[ReferenceBank | None, MultiReferenceDistance | None]:
        """Stage L: the exemplars the learner may hear, and the distance to them.

        Loop A never fails an attempt. A learner who has just spoken should still
        get their content and GOP feedback when the reference bank is empty, being
        rebuilt, or unreadable — the exemplars are an addition to the response, not
        a precondition for it. So every failure here becomes absence plus a receipt
        that says which stage was skipped and why.
        """

        if self._reference_bank is None:
            return None, None
        try:
            bank = self._reference_bank.bank_for(prompt_id=prompt_id)
        except Exception as error:  # noqa: BLE001 - Loop A must not fail an attempt
            receipts.append(
                StageReceipt(
                    stage="reference_bank",
                    backend_id=self._reference_bank.bank_id,
                    revision=None,
                    detail=f"unavailable: {type(error).__name__}",
                )
            )
            return None, None
        receipts.append(
            StageReceipt(
                stage="reference_bank",
                backend_id=self._reference_bank.bank_id,
                revision=bank.selection_id or None,
                detail=f"exemplars={len(bank.exemplars)}",
            )
        )
        if self._prosody_distance is None or bank.is_empty:
            return bank, None
        try:
            distance = self._prosody_distance.distance_to_bank(
                audio_path=audio_path, prompt_spec=spec, bank=bank
            )
        except Exception as error:  # noqa: BLE001 - see above
            receipts.append(
                StageReceipt(
                    stage="prosody_distance",
                    backend_id=self._prosody_distance.backend_id,
                    revision=None,
                    detail=f"unavailable: {type(error).__name__}",
                )
            )
            return bank, None
        receipts.append(
            StageReceipt(
                stage="prosody_distance",
                backend_id=self._prosody_distance.backend_id,
                revision=None,
                detail=distance.state.value,
            )
        )
        return bank, distance

    # ----------------------------------------------------------------- result

    def _failed(
        self,
        exc: PronunciationFeedbackError,
        receipts: list[StageReceipt],
        *,
        prompt_id: str,
        spec: PromptSpec | None = None,
        observation: AudioObservation | None = None,
    ) -> AttemptResult:
        return AttemptResult(
            status=AttemptStatus.FAILED_CLOSED,
            failure_code=exc.code,
            reason=exc.detail,
            prompt_id=prompt_id,
            prompt_version=spec.prompt_version if spec is not None else None,
            analysis_profile=self._analysis_profile,
            audio_sha256=(
                observation.audio_sha256 if observation is not None else None
            ),
            learner_feedback=None,
            receipts=tuple(receipts),
        )

    def _terminal(
        self,
        status: AttemptStatus,
        failure_code: FailureCode | None,
        reason: str,
        receipts: list[StageReceipt],
        *,
        prompt: PracticePrompt,
        spec: PromptSpec,
        observation: AudioObservation | None = None,
    ) -> AttemptResult:
        return AttemptResult(
            status=status,
            failure_code=failure_code,
            reason=reason,
            prompt_id=prompt.prompt_id,
            prompt_version=spec.prompt_version,
            analysis_profile=self._analysis_profile,
            audio_sha256=(
                observation.audio_sha256 if observation is not None else None
            ),
            learner_feedback=None,
            receipts=tuple(receipts),
        )
