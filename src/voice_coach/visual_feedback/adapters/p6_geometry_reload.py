"""Network-isolated fresh-process reload adapter for P6 geometry artifacts."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys

from ...pronunciation_attempts.adapters.network_isolated_exec import isolated_command
from ..models.integrity import canonical_json_bytes, sha256_document
from ..models.p6_geometry import (
    P6_GEOMETRY_RELOAD_SCHEMA_VERSION,
    P6GeometryArtifact,
    P6GeometryReloadReceipt,
)
from .p6_geometry_reload_worker import LOADER_REVISION


_ISOLATION_LAUNCHER = Path(__file__).parents[2] / (
    "pronunciation_attempts/adapters/network_isolated_exec.py"
)
_MAXIMUM_ARTIFACT_BYTES = 128 * 1024


class P6GeometryReloadError(RuntimeError):
    """Path-free reload failure."""


class P6GeometryReloadAdapter:
    """Launch a new Python interpreter and verify every returned cross-digest."""

    def __init__(
        self,
        *,
        repository_root: str | Path | None = None,
        worker_python: str | Path | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._repository_root = Path(
            repository_root
            if repository_root is not None
            else Path(__file__).resolve().parents[4]
        )
        self._worker_python = Path(worker_python or sys.executable)
        self._timeout_seconds = timeout_seconds

    def reload(self, artifact: P6GeometryArtifact) -> P6GeometryReloadReceipt:
        target = [
            str(self._worker_python.absolute()),
            "-m",
            "voice_coach.visual_feedback.adapters.p6_geometry_reload_worker",
        ]
        command = isolated_command(
            launcher_python=sys.executable,
            launcher_path=str(_ISOLATION_LAUNCHER),
            target=target,
            cpu_seconds=max(1, math.ceil(self._timeout_seconds) + 2),
            address_space_bytes=512 * 1024 * 1024,
        )
        environment = {
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": str(self._repository_root / "src"),
        }
        try:
            completed = subprocess.run(
                command,
                input=canonical_json_bytes(artifact.to_json_object()),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self._repository_root,
                env=environment,
                timeout=self._timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise P6GeometryReloadError("p6_reload_process_failed") from None
        if len(completed.stdout) > 128 * 1024 or len(completed.stderr) > 128 * 1024:
            raise P6GeometryReloadError("p6_reload_output_exceeded")
        try:
            response = json.loads(completed.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise P6GeometryReloadError("p6_reload_output_invalid") from None
        expected = {
            "status": "completed",
            "loader_revision": LOADER_REVISION,
            "artifact_sha256": artifact.artifact_sha256,
            "artifact_document_sha256": sha256_document(artifact.to_json_object()),
            "mapping_sha256": artifact.mapping_sha256,
            "threshold_protocol_sha256": artifact.threshold_protocol_sha256,
            "parity_probe_sha256": artifact.parity_probe_sha256,
        }
        if completed.returncode != 0 or response != expected:
            raise P6GeometryReloadError("p6_reload_cross_digest_mismatch")
        unsigned = {
            "schema_version": P6_GEOMETRY_RELOAD_SCHEMA_VERSION,
            "loader_revision": LOADER_REVISION,
            "artifact_sha256": artifact.artifact_sha256,
            "artifact_document_sha256": expected["artifact_document_sha256"],
            "mapping_sha256": artifact.mapping_sha256,
            "threshold_protocol_sha256": artifact.threshold_protocol_sha256,
            "parity_probe_sha256": artifact.parity_probe_sha256,
            "fresh_process": True,
            "hash_match": True,
            "mapping_hash_match": True,
            "threshold_hash_match": True,
            "output_parity": True,
            "network_isolation": "seccomp_socket_denied",
        }
        return P6GeometryReloadReceipt(
            loader_revision=LOADER_REVISION,
            artifact_sha256=artifact.artifact_sha256,
            artifact_document_sha256=str(expected["artifact_document_sha256"]),
            mapping_sha256=artifact.mapping_sha256,
            threshold_protocol_sha256=artifact.threshold_protocol_sha256,
            parity_probe_sha256=artifact.parity_probe_sha256,
            receipt_sha256=sha256_document(unsigned),
        )


def load_p6_geometry_artifact(
    path: str | Path,
    *,
    repository_root: str | Path | None = None,
) -> P6GeometryArtifact:
    """Load one private immutable artifact and reproduce it in isolation."""

    descriptor = -1
    try:
        descriptor = os.open(
            Path(path),
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
            or before.st_size <= 0
            or before.st_size > _MAXIMUM_ARTIFACT_BYTES
            or stat.S_IMODE(before.st_mode) != 0o400
        ):
            raise P6GeometryReloadError("p6_artifact_file_not_private")
        remaining = before.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                raise P6GeometryReloadError("p6_artifact_file_changed")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise P6GeometryReloadError("p6_artifact_file_changed")
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise P6GeometryReloadError("p6_artifact_file_changed")
    except P6GeometryReloadError:
        raise
    except OSError:
        raise P6GeometryReloadError("p6_artifact_file_unavailable") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    def reject_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        document: dict[str, object] = {}
        for key, value in pairs:
            if key in document:
                raise P6GeometryReloadError("p6_artifact_json_invalid")
            document[key] = value
        return document

    try:
        document = json.loads(
            b"".join(chunks).decode("utf-8", errors="strict"),
            object_pairs_hook=reject_duplicate_keys,
        )
        artifact = P6GeometryArtifact.from_json_object(document)
        P6GeometryReloadAdapter(repository_root=repository_root).reload(artifact)
    except P6GeometryReloadError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise P6GeometryReloadError("p6_artifact_json_invalid") from None
    return artifact


__all__ = [
    "P6GeometryReloadAdapter",
    "P6GeometryReloadError",
    "P6GeometryReloadReceipt",
    "load_p6_geometry_artifact",
]
