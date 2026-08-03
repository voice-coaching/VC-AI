from __future__ import annotations

from dataclasses import fields
import json
import math
import re
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from voice_coach import evaluation
from voice_coach.evaluation.pitch_contour_agreement import (
    PITCH_CONTOUR_AGREEMENT_POLICY_ID,
    PitchContourAgreementError,
    PitchContourSeries,
    PitchEndpointPolicy,
    PitchInterpolationPolicy,
    PitchUnvoicedPolicy,
    evaluate_pitch_contour_agreement,
)


def test_public_evaluation_package_exports_the_agreement_contract() -> None:
    assert (
        evaluation.PITCH_CONTOUR_AGREEMENT_POLICY_ID
        == PITCH_CONTOUR_AGREEMENT_POLICY_ID
    )
    assert evaluation.PitchContourSeries is PitchContourSeries
    assert evaluation.PitchEndpointPolicy is PitchEndpointPolicy
    assert evaluation.PitchInterpolationPolicy is PitchInterpolationPolicy
    assert evaluation.PitchUnvoicedPolicy is PitchUnvoicedPolicy
    assert (
        evaluation.evaluate_pitch_contour_agreement is evaluate_pitch_contour_agreement
    )


def _series(
    f0_hz: tuple[float, ...] = (100.0, 100.0, 100.0),
    *,
    time_seconds: tuple[float, ...] = (0.0, 1.0, 2.0),
) -> PitchContourSeries:
    return PitchContourSeries(time_seconds=time_seconds, f0_hz=f0_hz)


def _evaluate(
    reference: PitchContourSeries | None = None,
    candidate: PitchContourSeries | None = None,
    common_time_seconds: tuple[float, ...] = (0.0, 1.0, 2.0),
    **overrides: Any,
):
    arguments: dict[str, object] = {
        "interpolation_policy": PitchInterpolationPolicy.LINEAR,
        "endpoint_policy": PitchEndpointPolicy.REQUIRE_SHARED_SUPPORT,
        "unvoiced_policy": PitchUnvoicedPolicy.PRESERVE_GAPS,
        "octave_tolerance_cents": 20.0,
        "max_frame_count": 100,
    }
    arguments.update(overrides)
    return evaluate_pitch_contour_agreement(
        _series() if reference is None else reference,
        _series() if candidate is None else candidate,
        common_time_seconds,
        **arguments,  # type: ignore[arg-type]
    )


def test_identical_contours_report_zero_error_and_full_voicing_agreement() -> None:
    contour = _series((100.0, 0.0, 200.0))

    result = _evaluate(contour, contour)

    assert result.evaluation_policy_id == PITCH_CONTOUR_AGREEMENT_POLICY_ID
    assert result.reference_source_frame_count == 3
    assert result.candidate_source_frame_count == 3
    assert result.requested_common_frame_count == 3
    assert result.evaluated_common_frame_count == 3
    assert result.trimmed_common_frame_count == 0
    assert result.resampled_reference_voiced_frame_count == 2
    assert result.resampled_candidate_voiced_frame_count == 2
    assert result.jointly_voiced_frame_count == 2
    assert result.reference_only_voiced_frame_count == 0
    assert result.candidate_only_voiced_frame_count == 0
    assert result.jointly_unvoiced_frame_count == 1
    assert result.voicing_agreement_rate == 1.0
    assert result.jointly_voiced_rate == pytest.approx(2.0 / 3.0)
    assert result.mean_candidate_minus_reference_hz == 0.0
    assert result.mean_absolute_hz_error == 0.0
    assert result.median_absolute_hz_error == 0.0
    assert result.root_mean_squared_hz_error == 0.0
    assert result.maximum_absolute_hz_error == 0.0
    assert result.mean_candidate_minus_reference_cents == 0.0
    assert result.mean_absolute_cents_error == 0.0
    assert result.median_absolute_cents_error == 0.0
    assert result.root_mean_squared_cents_error == 0.0
    assert result.maximum_absolute_cents_error == 0.0
    assert result.octave_error_frame_count == 0
    assert result.octave_error_rate == 0.0
    assert re.fullmatch(r"[0-9a-f]{64}", result.result_digest_sha256)


def test_known_hz_cents_and_octave_statistics_are_descriptive() -> None:
    result = _evaluate(
        _series((100.0, 100.0, 100.0)),
        _series((100.0, 200.0, 50.0)),
        octave_tolerance_cents=0.0,
    )

    assert result.mean_candidate_minus_reference_hz == pytest.approx(50.0 / 3.0)
    assert result.mean_absolute_hz_error == 50.0
    assert result.median_absolute_hz_error == 50.0
    assert result.root_mean_squared_hz_error == pytest.approx(math.sqrt(12_500.0 / 3.0))
    assert result.maximum_absolute_hz_error == 100.0
    assert result.mean_candidate_minus_reference_cents == pytest.approx(0.0)
    assert result.mean_absolute_cents_error == pytest.approx(800.0)
    assert result.median_absolute_cents_error == pytest.approx(1_200.0)
    assert result.root_mean_squared_cents_error == pytest.approx(math.sqrt(960_000.0))
    assert result.maximum_absolute_cents_error == pytest.approx(1_200.0)
    assert result.octave_error_frame_count == 2
    assert result.octave_error_rate == pytest.approx(2.0 / 3.0)


def test_octave_tolerance_is_caller_selected_without_a_scientific_default() -> None:
    candidate_hz = 100.0 * (2.0 ** (1_180.0 / 1_200.0))
    strict = _evaluate(
        _series((100.0,), time_seconds=(0.0,)),
        _series((candidate_hz,), time_seconds=(0.0,)),
        (0.0,),
        octave_tolerance_cents=19.0,
    )
    permissive = _evaluate(
        _series((100.0,), time_seconds=(0.0,)),
        _series((candidate_hz,), time_seconds=(0.0,)),
        (0.0,),
        octave_tolerance_cents=21.0,
    )

    assert strict.maximum_absolute_cents_error == pytest.approx(1_180.0)
    assert strict.octave_error_frame_count == 0
    assert permissive.octave_error_frame_count == 1


def test_voiced_unvoiced_confusion_counts_use_no_gold_accuracy_label() -> None:
    result = _evaluate(
        _series((100.0, 0.0, 200.0)),
        _series((100.0, 100.0, 0.0)),
    )

    assert result.jointly_voiced_frame_count == 1
    assert result.reference_only_voiced_frame_count == 1
    assert result.candidate_only_voiced_frame_count == 1
    assert result.jointly_unvoiced_frame_count == 0
    assert result.resampled_reference_voiced_frame_count == 2
    assert result.resampled_candidate_voiced_frame_count == 2
    assert result.voicing_agreement_rate == pytest.approx(1.0 / 3.0)


def test_linear_interpolation_uses_the_caller_supplied_common_grid() -> None:
    result = _evaluate(
        _series((100.0, 300.0), time_seconds=(0.0, 2.0)),
        _series((200.0, 200.0), time_seconds=(0.0, 2.0)),
        (0.5, 1.0, 1.5),
    )

    assert result.jointly_voiced_frame_count == 3
    assert result.mean_candidate_minus_reference_hz == pytest.approx(0.0)
    assert result.mean_absolute_hz_error == pytest.approx(100.0 / 3.0)
    assert result.median_absolute_hz_error == 50.0
    assert result.root_mean_squared_hz_error == pytest.approx(math.sqrt(5_000.0 / 3.0))


def test_preserve_gaps_and_bridge_internal_gaps_are_distinct() -> None:
    reference = _series((100.0, 0.0, 300.0))
    candidate = _series((100.0, 100.0, 100.0))
    common_grid = (0.0, 0.5, 1.0, 1.5, 2.0)

    preserved = _evaluate(reference, candidate, common_grid)
    bridged = _evaluate(
        reference,
        candidate,
        common_grid,
        unvoiced_policy=PitchUnvoicedPolicy.BRIDGE_INTERNAL_GAPS,
    )

    assert preserved.jointly_voiced_frame_count == 2
    assert preserved.candidate_only_voiced_frame_count == 3
    assert bridged.jointly_voiced_frame_count == 5
    assert bridged.candidate_only_voiced_frame_count == 0
    assert bridged.mean_candidate_minus_reference_hz == pytest.approx(-100.0)


def test_bridge_policy_does_not_extrapolate_outside_voiced_support() -> None:
    reference = _series((0.0, 100.0, 0.0))
    candidate = _series((100.0, 100.0, 100.0))

    result = _evaluate(
        reference,
        candidate,
        unvoiced_policy=PitchUnvoicedPolicy.BRIDGE_INTERNAL_GAPS,
    )

    assert result.jointly_voiced_frame_count == 1
    assert result.candidate_only_voiced_frame_count == 2


def test_nearest_tie_direction_is_part_of_the_interpolation_policy() -> None:
    reference = _series((100.0, 200.0), time_seconds=(0.0, 2.0))
    candidate = _series((100.0, 100.0), time_seconds=(0.0, 2.0))

    earlier = _evaluate(
        reference,
        candidate,
        (1.0,),
        interpolation_policy=PitchInterpolationPolicy.NEAREST_EARLIER_ON_TIE,
    )
    later = _evaluate(
        reference,
        candidate,
        (1.0,),
        interpolation_policy=PitchInterpolationPolicy.NEAREST_LATER_ON_TIE,
    )

    assert earlier.mean_candidate_minus_reference_hz == 0.0
    assert later.mean_candidate_minus_reference_hz == -100.0
    assert earlier.result_digest_sha256 != later.result_digest_sha256


def test_strict_endpoint_policy_rejects_points_outside_shared_support() -> None:
    reference = _series(
        (100.0, 100.0),
        time_seconds=(0.5, 1.5),
    )

    with pytest.raises(PitchContourAgreementError, match="outside shared"):
        _evaluate(reference, _series(), (0.0, 1.0, 2.0))


def test_trim_endpoint_policy_reports_removed_grid_points() -> None:
    reference = _series(
        (100.0, 100.0),
        time_seconds=(0.5, 1.5),
    )

    result = _evaluate(
        reference,
        _series(),
        (0.0, 1.0, 2.0),
        endpoint_policy=PitchEndpointPolicy.TRIM_TO_SHARED_SUPPORT,
    )

    assert result.requested_common_frame_count == 3
    assert result.evaluated_common_frame_count == 1
    assert result.trimmed_common_frame_count == 2
    assert result.jointly_voiced_frame_count == 1


def test_disjoint_source_support_fails_before_interpolation() -> None:
    reference = _series((100.0, 100.0), time_seconds=(0.0, 1.0))
    candidate = _series((100.0, 100.0), time_seconds=(2.0, 3.0))

    with pytest.raises(PitchContourAgreementError, match="no shared time support"):
        _evaluate(
            reference,
            candidate,
            (1.5,),
            endpoint_policy=PitchEndpointPolicy.TRIM_TO_SHARED_SUPPORT,
        )


def test_trim_policy_fails_when_no_requested_point_remains() -> None:
    reference = _series((100.0, 100.0), time_seconds=(0.5, 1.5))

    with pytest.raises(PitchContourAgreementError, match="leaves no common"):
        _evaluate(
            reference,
            _series(),
            (0.0, 2.0),
            endpoint_policy=PitchEndpointPolicy.TRIM_TO_SHARED_SUPPORT,
        )


def test_single_frame_contours_are_valid_only_on_their_exact_shared_time() -> None:
    result = _evaluate(
        _series((100.0,), time_seconds=(1.0,)),
        _series((200.0,), time_seconds=(1.0,)),
        (1.0,),
        octave_tolerance_cents=0.0,
    )

    assert result.jointly_voiced_frame_count == 1
    assert result.mean_absolute_hz_error == 100.0
    assert result.mean_absolute_cents_error == pytest.approx(1_200.0)
    assert result.octave_error_frame_count == 1


@pytest.mark.parametrize(
    ("field_name", "invalid"),
    [
        ("interpolation_policy", "linear"),
        ("endpoint_policy", "require_shared_support"),
        ("unvoiced_policy", "preserve_gaps"),
    ],
)
def test_policies_require_exact_enum_values(
    field_name: str,
    invalid: object,
) -> None:
    with pytest.raises(PitchContourAgreementError, match="explicit"):
        _evaluate(**{field_name: invalid})  # type: ignore[arg-type]


def test_all_scientific_and_operational_arguments_have_no_implicit_default() -> None:
    with pytest.raises(TypeError):
        evaluate_pitch_contour_agreement(  # type: ignore[call-arg]
            _series(),
            _series(),
            (0.0, 1.0, 2.0),
        )


@pytest.mark.parametrize("invalid", [True, 0, -1, 1.5, "100"])
def test_max_frame_count_must_be_an_explicit_positive_integer(
    invalid: object,
) -> None:
    with pytest.raises(PitchContourAgreementError, match="positive integer"):
        _evaluate(max_frame_count=invalid)


@pytest.mark.parametrize(
    ("reference", "candidate", "common_grid"),
    [
        (_series(), _series((100.0,), time_seconds=(0.0,)), (0.0,)),
        (_series((100.0,), time_seconds=(0.0,)), _series(), (0.0,)),
        (
            _series((100.0,), time_seconds=(0.0,)),
            _series((100.0,), time_seconds=(0.0,)),
            (0.0, 1.0),
        ),
    ],
)
def test_max_frame_count_applies_to_both_sources_and_requested_grid(
    reference: PitchContourSeries,
    candidate: PitchContourSeries,
    common_grid: tuple[float, ...],
) -> None:
    with pytest.raises(PitchContourAgreementError, match="exceeds"):
        _evaluate(reference, candidate, common_grid, max_frame_count=1)


@pytest.mark.parametrize(
    "invalid",
    [
        SimpleNamespace(time_seconds=(0.0,), f0_hz=(100.0,)),
        {"time_seconds": (0.0,), "f0_hz": (100.0,)},
        ((0.0,), (100.0,)),
    ],
)
def test_contours_require_the_exact_input_dataclass(invalid: object) -> None:
    with pytest.raises(PitchContourAgreementError, match="PitchContourSeries"):
        _evaluate(reference=invalid)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("time_seconds", "f0_hz", "message"),
    [
        ([], (100.0,), "exact tuple"),
        ((0.0,), [100.0], "exact tuple"),
        (np.array([0.0]), (100.0,), "exact tuple"),
        ((0.0,), np.array([100.0]), "exact tuple"),
        ((), (), "must not be empty"),
        ((0.0, 1.0), (100.0,), "frame counts differ"),
    ],
)
def test_contour_fields_are_exact_nonempty_equal_length_tuples(
    time_seconds: object,
    f0_hz: object,
    message: str,
) -> None:
    contour = PitchContourSeries(
        time_seconds=time_seconds,  # type: ignore[arg-type]
        f0_hz=f0_hz,  # type: ignore[arg-type]
    )

    with pytest.raises(PitchContourAgreementError, match=message):
        _evaluate(reference=contour)


@pytest.mark.parametrize(
    ("time_seconds", "message"),
    [
        ((-0.1, 1.0), "non-negative"),
        ((0.0, 0.0), "strictly increasing"),
        ((1.0, 0.0), "strictly increasing"),
        ((0.0, math.nan), "finite"),
        ((0.0, math.inf), "finite"),
        ((0.0, True), "finite"),
        ((0.0, "1.0"), "finite"),
    ],
)
def test_source_time_grid_must_be_finite_nonnegative_and_strict(
    time_seconds: tuple[object, ...],
    message: str,
) -> None:
    contour = PitchContourSeries(
        time_seconds=time_seconds,  # type: ignore[arg-type]
        f0_hz=(100.0,) * len(time_seconds),
    )

    with pytest.raises(PitchContourAgreementError, match=message):
        _evaluate(reference=contour)


@pytest.mark.parametrize(
    ("f0_hz", "message"),
    [
        ((-1.0, 100.0, 100.0), "use zero"),
        ((math.nan, 100.0, 100.0), "finite"),
        ((math.inf, 100.0, 100.0), "finite"),
        ((True, 100.0, 100.0), "finite"),
        (("100.0", 100.0, 100.0), "finite"),
    ],
)
def test_source_f0_must_be_finite_nonnegative_zero_is_unvoiced(
    f0_hz: tuple[object, ...],
    message: str,
) -> None:
    contour = PitchContourSeries(
        time_seconds=(0.0, 1.0, 2.0),
        f0_hz=f0_hz,  # type: ignore[arg-type]
    )

    with pytest.raises(PitchContourAgreementError, match=message):
        _evaluate(reference=contour)


@pytest.mark.parametrize(
    ("common_grid", "message"),
    [
        ([], "exact tuple"),
        ((), "must not be empty"),
        ((-0.1, 1.0), "non-negative"),
        ((0.0, 0.0), "strictly increasing"),
        ((1.0, 0.0), "strictly increasing"),
        ((0.0, math.nan), "finite"),
        ((0.0, math.inf), "finite"),
        ((0.0, True), "finite"),
        ((0.0, "1.0"), "finite"),
    ],
)
def test_common_grid_uses_the_same_strict_time_contract(
    common_grid: object,
    message: str,
) -> None:
    with pytest.raises(PitchContourAgreementError, match=message):
        _evaluate(common_time_seconds=common_grid)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "invalid",
    [True, "20", math.nan, math.inf, -0.1, 600.0],
)
def test_octave_tolerance_must_be_explicit_finite_and_unambiguous(
    invalid: object,
) -> None:
    with pytest.raises(PitchContourAgreementError, match="octave_tolerance"):
        _evaluate(octave_tolerance_cents=invalid)


@pytest.mark.parametrize(
    ("reference", "candidate"),
    [
        (_series((0.0, 0.0, 0.0)), _series()),
        (_series(), _series((0.0, 0.0, 0.0))),
        (_series((0.0, 0.0, 0.0)), _series((0.0, 0.0, 0.0))),
    ],
)
def test_no_jointly_voiced_frames_fail_closed(
    reference: PitchContourSeries,
    candidate: PitchContourSeries,
) -> None:
    with pytest.raises(PitchContourAgreementError, match="no jointly voiced"):
        _evaluate(reference, candidate)


def test_result_is_deterministic_policy_sensitive_and_json_safe() -> None:
    first = _evaluate()
    repeated = _evaluate()
    changed_tolerance = _evaluate(octave_tolerance_cents=21.0)
    payload = first.to_dict()

    assert first == repeated
    assert first.result_digest_sha256 != changed_tolerance.result_digest_sha256
    assert json.loads(json.dumps(payload, allow_nan=False)) == payload


def test_result_retains_no_contour_grid_identifier_text_path_or_audio() -> None:
    result = _evaluate(
        _series((123.456, 0.0, 200.0)),
        _series((120.0, 0.0, 220.0)),
    )
    field_names = {field.name for field in fields(result)}

    assert {
        "time_seconds",
        "common_time_seconds",
        "reference_f0_hz",
        "candidate_f0_hz",
        "reference",
        "candidate",
        "audio",
        "audio_path",
        "speaker_id",
        "utterance_id",
        "participant_id",
        "text",
        "transcript",
    }.isdisjoint(field_names)
    assert all(
        forbidden not in key
        for key in result.to_dict()
        for forbidden in (
            "speaker",
            "utterance",
            "participant",
            "audio",
            "path",
            "text",
        )
    )


def test_reference_only_controls_signed_error_direction() -> None:
    forward = _evaluate(_series((100.0,) * 3), _series((125.0,) * 3))
    reverse = _evaluate(_series((125.0,) * 3), _series((100.0,) * 3))

    assert forward.mean_candidate_minus_reference_hz == 25.0
    assert reverse.mean_candidate_minus_reference_hz == -25.0
    assert forward.mean_absolute_hz_error == reverse.mean_absolute_hz_error
    assert forward.mean_candidate_minus_reference_cents == pytest.approx(
        -reverse.mean_candidate_minus_reference_cents
    )
    assert forward.mean_absolute_cents_error == pytest.approx(
        reverse.mean_absolute_cents_error
    )


def test_scaled_statistics_handle_large_finite_frequencies_without_overflow() -> None:
    result = _evaluate(
        _series((1.0e308, 1.0e308), time_seconds=(0.0, 2.0)),
        _series((1.0e308, 1.0e308), time_seconds=(0.0, 2.0)),
        (1.0,),
    )

    assert result.mean_absolute_hz_error == 0.0
    assert result.root_mean_squared_hz_error == 0.0
