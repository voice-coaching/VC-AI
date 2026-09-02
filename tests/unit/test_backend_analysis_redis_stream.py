from __future__ import annotations

import hashlib
import json
import threading
from uuid import UUID

import pytest

from voice_coach.backend_analysis.adapters.redis_stream import (
    RedisStreamSettings,
    RedisStreamWorker,
)
from voice_coach.backend_analysis.domain import AnalysisOutcome
from voice_coach.backend_analysis.ports import (
    AnalysisAuthorization,
    MaterializedAudio,
    SeungunAnalysis,
)
from voice_coach.backend_analysis.service import BackendAnalysisWorker


class _FakeRedis:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.added: list[tuple[object, object, dict[str, object]]] = []
        self.acked: list[tuple[object, object, object]] = []
        self.claimed: list[tuple[object, object, object, object]] = []
        self.cancelled_keys: set[str] = set()
        self.exists_calls = 0
        self.cancel_after_exists_calls: int | None = None
        self.cancel_on_eval = False

    def xgroup_create(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        return True

    def xreadgroup(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        return self.responses.pop(0) if self.responses else []

    def xautoclaim(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        return (b"0-0", [], [])

    def xadd(self, stream: object, fields: object, **kwargs: object) -> object:
        self.added.append((stream, fields, kwargs))
        return b"1-0"

    def xack(self, stream: object, group: object, message_id: object) -> object:
        self.acked.append((stream, group, message_id))
        return 1

    def xclaim(
        self,
        stream: object,
        group: object,
        consumer: object,
        minimum_idle: object,
        message_ids: list[object],
        **kwargs: object,
    ) -> object:
        del kwargs
        self.claimed.append((stream, group, consumer, minimum_idle))
        return message_ids

    def exists(self, key: object) -> int:
        self.exists_calls += 1
        if (
            self.cancel_after_exists_calls is not None
            and self.exists_calls >= self.cancel_after_exists_calls
        ):
            self.cancelled_keys.add(str(key))
        return int(str(key) in self.cancelled_keys)

    def eval(self, script: object, key_count: object, *args: object) -> int:
        del script
        assert key_count == 2
        cancellation_key, result_stream, payload = args
        if self.cancel_on_eval:
            self.cancelled_keys.add(str(cancellation_key))
        if str(cancellation_key) in self.cancelled_keys:
            return 0
        self.added.append((result_stream, {"payload": payload}, {}))
        return 1


class _LeaseLostRedis(_FakeRedis):
    def xclaim(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        return []


class _Authorizer:
    def authorize(self, request: object) -> AnalysisAuthorization:
        del request
        from datetime import UTC, datetime, timedelta

        return AnalysisAuthorization(
            receipt_sha256="b" * 64,
            policy_revision="consent-policy-v1",
            expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
            delete_on_completion=True,
            remote_egress_allowed=False,
        )


class _Store:
    def materialize(self, request: object, authorization: object):
        del request, authorization
        from contextlib import nullcontext
        from pathlib import Path

        return nullcontext(
            MaterializedAudio(
                path=Path("/restricted/attempt.wav"),
                byte_count=1234,
                sha256="a" * 64,
            )
        )


class _Seungun:
    def analyze(self, **kwargs: object) -> SeungunAnalysis:
        del kwargs
        return SeungunAnalysis(
            outcome=AnalysisOutcome.COMPLETED_NO_ISSUE,
            summary_feedback=None,
            pronunciation_evidence=None,
            pipeline_revision="seungun-locked-v1",
        )


def _settings() -> RedisStreamSettings:
    return RedisStreamSettings(
        host="redis.private",
        port=6380,
        username="worker",
        password="not-printed",
        ssl=True,
        request_stream="analysis:request:v1",
        result_stream="analysis:result:v1",
        request_dlq_stream="analysis:request:dlq:v1",
        consumer_group="analysis-workers",
        consumer_name="worker-1",
        claim_idle_ms=300_000,
    )


def _payload() -> bytes:
    script = "안녕하세요. 오늘 날씨가 좋습니다."
    return json.dumps(
        {
            "schemaVersion": "voice-coaching.analysis-request.v4",
            "eventId": "4adfe173-0691-4e89-b94e-a5c5c5085826",
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
                "grantVersion": "voice-coaching.analysis-authorization.v3",
                "keyId": "backend-test-v1",
                "requestEventId": "4adfe173-0691-4e89-b94e-a5c5c5085826",
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
                "visualObjectKeySha256": None,
                "visualSha256": None,
                "visualMimeType": None,
                "visualFileSizeBytes": None,
                "visualConsentReceiptSha256": None,
                "visualConsentPolicyRevision": None,
                "issuedAtUtc": "2026-09-02T00:00:00Z",
                "expiresAtUtc": "2026-09-02T00:05:00Z",
                "purpose": "pronunciation_coaching",
                "dataCategory": "learner_voice_recording",
                "deleteOnCompletion": True,
                "remoteEgressAllowed": False,
                "signature": "c" * 64,
            },
            "visualInput": None,
        },
        ensure_ascii=False,
    ).encode("utf-8")


def _worker() -> BackendAnalysisWorker:
    return BackendAnalysisWorker(
        authorization=_Authorizer(),
        object_store=_Store(),
        seungun=_Seungun(),
        event_id_factory=lambda: UUID("e917fda8-3c4f-4b7e-9094-7a1706081f1b"),
    )


def test_terminal_result_is_published_before_request_is_acknowledged() -> None:
    client = _FakeRedis([[[b"analysis:request:v1", [[b"1-0", {b"payload": _payload()}]]]]])
    adapter = RedisStreamWorker(client=client, settings=_settings(), worker=_worker())

    assert adapter.poll_once(block_ms=0) == 1

    assert client.added[0][0] == "analysis:result:v1"
    result = json.loads(client.added[0][1]["payload"])
    assert result["status"] == "COMPLETED"
    assert client.claimed == [
        ("analysis:request:v1", "analysis-workers", "worker-1", 0)
    ]
    assert client.acked == [("analysis:request:v1", "analysis-workers", b"1-0")]


def test_malformed_request_is_dead_lettered_then_acknowledged() -> None:
    client = _FakeRedis([[(b"analysis:request:v1", [(b"1-0", {b"payload": b"not-json"})])]])
    adapter = RedisStreamWorker(client=client, settings=_settings(), worker=_worker())

    assert adapter.poll_once(block_ms=0) == 1

    assert client.added[0][0] == "analysis:request:dlq:v1"
    assert client.added[0][1]["failureCode"] == "analysis_request_contract_invalid"
    assert client.added[0][2] == {"maxlen": 10_000, "approximate": True}
    assert client.acked == [("analysis:request:v1", "analysis-workers", b"1-0")]


def test_oversized_request_is_not_copied_into_the_bounded_dlq() -> None:
    settings = _settings()
    client = _FakeRedis(
        [[(b"analysis:request:v1", [(b"1-0", {b"payload": b"x" * 70_000})])]]
    )
    adapter = RedisStreamWorker(client=client, settings=settings, worker=_worker())

    assert adapter.poll_once(block_ms=0) == 1

    assert client.added == [
        (
            "analysis:request:dlq:v1",
            {
                "sourceStreamId": "1-0",
                "failureCode": "analysis_request_payload_too_large",
            },
            {"maxlen": 10_000, "approximate": True},
        )
    ]
    assert client.acked == [("analysis:request:v1", "analysis-workers", b"1-0")]


def test_lost_processing_lease_never_publishes_or_acknowledges_result() -> None:
    client = _LeaseLostRedis(
        [[(b"analysis:request:v1", [(b"1-0", {b"payload": _payload()})])]]
    )
    adapter = RedisStreamWorker(client=client, settings=_settings(), worker=_worker())

    with pytest.raises(RuntimeError, match="analysis_request_lease_lost"):
        adapter.poll_once(block_ms=0)

    assert client.added == []
    assert client.acked == []


def test_existing_cancellation_tombstone_skips_work_and_acknowledges_request() -> None:
    client = _FakeRedis([[(b"analysis:request:v1", [(b"1-0", {b"payload": _payload()})])]])
    client.cancelled_keys.add(
        "analysis:canceled:v1:4adfe173-0691-4e89-b94e-a5c5c5085826"
    )
    adapter = RedisStreamWorker(client=client, settings=_settings(), worker=_worker())

    assert adapter.poll_once(block_ms=0) == 1

    assert client.added == []
    assert client.acked == [("analysis:request:v1", "analysis-workers", b"1-0")]


def test_atomic_publish_suppresses_result_when_cancellation_wins_the_race() -> None:
    client = _FakeRedis([[(b"analysis:request:v1", [(b"1-0", {b"payload": _payload()})])]])
    client.cancel_on_eval = True
    adapter = RedisStreamWorker(client=client, settings=_settings(), worker=_worker())

    assert adapter.poll_once(block_ms=0) == 1

    assert client.added == []
    assert client.acked == [("analysis:request:v1", "analysis-workers", b"1-0")]


def test_running_analysis_is_interrupted_after_tombstone_arrives() -> None:
    class _CancellableWorker:
        def __init__(self) -> None:
            self.cancelled = threading.Event()

        def process(self, request: object):
            assert self.cancelled.wait(timeout=2)
            return _worker().process(request)  # type: ignore[arg-type]

        def cancel_current(self) -> None:
            self.cancelled.set()

    settings = _settings()
    settings.cancellation_poll_interval_seconds = 0.05
    client = _FakeRedis([[(b"analysis:request:v1", [(b"1-0", {b"payload": _payload()})])]])
    client.cancel_after_exists_calls = 2
    worker = _CancellableWorker()
    adapter = RedisStreamWorker(client=client, settings=settings, worker=worker)  # type: ignore[arg-type]

    assert adapter.poll_once(block_ms=0) == 1

    assert worker.cancelled.is_set()
    assert client.added == []
    assert client.acked == [("analysis:request:v1", "analysis-workers", b"1-0")]
