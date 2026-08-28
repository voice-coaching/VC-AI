"""Loop C: the learner state that makes ``Trainability`` a measurement, not a constant.

``Priority(e) = Severity × Confidence × ComprehensibilityImpact × Trainability``
multiplies four factors, and today the fourth arrives as a constant. That quietly
turns a four-factor ranking into a three-factor one while looking like the paper's
formula. This module estimates trainability from what the learner actually did —
whether an error type's severity falls across attempts — and, crucially, reports
when it does *not yet know*.

Storage rules, because this is learner data:

* identifiers are hashed before they arrive here and validated on the way in; a
  raw identifier cannot be stored even by mistake;
* nothing audio-derived is retained — no path, no waveform, no PCM digest;
* the store is append-only, so a history cannot be quietly rewritten to make a
  curriculum look better than it was.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .domain import MetricState

LEARNER_STATE_SCHEMA_VERSION = "voice_coach.learner_state.v1"
ATTEMPT_RECORD_SCHEMA_VERSION = "voice_coach.learner_attempt.v1"
#: Below this many attempts on an issue type, a reduction curve is noise.
MINIMUM_OBSERVATIONS_FOR_TRAINABILITY = 3
#: The paper coaches one issue and may name a couple more as next targets.
MAXIMUM_NEXT_TARGETS = 3
LEARNER_LEVELS: tuple[str, ...] = ("unknown", "beginner", "intermediate", "advanced")

_HASHED_ID = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_FIELDS = frozenset(
    {"audio_path", "path", "pcm_sha256", "waveform", "learner_id", "speaker_id"}
)


class LearnerModelError(ValueError):
    """Raised when learner state or an attempt record breaks the contract."""


def _validate_hashed_id(value: str) -> str:
    if not isinstance(value, str) or not _HASHED_ID.fullmatch(value):
        raise LearnerModelError("learner_id_sha256 must be a lowercase sha256 hex digest")
    return value


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """One practice attempt, reduced to what a curriculum needs.

    ``severity`` is ``None`` when the attempt produced no issue of that type,
    which is itself evidence of improvement, so it is recorded rather than dropped.
    """

    learner_id_sha256: str
    prompt_id: str
    attempt_index: int
    recorded_at_utc: str
    status: str
    issue_type: str | None
    severity: float | None
    confidence: float | None

    def __post_init__(self) -> None:
        _validate_hashed_id(self.learner_id_sha256)
        if not self.prompt_id:
            raise LearnerModelError("prompt_id must not be empty")
        if self.attempt_index < 1:
            raise LearnerModelError("attempt_index starts at 1")
        if not self.status:
            raise LearnerModelError("status must be named")
        if not self.recorded_at_utc:
            raise LearnerModelError("recorded_at_utc must be set")
        for name in ("severity", "confidence"):
            value = getattr(self, name)
            if value is None:
                continue
            if not 0.0 <= float(value) <= 1.0:
                raise LearnerModelError(f"{name} must be within [0, 1]")
        if self.issue_type is None and self.severity is not None:
            raise LearnerModelError("severity without an issue type is meaningless")

    def to_json_object(self) -> dict[str, Any]:
        return {
            "schema_version": ATTEMPT_RECORD_SCHEMA_VERSION,
            "learner_id_sha256": self.learner_id_sha256,
            "prompt_id": self.prompt_id,
            "attempt_index": self.attempt_index,
            "recorded_at_utc": self.recorded_at_utc,
            "status": self.status,
            "issue_type": self.issue_type,
            "severity": self.severity,
            "confidence": self.confidence,
        }

    @classmethod
    def from_json_object(cls, document: Mapping[str, Any]) -> AttemptRecord:
        if document.get("schema_version") != ATTEMPT_RECORD_SCHEMA_VERSION:
            raise LearnerModelError("attempt record schema version differs")
        leaked = _FORBIDDEN_FIELDS.intersection(document)
        if leaked:
            raise LearnerModelError(
                "attempt record carries a forbidden field: " + ", ".join(sorted(leaked))
            )
        return cls(
            learner_id_sha256=str(document["learner_id_sha256"]),
            prompt_id=str(document["prompt_id"]),
            attempt_index=int(document["attempt_index"]),
            recorded_at_utc=str(document["recorded_at_utc"]),
            status=str(document["status"]),
            issue_type=document.get("issue_type"),
            severity=document.get("severity"),
            confidence=document.get("confidence"),
        )


@dataclass(frozen=True, slots=True)
class TrainabilityEstimate:
    """How correctable an issue type looks for this learner, and on what basis."""

    issue_type: str
    value: float | None
    state: MetricState
    observation_count: int
    basis: str

    def __post_init__(self) -> None:
        if not self.issue_type:
            raise LearnerModelError("issue_type must not be empty")
        if self.observation_count < 0:
            raise LearnerModelError("observation_count must not be negative")
        if self.state is MetricState.OBSERVED:
            if self.value is None:
                raise LearnerModelError("an observed estimate needs a value")
            if not 0.0 <= float(self.value) <= 1.0:
                raise LearnerModelError("value must be within [0, 1]")
        elif self.value is not None:
            raise LearnerModelError("a non-observed estimate must not carry a value")


@dataclass(frozen=True, slots=True)
class LearnerState:
    """Everything the pipeline may know about one learner."""

    learner_id_sha256: str
    level: str
    attempt_count: int
    recurring_issue_types: tuple[str, ...]
    trainability: tuple[TrainabilityEstimate, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _validate_hashed_id(self.learner_id_sha256)
        if self.level not in LEARNER_LEVELS:
            raise LearnerModelError(f"level must be one of {LEARNER_LEVELS}")
        if self.attempt_count < 0:
            raise LearnerModelError("attempt_count must not be negative")
        names = [item.issue_type for item in self.trainability]
        if len(set(names)) != len(names):
            raise LearnerModelError("duplicate trainability estimate")

    def trainability_for(self, issue_type: str) -> TrainabilityEstimate | None:
        for item in self.trainability:
            if item.issue_type == issue_type:
                return item
        return None


def estimate_trainability(
    *,
    issue_type: str,
    severities: Sequence[float | None],
) -> TrainabilityEstimate:
    """Read an error-reduction curve as correctability.

    ``severities`` is ordered oldest to newest; ``None`` means the issue did not
    occur on that attempt, which counts as fully corrected for that attempt.

    Below :data:`MINIMUM_OBSERVATIONS_FOR_TRAINABILITY` observations the estimate
    is ``MISSING`` rather than a guess — a two-point "curve" would let one lucky
    attempt dominate the ranking of what to coach next.
    """

    if len(severities) < MINIMUM_OBSERVATIONS_FOR_TRAINABILITY:
        return TrainabilityEstimate(
            issue_type=issue_type,
            value=None,
            state=MetricState.MISSING,
            observation_count=len(severities),
            basis="insufficient_observations",
        )
    values = [0.0 if item is None else float(item) for item in severities]
    for value in values:
        if not 0.0 <= value <= 1.0:
            raise LearnerModelError("severity must be within [0, 1]")
    midpoint = len(values) // 2
    earlier = values[:midpoint]
    later = values[len(values) - midpoint :]
    reduction = (sum(earlier) / len(earlier)) - (sum(later) / len(later))
    # A halving of severity across the window reads as fully trainable; no change
    # reads as the neutral middle; getting worse reads as low.
    scaled = 0.5 + reduction
    return TrainabilityEstimate(
        issue_type=issue_type,
        value=max(0.0, min(1.0, scaled)),
        state=MetricState.OBSERVED,
        observation_count=len(values),
        basis="mean_severity_reduction_across_attempts",
    )


def build_learner_state(
    *,
    learner_id_sha256: str,
    records: Iterable[AttemptRecord],
    level: str = "unknown",
    recurrence_threshold: int = 2,
) -> LearnerState:
    """Fold an append-only history into the state a curriculum reads."""

    history = sorted(
        records,
        key=lambda item: (item.attempt_index, item.recorded_at_utc, item.issue_type or ""),
    )
    for record in history:
        if record.learner_id_sha256 != learner_id_sha256:
            raise LearnerModelError("history mixes learners")
    attempts = {record.attempt_index for record in history}
    by_type: dict[str, list[float | None]] = {}
    for record in history:
        if record.issue_type is None:
            continue
        by_type.setdefault(record.issue_type, []).append(record.severity)
    estimates = tuple(
        estimate_trainability(issue_type=issue_type, severities=severities)
        for issue_type, severities in sorted(by_type.items())
    )
    recurring = tuple(
        issue_type
        for issue_type, severities in sorted(by_type.items())
        if len(severities) >= recurrence_threshold
    )
    return LearnerState(
        learner_id_sha256=learner_id_sha256,
        level=level,
        attempt_count=len(attempts),
        recurring_issue_types=recurring,
        trainability=estimates,
    )


def select_next_targets(
    state: LearnerState,
    *,
    maximum: int = MAXIMUM_NEXT_TARGETS,
) -> tuple[str, ...]:
    """Pick the next practice targets: recurring, and correctable when known.

    Issue types whose trainability is unknown are kept as candidates but ranked
    after the ones known to respond, so an unmeasured factor neither promotes nor
    silently excludes a target.
    """

    if maximum < 1:
        raise LearnerModelError("maximum must be at least 1")

    def sort_key(issue_type: str) -> tuple[int, float, str]:
        estimate = state.trainability_for(issue_type)
        if estimate is not None and estimate.state is MetricState.OBSERVED:
            return (0, -float(estimate.value or 0.0), issue_type)
        return (1, 0.0, issue_type)

    return tuple(sorted(state.recurring_issue_types, key=sort_key)[:maximum])


def priority_with_trainability(
    *,
    severity: float,
    confidence: float,
    comprehensibility_impact: float,
    trainability: TrainabilityEstimate | None,
    declared_prior: float,
) -> tuple[float, str]:
    """The paper's four-factor priority, with the unknown case made explicit.

    When trainability is unmeasured the caller's ``declared_prior`` is used and the
    basis says so, because a ranking computed from a prior is not the same claim
    as one computed from a learner's own curve.
    """

    for name, value in (
        ("severity", severity),
        ("confidence", confidence),
        ("comprehensibility_impact", comprehensibility_impact),
        ("declared_prior", declared_prior),
    ):
        if not 0.0 <= float(value) <= 1.0:
            raise LearnerModelError(f"{name} must be within [0, 1]")
    if trainability is not None and trainability.state is MetricState.OBSERVED:
        factor = float(trainability.value or 0.0)
        basis = trainability.basis
    else:
        factor = float(declared_prior)
        basis = "declared_prior_no_learner_history"
    return severity * confidence * comprehensibility_impact * factor, basis


class JsonlLearnerStore:
    """Append-only learner history on local disk.

    Append-only on purpose: a curriculum that can rewrite the history it is judged
    by is not auditable. Records are validated on write *and* on read, so a file
    edited by hand fails closed instead of feeding a silent bad estimate.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def append(self, record: AttemptRecord) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            record.to_json_object(), ensure_ascii=False, sort_keys=True, allow_nan=False
        )
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def read(self, learner_id_sha256: str) -> tuple[AttemptRecord, ...]:
        _validate_hashed_id(learner_id_sha256)
        if not self._path.is_file():
            return ()
        records: list[AttemptRecord] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                document = json.loads(line)
            except json.JSONDecodeError as error:
                raise LearnerModelError("learner history contains invalid JSON") from error
            record = AttemptRecord.from_json_object(document)
            if record.learner_id_sha256 == learner_id_sha256:
                records.append(record)
        return tuple(records)

    def next_attempt_index(self, learner_id_sha256: str, prompt_id: str) -> int:
        existing = [
            record.attempt_index
            for record in self.read(learner_id_sha256)
            if record.prompt_id == prompt_id
        ]
        return max(existing, default=0) + 1


class LearnerModel:
    """``LearnerModelPort`` over an append-only store, with a bounded retry rule."""

    def __init__(
        self,
        store: JsonlLearnerStore,
        *,
        maximum_attempts_per_prompt: int,
        level_resolver: Any | None = None,
    ) -> None:
        if maximum_attempts_per_prompt < 1:
            raise LearnerModelError("maximum_attempts_per_prompt must be at least 1")
        self._store = store
        self._maximum_attempts = maximum_attempts_per_prompt
        self._level_resolver = level_resolver

    @property
    def model_id(self) -> str:
        return "jsonl-learner-model-v1"

    @property
    def maximum_attempts_per_prompt(self) -> int:
        return self._maximum_attempts

    def state_for(self, learner_id_sha256: str) -> LearnerState:
        records = self._store.read(learner_id_sha256)
        level = "unknown"
        if self._level_resolver is not None:
            level = str(self._level_resolver(records))
        return build_learner_state(
            learner_id_sha256=learner_id_sha256,
            records=records,
            level=level,
        )

    def retries_remaining(self, learner_id_sha256: str, prompt_id: str) -> int:
        used = self._store.next_attempt_index(learner_id_sha256, prompt_id) - 1
        return max(0, self._maximum_attempts - used)

    def record_attempt(
        self,
        *,
        learner_id_sha256: str,
        prompt_id: str,
        status: str,
        issue_type: str | None = None,
        severity: float | None = None,
        confidence: float | None = None,
        recorded_at_utc: str | None = None,
    ) -> AttemptRecord:
        record = AttemptRecord(
            learner_id_sha256=learner_id_sha256,
            prompt_id=prompt_id,
            attempt_index=self._store.next_attempt_index(learner_id_sha256, prompt_id),
            recorded_at_utc=recorded_at_utc or datetime.now(UTC).isoformat(),
            status=status,
            issue_type=issue_type,
            severity=severity,
            confidence=confidence,
        )
        self._store.append(record)
        return record


__all__ = [
    "ATTEMPT_RECORD_SCHEMA_VERSION",
    "LEARNER_LEVELS",
    "LEARNER_STATE_SCHEMA_VERSION",
    "MAXIMUM_NEXT_TARGETS",
    "MINIMUM_OBSERVATIONS_FOR_TRAINABILITY",
    "AttemptRecord",
    "JsonlLearnerStore",
    "LearnerModel",
    "LearnerModelError",
    "LearnerState",
    "TrainabilityEstimate",
    "build_learner_state",
    "estimate_trainability",
    "priority_with_trainability",
    "select_next_targets",
]
