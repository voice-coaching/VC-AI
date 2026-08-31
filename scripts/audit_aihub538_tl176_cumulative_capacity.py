#!/usr/bin/env python3
"""Issue v5 post-exclusion authorization for TL174+TL175+TL176 labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from voice_coach.visual_feedback.models.aihub538_nonlocked_shards import (  # noqa: E402
    AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS,
)
from voice_coach.visual_feedback.services.aihub538_label_capacity_audit import (  # noqa: E402
    Aihub538LabelCapacityAuditPipeline,
    Aihub538LabelCapacityPipelineError,
)


def _descriptor(value: str) -> int:
    try:
        descriptor = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "descriptor must be an integer >= 3"
        ) from None
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
    )
    parser.add_argument(
        "--prior-prefix-audit-directory",
        type=Path,
        required=True,
        help="completed_infeasible TL175 v4 audit run",
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
        receipt = Aihub538LabelCapacityAuditPipeline(
            audit_shard_spec=AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[1]
        ).run_from_completed_directory(
            input_directory=arguments.input_directory.absolute(),
            baseline_admission_directory=(
                arguments.baseline_admission_directory.absolute()
            ),
            prior_prefix_audit_directory=(
                arguments.prior_prefix_audit_directory.absolute()
            ),
            identity_key_descriptor=arguments.identity_key_fd,
            output_directory=arguments.output_directory.absolute(),
            dataset_access_permission_confirmed=(
                arguments.dataset_access_permission_confirmed
            ),
        )
        document = receipt.to_json_object()
        print(
            json.dumps(
                {
                    "status": document["status"],
                    "evaluated_label_prefix": document[
                        "evaluated_label_prefix"
                    ],
                    "evaluated_label_file_key_prefix": document[
                        "evaluated_label_file_key_prefix"
                    ],
                    "label_count": receipt.label_count,
                    "actor_count": receipt.actor_count,
                    "attempt_count": receipt.attempt_count,
                    "development_actor_count": (
                        receipt.development_actor_count
                    ),
                    "calibration_actor_count": (
                        receipt.calibration_actor_count
                    ),
                    "cross_shard_path_collision_count": (
                        receipt.cross_shard_path_collision_count
                    ),
                    "cross_shard_actor_intersection_count": (
                        receipt.cross_shard_actor_intersection_count
                    ),
                    "cross_shard_attempt_intersection_count": (
                        receipt.cross_shard_attempt_intersection_count
                    ),
                    "post_exclusion_angle_capacity": [
                        item.to_json_object()
                        for item in receipt.post_exclusion_angle_receipts
                    ],
                    "prefix_bbox_unavailable_counts": [
                        {
                            "label_role_component": (
                                item.label_role_component
                            ),
                            "face": (
                                item.face_reversed_bbox_unavailable_count
                            ),
                            "lip": item.lip_reversed_bbox_unavailable_count,
                            "affected_labels": (
                                item.reversed_bbox_affected_label_count
                            ),
                        }
                        for item in receipt.prefix_lineage
                    ],
                    "source_download_authorized": receipt.is_feasible,
                    "historical_capacity_receipt_sha256": (
                        receipt.historical_capacity_receipt_sha256
                    ),
                    "metadata_excluded_label_count": (
                        receipt.metadata_excluded_label_count
                    ),
                    "authorized_source_role_prefix": document[
                        "authorized_source_role_prefix"
                    ],
                    "authorized_source_file_key_prefix": document[
                        "authorized_source_file_key_prefix"
                    ],
                    "label_selection_authorization_receipt_sha256": (
                        receipt.receipt_sha256
                    ),
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
                        "aihub538_cumulative_capacity_audit_failed",
                    ),
                    "source_download_authorized": False,
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
