"""The zero-target-phone exclusion must be a contract term, not a silent skip.

Two guarded train rows carry phone alignments but no target phone interval and no
word interval, so the hierarchy has nothing to aggregate at any level.  S104
trained through such rows and the shared fit scan counts them, so S105 cannot
simply crash on them — but it also must not drop rows quietly.  These tests fix
the declared-policy behaviour: the exact count is pinned in the config, the
validator returns an empty target set instead of raising, and the stream refuses
to run under any policy other than the declared one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from voice_coach.training.wavlm_hierarchy_contract import (
    ZERO_TARGET_PHONE_POLICY,
    S105ContractError,
    validate_config,
)
from voice_coach.training.wavlm_hierarchy_execution import (
    _clean_phone_targets,
    iter_hierarchy_examples,
)
from voice_coach.training.wavlm_hierarchy_training import S105TrainingError

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = (
    ROOT / "configs/experiments/wavlm_s105_hierarchy_v5_stream_cardinality.yaml"
)


def _config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def test_repository_config_declares_the_policy_and_the_exact_counts() -> None:
    mapping = _config()["data"]["interval_mapping"]

    assert mapping["zero_target_phone_utterance_policy"] == ZERO_TARGET_PHONE_POLICY
    assert mapping["expected_zero_target_phone_exclusions"] == {
        "fit": 1,
        "inner_validation": 1,
        "dev": 0,
    }
    validate_config(_config())


def test_missing_policy_fails_the_contract() -> None:
    config = _config()
    del config["data"]["interval_mapping"]["zero_target_phone_utterance_policy"]

    with pytest.raises(S105ContractError, match="zero_target_phone_utterance_policy"):
        validate_config(config)


def test_undeclared_policy_value_fails_the_contract() -> None:
    config = _config()
    config["data"]["interval_mapping"]["zero_target_phone_utterance_policy"] = (
        "silently_skip"
    )

    with pytest.raises(S105ContractError, match="zero_target_phone_utterance_policy"):
        validate_config(config)


def test_missing_exclusion_counts_fail_the_contract() -> None:
    config = _config()
    del config["data"]["interval_mapping"]["expected_zero_target_phone_exclusions"]

    with pytest.raises(S105ContractError, match="expected_zero_target_phone"):
        validate_config(config)


@pytest.mark.parametrize(
    "exclusions",
    [
        {"fit": 1},
        {"fit": 1, "inner_validation": 1},
        {"fit": 1, "dev": 0},
        {"fit": 1, "inner_validation": 1, "dev": 0, "calibration": 0},
    ],
)
def test_wrong_exclusion_subsets_fail_the_contract(exclusions: dict) -> None:
    config = _config()
    config["data"]["interval_mapping"][
        "expected_zero_target_phone_exclusions"
    ] = exclusions

    with pytest.raises(S105ContractError, match="exclusion subsets differ"):
        validate_config(config)


@pytest.mark.parametrize("count", [-1, 1.5, True, "1", None])
def test_non_integer_exclusion_counts_fail_the_contract(count: object) -> None:
    config = _config()
    config["data"]["interval_mapping"]["expected_zero_target_phone_exclusions"][
        "fit"
    ] = count

    with pytest.raises(S105ContractError, match="exclusion count is invalid"):
        validate_config(config)


def test_clean_phone_targets_returns_empty_instead_of_raising() -> None:
    """The validator reports the condition; the caller applies the policy."""

    phones, binary = _clean_phone_targets([], [])

    assert phones == ()
    assert binary == ()


def test_alignments_without_a_target_label_leave_no_phone_target() -> None:
    """The degenerate manifest rows look exactly like this: alignments, no targets."""

    phones, binary = _clean_phone_targets(
        [],
        [{"target_label": None, "operation": "insertion"} for _ in range(21)],
    )

    assert phones == ()
    assert binary == ()


def test_stream_refuses_an_undeclared_policy_before_touching_the_manifest(
    tmp_path: Path,
) -> None:
    stream = iter_hierarchy_examples(
        manifest_path=tmp_path / "absent.parquet",
        dataset_root=tmp_path,
        labels={},
        reserved_prompts=set(),
        target_vocabulary={"<UNK>": 0},
        subset="fit",
        modulus=10,
        validation_buckets={0, 1},
        allowed_l1={"Chinese"},
        allowed_gender={"F"},
        zero_target_phone_policy="silently_skip",
    )

    with pytest.raises(S105TrainingError, match="not the declared policy"):
        next(iter(stream))


def test_declared_policy_is_accepted_by_the_stream_constructor(
    tmp_path: Path,
) -> None:
    """The policy check must precede manifest access, so a missing file surfaces."""

    stream = iter_hierarchy_examples(
        manifest_path=tmp_path / "absent.parquet",
        dataset_root=tmp_path,
        labels={},
        reserved_prompts=set(),
        target_vocabulary={"<UNK>": 0},
        subset="fit",
        modulus=10,
        validation_buckets={0, 1},
        allowed_l1={"Chinese"},
        allowed_gender={"F"},
        zero_target_phone_policy=ZERO_TARGET_PHONE_POLICY,
    )

    with pytest.raises(Exception) as caught:
        next(iter(stream))
    assert "not the declared policy" not in str(caught.value)


def test_every_streamed_subset_declares_a_count() -> None:
    """fit, inner_validation and dev are all iterated, so all three must be declared.

    v4 failed 99% of the way through epoch 1 because the fit cardinality check
    compared the streamed count against the *scanned* count, which still included
    the excluded row. The declaration now covers every stream so the arithmetic
    has one source.
    """

    exclusions = _config()["data"]["interval_mapping"][
        "expected_zero_target_phone_exclusions"
    ]

    assert set(exclusions) == {"fit", "inner_validation", "dev"}


def test_measured_stream_totals_match_the_declared_corrections() -> None:
    """Guarded totals minus declared exclusions are what each pass must observe."""

    config = _config()
    exclusions = config["data"]["interval_mapping"][
        "expected_zero_target_phone_exclusions"
    ]
    # Measured from the manifest under the S105 guards.
    guarded = {"fit": 108933, "inner_validation": 26510, "dev": 32063}
    streamed = {name: guarded[name] - exclusions[name] for name in guarded}

    assert streamed == {"fit": 108932, "inner_validation": 26509, "dev": 32063}
