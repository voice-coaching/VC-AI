"""Offline orchestration and immutable receipt publication for E01 duration.

This module is loaded from verified source bytes by the thin CLI bootstrap.  It
launches exactly one pinned acoustic-environment process, accepts only the
aggregate result of a deterministic synthetic job, and publishes one
content-addressed receipt.  It never reads corpus audio and never receives a
speaker, utterance, or file identifier.
"""

from __future__ import annotations

import ctypes
import errno
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
from typing import Any, Mapping, cast


CONFIG_SCHEMA_VERSION = "voice_coach.e01.duration_worker_config.v1"
RECEIPT_SCHEMA_VERSION = "voice_coach.e01.duration_worker_receipt.v1"
WORKER_SCHEMA_VERSION = "voice_coach.e01.duration_worker_result.v1"
EXPERIMENT_ID = "e01_duration_synthetic_worker_receipt_v1"

CONFIG_RELATIVE_PATH = PurePosixPath(
    "configs/experiments/e01_duration_synthetic_worker_v1.json"
)
RUNNER_RELATIVE_PATH = PurePosixPath("scripts/run_e01_duration_synthetic_receipt.py")
WORKER_RELATIVE_PATH = PurePosixPath("scripts/run_e01_duration_synthetic_worker.py")
APPLICATION_RELATIVE_PATH = PurePosixPath(
    "src/voice_coach/experiments/e01_duration_worker_receipt.py"
)
PRIMITIVE_RELATIVE_PATH = PurePosixPath(
    "src/voice_coach/evaluation/e01_duration_transform.py"
)
SOURCE_PATHS = (
    RUNNER_RELATIVE_PATH,
    WORKER_RELATIVE_PATH,
    APPLICATION_RELATIVE_PATH,
    PRIMITIVE_RELATIVE_PATH,
)
OUTPUT_RELATIVE_ROOT = PurePosixPath(
    "artifacts/phase1/e01_duration_synthetic_worker_receipts"
)

MAXIMUM_CONFIG_BYTES = 32_768
MAXIMUM_SOURCE_BYTES = 2_000_000
MAXIMUM_WORKER_STDOUT_BYTES = 65_536
SHA256_HEX_LENGTH = 64
DIRECTORY_MODE = 0o700
RECEIPT_MODE = 0o600
KERNEL_NETWORK_GUARD_POLICY = "linux_libseccomp_deny_socket_syscalls_preexec_v1"
VERIFIED_EXECUTABLE_MATERIALIZATION_POLICY = "sealed_memfd_from_verified_bytes_v1"
KERNEL_NETWORK_GUARD_SYSCALLS = (
    "accept",
    "accept4",
    "bind",
    "connect",
    "getpeername",
    "getsockname",
    "getsockopt",
    "io_uring_enter",
    "io_uring_register",
    "io_uring_setup",
    "listen",
    "recvfrom",
    "recvmmsg",
    "recvmsg",
    "sendmmsg",
    "sendmsg",
    "sendto",
    "setsockopt",
    "shutdown",
    "socket",
    "socketpair",
)


class E01DurationWorkerReceiptError(ValueError):
    """Raised when the bounded synthetic receipt contract fails closed."""


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    """Stable bytes and inode metadata from one no-follow descriptor read."""

    content: bytes
    sha256: str
    device: int
    inode: int
    mode: int
    link_count: int
    size: int


class _ScmpVersion(ctypes.Structure):
    _fields_ = [
        ("major", ctypes.c_uint),
        ("minor", ctypes.c_uint),
        ("micro", ctypes.c_uint),
    ]


class _PreparedSeccompGuard:
    """Exact libseccomp filter inherited by the child and loaded pre-exec."""

    _ACTION_ALLOW = 0x7FFF0000
    _ACTION_ERRNO_EPERM = 0x00050000 | errno.EPERM

    def __init__(
        self,
        *,
        library: Any,
        context: int,
        library_fd: int,
        library_snapshot: _FileSnapshot,
        library_filename: str,
        version: str,
    ) -> None:
        self._library = library
        self._context = context
        self._library_fd = library_fd
        self._library_snapshot = library_snapshot
        self._library_filename = library_filename
        self._version = version
        self._closed = False

    @classmethod
    def prepare(
        cls,
        runtime: Mapping[str, Any],
    ) -> _PreparedSeccompGuard:
        library_path = Path(cast(str, runtime["libseccomp_path"]))
        library_bytes, snapshot = _read_external_regular_file(
            library_path,
            maximum_bytes=4_000_000,
        )
        if snapshot.sha256 != runtime["libseccomp_sha256"]:
            raise E01DurationWorkerReceiptError("libseccomp_sha256_mismatch")
        library_fd = _sealed_memfd_from_bytes(
            "voice-coach-libseccomp",
            library_bytes,
            executable=False,
        )
        try:
            library = ctypes.CDLL(
                f"/proc/self/fd/{library_fd}",
                use_errno=True,
            )
            library.seccomp_init.argtypes = [ctypes.c_uint32]
            library.seccomp_init.restype = ctypes.c_void_p
            library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
            library.seccomp_syscall_resolve_name.restype = ctypes.c_int
            library.seccomp_rule_add.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_int,
                ctypes.c_uint,
            ]
            library.seccomp_rule_add.restype = ctypes.c_int
            library.seccomp_load.argtypes = [ctypes.c_void_p]
            library.seccomp_load.restype = ctypes.c_int
            library.seccomp_release.argtypes = [ctypes.c_void_p]
            library.seccomp_release.restype = None
            library.seccomp_version.argtypes = []
            library.seccomp_version.restype = ctypes.POINTER(_ScmpVersion)
            version_pointer = library.seccomp_version()
            if not version_pointer:
                raise E01DurationWorkerReceiptError("libseccomp_version")
            version_struct = version_pointer.contents
            version = (
                f"{version_struct.major}.{version_struct.minor}.{version_struct.micro}"
            )
            if version != runtime["libseccomp_version"]:
                raise E01DurationWorkerReceiptError("libseccomp_version")
            context_pointer = library.seccomp_init(cls._ACTION_ALLOW)
            if not context_pointer:
                raise E01DurationWorkerReceiptError("libseccomp_init")
            context = cast(int, context_pointer)
            for syscall_name in KERNEL_NETWORK_GUARD_SYSCALLS:
                syscall_number = library.seccomp_syscall_resolve_name(
                    syscall_name.encode("ascii")
                )
                if syscall_number < 0:
                    library.seccomp_release(context)
                    raise E01DurationWorkerReceiptError("libseccomp_syscall_resolution")
                if (
                    library.seccomp_rule_add(
                        context,
                        cls._ACTION_ERRNO_EPERM,
                        syscall_number,
                        0,
                    )
                    != 0
                ):
                    library.seccomp_release(context)
                    raise E01DurationWorkerReceiptError("libseccomp_rule")
        except Exception:
            os.close(library_fd)
            raise
        return cls(
            library=library,
            context=context,
            library_fd=library_fd,
            library_snapshot=snapshot,
            library_filename=library_path.name,
            version=version,
        )

    def install_in_child(self) -> None:
        """Load the prepared filter after fork and before interpreter exec."""

        if self._library.seccomp_load(self._context) != 0:
            os._exit(126)

    def provenance(self) -> dict[str, Any]:
        return {
            "policy": KERNEL_NETWORK_GUARD_POLICY,
            "libseccomp_filename": self._library_filename,
            "libseccomp_version": self._version,
            "libseccomp_sha256": self._library_snapshot.sha256,
            "libseccomp_size_bytes": self._library_snapshot.size,
            "blocked_syscalls": list(KERNEL_NETWORK_GUARD_SYSCALLS),
            "installation_boundary": "subprocess_preexec_before_python_exec",
            "denied_errno": "EPERM",
        }

    def close(self) -> None:
        if self._closed:
            return
        self._library.seccomp_release(self._context)
        os.close(self._library_fd)
        self._closed = True


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Return the single canonical JSON encoding used by config and receipts."""

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
        and len(value) == SHA256_HEX_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _exact_keys(
    value: object,
    *,
    expected: set[str],
    field_name: str,
) -> dict[str, Any]:
    if type(value) is not dict or set(cast(dict[object, object], value)) != expected:
        raise E01DurationWorkerReceiptError(f"{field_name}_contract")
    return cast(dict[str, Any], value)


def _exact_value(value: object, expected: object, *, field_name: str) -> None:
    if type(value) is not type(expected) or value != expected:
        raise E01DurationWorkerReceiptError(f"{field_name}_contract")


def _validate_source_pins(value: object) -> dict[str, str]:
    expected_paths = {path.as_posix() for path in SOURCE_PATHS}
    pins = _exact_keys(
        value,
        expected=expected_paths,
        field_name="source_sha256",
    )
    for path, digest in pins.items():
        if not _is_sha256(digest):
            raise E01DurationWorkerReceiptError(
                f"source_sha256_{PurePosixPath(path).name}_contract"
            )
    return cast(dict[str, str], pins)


def validate_config(config_bytes: bytes) -> dict[str, Any]:
    """Validate the exact, synthetic-only, one-job experiment contract."""

    if (
        type(config_bytes) is not bytes
        or not config_bytes
        or len(config_bytes) > MAXIMUM_CONFIG_BYTES
    ):
        raise E01DurationWorkerReceiptError("config_size_contract")
    try:
        decoded = config_bytes.decode("utf-8")
        document = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise E01DurationWorkerReceiptError("config_json_contract") from None
    config = _exact_keys(
        document,
        expected={
            "schema_version",
            "experiment_id",
            "source_sha256",
            "runtime_pins",
            "execution_policy",
            "synthetic_input",
            "transform",
            "completion_limits",
        },
        field_name="config",
    )
    if canonical_json_bytes(config) != config_bytes:
        raise E01DurationWorkerReceiptError("config_canonical_encoding_contract")
    _exact_value(
        config["schema_version"],
        CONFIG_SCHEMA_VERSION,
        field_name="schema_version",
    )
    _exact_value(
        config["experiment_id"],
        EXPERIMENT_ID,
        field_name="experiment_id",
    )
    _validate_source_pins(config["source_sha256"])

    runtime = _exact_keys(
        config["runtime_pins"],
        expected={
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
            "installed_extension_filename",
            "installed_extension_sha256",
            "libseccomp_path",
            "libseccomp_version",
            "libseccomp_sha256",
        },
        field_name="runtime_pins",
    )
    expected_runtime: dict[str, object] = {
        "venv_python_relative_path": ".venv-paper-acoustics/bin/python",
        "venv_python_symlink_target": "python3.12",
        "venv_python_minor_symlink_target": "/usr/bin/python3.12",
        "base_python_sha256": (
            "6242e0e8650d7dbdebbc25e08bf4c9359ddaf65f54fcae0e57fa99395fa5357a"
        ),
        "python_implementation": "CPython",
        "python_version": "3.12.3",
        "python_build_string": ("3.12.3 (main, Mar 23 2026, 19:04:32) [GCC 13.3.0]"),
        "python_compiler": "GCC 13.3.0",
        "python_cache_tag": "cpython-312",
        "python_soabi": "cpython-312-aarch64-linux-gnu",
        "python_base_prefix": "/usr",
        "platform_system": "Linux",
        "platform_machine": "aarch64",
        "libc_name": "glibc",
        "libc_version": "2.39",
        "numpy_version": "2.5.1",
        "parselmouth_distribution": "praat-parselmouth",
        "parselmouth_version": "0.4.7",
        "praat_version": "6.1.38",
        "reviewed_wheel_filename": (
            "praat_parselmouth-0.4.7-cp312-cp312-linux_aarch64.whl"
        ),
        "reviewed_wheel_sha256": (
            "8589ea46113a899abbe77375ddacbb998c1f7e456c0584225dbadd60fdf175f1"
        ),
        "installed_extension_filename": (
            "parselmouth.cpython-312-aarch64-linux-gnu.so"
        ),
        "installed_extension_sha256": (
            "81cfbf20dd0167f10bff5305dfe224a8fa41c2ab2bc122b5cbaa40069737e08b"
        ),
        "libseccomp_path": ("/usr/lib/aarch64-linux-gnu/libseccomp.so.2.5.5"),
        "libseccomp_version": "2.5.5",
        "libseccomp_sha256": (
            "3d128123b494ca9e88f3c237b0912397145af1754e86ea99f460af6857441f19"
        ),
    }
    for field_name, expected in expected_runtime.items():
        _exact_value(runtime[field_name], expected, field_name=field_name)

    execution = _exact_keys(
        config["execution_policy"],
        expected={
            "worker_process_count",
            "job_count_per_process",
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
        },
        field_name="execution_policy",
    )
    expected_execution: dict[str, object] = {
        "worker_process_count": 1,
        "job_count_per_process": 1,
        "worker_timeout_seconds": 120,
        "worker_stdin_max_bytes": MAXIMUM_CONFIG_BYTES,
        "network_policy": "deny_all_ip_socket_operations",
        "expected_python_network_intercept_count": 0,
        "kernel_network_guard_policy": KERNEL_NETWORK_GUARD_POLICY,
        "expected_kernel_guard_probe_block_count": 1,
        "expected_kernel_seccomp_mode": 2,
        "verified_executable_materialization_policy": (
            VERIFIED_EXECUTABLE_MATERIALIZATION_POLICY
        ),
        "cpu_only": True,
        "python_hash_seed": "0",
        "native_thread_count": 1,
        "praat_process_isolation_policy": "dedicated_single_job_subprocess",
        "praat_rng_reset_policy": ("safe_unpredictable_finally_then_process_exit"),
        "failure_publication_policy": "sanitized_code_only",
        "waveform_publication_policy": "aggregate_and_digests_only",
        "receipt_publication_policy": ("content_addressed_immutable_no_overwrite"),
    }
    for field_name, expected in expected_execution.items():
        _exact_value(execution[field_name], expected, field_name=field_name)

    synthetic = _exact_keys(
        config["synthetic_input"],
        expected={
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
        _exact_value(synthetic[field_name], expected, field_name=field_name)

    transform = _exact_keys(
        config["transform"],
        expected={
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
        "sample_rate_hz": 16_000,
        "time_step_seconds": 0.01,
        "analysis_pitch_floor_hz": 75.0,
        "analysis_pitch_ceiling_hz": 300.0,
        "segment_start_seconds": 0.2,
        "segment_end_seconds": 0.4,
        "transition_seconds": 0.02,
        "duration_scale_factor": 1.2,
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
        _exact_value(transform[field_name], expected, field_name=field_name)
    if transform["duration_scale_factor"] == 1.0:
        raise E01DurationWorkerReceiptError("matched_control_forbidden")

    limits = _exact_keys(
        config["completion_limits"],
        expected={
            "paper_ready",
            "production_ready",
            "actual_korean_speech_completed",
            "s1_completed",
            "s2_completed",
            "matched_control_completed",
            "scientific_claim",
        },
        field_name="completion_limits",
    )
    expected_limits: dict[str, object] = {
        "paper_ready": False,
        "production_ready": False,
        "actual_korean_speech_completed": False,
        "s1_completed": False,
        "s2_completed": False,
        "matched_control_completed": False,
        "scientific_claim": "synthetic_technical_gate_only",
    }
    for field_name, expected in expected_limits.items():
        _exact_value(limits[field_name], expected, field_name=field_name)
    return config


def _validated_workspace_root(workspace_root: Path) -> Path:
    if not isinstance(workspace_root, Path) or not workspace_root.is_absolute():
        raise E01DurationWorkerReceiptError("workspace_root_contract")
    try:
        information = workspace_root.lstat()
        resolved = workspace_root.resolve(strict=True)
    except (OSError, RuntimeError):
        raise E01DurationWorkerReceiptError("workspace_root_unavailable") from None
    if (
        workspace_root.is_symlink()
        or resolved != workspace_root
        or not stat.S_ISDIR(information.st_mode)
    ):
        raise E01DurationWorkerReceiptError("workspace_root_identity")
    return workspace_root


def _validated_relative_path(relative_path: PurePosixPath) -> tuple[str, ...]:
    if (
        relative_path.is_absolute()
        or not relative_path.parts
        or any(part in {"", ".", ".."} for part in relative_path.parts)
    ):
        raise E01DurationWorkerReceiptError("relative_path_contract")
    return relative_path.parts


def _open_directory_at(parent_fd: int, name: str) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
        information = os.fstat(descriptor)
    except OSError:
        raise E01DurationWorkerReceiptError("directory_identity") from None
    if not stat.S_ISDIR(information.st_mode):
        os.close(descriptor)
        raise E01DurationWorkerReceiptError("directory_identity")
    return descriptor


def _read_regular_descriptor(
    descriptor: int,
    *,
    maximum_bytes: int,
) -> _FileSnapshot:
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size < 0
            or before.st_size > maximum_bytes
        ):
            raise E01DurationWorkerReceiptError("regular_file_contract")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1 << 20))
            if not chunk:
                raise E01DurationWorkerReceiptError("regular_file_short_read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise E01DurationWorkerReceiptError("regular_file_growth")
        after = os.fstat(descriptor)
    except OSError:
        raise E01DurationWorkerReceiptError("regular_file_read") from None
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_nlink,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_nlink,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if identity_before != identity_after:
        raise E01DurationWorkerReceiptError("regular_file_changed")
    content = b"".join(chunks)
    return _FileSnapshot(
        content=content,
        sha256=_sha256(content),
        device=before.st_dev,
        inode=before.st_ino,
        mode=stat.S_IMODE(before.st_mode),
        link_count=before.st_nlink,
        size=before.st_size,
    )


def read_workspace_file(
    workspace_root: Path,
    relative_path: PurePosixPath,
    *,
    maximum_bytes: int = MAXIMUM_SOURCE_BYTES,
) -> _FileSnapshot:
    """Read a workspace file by descriptor without following path symlinks."""

    root = _validated_workspace_root(workspace_root)
    parts = _validated_relative_path(relative_path)
    root_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: list[int] = []
    try:
        root_fd = os.open(root, root_flags)
        descriptors.append(root_fd)
        directory_fd = root_fd
        for component in parts[:-1]:
            directory_fd = _open_directory_at(directory_fd, component)
            descriptors.append(directory_fd)
        file_flags = (
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        file_fd = os.open(parts[-1], file_flags, dir_fd=directory_fd)
        descriptors.append(file_fd)
        return _read_regular_descriptor(file_fd, maximum_bytes=maximum_bytes)
    except OSError:
        raise E01DurationWorkerReceiptError("workspace_file_unavailable") from None
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def verify_source_pins(
    workspace_root: Path,
    config: Mapping[str, Any],
) -> dict[str, str]:
    """Re-hash every pinned source through no-follow descriptors."""

    pins = _validate_source_pins(config["source_sha256"])
    actual: dict[str, str] = {}
    for relative_path in SOURCE_PATHS:
        snapshot = read_workspace_file(workspace_root, relative_path)
        path_text = relative_path.as_posix()
        if snapshot.sha256 != pins[path_text]:
            raise E01DurationWorkerReceiptError("source_sha256_mismatch")
        actual[path_text] = snapshot.sha256
    return actual


def _sealed_memfd_from_bytes(
    name: str,
    content: bytes,
    *,
    executable: bool,
) -> int:
    """Copy verified bytes to a sealed anonymous file and re-hash it."""

    if type(name) is not str or not name or type(content) is not bytes or not content:
        raise E01DurationWorkerReceiptError("sealed_memfd_input_contract")
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
                raise E01DurationWorkerReceiptError("sealed_memfd_write")
            offset += written
        os.fchmod(descriptor, 0o500 if executable else 0o400)
        os.fsync(descriptor)
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, required_seals)
        if fcntl.fcntl(descriptor, fcntl.F_GET_SEALS) != required_seals:
            raise E01DurationWorkerReceiptError("sealed_memfd_seals")
        information = os.fstat(descriptor)
        if (
            not stat.S_ISREG(information.st_mode)
            or information.st_size != len(content)
            or stat.S_IMODE(information.st_mode) != (0o500 if executable else 0o400)
        ):
            raise E01DurationWorkerReceiptError("sealed_memfd_identity")
        observed = bytearray()
        offset = 0
        while offset < len(content):
            chunk = os.pread(descriptor, min(len(content) - offset, 1 << 20), offset)
            if not chunk:
                raise E01DurationWorkerReceiptError("sealed_memfd_short_read")
            observed.extend(chunk)
            offset += len(chunk)
        if bytes(observed) != content:
            raise E01DurationWorkerReceiptError("sealed_memfd_content")
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except OSError:
        if "descriptor" in locals():
            os.close(descriptor)
        raise E01DurationWorkerReceiptError("sealed_memfd_failure") from None
    except Exception:
        if "descriptor" in locals():
            os.close(descriptor)
        raise


def _verify_python_launcher(
    workspace_root: Path,
    runtime: Mapping[str, Any],
) -> tuple[Path, int, _FileSnapshot]:
    relative = cast(str, runtime["venv_python_relative_path"])
    launcher = workspace_root / relative
    minor = launcher.parent / cast(str, runtime["venv_python_symlink_target"])
    try:
        if (
            not launcher.is_symlink()
            or os.readlink(launcher) != cast(str, runtime["venv_python_symlink_target"])
            or not minor.is_symlink()
            or os.readlink(minor)
            != cast(str, runtime["venv_python_minor_symlink_target"])
        ):
            raise E01DurationWorkerReceiptError("python_launcher_identity")
        base_path = Path(cast(str, runtime["venv_python_minor_symlink_target"]))
        base_bytes, base_snapshot = _read_external_regular_file(
            base_path,
            maximum_bytes=16_000_000,
        )
    except OSError:
        raise E01DurationWorkerReceiptError("python_launcher_identity") from None
    if base_snapshot.sha256 != runtime["base_python_sha256"]:
        raise E01DurationWorkerReceiptError("base_python_sha256_mismatch")
    descriptor = _sealed_memfd_from_bytes(
        "voice-coach-python",
        base_bytes,
        executable=True,
    )
    return launcher, descriptor, base_snapshot


def _read_external_regular_file(
    path: Path,
    *,
    maximum_bytes: int,
) -> tuple[bytes, _FileSnapshot]:
    if not isinstance(path, Path) or not path.is_absolute():
        raise E01DurationWorkerReceiptError("external_file_path_contract")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        path_before = path.lstat()
        descriptor = os.open(path, flags)
    except OSError:
        raise E01DurationWorkerReceiptError("external_file_unavailable") from None
    try:
        snapshot = _read_regular_descriptor(
            descriptor,
            maximum_bytes=maximum_bytes,
        )
    finally:
        os.close(descriptor)
    try:
        path_after = path.lstat()
    except OSError:
        raise E01DurationWorkerReceiptError("external_file_changed") from None
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
        raise E01DurationWorkerReceiptError("external_file_changed")
    return snapshot.content, snapshot


def _worker_environment(
    workspace_root: Path,
    config: Mapping[str, Any],
) -> dict[str, str]:
    execution = cast(Mapping[str, Any], config["execution_policy"])
    runtime = cast(Mapping[str, Any], config["runtime_pins"])
    thread_count = str(execution["native_thread_count"])
    return {
        "__PYVENV_LAUNCHER__": str(
            workspace_root / cast(str, runtime["venv_python_relative_path"])
        ),
        "CUDA_VISIBLE_DEVICES": "",
        "HIP_VISIBLE_DEVICES": "",
        "ROCR_VISIBLE_DEVICES": "",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": cast(str, execution["python_hash_seed"]),
        "OPENBLAS_NUM_THREADS": thread_count,
        "OMP_NUM_THREADS": thread_count,
        "MKL_NUM_THREADS": thread_count,
        "NUMEXPR_NUM_THREADS": thread_count,
        "TZ": "UTC",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


def _open_worker_for_execution(
    workspace_root: Path,
    expected_sha256: str,
) -> tuple[int, _FileSnapshot]:
    snapshot = read_workspace_file(
        workspace_root,
        WORKER_RELATIVE_PATH,
        maximum_bytes=MAXIMUM_SOURCE_BYTES,
    )
    if snapshot.sha256 != expected_sha256:
        raise E01DurationWorkerReceiptError("worker_source_sha256_mismatch")
    worker_fd = _sealed_memfd_from_bytes(
        "voice-coach-e01-duration-worker",
        snapshot.content,
        executable=False,
    )
    return worker_fd, snapshot


def _run_worker(
    workspace_root: Path,
    config: Mapping[str, Any],
    config_bytes: bytes,
    reviewed_wheel_path: Path,
) -> dict[str, Any]:
    runtime = cast(Mapping[str, Any], config["runtime_pins"])
    execution = cast(Mapping[str, Any], config["execution_policy"])
    if (
        not isinstance(reviewed_wheel_path, Path)
        or not reviewed_wheel_path.is_absolute()
        or reviewed_wheel_path.name != runtime["reviewed_wheel_filename"]
    ):
        raise E01DurationWorkerReceiptError("reviewed_wheel_path_contract")
    _launcher, python_fd, _python_snapshot = _verify_python_launcher(
        workspace_root,
        runtime,
    )
    try:
        seccomp_guard = _PreparedSeccompGuard.prepare(runtime)
    except Exception:
        os.close(python_fd)
        raise
    worker_sha = cast(Mapping[str, str], config["source_sha256"])[
        WORKER_RELATIVE_PATH.as_posix()
    ]
    try:
        worker_fd, worker_snapshot = _open_worker_for_execution(
            workspace_root,
            worker_sha,
        )
    except Exception:
        seccomp_guard.close()
        os.close(python_fd)
        raise
    seccomp_provenance = seccomp_guard.provenance()
    try:
        command = [
            f"/proc/self/fd/{python_fd}",
            f"/proc/self/fd/{worker_fd}",
            "--workspace-root",
            str(workspace_root),
            "--reviewed-wheel",
            str(reviewed_wheel_path),
        ]
        try:
            completed = subprocess.run(
                command,
                input=config_bytes,
                capture_output=True,
                check=False,
                env=_worker_environment(workspace_root, config),
                pass_fds=(python_fd, worker_fd),
                preexec_fn=seccomp_guard.install_in_child,
                timeout=cast(int, execution["worker_timeout_seconds"]),
            )
        except (OSError, subprocess.TimeoutExpired):
            raise E01DurationWorkerReceiptError("worker_process_failure") from None
    finally:
        os.close(worker_fd)
        os.close(python_fd)
        seccomp_guard.close()
    if (
        completed.returncode != 0
        or completed.stderr
        or not completed.stdout
        or len(completed.stdout) > MAXIMUM_WORKER_STDOUT_BYTES
    ):
        raise E01DurationWorkerReceiptError("worker_execution_failure")
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise E01DurationWorkerReceiptError("worker_output_contract") from None
    worker = _exact_keys(
        payload,
        expected={
            "schema_version",
            "status",
            "experiment_id",
            "source_provenance",
            "runtime_build_provenance",
            "execution",
            "synthetic_input_aggregate",
            "transform_aggregate",
            "publication",
            "completion_limits",
        },
        field_name="worker_output",
    )
    _exact_value(
        worker["schema_version"],
        WORKER_SCHEMA_VERSION,
        field_name="worker_schema_version",
    )
    _exact_value(worker["status"], "completed", field_name="worker_status")
    _exact_value(
        worker["experiment_id"],
        EXPERIMENT_ID,
        field_name="worker_experiment_id",
    )
    worker_execution = cast(Mapping[str, Any], worker["execution"])
    if (
        worker_execution.get("worker_process_count") != 1
        or worker_execution.get("job_count") != 1
        or worker_execution.get("python_network_intercept_count") != 0
        or worker_execution.get("kernel_network_guard_probe_block_count") != 1
        or worker_execution.get("kernel_seccomp_mode") != 2
        or worker_execution.get("kernel_network_guard_policy")
        != KERNEL_NETWORK_GUARD_POLICY
    ):
        raise E01DurationWorkerReceiptError("worker_execution_contract")
    if worker_snapshot.sha256 != worker_sha:
        raise E01DurationWorkerReceiptError("worker_source_sha256_mismatch")
    runtime_provenance = cast(dict[str, Any], worker["runtime_build_provenance"])
    runtime_provenance["kernel_network_guard"] = seccomp_provenance
    execution_provenance = cast(dict[str, Any], worker["execution"])
    execution_provenance["verified_executable_materialization_policy"] = (
        VERIFIED_EXECUTABLE_MATERIALIZATION_POLICY
    )
    execution_provenance["verified_python_sha256"] = runtime["base_python_sha256"]
    execution_provenance["verified_worker_sha256"] = worker_sha
    return worker


class _ImmutableReceiptStore:
    """Descriptor-relative, no-overwrite content-addressed receipt store."""

    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = _validated_workspace_root(workspace_root)
        try:
            root_information = self._workspace_root.lstat()
        except OSError:
            raise E01DurationWorkerReceiptError("workspace_root_unavailable") from None
        self._root_device = root_information.st_dev
        self._root_inode = root_information.st_ino

    def _validate_root_identity(self, information: os.stat_result) -> None:
        if (
            not stat.S_ISDIR(information.st_mode)
            or information.st_dev != self._root_device
            or information.st_ino != self._root_inode
        ):
            raise E01DurationWorkerReceiptError("workspace_root_changed")

    @staticmethod
    def _open_or_create_directory(
        parent_fd: int,
        name: str,
        *,
        private_mode_required: bool,
    ) -> int:
        try:
            os.mkdir(name, DIRECTORY_MODE, dir_fd=parent_fd)
        except FileExistsError:
            pass
        except OSError:
            raise E01DurationWorkerReceiptError("receipt_directory_create") from None
        descriptor = _open_directory_at(parent_fd, name)
        information = os.fstat(descriptor)
        observed_mode = stat.S_IMODE(information.st_mode)
        if (
            private_mode_required
            and observed_mode != DIRECTORY_MODE
            or not private_mode_required
            and observed_mode & 0o002
        ):
            os.close(descriptor)
            raise E01DurationWorkerReceiptError("receipt_directory_mode")
        return descriptor

    @staticmethod
    def _validate_existing_receipt(
        generation_fd: int,
        expected_bytes: bytes,
    ) -> None:
        try:
            descriptor = os.open(
                "receipt.json",
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=generation_fd,
            )
        except OSError:
            raise E01DurationWorkerReceiptError("receipt_existing_invalid") from None
        try:
            snapshot = _read_regular_descriptor(
                descriptor,
                maximum_bytes=MAXIMUM_WORKER_STDOUT_BYTES,
            )
        finally:
            os.close(descriptor)
        if snapshot.mode != RECEIPT_MODE or snapshot.content != expected_bytes:
            raise E01DurationWorkerReceiptError("receipt_existing_invalid")

    def publish(self, receipt_bytes: bytes) -> tuple[str, str, bool]:
        """Publish once, or validate and reuse the byte-identical receipt."""

        receipt_sha256 = _sha256(receipt_bytes)
        root_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        opened: list[int] = []
        try:
            directory_fd = os.open(self._workspace_root, root_flags)
            opened.append(directory_fd)
            self._validate_root_identity(os.fstat(directory_fd))
            for component_index, component in enumerate(OUTPUT_RELATIVE_ROOT.parts):
                directory_fd = self._open_or_create_directory(
                    directory_fd,
                    component,
                    private_mode_required=component_index >= 2,
                )
                opened.append(directory_fd)
            generation_fd = self._open_or_create_directory(
                directory_fd,
                receipt_sha256,
                private_mode_required=True,
            )
            opened.append(generation_fd)
            try:
                self._validate_existing_receipt(generation_fd, receipt_bytes)
                created = False
            except E01DurationWorkerReceiptError as error:
                if str(error) != "receipt_existing_invalid":
                    raise
                temporary_name = f".receipt.{receipt_sha256}.staging"
                temporary_fd: int | None = None
                try:
                    temporary_fd = os.open(
                        temporary_name,
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        RECEIPT_MODE,
                        dir_fd=generation_fd,
                    )
                    offset = 0
                    while offset < len(receipt_bytes):
                        offset += os.write(temporary_fd, receipt_bytes[offset:])
                    os.fsync(temporary_fd)
                    os.close(temporary_fd)
                    temporary_fd = None
                    try:
                        os.link(
                            temporary_name,
                            "receipt.json",
                            src_dir_fd=generation_fd,
                            dst_dir_fd=generation_fd,
                            follow_symlinks=False,
                        )
                        created = True
                    except FileExistsError:
                        created = False
                    os.unlink(temporary_name, dir_fd=generation_fd)
                    os.fsync(generation_fd)
                    self._validate_existing_receipt(generation_fd, receipt_bytes)
                except FileExistsError:
                    raise E01DurationWorkerReceiptError(
                        "receipt_staging_collision"
                    ) from None
                except OSError:
                    raise E01DurationWorkerReceiptError(
                        "receipt_atomic_publish"
                    ) from None
                finally:
                    if temporary_fd is not None:
                        os.close(temporary_fd)
                    try:
                        os.unlink(temporary_name, dir_fd=generation_fd)
                    except OSError:
                        pass
            relative_path = (
                OUTPUT_RELATIVE_ROOT / receipt_sha256 / "receipt.json"
            ).as_posix()
            try:
                current_root_information = self._workspace_root.lstat()
            except OSError:
                raise E01DurationWorkerReceiptError("workspace_root_changed") from None
            self._validate_root_identity(current_root_information)
            return receipt_sha256, relative_path, created
        except OSError:
            raise E01DurationWorkerReceiptError("receipt_store_failure") from None
        finally:
            for descriptor in reversed(opened):
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def build_receipt(
    config: Mapping[str, Any],
    *,
    config_sha256: str,
    source_sha256: Mapping[str, str],
    worker: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the stable, privacy-minimal synthetic technical receipt."""

    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "status": "completed_synthetic_technical_gate",
        "experiment_id": EXPERIMENT_ID,
        "evidence_class": "synthetic_duration_worker_technical_only",
        "lineage": {
            "config_sha256": config_sha256,
            "source_sha256": dict(sorted(source_sha256.items())),
        },
        "source_load_provenance": worker["source_provenance"],
        "runtime_build_provenance": worker["runtime_build_provenance"],
        "execution": worker["execution"],
        "synthetic_input_aggregate": worker["synthetic_input_aggregate"],
        "transform_aggregate": worker["transform_aggregate"],
        "publication": worker["publication"],
        "completion_limits": config["completion_limits"],
    }


def run_receipt(
    *,
    workspace_root: Path,
    config_bytes: bytes,
    reviewed_wheel_path: Path,
) -> dict[str, Any]:
    """Execute one synthetic job and publish or reuse its immutable receipt."""

    config = validate_config(config_bytes)
    root = _validated_workspace_root(workspace_root)
    # Capture the publication root identity before any source read or worker
    # execution so one device/inode lineage spans the whole receipt run.
    receipt_store = _ImmutableReceiptStore(root)
    source_sha256 = verify_source_pins(root, config)
    worker = _run_worker(root, config, config_bytes, reviewed_wheel_path)
    if worker["completion_limits"] != config["completion_limits"]:
        raise E01DurationWorkerReceiptError("worker_completion_limits_contract")
    receipt = build_receipt(
        config,
        config_sha256=_sha256(config_bytes),
        source_sha256=source_sha256,
        worker=worker,
    )
    receipt_bytes = canonical_json_bytes(receipt)
    receipt_sha256, relative_path, created = receipt_store.publish(receipt_bytes)
    return {
        "status": "completed",
        "receipt_sha256": receipt_sha256,
        "receipt_relative_path": relative_path,
        "receipt_created": created,
        "idempotent_reuse": not created,
        "paper_ready": False,
        "production_ready": False,
    }


__all__ = [
    "APPLICATION_RELATIVE_PATH",
    "CONFIG_RELATIVE_PATH",
    "E01DurationWorkerReceiptError",
    "EXPERIMENT_ID",
    "MAXIMUM_CONFIG_BYTES",
    "PRIMITIVE_RELATIVE_PATH",
    "RUNNER_RELATIVE_PATH",
    "SOURCE_PATHS",
    "WORKER_RELATIVE_PATH",
    "_ImmutableReceiptStore",
    "build_receipt",
    "canonical_json_bytes",
    "read_workspace_file",
    "run_receipt",
    "validate_config",
    "verify_source_pins",
]
