"""Network-isolated strict reload worker for an actual-source P8 result.

The worker receives only the durable aggregate state and output directories.
It never receives, resolves, or opens the locked completed-download directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

from .aihub538_locked_dataset import p8_frozen_pipeline_pins_sha256
from .p8_seal_store import P8SealStateStore
from ..models.p8_locked_evaluation import P8LockedEvaluationStatus
from ..services.p8_aihub538_source_evaluator import P8LockedSourceReceiptStore


WORKER_REVISION = "p8_actual_source_strict_reload_worker_v1"


def worker_code_sha256() -> str:
    """Digest the exact worker source pinned before source consumption."""

    path = Path(__file__)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
            raise RuntimeError("p8_reload_worker_code_invalid")
        before = (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
        )
        digest = hashlib.sha256()
        offset = 0
        while offset < metadata.st_size:
            chunk = os.pread(descriptor, min(1024 * 1024, metadata.st_size - offset), offset)
            if not chunk:
                raise RuntimeError("p8_reload_worker_code_short_read")
            digest.update(chunk)
            offset += len(chunk)
        after_metadata = os.fstat(descriptor)
        after = (
            after_metadata.st_dev,
            after_metadata.st_ino,
            after_metadata.st_size,
            after_metadata.st_mtime_ns,
        )
        if before != after:
            raise RuntimeError("p8_reload_worker_code_changed")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _strict_reload(*, state_directory: Path, output_directory: Path) -> dict[str, object]:
    store = P8SealStateStore(state_directory)
    base = store.load_read_zero_seal()
    actual = store.load_actual_source_read_zero_seal()
    transaction = store.load_access_transaction()
    pending = store.load_actual_source_gate_pending()
    cleanup = store.load_actual_source_cleanup_state()
    report, source_manifest, metrics = (
        store.load_staged_actual_source_result_for_reload()
    )
    receipts = P8LockedSourceReceiptStore(output_directory)
    terminal = receipts.load_terminal()
    work_absent = receipts.restricted_work_directory_absent()

    if (
        actual.base_read_zero_seal_sha256 != base.seal_sha256
        or pending.actual_source_seal_sha256
        != actual.actual_source_seal_sha256
        or pending.access_transaction_sha256
        != transaction.transaction_sha256
        or cleanup.gate_pending_sha256 != pending.gate_pending_sha256
        or terminal.access_transaction_sha256
        != transaction.transaction_sha256
        or terminal.frozen_pipeline_pins_sha256
        != pending.frozen_pipeline_pins_sha256
        or terminal.canonical_report_sha256 != report.report_sha256
        or terminal.report_status != report.status.value
        or terminal.cleanup_state_sha256 != cleanup.cleanup_state_sha256
        or terminal.cleanup_status != cleanup.status.value
        or terminal.reason_codes != report.reason_codes
        or work_absent is not cleanup.restricted_work_directory_absent
        or pending.frozen_pipeline_pins_sha256
        != p8_frozen_pipeline_pins_sha256(base)
    ):
        raise RuntimeError("p8_actual_source_reload_lineage_invalid")

    lineage_sha256: str | None = None
    if source_manifest is not None or metrics is not None:
        if source_manifest is None or metrics is None:
            raise RuntimeError("p8_actual_source_reload_evidence_partial")
        if terminal.aggregate_lineage_sha256 is None:
            raise RuntimeError("p8_actual_source_reload_lineage_missing")
        cleanup_receipt = receipts.load_cleanup()
        lineage = receipts.load_lineage(
            expected_lineage_sha256=terminal.aggregate_lineage_sha256
        )
        if (
            cleanup.workspace_cleanup_receipt_sha256
            != cleanup_receipt.cleanup_sha256
            or cleanup_receipt.access_transaction_sha256
            != transaction.transaction_sha256
            or lineage.access_transaction_sha256
            != transaction.transaction_sha256
            or lineage.frozen_pipeline_pins_sha256
            != pending.frozen_pipeline_pins_sha256
            or lineage.source_manifest_sha256
            != source_manifest.manifest_sha256
            or lineage.aggregate_metrics_sha256 != metrics.metrics_sha256
            or lineage.cleanup_receipt_sha256
            != cleanup_receipt.cleanup_sha256
            or lineage.claim_replay.claim_catalog_sha256
            != base.claim_catalog_sha256
            or lineage.claim_replay.claim_policy_sha256
            != base.claim_policy_sha256
        ):
            raise RuntimeError("p8_actual_source_reload_receipt_pin_invalid")
        lineage_sha256 = lineage.lineage_sha256
    else:
        if terminal.aggregate_lineage_sha256 is not None:
            raise RuntimeError("p8_actual_source_reload_lineage_unexpected")
        if cleanup.workspace_cleanup_receipt_sha256 is not None:
            cleanup_receipt = receipts.load_cleanup()
            if (
                cleanup_receipt.cleanup_sha256
                != cleanup.workspace_cleanup_receipt_sha256
                or cleanup_receipt.access_transaction_sha256
                != transaction.transaction_sha256
            ):
                raise RuntimeError("p8_actual_source_reload_cleanup_pin_invalid")
        if terminal.failure_completeness_sha256 is not None:
            completeness = receipts.load_failure_completeness()
            if (
                completeness.receipt_sha256
                != terminal.failure_completeness_sha256
                or completeness.access_transaction_sha256
                != transaction.transaction_sha256
                or (
                    completeness.admitted_item_count,
                    completeness.evaluated_item_count,
                    completeness.evaluation_exclusion_count,
                )
                != (
                    completeness.evaluation_exclusion_count,
                    0,
                    completeness.admitted_item_count,
                )
            ):
                raise RuntimeError(
                    "p8_actual_source_reload_failure_completeness_invalid"
                )

    if cleanup.status.value == "cleanup_completed" and not work_absent:
        raise RuntimeError("p8_actual_source_reload_work_retained")
    worker_sha256 = worker_code_sha256()
    if worker_sha256 != actual.fresh_reload_worker_code_sha256:
        raise RuntimeError("p8_actual_source_reload_worker_pin_invalid")
    return {
        "status": "completed",
        "worker_revision": WORKER_REVISION,
        "worker_pid": os.getpid(),
        "worker_code_sha256": worker_sha256,
        "actual_source_seal_sha256": actual.actual_source_seal_sha256,
        "gate_pending_sha256": pending.gate_pending_sha256,
        "frozen_pipeline_pins_sha256": pending.frozen_pipeline_pins_sha256,
        "access_transaction_sha256": transaction.transaction_sha256,
        "cleanup_state_sha256": cleanup.cleanup_state_sha256,
        "canonical_report_sha256": report.report_sha256,
        "locked_source_manifest_sha256": (
            source_manifest.manifest_sha256 if source_manifest is not None else None
        ),
        "aggregate_metrics_sha256": (
            metrics.metrics_sha256 if metrics is not None else None
        ),
        "aggregate_lineage_sha256": lineage_sha256,
        "source_terminal_sha256": terminal.terminal_sha256,
        "result_status": report.status.value,
        "cleanup_status": cleanup.status.value,
        "restricted_work_directory_absent": work_absent,
        "strict_reload": True,
        "locked_source_reopened": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--state-directory", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    try:
        arguments = parser.parse_args(argv)
        response = _strict_reload(
            state_directory=arguments.state_directory,
            output_directory=arguments.output_directory,
        )
    except BaseException:
        response = {
            "status": "failed_closed",
            "reason_code": "p8_actual_source_fresh_reload_failed",
            "restricted_error_detail_serialized": False,
        }
        sys.stdout.write(
            json.dumps(response, sort_keys=True, separators=(",", ":")) + "\n"
        )
        return 2
    sys.stdout.write(
        json.dumps(response, sort_keys=True, separators=(",", ":")) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["WORKER_REVISION", "main", "worker_code_sha256"]
