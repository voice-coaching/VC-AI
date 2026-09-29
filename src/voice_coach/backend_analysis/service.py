"""Fail-closed orchestration for one VC-BE stream request."""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from .domain import (
    AnalysisRequest,
    AnalysisWorkerResult,
    ClosedBetaDebug,
    VisualSupplement,
)
from .grounded_feedback import BackendGroundedFeedback
from .ports import (
    AnalysisAuthorization,
    AnalysisAuthorizationPort,
    BackendAnalysisError,
    RestrictedObjectStorePort,
    SeungunAnalysisPort,
    MaterializedAudio,
    SeungunAnalysis,
    VisualAnalysisPort,
)


_WORKER_REVISION = "vc-be-redis-worker-v2"
_SAFE_FAILURES = {
    "consent_evidence_unavailable": "분석 처리 권한을 확인할 수 없습니다.",
    "consent_evidence_expired": "분석 처리 권한이 만료되었습니다.",
    "consent_policy_not_permitted": "현재 처리 정책으로는 분석할 수 없습니다.",
    "audio_metadata_mismatch": "등록된 녹음 메타데이터와 일치하지 않습니다.",
    "audio_integrity_mismatch": "등록된 녹음의 무결성을 확인할 수 없습니다.",
    "restricted_audio_unavailable": "승인된 녹음에 접근할 수 없습니다.",
    "audio_decode_unsupported": "지원되지 않는 녹음 형식입니다.",
    "analysis_focus_not_supported": "현재 학습 초점은 이 분석 파이프라인에서 지원하지 않습니다.",
    "seungun_backend_unavailable": "현재 분석 엔진을 사용할 수 없습니다. 다시 시도해 주세요.",
    "seungun_detector_failed": "분석을 안전하게 완료하지 못했습니다. 다시 시도해 주세요.",
    "analysis_execution_failed_closed": "분석을 안전하게 완료하지 못했습니다. 다시 시도해 주세요.",
}


@dataclass(frozen=True, slots=True)
class _VisualCapture:
    supplement: VisualSupplement | None
    materialized_path: str | None
    media_base64: str | None
    grounded_evidence: Mapping[str, object] | None = field(default=None, repr=False)
    feedback_state: str = "UNAVAILABLE"
    coaching_observations: tuple = ()


class BackendAnalysisWorker:
    """Coordinates authorization, restricted media, and Seungun in that order.

    This service deliberately has no Redis, object storage, subprocess, or model
    import.  Its result is ready for one result-stream ``XADD`` but it never
    acknowledges the request itself; the stream adapter ACKs only after that
    terminal result is durably written.
    """

    def __init__(
        self,
        *,
        authorization: AnalysisAuthorizationPort | None,
        object_store: RestrictedObjectStorePort,
        seungun: SeungunAnalysisPort,
        visual: VisualAnalysisPort | None = None,
        grounded_feedback: BackendGroundedFeedback | None = None,
        clova_scorer=None,
        coaching=None,
        event_id_factory: Callable[[], UUID] = uuid4,
        clock: Callable[[], datetime] | None = None,
        worker_revision: str = _WORKER_REVISION,
    ) -> None:
        if not worker_revision or len(worker_revision) > 100:
            raise ValueError("worker revision must be non-empty and at most 100 characters")
        self._authorization = authorization
        self._object_store = object_store
        self._seungun = seungun
        self._visual = visual
        self._grounded_feedback = grounded_feedback
        self._clova_scorer = clova_scorer
        self._coaching = coaching
        self._event_id_factory = event_id_factory
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self._worker_revision = worker_revision

    def process(self, request: AnalysisRequest) -> AnalysisWorkerResult:
        """Return one terminal result without exposing implementation details."""

        try:
            authorization = self._authorization.authorize(request)
            self._validate_authorization(authorization)
        except BackendAnalysisError as error:
            return self._failed(request, error.code, error.reason)
        except Exception:
            return self._failed(
                request,
                "consent_evidence_unavailable",
                "분석 처리 권한을 확인할 수 없습니다.",
            )

        return self.process_admitted(request, authorization=authorization)

    def process_admitted(self, request, *, authorization=None) -> AnalysisWorkerResult:
        """Run the common core after the transport has admitted the request.

        HTTP callers hold a Backend claim; Redis callers pass validated consent.
        This method never creates or validates a substitute grant.
        """
        if request.learning_focus != "PRONUNCIATION":
            return self._failed(
                request,
                "analysis_focus_not_supported",
                "현재 학습 초점은 이 분석 파이프라인에서 지원하지 않습니다.",
            )

        try:
            with self._object_store.materialize(request, authorization) as audio:
                if (
                    request.file_size_bytes is not None
                    and audio.byte_count != request.file_size_bytes
                ):
                    raise BackendAnalysisError(
                        "audio_metadata_mismatch",
                        "등록된 녹음 메타데이터와 일치하지 않습니다.",
                    )
                raw_audio_base64 = (
                    base64.b64encode(audio.path.read_bytes()).decode("ascii")
                    if request.closed_beta_context is not None
                    else None
                )
                analysis = self._seungun.analyze(request=request, audio=audio)
                visual_capture = self._visual_supplement(
                    request=request,
                    authorization=authorization,
                    audio=audio,
                    analysis=analysis,
                )
                overall_score, scoring_evidence = (None, None)
                if self._clova_scorer is not None and self._coaching is None:
                    if getattr(self._clova_scorer, "rubric_revision", None) == "clova-phone-rubric-v3":
                        overall_score, scoring_evidence = self._clova_scorer.score(
                            analysis, video_requested=request.visual_input is not None,
                            visual_supplement=visual_capture.supplement)
                    else:
                        overall_score, scoring_evidence = self._clova_scorer.score(analysis)
                summary_feedback = analysis.summary_feedback
                coaching_document = None
                if self._coaching is not None:
                    coaching_document = self._coaching.render(request=request, analysis=analysis,
                        visual_state=visual_capture.feedback_state,
                        visual_observations=visual_capture.coaching_observations)
                    # Legacy selected evidence remains provenance; new coaching
                    # items alone carry the admitted learning agenda.
                    if analysis.summary_feedback is not None:
                        # The summary already contains the complete bounded
                        # learner feedback; do not append diagnostic/details.
                        summary_feedback = coaching_document["summary"]
                elif self._grounded_feedback is not None:
                    summary_feedback = self._grounded_feedback.render(
                        request=request,
                        analysis=analysis,
                        visual_supplement=visual_capture.supplement,
                        visual_evidence=visual_capture.grounded_evidence,
                        visual_state=visual_capture.feedback_state,
                        overall_score=overall_score, scoring_evidence=scoring_evidence,
                    )
                return AnalysisWorkerResult.completed(
                    coaching=coaching_document,
                    overall_score=overall_score, scoring_evidence=scoring_evidence,
                    event_id=self._event_id_factory(),
                    request=request,
                    outcome=analysis.outcome,
                    summary_feedback=summary_feedback,
                    pronunciation_evidence=analysis.pronunciation_evidence,
                    worker_revision=self._worker_revision,
                    pipeline_revision=analysis.pipeline_revision,
                    audio_sha256=audio.sha256,
                    visual_supplement=visual_capture.supplement,
                    seungun_production_evidence=analysis.production_evidence,
                    closed_beta_debug=self._closed_beta_debug(
                        request=request,
                        audio=audio,
                        analysis=analysis,
                        raw_audio_base64=raw_audio_base64,
                        visual_capture=visual_capture,
                    ),
                )
        except BackendAnalysisError as error:
            return self._failed(request, error.code, error.reason)
        except Exception:
            return self._failed(
                request,
                "analysis_execution_failed_closed",
                "분석을 안전하게 완료하지 못했습니다. 다시 시도해 주세요.",
            )

    def _visual_supplement(
        self,
        *,
        request: AnalysisRequest,
        authorization: AnalysisAuthorization,
        audio: MaterializedAudio,
        analysis: SeungunAnalysis,
    ) -> _VisualCapture:
        if request.visual_input is None:
            return _VisualCapture(None, None, None, feedback_state="NOT_PROVIDED")
        try:
            with self._object_store.materialize_visual(
                request, authorization
            ) as video:
                materialized_path = (
                    str(video.path)
                    if request.closed_beta_context is not None
                    else None
                )
                media_base64 = (
                    base64.b64encode(video.path.read_bytes()).decode("ascii")
                    if request.closed_beta_context is not None
                    else None
                )
                grounded_evidence = None
                feedback_state = "NO_PUBLISHABLE_EVIDENCE"
                if self._coaching is not None:
                    if self._visual is None:
                        return _VisualCapture(None, materialized_path, media_base64, feedback_state="NOT_CONFIGURED")
                    observations = self._visual.analyze_coaching_observations(
                        request=request, audio=audio, video=video, seungun=analysis)
                    state = "OBSERVED_NOT_VALIDATED" if any(o["measurements"] for o in observations) else "NO_VALID_OBSERVATIONS"
                    return _VisualCapture(None, materialized_path, media_base64,
                        feedback_state=state, coaching_observations=tuple(observations))
                if self._visual is None:
                    supplement = None
                    feedback_state = "NOT_CONFIGURED"
                else:
                    try:
                        analyze_with_evidence = (
                            getattr(self._visual, "analyze_with_grounded_evidence", None)
                            if self._grounded_feedback is not None or self._coaching is not None
                            else None
                        )
                        if callable(analyze_with_evidence):
                            supplement, grounded_evidence = analyze_with_evidence(
                                request=request, audio=audio, video=video, seungun=analysis
                            )
                            if (
                                supplement is not None and not isinstance(supplement, VisualSupplement)
                            ) or (
                                grounded_evidence is not None and not isinstance(grounded_evidence, Mapping)
                            ):
                                raise ValueError("visual_grounded_evidence_invalid")
                        else:
                            supplement = self._visual.analyze(
                                request=request,
                                audio=audio,
                                video=video,
                                seungun=analysis,
                            )
                    except Exception:
                        supplement = None
                        grounded_evidence = None
                        feedback_state = "EXTRACTION_FAILED"
                return _VisualCapture(
                    supplement,
                    materialized_path,
                    media_base64,
                    grounded_evidence,
                    feedback_state if supplement is None else "OBSERVED",
                )
        except Exception:
            # Visual evidence is optional and cannot erase an approved Seungun result.
            return _VisualCapture(None, None, None, feedback_state="VISUAL_INPUT_PROCESSING_FAILED")

    @staticmethod
    def _closed_beta_debug(
        *,
        request: AnalysisRequest,
        audio: MaterializedAudio,
        analysis: SeungunAnalysis,
        raw_audio_base64: str | None,
        visual_capture: _VisualCapture,
    ) -> ClosedBetaDebug | None:
        context = request.closed_beta_context
        if context is None:
            return None
        video_expected = request.visual_input is not None
        complete = (
            raw_audio_base64 is not None
            and analysis.decoded_audio_path is not None
            and analysis.decoded_audio_media_base64 is not None
            and (not video_expected or visual_capture.media_base64 is not None)
        )
        return ClosedBetaDebug(
            context=context,
            capture_state="COMPLETE" if complete else "PARTIAL",
            audio_object_key=request.audio_object_key,
            visual_object_key=(
                request.visual_input.object_key
                if request.visual_input is not None
                else None
            ),
            materialized_audio_path=str(audio.path),
            decoded_audio_path=analysis.decoded_audio_path,
            materialized_video_path=visual_capture.materialized_path,
            audio_media_base64=raw_audio_base64,
            decoded_audio_media_base64=analysis.decoded_audio_media_base64,
            video_media_base64=visual_capture.media_base64,
        )

    def _validate_authorization(self, authorization: AnalysisAuthorization) -> None:
        if not authorization.receipt_sha256 or not authorization.policy_revision:
            raise BackendAnalysisError(
                "consent_evidence_unavailable",
                "분석 처리 권한을 확인할 수 없습니다.",
            )
        if authorization.expires_at.tzinfo is None or authorization.expires_at <= self._clock():
            raise BackendAnalysisError(
                "consent_evidence_expired",
                "분석 처리 권한이 만료되었습니다.",
            )
        if not authorization.delete_on_completion or authorization.remote_egress_allowed:
            raise BackendAnalysisError(
                "consent_policy_not_permitted",
                "현재 처리 정책으로는 분석할 수 없습니다.",
            )

    def _failed(
        self, request: AnalysisRequest, code: str, reason: str
    ) -> AnalysisWorkerResult:
        del reason
        if code not in _SAFE_FAILURES:
            code = "analysis_execution_failed_closed"
        return AnalysisWorkerResult.failed(
            event_id=self._event_id_factory(),
            request=request,
            code=code,
            reason=_SAFE_FAILURES[code],
            worker_revision=self._worker_revision,
            closed_beta_debug=(
                ClosedBetaDebug.unavailable(
                    context=request.closed_beta_context,
                    audio_object_key=request.audio_object_key,
                    visual_object_key=(
                        request.visual_input.object_key
                        if request.visual_input is not None
                        else None
                    ),
                )
                if request.closed_beta_context is not None
                else None
            ),
        )

    def close(self) -> None:
        """Release an optional long-lived Seungun adapter without widening its port."""

        close = getattr(self._seungun, "close", None)
        if callable(close):
            close()

    def cancel_current(self) -> None:
        """Best-effort interrupt for an externally revoked request generation."""

        for component in (self._seungun, self._visual):
            cancel = getattr(component, "cancel_current", None)
            if callable(cancel):
                cancel()
