"""m3 (duration/pause), m6 (rhythm/nPVI) and m8 (speaker similarity) as S1 branches.

The measurements already exist — ``features/explicit.py`` computes pauses, voiced
ratio and F0; ``npvi()`` computes the rhythm index; ``evaluation/speaker_similarity``
produces a cosine score. What was missing is the step that turns them into
:class:`S1Branch` records the fusion can weigh, with their states intact.

The same boundary as m4/m5 applies and is the reason this module is short: a pause
count and an nPVI value are *measurements*, and the comparison that makes them a
score is ``d(x, R)`` against a reference bank. Only m8 arrives already normalised —
a cosine similarity is a comparison by construction, so it is the one branch here
that can carry a score without a reference bank.

nPVI has one extra trap. It is undefined for fewer than two intervals, and
``npvi()`` signals that with ``None``. Turning that ``None`` into 0.0 would report
perfectly even rhythm for an utterance too short to have rhythm at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from voice_coach.evaluation.speaker_similarity import SpeakerSimilarityResult
from voice_coach.features.explicit import ExplicitFeatures, npvi

from ..domain import MetricState, S1Branch

DURATION_BRANCH = "duration"
PAUSE_BRANCH = "pause"
RHYTHM_BRANCH = "rhythm"
SPEAKER_SIMILARITY_BRANCH = "speaker_similarity"
#: nPVI needs at least two intervals to have a value at all.
MINIMUM_RHYTHM_INTERVALS = 2
#: nPVI is a percentage-style index in [0, 200]; this is the divisor that maps it
#: onto the unit interval when a reference comparison is not what is being asked.
NPVI_SCALE = 200.0


class ExplicitBranchError(ValueError):
    """Raised when a measurement set breaks the branch contract."""


@dataclass(frozen=True, slots=True)
class ExplicitBranchProvenance:
    """Backend identity every branch built here reports."""

    backend_id: str = "explicit-signal-features"
    revision: str = "voice_coach.features.explicit.v1"

    def __post_init__(self) -> None:
        if not self.backend_id or not self.revision:
            raise ExplicitBranchError("provenance must name a backend and revision")


def _measurement_only_branch(
    name: str,
    provenance: ExplicitBranchProvenance,
    *,
    reason: str,
) -> S1Branch:
    return S1Branch(
        name=name,
        score=None,
        confidence=None,
        state=MetricState.MISSING,
        backend_id=provenance.backend_id,
        revision=provenance.revision,
        config_hash=reason,
    )


def build_duration_pause_branches(
    features: ExplicitFeatures,
    *,
    duration_reference_score: float | None = None,
    pause_reference_score: float | None = None,
    provenance: ExplicitBranchProvenance | None = None,
) -> tuple[S1Branch, ...]:
    """m3: speaking-rate and pause branches.

    ``pause_count`` and ``pause_ratio`` describe *this* recording; whether that
    pausing is acceptable is a comparison, so a score requires a reference.
    """

    marks = provenance or ExplicitBranchProvenance()
    for name, value in (
        ("duration_reference_score", duration_reference_score),
        ("pause_reference_score", pause_reference_score),
    ):
        if value is not None and not 0.0 <= float(value) <= 1.0:
            raise ExplicitBranchError(f"{name} must be within [0, 1]")
    if features.duration_sec <= 0:
        raise ExplicitBranchError("duration_sec must be positive")

    branches: list[S1Branch] = []
    for branch_name, reference in (
        (DURATION_BRANCH, duration_reference_score),
        (PAUSE_BRANCH, pause_reference_score),
    ):
        if reference is None:
            branches.append(
                _measurement_only_branch(
                    branch_name,
                    marks,
                    reason="no_reference_bank_measurement_only",
                )
            )
            continue
        branches.append(
            S1Branch(
                name=branch_name,
                score=float(reference),
                confidence=1.0,
                state=MetricState.OBSERVED,
                backend_id=marks.backend_id,
                revision=marks.revision,
            )
        )
    return tuple(branches)


def build_rhythm_branch(
    interval_durations: Sequence[float],
    *,
    reference_score: float | None = None,
    provenance: ExplicitBranchProvenance | None = None,
) -> S1Branch:
    """m6: rhythm from nPVI over successive interval durations.

    Undefined below two intervals, and that stays ``MISSING``: an utterance too
    short to have rhythm must not report perfectly even rhythm.
    """

    marks = provenance or ExplicitBranchProvenance()
    if reference_score is not None and not 0.0 <= float(reference_score) <= 1.0:
        raise ExplicitBranchError("reference_score must be within [0, 1]")
    if len(interval_durations) < MINIMUM_RHYTHM_INTERVALS:
        return _measurement_only_branch(
            RHYTHM_BRANCH, marks, reason="fewer_than_two_intervals"
        )
    try:
        value = npvi(list(interval_durations))  # type: ignore[arg-type]
    except ValueError as error:
        raise ExplicitBranchError(f"rhythm measurement is unusable: {error}") from error
    if value is None:
        return _measurement_only_branch(
            RHYTHM_BRANCH, marks, reason="npvi_undefined"
        )
    if reference_score is None:
        return _measurement_only_branch(
            RHYTHM_BRANCH, marks, reason="no_reference_bank_measurement_only"
        )
    return S1Branch(
        name=RHYTHM_BRANCH,
        score=float(reference_score),
        confidence=1.0,
        state=MetricState.OBSERVED,
        backend_id=marks.backend_id,
        revision=marks.revision,
    )


def normalized_npvi(interval_durations: Sequence[float]) -> float | None:
    """nPVI mapped onto ``[0, 1]``, or ``None`` when undefined.

    Exposed separately from the branch builder so a caller that genuinely wants
    the index — a report, a reference-bank diversity axis — can have it without
    the branch pretending the index is an agreement score.
    """

    if len(interval_durations) < MINIMUM_RHYTHM_INTERVALS:
        return None
    value = npvi(list(interval_durations))  # type: ignore[arg-type]
    if value is None:
        return None
    return max(0.0, min(1.0, float(value) / NPVI_SCALE))


def build_speaker_similarity_branch(
    result: SpeakerSimilarityResult,
    *,
    backend_id: str = "wavlm-sv",
    revision: str = "voice_coach.evaluation.speaker_similarity.v1",
) -> S1Branch:
    """m8: cosine similarity to reference speakers.

    The one branch here that needs no reference bank to be a score: a cosine
    similarity is already a comparison, and ``reference_count`` records how many
    references it was taken against. Zero references means no comparison happened.
    """

    if result.reference_count <= 0:
        return S1Branch(
            name=SPEAKER_SIMILARITY_BRANCH,
            score=None,
            confidence=None,
            state=MetricState.MISSING,
            backend_id=backend_id,
            revision=revision,
            config_hash="no_reference_speaker",
        )
    score = float(result.score)
    if not -1.0 <= score <= 1.0:
        raise ExplicitBranchError("speaker similarity must be a cosine in [-1, 1]")
    # Cosine spans [-1, 1]; the branch contract is [0, 1]. Negative similarity is
    # clamped rather than rescaled: it means "not this speaker", and stretching it
    # would spend half the branch's range on distinctions below that threshold.
    return S1Branch(
        name=SPEAKER_SIMILARITY_BRANCH,
        score=max(0.0, min(1.0, score)),
        confidence=1.0,
        state=MetricState.OBSERVED,
        backend_id=backend_id,
        revision=revision,
        config_hash=result.result_digest_sha256,
    )


__all__ = [
    "DURATION_BRANCH",
    "MINIMUM_RHYTHM_INTERVALS",
    "NPVI_SCALE",
    "PAUSE_BRANCH",
    "RHYTHM_BRANCH",
    "SPEAKER_SIMILARITY_BRANCH",
    "ExplicitBranchError",
    "ExplicitBranchProvenance",
    "build_duration_pause_branches",
    "build_rhythm_branch",
    "build_speaker_similarity_branch",
    "normalized_npvi",
]
