#!/usr/bin/env python3
"""Issue an offline authority for AI Hub 539 Training TL8/TS8 download."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from voice_coach.visual_feedback.models.aihub539_actual_media_authorization import (  # noqa: E402
    AIHUB539_ACTUAL_MEDIA_DATASET_ID,
    AIHUB539_ACTUAL_MEDIA_ORDERED_FILE_KEYS,
    AIHUB539_ACTUAL_MEDIA_PARTITION,
)
from voice_coach.visual_feedback.services.aihub539_actual_media_authorization import (  # noqa: E402
    Aihub539ActualMediaAuthorizationRequest,
    Aihub539ActualMediaAuthorizationService,
    Aihub539ActualMediaAuthorizationServiceError,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--download-destination-directory",
        type=Path,
        required=True,
        help="existing empty owner-only mode-0700 directory",
    )
    parser.add_argument(
        "--authorization-output-directory",
        type=Path,
        required=True,
        help="new directory created exclusively for the mode-0400 receipt",
    )
    parser.add_argument(
        "--dataset-access-permission-confirmed",
        action="store_true",
        help="confirm the account has AI Hub 539 portal permission",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        authorization, authorization_path = (
            Aihub539ActualMediaAuthorizationService().issue(
                Aihub539ActualMediaAuthorizationRequest(
                    dataset_id=AIHUB539_ACTUAL_MEDIA_DATASET_ID,
                    partition=AIHUB539_ACTUAL_MEDIA_PARTITION,
                    ordered_file_keys=(
                        AIHUB539_ACTUAL_MEDIA_ORDERED_FILE_KEYS
                    ),
                    download_destination_directory=(
                        arguments.download_destination_directory.absolute()
                    ),
                    authorization_output_directory=(
                        arguments.authorization_output_directory.absolute()
                    ),
                    dataset_access_permission_confirmed=(
                        arguments.dataset_access_permission_confirmed
                    ),
                )
            )
        )
        print(
            json.dumps(
                {
                    "status": (
                        "authorized_nonlocked_training_acquisition"
                    ),
                    "dataset_id": AIHUB539_ACTUAL_MEDIA_DATASET_ID,
                    "partition": AIHUB539_ACTUAL_MEDIA_PARTITION,
                    "ordered_file_keys": list(
                        AIHUB539_ACTUAL_MEDIA_ORDERED_FILE_KEYS
                    ),
                    "authorization_path": str(authorization_path),
                    "authorization_sha256": (
                        authorization.authorization_sha256
                    ),
                    "network_download_performed": False,
                    "archive_admission_completed": False,
                    "actual_media_sync_validated": False,
                    "locked_partition_accessed": False,
                    "p8_authorized": False,
                    "promotion_authorized": False,
                },
                sort_keys=True,
            )
        )
        return 0
    except Aihub539ActualMediaAuthorizationServiceError as error:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "reason_code": error.reason_code,
                    "network_download_performed": False,
                    "archive_admission_completed": False,
                    "actual_media_sync_validated": False,
                    "locked_partition_accessed": False,
                    "p8_authorized": False,
                    "promotion_authorized": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
