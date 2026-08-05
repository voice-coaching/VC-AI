"""Read-only P04/P05 integrity and semantic-currency validation.

The validator deliberately distinguishes two questions:

* whether the current manuscript evidence baseline is internally intact; and
* whether newly completed aggregate evidence has been promoted into the paper.

The current canonical snapshot is expected to pass the first question while
returning ``integrity_pass_semantic_update_pending`` for the second.  That
status is not a P04/P05 completion claim and does not authorize manuscript,
evidence-map, tracker, locked-test, or external-human state changes.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, NoReturn, cast

import yaml  # type: ignore[import-untyped]


CONFIG_SCHEMA = "voice_coach.p04_p05_gap_validation_config.v1"
REPORT_SCHEMA = "voice_coach.p04_p05_gap_validation_report.v1"
RECEIPT_SCHEMA = "voice_coach.p04_p05_gap_validation_receipt.v1"
COMMIT_SCHEMA = "voice_coach.p04_p05_gap_validation_commit.v1"

EXPECTED_AUDIT_STATUS = "integrity_pass_semantic_update_pending"
CANONICAL_CONFIG_PATH = "configs/experiments/p04_p05_gap_validation_v1.yaml"
DEFAULT_OUTPUT_ROOT = "artifacts/reproducibility/p04_p05_gap_validation"
OUTPUT_PREFIX = ("artifacts", "reproducibility")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GAP_ROW_RE = re.compile(
    r"^\|\s*(?P<domain>[MSFER])(?P<number>\d{2})\s*"
    r"\|\s*(?P<status>[ABC])\s*\|",
    flags=re.MULTILINE,
)
MAX_SOURCE_BYTES = 8 * 1024 * 1024
FORBIDDEN_SOURCE_PARTS = frozenset(
    {
        ".env",
        "raw",
        "restricted",
        "stack",
        "calibration",
        "locked_test",
        "locked-test",
        "secret",
        "secrets",
        "credential",
        "credentials",
    }
)
REQUIRED_SOURCES = (
    "methods_gap",
    "manuscript",
    "claim_gap",
    "evidence_map",
    "validation_doc",
    "validation_report",
    "tracker",
    "b07_config",
    "b07_commit",
    "b07_registry",
    "b07_aggregate_report",
    "b07_paired_report",
    "license_ledger",
    "license_matrix",
)
REQUIRED_IMPLEMENTATION_SOURCES = ("module", "cli")
EXPECTED_FINDING_CODES = ("B07_COMPLETED_EVIDENCE_NOT_PROMOTED",)
EXPECTED_RESOLUTION_CODES = (
    "CLAIM_GAP_P04_C_COUNT_RESOLVED",
    "DATA_LICENSE_LEDGER_EVIDENCE_MAPPING_RESOLVED",
)
REPORT_FILE_NAME = "report.json"
RECEIPT_FILE_NAME = "receipt.json"
COMMIT_FILE_NAME = "commit.json"
PUBLICATION_LOCK_NAME = ".publish.lock"
NAMESPACE_LOCK_NAME = ".p04_p05_gap_validation.publish.lock"
GENERATION_MODE = 0o550
GENERATION_NLINK = 2
PUBLISHED_FILE_MODE = 0o440
COMMIT_MODE = 0o440
LOCK_MODE = 0o600


class GapValidationError(RuntimeError):
    """Fail-closed validation error with a stable machine-readable code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _UniqueKeyLoader(yaml.SafeLoader):  # type: ignore[misc]
    pass


def _construct_unique_mapping(
    loader: yaml.Loader,
    node: yaml.Node,
    deep: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            _fail("yaml_duplicate_key")
        result[str(key)] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True)
class Fingerprint:
    """Verified regular-file identity."""

    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class LoadedSource:
    """Stable aggregate source bytes and their verified identity."""

    fingerprint: Fingerprint
    content: bytes


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


def _fail(code: str) -> NoReturn:
    raise GapValidationError(code)


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return cast(Mapping[str, Any], value)


def _sequence(value: object, code: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        _fail(code)
    return value


def _string(value: object, code: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(code)
    return value


def _integer(value: object, code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        _fail(code)
    return value


def _number(value: object, code: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        _fail(code)
    result = float(value)
    if not math.isfinite(result):
        _fail(code)
    return result


def _sha(value: object, code: str) -> str:
    candidate = _string(value, code)
    if SHA256_RE.fullmatch(candidate) is None:
        _fail(code)
    return candidate


def _canonical_json_bytes(value: object, *, pretty: bool = False) -> bytes:
    if pretty:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        return (text + "\n").encode("utf-8")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_forbidden_source_part(part: str) -> bool:
    normalized = part.casefold()
    return normalized in FORBIDDEN_SOURCE_PARTS or normalized.startswith(".env.")


def _binding(document: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in document.items() if key != "binding_sha256"}
    return _digest_bytes(_canonical_json_bytes(payload))


def _load_yaml_bytes(value: bytes, code: str) -> Mapping[str, Any]:
    try:
        loaded = yaml.load(value.decode("utf-8"), Loader=_UniqueKeyLoader)
    except (UnicodeError, yaml.YAMLError):
        _fail(code)
    return _mapping(loaded, code)


def _load_json_bytes(value: bytes, code: str) -> Mapping[str, Any]:
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                _fail(f"{code}_duplicate_key")
            result[key] = item
        return result

    def reject_constant(_: str) -> NoReturn:
        _fail(f"{code}_non_finite")

    try:
        loaded = json.loads(
            value,
            object_pairs_hook=unique_pairs,
            parse_constant=reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError):
        _fail(code)
    return _mapping(loaded, code)


def _decode_text(value: bytes, code: str) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeError:
        _fail(code)


def _load_canonical_config(
    *,
    config_path: str | Path,
    workspace_root: str | Path,
) -> tuple[Path, LoadedSource, Mapping[str, Any]]:
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
    if configured.as_posix() != CANONICAL_CONFIG_PATH:
        _fail("config_namespace")
    candidate = _workspace_path(
        root,
        configured,
        must_exist=True,
        code="config",
    )
    source = _dynamic_fingerprint(root, candidate, code="config")
    document = _load_yaml_bytes(source.content, "config_parse")
    _validate_config(document)
    return root, source, document


def _validate_record_shape(value: object, code: str) -> None:
    record = _mapping(value, code)
    if set(record) != {"path", "sha256", "size_bytes", "aggregate_only"}:
        _fail(f"{code}_keys")
    path = _string(record.get("path"), f"{code}_path")
    lexical = Path(path)
    if lexical.is_absolute() or ".." in lexical.parts or "." in lexical.parts:
        _fail(f"{code}_path")
    if any(_is_forbidden_source_part(part) for part in lexical.parts):
        _fail(f"{code}_forbidden_path")
    _sha(record.get("sha256"), f"{code}_sha256")
    size_bytes = _integer(record.get("size_bytes"), f"{code}_size")
    if size_bytes < 1 or size_bytes > MAX_SOURCE_BYTES:
        _fail(f"{code}_size")
    if record.get("aggregate_only") is not True:
        _fail(f"{code}_aggregate_only")


def _validate_config(document: Mapping[str, Any]) -> None:
    expected_keys = {
        "schema_version",
        "status",
        "actual_state",
        "contract",
        "sources",
        "implementation_sources",
        "semantic_contract",
    }
    if set(document) != expected_keys:
        _fail("config_keys")
    if document.get("schema_version") != CONFIG_SCHEMA:
        _fail("config_schema")
    if document.get("status") != "ready":
        _fail("config_status")

    actual_state = _mapping(document.get("actual_state"), "actual_state")
    expected_actual_keys = {
        "canonical_files_read",
        "aggregate_sources_read",
        "locked_test_files_read",
        "raw_or_restricted_files_read",
        "external_human_records_read",
        "outputs_written",
    }
    if set(actual_state) != expected_actual_keys or any(
        _integer(actual_state.get(key), f"actual_state_{key}") != 0
        for key in expected_actual_keys
    ):
        _fail("config_actual_state")

    contract = _mapping(document.get("contract"), "contract")
    expected_contract_keys = {
        "expected_status",
        "gap",
        "evidence",
        "pending",
        "locked_guard",
        "registry_metric_splits",
        "output_root",
        "canonical_write_allowed",
        "p04_complete_claim_allowed",
        "p05_complete_claim_allowed",
        "manuscript_stale_resolved_claim_allowed",
    }
    if set(contract) != expected_contract_keys:
        _fail("contract_keys")
    if contract.get("expected_status") != EXPECTED_AUDIT_STATUS:
        _fail("contract_status")
    if contract.get("canonical_write_allowed") is not False:
        _fail("contract_canonical_write")
    for key in (
        "p04_complete_claim_allowed",
        "p05_complete_claim_allowed",
        "manuscript_stale_resolved_claim_allowed",
    ):
        if contract.get(key) is not False:
            _fail(f"contract_{key}")
    output_root = Path(_string(contract.get("output_root"), "contract_output_root"))
    if (
        output_root.is_absolute()
        or ".." in output_root.parts
        or tuple(output_root.parts[: len(OUTPUT_PREFIX)]) != OUTPUT_PREFIX
        or output_root.as_posix() != DEFAULT_OUTPUT_ROOT
    ):
        _fail("contract_output_root")

    gap = _mapping(contract.get("gap"), "contract_gap")
    if set(gap) != {"total_rows", "domain_rows", "status_counts", "domain_status"}:
        _fail("contract_gap_keys")
    if _integer(gap.get("total_rows"), "contract_gap_total") != 40:
        _fail("contract_gap_total")
    domain_rows = _mapping(gap.get("domain_rows"), "contract_gap_domains")
    if set(domain_rows) != set("MSFER") or any(
        _integer(domain_rows.get(domain), f"contract_gap_domain_{domain}") != 8
        for domain in "MSFER"
    ):
        _fail("contract_gap_domains")
    status_counts = _mapping(gap.get("status_counts"), "contract_gap_status")
    if set(status_counts) != {"A", "B", "C"} or {
        key: _integer(status_counts.get(key), f"contract_gap_status_{key}")
        for key in ("A", "B", "C")
    } != {"A": 33, "B": 0, "C": 7}:
        _fail("contract_gap_status")
    domain_status = _mapping(gap.get("domain_status"), "contract_gap_domain_status")
    if set(domain_status) != set("MSFER"):
        _fail("contract_gap_domain_status")
    for domain, expected in {
        "M": {"A": 7, "B": 0, "C": 1},
        "S": {"A": 7, "B": 0, "C": 1},
        "F": {"A": 6, "B": 0, "C": 2},
        "E": {"A": 6, "B": 0, "C": 2},
        "R": {"A": 7, "B": 0, "C": 1},
    }.items():
        observed = _mapping(
            domain_status.get(domain),
            f"contract_gap_domain_status_{domain}",
        )
        if (
            set(observed) != {"A", "B", "C"}
            or {
                key: _integer(
                    observed.get(key),
                    f"contract_gap_domain_status_{domain}_{key}",
                )
                for key in ("A", "B", "C")
            }
            != expected
        ):
            _fail("contract_gap_domain_status")

    evidence = _mapping(contract.get("evidence"), "contract_evidence")
    if set(evidence) != {
        "artifact_count",
        "artifact_passed",
        "claim_count",
        "claim_passed",
    }:
        _fail("contract_evidence_keys")
    if {
        key: _integer(evidence.get(key), f"contract_evidence_{key}") for key in evidence
    } != {
        "artifact_count":  35,
        "artifact_passed":  35,
        "claim_count": 475,
        "claim_passed": 475,
    }:
        _fail("contract_evidence")

    pending = _mapping(contract.get("pending"), "contract_pending")
    if set(pending) != {"marker_count", "markers_sha256"}:
        _fail("contract_pending_keys")
    if _integer(pending.get("marker_count"), "contract_pending_count") != 18:
        _fail("contract_pending_count")
    _sha(pending.get("markers_sha256"), "contract_pending_sha")

    locked = _mapping(contract.get("locked_guard"), "contract_locked")
    if set(locked) != {"entry_count", "expected_true_count", "status"}:
        _fail("contract_locked_keys")
    if (
        _integer(locked.get("entry_count"), "contract_locked_count") != 16
        or _integer(locked.get("expected_true_count"), "contract_locked_true") != 0
        or locked.get("status") != "passed"
    ):
        _fail("contract_locked")
    splits = tuple(
        _string(item, "contract_split")
        for item in _sequence(
            contract.get("registry_metric_splits"),
            "contract_splits",
        )
    )
    if splits != ("dev", "dev_exploratory", "inner_val"):
        _fail("contract_splits")

    sources = _mapping(document.get("sources"), "sources")
    if tuple(sources) != REQUIRED_SOURCES:
        _fail("source_order")
    for name in REQUIRED_SOURCES:
        _validate_record_shape(sources.get(name), f"source_{name}")
    implementations = _mapping(
        document.get("implementation_sources"),
        "implementation_sources",
    )
    if tuple(implementations) != REQUIRED_IMPLEMENTATION_SOURCES:
        _fail("implementation_source_order")
    for name in REQUIRED_IMPLEMENTATION_SOURCES:
        _validate_record_shape(implementations.get(name), f"implementation_{name}")

    semantic = _mapping(document.get("semantic_contract"), "semantic_contract")
    if set(semantic) != {
        "expected_finding_codes",
        "expected_resolution_codes",
        "b07",
        "claim_gap_resolution",
        "license",
        "external_pending_anchors",
    }:
        _fail("semantic_contract_keys")
    finding_codes = tuple(
        _string(item, "semantic_finding_code")
        for item in _sequence(
            semantic.get("expected_finding_codes"),
            "semantic_finding_codes",
        )
    )
    if finding_codes != EXPECTED_FINDING_CODES:
        _fail("semantic_finding_codes")
    resolution_codes = tuple(
        _string(item, "semantic_resolution_code")
        for item in _sequence(
            semantic.get("expected_resolution_codes"),
            "semantic_resolution_codes",
        )
    )
    if resolution_codes != EXPECTED_RESOLUTION_CODES:
        _fail("semantic_resolution_codes")
    _validate_b07_contract(_mapping(semantic.get("b07"), "semantic_b07"))
    claim_resolution = _mapping(
        semantic.get("claim_gap_resolution"),
        "semantic_claim_gap_resolution",
    )
    if set(claim_resolution) != {
        "expected_c_count",
        "current_anchor",
        "forbidden_stale_anchor",
    }:
        _fail("semantic_claim_gap_resolution_keys")
    if (
        _integer(
            claim_resolution.get("expected_c_count"),
            "semantic_claim_gap_expected_c_count",
        )
        != 7
    ):
        _fail("semantic_claim_gap_expected_c_count")
    _string(
        claim_resolution.get("current_anchor"),
        "semantic_claim_gap_current_anchor",
    )
    _string(
        claim_resolution.get("forbidden_stale_anchor"),
        "semantic_claim_gap_forbidden_anchor",
    )
    license_contract = _mapping(semantic.get("license"), "semantic_license")
    if set(license_contract) != {
        "ledger_path_token",
        "ledger_sha256",
        "evidence_alias",
        "required_ledger_anchors",
    }:
        _fail("semantic_license_keys")
    _string(license_contract.get("ledger_path_token"), "semantic_license_path")
    _sha(license_contract.get("ledger_sha256"), "semantic_license_sha")
    _string(license_contract.get("evidence_alias"), "semantic_license_alias")
    for item in _sequence(
        license_contract.get("required_ledger_anchors"),
        "semantic_license_anchors",
    ):
        _string(item, "semantic_license_anchor")
    external = _mapping(
        semantic.get("external_pending_anchors"),
        "semantic_external",
    )
    if not external:
        _fail("semantic_external_empty")
    for name, anchors in external.items():
        if name not in REQUIRED_SOURCES:
            _fail("semantic_external_source")
        values = _sequence(anchors, f"semantic_external_{name}")
        if not values:
            _fail("semantic_external_anchor_empty")
        for anchor in values:
            _string(anchor, f"semantic_external_{name}_anchor")


def _validate_b07_contract(contract: Mapping[str, Any]) -> None:
    expected_keys = {
        "generation_id",
        "artifact_count",
        "seeds",
        "mean_phone_macro_f1",
        "sample_std_phone_macro_f1",
        "mean_error_auprc",
        "sample_std_error_auprc",
        "explicit_refit_phone_macro_f1",
        "mean_phone_macro_f1_delta",
        "manuscript_future_gop_anchors",
        "required_absent_token",
        "claim_gap_legacy_scope_anchor",
    }
    if set(contract) != expected_keys:
        _fail("semantic_b07_keys")
    _sha(contract.get("generation_id"), "semantic_b07_generation")
    if _integer(contract.get("artifact_count"), "semantic_b07_artifact_count") != 21:
        _fail("semantic_b07_artifact_count")
    seeds = tuple(
        _integer(item, "semantic_b07_seed")
        for item in _sequence(contract.get("seeds"), "semantic_b07_seeds")
    )
    if seeds != (42, 43, 44):
        _fail("semantic_b07_seeds")
    for key in (
        "mean_phone_macro_f1",
        "sample_std_phone_macro_f1",
        "mean_error_auprc",
        "sample_std_error_auprc",
        "explicit_refit_phone_macro_f1",
        "mean_phone_macro_f1_delta",
    ):
        _number(contract.get(key), f"semantic_b07_{key}")
    anchors = _sequence(
        contract.get("manuscript_future_gop_anchors"),
        "semantic_b07_manuscript_anchors",
    )
    if len(anchors) != 2:
        _fail("semantic_b07_manuscript_anchors")
    for anchor in anchors:
        _string(anchor, "semantic_b07_manuscript_anchor")
    _string(contract.get("required_absent_token"), "semantic_b07_absent_token")
    _string(
        contract.get("claim_gap_legacy_scope_anchor"),
        "semantic_b07_claim_anchor",
    )


def validate_config_only(
    config_path: str | Path,
    *,
    workspace_root: str | Path,
) -> Mapping[str, Any]:
    """Validate only the config document and touch no declared source/output."""

    _, config_source, _ = _load_canonical_config(
        config_path=config_path,
        workspace_root=workspace_root,
    )
    return {
        "status": "valid_config_only",
        "schema_version": CONFIG_SCHEMA,
        "config": {
            "path": CANONICAL_CONFIG_PATH,
            "sha256": config_source.fingerprint.sha256,
            "size_bytes": config_source.fingerprint.size_bytes,
        },
        "declared_source_count": len(REQUIRED_SOURCES),
        "declared_implementation_source_count": len(REQUIRED_IMPLEMENTATION_SOURCES),
        "access": {
            "canonical_files_read": 0,
            "aggregate_sources_read": 0,
            "locked_test_files_read": 0,
            "raw_or_restricted_files_read": 0,
            "external_human_records_read": 0,
            "outputs_written": 0,
        },
        "completion_claims": {
            "p04_complete": False,
            "p05_complete": False,
            "manuscript_semantic_stale_resolved": False,
        },
    }


def _workspace_path(
    root: Path,
    configured: str | Path,
    *,
    must_exist: bool,
    code: str,
) -> Path:
    lexical = Path(configured)
    if (
        lexical.is_absolute()
        or not lexical.parts
        or ".." in lexical.parts
        or "." in lexical.parts
    ):
        _fail(f"{code}_path")
    if any(_is_forbidden_source_part(part) for part in lexical.parts):
        _fail(f"{code}_forbidden_path")
    current = root
    for index, part in enumerate(lexical.parts):
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            if must_exist:
                _fail(f"{code}_missing")
            break
        except OSError:
            _fail(f"{code}_lstat")
        if stat.S_ISLNK(metadata.st_mode):
            _fail(f"{code}_symlink")
        if index < len(lexical.parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
            _fail(f"{code}_parent_not_directory")
    candidate = root / lexical
    try:
        candidate.resolve(strict=must_exist).relative_to(root)
    except (OSError, ValueError):
        _fail(f"{code}_outside_workspace")
    return candidate


def _stable_read(
    path: Path,
    code: str,
    *,
    expected_mode: int | None = None,
    require_nlink_one: bool = False,
) -> bytes:
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
        if before.st_size < 1 or before.st_size > MAX_SOURCE_BYTES:
            _fail(f"{code}_size")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                _fail(f"{code}_short_read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _fail(f"{code}_grew")
        after = os.fstat(descriptor)
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
            _fail(f"{code}_changed")
        content = b"".join(chunks)
    finally:
        os.close(descriptor)
    try:
        path_after = path.lstat()
    except OSError:
        _fail(f"{code}_post_lstat")
    path_identity_after = (
        path_after.st_dev,
        path_after.st_ino,
        path_after.st_mode,
        path_after.st_nlink,
        path_after.st_size,
        path_after.st_mtime_ns,
        path_after.st_ctime_ns,
    )
    if stat.S_ISLNK(path_after.st_mode) or path_identity_after != identity_after:
        _fail(f"{code}_changed_after_read")
    return content


def _configured_fingerprint(
    root: Path,
    value: object,
    *,
    code: str,
) -> LoadedSource:
    record = _mapping(value, code)
    _validate_record_shape(record, code)
    relative = _string(record.get("path"), f"{code}_path")
    path = _workspace_path(root, relative, must_exist=True, code=code)
    content = _stable_read(path, code)
    actual_sha = _digest_bytes(content)
    actual_size = len(content)
    if actual_sha != _sha(record.get("sha256"), f"{code}_sha256"):
        _fail(f"{code}_hash_mismatch")
    if actual_size != _integer(record.get("size_bytes"), f"{code}_size"):
        _fail(f"{code}_size_mismatch")
    return LoadedSource(
        fingerprint=Fingerprint(path=path, sha256=actual_sha, size_bytes=actual_size),
        content=content,
    )


def _dynamic_fingerprint(
    root: Path,
    path: Path,
    *,
    code: str,
    expected_mode: int | None = None,
    require_nlink_one: bool = False,
) -> LoadedSource:
    configured = path
    if configured.is_absolute():
        try:
            configured = configured.relative_to(root)
        except ValueError:
            _fail(f"{code}_outside_workspace")
    resolved = _workspace_path(
        root,
        configured,
        must_exist=True,
        code=code,
    )
    try:
        resolved.relative_to(root)
    except ValueError:
        _fail(f"{code}_outside_workspace")
    content = _stable_read(
        resolved,
        code,
        expected_mode=expected_mode,
        require_nlink_one=require_nlink_one,
    )
    return LoadedSource(
        fingerprint=Fingerprint(
            path=resolved,
            sha256=_digest_bytes(content),
            size_bytes=len(content),
        ),
        content=content,
    )


def _record(root: Path, source: LoadedSource | Fingerprint) -> dict[str, Any]:
    fingerprint = source.fingerprint if isinstance(source, LoadedSource) else source
    return {
        "path": fingerprint.path.relative_to(root).as_posix(),
        "sha256": fingerprint.sha256,
        "size_bytes": fingerprint.size_bytes,
    }


def _publication_namespace(root: Path, config_file: LoadedSource) -> dict[str, str]:
    config_relative = config_file.fingerprint.path.relative_to(root).as_posix()
    if config_relative != CANONICAL_CONFIG_PATH:
        _fail("config_namespace")
    return {
        "config_path": config_relative,
        "config_sha256": config_file.fingerprint.sha256,
        "output_root": DEFAULT_OUTPUT_ROOT,
        "generations_name": "generations",
        "report_name": REPORT_FILE_NAME,
        "receipt_name": RECEIPT_FILE_NAME,
        "commit_name": COMMIT_FILE_NAME,
    }


def _validate_embedded_namespace(
    value: object,
    *,
    root: Path,
    config_file: LoadedSource,
    code: str,
) -> None:
    namespace = _mapping(value, code)
    expected = _publication_namespace(root, config_file)
    if set(namespace) != set(expected) or dict(namespace) != expected:
        _fail(f"{code}_mismatch")


def _assert_loaded_source_unchanged(
    root: Path,
    expected: LoadedSource,
    *,
    code: str,
) -> None:
    actual = _dynamic_fingerprint(
        root,
        expected.fingerprint.path,
        code=code,
    )
    if actual != expected:
        _fail(f"{code}_changed")


def _load_declared_sources(
    *,
    root: Path,
    config: Mapping[str, Any],
) -> tuple[dict[str, LoadedSource], dict[str, LoadedSource]]:
    source_specs = _mapping(config.get("sources"), "sources")
    sources = {
        name: _configured_fingerprint(
            root,
            source_specs.get(name),
            code=f"source_{name}",
        )
        for name in REQUIRED_SOURCES
    }
    implementation_specs = _mapping(
        config.get("implementation_sources"),
        "implementation_sources",
    )
    implementations = {
        name: _configured_fingerprint(
            root,
            implementation_specs.get(name),
            code=f"implementation_{name}",
        )
        for name in REQUIRED_IMPLEMENTATION_SOURCES
    }
    return sources, implementations


def _text_sources(sources: Mapping[str, LoadedSource]) -> dict[str, str]:
    return {
        name: _decode_text(sources[name].content, f"{name}_text")
        for name in (
            "methods_gap",
            "manuscript",
            "claim_gap",
            "validation_doc",
            "tracker",
            "license_ledger",
            "license_matrix",
        )
    }


def _count_gap_rows(
    methods_gap: str,
) -> tuple[dict[str, str], Counter[str], dict[str, Counter[str]]]:
    rows: dict[str, str] = {}
    statuses: Counter[str] = Counter()
    domain_statuses: dict[str, Counter[str]] = {domain: Counter() for domain in "MSFER"}
    for match in GAP_ROW_RE.finditer(methods_gap):
        identifier = f"{match.group('domain')}{match.group('number')}"
        status_value = match.group("status")
        if identifier in rows:
            _fail("gap_duplicate_id")
        rows[identifier] = status_value
        statuses[status_value] += 1
        domain_statuses[match.group("domain")][status_value] += 1
    return rows, statuses, domain_statuses


def _pending_markers(manuscript: str, pattern: str) -> tuple[list[str], str]:
    try:
        found = re.findall(pattern, manuscript, flags=re.MULTILINE)
    except re.error:
        _fail("pending_regex")
    markers = [
        "".join(item) if isinstance(item, tuple) else str(item) for item in found
    ]
    digest = _digest_bytes("\n".join(sorted(markers)).encode("utf-8"))
    return markers, digest


def _short_sha(value: str) -> str:
    return f"{value[:8]}…{value[-4:]}"


def _extract_tracker_row(tracker: str, task: str) -> str:
    match = re.search(
        rf"^\|\s*{re.escape(task)}\s*\|[^\n]+$",
        tracker,
        flags=re.MULTILINE,
    )
    if match is None:
        _fail(f"tracker_{task.casefold()}_row")
    return match.group(0)


def _require_anchor(text: str, anchor: str, code: str) -> None:
    if text.count(anchor) != 1:
        _fail(code)


def _assert_close(actual: object, expected: float, code: str) -> float:
    value = _number(actual, code)
    if not math.isclose(value, expected, rel_tol=0.0, abs_tol=1e-15):
        _fail(code)
    return value


def _validate_gap_integrity(
    *,
    config: Mapping[str, Any],
    texts: Mapping[str, str],
    evidence_map: Mapping[str, Any],
    validation_report: Mapping[str, Any],
    source_records: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    contract = _mapping(config.get("contract"), "contract")
    gap_contract = _mapping(contract.get("gap"), "contract_gap")
    rows, statuses, domain_statuses = _count_gap_rows(texts["methods_gap"])
    total_expected = _integer(gap_contract.get("total_rows"), "gap_total_expected")
    if len(rows) != total_expected:
        _fail("gap_row_count")
    expected_domain_rows = _mapping(
        gap_contract.get("domain_rows"),
        "gap_domain_rows_expected",
    )
    observed_domain_rows = Counter(identifier[0] for identifier in rows)
    for domain in "MSFER":
        if observed_domain_rows[domain] != _integer(
            expected_domain_rows.get(domain),
            f"gap_domain_{domain}_expected",
        ):
            _fail(f"gap_domain_{domain}_count")
    expected_status = _mapping(
        gap_contract.get("status_counts"),
        "gap_status_expected",
    )
    observed_status = {key: statuses[key] for key in ("A", "B", "C")}
    if observed_status != {
        key: _integer(expected_status.get(key), f"gap_status_{key}_expected")
        for key in ("A", "B", "C")
    }:
        _fail("gap_status_counts")
    expected_domain_status = _mapping(
        gap_contract.get("domain_status"),
        "gap_domain_status_expected",
    )
    for domain in "MSFER":
        configured = _mapping(
            expected_domain_status.get(domain),
            f"gap_domain_status_{domain}_expected",
        )
        observed = {key: domain_statuses[domain][key] for key in ("A", "B", "C")}
        expected = {
            key: _integer(
                configured.get(key),
                f"gap_domain_status_{domain}_{key}_expected",
            )
            for key in ("A", "B", "C")
        }
        if observed != expected:
            _fail(f"gap_domain_{domain}_status")
    expected_summary = "| **합계** | **33** | **0** | **7** | **82.5%** | **82.5%** |"
    _require_anchor(texts["methods_gap"], expected_summary, "gap_summary_row")

    evidence_contract = _mapping(contract.get("evidence"), "contract_evidence")
    summary = _mapping(validation_report.get("summary"), "validation_summary")
    observed_summary = {
        key: _integer(summary.get(key), f"validation_summary_{key}")
        for key in (
            "artifact_count",
            "artifact_passed",
            "claim_count",
            "claim_passed",
        )
    }
    expected_summary_values = {
        key: _integer(evidence_contract.get(key), f"evidence_expected_{key}")
        for key in observed_summary
    }
    if observed_summary != expected_summary_values:
        _fail("validation_summary")
    if validation_report.get("status") != "passed":
        _fail("validation_status")
    artifacts = _mapping(evidence_map.get("artifacts"), "evidence_artifacts")
    claims = _sequence(evidence_map.get("claims"), "evidence_claims")
    if len(artifacts) != observed_summary["artifact_count"]:
        _fail("evidence_artifact_count")
    if len(claims) != observed_summary["claim_count"]:
        _fail("evidence_claim_count")

    pending_contract = _mapping(contract.get("pending"), "contract_pending")
    pending_map = _mapping(evidence_map.get("pending_contract"), "evidence_pending")
    pending_report = _mapping(
        validation_report.get("pending_contract"),
        "validation_pending",
    )
    expected_pending_count = _integer(
        pending_contract.get("marker_count"),
        "pending_expected_count",
    )
    expected_pending_sha = _sha(
        pending_contract.get("markers_sha256"),
        "pending_expected_sha",
    )
    marker_regex = _string(pending_map.get("marker_regex"), "pending_marker_regex")
    markers, marker_sha = _pending_markers(texts["manuscript"], marker_regex)
    if (
        len(markers) != expected_pending_count
        or marker_sha != expected_pending_sha
        or _integer(pending_map.get("expected_count"), "evidence_pending_count")
        != expected_pending_count
        or _sha(
            pending_map.get("expected_markers_sha256"),
            "evidence_pending_sha",
        )
        != expected_pending_sha
        or _integer(
            pending_report.get("marker_count"),
            "validation_pending_count",
        )
        != expected_pending_count
        or _sha(
            pending_report.get("markers_sha256"),
            "validation_pending_sha",
        )
        != expected_pending_sha
        or pending_report.get("status") != "passed"
        or pending_report.get("errors") != []
    ):
        _fail("pending_contract")

    locked_contract = _mapping(contract.get("locked_guard"), "contract_locked")
    locked_map = _mapping(evidence_map.get("locked_test_guard"), "evidence_locked")
    access_checks = _sequence(
        locked_map.get("access_checks"),
        "evidence_locked_access_checks",
    )
    locked_report = _mapping(
        validation_report.get("locked_test_guard"),
        "validation_locked",
    )
    accessed = _mapping(locked_report.get("accessed"), "validation_locked_accessed")
    expected_locked_count = _integer(
        locked_contract.get("entry_count"),
        "locked_expected_count",
    )
    true_count = sum(value is True for value in accessed.values())
    if (
        len(access_checks) != expected_locked_count
        or len(accessed) != expected_locked_count
        or any(value is not False for value in accessed.values())
        or true_count
        != _integer(
            locked_contract.get("expected_true_count"),
            "locked_expected_true",
        )
        or locked_report.get("errors") != []
        or locked_report.get("status") != locked_contract.get("status")
    ):
        _fail("locked_guard")
    splits = tuple(
        _string(item, "validation_locked_split")
        for item in _sequence(
            locked_report.get("registry_metric_splits"),
            "validation_locked_splits",
        )
    )
    expected_splits = tuple(
        _string(item, "expected_split")
        for item in _sequence(
            contract.get("registry_metric_splits"),
            "expected_splits",
        )
    )
    if splits != expected_splits:
        _fail("locked_split_guard")

    if evidence_map.get("schema_version") != 1:
        _fail("evidence_schema")
    manuscript_record = _mapping(
        validation_report.get("manuscript"),
        "validation_manuscript",
    )
    evidence_record = _mapping(
        validation_report.get("evidence_map"),
        "validation_evidence_map",
    )
    if (
        manuscript_record.get("path") != source_records["manuscript"]["path"]
        or manuscript_record.get("sha256") != source_records["manuscript"]["sha256"]
        or evidence_record.get("path") != source_records["evidence_map"]["path"]
        or evidence_record.get("sha256") != source_records["evidence_map"]["sha256"]
        or evidence_map.get("manuscript") != source_records["manuscript"]["path"]
    ):
        _fail("validation_source_binding")

    _validate_recorded_hashes(texts=texts, source_records=source_records)
    _validate_external_pending(config=config, texts=texts)
    return {
        "status": "passed",
        "gap_rows": {
            "total": len(rows),
            "domain_counts": {
                domain: observed_domain_rows[domain] for domain in "MSFER"
            },
            "status_counts": observed_status,
            "domain_status_counts": {
                domain: {key: domain_statuses[domain][key] for key in ("A", "B", "C")}
                for domain in "MSFER"
            },
            "coverage_percent": 82.5,
        },
        "evidence_validation": {
            **observed_summary,
            "status": "passed",
        },
        "pending": {
            "marker_count": len(markers),
            "markers_sha256": marker_sha,
            "status": "passed",
        },
        "locked_test_guard": {
            "entry_count": len(accessed),
            "false_count": len(accessed) - true_count,
            "true_count": true_count,
            "registry_metric_splits": list(splits),
            "status": "passed",
        },
        "recorded_hashes": {
            "status": "passed",
            "config_full_source_pins_match": True,
            "p04_tracker_hashes_match": True,
            "p05_tracker_hashes_match": True,
            "full_sha_references_match": True,
            "validation_report_source_bindings_match": True,
        },
        "external_completion_guard": {
            "status": "passed",
            "false_completion_detected": False,
        },
    }


def _validate_recorded_hashes(
    *,
    texts: Mapping[str, str],
    source_records: Mapping[str, dict[str, Any]],
) -> None:
    methods_gap = texts["methods_gap"]
    claim_gap = texts["claim_gap"]
    tracker = texts["tracker"]
    report_sha = source_records["validation_report"]["sha256"]
    if methods_gap.count(report_sha) != 1:
        _fail("methods_gap_validation_report_sha")
    for name in ("manuscript", "evidence_map", "validation_doc", "validation_report"):
        if claim_gap.count(source_records[name]["sha256"]) != 1:
            _fail(f"claim_gap_{name}_sha")
    p04_row = _extract_tracker_row(tracker, "P04")
    p05_row = _extract_tracker_row(tracker, "P05")
    if (
        _short_sha(source_records["methods_gap"]["sha256"]) not in p04_row
        or "40개 중 33개(82.5%)" not in p04_row
        or "후속/외부 C 7개" not in p04_row
        or "stale `9` 표기를 `7`로 교정" not in p04_row
    ):
        _fail("tracker_p04_record")
    for name in (
        "manuscript",
        "evidence_map",
        "validation_report",
        "validation_doc",
    ):
        sha_value = source_records[name]["sha256"]
        tracker_token = _short_sha(sha_value)
        if tracker_token not in p05_row:
            _fail(f"tracker_p05_{name}_record")
    if "35/35·475/475" not in p05_row or "PENDING 18" not in p05_row:
        _fail("tracker_p05_counts")
    ledger_sha = source_records["license_ledger"]["sha256"]
    if f"{ledger_sha[:8]}…{ledger_sha[-5:]}" not in p05_row:
        _fail("tracker_p05_license_ledger_record")


def _validate_external_pending(
    *,
    config: Mapping[str, Any],
    texts: Mapping[str, str],
) -> None:
    semantic = _mapping(config.get("semantic_contract"), "semantic_contract")
    anchors = _mapping(
        semantic.get("external_pending_anchors"),
        "external_pending_anchors",
    )
    for source_name, configured_anchors in anchors.items():
        source_text = texts.get(source_name)
        if source_text is None:
            _fail("external_pending_source")
        for index, anchor_value in enumerate(
            _sequence(configured_anchors, f"external_{source_name}_anchors")
        ):
            anchor = _string(anchor_value, f"external_{source_name}_{index}")
            if source_text.count(anchor) != 1:
                _fail(f"external_pending_anchor_{source_name}_{index}")


def _validate_b07_lineage(
    *,
    root: Path,
    semantic: Mapping[str, Any],
    sources: Mapping[str, LoadedSource],
) -> dict[str, Any]:
    contract = _mapping(semantic.get("b07"), "semantic_b07")
    commit = _load_json_bytes(sources["b07_commit"].content, "b07_commit")
    registry = _load_json_bytes(sources["b07_registry"].content, "b07_registry")
    aggregate = _load_json_bytes(
        sources["b07_aggregate_report"].content,
        "b07_aggregate",
    )
    paired = _load_json_bytes(
        sources["b07_paired_report"].content,
        "b07_paired",
    )
    generation = _mapping(commit.get("generation"), "b07_commit_generation")
    registry_record = _mapping(commit.get("registry"), "b07_commit_registry")
    config_record = _mapping(commit.get("config"), "b07_commit_config")
    generation_id = _sha(contract.get("generation_id"), "b07_expected_generation")
    artifact_count = _integer(
        generation.get("artifact_count"),
        "b07_artifact_count",
    )
    expected_artifact_count = _integer(
        contract.get("artifact_count"),
        "b07_expected_artifact_count",
    )
    expected_seeds = tuple(
        _integer(item, "b07_expected_seed")
        for item in _sequence(contract.get("seeds"), "b07_expected_seeds")
    )
    commit_seeds = tuple(
        _integer(item, "b07_commit_seed")
        for item in _sequence(commit.get("seeds"), "b07_commit_seeds")
    )
    aggregate_source_record = _record(root, sources["b07_aggregate_report"])
    paired_source_record = _record(root, sources["b07_paired_report"])
    registry_source_record = _record(root, sources["b07_registry"])
    config_source_record = _record(root, sources["b07_config"])
    aggregate_parent = Path(
        _string(aggregate_source_record["path"], "b07_aggregate_path")
    )
    paired_parent = Path(_string(paired_source_record["path"], "b07_paired_path"))
    if aggregate_parent.parent != paired_parent.parent:
        _fail("b07_generation_parent")
    generation_records = _sequence(
        generation.get("records"),
        "b07_generation_records",
    )
    records_by_path: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(generation_records):
        record = _mapping(value, f"b07_generation_record_{index}")
        if set(record) != {"path", "sha256", "size_bytes"}:
            _fail("b07_generation_record_keys")
        record_path = _string(record.get("path"), "b07_generation_record_path")
        lexical = Path(record_path)
        if lexical.is_absolute() or ".." in lexical.parts or "." in lexical.parts:
            _fail("b07_generation_record_path")
        if record_path in records_by_path:
            _fail("b07_generation_record_duplicate")
        size_bytes = _integer(
            record.get("size_bytes"),
            "b07_generation_record_size",
        )
        if size_bytes < 1:
            _fail("b07_generation_record_size")
        records_by_path[record_path] = {
            "path": record_path,
            "sha256": _sha(
                record.get("sha256"),
                "b07_generation_record_sha",
            ),
            "size_bytes": size_bytes,
        }
    expected_aggregate_generation_record = {
        "path": aggregate_parent.name,
        "sha256": aggregate_source_record["sha256"],
        "size_bytes": aggregate_source_record["size_bytes"],
    }
    expected_paired_generation_record = {
        "path": paired_parent.name,
        "sha256": paired_source_record["sha256"],
        "size_bytes": paired_source_record["size_bytes"],
    }
    if (
        commit.get("schema_version") != "voice_coach.b07_gop_commit.v1"
        or commit.get("status") != "committed"
        or commit.get("experiment_id") != "aihub71490_b07_gop_acoustic_posterior_v1"
        or commit.get("oracle_alignment") is not True
        or commit.get("deployable") is not False
        or commit_seeds != expected_seeds
        or generation.get("id") != generation_id
        or generation.get("path") != aggregate_parent.parent.as_posix()
        or artifact_count != expected_artifact_count
        or len(generation_records) != expected_artifact_count
        or records_by_path.get(aggregate_parent.name)
        != expected_aggregate_generation_record
        or records_by_path.get(paired_parent.name) != expected_paired_generation_record
        or registry_record.get("path") != registry_source_record["path"]
        or registry_record.get("required_status") != "completed"
        or registry_record.get("sha256") != sources["b07_registry"].fingerprint.sha256
        or config_record.get("path") != config_source_record["path"]
        or config_record.get("sha256") != sources["b07_config"].fingerprint.sha256
    ):
        _fail("b07_commit_contract")
    if (
        registry.get("schema_version") != 1
        or registry.get("status") != "completed"
        or registry.get("failure") is not None
        or registry.get("generation_id") != generation_id
    ):
        _fail("b07_registry_contract")
    seeds = tuple(
        _integer(item, "b07_aggregate_seed")
        for item in _sequence(aggregate.get("seeds"), "b07_aggregate_seeds")
    )
    if (
        aggregate.get("schema_version") != "voice_coach.b07_gop_aggregate.v1"
        or aggregate.get("status") != "completed"
        or aggregate.get("experiment_id") != commit.get("experiment_id")
        or aggregate.get("oracle_alignment") is not True
        or aggregate.get("deployable") is not False
        or aggregate.get("canonical_b03_metric_claimed") is not False
        or seeds != expected_seeds
    ):
        _fail("b07_aggregate_contract")
    aggregate_metrics = _mapping(
        aggregate.get("gop_acoustic_posterior"),
        "b07_aggregate_metrics",
    )
    mean_f1 = _assert_close(
        aggregate_metrics.get("mean_seed_phone_macro_f1"),
        _number(
            contract.get("mean_phone_macro_f1"),
            "b07_expected_mean_f1",
        ),
        "b07_mean_f1",
    )
    std_f1 = _assert_close(
        aggregate_metrics.get("sample_std_seed_phone_macro_f1"),
        _number(
            contract.get("sample_std_phone_macro_f1"),
            "b07_expected_std_f1",
        ),
        "b07_std_f1",
    )
    mean_auprc = _assert_close(
        aggregate_metrics.get("mean_seed_error_auprc"),
        _number(contract.get("mean_error_auprc"), "b07_expected_mean_auprc"),
        "b07_mean_auprc",
    )
    std_auprc = _assert_close(
        aggregate_metrics.get("sample_std_seed_error_auprc"),
        _number(
            contract.get("sample_std_error_auprc"),
            "b07_expected_std_auprc",
        ),
        "b07_std_auprc",
    )
    explicit_refit = _assert_close(
        paired.get("mean_seed_explicit_refit_phone_macro_f1"),
        _number(
            contract.get("explicit_refit_phone_macro_f1"),
            "b07_expected_explicit_refit",
        ),
        "b07_explicit_refit",
    )
    delta = _assert_close(
        paired.get("mean_seed_phone_macro_f1_delta"),
        _number(
            contract.get("mean_phone_macro_f1_delta"),
            "b07_expected_delta",
        ),
        "b07_delta",
    )
    _assert_close(
        paired.get("mean_seed_gop_phone_macro_f1"),
        mean_f1,
        "b07_paired_mean_gop",
    )
    _assert_close(
        paired.get("sample_std_seed_phone_macro_f1_delta"),
        std_f1,
        "b07_paired_std_delta",
    )
    paired_role = "within_run_explicit_feature_refit_control_not_canonical_b03_metric"
    paired_rows = _sequence(paired.get("seed_rows"), "b07_paired_seed_rows")
    paired_seeds = tuple(
        _integer(_mapping(item, "b07_paired_seed_row").get("seed"), "b07_paired_seed")
        for item in paired_rows
    )
    if (
        paired.get("schema_version")
        != "voice_coach.b07_gop_paired_explicit_refit_control.v1"
        or paired.get("comparison_role") != paired_role
        or paired.get("canonical_b03_checkpoint_or_prediction_read") is not False
        or paired.get("same_dev_row_universe") is not True
        or _integer(paired.get("seed_summary_ddof"), "b07_paired_ddof") != 1
        or paired_seeds != expected_seeds
        or any(
            _mapping(item, "b07_paired_seed_row").get("same_row_universe") is not True
            or _mapping(item, "b07_paired_seed_row").get("comparison_role")
            != paired_role
            for item in paired_rows
        )
    ):
        _fail("b07_paired_contract")
    gate = _mapping(aggregate.get("adoption_gate"), "b07_adoption_gate")
    if gate.get("passed") is not True or _number(gate.get("value"), "b07_gate") != 0.6:
        _fail("b07_adoption_gate")
    metrics = _sequence(registry.get("metrics"), "b07_registry_metrics")
    if len(metrics) != 3:
        _fail("b07_registry_metric_count")
    registry_seeds = tuple(
        _integer(
            _mapping(item, "b07_registry_metric").get("step"),
            "b07_registry_seed",
        )
        for item in metrics
    )
    if registry_seeds != expected_seeds or any(
        _mapping(item, "b07_registry_metric").get("split") != "dev" for item in metrics
    ):
        _fail("b07_registry_metrics")
    return {
        "status": "completed",
        "generation_id": generation_id,
        "artifact_count": _integer(generation.get("artifact_count"), "b07_count"),
        "registry_status": "completed",
        "seeds": list(seeds),
        "scope": {
            "development_only": True,
            "oracle_alignment": True,
            "silver_label": True,
            "deployable": False,
            "locked_test_evidence": False,
        },
        "metrics": {
            "mean_phone_macro_f1": mean_f1,
            "sample_std_phone_macro_f1": std_f1,
            "mean_error_auprc": mean_auprc,
            "sample_std_error_auprc": std_auprc,
            "explicit_refit_phone_macro_f1": explicit_refit,
            "mean_phone_macro_f1_delta": delta,
        },
    }


def _semantic_findings(
    *,
    root: Path,
    config: Mapping[str, Any],
    sources: Mapping[str, LoadedSource],
    texts: Mapping[str, str],
    evidence_map: Mapping[str, Any],
    gap_integrity: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    semantic = _mapping(config.get("semantic_contract"), "semantic_contract")
    b07 = _validate_b07_lineage(root=root, semantic=semantic, sources=sources)
    b07_contract = _mapping(semantic.get("b07"), "semantic_b07")
    absent_token = _string(
        b07_contract.get("required_absent_token"),
        "b07_absent_token",
    )
    if (
        texts["claim_gap"].count(absent_token) != 0
        or texts["manuscript"].count(absent_token) != 0
    ):
        _fail("b07_omission_rule_drift")
    legacy_scope_anchor = _string(
        b07_contract.get("claim_gap_legacy_scope_anchor"),
        "b07_legacy_scope_anchor",
    )
    _require_anchor(
        texts["claim_gap"],
        legacy_scope_anchor,
        "b07_claim_gap_scope_anchor",
    )
    future_anchors = [
        _string(item, "b07_future_anchor")
        for item in _sequence(
            b07_contract.get("manuscript_future_gop_anchors"),
            "b07_future_anchors",
        )
    ]
    for index, anchor in enumerate(future_anchors):
        _require_anchor(
            texts["manuscript"],
            anchor,
            f"b07_future_anchor_{index}",
        )

    claim_resolution = _mapping(
        semantic.get("claim_gap_resolution"),
        "claim_gap_resolution",
    )
    expected_c_count = _integer(
        claim_resolution.get("expected_c_count"),
        "claim_gap_expected_c_count",
    )
    current_anchor = _string(
        claim_resolution.get("current_anchor"),
        "claim_gap_current_anchor",
    )
    forbidden_stale_anchor = _string(
        claim_resolution.get("forbidden_stale_anchor"),
        "claim_gap_forbidden_stale_anchor",
    )
    _require_anchor(
        texts["claim_gap"],
        current_anchor,
        "claim_gap_current_anchor",
    )
    canonical_c_count = _integer(
        _mapping(
            _mapping(gap_integrity.get("gap_rows"), "gap_rows").get("status_counts"),
            "gap_status_counts",
        ).get("C"),
        "canonical_c_count",
    )
    current_match = re.search(r"C 등급\s*(\d+)개", current_anchor)
    forbidden_match = re.search(r"C 등급\s*(\d+)개", forbidden_stale_anchor)
    if current_match is None or forbidden_match is None:
        _fail("claim_gap_resolution_anchor_parse")
    current_count = int(current_match.group(1))
    forbidden_stale_count = int(forbidden_match.group(1))
    if (
        expected_c_count != canonical_c_count
        or current_count != canonical_c_count
        or forbidden_stale_count == canonical_c_count
    ):
        _fail("claim_gap_c_count_resolution")
    if forbidden_stale_anchor in texts["claim_gap"]:
        _fail("claim_gap_forbidden_stale_anchor")
    observed_claim_gap_counts = [
        int(match.group(1))
        for match in re.finditer(
            r"후속·외부 artifact가 필요한 P04 C 등급\s*(\d+)개 갱신",
            texts["claim_gap"],
        )
    ]
    if observed_claim_gap_counts != [canonical_c_count]:
        _fail("claim_gap_c_count_mentions")

    license_contract = _mapping(semantic.get("license"), "semantic_license")
    ledger_path_token = _string(
        license_contract.get("ledger_path_token"),
        "license_ledger_path_token",
    )
    ledger_sha = _sha(
        license_contract.get("ledger_sha256"),
        "license_ledger_sha",
    )
    if ledger_sha != sources["license_ledger"].fingerprint.sha256:
        _fail("license_ledger_contract_sha")
    if (
        sources["license_ledger"].fingerprint.path.relative_to(root).as_posix()
        != ledger_path_token
    ):
        _fail("license_ledger_contract_path")
    for index, anchor_value in enumerate(
        _sequence(
            license_contract.get("required_ledger_anchors"),
            "license_required_anchors",
        )
    ):
        anchor = _string(anchor_value, f"license_anchor_{index}")
        _require_anchor(
            texts["license_ledger"],
            anchor,
            f"license_ledger_anchor_{index}",
        )
    evidence_serialized = _decode_text(
        sources["evidence_map"].content,
        "evidence_map_text",
    )
    artifacts = _mapping(evidence_map.get("artifacts"), "evidence_artifacts")
    evidence_alias = _string(
        license_contract.get("evidence_alias"),
        "license_evidence_alias",
    )
    evidence_record = _mapping(
        artifacts.get(evidence_alias),
        "license_evidence_record",
    )
    if set(evidence_record) != {"path", "sha256"} or dict(evidence_record) != {
        "path": ledger_path_token,
        "sha256": ledger_sha,
    }:
        _fail("license_evidence_record")
    evidence_path_mentions = evidence_serialized.count(ledger_path_token)
    evidence_hash_mentions = evidence_serialized.count(ledger_sha)
    manuscript_path_mentions = texts["manuscript"].count(ledger_path_token)
    manuscript_hash_mentions = texts["manuscript"].count(ledger_sha)
    if (
        evidence_path_mentions != 1
        or evidence_hash_mentions != 1
        or manuscript_path_mentions != 1
    ):
        _fail("license_mapping_mentions")

    findings: list[dict[str, Any]] = [
        {
            "code": "B07_COMPLETED_EVIDENCE_NOT_PROMOTED",
            "classification": "semantic_update_pending",
            "observed_completed_evidence": b07,
            "document_state": {
                "claim_gap_b07_mentions": 0,
                "manuscript_b07_mentions": 0,
                "future_gop_anchors": future_anchors,
            },
            "allowed_interpretation": (
                "completed dev-only oracle/silver engineering evidence awaiting "
                "the reviewed S104/S105/B07 paper-amendment path"
            ),
            "forbidden_interpretation": (
                "locked-test, deployable, independent-gold, P04-complete, "
                "or P05-complete evidence"
            ),
        }
    ]
    resolutions: list[dict[str, Any]] = [
        {
            "code": "CLAIM_GAP_P04_C_COUNT_RESOLVED",
            "classification": "resolved_canonical_alignment",
            "canonical_c_count": canonical_c_count,
            "claim_gap_c_count": current_count,
            "current_anchor": current_anchor,
            "forbidden_stale_count": forbidden_stale_count,
            "forbidden_stale_anchor_mentions": 0,
            "completion_state_changed": False,
        },
        {
            "code": "DATA_LICENSE_LEDGER_EVIDENCE_MAPPING_RESOLVED",
            "classification": "resolved_mapping_rights_pending",
            "ledger": {
                "path": sources["license_ledger"]
                .fingerprint.path.relative_to(root)
                .as_posix(),
                "sha256": ledger_sha,
            },
            "evidence_alias": evidence_alias,
            "evidence_map_path_mentions": evidence_path_mentions,
            "evidence_map_hash_mentions": evidence_hash_mentions,
            "manuscript_path_mentions": manuscript_path_mentions,
            "manuscript_hash_mentions": manuscript_hash_mentions,
            "remaining_limitations": [
                "KSS original-rights review remains unresolved",
                "model-artifact and redistribution audit remains unresolved",
                "the manuscript license PENDING marker must remain",
            ],
        },
    ]
    if tuple(item["code"] for item in findings) != EXPECTED_FINDING_CODES:
        _fail("semantic_finding_order")
    if tuple(item["code"] for item in resolutions) != EXPECTED_RESOLUTION_CODES:
        _fail("semantic_resolution_order")
    return findings, b07, resolutions


def _build_report(
    *,
    root: Path,
    config_file: LoadedSource,
    config: Mapping[str, Any],
    sources: Mapping[str, LoadedSource],
    implementations: Mapping[str, LoadedSource],
) -> tuple[dict[str, Any], str]:
    texts = _text_sources(sources)
    evidence_map = _load_yaml_bytes(
        sources["evidence_map"].content,
        "evidence_map_parse",
    )
    validation_report = _load_json_bytes(
        sources["validation_report"].content,
        "validation_report_parse",
    )
    source_records = {name: _record(root, source) for name, source in sources.items()}
    integrity = _validate_gap_integrity(
        config=config,
        texts=texts,
        evidence_map=evidence_map,
        validation_report=validation_report,
        source_records=source_records,
    )
    findings, b07, resolutions = _semantic_findings(
        root=root,
        config=config,
        sources=sources,
        texts=texts,
        evidence_map=evidence_map,
        gap_integrity=integrity,
    )
    core: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA,
        "status": EXPECTED_AUDIT_STATUS,
        "publication_namespace": _publication_namespace(root, config_file),
        "integrity": integrity,
        "semantic_findings": findings,
        "semantic_resolutions": resolutions,
        "b07_completed_evidence": b07,
        "source_records": source_records,
        "config": _record(root, config_file),
        "implementation_sources": {
            name: _record(root, source) for name, source in implementations.items()
        },
        "access": {
            "aggregate_source_files_read": len(sources),
            "aggregate_only": True,
            "raw_or_restricted_files_read": 0,
            "locked_test_files_read": 0,
            "external_human_records_read": 0,
            "canonical_files_written": 0,
        },
        "completion_claims": {
            "p04_complete": False,
            "p05_complete": False,
            "manuscript_semantic_stale_resolved": False,
            "locked_test_completed": False,
            "external_human_tasks_completed": False,
        },
        "limitations": [
            "P04 remains in progress at 33/40 (82.5%).",
            "P05 remains in progress; no final PDF audit is claimed.",
            "PENDING markers and locked-test guards are preserved.",
            "B07 is dev-only oracle/silver evidence and is not deployable evidence.",
            "Human approval and evaluation tasks remain external dependencies.",
        ],
    }
    generation_material = {
        "config": core["config"],
        "implementation_sources": core["implementation_sources"],
        "source_records": core["source_records"],
        "audit_sha256": _digest_bytes(_canonical_json_bytes(core)),
    }
    generation_id = _digest_bytes(_canonical_json_bytes(generation_material))
    report = {
        **core,
        "generation_id": generation_id,
    }
    return report, generation_id


def _validate_output_root(
    root: Path,
    configured: str | Path,
    *,
    protected: Iterable[Path],
) -> Path:
    output = _workspace_path(
        root,
        configured,
        must_exist=False,
        code="output_root",
    )
    relative = output.relative_to(root)
    if tuple(relative.parts[: len(OUTPUT_PREFIX)]) != OUTPUT_PREFIX:
        _fail("output_root_prefix")
    if output in set(protected):
        _fail("output_root_collision")
    try:
        metadata = output.lstat()
    except FileNotFoundError:
        pass
    except OSError:
        _fail("output_root_lstat")
    else:
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            _fail("output_root_not_directory")
    return output


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_safe_directory(root: Path, relative: Path, *, code: str) -> Path:
    path = root
    for part in relative.parts:
        path = path / part
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            try:
                path.mkdir(mode=0o750)
                _fsync_directory(path.parent)
            except FileExistsError:
                try:
                    metadata = path.lstat()
                except OSError:
                    _fail(f"{code}_lstat")
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                    _fail(f"{code}_not_directory")
            except OSError:
                _fail(f"{code}_create")
        except OSError:
            _fail(f"{code}_lstat")
        else:
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                _fail(f"{code}_not_directory")
    return path


def _existing_safe_directory(root: Path, relative: Path, *, code: str) -> Path:
    path = _workspace_path(
        root,
        relative,
        must_exist=True,
        code=code,
    )
    try:
        metadata = path.lstat()
    except OSError:
        _fail(f"{code}_lstat")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        _fail(f"{code}_not_directory")
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


def _write_file(
    path: Path,
    content: bytes,
    *,
    mode: int = PUBLISHED_FILE_MODE,
) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
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


def _fingerprint_bound_file(
    *,
    actual_path: Path,
    canonical_path: Path,
    code: str,
    expected_mode: int | None = None,
    require_nlink_one: bool = False,
) -> LoadedSource:
    try:
        metadata = actual_path.lstat()
    except OSError:
        _fail(f"{code}_lstat")
    if stat.S_ISLNK(metadata.st_mode):
        _fail(f"{code}_symlink")
    content = _stable_read(
        actual_path,
        code,
        expected_mode=expected_mode,
        require_nlink_one=require_nlink_one,
    )
    return LoadedSource(
        fingerprint=Fingerprint(
            path=canonical_path,
            sha256=_digest_bytes(content),
            size_bytes=len(content),
        ),
        content=content,
    )


def _seal_directory(path: Path, parent: Path) -> None:
    try:
        path_before = path.lstat()
    except OSError:
        _fail("generation_seal_missing")
    if stat.S_ISLNK(path_before.st_mode) or not stat.S_ISDIR(path_before.st_mode):
        _fail("generation_not_directory")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _fail("generation_seal_open")
    try:
        descriptor_before = os.fstat(descriptor)
        if not stat.S_ISDIR(descriptor_before.st_mode) or (
            path_before.st_dev,
            path_before.st_ino,
        ) != (
            descriptor_before.st_dev,
            descriptor_before.st_ino,
        ):
            _fail("generation_seal_changed")
        os.fchmod(descriptor, GENERATION_MODE)
        os.fsync(descriptor)
        descriptor_after = os.fstat(descriptor)
        try:
            path_after = path.lstat()
        except OSError:
            _fail("generation_seal_lstat")
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
            _fail("generation_seal_changed")
    except OSError:
        _fail("generation_seal")
    finally:
        os.close(descriptor)
    try:
        _fsync_directory(parent)
    except OSError:
        _fail("generation_seal")


def _validate_generation_tree(
    root: Path,
    generation_root: Path,
    *,
    boundary: PublicationBoundary,
    expected_report: bytes | None = None,
    expected_receipt: bytes | None = None,
) -> tuple[LoadedSource, LoadedSource]:
    if boundary.output_root != root / DEFAULT_OUTPUT_ROOT:
        _fail("output_location_binding")
    expected_parent = boundary.output_root / "generations"
    if generation_root.parent != expected_parent:
        _fail("generation_path")
    if SHA256_RE.fullmatch(generation_root.name) is None:
        _fail("generation_directory_id")
    _assert_publication_boundary(boundary)
    actual_generation_root = boundary.bound_output_path(
        "generations",
        generation_root.name,
    )
    try:
        root_before = actual_generation_root.lstat()
    except OSError:
        _fail("generation_missing")
    if stat.S_ISLNK(root_before.st_mode) or not stat.S_ISDIR(root_before.st_mode):
        _fail("generation_not_directory")
    if stat.S_IMODE(root_before.st_mode) != GENERATION_MODE:
        _fail("generation_mode")
    if root_before.st_nlink != GENERATION_NLINK:
        _fail("generation_nlink")
    root_identity = _directory_identity(root_before)
    try:
        children = sorted(
            actual_generation_root.iterdir(),
            key=lambda item: item.name,
        )
    except OSError:
        _fail("generation_list")
    if [child.name for child in children] != [RECEIPT_FILE_NAME, REPORT_FILE_NAME]:
        _fail("generation_tree")
    by_name = {child.name: child for child in children}
    receipt_source = _fingerprint_bound_file(
        actual_path=by_name[RECEIPT_FILE_NAME],
        canonical_path=generation_root / RECEIPT_FILE_NAME,
        code="generation_receipt",
        expected_mode=PUBLISHED_FILE_MODE,
        require_nlink_one=True,
    )
    report_source = _fingerprint_bound_file(
        actual_path=by_name[REPORT_FILE_NAME],
        canonical_path=generation_root / REPORT_FILE_NAME,
        code="generation_report",
        expected_mode=PUBLISHED_FILE_MODE,
        require_nlink_one=True,
    )
    try:
        root_after = actual_generation_root.lstat()
    except OSError:
        _fail("generation_post_lstat")
    if (
        stat.S_ISLNK(root_after.st_mode)
        or _directory_identity(root_after) != root_identity
    ):
        _fail("generation_changed")
    if (
        expected_report is not None
        and report_source.content != expected_report
        or expected_receipt is not None
        and receipt_source.content != expected_receipt
    ):
        _fail("generation_collision")
    _assert_publication_boundary(boundary)
    return report_source, receipt_source


def _atomic_replace(
    path: Path,
    content: bytes,
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
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _publish_generation(
    *,
    root: Path,
    output_root: Path,
    boundary: PublicationBoundary,
    generation_id: str,
    report_bytes: bytes,
    receipt_bytes: bytes,
) -> tuple[Path, Path]:
    if (
        output_root != boundary.output_root
        or boundary.output_root != root / DEFAULT_OUTPUT_ROOT
    ):
        _fail("output_root_binding")
    _assert_publication_boundary(boundary)
    generations = output_root / "generations"
    actual_generations = boundary.bound_output_path("generations")
    try:
        generations_metadata = actual_generations.lstat()
    except FileNotFoundError:
        try:
            actual_generations.mkdir(mode=0o750)
            os.fsync(boundary.output_descriptor)
            generations_metadata = actual_generations.lstat()
        except FileExistsError:
            try:
                generations_metadata = actual_generations.lstat()
            except OSError:
                _fail("generations_root_lstat")
        except OSError:
            _fail("generations_root_create")
    except OSError:
        _fail("generations_root_lstat")
    if stat.S_ISLNK(generations_metadata.st_mode) or not stat.S_ISDIR(
        generations_metadata.st_mode
    ):
        _fail("generations_root_not_directory")
    final_root = generations / generation_id
    actual_final_root = actual_generations / generation_id
    report_path = final_root / REPORT_FILE_NAME
    receipt_path = final_root / RECEIPT_FILE_NAME
    try:
        actual_final_root.lstat()
    except FileNotFoundError:
        pass
    except OSError:
        _fail("generation_lstat")
    else:
        _validate_generation_tree(
            root,
            final_root,
            boundary=boundary,
            expected_report=report_bytes,
            expected_receipt=receipt_bytes,
        )
        return report_path, receipt_path

    temporary = Path(
        tempfile.mkdtemp(prefix=f".{generation_id}.", dir=actual_generations)
    )
    try:
        _write_file(
            temporary / REPORT_FILE_NAME,
            report_bytes,
            mode=PUBLISHED_FILE_MODE,
        )
        _write_file(
            temporary / RECEIPT_FILE_NAME,
            receipt_bytes,
            mode=PUBLISHED_FILE_MODE,
        )
        _fsync_directory(temporary)
        _seal_directory(temporary, actual_generations)
        try:
            os.rename(temporary, actual_final_root)
        except OSError:
            try:
                actual_final_root.lstat()
            except OSError:
                _fail("generation_publish")
            _validate_generation_tree(
                root,
                final_root,
                boundary=boundary,
                expected_report=report_bytes,
                expected_receipt=receipt_bytes,
            )
        _fsync_directory(actual_generations)
    finally:
        try:
            temporary_metadata = temporary.lstat()
        except OSError:
            pass
        else:
            if stat.S_ISDIR(temporary_metadata.st_mode) and not stat.S_ISLNK(
                temporary_metadata.st_mode
            ):
                try:
                    os.chmod(temporary, 0o750)
                    for child in temporary.iterdir():
                        if child.name in {REPORT_FILE_NAME, RECEIPT_FILE_NAME}:
                            child.unlink(missing_ok=True)
                    temporary.rmdir()
                    _fsync_directory(actual_generations)
                except OSError:
                    pass
    _validate_generation_tree(
        root,
        final_root,
        boundary=boundary,
        expected_report=report_bytes,
        expected_receipt=receipt_bytes,
    )
    _assert_publication_boundary(boundary)
    return report_path, receipt_path


def _load_record(
    root: Path,
    value: object,
    *,
    code: str,
    expected_mode: int | None = None,
    require_nlink_one: bool = False,
) -> LoadedSource:
    record = _mapping(value, code)
    if set(record) != {"path", "sha256", "size_bytes"}:
        _fail(f"{code}_keys")
    relative = _string(record.get("path"), f"{code}_path")
    path = _workspace_path(root, relative, must_exist=True, code=code)
    content = _stable_read(
        path,
        code,
        expected_mode=expected_mode,
        require_nlink_one=require_nlink_one,
    )
    sha = _digest_bytes(content)
    size = len(content)
    if sha != _sha(record.get("sha256"), f"{code}_sha") or size != _integer(
        record.get("size_bytes"), f"{code}_size"
    ):
        _fail(f"{code}_mismatch")
    return LoadedSource(
        fingerprint=Fingerprint(path=path, sha256=sha, size_bytes=size),
        content=content,
    )


def _load_bound_output_record(
    root: Path,
    value: object,
    *,
    boundary: PublicationBoundary,
    code: str,
    expected_mode: int | None = None,
    require_nlink_one: bool = False,
) -> LoadedSource:
    record = _mapping(value, code)
    if set(record) != {"path", "sha256", "size_bytes"}:
        _fail(f"{code}_keys")
    relative_text = _string(record.get("path"), f"{code}_path")
    relative = Path(relative_text)
    if (
        relative.is_absolute()
        or not relative.parts
        or ".." in relative.parts
        or "." in relative.parts
        or any(_is_forbidden_source_part(part) for part in relative.parts)
    ):
        _fail(f"{code}_path")
    output_relative = Path(DEFAULT_OUTPUT_ROOT)
    prefix_length = len(output_relative.parts)
    if tuple(relative.parts[:prefix_length]) != output_relative.parts:
        _fail("output_location_binding")
    tail = relative.parts[prefix_length:]
    if not tail:
        _fail("output_location_binding")
    _assert_publication_boundary(boundary)
    source = _fingerprint_bound_file(
        actual_path=boundary.bound_output_path(*tail),
        canonical_path=root / relative,
        code=code,
        expected_mode=expected_mode,
        require_nlink_one=require_nlink_one,
    )
    if source.fingerprint.sha256 != _sha(
        record.get("sha256"), f"{code}_sha"
    ) or source.fingerprint.size_bytes != _integer(
        record.get("size_bytes"), f"{code}_size"
    ):
        _fail(f"{code}_mismatch")
    _assert_publication_boundary(boundary)
    return source


def _relative_argument(
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
            _fail(f"{code}_path")
    return path


def _validate_committed_gap_audit_loaded(
    *,
    root: Path,
    config_file: LoadedSource,
    config: Mapping[str, Any],
    commit_path: str | Path,
    boundary: PublicationBoundary,
) -> Mapping[str, Any]:
    _assert_publication_boundary(boundary)
    expected_output_root = root / DEFAULT_OUTPUT_ROOT
    if boundary.output_root != expected_output_root:
        _fail("output_location_binding")
    configured_commit = _relative_argument(root, commit_path, code="commit")
    expected_commit_relative = (expected_output_root / COMMIT_FILE_NAME).relative_to(
        root
    )
    if configured_commit != expected_commit_relative:
        _fail("output_location_binding")
    commit_resolved = expected_output_root / COMMIT_FILE_NAME
    commit_source = _fingerprint_bound_file(
        actual_path=boundary.bound_output_path(COMMIT_FILE_NAME),
        canonical_path=commit_resolved,
        code="commit",
        expected_mode=COMMIT_MODE,
        require_nlink_one=True,
    )
    commit = _load_json_bytes(commit_source.content, "commit_parse")
    expected_commit_keys = {
        "schema_version",
        "status",
        "publication_namespace",
        "generation_id",
        "receipt",
        "canonical_files_modified",
        "p04_complete",
        "p05_complete",
        "binding_sha256",
    }
    if (
        set(commit) != expected_commit_keys
        or commit.get("schema_version") != COMMIT_SCHEMA
        or commit.get("status") != "committed"
        or commit.get("canonical_files_modified") is not False
        or commit.get("p04_complete") is not False
        or commit.get("p05_complete") is not False
        or commit.get("binding_sha256") != _binding(commit)
    ):
        _fail("commit_contract")
    _validate_embedded_namespace(
        commit.get("publication_namespace"),
        root=root,
        config_file=config_file,
        code="commit_namespace",
    )
    generation_id = _sha(commit.get("generation_id"), "commit_generation_id")
    expected_generation_root = expected_output_root / "generations" / generation_id
    expected_receipt_path = expected_generation_root / RECEIPT_FILE_NAME
    receipt_source = _load_bound_output_record(
        root,
        commit.get("receipt"),
        boundary=boundary,
        code="receipt",
        expected_mode=PUBLISHED_FILE_MODE,
        require_nlink_one=True,
    )
    if receipt_source.fingerprint.path != expected_receipt_path:
        _fail("output_location_binding")
    receipt = _load_json_bytes(receipt_source.content, "receipt_parse")
    expected_receipt_keys = {
        "schema_version",
        "publication_status",
        "audit_status",
        "publication_namespace",
        "generation_id",
        "config",
        "implementation_sources",
        "source_records",
        "outputs",
        "canonical_files_modified",
        "completion_claims",
        "binding_sha256",
    }
    if (
        set(receipt) != expected_receipt_keys
        or receipt.get("schema_version") != RECEIPT_SCHEMA
        or receipt.get("publication_status") != "completed"
        or receipt.get("audit_status") != EXPECTED_AUDIT_STATUS
        or receipt.get("generation_id") != generation_id
        or receipt.get("canonical_files_modified") is not False
        or receipt.get("binding_sha256") != _binding(receipt)
    ):
        _fail("receipt_contract")
    _validate_embedded_namespace(
        receipt.get("publication_namespace"),
        root=root,
        config_file=config_file,
        code="receipt_namespace",
    )
    completion_claims = _mapping(
        receipt.get("completion_claims"),
        "receipt_completion_claims",
    )
    if set(completion_claims) != {
        "p04_complete",
        "p05_complete",
        "manuscript_semantic_stale_resolved",
    } or any(completion_claims.values()):
        _fail("receipt_completion_claims")

    output_records = _mapping(receipt.get("outputs"), "receipt_outputs")
    if set(output_records) != {"report"}:
        _fail("receipt_outputs")
    report_source = _load_bound_output_record(
        root,
        output_records.get("report"),
        boundary=boundary,
        code="published_report",
        expected_mode=PUBLISHED_FILE_MODE,
        require_nlink_one=True,
    )
    if report_source.fingerprint.path.parent != receipt_source.fingerprint.path.parent:
        _fail("generation_parent_binding")
    if report_source.fingerprint.path.name != REPORT_FILE_NAME:
        _fail("generation_report_name")
    if receipt_source.fingerprint.path.name != RECEIPT_FILE_NAME:
        _fail("generation_receipt_name")
    generation_root = report_source.fingerprint.path.parent
    if generation_root.name != generation_id:
        _fail("generation_directory_id")
    tree_report, tree_receipt = _validate_generation_tree(
        root,
        generation_root,
        boundary=boundary,
    )
    if (
        tree_report.fingerprint != report_source.fingerprint
        or tree_receipt.fingerprint != receipt_source.fingerprint
    ):
        _fail("generation_record_binding")

    receipt_config_record = _mapping(receipt.get("config"), "receipt_config")
    if dict(receipt_config_record) != _record(root, config_file):
        _fail("receipt_config_binding")
    config_source = _load_record(
        root,
        receipt_config_record,
        code="receipt_config",
    )
    if config_source != config_file:
        _fail("receipt_config_binding")
    if (
        commit_resolved != expected_output_root / COMMIT_FILE_NAME
        or generation_root != expected_output_root / "generations" / generation_id
    ):
        _fail("output_location_binding")
    implementation_records = _mapping(
        receipt.get("implementation_sources"),
        "receipt_implementations",
    )
    source_records = _mapping(receipt.get("source_records"), "receipt_sources")
    if set(implementation_records) != set(REQUIRED_IMPLEMENTATION_SOURCES):
        _fail("receipt_implementation_set")
    if set(source_records) != set(REQUIRED_SOURCES):
        _fail("receipt_source_set")
    implementations = {
        name: _load_record(
            root,
            implementation_records.get(name),
            code=f"receipt_implementation_{name}",
        )
        for name in REQUIRED_IMPLEMENTATION_SOURCES
    }
    sources = {
        name: _load_record(
            root,
            source_records.get(name),
            code=f"receipt_source_{name}",
        )
        for name in REQUIRED_SOURCES
    }
    expected_sources, expected_implementations = _load_declared_sources(
        root=root,
        config=config,
    )
    if {name: _record(root, source) for name, source in sources.items()} != {
        name: _record(root, source) for name, source in expected_sources.items()
    }:
        _fail("receipt_source_config_binding")
    if {name: _record(root, source) for name, source in implementations.items()} != {
        name: _record(root, source) for name, source in expected_implementations.items()
    }:
        _fail("receipt_implementation_config_binding")

    recomputed_report, recomputed_generation = _build_report(
        root=root,
        config_file=config_source,
        config=config,
        sources=sources,
        implementations=implementations,
    )
    if recomputed_generation != generation_id:
        _fail("generation_recomputation")
    expected_report_bytes = _canonical_json_bytes(recomputed_report, pretty=True)
    if report_source.content != expected_report_bytes:
        _fail("report_recomputation")
    report = _load_json_bytes(report_source.content, "report_parse")
    if (
        report.get("schema_version") != REPORT_SCHEMA
        or report.get("status") != EXPECTED_AUDIT_STATUS
        or report.get("generation_id") != generation_id
    ):
        _fail("report_contract")
    _validate_embedded_namespace(
        report.get("publication_namespace"),
        root=root,
        config_file=config_file,
        code="report_namespace",
    )
    semantic_finding_codes = [
        item["code"]
        for item in _sequence(
            report.get("semantic_findings"),
            "report_semantic_findings",
        )
    ]
    if tuple(semantic_finding_codes) != EXPECTED_FINDING_CODES:
        _fail("report_semantic_findings")
    semantic_resolution_codes = [
        item["code"]
        for item in _sequence(
            report.get("semantic_resolutions"),
            "report_semantic_resolutions",
        )
    ]
    if tuple(semantic_resolution_codes) != EXPECTED_RESOLUTION_CODES:
        _fail("report_semantic_resolutions")
    _assert_loaded_source_unchanged(
        root,
        config_file,
        code="config_post_deep",
    )
    _assert_publication_boundary(boundary)
    return {
        "status": "valid",
        "audit_status": EXPECTED_AUDIT_STATUS,
        "generation_id": generation_id,
        "commit": _record(root, commit_source),
        "receipt": _record(root, receipt_source),
        "report": _record(root, report_source),
        "semantic_finding_codes": semantic_finding_codes,
        "semantic_resolution_codes": semantic_resolution_codes,
        "canonical_files_modified": False,
        "p04_complete": False,
        "p05_complete": False,
    }


def validate_committed_gap_audit(
    *,
    commit_path: str | Path,
    workspace_root: str | Path,
) -> Mapping[str, Any]:
    """Deeply revalidate a committed report and all aggregate source bindings."""

    root, config_file, config = _load_canonical_config(
        config_path=CANONICAL_CONFIG_PATH,
        workspace_root=workspace_root,
    )
    output_root = _existing_safe_directory(
        root,
        Path(DEFAULT_OUTPUT_ROOT),
        code="output_root",
    )
    with _publication_lock(
        output_root,
        exclusive=False,
        create=False,
    ) as boundary:
        _assert_publication_boundary(boundary)
        _assert_loaded_source_unchanged(
            root,
            config_file,
            code="config_after_lock",
        )
        result = _validate_committed_gap_audit_loaded(
            root=root,
            config_file=config_file,
            config=config,
            commit_path=commit_path,
            boundary=boundary,
        )
        _assert_publication_boundary(boundary)
        return result


def _prepare_existing_commit(
    *,
    root: Path,
    config_file: LoadedSource,
    config: Mapping[str, Any],
    commit_path: Path,
    boundary: PublicationBoundary,
) -> tuple[bytes | None, bytes | None]:
    if commit_path != boundary.output_root / COMMIT_FILE_NAME:
        _fail("output_location_binding")
    actual_commit_path = boundary.bound_output_path(COMMIT_FILE_NAME)
    try:
        metadata = actual_commit_path.lstat()
    except FileNotFoundError:
        return None, None
    except OSError:
        _fail("commit_lstat")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        _fail("commit_not_regular")
    if stat.S_IMODE(metadata.st_mode) != COMMIT_MODE:
        _fail("commit_mode")
    if metadata.st_nlink != 1:
        _fail("commit_nlink")
    prior_bytes = _stable_read(
        actual_commit_path,
        "commit_prior",
        expected_mode=COMMIT_MODE,
        require_nlink_one=True,
    )
    try:
        _validate_committed_gap_audit_loaded(
            root=root,
            config_file=config_file,
            config=config,
            commit_path=commit_path,
            boundary=boundary,
        )
    except GapValidationError:
        return None, prior_bytes
    validated_bytes = _stable_read(
        actual_commit_path,
        "commit_prior_valid",
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
    try:
        commit_path.relative_to(root)
    except ValueError:
        _fail("commit_rollback_path")
    if commit_path != boundary.output_root / COMMIT_FILE_NAME:
        _fail("commit_rollback_path")
    actual_commit_path = boundary.bound_output_path(COMMIT_FILE_NAME)
    try:
        current_bytes = _stable_read(
            actual_commit_path,
            "commit_rollback",
            expected_mode=COMMIT_MODE,
            require_nlink_one=True,
        )
    except GapValidationError:
        if prior_bytes is None:
            try:
                actual_commit_path.lstat()
            except FileNotFoundError:
                return
            except OSError:
                _fail("commit_rollback_failed")
        else:
            try:
                unchanged_bytes = _stable_read(
                    actual_commit_path,
                    "commit_rollback_prior",
                    require_nlink_one=True,
                )
            except GapValidationError:
                _fail("commit_rollback_failed")
            if unchanged_bytes == prior_bytes:
                return
        _fail("commit_rollback_failed")
    if current_bytes != new_commit_bytes:
        if prior_bytes is not None and current_bytes == prior_bytes:
            return
        _fail("commit_rollback_failed")
    if previous_valid_bytes is not None:
        _atomic_replace(actual_commit_path, previous_valid_bytes)
        restored = _stable_read(
            actual_commit_path,
            "commit_restored",
            expected_mode=COMMIT_MODE,
            require_nlink_one=True,
        )
        if restored != previous_valid_bytes:
            _fail("commit_rollback_failed")
        return
    try:
        actual_commit_path.unlink()
        os.fsync(boundary.output_descriptor)
    except OSError:
        _fail("commit_rollback_failed")


def run_gap_audit(
    *,
    config_path: str | Path,
    workspace_root: str | Path,
    output_root: str | Path | None = None,
) -> Mapping[str, Any]:
    """Run the read-only audit and atomically publish a content-addressed report."""

    root, config_file, config = _load_canonical_config(
        config_path=config_path,
        workspace_root=workspace_root,
    )
    configured_output = _string(
        _mapping(config.get("contract"), "contract").get("output_root"),
        "contract_output_root",
    )
    if output_root is not None and Path(output_root) != Path(configured_output):
        _fail("output_root_contract")
    output_candidate = _validate_output_root(
        root,
        configured_output,
        protected={config_file.fingerprint.path},
    )
    output = _ensure_safe_directory(
        root,
        output_candidate.relative_to(root),
        code="output_root",
    )
    with _publication_lock(output, exclusive=True, create=True) as boundary:
        _assert_publication_boundary(boundary)
        _assert_loaded_source_unchanged(
            root,
            config_file,
            code="config_after_lock",
        )
        sources, implementations = _load_declared_sources(root=root, config=config)
        protected = {
            config_file.fingerprint.path,
            *(source.fingerprint.path for source in sources.values()),
            *(source.fingerprint.path for source in implementations.values()),
        }
        if (
            _validate_output_root(
                root,
                configured_output,
                protected=protected,
            )
            != output
        ):
            _fail("output_root_binding")
        _assert_publication_boundary(boundary)
        report, generation_id = _build_report(
            root=root,
            config_file=config_file,
            config=config,
            sources=sources,
            implementations=implementations,
        )
        report_bytes = _canonical_json_bytes(report, pretty=True)
        generation_root = output / "generations" / generation_id
        report_path = generation_root / REPORT_FILE_NAME
        namespace = _publication_namespace(root, config_file)
        receipt: dict[str, Any] = {
            "schema_version": RECEIPT_SCHEMA,
            "publication_status": "completed",
            "audit_status": EXPECTED_AUDIT_STATUS,
            "publication_namespace": namespace,
            "generation_id": generation_id,
            "config": _record(root, config_file),
            "implementation_sources": {
                name: _record(root, source) for name, source in implementations.items()
            },
            "source_records": {
                name: _record(root, source) for name, source in sources.items()
            },
            "outputs": {
                "report": {
                    "path": report_path.relative_to(root).as_posix(),
                    "sha256": _digest_bytes(report_bytes),
                    "size_bytes": len(report_bytes),
                }
            },
            "canonical_files_modified": False,
            "completion_claims": {
                "p04_complete": False,
                "p05_complete": False,
                "manuscript_semantic_stale_resolved": False,
            },
        }
        receipt["binding_sha256"] = _binding(receipt)
        receipt_bytes = _canonical_json_bytes(receipt, pretty=True)
        _assert_publication_boundary(boundary)
        published_report, published_receipt = _publish_generation(
            root=root,
            output_root=output,
            boundary=boundary,
            generation_id=generation_id,
            report_bytes=report_bytes,
            receipt_bytes=receipt_bytes,
        )
        _assert_publication_boundary(boundary)

        _assert_loaded_source_unchanged(
            root,
            config_file,
            code="config_before_commit",
        )
        sources_after, implementations_after = _load_declared_sources(
            root=root,
            config=config,
        )
        if {name: _record(root, source) for name, source in sources_after.items()} != {
            name: _record(root, source) for name, source in sources.items()
        }:
            _fail("source_changed_after_publication")
        if {
            name: _record(root, source)
            for name, source in implementations_after.items()
        } != {name: _record(root, source) for name, source in implementations.items()}:
            _fail("implementation_changed_after_publication")

        commit_path = output / COMMIT_FILE_NAME
        commit: dict[str, Any] = {
            "schema_version": COMMIT_SCHEMA,
            "status": "committed",
            "publication_namespace": namespace,
            "generation_id": generation_id,
            "receipt": {
                "path": published_receipt.relative_to(root).as_posix(),
                "sha256": _digest_bytes(receipt_bytes),
                "size_bytes": len(receipt_bytes),
            },
            "canonical_files_modified": False,
            "p04_complete": False,
            "p05_complete": False,
        }
        commit["binding_sha256"] = _binding(commit)
        commit_bytes = _canonical_json_bytes(commit, pretty=True)
        previous_valid_bytes, prior_bytes = _prepare_existing_commit(
            root=root,
            config_file=config_file,
            config=config,
            commit_path=commit_path,
            boundary=boundary,
        )
        try:
            _assert_publication_boundary(boundary)
            _atomic_replace(
                boundary.bound_output_path(COMMIT_FILE_NAME),
                commit_bytes,
            )
            _assert_publication_boundary(boundary)
            deep = _validate_committed_gap_audit_loaded(
                root=root,
                config_file=config_file,
                config=config,
                commit_path=commit_path,
                boundary=boundary,
            )
            if deep.get("generation_id") != generation_id:
                _fail("deep_validation_generation")
            _assert_publication_boundary(boundary)
        except BaseException:
            try:
                _rollback_commit_if_needed(
                    root=root,
                    commit_path=commit_path,
                    new_commit_bytes=commit_bytes,
                    previous_valid_bytes=previous_valid_bytes,
                    prior_bytes=prior_bytes,
                    boundary=boundary,
                )
            except BaseException as rollback_error:
                raise GapValidationError("commit_rollback_failed") from rollback_error
            raise
        _assert_publication_boundary(boundary)
        return {
            "status": EXPECTED_AUDIT_STATUS,
            "generation_id": generation_id,
            "commit": commit_path.relative_to(root).as_posix(),
            "report": published_report.relative_to(root).as_posix(),
            "receipt": published_receipt.relative_to(root).as_posix(),
            "deep_validation": "valid",
            "semantic_finding_codes": list(EXPECTED_FINDING_CODES),
            "semantic_resolution_codes": list(EXPECTED_RESOLUTION_CODES),
            "canonical_files_modified": False,
            "p04_complete": False,
            "p05_complete": False,
        }


__all__ = [
    "COMMIT_SCHEMA",
    "CONFIG_SCHEMA",
    "DEFAULT_OUTPUT_ROOT",
    "EXPECTED_AUDIT_STATUS",
    "EXPECTED_FINDING_CODES",
    "EXPECTED_RESOLUTION_CODES",
    "GapValidationError",
    "RECEIPT_SCHEMA",
    "REPORT_SCHEMA",
    "run_gap_audit",
    "validate_committed_gap_audit",
    "validate_config_only",
]
