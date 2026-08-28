"""Network-isolated fresh-process reload for a P6B producer document."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

from ...pronunciation_attempts.adapters.network_isolated_exec import isolated_command
from ..models.integrity import canonical_json_bytes, sha256_document
from ..models.p6_phone_reference import (
    P6PhoneReferenceProducerEvidence,
    P6PhoneReferenceReloadReceipt,
)
from .p6_phone_reference_reload_worker import LOADER_REVISION


_ISOLATION_LAUNCHER = Path(__file__).parents[2] / (
    "pronunciation_attempts/adapters/network_isolated_exec.py"
)


class P6PhoneReferenceReloadError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class P6PhoneReferenceReloadAdapter:
    def __init__(
        self,
        *,
        repository_root: str | Path,
        worker_python: str | Path | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._repository_root = Path(repository_root)
        self._worker_python = Path(worker_python or sys.executable)
        self._timeout_seconds = timeout_seconds

    def reload(
        self, producer: P6PhoneReferenceProducerEvidence
    ) -> P6PhoneReferenceReloadReceipt:
        target = [
            str(self._worker_python.absolute()),
            "-m",
            "voice_coach.visual_feedback.adapters.p6_phone_reference_reload_worker",
        ]
        command = isolated_command(
            launcher_python=sys.executable,
            launcher_path=str(_ISOLATION_LAUNCHER),
            target=target,
            cpu_seconds=max(1, math.ceil(self._timeout_seconds) + 2),
            address_space_bytes=768 * 1024 * 1024,
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
                input=canonical_json_bytes(producer.to_json_object()),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self._repository_root,
                env=environment,
                timeout=self._timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise P6PhoneReferenceReloadError("p6b_reload_process_failed") from None
        if len(completed.stdout) > 256 * 1024 or len(completed.stderr) > 256 * 1024:
            raise P6PhoneReferenceReloadError("p6b_reload_output_exceeded")
        try:
            response = json.loads(completed.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise P6PhoneReferenceReloadError("p6b_reload_output_invalid") from None
        expected = {
            "status": "completed",
            "loader_revision": LOADER_REVISION,
            "producer_sha256": producer.producer_sha256,
            "producer_document_sha256": sha256_document(producer.to_json_object()),
            "parity_receipt_sha256": producer.runtime_parity_receipt.receipt_sha256,
            "calibration_artifact_sha256": producer.calibration_artifact.artifact_sha256,
            "catalog_sha256": producer.claim_catalog.catalog_sha256,
            "policy_sha256": producer.claim_policy.policy_sha256,
        }
        if completed.returncode != 0 or response != expected:
            raise P6PhoneReferenceReloadError("p6b_reload_cross_digest_mismatch")
        loader_path = Path(__file__).with_name("p6_phone_reference_reload_worker.py")
        receipt = P6PhoneReferenceReloadReceipt.issue(
            loader_revision=LOADER_REVISION,
            loader_sha256=_sha256_file(loader_path),
            producer_sha256=producer.producer_sha256,
            producer_document_sha256=expected["producer_document_sha256"],
            parity_receipt_sha256=producer.runtime_parity_receipt.receipt_sha256,
            calibration_artifact_sha256=producer.calibration_artifact.artifact_sha256,
            catalog_sha256=producer.claim_catalog.catalog_sha256,
            policy_sha256=producer.claim_policy.policy_sha256,
        )
        receipt.validate_producer(producer)
        return receipt


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["P6PhoneReferenceReloadAdapter", "P6PhoneReferenceReloadError"]
