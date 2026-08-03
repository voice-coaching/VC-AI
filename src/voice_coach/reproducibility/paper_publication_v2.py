"""Hardened, aggregate-only paper publication audit v2.

Version 2 is intentionally isolated from the historical v1 config, schemas and
publication namespace. All security-sensitive filesystem operations are rooted
at a held workspace directory descriptor and traverse with ``openat`` semantics.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
from types import MappingProxyType
from typing import Any, Iterator, NoReturn

import yaml

from voice_coach.reproducibility import paper_publication as v1
from voice_coach.reproducibility.protected_paths import (
    DescriptorIOError,
    ProtectedPathError,
    RootDirectory,
    atomic_replace_at,
    directory_identity,
    fsync_directory_descriptor,
    open_child_directory,
    read_file_at,
    validate_relative_path,
    write_new_file_at,
)


CONFIG_SCHEMA = "voice_coach.paper_publication_audit_config.v2"
RECEIPT_SCHEMA = "voice_coach.paper_publication_audit_receipt.v2"
COMMIT_SCHEMA = "voice_coach.paper_publication_audit_commit.v2"
NETWORK_OBSERVATION_SCHEMA = "voice_coach.paper_publication_network_observation.v2"

CANONICAL_CONFIG_PATH = "configs/paper/publication_v2.yaml"
CANONICAL_OUTPUT_ROOT = "artifacts/reproducibility/paper_publication_v2"
CANONICAL_COMMIT_NAME = "commit-v2.json"
CANONICAL_RECEIPT_NAME = "publication-audit-v2.json"
CANONICAL_NETWORK_ROOT = (
    "artifacts/reproducibility/paper_publication_v2/network_observations"
)
ROOT_NAMESPACE_LOCK_NAME = ".paper_publication_v2.publish.lock"
OUTPUT_LOCK_NAME = ".publish-v2.lock"

PENDING_DEFAULT_RE = r"\[PENDING:[^\]]+\]"
LOCKED_KEY_COUNT = 16
LOCKED_KEY_SET_SHA256 = (
    "de011d5deb430036e293b3b0b16593508cc22f200a30cdcb314c4996184d2f11"
)
COMPLETION_STATE: Mapping[str, bool | str] = MappingProxyType(
    {
        "paper_complete": False,
        "submission_status": "not_submitted",
        "manual_approval_required": True,
        "manual_approval_status": "not_approved",
    }
)

MAX_CONFIG_BYTES = 1024 * 1024
MAX_SOURCE_BYTES = 8 * 1024 * 1024
GENERATION_MODE = 0o550
RECEIPT_MODE = 0o440
COMMIT_MODE = 0o440
LOCK_MODE = 0o600
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PublicationV2Error(RuntimeError):
    """Fail-closed v2 error carrying a stable machine code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class FileRecord:
    """One workspace-relative immutable source fingerprint."""

    path: str
    sha256: str
    size_bytes: int

    def as_dict(self) -> dict[str, str | int]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass
class AccessCounters:
    """Measured audit observations; values are never hand-authored in receipts."""

    config_files_read: int = 0
    referenced_source_files_read: int = 0
    aggregate_source_files_read: int = 0
    canonical_files_read: int = 0
    local_asset_files_read: int = 0
    raw_files_read: int = 0
    environment_files_read: int = 0
    secret_files_read: int = 0
    locked_test_files_read: int = 0
    network_requests: int = 0
    output_files_written: int = 0
    canonical_files_written: int = 0

    def record_source(self, category: str) -> None:
        self.referenced_source_files_read += 1
        if category == "canonical":
            self.canonical_files_read += 1
        elif category == "local_asset":
            self.local_asset_files_read += 1
        else:
            self.aggregate_source_files_read += 1

    def as_dict(self) -> dict[str, int]:
        return {
            "config_files_read": self.config_files_read,
            "referenced_source_files_read": self.referenced_source_files_read,
            "aggregate_source_files_read": self.aggregate_source_files_read,
            "canonical_files_read": self.canonical_files_read,
            "local_asset_files_read": self.local_asset_files_read,
            "raw_files_read": self.raw_files_read,
            "environment_files_read": self.environment_files_read,
            "secret_files_read": self.secret_files_read,
            "locked_test_files_read": self.locked_test_files_read,
            "network_requests": self.network_requests,
            "output_files_written": self.output_files_written,
            "canonical_files_written": self.canonical_files_written,
        }

    def privacy(self) -> dict[str, bool]:
        return {
            "aggregate_only": all(
                value == 0
                for value in (
                    self.raw_files_read,
                    self.environment_files_read,
                    self.secret_files_read,
                    self.locked_test_files_read,
                )
            ),
            "raw_files_read": self.raw_files_read != 0,
            "environment_files_read": self.environment_files_read != 0,
            "secret_files_read": self.secret_files_read != 0,
            "locked_test_files_read": self.locked_test_files_read != 0,
        }


@dataclass
class PublicationCounters:
    """Measured writes for one publication transaction, outside the receipt."""

    generation_files_written: int = 0
    generation_files_reused: int = 0
    commit_files_written: int = 0
    canonical_files_written: int = 0
    network_requests: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "generation_files_written": self.generation_files_written,
            "generation_files_reused": self.generation_files_reused,
            "commit_files_written": self.commit_files_written,
            "canonical_files_written": self.canonical_files_written,
            "network_requests": self.network_requests,
        }


@dataclass(frozen=True)
class LoadedConfig:
    """Validated canonical v2 config and its exact fingerprint."""

    record: FileRecord
    document: Mapping[str, Any]


Requester = Callable[[str, float], tuple[int, str]]


def _fail(code: str) -> NoReturn:
    raise PublicationV2Error(code)


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
        try:
            duplicate = key in output
        except TypeError:
            _fail("config_mapping_key")
        if duplicate:
            _fail("config_duplicate_key")
        output[key] = loader.construct_object(value_node, deep=deep)
    return output


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


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


def _reject_nonfinite(value: Any, code: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        _fail(code)
    if isinstance(value, Mapping):
        for child in value.values():
            _reject_nonfinite(child, code)
    elif isinstance(value, list):
        for child in value:
            _reject_nonfinite(child, code)


def _expect_keys(value: Mapping[str, Any], expected: set[str], code: str) -> None:
    if set(value) != expected:
        _fail(code)


def _sha(value: object, code: str) -> str:
    candidate = _string(value, code)
    if SHA256_RE.fullmatch(candidate) is None:
        _fail(code)
    return candidate


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


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def locked_key_set_digest(keys: Sequence[str]) -> str:
    """Hash the sorted exact locked-evaluation key set without a trailing LF."""

    return _digest("\n".join(sorted(keys)).encode("utf-8"))


def _safe_path(value: object, code: str, *, protected: bool = True) -> PurePosixPath:
    try:
        return validate_relative_path(
            _string(value, code),
            reject_protected=protected,
        )
    except ProtectedPathError:
        _fail(f"{code}_forbidden" if protected else code)


def _expected_record(value: object, code: str) -> FileRecord:
    record = _mapping(value, code)
    _expect_keys(record, {"path", "sha256", "size_bytes"}, f"{code}_keys")
    path = _safe_path(record.get("path"), f"{code}_path").as_posix()
    sha256 = _sha(record.get("sha256"), f"{code}_sha256")
    size = _integer(record.get("size_bytes"), f"{code}_size", minimum=1)
    if size > MAX_SOURCE_BYTES:
        _fail(f"{code}_size")
    return FileRecord(path=path, sha256=sha256, size_bytes=size)


def _parse_yaml(value: bytes, code: str) -> Mapping[str, Any]:
    try:
        document = yaml.load(value.decode("utf-8"), Loader=_UniqueKeyLoader)
    except (UnicodeError, yaml.YAMLError):
        _fail(code)
    result = _mapping(document, f"{code}_root")
    _reject_nonfinite(result, f"{code}_nonfinite")
    return result


def _parse_json(value: bytes, code: str) -> Mapping[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
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
            value.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError):
        _fail(code)
    result = _mapping(document, f"{code}_root")
    _reject_nonfinite(result, f"{code}_nonfinite")
    return result


def _read_config(
    workspace: RootDirectory,
    config_path: str | Path,
) -> LoadedConfig:
    configured = _safe_path(
        Path(config_path).as_posix(),
        "config_path",
        protected=True,
    )
    if configured.as_posix() != CANONICAL_CONFIG_PATH:
        _fail("config_namespace")
    try:
        content, metadata = workspace.read_bytes(
            configured.as_posix(),
            maximum_bytes=MAX_CONFIG_BYTES,
            require_nlink_one=True,
            reject_protected=True,
        )
    except DescriptorIOError as exc:
        _fail(f"config_{exc}")
    document = _parse_yaml(content, "config_yaml")
    _validate_config_document(document)
    return LoadedConfig(
        record=FileRecord(
            path=configured.as_posix(),
            sha256=_digest(content),
            size_bytes=metadata.st_size,
        ),
        document=document,
    )


def _read_record(
    workspace: RootDirectory,
    value: object,
    *,
    counters: AccessCounters,
    category: str,
    code: str,
) -> tuple[bytes, FileRecord]:
    expected = _expected_record(value, code)
    try:
        content, metadata = workspace.read_bytes(
            expected.path,
            maximum_bytes=MAX_SOURCE_BYTES,
            require_nlink_one=True,
            reject_protected=True,
        )
    except (DescriptorIOError, ProtectedPathError) as exc:
        _fail(f"{code}_{exc}")
    actual = FileRecord(
        path=expected.path,
        sha256=_digest(content),
        size_bytes=metadata.st_size,
    )
    if actual != expected:
        _fail(f"{code}_fingerprint")
    counters.record_source(category)
    return content, actual


def _validate_completion_state(value: object, code: str) -> None:
    state = _mapping(value, code)
    _expect_keys(state, set(COMPLETION_STATE), code)
    if (
        state.get("paper_complete") is not False
        or state.get("submission_status") != "not_submitted"
        or state.get("manual_approval_required") is not True
        or state.get("manual_approval_status") != "not_approved"
    ):
        _fail(code)


def _validate_config_document(config: Mapping[str, Any]) -> None:
    _expect_keys(
        config,
        {
            "schema_version",
            "status",
            "base_content_contract",
            "policy",
            "completion_state",
            "implementation",
            "evidence_gate",
            "canonical",
            "output",
        },
        "config_keys",
    )
    if config.get("schema_version") != CONFIG_SCHEMA:
        _fail("config_schema")
    if config.get("status") != "ready":
        _fail("config_status")
    _expected_record(config.get("base_content_contract"), "base_content_contract")

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
            "policy_network_completion",
        )
        is not False
        or _boolean(
            policy.get("canonical_write_allowed"),
            "policy_canonical_write",
        )
        is not False
    ):
        _fail("policy_fail_closed")
    if policy.get("pending_marker_regex") != PENDING_DEFAULT_RE:
        _fail("policy_pending_regex")
    _integer(policy.get("expected_markdown_tables"), "policy_tables", minimum=1)
    _integer(policy.get("expected_pending_count"), "policy_pending_count", minimum=1)
    _sha(policy.get("expected_pending_markers_sha256"), "policy_pending_sha")
    _validate_completion_state(config.get("completion_state"), "completion_state")

    implementation = _mapping(config.get("implementation"), "implementation")
    _expect_keys(
        implementation,
        {"module", "cli", "protected_paths", "content_audit_module"},
        "implementation_keys",
    )
    for name in ("module", "cli", "protected_paths", "content_audit_module"):
        _expected_record(implementation.get(name), f"implementation_{name}")

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
    expected = _mapping(gate.get("expected"), "evidence_expected")
    _expect_keys(
        expected,
        {
            "status",
            "artifact_count",
            "claim_count",
            "pending_count",
            "pending_markers_sha256",
            "locked_guard_status",
            "locked_access_entry_count",
            "locked_access_keys",
            "locked_access_keys_sha256",
            "registry_metric_splits",
        },
        "evidence_expected_keys",
    )
    if (
        expected.get("status") != "passed"
        or expected.get("locked_guard_status") != "passed"
    ):
        _fail("evidence_expected_status")
    _integer(expected.get("artifact_count"), "evidence_artifact_count", minimum=1)
    _integer(expected.get("claim_count"), "evidence_claim_count", minimum=1)
    _integer(expected.get("pending_count"), "evidence_pending_count", minimum=1)
    _sha(expected.get("pending_markers_sha256"), "evidence_pending_sha")
    if expected.get("pending_count") != policy.get(
        "expected_pending_count"
    ) or expected.get("pending_markers_sha256") != policy.get(
        "expected_pending_markers_sha256"
    ):
        _fail("pending_contract_mismatch")
    entry_count = _integer(
        expected.get("locked_access_entry_count"),
        "evidence_locked_count",
        minimum=1,
    )
    keys = [
        _string(item, f"evidence_locked_key_{index}")
        for index, item in enumerate(
            _list(expected.get("locked_access_keys"), "evidence_locked_keys")
        )
    ]
    if (
        entry_count != LOCKED_KEY_COUNT
        or len(keys) != LOCKED_KEY_COUNT
        or keys != sorted(set(keys))
        or locked_key_set_digest(keys) != LOCKED_KEY_SET_SHA256
        or expected.get("locked_access_keys_sha256") != LOCKED_KEY_SET_SHA256
    ):
        _fail("evidence_locked_key_contract")
    splits = _list(expected.get("registry_metric_splits"), "evidence_splits")
    if (
        not splits
        or any(not isinstance(item, str) or not item for item in splits)
        or splits != sorted(set(splits))
    ):
        _fail("evidence_splits")

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
    source_paths: list[str] = []
    for index, item in enumerate(assets):
        asset = _mapping(item, f"asset_{index}")
        _expect_keys(asset, {"link_target", "source"}, f"asset_{index}_keys")
        target = _safe_path(
            asset.get("link_target"),
            f"asset_{index}_target",
        ).as_posix()
        source = _expected_record(asset.get("source"), f"asset_{index}")
        targets.append(target)
        source_paths.append(source.path)
    if len(targets) != len(set(targets)) or len(source_paths) != len(set(source_paths)):
        _fail("asset_duplicate")

    output = _mapping(config.get("output"), "output")
    _expect_keys(
        output,
        {"root", "commit_name", "receipt_name", "network_observation_root"},
        "output_keys",
    )
    if (
        _safe_path(output.get("root"), "output_root", protected=True).as_posix()
        != CANONICAL_OUTPUT_ROOT
        or output.get("commit_name") != CANONICAL_COMMIT_NAME
        or output.get("receipt_name") != CANONICAL_RECEIPT_NAME
        or _safe_path(
            output.get("network_observation_root"),
            "network_output_root",
            protected=True,
        ).as_posix()
        != CANONICAL_NETWORK_ROOT
    ):
        _fail("output_namespace")


def _publication_namespace(loaded: LoadedConfig) -> dict[str, str]:
    return {
        "config_path": loaded.record.path,
        "config_sha256": loaded.record.sha256,
        "output_root": CANONICAL_OUTPUT_ROOT,
        "commit_name": CANONICAL_COMMIT_NAME,
        "receipt_name": CANONICAL_RECEIPT_NAME,
    }


def _locked_contract(
    expected: Mapping[str, Any],
    *,
    locked_test_files_read: int = 0,
) -> dict[str, Any]:
    return {
        "entry_count": expected["locked_access_entry_count"],
        "keys_sha256": expected["locked_access_keys_sha256"],
        "all_access_flags_false": True,
        "locked_test_files_read": locked_test_files_read,
    }


def _validate_locked_contract(
    value: object,
    expected: Mapping[str, Any],
    code: str,
) -> None:
    contract = _mapping(value, code)
    _expect_keys(
        contract,
        {
            "entry_count",
            "keys_sha256",
            "all_access_flags_false",
            "locked_test_files_read",
        },
        code,
    )
    if (
        _integer(contract.get("entry_count"), code, minimum=1)
        != expected.get("locked_access_entry_count")
        or _sha(contract.get("keys_sha256"), code)
        != expected.get("locked_access_keys_sha256")
        or _boolean(contract.get("all_access_flags_false"), code) is not True
        or _integer(contract.get("locked_test_files_read"), code) != 0
    ):
        _fail(code)


def _assert_offline_counter_contract(counters: AccessCounters) -> None:
    if (
        counters.raw_files_read != 0
        or counters.environment_files_read != 0
        or counters.secret_files_read != 0
        or counters.locked_test_files_read != 0
        or counters.network_requests != 0
        or counters.canonical_files_written != 0
        or counters.output_files_written != 0
    ):
        _fail("offline_access_counter_contract")


def _validate_evidence_gate(
    workspace: RootDirectory,
    config: Mapping[str, Any],
    counters: AccessCounters,
) -> tuple[dict[str, Any], list[FileRecord]]:
    gate = _mapping(config.get("evidence_gate"), "evidence_gate")
    expected = _mapping(gate.get("expected"), "evidence_expected")
    report_bytes, report_record = _read_record(
        workspace,
        gate.get("report"),
        counters=counters,
        category="aggregate",
        code="evidence_report",
    )
    report = _parse_json(report_bytes, "evidence_report")
    if report.get("status") != expected.get("status"):
        _fail("evidence_report_status")
    summary = _mapping(report.get("summary"), "evidence_report_summary")
    if summary != {
        "artifact_count": expected.get("artifact_count"),
        "artifact_passed": expected.get("artifact_count"),
        "claim_count": expected.get("claim_count"),
        "claim_passed": expected.get("claim_count"),
    }:
        _fail("evidence_report_summary")
    pending = _mapping(report.get("pending_contract"), "evidence_report_pending")
    _expect_keys(
        pending,
        {"status", "marker_count", "markers_sha256", "errors"},
        "evidence_report_pending_keys",
    )
    if (
        pending.get("status") != "passed"
        or pending.get("marker_count") != expected.get("pending_count")
        or pending.get("markers_sha256") != expected.get("pending_markers_sha256")
        or pending.get("errors") != []
    ):
        _fail("evidence_report_pending")
    locked = _mapping(report.get("locked_test_guard"), "evidence_report_locked")
    _expect_keys(
        locked,
        {"status", "errors", "accessed", "registry_metric_splits"},
        "evidence_report_locked_keys",
    )
    accessed = _mapping(
        locked.get("accessed"),
        "evidence_report_locked_accessed",
    )
    expected_keys = _list(
        expected.get("locked_access_keys"),
        "evidence_expected_locked_keys",
    )
    observed_keys = sorted(accessed)
    if (
        locked.get("status") != expected.get("locked_guard_status")
        or locked.get("errors") != []
        or any(value is not False for value in accessed.values())
        or observed_keys != expected_keys
        or len(observed_keys) != expected.get("locked_access_entry_count")
        or locked_key_set_digest(observed_keys)
        != expected.get("locked_access_keys_sha256")
    ):
        _fail("evidence_report_locked")
    splits = locked.get("registry_metric_splits")
    if not isinstance(splits, list) or splits != expected.get("registry_metric_splits"):
        _fail("evidence_report_splits")
    privacy = _mapping(report.get("privacy"), "evidence_report_privacy")
    if privacy != {
        "aggregate_only": True,
        "environment_files_read": False,
        "raw_data_read": False,
    }:
        _fail("evidence_report_privacy")

    canonical = _mapping(config.get("canonical"), "canonical")
    manuscript = _expected_record(
        canonical.get("manuscript"),
        "canonical_manuscript",
    )
    evidence_map = _expected_record(
        canonical.get("evidence_map"),
        "canonical_evidence_map",
    )
    if report.get("manuscript") != {
        "path": manuscript.path,
        "sha256": manuscript.sha256,
    }:
        _fail("evidence_report_manuscript_binding")
    if report.get("evidence_map") != {
        "path": evidence_map.path,
        "sha256": evidence_map.sha256,
    }:
        _fail("evidence_report_map_binding")

    records = [report_record]
    for name in ("validator_script", "validator_module", "validation_document"):
        _, record = _read_record(
            workspace,
            gate.get(name),
            counters=counters,
            category="aggregate",
            code=f"evidence_{name}",
        )
        records.append(record)
    return (
        {
            "status": "passed",
            "report": report_record.as_dict(),
            "artifact_count": summary["artifact_count"],
            "claim_count": summary["claim_count"],
            "pending_count": pending["marker_count"],
            "pending_markers_sha256": pending["markers_sha256"],
            "locked_guard_status": locked["status"],
            "locked_access_entry_count": len(observed_keys),
            "locked_access_keys": observed_keys,
            "locked_access_keys_sha256": locked_key_set_digest(observed_keys),
            "locked_access_all_false": True,
            "registry_metric_splits": sorted(set(splits)),
        },
        records,
    )


def _load_base_contract(
    workspace: RootDirectory,
    config: Mapping[str, Any],
    counters: AccessCounters,
) -> tuple[Mapping[str, Any], FileRecord]:
    content, record = _read_record(
        workspace,
        config.get("base_content_contract"),
        counters=counters,
        category="aggregate",
        code="base_content_contract",
    )
    base = _parse_yaml(content, "base_content_contract")
    try:
        v1._validate_config_document(base)
    except v1.PublicationAuditError as exc:
        _fail(f"base_content_contract_{exc.code}")
    return base, record


def _local_asset_audit(
    workspace: RootDirectory,
    manuscript_path: str,
    markdown_links: Sequence[str],
    config: Mapping[str, Any],
    counters: AccessCounters,
) -> tuple[dict[str, Any], list[FileRecord]]:
    canonical = _mapping(config.get("canonical"), "canonical")
    assets = _list(canonical.get("local_assets"), "canonical_local_assets")
    local_targets = [
        target
        for target in markdown_links
        if not target.startswith(("https://", "http://", "mailto:", "#"))
    ]
    configured_targets = [
        _safe_path(
            _mapping(item, f"asset_{index}").get("link_target"),
            f"asset_{index}_target",
        ).as_posix()
        for index, item in enumerate(assets)
    ]
    if local_targets != configured_targets:
        _fail("local_asset_link_order")
    parent = PurePosixPath(manuscript_path).parent
    records: list[FileRecord] = []
    for index, item in enumerate(assets):
        asset = _mapping(item, f"asset_{index}")
        expected_path = (parent / configured_targets[index]).as_posix()
        source = _expected_record(asset.get("source"), f"asset_{index}")
        if source.path != expected_path:
            _fail(f"asset_{index}_binding")
        _, actual = _read_record(
            workspace,
            asset.get("source"),
            counters=counters,
            category="local_asset",
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


def _build_receipt(
    workspace: RootDirectory,
    loaded: LoadedConfig,
) -> tuple[dict[str, Any], list[FileRecord], AccessCounters]:
    config = loaded.document
    counters = AccessCounters(config_files_read=1)
    evidence, evidence_records = _validate_evidence_gate(
        workspace,
        config,
        counters,
    )

    implementation_records: list[FileRecord] = []
    implementation = _mapping(config.get("implementation"), "implementation")
    for name in ("module", "cli", "protected_paths", "content_audit_module"):
        _, record = _read_record(
            workspace,
            implementation.get(name),
            counters=counters,
            category="aggregate",
            code=f"implementation_{name}",
        )
        implementation_records.append(record)

    base, base_record = _load_base_contract(workspace, config, counters)

    canonical = _mapping(config.get("canonical"), "canonical")
    manuscript_bytes, manuscript_record = _read_record(
        workspace,
        canonical.get("manuscript"),
        counters=counters,
        category="canonical",
        code="canonical_manuscript",
    )
    map_bytes, map_record = _read_record(
        workspace,
        canonical.get("evidence_map"),
        counters=counters,
        category="canonical",
        code="canonical_evidence_map",
    )
    ledger_bytes, ledger_record = _read_record(
        workspace,
        canonical.get("source_ledger"),
        counters=counters,
        category="canonical",
        code="canonical_source_ledger",
    )
    try:
        manuscript = manuscript_bytes.decode("utf-8")
        map_bytes.decode("utf-8")
        ledger = ledger_bytes.decode("utf-8")
    except UnicodeError:
        _fail("canonical_utf8")

    runtime_config = {
        "policy": config["policy"],
        "canonical": config["canonical"],
        "citation_contract": base["citation_contract"],
        "external_url_contract": base["external_url_contract"],
    }
    try:
        citation, manuscript_urls, markdown_links = v1._citation_audit(
            manuscript,
            runtime_config,
        )
        tables = v1._parse_markdown_tables(manuscript)
        pending = v1._pending_audit(manuscript, runtime_config)
        external = v1._external_url_audit(
            ledger,
            manuscript_urls,
            runtime_config,
        )
    except v1.PublicationAuditError as exc:
        _fail(f"content_{exc.code}")
    if len(tables) != config["policy"]["expected_markdown_tables"]:
        _fail("markdown_table_count")
    local_assets, asset_records = _local_asset_audit(
        workspace,
        manuscript_record.path,
        markdown_links,
        config,
        counters,
    )

    records = [
        *evidence_records,
        base_record,
        manuscript_record,
        map_record,
        ledger_record,
        *asset_records,
        *implementation_records,
    ]
    if len({record.path for record in records}) != len(records):
        _fail("source_record_duplicate")
    external = {
        **external,
        "network_requests": counters.network_requests,
        "canonical_completion_evidence_from_network": False,
    }
    _assert_offline_counter_contract(counters)
    access = counters.as_dict()
    expected = _mapping(
        _mapping(config.get("evidence_gate"), "evidence_gate").get("expected"),
        "evidence_expected",
    )
    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA,
        "status": "passed",
        "scope": "publication_static_audit_v2_only",
        "completion_state": dict(COMPLETION_STATE),
        "publication_namespace": _publication_namespace(loaded),
        "config": loaded.record.as_dict(),
        "base_content_contract": base_record.as_dict(),
        "access_counter_scope": "single_deterministic_receipt_reconstruction",
        "source_pins": [
            record.as_dict() for record in sorted(records, key=lambda item: item.path)
        ],
        "evidence_gate": evidence,
        "locked_evaluation_contract": _locked_contract(
            expected,
            locked_test_files_read=counters.locked_test_files_read,
        ),
        "citation_audit": citation,
        "markdown_audit": {
            "status": "passed",
            "table_count": len(tables),
            "tables": tables,
        },
        "local_asset_audit": local_assets,
        "pending_contract": pending,
        "external_url_ledger": external,
        "network_observation": {
            "performed": counters.network_requests > 0,
            "request_count": counters.network_requests,
            "canonical_completion_evidence": False,
        },
        "access_counters": access,
        "privacy": counters.privacy(),
        "canonical_files_modified": counters.canonical_files_written != 0,
    }
    return receipt, records, counters


def validate_config_only(
    *,
    config_path: str | Path = CANONICAL_CONFIG_PATH,
    workspace_root: str | Path,
) -> Mapping[str, Any]:
    """Validate only the canonical v2 config and touch no declared source/output."""

    try:
        with RootDirectory.open(workspace_root) as workspace:
            loaded = _read_config(workspace, config_path)
    except DescriptorIOError as exc:
        _fail(f"descriptor_{exc}")
    counters = AccessCounters(config_files_read=1)
    source_count = (
        1
        + 4
        + 4
        + 3
        + len(
            _list(
                _mapping(loaded.document.get("canonical"), "canonical").get(
                    "local_assets"
                ),
                "canonical_local_assets",
            )
        )
    )
    return {
        "schema_version": CONFIG_SCHEMA,
        "status": "valid_config_only",
        "config": loaded.record.as_dict(),
        "source_record_schemas_validated": source_count,
        "completion_state": dict(COMPLETION_STATE),
        "access_counter_scope": "config_structure_validation_only",
        "access_counters": counters.as_dict(),
        "privacy": counters.privacy(),
    }


def _acquire_lock_at(
    parent_descriptor: int,
    name: str,
    *,
    exclusive: bool,
    create: bool,
    code: str,
) -> int:
    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    created = False
    if create:
        try:
            descriptor = os.open(
                name,
                flags | os.O_CREAT | os.O_EXCL,
                LOCK_MODE,
                dir_fd=parent_descriptor,
            )
            created = True
        except FileExistsError:
            pass
        except OSError:
            _fail(f"{code}_create")
    if descriptor is None:
        try:
            descriptor = os.open(
                name,
                flags,
                LOCK_MODE,
                dir_fd=parent_descriptor,
            )
        except OSError:
            _fail(f"{code}_open")
    try:
        if created:
            os.fchmod(descriptor, LOCK_MODE)
            os.fsync(descriptor)
            fsync_directory_descriptor(parent_descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        _assert_lock_binding(parent_descriptor, name, descriptor, code=code)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _assert_lock_binding(
    parent_descriptor: int,
    name: str,
    descriptor: int,
    *,
    code: str,
) -> None:
    try:
        metadata = os.fstat(descriptor)
        path_metadata = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except OSError:
        _fail(f"{code}_contract")
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(path_metadata.st_mode)
        or not stat.S_ISREG(path_metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != LOCK_MODE
        or stat.S_IMODE(path_metadata.st_mode) != LOCK_MODE
        or metadata.st_nlink != 1
        or path_metadata.st_nlink != 1
        or (metadata.st_dev, metadata.st_ino)
        != (path_metadata.st_dev, path_metadata.st_ino)
    ):
        _fail(f"{code}_contract")


def _open_output_directory(
    workspace: RootDirectory,
    *,
    create: bool,
) -> int:
    descriptor = os.dup(workspace.descriptor)
    try:
        for part in PurePosixPath(CANONICAL_OUTPUT_ROOT).parts:
            child = open_child_directory(descriptor, part, create=create)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@dataclass
class PublicationStore:
    """Two-lock output transaction anchored to root and output descriptors."""

    workspace: RootDirectory
    output_descriptor: int
    output_identity: tuple[int, int]
    namespace_lock_descriptor: int
    output_lock_descriptor: int

    @classmethod
    @contextmanager
    def open(
        cls,
        workspace: RootDirectory,
        *,
        exclusive: bool,
        create: bool,
    ) -> Iterator[PublicationStore]:
        namespace_lock = _acquire_lock_at(
            workspace.descriptor,
            ROOT_NAMESPACE_LOCK_NAME,
            exclusive=exclusive,
            create=create,
            code="namespace_lock",
        )
        output_descriptor: int | None = None
        output_lock: int | None = None
        store: PublicationStore | None = None
        completed = False
        try:
            output_descriptor = _open_output_directory(workspace, create=create)
            output_identity = directory_identity(os.fstat(output_descriptor))
            output_lock = _acquire_lock_at(
                output_descriptor,
                OUTPUT_LOCK_NAME,
                exclusive=exclusive,
                create=create,
                code="output_lock",
            )
            store = cls(
                workspace=workspace,
                output_descriptor=output_descriptor,
                output_identity=output_identity,
                namespace_lock_descriptor=namespace_lock,
                output_lock_descriptor=output_lock,
            )
            store.assert_bound()
            yield store
            completed = True
        finally:
            try:
                if completed and store is not None:
                    store.assert_bound()
            finally:
                if output_lock is not None:
                    try:
                        fcntl.flock(output_lock, fcntl.LOCK_UN)
                    finally:
                        os.close(output_lock)
                if output_descriptor is not None:
                    os.close(output_descriptor)
                try:
                    fcntl.flock(namespace_lock, fcntl.LOCK_UN)
                finally:
                    os.close(namespace_lock)

    def assert_bound(self) -> None:
        self.workspace.assert_bound()
        _assert_lock_binding(
            self.workspace.descriptor,
            ROOT_NAMESPACE_LOCK_NAME,
            self.namespace_lock_descriptor,
            code="namespace_lock",
        )
        _assert_lock_binding(
            self.output_descriptor,
            OUTPUT_LOCK_NAME,
            self.output_lock_descriptor,
            code="output_lock",
        )
        try:
            current = self.workspace.directory_identity(CANONICAL_OUTPUT_ROOT)
        except DescriptorIOError:
            _fail("output_root_rebound")
        if current != self.output_identity:
            _fail("output_root_rebound")

    @contextmanager
    def open_directory(
        self,
        parts: Sequence[str],
        *,
        create: bool,
    ) -> Iterator[int]:
        descriptor = os.dup(self.output_descriptor)
        completed = False
        identity: tuple[int, int] | None = None
        try:
            for part in parts:
                child = open_child_directory(descriptor, part, create=create)
                os.close(descriptor)
                descriptor = child
            identity = directory_identity(os.fstat(descriptor))
            yield descriptor
            completed = True
        finally:
            try:
                if completed:
                    current: int | None = None
                    try:
                        current = os.dup(self.output_descriptor)
                        for part in parts:
                            child = open_child_directory(
                                current,
                                part,
                                create=False,
                            )
                            os.close(current)
                            current = child
                        if directory_identity(os.fstat(current)) != identity:
                            _fail("output_descendant_rebound")
                    except DescriptorIOError:
                        _fail("output_descendant_rebound")
                    finally:
                        if current is not None:
                            os.close(current)
            finally:
                os.close(descriptor)

    def read_output_file(
        self,
        name: str,
        *,
        expected_mode: int,
    ) -> bytes:
        self.assert_bound()
        return self.read_held_output_file(name, expected_mode=expected_mode)

    def held_output_metadata(self, name: str) -> os.stat_result | None:
        """lstat one held-output entry; only exact absence returns ``None``."""

        try:
            return os.stat(
                name,
                dir_fd=self.output_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return None
        except OSError:
            _fail(f"output_{name}_lstat")

    def read_held_output_file(
        self,
        name: str,
        *,
        expected_mode: int,
    ) -> bytes:
        """Read through the held output fd, including during safe rollback."""

        try:
            content, _ = read_file_at(
                self.output_descriptor,
                name,
                maximum_bytes=MAX_SOURCE_BYTES,
                expected_mode=expected_mode,
                require_nlink_one=True,
            )
            return content
        except DescriptorIOError as exc:
            _fail(f"output_{name}_{exc}")

    def atomic_replace(self, name: str, content: bytes, *, mode: int) -> None:
        self.assert_bound()
        self.atomic_replace_held(name, content, mode=mode)
        self.assert_bound()

    def atomic_replace_held(self, name: str, content: bytes, *, mode: int) -> None:
        """Replace through the held output fd without resolving its pathname."""

        temporary = f".{name}.{secrets.token_hex(16)}.tmp"
        try:
            atomic_replace_at(
                self.output_descriptor,
                name,
                temporary,
                content,
                mode=mode,
            )
        except DescriptorIOError as exc:
            _fail(f"output_atomic_{exc}")

    def unlink(self, name: str) -> None:
        self.assert_bound()
        self.unlink_held(name)
        self.assert_bound()

    def unlink_held(self, name: str) -> None:
        """Unlink through the held output fd, including after a route rebind."""

        try:
            os.unlink(name, dir_fd=self.output_descriptor)
            fsync_directory_descriptor(self.output_descriptor)
        except FileNotFoundError:
            return
        except OSError:
            _fail("output_unlink")

    def publish_generation(
        self,
        *,
        generation_id: str,
        receipt_bytes: bytes,
    ) -> tuple[FileRecord, bool]:
        self.assert_bound()
        with self.open_directory(("generations",), create=True) as generations:
            try:
                existing_metadata = os.stat(
                    generation_id,
                    dir_fd=generations,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                existing_metadata = None
            except OSError:
                _fail("generation_lstat")
            if existing_metadata is not None and (
                stat.S_ISLNK(existing_metadata.st_mode)
                or not stat.S_ISDIR(existing_metadata.st_mode)
            ):
                _fail("generation_not_directory")
            try:
                existing = open_child_directory(
                    generations,
                    generation_id,
                    create=False,
                )
            except DescriptorIOError:
                if existing_metadata is not None:
                    _fail("generation_open")
                existing = None
            if existing is not None:
                try:
                    observed = _validate_generation_descriptor(
                        existing,
                        generation_id=generation_id,
                        expected_bytes=receipt_bytes,
                    )
                finally:
                    os.close(existing)
                return (
                    FileRecord(
                        path=(
                            f"{CANONICAL_OUTPUT_ROOT}/generations/{generation_id}/"
                            f"{CANONICAL_RECEIPT_NAME}"
                        ),
                        sha256=generation_id,
                        size_bytes=len(observed),
                    ),
                    False,
                )

            temporary = f".{generation_id}.{secrets.token_hex(16)}"
            try:
                os.mkdir(temporary, mode=0o750, dir_fd=generations)
                fsync_directory_descriptor(generations)
                temporary_descriptor = open_child_directory(
                    generations,
                    temporary,
                    create=False,
                )
                try:
                    write_new_file_at(
                        temporary_descriptor,
                        CANONICAL_RECEIPT_NAME,
                        receipt_bytes,
                        mode=RECEIPT_MODE,
                    )
                    os.fchmod(temporary_descriptor, GENERATION_MODE)
                    fsync_directory_descriptor(temporary_descriptor)
                finally:
                    os.close(temporary_descriptor)
                os.rename(
                    temporary,
                    generation_id,
                    src_dir_fd=generations,
                    dst_dir_fd=generations,
                )
                fsync_directory_descriptor(generations)
            except BaseException:
                _cleanup_generation_at(generations, temporary)
                raise
            final_descriptor = open_child_directory(
                generations,
                generation_id,
                create=False,
            )
            try:
                observed = _validate_generation_descriptor(
                    final_descriptor,
                    generation_id=generation_id,
                    expected_bytes=receipt_bytes,
                )
            finally:
                os.close(final_descriptor)
        self.assert_bound()
        return (
            FileRecord(
                path=(
                    f"{CANONICAL_OUTPUT_ROOT}/generations/{generation_id}/"
                    f"{CANONICAL_RECEIPT_NAME}"
                ),
                sha256=generation_id,
                size_bytes=len(observed),
            ),
            True,
        )

    def read_generation(
        self,
        *,
        generation_id: str,
    ) -> tuple[bytes, FileRecord]:
        self.assert_bound()
        with self.open_directory(
            ("generations", generation_id),
            create=False,
        ) as generation:
            content = _validate_generation_descriptor(
                generation,
                generation_id=generation_id,
                expected_bytes=None,
            )
        return (
            content,
            FileRecord(
                path=(
                    f"{CANONICAL_OUTPUT_ROOT}/generations/{generation_id}/"
                    f"{CANONICAL_RECEIPT_NAME}"
                ),
                sha256=_digest(content),
                size_bytes=len(content),
            ),
        )


def _cleanup_generation_at(generations_descriptor: int, name: str) -> None:
    try:
        descriptor = open_child_directory(
            generations_descriptor,
            name,
            create=False,
        )
    except DescriptorIOError:
        return
    try:
        try:
            os.fchmod(descriptor, 0o750)
            for child in os.listdir(descriptor):
                os.unlink(child, dir_fd=descriptor)
        finally:
            os.close(descriptor)
        os.rmdir(name, dir_fd=generations_descriptor)
        fsync_directory_descriptor(generations_descriptor)
    except OSError:
        return


def _validate_generation_descriptor(
    descriptor: int,
    *,
    generation_id: str,
    expected_bytes: bytes | None,
) -> bytes:
    metadata = os.fstat(descriptor)
    identity = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != GENERATION_MODE
        or metadata.st_nlink != 2
    ):
        _fail("generation_contract")
    if os.listdir(descriptor) != [CANONICAL_RECEIPT_NAME]:
        _fail("generation_tree")
    try:
        content, _ = read_file_at(
            descriptor,
            CANONICAL_RECEIPT_NAME,
            maximum_bytes=MAX_SOURCE_BYTES,
            expected_mode=RECEIPT_MODE,
            require_nlink_one=True,
        )
    except DescriptorIOError as exc:
        _fail(f"generation_receipt_{exc}")
    if _digest(content) != generation_id:
        _fail("generation_self_hash")
    if expected_bytes is not None and content != expected_bytes:
        _fail("generation_collision")
    after = os.fstat(descriptor)
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_nlink,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if after_identity != identity or os.listdir(descriptor) != [CANONICAL_RECEIPT_NAME]:
        _fail("generation_changed")
    return content


def _binding(document: Mapping[str, Any]) -> str:
    core = {key: value for key, value in document.items() if key != "binding_sha256"}
    return _digest(_canonical_json_bytes(core))


def _commit_document(
    loaded: LoadedConfig,
    generation_id: str,
    receipt: FileRecord,
) -> dict[str, Any]:
    expected = _mapping(
        _mapping(loaded.document.get("evidence_gate"), "evidence_gate").get("expected"),
        "evidence_expected",
    )
    commit: dict[str, Any] = {
        "schema_version": COMMIT_SCHEMA,
        "status": "committed",
        "scope": "publication_static_audit_v2_only",
        "completion_state": dict(COMPLETION_STATE),
        "publication_namespace": _publication_namespace(loaded),
        "generation_id": generation_id,
        "receipt": receipt.as_dict(),
        "locked_evaluation_contract": _locked_contract(expected),
    }
    commit["binding_sha256"] = _binding(commit)
    return commit


def _validate_commit_contract(
    commit: Mapping[str, Any],
    loaded: LoadedConfig,
) -> tuple[str, FileRecord]:
    _expect_keys(
        commit,
        {
            "schema_version",
            "status",
            "scope",
            "completion_state",
            "publication_namespace",
            "generation_id",
            "receipt",
            "locked_evaluation_contract",
            "binding_sha256",
        },
        "commit_keys",
    )
    if (
        commit.get("schema_version") != COMMIT_SCHEMA
        or commit.get("status") != "committed"
        or commit.get("scope") != "publication_static_audit_v2_only"
        or commit.get("binding_sha256") != _binding(commit)
        or commit.get("publication_namespace") != _publication_namespace(loaded)
    ):
        _fail("commit_contract")
    _validate_completion_state(commit.get("completion_state"), "commit_completion")
    expected = _mapping(
        _mapping(loaded.document.get("evidence_gate"), "evidence_gate").get("expected"),
        "evidence_expected",
    )
    _validate_locked_contract(
        commit.get("locked_evaluation_contract"),
        expected,
        "commit_locked_contract",
    )
    generation_id = _sha(commit.get("generation_id"), "commit_generation")
    receipt = _expected_record(commit.get("receipt"), "commit_receipt")
    expected_path = (
        f"{CANONICAL_OUTPUT_ROOT}/generations/{generation_id}/{CANONICAL_RECEIPT_NAME}"
    )
    if receipt.path != expected_path or receipt.sha256 != generation_id:
        _fail("commit_receipt_binding")
    return generation_id, receipt


def _assert_config_unchanged(
    workspace: RootDirectory,
    loaded: LoadedConfig,
) -> None:
    current = _read_config(workspace, CANONICAL_CONFIG_PATH)
    if current.record != loaded.record or current.document != loaded.document:
        _fail("config_changed")


def _assert_records_unchanged(
    workspace: RootDirectory,
    records: Sequence[FileRecord],
) -> None:
    """Descriptor-recheck every pin without changing receipt counter scope."""

    for index, expected in enumerate(records):
        try:
            content, metadata = workspace.read_bytes(
                expected.path,
                maximum_bytes=MAX_SOURCE_BYTES,
                require_nlink_one=True,
                reject_protected=True,
            )
        except (DescriptorIOError, ProtectedPathError) as exc:
            _fail(f"source_recheck_{index}_{exc}")
        if (
            metadata.st_size != expected.size_bytes
            or _digest(content) != expected.sha256
        ):
            _fail(f"source_recheck_{index}_fingerprint")


def _validate_loaded(
    workspace: RootDirectory,
    loaded: LoadedConfig,
    store: PublicationStore,
) -> Mapping[str, Any]:
    commit_bytes = store.read_output_file(
        CANONICAL_COMMIT_NAME,
        expected_mode=COMMIT_MODE,
    )
    commit = _parse_json(commit_bytes, "commit")
    generation_id, expected_receipt = _validate_commit_contract(commit, loaded)
    receipt_bytes, actual_receipt = store.read_generation(
        generation_id=generation_id,
    )
    if actual_receipt != expected_receipt:
        _fail("receipt_fingerprint")
    expected_document, records, counters = _build_receipt(workspace, loaded)
    if receipt_bytes != _pretty_json_bytes(expected_document):
        _fail("receipt_content")
    receipt = _parse_json(receipt_bytes, "receipt")
    if receipt != expected_document:
        _fail("receipt_document")
    _validate_completion_state(
        receipt.get("completion_state"),
        "receipt_completion",
    )
    expected = _mapping(
        _mapping(loaded.document.get("evidence_gate"), "evidence_gate").get("expected"),
        "evidence_expected",
    )
    _validate_locked_contract(
        receipt.get("locked_evaluation_contract"),
        expected,
        "receipt_locked_contract",
    )
    if (
        receipt.get("canonical_files_modified") is not False
        or receipt.get("privacy") != counters.privacy()
        or receipt.get("access_counters") != counters.as_dict()
    ):
        _fail("receipt_observation_contract")
    _assert_records_unchanged(workspace, records)
    _assert_config_unchanged(workspace, loaded)
    store.assert_bound()
    return {
        "status": "passed",
        "scope": "publication_static_audit_v2_only",
        "generation_id": generation_id,
        "commit": f"{CANONICAL_OUTPUT_ROOT}/{CANONICAL_COMMIT_NAME}",
        "receipt": actual_receipt.path,
        "source_pin_count": len(records),
        "completion_state": dict(COMPLETION_STATE),
        "locked_evaluation_contract": receipt["locked_evaluation_contract"],
        "access_counter_scope": "single_deterministic_receipt_reconstruction",
        "access_counters": counters.as_dict(),
        "privacy": counters.privacy(),
    }


def _prepare_existing_commit(
    workspace: RootDirectory,
    loaded: LoadedConfig,
    store: PublicationStore,
) -> tuple[bytes | None, bytes | None]:
    store.assert_bound()
    metadata = store.held_output_metadata(CANONICAL_COMMIT_NAME)
    if metadata is None:
        return None, None
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != COMMIT_MODE
        or metadata.st_nlink != 1
    ):
        _fail("existing_commit_contract")
    prior = store.read_output_file(
        CANONICAL_COMMIT_NAME,
        expected_mode=COMMIT_MODE,
    )
    _validate_loaded(workspace, loaded, store)
    return prior, prior


def _rollback_commit(
    store: PublicationStore,
    *,
    new_bytes: bytes,
    previous_valid: bytes | None,
    prior: bytes | None,
) -> None:
    metadata = store.held_output_metadata(CANONICAL_COMMIT_NAME)
    if metadata is None:
        if prior is None:
            return
        _fail("rollback_failed")
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != COMMIT_MODE
        or metadata.st_nlink != 1
    ):
        _fail("rollback_failed")
    current = store.read_held_output_file(
        CANONICAL_COMMIT_NAME,
        expected_mode=COMMIT_MODE,
    )
    if current != new_bytes:
        if prior is not None and current == prior:
            return
        _fail("rollback_failed")
    if previous_valid is not None:
        store.atomic_replace_held(
            CANONICAL_COMMIT_NAME,
            previous_valid,
            mode=COMMIT_MODE,
        )
        if (
            store.read_held_output_file(
                CANONICAL_COMMIT_NAME,
                expected_mode=COMMIT_MODE,
            )
            != previous_valid
        ):
            _fail("rollback_failed")
    else:
        store.unlink_held(CANONICAL_COMMIT_NAME)
        if store.held_output_metadata(CANONICAL_COMMIT_NAME) is not None:
            _fail("rollback_failed")


def audit_paper_publication(
    *,
    config_path: str | Path = CANONICAL_CONFIG_PATH,
    workspace_root: str | Path,
) -> Mapping[str, Any]:
    """Run and atomically publish the deterministic offline v2 receipt."""

    try:
        with RootDirectory.open(workspace_root) as workspace:
            loaded = _read_config(workspace, config_path)
            with PublicationStore.open(
                workspace,
                exclusive=True,
                create=True,
            ) as store:
                publication_counters = PublicationCounters()
                _assert_config_unchanged(workspace, loaded)
                receipt, source_records, _ = _build_receipt(workspace, loaded)
                _assert_records_unchanged(workspace, source_records)
                receipt_bytes = _pretty_json_bytes(receipt)
                generation_id = _digest(receipt_bytes)
                receipt_record, generation_created = store.publish_generation(
                    generation_id=generation_id,
                    receipt_bytes=receipt_bytes,
                )
                if generation_created:
                    publication_counters.generation_files_written += 1
                else:
                    publication_counters.generation_files_reused += 1
                _assert_records_unchanged(workspace, source_records)
                _assert_config_unchanged(workspace, loaded)
                commit = _commit_document(
                    loaded,
                    generation_id,
                    receipt_record,
                )
                commit_bytes = _pretty_json_bytes(commit)
                previous_valid, prior = _prepare_existing_commit(
                    workspace,
                    loaded,
                    store,
                )
                try:
                    store.atomic_replace(
                        CANONICAL_COMMIT_NAME,
                        commit_bytes,
                        mode=COMMIT_MODE,
                    )
                    publication_counters.commit_files_written += 1
                    validated = _validate_loaded(workspace, loaded, store)
                    if validated.get("generation_id") != generation_id:
                        _fail("published_generation")
                except BaseException:
                    try:
                        _rollback_commit(
                            store,
                            new_bytes=commit_bytes,
                            previous_valid=previous_valid,
                            prior=prior,
                        )
                    except BaseException as rollback_error:
                        raise PublicationV2Error("rollback_failed") from rollback_error
                    raise
                return {
                    **validated,
                    "publication_observation": {
                        "counter_scope": "current_publish_transaction",
                        **publication_counters.as_dict(),
                    },
                }
    except DescriptorIOError as exc:
        _fail(f"descriptor_{exc}")


def validate_publication_receipt(
    *,
    config_path: str | Path = CANONICAL_CONFIG_PATH,
    workspace_root: str | Path,
) -> Mapping[str, Any]:
    """Deeply validate the v2 commit, receipt and all current source pins."""

    try:
        with RootDirectory.open(workspace_root) as workspace:
            loaded = _read_config(workspace, config_path)
            with PublicationStore.open(
                workspace,
                exclusive=False,
                create=False,
            ) as store:
                _assert_config_unchanged(workspace, loaded)
                return _validate_loaded(workspace, loaded, store)
    except DescriptorIOError as exc:
        _fail(f"descriptor_{exc}")


def _validate_network_observation(document: Mapping[str, Any]) -> None:
    _expect_keys(
        document,
        {
            "schema_version",
            "status",
            "scope",
            "completion_state",
            "canonical_completion_evidence",
            "observed_at_utc",
            "config",
            "source_ledger",
            "request_count",
            "observations",
            "access_counter_scope",
            "access_counters",
            "privacy",
        },
        "network_observation_keys",
    )
    if (
        document.get("schema_version") != NETWORK_OBSERVATION_SCHEMA
        or document.get("status") != "observation_only"
        or document.get("scope") != "network_observation_v2_noncanonical"
        or document.get("canonical_completion_evidence") is not False
    ):
        _fail("network_observation_contract")
    _validate_completion_state(
        document.get("completion_state"),
        "network_completion_state",
    )
    timestamp = _string(
        document.get("observed_at_utc"),
        "network_timestamp",
    )
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", timestamp) is None:
        _fail("network_timestamp")
    try:
        datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        _fail("network_timestamp")
    _expected_record(document.get("config"), "network_config")
    _expected_record(document.get("source_ledger"), "network_source_ledger")
    observations = _list(document.get("observations"), "network_records")
    request_count = _integer(
        document.get("request_count"),
        "network_request_count",
        minimum=1,
    )
    if request_count != len(observations):
        _fail("network_request_count")
    counters = _mapping(document.get("access_counters"), "network_access_counters")
    expected_counter_keys = set(AccessCounters().as_dict())
    if set(counters) != expected_counter_keys:
        _fail("network_access_counters")
    validated_counters = {
        key: _integer(
            counters.get(key),
            f"network_access_counter_{key}",
        )
        for key in expected_counter_keys
    }
    expected_counters = AccessCounters(
        config_files_read=1,
        referenced_source_files_read=2,
        aggregate_source_files_read=1,
        canonical_files_read=1,
        network_requests=request_count,
    ).as_dict()
    if (
        document.get("access_counter_scope")
        != "network_observation_collection_before_write"
        or validated_counters != expected_counters
    ):
        _fail("network_access_counter_contract")
    expected_privacy = AccessCounters(**validated_counters).privacy()
    if document.get("privacy") != expected_privacy:
        _fail("network_privacy")
    for index, item in enumerate(observations):
        record = _mapping(item, f"network_record_{index}")
        _expect_keys(
            record,
            {
                "url",
                "expected_status",
                "observed_status",
                "effective_url",
                "matches_expected",
            },
            f"network_record_{index}_keys",
        )
        try:
            v1._https_url(record.get("url"), f"network_url_{index}")
            v1._https_url(record.get("effective_url"), f"network_effective_{index}")
        except v1.PublicationAuditError:
            _fail(f"network_url_{index}")
        expected_status = _integer(
            record.get("expected_status"),
            f"network_expected_{index}",
            minimum=100,
        )
        observed_status = _integer(
            record.get("observed_status"),
            f"network_observed_{index}",
        )
        if expected_status > 599 or observed_status > 599:
            _fail(f"network_status_{index}")
        if record.get("matches_expected") is not (expected_status == observed_status):
            _fail(f"network_match_{index}")
    urls = [record["url"] for record in observations]
    if urls != sorted(set(urls)):
        _fail("network_url_order")


def _validate_network_contract_binding(
    document: Mapping[str, Any],
    base_contract: Mapping[str, Any],
) -> None:
    contract = _mapping(
        base_contract.get("external_url_contract"),
        "network_external_contract",
    )
    expected_records = _list(
        contract.get("records"),
        "network_external_records",
    )
    observations = _list(document.get("observations"), "network_records")
    if len(observations) != len(expected_records):
        _fail("network_contract_count")
    for index, (observed_value, expected_value) in enumerate(
        zip(observations, expected_records, strict=True)
    ):
        observed = _mapping(observed_value, f"network_record_{index}")
        expected = _mapping(expected_value, f"network_expected_record_{index}")
        if observed.get("url") != expected.get("url") or observed.get(
            "expected_status"
        ) != expected.get("expected_status"):
            _fail(f"network_contract_record_{index}")


def observe_external_urls(
    *,
    config_path: str | Path = CANONICAL_CONFIG_PATH,
    workspace_root: str | Path,
    requester: Requester,
    observed_at: datetime,
    timeout_seconds: float = 20.0,
) -> Mapping[str, Any]:
    """Collect explicit noncanonical URL observations with measured counters."""

    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
        or not math.isfinite(timeout_seconds)
    ):
        _fail("network_timeout")
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
        _fail("network_observed_at_timezone")
    try:
        with RootDirectory.open(workspace_root) as workspace:
            loaded = _read_config(workspace, config_path)
            counters = AccessCounters(config_files_read=1)
            base, _ = _load_base_contract(workspace, loaded.document, counters)
            canonical = _mapping(loaded.document.get("canonical"), "canonical")
            _, ledger_record = _read_record(
                workspace,
                canonical.get("source_ledger"),
                counters=counters,
                category="canonical",
                code="canonical_source_ledger",
            )
            contract = _mapping(
                base.get("external_url_contract"),
                "external_url_contract",
            )
            observations: list[dict[str, Any]] = []
            for index, item in enumerate(
                _list(contract.get("records"), "external_records")
            ):
                record = _mapping(item, f"external_record_{index}")
                try:
                    url = v1._https_url(
                        record.get("url"),
                        f"external_url_{index}",
                    )
                except v1.PublicationAuditError:
                    _fail(f"external_url_{index}")
                expected_status = _integer(
                    record.get("expected_status"),
                    f"external_status_{index}",
                    minimum=100,
                )
                observed_status, effective_url = requester(url, timeout_seconds)
                counters.network_requests += 1
                observations.append(
                    {
                        "url": url,
                        "expected_status": expected_status,
                        "observed_status": observed_status,
                        "effective_url": effective_url,
                        "matches_expected": observed_status == expected_status,
                    }
                )
            document = {
                "schema_version": NETWORK_OBSERVATION_SCHEMA,
                "status": "observation_only",
                "scope": "network_observation_v2_noncanonical",
                "completion_state": dict(COMPLETION_STATE),
                "canonical_completion_evidence": False,
                "observed_at_utc": observed_at.astimezone(timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z"),
                "config": loaded.record.as_dict(),
                "source_ledger": ledger_record.as_dict(),
                "request_count": counters.network_requests,
                "observations": observations,
                "access_counter_scope": ("network_observation_collection_before_write"),
                "access_counters": counters.as_dict(),
                "privacy": counters.privacy(),
            }
            _validate_network_observation(document)
            _validate_network_contract_binding(document, base)
            return document
    except DescriptorIOError as exc:
        _fail(f"descriptor_{exc}")


def write_network_observation(
    *,
    document: Mapping[str, Any],
    output_path: str | Path,
    workspace_root: str | Path,
) -> str:
    """Write a validated noncanonical observation under the v2 output lock."""

    _validate_network_observation(document)
    relative = _safe_path(
        Path(output_path).as_posix(),
        "network_output_path",
        protected=True,
    )
    prefix = PurePosixPath(CANONICAL_NETWORK_ROOT)
    if relative.parent != prefix or not relative.name.endswith(".json"):
        _fail("network_output_namespace")
    try:
        with RootDirectory.open(workspace_root) as workspace:
            loaded = _read_config(workspace, CANONICAL_CONFIG_PATH)
            if document.get("config") != loaded.record.as_dict():
                _fail("network_config_binding")
            counters = AccessCounters(config_files_read=1)
            base, _ = _load_base_contract(workspace, loaded.document, counters)
            canonical = _mapping(loaded.document.get("canonical"), "canonical")
            _, ledger_record = _read_record(
                workspace,
                canonical.get("source_ledger"),
                counters=counters,
                category="canonical",
                code="network_source_ledger",
            )
            if document.get("source_ledger") != ledger_record.as_dict():
                _fail("network_source_ledger_binding")
            expected_write_binding_counters = {
                **counters.as_dict(),
                "network_requests": document.get("request_count"),
            }
            if document.get("access_counters") != expected_write_binding_counters:
                _fail("network_access_counter_binding")
            _validate_network_contract_binding(document, base)
            _assert_config_unchanged(workspace, loaded)
            with PublicationStore.open(
                workspace,
                exclusive=True,
                create=True,
            ) as store:
                _, rechecked_ledger = _read_record(
                    workspace,
                    canonical.get("source_ledger"),
                    counters=AccessCounters(),
                    category="canonical",
                    code="network_source_ledger_recheck",
                )
                if rechecked_ledger != ledger_record:
                    _fail("network_source_ledger_changed")
                with store.open_directory(
                    ("network_observations",),
                    create=True,
                ) as output:
                    output_bytes = _pretty_json_bytes(document)
                    created = False
                    try:
                        write_new_file_at(
                            output,
                            relative.name,
                            output_bytes,
                            mode=COMMIT_MODE,
                        )
                        created = True
                        observed, _ = read_file_at(
                            output,
                            relative.name,
                            maximum_bytes=MAX_SOURCE_BYTES,
                            expected_mode=COMMIT_MODE,
                            require_nlink_one=True,
                        )
                        if observed != output_bytes:
                            _fail("network_output_readback")
                        _, final_ledger = _read_record(
                            workspace,
                            canonical.get("source_ledger"),
                            counters=AccessCounters(),
                            category="canonical",
                            code="network_source_ledger_final",
                        )
                        if final_ledger != ledger_record:
                            _fail("network_source_ledger_changed")
                        store.assert_bound()
                        _assert_config_unchanged(workspace, loaded)
                    except BaseException:
                        if created:
                            try:
                                current, _ = read_file_at(
                                    output,
                                    relative.name,
                                    maximum_bytes=MAX_SOURCE_BYTES,
                                    expected_mode=COMMIT_MODE,
                                    require_nlink_one=True,
                                )
                                if current != output_bytes:
                                    _fail("network_output_rollback_failed")
                                os.unlink(relative.name, dir_fd=output)
                                fsync_directory_descriptor(output)
                            except BaseException as rollback_error:
                                raise PublicationV2Error(
                                    "network_output_rollback_failed"
                                ) from rollback_error
                        raise
    except DescriptorIOError as exc:
        _fail(f"descriptor_{exc}")
    return relative.as_posix()


__all__ = [
    "CANONICAL_COMMIT_NAME",
    "CANONICAL_CONFIG_PATH",
    "CANONICAL_NETWORK_ROOT",
    "CANONICAL_OUTPUT_ROOT",
    "CANONICAL_RECEIPT_NAME",
    "COMMIT_SCHEMA",
    "COMPLETION_STATE",
    "CONFIG_SCHEMA",
    "LOCKED_KEY_COUNT",
    "LOCKED_KEY_SET_SHA256",
    "NETWORK_OBSERVATION_SCHEMA",
    "PENDING_DEFAULT_RE",
    "PublicationV2Error",
    "RECEIPT_SCHEMA",
    "audit_paper_publication",
    "locked_key_set_digest",
    "observe_external_urls",
    "validate_config_only",
    "validate_publication_receipt",
    "write_network_observation",
]
