from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml

from voice_coach.reproducibility.wavlm_cache import (
    AUDITED_FULL_ROWS,
    validate_wavlm_feature_cache,
)


SCRIPT = Path(__file__).parents[2] / "scripts" / "validate_wavlm_feature_cache.py"
EXPECTED_ROWS = {"utterance": 4, "phone": 6, "word": 4}
MODEL_ID = "example/wavlm-test"
MODEL_REVISION = "1" * 40
SOURCE_HASH = "a" * 64


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _id(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _schema(
    kind: str,
    *,
    hidden_size: int,
    config_sha: str,
    privacy_column: bool = False,
) -> pa.Schema:
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
        fields = [
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
        inputs = ["embedding_mean_std"]
        labels: list[str] = []
        version = "voice_coach.wavlm_utterance_features.v1"
    elif kind == "phone":
        fields = [
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
        inputs = ["embedding_mean", "target_label"]
        labels = ["binary_error_label"]
        version = "voice_coach.wavlm_phone_features.v1"
    else:
        fields = [
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
        inputs = ["embedding_mean"]
        labels = []
        version = "voice_coach.wavlm_word_features.v1"
    if privacy_column:
        fields.append(pa.field("audio_path", pa.string(), nullable=False))
    metadata = {
        b"voice_coach.schema_version": version.encode(),
        b"voice_coach.model_id": MODEL_ID.encode(),
        b"voice_coach.model_revision": MODEL_REVISION.encode(),
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
        b"voice_coach.source_manifest_sha256": SOURCE_HASH.encode(),
        b"voice_coach.config_sha256": config_sha.encode(),
        b"voice_coach.input_feature_columns": json.dumps(
            inputs, separators=(",", ":")
        ).encode(),
        b"voice_coach.label_columns": json.dumps(
            labels, separators=(",", ":")
        ).encode(),
        b"voice_coach.slice_only_columns": b'["split","l1","gender","proficiency"]',
        b"voice_coach.forbidden_as_input": (
            b'["binary_error_label","realized_phone_identity",'
            b'"alignment_operation","error_tag","holistic_score"]'
        ),
    }
    return pa.schema(fields, metadata=metadata)


def _common(index: int, split: str) -> dict[str, Any]:
    return {
        "oracle_alignment": True,
        "utterance_id_sha256": _id(f"utterance:{index}"),
        "speaker_id_sha256": _id(f"speaker:{index}"),
        "split": split,
        "prompt_sha256": _id(f"prompt:{index}"),
        "l1": "Chinese" if split == "train" else "Japanese",
        "gender": "F",
        "proficiency": "Intermediate",
    }


def _rows(
    kind: str,
    *,
    hidden_size: int,
    mode: str | None,
) -> list[dict[str, Any]]:
    splits = ["train", "train", "dev", "dev"]
    common = [_common(index, split) for index, split in enumerate(splits)]
    if kind == "utterance":
        return [
            {
                "schema_version": "voice_coach.wavlm_utterance_features.v1",
                **item,
                "audio_duration_seconds": 1.0 + index,
                "chunk_count": 1,
                "frame_count": 10,
                "embedding_mean_std": [float(index + 1)] * (hidden_size * 2),
            }
            for index, item in enumerate(common)
        ]
    per_utterance = [2, 1, 2, 1] if kind == "phone" else [1, 1, 1, 1]
    output: list[dict[str, Any]] = []
    for utterance_index, count in enumerate(per_utterance):
        for child_index in range(count):
            index_value = child_index
            if mode in {"bad_index", "duplicate_key"} and kind == "phone":
                if utterance_index == 0 and child_index == 1:
                    index_value = 2 if mode == "bad_index" else 0
            item = {
                "schema_version": (
                    "voice_coach.wavlm_phone_features.v1"
                    if kind == "phone"
                    else "voice_coach.wavlm_word_features.v1"
                ),
                **common[utterance_index],
                f"{kind}_index": index_value,
                "oracle_start_seconds": child_index * 0.1,
                "oracle_end_seconds": child_index * 0.1 + 0.08,
                "pooled_frame_count": 3,
                "embedding_mean": [float(utterance_index + child_index + 1)]
                * hidden_size,
            }
            if kind == "phone":
                item["target_label"] = "ㄱ"
                item["binary_error_label"] = child_index % 2
            if mode == "nan" and kind == "phone" and not output:
                item["embedding_mean"][0] = float("nan")
            if mode == "forbidden_split" and kind == "word" and utterance_index == 3:
                item["split"] = "locked_test"
            if mode == "privacy" and kind == "word":
                item["audio_path"] = "must-not-export.wav"
            output.append(item)
    return output


def _write_shards(
    cache_root: Path,
    *,
    kind: str,
    rows: list[dict[str, Any]],
    schema: pa.Schema,
    shard_rows: int,
    row_group_rows: int,
) -> dict[str, Any]:
    directory = cache_root / kind
    directory.mkdir(parents=True)
    shards: list[dict[str, Any]] = []
    for shard_index, start in enumerate(range(0, len(rows), shard_rows)):
        path = directory / f"part-{shard_index:05d}.parquet"
        table = pa.Table.from_pylist(rows[start : start + shard_rows], schema=schema)
        pq.write_table(
            table,
            path,
            row_group_size=row_group_rows,
            compression="zstd",
        )
        parquet = pq.ParquetFile(path)
        shards.append(
            {
                "name": path.name,
                "row_count": parquet.metadata.num_rows,
                "row_group_count": parquet.metadata.num_row_groups,
                "byte_count": path.stat().st_size,
                "sha256": _sha(path),
            }
        )
    return {
        "row_count": len(rows),
        "shard_count": len(shards),
        "byte_count": sum(item["byte_count"] for item in shards),
        "content_sha256": _sha_json(
            [
                {
                    "name": item["name"],
                    "row_count": item["row_count"],
                    "sha256": item["sha256"],
                }
                for item in shards
            ]
        ),
        "shards": shards,
    }


@dataclass(frozen=True)
class Fixture:
    root: Path
    cache: Path
    report: Path
    config: Path
    builder: Path


def _fixture(
    tmp_path: Path,
    *,
    mode: str | None = None,
    hidden_size: int = 4,
) -> Fixture:
    prompt = tmp_path / "prompt-reservation.json"
    prompt.write_text('{"privacy_safe": true}', encoding="utf-8")
    builder = tmp_path / "builder.py"
    builder.write_text("# pinned synthetic builder\n", encoding="utf-8")
    model_snapshot = (
        tmp_path
        / "model-cache"
        / "models--example--wavlm-test"
        / "snapshots"
        / MODEL_REVISION
    )
    model_snapshot.mkdir(parents=True)
    model_config = model_snapshot / "config.json"
    model_checkpoint = model_snapshot / "pytorch_model.bin"
    model_config.write_text('{"hidden_size": 4}', encoding="utf-8")
    model_checkpoint.write_bytes(b"synthetic checkpoint")
    shard_rows = {"utterance": 2, "phone": 3, "word": 2}
    row_group_rows = {"utterance": 1, "phone": 1, "word": 1}
    config_value = {
        "model": {
            "id": MODEL_ID,
            "revision": MODEL_REVISION,
            "cache_dir": "model-cache",
            "local_files_only": True,
            "frozen": True,
            "hidden_size": hidden_size,
        },
        "data": {
            "modeling_manifest": "source-modeling-manifest-must-not-be-read.parquet",
            "modeling_manifest_sha256": SOURCE_HASH,
            "prompt_challenge_reservation": "prompt-reservation.json",
            "allowed_splits": ["train", "dev"],
            "forbidden_metric_splits": ["stack", "calibration", "locked_test"],
        },
        "features": {"storage_precision": "float16"},
        "output": {
            **{f"{kind}_shard_rows": value for kind, value in shard_rows.items()},
            **{
                f"{kind}_row_group_rows": value
                for kind, value in row_group_rows.items()
            },
            "compression": "zstd",
            "atomic_publish": True,
        },
        "full_run_gate": {
            "resource_estimate_reference_rows": dict(EXPECTED_ROWS),
        },
    }
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(config_value, sort_keys=False),
        encoding="utf-8",
    )
    config_sha = _sha(config)
    cache = tmp_path / "cache"
    cache.mkdir()
    outputs = {}
    for kind in ("utterance", "phone", "word"):
        outputs[kind] = _write_shards(
            cache,
            kind=kind,
            rows=_rows(kind, hidden_size=hidden_size, mode=mode),
            schema=_schema(
                kind,
                hidden_size=hidden_size,
                config_sha=config_sha,
                privacy_column=mode == "privacy" and kind == "word",
            ),
            shard_rows=shard_rows[kind],
            row_group_rows=row_group_rows[kind],
        )
    report_value = {
        "schema_version": "voice_coach.wavlm_feature_cache_report.v1",
        "status": "completed",
        "source": {
            "modeling_manifest_sha256": SOURCE_HASH,
            "prompt_reservation_sha256": _sha(prompt),
            "experiment_config_sha256": config_sha,
            "builder_sha256": _sha(builder),
        },
        "model": {
            "id": MODEL_ID,
            "revision": MODEL_REVISION,
            "config_sha256": _sha(model_config),
            "checkpoint_sha256": _sha(model_checkpoint),
            "hidden_size": hidden_size,
            "frozen": True,
            "local_files_only": True,
        },
        "runtime": {
            "inference_precision": "bfloat16",
            "storage_precision": "float16",
            "oom_count": 0,
        },
        "policy": {
            "allowed_splits": ["train", "dev"],
            "evaluation_splits": ["calibration", "locked_test", "stack"],
            "evaluation_split_metric_access": "forbidden",
            "holistic_score_columns_read": [],
            "metric_evaluation_performed": False,
            "oracle_alignment": True,
            "contains_direct_identifiers": False,
            "contains_transcript_text": False,
            "contains_audio_bytes": False,
            "contains_realized_phone_identity": False,
            "contains_alignment_operation": False,
            "shareable": False,
        },
        "configuration": {
            "limit": None,
            "full_run": True,
            "full_run_gate": {
                "approved_config_sha256": config_sha,
                "approved_builder_sha256": _sha(builder),
            },
            "streaming_writer": {
                kind: {
                    "shard_rows": shard_rows[kind],
                    "row_group_rows": row_group_rows[kind],
                }
                for kind in ("utterance", "phone", "word")
            },
        },
        "progress": {
            "source_rows_scanned": 8,
            "utterance_rows_written": EXPECTED_ROWS["utterance"],
            "phone_rows_written": EXPECTED_ROWS["phone"],
            "word_rows_written": EXPECTED_ROWS["word"],
            "source_scan_complete": True,
            "source_rows_by_split": {"train": 4, "dev": 4},
            "included_rows_by_split": {"train": 2, "dev": 2},
            "exclusions": {},
        },
        "outputs": outputs,
        "privacy_audit": {
            "schema_prohibited_column_hits": {
                "utterance": [],
                "phone": [],
                "word": [],
            },
            "raw_locator_values_exported": False,
            "raw_file_stem_values_exported": False,
            "realized_phone_values_exported": False,
            "alignment_operation_values_exported": False,
            "word_transcript_values_exported": False,
        },
    }
    report = tmp_path / "report.json"
    report.write_text(json.dumps(report_value), encoding="utf-8")
    return Fixture(
        root=tmp_path,
        cache=cache,
        report=report,
        config=config,
        builder=builder,
    )


def _validate(fixture: Fixture) -> dict[str, Any]:
    return validate_wavlm_feature_cache(
        cache_root=fixture.cache,
        report_path=fixture.report,
        config_path=fixture.config,
        builder_path=fixture.builder,
        workspace_root=fixture.root,
        batch_rows=1,
        expected_rows=EXPECTED_ROWS,
    )


def _codes(result: dict[str, Any]) -> set[str]:
    return {item["code"] for item in result["errors"]}


def test_audited_full_row_contract_is_pinned() -> None:
    assert AUDITED_FULL_ROWS == {
        "utterance": 169_837,
        "phone": 2_233_039,
        "word": 408_790,
    }


def test_multi_shard_streaming_validation_passes_without_source_manifest(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    source_manifest = tmp_path / "source-modeling-manifest-must-not-be-read.parquet"
    assert not source_manifest.exists()

    result = _validate(fixture)

    assert result["status"] == "passed"
    assert result["scan"]["rows_checked"] == EXPECTED_ROWS
    assert result["scan"]["embedding_vectors_checked"] == EXPECTED_ROWS
    assert result["scan"]["embedding_values_checked"] == {
        "utterance": 32,
        "phone": 24,
        "word": 16,
    }
    assert result["scan"]["forbidden_split_rows"] == {
        "utterance": 0,
        "phone": 0,
        "word": 0,
    }
    assert result["scan"]["nonfinite_embedding_values"] == {
        "utterance": 0,
        "phone": 0,
        "word": 0,
    }
    assert result["memory"]["full_embedding_matrix_materialized"] is False
    assert result["memory"]["max_batch_rows"] == 1
    assert result["privacy"]["source_modeling_manifest_read"] is False
    serialized = json.dumps(result)
    assert _id("utterance:0") not in serialized
    assert "must-not-be-read.parquet" not in serialized


def test_tampered_shard_fails_reported_hash(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    shard = fixture.cache / "utterance" / "part-00000.parquet"
    with shard.open("ab") as handle:
        handle.write(b"tamper")

    result = _validate(fixture)

    assert result["status"] == "failed"
    assert "utterance_shard_hash_mismatch" in _codes(result)


def test_builder_hash_change_fails(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture.builder.write_text("# changed after run\n", encoding="utf-8")

    result = _validate(fixture)

    assert result["status"] == "failed"
    assert "builder_hash_mismatch" in _codes(result)


def test_nan_embedding_fails_full_stream_scan(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, mode="nan")

    result = _validate(fixture)

    assert result["status"] == "failed"
    assert "phone_embedding_nonfinite" in _codes(result)
    assert result["scan"]["nonfinite_embedding_values"]["phone"] == 1
    assert result["scan"]["embedding_vectors_checked"]["phone"] == 6


@pytest.mark.parametrize("mode", ["bad_index", "duplicate_key"])
def test_bad_or_duplicate_child_index_fails(tmp_path: Path, mode: str) -> None:
    fixture = _fixture(tmp_path, mode=mode)

    result = _validate(fixture)

    assert result["status"] == "failed"
    assert "phone_index_not_contiguous" in _codes(result)


def test_forbidden_split_and_join_mismatch_fail(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, mode="forbidden_split")

    result = _validate(fixture)

    assert result["status"] == "failed"
    codes = _codes(result)
    assert "word_forbidden_split_row" in codes
    assert "word_forbidden_split_count_nonzero" in codes
    assert "word_utterance_join_mismatch" in codes
    assert result["scan"]["forbidden_split_rows"]["word"] == 1


def test_privacy_column_fails_even_when_report_claims_zero_hits(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, mode="privacy")

    result = _validate(fixture)

    assert result["status"] == "failed"
    codes = _codes(result)
    assert "word_schema_mismatch" in codes
    assert "word_privacy_prohibited_column" in codes


def test_cli_writes_atomic_aggregate_failure_report_and_exits_nonzero(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    fixture.builder.write_text("# changed after run\n", encoding="utf-8")
    output = tmp_path / "validation.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--workspace-root",
            str(tmp_path),
            "--cache-root",
            str(fixture.cache),
            "--report",
            str(fixture.report),
            "--config",
            str(fixture.config),
            "--builder",
            str(fixture.builder),
            "--output",
            str(output),
            "--batch-rows",
            "1",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["privacy"]["aggregate_only_output"] is True
    assert not list(tmp_path.glob(".validation.json.*.part"))
    assert _id("utterance:0") not in output.read_text(encoding="utf-8")
