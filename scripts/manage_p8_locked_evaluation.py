#!/usr/bin/env python3
"""Create, irreversibly consume, or reload aggregate-only P8 state.

This command has no dataset locator option and cannot open or download media.
Actual evaluation must inject a locked source evaluator into the application
service after the access transaction has been durably created.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from voice_coach.visual_feedback.adapters.p8_seal_store import (  # noqa: E402
    P8SealStateError,
    P8SealStateStore,
)
from voice_coach.visual_feedback.errors import (  # noqa: E402
    VisualFeedbackContractError,
)
from voice_coach.visual_feedback.models.integrity import (  # noqa: E402
    canonical_json_bytes,
)
from voice_coach.visual_feedback.models.artifacts import (  # noqa: E402
    VisualCalibrationArtifact,
    VisualClaimCatalog,
)
from voice_coach.visual_feedback.models.enums import (  # noqa: E402
    CalibrationScope,
)
from voice_coach.visual_feedback.models.p8_locked_evaluation import (  # noqa: E402
    P8ReadZeroSeal,
)
from voice_coach.visual_feedback.models.runtime_eligibility import (  # noqa: E402
    AutomatedP6PromotionReceipt,
    RuntimeVisualClaimPolicy,
    VisualRuntimePipelineIdentity,
)
from voice_coach.visual_feedback.models.runtime_policy_artifact import (  # noqa: E402
    P8RuntimePolicyArtifact,
)
from voice_coach.visual_feedback.services.p8_locked_evaluation import (  # noqa: E402
    P8LockedEvaluationService,
)

_MAX_PINNED_FILE_BYTES = 256 * 1024 * 1024
_MAX_TYPED_JSON_BYTES = 4 * 1024 * 1024


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage the aggregate-only one-shot P8 seal state",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    seal_parser = subparsers.add_parser(
        "seal", help="create an access_count=0 read-zero seal"
    )
    seal_parser.add_argument("--state-dir", type=Path, required=True)
    seal_parser.add_argument("--seal-id", required=True)
    seal_parser.add_argument("--protocol-revision", required=True)
    seal_parser.add_argument("--source-revision", required=True)
    seal_parser.add_argument("--p6-evidence-receipt-file", type=Path, required=True)
    seal_parser.add_argument("--calibration-artifact-file", type=Path, required=True)
    seal_parser.add_argument("--claim-catalog-file", type=Path, required=True)
    seal_parser.add_argument("--claim-policy-file", type=Path, required=True)
    seal_parser.add_argument("--pipeline-identity-file", type=Path, required=True)
    seal_parser.add_argument("--decoder-code-file", type=Path, required=True)
    seal_parser.add_argument("--tracker-code-file", type=Path, required=True)
    seal_parser.add_argument(
        "--tracker-adapter-code-file", type=Path, required=True
    )
    seal_parser.add_argument("--landmark-model-file", type=Path, required=True)
    seal_parser.add_argument("--normalizer-code-file", type=Path, required=True)
    seal_parser.add_argument("--cue-extractor-code-file", type=Path, required=True)
    seal_parser.add_argument(
        "--video-qc-measurement-code-file", type=Path, required=True
    )
    seal_parser.add_argument(
        "--video-qc-measurement-policy-file", type=Path, required=True
    )
    seal_parser.add_argument(
        "--video-qc-decision-code-file", type=Path, required=True
    )
    seal_parser.add_argument(
        "--video-qc-decision-policy-file", type=Path, required=True
    )
    seal_parser.add_argument(
        "--av-sync-measurement-code-file", type=Path, required=True
    )
    seal_parser.add_argument(
        "--av-sync-measurement-policy-file", type=Path, required=True
    )
    seal_parser.add_argument(
        "--av-sync-decision-code-file", type=Path, required=True
    )
    seal_parser.add_argument(
        "--av-sync-decision-policy-file", type=Path, required=True
    )
    seal_parser.add_argument("--phone-window-code-file", type=Path, required=True)
    seal_parser.add_argument(
        "--phone-window-policy-file", type=Path, required=True
    )
    seal_parser.add_argument("--sealed-at-utc", default=None)

    consume_parser = subparsers.add_parser(
        "consume-without-source-open",
        help="irreversibly consume the seal and emit a non-execution report",
    )
    consume_parser.add_argument("--state-dir", type=Path, required=True)
    consume_parser.add_argument("--expected-seal-sha256", required=True)
    consume_parser.add_argument("--transaction-id", required=True)
    consume_parser.add_argument("--report-id", required=True)
    consume_parser.add_argument("--consumed-at-utc", default=None)
    consume_parser.add_argument(
        "--confirm-irreversible-consumption",
        action="store_true",
        required=True,
        help="acknowledge that no retry will be authorized",
    )

    inspect_parser = subparsers.add_parser(
        "reload", help="freshly parse and self-digest-check durable state"
    )
    inspect_parser.add_argument("--state-dir", type=Path, required=True)
    inspect_parser.add_argument(
        "state", choices=("seal", "access", "manifest", "metrics", "report")
    )
    return parser


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_regular_file(path: Path) -> str:
    """Hash one stable regular file through a read-only, no-follow descriptor."""

    if ".." in path.parts:
        raise VisualFeedbackContractError("pinned file path must be normalized")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise VisualFeedbackContractError("pinned file is unavailable") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise VisualFeedbackContractError("pinned input must be a regular file")
        if before.st_size <= 0 or before.st_size > _MAX_PINNED_FILE_BYTES:
            raise VisualFeedbackContractError("pinned input size is outside policy")
        digest = hashlib.sha256()
        total_bytes = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            total_bytes += len(chunk)
        after = os.fstat(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if total_bytes != before.st_size or before_identity != after_identity:
            raise VisualFeedbackContractError("pinned input changed while hashing")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _read_strict_json_regular_file(path: Path) -> object:
    """Read one stable no-follow JSON file and reject duplicate object keys."""

    if ".." in path.parts:
        raise VisualFeedbackContractError("typed JSON path must be normalized")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise VisualFeedbackContractError("typed JSON input is unavailable") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise VisualFeedbackContractError(
                "typed JSON input must be a regular file"
            )
        if before.st_size <= 0 or before.st_size > _MAX_TYPED_JSON_BYTES:
            raise VisualFeedbackContractError(
                "typed JSON input size is outside policy"
            )
        payload = bytearray()
        while len(payload) <= _MAX_TYPED_JSON_BYTES:
            chunk = os.read(
                descriptor,
                min(64 * 1024, _MAX_TYPED_JSON_BYTES + 1 - len(payload)),
            )
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if len(payload) != before.st_size or before_identity != after_identity:
            raise VisualFeedbackContractError(
                "typed JSON input changed while reading"
            )
    finally:
        os.close(descriptor)

    def reject_duplicate_keys(pairs):  # noqa: ANN001
        document: dict[str, object] = {}
        for key, value in pairs:
            if key in document:
                raise VisualFeedbackContractError(
                    "typed JSON input contains duplicate keys"
                )
            document[key] = value
        return document

    try:
        return json.loads(
            bytes(payload),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                VisualFeedbackContractError(
                    "typed JSON input contains a non-finite number"
                )
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VisualFeedbackContractError("typed JSON input is invalid") from error


def _emit(document: object, *, stream) -> None:  # noqa: ANN001
    stream.write(canonical_json_bytes(document).decode("utf-8"))
    stream.write("\n")


def _seal(arguments: argparse.Namespace) -> dict[str, object]:
    store = P8SealStateStore(arguments.state_dir)
    p6_receipt = AutomatedP6PromotionReceipt.from_json_object(
        _read_strict_json_regular_file(arguments.p6_evidence_receipt_file)
    )
    calibration = VisualCalibrationArtifact.from_json_object(
        _read_strict_json_regular_file(arguments.calibration_artifact_file)
    )
    catalog = VisualClaimCatalog.from_json_object(
        _read_strict_json_regular_file(arguments.claim_catalog_file)
    )
    claim_policy = RuntimeVisualClaimPolicy.from_json_object(
        _read_strict_json_regular_file(arguments.claim_policy_file)
    )
    pipeline_identity = VisualRuntimePipelineIdentity.from_json_object(
        _read_strict_json_regular_file(arguments.pipeline_identity_file)
    )
    video_qc_measurement_policy = P8RuntimePolicyArtifact.from_json_object(
        _read_strict_json_regular_file(
            arguments.video_qc_measurement_policy_file
        )
    )
    video_qc_decision_policy = P8RuntimePolicyArtifact.from_json_object(
        _read_strict_json_regular_file(arguments.video_qc_decision_policy_file)
    )
    av_sync_measurement_policy = P8RuntimePolicyArtifact.from_json_object(
        _read_strict_json_regular_file(
            arguments.av_sync_measurement_policy_file
        )
    )
    av_sync_decision_policy = P8RuntimePolicyArtifact.from_json_object(
        _read_strict_json_regular_file(arguments.av_sync_decision_policy_file)
    )
    phone_window_policy = P8RuntimePolicyArtifact.from_json_object(
        _read_strict_json_regular_file(arguments.phone_window_policy_file)
    )
    _validate_semantic_evidence_pins(
        p6_receipt=p6_receipt,
        calibration=calibration,
        catalog=catalog,
        claim_policy=claim_policy,
        pipeline_identity=pipeline_identity,
        video_qc_measurement_policy=video_qc_measurement_policy,
        video_qc_decision_policy=video_qc_decision_policy,
        av_sync_measurement_policy=av_sync_measurement_policy,
        av_sync_decision_policy=av_sync_decision_policy,
        phone_window_policy=phone_window_policy,
    )
    _validate_raw_pipeline_pins(arguments, pipeline_identity)
    seal = P8ReadZeroSeal.issue(
        seal_id=arguments.seal_id,
        protocol_revision=arguments.protocol_revision,
        source_revision=arguments.source_revision,
        p6_evidence_receipt_sha256=p6_receipt.receipt_sha256,
        calibration_artifact_sha256=calibration.artifact_sha256,
        claim_catalog_sha256=catalog.catalog_sha256,
        claim_policy_sha256=claim_policy.policy_sha256,
        media_capture_contract_id=(
            pipeline_identity.media_capture_contract_id
        ),
        media_capture_contract_sha256=(
            pipeline_identity.media_capture_contract_sha256
        ),
        visual_observation_contract_id=(
            pipeline_identity.visual_observation_contract_id
        ),
        visual_observation_contract_sha256=(
            pipeline_identity.visual_observation_contract_sha256
        ),
        decoder_code_sha256=pipeline_identity.decoder_sha256,
        tracker_code_sha256=pipeline_identity.tracker_sha256,
        tracker_adapter_code_sha256=(
            pipeline_identity.tracker_adapter_sha256
        ),
        landmark_model_sha256=pipeline_identity.landmark_model_sha256,
        normalizer_code_sha256=pipeline_identity.normalizer_sha256,
        cue_extractor_code_sha256=pipeline_identity.cue_extractor_sha256,
        video_qc_measurement_code_sha256=(
            pipeline_identity.video_qc_measurement_code_sha256
        ),
        video_qc_measurement_policy_sha256=(
            video_qc_measurement_policy.policy_sha256
        ),
        video_qc_decision_code_sha256=(
            pipeline_identity.video_qc_decision_code_sha256
        ),
        video_qc_decision_policy_revision=(
            video_qc_decision_policy.policy_revision
        ),
        video_qc_decision_policy_sha256=(
            video_qc_decision_policy.policy_sha256
        ),
        av_sync_measurement_code_sha256=(
            pipeline_identity.av_sync_measurement_code_sha256
        ),
        av_sync_measurement_policy_sha256=(
            av_sync_measurement_policy.policy_sha256
        ),
        av_sync_decision_code_sha256=(
            pipeline_identity.av_sync_decision_code_sha256
        ),
        av_sync_decision_policy_revision=(
            av_sync_decision_policy.policy_revision
        ),
        av_sync_decision_policy_sha256=av_sync_decision_policy.policy_sha256,
        phone_window_code_sha256=pipeline_identity.phone_window_code_sha256,
        phone_window_policy_revision=phone_window_policy.policy_revision,
        phone_window_policy_sha256=phone_window_policy.policy_sha256,
        sealed_at_utc=arguments.sealed_at_utc or _utc_now(),
    )
    P8LockedEvaluationService(store).create_read_zero_seal(seal)
    return seal.to_json_object()


def _validate_semantic_evidence_pins(
    *,
    p6_receipt: AutomatedP6PromotionReceipt,
    calibration: VisualCalibrationArtifact,
    catalog: VisualClaimCatalog,
    claim_policy: RuntimeVisualClaimPolicy,
    pipeline_identity: VisualRuntimePipelineIdentity,
    video_qc_measurement_policy: P8RuntimePolicyArtifact,
    video_qc_decision_policy: P8RuntimePolicyArtifact,
    av_sync_measurement_policy: P8RuntimePolicyArtifact,
    av_sync_decision_policy: P8RuntimePolicyArtifact,
    phone_window_policy: P8RuntimePolicyArtifact,
) -> None:
    """Reject a seal whose typed producer evidence cannot later be replayed."""

    if calibration.scope is not CalibrationScope.CALIBRATED:
        raise VisualFeedbackContractError("P8 calibration artifact is not calibrated")
    if catalog.scope is not CalibrationScope.CALIBRATED:
        raise VisualFeedbackContractError("P8 claim catalog is not calibrated")
    expected_equalities = (
        (
            p6_receipt.calibration_artifact_sha256,
            calibration.artifact_sha256,
        ),
        (p6_receipt.catalog_sha256, catalog.catalog_sha256),
        (p6_receipt.policy_sha256, claim_policy.policy_sha256),
        (p6_receipt.pipeline_identity_sha256, pipeline_identity.identity_sha256),
        (
            p6_receipt.media_capture_contract_id,
            pipeline_identity.media_capture_contract_id,
        ),
        (
            p6_receipt.media_capture_contract_sha256,
            pipeline_identity.media_capture_contract_sha256,
        ),
        (
            p6_receipt.visual_observation_contract_id,
            pipeline_identity.visual_observation_contract_id,
        ),
        (
            p6_receipt.visual_observation_contract_sha256,
            pipeline_identity.visual_observation_contract_sha256,
        ),
        (p6_receipt.population_id_sha256, pipeline_identity.population_id_sha256),
        (p6_receipt.context_contract_sha256, pipeline_identity.context_contract_sha256),
        (
            calibration.capture_contract_id,
            pipeline_identity.visual_observation_contract_id,
        ),
        (calibration.population_id_sha256, pipeline_identity.population_id_sha256),
    )
    if any(left != right for left, right in expected_equalities):
        raise VisualFeedbackContractError("P8 typed producer evidence pins differ")
    if not claim_policy.allowed_cue_ids or not catalog.claims:
        raise VisualFeedbackContractError("P8 claim evidence is empty")
    if (
        video_qc_measurement_policy.policy_kind != "video_qc"
        or video_qc_measurement_policy.policy_sha256
        != pipeline_identity.video_qc_measurement_policy_sha256
    ):
        raise VisualFeedbackContractError(
            "P8 video QC measurement policy pin differs"
        )
    if (
        video_qc_decision_policy.policy_kind != "video_qc_decision"
        or video_qc_decision_policy.policy_revision
        != pipeline_identity.video_qc_decision_policy_revision
        or video_qc_decision_policy.policy_sha256
        != pipeline_identity.video_qc_decision_policy_sha256
        or getattr(
            video_qc_decision_policy.policy_document,
            "expected_measurement_policy_sha256",
            None,
        )
        != video_qc_measurement_policy.policy_sha256
    ):
        raise VisualFeedbackContractError(
            "P8 video QC decision policy pin differs"
        )
    if (
        av_sync_measurement_policy.policy_kind != "av_sync"
        or av_sync_measurement_policy.policy_sha256
        != pipeline_identity.av_sync_measurement_policy_sha256
    ):
        raise VisualFeedbackContractError(
            "P8 A/V sync measurement policy pin differs"
        )
    if (
        av_sync_decision_policy.policy_kind != "av_sync_decision"
        or av_sync_decision_policy.policy_revision
        != pipeline_identity.av_sync_decision_policy_revision
        or av_sync_decision_policy.policy_sha256
        != pipeline_identity.av_sync_decision_policy_sha256
        or getattr(
            av_sync_decision_policy.policy_document,
            "expected_measurement_policy_sha256",
            None,
        )
        != av_sync_measurement_policy.policy_sha256
    ):
        raise VisualFeedbackContractError(
            "P8 A/V sync decision policy pin differs"
        )
    if (
        phone_window_policy.policy_kind != "phone_window"
        or phone_window_policy.policy_revision
        != pipeline_identity.phone_window_policy_revision
        or phone_window_policy.policy_sha256
        != pipeline_identity.phone_window_policy_sha256
    ):
        raise VisualFeedbackContractError(
            "P8 phone-window semantic policy pin differs"
        )


def _validate_raw_pipeline_pins(
    arguments: argparse.Namespace,
    pipeline_identity: VisualRuntimePipelineIdentity,
) -> None:
    """Cross-check executable/model bytes against the strict pipeline identity."""

    actual = {
        "decoder": _sha256_regular_file(arguments.decoder_code_file),
        "tracker": _sha256_regular_file(arguments.tracker_code_file),
        "tracker_adapter": _sha256_regular_file(
            arguments.tracker_adapter_code_file
        ),
        "landmark_model": _sha256_regular_file(arguments.landmark_model_file),
        "normalizer": _sha256_regular_file(arguments.normalizer_code_file),
        "cue_extractor": _sha256_regular_file(arguments.cue_extractor_code_file),
        "video_qc_measurement_code": _sha256_regular_file(
            arguments.video_qc_measurement_code_file
        ),
        "video_qc_decision_code": _sha256_regular_file(
            arguments.video_qc_decision_code_file
        ),
        "av_sync_measurement_code": _sha256_regular_file(
            arguments.av_sync_measurement_code_file
        ),
        "av_sync_decision_code": _sha256_regular_file(
            arguments.av_sync_decision_code_file
        ),
        "phone_window_code": _sha256_regular_file(
            arguments.phone_window_code_file
        ),
    }
    expected = {
        "decoder": pipeline_identity.decoder_sha256,
        "tracker": pipeline_identity.tracker_sha256,
        "tracker_adapter": pipeline_identity.tracker_adapter_sha256,
        "landmark_model": pipeline_identity.landmark_model_sha256,
        "normalizer": pipeline_identity.normalizer_sha256,
        "cue_extractor": pipeline_identity.cue_extractor_sha256,
        "video_qc_measurement_code": (
            pipeline_identity.video_qc_measurement_code_sha256
        ),
        "video_qc_decision_code": (
            pipeline_identity.video_qc_decision_code_sha256
        ),
        "av_sync_measurement_code": (
            pipeline_identity.av_sync_measurement_code_sha256
        ),
        "av_sync_decision_code": (
            pipeline_identity.av_sync_decision_code_sha256
        ),
        "phone_window_code": pipeline_identity.phone_window_code_sha256,
    }
    if actual != expected:
        raise VisualFeedbackContractError("P8 raw pipeline pin differs")


def _consume_without_source_open(arguments: argparse.Namespace) -> dict[str, object]:
    report = P8LockedEvaluationService(
        P8SealStateStore(arguments.state_dir)
    ).consume_without_source_open(
        expected_seal_sha256=arguments.expected_seal_sha256,
        transaction_id=arguments.transaction_id,
        consumed_at_utc=arguments.consumed_at_utc or _utc_now(),
        report_id=arguments.report_id,
    )
    return report.to_json_object()


def _reload(arguments: argparse.Namespace) -> dict[str, object]:
    store = P8SealStateStore(arguments.state_dir)
    if arguments.state == "seal":
        return store.load_read_zero_seal().to_json_object()
    if arguments.state == "access":
        return store.load_access_transaction().to_json_object()
    if arguments.state == "manifest":
        return store.load_locked_source_manifest().to_json_object()
    if arguments.state == "metrics":
        return store.load_aggregate_metrics().to_json_object()
    return store.load_locked_report().to_json_object()


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "seal":
            document = _seal(arguments)
        elif arguments.command == "consume-without-source-open":
            document = _consume_without_source_open(arguments)
        else:
            document = _reload(arguments)
    except (OSError, P8SealStateError, VisualFeedbackContractError):
        _emit(
            {
                "status": "failed_closed",
                "reason_codes": ["p8_locked_state_operation_failed"],
            },
            stream=sys.stderr,
        )
        return 2
    _emit(document, stream=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
