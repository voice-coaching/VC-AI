from __future__ import annotations

from collections.abc import Mapping
import math

import pytest

from voice_coach.evaluation.nine_axis_labels import (
    CANONICAL_AXIS_ORDER,
    NINE_AXIS_LABEL_POLICY_ID,
    AssessmentAxis,
    AxisScoreNormalization,
    NineAxisLabelError,
    NineAxisLabelVector,
    ObservedAxisPairs,
    build_nine_axis_label_vector,
    select_observed_axis_pairs,
)


NORMALIZATION = AxisScoreNormalization.CALLER_NORMALIZED_UNIT_INTERVAL


def _labels(**overrides: object) -> dict[AssessmentAxis, object]:
    labels: dict[AssessmentAxis, object] = {
        axis: (index + 1) / 10 for index, axis in enumerate(CANONICAL_AXIS_ORDER)
    }
    for key, value in overrides.items():
        labels[AssessmentAxis(key)] = value
    return labels


def _build(
    labels: Mapping[AssessmentAxis, object] | None = None,
    *,
    normalization_policy: object = NORMALIZATION,
) -> NineAxisLabelVector:
    return build_nine_axis_label_vector(
        _labels() if labels is None else labels,
        normalization_policy=normalization_policy,  # type: ignore[arg-type]
    )


def test_axis_set_and_order_match_the_pdf_design_principle() -> None:
    assert tuple(axis.value for axis in CANONICAL_AXIS_ORDER) == (
        "content",
        "segmental",
        "duration",
        "pause",
        "intonation",
        "energy",
        "rhythm",
        "naturalness",
        "comprehensibility",
    )


def test_partial_labels_create_a_separate_mask_and_zero_fill() -> None:
    vector = _build(
        _labels(
            duration=None,
            intonation=None,
            naturalness=None,
        )
    )

    assert vector.observed_count == 6
    assert vector.missing_axes == (
        AssessmentAxis.DURATION,
        AssessmentAxis.INTONATION,
        AssessmentAxis.NATURALNESS,
    )
    for axis in vector.missing_axes:
        index = CANONICAL_AXIS_ORDER.index(axis)
        assert vector.normalized_scores[index] == 0.0
        assert vector.observed_mask[index] is False


def test_observed_zero_is_distinct_from_missing_zero() -> None:
    vector = _build(_labels(content=0.0, segmental=None))

    assert vector.normalized_scores[:2] == (0.0, 0.0)
    assert vector.observed_mask[:2] == (True, False)
    assert vector.missing_axes == (AssessmentAxis.SEGMENTAL,)


def test_input_mapping_order_does_not_change_the_canonical_vector() -> None:
    forward = _labels()
    reverse = dict(reversed(tuple(forward.items())))

    assert _build(forward) == _build(reverse)


def test_label_payload_has_only_policy_scores_mask_and_counts() -> None:
    payload = _build(_labels(pause=None)).to_dict()

    assert set(payload) == {
        "policy_id",
        "normalization_policy",
        "axis_order",
        "normalized_scores",
        "observed_mask",
        "observed_count",
    }
    assert payload["policy_id"] == NINE_AXIS_LABEL_POLICY_ID
    assert payload["normalization_policy"] == NORMALIZATION.value
    assert payload["axis_order"] == [axis.value for axis in CANONICAL_AXIS_ORDER]
    assert all(
        forbidden not in key
        for key in payload
        for forbidden in (
            "speaker",
            "participant",
            "audio",
            "text",
            "path",
            "utterance_id",
        )
    )


def test_observed_pair_selection_preserves_canonical_order_and_omits_missing() -> None:
    labels = _build(_labels(segmental=None, energy=None))
    predictions = tuple((index + 1) / 10 for index in range(9))

    pairs = select_observed_axis_pairs(predictions, labels)

    assert pairs.axes == tuple(
        axis
        for axis in CANONICAL_AXIS_ORDER
        if axis not in {AssessmentAxis.SEGMENTAL, AssessmentAxis.ENERGY}
    )
    assert pairs.prediction_scores == tuple(
        value for index, value in enumerate(predictions) if index not in {1, 5}
    )
    assert pairs.target_scores == tuple(
        value
        for value, is_observed in zip(
            labels.normalized_scores,
            labels.observed_mask,
            strict=True,
        )
        if is_observed
    )
    assert pairs.observed_count == 7


def test_pair_payload_does_not_invent_loss_weight_threshold_or_decision() -> None:
    payload = select_observed_axis_pairs([0.5] * 9, _build()).to_dict()

    assert set(payload) == {
        "policy_id",
        "axes",
        "prediction_scores",
        "target_scores",
        "observed_count",
    }
    for forbidden in ("loss", "weight", "threshold", "passed", "decision"):
        assert forbidden not in payload


def test_normalization_policy_is_required_and_must_be_the_exact_enum() -> None:
    with pytest.raises(TypeError):
        build_nine_axis_label_vector(_labels())  # type: ignore[call-arg]

    for invalid in ("caller_normalized_unit_interval", True, None):
        with pytest.raises(NineAxisLabelError, match="explicit"):
            _build(normalization_policy=invalid)


def test_every_axis_key_is_required_and_string_keys_fail_closed() -> None:
    missing = _labels()
    missing.pop(AssessmentAxis.RHYTHM)
    with pytest.raises(NineAxisLabelError, match="every canonical axis"):
        _build(missing)

    string_keyed: dict[object, object] = {
        axis.value: value for axis, value in _labels().items()
    }
    with pytest.raises(NineAxisLabelError, match="explicit AssessmentAxis"):
        _build(string_keyed)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "invalid",
    [
        True,
        "0.5",
        math.nan,
        math.inf,
        -math.inf,
        -0.01,
        1.01,
    ],
)
def test_invalid_observed_label_scores_fail_closed(invalid: object) -> None:
    with pytest.raises(NineAxisLabelError, match=r"\[0, 1\]"):
        _build(_labels(content=invalid))


def test_all_missing_labels_fail_closed() -> None:
    with pytest.raises(NineAxisLabelError, match="at least one"):
        _build({axis: None for axis in CANONICAL_AXIS_ORDER})


@pytest.mark.parametrize(
    "predictions",
    [
        [0.5] * 8,
        [0.5] * 10,
        [0.5] * 8 + [math.nan],
        [0.5] * 8 + [math.inf],
        [0.5] * 8 + [True],
        [0.5] * 8 + ["0.5"],
        [0.5] * 8 + [-0.1],
        [0.5] * 8 + [1.1],
    ],
)
def test_invalid_prediction_vectors_fail_closed(predictions: list[object]) -> None:
    with pytest.raises(NineAxisLabelError):
        select_observed_axis_pairs(predictions, _build())


def test_non_sequence_prediction_and_nonexact_label_type_fail_closed() -> None:
    with pytest.raises(NineAxisLabelError, match="sequence"):
        select_observed_axis_pairs(0.5, _build())  # type: ignore[arg-type]
    with pytest.raises(NineAxisLabelError, match="exact NineAxisLabelVector"):
        select_observed_axis_pairs([0.5] * 9, object())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "normalized_scores": (0.5,) * 8,
            "observed_mask": (True,) * 9,
            "normalization_policy": NORMALIZATION,
        },
        {
            "normalized_scores": (0.5,) * 9,
            "observed_mask": (True,) * 8,
            "normalization_policy": NORMALIZATION,
        },
        {
            "normalized_scores": (0.5,) * 8 + (0.1,),
            "observed_mask": (True,) * 8 + (False,),
            "normalization_policy": NORMALIZATION,
        },
        {
            "normalized_scores": (0.5,) * 9,
            "observed_mask": (True,) * 8 + (1,),
            "normalization_policy": NORMALIZATION,
        },
        {
            "normalized_scores": (0.5,) * 8 + (0,),
            "observed_mask": (True,) * 9,
            "normalization_policy": NORMALIZATION,
        },
        {
            "normalized_scores": (0.5,) * 8 + (-0.0,),
            "observed_mask": (True,) * 9,
            "normalization_policy": NORMALIZATION,
        },
    ],
)
def test_direct_label_dataclass_construction_cannot_bypass_invariants(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(NineAxisLabelError):
        NineAxisLabelVector(**kwargs)  # type: ignore[arg-type]


def test_direct_pair_dataclass_construction_cannot_bypass_invariants() -> None:
    with pytest.raises(NineAxisLabelError, match="canonical-order"):
        ObservedAxisPairs(
            axes=(AssessmentAxis.PAUSE, AssessmentAxis.CONTENT),
            prediction_scores=(0.5, 0.5),
            target_scores=(0.5, 0.5),
        )
    with pytest.raises(NineAxisLabelError, match="match axes"):
        ObservedAxisPairs(
            axes=(AssessmentAxis.CONTENT,),
            prediction_scores=(0.5, 0.5),
            target_scores=(0.5,),
        )
    with pytest.raises(NineAxisLabelError, match="built-in floats"):
        ObservedAxisPairs(
            axes=(AssessmentAxis.CONTENT,),
            prediction_scores=(0,),
            target_scores=(0.5,),
        )
