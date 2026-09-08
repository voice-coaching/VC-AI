"""Training and comparison primitives for the preregistered WP4 S105 ablation.

This module does not open a split or run a GPU job on import.  It supplies the
grouping, masked two-task loss, train-inner selection guards, exact dev-pass
ledger, paired-speaker comparison, subgroup gate, and artifact-tree checks
needed by a later production runner.  Word intervals never become targets.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import ctypes
from dataclasses import dataclass
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
from typing import Any, Literal, cast
from uuid import uuid4

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from safetensors import safe_open
import torch
from torch import nn
from torch.nn import functional

from voice_coach.evaluation.metrics import holm_correction, macro_f1
from voice_coach.models.wavlm_hierarchy import (
    HierarchyOutput,
    IncrementalHierarchyHead,
    VARIANT_ORDER,
)


REPORT_SCHEMA_VERSION = "voice_coach.wavlm_s105_hierarchy_report.v1"
COMPARISON_SCHEMA_VERSION = "voice_coach.wavlm_s105_hierarchy_comparison.v1"
COMMIT_SCHEMA_VERSION = "voice_coach.wavlm_s105_hierarchy_commit.v1"
CHECKPOINT_SCHEMA_VERSION = "voice_coach.wavlm_s105_hierarchy_checkpoint.v1"
PHONE_PREDICTION_SCHEMA_VERSION = "voice_coach.wavlm_s105_phone_predictions.v1"
UTTERANCE_PREDICTION_SCHEMA_VERSION = "voice_coach.wavlm_s105_utterance_predictions.v1"
SEEDS = (42, 43, 44)
TASKS = ("phone_binary_error", "utterance_scores")
PRIMARY_OUTCOMES = (
    "phone_macro_f1",
    "comprehensibility_score_icc_2_1",
)
ADJACENT_VARIANT_PAIRS = tuple(
    f"{control}__to__{candidate}"
    for control, candidate in zip(VARIANT_ORDER[:-1], VARIANT_ORDER[1:], strict=True)
)
ALLOWED_SPLITS = frozenset({"train", "dev"})
FORBIDDEN_SPLITS = frozenset({"stack", "calibration", "locked_test"})
OUTCOMES = (
    "pronunciation_score",
    "fluency_score",
    "comprehensibility_score",
)
SubgroupAxis = Literal["l1", "gender", "proficiency"]


class S105TrainingError(RuntimeError):
    """Raised when the S105 execution or evidence contract is violated."""


class GenerationPublicationDurabilityError(OSError):
    """A complete rename succeeded but generation-root fsync failed."""

    def __init__(self, publication: Mapping[str, Any]) -> None:
        super().__init__("S105 generation durability fsync failed")
        self.publication = publication


@dataclass(frozen=True, slots=True)
class Interval:
    start: float
    end: float


@dataclass(frozen=True, slots=True)
class HierarchyTrainingExample:
    """All-layer representations and labels for one guarded utterance."""

    utterance_id_sha256: str
    speaker_id_sha256: str
    split: str
    phone_layers: torch.Tensor
    word_layers: torch.Tensor
    utterance_layers: torch.Tensor
    phone_to_word: torch.Tensor
    target_indices: torch.Tensor
    phone_targets: torch.Tensor
    utterance_targets: torch.Tensor


@dataclass(frozen=True, slots=True)
class LossBundle:
    total: torch.Tensor
    phone: torch.Tensor
    utterance: torch.Tensor
    observed_utterance_outcomes: int


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    estimate: float
    confidence_lower: float
    confidence_upper: float
    confidence_level: float
    n_resamples: int
    n_valid_resamples: int
    n_speakers: int
    seed: int
    p_value_two_sided_zero: float


@dataclass(frozen=True, slots=True)
class SubgroupResult:
    groups: tuple[Mapping[str, Any], ...]
    eligible_group_count: int
    worst_group_drop: float
    threshold_exclusive: float
    passed: bool


@dataclass(frozen=True, slots=True)
class DeepValidationContext:
    """Immutable source bindings required for publish/commit validation."""

    config: Mapping[str, Any]
    execution_lock: Mapping[str, Any]
    expected_dev_truth: Mapping[str, Any]
    expected_fit_metadata: Any


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise S105TrainingError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise S105TrainingError(f"{name} must be finite")
    return result


def canonical_json_bytes(value: Mapping[str, Any], *, pretty: bool) -> bytes:
    options: dict[str, Any] = {
        "ensure_ascii": False,
        "sort_keys": True,
        "allow_nan": False,
    }
    if pretty:
        options["indent"] = 2
    else:
        options["separators"] = (",", ":")
    return (json.dumps(value, **options) + "\n").encode("utf-8")


def sha256_file(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise S105TrainingError("artifact must be a regular non-symlink file")
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    after = path.stat()
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise S105TrainingError("artifact changed while hashing")
    return digest.hexdigest()


def _clean_interval(value: object, name: str) -> Interval:
    if not isinstance(value, Mapping):
        raise S105TrainingError(f"{name} is not an interval")
    start = _finite(value.get("start"), f"{name}.start")
    end = _finite(value.get("end"), f"{name}.end")
    if start < 0 or end <= start:
        raise S105TrainingError(f"{name} bounds are invalid")
    return Interval(start=start, end=end)


def map_phones_to_words(
    phone_intervals: Sequence[object],
    word_intervals: Sequence[object],
) -> tuple[int, ...]:
    """Map each target phone by maximum positive overlap.

    Equal overlaps choose the lowest word index.  A phone with no positive
    overlap maps to ``-1`` and is represented only in the utterance orphan
    bucket.  Neither word text nor any outcome label is used.
    """

    phones = tuple(
        _clean_interval(value, f"phone_intervals[{index}]")
        for index, value in enumerate(phone_intervals)
    )
    words = tuple(
        _clean_interval(value, f"word_intervals[{index}]")
        for index, value in enumerate(word_intervals)
    )
    if phones != tuple(sorted(phones, key=lambda item: (item.start, item.end))):
        raise S105TrainingError("phone intervals are not ordered")
    if words != tuple(sorted(words, key=lambda item: (item.start, item.end))):
        raise S105TrainingError("word intervals are not ordered")
    output: list[int] = []
    for phone in phones:
        overlaps = [
            max(0.0, min(phone.end, word.end) - max(phone.start, word.start))
            for word in words
        ]
        positive = [index for index, overlap in enumerate(overlaps) if overlap > 0]
        if not positive:
            output.append(-1)
            continue
        maximum = max(overlaps[index] for index in positive)
        output.append(
            min(
                index
                for index in positive
                if math.isclose(
                    overlaps[index],
                    maximum,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
            )
        )
    return tuple(output)


def assert_allowed_split(split: str, *, training: bool) -> None:
    if split in FORBIDDEN_SPLITS or split not in ALLOWED_SPLITS:
        raise S105TrainingError("protected split access is forbidden")
    expected = "train" if training else "dev"
    if split != expected:
        raise S105TrainingError(f"expected only the {expected} split")


def joint_hierarchy_loss(
    output: HierarchyOutput,
    *,
    phone_targets: torch.Tensor,
    utterance_targets: torch.Tensor,
    positive_weight: torch.Tensor,
    phone_loss_weight: float,
    utterance_loss_weight: float,
    word_loss_weight: float = 0.0,
) -> LossBundle:
    """Compute only phone and observed utterance losses.

    The explicit zero word weight is checked rather than silently ignored so a
    caller cannot introduce synthetic word supervision through this API.
    """

    if word_loss_weight != 0.0:
        raise S105TrainingError("word loss is forbidden")
    if phone_loss_weight <= 0 or utterance_loss_weight <= 0:
        raise S105TrainingError("task loss weights must be positive")
    if (
        output.phone_logits.ndim != 1
        or phone_targets.shape != output.phone_logits.shape
    ):
        raise S105TrainingError("phone target shape differs")
    if phone_targets.dtype not in {torch.float16, torch.float32, torch.float64}:
        raise S105TrainingError("phone targets must be floating point")
    if output.utterance_scores.shape != (len(OUTCOMES),):
        raise S105TrainingError("utterance output shape differs")
    if utterance_targets.shape != (len(OUTCOMES),):
        raise S105TrainingError("utterance target shape differs")
    observed = torch.isfinite(utterance_targets)
    observed_count = int(observed.sum().item())
    if observed_count <= 0:
        utterance_loss = output.utterance_scores.sum() * 0.0
    else:
        utterance_loss = functional.mse_loss(
            output.utterance_scores[observed],
            utterance_targets[observed],
            reduction="mean",
        )
    phone_loss = functional.binary_cross_entropy_with_logits(
        output.phone_logits,
        phone_targets,
        pos_weight=positive_weight,
        reduction="mean",
    )
    total = (
        float(phone_loss_weight) * phone_loss
        + float(utterance_loss_weight) * utterance_loss
    )
    if not bool(torch.isfinite(total)):
        raise S105TrainingError("joint loss is non-finite")
    return LossBundle(
        total=total,
        phone=phone_loss,
        utterance=utterance_loss,
        observed_utterance_outcomes=observed_count,
    )


class DevPassLedger:
    """Require exactly one completed dev pass for every variant/task/seed."""

    def __init__(
        self,
        *,
        variants: Sequence[str] = VARIANT_ORDER,
        tasks: Sequence[str] = TASKS,
        seeds: Sequence[int] = SEEDS,
    ) -> None:
        self._expected = {
            (str(variant), str(task), int(seed))
            for variant in variants
            for task in tasks
            for seed in seeds
        }
        self._active: set[tuple[str, str, int]] = set()
        self._completed: set[tuple[str, str, int]] = set()

    def enter(self, *, variant: str, task: str, seed: int) -> None:
        key = (variant, task, seed)
        if key not in self._expected:
            raise S105TrainingError("dev pass key is not preregistered")
        if key in self._active or key in self._completed:
            raise S105TrainingError("dev pass attempted more than once")
        self._active.add(key)

    def complete(self, *, variant: str, task: str, seed: int) -> None:
        key = (variant, task, seed)
        if key not in self._active:
            raise S105TrainingError("dev pass completed without an active pass")
        self._active.remove(key)
        self._completed.add(key)

    def assert_exact(self) -> None:
        if self._active or self._completed != self._expected:
            raise S105TrainingError("dev pass ledger is not exactly complete")


def train_head_epoch(
    *,
    model: IncrementalHierarchyHead,
    examples: Iterable[HierarchyTrainingExample],
    optimizer: torch.optim.Optimizer,
    positive_weight: torch.Tensor,
    phone_loss_weight: float,
    utterance_loss_weight: float,
    gradient_clip_norm: float = 1.0,
) -> Mapping[str, float]:
    """Train a hierarchy head on a caller-provided train-inner-fit stream."""

    if gradient_clip_norm <= 0:
        raise S105TrainingError("gradient clip norm must be positive")
    model.train()
    total_loss = 0.0
    phone_loss = 0.0
    utterance_loss = 0.0
    row_count = 0
    for example in examples:
        assert_allowed_split(example.split, training=True)
        optimizer.zero_grad(set_to_none=True)
        output = model(
            phone_layers=example.phone_layers,
            word_layers=example.word_layers,
            utterance_layers=example.utterance_layers,
            phone_to_word=example.phone_to_word,
            target_indices=example.target_indices,
        )
        losses = joint_hierarchy_loss(
            output,
            phone_targets=example.phone_targets,
            utterance_targets=example.utterance_targets,
            positive_weight=positive_weight,
            phone_loss_weight=phone_loss_weight,
            utterance_loss_weight=utterance_loss_weight,
            word_loss_weight=0.0,
        )
        losses.total.backward()
        gradient_norm = torch.nn.utils.clip_grad_norm_(
            model.parameters(),
            max_norm=float(gradient_clip_norm),
            error_if_nonfinite=True,
        )
        if not bool(torch.isfinite(gradient_norm)):
            raise S105TrainingError("hierarchy gradient norm is non-finite")
        optimizer.step()
        for parameter in model.parameters():
            if not bool(torch.all(torch.isfinite(parameter))):
                raise S105TrainingError("hierarchy parameter became non-finite")
        total_loss += float(losses.total.detach())
        phone_loss += float(losses.phone.detach())
        utterance_loss += float(losses.utterance.detach())
        row_count += 1
    if row_count <= 0:
        raise S105TrainingError("train-inner-fit stream is empty")
    return {
        "utterances": float(row_count),
        "total_loss_mean": total_loss / row_count,
        "phone_loss_mean": phone_loss / row_count,
        "utterance_loss_mean": utterance_loss / row_count,
    }


def _bootstrap_p_value(distribution: np.ndarray) -> float:
    less_equal = int(np.count_nonzero(distribution <= 0))
    greater_equal = int(np.count_nonzero(distribution >= 0))
    tail = min(
        (less_equal + 1) / (distribution.size + 1),
        (greater_equal + 1) / (distribution.size + 1),
    )
    return float(min(1.0, 2.0 * tail))


def _finish_bootstrap(
    distribution: Sequence[float],
    *,
    estimate: float,
    confidence_level: float,
    n_resamples: int,
    n_speakers: int,
    seed: int,
) -> BootstrapResult:
    values = np.asarray(distribution, dtype=np.float64)
    values = values[np.isfinite(values)]
    if not math.isfinite(estimate) or values.size <= 0:
        raise S105TrainingError("paired bootstrap produced no finite values")
    tail = (1.0 - confidence_level) / 2.0
    lower, upper = np.quantile(values, [tail, 1.0 - tail])
    return BootstrapResult(
        estimate=float(estimate),
        confidence_lower=float(lower),
        confidence_upper=float(upper),
        confidence_level=float(confidence_level),
        n_resamples=int(n_resamples),
        n_valid_resamples=int(values.size),
        n_speakers=int(n_speakers),
        seed=int(seed),
        p_value_two_sided_zero=_bootstrap_p_value(values),
    )


def _macro_f1_from_confusion(values: np.ndarray) -> float:
    true_negative, false_positive, false_negative, true_positive = (
        float(item) for item in values
    )
    shared = false_positive + false_negative
    negative_denominator = 2 * true_negative + shared
    positive_denominator = 2 * true_positive + shared
    negative_f1 = (
        2 * true_negative / negative_denominator if negative_denominator else 0.0
    )
    positive_f1 = (
        2 * true_positive / positive_denominator if positive_denominator else 0.0
    )
    return float((negative_f1 + positive_f1) / 2.0)


def paired_phone_bootstrap(
    *,
    speaker_ids: np.ndarray,
    targets: np.ndarray,
    control_predictions: Mapping[int, np.ndarray],
    candidate_predictions: Mapping[int, np.ndarray],
    n_resamples: int = 5_000,
    confidence_level: float = 0.95,
    seed: int = 42,
) -> BootstrapResult:
    """Candidate-minus-control phone F1 with paired speaker resampling."""

    speakers = np.asarray(speaker_ids)
    truth = np.asarray(targets, dtype=np.int8)
    unique, inverse = np.unique(speakers, return_inverse=True)
    if unique.size < 2 or truth.ndim != 1 or truth.size != speakers.size:
        raise S105TrainingError("phone bootstrap inputs are invalid")
    if set(control_predictions) != set(SEEDS) or set(candidate_predictions) != set(
        SEEDS
    ):
        raise S105TrainingError("phone bootstrap requires all preregistered seeds")
    counts = np.zeros((len(unique), len(SEEDS), 2, 4), dtype=np.int64)
    for seed_index, seed_value in enumerate(SEEDS):
        for variant_index, prediction_map in enumerate(
            (control_predictions, candidate_predictions)
        ):
            predictions = np.asarray(prediction_map[seed_value], dtype=np.int8)
            if predictions.shape != truth.shape or np.any(
                (predictions < 0) | (predictions > 1)
            ):
                raise S105TrainingError("phone bootstrap prediction shape differs")
            categories = truth * 2 + predictions
            np.add.at(
                counts[:, seed_index, variant_index, :],
                (inverse, categories),
                1,
            )

    def effect(weights: np.ndarray) -> float:
        total = np.tensordot(weights, counts, axes=(0, 0))
        return float(
            np.mean(
                [
                    _macro_f1_from_confusion(total[index, 1])
                    - _macro_f1_from_confusion(total[index, 0])
                    for index in range(len(SEEDS))
                ]
            )
        )

    estimate = effect(np.ones(len(unique), dtype=np.int64))
    generator = np.random.default_rng(seed)
    distribution = []
    for _ in range(n_resamples):
        sampled = generator.integers(0, len(unique), size=len(unique))
        distribution.append(effect(np.bincount(sampled, minlength=len(unique))))
    return _finish_bootstrap(
        distribution,
        estimate=estimate,
        confidence_level=confidence_level,
        n_resamples=n_resamples,
        n_speakers=len(unique),
        seed=seed,
    )


def _icc_sufficient_by_speaker(
    inverse: np.ndarray,
    n_speakers: int,
    targets: np.ndarray,
    predictions: np.ndarray,
) -> np.ndarray:
    means = (targets + predictions) / 2.0
    values = np.column_stack(
        (
            np.ones(len(targets), dtype=np.float64),
            targets + predictions,
            targets**2 + predictions**2,
            means,
            means**2,
            targets,
            predictions,
        )
    )
    output = np.zeros((n_speakers, 7), dtype=np.float64)
    for column in range(7):
        np.add.at(output[:, column], inverse, values[:, column])
    return output


def _icc_from_sufficient(values: np.ndarray) -> float:
    (
        n_targets,
        sum_total,
        sum_sq_total,
        sum_means,
        sum_mean_squares,
        sum_targets,
        sum_predictions,
    ) = (float(value) for value in values)
    if n_targets < 2:
        return float("nan")
    grand_mean = sum_total / (2.0 * n_targets)
    ss_total = sum_sq_total - 2 * grand_mean * sum_total + 2 * n_targets * grand_mean**2
    ss_targets = 2 * (
        sum_mean_squares - 2 * grand_mean * sum_means + n_targets * grand_mean**2
    )
    target_mean = sum_targets / n_targets
    prediction_mean = sum_predictions / n_targets
    ss_raters = n_targets * (
        (target_mean - grand_mean) ** 2 + (prediction_mean - grand_mean) ** 2
    )
    ss_error = ss_total - ss_targets - ss_raters
    scale = max(abs(ss_total), abs(ss_targets), abs(ss_raters), 1.0)
    if ss_error < 0 and abs(ss_error) <= np.finfo(float).eps * scale * 32:
        ss_error = 0.0
    ms_targets = ss_targets / (n_targets - 1)
    ms_raters = ss_raters
    ms_error = ss_error / (n_targets - 1)
    denominator = ms_targets + ms_error + 2.0 * (ms_raters - ms_error) / n_targets
    if np.isclose(denominator, 0.0):
        return float("nan")
    return float((ms_targets - ms_error) / denominator)


def paired_utterance_icc_bootstrap(
    *,
    speaker_ids: np.ndarray,
    targets: np.ndarray,
    control_predictions: Mapping[int, np.ndarray],
    candidate_predictions: Mapping[int, np.ndarray],
    n_resamples: int = 5_000,
    confidence_level: float = 0.95,
    seed: int = 42,
) -> BootstrapResult:
    """Candidate-minus-control ICC with paired speaker resampling."""

    speakers = np.asarray(speaker_ids)
    truth = np.asarray(targets, dtype=np.float64)
    unique, inverse = np.unique(speakers, return_inverse=True)
    if unique.size < 2 or truth.ndim != 1 or truth.size != speakers.size:
        raise S105TrainingError("utterance bootstrap inputs are invalid")
    if not np.all(np.isfinite(truth)):
        raise S105TrainingError("utterance bootstrap target contains non-finite values")
    sufficient = np.zeros((len(unique), len(SEEDS), 2, 7), dtype=np.float64)
    for seed_index, seed_value in enumerate(SEEDS):
        for variant_index, prediction_map in enumerate(
            (control_predictions, candidate_predictions)
        ):
            predictions = np.asarray(prediction_map[seed_value], dtype=np.float64)
            if predictions.shape != truth.shape or not np.all(np.isfinite(predictions)):
                raise S105TrainingError("utterance bootstrap prediction shape differs")
            sufficient[:, seed_index, variant_index, :] = _icc_sufficient_by_speaker(
                inverse,
                len(unique),
                truth,
                predictions,
            )

    def effect(weights: np.ndarray) -> float:
        total = np.tensordot(weights, sufficient, axes=(0, 0))
        return float(
            np.mean(
                [
                    _icc_from_sufficient(total[index, 1])
                    - _icc_from_sufficient(total[index, 0])
                    for index in range(len(SEEDS))
                ]
            )
        )

    estimate = effect(np.ones(len(unique), dtype=np.int64))
    generator = np.random.default_rng(seed)
    distribution: list[float] = []
    for _ in range(n_resamples):
        sampled = generator.integers(0, len(unique), size=len(unique))
        value = effect(np.bincount(sampled, minlength=len(unique)))
        if math.isfinite(value):
            distribution.append(value)
    return _finish_bootstrap(
        distribution,
        estimate=estimate,
        confidence_level=confidence_level,
        n_resamples=n_resamples,
        n_speakers=len(unique),
        seed=seed,
    )


def subgroup_worst_group_drop(
    *,
    speaker_ids: np.ndarray,
    targets: np.ndarray,
    groups_by_axis: Mapping[str, np.ndarray],
    control_predictions: Mapping[int, np.ndarray],
    candidate_predictions: Mapping[int, np.ndarray],
    fixed_groups: Mapping[str, Sequence[str]],
    minimum_unique_speakers: int,
    minimum_positive_phone_rows: int,
    minimum_negative_phone_rows: int,
    threshold_exclusive: float,
) -> SubgroupResult:
    """Compute max(control F1 - candidate F1) over preregistered eligible groups."""

    speakers = np.asarray(speaker_ids)
    truth = np.asarray(targets, dtype=np.int8)
    rows: list[Mapping[str, Any]] = []
    eligible_drops: list[float] = []
    for axis in ("l1", "gender", "proficiency"):
        groups = np.asarray(groups_by_axis[axis])
        if groups.shape != truth.shape:
            raise S105TrainingError("subgroup vector shape differs")
        for group in fixed_groups[axis]:
            selected = groups == group
            selected_truth = truth[selected]
            speaker_count = int(np.unique(speakers[selected]).size)
            positive = int(np.count_nonzero(selected_truth == 1))
            negative = int(np.count_nonzero(selected_truth == 0))
            eligible = (
                speaker_count >= minimum_unique_speakers
                and positive >= minimum_positive_phone_rows
                and negative >= minimum_negative_phone_rows
            )
            control_values: list[float] = []
            candidate_values: list[float] = []
            if eligible:
                for seed_value in SEEDS:
                    control_values.append(
                        float(
                            macro_f1(
                                selected_truth,
                                np.asarray(control_predictions[seed_value])[selected],
                            )
                        )
                    )
                    candidate_values.append(
                        float(
                            macro_f1(
                                selected_truth,
                                np.asarray(candidate_predictions[seed_value])[selected],
                            )
                        )
                    )
                drop = float(np.mean(control_values) - np.mean(candidate_values))
                eligible_drops.append(drop)
            else:
                drop = float("nan")
            rows.append(
                {
                    "axis": axis,
                    "group": group,
                    "unique_speakers": speaker_count,
                    "positive_phone_rows": positive,
                    "negative_phone_rows": negative,
                    "eligible": eligible,
                    "control_phone_macro_f1_mean": (
                        float(np.mean(control_values)) if control_values else None
                    ),
                    "candidate_phone_macro_f1_mean": (
                        float(np.mean(candidate_values)) if candidate_values else None
                    ),
                    "control_minus_candidate": (drop if math.isfinite(drop) else None),
                }
            )
    if not eligible_drops:
        raise S105TrainingError("no subgroup is eligible for the adoption gate")
    worst = max(eligible_drops)
    return SubgroupResult(
        groups=tuple(rows),
        eligible_group_count=len(eligible_drops),
        worst_group_drop=float(worst),
        threshold_exclusive=float(threshold_exclusive),
        passed=bool(worst < threshold_exclusive),
    )


def _bootstrap_mapping(result: BootstrapResult) -> Mapping[str, Any]:
    return {
        "estimate": result.estimate,
        "confidence_lower": result.confidence_lower,
        "confidence_upper": result.confidence_upper,
        "confidence_level": result.confidence_level,
        "n_resamples": result.n_resamples,
        "n_valid_resamples": result.n_valid_resamples,
        "n_speakers": result.n_speakers,
        "seed": result.seed,
        "p_value_two_sided_zero": result.p_value_two_sided_zero,
    }


def build_adoption_report(
    *,
    phone_bootstrap: BootstrapResult,
    utterance_bootstrap: BootstrapResult,
    subgroup: SubgroupResult,
    incremental_comparisons: Mapping[
        str,
        Mapping[str, BootstrapResult],
    ],
    minimum_absolute_improvement: float = 0.03,
    alpha: float = 0.05,
) -> Mapping[str, Any]:
    """Apply the preregistered point-estimate gate and report Holm separately."""

    correction = holm_correction(
        [
            phone_bootstrap.p_value_two_sided_zero,
            utterance_bootstrap.p_value_two_sided_zero,
        ],
        alpha=alpha,
    )
    primary = {
        "phone_macro_f1": {
            "paired_speaker_bootstrap": _bootstrap_mapping(phone_bootstrap),
            "holm_adjusted_p_value": float(correction.adjusted_p_values[0]),
            "holm_rejected": bool(correction.rejected[0]),
        },
        "comprehensibility_score_icc_2_1": {
            "paired_speaker_bootstrap": _bootstrap_mapping(utterance_bootstrap),
            "holm_adjusted_p_value": float(correction.adjusted_p_values[1]),
            "holm_rejected": bool(correction.rejected[1]),
        },
    }
    if set(incremental_comparisons) != set(ADJACENT_VARIANT_PAIRS):
        raise S105TrainingError("incremental comparison pairs differ")
    incremental_ordered: list[tuple[str, str, BootstrapResult]] = []
    for pair in ADJACENT_VARIANT_PAIRS:
        outcomes = incremental_comparisons[pair]
        if set(outcomes) != set(PRIMARY_OUTCOMES):
            raise S105TrainingError("incremental comparison outcomes differ")
        for outcome in PRIMARY_OUTCOMES:
            incremental_ordered.append((pair, outcome, outcomes[outcome]))
    incremental_correction = holm_correction(
        [result.p_value_two_sided_zero for _, _, result in incremental_ordered],
        alpha=alpha,
    )
    incremental_payload: dict[str, dict[str, Any]] = {
        pair: {} for pair in ADJACENT_VARIANT_PAIRS
    }
    for index, (pair, outcome, result) in enumerate(incremental_ordered):
        incremental_payload[pair][outcome] = {
            "paired_speaker_bootstrap": _bootstrap_mapping(result),
            "holm_adjusted_p_value": float(
                incremental_correction.adjusted_p_values[index]
            ),
            "holm_rejected": bool(incremental_correction.rejected[index]),
        }
    point_passed = bool(
        phone_bootstrap.estimate >= minimum_absolute_improvement
        or utterance_bootstrap.estimate >= minimum_absolute_improvement
    )
    adopted = bool(point_passed and subgroup.passed)
    return {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "status": "completed",
        "development_only": True,
        "oracle_alignment": True,
        "silver_label": True,
        "promotion_decision_allowed": False,
        "control_variant": "simple_head_control",
        "candidate_variant": "levelwise_multi_aspect_hierarchy",
        "primary_family": primary,
        "multiplicity": {
            "primary": {
                "method": "holm",
                "alpha": alpha,
                "family_size": 2,
                "significance_used_for_gate": False,
            },
            "incremental": {
                "method": "holm",
                "alpha": alpha,
                "family_size": 8,
                "significance_used_for_gate": False,
            },
        },
        "incremental_comparisons": incremental_payload,
        "subgroup": {
            "metric": "phone_macro_f1",
            "difference": "control_minus_candidate",
            "eligible_group_count": subgroup.eligible_group_count,
            "groups": list(subgroup.groups),
            "worst_group_drop": subgroup.worst_group_drop,
            "maximum_exclusive": subgroup.threshold_exclusive,
            "passed": subgroup.passed,
        },
        "adoption_gate": {
            "minimum_absolute_improvement": minimum_absolute_improvement,
            "rule": (
                "any_primary_point_estimate_gte_threshold_and_"
                "worst_group_drop_lt_threshold"
            ),
            "confidence_interval_use": "descriptive_not_gate",
            "p_value_use": "descriptive_not_gate",
            "point_improvement_passed": point_passed,
            "worst_group_drop_passed": subgroup.passed,
            "status": "passed" if adopted else "failed",
            "adopt_candidate": adopted,
        },
        "word_supervision": {
            "role": "unlabeled_intermediate_representation_only",
            "word_loss_performed": False,
            "word_pcc_status": "not_evaluable",
        },
        "leakage": {
            "train_inner_selection_only": True,
            "dev_passes_per_variant_task_seed": 1,
            "dev_used_for_selection": False,
            "stack_accessed": False,
            "calibration_accessed": False,
            "locked_test_accessed": False,
        },
    }


def phone_prediction_schema() -> pa.Schema:
    """Exact persisted schema for one variant/seed phone dev prediction."""

    return pa.schema(
        [
            pa.field("schema_version", pa.string(), nullable=False),
            pa.field("variant", pa.string(), nullable=False),
            pa.field("seed", pa.int32(), nullable=False),
            pa.field("split", pa.string(), nullable=False),
            pa.field("development_only", pa.bool_(), nullable=False),
            pa.field("oracle_alignment", pa.bool_(), nullable=False),
            pa.field("utterance_id_sha256", pa.string(), nullable=False),
            pa.field("speaker_id_sha256", pa.string(), nullable=False),
            pa.field("phone_index", pa.int32(), nullable=False),
            pa.field("target_label", pa.string(), nullable=False),
            pa.field("binary_error_label", pa.int8(), nullable=False),
            pa.field("error_probability", pa.float32(), nullable=False),
            pa.field("error_prediction", pa.int8(), nullable=False),
            pa.field("l1", pa.string(), nullable=False),
            pa.field("gender", pa.string(), nullable=False),
            pa.field("proficiency", pa.string(), nullable=False),
        ],
        metadata={
            b"voice_coach.development_only": b"true",
            b"voice_coach.oracle_alignment": b"true",
            b"voice_coach.allowed_split": b"dev",
            b"voice_coach.dev_inference_passes": b"1",
            b"voice_coach.raw_locator_columns_written": b"false",
            b"voice_coach.access_scope": b"restricted_internal",
        },
    )


def utterance_prediction_schema() -> pa.Schema:
    """Exact persisted schema for one variant/seed utterance dev prediction."""

    fields = [
        pa.field("schema_version", pa.string(), nullable=False),
        pa.field("variant", pa.string(), nullable=False),
        pa.field("seed", pa.int32(), nullable=False),
        pa.field("split", pa.string(), nullable=False),
        pa.field("development_only", pa.bool_(), nullable=False),
        pa.field("oracle_alignment", pa.bool_(), nullable=False),
        pa.field("utterance_id_sha256", pa.string(), nullable=False),
        pa.field("speaker_id_sha256", pa.string(), nullable=False),
        pa.field("l1", pa.string(), nullable=False),
        pa.field("gender", pa.string(), nullable=False),
        pa.field("proficiency", pa.string(), nullable=False),
    ]
    for outcome in OUTCOMES:
        fields.extend(
            [
                pa.field(f"{outcome}_true", pa.float32()),
                pa.field(f"{outcome}_pred", pa.float32(), nullable=False),
                pa.field(f"{outcome}_observed", pa.bool_(), nullable=False),
            ]
        )
    return pa.schema(
        fields,
        metadata={
            b"voice_coach.development_only": b"true",
            b"voice_coach.oracle_alignment": b"true",
            b"voice_coach.allowed_split": b"dev",
            b"voice_coach.dev_inference_passes": b"1",
            b"voice_coach.raw_locator_columns_written": b"false",
            b"voice_coach.access_scope": b"restricted_internal",
        },
    )


class HierarchyPredictionWriters:
    """Bounded row-group writers used by the shared physical dev scan."""

    def __init__(
        self,
        *,
        phone_path: Path,
        utterance_path: Path,
        row_group_size: int = 8_192,
    ) -> None:
        if row_group_size <= 0:
            raise S105TrainingError("prediction row-group size must be positive")
        self.phone_path = phone_path
        self.utterance_path = utterance_path
        self.row_group_size = row_group_size
        self._phone_rows: list[dict[str, Any]] = []
        self._utterance_rows: list[dict[str, Any]] = []
        self._phone_writer = pq.ParquetWriter(
            phone_path,
            phone_prediction_schema(),
            compression="zstd",
        )
        self._utterance_writer = pq.ParquetWriter(
            utterance_path,
            utterance_prediction_schema(),
            compression="zstd",
        )
        self._closed = False

    def add_phone(self, row: Mapping[str, Any]) -> None:
        self._phone_rows.append(dict(row))
        if len(self._phone_rows) >= self.row_group_size:
            self._flush_phone()

    def add_utterance(self, row: Mapping[str, Any]) -> None:
        self._utterance_rows.append(dict(row))
        if len(self._utterance_rows) >= self.row_group_size:
            self._flush_utterance()

    def _flush_phone(self) -> None:
        if self._phone_rows:
            self._phone_writer.write_table(
                pa.Table.from_pylist(
                    self._phone_rows,
                    schema=phone_prediction_schema(),
                )
            )
            self._phone_rows.clear()

    def _flush_utterance(self) -> None:
        if self._utterance_rows:
            self._utterance_writer.write_table(
                pa.Table.from_pylist(
                    self._utterance_rows,
                    schema=utterance_prediction_schema(),
                )
            )
            self._utterance_rows.clear()

    def close(self) -> None:
        if self._closed:
            return
        self._flush_phone()
        self._flush_utterance()
        self._phone_writer.close()
        self._utterance_writer.close()
        self._closed = True

    def __enter__(self) -> HierarchyPredictionWriters:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, (np.floating, float)):
        result = float(value)
        return result if math.isfinite(result) else None
    return value


def _load_json(path: Path, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite token: {token}")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise S105TrainingError(f"{name} is invalid JSON") from error
    if not isinstance(value, Mapping):
        raise S105TrainingError(f"{name} must be a JSON object")
    return cast(Mapping[str, Any], value)


def _aggregate_metric(values: Sequence[float | None]) -> Mapping[str, Any]:
    finite = np.asarray(
        [
            float(value)
            for value in values
            if value is not None and math.isfinite(float(value))
        ],
        dtype=np.float64,
    )
    return {
        "mean": float(finite.mean()) if finite.size else None,
        "sample_std": float(finite.std(ddof=1)) if finite.size > 1 else None,
    }


def build_variant_report(
    *,
    generation: Path,
    variant: str,
) -> Mapping[str, Any]:
    """Recompute a variant aggregate only from persisted seed evidence."""

    from voice_coach.training.wavlm_adapter_training import (
        metric_bundle_from_prediction_files,
    )

    if variant not in VARIANT_ORDER:
        raise S105TrainingError("variant report name is not preregistered")
    metrics_by_seed: dict[str, Any] = {}
    thresholds: dict[str, float] = {}
    best_epochs: dict[str, int] = {}
    phone_values: list[float] = []
    comprehension_values: list[float | None] = []
    for seed in SEEDS:
        seed_root = generation / variant / f"seed_{seed}"
        resource = _load_json(seed_root / "resource_report.json", "resource report")
        selection = resource.get("selection")
        if not isinstance(selection, Mapping):
            raise S105TrainingError("selection evidence is absent")
        threshold = _finite(selection.get("phone_decision_threshold"), "threshold")
        best_epoch = selection.get("best_epoch")
        if (
            not 0 <= threshold <= 1
            or isinstance(best_epoch, bool)
            or not isinstance(best_epoch, int)
            or best_epoch <= 0
        ):
            raise S105TrainingError("selection evidence is invalid")
        raw_metrics = metric_bundle_from_prediction_files(
            phone_path=seed_root / "phone_dev_predictions.parquet",
            utterance_path=seed_root / "utterance_dev_predictions.parquet",
            threshold=threshold,
        )
        metrics = cast(Mapping[str, Any], _json_safe(raw_metrics))
        metrics_by_seed[str(seed)] = metrics
        thresholds[str(seed)] = threshold
        best_epochs[str(seed)] = best_epoch
        phone = cast(Mapping[str, Any], metrics["phone"])
        utterance = cast(Mapping[str, Any], metrics["utterance"])
        outcomes = cast(Mapping[str, Any], utterance["outcomes"])
        comprehension = cast(
            Mapping[str, Any],
            outcomes["comprehensibility_score"],
        )
        phone_values.append(float(phone["macro_f1"]))
        icc_value = comprehension.get("icc_2_1")
        comprehension_values.append(
            float(icc_value) if isinstance(icc_value, (int, float)) else None
        )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "completed",
        "development_only": True,
        "oracle_alignment": True,
        "silver_label": True,
        "promotion_decision_allowed": False,
        "variant": variant,
        "seeds": list(SEEDS),
        "dev_passes_per_task_seed": 1,
        "metrics_by_seed": metrics_by_seed,
        "aggregate": {
            "phone_macro_f1": _aggregate_metric(phone_values),
            "comprehensibility_score_icc_2_1": _aggregate_metric(comprehension_values),
        },
        "phone_decision_thresholds": thresholds,
        "best_epochs": best_epochs,
        "word_loss_performed": False,
        "word_pcc_status": "not_evaluable",
        "stack_accessed": False,
        "calibration_accessed": False,
        "locked_test_accessed": False,
    }


def _prediction_arrays(
    generation: Path,
) -> Mapping[str, Any]:
    """Load the small comparison vectors while preserving exact row order."""

    phone_identity: list[tuple[Any, ...]] | None = None
    utterance_identity: list[tuple[Any, ...]] | None = None
    phone_targets: np.ndarray | None = None
    phone_speakers: np.ndarray | None = None
    phone_groups: dict[str, np.ndarray] = {}
    utterance_targets: np.ndarray | None = None
    utterance_speakers: np.ndarray | None = None
    utterance_observed: np.ndarray | None = None
    phone_predictions: dict[str, dict[int, np.ndarray]] = {
        str(variant): {} for variant in VARIANT_ORDER
    }
    utterance_predictions: dict[str, dict[int, np.ndarray]] = {
        str(variant): {} for variant in VARIANT_ORDER
    }
    for variant in VARIANT_ORDER:
        for seed in SEEDS:
            root = generation / variant / f"seed_{seed}"
            checkpoint = root / "hierarchy_head.safetensors"
            with safe_open(checkpoint, framework="pt") as handle:
                metadata = handle.metadata()
            try:
                threshold = float(
                    cast(Mapping[str, str], metadata)["phone_decision_threshold"]
                )
            except (KeyError, TypeError, ValueError) as error:
                raise S105TrainingError("checkpoint threshold is invalid") from error
            phone = pq.read_table(
                root / "phone_dev_predictions.parquet",
                use_threads=False,
            ).to_pylist()
            utterance = pq.read_table(
                root / "utterance_dev_predictions.parquet",
                use_threads=False,
            ).to_pylist()
            current_phone_identity = [
                (
                    row["utterance_id_sha256"],
                    row["speaker_id_sha256"],
                    row["phone_index"],
                    row["target_label"],
                    row["binary_error_label"],
                    row["l1"],
                    row["gender"],
                    row["proficiency"],
                )
                for row in phone
            ]
            current_utterance_identity = [
                (
                    row["utterance_id_sha256"],
                    row["speaker_id_sha256"],
                    row["l1"],
                    row["gender"],
                    row["proficiency"],
                    *(
                        value
                        for outcome in OUTCOMES
                        for value in (
                            row[f"{outcome}_true"],
                            row[f"{outcome}_observed"],
                        )
                    ),
                )
                for row in utterance
            ]
            if phone_identity is None:
                phone_identity = current_phone_identity
                utterance_identity = current_utterance_identity
                phone_targets = np.asarray(
                    [row["binary_error_label"] for row in phone],
                    dtype=np.int8,
                )
                phone_speakers = np.asarray([row["speaker_id_sha256"] for row in phone])
                phone_groups = {
                    axis: np.asarray([row[axis] for row in phone])
                    for axis in ("l1", "gender", "proficiency")
                }
                utterance_targets = np.asarray(
                    [row["comprehensibility_score_true"] for row in utterance],
                    dtype=np.float64,
                )
                utterance_observed = np.asarray(
                    [row["comprehensibility_score_observed"] for row in utterance],
                    dtype=np.bool_,
                )
                utterance_speakers = np.asarray(
                    [row["speaker_id_sha256"] for row in utterance]
                )
            elif (
                current_phone_identity != phone_identity
                or current_utterance_identity != utterance_identity
            ):
                raise S105TrainingError(
                    "prediction identity/order differs across variants or seeds"
                )
            probabilities = np.asarray(
                [row["error_probability"] for row in phone],
                dtype=np.float32,
            )
            persisted = np.asarray(
                [row["error_prediction"] for row in phone],
                dtype=np.int8,
            )
            derived = (probabilities >= threshold).astype(np.int8)
            if not np.array_equal(persisted, derived):
                raise S105TrainingError(
                    "persisted phone decision differs from checkpoint threshold"
                )
            phone_predictions[str(variant)][seed] = derived
            utterance_predictions[str(variant)][seed] = np.asarray(
                [row["comprehensibility_score_pred"] for row in utterance],
                dtype=np.float64,
            )
    if (
        phone_targets is None
        or phone_speakers is None
        or utterance_targets is None
        or utterance_speakers is None
        or utterance_observed is None
    ):
        raise S105TrainingError("comparison prediction evidence is empty")
    if not np.any(utterance_observed):
        raise S105TrainingError("comprehensibility comparison has no observations")
    return {
        "phone_targets": phone_targets,
        "phone_speakers": phone_speakers,
        "phone_groups": phone_groups,
        "utterance_targets": utterance_targets[utterance_observed],
        "utterance_speakers": utterance_speakers[utterance_observed],
        "phone_predictions": phone_predictions,
        "utterance_predictions": {
            variant: {
                seed: values[utterance_observed] for seed, values in predictions.items()
            }
            for variant, predictions in utterance_predictions.items()
        },
    }


def recompute_ablation_comparison(
    *,
    generation: Path,
    config: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Recompute every paired effect, Holm family, subgroup, and gate."""

    values = _prediction_arrays(generation)
    adoption = cast(Mapping[str, Any], config["adoption"])
    bootstrap = cast(Mapping[str, Any], adoption["bootstrap"])
    subgroup_config = cast(Mapping[str, Any], adoption["subgroup"])
    phone_predictions = cast(
        Mapping[str, Mapping[int, np.ndarray]],
        values["phone_predictions"],
    )
    utterance_predictions = cast(
        Mapping[str, Mapping[int, np.ndarray]],
        values["utterance_predictions"],
    )
    n_resamples = int(bootstrap["n_resamples"])
    confidence_level = float(bootstrap["confidence_level"])
    bootstrap_seed = int(bootstrap["seed"])

    def phone_effect(control: str, candidate: str) -> BootstrapResult:
        return paired_phone_bootstrap(
            speaker_ids=cast(np.ndarray, values["phone_speakers"]),
            targets=cast(np.ndarray, values["phone_targets"]),
            control_predictions=phone_predictions[control],
            candidate_predictions=phone_predictions[candidate],
            n_resamples=n_resamples,
            confidence_level=confidence_level,
            seed=bootstrap_seed,
        )

    def utterance_effect(control: str, candidate: str) -> BootstrapResult:
        return paired_utterance_icc_bootstrap(
            speaker_ids=cast(np.ndarray, values["utterance_speakers"]),
            targets=cast(np.ndarray, values["utterance_targets"]),
            control_predictions=utterance_predictions[control],
            candidate_predictions=utterance_predictions[candidate],
            n_resamples=n_resamples,
            confidence_level=confidence_level,
            seed=bootstrap_seed,
        )

    control = str(adoption["control_variant"])
    candidate = str(adoption["candidate_variant"])
    incremental: dict[str, Mapping[str, BootstrapResult]] = {}
    for left, right in zip(VARIANT_ORDER[:-1], VARIANT_ORDER[1:], strict=True):
        pair = f"{left}__to__{right}"
        incremental[pair] = {
            "phone_macro_f1": phone_effect(str(left), str(right)),
            "comprehensibility_score_icc_2_1": utterance_effect(
                str(left),
                str(right),
            ),
        }
    subgroup = subgroup_worst_group_drop(
        speaker_ids=cast(np.ndarray, values["phone_speakers"]),
        targets=cast(np.ndarray, values["phone_targets"]),
        groups_by_axis=cast(
            Mapping[str, np.ndarray],
            values["phone_groups"],
        ),
        control_predictions=phone_predictions[control],
        candidate_predictions=phone_predictions[candidate],
        fixed_groups=cast(
            Mapping[str, Sequence[str]],
            subgroup_config["fixed_groups"],
        ),
        minimum_unique_speakers=int(subgroup_config["minimum_unique_speakers"]),
        minimum_positive_phone_rows=int(subgroup_config["minimum_positive_phone_rows"]),
        minimum_negative_phone_rows=int(subgroup_config["minimum_negative_phone_rows"]),
        threshold_exclusive=float(subgroup_config["maximum_exclusive"]),
    )
    return build_adoption_report(
        phone_bootstrap=phone_effect(control, candidate),
        utterance_bootstrap=utterance_effect(control, candidate),
        subgroup=subgroup,
        incremental_comparisons=incremental,
        minimum_absolute_improvement=float(adoption["minimum_absolute_improvement"]),
        alpha=float(cast(Mapping[str, Any], adoption["multiplicity"])["primary_alpha"]),
    )


def render_ablation_table(
    *,
    generation: Path,
    comparison: Mapping[str, Any],
) -> str:
    """Render the deterministic development-only table bound by validation."""

    lines = [
        "# S105 development-only hierarchy ablation",
        "",
        "| Variant | Phone macro-F1 mean | Comprehensibility ICC(2,1) mean |",
        "|---|---:|---:|",
    ]
    for variant in VARIANT_ORDER:
        report = build_variant_report(generation=generation, variant=str(variant))
        aggregate = cast(Mapping[str, Any], report["aggregate"])
        phone = cast(Mapping[str, Any], aggregate["phone_macro_f1"])["mean"]
        icc = cast(
            Mapping[str, Any],
            aggregate["comprehensibility_score_icc_2_1"],
        )["mean"]

        def formatted(value: object) -> str:
            return "NA" if value is None else f"{float(cast(float, value)):.6f}"

        lines.append(f"| {variant} | {formatted(phone)} | {formatted(icc)} |")
    gate = cast(Mapping[str, Any], comparison["adoption_gate"])
    lines.extend(
        [
            "",
            f"Adoption gate: **{gate['status']}**.",
            "",
            (
                "Development-only, oracle-alignment, silver-label evidence; "
                "not a promotion decision."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def expected_artifact_layout() -> tuple[tuple[str, str], ...]:
    output: list[tuple[str, str]] = []
    for variant in VARIANT_ORDER:
        for seed in SEEDS:
            root = f"{variant}/seed_{seed}"
            output.extend(
                [
                    (
                        f"{variant}_seed_{seed}_hierarchy_checkpoint",
                        f"{root}/hierarchy_head.safetensors",
                    ),
                    (
                        f"{variant}_seed_{seed}_phone_dev_predictions",
                        f"{root}/phone_dev_predictions.parquet",
                    ),
                    (
                        f"{variant}_seed_{seed}_utterance_dev_predictions",
                        f"{root}/utterance_dev_predictions.parquet",
                    ),
                    (
                        f"{variant}_seed_{seed}_dev_slice_metrics",
                        f"{root}/dev_slice_metrics.json",
                    ),
                    (
                        f"{variant}_seed_{seed}_resource_report",
                        f"{root}/resource_report.json",
                    ),
                ]
            )
        output.append(
            (
                f"{variant}_variant_report",
                f"{variant}/variant_report.json",
            )
        )
    output.extend(
        [
            ("ablation_comparison_report", "ablation_comparison_report.json"),
            ("ablation_table", "ablation_table.md"),
        ]
    )
    if len(output) != 82:
        raise AssertionError("S105 artifact layout cardinality changed")
    return tuple(output)


def artifact_records(root: Path) -> tuple[Mapping[str, Any], ...]:
    expected = expected_artifact_layout()
    expected_files = {relative for _, relative in expected}
    expected_directories = {str(variant) for variant in VARIANT_ORDER} | {
        f"{variant}/seed_{seed}" for variant in VARIANT_ORDER for seed in SEEDS
    }
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise S105TrainingError("S105 artifact tree contains a symlink")
        relative = path.relative_to(root).as_posix()
        if path.is_file():
            actual_files.add(relative)
        elif path.is_dir():
            actual_directories.add(relative)
        else:
            raise S105TrainingError("S105 artifact tree node type differs")
    if actual_files != expected_files or actual_directories != expected_directories:
        raise S105TrainingError("S105 artifact tree differs")
    records: list[Mapping[str, Any]] = []
    for name, relative in expected:
        path = root / relative
        if path.stat().st_size <= 0:
            raise S105TrainingError("S105 artifact is empty")
        records.append(
            {
                "name": name,
                "path": relative,
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return tuple(records)


def _validate_bootstrap_payload(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise S105TrainingError(f"{name} bootstrap is absent")
    payload = cast(Mapping[str, Any], value)
    if set(payload) != {
        "estimate",
        "confidence_lower",
        "confidence_upper",
        "confidence_level",
        "n_resamples",
        "n_valid_resamples",
        "n_speakers",
        "seed",
        "p_value_two_sided_zero",
    }:
        raise S105TrainingError(f"{name} bootstrap schema differs")
    estimate = _finite(payload.get("estimate"), f"{name}.estimate")
    lower = _finite(payload.get("confidence_lower"), f"{name}.lower")
    upper = _finite(payload.get("confidence_upper"), f"{name}.upper")
    p_value = _finite(payload.get("p_value_two_sided_zero"), f"{name}.p")
    if (
        lower > estimate
        or estimate > upper
        or payload.get("confidence_level") != 0.95
        or payload.get("n_resamples") != 5000
        or not isinstance(payload.get("n_valid_resamples"), int)
        or not 0 < int(payload["n_valid_resamples"]) <= 5000
        or not isinstance(payload.get("n_speakers"), int)
        or int(payload["n_speakers"]) < 2
        or payload.get("seed") != 42
        or not 0 <= p_value <= 1
    ):
        raise S105TrainingError(f"{name} bootstrap contract differs")
    return payload


def _validate_holm_family(
    family: Mapping[str, Mapping[str, Any]],
    *,
    expected_size: int,
    name: str,
) -> None:
    flattened = list(family.values())
    if len(flattened) != expected_size:
        raise S105TrainingError(f"{name} Holm family size differs")
    raw = [
        float(
            cast(Mapping[str, Any], item["paired_speaker_bootstrap"])[
                "p_value_two_sided_zero"
            ]
        )
        for item in flattened
    ]
    correction = holm_correction(raw, alpha=0.05)
    for index, item in enumerate(flattened):
        if not math.isclose(
            _finite(item.get("holm_adjusted_p_value"), f"{name}.adjusted_p"),
            float(correction.adjusted_p_values[index]),
            rel_tol=0.0,
            abs_tol=1e-15,
        ) or item.get("holm_rejected") is not bool(correction.rejected[index]):
            raise S105TrainingError(f"{name} Holm recomputation differs")


def validate_comparison_report(report: Mapping[str, Any]) -> None:
    """Validate the aggregate policy fields without trusting a renderer."""

    if (
        report.get("schema_version") != COMPARISON_SCHEMA_VERSION
        or report.get("status") != "completed"
        or report.get("development_only") is not True
        or report.get("oracle_alignment") is not True
        or report.get("silver_label") is not True
        or report.get("promotion_decision_allowed") is not False
    ):
        raise S105TrainingError("S105 comparison status contract differs")
    word = report.get("word_supervision")
    if (
        not isinstance(word, Mapping)
        or word.get("role") != "unlabeled_intermediate_representation_only"
        or word.get("word_loss_performed") is not False
        or word.get("word_pcc_status") != "not_evaluable"
    ):
        raise S105TrainingError("S105 word-supervision boundary differs")
    leakage = report.get("leakage")
    if (
        not isinstance(leakage, Mapping)
        or leakage.get("train_inner_selection_only") is not True
        or leakage.get("dev_passes_per_variant_task_seed") != 1
        or leakage.get("dev_used_for_selection") is not False
        or leakage.get("stack_accessed") is not False
        or leakage.get("calibration_accessed") is not False
        or leakage.get("locked_test_accessed") is not False
    ):
        raise S105TrainingError("S105 leakage contract differs")
    gate = report.get("adoption_gate")
    subgroup = report.get("subgroup")
    if not isinstance(gate, Mapping) or not isinstance(subgroup, Mapping):
        raise S105TrainingError("S105 adoption payload is absent")
    primary = report.get("primary_family")
    if not isinstance(primary, Mapping) or set(primary) != set(PRIMARY_OUTCOMES):
        raise S105TrainingError("S105 primary family differs")
    primary_items: dict[str, Mapping[str, Any]] = {}
    for outcome in PRIMARY_OUTCOMES:
        item = cast(Mapping[str, Any], primary[outcome])
        bootstrap = _validate_bootstrap_payload(
            item.get("paired_speaker_bootstrap"),
            f"primary.{outcome}",
        )
        primary_items[outcome] = {
            **item,
            "paired_speaker_bootstrap": bootstrap,
        }
    _validate_holm_family(primary_items, expected_size=2, name="primary")
    incremental = report.get("incremental_comparisons")
    if not isinstance(incremental, Mapping) or set(incremental) != set(
        ADJACENT_VARIANT_PAIRS
    ):
        raise S105TrainingError("S105 incremental family differs")
    incremental_items: dict[str, Mapping[str, Any]] = {}
    for pair in ADJACENT_VARIANT_PAIRS:
        outcomes = cast(Mapping[str, Any], incremental[pair])
        if set(outcomes) != set(PRIMARY_OUTCOMES):
            raise S105TrainingError("S105 incremental outcome order differs")
        for outcome in PRIMARY_OUTCOMES:
            item = cast(Mapping[str, Any], outcomes[outcome])
            _validate_bootstrap_payload(
                item.get("paired_speaker_bootstrap"),
                f"incremental.{pair}.{outcome}",
            )
            incremental_items[f"{pair}:{outcome}"] = item
    _validate_holm_family(
        incremental_items,
        expected_size=8,
        name="incremental",
    )
    multiplicity = report.get("multiplicity")
    if not isinstance(multiplicity, Mapping) or dict(multiplicity) != {
        "primary": {
            "method": "holm",
            "alpha": 0.05,
            "family_size": 2,
            "significance_used_for_gate": False,
        },
        "incremental": {
            "method": "holm",
            "alpha": 0.05,
            "family_size": 8,
            "significance_used_for_gate": False,
        },
    }:
        raise S105TrainingError("S105 multiplicity policy differs")
    point_passed = bool(
        float(
            cast(
                Mapping[str, Any],
                primary_items["phone_macro_f1"]["paired_speaker_bootstrap"],
            )["estimate"]
        )
        >= 0.03
        or float(
            cast(
                Mapping[str, Any],
                primary_items["comprehensibility_score_icc_2_1"][
                    "paired_speaker_bootstrap"
                ],
            )["estimate"]
        )
        >= 0.03
    )
    groups = subgroup.get("groups")
    if not isinstance(groups, list):
        raise S105TrainingError("S105 subgroup rows are absent")
    eligible_drops = [
        _finite(item.get("control_minus_candidate"), "subgroup drop")
        for item in groups
        if isinstance(item, Mapping) and item.get("eligible") is True
    ]
    if not eligible_drops or subgroup.get("eligible_group_count") != len(
        eligible_drops
    ):
        raise S105TrainingError("S105 eligible subgroup count differs")
    drop = _finite(subgroup.get("worst_group_drop"), "worst_group_drop")
    if not math.isclose(drop, max(eligible_drops), rel_tol=0.0, abs_tol=1e-15):
        raise S105TrainingError("S105 worst_group_drop recomputation differs")
    drop_passed = bool(drop < 0.05)
    adopted = point_passed and drop_passed
    if (
        gate.get("point_improvement_passed") is not point_passed
        or subgroup.get("maximum_exclusive") != 0.05
        or subgroup.get("passed") is not drop_passed
        or gate.get("worst_group_drop_passed") is not drop_passed
        or gate.get("status") != ("passed" if adopted else "failed")
        or gate.get("adopt_candidate") is not adopted
        or gate.get("minimum_absolute_improvement") != 0.03
        or gate.get("confidence_interval_use") != "descriptive_not_gate"
        or gate.get("p_value_use") != "descriptive_not_gate"
    ):
        raise S105TrainingError("S105 adoption gate recomputation differs")


def _expected_checkpoint_record(
    execution_lock: Mapping[str, Any],
    seed: int,
) -> Mapping[str, Any]:
    predecessor = execution_lock.get("predecessor")
    if not isinstance(predecessor, Mapping):
        raise S105TrainingError("execution lock predecessor is absent")
    records = predecessor.get("same_seed_checkpoints")
    if not isinstance(records, list):
        raise S105TrainingError("same-seed checkpoint bindings are absent")
    matches = [
        item
        for item in records
        if isinstance(item, Mapping) and item.get("seed") == seed
    ]
    if len(matches) != 1 or not _is_sha256(matches[0].get("sha256")):
        raise S105TrainingError("same-seed checkpoint binding differs")
    return cast(Mapping[str, Any], matches[0])


def _fit_metadata_payload(metadata: Any) -> Mapping[str, Any]:
    try:
        vocabulary = list(metadata.target_vocabulary)
        positive = int(metadata.positive_phone_rows)
        negative = int(metadata.negative_phone_rows)
        pos_weight = float(metadata.pos_weight)
        utterances = int(metadata.fit_utterances)
    except (AttributeError, TypeError, ValueError) as error:
        raise S105TrainingError("fit metadata source is invalid") from error
    if (
        not vocabulary
        or vocabulary[0] != "<UNK>"
        or len(set(vocabulary)) != len(vocabulary)
        or positive <= 0
        or negative <= 0
        or utterances <= 0
        or not math.isfinite(pos_weight)
        or pos_weight != negative / positive
    ):
        raise S105TrainingError("fit metadata source contract differs")
    return {
        "target_vocabulary": vocabulary,
        "positive_phone_rows": positive,
        "negative_phone_rows": negative,
        "pos_weight": pos_weight,
        "fit_utterances": utterances,
    }


def _validate_checkpoint_and_resource(
    *,
    seed_root: Path,
    variant: str,
    seed: int,
    context: DeepValidationContext,
) -> float:
    config = context.config
    execution_lock = context.execution_lock
    predecessor = cast(Mapping[str, Any], execution_lock["predecessor"])
    selected = cast(Mapping[str, Any], predecessor["selected_candidate"])
    dimensions = cast(
        Mapping[str, Any],
        cast(Mapping[str, Any], config["variants"])["dimensions"],
    )
    fit_metadata = _fit_metadata_payload(context.expected_fit_metadata)
    checkpoint_path = seed_root / "hierarchy_head.safetensors"
    with safe_open(checkpoint_path, framework="pt") as handle:
        keys = tuple(handle.keys())
        metadata = handle.metadata()
        tensors = {name: handle.get_tensor(name) for name in keys}
    expected_model = IncrementalHierarchyHead(
        variant=cast(Any, variant),
        hidden_size=int(selected["hidden_size"]),
        encoder_layer_count=int(selected["encoder_layer_count"]),
        projection_dim=int(dimensions["projection_dim"]),
        target_vocabulary_size=len(
            cast(Sequence[str], fit_metadata["target_vocabulary"])
        ),
        target_embedding_dim=int(dimensions["phone_target_embedding_dim"]),
        attention_hidden_dim=int(dimensions["attention_hidden_dim"]),
        attention_aspects=int(dimensions["attention_aspects"]),
        dropout=float(dimensions["dropout"]),
        score_range=cast(Sequence[float], dimensions["score_range"]),
    )
    expected_state = expected_model.state_dict()
    if set(keys) != set(expected_state):
        raise S105TrainingError("hierarchy checkpoint tensor allowlist differs")
    for name, tensor in tensors.items():
        expected_tensor = expected_state[name]
        if (
            tensor.shape != expected_tensor.shape
            or tensor.dtype != expected_tensor.dtype
            or tensor.numel() <= 0
            or not tensor.is_floating_point()
            or not bool(torch.isfinite(tensor).all())
        ):
            raise S105TrainingError("hierarchy checkpoint tensor is invalid")
    checkpoint_record = _expected_checkpoint_record(execution_lock, seed)
    expected_metadata_keys = {
        "schema_version",
        "variant",
        "seed",
        "development_only",
        "oracle_alignment",
        "model_id",
        "revision",
        "target_vocabulary_json",
        "phone_decision_threshold",
        "best_epoch",
        "full_backbone_tensor_count",
        "s104_checkpoint_sha256",
        "word_loss_performed",
    }
    try:
        metadata_value = cast(Mapping[str, str], metadata)
        threshold = float(metadata_value["phone_decision_threshold"])
        best_epoch = int(metadata_value["best_epoch"])
        checkpoint_vocabulary = json.loads(metadata_value["target_vocabulary_json"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise S105TrainingError("hierarchy checkpoint metadata is invalid") from error
    if (
        metadata is None
        or set(metadata) != expected_metadata_keys
        or metadata_value.get("schema_version") != CHECKPOINT_SCHEMA_VERSION
        or metadata_value.get("variant") != variant
        or metadata_value.get("seed") != str(seed)
        or metadata_value.get("development_only") != "true"
        or metadata_value.get("oracle_alignment") != "true"
        or metadata_value.get("model_id") != selected["model_id"]
        or metadata_value.get("revision") != selected["revision"]
        or metadata_value.get("full_backbone_tensor_count") != "0"
        or metadata_value.get("s104_checkpoint_sha256") != checkpoint_record["sha256"]
        or metadata_value.get("word_loss_performed") != "false"
        or checkpoint_vocabulary != fit_metadata["target_vocabulary"]
        or not math.isfinite(threshold)
        or not 0 <= threshold <= 1
        or best_epoch <= 0
    ):
        raise S105TrainingError("hierarchy checkpoint metadata contract differs")
    resource = _load_json(seed_root / "resource_report.json", "resource report")
    expected_resource_keys = {
        "schema_version",
        "variant",
        "seed",
        "trainable_parameter_audit",
        "checkpoint",
        "fit_metadata",
        "selection",
        "device",
        "peak_process_rss_bytes",
        "peak_cuda_allocated_bytes",
        "peak_cuda_reserved_bytes",
        "automatic_oom_retry_count",
        "shared_encoder_forward_across_variants",
        "dev_passes",
        "s104_checkpoint_sha256",
    }
    audit = resource.get("trainable_parameter_audit")
    persisted_checkpoint = resource.get("checkpoint")
    selection = resource.get("selection")
    device = resource.get("device")
    expected_audit = assert_head_only_trainable(expected_model)
    tensor_shapes = {name: list(tensors[name].shape) for name in sorted(tensors)}
    tensor_dtypes = {name: str(tensors[name].dtype) for name in sorted(tensors)}
    expected_checkpoint = {
        "tensor_count": len(tensors),
        "full_backbone_tensor_count": 0,
        "trainable_parameter_names": sorted(tensors),
        "tensor_shapes": tensor_shapes,
        "tensor_dtypes": tensor_dtypes,
        "sha256": sha256_file(checkpoint_path),
        "size_bytes": checkpoint_path.stat().st_size,
    }
    history = selection.get("history") if isinstance(selection, Mapping) else None
    if (
        set(resource) != expected_resource_keys
        or resource.get("schema_version") != REPORT_SCHEMA_VERSION
        or resource.get("variant") != variant
        or resource.get("seed") != seed
        or audit != expected_audit
        or persisted_checkpoint != expected_checkpoint
        or resource.get("fit_metadata") != fit_metadata
        or not isinstance(selection, Mapping)
        or set(selection)
        != {
            "best_epoch",
            "phone_decision_threshold",
            "inner_phone_macro_f1",
            "inner_comprehensibility_icc_2_1",
            "stopped_early",
            "history",
        }
        or selection.get("best_epoch") != best_epoch
        or selection.get("phone_decision_threshold") != threshold
        or not isinstance(selection.get("stopped_early"), bool)
        or not isinstance(history, list)
        or not isinstance(device, Mapping)
        or device.get("type") != "cuda"
        or device.get("mixed_precision") != "bfloat16"
        or resource.get("automatic_oom_retry_count") != 0
        or resource.get("shared_encoder_forward_across_variants") is not True
        or resource.get("dev_passes")
        != {"phone_binary_error": 1, "utterance_scores": 1}
        or resource.get("s104_checkpoint_sha256") != checkpoint_record["sha256"]
    ):
        raise S105TrainingError("resource/trainable evidence differs")
    optimization = cast(Mapping[str, Any], config["optimization"])
    maximum_epochs = int(optimization["maximum_epochs"])
    minimum_epochs = int(optimization["minimum_epochs"])
    patience = int(optimization["early_stopping_patience"])
    expected_steps = math.ceil(
        int(fit_metadata["fit_utterances"])
        / int(optimization["gradient_accumulation_utterances"])
    )
    thresholds = cast(
        Mapping[str, Any],
        optimization["phone_threshold_grid"],
    )
    grid_start = float(thresholds["start"])
    grid_stop = float(thresholds["stop"])
    grid_step = float(thresholds["step"])
    if not history or len(history) > maximum_epochs:
        raise S105TrainingError("selection history cardinality differs")
    derived_best_epoch = 0
    derived_best_phone = -math.inf
    derived_best_icc: float | None = None
    epochs_without_improvement = 0
    for epoch, item_value in enumerate(history, start=1):
        if not isinstance(item_value, Mapping):
            raise S105TrainingError("selection history row is invalid")
        item = cast(Mapping[str, Any], item_value)
        if set(item) != {
            "epoch",
            "train_mean_joint_loss",
            "optimizer_steps",
            "inner_phone_macro_f1",
            "inner_phone_threshold",
            "inner_comprehensibility_icc_2_1",
            "inner_comprehensibility_icc_defined",
            "selected_as_best",
        }:
            raise S105TrainingError("selection history schema differs")
        loss = _finite(item.get("train_mean_joint_loss"), "selection loss")
        phone = _finite(item.get("inner_phone_macro_f1"), "selection phone")
        item_threshold = _finite(
            item.get("inner_phone_threshold"),
            "selection threshold",
        )
        raw_icc = item.get("inner_comprehensibility_icc_2_1")
        icc = _finite(raw_icc, "selection ICC") if raw_icc is not None else None
        if (
            item.get("epoch") != epoch
            or loss < 0
            or item.get("optimizer_steps") != expected_steps
            or not 0 <= phone <= 1
            or not grid_start <= item_threshold <= grid_stop
            or not math.isclose(
                (item_threshold - grid_start) / grid_step,
                round((item_threshold - grid_start) / grid_step),
                rel_tol=0.0,
                abs_tol=1e-9,
            )
            or item.get("inner_comprehensibility_icc_defined") is not (icc is not None)
            or not isinstance(item.get("selected_as_best"), bool)
        ):
            raise S105TrainingError("selection history value differs")
        improved = phone > derived_best_phone + 1e-12 or (
            abs(phone - derived_best_phone) <= 1e-12
            and icc is not None
            and (derived_best_icc is None or icc > derived_best_icc + 1e-12)
        )
        if item.get("selected_as_best") is not improved:
            raise S105TrainingError("selection best-epoch trace differs")
        if improved:
            derived_best_epoch = epoch
            derived_best_phone = phone
            derived_best_icc = icc
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1
    expected_stopped = bool(
        len(history) >= minimum_epochs and epochs_without_improvement >= patience
    )
    if (
        derived_best_epoch != best_epoch
        or selection.get("inner_phone_macro_f1") != derived_best_phone
        or selection.get("inner_comprehensibility_icc_2_1") != derived_best_icc
        or selection.get("stopped_early") is not expected_stopped
    ):
        raise S105TrainingError("selection summary/history binding differs")
    for name in (
        "inner_phone_macro_f1",
        "inner_comprehensibility_icc_2_1",
    ):
        value = selection.get(name)
        if value is not None:
            _finite(value, f"selection.{name}")
    numeric_resource_fields = (
        "peak_process_rss_bytes",
        "peak_cuda_allocated_bytes",
        "peak_cuda_reserved_bytes",
    )
    expected_device_keys = {
        "type",
        "index",
        "name",
        "compute_capability",
        "total_memory_bytes",
        "torch_version",
        "cuda_version",
        "mixed_precision",
    }
    if (
        not isinstance(device, Mapping)
        or set(device) != expected_device_keys
        or isinstance(device.get("index"), bool)
        or not isinstance(device.get("index"), int)
        or not isinstance(device.get("name"), str)
        or not isinstance(device.get("compute_capability"), str)
        or isinstance(device.get("total_memory_bytes"), bool)
        or not isinstance(device.get("total_memory_bytes"), int)
        or int(device["total_memory_bytes"]) <= 0
        or not isinstance(device.get("torch_version"), str)
        or not isinstance(device.get("cuda_version"), str)
    ):
        raise S105TrainingError("resource device metadata differs")
    if (
        any(
            isinstance(resource.get(name), bool)
            or not isinstance(resource.get(name), int)
            or int(cast(int, resource[name])) < (1 if "rss" in name else 0)
            for name in numeric_resource_fields
        )
        or int(resource["peak_cuda_reserved_bytes"])
        < int(resource["peak_cuda_allocated_bytes"])
        or int(resource["peak_cuda_reserved_bytes"]) > int(device["total_memory_bytes"])
    ):
        raise S105TrainingError("resource measurements are invalid")
    return threshold


def _validate_prediction_source_binding(
    *,
    phone_path: Path,
    utterance_path: Path,
    variant: str,
    seed: int,
    expected: Mapping[str, Any],
) -> None:
    if not pq.read_schema(phone_path).equals(
        phone_prediction_schema(),
        check_metadata=True,
    ) or not pq.read_schema(utterance_path).equals(
        utterance_prediction_schema(),
        check_metadata=True,
    ):
        raise S105TrainingError("S105 prediction Parquet schema differs")
    phone_rows = pq.read_table(phone_path, use_threads=False).to_pylist()
    utterance_rows = pq.read_table(
        utterance_path,
        use_threads=False,
    ).to_pylist()
    expected_identifiers = list(expected)
    if len(utterance_rows) != len(expected_identifiers):
        raise S105TrainingError("utterance prediction cardinality differs")
    phone_cursor = 0
    seen_utterances: set[str] = set()
    for expected_identifier, utterance_row in zip(
        expected_identifiers,
        utterance_rows,
        strict=True,
    ):
        truth = expected[expected_identifier]
        try:
            speaker = truth.speaker_id_sha256
            l1 = truth.l1
            gender = truth.gender
            proficiency = truth.proficiency
            phones = truth.phones
            scores = truth.scores
        except AttributeError as error:
            raise S105TrainingError("expected dev truth shape differs") from error
        if (
            utterance_row.get("schema_version") != UTTERANCE_PREDICTION_SCHEMA_VERSION
            or utterance_row.get("variant") != variant
            or utterance_row.get("seed") != seed
            or utterance_row.get("split") != "dev"
            or utterance_row.get("development_only") is not True
            or utterance_row.get("oracle_alignment") is not True
            or utterance_row.get("utterance_id_sha256") != expected_identifier
            or utterance_row.get("speaker_id_sha256") != speaker
            or utterance_row.get("l1") != l1
            or utterance_row.get("gender") != gender
            or utterance_row.get("proficiency") != proficiency
            or expected_identifier in seen_utterances
        ):
            raise S105TrainingError("utterance prediction source identity differs")
        seen_utterances.add(expected_identifier)
        for index, outcome in enumerate(OUTCOMES):
            observed = math.isfinite(float(scores[index]))
            persisted_true = utterance_row[f"{outcome}_true"]
            predicted = utterance_row[f"{outcome}_pred"]
            if (
                utterance_row[f"{outcome}_observed"] is not observed
                or (
                    observed
                    and float(persisted_true) != float(np.float32(float(scores[index])))
                )
                or (not observed and persisted_true is not None)
                or not math.isfinite(float(predicted))
                or not 1 <= float(predicted) <= 5
            ):
                raise S105TrainingError("utterance prediction truth/value differs")
        for phone_index, (target_label, binary_label) in enumerate(phones):
            if phone_cursor >= len(phone_rows):
                raise S105TrainingError("phone prediction cardinality differs")
            row = phone_rows[phone_cursor]
            probability = float(row.get("error_probability"))
            if (
                row.get("schema_version") != PHONE_PREDICTION_SCHEMA_VERSION
                or row.get("variant") != variant
                or row.get("seed") != seed
                or row.get("split") != "dev"
                or row.get("development_only") is not True
                or row.get("oracle_alignment") is not True
                or row.get("utterance_id_sha256") != expected_identifier
                or row.get("speaker_id_sha256") != speaker
                or row.get("phone_index") != phone_index
                or row.get("target_label") != target_label
                or row.get("binary_error_label") != binary_label
                or row.get("l1") != l1
                or row.get("gender") != gender
                or row.get("proficiency") != proficiency
                or not math.isfinite(probability)
                or not 0 <= probability <= 1
                or row.get("error_prediction") not in (0, 1)
            ):
                raise S105TrainingError("phone prediction source identity differs")
            phone_cursor += 1
    if phone_cursor != len(phone_rows):
        raise S105TrainingError("phone prediction cardinality differs")


def _validate_deep_generation(
    *,
    root: Path,
    context: DeepValidationContext,
) -> None:
    from voice_coach.training.wavlm_adapter_training import (
        slice_metrics_from_prediction_files,
    )

    lock_data = context.execution_lock.get("data")
    if not isinstance(lock_data, Mapping):
        raise S105TrainingError("execution-lock data audit is absent")
    fit_metadata = _fit_metadata_payload(context.expected_fit_metadata)
    if fit_metadata["fit_utterances"] != lock_data.get("inner_fit_utterances"):
        raise S105TrainingError("fit utterance source/lock binding differs")
    if len(context.expected_dev_truth) != cast(
        Mapping[str, Any],
        lock_data["split_utterances"],
    ).get("dev"):
        raise S105TrainingError("dev utterance source/lock binding differs")
    for variant in VARIANT_ORDER:
        for seed in SEEDS:
            seed_root = root / variant / f"seed_{seed}"
            threshold = _validate_checkpoint_and_resource(
                seed_root=seed_root,
                variant=str(variant),
                seed=seed,
                context=context,
            )
            phone_path = seed_root / "phone_dev_predictions.parquet"
            utterance_path = seed_root / "utterance_dev_predictions.parquet"
            _validate_prediction_source_binding(
                phone_path=phone_path,
                utterance_path=utterance_path,
                variant=str(variant),
                seed=seed,
                expected=context.expected_dev_truth,
            )
            slices = _load_json(
                seed_root / "dev_slice_metrics.json",
                "dev slice metrics",
            )
            expected_metrics = _json_safe(
                slice_metrics_from_prediction_files(
                    phone_path=phone_path,
                    utterance_path=utterance_path,
                    threshold=threshold,
                )
            )
            if slices != {
                "schema_version": REPORT_SCHEMA_VERSION,
                "variant": str(variant),
                "seed": seed,
                "development_only": True,
                "dev_inference_passes_by_task": {
                    "phone_binary_error": 1,
                    "utterance_scores": 1,
                },
                "metrics": expected_metrics,
            }:
                raise S105TrainingError(
                    "dev slice metrics differ from persisted predictions"
                )
        expected_variant_report = build_variant_report(
            generation=root,
            variant=str(variant),
        )
        actual_variant_report = _load_json(
            root / variant / "variant_report.json",
            "variant report",
        )
        if actual_variant_report != expected_variant_report:
            raise S105TrainingError("variant report recomputation differs")
    # This second loader also enforces exact identity and order across all
    # fifteen prediction pairs and checkpoint-threshold decisions.
    comparison = recompute_ablation_comparison(
        generation=root,
        config=context.config,
    )
    actual_comparison = _load_json(
        root / "ablation_comparison_report.json",
        "comparison report",
    )
    if actual_comparison != comparison:
        raise S105TrainingError("ablation comparison recomputation differs")
    expected_table = render_ablation_table(
        generation=root,
        comparison=comparison,
    )
    try:
        actual_table = (root / "ablation_table.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise S105TrainingError("ablation table is unreadable") from error
    if actual_table != expected_table:
        raise S105TrainingError("ablation table recomputation differs")


def validate_generation(
    root: Path,
    *,
    context: DeepValidationContext | None = None,
) -> Mapping[str, Any]:
    """Validate the exact 82-file tree and, when bound, every derived value."""

    records = artifact_records(root)
    report = _load_json(
        root / "ablation_comparison_report.json",
        "S105 comparison report",
    )
    validate_comparison_report(report)
    for variant in VARIANT_ORDER:
        variant_report = _load_json(
            root / variant / "variant_report.json",
            "S105 variant report",
        )
        if (
            variant_report.get("schema_version") != REPORT_SCHEMA_VERSION
            or variant_report.get("status") != "completed"
            or variant_report.get("variant") != variant
            or variant_report.get("seeds") != list(SEEDS)
            or variant_report.get("dev_passes_per_task_seed") != 1
            or variant_report.get("word_loss_performed") is not False
            or variant_report.get("word_pcc_status") != "not_evaluable"
            or variant_report.get("stack_accessed") is not False
            or variant_report.get("calibration_accessed") is not False
            or variant_report.get("locked_test_accessed") is not False
        ):
            raise S105TrainingError("S105 variant report contract differs")
    if context is not None:
        _validate_deep_generation(root=root, context=context)
    generation_id = hashlib.sha256(
        canonical_json_bytes(
            {
                "schema_version": COMMIT_SCHEMA_VERSION,
                "artifacts": list(records),
            },
            pretty=False,
        )
    ).hexdigest()
    return {
        "status": "valid",
        "validation_depth": "deep" if context is not None else "layout",
        "generation_id": generation_id,
        "artifact_count": len(records),
        "locked_test_accessed": False,
    }


def publish_content_addressed_generation(
    *,
    temporary: Path,
    generation_root: Path,
    context: DeepValidationContext | None = None,
) -> Mapping[str, Any]:
    """Atomically publish only a fully validated 82-file generation."""

    validation = validate_generation(temporary, context=context)
    generation_id = str(validation["generation_id"])
    destination = generation_root / generation_id
    generation_root.mkdir(parents=True, exist_ok=True)
    _assert_no_symlink_chain(generation_root)
    _fsync_artifact_tree(temporary)
    created = _rename_no_replace(temporary, destination)
    if not created:
        if destination.is_symlink():
            raise S105TrainingError("S105 generation destination is a symlink")
        if not destination.is_dir() or artifact_records(
            destination
        ) != artifact_records(temporary):
            raise S105TrainingError("content-addressed S105 generation already differs")
        shutil.rmtree(temporary)
        _fsync_directory(generation_root)
    publication = {
        **validation,
        "generation_path": destination,
        "created": created,
    }
    if created:
        try:
            _fsync_directory(generation_root)
        except OSError as error:
            raise GenerationPublicationDurabilityError(publication) from error
    return publication


def _assert_no_symlink_chain(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if os.path.lexists(current) and current.is_symlink():
            raise S105TrainingError("S105 path contains a symlink component")


def _rename_no_replace(source: Path, destination: Path) -> bool:
    """Atomically rename a directory without ever replacing a claimant."""

    renameat2 = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    if renameat2 is None:
        raise S105TrainingError("atomic no-replace rename is unavailable")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        -100,  # AT_FDCWD
        os.fsencode(source),
        -100,
        os.fsencode(destination),
        1,  # RENAME_NOREPLACE
    )
    if result == 0:
        return True
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        return False
    raise OSError(error_number, os.strerror(error_number), str(destination))


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_artifact_tree(root: Path) -> None:
    """Durably flush every regular artifact and directory before rename."""

    directories = [root]
    for path in root.rglob("*"):
        if path.is_symlink():
            raise S105TrainingError("S105 artifact tree contains a symlink")
        if path.is_file():
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        elif path.is_dir():
            directories.append(path)
        else:
            raise S105TrainingError("S105 artifact tree node type differs")
    for directory in sorted(
        directories,
        key=lambda value: len(value.parts),
        reverse=True,
    ):
        _fsync_directory(directory)


def write_atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = canonical_json_bytes(value, pretty=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part-{uuid4().hex}")
    replaced = False
    try:
        with temporary.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        replaced = True
        _fsync_directory(path.parent)
    finally:
        if not replaced:
            temporary.unlink(missing_ok=True)


def assert_head_only_trainable(model: nn.Module) -> Mapping[str, Any]:
    """Reject a model containing any trainable parameter outside its S105 head."""

    names: list[str] = []
    count = 0
    for name, parameter in model.named_parameters():
        if parameter.requires_grad:
            if name.startswith(("wavlm.", "adapter.", "encoder.")):
                raise S105TrainingError("S104 encoder/adapter must remain frozen")
            names.append(name)
            count += int(parameter.numel())
    if not names or count <= 0:
        raise S105TrainingError("S105 hierarchy has no trainable head parameters")
    return {
        "trainable_parameter_count": count,
        "trainable_tensor_count": len(names),
        "trainable_parameter_names": names,
        "s104_adapter_frozen": True,
        "backbone_frozen": True,
        "hierarchy_heads_only": True,
    }


def _resolve_workspace_path(
    workspace_root: Path,
    value: object,
    name: str,
    *,
    must_exist: bool = True,
) -> Path:
    if not isinstance(value, str) or not value:
        raise S105TrainingError(f"{name} is not a path")
    raw = Path(value)
    if any(part.casefold().startswith(".env") for part in raw.parts):
        raise S105TrainingError(f"{name} references an environment file")
    candidate = raw if raw.is_absolute() else workspace_root / raw
    lexical = Path(os.path.abspath(candidate))
    try:
        lexical.relative_to(workspace_root)
        _assert_no_symlink_chain(lexical)
        resolved = lexical.resolve(strict=must_exist)
        resolved.relative_to(workspace_root)
    except (OSError, ValueError) as error:
        raise S105TrainingError(f"{name} is absent or escapes the workspace") from error
    return resolved


def build_deep_validation_context(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    execution_lock: Mapping[str, Any],
) -> DeepValidationContext:
    """Reconstruct immutable metadata truth without opening raw audio."""

    from voice_coach.training.wavlm_adapter_training import (
        expected_dev_ground_truth,
        load_reserved_prompt_hashes,
        load_utterance_labels,
        scan_fit_metadata,
    )

    data = cast(Mapping[str, Any], config["data"])
    manifest_path = _resolve_workspace_path(
        workspace_root,
        data["modeling_manifest"],
        "S105 modeling manifest",
    )
    reservation_path = _resolve_workspace_path(
        workspace_root,
        data["prompt_challenge_reservation"],
        "S105 prompt reservation",
    )
    labels_path = _resolve_workspace_path(
        workspace_root,
        data["explicit_utterance_labels"],
        "S105 explicit labels",
    )
    for name, path in (
        ("modeling_manifest", manifest_path),
        ("prompt_challenge_reservation", reservation_path),
        ("explicit_utterance_labels", labels_path),
    ):
        if sha256_file(path) != data[f"{name}_sha256"]:
            raise S105TrainingError(f"S105 {name} source changed")
    reserved = load_reserved_prompt_hashes(reservation_path)
    labels = load_utterance_labels(labels_path)
    inner = cast(Mapping[str, Any], data["inner_validation"])
    scope = cast(Mapping[str, Any], data["primary_analysis_scope"])
    metadata = scan_fit_metadata(
        manifest_path=manifest_path,
        reserved_prompts=reserved,
        modulus=int(inner["modulus"]),
        validation_buckets={
            int(value) for value in cast(Sequence[int], inner["validation_buckets"])
        },
        allowed_l1={str(value) for value in cast(Sequence[str], scope["l1"])},
        allowed_gender={str(value) for value in cast(Sequence[str], scope["gender"])},
    )
    expected = expected_dev_ground_truth(
        manifest_path=manifest_path,
        labels=labels,
        reserved_prompts=reserved,
        allowed_l1={str(value) for value in cast(Sequence[str], scope["l1"])},
        allowed_gender={str(value) for value in cast(Sequence[str], scope["gender"])},
    )
    locked_data = execution_lock.get("data")
    if not isinstance(locked_data, Mapping):
        raise S105TrainingError("execution-lock data audit is absent")
    sources = locked_data.get("sources")
    if not isinstance(sources, Mapping):
        raise S105TrainingError("execution-lock source records are absent")
    for name, path in (
        ("modeling_manifest", manifest_path),
        ("prompt_challenge_reservation", reservation_path),
        ("explicit_utterance_labels", labels_path),
    ):
        record = sources.get(name)
        if (
            not isinstance(record, Mapping)
            or record.get("path") != path.relative_to(workspace_root).as_posix()
            or record.get("sha256") != sha256_file(path)
            or record.get("size_bytes") != path.stat().st_size
        ):
            raise S105TrainingError("execution-lock data source binding differs")
    if metadata.fit_utterances != locked_data.get("inner_fit_utterances") or len(
        expected
    ) != cast(Mapping[str, Any], locked_data["split_utterances"]).get("dev"):
        raise S105TrainingError("execution-lock metadata cardinality differs")
    return DeepValidationContext(
        config=config,
        execution_lock=execution_lock,
        expected_dev_truth=expected,
        expected_fit_metadata=metadata,
    )


def _commit_binding(document: Mapping[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("binding_sha256", None)
    return hashlib.sha256(canonical_json_bytes(unsigned, pretty=False)).hexdigest()


def validate_s105_commit(
    *,
    workspace_root: Path,
    config_path: Path,
) -> Mapping[str, Any]:
    """Read-only validate a complete S105 generation, registries, and lineage."""

    from voice_coach.training.wavlm_hierarchy_contract import (
        EXECUTION_LOCK_SCHEMA_VERSION,
        load_config,
        load_json_strict,
        validate_config,
        validate_implementation_sources,
        validate_s104_predecessor,
    )

    root = workspace_root.resolve(strict=True)
    resolved_config = _resolve_workspace_path(root, str(config_path), "S105 config")
    config = load_config(resolved_config)
    validate_config(config)
    implementation, _ = validate_implementation_sources(
        workspace_root=root,
        config=config,
    )
    predecessor = validate_s104_predecessor(workspace_root=root, config=config)
    outputs = cast(Mapping[str, Any], config["outputs"])
    commit_path = _resolve_workspace_path(
        root,
        outputs["commit_manifest"],
        "S105 commit",
    )
    commit_sha256 = sha256_file(commit_path)
    commit = load_json_strict(commit_path, "S105 commit")
    expected_keys = {
        "schema_version",
        "status",
        "generation_id",
        "generation_path",
        "artifacts",
        "comparison_report_sha256",
        "adoption_status",
        "selected_variant",
        "registries",
        "execution_lock",
        "predecessor",
        "binding_sha256",
    }
    binding = commit.get("binding_sha256")
    if (
        set(commit) != expected_keys
        or commit.get("schema_version") != COMMIT_SCHEMA_VERSION
        or commit.get("status") != "committed"
        or not _is_sha256(commit.get("generation_id"))
        or binding != _commit_binding(commit)
    ):
        raise S105TrainingError("S105 commit schema or binding differs")
    generation_root = _resolve_workspace_path(
        root,
        outputs["generation_root"],
        "S105 generation root",
    )
    generation = _resolve_workspace_path(
        root,
        commit.get("generation_path"),
        "S105 generation",
    )
    if (
        not generation.is_dir()
        or generation.parent != generation_root
        or generation.name != commit.get("generation_id")
    ):
        raise S105TrainingError("S105 generation path differs")
    execution_lock_path = _resolve_workspace_path(
        root,
        outputs["execution_lock"],
        "S105 execution lock",
    )
    execution_lock = load_json_strict(execution_lock_path, "S105 execution lock")
    validation_context = build_deep_validation_context(
        workspace_root=root,
        config=config,
        execution_lock=execution_lock,
    )
    validation = validate_generation(generation, context=validation_context)
    records = artifact_records(generation)
    if (
        validation.get("generation_id") != commit.get("generation_id")
        or commit.get("artifacts") != list(records)
        or validation.get("artifact_count") != 82
    ):
        raise S105TrainingError("S105 artifact binding differs")
    comparison_path = generation / "ablation_comparison_report.json"
    comparison = load_json_strict(comparison_path, "S105 comparison report")
    validate_comparison_report(comparison)
    gate = cast(Mapping[str, Any], comparison["adoption_gate"])
    expected_adoption = str(gate["status"])
    expected_selected = (
        "levelwise_multi_aspect_hierarchy"
        if gate["adopt_candidate"] is True
        else "simple_head_control"
    )
    if (
        commit.get("comparison_report_sha256") != sha256_file(comparison_path)
        or commit.get("adoption_status") != expected_adoption
        or commit.get("selected_variant") != expected_selected
    ):
        raise S105TrainingError("S105 selected-variant binding differs")
    predecessor_record = commit.get("predecessor")
    if (
        not isinstance(predecessor_record, Mapping)
        or set(predecessor_record) != {"stage", "commit_sha256", "generation_id"}
        or predecessor_record.get("stage") != "s104_adapter"
        or predecessor_record.get("commit_sha256") != predecessor.commit_sha256
        or predecessor_record.get("generation_id") != predecessor.generation_id
    ):
        raise S105TrainingError("S105 predecessor binding differs")
    execution_record = commit.get("execution_lock")
    if (
        not isinstance(execution_record, Mapping)
        or set(execution_record) != {"path", "sha256"}
        or execution_record.get("path")
        != execution_lock_path.relative_to(root).as_posix()
        or execution_record.get("sha256") != sha256_file(execution_lock_path)
        or execution_lock.get("schema_version") != EXECUTION_LOCK_SCHEMA_VERSION
        or execution_lock.get("status") != "ready_for_s105_gpu_training"
        or execution_lock.get("config_sha256") != sha256_file(resolved_config)
        or cast(Mapping[str, Any], execution_lock.get("predecessor", {})).get(
            "commit_sha256"
        )
        != predecessor.commit_sha256
        or execution_lock.get("implementation") != implementation
    ):
        raise S105TrainingError("S105 execution-lock binding differs")
    registries = commit.get("registries")
    if not isinstance(registries, list) or len(registries) != len(VARIANT_ORDER):
        raise S105TrainingError("S105 variant registry count differs")
    registry_root = _resolve_workspace_path(
        root,
        outputs["registry_root"],
        "S105 registry root",
    )
    prefix = str(outputs["registry_run_id_prefix"])
    for variant, record_value in zip(VARIANT_ORDER, registries, strict=True):
        record = cast(Mapping[str, Any], record_value)
        run_id = f"{prefix}-{variant}"
        path = _resolve_workspace_path(root, record.get("path"), "S105 registry")
        expected_path = registry_root / run_id / "metadata.json"
        if (
            set(record)
            != {
                "variant",
                "run_id",
                "path",
                "sha256",
                "status",
                "artifact_count",
            }
            or record.get("variant") != variant
            or record.get("run_id") != run_id
            or path != expected_path
            or record.get("sha256") != sha256_file(path)
            or record.get("status") != "completed"
            or record.get("artifact_count") != 16
        ):
            raise S105TrainingError("S105 registry record differs")
        registry = load_json_strict(path, "S105 registry")
        artifacts = registry.get("artifacts")
        if (
            registry.get("schema_version") != 1
            or registry.get("run_id") != run_id
            or registry.get("status") != "completed"
            or registry.get("failure") is not None
            or registry.get("seeds")
            != {f"experiment_seed_{seed}": seed for seed in SEEDS}
            or not isinstance(registry.get("metrics"), list)
            or not isinstance(artifacts, list)
            or len(artifacts) != 16
        ):
            raise S105TrainingError("S105 registry document differs")
        expected_names = {
            name
            for name, relative in expected_artifact_layout()
            if relative.startswith(f"{variant}/")
        }
        actual_names = {
            item.get("name") for item in artifacts if isinstance(item, Mapping)
        }
        if actual_names != expected_names:
            raise S105TrainingError("S105 registry artifact names differ")
        by_name = {record["name"]: record for record in records}
        for artifact in artifacts:
            item = cast(Mapping[str, Any], artifact)
            name = str(item["name"])
            canonical = by_name[name]
            artifact_path = generation / str(canonical["path"])
            if (
                item.get("path") != artifact_path.relative_to(root).as_posix()
                or item.get("sha256") != canonical["sha256"]
                or item.get("size_bytes") != canonical["size_bytes"]
            ):
                raise S105TrainingError("S105 registry artifact binding differs")
    if (
        sha256_file(commit_path) != commit_sha256
        or sha256_file(comparison_path) != commit["comparison_report_sha256"]
        or artifact_records(generation) != records
    ):
        raise S105TrainingError("S105 evidence changed during validation")
    return {
        "status": "valid",
        "generation_id": commit["generation_id"],
        "artifact_count": len(records),
        "registry_count": len(registries),
        "commit_sha256": commit_sha256,
        "selected_variant": expected_selected,
        "adoption_status": expected_adoption,
        "development_only": True,
        "stack_accessed": False,
        "calibration_accessed": False,
        "locked_test_accessed": False,
    }


__all__ = [
    "ADJACENT_VARIANT_PAIRS",
    "ALLOWED_SPLITS",
    "BootstrapResult",
    "CHECKPOINT_SCHEMA_VERSION",
    "COMMIT_SCHEMA_VERSION",
    "COMPARISON_SCHEMA_VERSION",
    "DeepValidationContext",
    "DevPassLedger",
    "FORBIDDEN_SPLITS",
    "GenerationPublicationDurabilityError",
    "HierarchyPredictionWriters",
    "HierarchyTrainingExample",
    "LossBundle",
    "OUTCOMES",
    "PRIMARY_OUTCOMES",
    "PHONE_PREDICTION_SCHEMA_VERSION",
    "REPORT_SCHEMA_VERSION",
    "S105TrainingError",
    "SEEDS",
    "SubgroupResult",
    "TASKS",
    "UTTERANCE_PREDICTION_SCHEMA_VERSION",
    "artifact_records",
    "assert_allowed_split",
    "assert_head_only_trainable",
    "build_adoption_report",
    "build_deep_validation_context",
    "build_variant_report",
    "expected_artifact_layout",
    "joint_hierarchy_loss",
    "map_phones_to_words",
    "paired_phone_bootstrap",
    "paired_utterance_icc_bootstrap",
    "phone_prediction_schema",
    "publish_content_addressed_generation",
    "recompute_ablation_comparison",
    "render_ablation_table",
    "subgroup_worst_group_drop",
    "train_head_epoch",
    "validate_comparison_report",
    "validate_generation",
    "validate_s105_commit",
    "write_atomic_json",
    "utterance_prediction_schema",
]
