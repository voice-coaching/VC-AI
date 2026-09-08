from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from voice_coach.experiments import (
    ExperimentRun,
    experiment_run,
    fingerprint_file,
    sanitize_argv,
)
from voice_coach.experiments import registry as registry_module


@pytest.fixture
def deterministic_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        registry_module,
        "collect_runtime_metadata",
        lambda package_names: {
            "python": {"version": "3.12.0"},
            "platform": {"system": "TestOS"},
            "packages": {name: "1.0" for name in package_names},
        },
    )
    monkeypatch.setattr(
        registry_module,
        "collect_accelerator_metadata",
        lambda: {
            "cuda_available": True,
            "cuda_runtime_version": "13.0",
            "torch_device_count": 1,
            "torch_devices": [
                {
                    "index": 0,
                    "name": "Test GPU",
                    "compute_capability": "12.1",
                    "memory_total_bytes": 128,
                }
            ],
            "nvidia_smi": {"available": True, "gpus": []},
        },
    )
    monkeypatch.setattr(
        registry_module,
        "collect_git_metadata",
        lambda repo_path: {
            "available": True,
            "commit": "a" * 40,
            "dirty": True,
        },
    )


def _write_inputs(root: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for role in ("config", "data", "model", "checkpoint"):
        path = root / f"{role}.bin"
        path.write_bytes(f"{role}-contents".encode())
        paths[role] = path
    return paths


def test_completed_run_records_hashes_metrics_artifacts_and_provenance(
    tmp_path: Path,
    deterministic_probes: None,
) -> None:
    del deterministic_probes
    inputs = _write_inputs(tmp_path)
    artifact = tmp_path / "predictions.json"
    artifact.write_text('{"count": 3}', encoding="utf-8")
    api_key = "11111111-2222-3333-4444-555555555555"

    with experiment_run(
        tmp_path / "runs",
        run_id="baseline-seed42",
        config_files=inputs["config"],
        data_files=[inputs["data"]],
        model_files=(inputs["model"],),
        checkpoint_files=inputs["checkpoint"],
        seeds={"numpy": 42, "python": 42, "torch": 42},
        argv=[
            "train.py",
            "--api-key",
            "never-write-this",
            "--approval-id",
            api_key,
            "--seed=42",
        ],
        repo_path=tmp_path,
        package_names=("numpy", "torch"),
    ) as run:
        running = json.loads(run.metadata_path.read_text(encoding="utf-8"))
        assert running["status"] == "running"
        assert running["timestamps"]["ended"] is None
        run.log_metrics(
            {
                "macro_f1": 0.625,
                "per_l1": {"zh": 0.60, "en": 0.65},
            },
            step=3,
            split="dev",
        )
        run.add_artifact("predictions", artifact, kind="evaluation")

    payload_text = run.metadata_path.read_text(encoding="utf-8")
    payload = json.loads(payload_text)
    assert payload["status"] == "completed"
    assert payload["timestamps"]["started"]["utc"].endswith("+00:00")
    assert payload["timestamps"]["started"]["kst"].endswith("+09:00")
    assert payload["timestamps"]["ended"] is not None
    assert payload["seeds"] == {"numpy": 42, "python": 42, "torch": 42}
    assert payload["git"] == {
        "available": True,
        "commit": "a" * 40,
        "dirty": True,
    }
    assert payload["runtime"]["packages"] == {"numpy": "1.0", "torch": "1.0"}
    assert payload["accelerator"]["cuda_runtime_version"] == "13.0"

    for role, path in inputs.items():
        entry = payload["inputs"][role][0]
        assert entry["path"] == path.name
        assert entry["size_bytes"] == path.stat().st_size
        assert entry["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()

    metric_event = payload["metrics"][0]
    assert metric_event["step"] == 3
    assert metric_event["split"] == "dev"
    assert metric_event["values"]["macro_f1"] == 0.625
    artifact_entry = payload["artifacts"][0]
    assert artifact_entry["path"] == artifact.name
    assert artifact_entry["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()

    assert "never-write-this" not in payload_text
    assert api_key not in payload_text
    assert payload["argv"][2] == "<redacted>"
    assert payload["argv"][4] == "<redacted>"
    assert not list(run.run_directory.glob("*.tmp"))
    assert not list(run.run_directory.glob(".*.tmp"))


def test_failed_run_records_only_exception_type(
    tmp_path: Path,
    deterministic_probes: None,
) -> None:
    del deterministic_probes
    secret_in_exception = "password=do-not-persist"
    run = experiment_run(
        tmp_path / "runs",
        run_id="failed-run",
        argv=["train.py"],
        repo_path=tmp_path,
        package_names=(),
    )

    with pytest.raises(RuntimeError, match="do-not-persist"):
        with run:
            raise RuntimeError(secret_in_exception)

    payload_text = run.metadata_path.read_text(encoding="utf-8")
    payload = json.loads(payload_text)
    assert payload["status"] == "failed"
    assert payload["failure"] == {"exception_type": "builtins.RuntimeError"}
    assert payload["timestamps"]["ended"] is not None
    assert secret_in_exception not in payload_text


def test_failed_run_records_public_stage_and_oom_without_exception_text(
    tmp_path: Path,
    deterministic_probes: None,
) -> None:
    del deterministic_probes
    run = experiment_run(
        tmp_path / "runs",
        run_id="failed-oom-run",
        argv=["train.py"],
        repo_path=tmp_path,
        package_names=(),
    )

    with pytest.raises(RuntimeError, match="private-path"):
        with run:
            run.set_failure_context(stage="seed_training", oom=True)
            raise RuntimeError("/restricted/private-path.wav")

    payload_text = run.metadata_path.read_text(encoding="utf-8")
    payload = json.loads(payload_text)
    assert payload["failure"] == {
        "exception_type": "builtins.RuntimeError",
        "stage": "seed_training",
        "oom": True,
    }
    assert "private-path" not in payload_text


def test_failure_context_rejects_sensitive_stage(
    tmp_path: Path,
    deterministic_probes: None,
) -> None:
    del deterministic_probes
    with experiment_run(
        tmp_path / "runs",
        run_id="failure-stage-guard",
        argv=["train.py"],
        repo_path=tmp_path,
        package_names=(),
    ) as run:
        with pytest.raises(ValueError, match="sensitive"):
            run.set_failure_context(stage="api_token_loading", oom=False)
        with pytest.raises(ValueError, match="public identifier"):
            run.set_failure_context(
                stage="/restricted/raw-speaker.wav",
                oom=False,
            )


def test_env_files_are_rejected_even_through_symlink(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.local"
    env_path.write_text("API_KEY=must-not-be-read", encoding="utf-8")
    link = tmp_path / "innocent-config"
    link.symlink_to(env_path)

    with pytest.raises(ValueError, match=r"\.env"):
        fingerprint_file(env_path)
    with pytest.raises(ValueError, match=r"\.env"):
        fingerprint_file(link)


def test_sensitive_metrics_and_artifacts_are_rejected(
    tmp_path: Path,
    deterministic_probes: None,
) -> None:
    del deterministic_probes
    env_path = tmp_path / ".env.local"
    env_path.write_text("TOKEN=must-not-be-read", encoding="utf-8")

    with experiment_run(
        tmp_path / "runs",
        run_id="secret-guard",
        argv=["train.py"],
        repo_path=tmp_path,
        package_names=(),
    ) as run:
        with pytest.raises(ValueError, match="sensitive"):
            run.log_metrics({"api_key": 1})
        with pytest.raises(TypeError, match="metrics"):
            run.log_metrics({"notes": "not numeric"})
        with pytest.raises(ValueError, match=r"\.env"):
            run.add_artifact("environment", env_path)

    serialized = run.metadata_path.read_text(encoding="utf-8")
    assert "must-not-be-read" not in serialized


def test_sanitize_argv_redacts_assignments_env_paths_and_opaque_values() -> None:
    opaque = "A" * 40
    assert sanitize_argv(
        [
            "train.py",
            "--token=abc",
            "PASSWORD=def",
            "--env-file",
            ".env.local",
            "--dotenv=.env.test",
            opaque,
            "--data",
            "manifest.parquet",
        ]
    ) == [
        "train.py",
        "--token=<redacted>",
        "PASSWORD=<redacted>",
        "--env-file",
        "<redacted-env-file>",
        "--dotenv=<redacted-env-file>",
        "<redacted>",
        "--data",
        "manifest.parquet",
    ]


def test_duplicate_run_id_and_unknown_file_role_are_rejected(
    tmp_path: Path,
    deterministic_probes: None,
) -> None:
    del deterministic_probes
    first = ExperimentRun(
        tmp_path / "runs",
        run_id="same-id",
        argv=["train.py"],
        repo_path=tmp_path,
        package_names=(),
    )
    with first:
        pass

    second = ExperimentRun(
        tmp_path / "runs",
        run_id="same-id",
        argv=["train.py"],
        repo_path=tmp_path,
        package_names=(),
    )
    with pytest.raises(FileExistsError):
        second.start()

    with pytest.raises(ValueError, match="unknown tracked-file roles"):
        ExperimentRun(
            tmp_path / "runs",
            input_files={"environment": []},
            argv=["train.py"],
        )


def test_multiple_input_files_are_fingerprinted_once_each(
    tmp_path: Path,
    deterministic_probes: None,
) -> None:
    del deterministic_probes
    first = tmp_path / "part-00000.parquet"
    second = tmp_path / "part-00001.parquet"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    with experiment_run(
        tmp_path / "runs",
        run_id="multi-shard",
        data_files=[first, second],
        argv=["train.py"],
        repo_path=tmp_path,
        package_names=(),
    ) as run:
        pass

    metadata = json.loads(run.metadata_path.read_text(encoding="utf-8"))
    assert [item["path"] for item in metadata["inputs"]["data"]] == [
        first.name,
        second.name,
    ]


def test_opt_in_post_completion_publication_failure_is_safe(
    tmp_path: Path,
    deterministic_probes: None,
) -> None:
    del deterministic_probes
    run = ExperimentRun(
        tmp_path / "runs",
        run_id="post-completion-failure",
        argv=["train.py"],
        repo_path=tmp_path,
        package_names=(),
    )
    with run:
        pass

    run.mark_post_completion_failure(
        stage="commit_publication",
        exception_type=OSError,
        oom=False,
    )

    payload = json.loads(run.metadata_path.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["failure"] == {
        "exception_type": "builtins.OSError",
        "stage": "commit_publication",
        "oom": False,
    }


@pytest.mark.parametrize("post_rename", [False, True])
def test_registry_initialization_fault_removes_retry_wedge(
    tmp_path: Path,
    deterministic_probes: None,
    monkeypatch: pytest.MonkeyPatch,
    post_rename: bool,
) -> None:
    del deterministic_probes
    original = registry_module._atomic_write_json

    def fail_initialization(
        path: Path,
        payload: dict[str, object],
    ) -> None:
        if post_rename:
            original(path, payload)
        raise OSError("synthetic initialization durability failure")

    monkeypatch.setattr(
        registry_module,
        "_atomic_write_json",
        fail_initialization,
    )
    first = ExperimentRun(
        tmp_path / "runs",
        run_id="retryable-init",
        argv=["train.py"],
        repo_path=tmp_path,
        package_names=(),
    )
    with pytest.raises(OSError, match="durability"):
        first.start()
    assert not first.run_directory.exists()

    monkeypatch.setattr(registry_module, "_atomic_write_json", original)
    repeated = ExperimentRun(
        tmp_path / "runs",
        run_id="retryable-init",
        argv=["train.py"],
        repo_path=tmp_path,
        package_names=(),
    )
    with repeated:
        pass
    assert repeated.metadata["status"] == "completed"
