"""Non-sensitive worker liveness state for an external supervisor."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import stat


_SCHEMA_VERSION = "voice-coaching.analysis-worker-state.v1"


class WorkerRuntimeState:
    """Atomically publish bounded counters without request or connection data."""

    def __init__(
        self,
        path: Path | None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if path is not None:
            try:
                parent = path.parent
                parent_metadata = parent.lstat()
            except OSError:
                raise ValueError("analysis_worker_state_path_invalid") from None
            if (
                not path.is_absolute()
                or parent.is_symlink()
                or not stat.S_ISDIR(parent_metadata.st_mode)
            ):
                raise ValueError("analysis_worker_state_path_invalid")
        self._path = path
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self._started_at = self._now()
        self._acked_count = 0
        self._consecutive_failures = 0

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "WorkerRuntimeState":
        env = os.environ if environment is None else environment
        raw_path = env.get("AI_ANALYSIS_WORKER_STATE_PATH")
        return cls(Path(raw_path) if raw_path else None)

    def ready(self) -> None:
        self._write("ready", "none")

    def polled(self, acked: int) -> None:
        if acked < 0:
            raise ValueError("acked count must not be negative")
        self._acked_count += acked
        self._consecutive_failures = 0
        self._write("ready", "none")

    def failed(self, code: str) -> None:
        if not code or len(code) > 100:
            raise ValueError("worker failure code is invalid")
        self._consecutive_failures += 1
        self._write("degraded", code)

    def stopping(self) -> None:
        self._write("stopping", "none")

    def _write(self, status: str, failure_code: str) -> None:
        if self._path is None:
            return
        document = {
            "schemaVersion": _SCHEMA_VERSION,
            "pid": os.getpid(),
            "status": status,
            "startedAtUtc": self._started_at,
            "lastPollAtUtc": self._now(),
            "ackedCount": self._acked_count,
            "consecutiveFailures": self._consecutive_failures,
            "lastFailureCode": failure_code,
        }
        payload = json.dumps(
            document,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        temporary = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _now(self) -> str:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("analysis worker state clock must be timezone-aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = ["WorkerRuntimeState"]
