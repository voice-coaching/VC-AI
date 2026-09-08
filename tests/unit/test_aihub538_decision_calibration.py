"""Synthetic-adapter tests for AI Hub 538 actual-media decision calibration."""

from __future__ import annotations

from dataclasses import replace
import importlib.util
import json
import math
import os
from pathlib import Path
import select
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

from voice_coach.visual_feedback.adapters import (
    aihub538_decision_calibration_publish as publisher_adapter,
)
from voice_coach.visual_feedback.adapters.aihub538_decision_calibration_publish import (
    AIHUB538_CALIBRATION_OUTPUT_NAMES,
    AIHUB538_MULTI_CALIBRATION_FAILURE_OUTPUT_NAME,
    AIHUB538_MULTI_CALIBRATION_FAILURE_V1_OUTPUT_NAME,
    AIHUB538_MULTI_CALIBRATION_FAILURE_V2_OUTPUT_NAME,
    AIHUB538_MULTI_CALIBRATION_OUTPUT_NAMES,
    load_aihub538_decision_calibration_failure_artifact,
    publish_aihub538_decision_calibration_failure,
    publish_aihub538_decision_calibration_bundle,
)
from voice_coach.pronunciation_attempts.adapters.lip_audio_av_sync import (
    MEASUREMENT_REVISION as AV_SYNC_MEASUREMENT_REVISION,
    AvSyncAnchorCandidate,
    AvSyncCandidateEvidence,
    av_sync_measurement_code_sha256,
)
from voice_coach.pronunciation_attempts.adapters.video_qc import (
    MEASUREMENT_REVISION as VIDEO_QC_MEASUREMENT_REVISION,
    VideoQcCandidateEvidence,
    VideoQcMetrics,
    video_qc_measurement_code_sha256,
)
from voice_coach.visual_feedback.adapters.aihub538_decision_calibration_reload import (
    fresh_process_reload_aihub538_calibration,
)
from voice_coach.visual_feedback.models.aihub538_decision_calibration import (
    AIHUB538_DECISION_CALIBRATION_SCHEMA_VERSION,
    AIHUB538_MULTI_DECISION_CALIBRATION_SCHEMA_VERSION,
    AIHUB538_MULTI_DECISION_CALIBRATION_FAILURE_SCHEMA_VERSION,
    AIHUB538_MULTI_DECISION_CALIBRATION_FAILURE_V2_SCHEMA_VERSION,
    AIHUB538_QC_CANDIDATE_UNAVAILABLE_REASON_CODES,
    AIHUB538_QC_DECISION_FAILURE_REASON_CODES,
    AIHUB538_QC_THRESHOLD_DIAGNOSTIC_SPECS,
    DEFAULT_AIHUB538_DECISION_CALIBRATION_PROTOCOL,
    MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL,
    Aihub538DecisionCalibrationEvidence,
    Aihub538DecisionCalibrationFailureArtifact,
    Aihub538DecisionCalibrationFailureArtifactV2,
    Aihub538DecisionCalibrationProtocol,
)
from voice_coach.visual_feedback.models.aihub538_multishard_admission import (
    AIHUB538_MULTI_ADMISSION_POLICY_SHA256,
    AIHUB538_MULTI_SAMPLE_SELECTION_RULE,
    Aihub538MultiShardAdmissionReceipt,
    Aihub538MultiShardLineage,
)
from voice_coach.visual_feedback.models.aihub538_label_selection_authorization import (
    AIHUB538_FROZEN_SOURCE_AUTHORIZATION_RECEIPT_SHA256,
)
from voice_coach.visual_feedback.models.aihub538_nonlocked_shards import (
    AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS,
    AIHUB538_TS174_TL174_SHARD,
    AIHUB538_TS175_TL175_SHARD,
    AIHUB538_TS176_TL176_SHARD,
    Aihub538NonlockedShardSpec,
)
from voice_coach.visual_feedback.models.aihub538_training_evidence import (
    AIHUB538_TRAINING_ADMISSION_POLICY_SHA256,
    AIHUB538_TRAINING_ANGLES,
    Aihub538RestrictedIdentitySet,
    Aihub538TrainingAdmissionAggregate,
    Aihub538TrainingAdmissionReceipt,
    Aihub538TrainingAngleAggregate,
    Aihub538TrainingSampleAggregate,
    aihub538_shard_admission_policy_sha256,
)
from voice_coach.visual_feedback.models.binding import (
    MediaTimebaseReceipt,
    SameAttemptBindingReceipt,
)
from voice_coach.visual_feedback.models.integrity import (
    canonical_json_bytes,
    sha256_document,
)
from voice_coach.visual_feedback.models.runtime_policy_artifact import (
    AvSyncSemanticPolicy,
    MULTISHARD_RUNTIME_DECISION_POLICY_EVIDENCE_SCHEMA_VERSION,
    P8RuntimePolicyArtifact,
    RUNTIME_DECISION_POLICY_EVIDENCE_SCHEMA_VERSION,
    RuntimeDecisionPolicyEvidence,
    VideoQcSemanticPolicy,
)
from voice_coach.visual_feedback.services.aihub538_decision_calibration import (
    Aihub538CalibrationMeasurement,
    Aihub538CalibrationMediaItem,
    Aihub538DecisionCalibrationError,
    Aihub538DecisionCalibrationService,
    build_aihub538_calibration_split,
)
from voice_coach.visual_feedback.services import (
    aihub538_decision_calibration as calibration_service_module,
)


ROOT = Path(__file__).resolve().parents[2]


def _load_calibration_cli() -> ModuleType:
    path = ROOT / "scripts/calibrate_aihub538_runtime_decisions.py"
    spec = importlib.util.spec_from_file_location(
        "fixture_calibrate_aihub538_runtime_decisions_failure",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    dependency_name = (
        "voice_coach.visual_feedback.adapters.aihub538_training_run_binding"
    )
    previous_dependency = sys.modules.get(dependency_name)
    dependency_stub = ModuleType(dependency_name)

    def open_training_run_stub(**_values: object) -> None:
        return None

    dependency_stub.open_aihub538_authorized_training_run = open_training_run_stub
    sys.modules[dependency_name] = dependency_stub
    try:
        spec.loader.exec_module(module)
    finally:
        if previous_dependency is None:
            sys.modules.pop(dependency_name, None)
        else:
            sys.modules[dependency_name] = previous_dependency
    return module


def _sha(value: str) -> str:
    return sha256_document({"fixture": value})


def _protocol() -> Aihub538DecisionCalibrationProtocol:
    return DEFAULT_AIHUB538_DECISION_CALIBRATION_PROTOCOL


def _items() -> tuple[Aihub538CalibrationMediaItem, ...]:
    items: list[Aihub538CalibrationMediaItem] = []
    # Many actors make both deterministic hash partitions comfortably exceed
    # the two-per-angle fixture quota without weakening production rules.
    for actor_index in range(64):
        actor = _sha(f"actor-{actor_index}")
        for angle in AIHUB538_TRAINING_ANGLES:
            items.append(
                Aihub538CalibrationMediaItem(
                    sample_sha256=_sha(f"sample-{actor_index}-{angle}"),
                    actor_token_sha256=actor,
                    attempt_token_sha256=_sha(f"attempt-{actor_index}-{angle}"),
                    angle=angle,
                )
            )
    return tuple(items)


def _admission_receipt(
    items: tuple[Aihub538CalibrationMediaItem, ...],
) -> Aihub538TrainingAdmissionReceipt:
    samples = tuple(
        Aihub538TrainingSampleAggregate(
            sample_sha256=item.sample_sha256,
            angle=item.angle,
            noise_code=1,
            video_duration_seconds=3.0,
            audio_duration_seconds=3.0,
            frames_per_second=30.0,
            width=1920,
            height=1080,
            lip_bbox_frame_count=90,
            face_bbox_frame_count=90,
            sentence_count=1,
            sentence_duration_seconds=2.0,
            video_size_bytes=1024,
            audio_size_bytes=1024,
            video_crc32=1,
            audio_crc32=2,
        )
        for item in items
    )
    angles = tuple(
        Aihub538TrainingAngleAggregate(
            angle=angle,
            paired_sample_count=sum(item.angle == angle for item in items),
            label_count=sum(item.angle == angle for item in items),
            video_count=sum(item.angle == angle for item in items),
            audio_reference_count=sum(item.angle == angle for item in items),
            lip_bbox_frame_count=90 * sum(item.angle == angle for item in items),
            sentence_count=sum(item.angle == angle for item in items),
        )
        for angle in AIHUB538_TRAINING_ANGLES
    )
    selection_sha256 = sha256_document(
        {
            "schema_version": "voice_coach.aihub538_training_sample_selection.v1",
            "selection_rule": "all_exactly_admitted_ts174_tl174_pairs",
            "admission_policy_sha256": AIHUB538_TRAINING_ADMISSION_POLICY_SHA256,
            "sample_sha256": sorted(item.sample_sha256 for item in items),
        }
    )
    aggregate = Aihub538TrainingAdmissionAggregate(
        label_archive_sha256=_sha("label-archive"),
        source_archive_sha256=_sha("source-archive"),
        label_zip_content_root_sha256=_sha("label-root"),
        source_zip_content_root_sha256=_sha("source-root"),
        samples=samples,
        speaker_count=len({item.actor_token_sha256 for item in items}),
        unique_audio_count=len(items),
        angle_aggregates=angles,
        sample_selection_sha256=selection_sha256,
        admission_manifest_sha256=_sha("admission-manifest"),
        restricted_identity_set=Aihub538RestrictedIdentitySet(
            namespace_commitment_sha256=_sha("identity-namespace"),
            speaker_hmac_sha256=frozenset(
                item.actor_token_sha256 for item in items
            ),
            session_hmac_sha256=frozenset(
                item.attempt_token_sha256 for item in items
            ),
        ),
    )
    return Aihub538TrainingAdmissionReceipt.issue(aggregate)


def _measurement_policies() -> tuple[P8RuntimePolicyArtifact, P8RuntimePolicyArtifact]:
    return (
        P8RuntimePolicyArtifact.issue(
            policy_kind="video_qc",
            policy_revision="video_qc_measurement_v1",
            policy_document=VideoQcSemanticPolicy(
                underexposed_luma_max_8bit=16,
                overexposed_luma_min_8bit=239,
                lip_roi_padding_fraction=0.05,
                minimum_single_face_frames=6,
                maximum_total_rgb_bytes=64 * 1024 * 1024,
            ),
        ),
        P8RuntimePolicyArtifact.issue(
            policy_kind="av_sync",
            policy_revision="lip_audio_av_sync_policy_v1",
            policy_document=AvSyncSemanticPolicy(
                maximum_absolute_offset_ms=300,
                offset_step_ms=20,
                energy_window_ms=40,
                minimum_landmark_frames=6,
                maximum_anchor_count=4,
            ),
        ),
    )


class _MeasurementPort:
    def __init__(
        self,
        *,
        qc_policy_sha256: str,
        sync_policy_sha256: str,
        failed_sync_samples: frozenset[str] = frozenset(),
        unavailable_qc_samples: frozenset[str] = frozenset(),
        qc_unavailable_reason_overrides: (
            dict[str, tuple[str, ...]] | None
        ) = None,
        qc_metric_overrides: (
            dict[str, dict[str, float | None]] | None
        ) = None,
        return_wrong_item: bool = False,
    ) -> None:
        self._qc_policy_sha256 = qc_policy_sha256
        self._sync_policy_sha256 = sync_policy_sha256
        self._failed_sync_samples = failed_sync_samples
        self._unavailable_qc_samples = unavailable_qc_samples
        self._qc_unavailable_reason_overrides = (
            qc_unavailable_reason_overrides or {}
        )
        self._qc_metric_overrides = qc_metric_overrides or {}
        self._return_wrong_item = return_wrong_item

    def measure(
        self, item: Aihub538CalibrationMediaItem
    ) -> Aihub538CalibrationMeasurement:
        container = _sha(f"container-{item.sample_sha256}")
        audio = _sha(f"audio-{item.sample_sha256}")
        asset_handle = _sha(f"handle-{item.sample_sha256}")
        landmark = _sha(f"landmarks-{item.sample_sha256}")
        model = _sha("model")
        derivation = _sha(f"derivation-{item.sample_sha256}")
        binding = SameAttemptBindingReceipt.issue(
            attempt_id_sha256=item.attempt_token_sha256,
            prompt_id_sha256=_sha("prompt"),
            prompt_revision="unit_calibration_v1",
            expected_phone_sha256=_sha("expected"),
            analysis_sha256=_sha(f"analysis-{item.sample_sha256}"),
            audio_source_sha256=audio,
            audio_derivation_receipt_sha256=derivation,
            video_source_sha256=container,
            audio_timebase=MediaTimebaseReceipt(
                clock_id_sha256=_sha("audio-clock"),
                duration_ms=3_000,
                origin_offset_ms=0,
                transform_revision="unit_audio_v1",
            ),
            video_timebase=MediaTimebaseReceipt(
                clock_id_sha256=_sha("video-clock"),
                duration_ms=3_000,
                origin_offset_ms=0,
                transform_revision="unit_video_v1",
            ),
            upstream_reliability_receipt_sha256=_sha("upstream"),
            consent_receipt_sha256=_sha("authorization"),
            issued_at_utc="2026-08-28T00:00:00Z",
        )
        qc = _qc_candidate(
            asset_handle=asset_handle,
            container=container,
            landmark=landmark,
            model=model,
            policy_sha256=self._qc_policy_sha256,
            unavailable_reasons=self._qc_unavailable_reason_overrides.get(
                item.sample_sha256,
                (
                    ("video_qc_single_face_geometry_unavailable",)
                    if item.sample_sha256 in self._unavailable_qc_samples
                    else ()
                ),
            ),
            metric_overrides=self._qc_metric_overrides.get(item.sample_sha256),
        )
        sync = _sync_candidate(
            asset_handle=asset_handle,
            container=container,
            audio=audio,
            derivation=derivation,
            landmark=landmark,
            model=model,
            policy_sha256=self._sync_policy_sha256,
            correlation=(
                0.10 if item.sample_sha256 in self._failed_sync_samples else 0.95
            ),
        )
        returned_item = (
            replace(item, sample_sha256=_sha("wrong-item"))
            if self._return_wrong_item
            else item
        )
        return Aihub538CalibrationMeasurement(
            item=returned_item,
            binding_receipt=binding,
            video_qc_candidate=qc,
            av_sync_candidate=sync,
        )


def _qc_candidate(
    *,
    asset_handle: str,
    container: str,
    landmark: str,
    model: str,
    policy_sha256: str,
    unavailable_reasons: tuple[str, ...] = (),
    metric_overrides: dict[str, float | None] | None = None,
) -> VideoQcCandidateEvidence:
    timestamps_us = tuple(index * 1_000 for index in range(8))
    metrics = VideoQcMetrics(
        frame_count=8,
        single_face_frame_count=8,
        multiple_face_frame_count=0,
        single_face_coverage=1.0,
        multiple_face_frame_fraction=0.0,
        mean_luma_8bit=100.0,
        underexposed_pixel_fraction=0.0,
        overexposed_pixel_fraction=0.0,
        mean_neighbor_luma_delta_8bit=10.0,
        lip_roi_frame_count=8,
        lip_roi_mean_luma_8bit=100.0,
        lip_roi_underexposed_pixel_fraction=0.0,
        lip_roi_overexposed_pixel_fraction=0.0,
        lip_roi_mean_neighbor_luma_delta_8bit=10.0,
        track_transition_coverage=1.0,
        longest_single_face_run_fraction=1.0,
        maximum_lip_center_step_normalized=0.01,
    )
    if unavailable_reasons:
        if not set(unavailable_reasons).issubset(
            {
                "video_qc_single_face_geometry_unavailable",
                "video_qc_single_face_coverage_insufficient",
            }
        ):
            raise AssertionError("unsupported fixture QC unavailable reason")
        metrics = VideoQcMetrics(
            frame_count=8,
            single_face_frame_count=0,
            multiple_face_frame_count=0,
            single_face_coverage=0.0,
            multiple_face_frame_fraction=0.0,
            mean_luma_8bit=100.0,
            underexposed_pixel_fraction=0.0,
            overexposed_pixel_fraction=0.0,
            mean_neighbor_luma_delta_8bit=10.0,
            lip_roi_frame_count=0,
            lip_roi_mean_luma_8bit=None,
            lip_roi_underexposed_pixel_fraction=None,
            lip_roi_overexposed_pixel_fraction=None,
            lip_roi_mean_neighbor_luma_delta_8bit=None,
            track_transition_coverage=0.0,
            longest_single_face_run_fraction=0.0,
            maximum_lip_center_step_normalized=None,
        )
    if metric_overrides:
        metrics = replace(metrics, **metric_overrides)
    status = "unavailable" if unavailable_reasons else "candidate_unvalidated"
    reasons = unavailable_reasons
    provenance = {
        "schema_version": "voice_coach.video_qc_provenance.v2",
        "asset_handle_id": asset_handle,
        "container_sha256": container,
        "video_stream_index": 0,
        "sample_timestamps_us": list(timestamps_us),
        "landmark_timestamps_ms": [value // 1_000 for value in timestamps_us],
        "decoded_rgb_evidence_sha256": _sha(f"rgb-{container}"),
        "landmark_evidence_sha256": landmark,
        "landmark_model_sha256": model,
        "policy_receipt_sha256": policy_sha256,
        "measurement_revision": VIDEO_QC_MEASUREMENT_REVISION,
        "measurement_adapter_code_sha256": video_qc_measurement_code_sha256(),
    }
    unsigned = {
        "schema_version": "voice_coach.video_qc_candidate.v2",
        "status": status,
        **{key: value for key, value in provenance.items() if key != "schema_version"},
        "metrics": metrics.to_json_object(),
        "reason_codes": list(reasons),
        "provenance_receipt_sha256": sha256_document(provenance),
    }
    return VideoQcCandidateEvidence(
        status=status,
        asset_handle_id=asset_handle,
        container_sha256=container,
        video_stream_index=0,
        sample_timestamps_us=timestamps_us,
        landmark_timestamps_ms=tuple(value // 1_000 for value in timestamps_us),
        decoded_rgb_evidence_sha256=provenance["decoded_rgb_evidence_sha256"],
        landmark_evidence_sha256=landmark,
        landmark_model_sha256=model,
        policy_receipt_sha256=policy_sha256,
        measurement_revision=VIDEO_QC_MEASUREMENT_REVISION,
        measurement_adapter_code_sha256=video_qc_measurement_code_sha256(),
        metrics=metrics,
        reason_codes=reasons,
        provenance_receipt_sha256=unsigned["provenance_receipt_sha256"],
        receipt_sha256=sha256_document(unsigned),
    )


def _sync_candidate(
    *,
    asset_handle: str,
    container: str,
    audio: str,
    derivation: str,
    landmark: str,
    model: str,
    policy_sha256: str,
    correlation: float,
) -> AvSyncCandidateEvidence:
    anchors = (
        AvSyncAnchorCandidate(audio_ms=200, video_ms=180, strength=0.90),
        AvSyncAnchorCandidate(audio_ms=400, video_ms=380, strength=0.85),
    )
    lineage = {
        "schema_version": "voice_coach.lip_audio_av_sync_lineage.v2",
        "asset_handle_id": asset_handle,
        "audio_derivation_receipt_sha256": derivation,
        "audio_source_sha256": audio,
        "video_source_sha256": container,
        "audio_stream_index": 1,
        "audio_start_time_us": 0,
        "video_stream_index": 0,
        "video_start_time_us": 0,
        "landmark_evidence_sha256": landmark,
        "landmark_model_sha256": model,
        "measurement_policy_sha256": policy_sha256,
        "measurement_revision": AV_SYNC_MEASUREMENT_REVISION,
        "measurement_adapter_code_sha256": av_sync_measurement_code_sha256(),
    }
    unsigned = {
        "schema_version": "voice_coach.lip_audio_av_sync_candidate.v2",
        **{key: value for key, value in lineage.items() if key != "schema_version"},
        "status": "candidate_unvalidated",
        "candidate_offset_ms": 20,
        "correlation": correlation,
        "anchors": [anchor.to_json_object() for anchor in anchors],
        "reason_codes": [],
        "lineage_receipt_sha256": sha256_document(lineage),
    }
    return AvSyncCandidateEvidence(
        asset_handle_id=asset_handle,
        audio_derivation_receipt_sha256=derivation,
        audio_source_sha256=audio,
        video_source_sha256=container,
        audio_stream_index=1,
        audio_start_time_us=0,
        video_stream_index=0,
        video_start_time_us=0,
        landmark_evidence_sha256=landmark,
        landmark_model_sha256=model,
        measurement_policy_sha256=policy_sha256,
        measurement_revision=AV_SYNC_MEASUREMENT_REVISION,
        measurement_adapter_code_sha256=av_sync_measurement_code_sha256(),
        status="candidate_unvalidated",
        candidate_offset_ms=20,
        correlation=correlation,
        anchors=anchors,
        reason_codes=(),
        lineage_receipt_sha256=unsigned["lineage_receipt_sha256"],
        receipt_sha256=sha256_document(unsigned),
    )


def _successful_result():  # noqa: ANN202
    items = _items()
    admission = _admission_receipt(items)
    qc, sync = _measurement_policies()
    service = Aihub538DecisionCalibrationService(
        measurement_port=_MeasurementPort(
            qc_policy_sha256=qc.policy_sha256,
            sync_policy_sha256=sync.policy_sha256,
        ),
        protocol=_protocol(),
    )
    return service.calibrate(
        items=items,
        admission_receipt=admission,
        video_qc_measurement_policy=qc,
        av_sync_measurement_policy=sync,
    ), admission, items


def _multi_admission_receipt() -> Aihub538MultiShardAdmissionReceipt:
    capacity_receipt_sha256 = (
        AIHUB538_FROZEN_SOURCE_AUTHORIZATION_RECEIPT_SHA256
    )
    shard_specs = (
        AIHUB538_TS174_TL174_SHARD,
        AIHUB538_TS175_TL175_SHARD,
        AIHUB538_TS176_TL176_SHARD,
    )
    lineage = tuple(
        Aihub538MultiShardLineage(
            shard_id=shard_spec.shard_id,
            admission_receipt_sha256=_sha(f"{shard_spec.shard_id}-admission"),
            admission_manifest_sha256=_sha(f"{shard_spec.shard_id}-manifest"),
            label_archive_sha256=_sha(f"{shard_spec.shard_id}-label-archive"),
            source_archive_sha256=_sha(f"{shard_spec.shard_id}-source-archive"),
            label_content_root_sha256=_sha(f"{shard_spec.shard_id}-label-root"),
            source_content_root_sha256=_sha(f"{shard_spec.shard_id}-source-root"),
            paired_sample_count=60,
            pipeline_completion_receipt_sha256=_sha(
                f"{shard_spec.shard_id}-completion"
            ),
            capacity_audit_receipt_sha256=(
                None if index == 0 else capacity_receipt_sha256
            ),
        )
        for index, shard_spec in enumerate(shard_specs)
    )
    draft = Aihub538MultiShardAdmissionReceipt(
        shard_lineage=lineage,
        sample_selection_sha256=_sha("multi-sample-selection"),
        admission_manifest_sha256=_sha("multi-admission-manifest"),
        identity_namespace_commitment_sha256=_sha("identity-namespace"),
        member_namespace_commitment_sha256=_sha("member-namespace"),
        paired_sample_count=180,
        actor_count=5,
        attempt_count=180,
        cross_shard_path_collision_count=0,
        cross_shard_actor_intersection_count=0,
        cross_shard_attempt_intersection_count=0,
        archive_content_root_collision_count=0,
        identity_union_root_sha256=_sha("identity-union"),
        member_union_root_sha256=_sha("member-union"),
        capacity_audit_receipt_sha256=capacity_receipt_sha256,
        capacity_actor_assignment_root_sha256=_sha("actor-assignment"),
        capacity_metadata_excluded_label_count=2,
        capacity_metadata_exclusion_root_sha256=_sha("metadata-exclusion"),
        capacity_development_label_selection_root_sha256=_sha(
            "development-label-selection"
        ),
        capacity_calibration_label_selection_root_sha256=_sha(
            "calibration-label-selection"
        ),
        frozen_calibration_protocol_sha256=(
            MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL.protocol_sha256
        ),
        receipt_sha256="0" * 64,
        admission_policy_sha256=AIHUB538_MULTI_ADMISSION_POLICY_SHA256,
        sample_selection_rule=AIHUB538_MULTI_SAMPLE_SELECTION_RULE,
    )
    receipt = replace(
        draft,
        receipt_sha256=sha256_document(draft.unsigned_json_object()),
    )
    receipt._validate()
    return receipt


def _multi_result():  # noqa: ANN202
    result, _legacy_admission, items = _successful_result()
    admission = _multi_admission_receipt()
    legacy_evidence = result.calibration_evidence
    values = {
        name: getattr(legacy_evidence, name)
        for name in legacy_evidence.__dataclass_fields__
        if name
        not in {
            "receipt_sha256",
            "schema_version",
            "source_partitions",
            "admitted_item_count",
            "metadata_excluded_item_count",
        }
    }
    values.update(
        {
            "admission_receipt_sha256": admission.receipt_sha256,
            "identity_namespace_commitment_sha256": (
                admission.identity_namespace_commitment_sha256
            ),
            "evaluation_protocol_sha256": (
                MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL.protocol_sha256
            ),
            "source_partitions": AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS,
            "admitted_item_count": legacy_evidence.eligible_item_count + 2,
            "metadata_excluded_item_count": 2,
        }
    )
    multi_evidence = Aihub538DecisionCalibrationEvidence.issue(**values)
    return replace(result, calibration_evidence=multi_evidence), admission, items


def _multishard_stop_failure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    failed_sync_samples: frozenset[str] = frozenset(),
    development_qc_unavailable: bool = False,
    qc_unavailable_reason_overrides: (
        dict[str, tuple[str, ...]] | None
    ) = None,
    qc_metric_overrides: (
        dict[str, dict[str, float | None]] | None
    ) = None,
) -> tuple[
    Aihub538DecisionCalibrationError,
    tuple[Aihub538CalibrationMediaItem, ...],
]:
    items = _items()
    legacy_split = build_aihub538_calibration_split(
        items=items,
        admission_receipt=_admission_receipt(items),
        protocol=_protocol(),
    )
    split = replace(
        legacy_split,
        admitted_item_count=180,
        eligible_item_count=178,
        metadata_excluded_item_count=2,
        source_manifest_sha256=_sha("multishard-failure-source-manifest"),
    )
    monkeypatch.setattr(
        calibration_service_module,
        "build_aihub538_calibration_split",
        lambda **_ignored: split,
    )
    unavailable_qc_samples = (
        frozenset(item.sample_sha256 for item in split.development)
        if development_qc_unavailable
        else frozenset()
    )
    qc, sync = _measurement_policies()
    service = Aihub538DecisionCalibrationService(
        measurement_port=_MeasurementPort(
            qc_policy_sha256=qc.policy_sha256,
            sync_policy_sha256=sync.policy_sha256,
            failed_sync_samples=failed_sync_samples,
            unavailable_qc_samples=unavailable_qc_samples,
            qc_unavailable_reason_overrides=qc_unavailable_reason_overrides,
            qc_metric_overrides=qc_metric_overrides,
        ),
        protocol=MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL,
    )
    with pytest.raises(Aihub538DecisionCalibrationError) as captured:
        service.calibrate(
            items=items,
            admission_receipt=_multi_admission_receipt(),
            video_qc_measurement_policy=qc,
            av_sync_measurement_policy=sync,
        )
    return captured.value, split.calibration


def _reissue_evidence(
    evidence: Aihub538DecisionCalibrationEvidence,
    **updates: object,
) -> Aihub538DecisionCalibrationEvidence:
    values = {
        name: getattr(evidence, name)
        for name in evidence.__dataclass_fields__
        if name != "receipt_sha256"
    }
    values.update(updates)
    return Aihub538DecisionCalibrationEvidence.issue(**values)


def test_split_is_deterministic_actor_attempt_disjoint_and_angle_balanced() -> None:
    items = _items()
    admission = _admission_receipt(items)

    first = build_aihub538_calibration_split(
        items=items,
        admission_receipt=admission,
        protocol=_protocol(),
    )
    second = build_aihub538_calibration_split(
        items=tuple(reversed(items)),
        admission_receipt=admission,
        protocol=_protocol(),
    )

    assert first == second
    assert first.actor_overlap_count == 0
    assert first.attempt_overlap_count == 0
    assert {
        item.actor_token_sha256 for item in first.development
    }.isdisjoint(item.actor_token_sha256 for item in first.calibration)
    for angle in AIHUB538_TRAINING_ANGLES:
        assert sum(item.angle == angle for item in first.development) == 8
        assert sum(item.angle == angle for item in first.calibration) == 8


def test_service_derives_fixed_quantile_policies_and_aggregate_only_evidence() -> None:
    result, _admission, items = _successful_result()
    qc = result.video_qc_decision_policy.policy_document
    sync = result.av_sync_decision_policy.policy_document

    assert qc.minimum_single_face_coverage == 0.98
    assert qc.maximum_multiple_face_frame_fraction == 0.02
    assert qc.minimum_mean_luma_8bit == 95.0
    assert qc.maximum_mean_luma_8bit == 105.0
    assert qc.minimum_lip_roi_mean_neighbor_luma_delta_8bit == 9.5
    assert qc.maximum_lip_center_step_normalized == 0.03
    assert sync.maximum_absolute_offset_ms == 40
    assert sync.minimum_correlation == 0.90
    assert sync.minimum_anchor_count == 2
    assert sync.minimum_anchor_strength == 0.85

    evidence = result.calibration_evidence
    assert evidence.development_selected_count == 72
    assert evidence.calibration_selected_count == 72
    assert evidence.qc_decision_lineage_valid_item_count == 72
    assert evidence.sync_decision_lineage_valid_item_count == 72
    serialized = canonical_json_bytes(evidence.to_json_object()).decode("utf-8")
    assert "raw_paths_serialized\":false" in serialized
    assert "transcripts_serialized\":false" in serialized
    assert "sample_level_digests_serialized\":false" in serialized
    assert "/restricted/private/member" not in serialized
    assert "민감 전사 문장" not in serialized
    assert all(item.actor_token_sha256 not in serialized for item in items)
    assert (
        Aihub538DecisionCalibrationEvidence.from_json_object(
            json.loads(serialized)
        )
        == evidence
    )


def test_legacy_v1_shape_is_unchanged_and_multishard_requires_v2() -> None:
    legacy_result, _legacy_admission, _items_value = _successful_result()
    legacy_document = legacy_result.calibration_evidence.to_json_object()
    assert legacy_document["schema_version"] == (
        AIHUB538_DECISION_CALIBRATION_SCHEMA_VERSION
    )
    assert legacy_document["source_partitions"] == ["TS174", "TL174"]
    assert set(legacy_document) == {
        "schema_version",
        "status",
        "source_dataset_id",
        "source_partitions",
        "locked_partition_accessed",
        "source_manifest_sha256",
        "admission_receipt_sha256",
        "identity_namespace_commitment_sha256",
        "evaluation_protocol_sha256",
        "evaluator_code_sha256",
        "video_qc_measurement_policy_artifact_sha256",
        "video_qc_measurement_policy_sha256",
        "av_sync_measurement_policy_artifact_sha256",
        "av_sync_measurement_policy_sha256",
        "video_qc_decision_policy_artifact_sha256",
        "video_qc_decision_policy_sha256",
        "av_sync_decision_policy_artifact_sha256",
        "av_sync_decision_policy_sha256",
        "eligible_item_count",
        "development_selected_count",
        "calibration_selected_count",
        "development_actor_count",
        "calibration_actor_count",
        "actor_overlap_count",
        "attempt_overlap_count",
        "qc_decision_lineage_valid_item_count",
        "sync_decision_lineage_valid_item_count",
        "angle_evidence",
        "actual_media_decode_executed",
        "actual_landmark_tracker_executed",
        "actual_pcm_sync_measurement_executed",
        "measurement_to_decision_lineage_complete",
        "aggregate_only",
        "raw_paths_serialized",
        "transcripts_serialized",
        "actor_identifiers_serialized",
        "attempt_identifiers_serialized",
        "sample_level_digests_serialized",
        "runtime_promotion_authorized",
        "receipt_sha256",
    }
    assert "admitted_item_count" not in legacy_document
    assert "metadata_excluded_item_count" not in legacy_document
    assert "metadata_bbox_eligibility_rule" not in legacy_document

    multi_result, _multi_admission, _items_value = _multi_result()
    multi_evidence = multi_result.calibration_evidence
    multi_document = multi_evidence.to_json_object()
    assert multi_document["schema_version"] == (
        AIHUB538_MULTI_DECISION_CALIBRATION_SCHEMA_VERSION
    )
    assert multi_document["source_partitions"] == list(
        AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS
    )
    assert multi_document["admitted_item_count"] == (
        multi_evidence.eligible_item_count
        + multi_evidence.metadata_excluded_item_count
    )
    assert (
        Aihub538DecisionCalibrationEvidence.from_json_object(multi_document)
        == multi_evidence
    )

    for document, wrong_schema in (
        (dict(multi_document), AIHUB538_DECISION_CALIBRATION_SCHEMA_VERSION),
        (
            dict(legacy_document),
            AIHUB538_MULTI_DECISION_CALIBRATION_SCHEMA_VERSION,
        ),
    ):
        document["schema_version"] = wrong_schema
        document["receipt_sha256"] = sha256_document(
            {key: value for key, value in document.items() if key != "receipt_sha256"}
        )
        with pytest.raises(
            ValueError,
            match="decision calibration evidence schema boundary differs",
        ):
            Aihub538DecisionCalibrationEvidence.from_json_object(document)


def test_calibration_stop_failure_issues_no_result() -> None:
    items = _items()
    admission = _admission_receipt(items)
    qc, sync = _measurement_policies()
    split = build_aihub538_calibration_split(
        items=items,
        admission_receipt=admission,
        protocol=_protocol(),
    )
    failed = frozenset(item.sample_sha256 for item in split.calibration)
    service = Aihub538DecisionCalibrationService(
        measurement_port=_MeasurementPort(
            qc_policy_sha256=qc.policy_sha256,
            sync_policy_sha256=sync.policy_sha256,
            failed_sync_samples=failed,
        ),
        protocol=_protocol(),
    )

    with pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_calibration_per_angle_stop_policy_failed",
    ):
        service.calibrate(
            items=items,
            admission_receipt=admission,
            video_qc_measurement_policy=qc,
            av_sync_measurement_policy=sync,
        )


def test_multishard_development_failure_carries_strict_aggregate_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error, _calibration = _multishard_stop_failure(
        monkeypatch,
        development_qc_unavailable=True,
    )

    assert error.reason_code == (
        "aihub538_development_candidate_availability_below_stop_policy"
    )
    artifact = error.failure_artifact
    assert type(artifact) is Aihub538DecisionCalibrationFailureArtifactV2
    assert artifact.failure_stage == "development_availability"
    assert len(artifact.angle_diagnostics) == 9
    assert artifact.development_qc_available_count == 0
    assert artifact.calibration_qc_available_count == 0
    assert artifact.calibration_qc_pass_fraction is None
    assert artifact.decision_policies_derived is False
    assert artifact.diagnostic_video_qc_decision_policy is None
    for diagnostic in artifact.angle_qc_diagnostics:
        development_reasons = {
            item.reason_code: item.occurrence_count
            for item in diagnostic.development_candidate_unavailable_reason_counts
        }
        assert tuple(development_reasons) == (
            AIHUB538_QC_CANDIDATE_UNAVAILABLE_REASON_CODES
        )
        assert development_reasons[
            "video_qc_single_face_geometry_unavailable"
        ] == 8
        assert all(
            item.occurrence_count == 0
            for item in (
                diagnostic.calibration_candidate_unavailable_reason_counts
                + diagnostic.calibration_decision_failure_reason_counts
            )
        )
        assert diagnostic.development_threshold_margin_histograms == ()
        assert diagnostic.calibration_threshold_margin_histograms == ()
    assert (
        Aihub538DecisionCalibrationFailureArtifactV2.from_json_object(
            artifact.to_json_object()
        )
        == artifact
    )


def test_v2_development_failure_counts_derived_missing_metric_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = _items()
    split = build_aihub538_calibration_split(
        items=items,
        admission_receipt=_admission_receipt(items),
        protocol=_protocol(),
    )
    error, _calibration = _multishard_stop_failure(
        monkeypatch,
        qc_metric_overrides={
            item.sample_sha256: {
                "maximum_lip_center_step_normalized": None,
            }
            for item in split.development
        },
    )

    artifact = error.failure_artifact
    assert type(artifact) is Aihub538DecisionCalibrationFailureArtifactV2
    assert artifact.failure_stage == "development_availability"
    for diagnostic in artifact.angle_qc_diagnostics:
        assert diagnostic.development_source_candidate_available_count == 8
        assert diagnostic.calibration_source_candidate_available_count == 0
        assert (
            diagnostic.development_optional_metric_missing_combinations
            .lip_center_step_only_missing_count
            == 8
        )
        assert (
            diagnostic.development_optional_metric_missing_combinations
            .both_optional_metrics_missing_count
            == 0
        )
        development_reasons = {
            item.reason_code: item.occurrence_count
            for item in diagnostic.development_candidate_unavailable_reason_counts
        }
        assert development_reasons[
            "video_qc_lip_center_step_metric_unavailable"
        ] == 8


def test_v2_calibration_failure_reconciles_unbinned_missing_metric_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = _items()
    split = build_aihub538_calibration_split(
        items=items,
        admission_receipt=_admission_receipt(items),
        protocol=_protocol(),
    )
    failed_angle = AIHUB538_TRAINING_ANGLES[0]
    missing_metric_rows = tuple(
        item for item in split.calibration if item.angle == failed_angle
    )[:5]
    error, _calibration = _multishard_stop_failure(
        monkeypatch,
        qc_metric_overrides={
            item.sample_sha256: {
                "maximum_lip_center_step_normalized": None,
                "mean_luma_8bit": 120.0,
            }
            for item in missing_metric_rows
        },
    )

    artifact = error.failure_artifact
    assert type(artifact) is Aihub538DecisionCalibrationFailureArtifactV2
    diagnostic = next(
        item for item in artifact.angle_qc_diagnostics if item.angle == failed_angle
    )
    summary = next(
        item
        for item in artifact.failure_summary.angle_diagnostics
        if item.angle == failed_angle
    )
    availability_counts = {
        item.reason_code: item.occurrence_count
        for item in diagnostic.calibration_candidate_unavailable_reason_counts
    }
    decision_counts = {
        item.reason_code: item.occurrence_count
        for item in diagnostic.calibration_decision_failure_reason_counts
    }
    histograms = {
        item.reason_code: item
        for item in diagnostic.calibration_threshold_margin_histograms
    }
    assert availability_counts[
        "video_qc_lip_center_step_metric_unavailable"
    ] == 5
    assert diagnostic.calibration_source_candidate_available_count == 8
    assert (
        diagnostic.calibration_optional_metric_missing_combinations
        .lip_center_step_only_missing_count
        == 5
    )
    assert (
        diagnostic.calibration_optional_metric_missing_combinations
        .both_optional_metrics_missing_count
        == 0
    )
    assert summary.calibration_qc_available_count == 3
    assert decision_counts["video_qc_source_candidate_unavailable"] == 0
    assert decision_counts["video_qc_mean_luma_above_policy"] == 5
    assert decision_counts["video_qc_lip_center_step_above_policy"] == 5
    assert histograms[
        "video_qc_mean_luma_above_policy"
    ].population_count == 8
    assert histograms[
        "video_qc_mean_luma_above_policy"
    ].failure_count == 5
    assert histograms[
        "video_qc_lip_center_step_above_policy"
    ].population_count == 3
    assert histograms[
        "video_qc_lip_center_step_above_policy"
    ].failure_count == 0

    old_whole_row_exclusion = json.loads(
        json.dumps(artifact.to_json_object())
    )
    mean_histogram = next(
        item
        for item in old_whole_row_exclusion["angle_qc_diagnostics"][0][
            "calibration_threshold_margin_histograms"
        ]
        if item["reason_code"] == "video_qc_mean_luma_above_policy"
    )
    assert mean_histogram["failed_beyond_margin_count"] == 5
    mean_histogram["failed_beyond_margin_count"] = 0
    old_whole_row_exclusion["artifact_sha256"] = sha256_document(
        {
            key: value
            for key, value in old_whole_row_exclusion.items()
            if key != "artifact_sha256"
        }
    )
    with pytest.raises(ValueError, match="histogram"):
        Aihub538DecisionCalibrationFailureArtifactV2.from_json_object(
            old_whole_row_exclusion
        )


def test_v2_rejects_impossible_overlap_of_both_optional_metric_missing_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = _items()
    split = build_aihub538_calibration_split(
        items=items,
        admission_receipt=_admission_receipt(items),
        protocol=_protocol(),
    )
    failed_angle = AIHUB538_TRAINING_ANGLES[0]
    missing_rows = tuple(
        item for item in split.calibration if item.angle == failed_angle
    )[:5]
    error, _calibration = _multishard_stop_failure(
        monkeypatch,
        qc_metric_overrides={
            item.sample_sha256: {
                "maximum_lip_center_step_normalized": None,
            }
            for item in missing_rows
        },
    )
    artifact = error.failure_artifact
    assert type(artifact) is Aihub538DecisionCalibrationFailureArtifactV2

    underreported = json.loads(json.dumps(artifact.to_json_object()))
    candidate_rows = underreported["angle_qc_diagnostics"][0][
        "calibration_candidate_unavailable_reason_counts"
    ]
    next(
        item
        for item in candidate_rows
        if item["reason_code"]
        == "video_qc_lip_center_step_metric_unavailable"
    )["occurrence_count"] = 4
    underreported["artifact_sha256"] = sha256_document(
        {
            key: value
            for key, value in underreported.items()
            if key != "artifact_sha256"
        }
    )
    with pytest.raises(ValueError, match="optional metric missing combinations"):
        Aihub538DecisionCalibrationFailureArtifactV2.from_json_object(
            underreported
        )

    overlap = json.loads(json.dumps(artifact.to_json_object()))
    angle_diagnostic = overlap["angle_qc_diagnostics"][0]
    missing_combinations = angle_diagnostic[
        "calibration_optional_metric_missing_combinations"
    ]
    missing_combinations["lip_center_step_only_missing_count"] = 0
    missing_combinations["both_optional_metrics_missing_count"] = 5
    next(
        item
        for item in angle_diagnostic[
            "calibration_candidate_unavailable_reason_counts"
        ]
        if item["reason_code"] == "video_qc_lip_edge_metric_unavailable"
    )["occurrence_count"] = 5
    next(
        item
        for item in angle_diagnostic[
            "calibration_decision_failure_reason_counts"
        ]
        if item["reason_code"] == "video_qc_lip_edge_delta_below_policy"
    )["occurrence_count"] = 5
    lip_edge_histogram = next(
        item
        for item in angle_diagnostic[
            "calibration_threshold_margin_histograms"
        ]
        if item["reason_code"] == "video_qc_lip_edge_delta_below_policy"
    )
    for name in (
        "failed_beyond_margin_count",
        "failed_within_margin_count",
        "passed_within_margin_count",
        "passed_beyond_margin_count",
    ):
        lip_edge_histogram[name] = 0
    lip_edge_histogram["passed_beyond_margin_count"] = 3
    overlap["artifact_sha256"] = sha256_document(
        {
            key: value
            for key, value in overlap.items()
            if key != "artifact_sha256"
        }
    )

    with pytest.raises(
        ValueError,
        match="source-available lip edge metric cannot be missing",
    ):
        Aihub538DecisionCalibrationFailureArtifactV2.from_json_object(overlap)


def test_v2_reason_counts_allow_overlapping_reasons_for_one_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = _items()
    split = build_aihub538_calibration_split(
        items=items,
        admission_receipt=_admission_receipt(items),
        protocol=_protocol(),
    )
    error, _calibration = _multishard_stop_failure(
        monkeypatch,
        qc_unavailable_reason_overrides={
            item.sample_sha256: (
                "video_qc_single_face_geometry_unavailable",
                "video_qc_single_face_coverage_insufficient",
            )
            for item in split.development
        },
    )
    artifact = error.failure_artifact
    assert type(artifact) is Aihub538DecisionCalibrationFailureArtifactV2
    assert (
        artifact.angle_qc_diagnostics[0]
        .development_source_candidate_available_count
        == 0
    )
    source_reason_counts = {
        item.reason_code: item.occurrence_count
        for item in artifact.angle_qc_diagnostics[
            0
        ].development_candidate_unavailable_reason_counts
    }
    assert source_reason_counts[
        "video_qc_single_face_geometry_unavailable"
    ] == 8
    assert source_reason_counts[
        "video_qc_single_face_coverage_insufficient"
    ] == 8
    underreported = json.loads(json.dumps(artifact.to_json_object()))
    for row in underreported["angle_qc_diagnostics"][0][
        "development_candidate_unavailable_reason_counts"
    ]:
        row["occurrence_count"] = 0
    underreported["artifact_sha256"] = sha256_document(
        {
            key: value
            for key, value in underreported.items()
            if key != "artifact_sha256"
        }
    )
    with pytest.raises(ValueError, match="reason coverage differs"):
        Aihub538DecisionCalibrationFailureArtifactV2.from_json_object(
            underreported
        )

    reloaded = Aihub538DecisionCalibrationFailureArtifactV2.from_json_object(
        artifact.to_json_object()
    )

    assert reloaded.angle_qc_diagnostics[0].angle == AIHUB538_TRAINING_ANGLES[0]


def test_v2_calibration_source_unavailable_multi_reason_is_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = _items()
    split = build_aihub538_calibration_split(
        items=items,
        admission_receipt=_admission_receipt(items),
        protocol=_protocol(),
    )
    failed_angle = AIHUB538_TRAINING_ANGLES[0]
    unavailable_rows = tuple(
        item for item in split.calibration if item.angle == failed_angle
    )[:5]
    error, _calibration = _multishard_stop_failure(
        monkeypatch,
        qc_unavailable_reason_overrides={
            item.sample_sha256: (
                "video_qc_single_face_geometry_unavailable",
                "video_qc_single_face_coverage_insufficient",
            )
            for item in unavailable_rows
        },
    )
    artifact = error.failure_artifact
    assert type(artifact) is Aihub538DecisionCalibrationFailureArtifactV2
    diagnostic = next(
        item for item in artifact.angle_qc_diagnostics if item.angle == failed_angle
    )
    source_reason_counts = {
        item.reason_code: item.occurrence_count
        for item in diagnostic.calibration_candidate_unavailable_reason_counts
    }
    decision_counts = {
        item.reason_code: item.occurrence_count
        for item in diagnostic.calibration_decision_failure_reason_counts
    }
    assert diagnostic.calibration_source_candidate_available_count == 3
    assert source_reason_counts[
        "video_qc_single_face_geometry_unavailable"
    ] == 5
    assert source_reason_counts[
        "video_qc_single_face_coverage_insufficient"
    ] == 5
    assert decision_counts["video_qc_source_candidate_unavailable"] == 5
    assert all(
        histogram.population_count == 3
        for histogram in diagnostic.calibration_threshold_margin_histograms
    )

    forged_source_count = json.loads(json.dumps(artifact.to_json_object()))
    forged_source_count["angle_qc_diagnostics"][0][
        "calibration_source_candidate_available_count"
    ] = 4
    forged_source_count["artifact_sha256"] = sha256_document(
        {
            key: value
            for key, value in forged_source_count.items()
            if key != "artifact_sha256"
        }
    )
    with pytest.raises(ValueError, match="source unavailable reason coverage"):
        Aihub538DecisionCalibrationFailureArtifactV2.from_json_object(
            forged_source_count
        )


def test_multishard_per_angle_failure_carries_exact_144_measurement_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = _items()
    legacy_split = build_aihub538_calibration_split(
        items=items,
        admission_receipt=_admission_receipt(items),
        protocol=_protocol(),
    )
    first_angle = AIHUB538_TRAINING_ANGLES[0]
    failed = frozenset(
        item.sample_sha256
        for item in legacy_split.calibration
        if item.angle == first_angle
    )
    error, _calibration = _multishard_stop_failure(
        monkeypatch,
        failed_sync_samples=failed,
    )

    assert error.reason_code == (
        "aihub538_calibration_per_angle_stop_policy_failed"
    )
    artifact = error.failure_artifact
    assert type(artifact) is Aihub538DecisionCalibrationFailureArtifactV2
    assert artifact.failure_stage == "per_angle_stop_policy"
    assert artifact.development_selected_count == 72
    assert artifact.calibration_selected_count == 72
    assert artifact.development_measurement_count == 72
    assert artifact.calibration_measurement_count == 72
    assert artifact.decision_evaluated_count == 72
    assert artifact.sync_lineage_valid_count == 72
    assert artifact.calibration_sync_passed_count == 64
    assert artifact.decision_policies_derived is True


def test_multishard_global_failure_is_distinct_and_privacy_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = _items()
    legacy_split = build_aihub538_calibration_split(
        items=items,
        admission_receipt=_admission_receipt(items),
        protocol=_protocol(),
    )
    failed = frozenset(
        item.sample_sha256
        for angle in AIHUB538_TRAINING_ANGLES
        for item in tuple(
            row for row in legacy_split.calibration if row.angle == angle
        )[:4]
    )
    error, _calibration = _multishard_stop_failure(
        monkeypatch,
        failed_sync_samples=failed,
    )

    assert error.reason_code == "aihub538_calibration_global_stop_policy_failed"
    artifact = error.failure_artifact
    assert type(artifact) is Aihub538DecisionCalibrationFailureArtifactV2
    assert artifact.failure_stage == "global_stop_policy"
    assert artifact.calibration_sync_passed_count == 36
    assert artifact.calibration_sync_pass_fraction == 0.5
    document = artifact.to_json_object()
    serialized = canonical_json_bytes(document).decode("utf-8")
    forbidden_fragments = (
        '"sample_sha256":',
        '"actor_token":',
        '"attempt_token":',
        '"transcript":',
        '"archive_member":',
        "/restricted/",
        '"hmac_sha256":',
    )
    assert all(fragment not in serialized for fragment in forbidden_fragments)
    assert document["locked_partition_accessed"] is False
    assert document["promotion_authorized"] is False
    assert document["raw_identifiers_serialized"] is False
    tampered = dict(document)
    tampered["sample_sha256"] = _sha("forbidden-sample")
    with pytest.raises(ValueError, match="fields differ"):
        Aihub538DecisionCalibrationFailureArtifactV2.from_json_object(tampered)

    semantically_forged = artifact.failure_summary.to_json_object()
    semantically_forged.update(
        {
            "reason_code": (
                "aihub538_calibration_per_angle_stop_policy_failed"
            ),
            "failure_stage": "per_angle_stop_policy",
        }
    )
    semantically_forged["artifact_sha256"] = sha256_document(
        {
            key: value
            for key, value in semantically_forged.items()
            if key != "artifact_sha256"
        }
    )
    with pytest.raises(ValueError, match="stop reason is unproven"):
        Aihub538DecisionCalibrationFailureArtifact.from_json_object(
            semantically_forged
        )


def test_v2_qc_failure_preserves_fixed_reason_counts_policy_and_histograms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = _items()
    split = build_aihub538_calibration_split(
        items=items,
        admission_receipt=_admission_receipt(items),
        protocol=_protocol(),
    )
    failed_angle = AIHUB538_TRAINING_ANGLES[0]
    failed_rows = tuple(
        item for item in split.calibration if item.angle == failed_angle
    )
    overrides = {
        item.sample_sha256: {
            "mean_luma_8bit": 120.0,
            "lip_roi_mean_neighbor_luma_delta_8bit": 0.0,
            "maximum_lip_center_step_normalized": 0.10,
        }
        for item in failed_rows
    }

    error, _calibration = _multishard_stop_failure(
        monkeypatch,
        qc_metric_overrides=overrides,
    )

    assert error.reason_code == (
        "aihub538_calibration_per_angle_stop_policy_failed"
    )
    artifact = error.failure_artifact
    assert type(artifact) is Aihub538DecisionCalibrationFailureArtifactV2
    assert artifact.failure_summary.calibration_qc_passed_count == 64
    policy = artifact.diagnostic_video_qc_decision_policy
    assert policy is not None
    assert policy.policy_kind == "video_qc_decision"
    assert policy.policy_sha256 == (
        artifact.failure_summary.video_qc_decision_policy_sha256
    )
    assert policy.artifact_sha256 == (
        artifact.failure_summary.video_qc_decision_policy_artifact_sha256
    )
    diagnostic = next(
        item for item in artifact.angle_qc_diagnostics if item.angle == failed_angle
    )
    reason_counts = {
        item.reason_code: item.occurrence_count
        for item in diagnostic.calibration_decision_failure_reason_counts
    }
    assert tuple(reason_counts) == AIHUB538_QC_DECISION_FAILURE_REASON_CODES
    assert reason_counts["video_qc_mean_luma_above_policy"] == 8
    assert reason_counts["video_qc_lip_edge_delta_below_policy"] == 8
    assert reason_counts["video_qc_lip_center_step_above_policy"] == 8
    # Eight distinct failed clips legitimately produce 24 reason occurrences.
    assert sum(reason_counts.values()) == 24
    histograms = {
        item.reason_code: item
        for item in diagnostic.calibration_threshold_margin_histograms
    }
    assert tuple(histograms) == tuple(
        item[0] for item in AIHUB538_QC_THRESHOLD_DIAGNOSTIC_SPECS
    )
    for reason_code in (
        "video_qc_mean_luma_above_policy",
        "video_qc_lip_edge_delta_below_policy",
        "video_qc_lip_center_step_above_policy",
    ):
        assert histograms[reason_code].failed_beyond_margin_count == 8
        assert histograms[reason_code].failure_count == 8
        assert histograms[reason_code].population_count == 8
    assert (
        Aihub538DecisionCalibrationFailureArtifactV2.from_json_object(
            artifact.to_json_object()
        )
        == artifact
    )


def test_qc_threshold_histogram_boundaries_are_frozen_and_deterministic() -> None:
    result, _admission, _items_value = _successful_result()
    policy = result.video_qc_decision_policy.policy_document
    qc, sync = _measurement_policies()
    threshold = policy.minimum_mean_luma_8bit
    margin = MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL.qc_luma_margin_8bit
    values = (
        math.nextafter(threshold - margin, -math.inf),
        threshold - margin,
        threshold,
        threshold + margin,
        math.nextafter(threshold + margin, math.inf),
    )
    selected = _items()[: len(values)]
    port = _MeasurementPort(
        qc_policy_sha256=qc.policy_sha256,
        sync_policy_sha256=sync.policy_sha256,
        qc_metric_overrides={
            item.sample_sha256: {"mean_luma_8bit": value}
            for item, value in zip(selected, values, strict=True)
        },
    )
    measurements = tuple(port.measure(item) for item in selected)

    histograms = calibration_service_module._qc_threshold_margin_histograms(
        measurements=measurements,
        policy=policy,
    )
    histogram = next(
        item
        for item in histograms
        if item.reason_code == "video_qc_mean_luma_below_policy"
    )

    assert histogram.failed_beyond_margin_count == 1
    assert histogram.failed_within_margin_count == 1
    assert histogram.passed_within_margin_count == 2
    assert histogram.passed_beyond_margin_count == 1


def test_v2_rejects_policy_catalog_and_histogram_forgery_after_outer_redigest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = _items()
    split = build_aihub538_calibration_split(
        items=items,
        admission_receipt=_admission_receipt(items),
        protocol=_protocol(),
    )
    failed_angle = AIHUB538_TRAINING_ANGLES[0]
    overrides = {
        item.sample_sha256: {"maximum_lip_center_step_normalized": 0.10}
        for item in split.calibration
        if item.angle == failed_angle
    }
    error, _calibration = _multishard_stop_failure(
        monkeypatch,
        qc_metric_overrides=overrides,
    )
    artifact = error.failure_artifact
    assert type(artifact) is Aihub538DecisionCalibrationFailureArtifactV2

    def redigest(document: dict[str, object]) -> dict[str, object]:
        document["artifact_sha256"] = sha256_document(
            {
                key: value
                for key, value in document.items()
                if key != "artifact_sha256"
            }
        )
        return document

    policy = artifact.diagnostic_video_qc_decision_policy
    assert policy is not None
    forged_policy_document = replace(
        policy.policy_document,
        maximum_lip_center_step_normalized=(
            policy.policy_document.maximum_lip_center_step_normalized + 0.01
        ),
    )
    forged_policy = P8RuntimePolicyArtifact.issue(
        policy_kind="video_qc_decision",
        policy_revision=policy.policy_revision,
        policy_document=forged_policy_document,
    )
    policy_forgery = json.loads(json.dumps(artifact.to_json_object()))
    policy_forgery["diagnostic_video_qc_decision_policy"] = (
        forged_policy.to_json_object()
    )
    with pytest.raises(ValueError, match="diagnostic decision policy differs"):
        Aihub538DecisionCalibrationFailureArtifactV2.from_json_object(
            redigest(policy_forgery)
        )

    catalog_forgery = json.loads(json.dumps(artifact.to_json_object()))
    rows = catalog_forgery["angle_qc_diagnostics"][0][
        "calibration_decision_failure_reason_counts"
    ]
    rows[0], rows[1] = rows[1], rows[0]
    with pytest.raises(ValueError, match="catalog differs"):
        Aihub538DecisionCalibrationFailureArtifactV2.from_json_object(
            redigest(catalog_forgery)
        )

    histogram_forgery = json.loads(json.dumps(artifact.to_json_object()))
    histogram_forgery["angle_qc_diagnostics"][0][
        "calibration_threshold_margin_histograms"
    ][-1]["failed_beyond_margin_count"] += 1
    with pytest.raises(ValueError, match="histogram"):
        Aihub538DecisionCalibrationFailureArtifactV2.from_json_object(
            redigest(histogram_forgery)
        )

    missing_histograms = json.loads(json.dumps(artifact.to_json_object()))
    missing_histograms["angle_qc_diagnostics"][0][
        "development_threshold_margin_histograms"
    ] = []
    missing_histograms["angle_qc_diagnostics"][0][
        "calibration_threshold_margin_histograms"
    ] = []
    with pytest.raises(ValueError, match="complete QC histograms"):
        Aihub538DecisionCalibrationFailureArtifactV2.from_json_object(
            redigest(missing_histograms)
        )


def test_v2_diagnostic_serializes_no_fixture_identity_or_sample_metric_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error, _calibration = _multishard_stop_failure(
        monkeypatch,
        failed_sync_samples=frozenset(
            item.sample_sha256
            for item in build_aihub538_calibration_split(
                items=_items(),
                admission_receipt=_admission_receipt(_items()),
                protocol=_protocol(),
            ).calibration[:8]
        ),
    )
    artifact = error.failure_artifact
    assert type(artifact) is Aihub538DecisionCalibrationFailureArtifactV2
    serialized = canonical_json_bytes(artifact.to_json_object()).decode("utf-8")
    for item in _items():
        assert item.sample_sha256 not in serialized
        assert item.actor_token_sha256 not in serialized
        assert item.attempt_token_sha256 not in serialized
        assert _sha(f"container-{item.sample_sha256}") not in serialized
    assert '"observed_value"' not in serialized
    assert '"observed_minimum"' not in serialized
    assert '"observed_maximum"' not in serialized
    assert '"observed_mean"' not in serialized
    assert '"observed_quantile"' not in serialized
    for diagnostic in artifact.to_json_object()["angle_qc_diagnostics"]:
        for histogram in diagnostic[
            "calibration_threshold_margin_histograms"
        ]:
            assert set(histogram) == {
                "reason_code",
                "failed_beyond_margin_count",
                "failed_within_margin_count",
                "passed_within_margin_count",
                "passed_beyond_margin_count",
            }


def test_multishard_failure_publisher_is_one_file_atomic_and_strict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error, _calibration = _multishard_stop_failure(
        monkeypatch,
        development_qc_unavailable=True,
    )
    artifact = error.failure_artifact
    assert type(artifact) is Aihub538DecisionCalibrationFailureArtifactV2
    output = tmp_path / "failure-output"
    output.mkdir(mode=0o700)

    path = publish_aihub538_decision_calibration_failure(
        output_dir=output,
        failure_artifact=artifact,
    )

    assert path.name == AIHUB538_MULTI_CALIBRATION_FAILURE_OUTPUT_NAME
    assert tuple(output.iterdir()) == (path,)
    assert stat_mode(path) == 0o400
    assert load_aihub538_decision_calibration_failure_artifact(path) == artifact
    assert not any(
        (output / name).exists()
        for name in AIHUB538_MULTI_CALIBRATION_OUTPUT_NAMES.values()
    )
    alias = output / "forbidden-hardlink.json"
    os.link(path, alias)
    with pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_calibration_failure_strict_reload_failed",
    ):
        load_aihub538_decision_calibration_failure_artifact(path)
    alias.unlink()
    path.chmod(0o600)
    with pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_calibration_failure_strict_reload_failed",
    ):
        load_aihub538_decision_calibration_failure_artifact(path)
    path.chmod(0o400)
    before = path.read_bytes()
    with pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_calibration_failure_output_already_exists",
    ):
        publish_aihub538_decision_calibration_failure(
            output_dir=output,
            failure_artifact=artifact,
        )
    assert path.read_bytes() == before


def test_v1_and_v2_failure_publish_reload_and_cross_conflict_are_compatible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error, _calibration = _multishard_stop_failure(
        monkeypatch,
        development_qc_unavailable=True,
    )
    v2 = error.failure_artifact
    assert type(v2) is Aihub538DecisionCalibrationFailureArtifactV2
    v1 = v2.failure_summary
    assert v1.schema_version == (
        AIHUB538_MULTI_DECISION_CALIBRATION_FAILURE_SCHEMA_VERSION
    )
    assert v2.schema_version == (
        AIHUB538_MULTI_DECISION_CALIBRATION_FAILURE_V2_SCHEMA_VERSION
    )

    v1_output = tmp_path / "v1-output"
    v1_output.mkdir(mode=0o700)
    v1_path = publish_aihub538_decision_calibration_failure(
        output_dir=v1_output,
        failure_artifact=v1,
    )
    assert v1_path.name == AIHUB538_MULTI_CALIBRATION_FAILURE_V1_OUTPUT_NAME
    assert type(load_aihub538_decision_calibration_failure_artifact(v1_path)) is (
        Aihub538DecisionCalibrationFailureArtifact
    )
    with pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_calibration_failure_output_already_exists",
    ):
        publish_aihub538_decision_calibration_failure(
            output_dir=v1_output,
            failure_artifact=v2,
        )

    v2_output = tmp_path / "v2-output"
    v2_output.mkdir(mode=0o700)
    v2_path = publish_aihub538_decision_calibration_failure(
        output_dir=v2_output,
        failure_artifact=v2,
    )
    assert v2_path.name == AIHUB538_MULTI_CALIBRATION_FAILURE_V2_OUTPUT_NAME
    assert type(load_aihub538_decision_calibration_failure_artifact(v2_path)) is (
        Aihub538DecisionCalibrationFailureArtifactV2
    )
    with pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_calibration_failure_output_already_exists",
    ):
        publish_aihub538_decision_calibration_failure(
            output_dir=v2_output,
            failure_artifact=v1,
        )

    multi_result, multi_admission, _items_value = _multi_result()
    workspace = tmp_path / "compat-workspace"
    workspace.mkdir(mode=0o700)
    for output in (v1_output, v2_output):
        with pytest.raises(
            Aihub538DecisionCalibrationError,
            match="aihub538_calibration_success_conflicts_with_failure_output",
        ):
            publish_aihub538_decision_calibration_bundle(
                output_dir=output,
                workspace_root=workspace,
                repository_root=ROOT,
                result=multi_result,
                admission_receipt=multi_admission,
            )


def test_failure_loader_dispatches_exact_filename_schema_and_rejects_duplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error, _calibration = _multishard_stop_failure(
        monkeypatch,
        development_qc_unavailable=True,
    )
    v2 = error.failure_artifact
    assert type(v2) is Aihub538DecisionCalibrationFailureArtifactV2
    v1 = v2.failure_summary

    cases = (
        (
            AIHUB538_MULTI_CALIBRATION_FAILURE_V2_OUTPUT_NAME,
            v1.to_json_object(),
        ),
        (
            AIHUB538_MULTI_CALIBRATION_FAILURE_V1_OUTPUT_NAME,
            v2.to_json_object(),
        ),
        ("unknown_failure.json", v2.to_json_object()),
    )
    for index, (name, document) in enumerate(cases):
        case_root = tmp_path / f"case-{index}"
        case_root.mkdir(mode=0o700)
        path = case_root / name
        path.write_bytes(canonical_json_bytes(document) + b"\n")
        path.chmod(0o400)
        with pytest.raises(
            Aihub538DecisionCalibrationError,
            match="aihub538_calibration_failure_strict_reload_failed",
        ):
            load_aihub538_decision_calibration_failure_artifact(path)

    duplicate_root = tmp_path / "duplicate"
    duplicate_root.mkdir(mode=0o700)
    duplicate_path = (
        duplicate_root / AIHUB538_MULTI_CALIBRATION_FAILURE_V2_OUTPUT_NAME
    )
    duplicate_path.write_text(
        (
            '{"schema_version":"'
            + AIHUB538_MULTI_DECISION_CALIBRATION_FAILURE_V2_SCHEMA_VERSION
            + '","schema_version":"'
            + AIHUB538_MULTI_DECISION_CALIBRATION_FAILURE_V2_SCHEMA_VERSION
            + '"}\n'
        ),
        encoding="utf-8",
    )
    duplicate_path.chmod(0o400)
    with pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_calibration_failure_strict_reload_failed",
    ):
        load_aihub538_decision_calibration_failure_artifact(duplicate_path)


def test_v1_and_v2_failure_strict_reload_in_fresh_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error, _calibration = _multishard_stop_failure(
        monkeypatch,
        development_qc_unavailable=True,
    )
    v2 = error.failure_artifact
    assert type(v2) is Aihub538DecisionCalibrationFailureArtifactV2
    paths: list[Path] = []
    for name, artifact in (("v1", v2.failure_summary), ("v2", v2)):
        output = tmp_path / name
        output.mkdir(mode=0o700)
        paths.append(
            publish_aihub538_decision_calibration_failure(
                output_dir=output,
                failure_artifact=artifact,
            )
        )
    child_code = """
import json
from pathlib import Path
import sys
from voice_coach.visual_feedback.adapters.aihub538_decision_calibration_publish import (
    load_aihub538_decision_calibration_failure_artifact,
)

loaded = [
    load_aihub538_decision_calibration_failure_artifact(Path(value))
    for value in sys.argv[1:]
]
print(
    json.dumps(
        [
            [type(item).__name__, item.schema_version, item.artifact_sha256]
            for item in loaded
        ]
    )
)
"""
    completed = subprocess.run(
        [sys.executable, "-c", child_code, *(str(path) for path in paths)],
        cwd=ROOT,
        env={
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": str(ROOT / "src"),
        },
        capture_output=True,
        check=False,
        timeout=30.0,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report == [
        [
            "Aihub538DecisionCalibrationFailureArtifact",
            AIHUB538_MULTI_DECISION_CALIBRATION_FAILURE_SCHEMA_VERSION,
            v2.failure_summary.artifact_sha256,
        ],
        [
            "Aihub538DecisionCalibrationFailureArtifactV2",
            AIHUB538_MULTI_DECISION_CALIBRATION_FAILURE_V2_SCHEMA_VERSION,
            v2.artifact_sha256,
        ],
    ]


def test_multishard_failure_publish_rolls_back_and_rejects_success_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error, _calibration = _multishard_stop_failure(
        monkeypatch,
        development_qc_unavailable=True,
    )
    artifact = error.failure_artifact
    assert type(artifact) is Aihub538DecisionCalibrationFailureArtifactV2
    output = tmp_path / "rollback-output"
    output.mkdir(mode=0o700)
    original_write = publisher_adapter.os.write

    def fail_write(descriptor: int, payload: bytes) -> int:
        del descriptor, payload
        raise OSError("injected write failure")

    monkeypatch.setattr(publisher_adapter.os, "write", fail_write)
    with pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_calibration_failure_exclusive_publish_failed",
    ):
        publish_aihub538_decision_calibration_failure(
            output_dir=output,
            failure_artifact=artifact,
        )
    assert tuple(output.iterdir()) == ()
    monkeypatch.setattr(publisher_adapter.os, "write", original_write)

    success_path = output / AIHUB538_MULTI_CALIBRATION_OUTPUT_NAMES["video_qc"]
    success_path.write_text("{}", encoding="utf-8")
    with pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_calibration_failure_conflicts_with_success_output",
    ):
        publish_aihub538_decision_calibration_failure(
            output_dir=output,
            failure_artifact=artifact,
        )
    assert tuple(output.iterdir()) == (success_path,)


def test_multishard_success_and_failure_publishers_share_cross_process_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    multi_result, multi_admission, _items_value = _multi_result()
    error, _calibration = _multishard_stop_failure(
        monkeypatch,
        development_qc_unavailable=True,
    )
    artifact = error.failure_artifact
    assert type(artifact) is Aihub538DecisionCalibrationFailureArtifactV2
    output = tmp_path / "race-output"
    output.mkdir(mode=0o700)
    workspace = tmp_path / "race-workspace"
    workspace.mkdir(mode=0o700)
    sentinel = output / "unrelated-existing-file.txt"
    sentinel.write_bytes(b"preserve-me")
    before = sentinel.read_bytes()

    artifact_input = tmp_path / "failure-artifact-input.json"
    artifact_input.write_bytes(
        canonical_json_bytes(artifact.to_json_object()) + b"\n"
    )
    artifact_input.chmod(0o400)
    child_code = """
import json
from pathlib import Path
import sys

from voice_coach.visual_feedback.adapters import (
    aihub538_decision_calibration_publish as publisher,
)
from voice_coach.visual_feedback.models.aihub538_decision_calibration import (
    Aihub538DecisionCalibrationFailureArtifactV2,
)

output = Path(sys.argv[1])
artifact = Aihub538DecisionCalibrationFailureArtifactV2.from_json_object(
    json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
)
original = publisher._write_pending_failure_at

def pause_after_lock(directory_descriptor, pending_name, payload):
    print("READY", flush=True)
    if sys.stdin.buffer.read(1) != b"G":
        raise OSError("publication race test release failed")
    return original(directory_descriptor, pending_name, payload)

publisher._write_pending_failure_at = pause_after_lock
publisher.publish_aihub538_decision_calibration_failure(
    output_dir=output,
    failure_artifact=artifact,
)
"""
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            child_code,
            str(output),
            str(artifact_input),
        ],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert child.stdin is not None
    assert child.stdout is not None
    child_return_code: int | None = None
    try:
        readable, _writable, _exceptional = select.select(
            [child.stdout],
            [],
            [],
            10.0,
        )
        assert readable and child.stdout.readline() == b"READY\n"

        with pytest.raises(
            Aihub538DecisionCalibrationError,
            match="aihub538_calibration_failure_output_lock_unavailable",
        ):
            publish_aihub538_decision_calibration_failure(
                output_dir=output,
                failure_artifact=artifact,
            )
        with pytest.raises(
            Aihub538DecisionCalibrationError,
            match="aihub538_calibration_output_lock_unavailable",
        ):
            publish_aihub538_decision_calibration_bundle(
                output_dir=output,
                workspace_root=workspace,
                repository_root=ROOT,
                result=multi_result,
                admission_receipt=multi_admission,
            )
        assert sentinel.read_bytes() == before
        assert tuple(workspace.iterdir()) == ()
    finally:
        try:
            child.stdin.write(b"G")
            child.stdin.flush()
            child.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            child_return_code = child.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            child.kill()
            child_return_code = child.wait(timeout=10.0)
    remaining_stdout = child.stdout.read()
    child_stderr = child.stderr.read() if child.stderr is not None else b""
    assert child_return_code == 0, (remaining_stdout, child_stderr)
    assert sentinel.read_bytes() == before
    assert (
        output / AIHUB538_MULTI_CALIBRATION_FAILURE_OUTPUT_NAME
    ).is_file()
    assert not any(
        (output / name).exists()
        for name in AIHUB538_MULTI_CALIBRATION_OUTPUT_NAMES.values()
    )
    with pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_calibration_success_conflicts_with_failure_output",
    ):
        publish_aihub538_decision_calibration_bundle(
            output_dir=output,
            workspace_root=workspace,
            repository_root=ROOT,
            result=multi_result,
            admission_receipt=multi_admission,
        )


def test_publishers_reject_group_writable_output_directory_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    multi_result, multi_admission, _items_value = _multi_result()
    error, _calibration = _multishard_stop_failure(
        monkeypatch,
        development_qc_unavailable=True,
    )
    artifact = error.failure_artifact
    assert type(artifact) is Aihub538DecisionCalibrationFailureArtifactV2
    output = tmp_path / "unsafe-output"
    output.mkdir(mode=0o770)
    output.chmod(0o770)
    sentinel = output / "preserve.txt"
    sentinel.write_bytes(b"unchanged")
    workspace = tmp_path / "unsafe-workspace"
    workspace.mkdir(mode=0o700)

    with pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_calibration_failure_output_directory_unsafe",
    ):
        publish_aihub538_decision_calibration_failure(
            output_dir=output,
            failure_artifact=artifact,
        )
    with pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_calibration_output_directory_unsafe",
    ):
        publish_aihub538_decision_calibration_bundle(
            output_dir=output,
            workspace_root=workspace,
            repository_root=ROOT,
            result=multi_result,
            admission_receipt=multi_admission,
        )
    assert tuple(output.iterdir()) == (sentinel,)
    assert sentinel.read_bytes() == b"unchanged"
    assert tuple(workspace.iterdir()) == ()


def test_success_publish_uses_locked_dirfd_and_rolls_back_on_path_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    multi_result, multi_admission, _items_value = _multi_result()
    output = tmp_path / "swap-output"
    output.mkdir(mode=0o700)
    displaced = tmp_path / "displaced-output"
    workspace = tmp_path / "swap-workspace"
    workspace.mkdir(mode=0o700)
    original_write = publisher_adapter._write_exclusive_document_at
    swapped = False

    def swap_after_first_write(
        directory_descriptor: int,
        name: str,
        document: object,
    ) -> tuple[int, int]:
        nonlocal swapped
        identity = original_write(directory_descriptor, name, document)
        if not swapped:
            output.rename(displaced)
            output.mkdir(mode=0o700)
            swapped = True
        return identity

    monkeypatch.setattr(
        publisher_adapter,
        "_write_exclusive_document_at",
        swap_after_first_write,
    )
    with pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_calibration_output_directory_changed",
    ):
        publish_aihub538_decision_calibration_bundle(
            output_dir=output,
            workspace_root=workspace,
            repository_root=ROOT,
            result=multi_result,
            admission_receipt=multi_admission,
        )

    assert swapped is True
    assert tuple(output.iterdir()) == ()
    assert tuple(displaced.iterdir()) == ()
    assert tuple(workspace.iterdir()) == ()


def test_multishard_stop_failure_cli_publishes_and_reports_safe_basename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    error, _calibration = _multishard_stop_failure(
        monkeypatch,
        development_qc_unavailable=True,
    )
    module = _load_calibration_cli()
    output = tmp_path / "cli-output"
    output.mkdir(mode=0o700)
    workspace = tmp_path / "cli-workspace"
    workspace.mkdir(mode=0o700)
    identity = tmp_path / "identity"
    identity.mkdir(mode=0o700)
    monkeypatch.setattr(
        module,
        "load_aihub538_identity_key",
        lambda **_ignored: (SimpleNamespace(key=b"fixture-key"), None),
    )
    monkeypatch.setattr(
        module,
        "_model_sha256",
        lambda _path: module.OFFICIAL_FACE_LANDMARKER_SHA256,
    )
    monkeypatch.setattr(module, "_multi_mode", lambda _arguments: True)

    def fail_run(**_ignored: object) -> object:
        raise error

    monkeypatch.setattr(module, "_run_multi", fail_run)

    assert module.main(
        [
            "--identity-state-dir",
            str(identity),
            "--restricted-workspace-root",
            str(workspace),
            "--output-dir",
            str(output),
            "--dataset-access-permission-confirmed",
        ]
    ) == 2
    report = json.loads(capsys.readouterr().err)
    assert report["reason_code"] == error.reason_code
    assert report["failure_artifact_sha256"] == (
        error.failure_artifact.artifact_sha256
    )
    assert report["failure_artifact_basename"] == (
        AIHUB538_MULTI_CALIBRATION_FAILURE_OUTPUT_NAME
    )
    assert "/" not in report["failure_artifact_basename"]
    assert report["locked_partition_accessed"] is False
    assert report["promotion_authorized"] is False
    assert report["raw_identifiers_serialized"] is False
    assert {path.name for path in output.iterdir()} == {
        AIHUB538_MULTI_CALIBRATION_FAILURE_OUTPUT_NAME
    }


def test_measurement_item_mismatch_fails_closed() -> None:
    items = _items()
    admission = _admission_receipt(items)
    qc, sync = _measurement_policies()
    service = Aihub538DecisionCalibrationService(
        measurement_port=_MeasurementPort(
            qc_policy_sha256=qc.policy_sha256,
            sync_policy_sha256=sync.policy_sha256,
            return_wrong_item=True,
        ),
        protocol=_protocol(),
    )

    with pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_measurement_item_binding_mismatch",
    ):
        service.calibrate(
            items=items,
            admission_receipt=admission,
            video_qc_measurement_policy=qc,
            av_sync_measurement_policy=sync,
        )


def test_fresh_process_reload_is_strict_and_requires_mode_0400(
    tmp_path: Path,
) -> None:
    result, _admission, _items_value = _successful_result()
    documents = (
        result.video_qc_decision_policy.to_json_object(),
        result.av_sync_decision_policy.to_json_object(),
        result.calibration_evidence.to_json_object(),
    )
    paths: list[Path] = []
    for index, document in enumerate(documents):
        path = tmp_path / f"input-{index}.json"
        path.write_bytes(canonical_json_bytes(document) + b"\n")
        path.chmod(0o400)
        paths.append(path)

    receipt = fresh_process_reload_aihub538_calibration(
        video_qc_policy_path=paths[0],
        av_sync_policy_path=paths[1],
        calibration_evidence_path=paths[2],
        repository_root=ROOT,
    )
    assert receipt.calibration_evidence_receipt_sha256 == (
        result.calibration_evidence.receipt_sha256
    )

    paths[2].chmod(0o600)
    with pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_calibration_fresh_reload_failed",
    ):
        fresh_process_reload_aihub538_calibration(
            video_qc_policy_path=paths[0],
            av_sync_policy_path=paths[1],
            calibration_evidence_path=paths[2],
            repository_root=ROOT,
        )


def test_publish_is_exclusive_fsync_frozen_and_writes_final_evidence_last(
    tmp_path: Path,
) -> None:
    result, admission, _items_value = _successful_result()
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)

    evidence, paths = publish_aihub538_decision_calibration_bundle(
        output_dir=output,
        workspace_root=workspace,
        repository_root=ROOT,
        result=result,
        admission_receipt=admission,
    )

    assert len(paths) == 5
    assert all(stat_mode(path) == 0o400 for path in paths)
    persisted = RuntimeDecisionPolicyEvidence.from_json_object(
        json.loads(
            (
                output
                / "aihub538_ts174_tl174_decision_policy_evidence_v1.json"
            ).read_text()
        )
    )
    assert persisted == evidence
    assert persisted.schema_version == (
        RUNTIME_DECISION_POLICY_EVIDENCE_SCHEMA_VERSION
    )
    assert persisted.source_partitions == ("TS174", "TL174")
    assert persisted.fresh_process_reload_passed is True
    before = {path.name: path.read_bytes() for path in paths}

    with pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_calibration_output_already_exists",
    ):
        publish_aihub538_decision_calibration_bundle(
            output_dir=output,
            workspace_root=workspace,
            repository_root=ROOT,
            result=result,
            admission_receipt=admission,
        )
    assert {path.name: path.read_bytes() for path in paths} == before


def test_legacy_and_multishard_bundles_publish_to_disjoint_names(
    tmp_path: Path,
) -> None:
    legacy_result, legacy_admission, _items_value = _successful_result()
    multi_result, multi_admission, _items_value = _multi_result()
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)

    legacy_policy_evidence, legacy_paths = publish_aihub538_decision_calibration_bundle(
        output_dir=output,
        workspace_root=workspace,
        repository_root=ROOT,
        result=legacy_result,
        admission_receipt=legacy_admission,
    )
    _multi_evidence, multi_paths = publish_aihub538_decision_calibration_bundle(
        output_dir=output,
        workspace_root=workspace,
        repository_root=ROOT,
        result=multi_result,
        admission_receipt=multi_admission,
    )

    legacy_names = {path.name for path in legacy_paths}
    multi_names = {path.name for path in multi_paths}
    assert legacy_names == set(AIHUB538_CALIBRATION_OUTPUT_NAMES.values())
    assert multi_names == set(AIHUB538_MULTI_CALIBRATION_OUTPUT_NAMES.values())
    assert legacy_names.isdisjoint(multi_names)
    assert len(tuple(output.iterdir())) == 10
    legacy_policy_document = legacy_policy_evidence.to_json_object()
    assert "eligible_item_count" not in legacy_policy_document
    assert "metadata_excluded_item_count" not in legacy_policy_document
    assert "development_selected_count" not in legacy_policy_document
    assert "calibration_selected_count" not in legacy_policy_document
    persisted_multi = Aihub538DecisionCalibrationEvidence.from_json_object(
        json.loads(
            (
                output / AIHUB538_MULTI_CALIBRATION_OUTPUT_NAMES["calibration"]
            ).read_text()
        )
    )
    assert persisted_multi == multi_result.calibration_evidence
    persisted_multi_policy = RuntimeDecisionPolicyEvidence.from_json_object(
        json.loads(
            (
                output / AIHUB538_MULTI_CALIBRATION_OUTPUT_NAMES["evidence"]
            ).read_text()
        )
    )
    assert persisted_multi_policy.schema_version == (
        MULTISHARD_RUNTIME_DECISION_POLICY_EVIDENCE_SCHEMA_VERSION
    )
    assert persisted_multi_policy.source_partitions == (
        AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS
    )
    assert persisted_multi_policy.admission_receipt_sha256 == (
        multi_admission.receipt_sha256
    )
    assert persisted_multi_policy.evaluation_protocol_sha256 == (
        MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL.protocol_sha256
    )
    calibration_evidence = multi_result.calibration_evidence
    assert persisted_multi_policy.admitted_item_count == (
        calibration_evidence.admitted_item_count
    )
    assert persisted_multi_policy.eligible_item_count == (
        calibration_evidence.eligible_item_count
    )
    assert persisted_multi_policy.metadata_excluded_item_count == (
        calibration_evidence.metadata_excluded_item_count
    )
    assert persisted_multi_policy.development_selected_count == (
        calibration_evidence.development_selected_count
    )
    assert persisted_multi_policy.calibration_selected_count == (
        calibration_evidence.calibration_selected_count
    )
    assert persisted_multi_policy.evaluated_item_count == (
        calibration_evidence.calibration_selected_count
    )

    wrong_schema_document = persisted_multi_policy.to_json_object()
    wrong_schema_document["schema_version"] = (
        RUNTIME_DECISION_POLICY_EVIDENCE_SCHEMA_VERSION
    )
    wrong_schema_document["receipt_sha256"] = sha256_document(
        {
            key: value
            for key, value in wrong_schema_document.items()
            if key != "receipt_sha256"
        }
    )
    with pytest.raises(
        ValueError,
        match="runtime decision policy evidence schema boundary differs",
    ):
        RuntimeDecisionPolicyEvidence.from_json_object(wrong_schema_document)

    for field, value in (
        ("admitted_item_count", persisted_multi_policy.admitted_item_count - 1),
        ("evaluated_item_count", persisted_multi_policy.evaluated_item_count - 1),
        ("eligible_item_count", persisted_multi_policy.eligible_item_count - 1),
        (
            "development_selected_count",
            persisted_multi_policy.eligible_item_count,
        ),
    ):
        wrong_count_document = persisted_multi_policy.to_json_object()
        wrong_count_document[field] = value
        wrong_count_document["receipt_sha256"] = sha256_document(
            {
                key: item
                for key, item in wrong_count_document.items()
                if key != "receipt_sha256"
            }
        )
        with pytest.raises(ValueError, match="multishard counts differ"):
            RuntimeDecisionPolicyEvidence.from_json_object(wrong_count_document)


def test_publish_strictly_dispatches_receipt_scope_and_binds_protocol(
    tmp_path: Path,
) -> None:
    legacy_result, legacy_admission, _items_value = _successful_result()
    multi_result, multi_admission, _items_value = _multi_result()
    wrong_protocol_result = replace(
        multi_result,
        calibration_evidence=_reissue_evidence(
            multi_result.calibration_evidence,
            evaluation_protocol_sha256=_sha("wrong-multi-protocol"),
        ),
    )
    wrong_legacy_protocol_result = replace(
        legacy_result,
        calibration_evidence=_reissue_evidence(
            legacy_result.calibration_evidence,
            evaluation_protocol_sha256=_sha("wrong-legacy-protocol"),
        ),
    )
    changed_admission_draft = replace(
        multi_admission,
        admission_manifest_sha256=_sha("different-multi-manifest"),
        receipt_sha256="0" * 64,
    )
    changed_admission = replace(
        changed_admission_draft,
        receipt_sha256=sha256_document(
            changed_admission_draft.unsigned_json_object()
        ),
    )
    changed_admission._validate()
    alternate_capacity_sha256 = _sha("self-digested-feasible-v5-not-released")
    alternate_pin_draft = replace(
        multi_admission,
        shard_lineage=tuple(
            replace(
                lineage,
                capacity_audit_receipt_sha256=(
                    None if index == 0 else alternate_capacity_sha256
                ),
            )
            for index, lineage in enumerate(multi_admission.shard_lineage)
        ),
        capacity_audit_receipt_sha256=alternate_capacity_sha256,
        receipt_sha256="0" * 64,
    )
    alternate_pin_admission = replace(
        alternate_pin_draft,
        receipt_sha256=sha256_document(
            alternate_pin_draft.unsigned_json_object()
        ),
    )
    alternate_pin_admission._validate()
    cases = (
        (multi_result, legacy_admission),
        (legacy_result, multi_admission),
        (wrong_protocol_result, multi_admission),
        (wrong_legacy_protocol_result, legacy_admission),
        (multi_result, changed_admission),
        (multi_result, alternate_pin_admission),
    )

    for index, (result, admission_receipt) in enumerate(cases):
        output = tmp_path / f"output-{index}"
        output.mkdir(mode=0o700)
        workspace = tmp_path / f"workspace-{index}"
        workspace.mkdir(mode=0o700)
        with pytest.raises(
            Aihub538DecisionCalibrationError,
            match="aihub538_calibration_publication_scope_mismatch",
        ):
            publish_aihub538_decision_calibration_bundle(
                output_dir=output,
                workspace_root=workspace,
                repository_root=ROOT,
                result=result,
                admission_receipt=admission_receipt,
            )
        assert tuple(output.iterdir()) == ()
        assert tuple(workspace.iterdir()) == ()


@pytest.mark.parametrize(
    "shard_spec",
    (AIHUB538_TS175_TL175_SHARD, AIHUB538_TS176_TL176_SHARD),
)
def test_publish_rejects_individual_ladder_shard_laundering_as_legacy(
    tmp_path: Path,
    shard_spec: Aihub538NonlockedShardSpec,
) -> None:
    result, legacy_admission, _items_value = _successful_result()
    ladder_draft = replace(
        legacy_admission,
        shard_id=shard_spec.shard_id,
        dataset_partition=shard_spec.dataset_partition,
        source_role_component=shard_spec.source_role_component,
        label_role_component=shard_spec.label_role_component,
        ordered_file_keys=shard_spec.ordered_file_keys,
        admission_policy_sha256=aihub538_shard_admission_policy_sha256(
            shard_spec
        ),
        sample_selection_rule=shard_spec.sample_selection_rule,
        receipt_sha256="0" * 64,
    )
    ladder_admission = replace(
        ladder_draft,
        receipt_sha256=sha256_document(ladder_draft._unsigned_json()),
    )
    ladder_admission._validate()
    laundered_result = replace(
        result,
        calibration_evidence=_reissue_evidence(
            result.calibration_evidence,
            admission_receipt_sha256=ladder_admission.receipt_sha256,
        ),
    )
    output = tmp_path / shard_spec.shard_id
    output.mkdir(mode=0o700)
    workspace = tmp_path / f"{shard_spec.shard_id}-workspace"
    workspace.mkdir(mode=0o700)

    with pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_calibration_publication_scope_mismatch",
    ):
        publish_aihub538_decision_calibration_bundle(
            output_dir=output,
            workspace_root=workspace,
            repository_root=ROOT,
            result=laundered_result,
            admission_receipt=ladder_admission,
        )
    assert tuple(output.iterdir()) == ()
    assert tuple(workspace.iterdir()) == ()


def test_fresh_reload_failure_publishes_no_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, admission, _items_value = _successful_result()
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)

    def reject_reload(**_arguments: object) -> None:
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_fresh_reload_failed"
        )

    monkeypatch.setattr(
        publisher_adapter,
        "fresh_process_reload_aihub538_calibration",
        reject_reload,
    )
    with pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_calibration_fresh_reload_failed",
    ):
        publish_aihub538_decision_calibration_bundle(
            output_dir=output,
            workspace_root=workspace,
            repository_root=ROOT,
            result=result,
            admission_receipt=admission,
        )

    assert tuple(output.iterdir()) == ()
    assert tuple(workspace.iterdir()) == ()


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777
