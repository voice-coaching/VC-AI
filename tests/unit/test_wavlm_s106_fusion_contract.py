from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import pytest

from voice_coach.training import wavlm_s106_fusion_contract as contract


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/experiments/wavlm_s106_stack_fusion_v1.yaml"
SCRIPT = ROOT / "scripts/prepare_wavlm_s106_fusion.py"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _speaker_for_fold(fold: int) -> str:
    for index in range(100_000):
        speaker = hashlib.sha256(f"speaker-{fold}-{index}".encode()).hexdigest()
        if contract.assign_speaker_fold(speaker) == fold:
            return speaker
    raise AssertionError("speaker fold fixture failed")


def _s105_projection_artifacts(
    generation: Path,
    selected_variant: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for seed in contract.SEEDS:
        relative = f"{selected_variant}/seed_{seed}/hierarchy_head.safetensors"
        path = generation / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"synthetic S105 checkpoint {seed}\n".encode())
        records.append(
            {
                "name": (f"{selected_variant}_seed_{seed}_hierarchy_checkpoint"),
                "path": relative,
                "sha256": _sha(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return records


def test_repository_config_and_source_pins_are_valid() -> None:
    config = contract.load_config(CONFIG)
    contract.validate_config(config)
    sources = contract.validate_implementation_sources(
        workspace_root=ROOT,
        config=config,
    )
    assert set(sources) == {
        "preflight_script",
        "training_script",
        "contract_module",
        "training_module",
        "component_projection_module",
        "component_generator_config",
        "component_generator_script",
        "component_generator_module",
        "project_manifest",
        "scorer_lock",
    }
    assert sources["preflight_script"]["sha256"] == _sha(SCRIPT)
    assert sources["contract_module"]["sha256"] == _sha(
        ROOT / "src/voice_coach/training/wavlm_s106_fusion_contract.py"
    )
    assert sources["training_script"]["sha256"] == _sha(
        ROOT / "scripts/train_wavlm_s106_fusion.py"
    )
    assert sources["training_module"]["sha256"] == _sha(
        ROOT / "src/voice_coach/training/wavlm_s106_fusion_training.py"
    )
    assert sources["component_projection_module"]["sha256"] == _sha(
        ROOT / "src/voice_coach/training/wavlm_s106_component_projection.py"
    )
    s104 = config["predecessors"]["s104"]
    assert s104["config"] == contract.S104_CONFIG_PATH
    assert s104["config_sha256"] == contract.S104_CONFIG_SHA256
    assert s104["config_sha256"] == _sha(ROOT / s104["config"])
    assert s104["validator_module"]["path"] == contract.S104_VALIDATOR_MODULE_PATH
    assert s104["validator_module"]["sha256"] == contract.S104_VALIDATOR_MODULE_SHA256
    assert s104["validator_module"]["sha256"] == _sha(
        ROOT / s104["validator_module"]["path"]
    )
    s105 = config["predecessors"]["s105"]
    assert s105["config_sha256"] == _sha(ROOT / s105["config"])
    assert s105["validator_module"]["sha256"] == _sha(
        ROOT / s105["validator_module"]["path"]
    )
    b07 = config["predecessors"]["b07"]
    assert b07["config_sha256"] == contract.B07_CONFIG_SHA256
    assert b07["commit_sha256"] == contract.B07_COMMIT_SHA256
    assert b07["registry_sha256"] == contract.B07_REGISTRY_SHA256
    assert b07["generation_id"] == contract.B07_GENERATION_ID


def test_config_only_cli_is_non_accessing_and_non_writing() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--workspace-root",
            str(ROOT),
            "--config",
            str(CONFIG),
            "--config-only",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "config_valid"
    assert result["predecessor_validators_imported"] is False
    assert result["s104_read"] is False
    assert result["s105_read"] is False
    assert result["b07_read"] is False
    assert result["predecessor_audit_train_read"] is False
    assert result["predecessor_audit_dev_read"] is False
    assert result["predecessor_audit_dev_predictions_read"] is False
    assert result["fusion_feature_train_read"] is False
    assert result["fusion_feature_dev_read"] is False
    assert result["b07_dev_predictions_reused_as_component"] is False
    assert result["component_prediction_files_written"] == 0
    assert result["explicit_component_read"] is False
    assert result["stack_path_resolved"] is False
    assert result["stack_read"] is False
    assert result["calibration_read"] is False
    assert result["locked_test_read"] is False
    assert result["gpu_initialized"] is False
    assert result["output_written"] is False


def test_predecessor_failure_occurs_before_stack_resolution_or_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []

    def fail_predecessors(**_: object) -> object:
        events.append("predecessors")
        raise contract.S106ContractError("S105 commit is absent")

    def forbidden_stack_resolution(**_: object) -> object:
        events.append("stack")
        raise AssertionError("stack resolver must not be called")

    monkeypatch.setattr(contract, "validate_predecessors", fail_predecessors)
    monkeypatch.setattr(contract, "_resolve_stack_contract", forbidden_stack_resolution)
    output = tmp_path / "execution_lock.json"
    with pytest.raises(contract.S106ContractError, match="S105 commit is absent"):
        contract.prepare_s106_execution(
            workspace_root=ROOT,
            config_path=CONFIG,
            output_path=output,
        )
    assert events == ["predecessors"]
    assert not output.exists()


def test_s104_is_validated_before_s105_and_s105_failure_stops_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = contract.load_config(CONFIG)
    events: list[str] = []
    dummy = contract.PredecessorEvidence(
        stage="s104",
        commit_path="artifacts/s104/commit.json",
        commit_sha256="1" * 64,
        generation_id="2" * 64,
        report_path="artifacts/s104/report.json",
        report_sha256="3" * 64,
        selected_variant="base_plus",
        adoption_status=None,
    )

    def valid_s104(**_: object) -> contract.PredecessorEvidence:
        events.append("s104")
        return dummy

    def absent_s105(**_: object) -> contract.PredecessorEvidence:
        events.append("s105")
        raise contract.S106ContractError("S105 predecessor is absent")

    def forbidden_b07(**_: object) -> contract.PredecessorEvidence:
        events.append("b07")
        raise AssertionError("B07 must not run after S105 failure")

    monkeypatch.setattr(contract, "_validate_s104_predecessor", valid_s104)
    monkeypatch.setattr(contract, "_validate_s105_predecessor", absent_s105)
    monkeypatch.setattr(contract, "_validate_b07_predecessor", forbidden_b07)
    with pytest.raises(contract.S106ContractError, match="S105 predecessor"):
        contract.validate_predecessors(
            workspace_root=ROOT,
            config=config,
        )
    assert events == ["s104", "s105"]


def test_b07_is_validated_after_s104_and_s105(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = contract.load_config(CONFIG)
    events: list[str] = []

    def evidence(stage: str) -> contract.PredecessorEvidence:
        return contract.PredecessorEvidence(
            stage=stage,
            commit_path=f"artifacts/{stage}/commit.json",
            commit_sha256=stage[0] * 64,
            generation_id="1" * 64,
            report_path=f"artifacts/{stage}/report.json",
            report_sha256="2" * 64,
            selected_variant=(
                "base_plus"
                if stage == "s104"
                else (
                    "simple_head_control"
                    if stage == "s105"
                    else "gop_acoustic_posterior"
                )
            ),
            adoption_status=None if stage == "s104" else "passed",
        )

    def s104(**_: object) -> contract.PredecessorEvidence:
        events.append("s104")
        return evidence("s104")

    def s105(**_: object) -> contract.PredecessorEvidence:
        events.append("s105")
        return evidence("s105")

    def b07(**_: object) -> contract.PredecessorEvidence:
        events.append("b07")
        raise contract.S106ContractError("B07 predecessor is absent")

    monkeypatch.setattr(contract, "_validate_s104_predecessor", s104)
    monkeypatch.setattr(contract, "_validate_s105_predecessor", s105)
    monkeypatch.setattr(contract, "_validate_b07_predecessor", b07)
    with pytest.raises(contract.S106ContractError, match="B07 predecessor"):
        contract.validate_predecessors(workspace_root=ROOT, config=config)
    assert events == ["s104", "s105", "b07"]


@pytest.mark.parametrize(
    ("adopted", "selected"),
    [
        (True, "levelwise_multi_aspect_hierarchy"),
        (False, "simple_head_control"),
    ],
)
def test_s105_adoption_decision_selects_one_frozen_variant(
    tmp_path: Path,
    adopted: bool,
    selected: str,
) -> None:
    generation_id = "a" * 64
    generation = tmp_path / "artifacts/s105/generations" / generation_id
    report_path = generation / "ablation_comparison_report.json"
    report = {
        "schema_version": contract.S105_REPORT_SCHEMA_VERSION,
        "status": "completed",
        "development_only": True,
        "oracle_alignment": True,
        "silver_label": True,
        "promotion_decision_allowed": False,
        "control_variant": "simple_head_control",
        "candidate_variant": "levelwise_multi_aspect_hierarchy",
        "adoption_gate": {
            "status": "passed" if adopted else "failed",
            "adopt_candidate": adopted,
        },
        "leakage": {
            "stack_accessed": False,
            "calibration_accessed": False,
            "locked_test_accessed": False,
        },
    }
    _write_json(report_path, report)
    config_path = tmp_path / contract.S105_CONFIG_PATH
    config_path.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / contract.S105_CONFIG_PATH, config_path)
    validator_module_path = tmp_path / contract.S105_VALIDATOR_MODULE_PATH
    validator_module_path.parent.mkdir(parents=True)
    shutil.copyfile(
        ROOT / contract.S105_VALIDATOR_MODULE_PATH,
        validator_module_path,
    )
    commit_path = tmp_path / "artifacts/s105/commit.json"
    commit = {
        "schema_version": contract.S105_COMMIT_SCHEMA_VERSION,
        "status": "committed",
        "generation_id": generation_id,
        "generation_path": generation.relative_to(tmp_path).as_posix(),
        "adoption_status": "passed" if adopted else "failed",
        "selected_variant": selected,
        "artifacts": [
            {
                "name": "ablation_comparison_report",
                "path": "ablation_comparison_report.json",
                "sha256": _sha(report_path),
                "size_bytes": report_path.stat().st_size,
            },
            *_s105_projection_artifacts(generation, selected),
        ],
    }
    _write_json(commit_path, commit)
    spec = {
        "config": config_path.relative_to(tmp_path).as_posix(),
        "config_sha256": contract.S105_CONFIG_SHA256,
        "commit_manifest": commit_path.relative_to(tmp_path).as_posix(),
        "commit_schema_version": contract.S105_COMMIT_SCHEMA_VERSION,
        "comparison_report_schema_version": contract.S105_REPORT_SCHEMA_VERSION,
        "comparison_report_relative_path": "ablation_comparison_report.json",
        "independent_validator": "fixture.validate_s105_commit",
        "validator_module": {
            "path": contract.S105_VALIDATOR_MODULE_PATH,
            "sha256": contract.S105_VALIDATOR_MODULE_SHA256,
        },
        "required_validator_status": "valid",
        "required_commit_status": "committed",
        "required_report_status": "completed",
        "control_variant": "simple_head_control",
        "candidate_variant": "levelwise_multi_aspect_hierarchy",
    }

    def validator(_: str, __: Path, ___: Path) -> dict[str, Any]:
        return {
            "status": "valid",
            "commit_sha256": _sha(commit_path),
            "selected_variant": selected,
            "adoption_status": "passed" if adopted else "failed",
            "development_only": True,
            "stack_accessed": False,
            "calibration_accessed": False,
            "locked_test_accessed": False,
        }

    evidence = contract._validate_s105_predecessor(
        workspace_root=tmp_path,
        spec=spec,
        invoker=validator,
    )
    assert evidence.selected_variant == selected
    assert evidence.adoption_status == ("passed" if adopted else "failed")


def test_s105_commit_cannot_override_recomputed_adoption_selection(
    tmp_path: Path,
) -> None:
    generation_id = "a" * 64
    generation = tmp_path / "artifacts/s105/generations" / generation_id
    report_path = generation / "ablation_comparison_report.json"
    _write_json(
        report_path,
        {
            "schema_version": contract.S105_REPORT_SCHEMA_VERSION,
            "status": "completed",
            "development_only": True,
            "oracle_alignment": True,
            "silver_label": True,
            "promotion_decision_allowed": False,
            "control_variant": "simple_head_control",
            "candidate_variant": "levelwise_multi_aspect_hierarchy",
            "adoption_gate": {"status": "failed", "adopt_candidate": False},
            "leakage": {
                "stack_accessed": False,
                "calibration_accessed": False,
                "locked_test_accessed": False,
            },
        },
    )
    config_path = tmp_path / "configs/s105.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("schema_version: 1\n", encoding="utf-8")
    commit_path = tmp_path / "artifacts/s105/commit.json"
    _write_json(
        commit_path,
        {
            "schema_version": contract.S105_COMMIT_SCHEMA_VERSION,
            "status": "committed",
            "generation_id": generation_id,
            "generation_path": generation.relative_to(tmp_path).as_posix(),
            "adoption_status": "failed",
            "selected_variant": "levelwise_multi_aspect_hierarchy",
            "artifacts": [
                {
                    "name": "ablation_comparison_report",
                    "path": "ablation_comparison_report.json",
                    "sha256": _sha(report_path),
                    "size_bytes": report_path.stat().st_size,
                },
                *_s105_projection_artifacts(
                    generation,
                    "levelwise_multi_aspect_hierarchy",
                ),
            ],
        },
    )
    spec = {
        "config": config_path.relative_to(tmp_path).as_posix(),
        "commit_manifest": commit_path.relative_to(tmp_path).as_posix(),
        "commit_schema_version": contract.S105_COMMIT_SCHEMA_VERSION,
        "comparison_report_schema_version": contract.S105_REPORT_SCHEMA_VERSION,
        "comparison_report_relative_path": "ablation_comparison_report.json",
        "independent_validator": "fixture.validate_s105_commit",
        "required_validator_status": "valid",
        "required_commit_status": "committed",
        "required_report_status": "completed",
        "control_variant": "simple_head_control",
        "candidate_variant": "levelwise_multi_aspect_hierarchy",
    }

    def validator(_: str, __: Path, ___: Path) -> dict[str, Any]:
        return {
            "status": "valid",
            "commit_sha256": _sha(commit_path),
            "selected_variant": "levelwise_multi_aspect_hierarchy",
            "adoption_status": "failed",
            "development_only": True,
            "stack_accessed": False,
            "calibration_accessed": False,
            "locked_test_accessed": False,
        }

    with pytest.raises(contract.S106ContractError, match="selected_variant"):
        contract._validate_s105_predecessor(
            workspace_root=tmp_path,
            spec=spec,
            invoker=validator,
        )


def test_exact_feature_allowlist_excludes_labels_and_realized_annotations() -> None:
    contract.validate_feature_columns(contract.EXACT_FEATURES)
    assert len(contract.EXACT_FEATURES) == 26
    assert contract.COMPONENTS[-1] == "gop_acoustic_posterior"
    for forbidden in (
        "reference_binary_error_label",
        "realized_phone",
        "alignment_operation",
        "error_tag",
    ):
        altered = list(contract.EXACT_FEATURES)
        altered[0] = forbidden
        with pytest.raises(contract.S106ContractError):
            contract.validate_feature_columns(altered)


def test_component_seed_aggregation_is_frozen_before_stack() -> None:
    summary = contract.aggregate_component_probabilities({42: 0.2, 43: 0.5, 44: 0.8})
    assert summary["probability_mean"] == pytest.approx(0.5)
    assert summary["logit_from_mean_probability"] == pytest.approx(0.0)
    assert summary["margin_abs_2p_minus_1"] == pytest.approx(0.0)
    assert summary["seed_probability_variance_population"] == pytest.approx(0.06)
    assert summary["missing"] == 0.0

    missing = contract.aggregate_component_probabilities({})
    assert missing["probability_mean"] == 0.5
    assert missing["entropy_binary"] == pytest.approx(0.6931471805599453)
    assert missing["missing"] == 1.0
    with pytest.raises(contract.S106ContractError, match="seed set"):
        contract.aggregate_component_probabilities({42: 0.2, 43: 0.5})


def test_alignment_confidence_has_no_label_or_realization_input() -> None:
    assert contract.alignment_confidence_features(0.75) == {
        "target_interval_confidence": 0.75,
        "confidence_missing": 0.0,
    }
    assert contract.alignment_confidence_features(None) == {
        "target_interval_confidence": 0.0,
        "confidence_missing": 1.0,
    }
    with pytest.raises(contract.S106ContractError, match="outside"):
        contract.alignment_confidence_features(1.01)


@pytest.mark.parametrize("split", ["train", "dev", "calibration", "locked_test"])
def test_s106_rejects_every_non_stack_split(split: str) -> None:
    with pytest.raises(contract.S106ContractError, match="cannot access"):
        contract.assert_s106_split(split)
    contract.assert_s106_split("stack")


def test_static_config_rejects_per_seed_channels_and_label_feature() -> None:
    original = contract.load_config(CONFIG)
    per_seed = copy.deepcopy(original)
    per_seed["seed_aggregation"]["per_seed_channels_allowed"] = True
    with pytest.raises(contract.S106ContractError, match="per_seed_channels"):
        contract.validate_config(per_seed)

    label_feature = copy.deepcopy(original)
    label_feature["feature_contract"]["exact_order"][0] = "reference_binary_error_label"
    with pytest.raises(contract.S106ContractError, match="feature"):
        contract.validate_config(label_feature)

    s105_drift = copy.deepcopy(original)
    s105_drift["predecessors"]["s105"]["config_sha256"] = "0" * 64
    with pytest.raises(contract.S106ContractError, match="s105.config_sha256"):
        contract.validate_config(s105_drift)

    s104_config_drift = copy.deepcopy(original)
    s104_config_drift["predecessors"]["s104"]["config_sha256"] = "0" * 64
    with pytest.raises(contract.S106ContractError, match="s104.config_sha256"):
        contract.validate_config(s104_config_drift)

    s104_module_drift = copy.deepcopy(original)
    s104_module_drift["predecessors"]["s104"]["validator_module"]["sha256"] = "0" * 64
    with pytest.raises(
        contract.S106ContractError,
        match="s104.validator_module.sha256",
    ):
        contract.validate_config(s104_module_drift)

    in_sample_metric = copy.deepcopy(original)
    in_sample_metric["oof"]["final_fit"]["in_sample_metrics_reported"] = True
    with pytest.raises(contract.S106ContractError, match="in_sample_metrics_reported"):
        contract.validate_config(in_sample_metric)


def test_oof_assignments_are_speaker_disjoint_and_exact() -> None:
    rows: list[dict[str, Any]] = []
    for fold in range(contract.OOF_FOLD_COUNT):
        speaker = _speaker_for_fold(fold)
        for phone_index in (0, 1):
            utterance = hashlib.sha256(
                f"utterance-{fold}-{phone_index}".encode()
            ).hexdigest()
            rows.append(
                {
                    "utterance_id_sha256": utterance,
                    "speaker_id_sha256": speaker,
                    "phone_index": phone_index,
                    "fold": fold,
                    "split": "stack",
                }
            )
    summary = contract.validate_oof_assignments(rows)
    assert summary["row_count"] == 10
    assert summary["speaker_count"] == 5
    assert summary["speaker_overlap_maximum"] == 0
    assert summary["every_row_exactly_one_oof_prediction"] is True


def test_oof_rejects_a_speaker_assigned_to_a_different_fold() -> None:
    rows: list[dict[str, Any]] = []
    for fold in range(contract.OOF_FOLD_COUNT):
        speaker = _speaker_for_fold(fold)
        rows.append(
            {
                "utterance_id_sha256": hashlib.sha256(
                    f"utterance-{fold}".encode()
                ).hexdigest(),
                "speaker_id_sha256": speaker,
                "phone_index": 0,
                "fold": (fold + 1) % contract.OOF_FOLD_COUNT if fold == 0 else fold,
                "split": "stack",
            }
        )
    with pytest.raises(contract.S106ContractError, match="stable speaker"):
        contract.validate_oof_assignments(rows)


def test_hash_join_allows_declared_missingness_but_rejects_speaker_mismatch() -> None:
    utterance_a = hashlib.sha256(b"utterance-a").hexdigest()
    utterance_b = hashlib.sha256(b"utterance-b").hexdigest()
    speaker_a = hashlib.sha256(b"speaker-a").hexdigest()
    speaker_b = hashlib.sha256(b"speaker-b").hexdigest()
    universe = {
        (utterance_a, 0): speaker_a,
        (utterance_b, 0): speaker_b,
    }
    components = {
        "explicit": dict(universe),
        "s104_adapter": {(utterance_a, 0): speaker_a},
        "s105_selected_hierarchy": dict(universe),
        "gop_acoustic_posterior": dict(universe),
    }
    result = contract.validate_component_join(universe, components)
    assert result["component_missing_rows"]["s104_adapter"] == 1
    assert result["labels_visible_to_feature_builder"] is False

    mismatched = copy.deepcopy(components)
    mismatched["explicit"][(utterance_a, 0)] = speaker_b
    with pytest.raises(contract.S106ContractError, match="speaker binding"):
        contract.validate_component_join(universe, mismatched)


def test_duplicate_yaml_key_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.yaml"
    path.write_text("schema_version: 1\nschema_version: 1\n", encoding="utf-8")
    with pytest.raises(contract.S106ContractError, match="duplicate key"):
        contract.load_config(path)


def test_execution_lock_publication_is_atomic_idempotent_and_immutable(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "execution_lock.json"
    payload = {"schema_version": "fixture.v1", "status": "ready"}
    contract.publish_immutable_json(destination, payload)
    first = destination.read_bytes()
    contract.publish_immutable_json(destination, payload)
    assert destination.read_bytes() == first
    with pytest.raises(contract.S106ContractError, match="differs"):
        contract.publish_immutable_json(
            destination,
            {"schema_version": "fixture.v1", "status": "changed"},
        )
    assert not list(tmp_path.glob(".*.part-*"))

    symlink_target = tmp_path / "symlink-target.json"
    symlink_target.write_bytes(first)
    symlink_destination = tmp_path / "symlink-lock.json"
    symlink_destination.symlink_to(symlink_target)
    with pytest.raises(contract.S106ContractError, match="differs"):
        contract.publish_immutable_json(symlink_destination, payload)

    real_directory = tmp_path / "real-directory"
    real_directory.mkdir()
    linked_directory = tmp_path / "linked-directory"
    linked_directory.symlink_to(real_directory, target_is_directory=True)
    with pytest.raises(contract.S106ContractError, match="symlink"):
        contract._resolve_output(  # noqa: SLF001 - security-boundary regression
            tmp_path,
            "linked-directory/lock.json",
            "synthetic output",
        )
