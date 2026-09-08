"""Loop A: an exemplar set must widen tolerance, and an absent one must stay absent.

The paper's P6 replaces a single "correct" rendering with the minimum distance to
a set of acceptable ones.  These tests fix that reduction and the two ways it can
be corrupted: by reading an unmeasurable comparison as a good score, and by
playing an exemplar whose rights basis is unknown.
"""

from __future__ import annotations

import pytest

from voice_coach.pronunciation_feedback.domain import (
    MetricState,
    ReferenceBank,
    ReferenceExemplar,
)
from voice_coach.pronunciation_feedback.multi_reference import (
    PROSODY_AXES,
    ExemplarDistance,
    MultiReferenceError,
    UnavailableReferenceBank,
    filter_rights_cleared,
    reduce_to_minimum,
)


def _exemplar(
    exemplar_id: str,
    *,
    prompt_id: str = "R00-01",
    speed: str = "neutral",
    speaker: str = "spk_a",
    license_spdx: str = "CC0-1.0",
    synthetic: bool = True,
    disclosure: str | None = "본 음성은 AI가 생성한 합성음성입니다.",
) -> ReferenceExemplar:
    return ReferenceExemplar(
        exemplar_id=exemplar_id,
        prompt_id=prompt_id,
        generator_id="cosyvoice2",
        generator_revision="rev-1",
        audio_sha256="a" * 64,
        duration_seconds=3.4,
        sample_rate=16_000,
        speaker_axis=speaker,
        speed_axis=speed,
        license_spdx=license_spdx,
        rights_basis="synthetic_own_generation",
        synthetic=synthetic,
        disclosure=disclosure,
    )


def _bank(*exemplars: ReferenceExemplar, prompt_id: str = "R00-01") -> ReferenceBank:
    return ReferenceBank(
        prompt_id=prompt_id,
        exemplars=exemplars,
        selection_id="sel-1" if exemplars else "",
        selection_rule="pareto_then_diversity",
        diversity_axes=("speaker_axis", "speed_axis"),
    )


# -- exemplar and bank invariants ---------------------------------------------


def test_synthetic_exemplar_requires_a_disclosure() -> None:
    with pytest.raises(ValueError, match="disclosure"):
        _exemplar("e1", disclosure=None)


def test_non_synthetic_exemplar_needs_no_disclosure() -> None:
    exemplar = _exemplar("e1", synthetic=False, disclosure=None)

    assert exemplar.synthetic is False


@pytest.mark.parametrize(
    "field", ["exemplar_id", "generator_id", "license_spdx", "rights_basis"]
)
def test_exemplar_requires_provenance_fields(field: str) -> None:
    kwargs = {
        "exemplar_id": "e1",
        "prompt_id": "R00-01",
        "generator_id": "cosyvoice2",
        "generator_revision": "rev-1",
        "audio_sha256": "a" * 64,
        "duration_seconds": 3.4,
        "sample_rate": 16_000,
        "speaker_axis": "spk_a",
        "speed_axis": "neutral",
        "license_spdx": "CC0-1.0",
        "rights_basis": "synthetic_own_generation",
        "synthetic": False,
    }
    kwargs[field] = ""

    with pytest.raises(ValueError, match=field):
        ReferenceExemplar(**kwargs)  # type: ignore[arg-type]


def test_bank_rejects_an_exemplar_from_another_prompt() -> None:
    with pytest.raises(ValueError, match="belong to this prompt"):
        _bank(_exemplar("e1", prompt_id="R00-02"))


def test_bank_rejects_duplicate_exemplar_ids() -> None:
    with pytest.raises(ValueError, match="unique"):
        _bank(_exemplar("e1"), _exemplar("e1", speed="fast"))


def test_non_empty_bank_must_name_its_selection() -> None:
    with pytest.raises(ValueError, match="name its selection"):
        ReferenceBank(
            prompt_id="R00-01",
            exemplars=(_exemplar("e1"),),
            selection_id="",
            selection_rule="pareto",
        )


# -- the minimum reduction ----------------------------------------------------


def test_minimum_is_taken_over_exemplars_not_the_mean() -> None:
    bank = _bank(_exemplar("e1"), _exemplar("e2", speed="fast"))
    distances = [
        ExemplarDistance("e1", {axis: 0.9 for axis in PROSODY_AXES}),
        ExemplarDistance("e2", {axis: 0.2 for axis in PROSODY_AXES}),
    ]

    result = reduce_to_minimum(bank=bank, distances=distances)

    assert result.state is MetricState.OBSERVED
    assert result.minimum_distance == pytest.approx(0.2)
    assert result.nearest_exemplar_id == "e2"
    assert result.exemplar_count == 2


def test_adding_a_closer_exemplar_can_only_lower_the_distance() -> None:
    narrow = _bank(_exemplar("e1"))
    wide = _bank(_exemplar("e1"), _exemplar("e2", speaker="spk_b"))
    far = ExemplarDistance("e1", {axis: 0.8 for axis in PROSODY_AXES})
    near = ExemplarDistance("e2", {axis: 0.3 for axis in PROSODY_AXES})

    before = reduce_to_minimum(bank=narrow, distances=[far])
    after = reduce_to_minimum(bank=wide, distances=[far, near])

    assert after.minimum_distance is not None
    assert before.minimum_distance is not None
    assert after.minimum_distance <= before.minimum_distance


def test_per_axis_minimum_may_come_from_different_exemplars() -> None:
    bank = _bank(_exemplar("e1"), _exemplar("e2", speed="fast"))
    distances = [
        ExemplarDistance("e1", {"f0": 0.1, "duration": 0.9, "energy": 0.5, "rhythm": 0.5}),
        ExemplarDistance("e2", {"f0": 0.7, "duration": 0.2, "energy": 0.5, "rhythm": 0.5}),
    ]

    result = reduce_to_minimum(bank=bank, distances=distances)

    by_axis = {item.axis: item for item in result.axes}
    assert by_axis["f0"].nearest_exemplar_id == "e1"
    assert by_axis["duration"].nearest_exemplar_id == "e2"


def test_empty_bank_yields_missing_without_a_number() -> None:
    result = reduce_to_minimum(bank=_bank(), distances=[])

    assert result.state is MetricState.MISSING
    assert result.minimum_distance is None
    assert result.nearest_exemplar_id is None
    assert all(item.state is MetricState.MISSING for item in result.axes)


def test_bank_with_no_measurable_axis_yields_missing() -> None:
    bank = _bank(_exemplar("e1"))
    distances = [ExemplarDistance("e1", {axis: None for axis in PROSODY_AXES})]

    result = reduce_to_minimum(bank=bank, distances=distances)

    assert result.state is MetricState.MISSING
    assert result.minimum_distance is None


def test_an_unmeasured_axis_stays_missing_while_others_are_observed() -> None:
    bank = _bank(_exemplar("e1"))
    distances = [
        ExemplarDistance("e1", {"f0": None, "duration": 0.4, "energy": 0.4, "rhythm": 0.4})
    ]

    result = reduce_to_minimum(bank=bank, distances=distances)

    by_axis = {item.axis: item for item in result.axes}
    assert by_axis["f0"].state is MetricState.MISSING
    assert by_axis["f0"].distance is None
    assert by_axis["duration"].state is MetricState.OBSERVED
    assert result.state is MetricState.OBSERVED


def test_overall_ignores_unmeasured_axes_rather_than_scoring_them_zero() -> None:
    measured = ExemplarDistance("e1", {"f0": None, "duration": 0.4})

    assert measured.overall() == pytest.approx(0.4)


def test_distance_outside_the_bank_fails_closed() -> None:
    bank = _bank(_exemplar("e1"))

    with pytest.raises(MultiReferenceError, match="outside the bank"):
        reduce_to_minimum(
            bank=bank,
            distances=[ExemplarDistance("e9", {"f0": 0.1})],
        )


def test_duplicate_distance_entry_fails_closed() -> None:
    bank = _bank(_exemplar("e1"))

    with pytest.raises(MultiReferenceError, match="duplicate"):
        reduce_to_minimum(
            bank=bank,
            distances=[
                ExemplarDistance("e1", {"f0": 0.1}),
                ExemplarDistance("e1", {"f0": 0.2}),
            ],
        )


@pytest.mark.parametrize(
    ("axes", "message"),
    [
        ({"pitch": 0.1}, "unknown prosody axis"),
        ({"f0": -0.1}, "must not be negative"),
        ({"f0": float("nan")}, "NaN"),
        ({"f0": True}, "must be a number"),
    ],
)
def test_malformed_axis_values_fail_closed(axes: dict, message: str) -> None:
    with pytest.raises(MultiReferenceError, match=message):
        ExemplarDistance("e1", axes)


def test_ties_resolve_deterministically_by_exemplar_id() -> None:
    bank = _bank(_exemplar("e2"), _exemplar("e1", speed="fast"))
    distances = [
        ExemplarDistance("e2", {axis: 0.5 for axis in PROSODY_AXES}),
        ExemplarDistance("e1", {axis: 0.5 for axis in PROSODY_AXES}),
    ]

    first = reduce_to_minimum(bank=bank, distances=distances)
    second = reduce_to_minimum(bank=bank, distances=list(reversed(distances)))

    assert first.nearest_exemplar_id == second.nearest_exemplar_id == "e1"


# -- rights gating ------------------------------------------------------------


def test_unavailable_bank_returns_an_empty_bank_not_an_error() -> None:
    port = UnavailableReferenceBank()

    bank = port.bank_for(prompt_id="R00-01")

    assert bank.is_empty
    assert bank.prompt_id == "R00-01"
    assert bank.selection_rule == "no_rights_cleared_exemplar_available"


def test_only_allowed_licences_survive_filtering() -> None:
    exemplars = (
        _exemplar("e1", license_spdx="CC0-1.0"),
        _exemplar("e2", license_spdx="CC-BY-NC-4.0", speed="fast"),
    )

    kept = filter_rights_cleared(exemplars, allowed_licenses=["CC0-1.0"])

    assert [item.exemplar_id for item in kept] == ["e1"]


def test_an_empty_allowlist_keeps_nothing() -> None:
    exemplars = (_exemplar("e1"),)

    assert filter_rights_cleared(exemplars, allowed_licenses=[]) == ()
    assert filter_rights_cleared(exemplars, allowed_licenses=[""]) == ()
