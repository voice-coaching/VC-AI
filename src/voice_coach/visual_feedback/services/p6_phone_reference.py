"""P6B producer: calibrate one conservative visible-lip aperture reference."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import statistics
from typing import Protocol

from ..adapters.aihub68_geometry_dataset import (
    Aihub68AdmittedDataset,
    Aihub68GeometrySample,
)
from ..adapters.p6_mediapipe_geometry import (
    P6MediaPipeBatch,
    P6MediaPipeObservation,
)
from ..models.artifacts import (
    CalibrationRule,
    VisualCalibrationArtifact,
    VisualClaim,
    VisualClaimCatalog,
)
from ..models.enums import CalibrationScope, DeviationDirection
from ..models.integrity import sha256_document
from ..models.p6_geometry import P6GeometryEvidenceBundle
from ..models.p6_phone_reference import (
    P6_PHONE_REFERENCE_CAPTURE_CONTRACT_ID,
    P6_PHONE_REFERENCE_CONTEXT_ID,
    P6_PHONE_REFERENCE_CUE_ID,
    P6_PHONE_REFERENCE_EXPECTED_PHONE,
    P6PhoneReferenceCameraMetric,
    P6PhoneReferenceMetrics,
    P6PhoneReferenceProducerEvidence,
    P6PhoneReferenceThresholds,
    P6RuntimeCueParityReceipt,
)
from ..models.p6_geometry_schema import AIHUB68_KEYPOINT_NAMES
from ..models.runtime_eligibility import (
    RuntimeClaimBinding,
    RuntimeVisualClaimPolicy,
)


class P6PhoneReferenceInferencePort(Protocol):
    def infer(
        self, samples: tuple[Aihub68GeometrySample, ...]
    ) -> P6MediaPipeBatch: ...


@dataclass(frozen=True, slots=True)
class P6ProductionCueObservation:
    sample_sha256: str
    face_count: int
    aperture_ratio: float | None


@dataclass(frozen=True, slots=True)
class P6ProductionCueBatch:
    observations: tuple[P6ProductionCueObservation, ...]
    backend_revision: str
    worker_sha256: str
    adapter_sha256: str
    model_sha256: str


class P6ProductionCueParityPort(Protocol):
    def infer(
        self, samples: tuple[Aihub68GeometrySample, ...]
    ) -> P6ProductionCueBatch: ...


class P6PhoneReferenceCalibrationError(RuntimeError):
    """Aggregate-only fail-closed result."""

    def __init__(
        self,
        reason_codes: tuple[str, ...],
        *,
        metrics: P6PhoneReferenceMetrics | None = None,
        parity_receipt: P6RuntimeCueParityReceipt | None = None,
    ) -> None:
        self.reason_codes = reason_codes
        self.metrics = metrics
        self.parity_receipt = parity_receipt
        super().__init__(reason_codes[0])


@dataclass(frozen=True, slots=True)
class _FrozenReferenceSplit:
    development_actor_ids: frozenset[str]
    calibration_actor_ids: frozenset[str]
    development_samples: tuple[Aihub68GeometrySample, ...]
    calibration_samples: tuple[Aihub68GeometrySample, ...]
    full_development_partition_sha256: str
    full_calibration_partition_sha256: str
    selected_development_partition_sha256: str
    selected_calibration_partition_sha256: str
    actor_overlap_count: int
    attempt_overlap_count: int


class P6PhoneReferenceCalibrationService:
    """Create producer evidence only after every pre-frozen P6B gate passes."""

    def __init__(
        self,
        *,
        calibration_inference: P6PhoneReferenceInferencePort,
        production_parity: P6ProductionCueParityPort,
    ) -> None:
        self._calibration_inference = calibration_inference
        self._production_parity = production_parity

    def calibrate(
        self,
        *,
        dataset: Aihub68AdmittedDataset,
        geometry_bundle: P6GeometryEvidenceBundle,
        rights_confirmed: bool,
    ) -> P6PhoneReferenceProducerEvidence:
        if rights_confirmed is not True:
            raise P6PhoneReferenceCalibrationError(("p6b_rights_confirmation_required",))
        geometry = geometry_bundle.artifact
        geometry_bundle.reload_receipt.validate_artifact(geometry)
        calibration_worker_source = Path(__file__).parents[1] / (
            "adapters/p6_mediapipe_geometry_worker.py"
        )
        if _sha256_file(calibration_worker_source) != geometry.tracker_sha256:
            raise P6PhoneReferenceCalibrationError(
                ("p6b_calibration_worker_code_mismatch",)
            )
        if (
            dataset.dataset_manifest_sha256 != geometry.dataset_manifest_sha256
            or dataset.label_archive_sha256 != geometry.label_archive_sha256
            or dataset.image_archive_sha256 != geometry.image_archive_sha256
            or dataset.population_id_sha256 != geometry.population_id_sha256
        ):
            raise P6PhoneReferenceCalibrationError(("p6b_geometry_source_lineage_mismatch",))

        # Freeze every decision before either inference adapter is called.
        thresholds = P6PhoneReferenceThresholds()
        protocol_sha256 = thresholds.protocol_sha256
        split = _freeze_reference_split(dataset)
        if (
            split.full_development_partition_sha256
            != geometry.development_partition_sha256
            or split.full_calibration_partition_sha256
            != geometry.calibration_partition_sha256
            or len(split.development_actor_ids) != geometry.development_actor_count
            or len(split.calibration_actor_ids) != geometry.calibration_actor_count
        ):
            raise P6PhoneReferenceCalibrationError(("p6b_geometry_partition_mismatch",))
        selected = split.development_samples + split.calibration_samples
        _validate_frozen_selection(selected)
        pre_metric_freeze_sha256 = sha256_document(
            {
                "protocol_sha256": protocol_sha256,
                "geometry_bundle_sha256": geometry_bundle.bundle_sha256,
                "selected_development_partition_sha256": split.selected_development_partition_sha256,
                "selected_calibration_partition_sha256": split.selected_calibration_partition_sha256,
                "frozen_before_metric_access": True,
            }
        )
        if not pre_metric_freeze_sha256:
            raise AssertionError("unreachable pre-metric freeze digest")

        mapping_indices = _runtime_mapping_indices(geometry_bundle)
        calibration_batch = self._calibration_inference.infer(selected)
        if (
            calibration_batch.backend_revision != geometry.tracker_revision
            or calibration_batch.model_sha256 != geometry.model_sha256
        ):
            raise P6PhoneReferenceCalibrationError(("p6b_calibration_pipeline_mismatch",))
        calibration_observations = _observation_index(
            selected, calibration_batch.observations
        )
        calibration_cues = {
            sample.sample_sha256: _calibration_aperture(
                calibration_observations[sample.sample_sha256],
                calibration_batch=calibration_batch,
                mapping_indices=mapping_indices,
            )
            for sample in selected
        }

        production_batch = self._production_parity.infer(selected)
        if production_batch.model_sha256 != geometry.model_sha256:
            raise P6PhoneReferenceCalibrationError(("p6b_production_model_mismatch",))
        production_observations = _production_observation_index(
            selected, production_batch.observations
        )
        parity_receipt = _parity_receipt(
            selected=selected,
            calibration_cues=calibration_cues,
            production_observations=production_observations,
            calibration_batch=calibration_batch,
            production_batch=production_batch,
            mapping_indices=mapping_indices,
            geometry_worker_sha256=geometry.tracker_sha256,
        )

        development_values = tuple(
            value
            for sample in split.development_samples
            if (value := calibration_cues[sample.sample_sha256]) is not None
        )
        if not development_values:
            raise P6PhoneReferenceCalibrationError(("p6b_development_cues_missing",))
        center = statistics.median(development_values)
        mad = statistics.median(abs(value - center) for value in development_values)
        scale = max(1.4826 * mad, thresholds.minimum_robust_scale)
        q1 = _type7_quantile(development_values, 0.25)
        q3 = _type7_quantile(development_values, 0.75)
        iqr = q3 - q1
        support_minimum = max(0.0, q1 - 1.5 * iqr)
        support_maximum = min(1.0, q3 + 1.5 * iqr)
        if support_maximum <= support_minimum:
            raise P6PhoneReferenceCalibrationError(("p6b_development_support_degenerate",))

        metrics = _metrics(
            split=split,
            calibration_cues=calibration_cues,
            center=center,
            scale=scale,
            support_minimum=support_minimum,
            support_maximum=support_maximum,
        )
        failures = _threshold_failures(
            split=split,
            metrics=metrics,
            parity_receipt=parity_receipt,
            thresholds=thresholds,
        )
        if failures:
            raise P6PhoneReferenceCalibrationError(
                failures, metrics=metrics, parity_receipt=parity_receipt
            )

        artifact = _calibration_artifact(
            population_id_sha256=dataset.population_id_sha256,
            metrics=metrics,
            thresholds=thresholds,
        )
        catalog = _claim_catalog()
        policy = _claim_policy(catalog)
        service_sha256 = _sha256_file(Path(__file__))
        admission_adapter_sha256 = _sha256_file(
            Path(__file__).parents[1] / "adapters/aihub68_geometry_dataset.py"
        )
        return P6PhoneReferenceProducerEvidence.issue(
            source_revision=geometry.source_revision,
            dataset_manifest_sha256=dataset.dataset_manifest_sha256,
            label_archive_sha256=dataset.label_archive_sha256,
            image_archive_sha256=dataset.image_archive_sha256,
            population_id_sha256=dataset.population_id_sha256,
            geometry_bundle_sha256=geometry_bundle.bundle_sha256,
            geometry_artifact_sha256=geometry.artifact_sha256,
            geometry_mapping_sha256=geometry.mapping_sha256,
            admission_adapter_sha256=admission_adapter_sha256,
            reference_service_sha256=service_sha256,
            development_partition_sha256=split.full_development_partition_sha256,
            calibration_partition_sha256=split.full_calibration_partition_sha256,
            selected_development_partition_sha256=split.selected_development_partition_sha256,
            selected_calibration_partition_sha256=split.selected_calibration_partition_sha256,
            development_actor_count=len(split.development_actor_ids),
            calibration_actor_count=len(split.calibration_actor_ids),
            actor_overlap_count=split.actor_overlap_count,
            attempt_overlap_count=split.attempt_overlap_count,
            protocol_sha256=protocol_sha256,
            thresholds=thresholds,
            mapping_indices=mapping_indices,
            metrics=metrics,
            runtime_parity_receipt=parity_receipt,
            calibration_artifact=artifact,
            claim_catalog=catalog,
            claim_policy=policy,
        )


def validate_p6_phone_reference_producer(
    *,
    producer: P6PhoneReferenceProducerEvidence,
    geometry_bundle: P6GeometryEvidenceBundle,
    production_tracker_revision: str,
    production_tracker_sha256: str,
    landmark_model_sha256: str,
) -> None:
    """Strict integration hook for the automated P6 promotion builder."""

    geometry = geometry_bundle.artifact
    geometry_bundle.reload_receipt.validate_artifact(geometry)
    parity = producer.runtime_parity_receipt
    expected = {
        "geometry_bundle_sha256": geometry_bundle.bundle_sha256,
        "geometry_artifact_sha256": geometry.artifact_sha256,
        "geometry_mapping_sha256": geometry.mapping_sha256,
        "dataset_manifest_sha256": geometry.dataset_manifest_sha256,
        "label_archive_sha256": geometry.label_archive_sha256,
        "image_archive_sha256": geometry.image_archive_sha256,
        "population_id_sha256": geometry.population_id_sha256,
        "development_partition_sha256": geometry.development_partition_sha256,
        "calibration_partition_sha256": geometry.calibration_partition_sha256,
    }
    if any(getattr(producer, name) != value for name, value in expected.items()):
        raise P6PhoneReferenceCalibrationError(("p6b_geometry_cross_digest_mismatch",))
    if producer.reference_service_sha256 != _sha256_file(Path(__file__)):
        raise P6PhoneReferenceCalibrationError(("p6b_reference_service_code_mismatch",))
    if producer.admission_adapter_sha256 != _sha256_file(
        Path(__file__).parents[1] / "adapters/aihub68_geometry_dataset.py"
    ):
        raise P6PhoneReferenceCalibrationError(("p6b_admission_adapter_code_mismatch",))
    if (
        parity.production_worker_revision != production_tracker_revision
        or parity.production_worker_sha256 != production_tracker_sha256
        or parity.model_sha256 != landmark_model_sha256
        or geometry.model_sha256 != landmark_model_sha256
    ):
        raise P6PhoneReferenceCalibrationError(("p6b_production_identity_mismatch",))


def _freeze_reference_split(dataset: Aihub68AdmittedDataset) -> _FrozenReferenceSplit:
    actor_ids = {sample.actor_id for sample in dataset.samples}
    ranked = sorted(
        actor_ids,
        key=lambda actor_id: hashlib.sha256(
            b"voice_coach.p6.actor_split.v1\x00" + actor_id.encode("utf-8")
        ).hexdigest(),
    )
    if len(ranked) != 17:
        raise P6PhoneReferenceCalibrationError(("p6b_actor_count_differs",))
    calibration_actor_ids = frozenset(ranked[:5])
    development_actor_ids = frozenset(ranked[5:])
    full_development = tuple(
        sample for sample in dataset.samples if sample.actor_id in development_actor_ids
    )
    full_calibration = tuple(
        sample for sample in dataset.samples if sample.actor_id in calibration_actor_ids
    )
    development = tuple(
        sample
        for sample in full_development
        if sample.word_id == 9 and sample.frame_index == 10
    )
    calibration = tuple(
        sample
        for sample in full_calibration
        if sample.word_id == 9 and sample.frame_index == 10
    )
    development_attempts = {sample.attempt_id for sample in development}
    calibration_attempts = {sample.attempt_id for sample in calibration}
    return _FrozenReferenceSplit(
        development_actor_ids=development_actor_ids,
        calibration_actor_ids=calibration_actor_ids,
        development_samples=development,
        calibration_samples=calibration,
        full_development_partition_sha256=_partition_sha256(
            development_actor_ids, full_development
        ),
        full_calibration_partition_sha256=_partition_sha256(
            calibration_actor_ids, full_calibration
        ),
        selected_development_partition_sha256=_partition_sha256(
            development_actor_ids, development
        ),
        selected_calibration_partition_sha256=_partition_sha256(
            calibration_actor_ids, calibration
        ),
        actor_overlap_count=len(development_actor_ids & calibration_actor_ids),
        attempt_overlap_count=len(development_attempts & calibration_attempts),
    )


def _partition_sha256(
    actor_ids: frozenset[str], samples: tuple[Aihub68GeometrySample, ...]
) -> str:
    return sha256_document(
        {
            "assignment_unit": "actor_id",
            "actor_sha256": sorted(
                hashlib.sha256(
                    b"voice_coach.p6.partition_actor.v1\x00" + actor_id.encode("utf-8")
                ).hexdigest()
                for actor_id in actor_ids
            ),
            "sample_sha256": sorted(sample.sample_sha256 for sample in samples),
        }
    )


def _validate_frozen_selection(
    samples: tuple[Aihub68GeometrySample, ...]
) -> None:
    if len(samples) != 85:
        raise P6PhoneReferenceCalibrationError(("p6b_selected_sample_count_differs",))
    keys = {(sample.actor_id, sample.camera_id) for sample in samples}
    if (
        len(keys) != 85
        or len({sample.actor_id for sample in samples}) != 17
        or len({sample.camera_id for sample in samples}) != 5
        or any(
            sample.word_id != 9
            or sample.word != "어"
            or sample.frame_index != 10
            for sample in samples
        )
    ):
        raise P6PhoneReferenceCalibrationError(("p6b_frozen_selection_differs",))


def _runtime_mapping_indices(
    geometry_bundle: P6GeometryEvidenceBundle,
) -> tuple[int, int, int, int]:
    mapping = geometry_bundle.artifact.mapping_mediapipe_indices
    indices = tuple(
        mapping[AIHUB68_KEYPOINT_NAMES.index(name)]
        for name in (
            "outer_0_left",
            "outer_6_right",
            "inner_3_top",
            "inner_9_bottom",
        )
    )
    if indices != (61, 291, 13, 14):
        raise P6PhoneReferenceCalibrationError(("p6b_runtime_mapping_not_supported",))
    return indices  # type: ignore[return-value]


def _observation_index(
    samples: tuple[Aihub68GeometrySample, ...],
    observations: tuple[P6MediaPipeObservation, ...],
) -> dict[str, P6MediaPipeObservation]:
    result = {item.sample_sha256: item for item in observations}
    if len(result) != len(samples) or set(result) != {
        sample.sample_sha256 for sample in samples
    }:
        raise P6PhoneReferenceCalibrationError(("p6b_calibration_lineage_mismatch",))
    return result


def _production_observation_index(
    samples: tuple[Aihub68GeometrySample, ...],
    observations: tuple[P6ProductionCueObservation, ...],
) -> dict[str, P6ProductionCueObservation]:
    result = {item.sample_sha256: item for item in observations}
    if len(result) != len(samples) or set(result) != {
        sample.sample_sha256 for sample in samples
    }:
        raise P6PhoneReferenceCalibrationError(("p6b_production_lineage_mismatch",))
    return result


def _calibration_aperture(
    observation: P6MediaPipeObservation,
    *,
    calibration_batch: P6MediaPipeBatch,
    mapping_indices: tuple[int, int, int, int],
) -> float | None:
    if observation.face_count != 1 or observation.candidate_points_xy is None:
        return None
    by_index = dict(
        zip(calibration_batch.candidate_indices, observation.candidate_points_xy, strict=True)
    )
    left, right, top, bottom = (by_index[index] for index in mapping_indices)
    width = abs(right[0] - left[0])
    if width <= 0:
        raise P6PhoneReferenceCalibrationError(("p6b_lip_width_degenerate",))
    value = abs(bottom[1] - top[1]) / width
    if not math.isfinite(value):
        raise P6PhoneReferenceCalibrationError(("p6b_cue_not_finite",))
    return value


def _parity_receipt(
    *,
    selected: tuple[Aihub68GeometrySample, ...],
    calibration_cues: dict[str, float | None],
    production_observations: dict[str, P6ProductionCueObservation],
    calibration_batch: P6MediaPipeBatch,
    production_batch: P6ProductionCueBatch,
    mapping_indices: tuple[int, int, int, int],
    geometry_worker_sha256: str,
) -> P6RuntimeCueParityReceipt:
    differences: list[float] = []
    value_rows: list[dict[str, object]] = []
    for sample in selected:
        calibration = calibration_cues[sample.sample_sha256]
        production = production_observations[sample.sample_sha256].aperture_ratio
        if calibration is None or production is None:
            continue
        difference = abs(calibration - production)
        differences.append(difference)
        value_rows.append(
            {
                "sample_sha256": sample.sample_sha256,
                "calibration_cue": calibration,
                "production_cue": production,
                "absolute_difference": difference,
            }
        )
    calibration_detected = sum(value is not None for value in calibration_cues.values())
    production_detected = sum(
        item.aperture_ratio is not None for item in production_observations.values()
    )
    return P6RuntimeCueParityReceipt.issue(
        selected_sample_count=len(selected),
        calibration_detected_count=calibration_detected,
        production_detected_count=production_detected,
        paired_cue_count=len(differences),
        parity_coverage=len(differences) / len(selected),
        maximum_absolute_cue_difference=max(differences, default=math.inf),
        mean_absolute_cue_difference=(
            sum(differences) / len(differences) if differences else math.inf
        ),
        mapping_indices=mapping_indices,
        calibration_worker_revision=calibration_batch.backend_revision,
        calibration_worker_sha256=geometry_worker_sha256,
        production_worker_revision=production_batch.backend_revision,
        production_worker_sha256=production_batch.worker_sha256,
        production_adapter_sha256=production_batch.adapter_sha256,
        model_sha256=production_batch.model_sha256,
        parity_values_sha256=sha256_document(
            {
                "schema_version": "voice_coach.p6_runtime_parity_values.v1",
                "ordered_rows": value_rows,
            }
        ),
    )


def _type7_quantile(values: tuple[float, ...], probability: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    fraction = position - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def _metrics(
    *,
    split: _FrozenReferenceSplit,
    calibration_cues: dict[str, float | None],
    center: float,
    scale: float,
    support_minimum: float,
    support_maximum: float,
) -> P6PhoneReferenceMetrics:
    development_detected = sum(
        calibration_cues[item.sample_sha256] is not None
        for item in split.development_samples
    )
    calibration_detected = sum(
        calibration_cues[item.sample_sha256] is not None
        for item in split.calibration_samples
    )
    calibration_supported = sum(
        value is not None and support_minimum <= value <= support_maximum
        for sample in split.calibration_samples
        for value in (calibration_cues[sample.sample_sha256],)
    )
    camera_metrics: list[P6PhoneReferenceCameraMetric] = []
    for camera_id in sorted({sample.camera_id for sample in split.calibration_samples}):
        samples = tuple(
            sample for sample in split.calibration_samples if sample.camera_id == camera_id
        )
        values = tuple(calibration_cues[sample.sample_sha256] for sample in samples)
        detected = sum(value is not None for value in values)
        supported = sum(
            value is not None and support_minimum <= value <= support_maximum
            for value in values
        )
        camera_metrics.append(
            P6PhoneReferenceCameraMetric(
                camera_id=camera_id,
                sample_count=len(samples),
                detected_count=detected,
                supported_count=supported,
                support_coverage=supported / len(samples),
            )
        )
    selected_count = len(split.development_samples) + len(split.calibration_samples)
    return P6PhoneReferenceMetrics(
        selected_sample_count=selected_count,
        development_sample_count=len(split.development_samples),
        development_detected_count=development_detected,
        calibration_sample_count=len(split.calibration_samples),
        calibration_detected_count=calibration_detected,
        single_face_coverage=(development_detected + calibration_detected) / selected_count,
        reference_center=center,
        reference_scale=scale,
        support_minimum=support_minimum,
        support_maximum=support_maximum,
        calibration_supported_count=calibration_supported,
        calibration_support_coverage=calibration_supported / len(split.calibration_samples),
        camera_metrics=tuple(camera_metrics),
    )


def _threshold_failures(
    *,
    split: _FrozenReferenceSplit,
    metrics: P6PhoneReferenceMetrics,
    parity_receipt: P6RuntimeCueParityReceipt,
    thresholds: P6PhoneReferenceThresholds,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if metrics.development_sample_count < thresholds.minimum_development_sample_count:
        reasons.append("p6b_development_count_below_threshold")
    if metrics.calibration_sample_count < thresholds.minimum_calibration_sample_count:
        reasons.append("p6b_calibration_count_below_threshold")
    if split.actor_overlap_count != 0:
        reasons.append("p6b_actor_overlap_detected")
    if split.attempt_overlap_count != 0:
        reasons.append("p6b_attempt_overlap_detected")
    if len(metrics.camera_metrics) != thresholds.required_camera_count:
        reasons.append("p6b_camera_count_differs")
    if metrics.single_face_coverage < thresholds.minimum_single_face_coverage:
        reasons.append("p6b_single_face_coverage_below_threshold")
    if metrics.calibration_support_coverage < thresholds.minimum_overall_calibration_support_coverage:
        reasons.append("p6b_support_coverage_below_threshold")
    if any(
        item.support_coverage < thresholds.minimum_camera_calibration_support_coverage
        for item in metrics.camera_metrics
    ):
        reasons.append("p6b_camera_support_coverage_below_threshold")
    if parity_receipt.parity_coverage < thresholds.minimum_runtime_parity_coverage:
        reasons.append("p6b_runtime_parity_coverage_below_threshold")
    if parity_receipt.maximum_absolute_cue_difference > thresholds.maximum_runtime_parity_absolute_difference:
        reasons.append("p6b_runtime_parity_difference_above_threshold")
    return tuple(reasons)


def _calibration_artifact(
    *,
    population_id_sha256: str,
    metrics: P6PhoneReferenceMetrics,
    thresholds: P6PhoneReferenceThresholds,
) -> VisualCalibrationArtifact:
    return VisualCalibrationArtifact.issue(
        artifact_id="aihub68_eo_visible_lip_aperture_v2",
        revision="aihub68_word9_frame10_actor_disjoint_v2",
        scope=CalibrationScope.CALIBRATED,
        capture_contract_id=P6_PHONE_REFERENCE_CAPTURE_CONTRACT_ID,
        population_id_sha256=population_id_sha256,
        rules=(
            CalibrationRule(
                cue_id=P6_PHONE_REFERENCE_CUE_ID,
                measurement_unit="ratio",
                expected_phones=(P6_PHONE_REFERENCE_EXPECTED_PHONE,),
                context_id=P6_PHONE_REFERENCE_CONTEXT_ID,
                reference_center=metrics.reference_center,
                reference_scale=metrics.reference_scale,
                support_minimum=metrics.support_minimum,
                support_maximum=metrics.support_maximum,
                feedback_minimum_absolute_deviation=(
                    thresholds.feedback_trigger_standardized_deviation
                ),
            ),
        ),
    )


def _claim_catalog() -> VisualClaimCatalog:
    return VisualClaimCatalog.issue(
        catalog_id="aihub68_visible_lip_opening_claims_v2",
        revision="visible_only_two_direction_v2",
        scope=CalibrationScope.CALIBRATED,
        claims=(
            VisualClaim(
                action_id="reduce_visible_lip_opening",
                approved_claim_id="visible_lip_opening_above_reference_v1",
                renderer_key="visual_lip_opening_reduce",
                cue_id=P6_PHONE_REFERENCE_CUE_ID,
                expected_phones=(P6_PHONE_REFERENCE_EXPECTED_PHONE,),
                context_id=P6_PHONE_REFERENCE_CONTEXT_ID,
                direction=DeviationDirection.ABOVE_REFERENCE,
            ),
            VisualClaim(
                action_id="increase_visible_lip_opening",
                approved_claim_id="visible_lip_opening_below_reference_v1",
                renderer_key="visual_lip_opening_increase",
                cue_id=P6_PHONE_REFERENCE_CUE_ID,
                expected_phones=(P6_PHONE_REFERENCE_EXPECTED_PHONE,),
                context_id=P6_PHONE_REFERENCE_CONTEXT_ID,
                direction=DeviationDirection.BELOW_REFERENCE,
            ),
        ),
    )


def _claim_policy(catalog: VisualClaimCatalog) -> RuntimeVisualClaimPolicy:
    bindings = tuple(
        RuntimeClaimBinding(
            approved_claim_id=claim.approved_claim_id,
            action_id=claim.action_id,
            renderer_key=claim.renderer_key,
            cue_id=claim.cue_id,
        )
        for claim in catalog.claims
    )
    unsigned = {
        "schema_version": "voice_coach.visual_claim_policy.v1",
        "policy_revision": "aihub68_visible_lip_opening_exact_allowlist_v2",
        "allowed_cue_ids": [P6_PHONE_REFERENCE_CUE_ID],
        "allowed_renderer_keys": [
            "visual_lip_opening_reduce",
            "visual_lip_opening_increase",
        ],
        "allowed_claim_bindings": [item.to_json_object() for item in bindings],
        "forbidden_identifier_tokens": sorted(
            {
                "airflow",
                "anatomy",
                "correctness",
                "diagnosis",
                "free_prose",
                "glottis",
                "medical",
                "tongue",
                "velum",
            }
        ),
    }
    return RuntimeVisualClaimPolicy.from_json_object(
        {**unsigned, "policy_sha256": sha256_document(unsigned)}
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "P6PhoneReferenceCalibrationError",
    "P6PhoneReferenceCalibrationService",
    "P6ProductionCueBatch",
    "P6ProductionCueObservation",
    "validate_p6_phone_reference_producer",
]
