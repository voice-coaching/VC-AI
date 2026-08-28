"""PDF-aligned S1 metric aggregation and independent S2 content audit.

This module is an in-memory adapter. GOP/CTC, F0, eGeMAPS, nPVI, UTMOS and
speaker-similarity values must be supplied by their explicitly versioned
backends. It never invents unavailable UTMOS or speaker scores. S2 content is
computed through the existing Korean WER/CER primitive and requires a backend
identity different from the S1 evaluator.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Mapping, Sequence

import numpy as np

from voice_coach.evaluation.duration_sequence_dtw import DurationDtwResult
from voice_coach.evaluation.korean_content_metrics import (
    ContentMismatchDecision,
    KoreanContentMetrics,
    decide_content_mismatch,
    korean_content_metrics,
)
from voice_coach.features.egemaps import EGeMAPSFunctionals
from voice_coach.features.gop import GOPFeatures
from voice_coach.features.parselmouth_pitch import ParselmouthPitchContour


class ClosedLoopMetricError(ValueError):
    """Raised when a PDF metric input is missing or non-finite."""


def _score(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ClosedLoopMetricError(f"{field} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise ClosedLoopMetricError(f"{field} must be finite and in [0, 1]")
    return parsed


def _mean(values: Sequence[float], field: str) -> float:
    if not values:
        raise ClosedLoopMetricError(f"{field} must not be empty")
    converted = tuple(float(value) for value in values)
    if not all(math.isfinite(value) for value in converted):
        raise ClosedLoopMetricError(f"{field} contains NaN or Inf")
    return math.fsum(converted) / len(converted)


S1_EVALUATION_TARGETS: Mapping[str, Mapping[str, float]] = {
    "phone": {"f1_min": 0.80},
    "word": {"pcc_min": 0.70},
    "utterance": {"icc_min": 0.70},
    "calibration": {"ece_max": 0.10},
}


def normalized_pairwise_variability_index(durations_seconds: Sequence[float]) -> float:
    """Return nPVI for a positive rhythm-duration sequence."""

    if len(durations_seconds) < 2:
        raise ClosedLoopMetricError("nPVI requires at least two durations")
    values = tuple(float(value) for value in durations_seconds)
    if not all(math.isfinite(value) and value > 0.0 for value in values):
        raise ClosedLoopMetricError("nPVI durations must be finite and positive")
    return 100.0 * _mean(
        [
            abs(left - right) / ((left + right) / 2.0)
            for left, right in zip(values, values[1:])
        ],
        "nPVI pairwise values",
    )


def _gop_summary(features: GOPFeatures) -> dict[str, float]:
    matrix = np.asarray(features.as_matrix(), dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[1] != 4 or matrix.shape[0] == 0:
        raise ClosedLoopMetricError("GOP features must be a non-empty [rows, 4] matrix")
    if not np.isfinite(matrix).all():
        raise ClosedLoopMetricError("GOP features contain NaN or Inf")
    return {
        "gop_target_log_posterior_mean": float(np.mean(matrix[:, 0])),
        "gop_margin_mean": float(np.mean(matrix[:, 1])),
        "gop_entropy_mean": float(np.mean(matrix[:, 2])),
        "gop_top1_rate": float(np.mean(matrix[:, 3])),
    }


def _ctc_summary(
    log_probabilities: object,
    frame_indices: Sequence[int],
    target_class_indices: Sequence[int],
) -> dict[str, float]:
    values = np.asarray(log_probabilities, dtype=np.float64)
    if values.ndim != 2 or values.shape[0] == 0 or values.shape[1] == 0:
        raise ClosedLoopMetricError("CTC log probabilities must be non-empty [frames, classes]")
    frames = tuple(int(value) for value in frame_indices)
    targets = tuple(int(value) for value in target_class_indices)
    if len(frames) != len(targets) or not frames:
        raise ClosedLoopMetricError("CTC frame and target indices must have equal nonzero length")
    if any(frame < 0 or frame >= values.shape[0] for frame in frames):
        raise ClosedLoopMetricError("CTC frame index is out of range")
    if any(target < 0 or target >= values.shape[1] for target in targets):
        raise ClosedLoopMetricError("CTC target class index is out of range")
    selected = np.asarray([values[frame, target] for frame, target in zip(frames, targets, strict=True)])
    top1 = np.asarray([int(np.argmax(values[frame])) == target for frame, target in zip(frames, targets, strict=True)])
    if not np.isfinite(selected).all():
        raise ClosedLoopMetricError("CTC selected values contain NaN or Inf")
    return {
        "ctc_target_log_probability_mean": float(np.mean(selected)),
        "ctc_target_top1_rate": float(np.mean(top1)),
        "ctc_frame_count": float(len(frames)),
    }


@dataclass(frozen=True, slots=True)
class S1MetricBundle:
    """Aggregate-only S1 metrics for one generated/reference comparison."""

    metrics: Mapping[str, float]
    missing_metrics: tuple[str, ...]
    backend_ids: Mapping[str, str]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "voice_coach.s1_metric_bundle.v1",
            "metrics": dict(self.metrics),
            "missing_metrics": list(self.missing_metrics),
            "backend_ids": dict(self.backend_ids),
        }


def compute_s1_metrics(
    *,
    gop_features: GOPFeatures | None = None,
    ctc_log_probabilities: object | None = None,
    ctc_frame_indices: Sequence[int] | None = None,
    ctc_target_class_indices: Sequence[int] | None = None,
    f0_contour: ParselmouthPitchContour | None = None,
    egemaps: EGeMAPSFunctionals | None = None,
    phone_durations_seconds: Sequence[float] | None = None,
    duration_dtw_result: DurationDtwResult | None = None,
    utmos_score: float | None = None,
    speaker_similarity_score: float | None = None,
    backend_ids: Mapping[str, str] | None = None,
) -> S1MetricBundle:
    """Collect available PDF S1 axes without substituting zeros for missing data."""

    metrics: dict[str, float] = {}
    missing: list[str] = []
    if gop_features is None:
        missing.append("gop")
    else:
        metrics.update(_gop_summary(gop_features))
    if ctc_log_probabilities is None:
        missing.append("ctc")
    elif ctc_frame_indices is None or ctc_target_class_indices is None:
        raise ClosedLoopMetricError("CTC indices are required with log probabilities")
    else:
        metrics.update(_ctc_summary(ctc_log_probabilities, ctc_frame_indices, ctc_target_class_indices))
    if f0_contour is None:
        missing.append("f0")
    else:
        metrics.update({
            "f0_voiced_median_hz": float(f0_contour.voiced_median_hz),
            "f0_voiced_range_hz": float(f0_contour.voiced_range_hz),
            "f0_voiced_frame_rate": f0_contour.voiced_frame_count / f0_contour.backend_frame_count,
        })
    if egemaps is None:
        missing.append("egemaps")
    else:
        metrics["egemaps_functional_mean"] = _mean(egemaps.values, "eGeMAPS values")
        metrics["egemaps_functional_std"] = float(np.std(np.asarray(egemaps.values, dtype=np.float64)))
    if phone_durations_seconds is None:
        missing.append("npvi")
    else:
        metrics["npvi"] = normalized_pairwise_variability_index(phone_durations_seconds)
    if duration_dtw_result is None:
        missing.append("mfa_dtw")
    else:
        if not math.isfinite(float(duration_dtw_result.normalized_cost)) or duration_dtw_result.normalized_cost < 0:
            raise ClosedLoopMetricError("MFA+DTW normalized cost must be finite and non-negative")
        metrics["mfa_dtw_normalized_cost"] = float(duration_dtw_result.normalized_cost)
        metrics["mfa_dtw_path_cell_count"] = float(duration_dtw_result.path_cell_count)
    if utmos_score is None:
        missing.append("utmos")
    else:
        metrics["utmos"] = _score(utmos_score, "utmos_score")
    if speaker_similarity_score is None:
        missing.append("speaker_similarity")
    else:
        metrics["speaker_similarity"] = _score(speaker_similarity_score, "speaker_similarity_score")
    if not all(math.isfinite(value) for value in metrics.values()):
        raise ClosedLoopMetricError("S1 metrics contain NaN or Inf")
    return S1MetricBundle(
        metrics=metrics,
        missing_metrics=tuple(missing),
        backend_ids=dict(backend_ids or {}),
    )


@dataclass(frozen=True, slots=True)
class S2AuditBundle:
    """Independent S2 content audit summary."""

    auditor_backend_id: str
    s1_backend_id: str
    content: KoreanContentMetrics
    decision: ContentMismatchDecision
    result_digest_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "voice_coach.s2_independent_audit_bundle.v1",
            "auditor_backend_id": self.auditor_backend_id,
            "s1_backend_id": self.s1_backend_id,
            "content": self.content.to_dict(),
            "decision": self.decision.to_dict(),
            "result_digest_sha256": self.result_digest_sha256,
        }


@dataclass(frozen=True, slots=True)
class S1ScorerEvaluationBundle:
    """PDF section 7.1 scorer metrics, without inventing absent labels."""

    metrics: Mapping[str, float]
    missing_metrics: tuple[str, ...]
    targets: Mapping[str, Mapping[str, float]]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "voice_coach.s1_scorer_evaluation.v1",
            "metrics": dict(self.metrics),
            "missing_metrics": list(self.missing_metrics),
            "targets": {key: dict(value) for key, value in self.targets.items()},
        }


def _finite_vector(values: object, field: str) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1 or array.size == 0 or not np.isfinite(array).all():
        raise ClosedLoopMetricError(f"{field} must be a non-empty finite vector")
    return array


def _binary_vector(values: object, field: str) -> np.ndarray:
    array = _finite_vector(values, field)
    if not np.isin(array, (0.0, 1.0)).all():
        raise ClosedLoopMetricError(f"{field} must contain only 0 or 1")
    return array


def _unit_vector(values: object, field: str) -> np.ndarray:
    array = _finite_vector(values, field)
    if np.any(array < 0.0) or np.any(array > 1.0):
        raise ClosedLoopMetricError(f"{field} must be in [0, 1]")
    return array


def _rank_average(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(values.size, dtype=np.float64)
    index = 0
    while index < values.size:
        end = index + 1
        while end < values.size and values[order[end]] == values[order[index]]:
            end += 1
        ranks[order[index:end]] = (index + 1 + end) / 2.0
        index = end
    return ranks


def _pearson(left: np.ndarray, right: np.ndarray, field: str) -> float:
    left_centered = left - np.mean(left)
    right_centered = right - np.mean(right)
    denominator = float(np.sqrt(np.sum(left_centered**2) * np.sum(right_centered**2)))
    if denominator == 0.0:
        raise ClosedLoopMetricError(f"{field} correlation is undefined for constant values")
    return float(np.sum(left_centered * right_centered) / denominator)


def _paired_regression_metrics(targets: object, predictions: object, field: str) -> dict[str, float]:
    target = _finite_vector(targets, f"{field}_targets")
    prediction = _finite_vector(predictions, f"{field}_predictions")
    if target.size != prediction.size or target.size < 2:
        raise ClosedLoopMetricError(f"{field} targets and predictions must have equal length >= 2")
    return {
        "pcc": _pearson(target, prediction, f"{field}.pcc"),
        "spearman": _pearson(_rank_average(target), _rank_average(prediction), f"{field}.spearman"),
        "mae": float(np.mean(np.abs(target - prediction))),
    }


def _binary_auc(labels: np.ndarray, scores: np.ndarray) -> float:
    positives = labels == 1.0
    negatives = labels == 0.0
    if not positives.any() or not negatives.any():
        raise ClosedLoopMetricError("phone AUC requires both positive and negative labels")
    ranks = _rank_average(scores)
    positive_count = float(positives.sum())
    negative_count = float(negatives.sum())
    return float((np.sum(ranks[positives]) - positive_count * (positive_count + 1.0) / 2.0) / (positive_count * negative_count))


def _icc_3_1(targets: np.ndarray, predictions: np.ndarray) -> float:
    matrix = np.column_stack((targets, predictions))
    count = matrix.shape[0]
    grand = float(np.mean(matrix))
    row_means = np.mean(matrix, axis=1)
    column_means = np.mean(matrix, axis=0)
    ms_rows = 2.0 * float(np.sum((row_means - grand) ** 2)) / (count - 1)
    residual = matrix - row_means[:, None] - column_means[None, :] + grand
    ms_error = float(np.sum(residual**2)) / (count - 1)
    denominator = ms_rows + ms_error
    if denominator == 0.0:
        raise ClosedLoopMetricError("utterance ICC is undefined for constant values")
    return float((ms_rows - ms_error) / denominator)


def _calibration_metrics(confidence: object, correctness: object, bins: int) -> dict[str, float]:
    conf = _unit_vector(confidence, "calibration_confidence")
    correct = _unit_vector(correctness, "calibration_correctness")
    if conf.size != correct.size:
        raise ClosedLoopMetricError("calibration confidence and correctness lengths differ")
    if isinstance(bins, bool) or not isinstance(bins, int) or bins < 2:
        raise ClosedLoopMetricError("calibration bins must be an integer >= 2")
    ece = 0.0
    for index in range(bins):
        lower = index / bins
        upper = (index + 1) / bins
        mask = (conf >= lower) & (conf < upper if index + 1 < bins else conf <= upper)
        if mask.any():
            ece += float(mask.mean()) * abs(float(np.mean(correct[mask])) - float(np.mean(conf[mask])))
    order = np.argsort(-conf, kind="mergesort")
    cumulative_risk = np.cumsum(1.0 - correct[order]) / np.arange(1, correct.size + 1)
    coverage_index = min(correct.size - 1, max(0, int(math.ceil(0.8 * correct.size)) - 1))
    return {
        "ece": float(ece),
        "brier": float(np.mean((conf - correct) ** 2)),
        "selective_risk_coverage_auc": float(np.mean(cumulative_risk)),
        "selective_risk_at_coverage_0_8": float(cumulative_risk[coverage_index]),
    }


def compute_s1_scorer_evaluation(
    *,
    phone_labels: object | None = None,
    phone_scores: object | None = None,
    word_targets: object | None = None,
    word_predictions: object | None = None,
    utterance_targets: object | None = None,
    utterance_predictions: object | None = None,
    calibration_confidence: object | None = None,
    calibration_correctness: object | None = None,
    calibration_bins: int = 10,
) -> S1ScorerEvaluationBundle:
    """Compute PDF 7.1 phone/word/utterance/calibration metrics."""

    metrics: dict[str, float] = {}
    missing: list[str] = []
    if phone_labels is None or phone_scores is None:
        if phone_labels is not None or phone_scores is not None:
            raise ClosedLoopMetricError("phone labels and scores must be supplied together")
        missing.append("phone")
    else:
        labels = _binary_vector(phone_labels, "phone_labels")
        scores = _unit_vector(phone_scores, "phone_scores")
        if labels.size != scores.size:
            raise ClosedLoopMetricError("phone labels and scores lengths differ")
        predicted = scores >= 0.5
        true_positive = float(np.sum(predicted & (labels == 1.0)))
        false_positive = float(np.sum(predicted & (labels == 0.0)))
        false_negative = float(np.sum(~predicted & (labels == 1.0)))
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
        recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
        metrics.update({
            "phone_precision": precision,
            "phone_recall": recall,
            "phone_f1": 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0,
            "phone_auc": _binary_auc(labels, scores),
        })
    if word_targets is None or word_predictions is None:
        if word_targets is not None or word_predictions is not None:
            raise ClosedLoopMetricError("word targets and predictions must be supplied together")
        missing.append("word")
    else:
        metrics.update({f"word_{key}": value for key, value in _paired_regression_metrics(word_targets, word_predictions, "word").items() if key != "mae"})
    if utterance_targets is None or utterance_predictions is None:
        if utterance_targets is not None or utterance_predictions is not None:
            raise ClosedLoopMetricError("utterance targets and predictions must be supplied together")
        missing.append("utterance")
    else:
        utterance = _paired_regression_metrics(utterance_targets, utterance_predictions, "utterance")
        utterance["icc"] = _icc_3_1(_finite_vector(utterance_targets, "utterance_targets"), _finite_vector(utterance_predictions, "utterance_predictions"))
        metrics.update({f"utterance_{key}": value for key, value in utterance.items()})
    if calibration_confidence is None or calibration_correctness is None:
        if calibration_confidence is not None or calibration_correctness is not None:
            raise ClosedLoopMetricError("calibration confidence and correctness must be supplied together")
        missing.append("calibration")
    else:
        metrics.update({f"calibration_{key}": value for key, value in _calibration_metrics(calibration_confidence, calibration_correctness, calibration_bins).items()})
    return S1ScorerEvaluationBundle(metrics=metrics, missing_metrics=tuple(missing), targets=S1_EVALUATION_TARGETS)


def run_s2_independent_audit(
    reference_transcript: str | bytes,
    hypothesis_transcript: str | bytes,
    *,
    wer_threshold: float,
    cer_threshold: float,
    auditor_backend_id: str,
    s1_backend_id: str,
) -> S2AuditBundle:
    """Run the independent transcript/content branch with explicit thresholds."""

    if not auditor_backend_id or not s1_backend_id or auditor_backend_id == s1_backend_id:
        raise ClosedLoopMetricError("S2 auditor must be independently identified from S1")
    content = korean_content_metrics(reference_transcript, hypothesis_transcript)
    decision = decide_content_mismatch(
        content,
        wer_threshold=wer_threshold,
        cer_threshold=cer_threshold,
    )
    digest = hashlib.sha256(
        json_bytes({
            "auditor_backend_id": auditor_backend_id,
            "s1_backend_id": s1_backend_id,
            "content": content.to_dict(),
            "decision": decision.to_dict(),
        })
    ).hexdigest()
    return S2AuditBundle(
        auditor_backend_id=auditor_backend_id,
        s1_backend_id=s1_backend_id,
        content=content,
        decision=decision,
        result_digest_sha256=digest,
    )


def json_bytes(value: object) -> bytes:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


__all__ = [
    "ClosedLoopMetricError",
    "S1MetricBundle",
    "S1ScorerEvaluationBundle",
    "S1_EVALUATION_TARGETS",
    "S2AuditBundle",
    "compute_s1_metrics",
    "compute_s1_scorer_evaluation",
    "normalized_pairwise_variability_index",
    "run_s2_independent_audit",
]
