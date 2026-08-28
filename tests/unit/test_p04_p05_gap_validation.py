from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import threading
import time
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

from voice_coach.reproducibility import p04_p05_gap_validation as gap
from voice_coach.reproducibility.p04_p05_gap_validation import (
    EXPECTED_AUDIT_STATUS,
    EXPECTED_FINDING_CODES,
    EXPECTED_RESOLUTION_CODES,
    GapValidationError,
    run_gap_audit,
    validate_committed_gap_audit,
    validate_config_only,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG_RELATIVE = Path("configs/experiments/p04_p05_gap_validation_v1.yaml")
ACTIVE_BLOCKED_CONTRACT_TESTS = {
    "test_config_only_does_not_resolve_declared_sources_or_write_output",
    "test_config_only_rejects_env_variant_without_reading_it",
    "test_blocked_audit_fails_before_source_or_output_resolution",
}


@pytest.fixture(autouse=True)
def _retired_contract_scope(request: pytest.FixtureRequest) -> None:
    if request.node.name not in ACTIVE_BLOCKED_CONTRACT_TESTS:
        pytest.skip("legacy ready-state fixture retired with its canonical sources")


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _load_config(path: Path) -> dict[str, object]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _write_config(path: Path, config: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            config,
            allow_unicode=True,
            sort_keys=False,
            width=1000,
        ),
        encoding="utf-8",
    )


def _record_path(config: dict[str, object], group: str, name: str) -> Path:
    records = config[group]
    assert isinstance(records, dict)
    record = records[name]
    assert isinstance(record, dict)
    value = record["path"]
    assert isinstance(value, str)
    return Path(value)


def _repin(
    workspace: Path,
    config: dict[str, object],
    group: str,
    name: str,
) -> None:
    relative = _record_path(config, group, name)
    content = (workspace / relative).read_bytes()
    records = config[group]
    assert isinstance(records, dict)
    record = records[name]
    assert isinstance(record, dict)
    record["sha256"] = _sha(content)
    record["size_bytes"] = len(content)


def _workspace(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    workspace = tmp_path / "workspace"
    source_config = _load_config(ROOT / CONFIG_RELATIVE)
    for group in ("sources", "implementation_sources"):
        records = source_config[group]
        assert isinstance(records, dict)
        for name in records:
            relative = _record_path(source_config, group, str(name))
            destination = workspace / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
            _repin(workspace, source_config, group, str(name))
    config_path = workspace / CONFIG_RELATIVE
    _write_config(config_path, source_config)
    return workspace, config_path, source_config


def _mutate_text(
    workspace: Path,
    config_path: Path,
    config: dict[str, object],
    name: str,
    old: str,
    new: str,
) -> None:
    relative = _record_path(config, "sources", name)
    path = workspace / relative
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    _repin(workspace, config, "sources", name)
    _write_config(config_path, config)


def _assert_error(code: str, action: object) -> None:
    assert callable(action)
    with pytest.raises(GapValidationError, match=f"^{code}$") as captured:
        action()
    assert captured.value.code == code


def test_config_only_does_not_resolve_declared_sources_or_write_output(
    tmp_path: Path,
) -> None:
    config = _load_config(ROOT / CONFIG_RELATIVE)
    workspace = tmp_path / "workspace"
    config_path = workspace / CONFIG_RELATIVE
    _write_config(config_path, config)

    result = validate_config_only(
        config_path.relative_to(workspace),
        workspace_root=workspace,
    )

    assert result["status"] == gap.BLOCKED_CONFIG_STATUS
    assert result["blocker"] == gap.BLOCKER_CODE
    assert result["declared_source_count"] == 4
    assert result["declared_implementation_source_count"] == 0
    assert result["access"] == {
        "canonical_files_read": 0,
        "aggregate_sources_read": 0,
        "locked_test_files_read": 0,
        "raw_or_restricted_files_read": 0,
        "external_human_records_read": 0,
        "outputs_written": 0,
    }
    assert not (workspace / "artifacts").exists()


def test_config_only_rejects_env_variant_without_reading_it(tmp_path: Path) -> None:
    config = _load_config(ROOT / CONFIG_RELATIVE)
    boundaries = config["current_boundaries"]
    assert isinstance(boundaries, dict)
    data_boundary = boundaries["data_boundary"]
    assert isinstance(data_boundary, dict)
    data_boundary["path"] = ".env.local"
    workspace = tmp_path / "workspace"
    config_path = workspace / CONFIG_RELATIVE
    _write_config(config_path, config)
    (workspace / ".env.local").write_text("must-not-be-read", encoding="utf-8")

    _assert_error(
        "current_boundary_data_boundary_forbidden_path",
        lambda: validate_config_only(
            config_path.relative_to(workspace),
            workspace_root=workspace,
        ),
    )
    assert (workspace / ".env.local").read_text(encoding="utf-8") == "must-not-be-read"


def test_blocked_audit_fails_before_source_or_output_resolution(tmp_path: Path) -> None:
    config = _load_config(ROOT / CONFIG_RELATIVE)
    workspace = tmp_path / "workspace"
    config_path = workspace / CONFIG_RELATIVE
    _write_config(config_path, config)

    _assert_error(
        gap.BLOCKER_CODE,
        lambda: run_gap_audit(
            config_path=config_path.relative_to(workspace),
            workspace_root=workspace,
        ),
    )
    assert not (workspace / "artifacts").exists()


def test_current_fixture_publishes_expected_pending_status_and_deep_validates(
    tmp_path: Path,
) -> None:
    workspace, config_path, _ = _workspace(tmp_path)

    result = run_gap_audit(
        config_path=config_path.relative_to(workspace),
        workspace_root=workspace,
    )
    deep = validate_committed_gap_audit(
        commit_path=result["commit"],
        workspace_root=workspace,
    )
    report = json.loads((workspace / str(result["report"])).read_text())
    repeated = run_gap_audit(
        config_path=config_path.relative_to(workspace),
        workspace_root=workspace,
    )
    receipt_path = workspace / str(result["receipt"])
    report_path = workspace / str(result["report"])
    commit_path = workspace / str(result["commit"])
    generation_path = receipt_path.parent
    lock_path = workspace / gap.DEFAULT_OUTPUT_ROOT / gap.PUBLICATION_LOCK_NAME
    namespace_lock_path = (
        workspace / Path(gap.DEFAULT_OUTPUT_ROOT).parent / gap.NAMESPACE_LOCK_NAME
    )

    assert result["status"] == EXPECTED_AUDIT_STATUS
    assert deep["status"] == "valid"
    assert repeated["generation_id"] == result["generation_id"]
    assert sorted(item.name for item in generation_path.iterdir()) == [
        gap.RECEIPT_FILE_NAME,
        gap.REPORT_FILE_NAME,
    ]
    assert stat.S_IMODE(generation_path.lstat().st_mode) == gap.GENERATION_MODE
    assert generation_path.lstat().st_nlink == gap.GENERATION_NLINK
    for published in (receipt_path, report_path):
        assert stat.S_IMODE(published.lstat().st_mode) == gap.PUBLISHED_FILE_MODE
        assert published.lstat().st_nlink == 1
    assert stat.S_IMODE(commit_path.lstat().st_mode) == gap.COMMIT_MODE
    assert commit_path.lstat().st_nlink == 1
    for published_lock in (namespace_lock_path, lock_path):
        assert stat.S_IMODE(published_lock.lstat().st_mode) == gap.LOCK_MODE
        assert published_lock.lstat().st_nlink == 1
    assert report["publication_namespace"] == {
        "commit_name": gap.COMMIT_FILE_NAME,
        "config_path": gap.CANONICAL_CONFIG_PATH,
        "config_sha256": _sha(config_path.read_bytes()),
        "generations_name": "generations",
        "output_root": gap.DEFAULT_OUTPUT_ROOT,
        "receipt_name": gap.RECEIPT_FILE_NAME,
        "report_name": gap.REPORT_FILE_NAME,
    }
    assert tuple(result["semantic_finding_codes"]) == EXPECTED_FINDING_CODES
    assert tuple(result["semantic_resolution_codes"]) == EXPECTED_RESOLUTION_CODES
    assert report["integrity"]["gap_rows"]["status_counts"] == {
        "A": 33,
        "B": 0,
        "C": 7,
    }
    assert report["integrity"]["pending"]["marker_count"] == 18
    assert report["integrity"]["locked_test_guard"]["true_count"] == 0
    assert report["access"] == {
        "aggregate_source_files_read": 14,
        "aggregate_only": True,
        "raw_or_restricted_files_read": 0,
        "locked_test_files_read": 0,
        "external_human_records_read": 0,
        "canonical_files_written": 0,
    }
    assert len(report["semantic_findings"]) == 1
    assert report["semantic_resolutions"][0]["canonical_c_count"] == 7
    assert report["semantic_resolutions"][0]["claim_gap_c_count"] == 7
    assert report["semantic_resolutions"][0]["forbidden_stale_anchor_mentions"] == 0
    assert report["semantic_resolutions"][1]["ledger"]["path"] == (
        "docs/constraints/DATA_LICENSE_SOURCE_LEDGER_KO.md"
    )
    assert report["semantic_resolutions"][1]["evidence_alias"] == (
        "data_license_source_ledger"
    )
    assert report["integrity"]["evidence_validation"]["artifact_count"] ==  35
    assert report["integrity"]["evidence_validation"]["artifact_passed"] ==  35
    assert report["completion_claims"]["p04_complete"] is False
    assert report["completion_claims"]["p05_complete"] is False
    assert report["completion_claims"]["manuscript_semantic_stale_resolved"] is False


def test_gap_row_count_mutation_fails_closed(tmp_path: Path) -> None:
    workspace, config_path, config = _workspace(tmp_path)
    methods = workspace / _record_path(config, "sources", "methods_gap")
    text = methods.read_text(encoding="utf-8")
    line = next(item for item in text.splitlines() if item.startswith("| M01 |"))
    methods.write_text(text.replace(line + "\n", "", 1), encoding="utf-8")
    _repin(workspace, config, "sources", "methods_gap")
    _write_config(config_path, config)

    _assert_error(
        "gap_row_count",
        lambda: run_gap_audit(
            config_path=config_path.relative_to(workspace),
            workspace_root=workspace,
        ),
    )


def test_declared_hash_mutation_fails_before_source_use(tmp_path: Path) -> None:
    workspace, config_path, config = _workspace(tmp_path)
    sources = config["sources"]
    assert isinstance(sources, dict)
    manuscript = sources["manuscript"]
    assert isinstance(manuscript, dict)
    manuscript["sha256"] = "0" * 64
    _write_config(config_path, config)

    _assert_error(
        "source_manuscript_hash_mismatch",
        lambda: run_gap_audit(
            config_path=config_path.relative_to(workspace),
            workspace_root=workspace,
        ),
    )


def test_pending_marker_mutation_fails_closed(tmp_path: Path) -> None:
    workspace, config_path, config = _workspace(tmp_path)
    _mutate_text(
        workspace,
        config_path,
        config,
        "manuscript",
        "[PENDING: G04]",
        "[PENDING: G04 MUTATED]",
    )

    _assert_error(
        "pending_contract",
        lambda: run_gap_audit(
            config_path=config_path.relative_to(workspace),
            workspace_root=workspace,
        ),
    )


def test_locked_access_true_mutation_fails_closed(tmp_path: Path) -> None:
    workspace, config_path, config = _workspace(tmp_path)
    relative = _record_path(config, "sources", "validation_report")
    report_path = workspace / relative
    report = json.loads(report_path.read_text())
    accessed = report["locked_test_guard"]["accessed"]
    accessed[next(iter(accessed))] = True
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    _repin(workspace, config, "sources", "validation_report")
    _write_config(config_path, config)

    _assert_error(
        "locked_guard",
        lambda: run_gap_audit(
            config_path=config_path.relative_to(workspace),
            workspace_root=workspace,
        ),
    )


def test_external_false_completion_mutation_fails_closed(tmp_path: Path) -> None:
    workspace, config_path, config = _workspace(tmp_path)
    _mutate_text(
        workspace,
        config_path,
        config,
        "tracker",
        "| G04 | 실패(1차 감사 완료·95% 미달) | 240건 수동 정렬 정확도≥95% |",
        "| G04 | 완료 | 240건 수동 정렬 정확도≥95% |",
    )

    _assert_error(
        "external_pending_anchor_tracker_0",
        lambda: run_gap_audit(
            config_path=config_path.relative_to(workspace),
            workspace_root=workspace,
        ),
    )


def test_b07_omission_rule_drift_fails_closed(tmp_path: Path) -> None:
    workspace, config_path, config = _workspace(tmp_path)
    semantic = config["semantic_contract"]
    assert isinstance(semantic, dict)
    b07 = semantic["b07"]
    assert isinstance(b07, dict)
    b07["required_absent_token"] = "S103"
    _write_config(config_path, config)

    _assert_error(
        "b07_omission_rule_drift",
        lambda: run_gap_audit(
            config_path=config_path.relative_to(workspace),
            workspace_root=workspace,
        ),
    )


def test_b07_commit_aggregate_record_drift_fails_closed(tmp_path: Path) -> None:
    workspace, config_path, config = _workspace(tmp_path)
    relative = _record_path(config, "sources", "b07_commit")
    commit_path = workspace / relative
    commit = json.loads(commit_path.read_text(encoding="utf-8"))
    records = commit["generation"]["records"]
    aggregate = next(
        record for record in records if record["path"] == "aggregate_report.json"
    )
    aggregate["sha256"] = "0" * 64
    commit_path.write_text(
        json.dumps(commit, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    _repin(workspace, config, "sources", "b07_commit")
    _write_config(config_path, config)

    _assert_error(
        "b07_commit_contract",
        lambda: run_gap_audit(
            config_path=config_path.relative_to(workspace),
            workspace_root=workspace,
        ),
    )


def test_resolved_c_count_regression_to_stale_nine_fails_closed(tmp_path: Path) -> None:
    workspace, config_path, config = _workspace(tmp_path)
    _mutate_text(
        workspace,
        config_path,
        config,
        "claim_gap",
        "후속·외부 artifact가 필요한 P04 C 등급 7개 갱신",
        "후속·외부 artifact가 필요한 P04 C 등급 9개 갱신",
    )

    _assert_error(
        "claim_gap_current_anchor",
        lambda: run_gap_audit(
            config_path=config_path.relative_to(workspace),
            workspace_root=workspace,
        ),
    )


def test_license_evidence_alias_record_drift_fails_closed(tmp_path: Path) -> None:
    workspace, config_path, config = _workspace(tmp_path)
    semantic = config["semantic_contract"]
    assert isinstance(semantic, dict)
    license_contract = semantic["license"]
    assert isinstance(license_contract, dict)
    license_contract["evidence_alias"] = "paper_aihub71490_data_qc_table"
    _write_config(config_path, config)

    _assert_error(
        "license_evidence_record",
        lambda: run_gap_audit(
            config_path=config_path.relative_to(workspace),
            workspace_root=workspace,
        ),
    )


def test_license_manuscript_path_mention_drift_fails_closed(tmp_path: Path) -> None:
    workspace, config_path, _ = _workspace(tmp_path)
    root, _, config = gap._load_canonical_config(
        config_path=config_path.relative_to(workspace),
        workspace_root=workspace,
    )
    sources, _ = gap._load_declared_sources(root=root, config=config)
    texts = gap._text_sources(sources)
    evidence_map = gap._load_yaml_bytes(
        sources["evidence_map"].content,
        "evidence_map_parse",
    )
    validation_report = gap._load_json_bytes(
        sources["validation_report"].content,
        "validation_report_parse",
    )
    integrity = gap._validate_gap_integrity(
        config=config,
        texts=texts,
        evidence_map=evidence_map,
        validation_report=validation_report,
        source_records={
            name: gap._record(root, source) for name, source in sources.items()
        },
    )
    ledger_path = "docs/constraints/DATA_LICENSE_SOURCE_LEDGER_KO.md"
    assert texts["manuscript"].count(ledger_path) == 1
    mutated_texts = {
        **texts,
        "manuscript": texts["manuscript"].replace(
            ledger_path,
            "docs/constraints/DATA_LICENSE_SOURCE_LEDGER_KO.missing",
            1,
        ),
    }

    _assert_error(
        "license_mapping_mentions",
        lambda: gap._semantic_findings(
            root=root,
            config=config,
            sources=sources,
            texts=mutated_texts,
            evidence_map=evidence_map,
            gap_integrity=integrity,
        ),
    )


def test_published_report_tamper_is_rejected(tmp_path: Path) -> None:
    workspace, config_path, _ = _workspace(tmp_path)
    result = run_gap_audit(
        config_path=config_path.relative_to(workspace),
        workspace_root=workspace,
    )
    report_path = workspace / str(result["report"])
    report_path.chmod(0o640)
    report_path.write_bytes(report_path.read_bytes() + b" ")
    report_path.chmod(gap.PUBLISHED_FILE_MODE)

    _assert_error(
        "published_report_mismatch",
        lambda: validate_committed_gap_audit(
            commit_path=result["commit"],
            workspace_root=workspace,
        ),
    )


def test_duplicate_commit_json_key_is_rejected(tmp_path: Path) -> None:
    workspace, config_path, _ = _workspace(tmp_path)
    result = run_gap_audit(
        config_path=config_path.relative_to(workspace),
        workspace_root=workspace,
    )
    commit_path = workspace / str(result["commit"])
    original = commit_path.read_text(encoding="utf-8")
    schema_line = '  "schema_version": "voice_coach.p04_p05_gap_validation_commit.v1",'
    assert original.count(schema_line) == 1
    commit_path.chmod(0o640)
    commit_path.write_text(
        original.replace(schema_line, f"{schema_line}\n{schema_line}", 1),
        encoding="utf-8",
    )
    commit_path.chmod(gap.COMMIT_MODE)

    _assert_error(
        "commit_parse_duplicate_key",
        lambda: validate_committed_gap_audit(
            commit_path=result["commit"],
            workspace_root=workspace,
        ),
    )


def test_alternate_config_path_is_rejected_without_output(tmp_path: Path) -> None:
    workspace, config_path, _ = _workspace(tmp_path)
    alternate = workspace / "configs/experiments/alternate-p04-p05.yaml"
    alternate.write_bytes(config_path.read_bytes())

    _assert_error(
        "config_namespace",
        lambda: validate_config_only(
            alternate.relative_to(workspace),
            workspace_root=workspace,
        ),
    )
    assert not (workspace / gap.DEFAULT_OUTPUT_ROOT).exists()


def test_exclusive_lock_is_held_through_post_publish_deep_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path, _ = _workspace(tmp_path)
    original = gap._validate_committed_gap_audit_loaded
    observed = False

    def assert_locked(
        *,
        root: Path,
        config_file: gap.LoadedSource,
        config: Mapping[str, Any],
        commit_path: str | Path,
        boundary: gap.PublicationBoundary,
    ) -> Mapping[str, Any]:
        nonlocal observed
        descriptor = os.open(
            root / gap.DEFAULT_OUTPUT_ROOT / gap.PUBLICATION_LOCK_NAME,
            os.O_RDWR,
        )
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(
                    descriptor,
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
        finally:
            os.close(descriptor)
        observed = True
        return original(
            root=root,
            config_file=config_file,
            config=config,
            commit_path=commit_path,
            boundary=boundary,
        )

    monkeypatch.setattr(
        gap,
        "_validate_committed_gap_audit_loaded",
        assert_locked,
    )
    result = run_gap_audit(
        config_path=config_path.relative_to(workspace),
        workspace_root=workspace,
    )

    assert result["deep_validation"] == "valid"
    assert observed is True


def test_concurrent_publishers_are_serialized_by_single_writer_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path, _ = _workspace(tmp_path)
    original = gap._build_report
    guard = threading.Lock()
    active = 0
    maximum_active = 0

    def delayed_build(
        *,
        root: Path,
        config_file: gap.LoadedSource,
        config: Mapping[str, Any],
        sources: Mapping[str, gap.LoadedSource],
        implementations: Mapping[str, gap.LoadedSource],
    ) -> tuple[dict[str, Any], str]:
        nonlocal active, maximum_active
        with guard:
            active += 1
            maximum_active = max(maximum_active, active)
        try:
            time.sleep(0.03)
            return original(
                root=root,
                config_file=config_file,
                config=config,
                sources=sources,
                implementations=implementations,
            )
        finally:
            with guard:
                active -= 1

    monkeypatch.setattr(gap, "_build_report", delayed_build)

    def publish() -> Mapping[str, Any]:
        return run_gap_audit(
            config_path=config_path.relative_to(workspace),
            workspace_root=workspace,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: publish(), range(2)))

    assert maximum_active == 1
    assert results[0]["generation_id"] == results[1]["generation_id"]
    generations = list((workspace / gap.DEFAULT_OUTPUT_ROOT / "generations").iterdir())
    assert len(generations) == 1


def test_output_root_inode_rebind_cannot_split_single_writer_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path, _ = _workspace(tmp_path)
    original_build = gap._build_report
    original_publish = gap._publish_generation
    first_build_entered = threading.Event()
    root_rebound = threading.Event()
    guard = threading.Lock()
    build_calls = 0
    active_publishers = 0
    maximum_active_publishers = 0

    def block_first_build(
        *,
        root: Path,
        config_file: gap.LoadedSource,
        config: Mapping[str, Any],
        sources: Mapping[str, gap.LoadedSource],
        implementations: Mapping[str, gap.LoadedSource],
    ) -> tuple[dict[str, Any], str]:
        nonlocal build_calls
        with guard:
            build_calls += 1
            call_number = build_calls
        if call_number == 1:
            first_build_entered.set()
            assert root_rebound.wait(timeout=5.0)
        return original_build(
            root=root,
            config_file=config_file,
            config=config,
            sources=sources,
            implementations=implementations,
        )

    def overlap_probe(
        *,
        root: Path,
        output_root: Path,
        boundary: gap.PublicationBoundary,
        generation_id: str,
        report_bytes: bytes,
        receipt_bytes: bytes,
    ) -> tuple[Path, Path]:
        nonlocal active_publishers, maximum_active_publishers
        with guard:
            active_publishers += 1
            maximum_active_publishers = max(
                maximum_active_publishers,
                active_publishers,
            )
        try:
            time.sleep(0.1)
            return original_publish(
                root=root,
                output_root=output_root,
                boundary=boundary,
                generation_id=generation_id,
                report_bytes=report_bytes,
                receipt_bytes=receipt_bytes,
            )
        finally:
            with guard:
                active_publishers -= 1

    monkeypatch.setattr(gap, "_build_report", block_first_build)
    monkeypatch.setattr(gap, "_publish_generation", overlap_probe)

    def publish() -> tuple[str, object]:
        try:
            return (
                "ok",
                run_gap_audit(
                    config_path=config_path.relative_to(workspace),
                    workspace_root=workspace,
                ),
            )
        except GapValidationError as error:
            return ("error", error.code)

    output_root = workspace / gap.DEFAULT_OUTPUT_ROOT
    displaced_root = workspace / "artifacts/reproducibility/rebound-old-output"
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(publish)
        assert first_build_entered.wait(timeout=5.0)
        output_root.rename(displaced_root)
        output_root.mkdir(mode=0o750)
        second = executor.submit(publish)
        root_rebound.set()
        outcomes = [first.result(timeout=10.0), second.result(timeout=10.0)]

    assert maximum_active_publishers == 1
    assert sorted(status for status, _ in outcomes) == ["error", "ok"]
    assert [value for status, value in outcomes if status == "error"] == [
        "output_root_rebound"
    ]
    assert not (displaced_root / gap.COMMIT_FILE_NAME).exists()
    valid = validate_committed_gap_audit(
        commit_path=Path(gap.DEFAULT_OUTPUT_ROOT) / gap.COMMIT_FILE_NAME,
        workspace_root=workspace,
    )
    assert valid["status"] == "valid"


@pytest.mark.parametrize(
    ("target", "mode", "error"),
    [
        ("generation", 0o750, "generation_mode"),
        ("report", 0o640, "published_report_mode"),
        ("receipt", 0o640, "receipt_mode"),
        ("commit", 0o640, "commit_mode"),
    ],
)
def test_deep_validation_rejects_noncanonical_modes(
    tmp_path: Path,
    target: str,
    mode: int,
    error: str,
) -> None:
    workspace, config_path, _ = _workspace(tmp_path)
    result = run_gap_audit(
        config_path=config_path.relative_to(workspace),
        workspace_root=workspace,
    )
    receipt_path = workspace / str(result["receipt"])
    paths = {
        "generation": receipt_path.parent,
        "report": workspace / str(result["report"]),
        "receipt": receipt_path,
        "commit": workspace / str(result["commit"]),
    }
    paths[target].chmod(mode)

    _assert_error(
        error,
        lambda: validate_committed_gap_audit(
            commit_path=result["commit"],
            workspace_root=workspace,
        ),
    )


@pytest.mark.parametrize(
    ("target", "error"),
    [
        ("report", "published_report_nlink"),
        ("receipt", "receipt_nlink"),
        ("commit", "commit_nlink"),
    ],
)
def test_deep_validation_rejects_hardlinks(
    tmp_path: Path,
    target: str,
    error: str,
) -> None:
    workspace, config_path, _ = _workspace(tmp_path)
    result = run_gap_audit(
        config_path=config_path.relative_to(workspace),
        workspace_root=workspace,
    )
    paths = {
        "report": workspace / str(result["report"]),
        "receipt": workspace / str(result["receipt"]),
        "commit": workspace / str(result["commit"]),
    }
    os.link(
        paths[target],
        workspace / gap.DEFAULT_OUTPUT_ROOT / f"{target}-hardlink.json",
    )

    _assert_error(
        error,
        lambda: validate_committed_gap_audit(
            commit_path=result["commit"],
            workspace_root=workspace,
        ),
    )


def test_generation_collision_rejects_report_hardlink(tmp_path: Path) -> None:
    workspace, config_path, _ = _workspace(tmp_path)
    result = run_gap_audit(
        config_path=config_path.relative_to(workspace),
        workspace_root=workspace,
    )
    os.link(
        workspace / str(result["report"]),
        workspace / gap.DEFAULT_OUTPUT_ROOT / "collision-report-hardlink.json",
    )

    _assert_error(
        "generation_report_nlink",
        lambda: run_gap_audit(
            config_path=config_path.relative_to(workspace),
            workspace_root=workspace,
        ),
    )


def test_deep_validation_rejects_receipt_symlink(tmp_path: Path) -> None:
    workspace, config_path, _ = _workspace(tmp_path)
    result = run_gap_audit(
        config_path=config_path.relative_to(workspace),
        workspace_root=workspace,
    )
    receipt_path = workspace / str(result["receipt"])
    generation = receipt_path.parent
    moved = workspace / gap.DEFAULT_OUTPUT_ROOT / "receipt-symlink-target.json"
    generation.chmod(0o750)
    receipt_path.rename(moved)
    receipt_path.symlink_to(moved)
    generation.chmod(gap.GENERATION_MODE)

    _assert_error(
        "receipt_symlink",
        lambda: validate_committed_gap_audit(
            commit_path=result["commit"],
            workspace_root=workspace,
        ),
    )


def test_deep_receipt_read_detects_path_replacement_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path, _ = _workspace(tmp_path)
    result = run_gap_audit(
        config_path=config_path.relative_to(workspace),
        workspace_root=workspace,
    )
    receipt_path = workspace / str(result["receipt"])
    generation = receipt_path.parent
    moved = workspace / gap.DEFAULT_OUTPUT_ROOT / "raced-receipt.json"
    receipt_inode = receipt_path.lstat().st_ino
    original_read = os.read
    replaced = False

    def replace_after_read(descriptor: int, count: int) -> bytes:
        nonlocal replaced
        content = original_read(descriptor, count)
        if not replaced and content and os.fstat(descriptor).st_ino == receipt_inode:
            replaced = True
            generation.chmod(0o750)
            receipt_path.rename(moved)
            receipt_path.write_bytes(moved.read_bytes())
            receipt_path.chmod(gap.PUBLISHED_FILE_MODE)
            generation.chmod(gap.GENERATION_MODE)
        return content

    monkeypatch.setattr(os, "read", replace_after_read)
    with pytest.raises(
        GapValidationError,
        match=r"^receipt_changed(?:_after_read)?$",
    ):
        validate_committed_gap_audit(
            commit_path=result["commit"],
            workspace_root=workspace,
        )
    assert replaced is True


def test_commit_namespace_tamper_is_rejected_with_valid_binding(
    tmp_path: Path,
) -> None:
    workspace, config_path, _ = _workspace(tmp_path)
    result = run_gap_audit(
        config_path=config_path.relative_to(workspace),
        workspace_root=workspace,
    )
    commit_path = workspace / str(result["commit"])
    commit = json.loads(commit_path.read_text(encoding="utf-8"))
    commit["publication_namespace"]["config_sha256"] = "0" * 64
    commit["binding_sha256"] = gap._binding(commit)
    commit_path.chmod(0o640)
    commit_path.write_bytes(gap._canonical_json_bytes(commit, pretty=True))
    commit_path.chmod(gap.COMMIT_MODE)

    _assert_error(
        "commit_namespace_mismatch",
        lambda: validate_committed_gap_audit(
            commit_path=result["commit"],
            workspace_root=workspace,
        ),
    )


def test_post_commit_deep_failure_removes_new_commit_and_fsyncs_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path, _ = _workspace(tmp_path)
    output = workspace / gap.DEFAULT_OUTPUT_ROOT
    fsynced: list[tuple[int, int]] = []
    original_fsync = gap._fsync_directory

    def record_fsync(path: Path) -> None:
        metadata = path.stat()
        fsynced.append((metadata.st_dev, metadata.st_ino))
        original_fsync(path)

    def fail_deep(
        *,
        root: Path,
        config_file: gap.LoadedSource,
        config: Mapping[str, Any],
        commit_path: str | Path,
        boundary: gap.PublicationBoundary,
    ) -> Mapping[str, Any]:
        del root, config_file, config, commit_path, boundary
        raise GapValidationError("injected_post_commit_failure")

    monkeypatch.setattr(gap, "_fsync_directory", record_fsync)
    monkeypatch.setattr(
        gap,
        "_validate_committed_gap_audit_loaded",
        fail_deep,
    )
    _assert_error(
        "injected_post_commit_failure",
        lambda: run_gap_audit(
            config_path=config_path.relative_to(workspace),
            workspace_root=workspace,
        ),
    )

    assert not (output / gap.COMMIT_FILE_NAME).exists()
    output_metadata = output.stat()
    assert (output_metadata.st_dev, output_metadata.st_ino) in fsynced
    generations = list((output / "generations").iterdir())
    assert len(generations) == 1
    assert stat.S_IMODE(generations[0].lstat().st_mode) == gap.GENERATION_MODE


def test_post_commit_deep_failure_restores_previous_valid_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path, _ = _workspace(tmp_path)
    first = run_gap_audit(
        config_path=config_path.relative_to(workspace),
        workspace_root=workspace,
    )
    commit_path = workspace / str(first["commit"])
    previous_bytes = commit_path.read_bytes()
    original_validate = gap._validate_committed_gap_audit_loaded
    original_replace = gap._atomic_replace
    validation_calls = 0
    replacement_calls = 0

    def fail_post_only(
        *,
        root: Path,
        config_file: gap.LoadedSource,
        config: Mapping[str, Any],
        commit_path: str | Path,
        boundary: gap.PublicationBoundary,
    ) -> Mapping[str, Any]:
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls == 2:
            raise GapValidationError("injected_post_commit_failure")
        return original_validate(
            root=root,
            config_file=config_file,
            config=config,
            commit_path=commit_path,
            boundary=boundary,
        )

    def record_replace(
        path: Path,
        content: bytes,
        *,
        mode: int = gap.COMMIT_MODE,
    ) -> None:
        nonlocal replacement_calls
        replacement_calls += 1
        original_replace(path, content, mode=mode)

    monkeypatch.setattr(
        gap,
        "_validate_committed_gap_audit_loaded",
        fail_post_only,
    )
    monkeypatch.setattr(gap, "_atomic_replace", record_replace)
    _assert_error(
        "injected_post_commit_failure",
        lambda: run_gap_audit(
            config_path=config_path.relative_to(workspace),
            workspace_root=workspace,
        ),
    )

    assert validation_calls == 2
    assert replacement_calls == 2
    assert commit_path.read_bytes() == previous_bytes
    assert stat.S_IMODE(commit_path.lstat().st_mode) == gap.COMMIT_MODE
    monkeypatch.setattr(
        gap,
        "_validate_committed_gap_audit_loaded",
        original_validate,
    )
    deep = validate_committed_gap_audit(
        commit_path=first["commit"],
        workspace_root=workspace,
    )
    assert deep["generation_id"] == first["generation_id"]


def test_output_root_override_outside_contract_is_rejected(tmp_path: Path) -> None:
    workspace, config_path, _ = _workspace(tmp_path)

    _assert_error(
        "output_root_contract",
        lambda: run_gap_audit(
            config_path=config_path.relative_to(workspace),
            workspace_root=workspace,
            output_root="artifacts/reproducibility/unbound-output",
        ),
    )
    assert not (workspace / "artifacts/reproducibility/unbound-output").exists()
