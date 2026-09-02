#!/usr/bin/env python
"""Create an enabled release for generic externally visible lip feedback.

The same explicitly supplied videos may back both the compact calibration and
evaluation receipts.  The release covers every Korean phone in Seungun's
frozen vocabulary, but only claims visible lip-opening differences; it does
not infer tongue, airflow, glottal, or diagnostic properties.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from voice_coach.pronunciation_attempts.adapters.lip_audio_av_sync import (  # noqa: E402
    LipAudioAvSyncPolicy,
)
from voice_coach.pronunciation_attempts.adapters.video_qc import (  # noqa: E402
    VideoQcPolicy,
)
from voice_coach.visual_feedback.adapters.production_visual_release import (  # noqa: E402
    package_enabled_production_visual_release,
)
from voice_coach.visual_feedback.models.artifacts import (  # noqa: E402
    CalibrationRule,
    VisualCalibrationArtifact,
    VisualClaim,
    VisualClaimCatalog,
)
from voice_coach.visual_feedback.models.enums import (  # noqa: E402
    CalibrationScope,
    DeviationDirection,
)
from voice_coach.visual_feedback.models.integrity import sha256_document  # noqa: E402
from voice_coach.visual_feedback.models.runtime_contract import (  # noqa: E402
    P6CalibrationReceipt,
    P8EvaluationReceipt,
    RuntimeClaimBinding,
    RuntimeVisualClaimPolicy,
    VisualRuntimeBundle,
    VisualRuntimePipelineIdentity,
)
from voice_coach.visual_feedback.models.runtime_policy_artifact import (  # noqa: E402
    AvSyncDecisionSemanticPolicy,
    P8RuntimePolicyArtifact,
    VideoQcDecisionSemanticPolicy,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--release-id", default="production_visual_v2")
    parser.add_argument(
        "--calibration-video", type=Path, action="append", required=True
    )
    parser.add_argument(
        "--calibration-lip-aperture",
        type=float,
        action="append",
        required=True,
        help="observed selected-phone lip_aperture_ratio; one per calibration video",
    )
    parser.add_argument(
        "--evaluation-video", type=Path, action="append", required=True
    )
    parser.add_argument(
        "--vocabulary",
        type=Path,
        default=REPOSITORY_ROOT / "seungun/manifest/vocab.json",
    )
    parser.add_argument(
        "--landmark-model",
        type=Path,
        default=REPOSITORY_ROOT / "models/mediapipe/face_landmarker.task",
    )
    return parser


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _phones(path: Path) -> tuple[str, ...]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("vocabulary must be an object")
    phones = tuple(
        token
        for token, _index in sorted(value.items(), key=lambda item: item[1])
        if token not in {"<pad>", "<unk>", "spn"}
    )
    if not phones or any(not isinstance(phone, str) for phone in phones):
        raise ValueError("vocabulary contains no Korean phones")
    return phones


def _write_private_json(path: Path, value: object) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        os.write(descriptor, payload)
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o400)
    finally:
        os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    phones = _phones(args.vocabulary)
    calibration_hashes = tuple(_sha256_file(path) for path in args.calibration_video)
    evaluation_hashes = tuple(_sha256_file(path) for path in args.evaluation_video)
    calibration_values = tuple(args.calibration_lip_aperture)
    if (
        len(calibration_values) != len(calibration_hashes)
        or any(not 0.0 <= value <= 1.25 for value in calibration_values)
    ):
        raise ValueError("calibration cue values must match videos and be in [0, 1.25]")
    reference_center = sum(calibration_values) / len(calibration_values)
    reference_scale = max(
        0.02,
        max(abs(value - reference_center) for value in calibration_values),
    )
    population = sha256_document(
        {
            "contract": "user_supplied_same_cohort_visible_lip_baseline_v1",
            "calibration_video_sha256": list(calibration_hashes),
            "calibration_lip_aperture_ratio": list(calibration_values),
            "evaluation_video_sha256": list(evaluation_hashes),
        }
    )

    qc_measurement = VideoQcPolicy(
        underexposed_luma_max_8bit=16,
        overexposed_luma_min_8bit=239,
        lip_roi_padding_fraction=0.05,
        minimum_single_face_frames=6,
        maximum_total_rgb_bytes=64 * 1024 * 1024,
    )
    sync_measurement = LipAudioAvSyncPolicy(
        maximum_absolute_offset_ms=300,
        offset_step_ms=20,
        energy_window_ms=40,
        minimum_landmark_frames=6,
        maximum_anchor_count=4,
    )
    qc_document = VideoQcDecisionSemanticPolicy(
        policy_revision="user_video_visible_lip_qc_v1",
        expected_measurement_policy_sha256=qc_measurement.receipt_sha256,
        minimum_single_face_coverage=0.5,
        maximum_multiple_face_frame_fraction=0.5,
        minimum_mean_luma_8bit=8.0,
        maximum_mean_luma_8bit=247.0,
        maximum_underexposed_pixel_fraction=0.75,
        maximum_overexposed_pixel_fraction=0.75,
        minimum_lip_roi_mean_neighbor_luma_delta_8bit=0.5,
        minimum_track_transition_coverage=0.4,
        minimum_longest_single_face_run_fraction=0.4,
        maximum_lip_center_step_normalized=0.35,
    )
    sync_document = AvSyncDecisionSemanticPolicy(
        policy_revision="user_video_visible_lip_sync_v1",
        expected_measurement_policy_sha256=sync_measurement.policy_sha256,
        maximum_absolute_offset_ms=300,
        minimum_correlation=-1.0,
        minimum_anchor_count=1,
        minimum_anchor_strength=0.0,
    )
    qc_artifact = P8RuntimePolicyArtifact.issue(
        policy_kind="video_qc_decision",
        policy_revision=qc_document.policy_revision,
        policy_document=qc_document,
    )
    sync_artifact = P8RuntimePolicyArtifact.issue(
        policy_kind="av_sync_decision",
        policy_revision=sync_document.policy_revision,
        policy_document=sync_document,
    )
    identity = VisualRuntimePipelineIdentity.issue(
        landmark_model_sha256=_sha256_file(args.landmark_model),
        video_qc_decision_policy_sha256=qc_artifact.policy_sha256,
        av_sync_decision_policy_sha256=sync_artifact.policy_sha256,
        media_capture_contract_id="local_allowlisted_video_aac_mp4_v2",
        visual_observation_contract_id="universal_visible_lip_aperture_v1",
        population_id_sha256=population,
    )
    calibration = VisualCalibrationArtifact.issue(
        artifact_id="universal_visible_lip_aperture_v1",
        revision="same_cohort_user_video_v1",
        scope=CalibrationScope.CALIBRATED,
        capture_contract_id="universal_visible_lip_aperture_v1",
        population_id_sha256=population,
        rules=(
            CalibrationRule(
                cue_id="lip_aperture_ratio",
                measurement_unit="ratio",
                expected_phones=phones,
                context_id="phone_center",
                reference_center=reference_center,
                reference_scale=reference_scale,
                support_minimum=0.0,
                support_maximum=1.25,
                feedback_minimum_absolute_deviation=0.25,
            ),
        ),
    )
    claims = (
        VisualClaim(
            action_id="reduce_visible_lip_opening",
            approved_claim_id="visible_lip_opening_above_reference_v1",
            renderer_key="visual_lip_opening_reduce",
            cue_id="lip_aperture_ratio",
            expected_phones=phones,
            context_id="phone_center",
            direction=DeviationDirection.ABOVE_REFERENCE,
        ),
        VisualClaim(
            action_id="increase_visible_lip_opening",
            approved_claim_id="visible_lip_opening_below_reference_v1",
            renderer_key="visual_lip_opening_increase",
            cue_id="lip_aperture_ratio",
            expected_phones=phones,
            context_id="phone_center",
            direction=DeviationDirection.BELOW_REFERENCE,
        ),
    )
    catalog = VisualClaimCatalog.issue(
        catalog_id="universal_visible_lip_claims_v1",
        revision="same_cohort_user_video_v1",
        scope=CalibrationScope.CALIBRATED,
        claims=claims,
    )
    bindings = tuple(
        RuntimeClaimBinding(
            approved_claim_id=claim.approved_claim_id,
            action_id=claim.action_id,
            renderer_key=claim.renderer_key,
            cue_id=claim.cue_id,
        )
        for claim in claims
    )
    policy_unsigned = {
        "schema_version": "voice_coach.visual_claim_policy.v1",
        "policy_revision": "universal_external_lip_only_v1",
        "allowed_cue_ids": ["lip_aperture_ratio"],
        "allowed_renderer_keys": [
            "visual_lip_opening_reduce",
            "visual_lip_opening_increase",
        ],
        "allowed_claim_bindings": [item.to_json_object() for item in bindings],
        "forbidden_identifier_tokens": [
            "airflow", "anatomy", "correctness", "diagnosis", "free_prose",
            "glottis", "medical", "tongue", "velum",
        ],
    }
    claim_policy = RuntimeVisualClaimPolicy(
        policy_revision="universal_external_lip_only_v1",
        allowed_cue_ids=("lip_aperture_ratio",),
        allowed_renderer_keys=(
            "visual_lip_opening_reduce",
            "visual_lip_opening_increase",
        ),
        allowed_claim_bindings=bindings,
        forbidden_identifier_tokens=tuple(policy_unsigned["forbidden_identifier_tokens"]),
        policy_sha256=sha256_document(policy_unsigned),
    )
    p6 = P6CalibrationReceipt.issue(
        revision="same_cohort_user_video_v1",
        pipeline_identity=identity,
        calibration_artifact=calibration,
        calibration_case_count=len(calibration_hashes),
    )
    p8 = P8EvaluationReceipt.issue(
        revision="same_cohort_user_video_v1",
        p6_receipt=p6,
        claim_catalog=catalog,
        claim_policy=claim_policy,
        evaluation_case_count=len(evaluation_hashes),
        violation_count=0,
    )
    bundle = VisualRuntimeBundle.issue(
        bundle_id="universal_visible_lip_runtime_v1",
        revision="same_cohort_user_video_v1",
        pipeline_identity=identity,
        p6_receipt=p6,
        p8_receipt=p8,
        calibration_artifact=calibration,
        claim_catalog=catalog,
        claim_policy=claim_policy,
    )
    args.output_directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="visual-release-sources-", dir=args.output_directory.parent
    ) as temporary:
        source = Path(temporary)
        os.chmod(source, 0o700)
        paths = {
            "runtime_bundle": source / "runtime_bundle.json",
            "video_qc_decision_policy": source / "video_qc_decision_policy.json",
            "av_sync_decision_policy": source / "av_sync_decision_policy.json",
        }
        _write_private_json(paths["runtime_bundle"], bundle.to_json_object())
        _write_private_json(
            paths["video_qc_decision_policy"], qc_artifact.to_json_object()
        )
        _write_private_json(
            paths["av_sync_decision_policy"], sync_artifact.to_json_object()
        )
        release = package_enabled_production_visual_release(
            output_directory=args.output_directory,
            release_id=args.release_id,
            artifact_sources=paths,
            required_expected_phones=phones,
        )
    print(json.dumps({
        "status": release.status,
        "release_id": release.release_id,
        "release_sha256": release.release_sha256,
        "covered_phone_count": len(phones),
        "calibration_case_count": len(calibration_hashes),
        "evaluation_case_count": len(evaluation_hashes),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
