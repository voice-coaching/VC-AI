#!/usr/bin/env python3
"""Audit a completed AI Hub 538 TL175 label-only download before TS175."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from voice_coach.visual_feedback.services.aihub538_label_capacity_audit import (  # noqa: E402
    Aihub538LabelCapacityAuditPipeline,
    Aihub538LabelCapacityPipelineError,
)


def _descriptor(value: str) -> int:
    try:
        descriptor = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("descriptor must be an integer >= 3") from None
    if descriptor < 3:
        raise argparse.ArgumentTypeError("descriptor must be an integer >= 3")
    return descriptor


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-directory", type=Path, required=True)
    parser.add_argument(
        "--baseline-admission-directory",
        type=Path,
        required=True,
        help="completed TS174 run with exact candidate-0/50/99 lineage files",
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
        receipt = Aihub538LabelCapacityAuditPipeline().run_from_completed_directory(
            input_directory=arguments.input_directory.absolute(),
            baseline_admission_directory=(
                arguments.baseline_admission_directory.absolute()
            ),
            identity_key_descriptor=arguments.identity_key_fd,
            output_directory=arguments.output_directory.absolute(),
            dataset_access_permission_confirmed=(
                arguments.dataset_access_permission_confirmed
            ),
        )
        print(
            json.dumps(
                {
                    "status": (
                        "completed_feasible"
                        if receipt.is_feasible
                        else "completed_infeasible"
                    ),
                    "label_count": receipt.label_count,
                    "actor_count": receipt.actor_count,
                    "attempt_count": receipt.attempt_count,
                    "samples_per_angle_per_partition": 8,
                    "minimum_actor_count_per_partition": 2,
                    "angle_capacity": [
                        item.to_json_object()
                        for item in receipt.angle_receipts
                    ],
                    "development_actor_count": (
                        receipt.development_actor_count
                    ),
                    "calibration_actor_count": (
                        receipt.calibration_actor_count
                    ),
                    "cross_shard_actor_intersection_count": (
                        receipt.cross_shard_actor_intersection_count
                    ),
                    "cross_shard_attempt_intersection_count": (
                        receipt.cross_shard_attempt_intersection_count
                    ),
                    "candidate_lip_reversed_bbox_unavailable_count": (
                        receipt.candidate_lip_reversed_bbox_unavailable_count
                    ),
                    "candidate_reversed_bbox_affected_label_count": (
                        receipt.candidate_reversed_bbox_affected_label_count
                    ),
                    "ts175_pair_admission_precondition_satisfied": (
                        receipt.is_feasible
                    ),
                    "source_download_authorized": receipt.is_feasible,
                    "next_label_role_component": (
                        receipt.to_json_object()[
                            "next_label_role_component"
                        ]
                    ),
                    "next_label_file_key": (
                        receipt.to_json_object()["next_label_file_key"]
                    ),
                    "capacity_audit_receipt_sha256": receipt.receipt_sha256,
                    "source_partition_accessed": False,
                    "partial_download_accessed": False,
                    "locked_partition_accessed": False,
                    "raw_identifiers_serialized": False,
                    "promotion_authorized": False,
                },
                ensure_ascii=True,
                sort_keys=True,
                indent=2,
            )
        )
        return 0 if receipt.is_feasible else 3
    except (Aihub538LabelCapacityPipelineError, OSError, ValueError) as error:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "reason_code": getattr(
                        error,
                        "reason_code",
                        "aihub538_label_capacity_audit_failed",
                    ),
                    "ts175_pair_admission_precondition_satisfied": False,
                    "source_partition_accessed": False,
                    "partial_download_accessed": False,
                    "locked_partition_accessed": False,
                    "promotion_authorized": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
