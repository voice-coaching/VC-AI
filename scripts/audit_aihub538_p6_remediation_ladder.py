#!/usr/bin/env python3
"""Audit the frozen TL177--TL180 P6 remediation ladder without source access."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from voice_coach.visual_feedback.services.aihub538_p6_remediation import (  # noqa: E402
    Aihub538P6RemediationAuditRequest,
    Aihub538P6RemediationAuditService,
    Aihub538P6RemediationError,
)
from voice_coach.visual_feedback.adapters.aihub538_decision_calibration_publish import (  # noqa: E402
    load_aihub538_decision_calibration_failure_artifact,
)
from voice_coach.visual_feedback.services.aihub538_decision_calibration import (  # noqa: E402
    Aihub538DecisionCalibrationError,
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
    parser.add_argument(
        "--design-label-fd",
        type=_descriptor,
        action="append",
        required=True,
        help="repeat exactly three times in TL174,TL175,TL176 order",
    )
    parser.add_argument(
        "--prior-label-fd",
        type=_descriptor,
        action="append",
        default=[],
        help="repeat 0--3 times for the already-audited contiguous prefix",
    )
    parser.add_argument(
        "--new-label-fd",
        type=_descriptor,
        action="append",
        required=True,
        help="the one exact next remediation label archive",
    )
    parser.add_argument(
        "--prior-prefix-receipt-fd",
        type=_descriptor,
        help="required exactly when one or more --prior-label-fd are supplied",
    )
    parser.add_argument("--identity-key-fd", type=_descriptor, required=True)
    parser.add_argument("--v3-failure-artifact", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument(
        "--dataset-access-permission-confirmed",
        action="store_true",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if len(arguments.design_label_fd) != 3:
        _parser().error("--design-label-fd must be repeated exactly 3 times")
    if not 0 <= len(arguments.prior_label_fd) <= 3:
        _parser().error("--prior-label-fd may be repeated at most 3 times")
    if len(arguments.new_label_fd) != 1:
        _parser().error("--new-label-fd must be supplied exactly once")
    if (len(arguments.prior_label_fd) == 0) != (
        arguments.prior_prefix_receipt_fd is None
    ):
        _parser().error(
            "--prior-prefix-receipt-fd is required exactly when "
            "--prior-label-fd is supplied"
        )
    try:
        if arguments.dataset_access_permission_confirmed is not True:
            raise Aihub538P6RemediationError(
                "aihub538_dataset_access_permission_not_confirmed"
            )
        design_failure = load_aihub538_decision_calibration_failure_artifact(
            arguments.v3_failure_artifact.absolute()
        )
        receipt = Aihub538P6RemediationAuditService().audit_first_feasible_prefix(
            Aihub538P6RemediationAuditRequest(
                design_used_label_zip_descriptors=tuple(
                    arguments.design_label_fd
                ),
                prior_candidate_label_zip_descriptors=tuple(
                    arguments.prior_label_fd
                ),
                new_candidate_label_zip_descriptor=arguments.new_label_fd[0],
                prior_prefix_receipt_descriptor=(
                    arguments.prior_prefix_receipt_fd
                ),
                identity_key_descriptor=arguments.identity_key_fd,
                output_directory=arguments.output_directory.absolute(),
                design_failure_artifact=design_failure,
                dataset_access_permission_confirmed=(
                    arguments.dataset_access_permission_confirmed
                ),
            )
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
                    "next_label_role_component": document[
                        "next_label_role_component"
                    ],
                    "next_label_file_key": document[
                        "next_label_file_key"
                    ],
                    "prior_design_actor_intersection_count": (
                        receipt.prior_design_actor_intersection_count
                    ),
                    "prior_design_attempt_intersection_count": (
                        receipt.prior_design_attempt_intersection_count
                    ),
                    "authorized_source_role_prefix": document[
                        "authorized_source_role_prefix"
                    ],
                    "authorized_source_file_key_prefix": document[
                        "authorized_source_file_key_prefix"
                    ],
                    "source_download_authorized": receipt.is_feasible,
                    "locked_partition_accessed": False,
                    "promotion_authorized": False,
                    "receipt_sha256": receipt.receipt_sha256,
                },
                sort_keys=True,
            )
        )
        return 0 if receipt.is_feasible else 3
    except (
        Aihub538DecisionCalibrationError,
        Aihub538P6RemediationError,
        OSError,
        TypeError,
        ValueError,
    ) as error:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "reason_code": getattr(
                        error,
                        "reason_code",
                        "aihub538_remediation_audit_failed",
                    ),
                    "source_download_authorized": False,
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
