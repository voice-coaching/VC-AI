"""Fail-closed rollover for the interrupted canonical S104 attempt.

The default operation is read-only.  Mutation is available only through
``prepare_s104_interrupted_rollover`` and never launches training.  The
rollover moves the stale registry directory and the single empty ``.part``
directory into a content-addressed incident archive using no-replace atomic
renames on one filesystem, then publishes a deterministic incident receipt.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import ctypes
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from typing import Any, cast
import uuid

import yaml  # type: ignore[import-untyped]


AUDIT_SCHEMA_VERSION = "voice_coach.s104_interrupted_attempt_audit.v1"
TRANSACTION_SCHEMA_VERSION = "voice_coach.s104_interrupted_attempt_transaction.v1"
RECEIPT_SCHEMA_VERSION = "voice_coach.s104_interrupted_attempt_receipt.v1"
COMMIT_SCHEMA_VERSION = "voice_coach.s104_interrupted_attempt_commit.v1"

EXPERIMENT_ID = "aihub71490_wavlm_s104_adapter_v1"
REGISTRY_RUN_ID = "aihub71490-wavlm-s104-adapter-v1-dev"
CONFIG_RELATIVE = "configs/experiments/wavlm_s104_adapter_v1.yaml"
EXECUTION_LOCK_RELATIVE = (
    "artifacts/experiments/aihub71490_wavlm_s104_adapter_v1/execution_lock.json"
)
COMMIT_RELATIVE = "artifacts/experiments/aihub71490_wavlm_s104_adapter_v1/commit.json"
REGISTRY_RUN_RELATIVE = (
    "artifacts/experiment_registry/aihub71490-wavlm-s104-adapter-v1-dev"
)
GENERATION_ROOT_RELATIVE = (
    "artifacts/experiments/aihub71490_wavlm_s104_adapter_v1/generations"
)
EMPTY_PART_NAME = ".part-1ee97821e55d4fa283dea77182283c8e"
ARCHIVE_ROOT_RELATIVE = (
    "artifacts/interrupted_attempts/aihub71490_wavlm_s104_adapter_v1"
)
S103_COMMIT_RELATIVE = (
    "artifacts/experiments/"
    "aihub71490_wavlm_frozen_head_comparison_v1/commit.json"
)
TRAINER_RELATIVE = "scripts/train_wavlm_s104_adapter.py"

EXPECTED_CONFIG_SHA256 = (
    "aa9a19fa1bf2b706d2c692498055795284364d2cfab13774fddb2ad77e1ba8e9"
)
EXPECTED_EXECUTION_LOCK_SHA256 = (
    "8d8778061ec8e366208618071a806e2b04c78e22612ffdabdd6a46f047d274ca"
)
EXPECTED_REGISTRY_METADATA_SHA256 = (
    "ff7fac6b7bb9104fda8c540794d5eec45c32de7f7d3458c27e9e392f6b56b7f2"
)
EXPECTED_S103_COMMIT_SHA256 = (
    "e45cf869ab54beff6bcf5a3c674c0bdff0d75460ab8daabe50c7477afca16c6e"
)
EXPECTED_CONFIG_SIZE = 9646
EXPECTED_EXECUTION_LOCK_SIZE = 9114
EXPECTED_REGISTRY_METADATA_SIZE = 6265
EXPECTED_S103_COMMIT_SIZE = 988
EXPECTED_PID = 386918

_SOURCE_DIRECTORY_MODE = 0o775
_SOURCE_FILE_MODE = 0o664
_PRIVATE_FILE_MODE = 0o600
_MANAGED_DIRECTORY_MODE = 0o750
_IMMUTABLE_FILE_MODE = 0o440
_LOCK_FILE_MODE = 0o600
_SHA256_RE_LENGTH = 64
_RENAME_NOREPLACE = 1


class S104InterruptedRolloverError(ValueError):
    """Raised when rollover evidence or state fails closed."""


@dataclass(frozen=True)
class RolloverContract:
    """Pinned identity of the interrupted attempt.

    The private ``contract`` arguments on public functions exist solely for
    isolated tests.  Production callers use ``CANONICAL_CONTRACT``.
    """

    config_sha256: str = EXPECTED_CONFIG_SHA256
    config_size: int = EXPECTED_CONFIG_SIZE
    execution_lock_sha256: str = EXPECTED_EXECUTION_LOCK_SHA256
    execution_lock_size: int = EXPECTED_EXECUTION_LOCK_SIZE
    registry_metadata_sha256: str = EXPECTED_REGISTRY_METADATA_SHA256
    registry_metadata_size: int = EXPECTED_REGISTRY_METADATA_SIZE
    s103_commit_sha256: str = EXPECTED_S103_COMMIT_SHA256
    s103_commit_size: int = EXPECTED_S103_COMMIT_SIZE
    expected_pid: int = EXPECTED_PID
    empty_part_name: str = EMPTY_PART_NAME


CANONICAL_CONTRACT = RolloverContract()


@dataclass(frozen=True)
class IncidentEvidence:
    """Operator-supplied observations; none attributes a signal source."""

    observed_exit_code: int
    last_alive_at: str
    first_missing_at: str
    session_id: int
    signal_source: str
    last_rchar: int
    last_wchar: int
    last_phase: str


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Return deterministic UTF-8 JSON without accepting non-finite values."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _reject_constant(value: str) -> None:
    raise S104InterruptedRolloverError(
        f"non-finite JSON constant is forbidden: {value}"
    )


def _unique_mapping(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise S104InterruptedRolloverError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def _load_json_bytes(raw: bytes, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_mapping,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise S104InterruptedRolloverError(f"{name} is not strict JSON") from error
    if not isinstance(value, Mapping):
        raise S104InterruptedRolloverError(f"{name} must be a JSON object")
    return cast(Mapping[str, Any], value)


def _parse_timestamp(value: str, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise S104InterruptedRolloverError(f"{name} must be a non-empty timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise S104InterruptedRolloverError(f"{name} is not ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise S104InterruptedRolloverError(f"{name} must include an offset")
    return parsed


def _validate_evidence(evidence: IncidentEvidence) -> Mapping[str, Any]:
    if isinstance(evidence.observed_exit_code, bool) or evidence.observed_exit_code != 143:
        raise S104InterruptedRolloverError("observed exit code must be exactly 143")
    if isinstance(evidence.session_id, bool) or evidence.session_id != 91182:
        raise S104InterruptedRolloverError("session id must be exactly 91182")
    if evidence.signal_source != "unknown":
        raise S104InterruptedRolloverError("signal source must be exactly unknown")
    if (
        isinstance(evidence.last_rchar, bool)
        or evidence.last_rchar != 471_736_121_708
        or isinstance(evidence.last_wchar, bool)
        or evidence.last_wchar != 114_034
    ):
        raise S104InterruptedRolloverError("last process I/O counters differ")
    if evidence.last_phase != "seed42_epoch9_fit":
        raise S104InterruptedRolloverError("last observed phase differs")
    last_alive = _parse_timestamp(evidence.last_alive_at, "last_alive_at")
    first_missing = _parse_timestamp(evidence.first_missing_at, "first_missing_at")
    if last_alive >= first_missing:
        raise S104InterruptedRolloverError(
            "last_alive_at must precede first_missing_at"
        )
    if (first_missing - last_alive).total_seconds() != 30:
        raise S104InterruptedRolloverError(
            "last-alive/first-missing window must be exactly 30 seconds"
        )
    return {
        "observed_facts": {
            "session_process_exit_code": 143,
            "last_alive_at": evidence.last_alive_at,
            "first_missing_at": evidence.first_missing_at,
            "observation_window_seconds": 30,
            "session_id": 91182,
            "signal_source": "unknown",
            "last_process_io": {
                "rchar": 471_736_121_708,
                "wchar": 114_034,
            },
            "last_observed_phase": "seed42_epoch9_fit",
        },
        "interpretation": {
            "exit_code_143_conventionally_consistent_with_sigterm": True,
            "signal_source_attributed": False,
            "signal_source": "unknown",
            "cause_determined": False,
        },
    }


def _workspace_lexical(workspace_root: Path) -> Path:
    root = Path(os.path.abspath(workspace_root))
    if root == Path(root.anchor):
        raise S104InterruptedRolloverError("workspace root is too broad")
    return root


def _relative_path(root: Path, relative: str, name: str) -> Path:
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts:
        raise S104InterruptedRolloverError(f"{name} is not a canonical relative path")
    path = Path(os.path.abspath(root / raw))
    try:
        path.relative_to(root)
    except ValueError as error:
        raise S104InterruptedRolloverError(f"{name} escapes workspace") from error
    return path


def _stat_dict(result: os.stat_result) -> dict[str, int]:
    return {
        "device": int(result.st_dev),
        "inode": int(result.st_ino),
        "mode": stat.S_IMODE(result.st_mode),
        "nlink": int(result.st_nlink),
        "size_bytes": int(result.st_size),
        "mtime_ns": int(result.st_mtime_ns),
        "ctime_ns": int(result.st_ctime_ns),
    }


def _stable_stat_identity(result: os.stat_result) -> tuple[int, ...]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_mode,
        result.st_nlink,
        result.st_size,
        result.st_mtime_ns,
        result.st_ctime_ns,
    )


def _assert_real_directory(
    path: Path,
    *,
    name: str,
    expected_mode: int | None = None,
    expected_nlink: int | None = None,
) -> os.stat_result:
    try:
        result = os.lstat(path)
    except OSError as error:
        raise S104InterruptedRolloverError(f"{name} is unavailable") from error
    if stat.S_ISLNK(result.st_mode) or not stat.S_ISDIR(result.st_mode):
        raise S104InterruptedRolloverError(f"{name} must be a real directory")
    if expected_mode is not None and stat.S_IMODE(result.st_mode) != expected_mode:
        raise S104InterruptedRolloverError(f"{name} mode differs")
    if expected_nlink is not None and result.st_nlink != expected_nlink:
        raise S104InterruptedRolloverError(f"{name} link count differs")
    return result


def _assert_parent_chain(
    root: Path,
    path: Path,
    *,
    name: str,
    leaf_may_be_symlink: bool = False,
    allowed_parent_modes: frozenset[int] = frozenset({_SOURCE_DIRECTORY_MODE}),
) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise S104InterruptedRolloverError(f"{name} escapes workspace") from error
    _assert_real_directory(
        root,
        name="workspace root",
        expected_mode=_SOURCE_DIRECTORY_MODE,
    )
    current = root
    for index, part in enumerate(relative.parts):
        current /= part
        try:
            result = os.lstat(current)
        except OSError as error:
            raise S104InterruptedRolloverError(f"{name} is unavailable") from error
        is_leaf = index == len(relative.parts) - 1
        if stat.S_ISLNK(result.st_mode) and not (is_leaf and leaf_may_be_symlink):
            raise S104InterruptedRolloverError(f"{name} parent chain contains a symlink")
        if not is_leaf and not stat.S_ISDIR(result.st_mode):
            raise S104InterruptedRolloverError(f"{name} parent is not a directory")
        if not is_leaf and stat.S_IMODE(result.st_mode) not in allowed_parent_modes:
            raise S104InterruptedRolloverError(f"{name} parent mode differs")


def _read_regular_file(
    root: Path,
    relative: str,
    *,
    name: str,
    expected_mode: int,
    expected_sha256: str | None = None,
    expected_size: int | None = None,
    allowed_parent_modes: frozenset[int] = frozenset({_SOURCE_DIRECTORY_MODE}),
) -> tuple[Mapping[str, Any], bytes]:
    path = _relative_path(root, relative, name)
    _assert_parent_chain(
        root,
        path,
        name=name,
        allowed_parent_modes=allowed_parent_modes,
    )
    before = os.lstat(path)
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != expected_mode
    ):
        raise S104InterruptedRolloverError(f"{name} file security differs")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise S104InterruptedRolloverError(f"{name} cannot be opened securely") from error
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise S104InterruptedRolloverError(f"{name} changed before secure read")
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            chunks.append(chunk)
        after_fd = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after_path = os.lstat(path)
    if (
        _stable_stat_identity(before) != _stable_stat_identity(after_fd)
        or _stable_stat_identity(after_fd) != _stable_stat_identity(after_path)
    ):
        raise S104InterruptedRolloverError(f"{name} changed while reading")
    actual_sha = digest.hexdigest()
    if expected_sha256 is not None and actual_sha != expected_sha256:
        raise S104InterruptedRolloverError(f"{name} SHA-256 differs")
    if expected_size is not None and after_fd.st_size != expected_size:
        raise S104InterruptedRolloverError(f"{name} size differs")
    return (
        {
            "path": relative,
            "sha256": actual_sha,
            **_stat_dict(after_fd),
        },
        b"".join(chunks),
    )


def _read_model_symlink(
    root: Path,
    relative: str,
    *,
    repository_relative: str,
    name: str,
    expected_sha256: str,
    expected_size: int,
) -> Mapping[str, Any]:
    path = _relative_path(root, relative, name)
    repository = _relative_path(root, repository_relative, f"{name} repository")
    _assert_parent_chain(root, path, name=name, leaf_may_be_symlink=True)
    link_stat = os.lstat(path)
    if not stat.S_ISLNK(link_stat.st_mode):
        raise S104InterruptedRolloverError(f"{name} snapshot leaf must be a symlink")
    try:
        target = path.resolve(strict=True)
        target.relative_to(repository)
    except (OSError, ValueError) as error:
        raise S104InterruptedRolloverError(
            f"{name} symlink target escapes model repository"
        ) from error
    _assert_parent_chain(root, target, name=f"{name} target")
    target_relative = target.relative_to(root).as_posix()
    evidence, _ = _read_regular_file(
        root,
        target_relative,
        name=f"{name} target",
        expected_mode=_SOURCE_FILE_MODE,
        expected_sha256=expected_sha256,
        expected_size=expected_size,
    )
    if os.lstat(path) != link_stat:
        raise S104InterruptedRolloverError(f"{name} symlink changed while reading")
    return {
        **dict(evidence),
        "path": relative,
        "symlink_target": target_relative,
        "symlink_mode": stat.S_IMODE(link_stat.st_mode),
    }


def _load_yaml(raw: bytes, name: str) -> Mapping[str, Any]:
    try:
        value = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as error:
        raise S104InterruptedRolloverError(f"{name} is not valid YAML") from error
    if not isinstance(value, Mapping):
        raise S104InterruptedRolloverError(f"{name} must be a mapping")
    return cast(Mapping[str, Any], value)


def _require_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise S104InterruptedRolloverError(f"{name} must be a mapping")
    return cast(Mapping[str, Any], value)


def _require_exact_path(value: object, expected: str, name: str) -> None:
    if value != expected:
        raise S104InterruptedRolloverError(f"{name} path differs")


def _audit_pins(root: Path, contract: RolloverContract) -> Mapping[str, Any]:
    config_evidence, config_raw = _read_regular_file(
        root,
        CONFIG_RELATIVE,
        name="S104 config",
        expected_mode=_SOURCE_FILE_MODE,
        expected_sha256=contract.config_sha256,
        expected_size=contract.config_size,
    )
    config = _load_yaml(config_raw, "S104 config")
    experiment = _require_mapping(config.get("experiment"), "experiment")
    if (
        experiment.get("id") != EXPERIMENT_ID
        or experiment.get("stage") != "s104_adapter"
        or experiment.get("development_only") is not True
        or experiment.get("promotion_decision_allowed") is not False
    ):
        raise S104InterruptedRolloverError("S104 experiment contract differs")
    outputs = _require_mapping(config.get("outputs"), "outputs")
    _require_exact_path(outputs.get("execution_lock"), EXECUTION_LOCK_RELATIVE, "lock")
    _require_exact_path(outputs.get("commit_manifest"), COMMIT_RELATIVE, "commit")
    _require_exact_path(
        outputs.get("generation_root"), GENERATION_ROOT_RELATIVE, "generation"
    )
    if (
        outputs.get("registry_root") != "artifacts/experiment_registry"
        or outputs.get("planned_registry_run_id") != REGISTRY_RUN_ID
    ):
        raise S104InterruptedRolloverError("registry output contract differs")

    lock_evidence, lock_raw = _read_regular_file(
        root,
        EXECUTION_LOCK_RELATIVE,
        name="S104 execution lock",
        expected_mode=_PRIVATE_FILE_MODE,
        expected_sha256=contract.execution_lock_sha256,
        expected_size=contract.execution_lock_size,
    )
    execution_lock = _load_json_bytes(lock_raw, "S104 execution lock")
    if (
        execution_lock.get("schema_version")
        != "voice_coach.wavlm_s104_execution_lock.v1"
        or execution_lock.get("status") != "ready_for_s104_gpu_training"
        or execution_lock.get("experiment_id") != EXPERIMENT_ID
        or execution_lock.get("config_sha256") != contract.config_sha256
    ):
        raise S104InterruptedRolloverError("execution-lock identity differs")

    configured_implementation = _require_mapping(
        config.get("implementation"), "implementation"
    )
    locked_implementation = _require_mapping(
        execution_lock.get("implementation"), "locked implementation"
    )
    if set(configured_implementation) != set(locked_implementation) or len(
        configured_implementation
    ) != 14:
        raise S104InterruptedRolloverError("implementation role set differs")
    implementation: list[Mapping[str, Any]] = []
    for role in sorted(configured_implementation):
        configured = _require_mapping(
            configured_implementation[role], f"implementation {role}"
        )
        locked = _require_mapping(
            locked_implementation.get(role), f"locked implementation {role}"
        )
        path = configured.get("path")
        if not isinstance(path, str):
            raise S104InterruptedRolloverError(
                f"implementation path differs: {role}"
            )
        sha256 = configured.get("sha256")
        if not isinstance(sha256, str) or sha256 != locked.get("sha256"):
            raise S104InterruptedRolloverError(
                f"implementation binding differs: {role}"
            )
        size = locked.get("size_bytes")
        if isinstance(size, bool) or not isinstance(size, int):
            raise S104InterruptedRolloverError(
                f"implementation size differs: {role}"
            )
        record, _ = _read_regular_file(
            root,
            path,
            name=f"implementation {role}",
            expected_mode=_SOURCE_FILE_MODE,
            expected_sha256=sha256,
            expected_size=size,
        )
        implementation.append({"role": role, **dict(record)})

    data = _require_mapping(config.get("data"), "data")
    data_hashes = _require_mapping(
        _require_mapping(execution_lock.get("data_contract"), "data contract").get(
            "source_hashes"
        ),
        "data source hashes",
    )
    data_roles = {
        "modeling_manifest": data.get("modeling_manifest"),
        "prompt_challenge_reservation": data.get("prompt_challenge_reservation"),
        "explicit_utterance_labels": data.get("explicit_utterance_labels"),
    }
    inputs: list[Mapping[str, Any]] = []
    for role, path in data_roles.items():
        if not isinstance(path, str):
            raise S104InterruptedRolloverError(f"data path differs: {role}")
        expected = data_hashes.get(role)
        if not isinstance(expected, str):
            raise S104InterruptedRolloverError(f"data hash differs: {role}")
        record, _ = _read_regular_file(
            root,
            path,
            name=f"data {role}",
            expected_mode=_SOURCE_FILE_MODE,
            expected_sha256=expected,
        )
        inputs.append({"role": role, **dict(record)})

    selected = _require_mapping(
        execution_lock.get("selected_candidate"), "selected candidate"
    )
    candidates = _require_mapping(config.get("candidates"), "candidates")
    base_plus = _require_mapping(candidates.get("base_plus"), "base_plus candidate")
    if (
        selected.get("variant") != "base_plus"
        or selected.get("model_id") != "microsoft/wavlm-base-plus"
        or selected.get("revision") != base_plus.get("revision")
    ):
        raise S104InterruptedRolloverError("selected model identity differs")
    revision = str(selected["revision"])
    repository_relative = (
        "data/cache/huggingface/hub/models--microsoft--wavlm-base-plus"
    )
    snapshot_relative = f"{repository_relative}/snapshots/{revision}"
    model_specs = (
        (
            "model_config",
            f"{snapshot_relative}/config.json",
            selected.get("source_config_sha256"),
            2232,
        ),
        (
            "model_checkpoint",
            f"{snapshot_relative}/pytorch_model.bin",
            selected.get("source_checkpoint_sha256"),
            selected.get("local_checkpoint_size_bytes"),
        ),
    )
    model: list[Mapping[str, Any]] = []
    for role, path, expected_sha, expected_size in model_specs:
        if (
            not isinstance(expected_sha, str)
            or isinstance(expected_size, bool)
            or not isinstance(expected_size, int)
        ):
            raise S104InterruptedRolloverError(f"model binding differs: {role}")
        record = _read_model_symlink(
            root,
            path,
            repository_relative=repository_relative,
            name=role,
            expected_sha256=expected_sha,
            expected_size=expected_size,
        )
        model.append({"role": role, **dict(record)})

    s103_evidence, s103_raw = _read_regular_file(
        root,
        S103_COMMIT_RELATIVE,
        name="S103 commit",
        expected_mode=_SOURCE_FILE_MODE,
        expected_sha256=contract.s103_commit_sha256,
        expected_size=contract.s103_commit_size,
    )
    s103 = _load_json_bytes(s103_raw, "S103 commit")
    lineage = _require_mapping(
        execution_lock.get("s103_branch_evidence"), "S103 lineage"
    )
    if (
        s103.get("status") != "committed"
        or lineage.get("commit_sha256") != contract.s103_commit_sha256
    ):
        raise S104InterruptedRolloverError("S103 commit binding differs")
    s103_artifacts: list[Mapping[str, Any]] = []
    for key, lineage_key in (
        ("comparison_report", "comparison_report_sha256"),
        ("execution_receipt", "execution_receipt_sha256"),
    ):
        source = _require_mapping(s103.get(key), f"S103 {key}")
        path = source.get("path")
        sha256 = source.get("sha256")
        size = source.get("size_bytes")
        if (
            not isinstance(path, str)
            or not isinstance(sha256, str)
            or isinstance(size, bool)
            or not isinstance(size, int)
            or lineage.get(lineage_key) != sha256
        ):
            raise S104InterruptedRolloverError(f"S103 {key} binding differs")
        record, _ = _read_regular_file(
            root,
            path,
            name=f"S103 {key}",
            expected_mode=_SOURCE_FILE_MODE,
            expected_sha256=sha256,
            expected_size=size,
        )
        s103_artifacts.append({"role": key, **dict(record)})

    return {
        "config": config_evidence,
        "execution_lock": lock_evidence,
        "implementation": implementation,
        "inputs": inputs,
        "model": model,
        "s103": {
            "commit": s103_evidence,
            "artifacts": s103_artifacts,
        },
    }


def _scan_live_trainers(
    *,
    root: Path,
    contract: RolloverContract,
) -> list[Mapping[str, Any]]:
    trainer = str(root / TRAINER_RELATIVE)
    config = str(root / CONFIG_RELATIVE)
    matches: list[Mapping[str, Any]] = []
    proc_root = Path("/proc")
    try:
        entries = list(proc_root.iterdir())
    except OSError as error:
        raise S104InterruptedRolloverError("cannot inspect process table") from error
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            raw = (entry / "cmdline").read_bytes()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        args = [
            item.decode("utf-8", errors="replace")
            for item in raw.split(b"\0")
            if item
        ]
        exact_trainer = any(
            argument == TRAINER_RELATIVE or argument == trainer for argument in args
        )
        exact_config = any(
            argument == CONFIG_RELATIVE or argument == config for argument in args
        )
        if pid == contract.expected_pid or (exact_trainer and exact_config):
            matches.append(
                {
                    "pid": pid,
                    "expected_pid": pid == contract.expected_pid,
                    "canonical_command": exact_trainer and exact_config,
                }
            )
    return sorted(matches, key=lambda item: int(item["pid"]))


def _gpu_process_probe(
    *,
    root: Path,
    contract: RolloverContract,
) -> Mapping[str, Any]:
    command = [
        "nvidia-smi",
        "--query-compute-apps=pid,process_name",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return {"available": False, "matching_pids": []}
    matching: list[int] = []
    for line in completed.stdout.splitlines():
        first, _, _ = line.partition(",")
        try:
            pid = int(first.strip())
        except ValueError:
            continue
        if pid == contract.expected_pid:
            matching.append(pid)
            continue
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            continue
        args = {
            item.decode("utf-8", errors="replace")
            for item in raw.split(b"\0")
            if item
        }
        if (
            TRAINER_RELATIVE in args or str(root / TRAINER_RELATIVE) in args
        ) and (CONFIG_RELATIVE in args or str(root / CONFIG_RELATIVE) in args):
            matching.append(pid)
    return {
        "available": True,
        "command_exit_code": completed.returncode,
        "matching_pids": sorted(set(matching)),
    }


def _audit_live_absence(
    *,
    root: Path,
    contract: RolloverContract,
    process_probe: Callable[..., list[Mapping[str, Any]]],
    gpu_probe: Callable[..., Mapping[str, Any]],
) -> Mapping[str, Any]:
    process_matches = process_probe(root=root, contract=contract)
    gpu = gpu_probe(root=root, contract=contract)
    gpu_matches = gpu.get("matching_pids")
    if not isinstance(gpu_matches, list):
        raise S104InterruptedRolloverError("GPU process probe result differs")
    if process_matches or gpu_matches:
        raise S104InterruptedRolloverError("an active canonical S104 trainer exists")
    return {
        "expected_pid_absent": True,
        "canonical_command_absent": True,
        "gpu_probe": dict(gpu),
    }


def _audit_commit_absent(root: Path) -> None:
    commit_path = _relative_path(root, COMMIT_RELATIVE, "S104 commit")
    _assert_parent_chain(
        root,
        commit_path.parent,
        name="S104 commit parent",
    )
    try:
        os.lstat(commit_path)
    except FileNotFoundError:
        return
    except OSError as error:
        raise S104InterruptedRolloverError("S104 commit cannot be inspected") from error
    raise S104InterruptedRolloverError("S104 commit already exists")


def _audit_stale_sources(
    root: Path,
    contract: RolloverContract,
) -> Mapping[str, Any]:
    registry_path = _relative_path(root, REGISTRY_RUN_RELATIVE, "stale registry")
    _assert_parent_chain(root, registry_path, name="stale registry")
    registry_stat = _assert_real_directory(
        registry_path,
        name="stale registry",
        expected_mode=_SOURCE_DIRECTORY_MODE,
        expected_nlink=2,
    )
    metadata_relative = f"{REGISTRY_RUN_RELATIVE}/metadata.json"
    metadata, metadata_raw = _read_regular_file(
        root,
        metadata_relative,
        name="stale registry metadata",
        expected_mode=_PRIVATE_FILE_MODE,
        expected_sha256=contract.registry_metadata_sha256,
        expected_size=contract.registry_metadata_size,
    )
    try:
        registry_entries = sorted(entry.name for entry in registry_path.iterdir())
    except OSError as error:
        raise S104InterruptedRolloverError("stale registry is unreadable") from error
    if registry_entries != ["metadata.json"]:
        raise S104InterruptedRolloverError("stale registry tree differs")
    registry = _load_json_bytes(metadata_raw, "stale registry metadata")
    timestamps = _require_mapping(registry.get("timestamps"), "registry timestamps")
    metrics = registry.get("metrics")
    artifacts = registry.get("artifacts")
    if (
        registry.get("run_id") != REGISTRY_RUN_ID
        or registry.get("status") != "running"
        or timestamps.get("ended") is not None
        or registry.get("failure") is not None
        or not isinstance(metrics, list)
        or len(metrics) != 0
        or not isinstance(artifacts, list)
        or len(artifacts) != 0
    ):
        raise S104InterruptedRolloverError("stale registry state differs")

    generation_root = _relative_path(
        root, GENERATION_ROOT_RELATIVE, "generation root"
    )
    _assert_parent_chain(root, generation_root, name="generation root")
    _assert_real_directory(
        generation_root,
        name="generation root",
        expected_mode=_SOURCE_DIRECTORY_MODE,
    )
    try:
        part_entries = sorted(
            entry.name
            for entry in generation_root.iterdir()
            if entry.name.startswith(".part-")
        )
    except OSError as error:
        raise S104InterruptedRolloverError("generation root is unreadable") from error
    if part_entries != [contract.empty_part_name]:
        raise S104InterruptedRolloverError(
            "generation root must contain exactly the pinned empty .part directory"
        )
    part_path = generation_root / contract.empty_part_name
    part_stat = _assert_real_directory(
        part_path,
        name="empty .part directory",
        expected_mode=_SOURCE_DIRECTORY_MODE,
        expected_nlink=2,
    )
    try:
        part_children = list(part_path.iterdir())
    except OSError as error:
        raise S104InterruptedRolloverError("empty .part is unreadable") from error
    if part_children:
        raise S104InterruptedRolloverError("pinned .part directory is not empty")
    return {
        "registry": {
            "path": REGISTRY_RUN_RELATIVE,
            "directory_stat": _stat_dict(registry_stat),
            "metadata": metadata,
            "state": {
                "status": "running",
                "ended": None,
                "failure": None,
                "metrics_count": 0,
                "artifacts_count": 0,
            },
        },
        "empty_part": {
            "path": f"{GENERATION_ROOT_RELATIVE}/{contract.empty_part_name}",
            "directory_stat": _stat_dict(part_stat),
            "child_count": 0,
        },
    }


def _incident_identity(
    *,
    evidence: Mapping[str, Any],
    pins: Mapping[str, Any],
    stale: Mapping[str, Any],
) -> Mapping[str, Any]:
    payload = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "registry_run_id": REGISTRY_RUN_ID,
        "evidence": evidence,
        "pin_identity": {
            "config_sha256": cast(Mapping[str, Any], pins["config"])["sha256"],
            "execution_lock_sha256": cast(
                Mapping[str, Any], pins["execution_lock"]
            )["sha256"],
            "registry_metadata_sha256": cast(
                Mapping[str, Any],
                cast(Mapping[str, Any], stale["registry"])["metadata"],
            )["sha256"],
        },
        "source_evidence": stale,
    }
    return {**payload, "incident_id": _sha256_bytes(canonical_json_bytes(payload))}


def audit_s104_interrupted_attempt(
    *,
    workspace_root: Path,
    evidence: IncidentEvidence,
    config_only: bool = False,
    contract: RolloverContract = CANONICAL_CONTRACT,
    process_probe: Callable[..., list[Mapping[str, Any]]] = _scan_live_trainers,
    gpu_probe: Callable[..., Mapping[str, Any]] = _gpu_process_probe,
) -> Mapping[str, Any]:
    """Audit the interrupted attempt without creating or mutating any path."""

    root = _workspace_lexical(workspace_root)
    evidence_record = _validate_evidence(evidence)
    if config_only:
        return {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "status": "config_valid",
            "mode": "config_only",
            "mutated": False,
            "workspace_root": str(root),
            "canonical_paths": {
                "config": CONFIG_RELATIVE,
                "execution_lock": EXECUTION_LOCK_RELATIVE,
                "commit": COMMIT_RELATIVE,
                "registry_run": REGISTRY_RUN_RELATIVE,
                "generation_root": GENERATION_ROOT_RELATIVE,
                "empty_part_name": contract.empty_part_name,
                "archive_root": ARCHIVE_ROOT_RELATIVE,
            },
            "evidence": evidence_record,
        }
    pins = _audit_pins(root, contract)
    live = _audit_live_absence(
        root=root,
        contract=contract,
        process_probe=process_probe,
        gpu_probe=gpu_probe,
    )
    _audit_commit_absent(root)
    stale = _audit_stale_sources(root, contract)
    identity = _incident_identity(evidence=evidence_record, pins=pins, stale=stale)
    return {
        **identity,
        "status": "ready_for_rollover",
        "mode": "read_only_audit",
        "mutated": False,
        "live_process_audit": live,
        "pins": pins,
    }


def _mkdir_managed(path: Path) -> bool:
    try:
        os.mkdir(path, _MANAGED_DIRECTORY_MODE)
        return True
    except FileExistsError:
        result = os.lstat(path)
        if (
            stat.S_ISLNK(result.st_mode)
            or not stat.S_ISDIR(result.st_mode)
            or stat.S_IMODE(result.st_mode) != _MANAGED_DIRECTORY_MODE
        ):
            raise S104InterruptedRolloverError(
                "managed archive directory security differs"
            ) from None
        return False


def _ensure_archive_directories(root: Path) -> list[Path]:
    artifacts = _relative_path(root, "artifacts", "artifacts")
    _assert_real_directory(
        artifacts,
        name="artifacts",
        expected_mode=_SOURCE_DIRECTORY_MODE,
    )
    created: list[Path] = []
    current = artifacts
    archive_parts = Path(ARCHIVE_ROOT_RELATIVE).relative_to("artifacts").parts
    for part in archive_parts:
        current /= part
        if _mkdir_managed(current):
            created.append(current)
    for relative in ("transactions", "generations", "receipts"):
        path = current / relative
        if _mkdir_managed(path):
            created.append(path)
    receipts = current / "receipts"
    for relative in ("generations",):
        path = receipts / relative
        if _mkdir_managed(path):
            created.append(path)
    return created


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rename_noreplace(
    source_parent_fd: int,
    source_name: str,
    target_parent_fd: int,
    target_name: str,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise S104InterruptedRolloverError("renameat2 is unavailable")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        source_parent_fd,
        os.fsencode(source_name),
        target_parent_fd,
        os.fsencode(target_name),
        _RENAME_NOREPLACE,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def _atomic_create_bytes(path: Path, payload: bytes) -> bool:
    """Create an immutable file atomically, or validate identical prior bytes."""

    parent = path.parent
    _assert_real_directory(
        parent,
        name="managed file parent",
        expected_mode=_MANAGED_DIRECTORY_MODE,
    )
    temporary = parent / f".tmp-{uuid.uuid4().hex}"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(temporary, flags, _PRIVATE_FILE_MODE)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError(errno.EIO, "short write")
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, _IMMUTABLE_FILE_MODE)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    source_parent_fd = os.open(
        parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        try:
            _rename_noreplace(
                source_parent_fd,
                temporary.name,
                source_parent_fd,
                path.name,
            )
            os.fsync(source_parent_fd)
            return True
        except OSError as error:
            if error.errno != errno.EEXIST:
                raise
            existing, raw = _read_managed_file(path)
            del existing
            if raw != payload:
                raise S104InterruptedRolloverError(
                    "existing create-only file content differs"
                ) from error
            return False
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        os.fsync(source_parent_fd)
        os.close(source_parent_fd)


def _read_managed_file(path: Path) -> tuple[Mapping[str, Any], bytes]:
    before = os.lstat(path)
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != _IMMUTABLE_FILE_MODE
    ):
        raise S104InterruptedRolloverError("managed file security differs")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise S104InterruptedRolloverError("managed file changed before read")
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    final = os.lstat(path)
    if (
        _stable_stat_identity(before) != _stable_stat_identity(after)
        or _stable_stat_identity(after) != _stable_stat_identity(final)
    ):
        raise S104InterruptedRolloverError("managed file changed while reading")
    return (
        {
            "sha256": digest.hexdigest(),
            **_stat_dict(after),
        },
        b"".join(chunks),
    )


def _open_lock(archive_root: Path) -> int:
    lock_path = archive_root / ".prepare.lock"
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(lock_path, flags, _LOCK_FILE_MODE)
    result = os.fstat(descriptor)
    if (
        not stat.S_ISREG(result.st_mode)
        or result.st_nlink != 1
        or stat.S_IMODE(result.st_mode) != _LOCK_FILE_MODE
    ):
        os.close(descriptor)
        raise S104InterruptedRolloverError("rollover lock security differs")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        os.close(descriptor)
        raise S104InterruptedRolloverError(
            "another rollover writer holds the lock"
        ) from error
    _fsync_directory(archive_root)
    return descriptor


def _transaction_payload(
    audit: Mapping[str, Any],
    *,
    prepared_at: str,
) -> Mapping[str, Any]:
    incident_id = str(audit["incident_id"])
    generation_relative = (
        f"{ARCHIVE_ROOT_RELATIVE}/generations/{incident_id}"
    )
    unsigned = {
        "schema_version": TRANSACTION_SCHEMA_VERSION,
        "incident_id": incident_id,
        "experiment_id": EXPERIMENT_ID,
        "registry_run_id": REGISTRY_RUN_ID,
        "prepared_at": prepared_at,
        "evidence": audit["evidence"],
        "pins": audit["pins"],
        "source_evidence": audit["source_evidence"],
        "moves": {
            "registry": {
                "source": REGISTRY_RUN_RELATIVE,
                "archive": f"{generation_relative}/registry_run",
            },
            "empty_part": {
                "source": (
                    f"{GENERATION_ROOT_RELATIVE}/"
                    f"{cast(Mapping[str, Any], audit['source_evidence'])['empty_part']['path'].split('/')[-1]}"
                ),
                "archive": f"{generation_relative}/empty_part",
            },
        },
    }
    return {
        **unsigned,
        "binding_sha256": _sha256_bytes(canonical_json_bytes(unsigned)),
    }


def _validate_transaction(
    transaction: Mapping[str, Any],
    *,
    evidence_record: Mapping[str, Any],
) -> None:
    expected_keys = {
        "schema_version",
        "incident_id",
        "experiment_id",
        "registry_run_id",
        "prepared_at",
        "evidence",
        "pins",
        "source_evidence",
        "moves",
        "binding_sha256",
    }
    if set(transaction) != expected_keys:
        raise S104InterruptedRolloverError("transaction keys differ")
    unsigned = dict(transaction)
    binding = unsigned.pop("binding_sha256")
    if (
        transaction.get("schema_version") != TRANSACTION_SCHEMA_VERSION
        or transaction.get("experiment_id") != EXPERIMENT_ID
        or transaction.get("registry_run_id") != REGISTRY_RUN_ID
        or transaction.get("evidence") != evidence_record
        or not isinstance(binding, str)
        or binding != _sha256_bytes(canonical_json_bytes(unsigned))
    ):
        raise S104InterruptedRolloverError("transaction binding differs")
    _parse_timestamp(str(transaction.get("prepared_at")), "transaction prepared_at")


def _load_transaction_if_any(
    archive_root: Path,
    *,
    evidence_record: Mapping[str, Any],
) -> tuple[Path, Mapping[str, Any]] | None:
    transaction_root = archive_root / "transactions"
    try:
        entries = sorted(transaction_root.iterdir())
    except FileNotFoundError:
        return None
    except OSError as error:
        raise S104InterruptedRolloverError("transaction namespace unavailable") from error
    visible = [path for path in entries if not path.name.startswith(".tmp-")]
    if not visible:
        return None
    if len(visible) != 1:
        raise S104InterruptedRolloverError("transaction namespace is ambiguous")
    path = visible[0]
    if path.suffix != ".json":
        raise S104InterruptedRolloverError("transaction filename differs")
    _, raw = _read_managed_file(path)
    transaction = _load_json_bytes(raw, "rollover transaction")
    _validate_transaction(transaction, evidence_record=evidence_record)
    if path.name != f"{transaction.get('incident_id')}.json":
        raise S104InterruptedRolloverError(
            "transaction is not in its content-addressed path"
        )
    return path, transaction


def _entry_state(path: Path) -> str:
    try:
        result = os.lstat(path)
    except FileNotFoundError:
        return "absent"
    if stat.S_ISLNK(result.st_mode):
        return "symlink"
    if stat.S_ISDIR(result.st_mode):
        return "directory"
    return "other"


def _verify_archived_source(
    *,
    root: Path,
    archive_path: Path,
    source_record: Mapping[str, Any],
    kind: str,
) -> Mapping[str, Any]:
    result = _assert_real_directory(
        archive_path,
        name=f"archived {kind}",
        expected_mode=_SOURCE_DIRECTORY_MODE,
        expected_nlink=2,
    )
    if kind == "registry":
        metadata_record = cast(Mapping[str, Any], source_record["metadata"])
        relative = (
            f"{archive_path.relative_to(root).as_posix()}/metadata.json"
        )
        metadata, _ = _read_regular_file(
            root,
            relative,
            name="archived registry metadata",
            expected_mode=_PRIVATE_FILE_MODE,
            expected_sha256=str(metadata_record["sha256"]),
            expected_size=int(metadata_record["size_bytes"]),
            allowed_parent_modes=frozenset(
                {_SOURCE_DIRECTORY_MODE, _MANAGED_DIRECTORY_MODE}
            ),
        )
        for field in ("device", "inode", "mode", "nlink", "size_bytes", "mtime_ns", "ctime_ns"):
            if metadata.get(field) != metadata_record.get(field):
                raise S104InterruptedRolloverError(
                    "archived registry metadata stat differs"
                )
        entries = sorted(path.name for path in archive_path.iterdir())
        if entries != ["metadata.json"]:
            raise S104InterruptedRolloverError("archived registry tree differs")
        directory_before = cast(Mapping[str, Any], source_record["directory_stat"])
        for field in ("device", "inode", "mode", "nlink", "size_bytes", "mtime_ns"):
            if _stat_dict(result).get(field) != directory_before.get(field):
                raise S104InterruptedRolloverError(
                    "archived registry directory identity differs"
                )
        return {
            "path": archive_path.relative_to(root).as_posix(),
            "directory_stat": _stat_dict(result),
            "metadata": metadata,
        }
    if list(archive_path.iterdir()):
        raise S104InterruptedRolloverError("archived .part directory is not empty")
    directory_before = cast(Mapping[str, Any], source_record["directory_stat"])
    for field in ("device", "inode", "mode", "nlink", "size_bytes", "mtime_ns"):
        if _stat_dict(result).get(field) != directory_before.get(field):
            raise S104InterruptedRolloverError(
                "archived .part directory identity differs"
            )
    return {
        "path": archive_path.relative_to(root).as_posix(),
        "directory_stat": _stat_dict(result),
        "child_count": 0,
    }


def _open_directory(path: Path, name: str) -> tuple[int, os.stat_result]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise S104InterruptedRolloverError(f"{name} cannot be opened") from error
    result = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(result.st_mode)
        or stat.S_IMODE(result.st_mode)
        not in {_SOURCE_DIRECTORY_MODE, _MANAGED_DIRECTORY_MODE}
    ):
        os.close(descriptor)
        raise S104InterruptedRolloverError(f"{name} directory security differs")
    return descriptor, result


def _rename_path_noreplace(source: Path, target: Path) -> None:
    source_fd, source_parent_stat = _open_directory(source.parent, "source parent")
    target_fd, target_parent_stat = _open_directory(target.parent, "target parent")
    try:
        if source_parent_stat.st_dev != target_parent_stat.st_dev:
            raise S104InterruptedRolloverError(
                "rollover rename parents are on different devices"
            )
        source_before = os.stat(source.name, dir_fd=source_fd, follow_symlinks=False)
        if not stat.S_ISDIR(source_before.st_mode):
            raise S104InterruptedRolloverError("rollover source is not a directory")
        try:
            os.stat(target.name, dir_fd=target_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise S104InterruptedRolloverError("rollover target already exists")
        _rename_noreplace(source_fd, source.name, target_fd, target.name)
        os.fsync(source_fd)
        os.fsync(target_fd)
        target_after = os.stat(target.name, dir_fd=target_fd, follow_symlinks=False)
        if (target_after.st_dev, target_after.st_ino) != (
            source_before.st_dev,
            source_before.st_ino,
        ):
            raise S104InterruptedRolloverError("atomic rename identity differs")
    finally:
        os.close(source_fd)
        os.close(target_fd)


def _move_or_validate(
    *,
    source: Path,
    archive: Path,
) -> bool:
    source_state = _entry_state(source)
    archive_state = _entry_state(archive)
    if source_state == "directory" and archive_state == "absent":
        _rename_path_noreplace(source, archive)
        return True
    if source_state == "absent" and archive_state == "directory":
        return False
    if source_state == "directory" and archive_state == "directory":
        raise S104InterruptedRolloverError(
            "both canonical and archived rollover entries exist"
        )
    raise S104InterruptedRolloverError("rollover entry state differs")


def _rollback_move(archive: Path, source: Path) -> None:
    archive_state = _entry_state(archive)
    source_state = _entry_state(source)
    if archive_state == "directory" and source_state == "absent":
        _rename_path_noreplace(archive, source)
        return
    if archive_state == "absent" and source_state == "directory":
        return
    raise S104InterruptedRolloverError("rollover rollback state conflicts")


def _postflight_sources_absent(
    *,
    root: Path,
    contract: RolloverContract,
    execution_lock_before: Mapping[str, Any],
) -> None:
    if _entry_state(_relative_path(root, REGISTRY_RUN_RELATIVE, "registry")) != "absent":
        raise S104InterruptedRolloverError("canonical stale registry remains")
    generation_root = _relative_path(root, GENERATION_ROOT_RELATIVE, "generation root")
    part_entries = sorted(
        path.name for path in generation_root.iterdir() if path.name.startswith(".part-")
    )
    if part_entries:
        raise S104InterruptedRolloverError("canonical stale .part remains")
    lock, _ = _read_regular_file(
        root,
        EXECUTION_LOCK_RELATIVE,
        name="S104 execution lock postflight",
        expected_mode=_PRIVATE_FILE_MODE,
        expected_sha256=str(execution_lock_before["sha256"]),
        expected_size=int(execution_lock_before["size_bytes"]),
    )
    for field in ("device", "inode", "mode", "nlink", "size_bytes", "mtime_ns", "ctime_ns"):
        if lock.get(field) != execution_lock_before.get(field):
            raise S104InterruptedRolloverError(
                "immutable execution lock stat changed"
            )


def _receipt_payload(
    *,
    transaction: Mapping[str, Any],
    archive_evidence: Mapping[str, Any],
) -> Mapping[str, Any]:
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "status": "archived_interrupted_attempt",
        "incident_id": transaction["incident_id"],
        "experiment_id": EXPERIMENT_ID,
        "registry_run_id": REGISTRY_RUN_ID,
        "prepared_at": transaction["prepared_at"],
        "evidence": transaction["evidence"],
        "facts_and_inference_policy": {
            "observed_facts_separate_from_interpretation": True,
            "signal_source": "unknown",
            "cause_determined": False,
        },
        "pins": transaction["pins"],
        "original_source_evidence": transaction["source_evidence"],
        "archive_evidence": archive_evidence,
        "postflight": {
            "canonical_stale_registry_absent": True,
            "canonical_empty_part_absent": True,
            "execution_lock_unchanged": True,
            "training_started": False,
        },
    }


def _publish_receipt(
    *,
    root: Path,
    archive_root: Path,
    transaction: Mapping[str, Any],
    archive_evidence: Mapping[str, Any],
) -> Mapping[str, Any]:
    receipt = _receipt_payload(
        transaction=transaction,
        archive_evidence=archive_evidence,
    )
    receipt_bytes = canonical_json_bytes(receipt)
    receipt_sha = _sha256_bytes(receipt_bytes)
    receipt_generation = archive_root / "receipts" / "generations" / receipt_sha
    _mkdir_managed(receipt_generation)
    receipt_path = receipt_generation / "receipt.json"
    _atomic_create_bytes(receipt_path, receipt_bytes)
    unsigned_commit = {
        "schema_version": COMMIT_SCHEMA_VERSION,
        "status": "committed",
        "incident_id": transaction["incident_id"],
        "receipt_path": receipt_path.relative_to(root).as_posix(),
        "receipt_sha256": receipt_sha,
    }
    commit = {
        **unsigned_commit,
        "binding_sha256": _sha256_bytes(canonical_json_bytes(unsigned_commit)),
    }
    commit_path = archive_root / "receipts" / "commit.json"
    _atomic_create_bytes(commit_path, canonical_json_bytes(commit))
    return {
        "receipt": receipt,
        "receipt_sha256": receipt_sha,
        "receipt_path": receipt_path.relative_to(root).as_posix(),
        "commit_path": commit_path.relative_to(root).as_posix(),
        "commit_sha256": _sha256_bytes(canonical_json_bytes(commit)),
    }


def _validate_receipt_commit(
    *,
    root: Path,
    archive_root: Path,
    transaction: Mapping[str, Any],
    contract: RolloverContract,
) -> Mapping[str, Any]:
    commit_path = archive_root / "receipts" / "commit.json"
    commit_evidence, commit_raw = _read_managed_file(commit_path)
    commit = _load_json_bytes(commit_raw, "rollover receipt commit")
    expected_keys = {
        "schema_version",
        "status",
        "incident_id",
        "receipt_path",
        "receipt_sha256",
        "binding_sha256",
    }
    if set(commit) != expected_keys:
        raise S104InterruptedRolloverError("receipt commit keys differ")
    unsigned = dict(commit)
    binding = unsigned.pop("binding_sha256")
    receipt_sha = commit.get("receipt_sha256")
    expected_receipt_relative = (
        f"{ARCHIVE_ROOT_RELATIVE}/receipts/generations/"
        f"{receipt_sha}/receipt.json"
    )
    if (
        commit.get("schema_version") != COMMIT_SCHEMA_VERSION
        or commit.get("status") != "committed"
        or commit.get("incident_id") != transaction.get("incident_id")
        or commit.get("receipt_path") != expected_receipt_relative
        or not isinstance(receipt_sha, str)
        or len(receipt_sha) != _SHA256_RE_LENGTH
        or binding != _sha256_bytes(canonical_json_bytes(unsigned))
    ):
        raise S104InterruptedRolloverError("receipt commit binding differs")
    receipt_path = _relative_path(root, expected_receipt_relative, "receipt")
    receipt_evidence, receipt_raw = _read_managed_file(receipt_path)
    if _sha256_bytes(receipt_raw) != receipt_sha:
        raise S104InterruptedRolloverError("receipt hash differs")
    receipt = _load_json_bytes(receipt_raw, "rollover receipt")
    moves = _require_mapping(transaction.get("moves"), "transaction moves")
    registry_move = _require_mapping(moves.get("registry"), "registry move")
    part_move = _require_mapping(moves.get("empty_part"), "part move")
    source_evidence = _require_mapping(
        transaction.get("source_evidence"), "source evidence"
    )
    archive_evidence = {
        "registry": _verify_archived_source(
            root=root,
            archive_path=_relative_path(
                root, str(registry_move["archive"]), "archived registry"
            ),
            source_record=_require_mapping(
                source_evidence.get("registry"), "registry source evidence"
            ),
            kind="registry",
        ),
        "empty_part": _verify_archived_source(
            root=root,
            archive_path=_relative_path(
                root, str(part_move["archive"]), "archived part"
            ),
            source_record=_require_mapping(
                source_evidence.get("empty_part"), "part source evidence"
            ),
            kind="empty_part",
        ),
    }
    expected_receipt = _receipt_payload(
        transaction=transaction,
        archive_evidence=archive_evidence,
    )
    if receipt != expected_receipt:
        raise S104InterruptedRolloverError("receipt live evidence differs")
    _postflight_sources_absent(
        root=root,
        contract=contract,
        execution_lock_before=_require_mapping(
            _require_mapping(transaction.get("pins"), "transaction pins").get(
                "execution_lock"
            ),
            "transaction execution lock",
        ),
    )
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "status": "valid_archived_interrupted_attempt",
        "mode": "validate_receipt",
        "mutated": False,
        "incident_id": transaction["incident_id"],
        "receipt_sha256": receipt_sha,
        "receipt_path": expected_receipt_relative,
        "receipt_commit_sha256": commit_evidence["sha256"],
        "training_started": False,
    }


def _current_prepared_at(now: datetime | None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise S104InterruptedRolloverError("prepared_at clock must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def prepare_s104_interrupted_rollover(
    *,
    workspace_root: Path,
    evidence: IncidentEvidence,
    contract: RolloverContract = CANONICAL_CONTRACT,
    process_probe: Callable[..., list[Mapping[str, Any]]] = _scan_live_trainers,
    gpu_probe: Callable[..., Mapping[str, Any]] = _gpu_process_probe,
    now: datetime | None = None,
    fault_injector: Callable[[str], None] | None = None,
) -> Mapping[str, Any]:
    """Create-only archive the interrupted attempt; never launch a new run."""

    root = _workspace_lexical(workspace_root)
    evidence_record = _validate_evidence(evidence)
    _assert_real_directory(
        root,
        name="workspace root",
        expected_mode=_SOURCE_DIRECTORY_MODE,
    )
    _ensure_archive_directories(root)
    archive_root = _relative_path(root, ARCHIVE_ROOT_RELATIVE, "archive root")
    lock_descriptor = _open_lock(archive_root)
    moved: list[tuple[Path, Path]] = []
    commit_visible = False
    try:
        transaction_entry = _load_transaction_if_any(
            archive_root,
            evidence_record=evidence_record,
        )
        commit_path = archive_root / "receipts" / "commit.json"
        if transaction_entry is not None and _entry_state(commit_path) != "absent":
            _, transaction = transaction_entry
            pins = _audit_pins(root, contract)
            if pins != transaction.get("pins"):
                raise S104InterruptedRolloverError(
                    "current pins differ from archived transaction"
                )
            return _validate_receipt_commit(
                root=root,
                archive_root=archive_root,
                transaction=transaction,
                contract=contract,
            )

        if transaction_entry is None:
            audit = audit_s104_interrupted_attempt(
                workspace_root=root,
                evidence=evidence,
                contract=contract,
                process_probe=process_probe,
                gpu_probe=gpu_probe,
            )
            transaction = _transaction_payload(
                audit,
                prepared_at=_current_prepared_at(now),
            )
            incident_id = str(transaction["incident_id"])
            transaction_path = archive_root / "transactions" / f"{incident_id}.json"
            _atomic_create_bytes(transaction_path, canonical_json_bytes(transaction))
        else:
            _, transaction = transaction_entry
            _audit_live_absence(
                root=root,
                contract=contract,
                process_probe=process_probe,
                gpu_probe=gpu_probe,
            )
            pins = _audit_pins(root, contract)
            if pins != transaction.get("pins"):
                raise S104InterruptedRolloverError(
                    "current pins differ from rollover transaction"
                )
            _audit_commit_absent(root)

        moves = _require_mapping(transaction.get("moves"), "transaction moves")
        registry_move = _require_mapping(moves.get("registry"), "registry move")
        part_move = _require_mapping(moves.get("empty_part"), "part move")
        generation = (
            archive_root / "generations" / str(transaction["incident_id"])
        )
        _mkdir_managed(generation)
        registry_source = _relative_path(
            root, str(registry_move["source"]), "registry source"
        )
        registry_archive = _relative_path(
            root, str(registry_move["archive"]), "registry archive"
        )
        part_source = _relative_path(root, str(part_move["source"]), "part source")
        part_archive = _relative_path(root, str(part_move["archive"]), "part archive")

        if _move_or_validate(source=registry_source, archive=registry_archive):
            moved.append((registry_archive, registry_source))
        if fault_injector is not None:
            fault_injector("after_registry_move")
        if _move_or_validate(source=part_source, archive=part_archive):
            moved.append((part_archive, part_source))
        if fault_injector is not None:
            fault_injector("after_part_move")

        source_evidence = _require_mapping(
            transaction.get("source_evidence"), "source evidence"
        )
        archive_evidence = {
            "registry": _verify_archived_source(
                root=root,
                archive_path=registry_archive,
                source_record=_require_mapping(
                    source_evidence.get("registry"), "registry source evidence"
                ),
                kind="registry",
            ),
            "empty_part": _verify_archived_source(
                root=root,
                archive_path=part_archive,
                source_record=_require_mapping(
                    source_evidence.get("empty_part"), "part source evidence"
                ),
                kind="empty_part",
            ),
        }
        _postflight_sources_absent(
            root=root,
            contract=contract,
            execution_lock_before=_require_mapping(
                _require_mapping(transaction.get("pins"), "transaction pins").get(
                    "execution_lock"
                ),
                "transaction execution lock",
            ),
        )
        if fault_injector is not None:
            fault_injector("before_receipt_publish")
        published = _publish_receipt(
            root=root,
            archive_root=archive_root,
            transaction=transaction,
            archive_evidence=archive_evidence,
        )
        commit_visible = True
        if fault_injector is not None:
            fault_injector("after_receipt_publish")
        validated = _validate_receipt_commit(
            root=root,
            archive_root=archive_root,
            transaction=transaction,
            contract=contract,
        )
        return {
            **dict(validated),
            "status": "prepared",
            "mode": "prepare",
            "mutated": True,
            "receipt_commit_sha256": published["commit_sha256"],
        }
    except Exception:
        if not commit_visible:
            rollback_errors: list[Exception] = []
            for archive, source in reversed(moved):
                try:
                    _rollback_move(archive, source)
                except Exception as rollback_error:  # pragma: no cover - catastrophic
                    rollback_errors.append(rollback_error)
            if rollback_errors:
                raise S104InterruptedRolloverError(
                    "rollover failed and rollback could not restore all sources"
                ) from rollback_errors[0]
        raise
    finally:
        fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        os.close(lock_descriptor)


def validate_s104_interrupted_rollover(
    *,
    workspace_root: Path,
    evidence: IncidentEvidence,
    contract: RolloverContract = CANONICAL_CONTRACT,
) -> Mapping[str, Any]:
    """Read-only validate an already committed rollover receipt."""

    root = _workspace_lexical(workspace_root)
    evidence_record = _validate_evidence(evidence)
    archive_root = _relative_path(root, ARCHIVE_ROOT_RELATIVE, "archive root")
    _assert_real_directory(
        archive_root,
        name="archive root",
        expected_mode=_MANAGED_DIRECTORY_MODE,
    )
    transaction_entry = _load_transaction_if_any(
        archive_root,
        evidence_record=evidence_record,
    )
    if transaction_entry is None:
        raise S104InterruptedRolloverError("rollover transaction is absent")
    _, transaction = transaction_entry
    pins = _audit_pins(root, contract)
    if pins != transaction.get("pins"):
        raise S104InterruptedRolloverError(
            "current pins differ from archived transaction"
        )
    return _validate_receipt_commit(
        root=root,
        archive_root=archive_root,
        transaction=transaction,
        contract=contract,
    )
