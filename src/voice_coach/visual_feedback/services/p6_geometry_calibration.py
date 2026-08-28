"""P6 use case: freeze split/thresholds, learn mapping, evaluate calibration."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
from typing import Protocol

from ..adapters.aihub68_geometry_dataset import (
    Aihub68AdmittedDataset,
    Aihub68GeometrySample,
)
from ..adapters.p6_mediapipe_geometry import (
    P6MediaPipeBatch,
    P6MediaPipeObservation,
)
from ..models.integrity import sha256_document
from ..models.p6_geometry import (
    P6GeometryArtifact,
    P6GeometryMetricSummary,
    P6GeometrySliceMetric,
    P6GeometryThresholds,
    p6_mapping_parity_probe_sha256,
)
from ..models.p6_geometry_schema import (
    AIHUB68_KEYPOINT_NAMES,
    MEDIAPIPE_LIP_CANDIDATE_INDICES,
)


class P6GeometryInferencePort(Protocol):
    """Narrow calibration-only tracker boundary."""

    def infer(
        self, samples: tuple[Aihub68GeometrySample, ...]
    ) -> P6MediaPipeBatch: ...


class P6GeometryCalibrationError(RuntimeError):
    """Fail-closed aggregate result without restricted sample details."""

    def __init__(
        self,
        reason_codes: tuple[str, ...],
        *,
        metric_summary: P6GeometryMetricSummary | None = None,
    ) -> None:
        self.reason_codes = reason_codes
        self.metric_summary = metric_summary
        super().__init__(reason_codes[0])


@dataclass(frozen=True, slots=True)
class _FrozenSplit:
    development_samples: tuple[Aihub68GeometrySample, ...]
    calibration_samples: tuple[Aihub68GeometrySample, ...]
    development_partition_sha256: str
    calibration_partition_sha256: str
    development_actor_count: int
    calibration_actor_count: int
    actor_overlap_count: int
    attempt_overlap_count: int


class P6GeometryCalibrationService:
    """Produce an artifact only when every pre-frozen threshold passes."""

    def __init__(self, inference_adapter: P6GeometryInferencePort) -> None:
        self._inference_adapter = inference_adapter

    def calibrate(
        self,
        dataset: Aihub68AdmittedDataset,
        *,
        rights_confirmed: bool,
    ) -> P6GeometryArtifact:
        """Run one complete actor-disjoint P6 geometry calibration."""

        if rights_confirmed is not True:
            raise P6GeometryCalibrationError(("p6_rights_confirmation_required",))

        # These immutable objects and their digests exist before the adapter is
        # called.  The split cannot therefore be changed after seeing metrics.
        thresholds = P6GeometryThresholds()
        threshold_protocol_sha256 = thresholds.threshold_protocol_sha256
        frozen_split = _freeze_actor_split(dataset)
        pre_metric_freeze_sha256 = sha256_document(
            {
                "threshold_protocol_sha256": threshold_protocol_sha256,
                "development_partition_sha256": (
                    frozen_split.development_partition_sha256
                ),
                "calibration_partition_sha256": (
                    frozen_split.calibration_partition_sha256
                ),
                "frozen_before_metric_access": True,
            }
        )
        if not pre_metric_freeze_sha256:
            raise AssertionError("unreachable pre-metric freeze digest")

        ordered_samples = (
            frozen_split.development_samples + frozen_split.calibration_samples
        )
        inference = self._inference_adapter.infer(ordered_samples)
        observation_by_digest = {
            observation.sample_sha256: observation
            for observation in inference.observations
        }
        if len(observation_by_digest) != len(ordered_samples) or set(
            observation_by_digest
        ) != {sample.sample_sha256 for sample in ordered_samples}:
            raise P6GeometryCalibrationError(("p6_inference_lineage_mismatch",))

        development_pairs = _detected_pairs(
            frozen_split.development_samples, observation_by_digest
        )
        if not development_pairs:
            raise P6GeometryCalibrationError(("p6_development_faces_missing",))
        mapping_offsets = _derive_mapping_offsets(development_pairs)
        mapping_indices = tuple(
            MEDIAPIPE_LIP_CANDIDATE_INDICES[offset] for offset in mapping_offsets
        )
        metric_summary = _measure_metrics(
            frozen_split=frozen_split,
            observations=observation_by_digest,
            mapping_offsets=mapping_offsets,
        )
        reason_codes = _threshold_failures(
            frozen_split=frozen_split,
            metrics=metric_summary,
            thresholds=thresholds,
            camera_ids=dataset.camera_ids,
        )
        if reason_codes:
            raise P6GeometryCalibrationError(
                reason_codes,
                metric_summary=metric_summary,
            )

        admission_source = Path(
            __file__
        ).parents[1] / "adapters/aihub68_geometry_dataset.py"
        worker_source = Path(
            __file__
        ).parents[1] / "adapters/p6_mediapipe_geometry_worker.py"
        return P6GeometryArtifact.issue(
            source_revision=(
                "aihub68_vl_0828_"
                f"{dataset.label_archive_sha256[:12]}_"
                f"{dataset.image_archive_sha256[:12]}"
            ),
            dataset_manifest_sha256=dataset.dataset_manifest_sha256,
            population_id_sha256=dataset.population_id_sha256,
            label_archive_sha256=dataset.label_archive_sha256,
            image_archive_sha256=dataset.image_archive_sha256,
            development_partition_sha256=(
                frozen_split.development_partition_sha256
            ),
            calibration_partition_sha256=(
                frozen_split.calibration_partition_sha256
            ),
            development_actor_count=frozen_split.development_actor_count,
            calibration_actor_count=frozen_split.calibration_actor_count,
            actor_overlap_count=frozen_split.actor_overlap_count,
            attempt_overlap_count=frozen_split.attempt_overlap_count,
            tracker_revision=inference.backend_revision,
            tracker_sha256=_sha256_file(worker_source),
            model_sha256=inference.model_sha256,
            admission_adapter_sha256=_sha256_file(admission_source),
            calibration_service_sha256=_sha256_file(Path(__file__)),
            mapping_mediapipe_indices=mapping_indices,
            mapping_sha256=sha256_document(
                {
                    "gold_keypoint_names": list(AIHUB68_KEYPOINT_NAMES),
                    "mediapipe_indices": list(mapping_indices),
                    "candidate_set": list(MEDIAPIPE_LIP_CANDIDATE_INDICES),
                    "derivation": (
                        "development_only_rectangular_hungarian_mean_nle_v1"
                    ),
                }
            ),
            metrics=metric_summary,
            thresholds=thresholds,
            threshold_protocol_sha256=threshold_protocol_sha256,
            thresholds_frozen_before_metric_access=True,
            split_frozen_before_metric_access=True,
            gate_status="passed",
            reason_codes=(),
            parity_probe_sha256=p6_mapping_parity_probe_sha256(mapping_indices),
        )


def _freeze_actor_split(dataset: Aihub68AdmittedDataset) -> _FrozenSplit:
    actor_ids = {sample.actor_id for sample in dataset.samples}
    ranked_actors = sorted(
        actor_ids,
        key=lambda actor_id: hashlib.sha256(
            b"voice_coach.p6.actor_split.v1\x00" + actor_id.encode("utf-8")
        ).hexdigest(),
    )
    if len(ranked_actors) < 6:
        raise P6GeometryCalibrationError(("p6_actor_count_insufficient",))
    calibration_actor_ids = frozenset(ranked_actors[:5])
    development_actor_ids = frozenset(ranked_actors[5:])
    development_samples = tuple(
        sample for sample in dataset.samples if sample.actor_id in development_actor_ids
    )
    calibration_samples = tuple(
        sample for sample in dataset.samples if sample.actor_id in calibration_actor_ids
    )
    development_attempts = {sample.attempt_id for sample in development_samples}
    calibration_attempts = {sample.attempt_id for sample in calibration_samples}
    return _FrozenSplit(
        development_samples=development_samples,
        calibration_samples=calibration_samples,
        development_partition_sha256=_partition_sha256(
            development_actor_ids, development_samples
        ),
        calibration_partition_sha256=_partition_sha256(
            calibration_actor_ids, calibration_samples
        ),
        development_actor_count=len(development_actor_ids),
        calibration_actor_count=len(calibration_actor_ids),
        actor_overlap_count=len(development_actor_ids & calibration_actor_ids),
        attempt_overlap_count=len(development_attempts & calibration_attempts),
    )


def _partition_sha256(
    actor_ids: frozenset[str], samples: tuple[Aihub68GeometrySample, ...]
) -> str:
    return sha256_document(
        {
            "assignment_unit": "actor_id",
            "actor_sha256": sorted(_actor_sha256(value) for value in actor_ids),
            "sample_sha256": sorted(sample.sample_sha256 for sample in samples),
        }
    )


def _actor_sha256(actor_id: str) -> str:
    return hashlib.sha256(
        b"voice_coach.p6.partition_actor.v1\x00" + actor_id.encode("utf-8")
    ).hexdigest()


def _detected_pairs(
    samples: tuple[Aihub68GeometrySample, ...],
    observations: dict[str, P6MediaPipeObservation],
) -> tuple[tuple[Aihub68GeometrySample, P6MediaPipeObservation], ...]:
    return tuple(
        (sample, observations[sample.sample_sha256])
        for sample in samples
        if observations[sample.sample_sha256].face_count == 1
        and observations[sample.sample_sha256].candidate_points_xy is not None
    )


def _derive_mapping_offsets(
    development_pairs: tuple[
        tuple[Aihub68GeometrySample, P6MediaPipeObservation], ...
    ],
) -> tuple[int, ...]:
    costs: list[list[float]] = []
    for gold_index in range(24):
        row: list[float] = []
        for candidate_offset in range(len(MEDIAPIPE_LIP_CANDIDATE_INDICES)):
            row.append(
                sum(
                    _point_error(
                        sample=sample,
                        gold_point=sample.points_xy[gold_index],
                        predicted_point=observation.candidate_points_xy[
                            candidate_offset
                        ],
                    )
                    for sample, observation in development_pairs
                    if observation.candidate_points_xy is not None
                )
                / len(development_pairs)
            )
        costs.append(row)
    return _rectangular_hungarian(costs)


def _rectangular_hungarian(costs: list[list[float]]) -> tuple[int, ...]:
    """Deterministic minimum-cost assignment for rows <= columns."""

    row_count = len(costs)
    column_count = len(costs[0])
    if row_count > column_count or any(len(row) != column_count for row in costs):
        raise P6GeometryCalibrationError(("p6_mapping_cost_shape_invalid",))
    potential_rows = [0.0] * (row_count + 1)
    potential_columns = [0.0] * (column_count + 1)
    matched_row = [0] * (column_count + 1)
    predecessor = [0] * (column_count + 1)
    for row_index in range(1, row_count + 1):
        matched_row[0] = row_index
        minimum = [math.inf] * (column_count + 1)
        used = [False] * (column_count + 1)
        column = 0
        while True:
            used[column] = True
            active_row = matched_row[column]
            delta = math.inf
            next_column = 0
            for candidate_column in range(1, column_count + 1):
                if used[candidate_column]:
                    continue
                current = (
                    costs[active_row - 1][candidate_column - 1]
                    - potential_rows[active_row]
                    - potential_columns[candidate_column]
                )
                if current < minimum[candidate_column]:
                    minimum[candidate_column] = current
                    predecessor[candidate_column] = column
                if minimum[candidate_column] < delta:
                    delta = minimum[candidate_column]
                    next_column = candidate_column
            for candidate_column in range(column_count + 1):
                if used[candidate_column]:
                    potential_rows[matched_row[candidate_column]] += delta
                    potential_columns[candidate_column] -= delta
                else:
                    minimum[candidate_column] -= delta
            column = next_column
            if matched_row[column] == 0:
                break
        while True:
            previous_column = predecessor[column]
            matched_row[column] = matched_row[previous_column]
            column = previous_column
            if column == 0:
                break
    assignment = [-1] * row_count
    for column in range(1, column_count + 1):
        if matched_row[column] != 0:
            assignment[matched_row[column] - 1] = column - 1
    if any(value < 0 for value in assignment):
        raise P6GeometryCalibrationError(("p6_mapping_assignment_failed",))
    return tuple(assignment)


def _measure_metrics(
    *,
    frozen_split: _FrozenSplit,
    observations: dict[str, P6MediaPipeObservation],
    mapping_offsets: tuple[int, ...],
) -> P6GeometryMetricSummary:
    development_errors = _errors(
        frozen_split.development_samples, observations, mapping_offsets
    )
    calibration_errors = _errors(
        frozen_split.calibration_samples, observations, mapping_offsets
    )
    if not development_errors or not calibration_errors:
        raise P6GeometryCalibrationError(("p6_detected_samples_insufficient",))
    slices: list[P6GeometrySliceMetric] = []
    for dimension, labels in (
        ("camera", sorted({str(sample.camera_id) for sample in frozen_split.calibration_samples})),
        ("sex", sorted({sample.sex for sample in frozen_split.calibration_samples})),
        ("age", sorted({sample.age_band for sample in frozen_split.calibration_samples})),
    ):
        for label in labels:
            selected = tuple(
                sample
                for sample in frozen_split.calibration_samples
                if _slice_label(sample, dimension) == label
            )
            selected_errors = _errors(selected, observations, mapping_offsets)
            slices.append(
                P6GeometrySliceMetric(
                    dimension=dimension,
                    label=(f"camera_{label}" if dimension == "camera" else label),
                    sample_count=len(selected),
                    detected_count=len(selected_errors),
                    coverage_fraction=len(selected_errors) / len(selected),
                    normalized_landmark_error=(
                        sum(selected_errors) / len(selected_errors)
                        if selected_errors
                        else None
                    ),
                )
            )
    repeatability_group_count, repeatability_rmse = _repeatability(
        frozen_split.calibration_samples,
        observations,
        mapping_offsets,
    )
    sorted_calibration_errors = sorted(calibration_errors)
    p95_index = max(0, math.ceil(0.95 * len(sorted_calibration_errors)) - 1)
    return P6GeometryMetricSummary(
        development_sample_count=len(frozen_split.development_samples),
        development_detected_count=len(development_errors),
        development_coverage_fraction=(
            len(development_errors) / len(frozen_split.development_samples)
        ),
        development_normalized_landmark_error=(
            sum(development_errors) / len(development_errors)
        ),
        calibration_sample_count=len(frozen_split.calibration_samples),
        calibration_detected_count=len(calibration_errors),
        calibration_coverage_fraction=(
            len(calibration_errors) / len(frozen_split.calibration_samples)
        ),
        calibration_normalized_landmark_error=(
            sum(calibration_errors) / len(calibration_errors)
        ),
        calibration_nle_p95=sorted_calibration_errors[p95_index],
        repeatability_group_count=repeatability_group_count,
        repeatability_normalized_cue_rmse=repeatability_rmse,
        slices=tuple(slices),
    )


def _errors(
    samples: tuple[Aihub68GeometrySample, ...],
    observations: dict[str, P6MediaPipeObservation],
    mapping_offsets: tuple[int, ...],
) -> tuple[float, ...]:
    values: list[float] = []
    for sample in samples:
        observation = observations[sample.sample_sha256]
        if observation.face_count != 1 or observation.candidate_points_xy is None:
            continue
        values.append(
            sum(
                _point_error(
                    sample=sample,
                    gold_point=gold_point,
                    predicted_point=observation.candidate_points_xy[mapping_offset],
                )
                for gold_point, mapping_offset in zip(
                    sample.points_xy, mapping_offsets, strict=True
                )
            )
            / 24
        )
    return tuple(values)


def _point_error(
    *,
    sample: Aihub68GeometrySample,
    gold_point: tuple[float, float],
    predicted_point: tuple[float, float],
) -> float:
    outer_left = sample.points_xy[0]
    outer_right = sample.points_xy[6]
    scale = math.hypot(
        (outer_right[0] - outer_left[0]) * sample.width,
        (outer_right[1] - outer_left[1]) * sample.height,
    )
    if scale <= 0:
        raise P6GeometryCalibrationError(("p6_gold_lip_width_degenerate",))
    return math.hypot(
        (predicted_point[0] - gold_point[0]) * sample.width,
        (predicted_point[1] - gold_point[1]) * sample.height,
    ) / scale


def _repeatability(
    samples: tuple[Aihub68GeometrySample, ...],
    observations: dict[str, P6MediaPipeObservation],
    mapping_offsets: tuple[int, ...],
) -> tuple[int, float]:
    residuals_by_group: defaultdict[tuple[str, int, int], list[float]] = defaultdict(list)
    for sample in samples:
        observation = observations[sample.sample_sha256]
        if observation.face_count != 1 or observation.candidate_points_xy is None:
            continue
        predicted = tuple(
            observation.candidate_points_xy[offset] for offset in mapping_offsets
        )
        gold_aperture = _aperture_ratio(sample.points_xy, sample.width, sample.height)
        predicted_aperture = _aperture_ratio(predicted, sample.width, sample.height)
        residuals_by_group[(sample.actor_id, sample.word_id, sample.frame_index)].append(
            predicted_aperture - gold_aperture
        )
    squared_centered: list[float] = []
    group_count = 0
    for residuals in residuals_by_group.values():
        if len(residuals) < 2:
            continue
        group_count += 1
        center = sum(residuals) / len(residuals)
        squared_centered.extend((value - center) ** 2 for value in residuals)
    if not squared_centered:
        raise P6GeometryCalibrationError(("p6_repeatability_groups_missing",))
    return group_count, math.sqrt(sum(squared_centered) / len(squared_centered))


def _aperture_ratio(
    points: tuple[tuple[float, float], ...], width: int, height: int
) -> float:
    lip_width = math.hypot(
        (points[6][0] - points[0][0]) * width,
        (points[6][1] - points[0][1]) * height,
    )
    aperture = math.hypot(
        (points[21][0] - points[15][0]) * width,
        (points[21][1] - points[15][1]) * height,
    )
    if lip_width <= 0:
        raise P6GeometryCalibrationError(("p6_lip_width_degenerate",))
    return aperture / lip_width


def _slice_label(sample: Aihub68GeometrySample, dimension: str) -> str:
    if dimension == "camera":
        return str(sample.camera_id)
    if dimension == "sex":
        return sample.sex
    return sample.age_band


def _threshold_failures(
    *,
    frozen_split: _FrozenSplit,
    metrics: P6GeometryMetricSummary,
    thresholds: P6GeometryThresholds,
    camera_ids: tuple[int, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if frozen_split.calibration_actor_count < thresholds.minimum_calibration_actor_count:
        reasons.append("p6_calibration_actor_count_below_threshold")
    if (
        metrics.calibration_detected_count
        < thresholds.minimum_evaluated_calibration_frame_count
    ):
        reasons.append("p6_evaluated_frame_count_below_threshold")
    if metrics.calibration_coverage_fraction < thresholds.minimum_overall_coverage:
        reasons.append("p6_overall_coverage_below_threshold")
    if metrics.calibration_normalized_landmark_error > thresholds.maximum_overall_nle:
        reasons.append("p6_overall_nle_above_threshold")
    camera_slices = tuple(
        item for item in metrics.slices if item.dimension == "camera"
    )
    if len(camera_ids) != thresholds.required_camera_count or len(
        camera_slices
    ) != thresholds.required_camera_count:
        reasons.append("p6_required_camera_set_missing")
    if any(
        item.coverage_fraction < thresholds.minimum_camera_coverage
        for item in camera_slices
    ):
        reasons.append("p6_camera_coverage_below_threshold")
    if any(
        item.normalized_landmark_error is None
        or item.normalized_landmark_error > thresholds.maximum_camera_nle
        for item in camera_slices
    ):
        reasons.append("p6_camera_nle_above_threshold")
    if (
        metrics.repeatability_normalized_cue_rmse
        > thresholds.maximum_repeatability_normalized_cue_rmse
    ):
        reasons.append("p6_repeatability_rmse_above_threshold")
    if frozen_split.actor_overlap_count != 0:
        reasons.append("p6_actor_overlap_detected")
    if frozen_split.attempt_overlap_count != 0:
        reasons.append("p6_attempt_overlap_detected")
    return tuple(reasons)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "P6GeometryCalibrationError",
    "P6GeometryCalibrationService",
    "P6GeometryInferencePort",
]
