"""Redis Streams adapter for the versioned VC-BE analysis contract.

``redis`` is intentionally imported only when a deployment composes this
adapter.  Unit tests and the default research API remain free of a Redis client
or external network dependency.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

from ..domain import AnalysisRequest, BackendAnalysisContractError
from ..service import BackendAnalysisWorker


class WorkerConfigurationError(RuntimeError):
    """Safe configuration failure; never includes an endpoint or secret."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _RedisClient(Protocol):
    def xgroup_create(self, *args: object, **kwargs: object) -> object: ...

    def xreadgroup(self, *args: object, **kwargs: object) -> object: ...

    def xautoclaim(self, *args: object, **kwargs: object) -> object: ...

    def xadd(self, *args: object, **kwargs: object) -> object: ...

    def xack(self, *args: object, **kwargs: object) -> object: ...


class RedisStreamSettings:
    """Environment-only Stream configuration with no URL-shaped secret."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str,
        ssl: bool,
        request_stream: str,
        result_stream: str,
        request_dlq_stream: str,
        consumer_group: str,
        consumer_name: str,
        claim_idle_ms: int,
        ca_cert_path: str | None = None,
    ) -> None:
        if (
            not host
            or not password
            or not ssl
            or not request_stream
            or not result_stream
            or not request_dlq_stream
            or not consumer_group
            or not consumer_name
            or not 1 <= port <= 65_535
            or claim_idle_ms <= 0
            or (
                ca_cert_path is not None
                and (
                    not Path(ca_cert_path).is_absolute()
                    or not Path(ca_cert_path).is_file()
                )
            )
        ):
            raise WorkerConfigurationError("analysis_stream_configuration_invalid")
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.ssl = ssl
        self.request_stream = request_stream
        self.result_stream = result_stream
        self.request_dlq_stream = request_dlq_stream
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name
        self.claim_idle_ms = claim_idle_ms
        self.ca_cert_path = ca_cert_path

    @classmethod
    def from_environment(cls, environment: dict[str, str] | None = None) -> "RedisStreamSettings":
        env = os.environ if environment is None else environment
        try:
            port = int(env["AI_ANALYSIS_REDIS_PORT"])
            claim_idle_ms = int(env.get("AI_ANALYSIS_PENDING_CLAIM_IDLE_MS", "300000"))
        except (KeyError, ValueError):
            raise WorkerConfigurationError("analysis_stream_configuration_invalid") from None
        return cls(
            host=env.get("AI_ANALYSIS_REDIS_HOST", ""),
            port=port,
            username=env.get("AI_ANALYSIS_REDIS_USERNAME") or None,
            password=env.get("AI_ANALYSIS_REDIS_PASSWORD", ""),
            ssl=env.get("AI_ANALYSIS_REDIS_SSL_ENABLED", "").lower() == "true",
            request_stream=env.get("AI_ANALYSIS_REQUEST_STREAM", "analysis:request:v1"),
            result_stream=env.get("AI_ANALYSIS_RESULT_STREAM", "analysis:result:v1"),
            request_dlq_stream=env.get("AI_ANALYSIS_REQUEST_DLQ_STREAM", "analysis:request:dlq:v1"),
            consumer_group=env.get("AI_ANALYSIS_REQUEST_CONSUMER_GROUP", "analysis-workers"),
            consumer_name=env.get("AI_ANALYSIS_REQUEST_CONSUMER_NAME", ""),
            claim_idle_ms=claim_idle_ms,
            ca_cert_path=env.get("AI_ANALYSIS_REDIS_CA_CERT") or None,
        )


def create_redis_client(settings: RedisStreamSettings) -> _RedisClient:
    """Create a TLS Redis client, without logging any connection material."""

    try:
        import redis  # type: ignore[import-not-found]
    except ImportError:
        raise WorkerConfigurationError("redis_client_dependency_unavailable") from None
    options: dict[str, object] = {
        "host": settings.host,
        "port": settings.port,
        "username": settings.username,
        "password": settings.password,
        "ssl": True,
        "decode_responses": False,
        "socket_connect_timeout": 5,
        "socket_timeout": 30,
        "health_check_interval": 30,
    }
    if settings.ca_cert_path is not None:
        options["ssl_ca_certs"] = settings.ca_cert_path
    return redis.Redis(**options)


class RedisStreamWorker:
    """Consume one-or-more requests and ACK only after a terminal publication."""

    def __init__(
        self,
        *,
        client: _RedisClient,
        settings: RedisStreamSettings,
        worker: BackendAnalysisWorker,
    ) -> None:
        self._client = client
        self._settings = settings
        self._worker = worker

    def ensure_consumer_group(self) -> None:
        try:
            self._client.xgroup_create(
                self._settings.request_stream,
                self._settings.consumer_group,
                id="0-0",
                mkstream=True,
            )
        except Exception as error:
            if "BUSYGROUP" not in str(error):
                raise WorkerConfigurationError("analysis_stream_group_unavailable") from None

    def poll_once(self, *, block_ms: int = 1_000, count: int = 1) -> int:
        """Process pending reclaim first, then fresh entries; returns ACK count."""

        if block_ms < 0 or count <= 0:
            raise ValueError("block_ms must be non-negative and count must be positive")
        delivered = self._claim_idle(count=count)
        if delivered >= count:
            return delivered
        response = self._client.xreadgroup(
            self._settings.consumer_group,
            self._settings.consumer_name,
            {self._settings.request_stream: ">"},
            count=count - delivered,
            block=block_ms,
        )
        for _stream, entries in _stream_entries(response):
            for message_id, fields in entries:
                self._publish_then_ack(message_id, fields)
                delivered += 1
        return delivered

    def _claim_idle(self, *, count: int) -> int:
        try:
            response = self._client.xautoclaim(
                self._settings.request_stream,
                self._settings.consumer_group,
                self._settings.consumer_name,
                self._settings.claim_idle_ms,
                "0-0",
                count=count,
            )
        except Exception:
            # A transient reclaim failure must not ACK or drop an entry. Fresh
            # reads remain safe; a later poll will retry the PEL claim.
            return 0
        entries = _claimed_entries(response)
        for message_id, fields in entries:
            self._publish_then_ack(message_id, fields)
        return len(entries)

    def _publish_then_ack(self, message_id: object, fields: object) -> None:
        payload = _payload_field(fields)
        if payload is None:
            self._publish_request_dlq(message_id, None, "analysis_request_payload_missing")
        else:
            try:
                decoded = json.loads(payload.decode("utf-8", errors="strict"))
                request = AnalysisRequest.from_payload(decoded)
            except (UnicodeDecodeError, json.JSONDecodeError, BackendAnalysisContractError):
                self._publish_request_dlq(
                    message_id,
                    payload,
                    "analysis_request_contract_invalid",
                )
            else:
                result = self._worker.process(request)
                encoded = json.dumps(
                    result.to_payload(),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                self._client.xadd(self._settings.result_stream, {"payload": encoded})
        self._client.xack(
            self._settings.request_stream,
            self._settings.consumer_group,
            message_id,
        )

    def _publish_request_dlq(
        self, message_id: object, payload: bytes | None, failure_code: str
    ) -> None:
        fields: dict[str, object] = {
            "sourceStreamId": _text(message_id),
            "failureCode": failure_code,
        }
        if payload is not None:
            fields["payload"] = payload
        self._client.xadd(self._settings.request_dlq_stream, fields)


def _payload_field(fields: object) -> bytes | None:
    if not isinstance(fields, dict) or set(fields) != {b"payload"}:
        return None
    payload = fields[b"payload"]
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return payload if isinstance(payload, bytes) else None


def _stream_entries(response: object) -> tuple[tuple[object, tuple[tuple[object, object], ...]], ...]:
    if not isinstance(response, list):
        return ()
    converted: list[tuple[object, tuple[tuple[object, object], ...]]] = []
    for item in response:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        stream, entries = item
        if not isinstance(entries, list):
            continue
        accepted = tuple(
            (entry[0], entry[1])
            for entry in entries
            if isinstance(entry, (list, tuple)) and len(entry) == 2
        )
        converted.append((stream, accepted))
    return tuple(converted)


def _claimed_entries(response: object) -> tuple[tuple[object, object], ...]:
    if not isinstance(response, (list, tuple)) or len(response) < 2:
        return ()
    entries = response[1]
    if not isinstance(entries, list):
        return ()
    return tuple(
        (entry[0], entry[1])
        for entry in entries
        if isinstance(entry, (list, tuple)) and len(entry) == 2
    )


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("ascii", errors="replace")
    return str(value)
