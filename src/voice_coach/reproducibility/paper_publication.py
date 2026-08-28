"""Offline, fail-closed publication linting for the experimental manuscript.

The audit in this module is deliberately narrower than a final-paper decision.
It verifies already-public aggregate paper sources, the existing manuscript
evidence receipt, citations, Markdown tables, local assets, pending markers,
and the canonical related-work URL ledger.  It never opens raw/restricted
data, environment files, or locked-test material and never edits the canonical
manuscript or evidence map.

The default audit performs no network requests.  Optional URL observations use
a separate, explicitly non-canonical schema and cannot satisfy this audit.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, NoReturn
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml  # type: ignore[import-untyped]


CONFIG_SCHEMA = "voice_coach.paper_publication_audit_config.v1"
RECEIPT_SCHEMA = "voice_coach.paper_publication_audit_receipt.v1"
COMMIT_SCHEMA = "voice_coach.paper_publication_audit_commit.v1"
NETWORK_OBSERVATION_SCHEMA = "voice_coach.paper_publication_network_observation.v1"
OUTPUT_PREFIX = ("artifacts", "reproducibility", "paper_publication")
CANONICAL_CONFIG_PATH = "configs/paper/publication_v1.yaml"
CANONICAL_OUTPUT_ROOT = "artifacts/reproducibility/paper_publication"
CANONICAL_COMMIT_NAME = "commit.json"
CANONICAL_RECEIPT_NAME = "publication-audit.json"
BLOCKED_CONFIG_STATUS = "blocked_retired_canonical_sources"
BLOCKER_CODE = "retired_canonical_sources"
BLOCKED_BOUNDARY_SOURCES = (
    "manuscript",
    "evidence_map",
    "source_ledger",
    "data_boundary",
)
BLOCKED_RETIRED_SOURCE_ROLES = ("manuscript_evidence_validation_document",)
PUBLICATION_LOCK_NAME = ".publish.lock"
NAMESPACE_LOCK_NAME = ".paper_publication.publish.lock"
GENERATION_MODE = 0o550
RECEIPT_MODE = 0o440
COMMIT_MODE = 0o440
LOCK_MODE = 0o600
SHA256_RE = re.compile(r"[0-9a-f]{64}")
PENDING_DEFAULT_RE = r"\[PENDING:[^\]]+\]"
MAX_CONFIG_BYTES = 1024 * 1024
MAX_SOURCE_BYTES = 8 * 1024 * 1024
FORBIDDEN_PATH_PARTS = {
    ".env",
    "credential",
    "credentials",
    "raw",
    "restricted",
    "secret",
    "secrets",
}


class PublicationAuditError(RuntimeError):
    """Raised when publication evidence is incomplete or unsafe."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: yaml.Loader,
    node: yaml.Node,
    deep: bool = False,
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in output:
            _fail("config_duplicate_key")
        output[key] = loader.construct_object(value_node, deep=deep)
    return output


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True)
class FileRecord:
    """A stable workspace-relative file fingerprint."""

    path: str
    sha256: str
    size_bytes: int

    def as_dict(self) -> dict[str, str | int]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class _Config:
    path: Path
    record: FileRecord
    document: Mapping[str, Any]


@dataclass(frozen=True)
class PublicationBoundary:
    """Held directory identities for one serialized publication transaction."""

    namespace_parent: Path
    namespace_parent_descriptor: int
    namespace_parent_identity: tuple[int, int]
    output_root: Path
    output_descriptor: int
    output_identity: tuple[int, int]

    def bound_output_path(self, *parts: str) -> Path:
        return Path(f"/proc/self/fd/{self.output_descriptor}").joinpath(*parts)


Requester = Callable[[str, float], tuple[int, str]]


def _fail(code: str) -> NoReturn:
    raise PublicationAuditError(code)


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _list(value: object, code: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(code)
    return value


def _string(value: object, code: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(code)
    return value


def _integer(value: object, code: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(code)
    return value


def _boolean(value: object, code: str) -> bool:
    if not isinstance(value, bool):
        _fail(code)
    return value


def _expect_keys(value: Mapping[str, Any], expected: set[str], code: str) -> None:
    if set(value) != expected:
        _fail(code)


def _reject_nonfinite(value: Any, code: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        _fail(code)
    if isinstance(value, Mapping):
        for child in value.values():
            _reject_nonfinite(child, code)
    elif isinstance(value, list):
        for child in value:
            _reject_nonfinite(child, code)


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _pretty_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_relative_path(configured: object, code: str) -> Path:
    text = _string(configured, code)
    path = Path(text)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        _fail(code)
    lowered = [part.lower() for part in path.parts]
    if any(part in FORBIDDEN_PATH_PARTS or part.startswith(".env") for part in lowered):
        _fail(f"{code}_forbidden")
    return path


def _workspace_candidate(
    root: Path,
    configured: object,
    *,
    code: str,
    must_exist: bool,
) -> tuple[Path, str]:
    relative = _safe_relative_path(configured, code)
    candidate = root.joinpath(relative)
    current = root
    for index, part in enumerate(relative.parts):
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            if must_exist or index != len(relative.parts) - 1:
                _fail(f"{code}_missing")
            break
        except OSError:
            _fail(f"{code}_lstat")
        if stat.S_ISLNK(metadata.st_mode):
            _fail(f"{code}_symlink")
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
            _fail(f"{code}_parent_not_directory")
    try:
        candidate.resolve(strict=must_exist).relative_to(root)
    except (OSError, ValueError):
        _fail(f"{code}_outside_workspace")
    return candidate, relative.as_posix()


def _read_file_stable(
    root: Path,
    configured: object,
    *,
    code: str,
    maximum_bytes: int = MAX_SOURCE_BYTES,
    expected_mode: int | None = None,
    require_nlink_one: bool = False,
) -> tuple[bytes, FileRecord]:
    path, relative = _workspace_candidate(
        root,
        configured,
        code=code,
        must_exist=True,
    )
    try:
        path_before = path.lstat()
    except OSError:
        _fail(f"{code}_lstat")
    if stat.S_ISLNK(path_before.st_mode) or not stat.S_ISREG(path_before.st_mode):
        _fail(f"{code}_not_regular")
    if expected_mode is not None and stat.S_IMODE(path_before.st_mode) != expected_mode:
        _fail(f"{code}_mode")
    if require_nlink_one and path_before.st_nlink != 1:
        _fail(f"{code}_nlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _fail(f"{code}_open")
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            _fail(f"{code}_not_regular")
        path_identity_before = (
            path_before.st_dev,
            path_before.st_ino,
            path_before.st_mode,
            path_before.st_nlink,
            path_before.st_size,
            path_before.st_mtime_ns,
            path_before.st_ctime_ns,
        )
        descriptor_identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        if path_identity_before != descriptor_identity_before:
            _fail(f"{code}_changed_before_open")
        if expected_mode is not None and stat.S_IMODE(before.st_mode) != expected_mode:
            _fail(f"{code}_mode")
        if require_nlink_one and before.st_nlink != 1:
            _fail(f"{code}_nlink")
        if before.st_size > maximum_bytes:
            _fail(f"{code}_too_large")
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        if len(value) > maximum_bytes:
            _fail(f"{code}_too_large")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
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
    if identity_before != identity_after or len(value) != before.st_size:
        _fail(f"{code}_changed_while_reading")
    try:
        final = path.lstat()
    except OSError:
        _fail(f"{code}_post_lstat")
    if (
        stat.S_ISLNK(final.st_mode)
        or (
            final.st_dev,
            final.st_ino,
            final.st_mode,
            final.st_nlink,
            final.st_size,
            final.st_mtime_ns,
            final.st_ctime_ns,
        )
        != identity_after
    ):
        _fail(f"{code}_changed_after_read")
    return (
        value,
        FileRecord(
            path=relative,
            sha256=_sha256_bytes(value),
            size_bytes=len(value),
        ),
    )


def _read_bound_file_stable(
    actual_path: Path,
    canonical_relative: str | Path,
    *,
    code: str,
    maximum_bytes: int = MAX_SOURCE_BYTES,
    expected_mode: int | None = None,
    require_nlink_one: bool = False,
) -> tuple[bytes, FileRecord]:
    """Read through a held output dirfd while retaining the canonical record path."""

    relative = _safe_relative_path(
        Path(canonical_relative).as_posix(),
        f"{code}_path",
    ).as_posix()
    try:
        path_before = actual_path.lstat()
    except OSError:
        _fail(f"{code}_lstat")
    if stat.S_ISLNK(path_before.st_mode):
        _fail(f"{code}_symlink")
    if not stat.S_ISREG(path_before.st_mode):
        _fail(f"{code}_not_regular")
    if expected_mode is not None and stat.S_IMODE(path_before.st_mode) != expected_mode:
        _fail(f"{code}_mode")
    if require_nlink_one and path_before.st_nlink != 1:
        _fail(f"{code}_nlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(actual_path, flags)
    except OSError:
        _fail(f"{code}_open")
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            _fail(f"{code}_not_regular")
        path_identity_before = (
            path_before.st_dev,
            path_before.st_ino,
            path_before.st_mode,
            path_before.st_nlink,
            path_before.st_size,
            path_before.st_mtime_ns,
            path_before.st_ctime_ns,
        )
        descriptor_identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        if path_identity_before != descriptor_identity_before:
            _fail(f"{code}_changed_before_open")
        if expected_mode is not None and stat.S_IMODE(before.st_mode) != expected_mode:
            _fail(f"{code}_mode")
        if require_nlink_one and before.st_nlink != 1:
            _fail(f"{code}_nlink")
        if before.st_size > maximum_bytes:
            _fail(f"{code}_too_large")
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        if len(value) > maximum_bytes:
            _fail(f"{code}_too_large")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
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
    if identity_before != identity_after or len(value) != before.st_size:
        _fail(f"{code}_changed_while_reading")
    try:
        final = actual_path.lstat()
    except OSError:
        _fail(f"{code}_post_lstat")
    if (
        stat.S_ISLNK(final.st_mode)
        or (
            final.st_dev,
            final.st_ino,
            final.st_mode,
            final.st_nlink,
            final.st_size,
            final.st_mtime_ns,
            final.st_ctime_ns,
        )
        != identity_after
    ):
        _fail(f"{code}_changed_after_read")
    return (
        value,
        FileRecord(
            path=relative,
            sha256=_sha256_bytes(value),
            size_bytes=len(value),
        ),
    )


def _expected_record(value: object, code: str) -> FileRecord:
    record = _mapping(value, f"{code}_record")
    _expect_keys(record, {"path", "sha256", "size_bytes"}, f"{code}_keys")
    path = _safe_relative_path(record.get("path"), f"{code}_path").as_posix()
    digest = _string(record.get("sha256"), f"{code}_sha256")
    if SHA256_RE.fullmatch(digest) is None:
        _fail(f"{code}_sha256")
    size_bytes = _integer(record.get("size_bytes"), f"{code}_size", minimum=1)
    return FileRecord(path=path, sha256=digest, size_bytes=size_bytes)


def _read_source_record(
    root: Path,
    value: object,
    *,
    code: str,
    maximum_bytes: int = MAX_SOURCE_BYTES,
) -> tuple[bytes, FileRecord]:
    expected = _expected_record(value, code)
    content, actual = _read_file_stable(
        root,
        expected.path,
        code=code,
        maximum_bytes=maximum_bytes,
    )
    if actual != expected:
        _fail(f"{code}_fingerprint")
    return content, actual


def _decode_utf8(value: bytes, code: str) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeError:
        _fail(f"{code}_utf8")


def _json_no_duplicates(value: bytes, code: str) -> Mapping[str, Any]:
    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, child in pairs:
            if key in output:
                _fail(f"{code}_duplicate_key")
            output[key] = child
        return output

    def reject_constant(_: str) -> NoReturn:
        _fail(f"{code}_nonfinite")

    try:
        document = json.loads(
            value,
            object_pairs_hook=pairs_hook,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, UnicodeError):
        _fail(f"{code}_json")
    result = _mapping(document, f"{code}_root")
    _reject_nonfinite(result, f"{code}_nonfinite")
    return result


def _load_config(
    config_path: str | Path,
    *,
    workspace_root: str | Path,
) -> tuple[Path, _Config]:
    try:
        root = Path(workspace_root).resolve(strict=True)
    except OSError:
        _fail("workspace_root")
    if not root.is_dir():
        _fail("workspace_root")
    configured = Path(config_path)
    if configured.is_absolute():
        try:
            configured = configured.relative_to(root)
        except ValueError:
            _fail("config_path")
    value, record = _read_file_stable(
        root,
        configured.as_posix(),
        code="config",
        maximum_bytes=MAX_CONFIG_BYTES,
    )
    try:
        document = yaml.load(_decode_utf8(value, "config"), Loader=_UniqueKeyLoader)
    except PublicationAuditError:
        raise
    except yaml.YAMLError:
        _fail("config_yaml")
    config = _mapping(document, "config_root")
    _reject_nonfinite(config, "config_nonfinite")
    _validate_config_document(config)
    loaded = _Config(path=root / record.path, record=record, document=config)
    _assert_canonical_publication_namespace(loaded)
    return root, loaded


def _publication_namespace(loaded: _Config) -> dict[str, str]:
    output = _mapping(loaded.document.get("output"), "output")
    return {
        "config_path": loaded.record.path,
        "config_sha256": loaded.record.sha256,
        "output_root": _string(output.get("root"), "output_root"),
        "commit_name": _string(output.get("commit_name"), "output_commit_name"),
        "receipt_name": _string(output.get("receipt_name"), "output_receipt_name"),
    }


def _assert_canonical_publication_namespace(loaded: _Config) -> None:
    namespace = _publication_namespace(loaded)
    expected = {
        "config_path": CANONICAL_CONFIG_PATH,
        "config_sha256": loaded.record.sha256,
        "output_root": CANONICAL_OUTPUT_ROOT,
        "commit_name": CANONICAL_COMMIT_NAME,
        "receipt_name": CANONICAL_RECEIPT_NAME,
    }
    if namespace != expected:
        _fail("publication_namespace")


def _validate_blocked_config(config: Mapping[str, Any]) -> None:
    _expect_keys(
        config,
        {
            "schema_version",
            "status",
            "blocker",
            "current_boundaries",
            "completion_state",
            "output",
        },
        "blocked_config_keys",
    )
    blocker = _mapping(config.get("blocker"), "blocker")
    _expect_keys(
        blocker,
        {"code", "retired_source_roles", "evidence_status", "explanation"},
        "blocker_keys",
    )
    if blocker.get("code") != BLOCKER_CODE:
        _fail("blocker_code")
    roles = tuple(
        _string(item, "retired_source_role")
        for item in _list(blocker.get("retired_source_roles"), "retired_source_roles")
    )
    if roles != BLOCKED_RETIRED_SOURCE_ROLES:
        _fail("retired_source_roles")
    if blocker.get("evidence_status") != "unresolved":
        _fail("blocker_evidence_status")
    _string(blocker.get("explanation"), "blocker_explanation")

    boundaries = _mapping(config.get("current_boundaries"), "current_boundaries")
    if tuple(boundaries) != BLOCKED_BOUNDARY_SOURCES:
        _fail("current_boundary_order")
    for name in BLOCKED_BOUNDARY_SOURCES:
        _expected_record(boundaries.get(name), f"current_boundary_{name}")

    completion = _mapping(config.get("completion_state"), "completion_state")
    _expect_keys(
        completion,
        {
            "paper_complete",
            "submission_status",
            "manual_approval_required",
            "manual_approval_status",
        },
        "completion_state_keys",
    )
    if (
        completion.get("paper_complete") is not False
        or completion.get("submission_status") != "not_submitted"
        or completion.get("manual_approval_required") is not True
        or completion.get("manual_approval_status") != "not_approved"
    ):
        _fail("completion_state")

    output = _mapping(config.get("output"), "output")
    _expect_keys(output, {"root", "commit_name", "receipt_name"}, "output_keys")
    if output != {
        "root": CANONICAL_OUTPUT_ROOT,
        "commit_name": CANONICAL_COMMIT_NAME,
        "receipt_name": CANONICAL_RECEIPT_NAME,
    }:
        _fail("blocked_output")


def _validate_config_document(config: Mapping[str, Any]) -> None:
    if config.get("schema_version") != CONFIG_SCHEMA:
        _fail("config_schema")
    if config.get("status") == BLOCKED_CONFIG_STATUS:
        _validate_blocked_config(config)
        return

    _expect_keys(
        config,
        {
            "schema_version",
            "status",
            "policy",
            "implementation",
            "evidence_gate",
            "canonical",
            "citation_contract",
            "external_url_contract",
            "output",
        },
        "config_keys",
    )
    if config.get("status") != "ready":
        _fail("config_not_ready")

    policy = _mapping(config.get("policy"), "policy")
    _expect_keys(
        policy,
        {
            "offline_default",
            "network_is_completion_evidence",
            "canonical_write_allowed",
            "expected_markdown_tables",
            "pending_marker_regex",
            "expected_pending_count",
            "expected_pending_markers_sha256",
        },
        "policy_keys",
    )
    if (
        _boolean(policy.get("offline_default"), "policy_offline") is not True
        or _boolean(
            policy.get("network_is_completion_evidence"),
            "policy_network_evidence",
        )
        is not False
        or _boolean(
            policy.get("canonical_write_allowed"),
            "policy_canonical_write",
        )
        is not False
    ):
        _fail("policy_fail_closed")
    _integer(policy.get("expected_markdown_tables"), "policy_table_count", minimum=1)
    marker_regex = _string(
        policy.get("pending_marker_regex"),
        "policy_pending_regex",
    )
    try:
        re.compile(marker_regex)
    except re.error:
        _fail("policy_pending_regex")
    _integer(
        policy.get("expected_pending_count"),
        "policy_pending_count",
        minimum=1,
    )
    marker_hash = _string(
        policy.get("expected_pending_markers_sha256"),
        "policy_pending_hash",
    )
    if SHA256_RE.fullmatch(marker_hash) is None:
        _fail("policy_pending_hash")

    implementation = _mapping(config.get("implementation"), "implementation")
    _expect_keys(implementation, {"module", "cli"}, "implementation_keys")
    _expected_record(implementation.get("module"), "implementation_module")
    _expected_record(implementation.get("cli"), "implementation_cli")

    gate = _mapping(config.get("evidence_gate"), "evidence_gate")
    _expect_keys(
        gate,
        {
            "report",
            "validator_script",
            "validator_module",
            "validation_document",
            "expected",
        },
        "evidence_gate_keys",
    )
    for name in (
        "report",
        "validator_script",
        "validator_module",
        "validation_document",
    ):
        _expected_record(gate.get(name), f"evidence_{name}")
    expected_gate = _mapping(gate.get("expected"), "evidence_expected")
    _expect_keys(
        expected_gate,
        {
            "status",
            "artifact_count",
            "claim_count",
            "pending_count",
            "pending_markers_sha256",
            "locked_guard_status",
            "registry_metric_splits",
        },
        "evidence_expected_keys",
    )
    if (
        expected_gate.get("status") != "passed"
        or expected_gate.get("locked_guard_status") != "passed"
    ):
        _fail("evidence_expected_status")
    _integer(expected_gate.get("artifact_count"), "evidence_artifact_count", minimum=1)
    _integer(expected_gate.get("claim_count"), "evidence_claim_count", minimum=1)
    _integer(expected_gate.get("pending_count"), "evidence_pending_count", minimum=1)
    expected_pending_hash = _string(
        expected_gate.get("pending_markers_sha256"),
        "evidence_pending_hash",
    )
    if SHA256_RE.fullmatch(expected_pending_hash) is None:
        _fail("evidence_pending_hash")
    splits = _list(
        expected_gate.get("registry_metric_splits"),
        "evidence_registry_splits",
    )
    if (
        not splits
        or any(not isinstance(item, str) or not item for item in splits)
        or splits != sorted(set(splits))
    ):
        _fail("evidence_registry_splits")

    canonical = _mapping(config.get("canonical"), "canonical")
    _expect_keys(
        canonical,
        {"manuscript", "evidence_map", "source_ledger", "local_assets"},
        "canonical_keys",
    )
    for name in ("manuscript", "evidence_map", "source_ledger"):
        _expected_record(canonical.get(name), f"canonical_{name}")
    assets = _list(canonical.get("local_assets"), "canonical_local_assets")
    if not assets:
        _fail("canonical_local_assets")
    targets: list[str] = []
    paths: list[str] = []
    for index, item in enumerate(assets):
        asset = _mapping(item, f"asset_{index}")
        _expect_keys(asset, {"link_target", "source"}, f"asset_{index}_keys")
        target = _string(asset.get("link_target"), f"asset_{index}_target")
        if "://" in target or target.startswith(("/", "#")):
            _fail(f"asset_{index}_target")
        targets.append(target)
        paths.append(_expected_record(asset.get("source"), f"asset_{index}").path)
    if len(targets) != len(set(targets)) or len(paths) != len(set(paths)):
        _fail("asset_duplicate")

    citations = _mapping(config.get("citation_contract"), "citation_contract")
    _expect_keys(
        citations,
        {
            "expected_reference_count",
            "expected_definition_sequence",
            "expected_first_use_order",
            "expected_primary_targets",
        },
        "citation_contract_keys",
    )
    count = _integer(
        citations.get("expected_reference_count"),
        "citation_count",
        minimum=1,
    )
    expected_sequence = _integer_sequence(
        citations.get("expected_definition_sequence"),
        "citation_definition_sequence",
    )
    if expected_sequence != list(range(1, count + 1)):
        _fail("citation_definition_sequence")
    first_use = _integer_sequence(
        citations.get("expected_first_use_order"),
        "citation_first_use_order",
    )
    if len(first_use) != count or set(first_use) != set(expected_sequence):
        _fail("citation_first_use_order")
    targets_map = _mapping(
        citations.get("expected_primary_targets"),
        "citation_primary_targets",
    )
    if set(targets_map) != {str(number) for number in expected_sequence}:
        _fail("citation_primary_target_keys")
    target_values = [
        _https_url(targets_map[str(number)], f"citation_target_{number}")
        for number in expected_sequence
    ]
    if len(target_values) != len(set(target_values)):
        _fail("citation_primary_target_duplicate")

    external = _mapping(
        config.get("external_url_contract"),
        "external_url_contract",
    )
    _expect_keys(
        external,
        {"expected_count", "expected_status_counts", "records"},
        "external_url_contract_keys",
    )
    url_count = _integer(external.get("expected_count"), "external_count", minimum=1)
    status_counts = _status_counts(
        external.get("expected_status_counts"),
        "external_status_counts",
    )
    records = _list(external.get("records"), "external_records")
    if len(records) != url_count:
        _fail("external_record_count")
    urls: list[str] = []
    observed_counts: dict[int, int] = {}
    for index, item in enumerate(records):
        record = _mapping(item, f"external_record_{index}")
        _expect_keys(
            record,
            {"url", "expected_status"},
            f"external_record_{index}_keys",
        )
        urls.append(_https_url(record.get("url"), f"external_url_{index}"))
        status = _integer(
            record.get("expected_status"),
            f"external_status_{index}",
            minimum=100,
        )
        if status > 599:
            _fail(f"external_status_{index}")
        observed_counts[status] = observed_counts.get(status, 0) + 1
    if urls != sorted(set(urls)):
        _fail("external_urls_not_sorted_unique")
    if observed_counts != status_counts:
        _fail("external_status_count_mismatch")
    if not set(target_values).issubset(set(urls)):
        _fail("citation_target_not_in_ledger")

    output = _mapping(config.get("output"), "output")
    _expect_keys(
        output,
        {"root", "commit_name", "receipt_name"},
        "output_keys",
    )
    root_path = _safe_relative_path(output.get("root"), "output_root")
    if root_path.parts[: len(OUTPUT_PREFIX)] != OUTPUT_PREFIX:
        _fail("output_root_prefix")
    for name in ("commit_name", "receipt_name"):
        filename = _string(output.get(name), f"output_{name}")
        if Path(filename).name != filename or not filename.endswith(".json"):
            _fail(f"output_{name}")


def _integer_sequence(value: object, code: str) -> list[int]:
    sequence = _list(value, code)
    return [
        _integer(item, f"{code}_{index}", minimum=1)
        for index, item in enumerate(sequence)
    ]


def _https_url(value: object, code: str) -> str:
    url = _string(value, code)
    if (
        not url.startswith("https://")
        or any(character.isspace() for character in url)
        or "@" in url.split("/", 3)[2]
        or "#" in url
    ):
        _fail(code)
    return url


def _status_counts(value: object, code: str) -> dict[int, int]:
    raw = _mapping(value, code)
    result: dict[int, int] = {}
    for key, child in raw.items():
        if not isinstance(key, (str, int)) or not str(key).isdigit():
            _fail(code)
        status = int(key)
        if status < 100 or status > 599 or status in result:
            _fail(code)
        result[status] = _integer(child, code, minimum=1)
    return result


def validate_config_only(
    *,
    config_path: str | Path,
    workspace_root: str | Path,
) -> Mapping[str, Any]:
    """Validate static configuration without reading any referenced source."""

    _, config = _load_config(config_path, workspace_root=workspace_root)
    blocked = config.document.get("status") == BLOCKED_CONFIG_STATUS
    source_record_count = (
        len(BLOCKED_BOUNDARY_SOURCES)
        if blocked
        else (
            2
            + 4
            + 3
            + len(
                _list(
                    _mapping(config.document.get("canonical"), "canonical").get(
                        "local_assets"
                    ),
                    "canonical_local_assets",
                )
            )
        )
    )
    return {
        "schema_version": CONFIG_SCHEMA,
        "status": BLOCKED_CONFIG_STATUS if blocked else "valid_config_only",
        "blocker": BLOCKER_CODE if blocked else None,
        "config": config.record.as_dict(),
        "source_record_schemas_validated": source_record_count,
        "actual_state": {
            "referenced_source_files_read": 0,
            "canonical_files_read": 0,
            "local_asset_files_read": 0,
            "raw_files_read": 0,
            "environment_files_read": 0,
            "secret_files_read": 0,
            "locked_test_files_read": 0,
            "network_requests": 0,
            "outputs_written": 0,
        },
    }


def _validate_evidence_gate(
    root: Path,
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], list[FileRecord]]:
    gate = _mapping(config.get("evidence_gate"), "evidence_gate")
    expected = _mapping(gate.get("expected"), "evidence_expected")

    # The completed evidence report is intentionally the first referenced
    # source opened by a production audit.
    report_bytes, report_record = _read_source_record(
        root,
        gate.get("report"),
        code="evidence_report",
    )
    report = _json_no_duplicates(report_bytes, "evidence_report")
    if report.get("status") != expected.get("status"):
        _fail("evidence_report_status")
    summary = _mapping(report.get("summary"), "evidence_report_summary")
    if (
        summary.get("artifact_count") != expected.get("artifact_count")
        or summary.get("artifact_passed") != expected.get("artifact_count")
        or summary.get("claim_count") != expected.get("claim_count")
        or summary.get("claim_passed") != expected.get("claim_count")
    ):
        _fail("evidence_report_summary")
    pending = _mapping(
        report.get("pending_contract"),
        "evidence_report_pending",
    )
    if (
        pending.get("status") != "passed"
        or pending.get("marker_count") != expected.get("pending_count")
        or pending.get("markers_sha256") != expected.get("pending_markers_sha256")
        or pending.get("errors") != []
    ):
        _fail("evidence_report_pending")
    locked = _mapping(
        report.get("locked_test_guard"),
        "evidence_report_locked",
    )
    accessed = _mapping(
        locked.get("accessed"),
        "evidence_report_locked_accessed",
    )
    if (
        locked.get("status") != expected.get("locked_guard_status")
        or locked.get("errors") != []
        or not accessed
        or any(value is not False for value in accessed.values())
    ):
        _fail("evidence_report_locked")
    splits = locked.get("registry_metric_splits")
    if not isinstance(splits, list) or sorted(set(splits)) != _list(
        expected.get("registry_metric_splits"),
        "evidence_registry_splits",
    ):
        _fail("evidence_report_registry_splits")
    privacy = _mapping(report.get("privacy"), "evidence_report_privacy")
    if privacy != {
        "aggregate_only": True,
        "environment_files_read": False,
        "raw_data_read": False,
    }:
        _fail("evidence_report_privacy")

    canonical = _mapping(config.get("canonical"), "canonical")
    expected_manuscript = _expected_record(
        canonical.get("manuscript"),
        "canonical_manuscript",
    )
    expected_map = _expected_record(
        canonical.get("evidence_map"),
        "canonical_evidence_map",
    )
    manuscript = _mapping(report.get("manuscript"), "evidence_report_manuscript")
    evidence_map = _mapping(
        report.get("evidence_map"),
        "evidence_report_evidence_map",
    )
    if manuscript != {
        "path": expected_manuscript.path,
        "sha256": expected_manuscript.sha256,
    }:
        _fail("evidence_report_manuscript_binding")
    if evidence_map != {
        "path": expected_map.path,
        "sha256": expected_map.sha256,
    }:
        _fail("evidence_report_map_binding")

    records = [report_record]
    for name in ("validator_script", "validator_module", "validation_document"):
        _, record = _read_source_record(
            root,
            gate.get(name),
            code=f"evidence_{name}",
        )
        records.append(record)
    return (
        {
            "status": "passed",
            "report": report_record.as_dict(),
            "artifact_count": summary.get("artifact_count"),
            "claim_count": summary.get("claim_count"),
            "pending_count": pending.get("marker_count"),
            "pending_markers_sha256": pending.get("markers_sha256"),
            "locked_guard_status": locked.get("status"),
            "locked_access_check_count": len(accessed),
            "locked_access_all_false": True,
            "registry_metric_splits": sorted(set(splits)),
        },
        records,
    )


_MARKDOWN_LINK_RE = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)\)")
_CITATION_RE = re.compile(r"\[((?:\d+\s*(?:[,;\-–]\s*\d+\s*)*))\](?!\()")
_REFERENCE_RE = re.compile(r"^([1-9]\d*)\.\s+", flags=re.MULTILINE)
_TABLE_DELIMITER_RE = re.compile(r"^:?-{3,}:?$")
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")


def _markdown_links(text: str) -> list[str]:
    return [match.group(1) for match in _MARKDOWN_LINK_RE.finditer(text)]


def _expand_citation(token: str) -> list[int]:
    output: list[int] = []
    for group in re.split(r"\s*[,;]\s*", token.strip()):
        if not group:
            _fail("citation_empty")
        range_match = re.fullmatch(r"(\d+)\s*[\-–]\s*(\d+)", group)
        if range_match:
            start, end = map(int, range_match.groups())
            if end < start:
                _fail("citation_descending_range")
            output.extend(range(start, end + 1))
        elif group.isdigit():
            output.append(int(group))
        else:
            _fail("citation_syntax")
    if len(output) != len(set(output)):
        _fail("citation_duplicate_within_marker")
    return output


def _split_pipe_row(line: str) -> list[str] | None:
    stripped = line.strip()
    if "|" not in stripped:
        return None
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    in_code = False
    for character in stripped:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            current.append(character)
            escaped = True
        elif character == "`":
            in_code = not in_code
            current.append(character)
        elif character == "|" and not in_code:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    cells.append("".join(current).strip())
    if stripped.startswith("|"):
        cells = cells[1:]
    if stripped.endswith("|"):
        cells = cells[:-1]
    return cells


def _parse_markdown_tables(text: str) -> list[dict[str, int]]:
    lines = text.splitlines()
    tables: list[dict[str, int]] = []
    index = 0
    active_fence: str | None = None
    while index < len(lines):
        fence = _FENCE_RE.match(lines[index])
        if fence:
            marker = fence.group(1)[0]
            if active_fence is None:
                active_fence = marker
            elif active_fence == marker:
                active_fence = None
            index += 1
            continue
        if active_fence is not None or index + 1 >= len(lines):
            index += 1
            continue
        header = _split_pipe_row(lines[index])
        delimiter = _split_pipe_row(lines[index + 1])
        if (
            header is None
            or delimiter is None
            or len(header) == 0
            or len(header) != len(delimiter)
            or any(_TABLE_DELIMITER_RE.fullmatch(cell) is None for cell in delimiter)
        ):
            index += 1
            continue
        row_count = 0
        cursor = index + 2
        while cursor < len(lines):
            row = _split_pipe_row(lines[cursor])
            if row is None:
                break
            if len(row) != len(header):
                _fail("markdown_table_column_mismatch")
            row_count += 1
            cursor += 1
        if row_count == 0:
            _fail("markdown_table_without_rows")
        tables.append(
            {
                "start_line": index + 1,
                "column_count": len(header),
                "body_row_count": row_count,
            }
        )
        index = cursor
    if active_fence is not None:
        _fail("markdown_unclosed_fence")
    return tables


def _canonical_string_hash(values: Iterable[str]) -> str:
    return _sha256_bytes("\n".join(sorted(values)).encode("utf-8"))


def _citation_audit(
    manuscript: str,
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], set[str], list[str]]:
    if manuscript.count("## 참고문헌") != 1 or manuscript.count("## 부록") != 1:
        _fail("reference_heading_count")
    body, tail = manuscript.split("## 참고문헌", 1)
    references_text, _ = tail.split("## 부록", 1)
    definitions = [
        (int(match.group(1)), match.start())
        for match in _REFERENCE_RE.finditer(references_text)
    ]
    citation_config = _mapping(
        config.get("citation_contract"),
        "citation_contract",
    )
    expected_sequence = _integer_sequence(
        citation_config.get("expected_definition_sequence"),
        "citation_definition_sequence",
    )
    definition_numbers = [number for number, _ in definitions]
    if definition_numbers != expected_sequence:
        _fail("reference_definition_order")
    if len(definition_numbers) != len(set(definition_numbers)):
        _fail("reference_definition_duplicate")

    reference_urls: dict[int, list[str]] = {}
    for index, (number, start) in enumerate(definitions):
        end = (
            definitions[index + 1][1]
            if index + 1 < len(definitions)
            else len(references_text)
        )
        reference_urls[number] = [
            target
            for target in _markdown_links(references_text[start:end])
            if target.startswith("https://")
        ]
    configured_targets = _mapping(
        citation_config.get("expected_primary_targets"),
        "citation_primary_targets",
    )
    primary_targets: list[str] = []
    for number in expected_sequence:
        target = _string(
            configured_targets.get(str(number)),
            f"citation_target_{number}",
        )
        if reference_urls[number].count(target) != 1:
            _fail("reference_primary_target")
        primary_targets.append(target)
    if len(primary_targets) != len(set(primary_targets)):
        _fail("reference_primary_target_duplicate")

    citation_occurrences: list[int] = []
    first_use: list[int] = []
    seen: set[int] = set()
    for match in _CITATION_RE.finditer(body):
        expanded = _expand_citation(match.group(1))
        citation_occurrences.extend(expanded)
        for number in expanded:
            if number not in seen:
                first_use.append(number)
                seen.add(number)
    defined = set(expected_sequence)
    if set(citation_occurrences) - defined:
        _fail("citation_undefined")
    if defined - set(citation_occurrences):
        _fail("citation_unused_reference")
    expected_first_use = _integer_sequence(
        citation_config.get("expected_first_use_order"),
        "citation_first_use_order",
    )
    if first_use != expected_first_use:
        _fail("citation_first_use_order")

    manuscript_urls = {
        target
        for target in _markdown_links(manuscript)
        if target.startswith("https://")
    }
    return (
        {
            "status": "passed",
            "reference_count": len(definition_numbers),
            "definition_sequence": definition_numbers,
            "citation_occurrence_count": len(citation_occurrences),
            "used_reference_count": len(set(citation_occurrences)),
            "undefined_reference_count": 0,
            "unused_reference_count": 0,
            "duplicate_definition_count": 0,
            "duplicate_primary_target_count": 0,
            "first_use_order": first_use,
            "primary_targets_sha256": _canonical_string_hash(primary_targets),
        },
        manuscript_urls,
        _markdown_links(manuscript),
    )


def _pending_audit(
    manuscript: str,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    policy = _mapping(config.get("policy"), "policy")
    regex = _string(policy.get("pending_marker_regex"), "policy_pending_regex")
    markers = re.findall(regex, manuscript, flags=re.MULTILINE)
    if markers and isinstance(markers[0], tuple):
        markers = ["".join(marker) for marker in markers]
    digest = _canonical_string_hash(markers)
    if len(markers) != policy.get("expected_pending_count") or digest != policy.get(
        "expected_pending_markers_sha256"
    ):
        _fail("pending_contract")
    return {
        "status": "passed",
        "marker_count": len(markers),
        "markers_sha256": digest,
        "markers_preserved": True,
    }


def _local_asset_audit(
    root: Path,
    manuscript_path: str,
    markdown_links: Sequence[str],
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], list[FileRecord]]:
    canonical = _mapping(config.get("canonical"), "canonical")
    configured_assets = _list(
        canonical.get("local_assets"),
        "canonical_local_assets",
    )
    local_targets = [
        target
        for target in markdown_links
        if not target.startswith(("https://", "http://", "mailto:", "#"))
    ]
    expected_targets = [
        _string(
            _mapping(item, f"asset_{index}").get("link_target"),
            f"asset_{index}_target",
        )
        for index, item in enumerate(configured_assets)
    ]
    if local_targets != expected_targets:
        _fail("local_asset_link_order")
    manuscript_parent = Path(manuscript_path).parent
    records: list[FileRecord] = []
    for index, item in enumerate(configured_assets):
        asset = _mapping(item, f"asset_{index}")
        target = expected_targets[index]
        if any(part in {"", ".", ".."} for part in Path(target).parts):
            _fail(f"asset_{index}_target")
        expected_path = (manuscript_parent / target).as_posix()
        source = _expected_record(asset.get("source"), f"asset_{index}")
        if source.path != expected_path:
            _fail(f"asset_{index}_binding")
        _, actual = _read_source_record(
            root,
            asset.get("source"),
            code=f"asset_{index}",
        )
        records.append(actual)
    return (
        {
            "status": "passed",
            "asset_count": len(records),
            "assets": [record.as_dict() for record in records],
        },
        records,
    )


def _external_url_audit(
    ledger: str,
    manuscript_urls: set[str],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    contract = _mapping(
        config.get("external_url_contract"),
        "external_url_contract",
    )
    records = _list(contract.get("records"), "external_records")
    expected_urls = [
        _string(
            _mapping(item, f"external_record_{index}").get("url"),
            f"external_url_{index}",
        )
        for index, item in enumerate(records)
    ]
    ledger_urls = sorted(
        {target for target in _markdown_links(ledger) if target.startswith("https://")}
    )
    if ledger_urls != expected_urls:
        _fail("external_ledger_url_set")
    if not manuscript_urls.issubset(set(expected_urls)):
        _fail("manuscript_url_not_in_ledger")
    status_counts = _status_counts(
        contract.get("expected_status_counts"),
        "external_status_counts",
    )
    if (
        f"{status_counts.get(200, 0)}개는 HTTP 200" not in ledger
        or f"HTTP {202}" not in ledger
        or status_counts.get(202) != 4
    ):
        _fail("external_ledger_status_statement")
    return {
        "status": "passed",
        "offline_validation": True,
        "network_requests": 0,
        "canonical_completion_evidence_from_network": False,
        "ledger_url_count": len(ledger_urls),
        "ledger_urls_sha256": _canonical_string_hash(ledger_urls),
        "expected_status_counts": {
            str(status): count for status, count in sorted(status_counts.items())
        },
        "manuscript_external_url_count": len(manuscript_urls),
        "manuscript_urls_are_ledger_subset": True,
    }


def _implementation_records(
    root: Path,
    config: Mapping[str, Any],
) -> list[FileRecord]:
    implementation = _mapping(config.get("implementation"), "implementation")
    records: list[FileRecord] = []
    for name in ("module", "cli"):
        _, record = _read_source_record(
            root,
            implementation.get(name),
            code=f"implementation_{name}",
        )
        records.append(record)
    return records


def _build_receipt(
    root: Path,
    loaded: _Config,
) -> tuple[dict[str, Any], list[FileRecord]]:
    config = loaded.document
    evidence_gate, evidence_records = _validate_evidence_gate(root, config)

    canonical = _mapping(config.get("canonical"), "canonical")
    manuscript_bytes, manuscript_record = _read_source_record(
        root,
        canonical.get("manuscript"),
        code="canonical_manuscript",
    )
    evidence_map_bytes, evidence_map_record = _read_source_record(
        root,
        canonical.get("evidence_map"),
        code="canonical_evidence_map",
    )
    ledger_bytes, ledger_record = _read_source_record(
        root,
        canonical.get("source_ledger"),
        code="canonical_source_ledger",
    )
    manuscript = _decode_utf8(manuscript_bytes, "canonical_manuscript")
    _decode_utf8(evidence_map_bytes, "canonical_evidence_map")
    ledger = _decode_utf8(ledger_bytes, "canonical_source_ledger")

    citation, manuscript_urls, markdown_links = _citation_audit(manuscript, config)
    tables = _parse_markdown_tables(manuscript)
    expected_table_count = _mapping(config.get("policy"), "policy").get(
        "expected_markdown_tables"
    )
    if len(tables) != expected_table_count:
        _fail("markdown_table_count")
    local_assets, asset_records = _local_asset_audit(
        root,
        manuscript_record.path,
        markdown_links,
        config,
    )
    pending = _pending_audit(manuscript, config)
    external = _external_url_audit(ledger, manuscript_urls, config)
    implementation_records = _implementation_records(root, config)

    all_records = [
        *evidence_records,
        manuscript_record,
        evidence_map_record,
        ledger_record,
        *asset_records,
        *implementation_records,
    ]
    if len({record.path for record in all_records}) != len(all_records):
        _fail("source_record_duplicate")
    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA,
        "status": "passed",
        "scope": "publication_static_audit_only",
        "publication_namespace": _publication_namespace(loaded),
        "config": loaded.record.as_dict(),
        "source_pins": [
            record.as_dict()
            for record in sorted(all_records, key=lambda item: item.path)
        ],
        "evidence_gate": evidence_gate,
        "citation_audit": citation,
        "markdown_audit": {
            "status": "passed",
            "table_count": len(tables),
            "tables": tables,
        },
        "local_asset_audit": local_assets,
        "pending_contract": pending,
        "locked_test_guard": {
            "status": evidence_gate["locked_guard_status"],
            "access_check_count": evidence_gate["locked_access_check_count"],
            "all_access_flags_false": True,
            "locked_test_files_read": 0,
        },
        "external_url_ledger": external,
        "network_observation": {
            "performed": False,
            "request_count": 0,
            "canonical_completion_evidence": False,
        },
        "privacy": {
            "aggregate_only": True,
            "raw_files_read": False,
            "environment_files_read": False,
            "secret_files_read": False,
            "locked_test_files_read": False,
        },
        "canonical_files_modified": False,
    }
    _reject_forbidden_receipt_paths(receipt)
    return receipt, all_records


def _reject_forbidden_receipt_paths(value: Any, *, key: str = "") -> None:
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            _reject_forbidden_receipt_paths(child, key=str(child_key))
    elif isinstance(value, list):
        for child in value:
            _reject_forbidden_receipt_paths(child, key=key)
    elif isinstance(value, str) and (key == "path" or key.endswith("_path")):
        _safe_relative_path(value, "receipt_path")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _ensure_safe_output_directory(root: Path, relative: Path) -> Path:
    if relative.parts[: len(OUTPUT_PREFIX)] != OUTPUT_PREFIX:
        _fail("output_root_prefix")
    path = root
    for part in relative.parts:
        path = path / part
        if path.exists():
            try:
                metadata = path.lstat()
            except OSError:
                _fail("output_root_lstat")
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                _fail("output_root_not_directory")
        else:
            try:
                path.mkdir(mode=0o750)
                _fsync_directory(path.parent)
            except FileExistsError:
                try:
                    metadata = path.lstat()
                except OSError:
                    _fail("output_root_lstat")
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                    _fail("output_root_not_directory")
            except OSError:
                _fail("output_root_create")
    return path


def _existing_safe_output_directory(root: Path, relative: Path) -> Path:
    path, _ = _workspace_candidate(
        root,
        relative.as_posix(),
        code="output_root",
        must_exist=True,
    )
    try:
        metadata = path.lstat()
    except OSError:
        _fail("output_root_lstat")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        _fail("output_root_not_directory")
    return path


@contextmanager
def _publication_lock(
    output_root: Path,
    *,
    exclusive: bool,
    create: bool,
) -> Iterator[PublicationBoundary]:
    namespace_parent = output_root.parent
    parent_descriptor, parent_identity = _open_bound_directory(
        namespace_parent,
        code="publication_parent",
    )
    namespace_lock_descriptor: int | None = None
    output_descriptor: int | None = None
    publication_lock_descriptor: int | None = None
    try:
        namespace_lock_descriptor = _acquire_bound_lock(
            parent_descriptor,
            NAMESPACE_LOCK_NAME,
            exclusive=exclusive,
            create=create,
            code="namespace_lock",
        )
        _assert_directory_binding(
            namespace_parent,
            parent_descriptor,
            parent_identity,
            code="publication_parent_rebound",
        )
        output_descriptor, output_identity = _open_bound_directory(
            output_root,
            code="output_root",
        )
        publication_lock_descriptor = _acquire_bound_lock(
            output_descriptor,
            PUBLICATION_LOCK_NAME,
            exclusive=exclusive,
            create=create,
            code="publication_lock",
        )
        boundary = PublicationBoundary(
            namespace_parent=namespace_parent,
            namespace_parent_descriptor=parent_descriptor,
            namespace_parent_identity=parent_identity,
            output_root=output_root,
            output_descriptor=output_descriptor,
            output_identity=output_identity,
        )
        _assert_publication_boundary(boundary)
        yield boundary
    finally:
        if publication_lock_descriptor is not None:
            try:
                fcntl.flock(publication_lock_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(publication_lock_descriptor)
        if output_descriptor is not None:
            os.close(output_descriptor)
        if namespace_lock_descriptor is not None:
            try:
                fcntl.flock(namespace_lock_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(namespace_lock_descriptor)
        os.close(parent_descriptor)


def _open_bound_directory(
    path: Path,
    *,
    code: str,
) -> tuple[int, tuple[int, int]]:
    try:
        path_before = path.lstat()
    except OSError:
        _fail(f"{code}_lstat")
    if stat.S_ISLNK(path_before.st_mode) or not stat.S_ISDIR(path_before.st_mode):
        _fail(f"{code}_not_directory")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _fail(f"{code}_open")
    try:
        descriptor_metadata = os.fstat(descriptor)
        identity = (descriptor_metadata.st_dev, descriptor_metadata.st_ino)
        if not stat.S_ISDIR(descriptor_metadata.st_mode) or identity != (
            path_before.st_dev,
            path_before.st_ino,
        ):
            _fail(f"{code}_changed")
        _assert_directory_binding(
            path,
            descriptor,
            identity,
            code=f"{code}_changed",
        )
        return descriptor, identity
    except BaseException:
        os.close(descriptor)
        raise


def _assert_directory_binding(
    path: Path,
    descriptor: int,
    identity: tuple[int, int],
    *,
    code: str,
) -> None:
    try:
        descriptor_metadata = os.fstat(descriptor)
        path_metadata = path.lstat()
    except OSError:
        _fail(code)
    if (
        stat.S_ISLNK(path_metadata.st_mode)
        or not stat.S_ISDIR(path_metadata.st_mode)
        or not stat.S_ISDIR(descriptor_metadata.st_mode)
        or (descriptor_metadata.st_dev, descriptor_metadata.st_ino) != identity
        or (path_metadata.st_dev, path_metadata.st_ino) != identity
    ):
        _fail(code)


def _assert_publication_boundary(boundary: PublicationBoundary) -> None:
    _assert_directory_binding(
        boundary.namespace_parent,
        boundary.namespace_parent_descriptor,
        boundary.namespace_parent_identity,
        code="publication_parent_rebound",
    )
    _assert_directory_binding(
        boundary.output_root,
        boundary.output_descriptor,
        boundary.output_identity,
        code="output_root_rebound",
    )


def _acquire_bound_lock(
    directory_descriptor: int,
    name: str,
    *,
    exclusive: bool,
    create: bool,
    code: str,
) -> int:
    lock_path = Path(f"/proc/self/fd/{directory_descriptor}") / name
    descriptor: int | None = None
    created = False
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    if create:
        try:
            descriptor = os.open(
                lock_path,
                flags | os.O_CREAT | os.O_EXCL,
                LOCK_MODE,
            )
            created = True
        except FileExistsError:
            pass
        except OSError:
            _fail(f"{code}_create")
    if descriptor is None:
        try:
            path_before = lock_path.lstat()
        except OSError:
            _fail(f"{code}_missing")
        if stat.S_ISLNK(path_before.st_mode) or not stat.S_ISREG(path_before.st_mode):
            _fail(f"{code}_not_regular")
        if stat.S_IMODE(path_before.st_mode) != LOCK_MODE:
            _fail(f"{code}_mode")
        if path_before.st_nlink != 1:
            _fail(f"{code}_nlink")
        try:
            descriptor = os.open(lock_path, flags)
        except OSError:
            _fail(f"{code}_open")
    try:
        if created:
            os.fchmod(descriptor, LOCK_MODE)
            os.fsync(descriptor)
            os.fsync(directory_descriptor)
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        try:
            fcntl.flock(descriptor, operation)
        except OSError:
            _fail(f"{code}_acquire")
        descriptor_metadata = os.fstat(descriptor)
        try:
            path_metadata = lock_path.lstat()
        except OSError:
            _fail(f"{code}_lstat")
        if (
            stat.S_ISLNK(path_metadata.st_mode)
            or not stat.S_ISREG(descriptor_metadata.st_mode)
            or not stat.S_ISREG(path_metadata.st_mode)
        ):
            _fail(f"{code}_not_regular")
        if (
            stat.S_IMODE(descriptor_metadata.st_mode) != LOCK_MODE
            or stat.S_IMODE(path_metadata.st_mode) != LOCK_MODE
        ):
            _fail(f"{code}_mode")
        if descriptor_metadata.st_nlink != 1 or path_metadata.st_nlink != 1:
            _fail(f"{code}_nlink")
        if (
            descriptor_metadata.st_dev,
            descriptor_metadata.st_ino,
        ) != (
            path_metadata.st_dev,
            path_metadata.st_ino,
        ):
            _fail(f"{code}_changed")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _write_new_file(
    path: Path,
    value: bytes,
    *,
    mode: int = RECEIPT_MODE,
) -> None:
    created = False
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            mode,
        )
        created = True
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = None
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(path.parent)
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            try:
                path.unlink(missing_ok=True)
                _fsync_directory(path.parent)
            except OSError:
                pass
        raise


def _atomic_replace(
    path: Path,
    value: bytes,
    *,
    mode: int = COMMIT_MODE,
) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _binding(document: Mapping[str, Any]) -> str:
    return _sha256_bytes(_canonical_json_bytes(document))


def _validate_binding(document: Mapping[str, Any], code: str) -> None:
    binding = _string(document.get("binding_sha256"), f"{code}_binding")
    if SHA256_RE.fullmatch(binding) is None:
        _fail(f"{code}_binding")
    core = dict(document)
    del core["binding_sha256"]
    if _binding(core) != binding:
        _fail(f"{code}_binding_mismatch")


def _output_paths(
    root: Path,
    config: Mapping[str, Any],
) -> tuple[Path, str, str]:
    output = _mapping(config.get("output"), "output")
    relative = _safe_relative_path(output.get("root"), "output_root")
    output_root = root / relative
    return (
        output_root,
        _string(output.get("commit_name"), "output_commit_name"),
        _string(output.get("receipt_name"), "output_receipt_name"),
    )


def _validate_generation_tree(
    root: Path,
    generation_root: Path,
    *,
    boundary: PublicationBoundary,
    receipt_name: str,
    expected_bytes: bytes | None = None,
) -> FileRecord:
    if boundary.output_root != root / CANONICAL_OUTPUT_ROOT:
        _fail("receipt_generation_path")
    if generation_root.parent != boundary.output_root / "generations":
        _fail("receipt_generation_path")
    _assert_publication_boundary(boundary)
    actual_generation_root = boundary.bound_output_path(
        "generations",
        generation_root.name,
    )
    try:
        root_metadata = actual_generation_root.lstat()
    except OSError:
        _fail("receipt_generation_missing")
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        _fail("receipt_generation_not_directory")
    if stat.S_IMODE(root_metadata.st_mode) != GENERATION_MODE:
        _fail("receipt_generation_mode")
    if root_metadata.st_mode & stat.S_IWUSR:
        _fail("receipt_generation_owner_writable")
    root_identity = _directory_identity(root_metadata)
    try:
        entries = sorted(actual_generation_root.iterdir(), key=lambda item: item.name)
    except OSError:
        _fail("receipt_generation_list")
    if [item.name for item in entries] != [receipt_name]:
        _fail("receipt_generation_tree")
    receipt = entries[0]
    canonical_receipt = generation_root / receipt_name
    value, receipt_record = _read_bound_file_stable(
        receipt,
        canonical_receipt.relative_to(root),
        code="receipt_file",
        maximum_bytes=MAX_SOURCE_BYTES,
        expected_mode=RECEIPT_MODE,
        require_nlink_one=True,
    )
    try:
        root_after = actual_generation_root.lstat()
    except OSError:
        _fail("receipt_generation_post_lstat")
    if (
        stat.S_ISLNK(root_after.st_mode)
        or _directory_identity(root_after) != root_identity
    ):
        _fail("receipt_generation_changed")
    if expected_bytes is not None and value != expected_bytes:
        _fail("receipt_generation_collision")
    generation_id = generation_root.name
    if (
        SHA256_RE.fullmatch(generation_id) is None
        or _sha256_bytes(value) != generation_id
    ):
        _fail("receipt_generation_id")
    _assert_publication_boundary(boundary)
    return FileRecord(
        path=receipt_record.path,
        sha256=generation_id,
        size_bytes=len(value),
    )


def _assert_source_records_unchanged(
    root: Path,
    config_record: FileRecord,
    records: Sequence[FileRecord],
) -> None:
    for index, record in enumerate((config_record, *records)):
        _, actual = _read_file_stable(
            root,
            record.path,
            code=f"source_recheck_{index}",
        )
        if actual != record:
            _fail("source_changed_before_commit")


def _assert_loaded_config_unchanged(root: Path, loaded: _Config) -> None:
    _, actual = _read_file_stable(
        root,
        loaded.record.path,
        code="config_recheck",
        maximum_bytes=MAX_CONFIG_BYTES,
    )
    if actual != loaded.record:
        _fail("config_changed_after_lock")


def _seal_generation(generation_root: Path, generations_root: Path) -> None:
    try:
        path_before = generation_root.lstat()
    except OSError:
        _fail("receipt_generation_missing")
    if stat.S_ISLNK(path_before.st_mode) or not stat.S_ISDIR(path_before.st_mode):
        _fail("receipt_generation_not_directory")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(generation_root, flags)
    except OSError:
        _fail("receipt_generation_seal_open")
    try:
        descriptor_before = os.fstat(descriptor)
        if not stat.S_ISDIR(descriptor_before.st_mode) or (
            path_before.st_dev,
            path_before.st_ino,
        ) != (
            descriptor_before.st_dev,
            descriptor_before.st_ino,
        ):
            _fail("receipt_generation_seal_changed")
        os.fchmod(descriptor, GENERATION_MODE)
        os.fsync(descriptor)
        descriptor_after = os.fstat(descriptor)
        try:
            path_after = generation_root.lstat()
        except OSError:
            _fail("receipt_generation_seal_lstat")
        if (
            stat.S_ISLNK(path_after.st_mode)
            or not stat.S_ISDIR(path_after.st_mode)
            or stat.S_IMODE(descriptor_after.st_mode) != GENERATION_MODE
            or stat.S_IMODE(path_after.st_mode) != GENERATION_MODE
            or (
                descriptor_after.st_dev,
                descriptor_after.st_ino,
            )
            != (
                path_after.st_dev,
                path_after.st_ino,
            )
        ):
            _fail("receipt_generation_seal_changed")
    except OSError:
        _fail("receipt_generation_seal")
    finally:
        os.close(descriptor)
    try:
        _fsync_directory(generations_root)
    except OSError:
        _fail("receipt_generation_seal")


def _publish_generation(
    root: Path,
    generations_root: Path,
    boundary: PublicationBoundary,
    generation_id: str,
    receipt_name: str,
    receipt_bytes: bytes,
) -> tuple[Path, FileRecord]:
    if (
        boundary.output_root != root / CANONICAL_OUTPUT_ROOT
        or generations_root != boundary.output_root / "generations"
    ):
        _fail("receipt_generation_path")
    _assert_publication_boundary(boundary)
    actual_generations_root = boundary.bound_output_path("generations")
    try:
        generations_metadata = actual_generations_root.lstat()
    except FileNotFoundError:
        try:
            actual_generations_root.mkdir(mode=0o750)
            os.fsync(boundary.output_descriptor)
            generations_metadata = actual_generations_root.lstat()
        except FileExistsError:
            try:
                generations_metadata = actual_generations_root.lstat()
            except OSError:
                _fail("receipt_generations_lstat")
        except OSError:
            _fail("receipt_generations_create")
    except OSError:
        _fail("receipt_generations_lstat")
    if stat.S_ISLNK(generations_metadata.st_mode) or not stat.S_ISDIR(
        generations_metadata.st_mode
    ):
        _fail("receipt_generations_not_directory")
    generation_root = generations_root / generation_id
    actual_generation_root = actual_generations_root / generation_id
    actual_receipt_path = actual_generation_root / receipt_name
    try:
        actual_generation_root.lstat()
    except FileNotFoundError:
        try:
            actual_generation_root.mkdir(mode=0o750)
            _fsync_directory(actual_generations_root)
        except FileExistsError:
            return generation_root, _validate_generation_tree(
                root,
                generation_root,
                boundary=boundary,
                receipt_name=receipt_name,
                expected_bytes=receipt_bytes,
            )
        except OSError:
            _fail("receipt_generation_create")
        try:
            _write_new_file(
                actual_receipt_path,
                receipt_bytes,
                mode=RECEIPT_MODE,
            )
        except BaseException:
            try:
                receipt_metadata = actual_receipt_path.lstat()
            except OSError:
                try:
                    actual_generation_root.rmdir()
                    _fsync_directory(actual_generations_root)
                except OSError:
                    pass
            else:
                if stat.S_ISREG(receipt_metadata.st_mode):
                    try:
                        _seal_generation(
                            actual_generation_root,
                            actual_generations_root,
                        )
                        _validate_generation_tree(
                            root,
                            generation_root,
                            boundary=boundary,
                            receipt_name=receipt_name,
                            expected_bytes=receipt_bytes,
                        )
                    except PublicationAuditError:
                        pass
            raise
        _seal_generation(actual_generation_root, actual_generations_root)
    except OSError:
        _fail("receipt_generation_lstat")
    record = _validate_generation_tree(
        root,
        generation_root,
        boundary=boundary,
        receipt_name=receipt_name,
        expected_bytes=receipt_bytes,
    )
    _assert_publication_boundary(boundary)
    return generation_root, record


def _configured_relative_path(
    root: Path,
    configured: str | Path,
    *,
    code: str,
) -> Path:
    path = Path(configured)
    if path.is_absolute():
        try:
            path = path.relative_to(root)
        except ValueError:
            _fail(code)
    return _safe_relative_path(path.as_posix(), code)


def _validate_embedded_namespace(value: object, loaded: _Config, code: str) -> None:
    namespace = _mapping(value, code)
    expected = _publication_namespace(loaded)
    _expect_keys(namespace, set(expected), f"{code}_keys")
    if dict(namespace) != expected:
        _fail(f"{code}_mismatch")


def _validate_publication_receipt_loaded(
    *,
    root: Path,
    loaded: _Config,
    commit_path: str | Path,
    boundary: PublicationBoundary,
) -> Mapping[str, Any]:
    _assert_publication_boundary(boundary)
    output_root, commit_name, receipt_name = _output_paths(root, loaded.document)
    if output_root != boundary.output_root:
        _fail("publication_commit_path")
    expected_commit_path = (output_root / commit_name).relative_to(root)
    configured_commit = _configured_relative_path(
        root,
        commit_path,
        code="publication_commit_path",
    )
    if configured_commit != expected_commit_path:
        _fail("publication_commit_path")
    commit_bytes, commit_record = _read_bound_file_stable(
        boundary.bound_output_path(commit_name),
        configured_commit,
        code="publication_commit",
        expected_mode=COMMIT_MODE,
        require_nlink_one=True,
    )
    commit = _json_no_duplicates(commit_bytes, "publication_commit")
    _expect_keys(
        commit,
        {
            "schema_version",
            "status",
            "publication_namespace",
            "generation_id",
            "receipt",
            "binding_sha256",
        },
        "publication_commit_keys",
    )
    if (
        commit.get("schema_version") != COMMIT_SCHEMA
        or commit.get("status") != "committed"
    ):
        _fail("publication_commit_contract")
    _validate_binding(commit, "publication_commit")
    _validate_embedded_namespace(
        commit.get("publication_namespace"),
        loaded,
        "publication_commit_namespace",
    )
    generation_id = _string(
        commit.get("generation_id"),
        "publication_generation_id",
    )
    if SHA256_RE.fullmatch(generation_id) is None:
        _fail("publication_generation_id")
    expected_receipt = _expected_record(
        commit.get("receipt"),
        "publication_receipt",
    )
    if expected_receipt.sha256 != generation_id:
        _fail("publication_receipt_generation_binding")
    generation_root = output_root / "generations" / generation_id
    actual_receipt = _validate_generation_tree(
        root,
        generation_root,
        boundary=boundary,
        receipt_name=receipt_name,
    )
    if actual_receipt != expected_receipt:
        _fail("publication_receipt_fingerprint")
    receipt_bytes, _ = _read_bound_file_stable(
        boundary.bound_output_path("generations", generation_id, receipt_name),
        expected_receipt.path,
        code="publication_receipt",
        expected_mode=RECEIPT_MODE,
        require_nlink_one=True,
    )
    receipt = _json_no_duplicates(receipt_bytes, "publication_receipt")
    _validate_embedded_namespace(
        receipt.get("publication_namespace"),
        loaded,
        "publication_receipt_namespace",
    )
    expected_document, source_records = _build_receipt(root, loaded)
    if receipt != expected_document or receipt_bytes != _pretty_json_bytes(
        expected_document
    ):
        _fail("publication_receipt_content")
    if (
        receipt.get("schema_version") != RECEIPT_SCHEMA
        or receipt.get("status") != "passed"
        or receipt.get("canonical_files_modified") is not False
    ):
        _fail("publication_receipt_contract")
    network = _mapping(
        receipt.get("network_observation"),
        "publication_receipt_network",
    )
    privacy = _mapping(receipt.get("privacy"), "publication_receipt_privacy")
    if network != {
        "canonical_completion_evidence": False,
        "performed": False,
        "request_count": 0,
    }:
        _fail("publication_receipt_network")
    if privacy != {
        "aggregate_only": True,
        "environment_files_read": False,
        "locked_test_files_read": False,
        "raw_files_read": False,
        "secret_files_read": False,
    }:
        _fail("publication_receipt_privacy")
    _assert_source_records_unchanged(root, loaded.record, source_records)
    _assert_publication_boundary(boundary)
    return {
        "status": "passed",
        "generation_id": generation_id,
        "commit": commit_record.path,
        "receipt": expected_receipt.path,
        "source_pin_count": len(source_records),
        "network_requests": 0,
        "locked_test_files_read": 0,
        "canonical_files_modified": False,
    }


def _prepare_existing_commit(
    *,
    root: Path,
    loaded: _Config,
    commit_path: Path,
    boundary: PublicationBoundary,
) -> tuple[bytes | None, bytes | None]:
    if commit_path != boundary.output_root / CANONICAL_COMMIT_NAME:
        _fail("publication_commit_path")
    actual_commit_path = boundary.bound_output_path(CANONICAL_COMMIT_NAME)
    try:
        metadata = actual_commit_path.lstat()
    except FileNotFoundError:
        return None, None
    except OSError:
        _fail("publication_commit_lstat")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        _fail("publication_commit_not_regular")
    if metadata.st_nlink != 1:
        _fail("publication_commit_nlink")
    mode = stat.S_IMODE(metadata.st_mode)
    if mode not in {COMMIT_MODE, 0o640}:
        _fail("publication_commit_mode")
    prior_bytes, _ = _read_bound_file_stable(
        actual_commit_path,
        commit_path.relative_to(root),
        code="publication_commit_prior",
        require_nlink_one=True,
    )
    try:
        _validate_publication_receipt_loaded(
            root=root,
            loaded=loaded,
            commit_path=commit_path,
            boundary=boundary,
        )
    except PublicationAuditError:
        return None, prior_bytes
    validated_bytes, _ = _read_bound_file_stable(
        actual_commit_path,
        commit_path.relative_to(root),
        code="publication_commit_prior_valid",
        expected_mode=COMMIT_MODE,
        require_nlink_one=True,
    )
    return validated_bytes, prior_bytes


def _rollback_commit_if_needed(
    *,
    root: Path,
    commit_path: Path,
    new_commit_bytes: bytes,
    previous_valid_bytes: bytes | None,
    prior_bytes: bytes | None,
    boundary: PublicationBoundary,
) -> None:
    if commit_path != boundary.output_root / CANONICAL_COMMIT_NAME:
        _fail("publication_commit_path")
    actual_commit_path = boundary.bound_output_path(CANONICAL_COMMIT_NAME)
    try:
        current_bytes, _ = _read_bound_file_stable(
            actual_commit_path,
            commit_path.relative_to(root),
            code="publication_commit_rollback",
            expected_mode=COMMIT_MODE,
            require_nlink_one=True,
        )
    except PublicationAuditError:
        if prior_bytes is None:
            try:
                actual_commit_path.lstat()
            except FileNotFoundError:
                return
            except OSError:
                _fail("publication_rollback_failed")
        else:
            try:
                unchanged_bytes, _ = _read_bound_file_stable(
                    actual_commit_path,
                    commit_path.relative_to(root),
                    code="publication_commit_rollback_prior",
                    require_nlink_one=True,
                )
            except PublicationAuditError:
                _fail("publication_rollback_failed")
            if unchanged_bytes == prior_bytes:
                return
        _fail("publication_rollback_failed")
    if current_bytes != new_commit_bytes:
        if prior_bytes is not None and current_bytes == prior_bytes:
            return
        _fail("publication_rollback_failed")
    if previous_valid_bytes is not None:
        _atomic_replace(actual_commit_path, previous_valid_bytes)
        restored_bytes, _ = _read_bound_file_stable(
            actual_commit_path,
            commit_path.relative_to(root),
            code="publication_commit_restored",
            expected_mode=COMMIT_MODE,
            require_nlink_one=True,
        )
        if restored_bytes != previous_valid_bytes:
            _fail("publication_rollback_failed")
        return
    try:
        actual_commit_path.unlink()
        _fsync_directory(actual_commit_path.parent)
    except OSError:
        _fail("publication_rollback_failed")


def audit_paper_publication(
    *,
    config_path: str | Path,
    workspace_root: str | Path,
) -> Mapping[str, Any]:
    """Run the offline audit and atomically publish a content-addressed receipt."""

    root, loaded = _load_config(config_path, workspace_root=workspace_root)
    if loaded.document.get("status") == BLOCKED_CONFIG_STATUS:
        _fail(BLOCKER_CODE)
    output_root_path, commit_name, receipt_name = _output_paths(root, loaded.document)
    output_relative = output_root_path.relative_to(root)
    output_root = _ensure_safe_output_directory(root, output_relative)
    with _publication_lock(
        output_root,
        exclusive=True,
        create=True,
    ) as boundary:
        _assert_publication_boundary(boundary)
        _assert_loaded_config_unchanged(root, loaded)
        receipt, source_records = _build_receipt(root, loaded)
        _assert_publication_boundary(boundary)
        receipt_bytes = _pretty_json_bytes(receipt)
        generation_id = _sha256_bytes(receipt_bytes)
        generations_root = output_root / "generations"
        generation_root, receipt_record = _publish_generation(
            root,
            generations_root,
            boundary,
            generation_id,
            receipt_name,
            receipt_bytes,
        )
        _assert_publication_boundary(boundary)
        _assert_source_records_unchanged(root, loaded.record, source_records)

        receipt_path = generation_root / receipt_name
        receipt_relative = receipt_path.relative_to(root).as_posix()
        if receipt_record.path != receipt_relative:
            _fail("receipt_generation_path")
        commit: dict[str, Any] = {
            "schema_version": COMMIT_SCHEMA,
            "status": "committed",
            "publication_namespace": _publication_namespace(loaded),
            "generation_id": generation_id,
            "receipt": receipt_record.as_dict(),
        }
        commit["binding_sha256"] = _binding(commit)
        new_commit_bytes = _pretty_json_bytes(commit)
        commit_path = output_root / commit_name
        previous_valid_bytes, prior_bytes = _prepare_existing_commit(
            root=root,
            loaded=loaded,
            commit_path=commit_path,
            boundary=boundary,
        )
        try:
            _assert_publication_boundary(boundary)
            _atomic_replace(
                boundary.bound_output_path(commit_name),
                new_commit_bytes,
            )
            _assert_publication_boundary(boundary)
            validated = _validate_publication_receipt_loaded(
                root=root,
                loaded=loaded,
                commit_path=commit_path,
                boundary=boundary,
            )
            if validated.get("generation_id") != generation_id:
                _fail("published_generation")
            _assert_publication_boundary(boundary)
        except BaseException:
            try:
                _rollback_commit_if_needed(
                    root=root,
                    commit_path=commit_path,
                    new_commit_bytes=new_commit_bytes,
                    previous_valid_bytes=previous_valid_bytes,
                    prior_bytes=prior_bytes,
                    boundary=boundary,
                )
            except BaseException as rollback_error:
                raise PublicationAuditError("publication_rollback_failed") from (
                    rollback_error
                )
            raise
        _assert_publication_boundary(boundary)
        return {
            "status": "passed",
            "scope": "publication_static_audit_only",
            "generation_id": generation_id,
            "commit": commit_path.relative_to(root).as_posix(),
            "receipt": receipt_relative,
            "canonical_files_modified": False,
            "pending_markers_preserved": True,
            "locked_test_files_read": 0,
            "network_requests": 0,
        }


def validate_publication_receipt(
    *,
    config_path: str | Path,
    commit_path: str | Path,
    workspace_root: str | Path,
) -> Mapping[str, Any]:
    """Deep-validate a committed receipt against the current pinned sources."""

    root, loaded = _load_config(config_path, workspace_root=workspace_root)
    output_root, _, _ = _output_paths(root, loaded.document)
    output_relative = output_root.relative_to(root)
    safe_output_root = _existing_safe_output_directory(root, output_relative)
    with _publication_lock(
        safe_output_root,
        exclusive=False,
        create=False,
    ) as boundary:
        _assert_publication_boundary(boundary)
        _assert_loaded_config_unchanged(root, loaded)
        result = _validate_publication_receipt_loaded(
            root=root,
            loaded=loaded,
            commit_path=commit_path,
            boundary=boundary,
        )
        _assert_publication_boundary(boundary)
        return result


def _default_requester(url: str, timeout_seconds: float) -> tuple[int, str]:
    request = Request(  # noqa: S310 - URLs are fixed, pinned HTTPS config values.
        url,
        method="HEAD",
        headers={"User-Agent": "voice-coach-publication-audit/1"},
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            return int(response.status), str(response.geturl())
    except HTTPError as error:
        return int(error.code), str(error.geturl())
    except URLError:
        return 0, url


def observe_external_urls(
    *,
    config_path: str | Path,
    workspace_root: str | Path,
    observed_at: datetime | None = None,
    timeout_seconds: float = 20.0,
    requester: Requester | None = None,
) -> Mapping[str, Any]:
    """Collect non-canonical URL observations after offline config validation."""

    if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
        _fail("network_timeout")
    root, loaded = _load_config(config_path, workspace_root=workspace_root)
    contract = _mapping(
        loaded.document.get("external_url_contract"),
        "external_url_contract",
    )
    canonical = _mapping(loaded.document.get("canonical"), "canonical")
    _, ledger_record = _read_source_record(
        root,
        canonical.get("source_ledger"),
        code="canonical_source_ledger",
    )
    request = requester or _default_requester
    observations: list[dict[str, Any]] = []
    records = _list(contract.get("records"), "external_records")
    for index, item in enumerate(records):
        record = _mapping(item, f"external_record_{index}")
        url = _https_url(record.get("url"), f"external_url_{index}")
        expected_status = _integer(
            record.get("expected_status"),
            f"external_status_{index}",
            minimum=100,
        )
        status, effective_url = request(url, timeout_seconds)
        observations.append(
            {
                "url": url,
                "expected_status": expected_status,
                "observed_status": status,
                "effective_url": effective_url,
                "matches_expected": status == expected_status,
            }
        )
    timestamp = observed_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        _fail("network_observed_at_timezone")
    return {
        "schema_version": NETWORK_OBSERVATION_SCHEMA,
        "status": "observation_only",
        "canonical_completion_evidence": False,
        "observed_at_utc": timestamp.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "config": loaded.record.as_dict(),
        "source_ledger": ledger_record.as_dict(),
        "request_count": len(observations),
        "observations": observations,
    }


def write_network_observation(
    *,
    document: Mapping[str, Any],
    output_path: str | Path,
    workspace_root: str | Path,
) -> str:
    """Atomically write a clearly non-canonical network observation."""

    _validate_network_observation_document(document)
    try:
        root = Path(workspace_root).resolve(strict=True)
    except OSError:
        _fail("workspace_root")
    configured = Path(output_path)
    if configured.is_absolute():
        try:
            configured = configured.resolve().relative_to(root)
        except ValueError:
            _fail("network_output_path")
    relative = _safe_relative_path(configured.as_posix(), "network_output_path")
    required_prefix = (*OUTPUT_PREFIX, "network_observations")
    if relative.parts[: len(required_prefix)] != required_prefix:
        _fail("network_output_prefix")
    output_parent = _ensure_safe_output_directory(root, relative.parent)
    path = output_parent / relative.name
    _atomic_replace(path, _pretty_json_bytes(document))
    return path.relative_to(root).as_posix()


def _validate_network_observation_document(document: Mapping[str, Any]) -> None:
    _expect_keys(
        document,
        {
            "schema_version",
            "status",
            "canonical_completion_evidence",
            "observed_at_utc",
            "config",
            "source_ledger",
            "request_count",
            "observations",
        },
        "network_observation_keys",
    )
    if (
        document.get("schema_version") != NETWORK_OBSERVATION_SCHEMA
        or document.get("status") != "observation_only"
        or document.get("canonical_completion_evidence") is not False
    ):
        _fail("network_observation_contract")
    timestamp = _string(
        document.get("observed_at_utc"),
        "network_observation_timestamp",
    )
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", timestamp) is None:
        _fail("network_observation_timestamp")
    _expected_record(document.get("config"), "network_observation_config")
    _expected_record(
        document.get("source_ledger"),
        "network_observation_ledger",
    )
    observations = _list(
        document.get("observations"),
        "network_observation_records",
    )
    if document.get("request_count") != len(observations):
        _fail("network_observation_count")
    for index, item in enumerate(observations):
        record = _mapping(item, f"network_observation_{index}")
        _expect_keys(
            record,
            {
                "url",
                "expected_status",
                "observed_status",
                "effective_url",
                "matches_expected",
            },
            f"network_observation_{index}_keys",
        )
        _https_url(record.get("url"), f"network_observation_{index}_url")
        _https_url(
            record.get("effective_url"),
            f"network_observation_{index}_effective_url",
        )
        expected_status = _integer(
            record.get("expected_status"),
            f"network_observation_{index}_expected_status",
            minimum=100,
        )
        observed_status = _integer(
            record.get("observed_status"),
            f"network_observation_{index}_observed_status",
            minimum=0,
        )
        if expected_status > 599 or observed_status > 599:
            _fail(f"network_observation_{index}_status")
        if record.get("matches_expected") is not (observed_status == expected_status):
            _fail(f"network_observation_{index}_match")


__all__ = [
    "COMMIT_SCHEMA",
    "CONFIG_SCHEMA",
    "NETWORK_OBSERVATION_SCHEMA",
    "PublicationAuditError",
    "RECEIPT_SCHEMA",
    "audit_paper_publication",
    "observe_external_urls",
    "validate_config_only",
    "validate_publication_receipt",
    "write_network_observation",
]
