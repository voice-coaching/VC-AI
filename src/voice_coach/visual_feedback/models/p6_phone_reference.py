"""Immutable P6B visible-lip reference producer evidence.

The AI Hub 68 source remains inside a restricted process boundary.  These
contracts expose only aggregate metrics and SHA-256 lineage.  In particular,
the artifact calibrates a visible aperture cue; it is not pronunciation,
correctness, anatomy, airflow, or medical ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from ..errors import VisualFeedbackContractError
from .artifacts import VisualCalibrationArtifact, VisualClaimCatalog
from .enums import CalibrationScope, DeviationDirection
from .integrity import (
    require_boolean,
    require_exact_keys,
    require_finite_number,
    require_integer,
    require_mapping,
    require_safe_identifier,
    require_sha256,
    require_text_token,
    sha256_document,
)
from .runtime_contract import RuntimeVisualClaimPolicy


P6_PHONE_REFERENCE_PRODUCER_SCHEMA_VERSION = (
    "voice_coach.p6_phone_reference_producer_evidence.v2"
)
P6_PHONE_REFERENCE_RELOAD_SCHEMA_VERSION = (
    "voice_coach.p6_phone_reference_reload_receipt.v2"
)
P6_PHONE_REFERENCE_BUNDLE_SCHEMA_VERSION = (
    "voice_coach.p6_phone_reference_evidence_bundle.v2"
)
P6_PHONE_REFERENCE_PROTOCOL_REVISION = "aihub68_eo_frame10_aperture_v2"
P6_PHONE_REFERENCE_CAPTURE_CONTRACT_ID = "single_face_visible_lips_rgb_v1"
P6_PHONE_REFERENCE_CONTEXT_ID = "phone_center"
P6_PHONE_REFERENCE_CUE_ID = "lip_aperture_ratio"
P6_PHONE_REFERENCE_EXPECTED_PHONE = "ㅓ"


@dataclass(frozen=True, slots=True)
class P6PhoneReferenceThresholds:
    """Thresholds fixed before accessing the selected aperture values."""

    minimum_development_sample_count: int = 50
    minimum_calibration_sample_count: int = 25
    required_camera_count: int = 5
    minimum_overall_calibration_support_coverage: float = 0.90
    minimum_camera_calibration_support_coverage: float = 0.80
    minimum_single_face_coverage: float = 0.95
    minimum_runtime_parity_coverage: float = 0.95
    maximum_runtime_parity_absolute_difference: float = 0.000001
    feedback_trigger_standardized_deviation: float = 2.5
    minimum_robust_scale: float = 0.01

    def __post_init__(self) -> None:
        for name in (
            "minimum_development_sample_count",
            "minimum_calibration_sample_count",
            "required_camera_count",
        ):
            if require_integer(getattr(self, name), name, minimum=1) <= 0:
                raise AssertionError("unreachable")
        for name in (
            "minimum_overall_calibration_support_coverage",
            "minimum_camera_calibration_support_coverage",
            "minimum_single_face_coverage",
            "minimum_runtime_parity_coverage",
        ):
            value = require_finite_number(getattr(self, name), name)
            if not 0 <= value <= 1:
                raise VisualFeedbackContractError(f"{name} must be in [0,1]")
        for name in (
            "maximum_runtime_parity_absolute_difference",
            "feedback_trigger_standardized_deviation",
            "minimum_robust_scale",
        ):
            if require_finite_number(getattr(self, name), name) <= 0:
                raise VisualFeedbackContractError(f"{name} must be positive")

    def to_json_object(self) -> dict[str, object]:
        return {
            "minimum_development_sample_count": self.minimum_development_sample_count,
            "minimum_calibration_sample_count": self.minimum_calibration_sample_count,
            "required_camera_count": self.required_camera_count,
            "minimum_overall_calibration_support_coverage": (
                self.minimum_overall_calibration_support_coverage
            ),
            "minimum_camera_calibration_support_coverage": (
                self.minimum_camera_calibration_support_coverage
            ),
            "minimum_single_face_coverage": self.minimum_single_face_coverage,
            "minimum_runtime_parity_coverage": self.minimum_runtime_parity_coverage,
            "maximum_runtime_parity_absolute_difference": (
                self.maximum_runtime_parity_absolute_difference
            ),
            "feedback_trigger_standardized_deviation": (
                self.feedback_trigger_standardized_deviation
            ),
            "minimum_robust_scale": self.minimum_robust_scale,
        }

    @property
    def protocol_sha256(self) -> str:
        return sha256_document(
            {
                "protocol_revision": P6_PHONE_REFERENCE_PROTOCOL_REVISION,
                "source_selector": {
                    "word_id": 9,
                    "word": "어",
                    "frame_index": 10,
                    "expected_phone": P6_PHONE_REFERENCE_EXPECTED_PHONE,
                },
                "actor_split": "voice_coach.p6.actor_split.v1",
                "cue": {
                    "cue_id": P6_PHONE_REFERENCE_CUE_ID,
                    "outer_left_gold_index": 0,
                    "outer_right_gold_index": 6,
                    "inner_top_gold_index": 15,
                    "inner_bottom_gold_index": 21,
                    "formula": "abs(inner_bottom_y-inner_top_y)/abs(outer_right_x-outer_left_x)",
                },
                "statistics": {
                    "center": "development_median",
                    "scale": "max(1.4826*development_mad,0.01)",
                    "quantile": "type7_linear",
                    "support": "tukey_q1_minus_1.5iqr_q3_plus_1.5iqr_clamped_0_1",
                },
                "runtime_parity": "one_fresh_video_mode_worker_per_selected_frame",
                "thresholds": self.to_json_object(),
            }
        )

    @classmethod
    def from_json_object(cls, value: object) -> P6PhoneReferenceThresholds:
        document = require_mapping(value, "p6_phone_reference_thresholds")
        defaults = cls().to_json_object()
        require_exact_keys(document, set(defaults), "p6_phone_reference_thresholds")
        result = cls(**document)  # type: ignore[arg-type]
        if result != cls():
            raise VisualFeedbackContractError("P6B frozen thresholds differ")
        return result


@dataclass(frozen=True, slots=True)
class P6PhoneReferenceCameraMetric:
    camera_id: int
    sample_count: int
    detected_count: int
    supported_count: int
    support_coverage: float

    def __post_init__(self) -> None:
        require_integer(self.camera_id, "p6b_camera.camera_id", minimum=0)
        total = require_integer(self.sample_count, "p6b_camera.sample_count", minimum=1)
        detected = require_integer(
            self.detected_count, "p6b_camera.detected_count", minimum=0
        )
        supported = require_integer(
            self.supported_count, "p6b_camera.supported_count", minimum=0
        )
        if not 0 <= supported <= detected <= total:
            raise VisualFeedbackContractError("P6B camera counts differ")
        coverage = require_finite_number(
            self.support_coverage, "p6b_camera.support_coverage"
        )
        if not 0 <= coverage <= 1 or not math.isclose(
            coverage, supported / total, abs_tol=1e-15
        ):
            raise VisualFeedbackContractError("P6B camera support coverage differs")

    def to_json_object(self) -> dict[str, object]:
        return {
            "camera_id": self.camera_id,
            "sample_count": self.sample_count,
            "detected_count": self.detected_count,
            "supported_count": self.supported_count,
            "support_coverage": self.support_coverage,
        }

    @classmethod
    def from_json_object(cls, value: object) -> P6PhoneReferenceCameraMetric:
        document = require_mapping(value, "p6_phone_reference_camera_metric")
        require_exact_keys(
            document,
            {
                "camera_id",
                "sample_count",
                "detected_count",
                "supported_count",
                "support_coverage",
            },
            "p6_phone_reference_camera_metric",
        )
        return cls(**document)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class P6PhoneReferenceMetrics:
    selected_sample_count: int
    development_sample_count: int
    development_detected_count: int
    calibration_sample_count: int
    calibration_detected_count: int
    single_face_coverage: float
    reference_center: float
    reference_scale: float
    support_minimum: float
    support_maximum: float
    calibration_supported_count: int
    calibration_support_coverage: float
    camera_metrics: tuple[P6PhoneReferenceCameraMetric, ...]

    def __post_init__(self) -> None:
        selected = require_integer(
            self.selected_sample_count, "p6b_metrics.selected_sample_count", minimum=1
        )
        development = require_integer(
            self.development_sample_count,
            "p6b_metrics.development_sample_count",
            minimum=1,
        )
        development_detected = require_integer(
            self.development_detected_count,
            "p6b_metrics.development_detected_count",
            minimum=0,
        )
        calibration = require_integer(
            self.calibration_sample_count,
            "p6b_metrics.calibration_sample_count",
            minimum=1,
        )
        calibration_detected = require_integer(
            self.calibration_detected_count,
            "p6b_metrics.calibration_detected_count",
            minimum=0,
        )
        supported = require_integer(
            self.calibration_supported_count,
            "p6b_metrics.calibration_supported_count",
            minimum=0,
        )
        if (
            selected != development + calibration
            or development_detected > development
            or calibration_detected > calibration
            or supported > calibration_detected
        ):
            raise VisualFeedbackContractError("P6B aggregate counts differ")
        coverage = require_finite_number(
            self.single_face_coverage, "p6b_metrics.single_face_coverage"
        )
        if not 0 <= coverage <= 1 or not math.isclose(
            coverage,
            (development_detected + calibration_detected) / selected,
            abs_tol=1e-15,
        ):
            raise VisualFeedbackContractError("P6B single-face coverage differs")
        support_coverage = require_finite_number(
            self.calibration_support_coverage,
            "p6b_metrics.calibration_support_coverage",
        )
        if not 0 <= support_coverage <= 1 or not math.isclose(
            support_coverage, supported / calibration, abs_tol=1e-15
        ):
            raise VisualFeedbackContractError("P6B support coverage differs")
        center = require_finite_number(
            self.reference_center, "p6b_metrics.reference_center"
        )
        scale = require_finite_number(
            self.reference_scale, "p6b_metrics.reference_scale"
        )
        minimum = require_finite_number(
            self.support_minimum, "p6b_metrics.support_minimum"
        )
        maximum = require_finite_number(
            self.support_maximum, "p6b_metrics.support_maximum"
        )
        if scale <= 0 or not 0 <= minimum <= center <= maximum <= 1:
            raise VisualFeedbackContractError("P6B reference geometry differs")
        camera_ids = tuple(metric.camera_id for metric in self.camera_metrics)
        if len(set(camera_ids)) != len(camera_ids):
            raise VisualFeedbackContractError("P6B camera metrics are duplicated")
        if sum(item.sample_count for item in self.camera_metrics) != calibration:
            raise VisualFeedbackContractError("P6B camera totals differ")

    def to_json_object(self) -> dict[str, object]:
        return {
            "selected_sample_count": self.selected_sample_count,
            "development_sample_count": self.development_sample_count,
            "development_detected_count": self.development_detected_count,
            "calibration_sample_count": self.calibration_sample_count,
            "calibration_detected_count": self.calibration_detected_count,
            "single_face_coverage": self.single_face_coverage,
            "reference_center": self.reference_center,
            "reference_scale": self.reference_scale,
            "support_minimum": self.support_minimum,
            "support_maximum": self.support_maximum,
            "calibration_supported_count": self.calibration_supported_count,
            "calibration_support_coverage": self.calibration_support_coverage,
            "camera_metrics": [item.to_json_object() for item in self.camera_metrics],
        }

    @classmethod
    def from_json_object(cls, value: object) -> P6PhoneReferenceMetrics:
        document = require_mapping(value, "p6_phone_reference_metrics")
        require_exact_keys(
            document,
            {
                "selected_sample_count",
                "development_sample_count",
                "development_detected_count",
                "calibration_sample_count",
                "calibration_detected_count",
                "single_face_coverage",
                "reference_center",
                "reference_scale",
                "support_minimum",
                "support_maximum",
                "calibration_supported_count",
                "calibration_support_coverage",
                "camera_metrics",
            },
            "p6_phone_reference_metrics",
        )
        cameras = document["camera_metrics"]
        if not isinstance(cameras, list):
            raise VisualFeedbackContractError("P6B camera metrics must be an array")
        values = dict(document)
        values["camera_metrics"] = tuple(
            P6PhoneReferenceCameraMetric.from_json_object(item) for item in cameras
        )
        return cls(**values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class P6RuntimeCueParityReceipt:
    selected_sample_count: int
    calibration_detected_count: int
    production_detected_count: int
    paired_cue_count: int
    parity_coverage: float
    maximum_absolute_cue_difference: float
    mean_absolute_cue_difference: float
    mapping_indices: tuple[int, int, int, int]
    calibration_worker_revision: str
    calibration_worker_sha256: str
    production_worker_revision: str
    production_worker_sha256: str
    production_adapter_sha256: str
    model_sha256: str
    parity_values_sha256: str
    receipt_sha256: str
    schema_version: str = "voice_coach.p6_runtime_cue_parity_receipt.v2"

    def __post_init__(self) -> None:
        if self.schema_version != "voice_coach.p6_runtime_cue_parity_receipt.v2":
            raise VisualFeedbackContractError("P6B parity schema differs")
        selected = require_integer(
            self.selected_sample_count, "p6b_parity.selected_sample_count", minimum=1
        )
        for name in (
            "calibration_detected_count",
            "production_detected_count",
            "paired_cue_count",
        ):
            value = require_integer(getattr(self, name), f"p6b_parity.{name}", minimum=0)
            if value > selected:
                raise VisualFeedbackContractError("P6B parity count differs")
        if self.paired_cue_count > min(
            self.calibration_detected_count, self.production_detected_count
        ):
            raise VisualFeedbackContractError("P6B paired count differs")
        coverage = require_finite_number(
            self.parity_coverage, "p6b_parity.parity_coverage"
        )
        if not 0 <= coverage <= 1 or not math.isclose(
            coverage, self.paired_cue_count / selected, abs_tol=1e-15
        ):
            raise VisualFeedbackContractError("P6B parity coverage differs")
        for name in (
            "maximum_absolute_cue_difference",
            "mean_absolute_cue_difference",
        ):
            if require_finite_number(getattr(self, name), f"p6b_parity.{name}") < 0:
                raise VisualFeedbackContractError("P6B parity difference is negative")
        if self.mapping_indices != (61, 291, 13, 14):
            raise VisualFeedbackContractError("P6B production cue mapping differs")
        for name in (
            "calibration_worker_revision",
            "production_worker_revision",
        ):
            require_safe_identifier(getattr(self, name), f"p6b_parity.{name}")
        for name in (
            "calibration_worker_sha256",
            "production_worker_sha256",
            "production_adapter_sha256",
            "model_sha256",
            "parity_values_sha256",
            "receipt_sha256",
        ):
            require_sha256(getattr(self, name), f"p6b_parity.{name}")
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("P6B parity receipt digest differs")

    @classmethod
    def issue(cls, **values: object) -> P6RuntimeCueParityReceipt:
        temporary = object.__new__(cls)
        object.__setattr__(
            temporary,
            "schema_version",
            "voice_coach.p6_runtime_cue_parity_receipt.v2",
        )
        for key, item in values.items():
            object.__setattr__(temporary, key, item)
        return cls(  # type: ignore[arg-type]
            **values,
            receipt_sha256=sha256_document(temporary.unsigned_json_object()),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "selected_sample_count": self.selected_sample_count,
            "calibration_detected_count": self.calibration_detected_count,
            "production_detected_count": self.production_detected_count,
            "paired_cue_count": self.paired_cue_count,
            "parity_coverage": self.parity_coverage,
            "maximum_absolute_cue_difference": self.maximum_absolute_cue_difference,
            "mean_absolute_cue_difference": self.mean_absolute_cue_difference,
            "mapping_indices": list(self.mapping_indices),
            "calibration_worker_revision": self.calibration_worker_revision,
            "calibration_worker_sha256": self.calibration_worker_sha256,
            "production_worker_revision": self.production_worker_revision,
            "production_worker_sha256": self.production_worker_sha256,
            "production_adapter_sha256": self.production_adapter_sha256,
            "model_sha256": self.model_sha256,
            "parity_values_sha256": self.parity_values_sha256,
            "privacy_contract": "aggregate_only_no_paths_ids_frames_or_cue_arrays",
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> P6RuntimeCueParityReceipt:
        document = require_mapping(value, "p6_runtime_cue_parity_receipt")
        expected = set(
            cls.issue(
                selected_sample_count=1,
                calibration_detected_count=1,
                production_detected_count=1,
                paired_cue_count=1,
                parity_coverage=1.0,
                maximum_absolute_cue_difference=0.0,
                mean_absolute_cue_difference=0.0,
                mapping_indices=(61, 291, 13, 14),
                calibration_worker_revision="fixture",
                calibration_worker_sha256="0" * 64,
                production_worker_revision="fixture",
                production_worker_sha256="1" * 64,
                production_adapter_sha256="2" * 64,
                model_sha256="3" * 64,
                parity_values_sha256="4" * 64,
            ).to_json_object()
        )
        require_exact_keys(document, expected, "p6_runtime_cue_parity_receipt")
        if document["privacy_contract"] != "aggregate_only_no_paths_ids_frames_or_cue_arrays":
            raise VisualFeedbackContractError("P6B parity privacy contract differs")
        values = {key: item for key, item in document.items() if key != "privacy_contract"}
        mapping = values.get("mapping_indices")
        if not isinstance(mapping, list):
            raise VisualFeedbackContractError("P6B parity mapping must be an array")
        values["mapping_indices"] = tuple(mapping)
        return cls(**values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class P6PhoneReferenceProducerEvidence:
    source_revision: str
    dataset_manifest_sha256: str
    label_archive_sha256: str
    image_archive_sha256: str
    population_id_sha256: str
    geometry_bundle_sha256: str
    geometry_artifact_sha256: str
    geometry_mapping_sha256: str
    admission_adapter_sha256: str
    reference_service_sha256: str
    development_partition_sha256: str
    calibration_partition_sha256: str
    selected_development_partition_sha256: str
    selected_calibration_partition_sha256: str
    development_actor_count: int
    calibration_actor_count: int
    actor_overlap_count: int
    attempt_overlap_count: int
    protocol_sha256: str
    thresholds: P6PhoneReferenceThresholds
    mapping_indices: tuple[int, int, int, int]
    metrics: P6PhoneReferenceMetrics
    runtime_parity_receipt: P6RuntimeCueParityReceipt
    calibration_artifact: VisualCalibrationArtifact
    claim_catalog: VisualClaimCatalog
    claim_policy: RuntimeVisualClaimPolicy
    producer_sha256: str
    source_dataset_id: str = "aihub_68"
    protocol_revision: str = P6_PHONE_REFERENCE_PROTOCOL_REVISION
    word_id: int = 9
    word: str = "어"
    frame_index: int = 10
    expected_phone: str = P6_PHONE_REFERENCE_EXPECTED_PHONE
    capture_contract_id: str = P6_PHONE_REFERENCE_CAPTURE_CONTRACT_ID
    context_id: str = P6_PHONE_REFERENCE_CONTEXT_ID
    schema_version: str = P6_PHONE_REFERENCE_PRODUCER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P6_PHONE_REFERENCE_PRODUCER_SCHEMA_VERSION:
            raise VisualFeedbackContractError("P6B producer schema differs")
        if (
            self.source_dataset_id != "aihub_68"
            or self.protocol_revision != P6_PHONE_REFERENCE_PROTOCOL_REVISION
            or self.word_id != 9
            or self.word != "어"
            or self.frame_index != 10
            or self.expected_phone != P6_PHONE_REFERENCE_EXPECTED_PHONE
            or self.capture_contract_id != P6_PHONE_REFERENCE_CAPTURE_CONTRACT_ID
            or self.context_id != P6_PHONE_REFERENCE_CONTEXT_ID
        ):
            raise VisualFeedbackContractError("P6B frozen source protocol differs")
        require_safe_identifier(self.source_revision, "p6b_producer.source_revision")
        require_text_token(self.expected_phone, "p6b_producer.expected_phone")
        for name in (
            "dataset_manifest_sha256",
            "label_archive_sha256",
            "image_archive_sha256",
            "population_id_sha256",
            "geometry_bundle_sha256",
            "geometry_artifact_sha256",
            "geometry_mapping_sha256",
            "admission_adapter_sha256",
            "reference_service_sha256",
            "development_partition_sha256",
            "calibration_partition_sha256",
            "selected_development_partition_sha256",
            "selected_calibration_partition_sha256",
            "protocol_sha256",
            "producer_sha256",
        ):
            require_sha256(getattr(self, name), f"p6b_producer.{name}")
        if self.protocol_sha256 != self.thresholds.protocol_sha256:
            raise VisualFeedbackContractError("P6B protocol digest differs")
        for name, expected in (
            ("development_actor_count", 12),
            ("calibration_actor_count", 5),
            ("actor_overlap_count", 0),
            ("attempt_overlap_count", 0),
        ):
            if require_integer(getattr(self, name), f"p6b_producer.{name}", minimum=0) != expected:
                raise VisualFeedbackContractError("P6B actor/attempt split differs")
        if self.mapping_indices != (61, 291, 13, 14):
            raise VisualFeedbackContractError("P6B mapping differs")
        _validate_outputs(self)
        if self.producer_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("P6B producer digest differs")

    @classmethod
    def issue(cls, **values: object) -> P6PhoneReferenceProducerEvidence:
        temporary = object.__new__(cls)
        for field_name, default in (
            ("source_dataset_id", "aihub_68"),
            ("protocol_revision", P6_PHONE_REFERENCE_PROTOCOL_REVISION),
            ("word_id", 9),
            ("word", "어"),
            ("frame_index", 10),
            ("expected_phone", P6_PHONE_REFERENCE_EXPECTED_PHONE),
            ("capture_contract_id", P6_PHONE_REFERENCE_CAPTURE_CONTRACT_ID),
            ("context_id", P6_PHONE_REFERENCE_CONTEXT_ID),
            ("schema_version", P6_PHONE_REFERENCE_PRODUCER_SCHEMA_VERSION),
        ):
            object.__setattr__(temporary, field_name, values.get(field_name, default))
        for key, item in values.items():
            object.__setattr__(temporary, key, item)
        unsigned = temporary.unsigned_json_object()
        return cls(**values, producer_sha256=sha256_document(unsigned))  # type: ignore[arg-type]

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_dataset_id": self.source_dataset_id,
            "source_revision": self.source_revision,
            "source_lineage": {
                "dataset_manifest_sha256": self.dataset_manifest_sha256,
                "label_archive_sha256": self.label_archive_sha256,
                "image_archive_sha256": self.image_archive_sha256,
                "population_id_sha256": self.population_id_sha256,
                "geometry_bundle_sha256": self.geometry_bundle_sha256,
                "geometry_artifact_sha256": self.geometry_artifact_sha256,
                "geometry_mapping_sha256": self.geometry_mapping_sha256,
                "admission_adapter_sha256": self.admission_adapter_sha256,
                "reference_service_sha256": self.reference_service_sha256,
            },
            "partition": {
                "development_partition_sha256": self.development_partition_sha256,
                "calibration_partition_sha256": self.calibration_partition_sha256,
                "selected_development_partition_sha256": self.selected_development_partition_sha256,
                "selected_calibration_partition_sha256": self.selected_calibration_partition_sha256,
                "development_actor_count": self.development_actor_count,
                "calibration_actor_count": self.calibration_actor_count,
                "actor_overlap_count": self.actor_overlap_count,
                "attempt_overlap_count": self.attempt_overlap_count,
                "assignment_unit": "actor_id",
                "same_as_geometry_split": True,
            },
            "protocol_revision": self.protocol_revision,
            "protocol_sha256": self.protocol_sha256,
            "thresholds_frozen_before_metric_access": True,
            "partition_frozen_before_metric_access": True,
            "source_selector": {
                "word_id": self.word_id,
                "word": self.word,
                "frame_index": self.frame_index,
                "expected_phone": self.expected_phone,
            },
            "capture_contract_id": self.capture_contract_id,
            "context_id": self.context_id,
            "mapping_indices": list(self.mapping_indices),
            "thresholds": self.thresholds.to_json_object(),
            "metrics": self.metrics.to_json_object(),
            "runtime_parity_receipt": self.runtime_parity_receipt.to_json_object(),
            "calibration_artifact": self.calibration_artifact.to_json_object(),
            "claim_catalog": self.claim_catalog.to_json_object(),
            "claim_policy": self.claim_policy.to_json_object(),
            "gate_status": "passed",
            "reason_codes": [],
            "privacy_contract": "aggregate_only_no_paths_ids_frames_or_cue_arrays",
            "claim_boundary": "visible_lip_opening_only_no_correctness_or_hidden_anatomy",
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "producer_sha256": self.producer_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> P6PhoneReferenceProducerEvidence:
        document = require_mapping(value, "p6_phone_reference_producer")
        expected = {
            "schema_version", "source_dataset_id", "source_revision", "source_lineage",
            "partition", "protocol_revision", "protocol_sha256",
            "thresholds_frozen_before_metric_access", "partition_frozen_before_metric_access",
            "source_selector", "capture_contract_id", "context_id", "mapping_indices",
            "thresholds", "metrics", "runtime_parity_receipt", "calibration_artifact",
            "claim_catalog", "claim_policy", "gate_status", "reason_codes",
            "privacy_contract", "claim_boundary", "producer_sha256",
        }
        require_exact_keys(document, expected, "p6_phone_reference_producer")
        if (
            document["thresholds_frozen_before_metric_access"] is not True
            or document["partition_frozen_before_metric_access"] is not True
            or document["gate_status"] != "passed"
            or document["reason_codes"] != []
            or document["privacy_contract"] != "aggregate_only_no_paths_ids_frames_or_cue_arrays"
            or document["claim_boundary"] != "visible_lip_opening_only_no_correctness_or_hidden_anatomy"
        ):
            raise VisualFeedbackContractError("P6B producer boundary differs")
        lineage = require_mapping(document["source_lineage"], "p6b_source_lineage")
        require_exact_keys(lineage, {
            "dataset_manifest_sha256", "label_archive_sha256", "image_archive_sha256",
            "population_id_sha256", "geometry_bundle_sha256", "geometry_artifact_sha256",
            "geometry_mapping_sha256",
            "admission_adapter_sha256",
            "reference_service_sha256",
        }, "p6b_source_lineage")
        partition = require_mapping(document["partition"], "p6b_partition")
        require_exact_keys(partition, {
            "development_partition_sha256", "calibration_partition_sha256",
            "selected_development_partition_sha256", "selected_calibration_partition_sha256",
            "development_actor_count", "calibration_actor_count", "actor_overlap_count",
            "attempt_overlap_count", "assignment_unit", "same_as_geometry_split",
        }, "p6b_partition")
        if partition["assignment_unit"] != "actor_id" or partition["same_as_geometry_split"] is not True:
            raise VisualFeedbackContractError("P6B partition contract differs")
        selector = require_mapping(document["source_selector"], "p6b_source_selector")
        require_exact_keys(selector, {"word_id", "word", "frame_index", "expected_phone"}, "p6b_source_selector")
        mapping = document["mapping_indices"]
        if not isinstance(mapping, list):
            raise VisualFeedbackContractError("P6B mapping must be an array")
        return cls(
            schema_version=document["schema_version"],
            source_dataset_id=document["source_dataset_id"],
            source_revision=document["source_revision"],
            **lineage,
            **{key: item for key, item in partition.items() if key not in {"assignment_unit", "same_as_geometry_split"}},
            protocol_revision=document["protocol_revision"],
            protocol_sha256=document["protocol_sha256"],
            word_id=selector["word_id"],
            word=selector["word"],
            frame_index=selector["frame_index"],
            expected_phone=selector["expected_phone"],
            capture_contract_id=document["capture_contract_id"],
            context_id=document["context_id"],
            mapping_indices=tuple(mapping),
            thresholds=P6PhoneReferenceThresholds.from_json_object(document["thresholds"]),
            metrics=P6PhoneReferenceMetrics.from_json_object(document["metrics"]),
            runtime_parity_receipt=P6RuntimeCueParityReceipt.from_json_object(document["runtime_parity_receipt"]),
            calibration_artifact=VisualCalibrationArtifact.from_json_object(document["calibration_artifact"]),
            claim_catalog=VisualClaimCatalog.from_json_object(document["claim_catalog"]),
            claim_policy=RuntimeVisualClaimPolicy.from_json_object(document["claim_policy"]),
            producer_sha256=document["producer_sha256"],
        )


def _validate_outputs(producer: P6PhoneReferenceProducerEvidence) -> None:
    thresholds = producer.thresholds
    metrics = producer.metrics
    parity = producer.runtime_parity_receipt
    failures: list[str] = []
    if metrics.development_sample_count < thresholds.minimum_development_sample_count:
        failures.append("p6b_development_count_below_threshold")
    if metrics.calibration_sample_count < thresholds.minimum_calibration_sample_count:
        failures.append("p6b_calibration_count_below_threshold")
    if len(metrics.camera_metrics) != thresholds.required_camera_count:
        failures.append("p6b_camera_count_below_threshold")
    if metrics.single_face_coverage < thresholds.minimum_single_face_coverage:
        failures.append("p6b_single_face_coverage_below_threshold")
    if metrics.calibration_support_coverage < thresholds.minimum_overall_calibration_support_coverage:
        failures.append("p6b_support_coverage_below_threshold")
    if any(item.support_coverage < thresholds.minimum_camera_calibration_support_coverage for item in metrics.camera_metrics):
        failures.append("p6b_camera_support_coverage_below_threshold")
    if parity.parity_coverage < thresholds.minimum_runtime_parity_coverage:
        failures.append("p6b_runtime_parity_coverage_below_threshold")
    if parity.maximum_absolute_cue_difference > thresholds.maximum_runtime_parity_absolute_difference:
        failures.append("p6b_runtime_parity_difference_above_threshold")
    if failures:
        raise VisualFeedbackContractError(f"P6B producer gate failed: {failures[0]}")
    artifact = producer.calibration_artifact
    if (
        artifact.scope is not CalibrationScope.CALIBRATED
        or artifact.capture_contract_id != producer.capture_contract_id
        or artifact.population_id_sha256 != producer.population_id_sha256
        or len(artifact.rules) != 1
    ):
        raise VisualFeedbackContractError("P6B calibration artifact differs")
    rule = artifact.rules[0]
    if (
        rule.cue_id != P6_PHONE_REFERENCE_CUE_ID
        or rule.measurement_unit != "ratio"
        or rule.expected_phones != (P6_PHONE_REFERENCE_EXPECTED_PHONE,)
        or rule.context_id != P6_PHONE_REFERENCE_CONTEXT_ID
        or rule.reference_center != metrics.reference_center
        or rule.reference_scale != metrics.reference_scale
        or rule.support_minimum != metrics.support_minimum
        or rule.support_maximum != metrics.support_maximum
        or rule.feedback_minimum_absolute_deviation != thresholds.feedback_trigger_standardized_deviation
    ):
        raise VisualFeedbackContractError("P6B calibration rule differs")
    catalog = producer.claim_catalog
    if catalog.scope is not CalibrationScope.CALIBRATED or len(catalog.claims) != 2:
        raise VisualFeedbackContractError("P6B claim catalog differs")
    expected_claims = {
        ("reduce_visible_lip_opening", "visible_lip_opening_above_reference_v1", "visual_lip_opening_reduce", DeviationDirection.ABOVE_REFERENCE),
        ("increase_visible_lip_opening", "visible_lip_opening_below_reference_v1", "visual_lip_opening_increase", DeviationDirection.BELOW_REFERENCE),
    }
    actual_claims = {
        (claim.action_id, claim.approved_claim_id, claim.renderer_key, claim.direction)
        for claim in catalog.claims
        if claim.cue_id == P6_PHONE_REFERENCE_CUE_ID
        and claim.expected_phones == (P6_PHONE_REFERENCE_EXPECTED_PHONE,)
        and claim.context_id == P6_PHONE_REFERENCE_CONTEXT_ID
    }
    if actual_claims != expected_claims:
        raise VisualFeedbackContractError("P6B visible-only claim set differs")
    policy = producer.claim_policy
    catalog_bindings = {
        (claim.approved_claim_id, claim.action_id, claim.renderer_key, claim.cue_id)
        for claim in catalog.claims
    }
    policy_bindings = {
        (item.approved_claim_id, item.action_id, item.renderer_key, item.cue_id)
        for item in policy.allowed_claim_bindings
    }
    required_forbidden = {
        "airflow", "anatomy", "correctness", "diagnosis", "free_prose",
        "glottis", "medical", "tongue", "velum",
    }
    if (
        policy.allowed_cue_ids != (P6_PHONE_REFERENCE_CUE_ID,)
        or set(policy.allowed_renderer_keys) != {"visual_lip_opening_reduce", "visual_lip_opening_increase"}
        or policy_bindings != catalog_bindings
        or set(policy.forbidden_identifier_tokens) != required_forbidden
    ):
        raise VisualFeedbackContractError("P6B exact runtime claim policy differs")


@dataclass(frozen=True, slots=True)
class P6PhoneReferenceReloadReceipt:
    loader_revision: str
    loader_sha256: str
    producer_sha256: str
    producer_document_sha256: str
    parity_receipt_sha256: str
    calibration_artifact_sha256: str
    catalog_sha256: str
    policy_sha256: str
    receipt_sha256: str
    schema_version: str = P6_PHONE_REFERENCE_RELOAD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P6_PHONE_REFERENCE_RELOAD_SCHEMA_VERSION:
            raise VisualFeedbackContractError("P6B reload schema differs")
        require_safe_identifier(self.loader_revision, "p6b_reload.loader_revision")
        for name in (
            "loader_sha256", "producer_sha256", "producer_document_sha256",
            "parity_receipt_sha256", "calibration_artifact_sha256", "catalog_sha256",
            "policy_sha256", "receipt_sha256",
        ):
            require_sha256(getattr(self, name), f"p6b_reload.{name}")
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("P6B reload digest differs")

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "loader_revision": self.loader_revision,
            "loader_sha256": self.loader_sha256,
            "producer_sha256": self.producer_sha256,
            "producer_document_sha256": self.producer_document_sha256,
            "parity_receipt_sha256": self.parity_receipt_sha256,
            "calibration_artifact_sha256": self.calibration_artifact_sha256,
            "catalog_sha256": self.catalog_sha256,
            "policy_sha256": self.policy_sha256,
            "fresh_process": True,
            "strict_reload": True,
            "network_isolation": "seccomp_socket_denied",
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def issue(cls, **values: object) -> P6PhoneReferenceReloadReceipt:
        temporary = object.__new__(cls)
        object.__setattr__(temporary, "schema_version", P6_PHONE_REFERENCE_RELOAD_SCHEMA_VERSION)
        for key, item in values.items():
            object.__setattr__(temporary, key, item)
        return cls(**values, receipt_sha256=sha256_document(temporary.unsigned_json_object()))  # type: ignore[arg-type]

    @classmethod
    def from_json_object(cls, value: object) -> P6PhoneReferenceReloadReceipt:
        document = require_mapping(value, "p6_phone_reference_reload")
        require_exact_keys(document, {
            "schema_version", "loader_revision", "loader_sha256", "producer_sha256",
            "producer_document_sha256", "parity_receipt_sha256",
            "calibration_artifact_sha256", "catalog_sha256", "policy_sha256",
            "fresh_process", "strict_reload", "network_isolation", "receipt_sha256",
        }, "p6_phone_reference_reload")
        if document["fresh_process"] is not True or document["strict_reload"] is not True or document["network_isolation"] != "seccomp_socket_denied":
            raise VisualFeedbackContractError("P6B reload evidence differs")
        values = {key: item for key, item in document.items() if key not in {"fresh_process", "strict_reload", "network_isolation"}}
        return cls(**values)  # type: ignore[arg-type]

    def validate_producer(self, producer: P6PhoneReferenceProducerEvidence) -> None:
        if (
            self.producer_sha256 != producer.producer_sha256
            or self.producer_document_sha256 != sha256_document(producer.to_json_object())
            or self.parity_receipt_sha256 != producer.runtime_parity_receipt.receipt_sha256
            or self.calibration_artifact_sha256 != producer.calibration_artifact.artifact_sha256
            or self.catalog_sha256 != producer.claim_catalog.catalog_sha256
            or self.policy_sha256 != producer.claim_policy.policy_sha256
        ):
            raise VisualFeedbackContractError("P6B reload cross-digest differs")


@dataclass(frozen=True, slots=True)
class P6PhoneReferenceEvidenceBundle:
    producer: P6PhoneReferenceProducerEvidence
    reload_receipt: P6PhoneReferenceReloadReceipt
    bundle_sha256: str
    schema_version: str = P6_PHONE_REFERENCE_BUNDLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P6_PHONE_REFERENCE_BUNDLE_SCHEMA_VERSION:
            raise VisualFeedbackContractError("P6B bundle schema differs")
        self.reload_receipt.validate_producer(self.producer)
        require_sha256(self.bundle_sha256, "p6b_bundle.bundle_sha256")
        if self.bundle_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("P6B bundle digest differs")

    @classmethod
    def issue(cls, *, producer: P6PhoneReferenceProducerEvidence, reload_receipt: P6PhoneReferenceReloadReceipt) -> P6PhoneReferenceEvidenceBundle:
        unsigned = {
            "schema_version": P6_PHONE_REFERENCE_BUNDLE_SCHEMA_VERSION,
            "producer": producer.to_json_object(),
            "reload_receipt": reload_receipt.to_json_object(),
        }
        return cls(producer=producer, reload_receipt=reload_receipt, bundle_sha256=sha256_document(unsigned))

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "producer": self.producer.to_json_object(),
            "reload_receipt": self.reload_receipt.to_json_object(),
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "bundle_sha256": self.bundle_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> P6PhoneReferenceEvidenceBundle:
        document = require_mapping(value, "p6_phone_reference_bundle")
        require_exact_keys(document, {"schema_version", "producer", "reload_receipt", "bundle_sha256"}, "p6_phone_reference_bundle")
        return cls(
            schema_version=document["schema_version"],
            producer=P6PhoneReferenceProducerEvidence.from_json_object(document["producer"]),
            reload_receipt=P6PhoneReferenceReloadReceipt.from_json_object(document["reload_receipt"]),
            bundle_sha256=document["bundle_sha256"],
        )


__all__ = [
    "P6_PHONE_REFERENCE_BUNDLE_SCHEMA_VERSION",
    "P6_PHONE_REFERENCE_CAPTURE_CONTRACT_ID",
    "P6_PHONE_REFERENCE_CONTEXT_ID",
    "P6_PHONE_REFERENCE_CUE_ID",
    "P6_PHONE_REFERENCE_EXPECTED_PHONE",
    "P6_PHONE_REFERENCE_PRODUCER_SCHEMA_VERSION",
    "P6_PHONE_REFERENCE_PROTOCOL_REVISION",
    "P6_PHONE_REFERENCE_RELOAD_SCHEMA_VERSION",
    "P6PhoneReferenceCameraMetric",
    "P6PhoneReferenceEvidenceBundle",
    "P6PhoneReferenceMetrics",
    "P6PhoneReferenceProducerEvidence",
    "P6PhoneReferenceReloadReceipt",
    "P6PhoneReferenceThresholds",
    "P6RuntimeCueParityReceipt",
]
