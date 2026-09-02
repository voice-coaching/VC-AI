"""Fail-closed orchestration for one VC-BE stream request."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from .domain import AnalysisRequest, AnalysisWorkerResult, VisualSupplement
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


_WORKER_REVISION = "vc-be-redis-worker-v1"
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
        authorization: AnalysisAuthorizationPort,
        object_store: RestrictedObjectStorePort,
        seungun: SeungunAnalysisPort,
        visual: VisualAnalysisPort | None = None,
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
                analysis = self._seungun.analyze(request=request, audio=audio)
                visual_supplement = self._visual_supplement(
                    request=request,
                    authorization=authorization,
                    audio=audio,
                    analysis=analysis,
                )
                return AnalysisWorkerResult.completed(
                    event_id=self._event_id_factory(),
                    request=request,
                    outcome=analysis.outcome,
                    summary_feedback=analysis.summary_feedback,
                    pronunciation_evidence=analysis.pronunciation_evidence,
                    worker_revision=self._worker_revision,
                    pipeline_revision=analysis.pipeline_revision,
                    audio_sha256=audio.sha256,
                    visual_supplement=visual_supplement,
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
    ) -> VisualSupplement | None:
        if request.visual_input is None or self._visual is None:
            return None
        try:
            with self._object_store.materialize_visual(
                request, authorization
            ) as video:
                return self._visual.analyze(
                    request=request,
                    audio=audio,
                    video=video,
                    seungun=analysis,
                )
        except Exception:
            # Visual evidence is optional and cannot erase an approved Seungun result.
            return None

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
