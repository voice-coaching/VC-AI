from __future__ import annotations

import math
from typing import Any

import pytest

from voice_coach.evaluation.e01_relation_evaluator import (
    EVALUATION_POLICY_ID,
    E01BoundaryRule,
    E01CaseWeighting,
    E01ExpectedRelation,
    E01MetamorphicFamily,
    E01RelationCase,
    E01RelationDirection,
    E01RelationEvaluationError,
    aggregate_e01_relation_cases,
    evaluate_e01_relation_case,
)


ALL_FAMILIES = tuple(E01MetamorphicFamily)


def _case(**overrides: Any) -> E01RelationCase:
    arguments: dict[str, object] = {
        "family": E01MetamorphicFamily.SILENCE,
        "relation": E01ExpectedRelation.INVARIANT,
        "direction": E01RelationDirection.NONE,
        "boundary_rule": E01BoundaryRule.INCLUSIVE,
        "tolerance_or_margin": 0.25,
        "base_metric": 1.0,
        "transformed_metric": 1.25,
    }
    arguments.update(overrides)
    return E01RelationCase(**arguments)  # type: ignore[arg-type]


def _five_family_cases() -> list[E01RelationCase]:
    return [
        _case(family=E01MetamorphicFamily.SILENCE),
        _case(
            family=E01MetamorphicFamily.GAIN,
            transformed_metric=1.5,
        ),
        _case(
            family=E01MetamorphicFamily.DURATION,
            relation=E01ExpectedRelation.MONOTONIC_NONINCREASING,
            direction=E01RelationDirection.DECREASE,
            transformed_metric=0.75,
        ),
        _case(
            family=E01MetamorphicFamily.F0,
            relation=E01ExpectedRelation.MONOTONIC_NONDECREASING,
            direction=E01RelationDirection.INCREASE,
            transformed_metric=0.75,
        ),
        _case(
            family=E01MetamorphicFamily.PHONE,
            relation=E01ExpectedRelation.TARGETED_CHANGE,
            direction=E01RelationDirection.DECREASE,
            tolerance_or_margin=0.5,
            transformed_metric=0.5,
        ),
    ]


def test_all_five_static_families_are_expressible() -> None:
    assert {family.value for family in E01MetamorphicFamily} == {
        "silence",
        "gain",
        "duration",
        "f0",
        "phone",
    }
    assert {case.family for case in _five_family_cases()} == set(ALL_FAMILIES)


@pytest.mark.parametrize(
    ("relation", "direction", "transformed", "expected"),
    [
        (
            E01ExpectedRelation.INVARIANT,
            E01RelationDirection.NONE,
            1.25,
            True,
        ),
        (
            E01ExpectedRelation.INVARIANT,
            E01RelationDirection.NONE,
            1.5,
            False,
        ),
        (
            E01ExpectedRelation.MONOTONIC_NONINCREASING,
            E01RelationDirection.DECREASE,
            1.25,
            True,
        ),
        (
            E01ExpectedRelation.MONOTONIC_NONINCREASING,
            E01RelationDirection.DECREASE,
            1.5,
            False,
        ),
        (
            E01ExpectedRelation.MONOTONIC_NONDECREASING,
            E01RelationDirection.INCREASE,
            0.75,
            True,
        ),
        (
            E01ExpectedRelation.MONOTONIC_NONDECREASING,
            E01RelationDirection.INCREASE,
            0.5,
            False,
        ),
    ],
)
def test_invariant_and_monotonic_relations_use_explicit_tolerance(
    relation: E01ExpectedRelation,
    direction: E01RelationDirection,
    transformed: float,
    expected: bool,
) -> None:
    result = evaluate_e01_relation_case(
        _case(
            relation=relation,
            direction=direction,
            transformed_metric=transformed,
        )
    )

    assert result.passed is expected


@pytest.mark.parametrize(
    ("direction", "transformed", "expected"),
    [
        (E01RelationDirection.INCREASE, 1.5, True),
        (E01RelationDirection.INCREASE, 0.5, False),
        (E01RelationDirection.DECREASE, 0.5, True),
        (E01RelationDirection.DECREASE, 1.5, False),
        (E01RelationDirection.EITHER, 1.5, True),
        (E01RelationDirection.EITHER, 0.5, True),
        (E01RelationDirection.EITHER, 1.25, False),
    ],
)
def test_targeted_change_requires_explicit_direction_and_margin(
    direction: E01RelationDirection,
    transformed: float,
    expected: bool,
) -> None:
    result = evaluate_e01_relation_case(
        _case(
            relation=E01ExpectedRelation.TARGETED_CHANGE,
            direction=direction,
            boundary_rule=E01BoundaryRule.STRICT,
            tolerance_or_margin=0.25,
            transformed_metric=transformed,
        )
    )

    assert result.passed is expected


def test_boundary_equality_is_caller_selected_without_hidden_epsilon() -> None:
    inclusive = evaluate_e01_relation_case(
        _case(boundary_rule=E01BoundaryRule.INCLUSIVE)
    )
    strict = evaluate_e01_relation_case(_case(boundary_rule=E01BoundaryRule.STRICT))

    assert inclusive.passed is True
    assert strict.passed is False


@pytest.mark.parametrize(
    ("relation", "direction"),
    [
        (E01ExpectedRelation.INVARIANT, E01RelationDirection.INCREASE),
        (
            E01ExpectedRelation.MONOTONIC_NONINCREASING,
            E01RelationDirection.INCREASE,
        ),
        (
            E01ExpectedRelation.MONOTONIC_NONDECREASING,
            E01RelationDirection.DECREASE,
        ),
        (E01ExpectedRelation.TARGETED_CHANGE, E01RelationDirection.NONE),
    ],
)
def test_relation_direction_mismatch_fails_closed(
    relation: E01ExpectedRelation,
    direction: E01RelationDirection,
) -> None:
    with pytest.raises(E01RelationEvaluationError, match="incompatible"):
        evaluate_e01_relation_case(_case(relation=relation, direction=direction))


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("family", "silence"),
        ("relation", "invariant"),
        ("direction", "none"),
        ("boundary_rule", "inclusive"),
    ],
)
def test_enums_must_be_explicit_values(field: str, invalid: object) -> None:
    with pytest.raises(E01RelationEvaluationError, match="explicit"):
        evaluate_e01_relation_case(_case(**{field: invalid}))


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("base_metric", True),
        ("base_metric", "1.0"),
        ("base_metric", float("nan")),
        ("transformed_metric", float("inf")),
        ("tolerance_or_margin", False),
        ("tolerance_or_margin", -0.1),
        ("tolerance_or_margin", float("-inf")),
    ],
)
def test_nonfinite_nonreal_or_negative_numeric_inputs_fail_closed(
    field: str,
    invalid: object,
) -> None:
    with pytest.raises(E01RelationEvaluationError):
        evaluate_e01_relation_case(_case(**{field: invalid}))


def test_finite_inputs_whose_difference_overflows_fail_closed() -> None:
    with pytest.raises(E01RelationEvaluationError, match="remain finite"):
        evaluate_e01_relation_case(_case(base_metric=-1e308, transformed_metric=1e308))


def test_zero_targeted_margin_is_only_meaningful_with_strict_boundary() -> None:
    inclusive = _case(
        relation=E01ExpectedRelation.TARGETED_CHANGE,
        direction=E01RelationDirection.EITHER,
        boundary_rule=E01BoundaryRule.INCLUSIVE,
        tolerance_or_margin=0.0,
        transformed_metric=1.0,
    )
    with pytest.raises(E01RelationEvaluationError, match="positive margin"):
        evaluate_e01_relation_case(inclusive)

    strict = _case(
        relation=E01ExpectedRelation.TARGETED_CHANGE,
        direction=E01RelationDirection.EITHER,
        boundary_rule=E01BoundaryRule.STRICT,
        tolerance_or_margin=0.0,
        transformed_metric=1.0,
    )
    assert evaluate_e01_relation_case(strict).passed is False


def test_case_result_omits_metric_values_identifiers_text_and_paths() -> None:
    result = evaluate_e01_relation_case(
        _case(base_metric=123.125, transformed_metric=123.25)
    )
    payload = result.to_dict()

    assert set(payload) == {
        "evaluation_policy_id",
        "family",
        "relation",
        "direction",
        "boundary_rule",
        "tolerance_or_margin",
        "passed",
    }
    assert result.evaluation_policy_id == EVALUATION_POLICY_ID
    assert "123.125" not in repr(payload)
    assert all(
        forbidden not in key
        for key in payload
        for forbidden in (
            "id_sha256",
            "speaker",
            "participant",
            "audio",
            "text",
            "transcript",
            "path",
            "base_metric",
            "transformed_metric",
        )
    )


def test_five_family_aggregate_is_canonical_and_order_independent() -> None:
    cases = _five_family_cases()

    first = aggregate_e01_relation_cases(
        cases,
        required_families=tuple(reversed(ALL_FAMILIES)),
        weighting_policy=E01CaseWeighting.EQUAL_PER_CASE,
    )
    second = aggregate_e01_relation_cases(
        tuple(reversed(cases)),
        required_families=ALL_FAMILIES,
        weighting_policy=E01CaseWeighting.EQUAL_PER_CASE,
    )

    assert first == second
    assert [result.family for result in first.family_results] == list(ALL_FAMILIES)
    assert first.family_count == 5
    assert first.case_count == 5
    assert first.passed_count == 4
    assert first.failed_count == 1
    assert first.pass_rate == pytest.approx(0.8)


def test_aggregate_counts_repeated_cases_with_equal_explicit_weight() -> None:
    passed = _case()
    failed = _case(transformed_metric=2.0)

    result = aggregate_e01_relation_cases(
        [passed, failed, passed],
        required_families=[E01MetamorphicFamily.SILENCE],
        weighting_policy=E01CaseWeighting.EQUAL_PER_CASE,
    )

    assert result.case_count == 3
    assert result.passed_count == 2
    assert result.failed_count == 1
    assert result.pass_rate == pytest.approx(2 / 3)
    assert result.family_results[0].pass_rate == pytest.approx(2 / 3)


@pytest.mark.parametrize(
    ("cases", "families", "weighting", "message"),
    [
        ([], [E01MetamorphicFamily.SILENCE], E01CaseWeighting.EQUAL_PER_CASE, "empty"),
        (
            [_case()],
            [],
            E01CaseWeighting.EQUAL_PER_CASE,
            "required_families",
        ),
        (
            [_case()],
            ["silence"],
            E01CaseWeighting.EQUAL_PER_CASE,
            "explicit",
        ),
        (
            [_case()],
            [
                E01MetamorphicFamily.SILENCE,
                E01MetamorphicFamily.SILENCE,
            ],
            E01CaseWeighting.EQUAL_PER_CASE,
            "duplicates",
        ),
        (
            [_case()],
            [E01MetamorphicFamily.SILENCE],
            "equal_per_case",
            "explicit",
        ),
    ],
)
def test_aggregate_policy_inputs_fail_closed(
    cases: object,
    families: object,
    weighting: object,
    message: str,
) -> None:
    with pytest.raises(E01RelationEvaluationError, match=message):
        aggregate_e01_relation_cases(
            cases,  # type: ignore[arg-type]
            required_families=families,  # type: ignore[arg-type]
            weighting_policy=weighting,  # type: ignore[arg-type]
        )


def test_aggregate_rejects_missing_or_out_of_scope_family() -> None:
    with pytest.raises(E01RelationEvaluationError, match="no cases"):
        aggregate_e01_relation_cases(
            [_case()],
            required_families=[
                E01MetamorphicFamily.SILENCE,
                E01MetamorphicFamily.GAIN,
            ],
            weighting_policy=E01CaseWeighting.EQUAL_PER_CASE,
        )

    with pytest.raises(E01RelationEvaluationError, match="outside"):
        aggregate_e01_relation_cases(
            [_case(family=E01MetamorphicFamily.GAIN)],
            required_families=[E01MetamorphicFamily.SILENCE],
            weighting_policy=E01CaseWeighting.EQUAL_PER_CASE,
        )


def test_aggregate_rejects_policy_drift_within_family() -> None:
    with pytest.raises(E01RelationEvaluationError, match="changes policy"):
        aggregate_e01_relation_cases(
            [
                _case(),
                _case(tolerance_or_margin=0.5),
            ],
            required_families=[E01MetamorphicFamily.SILENCE],
            weighting_policy=E01CaseWeighting.EQUAL_PER_CASE,
        )


def test_aggregate_invalid_case_aborts_instead_of_excluding_it() -> None:
    with pytest.raises(E01RelationEvaluationError, match=r"cases\[1\]"):
        aggregate_e01_relation_cases(
            [_case(), _case(base_metric=math.nan)],
            required_families=[E01MetamorphicFamily.SILENCE],
            weighting_policy=E01CaseWeighting.EQUAL_PER_CASE,
        )


def test_aggregate_payload_is_aggregate_only_and_has_no_decision() -> None:
    result = aggregate_e01_relation_cases(
        _five_family_cases(),
        required_families=ALL_FAMILIES,
        weighting_policy=E01CaseWeighting.EQUAL_PER_CASE,
    )
    payload = result.to_dict()

    assert set(payload) == {
        "evaluation_policy_id",
        "weighting_policy",
        "family_count",
        "case_count",
        "passed_count",
        "failed_count",
        "pass_rate",
        "family_results",
    }
    serialized_keys = repr(payload)
    for forbidden in (
        "case_id",
        "speaker",
        "participant",
        "audio",
        "transcript",
        "target_text",
        "path",
        "base_metric",
        "transformed_metric",
        "gate_statistic",
        "decision",
        "threshold",
    ):
        assert forbidden not in serialized_keys
