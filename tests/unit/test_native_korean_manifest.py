from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import soundfile as sf

from voice_coach.data.native_korean import (
    NativeCorpusRoots,
    build_native_korean_manifest,
    iter_native_korean_records,
)


def _audio_bytes(*, sample_rate: int = 16_000) -> bytes:
    stream = io.BytesIO()
    sf.write(
        stream,
        np.zeros(sample_rate // 10, dtype=np.float32),
        sample_rate,
        format="WAV",
        subtype="PCM_16",
    )
    return stream.getvalue()


def _write_audio(
    path: Path,
    *,
    sample_rate: int,
    audio_format: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(
        path,
        np.zeros(sample_rate // 10, dtype=np.float32),
        sample_rate,
        format=audio_format,
    )


def _fixture_roots(tmp_path: Path) -> NativeCorpusRoots:
    fleurs = tmp_path / "raw" / "fleurs"
    for split in ("train", "dev", "test"):
        audio_name = f"{split}.wav"
        _write_audio(
            fleurs / "extracted" / split / split / audio_name,
            sample_rate=16_000,
            audio_format="WAV",
        )
        (fleurs / f"{split}.tsv").write_text(
            f"1\t{audio_name}\t비공개 원문\t비공개 정규문\tchars\t1600\tmale\n",
            encoding="utf-8",
        )

    kss = tmp_path / "raw" / "kss"
    kss.mkdir(parents=True)
    kss_table = pa.Table.from_pylist(
        [
            {
                "audio": {"bytes": _audio_bytes(), "path": None},
                "original_script": "비공개 KSS 전사",
            },
            {
                "audio": {"bytes": _audio_bytes(), "path": None},
                "original_script": "두 번째 비공개 전사",
            },
        ]
    )
    pq.write_table(kss_table, kss / "train-00000-of-00001.parquet")

    seoul = tmp_path / "raw" / "seoul"
    _write_audio(
        seoul / "materialized" / "audio" / "session.flac",
        sample_rate=22_050,
        audio_format="FLAC",
    )
    label = seoul / "materialized" / "labels" / "session.TextGrid"
    label.parent.mkdir(parents=True)
    label.write_text("private annotation placeholder", encoding="utf-8")

    zeroth = tmp_path / "raw" / "zeroth"
    zeroth_audio = (
        zeroth
        / "extracted"
        / "train_data_01"
        / "003"
        / "100"
        / "100_003_0001.flac"
    )
    _write_audio(
        zeroth_audio,
        sample_rate=16_000,
        audio_format="FLAC",
    )
    (zeroth_audio.parent / "100_003.trans.txt").write_text(
        "100_003_0001 비공개 ZEROTH 전사\n",
        encoding="utf-8",
    )
    return NativeCorpusRoots(
        fleurs=fleurs,
        kss=kss,
        seoul=seoul,
        zeroth=zeroth,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_unified_loaders_are_deterministic_private_and_limit_each_source(
    tmp_path: Path,
) -> None:
    roots = _fixture_roots(tmp_path)
    first = list(iter_native_korean_records(roots, limit_per_source=1))
    second = list(iter_native_korean_records(roots, limit_per_source=1))

    assert len(first) == 4
    assert [record.source_id for record in first] == [
        "fleurs-ko-kr",
        "kss",
        "seoul-corpus",
        "zeroth-korean",
    ]
    assert [record.stable_id for record in first] == [
        record.stable_id for record in second
    ]
    assert len({record.stable_id for record in first}) == 4
    assert all(record.transcript_available for record in first)
    assert all(
        record.relative_path is None
        or not Path(record.relative_path).is_absolute()
        for record in first
    )

    kss = first[1]
    assert kss.relative_path is None
    assert kss.embedded_reference == (
        "train-00000-of-00001.parquet#row=0:audio.bytes"
    )
    assert kss.noncommercial_only is True
    assert kss.raw_redistribution_allowed is False
    assert first[2].noncommercial_only is True
    assert first[2].raw_redistribution_allowed is False
    assert first[0].noncommercial_only is False
    assert first[0].raw_redistribution_allowed is True
    assert first[3].split == "train"
    assert all(record.access_scope == "restricted_internal" for record in first)


def test_builder_separates_reviewed_restricted_manifest_and_public_report(
    tmp_path: Path,
) -> None:
    roots = _fixture_roots(tmp_path)
    kss_path = roots.kss / "train-00000-of-00001.parquet"
    seoul_audio = (
        roots.seoul / "materialized" / "audio" / "session.flac"
    )
    source_hashes = {
        kss_path: _sha256(kss_path),
        seoul_audio: _sha256(seoul_audio),
    }
    output = tmp_path / "processed" / "native_smoke.parquet"
    report_path = tmp_path / "processed" / "native_smoke.report.json"

    report = build_native_korean_manifest(
        roots=roots,
        output_path=output,
        report_path=report_path,
        limit_per_source=1,
    )

    table = pq.read_table(output)
    assert table.num_rows == 4
    assert table.schema.metadata is not None
    assert b"privacy_safe" not in table.schema.metadata
    assert table.schema.metadata[b"privacy_reviewed"] == b"true"
    assert table.schema.metadata[b"contains_direct_identifiers"] == b"false"
    assert table.schema.metadata[b"contains_transcript_text"] == b"false"
    assert table.schema.metadata[b"contains_audio_bytes"] == b"false"
    assert table.schema.metadata[b"access_scope"] == b"restricted_internal"
    assert table.schema.metadata[b"shareable"] == b"false"
    assert "transcript_text" not in table.column_names
    assert "speaker_id" not in table.column_names
    assert "audio_bytes" not in table.column_names
    assert report["counts"]["source"] == {
        "fleurs-ko-kr": 1,
        "kss": 1,
        "seoul-corpus": 1,
        "zeroth-korean": 1,
    }
    assert report["counts"]["noncommercial_only"] == {
        "False": 2,
        "True": 2,
    }
    assert report["privacy_checks"] == {
        "privacy_reviewed": True,
        "contains_direct_identifiers": False,
        "contains_transcript_text": False,
        "contains_audio_bytes": False,
        "contains_speaker_id_column": False,
        "report_contains_audio_locator": False,
        "report_contains_transcript_reference": False,
        "report_contains_raw_file_stem": False,
        "absolute_relative_path_count": 0,
        "stable_ids_unique": True,
    }
    assert report["sharing_boundary"]["aggregate_report"] == {
        "classification": "public_aggregate",
        "shareable": True,
        "contains_source_audio_locators": False,
        "contains_transcript_references": False,
        "contains_raw_file_stems": False,
    }
    assert "privacy_safe" not in report
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    for source_path, expected_hash in source_hashes.items():
        assert _sha256(source_path) == expected_hash

    stored = table.to_pylist()
    serialized = json.dumps(stored, ensure_ascii=False)
    assert "비공개 원문" not in serialized
    assert "비공개 KSS 전사" not in serialized
    assert "비공개 ZEROTH 전사" not in serialized
    report_serialized = report_path.read_text(encoding="utf-8")
    assert "session.flac" not in report_serialized
    assert "100_003_0001" not in report_serialized
    assert "train-00000-of-00001.parquet" not in report_serialized


def test_builder_rejects_outputs_inside_raw_corpus_roots(
    tmp_path: Path,
) -> None:
    roots = _fixture_roots(tmp_path)
    unsafe_output = roots.kss / "derived.parquet"
    try:
        build_native_korean_manifest(
            roots=roots,
            output_path=unsafe_output,
            report_path=tmp_path / "report.json",
            limit_per_source=1,
        )
    except ValueError as exc:
        assert "outside raw/native corpus roots" in str(exc)
    else:
        raise AssertionError("raw-root output should have been rejected")
    assert not unsafe_output.exists()
