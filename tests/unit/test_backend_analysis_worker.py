from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
from uuid import UUID

import pytest

from voice_coach.backend_analysis.domain import (
    AnalysisOutcome,
    AnalysisRequest,
    BackendAnalysisContractError,
    PronunciationEvidence,
)
from voice_coach.backend_analysis.ports import (
    AnalysisAuthorization,
    BackendAnalysisError,
    MaterializedAudio,
    SeungunAnalysis,
)
from voice_coach.backend_analysis.service import BackendAnalysisWorker


_NOW = datetime(2026, 9, 2, tzinfo=UTC)
_EVENT_ID = UUID("4adfe173-0691-4e89-b94e-a5c5c5085826")
_RESULT_EVENT_ID = UUID("e917fda8-3c4f-4b7e-9094-7a1706081f1b")


def _request_payload(**overrides: object) -> dict[str, object]:
    script = "안녕하세요. 오늘 날씨가 좋습니다."
    payload: dict[str, object] = {
        "schemaVersion": "voice-coaching.analysis-request.v3",
        "eventId": str(_EVENT_ID),
        "analysisId": 35,
        "contentId": 12,
        "promptRevision": "2026-09-02T00:00:00Z",
        "scriptText": script,
        "scriptSha256": hashlib.sha256(script.encode("utf-8")).hexdigest(),
        "audioObjectKey": "recordings/50.wav",
        "audioSha256": "a" * 64,
        "mimeType": "audio/wav",
        "fileSizeBytes": 1234,
        "durationMs": 1200,
        "learningFocus": "PRONUNCIATION",
        "authorizationGrant": {
            "grantVersion": "voice-coaching.analysis-authorization.v2",
            "keyId": "backend-test-v1",
            "requestEventId": str(_EVENT_ID),
            "analysisId": 35,
            "contentId": 12,
            "promptRevision": "2026-09-02T00:00:00Z",
            "scriptSha256": hashlib.sha256(script.encode("utf-8")).hexdigest(),
            "audioObjectKeySha256": hashlib.sha256(b"recordings/50.wav").hexdigest(),
            "audioSha256": "a" * 64,
            "mimeType": "audio/wav",
            "fileSizeBytes": 1234,
            "durationMs": 1200,
            "learningFocus": "PRONUNCIATION",
            "consentReceiptSha256": "b" * 64,
            "consentPolicyRevision": "voice-analysis-consent-v1",
            "issuedAtUtc": "2026-09-02T00:00:00Z",
            "expiresAtUtc": "2026-09-02T00:05:00Z",
            "purpose": "pronunciation_coaching",
            "dataCategory": "learner_voice_recording",
            "deleteOnCompletion": True,
            "remoteEgressAllowed": False,
            "signature": "c" * 64,
        },
    }
    payload.update(overrides)
    return payload


class _Authorizer:
    def __init__(self, result: AnalysisAuthorization | Exception) -> None:
        self.result = result
        self.calls = 0

    def authorize(self, request: AnalysisRequest) -> AnalysisAuthorization:
        del request
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _ObjectStore:
    def __init__(self) -> None:
        self.calls = 0

    @contextmanager
    def materialize(
        self, request: AnalysisRequest, authorization: AnalysisAuthorization
    ):
        del request, authorization
        self.calls += 1
        yield MaterializedAudio(
            path=Path("/restricted/attempt.wav"),
            byte_count=1234,
            sha256="a" * 64,
        )


class _Seungun:
    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, *, request: AnalysisRequest, audio: MaterializedAudio) -> SeungunAnalysis:
        del request, audio
        self.calls += 1
        return SeungunAnalysis(
            outcome=AnalysisOutcome.COACHING_READY,
            summary_feedback="승인된 Seungun 근거에 따른 피드백입니다.",
            pronunciation_evidence=PronunciationEvidence(
                selected_phone="ㄱ",
                selected_expected_index=0,
                selected_start_ms=100,
                selected_end_ms=200,
                detector_score=0.91,
                operating_threshold=0.8,
                score_semantics=(
                    "detector_ranking_score_not_calibrated_correctness_confidence"
                ),
                evidence_state="frozen_detector_threshold_passed",
            ),
            pipeline_revision="seungun-locked-v1",
        )


def _authorization(**overrides: object) -> AnalysisAuthorization:
    values: dict[str, object] = {
        "receipt_sha256": "b" * 64,
        "policy_revision": "consent-policy-v1",
        "expires_at": _NOW + timedelta(minutes=5),
        "delete_on_completion": True,
        "remote_egress_allowed": False,
    }
    values.update(overrides)
    return AnalysisAuthorization(**values)


def _worker(authorizer: _Authorizer, store: _ObjectStore, seungun: _Seungun) -> BackendAnalysisWorker:
    return BackendAnalysisWorker(
        authorization=authorizer,
        object_store=store,
        seungun=seungun,
        event_id_factory=lambda: _RESULT_EVENT_ID,
        clock=lambda: _NOW,
    )


def test_rejects_tampered_script_before_authorization_or_media_access() -> None:
    payload = _request_payload(scriptText="다른 문장입니다.")

    with pytest.raises(BackendAnalysisContractError) as error:
        AnalysisRequest.from_payload(payload)

    assert error.value.code == "analysis_request_script_integrity_invalid"


def test_refuses_unknown_request_fields() -> None:
    payload = _request_payload(userId=99)

    with pytest.raises(BackendAnalysisContractError) as error:
        AnalysisRequest.from_payload(payload)

    assert error.value.code == "analysis_request_contract_invalid"


def test_consent_failure_never_materializes_audio() -> None:
    request = AnalysisRequest.from_payload(_request_payload())
    authorizer = _Authorizer(
        BackendAnalysisError(
            "consent_evidence_unavailable",
            "분석 처리 권한을 확인할 수 없습니다.",
        )
    )
    store = _ObjectStore()
    seungun = _Seungun()

    result = _worker(authorizer, store, seungun).process(request).to_payload()

    assert result["status"] == "FAILED"
    assert result["failureCode"] == "consent_evidence_unavailable"
    assert result["audioSha256"] is None
    assert store.calls == 0
    assert seungun.calls == 0


def test_expired_authorization_never_materializes_audio() -> None:
    request = AnalysisRequest.from_payload(_request_payload())
    authorizer = _Authorizer(_authorization(expires_at=_NOW - timedelta(seconds=1)))
    store = _ObjectStore()
    seungun = _Seungun()

    result = _worker(authorizer, store, seungun).process(request).to_payload()

    assert result["status"] == "FAILED"
    assert result["failureCode"] == "consent_evidence_expired"
    assert store.calls == 0
    assert seungun.calls == 0


@pytest.mark.parametrize("focus", ["INTONATION", "BOTH"])
def test_unsupported_focus_never_materializes_audio(focus: str) -> None:
    payload = _request_payload(learningFocus=focus)
    payload["authorizationGrant"]["learningFocus"] = focus  # type: ignore[index]
    request = AnalysisRequest.from_payload(payload)
    authorizer = _Authorizer(_authorization())
    store = _ObjectStore()
    seungun = _Seungun()

    result = _worker(authorizer, store, seungun).process(request).to_payload()

    assert result["failureCode"] == "analysis_focus_not_supported"
    assert store.calls == 0
    assert seungun.calls == 0


def test_adapter_reason_is_not_forwarded_without_an_allowlisted_code() -> None:
    request = AnalysisRequest.from_payload(_request_payload())
    authorizer = _Authorizer(_authorization())
    store = _ObjectStore()
    seungun = _Seungun()

    class _FailingStore:
        def materialize(self, request: object, authorization: object):
            del request, authorization
            raise BackendAnalysisError("storage_response_500", "internal endpoint detail")

    result = _worker(authorizer, _FailingStore(), seungun).process(request).to_payload()

    assert result["failureCode"] == "analysis_execution_failed_closed"
    assert result["failureReason"] == "분석을 안전하게 완료하지 못했습니다. 다시 시도해 주세요."


def test_seungun_completed_result_keeps_unmeasured_scores_null() -> None:
    request = AnalysisRequest.from_payload(_request_payload())
    authorizer = _Authorizer(_authorization())
    store = _ObjectStore()
    seungun = _Seungun()

    result = _worker(authorizer, store, seungun).process(request).to_payload()

    assert result["eventId"] == str(_RESULT_EVENT_ID)
    assert result["requestEventId"] == str(_EVENT_ID)
    assert set(result) == {
        "schemaVersion",
        "eventId",
        "requestEventId",
        "analysisId",
        "status",
        "outcome",
        "failureCode",
        "failureReason",
        "transcript",
        "sttConfidence",
        "sttModelName",
        "overallScore",
        "pronunciationScore",
        "intonationScore",
        "speedWpm",
        "speedStatus",
        "stressScore",
        "pauseScore",
        "strengthsText",
        "weaknessesText",
        "summaryFeedback",
        "pronunciationEvidence",
        "workerRevision",
        "pipelineRevision",
        "audioSha256",
        "segments",
    }
    assert result["status"] == "COMPLETED"
    assert result["outcome"] == "COACHING_READY"
    assert result["summaryFeedback"] == "승인된 Seungun 근거에 따른 피드백입니다."
    assert result["pronunciationEvidence"] == {
        "schemaVersion": "voice-coaching.pronunciation-evidence.v1",
        "selectedPhone": "ㄱ",
        "selectedExpectedIndex": 0,
        "selectedStartMs": 100,
        "selectedEndMs": 200,
        "detectorScore": 0.91,
        "operatingThreshold": 0.8,
        "scoreSemantics": (
            "detector_ranking_score_not_calibrated_correctness_confidence"
        ),
        "evidenceState": "frozen_detector_threshold_passed",
    }
    assert result["overallScore"] is None
    assert result["pronunciationScore"] is None
    assert result["segments"] == []
    assert store.calls == 1
    assert seungun.calls == 1
