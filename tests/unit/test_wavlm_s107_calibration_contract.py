from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import numpy as np
import pytest

from voice_coach.training.wavlm_s107_calibration_contract import (
    S107ContractError,
    load_config,
    validate_config,
)
from voice_coach.training.wavlm_s107_calibration_training import (
    CalibrationData,
    fit_temperature,
    reliability_bins,
    select_issue_threshold,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPOSITORY_ROOT / "configs/experiments/wavlm_s107_calibration_v1.yaml"


def _synthetic_data() -> CalibrationData:
    rows = []
    probabilities = []
    labels = []
    speakers = []
    for index in range(120):
        label = 1 if index % 3 == 0 else 0
        probability = (
            0.82 + 0.001 * (index % 10) if label else 0.08 + 0.004 * (index % 20)
        )
        utterance = sha256(f"utterance-{index}".encode()).hexdigest()
        speaker = sha256(f"speaker-{index % 12}".encode()).hexdigest()
        rows.append(
            {
                "utterance_id_sha256": utterance,
                "speaker_id_sha256": speaker,
                "phone_index": 0,
                "split": "calibration",
                "uncalibrated_probability": probability,
                "reference_binary_error_label": label,
                "l1_group": "Chinese" if index % 2 else "Japanese",
                "gender_group": "F" if index % 2 else "M",
                "proficiency_group": "low" if index % 2 else "high",
                "duration_bucket": "short" if index % 2 else "long",
                "error_type_group": "binary_error" if label else "correct",
            }
        )
        probabilities.append(probability)
        labels.append(label)
        speakers.append(speaker)
    return CalibrationData(
        rows=tuple(rows),
        probabilities=np.asarray(probabilities, dtype=np.float64),
        labels=np.asarray(labels, dtype=np.int64),
        speakers=tuple(speakers),
    )


def test_canonical_config_freezes_fail_closed_policy() -> None:
    config = load_config(CONFIG_PATH)
    validate_config(config)
    assert config["frozen_model_policy"]["permitted_fitted_parameters"] == [
        "binary_temperature",
        "issue_threshold",
    ]
    assert config["label_evidence"]["canonical_run_required_status"] == (
        "silver_oracle_provisional"
    )
    assert config["threshold_policy"]["promotion_gate_use_allowed"] is False
    assert config["reporting"]["s108_ready_status"] == (
        "blocked_preregistration_and_d07"
    )


def test_duplicate_yaml_key_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.yaml"
    path.write_text("schema_version: 1\nschema_version: 2\n", encoding="utf-8")
    with pytest.raises(S107ContractError, match="duplicate key"):
        load_config(path)


def test_reliability_keeps_all_empty_bins_with_zero_contribution() -> None:
    probabilities: np.ndarray = np.asarray([0.01, 0.99], dtype=np.float64)
    labels: np.ndarray = np.asarray([0, 1], dtype=np.int64)
    bins, ece = reliability_bins(probabilities, labels, bins=15)
    assert len(bins) == 15
    empty = [row for row in bins if row["count"] == 0]
    assert len(empty) == 13
    assert all(row["mean_probability"] is None for row in empty)
    assert all(row["event_rate"] is None for row in empty)
    assert all(row["ece_contribution"] == 0.0 for row in empty)
    assert ece == pytest.approx(0.01)


def test_temperature_and_threshold_are_bounded_and_provisional() -> None:
    config = load_config(CONFIG_PATH)
    data = _synthetic_data()
    fit = fit_temperature(data, config)
    selection = select_issue_threshold(data=data, fit=fit, config=config)
    assert 0.05 <= fit.temperature <= 20.0
    assert np.all(
        (fit.calibrated_probabilities > 0) & (fit.calibrated_probabilities < 1)
    )
    assert selection.status == "selected_provisional_engineering"
    assert selection.issued_precision is not None
    assert selection.issued_precision >= 0.9
    assert selection.bootstrap["promotion_gate_use_allowed"] is False


def test_threshold_fail_closed_abstains_when_no_candidate() -> None:
    config = load_config(CONFIG_PATH)
    data = _synthetic_data()
    inverted = CalibrationData(
        rows=data.rows,
        probabilities=1.0 - data.probabilities,
        labels=data.labels,
        speakers=data.speakers,
    )
    fit = fit_temperature(inverted, config)
    selection = select_issue_threshold(data=inverted, fit=fit, config=config)
    assert selection.status == "abstain_all_no_provisional_candidate"
    assert selection.threshold == 1.0
    assert selection.issued_count == 0
    assert selection.issued_precision is None
