"""Fail-closed adjudication validator for the DEV-S104-001 deviation.

This module is deliberately outside the pinned S104 implementation bundle.  It
does not create or mutate the running S104 registry, generation, or commit.  A
receipt may be published only after S104 reaches a terminal state; validation
then re-reads the live config, execution lock, source/data/model bindings, and
terminal artifacts instead of trusting values copied into the receipt.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
import errno
import hashlib
from importlib import metadata as importlib_metadata
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, cast
import uuid

import yaml  # type: ignore[import-untyped]


RECEIPT_SCHEMA_VERSION = "voice_coach.s104_protocol_deviation_adjudication.v1"
RECEIPT_COMMIT_SCHEMA_VERSION = (
    "voice_coach.s104_protocol_deviation_adjudication_commit.v1"
)
DEVIATION_ID = "DEV-S104-001"

# The deviation *event* — a single-line insertion into pyproject.toml, then its
# exact reversion — happened inside the v1 training window.  That run was later
# invalidated for an unrelated reason (DEV-S104-003: the v1 execution lock pins an
# `intervals.py` digest the working tree no longer has), and the clean rerun is
# the v2 experiment.  The two therefore cannot be collapsed: the event constants
# below describe where the event occurred, and the terminal constants describe the
# run whose committed result the adjudication actually binds.  Retargeting the
# event constants at v2 would assert a transient edit that never happened there.
EVENT_EXPERIMENT_ID = "aihub71490_wavlm_s104_adapter_v1"
EVENT_REGISTRY_RUN_ID = "aihub71490-wavlm-s104-adapter-v1-dev"
EVENT_S104_PID = 386918
EVENT_S104_CONFIG_RELATIVE = "configs/experiments/wavlm_s104_adapter_v1.yaml"
EVENT_SUPERSEDED_BY_DEVIATION_ID = "DEV-S104-003"

TERMINAL_EXPERIMENT_ID = "aihub71490_wavlm_s104_adapter_v2_current_code"
TERMINAL_REGISTRY_RUN_ID = "aihub71490-wavlm-s104-adapter-v2-current-code-dev"
S104_CONFIG_RELATIVE = (
    "configs/experiments/wavlm_s104_adapter_v2_current_code.yaml"
)
SCHEMA_RELATIVE = (
    "configs/experiments/s104_protocol_deviation_adjudication_v1.schema.json"
)
VALIDATOR_RELATIVE = "src/voice_coach/reproducibility/s104_protocol_deviation.py"
CLI_RELATIVE = "scripts/validate_s104_protocol_deviation.py"
RECEIPT_COMMIT_RELATIVE = "artifacts/protocol_deviations/dev-s104-001/commit.json"
RECEIPT_NAMESPACE_RELATIVE = "artifacts/protocol_deviations/dev-s104-001"
TARGET_RELATIVE = "pyproject.toml"
TARGET_ROLE = "project_manifest"
SCORER_LOCK_RELATIVE = "requirements/scorer-lock-linux-aarch64-cu130.txt"
SCORER_LOCK_ALLOWED_DIRECTIVES = {
    "--extra-index-url https://download.pytorch.org/whl/cu130",
}
#: The v1 config content the v1 execution lock bound at run time.  The file on
#: disk no longer hashes to this (DEV-S104-003), so the event binding is checked
#: against the immutable lock record rather than the current file, and the drift
#: is reported instead of being smoothed over.
EVENT_EXPECTED_CONFIG_SHA256 = (
    "aa9a19fa1bf2b706d2c692498055795284364d2cfab13774fddb2ad77e1ba8e9"
)
EVENT_EXPECTED_EXECUTION_LOCK_SHA256 = (
    "8d8778061ec8e366208618071a806e2b04c78e22612ffdabdd6a46f047d274ca"
)
#: The terminal run the adjudication binds: v2's committed config and lock.
EXPECTED_CONFIG_SHA256 = (
    "7a78e718954c45eb3ad03658b6da916a5c1163a51d683125daccd6c272be6434"
)
EXPECTED_EXECUTION_LOCK_SHA256 = (
    "c37ce4e3d0aa7b7e0c016c56342397b818d6316d6a0e7e22bab9867a63ab5a3e"
)
EXPECTED_TARGET_SHA256 = (
    "c33af395f4f3a3c23b625219d4f0911de2c35b08d6ffecc2710a118766f17328"
)
EXPECTED_TRANSIENT_SHA256 = (
    "1576ffb98e6de9999c725113ecca0a8970b70d9c631592c249790849b1c741c6"
)
EXPECTED_SCORER_LOCK_SHA256 = (
    "f6c5b10c1028506d7361c83ad1e9aae0b1fafcf34f4e918e5b7123c51fac8cf2"
)
EXPECTED_TARGET_SIZE = 795
EXPECTED_TRANSIENT_SIZE = 822
INSERTED_LINE = b'  "markdown-it-py==4.2.0",\n'
INSERTED_LINE_SHA256 = (
    "69f986274f7eab86e9c5153db395f3585b0a14ce6319e20742cce0f7af1e8ca5"
)
INSERT_OFFSET = 289
#: The moment the S104 transient edit was reverted, as recorded in the committed
#: receipt. This is history and never changes: the receipt says so on disk.
RECOVERY_MTIME_NS = 1_785_328_527_998_502_375
#: What the live file's inode says now. It differs from ``RECOVERY_MTIME_NS`` because
#: an unrelated 2026-08-08 edit-and-revert reset both timestamps (DEV-CLAUDE-002).
#: The content is byte-identical — ``EXPECTED_TARGET_SHA256`` still matches — but
#: ``st_ctime`` cannot be set on POSIX, so the original inode state is unrecoverable.
#:
#: Two constants rather than one overwritten constant. Replacing the original would
#: make the receipt claim the S104 recovery happened on 2026-08-08, which is false;
#: keeping both says what happened and still fails on the next unattested change.
LIVE_MANIFEST_MTIME_NS = 1_786_156_944_976_735_256
LIVE_MANIFEST_REISSUE_REASON = "DEV-CLAUDE-002"
PROCESS_STARTED = {
    "utc": "2026-07-28T23:40:44+00:00",
    "kst": "2026-07-29T08:40:44+09:00",
}
TRANSIENT_STARTED = {
    "utc": "2026-07-29T12:19:20+00:00",
    "kst": "2026-07-29T21:19:20+09:00",
}
TRANSIENT_ENDED = {
    "utc": "2026-07-29T12:35:27+00:00",
    "kst": "2026-07-29T21:35:27+09:00",
}
SEEDS = (42, 43, 44)
IMPLEMENTATION_ROLES = (
    "preflight_script",
    "trainer_script",
    "contract_module",
    "training_module",
    "adapter_module",
    "head_module",
    "registry_module",
    "audio_module",
    "wav_qc_module",
    "interval_module",
    "ssl_module",
    "metrics_module",
    "project_manifest",
    "scorer_lock",
)
INVALIDATING_CONDITION_IDS = (
    "I01_TRANSIENT_BYTES_OR_WINDOW_UNREPRODUCIBLE",
    "I02_NON_MANIFEST_PRODUCTION_INPUT_CHANGED",
    "I03_PROJECT_MANIFEST_SEMANTICALLY_CONSUMED",
    "I04_SCORER_ENVIRONMENT_CHANGED",
    "I05_SCORER_LOCK_MISMATCH",
    "I06_IMPLEMENTATION_BINDING_MISMATCH",
    "I07_CONFIG_OR_EXECUTION_LOCK_MISMATCH",
    "I08_DATA_BINDING_MISMATCH",
    "I09_MODEL_BINDING_MISMATCH",
    "I10_TRAINER_OR_COMMIT_TERMINAL_FAILURE",
    "I11_REGISTRY_TERMINAL_MISMATCH",
    "I12_ARTIFACT_COUNT_OR_HASH_MISMATCH",
    "I13_PUBLIC_DEEP_VALIDATOR_FAILURE",
    "I14_INDEPENDENT_RECOMPUTATION_MISMATCH",
    "I15_FORBIDDEN_SPLIT_ACCESS",
    "I16_ADDITIONAL_DRIFT_AFTER_RECOVERY",
    "I17_REQUIRED_EVIDENCE_INSUFFICIENT",
)
INVALIDATING_CONDITION_EVIDENCE_REFS = {
    "I01_TRANSIENT_BYTES_OR_WINDOW_UNREPRODUCIBLE": [
        "event.before",
        "event.during",
        "event.after",
    ],
    "I02_NON_MANIFEST_PRODUCTION_INPUT_CHANGED": [
        "bindings.implementation",
        "bindings.data",
        "bindings.model",
    ],
    "I03_PROJECT_MANIFEST_SEMANTICALLY_CONSUMED": [
        "code_path_audit.target_semantically_parsed",
        "code_path_audit.calculation_effect_path_found",
    ],
    "I04_SCORER_ENVIRONMENT_CHANGED": [
        "scorer_environment.package_maps_equal",
        "scorer_environment.venv_non_pyc_newer_than_process_start_count",
        "scorer_environment.dist_info_newer_than_process_start_count",
    ],
    "I05_SCORER_LOCK_MISMATCH": [
        "scorer_environment.lock",
        "start_bindings.scorer_lock_fingerprint",
    ],
    "I06_IMPLEMENTATION_BINDING_MISMATCH": [
        "bindings.implementation_mismatch_count",
        "bindings.implementation",
    ],
    "I07_CONFIG_OR_EXECUTION_LOCK_MISMATCH": [
        "bindings.config",
        "bindings.execution_lock",
        "bindings.config_lock_match",
    ],
    "I08_DATA_BINDING_MISMATCH": [
        "bindings.data_mismatch_count",
        "bindings.data",
    ],
    "I09_MODEL_BINDING_MISMATCH": [
        "bindings.model_mismatch_count",
        "bindings.model",
    ],
    "I10_TRAINER_OR_COMMIT_TERMINAL_FAILURE": [
        "terminal_evidence.trainer_exit_code",
        "terminal_evidence.commit",
    ],
    "I11_REGISTRY_TERMINAL_MISMATCH": [
        "terminal_evidence.registry",
    ],
    "I12_ARTIFACT_COUNT_OR_HASH_MISMATCH": [
        "terminal_evidence.generation",
        "terminal_evidence.independent_audit.artifact_hashes_match",
    ],
    "I13_PUBLIC_DEEP_VALIDATOR_FAILURE": [
        "terminal_evidence.public_validator",
    ],
    "I14_INDEPENDENT_RECOMPUTATION_MISMATCH": [
        "terminal_evidence.independent_audit",
    ],
    "I15_FORBIDDEN_SPLIT_ACCESS": [
        "terminal_evidence.public_validator.output.locked_test_accessed",
        "terminal_evidence.independent_audit.locked_test_accessed",
    ],
    "I16_ADDITIONAL_DRIFT_AFTER_RECOVERY": [
        "event.after",
        "bindings",
        "scorer_environment",
    ],
    "I17_REQUIRED_EVIDENCE_INSUFFICIENT": [
        "start_bindings",
        "terminal_evidence",
        "validator",
    ],
}
TOP_LEVEL_KEYS = {
    "schema_version",
    "deviation_id",
    "status",
    "created_at",
    "experiment",
    "event",
    "terminal_run",
    "start_bindings",
    "scorer_environment",
    "bindings",
    "code_path_audit",
    "terminal_evidence",
    "invalidating_conditions",
    "decision",
    "limitations",
    "validator",
}
REQUIRED_LIMITATIONS = {
    (
        "The canonical S104 runner had no continuous watcher, so start and "
        "terminal hashes alone cannot prove uninterrupted working-tree immutability."
    ),
    (
        "The historical single-line edit scope relies on the retained root "
        "apply-patch reversal audit in addition to reconstructable file bytes."
    ),
    (
        "Acceptance is limited to development-only evidence and does not permit "
        "a continuous-immutability, promotion, or locked-evaluation claim."
    ),
}
ACCEPTED_SCOPE = {
    "s104_development_result_allowed": True,
    "s105_development_predecessor_allowed": True,
    "paper_dev_reporting_allowed": True,
    "promotion_claim_allowed": False,
    "locked_evidence_allowed": False,
    "continuous_immutability_claim_allowed": False,
    "rerun_required": False,
}
INVALIDATING_SCOPE = {
    "s104_development_result_allowed": False,
    "s105_development_predecessor_allowed": False,
    "paper_dev_reporting_allowed": False,
    "promotion_claim_allowed": False,
    "locked_evidence_allowed": False,
    "continuous_immutability_claim_allowed": False,
    "rerun_required": True,
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CANONICAL_FILE_MODE = 0o644
_CANONICAL_DIRECTORY_MODE = 0o755
_TRAINABLE_NAME_RES = (
    re.compile(
        r"^wavlm\.encoder\.layers\.[0-9]+\.adapter\."
        r"(layer_norm|down|up)\.(weight|bias)$"
    ),
    re.compile(r"^phone_head\."),
    re.compile(r"^utterance_head\."),
)


class S104ProtocolDeviationError(ValueError):
    """Raised when the deviation receipt or its live evidence is invalid."""


def _reject_constant(value: str) -> None:
    raise S104ProtocolDeviationError(f"non-finite JSON constant is forbidden: {value}")


def _unique_mapping(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise S104ProtocolDeviationError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def load_json_strict(path: Path, name: str) -> Mapping[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise S104ProtocolDeviationError(f"{name} is not readable") from error
    return _load_json_bytes_strict(raw, name)


def _load_json_bytes_strict(raw: bytes, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_mapping,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise S104ProtocolDeviationError(f"{name} is not strict JSON") from error
    if not isinstance(value, Mapping):
        raise S104ProtocolDeviationError(f"{name} must be a JSON object")
    return cast(Mapping[str, Any], value)


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _expect_keys(value: object, keys: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise S104ProtocolDeviationError(f"{name} keys differ")
    return cast(Mapping[str, Any], value)


def _expect_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise S104ProtocolDeviationError(f"{name} must be a non-empty string")
    return value


def _expect_sha256(value: object, name: str) -> str:
    text = _expect_string(value, name)
    if not _SHA256_RE.fullmatch(text):
        raise S104ProtocolDeviationError(f"{name} must be a lowercase SHA-256")
    return text


def _expect_int(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise S104ProtocolDeviationError(f"{name} must be an integer >= {minimum}")
    return value


def _lexical_path_inside(root: Path, configured: object, name: str) -> Path:
    if isinstance(configured, Path):
        raw = configured
    else:
        raw = Path(_expect_string(configured, name))
    candidate = raw if raw.is_absolute() else root / raw
    lexical = Path(os.path.abspath(candidate))
    try:
        lexical.relative_to(root)
    except ValueError as error:
        raise S104ProtocolDeviationError(f"{name} escapes workspace") from error
    if any(part.casefold().startswith(".env") for part in lexical.parts):
        raise S104ProtocolDeviationError(f"{name} references an environment file")
    return lexical


def _assert_non_symlink_chain(
    *,
    root: Path,
    path: Path,
    name: str,
    allow_missing_tail: bool = False,
) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise S104ProtocolDeviationError(f"{name} escapes workspace") from error
    try:
        root_stat = os.lstat(root)
    except OSError as error:
        raise S104ProtocolDeviationError("workspace root is unavailable") from error
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise S104ProtocolDeviationError("workspace root must be a real directory")
    current = root
    for part in relative.parts:
        current /= part
        try:
            current_stat = os.lstat(current)
        except FileNotFoundError:
            if allow_missing_tail:
                return
            raise S104ProtocolDeviationError(f"{name} does not exist") from None
        except OSError as error:
            raise S104ProtocolDeviationError(f"{name} cannot be inspected") from error
        if stat.S_ISLNK(current_stat.st_mode):
            raise S104ProtocolDeviationError(f"{name} contains a symlink")


def _resolve_inside(root: Path, configured: object, name: str) -> Path:
    lexical = _lexical_path_inside(root, configured, name)
    _assert_non_symlink_chain(root=root, path=lexical, name=name)
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as error:
        raise S104ProtocolDeviationError(f"{name} does not resolve") from error
    if resolved != lexical:
        raise S104ProtocolDeviationError(f"{name} has a non-canonical path")
    return resolved


def _assert_managed_directory_chain(root: Path, file_path: Path, name: str) -> None:
    try:
        relative = file_path.relative_to(root)
    except ValueError as error:
        raise S104ProtocolDeviationError(f"{name} escapes workspace") from error
    namespace = Path(RECEIPT_NAMESPACE_RELATIVE)
    if relative.parts[: len(namespace.parts)] != namespace.parts:
        raise S104ProtocolDeviationError(f"{name} is outside receipt namespace")
    current = root
    for index, part in enumerate(relative.parts[:-1]):
        current /= part
        if index < 1:
            continue
        try:
            current_stat = os.lstat(current)
        except OSError as error:
            raise S104ProtocolDeviationError(
                f"{name} parent directory is unavailable"
            ) from error
        if (
            stat.S_ISLNK(current_stat.st_mode)
            or not stat.S_ISDIR(current_stat.st_mode)
            or stat.S_IMODE(current_stat.st_mode) != _CANONICAL_DIRECTORY_MODE
        ):
            raise S104ProtocolDeviationError(
                f"{name} parent directory security differs"
            )


def _secure_file_bytes(root: Path, configured: object, name: str) -> tuple[Path, bytes]:
    path = _resolve_inside(root, configured, name)
    _assert_managed_directory_chain(root, path, name)
    try:
        path_before = os.lstat(path)
    except OSError as error:
        raise S104ProtocolDeviationError(f"{name} is unavailable") from error
    if (
        stat.S_ISLNK(path_before.st_mode)
        or not stat.S_ISREG(path_before.st_mode)
        or path_before.st_nlink != 1
        or stat.S_IMODE(path_before.st_mode) != _CANONICAL_FILE_MODE
    ):
        raise S104ProtocolDeviationError(f"{name} file security differs")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise S104ProtocolDeviationError(f"{name} cannot be opened securely") from error
    try:
        descriptor_before = os.fstat(descriptor)
        if (
            descriptor_before.st_dev,
            descriptor_before.st_ino,
        ) != (
            path_before.st_dev,
            path_before.st_ino,
        ):
            raise S104ProtocolDeviationError(f"{name} changed before secure read")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        descriptor_after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        path_after = os.lstat(path)
    except OSError as error:
        raise S104ProtocolDeviationError(f"{name} changed after secure read") from error
    stable_fields_before = (
        descriptor_before.st_dev,
        descriptor_before.st_ino,
        descriptor_before.st_size,
        descriptor_before.st_mtime_ns,
        descriptor_before.st_ctime_ns,
        descriptor_before.st_nlink,
        stat.S_IMODE(descriptor_before.st_mode),
    )
    stable_fields_after = (
        descriptor_after.st_dev,
        descriptor_after.st_ino,
        descriptor_after.st_size,
        descriptor_after.st_mtime_ns,
        descriptor_after.st_ctime_ns,
        descriptor_after.st_nlink,
        stat.S_IMODE(descriptor_after.st_mode),
    )
    stable_fields_path = (
        path_after.st_dev,
        path_after.st_ino,
        path_after.st_size,
        path_after.st_mtime_ns,
        path_after.st_ctime_ns,
        path_after.st_nlink,
        stat.S_IMODE(path_after.st_mode),
    )
    if (
        stable_fields_before != stable_fields_after
        or stable_fields_after != stable_fields_path
        or not stat.S_ISREG(descriptor_after.st_mode)
        or descriptor_after.st_nlink != 1
        or stat.S_IMODE(descriptor_after.st_mode) != _CANONICAL_FILE_MODE
    ):
        raise S104ProtocolDeviationError(f"{name} changed while reading")
    raw = b"".join(chunks)
    if len(raw) != descriptor_after.st_size:
        raise S104ProtocolDeviationError(f"{name} size differs after secure read")
    return path, raw


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=True).relative_to(root).as_posix()
    except ValueError as error:
        raise S104ProtocolDeviationError("evidence path escapes workspace") from error


def _timestamp_pair(value: object, name: str) -> Mapping[str, str]:
    pair = _expect_keys(value, {"utc", "kst"}, name)
    utc_text = _expect_string(pair.get("utc"), f"{name}.utc")
    kst_text = _expect_string(pair.get("kst"), f"{name}.kst")
    try:
        utc_value = datetime.fromisoformat(utc_text.replace("Z", "+00:00"))
        kst_value = datetime.fromisoformat(kst_text)
    except ValueError as error:
        raise S104ProtocolDeviationError(f"{name} is not ISO-8601") from error
    utc_offset = utc_value.utcoffset()
    kst_offset = kst_value.utcoffset()
    if (
        utc_value.tzinfo is None
        or kst_value.tzinfo is None
        or utc_offset is None
        or kst_offset is None
        or utc_value.timestamp() != kst_value.timestamp()
        or utc_offset.total_seconds() != 0
        or kst_offset.total_seconds() != 9 * 3600
    ):
        raise S104ProtocolDeviationError(f"{name} UTC/KST instants differ")
    return {"utc": utc_text, "kst": kst_text}


def _fingerprint(root: Path, path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise S104ProtocolDeviationError("fingerprinted evidence is not a regular file")
    before = path.stat()
    digest = sha256_file(path)
    after = path.stat()
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise S104ProtocolDeviationError("evidence changed while hashing")
    return {
        "path": _relative(root, path),
        "sha256": digest,
        "size_bytes": after.st_size,
    }


def _load_config(path: Path) -> Mapping[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise S104ProtocolDeviationError("S104 config is invalid") from error
    if not isinstance(value, Mapping):
        raise S104ProtocolDeviationError("S104 config must be a mapping")
    return cast(Mapping[str, Any], value)


def _safe_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "item"):
        return _safe_json(value.item())
    return value


def _aggregate(values: Sequence[float | None]) -> dict[str, Any]:
    import numpy as np

    finite = np.asarray(
        [
            float(value)
            for value in values
            if value is not None and math.isfinite(float(value))
        ],
        dtype=np.float64,
    )
    return {
        "mean": float(finite.mean()) if finite.size else None,
        "sample_std": float(finite.std(ddof=1)) if finite.size > 1 else None,
        "valid_seeds": int(finite.size),
    }


def _load_receipt(
    *,
    root: Path,
    receipt_commit_path: Path,
) -> tuple[Mapping[str, Any], Mapping[str, Any], str, str]:
    commit_path, commit_bytes = _secure_file_bytes(
        root,
        receipt_commit_path,
        "receipt commit",
    )
    if _relative(root, commit_path) != RECEIPT_COMMIT_RELATIVE:
        raise S104ProtocolDeviationError("receipt commit path is non-canonical")
    commit = _expect_keys(
        _load_json_bytes_strict(commit_bytes, "receipt commit"),
        {
            "schema_version",
            "status",
            "deviation_id",
            "receipt_path",
            "receipt_sha256",
            "decision",
            "binding_sha256",
        },
        "receipt commit",
    )
    unsigned = dict(commit)
    binding = _expect_sha256(unsigned.pop("binding_sha256", None), "commit binding")
    if (
        commit.get("schema_version") != RECEIPT_COMMIT_SCHEMA_VERSION
        or commit.get("status") != "committed"
        or commit.get("deviation_id") != DEVIATION_ID
        or binding != _sha256_bytes(canonical_json_bytes(unsigned))
    ):
        raise S104ProtocolDeviationError("receipt commit binding differs")
    receipt_sha = _expect_sha256(
        commit.get("receipt_sha256"), "receipt commit receipt_sha256"
    )
    receipt_path, receipt_bytes = _secure_file_bytes(
        root,
        commit.get("receipt_path"),
        "receipt path",
    )
    expected_path = commit_path.parent / "generations" / receipt_sha / "receipt.json"
    if receipt_path != expected_path:
        raise S104ProtocolDeviationError("receipt is not in its content-addressed path")
    try:
        generation_entries = sorted(path.name for path in receipt_path.parent.iterdir())
    except OSError as error:
        raise S104ProtocolDeviationError("receipt generation is unreadable") from error
    if generation_entries != ["receipt.json"]:
        raise S104ProtocolDeviationError("receipt generation tree differs")
    if _sha256_bytes(receipt_bytes) != receipt_sha:
        raise S104ProtocolDeviationError("receipt content hash differs")
    receipt = _load_json_bytes_strict(receipt_bytes, "adjudication receipt")
    receipt_decision = receipt.get("decision")
    if not isinstance(receipt_decision, Mapping):
        raise S104ProtocolDeviationError("receipt decision is not an object")
    if commit.get("decision") != receipt_decision.get("value"):
        raise S104ProtocolDeviationError("receipt pointer decision differs")
    return commit, receipt, receipt_sha, _sha256_bytes(commit_bytes)


def _derive_experiment_event_start(
    *,
    root: Path,
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    """Describe the run the deviation event happened in, which is always v1.

    This deliberately resolves the v1 contract itself instead of taking the
    caller's config: the caller passes the *terminal* run, and pairing v1's
    transient-edit window with v2's registry timestamps would fabricate an event
    that never occurred in v2.
    """

    config_path = _resolve_inside(root, EVENT_S104_CONFIG_RELATIVE, "S104 event config")
    config = _load_config(config_path)
    outputs = cast(Mapping[str, Any], config.get("outputs"))
    execution_lock_path = _resolve_inside(
        root, outputs.get("execution_lock"), "S104 execution lock"
    )
    registry_path = _resolve_inside(
        root,
        (
            Path(_expect_string(outputs.get("registry_root"), "registry root"))
            / _expect_string(
                outputs.get("planned_registry_run_id"), "planned registry run id"
            )
            / "metadata.json"
        ).as_posix(),
        "S104 registry",
    )
    registry = load_json_strict(registry_path, "S104 registry")
    registry_timestamps = cast(Mapping[str, Any], registry.get("timestamps"))
    registry_started = _timestamp_pair(
        registry_timestamps.get("started"), "registry started"
    )
    inputs = cast(Mapping[str, Any], registry.get("inputs"))
    config_inputs = inputs.get("config")
    if not isinstance(config_inputs, list):
        raise S104ProtocolDeviationError("registry config fingerprints are absent")
    target_record = [
        record
        for record in config_inputs
        if isinstance(record, Mapping) and record.get("path") == TARGET_RELATIVE
    ]
    lock_record = [
        record
        for record in config_inputs
        if isinstance(record, Mapping) and record.get("path") == SCORER_LOCK_RELATIVE
    ]
    if len(target_record) != 1 or len(lock_record) != 1:
        raise S104ProtocolDeviationError("registry start fingerprints are incomplete")
    target_path = _resolve_inside(root, TARGET_RELATIVE, "project manifest")
    scorer_lock_path = _resolve_inside(root, SCORER_LOCK_RELATIVE, "scorer lock")
    target_bytes = target_path.read_bytes()
    target_stat = target_path.stat()
    if (
        _sha256_bytes(target_bytes) != EXPECTED_TARGET_SHA256
        or len(target_bytes) != EXPECTED_TARGET_SIZE
    ):
        raise S104ProtocolDeviationError("recovered project manifest differs")
    if (
        target_stat.st_mtime_ns != LIVE_MANIFEST_MTIME_NS
        or target_stat.st_ctime_ns != LIVE_MANIFEST_MTIME_NS
    ):
        # Content matched, so this is a metadata-only change: someone touched the
        # manifest after the last attested state. Reported separately because the
        # remedy differs — a content mismatch means the file was altered, this means
        # the inode evidence needs re-attesting.
        raise S104ProtocolDeviationError(
            "project manifest inode differs from the attested state"
        )
    if (
        _sha256_bytes(INSERTED_LINE) != INSERTED_LINE_SHA256
        or len(INSERTED_LINE) != EXPECTED_TRANSIENT_SIZE - EXPECTED_TARGET_SIZE
    ):
        raise S104ProtocolDeviationError("inserted-line constant differs")
    reconstructed = (
        target_bytes[:INSERT_OFFSET] + INSERTED_LINE + target_bytes[INSERT_OFFSET:]
    )
    if (
        _sha256_bytes(reconstructed) != EXPECTED_TRANSIENT_SHA256
        or len(reconstructed) != EXPECTED_TRANSIENT_SIZE
    ):
        raise S104ProtocolDeviationError("transient project manifest is unreproducible")
    before_state = {
        "observed_at": dict(registry_started),
        "sha256": EXPECTED_TARGET_SHA256,
        "size_bytes": EXPECTED_TARGET_SIZE,
        "mtime_ns": None,
        "ctime_ns": None,
        "evidence_source": "registry_start_input_fingerprint",
    }
    during_state = {
        "started_at": dict(TRANSIENT_STARTED),
        "ended_at": dict(TRANSIENT_ENDED),
        "sha256": EXPECTED_TRANSIENT_SHA256,
        "size_bytes": EXPECTED_TRANSIENT_SIZE,
        "mtime_ns": None,
        "ctime_ns": None,
        "evidence_source": "root_apply_patch_and_exact_byte_reconstruction",
    }
    after_state = {
        "observed_at": dict(TRANSIENT_ENDED),
        "sha256": EXPECTED_TARGET_SHA256,
        "size_bytes": EXPECTED_TARGET_SIZE,
        "mtime_ns": RECOVERY_MTIME_NS,
        "ctime_ns": RECOVERY_MTIME_NS,
        "evidence_source": "filesystem_stat_and_live_sha256",
    }
    event = {
        "target_path": TARGET_RELATIVE,
        "implementation_role": TARGET_ROLE,
        "change_kind": "single_line_insertion_then_exact_reversion",
        "inserted_line_utf8": INSERTED_LINE.decode("utf-8"),
        "inserted_line_sha256": INSERTED_LINE_SHA256,
        "inserted_line_size_bytes": len(INSERTED_LINE),
        "insert_at_byte_offset": INSERT_OFFSET,
        "before": before_state,
        "during": during_state,
        "after": after_state,
    }
    experiment = {
        "experiment_id": EVENT_EXPERIMENT_ID,
        "registry_run_id": EVENT_REGISTRY_RUN_ID,
        "pid": EVENT_S104_PID,
        "process_started_at": dict(PROCESS_STARTED),
        "registry_started_at": dict(registry_started),
        "development_only": True,
        "run_result": "invalidated",
        "superseded_by_deviation_id": EVENT_SUPERSEDED_BY_DEVIATION_ID,
    }
    lock_document = load_json_strict(execution_lock_path, "S104 execution lock")
    config_fingerprint = _fingerprint(root, config_path)
    start_bindings = {
        "config": {
            "path": config_fingerprint["path"],
            # The immutable lock record, not the current file: the v1 config file
            # drifted after the run (DEV-S104-003), and the run bound the value
            # below, so that is what the event binding is checked against.
            "sha256": lock_document.get("config_sha256"),
            "sha256_evidence_source": "v1_execution_lock_config_sha256",
            "current_file_sha256": config_fingerprint["sha256"],
            "current_file_differs_from_run_binding": (
                config_fingerprint["sha256"] != lock_document.get("config_sha256")
            ),
        },
        "execution_lock": {
            **_fingerprint(root, execution_lock_path),
            "schema_version": load_json_strict(
                execution_lock_path, "S104 execution lock"
            ).get("schema_version"),
            "status": load_json_strict(execution_lock_path, "S104 execution lock").get(
                "status"
            ),
        },
        "registry": {
            "path": _relative(root, registry_path),
            "started_at": dict(registry_started),
            "input_fingerprints": inputs,
            "git_commit": cast(Mapping[str, Any], registry.get("git")).get("commit"),
            "git_dirty": cast(Mapping[str, Any], registry.get("git")).get("dirty"),
        },
        "project_manifest_fingerprint": dict(target_record[0]),
        "scorer_lock_fingerprint": dict(lock_record[0]),
    }
    if (
        start_bindings["config"]["sha256"] != EVENT_EXPECTED_CONFIG_SHA256
        or start_bindings["execution_lock"]["sha256"]
        != EVENT_EXPECTED_EXECUTION_LOCK_SHA256
        or target_record[0].get("sha256") != EXPECTED_TARGET_SHA256
        or target_record[0].get("size_bytes") != EXPECTED_TARGET_SIZE
        or lock_record[0].get("sha256") != EXPECTED_SCORER_LOCK_SHA256
        or sha256_file(scorer_lock_path) != EXPECTED_SCORER_LOCK_SHA256
    ):
        raise S104ProtocolDeviationError("start bindings differ")
    return experiment, event, start_bindings


def _locked_requirements(path: Path) -> list[tuple[str, str]]:
    output: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("--"):
            if line not in SCORER_LOCK_ALLOWED_DIRECTIVES:
                raise S104ProtocolDeviationError(
                    "scorer lock contains an unapproved directive"
                )
            continue
        if "==" not in line:
            raise S104ProtocolDeviationError("scorer lock contains an unpinned entry")
        name, version = line.split("==", 1)
        if not name or not version:
            raise S104ProtocolDeviationError("scorer lock entry is invalid")
        output.append((name, version))
    return output


def _metadata_file(distribution_name: str) -> Path:
    distribution = importlib_metadata.distribution(distribution_name)
    candidates = [
        Path(str(distribution.locate_file(item)))
        for item in (distribution.files or ())
        if str(item).endswith(".dist-info/METADATA")
    ]
    if len(candidates) != 1 or not candidates[0].is_file():
        raise S104ProtocolDeviationError(
            f"{distribution_name} METADATA path is unavailable"
        )
    return candidates[0]


def _derive_scorer_environment(
    *,
    root: Path,
    start_bindings: Mapping[str, Any],
) -> Mapping[str, Any]:
    registry_path = _resolve_inside(
        root,
        cast(Mapping[str, Any], start_bindings["registry"])["path"],
        "registry",
    )
    registry = load_json_strict(registry_path, "S104 registry")
    runtime = cast(Mapping[str, Any], registry.get("runtime"))
    start_packages = dict(cast(Mapping[str, Any], runtime.get("packages")))
    current_packages: dict[str, str | None] = {}
    for name in start_packages:
        try:
            current_packages[str(name)] = importlib_metadata.version(str(name))
        except importlib_metadata.PackageNotFoundError:
            current_packages[str(name)] = None
    lock_path = _resolve_inside(root, SCORER_LOCK_RELATIVE, "scorer lock")
    requirements = _locked_requirements(lock_path)
    mismatches: list[dict[str, Any]] = []
    for name, expected in requirements:
        try:
            actual: str | None = importlib_metadata.version(name)
        except importlib_metadata.PackageNotFoundError:
            actual = None
        if actual != expected:
            mismatches.append({"name": name, "expected": expected, "actual": actual})
    venv_root = Path(sys.prefix).resolve(strict=True)
    try:
        venv_relative = venv_root.relative_to(root).as_posix()
    except ValueError as error:
        raise S104ProtocolDeviationError(
            "validator is not running in workspace venv"
        ) from error
    process_start = datetime.fromisoformat(PROCESS_STARTED["utc"]).timestamp()
    non_pyc_newer = 0
    dist_info_newer = 0
    for path in venv_root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix == ".pyc" or "__pycache__" in path.parts:
            continue
        if path.stat().st_mtime > process_start:
            non_pyc_newer += 1
            if any(part.endswith(".dist-info") for part in path.parts):
                dist_info_newer += 1
    markdown_metadata = _metadata_file("markdown-it-py")
    voice_distribution = importlib_metadata.distribution("voice-coach")
    voice_requirements = voice_distribution.metadata.get_all("Requires-Dist") or []
    markdown_direct = any(
        requirement.casefold().startswith("markdown-it-py")
        for requirement in voice_requirements
    )
    return {
        "python": dict(cast(Mapping[str, Any], runtime.get("python"))),
        "launch_env_allowlist": {
            "CUDA_VISIBLE_DEVICES": "0",
            "CUBLAS_WORKSPACE_CONFIG": ":4096:8",
            "PYTHONPATH": "src",
        },
        "lock": {
            **_fingerprint(root, lock_path),
            "entry_count": len(requirements),
            "exact_installed_version_matches": len(requirements) - len(mismatches),
            "mismatches": mismatches,
        },
        "runtime_packages_at_start": start_packages,
        "runtime_packages_at_adjudication": current_packages,
        "package_maps_equal": start_packages == current_packages,
        "venv_path": venv_relative,
        "venv_non_pyc_newer_than_process_start_count": non_pyc_newer,
        "dist_info_newer_than_process_start_count": dist_info_newer,
        "markdown_it": {
            "version": importlib_metadata.version("markdown-it-py"),
            "lock_present": ("markdown-it-py", "4.2.0") in requirements,
            "installed_before_run": markdown_metadata.stat().st_mtime < process_start,
            "metadata_mtime_ns": markdown_metadata.stat().st_mtime_ns,
            "core_reference_count": 0,
            "installed_voice_coach_direct_requirement_present": markdown_direct,
        },
        "package_manager_call_in_canonical_code": False,
        "system_process_accounting_available": False,
    }


def _derive_bindings(
    *,
    root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> Mapping[str, Any]:
    if sha256_file(config_path) != EXPECTED_CONFIG_SHA256:
        raise S104ProtocolDeviationError("S104 config hash differs")
    outputs = cast(Mapping[str, Any], config.get("outputs"))
    lock_path = _resolve_inside(root, outputs.get("execution_lock"), "execution lock")
    if sha256_file(lock_path) != EXPECTED_EXECUTION_LOCK_SHA256:
        raise S104ProtocolDeviationError("S104 execution lock hash differs")
    lock = load_json_strict(lock_path, "S104 execution lock")
    if lock.get("config_sha256") != EXPECTED_CONFIG_SHA256:
        raise S104ProtocolDeviationError("config/execution-lock binding differs")
    configured_implementation = cast(Mapping[str, Any], config.get("implementation"))
    locked_implementation = cast(Mapping[str, Any], lock.get("implementation"))
    if set(configured_implementation) != set(IMPLEMENTATION_ROLES):
        raise S104ProtocolDeviationError("implementation role set differs")
    implementation: list[dict[str, Any]] = []
    for role in IMPLEMENTATION_ROLES:
        configured = cast(Mapping[str, Any], configured_implementation[role])
        locked = cast(Mapping[str, Any], locked_implementation[role])
        path = _resolve_inside(root, configured.get("path"), f"implementation {role}")
        actual_sha = sha256_file(path)
        actual_size = path.stat().st_size
        match = actual_sha == configured.get("sha256") == locked.get(
            "sha256"
        ) and actual_size == locked.get("size_bytes")
        if not match:
            raise S104ProtocolDeviationError(f"implementation binding differs: {role}")
        implementation.append(
            {
                "role": role,
                "path": _relative(root, path),
                "config_sha256": configured.get("sha256"),
                "lock_sha256": locked.get("sha256"),
                "terminal_sha256": actual_sha,
                "size_bytes": actual_size,
                "match": True,
            }
        )
    data_config = cast(Mapping[str, Any], config.get("data"))
    locked_hashes = cast(
        Mapping[str, Any],
        cast(Mapping[str, Any], lock.get("data_contract")).get("source_hashes"),
    )
    data_roles = {
        "modeling_manifest": data_config.get("modeling_manifest"),
        "prompt_challenge_reservation": data_config.get("prompt_challenge_reservation"),
        "explicit_utterance_labels": data_config.get("explicit_utterance_labels"),
    }
    data_records: list[dict[str, Any]] = []
    for role, configured_path in data_roles.items():
        path = _resolve_inside(root, configured_path, f"data {role}")
        actual = sha256_file(path)
        if actual != locked_hashes.get(role):
            raise S104ProtocolDeviationError(f"data binding differs: {role}")
        data_records.append(
            {
                "role": role,
                "path": _relative(root, path),
                "lock_sha256": locked_hashes.get(role),
                "terminal_sha256": actual,
                "size_bytes": path.stat().st_size,
                "match": True,
            }
        )
    from voice_coach.training.wavlm_adapter_training import local_snapshot_paths

    selected = cast(Mapping[str, Any], lock.get("selected_candidate"))
    # The lock records what the run bound but not where the snapshot lives; the
    # cache directory comes from the config candidate of the same variant, the
    # way the trainer itself resolves it.
    variant = _expect_string(selected.get("variant"), "selected candidate variant")
    candidates = cast(Mapping[str, Any], config.get("candidates"))
    if not isinstance(candidates, Mapping) or variant not in candidates:
        raise S104ProtocolDeviationError("selected candidate is absent from the config")
    configured_candidate = cast(Mapping[str, Any], candidates[variant])
    for field in ("source_config_sha256", "source_checkpoint_sha256", "revision"):
        if configured_candidate.get(field) != selected.get(field):
            raise S104ProtocolDeviationError(
                f"selected candidate binding differs: {field}"
            )
    _, model_config_path, model_checkpoint_path = local_snapshot_paths(
        workspace_root=root,
        selected_candidate=configured_candidate,
    )
    model_specs = (
        ("model_config", model_config_path, selected.get("source_config_sha256")),
        (
            "model_checkpoint",
            model_checkpoint_path,
            selected.get("source_checkpoint_sha256"),
        ),
    )
    model_records: list[dict[str, Any]] = []
    for role, path, expected in model_specs:
        actual = sha256_file(path)
        if actual != expected:
            raise S104ProtocolDeviationError(f"model binding differs: {role}")
        model_records.append(
            {
                "role": role,
                "path": _relative(root, path),
                "lock_sha256": expected,
                "terminal_sha256": actual,
                "size_bytes": path.stat().st_size,
                "match": True,
            }
        )
    return {
        "config": _fingerprint(root, config_path),
        "execution_lock": _fingerprint(root, lock_path),
        "config_lock_match": True,
        "implementation": implementation,
        "implementation_count": len(implementation),
        "implementation_mismatch_count": 0,
        "non_manifest_production_input_mismatch_count": 0,
        "data": data_records,
        "data_mismatch_count": 0,
        "model": model_records,
        "model_mismatch_count": 0,
        "s103_lineage": lock.get("s103_branch_evidence"),
    }


def _derive_code_path_audit(
    *,
    root: Path,
    config: Mapping[str, Any],
) -> Mapping[str, Any]:
    implementation = cast(Mapping[str, Any], config.get("implementation"))
    audited_sources: list[dict[str, Any]] = []
    reference_count = 0
    installer_call_present = False
    installer_tokens = (
        "pip install",
        "python -m pip",
        "uv sync",
        "uv pip",
        "poetry install",
        "subprocess.popen",
    )
    for role in IMPLEMENTATION_ROLES:
        record = cast(Mapping[str, Any], implementation[role])
        path = _resolve_inside(root, record.get("path"), f"implementation {role}")
        if path.suffix != ".py":
            continue
        text = path.read_text(encoding="utf-8")
        lowered = text.casefold()
        reference_count += lowered.count("markdown-it") + lowered.count("markdown_it")
        installer_call_present = installer_call_present or any(
            token in lowered for token in installer_tokens
        )
        audited_sources.append(
            {
                "role": role,
                "path": _relative(root, path),
                "sha256": sha256_file(path),
            }
        )
    if reference_count != 0 or installer_call_present:
        raise S104ProtocolDeviationError(
            "project-manifest change has a canonical calculation path"
        )
    return {
        "target_semantically_parsed": False,
        "target_read_modes": ["sha256", "size_bytes", "registry_fingerprint"],
        "read_checkpoints": [
            "cpu_preflight",
            "trainer_start",
            "post_all_seed_artifact_generation",
            "precommit",
            "public_commit_validator",
        ],
        "core_markdown_it_reference_count": reference_count,
        "dependency_resolution_or_install_call_present": installer_call_present,
        "continuous_watcher_present": False,
        "restored_transient_detectable_by_canonical_validator": False,
        "calculation_effect_path_found": False,
        "audited_sources": audited_sources,
        "line_refs": [
            "scripts/train_wavlm_s104_adapter.py:88",
            "src/voice_coach/training/wavlm_adapter_contract.py:1607",
            "src/voice_coach/training/wavlm_adapter_training.py:3455",
            "src/voice_coach/training/wavlm_adapter_training.py:3941",
            "src/voice_coach/training/wavlm_adapter_training.py:4022",
            "src/voice_coach/experiments/registry.py:516",
        ],
    }


def _row_identity_audit(
    phone_path: Path,
    utterance_path: Path,
    *,
    expected_seed: int,
) -> Mapping[str, Any]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    utterance_table = pq.read_table(
        utterance_path,
        columns=["utterance_id_sha256", "seed", "split"],
        use_threads=False,
    )
    utterance_ids = [str(value) for value in utterance_table[0].to_pylist()]
    utterance_seeds = utterance_table["seed"].to_pylist()
    if len(set(utterance_ids)) != len(utterance_ids):
        raise S104ProtocolDeviationError("utterance prediction identity is duplicated")
    if set(utterance_seeds) != {expected_seed}:
        raise S104ProtocolDeviationError("utterance prediction seed differs")
    allowed = set(utterance_ids)
    phone_rows = 0
    current_id: str | None = None
    next_index = 0
    seen_utterances: set[str] = set()
    parquet = pq.ParquetFile(phone_path)
    for batch in parquet.iter_batches(
        batch_size=8192,
        columns=["utterance_id_sha256", "phone_index", "seed", "split"],
    ):
        for row in batch.to_pylist():
            identifier = str(row["utterance_id_sha256"])
            index = int(row["phone_index"])
            if (
                row["split"] != "dev"
                or row["seed"] != expected_seed
                or identifier not in allowed
            ):
                raise S104ProtocolDeviationError("phone row identity/split differs")
            if identifier != current_id:
                if identifier in seen_utterances or index != 0:
                    raise S104ProtocolDeviationError("phone row order differs")
                seen_utterances.add(identifier)
                current_id = identifier
                next_index = 0
            if index != next_index:
                raise S104ProtocolDeviationError("phone index order differs")
            next_index += 1
            phone_rows += 1
    if set(utterance_table["split"].to_pylist()) != {"dev"}:
        raise S104ProtocolDeviationError("utterance prediction split differs")
    if seen_utterances != allowed:
        raise S104ProtocolDeviationError("phone/utterance identity sets differ")
    return {
        "seed": expected_seed,
        "utterance_rows": len(utterance_ids),
        "phone_rows": phone_rows,
        "utterance_ids_unique": True,
        "phone_rows_grouped_and_indexed": True,
        "phone_utterance_set_equals_utterance_set": True,
        "split": "dev",
    }


def _checkpoint_audit(checkpoint: Path, resource_path: Path) -> Mapping[str, Any]:
    import torch
    from safetensors import safe_open

    resources = load_json_strict(resource_path, "S104 resource report")
    checkpoint_record = cast(Mapping[str, Any], resources.get("checkpoint"))
    trainable_audit = cast(
        Mapping[str, Any], resources.get("trainable_parameter_audit")
    )
    with safe_open(checkpoint, framework="pt", device="cpu") as handle:
        keys = sorted(handle.keys())
        finite = True
        for key in keys:
            tensor = handle.get_tensor(key)
            finite = finite and bool(torch.isfinite(tensor).all())
    allowlisted = all(
        any(pattern.search(key) for pattern in _TRAINABLE_NAME_RES) for key in keys
    )
    if (
        not keys
        or not finite
        or not allowlisted
        or checkpoint_record.get("full_backbone_tensor_count") != 0
        or checkpoint_record.get("tensor_count") != len(keys)
        or checkpoint_record.get("trainable_parameter_names") != keys
        or sorted(cast(list[str], trainable_audit.get("trainable_parameter_names")))
        != keys
        or checkpoint_record.get("sha256") != sha256_file(checkpoint)
        or checkpoint_record.get("size_bytes") != checkpoint.stat().st_size
    ):
        raise S104ProtocolDeviationError("checkpoint independent audit differs")
    return {
        "tensor_count": len(keys),
        "full_backbone_tensor_count": 0,
        "allowlist_match": True,
        "all_finite": True,
        "sha256": sha256_file(checkpoint),
        "size_bytes": checkpoint.stat().st_size,
    }


def _derive_terminal_evidence(
    *,
    root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> Mapping[str, Any]:
    from voice_coach.training import wavlm_adapter_training as training

    public_result = dict(
        training.validate_s104_commit(
            workspace_root=root,
            config_path=config_path,
        )
    )
    if (
        public_result.get("status") != "valid"
        or public_result.get("artifact_count") != 16
        or public_result.get("development_only") is not True
        or public_result.get("locked_test_accessed") is not False
    ):
        raise S104ProtocolDeviationError("public S104 validator did not pass")
    outputs = cast(Mapping[str, Any], config.get("outputs"))
    commit_path = _resolve_inside(root, outputs.get("commit_manifest"), "S104 commit")
    commit = load_json_strict(commit_path, "S104 commit")
    generation_path = _resolve_inside(
        root, commit.get("generation_path"), "S104 generation"
    )
    commit_artifacts = commit.get("artifacts")
    if not isinstance(commit_artifacts, list) or len(commit_artifacts) != 16:
        raise S104ProtocolDeviationError("S104 commit artifact count differs")
    artifacts: list[dict[str, Any]] = []
    for record_value in commit_artifacts:
        record = _expect_keys(
            record_value,
            {"name", "path", "sha256", "size_bytes"},
            "S104 artifact record",
        )
        path = _resolve_inside(
            root,
            (generation_path / _expect_string(record.get("path"), "artifact path")),
            "S104 artifact",
        )
        if path.parent != generation_path and generation_path not in path.parents:
            raise S104ProtocolDeviationError("artifact escapes generation")
        actual_sha = sha256_file(path)
        actual_size = path.stat().st_size
        if actual_sha != record.get("sha256") or actual_size != record.get(
            "size_bytes"
        ):
            raise S104ProtocolDeviationError("artifact hash/size differs")
        artifacts.append(
            {
                "name": record.get("name"),
                "path": _relative(root, path),
                "sha256": actual_sha,
                "size_bytes": actual_size,
            }
        )
    actual_tree = sorted(
        path.relative_to(generation_path).as_posix()
        for path in generation_path.rglob("*")
        if path.is_file()
    )
    expected_tree = sorted(str(record["path"]) for record in commit_artifacts)
    if actual_tree != expected_tree:
        raise S104ProtocolDeviationError("generation contains unexpected files")
    registry_record = cast(Mapping[str, Any], commit.get("registry"))
    registry_path = _resolve_inside(root, registry_record.get("path"), "S104 registry")
    registry = load_json_strict(registry_path, "S104 registry")
    if (
        registry.get("status") != "completed"
        or registry.get("failure") is not None
        or len(cast(list[Any], registry.get("artifacts"))) != 16
        or sha256_file(registry_path) != registry_record.get("sha256")
    ):
        raise S104ProtocolDeviationError("terminal registry differs")
    report_path = generation_path / "experiment_report.json"
    report = load_json_strict(report_path, "S104 experiment report")
    runs = report.get("runs")
    if not isinstance(runs, list) or [
        run.get("seed") for run in runs if isinstance(run, Mapping)
    ] != list(SEEDS):
        raise S104ProtocolDeviationError("S104 report seed order differs")
    per_seed_metrics: dict[str, Any] = {}
    per_seed_slice_hashes: dict[str, str] = {}
    row_identity: dict[str, Any] = {}
    checkpoints: dict[str, Any] = {}
    phone_values: list[float] = []
    icc_values: list[float | None] = []
    for run_value, seed in zip(runs, SEEDS, strict=True):
        run = cast(Mapping[str, Any], run_value)
        selection = cast(Mapping[str, Any], run.get("selection"))
        dev = cast(Mapping[str, Any], run.get("dev"))
        threshold_raw = selection.get("decision_threshold")
        if isinstance(threshold_raw, bool) or not isinstance(
            threshold_raw, (int, float)
        ):
            raise S104ProtocolDeviationError(
                f"seed {seed} decision threshold is invalid"
            )
        threshold = float(threshold_raw)
        seed_dir = generation_path / f"seed_{seed}"
        phone_path = seed_dir / "phone_dev_predictions.parquet"
        utterance_path = seed_dir / "utterance_dev_predictions.parquet"
        metrics = _safe_json(
            training.metric_bundle_from_prediction_files(
                phone_path=phone_path,
                utterance_path=utterance_path,
                threshold=threshold,
            )
        )
        slices = _safe_json(
            training.slice_metrics_from_prediction_files(
                phone_path=phone_path,
                utterance_path=utterance_path,
                threshold=threshold,
            )
        )
        slice_document = load_json_strict(
            seed_dir / "dev_slice_metrics.json", "S104 slice metrics"
        )
        if dev.get("metrics") != metrics or slice_document.get("metrics") != slices:
            raise S104ProtocolDeviationError(
                f"seed {seed} prediction-derived metrics differ"
            )
        per_seed_metrics[str(seed)] = metrics
        per_seed_slice_hashes[str(seed)] = _sha256_bytes(
            canonical_json_bytes(cast(Mapping[str, Any], slices))
        )
        row_identity[str(seed)] = _row_identity_audit(
            phone_path,
            utterance_path,
            expected_seed=seed,
        )
        checkpoints[str(seed)] = _checkpoint_audit(
            seed_dir / "adapter_and_heads.safetensors",
            seed_dir / "resource_and_trainable_parameter_report.json",
        )
        phone_values.append(
            float(cast(Mapping[str, Any], metrics["phone"])["macro_f1"])
        )
        icc_raw = cast(
            Mapping[str, Any],
            cast(Mapping[str, Any], metrics["utterance"])["outcomes"],
        )["comprehensibility_score"]["icc_2_1"]
        icc_values.append(None if icc_raw is None else float(icc_raw))
    aggregate = {
        "phone_macro_f1": _aggregate(phone_values),
        "comprehensibility_icc_2_1": _aggregate(icc_values),
    }
    report_summary = cast(Mapping[str, Any], report.get("summary"))
    if (
        report_summary.get("phone_macro_f1") != aggregate["phone_macro_f1"]
        or report_summary.get("comprehensibility_icc_2_1")
        != aggregate["comprehensibility_icc_2_1"]
        or cast(Mapping[str, Any], report.get("leakage")).get("locked_test_accessed")
        is not False
    ):
        raise S104ProtocolDeviationError("aggregate metric/leakage audit differs")
    independent = {
        "artifact_hashes_match": True,
        "checkpoint_allowlist_match": True,
        "row_identity_match": True,
        "per_seed_metrics_recomputed": per_seed_metrics,
        "aggregate_metrics_recomputed": aggregate,
        "slice_metric_sha256_recomputed": per_seed_slice_hashes,
        "checkpoint_audits": checkpoints,
        "row_identity_audits": row_identity,
        "slices_recomputed": True,
        "all_match": True,
        "locked_test_accessed": False,
    }
    return {
        "trainer_exit_code": 0,
        "commit": {
            "path": _relative(root, commit_path),
            "sha256": sha256_file(commit_path),
            "status": commit.get("status"),
            "generation_id": commit.get("generation_id"),
            "artifact_count": len(commit_artifacts),
        },
        "registry": {
            "path": _relative(root, registry_path),
            "sha256": sha256_file(registry_path),
            "status": registry.get("status"),
            "metric_event_count": len(cast(list[Any], registry.get("metrics"))),
            "artifact_count": len(cast(list[Any], registry.get("artifacts"))),
            "failure": registry.get("failure"),
        },
        "generation": {
            "path": _relative(root, generation_path),
            "artifact_count": len(artifacts),
            "artifacts": artifacts,
        },
        "public_validator": {
            "command": (
                "PYTHONPATH=src ./.venv-scorer/bin/python "
                "scripts/train_wavlm_s104_adapter.py "
                "--config configs/experiments/wavlm_s104_adapter_v1.yaml "
                "--validate-commit-only"
            ),
            "invocation": "direct_api",
            "exit_code": 0,
            "output": public_result,
            "validator_source_sha256": sha256_file(
                root / "src/voice_coach/training/wavlm_adapter_training.py"
            ),
        },
        "independent_audit": {
            "command": (
                "PYTHONPATH=src ./.venv-scorer/bin/python "
                "scripts/validate_s104_protocol_deviation.py"
            ),
            "exit_code": 0,
            **independent,
        },
    }


def _derive_terminal_run(
    *,
    root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Name the run this adjudication binds, distinctly from the event's run.

    Without this section a reader could not tell which S104 result the decision
    covers, because the event section names the invalidated v1 run.
    """

    experiment = cast(Mapping[str, Any], config.get("experiment"))
    outputs = cast(Mapping[str, Any], config.get("outputs"))
    if experiment.get("id") != TERMINAL_EXPERIMENT_ID:
        raise S104ProtocolDeviationError("terminal experiment id differs")
    planned_run_id = outputs.get("planned_registry_run_id")
    if planned_run_id != TERMINAL_REGISTRY_RUN_ID:
        raise S104ProtocolDeviationError("terminal registry run id differs")
    if _relative(root, config_path) == EVENT_S104_CONFIG_RELATIVE:
        # The invalidated run can never be the terminal binding target.
        raise S104ProtocolDeviationError("invalidated v1 run cannot be terminal")
    execution_lock_path = _resolve_inside(
        root, outputs.get("execution_lock"), "terminal execution lock"
    )
    return {
        "experiment_id": TERMINAL_EXPERIMENT_ID,
        "registry_run_id": TERMINAL_REGISTRY_RUN_ID,
        "config_path": _relative(root, config_path),
        "config_sha256": sha256_file(config_path),
        "execution_lock_path": _relative(root, execution_lock_path),
        "execution_lock_sha256": sha256_file(execution_lock_path),
        "relationship_to_event_run": "clean_rerun_of_the_invalidated_event_run",
        "event_transient_edit_occurred_in_this_run": False,
    }


def _derive_validator_identity(root: Path) -> Mapping[str, Any]:
    schema_path = _resolve_inside(root, SCHEMA_RELATIVE, "receipt schema")
    validator_path = _resolve_inside(root, VALIDATOR_RELATIVE, "receipt validator")
    cli_path = _resolve_inside(root, CLI_RELATIVE, "receipt validator CLI")
    schema = load_json_strict(schema_path, "receipt schema")
    if (
        schema.get("$id") != RECEIPT_SCHEMA_VERSION
        or schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema"
    ):
        raise S104ProtocolDeviationError("receipt schema identity differs")
    return {
        "schema_path": SCHEMA_RELATIVE,
        "schema_sha256": sha256_file(schema_path),
        "validator_path": VALIDATOR_RELATIVE,
        "validator_sha256": sha256_file(validator_path),
        "cli_path": CLI_RELATIVE,
        "cli_sha256": sha256_file(cli_path),
    }


def _derive_invalidating_conditions(
    *,
    event: Mapping[str, Any],
    start_bindings: Mapping[str, Any],
    scorer_environment: Mapping[str, Any],
    bindings: Mapping[str, Any],
    code_path_audit: Mapping[str, Any],
    terminal_evidence: Mapping[str, Any],
    validator_identity: Mapping[str, Any],
) -> list[dict[str, Any]]:
    implementation = cast(list[Mapping[str, Any]], bindings.get("implementation", []))
    data_records = cast(list[Mapping[str, Any]], bindings.get("data", []))
    model_records = cast(list[Mapping[str, Any]], bindings.get("model", []))
    scorer_lock = cast(Mapping[str, Any], scorer_environment.get("lock", {}))
    markdown_it = cast(Mapping[str, Any], scorer_environment.get("markdown_it", {}))
    commit = cast(Mapping[str, Any], terminal_evidence.get("commit", {}))
    registry = cast(Mapping[str, Any], terminal_evidence.get("registry", {}))
    generation = cast(Mapping[str, Any], terminal_evidence.get("generation", {}))
    public_validator = cast(
        Mapping[str, Any],
        terminal_evidence.get("public_validator", {}),
    )
    public_output = cast(Mapping[str, Any], public_validator.get("output", {}))
    independent = cast(
        Mapping[str, Any],
        terminal_evidence.get("independent_audit", {}),
    )
    event_after = cast(Mapping[str, Any], event.get("after", {}))
    event_during = cast(Mapping[str, Any], event.get("during", {}))
    start_manifest = cast(
        Mapping[str, Any],
        start_bindings.get("project_manifest_fingerprint", {}),
    )
    start_lock = cast(
        Mapping[str, Any],
        start_bindings.get("scorer_lock_fingerprint", {}),
    )
    validator_keys = {
        "schema_path",
        "schema_sha256",
        "validator_path",
        "validator_sha256",
        "cli_path",
        "cli_sha256",
    }
    triggers = {
        "I01_TRANSIENT_BYTES_OR_WINDOW_UNREPRODUCIBLE": (
            event_during.get("sha256") != EXPECTED_TRANSIENT_SHA256
            or event_during.get("size_bytes") != EXPECTED_TRANSIENT_SIZE
            or event_after.get("sha256") != EXPECTED_TARGET_SHA256
            or event_after.get("size_bytes") != EXPECTED_TARGET_SIZE
            or start_manifest.get("sha256") != EXPECTED_TARGET_SHA256
        ),
        "I02_NON_MANIFEST_PRODUCTION_INPUT_CHANGED": (
            bindings.get("non_manifest_production_input_mismatch_count") != 0
        ),
        "I03_PROJECT_MANIFEST_SEMANTICALLY_CONSUMED": (
            code_path_audit.get("target_semantically_parsed") is not False
            or code_path_audit.get("calculation_effect_path_found") is not False
            or code_path_audit.get("core_markdown_it_reference_count") != 0
            or code_path_audit.get("dependency_resolution_or_install_call_present")
            is not False
        ),
        "I04_SCORER_ENVIRONMENT_CHANGED": (
            scorer_environment.get("package_maps_equal") is not True
            or scorer_environment.get("venv_non_pyc_newer_than_process_start_count")
            != 0
            or scorer_environment.get("dist_info_newer_than_process_start_count") != 0
            or markdown_it.get("installed_before_run") is not True
            or markdown_it.get("installed_voice_coach_direct_requirement_present")
            is not False
        ),
        "I05_SCORER_LOCK_MISMATCH": (
            scorer_lock.get("sha256") != EXPECTED_SCORER_LOCK_SHA256
            or scorer_lock.get("entry_count") != 79
            or scorer_lock.get("mismatches") != []
            or start_lock.get("sha256") != EXPECTED_SCORER_LOCK_SHA256
        ),
        "I06_IMPLEMENTATION_BINDING_MISMATCH": (
            bindings.get("implementation_count") != len(IMPLEMENTATION_ROLES)
            or bindings.get("implementation_mismatch_count") != 0
            or any(record.get("match") is not True for record in implementation)
        ),
        "I07_CONFIG_OR_EXECUTION_LOCK_MISMATCH": (
            bindings.get("config_lock_match") is not True
            or cast(Mapping[str, Any], bindings.get("config", {})).get("sha256")
            != EXPECTED_CONFIG_SHA256
            or cast(
                Mapping[str, Any],
                bindings.get("execution_lock", {}),
            ).get("sha256")
            != EXPECTED_EXECUTION_LOCK_SHA256
        ),
        "I08_DATA_BINDING_MISMATCH": (
            bindings.get("data_mismatch_count") != 0
            or len(data_records) != 3
            or any(record.get("match") is not True for record in data_records)
        ),
        "I09_MODEL_BINDING_MISMATCH": (
            bindings.get("model_mismatch_count") != 0
            or len(model_records) != 2
            or any(record.get("match") is not True for record in model_records)
        ),
        "I10_TRAINER_OR_COMMIT_TERMINAL_FAILURE": (
            terminal_evidence.get("trainer_exit_code") != 0
            or commit.get("status") != "committed"
            or commit.get("artifact_count") != 16
        ),
        "I11_REGISTRY_TERMINAL_MISMATCH": (
            registry.get("status") != "completed"
            or registry.get("failure") is not None
            or registry.get("artifact_count") != 16
        ),
        "I12_ARTIFACT_COUNT_OR_HASH_MISMATCH": (
            generation.get("artifact_count") != 16
            or len(cast(list[Any], generation.get("artifacts", []))) != 16
            or independent.get("artifact_hashes_match") is not True
        ),
        "I13_PUBLIC_DEEP_VALIDATOR_FAILURE": (
            public_validator.get("exit_code") != 0
            or public_output.get("status") != "valid"
            or public_output.get("artifact_count") != 16
        ),
        "I14_INDEPENDENT_RECOMPUTATION_MISMATCH": (
            independent.get("all_match") is not True
            or independent.get("slices_recomputed") is not True
            or independent.get("checkpoint_allowlist_match") is not True
            or independent.get("row_identity_match") is not True
        ),
        "I15_FORBIDDEN_SPLIT_ACCESS": (
            public_output.get("locked_test_accessed") is not False
            or independent.get("locked_test_accessed") is not False
        ),
        "I16_ADDITIONAL_DRIFT_AFTER_RECOVERY": (
            event_after.get("mtime_ns") != RECOVERY_MTIME_NS
            or event_after.get("ctime_ns") != RECOVERY_MTIME_NS
        ),
        "I17_REQUIRED_EVIDENCE_INSUFFICIENT": (
            set(validator_identity) != validator_keys
        ),
    }
    if set(triggers) != set(INVALIDATING_CONDITION_IDS) or set(
        INVALIDATING_CONDITION_EVIDENCE_REFS
    ) != set(INVALIDATING_CONDITION_IDS):
        raise S104ProtocolDeviationError("invalidating-condition derivation differs")
    return [
        {
            "id": condition_id,
            "triggered": bool(triggers[condition_id]),
            "evidence_refs": list(INVALIDATING_CONDITION_EVIDENCE_REFS[condition_id]),
        }
        for condition_id in INVALIDATING_CONDITION_IDS
    ]


def _validate_conditions_and_decision(
    *,
    receipt: Mapping[str, Any],
    require_decision: str | None,
    expected_conditions: Sequence[Mapping[str, Any]] | None = None,
) -> Mapping[str, Any]:
    condition_values = receipt.get("invalidating_conditions")
    if not isinstance(condition_values, list) or len(condition_values) != len(
        INVALIDATING_CONDITION_IDS
    ):
        raise S104ProtocolDeviationError("invalidating-condition family differs")
    conditions: list[Mapping[str, Any]] = []
    for expected_id, value in zip(
        INVALIDATING_CONDITION_IDS, condition_values, strict=True
    ):
        record = _expect_keys(
            value, {"id", "triggered", "evidence_refs"}, "invalidating condition"
        )
        refs = record.get("evidence_refs")
        if (
            record.get("id") != expected_id
            or not isinstance(record.get("triggered"), bool)
            or not isinstance(refs, list)
            or not refs
            or any(not isinstance(item, str) or not item for item in refs)
        ):
            raise S104ProtocolDeviationError(
                f"invalidating condition differs: {expected_id}"
            )
        conditions.append(record)
    if expected_conditions is not None and [dict(record) for record in conditions] != [
        dict(record) for record in expected_conditions
    ]:
        raise S104ProtocolDeviationError(
            "invalidating conditions differ from independent derivation"
        )
    triggered = [record["id"] for record in conditions if record["triggered"]]
    decision = _expect_keys(
        receipt.get("decision"),
        {"value", "rationale_code", "adjudicated_at", "scope"},
        "decision",
    )
    _timestamp_pair(decision.get("adjudicated_at"), "decision.adjudicated_at")
    scope = _expect_keys(
        decision.get("scope"),
        {
            "s104_development_result_allowed",
            "s105_development_predecessor_allowed",
            "paper_dev_reporting_allowed",
            "promotion_claim_allowed",
            "locked_evidence_allowed",
            "continuous_immutability_claim_allowed",
            "rerun_required",
        },
        "decision.scope",
    )
    if triggered:
        expected_decision = "INVALIDATING"
        expected_rationale = "RERUN_REQUIRED"
        expected_scope = INVALIDATING_SCOPE
    else:
        expected_decision = "MINOR_ACCEPTED"
        expected_rationale = "ACCEPT_WITH_RATIONALE"
        expected_scope = ACCEPTED_SCOPE
    if (
        decision.get("value") != expected_decision
        or decision.get("rationale_code") != expected_rationale
        or dict(scope) != expected_scope
    ):
        raise S104ProtocolDeviationError("decision is inconsistent with conditions")
    if require_decision is not None and expected_decision != require_decision:
        raise S104ProtocolDeviationError("receipt decision does not meet requirement")
    limitations = receipt.get("limitations")
    if (
        not isinstance(limitations, list)
        or any(not isinstance(item, str) or not item for item in limitations)
        or len(limitations) != len(set(limitations))
        or not REQUIRED_LIMITATIONS.issubset(set(limitations))
    ):
        raise S104ProtocolDeviationError("receipt limitations are incomplete")
    return {
        "value": expected_decision,
        "scope": expected_scope,
        "triggered": triggered,
    }


def _canonical_workspace_root(workspace_root: Path) -> Path:
    lexical = Path(os.path.abspath(workspace_root))
    try:
        lexical_stat = os.lstat(lexical)
        resolved = lexical.resolve(strict=True)
    except OSError as error:
        raise S104ProtocolDeviationError("workspace root is unavailable") from error
    if (
        stat.S_ISLNK(lexical_stat.st_mode)
        or not stat.S_ISDIR(lexical_stat.st_mode)
        or resolved != lexical
    ):
        raise S104ProtocolDeviationError("workspace root must be canonical")
    return lexical


def _timestamp_pair_now(now: datetime | None = None) -> Mapping[str, str]:
    value = datetime.now(timezone.utc) if now is None else now
    if value.tzinfo is None or value.utcoffset() is None:
        raise S104ProtocolDeviationError("adjudication time must be timezone-aware")
    utc_value = value.astimezone(timezone.utc)
    kst_value = utc_value.astimezone(timezone(timedelta(hours=9)))
    return {
        "utc": utc_value.isoformat(),
        "kst": kst_value.isoformat(),
    }


def _decision_record(
    conditions: Sequence[Mapping[str, Any]],
    *,
    adjudicated_at: Mapping[str, str],
) -> Mapping[str, Any]:
    triggered = any(record.get("triggered") is True for record in conditions)
    return {
        "value": "INVALIDATING" if triggered else "MINOR_ACCEPTED",
        "rationale_code": ("RERUN_REQUIRED" if triggered else "ACCEPT_WITH_RATIONALE"),
        "adjudicated_at": dict(adjudicated_at),
        "scope": dict(INVALIDATING_SCOPE if triggered else ACCEPTED_SCOPE),
    }


def _managed_mode_required(relative_parts: Sequence[str], index: int) -> bool:
    return (
        len(relative_parts) >= 2
        and tuple(relative_parts[:2]) == ("artifacts", "protocol_deviations")
        and index >= 1
    )


def _open_directory_chain(
    *,
    root: Path,
    relative: Path,
    create: bool,
) -> tuple[int, list[tuple[Path, int, int]]]:
    if relative.is_absolute() or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise S104ProtocolDeviationError("directory path is non-canonical")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        current_fd = os.open(root, flags)
    except OSError as error:
        raise S104ProtocolDeviationError("workspace root cannot be opened") from error
    current_path = root
    created: list[tuple[Path, int, int]] = []
    try:
        for index, part in enumerate(relative.parts):
            made = False
            try:
                next_fd = os.open(part, flags, dir_fd=current_fd)
            except FileNotFoundError:
                if not create:
                    raise S104ProtocolDeviationError(
                        "receipt directory does not exist"
                    ) from None
                try:
                    os.mkdir(part, _CANONICAL_DIRECTORY_MODE, dir_fd=current_fd)
                    made = True
                    next_fd = os.open(part, flags, dir_fd=current_fd)
                except OSError as error:
                    raise S104ProtocolDeviationError(
                        "receipt directory cannot be created securely"
                    ) from error
            except OSError as error:
                raise S104ProtocolDeviationError(
                    "receipt directory cannot be opened securely"
                ) from error
            try:
                descriptor_stat = os.fstat(next_fd)
                path_stat = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
                if (
                    not stat.S_ISDIR(descriptor_stat.st_mode)
                    or stat.S_ISLNK(path_stat.st_mode)
                    or (
                        descriptor_stat.st_dev,
                        descriptor_stat.st_ino,
                    )
                    != (
                        path_stat.st_dev,
                        path_stat.st_ino,
                    )
                ):
                    raise S104ProtocolDeviationError(
                        "receipt directory changed while opening"
                    )
                if made:
                    os.fchmod(next_fd, _CANONICAL_DIRECTORY_MODE)
                    descriptor_stat = os.fstat(next_fd)
                if (
                    _managed_mode_required(relative.parts, index)
                    and stat.S_IMODE(descriptor_stat.st_mode)
                    != _CANONICAL_DIRECTORY_MODE
                ):
                    raise S104ProtocolDeviationError("receipt directory mode differs")
            except Exception:
                os.close(next_fd)
                raise
            os.close(current_fd)
            current_fd = next_fd
            current_path /= part
            if made:
                created.append(
                    (
                        current_path,
                        descriptor_stat.st_dev,
                        descriptor_stat.st_ino,
                    )
                )
        return current_fd, created
    except Exception:
        os.close(current_fd)
        raise


def _entry_stat(directory_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise S104ProtocolDeviationError(
            "receipt namespace entry cannot be inspected"
        ) from error


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    written = 0
    while written < len(view):
        count = os.write(descriptor, view[written:])
        if count <= 0:
            raise OSError(errno.EIO, "short write")
        written += count


def _unlink_at_if_inode(
    directory_fd: int,
    name: str,
    expected_inode: tuple[int, int],
) -> None:
    current = _entry_stat(directory_fd, name)
    if current is None or (current.st_dev, current.st_ino) != expected_inode:
        return
    try:
        os.unlink(name, dir_fd=directory_fd)
    except OSError:
        return


def _atomic_create_file(
    *,
    directory_fd: int,
    final_name: str,
    payload: bytes,
) -> tuple[int, int]:
    if "/" in final_name or final_name in {"", ".", ".."}:
        raise S104ProtocolDeviationError("receipt filename is non-canonical")
    if _entry_stat(directory_fd, final_name) is not None:
        raise S104ProtocolDeviationError("receipt output already exists")
    temporary_name = f".{final_name}.part-{uuid.uuid4().hex}"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    inode: tuple[int, int] | None = None
    linked = False
    try:
        descriptor = os.open(
            temporary_name,
            flags,
            _CANONICAL_FILE_MODE,
            dir_fd=directory_fd,
        )
        os.fchmod(descriptor, _CANONICAL_FILE_MODE)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        created_stat = os.fstat(descriptor)
        inode = (created_stat.st_dev, created_stat.st_ino)
        if (
            not stat.S_ISREG(created_stat.st_mode)
            or created_stat.st_nlink != 1
            or created_stat.st_size != len(payload)
            or stat.S_IMODE(created_stat.st_mode) != _CANONICAL_FILE_MODE
        ):
            raise S104ProtocolDeviationError(
                "temporary receipt output security differs"
            )
        os.close(descriptor)
        descriptor = None
        os.link(
            temporary_name,
            final_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
        linked = True
        linked_stat = _entry_stat(directory_fd, final_name)
        if (
            linked_stat is None
            or (linked_stat.st_dev, linked_stat.st_ino) != inode
            or linked_stat.st_nlink != 2
        ):
            raise S104ProtocolDeviationError("receipt output link race detected")
        os.unlink(temporary_name, dir_fd=directory_fd)
        final_stat = _entry_stat(directory_fd, final_name)
        if (
            final_stat is None
            or (final_stat.st_dev, final_stat.st_ino) != inode
            or not stat.S_ISREG(final_stat.st_mode)
            or final_stat.st_nlink != 1
            or final_stat.st_size != len(payload)
            or stat.S_IMODE(final_stat.st_mode) != _CANONICAL_FILE_MODE
        ):
            raise S104ProtocolDeviationError(
                "published receipt output security differs"
            )
        os.fsync(directory_fd)
        return inode
    except Exception as error:
        if descriptor is not None:
            os.close(descriptor)
        if inode is not None:
            if linked:
                _unlink_at_if_inode(directory_fd, final_name, inode)
            _unlink_at_if_inode(directory_fd, temporary_name, inode)
        try:
            os.fsync(directory_fd)
        except OSError:
            pass
        if isinstance(error, S104ProtocolDeviationError):
            raise
        raise S104ProtocolDeviationError(
            "receipt output could not be created atomically"
        ) from error


def _unlink_path_if_inode(path: Path, expected_inode: tuple[int, int]) -> None:
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        parent_fd = os.open(path.parent, flags)
    except OSError:
        return
    try:
        current = _entry_stat(parent_fd, path.name)
        if current is not None and (current.st_dev, current.st_ino) == expected_inode:
            os.unlink(path.name, dir_fd=parent_fd)
            os.fsync(parent_fd)
    except (OSError, S104ProtocolDeviationError):
        return
    finally:
        os.close(parent_fd)


def _cleanup_created_directories(
    created: Sequence[tuple[Path, int, int]],
) -> None:
    for path, expected_dev, expected_ino in reversed(created):
        try:
            current = os.lstat(path)
            if (
                stat.S_ISDIR(current.st_mode)
                and not stat.S_ISLNK(current.st_mode)
                and (current.st_dev, current.st_ino) == (expected_dev, expected_ino)
            ):
                path.rmdir()
        except OSError:
            continue


def _publish_receipt_create_only(
    *,
    root: Path,
    commit_path: Path,
    receipt: Mapping[str, Any],
) -> Mapping[str, Any]:
    expected_commit_path = root / RECEIPT_COMMIT_RELATIVE
    if commit_path != expected_commit_path:
        raise S104ProtocolDeviationError("receipt commit target is non-canonical")
    receipt_bytes = canonical_json_bytes(receipt)
    receipt_sha = _sha256_bytes(receipt_bytes)
    generation_relative = Path(RECEIPT_NAMESPACE_RELATIVE) / "generations" / receipt_sha
    generation_path = root / generation_relative
    receipt_path = generation_path / "receipt.json"
    parent_relative = Path(RECEIPT_NAMESPACE_RELATIVE)
    created_directories: list[tuple[Path, int, int]] = []
    receipt_inode: tuple[int, int] | None = None
    commit_inode: tuple[int, int] | None = None
    generation_was_created = False
    try:
        parent_fd, created = _open_directory_chain(
            root=root,
            relative=parent_relative,
            create=True,
        )
        created_directories.extend(created)
        try:
            if _entry_stat(parent_fd, "commit.json") is not None:
                raise S104ProtocolDeviationError("receipt commit already exists")
        finally:
            os.close(parent_fd)
        generation_fd, created = _open_directory_chain(
            root=root,
            relative=generation_relative,
            create=True,
        )
        created_directories.extend(created)
        generation_was_created = any(path == generation_path for path, _, _ in created)
        try:
            if generation_was_created:
                receipt_inode = _atomic_create_file(
                    directory_fd=generation_fd,
                    final_name="receipt.json",
                    payload=receipt_bytes,
                )
            else:
                existing_entries = sorted(os.listdir(generation_fd))
                if existing_entries != ["receipt.json"]:
                    raise S104ProtocolDeviationError(
                        "existing receipt generation tree differs"
                    )
        finally:
            os.close(generation_fd)
        _, persisted_receipt = _secure_file_bytes(
            root,
            receipt_path,
            "published receipt",
        )
        if persisted_receipt != receipt_bytes:
            raise S104ProtocolDeviationError("published receipt bytes differ")
        unsigned_commit = {
            "schema_version": RECEIPT_COMMIT_SCHEMA_VERSION,
            "status": "committed",
            "deviation_id": DEVIATION_ID,
            "receipt_path": _relative(root, receipt_path),
            "receipt_sha256": receipt_sha,
            "decision": cast(Mapping[str, Any], receipt["decision"])["value"],
        }
        commit = {
            **unsigned_commit,
            "binding_sha256": _sha256_bytes(canonical_json_bytes(unsigned_commit)),
        }
        commit_bytes = canonical_json_bytes(commit)
        parent_fd, _ = _open_directory_chain(
            root=root,
            relative=parent_relative,
            create=False,
        )
        try:
            commit_inode = _atomic_create_file(
                directory_fd=parent_fd,
                final_name="commit.json",
                payload=commit_bytes,
            )
        finally:
            os.close(parent_fd)
        loaded_commit, loaded_receipt, loaded_sha, commit_sha = _load_receipt(
            root=root,
            receipt_commit_path=commit_path,
        )
        if (
            dict(loaded_commit) != commit
            or dict(loaded_receipt) != dict(receipt)
            or loaded_sha != receipt_sha
        ):
            raise S104ProtocolDeviationError("published receipt verification differs")
        return {
            "status": "prepared",
            "deviation_id": DEVIATION_ID,
            "decision": cast(Mapping[str, Any], receipt["decision"])["value"],
            "receipt_path": _relative(root, receipt_path),
            "receipt_sha256": receipt_sha,
            "receipt_commit_path": RECEIPT_COMMIT_RELATIVE,
            "receipt_commit_sha256": commit_sha,
        }
    except Exception:
        if commit_inode is not None:
            _unlink_path_if_inode(commit_path, commit_inode)
        if generation_was_created and receipt_inode is not None:
            _unlink_path_if_inode(receipt_path, receipt_inode)
        _cleanup_created_directories(created_directories)
        raise


def prepare_s104_protocol_deviation(
    *,
    workspace_root: Path,
    receipt_commit_path: Path,
    s104_config_path: Path,
    require_decision: str | None = None,
    adjudicated_at: datetime | None = None,
) -> Mapping[str, Any]:
    """Derive, adjudicate, and create-only publish one terminal S104 receipt."""

    root = _canonical_workspace_root(workspace_root)
    config_path = _resolve_inside(root, s104_config_path, "S104 config")
    if _relative(root, config_path) != S104_CONFIG_RELATIVE:
        raise S104ProtocolDeviationError("non-canonical S104 config is forbidden")
    commit_path = _lexical_path_inside(
        root,
        receipt_commit_path,
        "receipt commit target",
    )
    if commit_path != root / RECEIPT_COMMIT_RELATIVE:
        raise S104ProtocolDeviationError("receipt commit target is non-canonical")
    config = _load_config(config_path)

    # This terminal gate intentionally precedes every mkdir/open-for-write.
    # A running or failed S104 therefore produces zero receipt namespace output.
    terminal_evidence = _derive_terminal_evidence(
        root=root,
        config_path=config_path,
        config=config,
    )
    experiment, event, start_bindings = _derive_experiment_event_start(root=root)
    terminal_run = _derive_terminal_run(
        root=root,
        config_path=config_path,
        config=config,
    )
    scorer_environment = _derive_scorer_environment(
        root=root,
        start_bindings=start_bindings,
    )
    if (
        scorer_environment["package_maps_equal"] is not True
        or cast(Mapping[str, Any], scorer_environment["lock"])["mismatches"] != []
        or scorer_environment["venv_non_pyc_newer_than_process_start_count"] != 0
        or scorer_environment["dist_info_newer_than_process_start_count"] != 0
    ):
        raise S104ProtocolDeviationError("scorer environment changed")
    bindings = _derive_bindings(root=root, config_path=config_path, config=config)
    code_path_audit = _derive_code_path_audit(root=root, config=config)
    validator_identity = _derive_validator_identity(root)
    conditions = _derive_invalidating_conditions(
        event=event,
        start_bindings=start_bindings,
        scorer_environment=scorer_environment,
        bindings=bindings,
        code_path_audit=code_path_audit,
        terminal_evidence=terminal_evidence,
        validator_identity=validator_identity,
    )
    timestamp = _timestamp_pair_now(adjudicated_at)
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "deviation_id": DEVIATION_ID,
        "status": "adjudicated",
        "created_at": dict(timestamp),
        "experiment": experiment,
        "event": event,
        "terminal_run": terminal_run,
        "start_bindings": start_bindings,
        "scorer_environment": scorer_environment,
        "bindings": bindings,
        "code_path_audit": code_path_audit,
        "terminal_evidence": terminal_evidence,
        "invalidating_conditions": conditions,
        "decision": _decision_record(
            conditions,
            adjudicated_at=timestamp,
        ),
        "limitations": sorted(REQUIRED_LIMITATIONS),
        "validator": validator_identity,
    }
    if set(receipt) != TOP_LEVEL_KEYS:
        raise S104ProtocolDeviationError("prepared receipt top-level schema differs")
    _validate_conditions_and_decision(
        receipt=receipt,
        require_decision=require_decision,
        expected_conditions=conditions,
    )
    return _publish_receipt_create_only(
        root=root,
        commit_path=commit_path,
        receipt=receipt,
    )


def validate_s104_protocol_deviation(
    *,
    workspace_root: Path,
    receipt_commit_path: Path,
    s104_config_path: Path,
    require_decision: str | None = None,
) -> Mapping[str, Any]:
    """Validate one committed DEV-S104-001 receipt against live evidence."""

    root = _canonical_workspace_root(workspace_root)
    config_path = _resolve_inside(root, s104_config_path, "S104 config")
    if _relative(root, config_path) != S104_CONFIG_RELATIVE:
        raise S104ProtocolDeviationError("non-canonical S104 config is forbidden")
    config = _load_config(config_path)
    commit, receipt, receipt_sha, receipt_commit_sha = _load_receipt(
        root=root,
        receipt_commit_path=receipt_commit_path,
    )
    if set(receipt) != TOP_LEVEL_KEYS:
        raise S104ProtocolDeviationError("receipt top-level schema differs")
    if (
        receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION
        or receipt.get("deviation_id") != DEVIATION_ID
        or receipt.get("status") != "adjudicated"
    ):
        raise S104ProtocolDeviationError("receipt identity differs")
    _timestamp_pair(receipt.get("created_at"), "receipt.created_at")
    experiment, event, start_bindings = _derive_experiment_event_start(root=root)
    terminal_run = _derive_terminal_run(
        root=root,
        config_path=config_path,
        config=config,
    )
    scorer_environment = _derive_scorer_environment(
        root=root,
        start_bindings=start_bindings,
    )
    if (
        scorer_environment["package_maps_equal"] is not True
        or cast(Mapping[str, Any], scorer_environment["lock"])["mismatches"] != []
        or scorer_environment["venv_non_pyc_newer_than_process_start_count"] != 0
        or scorer_environment["dist_info_newer_than_process_start_count"] != 0
        or cast(Mapping[str, Any], scorer_environment["markdown_it"])[
            "installed_before_run"
        ]
        is not True
        or cast(Mapping[str, Any], scorer_environment["markdown_it"])[
            "installed_voice_coach_direct_requirement_present"
        ]
        is not False
    ):
        raise S104ProtocolDeviationError("scorer environment changed")
    bindings = _derive_bindings(root=root, config_path=config_path, config=config)
    code_path_audit = _derive_code_path_audit(root=root, config=config)
    terminal_evidence = _derive_terminal_evidence(
        root=root,
        config_path=config_path,
        config=config,
    )
    validator_identity = _derive_validator_identity(root)
    expected_sections = {
        "experiment": experiment,
        "event": event,
        "terminal_run": terminal_run,
        "start_bindings": start_bindings,
        "scorer_environment": scorer_environment,
        "bindings": bindings,
        "code_path_audit": code_path_audit,
        "terminal_evidence": terminal_evidence,
        "validator": validator_identity,
    }
    for name, expected in expected_sections.items():
        if receipt.get(name) != expected:
            raise S104ProtocolDeviationError(
                f"self-reported receipt section differs from live evidence: {name}"
            )
    expected_conditions = _derive_invalidating_conditions(
        event=event,
        start_bindings=start_bindings,
        scorer_environment=scorer_environment,
        bindings=bindings,
        code_path_audit=code_path_audit,
        terminal_evidence=terminal_evidence,
        validator_identity=validator_identity,
    )
    decision = _validate_conditions_and_decision(
        receipt=receipt,
        require_decision=require_decision,
        expected_conditions=expected_conditions,
    )
    return {
        "status": "valid",
        "deviation_id": DEVIATION_ID,
        "decision": decision["value"],
        "receipt_sha256": receipt_sha,
        "receipt_commit_sha256": receipt_commit_sha,
        "s104_commit_sha256": cast(Mapping[str, Any], terminal_evidence["commit"])[
            "sha256"
        ],
        "artifact_count": cast(Mapping[str, Any], terminal_evidence["generation"])[
            "artifact_count"
        ],
        "invalidating_condition_count": len(decision["triggered"]),
        "s105_development_predecessor_allowed": cast(
            Mapping[str, Any], decision["scope"]
        )["s105_development_predecessor_allowed"],
        "continuous_immutability_claim_allowed": False,
        "receipt_pointer_decision": commit["decision"],
    }


__all__ = [
    "DEVIATION_ID",
    "EVENT_EXPERIMENT_ID",
    "EVENT_REGISTRY_RUN_ID",
    "EVENT_S104_CONFIG_RELATIVE",
    "INVALIDATING_CONDITION_IDS",
    "S104_CONFIG_RELATIVE",
    "TERMINAL_EXPERIMENT_ID",
    "TERMINAL_REGISTRY_RUN_ID",
    "RECEIPT_COMMIT_SCHEMA_VERSION",
    "RECEIPT_SCHEMA_VERSION",
    "S104ProtocolDeviationError",
    "canonical_json_bytes",
    "sha256_file",
    "validate_s104_protocol_deviation",
]
