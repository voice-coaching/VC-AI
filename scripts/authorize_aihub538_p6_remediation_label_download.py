#!/usr/bin/env python3
"""Authorize one staged TL177--TL180 label-only AI Hub 538 download."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from voice_coach.visual_feedback.services.aihub538_p6_remediation_label_download import (  # noqa: E402
    Aihub538P6RemediationLabelDownloadError,
    Aihub538P6RemediationLabelDownloadRequest,
    Aihub538P6RemediationLabelDownloadService,
)


def _descriptor(value: str) -> int:
    try:
        descriptor = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("descriptor must be >= 3") from None
    if descriptor < 3:
        raise argparse.ArgumentTypeError("descriptor must be >= 3")
    return descriptor


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--download-destination-directory",
        type=Path,
        required=True,
        help="existing empty owner-only mode-0700 directory",
    )
    parser.add_argument(
        "--transaction-output-directory",
        type=Path,
        required=True,
        help="new directory created exclusively for the authority receipt",
    )
    parser.add_argument(
        "--prior-prefix-receipt-fd",
        type=_descriptor,
        help="omit for TL177; required for the exact next TL thereafter",
    )
    parser.add_argument(
        "--dataset-access-permission-confirmed",
        action="store_true",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        transaction, path = Aihub538P6RemediationLabelDownloadService().issue(
            Aihub538P6RemediationLabelDownloadRequest(
                download_destination_directory=(
                    arguments.download_destination_directory.absolute()
                ),
                transaction_output_directory=(
                    arguments.transaction_output_directory.absolute()
                ),
                prior_prefix_receipt_descriptor=(
                    arguments.prior_prefix_receipt_fd
                ),
                dataset_access_permission_confirmed=(
                    arguments.dataset_access_permission_confirmed
                ),
            )
        )
        print(
            json.dumps(
                {
                    "status": "authorized_one_remediation_label_download",
                    "label_role_component": (
                        transaction.label_role_component
                    ),
                    "label_file_key": transaction.label_file_key,
                    "prefix_sequence_number": (
                        transaction.prefix_sequence_number
                    ),
                    "requested_source_file_key_count": 0,
                    "transaction_path": str(path),
                    "transaction_sha256": transaction.transaction_sha256,
                    "promotion_authorized": False,
                },
                sort_keys=True,
            )
        )
        return 0
    except Aihub538P6RemediationLabelDownloadError as error:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "reason_code": error.reason_code,
                    "label_download_authorized": False,
                    "source_download_authorized": False,
                    "promotion_authorized": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
