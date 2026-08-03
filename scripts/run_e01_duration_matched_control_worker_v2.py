#!/usr/bin/env python3
"""One-process/one-job worker for the synthetic E01 duration gate.

The worker accepts only canonical config JSON on stdin.  It creates a fixed
voiced-plus-internal-noise waveform in memory, verifies the reviewed wheel,
installed extension, runtime, and duration primitive bytes, then executes the
primitive exactly once.  Only aggregate metadata and digests are emitted.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import ctypes
import errno
import fcntl
import hashlib
import importlib.abc
import importlib.machinery
from importlib import metadata
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import socket
import stat
import sys
import sysconfig
import _socket
from types import ModuleType
from typing import Any, Iterator, Mapping, NoReturn, cast

import numpy as np


CONFIG_SCHEMA_VERSION = "voice_coach.e01.duration_matched_control_config.v2"
WORKER_SCHEMA_VERSION = "voice_coach.e01.duration_matched_control_worker_result.v2"
EXPERIMENT_ID = "e01_duration_matched_control_pair_v2"
PRIMITIVE_RELATIVE_PATH = PurePosixPath(
    "src/voice_coach/evaluation/e01_duration_matched_control_v2.py"
)
MAXIMUM_CONFIG_BYTES = 32_768
MAXIMUM_SOURCE_BYTES = 2_000_000
MAXIMUM_WHEEL_BYTES = 32_000_000
MAXIMUM_EXTENSION_BYTES = 64_000_000
KERNEL_NETWORK_GUARD_POLICY = "linux_libseccomp_deny_socket_syscalls_preexec_v1"
VERIFIED_EXTENSION_MATERIALIZATION_POLICY = "sealed_memfd_from_verified_bytes_v1"


class MatchedControlWorkerError(ValueError):
    """Raised internally for a sanitized worker contract failure."""


class _SanitizedArgumentParser(argparse.ArgumentParser):
    """Convert argparse exits and messages to the fixed worker failure path."""

    def error(self, message: str) -> NoReturn:
        raise MatchedControlWorkerError("argument_contract")

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        raise MatchedControlWorkerError("argument_contract")


class _VerifiedBytesLoader(importlib.abc.Loader):
    """Importlib loader that executes only the already-verified source bytes."""

    def __init__(self, source: bytes, origin: str) -> None:
        self._source = source
        self._origin = origin

    def create_module(self, spec: object) -> ModuleType | None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        code = compile(self._source, self._origin, "exec", dont_inherit=True)
        exec(code, module.__dict__)


class _BlockedNetworkSocket(socket.socket):
    """Socket subtype that preserves type checks while denying IP operations."""

    attempt_counter: list[int] | None = None

    def _blocked(self) -> None:
        counter = type(self).attempt_counter
        if counter is None:
            raise MatchedControlWorkerError("network_guard_inactive")
        counter[0] += 1
        raise OSError("network disabled")

    def bind(self, address: Any) -> None:
        if self.family in {socket.AF_INET, socket.AF_INET6}:
            self._blocked()
        super().bind(address)

    def connect(self, address: Any) -> None:
        if self.family in {socket.AF_INET, socket.AF_INET6}:
            self._blocked()
        super().connect(address)

    def connect_ex(self, address: Any) -> int:
        if self.family in {socket.AF_INET, socket.AF_INET6}:
            self._blocked()
        return super().connect_ex(address)

    def sendto(self, data: Any, *args: Any) -> int:
        if self.family in {socket.AF_INET, socket.AF_INET6}:
            self._blocked()
        return super().sendto(data, *args)


@contextmanager
def _blocked_network() -> Iterator[list[int]]:
    original_socket = socket.socket
    original_create_connection = socket.create_connection
    original_getaddrinfo = socket.getaddrinfo
    original_gethostbyname = socket.gethostbyname
    original_gethostbyname_ex = socket.gethostbyname_ex
    original_gethostbyaddr = socket.gethostbyaddr
    original_getnameinfo = socket.getnameinfo
    attempts = [0]

    def reject_network(*_args: object, **_kwargs: object) -> Any:
        attempts[0] += 1
        raise OSError("network disabled")

    _BlockedNetworkSocket.attempt_counter = attempts
    setattr(socket, "socket", _BlockedNetworkSocket)
    socket.create_connection = reject_network
    socket.getaddrinfo = reject_network
    socket.gethostbyname = reject_network
    socket.gethostbyname_ex = reject_network
    socket.gethostbyaddr = reject_network
    socket.getnameinfo = reject_network
    try:
        yield attempts
    finally:
        setattr(socket, "socket", original_socket)
        socket.create_connection = original_create_connection
        socket.getaddrinfo = original_getaddrinfo
        socket.gethostbyname = original_gethostbyname
        socket.gethostbyname_ex = original_gethostbyname_ex
        socket.gethostbyaddr = original_gethostbyaddr
        socket.getnameinfo = original_getnameinfo
        _BlockedNetworkSocket.attempt_counter = None


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _exact_keys(
    value: object,
    expected: set[str],
    *,
    field_name: str,
) -> dict[str, Any]:
    if type(value) is not dict or set(cast(dict[object, object], value)) != expected:
        raise MatchedControlWorkerError(f"{field_name}_contract")
    return cast(dict[str, Any], value)


def _exact(value: object, expected: object, *, field_name: str) -> None:
    if type(value) is not type(expected) or value != expected:
        raise MatchedControlWorkerError(f"{field_name}_contract")


def _read_config() -> tuple[bytes, dict[str, Any]]:
    config_bytes = sys.stdin.buffer.read(MAXIMUM_CONFIG_BYTES + 1)
    if not config_bytes or len(config_bytes) > MAXIMUM_CONFIG_BYTES:
        raise MatchedControlWorkerError("config_size_contract")
    try:
        config = json.loads(config_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MatchedControlWorkerError("config_json_contract") from None
    document = _exact_keys(
        config,
        {
            "schema_version",
            "experiment_id",
            "source_sha256",
            "runtime_pins",
            "execution_policy",
            "synthetic_input",
            "roles",
            "completion_limits",
            "trust_boundary",
        },
        field_name="config",
    )
    if _canonical_json_bytes(document) != config_bytes:
        raise MatchedControlWorkerError("config_canonical_contract")
    _exact(
        document["schema_version"],
        CONFIG_SCHEMA_VERSION,
        field_name="schema_version",
    )
    _exact(document["experiment_id"], EXPERIMENT_ID, field_name="experiment_id")
    sources = cast(dict[str, Any], document["source_sha256"])
    primitive_digest = sources.get(PRIMITIVE_RELATIVE_PATH.as_posix())
    if not _is_sha256(primitive_digest):
        raise MatchedControlWorkerError("primitive_source_pin_contract")
    return config_bytes, document


def _validated_workspace_root(value: Path) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise MatchedControlWorkerError("workspace_root_contract")
    try:
        information = value.lstat()
        resolved = value.resolve(strict=True)
    except (OSError, RuntimeError):
        raise MatchedControlWorkerError("workspace_root_unavailable") from None
    if value.is_symlink() or resolved != value or not stat.S_ISDIR(information.st_mode):
        raise MatchedControlWorkerError("workspace_root_identity")
    return value


def _open_directory_at(parent_fd: int, component: str) -> int:
    try:
        descriptor = os.open(
            component,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        information = os.fstat(descriptor)
    except OSError:
        raise MatchedControlWorkerError("directory_identity") from None
    if not stat.S_ISDIR(information.st_mode):
        os.close(descriptor)
        raise MatchedControlWorkerError("directory_identity")
    return descriptor


def _read_fd(
    descriptor: int,
    *,
    maximum_bytes: int,
) -> tuple[bytes, os.stat_result]:
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > maximum_bytes
        ):
            raise MatchedControlWorkerError("regular_file_contract")
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1 << 20))
            if not chunk:
                raise MatchedControlWorkerError("regular_file_short_read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise MatchedControlWorkerError("regular_file_growth")
        after = os.fstat(descriptor)
    except OSError:
        raise MatchedControlWorkerError("regular_file_read") from None
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_nlink,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_nlink,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if before_identity != after_identity:
        raise MatchedControlWorkerError("regular_file_changed")
    return b"".join(chunks), before


def _sealed_memfd_from_bytes(name: str, content: bytes) -> int:
    """Materialize verified bytes in an immutable anonymous descriptor."""

    required_seals = (
        fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE
    )
    try:
        descriptor = os.memfd_create(
            name,
            flags=os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING,
        )
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise MatchedControlWorkerError("sealed_memfd_write")
            offset += written
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, required_seals)
        if fcntl.fcntl(descriptor, fcntl.F_GET_SEALS) != required_seals:
            raise MatchedControlWorkerError("sealed_memfd_seals")
        information = os.fstat(descriptor)
        if (
            information.st_size != len(content)
            or stat.S_IMODE(information.st_mode) != 0o400
        ):
            raise MatchedControlWorkerError("sealed_memfd_identity")
        observed = bytearray()
        offset = 0
        while offset < len(content):
            chunk = os.pread(descriptor, min(len(content) - offset, 1 << 20), offset)
            if not chunk:
                raise MatchedControlWorkerError("sealed_memfd_short_read")
            observed.extend(chunk)
            offset += len(chunk)
        if bytes(observed) != content:
            raise MatchedControlWorkerError("sealed_memfd_content")
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except OSError:
        if "descriptor" in locals():
            os.close(descriptor)
        raise MatchedControlWorkerError("sealed_memfd_failure") from None
    except Exception:
        if "descriptor" in locals():
            os.close(descriptor)
        raise


def _open_workspace_file(
    workspace_root: Path,
    relative_path: PurePosixPath,
) -> tuple[int, list[int]]:
    if (
        relative_path.is_absolute()
        or not relative_path.parts
        or any(part in {"", ".", ".."} for part in relative_path.parts)
    ):
        raise MatchedControlWorkerError("relative_path_contract")
    descriptors: list[int] = []
    try:
        root_fd = os.open(
            workspace_root,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        descriptors.append(root_fd)
        directory_fd = root_fd
        for component in relative_path.parts[:-1]:
            directory_fd = _open_directory_at(directory_fd, component)
            descriptors.append(directory_fd)
        file_fd = os.open(
            relative_path.parts[-1],
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        return file_fd, descriptors
    except OSError:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise MatchedControlWorkerError("workspace_file_unavailable") from None


def _read_workspace_source(
    workspace_root: Path,
    relative_path: PurePosixPath,
) -> bytes:
    file_fd, directory_fds = _open_workspace_file(workspace_root, relative_path)
    try:
        content, _ = _read_fd(file_fd, maximum_bytes=MAXIMUM_SOURCE_BYTES)
        return content
    finally:
        os.close(file_fd)
        for descriptor in reversed(directory_fds):
            os.close(descriptor)


def _read_external_file(path: Path, *, maximum_bytes: int) -> tuple[bytes, int]:
    if not isinstance(path, Path) or not path.is_absolute():
        raise MatchedControlWorkerError("external_file_path_contract")
    try:
        path_before = path.lstat()
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError:
        raise MatchedControlWorkerError("external_file_unavailable") from None
    try:
        content, information = _read_fd(descriptor, maximum_bytes=maximum_bytes)
    finally:
        os.close(descriptor)
    try:
        path_after = path.lstat()
    except OSError:
        raise MatchedControlWorkerError("external_file_changed") from None
    if (
        path_before.st_dev,
        path_before.st_ino,
        path_before.st_mode,
        path_before.st_nlink,
        path_before.st_size,
        path_before.st_mtime_ns,
        path_before.st_ctime_ns,
    ) != (
        path_after.st_dev,
        path_after.st_ino,
        path_after.st_mode,
        path_after.st_nlink,
        path_after.st_size,
        path_after.st_mtime_ns,
        path_after.st_ctime_ns,
    ):
        raise MatchedControlWorkerError("external_file_changed")
    return content, information.st_size


def _runtime_provenance(
    workspace_root: Path,
    config: Mapping[str, Any],
    reviewed_wheel_path: Path,
) -> tuple[dict[str, Any], int]:
    runtime = _exact_keys(
        config["runtime_pins"],
        {
            "venv_python_relative_path",
            "venv_python_symlink_target",
            "venv_python_minor_symlink_target",
            "base_python_sha256",
            "python_implementation",
            "python_version",
            "python_build_string",
            "python_compiler",
            "python_cache_tag",
            "python_soabi",
            "python_base_prefix",
            "platform_system",
            "platform_machine",
            "libc_name",
            "libc_version",
            "numpy_version",
            "parselmouth_distribution",
            "parselmouth_version",
            "praat_version",
            "reviewed_wheel_filename",
            "reviewed_wheel_sha256",
            "reviewed_wheel_size_bytes",
            "installed_extension_filename",
            "installed_extension_sha256",
            "installed_extension_size_bytes",
            "libseccomp_path",
            "libseccomp_version",
            "libseccomp_sha256",
            "libseccomp_size_bytes",
        },
        field_name="runtime_pins",
    )
    libc_name, libc_version = platform.libc_ver()
    observed: dict[str, object] = {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "python_build_string": sys.version,
        "python_compiler": platform.python_compiler(),
        "python_cache_tag": sys.implementation.cache_tag,
        "python_soabi": sysconfig.get_config_var("SOABI"),
        "python_base_prefix": sys.base_prefix,
        "platform_system": platform.system(),
        "platform_machine": platform.machine(),
        "libc_name": libc_name,
        "libc_version": libc_version,
        "numpy_version": metadata.version("numpy"),
        "parselmouth_version": metadata.version(
            cast(str, runtime["parselmouth_distribution"])
        ),
    }
    for field_name, actual in observed.items():
        _exact(actual, runtime[field_name], field_name=field_name)
    expected_prefix = workspace_root / ".venv-paper-acoustics"
    try:
        if Path(sys.prefix).resolve(strict=True) != expected_prefix.resolve(
            strict=True
        ):
            raise MatchedControlWorkerError("venv_prefix_identity")
    except (OSError, RuntimeError):
        raise MatchedControlWorkerError("venv_prefix_identity") from None
    if (
        reviewed_wheel_path.name != runtime["reviewed_wheel_filename"]
        or not reviewed_wheel_path.is_absolute()
    ):
        raise MatchedControlWorkerError("reviewed_wheel_path_contract")
    wheel_bytes, wheel_size = _read_external_file(
        reviewed_wheel_path,
        maximum_bytes=MAXIMUM_WHEEL_BYTES,
    )
    wheel_sha256 = _sha256(wheel_bytes)
    if (
        wheel_sha256 != runtime["reviewed_wheel_sha256"]
        or wheel_size != runtime["reviewed_wheel_size_bytes"]
    ):
        raise MatchedControlWorkerError("reviewed_wheel_sha256_mismatch")

    distribution = metadata.distribution(cast(str, runtime["parselmouth_distribution"]))
    extension_filename = cast(str, runtime["installed_extension_filename"])
    candidates = [
        entry
        for entry in distribution.files or ()
        if PurePosixPath(str(entry)).name == extension_filename
    ]
    if len(candidates) != 1:
        raise MatchedControlWorkerError("installed_extension_inventory")
    extension_path = Path(str(distribution.locate_file(candidates[0])))
    try:
        relative_extension = PurePosixPath(
            extension_path.relative_to(workspace_root).as_posix()
        )
    except ValueError:
        raise MatchedControlWorkerError("installed_extension_location") from None
    extension_fd, extension_directory_fds = _open_workspace_file(
        workspace_root,
        relative_extension,
    )
    try:
        extension_bytes, extension_information = _read_fd(
            extension_fd,
            maximum_bytes=MAXIMUM_EXTENSION_BYTES,
        )
    finally:
        os.close(extension_fd)
        for descriptor in reversed(extension_directory_fds):
            os.close(descriptor)
    extension_sha256 = _sha256(extension_bytes)
    if (
        extension_sha256 != runtime["installed_extension_sha256"]
        or extension_information.st_size != runtime["installed_extension_size_bytes"]
    ):
        raise MatchedControlWorkerError("installed_extension_sha256_mismatch")
    sealed_extension_fd = _sealed_memfd_from_bytes(
        "voice-coach-parselmouth-extension",
        extension_bytes,
    )
    try:
        extension_loader = importlib.machinery.ExtensionFileLoader(
            "parselmouth",
            f"/proc/self/fd/{sealed_extension_fd}",
        )
        extension_specification = importlib.util.spec_from_loader(
            "parselmouth",
            extension_loader,
            origin=f"/proc/self/fd/{sealed_extension_fd}",
        )
        if extension_specification is None:
            raise MatchedControlWorkerError("installed_extension_load")
        parselmouth = importlib.util.module_from_spec(extension_specification)
        sys.modules["parselmouth"] = parselmouth
        extension_loader.exec_module(parselmouth)
        if (
            getattr(parselmouth, "__version__", None) != runtime["parselmouth_version"]
            or getattr(parselmouth, "PRAAT_VERSION", None) != runtime["praat_version"]
        ):
            raise MatchedControlWorkerError("parselmouth_runtime_contract")
    finally:
        os.close(sealed_extension_fd)
    provenance = {
        "python": {
            "implementation": observed["python_implementation"],
            "version": observed["python_version"],
            "build_string": observed["python_build_string"],
            "compiler": observed["python_compiler"],
            "cache_tag": observed["python_cache_tag"],
            "soabi": observed["python_soabi"],
            "base_interpreter_sha256": runtime["base_python_sha256"],
        },
        "platform": {
            "system": observed["platform_system"],
            "machine": observed["platform_machine"],
            "libc_name": observed["libc_name"],
            "libc_version": observed["libc_version"],
        },
        "numpy_version": observed["numpy_version"],
        "parselmouth_distribution": runtime["parselmouth_distribution"],
        "parselmouth_version": runtime["parselmouth_version"],
        "praat_version": runtime["praat_version"],
        "reviewed_wheel": {
            "filename": runtime["reviewed_wheel_filename"],
            "sha256": wheel_sha256,
            "size_bytes": wheel_size,
        },
        "installed_extension": {
            "filename": extension_filename,
            "sha256": extension_sha256,
            "size_bytes": extension_information.st_size,
            "materialization_policy": VERIFIED_EXTENSION_MATERIALIZATION_POLICY,
        },
    }
    return provenance, extension_information.st_ino


def _load_duration_primitive(
    workspace_root: Path,
    config: Mapping[str, Any],
) -> tuple[ModuleType, str]:
    source = _read_workspace_source(workspace_root, PRIMITIVE_RELATIVE_PATH)
    source_sha256 = _sha256(source)
    expected = cast(Mapping[str, Any], config["source_sha256"])[
        PRIMITIVE_RELATIVE_PATH.as_posix()
    ]
    if source_sha256 != expected:
        raise MatchedControlWorkerError("primitive_source_sha256_mismatch")
    module_name = (
        f"_voice_coach_e01_duration_matched_control_v2_verified_{source_sha256}"
    )
    loader = _VerifiedBytesLoader(source, PRIMITIVE_RELATIVE_PATH.as_posix())
    specification = importlib.util.spec_from_loader(
        module_name,
        loader,
        origin=PRIMITIVE_RELATIVE_PATH.as_posix(),
    )
    if specification is None:
        raise MatchedControlWorkerError("primitive_import_contract")
    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    try:
        loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise MatchedControlWorkerError("primitive_import_contract") from None
    return module, source_sha256


def _validate_job_config(config: Mapping[str, Any], *, role: str) -> None:
    execution = _exact_keys(
        config["execution_policy"],
        {
            "worker_process_count",
            "job_count_per_process",
            "paired_role_process_count",
            "worker_timeout_seconds",
            "worker_stdin_max_bytes",
            "network_policy",
            "expected_python_network_intercept_count",
            "kernel_network_guard_policy",
            "expected_kernel_guard_probe_block_count",
            "expected_kernel_seccomp_mode",
            "verified_executable_materialization_policy",
            "cpu_only",
            "python_hash_seed",
            "native_thread_count",
            "praat_process_isolation_policy",
            "praat_rng_reset_policy",
            "failure_publication_policy",
            "waveform_publication_policy",
            "receipt_publication_policy",
            "internal_pcm_ipc_policy",
            "same_seed_interpretation",
        },
        field_name="execution_policy",
    )
    exact_execution: dict[str, object] = {
        "worker_process_count": 2,
        "job_count_per_process": 1,
        "paired_role_process_count": 2,
        "worker_timeout_seconds": 120,
        "worker_stdin_max_bytes": MAXIMUM_CONFIG_BYTES,
        "network_policy": "deny_all_ip_socket_operations",
        "expected_python_network_intercept_count": 0,
        "kernel_network_guard_policy": KERNEL_NETWORK_GUARD_POLICY,
        "expected_kernel_guard_probe_block_count": 1,
        "expected_kernel_seccomp_mode": 2,
        "verified_executable_materialization_policy": (
            VERIFIED_EXTENSION_MATERIALIZATION_POLICY
        ),
        "cpu_only": True,
        "python_hash_seed": "0",
        "native_thread_count": 1,
        "praat_process_isolation_policy": "dedicated_single_job_subprocess",
        "praat_rng_reset_policy": ("safe_unpredictable_finally_then_process_exit"),
        "failure_publication_policy": "sanitized_code_only",
        "waveform_publication_policy": "aggregate_and_digests_only",
        "receipt_publication_policy": ("content_addressed_immutable_no_overwrite"),
        "internal_pcm_ipc_policy": (
            "ephemeral_synthetic_pcm_in_parent_memory_removed_before_receipt"
        ),
        "same_seed_interpretation": (
            "paired_incremental_estimate_only_rng_draw_counts_may_differ"
        ),
    }
    for field_name, expected in exact_execution.items():
        _exact(execution[field_name], expected, field_name=field_name)
    if os.environ.get("PYTHONHASHSEED") != execution["python_hash_seed"]:
        raise MatchedControlWorkerError("python_hash_seed_environment")
    for variable in (
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        if os.environ.get(variable) != str(execution["native_thread_count"]):
            raise MatchedControlWorkerError("native_thread_environment")
    for variable in (
        "CUDA_VISIBLE_DEVICES",
        "HIP_VISIBLE_DEVICES",
        "ROCR_VISIBLE_DEVICES",
    ):
        if os.environ.get(variable) != "":
            raise MatchedControlWorkerError("cpu_only_environment")

    synthetic = _exact_keys(
        config["synthetic_input"],
        {
            "kind",
            "sample_rate_hz",
            "sample_count",
            "dtype",
            "sine_frequency_hz",
            "sine_amplitude",
            "noise_bit_generator",
            "noise_seed",
            "noise_standard_deviation",
            "noise_intervals_samples",
            "raw_pcm_publication",
            "path_or_identifier_input",
        },
        field_name="synthetic_input",
    )
    expected_synthetic: dict[str, object] = {
        "kind": "deterministic_voiced_with_internal_noise_v1",
        "sample_rate_hz": 16_000,
        "sample_count": 16_000,
        "dtype": "float64",
        "sine_frequency_hz": 180.0,
        "sine_amplitude": 0.1,
        "noise_bit_generator": "numpy.PCG64",
        "noise_seed": 777,
        "noise_standard_deviation": 0.02,
        "noise_intervals_samples": [[4_000, 5_200], [10_400, 11_800]],
        "raw_pcm_publication": False,
        "path_or_identifier_input": False,
    }
    for field_name, expected in expected_synthetic.items():
        _exact(synthetic[field_name], expected, field_name=field_name)

    if role not in {"identity_psola_control", "duration_treatment"}:
        raise MatchedControlWorkerError("role_contract")
    roles = _exact_keys(
        config["roles"],
        {
            "identity_psola_control",
            "duration_treatment",
        },
        field_name="roles",
    )
    transform = _exact_keys(
        roles[role],
        {
            "role",
            "sample_rate_hz",
            "time_step_seconds",
            "analysis_pitch_floor_hz",
            "analysis_pitch_ceiling_hz",
            "segment_start_seconds",
            "segment_end_seconds",
            "transition_seconds",
            "duration_scale_factor",
            "praat_random_seed",
            "praat_process_isolation_policy",
            "praat_rng_reset_policy",
            "expected_sample_count_rounding",
            "maximum_duration_error_samples",
            "minimum_pitch_tier_points",
            "peak_limit",
            "max_output_samples",
        },
        field_name="transform",
    )
    expected_transform: dict[str, object] = {
        "role": role,
        "sample_rate_hz": 16_000,
        "time_step_seconds": 0.01,
        "analysis_pitch_floor_hz": 75.0,
        "analysis_pitch_ceiling_hz": 300.0,
        "segment_start_seconds": 0.2,
        "segment_end_seconds": 0.4,
        "transition_seconds": 0.02,
        "duration_scale_factor": (1.0 if role == "identity_psola_control" else 1.2),
        "praat_random_seed": 5_489,
        "praat_process_isolation_policy": "dedicated_single_job_subprocess",
        "praat_rng_reset_policy": ("safe_unpredictable_finally_then_process_exit"),
        "expected_sample_count_rounding": "half_up",
        "maximum_duration_error_samples": 0,
        "minimum_pitch_tier_points": 3,
        "peak_limit": 0.5,
        "max_output_samples": 20_000,
    }
    for field_name, expected in expected_transform.items():
        _exact(transform[field_name], expected, field_name=field_name)
    limits = cast(Mapping[str, Any], config["completion_limits"])
    if (
        limits.get("paper_ready") is not False
        or limits.get("production_ready") is not False
        or limits.get("actual_korean_speech_completed") is not False
        or limits.get("s1_completed") is not False
        or limits.get("s2_completed") is not False
        or limits.get("actual_korean_matched_control_completed") is not False
        or limits.get("synthetic_matched_control_technical_gate_completed") is not True
        or limits.get("scientific_claim")
        != "synthetic_matched_control_technical_gate_only"
    ):
        raise MatchedControlWorkerError("completion_limits_contract")
    trust_boundary = cast(Mapping[str, Any], config["trust_boundary"])
    if (
        trust_boundary.get("threat_model")
        != "trusted_local_host_and_venv_no_hostile_same_user_race"
        or trust_boundary.get("hostile_same_user_race_resistance_claimed") is not False
        or trust_boundary.get("runner_startup_bytes_attested") is not False
        or trust_boundary.get("scorer_startup_import_closure_attested") is not False
        or trust_boundary.get("acoustic_startup_import_closure_attested") is not False
        or trust_boundary.get("numpy_content_attested") is not False
        or trust_boundary.get("numpy_version_verified") is not True
        or trust_boundary.get("selected_artifact_hashes_verified") is not True
        or trust_boundary.get("external_trusted_launcher_completed") is not False
        or trust_boundary.get("full_os_image_attested") is not False
        or trust_boundary.get("provenance_claim")
        != "selected_artifact_hashes_with_explicit_trusted_host_venv_assumption"
    ):
        raise MatchedControlWorkerError("trust_boundary_contract")


def _synthetic_waveform(config: Mapping[str, Any]) -> tuple[np.ndarray, dict[str, Any]]:
    synthetic = cast(Mapping[str, Any], config["synthetic_input"])
    sample_rate = cast(int, synthetic["sample_rate_hz"])
    sample_count = cast(int, synthetic["sample_count"])
    times = np.arange(sample_count, dtype=np.float64) / float(sample_rate)
    waveform = np.asarray(
        float(synthetic["sine_amplitude"])
        * np.sin(2.0 * np.pi * float(synthetic["sine_frequency_hz"]) * times),
        dtype=np.float64,
    )
    generator = np.random.Generator(np.random.PCG64(cast(int, synthetic["noise_seed"])))
    intervals = cast(list[list[int]], synthetic["noise_intervals_samples"])
    for start, end in intervals:
        waveform[start:end] = generator.normal(
            loc=0.0,
            scale=float(synthetic["noise_standard_deviation"]),
            size=end - start,
        )
    if (
        type(waveform) is not np.ndarray
        or waveform.dtype != np.dtype(np.float64)
        or waveform.ndim != 1
        or waveform.size != sample_count
        or not bool(np.isfinite(waveform).all())
    ):
        raise MatchedControlWorkerError("synthetic_waveform_contract")
    canonical = np.asarray(waveform, dtype="<f8", order="C")
    aggregate = {
        "kind": synthetic["kind"],
        "sample_rate_hz": sample_rate,
        "sample_count": sample_count,
        "dtype": synthetic["dtype"],
        "sine_frequency_hz": synthetic["sine_frequency_hz"],
        "sine_amplitude": synthetic["sine_amplitude"],
        "noise_bit_generator": synthetic["noise_bit_generator"],
        "noise_seed": synthetic["noise_seed"],
        "noise_standard_deviation": synthetic["noise_standard_deviation"],
        "noise_interval_count": len(intervals),
        "noise_sample_count": sum(end - start for start, end in intervals),
        "input_peak": float(np.max(np.abs(waveform))),
        "input_pcm_sha256": _sha256(canonical.tobytes(order="C")),
        "raw_pcm_included": False,
        "path_or_identifier_included": False,
    }
    return waveform, aggregate


def _probe_kernel_network_guard() -> tuple[int, int]:
    """Prove that a native socket syscall is denied by the inherited filter."""

    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    libc.prctl.restype = ctypes.c_int
    seccomp_mode = libc.prctl(21, 0, 0, 0, 0)
    if seccomp_mode != 2:
        raise MatchedControlWorkerError("kernel_seccomp_mode")
    try:
        handle = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    except OSError as error:
        if error.errno != errno.EPERM:
            raise MatchedControlWorkerError("kernel_network_guard_probe") from None
        return 1, seccomp_mode
    handle.close()
    raise MatchedControlWorkerError("kernel_network_guard_inactive")


def _pcm_sha256(waveform: np.ndarray) -> str:
    canonical = np.asarray(waveform, dtype="<f8", order="C")
    return _sha256(canonical.tobytes(order="C"))


def _direct_waveform_comparison(
    reference: np.ndarray,
    candidate: np.ndarray,
) -> dict[str, Any]:
    """Measure the direct prefix reconstruction without subtracting a control."""

    common_count = min(int(reference.size), int(candidate.size))
    if common_count <= 0:
        raise MatchedControlWorkerError("waveform_comparison_contract")
    reference_prefix = np.asarray(reference[:common_count], dtype=np.float64)
    candidate_prefix = np.asarray(candidate[:common_count], dtype=np.float64)
    difference = candidate_prefix - reference_prefix
    reference_centered = reference_prefix - float(np.mean(reference_prefix))
    candidate_centered = candidate_prefix - float(np.mean(candidate_prefix))
    denominator = float(
        np.linalg.norm(reference_centered) * np.linalg.norm(candidate_centered)
    )
    correlation = (
        float(np.dot(reference_centered, candidate_centered) / denominator)
        if denominator > 0.0
        else 0.0
    )
    return {
        "common_prefix_sample_count": common_count,
        "reference_sample_count": int(reference.size),
        "candidate_sample_count": int(candidate.size),
        "candidate_minus_reference_sample_count": (
            int(candidate.size) - int(reference.size)
        ),
        "mean_absolute_error": float(np.mean(np.abs(difference))),
        "root_mean_squared_error": float(np.sqrt(np.mean(np.square(difference)))),
        "maximum_absolute_error": float(np.max(np.abs(difference))),
        "pearson_correlation": correlation,
        "reference_pcm_sha256": _pcm_sha256(reference),
        "candidate_pcm_sha256": _pcm_sha256(candidate),
        "aligned_prefix_only_for_unequal_lengths": (
            int(reference.size) != int(candidate.size)
        ),
    }


def _execute_job(
    workspace_root: Path,
    reviewed_wheel_path: Path,
    config: Mapping[str, Any],
    *,
    role: str,
) -> dict[str, Any]:
    _validate_job_config(config, role=role)
    runtime_provenance, _extension_inode = _runtime_provenance(
        workspace_root,
        config,
        reviewed_wheel_path,
    )
    primitive, primitive_sha256 = _load_duration_primitive(workspace_root, config)
    waveform, synthetic_aggregate = _synthetic_waveform(config)
    roles = cast(Mapping[str, Mapping[str, Any]], config["roles"])
    transform = roles[role]
    job_count = 0
    job_count += 1
    if job_count != 1:
        raise MatchedControlWorkerError("job_count_contract")
    try:
        result = primitive.apply_duration_matched_control_v2(
            waveform,
            role=role,
            sample_rate_hz=transform["sample_rate_hz"],
            time_step_seconds=transform["time_step_seconds"],
            analysis_pitch_floor_hz=transform["analysis_pitch_floor_hz"],
            analysis_pitch_ceiling_hz=transform["analysis_pitch_ceiling_hz"],
            segment_start_seconds=transform["segment_start_seconds"],
            segment_end_seconds=transform["segment_end_seconds"],
            transition_seconds=transform["transition_seconds"],
            duration_scale_factor=transform["duration_scale_factor"],
            praat_random_seed=transform["praat_random_seed"],
            praat_process_isolation_policy=transform["praat_process_isolation_policy"],
            expected_sample_count_rounding=transform["expected_sample_count_rounding"],
            maximum_duration_error_samples=transform["maximum_duration_error_samples"],
            minimum_pitch_tier_points=transform["minimum_pitch_tier_points"],
            peak_limit=transform["peak_limit"],
            max_output_samples=transform["max_output_samples"],
        )
    except Exception:
        raise MatchedControlWorkerError("duration_transform_failure") from None
    if job_count != 1:
        raise MatchedControlWorkerError("job_count_contract")
    metadata_document = result.metadata.to_dict()
    if (
        metadata_document.get("backend_provenance") != "pinned_parselmouth_runtime"
        or metadata_document.get("paper_evidence_eligibility")
        != "requires_external_worker_receipt"
        or metadata_document.get("parselmouth_version")
        != cast(Mapping[str, Any], config["runtime_pins"])["parselmouth_version"]
        or metadata_document.get("praat_version")
        != cast(Mapping[str, Any], config["runtime_pins"])["praat_version"]
        or metadata_document.get("praat_process_isolation_policy")
        != transform["praat_process_isolation_policy"]
        or metadata_document.get("praat_rng_reset_policy")
        != transform["praat_rng_reset_policy"]
        or metadata_document.get("role") != role
        or metadata_document.get("execution_path_id")
        != "six_point_duration_tier_replace_seeded_overlap_add_no_fast_path_v2"
        or metadata_document.get("no_copy_fast_path") is not True
        or metadata_document.get("psola_resynthesis_call_count") != 1
        or not _is_sha256(metadata_document.get("output_digest_sha256"))
    ):
        raise MatchedControlWorkerError("duration_metadata_contract")
    output_waveform = np.asarray(result.waveform, dtype=np.float64).copy()
    direct_comparison = _direct_waveform_comparison(waveform, output_waveform)
    private_pcm = np.asarray(output_waveform, dtype="<f8", order="C").tobytes(order="C")
    private_pair_ipc = {
        "encoding": "base64_little_endian_float64",
        "sample_count": int(output_waveform.size),
        "pcm_sha256": _sha256(private_pcm),
        "payload": base64.b64encode(private_pcm).decode("ascii"),
        "publication_forbidden": True,
    }
    del result
    del output_waveform
    del waveform
    return {
        "runtime_build_provenance": runtime_provenance,
        "source_provenance": {
            "duration_primitive_relative_path": (PRIMITIVE_RELATIVE_PATH.as_posix()),
            "duration_primitive_sha256": primitive_sha256,
            "import_policy": "verified_bytes_importlib_loader_no_package_init",
        },
        "synthetic_input_aggregate": synthetic_aggregate,
        "transform_aggregate": metadata_document,
        "raw_to_output_direct_metrics": direct_comparison,
        "private_pair_ipc": private_pair_ipc,
        "role": role,
        "job_count": job_count,
    }


def _success_payload(
    config: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    config_sha256: str,
    python_network_intercept_count: int,
    kernel_network_guard_probe_block_count: int,
    kernel_seccomp_mode: int,
) -> dict[str, Any]:
    execution = cast(Mapping[str, Any], config["execution_policy"])
    return {
        "schema_version": WORKER_SCHEMA_VERSION,
        "status": "completed",
        "experiment_id": EXPERIMENT_ID,
        "role": result["role"],
        "lineage": {
            "config_sha256": config_sha256,
            "duration_primitive_sha256": result["source_provenance"][
                "duration_primitive_sha256"
            ],
        },
        "source_provenance": result["source_provenance"],
        "runtime_build_provenance": result["runtime_build_provenance"],
        "execution": {
            "worker_process_count": 1,
            "job_count": result["job_count"],
            "job_count_per_process_policy": execution["job_count_per_process"],
            "cpu_only": execution["cpu_only"],
            "network_policy": execution["network_policy"],
            "python_network_intercept_count": python_network_intercept_count,
            "kernel_network_guard_policy": execution["kernel_network_guard_policy"],
            "kernel_network_guard_probe_block_count": (
                kernel_network_guard_probe_block_count
            ),
            "kernel_seccomp_mode": kernel_seccomp_mode,
            "praat_process_isolation_policy": execution[
                "praat_process_isolation_policy"
            ],
            "praat_rng_reset_policy": execution["praat_rng_reset_policy"],
        },
        "synthetic_input_aggregate": result["synthetic_input_aggregate"],
        "transform_aggregate": result["transform_aggregate"],
        "raw_to_output_direct_metrics": result["raw_to_output_direct_metrics"],
        "private_pair_ipc": result["private_pair_ipc"],
        "publication": {
            "raw_pcm_included": False,
            "output_waveform_included": False,
            "absolute_path_included": False,
            "speaker_or_utterance_identifier_included": False,
            "aggregate_and_digests_only": True,
            "private_pcm_ipc_must_be_removed_before_pair_receipt": True,
        },
        "completion_limits": config["completion_limits"],
        "trust_boundary": config["trust_boundary"],
    }


def _failure_payload(
    failure_code: str,
    python_network_intercept_count: int,
) -> dict[str, Any]:
    return {
        "schema_version": WORKER_SCHEMA_VERSION,
        "status": "error",
        "experiment_id": EXPERIMENT_ID,
        "failure_code": failure_code,
        "python_network_intercept_count": python_network_intercept_count,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = _SanitizedArgumentParser(add_help=False)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--reviewed-wheel", type=Path, required=True)
    parser.add_argument(
        "--role",
        choices=("identity_psola_control", "duration_treatment"),
        required=True,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run exactly one synthetic duration job and emit sanitized JSON."""

    python_network_intercept_count = 0
    try:
        arguments = _build_parser().parse_args(argv)
        config_bytes, config = _read_config()
        workspace_root = _validated_workspace_root(arguments.workspace_root)
        with _blocked_network() as attempts:
            try:
                kernel_probe_block_count, kernel_seccomp_mode = (
                    _probe_kernel_network_guard()
                )
                result = _execute_job(
                    workspace_root,
                    arguments.reviewed_wheel,
                    config,
                    role=arguments.role,
                )
            except MatchedControlWorkerError:
                python_network_intercept_count = attempts[0]
                payload = _failure_payload(
                    "worker_contract_failure",
                    python_network_intercept_count,
                )
                print(
                    _canonical_json_bytes(payload).decode("utf-8"),
                    end="",
                )
                return 2
            except Exception:
                python_network_intercept_count = attempts[0]
                payload = _failure_payload(
                    "worker_internal_failure",
                    python_network_intercept_count,
                )
                print(
                    _canonical_json_bytes(payload).decode("utf-8"),
                    end="",
                )
                return 2
            python_network_intercept_count = attempts[0]
        if python_network_intercept_count != 0:
            payload = _failure_payload(
                "network_attempt_detected",
                python_network_intercept_count,
            )
            print(_canonical_json_bytes(payload).decode("utf-8"), end="")
            return 2
        payload = _success_payload(
            config,
            result,
            config_sha256=_sha256(config_bytes),
            python_network_intercept_count=python_network_intercept_count,
            kernel_network_guard_probe_block_count=kernel_probe_block_count,
            kernel_seccomp_mode=kernel_seccomp_mode,
        )
        print(_canonical_json_bytes(payload).decode("utf-8"), end="")
        return 0
    except (MatchedControlWorkerError, metadata.PackageNotFoundError):
        payload = _failure_payload(
            "worker_bootstrap_failure",
            python_network_intercept_count,
        )
        print(_canonical_json_bytes(payload).decode("utf-8"), end="")
        return 2
    except Exception:
        payload = _failure_payload(
            "worker_internal_failure",
            python_network_intercept_count,
        )
        print(_canonical_json_bytes(payload).decode("utf-8"), end="")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
