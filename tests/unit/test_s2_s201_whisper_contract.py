from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

from voice_coach.training import s2_s200_auditor_contract as s200
from voice_coach.training import s2_s201_whisper_contract as s201


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs/experiments/s2_s201_whisper_content_gate_v1.yaml"
S200_CONFIG_PATH = ROOT / "configs/experiments/s2_s200_independent_auditor_v1.yaml"


def _write_json(path: Path, value: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = s201.canonical_json_bytes(value, pretty=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _binding(value: dict[str, Any]) -> str:
    body = {key: nested for key, nested in value.items() if key != "binding_sha256"}
    return hashlib.sha256(s201.canonical_json_bytes(body, pretty=False)).hexdigest()


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _copy_static_workspace(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    paths = {pin["path"] for pin in config["canonical_sources"]["pins"]}
    s200_config = yaml.safe_load(S200_CONFIG_PATH.read_text(encoding="utf-8"))
    paths.update(pin["path"] for pin in s200_config["canonical_sources"]["pins"])
    for relative in sorted(paths):
        _copy_file(ROOT / relative, tmp_path / relative)
    target_config = tmp_path / CONFIG_PATH.relative_to(ROOT)
    _copy_file(CONFIG_PATH, target_config)
    return target_config, config


def _save_config(path: Path, config: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _create_s108(
    tmp_path: Path,
    config: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    contract = config["predecessors"]["s108_final"]
    artifact_hashes: dict[str, str] = {}
    artifacts: list[dict[str, Any]] = []
    for name in contract["required_artifact_names"]:
        relative = f"{contract['artifact_root']}/{name}.bin"
        data = f"immutable:{name}".encode()
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        artifact_hashes[name] = digest
        artifacts.append(
            {
                "name": name,
                "kind": "synthetic_predecessor",
                "path": relative,
                "sha256": digest,
                "size_bytes": len(data),
            }
        )
    registry = {
        "schema_version": 1,
        "run_id": "aihub71490-wavlm-s108-final-s1-v1",
        "status": "completed",
        "failure": None,
        "artifacts": artifacts,
    }
    registry_sha = _write_json(
        tmp_path / contract["registry_path"],
        registry,
    )
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
            "mode": "predicted_alignment",
            "oracle_only": False,
            "predicted_alignment_evaluated": True,
            "deployment_claim_allowed": False,
        },
        "artifact_hashes": artifact_hashes,
        "registry": {
            "path": contract["registry_path"],
            "sha256": registry_sha,
            "run_id": "aihub71490-wavlm-s108-final-s1-v1",
            "status": "completed",
        },
    }
    commit["binding_sha256"] = _binding(commit)
    commit_sha = _write_json(tmp_path / contract["commit_path"], commit)
    return commit_sha, commit


def _create_s200_external(
    tmp_path: Path,
    *,
    s108_sha: str,
) -> None:
    s200_config = yaml.safe_load(
        (tmp_path / S200_CONFIG_PATH.relative_to(ROOT)).read_text(encoding="utf-8")
    )
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
        "overlap_counts": {
            "s1_train_items": 0,
            "s1_dev_items": 0,
            "s1_stack_items": 0,
            "s1_calibration_items": 0,
            "s1_locked_test_items": 0,
            "s1_selection_speakers": 0,
            "s1_labeler_cohort": 0,
        },
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
        "s108_commit_sha256": s108_sha,
        "split_hashes": {
            "s2_auditor_train": "1" * 64,
            "s2_auditor_validation": "2" * 64,
            "s2_expert_holdout": "3" * 64,
        },
        "rater_cohort_hashes": {
            "s1_labeler_cohort": "4" * 64,
            "s2_labeler_cohort": "5" * 64,
        },
    }
    record["binding_sha256"] = _binding(record)
    _write_json(
        tmp_path / s200_config["s203"]["expert_holdout_attestation"]["path"],
        record,
    )


def _publish_s200_lock(
    tmp_path: Path,
    *,
    s108_sha: str,
) -> tuple[str, dict[str, Any]]:
    _create_s200_external(tmp_path, s108_sha=s108_sha)
    config_path = tmp_path / S200_CONFIG_PATH.relative_to(ROOT)
    config = s200.load_config(config_path)
    result = s200.run_full_preflight(
        workspace_root=tmp_path,
        config_path=config_path,
        config=config,
    )
    return result.readiness_lock_sha256, dict(result.readiness_lock)


def _predecessors_ready(
    tmp_path: Path,
    config: dict[str, Any],
) -> tuple[str, str]:
    s108_sha, _ = _create_s108(tmp_path, config)
    s200_sha, _ = _publish_s200_lock(tmp_path, s108_sha=s108_sha)
    return s108_sha, s200_sha


def _freeze_thresholds(
    tmp_path: Path,
    config: dict[str, Any],
    *,
    s108_sha: str,
    s200_sha: str,
) -> None:
    record = {
        "schema_version": "voice_coach.s2_s201_threshold_preregistration.v1",
        "status": "frozen",
        "results_seen_before_freeze": False,
        "data_paths_resolved_before_freeze": False,
        "model_path_resolved_before_freeze": False,
        "native_external_benchmark_selected_threshold": False,
        "s108_commit_sha256": s108_sha,
        "s200_readiness_lock_sha256": s200_sha,
        "thresholds": {
            "wer": {"operator": "less_than_or_equal", "value": 0.2},
            "cer": {"operator": "less_than_or_equal", "value": 0.1},
            "content_mismatch": {
                "rule": "fail_if_wer_or_cer_exceeds_its_frozen_threshold",
                "bypass_allowed": False,
            },
        },
    }
    record["binding_sha256"] = _binding(record)
    prereg = config["hard_gate"]["threshold_preregistration"]
    prereg["status"] = "frozen"
    prereg["execution_allowed"] = True
    prereg["sha256"] = _write_json(tmp_path / prereg["path"], record)
    config["hard_gate"]["metrics"]["wer"]["threshold"] = 0.2
    config["hard_gate"]["metrics"]["cer"]["threshold"] = 0.1
    config["hard_gate"]["metrics"]["content_mismatch"] = {
        "formula": "fail_if_wer_or_cer_exceeds_its_frozen_threshold",
        "operator": "boolean_equals",
        "threshold": False,
    }


def _create_data_attestation(
    tmp_path: Path,
    *,
    role_name: str,
    contract: dict[str, Any],
    s108_sha: str,
    s200_sha: str,
) -> None:
    manifest_path = f"{contract['manifest_root']}/{role_name}.manifest"
    manifest_data = f"opaque-manifest:{role_name}".encode()
    manifest = tmp_path / manifest_path
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_bytes(manifest_data)
    record: dict[str, Any] = {
        "schema_version": "voice_coach.s2_s201_data_attestation.v1",
        "status": "ready",
        "dataset_id": role_name,
        "aggregate_only": True,
        "contains_rows": False,
        "contains_audio": False,
        "contains_transcripts": False,
        "contains_s1_predictions": False,
        "contains_oracle_alignment": False,
        "contains_raw_identifiers": False,
        "s108_commit_sha256": s108_sha,
        "s200_readiness_lock_sha256": s200_sha,
        "partition_policy": contract["partition_policy"],
        "partition_hashes": {
            "synthetic_partition": hashlib.sha256(
                f"partition:{role_name}".encode()
            ).hexdigest()
        },
        "manifest": {
            "path": manifest_path,
            "sha256": hashlib.sha256(manifest_data).hexdigest(),
            "item_count": 3,
        },
    }
    if role_name == "s2_expert_content":
        record.update(
            {
                "role": "l2_content_gate_reference_and_evaluation",
                "independent_from_s1": True,
                "fine_phone_or_prosody_labels": False,
                "license": "separate_consent_and_institution_policy",
                "overlap_counts": {
                    "s1_train_items": 0,
                    "s1_dev_items": 0,
                    "s1_stack_items": 0,
                    "s1_calibration_items": 0,
                    "s1_locked_test_items": 0,
                    "s1_selection_speakers": 0,
                    "s1_labeler_cohort": 0,
                },
                "governance": {
                    "institutional_approval_complete": True,
                    "consent_complete": True,
                    "withdrawal_plan_complete": True,
                    "retention_and_destruction_plan_complete": True,
                    "restricted_redistribution": True,
                },
            }
        )
    else:
        record.update(
            {
                "role": "asr_external_evaluation_only",
                "license": "CC-BY-4.0",
                "attribution_complete": True,
                "original_partition_preserved": True,
                "learner_error_training_used": False,
                "s2_expert_transcript_substitute_used": False,
                "license_audit_completed": True,
            }
        )
    record["binding_sha256"] = _binding(record)
    contract["attestation_status"] = "ready"
    contract["attestation_sha256"] = _write_json(
        tmp_path / contract["attestation_path"],
        record,
    )


def _ready_data(
    tmp_path: Path,
    config: dict[str, Any],
    *,
    s108_sha: str,
    s200_sha: str,
) -> None:
    roles = config["data_roles"]
    _create_data_attestation(
        tmp_path,
        role_name="fleurs_ko_kr",
        contract=roles["native_external_asr"]["fleurs_ko_kr"],
        s108_sha=s108_sha,
        s200_sha=s200_sha,
    )
    _create_data_attestation(
        tmp_path,
        role_name="zeroth_korean",
        contract=roles["native_external_asr"]["zeroth_korean"],
        s108_sha=s108_sha,
        s200_sha=s200_sha,
    )
    _create_data_attestation(
        tmp_path,
        role_name="s2_expert_content",
        contract=roles["s2_expert_content"],
        s108_sha=s108_sha,
        s200_sha=s200_sha,
    )


def _ready_model(
    tmp_path: Path,
    config: dict[str, Any],
    *,
    s108_sha: str,
    s200_sha: str,
) -> None:
    offline = config["model"]["offline_only"]
    bundle_root = offline["bundle_root"]
    files: list[dict[str, Any]] = []
    for name, kind in (
        ("model-00001-of-00001.safetensors", "model_weight"),
        ("config.json", "model_config"),
        ("generation_config.json", "generation_config"),
        ("preprocessor_config.json", "preprocessor_config"),
        ("tokenizer.json", "tokenizer"),
    ):
        relative = f"{bundle_root}/{name}"
        data = f"synthetic:{kind}".encode()
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files.append(
            {
                "path": relative,
                "kind": kind,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
            }
        )
    identity = config["model"]["identity"]
    record = {
        "schema_version": "voice_coach.s2_s201_model_bundle_attestation.v1",
        "status": "ready",
        "model_id": identity["model_id"],
        "revision": identity["revision"],
        "processor_id": identity["model_id"],
        "processor_revision": identity["revision"],
        "local_files_only": True,
        "preflight_network_access_count": 0,
        "preflight_download_count": 0,
        "license": identity["license"],
        "license_sha256": identity["license_sha256"],
        "s108_commit_sha256": s108_sha,
        "s200_readiness_lock_sha256": s200_sha,
        "bundle_root": bundle_root,
        "files": files,
    }
    record["binding_sha256"] = _binding(record)
    offline["attestation_status"] = "ready"
    offline["attestation_sha256"] = _write_json(
        tmp_path / offline["attestation_path"],
        record,
    )


def _fully_ready_workspace(
    tmp_path: Path,
) -> tuple[Path, dict[str, Any]]:
    config_path, config = _copy_static_workspace(tmp_path)
    s108_sha, s200_sha = _predecessors_ready(tmp_path, config)
    _freeze_thresholds(
        tmp_path,
        config,
        s108_sha=s108_sha,
        s200_sha=s200_sha,
    )
    _ready_data(
        tmp_path,
        config,
        s108_sha=s108_sha,
        s200_sha=s200_sha,
    )
    _ready_model(
        tmp_path,
        config,
        s108_sha=s108_sha,
        s200_sha=s200_sha,
    )
    _save_config(config_path, config)
    return config_path, config


def test_repository_config_only_has_zero_runtime_access() -> None:
    config = s201.load_config(CONFIG_PATH)
    static = s201.validate_static_config(
        workspace_root=ROOT,
        config_path=CONFIG_PATH,
        config=config,
    )
    result = s201.config_only_result(static)
    assert result["status"] == "valid_config_only"
    assert result["source_pin_read_count"] == 10
    for field in (
        "predecessor_metadata_read_attempt_count",
        "external_attestation_read_attempt_count",
        "data_path_resolution_count",
        "data_manifest_read_count",
        "model_path_resolution_count",
        "model_download_count",
        "protected_data_read_count",
        "locked_test_read_count",
        "gpu_access_count",
        "metric_count",
        "result_count",
        "output_path_resolution_count",
        "output_count",
    ):
        assert result[field] == 0


def test_missing_s108_fails_before_s200_data_model_or_output(
    tmp_path: Path,
) -> None:
    config_path, config = _copy_static_workspace(tmp_path)
    with pytest.raises(s201.S201ContractError) as raised:
        s201.run_full_preflight(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )
    audit = raised.value.audit
    assert audit is not None
    assert raised.value.stage == "predecessor_validation"
    assert audit.predecessor_metadata_read_attempt_count == 1
    assert audit.external_attestation_read_attempt_count == 0
    assert audit.data_path_resolution_count == 0
    assert audit.model_path_resolution_count == 0
    assert audit.output_path_resolution_count == 0


def test_ready_predecessors_stop_at_tbd_threshold_before_external_or_model(
    tmp_path: Path,
) -> None:
    config_path, config = _copy_static_workspace(tmp_path)
    _predecessors_ready(tmp_path, config)
    with pytest.raises(
        s201.S201ContractError,
        match="TBD_PREREGISTRATION",
    ) as raised:
        s201.run_full_preflight(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )
    audit = raised.value.audit
    assert audit is not None
    assert raised.value.stage == "threshold_preregistration"
    assert audit.threshold_attestation_read_attempt_count == 0
    assert audit.external_attestation_read_attempt_count == 0
    assert audit.data_path_resolution_count == 0
    assert audit.model_path_resolution_count == 0


def test_missing_license_attestation_stops_before_model_or_data_manifest(
    tmp_path: Path,
) -> None:
    config_path, config = _copy_static_workspace(tmp_path)
    s108_sha, s200_sha = _predecessors_ready(tmp_path, config)
    _freeze_thresholds(
        tmp_path,
        config,
        s108_sha=s108_sha,
        s200_sha=s200_sha,
    )
    _save_config(config_path, config)
    with pytest.raises(
        s201.S201ContractError, match="attestation is not ready"
    ) as raised:
        s201.run_full_preflight(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )
    audit = raised.value.audit
    assert audit is not None
    assert audit.external_attestation_read_attempt_count == 0
    assert audit.model_path_resolution_count == 0
    assert audit.data_path_resolution_count == 0


def test_missing_local_bundle_remains_offline_and_stops_before_manifests(
    tmp_path: Path,
) -> None:
    config_path, config = _copy_static_workspace(tmp_path)
    s108_sha, s200_sha = _predecessors_ready(tmp_path, config)
    _freeze_thresholds(
        tmp_path,
        config,
        s108_sha=s108_sha,
        s200_sha=s200_sha,
    )
    _ready_data(
        tmp_path,
        config,
        s108_sha=s108_sha,
        s200_sha=s200_sha,
    )
    _save_config(config_path, config)
    with pytest.raises(s201.S201ContractError, match="local-only") as raised:
        s201.run_full_preflight(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )
    audit = raised.value.audit
    assert audit is not None
    assert audit.external_attestation_read_count == 3
    assert audit.model_path_resolution_count == 0
    assert audit.model_download_count == 0
    assert audit.data_path_resolution_count == 0


def test_fully_synthetic_preflight_hashes_inputs_without_rows_or_results(
    tmp_path: Path,
) -> None:
    config_path, config = _fully_ready_workspace(tmp_path)
    result = s201.run_full_preflight(
        workspace_root=tmp_path,
        config_path=config_path,
        config=config,
    )
    assert result.status == "ready_for_separately_reviewed_runner_implementation"
    assert set(result.manifest_hashes) == {
        "fleurs_ko_kr",
        "zeroth_korean",
        "s2_expert_content",
    }
    assert result.audit["predecessor_artifact_read_count"] == 16
    assert result.audit["model_weight_read_count"] == 2
    assert result.audit["data_manifest_read_count"] == 6
    for field in (
        "data_row_read_count",
        "audio_read_count",
        "transcript_row_read_count",
        "model_download_count",
        "protected_data_read_count",
        "s1_prediction_read_count",
        "oracle_alignment_read_count",
        "locked_test_read_count",
        "gpu_access_count",
        "metric_count",
        "prediction_count",
        "result_count",
        "output_path_resolution_count",
        "output_count",
    ):
        assert result.audit[field] == 0


def test_s108_artifact_hash_tamper_fails_before_s200_lock(
    tmp_path: Path,
) -> None:
    config_path, config = _copy_static_workspace(tmp_path)
    _create_s108(tmp_path, config)
    artifact = (
        tmp_path
        / config["predecessors"]["s108_final"]["artifact_root"]
        / "final_s1_config.bin"
    )
    artifact.write_bytes(b"tampered")
    with pytest.raises(s201.S201ContractError, match="hash"):
        s201.run_full_preflight(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )


def test_s108_registry_cannot_redirect_artifact_read_into_data_zone(
    tmp_path: Path,
) -> None:
    config_path, config = _copy_static_workspace(tmp_path)
    _, commit = _create_s108(tmp_path, config)
    contract = config["predecessors"]["s108_final"]
    registry_path = tmp_path / contract["registry_path"]
    registry = dict(s201._parse_json(registry_path.read_bytes(), "registry"))  # noqa: SLF001
    artifacts = [dict(item) for item in registry["artifacts"]]
    redirected = tmp_path / "data/forbidden/final_s1_config.bin"
    redirected.parent.mkdir(parents=True, exist_ok=True)
    redirected.write_bytes(b"immutable:final_s1_config")
    artifacts[0]["path"] = redirected.relative_to(tmp_path).as_posix()
    registry["artifacts"] = artifacts
    registry_sha = _write_json(registry_path, registry)
    commit["registry"]["sha256"] = registry_sha
    commit["binding_sha256"] = _binding(commit)
    _write_json(tmp_path / contract["commit_path"], commit)
    with pytest.raises(s201.S201ContractError, match="artifact root") as raised:
        s201.run_full_preflight(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )
    audit = raised.value.audit
    assert audit is not None
    assert audit.data_path_resolution_count == 0
    assert audit.model_path_resolution_count == 0


def test_s200_lock_tamper_is_rejected_before_threshold(
    tmp_path: Path,
) -> None:
    config_path, config = _copy_static_workspace(tmp_path)
    _predecessors_ready(tmp_path, config)
    lock_path = tmp_path / config["predecessors"]["s200_readiness_lock"]["lock_path"]
    lock = s201._parse_json(lock_path.read_bytes(), "lock")  # noqa: SLF001
    changed = dict(lock)
    changed["metric_count"] = 1
    changed["binding_sha256"] = _binding(changed)
    _write_json(lock_path, changed)
    with pytest.raises(s201.S201ContractError, match="metric_count"):
        s201.run_full_preflight(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )


def test_source_symlink_is_rejected_in_config_only(tmp_path: Path) -> None:
    config_path, config = _copy_static_workspace(tmp_path)
    relative = config["canonical_sources"]["pins"][0]["path"]
    source = tmp_path / relative
    replacement = tmp_path / "replacement"
    replacement.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(replacement)
    with pytest.raises(s201.S201ContractError, match="symlink"):
        s201.validate_static_config(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )


def test_mutable_checkpoint_revision_is_rejected(tmp_path: Path) -> None:
    config_path, config = _copy_static_workspace(tmp_path)
    config["model"]["identity"]["revision"] = "main"
    config["model"]["preprocessor"]["revision"] = "main"
    _save_config(config_path, config)
    with pytest.raises(s201.S201ContractError, match="exact 40-hex"):
        s201.validate_static_config(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )


def test_normalization_reordering_is_rejected(tmp_path: Path) -> None:
    config_path, config = _copy_static_workspace(tmp_path)
    steps = config["transcript_normalization"]["ordered_steps"]
    steps[1], steps[2] = steps[2], steps[1]
    _save_config(config_path, config)
    with pytest.raises(s201.S201ContractError, match="normalization steps"):
        s201.validate_static_config(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )


def test_enabling_s1_predictions_or_phone_role_is_rejected(
    tmp_path: Path,
) -> None:
    config_path, config = _copy_static_workspace(tmp_path)
    config["independence"]["s1_predictions_allowed"] = True
    config["hard_gate"]["fine_phone_judgment_allowed"] = True
    _save_config(config_path, config)
    with pytest.raises(s201.S201ContractError, match="phone role"):
        s201.validate_static_config(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )


def test_external_benchmark_cannot_become_training_or_expert_substitute(
    tmp_path: Path,
) -> None:
    config_path, config = _copy_static_workspace(tmp_path)
    fleurs = config["data_roles"]["native_external_asr"]["fleurs_ko_kr"]
    fleurs["learner_error_training_allowed"] = True
    fleurs["s2_expert_transcript_substitute_allowed"] = True
    _save_config(config_path, config)
    with pytest.raises(s201.S201ContractError, match="training"):
        s201.validate_static_config(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )


def test_model_bundle_symlink_is_rejected(tmp_path: Path) -> None:
    config_path, config = _fully_ready_workspace(tmp_path)
    bundle_root = config["model"]["offline_only"]["bundle_root"]
    weight = tmp_path / bundle_root / "model-00001-of-00001.safetensors"
    data = weight.read_bytes()
    target = tmp_path / "outside-weight"
    target.write_bytes(data)
    weight.unlink()
    weight.symlink_to(target)
    with pytest.raises(s201.S201ContractError, match="symlink"):
        s201.run_full_preflight(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )


def test_final_revalidation_detects_manifest_toctou(tmp_path: Path) -> None:
    config_path, config = _fully_ready_workspace(tmp_path)
    manifest = (
        tmp_path
        / config["data_roles"]["native_external_asr"]["fleurs_ko_kr"]["manifest_root"]
        / "fleurs_ko_kr.manifest"
    )

    def mutate() -> None:
        manifest.write_bytes(manifest.read_bytes() + b"-changed")

    with pytest.raises(s201.S201ContractError, match="changed"):
        s201.run_full_preflight(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
            before_final_verify_hook=mutate,
        )
