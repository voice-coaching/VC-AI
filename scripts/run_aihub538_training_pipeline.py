#!/usr/bin/env python3
"""Run the aggregate-only AI Hub 538 non-locked metadata smoke pipeline.

Examples use either a private completed-download directory or already-open
read-only descriptors.  This entrypoint never searches for partial downloads
and never accepts locked-partition file paths.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.visual_feedback.services.aihub538_training_pipeline import (
    Aihub538TrainingPipeline,
    Aihub538TrainingPipelineError,
)


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "The destination must be absent. On failure, only the fresh run "
            "root is removed and the same path may be retried. A cleanup_failed "
            "result requires preserving that root for manual inspection and "
            "retrying with a different absent destination."
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--input-directory",
        type=Path,
        help=(
            "private 0700 directory containing only the exact completed "
            "download.tar and download.receipt.json inputs"
        ),
    )
    source.add_argument(
        "--download-fd",
        type=int,
        help="caller-owned read-only completed download.tar descriptor",
    )
    parser.add_argument(
        "--receipt-fd",
        type=int,
        help="caller-owned read-only secure-download receipt descriptor",
    )
    parser.add_argument(
        "--identity-key-fd",
        type=int,
        required=True,
        help="caller-owned read-only 0400 identity HMAC key descriptor",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        required=True,
        help="absent destination; the pipeline creates it as 0700",
    )
    parser.add_argument(
        "--dataset-access-permission-confirmed",
        action="store_true",
        help="confirm that AI Hub dataset access was approved",
    )
    return parser.parse_args(argv)


def _public_failure(reason_code: str) -> None:
    print(
        json.dumps(
            {
                "status": "failed_closed",
                "reason_code": reason_code,
                "partial_download_accessed": False,
                "locked_partition_accessed": False,
                "restricted_values_serialized": False,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    arguments = _arguments(argv)
    if arguments.input_directory is None and arguments.receipt_fd is None:
        _public_failure("aihub538_completed_receipt_descriptor_required")
        return 64
    if arguments.input_directory is not None and arguments.receipt_fd is not None:
        _public_failure("aihub538_input_mode_invalid")
        return 64
    pipeline = Aihub538TrainingPipeline()
    output_directory = arguments.output_directory.absolute()
    try:
        if arguments.input_directory is not None:
            evidence = pipeline.run_from_completed_directory(
                input_directory=arguments.input_directory.absolute(),
                identity_key_descriptor=arguments.identity_key_fd,
                output_directory=output_directory,
                dataset_access_permission_confirmed=(
                    arguments.dataset_access_permission_confirmed
                ),
            )
        else:
            evidence = pipeline.run(
                archive_descriptor=arguments.download_fd,
                receipt_descriptor=arguments.receipt_fd,
                identity_key_descriptor=arguments.identity_key_fd,
                output_directory=output_directory,
                dataset_access_permission_confirmed=(
                    arguments.dataset_access_permission_confirmed
                ),
            )
    except Aihub538TrainingPipelineError as error:
        _public_failure(error.reason_code)
        return 65
    except Exception:
        _public_failure("aihub538_training_pipeline_internal_failure")
        return 70
    print(
        json.dumps(
            evidence.to_json_object(),
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
