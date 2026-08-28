#!/usr/bin/env python3
"""Audit approved AI Hub 539 TL8 labels and write aggregate-only evidence."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from voice_coach.visual_feedback.adapters.aihub539_sync_dataset import (  # noqa: E402
    Aihub539SyncAuditError,
    Aihub539SyncDatasetAdapter,
)
from voice_coach.visual_feedback.models.integrity import canonical_json_bytes  # noqa: E402
from voice_coach.visual_feedback.services.aihub539_sync_audit import (  # noqa: E402
    Aihub539SyncLabelAuditService,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--labels-root",
        type=Path,
        required=True,
        help="absolute restricted directory containing the extracted TL8 labels",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--dataset-access-permission-confirmed",
        action="store_true",
        help="confirm AI Hub 539 portal access permission; this is not a technical gate",
    )
    return parser


def _write_exclusive_atomic(path: Path, payload: bytes) -> None:
    """Publish one immutable receipt without replacing an existing filesystem node."""

    if not path.is_absolute() or not path.parent.is_dir() or path.parent.is_symlink():
        raise OSError("output boundary invalid")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".aihub539-sync-audit-",
            suffix=".partial",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fchmod(stream.fileno(), 0o400)
            os.fsync(stream.fileno())
        os.link(temporary_path, path, follow_symlinks=False)
        directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        receipt = Aihub539SyncLabelAuditService(
            Aihub539SyncDatasetAdapter()
        ).audit(
            labels_root=arguments.labels_root,
            dataset_access_permission_confirmed=(
                arguments.dataset_access_permission_confirmed
            ),
        )
        _write_exclusive_atomic(
            arguments.output,
            canonical_json_bytes(receipt.to_json_object()) + b"\n",
        )
    except Aihub539SyncAuditError as error:
        print(
            json.dumps(
                {"status": "failed_closed", "reason_codes": [error.reason_code]},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2
    except (OSError, ValueError):
        print(
            '{"reason_codes":["aihub539_execution_boundary_failed"],'
            '"status":"failed_closed"}'
        )
        return 2

    print(
        json.dumps(
            {
                "status": "completed",
                "receipt_sha256": receipt.receipt_sha256,
                "source_manifest_sha256": receipt.source_manifest_sha256,
                "source_document_count": 13,
                "source_hash_count": receipt.source_hash_count,
                "total_timeline_frame_count": (
                    receipt.timing.total_timeline_frame_count
                ),
                "total_sentence_count": receipt.timing.total_sentence_count,
                "actual_media_sync_validated": False,
                "promotion_authorized": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
