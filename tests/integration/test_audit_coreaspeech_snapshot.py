from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import soundfile as sf
import yaml


ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv-scorer" / "bin" / "python"
SCRIPT = ROOT / "scripts" / "audit_coreaspeech_snapshot.py"
REVISION = "b" * 40


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


def _build_fixture(tmp_path: Path) -> tuple[Path, Path]:
    dataset_root = tmp_path / REVISION
    dataset_root.mkdir()
    stream = io.BytesIO()
    sf.write(
        stream,
        np.zeros(4_800, dtype=np.float32),
        48_000,
        format="WAV",
        subtype="PCM_16",
    )
    shard = dataset_root / "train-00001-of-00047.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "audio": {"bytes": stream.getvalue(), "path": None},
                    "duration": 0.1,
                    "text": "must not leave process",
                    "tr": "must not leave process",
                    "N2gkPlus": "must not leave process",
                    "speaker": "must-not-leave-process",
                }
            ],
            schema=_schema(),
        ),
        shard,
    )
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
            "rows": 1,
            "speakers": 1,
            "duration_hours": 0.1 / 3600,
            "shard": {
                "remote_path": "data/train-00001-of-00047.parquet",
                "bytes": shard.stat().st_size,
                "sha256": hashlib.sha256(shard.read_bytes()).hexdigest(),
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
    return manifest_path, dataset_root


def test_cli_writes_privacy_safe_aggregate_report() -> None:
    fixture_parent = ROOT / "artifacts" / "test-workspaces"
    fixture_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=fixture_parent) as directory:
        fixture = Path(directory)
        manifest, dataset_root = _build_fixture(fixture)
        output = fixture / "report.json"
        completed = subprocess.run(
            [
                str(PYTHON),
                str(SCRIPT),
                "--manifest",
                str(manifest),
                "--dataset-root",
                str(dataset_root),
                "--output",
                str(output),
            ],
            cwd=ROOT,
            env={**os.environ, "CUDA_VISIBLE_DEVICES": ""},
            text=True,
            capture_output=True,
            check=False,
        )

        assert completed.returncode == 0, completed.stdout + completed.stderr
        report = json.loads(output.read_text(encoding="utf-8"))
        serialized = json.dumps(report)
        assert report["status"] == "full_header_qc_passed"
        assert "must not leave process" not in serialized
        assert str(dataset_root) not in serialized


def test_cli_redacts_paths_from_unexpected_io_errors() -> None:
    fixture_parent = ROOT / "artifacts" / "test-workspaces"
    fixture_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=fixture_parent) as directory:
        fixture = Path(directory)
        manifest, dataset_root = _build_fixture(fixture)
        output_directory = fixture / "must-not-appear-in-error"
        output_directory.mkdir()
        completed = subprocess.run(
            [
                str(PYTHON),
                str(SCRIPT),
                "--manifest",
                str(manifest),
                "--dataset-root",
                str(dataset_root),
                "--output",
                str(output_directory),
            ],
            cwd=ROOT,
            env={**os.environ, "CUDA_VISIBLE_DEVICES": ""},
            text=True,
            capture_output=True,
            check=False,
        )

        assert completed.returncode == 1
        response = json.loads(completed.stdout)
        assert response == {
            "status": "failed_closed",
            "error": "unexpected_local_io_or_value_error",
        }
        assert str(fixture) not in completed.stdout
        assert str(fixture) not in completed.stderr
