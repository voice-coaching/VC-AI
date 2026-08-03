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

from voice_coach.data import Interval


SCRIPT = Path(__file__).parents[2] / "scripts" / "build_wavlm_feature_cache.py"
SPEC = importlib.util.spec_from_file_location(
    "test_build_wavlm_feature_cache_module",
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


class FakeWavLM(torch.nn.Module):
    def forward(
        self,
        *,
        input_values: torch.Tensor,
        attention_mask: torch.Tensor,
    ) -> SimpleNamespace:
        del attention_mask
        frame_count = MODULE.WAVLM_BASE_PLUS_FRAME_GEOMETRY.output_frame_count(
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


def test_config_pins_official_preprocessing_and_local_revision() -> None:
    config = MODULE.load_and_validate_config(
        Path("configs/experiments/wavlm_base_plus_frozen_v1.yaml")
    )

    assert config["model"]["id"] == MODULE.MODEL_ID
    assert config["model"]["revision"] == MODULE.MODEL_REVISION
    assert config["model"]["do_normalize"] is False
    assert config["model"]["return_attention_mask"] is True
    assert config["model"]["local_files_only"] is True
    assert config["data"]["sample_rate_hz"] == 16_000
    assert config["data"]["allowed_splits"] == ["train", "dev"]


def test_wavlm_schemas_use_float16_and_separate_phone_label_role() -> None:
    source_hash = "a" * 64
    config_hash = "b" * 64
    schemas = {
        "utterance": MODULE.schema_with_metadata(
            MODULE.UTTERANCE_SCHEMA,
            kind="utterance",
            source_manifest_sha256=source_hash,
            config_sha256=config_hash,
        ),
        "phone": MODULE.schema_with_metadata(
            MODULE.PHONE_SCHEMA,
            kind="phone",
            source_manifest_sha256=source_hash,
            config_sha256=config_hash,
        ),
        "word": MODULE.schema_with_metadata(
            MODULE.WORD_SCHEMA,
            kind="word",
            source_manifest_sha256=source_hash,
            config_sha256=config_hash,
        ),
    }

    for schema in schemas.values():
        MODULE.assert_schema_privacy(schema)
        assert schema.metadata[b"voice_coach.privacy_reviewed"] == b"true"
        assert schema.metadata[b"voice_coach.contains_direct_identifiers"] == b"false"
        assert b"path" not in b",".join(name.encode("utf-8") for name in schema.names)
    assert schemas["utterance"].field("embedding_mean_std").type.list_size == 1_536
    assert pa.types.is_float16(
        schemas["utterance"].field("embedding_mean_std").type.value_type
    )
    assert schemas["phone"].field("embedding_mean").type.list_size == 768
    assert json.loads(schemas["phone"].metadata[b"voice_coach.label_columns"]) == [
        "binary_error_label"
    ]
    assert "binary_error_label" not in json.loads(
        schemas["phone"].metadata[b"voice_coach.input_feature_columns"]
    )
    assert "word_label" not in schemas["word"].names


def test_target_binary_label_uses_only_target_bearing_alignment() -> None:
    row = _source_row("example")
    targets = [
        Interval(0.10, 0.30, "ㄱ"),
        Interval(0.30, 0.55, "ㅏ"),
    ]

    labels = MODULE.target_binary_labels(row, targets)

    assert labels == [0, 1]


def test_extract_rows_pools_utterance_phone_and_unlabeled_word(
    tmp_path: Path,
) -> None:
    sample_rate = 16_000
    timeline = np.arange(sample_rate, dtype=np.float32) / sample_rate
    waveform = 0.1 * np.sin(2 * np.pi * 220 * timeline)
    sf.write(tmp_path / "sample.wav", waveform, sample_rate)
    row = _source_row("extract")

    extracted = MODULE.extract_rows(
        row,
        dataset_root=tmp_path,
        model=FakeWavLM(),
        device=torch.device("cpu"),
        autocast_dtype=None,
    )

    assert len(extracted.utterance["embedding_mean_std"]) == 1_536
    assert extracted.utterance["frame_count"] == 49
    assert len(extracted.phones) == 2
    assert [item["binary_error_label"] for item in extracted.phones] == [0, 1]
    assert all(len(item["embedding_mean"]) == 768 for item in extracted.phones)
    assert len(extracted.words) == 1
    assert len(extracted.words[0]["embedding_mean"]) == 768
    assert "word_label" not in extracted.words[0]
    forbidden = {"realized_label", "operation", "audio_path"}
    assert not (forbidden & set(extracted.phones[0]))
    assert not (forbidden & set(extracted.words[0]))


def _write_build_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, str]:
    dataset_root = tmp_path / "dataset"
    dataset_root.mkdir()
    sf.write(
        dataset_root / "sample.wav",
        np.zeros(16_000, dtype=np.float32),
        16_000,
    )
    reserved_hash = _hash("reserved")
    rows = [
        _source_row("locked", split="locked_test"),
        _source_row("qc", qc_flags=["bad"]),
        _source_row("reserved", prompt_hash=reserved_hash),
        _source_row("train"),
        _source_row("dev", split="dev"),
    ]
    manifest = tmp_path / "modeling.parquet"
    pq.write_table(pa.Table.from_pylist(rows), manifest)
    reservation = tmp_path / "reservation.json"
    reservation.write_text(
        json.dumps(
            {
                "source": {"modeling_manifest_sha256": _sha256(manifest)},
                "reserved_prompts": [
                    {"prompt_sha256": reserved_hash},
                ],
            }
        ),
        encoding="utf-8",
    )
    source_config = yaml.safe_load(
        Path("configs/experiments/wavlm_base_plus_frozen_v1.yaml").read_text(
            encoding="utf-8"
        )
    )
    source_config["data"]["modeling_manifest_sha256"] = _sha256(manifest)
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(source_config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return dataset_root, manifest, reservation, str(config)


def _patch_cuda_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            "name": "test-gpu",
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
                "config_sha256": "c" * 64,
                "checkpoint_sha256": "d" * 64,
                "frozen": True,
                "do_normalize": False,
            },
        ),
    )


def test_build_cache_filters_before_inference_and_publishes_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_root, manifest, reservation, config = _write_build_fixture(tmp_path)
    _patch_cuda_and_model(monkeypatch)
    extracted_ids: list[str] = []

    def fake_extract(
        row: dict,
        **_: object,
    ) -> object:
        extracted_ids.append(row["id"])
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
                "embedding_mean_std": [0.0] * 1_536,
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
                    "embedding_mean": [0.0] * 768,
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
                    "embedding_mean": [0.0] * 768,
                },
            ),
            decoded_audio_seconds=1.0,
            model_frames=49,
            chunk_count=1,
            padded_short_chunks=0,
            output_dtypes=("bfloat16",),
        )

    monkeypatch.setattr(MODULE, "extract_rows", fake_extract)
    output = tmp_path / "cache"
    report_output = tmp_path / "report.json"
    args = argparse.Namespace(
        config=Path(config),
        modeling_manifest=manifest,
        dataset_root=dataset_root,
        prompt_reservation=reservation,
        cache_dir=tmp_path / "model-cache",
        output=output,
        report_output=report_output,
        limit=2,
        allow_full=False,
        explicit_dev_report=None,
        smoke_review=None,
        batch_size=2,
        progress_every=0,
    )

    report = MODULE.build_cache(args)

    assert extracted_ids == ["train", "dev"]
    assert report["status"] == "completed"
    assert report["progress"]["exclusions"] == {
        "qc_flags_present": 1,
        "reserved_prompt": 1,
        "split_not_allowed": 1,
    }
    assert report["policy"]["holistic_score_columns_read"] == []
    assert report["runtime"]["oom_count"] == 0
    assert pq.read_table(output / "utterance").num_rows == 2
    assert pq.read_table(output / "phone").num_rows == 2
    words = pq.read_table(output / "word")
    assert words.num_rows == 2
    assert "word_label" not in words.column_names
    assert not list(tmp_path.glob(".cache.part-*"))
    assert not any(
        token in column.lower()
        for table_kind in ("utterance", "phone", "word")
        for column in pq.read_schema(
            next((output / table_kind).glob("*.parquet"))
        ).names
        for token in ("path", "stem", "realized", "operation")
    )


def test_build_failure_removes_partial_root_and_writes_sanitized_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_root, manifest, reservation, config = _write_build_fixture(tmp_path)
    _patch_cuda_and_model(monkeypatch)

    def fail_extract(row: dict, **_: object) -> object:
        del row
        raise RuntimeError("restricted/source/path/must/not/reach/report")

    monkeypatch.setattr(MODULE, "extract_rows", fail_extract)
    output = tmp_path / "failed-cache"
    report_output = tmp_path / "failed-report.json"
    args = argparse.Namespace(
        config=Path(config),
        modeling_manifest=manifest,
        dataset_root=dataset_root,
        prompt_reservation=reservation,
        cache_dir=tmp_path / "model-cache",
        output=output,
        report_output=report_output,
        limit=2,
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
    assert report["failure"] == {
        "stage": "wavlm_inference",
        "cause_type": "RuntimeError",
        "utterance_id_sha256": MODULE.hash_utterance_id("train"),
    }
    assert "restricted/source/path" not in report_output.read_text(encoding="utf-8")
    assert not output.exists()
    assert not list(tmp_path.glob(".failed-cache.part-*"))


def test_full_run_requires_both_completed_gate_artifacts() -> None:
    with pytest.raises(ValueError, match="full run requires"):
        MODULE.validate_full_run_gate(
            explicit_dev_report=None,
            smoke_review=None,
            expected_config_sha256="a" * 64,
            expected_builder_sha256="b" * 64,
        )


def test_streaming_writer_bounds_buffer_and_separates_shards(
    tmp_path: Path,
) -> None:
    schema = pa.schema(
        [
            pa.field("index", pa.int32(), nullable=False),
            pa.field(
                "embedding",
                pa.list_(pa.float16(), 4),
                nullable=False,
            ),
        ]
    )
    writer = MODULE.ShardedWriter(
        tmp_path / "stream",
        schema,
        shard_rows=5,
        row_group_rows=2,
    )

    writer.append(
        {
            "index": index,
            "embedding": [float(index)] * 4,
        }
        for index in range(13)
    )
    shards = writer.finalize()

    assert writer.max_buffered_rows == 2
    assert [item["row_count"] for item in shards] == [5, 5, 3]
    assert [item["row_group_count"] for item in shards] == [3, 3, 2]
    assert pq.read_table(tmp_path / "stream").num_rows == 13


def test_full_gate_accepts_completed_actual_explicit_report_shape(
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "explicit-report.json"
    explicit.write_text(
        json.dumps(
            {
                "status": "completed",
                "development_only": True,
                "leakage_controls": {
                    "accepted_splits": ["train", "dev"],
                    "locked_test_accessed": False,
                    "stack_accessed": False,
                    "calibration_accessed": False,
                },
                "summary": {
                    "seeds_completed": [42, 43, 44],
                    "results_are_development_only": True,
                    "locked_test_remains_unread": True,
                },
            }
        ),
        encoding="utf-8",
    )
    review = tmp_path / "smoke-review.json"
    review.write_text(
        json.dumps(
            {
                "status": "approved",
                "full_run_approved": True,
                "smoke_report_sha256": "a" * 64,
                "experiment_config_sha256": "b" * 64,
                "builder_sha256": "c" * 64,
                "resource_review": {
                    "approved": True,
                    "peak_rss_bytes": 1_000_000,
                    "estimated_full_cache_upper_bytes": 6_000_000_000,
                    "max_row_group_rows": 2_048,
                },
            }
        ),
        encoding="utf-8",
    )

    gate = MODULE.validate_full_run_gate(
        explicit_dev_report=explicit,
        smoke_review=review,
        expected_config_sha256="b" * 64,
        expected_builder_sha256="c" * 64,
    )

    assert gate["explicit_dev_report_sha256"] == _sha256(explicit)
    assert gate["smoke_review_sha256"] == _sha256(review)
    assert gate["approved_smoke_report_sha256"] == "a" * 64
    assert gate["approved_config_sha256"] == "b" * 64
    assert gate["approved_builder_sha256"] == "c" * 64
