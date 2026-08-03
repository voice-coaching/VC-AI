"""Pure, source-blind Pareto and diversity selection primitives for R03.

This module deliberately performs no filesystem, audio, model, GPU, network,
source-lineage, or R04/outcome access.  It accepts only the selector view
allowed by the existing R03 contract.  Every scientifically unresolved choice
is an explicit policy argument; this module does not provide experiment
defaults or authorize an R03 production run.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import hmac
import itertools
import json
import math
import re
from typing import Literal


Direction = Literal["maximize", "minimize"]
NormalizationMethod = Literal["identity", "minmax", "zscore_population"]
NormalizationScope = Literal["per_sentence", "global"]
ConstantObjectivePolicy = Literal["error", "zero"]
TiePolicy = Literal["keep_all", "canonical_candidate_id"]
FrontierShortfallPolicy = Literal["fail", "fill_by_front_rank"]
FrontierOverflowPolicy = Literal["fail", "diversity_select"]
DistanceMetric = Literal["weighted_euclidean", "weighted_manhattan"]
SelectionAlgorithm = Literal["exhaustive_maximin"]
CandidateOrderPolicy = Literal["canonical_sentence_then_candidate_id"]

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CANDIDATE_ID_RE = re.compile(r"^r01c_[0-9a-f]{32}$")
SENTENCE_ID_RE = re.compile(r"^R00-(?:0[1-9]|1[0-9]|20)$")
SPEED_BUCKETS = frozenset({"slow", "neutral", "fast"})
PITCH_BUCKETS = frozenset({"low", "neutral", "high"})

SELECTOR_INPUT_FIELDS = frozenset(
    {
        "candidate_id",
        "sentence_id",
        "target_text_sha256",
        "r02_eligible_row_sha256",
        "objective_vector",
        "speed_bucket",
        "pitch_bucket",
        "blinded_speaker_cluster_id_sha256",
    }
)

DENYLISTED_FIELDS = frozenset(
    {
        "source_kind",
        "source_attestation_sha256",
        "human_or_tts",
        "model_name",
        "r04_label",
        "r04_score",
        "mos",
        "cmos",
        "learner_outcome",
        "learning_gain",
        "retention",
        "publication_preference",
        "speaker_name",
        "legal_name",
        "raw_speaker_id",
        "source_path",
        "audio_path",
        "recording_path",
        "target_text",
        "transcript",
        "free_text_note",
    }
)


class R03SelectionError(ValueError):
    """Raised when a policy or source-blind selection input fails closed."""


@dataclass(frozen=True)
class NormalizationPolicy:
    """Explicit normalization choices for the selector view."""

    method: NormalizationMethod
    scope: NormalizationScope
    clipping_bounds: tuple[float, float] | None
    constant_objective_policy: ConstantObjectivePolicy


@dataclass(frozen=True)
class ParetoPolicy:
    """Explicit objective direction, tolerance, tie, and frontier policies."""

    direction_by_objective: Mapping[str, Direction]
    epsilon_by_objective: Mapping[str, float]
    tie_policy: TiePolicy
    frontier_shortfall_policy: FrontierShortfallPolicy
    frontier_overflow_policy: FrontierOverflowPolicy


@dataclass(frozen=True)
class DiversityPolicy:
    """Explicit distance, seed, search, and categorical floor choices."""

    distance_metric: DistanceMetric
    distance_objective_ids: tuple[str, ...]
    objective_weights: Mapping[str, float]
    speaker_mismatch_weight: float
    speed_mismatch_weight: float
    pitch_mismatch_weight: float
    minimum_separation: float
    minimum_distinct_speakers: int
    minimum_distinct_speed_buckets: int
    minimum_distinct_pitch_buckets: int
    selection_algorithm: SelectionAlgorithm
    seed: int
    combination_limit: int


@dataclass(frozen=True)
class SelectorPolicy:
    """Complete caller-supplied policy for one deterministic selection run."""

    objective_ids: tuple[str, ...]
    selected_count: int
    candidate_order_policy: CandidateOrderPolicy
    normalization: NormalizationPolicy
    pareto: ParetoPolicy
    diversity: DiversityPolicy


@dataclass(frozen=True)
class SentenceSelection:
    """Source-blind in-memory trace for one sentence."""

    sentence_id: str
    candidate_count: int
    non_dominated_fronts: tuple[tuple[str, ...], ...]
    tie_suppressed_candidate_ids: tuple[str, ...]
    selected_candidate_ids: tuple[str, ...]
    distinct_speaker_count: int
    distinct_speed_bucket_count: int
    distinct_pitch_bucket_count: int
    minimum_pairwise_distance: float
    pairwise_distance_sum: float
    combinations_evaluated: int
    normalized_view_sha256: str

    def trace_payload(self) -> dict[str, object]:
        """Return a JSON-safe D1 in-memory trace without source/outcome fields."""

        return {
            "sentence_id": self.sentence_id,
            "candidate_count": self.candidate_count,
            "non_dominated_fronts": [
                list(front) for front in self.non_dominated_fronts
            ],
            "tie_suppressed_candidate_ids": list(self.tie_suppressed_candidate_ids),
            "selected_candidate_ids": list(self.selected_candidate_ids),
            "distinct_speaker_count": self.distinct_speaker_count,
            "distinct_speed_bucket_count": self.distinct_speed_bucket_count,
            "distinct_pitch_bucket_count": self.distinct_pitch_bucket_count,
            "minimum_pairwise_distance": self.minimum_pairwise_distance,
            "pairwise_distance_sum": self.pairwise_distance_sum,
            "combinations_evaluated": self.combinations_evaluated,
            "normalized_view_sha256": self.normalized_view_sha256,
        }


@dataclass(frozen=True)
class SelectionResult:
    """Deterministic source-blind selection and replay evidence in memory."""

    sentences: tuple[SentenceSelection, ...]
    input_sha256: str
    policy_sha256: str
    trace_sha256: str
    replay_sha256: str

    def trace_payload(self) -> dict[str, object]:
        """Rebuild the exact payload bound by ``trace_sha256``."""

        return {
            "schema_version": "voice_coach.r03_source_blind_selection_trace.v1",
            "status": "selection_primitive_completed_not_r03_completion",
            "input_sha256": self.input_sha256,
            "policy_sha256": self.policy_sha256,
            "sentences": [sentence.trace_payload() for sentence in self.sentences],
            "source_or_r04_fields_accessed": False,
        }


@dataclass(frozen=True)
class _Candidate:
    candidate_id: str
    sentence_id: str
    target_text_sha256: str
    r02_eligible_row_sha256: str
    objective_vector: Mapping[str, float]
    speed_bucket: str
    pitch_bucket: str
    blinded_speaker_cluster_id_sha256: str


@dataclass(frozen=True)
class _NormalizedCandidate:
    candidate: _Candidate
    normalized_objectives: Mapping[str, float]
    oriented_objectives: Mapping[str, float]


@dataclass(frozen=True)
class _CombinationChoice:
    selected: tuple[_NormalizedCandidate, ...]
    minimum_distance: float
    distance_sum: float
    combinations_evaluated: int


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise R03SelectionError(f"value is not finite canonical JSON: {exc}") from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise R03SelectionError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise R03SelectionError(f"{name} must be finite")
    return result


def _positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise R03SelectionError(f"{name} must be a positive integer")
    return value


def _sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise R03SelectionError(f"{name} must be a lowercase SHA-256")
    return value


def _find_denylisted_field(value: object, path: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise R03SelectionError(f"{path} keys must be strings")
            if key in DENYLISTED_FIELDS:
                raise R03SelectionError(
                    f"{path}.{key} is denylisted from source-blind selection"
                )
            _find_denylisted_field(item, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _find_denylisted_field(item, f"{path}[{index}]")


def _validate_policy(policy: SelectorPolicy) -> None:
    objective_ids = policy.objective_ids
    if (
        not objective_ids
        or any(not isinstance(item, str) or not item for item in objective_ids)
        or len(set(objective_ids)) != len(objective_ids)
    ):
        raise R03SelectionError("objective_ids must be unique non-empty strings")
    denylisted_objectives = sorted(set(objective_ids) & DENYLISTED_FIELDS)
    if denylisted_objectives:
        raise R03SelectionError(
            f"objective_ids contain denylisted fields: {denylisted_objectives}"
        )
    if (
        isinstance(policy.selected_count, bool)
        or not isinstance(policy.selected_count, int)
        or not 3 <= policy.selected_count <= 5
    ):
        raise R03SelectionError(
            "selected_count must be explicitly set within the paper-backed 3-5 range"
        )
    if policy.candidate_order_policy != "canonical_sentence_then_candidate_id":
        raise R03SelectionError("unsupported candidate order policy")

    normalization = policy.normalization
    if normalization.method not in {
        "identity",
        "minmax",
        "zscore_population",
    }:
        raise R03SelectionError("unsupported normalization method")
    if normalization.scope not in {"per_sentence", "global"}:
        raise R03SelectionError("unsupported normalization scope")
    if normalization.constant_objective_policy not in {"error", "zero"}:
        raise R03SelectionError("unsupported constant objective policy")
    if normalization.clipping_bounds is not None:
        bounds = normalization.clipping_bounds
        if not isinstance(bounds, tuple) or len(bounds) != 2:
            raise R03SelectionError("clipping_bounds must be a two-item tuple or None")
        lower = _finite_number(bounds[0], "clipping lower bound")
        upper = _finite_number(bounds[1], "clipping upper bound")
        if lower > upper:
            raise R03SelectionError("clipping lower bound must not exceed upper bound")

    pareto = policy.pareto
    expected = set(objective_ids)
    if set(pareto.direction_by_objective) != expected:
        raise R03SelectionError(
            "direction_by_objective keys must exactly match objective_ids"
        )
    if set(pareto.epsilon_by_objective) != expected:
        raise R03SelectionError(
            "epsilon_by_objective keys must exactly match objective_ids"
        )
    for objective_id in objective_ids:
        if pareto.direction_by_objective[objective_id] not in {
            "maximize",
            "minimize",
        }:
            raise R03SelectionError(
                f"unsupported direction for objective {objective_id!r}"
            )
        epsilon = _finite_number(
            pareto.epsilon_by_objective[objective_id],
            f"epsilon[{objective_id}]",
        )
        if epsilon < 0.0:
            raise R03SelectionError("epsilon values must be non-negative")
    if pareto.tie_policy not in {"keep_all", "canonical_candidate_id"}:
        raise R03SelectionError("unsupported Pareto tie policy")
    if pareto.frontier_shortfall_policy not in {
        "fail",
        "fill_by_front_rank",
    }:
        raise R03SelectionError("unsupported frontier shortfall policy")
    if pareto.frontier_overflow_policy not in {"fail", "diversity_select"}:
        raise R03SelectionError("unsupported frontier overflow policy")

    diversity = policy.diversity
    if diversity.distance_metric not in {
        "weighted_euclidean",
        "weighted_manhattan",
    }:
        raise R03SelectionError("unsupported diversity distance metric")
    if diversity.selection_algorithm != "exhaustive_maximin":
        raise R03SelectionError("unsupported diversity selection algorithm")
    if len(set(diversity.distance_objective_ids)) != len(
        diversity.distance_objective_ids
    ) or not set(diversity.distance_objective_ids).issubset(expected):
        raise R03SelectionError(
            "distance_objective_ids must be a unique subset of objective_ids"
        )
    if set(diversity.objective_weights) != set(diversity.distance_objective_ids):
        raise R03SelectionError(
            "objective_weights keys must exactly match distance_objective_ids"
        )

    distance_weights = [
        _finite_number(
            diversity.objective_weights[objective_id],
            f"objective weight[{objective_id}]",
        )
        for objective_id in diversity.distance_objective_ids
    ]
    categorical_weights = [
        _finite_number(
            diversity.speaker_mismatch_weight,
            "speaker_mismatch_weight",
        ),
        _finite_number(
            diversity.speed_mismatch_weight,
            "speed_mismatch_weight",
        ),
        _finite_number(
            diversity.pitch_mismatch_weight,
            "pitch_mismatch_weight",
        ),
    ]
    if any(weight < 0.0 for weight in (*distance_weights, *categorical_weights)):
        raise R03SelectionError("distance weights must be non-negative")
    if not any(weight > 0.0 for weight in (*distance_weights, *categorical_weights)):
        raise R03SelectionError("at least one distance weight must be positive")

    minimum_separation = _finite_number(
        diversity.minimum_separation,
        "minimum_separation",
    )
    if minimum_separation < 0.0:
        raise R03SelectionError("minimum_separation must be non-negative")
    for name, value in (
        ("minimum_distinct_speakers", diversity.minimum_distinct_speakers),
        (
            "minimum_distinct_speed_buckets",
            diversity.minimum_distinct_speed_buckets,
        ),
        (
            "minimum_distinct_pitch_buckets",
            diversity.minimum_distinct_pitch_buckets,
        ),
    ):
        floor = _positive_integer(value, name)
        if floor > policy.selected_count:
            raise R03SelectionError(f"{name} cannot exceed selected_count")
    if isinstance(diversity.seed, bool) or not isinstance(diversity.seed, int):
        raise R03SelectionError("seed must be an explicit integer")
    _positive_integer(diversity.combination_limit, "combination_limit")


def _policy_payload(policy: SelectorPolicy) -> dict[str, object]:
    return {
        "objective_ids": list(policy.objective_ids),
        "selected_count": policy.selected_count,
        "candidate_order_policy": policy.candidate_order_policy,
        "normalization": {
            "method": policy.normalization.method,
            "scope": policy.normalization.scope,
            "clipping_bounds": (
                list(policy.normalization.clipping_bounds)
                if policy.normalization.clipping_bounds is not None
                else None
            ),
            "constant_objective_policy": (
                policy.normalization.constant_objective_policy
            ),
        },
        "pareto": {
            "direction_by_objective": dict(policy.pareto.direction_by_objective),
            "epsilon_by_objective": dict(policy.pareto.epsilon_by_objective),
            "tie_policy": policy.pareto.tie_policy,
            "frontier_shortfall_policy": (policy.pareto.frontier_shortfall_policy),
            "frontier_overflow_policy": (policy.pareto.frontier_overflow_policy),
        },
        "diversity": {
            "distance_metric": policy.diversity.distance_metric,
            "distance_objective_ids": list(policy.diversity.distance_objective_ids),
            "objective_weights": dict(policy.diversity.objective_weights),
            "speaker_mismatch_weight": (policy.diversity.speaker_mismatch_weight),
            "speed_mismatch_weight": policy.diversity.speed_mismatch_weight,
            "pitch_mismatch_weight": policy.diversity.pitch_mismatch_weight,
            "minimum_separation": policy.diversity.minimum_separation,
            "minimum_distinct_speakers": (policy.diversity.minimum_distinct_speakers),
            "minimum_distinct_speed_buckets": (
                policy.diversity.minimum_distinct_speed_buckets
            ),
            "minimum_distinct_pitch_buckets": (
                policy.diversity.minimum_distinct_pitch_buckets
            ),
            "selection_algorithm": policy.diversity.selection_algorithm,
            "seed": policy.diversity.seed,
            "combination_limit": policy.diversity.combination_limit,
        },
    }


def _parse_candidates(
    rows: Sequence[Mapping[str, object]],
    *,
    objective_ids: tuple[str, ...],
) -> tuple[_Candidate, ...]:
    if isinstance(rows, (str, bytes, bytearray)) or not isinstance(rows, Sequence):
        raise R03SelectionError("candidate rows must be a non-empty sequence")
    if not rows:
        raise R03SelectionError("candidate rows must not be empty")

    candidates: list[_Candidate] = []
    seen_candidate_ids: set[str] = set()
    target_by_sentence: dict[str, str] = {}
    expected_objectives = set(objective_ids)
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise R03SelectionError(f"candidate[{index}] must be a mapping")
        _find_denylisted_field(row, f"candidate[{index}]")
        actual_fields = set(row)
        if actual_fields != SELECTOR_INPUT_FIELDS:
            missing = sorted(SELECTOR_INPUT_FIELDS - actual_fields)
            extra = sorted(actual_fields - SELECTOR_INPUT_FIELDS)
            raise R03SelectionError(
                "candidate selector fields must exactly match the source-blind "
                f"allowlist; missing={missing}, extra={extra}"
            )

        candidate_id = row.get("candidate_id")
        if not isinstance(candidate_id, str) or not CANDIDATE_ID_RE.fullmatch(
            candidate_id
        ):
            raise R03SelectionError(f"candidate[{index}].candidate_id is invalid")
        if candidate_id in seen_candidate_ids:
            raise R03SelectionError(f"duplicate candidate_id: {candidate_id}")
        seen_candidate_ids.add(candidate_id)

        sentence_id = row.get("sentence_id")
        if not isinstance(sentence_id, str) or not SENTENCE_ID_RE.fullmatch(
            sentence_id
        ):
            raise R03SelectionError(f"candidate[{index}].sentence_id is invalid")
        target_sha = _sha256(
            row.get("target_text_sha256"),
            f"candidate[{index}].target_text_sha256",
        )
        prior_target = target_by_sentence.setdefault(sentence_id, target_sha)
        if target_sha != prior_target:
            raise R03SelectionError(
                f"target_text_sha256 mismatch within sentence {sentence_id}"
            )

        objective_value = row.get("objective_vector")
        if not isinstance(objective_value, Mapping):
            raise R03SelectionError(
                f"candidate[{index}].objective_vector must be a mapping"
            )
        if set(objective_value) != expected_objectives:
            missing = sorted(expected_objectives - set(objective_value))
            extra = sorted(set(objective_value) - expected_objectives)
            raise R03SelectionError(
                f"candidate[{index}] objective keys mismatch; "
                f"missing={missing}, extra={extra}"
            )
        objectives = {
            objective_id: _finite_number(
                objective_value[objective_id],
                f"candidate[{index}].objective_vector[{objective_id}]",
            )
            for objective_id in objective_ids
        }

        speed_bucket = row.get("speed_bucket")
        if speed_bucket not in SPEED_BUCKETS:
            raise R03SelectionError(f"candidate[{index}].speed_bucket is invalid")
        pitch_bucket = row.get("pitch_bucket")
        if pitch_bucket not in PITCH_BUCKETS:
            raise R03SelectionError(f"candidate[{index}].pitch_bucket is invalid")
        candidates.append(
            _Candidate(
                candidate_id=candidate_id,
                sentence_id=sentence_id,
                target_text_sha256=target_sha,
                r02_eligible_row_sha256=_sha256(
                    row.get("r02_eligible_row_sha256"),
                    f"candidate[{index}].r02_eligible_row_sha256",
                ),
                objective_vector=objectives,
                speed_bucket=str(speed_bucket),
                pitch_bucket=str(pitch_bucket),
                blinded_speaker_cluster_id_sha256=_sha256(
                    row.get("blinded_speaker_cluster_id_sha256"),
                    (f"candidate[{index}].blinded_speaker_cluster_id_sha256"),
                ),
            )
        )
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                candidate.sentence_id,
                candidate.candidate_id,
            ),
        )
    )


def _candidate_input_payload(
    candidates: Sequence[_Candidate],
) -> dict[str, object]:
    return {
        "schema_version": "voice_coach.r03_source_blind_selector_input.v1",
        "candidates": [
            {
                "candidate_id": candidate.candidate_id,
                "sentence_id": candidate.sentence_id,
                "target_text_sha256": candidate.target_text_sha256,
                "r02_eligible_row_sha256": (candidate.r02_eligible_row_sha256),
                "objective_vector": dict(candidate.objective_vector),
                "speed_bucket": candidate.speed_bucket,
                "pitch_bucket": candidate.pitch_bucket,
                "blinded_speaker_cluster_id_sha256": (
                    candidate.blinded_speaker_cluster_id_sha256
                ),
            }
            for candidate in candidates
        ],
    }


def _normalize_group(
    candidates: Sequence[_Candidate],
    *,
    objective_ids: tuple[str, ...],
    policy: NormalizationPolicy,
) -> dict[str, dict[str, float]]:
    normalized: dict[str, dict[str, float]] = {
        candidate.candidate_id: {} for candidate in candidates
    }
    for objective_id in objective_ids:
        values = [candidate.objective_vector[objective_id] for candidate in candidates]
        if policy.method == "identity":
            transformed = values
        elif policy.method == "minmax":
            lower = min(values)
            span = max(values) - lower
            if span == 0.0:
                if policy.constant_objective_policy == "error":
                    raise R03SelectionError(
                        f"constant objective {objective_id!r} cannot be minmax normalized"
                    )
                transformed = [0.0] * len(values)
            else:
                transformed = [(value - lower) / span for value in values]
        else:
            mean = math.fsum(values) / len(values)
            variance = math.fsum((value - mean) ** 2 for value in values) / len(values)
            if variance == 0.0:
                if policy.constant_objective_policy == "error":
                    raise R03SelectionError(
                        f"constant objective {objective_id!r} cannot be zscore normalized"
                    )
                transformed = [0.0] * len(values)
            else:
                standard_deviation = math.sqrt(variance)
                transformed = [(value - mean) / standard_deviation for value in values]

        if policy.clipping_bounds is not None:
            clip_lower, clip_upper = policy.clipping_bounds
            transformed = [
                min(max(value, clip_lower), clip_upper) for value in transformed
            ]
        for candidate, value in zip(candidates, transformed, strict=True):
            if not math.isfinite(value):
                raise R03SelectionError(
                    f"normalization produced non-finite {objective_id!r}"
                )
            normalized[candidate.candidate_id][objective_id] = value
    return normalized


def _normalize_candidates(
    candidates: Sequence[_Candidate],
    *,
    policy: SelectorPolicy,
) -> tuple[_NormalizedCandidate, ...]:
    by_sentence: dict[str, list[_Candidate]] = defaultdict(list)
    for candidate in candidates:
        by_sentence[candidate.sentence_id].append(candidate)

    normalized_by_id: dict[str, dict[str, float]] = {}
    if policy.normalization.scope == "global":
        normalized_by_id.update(
            _normalize_group(
                candidates,
                objective_ids=policy.objective_ids,
                policy=policy.normalization,
            )
        )
    else:
        for sentence_id in sorted(by_sentence):
            normalized_by_id.update(
                _normalize_group(
                    by_sentence[sentence_id],
                    objective_ids=policy.objective_ids,
                    policy=policy.normalization,
                )
            )

    result: list[_NormalizedCandidate] = []
    for candidate in candidates:
        normalized = normalized_by_id[candidate.candidate_id]
        oriented = {
            objective_id: (
                normalized[objective_id]
                if policy.pareto.direction_by_objective[objective_id] == "maximize"
                else -normalized[objective_id]
            )
            for objective_id in policy.objective_ids
        }
        result.append(
            _NormalizedCandidate(
                candidate=candidate,
                normalized_objectives=normalized,
                oriented_objectives=oriented,
            )
        )
    return tuple(result)


def _epsilon_tied(
    left: _NormalizedCandidate,
    right: _NormalizedCandidate,
    *,
    objective_ids: tuple[str, ...],
    epsilon_by_objective: Mapping[str, float],
) -> bool:
    return all(
        abs(
            left.oriented_objectives[objective_id]
            - right.oriented_objectives[objective_id]
        )
        <= epsilon_by_objective[objective_id]
        for objective_id in objective_ids
    )


def _dominates(
    left: _NormalizedCandidate,
    right: _NormalizedCandidate,
    *,
    objective_ids: tuple[str, ...],
    epsilon_by_objective: Mapping[str, float],
) -> bool:
    weakly_better = all(
        left.oriented_objectives[objective_id]
        >= (
            right.oriented_objectives[objective_id] - epsilon_by_objective[objective_id]
        )
        for objective_id in objective_ids
    )
    materially_better = any(
        left.oriented_objectives[objective_id]
        > (right.oriented_objectives[objective_id] + epsilon_by_objective[objective_id])
        for objective_id in objective_ids
    )
    return weakly_better and materially_better


def _apply_tie_policy(
    candidates: Sequence[_NormalizedCandidate],
    *,
    policy: SelectorPolicy,
) -> tuple[tuple[_NormalizedCandidate, ...], tuple[str, ...]]:
    ordered = sorted(
        candidates,
        key=lambda candidate: candidate.candidate.candidate_id,
    )
    if policy.pareto.tie_policy == "keep_all":
        return tuple(ordered), ()

    kept: list[_NormalizedCandidate] = []
    suppressed: list[str] = []
    for candidate in ordered:
        if any(
            _epsilon_tied(
                candidate,
                prior,
                objective_ids=policy.objective_ids,
                epsilon_by_objective=policy.pareto.epsilon_by_objective,
            )
            for prior in kept
        ):
            suppressed.append(candidate.candidate.candidate_id)
        else:
            kept.append(candidate)
    return tuple(kept), tuple(suppressed)


def _non_dominated_fronts(
    candidates: Sequence[_NormalizedCandidate],
    *,
    policy: SelectorPolicy,
) -> tuple[tuple[_NormalizedCandidate, ...], ...]:
    remaining = list(candidates)
    fronts: list[tuple[_NormalizedCandidate, ...]] = []
    while remaining:
        front = [
            candidate
            for candidate in remaining
            if not any(
                other.candidate.candidate_id != candidate.candidate.candidate_id
                and _dominates(
                    other,
                    candidate,
                    objective_ids=policy.objective_ids,
                    epsilon_by_objective=(policy.pareto.epsilon_by_objective),
                )
                for other in remaining
            )
        ]
        if not front:
            raise R03SelectionError(
                "epsilon dominance produced a cycle with no non-dominated candidate"
            )
        front = sorted(
            front,
            key=lambda candidate: candidate.candidate.candidate_id,
        )
        fronts.append(tuple(front))
        front_ids = {candidate.candidate.candidate_id for candidate in front}
        remaining = [
            candidate
            for candidate in remaining
            if candidate.candidate.candidate_id not in front_ids
        ]
    return tuple(fronts)


def _distance(
    left: _NormalizedCandidate,
    right: _NormalizedCandidate,
    *,
    policy: DiversityPolicy,
) -> float:
    numeric_terms: list[float] = []
    for objective_id in policy.distance_objective_ids:
        difference = abs(
            left.normalized_objectives[objective_id]
            - right.normalized_objectives[objective_id]
        )
        weight = policy.objective_weights[objective_id]
        if policy.distance_metric == "weighted_euclidean":
            numeric_terms.append(weight * difference**2)
        else:
            numeric_terms.append(weight * difference)

    categorical_terms = [
        (
            policy.speaker_mismatch_weight
            if left.candidate.blinded_speaker_cluster_id_sha256
            != right.candidate.blinded_speaker_cluster_id_sha256
            else 0.0
        ),
        (
            policy.speed_mismatch_weight
            if left.candidate.speed_bucket != right.candidate.speed_bucket
            else 0.0
        ),
        (
            policy.pitch_mismatch_weight
            if left.candidate.pitch_bucket != right.candidate.pitch_bucket
            else 0.0
        ),
    ]
    total = math.fsum((*numeric_terms, *categorical_terms))
    if policy.distance_metric == "weighted_euclidean":
        total = math.sqrt(total)
    if not math.isfinite(total):
        raise R03SelectionError("diversity distance is non-finite")
    return total


def _combination_metrics(
    selected: Sequence[_NormalizedCandidate],
    *,
    policy: DiversityPolicy,
) -> tuple[float, float]:
    distances = [
        _distance(left, right, policy=policy)
        for left, right in itertools.combinations(selected, 2)
    ]
    if not distances:
        raise R03SelectionError("diversity selection requires at least two candidates")
    return min(distances), math.fsum(distances)


def _meets_diversity_constraints(
    selected: Sequence[_NormalizedCandidate],
    *,
    policy: DiversityPolicy,
) -> tuple[bool, float, float]:
    speakers = {
        candidate.candidate.blinded_speaker_cluster_id_sha256 for candidate in selected
    }
    speeds = {candidate.candidate.speed_bucket for candidate in selected}
    pitches = {candidate.candidate.pitch_bucket for candidate in selected}
    minimum_distance, distance_sum = _combination_metrics(
        selected,
        policy=policy,
    )
    valid = (
        len(speakers) >= policy.minimum_distinct_speakers
        and len(speeds) >= policy.minimum_distinct_speed_buckets
        and len(pitches) >= policy.minimum_distinct_pitch_buckets
        and minimum_distance >= policy.minimum_separation
    )
    return valid, minimum_distance, distance_sum


def _seeded_combination_rank(
    selected: Sequence[_NormalizedCandidate],
    *,
    sentence_id: str,
    seed: int,
) -> str:
    candidate_ids = sorted(candidate.candidate.candidate_id for candidate in selected)
    return _digest(
        {
            "seed": seed,
            "sentence_id": sentence_id,
            "candidate_ids": candidate_ids,
        }
    )


def _choose_diverse_combination(
    *,
    sentence_id: str,
    mandatory: Sequence[_NormalizedCandidate],
    optional: Sequence[_NormalizedCandidate],
    optional_count: int,
    policy: DiversityPolicy,
) -> _CombinationChoice:
    if optional_count < 0 or optional_count > len(optional):
        raise R03SelectionError("invalid diversity optional_count")

    evaluated = 0
    best: tuple[tuple[float, float], str, tuple[_NormalizedCandidate, ...]] | None = (
        None
    )
    for choice in itertools.combinations(
        sorted(
            optional,
            key=lambda candidate: candidate.candidate.candidate_id,
        ),
        optional_count,
    ):
        evaluated += 1
        if evaluated > policy.combination_limit:
            raise R03SelectionError(
                f"diversity combination_limit exceeded for {sentence_id}"
            )
        selected = tuple(
            sorted(
                (*mandatory, *choice),
                key=lambda candidate: candidate.candidate.candidate_id,
            )
        )
        valid, minimum_distance, distance_sum = _meets_diversity_constraints(
            selected,
            policy=policy,
        )
        if not valid:
            continue
        score = (minimum_distance, distance_sum)
        seeded_rank = _seeded_combination_rank(
            selected,
            sentence_id=sentence_id,
            seed=policy.seed,
        )
        if (
            best is None
            or score > best[0]
            or (score == best[0] and seeded_rank < best[1])
        ):
            best = (score, seeded_rank, selected)

    if best is None:
        raise R03SelectionError(
            f"diversity constraints are infeasible for {sentence_id}"
        )
    return _CombinationChoice(
        selected=best[2],
        minimum_distance=best[0][0],
        distance_sum=best[0][1],
        combinations_evaluated=evaluated,
    )


def _select_sentence(
    sentence_id: str,
    candidates: Sequence[_NormalizedCandidate],
    *,
    policy: SelectorPolicy,
) -> SentenceSelection:
    ranked_candidates, suppressed = _apply_tie_policy(
        candidates,
        policy=policy,
    )
    if len(ranked_candidates) < policy.selected_count:
        raise R03SelectionError(
            f"{sentence_id} has fewer candidates than selected_count after tie policy"
        )
    fronts = _non_dominated_fronts(ranked_candidates, policy=policy)
    first_front = fronts[0]
    mandatory: list[_NormalizedCandidate] = []
    boundary: tuple[_NormalizedCandidate, ...]

    if len(first_front) < policy.selected_count:
        if policy.pareto.frontier_shortfall_policy == "fail":
            raise R03SelectionError(f"Pareto frontier shortfall for {sentence_id}")
        mandatory.extend(first_front)
        boundary = ()
        for front in fronts[1:]:
            remaining_slots = policy.selected_count - len(mandatory)
            if len(front) <= remaining_slots:
                mandatory.extend(front)
                if len(mandatory) == policy.selected_count:
                    break
            else:
                boundary = front
                break
        if len(mandatory) < policy.selected_count and not boundary:
            raise R03SelectionError(
                f"front-rank fill cannot satisfy selected_count for {sentence_id}"
            )
    elif len(first_front) == policy.selected_count:
        mandatory.extend(first_front)
        boundary = ()
    else:
        boundary = first_front

    optional_count = policy.selected_count - len(mandatory)
    if len(boundary) > optional_count:
        if policy.pareto.frontier_overflow_policy == "fail":
            raise R03SelectionError(f"Pareto frontier overflow for {sentence_id}")
    elif len(boundary) < optional_count:
        raise R03SelectionError(f"frontier policy cannot fill {sentence_id}")

    choice = _choose_diverse_combination(
        sentence_id=sentence_id,
        mandatory=mandatory,
        optional=boundary,
        optional_count=optional_count,
        policy=policy.diversity,
    )
    selected = choice.selected
    return SentenceSelection(
        sentence_id=sentence_id,
        candidate_count=len(candidates),
        non_dominated_fronts=tuple(
            tuple(candidate.candidate.candidate_id for candidate in front)
            for front in fronts
        ),
        tie_suppressed_candidate_ids=suppressed,
        selected_candidate_ids=tuple(
            candidate.candidate.candidate_id for candidate in selected
        ),
        distinct_speaker_count=len(
            {
                candidate.candidate.blinded_speaker_cluster_id_sha256
                for candidate in selected
            }
        ),
        distinct_speed_bucket_count=len(
            {candidate.candidate.speed_bucket for candidate in selected}
        ),
        distinct_pitch_bucket_count=len(
            {candidate.candidate.pitch_bucket for candidate in selected}
        ),
        minimum_pairwise_distance=choice.minimum_distance,
        pairwise_distance_sum=choice.distance_sum,
        combinations_evaluated=choice.combinations_evaluated,
        normalized_view_sha256=_digest(
            {
                candidate.candidate.candidate_id: dict(candidate.normalized_objectives)
                for candidate in candidates
            }
        ),
    )


def select_source_blind_references(
    rows: Sequence[Mapping[str, object]],
    *,
    policy: SelectorPolicy,
) -> SelectionResult:
    """Select 3–5 candidates per sentence using only the R03 blind view.

    The return value is in-memory evidence only.  It is not an R03 bank,
    completion artifact, source-balance claim, R04 result, or publication
    authorization.
    """

    _validate_policy(policy)
    candidates = _parse_candidates(rows, objective_ids=policy.objective_ids)
    normalized = _normalize_candidates(candidates, policy=policy)
    grouped: dict[str, list[_NormalizedCandidate]] = defaultdict(list)
    for candidate in normalized:
        grouped[candidate.candidate.sentence_id].append(candidate)

    sentence_results = tuple(
        _select_sentence(
            sentence_id,
            grouped[sentence_id],
            policy=policy,
        )
        for sentence_id in sorted(grouped)
    )
    input_sha256 = _digest(_candidate_input_payload(candidates))
    policy_sha256 = _digest(_policy_payload(policy))
    provisional = SelectionResult(
        sentences=sentence_results,
        input_sha256=input_sha256,
        policy_sha256=policy_sha256,
        trace_sha256="",
        replay_sha256="",
    )
    trace_sha256 = _digest(provisional.trace_payload())
    replay_sha256 = _digest(
        {
            "schema_version": "voice_coach.r03_source_blind_replay.v1",
            "input_sha256": input_sha256,
            "policy_sha256": policy_sha256,
            "trace_sha256": trace_sha256,
        }
    )
    return SelectionResult(
        sentences=sentence_results,
        input_sha256=input_sha256,
        policy_sha256=policy_sha256,
        trace_sha256=trace_sha256,
        replay_sha256=replay_sha256,
    )


def replay_source_blind_selection(
    rows: Sequence[Mapping[str, object]],
    *,
    policy: SelectorPolicy,
    expected_replay_sha256: str,
) -> SelectionResult:
    """Recompute a selection and fail closed unless its replay digest matches."""

    _sha256(expected_replay_sha256, "expected_replay_sha256")
    replayed = select_source_blind_references(rows, policy=policy)
    if not hmac.compare_digest(
        replayed.replay_sha256,
        expected_replay_sha256,
    ):
        raise R03SelectionError("selection replay digest mismatch")
    return replayed
