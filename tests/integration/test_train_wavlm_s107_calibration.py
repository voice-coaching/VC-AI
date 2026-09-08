from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import shutil
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from voice_coach.training import wavlm_s107_calibration_contract as contract
from voice_coach.training.wavlm_s107_calibration_contract import (
    BUNDLE_SCHEMA_VERSION,
    INPUT_COLUMNS,
    INPUT_SCHEMA_VERSION,
    S107ContractError,
    canonical_json_bytes,
    file_record,
    prepare_s107_execution,
    sha256_file,
)
from voice_coach.training.wavlm_s107_calibration_training import (
    S107CalibrationError,
    run_s107_calibration,
    validate_s107_commit,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
S107_CONFIG = "configs/experiments/wavlm_s107_calibration_v1.yaml"
S106_CONFIG = "configs/experiments/wavlm_s106_stack_fusion_v1.yaml"
S106_VALIDATOR = "src/voice_coach/training/wavlm_s106_fusion_training.py"
COPIED_FILES = (
    S107_CONFIG,
    S106_CONFIG,
    S106_VALIDATOR,
    "scripts/prepare_wavlm_s107_calibration.py",
    "scripts/run_wavlm_s107_calibration.py",
    "src/voice_coach/training/wavlm_s107_calibration_contract.py",
    "src/voice_coach/training/wavlm_s107_calibration_training.py",
    "pyproject.toml",
    "requirements/scorer-lock-linux-aarch64-cu130.txt",
)


@dataclass(frozen=True)
class SyntheticWorkspace:
    root: Path
    config_path: Path
    prediction_path: Path
    bundle_path: Path
    s106_commit_path: Path
    s106_checkpoint_path: Path

    def validator(self, **_: Any) -> dict[str, Any]:
        return {
            "status": "valid",
            "commit_sha256": sha256_file(self.s106_commit_path),
            "stack_only": True,
            "calibration_accessed": False,
            "locked_test_accessed": False,
            "full_stack_in_sample_metric_reported": False,
        }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value, pretty=True))


def _binding(document: dict[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("binding_sha256", None)
    return sha256(canonical_json_bytes(unsigned, pretty=False)).hexdigest()


def _copy_contract_files(root: Path) -> None:
    for relative in COPIED_FILES:
        source = REPOSITORY_ROOT / relative
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _prediction_rows(*, bad_split: bool = False) -> list[dict[str, Any]]:
    rows = []
    for index in range(120):
        label = 1 if index % 3 == 0 else 0
        probability = (
            0.83 + 0.002 * (index % 8) if label else 0.06 + 0.005 * (index % 20)
        )
        rows.append(
            {
                "utterance_id_sha256": sha256(
                    f"synthetic-utterance-{index}".encode()
                ).hexdigest(),
                "speaker_id_sha256": sha256(
                    f"synthetic-speaker-{index % 12}".encode()
                ).hexdigest(),
                "phone_index": index % 5,
                "split": "stack" if bad_split and index == 0 else "calibration",
                "uncalibrated_probability": probability,
                "reference_binary_error_label": label,
                "l1_group": "Chinese" if index % 2 else "Japanese",
                "gender_group": "F" if index % 2 else "M",
                "proficiency_group": "low" if index % 2 else "high",
                "duration_bucket": "short" if index % 2 else "long",
                "error_type_group": "binary_error" if label else "correct",
                "qc_flagged": False,
                "reserved_prompt": False,
            }
        )
    return rows


def _write_prediction(path: Path, *, bad_split: bool = False) -> None:
    rows = _prediction_rows(bad_split=bad_split)
    table = pa.Table.from_pylist(rows)
    assert table.schema.names == list(INPUT_COLUMNS)
    table = table.replace_schema_metadata(
        {b"voice_coach_schema_version": INPUT_SCHEMA_VERSION.encode()}
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        table,
        path,
        compression="zstd",
        use_dictionary=False,
        write_statistics=True,
    )


def _make_workspace(
    tmp_path: Path,
    *,
    with_s106: bool = True,
    bad_split: bool = False,
) -> SyntheticWorkspace:
    root = tmp_path / "workspace"
    root.mkdir()
    _copy_contract_files(root)
    config_path = root / S107_CONFIG
    generation_id = "a" * 64
    generation_path = (
        root
        / "artifacts/experiments/aihub71490_wavlm_s106_stack_fusion_v1"
        / "generations"
        / generation_id
    )
    checkpoint_path = generation_path / "final_fusion_checkpoint.json"
    s106_commit_path = (
        root
        / "artifacts/experiments/aihub71490_wavlm_s106_stack_fusion_v1"
        / "commit.json"
    )
    if with_s106:
        checkpoint = {
            "schema_version": "voice_coach.wavlm_s106_fusion_checkpoint.v1",
            "status": "completed",
            "family": "standardized_l2_logistic_regression",
            "fit_source": "full_stack_after_oof_selection",
            "fit_count": 1,
            "in_sample_prediction_computed": False,
            "in_sample_metric_computed": False,
            "in_sample_metric_reported": False,
        }
        _write_json(checkpoint_path, checkpoint)
        checkpoint_record = {
            "name": "final_fusion_checkpoint",
            "path": "final_fusion_checkpoint.json",
            "sha256": sha256_file(checkpoint_path),
            "size_bytes": checkpoint_path.stat().st_size,
            "format": "json",
            "access_scope": "D1_restricted_internal",
        }
        artifacts = [
            {
                "name": f"synthetic_{index}",
                "path": f"synthetic_{index}.json",
                "sha256": "b" * 64,
                "size_bytes": 1,
                "format": "json",
                "access_scope": "D1_restricted_internal",
            }
            for index in range(6)
        ] + [checkpoint_record]
        commit: dict[str, Any] = {
            "schema_version": "voice_coach.wavlm_s106_stack_fusion_commit.v2",
            "status": "committed",
            "generation_id": generation_id,
            "generation_path": generation_path.relative_to(root).as_posix(),
            "artifacts": artifacts,
            "config": file_record(root, root / S106_CONFIG),
            "execution_lock": {},
            "component_bundle_commit": {},
            "registry": {},
        }
        commit["binding_sha256"] = _binding(commit)
        _write_json(s106_commit_path, commit)

    prediction_path = (
        root
        / "artifacts/experiments/aihub71490_wavlm_s107_calibration_v1"
        / "synthetic_calibration_predictions.parquet"
    )
    _write_prediction(prediction_path, bad_split=bad_split)
    bundle_path = (
        root
        / "artifacts/experiments/aihub71490_wavlm_s107_calibration_v1"
        / "calibration_prediction_bundle_commit.json"
    )
    if with_s106:
        bundle: dict[str, Any] = {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "status": "committed",
            "s106_commit": file_record(root, s106_commit_path),
            "s106_checkpoint": file_record(root, checkpoint_path),
            "label_status": "silver_oracle_provisional",
            "d07_evidence": None,
            "prediction_matrix": {
                **file_record(root, prediction_path),
                "row_count": 120,
            },
        }
        bundle["binding_sha256"] = _binding(bundle)
        _write_json(bundle_path, bundle)
    return SyntheticWorkspace(
        root=root,
        config_path=config_path,
        prediction_path=prediction_path,
        bundle_path=bundle_path,
        s106_commit_path=s106_commit_path,
        s106_checkpoint_path=checkpoint_path,
    )


def _prepare(workspace: SyntheticWorkspace) -> Path:
    _, lock_path = prepare_s107_execution(
        workspace_root=workspace.root,
        config_path=workspace.config_path,
        predecessor_validator=workspace.validator,
    )
    return lock_path


def _snapshot_files(root: Path) -> dict[str, tuple[str, int, int]]:
    return {
        path.relative_to(root).as_posix(): (
            sha256_file(path),
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def test_bounded_synthetic_end_to_end_and_read_only_validation(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    lock_path = _prepare(workspace)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    assert lock["prediction_matrix"]["semantic_rows_read_by_preflight"] is False
    assert lock["split_access"]["calibration"] is False
    result = run_s107_calibration(
        workspace_root=workspace.root,
        config_path=workspace.config_path,
        predecessor_validator=workspace.validator,
    )
    assert result["status"] == "valid"
    assert result["artifact_count"] == 7
    assert result["row_count"] == 120
    assert result["label_status"] == "silver_oracle_provisional"
    assert result["promotion_decision_allowed"] is False
    assert result["s108_ready"] is False
    before = _snapshot_files(workspace.root)
    validated = validate_s107_commit(
        workspace_root=workspace.root,
        config_path=workspace.config_path,
        predecessor_validator=workspace.validator,
    )
    after = _snapshot_files(workspace.root)
    assert validated["commit_sha256"] == result["commit_sha256"]
    assert before == after


def test_preflight_fails_before_calibration_resolver_without_s106(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _make_workspace(tmp_path, with_s106=False)
    called = False

    def forbidden_bundle_resolution(**_: Any) -> None:
        nonlocal called
        called = True
        raise AssertionError("calibration resolver must not be reached")

    monkeypatch.setattr(
        contract,
        "validate_calibration_bundle",
        forbidden_bundle_resolution,
    )
    with pytest.raises(S107ContractError, match="S106 commit does not exist"):
        prepare_s107_execution(
            workspace_root=workspace.root,
            config_path=workspace.config_path,
            predecessor_validator=workspace.validator,
        )
    assert called is False
    assert not (
        workspace.root
        / "artifacts/experiments/aihub71490_wavlm_s107_calibration_v1"
        / "execution_lock.json"
    ).exists()


def test_deep_validator_rejects_aggregate_artifact_tamper(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _prepare(workspace)
    result = run_s107_calibration(
        workspace_root=workspace.root,
        config_path=workspace.config_path,
        predecessor_validator=workspace.validator,
    )
    generation = (
        workspace.root
        / "artifacts/experiments/aihub71490_wavlm_s107_calibration_v1"
        / "generations"
        / result["generation_id"]
    )
    report = generation / "reliability_report.json"
    report.write_bytes(report.read_bytes() + b" ")
    with pytest.raises(S107CalibrationError):
        validate_s107_commit(
            workspace_root=workspace.root,
            config_path=workspace.config_path,
            predecessor_validator=workspace.validator,
        )


def test_completed_registry_recovers_missing_commit(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _prepare(workspace)
    first = run_s107_calibration(
        workspace_root=workspace.root,
        config_path=workspace.config_path,
        predecessor_validator=workspace.validator,
    )
    commit_path = (
        workspace.root
        / "artifacts/experiments/aihub71490_wavlm_s107_calibration_v1"
        / "commit.json"
    )
    commit_path.unlink()
    recovered = run_s107_calibration(
        workspace_root=workspace.root,
        config_path=workspace.config_path,
        predecessor_validator=workspace.validator,
    )
    assert recovered["generation_id"] == first["generation_id"]
    assert commit_path.exists()


def test_existing_running_registry_blocks_concurrent_writer(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _prepare(workspace)
    registry = (
        workspace.root
        / "artifacts/experiment_registry"
        / "aihub71490-wavlm-s107-calibration-v1"
        / "metadata.json"
    )
    _write_json(registry, {"status": "running"})
    before = registry.read_bytes()
    with pytest.raises(S107CalibrationError, match="not recoverable"):
        run_s107_calibration(
            workspace_root=workspace.root,
            config_path=workspace.config_path,
            predecessor_validator=workspace.validator,
        )
    assert registry.read_bytes() == before


def test_symlinked_calibration_prediction_is_rejected(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    target = workspace.prediction_path.with_name("real_predictions.parquet")
    workspace.prediction_path.replace(target)
    workspace.prediction_path.symlink_to(target.name)
    with pytest.raises(S107ContractError, match="symlink"):
        _prepare(workspace)


def test_execution_lock_is_immutable_and_not_overwritten(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    lock_path = _prepare(workspace)
    before = lock_path.read_bytes()
    with pytest.raises(S107ContractError, match="already exists"):
        _prepare(workspace)
    assert lock_path.read_bytes() == before


def test_output_override_symlink_chain_is_rejected(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    real_directory = workspace.root / "reviewed-output"
    real_directory.mkdir()
    linked_directory = workspace.root / "linked-output"
    linked_directory.symlink_to(real_directory.name)
    with pytest.raises(S107ContractError, match="symlink"):
        prepare_s107_execution(
            workspace_root=workspace.root,
            config_path=workspace.config_path,
            output_path=linked_directory / "execution_lock.json",
            predecessor_validator=workspace.validator,
        )
    assert not (real_directory / "execution_lock.json").exists()


def test_semantic_split_failure_publishes_sanitized_failed_registry(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path, bad_split=True)
    _prepare(workspace)
    with pytest.raises(S107CalibrationError, match="non-calibration"):
        run_s107_calibration(
            workspace_root=workspace.root,
            config_path=workspace.config_path,
            predecessor_validator=workspace.validator,
        )
    registry = (
        workspace.root
        / "artifacts/experiment_registry"
        / "aihub71490-wavlm-s107-calibration-v1"
        / "metadata.json"
    )
    failure = json.loads(registry.read_text(encoding="utf-8"))
    assert failure["status"] == "failed"
    assert failure["failure"]["message"] == (
        "S107 failed; inspect restricted execution logs"
    )
    assert "synthetic-utterance" not in registry.read_text(encoding="utf-8")


def test_expert_gold_bundle_is_fail_closed_until_d07_schema_freezes(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace(tmp_path)
    bundle = json.loads(workspace.bundle_path.read_text(encoding="utf-8"))
    bundle["label_status"] = "expert_gold_d07"
    bundle["d07_evidence"] = {"status": "claimed"}
    bundle["binding_sha256"] = _binding(bundle)
    _write_json(workspace.bundle_path, bundle)
    with pytest.raises(S107ContractError, match="D07 evidence schema"):
        _prepare(workspace)
