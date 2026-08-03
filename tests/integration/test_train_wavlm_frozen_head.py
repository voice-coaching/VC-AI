from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import torch
import yaml

from voice_coach.experiments import registry as registry_module


REPOSITORY_ROOT = Path(__file__).parents[2]
SCRIPT = REPOSITORY_ROOT / "scripts" / "train_wavlm_frozen_head.py"
CONFIG = (
    REPOSITORY_ROOT / "configs" / "experiments" / "wavlm_base_plus_frozen_head_v1.yaml"
)
LARGE_CONFIG = (
    REPOSITORY_ROOT / "configs" / "experiments" / "wavlm_large_frozen_head_v1.yaml"
)
LARGE_CACHE_CONFIG = (
    REPOSITORY_ROOT / "configs" / "experiments" / "wavlm_large_frozen_v1.yaml"
)
LARGE_CACHE_BUILDER = REPOSITORY_ROOT / "scripts" / "build_wavlm_large_feature_cache.py"
SPEC = importlib.util.spec_from_file_location(
    "train_wavlm_frozen_head_test_module",
    SCRIPT,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _speaker_hash(prefix: str, bucket: int, modulus: int = 10) -> str:
    for index in range(100_000):
        value = hashlib.sha256(f"{prefix}-{index}".encode()).hexdigest()
        if int(value[:16], 16) % modulus == bucket:
            return value
    raise AssertionError("could not construct a speaker bucket")


def _cache_metadata(
    *,
    kind: str,
    model_id: str,
    revision: str,
    manifest_sha: str,
    builder_config_sha: str,
) -> dict[bytes, bytes]:
    inputs = {
        "utterance": ["embedding_mean_std"],
        "phone": ["embedding_mean", "target_label"],
        "word": ["embedding_mean"],
    }[kind]
    labels = {"utterance": [], "phone": ["binary_error_label"], "word": []}[kind]
    return {
        b"voice_coach.schema_version": f"synthetic.{kind}.v1".encode(),
        b"voice_coach.model_id": model_id.encode(),
        b"voice_coach.model_revision": revision.encode(),
        b"voice_coach.frozen_encoder": b"true",
        b"voice_coach.oracle_alignment": b"true",
        b"voice_coach.storage_precision": b"float16",
        b"voice_coach.allowed_splits": b"train,dev",
        b"voice_coach.metric_evaluation_performed": b"false",
        b"voice_coach.privacy_reviewed": b"true",
        b"voice_coach.contains_direct_identifiers": b"false",
        b"voice_coach.contains_transcript_text": b"false",
        b"voice_coach.contains_audio_bytes": b"false",
        b"voice_coach.contains_realized_phone_identity": b"false",
        b"voice_coach.contains_alignment_operation": b"false",
        b"voice_coach.access_scope": b"restricted_internal",
        b"voice_coach.shareable": b"false",
        b"voice_coach.source_manifest_sha256": manifest_sha.encode(),
        b"voice_coach.config_sha256": builder_config_sha.encode(),
        b"voice_coach.input_feature_columns": json.dumps(inputs).encode(),
        b"voice_coach.label_columns": json.dumps(labels).encode(),
        b"voice_coach.slice_only_columns": json.dumps(
            ["split", "l1", "gender", "proficiency"]
        ).encode(),
        b"voice_coach.forbidden_as_input": json.dumps(
            [
                "binary_error_label",
                "realized_phone_identity",
                "alignment_operation",
                "error_tag",
                "holistic_score",
            ]
        ).encode(),
    }


def _schema(kind: str, hidden_size: int) -> pa.Schema:
    common = [
        pa.field("schema_version", pa.string(), nullable=False),
        pa.field("oracle_alignment", pa.bool_(), nullable=False),
        pa.field("utterance_id_sha256", pa.string(), nullable=False),
        pa.field("speaker_id_sha256", pa.string(), nullable=False),
        pa.field("split", pa.string(), nullable=False),
        pa.field("prompt_sha256", pa.string(), nullable=False),
        pa.field("l1", pa.string()),
        pa.field("gender", pa.string()),
        pa.field("proficiency", pa.string()),
    ]
    if kind == "utterance":
        return pa.schema(
            [
                *common,
                pa.field("audio_duration_seconds", pa.float64(), nullable=False),
                pa.field("chunk_count", pa.int16(), nullable=False),
                pa.field("frame_count", pa.int32(), nullable=False),
                pa.field(
                    "embedding_mean_std",
                    pa.list_(pa.float16(), hidden_size * 2),
                    nullable=False,
                ),
            ]
        )
    if kind == "phone":
        return pa.schema(
            [
                *common,
                pa.field("phone_index", pa.int32(), nullable=False),
                pa.field("target_label", pa.string(), nullable=False),
                pa.field("oracle_start_seconds", pa.float64(), nullable=False),
                pa.field("oracle_end_seconds", pa.float64(), nullable=False),
                pa.field("pooled_frame_count", pa.int16(), nullable=False),
                pa.field(
                    "embedding_mean",
                    pa.list_(pa.float16(), hidden_size),
                    nullable=False,
                ),
                pa.field("binary_error_label", pa.int8(), nullable=False),
            ]
        )
    return pa.schema(
        [
            *common,
            pa.field("word_index", pa.int32(), nullable=False),
            pa.field("oracle_start_seconds", pa.float64(), nullable=False),
            pa.field("oracle_end_seconds", pa.float64(), nullable=False),
            pa.field("pooled_frame_count", pa.int16(), nullable=False),
            pa.field(
                "embedding_mean",
                pa.list_(pa.float16(), hidden_size),
                nullable=False,
            ),
        ]
    )


def _base_row(
    index: int,
    split: str,
    speaker: str,
) -> dict:
    utterance_id = hashlib.sha256(f"utterance-{split}-{index}".encode()).hexdigest()
    return {
        "schema_version": "synthetic.v1",
        "oracle_alignment": True,
        "utterance_id_sha256": utterance_id,
        "speaker_id_sha256": speaker,
        "split": split,
        "prompt_sha256": hashlib.sha256(f"prompt-{index % 3}".encode()).hexdigest(),
        "l1": "Chinese" if index % 2 == 0 else "Japanese",
        "gender": "F" if index % 2 == 0 else "M",
        "proficiency": "Beginner" if index % 3 == 0 else "Intermediate",
    }


def _fixture(tmp_path: Path) -> argparse.Namespace:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    hidden_size = 8
    manifest_sha = "a" * 64
    builder_config_sha = "b" * 64
    builder_sha = "c" * 64
    model_config_sha = "d" * 64
    model_checkpoint_sha = "e" * 64
    config["encoder"].update(
        {
            "hidden_size": hidden_size,
            "utterance_embedding_size": hidden_size * 2,
            "source_config_sha256": model_config_sha,
            "source_checkpoint_sha256": model_checkpoint_sha,
        }
    )
    config["data"].update(
        {
            "modeling_manifest_sha256": manifest_sha,
            "cache_builder_config_sha256": builder_config_sha,
            "cache_builder_sha256": builder_sha,
            "expected_source_rows": 200,
        }
    )
    config["heads"]["phone"].update(
        {"target_embedding_dim": 4, "hidden_dim": 8, "dropout": 0.0}
    )
    config["heads"]["utterance"].update({"hidden_dim": 8, "dropout": 0.0})
    config["optimization"].update(
        {
            "phone_batch_size": 4,
            "utterance_batch_size": 4,
            "parquet_batch_rows": 8,
            "phone_max_epochs": 2,
            "utterance_max_epochs": 2,
            "minimum_epochs": 1,
            "early_stopping_patience": 1,
        }
    )
    config["runtime"]["streaming"]["maximum_parquet_batch_rows"] = 8

    train_fit_speakers = [
        _speaker_hash(f"fit-{index}", 2 + index) for index in range(4)
    ]
    inner_speakers = [_speaker_hash(f"inner-{index}", index) for index in range(2)]
    dev_speakers = [_speaker_hash(f"dev-{index}", 7 + index) for index in range(3)]
    utterance_rows: list[dict] = []
    for index in range(12):
        speakers = train_fit_speakers if index < 8 else inner_speakers
        speaker = speakers[index % len(speakers)]
        row = _base_row(index, "train", speaker)
        vector = np.linspace(
            -1,
            1,
            hidden_size * 2,
            dtype=np.float16,
        ) + np.float16(index / 20)
        row.update(
            {
                "audio_duration_seconds": 2.0 + index / 10,
                "chunk_count": 1,
                "frame_count": 100,
                "embedding_mean_std": vector.tolist(),
            }
        )
        utterance_rows.append(row)
    for offset in range(6):
        index = 12 + offset
        row = _base_row(index, "dev", dev_speakers[offset % 3])
        vector = np.linspace(
            -0.8,
            1.2,
            hidden_size * 2,
            dtype=np.float16,
        ) + np.float16(offset / 20)
        row.update(
            {
                "audio_duration_seconds": 3.0 + offset / 10,
                "chunk_count": 1,
                "frame_count": 120,
                "embedding_mean_std": vector.tolist(),
            }
        )
        utterance_rows.append(row)

    phone_rows: list[dict] = []
    word_rows: list[dict] = []
    phones = ["ㄱ", "ㅏ", "ㄴ", "ㄷ"]
    for utterance_index, utterance in enumerate(utterance_rows):
        for phone_index in range(4):
            vector = np.linspace(
                -0.5,
                0.5,
                hidden_size,
                dtype=np.float16,
            ) + np.float16((utterance_index + phone_index) / 50)
            phone_rows.append(
                {
                    **{
                        key: utterance[key]
                        for key in (
                            "schema_version",
                            "oracle_alignment",
                            "utterance_id_sha256",
                            "speaker_id_sha256",
                            "split",
                            "prompt_sha256",
                            "l1",
                            "gender",
                            "proficiency",
                        )
                    },
                    "phone_index": phone_index,
                    "target_label": phones[phone_index],
                    "oracle_start_seconds": phone_index * 0.1,
                    "oracle_end_seconds": (phone_index + 1) * 0.1,
                    "pooled_frame_count": 5,
                    "embedding_mean": vector.tolist(),
                    "binary_error_label": (utterance_index + phone_index) % 2,
                }
            )
        word_rows.append(
            {
                **{
                    key: utterance[key]
                    for key in (
                        "schema_version",
                        "oracle_alignment",
                        "utterance_id_sha256",
                        "speaker_id_sha256",
                        "split",
                        "prompt_sha256",
                        "l1",
                        "gender",
                        "proficiency",
                    )
                },
                "word_index": 0,
                "oracle_start_seconds": 0.0,
                "oracle_end_seconds": 0.4,
                "pooled_frame_count": 20,
                "embedding_mean": np.zeros(hidden_size, dtype=np.float16).tolist(),
            }
        )

    cache_root = tmp_path / "cache"
    output_records: dict[str, dict] = {}
    for kind, rows in (
        ("utterance", utterance_rows),
        ("phone", phone_rows),
        ("word", word_rows),
    ):
        directory = cache_root / kind
        directory.mkdir(parents=True)
        path = directory / "part-00000.parquet"
        schema = _schema(kind, hidden_size).with_metadata(
            _cache_metadata(
                kind=kind,
                model_id=config["encoder"]["id"],
                revision=config["encoder"]["revision"],
                manifest_sha=manifest_sha,
                builder_config_sha=builder_config_sha,
            )
        )
        table = pa.Table.from_pylist(rows, schema=schema)
        pq.write_table(table, path, compression="zstd", row_group_size=8)
        parquet = pq.ParquetFile(path)
        fingerprint = {
            "name": path.name,
            "row_count": len(rows),
            "row_group_count": parquet.metadata.num_row_groups,
            "byte_count": path.stat().st_size,
            "sha256": _sha256(path),
        }
        output_records[kind] = {
            "row_count": len(rows),
            "shard_count": 1,
            "byte_count": path.stat().st_size,
            "content_sha256": MODULE.sha256_json(
                [
                    {
                        "name": path.name,
                        "row_count": len(rows),
                        "sha256": _sha256(path),
                    }
                ]
            ),
            "shards": [fingerprint],
        }
    config["data"]["expected_output_rows"] = {
        "utterance": len(utterance_rows),
        "phone": len(phone_rows),
        "word": len(word_rows),
    }

    explicit_rows = []
    for index, utterance in enumerate(utterance_rows):
        score = 1 + index % 5
        explicit_rows.append(
            {
                "utterance_id_sha256": utterance["utterance_id_sha256"],
                "speaker_id_sha256": utterance["speaker_id_sha256"],
                "split": utterance["split"],
                "l1": utterance["l1"],
                "gender": utterance["gender"],
                "proficiency": utterance["proficiency"],
                "pronunciation_score": score,
                "fluency_score": 1 + (index + 1) % 5,
                "comprehensibility_score": 1 + (index + 2) % 5,
            }
        )
    explicit_metadata = {
        b"voice_coach.schema_version": b"synthetic.explicit.v1",
        b"voice_coach.oracle_alignment": b"true",
        b"voice_coach.privacy_safe": b"true",
        b"voice_coach.allowed_splits": b"train,dev",
        b"voice_coach.metric_evaluation_performed": b"false",
        b"voice_coach.source_manifest_sha256": manifest_sha.encode(),
        b"voice_coach.input_feature_columns": b"[]",
        b"voice_coach.label_columns": json.dumps(list(MODULE.OUTCOMES)).encode(),
        b"voice_coach.forbidden_as_input": json.dumps(list(MODULE.OUTCOMES)).encode(),
    }
    explicit_path = tmp_path / "explicit_labels.parquet"
    explicit_table = pa.Table.from_pylist(explicit_rows).replace_schema_metadata(
        explicit_metadata
    )
    pq.write_table(explicit_table, explicit_path)
    config["data"]["explicit_utterance_labels_sha256"] = _sha256(explicit_path)

    cache_report = tmp_path / "cache_report.json"
    cache_report.write_text(
        json.dumps(
            {
                "schema_version": MODULE.CACHE_REPORT_SCHEMA_VERSION,
                "status": "completed",
                "source": {
                    "modeling_manifest_sha256": manifest_sha,
                    "prompt_reservation_sha256": "f" * 64,
                    "experiment_config_sha256": builder_config_sha,
                    "builder_sha256": builder_sha,
                },
                "model": {
                    "id": config["encoder"]["id"],
                    "revision": config["encoder"]["revision"],
                    "hidden_size": hidden_size,
                    "config_sha256": model_config_sha,
                    "checkpoint_sha256": model_checkpoint_sha,
                    "frozen": True,
                    "local_files_only": True,
                },
                "runtime": {
                    "storage_precision": "float16",
                    "oom_count": 0,
                },
                "policy": {
                    "allowed_splits": ["train", "dev"],
                    "evaluation_splits": [
                        "calibration",
                        "locked_test",
                        "stack",
                    ],
                    "evaluation_split_metric_access": "forbidden",
                    "holistic_score_columns_read": [],
                    "metric_evaluation_performed": False,
                    "rows_with_qc_flags_excluded": True,
                    "reserved_prompts_excluded_from_every_split": True,
                    "oracle_alignment": True,
                    "contains_direct_identifiers": False,
                    "contains_transcript_text": False,
                    "contains_audio_bytes": False,
                    "contains_realized_phone_identity": False,
                    "contains_alignment_operation": False,
                    "access_scope": "restricted_internal",
                    "shareable": False,
                    "column_roles": {
                        "utterance": {
                            "input_feature_columns": ["embedding_mean_std"],
                            "label_columns": [],
                        },
                        "phone": {
                            "input_feature_columns": [
                                "embedding_mean",
                                "target_label",
                            ],
                            "label_columns": ["binary_error_label"],
                        },
                        "word": {
                            "input_feature_columns": ["embedding_mean"],
                            "label_columns": [],
                        },
                    },
                },
                "configuration": {
                    "limit": None,
                    "full_run": True,
                    "full_run_gate": {"approved": "synthetic"},
                },
                "progress": {
                    "source_rows_scanned": 200,
                    "source_scan_complete": True,
                    "utterance_rows_written": len(utterance_rows),
                    "phone_rows_written": len(phone_rows),
                    "word_rows_written": len(word_rows),
                },
                "outputs": output_records,
                "privacy_audit": {
                    "raw_locator_values_exported": False,
                    "raw_file_stem_values_exported": False,
                    "realized_phone_values_exported": False,
                    "alignment_operation_values_exported": False,
                    "word_transcript_values_exported": False,
                    "phone_binary_target_is_separate_label_role": True,
                },
            }
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "experiment.yaml"
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return argparse.Namespace(
        config=config_path,
        cache_root=cache_root,
        cache_report=cache_report,
        explicit_label_cache=explicit_path,
        output_dir=tmp_path / "experiment",
        registry_root=tmp_path / "registry",
        run_id="synthetic-wavlm-frozen-head",
    )


def _stub_registry_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        registry_module,
        "collect_runtime_metadata",
        lambda package_names: {
            "python": {"version": "3.12.0"},
            "platform": {"system": "TestOS"},
            "packages": {name: "test" for name in package_names},
        },
    )
    monkeypatch.setattr(
        registry_module,
        "collect_accelerator_metadata",
        lambda: {
            "cuda_available": False,
            "cuda_runtime_version": None,
            "torch_device_count": 0,
            "torch_devices": [],
            "nvidia_smi": {"available": False, "gpus": []},
        },
    )
    monkeypatch.setattr(
        registry_module,
        "collect_git_metadata",
        lambda repo_path: {
            "available": True,
            "commit": "1" * 40,
            "dirty": True,
        },
    )


def test_synthetic_full_cache_trains_three_seeds_without_dev_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _fixture(tmp_path)
    seed_calls: list[int] = []
    original_set_seed = MODULE.set_deterministic_seed

    def record_seed(seed: int, *, deterministic_algorithms: bool) -> None:
        seed_calls.append(seed)
        original_set_seed(
            seed,
            deterministic_algorithms=deterministic_algorithms,
        )

    monkeypatch.setattr(MODULE, "set_deterministic_seed", record_seed)

    report = MODULE.train_frozen_head(
        args,
        device_override=torch.device("cpu"),
    )

    assert report["status"] == "completed"
    assert report["summary"]["seeds_completed"] == [42, 43, 44]
    assert report["summary"]["locked_test_remains_unread"] is True
    assert report["summary"]["dev_used_for_selection"] is False
    assert report["cache_contract"]["full_cache_validated"] is True
    assert report["cache_contract"]["smoke_cache_rejected"] is True
    assert report["leakage_controls"]["raw_manifest_read"] is False
    assert report["leakage_controls"]["environment_files_read"] == []
    assert report["memory_contract"]["full_embedding_matrix_materialized"] is False
    assert report["experiment_registry"]["integrated"] is False
    assert report["encoder"]["variant"] == "base_plus"
    assert report["encoder"]["current_experiment_variant"] == "base_plus"
    assert report["sources"]["trainer_source_sha256"] == _sha256(SCRIPT)
    assert report["sources"]["head_source_sha256"] == _sha256(
        REPOSITORY_ROOT / "src" / "voice_coach" / "models" / "wavlm_frozen_head.py"
    )
    assert report["runtime"]["determinism"] == {
        "deterministic_algorithms": True,
        "cublas_workspace_config": ":4096:8",
        "configured_before_cuda_initialization": True,
        "fresh_process_contract_enforced": False,
    }
    assert report["head_contract"]["task_rng_reinitialized_per_head"] is True
    assert seed_calls == [42, 42, 43, 43, 44, 44]
    for run in report["runs"]:
        assert run["selection"]["dev_values_used_for_selection"] is False
        assert run["selection"]["phone"]["decision_threshold_source"].endswith(
            "inner_validation_only"
        )
        assert 0 <= run["phone"]["metrics"]["macro_f1"] <= 1
        assert run["phone"]["rows"] == 24
        assert run["utterance"]["rows"] == 6
        seed_dir = args.output_dir / f"seed_{run['seed']}"
        assert (seed_dir / "checkpoint.pt").is_file()
        assert (seed_dir / "phone_dev_predictions.parquet").is_file()
        assert (seed_dir / "utterance_dev_predictions.parquet").is_file()
        assert (seed_dir / "slice_metrics.json").is_file()
        phone_predictions = pq.read_table(seed_dir / "phone_dev_predictions.parquet")
        assert "embedding_mean" not in phone_predictions.column_names
        assert set(phone_predictions.column("split").to_pylist()) == {"dev"}
    assert not list(tmp_path.glob(".experiment.part-*"))


def test_smoke_cache_is_rejected_before_partial_output(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    report = json.loads(args.cache_report.read_text(encoding="utf-8"))
    report["configuration"]["full_run"] = False
    report["configuration"]["limit"] = 100
    report["progress"]["source_scan_complete"] = False
    args.cache_report.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(ValueError, match="smoke"):
        MODULE.train_frozen_head(
            args,
            device_override=torch.device("cpu"),
        )

    assert not args.output_dir.exists()
    assert not list(tmp_path.glob(".experiment.part-*"))


def test_phone_label_cannot_be_added_to_input_allowlist(
    tmp_path: Path,
) -> None:
    args = _fixture(tmp_path)
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    config["inputs"]["phone"]["feature_columns"].append("binary_error_label")
    args.config.write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(ValueError, match="phone inputs"):
        MODULE.train_frozen_head(
            args,
            device_override=torch.device("cpu"),
        )

    assert not args.output_dir.exists()


def test_encoder_variant_must_match_encoder_id(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    config["encoder"]["variant"] = "large"

    with pytest.raises(ValueError, match="variant does not match"):
        MODULE.validate_experiment_config(config)


def test_large_config_reuses_base_protocol_with_only_large_pins() -> None:
    base = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    large = yaml.safe_load(LARGE_CONFIG.read_text(encoding="utf-8"))

    MODULE.validate_experiment_config(large)
    assert large["experiment"]["id"] == "aihub71490_wavlm_large_frozen_head_v1"
    assert large["experiment"]["seeds"] == base["experiment"]["seeds"]
    assert large["encoder"] == {
        "variant": "large",
        "id": "microsoft/wavlm-large",
        "revision": "c1423ed94bb01d80a3f5ce5bc39f6026a0f4828c",
        "hidden_size": 1_024,
        "utterance_embedding_size": 2_048,
        "frozen": True,
        "source_config_sha256": (
            "a3d8fe831aaf63d725b54a8ac36f3549cd4365c5086774b2c89cabbc6f9e129d"
        ),
        "source_checkpoint_sha256": (
            "fdee460e529396ddb2f8c8e8ce0ad74cfb747b726bc6f612e666c7c1e1963c9d"
        ),
    }
    for section in ("inputs", "heads", "optimization", "evaluation", "runtime"):
        assert large[section] == base[section]
    for key in (
        "explicit_utterance_labels",
        "explicit_utterance_labels_sha256",
        "modeling_manifest_sha256",
        "expected_source_rows",
        "expected_output_rows",
        "training_splits",
        "development_splits",
        "forbidden_metric_splits",
        "primary_analysis_scope",
        "inner_validation",
    ):
        assert large["data"][key] == base["data"][key]
    assert large["data"]["feature_cache_root"].endswith("wavlm_large_frozen_v1")
    assert large["data"]["feature_cache_report"].endswith(
        "wavlm_large_frozen_v1_report.json"
    )
    assert large["data"]["cache_builder_config_sha256"] == _sha256(LARGE_CACHE_CONFIG)
    assert large["data"]["cache_builder_sha256"] == _sha256(LARGE_CACHE_BUILDER)
    assert large["outputs"]["artifact_root"].endswith(
        "aihub71490_wavlm_large_frozen_head_v1"
    )
    assert large["outputs"]["planned_registry_run_id"] == (
        "aihub71490-wavlm-large-frozen-head-v1-dev"
    )


def test_fresh_import_sets_cublas_contract_before_cuda_initialization() -> None:
    child_environment = os.environ.copy()
    child_environment.pop("CUBLAS_WORKSPACE_CONFIG", None)
    existing_pythonpath = child_environment.get("PYTHONPATH")
    source_root = str(REPOSITORY_ROOT / "src")
    child_environment["PYTHONPATH"] = (
        source_root
        if not existing_pythonpath
        else os.pathsep.join((source_root, existing_pythonpath))
    )
    program = f"""
import importlib.util
import json
import os
from pathlib import Path
import sys

script = Path({str(SCRIPT)!r})
spec = importlib.util.spec_from_file_location("fresh_frozen_head_runner", script)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
print(json.dumps({{
    "workspace": os.environ.get("CUBLAS_WORKSPACE_CONFIG"),
    "cuda_initialized": module.torch.cuda.is_initialized(),
    "late_setup": module._CUDA_INITIALIZED_BEFORE_DETERMINISM_SETUP,
}}))
"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=REPOSITORY_ROOT,
        env=child_environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert json.loads(result.stdout) == {
        "workspace": ":4096:8",
        "cuda_initialized": False,
        "late_setup": False,
    }


def test_registered_wrapper_hashes_inputs_metrics_and_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _fixture(tmp_path)
    _stub_registry_environment(monkeypatch)

    report, metadata_path = MODULE.run_registered_experiment(
        args,
        device_override=torch.device("cpu"),
    )

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["status"] == "completed"
    assert len(metadata["inputs"]["config"]) == 1
    assert len(metadata["inputs"]["data"]) == 5
    assert len(metadata["inputs"]["model"]) == 2
    assert len(metadata["metrics"]) == 4
    assert len(metadata["artifacts"]) == 13
    assert report["experiment_registry"]["integrated"] is True
    assert report["experiment_registry"]["t03_complete"] is True
    assert report["experiment_registry"]["input_roles"]["model"] == 2
    model_hashes = {item["sha256"] for item in metadata["inputs"]["model"]}
    assert report["sources"]["trainer_source_sha256"] in model_hashes
    assert report["sources"]["head_source_sha256"] in model_hashes
    assert all(".env" not in item["path"] for item in metadata["inputs"]["data"])


def test_registry_failure_atomically_removes_published_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _fixture(tmp_path)
    _stub_registry_environment(monkeypatch)

    def fail_metric_logging(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("injected registry failure")

    monkeypatch.setattr(
        registry_module.ExperimentRun,
        "log_metrics",
        fail_metric_logging,
    )

    with pytest.raises(RuntimeError, match="injected registry failure"):
        MODULE.run_registered_experiment(
            args,
            device_override=torch.device("cpu"),
        )

    assert not args.output_dir.exists()
    assert not list(tmp_path.glob(".experiment.part-*"))
    metadata_paths = list(args.registry_root.glob("*/metadata.json"))
    assert len(metadata_paths) == 1
    metadata = json.loads(metadata_paths[0].read_text(encoding="utf-8"))
    assert metadata["status"] == "failed"
    assert metadata["failure"] == {"exception_type": "builtins.RuntimeError"}
