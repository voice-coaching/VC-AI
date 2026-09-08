from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import torch

from voice_coach.models.wavlm_hierarchy import HierarchyOutput
from voice_coach.training import wavlm_hierarchy_training as training


def test_phone_word_mapping_uses_overlap_tie_break_and_orphan_bucket() -> None:
    phones = [
        {"start": 0.0, "end": 0.2},
        {"start": 0.9, "end": 1.1},
        {"start": 2.0, "end": 2.1},
    ]
    words = [
        {"start": 0.0, "end": 1.0},
        {"start": 1.0, "end": 2.0},
    ]
    # The second phone overlaps both words equally and chooses index 0.
    assert training.map_phones_to_words(phones, words) == (0, 0, -1)


def test_phone_word_mapping_rejects_unordered_or_invalid_intervals() -> None:
    with pytest.raises(training.S105TrainingError, match="not ordered"):
        training.map_phones_to_words(
            [{"start": 1.0, "end": 2.0}, {"start": 0.0, "end": 1.0}],
            [{"start": 0.0, "end": 2.0}],
        )
    with pytest.raises(training.S105TrainingError, match="bounds"):
        training.map_phones_to_words(
            [{"start": 1.0, "end": 1.0}],
            [{"start": 0.0, "end": 2.0}],
        )


def test_joint_loss_masks_missing_utterance_outcomes_and_forbids_word_loss() -> None:
    logits = torch.tensor([0.0, 1.0], requires_grad=True)
    scores = torch.tensor([2.0, 3.0, 4.0], requires_grad=True)
    output = HierarchyOutput(phone_logits=logits, utterance_scores=scores)
    bundle = training.joint_hierarchy_loss(
        output,
        phone_targets=torch.tensor([0.0, 1.0]),
        utterance_targets=torch.tensor([2.5, float("nan"), 3.5]),
        positive_weight=torch.tensor(1.0),
        phone_loss_weight=1.0,
        utterance_loss_weight=1.0,
    )
    assert bundle.observed_utterance_outcomes == 2
    bundle.total.backward()
    assert logits.grad is not None
    assert scores.grad is not None
    assert scores.grad[1] == 0

    with pytest.raises(training.S105TrainingError, match="word loss"):
        training.joint_hierarchy_loss(
            output,
            phone_targets=torch.tensor([0.0, 1.0]),
            utterance_targets=torch.tensor([2.5, 3.0, 3.5]),
            positive_weight=torch.tensor(1.0),
            phone_loss_weight=1.0,
            utterance_loss_weight=1.0,
            word_loss_weight=0.1,
        )


def test_dev_pass_ledger_requires_one_pass_per_variant_task_seed() -> None:
    ledger = training.DevPassLedger(
        variants=["simple_head_control"],
        tasks=["phone_binary_error", "utterance_scores"],
        seeds=[42],
    )
    for task in ("phone_binary_error", "utterance_scores"):
        ledger.enter(variant="simple_head_control", task=task, seed=42)
        ledger.complete(variant="simple_head_control", task=task, seed=42)
    ledger.assert_exact()
    with pytest.raises(training.S105TrainingError, match="more than once"):
        ledger.enter(
            variant="simple_head_control",
            task="phone_binary_error",
            seed=42,
        )

    incomplete = training.DevPassLedger(
        variants=["simple_head_control"],
        tasks=["phone_binary_error"],
        seeds=[42],
    )
    with pytest.raises(training.S105TrainingError, match="not exactly complete"):
        incomplete.assert_exact()


@pytest.mark.parametrize("split", ["stack", "calibration", "locked_test"])
def test_split_guard_rejects_every_protected_split(split: str) -> None:
    with pytest.raises(training.S105TrainingError, match="protected"):
        training.assert_allowed_split(split, training=True)


def _paired_fixture() -> tuple[
    np.ndarray,
    np.ndarray,
    dict[int, np.ndarray],
    dict[int, np.ndarray],
]:
    speakers = np.asarray(["a"] * 4 + ["b"] * 4 + ["c"] * 4)
    targets = np.asarray([0, 1, 0, 1] * 3, dtype=np.int8)
    control = {
        seed: np.asarray([0, 0, 0, 1] * 3, dtype=np.int8) for seed in training.SEEDS
    }
    candidate = {seed: targets.copy() for seed in training.SEEDS}
    return speakers, targets, control, candidate


def test_paired_phone_bootstrap_is_deterministic_and_candidate_minus_control() -> None:
    speakers, targets, control, candidate = _paired_fixture()
    first = training.paired_phone_bootstrap(
        speaker_ids=speakers,
        targets=targets,
        control_predictions=control,
        candidate_predictions=candidate,
        n_resamples=100,
    )
    second = training.paired_phone_bootstrap(
        speaker_ids=speakers,
        targets=targets,
        control_predictions=control,
        candidate_predictions=candidate,
        n_resamples=100,
    )
    assert first == second
    assert first.estimate > 0
    assert first.n_speakers == 3


def test_paired_utterance_bootstrap_is_deterministic() -> None:
    speakers = np.asarray(["a", "a", "b", "b", "c", "c"])
    targets = np.asarray([1, 2, 2, 3, 4, 5], dtype=np.float64)
    control = {
        seed: np.asarray([3, 3, 3, 3, 3, 3], dtype=np.float64)
        for seed in training.SEEDS
    }
    candidate = {seed: targets.copy() for seed in training.SEEDS}
    result = training.paired_utterance_icc_bootstrap(
        speaker_ids=speakers,
        targets=targets,
        control_predictions=control,
        candidate_predictions=candidate,
        n_resamples=100,
    )
    assert result.estimate > 0
    assert result.n_valid_resamples > 0


def test_subgroup_drop_preserves_negative_improvement_and_applies_exclusive_gate() -> (
    None
):
    speakers, targets, control, candidate = _paired_fixture()
    result = training.subgroup_worst_group_drop(
        speaker_ids=speakers,
        targets=targets,
        groups_by_axis={
            "l1": np.asarray(["Chinese"] * 12),
            "gender": np.asarray(["F"] * 12),
            "proficiency": np.asarray(["beginner"] * 12),
        },
        control_predictions=control,
        candidate_predictions=candidate,
        fixed_groups={
            "l1": ["Chinese"],
            "gender": ["F"],
            "proficiency": ["beginner"],
        },
        minimum_unique_speakers=2,
        minimum_positive_phone_rows=2,
        minimum_negative_phone_rows=2,
        threshold_exclusive=0.05,
    )
    assert result.worst_group_drop < 0
    assert result.passed
    assert all(
        row["control_minus_candidate"] < 0 for row in result.groups if row["eligible"]
    )


def _bootstrap(estimate: float, p_value: float) -> training.BootstrapResult:
    return training.BootstrapResult(
        estimate=estimate,
        confidence_lower=estimate - 0.01,
        confidence_upper=estimate + 0.01,
        confidence_level=0.95,
        n_resamples=5000,
        n_valid_resamples=5000,
        n_speakers=30,
        seed=42,
        p_value_two_sided_zero=p_value,
    )


def _incremental() -> dict[str, dict[str, training.BootstrapResult]]:
    return {
        pair: {
            "phone_macro_f1": _bootstrap(0.01, 0.20 + index * 0.01),
            "comprehensibility_score_icc_2_1": _bootstrap(
                0.01,
                0.30 + index * 0.01,
            ),
        }
        for index, pair in enumerate(training.ADJACENT_VARIANT_PAIRS)
    }


def _subgroup() -> training.SubgroupResult:
    return training.SubgroupResult(
        groups=(
            {
                "axis": "l1",
                "group": "Chinese",
                "eligible": True,
                "control_minus_candidate": 0.01,
            },
        ),
        eligible_group_count=1,
        worst_group_drop=0.01,
        threshold_exclusive=0.05,
        passed=True,
    )


def test_adoption_gate_uses_point_estimate_and_drop_not_holm() -> None:
    report = training.build_adoption_report(
        phone_bootstrap=_bootstrap(0.031, 0.8),
        utterance_bootstrap=_bootstrap(0.0, 0.9),
        subgroup=_subgroup(),
        incremental_comparisons=_incremental(),
    )
    assert report["adoption_gate"]["status"] == "passed"
    assert report["adoption_gate"]["adopt_candidate"] is True
    assert report["multiplicity"]["primary"]["significance_used_for_gate"] is False
    assert report["multiplicity"]["incremental"]["family_size"] == 8
    training.validate_comparison_report(report)


def _write_generation(root: Path) -> None:
    for _, relative in training.expected_artifact_layout():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative == "ablation_comparison_report.json":
            report = training.build_adoption_report(
                phone_bootstrap=_bootstrap(0.031, 0.01),
                utterance_bootstrap=_bootstrap(0.0, 0.5),
                subgroup=_subgroup(),
                incremental_comparisons=_incremental(),
            )
            path.write_text(json.dumps(report), encoding="utf-8")
        elif relative.endswith("variant_report.json"):
            variant = relative.split("/")[0]
            path.write_text(
                json.dumps(
                    {
                        "schema_version": training.REPORT_SCHEMA_VERSION,
                        "status": "completed",
                        "variant": variant,
                        "seeds": list(training.SEEDS),
                        "dev_passes_per_task_seed": 1,
                        "word_loss_performed": False,
                        "word_pcc_status": "not_evaluable",
                        "stack_accessed": False,
                        "calibration_accessed": False,
                        "locked_test_accessed": False,
                    }
                ),
                encoding="utf-8",
            )
        else:
            path.write_bytes(f"fixture:{relative}\n".encode())


def test_exact_82_file_generation_validator_rejects_extra_artifact(
    tmp_path: Path,
) -> None:
    _write_generation(tmp_path)
    validation = training.validate_generation(tmp_path)
    assert validation["artifact_count"] == 82

    (tmp_path / "unexpected.txt").write_text("bad", encoding="utf-8")
    with pytest.raises(training.S105TrainingError, match="tree differs"):
        training.validate_generation(tmp_path)


def test_comparison_validator_rejects_word_metric_or_protected_access() -> None:
    report = dict(
        training.build_adoption_report(
            phone_bootstrap=_bootstrap(0.0, 0.5),
            utterance_bootstrap=_bootstrap(0.0, 0.5),
            subgroup=_subgroup(),
            incremental_comparisons=_incremental(),
        )
    )
    report["word_supervision"] = {
        "role": "supervised",
        "word_loss_performed": True,
        "word_pcc_status": "reported",
    }
    with pytest.raises(training.S105TrainingError, match="word-supervision"):
        training.validate_comparison_report(report)
