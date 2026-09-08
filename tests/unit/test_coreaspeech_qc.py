from __future__ import annotations

import hashlib
import io
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import soundfile as sf
import yaml

from voice_coach.data.coreaspeech_qc import (
    CoreaSpeechAuditError,
    audit_coreaspeech_shard,
)


REVISION = "a" * 40


def _schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field(
                "audio",
                pa.struct(
                    [
                        pa.field("bytes", pa.binary()),
                        pa.field("path", pa.string()),
                    ]
                ),
            ),
            pa.field("duration", pa.float64()),
            pa.field("text", pa.string()),
            pa.field("tr", pa.string()),
            pa.field("N2gkPlus", pa.string()),
            pa.field("speaker", pa.string()),
        ]
    )


def _wav_bytes(duration_seconds: float = 0.1) -> bytes:
    stream = io.BytesIO()
    sf.write(
        stream,
        np.zeros(round(48_000 * duration_seconds), dtype=np.float32),
        48_000,
        format="WAV",
        subtype="PCM_16",
    )
    return stream.getvalue()


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / REVISION
    root.mkdir()
    shard = root / "train-00001-of-00047.parquet"
    rows = [
        {
            "audio": {
                "bytes": _wav_bytes(),
                "path": f"private-audio-{index}.wav",
            },
            "duration": 0.1,
            "text": "private text",
            "tr": "private transcription",
            "N2gkPlus": "private pronunciation",
            "speaker": f"private-speaker-{index}",
        }
        for index in range(2)
    ]
    pq.write_table(pa.Table.from_pylist(rows, schema=_schema()), shard)
    digest = hashlib.sha256(shard.read_bytes()).hexdigest()
    manifest = {
        "schema_version": "voice_coach.external_dataset_manifest.v1",
        "dataset": {
            "id": "coreaspeech",
            "revision": REVISION,
            "access": {
                "private": False,
                "gated": False,
                "authentication_required": False,
            },
        },
        "research_policy": {
            "research_only": True,
            "commercial_use_allowed": False,
            "redistribution_allowed": False,
            "secondary_modification_allowed": False,
        },
        "smoke_snapshot": {
            "rows": 2,
            "speakers": 2,
            "duration_hours": 0.2 / 3600,
            "shard": {
                "remote_path": "data/train-00001-of-00047.parquet",
                "bytes": shard.stat().st_size,
                "sha256": digest,
            },
            "expected_audio_header": {
                "sample_rate_hz": 48_000,
                "channels": 1,
                "formats": ["WAV"],
            },
        },
        "training_authorization": {"current_status": "qc_only"},
    }
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    return manifest_path, root, shard


def test_full_qc_is_aggregate_only_and_passes(tmp_path: Path) -> None:
    manifest, root, _ = _fixture(tmp_path)

    report = audit_coreaspeech_shard(
        manifest_path=manifest,
        dataset_root=root,
    )

    assert report["status"] == "full_header_qc_passed"
    assert report["scope"]["rows_scanned"] == 2
    assert report["scope"]["audio_materialized"] is False
    assert report["aggregate"]["speaker_count"] == 2
    assert report["aggregate"]["sample_rate_hz_counts"] == {"48000": 2}
    assert report["aggregate"]["embedded_audio_path_value_ignored_count"] == 2
    serialized = str(report)
    assert "private text" not in serialized
    assert "private-speaker" not in serialized
    assert "private-audio" not in serialized
    assert str(root) not in serialized


def test_limited_qc_is_not_promoted_to_full(tmp_path: Path) -> None:
    manifest, root, _ = _fixture(tmp_path)

    report = audit_coreaspeech_shard(
        manifest_path=manifest,
        dataset_root=root,
        row_limit=1,
    )

    assert report["status"] == "sampled_header_qc_completed"
    assert report["failed_checks"] == ["not_full_shard"]
    assert report["scope"]["full_shard"] is False


def test_tampered_shard_fails_before_header_access(tmp_path: Path) -> None:
    manifest, root, shard = _fixture(tmp_path)
    shard.write_bytes(shard.read_bytes() + b"x")

    with pytest.raises(CoreaSpeechAuditError, match="byte size"):
        audit_coreaspeech_shard(
            manifest_path=manifest,
            dataset_root=root,
        )


def test_policy_relaxation_is_rejected(tmp_path: Path) -> None:
    manifest, root, _ = _fixture(tmp_path)
    document = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    document["research_policy"]["secondary_modification_allowed"] = True
    manifest.write_text(
        yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(CoreaSpeechAuditError, match="QC-only"):
        audit_coreaspeech_shard(
            manifest_path=manifest,
            dataset_root=root,
        )
