#!/usr/bin/env python3
"""Build immutable automated-P6 runtime evidence from the admitted v2 bundles.

This command is aggregate-only.  It reads no AI Hub source archive and emits no
actor, attempt, path, frame, or landmark values.  It does not authorize P8 or
user-visible runtime claims.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

from voice_coach.pronunciation_attempts.phone_window import PhoneWindowDecodePolicy
from voice_coach.pronunciation_attempts.adapters.lip_audio_av_sync import (
    MEASUREMENT_REVISION as AV_SYNC_MEASUREMENT_REVISION,
)
from voice_coach.pronunciation_attempts.adapters.video_qc import (
    MEASUREMENT_REVISION as VIDEO_QC_MEASUREMENT_REVISION,
)
from voice_coach.visual_feedback.errors import VisualFeedbackContractError
from voice_coach.visual_feedback.models.artifacts import VisualCalibrationArtifact
from voice_coach.visual_feedback.models.integrity import (
    canonical_json_bytes,
    require_exact_keys,
    require_mapping,
    require_sha256,
    sha256_document,
)
from voice_coach.visual_feedback.models.p6_geometry import P6GeometryEvidenceBundle
from voice_coach.visual_feedback.models.p6_phone_reference import (
    P6_PHONE_REFERENCE_BUNDLE_SCHEMA_VERSION,
    P6_PHONE_REFERENCE_EXPECTED_PHONE,
    P6PhoneReferenceEvidenceBundle,
)
from voice_coach.visual_feedback.models.runtime_eligibility import (
    AutomatedP6PromotionReceipt,
    VisualRuntimePipelineIdentity,
)
from voice_coach.visual_feedback.models.runtime_policy_artifact import (
    AvSyncSemanticPolicy,
    P8RuntimePolicyArtifact,
    RuntimeDecisionPolicyEvidence,
    PhoneWindowSemanticPolicy,
    VideoQcSemanticPolicy,
)
from voice_coach.visual_feedback.services.runtime_eligibility import (
    build_automated_p6_promotion_receipt,
)


_MAX_JSON_BYTES = 16 * 1024 * 1024
_MAX_PIN_BYTES = 256 * 1024 * 1024
_MEDIA_CAPTURE_CONTRACT_ID = "local_allowlisted_video_aac_mp4_v2"
_CONTEXT_ID = "internal_video_cli"
_REVOCATION_SCHEMA = "voice_coach.p6_phone_reference_supersession.v1"


def _parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    artifact_root = root / "artifacts/visual_feedback/p6"
    parser = argparse.ArgumentParser(
        description="Build fresh-reloaded P6 runtime promotion evidence"
    )
    parser.add_argument("--repository-root", type=Path, default=root)
    parser.add_argument("--output-dir", type=Path, default=artifact_root)
    parser.add_argument(
        "--geometry-bundle",
        type=Path,
        default=artifact_root / "aihub68_geometry_evidence_bundle_v2.json",
    )
    parser.add_argument(
        "--phone-reference-bundle",
        type=Path,
        default=artifact_root / "aihub68_phone_reference_bundle_v2.json",
    )
    parser.add_argument(
        "--invalid-v1-phone-reference-bundle",
        type=Path,
        default=artifact_root / "aihub68_phone_reference_bundle_v1.json",
    )
    parser.add_argument(
        "--landmark-model",
        type=Path,
        default=root / "models/mediapipe/face_landmarker.task",
    )
    parser.add_argument(
        "--video-qc-decision-policy",
        type=Path,
        default=(
            artifact_root
            / (
                "aihub538_ts174_tl174_ts175_tl175_ts176_tl176_"
                "video_qc_decision_policy_v1.json"
            )
        ),
    )
    parser.add_argument(
        "--av-sync-decision-policy",
        type=Path,
        default=(
            artifact_root
            / (
                "aihub538_ts174_tl174_ts175_tl175_ts176_tl176_"
                "av_sync_decision_policy_v1.json"
            )
        ),
    )
    parser.add_argument(
        "--decision-policy-evidence",
        type=Path,
        default=(
            artifact_root
            / (
                "aihub538_ts174_tl174_ts175_tl175_ts176_tl176_"
                "decision_policy_evidence_v2.json"
            )
        ),
    )
    return parser


def _stable_read(
    path: Path,
    *,
    maximum_bytes: int,
    required_mode: int | None = None,
) -> bytes:
    if ".." in path.parts:
        raise VisualFeedbackContractError("input path must be normalized")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise VisualFeedbackContractError("pinned input is unavailable") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise VisualFeedbackContractError("pinned input must be a regular file")
        if required_mode is not None and stat.S_IMODE(before.st_mode) != required_mode:
            raise VisualFeedbackContractError(
                "calibrated decision evidence must be mode 0400"
            )
        if before.st_size <= 0 or before.st_size > maximum_bytes:
            raise VisualFeedbackContractError("pinned input size is outside policy")
        payload = bytearray()
        while len(payload) <= maximum_bytes:
            chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(payload) != before.st_size
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise VisualFeedbackContractError("pinned input changed while reading")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _strict_json(path: Path, *, require_frozen: bool = False) -> object:
    payload = _stable_read(
        path,
        maximum_bytes=_MAX_JSON_BYTES,
        required_mode=0o400 if require_frozen else None,
    )

    def reject_duplicates(pairs):  # noqa: ANN001
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise VisualFeedbackContractError("JSON contains duplicate keys")
            result[key] = value
        return result

    try:
        return json.loads(
            payload,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                VisualFeedbackContractError("JSON contains a non-finite number")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VisualFeedbackContractError("pinned JSON is invalid") from error


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(
        _stable_read(path, maximum_bytes=_MAX_PIN_BYTES)
    ).hexdigest()


def _write_exclusive(path: Path, payload: bytes, *, mode: int = 0o400) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, mode)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        os.fchmod(descriptor, mode)
    finally:
        os.close(descriptor)


def _policy_artifacts(
    *, context_contract_sha256: str
) -> tuple[
    P8RuntimePolicyArtifact,
    P8RuntimePolicyArtifact,
    P8RuntimePolicyArtifact,
    PhoneWindowDecodePolicy,
]:
    video_qc = P8RuntimePolicyArtifact.issue(
        policy_kind="video_qc",
        policy_revision="video_qc_measurement_v1",
        policy_document=VideoQcSemanticPolicy(
            underexposed_luma_max_8bit=16,
            overexposed_luma_min_8bit=239,
            lip_roi_padding_fraction=0.05,
            minimum_single_face_frames=6,
            maximum_total_rgb_bytes=64 * 1024 * 1024,
        ),
    )
    av_sync = P8RuntimePolicyArtifact.issue(
        policy_kind="av_sync",
        policy_revision="lip_audio_av_sync_policy_v1",
        policy_document=AvSyncSemanticPolicy(
            maximum_absolute_offset_ms=300,
            offset_step_ms=20,
            energy_window_ms=40,
            minimum_landmark_frames=6,
            maximum_anchor_count=4,
        ),
    )
    phone_policy = PhoneWindowDecodePolicy(
        policy_revision="selected_phone_rgb24_v1",
        expected_av_sync_policy_sha256=av_sync.policy_sha256,
        context_contract_sha256=context_contract_sha256,
        maximum_absolute_offset_ms=300,
        padding_before_ms=100,
        padding_after_ms=100,
        minimum_frame_count=2,
        maximum_frame_count=12,
        maximum_window_duration_ms=1_000,
        maximum_rgb_bytes=80 * 1024 * 1024,
    )
    phone_window = P8RuntimePolicyArtifact.issue(
        policy_kind="phone_window",
        policy_revision=phone_policy.policy_revision,
        policy_document=PhoneWindowSemanticPolicy(
            **{
                key: value
                for key, value in phone_policy.to_json_object().items()
                if key != "schema_version"
            }
        ),
    )
    if phone_window.policy_sha256 != phone_policy.policy_sha256:
        raise VisualFeedbackContractError(
            "phone-window semantic policy digest differs from production"
        )
    return video_qc, av_sync, phone_window, phone_policy


def _pipeline_identity(
    *,
    root: Path,
    phone_reference: P6PhoneReferenceEvidenceBundle,
    landmark_model: Path,
    context_contract_sha256: str,
    video_qc_policy: P8RuntimePolicyArtifact,
    video_qc_decision_policy: P8RuntimePolicyArtifact,
    av_sync_policy: P8RuntimePolicyArtifact,
    av_sync_decision_policy: P8RuntimePolicyArtifact,
    phone_window_policy: P8RuntimePolicyArtifact,
) -> VisualRuntimePipelineIdentity:
    adapter_root = root / "src/voice_coach/pronunciation_attempts/adapters"
    parity = phone_reference.producer.runtime_parity_receipt
    identity = VisualRuntimePipelineIdentity.issue(
        tracker_revision=parity.production_worker_revision,
        tracker_sha256=parity.production_worker_sha256,
        tracker_adapter_revision="mediapipe_lip_landmark_adapter_v1",
        tracker_adapter_sha256=parity.production_adapter_sha256,
        decoder_revision="ffmpeg_av_container_adapter_v2",
        decoder_sha256=_sha256_file(adapter_root / "ffmpeg_av_container.py"),
        landmark_model_revision="mediapipe_face_landmarker_float16_v1",
        landmark_model_sha256=_sha256_file(landmark_model),
        normalizer_revision="mediapipe_visible_lip_normalizer_v1",
        normalizer_sha256=_sha256_file(adapter_root / "mediapipe_lip_landmarks.py"),
        cue_extractor_revision="dense_phone_window_tracking_bridge_v2",
        cue_extractor_sha256=_sha256_file(adapter_root / "runtime.py"),
        video_qc_measurement_revision=VIDEO_QC_MEASUREMENT_REVISION,
        video_qc_measurement_policy_sha256=video_qc_policy.policy_sha256,
        video_qc_measurement_code_sha256=_sha256_file(
            adapter_root / "video_qc.py"
        ),
        video_qc_decision_policy_revision=(
            video_qc_decision_policy.policy_revision
        ),
        video_qc_decision_policy_sha256=video_qc_decision_policy.policy_sha256,
        video_qc_decision_code_sha256=_sha256_file(
            adapter_root / "calibrated_runtime_decisions.py"
        ),
        av_sync_measurement_revision=AV_SYNC_MEASUREMENT_REVISION,
        av_sync_measurement_policy_sha256=av_sync_policy.policy_sha256,
        av_sync_measurement_code_sha256=_sha256_file(
            adapter_root / "lip_audio_av_sync.py"
        ),
        av_sync_decision_policy_revision=(
            av_sync_decision_policy.policy_revision
        ),
        av_sync_decision_policy_sha256=av_sync_decision_policy.policy_sha256,
        av_sync_decision_code_sha256=_sha256_file(
            adapter_root / "calibrated_runtime_decisions.py"
        ),
        phone_window_code_revision="dense_phone_window_v2",
        phone_window_code_sha256=_sha256_file(
            root / "src/voice_coach/pronunciation_attempts/phone_window.py"
        ),
        phone_window_policy_revision=phone_window_policy.policy_revision,
        phone_window_policy_sha256=phone_window_policy.policy_sha256,
        media_capture_contract_id=_MEDIA_CAPTURE_CONTRACT_ID,
        visual_observation_contract_id=(
            phone_reference.producer.capture_contract_id
        ),
        population_id_sha256=phone_reference.producer.population_id_sha256,
        context_contract_sha256=context_contract_sha256,
    )
    if identity.landmark_model_sha256 != parity.model_sha256:
        raise VisualFeedbackContractError("landmark model bytes differ from P6B")
    return identity


def _revocation_marker(
    *, invalid_v1: object, replacement: P6PhoneReferenceEvidenceBundle
) -> dict[str, object]:
    document = require_mapping(invalid_v1, "invalid_v1_phone_reference_bundle")
    producer = require_mapping(document.get("producer"), "invalid_v1_producer")
    selector = require_mapping(
        producer.get("source_selector"), "invalid_v1_source_selector"
    )
    invalid_phone = selector.get("expected_phone")
    if invalid_phone != "ㅃ" or P6_PHONE_REFERENCE_EXPECTED_PHONE != "ㅓ":
        raise VisualFeedbackContractError("invalid v1 phone label is not the audited defect")
    invalid_digest = require_sha256(
        document.get("bundle_sha256"), "invalid_v1_bundle_sha256"
    )
    if replacement.schema_version != P6_PHONE_REFERENCE_BUNDLE_SCHEMA_VERSION:
        raise VisualFeedbackContractError("replacement P6B schema differs")
    unsigned = {
        "schema_version": _REVOCATION_SCHEMA,
        "invalid_bundle_sha256": invalid_digest,
        "invalid_expected_phone": invalid_phone,
        "invalid_runtime_authorized": False,
        "superseded_by_bundle_sha256": replacement.bundle_sha256,
        "replacement_expected_phone": P6_PHONE_REFERENCE_EXPECTED_PHONE,
        "replacement_protocol_revision": replacement.producer.protocol_revision,
        "reason_code": "p6b_v1_wrong_expected_phone_label",
        "preservation_policy": "retain_invalid_v1_for_audit_never_use_at_runtime",
    }
    return {**unsigned, "marker_sha256": sha256_document(unsigned)}


def _validate_reload_receipt(
    value: object,
    *,
    identity: VisualRuntimePipelineIdentity,
    promotion: AutomatedP6PromotionReceipt,
    video_qc_decision_policy: P8RuntimePolicyArtifact,
    av_sync_decision_policy: P8RuntimePolicyArtifact,
    decision_policy_evidence: RuntimeDecisionPolicyEvidence,
) -> dict[str, object]:
    document = require_mapping(value, "p6_promotion_reload_receipt")
    require_exact_keys(
        document,
        {
            "schema_version",
            "pipeline_identity_sha256",
            "promotion_receipt_sha256",
            "geometry_bundle_sha256",
            "phone_reference_bundle_sha256",
            "calibration_artifact_sha256",
            "video_qc_decision_policy_artifact_sha256",
            "av_sync_decision_policy_artifact_sha256",
            "decision_policy_evidence_receipt_sha256",
            "worker_sha256",
            "validator_service_sha256",
            "fresh_process",
            "strict_typed_reload",
            "p6_promotion_reproduced",
            "p6_automated_promotion_authorized",
            "p8_promotion_authorized",
            "user_visual_runtime_authorized",
            "receipt_sha256",
        },
        "p6_promotion_reload_receipt",
    )
    digest = require_sha256(document["receipt_sha256"], "receipt_sha256")
    unsigned = {key: item for key, item in document.items() if key != "receipt_sha256"}
    if digest != sha256_document(unsigned):
        raise VisualFeedbackContractError("P6 promotion reload digest differs")
    if (
        document["pipeline_identity_sha256"] != identity.identity_sha256
        or document["promotion_receipt_sha256"] != promotion.receipt_sha256
        or document["video_qc_decision_policy_artifact_sha256"]
        != video_qc_decision_policy.artifact_sha256
        or document["av_sync_decision_policy_artifact_sha256"]
        != av_sync_decision_policy.artifact_sha256
        or document["decision_policy_evidence_receipt_sha256"]
        != decision_policy_evidence.receipt_sha256
        or document["fresh_process"] is not True
        or document["strict_typed_reload"] is not True
        or document["p6_promotion_reproduced"] is not True
        or document["p6_automated_promotion_authorized"] is not True
        or document["p8_promotion_authorized"] is not False
        or document["user_visual_runtime_authorized"] is not False
    ):
        raise VisualFeedbackContractError("P6 promotion reload claims differ")
    return dict(document)


def _run(arguments: argparse.Namespace) -> dict[str, object]:
    root = arguments.repository_root.resolve(strict=True)
    output_dir = arguments.output_dir.resolve(strict=True)
    if not output_dir.is_dir():
        raise VisualFeedbackContractError("output directory must already exist")
    geometry = P6GeometryEvidenceBundle.from_json_object(
        _strict_json(arguments.geometry_bundle)
    )
    phone_reference = P6PhoneReferenceEvidenceBundle.from_json_object(
        _strict_json(arguments.phone_reference_bundle)
    )
    calibration = phone_reference.producer.calibration_artifact
    context_contract_sha256 = sha256_document(
        {
            "capture_contract_id": _MEDIA_CAPTURE_CONTRACT_ID,
            "context_id": _CONTEXT_ID,
            "video_codecs": ["h264", "hevc"],
            "audio_codec": "aac",
        }
    )
    video_qc, av_sync, phone_window, _ = _policy_artifacts(
        context_contract_sha256=context_contract_sha256
    )
    video_qc_decision_policy = P8RuntimePolicyArtifact.from_json_object(
        _strict_json(
            arguments.video_qc_decision_policy,
            require_frozen=True,
        )
    )
    av_sync_decision_policy = P8RuntimePolicyArtifact.from_json_object(
        _strict_json(
            arguments.av_sync_decision_policy,
            require_frozen=True,
        )
    )
    decision_policy_evidence = RuntimeDecisionPolicyEvidence.from_json_object(
        _strict_json(
            arguments.decision_policy_evidence,
            require_frozen=True,
        )
    )
    identity = _pipeline_identity(
        root=root,
        phone_reference=phone_reference,
        landmark_model=arguments.landmark_model,
        context_contract_sha256=context_contract_sha256,
        video_qc_policy=video_qc,
        video_qc_decision_policy=video_qc_decision_policy,
        av_sync_policy=av_sync,
        av_sync_decision_policy=av_sync_decision_policy,
        phone_window_policy=phone_window,
    )
    promotion = build_automated_p6_promotion_receipt(
        geometry_bundle=geometry,
        phone_reference_bundle=phone_reference,
        calibration_artifact=calibration,
        pipeline_identity=identity,
        video_qc_decision_policy=video_qc_decision_policy,
        av_sync_decision_policy=av_sync_decision_policy,
        decision_policy_evidence=decision_policy_evidence,
    )
    revocation = _revocation_marker(
        invalid_v1=_strict_json(arguments.invalid_v1_phone_reference_bundle),
        replacement=phone_reference,
    )
    output_documents: dict[str, object] = {
        "visual_runtime_pipeline_identity_v3.json": identity.to_json_object(),
        "automated_p6_promotion_receipt_v4.json": promotion.to_json_object(),
        "aihub68_phone_reference_v1_superseded_v2.json": revocation,
        "video_qc_measurement_policy_artifact_v3.json": (
            video_qc.to_json_object()
        ),
        "av_sync_measurement_policy_artifact_v3.json": av_sync.to_json_object(),
        "phone_window_policy_artifact_v3.json": phone_window.to_json_object(),
    }
    reload_name = "automated_p6_promotion_reload_receipt_v2.json"
    for name in (*output_documents, reload_name):
        if (output_dir / name).exists():
            raise VisualFeedbackContractError("P6 promotion output already exists")

    with tempfile.TemporaryDirectory(
        prefix=".p6-promotion-candidate-", dir=output_dir.parent
    ) as temporary_name:
        temporary = Path(temporary_name)
        identity_path = temporary / "identity.json"
        promotion_path = temporary / "promotion.json"
        calibration_path = temporary / "calibration.json"
        video_qc_decision_policy_path = temporary / "video_qc_decision.json"
        av_sync_decision_policy_path = temporary / "av_sync_decision.json"
        decision_policy_evidence_path = temporary / "decision_evidence.json"
        _write_exclusive(
            identity_path,
            canonical_json_bytes(identity.to_json_object()) + b"\n",
        )
        _write_exclusive(
            promotion_path,
            canonical_json_bytes(promotion.to_json_object()) + b"\n",
        )
        _write_exclusive(
            calibration_path,
            canonical_json_bytes(calibration.to_json_object()) + b"\n",
        )
        _write_exclusive(
            video_qc_decision_policy_path,
            canonical_json_bytes(video_qc_decision_policy.to_json_object())
            + b"\n",
        )
        _write_exclusive(
            av_sync_decision_policy_path,
            canonical_json_bytes(av_sync_decision_policy.to_json_object())
            + b"\n",
        )
        _write_exclusive(
            decision_policy_evidence_path,
            canonical_json_bytes(decision_policy_evidence.to_json_object())
            + b"\n",
        )
        command = [
            sys.executable,
            "-I",
            "-m",
            "voice_coach.visual_feedback.adapters.p6_promotion_reload_worker",
            "--identity",
            str(identity_path),
            "--promotion-receipt",
            str(promotion_path),
            "--geometry-bundle",
            str(arguments.geometry_bundle),
            "--phone-reference-bundle",
            str(arguments.phone_reference_bundle),
            "--calibration-artifact",
            str(calibration_path),
            "--video-qc-decision-policy",
            str(video_qc_decision_policy_path),
            "--av-sync-decision-policy",
            str(av_sync_decision_policy_path),
            "--decision-policy-evidence",
            str(decision_policy_evidence_path),
        ]
        completed = subprocess.run(
            command,
            cwd=root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=60,
        )
        if completed.returncode != 0 or len(completed.stdout) > _MAX_JSON_BYTES:
            raise VisualFeedbackContractError("fresh-process P6 reload failed")
        try:
            reload_value = json.loads(completed.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise VisualFeedbackContractError(
                "fresh-process P6 reload output is invalid"
            ) from error
    reload_receipt = _validate_reload_receipt(
        reload_value,
        identity=identity,
        promotion=promotion,
        video_qc_decision_policy=video_qc_decision_policy,
        av_sync_decision_policy=av_sync_decision_policy,
        decision_policy_evidence=decision_policy_evidence,
    )
    output_documents[reload_name] = reload_receipt
    for name, document in output_documents.items():
        _write_exclusive(
            output_dir / name,
            canonical_json_bytes(document) + b"\n",
        )
    directory_descriptor = os.open(
        output_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    )
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
    return {
        "status": "p6_automated_promotion_authorized",
        "pipeline_identity_sha256": identity.identity_sha256,
        "promotion_receipt_sha256": promotion.receipt_sha256,
        "reload_receipt_sha256": reload_receipt["receipt_sha256"],
        "decision_policy_evidence_receipt_sha256": (
            decision_policy_evidence.receipt_sha256
        ),
        "invalid_v1_runtime_authorized": False,
        "p8_promotion_authorized": False,
        "user_visual_runtime_authorized": False,
        "output_file_count": len(output_documents),
    }


def main(argv: list[str] | None = None) -> int:
    try:
        result = _run(_parser().parse_args(argv))
    except (OSError, subprocess.SubprocessError, VisualFeedbackContractError) as error:
        failure = {
            "status": "failed_closed",
            "reason_codes": ["p6_automated_promotion_build_failed"],
            "error_type": type(error).__name__,
        }
        sys.stderr.buffer.write(canonical_json_bytes(failure) + b"\n")
        return 2
    sys.stdout.buffer.write(canonical_json_bytes(result) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
