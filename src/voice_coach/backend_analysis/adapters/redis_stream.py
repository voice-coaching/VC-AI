"""Redis Streams adapter for the versioned VC-BE analysis contract.

``redis`` is intentionally imported only when a deployment composes this
adapter.  Unit tests and the default research API remain free of a Redis client
or external network dependency.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import threading
from typing import Protocol

from ..domain import AnalysisRequest, BackendAnalysisContractError
from ..secret_files import secret_text
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

    def xclaim(self, *args: object, **kwargs: object) -> object: ...

    def exists(self, *args: object, **kwargs: object) -> object: ...

    def eval(self, *args: object, **kwargs: object) -> object: ...


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
        socket_connect_timeout_seconds: float = 5.0,
        socket_timeout_seconds: float = 30.0,
        health_check_interval_seconds: int = 30,
        maximum_request_payload_bytes: int = 64 * 1024,
        maximum_dlq_entries: int = 10_000,
        processing_heartbeat_interval_seconds: float = 60.0,
        cancellation_key_prefix: str = "analysis:canceled:v1:",
        cancellation_poll_interval_seconds: float = 0.25,
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
            or not math.isfinite(socket_connect_timeout_seconds)
            or not 0 < socket_connect_timeout_seconds <= 30
            or not math.isfinite(socket_timeout_seconds)
            or not 1 <= socket_timeout_seconds <= 300
            or not 1 <= health_check_interval_seconds <= 300
            or not 1_024 <= maximum_request_payload_bytes <= 1024 * 1024
            or not 1 <= maximum_dlq_entries <= 1_000_000
            or not math.isfinite(processing_heartbeat_interval_seconds)
            or not 0.1 <= processing_heartbeat_interval_seconds <= 300
            or processing_heartbeat_interval_seconds * 1000 >= claim_idle_ms
            or not cancellation_key_prefix.endswith(":")
            or len(cancellation_key_prefix) > 100
            or any(
                character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:_-"
                for character in cancellation_key_prefix
            )
            or not math.isfinite(cancellation_poll_interval_seconds)
            or not 0.05 <= cancellation_poll_interval_seconds <= 5
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
        self.socket_connect_timeout_seconds = socket_connect_timeout_seconds
        self.socket_timeout_seconds = socket_timeout_seconds
        self.health_check_interval_seconds = health_check_interval_seconds
        self.maximum_request_payload_bytes = maximum_request_payload_bytes
        self.maximum_dlq_entries = maximum_dlq_entries
        self.processing_heartbeat_interval_seconds = processing_heartbeat_interval_seconds
        self.cancellation_key_prefix = cancellation_key_prefix
        self.cancellation_poll_interval_seconds = cancellation_poll_interval_seconds

    @classmethod
    def from_environment(cls, environment: dict[str, str] | None = None) -> "RedisStreamSettings":
        env = os.environ if environment is None else environment
        try:
            port = int(env["AI_ANALYSIS_REDIS_PORT"])
            claim_idle_ms = int(env.get("AI_ANALYSIS_PENDING_CLAIM_IDLE_MS", "300000"))
            connect_timeout = float(
                env.get("AI_ANALYSIS_REDIS_CONNECT_TIMEOUT_SECONDS", "5")
            )
            socket_timeout = float(
                env.get("AI_ANALYSIS_REDIS_SOCKET_TIMEOUT_SECONDS", "30")
            )
            health_interval = int(
                env.get("AI_ANALYSIS_REDIS_HEALTH_CHECK_INTERVAL_SECONDS", "30")
            )
            maximum_payload = int(
                env.get("AI_ANALYSIS_MAXIMUM_REQUEST_PAYLOAD_BYTES", str(64 * 1024))
            )
            maximum_dlq_entries = int(
                env.get("AI_ANALYSIS_MAXIMUM_REQUEST_DLQ_ENTRIES", "10000")
            )
            processing_heartbeat_interval = float(
                env.get("AI_ANALYSIS_PROCESSING_HEARTBEAT_INTERVAL_SECONDS", "60")
            )
            cancellation_poll_interval = float(
                env.get("AI_ANALYSIS_CANCELLATION_POLL_INTERVAL_SECONDS", "0.25")
            )
            password = secret_text(
                env,
                value_name="AI_ANALYSIS_REDIS_PASSWORD",
                file_name="AI_ANALYSIS_REDIS_PASSWORD_FILE",
                maximum_bytes=4 * 1024,
            )
        except (KeyError, ValueError):
            raise WorkerConfigurationError("analysis_stream_configuration_invalid") from None
        return cls(
            host=env.get("AI_ANALYSIS_REDIS_HOST", ""),
            port=port,
            username=env.get("AI_ANALYSIS_REDIS_USERNAME") or None,
            password=password,
            ssl=env.get("AI_ANALYSIS_REDIS_SSL_ENABLED", "").lower() == "true",
            request_stream=env.get("AI_ANALYSIS_REQUEST_STREAM", "analysis:request:v1"),
            result_stream=env.get("AI_ANALYSIS_RESULT_STREAM", "analysis:result:v1"),
            request_dlq_stream=env.get("AI_ANALYSIS_REQUEST_DLQ_STREAM", "analysis:request:dlq:v1"),
            consumer_group=env.get("AI_ANALYSIS_REQUEST_CONSUMER_GROUP", "analysis-workers"),
            consumer_name=env.get("AI_ANALYSIS_REQUEST_CONSUMER_NAME", ""),
            claim_idle_ms=claim_idle_ms,
            ca_cert_path=env.get("AI_ANALYSIS_REDIS_CA_CERT") or None,
            socket_connect_timeout_seconds=connect_timeout,
            socket_timeout_seconds=socket_timeout,
            health_check_interval_seconds=health_interval,
            maximum_request_payload_bytes=maximum_payload,
            maximum_dlq_entries=maximum_dlq_entries,
            processing_heartbeat_interval_seconds=processing_heartbeat_interval,
            cancellation_key_prefix=env.get(
                "AI_ANALYSIS_CANCELLATION_KEY_PREFIX", "analysis:canceled:v1:"
            ),
            cancellation_poll_interval_seconds=cancellation_poll_interval,
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
        "socket_connect_timeout": settings.socket_connect_timeout_seconds,
        "socket_timeout": settings.socket_timeout_seconds,
        "health_check_interval": settings.health_check_interval_seconds,
        "max_connections": 4,
        "ssl_cert_reqs": "required",
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
        cancellation_key: str | None = None
        payload = _payload_field(fields)
        if payload is None:
            self._publish_request_dlq(message_id, None, "analysis_request_payload_missing")
        elif len(payload) > self._settings.maximum_request_payload_bytes:
            self._publish_request_dlq(
                message_id,
                None,
                "analysis_request_payload_too_large",
            )
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
                cancellation = _CancellationMonitor(
                    client=self._client,
                    settings=self._settings,
                    request_event_id=str(request.event_id),
                    cancel_current=self._worker.cancel_current,
                )
                cancellation_key = cancellation.key
                result = None
                with _ProcessingLease(
                    client=self._client,
                    settings=self._settings,
                    message_id=message_id,
                ) as lease, cancellation:
                    if not cancellation.cancelled:
                        result = self._worker.process(request)
                    lease.refresh_now()
                    cancellation.refresh_now()
                if result is not None and not cancellation.cancelled:
                    encoded = json.dumps(
                        result.to_payload(),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode("utf-8")
                    self._publish_result_if_active(cancellation.key, encoded)
        self._acknowledge_and_delete(message_id, cancellation_key)

    def _acknowledge_and_delete(
        self, message_id: object, cancellation_key: str | None
    ) -> None:
        acknowledged = self._client.eval(
            _ACKNOWLEDGE_AND_DELETE_SCRIPT,
            1,
            self._settings.request_stream,
            self._settings.consumer_group,
            message_id,
            cancellation_key or "",
        )
        if acknowledged != 1:
            raise RuntimeError("analysis_request_acknowledgement_unconfirmed")

    def _publish_result_if_active(self, cancellation_key: str, payload: bytes) -> bool:
        published = self._client.eval(
            _PUBLISH_RESULT_IF_ACTIVE_SCRIPT,
            2,
            cancellation_key,
            self._settings.result_stream,
            payload,
        )
        if published == 1:
            return True
        if published == 0:
            self._worker.cancel_current()
            return False
        raise RuntimeError("analysis_result_atomic_publish_failed")

    def _publish_request_dlq(
        self, message_id: object, payload: bytes | None, failure_code: str
    ) -> None:
        fields: dict[str, object] = {
            "sourceStreamId": _text(message_id),
            "failureCode": failure_code,
        }
        if payload is not None:
            fields["payload"] = payload
        self._client.xadd(
            self._settings.request_dlq_stream,
            fields,
            maxlen=self._settings.maximum_dlq_entries,
            approximate=True,
        )


class _ProcessingLease:
    """Refresh Stream pending idle time while one bounded analysis is running."""

    def __init__(
        self,
        *,
        client: _RedisClient,
        settings: RedisStreamSettings,
        message_id: object,
    ) -> None:
        self._client = client
        self._settings = settings
        self._message_id = message_id
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_ProcessingLease":
        self._thread = threading.Thread(
            target=self._heartbeat,
            name="analysis-stream-lease",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def refresh_now(self) -> None:
        try:
            claimed = self._client.xclaim(
                self._settings.request_stream,
                self._settings.consumer_group,
                self._settings.consumer_name,
                0,
                [self._message_id],
                justid=True,
            )
        except Exception:
            raise RuntimeError("analysis_request_lease_refresh_failed") from None
        if not _contains_message_id(claimed, self._message_id):
            raise RuntimeError("analysis_request_lease_lost")

    def _heartbeat(self) -> None:
        interval = self._settings.processing_heartbeat_interval_seconds
        while not self._stop.wait(interval):
            try:
                self.refresh_now()
            except RuntimeError:
                # The final synchronous refresh is authoritative. A transient
                # failure here never ACKs or publishes by itself.
                continue


_PUBLISH_RESULT_IF_ACTIVE_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
    return 0
end
redis.call('XADD', KEYS[2], '*', 'payload', ARGV[1])
return 1
"""

_ACKNOWLEDGE_AND_DELETE_SCRIPT = """
local acknowledged = redis.call('XACK', KEYS[1], ARGV[1], ARGV[2])
if acknowledged == 1 then
    redis.call('XDEL', KEYS[1], ARGV[2])
    if ARGV[3] ~= '' then
        redis.call('DEL', ARGV[3])
    end
end
return acknowledged
"""


class _CancellationMonitor:
    """Observe a shared tombstone and interrupt the active model process."""

    def __init__(
        self,
        *,
        client: _RedisClient,
        settings: RedisStreamSettings,
        request_event_id: str,
        cancel_current: object,
    ) -> None:
        self._client = client
        self._interval = settings.cancellation_poll_interval_seconds
        self._cancel_current = cancel_current
        self.key = settings.cancellation_key_prefix + request_event_id
        self._stop = threading.Event()
        self._cancelled = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def __enter__(self) -> "_CancellationMonitor":
        self.refresh_now()
        if not self.cancelled:
            self._thread = threading.Thread(
                target=self._watch,
                name="analysis-cancellation-monitor",
                daemon=True,
            )
            self._thread.start()
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def refresh_now(self) -> None:
        try:
            exists = self._client.exists(self.key)
        except Exception:
            raise RuntimeError("analysis_cancellation_check_failed") from None
        if exists not in (0, 1, False, True):
            raise RuntimeError("analysis_cancellation_check_failed")
        if bool(exists) and not self._cancelled.is_set():
            self._cancelled.set()
            cancel = self._cancel_current
            if callable(cancel):
                cancel()

    def _watch(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.refresh_now()
            except RuntimeError:
                continue
            if self.cancelled:
                return


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


def _contains_message_id(values: object, expected: object) -> bool:
    if not isinstance(values, (list, tuple)):
        return False
    expected_text = _text(expected)
    return any(_text(value) == expected_text for value in values)
