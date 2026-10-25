#!/usr/bin/env python3
"""Validate repository-current source pins in Git-tracked config files.

Only files present in the Git index can satisfy a repository-current pin.  A
matching ignored or untracked runtime artifact is counted separately and never
contributes to the successful-pin count.

Some WavLM configs are immutable records of an older, fail-closed development
attempt.  Their known stale pins are excluded only when the complete config
digest and non-promotion safety fields match an explicit status below.  Changing
any config byte invalidates its exemptions and restores the normal live check.
"""

from __future__ import annotations

import argparse
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_UNRESOLVED_DIGEST_PATTERN = re.compile(r"^TBD(?:_[A-Z0-9]+)*$")
_YAML_KEY_VALUE_PATTERN = re.compile(
    r"^(?P<spaces> *)(?P<sequence>- )?"
    r"(?P<key>[A-Za-z_][A-Za-z0-9_]*):(?:[ \t]*(?P<value>.*?))?[ \t]*$"
)
_CONFIG_SUFFIXES = frozenset({".json", ".yaml", ".yml"})
_SOURCE_PREFIXES = (
    ".codex/",
    ".github/",
    "configs/",
    "docs/",
    "requirements/",
    "scripts/",
    "seungun/",
    "src/",
    "tests/",
)
_ROOT_SOURCE_FILES = frozenset(
    {
        "README.md",
        "pyproject.toml",
        "pytest.ini",
        "requirements.txt",
    }
)


@dataclass(frozen=True, slots=True)
class SourcePin:
    """One path/digest binding found in a tracked config."""

    config_path: PurePosixPath
    location: str
    line_number: int
    source_path: str
    expected_sha256: str


@dataclass(frozen=True, slots=True)
class SourcePinIssue:
    """A source binding that cannot describe the current tracked repository."""

    pin: SourcePin
    kind: str
    detail: str


@dataclass(frozen=True, slots=True)
class HistoricalConfigStatus:
    """Exact, reviewable status for one immutable fail-closed config snapshot."""

    config_path: PurePosixPath
    config_sha256: str
    experiment_id: str
    status: str
    rationale: str
    exempt_pins: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class SourcePinCheckResult:
    """Aggregate result of a repository source-pin check."""

    checked_pins: int
    checked_configs: int
    historical_excluded_pins: int
    historical_excluded_configs: int
    unresolved_repository_pairs: int
    runtime_or_external_pairs: int
    issues: tuple[SourcePinIssue, ...]


# These are development-only, promotion-disabled snapshots.  The complete
# config digest prevents a filename-only exemption from silently widening.
HISTORICAL_CONFIG_STATUSES: tuple[HistoricalConfigStatus, ...] = (
    HistoricalConfigStatus(
        config_path=PurePosixPath(
            "configs/experiments/wavlm_s104_adapter_v1.yaml"
        ),
        config_sha256=(
            "0b633fa93de9ed0e7952893a2221ee5fdaaf1968255bc83c02fd031ae08bde70"
        ),
        experiment_id="aihub71490_wavlm_s104_adapter_v1",
        status="historical_fail_closed_source_snapshot",
        rationale="pre-interval-tiebreak S104 development snapshot",
        exempt_pins=(
            (
                "src/voice_coach/data/intervals.py",
                "0fc28d734951ca300bb208d2b76b89a9207093886853a0ce65adf30705c247cf",
            ),
            (
                "pyproject.toml",
                "c33af395f4f3a3c23b625219d4f0911de2c35b08d6ffecc2710a118766f17328",
            ),
        ),
    ),
    HistoricalConfigStatus(
        config_path=PurePosixPath(
            "configs/experiments/wavlm_s104_adapter_v2_current_code.yaml"
        ),
        config_sha256=(
            "7a78e718954c45eb3ad03658b6da916a5c1163a51d683125daccd6c272be6434"
        ),
        experiment_id="aihub71490_wavlm_s104_adapter_v2_current_code",
        status="historical_fail_closed_source_snapshot",
        rationale="superseded S104 current-code development snapshot",
        exempt_pins=(
            (
                "src/voice_coach/data/intervals.py",
                "0fc28d734951ca300bb208d2b76b89a9207093886853a0ce65adf30705c247cf",
            ),
            (
                "pyproject.toml",
                "c33af395f4f3a3c23b625219d4f0911de2c35b08d6ffecc2710a118766f17328",
            ),
        ),
    ),
    HistoricalConfigStatus(
        config_path=PurePosixPath(
            "configs/experiments/wavlm_s104_adapter_v3_attempt2.yaml"
        ),
        config_sha256=(
            "6e568b6a76a2f58316721332cba34cd844fddb46ab0e142d5bf45545a39ca006"
        ),
        experiment_id="aihub71490_wavlm_s104_adapter_v3_attempt2",
        status="historical_fail_closed_source_snapshot",
        rationale="terminated S104 development attempt 2 snapshot",
        exempt_pins=(
            (
                "pyproject.toml",
                "20ede7c9fbd4eb047fdcd81a2732588756278842b757a1b9b44794befdc4d1da",
            ),
        ),
    ),
    HistoricalConfigStatus(
        config_path=PurePosixPath(
            "configs/experiments/wavlm_s104_adapter_v3_attempt3.yaml"
        ),
        config_sha256=(
            "cb6d9b1ea2871c03d6a966aa663b57bb922350958bf4c07a73ec1647f49d5e5a"
        ),
        experiment_id="aihub71490_wavlm_s104_adapter_v3_attempt3",
        status="historical_fail_closed_source_snapshot",
        rationale="terminated S104 development attempt 3 snapshot",
        exempt_pins=(
            (
                "pyproject.toml",
                "20ede7c9fbd4eb047fdcd81a2732588756278842b757a1b9b44794befdc4d1da",
            ),
        ),
    ),
)

# Exact historical configs remain byte-identical. Their deleted test-only lock
# dependencies must not be restored to satisfy a source pin. Other bindings and
# all existing non-promotion fields remain checked.
_HISTORICAL_SCORER_LOCKS = {'configs/experiments/wavlm_s104_adapter_v1.yaml': 'f6c5b10c1028506d7361c83ad1e9aae0b1fafcf34f4e918e5b7123c51fac8cf2', 'configs/experiments/wavlm_s104_adapter_v2_current_code.yaml': 'f6c5b10c1028506d7361c83ad1e9aae0b1fafcf34f4e918e5b7123c51fac8cf2', 'configs/experiments/wavlm_s104_adapter_v3_attempt2.yaml': 'f6c5b10c1028506d7361c83ad1e9aae0b1fafcf34f4e918e5b7123c51fac8cf2', 'configs/experiments/wavlm_s104_adapter_v3_attempt3.yaml': 'f6c5b10c1028506d7361c83ad1e9aae0b1fafcf34f4e918e5b7123c51fac8cf2'}
HISTORICAL_CONFIG_STATUSES = tuple(
    replace(status, exempt_pins=status.exempt_pins + ((
        "requirements/scorer-lock-linux-aarch64-cu130.txt",
        _HISTORICAL_SCORER_LOCKS[status.config_path.as_posix()],
    ),)) for status in HISTORICAL_CONFIG_STATUSES
)


@dataclass(frozen=True, slots=True)
class _YamlEntry:
    line_number: int
    indent: int
    starts_sequence_item: bool
    key: str
    value: str


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strip_yaml_comment(value: str) -> str:
    """Strip an unquoted YAML comment from a scalar used by repository configs."""

    quote: str | None = None
    escaped = False
    for index, character in enumerate(value):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote == '"':
            escaped = True
            continue
        if quote is not None:
            if character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
            continue
        if character == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value.rstrip()


def _yaml_scalar(value: str) -> str:
    scalar = _strip_yaml_comment(value).strip()
    if len(scalar) >= 2 and scalar[0] == scalar[-1] and scalar[0] in {"'", '"'}:
        return scalar[1:-1]
    return scalar


def _yaml_entries(text: str) -> tuple[_YamlEntry, ...]:
    entries: list[_YamlEntry] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        match = _YAML_KEY_VALUE_PATTERN.match(raw_line)
        if match is None:
            continue
        starts_sequence = match.group("sequence") is not None
        entries.append(
            _YamlEntry(
                line_number=line_number,
                indent=len(match.group("spaces")) + (2 if starts_sequence else 0),
                starts_sequence_item=starts_sequence,
                key=match.group("key"),
                value=_yaml_scalar(match.group("value") or ""),
            )
        )
    return tuple(entries)


def _find_yaml_companion(
    entries: Sequence[_YamlEntry],
    start_index: int,
    companion_key: str,
) -> _YamlEntry | None:
    start = entries[start_index]
    for candidate in entries[start_index + 1 :]:
        if candidate.indent < start.indent:
            return None
        if candidate.indent == start.indent and candidate.starts_sequence_item:
            return None
        if candidate.indent == start.indent and candidate.key == companion_key:
            return candidate
    return None


def _yaml_source_pins(
    config_path: PurePosixPath,
    text: str,
) -> tuple[SourcePin, ...]:
    entries = _yaml_entries(text)
    pins: list[SourcePin] = []
    for index, entry in enumerate(entries):
        if not entry.value:
            continue
        if entry.key == "path":
            companion_key = "sha256"
        elif entry.key.endswith("_path"):
            companion_key = f"{entry.key[:-5]}_sha256"
        else:
            continue
        companion = _find_yaml_companion(entries, index, companion_key)
        if companion is None or not companion.value:
            continue
        pins.append(
            SourcePin(
                config_path=config_path,
                location=entry.key,
                line_number=entry.line_number,
                source_path=entry.value,
                expected_sha256=companion.value,
            )
        )
    return tuple(pins)


def _json_line_number(text: str, source_path: str) -> int:
    encoded = json.dumps(source_path, ensure_ascii=False)
    offset = text.find(encoded)
    return text.count("\n", 0, offset) + 1 if offset >= 0 else 1


def _json_source_pins(
    config_path: PurePosixPath,
    text: str,
) -> tuple[SourcePin, ...]:
    document = json.loads(text)
    pins: list[SourcePin] = []

    def visit(value: Any, location: tuple[str, ...]) -> None:
        if isinstance(value, Mapping):
            path_value = value.get("path")
            sha_value = value.get("sha256")
            if isinstance(path_value, str) and isinstance(sha_value, str):
                pins.append(
                    SourcePin(
                        config_path=config_path,
                        location=".".join((*location, "path")),
                        line_number=_json_line_number(text, path_value),
                        source_path=path_value,
                        expected_sha256=sha_value,
                    )
                )
            for key, child in value.items():
                if key.endswith("_path") and isinstance(child, str):
                    companion = value.get(f"{key[:-5]}_sha256")
                    if isinstance(companion, str):
                        pins.append(
                            SourcePin(
                                config_path=config_path,
                                location=".".join((*location, key)),
                                line_number=_json_line_number(text, child),
                                source_path=child,
                                expected_sha256=companion,
                            )
                        )
                visit(child, (*location, str(key)))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, (*location, str(index)))

    visit(document, ())
    return tuple(pins)


def extract_source_pins(
    config_path: PurePosixPath,
    text: str,
) -> tuple[SourcePin, ...]:
    """Extract path/digest pairs without requiring the optional PyYAML package."""

    if config_path.suffix == ".json":
        return _json_source_pins(config_path, text)
    return _yaml_source_pins(config_path, text)


def _yaml_experiment_fields(text: str) -> dict[str, str]:
    entries = _yaml_entries(text)
    experiment_index = next(
        (
            index
            for index, entry in enumerate(entries)
            if entry.indent == 0 and entry.key == "experiment" and not entry.value
        ),
        None,
    )
    if experiment_index is None:
        return {}
    fields: dict[str, str] = {}
    for entry in entries[experiment_index + 1 :]:
        if entry.indent == 0:
            break
        if entry.indent == 2 and entry.value:
            fields[entry.key] = entry.value
    return fields


def _historical_status_error(
    repository_root: Path,
    status: HistoricalConfigStatus,
    text: str,
) -> str | None:
    config_file = repository_root / status.config_path
    actual_digest = _sha256_file(config_file)
    if actual_digest != status.config_sha256:
        return (
            "historical status byte digest differs "
            f"(expected {status.config_sha256}, actual {actual_digest})"
        )
    fields = _yaml_experiment_fields(text)
    required = {
        "id": status.experiment_id,
        "stage": "s104_adapter",
        "development_only": "true",
        "oracle_alignment": "true",
        "silver_label": "true",
        "promotion_decision_allowed": "false",
    }
    mismatches = [
        f"{key}={fields.get(key)!r} (expected {expected!r})"
        for key, expected in required.items()
        if fields.get(key) != expected
    ]
    if mismatches:
        return "historical status safety fields differ: " + ", ".join(mismatches)
    if status.status != "historical_fail_closed_source_snapshot":
        return f"unrecognized historical status {status.status!r}"
    return None


def _normalized_repository_path(value: str) -> PurePosixPath | None:
    if not value or value.startswith(("/", "~")) or "://" in value:
        return None
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    return candidate


def _looks_like_repository_source(path: PurePosixPath) -> bool:
    value = path.as_posix()
    return value in _ROOT_SOURCE_FILES or value.startswith(_SOURCE_PREFIXES)


def check_source_pins(
    repository_root: Path,
    config_files: Iterable[PurePosixPath],
    tracked_files: Collection[PurePosixPath],
    *,
    historical_statuses: Iterable[HistoricalConfigStatus] = (),
) -> SourcePinCheckResult:
    """Check tracked repository-current pins and classify non-repository pairs."""

    root = repository_root.resolve()
    tracked = frozenset(tracked_files)
    status_by_path = {status.config_path: status for status in historical_statuses}
    checked_pins = 0
    checked_configs: set[PurePosixPath] = set()
    excluded_pins = 0
    excluded_configs: set[PurePosixPath] = set()
    unresolved_pairs = 0
    runtime_pairs = 0
    issues: list[SourcePinIssue] = []

    for config_path in sorted(config_files):
        config_file = root / config_path
        text = config_file.read_text(encoding="utf-8")
        pins = extract_source_pins(config_path, text)
        status = status_by_path.get(config_path)
        exempt_pins: frozenset[tuple[str, str]] = frozenset()
        if status is not None:
            error = _historical_status_error(root, status, text)
            if error is None:
                present_pins = frozenset(
                    (pin.source_path, pin.expected_sha256) for pin in pins
                )
                absent = frozenset(status.exempt_pins) - present_pins
                if absent:
                    error = (
                        "historical status exempt pins are absent: "
                        + ", ".join(path for path, _ in sorted(absent))
                    )
                else:
                    exempt_pins = frozenset(status.exempt_pins)
            if error is not None:
                synthetic_pin = SourcePin(
                    config_path=config_path,
                    location="historical_status",
                    line_number=1,
                    source_path=config_path.as_posix(),
                    expected_sha256=status.config_sha256,
                )
                issues.append(
                    SourcePinIssue(
                        pin=synthetic_pin,
                        kind="historical_status_invalid",
                        detail=error,
                    )
                )

        for pin in pins:
            if (pin.source_path, pin.expected_sha256) in exempt_pins:
                excluded_pins += 1
                excluded_configs.add(config_path)
                continue
            source_path = _normalized_repository_path(pin.source_path)
            if source_path is None:
                runtime_pairs += 1
                continue
            if source_path not in tracked:
                if not _looks_like_repository_source(source_path):
                    runtime_pairs += 1
                    continue
                source_file = root / source_path
                kind = "untracked_source" if source_file.exists() else "missing_source"
                issues.append(
                    SourcePinIssue(
                        pin=pin,
                        kind=kind,
                        detail=(
                            "repository source is not present in the Git index; "
                            "a local or ignored file cannot satisfy this pin"
                        ),
                    )
                )
                continue

            if not _SHA256_PATTERN.fullmatch(pin.expected_sha256):
                if _UNRESOLVED_DIGEST_PATTERN.fullmatch(pin.expected_sha256):
                    unresolved_pairs += 1
                    continue
                issues.append(
                    SourcePinIssue(
                        pin=pin,
                        kind="invalid_digest",
                        detail="expected digest is not a lowercase SHA-256 value",
                    )
                )
                continue
            checked_pins += 1
            checked_configs.add(config_path)
            source_file = root / source_path
            if not source_file.is_file() or source_file.is_symlink():
                issues.append(
                    SourcePinIssue(
                        pin=pin,
                        kind="missing_source",
                        detail="Git-tracked source is absent or is not a regular file",
                    )
                )
                continue
            actual_digest = _sha256_file(source_file)
            if actual_digest != pin.expected_sha256:
                issues.append(
                    SourcePinIssue(
                        pin=pin,
                        kind="digest_drift",
                        detail=f"actual SHA-256 is {actual_digest}",
                    )
                )

    return SourcePinCheckResult(
        checked_pins=checked_pins,
        checked_configs=len(checked_configs),
        historical_excluded_pins=excluded_pins,
        historical_excluded_configs=len(excluded_configs),
        unresolved_repository_pairs=unresolved_pairs,
        runtime_or_external_pairs=runtime_pairs,
        issues=tuple(issues),
    )


def _git_tracked_files(repository_root: Path) -> frozenset[PurePosixPath]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repository_root,
        check=True,
        capture_output=True,
    )
    return frozenset(
        PurePosixPath(raw_path.decode("utf-8"))
        for raw_path in completed.stdout.split(b"\0")
        if raw_path
    )


def _tracked_config_files(
    tracked_files: Collection[PurePosixPath],
) -> tuple[PurePosixPath, ...]:
    return tuple(
        sorted(
            path
            for path in tracked_files
            if path.parts
            and path.parts[0] == "configs"
            and path.suffix in _CONFIG_SUFFIXES
        )
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="check repository-current source pins in tracked configs"
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Git worktree root (default: inferred from this script)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    repository_root = args.repository_root.resolve()
    tracked_files = _git_tracked_files(repository_root)
    config_files = tuple(
        path
        for path in _tracked_config_files(tracked_files)
        if (repository_root / path).is_file()
    )
    result = check_source_pins(
        repository_root,
        config_files,
        tracked_files,
        historical_statuses=HISTORICAL_CONFIG_STATUSES,
    )

    for issue in result.issues:
        print(
            f"{issue.pin.config_path}:{issue.pin.line_number}: "
            f"{issue.kind}: {issue.pin.source_path!r}: {issue.detail}",
            file=sys.stderr,
        )
    print(
        "source-pin summary: "
        f"{result.checked_pins} repository-current pins checked across "
        f"{result.checked_configs} configs; "
        f"{result.historical_excluded_pins} pins in "
        f"{result.historical_excluded_configs} exact historical configs excluded; "
        f"{result.unresolved_repository_pairs} explicit unresolved repository pairs "
        "not counted; "
        f"{result.runtime_or_external_pairs} runtime/external pairs not counted"
    )
    if result.issues:
        print(f"source-pin failures: {len(result.issues)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
