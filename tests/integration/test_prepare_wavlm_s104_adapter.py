from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml

from voice_coach.training import wavlm_adapter_contract as contract


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/experiments/wavlm_s104_adapter_v1.yaml"
SCRIPT = ROOT / "scripts/prepare_wavlm_s104_adapter.py"
CONTRACT_SOURCE = ROOT / "src/voice_coach/training/wavlm_adapter_contract.py"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _canonical(value: dict[str, Any], *, pretty: bool) -> bytes:
    options: dict[str, Any] = {
        "ensure_ascii": False,
        "sort_keys": True,
        "allow_nan": False,
    }
    if pretty:
        options["indent"] = 2
    return (json.dumps(value, **options) + "\n").encode()


def _record(workspace: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(workspace).as_posix(),
        "sha256": _sha(path),
        "size_bytes": path.stat().st_size,
    }


def _speaker(prefix: str, bucket: int, modulus: int = 10) -> str:
    for index in range(100_000):
        value = hashlib.sha256(f"{prefix}-{index}".encode()).hexdigest()
        if int(value[:16], 16) % modulus == bucket:
            return value
    raise AssertionError("speaker bucket fixture failed")


def _source_record_files(
    workspace: Path,
    *,
    registry_root: Path | None = None,
) -> dict[str, Any]:
    source_root = workspace / "artifacts/s103/sources"
    source_root.mkdir(parents=True)

    def create_path(name: str) -> Path:
        path = source_root / name
        path.write_bytes(f"fixture:{name}\n".encode())
        return path

    def create(name: str) -> dict[str, Any]:
        return _record(workspace, create_path(name))

    sources: dict[str, Any] = {
        "config": create("comparison.yaml"),
        "comparison_script": create("compare.py"),
        "comparison_module": create("comparison_module.py"),
    }
    for variant in ("base_plus", "large"):
        predictions: dict[str, Any] = {}
        artifact_records: list[dict[str, Any]] = []
        for seed in (42, 43, 44):
            phone = create(f"{variant}-seed-{seed}-phone.parquet")
            utterance = create(f"{variant}-seed-{seed}-utterance.parquet")
            predictions[f"seed_{seed}"] = {
                "phone": phone,
                "utterance": utterance,
            }
            artifact_records.extend(
                [
                    {
                        **create(f"{variant}-seed-{seed}-checkpoint.pt"),
                        "name": f"seed_{seed}_checkpoint",
                        "kind": "checkpoint",
                    },
                    {
                        **phone,
                        "name": f"seed_{seed}_phone_predictions",
                        "kind": "evaluation",
                    },
                    {
                        **utterance,
                        "name": f"seed_{seed}_utterance_predictions",
                        "kind": "evaluation",
                    },
                    {
                        **create(f"{variant}-seed-{seed}-slice.json"),
                        "name": f"seed_{seed}_slice_metrics",
                        "kind": "evaluation",
                    },
                ]
            )
        report_path = create_path(f"{variant}-report.json")
        report_record = _record(workspace, report_path)
        artifact_records.append(
            {
                **report_record,
                "name": "final_report",
                "kind": "evaluation_report",
            }
        )
        input_config = create(f"{variant}-train-config.yaml")
        input_data = [
            create(f"{variant}-input-data-{index:02d}.parquet") for index in range(33)
        ]
        input_model = [
            create(f"{variant}-trainer.py"),
            create(f"{variant}-head.py"),
        ]
        run_id = f"synthetic-{variant}-head"
        registry_path = (
            registry_root / run_id / "metadata.json"
            if registry_root is not None
            else source_root / f"{variant}-registry.json"
        )
        _write_json(
            registry_path,
            {
                "schema_version": 1,
                "run_id": run_id,
                "status": "completed",
                "inputs": {
                    "config": [input_config],
                    "data": input_data,
                    "model": input_model,
                    "checkpoint": [],
                },
                "artifacts": artifact_records,
            },
        )
        registry_record = {
            **_record(workspace, registry_path),
            "run_id": run_id,
            "status": "completed",
        }
        sources[variant] = {
            "report": report_record,
            "registry": registry_record,
            "predictions": predictions,
        }
    return sources


def _s103_fixture(
    workspace: Path,
    *,
    passed: bool,
    registry_root: Path | None = None,
) -> Path:
    estimate = 0.031 if passed else 0.029
    sources = _source_record_files(workspace, registry_root=registry_root)
    report: dict[str, Any] = {
        "schema_version": contract.S103_REPORT_SCHEMA_VERSION,
        "status": "completed",
        "development_only": True,
        "oracle_alignment": True,
        "sources": sources,
        "contract": {
            "leakage": {
                "stack_accessed": False,
                "calibration_accessed": False,
                "locked_test_accessed": False,
                "raw_data_read": False,
                "environment_files_read": [],
            }
        },
        "metrics": {"aggregate": {"fixture": 1}},
        "primary_comparison": {
            "metric": "phone_macro_f1",
            "effect": "large_minus_base",
            "paired_speaker_bootstrap": {"estimate": estimate},
            "gate": {
                "threshold_absolute": 0.03,
                "rule": "point_estimate_gte_threshold",
                "point_estimate_passed": passed,
                "status": "passed" if passed else "failed",
            },
        },
        "multiplicity": {"method": "holm"},
        "summary": {
            "large_adoption_gate_passed": passed,
            "locked_test_remains_unread": True,
            "stack_remains_unread": True,
            "calibration_remains_unread": True,
        },
    }
    report_bytes = _canonical(report, pretty=True)
    generation_id = hashlib.sha256(report_bytes).hexdigest()
    generation = workspace / "artifacts/s103/generations" / generation_id
    generation.mkdir(parents=True)
    report_path = generation / "report.json"
    report_path.write_bytes(report_bytes)
    statistics = {
        "metrics": report["metrics"],
        "primary_comparison": report["primary_comparison"],
        "multiplicity": report["multiplicity"],
    }
    statistics_sha = hashlib.sha256(_canonical(statistics, pretty=False)).hexdigest()
    receipt: dict[str, Any] = {
        "schema_version": contract.S103_RECEIPT_SCHEMA_VERSION,
        "status": "completed",
        "generation_id": generation_id,
        "comparison_report": _record(workspace, report_path),
        "execution_sources": sources,
        "statistics_sha256": statistics_sha,
    }
    receipt["binding_sha256"] = hashlib.sha256(
        _canonical(receipt, pretty=False)
    ).hexdigest()
    receipt_path = generation / "execution_receipt.json"
    receipt_path.write_bytes(_canonical(receipt, pretty=True))
    commit: dict[str, Any] = {
        "schema_version": contract.S103_COMMIT_SCHEMA_VERSION,
        "status": "committed",
        "generation_id": generation_id,
        "comparison_report": _record(workspace, report_path),
        "execution_receipt": _record(workspace, receipt_path),
        "statistics_sha256": statistics_sha,
    }
    commit["binding_sha256"] = hashlib.sha256(
        _canonical(commit, pretty=False)
    ).hexdigest()
    commit_path = workspace / "artifacts/s103/commit.json"
    _write_json(commit_path, commit)
    return commit_path


def _manifest_schema() -> pa.Schema:
    interval = pa.list_(
        pa.struct(
            [
                pa.field("start", pa.float64()),
                pa.field("end", pa.float64()),
                pa.field("label", pa.string()),
            ]
        )
    )
    alignments = pa.list_(
        pa.struct(
            [
                pa.field("target_label", pa.string()),
                pa.field("realized_label", pa.string()),
                pa.field("operation", pa.string()),
            ]
        )
    )
    return pa.schema(
        [
            pa.field("id", pa.string(), nullable=False),
            pa.field("speaker_id", pa.string(), nullable=False),
            pa.field("split", pa.string(), nullable=False),
            pa.field("audio_path", pa.string(), nullable=False),
            pa.field("pcm_sha256", pa.string(), nullable=False),
            pa.field("prompt_sha256", pa.string(), nullable=False),
            pa.field("qc_flags", pa.list_(pa.string()), nullable=False),
            pa.field("l1", pa.string(), nullable=False),
            pa.field("gender", pa.string(), nullable=False),
            pa.field("proficiency", pa.string(), nullable=False),
            pa.field("target_phone_intervals", interval, nullable=False),
            pa.field("phone_alignments", alignments, nullable=False),
        ]
    )


def _data_fixture(workspace: Path) -> tuple[Path, Path, Path, int]:
    dataset_root = workspace / "data/restricted/aihub/dataset-71490"
    dataset_root.mkdir(parents=True)
    manifest_path = workspace / "data/processed/modeling.parquet"
    manifest_path.parent.mkdir(parents=True)
    prompt_reserved = hashlib.sha256(b"reserved").hexdigest()
    prompt_used = hashlib.sha256(b"used").hexdigest()
    fit_speaker = _speaker("fit", 4)
    inner_speaker = _speaker("inner", 0)
    dev_speaker = _speaker("dev", 7)
    protected_speaker = _speaker("protected", 8)
    rows = [
        {
            "id": "fit",
            "speaker_id": fit_speaker,
            "split": "train",
            "audio_path": "fit.wav",
            "pcm_sha256": _sha_bytes(b"fit-pcm"),
            "prompt_sha256": prompt_used,
            "qc_flags": [],
            "l1": "Chinese",
            "gender": "F",
            "proficiency": "beginner",
            "target_phone_intervals": [
                {"start": 0.0, "end": 0.1, "label": "AA"},
                {"start": 0.1, "end": 0.2, "label": "AA"},
            ],
            "phone_alignments": [
                {
                    "target_label": "AA",
                    "realized_label": "BB",
                    "operation": "substitution",
                },
                {
                    "target_label": "AA",
                    "realized_label": "AA",
                    "operation": "match",
                },
            ],
        },
        {
            "id": "inner",
            "speaker_id": inner_speaker,
            "split": "train",
            "audio_path": "inner.wav",
            "pcm_sha256": _sha_bytes(b"inner-pcm"),
            "prompt_sha256": prompt_used,
            "qc_flags": [],
            "l1": "Japanese",
            "gender": "M",
            "proficiency": "intermediate",
            "target_phone_intervals": [],
            "phone_alignments": [],
        },
        {
            "id": "dev",
            "speaker_id": dev_speaker,
            "split": "dev",
            "audio_path": "dev.wav",
            "pcm_sha256": _sha_bytes(b"dev-pcm"),
            "prompt_sha256": prompt_used,
            "qc_flags": [],
            "l1": "Chinese",
            "gender": "M",
            "proficiency": "advanced",
            "target_phone_intervals": [{"start": 0.0, "end": 0.1, "label": "AA"}],
            "phone_alignments": [
                {
                    "target_label": "AA",
                    "realized_label": "BB",
                    "operation": "substitution",
                }
            ],
        },
        {
            "id": "reserved",
            "speaker_id": fit_speaker,
            "split": "train",
            "audio_path": "reserved.wav",
            "pcm_sha256": _sha_bytes(b"reserved-pcm"),
            "prompt_sha256": prompt_reserved,
            "qc_flags": [],
            "l1": "Chinese",
            "gender": "F",
            "proficiency": "beginner",
            "target_phone_intervals": [],
            "phone_alignments": [],
        },
        {
            "id": "qc",
            "speaker_id": fit_speaker,
            "split": "dev",
            "audio_path": "qc.wav",
            "pcm_sha256": _sha_bytes(b"qc-pcm"),
            "prompt_sha256": prompt_used,
            "qc_flags": ["bad"],
            "l1": "Japanese",
            "gender": "M",
            "proficiency": "advanced",
            "target_phone_intervals": [],
            "phone_alignments": [],
        },
        {
            "id": "protected",
            "speaker_id": protected_speaker,
            "split": "stack",
            "audio_path": "must-never-open.wav",
            "pcm_sha256": _sha_bytes(b"protected-pcm"),
            "prompt_sha256": prompt_used,
            "qc_flags": [],
            "l1": "Chinese",
            "gender": "F",
            "proficiency": "advanced",
            "target_phone_intervals": [],
            "phone_alignments": [],
        },
    ]
    pq.write_table(pa.Table.from_pylist(rows, schema=_manifest_schema()), manifest_path)
    manifest_sha = _sha(manifest_path)

    reservation_path = workspace / "data/processed/reservation.json"
    _write_json(
        reservation_path,
        {
            "source": {"modeling_manifest_sha256": manifest_sha},
            "reserved_prompts": [{"prompt_sha256": prompt_reserved}],
        },
    )

    explicit_path = workspace / "data/processed/explicit.parquet"
    explicit_rows = []
    for index, (identifier, speaker, split) in enumerate(
        [
            ("fit", fit_speaker, "train"),
            ("inner", inner_speaker, "train"),
            ("dev", dev_speaker, "dev"),
        ]
    ):
        explicit_rows.append(
            {
                "utterance_id_sha256": hashlib.sha256(
                    (f"{contract.UTTERANCE_HASH_NAMESPACE}:{identifier}").encode()
                ).hexdigest(),
                "speaker_id_sha256": speaker,
                "split": split,
                "pronunciation_score": 1 + index,
                "fluency_score": 2 + index,
                "comprehensibility_score": 3 + index,
            }
        )
    explicit_schema = pa.schema(
        [
            pa.field("utterance_id_sha256", pa.string(), nullable=False),
            pa.field("speaker_id_sha256", pa.string(), nullable=False),
            pa.field("split", pa.string(), nullable=False),
            pa.field("pronunciation_score", pa.int8()),
            pa.field("fluency_score", pa.int8()),
            pa.field("comprehensibility_score", pa.int8()),
        ],
        metadata={
            b"voice_coach.allowed_splits": b"train,dev",
            b"voice_coach.source_manifest_sha256": manifest_sha.encode(),
        },
    )
    pq.write_table(
        pa.Table.from_pylist(explicit_rows, schema=explicit_schema),
        explicit_path,
    )
    return manifest_path, reservation_path, explicit_path, len(explicit_rows)


def _model_cache_fixture(
    workspace: Path,
    config: dict[str, Any],
) -> None:
    cache_root = workspace / "data/cache/huggingface/hub"
    for variant in ("base_plus", "large"):
        candidate = config["candidates"][variant]
        repository = cache_root / (
            f"models--{candidate['model_id'].replace('/', '--')}"
        )
        blobs = repository / "blobs"
        snapshot = repository / "snapshots" / candidate["revision"]
        blobs.mkdir(parents=True)
        snapshot.mkdir(parents=True)
        config_document = {
            "model_type": "wavlm",
            "hidden_size": candidate["hidden_size"],
            "num_hidden_layers": candidate["encoder_layer_count"],
            "conv_kernel": [10, 3, 3, 3, 3, 2, 2],
            "conv_stride": [5, 2, 2, 2, 2, 2, 2],
            "layerdrop": 0.05 if variant == "base_plus" else 0.1,
            "apply_spec_augment": True,
        }
        config_bytes = _canonical(config_document, pretty=True)
        config_sha = hashlib.sha256(config_bytes).hexdigest()
        config_blob = blobs / config_sha
        config_blob.write_bytes(config_bytes)
        checkpoint_bytes = f"synthetic checkpoint:{variant}\n".encode()
        checkpoint_sha = hashlib.sha256(checkpoint_bytes).hexdigest()
        checkpoint_blob = blobs / checkpoint_sha
        checkpoint_blob.write_bytes(checkpoint_bytes)
        (snapshot / "config.json").symlink_to(Path("../../blobs") / config_sha)
        (snapshot / "pytorch_model.bin").symlink_to(
            Path("../../blobs") / checkpoint_sha
        )
        candidate["cache_dir"] = cache_root.relative_to(workspace).as_posix()
        candidate["source_config_sha256"] = config_sha
        candidate["source_checkpoint_sha256"] = checkpoint_sha


def _fixture(
    tmp_path: Path,
    *,
    passed: bool = True,
    shared_registry_root: bool = False,
) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    implementation_copies: dict[str, Path] = {}
    for role, record in config["implementation"].items():
        source = ROOT / record["path"]
        target = workspace / record["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        implementation_copies[role] = target

    _model_cache_fixture(workspace, config)
    registry_root = workspace / "artifacts/registry"
    commit = _s103_fixture(
        workspace,
        passed=passed,
        registry_root=registry_root if shared_registry_root else None,
    )
    manifest, reservation, explicit, included = _data_fixture(workspace)
    config["s103"]["commit_manifest"] = commit.relative_to(workspace).as_posix()
    config["data"].update(
        {
            "dataset_root": "data/restricted/aihub/dataset-71490",
            "modeling_manifest": manifest.relative_to(workspace).as_posix(),
            "modeling_manifest_sha256": _sha(manifest),
            "prompt_challenge_reservation": reservation.relative_to(
                workspace
            ).as_posix(),
            "prompt_challenge_reservation_sha256": _sha(reservation),
            "explicit_utterance_labels": explicit.relative_to(workspace).as_posix(),
            "explicit_utterance_labels_sha256": _sha(explicit),
            "expected_included_utterances": included,
        }
    )
    for role, path in implementation_copies.items():
        config["implementation"][role] = {
            "path": path.relative_to(workspace).as_posix(),
            "sha256": _sha(path),
        }
    config["outputs"]["execution_lock"] = "artifacts/s104/execution_lock.json"
    config["outputs"]["generation_root"] = "artifacts/s104/generations"
    config["outputs"]["commit_manifest"] = "artifacts/s104/commit.json"
    config["outputs"]["registry_root"] = registry_root.relative_to(workspace).as_posix()
    config_path = workspace / "configs/s104.yaml"
    config_path.parent.mkdir()
    config_path.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return workspace, config_path


def test_repository_config_is_valid_and_cli_config_only_is_read_only() -> None:
    document = contract.load_config(CONFIG)
    contract.validate_config(document)
    before = set(ROOT.rglob("execution_lock.json"))
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config",
            str(CONFIG),
            "--workspace-root",
            str(ROOT),
            "--config-only",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["status"] == "config_valid"
    assert output["gpu_training_executed"] is False
    assert output["raw_audio_opened"] is False
    assert output["s103_read"] is False
    assert output["data_read"] is False
    assert output["output_written"] is False
    assert set(ROOT.rglob("execution_lock.json")) == before


@pytest.mark.parametrize(
    ("passed", "expected_candidate"),
    [(True, "large"), (False, "base_plus")],
)
def test_full_preflight_resolves_branch_and_writes_aggregate_lock(
    tmp_path: Path,
    passed: bool,
    expected_candidate: str,
) -> None:
    workspace, config_path = _fixture(tmp_path, passed=passed)
    payload, output = contract.prepare_s104_execution(
        workspace_root=workspace,
        config_path=config_path,
    )
    assert payload["status"] == "ready_for_s104_gpu_training"
    assert payload["selected_candidate"]["variant"] == expected_candidate
    assert payload["data_contract"]["included_utterances"] == 3
    assert payload["data_contract"]["speaker_overlap_train_dev"] == 0
    assert payload["data_contract"]["speaker_overlap_inner_train_validation"] == 0
    assert payload["data_contract"]["inner_validation_fraction"] == (
        payload["data_contract"]["inner_validation_rows"]
        / payload["data_contract"]["primary_split_rows"]["train"]
    )
    assert payload["privacy"] == {
        "scope": "execution_lock_payload_only",
        "aggregate_only": True,
        "direct_identifiers_written": False,
        "pseudonymous_row_ids_written": False,
        "raw_locators_written": False,
        "environment_files_read": [],
        "stack_accessed": False,
        "calibration_accessed": False,
        "locked_test_accessed": False,
    }
    serialized = output.read_text(encoding="utf-8")
    assert "must-never-open" not in serialized
    assert "audio_path" not in serialized
    assert "utterance_id_sha256" not in serialized
    assert "speaker_id_sha256" not in serialized
    assert "data/restricted" not in serialized

    # The exact same invocation is idempotent.
    repeated, repeated_path = contract.prepare_s104_execution(
        workspace_root=workspace,
        config_path=config_path,
    )
    assert repeated == payload
    assert repeated_path == output


def test_preflight_fails_closed_when_s103_commit_is_absent(tmp_path: Path) -> None:
    workspace, config_path = _fixture(tmp_path)
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    document["s103"]["commit_manifest"] = "artifacts/s103/missing.json"
    config_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    output = workspace / "artifacts/s104/execution_lock.json"
    with pytest.raises(contract.S104ContractError, match="does not exist"):
        contract.prepare_s104_execution(
            workspace_root=workspace,
            config_path=config_path,
        )
    assert not output.exists()


def test_preflight_rejects_overlapping_generation_and_registry_roots(
    tmp_path: Path,
) -> None:
    workspace, config_path = _fixture(tmp_path)
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    document["outputs"]["registry_root"] = document["outputs"]["generation_root"]
    config_path.write_text(
        yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(
        contract.S104ContractError,
        match="distinct|roots overlap",
    ):
        contract.prepare_s104_execution(
            workspace_root=workspace,
            config_path=config_path,
        )
    assert not (workspace / document["outputs"]["execution_lock"]).exists()


def test_preflight_allows_shared_registry_root_with_predecessor_runs(
    tmp_path: Path,
) -> None:
    workspace, config_path = _fixture(tmp_path, shared_registry_root=True)
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    registry_root = workspace / document["outputs"]["registry_root"]
    predecessor_metadata = sorted(registry_root.glob("*/metadata.json"))
    before = {path: path.read_bytes() for path in predecessor_metadata}

    payload, output = contract.prepare_s104_execution(
        workspace_root=workspace,
        config_path=config_path,
    )

    assert payload["status"] == "ready_for_s104_gpu_training"
    assert output.is_file()
    assert len(predecessor_metadata) == 2
    assert {path: path.read_bytes() for path in predecessor_metadata} == before


def test_preflight_rejects_planned_registry_run_collision_with_predecessor(
    tmp_path: Path,
) -> None:
    workspace, config_path = _fixture(tmp_path, shared_registry_root=True)
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    document["outputs"]["planned_registry_run_id"] = "synthetic-large-head"
    config_path.write_text(
        yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(
        contract.S104ContractError,
        match="planned registry run directory overlaps an input",
    ):
        contract.prepare_s104_execution(
            workspace_root=workspace,
            config_path=config_path,
        )
    assert not (workspace / document["outputs"]["execution_lock"]).exists()


def test_preflight_rejects_nonordinal_explicit_score(tmp_path: Path) -> None:
    workspace, config_path = _fixture(tmp_path)
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    explicit = workspace / document["data"]["explicit_utterance_labels"]
    source = pq.read_table(explicit)
    rows = source.to_pylist()
    rows[0]["pronunciation_score"] = 2.5
    schema = pa.schema(
        [
            pa.field("utterance_id_sha256", pa.string(), nullable=False),
            pa.field("speaker_id_sha256", pa.string(), nullable=False),
            pa.field("split", pa.string(), nullable=False),
            pa.field("pronunciation_score", pa.float64()),
            pa.field("fluency_score", pa.float64()),
            pa.field("comprehensibility_score", pa.float64()),
        ],
        metadata=source.schema.metadata,
    )
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), explicit)
    document["data"]["explicit_utterance_labels_sha256"] = _sha(explicit)
    config_path.write_text(
        yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(contract.S104ContractError, match="ordinal score"):
        contract.prepare_s104_execution(
            workspace_root=workspace,
            config_path=config_path,
        )


def test_preflight_rejects_inconsistent_s103_gate(tmp_path: Path) -> None:
    workspace, config_path = _fixture(tmp_path)
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    commit_path = workspace / document["s103"]["commit_manifest"]
    commit = json.loads(commit_path.read_text(encoding="utf-8"))
    report_path = workspace / commit["comparison_report"]["path"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["primary_comparison"]["gate"]["point_estimate_passed"] = False
    # Deliberately leave commit/receipt fingerprints unchanged.
    _write_json(report_path, report)
    with pytest.raises(
        contract.S104ContractError, match="size differs|SHA-256 differs"
    ):
        contract.prepare_s104_execution(
            workspace_root=workspace,
            config_path=config_path,
        )


def test_preflight_requires_report_and_receipt_source_identity(
    tmp_path: Path,
) -> None:
    workspace, config_path = _fixture(tmp_path)
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    commit_path = workspace / document["s103"]["commit_manifest"]
    commit = json.loads(commit_path.read_text(encoding="utf-8"))
    receipt_path = workspace / commit["execution_receipt"]["path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    alternate = workspace / "artifacts/s103/alternate-comparison.yaml"
    alternate.parent.mkdir(parents=True, exist_ok=True)
    alternate.write_text("alternate\n", encoding="utf-8")
    receipt["execution_sources"]["config"] = _record(workspace, alternate)
    receipt.pop("binding_sha256")
    receipt["binding_sha256"] = hashlib.sha256(
        _canonical(receipt, pretty=False)
    ).hexdigest()
    receipt_path.write_bytes(_canonical(receipt, pretty=True))
    commit["execution_receipt"] = _record(workspace, receipt_path)
    commit.pop("binding_sha256")
    commit["binding_sha256"] = hashlib.sha256(
        _canonical(commit, pretty=False)
    ).hexdigest()
    commit_path.write_bytes(_canonical(commit, pretty=True))
    with pytest.raises(
        contract.S104ContractError,
        match="report and receipt execution sources differ",
    ):
        contract.prepare_s104_execution(
            workspace_root=workspace,
            config_path=config_path,
        )


def test_preflight_rejects_protected_or_colliding_output(tmp_path: Path) -> None:
    workspace, config_path = _fixture(tmp_path)
    with pytest.raises(contract.S104ContractError, match="protected data path"):
        contract.prepare_s104_execution(
            workspace_root=workspace,
            config_path=config_path,
            output_path=Path("artifacts/stack/execution_lock.json"),
        )
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    collision = Path(document["data"]["modeling_manifest"])
    with pytest.raises(contract.S104ContractError, match="overwrite an input"):
        contract.prepare_s104_execution(
            workspace_root=workspace,
            config_path=config_path,
            output_path=collision,
        )
    assert (workspace / collision).is_file()


def test_preflight_protects_registry_transitive_input_and_detects_tamper(
    tmp_path: Path,
) -> None:
    workspace, config_path = _fixture(tmp_path)
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    commit = json.loads(
        (workspace / document["s103"]["commit_manifest"]).read_text(encoding="utf-8")
    )
    report = json.loads(
        (workspace / commit["comparison_report"]["path"]).read_text(encoding="utf-8")
    )
    registry_path = workspace / report["sources"]["large"]["registry"]["path"]
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    transitive_input = workspace / registry["inputs"]["data"][0]["path"]
    original = transitive_input.read_bytes()

    with pytest.raises(contract.S104ContractError, match="overwrite an input"):
        contract.prepare_s104_execution(
            workspace_root=workspace,
            config_path=config_path,
            output_path=transitive_input,
        )
    assert transitive_input.read_bytes() == original

    transitive_input.write_bytes(original + b"tamper")
    with pytest.raises(
        contract.S104ContractError,
        match="size differs|SHA-256 differs",
    ):
        contract.prepare_s104_execution(
            workspace_root=workspace,
            config_path=config_path,
        )


@pytest.mark.parametrize(
    "environment_component",
    [".env", ".env.local", ".env_backup", ".env-local", ".envrc"],
)
def test_preflight_rejects_every_environment_like_input_and_output_component(
    tmp_path: Path,
    environment_component: str,
) -> None:
    workspace, config_path = _fixture(tmp_path)
    with pytest.raises(contract.S104ContractError, match="protected data path"):
        contract.prepare_s104_execution(
            workspace_root=workspace,
            config_path=config_path,
            output_path=Path("artifacts") / environment_component / "lock.json",
        )

    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    original = workspace / document["implementation"]["adapter_module"]["path"]
    hidden = workspace / "src" / environment_component / "adapter.py"
    hidden.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(original, hidden)
    document["implementation"]["adapter_module"] = {
        "path": hidden.relative_to(workspace).as_posix(),
        "sha256": _sha(hidden),
    }
    config_path.write_text(
        yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(
        contract.S104ContractError,
        match="environment file",
    ):
        contract.prepare_s104_execution(
            workspace_root=workspace,
            config_path=config_path,
        )


def test_preflight_rejects_safe_alias_to_environment_like_source(
    tmp_path: Path,
) -> None:
    workspace, config_path = _fixture(tmp_path)
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    original = workspace / document["implementation"]["adapter_module"]["path"]
    hidden_directory = workspace / "src/.env_backup"
    hidden_directory.mkdir(parents=True)
    hidden = hidden_directory / "adapter.py"
    shutil.copyfile(original, hidden)
    alias = workspace / "src/safe-alias"
    alias.symlink_to(hidden_directory.name)
    aliased_source = alias / hidden.name
    document["implementation"]["adapter_module"] = {
        "path": aliased_source.relative_to(workspace).as_posix(),
        "sha256": _sha(aliased_source),
    }
    config_path.write_text(
        yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(
        contract.S104ContractError,
        match="environment file",
    ):
        contract.prepare_s104_execution(
            workspace_root=workspace,
            config_path=config_path,
        )


def test_config_rejects_posthoc_primary_outcome_change() -> None:
    document = contract.load_config(CONFIG)
    document["evaluation"]["primary_utterance_outcome"] = "fluency_score"
    with pytest.raises(contract.S104ContractError, match="primary_utterance_outcome"):
        contract.validate_config(document)


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("data", "pooled_feature_cache_allowed_for_encoder_gradient", True),
        ("data", "representation_source", "pooled_frozen_cache"),
        ("outputs", "expected_completed_registry_artifact_count", 15),
        ("adapter", "trainable_name_patterns", [".*"]),
    ],
)
def test_config_rejects_gradient_or_artifact_contract_weakening(
    section: str,
    key: str,
    value: object,
) -> None:
    document = contract.load_config(CONFIG)
    document[section][key] = value
    with pytest.raises(contract.S104ContractError):
        contract.validate_config(document)


def test_implementation_hash_tamper_fails_before_s103_or_data(
    tmp_path: Path,
) -> None:
    workspace, config_path = _fixture(tmp_path)
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    source = workspace / document["implementation"]["adapter_module"]["path"]
    source.write_text(source.read_text(encoding="utf-8") + "\n# tamper\n")
    with pytest.raises(contract.S104ContractError, match="SHA-256 differs"):
        contract.prepare_s104_execution(
            workspace_root=workspace,
            config_path=config_path,
        )


def test_selected_model_checkpoint_tamper_fails_before_data_lock(
    tmp_path: Path,
) -> None:
    workspace, config_path = _fixture(tmp_path, passed=True)
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    candidate = document["candidates"]["large"]
    snapshot = (
        workspace
        / candidate["cache_dir"]
        / "models--microsoft--wavlm-large"
        / "snapshots"
        / candidate["revision"]
    )
    checkpoint = (snapshot / "pytorch_model.bin").resolve(strict=True)
    checkpoint.write_bytes(checkpoint.read_bytes() + b"tamper")
    with pytest.raises(
        contract.S104ContractError,
        match="checkpoint SHA-256",
    ):
        contract.prepare_s104_execution(
            workspace_root=workspace,
            config_path=config_path,
        )
    assert not (workspace / "artifacts/s104/execution_lock.json").exists()


def test_selected_model_blob_is_a_protected_output_source(tmp_path: Path) -> None:
    workspace, config_path = _fixture(tmp_path, passed=True)
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    candidate = document["candidates"]["large"]
    checkpoint_blob = (
        workspace
        / candidate["cache_dir"]
        / "models--microsoft--wavlm-large"
        / "snapshots"
        / candidate["revision"]
        / "pytorch_model.bin"
    ).resolve(strict=True)
    original = checkpoint_blob.read_bytes()
    with pytest.raises(contract.S104ContractError, match="overwrite an input"):
        contract.prepare_s104_execution(
            workspace_root=workspace,
            config_path=config_path,
            output_path=checkpoint_blob,
        )
    assert checkpoint_blob.read_bytes() == original


def test_selected_model_symlink_escape_is_rejected(tmp_path: Path) -> None:
    workspace, config_path = _fixture(tmp_path, passed=False)
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    candidate = document["candidates"]["base_plus"]
    snapshot = (
        workspace
        / candidate["cache_dir"]
        / "models--microsoft--wavlm-base-plus"
        / "snapshots"
        / candidate["revision"]
    )
    config_link = snapshot / "config.json"
    config_link.unlink()
    escaped = workspace / "escaped-config.json"
    escaped.write_text(
        json.dumps(
            {
                "model_type": "wavlm",
                "hidden_size": 768,
                "num_hidden_layers": 12,
            }
        ),
        encoding="utf-8",
    )
    config_link.symlink_to(os.path.relpath(escaped, start=snapshot))
    with pytest.raises(
        contract.S104ContractError,
        match="escapes the repository blobs",
    ):
        contract.prepare_s104_execution(
            workspace_root=workspace,
            config_path=config_path,
        )


def test_selected_model_repository_symlink_is_rejected(tmp_path: Path) -> None:
    workspace, config_path = _fixture(tmp_path, passed=True)
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    candidate = document["candidates"]["large"]
    cache = workspace / candidate["cache_dir"]
    repository = cache / "models--microsoft--wavlm-large"
    hidden = workspace / ".env_backup/model-repository"
    hidden.parent.mkdir(parents=True)
    repository.rename(hidden)
    repository.symlink_to(os.path.relpath(hidden, start=cache))
    with pytest.raises(
        contract.S104ContractError,
        match="repository may not be a symlink",
    ):
        contract.prepare_s104_execution(
            workspace_root=workspace,
            config_path=config_path,
        )


def test_selected_model_blobs_directory_symlink_is_rejected(
    tmp_path: Path,
) -> None:
    workspace, config_path = _fixture(tmp_path, passed=False)
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    candidate = document["candidates"]["base_plus"]
    repository = (
        workspace / candidate["cache_dir"] / "models--microsoft--wavlm-base-plus"
    )
    blobs = repository / "blobs"
    hidden = workspace / ".env_backup/model-blobs"
    hidden.parent.mkdir(parents=True)
    blobs.rename(hidden)
    blobs.symlink_to(os.path.relpath(hidden, start=repository))
    with pytest.raises(
        contract.S104ContractError,
        match="blobs directory may not be a symlink",
    ):
        contract.prepare_s104_execution(
            workspace_root=workspace,
            config_path=config_path,
        )


def test_selected_model_environment_like_blob_filename_is_rejected(
    tmp_path: Path,
) -> None:
    workspace, config_path = _fixture(tmp_path, passed=True)
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    candidate = document["candidates"]["large"]
    repository = workspace / candidate["cache_dir"] / "models--microsoft--wavlm-large"
    config_link = repository / "snapshots" / candidate["revision"] / "config.json"
    original_blob = config_link.resolve(strict=True)
    hidden_blob = repository / "blobs/.env_backup"
    original_blob.rename(hidden_blob)
    config_link.unlink()
    config_link.symlink_to(Path("../../blobs/.env_backup"))
    with pytest.raises(
        contract.S104ContractError,
        match="one regular blob",
    ):
        contract.prepare_s104_execution(
            workspace_root=workspace,
            config_path=config_path,
        )


def test_head_registry_transitive_cardinality_is_exact(tmp_path: Path) -> None:
    workspace, config_path = _fixture(tmp_path)
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    commit = json.loads(
        (workspace / document["s103"]["commit_manifest"]).read_text(encoding="utf-8")
    )
    report = json.loads(
        (workspace / commit["comparison_report"]["path"]).read_text(encoding="utf-8")
    )
    registry_path = workspace / report["sources"]["base_plus"]["registry"]["path"]
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["artifacts"].pop()
    _write_json(registry_path, registry)
    with pytest.raises(contract.S104ContractError, match="artifact count differs"):
        contract._validate_registry_transitive_files(  # noqa: SLF001
            workspace,
            registry_path,
            name="synthetic registry",
        )


def test_atomic_publish_preserves_valid_file_after_directory_fsync_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "execution_lock.json"
    payload = {"schema_version": "fixture.v1", "status": "ready"}
    real_fsync = os.fsync
    calls = 0

    def fail_directory_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected directory fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(contract.os, "fsync", fail_directory_fsync)
    with pytest.raises(OSError, match="injected"):
        contract.publish_immutable_json(output, payload)
    assert output.read_bytes() == _canonical(payload, pretty=True)
    assert not list(tmp_path.glob(".*.part"))

    monkeypatch.setattr(contract.os, "fsync", real_fsync)
    contract.publish_immutable_json(output, payload)
    assert output.read_bytes() == _canonical(payload, pretty=True)


def test_atomic_publish_cleans_temp_after_file_fsync_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "execution_lock.json"
    payload = {"schema_version": "fixture.v1", "status": "ready"}
    real_fsync = os.fsync

    def fail_regular_file_fsync(descriptor: int) -> None:
        if os.path.isfile(f"/proc/self/fd/{descriptor}"):
            raise OSError("injected file fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(contract.os, "fsync", fail_regular_file_fsync)
    with pytest.raises(OSError, match="injected"):
        contract.publish_immutable_json(output, payload)
    assert not output.exists()
    assert not list(tmp_path.glob(".*.part"))

    monkeypatch.setattr(contract.os, "fsync", real_fsync)
    contract.publish_immutable_json(output, payload)
    assert output.read_bytes() == _canonical(payload, pretty=True)


def test_atomic_publish_cleans_temp_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "execution_lock.json"

    def fail_replace(source: Path, target: Path) -> None:
        del source, target
        raise OSError("injected replace failure")

    monkeypatch.setattr(contract.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        contract.publish_immutable_json(output, {"status": "ready"})
    assert not output.exists()
    assert not list(tmp_path.glob(".*.part"))


def test_execution_lock_is_immutable_after_first_success(tmp_path: Path) -> None:
    output = tmp_path / "execution_lock.json"
    original = {"schema_version": "fixture.v1", "status": "ready"}
    contract.publish_immutable_json(output, original)
    original_bytes = output.read_bytes()
    with pytest.raises(contract.S104ContractError, match="other bytes"):
        contract.publish_immutable_json(
            output,
            {"schema_version": "fixture.v1", "status": "changed"},
        )
    assert output.read_bytes() == original_bytes
    assert not list(tmp_path.glob(".*.part"))


def test_contract_module_does_not_import_torch() -> None:
    source = CONTRACT_SOURCE.read_text(encoding="utf-8")
    assert "import torch" not in source
    specification = importlib.util.spec_from_file_location(
        "s104_contract_isolated",
        CONTRACT_SOURCE,
    )
    assert specification is not None
