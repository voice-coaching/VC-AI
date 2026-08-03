"""Realized-phone-free prototype posterior and GOP features.

Only clean match rows are allowed to fit acoustic prototypes.  The module is
deliberately independent of manifests, raw audio, alignment operations, and
realized-phone identities so that the feature boundary can be unit tested.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
from typing import Final

import numpy as np
from numpy.typing import NDArray


FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
BoolArray = NDArray[np.bool_]
SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_FOLD_NAMESPACE: Final = "voice-coach.aihub71490.b07-gop-speaker-fold.v1"
PHONE_INVENTORY: Final = (
    "spn",
    "ㄱ",
    "ㄲ",
    "ㄴ",
    "ㄷ",
    "ㄸ",
    "ㄹ",
    "ㅁ",
    "ㅂ",
    "ㅃ",
    "ㅅ",
    "ㅆ",
    "ㅇ",
    "ㅈ",
    "ㅉ",
    "ㅊ",
    "ㅋ",
    "ㅌ",
    "ㅍ",
    "ㅎ",
    "ㅏ",
    "ㅐ",
    "ㅑ",
    "ㅓ",
    "ㅔ",
    "ㅕ",
    "ㅖ",
    "ㅗ",
    "ㅘ",
    "ㅚ",
    "ㅛ",
    "ㅜ",
    "ㅝ",
    "ㅟ",
    "ㅠ",
    "ㅡ",
    "ㅢ",
    "ㅣ",
)


class GOPError(ValueError):
    """Raised when prototype-posterior invariants are violated."""


@dataclass(frozen=True, slots=True)
class GOPFeatures:
    """Four exact acoustic posterior features in the frozen order."""

    target_log_posterior: FloatArray
    gop_margin: FloatArray
    posterior_entropy: FloatArray
    target_is_top1: FloatArray

    def as_matrix(self) -> FloatArray:
        return np.column_stack(
            (
                self.target_log_posterior,
                self.gop_margin,
                self.posterior_entropy,
                self.target_is_top1,
            )
        )


@dataclass(frozen=True, slots=True)
class TemperatureCandidate:
    temperature: float
    match_negative_log_likelihood: float
    match_row_count: int


@dataclass(frozen=True, slots=True)
class CrossFitGOPResult:
    features: GOPFeatures
    folds: IntArray
    selected_temperature: float
    temperature_candidates: tuple[TemperatureCandidate, ...]
    fold_prototypes: tuple[FloatArray, ...]


def _as_float_matrix(embeddings: object, *, name: str) -> FloatArray:
    raw = np.asarray(embeddings)
    values = np.asarray(
        raw,
        dtype=np.float64 if raw.dtype == np.float64 else np.float32,
    )
    if values.ndim != 2 or values.shape[0] == 0 or values.shape[1] == 0:
        raise GOPError(f"{name} must be a non-empty two-dimensional matrix")
    if not np.isfinite(values).all():
        raise GOPError(f"{name} contains NaN or Inf")
    return values


def _as_string_vector(values: object, *, name: str, rows: int) -> NDArray[np.str_]:
    vector = np.asarray(values, dtype=np.str_)
    if vector.ndim != 1 or vector.shape[0] != rows:
        raise GOPError(f"{name} must be a one-dimensional vector of length {rows}")
    if np.any(vector == ""):
        raise GOPError(f"{name} contains an empty value")
    return vector


def _target_indexes(
    target_labels: object,
    *,
    inventory: tuple[str, ...],
    rows: int,
) -> tuple[NDArray[np.str_], IntArray]:
    labels = _as_string_vector(target_labels, name="target_labels", rows=rows)
    if len(inventory) == 0 or len(set(inventory)) != len(inventory):
        raise GOPError("inventory must be non-empty and unique")
    index = {label: position for position, label in enumerate(inventory)}
    unknown = sorted(set(labels.tolist()).difference(index))
    if unknown:
        raise GOPError(f"target_labels contain out-of-inventory values: {unknown!r}")
    return labels, np.asarray([index[label] for label in labels], dtype=np.int64)


def l2_normalize_rows(embeddings: object, *, name: str = "embeddings") -> FloatArray:
    """Return row-wise L2-normalized embeddings, rejecting zero norms."""

    values = _as_float_matrix(embeddings, name=name)
    norms = np.linalg.norm(values, axis=1)
    if not np.isfinite(norms).all() or np.any(norms <= 0.0):
        raise GOPError(f"{name} contains a zero-norm or non-finite row")
    normalized = values / norms[:, np.newaxis]
    if not np.isfinite(normalized).all():
        raise GOPError(f"{name} normalization produced NaN or Inf")
    return normalized


def fit_match_prototypes(
    embeddings: object,
    target_labels: object,
    match_mask: object,
    *,
    inventory: tuple[str, ...] = PHONE_INVENTORY,
) -> FloatArray:
    """Fit one normalized prototype per class from match rows only."""

    normalized = l2_normalize_rows(embeddings)
    labels, target_indexes = _target_indexes(
        target_labels,
        inventory=inventory,
        rows=normalized.shape[0],
    )
    del labels
    matches = np.asarray(match_mask)
    if (
        matches.ndim != 1
        or matches.shape[0] != normalized.shape[0]
        or matches.dtype.kind != "b"
    ):
        raise GOPError("match_mask must be a boolean vector matching embeddings")
    prototypes = np.empty((len(inventory), normalized.shape[1]), dtype=np.float64)
    for class_index, label in enumerate(inventory):
        selected = normalized[matches & (target_indexes == class_index)]
        if selected.shape[0] == 0:
            raise GOPError(f"match-only prototype class is empty: {label!r}")
        mean = np.mean(selected, axis=0, dtype=np.float64)
        norm = float(np.linalg.norm(mean))
        if not math.isfinite(norm) or norm <= 0.0:
            raise GOPError(f"prototype has zero or non-finite norm: {label!r}")
        prototypes[class_index] = mean / norm
    if not np.isfinite(prototypes).all():
        raise GOPError("prototypes contain NaN or Inf")
    return prototypes


def _cosine_scores(embeddings: object, prototypes: object) -> FloatArray:
    normalized = l2_normalize_rows(embeddings)
    prototype_values = _as_float_matrix(prototypes, name="prototypes")
    if normalized.shape[1] != prototype_values.shape[1]:
        raise GOPError("embedding and prototype dimensions differ")
    prototype_norms = np.linalg.norm(prototype_values, axis=1)
    if not np.isfinite(prototype_norms).all() or not np.allclose(
        prototype_norms,
        1.0,
        rtol=1e-8,
        atol=1e-10,
    ):
        raise GOPError("prototypes must be finite unit-norm rows")
    scores = normalized @ prototype_values.T
    if not np.isfinite(scores).all():
        raise GOPError("cosine scores contain NaN or Inf")
    return scores


def _log_softmax(scores: FloatArray, temperature: float) -> FloatArray:
    if not math.isfinite(temperature) or temperature <= 0.0:
        raise GOPError("temperature must be finite and positive")
    logits = scores / temperature
    maxima = np.max(logits, axis=1, keepdims=True)
    shifted = logits - maxima
    log_normalizer = np.log(np.sum(np.exp(shifted), axis=1, keepdims=True))
    values = shifted - log_normalizer
    if not np.isfinite(values).all():
        raise GOPError("posterior contains NaN or Inf")
    return values


def posterior_log_probabilities(
    embeddings: object,
    prototypes: object,
    temperature: float,
) -> FloatArray:
    """Return stable uniform-prior log posteriors."""

    return _log_softmax(_cosine_scores(embeddings, prototypes), temperature)


def gop_features_from_log_probabilities(
    log_probabilities: object,
    target_labels: object,
    *,
    inventory: tuple[str, ...] = PHONE_INVENTORY,
) -> GOPFeatures:
    """Compute the exact four GOP features from class log probabilities."""

    log_posterior = _as_float_matrix(
        log_probabilities,
        name="log_probabilities",
    )
    if log_posterior.shape[1] != len(inventory):
        raise GOPError("posterior class dimension differs from inventory")
    _, target_indexes = _target_indexes(
        target_labels,
        inventory=inventory,
        rows=log_posterior.shape[0],
    )
    rows = np.arange(log_posterior.shape[0], dtype=np.int64)
    target_log = log_posterior[rows, target_indexes]
    competitors = log_posterior.copy()
    competitors[rows, target_indexes] = -np.inf
    strongest_competitor = np.max(competitors, axis=1)
    probabilities = np.exp(log_posterior)
    entropy = -np.sum(probabilities * log_posterior, axis=1)
    top1 = (np.argmax(log_posterior, axis=1) == target_indexes).astype(np.float64)
    result = GOPFeatures(
        target_log_posterior=np.asarray(target_log, dtype=np.float64),
        gop_margin=np.asarray(target_log - strongest_competitor, dtype=np.float64),
        posterior_entropy=np.asarray(entropy, dtype=np.float64),
        target_is_top1=top1,
    )
    if not np.isfinite(result.as_matrix()).all():
        raise GOPError("GOP features contain NaN or Inf")
    return result


def compute_gop_features(
    embeddings: object,
    target_labels: object,
    prototypes: object,
    temperature: float,
    *,
    inventory: tuple[str, ...] = PHONE_INVENTORY,
) -> GOPFeatures:
    return gop_features_from_log_probabilities(
        posterior_log_probabilities(embeddings, prototypes, temperature),
        target_labels,
        inventory=inventory,
    )


def assign_speaker_fold(
    speaker_id_sha256: str,
    *,
    seed: int,
    fold_count: int = 5,
    namespace: str = DEFAULT_FOLD_NAMESPACE,
) -> int:
    """Assign a pseudonymous speaker deterministically to one fold."""

    if not SHA256_PATTERN.fullmatch(speaker_id_sha256):
        raise GOPError("speaker_id_sha256 must be a lowercase SHA-256")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise GOPError("seed must be an integer")
    if (
        isinstance(fold_count, bool)
        or not isinstance(fold_count, int)
        or fold_count < 2
    ):
        raise GOPError("fold_count must be an integer of at least two")
    if not namespace:
        raise GOPError("namespace must be non-empty")
    digest = hashlib.sha256(f"{namespace}:{seed}:{speaker_id_sha256}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big") % fold_count


def assign_speaker_folds(
    speaker_ids: object,
    *,
    seed: int,
    fold_count: int = 5,
    namespace: str = DEFAULT_FOLD_NAMESPACE,
) -> IntArray:
    speakers = np.asarray(speaker_ids, dtype=np.str_)
    if speakers.ndim != 1 or speakers.shape[0] == 0:
        raise GOPError("speaker_ids must be a non-empty one-dimensional vector")
    by_speaker = {
        speaker: assign_speaker_fold(
            speaker,
            seed=seed,
            fold_count=fold_count,
            namespace=namespace,
        )
        for speaker in set(speakers.tolist())
    }
    return np.asarray([by_speaker[speaker] for speaker in speakers], dtype=np.int64)


def _features_from_fold_blocks(
    *,
    embeddings: FloatArray,
    target_labels: NDArray[np.str_],
    folds: IntArray,
    fold_prototypes: tuple[FloatArray, ...],
    temperature: float,
    inventory: tuple[str, ...],
) -> GOPFeatures:
    matrix = np.empty((embeddings.shape[0], 4), dtype=np.float64)
    coverage = np.zeros(embeddings.shape[0], dtype=np.int8)
    for fold, prototypes in enumerate(fold_prototypes):
        validation = folds == fold
        if not np.any(validation):
            raise GOPError(f"speaker fold {fold} has no validation rows")
        matrix[validation] = compute_gop_features(
            embeddings[validation],
            target_labels[validation],
            prototypes,
            temperature,
            inventory=inventory,
        ).as_matrix()
        coverage[validation] += 1
    if not np.all(coverage == 1):
        raise GOPError("every row must receive exactly one cross-fit feature row")
    return GOPFeatures(
        target_log_posterior=matrix[:, 0],
        gop_margin=matrix[:, 1],
        posterior_entropy=matrix[:, 2],
        target_is_top1=matrix[:, 3],
    )


def cross_fit_gop_features(
    embeddings: object,
    target_labels: object,
    match_mask: object,
    speaker_ids: object,
    *,
    seed: int,
    temperatures: tuple[float, ...],
    temperature_validation_folds: tuple[int, ...] = (0,),
    fold_count: int = 5,
    namespace: str = DEFAULT_FOLD_NAMESPACE,
    inventory: tuple[str, ...] = PHONE_INVENTORY,
) -> CrossFitGOPResult:
    """Select temperature and produce leak-free speaker cross-fit GOP rows."""

    values = _as_float_matrix(embeddings, name="embeddings")
    labels, target_indexes = _target_indexes(
        target_labels,
        inventory=inventory,
        rows=values.shape[0],
    )
    matches = np.asarray(match_mask)
    if (
        matches.ndim != 1
        or matches.shape[0] != values.shape[0]
        or matches.dtype.kind != "b"
    ):
        raise GOPError("match_mask must be a boolean vector matching embeddings")
    speakers = _as_string_vector(
        speaker_ids,
        name="speaker_ids",
        rows=values.shape[0],
    )
    candidates = tuple(float(value) for value in temperatures)
    if (
        not candidates
        or len(set(candidates)) != len(candidates)
        or any(not math.isfinite(value) or value <= 0.0 for value in candidates)
    ):
        raise GOPError("temperatures must be unique finite positive values")
    temperature_folds = tuple(int(value) for value in temperature_validation_folds)
    if (
        not temperature_folds
        or len(set(temperature_folds)) != len(temperature_folds)
        or any(value < 0 or value >= fold_count for value in temperature_folds)
    ):
        raise GOPError("temperature_validation_folds must be unique valid fold indexes")
    folds = assign_speaker_folds(
        speakers,
        seed=seed,
        fold_count=fold_count,
        namespace=namespace,
    )
    fold_prototypes: list[FloatArray] = []
    nll_sums = np.zeros(len(candidates), dtype=np.float64)
    match_counts = np.zeros(len(candidates), dtype=np.int64)
    for fold in range(fold_count):
        training = folds != fold
        validation = folds == fold
        if not np.any(training) or not np.any(validation):
            raise GOPError(f"speaker fold {fold} is empty")
        if set(speakers[training]).intersection(set(speakers[validation])):
            raise GOPError(f"speaker leakage detected in fold {fold}")
        prototypes = fit_match_prototypes(
            values[training],
            labels[training],
            matches[training],
            inventory=inventory,
        )
        fold_prototypes.append(prototypes)
        if fold not in temperature_folds:
            continue
        validation_match = validation & matches
        if not np.any(validation_match):
            raise GOPError(f"speaker fold {fold} has no match validation rows")
        scores = _cosine_scores(values[validation_match], prototypes)
        validation_targets = target_indexes[validation_match]
        rows = np.arange(validation_targets.shape[0], dtype=np.int64)
        for candidate_index, temperature in enumerate(candidates):
            log_posterior = _log_softmax(scores, temperature)
            nll_sums[candidate_index] += float(
                -np.sum(log_posterior[rows, validation_targets], dtype=np.float64)
            )
            match_counts[candidate_index] += validation_targets.shape[0]
    if np.any(match_counts == 0):
        raise GOPError("temperature selection has no match validation rows")
    traces = tuple(
        TemperatureCandidate(
            temperature=temperature,
            match_negative_log_likelihood=float(nll_sums[index] / match_counts[index]),
            match_row_count=int(match_counts[index]),
        )
        for index, temperature in enumerate(candidates)
    )
    selected = min(
        traces,
        key=lambda item: (
            item.match_negative_log_likelihood,
            item.temperature,
        ),
    )
    prototypes_tuple = tuple(fold_prototypes)
    features = _features_from_fold_blocks(
        embeddings=values,
        target_labels=labels,
        folds=folds,
        fold_prototypes=prototypes_tuple,
        temperature=selected.temperature,
        inventory=inventory,
    )
    return CrossFitGOPResult(
        features=features,
        folds=folds,
        selected_temperature=selected.temperature,
        temperature_candidates=traces,
        fold_prototypes=prototypes_tuple,
    )
