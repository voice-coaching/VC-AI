"""Production runner, publisher, recovery, and deep validator for WP4 S107.

S107 consumes probabilities produced by the already frozen S106 scorer on the
calibration split.  It fits exactly one scalar temperature and one provisional
engineering issue threshold.  It cannot retrain the encoder, task heads, fusion
model, feature contract, or loss.  Silver/oracle labels remain explicitly
provisional and can never produce a promotion or S108-readiness decision.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
from typing import Any
from uuid import uuid4

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import scipy  # type: ignore[import-untyped]
from scipy.optimize import minimize_scalar  # type: ignore[import-untyped]

from voice_coach.training.wavlm_s107_calibration_contract import (
    ARTIFACT_LAYOUT,
    EXECUTION_LOCK_SCHEMA_VERSION,
    INPUT_COLUMNS,
    INPUT_SCHEMA_VERSION,
    SLICE_AXES,
    CalibrationBundle,
    PredecessorEvidence,
    PredecessorValidator,
    S107ContractError,
    canonical_json_bytes,
    file_record,
    load_config,
    load_json_strict,
    resolve_file,
    resolve_output,
    sha256_file,
    validate_calibration_bundle,
    validate_config,
    validate_implementation_sources,
    validate_s106_predecessor,
    workspace_relative,
)


COMMIT_SCHEMA_VERSION = "voice_coach.wavlm_s107_calibration_commit.v1"
PREDICTION_SCHEMA_VERSION = "voice_coach.wavlm_s107_calibrated_predictions.v1"
CALIBRATOR_SCHEMA_VERSION = "voice_coach.wavlm_s107_calibrator_parameters.v1"
THRESHOLD_SCHEMA_VERSION = "voice_coach.wavlm_s107_threshold_policy.v1"
RELIABILITY_SCHEMA_VERSION = "voice_coach.wavlm_s107_reliability_report.v1"
RISK_SCHEMA_VERSION = "voice_coach.wavlm_s107_risk_coverage_report.v1"
SLICE_SCHEMA_VERSION = "voice_coach.wavlm_s107_slice_report.v1"
REPORT_SCHEMA_VERSION = "voice_coach.wavlm_s107_calibration_report.v1"
OUTPUT_COLUMNS = (
    "utterance_id_sha256",
    "speaker_id_sha256",
    "phone_index",
    "split",
    "uncalibrated_probability",
    "calibrated_error_probability",
    "reference_binary_error_label",
    "issued",
    "abstained",
    *SLICE_AXES,
)
SHA256_HEX = frozenset("0123456789abcdef")


class S107CalibrationError(RuntimeError):
    """Raised when protected input or S107 evidence is invalid."""


@dataclass(frozen=True, slots=True)
class CalibrationData:
    rows: tuple[Mapping[str, Any], ...]
    probabilities: np.ndarray
    labels: np.ndarray
    speakers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CalibrationFit:
    temperature: float
    calibrated_probabilities: np.ndarray
    raw_nll: float
    calibrated_nll: float
    optimizer_evaluations: int


@dataclass(frozen=True, slots=True)
class ThresholdSelection:
    threshold: float
    status: str
    issued: np.ndarray
    candidates: tuple[Mapping[str, Any], ...]
    issued_count: int
    issued_coverage: float
    issued_precision: float | None
    bootstrap: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ExpectedEvidence:
    data: CalibrationData
    fit: CalibrationFit
    threshold: ThresholdSelection
    prediction_rows: tuple[Mapping[str, Any], ...]
    documents: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class PublishedGeneration:
    generation_id: str
    path: Path
    records: tuple[Mapping[str, Any], ...]
    created: bool


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise S107CalibrationError(f"{name} must be a mapping")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise S107CalibrationError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise S107CalibrationError(f"{name} must be a non-empty string")
    return value


def _integer(value: object, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise S107CalibrationError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise S107CalibrationError(f"{name} must be at least {minimum}")
    return value


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise S107CalibrationError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise S107CalibrationError(f"{name} must be finite")
    return result


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) == 64 and set(value).issubset(SHA256_HEX)
    )


def _is_git_oid(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) in {40, 64}
        and set(value).issubset(SHA256_HEX)
    )


def _binding(document: Mapping[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("binding_sha256", None)
    return hashlib.sha256(canonical_json_bytes(unsigned, pretty=False)).hexdigest()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc(value: object, name: str) -> datetime:
    text = _string(value, name)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise S107CalibrationError(f"{name} is not ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise S107CalibrationError(f"{name} must be UTC")
    return parsed


def _write_atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = canonical_json_bytes(value, pretty=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part-{uuid4().hex}")
    replaced = False
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        replaced = True
    finally:
        if not replaced:
            temporary.unlink(missing_ok=True)


def _publish_new_atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = canonical_json_bytes(value, pretty=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part-{uuid4().hex}")
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise S107CalibrationError(
                f"concurrent or existing S107 writer: {path.name}"
            ) from error
    finally:
        temporary.unlink(missing_ok=True)


def _publish_immutable_json(path: Path, value: Mapping[str, Any]) -> None:
    _publish_new_atomic_json(path, value)


def _record_with_rows(
    root: Path,
    path: Path,
    row_count: int,
) -> Mapping[str, Any]:
    return {**file_record(root, path), "row_count": row_count}


def _schema_metadata(table: pa.Table, expected: str, name: str) -> None:
    metadata = table.schema.metadata or {}
    if metadata.get(b"voice_coach_schema_version") != expected.encode("utf-8"):
        raise S107CalibrationError(f"{name} schema-version metadata differs")


def _safe_group(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 128:
        raise S107CalibrationError(f"{name} must be null or a bounded string")
    if any(ord(character) < 32 for character in value):
        raise S107CalibrationError(f"{name} contains a control character")
    if value == "__MISSING__":
        raise S107CalibrationError(f"{name} uses a reserved missing-value token")
    return value


def load_calibration_data(bundle: CalibrationBundle) -> CalibrationData:
    """Read only calibration rows, using filter pushdown and exact row counts."""

    schema = pq.read_schema(bundle.prediction_path)
    if schema.names != list(INPUT_COLUMNS):
        raise S107CalibrationError("calibration input exact columns differ")
    if (schema.metadata or {}).get(b"voice_coach_schema_version") != (
        INPUT_SCHEMA_VERSION.encode("utf-8")
    ):
        raise S107CalibrationError("calibration input schema metadata differs")
    table = pq.read_table(
        bundle.prediction_path,
        columns=list(INPUT_COLUMNS),
        filters=[("split", "=", "calibration")],
    )
    _schema_metadata(table, INPUT_SCHEMA_VERSION, "calibration input")
    if table.num_rows != bundle.prediction_row_count:
        raise S107CalibrationError(
            "bundle row count differs or a non-calibration row was filtered"
        )
    parsed: list[Mapping[str, Any]] = []
    keys: set[tuple[str, int]] = set()
    for source in table.to_pylist():
        utterance = source.get("utterance_id_sha256")
        speaker = source.get("speaker_id_sha256")
        if not _is_sha256(utterance) or not _is_sha256(speaker):
            raise S107CalibrationError("calibration IDs are not namespaced SHA-256")
        phone_index = _integer(source.get("phone_index"), "phone_index", minimum=0)
        key = (str(utterance), phone_index)
        if key in keys:
            raise S107CalibrationError("duplicate calibration row key")
        keys.add(key)
        if source.get("split") != "calibration":
            raise S107CalibrationError("non-calibration row reached S107")
        probability = _finite(
            source.get("uncalibrated_probability"),
            "uncalibrated_probability",
        )
        if not 0.0 <= probability <= 1.0:
            raise S107CalibrationError("uncalibrated probability is outside [0,1]")
        label = source.get("reference_binary_error_label")
        if label not in (0, 1) or isinstance(label, bool):
            raise S107CalibrationError("binary calibration label differs")
        if source.get("qc_flagged") is not False:
            raise S107CalibrationError("QC-flagged calibration row is forbidden")
        if source.get("reserved_prompt") is not False:
            raise S107CalibrationError("reserved-prompt calibration row is forbidden")
        row: dict[str, Any] = {
            "utterance_id_sha256": str(utterance),
            "speaker_id_sha256": str(speaker),
            "phone_index": phone_index,
            "split": "calibration",
            "uncalibrated_probability": probability,
            "reference_binary_error_label": int(label),
        }
        for axis in SLICE_AXES:
            row[axis] = _safe_group(source.get(axis), axis)
        parsed.append(row)
    if not parsed:
        raise S107CalibrationError("calibration input is empty")
    parsed.sort(
        key=lambda row: (str(row["utterance_id_sha256"]), int(row["phone_index"]))
    )
    labels = np.asarray(
        [row["reference_binary_error_label"] for row in parsed],
        dtype=np.int64,
    )
    if len(np.unique(labels)) != 2:
        raise S107CalibrationError("calibration requires both binary classes")
    return CalibrationData(
        rows=tuple(parsed),
        probabilities=np.asarray(
            [row["uncalibrated_probability"] for row in parsed],
            dtype=np.float64,
        ),
        labels=labels,
        speakers=tuple(str(row["speaker_id_sha256"]) for row in parsed),
    )


def _binary_nll(probabilities: np.ndarray, labels: np.ndarray) -> float:
    return float(
        -np.mean(
            labels * np.log(probabilities) + (1 - labels) * np.log1p(-probabilities)
        )
    )


def _sigmoid(values: np.ndarray) -> np.ndarray:
    positive = values >= 0
    output = np.empty_like(values, dtype=np.float64)
    output[positive] = 1.0 / (1.0 + np.exp(-values[positive]))
    exponent = np.exp(values[~positive])
    output[~positive] = exponent / (1.0 + exponent)
    return output


def fit_temperature(
    data: CalibrationData,
    config: Mapping[str, Any],
) -> CalibrationFit:
    calibrator = _mapping(config.get("calibrator"), "calibrator")
    clip = _list(calibrator.get("probability_clip"), "probability clip")
    lower_clip, upper_clip = float(clip[0]), float(clip[1])
    clipped = np.clip(data.probabilities, lower_clip, upper_clip)
    logits = np.log(clipped) - np.log1p(-clipped)
    bounds = _list(calibrator.get("temperature_bounds"), "temperature bounds")
    lower, upper = float(bounds[0]), float(bounds[1])

    def objective(log_temperature: float) -> float:
        temperature = math.exp(log_temperature)
        calibrated = np.clip(
            _sigmoid(logits / temperature),
            lower_clip,
            upper_clip,
        )
        return _binary_nll(calibrated, data.labels)

    result = minimize_scalar(
        objective,
        bounds=(math.log(lower), math.log(upper)),
        method="bounded",
        options={"xatol": 1e-12, "maxiter": 1000},
    )
    if not result.success or not math.isfinite(float(result.x)):
        raise S107CalibrationError("temperature optimization failed")
    temperature = float(math.exp(float(result.x)))
    calibrated = np.clip(
        _sigmoid(logits / temperature),
        lower_clip,
        upper_clip,
    )
    if not np.all(np.isfinite(calibrated)):
        raise S107CalibrationError("calibrated probabilities are non-finite")
    return CalibrationFit(
        temperature=temperature,
        calibrated_probabilities=calibrated,
        raw_nll=_binary_nll(clipped, data.labels),
        calibrated_nll=_binary_nll(calibrated, data.labels),
        optimizer_evaluations=int(result.nfev),
    )


def _threshold_grid(config: Mapping[str, Any]) -> tuple[float, ...]:
    policy = _mapping(config.get("threshold_policy"), "threshold policy")
    grid = _mapping(policy.get("grid"), "threshold grid")
    start = float(grid["start"])
    stop = float(grid["stop"])
    step = float(grid["step"])
    count = int(round((stop - start) / step))
    return tuple(round(start + index * step, 12) for index in range(count + 1))


def _speaker_cluster_bootstrap(
    *,
    issued: np.ndarray,
    labels: np.ndarray,
    speakers: Sequence[str],
    config: Mapping[str, Any],
) -> Mapping[str, Any]:
    uncertainty = _mapping(config.get("uncertainty"), "uncertainty")
    unique = sorted(set(speakers))
    if not np.any(issued):
        return {
            "status": "not_evaluable_no_issued_rows",
            "method": "percentile_cluster_bootstrap",
            "cluster_unit": "speaker_id_sha256",
            "confidence_level": float(uncertainty["confidence_level"]),
            "replicates_requested": int(uncertainty["replicates"]),
            "replicates_valid": 0,
            "seed": int(uncertainty["seed"]),
            "lower": None,
            "upper": None,
            "promotion_gate_use_allowed": False,
        }
    speaker_array = np.asarray(speakers, dtype=object)
    issued_totals: np.ndarray = np.zeros(len(unique), dtype=np.int64)
    correct_totals: np.ndarray = np.zeros(len(unique), dtype=np.int64)
    for index, speaker in enumerate(unique):
        mask = speaker_array == speaker
        issued_totals[index] = int(np.sum(issued[mask]))
        correct_totals[index] = int(np.sum(issued[mask] & (labels[mask] == 1)))
    replicates = int(uncertainty["replicates"])
    rng = np.random.default_rng(int(uncertainty["seed"]))
    values: list[float] = []
    chunk = 250
    for start in range(0, replicates, chunk):
        size = min(chunk, replicates - start)
        samples = rng.integers(0, len(unique), size=(size, len(unique)))
        denominators = issued_totals[samples].sum(axis=1)
        numerators = correct_totals[samples].sum(axis=1)
        valid = denominators > 0
        values.extend((numerators[valid] / denominators[valid]).tolist())
    confidence = float(uncertainty["confidence_level"])
    alpha = (1.0 - confidence) / 2.0
    array: np.ndarray = np.asarray(values, dtype=np.float64)
    return {
        "status": "computed_provisional_not_promotion",
        "method": "percentile_cluster_bootstrap",
        "cluster_unit": "speaker_id_sha256",
        "confidence_level": confidence,
        "replicates_requested": replicates,
        "replicates_valid": len(values),
        "seed": int(uncertainty["seed"]),
        "lower": float(np.quantile(array, alpha)) if len(array) else None,
        "upper": float(np.quantile(array, 1.0 - alpha)) if len(array) else None,
        "promotion_gate_use_allowed": False,
    }


def select_issue_threshold(
    *,
    data: CalibrationData,
    fit: CalibrationFit,
    config: Mapping[str, Any],
) -> ThresholdSelection:
    policy = _mapping(config.get("threshold_policy"), "threshold policy")
    minimum_count = int(policy["provisional_minimum_issued_phone_count"])
    minimum_coverage = float(policy["provisional_minimum_issued_coverage"])
    target_precision = float(policy["target_point_precision"])
    candidates: list[Mapping[str, Any]] = []
    selected_mask: np.ndarray | None = None
    selected_threshold = 1.0
    for threshold in _threshold_grid(config):
        mask = fit.calibrated_probabilities >= threshold
        count = int(np.sum(mask))
        coverage = count / len(data.labels)
        precision = float(np.mean(data.labels[mask])) if count else None
        eligible = (
            count >= minimum_count
            and coverage >= minimum_coverage
            and precision is not None
            and precision >= target_precision
        )
        candidates.append(
            {
                "issue_threshold": threshold,
                "issued_count": count,
                "issued_coverage": coverage,
                "issued_precision": precision,
                "provisional_engineering_eligible": eligible,
            }
        )
        if eligible and selected_mask is None:
            selected_mask = mask
            selected_threshold = threshold
    if selected_mask is None:
        selected_mask = np.zeros(len(data.labels), dtype=np.bool_)
        status = "abstain_all_no_provisional_candidate"
    else:
        status = "selected_provisional_engineering"
    count = int(np.sum(selected_mask))
    coverage = count / len(data.labels)
    precision = float(np.mean(data.labels[selected_mask])) if count else None
    bootstrap = _speaker_cluster_bootstrap(
        issued=selected_mask,
        labels=data.labels,
        speakers=data.speakers,
        config=config,
    )
    return ThresholdSelection(
        threshold=selected_threshold,
        status=status,
        issued=selected_mask,
        candidates=tuple(candidates),
        issued_count=count,
        issued_coverage=coverage,
        issued_precision=precision,
        bootstrap=bootstrap,
    )


def reliability_bins(
    probabilities: np.ndarray,
    labels: np.ndarray,
    *,
    bins: int = 15,
) -> tuple[tuple[Mapping[str, Any], ...], float]:
    boundaries = np.linspace(0.0, 1.0, bins + 1)
    rows: list[Mapping[str, Any]] = []
    ece = 0.0
    total = len(labels)
    for index in range(bins):
        lower = float(boundaries[index])
        upper = float(boundaries[index + 1])
        if index == bins - 1:
            mask = (probabilities >= lower) & (probabilities <= upper)
        else:
            mask = (probabilities >= lower) & (probabilities < upper)
        count = int(np.sum(mask))
        if count:
            mean_probability = float(np.mean(probabilities[mask]))
            event_rate = float(np.mean(labels[mask]))
            gap = abs(mean_probability - event_rate)
            contribution = count / total * gap
            ece += contribution
        else:
            mean_probability = None
            event_rate = None
            gap = None
            contribution = 0.0
        rows.append(
            {
                "bin_index": index,
                "lower": lower,
                "upper": upper,
                "right_inclusive": index == bins - 1,
                "count": count,
                "mean_probability": mean_probability,
                "event_rate": event_rate,
                "absolute_gap": gap,
                "ece_contribution": contribution,
            }
        )
    return tuple(rows), float(ece)


def _brier(probabilities: np.ndarray, labels: np.ndarray) -> float:
    return float(np.mean((probabilities - labels) ** 2))


def _risk_coverage(
    *,
    data: CalibrationData,
    fit: CalibrationFit,
    selection: ThresholdSelection,
    config: Mapping[str, Any],
) -> Mapping[str, Any]:
    contract = _mapping(config.get("risk_coverage"), "risk coverage")
    order = np.argsort(-fit.calibrated_probabilities, kind="stable")
    points: list[Mapping[str, Any]] = []
    for target in contract["target_coverages"]:
        count = min(len(order), max(1, int(math.ceil(float(target) * len(order)))))
        chosen = order[:count]
        precision = float(np.mean(data.labels[chosen]))
        points.append(
            {
                "target_coverage": float(target),
                "retained_count": count,
                "actual_coverage": count / len(order),
                "minimum_calibrated_probability": float(
                    fit.calibrated_probabilities[order[count - 1]]
                ),
                "issued_precision": precision,
                "risk_one_minus_precision": 1.0 - precision,
            }
        )
    return {
        "schema_version": RISK_SCHEMA_VERSION,
        "status": "completed_provisional_engineering",
        "split": "calibration",
        "label_status": "silver_oracle_provisional",
        "ranking": "descending_calibrated_error_probability",
        "risk_definition": "one_minus_issued_precision",
        "points": points,
        "selected_policy_point": {
            "threshold": selection.threshold,
            "status": selection.status,
            "issued_count": selection.issued_count,
            "coverage": selection.issued_coverage,
            "precision": selection.issued_precision,
            "risk": (
                1.0 - selection.issued_precision
                if selection.issued_precision is not None
                else None
            ),
        },
        "promotion_evidence_allowed": False,
    }


def _slice_metrics(
    *,
    data: CalibrationData,
    fit: CalibrationFit,
    selection: ThresholdSelection,
    config: Mapping[str, Any],
) -> Mapping[str, Any]:
    minimum = int(
        _mapping(config.get("slices"), "slices")["provisional_minimum_phone_rows"]
    )
    axes: list[Mapping[str, Any]] = []
    for axis in SLICE_AXES:
        group_to_indices: dict[str, list[int]] = {}
        for index, row in enumerate(data.rows):
            value = row[axis] if row[axis] is not None else "__MISSING__"
            group_to_indices.setdefault(str(value), []).append(index)
        groups: list[Mapping[str, Any]] = []
        suppressed_rows = 0
        suppressed_groups = 0
        for group in sorted(group_to_indices):
            indices: np.ndarray = np.asarray(
                group_to_indices[group],
                dtype=np.int64,
            )
            if len(indices) < minimum:
                suppressed_rows += len(indices)
                suppressed_groups += 1
                continue
            probabilities = fit.calibrated_probabilities[indices]
            labels = data.labels[indices]
            issued = selection.issued[indices]
            _, ece = reliability_bins(probabilities, labels)
            issued_count = int(np.sum(issued))
            groups.append(
                {
                    "group": group,
                    "phone_rows": len(indices),
                    "speaker_count": len({data.speakers[index] for index in indices}),
                    "positive_count": int(np.sum(labels == 1)),
                    "negative_count": int(np.sum(labels == 0)),
                    "ece_15_equal_width": ece,
                    "brier": _brier(probabilities, labels),
                    "issued_count": issued_count,
                    "issued_coverage": issued_count / len(indices),
                    "issued_precision": (
                        float(np.mean(labels[issued])) if issued_count else None
                    ),
                    "promotion_gap_eligible": False,
                }
            )
        axes.append(
            {
                "axis": axis,
                "reported_groups": groups,
                "suppressed_group_count": suppressed_groups,
                "suppressed_phone_rows": suppressed_rows,
                "minimum_phone_rows": minimum,
                "small_cell_policy": "aggregate_suppressed_without_group_names",
            }
        )
    return {
        "schema_version": SLICE_SCHEMA_VERSION,
        "status": "completed_provisional_engineering",
        "split": "calibration",
        "label_status": "silver_oracle_provisional",
        "axes": axes,
        "minimum_speaker_positive_negative_support": "TBD_PREREGISTRATION",
        "promotion_gap_gate_allowed": False,
    }


def _prediction_rows(
    *,
    data: CalibrationData,
    fit: CalibrationFit,
    selection: ThresholdSelection,
) -> tuple[Mapping[str, Any], ...]:
    result: list[Mapping[str, Any]] = []
    for index, source in enumerate(data.rows):
        row = {
            "utterance_id_sha256": source["utterance_id_sha256"],
            "speaker_id_sha256": source["speaker_id_sha256"],
            "phone_index": source["phone_index"],
            "split": "calibration",
            "uncalibrated_probability": source["uncalibrated_probability"],
            "calibrated_error_probability": float(fit.calibrated_probabilities[index]),
            "reference_binary_error_label": source["reference_binary_error_label"],
            "issued": bool(selection.issued[index]),
            "abstained": not bool(selection.issued[index]),
        }
        for axis in SLICE_AXES:
            row[axis] = source[axis]
        result.append(row)
    return tuple(result)


def build_expected_evidence(
    *,
    data: CalibrationData,
    config: Mapping[str, Any],
    config_sha256: str,
    execution_lock_sha256: str,
    bundle: CalibrationBundle,
    predecessor: PredecessorEvidence,
) -> ExpectedEvidence:
    fit = fit_temperature(data, config)
    selection = select_issue_threshold(data=data, fit=fit, config=config)
    raw_bins, raw_ece = reliability_bins(data.probabilities, data.labels)
    calibrated_bins, calibrated_ece = reliability_bins(
        fit.calibrated_probabilities,
        data.labels,
    )
    calibrated_brier = _brier(fit.calibrated_probabilities, data.labels)
    ece_state = (
        "pass_engineering_only"
        if calibrated_ece <= 0.10
        else "gray_zone_promotion_fail"
        if calibrated_ece <= 0.15
        else "hard_fail"
    )
    documents: dict[str, Mapping[str, Any]] = {}
    documents["calibrator_parameters"] = {
        "schema_version": CALIBRATOR_SCHEMA_VERSION,
        "status": "completed",
        "family": "binary_logit_temperature_scaling",
        "temperature": fit.temperature,
        "intercept": None,
        "objective": "binary_negative_log_likelihood",
        "temperature_bounds": [0.05, 20.0],
        "probability_clip": [0.000001, 0.999999],
        "optimizer": "scipy_bounded_scalar",
        "optimizer_evaluations": fit.optimizer_evaluations,
        "fit_count": 1,
        "fit_split": "calibration",
        "source_s106_commit_sha256": predecessor.commit_sha256,
        "source_s106_checkpoint_sha256": predecessor.checkpoint_sha256,
        "source_bundle_sha256": bundle.sha256,
        "source_config_sha256": config_sha256,
        "source_execution_lock_sha256": execution_lock_sha256,
        "encoder_retrained": False,
        "head_retrained": False,
        "fusion_retrained": False,
        "fusion_feature_changed": False,
        "loss_changed": False,
    }
    documents["threshold_policy_report"] = {
        "schema_version": THRESHOLD_SCHEMA_VERSION,
        "status": selection.status,
        "policy_status": "provisional_engineering_not_promotion",
        "split": "calibration",
        "label_status": "silver_oracle_provisional",
        "target_point_precision": 0.9,
        "provisional_minimum_issued_phone_count": 30,
        "provisional_minimum_issued_coverage": 0.01,
        "selection": "maximum_coverage_among_point_precision_eligible",
        "tie_break": ["lower_issue_threshold"],
        "no_eligible_candidate": "abstain_all",
        "selected_issue_threshold": selection.threshold,
        "issued_count": selection.issued_count,
        "eligible_phone_count": len(data.labels),
        "issued_coverage": selection.issued_coverage,
        "issued_precision": selection.issued_precision,
        "abstained_count": len(data.labels) - selection.issued_count,
        "candidates": list(selection.candidates),
        "precision_uncertainty": dict(selection.bootstrap),
        "unresolved_final_preregistration": [
            "minimum_issued_phone_count",
            "minimum_issued_coverage",
            "ci_method_and_confidence_level",
            "point_estimate_or_ci_lower_bound_gate",
            "within_utterance_cluster_uncertainty",
        ],
        "promotion_gate_use_allowed": False,
        "s108_readiness_claim_allowed": False,
    }
    documents["reliability_report"] = {
        "schema_version": RELIABILITY_SCHEMA_VERSION,
        "status": "completed_provisional_engineering",
        "split": "calibration",
        "label_status": "silver_oracle_provisional",
        "event": "reference_binary_error_label_equals_one",
        "binning": {
            "bins": 15,
            "method": "equal_width",
            "interval_policy": "left_closed_right_open_last_closed",
            "empty_bin_policy": "retain_with_count_zero_null_statistics_zero_contribution",
        },
        "uncalibrated": {
            "ece": raw_ece,
            "brier": _brier(data.probabilities, data.labels),
            "nll": fit.raw_nll,
            "bins": list(raw_bins),
        },
        "calibrated": {
            "ece": calibrated_ece,
            "brier": calibrated_brier,
            "nll": fit.calibrated_nll,
            "bins": list(calibrated_bins),
        },
        "ece_state": ece_state,
        "promotion_evidence_allowed": False,
    }
    documents["risk_coverage_report"] = _risk_coverage(
        data=data,
        fit=fit,
        selection=selection,
        config=config,
    )
    documents["slice_report"] = _slice_metrics(
        data=data,
        fit=fit,
        selection=selection,
        config=config,
    )
    documents["calibration_only_report"] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "completed_provisional_engineering",
        "experiment_id": "aihub71490_wavlm_s107_calibration_v1",
        "stage": "s107_calibration_only_frozen_scorer",
        "task": "phone_binary_error",
        "split": "calibration",
        "row_count": len(data.labels),
        "speaker_count": len(set(data.speakers)),
        "positive_count": int(np.sum(data.labels == 1)),
        "negative_count": int(np.sum(data.labels == 0)),
        "label_evidence": {
            "status": "silver_oracle_provisional",
            "expert_gold": False,
            "d07_completed": False,
            "expert_valid_claim_allowed": False,
            "promotion_evidence_allowed": False,
        },
        "calibrator": {
            "family": "binary_logit_temperature_scaling",
            "temperature": fit.temperature,
            "fit_count": 1,
        },
        "metrics": {
            "ece_15_equal_width": calibrated_ece,
            "brier": calibrated_brier,
            "binary_nll": fit.calibrated_nll,
            "issued_phone_count": selection.issued_count,
            "issued_coverage": selection.issued_coverage,
            "issued_precision": selection.issued_precision,
        },
        "ece_state": ece_state,
        "issue_policy_status": selection.status,
        "frozen_model": {
            "encoder_retrained": False,
            "head_retrained": False,
            "fusion_retrained": False,
            "fusion_feature_changed": False,
            "loss_changed": False,
        },
        "split_access": {
            "train": False,
            "dev": False,
            "stack": False,
            "calibration": True,
            "locked_test": False,
        },
        "word_calibration_status": "not_evaluable",
        "utterance_calibration_status": "not_evaluable",
        "promotion_decision_allowed": False,
        "s108_ready": False,
        "s108_ready_status": "blocked_preregistration_and_d07",
        "unresolved_final_preregistration": [
            "minimum_issued_phone_count",
            "minimum_issued_coverage",
            "ci_method_and_confidence_level",
            "point_estimate_or_ci_lower_bound_gate",
            "within_utterance_cluster_uncertainty",
            "slice_minimum_speaker_positive_negative_support",
        ],
        "sources": {
            "config_sha256": config_sha256,
            "execution_lock_sha256": execution_lock_sha256,
            "s106_commit_sha256": predecessor.commit_sha256,
            "s106_checkpoint_sha256": predecessor.checkpoint_sha256,
            "calibration_bundle_sha256": bundle.sha256,
            "calibration_prediction_sha256": bundle.prediction_sha256,
        },
    }
    return ExpectedEvidence(
        data=data,
        fit=fit,
        threshold=selection,
        prediction_rows=_prediction_rows(
            data=data,
            fit=fit,
            selection=selection,
        ),
        documents=documents,
    )


def _write_parquet(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    table = pa.Table.from_pylist(list(rows))
    if table.schema.names != list(OUTPUT_COLUMNS):
        raise S107CalibrationError("generated prediction columns differ")
    table = table.replace_schema_metadata(
        {b"voice_coach_schema_version": PREDICTION_SCHEMA_VERSION.encode("utf-8")}
    )
    pq.write_table(
        table,
        path,
        compression="zstd",
        use_dictionary=False,
        write_statistics=True,
    )


def artifact_records(generation_path: Path) -> tuple[Mapping[str, Any], ...]:
    expected_files = {relative for _, relative, _, _ in ARTIFACT_LAYOUT}
    files: set[str] = set()
    directories: set[str] = set()
    for path in generation_path.rglob("*"):
        if path.is_symlink():
            raise S107CalibrationError("S107 generation contains a symlink")
        relative = path.relative_to(generation_path).as_posix()
        if path.is_file():
            files.add(relative)
        elif path.is_dir():
            directories.add(relative)
        else:
            raise S107CalibrationError("S107 generation node type differs")
    if files != expected_files or directories:
        raise S107CalibrationError("S107 generation exact layout differs")
    records: list[Mapping[str, Any]] = []
    for name, relative, format_name, access_scope in ARTIFACT_LAYOUT:
        path = generation_path / relative
        if path.stat().st_size <= 0:
            raise S107CalibrationError(f"S107 artifact is empty: {name}")
        records.append(
            {
                "name": name,
                "path": relative,
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
                "format": format_name,
                "access_scope": access_scope,
            }
        )
    return tuple(records)


def _generation_id(records: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "schema_version": COMMIT_SCHEMA_VERSION,
                "artifacts": list(records),
            },
            pretty=False,
        )
    ).hexdigest()


def _build_generation(
    *,
    temporary: Path,
    evidence: ExpectedEvidence,
) -> None:
    temporary.mkdir(parents=False, exist_ok=False)
    _write_parquet(
        temporary / "calibration_predictions.parquet",
        evidence.prediction_rows,
    )
    for name in (
        "calibrator_parameters",
        "threshold_policy_report",
        "reliability_report",
        "risk_coverage_report",
        "slice_report",
        "calibration_only_report",
    ):
        _write_atomic_json(
            temporary / f"{name}.json",
            evidence.documents[name],
        )
    artifact_records(temporary)


def publish_generation(
    *,
    temporary: Path,
    generation_root: Path,
) -> PublishedGeneration:
    records = artifact_records(temporary)
    generation_id = _generation_id(records)
    destination = generation_root / generation_id
    generation_root.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if (
            destination.is_symlink()
            or not destination.is_dir()
            or artifact_records(destination) != records
        ):
            raise S107CalibrationError("content-addressed generation differs")
        shutil.rmtree(temporary)
        created = False
    else:
        os.replace(temporary, destination)
        created = True
    return PublishedGeneration(
        generation_id=generation_id,
        path=destination,
        records=records,
        created=created,
    )


def _git_metadata(root: Path) -> Mapping[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )
    except (OSError, subprocess.CalledProcessError):
        return {"available": False, "commit": None, "dirty": None}
    return {"available": True, "commit": commit, "dirty": dirty}


def _registry_path(root: Path, config: Mapping[str, Any]) -> Path:
    outputs = _mapping(config.get("outputs"), "outputs")
    registry_root = resolve_output(root, outputs.get("registry_root"), "registry root")
    run_id = _string(outputs.get("planned_registry_run_id"), "registry run id")
    if "/" in run_id or "\\" in run_id or run_id.startswith("."):
        raise S107CalibrationError("registry run id is unsafe")
    relative = (
        Path(workspace_relative(root, registry_root)) / run_id / "metadata.json"
    ).as_posix()
    return resolve_output(root, relative, "registry metadata")


def _running_registry(
    *,
    root: Path,
    config_path: Path,
    lock_path: Path,
    predecessor: PredecessorEvidence,
    bundle: CalibrationBundle,
    run_id: str,
) -> Mapping[str, Any]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "experiment_id": "aihub71490_wavlm_s107_calibration_v1",
        "stage": "s107_calibration_only_frozen_scorer",
        "status": "running",
        "started_at_utc": _now_utc(),
        "completed_at_utc": None,
        "failure": None,
        "generation_id": None,
        "generation_path": None,
        "inputs": {
            "config": file_record(root, config_path),
            "execution_lock": file_record(root, lock_path),
            "s106_commit": file_record(root, predecessor.commit_path),
            "s106_checkpoint": file_record(root, predecessor.checkpoint_path),
            "calibration_bundle": file_record(root, bundle.path),
            "calibration_prediction": _record_with_rows(
                root,
                bundle.prediction_path,
                bundle.prediction_row_count,
            ),
        },
        "split_access": {
            "train": False,
            "dev": False,
            "stack": False,
            "calibration": True,
            "locked_test": False,
        },
        "label_status": "silver_oracle_provisional",
        "environment_files_read": [],
        "raw_audio_accessed": False,
        "gpu_initialized": False,
        "frozen_model": {
            "encoder_retrained": False,
            "head_retrained": False,
            "fusion_retrained": False,
            "fusion_feature_changed": False,
            "loss_changed": False,
        },
        "artifacts": [],
        "metrics": [],
        "selection": None,
        "runtime": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pyarrow": pa.__version__,
            "scipy": scipy.__version__,
            "device": "cpu",
            "cuda_used": False,
            "thread_count": 1,
            "git": _git_metadata(root),
        },
        "promotion_decision_allowed": False,
        "s108_ready": False,
    }


def _completed_registry(
    *,
    root: Path,
    running: Mapping[str, Any],
    generation: PublishedGeneration,
    evidence: ExpectedEvidence,
) -> Mapping[str, Any]:
    artifacts = [
        {
            **record,
            "path": workspace_relative(
                root,
                generation.path / str(record["path"]),
            ),
        }
        for record in generation.records
    ]
    report = evidence.documents["calibration_only_report"]
    metrics = _mapping(report.get("metrics"), "report metrics")
    return {
        **running,
        "status": "completed",
        "completed_at_utc": _now_utc(),
        "failure": None,
        "generation_id": generation.generation_id,
        "generation_path": workspace_relative(root, generation.path),
        "artifacts": artifacts,
        "metrics": [
            {
                "name": "ece_15_equal_width",
                "split": "calibration_silver_oracle",
                "value": metrics["ece_15_equal_width"],
            },
            {
                "name": "brier",
                "split": "calibration_silver_oracle",
                "value": metrics["brier"],
            },
            {
                "name": "issued_precision",
                "split": "calibration_silver_oracle",
                "value": metrics["issued_precision"],
            },
        ],
        "selection": {
            "temperature": evidence.fit.temperature,
            "issue_threshold": evidence.threshold.threshold,
            "scope": "calibration_only_provisional_engineering",
        },
    }


def _failed_registry(
    running: Mapping[str, Any],
    error: BaseException,
) -> Mapping[str, Any]:
    if isinstance(error, (S107ContractError, S107CalibrationError)):
        category = type(error).__name__
    elif isinstance(error, OSError):
        category = "OSError"
    else:
        category = "unexpected_error"
    return {
        **running,
        "status": "failed",
        "completed_at_utc": _now_utc(),
        "failure": {
            "category": category,
            "message": "S107 failed; inspect restricted execution logs",
        },
    }


def validate_execution_lock(
    *,
    root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    lock_path: Path,
    predecessor_validator: PredecessorValidator | None,
) -> tuple[Mapping[str, Any], PredecessorEvidence, CalibrationBundle]:
    lock_sha = sha256_file(lock_path)
    lock = load_json_strict(lock_path, "S107 execution lock")
    if (
        lock.get("schema_version") != EXECUTION_LOCK_SCHEMA_VERSION
        or lock.get("status") != "prepared"
        or lock.get("experiment_id") != "aihub71490_wavlm_s107_calibration_v1"
        or lock.get("stage") != "s107_calibration_only_frozen_scorer"
        or lock.get("config") != file_record(root, config_path)
        or lock.get("gpu_initialized") is not False
        or lock.get("metrics_computed") is not False
        or lock.get("raw_audio_accessed") is not False
        or lock.get("environment_files_read") != []
        or lock.get("output_role") != "no_metric_execution_lock"
    ):
        raise S107CalibrationError("S107 execution lock status differs")
    split_access = _mapping(lock.get("split_access"), "execution lock split access")
    if split_access != {
        "train": False,
        "dev": False,
        "stack": False,
        "calibration": False,
        "locked_test": False,
    }:
        raise S107CalibrationError("preflight split access differs")
    implementation = validate_implementation_sources(
        workspace_root=root,
        config=config,
    )
    if lock.get("implementation") != implementation:
        raise S107CalibrationError("execution lock implementation differs")
    predecessor = validate_s106_predecessor(
        workspace_root=root,
        config=config,
        validator=predecessor_validator,
    )
    locked_predecessor = _mapping(lock.get("predecessor"), "lock predecessor")
    if locked_predecessor != {
        "stage": "s106_stack_only_confidence_aware_fusion",
        "validation_status": "valid",
        "commit": file_record(root, predecessor.commit_path),
        "checkpoint": file_record(root, predecessor.checkpoint_path),
        "generation_id": predecessor.generation_id,
        "full_stack_in_sample_metric_reported": False,
    }:
        raise S107CalibrationError("execution lock predecessor differs")
    bundle = validate_calibration_bundle(
        workspace_root=root,
        config=config,
        predecessor=predecessor,
    )
    if (
        lock.get("calibration_bundle") != file_record(root, bundle.path)
        or lock.get("prediction_matrix")
        != {
            **file_record(root, bundle.prediction_path),
            "row_count": bundle.prediction_row_count,
            "semantic_rows_read_by_preflight": False,
        }
        or lock.get("label_status") != bundle.label_status
    ):
        raise S107CalibrationError("execution lock calibration bundle differs")
    if sha256_file(lock_path) != lock_sha:
        raise S107CalibrationError("execution lock changed during validation")
    return lock, predecessor, bundle


def _validate_prediction_artifact(
    path: Path,
    expected: Sequence[Mapping[str, Any]],
) -> None:
    table = pq.read_table(path)
    if table.schema.names != list(OUTPUT_COLUMNS):
        raise S107CalibrationError("published prediction columns differ")
    _schema_metadata(table, PREDICTION_SCHEMA_VERSION, "published predictions")
    rows = table.to_pylist()
    if len(rows) != len(expected):
        raise S107CalibrationError("published prediction row count differs")
    for actual, target in zip(rows, expected, strict=True):
        if set(actual) != set(target):
            raise S107CalibrationError("published prediction schema differs")
        for name, value in target.items():
            if name in {
                "uncalibrated_probability",
                "calibrated_error_probability",
            }:
                if not math.isclose(
                    _finite(actual.get(name), name),
                    float(value),
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                ):
                    raise S107CalibrationError("published probability differs")
            elif actual.get(name) != value:
                raise S107CalibrationError("published prediction row differs")


def _validate_json_equal(path: Path, expected: Mapping[str, Any], name: str) -> None:
    actual = load_json_strict(path, name)
    if actual != expected:
        raise S107CalibrationError(f"{name} differs from recomputation")


def validate_generation_deep(
    *,
    root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    lock_path: Path,
    predecessor: PredecessorEvidence,
    bundle: CalibrationBundle,
    generation_path: Path,
) -> Mapping[str, Any]:
    data = load_calibration_data(bundle)
    evidence = build_expected_evidence(
        data=data,
        config=config,
        config_sha256=sha256_file(config_path),
        execution_lock_sha256=sha256_file(lock_path),
        bundle=bundle,
        predecessor=predecessor,
    )
    records = artifact_records(generation_path)
    generation_id = _generation_id(records)
    if generation_path.name != generation_id:
        raise S107CalibrationError("S107 generation identity differs")
    _validate_prediction_artifact(
        generation_path / "calibration_predictions.parquet",
        evidence.prediction_rows,
    )
    for name in (
        "calibrator_parameters",
        "threshold_policy_report",
        "reliability_report",
        "risk_coverage_report",
        "slice_report",
        "calibration_only_report",
    ):
        _validate_json_equal(
            generation_path / f"{name}.json",
            evidence.documents[name],
            f"S107 {name}",
        )
    report = evidence.documents["calibration_only_report"]
    return {
        "status": "valid",
        "generation_id": generation_id,
        "artifact_count": len(records),
        "row_count": len(data.labels),
        "speaker_count": len(set(data.speakers)),
        "temperature": evidence.fit.temperature,
        "issue_threshold": evidence.threshold.threshold,
        "ece_15_equal_width": report["metrics"]["ece_15_equal_width"],
        "brier": report["metrics"]["brier"],
        "issued_precision": report["metrics"]["issued_precision"],
        "label_status": "silver_oracle_provisional",
        "promotion_decision_allowed": False,
        "s108_ready": False,
        "encoder_retrained": False,
        "head_retrained": False,
        "fusion_retrained": False,
    }


def _validate_registry(
    *,
    root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    lock_path: Path,
    predecessor: PredecessorEvidence,
    bundle: CalibrationBundle,
    generation_path: Path,
    records: Sequence[Mapping[str, Any]],
    registry_path: Path,
    validation: Mapping[str, Any],
) -> Mapping[str, Any]:
    registry_sha = sha256_file(registry_path)
    registry = load_json_strict(registry_path, "S107 registry")
    outputs = _mapping(config.get("outputs"), "outputs")
    if (
        registry.get("schema_version") != 1
        or registry.get("run_id") != outputs.get("planned_registry_run_id")
        or registry.get("experiment_id") != "aihub71490_wavlm_s107_calibration_v1"
        or registry.get("stage") != "s107_calibration_only_frozen_scorer"
        or registry.get("status") != "completed"
        or registry.get("failure") is not None
        or registry.get("generation_id") != generation_path.name
        or registry.get("generation_path") != workspace_relative(root, generation_path)
        or registry.get("label_status") != "silver_oracle_provisional"
        or registry.get("environment_files_read") != []
        or registry.get("raw_audio_accessed") is not False
        or registry.get("gpu_initialized") is not False
        or registry.get("promotion_decision_allowed") is not False
        or registry.get("s108_ready") is not False
    ):
        raise S107CalibrationError("completed S107 registry status differs")
    started = _parse_utc(registry.get("started_at_utc"), "registry start")
    completed = _parse_utc(registry.get("completed_at_utc"), "registry completion")
    if completed < started:
        raise S107CalibrationError("registry completion precedes start")
    if registry.get("split_access") != {
        "train": False,
        "dev": False,
        "stack": False,
        "calibration": True,
        "locked_test": False,
    }:
        raise S107CalibrationError("registry split access differs")
    if registry.get("frozen_model") != {
        "encoder_retrained": False,
        "head_retrained": False,
        "fusion_retrained": False,
        "fusion_feature_changed": False,
        "loss_changed": False,
    }:
        raise S107CalibrationError("registry frozen-model status differs")
    expected_inputs = {
        "config": file_record(root, config_path),
        "execution_lock": file_record(root, lock_path),
        "s106_commit": file_record(root, predecessor.commit_path),
        "s106_checkpoint": file_record(root, predecessor.checkpoint_path),
        "calibration_bundle": file_record(root, bundle.path),
        "calibration_prediction": _record_with_rows(
            root,
            bundle.prediction_path,
            bundle.prediction_row_count,
        ),
    }
    if registry.get("inputs") != expected_inputs:
        raise S107CalibrationError("registry input binding differs")
    expected_artifacts = [
        {
            **record,
            "path": workspace_relative(root, generation_path / str(record["path"])),
        }
        for record in records
    ]
    if registry.get("artifacts") != expected_artifacts:
        raise S107CalibrationError("registry artifact binding differs")
    expected_metrics = [
        {
            "name": "ece_15_equal_width",
            "split": "calibration_silver_oracle",
            "value": validation["ece_15_equal_width"],
        },
        {
            "name": "brier",
            "split": "calibration_silver_oracle",
            "value": validation["brier"],
        },
        {
            "name": "issued_precision",
            "split": "calibration_silver_oracle",
            "value": validation["issued_precision"],
        },
    ]
    if registry.get("metrics") != expected_metrics:
        raise S107CalibrationError("registry metrics differ")
    if registry.get("selection") != {
        "temperature": validation["temperature"],
        "issue_threshold": validation["issue_threshold"],
        "scope": "calibration_only_provisional_engineering",
    }:
        raise S107CalibrationError("registry selection differs")
    runtime = _mapping(registry.get("runtime"), "registry runtime")
    if (
        runtime.get("python") != platform.python_version()
        or runtime.get("numpy") != np.__version__
        or runtime.get("pyarrow") != pa.__version__
        or runtime.get("scipy") != scipy.__version__
        or runtime.get("device") != "cpu"
        or runtime.get("cuda_used") is not False
        or runtime.get("thread_count") != 1
    ):
        raise S107CalibrationError("registry runtime differs")
    git = _mapping(runtime.get("git"), "registry git")
    if (
        not isinstance(git.get("available"), bool)
        or (
            git.get("available") is True
            and (
                not _is_git_oid(git.get("commit"))
                or not isinstance(git.get("dirty"), bool)
            )
        )
        or (
            git.get("available") is False
            and (git.get("commit") is not None or git.get("dirty") is not None)
        )
    ):
        raise S107CalibrationError("registry git metadata differs")
    if sha256_file(registry_path) != registry_sha:
        raise S107CalibrationError("registry changed during validation")
    return registry


def _build_commit(
    *,
    root: Path,
    config_path: Path,
    lock_path: Path,
    predecessor: PredecessorEvidence,
    bundle: CalibrationBundle,
    generation: PublishedGeneration,
    registry_path: Path,
) -> Mapping[str, Any]:
    commit: dict[str, Any] = {
        "schema_version": COMMIT_SCHEMA_VERSION,
        "status": "committed",
        "generation_id": generation.generation_id,
        "generation_path": workspace_relative(root, generation.path),
        "artifacts": list(generation.records),
        "config": file_record(root, config_path),
        "execution_lock": file_record(root, lock_path),
        "s106_commit": file_record(root, predecessor.commit_path),
        "s106_checkpoint": file_record(root, predecessor.checkpoint_path),
        "calibration_bundle": file_record(root, bundle.path),
        "registry": {
            **file_record(root, registry_path),
            "run_id": load_json_strict(registry_path, "S107 registry")["run_id"],
            "status": "completed",
            "artifact_count": len(generation.records),
        },
        "label_status": "silver_oracle_provisional",
        "promotion_decision_allowed": False,
        "s108_ready": False,
    }
    commit["binding_sha256"] = _binding(commit)
    return commit


def validate_s107_commit(
    *,
    workspace_root: Path,
    config_path: Path,
    predecessor_validator: PredecessorValidator | None = None,
) -> Mapping[str, Any]:
    """Read-only deep recomputation of input, metrics, artifacts, and hash chain."""

    root = workspace_root.resolve(strict=True)
    resolved_config = config_path
    if not resolved_config.is_absolute():
        resolved_config = root / resolved_config
    resolved_config = resolved_config.resolve(strict=True)
    config = load_config(resolved_config)
    validate_config(config)
    validate_implementation_sources(workspace_root=root, config=config)
    outputs = _mapping(config.get("outputs"), "outputs")
    lock_path = resolve_file(root, outputs.get("execution_lock"), "execution lock")
    _, predecessor, bundle = validate_execution_lock(
        root=root,
        config_path=resolved_config,
        config=config,
        lock_path=lock_path,
        predecessor_validator=predecessor_validator,
    )
    commit_path = resolve_file(root, outputs.get("commit_manifest"), "S107 commit")
    commit_sha = sha256_file(commit_path)
    commit = load_json_strict(commit_path, "S107 commit")
    if set(commit) != {
        "schema_version",
        "status",
        "generation_id",
        "generation_path",
        "artifacts",
        "config",
        "execution_lock",
        "s106_commit",
        "s106_checkpoint",
        "calibration_bundle",
        "registry",
        "label_status",
        "promotion_decision_allowed",
        "s108_ready",
        "binding_sha256",
    }:
        raise S107CalibrationError("S107 commit schema differs")
    if (
        commit.get("schema_version") != COMMIT_SCHEMA_VERSION
        or commit.get("status") != "committed"
        or not _is_sha256(commit.get("generation_id"))
        or commit.get("binding_sha256") != _binding(commit)
        or commit.get("label_status") != "silver_oracle_provisional"
        or commit.get("promotion_decision_allowed") is not False
        or commit.get("s108_ready") is not False
    ):
        raise S107CalibrationError("S107 commit binding differs")
    generation_root = resolve_output(
        root,
        outputs.get("generation_root"),
        "generation root",
    )
    generation_path = resolve_output(
        root,
        commit.get("generation_path"),
        "generation path",
    )
    if (
        not generation_path.is_dir()
        or generation_path.is_symlink()
        or generation_path.parent != generation_root
        or generation_path.name != commit.get("generation_id")
    ):
        raise S107CalibrationError("S107 generation path differs")
    validation = validate_generation_deep(
        root=root,
        config_path=resolved_config,
        config=config,
        lock_path=lock_path,
        predecessor=predecessor,
        bundle=bundle,
        generation_path=generation_path,
    )
    records = artifact_records(generation_path)
    if (
        commit.get("artifacts") != list(records)
        or commit.get("config") != file_record(root, resolved_config)
        or commit.get("execution_lock") != file_record(root, lock_path)
        or commit.get("s106_commit") != file_record(root, predecessor.commit_path)
        or commit.get("s106_checkpoint")
        != file_record(root, predecessor.checkpoint_path)
        or commit.get("calibration_bundle") != file_record(root, bundle.path)
    ):
        raise S107CalibrationError("S107 commit source binding differs")
    registry_path = _registry_path(root, config)
    registry = _validate_registry(
        root=root,
        config_path=resolved_config,
        config=config,
        lock_path=lock_path,
        predecessor=predecessor,
        bundle=bundle,
        generation_path=generation_path,
        records=records,
        registry_path=registry_path,
        validation=validation,
    )
    expected_registry = {
        **file_record(root, registry_path),
        "run_id": registry["run_id"],
        "status": "completed",
        "artifact_count": len(records),
    }
    if commit.get("registry") != expected_registry:
        raise S107CalibrationError("S107 commit registry differs")
    # Final TOCTOU audit repeats predecessor/bundle/source validation and every
    # content hash without writing.
    validate_implementation_sources(workspace_root=root, config=config)
    final_predecessor = validate_s106_predecessor(
        workspace_root=root,
        config=config,
        validator=predecessor_validator,
    )
    final_bundle = validate_calibration_bundle(
        workspace_root=root,
        config=config,
        predecessor=final_predecessor,
    )
    if (
        sha256_file(commit_path) != commit_sha
        or final_predecessor.commit_sha256 != predecessor.commit_sha256
        or final_predecessor.checkpoint_sha256 != predecessor.checkpoint_sha256
        or final_bundle.sha256 != bundle.sha256
        or final_bundle.prediction_sha256 != bundle.prediction_sha256
        or artifact_records(generation_path) != records
        or sha256_file(registry_path) != expected_registry["sha256"]
    ):
        raise S107CalibrationError("S107 evidence changed during validation")
    return {
        **validation,
        "commit_sha256": commit_sha,
        "registry_sha256": expected_registry["sha256"],
        "execution_lock_sha256": sha256_file(lock_path),
        "calibration_bundle_sha256": bundle.sha256,
        "calibration_prediction_sha256": bundle.prediction_sha256,
        "calibration_only": True,
        "train_accessed": False,
        "dev_accessed": False,
        "stack_accessed": False,
        "calibration_accessed": True,
        "locked_test_accessed": False,
        "raw_audio_accessed": False,
        "gpu_initialized": False,
    }


def _precommit_revalidate(
    *,
    root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    lock_path: Path,
    predecessor: PredecessorEvidence,
    bundle: CalibrationBundle,
    generation: PublishedGeneration,
    registry_path: Path,
    validation: Mapping[str, Any],
    predecessor_validator: PredecessorValidator | None,
) -> None:
    _, current_predecessor, current_bundle = validate_execution_lock(
        root=root,
        config_path=config_path,
        config=config,
        lock_path=lock_path,
        predecessor_validator=predecessor_validator,
    )
    if (
        current_predecessor.commit_sha256 != predecessor.commit_sha256
        or current_predecessor.checkpoint_sha256 != predecessor.checkpoint_sha256
        or current_bundle.sha256 != bundle.sha256
        or current_bundle.prediction_sha256 != bundle.prediction_sha256
        or artifact_records(generation.path) != generation.records
    ):
        raise S107CalibrationError("S107 evidence changed before commit")
    _validate_registry(
        root=root,
        config_path=config_path,
        config=config,
        lock_path=lock_path,
        predecessor=predecessor,
        bundle=bundle,
        generation_path=generation.path,
        records=generation.records,
        registry_path=registry_path,
        validation=validation,
    )


def _recover_completed_registry(
    *,
    root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    lock_path: Path,
    predecessor: PredecessorEvidence,
    bundle: CalibrationBundle,
    registry_path: Path,
    commit_path: Path,
    predecessor_validator: PredecessorValidator | None,
) -> Mapping[str, Any]:
    registry = load_json_strict(registry_path, "S107 recovery registry")
    if registry.get("status") != "completed":
        raise S107CalibrationError("existing S107 registry is not recoverable")
    generation_path = resolve_output(
        root,
        registry.get("generation_path"),
        "recoverable generation",
    )
    validation = validate_generation_deep(
        root=root,
        config_path=config_path,
        config=config,
        lock_path=lock_path,
        predecessor=predecessor,
        bundle=bundle,
        generation_path=generation_path,
    )
    records = artifact_records(generation_path)
    generation = PublishedGeneration(
        generation_id=generation_path.name,
        path=generation_path,
        records=records,
        created=False,
    )
    _precommit_revalidate(
        root=root,
        config_path=config_path,
        config=config,
        lock_path=lock_path,
        predecessor=predecessor,
        bundle=bundle,
        generation=generation,
        registry_path=registry_path,
        validation=validation,
        predecessor_validator=predecessor_validator,
    )
    commit = _build_commit(
        root=root,
        config_path=config_path,
        lock_path=lock_path,
        predecessor=predecessor,
        bundle=bundle,
        generation=generation,
        registry_path=registry_path,
    )
    _publish_immutable_json(commit_path, commit)
    return validate_s107_commit(
        workspace_root=root,
        config_path=config_path,
        predecessor_validator=predecessor_validator,
    )


def run_s107_calibration(
    *,
    workspace_root: Path,
    config_path: Path,
    predecessor_validator: PredecessorValidator | None = None,
) -> Mapping[str, Any]:
    """Fit/recover one provisional calibration generation and validate it."""

    root = workspace_root.resolve(strict=True)
    resolved_config = config_path
    if not resolved_config.is_absolute():
        resolved_config = root / resolved_config
    resolved_config = resolved_config.resolve(strict=True)
    config = load_config(resolved_config)
    validate_config(config)
    validate_implementation_sources(workspace_root=root, config=config)
    outputs = _mapping(config.get("outputs"), "outputs")
    lock_path = resolve_file(root, outputs.get("execution_lock"), "execution lock")
    _, predecessor, bundle = validate_execution_lock(
        root=root,
        config_path=resolved_config,
        config=config,
        lock_path=lock_path,
        predecessor_validator=predecessor_validator,
    )
    commit_path = resolve_output(root, outputs.get("commit_manifest"), "commit")
    registry_path = _registry_path(root, config)
    if commit_path.exists():
        return validate_s107_commit(
            workspace_root=root,
            config_path=resolved_config,
            predecessor_validator=predecessor_validator,
        )
    if registry_path.exists():
        return _recover_completed_registry(
            root=root,
            config_path=resolved_config,
            config=config,
            lock_path=lock_path,
            predecessor=predecessor,
            bundle=bundle,
            registry_path=registry_path,
            commit_path=commit_path,
            predecessor_validator=predecessor_validator,
        )
    run_id = _string(outputs.get("planned_registry_run_id"), "registry run id")
    running = _running_registry(
        root=root,
        config_path=resolved_config,
        lock_path=lock_path,
        predecessor=predecessor,
        bundle=bundle,
        run_id=run_id,
    )
    _publish_new_atomic_json(registry_path, running)
    generation_root = resolve_output(
        root,
        outputs.get("generation_root"),
        "generation root",
    )
    generation_root.mkdir(parents=True, exist_ok=True)
    temporary = generation_root / f".part-{uuid4().hex}"
    try:
        data = load_calibration_data(bundle)
        evidence = build_expected_evidence(
            data=data,
            config=config,
            config_sha256=sha256_file(resolved_config),
            execution_lock_sha256=sha256_file(lock_path),
            bundle=bundle,
            predecessor=predecessor,
        )
        _build_generation(temporary=temporary, evidence=evidence)
        generation = publish_generation(
            temporary=temporary,
            generation_root=generation_root,
        )
        validation = validate_generation_deep(
            root=root,
            config_path=resolved_config,
            config=config,
            lock_path=lock_path,
            predecessor=predecessor,
            bundle=bundle,
            generation_path=generation.path,
        )
        completed = _completed_registry(
            root=root,
            running=running,
            generation=generation,
            evidence=evidence,
        )
        _write_atomic_json(registry_path, completed)
        _precommit_revalidate(
            root=root,
            config_path=resolved_config,
            config=config,
            lock_path=lock_path,
            predecessor=predecessor,
            bundle=bundle,
            generation=generation,
            registry_path=registry_path,
            validation=validation,
            predecessor_validator=predecessor_validator,
        )
        commit = _build_commit(
            root=root,
            config_path=resolved_config,
            lock_path=lock_path,
            predecessor=predecessor,
            bundle=bundle,
            generation=generation,
            registry_path=registry_path,
        )
        _publish_immutable_json(commit_path, commit)
        return validate_s107_commit(
            workspace_root=root,
            config_path=resolved_config,
            predecessor_validator=predecessor_validator,
        )
    except BaseException as error:
        if temporary.exists():
            shutil.rmtree(temporary)
        if registry_path.exists():
            current = load_json_strict(registry_path, "S107 failure registry")
            if current.get("status") == "running":
                _write_atomic_json(registry_path, _failed_registry(running, error))
        raise


__all__ = [
    "CALIBRATOR_SCHEMA_VERSION",
    "COMMIT_SCHEMA_VERSION",
    "PREDICTION_SCHEMA_VERSION",
    "RELIABILITY_SCHEMA_VERSION",
    "REPORT_SCHEMA_VERSION",
    "RISK_SCHEMA_VERSION",
    "S107CalibrationError",
    "SLICE_SCHEMA_VERSION",
    "THRESHOLD_SCHEMA_VERSION",
    "artifact_records",
    "build_expected_evidence",
    "fit_temperature",
    "load_calibration_data",
    "publish_generation",
    "reliability_bins",
    "run_s107_calibration",
    "select_issue_threshold",
    "validate_execution_lock",
    "validate_generation_deep",
    "validate_s107_commit",
]
