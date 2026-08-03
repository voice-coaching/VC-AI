"""Pure numeric relation evaluation for future E01 metamorphic cases.

The module evaluates only caller-supplied base/transformed metric pairs under
an explicit relation, direction, boundary rule, and tolerance or margin.  It
does not choose a metric, perturbation magnitude, threshold, aggregation
scope, missing/invalid policy, or publication decision; read audio or files;
run a model; or retain identifiers, text, paths, or metric values in results.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
import math
from numbers import Real


EVALUATION_POLICY_ID = "voice_coach.e01_numeric_relation.v1"


class E01RelationEvaluationError(ValueError):
    """Raised when a relation case or aggregate policy is not explicit and safe."""


class E01MetamorphicFamily(str, Enum):
    """The five E01 perturbation families fixed by the static contract."""

    SILENCE = "silence"
    GAIN = "gain"
    DURATION = "duration"
    F0 = "f0"
    PHONE = "phone"


class E01ExpectedRelation(str, Enum):
    """Relation types allowed by the E01 preregistration contract."""

    INVARIANT = "invariant"
    MONOTONIC_NONINCREASING = "monotonic_nonincreasing"
    MONOTONIC_NONDECREASING = "monotonic_nondecreasing"
    TARGETED_CHANGE = "targeted_change"


class E01RelationDirection(str, Enum):
    """Caller-frozen direction for a numeric relation."""

    NONE = "none"
    INCREASE = "increase"
    DECREASE = "decrease"
    EITHER = "either"


class E01BoundaryRule(str, Enum):
    """Whether equality at the explicit tolerance or margin passes."""

    INCLUSIVE = "inclusive"
    STRICT = "strict"


class E01CaseWeighting(str, Enum):
    """Supported, caller-selected mechanical case weighting."""

    EQUAL_PER_CASE = "equal_per_case"


_FAMILY_ORDER = tuple(E01MetamorphicFamily)
_DIRECTIONS_BY_RELATION = {
    E01ExpectedRelation.INVARIANT: frozenset({E01RelationDirection.NONE}),
    E01ExpectedRelation.MONOTONIC_NONINCREASING: frozenset(
        {E01RelationDirection.DECREASE}
    ),
    E01ExpectedRelation.MONOTONIC_NONDECREASING: frozenset(
        {E01RelationDirection.INCREASE}
    ),
    E01ExpectedRelation.TARGETED_CHANGE: frozenset(
        {
            E01RelationDirection.INCREASE,
            E01RelationDirection.DECREASE,
            E01RelationDirection.EITHER,
        }
    ),
}


@dataclass(frozen=True, slots=True)
class E01RelationCase:
    """One in-memory numeric relation case with no identifier or metric name."""

    family: E01MetamorphicFamily
    relation: E01ExpectedRelation
    direction: E01RelationDirection
    boundary_rule: E01BoundaryRule
    tolerance_or_margin: float
    base_metric: float
    transformed_metric: float


@dataclass(frozen=True, slots=True)
class E01RelationCaseResult:
    """Privacy-minimal case result that omits both supplied metric values."""

    evaluation_policy_id: str
    family: E01MetamorphicFamily
    relation: E01ExpectedRelation
    direction: E01RelationDirection
    boundary_rule: E01BoundaryRule
    tolerance_or_margin: float
    passed: bool

    def to_dict(self) -> dict[str, object]:
        """Return only policy fields and the relation outcome."""

        return {
            "evaluation_policy_id": self.evaluation_policy_id,
            "family": self.family.value,
            "relation": self.relation.value,
            "direction": self.direction.value,
            "boundary_rule": self.boundary_rule.value,
            "tolerance_or_margin": self.tolerance_or_margin,
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class E01FamilyRelationAggregate:
    """Deterministic count summary for one family and one frozen policy."""

    family: E01MetamorphicFamily
    relation: E01ExpectedRelation
    direction: E01RelationDirection
    boundary_rule: E01BoundaryRule
    tolerance_or_margin: float
    case_count: int
    passed_count: int
    failed_count: int
    pass_rate: float

    def to_dict(self) -> dict[str, object]:
        """Return an aggregate-only family record."""

        return {
            "family": self.family.value,
            "relation": self.relation.value,
            "direction": self.direction.value,
            "boundary_rule": self.boundary_rule.value,
            "tolerance_or_margin": self.tolerance_or_margin,
            "case_count": self.case_count,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "pass_rate": self.pass_rate,
        }


@dataclass(frozen=True, slots=True)
class E01RelationAggregate:
    """Aggregate counts only; no E01 gate statistic or decision is produced."""

    evaluation_policy_id: str
    weighting_policy: E01CaseWeighting
    family_count: int
    case_count: int
    passed_count: int
    failed_count: int
    pass_rate: float
    family_results: tuple[E01FamilyRelationAggregate, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic aggregate without any case-level payload."""

        return {
            "evaluation_policy_id": self.evaluation_policy_id,
            "weighting_policy": self.weighting_policy.value,
            "family_count": self.family_count,
            "case_count": self.case_count,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "pass_rate": self.pass_rate,
            "family_results": [result.to_dict() for result in self.family_results],
        }


def _explicit_enum(value: object, enum_type: type[Enum], field_name: str) -> None:
    if not isinstance(value, enum_type):
        raise E01RelationEvaluationError(
            f"{field_name} must be an explicit {enum_type.__name__} value"
        )


def _finite_real(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise E01RelationEvaluationError(f"{field_name} must be a finite real number")
    try:
        converted = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise E01RelationEvaluationError(
            f"{field_name} must be a finite real number"
        ) from exc
    if not math.isfinite(converted):
        raise E01RelationEvaluationError(f"{field_name} must be a finite real number")
    return 0.0 if converted == 0.0 else converted


def _validate_case(
    case: E01RelationCase,
) -> tuple[float, float, float]:
    if type(case) is not E01RelationCase:
        raise E01RelationEvaluationError("case must be an exact E01RelationCase value")
    _explicit_enum(case.family, E01MetamorphicFamily, "family")
    _explicit_enum(case.relation, E01ExpectedRelation, "relation")
    _explicit_enum(case.direction, E01RelationDirection, "direction")
    _explicit_enum(case.boundary_rule, E01BoundaryRule, "boundary_rule")

    allowed_directions = _DIRECTIONS_BY_RELATION[case.relation]
    if case.direction not in allowed_directions:
        raise E01RelationEvaluationError(
            "direction is incompatible with the explicit relation"
        )

    tolerance_or_margin = _finite_real(
        case.tolerance_or_margin,
        "tolerance_or_margin",
    )
    if tolerance_or_margin < 0.0:
        raise E01RelationEvaluationError("tolerance_or_margin must be non-negative")
    if (
        case.relation is E01ExpectedRelation.TARGETED_CHANGE
        and case.boundary_rule is E01BoundaryRule.INCLUSIVE
        and tolerance_or_margin == 0.0
    ):
        raise E01RelationEvaluationError(
            "inclusive targeted_change requires a positive margin"
        )

    base_metric = _finite_real(case.base_metric, "base_metric")
    transformed_metric = _finite_real(
        case.transformed_metric,
        "transformed_metric",
    )
    difference = transformed_metric - base_metric
    if not math.isfinite(difference):
        raise E01RelationEvaluationError(
            "transformed_metric minus base_metric must remain finite"
        )
    return tolerance_or_margin, difference, abs(difference)


def _upper_boundary(
    observed: float,
    limit: float,
    rule: E01BoundaryRule,
) -> bool:
    if rule is E01BoundaryRule.INCLUSIVE:
        return observed <= limit
    return observed < limit


def _lower_boundary(
    observed: float,
    limit: float,
    rule: E01BoundaryRule,
) -> bool:
    if rule is E01BoundaryRule.INCLUSIVE:
        return observed >= limit
    return observed > limit


def evaluate_e01_relation_case(
    case: E01RelationCase,
) -> E01RelationCaseResult:
    """Evaluate one explicitly parameterized metric relation.

    ``tolerance_or_margin`` is a maximum absolute deviation for invariance, a
    maximum opposite-direction drift for monotonic relations, and a minimum
    change magnitude for targeted change.  No implicit epsilon is added.
    """

    tolerance_or_margin, difference, absolute_difference = _validate_case(case)

    if case.relation is E01ExpectedRelation.INVARIANT:
        passed = _upper_boundary(
            absolute_difference,
            tolerance_or_margin,
            case.boundary_rule,
        )
    elif case.relation is E01ExpectedRelation.MONOTONIC_NONINCREASING:
        passed = _upper_boundary(
            difference,
            tolerance_or_margin,
            case.boundary_rule,
        )
    elif case.relation is E01ExpectedRelation.MONOTONIC_NONDECREASING:
        passed = _lower_boundary(
            difference,
            -tolerance_or_margin,
            case.boundary_rule,
        )
    elif case.direction is E01RelationDirection.INCREASE:
        passed = _lower_boundary(
            difference,
            tolerance_or_margin,
            case.boundary_rule,
        )
    elif case.direction is E01RelationDirection.DECREASE:
        passed = _lower_boundary(
            -difference,
            tolerance_or_margin,
            case.boundary_rule,
        )
    else:
        passed = _lower_boundary(
            absolute_difference,
            tolerance_or_margin,
            case.boundary_rule,
        )

    return E01RelationCaseResult(
        evaluation_policy_id=EVALUATION_POLICY_ID,
        family=case.family,
        relation=case.relation,
        direction=case.direction,
        boundary_rule=case.boundary_rule,
        tolerance_or_margin=tolerance_or_margin,
        passed=passed,
    )


def _required_family_set(
    required_families: Sequence[E01MetamorphicFamily],
) -> frozenset[E01MetamorphicFamily]:
    if isinstance(required_families, (str, bytes, bytearray)) or not isinstance(
        required_families, Sequence
    ):
        raise E01RelationEvaluationError(
            "required_families must be an explicit sequence"
        )
    if not required_families:
        raise E01RelationEvaluationError("required_families must not be empty")
    validated: list[E01MetamorphicFamily] = []
    for index, family in enumerate(required_families):
        _explicit_enum(
            family,
            E01MetamorphicFamily,
            f"required_families[{index}]",
        )
        validated.append(family)
    if len(set(validated)) != len(validated):
        raise E01RelationEvaluationError("required_families contains duplicates")
    return frozenset(validated)


def _same_family_policy(
    left: E01RelationCaseResult,
    right: E01RelationCaseResult,
) -> bool:
    return (
        left.relation is right.relation
        and left.direction is right.direction
        and left.boundary_rule is right.boundary_rule
        and left.tolerance_or_margin == right.tolerance_or_margin
    )


def aggregate_e01_relation_cases(
    cases: Sequence[E01RelationCase],
    *,
    required_families: Sequence[E01MetamorphicFamily],
    weighting_policy: E01CaseWeighting,
) -> E01RelationAggregate:
    """Evaluate and aggregate explicit cases without making an E01 decision.

    Invalid cases fail the whole call.  No missing/invalid exclusion, confidence
    interval, family weighting, gate threshold, or pass/fail decision is
    inferred.  Every case has equal weight only when the caller explicitly
    selects ``EQUAL_PER_CASE``.
    """

    if not isinstance(weighting_policy, E01CaseWeighting):
        raise E01RelationEvaluationError(
            "weighting_policy must be an explicit E01CaseWeighting value"
        )
    if weighting_policy is not E01CaseWeighting.EQUAL_PER_CASE:
        raise E01RelationEvaluationError("weighting_policy is not supported")
    required = _required_family_set(required_families)
    if isinstance(cases, (str, bytes, bytearray)) or not isinstance(cases, Sequence):
        raise E01RelationEvaluationError("cases must be an explicit sequence")
    if not cases:
        raise E01RelationEvaluationError("cases must not be empty")

    grouped: dict[E01MetamorphicFamily, list[E01RelationCaseResult]] = {
        family: [] for family in required
    }
    for index, case in enumerate(cases):
        try:
            result = evaluate_e01_relation_case(case)
        except E01RelationEvaluationError as exc:
            raise E01RelationEvaluationError(
                f"cases[{index}] violates the relation contract: {exc}"
            ) from exc
        if result.family not in required:
            raise E01RelationEvaluationError(
                f"cases[{index}] family is outside required_families"
            )
        family_results = grouped[result.family]
        if family_results and not _same_family_policy(family_results[0], result):
            raise E01RelationEvaluationError(
                f"cases[{index}] changes policy within family {result.family.value}"
            )
        family_results.append(result)

    missing = [
        family.value
        for family in _FAMILY_ORDER
        if family in required and not grouped[family]
    ]
    if missing:
        raise E01RelationEvaluationError(f"required_families have no cases: {missing}")

    family_aggregates: list[E01FamilyRelationAggregate] = []
    for family in _FAMILY_ORDER:
        if family not in required:
            continue
        results = grouped[family]
        policy = results[0]
        passed_count = sum(result.passed for result in results)
        case_count = len(results)
        family_aggregates.append(
            E01FamilyRelationAggregate(
                family=family,
                relation=policy.relation,
                direction=policy.direction,
                boundary_rule=policy.boundary_rule,
                tolerance_or_margin=policy.tolerance_or_margin,
                case_count=case_count,
                passed_count=passed_count,
                failed_count=case_count - passed_count,
                pass_rate=passed_count / case_count,
            )
        )

    case_count = sum(result.case_count for result in family_aggregates)
    passed_count = sum(result.passed_count for result in family_aggregates)
    return E01RelationAggregate(
        evaluation_policy_id=EVALUATION_POLICY_ID,
        weighting_policy=weighting_policy,
        family_count=len(family_aggregates),
        case_count=case_count,
        passed_count=passed_count,
        failed_count=case_count - passed_count,
        pass_rate=passed_count / case_count,
        family_results=tuple(family_aggregates),
    )


__all__ = [
    "EVALUATION_POLICY_ID",
    "E01BoundaryRule",
    "E01CaseWeighting",
    "E01ExpectedRelation",
    "E01FamilyRelationAggregate",
    "E01MetamorphicFamily",
    "E01RelationAggregate",
    "E01RelationCase",
    "E01RelationCaseResult",
    "E01RelationDirection",
    "E01RelationEvaluationError",
    "aggregate_e01_relation_cases",
    "evaluate_e01_relation_case",
]
