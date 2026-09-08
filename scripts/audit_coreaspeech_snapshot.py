#!/usr/bin/env python3
"""Run aggregate-only, read-only audio-header QC on a pinned CoreaSpeech shard."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

from voice_coach.data.coreaspeech_qc import (
    CoreaSpeechAuditError,
    audit_coreaspeech_shard,
)


SCRIPT_PATH = Path(__file__).resolve()
WORKSPACE_ROOT = SCRIPT_PATH.parents[1]
DEFAULT_REVISION = "950d6a50b2b3e338d141313d11440fe47b037745"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a pinned CoreaSpeech shard and inspect embedded WAV headers "
            "without decoding, materializing, or exporting any utterance."
        )
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/manifests/coreaspeech-smoke-v1.yaml"),
    )
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=Path("data/raw/research-noncommercial/coreaspeech") / DEFAULT_REVISION,
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--row-limit",
        type=int,
        help="Optional sampled smoke limit; omit for canonical full-shard QC.",
    )
    parser.add_argument(
        "--duration-tolerance-seconds",
        type=float,
        default=0.05,
    )
    return parser.parse_args()


def _resolve_workspace_path(
    candidate: Path,
    *,
    must_exist: bool,
    field_name: str,
) -> Path:
    resolved = (
        candidate if candidate.is_absolute() else WORKSPACE_ROOT / candidate
    ).resolve(strict=must_exist)
    try:
        resolved.relative_to(WORKSPACE_ROOT)
    except ValueError as exc:
        raise CoreaSpeechAuditError(
            f"{field_name} must remain inside the workspace"
        ) from exc
    return resolved


def _write_json_atomic(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            document,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def main() -> int:
    args = parse_args()
    try:
        manifest = _resolve_workspace_path(
            args.manifest,
            must_exist=True,
            field_name="manifest",
        )
        dataset_root = _resolve_workspace_path(
            args.dataset_root,
            must_exist=True,
            field_name="dataset root",
        )
        output = _resolve_workspace_path(
            args.output,
            must_exist=False,
            field_name="output",
        )
        report = audit_coreaspeech_shard(
            manifest_path=manifest,
            dataset_root=dataset_root,
            row_limit=args.row_limit,
            duration_tolerance_seconds=args.duration_tolerance_seconds,
        )
        _write_json_atomic(output, report)
    except CoreaSpeechAuditError as error:
        print(
            json.dumps(
                {"status": "failed_closed", "error": str(error)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    except (OSError, ValueError):
        # Raw OS/parser errors can contain workspace paths, temporary names, or
        # backend details.  The CLI is aggregate-only, so unexpected failures
        # intentionally expose only a stable public error code.
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error": "unexpected_local_io_or_value_error",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] != "header_qc_failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
