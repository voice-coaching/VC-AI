from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from voice_coach.training import s2_s201_whisper_contract as s201
from voice_coach.training import s2_s202_auditor_contract as s202
from voice_coach.training import s2_s203_holdout_contract as contract


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs/experiments/s2_s203_expert_holdout_audit_v1.yaml"


def _repository_config() -> dict[str, object]:
    return copy.deepcopy(dict(contract.load_config(CONFIG_PATH)))


def test_repository_static_config_is_valid_and_runtime_access_is_zero() -> None:
    static = contract.validate_static_config(
        workspace_root=ROOT,
        config_path=CONFIG_PATH,
        config=_repository_config(),
    )
    result = contract.config_only_result(static)
    assert result["status"] == "valid_config_only"
    assert result["source_pin_read_count"] == 13
    for name, value in result.items():
        if name.endswith("_count") and name != "source_pin_read_count":
            assert value == 0
    assert result["production_evaluator_implemented"] is False
    assert result["holdout_access_allowed"] is False
    assert result["metric_generation_allowed"] is False
    assert result["completion_claim_allowed"] is False


def test_static_package_has_no_output_and_requires_future_no_overwrite() -> None:
    config = _repository_config()
    outputs = config["outputs"]
    assert isinstance(outputs, dict)
    assert outputs["current_output_count"] == 0
    assert outputs["output_path_resolution_allowed"] is False
    assert outputs["atomic_publish_implemented"] is False
    assert outputs["future_atomic_publish_required"] is True
    assert outputs["future_no_overwrite_required"] is True
    assert outputs["future_no_symlink_required"] is True


@pytest.mark.parametrize(
    ("section", "field", "replacement"),
    [
        ("frozen_evaluation_inputs", "s1_retraining_allowed", True),
        ("frozen_evaluation_inputs", "s2_reselection_allowed", True),
        (
            "frozen_evaluation_inputs",
            "predictions_allowed_as_training_features",
            True,
        ),
        (
            "frozen_evaluation_inputs",
            "locked_test_row_or_prediction_reuse_allowed",
            True,
        ),
        ("expert_holdout", "maximum_access_count", 2),
        ("expert_holdout", "data_manifest_path", "private/holdout.jsonl"),
        ("d07_separation", "d07_rows_may_be_reused", True),
        ("outputs", "output_path_resolution_allowed", True),
    ],
)
def test_static_policy_rejects_leakage_or_early_execution(
    section: str,
    field: str,
    replacement: object,
) -> None:
    config = _repository_config()
    nested = config[section]
    assert isinstance(nested, dict)
    nested[field] = replacement
    with pytest.raises(contract.S203ContractError):
        contract.validate_static_config(
            workspace_root=ROOT,
            config_path=CONFIG_PATH,
            config=config,
        )


@pytest.mark.parametrize(
    ("metric", "operator", "value"),
    [
        ("serious_s1_misjudgment_recall", "greater_than_or_equal", 0.79),
        ("false_high_score_relative_reduction", "greater_than_or_equal", 0.31),
        ("normal_sample_referral_rate", "less_than", 0.15),
        ("disagreement_expert_error_hit_rate", "greater_than_or_equal", 0.61),
    ],
)
def test_static_policy_rejects_undocumented_gate_changes(
    metric: str,
    operator: str,
    value: float,
) -> None:
    config = _repository_config()
    evaluation = config["evaluation_contract"]
    assert isinstance(evaluation, dict)
    gates = evaluation["documented_primary_gates"]
    assert isinstance(gates, dict)
    selected = gates[metric]
    assert isinstance(selected, dict)
    gate = selected["gate"]
    assert isinstance(gate, dict)
    gate["operator"] = operator
    gate["value"] = value
    with pytest.raises(contract.S203ContractError):
        contract.validate_static_config(
            workspace_root=ROOT,
            config_path=CONFIG_PATH,
            config=config,
        )


def test_static_policy_requires_every_undocumented_choice_to_remain_tbd() -> None:
    config = _repository_config()
    evaluation = config["evaluation_contract"]
    assert isinstance(evaluation, dict)
    unresolved = evaluation["unresolved_preregistration"]
    assert isinstance(unresolved, dict)
    for field in contract.UNRESOLVED_PREREGISTRATION_FIELDS:
        changed = copy.deepcopy(config)
        changed_evaluation = changed["evaluation_contract"]
        assert isinstance(changed_evaluation, dict)
        changed_unresolved = changed_evaluation["unresolved_preregistration"]
        assert isinstance(changed_unresolved, dict)
        changed_unresolved[field] = "invented_policy"
        with pytest.raises(contract.S203ContractError):
            contract.validate_static_config(
                workspace_root=ROOT,
                config_path=CONFIG_PATH,
                config=changed,
            )


def test_static_policy_rejects_source_hash_drift_and_path_traversal() -> None:
    config = _repository_config()
    sources = config["canonical_sources"]
    assert isinstance(sources, dict)
    pins = sources["pins"]
    assert isinstance(pins, list)
    assert isinstance(pins[0], dict)
    pins[0]["sha256"] = "0" * 64
    with pytest.raises(contract.S203ContractError, match="source pin"):
        contract.validate_static_config(
            workspace_root=ROOT,
            config_path=CONFIG_PATH,
            config=config,
        )

    config = _repository_config()
    predecessors = config["predecessors"]
    assert isinstance(predecessors, dict)
    s202_contract = predecessors["s202_auditor"]
    assert isinstance(s202_contract, dict)
    s202_contract["aggregate_commit_path"] = "../commit.json"
    with pytest.raises(contract.S203ContractError, match="relative path"):
        contract.validate_static_config(
            workspace_root=ROOT,
            config_path=CONFIG_PATH,
            config=config,
        )


def test_load_config_rejects_duplicate_key_and_symlink(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text("schema_version: 1\nschema_version: 1\n", encoding="utf-8")
    with pytest.raises(contract.S203ContractError, match="duplicate key"):
        contract.load_config(duplicate)
    target = tmp_path / "target.yaml"
    target.write_text("schema_version: 1\n", encoding="utf-8")
    link = tmp_path / "link.yaml"
    link.symlink_to(target.name)
    with pytest.raises(contract.S203ContractError, match="symlink"):
        contract.load_config(link)
    with pytest.raises(contract.S203ContractError, match="symlink"):
        contract._snapshot_file(tmp_path, "link.yaml", "fixture")  # noqa: SLF001


def test_snapshot_detects_hash_and_identity_toctou(tmp_path: Path) -> None:
    path = tmp_path / "source.txt"
    path.write_text("before", encoding="utf-8")
    snapshot = contract._snapshot_file(tmp_path, "source.txt", "source")  # noqa: SLF001
    path.write_text("after", encoding="utf-8")
    with pytest.raises(contract.S203ContractError, match="changed"):
        contract._verify_snapshot(tmp_path, snapshot, "source")  # noqa: SLF001


def test_repository_full_preflight_fails_at_s108_before_runtime_access() -> None:
    with pytest.raises(contract.S203ContractError) as captured:
        contract.run_full_preflight(
            workspace_root=ROOT,
            config_path=CONFIG_PATH,
            config=_repository_config(),
        )
    error = captured.value
    assert error.stage == "predecessor_s108"
    assert error.audit is not None
    audit = error.audit.as_dict()
    assert audit["predecessor_public_validation_count"] == 1
    assert audit["predecessor_metadata_read_attempt_count"] == 1
    assert audit["s108_validation_count"] == 0
    assert audit["s200_validation_count"] == 0
    assert audit["s201_validation_count"] == 0
    assert audit["s202_public_validation_count"] == 0
    assert audit["s202_deep_validation_count"] == 0
    for field in (
        "preregistration_read_attempt_count",
        "expert_attestation_read_attempt_count",
        "d07_artifact_read_count",
        "data_path_resolution_count",
        "data_manifest_read_count",
        "data_row_read_count",
        "audio_read_count",
        "protected_data_read_count",
        "expert_holdout_read_count",
        "holdout_access_transaction_count",
        "model_path_resolution_count",
        "model_weight_read_count",
        "model_retrain_count",
        "model_reselection_count",
        "s1_prediction_read_count",
        "s2_prediction_read_count",
        "prediction_join_count",
        "locked_test_read_count",
        "gpu_access_count",
        "metric_count",
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


def _snapshot(path: str, digest: str) -> s201.FileSnapshot:
    return s201.FileSnapshot(
        path=path,
        sha256=digest,
        size_bytes=1,
        device=1,
        inode=1,
        modified_ns=1,
    )


def _fake_predecessors(config_hash: str) -> contract.PredecessorEvidence:
    s108 = s202.S108Evidence(
        inner=s201.S108Evidence(
            commit_snapshot=_snapshot("s108/commit.json", "1" * 64),
            registry_snapshot=_snapshot("s108/registry.json", "2" * 64),
            commit_binding_sha256="3" * 64,
            artifact_snapshots=(),
        ),
        s201_config={},
        s201_static=s201.StaticValidation(
            config_sha256="4" * 64,
            source_snapshots=(),
            policy_sha256="5" * 64,
        ),
    )
    s200 = s202.S200Evidence(
        inner=s201.S200Evidence(
            lock_snapshot=_snapshot("s200/lock.json", "6" * 64),
            lock_binding_sha256="7" * 64,
            s200_config_sha256="8" * 64,
        )
    )
    s201_evidence = s202.S201Evidence(
        commit_snapshot=s202.FileSnapshot(
            path="s201/commit.json",
            sha256="9" * 64,
            size_bytes=1,
            device=1,
            inode=1,
            modified_ns=1,
        ),
        registry_snapshot=s202.FileSnapshot(
            path="s201/registry.json",
            sha256="a" * 64,
            size_bytes=1,
            device=1,
            inode=1,
            modified_ns=1,
        ),
        artifact_snapshots=(),
        binding_sha256="b" * 64,
    )
    return contract.PredecessorEvidence(
        s202_config={},
        s202_static=s202.StaticValidation(
            config_sha256=config_hash,
            source_snapshots=(),
            policy_sha256="c" * 64,
        ),
        s108=s108,
        s200=s200,
        s201=s201_evidence,
    )


def _synthetic_s202(
    tmp_path: Path,
) -> tuple[
    dict[str, object],
    contract.PredecessorEvidence,
    contract.S202PublicEvidence,
]:
    root_name = "artifacts/s202"
    config_hash = "d" * 64
    predecessors = _fake_predecessors(config_hash)
    seed_contracts: list[dict[str, object]] = []
    seed_commit_bindings: dict[str, object] = {}
    for seed in (42, 43, 44):
        seed_root = f"{root_name}/seed_{seed}"
        artifact_hashes: dict[str, str] = {}
        registry_artifacts: list[dict[str, object]] = []
        for name in contract.S202_SEED_ARTIFACTS:
            relative = f"{seed_root}/{name}.json"
            digest = _write_json(tmp_path / relative, {"name": name, "seed": seed})
            artifact_hashes[name] = digest
            registry_artifacts.append(
                {
                    "name": name,
                    "path": relative,
                    "sha256": digest,
                    "size_bytes": (tmp_path / relative).stat().st_size,
                }
            )
        run_id = f"aihub71490-s2-s202-auditor-v1-seed-{seed}"
        registry_path = f"artifacts/registry/{run_id}.json"
        registry_hash = _write_json(
            tmp_path / registry_path,
            {
                "schema_version": 1,
                "run_id": run_id,
                "status": "completed",
                "failure": None,
                "artifacts": registry_artifacts,
            },
        )
        commit_path = f"{seed_root}/commit.json"
        commit: dict[str, object] = {
            "schema_version": contract.S202_SEED_SCHEMA,
            "status": "committed",
            "decision": "seed_model_frozen",
            "seed": seed,
            "production_trainer_implemented": True,
            "expert_holdout_access_count": 0,
            "s1_prediction_feature_count": 0,
            "s1_label_feature_count": 0,
            "s1_alignment_feature_count": 0,
            "whisper_asr_score_feature_count": 0,
            "locked_test_access_count": 0,
            "result_based_reselection_count": 0,
            "predecessors": {
                "s108_commit_sha256": "1" * 64,
                "s200_readiness_lock_sha256": "6" * 64,
                "s201_commit_sha256": "9" * 64,
                "s202_config_sha256": config_hash,
                "s202_preregistration_sha256": "e" * 64,
            },
            "artifact_hashes": artifact_hashes,
            "registry": {
                "path": registry_path,
                "sha256": registry_hash,
                "run_id": run_id,
                "status": "completed",
            },
        }
        commit["binding_sha256"] = contract._binding_sha256(commit)  # noqa: SLF001
        commit_hash = _write_json(tmp_path / commit_path, commit)
        seed_commit_bindings[str(seed)] = {
            "path": commit_path,
            "sha256": commit_hash,
        }
        seed_contracts.append(
            {
                "seed": seed,
                "commit_path": commit_path,
                "registry_path": registry_path,
                "registry_run_id": run_id,
            }
        )
    aggregate_hashes: dict[str, str] = {}
    aggregate_paths: dict[str, str] = {}
    for name in contract.S202_AGGREGATE_ARTIFACTS:
        relative = f"{root_name}/{name}.json"
        digest = _write_json(tmp_path / relative, {"name": name})
        aggregate_hashes[name] = digest
        aggregate_paths[name] = relative
    aggregate: dict[str, object] = {
        "schema_version": contract.S202_AGGREGATE_SCHEMA,
        "status": "committed",
        "decision": "independent_auditor_frozen",
        "production_trainer_implemented": True,
        "training_completed": True,
        "seed_count": 3,
        "seeds": [42, 43, 44],
        "expert_holdout_access_count": 0,
        "s1_prediction_feature_count": 0,
        "s1_label_feature_count": 0,
        "s1_alignment_feature_count": 0,
        "whisper_asr_score_feature_count": 0,
        "locked_test_access_count": 0,
        "result_based_reselection_count": 0,
        "preregistration_sha256": "e" * 64,
        "expert_label_attestation_sha256": "f" * 64,
        "split_attestation_sha256": "0" * 64,
        "model_bundle_attestation_sha256": "1" * 64,
        "predecessors": {
            "s108_commit_sha256": "1" * 64,
            "s200_readiness_lock_sha256": "6" * 64,
            "s201_commit_sha256": "9" * 64,
            "s202_config_sha256": config_hash,
        },
        "artifact_hashes": aggregate_hashes,
        "artifact_paths": aggregate_paths,
        "seed_commits": seed_commit_bindings,
    }
    aggregate["binding_sha256"] = contract._binding_sha256(aggregate)  # noqa: SLF001
    aggregate_path = f"{root_name}/commit.json"
    _write_json(tmp_path / aggregate_path, aggregate)
    config: dict[str, object] = {
        "predecessors": {
            "s202_auditor": {
                "artifact_root": root_name,
                "aggregate_commit_path": aggregate_path,
                "seeds": seed_contracts,
            }
        }
    }
    audit = contract.AccessAudit()
    public = contract.validate_s202_completed_public(
        root=tmp_path,
        config=config,
        predecessors=predecessors,
        audit=audit,
    )
    assert audit.s202_public_validation_count == 1
    return config, predecessors, public


def test_s202_public_and_deep_validators_accept_exact_bundle(tmp_path: Path) -> None:
    config, _, public = _synthetic_s202(tmp_path)
    audit = contract.AccessAudit()
    deep = contract.deep_validate_s202_completed(
        root=tmp_path,
        config=config,
        public=public,
        audit=audit,
    )
    assert audit.s202_deep_validation_count == 1
    assert len(deep.registry_snapshots) == 3
    assert len(deep.artifact_snapshots) == 17
    assert audit.predecessor_artifact_read_count == 17


def test_s202_deep_validator_rejects_artifact_tamper(tmp_path: Path) -> None:
    config, _, public = _synthetic_s202(tmp_path)
    target = tmp_path / "artifacts/s202/seed_42/seed_checkpoint.json"
    target.write_text('{"tampered":true}', encoding="utf-8")
    with pytest.raises(contract.S203ContractError, match="hash"):
        contract.deep_validate_s202_completed(
            root=tmp_path,
            config=config,
            public=public,
            audit=contract.AccessAudit(),
        )


def test_s202_deep_validator_rejects_registry_symlink(tmp_path: Path) -> None:
    config, _, public = _synthetic_s202(tmp_path)
    registry = (
        tmp_path / "artifacts/registry/aihub71490-s2-s202-auditor-v1-seed-42.json"
    )
    target = registry.with_name("target.json")
    registry.rename(target)
    registry.symlink_to(target.name)
    with pytest.raises(contract.S203ContractError, match="symlink"):
        contract.deep_validate_s202_completed(
            root=tmp_path,
            config=config,
            public=public,
            audit=contract.AccessAudit(),
        )


def test_s202_public_validator_rejects_any_holdout_access(tmp_path: Path) -> None:
    config, predecessors, public = _synthetic_s202(tmp_path)
    aggregate_path = tmp_path / "artifacts/s202/commit.json"
    aggregate = dict(public.aggregate_commit)
    aggregate["expert_holdout_access_count"] = 1
    aggregate["binding_sha256"] = contract._binding_sha256(aggregate)  # noqa: SLF001
    _write_json(aggregate_path, aggregate)
    with pytest.raises(contract.S203ContractError, match="holdout"):
        contract.validate_s202_completed_public(
            root=tmp_path,
            config=config,
            predecessors=predecessors,
            audit=contract.AccessAudit(),
        )
