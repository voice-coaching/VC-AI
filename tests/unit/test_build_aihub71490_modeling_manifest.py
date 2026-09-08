from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pyarrow as pa
import pyarrow.parquet as pq
import pytest


SCRIPT = (
    Path(__file__).parents[2]
    / "scripts"
    / "build_aihub71490_modeling_manifest.py"
)
SPEC = importlib.util.spec_from_file_location(
    "build_aihub71490_modeling_manifest",
    SCRIPT,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _raw_record(
    record_id: str,
    *,
    prompt: str = "",
    pronunciation: object = None,
    fluency: object = None,
    comprehensibility: object = None,
) -> dict:
    return {
        "id": record_id,
        "json_metadata": {
            "SpeakerMetadata": {
                "UserID": "raw-user-id-must-not-be-exported",
                "nationality": "sensitive-raw-value",
                "recording_level": "중급",
                "topik_level": "4",
            },
            "RecordingMetadata": {
                "prompt": prompt,
                "sound_environment": "조용한 실내",
            },
            "EvaluationMetadata": {
                "PronunProfEval": pronunciation,
                "FluencyEval": fluency,
                "ComprehendEval": comprehensibility,
            },
        },
    }


def _write_raw_manifest(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )


def _run_builder(
    tmp_path: Path,
    *,
    enriched_rows: list[dict],
    raw_rows: list[dict],
) -> subprocess.CompletedProcess[str]:
    enriched = tmp_path / "enriched.parquet"
    raw = tmp_path / "raw.jsonl"
    output = tmp_path / "modeling.parquet"
    report = tmp_path / "report.json"
    pq.write_table(pa.Table.from_pylist(enriched_rows), enriched)
    _write_raw_manifest(raw, raw_rows)
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--enriched-manifest",
            str(enriched),
            "--raw-manifest",
            str(raw),
            "--output",
            str(output),
            "--report-output",
            str(report),
            "--batch-size",
            "1",
            "--progress-every",
            "0",
        ],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "score",
    [1, 2, 3, 4, 5, "1", "5", 1.0, 5.0],
)
def test_optional_score_accepts_only_official_scale_values(
    score: object,
) -> None:
    assert MODULE.optional_score(score) == int(score)


@pytest.mark.parametrize(
    "score",
    [None, "", 0, "0", -1, 6, "bad", True, False, 1.5, 5.1],
)
def test_optional_score_maps_sentinel_and_invalid_values_to_none(
    score: object,
) -> None:
    assert MODULE.optional_score(score) is None


def test_extract_model_fields_hashes_only_normalized_prompt() -> None:
    raw = _raw_record(
        "sample",
        prompt=" \u3000ＡＢＣ\t 안녕 \n 하세요  ",
        pronunciation=0,
        fluency=1,
        comprehensibility=5,
    )

    fields = MODULE.extract_model_fields(raw)
    normalized_prompt = "ABC 안녕 하세요"

    assert fields == {
        "prompt_sha256": hashlib.sha256(
            normalized_prompt.encode("utf-8")
        ).hexdigest(),
        "prompt_length_chars": len(normalized_prompt),
        "sound_environment": "조용한 실내",
        "recording_level": "중급",
        "topik_level": 4,
        "pronunciation_score": None,
        "fluency_score": 1,
        "comprehensibility_score": 5,
    }
    assert "prompt" not in fields
    assert "UserID" not in fields
    assert "nationality" not in fields


def test_builder_emits_privacy_safe_typed_schema_and_report(
    tmp_path: Path,
) -> None:
    result = _run_builder(
        tmp_path,
        enriched_rows=[
            {"id": "a", "split": "train"},
            {"id": "b", "split": "dev"},
        ],
        raw_rows=[
            _raw_record(
                "a",
                prompt=" 같은   문장 ",
                pronunciation=0,
                fluency=2,
                comprehensibility=5,
            ),
            _raw_record(
                "b",
                prompt="같은 문장",
                pronunciation=1,
                fluency=3,
                comprehensibility=6,
            ),
        ],
    )

    assert result.returncode == 0, result.stderr
    table = pq.read_table(tmp_path / "modeling.parquet")
    assert table.schema == pa.schema(
        [
            pa.field("id", pa.string()),
            pa.field("split", pa.string()),
            *MODULE.ADDED_SCHEMA,
        ]
    )
    assert table.column("pronunciation_score").to_pylist() == [None, 1]
    assert table.column("fluency_score").to_pylist() == [2, 3]
    assert table.column("comprehensibility_score").to_pylist() == [5, None]
    assert len(set(table.column("prompt_sha256").to_pylist())) == 1
    assert table.column("prompt_length_chars").to_pylist() == [5, 5]
    assert "json_metadata" not in table.column_names
    assert "prompt" not in table.column_names
    assert "UserID" not in table.column_names

    report = json.loads(
        (tmp_path / "report.json").read_text(encoding="utf-8")
    )
    assert report["record_count"] == 2
    assert report["unique_prompt_count"] == 1
    assert report["duplicate_prompt_record_count"] == 2
    assert report["score_distributions"] == {
        "pronunciation_score": {"1": 1},
        "fluency_score": {"2": 1, "3": 1},
        "comprehensibility_score": {"5": 1},
    }
    assert report["missing_score_counts"] == {
        "comprehensibility_score": 1,
        "pronunciation_score": 1,
    }


def test_builder_rejects_raw_and_enriched_id_order_mismatch(
    tmp_path: Path,
) -> None:
    result = _run_builder(
        tmp_path,
        enriched_rows=[
            {"id": "expected-first", "split": "train"},
            {"id": "expected-second", "split": "dev"},
        ],
        raw_rows=[
            _raw_record("expected-second"),
            _raw_record("expected-first"),
        ],
    )

    assert result.returncode != 0
    assert (
        "manifest order/id mismatch: expected-second != expected-first"
        in result.stderr
    )
    assert not (tmp_path / "modeling.parquet").exists()
    assert not (tmp_path / "report.json").exists()
