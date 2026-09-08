import pytest

from korean_phone_ctc.training_utils import (
    collapse_ctc_ids,
    score_phone_sequences,
    validate_manual_alignment_gate,
)


def test_collapse_ctc_ids() -> None:
    assert collapse_ctc_ids([0, 3, 3, 0, 3, 4, 4, 0]) == [3, 3, 4]


def test_collapse_ctc_ids_ignores_trainer_padding() -> None:
    assert collapse_ctc_ids([0, 3, 3, 0, 4, -100, -100]) == [3, 4]


def test_phone_error_metrics() -> None:
    metrics, confusion = score_phone_sequences(
        [["ㄱ", "ㅏ", "ㄴ"]],
        [["ㄱ", "ㅓ", "ㄴ", "ㄷ"]],
    )
    assert metrics.substitutions == 1
    assert metrics.insertions == 1
    assert metrics.deletions == 0
    assert metrics.per == 2 / 3
    assert confusion[("ㅏ", "ㅓ")] == 1
    assert confusion[("<ins>", "ㄷ")] == 1


def test_manual_gate_requires_passed_independent_result() -> None:
    validate_manual_alignment_gate({"status": "passed", "alignment_accuracy": 0.95})
    with pytest.raises(ValueError, match="status"):
        validate_manual_alignment_gate({"status": "pending", "accuracy": 1.0})
    with pytest.raises(ValueError, match="below"):
        validate_manual_alignment_gate({"status": "passed", "accuracy": 0.949})


def test_training_gate_rejects_legacy_ungated_diagnostic_smoke() -> None:
    from korean_phone_ctc.training_utils import validate_training_gate

    with pytest.raises(ValueError, match="overrides are disabled"):
        validate_training_gate(
            {"status": "pending", "alignment_accuracy": 0.0},
            mode="smoke",
            allow_ungated_diagnostic_smoke=True,
        )


def test_training_gate_rejects_legacy_diagnostic_override_for_full() -> None:
    from korean_phone_ctc.training_utils import validate_training_gate

    with pytest.raises(ValueError, match="overrides are disabled"):
        validate_training_gate(
            {"status": "pending", "alignment_accuracy": 0.0},
            mode="full",
            allow_ungated_diagnostic_smoke=True,
        )


def test_training_gate_still_requires_manual_gate_without_override() -> None:
    from korean_phone_ctc.training_utils import validate_training_gate

    with pytest.raises(ValueError, match="status"):
        validate_training_gate(
            {"status": "pending", "alignment_accuracy": 0.0},
            mode="smoke",
            allow_ungated_diagnostic_smoke=False,
        )


def test_training_gate_rejects_legacy_ungated_internal_full() -> None:
    from korean_phone_ctc.training_utils import validate_training_gate

    with pytest.raises(ValueError, match="overrides are disabled"):
        validate_training_gate(
            {"status": "pending", "alignment_accuracy": 0.0},
            mode="full",
            allow_ungated_internal_full=True,
        )


def test_training_gate_rejects_legacy_internal_override_for_smoke() -> None:
    from korean_phone_ctc.training_utils import validate_training_gate

    with pytest.raises(ValueError, match="overrides are disabled"):
        validate_training_gate(
            {"status": "pending", "alignment_accuracy": 0.0},
            mode="smoke",
            allow_ungated_internal_full=True,
        )
