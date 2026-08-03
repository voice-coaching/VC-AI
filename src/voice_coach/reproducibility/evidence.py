from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable, Mapping

import pyarrow.dataset as ds  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]


class EvidenceValidationError(ValueError):
    """Raised when the evidence-map configuration itself is invalid."""


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(
    loader: yaml.Loader, node: yaml.Node, deep: bool = False
) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise EvidenceValidationError(f"duplicate YAML key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)

_FORBIDDEN_PARTS = {".env", "raw", "restricted"}
_NONFINITE_TOKEN_RE = re.compile(r"^[+-]?(?:nan|inf(?:inity)?)$", re.IGNORECASE)


@dataclass(frozen=True)
class _Artifact:
    alias: str
    path: Path
    relative_path: str
    expected_sha256: str
    actual_sha256: str | None
    hash_mode: str
    valid: bool
    error: str | None = None


def _load_map(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvidenceValidationError(f"cannot read evidence map: {exc}") from exc
    try:
        if path.suffix.lower() == ".json":
            value = json.loads(text)
        else:
            value = yaml.load(text, Loader=_UniqueKeyLoader)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise EvidenceValidationError(f"invalid evidence map: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceValidationError("evidence map root must be an object")
    return value


def _safe_relative_path(root: Path, configured: Any, *, role: str) -> tuple[Path, str]:
    if not isinstance(configured, str) or not configured.strip():
        raise EvidenceValidationError(f"{role} path must be a non-empty string")
    relative = Path(configured)
    if relative.is_absolute():
        raise EvidenceValidationError(
            f"{role} path must be workspace-relative: {configured}"
        )
    lowered = [part.lower() for part in relative.parts]
    if any(part in _FORBIDDEN_PARTS or part.startswith(".env") for part in lowered):
        raise EvidenceValidationError(
            f"{role} path is forbidden by privacy policy: {configured}"
        )
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise EvidenceValidationError(
            f"{role} path escapes workspace: {configured}"
        ) from exc
    return resolved, relative.as_posix()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_tree(path: Path) -> str:
    if not path.is_dir():
        raise OSError(f"tree hash target is not a directory: {path}")
    digest = hashlib.sha256()
    files = sorted(candidate for candidate in path.rglob("*") if candidate.is_file())
    for candidate in files:
        if candidate.is_symlink():
            raise OSError(f"tree hash does not permit symlinked files: {candidate}")
        relative = candidate.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_file(candidate).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _assert_finite(value: Any, *, location: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise EvidenceValidationError(f"non-finite number at {location}")
    if isinstance(value, Mapping):
        for key, child in value.items():
            _assert_finite(child, location=f"{location}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_finite(child, location=f"{location}/{index}")


def _load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(
                handle,
                parse_constant=lambda token: (_ for _ in ()).throw(
                    EvidenceValidationError(f"non-finite JSON number: {token}")
                ),
            )
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceValidationError(
            f"cannot load JSON artifact {path}: {exc}"
        ) from exc
    _assert_finite(value, location="$")
    return value


def _json_pointer(value: Any, pointer: Any) -> Any:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        if pointer == "":
            return value
        raise EvidenceValidationError(f"invalid JSON pointer: {pointer!r}")
    current = value
    for raw_part in pointer.split("/")[1:]:
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as exc:
                raise EvidenceValidationError(
                    f"JSON pointer index not found: {pointer}"
                ) from exc
        elif isinstance(current, dict):
            if part not in current:
                raise EvidenceValidationError(f"JSON pointer key not found: {pointer}")
            current = current[part]
        else:
            raise EvidenceValidationError(f"JSON pointer traverses scalar: {pointer}")
    return current


def _numeric(value: Any, *, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceValidationError(
            f"expected numeric value at {location}, got {type(value).__name__}"
        )
    number = float(value)
    if not math.isfinite(number):
        raise EvidenceValidationError(f"non-finite number at {location}")
    return number


def _apply_transform(value: Any, transform: Any, *, location: str) -> Any:
    if transform is None:
        return value
    transforms = transform if isinstance(transform, list) else [transform]
    current = value
    for index, step in enumerate(transforms):
        if not isinstance(step, dict):
            raise EvidenceValidationError(
                f"transform {location}/{index} must be an object"
            )
        operation = step.get("operation")
        if operation == "multiply":
            current = _numeric(current, location=location) * _numeric(
                step.get("value"), location=f"{location}/transform/{index}/value"
            )
        elif operation == "divide":
            divisor = _numeric(
                step.get("value"), location=f"{location}/transform/{index}/value"
            )
            if divisor == 0:
                raise EvidenceValidationError(f"division by zero at {location}")
            current = _numeric(current, location=location) / divisor
        elif operation == "length":
            if not isinstance(current, (list, dict, str)):
                raise EvidenceValidationError(
                    f"length transform requires a collection at {location}"
                )
            current = len(current)
        else:
            raise EvidenceValidationError(
                f"unsupported transform operation: {operation!r}"
            )
    return current


def _parquet_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(
        candidate for candidate in path.rglob("*.parquet") if candidate.is_file()
    )


def _parquet_aggregate(path: Path, source: Mapping[str, Any]) -> int | float:
    files = _parquet_files(path)
    if not files:
        raise EvidenceValidationError(f"no Parquet files found: {path}")
    aggregate = source.get("aggregate")
    if aggregate == "file_count":
        return len(files)
    if aggregate == "row_count":
        return sum(pq.ParquetFile(candidate).metadata.num_rows for candidate in files)
    if aggregate not in {"count_equal", "ratio_percent"}:
        raise EvidenceValidationError(f"unsupported Parquet aggregate: {aggregate!r}")
    column = source.get("column")
    if not isinstance(column, str) or not column:
        raise EvidenceValidationError("Parquet count aggregate requires a column")
    target = source.get("equals")
    dataset = ds.dataset([str(candidate) for candidate in files], format="parquet")
    if column not in dataset.schema.names:
        raise EvidenceValidationError(f"Parquet column not found: {column}")
    matched = 0
    total = 0
    for batch in dataset.to_batches(columns=[column], batch_size=131_072):
        values = batch.column(0).to_pylist()
        matched += sum(value == target for value in values)
        total += len(values)
    if aggregate == "count_equal":
        return matched
    if total == 0:
        raise EvidenceValidationError(
            "cannot calculate a ratio for an empty Parquet dataset"
        )
    return matched * 100.0 / total


def _extract_source(
    source: Mapping[str, Any],
    artifacts: Mapping[str, _Artifact],
    json_cache: dict[str, Any],
) -> Any:
    alias = source.get("artifact")
    if not isinstance(alias, str) or alias not in artifacts:
        raise EvidenceValidationError(f"unknown artifact alias: {alias!r}")
    artifact = artifacts[alias]
    if not artifact.valid:
        raise EvidenceValidationError(f"artifact {alias!r} failed integrity validation")
    kind = source.get("kind")
    if kind == "artifact_sha256":
        value: Any = artifact.actual_sha256
    elif kind == "json_pointer":
        if alias not in json_cache:
            json_cache[alias] = _load_json(artifact.path)
        value = _json_pointer(json_cache[alias], source.get("pointer"))
    elif kind == "json_arithmetic":
        if alias not in json_cache:
            json_cache[alias] = _load_json(artifact.path)
        pointers = source.get("pointers")
        if not isinstance(pointers, list) or not pointers:
            raise EvidenceValidationError(
                "json_arithmetic requires a non-empty pointers list"
            )
        values = [
            _numeric(
                _json_pointer(json_cache[alias], pointer),
                location=f"claim-source:{alias}:{pointer}",
            )
            for pointer in pointers
        ]
        operation = source.get("operation")
        if operation == "sum":
            value = sum(values)
        elif operation == "subtract":
            value = values[0] - sum(values[1:])
        else:
            raise EvidenceValidationError(
                f"unsupported json_arithmetic operation: {operation!r}"
            )
    elif kind == "parquet_metadata":
        value = _parquet_aggregate(artifact.path, source)
    else:
        raise EvidenceValidationError(f"unsupported evidence source kind: {kind!r}")
    _assert_finite(value, location=f"claim-source:{alias}")
    return _apply_transform(
        value, source.get("transform"), location=f"claim-source:{alias}"
    )


def _parse_document_number(token: str) -> Decimal:
    normalized = token.strip().replace(",", "").replace("%", "")
    if _NONFINITE_TOKEN_RE.fullmatch(normalized):
        raise EvidenceValidationError(f"non-finite manuscript number: {token!r}")
    try:
        number = Decimal(normalized)
    except InvalidOperation as exc:
        raise EvidenceValidationError(f"invalid manuscript number: {token!r}") from exc
    if not number.is_finite():
        raise EvidenceValidationError(f"non-finite manuscript number: {token!r}")
    return number


def _validate_claim(
    claim: Mapping[str, Any],
    manuscript: str,
    artifacts: Mapping[str, _Artifact],
    json_cache: dict[str, Any],
) -> dict[str, Any]:
    claim_id = claim.get("id")
    if not isinstance(claim_id, str) or not claim_id:
        raise EvidenceValidationError("each claim requires a non-empty id")
    source = claim.get("source")
    document = claim.get("document")
    if not isinstance(source, dict) or not isinstance(document, dict):
        raise EvidenceValidationError(
            f"claim {claim_id}: source and document must be objects"
        )
    regex = document.get("regex")
    if not isinstance(regex, str) or not regex:
        raise EvidenceValidationError(f"claim {claim_id}: document.regex is required")
    try:
        pattern = re.compile(regex, re.MULTILINE)
    except re.error as exc:
        raise EvidenceValidationError(
            f"claim {claim_id}: invalid regex: {exc}"
        ) from exc
    matches = list(pattern.finditer(manuscript))
    expected_matches = document.get("expected_matches", 1)
    if not isinstance(expected_matches, int) or expected_matches < 1:
        raise EvidenceValidationError(
            f"claim {claim_id}: expected_matches must be a positive integer"
        )
    if len(matches) != expected_matches:
        return {
            "id": claim_id,
            "status": "failed",
            "error": f"document regex matched {len(matches)} time(s), expected {expected_matches}",
        }
    if "value" not in pattern.groupindex:
        raise EvidenceValidationError(
            f"claim {claim_id}: document regex needs a named 'value' group"
        )
    source_value = _extract_source(source, artifacts, json_cache)
    comparison = claim.get("comparison", {})
    if not isinstance(comparison, dict):
        raise EvidenceValidationError(f"claim {claim_id}: comparison must be an object")
    comparison_type = comparison.get("type", "numeric")
    observed_tokens = [match.group("value") for match in matches]
    if comparison_type == "exact":
        expected = str(source_value)
        passed = all(token == expected for token in observed_tokens)
        expected_report: Any = expected
        observed_report: Any = observed_tokens
    elif comparison_type == "numeric":
        source_number = Decimal(
            str(_numeric(source_value, location=f"claim:{claim_id}"))
        )
        decimals = comparison.get("decimals")
        if not isinstance(decimals, int) or decimals < 0:
            raise EvidenceValidationError(
                f"claim {claim_id}: numeric comparison requires non-negative decimals"
            )
        quantum = Decimal(1).scaleb(-decimals)
        expected_number = source_number.quantize(quantum, rounding=ROUND_HALF_UP)
        observed_numbers = [_parse_document_number(token) for token in observed_tokens]
        passed = all(number == expected_number for number in observed_numbers)
        expected_report = format(expected_number, f".{decimals}f")
        observed_report = [str(number) for number in observed_numbers]
    else:
        raise EvidenceValidationError(
            f"claim {claim_id}: unsupported comparison type {comparison_type!r}"
        )
    result: dict[str, Any] = {
        "id": claim_id,
        "status": "passed" if passed else "failed",
        "artifact": source.get("artifact"),
        "source_kind": source.get("kind"),
        "expected": expected_report,
        "observed": observed_report,
    }
    if not passed:
        result["error"] = (
            "manuscript value does not match allowed artifact-derived value"
        )
    return result


def _canonical_pending_hash(markers: Iterable[str]) -> str:
    canonical = "\n".join(sorted(markers)).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validate_pending_contract(config: Any, manuscript: str) -> dict[str, Any] | None:
    if config is None:
        return None
    if not isinstance(config, dict):
        raise EvidenceValidationError("pending_contract must be an object")
    regex = config.get("marker_regex", r"\[PENDING:[^\]]+\]")
    if not isinstance(regex, str):
        raise EvidenceValidationError("pending_contract.marker_regex must be a string")
    try:
        markers = re.findall(regex, manuscript, flags=re.MULTILINE)
    except re.error as exc:
        raise EvidenceValidationError(f"invalid pending marker regex: {exc}") from exc
    if markers and isinstance(markers[0], tuple):
        markers = ["".join(marker) for marker in markers]
    expected_count = config.get("expected_count")
    expected_hash = config.get("expected_markers_sha256")
    errors: list[str] = []
    if not isinstance(expected_count, int) or expected_count < 0:
        raise EvidenceValidationError(
            "pending_contract.expected_count must be a non-negative integer"
        )
    actual_hash = _canonical_pending_hash(markers)
    if len(markers) != expected_count:
        errors.append(
            f"pending marker count is {len(markers)}, expected {expected_count}"
        )
    if not isinstance(expected_hash, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_hash
    ):
        raise EvidenceValidationError(
            "pending_contract.expected_markers_sha256 must be lowercase SHA-256"
        )
    if actual_hash != expected_hash:
        errors.append("pending marker set changed")
    scrubbed = re.sub(regex, "[PENDING]", manuscript, flags=re.MULTILINE)
    forbidden_regexes = config.get("forbidden_numeric_regexes", [])
    if not isinstance(forbidden_regexes, list):
        raise EvidenceValidationError(
            "pending_contract.forbidden_numeric_regexes must be a list"
        )
    for forbidden in forbidden_regexes:
        if not isinstance(forbidden, str):
            raise EvidenceValidationError("pending forbidden regexes must be strings")
        try:
            if re.search(forbidden, scrubbed, flags=re.MULTILINE | re.IGNORECASE):
                errors.append(
                    f"unresolved claim has an unbacked numeric result: {forbidden}"
                )
        except re.error as exc:
            raise EvidenceValidationError(
                f"invalid pending forbidden regex: {exc}"
            ) from exc
    return {
        "status": "failed" if errors else "passed",
        "marker_count": len(markers),
        "markers_sha256": actual_hash,
        "errors": errors,
    }


def _validate_locked_test_guard(
    config: Any,
    manuscript: str,
    artifacts: Mapping[str, _Artifact],
    json_cache: dict[str, Any],
) -> dict[str, Any] | None:
    if config is None:
        return None
    if not isinstance(config, dict):
        raise EvidenceValidationError("locked_test_guard must be an object")
    errors: list[str] = []
    configured_access = config.get("access_checks")
    if configured_access is None:
        configured_access = [config.get("access")]
    if not isinstance(configured_access, list) or not configured_access:
        raise EvidenceValidationError(
            "locked_test_guard requires at least one access check"
        )
    access_values: dict[str, Any] = {}
    for index, access in enumerate(configured_access):
        if not isinstance(access, dict):
            raise EvidenceValidationError(
                "locked_test_guard access checks must be objects"
            )
        alias = access.get("artifact")
        access_value = _extract_source(
            {
                "kind": "json_pointer",
                "artifact": alias,
                "pointer": access.get("pointer"),
            },
            artifacts,
            json_cache,
        )
        key = str(alias) if str(alias) not in access_values else f"{alias}:{index}"
        access_values[key] = access_value
        if access_value is not False:
            errors.append(f"locked-test access flag is not false: {alias}")
    configured_registries = config.get("registries")
    if configured_registries is None:
        registry = config.get("registry")
        configured_registries = [] if registry is None else [registry]
    if not isinstance(configured_registries, list):
        raise EvidenceValidationError("locked_test_guard.registries must be a list")
    registry_splits: list[Any] = []
    for registry in configured_registries:
        if not isinstance(registry, dict):
            raise EvidenceValidationError(
                "locked_test_guard registry checks must be objects"
            )
        alias = registry.get("artifact")
        if not isinstance(alias, str) or alias not in artifacts:
            raise EvidenceValidationError("locked-test registry artifact is unknown")
        artifact = artifacts[alias]
        if not artifact.valid:
            errors.append("locked-test registry artifact failed integrity validation")
        else:
            if alias not in json_cache:
                json_cache[alias] = _load_json(artifact.path)
            records = _json_pointer(json_cache[alias], registry.get("array_pointer"))
            if not isinstance(records, list):
                raise EvidenceValidationError(
                    "locked-test registry pointer must resolve to an array"
                )
            split_field = registry.get("split_field", "split")
            current_splits = [
                record.get(split_field)
                for record in records
                if isinstance(record, dict)
            ]
            registry_splits.extend(current_splits)
            forbidden_splits = set(registry.get("forbidden_splits", ["locked_test"]))
            if any(split in forbidden_splits for split in current_splits):
                errors.append("registry contains a forbidden locked-test metric")
    scrubbed = re.sub(r"\[PENDING:[^\]]+\]", "[PENDING]", manuscript)
    forbidden_regexes = config.get("forbidden_document_regexes", [])
    if not isinstance(forbidden_regexes, list):
        raise EvidenceValidationError(
            "locked_test_guard.forbidden_document_regexes must be a list"
        )
    for forbidden in forbidden_regexes:
        if not isinstance(forbidden, str):
            raise EvidenceValidationError(
                "locked-test forbidden regexes must be strings"
            )
        try:
            if re.search(forbidden, scrubbed, flags=re.MULTILINE | re.IGNORECASE):
                errors.append(
                    f"manuscript contains a forbidden locked-test result: {forbidden}"
                )
        except re.error as exc:
            raise EvidenceValidationError(
                f"invalid locked-test forbidden regex: {exc}"
            ) from exc
    return {
        "status": "failed" if errors else "passed",
        "accessed": (
            next(iter(access_values.values()))
            if len(access_values) == 1
            else access_values
        ),
        "registry_metric_splits": sorted({str(split) for split in registry_splits}),
        "errors": errors,
    }


def _artifact_records(
    root: Path,
    definitions: Any,
) -> tuple[dict[str, _Artifact], list[dict[str, Any]]]:
    if not isinstance(definitions, dict) or not definitions:
        raise EvidenceValidationError("artifacts must be a non-empty object")
    artifacts: dict[str, _Artifact] = {}
    reports: list[dict[str, Any]] = []
    for alias, definition in definitions.items():
        if not isinstance(alias, str) or not isinstance(definition, dict):
            raise EvidenceValidationError(
                "artifact entries must map string aliases to objects"
            )
        path, relative = _safe_relative_path(
            root, definition.get("path"), role=f"artifact {alias}"
        )
        expected = definition.get("sha256")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise EvidenceValidationError(
                f"artifact {alias}: sha256 must be lowercase SHA-256"
            )
        hash_mode = definition.get("hash_mode", "file")
        if hash_mode not in {"file", "tree"}:
            raise EvidenceValidationError(
                f"artifact {alias}: hash_mode must be file or tree"
            )
        actual: str | None = None
        error: str | None = None
        try:
            if not path.exists():
                raise OSError("artifact does not exist")
            actual = _sha256_tree(path) if hash_mode == "tree" else _sha256_file(path)
            if actual != expected:
                error = "artifact SHA-256 changed"
        except OSError as exc:
            error = str(exc)
        artifact = _Artifact(
            alias=alias,
            path=path,
            relative_path=relative,
            expected_sha256=expected,
            actual_sha256=actual,
            hash_mode=hash_mode,
            valid=error is None,
            error=error,
        )
        artifacts[alias] = artifact
        reports.append(
            {
                "alias": alias,
                "path": relative,
                "hash_mode": hash_mode,
                "expected_sha256": expected,
                "actual_sha256": actual,
                "status": "passed" if error is None else "failed",
                "error": error,
            }
        )
    return artifacts, reports


def validate_evidence_map(
    evidence_map_path: str | Path, *, workspace_root: str | Path
) -> dict[str, Any]:
    """Validate manuscript claims against immutable, privacy-safe aggregate artifacts."""
    root = Path(workspace_root).resolve()
    map_path = Path(evidence_map_path).resolve()
    try:
        map_relative = map_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise EvidenceValidationError("evidence map must be inside workspace") from exc
    map_parts = [part.lower() for part in Path(map_relative).parts]
    if any(part in _FORBIDDEN_PARTS or part.startswith(".env") for part in map_parts):
        raise EvidenceValidationError(
            "evidence map path is forbidden by privacy policy"
        )
    config = _load_map(map_path)
    _assert_finite(config, location="evidence-map")
    if config.get("schema_version") != 1:
        raise EvidenceValidationError("schema_version must be 1")
    manuscript_path, manuscript_relative = _safe_relative_path(
        root, config.get("manuscript"), role="manuscript"
    )
    try:
        manuscript = manuscript_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvidenceValidationError(f"cannot read manuscript: {exc}") from exc
    artifacts, artifact_reports = _artifact_records(root, config.get("artifacts"))
    json_cache: dict[str, Any] = {}
    claim_reports: list[dict[str, Any]] = []
    ids: set[str] = set()
    claims = config.get("claims")
    if not isinstance(claims, list) or not claims:
        raise EvidenceValidationError("claims must be a non-empty list")
    for claim in claims:
        if not isinstance(claim, dict):
            raise EvidenceValidationError("each claim must be an object")
        claim_id = claim.get("id")
        if not isinstance(claim_id, str) or not claim_id:
            raise EvidenceValidationError("each claim requires a non-empty id")
        if claim_id in ids:
            raise EvidenceValidationError(f"duplicate claim id: {claim_id}")
        ids.add(claim_id)
        try:
            claim_reports.append(
                _validate_claim(claim, manuscript, artifacts, json_cache)
            )
        except EvidenceValidationError as exc:
            claim_reports.append(
                {
                    "id": claim_id if isinstance(claim_id, str) else "<invalid>",
                    "status": "failed",
                    "error": str(exc),
                }
            )
    try:
        pending_report = _validate_pending_contract(
            config.get("pending_contract"), manuscript
        )
    except EvidenceValidationError as exc:
        pending_report = {"status": "failed", "errors": [str(exc)]}
    try:
        locked_report = _validate_locked_test_guard(
            config.get("locked_test_guard"),
            manuscript,
            artifacts,
            json_cache,
        )
    except EvidenceValidationError as exc:
        locked_report = {"status": "failed", "errors": [str(exc)]}
    sections: list[Iterable[Mapping[str, Any]]] = [artifact_reports, claim_reports]
    failed = any(
        item.get("status") != "passed" for section in sections for item in section
    )
    failed = failed or bool(pending_report and pending_report.get("status") != "passed")
    failed = failed or bool(locked_report and locked_report.get("status") != "passed")
    return {
        "schema_version": 1,
        "status": "failed" if failed else "passed",
        "privacy": {
            "raw_data_read": False,
            "environment_files_read": False,
            "aggregate_only": True,
        },
        "evidence_map": {
            "path": map_relative,
            "sha256": _sha256_file(map_path),
        },
        "manuscript": {
            "path": manuscript_relative,
            "sha256": _sha256_file(manuscript_path),
        },
        "summary": {
            "artifact_count": len(artifact_reports),
            "artifact_passed": sum(
                item["status"] == "passed" for item in artifact_reports
            ),
            "claim_count": len(claim_reports),
            "claim_passed": sum(item["status"] == "passed" for item in claim_reports),
        },
        "artifacts": artifact_reports,
        "claims": claim_reports,
        "pending_contract": pending_report,
        "locked_test_guard": locked_report,
    }


def write_json_atomic(path: str | Path, value: Mapping[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()
