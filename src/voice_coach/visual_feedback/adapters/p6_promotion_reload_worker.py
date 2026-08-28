"""Fresh-process validator for one automated P6 promotion candidate.

The worker emits aggregate digests only.  It never opens AI Hub media or label
archives and it does not retain input locators in its receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from ..errors import VisualFeedbackContractError
from ..models.artifacts import VisualCalibrationArtifact
from ..models.integrity import canonical_json_bytes, sha256_document
from ..models.p6_geometry import P6GeometryEvidenceBundle
from ..models.p6_phone_reference import P6PhoneReferenceEvidenceBundle
from ..models.runtime_eligibility import (
    AutomatedP6PromotionReceipt,
    VisualRuntimePipelineIdentity,
)
from ..models.runtime_policy_artifact import (
    P8RuntimePolicyArtifact,
    RuntimeDecisionPolicyEvidence,
)
from ..services.runtime_eligibility import (
    validate_automated_p6_promotion_receipt,
)

P6_PROMOTION_RELOAD_RECEIPT_SCHEMA_VERSION = (
    "voice_coach.p6_automated_promotion_reload_receipt.v2"
)


def _read_json(path: Path) -> object:
    def reject_duplicates(pairs):  # noqa: ANN001
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise VisualFeedbackContractError(
                    "P6 promotion reload input contains duplicate keys"
                )
            result[key] = value
        return result

    try:
        return json.loads(
            path.read_bytes(),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                VisualFeedbackContractError(
                    "P6 promotion reload input contains non-finite JSON"
                )
            ),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VisualFeedbackContractError(
            "P6 promotion reload input is unavailable"
        ) from error


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise VisualFeedbackContractError(
            "P6 promotion reload code is unavailable"
        ) from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--promotion-receipt", type=Path, required=True)
    parser.add_argument("--geometry-bundle", type=Path, required=True)
    parser.add_argument("--phone-reference-bundle", type=Path, required=True)
    parser.add_argument("--calibration-artifact", type=Path, required=True)
    parser.add_argument("--video-qc-decision-policy", type=Path, required=True)
    parser.add_argument("--av-sync-decision-policy", type=Path, required=True)
    parser.add_argument("--decision-policy-evidence", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    identity = VisualRuntimePipelineIdentity.from_json_object(
        _read_json(arguments.identity)
    )
    promotion = AutomatedP6PromotionReceipt.from_json_object(
        _read_json(arguments.promotion_receipt)
    )
    geometry = P6GeometryEvidenceBundle.from_json_object(
        _read_json(arguments.geometry_bundle)
    )
    phone_reference = P6PhoneReferenceEvidenceBundle.from_json_object(
        _read_json(arguments.phone_reference_bundle)
    )
    calibration = VisualCalibrationArtifact.from_json_object(
        _read_json(arguments.calibration_artifact)
    )
    video_qc_decision_policy = P8RuntimePolicyArtifact.from_json_object(
        _read_json(arguments.video_qc_decision_policy)
    )
    av_sync_decision_policy = P8RuntimePolicyArtifact.from_json_object(
        _read_json(arguments.av_sync_decision_policy)
    )
    decision_policy_evidence = RuntimeDecisionPolicyEvidence.from_json_object(
        _read_json(arguments.decision_policy_evidence)
    )
    validate_automated_p6_promotion_receipt(
        receipt=promotion,
        geometry_bundle=geometry,
        phone_reference_bundle=phone_reference,
        calibration_artifact=calibration,
        pipeline_identity=identity,
        video_qc_decision_policy=video_qc_decision_policy,
        av_sync_decision_policy=av_sync_decision_policy,
        decision_policy_evidence=decision_policy_evidence,
    )
    service_path = Path(__file__).parents[1] / "services/runtime_eligibility.py"
    unsigned = {
        "schema_version": P6_PROMOTION_RELOAD_RECEIPT_SCHEMA_VERSION,
        "pipeline_identity_sha256": identity.identity_sha256,
        "promotion_receipt_sha256": promotion.receipt_sha256,
        "geometry_bundle_sha256": geometry.bundle_sha256,
        "phone_reference_bundle_sha256": phone_reference.bundle_sha256,
        "calibration_artifact_sha256": calibration.artifact_sha256,
        "video_qc_decision_policy_artifact_sha256": (
            video_qc_decision_policy.artifact_sha256
        ),
        "av_sync_decision_policy_artifact_sha256": (
            av_sync_decision_policy.artifact_sha256
        ),
        "decision_policy_evidence_receipt_sha256": (
            decision_policy_evidence.receipt_sha256
        ),
        "worker_sha256": _sha256_file(Path(__file__)),
        "validator_service_sha256": _sha256_file(service_path),
        "fresh_process": True,
        "strict_typed_reload": True,
        "p6_promotion_reproduced": True,
        "p6_automated_promotion_authorized": True,
        "p8_promotion_authorized": False,
        "user_visual_runtime_authorized": False,
    }
    receipt = {**unsigned, "receipt_sha256": sha256_document(unsigned)}
    sys.stdout.buffer.write(canonical_json_bytes(receipt) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
