"""Atomic, secret-conscious experiment-run metadata registry.

The registry intentionally records a small allowlist of reproducibility
metadata.  It never reads or serializes environment variables, and refuses to
fingerprint ``.env`` files.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime
import hashlib
from importlib import metadata as importlib_metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from types import TracebackType
from typing import Any, Literal, Self
from uuid import uuid4
from zoneinfo import ZoneInfo

TRACKED_FILE_ROLES = ("config", "data", "model", "checkpoint")
DEFAULT_PACKAGE_NAMES = (
    "fastapi",
    "numpy",
    "pydantic",
    "pyarrow",
    "scikit-learn",
    "scipy",
    "soundfile",
    "torch",
    "transformers",
    "voice-coach",
)

_KST = ZoneInfo("Asia/Seoul")
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FAILURE_STAGE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SENSITIVE_NAME_PATTERN = re.compile(
    r"(?:api[-_]?key|access[-_]?token|auth(?:orization)?|credential|"
    r"passwd|password|secret|token)",
    flags=re.IGNORECASE,
)
_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_HIGH_ENTROPY_ARGUMENT_PATTERN = re.compile(r"^[A-Za-z0-9+/=_-]{32,}$")


def _is_env_path(path: Path) -> bool:
    return any(
        part.casefold() == ".env" or part.casefold().startswith(".env.")
        for part in path.parts
    )


def _validated_file(path: str | Path) -> Path:
    candidate = Path(path)
    if _is_env_path(candidate) or _is_env_path(candidate.resolve(strict=False)):
        raise ValueError(".env files must never be tracked by the run registry")
    if not candidate.is_file():
        raise FileNotFoundError(f"tracked path is not a regular file: {candidate}")
    return candidate


def _sha256_unchecked(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_file(
    path: str | Path,
    *,
    relative_to: str | Path | None = None,
) -> dict[str, str | int]:
    """Return path, byte size, and SHA256 for a stable regular file.

    The before/after stat check prevents a concurrently modified file from
    receiving a misleading digest.  ``.env`` files are rejected, including
    when reached through a symlink.
    """

    candidate = _validated_file(path)
    before = candidate.stat()
    digest = _sha256_unchecked(candidate)
    after = candidate.stat()
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if identity_before != identity_after:
        raise RuntimeError(f"tracked file changed while hashing: {candidate}")

    resolved = candidate.resolve()
    recorded_path: Path = resolved
    if relative_to is not None:
        try:
            recorded_path = resolved.relative_to(Path(relative_to).resolve())
        except ValueError:
            pass
    return {
        "path": recorded_path.as_posix(),
        "size_bytes": after.st_size,
        "sha256": digest,
    }


def _looks_like_env_argument(argument: str) -> bool:
    normalized = argument.replace("\\", "/")
    return any(
        part.casefold() == ".env" or part.casefold().startswith(".env.")
        for part in normalized.split("/")
    )


def sanitize_argv(argv: Sequence[str]) -> list[str]:
    """Redact common secret-bearing CLI arguments without reading the env.

    Sensitive option values, assignments, environment-file paths, UUID-shaped
    values, and opaque high-entropy values are replaced.  The original
    ``argv`` object is never retained.
    """

    sanitized: list[str] = []
    redact_next = False
    for raw_argument in argv:
        argument = str(raw_argument)
        if redact_next:
            sanitized.append("<redacted>")
            redact_next = False
            continue
        if _looks_like_env_argument(argument):
            sanitized.append("<redacted-env-file>")
            continue

        if "=" in argument:
            name, value = argument.split("=", 1)
            if _looks_like_env_argument(value):
                sanitized.append(f"{name}=<redacted-env-file>")
                continue
            if _SENSITIVE_NAME_PATTERN.search(name):
                sanitized.append(f"{name}=<redacted>")
                continue
            if _UUID_PATTERN.fullmatch(value) or (
                _HIGH_ENTROPY_ARGUMENT_PATTERN.fullmatch(value) and "/" not in value
            ):
                sanitized.append(f"{name}=<redacted>")
                continue

        option_name = argument.lstrip("-")
        if argument.startswith("-") and _SENSITIVE_NAME_PATTERN.search(option_name):
            sanitized.append(argument)
            redact_next = True
            continue
        if _UUID_PATTERN.fullmatch(argument) or (
            _HIGH_ENTROPY_ARGUMENT_PATTERN.fullmatch(argument) and "/" not in argument
        ):
            sanitized.append("<redacted>")
            continue
        sanitized.append(argument)
    return sanitized


def collect_runtime_metadata(
    package_names: Iterable[str] = DEFAULT_PACKAGE_NAMES,
) -> dict[str, Any]:
    """Collect an allowlisted Python/platform/package summary."""

    packages: dict[str, str | None] = {}
    for package_name in sorted(set(package_names)):
        try:
            packages[package_name] = importlib_metadata.version(package_name)
        except importlib_metadata.PackageNotFoundError:
            packages[package_name] = None
    return {
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "compiler": platform.python_compiler(),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "packages": packages,
    }


def _nvidia_smi_metadata() -> dict[str, Any]:
    command = [
        "nvidia-smi",
        "--query-gpu=index,name,driver_version,memory.total,compute_cap",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return {
            "available": False,
            "probe_error_type": type(exc).__name__,
            "gpus": [],
        }
    if result.returncode != 0:
        return {
            "available": False,
            "probe_error_type": "NonZeroExit",
            "gpus": [],
        }

    gpus: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        fields = [field.strip() for field in line.split(",", maxsplit=4)]
        if len(fields) != 5:
            continue
        index, name, driver_version, memory_mib, compute_capability = fields
        try:
            parsed_index: int | str = int(index)
        except ValueError:
            parsed_index = index
        try:
            parsed_memory: int | str = int(memory_mib)
        except ValueError:
            parsed_memory = memory_mib
        gpus.append(
            {
                "index": parsed_index,
                "name": name,
                "driver_version": driver_version,
                "memory_total_mib": parsed_memory,
                "compute_capability": compute_capability,
            }
        )
    return {"available": bool(gpus), "gpus": gpus}


def collect_accelerator_metadata() -> dict[str, Any]:
    """Collect CUDA runtime and GPU properties without recording the env."""

    summary: dict[str, Any] = {
        "cuda_available": False,
        "cuda_runtime_version": None,
        "torch_device_count": 0,
        "torch_devices": [],
        "nvidia_smi": _nvidia_smi_metadata(),
    }
    try:
        import torch
    except Exception as exc:  # pragma: no cover - depends on local installation
        summary["torch_probe_error_type"] = type(exc).__name__
        return summary

    summary["cuda_runtime_version"] = torch.version.cuda
    try:
        cuda_available = bool(torch.cuda.is_available())
        device_count = int(torch.cuda.device_count()) if cuda_available else 0
        devices: list[dict[str, Any]] = []
        for index in range(device_count):
            properties = torch.cuda.get_device_properties(index)
            devices.append(
                {
                    "index": index,
                    "name": properties.name,
                    "compute_capability": (f"{properties.major}.{properties.minor}"),
                    "memory_total_bytes": int(properties.total_memory),
                }
            )
        summary.update(
            {
                "cuda_available": cuda_available,
                "torch_device_count": device_count,
                "torch_devices": devices,
            }
        )
    except Exception as exc:  # pragma: no cover - hardware/driver dependent
        summary["torch_probe_error_type"] = type(exc).__name__
    return summary


def _run_git(
    repo_path: Path, arguments: Sequence[str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repo_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )


def collect_git_metadata(repo_path: str | Path = ".") -> dict[str, Any]:
    """Collect only commit identity and dirty state, never diff contents."""

    path = Path(repo_path)
    try:
        commit_result = _run_git(path, ["rev-parse", "--verify", "HEAD"])
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return {
            "available": False,
            "commit": None,
            "dirty": None,
            "probe_error_type": type(exc).__name__,
        }
    if commit_result.returncode != 0:
        return {"available": False, "commit": None, "dirty": None}

    try:
        status_result = _run_git(
            path,
            ["status", "--porcelain", "--untracked-files=normal"],
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "available": True,
            "commit": commit_result.stdout.strip(),
            "dirty": None,
            "probe_error_type": type(exc).__name__,
        }
    return {
        "available": True,
        "commit": commit_result.stdout.strip(),
        "dirty": bool(status_result.stdout) if status_result.returncode == 0 else None,
    }


def _timestamp_pair(instant: datetime | None = None) -> dict[str, str]:
    utc = instant or datetime.now(UTC)
    return {
        "utc": utc.astimezone(UTC).isoformat(),
        "kst": utc.astimezone(_KST).isoformat(),
    }


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_descriptor = os.open(path.parent, directory_flags)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary_path.unlink(missing_ok=True)


def _validate_run_id(run_id: str) -> str:
    if not _RUN_ID_PATTERN.fullmatch(run_id) or run_id in {".", ".."}:
        raise ValueError("run_id must contain only letters, digits, '.', '_', or '-'")
    return run_id


def _normalize_files(
    values: Iterable[str | Path] | str | Path,
) -> tuple[str | Path, ...]:
    if isinstance(values, (str, Path)):
        return (values,)
    return tuple(values)


def _validate_seeds(seeds: Mapping[str, int] | None) -> dict[str, int]:
    output: dict[str, int] = {}
    for name, value in sorted((seeds or {}).items()):
        if (
            not isinstance(name, str)
            or not name
            or _SENSITIVE_NAME_PATTERN.search(name)
        ):
            raise ValueError("seed names must be non-sensitive, non-empty strings")
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("seed values must be integers")
        output[name] = value
    return output


def _validate_metric_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("metric values must be finite")
        return value
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for raw_key, nested_value in value.items():
            if not isinstance(raw_key, str) or not raw_key:
                raise TypeError("metric mapping keys must be non-empty strings")
            if _SENSITIVE_NAME_PATTERN.search(raw_key):
                raise ValueError("sensitive keys are not permitted in metrics")
            output[raw_key] = _validate_metric_value(nested_value)
        return output
    if isinstance(value, (list, tuple)):
        return [_validate_metric_value(item) for item in value]
    raise TypeError(
        "metrics may contain only numeric, boolean, null, list, or mapping values"
    )


def _validate_public_label(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    if _SENSITIVE_NAME_PATTERN.search(value):
        raise ValueError(f"sensitive text is not permitted in {field_name}")
    return value


class ExperimentRun(AbstractContextManager["ExperimentRun"]):
    """One atomic metadata record managed through a context manager."""

    def __init__(
        self,
        registry_root: str | Path,
        *,
        run_id: str | None = None,
        input_files: Mapping[
            str,
            Iterable[str | Path] | str | Path,
        ]
        | None = None,
        seeds: Mapping[str, int] | None = None,
        argv: Sequence[str] | None = None,
        repo_path: str | Path = ".",
        package_names: Iterable[str] = DEFAULT_PACKAGE_NAMES,
    ) -> None:
        generated_id = (
            f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        )
        self.run_id = _validate_run_id(run_id or generated_id)
        self.registry_root = Path(registry_root)
        self.run_directory = self.registry_root / self.run_id
        self.metadata_path = self.run_directory / "metadata.json"
        self.repo_path = Path(repo_path)
        self._input_files = {
            role: _normalize_files((input_files or {}).get(role, ()))
            for role in TRACKED_FILE_ROLES
        }
        unknown_roles = set(input_files or {}) - set(TRACKED_FILE_ROLES)
        if unknown_roles:
            raise ValueError(f"unknown tracked-file roles: {sorted(unknown_roles)!r}")
        self._seeds = _validate_seeds(seeds)
        self._argv = sanitize_argv(list(sys.argv) if argv is None else argv)
        self._package_names = tuple(package_names)
        self._record: dict[str, Any] | None = None
        self._failure_context: dict[str, Any] | None = None

    @property
    def metadata(self) -> dict[str, Any]:
        """Return a detached copy of the in-memory metadata."""

        if self._record is None:
            raise RuntimeError("experiment run has not started")
        return json.loads(json.dumps(self._record, ensure_ascii=False))

    def _require_running(self) -> dict[str, Any]:
        if self._record is None or self._record["status"] != "running":
            raise RuntimeError("experiment run is not running")
        return self._record

    def _persist(self) -> None:
        if self._record is None:
            raise RuntimeError("experiment run has not started")
        self._record["timestamps"]["updated"] = _timestamp_pair()
        _atomic_write_json(self.metadata_path, self._record)

    def start(self) -> Self:
        if self._record is not None:
            raise RuntimeError("experiment run has already started")

        fingerprints: dict[str, list[dict[str, str | int]]] = {}
        for role, paths in self._input_files.items():
            fingerprints[role] = sorted(
                (fingerprint_file(path, relative_to=self.repo_path) for path in paths),
                key=lambda item: str(item["path"]),
            )

        started = _timestamp_pair()
        record = {
            "schema_version": 1,
            "run_id": self.run_id,
            "status": "running",
            "timestamps": {
                "started": started,
                "updated": started,
                "ended": None,
            },
            "seeds": self._seeds,
            "argv": self._argv,
            "inputs": fingerprints,
            "runtime": collect_runtime_metadata(self._package_names),
            "accelerator": collect_accelerator_metadata(),
            "git": collect_git_metadata(self.repo_path),
            "metrics": [],
            "artifacts": [],
            "failure": None,
        }

        self.registry_root.mkdir(parents=True, exist_ok=True)
        self.run_directory.mkdir(exist_ok=False)
        self._record = record
        try:
            self._persist()
        except BaseException:
            # The run directory was created by this call and no user work has
            # started. Remove both pre-rename and post-rename initialization
            # remnants so the fixed run ID is retryable.
            self._record = None
            if self.run_directory.exists():
                shutil.rmtree(self.run_directory)
                directory_flags = os.O_RDONLY | getattr(
                    os,
                    "O_DIRECTORY",
                    0,
                )
                descriptor = os.open(self.registry_root, directory_flags)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            raise
        return self

    def log_metrics(
        self,
        metrics: Mapping[str, Any],
        *,
        step: int | None = None,
        split: str | None = None,
    ) -> None:
        """Append finite, non-secret metric values and persist immediately."""

        record = self._require_running()
        if step is not None and (
            isinstance(step, bool) or not isinstance(step, int) or step < 0
        ):
            raise ValueError("metric step must be a non-negative integer")
        if split is not None:
            split = _validate_public_label(split, field_name="metric split")
        normalized_metrics: dict[str, Any] = {}
        for name, value in metrics.items():
            normalized_name = _validate_public_label(
                name,
                field_name="metric name",
            )
            normalized_metrics[normalized_name] = _validate_metric_value(value)
        event = {
            "timestamp": _timestamp_pair(),
            "step": step,
            "split": split,
            "values": normalized_metrics,
        }
        record["metrics"].append(event)
        self._persist()

    def add_artifact(
        self,
        name: str,
        path: str | Path,
        *,
        kind: str = "file",
    ) -> None:
        """Register an existing artifact, hashing regular files."""

        record = self._require_running()
        normalized_name = _validate_public_label(name, field_name="artifact name")
        normalized_kind = _validate_public_label(kind, field_name="artifact kind")
        if any(item["name"] == normalized_name for item in record["artifacts"]):
            raise ValueError(f"artifact name already registered: {normalized_name}")

        candidate = Path(path)
        if _is_env_path(candidate) or _is_env_path(candidate.resolve(strict=False)):
            raise ValueError(".env files must never be registered as artifacts")
        if not candidate.exists():
            raise FileNotFoundError(f"artifact does not exist: {candidate}")

        if candidate.is_file():
            artifact: dict[str, Any] = {
                "name": normalized_name,
                "kind": normalized_kind,
                **fingerprint_file(candidate, relative_to=self.repo_path),
            }
        elif candidate.is_dir():
            resolved = candidate.resolve()
            try:
                recorded_path = resolved.relative_to(self.repo_path.resolve())
            except ValueError:
                recorded_path = resolved
            artifact = {
                "name": normalized_name,
                "kind": normalized_kind,
                "path": recorded_path.as_posix(),
                "size_bytes": None,
                "sha256": None,
            }
        else:
            raise ValueError(f"unsupported artifact path type: {candidate}")
        artifact["registered_at"] = _timestamp_pair()
        record["artifacts"].append(artifact)
        self._persist()

    def set_failure_context(self, *, stage: str, oom: bool) -> None:
        """Predeclare the public failure stage and whether CUDA reported OOM.

        The values are retained only if the run fails.  Exception messages and
        tracebacks remain deliberately excluded because they can contain raw
        paths, identifiers, or credentials.
        """

        self._require_running()
        normalized_stage = _validate_public_label(
            stage,
            field_name="failure stage",
        )
        if not _FAILURE_STAGE_PATTERN.fullmatch(normalized_stage):
            raise ValueError("failure stage must be a lowercase public identifier")
        if not isinstance(oom, bool):
            raise TypeError("failure oom flag must be boolean")
        self._failure_context = {
            "stage": normalized_stage,
            "oom": oom,
        }

    def mark_post_completion_failure(
        self,
        *,
        stage: str,
        exception_type: type[BaseException],
        oom: bool,
    ) -> None:
        """Downgrade a completed run when a gated publication check fails.

        This opt-in transition is for workflows whose registry completion is
        followed by a separate visibility pointer. It never records exception
        text or a traceback.
        """

        if self._record is None or self._record["status"] != "completed":
            raise RuntimeError("experiment run is not completed")
        normalized_stage = _validate_public_label(
            stage,
            field_name="failure stage",
        )
        if not _FAILURE_STAGE_PATTERN.fullmatch(normalized_stage):
            raise ValueError("failure stage must be a lowercase public identifier")
        if not isinstance(exception_type, type) or not issubclass(
            exception_type,
            BaseException,
        ):
            raise TypeError("exception_type must be an exception class")
        if not isinstance(oom, bool):
            raise TypeError("failure oom flag must be boolean")
        self._record["status"] = "failed"
        self._record["failure"] = {
            "exception_type": (
                f"{exception_type.__module__}.{exception_type.__qualname__}"
            ),
            "stage": normalized_stage,
            "oom": oom,
        }
        self._record["timestamps"]["ended"] = _timestamp_pair()
        self._persist()

    def _finish(
        self,
        *,
        status: str,
        exception_type: type[BaseException] | None = None,
    ) -> None:
        record = self._require_running()
        record["status"] = status
        record["timestamps"]["ended"] = _timestamp_pair()
        if exception_type is not None:
            record["failure"] = {
                # Deliberately omit exception text and traceback: either can
                # contain credentials or environment values.
                "exception_type": (
                    f"{exception_type.__module__}.{exception_type.__qualname__}"
                ),
                **(self._failure_context or {}),
            }
        self._persist()

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        del exc_value, traceback
        if exc_type is None:
            self._finish(status="completed")
        else:
            self._finish(status="failed", exception_type=exc_type)
        return False


def experiment_run(
    registry_root: str | Path,
    *,
    run_id: str | None = None,
    config_files: Iterable[str | Path] | str | Path = (),
    data_files: Iterable[str | Path] | str | Path = (),
    model_files: Iterable[str | Path] | str | Path = (),
    checkpoint_files: Iterable[str | Path] | str | Path = (),
    seeds: Mapping[str, int] | None = None,
    argv: Sequence[str] | None = None,
    repo_path: str | Path = ".",
    package_names: Iterable[str] = DEFAULT_PACKAGE_NAMES,
) -> ExperimentRun:
    """Build a CLI-friendly experiment context manager.

    Example::

        with experiment_run(
            "artifacts/experiments",
            run_id="explicit-baseline-seed42",
            config_files=["configs/experiments/high_performance_gpu.yaml"],
            data_files=["data/processed/manifest.parquet"],
            seeds={"python": 42, "numpy": 42, "torch": 42},
        ) as run:
            train()
            run.log_metrics({"macro_f1": 0.61}, split="dev")
            run.add_artifact("best_checkpoint", "artifacts/model.pt")
    """

    return ExperimentRun(
        registry_root,
        run_id=run_id,
        input_files={
            "config": config_files,
            "data": data_files,
            "model": model_files,
            "checkpoint": checkpoint_files,
        },
        seeds=seeds,
        argv=argv,
        repo_path=repo_path,
        package_names=package_names,
    )
