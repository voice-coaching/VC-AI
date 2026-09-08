"""Privacy-minimal cosine similarity for precomputed speaker embeddings.

This module is a pure mathematical primitive.  It does not read audio, run a
WavLM-SV or ECAPA model, inspect consent records, use a GPU, access the
filesystem, or choose an aggregation policy or decision threshold.  A caller
must pass exactly one candidate embedding, a consent-gated reference embedding
collection, and an explicit aggregation enum.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from numbers import Real


L2_NORMALIZATION_POLICY_ID = "per_embedding_l2_float64_v1"
_INPUT_DIGEST_SCHEMA = "voice_coach.speaker_similarity_effective_input.v1"
_RESULT_DIGEST_SCHEMA = "voice_coach.speaker_similarity_result.v1"


class SpeakerSimilarityError(ValueError):
    """Raised when an embedding or aggregation violates the strict contract."""


class SimilarityAggregation(str, Enum):
    """Caller-selected interpretation of candidate-to-reference cosine scores."""

    PAIRWISE_MEAN = "pairwise_mean"
    PAIRWISE_MEDIAN = "pairwise_median"
    PAIRWISE_MINIMUM = "pairwise_minimum"
    PAIRWISE_MAXIMUM = "pairwise_maximum"
    NORMALIZED_CENTROID = "normalized_centroid"


@dataclass(frozen=True, slots=True)
class SpeakerSimilarityResult:
    """Privacy-minimal deterministic result without embeddings or identifiers."""

    score: float
    reference_count: int
    embedding_dimension: int
    normalization_policy: str
    aggregation_policy: SimilarityAggregation
    input_digest_sha256: str
    result_digest_sha256: str

    def to_dict(self) -> dict[str, object]:
        """Return the exact JSON-safe public result fields."""

        return {
            "score": self.score,
            "reference_count": self.reference_count,
            "embedding_dimension": self.embedding_dimension,
            "normalization_policy": self.normalization_policy,
            "aggregation_policy": self.aggregation_policy.value,
            "input_digest_sha256": self.input_digest_sha256,
            "result_digest_sha256": self.result_digest_sha256,
        }


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
        raise SpeakerSimilarityError(
            f"value is not finite canonical JSON: {exc}"
        ) from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _canonical_zero(value: float) -> float:
    return 0.0 if value == 0.0 else value


def _normalize_embedding(
    embedding: Sequence[float],
    *,
    field_name: str,
    expected_dimension: int | None = None,
) -> tuple[float, ...]:
    if isinstance(embedding, (str, bytes, bytearray)) or not isinstance(
        embedding, Sequence
    ):
        raise SpeakerSimilarityError(f"{field_name} must be a numeric sequence")
    if not embedding:
        raise SpeakerSimilarityError(f"{field_name} must not be empty")
    if expected_dimension is not None and len(embedding) != expected_dimension:
        raise SpeakerSimilarityError(
            f"{field_name} dimension must equal candidate dimension "
            f"{expected_dimension}, got {len(embedding)}"
        )

    values: list[float] = []
    for index, value in enumerate(embedding):
        if isinstance(value, bool):
            raise SpeakerSimilarityError(f"{field_name}[{index}] must not be boolean")
        if not isinstance(value, Real):
            raise SpeakerSimilarityError(
                f"{field_name}[{index}] must be a finite real number"
            )
        converted = float(value)
        if not math.isfinite(converted):
            raise SpeakerSimilarityError(f"{field_name}[{index}] must be finite")
        values.append(converted)

    norm = math.hypot(*values)
    if not math.isfinite(norm) or norm == 0.0:
        raise SpeakerSimilarityError(f"{field_name} must have a finite nonzero L2 norm")
    return tuple(_canonical_zero(value / norm) for value in values)


def _cosine(
    normalized_left: tuple[float, ...],
    normalized_right: tuple[float, ...],
) -> float:
    score = math.fsum(
        left * right
        for left, right in zip(normalized_left, normalized_right, strict=True)
    )
    if not math.isfinite(score):
        raise SpeakerSimilarityError("cosine score must be finite")
    return _canonical_zero(max(-1.0, min(1.0, score)))


def _median(values: tuple[float, ...]) -> float:
    ordered = tuple(sorted(values))
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return math.fsum((ordered[middle - 1], ordered[middle])) / 2.0


def _normalized_centroid(
    normalized_references: tuple[tuple[float, ...], ...],
    *,
    embedding_dimension: int,
) -> tuple[float, ...]:
    centroid = tuple(
        math.fsum(reference[index] for reference in normalized_references)
        / len(normalized_references)
        for index in range(embedding_dimension)
    )
    return _normalize_embedding(
        centroid,
        field_name="normalized reference centroid",
        expected_dimension=embedding_dimension,
    )


def _aggregate_score(
    normalized_candidate: tuple[float, ...],
    normalized_references: tuple[tuple[float, ...], ...],
    *,
    aggregation: SimilarityAggregation,
) -> float:
    if aggregation is SimilarityAggregation.NORMALIZED_CENTROID:
        centroid = _normalized_centroid(
            normalized_references,
            embedding_dimension=len(normalized_candidate),
        )
        return _cosine(normalized_candidate, centroid)

    pairwise_scores = tuple(
        _cosine(normalized_candidate, reference) for reference in normalized_references
    )
    if aggregation is SimilarityAggregation.PAIRWISE_MEAN:
        return math.fsum(pairwise_scores) / len(pairwise_scores)
    if aggregation is SimilarityAggregation.PAIRWISE_MEDIAN:
        return _median(pairwise_scores)
    if aggregation is SimilarityAggregation.PAIRWISE_MINIMUM:
        return min(pairwise_scores)
    if aggregation is SimilarityAggregation.PAIRWISE_MAXIMUM:
        return max(pairwise_scores)
    raise SpeakerSimilarityError("unsupported aggregation policy")


def score_speaker_similarity(
    candidate_embedding: Sequence[float],
    consented_reference_embeddings: Sequence[Sequence[float]],
    *,
    aggregation: SimilarityAggregation,
) -> SpeakerSimilarityResult:
    """Score one candidate against consent-gated references.

    The ``consented_`` name documents the required upstream gate; this pure
    function cannot verify consent itself.  Reference order is canonicalized,
    while duplicate references remain distinct observations.  No aggregation
    has a default because the research protocol must freeze that choice before
    results are inspected.
    """

    if not isinstance(aggregation, SimilarityAggregation):
        raise SpeakerSimilarityError(
            "aggregation must be an explicit SimilarityAggregation value"
        )
    if isinstance(
        consented_reference_embeddings, (str, bytes, bytearray)
    ) or not isinstance(consented_reference_embeddings, Sequence):
        raise SpeakerSimilarityError(
            "consented_reference_embeddings must be a sequence"
        )
    if not consented_reference_embeddings:
        raise SpeakerSimilarityError("consented_reference_embeddings must not be empty")

    normalized_candidate = _normalize_embedding(
        candidate_embedding,
        field_name="candidate_embedding",
    )
    embedding_dimension = len(normalized_candidate)
    normalized_references = tuple(
        _normalize_embedding(
            reference,
            field_name=f"consented_reference_embeddings[{index}]",
            expected_dimension=embedding_dimension,
        )
        for index, reference in enumerate(consented_reference_embeddings)
    )
    canonical_references = tuple(
        sorted(normalized_references, key=_canonical_json_bytes)
    )
    score = _canonical_zero(
        _aggregate_score(
            normalized_candidate,
            canonical_references,
            aggregation=aggregation,
        )
    )

    input_digest_sha256 = _digest(
        {
            "schema_version": _INPUT_DIGEST_SCHEMA,
            "normalization_policy": L2_NORMALIZATION_POLICY_ID,
            "aggregation_policy": aggregation.value,
            "candidate_embedding": list(normalized_candidate),
            "consented_reference_embeddings": [
                list(reference) for reference in canonical_references
            ],
        }
    )
    result_payload = {
        "schema_version": _RESULT_DIGEST_SCHEMA,
        "score": score,
        "reference_count": len(canonical_references),
        "embedding_dimension": embedding_dimension,
        "normalization_policy": L2_NORMALIZATION_POLICY_ID,
        "aggregation_policy": aggregation.value,
        "input_digest_sha256": input_digest_sha256,
    }
    return SpeakerSimilarityResult(
        score=score,
        reference_count=len(canonical_references),
        embedding_dimension=embedding_dimension,
        normalization_policy=L2_NORMALIZATION_POLICY_ID,
        aggregation_policy=aggregation,
        input_digest_sha256=input_digest_sha256,
        result_digest_sha256=_digest(result_payload),
    )


__all__ = [
    "L2_NORMALIZATION_POLICY_ID",
    "SimilarityAggregation",
    "SpeakerSimilarityError",
    "SpeakerSimilarityResult",
    "score_speaker_similarity",
]
