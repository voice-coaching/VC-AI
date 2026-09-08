#!/usr/bin/env python3
"""Prepare one approved AI Hub 538 TL177--TL180 label-only download."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from voice_coach.visual_feedback.models import (  # noqa: E402
    aihub538_p6_label_preparation as preparation_model,
)
from voice_coach.visual_feedback.services import (  # noqa: E402
    aihub538_p6_label_preparation as preparation_service,
)


AIHUB538_P6_REMEDIATION_LABEL_AUDIT_SHARDS = (
    preparation_model.AIHUB538_P6_REMEDIATION_LABEL_AUDIT_SHARDS
)
Aihub538P6LabelPreparationError = (
    preparation_service.Aihub538P6LabelPreparationError
)
Aihub538P6LabelPreparationService = (
    preparation_service.Aihub538P6LabelPreparationService
)


_SHARDS_BY_ROLE = {
    shard.label_role_component: shard
    for shard in AIHUB538_P6_REMEDIATION_LABEL_AUDIT_SHARDS
}


def _descriptor(value: str) -> int:
    try:
        descriptor = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "descriptor must be an integer >= 3"
        ) from None
    if descriptor < 3:
        raise argparse.ArgumentTypeError(
            "descriptor must be an integer >= 3"
        )
    return descriptor


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-directory", type=Path, required=True)
    parser.add_argument(
        "--label-role",
        choices=tuple(_SHARDS_BY_ROLE),
        required=True,
    )
    parser.add_argument("--identity-key-fd", type=_descriptor, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument(
        "--dataset-access-permission-confirmed",
        action="store_true",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        receipt = Aihub538P6LabelPreparationService(
            shard_spec=_SHARDS_BY_ROLE[arguments.label_role]
        ).run_from_completed_directory(
            input_directory=arguments.input_directory.absolute(),
            identity_key_descriptor=arguments.identity_key_fd,
            output_directory=arguments.output_directory.absolute(),
            dataset_access_permission_confirmed=(
                arguments.dataset_access_permission_confirmed
            ),
        )
        print(
            json.dumps(
                {
                    "status": "completed_remediation_label_preparation",
                    "label_role_component": receipt.label_role_component,
                    "label_file_key": receipt.label_file_key,
                    "label_member_count": receipt.label_member_count,
                    "actor_count": receipt.actor_count,
                    "attempt_count": receipt.attempt_count,
                    "metadata_excluded_label_count": (
                        receipt.metadata_excluded_label_count
                    ),
                    "receipt_sha256": receipt.receipt_sha256,
                    "source_partition_accessed": False,
                    "locked_partition_accessed": False,
                    "raw_identifiers_serialized": False,
                    "promotion_authorized": False,
                },
                ensure_ascii=True,
                sort_keys=True,
                indent=2,
            )
        )
        return 0
    except (Aihub538P6LabelPreparationError, OSError, ValueError) as error:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "reason_code": getattr(
                        error,
                        "reason_code",
                        "aihub538_p6_label_preparation_failed",
                    ),
                    "source_partition_accessed": False,
                    "locked_partition_accessed": False,
                    "promotion_authorized": False,
                },
                sort_keys=True,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
