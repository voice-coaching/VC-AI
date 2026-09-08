from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import soundfile as sf
import torch
import yaml

SCRIPT = Path(__file__).parents[2] / "scripts" / "build_wavlm_large_feature_cache.py"
BASE_SCRIPT = Path(__file__).parents[2] / "scripts" / "build_wavlm_feature_cache.py"
CONFIG = (
    Path(__file__).parents[2] / "configs" / "experiments" / "wavlm_large_frozen_v1.yaml"
)
REVIEW_TEMPLATE = (
    Path(__file__).parents[2]
    / "configs"
    / "experiments"
    / "wavlm_large_frozen_v1_smoke_review.template.json"
)
SPEC = importlib.util.spec_from_file_location(
    "test_build_wavlm_large_feature_cache_module",
    SCRIPT,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class FakeWavLMLarge(torch.nn.Module):
    def forward(
        self,
        *,
        input_values: torch.Tensor,
        attention_mask: torch.Tensor,
    ) -> SimpleNamespace:
        del attention_mask
        frame_count = MODULE.WAVLM_LARGE_FRAME_GEOMETRY.output_frame_count(
            input_values.shape[1]
        )
        values = torch.arange(
            frame_count * MODULE.HIDDEN_SIZE,
            dtype=torch.float32,
            device=input_values.device,
        ).reshape(1, frame_count, MODULE.HIDDEN_SIZE)
        return SimpleNamespace(last_hidden_state=values / 1_000)


def _source_row(
    record_id: str,
    *,
    split: str = "train",
    prompt_hash: str | None = None,
    qc_flags: list[str] | None = None,
) -> dict:
    return {
        "id": record_id,
        "speaker_id": _hash(f"speaker:{record_id}"),
        "split": split,
        "audio_path": "sample.wav",
        "prompt_sha256": prompt_hash or _hash("open"),
        "qc_flags": qc_flags or [],
        "word_intervals": [
            {"start": 0.10, "end": 0.70, "label": "비공개단어"},
        ],
        "target_phone_intervals": [
            {"start": 0.10, "end": 0.30, "label": "ㄱ"},
            {"start": 0.30, "end": 0.55, "label": "ㅏ"},
        ],
        "phone_alignments": [
            {
                "target_label": "ㄱ",
                "realized_label": "ㄱ",
                "operation": "match",
            },
            {
                "target_label": None,
                "realized_label": "ㄴ",
                "operation": "insertion",
            },
            {
                "target_label": "ㅏ",
                "realized_label": "ㅓ",
                "operation": "substitution",
            },
        ],
        "l1": "Chinese",
        "gender": "F",
        "proficiency": "Intermediate",
        "pronunciation_score": 5,
        "fluency_score": 4,
        "comprehensibility_score": 3,
    }


def _write_build_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path]:
    dataset_root = tmp_path / "dataset"
    dataset_root.mkdir()
    sf.write(
        dataset_root / "sample.wav",
        np.zeros(16_000, dtype=np.float32),
        16_000,
    )
    reserved_hash = _hash("reserved")
    included = [
        _source_row(
            f"included-{index:03d}",
            split="train" if index % 2 == 0 else "dev",
        )
        for index in range(100)
    ]
    rows = [
        _source_row("locked", split="locked_test"),
        _source_row("qc", qc_flags=["bad"]),
        _source_row("reserved", prompt_hash=reserved_hash),
        *included,
    ]
    manifest = tmp_path / "modeling.parquet"
    pq.write_table(pa.Table.from_pylist(rows), manifest)
    reservation = tmp_path / "reservation.json"
    reservation.write_text(
        json.dumps(
            {
                "source": {"modeling_manifest_sha256": _sha256(manifest)},
                "reserved_prompts": [{"prompt_sha256": reserved_hash}],
            }
        ),
        encoding="utf-8",
    )
    source_config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    source_config["data"]["modeling_manifest_sha256"] = _sha256(manifest)
    source_config["full_run_gate"]["base_plus_head_contract"][
        "modeling_manifest_sha256"
    ] = _sha256(manifest)
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(source_config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return dataset_root, manifest, reservation, config


def _patch_cuda_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MODULE.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(MODULE.torch.cuda, "empty_cache", lambda: None)
    monkeypatch.setattr(
        MODULE.torch.cuda,
        "reset_peak_memory_stats",
        lambda device: None,
    )
    monkeypatch.setattr(
        MODULE.torch.cuda,
        "max_memory_allocated",
        lambda device: 123,
    )
    monkeypatch.setattr(
        MODULE.torch.cuda,
        "max_memory_reserved",
        lambda device: 456,
    )
    monkeypatch.setattr(
        MODULE,
        "_gpu_metadata",
        lambda device: {
            "device_index": 0,
            "name": "synthetic-test-gpu",
            "compute_capability": "0.0",
            "total_memory_bytes": 1,
            "torch_version": torch.__version__,
            "cuda_version": None,
        },
    )
    monkeypatch.setattr(
        MODULE,
        "load_frozen_model",
        lambda cache_dir, device: (
            torch.nn.Identity(),
            {
                "id": MODULE.MODEL_ID,
                "revision": MODULE.MODEL_REVISION,
                "config_sha256": MODULE.MODEL_CONFIG_SHA256,
                "checkpoint_sha256": MODULE.MODEL_CHECKPOINT_SHA256,
                "preprocessor_config_sha256": MODULE.MODEL_PREPROCESSOR_SHA256,
                "hidden_size": MODULE.HIDDEN_SIZE,
                "frozen": True,
                "local_files_only": True,
                "do_normalize": True,
                "preprocessor_contract": {
                    "sampling_rate_hz": MODULE.SAMPLE_RATE_HZ,
                    "do_normalize": True,
                    "return_attention_mask": True,
                    "source": "official_pinned_revision",
                    "source_url": MODULE.PINNED_PREPROCESSOR_URL,
                    "config_sha256": MODULE.MODEL_PREPROCESSOR_SHA256,
                    "local_preprocessor_config_present": True,
                    "local_preprocessor_config_validated": True,
                    "auto_feature_extractor_used": False,
                },
            },
        ),
    )


def _fake_extracted(row: dict) -> object:
    identifiers = {
        "utterance_id_sha256": MODULE.hash_utterance_id(row["id"]),
        "speaker_id_sha256": row["speaker_id"],
        "split": row["split"],
        "prompt_sha256": row["prompt_sha256"],
        "l1": row["l1"],
        "gender": row["gender"],
        "proficiency": row["proficiency"],
    }
    return MODULE.ExtractedRows(
        utterance={
            "schema_version": MODULE.UTTERANCE_SCHEMA_VERSION,
            "oracle_alignment": True,
            **identifiers,
            "audio_duration_seconds": 1.0,
            "chunk_count": 1,
            "frame_count": 49,
            "embedding_mean_std": [0.0] * (MODULE.HIDDEN_SIZE * 2),
        },
        phones=(
            {
                "schema_version": MODULE.PHONE_SCHEMA_VERSION,
                "oracle_alignment": True,
                **identifiers,
                "phone_index": 0,
                "target_label": "ㄱ",
                "oracle_start_seconds": 0.1,
                "oracle_end_seconds": 0.3,
                "pooled_frame_count": 10,
                "embedding_mean": [0.0] * MODULE.HIDDEN_SIZE,
                "binary_error_label": 0,
            },
        ),
        words=(
            {
                "schema_version": MODULE.WORD_SCHEMA_VERSION,
                "oracle_alignment": True,
                **identifiers,
                "word_index": 0,
                "oracle_start_seconds": 0.1,
                "oracle_end_seconds": 0.7,
                "pooled_frame_count": 20,
                "embedding_mean": [0.0] * MODULE.HIDDEN_SIZE,
            },
        ),
        decoded_audio_seconds=1.0,
        model_frames=49,
        chunk_count=1,
        padded_short_chunks=0,
        output_dtypes=("float32",),
    )


def _write_base_plus_head_report(
    path: Path,
    *,
    repository_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    def write(relative: str, value: bytes) -> Path:
        output = repository_root / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(value)
        return output

    def input_entry(file_path: Path) -> dict[str, object]:
        return {
            "path": file_path.relative_to(repository_root).as_posix(),
            "sha256": _sha256(file_path),
            "size_bytes": file_path.stat().st_size,
        }

    head_config = write("configs/head.yaml", b"fixed head config\n")
    trainer_source = write("scripts/trainer.py", b"fixed trainer\n")
    head_source = write("src/head.py", b"fixed head module\n")
    explicit_labels = write("data/labels.parquet", b"privacy-safe labels\n")
    cache_report = write("data/cache-report.json", b'{"status":"completed"}\n')
    cache_shards = {
        kind: write(f"data/cache/{kind}/part-00000.parquet", kind.encode("ascii"))
        for kind in MODULE.EXPECTED_CACHE_ROWS
    }
    monkeypatch.setattr(MODULE, "REPOSITORY_ROOT", repository_root)
    monkeypatch.setattr(
        MODULE,
        "BASE_PLUS_HEAD_CONFIG_SHA256",
        _sha256(head_config),
    )
    monkeypatch.setattr(
        MODULE,
        "BASE_PLUS_HEAD_TRAINER_SHA256",
        _sha256(trainer_source),
    )
    monkeypatch.setattr(
        MODULE,
        "BASE_PLUS_HEAD_MODEL_SHA256",
        _sha256(head_source),
    )
    monkeypatch.setattr(
        MODULE,
        "BASE_PLUS_EXPLICIT_LABEL_SHA256",
        _sha256(explicit_labels),
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, object]] = []
    report_artifact_files: dict[str, Path] = {}
    for seed in MODULE.REQUIRED_SEEDS:
        seed_directory = path.parent / f"seed_{seed}"
        seed_directory.mkdir()
        named_files = {
            f"seed_{seed}_checkpoint": seed_directory / "checkpoint.pt",
            f"seed_{seed}_phone_predictions": (
                seed_directory / "phone_dev_predictions.parquet"
            ),
            f"seed_{seed}_utterance_predictions": (
                seed_directory / "utterance_dev_predictions.parquet"
            ),
            f"seed_{seed}_slice_metrics": seed_directory / "slice_metrics.json",
        }
        for name, artifact_path in named_files.items():
            artifact_path.write_bytes(f"{name}\n".encode("ascii"))
        report_artifact_files.update(named_files)

        def reported(name: str) -> dict[str, str]:
            artifact_path = named_files[name]
            return {
                "path": artifact_path.relative_to(path.parent).as_posix(),
                "sha256": _sha256(artifact_path),
            }

        runs.append(
            {
                "seed": seed,
                "selection": {"dev_values_used_for_selection": False},
                "phone": {"predictions": reported(f"seed_{seed}_phone_predictions")},
                "utterance": {
                    "predictions": reported(f"seed_{seed}_utterance_predictions")
                },
                "artifacts": {
                    "checkpoint": reported(f"seed_{seed}_checkpoint"),
                    "slice_metrics": reported(f"seed_{seed}_slice_metrics"),
                },
            }
        )

    registry_metadata = (
        repository_root
        / "artifacts"
        / "experiment_registry"
        / "base-head-test"
        / "metadata.json"
    )
    fingerprints = {
        kind: [
            {
                "name": cache_shards[kind].name,
                "row_count": rows,
                "sha256": _sha256(cache_shards[kind]),
            }
        ]
        for kind, rows in MODULE.EXPECTED_CACHE_ROWS.items()
    }
    report = {
        "schema_version": MODULE.BASE_PLUS_HEAD_REPORT_SCHEMA,
        "status": "completed",
        "experiment_id": MODULE.BASE_PLUS_HEAD_EXPERIMENT_ID,
        "development_only": True,
        "encoder": {
            "variant": "base_plus",
            "id": MODULE.BASE_PLUS_MODEL_ID,
            "revision": MODULE.BASE_PLUS_MODEL_REVISION,
            "hidden_size": 768,
            "utterance_embedding_size": 1_536,
            "frozen": True,
            "source_config_sha256": MODULE.BASE_PLUS_MODEL_CONFIG_SHA256,
            "source_checkpoint_sha256": (MODULE.BASE_PLUS_MODEL_CHECKPOINT_SHA256),
            "current_experiment_variant": "base_plus",
        },
        "sources": {
            "training_config_sha256": MODULE.BASE_PLUS_HEAD_CONFIG_SHA256,
            "trainer_source_sha256": MODULE.BASE_PLUS_HEAD_TRAINER_SHA256,
            "head_source_sha256": MODULE.BASE_PLUS_HEAD_MODEL_SHA256,
            "feature_cache_report_sha256": _sha256(cache_report),
            "explicit_utterance_labels_sha256": (
                MODULE.BASE_PLUS_EXPLICIT_LABEL_SHA256
            ),
            "modeling_manifest_sha256": MODULE.MODELING_MANIFEST_SHA256,
            "prompt_reservation_sha256": MODULE.PROMPT_RESERVATION_SHA256,
            "cache_builder_config_sha256": MODULE.BASE_PLUS_CACHE_CONFIG_SHA256,
            "cache_builder_sha256": MODULE.BASE_ENGINE_SHA256,
            "encoder_config_sha256": MODULE.BASE_PLUS_MODEL_CONFIG_SHA256,
            "encoder_checkpoint_sha256": (MODULE.BASE_PLUS_MODEL_CHECKPOINT_SHA256),
            "model_hashes_bound_by_validated_config_and_cache_report": True,
        },
        "summary": {
            "seeds_completed": [42, 43, 44],
            "results_are_development_only": True,
            "locked_test_remains_unread": True,
            "stack_remains_unread": True,
            "calibration_remains_unread": True,
            "dev_inference_passes_per_seed": 1,
        },
        "leakage_controls": {
            "accepted_splits": ["train", "dev"],
            "stack_accessed": False,
            "calibration_accessed": False,
            "locked_test_accessed": False,
            "dev_used_for_selection": False,
            "train_inner_validation_used": True,
        },
        "cache_contract": {
            "full_cache_validated": True,
            "source_scan_complete": True,
            "smoke_cache_rejected": True,
            "every_shard_hash_validated": True,
            "every_shard_schema_validated": True,
            "every_shard_split_policy_validated": True,
            "every_shard_privacy_metadata_validated": True,
            "rows": MODULE.EXPECTED_CACHE_ROWS,
            "split_rows": {
                kind: {"train": rows - 1, "dev": 1}
                for kind, rows in MODULE.EXPECTED_CACHE_ROWS.items()
            },
            "shard_fingerprints": fingerprints,
            "speaker_overlap_train_dev": 0,
            "access_scope": "restricted_internal",
            "shareable": False,
        },
        "experiment_registry": {
            "integrated": True,
            "t03_complete": True,
            "run_id": "base-head-test",
            "metadata_path": registry_metadata.relative_to(repository_root).as_posix(),
            "implementation_source_hashes_match_registry_inputs": True,
            "metrics_recorded": True,
            "artifacts_recorded": True,
            "registered_artifact_count": 13,
        },
        "runs": runs,
    }
    path.write_text(json.dumps(report), encoding="utf-8")
    registry_metadata.parent.mkdir(parents=True)
    registry_artifacts = [
        {
            "name": name,
            **input_entry(artifact_path),
        }
        for name, artifact_path in report_artifact_files.items()
    ]
    registry_artifacts.append({"name": "final_report", **input_entry(path)})
    registry_metadata.write_text(
        json.dumps(
            {
                "run_id": "base-head-test",
                "status": "completed",
                "failure": None,
                "seeds": {
                    f"experiment_seed_{seed}": seed for seed in MODULE.REQUIRED_SEEDS
                },
                "inputs": {
                    "config": [input_entry(head_config)],
                    "data": [
                        input_entry(cache_report),
                        input_entry(explicit_labels),
                        *[
                            input_entry(cache_shards[kind])
                            for kind in MODULE.EXPECTED_CACHE_ROWS
                        ],
                    ],
                    "model": [
                        input_entry(trainer_source),
                        input_entry(head_source),
                    ],
                    "checkpoint": [],
                },
                "artifacts": registry_artifacts,
            }
        ),
        encoding="utf-8",
    )
    return registry_metadata


def _write_smoke_and_review(
    tmp_path: Path,
    *,
    config_sha256: str,
    builder_sha256: str,
) -> tuple[Path, Path]:
    reviewer_source: dict[str, str] = {}
    for role, relative_path in (
        ("cli", MODULE.REVIEWER_CLI_RELATIVE_PATH),
        ("core", MODULE.REVIEWER_CORE_RELATIVE_PATH),
    ):
        source_path = MODULE.REPOSITORY_ROOT / relative_path
        if not source_path.exists():
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(
                f"# synthetic reviewer {role}\n",
                encoding="utf-8",
            )
        reviewer_source[f"{role}_path"] = relative_path.as_posix()
        reviewer_source[f"{role}_sha256"] = _sha256(source_path)

    smoke = tmp_path / "large-smoke-report.json"
    smoke.write_text(
        json.dumps(
            {
                "schema_version": MODULE.REPORT_SCHEMA_VERSION,
                "status": "completed",
                "source": {
                    "experiment_config_sha256": config_sha256,
                    "builder_sha256": builder_sha256,
                    "builder_dependency_sha256": MODULE.BASE_ENGINE_SHA256,
                    "preprocessor_config_sha256": (MODULE.MODEL_PREPROCESSOR_SHA256),
                },
                "model": {
                    "id": MODULE.MODEL_ID,
                    "revision": MODULE.MODEL_REVISION,
                    "config_sha256": MODULE.MODEL_CONFIG_SHA256,
                    "checkpoint_sha256": MODULE.MODEL_CHECKPOINT_SHA256,
                    "preprocessor_config_sha256": (MODULE.MODEL_PREPROCESSOR_SHA256),
                    "hidden_size": MODULE.HIDDEN_SIZE,
                    "frozen": True,
                    "local_files_only": True,
                    "do_normalize": True,
                    "preprocessor_contract": {
                        "sampling_rate_hz": MODULE.SAMPLE_RATE_HZ,
                        "do_normalize": True,
                        "return_attention_mask": True,
                        "source": "official_pinned_revision",
                        "source_url": MODULE.PINNED_PREPROCESSOR_URL,
                        "config_sha256": MODULE.MODEL_PREPROCESSOR_SHA256,
                        "local_preprocessor_config_present": True,
                        "local_preprocessor_config_validated": True,
                        "auto_feature_extractor_used": False,
                    },
                },
                "runtime": {
                    "cuda_autocast_precision": "bfloat16",
                    "storage_precision": "float16",
                    "observed_model_output_dtypes": {"float32": 102},
                    "oom_count": 0,
                },
                "policy": {
                    "allowed_splits": ["train", "dev"],
                    "metric_evaluation_performed": False,
                    "oracle_alignment": True,
                    "source_manifest_columns_deserialized_before_split_filter": (
                        list(MODULE.SOURCE_COLUMNS)
                    ),
                    "evaluation_manifest_annotations_may_be_deserialized_for_filtering": (
                        True
                    ),
                    "evaluation_audio_decoded": False,
                    "evaluation_rows_sent_to_encoder": False,
                    "evaluation_labels_used_for_training_or_metrics": False,
                },
                "configuration": {"limit": 100, "full_run": False},
                "progress": {
                    "included_rows_by_split": {"train": 100},
                },
            }
        ),
        encoding="utf-8",
    )
    review = tmp_path / "large-smoke-review.json"
    review.write_text(
        json.dumps(
            {
                "schema_version": MODULE.SMOKE_REVIEW_SCHEMA_VERSION,
                "status": "approved",
                "full_run_approved": True,
                "review_type": "automated_engineering_gate",
                "human_expert_review": False,
                "smoke_report_path": str(smoke),
                "smoke_report_sha256": _sha256(smoke),
                "experiment_config_sha256": config_sha256,
                "builder_sha256": builder_sha256,
                "builder_dependency_sha256": MODULE.BASE_ENGINE_SHA256,
                "reviewer_source": reviewer_source,
                "source_contract": {
                    "model_id": MODULE.MODEL_ID,
                    "model_revision": MODULE.MODEL_REVISION,
                    "preprocessor_config_sha256": (MODULE.MODEL_PREPROCESSOR_SHA256),
                    "do_normalize": True,
                    "allowed_splits": ["train", "dev"],
                    "observed_output_splits": {
                        "utterance": ["train"],
                        "phone": ["train"],
                        "word": ["train"],
                    },
                    "allowed_split_coverage_complete": False,
                    "full_cache_validation_requires_all_allowed_splits": True,
                    "metric_evaluation_performed": False,
                    "evaluation_manifest_annotations_may_be_deserialized_for_filtering": (
                        True
                    ),
                    "evaluation_audio_decoded": False,
                    "evaluation_rows_sent_to_encoder": False,
                    "evaluation_labels_used_for_training_or_metrics": False,
                },
                "technical_checks": {
                    "report_status_completed": True,
                    "smoke_utterance_limit": 100,
                    "embedding_dimensions": {
                        "utterance_mean_std": 2_048,
                        "phone_mean": 1_024,
                        "word_mean": 1_024,
                    },
                    "all_reported_shard_sizes_and_sha256_match": True,
                    "storage_dtype_float16": True,
                    "pinned_preprocessor_config_validated": True,
                    "all_embeddings_finite_and_nonzero": True,
                    "prohibited_output_column_hits": [],
                    "identifier_columns_are_lowercase_sha256": True,
                    "frame_geometry_check_passed": True,
                    "oom_count": 0,
                },
                "resource_review": {
                    "approved": True,
                    "peak_rss_bytes": 1_000_000,
                    "estimated_full_cache_upper_bytes": 8_000_000_000,
                    "max_row_group_rows": 2_048,
                },
            }
        ),
        encoding="utf-8",
    )
    return smoke, review


def test_base_engine_is_immutable_and_pinned() -> None:
    assert _sha256(BASE_SCRIPT) == MODULE.BASE_ENGINE_SHA256


def test_pending_review_template_pins_current_code_and_config() -> None:
    template = json.loads(REVIEW_TEMPLATE.read_text(encoding="utf-8"))

    assert template["status"] == "pending"
    assert template["full_run_approved"] is False
    assert template["human_expert_review"] is False
    assert template["experiment_config_sha256"] == _sha256(CONFIG)
    assert template["builder_sha256"] == _sha256(SCRIPT)
    assert template["builder_dependency_sha256"] == _sha256(BASE_SCRIPT)
    assert template["smoke_report_sha256"] is None
    assert template["smoke_report_path"].endswith("_smoke100_v2_report.json")
    assert template["reviewer_source"] == {
        "cli_path": MODULE.REVIEWER_CLI_RELATIVE_PATH.as_posix(),
        "cli_sha256": _sha256(
            MODULE.REPOSITORY_ROOT / MODULE.REVIEWER_CLI_RELATIVE_PATH
        ),
        "core_path": MODULE.REVIEWER_CORE_RELATIVE_PATH.as_posix(),
        "core_sha256": _sha256(
            MODULE.REPOSITORY_ROOT / MODULE.REVIEWER_CORE_RELATIVE_PATH
        ),
    }
    assert template["source_contract"]["observed_output_splits"] is None
    assert template["source_contract"]["allowed_split_coverage_complete"] is None
    assert (
        template["source_contract"]["full_cache_validation_requires_all_allowed_splits"]
        is True
    )


def test_config_pins_large_architecture_precision_and_full_gate() -> None:
    config = MODULE.load_and_validate_config(CONFIG)

    assert config["model"]["id"] == "microsoft/wavlm-large"
    assert config["model"]["revision"] == MODULE.MODEL_REVISION
    assert config["model"]["hidden_size"] == 1_024
    assert config["model"]["num_hidden_layers"] == 24
    assert config["model"]["num_attention_heads"] == 16
    assert config["model"]["conv_kernel"] == [10, 3, 3, 3, 3, 2, 2]
    assert config["model"]["conv_stride"] == [5, 2, 2, 2, 2, 2, 2]
    assert config["features"]["cuda_autocast_precision"] == "bfloat16"
    assert config["features"]["observed_model_output_dtype"] == ("record_at_runtime")
    assert config["features"]["storage_precision"] == "float16"
    assert config["model"]["do_normalize"] is True
    assert (
        config["model"]["preprocessor_config_sha256"]
        == MODULE.MODEL_PREPROCESSOR_SHA256
    )
    assert config["full_run_gate"]["require_completed_base_plus_head_result"] is True


def test_local_large_metadata_and_hashes_are_pinned_without_model_load() -> None:
    metadata = MODULE.inspect_pinned_local_metadata(
        cache_dir=Path("data/cache/huggingface/hub")
    )

    assert metadata["hashes"] == {
        "config": MODULE.MODEL_CONFIG_SHA256,
        "checkpoint": MODULE.MODEL_CHECKPOINT_SHA256,
        "preprocessor": MODULE.MODEL_PREPROCESSOR_SHA256,
    }
    assert metadata["architecture"]["hidden_size"] == 1_024
    assert metadata["architecture"]["num_hidden_layers"] == 24
    assert metadata["frame_stride_samples"] == 320
    assert metadata["receptive_field_samples"] == 400
    assert metadata["preprocessor"]["do_normalize"] is True
    assert metadata["preprocessor"]["return_attention_mask"] is True


def test_model_loader_is_frozen_revision_pinned_and_local_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import transformers

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class FakeLoadedLarge(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.weight = torch.nn.Parameter(torch.ones(1))
            self.config = SimpleNamespace(
                _commit_hash=MODULE.MODEL_REVISION,
                hidden_size=MODULE.HIDDEN_SIZE,
                num_hidden_layers=MODULE.NUM_HIDDEN_LAYERS,
                num_attention_heads=MODULE.NUM_ATTENTION_HEADS,
                intermediate_size=MODULE.INTERMEDIATE_SIZE,
                conv_dim=list(MODULE.CONV_DIM),
                conv_kernel=list(MODULE.CONV_KERNEL),
                conv_stride=list(MODULE.CONV_STRIDE),
                feat_extract_norm=MODULE.FEAT_EXTRACT_NORM,
                feat_extract_activation=MODULE.FEAT_EXTRACT_ACTIVATION,
                hidden_act=MODULE.HIDDEN_ACTIVATION,
            )

    def fake_from_pretrained(
        *args: object,
        **kwargs: object,
    ) -> FakeLoadedLarge:
        calls.append((args, kwargs))
        return FakeLoadedLarge()

    monkeypatch.setattr(
        MODULE,
        "inspect_pinned_local_metadata",
        lambda cache_dir: {
            "hashes": {
                "config": MODULE.MODEL_CONFIG_SHA256,
                "checkpoint": MODULE.MODEL_CHECKPOINT_SHA256,
                "preprocessor": MODULE.MODEL_PREPROCESSOR_SHA256,
            }
        },
    )
    monkeypatch.setattr(
        transformers.WavLMModel,
        "from_pretrained",
        fake_from_pretrained,
    )

    model, metadata = MODULE.load_frozen_model(
        cache_dir=tmp_path,
        device=torch.device("cpu"),
    )

    assert calls == [
        (
            (MODULE.MODEL_ID,),
            {
                "revision": MODULE.MODEL_REVISION,
                "cache_dir": str(tmp_path),
                "local_files_only": True,
                "use_safetensors": False,
            },
        )
    ]
    assert metadata["revision"] == MODULE.MODEL_REVISION
    assert metadata["local_files_only"] is True
    assert metadata["frozen"] is True
    assert not any(parameter.requires_grad for parameter in model.parameters())


def test_large_schema_keeps_base_columns_and_changes_only_dimensions() -> None:
    expected = {
        "utterance": [
            "schema_version",
            "oracle_alignment",
            "utterance_id_sha256",
            "speaker_id_sha256",
            "split",
            "prompt_sha256",
            "l1",
            "gender",
            "proficiency",
            "audio_duration_seconds",
            "chunk_count",
            "frame_count",
            "embedding_mean_std",
        ],
        "phone": [
            "schema_version",
            "oracle_alignment",
            "utterance_id_sha256",
            "speaker_id_sha256",
            "split",
            "prompt_sha256",
            "l1",
            "gender",
            "proficiency",
            "phone_index",
            "target_label",
            "oracle_start_seconds",
            "oracle_end_seconds",
            "pooled_frame_count",
            "embedding_mean",
            "binary_error_label",
        ],
        "word": [
            "schema_version",
            "oracle_alignment",
            "utterance_id_sha256",
            "speaker_id_sha256",
            "split",
            "prompt_sha256",
            "l1",
            "gender",
            "proficiency",
            "word_index",
            "oracle_start_seconds",
            "oracle_end_seconds",
            "pooled_frame_count",
            "embedding_mean",
        ],
    }
    schemas = {
        "utterance": MODULE.schema_with_metadata(
            MODULE.UTTERANCE_SCHEMA,
            kind="utterance",
            source_manifest_sha256="a" * 64,
            config_sha256="b" * 64,
        ),
        "phone": MODULE.schema_with_metadata(
            MODULE.PHONE_SCHEMA,
            kind="phone",
            source_manifest_sha256="a" * 64,
            config_sha256="b" * 64,
        ),
        "word": MODULE.schema_with_metadata(
            MODULE.WORD_SCHEMA,
            kind="word",
            source_manifest_sha256="a" * 64,
            config_sha256="b" * 64,
        ),
    }
    for kind, schema in schemas.items():
        assert schema.names == expected[kind]
        MODULE.assert_schema_privacy(schema)
        assert schema.metadata[b"voice_coach.model_id"] == b"microsoft/wavlm-large"
        assert schema.metadata[b"voice_coach.model_revision"] == (
            MODULE.MODEL_REVISION.encode("ascii")
        )
    assert schemas["utterance"].field("embedding_mean_std").type.list_size == 2_048
    assert schemas["phone"].field("embedding_mean").type.list_size == 1_024
    assert schemas["word"].field("embedding_mean").type.list_size == 1_024
    assert pa.types.is_float16(schemas["phone"].field("embedding_mean").type.value_type)


def test_extract_rows_uses_large_dimensions_and_records_observed_dtype(
    tmp_path: Path,
) -> None:
    timeline = np.arange(16_000, dtype=np.float32) / 16_000
    sf.write(
        tmp_path / "sample.wav",
        0.1 * np.sin(2 * np.pi * 220 * timeline),
        16_000,
    )
    extracted = MODULE.extract_rows(
        _source_row("extract"),
        dataset_root=tmp_path,
        model=FakeWavLMLarge(),
        device=torch.device("cpu"),
        autocast_dtype=None,
    )

    assert extracted.utterance["frame_count"] == 49
    assert len(extracted.utterance["embedding_mean_std"]) == 2_048
    assert all(len(row["embedding_mean"]) == 1_024 for row in extracted.phones)
    assert all(len(row["embedding_mean"]) == 1_024 for row in extracted.words)
    assert extracted.output_dtypes == ("float32",)
    assert [row["binary_error_label"] for row in extracted.phones] == [0, 1]
    assert "word_label" not in extracted.words[0]


def test_synthetic_build_filters_before_inference_and_reports_precision_roles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_root, manifest, reservation, config = _write_build_fixture(tmp_path)
    monkeypatch.setattr(MODULE, "MODELING_MANIFEST_SHA256", _sha256(manifest))
    _patch_cuda_and_model(monkeypatch)
    extracted_ids: list[str] = []

    def fake_extract(row: dict, **_: object) -> object:
        extracted_ids.append(row["id"])
        return _fake_extracted(row)

    monkeypatch.setattr(MODULE, "extract_rows", fake_extract)
    output = tmp_path / "cache"
    report_output = tmp_path / "report.json"
    report = MODULE.build_cache(
        argparse.Namespace(
            config=config,
            modeling_manifest=manifest,
            dataset_root=dataset_root,
            prompt_reservation=reservation,
            cache_dir=tmp_path / "model-cache",
            output=output,
            report_output=report_output,
            limit=100,
            allow_full=False,
            explicit_dev_report=None,
            smoke_review=None,
            batch_size=2,
            progress_every=0,
        )
    )

    assert extracted_ids == [f"included-{index:03d}" for index in range(100)]
    assert report["status"] == "completed"
    assert report["progress"]["exclusions"] == {
        "qc_flags_present": 1,
        "reserved_prompt": 1,
        "split_not_allowed": 1,
    }
    assert report["policy"]["holistic_score_columns_read"] == []
    assert report["runtime"]["cuda_autocast_precision"] == "bfloat16"
    assert report["runtime"]["storage_precision"] == "float16"
    assert report["runtime"]["observed_model_output_dtypes"] == {"float32": 100}
    assert report["source"]["builder_dependency_sha256"] == (MODULE.BASE_ENGINE_SHA256)
    assert (
        report["source"]["preprocessor_config_sha256"]
        == MODULE.MODEL_PREPROCESSOR_SHA256
    )
    assert report["model"]["do_normalize"] is True
    assert (
        report["model"]["preprocessor_config_sha256"]
        == MODULE.MODEL_PREPROCESSOR_SHA256
    )
    assert (
        report["policy"][
            "evaluation_manifest_annotations_may_be_deserialized_for_filtering"
        ]
        is True
    )
    assert report["policy"]["evaluation_audio_decoded"] is False
    assert report["policy"]["evaluation_rows_sent_to_encoder"] is False
    assert report["policy"]["evaluation_labels_used_for_training_or_metrics"] is False
    assert report["configuration"]["embedding_dimensions"] == {
        "utterance_mean_std": 2_048,
        "phone_mean": 1_024,
        "word_mean": 1_024,
    }
    assert pq.read_table(output / "utterance").num_rows == 100
    assert pq.read_table(output / "phone").num_rows == 100
    assert pq.read_table(output / "word").num_rows == 100
    assert not list(tmp_path.glob(".cache.part-*"))


def test_synthetic_failure_cleans_partial_output_and_sanitizes_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_root, manifest, reservation, config = _write_build_fixture(tmp_path)
    monkeypatch.setattr(MODULE, "MODELING_MANIFEST_SHA256", _sha256(manifest))
    _patch_cuda_and_model(monkeypatch)

    def fail_extract(row: dict, **_: object) -> object:
        del row
        raise RuntimeError("restricted/source/path/must/not/reach/report")

    monkeypatch.setattr(MODULE, "extract_rows", fail_extract)
    output = tmp_path / "failed-cache"
    report_output = tmp_path / "failed-report.json"
    args = argparse.Namespace(
        config=config,
        modeling_manifest=manifest,
        dataset_root=dataset_root,
        prompt_reservation=reservation,
        cache_dir=tmp_path / "model-cache",
        output=output,
        report_output=report_output,
        limit=100,
        allow_full=False,
        explicit_dev_report=None,
        smoke_review=None,
        batch_size=2,
        progress_every=0,
    )

    with pytest.raises(RuntimeError):
        MODULE.build_cache(args)

    report = json.loads(report_output.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["failure"]["cause_type"] == "RuntimeError"
    assert "restricted/source/path" not in report_output.read_text(encoding="utf-8")
    assert not output.exists()
    assert not list(tmp_path.glob(".failed-cache.part-*"))


def test_full_gate_requires_completed_base_head_and_large_review() -> None:
    with pytest.raises(ValueError, match="base-plus-head-report"):
        MODULE.validate_full_run_gate(
            explicit_dev_report=None,
            smoke_review=None,
            expected_config_sha256="a" * 64,
            expected_builder_sha256="b" * 64,
        )


def test_cli_smoke_requires_exact_limit_and_isolated_outputs() -> None:
    common = {
        "config": CONFIG,
        "batch_size": 256,
        "progress_every": 10,
        "limit": 100,
        "allow_full": False,
        "output": None,
        "report_output": None,
        "explicit_dev_report": None,
        "smoke_review": None,
    }
    with pytest.raises(ValueError, match="explicit --output"):
        MODULE.validate_cli_args(argparse.Namespace(**common))

    common["output"] = Path("smoke-cache")
    common["report_output"] = Path("smoke-report.json")
    MODULE.validate_cli_args(argparse.Namespace(**common))

    common["limit"] = 99
    with pytest.raises(ValueError, match="--limit 100"):
        MODULE.validate_cli_args(argparse.Namespace(**common))


def test_cli_rejects_direct_full_bypass_and_canonical_smoke_paths() -> None:
    neither_mode = argparse.Namespace(
        config=CONFIG,
        batch_size=256,
        progress_every=10,
        limit=None,
        allow_full=False,
        output=None,
        report_output=None,
        explicit_dev_report=None,
        smoke_review=None,
    )
    with pytest.raises(ValueError, match="exactly one"):
        MODULE.validate_cli_args(neither_mode)

    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    canonical_smoke = argparse.Namespace(
        config=CONFIG,
        batch_size=256,
        progress_every=10,
        limit=100,
        allow_full=False,
        output=Path(config["output"]["root"]),
        report_output=Path(config["output"]["report"]),
        explicit_dev_report=None,
        smoke_review=None,
    )
    with pytest.raises(ValueError, match="canonical full-run"):
        MODULE.validate_cli_args(canonical_smoke)


def test_config_rejects_base_plus_normalization_policy(tmp_path: Path) -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config["model"]["do_normalize"] = False
    path = tmp_path / "wrong-normalization.yaml"
    path.write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="do_normalize"):
        MODULE.load_and_validate_config(path)


def test_local_model_contract_requires_pinned_preprocessor(tmp_path: Path) -> None:
    snapshot = tmp_path / MODULE.MODEL_CACHE_NAME / "snapshots" / MODULE.MODEL_REVISION
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    (snapshot / "pytorch_model.bin").write_bytes(b"checkpoint")

    with pytest.raises(FileNotFoundError, match="preprocessor"):
        MODULE.resolve_local_model_files(cache_dir=tmp_path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (("schema_version", "unknown"), "schema"),
        (("resource_review.peak_rss_bytes", True), "resource"),
        (
            ("source_contract.preprocessor_config_sha256", "0" * 64),
            "source contract",
        ),
        (("reviewer_source", None), "reviewer source pins"),
    ],
)
def test_smoke_review_rejects_schema_bool_resource_and_preprocessor_drift(
    tmp_path: Path,
    mutation: tuple[str, object],
    message: str,
) -> None:
    _, review_path = _write_smoke_and_review(
        tmp_path,
        config_sha256="a" * 64,
        builder_sha256="b" * 64,
    )
    review = json.loads(review_path.read_text(encoding="utf-8"))
    dotted, value = mutation
    target = review
    keys = dotted.split(".")
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    review_path.write_text(json.dumps(review), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        MODULE._validate_large_smoke_review(
            review_path,
            expected_config_sha256="a" * 64,
            expected_builder_sha256="b" * 64,
        )


def test_full_gate_accepts_exact_base_head_and_hash_pinned_large_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head = tmp_path / "artifacts" / "experiments" / "base-head" / "report.json"
    registry_metadata = _write_base_plus_head_report(
        head,
        repository_root=tmp_path,
        monkeypatch=monkeypatch,
    )
    _, review = _write_smoke_and_review(
        tmp_path,
        config_sha256="a" * 64,
        builder_sha256="b" * 64,
    )

    gate = MODULE.validate_full_run_gate(
        explicit_dev_report=head,
        smoke_review=review,
        expected_config_sha256="a" * 64,
        expected_builder_sha256="b" * 64,
    )

    assert gate["base_plus_head_report_sha256"] == _sha256(head)
    assert gate["base_plus_head_registry_metadata_sha256"] == _sha256(registry_metadata)
    assert gate["smoke_review_sha256"] == _sha256(review)
    assert gate["approved_config_sha256"] == "a" * 64
    assert gate["approved_builder_sha256"] == "b" * 64
    assert gate["approved_builder_dependency_sha256"] == (MODULE.BASE_ENGINE_SHA256)
    assert gate["reviewer_cli_sha256"] == _sha256(
        MODULE.REPOSITORY_ROOT / MODULE.REVIEWER_CLI_RELATIVE_PATH
    )
    assert gate["reviewer_core_sha256"] == _sha256(
        MODULE.REPOSITORY_ROOT / MODULE.REVIEWER_CORE_RELATIVE_PATH
    )


@pytest.mark.parametrize("role", ["cli", "core"])
def test_full_gate_rejects_tampered_reviewer_source_hashes(
    tmp_path: Path,
    role: str,
) -> None:
    _, review_path = _write_smoke_and_review(
        tmp_path,
        config_sha256="a" * 64,
        builder_sha256="b" * 64,
    )
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["reviewer_source"][f"{role}_sha256"] = "0" * 64
    review_path.write_text(json.dumps(review), encoding="utf-8")

    with pytest.raises(ValueError, match="reviewer source hash differs"):
        MODULE._validate_large_smoke_review(
            review_path,
            expected_config_sha256="a" * 64,
            expected_builder_sha256="b" * 64,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing_observed_splits", "split coverage is missing"),
        ("forged_coverage_flag", "coverage flag is forged"),
        ("missing_full_cache_requirement", "split requirement is missing"),
        ("forbidden_observed_split", "split coverage is invalid"),
        ("empty_observed_split", "split coverage is empty"),
    ],
)
def test_full_gate_rejects_missing_or_forged_split_coverage(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    _, review_path = _write_smoke_and_review(
        tmp_path,
        config_sha256="a" * 64,
        builder_sha256="b" * 64,
    )
    review = json.loads(review_path.read_text(encoding="utf-8"))
    source = review["source_contract"]
    if mutation == "missing_observed_splits":
        del source["observed_output_splits"]
    elif mutation == "forged_coverage_flag":
        source["allowed_split_coverage_complete"] = True
    elif mutation == "missing_full_cache_requirement":
        del source["full_cache_validation_requires_all_allowed_splits"]
    elif mutation == "forbidden_observed_split":
        source["observed_output_splits"]["phone"] = ["locked_test"]
    elif mutation == "empty_observed_split":
        source["observed_output_splits"]["word"] = []
    else:
        raise AssertionError(f"unknown mutation: {mutation}")
    review_path.write_text(json.dumps(review), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        MODULE._validate_large_smoke_review(
            review_path,
            expected_config_sha256="a" * 64,
            expected_builder_sha256="b" * 64,
        )


def test_full_gate_rejects_forbidden_split_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head = tmp_path / "artifacts" / "experiments" / "base-head" / "report.json"
    _write_base_plus_head_report(
        head,
        repository_root=tmp_path,
        monkeypatch=monkeypatch,
    )
    document = json.loads(head.read_text(encoding="utf-8"))
    document["leakage_controls"]["locked_test_accessed"] = True
    head.write_text(json.dumps(document), encoding="utf-8")
    _, review = _write_smoke_and_review(
        tmp_path,
        config_sha256="a" * 64,
        builder_sha256="b" * 64,
    )

    with pytest.raises(ValueError, match="split isolation"):
        MODULE.validate_full_run_gate(
            explicit_dev_report=head,
            smoke_review=review,
            expected_config_sha256="a" * 64,
            expected_builder_sha256="b" * 64,
        )


def test_base_head_gate_rejects_source_hash_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head = tmp_path / "artifacts" / "experiments" / "base-head" / "report.json"
    _write_base_plus_head_report(
        head,
        repository_root=tmp_path,
        monkeypatch=monkeypatch,
    )
    document = json.loads(head.read_text(encoding="utf-8"))
    document["sources"]["trainer_source_sha256"] = "0" * 64
    head.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="source hash chain"):
        MODULE._validate_base_plus_head_report(head)


def test_base_head_gate_rejects_tampered_registered_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head = tmp_path / "artifacts" / "experiments" / "base-head" / "report.json"
    _write_base_plus_head_report(
        head,
        repository_root=tmp_path,
        monkeypatch=monkeypatch,
    )
    checkpoint = head.parent / "seed_42" / "checkpoint.pt"
    checkpoint.write_bytes(b"tampered checkpoint\n")

    with pytest.raises(ValueError, match="artifact hash differs"):
        MODULE._validate_base_plus_head_report(head)
