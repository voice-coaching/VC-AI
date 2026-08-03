from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import shutil
from typing import Any

import pytest
import yaml

from voice_coach.training import s2_s200_auditor_contract as s200


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs/experiments/s2_s200_independent_auditor_v1.yaml"


def _write_json(path: Path, value: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = s200.canonical_json_bytes(value, pretty=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _binding(value: dict[str, Any]) -> str:
    body = {key: nested for key, nested in value.items() if key != "binding_sha256"}
    return hashlib.sha256(s200.canonical_json_bytes(body, pretty=False)).hexdigest()


def _copy_static_workspace(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    for pin in config["canonical_sources"]["pins"]:
        source = ROOT / pin["path"]
        target = tmp_path / pin["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    target_config = tmp_path / CONFIG_PATH.relative_to(ROOT)
    target_config.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CONFIG_PATH, target_config)
    return target_config, config


def _create_predecessor(
    tmp_path: Path,
    config: dict[str, Any],
    *,
    alignment_mode: str = "oracle_target_boundary",
) -> tuple[str, dict[str, Any]]:
    predecessor = config["predecessor"]["s108_final_research_candidate"]
    registry_path = predecessor["registry"]["path"]
    registry = {
        "schema_version": 1,
        "run_id": predecessor["registry"]["run_id"],
        "status": "completed",
        "failure": None,
        "artifacts": [
            {"name": name, "sha256": hashlib.sha256(name.encode()).hexdigest()}
            for name in predecessor["required_artifact_hash_names"]
        ],
    }
    registry_sha = _write_json(tmp_path / registry_path, registry)
    oracle = alignment_mode == "oracle_target_boundary"
    commit = {
        "schema_version": "voice_coach.wavlm_s108_final_s1_commit.v1",
        "status": "committed",
        "decision": "research_candidate_passed",
        "research_candidate_completed": True,
        "deployment_approved": False,
        "locked_batch": {
            "access_count": 1,
            "rerun_count": 0,
            "result_based_reselection": False,
        },
        "alignment_evidence": {
            "mode": alignment_mode,
            "oracle_only": oracle,
            "predicted_alignment_evaluated": not oracle,
            "deployment_claim_allowed": False,
        },
        "artifact_hashes": {
            name: hashlib.sha256(f"artifact:{name}".encode()).hexdigest()
            for name in predecessor["required_artifact_hash_names"]
        },
        "registry": {
            "path": registry_path,
            "sha256": registry_sha,
            "run_id": predecessor["registry"]["run_id"],
            "status": "completed",
        },
    }
    commit["binding_sha256"] = _binding(commit)
    commit_sha = _write_json(
        tmp_path / predecessor["commit"]["path"],
        commit,
    )
    return commit_sha, commit


def _create_external_attestation(
    tmp_path: Path,
    config: dict[str, Any],
    *,
    s108_commit_sha: str,
    overlap_override: tuple[str, int] | None = None,
) -> dict[str, Any]:
    overlap = {
        "s1_train_items": 0,
        "s1_dev_items": 0,
        "s1_stack_items": 0,
        "s1_calibration_items": 0,
        "s1_locked_test_items": 0,
        "s1_selection_speakers": 0,
        "s1_labeler_cohort": 0,
    }
    if overlap_override is not None:
        overlap[overlap_override[0]] = overlap_override[1]
    record = {
        "schema_version": "voice_coach.s2_s203_expert_holdout_attestation.v1",
        "status": "ready",
        "aggregate_only": True,
        "contains_rows": False,
        "contains_audio": False,
        "contains_transcripts": False,
        "contains_labels": False,
        "contains_predictions": False,
        "contains_raw_identifiers": False,
        "holdout_opened_for_model_selection": False,
        "item_count": 1000,
        "independent_rater_count": 2,
        "adjudicator_count": 1,
        "overlap_counts": overlap,
        "blinding": {
            "s1_predictions_hidden_from_s2_trainers": True,
            "s2_holdout_gold_hidden_until_freeze": True,
            "reviewers_blind_to_s1_and_s2": True,
            "adjudication_preserves_original_ratings": True,
        },
        "governance": {
            "institutional_approval_complete": True,
            "consent_complete": True,
            "withdrawal_plan_complete": True,
            "retention_and_destruction_plan_complete": True,
            "restricted_redistribution": True,
        },
        "s108_commit_sha256": s108_commit_sha,
        "split_hashes": {
            name: hashlib.sha256(name.encode()).hexdigest()
            for name in (
                "s2_auditor_train",
                "s2_auditor_validation",
                "s2_expert_holdout",
            )
        },
        "rater_cohort_hashes": {
            "s1_labeler_cohort": "1" * 64,
            "s2_labeler_cohort": "2" * 64,
        },
    }
    record["binding_sha256"] = _binding(record)
    path = config["s203"]["expert_holdout_attestation"]["path"]
    _write_json(tmp_path / path, record)
    return record


def _ready_workspace(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    config_path, config = _copy_static_workspace(tmp_path)
    commit_sha, _ = _create_predecessor(tmp_path, config)
    _create_external_attestation(
        tmp_path,
        config,
        s108_commit_sha=commit_sha,
    )
    return config_path, config


def _save_config(path: Path, config: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def test_repository_config_only_is_valid_and_has_no_runtime_access() -> None:
    config = s200.load_config(CONFIG_PATH)
    result = s200.validate_static_config(
        workspace_root=ROOT,
        config_path=CONFIG_PATH,
        config=config,
    )
    output = s200.config_only_result(result)
    assert output["status"] == "valid_config_only"
    assert output["source_pin_read_count"] >= 10
    for field_name in (
        "predecessor_metadata_read_count",
        "external_aggregate_read_count",
        "data_read_count",
        "model_read_count",
        "protected_data_read_count",
        "holdout_read_count",
        "locked_test_read_count",
        "gpu_access_count",
        "metric_count",
        "result_count",
        "output_count",
    ):
        assert output[field_name] == 0


def test_full_preflight_stops_at_missing_predecessor_before_external_or_output(
    tmp_path: Path,
) -> None:
    config_path, config = _copy_static_workspace(tmp_path)
    external_path = tmp_path / config["s203"]["expert_holdout_attestation"]["path"]
    external_path.parent.mkdir(parents=True, exist_ok=True)
    external_path.symlink_to(tmp_path / "must-not-be-read.json")
    with pytest.raises(s200.S200ContractError) as raised:
        s200.run_full_preflight(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )
    audit = raised.value.audit
    assert audit is not None
    assert raised.value.stage == "predecessor_validation"
    assert audit.predecessor_metadata_read_attempt_count == 1
    assert audit.external_aggregate_read_attempt_count == 0
    assert audit.protected_data_read_count == 0
    assert audit.output_count == 0


def test_static_source_pin_mismatch_fails_closed(tmp_path: Path) -> None:
    config_path, config = _copy_static_workspace(tmp_path)
    source_path = tmp_path / config["canonical_sources"]["pins"][0]["path"]
    source_path.write_bytes(source_path.read_bytes() + b"tamper")
    with pytest.raises(s200.S200ContractError, match="source pin mismatch"):
        s200.validate_static_config(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )


def test_source_symlink_is_rejected(tmp_path: Path) -> None:
    config_path, config = _copy_static_workspace(tmp_path)
    source_path = tmp_path / config["canonical_sources"]["pins"][0]["path"]
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(source_path.read_bytes())
    source_path.unlink()
    source_path.symlink_to(replacement)
    with pytest.raises(s200.S200ContractError, match="symlink"):
        s200.validate_static_config(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )


def test_traversal_in_predecessor_path_is_rejected(tmp_path: Path) -> None:
    config_path, config = _copy_static_workspace(tmp_path)
    config["predecessor"]["s108_final_research_candidate"]["commit"]["path"] = (
        "../outside.json"
    )
    _save_config(config_path, config)
    with pytest.raises(s200.S200ContractError, match="workspace-relative"):
        s200.validate_static_config(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )


def test_weakening_s1_prediction_leakage_guard_is_rejected(tmp_path: Path) -> None:
    config_path, config = _copy_static_workspace(tmp_path)
    config["independence"]["leakage"]["s1_predictions_allowed_in_s201_or_s202"] = True
    _save_config(config_path, config)
    with pytest.raises(s200.S200ContractError, match="must equal False"):
        s200.validate_static_config(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )


def test_valid_synthetic_metadata_publishes_only_one_aggregate_lock(
    tmp_path: Path,
) -> None:
    config_path, config = _ready_workspace(tmp_path)
    result = s200.run_full_preflight(
        workspace_root=tmp_path,
        config_path=config_path,
        config=config,
    )
    assert result.readiness_lock_path.is_file()
    assert result.audit["output_count"] == 1
    assert result.readiness_lock["s200_completion_claim_allowed"] is False
    assert result.readiness_lock["actual_model_download_count"] == 0
    assert result.readiness_lock["actual_model_training_count"] == 0
    for field_name in (
        "data_read_count",
        "model_read_count",
        "protected_data_read_count",
        "holdout_read_count",
        "locked_test_read_count",
        "gpu_access_count",
        "metric_count",
        "result_count",
    ):
        assert result.audit[field_name] == 0


def test_no_overwrite_preserves_first_lock(tmp_path: Path) -> None:
    config_path, config = _ready_workspace(tmp_path)
    first = s200.run_full_preflight(
        workspace_root=tmp_path,
        config_path=config_path,
        config=config,
    )
    original = first.readiness_lock_path.read_bytes()
    with pytest.raises(s200.S200PublicationError, match="already exists"):
        s200.run_full_preflight(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )
    assert first.readiness_lock_path.read_bytes() == original


def test_concurrent_publish_allows_exactly_one_lock(tmp_path: Path) -> None:
    config_path, config = _ready_workspace(tmp_path)

    def publish() -> str:
        try:
            s200.run_full_preflight(
                workspace_root=tmp_path,
                config_path=config_path,
                config=config,
            )
        except s200.S200PublicationError:
            return "rejected"
        return "published"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: publish(), range(2)))
    assert sorted(outcomes) == ["published", "rejected"]
    output = tmp_path / config["outputs"]["readiness_lock_path"]
    assert output.is_file()
    assert not list(output.parent.glob(".*.part-*"))


@pytest.mark.parametrize("fault", ["after_temporary_write", "before_publish"])
def test_synthetic_publication_fault_leaves_no_lock_or_part(
    tmp_path: Path,
    fault: str,
) -> None:
    config_path, config = _ready_workspace(tmp_path)
    with pytest.raises(s200.S200PublicationError, match="synthetic fault"):
        s200.run_full_preflight(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
            fault_injection=fault,
        )
    output = tmp_path / config["outputs"]["readiness_lock_path"]
    assert not output.exists()
    if output.parent.exists():
        assert not list(output.parent.glob("*.part-*"))
        assert not list(output.parent.glob(".*.part-*"))


def test_output_parent_symlink_is_rejected(tmp_path: Path) -> None:
    config_path, config = _ready_workspace(tmp_path)
    output = tmp_path / config["outputs"]["readiness_lock_path"]
    output.parent.parent.mkdir(parents=True, exist_ok=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    output.parent.symlink_to(elsewhere)
    with pytest.raises(s200.S200PublicationError, match="non-symlink directory"):
        s200.run_full_preflight(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )
    assert not (elsewhere / output.name).exists()


def test_source_toctou_is_detected_before_output(tmp_path: Path) -> None:
    config_path, config = _ready_workspace(tmp_path)
    source = tmp_path / config["canonical_sources"]["pins"][0]["path"]

    def mutate_source() -> None:
        source.write_bytes(source.read_bytes() + b"changed")

    with pytest.raises(s200.S200ContractError, match="source pin mismatch"):
        s200.run_full_preflight(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
            before_commit_hook=mutate_source,
        )
    assert not (tmp_path / config["outputs"]["readiness_lock_path"]).exists()


def test_oracle_predecessor_cannot_claim_predicted_alignment(tmp_path: Path) -> None:
    config_path, config = _copy_static_workspace(tmp_path)
    _, commit = _create_predecessor(tmp_path, config)
    commit["alignment_evidence"]["predicted_alignment_evaluated"] = True
    commit["binding_sha256"] = _binding(commit)
    _write_json(
        tmp_path
        / config["predecessor"]["s108_final_research_candidate"]["commit"]["path"],
        commit,
    )
    with pytest.raises(s200.S200ContractError, match="predicted marker"):
        s200.run_full_preflight(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )


def test_nonzero_s1_holdout_overlap_is_rejected(tmp_path: Path) -> None:
    config_path, config = _copy_static_workspace(tmp_path)
    commit_sha, _ = _create_predecessor(tmp_path, config)
    _create_external_attestation(
        tmp_path,
        config,
        s108_commit_sha=commit_sha,
        overlap_override=("s1_train_items", 1),
    )
    with pytest.raises(s200.S200ContractError, match="must equal 0"):
        s200.run_full_preflight(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )


def test_predecessor_symlink_is_rejected(tmp_path: Path) -> None:
    config_path, config = _copy_static_workspace(tmp_path)
    commit_path = (
        tmp_path
        / config["predecessor"]["s108_final_research_candidate"]["commit"]["path"]
    )
    commit_path.parent.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "outside-commit.json"
    _write_json(target, {"status": "committed"})
    commit_path.symlink_to(target)
    with pytest.raises(s200.S200ContractError, match="symlink") as raised:
        s200.run_full_preflight(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )
    assert raised.value.audit is not None
    assert raised.value.audit.external_aggregate_read_attempt_count == 0
