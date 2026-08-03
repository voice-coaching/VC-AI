from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import soundfile as sf


SCRIPT = (
    Path(__file__).parents[2] / "scripts" / "build_explicit_feature_cache.py"
)
SPEC = importlib.util.spec_from_file_location(
    "test_build_explicit_feature_cache_module",
    SCRIPT,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _interval(start: float, end: float, label: str) -> dict:
    return {"start": start, "end": end, "label": label}


def _row(
    record_id: str,
    split: str,
    audio_path: str,
    prompt_hash: str,
    *,
    qc_flags: list[str] | None = None,
) -> dict:
    return {
        "schema_version": "aihub71490.enriched.v1",
        "id": record_id,
        "speaker_id": hashlib.sha256(
            f"speaker-{record_id}".encode()
        ).hexdigest(),
        "split": split,
        "official_partitions": ["Training"],
        "audio_path": audio_path,
        "json_path": "restricted.json",
        "textgrid_path": "restricted.TextGrid",
        "duration_seconds": 1.0,
        "sample_rate_hz": 8_000,
        "channels": 1,
        "pcm_sha256": "f" * 64,
        "l1": "Chinese",
        "gender": "F",
        "proficiency": "Intermediate",
        "error_presence": True,
        "word_intervals": [
            _interval(0.10, 0.40, "안녕"),
            _interval(0.50, 0.90, "하세요"),
        ],
        "target_phone_intervals": [
            _interval(0.10, 0.30, "ㄴ"),
            _interval(0.50, 0.70, "ㅏ"),
        ],
        "realized_phone_intervals": [
            _interval(0.10, 0.30, "ㄴ"),
            _interval(0.50, 0.70, "ㅓ"),
        ],
        "error_intervals": [_interval(0.50, 0.70, "중성")],
        "phone_alignments": [
            {
                "target_label": "ㄴ",
                "realized_label": "ㄴ",
                "start": 0.10,
                "end": 0.30,
                "overlap_seconds": 0.20,
                "overlap_ratio": 1.0,
                "operation": "match",
            },
            {
                "target_label": "ㅏ",
                "realized_label": "ㅓ",
                "start": 0.50,
                "end": 0.70,
                "overlap_seconds": 0.20,
                "overlap_ratio": 1.0,
                "operation": "substitution",
            },
        ],
        "qc_flags": qc_flags or [],
        "prompt_sha256": prompt_hash,
        "prompt_length_chars": 5,
        "sound_environment": "online",
        "recording_level": "Advanced",
        "topik_level": 4,
        "pronunciation_score": 4,
        "fluency_score": 3,
        "comprehensibility_score": 5,
    }


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    dataset_root = tmp_path / "dataset"
    dataset_root.mkdir()
    sample_rate = 8_000
    time = np.arange(sample_rate) / sample_rate
    samples = 0.2 * np.sin(2 * np.pi * 200 * time)
    sf.write(dataset_root / "audio.wav", samples, sample_rate)
    reserved_hash = hashlib.sha256(b"reserved").hexdigest()
    open_hash = hashlib.sha256(b"open").hexdigest()
    rows = [
        _row("train-ok", "train", "audio.wav", open_hash),
        _row("dev-ok", "dev", "audio.wav", open_hash),
        _row("dev-reserved", "dev", "audio.wav", reserved_hash),
        _row(
            "train-qc",
            "train",
            "audio.wav",
            open_hash,
            qc_flags=["interval_out_of_bounds"],
        ),
        _row("locked", "locked_test", "audio.wav", open_hash),
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
    return dataset_root, manifest, reservation, open_hash


def _args(
    tmp_path: Path,
    dataset_root: Path,
    manifest: Path,
    reservation: Path,
    *,
    phone: bool = True,
) -> argparse.Namespace:
    return argparse.Namespace(
        modeling_manifest=manifest,
        dataset_root=dataset_root,
        prompt_reservation=reservation,
        output=tmp_path / "utterance.parquet",
        report_output=tmp_path / "report.json",
        phone_output=(tmp_path / "phones") if phone else None,
        canonical_utterance_cache=None,
        splits=["train", "dev"],
        batch_size=1,
        workers=1,
        phone_shard_size=3,
        progress_every=0,
        limit=None,
        disable_f0=False,
    )


def test_build_cache_excludes_qc_reserved_and_non_allowed_splits(
    tmp_path: Path,
) -> None:
    dataset_root, manifest, reservation, open_hash = _fixture(tmp_path)
    args = _args(tmp_path, dataset_root, manifest, reservation)

    report = MODULE.build_cache(args)

    table = pq.read_table(args.output)
    assert table.column("utterance_id_sha256").to_pylist() == [
        MODULE.hash_utterance_id("train-ok"),
        MODULE.hash_utterance_id("dev-ok"),
    ]
    assert table.column("split").to_pylist() == ["train", "dev"]
    assert table.column("prompt_sha256").to_pylist() == [open_hash, open_hash]
    assert table.column("model_sample_rate_hz").to_pylist() == [16_000, 16_000]
    assert table.column("source_sample_rate_hz").to_pylist() == [8_000, 8_000]
    assert table.column("word_count").to_pylist() == [2, 2]
    assert table.column("target_phone_count").to_pylist() == [2, 2]
    assert table.column("speech_rate_words_per_second").to_pylist() == [2.0, 2.0]
    assert table.column("articulation_rate_words_per_second").to_pylist() == (
        pytest.approx([2 / 0.7, 2 / 0.7])
    )
    assert table.column("target_phone_label_duration_npvi").to_pylist() == [
        pytest.approx(0.0),
        pytest.approx(0.0),
    ]
    assert table.column("pronunciation_score").to_pylist() == [4, 4]
    assert table.schema.metadata[b"voice_coach.oracle_alignment"] == b"true"
    assert table.schema.metadata[b"voice_coach.privacy_safe"] == b"true"
    assert "pronunciation_score" not in json.loads(
        table.schema.metadata[b"voice_coach.input_feature_columns"]
    )
    assert "l1" not in json.loads(
        table.schema.metadata[b"voice_coach.input_feature_columns"]
    )
    assert "l1" in json.loads(
        table.schema.metadata[b"voice_coach.slice_only_columns"]
    )
    assert not (set(table.column_names) & MODULE.PROHIBITED_OUTPUT_COLUMNS)

    shards = sorted(args.phone_output.glob("part-*.parquet"))
    assert len(shards) == 2
    phones = pq.read_table(args.phone_output)
    assert phones.num_rows == 4
    assert phones.column("binary_error_label").to_pylist() == [0, 1, 0, 1]
    assert "binary_error_label" not in json.loads(
        phones.schema.metadata[b"voice_coach.input_feature_columns"]
    )
    assert "binary_error_label" == json.loads(
        phones.schema.metadata[b"voice_coach.label_columns"]
    )[0]
    assert phones.column("previous_target_label").to_pylist() == [
        None,
        "ㄴ",
        None,
        "ㄴ",
    ]
    assert phones.column("next_target_label").to_pylist() == [
        "ㅏ",
        None,
        "ㅏ",
        None,
    ]
    assert phones.schema.metadata[b"voice_coach.oracle_alignment"] == b"true"

    assert report["status"] == "completed"
    assert report["policy"]["metric_evaluation_performed"] is False
    assert report["progress"]["exclusions"] == {
        "qc_flags_present": 1,
        "reserved_prompt": 1,
        "split_not_allowed": 1,
    }
    assert report["outputs"]["utterance"]["row_count"] == 2
    assert report["outputs"]["phone"]["row_count"] == 4
    assert report["outputs"]["utterance"]["sha256"] == _sha256(args.output)
    assert report["throughput"]["utterances_per_wall_second"] > 0
    assert report["policy"][
        "speaker_id_sha256_is_pseudonymous_hash_not_raw_user_id"
    ]
    assert report["policy"][
        "utterance_id_sha256_is_namespaced_hash_not_raw_file_stem"
    ]
    assert not list(tmp_path.glob("*.part-*"))


def test_limit_is_applied_after_exclusions_and_marks_partial_scan(
    tmp_path: Path,
) -> None:
    dataset_root, manifest, reservation, _ = _fixture(tmp_path)
    args = _args(tmp_path, dataset_root, manifest, reservation, phone=False)
    args.limit = 1

    report = MODULE.build_cache(args)

    assert pq.read_table(args.output).num_rows == 1
    assert report["progress"]["source_rows_scanned"] == 1
    assert report["progress"]["source_scan_complete"] is False
    assert report["configuration"]["limit"] == 1


def test_disable_f0_emits_nulls_and_records_cheap_profile(
    tmp_path: Path,
) -> None:
    dataset_root, manifest, reservation, _ = _fixture(tmp_path)
    args = _args(tmp_path, dataset_root, manifest, reservation, phone=False)
    args.limit = 1
    args.disable_f0 = True

    report = MODULE.build_cache(args)

    row = pq.read_table(args.output).to_pylist()[0]
    assert row["f0_median_hz"] is None
    assert row["f0_range_hz"] is None
    assert report["configuration"]["feature_profile"] == "cheap_without_f0"
    assert report["configuration"]["disabled_features"] == [
        "f0_median_hz",
        "f0_range_hz",
    ]


def test_phone_run_marks_utterance_output_as_incidental_duplicate(
    tmp_path: Path,
) -> None:
    dataset_root, manifest, reservation, _ = _fixture(tmp_path)
    canonical = tmp_path / "canonical.parquet"
    canonical.write_bytes(b"canonical utterance cache")
    args = _args(tmp_path, dataset_root, manifest, reservation)
    args.limit = 1
    args.disable_f0 = True
    args.canonical_utterance_cache = canonical

    report = MODULE.build_cache(args)

    assert report["configuration"]["utterance_output_role"] == (
        "incidental_duplicate_for_phone_cache"
    )
    assert report["outputs"]["utterance"]["role"] == (
        "incidental_duplicate_for_phone_cache"
    )
    assert report["outputs"]["utterance"][
        "canonical_utterance_cache_sha256"
    ] == _sha256(canonical)


def test_bounded_process_workers_preserve_manifest_order(
    tmp_path: Path,
) -> None:
    dataset_root, manifest, reservation, _ = _fixture(tmp_path)
    output = tmp_path / "parallel.parquet"
    report_output = tmp_path / "parallel-report.json"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--modeling-manifest",
            str(manifest),
            "--dataset-root",
            str(dataset_root),
            "--prompt-reservation",
            str(reservation),
            "--output",
            str(output),
            "--report-output",
            str(report_output),
            "--batch-size",
            "1",
            "--workers",
            "2",
            "--progress-every",
            "0",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert pq.read_table(output).column(
        "utterance_id_sha256"
    ).to_pylist() == [
        MODULE.hash_utterance_id("train-ok"),
        MODULE.hash_utterance_id("dev-ok"),
    ]
    report = json.loads(report_output.read_text(encoding="utf-8"))
    assert report["configuration"]["process_workers"] == 2


def test_failure_is_reported_without_publishing_partial_outputs(
    tmp_path: Path,
) -> None:
    dataset_root, manifest, reservation, _ = _fixture(tmp_path)
    args = _args(tmp_path, dataset_root, manifest, reservation)
    # The first otherwise-eligible row now fails in the read-only decoder.
    (dataset_root / "audio.wav").unlink()

    with pytest.raises(MODULE.FeatureBuildError):
        MODULE.build_cache(args)

    assert not args.output.exists()
    assert not args.phone_output.exists()
    report = json.loads(args.report_output.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["failure"]["stage"] == "utterance"
    assert report["failure"]["utterance_id_sha256"] == MODULE.hash_utterance_id(
        "train-ok"
    )
    assert report["failure"]["cause_type"] == "ValueError"
    assert str(dataset_root) not in report["failure"]["message"]


@pytest.mark.parametrize(
    "relative",
    ["/absolute.wav", "../escape.wav", "nested/../../escape.wav"],
)
def test_resolve_audio_path_rejects_manifest_escape(
    tmp_path: Path,
    relative: str,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ValueError):
        MODULE.resolve_audio_path(root, relative)


def test_stale_prompt_reservation_is_rejected_before_output(
    tmp_path: Path,
) -> None:
    dataset_root, manifest, reservation, _ = _fixture(tmp_path)
    document = json.loads(reservation.read_text(encoding="utf-8"))
    document["source"]["modeling_manifest_sha256"] = "0" * 64
    reservation.write_text(json.dumps(document), encoding="utf-8")
    args = _args(tmp_path, dataset_root, manifest, reservation)

    with pytest.raises(ValueError, match="different modeling manifest"):
        MODULE.build_cache(args)

    assert not args.output.exists()
    assert not args.report_output.exists()


def test_existing_utterance_output_is_never_overwritten(
    tmp_path: Path,
) -> None:
    dataset_root, manifest, reservation, _ = _fixture(tmp_path)
    args = _args(tmp_path, dataset_root, manifest, reservation, phone=False)
    args.output.write_bytes(b"preserve me")

    with pytest.raises(FileExistsError, match="already exists"):
        MODULE.build_cache(args)

    assert args.output.read_bytes() == b"preserve me"
