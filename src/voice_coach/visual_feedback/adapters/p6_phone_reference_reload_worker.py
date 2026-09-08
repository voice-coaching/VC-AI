"""Fresh-process strict parser for aggregate-only P6B producer evidence."""

from __future__ import annotations

import json
import sys

from ..errors import VisualFeedbackContractError
from ..models.integrity import sha256_document
from ..models.p6_phone_reference import P6PhoneReferenceProducerEvidence


LOADER_REVISION = "p6_phone_reference_strict_loader_v2"


def main() -> int:
    try:
        document = json.load(sys.stdin)
        producer = P6PhoneReferenceProducerEvidence.from_json_object(document)
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        VisualFeedbackContractError,
        TypeError,
        ValueError,
    ):
        sys.stdout.write(
            '{"reason_code":"p6b_reload_validation_failed","status":"failed_closed"}\n'
        )
        return 2
    response = {
        "status": "completed",
        "loader_revision": LOADER_REVISION,
        "producer_sha256": producer.producer_sha256,
        "producer_document_sha256": sha256_document(producer.to_json_object()),
        "parity_receipt_sha256": producer.runtime_parity_receipt.receipt_sha256,
        "calibration_artifact_sha256": producer.calibration_artifact.artifact_sha256,
        "catalog_sha256": producer.claim_catalog.catalog_sha256,
        "policy_sha256": producer.claim_policy.policy_sha256,
    }
    sys.stdout.write(
        json.dumps(response, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
