from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
from types import ModuleType

import pytest


ROOT = Path(__file__).parents[2]
APPLICATION_PATH = ROOT / "src/voice_coach/experiments/e01_duration_worker_receipt.py"
WORKER_PATH = ROOT / "scripts/run_e01_duration_synthetic_worker.py"
RUNNER_PATH = ROOT / "scripts/run_e01_duration_synthetic_receipt.py"
CONFIG_PATH = ROOT / "configs/experiments/e01_duration_synthetic_worker_v1.json"
ACOUSTIC_PYTHON = ROOT / ".venv-paper-acoustics/bin/python"
SCORER_PYTHON = ROOT / ".venv-scorer/bin/python"
REVIEWED_WHEEL = (
    ROOT.parents[1]
    / ".cache/pip/wheels/ec/06/e8"
    / "9b60c72eb3dcc075dc5b5706fb5c1d3be64f9367253e18a15c"
    / "praat_parselmouth-0.4.7-cp312-cp312-linux_aarch64.whl"
)


def _load_module(name: str, path: Path) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


APPLICATION = _load_module(
    "_test_e01_duration_worker_receipt",
    APPLICATION_PATH,
)
WORKER = _load_module(
    "_test_run_e01_duration_synthetic_worker",
    WORKER_PATH,
)


def _config_document() -> dict[str, object]:
    value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert type(value) is dict
    return value


def _runner_command(*, reviewed_wheel: Path = REVIEWED_WHEEL) -> list[str]:
    return [
        str(SCORER_PYTHON),
        str(RUNNER_PATH),
        "--workspace-root",
        str(ROOT),
        "--reviewed-wheel",
        str(reviewed_wheel),
    ]


def test_config_is_canonical_and_all_source_pins_match() -> None:
    config_bytes = CONFIG_PATH.read_bytes()

    config = APPLICATION.validate_config(config_bytes)
    observed = APPLICATION.verify_source_pins(ROOT, config)

    assert APPLICATION.canonical_json_bytes(config) == config_bytes
    assert observed == config["source_sha256"]
    assert config["completion_limits"] == {
        "actual_korean_speech_completed": False,
        "matched_control_completed": False,
        "paper_ready": False,
        "production_ready": False,
        "s1_completed": False,
        "s2_completed": False,
        "scientific_claim": "synthetic_technical_gate_only",
    }


@pytest.mark.parametrize(
    ("section", "field", "mutated_value"),
    [
        ("execution_policy", "job_count_per_process", 2),
        ("execution_policy", "expected_python_network_intercept_count", 1),
        ("execution_policy", "expected_kernel_seccomp_mode", 0),
        ("synthetic_input", "path_or_identifier_input", True),
        ("transform", "duration_scale_factor", 1.0),
        ("completion_limits", "paper_ready", True),
        ("completion_limits", "matched_control_completed", True),
    ],
)
def test_config_rejects_scope_and_safety_mutations(
    section: str,
    field: str,
    mutated_value: object,
) -> None:
    config = _config_document()
    section_value = config[section]
    assert type(section_value) is dict
    section_value[field] = mutated_value

    with pytest.raises(APPLICATION.E01DurationWorkerReceiptError):
        APPLICATION.validate_config(APPLICATION.canonical_json_bytes(config))


def test_config_rejects_extra_field_and_noncanonical_encoding() -> None:
    config = _config_document()
    config["unexpected"] = False
    with pytest.raises(APPLICATION.E01DurationWorkerReceiptError):
        APPLICATION.validate_config(APPLICATION.canonical_json_bytes(config))

    pretty = json.dumps(
        _config_document(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    with pytest.raises(APPLICATION.E01DurationWorkerReceiptError):
        APPLICATION.validate_config(pretty)


def test_immutable_store_publishes_once_and_reuses_exact_bytes(
    tmp_path: Path,
) -> None:
    receipt = APPLICATION.canonical_json_bytes(
        {"schema_version": "test.v1", "status": "completed"}
    )
    store = APPLICATION._ImmutableReceiptStore(tmp_path)

    digest, relative_path, created = store.publish(receipt)
    repeated_digest, repeated_path, repeated_created = store.publish(receipt)

    path = tmp_path / relative_path
    assert created is True
    assert repeated_created is False
    assert repeated_digest == digest
    assert repeated_path == relative_path
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.stat().st_nlink == 1
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_verified_bytes_memfd_is_write_sealed() -> None:
    content = b"verified-worker-bytes"
    descriptor = APPLICATION._sealed_memfd_from_bytes(
        "test-e01-sealed-source",
        content,
        executable=False,
    )
    try:
        required = (
            fcntl.F_SEAL_SEAL
            | fcntl.F_SEAL_SHRINK
            | fcntl.F_SEAL_GROW
            | fcntl.F_SEAL_WRITE
        )
        assert fcntl.fcntl(descriptor, fcntl.F_GET_SEALS) == required
        assert os.pread(descriptor, len(content), 0) == content
        with pytest.raises(OSError):
            os.pwrite(descriptor, b"X", 0)
    finally:
        os.close(descriptor)


def test_immutable_store_rejects_hardlinked_existing_receipt(
    tmp_path: Path,
) -> None:
    receipt = APPLICATION.canonical_json_bytes({"status": "completed"})
    store = APPLICATION._ImmutableReceiptStore(tmp_path)
    _, relative_path, _ = store.publish(receipt)
    receipt_path = tmp_path / relative_path
    os.link(receipt_path, tmp_path / "second-link.json")

    with pytest.raises(APPLICATION.E01DurationWorkerReceiptError):
        store.publish(receipt)


def test_immutable_store_rejects_symlinked_generation_parent(
    tmp_path: Path,
) -> None:
    (tmp_path / "artifacts/phase1").mkdir(parents=True, mode=0o700)
    external = tmp_path / "external"
    external.mkdir(mode=0o700)
    output_root = tmp_path / "artifacts/phase1/e01_duration_synthetic_worker_receipts"
    output_root.symlink_to(external, target_is_directory=True)
    store = APPLICATION._ImmutableReceiptStore(tmp_path)

    with pytest.raises(
        APPLICATION.E01DurationWorkerReceiptError,
        match="directory_identity",
    ):
        store.publish(APPLICATION.canonical_json_bytes({"status": "completed"}))


def test_immutable_store_rejects_workspace_root_rename_replacement(
    tmp_path: Path,
) -> None:
    store = APPLICATION._ImmutableReceiptStore(tmp_path)
    original = tmp_path.with_name(f"{tmp_path.name}-original")
    tmp_path.rename(original)
    tmp_path.mkdir(mode=0o700)
    try:
        with pytest.raises(
            APPLICATION.E01DurationWorkerReceiptError,
            match="workspace_root_changed",
        ):
            store.publish(APPLICATION.canonical_json_bytes({"status": "completed"}))
        assert not (tmp_path / "artifacts").exists()
    finally:
        tmp_path.rmdir()
        original.rename(tmp_path)


def test_run_receipt_captures_root_identity_before_worker_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completion_limits = {
        "actual_korean_speech_completed": False,
        "matched_control_completed": False,
        "paper_ready": False,
        "production_ready": False,
        "s1_completed": False,
        "s2_completed": False,
        "scientific_claim": "synthetic_technical_gate_only",
    }
    config = {"completion_limits": completion_limits}
    original = tmp_path.with_name(f"{tmp_path.name}-original")

    monkeypatch.setattr(APPLICATION, "validate_config", lambda _: config)
    monkeypatch.setattr(APPLICATION, "verify_source_pins", lambda *_: {})
    monkeypatch.setattr(
        APPLICATION,
        "build_receipt",
        lambda *_, **__: {"status": "completed"},
    )

    def replace_root_during_worker(*_: object) -> dict[str, object]:
        tmp_path.rename(original)
        tmp_path.mkdir(mode=0o700)
        return {"completion_limits": completion_limits}

    monkeypatch.setattr(APPLICATION, "_run_worker", replace_root_during_worker)

    try:
        with pytest.raises(
            APPLICATION.E01DurationWorkerReceiptError,
            match="workspace_root_changed",
        ):
            APPLICATION.run_receipt(
                workspace_root=tmp_path,
                config_bytes=b"{}",
                reviewed_wheel_path=tmp_path / "reviewed.whl",
            )
        assert not (tmp_path / "artifacts").exists()
    finally:
        tmp_path.rmdir()
        original.rename(tmp_path)


def test_network_guard_preserves_socket_type_and_blocks_ip_connect() -> None:
    original_socket = socket.socket

    with WORKER._blocked_network() as attempts:
        assert isinstance(socket.socket, type)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
            with pytest.raises(OSError, match="network disabled"):
                handle.connect(("127.0.0.1", 9))
        with pytest.raises(OSError, match="network disabled"):
            socket.gethostbyname("example.invalid")

    assert attempts == [2]
    assert socket.socket is original_socket


def test_kernel_guard_denies_native_socket_before_child_exec() -> None:
    config = APPLICATION.validate_config(CONFIG_PATH.read_bytes())
    guard = APPLICATION._PreparedSeccompGuard.prepare(config["runtime_pins"])
    code = (
        "import _socket,errno,sys;"
        "\ntry:_socket.socket(_socket.AF_INET,_socket.SOCK_STREAM)"
        "\nexcept OSError as error:sys.exit(0 if error.errno==errno.EPERM else 3)"
        "\nelse:sys.exit(4)"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            check=False,
            capture_output=True,
            preexec_fn=guard.install_in_child,
            timeout=30,
        )
    finally:
        guard.close()

    assert result.returncode == 0
    assert result.stdout == b""
    assert result.stderr == b""


@pytest.mark.skipif(
    not (
        ACOUSTIC_PYTHON.exists() and SCORER_PYTHON.exists() and REVIEWED_WHEEL.exists()
    ),
    reason="pinned E01 acoustic execution inputs are unavailable",
)
def test_actual_runner_is_idempotent_and_receipt_is_privacy_minimal() -> None:
    first = subprocess.run(
        _runner_command(),
        check=False,
        capture_output=True,
        timeout=180,
    )
    second = subprocess.run(
        _runner_command(),
        check=False,
        capture_output=True,
        timeout=180,
    )

    assert first.returncode == 0
    assert second.returncode == 0
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert first_payload["receipt_sha256"] == second_payload["receipt_sha256"]
    assert second_payload["idempotent_reuse"] is True
    receipt_path = ROOT / first_payload["receipt_relative_path"]
    receipt_bytes = receipt_path.read_bytes()
    receipt = json.loads(receipt_bytes)
    assert hashlib.sha256(receipt_bytes).hexdigest() == first_payload["receipt_sha256"]
    assert receipt["execution"]["worker_process_count"] == 1
    assert receipt["execution"]["job_count"] == 1
    assert receipt["execution"]["python_network_intercept_count"] == 0
    assert receipt["execution"]["kernel_network_guard_probe_block_count"] == 1
    assert receipt["execution"]["kernel_seccomp_mode"] == 2
    assert receipt["execution"]["verified_executable_materialization_policy"] == (
        "sealed_memfd_from_verified_bytes_v1"
    )
    kernel_guard = receipt["runtime_build_provenance"]["kernel_network_guard"]
    assert kernel_guard["installation_boundary"] == (
        "subprocess_preexec_before_python_exec"
    )
    assert kernel_guard["libseccomp_sha256"] == (
        "3d128123b494ca9e88f3c237b0912397145af1754e86ea99f460af6857441f19"
    )
    assert {"socket", "connect", "io_uring_setup"} <= set(
        kernel_guard["blocked_syscalls"]
    )
    assert receipt["source_load_provenance"]["import_policy"] == (
        "verified_bytes_importlib_loader_no_package_init"
    )
    assert receipt["publication"] == {
        "absolute_path_included": False,
        "aggregate_and_digests_only": True,
        "output_waveform_included": False,
        "raw_pcm_included": False,
        "speaker_or_utterance_identifier_included": False,
    }
    assert "/home/" not in receipt_bytes.decode("utf-8")
    assert ".wav" not in receipt_bytes.decode("utf-8")
    assert ".npy" not in receipt_bytes.decode("utf-8")
    assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o600
    assert receipt_path.stat().st_nlink == 1


@pytest.mark.skipif(
    not (ACOUSTIC_PYTHON.exists() and SCORER_PYTHON.exists()),
    reason="pinned E01 execution environments are unavailable",
)
def test_runner_failure_is_sanitized_and_does_not_publish_private_path(
    tmp_path: Path,
) -> None:
    private_name = "private-speaker-utterance-wheel.whl"
    unavailable = tmp_path / private_name

    result = subprocess.run(
        _runner_command(reviewed_wheel=unavailable),
        check=False,
        capture_output=True,
        timeout=180,
    )

    assert result.returncode == 2
    assert result.stderr == b""
    assert json.loads(result.stdout) == {
        "failure_code": "synthetic_receipt_runner_failure",
        "status": "error",
    }
    assert private_name.encode("utf-8") not in result.stdout
    assert str(tmp_path).encode("utf-8") not in result.stdout


def test_runner_argument_failure_does_not_echo_private_argument() -> None:
    private_argument = "--private-speaker-token=do-not-publish"

    result = subprocess.run(
        [*_runner_command(), private_argument],
        check=False,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 2
    assert result.stderr == b""
    assert json.loads(result.stdout) == {
        "failure_code": "synthetic_receipt_runner_failure",
        "status": "error",
    }
    assert private_argument.encode("utf-8") not in result.stdout


@pytest.mark.skipif(
    not ACOUSTIC_PYTHON.exists(),
    reason="pinned E01 acoustic environment is unavailable",
)
def test_worker_argument_failure_is_sanitized() -> None:
    private_argument = "--private-audio-id=do-not-publish"

    result = subprocess.run(
        [str(ACOUSTIC_PYTHON), str(WORKER_PATH), private_argument],
        input=CONFIG_PATH.read_bytes(),
        check=False,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 2
    assert result.stderr == b""
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["failure_code"] == "worker_bootstrap_failure"
    assert private_argument.encode("utf-8") not in result.stdout
