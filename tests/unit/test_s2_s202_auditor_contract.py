from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from voice_coach.training import s2_s201_whisper_contract as s201
from voice_coach.training import s2_s202_auditor_contract as contract


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs/experiments/s2_s202_independent_auditor_v1.yaml"


def _repository_config() -> dict[str, object]:
    return dict(contract.load_config(CONFIG_PATH))


def test_repository_static_config_is_valid_and_runtime_access_is_zero() -> None:
    static = contract.validate_static_config(
        workspace_root=ROOT,
        config_path=CONFIG_PATH,
        config=_repository_config(),
    )
    result = contract.config_only_result(static)
    assert result["status"] == "valid_config_only"
    assert result["source_pin_read_count"] == 9
    for name, value in result.items():
        if name.endswith("_count") and name != "source_pin_read_count":
            assert value == 0
    assert result["production_trainer_implemented"] is False
    assert result["execution_allowed"] is False
    assert result["completion_claim_allowed"] is False


@pytest.mark.parametrize(
    ("section", "field", "replacement"),
    [
        ("independence", "s1_predictions_allowed", True),
        ("independence", "oracle_alignment_allowed", True),
        ("independence", "aihub_silver_labels_allowed", True),
        ("independence", "whisper_asr_score_as_feature_or_target_allowed", True),
        ("training_protocol", "inner_selection_metric", "pairwise_accuracy"),
        ("evaluation", "primary_metric", "pairwise_accuracy"),
        ("representation_model", "selection", {"status": "ready"}),
    ],
)
def test_static_policy_rejects_leakage_or_unapproved_choices(
    section: str,
    field: str,
    replacement: object,
) -> None:
    config = copy.deepcopy(_repository_config())
    nested = config[section]
    assert isinstance(nested, dict)
    nested[field] = replacement
    with pytest.raises(contract.S202ContractError):
        contract.validate_static_config(
            workspace_root=ROOT,
            config_path=CONFIG_PATH,
            config=config,
        )


def test_static_policy_rejects_source_hash_drift() -> None:
    config = copy.deepcopy(_repository_config())
    sources = config["canonical_sources"]
    assert isinstance(sources, dict)
    pins = sources["pins"]
    assert isinstance(pins, list)
    assert isinstance(pins[0], dict)
    pins[0]["sha256"] = "0" * 64
    with pytest.raises(contract.S202ContractError, match="source pin"):
        contract.validate_static_config(
            workspace_root=ROOT,
            config_path=CONFIG_PATH,
            config=config,
        )


def test_static_policy_rejects_predecessor_path_traversal() -> None:
    config = copy.deepcopy(_repository_config())
    predecessors = config["predecessors"]
    assert isinstance(predecessors, dict)
    s201_predecessor = predecessors["s201_content_gate"]
    assert isinstance(s201_predecessor, dict)
    s201_predecessor["commit_path"] = "../commit.json"
    with pytest.raises(contract.S202ContractError, match="relative path"):
        contract.validate_static_config(
            workspace_root=ROOT,
            config_path=CONFIG_PATH,
            config=config,
        )


def test_load_config_rejects_duplicate_yaml_key(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.yaml"
    path.write_text("schema_version: 1\nschema_version: 1\n", encoding="utf-8")
    with pytest.raises(contract.S202ContractError, match="duplicate key"):
        contract.load_config(path)


def test_load_config_and_snapshot_reject_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target.yaml"
    target.write_text("schema_version: 1\n", encoding="utf-8")
    link = tmp_path / "link.yaml"
    link.symlink_to(target.name)
    with pytest.raises(contract.S202ContractError, match="symlink"):
        contract.load_config(link)
    with pytest.raises(contract.S202ContractError, match="symlink"):
        contract._snapshot_file(tmp_path, "link.yaml", "fixture")  # noqa: SLF001


def test_snapshot_detects_hash_and_identity_toctou(tmp_path: Path) -> None:
    path = tmp_path / "source.txt"
    path.write_text("before", encoding="utf-8")
    snapshot = contract._snapshot_file(  # noqa: SLF001
        tmp_path,
        "source.txt",
        "source",
    )
    path.write_text("after", encoding="utf-8")
    with pytest.raises(contract.S202ContractError, match="changed"):
        contract._verify_snapshot(tmp_path, snapshot, "source")  # noqa: SLF001


def test_full_preflight_fails_at_s108_before_all_runtime_access() -> None:
    with pytest.raises(contract.S202ContractError) as captured:
        contract.run_full_preflight(
            workspace_root=ROOT,
            config_path=CONFIG_PATH,
            config=_repository_config(),
        )
    error = captured.value
    assert error.stage == "predecessor_s108"
    assert error.audit is not None
    audit = error.audit.as_dict()
    assert audit["predecessor_metadata_read_attempt_count"] == 1
    assert audit["s108_validation_count"] == 0
    assert audit["s200_validation_count"] == 0
    assert audit["s201_validation_count"] == 0
    for field in (
        "preregistration_read_attempt_count",
        "external_attestation_read_attempt_count",
        "data_path_resolution_count",
        "data_manifest_read_count",
        "data_row_read_count",
        "audio_read_count",
        "model_path_resolution_count",
        "model_metadata_read_count",
        "model_weight_read_count",
        "model_download_count",
        "protected_data_read_count",
        "expert_holdout_read_count",
        "s1_prediction_read_count",
        "oracle_alignment_read_count",
        "silver_label_read_count",
        "whisper_asr_score_read_count",
        "locked_test_read_count",
        "gpu_access_count",
        "metric_count",
        "prediction_count",
        "checkpoint_count",
        "result_count",
        "output_path_resolution_count",
        "output_count",
    ):
        assert audit[field] == 0


def _write_json(path: Path, value: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _snapshot(path: str, sha256: str = "1" * 64) -> s201.FileSnapshot:
    return s201.FileSnapshot(
        path=path,
        sha256=sha256,
        size_bytes=1,
        device=1,
        inode=1,
        modified_ns=1,
    )


def _synthetic_s201_fixture(
    tmp_path: Path,
) -> tuple[
    dict[str, object],
    contract.S108Evidence,
    contract.S200Evidence,
    contract.AccessAudit,
]:
    root_name = "artifacts/s201"
    artifacts: list[dict[str, object]] = []
    hashes: dict[str, str] = {}
    config_hash = ""
    for name in contract.S201_ARTIFACT_NAMES:
        relative = f"{root_name}/{name}.json"
        digest = _write_json(tmp_path / relative, {"name": name, "status": "passed"})
        if name == "s201_config":
            config_hash = digest
        hashes[name] = digest
        artifacts.append(
            {
                "name": name,
                "path": relative,
                "sha256": digest,
                "size_bytes": (tmp_path / relative).stat().st_size,
            }
        )
    registry_path = "artifacts/registry/s201.json"
    registry_hash = _write_json(
        tmp_path / registry_path,
        {
            "schema_version": 1,
            "run_id": contract.S201_REGISTRY_RUN_ID,
            "status": "completed",
            "failure": None,
            "artifacts": artifacts,
        },
    )
    s108_inner = s201.S108Evidence(
        commit_snapshot=_snapshot("s108/commit.json", "2" * 64),
        registry_snapshot=_snapshot("s108/registry.json", "3" * 64),
        commit_binding_sha256="4" * 64,
        artifact_snapshots=(),
    )
    s108 = contract.S108Evidence(
        inner=s108_inner,
        s201_config={},
        s201_static=s201.StaticValidation(
            config_sha256=config_hash,
            source_snapshots=(),
            policy_sha256="5" * 64,
        ),
    )
    s200 = contract.S200Evidence(
        inner=s201.S200Evidence(
            lock_snapshot=_snapshot("s200/lock.json", "6" * 64),
            lock_binding_sha256="7" * 64,
            s200_config_sha256="8" * 64,
        )
    )
    commit_path = "artifacts/s201/commit.json"
    commit: dict[str, object] = {
        "schema_version": contract.S201_COMMIT_SCHEMA,
        "status": "committed",
        "decision": "content_hard_gate_passed",
        "content_hard_gate_completed": True,
        "production_runner_implemented": True,
        "hard_gate_bypassed": False,
        "failed_content_rescued_by_s202": False,
        "fine_phone_or_prosody_role": False,
        "content_gate_evaluation_count": 1,
        "metric_count": 3,
        "prediction_count": 10,
        "threshold_preregistration_sha256": "9" * 64,
        "model_bundle_attestation_sha256": "a" * 64,
        "data_attestation_hashes": {
            "fleurs_ko_kr": "b" * 64,
            "zeroth_korean": "c" * 64,
            "s2_expert_content": "d" * 64,
        },
        "s1_prediction_read_count": 0,
        "oracle_alignment_read_count": 0,
        "whisper_timestamp_feature_count": 0,
        "locked_test_read_count": 0,
        "result_based_reselection_count": 0,
        "predecessors": {
            "s108_commit_sha256": "2" * 64,
            "s200_readiness_lock_sha256": "6" * 64,
            "s201_config_sha256": config_hash,
        },
        "artifact_hashes": hashes,
        "registry": {
            "path": registry_path,
            "sha256": registry_hash,
            "run_id": contract.S201_REGISTRY_RUN_ID,
            "status": "completed",
        },
    }
    commit["binding_sha256"] = contract._binding_sha256(commit)  # noqa: SLF001
    _write_json(tmp_path / commit_path, commit)
    config: dict[str, object] = {
        "predecessors": {
            "s201_content_gate": {
                "commit_path": commit_path,
                "registry_path": registry_path,
                "artifact_root": root_name,
            }
        }
    }
    return config, s108, s200, contract.AccessAudit()


def test_s201_deep_validator_accepts_exact_commit_then_rejects_artifact_tamper(
    tmp_path: Path,
) -> None:
    config, s108, s200, audit = _synthetic_s201_fixture(tmp_path)
    evidence = contract.deep_validate_s201_completed(
        root=tmp_path,
        config=config,
        s108=s108,
        s200=s200,
        audit=audit,
    )
    assert evidence.commit_snapshot.sha256
    assert audit.s201_validation_count == 1
    assert len(evidence.artifact_snapshots) == len(contract.S201_ARTIFACT_NAMES)

    target = tmp_path / "artifacts/s201/aggregate_wer_cer_content_report.json"
    target.write_text('{"tampered":true}', encoding="utf-8")
    with pytest.raises(contract.S202ContractError, match="hash"):
        contract.deep_validate_s201_completed(
            root=tmp_path,
            config=config,
            s108=s108,
            s200=s200,
            audit=contract.AccessAudit(),
        )


def test_s201_deep_validator_rejects_registry_symlink(tmp_path: Path) -> None:
    config, s108, s200, _ = _synthetic_s201_fixture(tmp_path)
    registry = tmp_path / "artifacts/registry/s201.json"
    target = tmp_path / "artifacts/registry/target.json"
    registry.rename(target)
    registry.symlink_to(target.name)
    with pytest.raises(contract.S202ContractError, match="symlink"):
        contract.deep_validate_s201_completed(
            root=tmp_path,
            config=config,
            s108=s108,
            s200=s200,
            audit=contract.AccessAudit(),
        )
