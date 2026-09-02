from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import time
from uuid import UUID, uuid4

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


redis = pytest.importorskip("redis")


class _Authorizer:
    def authorize(self, request: object) -> AnalysisAuthorization:
        del request
        return AnalysisAuthorization(
            receipt_sha256="b" * 64,
            policy_revision="voice-analysis-consent-v1",
            expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
            delete_on_completion=True,
            remote_egress_allowed=False,
        )


class _Store:
    def materialize(self, request: object, authorization: object):
        del request, authorization
        return nullcontext(
            MaterializedAudio(
                path=Path("/restricted/synthetic.wav"),
                byte_count=4,
                sha256=hashlib.sha256(b"RIFF").hexdigest(),
            )
        )


class _Seungun:
    def analyze(self, **kwargs: object) -> SeungunAnalysis:
        del kwargs
        return SeungunAnalysis(
            outcome=AnalysisOutcome.COMPLETED_NO_ISSUE,
            summary_feedback=None,
            pronunciation_evidence=None,
            pipeline_revision="synthetic-seungun-v1",
        )


class _FailResultOnce:
    def __init__(self, client: object, result_stream: str) -> None:
        self._client = client
        self._result_stream = result_stream
        self._failed = False

    def __getattr__(self, name: str) -> object:
        return getattr(self._client, name)

    def xadd(self, stream: str, fields: object) -> object:
        if stream == self._result_stream and not self._failed:
            self._failed = True
            raise ConnectionError("synthetic result publication failure")
        return self._client.xadd(stream, fields)


def _client():
    required = (
        "AI_TEST_REDIS_HOST",
        "AI_TEST_REDIS_PORT",
        "AI_TEST_REDIS_PASSWORD",
        "AI_TEST_REDIS_CA_CERT",
    )
    if any(not os.environ.get(name) for name in required):
        pytest.skip("TLS Redis integration environment is not configured")
    return redis.Redis(
        host=os.environ["AI_TEST_REDIS_HOST"],
        port=int(os.environ["AI_TEST_REDIS_PORT"]),
        password=os.environ["AI_TEST_REDIS_PASSWORD"],
        ssl=True,
        ssl_ca_certs=os.environ["AI_TEST_REDIS_CA_CERT"],
        decode_responses=False,
        socket_connect_timeout=3,
        socket_timeout=3,
    )


def _payload(event_id: UUID) -> str:
    script = "가"
    script_sha = hashlib.sha256(script.encode()).hexdigest()
    key = "recordings/synthetic.wav"
    grant = {
        "grantVersion": "voice-coaching.analysis-authorization.v2",
        "keyId": "synthetic-key-v1",
        "requestEventId": str(event_id),
        "analysisId": 35,
        "contentId": 12,
        "promptRevision": "synthetic-v1",
        "scriptSha256": script_sha,
        "audioObjectKeySha256": hashlib.sha256(key.encode()).hexdigest(),
        "audioSha256": "a" * 64,
        "mimeType": "audio/wav",
        "fileSizeBytes": 4,
        "durationMs": 100,
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
    }
    return json.dumps(
        {
            "schemaVersion": "voice-coaching.analysis-request.v3",
            "eventId": str(event_id),
            "analysisId": 35,
            "contentId": 12,
            "promptRevision": "synthetic-v1",
            "scriptText": script,
            "scriptSha256": script_sha,
            "audioObjectKey": key,
            "audioSha256": "a" * 64,
            "mimeType": "audio/wav",
            "fileSizeBytes": 4,
            "durationMs": 100,
            "learningFocus": "PRONUNCIATION",
            "authorizationGrant": grant,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _settings(suffix: str) -> RedisStreamSettings:
    return RedisStreamSettings(
        host="localhost",
        port=1,
        username=None,
        password="synthetic-only",
        ssl=True,
        request_stream=f"test:analysis:request:{suffix}",
        result_stream=f"test:analysis:result:{suffix}",
        request_dlq_stream=f"test:analysis:request:dlq:{suffix}",
        consumer_group=f"test-analysis-workers-{suffix}",
        consumer_name="worker-1",
        claim_idle_ms=1,
    )


def _worker() -> BackendAnalysisWorker:
    return BackendAnalysisWorker(
        authorization=_Authorizer(),
        object_store=_Store(),
        seungun=_Seungun(),
    )


def test_result_is_written_before_ack_and_pending_message_is_reclaimed() -> None:
    client = _client()
    settings = _settings(uuid4().hex)
    streams = [settings.request_stream, settings.result_stream, settings.request_dlq_stream]
    try:
        adapter = RedisStreamWorker(
            client=_FailResultOnce(client, settings.result_stream),
            settings=settings,
            worker=_worker(),
        )
        adapter.ensure_consumer_group()
        client.xadd(settings.request_stream, {"payload": _payload(uuid4())})

        with pytest.raises(ConnectionError):
            adapter.poll_once(block_ms=0)
        assert client.xpending(settings.request_stream, settings.consumer_group)["pending"] == 1
        assert client.xlen(settings.result_stream) == 0

        time.sleep(0.01)
        recovered = RedisStreamWorker(
            client=client,
            settings=settings,
            worker=_worker(),
        )
        assert recovered.poll_once(block_ms=0) == 1
        assert client.xpending(settings.request_stream, settings.consumer_group)["pending"] == 0
        assert client.xlen(settings.result_stream) == 1
        result = json.loads(client.xrange(settings.result_stream)[0][1][b"payload"])
        assert result["status"] == "COMPLETED"
        assert result["outcome"] == "COMPLETED_NO_ISSUE"
    finally:
        client.delete(*streams)


def test_malformed_request_goes_to_dlq_and_is_acked() -> None:
    client = _client()
    settings = _settings(uuid4().hex)
    streams = [settings.request_stream, settings.result_stream, settings.request_dlq_stream]
    try:
        adapter = RedisStreamWorker(client=client, settings=settings, worker=_worker())
        adapter.ensure_consumer_group()
        client.xadd(settings.request_stream, {"payload": "{}"})

        assert adapter.poll_once(block_ms=0) == 1
        assert client.xpending(settings.request_stream, settings.consumer_group)["pending"] == 0
        assert client.xlen(settings.result_stream) == 0
        entry = client.xrange(settings.request_dlq_stream)[0][1]
        assert entry[b"failureCode"] == b"analysis_request_contract_invalid"
    finally:
        client.delete(*streams)
