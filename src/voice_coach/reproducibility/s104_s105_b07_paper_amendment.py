"""Fail-closed S104/S105/B07 manuscript-amendment preparation.

The package has two deliberately separate modes:

* ``validate_config_only`` validates a static, blocked manifest without resolving
  any experiment, manuscript, evidence-map, or registry path.
* ``prepare_paper_amendment`` first runs the three public deep validators and
  only then reads completed artifacts and canonical paper targets.  It publishes
  a content-addressed review bundle, never the canonical paper files.

The renderer projects only development evidence.  JSON ``null`` is represented
as the literal string ``"NA"`` in both the paper-safe JSON and Markdown.  It is
never coerced to zero or omitted.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import tempfile
from typing import Any, NoReturn, Protocol, cast

import yaml  # type: ignore[import-untyped]

from voice_coach.reproducibility.evidence import validate_evidence_map


MANIFEST_SCHEMA = "voice_coach.s104_s105_b07_paper_amendment_manifest.v1"
RESULT_SCHEMA = "voice_coach.s104_s105_b07_paper_results.v1"
RENDERER_RECEIPT_SCHEMA = "voice_coach.s104_s105_b07_paper_renderer_receipt.v1"
PROMOTION_RECEIPT_SCHEMA = "voice_coach.s104_s105_b07_paper_amendment_receipt.v1"
COMMIT_SCHEMA = "voice_coach.s104_s105_b07_paper_amendment_commit.v1"

CANONICAL_MANUSCRIPT = "docs/paper/EXPERIMENTAL_MANUSCRIPT_KO.md"
CANONICAL_EVIDENCE_MAP = "docs/paper/evidence-map.yaml"
EXPECTED_PENDING_COUNT = 18
CURRENT_PENDING_MARKERS_SHA256 = (
    "1d0cce59025c86879d0a9972f0da31e45e87169b41f5b8fadd427c111e6d7673"
)
OUTPUT_PREFIX = ("artifacts", "reproducibility")
DEFAULT_OUTPUT_ROOT = "artifacts/reproducibility/s104_s105_b07_paper_amendment"
EXPECTED_SEEDS = (42, 43, 44)
S105_VARIANTS = (
    "simple_head_control",
    "flat_shared_multitask",
    "final_layer_hierarchy",
    "layerwise_weighted_hierarchy",
    "levelwise_multi_aspect_hierarchy",
)
S105_INCREMENTAL_PAIRS = tuple(
    f"{control}__to__{candidate}"
    for control, candidate in zip(
        S105_VARIANTS[:-1],
        S105_VARIANTS[1:],
        strict=True,
    )
)
SECTIONS = (
    "methods",
    "analysis_status",
    "results",
    "discussion",
    "reproducibility",
    "appendix",
)
BEGIN_MARKERS = {
    section: f"<!-- BEGIN S104_S105_B07_{section.upper()} -->" for section in SECTIONS
}
END_MARKERS = {
    section: f"<!-- END S104_S105_B07_{section.upper()} -->" for section in SECTIONS
}
NUMBER_PATTERN = r"[+-]?\d+(?:\.\d+)?"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_TEXT_BYTES = 64 * 1024 * 1024
FORBIDDEN_SPLITS = frozenset({"stack", "calibration", "locked_test"})
FORBIDDEN_PATH_PARTS = frozenset(
    {".env", "raw", "restricted", "stack", "calibration", "locked_test", "locked-test"}
)

EXPECTED_INPUTS = (
    "s104_config",
    "s104_commit",
    "s104_report",
    "s104_registry",
    "s104_execution_lock",
    "s105_config",
    "s105_commit",
    "s105_comparison_report",
    "s105_ablation_table",
    "s105_execution_lock",
    *(f"s105_variant_{variant}_report" for variant in S105_VARIANTS),
    *(f"s105_variant_{variant}_registry" for variant in S105_VARIANTS),
    "b07_config",
    "b07_commit",
    "b07_aggregate_report",
    "b07_paired_control_report",
    "b07_registry",
    "b07_execution_lock",
    *(f"b07_seed_{seed}_metrics" for seed in EXPECTED_SEEDS),
    *(f"b07_seed_{seed}_selection_trace" for seed in EXPECTED_SEEDS),
)
EXPECTED_VALIDATORS = ("s104", "s105", "b07")
VALIDATOR_COUNTS = {
    "s104": {"artifact_count": 16},
    "s105": {"artifact_count": 82, "registry_count": 5},
    "b07": {"artifact_count": 21},
}
EVIDENCE_ALIASES = {
    "s104_commit": "wavlm_s104_commit",
    "s104_report": "wavlm_s104_report",
    "s104_registry": "wavlm_s104_registry",
    "s104_execution_lock": "wavlm_s104_execution_lock",
    "s105_commit": "wavlm_s105_commit",
    "s105_comparison_report": "wavlm_s105_comparison_report",
    "s105_ablation_table": "wavlm_s105_ablation_table",
    "s105_execution_lock": "wavlm_s105_execution_lock",
    "b07_commit": "b07_gop_commit",
    "b07_aggregate_report": "b07_gop_aggregate_report",
    "b07_paired_control_report": "b07_gop_paired_control_report",
    "b07_registry": "b07_gop_registry",
    "b07_execution_lock": "b07_gop_execution_lock",
}
for _variant in S105_VARIANTS:
    EVIDENCE_ALIASES[f"s105_variant_{_variant}_report"] = (
        f"wavlm_s105_variant_{_variant}_report"
    )
    EVIDENCE_ALIASES[f"s105_variant_{_variant}_registry"] = (
        f"wavlm_s105_registry_{_variant}"
    )
for _seed in EXPECTED_SEEDS:
    EVIDENCE_ALIASES[f"b07_seed_{_seed}_metrics"] = f"b07_gop_seed_{_seed}_metrics"
    EVIDENCE_ALIASES[f"b07_seed_{_seed}_selection_trace"] = (
        f"b07_gop_seed_{_seed}_selection_trace"
    )


class PaperAmendmentError(RuntimeError):
    """Raised when a paper amendment cannot be prepared safely."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ValidatorRunner(Protocol):
    """Read-only public deep-validator runner."""

    def __call__(
        self,
        stage: str,
        workspace_root: Path,
        script: Fingerprint,
        config: Fingerprint,
        timeout_seconds: int,
    ) -> Mapping[str, Any]: ...


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
            _fail("yaml_duplicate_key")
        output[str(key)] = loader.construct_object(value_node, deep=deep)
    return output


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True)
class Fingerprint:
    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class Targets:
    manuscript: Fingerprint
    evidence_map: Fingerprint


@dataclass(frozen=True)
class RenderedPaper:
    sections: Mapping[str, str]
    markdown: str
    payload: Mapping[str, Any]
    claim_specs: Mapping[str, Mapping[str, Any]]


def _fail(code: str) -> NoReturn:
    raise PaperAmendmentError(code)


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return cast(Mapping[str, Any], value)


def _list(value: object, code: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(code)
    return value


def _string(value: object, code: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(code)
    return value


def _integer(
    value: object,
    code: str,
    *,
    minimum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(code)
    if minimum is not None and value < minimum:
        _fail(code)
    return value


def _number_or_none(
    value: object,
    code: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(code)
    output = float(value)
    if not math.isfinite(output):
        _fail(code)
    if minimum is not None and output < minimum:
        _fail(code)
    if maximum is not None and output > maximum:
        _fail(code)
    return output


def _boolean(value: object, expected: bool, code: str) -> None:
    if value is not expected:
        _fail(code)


def _bool_value(value: object, code: str) -> bool:
    if not isinstance(value, bool):
        _fail(code)
    return value


def _reject_nonfinite(value: Any, *, code: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        _fail(code)
    if isinstance(value, Mapping):
        for child in value.values():
            _reject_nonfinite(child, code=code)
    elif isinstance(value, list):
        for child in value:
            _reject_nonfinite(child, code=code)


def _canonical_json_bytes(value: Mapping[str, Any], *, pretty: bool = False) -> bytes:
    if pretty:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
    else:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    return (text + "\n").encode("utf-8")


def _binding(value: Mapping[str, Any]) -> str:
    core = dict(value)
    core.pop("binding_sha256", None)
    return hashlib.sha256(_canonical_json_bytes(core)).hexdigest()


def _load_yaml(path: Path) -> Mapping[str, Any]:
    try:
        if path.stat().st_size > MAX_TEXT_BYTES:
            _fail("yaml_too_large")
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except PaperAmendmentError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError):
        _fail("yaml_read_failed")
    document = _mapping(value, "yaml_root")
    _reject_nonfinite(document, code="yaml_nonfinite")
    return document


def _load_json(path: Path, code: str) -> Mapping[str, Any]:
    def reject_constant(_: str) -> NoReturn:
        _fail(f"{code}_nonfinite")

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in pairs:
            if key in output:
                _fail(f"{code}_duplicate_key")
            output[key] = value
        return output

    try:
        if path.stat().st_size > MAX_TEXT_BYTES:
            _fail(f"{code}_too_large")
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(
                handle,
                object_pairs_hook=unique_pairs,
                parse_constant=reject_constant,
            )
    except PaperAmendmentError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail(f"{code}_read_failed")
    output = _mapping(value, f"{code}_root")
    _reject_nonfinite(output, code=f"{code}_nonfinite")
    return output


def _path_parts_are_safe(path: Path) -> bool:
    for part in path.parts:
        lowered = part.casefold()
        if lowered.startswith(".env") or lowered in FORBIDDEN_PATH_PARTS:
            return False
    return True


def _workspace_path(
    root: Path,
    configured: str | Path,
    *,
    must_exist: bool,
    code: str,
) -> Path:
    lexical = Path(configured)
    if lexical.is_absolute():
        candidate = lexical
        try:
            relative = candidate.resolve(strict=False).relative_to(root)
        except (OSError, RuntimeError, ValueError):
            _fail(f"{code}_path")
    else:
        candidate = root / lexical
        relative = lexical
    if not _path_parts_are_safe(relative):
        _fail(f"{code}_path")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            _fail(f"{code}_symlink")
    try:
        resolved = candidate.resolve(strict=must_exist)
        resolved.relative_to(root)
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        _fail(f"{code}_path")
    if not _path_parts_are_safe(resolved.relative_to(root)):
        _fail(f"{code}_path")
    return resolved


def _fingerprint(path: Path, *, code: str) -> Fingerprint:
    if path.is_symlink() or not path.is_file():
        _fail(f"{code}_not_regular_file")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        size = path.stat().st_size
    except OSError:
        _fail(f"{code}_fingerprint")
    return Fingerprint(path=path, sha256=digest.hexdigest(), size_bytes=size)


def _record_syntax(
    record: object,
    *,
    code: str,
    allow_placeholder: bool,
) -> Mapping[str, Any]:
    item = _mapping(record, f"{code}_record")
    if set(item) != {"path", "sha256", "size_bytes"}:
        _fail(f"{code}_record_keys")
    path = _string(item.get("path"), f"{code}_path")
    sha = _string(item.get("sha256"), f"{code}_sha256")
    size = _integer(item.get("size_bytes"), f"{code}_size", minimum=0)
    if allow_placeholder:
        if sha != "TBD" and SHA256_RE.fullmatch(sha) is None:
            _fail(f"{code}_sha256")
        if path != "TBD" and (
            Path(path).is_absolute() or not _path_parts_are_safe(Path(path))
        ):
            _fail(f"{code}_path")
    elif SHA256_RE.fullmatch(sha) is None or path == "TBD" or size <= 0:
        _fail(f"{code}_not_ready")
    return item


def _verify_record(
    root: Path,
    record: object,
    *,
    code: str,
) -> Fingerprint:
    item = _record_syntax(record, code=code, allow_placeholder=False)
    path = _workspace_path(
        root,
        _string(item.get("path"), f"{code}_path"),
        must_exist=True,
        code=code,
    )
    actual = _fingerprint(path, code=code)
    if actual.sha256 != item.get("sha256") or actual.size_bytes != item.get(
        "size_bytes"
    ):
        _fail(f"{code}_fingerprint_mismatch")
    return actual


def _record(root: Path, value: Fingerprint) -> dict[str, Any]:
    return {
        "path": value.path.relative_to(root).as_posix(),
        "sha256": value.sha256,
        "size_bytes": value.size_bytes,
    }


def _record_bytes(root: Path, path: Path, value: bytes) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(value).hexdigest(),
        "size_bytes": len(value),
    }


def _validate_manifest_shape(
    manifest: Mapping[str, Any],
    *,
    allow_placeholder: bool,
) -> None:
    expected_keys = {
        "schema_version",
        "status",
        "actual_state",
        "contract",
        "validators",
        "inputs",
        "targets",
        "edits",
    }
    if set(manifest) != expected_keys:
        _fail("manifest_keys")
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        _fail("manifest_schema")
    status = manifest.get("status")
    if status not in {"config_only", "ready"}:
        _fail("manifest_status")
    if not allow_placeholder and status != "ready":
        _fail("manifest_not_ready")

    actual = _mapping(manifest.get("actual_state"), "actual_state")
    if set(actual) != {
        "metric_values_read",
        "artifact_or_registry_files_read",
        "canonical_files_read",
        "outputs_written",
    } or any(value != 0 for value in actual.values()):
        _fail("actual_state_not_zero")

    contract = _mapping(manifest.get("contract"), "contract")
    if set(contract) != {
        "pending_marker_count",
        "pending_markers_sha256",
        "null_input",
        "null_manuscript_token",
        "candidate_only",
        "canonical_write_allowed",
        "validator_timeout_seconds",
    }:
        _fail("contract_keys")
    if (
        contract.get("pending_marker_count") != EXPECTED_PENDING_COUNT
        or _string(
            contract.get("pending_markers_sha256"),
            "pending_markers_sha256",
        )
        != CURRENT_PENDING_MARKERS_SHA256
        or contract.get("null_input") is not None
        or contract.get("null_manuscript_token") != "NA"
        or contract.get("candidate_only") is not True
        or contract.get("canonical_write_allowed") is not False
        or not 60
        <= _integer(
            contract.get("validator_timeout_seconds"),
            "validator_timeout",
        )
        <= 86_400
    ):
        _fail("contract_value")

    validators = _mapping(manifest.get("validators"), "validators")
    if set(validators) != set(EXPECTED_VALIDATORS):
        _fail("validator_set")
    for stage in EXPECTED_VALIDATORS:
        _record_syntax(
            validators.get(stage),
            code=f"validator_{stage}",
            allow_placeholder=allow_placeholder,
        )

    inputs = _mapping(manifest.get("inputs"), "inputs")
    if set(inputs) != set(EXPECTED_INPUTS):
        _fail("input_set")
    for name in EXPECTED_INPUTS:
        _record_syntax(
            inputs.get(name),
            code=f"input_{name}",
            allow_placeholder=allow_placeholder,
        )

    targets = _mapping(manifest.get("targets"), "targets")
    if set(targets) != {"manuscript", "evidence_map"}:
        _fail("target_set")
    for name in ("manuscript", "evidence_map"):
        _record_syntax(
            targets.get(name),
            code=f"target_{name}",
            allow_placeholder=allow_placeholder,
        )

    edits = _mapping(manifest.get("edits"), "edits")
    if set(edits) != set(SECTIONS):
        _fail("edit_set")
    for section in SECTIONS:
        edit = _mapping(edits.get(section), f"edit_{section}")
        if set(edit) != {"operation", "anchor", "expected_matches"}:
            _fail(f"edit_{section}_keys")
        if edit.get("operation") not in {
            "insert_before",
            "insert_after",
            "replace_exact",
        }:
            _fail(f"edit_{section}_operation")
        anchor = _string(edit.get("anchor"), f"edit_{section}_anchor")
        if not allow_placeholder and anchor.startswith("TBD_"):
            _fail(f"edit_{section}_not_ready")
        if edit.get("expected_matches") != 1:
            _fail(f"edit_{section}_matches")


def validate_config_only(manifest_path: str | Path) -> Mapping[str, Any]:
    """Validate a blocked template without resolving any configured path."""

    path = Path(manifest_path)
    manifest = _load_yaml(path)
    _validate_manifest_shape(manifest, allow_placeholder=True)
    if manifest.get("status") != "config_only":
        _fail("config_only_status")
    contract = _mapping(manifest.get("contract"), "contract")
    if contract.get("pending_markers_sha256") != CURRENT_PENDING_MARKERS_SHA256:
        _fail("config_only_current_pending_hash")
    return {
        "status": "valid",
        "mode": "config_only",
        "actual_metric_values_read": 0,
        "actual_artifact_or_registry_files_read": 0,
        "actual_canonical_files_read": 0,
        "actual_outputs_written": 0,
        "pending_marker_count": EXPECTED_PENDING_COUNT,
        "pending_markers_sha256": CURRENT_PENDING_MARKERS_SHA256,
    }


def _default_validator_runner(
    stage: str,
    workspace_root: Path,
    script: Fingerprint,
    config: Fingerprint,
    timeout_seconds: int,
) -> Mapping[str, Any]:
    command = [
        sys.executable,
        str(script.path),
        "--config",
        config.path.relative_to(workspace_root).as_posix(),
        "--workspace-root",
        str(workspace_root),
        "--validate-commit-only",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=workspace_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError):
        _fail(f"validator_{stage}_execution")
    if completed.returncode != 0:
        _fail(f"validator_{stage}_failed")
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if len(lines) != 1 or completed.stderr.strip():
        _fail(f"validator_{stage}_output_contract")
    try:
        value = json.loads(lines[0])
    except json.JSONDecodeError:
        _fail(f"validator_{stage}_output_json")
    return _mapping(value, f"validator_{stage}_output")


def _validate_deep_result(
    stage: str,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if value.get("status") != "valid":
        _fail(f"validator_{stage}_status")
    output: dict[str, Any] = {"status": "valid"}
    for key, expected in VALIDATOR_COUNTS[stage].items():
        if value.get(key) != expected:
            _fail(f"validator_{stage}_{key}")
        output[key] = expected
    for optional in ("generation_id", "commit_sha256"):
        candidate = value.get(optional)
        if candidate is not None:
            if not isinstance(candidate, str) or SHA256_RE.fullmatch(candidate) is None:
                _fail(f"validator_{stage}_{optional}")
            output[optional] = candidate
    return output


def _json_pointer(value: Any, pointer: str, code: str) -> Any:
    current = value
    if pointer == "":
        return current
    if not pointer.startswith("/"):
        _fail(code)
    for raw in pointer.split("/")[1:]:
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if part not in current:
                _fail(code)
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                _fail(code)
        else:
            _fail(code)
    return current


def _tree_contains_string(value: Any, expected: str) -> bool:
    if value == expected:
        return True
    if isinstance(value, Mapping):
        return any(_tree_contains_string(child, expected) for child in value.values())
    if isinstance(value, list):
        return any(_tree_contains_string(child, expected) for child in value)
    return False


def _iter_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _iter_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_mappings(child)


def _commit_binds(
    root: Path,
    commit_path: Path,
    commit: Mapping[str, Any],
    artifact: Fingerprint,
) -> bool:
    bases = [root, commit_path.parent]
    for item in _iter_mappings(commit):
        for key in ("generation_path", "generation_root"):
            configured_base = item.get(key)
            if (
                not isinstance(configured_base, str)
                or Path(configured_base).is_absolute()
            ):
                continue
            for parent in (root, commit_path.parent):
                try:
                    candidate_base = (parent / configured_base).resolve(strict=False)
                    candidate_base.relative_to(root)
                except (OSError, RuntimeError, ValueError):
                    continue
                if candidate_base not in bases:
                    bases.append(candidate_base)
    for item in _iter_mappings(commit):
        if item.get("sha256") != artifact.sha256:
            continue
        size = item.get("size_bytes")
        if size is not None and size != artifact.size_bytes:
            continue
        configured = item.get("path", item.get("relative_path"))
        if not isinstance(configured, str) or Path(configured).is_absolute():
            continue
        candidates = tuple(base / configured for base in bases)
        for candidate in candidates:
            try:
                if candidate.resolve(strict=False) == artifact.path:
                    return True
            except (OSError, RuntimeError):
                continue
    return False


def _assert_registries_safe(
    registries: Mapping[str, Mapping[str, Any]],
) -> None:
    for name, registry in registries.items():
        metrics = registry.get("metrics")
        if not isinstance(metrics, list):
            _fail(f"{name}_metrics")
        for metric in metrics:
            item = _mapping(metric, f"{name}_metric")
            split = item.get("split")
            if not isinstance(split, str):
                _fail(f"{name}_metric_split")
            if split.casefold() in FORBIDDEN_SPLITS:
                _fail(f"{name}_forbidden_split")


def _reject_positive_access(value: Any, *, code: str) -> set[str]:
    found: set[str] = set()

    def walk(current: Any, path: tuple[str, ...]) -> None:
        if isinstance(current, Mapping):
            for key, child in current.items():
                lowered = str(key).casefold()
                next_path = (*path, lowered)
                for token in ("stack", "calibration", "locked"):
                    if token in lowered or any(token in part for part in path):
                        if isinstance(child, bool):
                            found.add(token)
                            if child:
                                _fail(f"{code}_{token}_access")
                        elif isinstance(child, (int, float)) and not isinstance(
                            child,
                            bool,
                        ):
                            found.add(token)
                            if float(child) != 0.0:
                                _fail(f"{code}_{token}_access")
                walk(child, next_path)
        elif isinstance(current, list):
            for child in current:
                walk(child, path)

    walk(value, ())
    return found


def _assert_aggregate(
    values: Sequence[float | None],
    reported: Mapping[str, Any],
    *,
    code: str,
) -> tuple[float | None, float | None]:
    mean = _number_or_none(reported.get("mean"), f"{code}_mean")
    sample_std = _number_or_none(
        reported.get("sample_std"),
        f"{code}_sample_std",
        minimum=0.0,
    )
    numeric = [cast(float, value) for value in values if value is not None]
    expected_mean = statistics.mean(numeric) if numeric else None
    expected_std = statistics.stdev(numeric) if len(numeric) > 1 else None

    def same(actual: float | None, expected: float | None) -> bool:
        if actual is None or expected is None:
            return actual is expected
        return math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-12)

    if not same(mean, expected_mean) or not same(sample_std, expected_std):
        _fail(f"{code}_aggregate_mismatch")
    return mean, sample_std


def _paper_value(value: Any, *, code: str) -> Any:
    if value is None:
        return "NA"
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail(f"{code}_nonfinite")
        return value
    if isinstance(value, str) and value:
        return value
    _fail(f"{code}_paper_value")


class _MarkdownClaims:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.specs: dict[str, dict[str, Any]] = {}

    def cell(self, claim_id: str, value: Any, *, decimals: int = 6) -> str:
        if claim_id in self.values or re.fullmatch(r"[a-z0-9_]+", claim_id) is None:
            _fail("renderer_claim_id")
        projected = _paper_value(value, code=claim_id)
        if isinstance(projected, float):
            display = f"{projected:.{decimals}f}"
            comparison = "numeric"
        elif isinstance(projected, int) and not isinstance(projected, bool):
            display = str(projected)
            comparison = "numeric"
            decimals = 0
        else:
            display = str(projected)
            comparison = "exact"
        self.values[claim_id] = projected
        self.specs[claim_id] = {
            "comparison": comparison,
            "decimals": decimals,
            "regex": (
                rf"(?P<value>{re.escape(display)})"
                rf"\s*<!-- evidence:{re.escape(claim_id)} -->"
            ),
        }
        return f"{display} <!-- evidence:{claim_id} -->"


def _s104_projection(report: Mapping[str, Any]) -> dict[str, Any]:
    if (
        report.get("status") != "completed"
        or report.get("development_only") is not True
        or report.get("oracle_alignment") is not True
        or report.get("silver_label") is not True
        or report.get("promotion_decision_allowed") is not False
    ):
        _fail("s104_scope")
    leakage = _mapping(report.get("leakage"), "s104_leakage")
    for name in ("stack_accessed", "calibration_accessed", "locked_test_accessed"):
        _boolean(leakage.get(name), False, f"s104_{name}")

    runs = _list(report.get("runs"), "s104_runs")
    if len(runs) != len(EXPECTED_SEEDS):
        _fail("s104_run_count")
    by_seed: dict[int, dict[str, Any]] = {}
    for raw in runs:
        run = _mapping(raw, "s104_run")
        seed = _integer(run.get("seed"), "s104_seed")
        if seed not in EXPECTED_SEEDS or seed in by_seed:
            _fail("s104_seed_set")
        phone = _mapping(
            _json_pointer(run, "/dev/metrics/phone", "s104_phone"),
            "s104_phone",
        )
        outcome = _mapping(
            _json_pointer(
                run,
                "/dev/metrics/utterance/outcomes/comprehensibility_score",
                "s104_comprehensibility",
            ),
            "s104_comprehensibility",
        )
        by_seed[seed] = {
            "phone_macro_f1": _number_or_none(
                phone.get("macro_f1"),
                "s104_phone_macro_f1",
                minimum=0.0,
                maximum=1.0,
            ),
            "phone_auprc": _number_or_none(
                phone.get("auprc"),
                "s104_phone_auprc",
                minimum=0.0,
                maximum=1.0,
            ),
            "comprehensibility_icc_2_1": _number_or_none(
                outcome.get("icc_2_1"),
                "s104_icc",
                minimum=-1.0,
                maximum=1.0,
            ),
        }
    if set(by_seed) != set(EXPECTED_SEEDS):
        _fail("s104_seed_set")
    phone_values = [by_seed[seed]["phone_macro_f1"] for seed in EXPECTED_SEEDS]
    icc_values = [by_seed[seed]["comprehensibility_icc_2_1"] for seed in EXPECTED_SEEDS]
    summary = _mapping(report.get("summary"), "s104_summary")
    phone_summary = _mapping(
        summary.get("phone_macro_f1"),
        "s104_phone_summary",
    )
    icc_summary = _mapping(
        summary.get("comprehensibility_icc_2_1"),
        "s104_icc_summary",
    )
    phone_mean, phone_std = _assert_aggregate(
        phone_values,
        phone_summary,
        code="s104_phone",
    )
    icc_mean, icc_std = _assert_aggregate(
        icc_values,
        icc_summary,
        code="s104_icc",
    )
    auprc_values = [by_seed[seed]["phone_auprc"] for seed in EXPECTED_SEEDS]
    numeric_auprc = [cast(float, value) for value in auprc_values if value is not None]
    auprc_mean = statistics.mean(numeric_auprc) if numeric_auprc else None
    auprc_std = statistics.stdev(numeric_auprc) if len(numeric_auprc) > 1 else None
    engineering = _mapping(
        summary.get("engineering_targets"),
        "s104_engineering",
    )
    return {
        "seeds": {str(seed): by_seed[seed] for seed in EXPECTED_SEEDS},
        "aggregate": {
            "phone_macro_f1": {"mean": phone_mean, "sample_std": phone_std},
            "phone_auprc": {"mean": auprc_mean, "sample_std": auprc_std},
            "comprehensibility_icc_2_1": {
                "mean": icc_mean,
                "sample_std": icc_std,
            },
        },
        "engineering_targets": {
            "mean_phone_passed": _bool_value(
                engineering.get("mean_phone_passed"),
                "s104_engineering_phone",
            ),
            "mean_primary_utterance_passed": _bool_value(
                engineering.get("mean_primary_utterance_passed"),
                "s104_engineering_utterance",
            ),
        },
    }


def _s105_projection(
    comparison: Mapping[str, Any],
    variant_reports: Mapping[str, Mapping[str, Any]],
    committed_ablation_table: str,
) -> dict[str, Any]:
    if (
        comparison.get("status") != "completed"
        or comparison.get("development_only") is not True
        or comparison.get("oracle_alignment") is not True
        or comparison.get("silver_label") is not True
        or comparison.get("promotion_decision_allowed") is not False
    ):
        _fail("s105_scope")
    leakage = _mapping(comparison.get("leakage"), "s105_leakage")
    for name in ("stack_accessed", "calibration_accessed", "locked_test_accessed"):
        _boolean(leakage.get(name), False, f"s105_{name}")

    variants: dict[str, Any] = {}
    for variant in S105_VARIANTS:
        report = variant_reports[variant]
        if report.get("status") != "completed":
            _fail(f"s105_{variant}_status")
        metrics = _mapping(
            report.get("metrics_by_seed"),
            f"s105_{variant}_metrics_by_seed",
        )
        if set(metrics) != {str(seed) for seed in EXPECTED_SEEDS}:
            _fail(f"s105_{variant}_seed_set")
        seed_values: dict[str, Any] = {}
        phone_values: list[float | None] = []
        icc_values: list[float | None] = []
        for seed in EXPECTED_SEEDS:
            item = _mapping(metrics.get(str(seed)), f"s105_{variant}_{seed}")
            phone = _number_or_none(
                _json_pointer(
                    item,
                    "/phone/macro_f1",
                    f"s105_{variant}_{seed}_phone",
                ),
                f"s105_{variant}_{seed}_phone",
                minimum=0.0,
                maximum=1.0,
            )
            icc = _number_or_none(
                _json_pointer(
                    item,
                    "/utterance/outcomes/comprehensibility_score/icc_2_1",
                    f"s105_{variant}_{seed}_icc",
                ),
                f"s105_{variant}_{seed}_icc",
                minimum=-1.0,
                maximum=1.0,
            )
            phone_values.append(phone)
            icc_values.append(icc)
            seed_values[str(seed)] = {
                "phone_macro_f1": phone,
                "comprehensibility_icc_2_1": icc,
            }
        aggregate = _mapping(
            report.get("aggregate"),
            f"s105_{variant}_aggregate",
        )
        phone_mean, phone_std = _assert_aggregate(
            phone_values,
            _mapping(
                aggregate.get("phone_macro_f1"),
                f"s105_{variant}_phone_aggregate",
            ),
            code=f"s105_{variant}_phone",
        )
        icc_mean, icc_std = _assert_aggregate(
            icc_values,
            _mapping(
                aggregate.get("comprehensibility_score_icc_2_1"),
                f"s105_{variant}_icc_aggregate",
            ),
            code=f"s105_{variant}_icc",
        )
        variants[variant] = {
            "seeds": seed_values,
            "aggregate": {
                "phone_macro_f1": {
                    "mean": phone_mean,
                    "sample_std": phone_std,
                },
                "comprehensibility_icc_2_1": {
                    "mean": icc_mean,
                    "sample_std": icc_std,
                },
            },
        }

    gate = _mapping(comparison.get("adoption_gate"), "s105_adoption_gate")
    status = _string(gate.get("status"), "s105_gate_status")
    if status not in {"passed", "failed"}:
        _fail("s105_gate_status")
    expected_table_lines = [
        "# S105 development-only hierarchy ablation",
        "",
        "| Variant | Phone macro-F1 mean | Comprehensibility ICC(2,1) mean |",
        "|---|---:|---:|",
    ]
    for variant in S105_VARIANTS:
        aggregate = variants[variant]["aggregate"]

        def fmt(value: object) -> str:
            return "NA" if value is None else f"{float(cast(float, value)):.6f}"

        expected_table_lines.append(
            f"| {variant} | "
            f"{fmt(aggregate['phone_macro_f1']['mean'])} | "
            f"{fmt(aggregate['comprehensibility_icc_2_1']['mean'])} |"
        )
    expected_table_lines.extend(
        [
            "",
            f"Adoption gate: **{status}**.",
            "",
            (
                "Development-only, oracle-alignment, silver-label evidence; "
                "not a promotion decision."
            ),
            "",
        ]
    )
    expected_table = "\n".join(expected_table_lines)
    if committed_ablation_table != expected_table:
        _fail("s105_committed_ablation_table_mismatch")

    primary = _mapping(comparison.get("primary_family"), "s105_primary")
    expected_outcomes = {
        "phone_macro_f1",
        "comprehensibility_score_icc_2_1",
    }
    if set(primary) != expected_outcomes:
        _fail("s105_primary_set")

    def effect(item: Mapping[str, Any], code: str) -> dict[str, Any]:
        bootstrap = _mapping(
            item.get("paired_speaker_bootstrap"),
            f"{code}_bootstrap",
        )
        return {
            "estimate": _number_or_none(
                bootstrap.get("estimate"),
                f"{code}_estimate",
            ),
            "confidence_lower": _number_or_none(
                bootstrap.get("confidence_lower"),
                f"{code}_lower",
            ),
            "confidence_upper": _number_or_none(
                bootstrap.get("confidence_upper"),
                f"{code}_upper",
            ),
            "p_value_two_sided_zero": _number_or_none(
                bootstrap.get("p_value_two_sided_zero"),
                f"{code}_p",
                minimum=0.0,
                maximum=1.0,
            ),
            "holm_adjusted_p_value": _number_or_none(
                item.get("holm_adjusted_p_value"),
                f"{code}_holm_p",
                minimum=0.0,
                maximum=1.0,
            ),
            "holm_rejected": item.get("holm_rejected"),
        }

    primary_projection = {
        outcome: effect(
            _mapping(primary[outcome], f"s105_primary_{outcome}"),
            f"s105_primary_{outcome}",
        )
        for outcome in sorted(expected_outcomes)
    }
    incremental_raw = _mapping(
        comparison.get("incremental_comparisons"),
        "s105_incremental",
    )
    if set(incremental_raw) != set(S105_INCREMENTAL_PAIRS):
        _fail("s105_incremental_pair_set")
    incremental: dict[str, Any] = {}
    item_count = 0
    for pair in sorted(incremental_raw):
        outcomes = _mapping(incremental_raw[pair], f"s105_incremental_{pair}")
        if set(outcomes) != expected_outcomes:
            _fail("s105_incremental_outcome_set")
        incremental[pair] = {
            outcome: effect(
                _mapping(outcomes[outcome], f"s105_incremental_{pair}_{outcome}"),
                f"s105_incremental_{pair}_{outcome}",
            )
            for outcome in sorted(expected_outcomes)
        }
        item_count += len(outcomes)
    if item_count != 8:
        _fail("s105_incremental_family_size")

    subgroup = _mapping(comparison.get("subgroup"), "s105_subgroup")
    return {
        "variants": variants,
        "primary_family": primary_projection,
        "incremental_comparisons": incremental,
        "subgroup": {
            "eligible_group_count": _integer(
                subgroup.get("eligible_group_count"),
                "s105_subgroup_count",
                minimum=0,
            ),
            "worst_group_drop": _number_or_none(
                subgroup.get("worst_group_drop"),
                "s105_worst_group_drop",
            ),
            "maximum_exclusive": _number_or_none(
                subgroup.get("maximum_exclusive"),
                "s105_subgroup_threshold",
            ),
            "passed": _bool_value(
                subgroup.get("passed"),
                "s105_subgroup_passed",
            ),
        },
        "adoption_gate": {
            "status": status,
            "adopt_candidate": _bool_value(
                gate.get("adopt_candidate"),
                "s105_adopt_candidate",
            ),
            "point_improvement_passed": _bool_value(
                gate.get("point_improvement_passed"),
                "s105_point_improvement",
            ),
            "worst_group_drop_passed": _bool_value(
                gate.get("worst_group_drop_passed"),
                "s105_worst_group_drop_passed",
            ),
        },
        "committed_ablation_table_reused": True,
    }


def _b07_projection(
    aggregate: Mapping[str, Any],
    paired: Mapping[str, Any],
    seed_metrics: Mapping[int, Mapping[str, Any]],
    selections: Mapping[int, Mapping[str, Any]],
) -> dict[str, Any]:
    if (
        aggregate.get("status") != "completed"
        or aggregate.get("oracle_alignment") is not True
        or aggregate.get("deployable") is not False
        or aggregate.get("canonical_b03_metric_claimed") is not False
    ):
        _fail("b07_scope")
    found: set[str] = set()
    found.update(_reject_positive_access(aggregate, code="b07_aggregate"))
    for seed in EXPECTED_SEEDS:
        found.update(
            _reject_positive_access(
                seed_metrics[seed],
                code=f"b07_seed_{seed}",
            )
        )
    if not {"stack", "calibration", "locked"}.issubset(found):
        _fail("b07_access_audit_missing")

    seeds: dict[str, Any] = {}
    phone_values: list[float] = []
    auprc_values: list[float] = []
    for seed in EXPECTED_SEEDS:
        metric = _mapping(
            seed_metrics[seed].get("gop_acoustic_posterior"),
            f"b07_seed_{seed}_gop",
        )
        phone = _number_or_none(
            metric.get("phone_macro_f1"),
            f"b07_seed_{seed}_phone",
            minimum=0.0,
            maximum=1.0,
        )
        auprc = _number_or_none(
            metric.get("error_auprc"),
            f"b07_seed_{seed}_auprc",
            minimum=0.0,
            maximum=1.0,
        )
        threshold = _number_or_none(
            metric.get("threshold"),
            f"b07_seed_{seed}_threshold",
        )
        if phone is None or auprc is None:
            _fail("b07_primary_metric_null")
        phone_values.append(phone)
        auprc_values.append(auprc)
        selection = selections[seed]
        selected = _mapping(
            selection.get("gop_selected"),
            f"b07_seed_{seed}_selection",
        )
        seeds[str(seed)] = {
            "phone_macro_f1": phone,
            "error_auprc": auprc,
            "threshold": threshold,
            "selected_temperature": _number_or_none(
                selection.get("selected_final_temperature"),
                f"b07_seed_{seed}_temperature",
            ),
            "regularization_c": _number_or_none(
                selected.get("regularization_c"),
                f"b07_seed_{seed}_c",
                minimum=0.0,
            ),
            "selected_threshold": _number_or_none(
                selected.get("threshold"),
                f"b07_seed_{seed}_selected_threshold",
            ),
        }

    gop = _mapping(
        aggregate.get("gop_acoustic_posterior"),
        "b07_gop_aggregate",
    )
    expected = {
        "mean_seed_phone_macro_f1": statistics.mean(phone_values),
        "sample_std_seed_phone_macro_f1": statistics.stdev(phone_values),
        "mean_seed_error_auprc": statistics.mean(auprc_values),
        "sample_std_seed_error_auprc": statistics.stdev(auprc_values),
    }
    for key, expected_value in expected.items():
        actual = _number_or_none(gop.get(key), f"b07_{key}")
        if actual is None or not math.isclose(
            actual,
            expected_value,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            _fail(f"b07_{key}_mismatch")
    if gop.get("seed_summary_ddof") != 1:
        _fail("b07_seed_summary_ddof")

    rows = _list(paired.get("seed_rows"), "b07_paired_rows")
    if len(rows) != 3:
        _fail("b07_paired_row_count")
    paired_by_seed: dict[str, Any] = {}
    for raw in rows:
        row = _mapping(raw, "b07_paired_row")
        seed = _integer(row.get("seed"), "b07_paired_seed")
        if seed not in EXPECTED_SEEDS or str(seed) in paired_by_seed:
            _fail("b07_paired_seed_set")
        paired_by_seed[str(seed)] = {
            "gop_phone_macro_f1": _number_or_none(
                row.get("gop_phone_macro_f1"),
                "b07_paired_gop",
            ),
            "explicit_refit_phone_macro_f1": _number_or_none(
                row.get("explicit_refit_phone_macro_f1"),
                "b07_paired_explicit",
            ),
            "phone_macro_f1_delta": _number_or_none(
                row.get("phone_macro_f1_delta"),
                "b07_paired_delta",
            ),
        }
    if set(paired_by_seed) != {str(seed) for seed in EXPECTED_SEEDS}:
        _fail("b07_paired_seed_set")
    _boolean(
        paired.get("canonical_b03_checkpoint_or_prediction_read"),
        False,
        "b07_canonical_b03_read",
    )
    gate = _mapping(aggregate.get("adoption_gate"), "b07_adoption_gate")
    return {
        "seeds": seeds,
        "aggregate": {
            key: _number_or_none(gop.get(key), f"b07_{key}") for key in expected
        },
        "paired_explicit_refit_control": {
            "seeds": paired_by_seed,
            "mean_seed_gop_phone_macro_f1": _number_or_none(
                paired.get("mean_seed_gop_phone_macro_f1"),
                "b07_paired_gop_mean",
            ),
            "mean_seed_explicit_refit_phone_macro_f1": _number_or_none(
                paired.get("mean_seed_explicit_refit_phone_macro_f1"),
                "b07_paired_explicit_mean",
            ),
            "mean_seed_phone_macro_f1_delta": _number_or_none(
                paired.get("mean_seed_phone_macro_f1_delta"),
                "b07_paired_delta_mean",
            ),
            "sample_std_seed_phone_macro_f1_delta": _number_or_none(
                paired.get("sample_std_seed_phone_macro_f1_delta"),
                "b07_paired_delta_std",
                minimum=0.0,
            ),
        },
        "adoption_gate": {
            "value": _number_or_none(gate.get("value"), "b07_gate_value"),
            "passed": _bool_value(gate.get("passed"), "b07_gate_passed"),
        },
        "canonical_b03_checkpoint_or_prediction_read": False,
    }


def _table_row(cells: Sequence[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _wrap_section(section: str, body: str) -> str:
    if section not in SECTIONS or not body.endswith("\n"):
        _fail("renderer_section_contract")
    return f"{BEGIN_MARKERS[section]}\n{body}{END_MARKERS[section]}\n"


def _render_paper(
    *,
    root: Path,
    inputs: Mapping[str, Fingerprint],
    deep_validation: Mapping[str, Mapping[str, Any]],
    registry_run_ids: Mapping[str, str],
    s104: Mapping[str, Any],
    s105: Mapping[str, Any],
    b07: Mapping[str, Any],
) -> RenderedPaper:
    claims = _MarkdownClaims()

    methods = _wrap_section(
        "methods",
        (
            "### 완료 단계의 개발 전용 방법 계약\n\n"
            "B07은 realized identity와 binary error label을 특징으로 사용하지 "
            "않는 match-only acoustic posterior와 speaker-disjoint nested "
            "selection을 사용했다. 각 seed에서 train 내부 선택을 끝낸 뒤 dev를 "
            "한 번만 평가했고, 동일 실행의 explicit-feature refit을 통제로 "
            "고정했다.\n\n"
            "S104는 frozen Base+ 선행계보 위에서 residual adapter를 온라인 raw "
            "audio 경로로 학습했고, 후보 선택은 train 내부에서만 수행했다. "
            "S105는 같은 seed의 검증된 S104 출력을 동결하고 누적 hierarchy "
            "variant를 비교했다. word 표현은 unlabeled intermediate "
            "representation일 뿐 word loss나 word metric의 근거가 아니다. "
            "모든 결과는 development-only·oracle-alignment·silver-label이며 "
            "promotion 결정에 사용할 수 없다.\n"
        ),
    )

    analysis_status = _wrap_section(
        "analysis_status",
        (
            "| 단계 | 상태 | 허용 범위 | 금지 범위 |\n"
            "|---|---|---|---|\n"
            "| B07 | completed commit + deep validation | dev-only GOP control | "
            "canonical B03 대체·promotion·locked test |\n"
            "| S104 | completed commit + deep validation | dev-only adapter | "
            "promotion·stack·calibration·locked test |\n"
            "| S105 | completed commit + deep validation | dev-only hierarchy "
            "ablation | word metric·promotion·stack·calibration·locked test |\n"
        ),
    )

    s104_lines = [
        "<!-- BEGIN WAVLM_S104_ADAPTER_RESULTS_TABLE -->",
        "### S104 adapter 개발 결과",
        "",
        "| Seed | Phone macro-F1 | Phone AUPRC | Comprehensibility ICC(2,1) |",
        "|---:|---:|---:|---:|",
    ]
    s104_seeds = _mapping(s104.get("seeds"), "render_s104_seeds")
    for seed in EXPECTED_SEEDS:
        item = _mapping(s104_seeds.get(str(seed)), f"render_s104_{seed}")
        s104_lines.append(
            _table_row(
                (
                    claims.cell(
                        f"wavlm_s104_seed_{seed}_seed",
                        seed,
                        decimals=0,
                    ),
                    claims.cell(
                        f"wavlm_s104_seed_{seed}_phone_macro_f1",
                        item.get("phone_macro_f1"),
                    ),
                    claims.cell(
                        f"wavlm_s104_seed_{seed}_phone_auprc",
                        item.get("phone_auprc"),
                    ),
                    claims.cell(
                        f"wavlm_s104_seed_{seed}_comprehensibility_icc_2_1",
                        item.get("comprehensibility_icc_2_1"),
                    ),
                )
            )
        )
    s104_aggregate = _mapping(
        s104.get("aggregate"),
        "render_s104_aggregate",
    )
    aggregate_metrics = (
        ("phone_macro_f1", "Phone macro-F1"),
        ("phone_auprc", "Phone AUPRC"),
        ("comprehensibility_icc_2_1", "Comprehensibility ICC(2,1)"),
    )
    s104_lines.extend(
        [
            "",
            "| Aggregate | Mean | Sample SD (ddof=1) |",
            "|---|---:|---:|",
        ]
    )
    for key, label in aggregate_metrics:
        metric = _mapping(
            s104_aggregate.get(key),
            f"render_s104_{key}",
        )
        s104_lines.append(
            _table_row(
                (
                    label,
                    claims.cell(
                        f"wavlm_s104_{key}_mean",
                        metric.get("mean"),
                    ),
                    claims.cell(
                        f"wavlm_s104_{key}_sample_std",
                        metric.get("sample_std"),
                    ),
                )
            )
        )
    engineering = _mapping(
        s104.get("engineering_targets"),
        "render_s104_engineering",
    )
    s104_lines.extend(
        [
            "",
            (
                "Engineering gates — phone: "
                + claims.cell(
                    "wavlm_s104_engineering_phone_passed",
                    engineering.get("mean_phone_passed"),
                )
                + "; primary utterance: "
                + claims.cell(
                    "wavlm_s104_engineering_utterance_passed",
                    engineering.get("mean_primary_utterance_passed"),
                )
                + "."
            ),
            "",
            (
                "Development-only, oracle-alignment, silver-label evidence; "
                "not a promotion decision."
            ),
            "<!-- END WAVLM_S104_ADAPTER_RESULTS_TABLE -->",
        ]
    )

    s105_lines = [
        "<!-- BEGIN WAVLM_S105_HIERARCHY_RESULTS_TABLE -->",
        "### S105 hierarchy ablation 개발 결과",
        "",
        "| Variant | Phone macro-F1 mean | Sample SD | "
        "Comprehensibility ICC(2,1) mean | Sample SD |",
        "|---|---:|---:|---:|---:|",
    ]
    s105_variants = _mapping(s105.get("variants"), "render_s105_variants")
    for variant in S105_VARIANTS:
        aggregate = _mapping(
            _mapping(
                s105_variants.get(variant),
                f"render_s105_{variant}",
            ).get("aggregate"),
            f"render_s105_{variant}_aggregate",
        )
        phone = _mapping(
            aggregate.get("phone_macro_f1"),
            f"render_s105_{variant}_phone",
        )
        icc = _mapping(
            aggregate.get("comprehensibility_icc_2_1"),
            f"render_s105_{variant}_icc",
        )
        s105_lines.append(
            _table_row(
                (
                    variant,
                    claims.cell(
                        f"wavlm_s105_{variant}_phone_macro_f1_mean",
                        phone.get("mean"),
                    ),
                    claims.cell(
                        f"wavlm_s105_{variant}_phone_macro_f1_sample_std",
                        phone.get("sample_std"),
                    ),
                    claims.cell(
                        f"wavlm_s105_{variant}_comprehensibility_icc_2_1_mean",
                        icc.get("mean"),
                    ),
                    claims.cell(
                        (f"wavlm_s105_{variant}_comprehensibility_icc_2_1_sample_std"),
                        icc.get("sample_std"),
                    ),
                )
            )
        )
    s105_lines.extend(
        [
            "",
            "| Primary outcome | Estimate | 95% CI lower | 95% CI upper | "
            "Raw p | Holm p | Holm rejected |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    primary = _mapping(s105.get("primary_family"), "render_s105_primary")
    for outcome in sorted(primary):
        item = _mapping(primary[outcome], f"render_s105_primary_{outcome}")
        prefix = f"wavlm_s105_primary_{outcome}"
        s105_lines.append(
            _table_row(
                (
                    outcome,
                    claims.cell(f"{prefix}_estimate", item.get("estimate")),
                    claims.cell(
                        f"{prefix}_confidence_lower",
                        item.get("confidence_lower"),
                    ),
                    claims.cell(
                        f"{prefix}_confidence_upper",
                        item.get("confidence_upper"),
                    ),
                    claims.cell(
                        f"{prefix}_raw_p",
                        item.get("p_value_two_sided_zero"),
                    ),
                    claims.cell(
                        f"{prefix}_holm_p",
                        item.get("holm_adjusted_p_value"),
                    ),
                    claims.cell(
                        f"{prefix}_holm_rejected",
                        item.get("holm_rejected"),
                    ),
                )
            )
        )
    subgroup = _mapping(s105.get("subgroup"), "render_s105_subgroup")
    gate = _mapping(s105.get("adoption_gate"), "render_s105_gate")
    s105_lines.extend(
        [
            "",
            "| S105 gate field | Value |",
            "|---|---:|",
            _table_row(
                (
                    "eligible subgroup count",
                    claims.cell(
                        "wavlm_s105_subgroup_eligible_group_count",
                        subgroup.get("eligible_group_count"),
                    ),
                )
            ),
            _table_row(
                (
                    "worst group drop",
                    claims.cell(
                        "wavlm_s105_subgroup_worst_group_drop",
                        subgroup.get("worst_group_drop"),
                    ),
                )
            ),
            _table_row(
                (
                    "subgroup maximum exclusive",
                    claims.cell(
                        "wavlm_s105_subgroup_maximum_exclusive",
                        subgroup.get("maximum_exclusive"),
                    ),
                )
            ),
            _table_row(
                (
                    "subgroup passed",
                    claims.cell(
                        "wavlm_s105_subgroup_passed",
                        subgroup.get("passed"),
                    ),
                )
            ),
            _table_row(
                (
                    "adoption status",
                    claims.cell(
                        "wavlm_s105_adoption_status",
                        gate.get("status"),
                    ),
                )
            ),
            _table_row(
                (
                    "adopt candidate",
                    claims.cell(
                        "wavlm_s105_adopt_candidate",
                        gate.get("adopt_candidate"),
                    ),
                )
            ),
            "",
            (
                "The committed deterministic `ablation_table.md` was reused and "
                "byte-checked before this richer table was rendered."
            ),
            "<!-- END WAVLM_S105_HIERARCHY_RESULTS_TABLE -->",
        ]
    )

    b07_lines = [
        "<!-- BEGIN B07_GOP_RESULTS_TABLE -->",
        "### B07 acoustic-posterior GOP 개발 결과",
        "",
        "| Seed | Phone macro-F1 | Error AUPRC | Temperature | C | Threshold |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    b07_seeds = _mapping(b07.get("seeds"), "render_b07_seeds")
    for seed in EXPECTED_SEEDS:
        item = _mapping(b07_seeds.get(str(seed)), f"render_b07_{seed}")
        prefix = f"b07_gop_seed_{seed}"
        b07_lines.append(
            _table_row(
                (
                    claims.cell(
                        f"b07_gop_seed_{seed}_seed",
                        seed,
                        decimals=0,
                    ),
                    claims.cell(
                        f"{prefix}_phone_macro_f1",
                        item.get("phone_macro_f1"),
                    ),
                    claims.cell(
                        f"{prefix}_error_auprc",
                        item.get("error_auprc"),
                    ),
                    claims.cell(
                        f"{prefix}_temperature",
                        item.get("selected_temperature"),
                    ),
                    claims.cell(
                        f"{prefix}_regularization_c",
                        item.get("regularization_c"),
                    ),
                    claims.cell(
                        f"{prefix}_threshold",
                        item.get("selected_threshold"),
                    ),
                )
            )
        )
    b07_aggregate = _mapping(
        b07.get("aggregate"),
        "render_b07_aggregate",
    )
    b07_lines.extend(
        [
            "",
            "| Aggregate | Mean | Sample SD (ddof=1) |",
            "|---|---:|---:|",
            _table_row(
                (
                    "Phone macro-F1",
                    claims.cell(
                        "b07_gop_phone_macro_f1_mean",
                        b07_aggregate.get("mean_seed_phone_macro_f1"),
                    ),
                    claims.cell(
                        "b07_gop_phone_macro_f1_sample_std",
                        b07_aggregate.get("sample_std_seed_phone_macro_f1"),
                    ),
                )
            ),
            _table_row(
                (
                    "Error AUPRC",
                    claims.cell(
                        "b07_gop_error_auprc_mean",
                        b07_aggregate.get("mean_seed_error_auprc"),
                    ),
                    claims.cell(
                        "b07_gop_error_auprc_sample_std",
                        b07_aggregate.get("sample_std_seed_error_auprc"),
                    ),
                )
            ),
            "",
            "| Within-run paired control | Value |",
            "|---|---:|",
        ]
    )
    paired = _mapping(
        b07.get("paired_explicit_refit_control"),
        "render_b07_paired",
    )
    for key, label in (
        ("mean_seed_gop_phone_macro_f1", "GOP mean"),
        ("mean_seed_explicit_refit_phone_macro_f1", "Explicit-refit mean"),
        ("mean_seed_phone_macro_f1_delta", "GOP − explicit-refit mean"),
        ("sample_std_seed_phone_macro_f1_delta", "Delta sample SD"),
    ):
        b07_lines.append(
            _table_row(
                (
                    label,
                    claims.cell(f"b07_gop_control_{key}", paired.get(key)),
                )
            )
        )
    b07_gate = _mapping(b07.get("adoption_gate"), "render_b07_gate")
    b07_lines.extend(
        [
            _table_row(
                (
                    "Adoption-gate value",
                    claims.cell(
                        "b07_gop_gate_value",
                        b07_gate.get("value"),
                    ),
                )
            ),
            _table_row(
                (
                    "Adoption-gate passed",
                    claims.cell(
                        "b07_gop_gate_passed",
                        b07_gate.get("passed"),
                    ),
                )
            ),
            "",
            (
                "This is a development-only within-run control and is not the "
                "canonical B03 metric or checkpoint."
            ),
            "<!-- END B07_GOP_RESULTS_TABLE -->",
        ]
    )

    results = _wrap_section(
        "results",
        "\n".join([*s104_lines, "", *s105_lines, "", *b07_lines, ""]) + "\n",
    )

    discussion = _wrap_section(
        "discussion",
        (
            "검증된 개발 산출물에서 S104 adapter, S105 hierarchy ablation, "
            "B07 GOP control의 관찰치는 위 표와 같다. S105 adoption status는 "
            f"{claims.cell('wavlm_s105_discussion_adoption_status', gate.get('status'))}"
            "이고 B07 engineering gate는 "
            f"{claims.cell('b07_gop_discussion_gate_passed', b07_gate.get('passed'))}"
            "였다. 이 관찰은 oracle/silver 개발 근거이므로 인과, 배포 성능, "
            "locked-test 일반화 또는 promotion을 뜻하지 않는다. Fusion 평가는 "
            "아직 완료 근거가 없으며 기존 PENDING 범위에 남는다.\n"
        ),
    )

    reproducibility_lines = [
        "| Stage artifact | SHA-256 | Validator / run ID |",
        "|---|---|---|",
    ]
    reproducibility_inputs = (
        "s104_commit",
        "s104_report",
        "s104_registry",
        "s104_execution_lock",
        "s105_commit",
        "s105_comparison_report",
        "s105_ablation_table",
        "s105_execution_lock",
        *(f"s105_variant_{variant}_registry" for variant in S105_VARIANTS),
        "b07_commit",
        "b07_aggregate_report",
        "b07_paired_control_report",
        "b07_registry",
        "b07_execution_lock",
    )
    for input_name in reproducibility_inputs:
        fingerprint = inputs[input_name]
        stage = input_name.split("_", 1)[0]
        if input_name == f"{stage}_commit":
            third_cell = claims.cell(
                f"{stage}_paper_deep_validator_status",
                deep_validation[stage].get("status"),
            )
        elif input_name in registry_run_ids:
            third_cell = claims.cell(
                f"{input_name}_paper_run_id",
                registry_run_ids[input_name],
            )
        else:
            third_cell = "hash-locked"
        reproducibility_lines.append(
            _table_row(
                (
                    input_name,
                    claims.cell(
                        f"{input_name}_paper_sha256",
                        fingerprint.sha256,
                    ),
                    third_cell,
                )
            )
        )
    reproducibility_lines.extend(
        [
            "",
            "All three public deep validators completed before any result report "
            "was rendered. Source fingerprints were checked again immediately "
            "before the immutable commit pointer was published.",
            "",
        ]
    )
    reproducibility = _wrap_section(
        "reproducibility",
        "\n".join(reproducibility_lines),
    )

    appendix_lines = [
        "### S105 incremental Holm family",
        "",
        "| Pair | Outcome | Estimate | Lower | Upper | Raw p | Holm p | Rejected |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    incremental = _mapping(
        s105.get("incremental_comparisons"),
        "render_s105_incremental",
    )
    for pair in sorted(incremental):
        outcomes = _mapping(incremental[pair], f"render_s105_{pair}")
        safe_pair = re.sub(r"[^a-z0-9]+", "_", pair.casefold()).strip("_")
        for outcome in sorted(outcomes):
            item = _mapping(
                outcomes[outcome],
                f"render_s105_{pair}_{outcome}",
            )
            prefix = f"wavlm_s105_incremental_{safe_pair}_{outcome}"
            appendix_lines.append(
                _table_row(
                    (
                        pair,
                        outcome,
                        claims.cell(f"{prefix}_estimate", item.get("estimate")),
                        claims.cell(
                            f"{prefix}_confidence_lower",
                            item.get("confidence_lower"),
                        ),
                        claims.cell(
                            f"{prefix}_confidence_upper",
                            item.get("confidence_upper"),
                        ),
                        claims.cell(
                            f"{prefix}_raw_p",
                            item.get("p_value_two_sided_zero"),
                        ),
                        claims.cell(
                            f"{prefix}_holm_p",
                            item.get("holm_adjusted_p_value"),
                        ),
                        claims.cell(
                            f"{prefix}_holm_rejected",
                            item.get("holm_rejected"),
                        ),
                    )
                )
            )
    appendix_lines.extend(
        [
            "",
            "### B07 within-run explicit-refit paired seeds",
            "",
            "| Seed | GOP macro-F1 | Explicit-refit macro-F1 | Delta |",
            "|---:|---:|---:|---:|",
        ]
    )
    paired_seeds = _mapping(
        paired.get("seeds"),
        "render_b07_paired_seeds",
    )
    for seed in EXPECTED_SEEDS:
        item = _mapping(
            paired_seeds.get(str(seed)),
            f"render_b07_paired_{seed}",
        )
        prefix = f"b07_gop_control_seed_{seed}"
        appendix_lines.append(
            _table_row(
                (
                    claims.cell(f"{prefix}_seed", seed, decimals=0),
                    claims.cell(
                        f"{prefix}_gop_phone_macro_f1",
                        item.get("gop_phone_macro_f1"),
                    ),
                    claims.cell(
                        f"{prefix}_explicit_refit_phone_macro_f1",
                        item.get("explicit_refit_phone_macro_f1"),
                    ),
                    claims.cell(
                        f"{prefix}_phone_macro_f1_delta",
                        item.get("phone_macro_f1_delta"),
                    ),
                )
            )
        )
    appendix = _wrap_section(
        "appendix",
        "\n".join([*appendix_lines, ""]) + "\n",
    )

    sections = {
        "methods": methods,
        "analysis_status": analysis_status,
        "results": results,
        "discussion": discussion,
        "reproducibility": reproducibility,
        "appendix": appendix,
    }
    markdown = "# S104 + S105 + B07 review-only manuscript amendment\n\n" + "\n".join(
        sections[section] for section in SECTIONS
    )
    payload: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA,
        "status": "completed",
        "scope": {
            "development_only": True,
            "oracle_alignment": True,
            "silver_label": True,
            "stack_accessed": False,
            "calibration_accessed": False,
            "locked_test_accessed": False,
            "promotion_decision_allowed": False,
            "canonical_b03_metric_claimed": False,
        },
        "null_contract": {
            "source_json_null": None,
            "paper_json_and_markdown_token": "NA",
            "zero_imputation_allowed": False,
            "row_omission_allowed": False,
        },
        "deep_validation": deepcopy(dict(deep_validation)),
        "provenance": {
            name: _record(root, value) for name, value in sorted(inputs.items())
        },
        "s104": deepcopy(dict(s104)),
        "s105": deepcopy(dict(s105)),
        "b07": deepcopy(dict(b07)),
        "evidence_values": deepcopy(claims.values),
    }
    return RenderedPaper(
        sections=sections,
        markdown=markdown,
        payload=payload,
        claim_specs=claims.specs,
    )


def _yaml_bytes(value: Mapping[str, Any]) -> bytes:
    return yaml.safe_dump(
        dict(value),
        allow_unicode=True,
        sort_keys=False,
        width=1000,
    ).encode("utf-8")


def _write_new(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    created = False
    try:
        descriptor = os.open(path, flags, 0o600)
        created = True
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(descriptor)
        _fsync_directory(path.parent)
    except BaseException:
        if created:
            path.unlink(missing_ok=True)
        raise


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_replace(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_created(paths: Sequence[Path], generation_root: Path) -> None:
    for path in reversed(paths):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        generation_root.rmdir()
        _fsync_directory(generation_root.parent)
    except OSError:
        pass


def _pending_markers(
    manuscript: str,
    pending_contract: Mapping[str, Any],
) -> tuple[list[str], str]:
    pattern = pending_contract.get("marker_regex", r"\[PENDING:[^\]]+\]")
    if not isinstance(pattern, str):
        _fail("pending_marker_regex")
    try:
        found = re.findall(pattern, manuscript, flags=re.MULTILINE)
    except re.error:
        _fail("pending_marker_regex")
    markers = [
        "".join(item) if isinstance(item, tuple) else str(item) for item in found
    ]
    digest = hashlib.sha256("\n".join(sorted(markers)).encode("utf-8")).hexdigest()
    return markers, digest


def _apply_candidate_edits(
    manuscript: str,
    edits: Mapping[str, Any],
    sections: Mapping[str, str],
) -> str:
    candidate = manuscript
    for section in SECTIONS:
        if BEGIN_MARKERS[section] in candidate or END_MARKERS[section] in candidate:
            _fail(f"candidate_{section}_already_present")
        edit = _mapping(edits.get(section), f"candidate_edit_{section}")
        anchor = _string(edit.get("anchor"), f"candidate_edit_{section}_anchor")
        if candidate.count(anchor) != 1:
            _fail(f"candidate_edit_{section}_anchor_count")
        block = sections[section]
        operation = edit.get("operation")
        if operation == "insert_before":
            candidate = candidate.replace(anchor, block + "\n" + anchor, 1)
        elif operation == "insert_after":
            candidate = candidate.replace(anchor, anchor + "\n" + block, 1)
        elif operation == "replace_exact":
            candidate = candidate.replace(anchor, block, 1)
        else:
            _fail(f"candidate_edit_{section}_operation")
        if (
            candidate.count(BEGIN_MARKERS[section]) != 1
            or candidate.count(END_MARKERS[section]) != 1
        ):
            _fail(f"candidate_{section}_block_count")
    return candidate


def _evidence_claims(
    claim_specs: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for claim_id, spec in sorted(claim_specs.items()):
        comparison = spec.get("comparison")
        claim: dict[str, Any] = {
            "id": claim_id,
            "source": {
                "artifact": "s104_s105_b07_paper_results_json",
                "kind": "json_pointer",
                "pointer": f"/evidence_values/{claim_id}",
            },
            "document": {"regex": spec.get("regex")},
            "comparison": {"type": comparison},
        }
        if comparison == "numeric":
            claim["comparison"]["decimals"] = spec.get("decimals")
        output.append(claim)
    return output


def _artifact_additions(
    *,
    root: Path,
    inputs: Mapping[str, Fingerprint],
    results_markdown: Mapping[str, Any],
    results_json: Mapping[str, Any],
    renderer_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    output = {
        "s104_s105_b07_paper_results_markdown": dict(results_markdown),
        "s104_s105_b07_paper_results_json": dict(results_json),
        "s104_s105_b07_paper_renderer_receipt": dict(renderer_receipt),
    }
    for input_name, alias in sorted(EVIDENCE_ALIASES.items()):
        fingerprint = inputs[input_name]
        output[alias] = {
            "path": fingerprint.path.relative_to(root).as_posix(),
            "sha256": fingerprint.sha256,
        }
    return output


def _guard_additions() -> dict[str, Any]:
    access_checks = [
        {
            "artifact": "s104_s105_b07_paper_results_json",
            "pointer": f"/scope/{name}",
        }
        for name in (
            "stack_accessed",
            "calibration_accessed",
            "locked_test_accessed",
            "promotion_decision_allowed",
            "canonical_b03_metric_claimed",
        )
    ]
    access_checks.extend(
        {
            "artifact": "wavlm_s104_report",
            "pointer": f"/leakage/{name}",
        }
        for name in (
            "stack_accessed",
            "calibration_accessed",
            "locked_test_accessed",
        )
    )
    access_checks.extend(
        {
            "artifact": "wavlm_s105_comparison_report",
            "pointer": f"/leakage/{name}",
        }
        for name in (
            "stack_accessed",
            "calibration_accessed",
            "locked_test_accessed",
        )
    )
    registries = [
        {
            "artifact": "wavlm_s104_registry",
            "array_pointer": "/metrics",
            "split_field": "split",
            "forbidden_splits": sorted(FORBIDDEN_SPLITS),
        },
        *(
            {
                "artifact": f"wavlm_s105_registry_{variant}",
                "array_pointer": "/metrics",
                "split_field": "split",
                "forbidden_splits": sorted(FORBIDDEN_SPLITS),
            }
            for variant in S105_VARIANTS
        ),
        {
            "artifact": "b07_gop_registry",
            "array_pointer": "/metrics",
            "split_field": "split",
            "forbidden_splits": sorted(FORBIDDEN_SPLITS),
        },
    ]
    return {
        "access_checks": access_checks,
        "registries": registries,
    }


def _merge_evidence(
    active: Mapping[str, Any],
    *,
    artifacts: Mapping[str, Any],
    claims: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    candidate = deepcopy(dict(active))
    current_artifacts = candidate.get("artifacts")
    if not isinstance(current_artifacts, dict):
        _fail("active_artifacts")
    if set(current_artifacts) & set(artifacts):
        _fail("candidate_artifact_alias_collision")
    current_artifacts.update(deepcopy(dict(artifacts)))

    current_claims = candidate.get("claims")
    if not isinstance(current_claims, list):
        _fail("active_claims")
    existing_ids = {
        item.get("id") for item in current_claims if isinstance(item, Mapping)
    }
    new_ids = {item.get("id") for item in claims}
    if len(new_ids) != len(claims) or existing_ids & new_ids:
        _fail("candidate_claim_collision")
    current_claims.extend(deepcopy(list(claims)))

    guard = candidate.get("locked_test_guard")
    if not isinstance(guard, dict):
        _fail("active_locked_guard")
    additions = _guard_additions()
    for key in ("access_checks", "registries"):
        current = guard.get(key)
        if not isinstance(current, list):
            _fail(f"active_locked_guard_{key}")
        current.extend(deepcopy(additions[key]))
    return candidate


def _assert_unchanged(
    expected: Iterable[Fingerprint],
    *,
    code: str,
) -> None:
    checked: set[Path] = set()
    for item in expected:
        if item.path in checked:
            continue
        checked.add(item.path)
        if _fingerprint(item.path, code=code) != item:
            _fail(f"{code}_changed")


def _read_active_paper(
    *,
    root: Path,
    targets: Targets,
    contract: Mapping[str, Any],
) -> tuple[str, Mapping[str, Any], Mapping[str, Any]]:
    try:
        report = validate_evidence_map(
            targets.evidence_map.path,
            workspace_root=root,
        )
    except (OSError, ValueError):
        _fail("active_evidence_validation_error")
    if report.get("status") != "passed":
        _fail("active_evidence_not_passed")
    manuscript_record = _mapping(
        report.get("manuscript"),
        "active_manuscript_record",
    )
    if (
        manuscript_record.get("path")
        != targets.manuscript.path.relative_to(root).as_posix()
        or manuscript_record.get("sha256") != targets.manuscript.sha256
    ):
        _fail("active_manuscript_binding")
    pending = _mapping(
        report.get("pending_contract"),
        "active_pending_contract",
    )
    if (
        pending.get("status") != "passed"
        or pending.get("marker_count") != contract.get("pending_marker_count")
        or pending.get("markers_sha256") != contract.get("pending_markers_sha256")
    ):
        _fail("active_pending_contract")
    try:
        manuscript = targets.manuscript.path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        _fail("active_manuscript_read")
    evidence = _load_yaml(targets.evidence_map.path)
    configured_pending = _mapping(
        evidence.get("pending_contract"),
        "active_pending_config",
    )
    markers, digest = _pending_markers(manuscript, configured_pending)
    if (
        len(markers) != EXPECTED_PENDING_COUNT
        or digest != CURRENT_PENDING_MARKERS_SHA256
    ):
        _fail("active_pending_recomputation")
    return manuscript, evidence, report


def _validator_and_config_preflight(
    *,
    root: Path,
    manifest: Mapping[str, Any],
    runner: ValidatorRunner,
) -> tuple[
    dict[str, Fingerprint],
    dict[str, Fingerprint],
    dict[str, Mapping[str, Any]],
]:
    validator_records = _mapping(manifest.get("validators"), "validators")
    input_records = _mapping(manifest.get("inputs"), "inputs")
    validators = {
        stage: _verify_record(
            root,
            validator_records.get(stage),
            code=f"validator_{stage}",
        )
        for stage in EXPECTED_VALIDATORS
    }
    configs = {
        stage: _verify_record(
            root,
            input_records.get(f"{stage}_config"),
            code=f"input_{stage}_config",
        )
        for stage in EXPECTED_VALIDATORS
    }
    contract = _mapping(manifest.get("contract"), "contract")
    timeout = _integer(
        contract.get("validator_timeout_seconds"),
        "validator_timeout",
    )
    deep: dict[str, Mapping[str, Any]] = {}
    for stage in EXPECTED_VALIDATORS:
        result = runner(
            stage,
            root,
            validators[stage],
            configs[stage],
            timeout,
        )
        deep[stage] = _validate_deep_result(stage, result)
        _assert_unchanged(
            (*validators.values(), *configs.values()),
            code="validator_preflight_source",
        )
    return validators, configs, deep


def _load_and_bind_inputs(
    *,
    root: Path,
    manifest: Mapping[str, Any],
    deep: Mapping[str, Mapping[str, Any]],
) -> tuple[
    dict[str, Fingerprint],
    dict[str, Mapping[str, Any]],
    dict[str, Mapping[str, Any]],
]:
    records = _mapping(manifest.get("inputs"), "inputs")
    inputs = {
        name: _verify_record(
            root,
            records.get(name),
            code=f"input_{name}",
        )
        for name in EXPECTED_INPUTS
    }
    if len({value.path for value in inputs.values()}) != len(inputs):
        _fail("input_path_collision")

    commits: dict[str, Mapping[str, Any]] = {}
    configs: dict[str, Mapping[str, Any]] = {}
    for stage in EXPECTED_VALIDATORS:
        commit_name = f"{stage}_commit"
        config_name = f"{stage}_config"
        commit = _load_json(inputs[commit_name].path, commit_name)
        config_path = inputs[config_name].path
        config = (
            _load_json(config_path, config_name)
            if config_path.suffix.casefold() == ".json"
            else _load_yaml(config_path)
        )
        relative_commit = inputs[commit_name].path.relative_to(root).as_posix()
        deep_commit_sha = deep[stage].get("commit_sha256")
        if not _tree_contains_string(config, relative_commit) and (
            deep_commit_sha != inputs[commit_name].sha256
        ):
            _fail(f"{stage}_config_commit_binding")
        commits[stage] = commit
        configs[stage] = config

    for name, artifact in inputs.items():
        stage = name.split("_", 1)[0]
        if name in {f"{stage}_config", f"{stage}_commit"}:
            continue
        if not _commit_binds(
            root,
            inputs[f"{stage}_commit"].path,
            commits[stage],
            artifact,
        ):
            _fail(f"{name}_not_commit_bound")
    return inputs, commits, configs


def _load_targets(
    *,
    root: Path,
    manifest: Mapping[str, Any],
) -> Targets:
    target_records = _mapping(manifest.get("targets"), "targets")
    manuscript = _verify_record(
        root,
        target_records.get("manuscript"),
        code="target_manuscript",
    )
    evidence_map = _verify_record(
        root,
        target_records.get("evidence_map"),
        code="target_evidence_map",
    )
    if manuscript.path.relative_to(root).as_posix() != CANONICAL_MANUSCRIPT:
        _fail("target_manuscript_not_canonical")
    if evidence_map.path.relative_to(root).as_posix() != CANONICAL_EVIDENCE_MAP:
        _fail("target_evidence_map_not_canonical")
    return Targets(manuscript=manuscript, evidence_map=evidence_map)


def _renderer_inputs(
    inputs: Mapping[str, Fingerprint],
) -> tuple[
    Mapping[str, Any],
    Mapping[str, Any],
    Mapping[str, Mapping[str, Any]],
    str,
    Mapping[str, Any],
    Mapping[str, Any],
    Mapping[int, Mapping[str, Any]],
    Mapping[int, Mapping[str, Any]],
]:
    s104_report = _load_json(inputs["s104_report"].path, "s104_report")
    s105_comparison = _load_json(
        inputs["s105_comparison_report"].path,
        "s105_comparison_report",
    )
    s105_variants = {
        variant: _load_json(
            inputs[f"s105_variant_{variant}_report"].path,
            f"s105_variant_{variant}_report",
        )
        for variant in S105_VARIANTS
    }
    try:
        s105_table = inputs["s105_ablation_table"].path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        _fail("s105_ablation_table_read")
    b07_aggregate = _load_json(
        inputs["b07_aggregate_report"].path,
        "b07_aggregate_report",
    )
    b07_paired = _load_json(
        inputs["b07_paired_control_report"].path,
        "b07_paired_control_report",
    )
    b07_metrics = {
        seed: _load_json(
            inputs[f"b07_seed_{seed}_metrics"].path,
            f"b07_seed_{seed}_metrics",
        )
        for seed in EXPECTED_SEEDS
    }
    b07_selections = {
        seed: _load_json(
            inputs[f"b07_seed_{seed}_selection_trace"].path,
            f"b07_seed_{seed}_selection_trace",
        )
        for seed in EXPECTED_SEEDS
    }
    return (
        s104_report,
        s105_comparison,
        s105_variants,
        s105_table,
        b07_aggregate,
        b07_paired,
        b07_metrics,
        b07_selections,
    )


def _load_registries(
    inputs: Mapping[str, Fingerprint],
) -> dict[str, Mapping[str, Any]]:
    names = [
        "s104_registry",
        *(f"s105_variant_{variant}_registry" for variant in S105_VARIANTS),
        "b07_registry",
    ]
    registries = {name: _load_json(inputs[name].path, name) for name in names}
    _assert_registries_safe(registries)
    return registries


def _registry_run_ids(
    registries: Mapping[str, Mapping[str, Any]],
) -> dict[str, str]:
    return {
        name: _string(registry.get("run_id"), f"{name}_run_id")
        for name, registry in registries.items()
    }


def _validate_output_root(
    root: Path,
    configured: str | Path,
    protected: Iterable[Path],
) -> Path:
    output = _workspace_path(
        root,
        configured,
        must_exist=False,
        code="output_root",
    )
    relative = output.relative_to(root)
    if relative.parts[: len(OUTPUT_PREFIX)] != OUTPUT_PREFIX:
        _fail("output_root_prefix")
    if output in set(protected):
        _fail("output_root_collision")
    if output.exists() and (output.is_symlink() or not output.is_dir()):
        _fail("output_root_not_directory")
    return output


def load_committed_paper_amendment(
    *,
    commit_path: str | Path,
    workspace_root: str | Path,
) -> Mapping[str, Any]:
    """Revalidate an immutable candidate bundle without canonical writes."""

    root = Path(workspace_root).resolve(strict=True)
    resolved = _workspace_path(
        root,
        commit_path,
        must_exist=True,
        code="amendment_commit",
    )
    commit_file = _fingerprint(resolved, code="amendment_commit")
    commit = _load_json(resolved, "amendment_commit")
    if (
        set(commit)
        != {
            "schema_version",
            "status",
            "generation_id",
            "promotion_receipt",
            "canonical_files_modified",
            "manual_apply_required",
            "binding_sha256",
        }
        or commit.get("canonical_files_modified") is not False
        or commit.get("manual_apply_required") is not True
        or commit.get("schema_version") != COMMIT_SCHEMA
        or commit.get("status") != "committed"
        or commit.get("binding_sha256") != _binding(commit)
    ):
        _fail("amendment_commit_contract")
    receipt = _verify_record(
        root,
        commit.get("promotion_receipt"),
        code="amendment_receipt",
    )
    receipt_document = _load_json(receipt.path, "amendment_receipt")
    if (
        receipt_document.get("schema_version") != PROMOTION_RECEIPT_SCHEMA
        or receipt_document.get("status") != "completed"
        or receipt_document.get("binding_sha256") != _binding(receipt_document)
        or receipt_document.get("generation_id") != commit.get("generation_id")
    ):
        _fail("amendment_receipt_contract")
    output_records = _mapping(
        receipt_document.get("outputs"),
        "amendment_receipt_outputs",
    )
    expected_output_names = {
        "paper_results_markdown",
        "paper_results_json",
        "renderer_receipt",
        "manuscript_candidate",
        "evidence_map_candidate",
        "evidence_map_validation",
        "evidence_map_additions",
        "candidate_validation",
    }
    if set(output_records) != expected_output_names:
        _fail("amendment_receipt_output_set")
    outputs = {
        name: _verify_record(
            root,
            record,
            code=f"amendment_output_{name}",
        )
        for name, record in output_records.items()
    }
    renderer_receipt = _load_json(
        outputs["renderer_receipt"].path,
        "renderer_receipt",
    )
    if (
        renderer_receipt.get("schema_version") != RENDERER_RECEIPT_SCHEMA
        or renderer_receipt.get("status") != "completed"
        or renderer_receipt.get("generation_id") != commit.get("generation_id")
        or renderer_receipt.get("binding_sha256") != _binding(renderer_receipt)
        or renderer_receipt.get("canonical_files_modified") is not False
    ):
        _fail("renderer_receipt_contract")
    renderer_outputs = _mapping(
        renderer_receipt.get("outputs"),
        "renderer_receipt_outputs",
    )
    for name in ("paper_results_markdown", "paper_results_json"):
        if renderer_outputs.get(name) != output_records.get(name):
            _fail("renderer_receipt_output_binding")
    result_payload = _load_json(
        outputs["paper_results_json"].path,
        "paper_results_json",
    )
    result_scope = _mapping(
        result_payload.get("scope"),
        "paper_results_scope",
    )
    result_null = _mapping(
        result_payload.get("null_contract"),
        "paper_results_null_contract",
    )
    if (
        result_payload.get("schema_version") != RESULT_SCHEMA
        or result_payload.get("status") != "completed"
        or result_scope.get("stack_accessed") is not False
        or result_scope.get("calibration_accessed") is not False
        or result_scope.get("locked_test_accessed") is not False
        or result_scope.get("promotion_decision_allowed") is not False
        or result_null
        != {
            "source_json_null": None,
            "paper_json_and_markdown_token": "NA",
            "zero_imputation_allowed": False,
            "row_omission_allowed": False,
        }
    ):
        _fail("paper_results_contract")
    validation = _load_json(
        outputs["candidate_validation"].path,
        "candidate_validation",
    )
    if validation.get("status") != "passed":
        _fail("amendment_candidate_validation")
    try:
        repeated_validation = validate_evidence_map(
            outputs["evidence_map_validation"].path,
            workspace_root=root,
        )
    except (OSError, ValueError):
        _fail("amendment_evidence_revalidation_error")
    if repeated_validation != validation:
        _fail("amendment_evidence_revalidation_mismatch")
    pending = _mapping(
        validation.get("pending_contract"),
        "amendment_candidate_pending",
    )
    if (
        pending.get("marker_count") != EXPECTED_PENDING_COUNT
        or pending.get("markers_sha256") != CURRENT_PENDING_MARKERS_SHA256
    ):
        _fail("amendment_candidate_pending")
    return {
        "commit": commit,
        "commit_file": commit_file,
        "receipt": receipt_document,
        "outputs": outputs,
        "candidate_validation": validation,
    }


def prepare_paper_amendment(
    *,
    manifest_path: str | Path,
    workspace_root: str | Path,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    validator_runner: ValidatorRunner | None = None,
) -> Mapping[str, Any]:
    """Publish a review-only, content-addressed paper-amendment bundle."""

    root = Path(workspace_root).resolve(strict=True)
    manifest_resolved = _workspace_path(
        root,
        manifest_path,
        must_exist=True,
        code="manifest",
    )
    manifest_file = _fingerprint(manifest_resolved, code="manifest")
    manifest = _load_yaml(manifest_resolved)
    _validate_manifest_shape(manifest, allow_placeholder=False)

    runner = validator_runner or _default_validator_runner
    validators, preflight_configs, deep = _validator_and_config_preflight(
        root=root,
        manifest=manifest,
        runner=runner,
    )
    inputs, _, _ = _load_and_bind_inputs(
        root=root,
        manifest=manifest,
        deep=deep,
    )
    for stage, config in preflight_configs.items():
        if inputs[f"{stage}_config"] != config:
            _fail(f"{stage}_config_changed_after_validator")
    targets = _load_targets(root=root, manifest=manifest)
    protected = {
        manifest_file.path,
        *(value.path for value in validators.values()),
        *(value.path for value in inputs.values()),
        targets.manuscript.path,
        targets.evidence_map.path,
    }
    output_base = _validate_output_root(root, output_root, protected)

    contract = _mapping(manifest.get("contract"), "contract")
    active_manuscript, active_evidence, active_validation = _read_active_paper(
        root=root,
        targets=targets,
        contract=contract,
    )
    (
        s104_report,
        s105_comparison,
        s105_variant_reports,
        s105_table,
        b07_aggregate,
        b07_paired,
        b07_seed_metrics,
        b07_selections,
    ) = _renderer_inputs(inputs)
    registries = _load_registries(inputs)
    registry_run_ids = _registry_run_ids(registries)
    s104 = _s104_projection(s104_report)
    s105 = _s105_projection(
        s105_comparison,
        s105_variant_reports,
        s105_table,
    )
    b07 = _b07_projection(
        b07_aggregate,
        b07_paired,
        b07_seed_metrics,
        b07_selections,
    )
    rendered = _render_paper(
        root=root,
        inputs=inputs,
        deep_validation=deep,
        registry_run_ids=registry_run_ids,
        s104=s104,
        s105=s105,
        b07=b07,
    )
    result_markdown_bytes = rendered.markdown.encode("utf-8")
    result_json_bytes = _canonical_json_bytes(rendered.payload, pretty=True)
    implementation = _fingerprint(
        Path(__file__).resolve(strict=True),
        code="implementation",
    )
    generation_material = {
        "manifest": _record(root, manifest_file),
        "validators": {
            name: _record(root, value) for name, value in sorted(validators.items())
        },
        "inputs": {
            name: _record(root, value) for name, value in sorted(inputs.items())
        },
        "targets": {
            "manuscript": _record(root, targets.manuscript),
            "evidence_map": _record(root, targets.evidence_map),
        },
        "implementation": _record(root, implementation),
        "deep_validation": deepcopy(dict(deep)),
        "result_markdown_sha256": hashlib.sha256(result_markdown_bytes).hexdigest(),
        "result_json_sha256": hashlib.sha256(result_json_bytes).hexdigest(),
        "pending_marker_count": EXPECTED_PENDING_COUNT,
        "pending_markers_sha256": CURRENT_PENDING_MARKERS_SHA256,
    }
    generation_id = hashlib.sha256(
        _canonical_json_bytes(generation_material)
    ).hexdigest()
    generation_root = output_base / "generations" / generation_id
    commit_path = output_base / "commit.json"
    if generation_root.exists():
        _fail("amendment_generation_exists")
    if commit_path.exists() and (commit_path.is_symlink() or not commit_path.is_file()):
        _fail("amendment_commit_not_regular")

    result_markdown_path = generation_root / "paper-results.md"
    result_json_path = generation_root / "paper-results.json"
    renderer_receipt_path = generation_root / "renderer-receipt.json"
    renderer_receipt: dict[str, Any] = {
        "schema_version": RENDERER_RECEIPT_SCHEMA,
        "status": "completed",
        "generation_id": generation_id,
        "manifest": _record(root, manifest_file),
        "implementation": _record(root, implementation),
        "validators": {
            name: _record(root, value) for name, value in sorted(validators.items())
        },
        "deep_validation": deepcopy(dict(deep)),
        "inputs": {
            name: _record(root, value) for name, value in sorted(inputs.items())
        },
        "outputs": {
            "paper_results_markdown": _record_bytes(
                root,
                result_markdown_path,
                result_markdown_bytes,
            ),
            "paper_results_json": _record_bytes(
                root,
                result_json_path,
                result_json_bytes,
            ),
        },
        "null_contract": {
            "source_json_null": None,
            "paper_json_and_markdown_token": "NA",
            "zero_imputation_allowed": False,
            "row_omission_allowed": False,
        },
        "canonical_files_modified": False,
    }
    renderer_receipt["binding_sha256"] = _binding(renderer_receipt)
    renderer_receipt_bytes = _canonical_json_bytes(
        renderer_receipt,
        pretty=True,
    )

    created: list[Path] = []
    commit_bytes: bytes | None = None
    try:
        for path, value in (
            (result_markdown_path, result_markdown_bytes),
            (result_json_path, result_json_bytes),
            (renderer_receipt_path, renderer_receipt_bytes),
        ):
            _write_new(path, value)
            created.append(path)

        edits = _mapping(manifest.get("edits"), "edits")
        candidate_manuscript = _apply_candidate_edits(
            active_manuscript,
            edits,
            rendered.sections,
        )
        active_pending = _mapping(
            active_evidence.get("pending_contract"),
            "active_pending_contract",
        )
        before_markers, before_hash = _pending_markers(
            active_manuscript,
            active_pending,
        )
        after_markers, after_hash = _pending_markers(
            candidate_manuscript,
            active_pending,
        )
        if (
            len(before_markers) != EXPECTED_PENDING_COUNT
            or before_markers != after_markers
            or before_hash != after_hash
            or after_hash != CURRENT_PENDING_MARKERS_SHA256
        ):
            _fail("candidate_pending_markers_changed")

        result_markdown_record = _record_bytes(
            root,
            result_markdown_path,
            result_markdown_bytes,
        )
        result_json_record = _record_bytes(
            root,
            result_json_path,
            result_json_bytes,
        )
        renderer_receipt_record = _record_bytes(
            root,
            renderer_receipt_path,
            renderer_receipt_bytes,
        )
        artifact_additions = _artifact_additions(
            root=root,
            inputs=inputs,
            results_markdown=result_markdown_record,
            results_json=result_json_record,
            renderer_receipt=renderer_receipt_record,
        )
        claim_additions = _evidence_claims(rendered.claim_specs)
        canonical_candidate = _merge_evidence(
            active_evidence,
            artifacts=artifact_additions,
            claims=claim_additions,
        )
        canonical_candidate["manuscript"] = CANONICAL_MANUSCRIPT

        manuscript_candidate_path = generation_root / "manuscript.candidate.md"
        evidence_candidate_path = generation_root / "evidence-map.candidate.yaml"
        evidence_validation_path = generation_root / "evidence-map.validation.yaml"
        evidence_additions_path = generation_root / "evidence-map.additions.yaml"
        validation_candidate = deepcopy(canonical_candidate)
        validation_candidate["manuscript"] = manuscript_candidate_path.relative_to(
            root
        ).as_posix()
        additions_document = {
            "schema_version": 1,
            "artifacts": artifact_additions,
            "claims": claim_additions,
            "locked_test_guard_additions": _guard_additions(),
            "edits": deepcopy(dict(edits)),
            "pending_contract": {
                "preserved": True,
                "marker_count": EXPECTED_PENDING_COUNT,
                "markers_sha256": CURRENT_PENDING_MARKERS_SHA256,
            },
            "manual_apply_required": True,
            "canonical_write_performed": False,
        }
        document_bytes = {
            "manuscript_candidate": candidate_manuscript.encode("utf-8"),
            "evidence_map_candidate": _yaml_bytes(canonical_candidate),
            "evidence_map_validation": _yaml_bytes(validation_candidate),
            "evidence_map_additions": _yaml_bytes(additions_document),
        }
        document_paths = {
            "manuscript_candidate": manuscript_candidate_path,
            "evidence_map_candidate": evidence_candidate_path,
            "evidence_map_validation": evidence_validation_path,
            "evidence_map_additions": evidence_additions_path,
        }
        for name, path in document_paths.items():
            _write_new(path, document_bytes[name])
            created.append(path)

        try:
            candidate_validation = validate_evidence_map(
                evidence_validation_path,
                workspace_root=root,
            )
        except (OSError, ValueError):
            _fail("candidate_evidence_validation_error")
        if candidate_validation.get("status") != "passed":
            _fail("candidate_evidence_not_passed")
        candidate_pending = _mapping(
            candidate_validation.get("pending_contract"),
            "candidate_pending_contract",
        )
        active_pending_report = _mapping(
            active_validation.get("pending_contract"),
            "active_pending_report",
        )
        if (
            candidate_pending.get("marker_count")
            != active_pending_report.get("marker_count")
            or candidate_pending.get("markers_sha256")
            != active_pending_report.get("markers_sha256")
            or candidate_pending.get("marker_count") != EXPECTED_PENDING_COUNT
            or candidate_pending.get("markers_sha256") != CURRENT_PENDING_MARKERS_SHA256
        ):
            _fail("candidate_pending_validation_binding")
        validation_bytes = _canonical_json_bytes(
            candidate_validation,
            pretty=True,
        )
        candidate_validation_path = generation_root / "candidate-validation.json"
        _write_new(candidate_validation_path, validation_bytes)
        created.append(candidate_validation_path)

        output_records = {
            "paper_results_markdown": result_markdown_record,
            "paper_results_json": result_json_record,
            "renderer_receipt": renderer_receipt_record,
            **{
                name: _record_bytes(
                    root,
                    document_paths[name],
                    document_bytes[name],
                )
                for name in document_paths
            },
            "candidate_validation": _record_bytes(
                root,
                candidate_validation_path,
                validation_bytes,
            ),
        }
        promotion_receipt: dict[str, Any] = {
            "schema_version": PROMOTION_RECEIPT_SCHEMA,
            "status": "completed",
            "generation_id": generation_id,
            "manifest": _record(root, manifest_file),
            "implementation": _record(root, implementation),
            "validators": {
                name: _record(root, value) for name, value in sorted(validators.items())
            },
            "deep_validation": deepcopy(dict(deep)),
            "inputs": {
                name: _record(root, value) for name, value in sorted(inputs.items())
            },
            "targets": {
                "manuscript": _record(root, targets.manuscript),
                "evidence_map": _record(root, targets.evidence_map),
            },
            "outputs": output_records,
            "pending_contract": {
                "preserved": True,
                "marker_count": EXPECTED_PENDING_COUNT,
                "markers_sha256": CURRENT_PENDING_MARKERS_SHA256,
            },
            "candidate_generic_evidence_validation": "passed",
            "manual_apply_required": True,
            "canonical_files_modified": False,
        }
        promotion_receipt["binding_sha256"] = _binding(promotion_receipt)
        promotion_receipt_bytes = _canonical_json_bytes(
            promotion_receipt,
            pretty=True,
        )
        promotion_receipt_path = generation_root / "promotion-receipt.json"
        _write_new(promotion_receipt_path, promotion_receipt_bytes)
        created.append(promotion_receipt_path)

        _assert_unchanged(
            (
                manifest_file,
                implementation,
                *validators.values(),
                *inputs.values(),
                targets.manuscript,
                targets.evidence_map,
            ),
            code="promotion_source",
        )
        commit: dict[str, Any] = {
            "schema_version": COMMIT_SCHEMA,
            "status": "committed",
            "generation_id": generation_id,
            "promotion_receipt": _record_bytes(
                root,
                promotion_receipt_path,
                promotion_receipt_bytes,
            ),
            "canonical_files_modified": False,
            "manual_apply_required": True,
        }
        commit["binding_sha256"] = _binding(commit)
        commit_bytes = _canonical_json_bytes(commit, pretty=True)
        _atomic_replace(commit_path, commit_bytes)
        loaded = load_committed_paper_amendment(
            commit_path=commit_path,
            workspace_root=root,
        )
        loaded_commit = _mapping(loaded.get("commit"), "published_commit")
        if loaded_commit.get("generation_id") != generation_id:
            _fail("published_generation_id")
    except BaseException:
        commit_matches = False
        if commit_bytes is not None:
            try:
                commit_matches = commit_path.read_bytes() == commit_bytes
            except OSError:
                pass
        if not commit_matches:
            _remove_created(created, generation_root)
        raise

    return {
        "status": "completed",
        "generation_id": generation_id,
        "commit": commit_path.relative_to(root).as_posix(),
        "candidate_manuscript": manuscript_candidate_path.relative_to(root).as_posix(),
        "candidate_evidence_map": evidence_candidate_path.relative_to(root).as_posix(),
        "candidate_generic_evidence_validation": "passed",
        "pending_marker_count": EXPECTED_PENDING_COUNT,
        "pending_markers_sha256": CURRENT_PENDING_MARKERS_SHA256,
        "pending_contract_preserved": True,
        "canonical_files_modified": False,
        "manual_apply_required": True,
    }


__all__ = [
    "CANONICAL_EVIDENCE_MAP",
    "CANONICAL_MANUSCRIPT",
    "COMMIT_SCHEMA",
    "CURRENT_PENDING_MARKERS_SHA256",
    "DEFAULT_OUTPUT_ROOT",
    "EXPECTED_PENDING_COUNT",
    "MANIFEST_SCHEMA",
    "PROMOTION_RECEIPT_SCHEMA",
    "PaperAmendmentError",
    "ValidatorRunner",
    "load_committed_paper_amendment",
    "prepare_paper_amendment",
    "validate_config_only",
]
