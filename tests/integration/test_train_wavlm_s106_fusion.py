from __future__ import annotations

from dataclasses import replace
from collections.abc import Callable
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
import yaml  # type: ignore[import-untyped]

from voice_coach.training import wavlm_s106_fusion_contract as contract
from voice_coach.training import wavlm_s106_fusion_training as training
from voice_coach.training import wavlm_s106_component_projection as projection
from voice_coach.training import wavlm_s106_component_generator as generator


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/experiments/wavlm_s106_stack_fusion_v1.yaml"
TRAIN_SCRIPT = ROOT / "scripts/train_wavlm_s106_fusion.py"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.chmod(0o640)
    path.write_bytes(contract.canonical_json_bytes(value, pretty=True))


def _write_parquet(
    path: Path,
    rows: list[dict[str, Any]],
    columns: tuple[str, ...],
    metadata: dict[bytes, bytes] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.chmod(0o640)
    table = pa.Table.from_pylist(rows).select(list(columns))
    if metadata is not None:
        table = table.replace_schema_metadata(metadata)
    pq.write_table(
        table,
        path,
        compression="zstd",
        use_dictionary=False,
        write_statistics=True,
    )


def _seal(path: Path) -> None:
    path.chmod(0o440)


def _rebind(document: dict[str, Any]) -> None:
    document.pop("binding_sha256", None)
    document["binding_sha256"] = hashlib.sha256(
        contract.canonical_json_bytes(document, pretty=False)
    ).hexdigest()


def _rewrite_generator_dag(
    workspace: Path,
    config: dict[str, Any],
    *,
    commit_mutator: Callable[[dict[str, Any]], None] | None = None,
    bundle_mutator: Callable[[dict[str, Any]], None] | None = None,
    registry_mutator: Callable[[dict[str, Any]], None] | None = None,
    sync_producer_identity: bool = False,
) -> None:
    inputs = config["inputs"]
    commit_path = workspace / inputs["component_generator_commit"]
    bundle_path = workspace / inputs["component_bundle_commit"]
    registry_path = workspace / inputs["component_generator_registry"]
    commit = json.loads(commit_path.read_text(encoding="utf-8"))
    if commit_mutator is not None:
        commit_mutator(commit)
    _rebind(commit)
    _write_json(commit_path, commit)
    _seal(commit_path)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["generator_commit"] = _checkpoint_record(commit_path, workspace)
    if sync_producer_identity:
        bundle["producer_identity"] = commit["producer_identity"]
    if bundle_mutator is not None:
        bundle_mutator(bundle)
    _rebind(bundle)
    _write_json(bundle_path, bundle)
    _seal(bundle_path)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["commit"] = _checkpoint_record(commit_path, workspace)
    registry["component_bundle"] = _checkpoint_record(bundle_path, workspace)
    if sync_producer_identity:
        registry["producer_identity"] = commit["producer_identity"]
    if registry_mutator is not None:
        registry_mutator(registry)
    _rebind(registry)
    _write_json(registry_path, registry)
    _seal(registry_path)


def _file_record(path: Path, workspace: Path, row_count: int) -> dict[str, Any]:
    return {
        "path": path.relative_to(workspace).as_posix(),
        "sha256": _sha(path),
        "size_bytes": path.stat().st_size,
        "row_count": row_count,
    }


def _checkpoint_record(path: Path, workspace: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(workspace).as_posix(),
        "sha256": _sha(path),
        "size_bytes": path.stat().st_size,
    }


def _projection_attestation(component: str) -> dict[str, Any]:
    return {
        "label_free": True,
        "stack_only": True,
        "binary_error_label_used": False,
        "realized_phone_identity_used": False,
        "dev_prediction_reused": False,
        "representation": projection.PROJECTION_REPRESENTATIONS[component],
    }


def _predecessor_deep_audit(stage: str) -> dict[str, Any]:
    if stage == "s104":
        return {
            "validator_status": "valid",
            "development_only": True,
            "locked_test_accessed": False,
            "fit_metadata_read": True,
            "dev_truth_read": True,
            "per_seed_dev_predictions_read": True,
            "access_basis": "pinned_public_validator_contract_inspection",
        }
    if stage == "s105":
        return {
            "validator_status": "valid",
            "development_only": True,
            "stack_accessed": False,
            "calibration_accessed": False,
            "locked_test_accessed": False,
            "manifest_reservation_labels_read": True,
            "dev_predictions_read": True,
            "nested_s104_full_validator": True,
            "access_basis": "pinned_public_validator_contract_inspection",
        }
    if stage == "b07":
        return {
            "validator_status": "valid",
            "full_train_selection_dev_recomputed": True,
            "serialized_acoustic_checkpoint_replayed": True,
            "serialized_error_checkpoint_replayed": True,
            "canonical_train_read": True,
            "canonical_dev_read": True,
            "stored_dev_predictions_read": True,
            "fusion_component_reuse": False,
            "access_basis": "validator_return_and_pinned_public_contract",
        }
    raise AssertionError(f"unsupported predecessor: {stage}")


def _prediction_metadata(
    *,
    component: str,
    seed: int,
    source_lineage_sha256: str,
    source_checkpoint_sha256s: list[str],
    attestation: dict[str, Any],
) -> dict[bytes, bytes]:
    return {
        b"voice_coach_schema_version": (
            projection.PREDICTION_RECORD_SCHEMA_VERSION.encode()
        ),
        b"voice_coach_component": component.encode(),
        b"voice_coach_seed": str(seed).encode(),
        b"voice_coach_split": b"stack",
        b"voice_coach_source_lineage_sha256": source_lineage_sha256.encode(),
        b"voice_coach_source_checkpoint_sha256s": ",".join(
            source_checkpoint_sha256s
        ).encode(),
        b"voice_coach_projection_attestation_sha256": hashlib.sha256(
            contract.canonical_json_bytes(attestation, pretty=False)
        )
        .hexdigest()
        .encode(),
    }


def _workspace_file_snapshot(root: Path) -> dict[str, tuple[str, int, int]]:
    return {
        path.relative_to(root).as_posix(): (
            _sha(path),
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def _speaker_for_fold(fold: int, ordinal: int) -> str:
    found = 0
    for index in range(1_000_000):
        speaker = hashlib.sha256(f"speaker-{fold}-{index}".encode()).hexdigest()
        if contract.assign_speaker_fold(speaker) == fold:
            if found == ordinal:
                return speaker
            found += 1
    raise AssertionError("speaker fold fixture failed")


def _copy_implementation_sources(workspace: Path, config: dict[str, Any]) -> None:
    for value in config["implementation"].values():
        source = ROOT / value["path"]
        destination = workspace / value["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def _copy_predecessor_canonical_sources(workspace: Path) -> None:
    for relative in (
        contract.S104_CONFIG_PATH,
        contract.S104_VALIDATOR_MODULE_PATH,
        contract.S105_CONFIG_PATH,
        contract.S105_VALIDATOR_MODULE_PATH,
        contract.B07_CONFIG_PATH,
        contract.B07_VALIDATOR_MODULE_PATH,
    ):
        source = ROOT / relative
        destination = workspace / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def _build_workspace(
    tmp_path: Path,
    *,
    partial_seed: bool = False,
    add_forbidden_label_column: bool = False,
    component_speaker_mismatch: bool = False,
    omit_component_generation_tree: bool = False,
) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    _copy_implementation_sources(workspace, config)
    _copy_predecessor_canonical_sources(workspace)

    stack_source = workspace / "data/synthetic_stack_source.bin"
    stack_source.parent.mkdir(parents=True)
    stack_source.write_bytes(b"synthetic stack source; no real corpus rows\n")
    config["data"]["stack"]["manifest"] = stack_source.relative_to(workspace).as_posix()
    config["data"]["stack"]["manifest_sha256"] = _sha(stack_source)
    config_path = workspace / "configs/experiments/wavlm_s106_stack_fusion_v1.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    loaded = contract.load_config(config_path)
    contract.validate_config(loaded)
    implementation = contract.validate_implementation_sources(
        workspace_root=workspace,
        config=loaded,
    )

    predecessor_root = workspace / "artifacts/synthetic_predecessors"
    s104_commit = predecessor_root / "s104_commit.json"
    s105_commit = predecessor_root / "s105_commit.json"
    b07_commit = predecessor_root / "b07_commit.json"
    b07_registry = predecessor_root / "b07_registry.json"
    explicit_registry = predecessor_root / "explicit_registry.json"
    s104_generation = predecessor_root / "s104_generation"
    s104_report = s104_generation / "experiment_report.json"
    _write_json(s104_report, {"status": "completed", "fixture": "s104"})
    _write_json(
        s104_commit,
        {
            "status": "committed",
            "generation_id": "1" * 64,
            "generation_path": s104_generation.relative_to(workspace).as_posix(),
            "artifacts": [
                {
                    "name": "experiment_report",
                    "path": "experiment_report.json",
                    "sha256": _sha(s104_report),
                    "size_bytes": s104_report.stat().st_size,
                }
            ],
            "experiment_report_sha256": _sha(s104_report),
            "fixture": "s104",
        },
    )
    s105_generation = predecessor_root / "s105_generation"
    s105_report = s105_generation / "ablation_comparison_report.json"
    _write_json(s105_report, {"status": "completed", "fixture": "s105"})
    _write_json(
        s105_commit,
        {
            "status": "committed",
            "generation_id": "3" * 64,
            "generation_path": s105_generation.relative_to(workspace).as_posix(),
            "artifacts": [
                {
                    "name": "ablation_comparison_report",
                    "path": "ablation_comparison_report.json",
                    "sha256": _sha(s105_report),
                    "size_bytes": s105_report.stat().st_size,
                }
            ],
            "fixture": "s105",
        },
    )
    b07_generation = predecessor_root / "b07_generation"
    b07_report = b07_generation / "aggregate_report.json"
    _write_json(b07_report, {"status": "completed", "fixture": "b07"})
    _write_json(
        b07_commit,
        {
            "status": "committed",
            "generation": {
                "id": "5" * 64,
                "path": b07_generation.relative_to(workspace).as_posix(),
                "records": [
                    {
                        "path": "aggregate_report.json",
                        "sha256": _sha(b07_report),
                        "size_bytes": b07_report.stat().st_size,
                    }
                ],
            },
            "fixture": "b07",
        },
    )
    _write_json(b07_registry, {"status": "completed", "fixture": "b07"})
    _write_json(
        explicit_registry,
        {"status": "completed", "fixture": "explicit"},
    )
    projection_checkpoints: dict[str, list[dict[str, Any]]] = {
        component: [] for component in contract.COMPONENTS
    }
    checkpoint_root = workspace / "artifacts/synthetic_projection_checkpoints"
    for component in contract.COMPONENTS:
        for seed in contract.SEEDS:
            if component == "gop_acoustic_posterior":
                acoustic = checkpoint_root / component / f"seed_{seed}_acoustic.json"
                error = checkpoint_root / component / f"seed_{seed}_error.json"
                acoustic.parent.mkdir(parents=True, exist_ok=True)
                acoustic.write_text(f"synthetic acoustic {seed}\n", encoding="utf-8")
                error.write_text(f"synthetic error {seed}\n", encoding="utf-8")
                projection_checkpoints[component].append(
                    {
                        "seed": seed,
                        "acoustic_checkpoint": _checkpoint_record(
                            acoustic,
                            workspace,
                        ),
                        "error_checkpoint": _checkpoint_record(error, workspace),
                        "projection_validation": {
                            "status": "projection_ready",
                            "label_argument_present": False,
                            "dev_prediction_reused": False,
                            "realized_phone_identity_used": False,
                            "acoustic_schema_version": (
                                "voice_coach.b07_gop_acoustic_checkpoint.v1"
                            ),
                            "error_schema_version": (
                                "voice_coach.b07_gop_error_checkpoint.v1"
                            ),
                            "seed": seed,
                        },
                    }
                )
            else:
                checkpoint = checkpoint_root / component / f"seed_{seed}.bin"
                checkpoint.parent.mkdir(parents=True, exist_ok=True)
                checkpoint.write_text(
                    f"synthetic {component} checkpoint {seed}\n",
                    encoding="utf-8",
                )
                projection_checkpoints[component].append(
                    {
                        "seed": seed,
                        "checkpoint": _checkpoint_record(checkpoint, workspace),
                    }
                )
    contract_hashes = {
        f"{name}_sha256": hashlib.sha256(
            contract.canonical_json_bytes(loaded[name], pretty=False)
        ).hexdigest()
        for name in ("seed_aggregation", "feature_contract", "oof", "reporting")
    }
    execution_lock_path = workspace / loaded["outputs"]["execution_lock"]
    execution_lock = {
        "schema_version": contract.EXECUTION_LOCK_SCHEMA_VERSION,
        "status": "ready",
        "experiment_id": loaded["experiment"]["id"],
        "stage": loaded["experiment"]["stage"],
        "config": {
            "path": config_path.relative_to(workspace).as_posix(),
            "sha256": _sha(config_path),
        },
        "implementation": implementation,
        "predecessors": {
            "s104": {
                "commit_path": s104_commit.relative_to(workspace).as_posix(),
                "commit_sha256": _sha(s104_commit),
                "generation_id": "1" * 64,
                "report_path": s104_report.relative_to(workspace).as_posix(),
                "report_sha256": _sha(s104_report),
                "selected_variant": "base_plus",
                "adoption_status": None,
                "projection_checkpoints": projection_checkpoints["s104_adapter"],
                "deep_audit": _predecessor_deep_audit("s104"),
                "canonical_sources": {
                    "config": _checkpoint_record(
                        workspace / contract.S104_CONFIG_PATH,
                        workspace,
                    ),
                    "validator_module": _checkpoint_record(
                        workspace / contract.S104_VALIDATOR_MODULE_PATH,
                        workspace,
                    ),
                    "report": _checkpoint_record(s104_report, workspace),
                },
            },
            "s105": {
                "commit_path": s105_commit.relative_to(workspace).as_posix(),
                "commit_sha256": _sha(s105_commit),
                "generation_id": "3" * 64,
                "report_path": s105_report.relative_to(workspace).as_posix(),
                "report_sha256": _sha(s105_report),
                "selected_variant": "simple_head_control",
                "adoption_status": "failed",
                "projection_checkpoints": projection_checkpoints[
                    "s105_selected_hierarchy"
                ],
                "deep_audit": _predecessor_deep_audit("s105"),
                "canonical_sources": {
                    "config": _checkpoint_record(
                        workspace / contract.S105_CONFIG_PATH,
                        workspace,
                    ),
                    "validator_module": _checkpoint_record(
                        workspace / contract.S105_VALIDATOR_MODULE_PATH,
                        workspace,
                    ),
                    "report": _checkpoint_record(s105_report, workspace),
                },
            },
            "b07": {
                "commit_path": b07_commit.relative_to(workspace).as_posix(),
                "commit_sha256": _sha(b07_commit),
                "generation_id": "5" * 64,
                "report_path": b07_report.relative_to(workspace).as_posix(),
                "report_sha256": _sha(b07_report),
                "selected_variant": "gop_acoustic_posterior",
                "adoption_status": "passed",
                "registry_path": b07_registry.relative_to(workspace).as_posix(),
                "registry_sha256": _sha(b07_registry),
                "projection_checkpoints": projection_checkpoints[
                    "gop_acoustic_posterior"
                ],
                "deep_audit": _predecessor_deep_audit("b07"),
                "canonical_sources": {
                    "config": _checkpoint_record(
                        workspace / contract.B07_CONFIG_PATH,
                        workspace,
                    ),
                    "validator_module": _checkpoint_record(
                        workspace / contract.B07_VALIDATOR_MODULE_PATH,
                        workspace,
                    ),
                    "report": _checkpoint_record(b07_report, workspace),
                },
            },
        },
        "frozen_components": {
            "order": list(contract.COMPONENTS),
            "explicit": {
                "name": "explicit",
                "status": "frozen",
                "seeds": list(contract.SEEDS),
                "sources": {
                    "registry": _checkpoint_record(
                        explicit_registry,
                        workspace,
                    )
                },
                "checkpoint_artifacts": [],
                "projection_checkpoints": projection_checkpoints["explicit"],
            },
            "s104_adapter": {
                "source_commit_sha256": _sha(s104_commit),
                "selected_variant": "base_plus",
                "seeds": list(contract.SEEDS),
                "projection_checkpoints": projection_checkpoints["s104_adapter"],
            },
            "s105_selected_hierarchy": {
                "source_commit_sha256": _sha(s105_commit),
                "selected_variant": "simple_head_control",
                "adoption_status": "failed",
                "seeds": list(contract.SEEDS),
                "projection_checkpoints": projection_checkpoints[
                    "s105_selected_hierarchy"
                ],
            },
            "gop_acoustic_posterior": {
                "source_commit_sha256": _sha(b07_commit),
                "source_registry_sha256": _sha(b07_registry),
                "generation_id": "5" * 64,
                "selected_variant": "gop_acoustic_posterior",
                "adoption_status": "passed",
                "seeds": list(contract.SEEDS),
                "projection_checkpoints": projection_checkpoints[
                    "gop_acoustic_posterior"
                ],
                "label_free_projection": True,
                "dev_prediction_reused": False,
            },
        },
        "stack_source": {
            "path": stack_source.relative_to(workspace).as_posix(),
            "sha256": _sha(stack_source),
            "size_bytes": stack_source.stat().st_size,
            "split_value": "stack",
            "row_data_read_by_preflight": False,
        },
        "component_bundle_contract": {
            "path": loaded["inputs"]["component_bundle_commit"],
            "schema_version": training.COMPONENT_BUNDLE_SCHEMA_VERSION,
            "resolved_or_read_by_preflight": False,
        },
        "contract_hashes": contract_hashes,
        "access_ledger": {
            "preflight_cpu_only": True,
            "gpu_initialized": False,
            "fusion_fit_performed": False,
            "environment_files_read": [],
            "metrics_computed": False,
            "metrics_reported": False,
            "component_prediction_files_written": 0,
            "predecessor_deep_audit": {
                "public_validator_call_order": [
                    "s104_direct",
                    "s105_with_nested_s104",
                    "b07",
                ],
                "validator_invocation_counts": {"s104": 2, "s105": 1, "b07": 1},
                "train_or_fit_inputs_read": True,
                "dev_inputs_read": True,
                "dev_prediction_artifacts_read": True,
                "s104": _predecessor_deep_audit("s104"),
                "s105": _predecessor_deep_audit("s105"),
                "b07": _predecessor_deep_audit("b07"),
            },
            "fusion_input_access": {
                "stack_rows_read_by_preflight": False,
                "stack_labels_read_by_preflight": False,
                "fusion_feature_train_read": False,
                "fusion_feature_dev_read": False,
                "predecessor_dev_predictions_reused_as_components": False,
                "b07_dev_predictions_reused_as_component": False,
                "calibration_read": False,
                "locked_test_read": False,
            },
        },
        "next_action": "synthetic S106 integration fixture",
    }
    _write_json(execution_lock_path, execution_lock)

    feature_identities: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    component_rows: dict[str, dict[int, list[dict[str, Any]]]] = {
        component: {seed: [] for seed in contract.SEEDS}
        for component in contract.COMPONENTS
    }
    row_index = 0
    for fold in range(contract.OOF_FOLD_COUNT):
        for speaker_ordinal in range(2):
            speaker = _speaker_for_fold(fold, speaker_ordinal)
            for local_index, label in enumerate((0, 1, 0, 1)):
                utterance = hashlib.sha256(
                    f"utterance-{fold}-{speaker_ordinal}-{local_index}".encode()
                ).hexdigest()
                labels.append(
                    {
                        "utterance_id_sha256": utterance,
                        "speaker_id_sha256": speaker,
                        "phone_index": local_index,
                        "split": "stack",
                        "reference_binary_error_label": label,
                    }
                )
                feature_identities.append(
                    {
                        "utterance_id_sha256": utterance,
                        "speaker_id_sha256": speaker,
                        "phone_index": local_index,
                        "split": "stack",
                        "alignment_confidence": None,
                        "qc_flagged": False,
                        "reserved_prompt": False,
                    }
                )
                bases = {
                    "explicit": 0.35 if label == 0 else 0.65,
                    "s104_adapter": 0.22 if label == 0 else 0.78,
                    "s105_selected_hierarchy": 0.18 if label == 0 else 0.82,
                    "gop_acoustic_posterior": 0.28 if label == 0 else 0.72,
                }
                for component in contract.COMPONENTS:
                    for seed_index, seed in enumerate(contract.SEEDS):
                        if (
                            partial_seed
                            and component == "explicit"
                            and row_index == 1
                            and seed == 44
                        ):
                            continue
                        probability = bases[component] + (seed_index - 1) * 0.01
                        prediction_speaker = (
                            "f" * 64
                            if component_speaker_mismatch
                            and component == "s105_selected_hierarchy"
                            and row_index == 2
                            and seed == 42
                            else speaker
                        )
                        component_rows[component][seed].append(
                            {
                                "utterance_id_sha256": utterance,
                                "speaker_id_sha256": prediction_speaker,
                                "phone_index": local_index,
                                "split": "stack",
                                "probability": probability,
                            }
                        )
                row_index += 1

    generator_config_path = (
        workspace / loaded["implementation"]["component_generator_config"]["path"]
    )
    generator_config_document = yaml.safe_load(
        generator_config_path.read_text(encoding="utf-8")
    )
    label_path = workspace / generator_config_document["outputs"]["label_matrix"]
    feature_identity_path = (
        workspace / generator_config_document["outputs"]["feature_identity_matrix"]
    )
    _write_parquet(
        feature_identity_path,
        feature_identities,
        training.FEATURE_IDENTITY_COLUMNS,
    )
    _seal(feature_identity_path)
    label_columns = (
        (*training.LABEL_COLUMNS, "realized_phone")
        if add_forbidden_label_column
        else training.LABEL_COLUMNS
    )
    if add_forbidden_label_column:
        for row in labels:
            row["realized_phone"] = "forbidden"
    _write_parquet(label_path, labels, label_columns)
    _seal(label_path)
    component_records: dict[str, Any] = {}
    for component in contract.COMPONENTS:
        seed_records: dict[str, Any] = {}
        frozen_component = execution_lock["frozen_components"][component]
        source_lineage_sha256 = (
            frozen_component["sources"]["registry"]["sha256"]
            if component == "explicit"
            else frozen_component["source_commit_sha256"]
        )
        for seed in contract.SEEDS:
            path = (
                workspace
                / projection.COMPONENT_PREDICTION_ROOT
                / component
                / f"seed_{seed}_stack_predictions.parquet"
            )
            rows = component_rows[component][seed]
            checkpoint_entry = next(
                value
                for value in projection_checkpoints[component]
                if value["seed"] == seed
            )
            source_checkpoint_sha256s = (
                [
                    checkpoint_entry["acoustic_checkpoint"]["sha256"],
                    checkpoint_entry["error_checkpoint"]["sha256"],
                ]
                if component == "gop_acoustic_posterior"
                else [checkpoint_entry["checkpoint"]["sha256"]]
            )
            attestation = _projection_attestation(component)
            _write_parquet(
                path,
                rows,
                training.COMPONENT_COLUMNS,
                metadata=_prediction_metadata(
                    component=component,
                    seed=seed,
                    source_lineage_sha256=source_lineage_sha256,
                    source_checkpoint_sha256s=source_checkpoint_sha256s,
                    attestation=attestation,
                ),
            )
            _seal(path)
            seed_records[f"seed_{seed}"] = {
                **_file_record(path, workspace, len(rows)),
                "schema_version": projection.PREDICTION_RECORD_SCHEMA_VERSION,
                "component": component,
                "seed": seed,
                "split": "stack",
                "source_lineage_sha256": source_lineage_sha256,
                "source_checkpoint_sha256s": source_checkpoint_sha256s,
                "projection_attestation": attestation,
            }
        component_records[component] = seed_records
    bundle_path = workspace / loaded["inputs"]["component_bundle_commit"]
    lock_record = {
        "path": execution_lock_path.relative_to(workspace).as_posix(),
        "sha256": _sha(execution_lock_path),
    }
    stack_record = {
        "path": stack_source.relative_to(workspace).as_posix(),
        "sha256": _sha(stack_source),
    }
    identity_record = _file_record(
        feature_identity_path,
        workspace,
        len(feature_identities),
    )
    label_record = _file_record(label_path, workspace, len(labels))
    feature_payload = generator._feature_digest_payload(  # noqa: SLF001
        execution_lock_record=lock_record,
        stack_source_record=stack_record,
        identity_record=identity_record,
        component_records=component_records,
    )
    generation_id = hashlib.sha256(
        contract.canonical_json_bytes(feature_payload, pretty=False)
    ).hexdigest()
    row_key_order_sha256 = hashlib.sha256(
        generator.canonical_json_bytes(
            [
                [row["utterance_id_sha256"], row["phone_index"]]
                for row in feature_identities
            ],
            pretty=False,
        )
    ).hexdigest()
    receipt = dict(
        generator.build_feature_freeze_receipt(
            generation_id=generation_id,
            canonical_row_key_order_sha256=row_key_order_sha256,
            row_count=len(feature_identities),
            execution_lock_record=lock_record,
            stack_source_record=stack_record,
            identity_record=identity_record,
            component_records=component_records,
        )
    )
    receipt_path = workspace / loaded["inputs"]["component_feature_freeze_receipt"]
    _write_json(receipt_path, receipt)
    _seal(receipt_path)
    if not omit_component_generation_tree:
        generation_path = (
            workspace
            / generator_config_document["outputs"]["generation_root"]
            / generation_id
        )
        (generation_path / "predictions").mkdir(parents=True)
        shutil.copyfile(
            feature_identity_path,
            generation_path / "feature_identity.parquet",
        )
        shutil.copyfile(
            receipt_path,
            generation_path / "feature_freeze_receipt.json",
        )
        for component in contract.COMPONENTS:
            component_path = generation_path / "predictions" / component
            component_path.mkdir()
            for seed in contract.SEEDS:
                public_prediction = (
                    workspace / component_records[component][f"seed_{seed}"]["path"]
                )
                shutil.copyfile(
                    public_prediction,
                    component_path / f"seed_{seed}_stack_predictions.parquet",
                )
        for path in generation_path.rglob("*"):
            if path.is_file():
                _seal(path)
        for path in sorted(
            (value for value in generation_path.rglob("*") if value.is_dir()),
            reverse=True,
        ):
            path.chmod(0o550)
        generation_path.chmod(0o550)
    receipt_record = _checkpoint_record(receipt_path, workspace)
    roles = (
        "component_projection_module",
        "component_generator_config",
        "component_generator_script",
        "component_generator_module",
    )
    producer_identity = {
        "mode": "production",
        "production_schema_allowed": True,
        "dependency_injection_allowed": False,
        "backend_factory": (
            "voice_coach.training.wavlm_s106_component_generator."
            "production_backend_factory"
        ),
        "preflight": (
            "voice_coach.training.wavlm_s106_fusion_contract.prepare_s106_execution"
        ),
        "bundle_builder": (
            "voice_coach.training.wavlm_s106_component_projection."
            "build_component_bundle_document"
        ),
        "pinned_sources": {
            role: dict(loaded["implementation"][role]) for role in roles
        },
    }
    generator_commit: dict[str, Any] = {
        "schema_version": loaded["inputs"]["component_generator_commit_schema_version"],
        "status": "committed",
        "generation_id": generation_id,
        "producer_identity": producer_identity,
        "generator_config": _checkpoint_record(generator_config_path, workspace),
        "execution_lock": lock_record,
        "feature_freeze_receipt": receipt_record,
        "label_matrix": label_record,
        "prediction_file_count": 12,
        "feature_rows": len(feature_identities),
        "label_rows": len(labels),
        "excluded_utterances": {"qc_flagged": 0, "reserved_prompt": 0},
        "access_audit": {
            "predecessor_validation_completed_before_runtime_access": True,
            "label_free_scan_before_checkpoint_probe": True,
            "eligible_raw_ids_retained_in_memory_only": True,
            "restricted_parquet_disk_snapshot_created": False,
            "label_scanner_exact_eligible_id_held_fd_predicate_pushed_down": True,
            "label_staging_anonymous_unlinked": True,
            "source_parquet_scanner_batches_bounded": True,
            "label_rows_streamed_in_bounded_batches": True,
            "production_publication_serialized": True,
            "phone_alignments_opened_after_feature_freeze": True,
            "operation_opened_after_feature_freeze": True,
            "audio_pcm_hash_and_decode_used_same_held_bytes": True,
            "models_loaded_from_private_immutable_snapshot": True,
            "production_dependencies_immutable": True,
            "b07_unadapted_forward_separate": True,
            "s104_s105_adapted_forward_shared_per_seed": True,
            "completed_registry_published_before_visibility_bundle": True,
            "bundle_is_final_visibility_commit": True,
            "development_only": True,
            "stack_only": True,
        },
    }
    generator_commit["binding_sha256"] = hashlib.sha256(
        contract.canonical_json_bytes(generator_commit, pretty=False)
    ).hexdigest()
    generator_commit_path = workspace / loaded["inputs"]["component_generator_commit"]
    _write_json(generator_commit_path, generator_commit)
    _seal(generator_commit_path)
    generator_commit_record = _checkpoint_record(generator_commit_path, workspace)
    if partial_seed:
        bundle: dict[str, Any] = {
            "schema_version": training.COMPONENT_BUNDLE_SCHEMA_VERSION,
            "status": "committed",
            "generator_commit": generator_commit_record,
            "feature_freeze_receipt": receipt_record,
            "producer_identity": producer_identity,
            "execution_lock": lock_record,
            "stack_source": stack_record,
            "frozen_components": execution_lock["frozen_components"],
            "feature_identity_matrix": identity_record,
            "label_matrix": label_record,
            "components": component_records,
            "projection_audit": {
                "component_order": list(contract.COMPONENTS),
                "seed_order": list(contract.SEEDS),
                "prediction_file_count": 12,
                "seed_aggregation_occurs_before_join": True,
                "feature_identity_built_before_label_open": True,
                "label_content_and_computed_hash_deferred_until_feature_freeze": True,
                "feature_builder_received_labels": False,
                "alignment_confidence_source": "absent_from_modeling_manifest",
                "alignment_confidence_non_null_allowed": False,
                "alignment_confidence_missing_indicator_forced": True,
                "b07_dev_predictions_reused": False,
                "realized_phone_identity_used": False,
                "binary_error_label_used_by_projectors": False,
            },
        }
        bundle["binding_sha256"] = hashlib.sha256(
            contract.canonical_json_bytes(bundle, pretty=False)
        ).hexdigest()
    else:
        bundle = dict(
            projection.build_component_bundle_document(
                workspace_root=workspace,
                execution_lock=execution_lock,
                execution_lock_record=lock_record,
                stack_source_record=stack_record,
                feature_identity_matrix_record=identity_record,
                label_matrix_record=label_record,
                component_records=component_records,
                generator_commit_record=generator_commit_record,
                feature_freeze_receipt_record=receipt_record,
                producer_identity=producer_identity,
            )
        )
    bundle_payload = contract.canonical_json_bytes(bundle, pretty=True)
    bundle_record = {
        "path": bundle_path.relative_to(workspace).as_posix(),
        "sha256": hashlib.sha256(bundle_payload).hexdigest(),
        "size_bytes": len(bundle_payload),
    }
    generator_registry: dict[str, Any] = {
        "schema_version": loaded["inputs"][
            "component_generator_registry_schema_version"
        ],
        "status": "completed",
        "run_id": "aihub71490-wavlm-s106-component-inputs-v1",
        "failure": None,
        "generation_id": generation_id,
        "producer_identity": producer_identity,
        "commit": generator_commit_record,
        "component_bundle": bundle_record,
        "feature_freeze_receipt": receipt_record,
        "artifact_counts": {
            "feature_identity": 1,
            "component_predictions": 12,
            "label_matrix": 1,
            "feature_freeze_receipt": 1,
            "component_bundle": 1,
            "generator_commit": 1,
        },
        "privacy": {
            "raw_identifiers_published": False,
            "raw_paths_published": False,
            "target_or_realized_phone_identity_published": False,
            "row_level_scope": "D1_restricted_internal",
        },
    }
    generator_registry["binding_sha256"] = hashlib.sha256(
        contract.canonical_json_bytes(generator_registry, pretty=False)
    ).hexdigest()
    registry_path = workspace / loaded["inputs"]["component_generator_registry"]
    _write_json(registry_path, generator_registry)
    _seal(registry_path)
    _write_json(bundle_path, bundle)
    _seal(bundle_path)
    return workspace, config_path


def test_synthetic_end_to_end_commit_and_deep_validation(tmp_path: Path) -> None:
    workspace, config_path = _build_workspace(tmp_path)
    result = training.run_s106_training(
        workspace_root=workspace,
        config_path=config_path,
    )
    assert result["status"] == "valid"
    assert result["artifact_count"] == 7
    assert result["row_count"] == 40
    assert result["speaker_count"] == 10
    assert result["full_stack_in_sample_metric_reported"] is False
    assert result["calibration_accessed"] is False
    assert result["locked_test_accessed"] is False
    validated = training.validate_s106_commit(
        workspace_root=workspace,
        config_path=config_path,
    )
    assert validated == result

    config = contract.load_config(config_path)
    generation = (
        workspace / config["outputs"]["generation_root"] / str(result["generation_id"])
    )
    matrix = pq.read_table(generation / "stack_component_matrix.parquet")
    assert "reference_binary_error_label" not in matrix.column_names
    assert tuple(matrix.column_names[5:]) == contract.EXACT_FEATURES
    assert len(contract.EXACT_FEATURES) == 26
    report = json.loads(
        (generation / "stack_only_report.json").read_text(encoding="utf-8")
    )
    assert report["final_fit"]["in_sample_metric_reported"] is False
    assert report["leakage"]["fusion_feature_train_accessed"] is False
    assert report["leakage"]["fusion_feature_dev_accessed"] is False
    assert report["leakage"]["b07_dev_predictions_reused_as_component"] is False
    assert report["predecessor_deep_audit"]["train_or_fit_inputs_read"] is True
    assert report["predecessor_deep_audit"]["dev_inputs_read"] is True
    assert report["predecessor_deep_audit"]["dev_prediction_artifacts_read"] is True
    assert report["leakage"]["calibration_accessed"] is False
    assert report["leakage"]["locked_test_accessed"] is False
    oof_report = json.loads(
        (generation / "oof_aggregate_report.json").read_text(encoding="utf-8")
    )
    assert "gop_only" in oof_report["controls_at_fixed_0_5"]


def test_document_only_component_lineage_without_generation_tree_is_rejected(
    tmp_path: Path,
) -> None:
    workspace, config_path = _build_workspace(
        tmp_path,
        omit_component_generation_tree=True,
    )
    with pytest.raises(
        training.S106TrainingError,
        match="component producer evidence tree differs",
    ):
        training.run_s106_training(
            workspace_root=workspace,
            config_path=config_path,
        )


def test_feature_freezes_before_label_file_is_opened(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path = _build_workspace(tmp_path)
    config = contract.load_config(config_path)
    lock_path = workspace / config["outputs"]["execution_lock"]
    lock = training.validate_execution_lock(
        root=workspace,
        config_path=config_path,
        config=config,
        lock_path=lock_path,
    )
    bundle = training.validate_component_bundle_commit(
        root=workspace,
        config=config,
        execution_lock_path=lock_path,
        execution_lock=lock,
        bundle_path=workspace / config["inputs"]["component_bundle_commit"],
    )
    events: list[str] = []
    original_identity = training._load_feature_identity_matrix
    original_component = training._load_component_seed
    original_assemble = training._assemble_feature_rows
    original_labels = training._load_label_targets

    def identity(*args: Any, **kwargs: Any) -> Any:
        events.append("identity")
        return original_identity(*args, **kwargs)

    def component(*args: Any, **kwargs: Any) -> Any:
        assert "labels" not in events
        events.append("component")
        return original_component(*args, **kwargs)

    def assemble(*args: Any, **kwargs: Any) -> Any:
        assert "labels" not in events
        result = original_assemble(*args, **kwargs)
        assert result[1].flags.writeable is False
        events.append("feature_freeze")
        return result

    def labels(*args: Any, **kwargs: Any) -> Any:
        assert events[-1] == "feature_freeze"
        events.append("labels")
        return original_labels(*args, **kwargs)

    monkeypatch.setattr(training, "_load_feature_identity_matrix", identity)
    monkeypatch.setattr(training, "_load_component_seed", component)
    monkeypatch.setattr(training, "_assemble_feature_rows", assemble)
    monkeypatch.setattr(training, "_load_label_targets", labels)
    matrix = training.build_component_matrix(root=workspace, bundle=bundle)
    assert events[0] == "identity"
    assert events.count("component") == 12
    assert events[-2:] == ["feature_freeze", "labels"]
    assert matrix.features.flags.writeable is False
    assert len(matrix.feature_freeze_sha256) == 64


def test_bundle_evidence_is_deep_frozen_and_redirect_is_rejected(
    tmp_path: Path,
) -> None:
    workspace, config_path = _build_workspace(tmp_path)
    config = contract.load_config(config_path)
    lock_path = workspace / config["outputs"]["execution_lock"]
    lock = training.validate_execution_lock(
        root=workspace,
        config_path=config_path,
        config=config,
        lock_path=lock_path,
    )
    bundle = training.validate_component_bundle_commit(
        root=workspace,
        config=config,
        execution_lock_path=lock_path,
        execution_lock=lock,
        bundle_path=workspace / config["inputs"]["component_bundle_commit"],
    )
    with pytest.raises(TypeError):
        bundle.component_records["explicit"][42]["path"] = (  # type: ignore[index]
            "redirected.parquet"
        )

    redirected = {
        component: {
            seed: dict(bundle.component_records[component][seed])
            for seed in contract.SEEDS
        }
        for component in contract.COMPONENTS
    }
    redirected["explicit"][42] = dict(bundle.component_records["s104_adapter"][42])
    malicious = replace(bundle, component_records=redirected)
    with pytest.raises(training.S106TrainingError, match="stack provenance"):
        training.build_component_matrix(root=workspace, bundle=malicious)
    assert not (workspace / config["outputs"]["commit_manifest"]).exists()


def test_stable_parquet_snapshot_rejects_mid_parse_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path = _build_workspace(tmp_path)
    config = contract.load_config(config_path)
    lock_path = workspace / config["outputs"]["execution_lock"]
    lock = training.validate_execution_lock(
        root=workspace,
        config_path=config_path,
        config=config,
        lock_path=lock_path,
    )
    bundle = training.validate_component_bundle_commit(
        root=workspace,
        config=config,
        execution_lock_path=lock_path,
        execution_lock=lock,
        bundle_path=workspace / config["inputs"]["component_bundle_commit"],
    )
    identity_path = workspace / str(bundle.feature_identity_record["path"])
    original_read_table = training.pq.read_table
    mutated = False

    def mutate_during_parse(*args: Any, **kwargs: Any) -> Any:
        nonlocal mutated
        if not mutated:
            mutated = True
            identity_path.chmod(0o640)
            identity_path.write_bytes(identity_path.read_bytes() + b"mutation")
        return original_read_table(*args, **kwargs)

    monkeypatch.setattr(training.pq, "read_table", mutate_during_parse)
    with pytest.raises(training.S106TrainingError, match="changed during stable read"):
        training.build_component_matrix(root=workspace, bundle=bundle)
    assert mutated is True
    assert not (workspace / config["outputs"]["commit_manifest"]).exists()


def test_cli_train_and_validate_commit_only(tmp_path: Path) -> None:
    workspace, config_path = _build_workspace(tmp_path)
    train = subprocess.run(
        [
            sys.executable,
            str(TRAIN_SCRIPT),
            "--workspace-root",
            str(workspace),
            "--config",
            str(config_path),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert train.returncode == 0, train.stderr
    trained = json.loads(train.stdout)
    assert trained["artifact_count"] == 7
    assert trained["full_stack_in_sample_metric_reported"] is False

    before_validation = _workspace_file_snapshot(workspace)
    validate = subprocess.run(
        [
            sys.executable,
            str(TRAIN_SCRIPT),
            "--workspace-root",
            str(workspace),
            "--config",
            str(config_path),
            "--validate-commit-only",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert validate.returncode == 0, validate.stderr
    validated = json.loads(validate.stdout)
    assert validated["mode"] == "validate_commit_only"
    assert validated["commit_sha256"] == trained["commit_sha256"]
    assert _workspace_file_snapshot(workspace) == before_validation


def test_completed_registry_recovers_missing_commit(tmp_path: Path) -> None:
    workspace, config_path = _build_workspace(tmp_path)
    first = training.run_s106_training(
        workspace_root=workspace,
        config_path=config_path,
    )
    config = contract.load_config(config_path)
    commit_path = workspace / config["outputs"]["commit_manifest"]
    commit_path.unlink()
    recovered = training.run_s106_training(
        workspace_root=workspace,
        config_path=config_path,
    )
    assert commit_path.is_file()
    assert recovered["generation_id"] == first["generation_id"]
    assert recovered["commit_sha256"] == first["commit_sha256"]


def test_atomic_registry_claim_refuses_a_concurrent_writer(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry/metadata.json"
    first = {"schema_version": 1, "status": "running", "owner": "first"}
    training._publish_new_atomic_json(  # noqa: SLF001 - concurrency regression
        registry_path,
        first,
    )
    with pytest.raises(training.S106TrainingError, match="concurrent"):
        training._publish_new_atomic_json(  # noqa: SLF001
            registry_path,
            {"schema_version": 1, "status": "running", "owner": "second"},
        )
    assert json.loads(registry_path.read_text(encoding="utf-8")) == first
    assert not list(registry_path.parent.glob(".*.part-*"))


def test_partial_seed_presence_fails_and_writes_failure_registry(
    tmp_path: Path,
) -> None:
    workspace, config_path = _build_workspace(tmp_path, partial_seed=True)
    with pytest.raises(training.S106TrainingError, match="row count differs"):
        training.run_s106_training(
            workspace_root=workspace,
            config_path=config_path,
        )
    config = contract.load_config(config_path)
    registry = (
        workspace
        / config["outputs"]["registry_root"]
        / config["outputs"]["planned_registry_run_id"]
        / "metadata.json"
    )
    document = json.loads(registry.read_text(encoding="utf-8"))
    assert document["status"] == "failed"
    assert document["failure"]["type"] == "S106TrainingError"
    assert document["artifacts"] == []
    assert not (workspace / config["outputs"]["commit_manifest"]).exists()
    generation_root = workspace / config["outputs"]["generation_root"]
    assert not list(generation_root.glob(".part-*"))


def test_forbidden_label_column_fails_closed(tmp_path: Path) -> None:
    workspace, config_path = _build_workspace(
        tmp_path,
        add_forbidden_label_column=True,
    )
    with pytest.raises(training.S106TrainingError, match="exact column"):
        training.run_s106_training(
            workspace_root=workspace,
            config_path=config_path,
        )


def test_component_speaker_mismatch_fails_exact_join(tmp_path: Path) -> None:
    workspace, config_path = _build_workspace(
        tmp_path,
        component_speaker_mismatch=True,
    )
    with pytest.raises(training.S106TrainingError, match="speaker binding"):
        training.run_s106_training(
            workspace_root=workspace,
            config_path=config_path,
        )


def test_tampered_artifact_is_rejected_by_deep_validator(tmp_path: Path) -> None:
    workspace, config_path = _build_workspace(tmp_path)
    result = training.run_s106_training(
        workspace_root=workspace,
        config_path=config_path,
    )
    config = contract.load_config(config_path)
    report_path = (
        workspace
        / config["outputs"]["generation_root"]
        / str(result["generation_id"])
        / "oof_aggregate_report.json"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["selected_oof_metric"] = 0.0
    _write_json(report_path, report)
    with pytest.raises(training.S106TrainingError):
        training.validate_s106_commit(
            workspace_root=workspace,
            config_path=config_path,
        )


def test_full_stack_metric_claim_is_rejected(tmp_path: Path) -> None:
    workspace, config_path = _build_workspace(tmp_path)
    result = training.run_s106_training(
        workspace_root=workspace,
        config_path=config_path,
    )
    config = contract.load_config(config_path)
    report_path = (
        workspace
        / config["outputs"]["generation_root"]
        / str(result["generation_id"])
        / "stack_only_report.json"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["final_fit"]["in_sample_metric_reported"] = True
    report["final_fit"]["in_sample_metric"] = 1.0
    _write_json(report_path, report)
    with pytest.raises(training.S106TrainingError):
        training.validate_s106_commit(
            workspace_root=workspace,
            config_path=config_path,
        )


def test_execution_lock_tamper_stops_before_bundle_rows(tmp_path: Path) -> None:
    workspace, config_path = _build_workspace(tmp_path)
    config = contract.load_config(config_path)
    lock_path = workspace / config["outputs"]["execution_lock"]
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["access_ledger"]["fusion_input_access"]["locked_test_read"] = True
    _write_json(lock_path, lock)
    with pytest.raises(training.S106TrainingError, match="access ledger"):
        training.run_s106_training(
            workspace_root=workspace,
            config_path=config_path,
        )
    registry_path = (
        workspace
        / config["outputs"]["registry_root"]
        / config["outputs"]["planned_registry_run_id"]
        / "metadata.json"
    )
    assert not registry_path.exists()


def test_component_bundle_cannot_change_frozen_lineage(tmp_path: Path) -> None:
    workspace, config_path = _build_workspace(tmp_path)
    config = contract.load_config(config_path)
    bundle_path = workspace / config["inputs"]["component_bundle_commit"]
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["frozen_components"]["s104_adapter"]["selected_variant"] = "large"
    bundle.pop("binding_sha256")
    bundle["binding_sha256"] = hashlib.sha256(
        contract.canonical_json_bytes(bundle, pretty=False)
    ).hexdigest()
    _write_json(bundle_path, bundle)
    _seal(bundle_path)
    with pytest.raises(training.S106TrainingError, match="lineage"):
        training.run_s106_training(
            workspace_root=workspace,
            config_path=config_path,
        )


def test_bundle_requires_canonical_generator_commit_field(tmp_path: Path) -> None:
    workspace, config_path = _build_workspace(tmp_path)
    config = contract.load_config(config_path)
    bundle_path = workspace / config["inputs"]["component_bundle_commit"]
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle.pop("generator_commit")
    _rebind(bundle)
    _write_json(bundle_path, bundle)
    _seal(bundle_path)
    with pytest.raises(training.S106TrainingError, match="schema"):
        training.run_s106_training(
            workspace_root=workspace,
            config_path=config_path,
        )


def test_generator_producer_identity_cannot_be_consistently_reminted(
    tmp_path: Path,
) -> None:
    workspace, config_path = _build_workspace(tmp_path)
    config = contract.load_config(config_path)

    def mutate(commit: dict[str, Any]) -> None:
        commit["producer_identity"]["backend_factory"] = "attacker.fake_backend"

    _rewrite_generator_dag(
        workspace,
        config,
        commit_mutator=mutate,
        sync_producer_identity=True,
    )
    with pytest.raises(training.S106TrainingError, match="producer identity"):
        training.run_s106_training(
            workspace_root=workspace,
            config_path=config_path,
        )


def test_generator_label_record_cannot_be_consistently_redirected(
    tmp_path: Path,
) -> None:
    workspace, config_path = _build_workspace(tmp_path)
    config = contract.load_config(config_path)
    bundle = json.loads(
        (workspace / config["inputs"]["component_bundle_commit"]).read_text(
            encoding="utf-8"
        )
    )
    canonical_label = workspace / bundle["label_matrix"]["path"]
    redirected_label = workspace / "artifacts/attacker/redirected-labels.parquet"
    redirected_label.parent.mkdir(parents=True)
    shutil.copyfile(canonical_label, redirected_label)
    _seal(redirected_label)
    redirected_record = _file_record(
        redirected_label,
        workspace,
        int(bundle["label_matrix"]["row_count"]),
    )
    _rewrite_generator_dag(
        workspace,
        config,
        commit_mutator=lambda commit: commit.update(
            {"label_matrix": redirected_record}
        ),
        bundle_mutator=lambda document: document.update(
            {"label_matrix": redirected_record}
        ),
    )
    with pytest.raises(
        training.S106TrainingError,
        match="component producer evidence tree differs",
    ):
        training.run_s106_training(
            workspace_root=workspace,
            config_path=config_path,
        )


def test_generator_commit_excluded_counts_are_exact_and_nonnegative(
    tmp_path: Path,
) -> None:
    workspace, config_path = _build_workspace(tmp_path)
    config = contract.load_config(config_path)
    _rewrite_generator_dag(
        workspace,
        config,
        commit_mutator=lambda commit: commit.update(
            {"excluded_utterances": {"qc_flagged": -1, "reserved_prompt": 0}}
        ),
    )
    with pytest.raises(training.S106TrainingError, match="commit lineage"):
        training.run_s106_training(
            workspace_root=workspace,
            config_path=config_path,
        )


@pytest.mark.parametrize(
    "registry_mutator",
    [
        lambda registry: registry.update({"run_id": "attacker-run"}),
        lambda registry: registry.update(
            {"artifact_counts": {**registry["artifact_counts"], "label_matrix": 2}}
        ),
        lambda registry: registry.update(
            {"privacy": {**registry["privacy"], "raw_identifiers_published": True}}
        ),
        lambda registry: registry.update(
            {"commit": {**registry["commit"], "extra": "forbidden"}}
        ),
    ],
)
def test_generator_registry_exact_semantics_are_required(
    tmp_path: Path,
    registry_mutator: Callable[[dict[str, Any]], None],
) -> None:
    workspace, config_path = _build_workspace(tmp_path)
    config = contract.load_config(config_path)
    _rewrite_generator_dag(
        workspace,
        config,
        registry_mutator=registry_mutator,
    )
    with pytest.raises(training.S106TrainingError, match="registry lineage"):
        training.run_s106_training(
            workspace_root=workspace,
            config_path=config_path,
        )


@pytest.mark.parametrize(
    ("stage", "role"),
    [
        ("s104", "config"),
        ("s104", "validator_module"),
        ("s104", "report"),
        ("s105", "config"),
        ("s105", "validator_module"),
        ("s105", "report"),
        ("b07", "config"),
        ("b07", "validator_module"),
        ("b07", "report"),
    ],
)
def test_run_rechecks_every_predecessor_canonical_source(
    tmp_path: Path,
    stage: str,
    role: str,
) -> None:
    workspace, config_path = _build_workspace(tmp_path)
    config = contract.load_config(config_path)
    lock_path = workspace / config["outputs"]["execution_lock"]
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    source_path = (
        workspace / lock["predecessors"][stage]["canonical_sources"][role]["path"]
    )
    source_path.write_bytes(source_path.read_bytes() + b"drift")
    with pytest.raises(training.S106TrainingError, match="canonical-source"):
        training.run_s106_training(
            workspace_root=workspace,
            config_path=config_path,
        )


def test_precommit_rechecks_predecessor_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path = _build_workspace(tmp_path)
    config = contract.load_config(config_path)
    lock_path = workspace / config["outputs"]["execution_lock"]
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    report_path = (
        workspace / lock["predecessors"]["s105"]["canonical_sources"]["report"]["path"]
    )
    original = training._precommit_revalidate

    def drift_then_revalidate(**kwargs: Any) -> None:
        report_path.write_bytes(report_path.read_bytes() + b"drift")
        original(**kwargs)

    monkeypatch.setattr(training, "_precommit_revalidate", drift_then_revalidate)
    with pytest.raises(training.S106TrainingError, match="canonical-source"):
        training.run_s106_training(
            workspace_root=workspace,
            config_path=config_path,
        )
    commit_path = workspace / config["outputs"]["commit_manifest"]
    assert not commit_path.exists()


def test_commit_only_validation_rechecks_predecessor_report(
    tmp_path: Path,
) -> None:
    workspace, config_path = _build_workspace(tmp_path)
    training.run_s106_training(
        workspace_root=workspace,
        config_path=config_path,
    )
    config = contract.load_config(config_path)
    lock_path = workspace / config["outputs"]["execution_lock"]
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    report_path = (
        workspace / lock["predecessors"]["b07"]["canonical_sources"]["report"]["path"]
    )
    report_path.write_bytes(report_path.read_bytes() + b"drift")
    with pytest.raises(training.S106TrainingError, match="canonical-source"):
        training.validate_s106_commit(
            workspace_root=workspace,
            config_path=config_path,
        )
