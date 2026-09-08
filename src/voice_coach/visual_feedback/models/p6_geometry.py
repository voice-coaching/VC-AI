"""Immutable aggregate contracts for real AI Hub 68 P6 geometry calibration."""

from __future__ import annotations

from dataclasses import dataclass
import math

from ..errors import VisualFeedbackContractError
from .integrity import (
    require_exact_keys,
    require_mapping,
    require_safe_identifier,
    require_sha256,
    sha256_document,
)
from .p6_geometry_schema import AIHUB68_KEYPOINT_NAMES, MEDIAPIPE_LIP_CANDIDATE_INDICES


P6_GEOMETRY_ARTIFACT_SCHEMA_VERSION = "voice_coach.p6_geometry_calibration.v2"
P6_GEOMETRY_RELOAD_SCHEMA_VERSION = "voice_coach.p6_geometry_reload_receipt.v2"
P6_GEOMETRY_BUNDLE_SCHEMA_VERSION = "voice_coach.p6_geometry_evidence_bundle.v2"


@dataclass(frozen=True, slots=True)
class P6GeometryThresholds:
    """Thresholds frozen before any MediaPipe metric access."""

    minimum_calibration_actor_count: int = 5
    minimum_evaluated_calibration_frame_count: int = 200
    minimum_overall_coverage: float = 0.95
    minimum_camera_coverage: float = 0.90
    maximum_overall_nle: float = 0.10
    maximum_camera_nle: float = 0.15
    required_camera_count: int = 5
    maximum_repeatability_normalized_cue_rmse: float = 0.15

    def __post_init__(self) -> None:
        for name in (
            "minimum_calibration_actor_count",
            "minimum_evaluated_calibration_frame_count",
            "required_camera_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise VisualFeedbackContractError(f"{name} must be positive")
        for name in (
            "minimum_overall_coverage",
            "minimum_camera_coverage",
            "maximum_overall_nle",
            "maximum_camera_nle",
            "maximum_repeatability_normalized_cue_rmse",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0 <= float(value) <= 1
            ):
                raise VisualFeedbackContractError(f"{name} must be in [0,1]")

    def to_json_object(self) -> dict[str, object]:
        return {
            "minimum_calibration_actor_count": self.minimum_calibration_actor_count,
            "minimum_evaluated_calibration_frame_count": (
                self.minimum_evaluated_calibration_frame_count
            ),
            "minimum_overall_coverage": self.minimum_overall_coverage,
            "minimum_camera_coverage": self.minimum_camera_coverage,
            "maximum_overall_nle": self.maximum_overall_nle,
            "maximum_camera_nle": self.maximum_camera_nle,
            "required_camera_count": self.required_camera_count,
            "maximum_repeatability_normalized_cue_rmse": (
                self.maximum_repeatability_normalized_cue_rmse
            ),
        }

    @property
    def threshold_protocol_sha256(self) -> str:
        return sha256_document(
            {
                "protocol_revision": "p6_geometry_threshold_protocol_v1",
                "nle_normalizer": "gold_outer_corner_euclidean_distance",
                "repeatability_metric": (
                    "within_actor_word_frame_cross_camera_centered_"
                    "aperture_residual_rmse"
                ),
                "thresholds": self.to_json_object(),
            }
        )


@dataclass(frozen=True, slots=True)
class P6GeometrySliceMetric:
    """Aggregate metric for one non-identifying demographic/capture slice."""

    dimension: str
    label: str
    sample_count: int
    detected_count: int
    coverage_fraction: float
    normalized_landmark_error: float | None

    def __post_init__(self) -> None:
        if self.dimension not in {"camera", "sex", "age"}:
            raise VisualFeedbackContractError("P6 slice dimension differs")
        require_safe_identifier(self.label, "p6_slice.label")
        if (
            isinstance(self.sample_count, bool)
            or not isinstance(self.sample_count, int)
            or self.sample_count <= 0
            or isinstance(self.detected_count, bool)
            or not isinstance(self.detected_count, int)
            or not 0 <= self.detected_count <= self.sample_count
        ):
            raise VisualFeedbackContractError("P6 slice counts are invalid")
        if (
            isinstance(self.coverage_fraction, bool)
            or not isinstance(self.coverage_fraction, (int, float))
            or not math.isfinite(float(self.coverage_fraction))
            or not 0 <= float(self.coverage_fraction) <= 1
        ):
            raise VisualFeedbackContractError("P6 slice coverage is invalid")
        if self.normalized_landmark_error is not None and (
            isinstance(self.normalized_landmark_error, bool)
            or not isinstance(self.normalized_landmark_error, (int, float))
            or not math.isfinite(float(self.normalized_landmark_error))
            or self.normalized_landmark_error < 0
        ):
            raise VisualFeedbackContractError("P6 slice NLE is invalid")

    def to_json_object(self) -> dict[str, object]:
        return {
            "dimension": self.dimension,
            "label": self.label,
            "sample_count": self.sample_count,
            "detected_count": self.detected_count,
            "coverage_fraction": self.coverage_fraction,
            "normalized_landmark_error": self.normalized_landmark_error,
        }


@dataclass(frozen=True, slots=True)
class P6GeometryMetricSummary:
    """Calibration-only aggregate; it contains no landmarks or identifiers."""

    development_sample_count: int
    development_detected_count: int
    development_coverage_fraction: float
    development_normalized_landmark_error: float
    calibration_sample_count: int
    calibration_detected_count: int
    calibration_coverage_fraction: float
    calibration_normalized_landmark_error: float
    calibration_nle_p95: float
    repeatability_group_count: int
    repeatability_normalized_cue_rmse: float
    slices: tuple[P6GeometrySliceMetric, ...]

    def __post_init__(self) -> None:
        for total_name, detected_name in (
            ("development_sample_count", "development_detected_count"),
            ("calibration_sample_count", "calibration_detected_count"),
        ):
            total = getattr(self, total_name)
            detected = getattr(self, detected_name)
            if (
                isinstance(total, bool)
                or not isinstance(total, int)
                or total <= 0
                or isinstance(detected, bool)
                or not isinstance(detected, int)
                or not 0 <= detected <= total
            ):
                raise VisualFeedbackContractError("P6 metric counts are invalid")
        if (
            isinstance(self.repeatability_group_count, bool)
            or not isinstance(self.repeatability_group_count, int)
            or self.repeatability_group_count <= 0
        ):
            raise VisualFeedbackContractError("P6 repeatability groups are invalid")
        for name in (
            "development_coverage_fraction",
            "calibration_coverage_fraction",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0 <= float(value) <= 1
            ):
                raise VisualFeedbackContractError("P6 metric coverage is invalid")
        for name in (
            "development_normalized_landmark_error",
            "calibration_normalized_landmark_error",
            "calibration_nle_p95",
            "repeatability_normalized_cue_rmse",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value < 0
            ):
                raise VisualFeedbackContractError("P6 metric value is invalid")
        slice_keys = {(item.dimension, item.label) for item in self.slices}
        if len(slice_keys) != len(self.slices):
            raise VisualFeedbackContractError("P6 metric slices are duplicated")

    def to_json_object(self) -> dict[str, object]:
        return {
            "development_sample_count": self.development_sample_count,
            "development_detected_count": self.development_detected_count,
            "development_coverage_fraction": self.development_coverage_fraction,
            "development_normalized_landmark_error": (
                self.development_normalized_landmark_error
            ),
            "calibration_sample_count": self.calibration_sample_count,
            "calibration_detected_count": self.calibration_detected_count,
            "calibration_coverage_fraction": self.calibration_coverage_fraction,
            "calibration_normalized_landmark_error": (
                self.calibration_normalized_landmark_error
            ),
            "calibration_nle_p95": self.calibration_nle_p95,
            "repeatability_group_count": self.repeatability_group_count,
            "repeatability_normalized_cue_rmse": (
                self.repeatability_normalized_cue_rmse
            ),
            "slices": [item.to_json_object() for item in self.slices],
        }


@dataclass(frozen=True, slots=True)
class P6GeometryArtifact:
    """Frozen 24-point mapping and evaluated aggregate reliability evidence."""

    source_revision: str
    dataset_manifest_sha256: str
    population_id_sha256: str
    label_archive_sha256: str
    image_archive_sha256: str
    development_partition_sha256: str
    calibration_partition_sha256: str
    development_actor_count: int
    calibration_actor_count: int
    actor_overlap_count: int
    attempt_overlap_count: int
    tracker_revision: str
    tracker_sha256: str
    model_sha256: str
    admission_adapter_sha256: str
    calibration_service_sha256: str
    mapping_mediapipe_indices: tuple[int, ...]
    mapping_sha256: str
    metrics: P6GeometryMetricSummary
    thresholds: P6GeometryThresholds
    threshold_protocol_sha256: str
    thresholds_frozen_before_metric_access: bool
    split_frozen_before_metric_access: bool
    gate_status: str
    reason_codes: tuple[str, ...]
    parity_probe_sha256: str
    artifact_sha256: str
    artifact_id: str = "aihub68_mediapipe_24point_geometry_v2"
    revision: str = "aihub68_validation_lip_interjection_v2"
    source_dataset_id: str = "aihub_68"
    rights_scope: str = "project_user_confirmed_derived_geometry_only"
    schema_version: str = P6_GEOMETRY_ARTIFACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P6_GEOMETRY_ARTIFACT_SCHEMA_VERSION:
            raise VisualFeedbackContractError("P6 geometry artifact schema differs")
        if self.source_dataset_id != "aihub_68":
            raise VisualFeedbackContractError("P6 geometry source differs")
        for name in (
            "artifact_id",
            "revision",
            "source_revision",
            "source_dataset_id",
            "rights_scope",
            "tracker_revision",
        ):
            require_safe_identifier(getattr(self, name), name)
        for name in (
            "development_actor_count",
            "calibration_actor_count",
            "actor_overlap_count",
            "attempt_overlap_count",
        ):
            value = getattr(self, name)
            minimum = 0 if "overlap" in name else 1
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < minimum
            ):
                raise VisualFeedbackContractError("P6 split count is invalid")
        for name in (
            "dataset_manifest_sha256",
            "population_id_sha256",
            "label_archive_sha256",
            "image_archive_sha256",
            "development_partition_sha256",
            "calibration_partition_sha256",
            "tracker_sha256",
            "model_sha256",
            "admission_adapter_sha256",
            "calibration_service_sha256",
            "mapping_sha256",
            "threshold_protocol_sha256",
            "parity_probe_sha256",
            "artifact_sha256",
        ):
            require_sha256(getattr(self, name), name)
        if len(self.mapping_mediapipe_indices) != 24 or len(
            set(self.mapping_mediapipe_indices)
        ) != 24 or not set(self.mapping_mediapipe_indices).issubset(
            MEDIAPIPE_LIP_CANDIDATE_INDICES
        ):
            raise VisualFeedbackContractError("P6 geometry mapping is invalid")
        if self.mapping_sha256 != sha256_document(self.mapping_json_object()):
            raise VisualFeedbackContractError("P6 geometry mapping digest differs")
        if self.threshold_protocol_sha256 != self.thresholds.threshold_protocol_sha256:
            raise VisualFeedbackContractError("P6 geometry threshold digest differs")
        if self.gate_status != "passed" or self.reason_codes:
            raise VisualFeedbackContractError("only passing P6 artifacts may be frozen")
        if not (
            self.thresholds_frozen_before_metric_access
            and self.split_frozen_before_metric_access
        ):
            raise VisualFeedbackContractError("P6 pre-metric freeze evidence is missing")
        if self.actor_overlap_count != 0 or self.attempt_overlap_count != 0:
            raise VisualFeedbackContractError("P6 split overlap is not zero")
        if self.parity_probe_sha256 != p6_mapping_parity_probe_sha256(
            self.mapping_mediapipe_indices
        ):
            raise VisualFeedbackContractError("P6 parity probe differs")
        if self.artifact_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("P6 geometry artifact digest differs")

    @classmethod
    def issue(cls, **values: object) -> P6GeometryArtifact:
        """Issue only after the service has enforced all frozen thresholds."""

        temporary = object.__new__(cls)
        defaults = {
            "artifact_id": "aihub68_mediapipe_24point_geometry_v2",
            "revision": "aihub68_validation_lip_interjection_v2",
            "source_dataset_id": "aihub_68",
            "rights_scope": "project_user_confirmed_derived_geometry_only",
            "schema_version": P6_GEOMETRY_ARTIFACT_SCHEMA_VERSION,
        }
        for name, value in {**defaults, **values}.items():
            object.__setattr__(temporary, name, value)
        digest = sha256_document(temporary.unsigned_json_object())
        return cls(**{**values, "artifact_sha256": digest})  # type: ignore[arg-type]

    def mapping_json_object(self) -> dict[str, object]:
        return {
            "gold_keypoint_names": list(AIHUB68_KEYPOINT_NAMES),
            "mediapipe_indices": list(self.mapping_mediapipe_indices),
            "candidate_set": list(MEDIAPIPE_LIP_CANDIDATE_INDICES),
            "derivation": "development_only_rectangular_hungarian_mean_nle_v1",
        }

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "artifact_id": self.artifact_id,
            "revision": self.revision,
            "source_dataset_id": self.source_dataset_id,
            "source_revision": self.source_revision,
            "rights_scope": self.rights_scope,
            "dataset_manifest_sha256": self.dataset_manifest_sha256,
            "population_id_sha256": self.population_id_sha256,
            "label_archive_sha256": self.label_archive_sha256,
            "image_archive_sha256": self.image_archive_sha256,
            "split": {
                "development_partition_sha256": self.development_partition_sha256,
                "calibration_partition_sha256": self.calibration_partition_sha256,
                "development_actor_count": self.development_actor_count,
                "calibration_actor_count": self.calibration_actor_count,
                "actor_overlap_count": self.actor_overlap_count,
                "attempt_overlap_count": self.attempt_overlap_count,
                "assignment_unit": "actor_id",
                "frozen_before_metric_access": self.split_frozen_before_metric_access,
            },
            "pipeline_identity": {
                "tracker_revision": self.tracker_revision,
                "tracker_sha256": self.tracker_sha256,
                "model_sha256": self.model_sha256,
                "admission_adapter_sha256": self.admission_adapter_sha256,
                "calibration_service_sha256": self.calibration_service_sha256,
            },
            "mapping": self.mapping_json_object(),
            "mapping_sha256": self.mapping_sha256,
            "metrics": self.metrics.to_json_object(),
            "thresholds": self.thresholds.to_json_object(),
            "threshold_protocol_sha256": self.threshold_protocol_sha256,
            "thresholds_frozen_before_metric_access": (
                self.thresholds_frozen_before_metric_access
            ),
            "gate_status": self.gate_status,
            "reason_codes": list(self.reason_codes),
            "parity_probe_sha256": self.parity_probe_sha256,
            "privacy_contract": "aggregate_only_no_paths_ids_frames_or_landmark_arrays",
            "contains_pronunciation_truth": False,
            "contains_action_truth": False,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "artifact_sha256": self.artifact_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> P6GeometryArtifact:
        """Strictly reload the published JSON document in a fresh process."""

        document = require_mapping(value, "p6_geometry_artifact")
        require_exact_keys(
            document,
            {
                "schema_version",
                "artifact_id",
                "revision",
                "source_dataset_id",
                "source_revision",
                "rights_scope",
                "dataset_manifest_sha256",
                "population_id_sha256",
                "label_archive_sha256",
                "image_archive_sha256",
                "split",
                "pipeline_identity",
                "mapping",
                "mapping_sha256",
                "metrics",
                "thresholds",
                "threshold_protocol_sha256",
                "thresholds_frozen_before_metric_access",
                "gate_status",
                "reason_codes",
                "parity_probe_sha256",
                "privacy_contract",
                "contains_pronunciation_truth",
                "contains_action_truth",
                "artifact_sha256",
            },
            "p6_geometry_artifact",
        )
        if (
            document["privacy_contract"]
            != "aggregate_only_no_paths_ids_frames_or_landmark_arrays"
            or document["contains_pronunciation_truth"] is not False
            or document["contains_action_truth"] is not False
        ):
            raise VisualFeedbackContractError("P6 privacy/truth boundary differs")
        split = require_mapping(document["split"], "p6_geometry_artifact.split")
        require_exact_keys(
            split,
            {
                "development_partition_sha256",
                "calibration_partition_sha256",
                "development_actor_count",
                "calibration_actor_count",
                "actor_overlap_count",
                "attempt_overlap_count",
                "assignment_unit",
                "frozen_before_metric_access",
            },
            "p6_geometry_artifact.split",
        )
        if split["assignment_unit"] != "actor_id":
            raise VisualFeedbackContractError("P6 split assignment unit differs")
        pipeline = require_mapping(
            document["pipeline_identity"], "p6_geometry_artifact.pipeline_identity"
        )
        require_exact_keys(
            pipeline,
            {
                "tracker_revision",
                "tracker_sha256",
                "model_sha256",
                "admission_adapter_sha256",
                "calibration_service_sha256",
            },
            "p6_geometry_artifact.pipeline_identity",
        )
        mapping = require_mapping(document["mapping"], "p6_geometry_artifact.mapping")
        require_exact_keys(
            mapping,
            {"gold_keypoint_names", "mediapipe_indices", "candidate_set", "derivation"},
            "p6_geometry_artifact.mapping",
        )
        if (
            mapping["gold_keypoint_names"] != list(AIHUB68_KEYPOINT_NAMES)
            or mapping["candidate_set"] != list(MEDIAPIPE_LIP_CANDIDATE_INDICES)
            or mapping["derivation"]
            != "development_only_rectangular_hungarian_mean_nle_v1"
            or not isinstance(mapping["mediapipe_indices"], list)
        ):
            raise VisualFeedbackContractError("P6 mapping contract differs")
        thresholds_value = require_mapping(
            document["thresholds"], "p6_geometry_artifact.thresholds"
        )
        metrics_value = require_mapping(document["metrics"], "p6_geometry_artifact.metrics")
        threshold_fields = {
            "minimum_calibration_actor_count",
            "minimum_evaluated_calibration_frame_count",
            "minimum_overall_coverage",
            "minimum_camera_coverage",
            "maximum_overall_nle",
            "maximum_camera_nle",
            "required_camera_count",
            "maximum_repeatability_normalized_cue_rmse",
        }
        require_exact_keys(thresholds_value, threshold_fields, "p6_geometry_artifact.thresholds")
        thresholds = P6GeometryThresholds(**thresholds_value)  # type: ignore[arg-type]
        metric_fields = {
            "development_sample_count",
            "development_detected_count",
            "development_coverage_fraction",
            "development_normalized_landmark_error",
            "calibration_sample_count",
            "calibration_detected_count",
            "calibration_coverage_fraction",
            "calibration_normalized_landmark_error",
            "calibration_nle_p95",
            "repeatability_group_count",
            "repeatability_normalized_cue_rmse",
            "slices",
        }
        require_exact_keys(metrics_value, metric_fields, "p6_geometry_artifact.metrics")
        slice_values = metrics_value.get("slices")
        if not isinstance(slice_values, list):
            raise VisualFeedbackContractError("P6 metric slices must be an array")
        slices_list: list[P6GeometrySliceMetric] = []
        for item in slice_values:
            slice_mapping = require_mapping(item, "p6_geometry_slice")
            require_exact_keys(
                slice_mapping,
                {
                    "dimension",
                    "label",
                    "sample_count",
                    "detected_count",
                    "coverage_fraction",
                    "normalized_landmark_error",
                },
                "p6_geometry_slice",
            )
            slices_list.append(P6GeometrySliceMetric(**slice_mapping))  # type: ignore[arg-type]
        slices = tuple(slices_list)
        metric_arguments = dict(metrics_value)
        metric_arguments["slices"] = slices
        metrics = P6GeometryMetricSummary(**metric_arguments)  # type: ignore[arg-type]
        return cls(
            schema_version=document["schema_version"],
            artifact_id=document["artifact_id"],
            revision=document["revision"],
            source_dataset_id=document["source_dataset_id"],
            source_revision=document["source_revision"],
            rights_scope=document["rights_scope"],
            dataset_manifest_sha256=document["dataset_manifest_sha256"],
            population_id_sha256=document["population_id_sha256"],
            label_archive_sha256=document["label_archive_sha256"],
            image_archive_sha256=document["image_archive_sha256"],
            development_partition_sha256=split["development_partition_sha256"],
            calibration_partition_sha256=split["calibration_partition_sha256"],
            development_actor_count=split["development_actor_count"],
            calibration_actor_count=split["calibration_actor_count"],
            actor_overlap_count=split["actor_overlap_count"],
            attempt_overlap_count=split["attempt_overlap_count"],
            split_frozen_before_metric_access=split["frozen_before_metric_access"],
            tracker_revision=pipeline["tracker_revision"],
            tracker_sha256=pipeline["tracker_sha256"],
            model_sha256=pipeline["model_sha256"],
            admission_adapter_sha256=pipeline["admission_adapter_sha256"],
            calibration_service_sha256=pipeline["calibration_service_sha256"],
            mapping_mediapipe_indices=tuple(mapping["mediapipe_indices"]),
            mapping_sha256=document["mapping_sha256"],
            metrics=metrics,
            thresholds=thresholds,
            threshold_protocol_sha256=document["threshold_protocol_sha256"],
            thresholds_frozen_before_metric_access=document[
                "thresholds_frozen_before_metric_access"
            ],
            gate_status=document["gate_status"],
            reason_codes=(
                tuple(document["reason_codes"])
                if isinstance(document["reason_codes"], list)
                else ("p6_reason_codes_invalid",)
            ),
            parity_probe_sha256=document["parity_probe_sha256"],
            artifact_sha256=document["artifact_sha256"],
        )


@dataclass(frozen=True, slots=True)
class P6GeometryReloadReceipt:
    """Strict fresh-process cross-digest receipt."""

    loader_revision: str
    artifact_sha256: str
    artifact_document_sha256: str
    mapping_sha256: str
    threshold_protocol_sha256: str
    parity_probe_sha256: str
    receipt_sha256: str
    schema_version: str = P6_GEOMETRY_RELOAD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P6_GEOMETRY_RELOAD_SCHEMA_VERSION:
            raise VisualFeedbackContractError("P6 reload schema differs")
        require_safe_identifier(self.loader_revision, "p6_reload.loader_revision")
        for name in (
            "artifact_sha256",
            "artifact_document_sha256",
            "mapping_sha256",
            "threshold_protocol_sha256",
            "parity_probe_sha256",
            "receipt_sha256",
        ):
            require_sha256(getattr(self, name), f"p6_reload.{name}")
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("P6 reload receipt digest differs")

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "loader_revision": self.loader_revision,
            "artifact_sha256": self.artifact_sha256,
            "artifact_document_sha256": self.artifact_document_sha256,
            "mapping_sha256": self.mapping_sha256,
            "threshold_protocol_sha256": self.threshold_protocol_sha256,
            "parity_probe_sha256": self.parity_probe_sha256,
            "fresh_process": True,
            "hash_match": True,
            "mapping_hash_match": True,
            "threshold_hash_match": True,
            "output_parity": True,
            "network_isolation": "seccomp_socket_denied",
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> P6GeometryReloadReceipt:
        document = require_mapping(value, "p6_geometry_reload_receipt")
        require_exact_keys(
            document,
            {
                "schema_version",
                "loader_revision",
                "artifact_sha256",
                "artifact_document_sha256",
                "mapping_sha256",
                "threshold_protocol_sha256",
                "parity_probe_sha256",
                "fresh_process",
                "hash_match",
                "mapping_hash_match",
                "threshold_hash_match",
                "output_parity",
                "network_isolation",
                "receipt_sha256",
            },
            "p6_geometry_reload_receipt",
        )
        for field in (
            "fresh_process",
            "hash_match",
            "mapping_hash_match",
            "threshold_hash_match",
            "output_parity",
        ):
            if document[field] is not True:
                raise VisualFeedbackContractError("P6 reload evidence is incomplete")
        if document["network_isolation"] != "seccomp_socket_denied":
            raise VisualFeedbackContractError("P6 reload isolation differs")
        return cls(
            schema_version=document["schema_version"],
            loader_revision=document["loader_revision"],
            artifact_sha256=document["artifact_sha256"],
            artifact_document_sha256=document["artifact_document_sha256"],
            mapping_sha256=document["mapping_sha256"],
            threshold_protocol_sha256=document["threshold_protocol_sha256"],
            parity_probe_sha256=document["parity_probe_sha256"],
            receipt_sha256=document["receipt_sha256"],
        )

    def validate_artifact(self, artifact: P6GeometryArtifact) -> None:
        expected = {
            "artifact_sha256": artifact.artifact_sha256,
            "artifact_document_sha256": sha256_document(artifact.to_json_object()),
            "mapping_sha256": artifact.mapping_sha256,
            "threshold_protocol_sha256": artifact.threshold_protocol_sha256,
            "parity_probe_sha256": artifact.parity_probe_sha256,
        }
        if any(getattr(self, name) != value for name, value in expected.items()):
            raise VisualFeedbackContractError("P6 reload/artifact cross-digest differs")


@dataclass(frozen=True, slots=True)
class P6GeometryEvidenceBundle:
    """Exact artifact+reload serialization consumed by promotion builders."""

    artifact: P6GeometryArtifact
    reload_receipt: P6GeometryReloadReceipt
    bundle_sha256: str
    schema_version: str = P6_GEOMETRY_BUNDLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P6_GEOMETRY_BUNDLE_SCHEMA_VERSION:
            raise VisualFeedbackContractError("P6 geometry bundle schema differs")
        require_sha256(self.bundle_sha256, "p6_geometry_bundle.bundle_sha256")
        self.reload_receipt.validate_artifact(self.artifact)
        if self.bundle_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("P6 geometry bundle digest differs")

    @classmethod
    def issue(
        cls,
        *,
        artifact: P6GeometryArtifact,
        reload_receipt: P6GeometryReloadReceipt,
    ) -> P6GeometryEvidenceBundle:
        unsigned = {
            "schema_version": P6_GEOMETRY_BUNDLE_SCHEMA_VERSION,
            "artifact": artifact.to_json_object(),
            "reload_receipt": reload_receipt.to_json_object(),
        }
        return cls(
            artifact=artifact,
            reload_receipt=reload_receipt,
            bundle_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "artifact": self.artifact.to_json_object(),
            "reload_receipt": self.reload_receipt.to_json_object(),
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "bundle_sha256": self.bundle_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> P6GeometryEvidenceBundle:
        document = require_mapping(value, "p6_geometry_bundle")
        require_exact_keys(
            document,
            {"schema_version", "artifact", "reload_receipt", "bundle_sha256"},
            "p6_geometry_bundle",
        )
        return cls(
            schema_version=document["schema_version"],
            artifact=P6GeometryArtifact.from_json_object(document["artifact"]),
            reload_receipt=P6GeometryReloadReceipt.from_json_object(
                document["reload_receipt"]
            ),
            bundle_sha256=document["bundle_sha256"],
        )


def p6_mapping_parity_probe_sha256(mapping_indices: tuple[int, ...]) -> str:
    """Hash a fixed synthetic candidate projection to test reload parity."""

    candidate_points = {
        index: [rank / 100.0, rank / 200.0]
        for rank, index in enumerate(MEDIAPIPE_LIP_CANDIDATE_INDICES)
    }
    return sha256_document(
        {
            "probe_revision": "p6_mapping_parity_probe_v1",
            "projected_points": [candidate_points[index] for index in mapping_indices],
        }
    )


__all__ = [
    "P6_GEOMETRY_ARTIFACT_SCHEMA_VERSION",
    "P6_GEOMETRY_BUNDLE_SCHEMA_VERSION",
    "P6_GEOMETRY_RELOAD_SCHEMA_VERSION",
    "P6GeometryArtifact",
    "P6GeometryEvidenceBundle",
    "P6GeometryMetricSummary",
    "P6GeometryReloadReceipt",
    "P6GeometrySliceMetric",
    "P6GeometryThresholds",
    "p6_mapping_parity_probe_sha256",
]
