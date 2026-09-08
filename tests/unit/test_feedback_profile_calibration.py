"""What ``engineering_provisional`` is actually claiming.

The claim is not that the thresholds are good.  It is that they were produced by
a rule fixed before the split was read, and that the rule refuses rather than
degrades when the split cannot support it.  Those two properties are what these
tests pin, because a calibration artifact whose rule quietly relaxed under a bad
split would still look exactly like a calibrated one from the gate's side.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "calibrate_profile",
    ROOT / "scripts" / "calibrate_pronunciation_feedback_profile.py",
)
calibrate = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules[_spec.name] = calibrate
_spec.loader.exec_module(calibrate)


def _split(
    *,
    matched_content: list[float] | None = None,
    mismatched_content: list[float] | None = None,
    branches: list[set[str]] | None = None,
) -> dict:
    matched = {
        "content": matched_content or [0.85] * 20,
        "alignment": [0.95] * 20,
        "s1": [0.9] * 20,
        "s2": [0.92] * 20,
    }
    mismatched = {"content": mismatched_content or [0.3] * 20, "s2": []}
    states = branches or [{"segmental", "fluency", "rhythm"}] * 20
    return {"matched": matched, "mismatched": mismatched, "branch_states": states}


# -- the quantile itself ------------------------------------------------------


def test_the_quantile_returns_a_value_that_was_actually_observed() -> None:
    """Interpolating would invent a threshold no recording ever produced."""

    values = [0.1, 0.4, 0.9]

    assert calibrate._quantile(values, 0.05) in values
    assert calibrate._quantile(values, 0.5) in values
    assert calibrate._quantile(values, 1.0) in values


def test_the_lowest_and_highest_quantiles_reach_the_ends() -> None:
    values = [0.2, 0.5, 0.7, 0.9]

    assert calibrate._quantile(values, 0.01) == 0.2
    assert calibrate._quantile(values, 1.0) == 0.9


def test_a_quantile_of_nothing_is_refused() -> None:
    with pytest.raises(calibrate.CalibrationError):
        calibrate._quantile([], 0.5)


def test_flooring_never_raises_a_threshold_above_what_was_measured() -> None:
    """Rounding up would reject recordings the split says are normal."""

    assert calibrate._floor2(0.8599) == 0.85
    assert calibrate._floor2(0.85) == 0.85


# -- the rule -----------------------------------------------------------------


def test_thresholds_sit_at_the_fifth_percentile_of_the_matched_split() -> None:
    split = _split(matched_content=[0.5] + [0.9] * 19)

    frozen = calibrate.freeze_thresholds(**split)

    assert frozen["min_content_score"] == 0.5


def test_the_issue_thresholds_come_from_one_shared_margin() -> None:
    """Severity and confidence are two readings of the same log-odds margin.
    Frozen independently they could drift into contradicting each other."""

    import math

    frozen = calibrate.freeze_thresholds(**_split())
    margin = math.log(calibrate.ODDS_TO_COACH)

    assert frozen["min_issue_confidence"] == pytest.approx(
        calibrate._floor2(1 / (1 + math.exp(-margin)))
    )
    assert frozen["min_issue_severity"] == pytest.approx(
        calibrate._floor2(1 - math.exp(-margin))
    )


def test_only_branches_observed_on_every_item_may_be_required() -> None:
    """Requiring a branch that was ever missing would fail closed on recordings
    the calibration split itself calls normal."""

    split = _split(
        branches=[{"segmental", "fluency", "rhythm"}] * 19 + [{"segmental", "fluency"}]
    )

    frozen = calibrate.freeze_thresholds(**split)

    assert frozen["required_branches"] == ["fluency", "segmental"]


# -- when the split cannot support the rule -----------------------------------


def test_an_inseparable_split_is_refused_rather_than_frozen() -> None:
    """The refusal is the point: a content threshold that does not sit above the
    mismatched condition cannot do the one job it has."""

    split = _split(matched_content=[0.4] * 20, mismatched_content=[0.6] * 20)

    with pytest.raises(calibrate.CalibrationError, match="does not separate"):
        calibrate.freeze_thresholds(**split)


def test_an_empty_matched_split_is_refused() -> None:
    split = _split()
    split["matched"]["content"] = []

    with pytest.raises(calibrate.CalibrationError, match="no calibration item"):
        calibrate.freeze_thresholds(**split)


def test_a_split_the_auditor_never_scored_cannot_freeze_an_s2_threshold() -> None:
    """Freezing ``min_s2_confidence`` from no observation would let the gate
    check an S2 confidence against a number nothing produced."""

    split = _split()
    split["matched"]["s2"] = []

    with pytest.raises(calibrate.CalibrationError, match="auditor scored no item"):
        calibrate.freeze_thresholds(**split)


def test_no_branch_common_to_every_item_is_refused() -> None:
    split = _split(branches=[{"segmental"}, {"fluency"}])

    with pytest.raises(calibrate.CalibrationError, match="nothing can be required"):
        calibrate.freeze_thresholds(**split)


# -- what the artifact must keep saying about itself --------------------------


def test_the_split_is_declared_as_synthesised_speech() -> None:
    """The cohort reaches the learner through every feedback notice.  If it ever
    stops naming the synthesised split, the notice starts overstating what the
    thresholds were frozen on."""

    parser_default = calibrate.__doc__ or ""

    assert "synthesised read speech" in parser_default
    assert "no learner recordings" in parser_default
