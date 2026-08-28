"""Tests for the Git-tracked repository source-pin checker."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path, PurePosixPath

from scripts.check_source_pins import (
    HistoricalConfigStatus,
    check_source_pins,
    extract_source_pins,
    main,
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def test_yaml_extractor_supports_path_and_named_path_pairs() -> None:
    pins = extract_source_pins(
        PurePosixPath("configs/experiment.yaml"),
        """
canonical_sources:
  pins:
    - path: src/current.py
      scope: current
      sha256: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
delegate:
  contract_path: src/contract.py
  contract_sha256: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
runtime:
  commit_path: artifacts/run/commit.json
  commit_sha256: TBD_COMPLETION
""",
    )

    assert [(pin.source_path, pin.expected_sha256) for pin in pins] == [
        ("src/current.py", "a" * 64),
        ("src/contract.py", "b" * 64),
        ("artifacts/run/commit.json", "TBD_COMPLETION"),
    ]


def test_json_extractor_supports_nested_path_pairs() -> None:
    pins = extract_source_pins(
        PurePosixPath("configs/experiment.json"),
        """{
  "source": {"path": "src/current.py", "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
  "delegate_path": "src/delegate.py",
  "delegate_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
}""",
    )

    assert [(pin.source_path, pin.expected_sha256) for pin in pins] == [
        ("src/current.py", "a" * 64),
        ("src/delegate.py", "b" * 64),
    ]


def test_check_validates_only_git_tracked_repository_sources(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src" / "current.py"
    source.parent.mkdir()
    source.write_bytes(b"tracked source\n")
    runtime = tmp_path / "artifacts" / "run" / "commit.json"
    runtime.parent.mkdir(parents=True)
    runtime.write_bytes(b"local runtime evidence\n")
    config = tmp_path / "configs" / "experiment.yaml"
    config.parent.mkdir()
    config.write_text(
        f"""canonical_sources:
  - path: src/current.py
    sha256: {_sha256(source.read_bytes())}
runtime:
  commit_path: artifacts/run/commit.json
  commit_sha256: {_sha256(runtime.read_bytes())}
""",
        encoding="utf-8",
    )

    result = check_source_pins(
        tmp_path,
        (PurePosixPath("configs/experiment.yaml"),),
        frozenset(
            {
                PurePosixPath("configs/experiment.yaml"),
                PurePosixPath("src/current.py"),
            }
        ),
    )

    assert result.checked_pins == 1
    assert result.unresolved_repository_pairs == 0
    assert result.runtime_or_external_pairs == 1
    assert result.issues == ()


def test_check_reports_drift_and_untracked_repository_source(
    tmp_path: Path,
) -> None:
    tracked_source = tmp_path / "src" / "tracked.py"
    tracked_source.parent.mkdir()
    tracked_source.write_bytes(b"changed\n")
    untracked_source = tmp_path / "scripts" / "local_only.py"
    untracked_source.parent.mkdir()
    untracked_source.write_bytes(b"local only\n")
    config = tmp_path / "configs" / "experiment.yaml"
    config.parent.mkdir()
    config.write_text(
        f"""sources:
  - path: src/tracked.py
    sha256: {'0' * 64}
  - path: scripts/local_only.py
    sha256: {_sha256(untracked_source.read_bytes())}
""",
        encoding="utf-8",
    )

    result = check_source_pins(
        tmp_path,
        (PurePosixPath("configs/experiment.yaml"),),
        frozenset(
            {
                PurePosixPath("configs/experiment.yaml"),
                PurePosixPath("src/tracked.py"),
            }
        ),
    )

    assert [issue.kind for issue in result.issues] == [
        "digest_drift",
        "untracked_source",
    ]


def test_exact_fail_closed_historical_status_excludes_stale_snapshot(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src" / "current.py"
    source.parent.mkdir()
    source.write_bytes(b"new source\n")
    config_path = PurePosixPath("configs/historical.yaml")
    config = tmp_path / config_path
    config.parent.mkdir()
    config.write_text(
        f"""experiment:
  id: historical-s104
  stage: s104_adapter
  development_only: true
  oracle_alignment: true
  silver_label: true
  promotion_decision_allowed: false
implementation:
  source:
    path: src/current.py
    sha256: {'0' * 64}
""",
        encoding="utf-8",
    )
    status = HistoricalConfigStatus(
        config_path=config_path,
        config_sha256=_sha256(config.read_bytes()),
        experiment_id="historical-s104",
        status="historical_fail_closed_source_snapshot",
        rationale="test snapshot",
        exempt_pins=(("src/current.py", "0" * 64),),
    )

    result = check_source_pins(
        tmp_path,
        (config_path,),
        frozenset({config_path, PurePosixPath("src/current.py")}),
        historical_statuses=(status,),
    )

    assert result.checked_pins == 0
    assert result.historical_excluded_configs == 1
    assert result.historical_excluded_pins == 1
    assert result.issues == ()


def test_historical_status_is_invalidated_by_any_config_change(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src" / "current.py"
    source.parent.mkdir()
    source.write_bytes(b"current\n")
    config_path = PurePosixPath("configs/historical.yaml")
    config = tmp_path / config_path
    config.parent.mkdir()
    original = f"""experiment:
  id: historical-s104
  stage: s104_adapter
  development_only: true
  oracle_alignment: true
  silver_label: true
  promotion_decision_allowed: false
source:
  path: src/current.py
  sha256: {_sha256(source.read_bytes())}
"""
    config.write_text(original, encoding="utf-8")
    status = HistoricalConfigStatus(
        config_path=config_path,
        config_sha256=_sha256(config.read_bytes()),
        experiment_id="historical-s104",
        status="historical_fail_closed_source_snapshot",
        rationale="test snapshot",
        exempt_pins=(("src/current.py", _sha256(source.read_bytes())),),
    )
    config.write_text(original + "# changed\n", encoding="utf-8")

    result = check_source_pins(
        tmp_path,
        (config_path,),
        frozenset({config_path, PurePosixPath("src/current.py")}),
        historical_statuses=(status,),
    )

    assert result.historical_excluded_configs == 0
    assert result.checked_pins == 1
    assert [issue.kind for issue in result.issues] == [
        "historical_status_invalid"
    ]


def test_explicit_tbd_repository_pair_is_unresolved_not_success(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src" / "current.py"
    source.parent.mkdir()
    source.write_bytes(b"tracked source\n")
    config = tmp_path / "configs" / "experiment.yaml"
    config.parent.mkdir()
    config.write_text(
        """source:
  path: src/current.py
  sha256: TBD_PREREGISTRATION
""",
        encoding="utf-8",
    )

    result = check_source_pins(
        tmp_path,
        (PurePosixPath("configs/experiment.yaml"),),
        frozenset(
            {
                PurePosixPath("configs/experiment.yaml"),
                PurePosixPath("src/current.py"),
            }
        ),
    )

    assert result.checked_pins == 0
    assert result.unresolved_repository_pairs == 1
    assert result.issues == ()


def test_cli_returns_nonzero_for_missing_tracked_source(tmp_path: Path) -> None:
    config = tmp_path / "configs" / "experiment.yaml"
    config.parent.mkdir()
    config.write_text(
        f"""source:
  path: src/missing.py
  sha256: {'0' * 64}
""",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "add", "configs/experiment.yaml"], cwd=tmp_path, check=True
    )

    exit_code = main(["--repository-root", str(tmp_path)])

    assert exit_code == 1
