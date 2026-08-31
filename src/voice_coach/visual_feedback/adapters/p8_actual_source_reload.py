"""Fresh-process, network-isolated verifier for staged P8 source evidence."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys

from ...pronunciation_attempts.adapters.network_isolated_exec import (
    isolated_command,
)
from .p8_actual_source_reload_worker import WORKER_REVISION, worker_code_sha256
from .p8_seal_store import P8SealStateStore
from ..models.p8_actual_source_state import (
    P8ActualSourceFreshProcessReloadReceipt,
    P8ActualSourceResultStatus,
)
from ..models.p8_locked_evaluation import P8LockedEvaluationStatus


_ISOLATION_LAUNCHER = Path(__file__).parents[2] / (
    "pronunciation_attempts/adapters/network_isolated_exec.py"
)


class P8ActualSourceReloadError(RuntimeError):
    """Path-free failure from the fresh-process verifier."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def p8_actual_source_reload_worker_code_sha256() -> str:
    return worker_code_sha256()


class P8ActualSourceFreshReloadAdapter:
    """Exec a fresh strict parser; no locked source locator is passed."""

    def __init__(
        self,
        *,
        repository_root: Path | None = None,
        worker_python: Path | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._repository_root = repository_root or Path(__file__).resolve().parents[4]
        self._worker_python = worker_python or Path(sys.executable)
        self._timeout_seconds = timeout_seconds

    def reload(
        self,
        *,
        state_store: P8SealStateStore,
        output_directory: Path,
        verified_at_utc: str,
    ) -> tuple[P8ActualSourceFreshProcessReloadReceipt, str]:
        state_directory = state_store._state_directory_for_isolated_reload()
        target = [
            str(self._worker_python.absolute()),
            "-m",
            (
                "voice_coach.visual_feedback.adapters."
                "p8_actual_source_reload_worker"
            ),
            "--state-directory",
            str(state_directory),
            "--output-directory",
            str(output_directory),
        ]
        command = isolated_command(
            launcher_python=sys.executable,
            launcher_path=str(_ISOLATION_LAUNCHER),
            target=target,
            cpu_seconds=max(2, math.ceil(self._timeout_seconds) + 2),
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
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self._repository_root,
                env=environment,
                timeout=self._timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise P8ActualSourceReloadError(
                "p8_actual_source_reload_process_failed"
            ) from None
        if (
            len(completed.stdout) > 256 * 1024
            or len(completed.stderr) > 256 * 1024
        ):
            raise P8ActualSourceReloadError(
                "p8_actual_source_reload_output_exceeded"
            )
        try:
            response = json.loads(
                completed.stdout, object_pairs_hook=_unique_json_object
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise P8ActualSourceReloadError(
                "p8_actual_source_reload_output_invalid"
            ) from None
        if completed.returncode != 0 or not isinstance(response, dict):
            raise P8ActualSourceReloadError(
                "p8_actual_source_reload_failed_closed"
            )

        actual = state_store.load_actual_source_read_zero_seal()
        pending = state_store.load_actual_source_gate_pending()
        cleanup = state_store.load_actual_source_cleanup_state()
        report, source_manifest, metrics = (
            state_store.load_staged_actual_source_result_for_reload()
        )
        expected_worker_sha256 = p8_actual_source_reload_worker_code_sha256()
        expected = {
            "status": "completed",
            "worker_revision": WORKER_REVISION,
            "worker_code_sha256": expected_worker_sha256,
            "actual_source_seal_sha256": actual.actual_source_seal_sha256,
            "gate_pending_sha256": pending.gate_pending_sha256,
            "frozen_pipeline_pins_sha256": (
                pending.frozen_pipeline_pins_sha256
            ),
            "access_transaction_sha256": pending.access_transaction_sha256,
            "cleanup_state_sha256": cleanup.cleanup_state_sha256,
            "canonical_report_sha256": report.report_sha256,
            "locked_source_manifest_sha256": (
                source_manifest.manifest_sha256
                if source_manifest is not None
                else None
            ),
            "aggregate_metrics_sha256": (
                metrics.metrics_sha256 if metrics is not None else None
            ),
            "result_status": report.status.value,
            "cleanup_status": cleanup.status.value,
            "restricted_work_directory_absent": (
                cleanup.restricted_work_directory_absent
            ),
            "strict_reload": True,
            "locked_source_reopened": False,
        }
        expected_keys = set(expected) | {
            "worker_pid",
            "aggregate_lineage_sha256",
            "source_terminal_sha256",
        }
        if set(response) != expected_keys or any(
            response[name] != value for name, value in expected.items()
        ):
            raise P8ActualSourceReloadError(
                "p8_actual_source_reload_cross_digest_mismatch"
            )
        worker_pid = response["worker_pid"]
        if (
            isinstance(worker_pid, bool)
            or not isinstance(worker_pid, int)
            or worker_pid <= 0
            or worker_pid == os.getpid()
        ):
            raise P8ActualSourceReloadError(
                "p8_actual_source_reload_not_fresh_process"
            )
        lineage_sha256 = response["aggregate_lineage_sha256"]
        terminal_sha256 = response["source_terminal_sha256"]
        if lineage_sha256 is not None and not _is_sha256(lineage_sha256):
            raise P8ActualSourceReloadError(
                "p8_actual_source_reload_lineage_digest_invalid"
            )
        if not _is_sha256(terminal_sha256):
            raise P8ActualSourceReloadError(
                "p8_actual_source_reload_terminal_digest_invalid"
            )
        if actual.fresh_reload_worker_code_sha256 != expected_worker_sha256:
            raise P8ActualSourceReloadError(
                "p8_actual_source_reload_worker_pin_mismatch"
            )
        result_status = (
            P8ActualSourceResultStatus.COMPLETED
            if report.status is P8LockedEvaluationStatus.COMPLETED
            else P8ActualSourceResultStatus.FAILED_CLOSED
        )
        receipt = P8ActualSourceFreshProcessReloadReceipt.issue(
            actual_source_seal=actual,
            gate_pending=pending,
            cleanup_state=cleanup,
            canonical_report_sha256=report.report_sha256,
            locked_source_manifest_sha256=expected[
                "locked_source_manifest_sha256"
            ],
            aggregate_metrics_sha256=expected["aggregate_metrics_sha256"],
            aggregate_lineage_sha256=lineage_sha256,
            result_status=result_status,
            verified_at_utc=verified_at_utc,
        )
        return receipt, terminal_sha256


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise P8ActualSourceReloadError(
                "p8_actual_source_reload_duplicate_key"
            )
        document[key] = value
    return document


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower()


__all__ = [
    "P8ActualSourceFreshReloadAdapter",
    "P8ActualSourceReloadError",
    "p8_actual_source_reload_worker_code_sha256",
]
