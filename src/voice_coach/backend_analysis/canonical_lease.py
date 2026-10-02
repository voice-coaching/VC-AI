"""Bounded snapshots of the parent's atomically replaced private lease file.

Only Backend-authorized parent updates grant time. Reading never renews a lease.
Immutable model artifacts continue to use their stricter result-file reader.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import stat
import time
from uuid import UUID, uuid4

from .canonical_bridge import CanonicalIntegrationError, _require
from .canonical_codec import encode_document
from .canonical_process import private_directory
from .http_models import parse_json, HttpFailure


LEASE_CODES = frozenset({
    "CANONICAL_LEASE_READ_FAILED", "CANONICAL_LEASE_INVALID",
    "CANONICAL_LEASE_REPLACEMENT_RETRY_EXHAUSTED", "CANONICAL_LEASE_EXPIRED",
    "CANONICAL_DEADLINE_EXCEEDED", "CANONICAL_LEASE_PUBLISH_FAILED",
})


@dataclass(frozen=True, slots=True)
class LeaseSnapshot:
    """Validated monotonic expiry for exactly one already admitted execution."""

    execution_id: UUID
    until: float
    replacements: int = 0

    def remaining(self, deadline: float) -> float:
        now = time.monotonic()
        _require(now < deadline, "CANONICAL_DEADLINE_EXCEEDED")
        _require(now < self.until, "CANONICAL_LEASE_EXPIRED")
        return min(self.until, deadline) - now


def _attributes(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_uid, info.st_mode)


def _validate_file(info: os.stat_result) -> None:
    _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
             and stat.S_IMODE(info.st_mode) == 0o600 and 0 < info.st_size <= 1024,
             "CANONICAL_LEASE_INVALID")


def read_lease(work: Path, execution_id: UUID, deadline: float) -> LeaseSnapshot:
    """Retry only a confirmed path replacement, at most three opens / 50 ms.

An unlinked old inode can change ctime while its bytes remain immutable. Discard
that snapshot and open the published successor; never accept a changed inode's
contents or reuse an expired cache. I/O itself remains subject to the outer guard.
"""
    _require(type(deadline) in (int, float) and math.isfinite(deadline),
             "CANONICAL_LEASE_INVALID")
    retry_deadline = min(deadline, time.monotonic() + 0.05)
    try:
        private_directory(work)
        directory = os.open(work, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for _attempt in range(3):
                _require(time.monotonic() < deadline, "CANONICAL_DEADLINE_EXCEEDED")
                if _attempt and time.monotonic() >= retry_deadline:
                    break
                descriptor = os.open("lease.json", os.O_RDONLY | os.O_NOFOLLOW,
                                     dir_fd=directory)
                with os.fdopen(descriptor, "rb") as stream:
                    before = os.fstat(stream.fileno())
                    _validate_file(before)
                    raw = stream.read(1025)
                    after = os.fstat(stream.fileno())
                    _validate_file(after)
                    current = os.stat("lease.json", dir_fd=directory, follow_symlinks=False)
                    _validate_file(current)
                if (current.st_dev, current.st_ino) != (after.st_dev, after.st_ino):
                    # Atomic publisher replaced the old inode. No grace or cached grant.
                    continue
                _require(_attributes(before) == _attributes(after)
                         and len(raw) == before.st_size, "CANONICAL_LEASE_INVALID")
                lease = parse_json(raw)
                _require(type(lease) is dict and set(lease) == {"executionId", "until"}
                         and lease["executionId"] == str(execution_id), "CANONICAL_LEASE_INVALID")
                until = lease["until"]
                _require(type(until) in (int, float) and math.isfinite(until),
                         "CANONICAL_LEASE_INVALID")
                _require(time.monotonic() < deadline, "CANONICAL_DEADLINE_EXCEEDED")
                return LeaseSnapshot(execution_id, until, _attempt)
        finally:
            os.close(directory)
    except OSError:
        raise CanonicalIntegrationError("CANONICAL_LEASE_READ_FAILED") from None
    except HttpFailure:
        raise CanonicalIntegrationError("CANONICAL_LEASE_INVALID") from None
    raise CanonicalIntegrationError("CANONICAL_LEASE_REPLACEMENT_RETRY_EXHAUSTED")


def publish_lease(work: Path, execution_id: UUID, until: float) -> None:
    """Publish a complete parent-authorized snapshot without truncating live bytes."""
    _require(type(until) in (int, float) and math.isfinite(until), "CANONICAL_LEASE_INVALID")
    private_directory(work)
    temporary = work / (".lease-" + uuid4().hex)
    created = False
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encode_document({"executionId": str(execution_id), "until": until}))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, work / "lease.json")
    except OSError:
        raise CanonicalIntegrationError("CANONICAL_LEASE_PUBLISH_FAILED") from None
    finally:
        if created:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # Preserve the primary failure; only this call's private temp is involved.
                pass
