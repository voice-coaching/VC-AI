from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import soundfile as sf

from voice_coach.training.adversarial_tts_contract import (
    load_adversarial_tts_config,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/train_adversarial_tts.py"
CONFIG = ROOT / "configs/experiments/adversarial_tts_feedback_v1.yaml"


def _run(*arguments: str) -> dict:
    environment = os.environ.copy()
    environment["CUDA_VISIBLE_DEVICES"] = ""
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--workspace-root",
            str(ROOT),
            "--config",
            str(CONFIG),
            *arguments,
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return json.loads(completed.stdout)


def test_default_command_is_torch_free_read_only_preflight() -> None:
    result = _run()

    assert result["status"] == "validated_prototype_config"
    assert result["torch_imported"] is False
    assert result["audio_opened"] is False
    assert result["model_or_gpu_accessed"] is False
    assert result["production_ready"] is False


def test_synthetic_smoke_runs_four_gradient_roles_without_corpus_audio() -> None:
    result = _run("--synthetic-smoke")
    smoke = result["smoke"]

    assert smoke["status"] == "synthetic_smoke_completed"
    assert smoke["device"] == "cpu"
    assert smoke["corpus_audio_opened"] is False
    assert smoke["checkpoint_published"] is False
    assert smoke["research_result"] is False
    assert smoke["refined_waveform_shape"] == [2, 256]
    assert all(value >= 0 for value in smoke["metrics"].values())


def _wav_bytes() -> bytes:
    stream = io.BytesIO()
    samples = np.linspace(-0.05, 0.05, 800, dtype=np.float32)
    sf.write(stream, samples, 16_000, format="WAV", subtype="PCM_16")
    return stream.getvalue()


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def test_real_data_smoke_opens_fixture_audio_without_cuda_or_checkpoint(
    tmp_path: Path,
) -> None:
    learner_root = tmp_path / "data/restricted/aihub"
    learner_root.mkdir(parents=True)
    learner_audio = learner_root / "learner.wav"
    learner_audio.write_bytes(_wav_bytes())
    learner_manifest = tmp_path / "data/processed/learner.parquet"
    learner_manifest.parent.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "id": "learner-fixture",
                    "split": "train",
                    "audio_path": "learner.wav",
                    "duration_seconds": 0.05,
                    "l1": "fixture",
                    "gender": "fixture",
                    "proficiency": "fixture",
                    "error_presence": True,
                    "target_phone_intervals": [
                        {"start": 0.0, "end": 0.05, "label": "ㄱ"}
                    ],
                    "phone_alignments": [{"operation": "match"}],
                    "qc_flags": [],
                    "prompt_sha256": "1" * 64,
                    "pronunciation_score": 4,
                    "fluency_score": None,
                    "comprehensibility_score": 3,
                }
            ]
        ),
        learner_manifest,
    )
    reservation = tmp_path / "data/processed/reservation.json"
    reservation.write_text(
        json.dumps({"reserved_prompts": [{"prompt_sha256": "2" * 64}]}),
        encoding="utf-8",
    )

    native_root = tmp_path / "data/raw/research-noncommercial/kss"
    native_root.mkdir(parents=True)
    native_shard = native_root / "train-00000-of-00001.parquet"
    pq.write_table(
        pa.Table.from_pylist([{"audio": {"bytes": _wav_bytes(), "path": None}}]),
        native_shard,
    )
    native_manifest = tmp_path / "data/processed/native.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "stable_id": "kss:fixture",
                    "source_id": "kss",
                    "audio_storage": "parquet_embedded",
                    "relative_path": None,
                    "embedded_reference": (
                        "train-00000-of-00001.parquet#row=0:audio.bytes"
                    ),
                    "sample_rate_hz": 16_000,
                    "channels": 1,
                    "duration_seconds": 0.05,
                    "split": "train",
                    "license_id": "CC-BY-NC-SA-4.0",
                    "noncommercial_only": True,
                    "intended_role": "research_only_native_korean_tts_baseline",
                    "access_scope": "restricted_internal",
                }
            ]
        ),
        native_manifest,
    )

    config = deepcopy(load_adversarial_tts_config(CONFIG))
    config["data"]["learner"]["manifest"] = str(learner_manifest.relative_to(tmp_path))
    config["data"]["learner"]["sha256"] = _sha256(learner_manifest)
    config["data"]["native"]["manifest"] = str(native_manifest.relative_to(tmp_path))
    config["data"]["native"]["sha256"] = _sha256(native_manifest)
    config["model"]["vocabulary_size"] = 64
    config["model"]["feedback_dim"] = 4
    config["model"]["generator"].update(
        {
            "hidden_dim": 8,
            "acoustic_dim": 8,
            "residual_blocks": 1,
            "dropout": 0.0,
        }
    )
    config["model"]["evaluator1"].update({"hidden_dim": 8, "embedding_dim": 8})
    config["model"]["evaluator2_meta_critic"].update(
        {"hidden_dim": 8, "embedding_dim": 8}
    )
    config["training"]["learning_rates"] = {
        name: 0.001 for name in config["training"]["learning_rates"]
    }
    config_path = tmp_path / "configs/real-smoke.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(config), encoding="utf-8")
    report_path = Path("artifacts/real-smoke.json")

    environment = os.environ.copy()
    environment["CUDA_VISIBLE_DEVICES"] = ""
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(ROOT / "src")
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--workspace-root",
            str(tmp_path),
            "--config",
            str(config_path),
            "--real-data-smoke",
            "--learner-dataset-root",
            str(learner_root),
            "--prompt-reservation",
            str(reservation),
            "--native-corpus-root",
            str(native_root),
            "--real-record-limit",
            "1",
            "--real-batch-size",
            "1",
            "--real-max-duration-seconds",
            "1",
            "--report",
            str(report_path),
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )

    result = json.loads(completed.stdout)
    smoke = result["smoke"]
    assert smoke["status"] == "real_data_wiring_smoke_completed"
    assert smoke["device"] == "cpu"
    assert smoke["cuda_visible_to_process"] is False
    assert smoke["corpus_audio_opened"] is True
    assert smoke["checkpoint_published"] is False
    assert smoke["research_result"] is False
    assert smoke["native_content_supervised_count"] == 0
    assert result["report"] == report_path.as_posix()
    persisted = json.loads((tmp_path / report_path).read_text(encoding="utf-8"))
    assert persisted["smoke"]["optimizer_steps"] == 1
