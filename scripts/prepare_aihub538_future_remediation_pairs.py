#!/usr/bin/env python3
"""Prepare exact feasible TS177+ pairs for the internal P6 v4 runner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from voice_coach.visual_feedback.services.aihub538_future_remediation_pair_preparation import (  # noqa: E402
    Aihub538FuturePairInput,
    Aihub538FuturePairPreparationError,
    Aihub538FutureRemediationPairPreparationService,
)


def _fd(value: str) -> int:
    descriptor = int(value)
    if descriptor < 3:
        raise argparse.ArgumentTypeError("descriptor must be >= 3")
    return descriptor


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remediation-receipt-fd", type=_fd, required=True)
    parser.add_argument("--identity-key-fd", type=_fd, required=True)
    parser.add_argument(
        "--pair-download-directory", type=Path, action="append", required=True
    )
    parser.add_argument(
        "--prepared-label-directory", type=Path, action="append", required=True
    )
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--dataset-access-permission-confirmed", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _arguments(argv)
    try:
        if len(arguments.pair_download_directory) != len(
            arguments.prepared_label_directory
        ):
            raise Aihub538FuturePairPreparationError(
                "aihub538_future_pair_input_count_mismatch"
            )
        result = Aihub538FutureRemediationPairPreparationService().run(
            remediation_receipt_descriptor=arguments.remediation_receipt_fd,
            identity_key_descriptor=arguments.identity_key_fd,
            pairs=tuple(
                Aihub538FuturePairInput(download.absolute(), prepared.absolute())
                for download, prepared in zip(
                    arguments.pair_download_directory,
                    arguments.prepared_label_directory,
                    strict=True,
                )
            ),
            output_directory=arguments.output_directory.absolute(),
            dataset_access_permission_confirmed=(
                arguments.dataset_access_permission_confirmed
            ),
        )
    except Aihub538FuturePairPreparationError as error:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "reason_code": error.reason_code,
                    "restricted_values_serialized": False,
                    "locked_partition_accessed": False,
                    "p8_accessed": False,
                    "promotion_authorized": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 65
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
