#!/usr/bin/env python3
"""Calibrate legacy TS174 or the authorized TS174+TS175+TS176 cohort.

Only caller-owned read-only descriptors are accepted for the two merged ZIPs.
For a training-pipeline run, open ``candidate-0.zip``, ``candidate-1.zip``, and
``50-dataset-admission.json`` as descriptors 3, 4, and 5, then pass
``--candidate-0-zip-fd 3 --candidate-1-zip-fd 4 --admission-receipt-fd 5``.
The role resolver ignores candidate filenames and maps by the admission
receipt's archive SHA-256 and content root.  The command cannot accept the P8
locked roles or alter the predeclared calibration policy after observations.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import stat
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from voice_coach.pronunciation_attempts.adapters.lip_audio_av_sync import (  # noqa: E402
    LipAudioAvSyncPolicy,
)
from voice_coach.pronunciation_attempts.adapters.mediapipe_lip_landmarks import (  # noqa: E402
    MediaPipeLandmarkPolicy,
)
from voice_coach.pronunciation_attempts.adapters.video_qc import (  # noqa: E402
    VideoQcPolicy,
)
from voice_coach.pronunciation_attempts.composition import (  # noqa: E402
    OFFICIAL_FACE_LANDMARKER_SHA256,
)
from voice_coach.pronunciation_attempts.models import AvContainerPolicy  # noqa: E402
from voice_coach.visual_feedback.adapters.aihub538_actual_media_calibration import (  # noqa: E402
    Aihub538ActualMediaCalibrationAdapter,
)
from voice_coach.visual_feedback.adapters.aihub538_admitted_archive_binding import (  # noqa: E402
    Aihub538AdmittedArchiveBinding,
    Aihub538CandidateRoleBinding,
    assert_aihub538_descriptor_identity,
    bind_aihub538_legacy_ts174_replay_aggregate,
    capture_aihub538_descriptor_identity,
    load_aihub538_training_admission_receipt_descriptor,
    resolve_aihub538_candidate_role_descriptors,
)
from voice_coach.visual_feedback.adapters.aihub538_multishard_media import (  # noqa: E402
    Aihub538CalibrationShardSource,
    Aihub538CompositeCalibrationMediaSource,
)
from voice_coach.visual_feedback.adapters.aihub538_training_run_binding import (  # noqa: E402
    open_aihub538_authorized_training_run,
)
from voice_coach.visual_feedback.adapters import (  # noqa: E402
    aihub538_decision_calibration_publish as calibration_publish,
)
from voice_coach.visual_feedback.adapters.aihub538_identity_key_store import (  # noqa: E402
    Aihub538IdentityKeyStoreError,
    load_aihub538_identity_key,
)
from voice_coach.visual_feedback.adapters.aihub538_training_dataset import (  # noqa: E402
    Aihub538TrainingDatasetAdapter,
)
from voice_coach.visual_feedback.errors import VisualFeedbackContractError  # noqa: E402
from voice_coach.visual_feedback.models.aihub538_decision_calibration import (  # noqa: E402
    DEFAULT_AIHUB538_DECISION_CALIBRATION_PROTOCOL,
    MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL,
)
from voice_coach.visual_feedback.models.aihub538_multishard_admission import (  # noqa: E402
    Aihub538MultiShardAdmissionReceipt,
)
from voice_coach.visual_feedback.models.aihub538_label_selection_authorization import (  # noqa: E402
    require_aihub538_frozen_source_authorization_receipt,
)
from voice_coach.visual_feedback.models.aihub538_nonlocked_shards import (  # noqa: E402
    AIHUB538_TS174_TL174_SHARD,
    AIHUB538_TS175_TL175_SHARD,
    AIHUB538_TS176_TL176_SHARD,
)
from voice_coach.visual_feedback.models.aihub538_training_evidence import (  # noqa: E402
    AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY_SHA256,
    Aihub538TrainingAdmissionReceipt,
)
from voice_coach.visual_feedback.models.runtime_policy_artifact import (  # noqa: E402
    AvSyncSemanticPolicy,
    P8RuntimePolicyArtifact,
    VideoQcSemanticPolicy,
)
from voice_coach.visual_feedback.services.aihub538_decision_calibration import (  # noqa: E402
    Aihub538DecisionCalibrationError,
    Aihub538DecisionCalibrationService,
)
from voice_coach.visual_feedback.services.aihub538_training_smoke import (  # noqa: E402
    Aihub538TrainingSmokeError,
    Aihub538TrainingSmokeService,
)
from voice_coach.visual_feedback.services.aihub538_label_capacity_audit import (  # noqa: E402
    Aihub538LabelCapacityPipelineError,
    load_aihub538_label_selection_authorization_receipt_descriptor,
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
    parser.add_argument(
        "--label-zip-fd",
        type=_descriptor,
        help="explicit role mode: caller-owned read-only TL174 ZIP descriptor",
    )
    parser.add_argument(
        "--source-zip-fd",
        type=_descriptor,
        help="explicit role mode: caller-owned read-only TS174 ZIP descriptor",
    )
    parser.add_argument(
        "--candidate-0-zip-fd",
        type=_descriptor,
        help="pipeline mode: read-only candidate-0.zip descriptor",
    )
    parser.add_argument(
        "--candidate-1-zip-fd",
        type=_descriptor,
        help="pipeline mode: read-only candidate-1.zip descriptor",
    )
    parser.add_argument(
        "--admission-receipt-fd",
        type=_descriptor,
        help=(
            "pipeline mode: read-only 0400 descriptor for "
            "50-dataset-admission.json"
        ),
    )
    parser.add_argument(
        "--label-selection-authorization-fd",
        type=_descriptor,
        help="multi mode: caller-owned read-only 0400 v5 receipt descriptor",
    )
    parser.add_argument(
        "--ts174-run-dir",
        type=Path,
        help="multi mode: exact private completed TS174/TL174 run",
    )
    parser.add_argument(
        "--ts175-run-dir",
        type=Path,
        help="multi mode: exact private completed TS175/TL175 run",
    )
    parser.add_argument(
        "--ts176-run-dir",
        type=Path,
        help="multi mode: exact private completed TS176/TL176 run",
    )
    parser.add_argument(
        "--identity-state-dir",
        type=Path,
        required=True,
        help="existing 0700 directory created for the AI Hub 538 HMAC key",
    )
    parser.add_argument(
        "--landmark-model",
        type=Path,
        default=REPOSITORY_ROOT / "models/mediapipe/face_landmarker.task",
    )
    parser.add_argument(
        "--restricted-workspace-root",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPOSITORY_ROOT / "artifacts/visual_feedback/p6",
    )
    parser.add_argument(
        "--dataset-access-permission-confirmed",
        action="store_true",
        help="confirm portal permission for AI Hub dataset 538",
    )
    return parser


def _measurement_policies() -> tuple[
    P8RuntimePolicyArtifact,
    P8RuntimePolicyArtifact,
    VideoQcPolicy,
    LipAudioAvSyncPolicy,
]:
    qc_runtime = VideoQcPolicy(
        underexposed_luma_max_8bit=16,
        overexposed_luma_min_8bit=239,
        lip_roi_padding_fraction=0.05,
        minimum_single_face_frames=6,
        maximum_total_rgb_bytes=64 * 1024 * 1024,
    )
    sync_runtime = LipAudioAvSyncPolicy(
        maximum_absolute_offset_ms=300,
        offset_step_ms=20,
        energy_window_ms=40,
        minimum_landmark_frames=6,
        maximum_anchor_count=4,
    )
    qc_artifact = P8RuntimePolicyArtifact.issue(
        policy_kind="video_qc",
        policy_revision="video_qc_measurement_v1",
        policy_document=VideoQcSemanticPolicy(**qc_runtime.to_json_object()),
    )
    sync_artifact = P8RuntimePolicyArtifact.issue(
        policy_kind="av_sync",
        policy_revision="lip_audio_av_sync_policy_v1",
        policy_document=AvSyncSemanticPolicy(**sync_runtime.to_json_object()),
    )
    if (
        qc_artifact.policy_sha256 != qc_runtime.receipt_sha256
        or sync_artifact.policy_sha256 != sync_runtime.policy_sha256
    ):
        raise Aihub538DecisionCalibrationError(
            "aihub538_measurement_policy_runtime_parity_failed"
        )
    return qc_artifact, sync_artifact, qc_runtime, sync_runtime


def _model_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError:
        raise Aihub538DecisionCalibrationError(
            "aihub538_landmark_model_unavailable"
        ) from None
    return digest.hexdigest()


def _validate_directory(path: Path, *, require_mode_0700: bool) -> Path:
    absolute = path.absolute()
    try:
        metadata = absolute.lstat()
    except OSError:
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_directory_invalid"
        ) from None
    if (
        not absolute.is_absolute()
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or require_mode_0700
        and stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_directory_invalid"
        )
    return absolute


def _resolve_archive_input(
    arguments: argparse.Namespace,
) -> tuple[
    int,
    int,
    Aihub538TrainingAdmissionReceipt | None,
    Aihub538CandidateRoleBinding | None,
]:
    explicit = (arguments.label_zip_fd, arguments.source_zip_fd)
    candidates = (
        arguments.candidate_0_zip_fd,
        arguments.candidate_1_zip_fd,
        arguments.admission_receipt_fd,
    )
    if all(value is not None for value in explicit) and all(
        value is None for value in candidates
    ):
        label_descriptor, source_descriptor = explicit
        assert label_descriptor is not None and source_descriptor is not None
        return label_descriptor, source_descriptor, None, None
    if all(value is None for value in explicit) and all(
        value is not None for value in candidates
    ):
        candidate_0, candidate_1, receipt_descriptor = candidates
        assert candidate_0 is not None
        assert candidate_1 is not None
        assert receipt_descriptor is not None
        admission_receipt = load_aihub538_training_admission_receipt_descriptor(
            receipt_descriptor
        )
        binding = resolve_aihub538_candidate_role_descriptors(
            candidate_0_descriptor=candidate_0,
            candidate_1_descriptor=candidate_1,
            admission_receipt=admission_receipt,
        )
        return (
            binding.label_zip_descriptor,
            binding.source_zip_descriptor,
            admission_receipt,
            binding,
        )
    raise Aihub538DecisionCalibrationError(
        "aihub538_calibration_archive_input_mode_invalid"
    )


def _actual_media_adapter(
    *,
    arguments: argparse.Namespace,
    label_zip_descriptor: int,
    source_zip_descriptor: int,
    identity_hash_key: bytes,
    admission_receipt: Aihub538TrainingAdmissionReceipt,
    shard_spec: object,
    archive_binding: Aihub538AdmittedArchiveBinding | None = None,
    workspace_root: Path,
    qc_runtime: VideoQcPolicy,
    sync_runtime: LipAudioAvSyncPolicy,
) -> Aihub538ActualMediaCalibrationAdapter:
    return Aihub538ActualMediaCalibrationAdapter(
        label_zip_descriptor=label_zip_descriptor,
        source_zip_descriptor=source_zip_descriptor,
        identity_hash_key=identity_hash_key,
        admission_receipt=admission_receipt,
        shard_spec=shard_spec,
        archive_binding=archive_binding,
        restricted_workspace_root=workspace_root,
        landmark_model_path=arguments.landmark_model.absolute(),
        landmark_model_sha256=OFFICIAL_FACE_LANDMARKER_SHA256,
        video_qc_policy=qc_runtime,
        av_sync_policy=sync_runtime,
        container_policy=AvContainerPolicy(
            minimum_duration_ms=500,
            maximum_duration_ms=25_000,
            minimum_width=160,
            maximum_width=1920,
            minimum_height=120,
            maximum_height=1920,
            minimum_frames_per_second=15.0,
            maximum_frames_per_second=60.0,
            minimum_frame_count=8,
            maximum_frame_count=1_500,
            maximum_rgb_sample_frames=8,
            maximum_rgb_sample_bytes=64 * 1024 * 1024,
            maximum_pcm_bytes=2 * 1024 * 1024,
            subprocess_timeout_seconds=120.0,
            maximum_subprocess_output_bytes=4 * 1024 * 1024,
        ),
        landmark_policy=MediaPipeLandmarkPolicy(
            minimum_face_detection_confidence=0.5,
            minimum_face_presence_confidence=0.5,
            minimum_tracking_confidence=0.5,
            maximum_frame_count=8,
            maximum_total_rgb_bytes=64 * 1024 * 1024,
            subprocess_timeout_seconds=120.0,
            maximum_subprocess_output_bytes=2 * 1024 * 1024,
        ),
        repository_root=REPOSITORY_ROOT,
    )


def _multi_mode(arguments: argparse.Namespace) -> bool:
    multi = (
        arguments.label_selection_authorization_fd,
        arguments.ts174_run_dir,
        arguments.ts175_run_dir,
        arguments.ts176_run_dir,
    )
    legacy = (
        arguments.label_zip_fd,
        arguments.source_zip_fd,
        arguments.candidate_0_zip_fd,
        arguments.candidate_1_zip_fd,
        arguments.admission_receipt_fd,
    )
    if any(value is not None for value in multi):
        if not all(value is not None for value in multi) or any(
            value is not None for value in legacy
        ):
            raise Aihub538DecisionCalibrationError(
                "aihub538_calibration_input_scope_invalid"
            )
        return True
    return False


def _run_legacy(
    *,
    arguments: argparse.Namespace,
    identity_hash_key: bytes,
    workspace_root: Path,
    qc_artifact: P8RuntimePolicyArtifact,
    sync_artifact: P8RuntimePolicyArtifact,
    qc_runtime: VideoQcPolicy,
    sync_runtime: LipAudioAvSyncPolicy,
):  # noqa: ANN202
    (
        label_zip_descriptor,
        source_zip_descriptor,
        pipeline_admission_receipt,
        candidate_role_binding,
    ) = _resolve_archive_input(arguments)
    smoke = Aihub538TrainingSmokeService(
        Aihub538TrainingDatasetAdapter()
    ).evaluate(
        label_zip_descriptor=label_zip_descriptor,
        source_zip_descriptor=source_zip_descriptor,
        dataset_access_permission_confirmed=True,
        identity_hash_key=identity_hash_key,
    )
    admission_receipt = smoke.admission_receipt
    if pipeline_admission_receipt is not None:
        if smoke.admission_receipt != pipeline_admission_receipt:
            if smoke.aggregate is None:
                raise Aihub538DecisionCalibrationError(
                    "aihub538_pipeline_admission_replay_mismatch"
                )
            bind_aihub538_legacy_ts174_replay_aggregate(
                current_aggregate=smoke.aggregate,
                legacy_receipt=pipeline_admission_receipt,
            )
        admission_receipt = pipeline_admission_receipt
    with _actual_media_adapter(
        arguments=arguments,
        label_zip_descriptor=label_zip_descriptor,
        source_zip_descriptor=source_zip_descriptor,
        identity_hash_key=identity_hash_key,
        admission_receipt=admission_receipt,
        shard_spec=AIHUB538_TS174_TL174_SHARD,
        workspace_root=workspace_root,
        qc_runtime=qc_runtime,
        sync_runtime=sync_runtime,
    ) as actual_media:
        result = Aihub538DecisionCalibrationService(
            measurement_port=actual_media,
            protocol=DEFAULT_AIHUB538_DECISION_CALIBRATION_PROTOCOL,
        ).calibrate(
            items=actual_media.inventory,
            admission_receipt=admission_receipt,
            video_qc_measurement_policy=qc_artifact,
            av_sync_measurement_policy=sync_artifact,
        )
    return result, admission_receipt, candidate_role_binding


def _run_multi(
    *,
    arguments: argparse.Namespace,
    identity_hash_key: bytes,
    workspace_root: Path,
    qc_artifact: P8RuntimePolicyArtifact,
    sync_artifact: P8RuntimePolicyArtifact,
    qc_runtime: VideoQcPolicy,
    sync_runtime: LipAudioAvSyncPolicy,
):  # noqa: ANN202
    receipt_descriptor = arguments.label_selection_authorization_fd
    assert receipt_descriptor is not None
    before = capture_aihub538_descriptor_identity(
        receipt_descriptor,
        require_mode=0o400,
    )
    capacity_receipt = (
        load_aihub538_label_selection_authorization_receipt_descriptor(
            receipt_descriptor
        )
    )
    require_aihub538_frozen_source_authorization_receipt(capacity_receipt)
    if not capacity_receipt.is_feasible:
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_label_selection_authorization_invalid"
        )
    # Close the parse-to-first-source-open window.  The caller retains the
    # descriptor, so every transition into restricted media must reassert the
    # exact inode/mode/size identity captured before parsing.
    assert_aihub538_descriptor_identity(receipt_descriptor, before)
    run_directories = (
        arguments.ts174_run_dir,
        arguments.ts175_run_dir,
        arguments.ts176_run_dir,
    )
    shard_specs = (
        AIHUB538_TS174_TL174_SHARD,
        AIHUB538_TS175_TL175_SHARD,
        AIHUB538_TS176_TL176_SHARD,
    )
    with ExitStack() as stack:
        authorizations = []
        shard_sources = []
        for run_directory, shard_spec in zip(
            run_directories,
            shard_specs,
            strict=True,
        ):
            assert run_directory is not None
            assert_aihub538_descriptor_identity(receipt_descriptor, before)
            binding = stack.enter_context(
                open_aihub538_authorized_training_run(
                    directory=run_directory.absolute(),
                    shard_spec=shard_spec,
                    capacity_receipt=(
                        None
                        if shard_spec == AIHUB538_TS174_TL174_SHARD
                        else capacity_receipt
                    ),
                )
            )
            roles = binding.candidate_role_binding
            prevalidated = Aihub538TrainingDatasetAdapter(
                shard_spec=shard_spec
            ).admit_prevalidated(
                archive_binding=binding.admitted_archive_binding,
                admission_receipt=binding.admission_receipt,
                identity_hash_key=identity_hash_key,
            )
            assert_aihub538_descriptor_identity(receipt_descriptor, before)
            if shard_spec == AIHUB538_TS174_TL174_SHARD:
                if (
                    binding.admission_receipt.admission_policy_sha256
                    == AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY_SHA256
                ):
                    legacy_bound = prevalidated.bind_legacy_ts174_replay()
                    authorization = binding.authorize(legacy_bound)
                else:
                    authorization = binding.authorize(prevalidated)
            else:
                authorization = binding.authorize(prevalidated)
            media_source = stack.enter_context(
                _actual_media_adapter(
                    arguments=arguments,
                    label_zip_descriptor=roles.label_zip_descriptor,
                    source_zip_descriptor=roles.source_zip_descriptor,
                    identity_hash_key=identity_hash_key,
                    admission_receipt=binding.admission_receipt,
                    shard_spec=shard_spec,
                    archive_binding=binding.admitted_archive_binding,
                    workspace_root=(workspace_root / shard_spec.shard_id),
                    qc_runtime=qc_runtime,
                    sync_runtime=sync_runtime,
                )
            )
            assert_aihub538_descriptor_identity(receipt_descriptor, before)
            authorizations.append(authorization)
            shard_sources.append(
                Aihub538CalibrationShardSource(
                    shard_spec=shard_spec,
                    authorization=authorization,
                    media_source=media_source,
                )
            )
        multi_receipt = Aihub538MultiShardAdmissionReceipt.issue(
            ts174=authorizations[0],
            ts175=authorizations[1],
            ts176=authorizations[2],
            capacity_receipt=capacity_receipt,
        )
        composite = Aihub538CompositeCalibrationMediaSource(
            sources=(shard_sources[0], shard_sources[1], shard_sources[2]),
            admission_receipt=multi_receipt,
            capacity_receipt=capacity_receipt,
        )
        result = Aihub538DecisionCalibrationService(
            measurement_port=composite,
            protocol=MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL,
        ).calibrate(
            items=composite.inventory,
            admission_receipt=multi_receipt,
            video_qc_measurement_policy=qc_artifact,
            av_sync_measurement_policy=sync_artifact,
        )
        assert_aihub538_descriptor_identity(receipt_descriptor, before)
    return result, multi_receipt, None


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    output_dir: Path | None = None
    try:
        if arguments.dataset_access_permission_confirmed is not True:
            raise Aihub538DecisionCalibrationError(
                "aihub538_dataset_access_permission_not_confirmed"
            )
        output_dir = _validate_directory(
            arguments.output_dir,
            require_mode_0700=False,
        )
        workspace_root = _validate_directory(
            arguments.restricted_workspace_root,
            require_mode_0700=True,
        )
        is_multi = _multi_mode(arguments)
        identity_material, _key_receipt = load_aihub538_identity_key(
            state_directory=arguments.identity_state_dir.absolute()
        )
        if _model_sha256(arguments.landmark_model) != OFFICIAL_FACE_LANDMARKER_SHA256:
            raise Aihub538DecisionCalibrationError(
                "aihub538_landmark_model_integrity_failed"
            )
        qc_artifact, sync_artifact, qc_runtime, sync_runtime = (
            _measurement_policies()
        )
        runner = _run_multi if is_multi else _run_legacy
        result, admission_receipt, candidate_role_binding = runner(
            arguments=arguments,
            identity_hash_key=identity_material.key,
            workspace_root=workspace_root,
            qc_artifact=qc_artifact,
            sync_artifact=sync_artifact,
            qc_runtime=qc_runtime,
            sync_runtime=sync_runtime,
        )
        decision_evidence, paths = (
            calibration_publish.publish_aihub538_decision_calibration_bundle(
                output_dir=output_dir,
                workspace_root=workspace_root,
                repository_root=REPOSITORY_ROOT,
                result=result,
                admission_receipt=admission_receipt,
            )
        )
        print(
            json.dumps(
                {
                    "status": decision_evidence.status,
                    "source_dataset_id": decision_evidence.source_dataset_id,
                    "source_partitions": list(decision_evidence.source_partitions),
                    "evaluated_item_count": decision_evidence.evaluated_item_count,
                    "actor_overlap_count": decision_evidence.actor_overlap_count,
                    "attempt_overlap_count": decision_evidence.attempt_overlap_count,
                    "fresh_process_reload_passed": (
                        decision_evidence.fresh_process_reload_passed
                    ),
                    "frozen": decision_evidence.frozen,
                    "output_file_count": len(paths),
                    "candidate_role_binding": (
                        candidate_role_binding.to_json_object()
                        if candidate_role_binding is not None
                        else None
                    ),
                    "locked_partition_accessed": False,
                    "raw_identifiers_serialized": False,
                    "runtime_promotion_authorized": False,
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
        )
        return 0
    except (
        Aihub538DecisionCalibrationError,
        Aihub538IdentityKeyStoreError,
        Aihub538LabelCapacityPipelineError,
        Aihub538TrainingSmokeError,
        VisualFeedbackContractError,
        OSError,
        ValueError,
    ) as error:
        reason_code = getattr(error, "reason_code", "aihub538_calibration_failed")
        failure_artifact = (
            error.failure_artifact
            if isinstance(error, Aihub538DecisionCalibrationError)
            else None
        )
        failure_output: dict[str, object] = {}
        if failure_artifact is not None and output_dir is not None:
            try:
                failure_path = (
                    calibration_publish.publish_aihub538_decision_calibration_failure(
                        output_dir=output_dir,
                        failure_artifact=failure_artifact,
                    )
                )
            except Aihub538DecisionCalibrationError as publish_error:
                failure_output["failure_artifact_publish_reason_code"] = (
                    publish_error.reason_code
                )
            else:
                failure_output.update(
                    {
                        "failure_artifact_sha256": (
                            failure_artifact.artifact_sha256
                        ),
                        "failure_artifact_basename": failure_path.name,
                        "failure_stage": failure_artifact.failure_stage,
                        "admitted_item_count": (
                            failure_artifact.admitted_item_count
                        ),
                        "eligible_item_count": (
                            failure_artifact.eligible_item_count
                        ),
                        "metadata_excluded_item_count": (
                            failure_artifact.metadata_excluded_item_count
                        ),
                        "calibration_qc_pass_fraction": (
                            failure_artifact.calibration_qc_pass_fraction
                        ),
                        "calibration_sync_pass_fraction": (
                            failure_artifact.calibration_sync_pass_fraction
                        ),
                    }
                )
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "reason_code": reason_code,
                    **failure_output,
                    "promotion_authorized": False,
                    "locked_partition_accessed": False,
                    "raw_identifiers_serialized": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
