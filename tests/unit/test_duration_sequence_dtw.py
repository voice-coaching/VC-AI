from __future__ import annotations

from dataclasses import fields
import json
import math
import re
from types import SimpleNamespace
from typing import Any

import pytest

from voice_coach.evaluation.duration_sequence_dtw import (
    DURATION_SEQUENCE_DTW_HARD_MAX_CELLS,
    DURATION_SEQUENCE_DTW_POLICY_ID,
    DurationBandPolicy,
    DurationDtwPolicy,
    DurationLocalCost,
    DurationNormalizationPolicy,
    DurationPathPolicy,
    DurationSequence,
    DurationSequenceDtwError,
    DurationSequenceProvenance,
    DurationStepPolicy,
    DurationTieBreakPolicy,
    evaluate_duration_sequence_dtw,
)


def _reference(
    values: tuple[float, ...] = (0.1, 0.2, 0.3),
    *,
    provenance: DurationSequenceProvenance = (
        DurationSequenceProvenance.REFERENCE_AUTOMATIC
    ),
) -> DurationSequence:
    return DurationSequence(
        provenance=provenance,
        durations_seconds=values,
    )


def _predicted(
    values: tuple[float, ...] = (0.1, 0.2, 0.3),
) -> DurationSequence:
    return DurationSequence(
        provenance=DurationSequenceProvenance.PREDICTED_AUTOMATIC,
        durations_seconds=values,
    )


def _policy(**overrides: Any) -> DurationDtwPolicy:
    arguments: dict[str, object] = {
        "local_cost": DurationLocalCost.ABSOLUTE_SECONDS,
        "band": DurationBandPolicy.UNBOUNDED,
        "band_radius": None,
        "step": DurationStepPolicy.SYMMETRIC_UNIT,
        "tie_break": DurationTieBreakPolicy.DIAGONAL_REFERENCE_PREDICTED,
        "normalization": DurationNormalizationPolicy.PATH_CELL_COUNT,
        "max_cells": 100,
        "path": DurationPathPolicy.RETURN_INDEX_PAIRS,
    }
    arguments.update(overrides)
    return DurationDtwPolicy(**arguments)  # type: ignore[arg-type]


def _evaluate(
    reference: DurationSequence | None = None,
    predicted: DurationSequence | None = None,
    **policy_overrides: Any,
):
    return evaluate_duration_sequence_dtw(
        reference=_reference() if reference is None else reference,
        predicted=_predicted() if predicted is None else predicted,
        policy=_policy(**policy_overrides),
    )


def test_identical_sequences_take_the_diagonal_and_report_zero_cost() -> None:
    result = _evaluate()

    assert result.evaluation_policy_id == DURATION_SEQUENCE_DTW_POLICY_ID
    assert result.reference_provenance is DurationSequenceProvenance.REFERENCE_AUTOMATIC
    assert result.predicted_provenance is DurationSequenceProvenance.PREDICTED_AUTOMATIC
    assert result.reference_count == 3
    assert result.predicted_count == 3
    assert result.full_lattice_cell_count == 9
    assert result.admissible_cell_count == 9
    assert result.reachable_cell_count == 9
    assert result.path_cell_count == 3
    assert result.diagonal_step_count == 2
    assert result.reference_advance_step_count == 0
    assert result.predicted_advance_step_count == 0
    assert result.total_cost == 0.0
    assert result.normalization_denominator == 3
    assert result.normalized_cost == 0.0
    assert result.index_path == ((0, 0), (1, 1), (2, 2))
    assert re.fullmatch(r"[0-9a-f]{64}", result.result_digest_sha256)


def test_symmetric_warp_can_align_one_repeated_predicted_duration() -> None:
    result = _evaluate(
        _reference((1.0, 2.0)),
        _predicted((1.0, 1.0, 2.0)),
    )

    assert result.total_cost == 0.0
    assert result.index_path == ((0, 0), (0, 1), (1, 2))
    assert result.diagonal_step_count == 1
    assert result.reference_advance_step_count == 0
    assert result.predicted_advance_step_count == 1


def test_absolute_seconds_cost_and_path_normalization_are_exact() -> None:
    result = _evaluate(
        _reference((1.0, 2.0)),
        _predicted((1.5, 2.5)),
    )

    assert result.index_path == ((0, 0), (1, 1))
    assert result.total_cost == 1.0
    assert result.normalization_denominator == 2
    assert result.normalized_cost == 0.5


def test_absolute_log_ratio_is_scale_invariant_and_uses_natural_log() -> None:
    original = _evaluate(
        _reference((0.1, 0.2)),
        _predicted((0.2, 0.4)),
        local_cost=DurationLocalCost.ABSOLUTE_LOG_RATIO,
    )
    scaled = _evaluate(
        _reference((1.0, 2.0)),
        _predicted((2.0, 4.0)),
        local_cost=DurationLocalCost.ABSOLUTE_LOG_RATIO,
    )

    assert original.total_cost == pytest.approx(2.0 * math.log(2.0))
    assert original.normalized_cost == pytest.approx(math.log(2.0))
    assert scaled.total_cost == pytest.approx(original.total_cost)
    assert scaled.normalized_cost == pytest.approx(original.normalized_cost)


@pytest.mark.parametrize(
    ("normalization", "denominator"),
    [
        (DurationNormalizationPolicy.NONE, 1),
        (DurationNormalizationPolicy.PATH_CELL_COUNT, 3),
        (DurationNormalizationPolicy.REFERENCE_COUNT, 2),
        (DurationNormalizationPolicy.PREDICTED_COUNT, 3),
    ],
)
def test_normalization_denominator_is_caller_selected(
    normalization: DurationNormalizationPolicy,
    denominator: int,
) -> None:
    result = _evaluate(
        _reference((1.0, 2.0)),
        _predicted((1.0, 1.0, 2.5)),
        normalization=normalization,
    )

    assert result.total_cost == 0.5
    assert result.normalization_denominator == denominator
    assert result.normalized_cost == pytest.approx(0.5 / denominator)


def test_zero_radius_band_exposes_only_diagonal_cells() -> None:
    result = _evaluate(
        band=DurationBandPolicy.SAKOE_CHIBA_ABSOLUTE_INDEX,
        band_radius=0,
        max_cells=3,
    )

    assert result.full_lattice_cell_count == 9
    assert result.admissible_cell_count == 3
    assert result.reachable_cell_count == 3
    assert result.index_path == ((0, 0), (1, 1), (2, 2))


def test_radius_one_band_has_the_expected_admissible_cell_count() -> None:
    result = _evaluate(
        band=DurationBandPolicy.SAKOE_CHIBA_ABSOLUTE_INDEX,
        band_radius=1,
        max_cells=7,
    )

    assert result.full_lattice_cell_count == 9
    assert result.admissible_cell_count == 7


def test_max_cells_applies_to_admissible_not_full_lattice_cells() -> None:
    result = _evaluate(
        band=DurationBandPolicy.SAKOE_CHIBA_ABSOLUTE_INDEX,
        band_radius=0,
        max_cells=3,
    )

    assert result.full_lattice_cell_count > result.max_cells
    assert result.admissible_cell_count == result.max_cells


def test_band_that_cannot_connect_unequal_endpoints_fails_closed() -> None:
    with pytest.raises(DurationSequenceDtwError, match="cannot connect"):
        _evaluate(
            _reference((1.0, 2.0)),
            _predicted((1.0, 1.0, 2.0, 2.0)),
            band=DurationBandPolicy.SAKOE_CHIBA_ABSOLUTE_INDEX,
            band_radius=1,
        )


def test_diagonal_only_policy_rejects_unequal_sequence_counts() -> None:
    with pytest.raises(DurationSequenceDtwError, match="equal sequence counts"):
        _evaluate(
            _reference((1.0, 2.0)),
            _predicted((1.0, 1.0, 2.0)),
            step=DurationStepPolicy.DIAGONAL_ONLY,
        )


def test_diagonal_only_policy_uses_no_warping_steps() -> None:
    result = _evaluate(step=DurationStepPolicy.DIAGONAL_ONLY)

    assert result.index_path == ((0, 0), (1, 1), (2, 2))
    assert result.reachable_cell_count == 3
    assert result.diagonal_step_count == 2


def test_tie_break_policy_controls_the_exact_zero_cost_path() -> None:
    reference = _reference((1.0, 1.0))
    predicted = _predicted((1.0, 1.0))

    diagonal_first = _evaluate(reference, predicted)
    reference_first = _evaluate(
        reference,
        predicted,
        tie_break=DurationTieBreakPolicy.REFERENCE_DIAGONAL_PREDICTED,
    )
    predicted_first = _evaluate(
        reference,
        predicted,
        tie_break=DurationTieBreakPolicy.PREDICTED_DIAGONAL_REFERENCE,
    )

    assert diagonal_first.index_path == ((0, 0), (1, 1))
    assert reference_first.index_path == ((0, 0), (0, 1), (1, 1))
    assert predicted_first.index_path == ((0, 0), (1, 0), (1, 1))
    assert diagonal_first.total_cost == reference_first.total_cost == 0.0
    assert reference_first.total_cost == predicted_first.total_cost


def test_admissible_lattice_above_caller_max_cells_fails_before_dtw() -> None:
    with pytest.raises(DurationSequenceDtwError, match="exceeds policy.max_cells"):
        _evaluate(max_cells=8)


@pytest.mark.parametrize("invalid", [True, 0, -1, 1.5, "100"])
def test_max_cells_must_be_a_positive_integer(invalid: object) -> None:
    with pytest.raises(DurationSequenceDtwError, match="positive integer"):
        _evaluate(max_cells=invalid)


def test_max_cells_cannot_disable_the_implementation_hard_limit() -> None:
    with pytest.raises(DurationSequenceDtwError, match="hard limit"):
        _evaluate(max_cells=DURATION_SEQUENCE_DTW_HARD_MAX_CELLS + 1)


def test_unbounded_band_requires_an_explicit_none_radius() -> None:
    with pytest.raises(DurationSequenceDtwError, match="must be None"):
        _evaluate(band_radius=0)


@pytest.mark.parametrize("invalid", [None, True, -1, 1.5, "1"])
def test_sakoe_chiba_radius_must_be_a_nonnegative_integer(
    invalid: object,
) -> None:
    with pytest.raises(DurationSequenceDtwError, match="non-negative integer"):
        _evaluate(
            band=DurationBandPolicy.SAKOE_CHIBA_ABSOLUTE_INDEX,
            band_radius=invalid,
        )


@pytest.mark.parametrize(
    ("field_name", "invalid"),
    [
        ("local_cost", "absolute_seconds"),
        ("band", "unbounded"),
        ("step", "symmetric_unit"),
        ("tie_break", "diagonal_reference_predicted"),
        ("normalization", "path_cell_count"),
        ("path", "summary_only"),
    ],
)
def test_policy_fields_require_exact_enum_values(
    field_name: str,
    invalid: object,
) -> None:
    with pytest.raises(DurationSequenceDtwError, match="explicit"):
        _evaluate(**{field_name: invalid})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "invalid",
    [
        SimpleNamespace(),
        {},
        (
            DurationLocalCost.ABSOLUTE_SECONDS,
            DurationBandPolicy.UNBOUNDED,
        ),
    ],
)
def test_policy_requires_the_exact_dataclass(invalid: object) -> None:
    with pytest.raises(DurationSequenceDtwError, match="DurationDtwPolicy"):
        evaluate_duration_sequence_dtw(
            reference=_reference(),
            predicted=_predicted(),
            policy=invalid,  # type: ignore[arg-type]
        )


def test_all_inputs_and_policies_have_no_implicit_default() -> None:
    with pytest.raises(TypeError):
        evaluate_duration_sequence_dtw()  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        DurationDtwPolicy()  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "invalid",
    [
        SimpleNamespace(
            provenance=DurationSequenceProvenance.REFERENCE_AUTOMATIC,
            durations_seconds=(0.1,),
        ),
        {
            "provenance": DurationSequenceProvenance.REFERENCE_AUTOMATIC,
            "durations_seconds": (0.1,),
        },
        (
            DurationSequenceProvenance.REFERENCE_AUTOMATIC,
            (0.1,),
        ),
    ],
)
def test_sequence_requires_the_exact_dataclass(invalid: object) -> None:
    with pytest.raises(DurationSequenceDtwError, match="DurationSequence"):
        evaluate_duration_sequence_dtw(
            reference=invalid,  # type: ignore[arg-type]
            predicted=_predicted(),
            policy=_policy(),
        )


@pytest.mark.parametrize("invalid", [[], [0.1], (value for value in (0.1,))])
def test_duration_values_require_an_exact_tuple(invalid: object) -> None:
    reference = DurationSequence(
        provenance=DurationSequenceProvenance.REFERENCE_AUTOMATIC,
        durations_seconds=invalid,  # type: ignore[arg-type]
    )

    with pytest.raises(DurationSequenceDtwError, match="exact tuple"):
        evaluate_duration_sequence_dtw(
            reference=reference,
            predicted=_predicted(),
            policy=_policy(),
        )


def test_duration_sequences_must_not_be_empty() -> None:
    with pytest.raises(DurationSequenceDtwError, match="must not be empty"):
        _evaluate(_reference(()))


@pytest.mark.parametrize(
    "invalid",
    [True, False, 0.0, -0.0, -0.1, math.nan, math.inf, -math.inf, "0.1", None],
)
def test_durations_must_be_finite_positive_reals(invalid: object) -> None:
    reference = DurationSequence(
        provenance=DurationSequenceProvenance.REFERENCE_AUTOMATIC,
        durations_seconds=(invalid,),  # type: ignore[arg-type]
    )

    with pytest.raises(DurationSequenceDtwError, match="finite positive"):
        evaluate_duration_sequence_dtw(
            reference=reference,
            predicted=_predicted((0.1,)),
            policy=_policy(),
        )


def test_provenance_requires_an_exact_enum_value() -> None:
    reference = DurationSequence(
        provenance="reference_automatic",  # type: ignore[arg-type]
        durations_seconds=(0.1,),
    )

    with pytest.raises(DurationSequenceDtwError, match="explicit"):
        evaluate_duration_sequence_dtw(
            reference=reference,
            predicted=_predicted((0.1,)),
            policy=_policy(),
        )


def test_reference_and_predicted_roles_cannot_be_swapped() -> None:
    with pytest.raises(DurationSequenceDtwError, match="reference role"):
        evaluate_duration_sequence_dtw(
            reference=_predicted((0.1,)),
            predicted=_predicted((0.1,)),
            policy=_policy(),
        )

    predicted_with_reference_role = DurationSequence(
        provenance=DurationSequenceProvenance.REFERENCE_AUTOMATIC,
        durations_seconds=(0.1,),
    )
    with pytest.raises(DurationSequenceDtwError, match="predicted role"):
        evaluate_duration_sequence_dtw(
            reference=_reference((0.1,)),
            predicted=predicted_with_reference_role,
            policy=_policy(),
        )


@pytest.mark.parametrize(
    "provenance",
    [
        DurationSequenceProvenance.REFERENCE_AUTOMATIC,
        DurationSequenceProvenance.REFERENCE_HUMAN_REVIEWED,
        DurationSequenceProvenance.REFERENCE_CALLER_SUPPLIED_UNVERIFIED,
    ],
)
def test_reference_boundary_provenance_is_preserved_without_inference(
    provenance: DurationSequenceProvenance,
) -> None:
    result = _evaluate(_reference(provenance=provenance))

    assert result.reference_provenance is provenance


def test_summary_only_policy_returns_no_index_path_or_duration_values() -> None:
    result = _evaluate(path=DurationPathPolicy.SUMMARY_ONLY)
    field_names = {field.name for field in fields(result)}

    assert result.index_path is None
    assert {
        "reference_durations",
        "predicted_durations",
        "durations_seconds",
        "reference",
        "predicted",
        "speaker_id",
        "utterance_id",
        "participant_id",
        "audio",
        "audio_path",
        "transcript",
        "text",
    }.isdisjoint(field_names)
    payload = result.to_dict()
    assert payload["index_path"] is None
    assert "durations_seconds" not in json.dumps(payload)


def test_index_path_policy_returns_only_integer_index_pairs() -> None:
    result = _evaluate(path=DurationPathPolicy.RETURN_INDEX_PAIRS)

    assert result.index_path is not None
    assert all(type(index) is int for pair in result.index_path for index in pair)
    assert result.index_path[0] == (0, 0)
    assert result.index_path[-1] == (2, 2)


def test_result_is_deterministic_policy_sensitive_and_json_safe() -> None:
    first = _evaluate()
    repeated = _evaluate()
    summary_only = _evaluate(path=DurationPathPolicy.SUMMARY_ONLY)
    payload = first.to_dict()

    assert first == repeated
    assert first.result_digest_sha256 == repeated.result_digest_sha256
    assert first.result_digest_sha256 != summary_only.result_digest_sha256
    assert json.loads(json.dumps(payload, allow_nan=False)) == payload


def test_very_large_accumulated_absolute_cost_fails_closed() -> None:
    with pytest.raises(DurationSequenceDtwError, match="accumulated"):
        _evaluate(
            _reference((1.0e308, 1.0e308)),
            _predicted((1.0e-308, 1.0e-308)),
        )


def test_log_ratio_handles_extreme_finite_positive_durations() -> None:
    result = _evaluate(
        _reference((1.0e308,)),
        _predicted((1.0e-308,)),
        local_cost=DurationLocalCost.ABSOLUTE_LOG_RATIO,
    )

    assert math.isfinite(result.total_cost)
    assert result.total_cost > 1_000.0


def test_error_messages_do_not_echo_duration_values() -> None:
    reference = DurationSequence(
        provenance=DurationSequenceProvenance.REFERENCE_AUTOMATIC,
        durations_seconds=(123.456, math.nan),
    )

    with pytest.raises(DurationSequenceDtwError) as captured:
        evaluate_duration_sequence_dtw(
            reference=reference,
            predicted=_predicted((0.1, 0.2)),
            policy=_policy(),
        )

    assert "123.456" not in str(captured.value)
    assert "nan" not in str(captured.value).lower()
