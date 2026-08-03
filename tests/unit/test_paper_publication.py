from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import time
from typing import Any

import pytest
import yaml

from voice_coach.reproducibility import paper_publication as publication


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPOSITORY_ROOT / "scripts" / "audit_paper_publication.py"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record(root: Path, path: str) -> dict[str, Any]:
    target = root / path
    return {
        "path": path,
        "sha256": _sha(target),
        "size_bytes": target.stat().st_size,
    }


def _pending_hash(markers: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(markers)).encode()).hexdigest()


def _write_text(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _write_json(root: Path, path: str, value: dict[str, Any]) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _fixture(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    markers = ["[PENDING: first]", "[PENDING: second]"]
    urls = [
        "https://arxiv.org/abs/0001.00001",
        "https://arxiv.org/abs/0002.00002",
        "https://doi.org/10.1109/ONE.1",
        "https://doi.org/10.1109/THREE.3",
        "https://doi.org/10.1109/TWO.2",
        "https://doi.org/10.1109/ZERO.0",
    ]
    manuscript = f"""# Synthetic paper

Observed [1], then compared [2].

{markers[0]}

| Metric | Value |
|---|---:|
| rows | 10 |

[data table](tables/data.md)

| Group | Count |
|---|---:|
| dev | 4 |

[flow](figures/flow.mmd)

{markers[1]}

## 참고문헌

1. First source. [source]({urls[0]})
2. Second source. [source]({urls[1]})

## 부록
"""
    _write_text(
        tmp_path,
        "docs/paper/EXPERIMENTAL_MANUSCRIPT_KO.md",
        manuscript,
    )
    _write_text(tmp_path, "docs/paper/evidence-map.yaml", "schema_version: 1\n")
    _write_text(tmp_path, "docs/paper/tables/data.md", "| a |\n|---|\n| 1 |\n")
    _write_text(tmp_path, "docs/paper/figures/flow.mmd", "flowchart TD\nA-->B\n")
    _write_text(
        tmp_path,
        "docs/paper/RELATED_WORK_SOURCE_LEDGER_KO.md",
        "\n".join(f"[u{index}]({url})" for index, url in enumerate(urls))
        + "\n2개는 HTTP 200, IEEE DOI 4개는 HTTP 202를 반환했다.\n",
    )
    _write_text(tmp_path, "scripts/validate_manuscript_evidence.py", "pass\n")
    _write_text(tmp_path, "src/voice_coach/reproducibility/evidence.py", "pass\n")
    _write_text(tmp_path, "docs/MANUSCRIPT_EVIDENCE_VALIDATION_KO.md", "passed\n")
    _write_text(
        tmp_path, "src/voice_coach/reproducibility/paper_publication.py", "pass\n"
    )
    _write_text(tmp_path, "scripts/audit_paper_publication.py", "pass\n")

    manuscript_record = _record(
        tmp_path,
        "docs/paper/EXPERIMENTAL_MANUSCRIPT_KO.md",
    )
    map_record = _record(tmp_path, "docs/paper/evidence-map.yaml")
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "passed",
        "privacy": {
            "aggregate_only": True,
            "environment_files_read": False,
            "raw_data_read": False,
        },
        "manuscript": {
            "path": manuscript_record["path"],
            "sha256": manuscript_record["sha256"],
        },
        "evidence_map": {
            "path": map_record["path"],
            "sha256": map_record["sha256"],
        },
        "summary": {
            "artifact_count": 2,
            "artifact_passed": 2,
            "claim_count": 4,
            "claim_passed": 4,
        },
        "pending_contract": {
            "status": "passed",
            "marker_count": 2,
            "markers_sha256": _pending_hash(markers),
            "errors": [],
        },
        "locked_test_guard": {
            "status": "passed",
            "errors": [],
            "accessed": {"synthetic": False},
            "registry_metric_splits": ["dev"],
        },
    }
    _write_json(
        tmp_path,
        "artifacts/reproducibility/manuscript_evidence_validation.json",
        report,
    )

    config: dict[str, Any] = {
        "schema_version": publication.CONFIG_SCHEMA,
        "status": "ready",
        "policy": {
            "offline_default": True,
            "network_is_completion_evidence": False,
            "canonical_write_allowed": False,
            "expected_markdown_tables": 2,
            "pending_marker_regex": r"\[PENDING:[^\]]+\]",
            "expected_pending_count": 2,
            "expected_pending_markers_sha256": _pending_hash(markers),
        },
        "implementation": {
            "module": _record(
                tmp_path,
                "src/voice_coach/reproducibility/paper_publication.py",
            ),
            "cli": _record(tmp_path, "scripts/audit_paper_publication.py"),
        },
        "evidence_gate": {
            "report": _record(
                tmp_path,
                "artifacts/reproducibility/manuscript_evidence_validation.json",
            ),
            "validator_script": _record(
                tmp_path,
                "scripts/validate_manuscript_evidence.py",
            ),
            "validator_module": _record(
                tmp_path,
                "src/voice_coach/reproducibility/evidence.py",
            ),
            "validation_document": _record(
                tmp_path,
                "docs/MANUSCRIPT_EVIDENCE_VALIDATION_KO.md",
            ),
            "expected": {
                "status": "passed",
                "artifact_count": 2,
                "claim_count": 4,
                "pending_count": 2,
                "pending_markers_sha256": _pending_hash(markers),
                "locked_guard_status": "passed",
                "registry_metric_splits": ["dev"],
            },
        },
        "canonical": {
            "manuscript": manuscript_record,
            "evidence_map": map_record,
            "source_ledger": _record(
                tmp_path,
                "docs/paper/RELATED_WORK_SOURCE_LEDGER_KO.md",
            ),
            "local_assets": [
                {
                    "link_target": "tables/data.md",
                    "source": _record(tmp_path, "docs/paper/tables/data.md"),
                },
                {
                    "link_target": "figures/flow.mmd",
                    "source": _record(tmp_path, "docs/paper/figures/flow.mmd"),
                },
            ],
        },
        "citation_contract": {
            "expected_reference_count": 2,
            "expected_definition_sequence": [1, 2],
            "expected_first_use_order": [1, 2],
            "expected_primary_targets": {"1": urls[0], "2": urls[1]},
        },
        "external_url_contract": {
            "expected_count": 6,
            "expected_status_counts": {"200": 2, "202": 4},
            "records": [
                {
                    "url": url,
                    "expected_status": 200 if index < 2 else 202,
                }
                for index, url in enumerate(urls)
            ],
        },
        "output": {
            "root": "artifacts/reproducibility/paper_publication",
            "commit_name": "commit.json",
            "receipt_name": "publication-audit.json",
        },
    }
    config_path = tmp_path / "configs/paper/publication_v1.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return config_path, config


def _rewrite_config(path: Path, config: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _refresh_report_pin(root: Path, config: dict[str, Any]) -> None:
    config["evidence_gate"]["report"] = _record(
        root,
        "artifacts/reproducibility/manuscript_evidence_validation.json",
    )


def _refresh_manuscript_binding(
    root: Path,
    config: dict[str, Any],
) -> None:
    manuscript = _record(
        root,
        "docs/paper/EXPERIMENTAL_MANUSCRIPT_KO.md",
    )
    config["canonical"]["manuscript"] = manuscript
    report_path = root / "artifacts/reproducibility/manuscript_evidence_validation.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["manuscript"] = {
        "path": manuscript["path"],
        "sha256": manuscript["sha256"],
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    _refresh_report_pin(root, config)


def test_config_only_reads_no_referenced_sources_or_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, _ = _fixture(tmp_path)

    def forbidden(
        root: Path,
        value: object,
        *,
        code: str,
        maximum_bytes: int = publication.MAX_SOURCE_BYTES,
    ) -> tuple[bytes, publication.FileRecord]:
        raise AssertionError((root, value, code, maximum_bytes))

    monkeypatch.setattr(publication, "_read_source_record", forbidden)
    result = publication.validate_config_only(
        config_path=config_path,
        workspace_root=tmp_path,
    )

    assert result["status"] == "valid_config_only"
    assert set(result["actual_state"].values()) == {0}
    assert not (tmp_path / "artifacts/reproducibility/paper_publication").exists()


def test_offline_audit_publishes_and_deep_validates_content_addressed_receipt(
    tmp_path: Path,
) -> None:
    config_path, _ = _fixture(tmp_path)
    manuscript = (tmp_path / "docs/paper/EXPERIMENTAL_MANUSCRIPT_KO.md").read_bytes()
    evidence = (tmp_path / "docs/paper/evidence-map.yaml").read_bytes()

    first = publication.audit_paper_publication(
        config_path=config_path,
        workspace_root=tmp_path,
    )
    second = publication.audit_paper_publication(
        config_path=config_path,
        workspace_root=tmp_path,
    )
    validated = publication.validate_publication_receipt(
        config_path=config_path,
        commit_path=first["commit"],
        workspace_root=tmp_path,
    )

    assert first["generation_id"] == second["generation_id"]
    assert validated["status"] == "passed"
    receipt_path = tmp_path / first["receipt"]
    commit_path = tmp_path / first["commit"]
    generation_path = receipt_path.parent
    lock_path = (
        tmp_path
        / "artifacts/reproducibility/paper_publication"
        / publication.PUBLICATION_LOCK_NAME
    )
    namespace_lock_path = (
        tmp_path / "artifacts/reproducibility" / publication.NAMESPACE_LOCK_NAME
    )
    assert _sha(receipt_path) == first["generation_id"]
    assert stat.S_IMODE(receipt_path.lstat().st_mode) == publication.RECEIPT_MODE
    assert receipt_path.lstat().st_nlink == 1
    assert stat.S_IMODE(commit_path.lstat().st_mode) == publication.COMMIT_MODE
    assert commit_path.lstat().st_nlink == 1
    assert stat.S_IMODE(generation_path.lstat().st_mode) == publication.GENERATION_MODE
    assert stat.S_IMODE(lock_path.lstat().st_mode) == publication.LOCK_MODE
    assert lock_path.lstat().st_nlink == 1
    assert stat.S_IMODE(namespace_lock_path.lstat().st_mode) == publication.LOCK_MODE
    assert namespace_lock_path.lstat().st_nlink == 1
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["publication_namespace"] == {
        "commit_name": publication.CANONICAL_COMMIT_NAME,
        "config_path": publication.CANONICAL_CONFIG_PATH,
        "config_sha256": _sha(config_path),
        "output_root": publication.CANONICAL_OUTPUT_ROOT,
        "receipt_name": publication.CANONICAL_RECEIPT_NAME,
    }
    assert receipt["citation_audit"]["reference_count"] == 2
    assert receipt["markdown_audit"]["table_count"] == 2
    assert receipt["network_observation"]["request_count"] == 0
    assert receipt["locked_test_guard"]["locked_test_files_read"] == 0
    assert (
        tmp_path / "docs/paper/EXPERIMENTAL_MANUSCRIPT_KO.md"
    ).read_bytes() == manuscript
    assert (tmp_path / "docs/paper/evidence-map.yaml").read_bytes() == evidence


def test_evidence_report_is_first_source_and_failed_status_stops_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, config = _fixture(tmp_path)
    report_path = (
        tmp_path / "artifacts/reproducibility/manuscript_evidence_validation.json"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["status"] = "failed"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    _refresh_report_pin(tmp_path, config)
    _rewrite_config(config_path, config)
    calls: list[str] = []
    original = publication._read_source_record

    def recording(
        root: Path,
        value: object,
        *,
        code: str,
        maximum_bytes: int = publication.MAX_SOURCE_BYTES,
    ) -> tuple[bytes, publication.FileRecord]:
        calls.append(code)
        return original(
            root,
            value,
            code=code,
            maximum_bytes=maximum_bytes,
        )

    monkeypatch.setattr(publication, "_read_source_record", recording)
    with pytest.raises(
        publication.PublicationAuditError,
        match="evidence_report_status",
    ):
        publication.audit_paper_publication(
            config_path=config_path,
            workspace_root=tmp_path,
        )
    assert calls == ["evidence_report"]


@pytest.mark.parametrize(
    ("replace_from", "replace_to", "error"),
    [
        (
            "Observed [1], then compared [2].",
            "Observed [1], then compared [3].",
            "citation_undefined",
        ),
        (
            "Observed [1], then compared [2].",
            "Observed [1], then compared [1].",
            "citation_unused_reference",
        ),
        ("2. Second source.", "1. Second source.", "reference_definition_order"),
        ("|---|---:|", "|---|---:|---|", "markdown_table_count"),
    ],
)
def test_citation_and_markdown_drift_fail_closed(
    tmp_path: Path,
    replace_from: str,
    replace_to: str,
    error: str,
) -> None:
    config_path, config = _fixture(tmp_path)
    manuscript_path = tmp_path / "docs/paper/EXPERIMENTAL_MANUSCRIPT_KO.md"
    manuscript_path.write_text(
        manuscript_path.read_text(encoding="utf-8").replace(
            replace_from,
            replace_to,
            1,
        ),
        encoding="utf-8",
    )
    _refresh_manuscript_binding(tmp_path, config)
    _rewrite_config(config_path, config)

    with pytest.raises(publication.PublicationAuditError, match=error):
        publication.audit_paper_publication(
            config_path=config_path,
            workspace_root=tmp_path,
        )


def test_local_asset_hash_and_pending_marker_drift_fail_closed(
    tmp_path: Path,
) -> None:
    config_path, _ = _fixture(tmp_path)
    (tmp_path / "docs/paper/tables/data.md").write_text(
        "tampered\n",
        encoding="utf-8",
    )
    with pytest.raises(publication.PublicationAuditError, match="asset_0_fingerprint"):
        publication.audit_paper_publication(
            config_path=config_path,
            workspace_root=tmp_path,
        )

    config_path, config = _fixture(tmp_path / "pending")
    manuscript_path = tmp_path / "pending/docs/paper/EXPERIMENTAL_MANUSCRIPT_KO.md"
    manuscript_path.write_text(
        manuscript_path.read_text(encoding="utf-8").replace(
            "[PENDING: second]",
            "premature result 0.9",
        ),
        encoding="utf-8",
    )
    _refresh_manuscript_binding(tmp_path / "pending", config)
    _rewrite_config(config_path, config)
    with pytest.raises(publication.PublicationAuditError, match="pending_contract"):
        publication.audit_paper_publication(
            config_path=config_path,
            workspace_root=tmp_path / "pending",
        )


def test_locked_access_true_and_url_ledger_drift_fail_closed(
    tmp_path: Path,
) -> None:
    config_path, config = _fixture(tmp_path)
    report_path = (
        tmp_path / "artifacts/reproducibility/manuscript_evidence_validation.json"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["locked_test_guard"]["accessed"]["synthetic"] = True
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    _refresh_report_pin(tmp_path, config)
    _rewrite_config(config_path, config)
    with pytest.raises(
        publication.PublicationAuditError,
        match="evidence_report_locked",
    ):
        publication.audit_paper_publication(
            config_path=config_path,
            workspace_root=tmp_path,
        )

    drift_root = tmp_path / "ledger"
    config_path, config = _fixture(drift_root)
    ledger_path = drift_root / "docs/paper/RELATED_WORK_SOURCE_LEDGER_KO.md"
    ledger_path.write_text(
        ledger_path.read_text(encoding="utf-8").replace(
            "https://arxiv.org/abs/0002.00002",
            "https://arxiv.org/abs/9999.99999",
        ),
        encoding="utf-8",
    )
    config["canonical"]["source_ledger"] = _record(
        drift_root,
        "docs/paper/RELATED_WORK_SOURCE_LEDGER_KO.md",
    )
    _rewrite_config(config_path, config)
    with pytest.raises(
        publication.PublicationAuditError,
        match="external_ledger_url_set",
    ):
        publication.audit_paper_publication(
            config_path=config_path,
            workspace_root=drift_root,
        )


def test_commit_fault_leaves_recoverable_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, _ = _fixture(tmp_path)
    original = publication._atomic_replace

    def fail_commit(
        path: Path,
        value: bytes,
        *,
        mode: int = publication.COMMIT_MODE,
    ) -> None:
        del path, value, mode
        raise OSError("injected commit fault")

    monkeypatch.setattr(publication, "_atomic_replace", fail_commit)
    with pytest.raises(OSError, match="injected commit fault"):
        publication.audit_paper_publication(
            config_path=config_path,
            workspace_root=tmp_path,
        )
    output = tmp_path / "artifacts/reproducibility/paper_publication"
    assert not (output / "commit.json").exists()
    assert len(list((output / "generations").iterdir())) == 1

    monkeypatch.setattr(publication, "_atomic_replace", original)
    recovered = publication.audit_paper_publication(
        config_path=config_path,
        workspace_root=tmp_path,
    )
    assert recovered["status"] == "passed"


def test_post_receipt_write_fault_recovers_without_duplicate_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, _ = _fixture(tmp_path)
    original = publication._write_new_file
    triggered = False

    def fail_after_write(path: Path, value: bytes, *, mode: int = 0o440) -> None:
        nonlocal triggered
        original(path, value, mode=mode)
        if not triggered:
            triggered = True
            raise OSError("injected post-write fault")

    monkeypatch.setattr(publication, "_write_new_file", fail_after_write)
    with pytest.raises(OSError, match="injected post-write fault"):
        publication.audit_paper_publication(
            config_path=config_path,
            workspace_root=tmp_path,
        )
    monkeypatch.setattr(publication, "_write_new_file", original)
    recovered = publication.audit_paper_publication(
        config_path=config_path,
        workspace_root=tmp_path,
    )
    generations = list(
        (tmp_path / "artifacts/reproducibility/paper_publication/generations").iterdir()
    )
    assert recovered["status"] == "passed"
    assert len(generations) == 1


def test_deep_validation_rejects_receipt_tamper_and_extra_generation_file(
    tmp_path: Path,
) -> None:
    config_path, _ = _fixture(tmp_path)
    result = publication.audit_paper_publication(
        config_path=config_path,
        workspace_root=tmp_path,
    )
    generation = (
        tmp_path
        / "artifacts/reproducibility/paper_publication/generations"
        / result["generation_id"]
    )
    os.chmod(generation, 0o750)
    (generation / "unexpected.txt").write_text("extra", encoding="utf-8")
    os.chmod(generation, publication.GENERATION_MODE)
    with pytest.raises(
        publication.PublicationAuditError,
        match="receipt_generation_tree",
    ):
        publication.validate_publication_receipt(
            config_path=config_path,
            commit_path=result["commit"],
            workspace_root=tmp_path,
        )
    os.chmod(generation, 0o750)
    (generation / "unexpected.txt").unlink()
    os.chmod(generation, publication.GENERATION_MODE)

    receipt = tmp_path / result["receipt"]
    os.chmod(receipt, 0o640)
    receipt.write_bytes(receipt.read_bytes() + b" ")
    os.chmod(receipt, publication.RECEIPT_MODE)
    with pytest.raises(
        publication.PublicationAuditError,
        match="receipt_generation_id",
    ):
        publication.validate_publication_receipt(
            config_path=config_path,
            commit_path=result["commit"],
            workspace_root=tmp_path,
        )


def test_canonical_namespace_rejects_alternate_config_without_output(
    tmp_path: Path,
) -> None:
    config_path, _ = _fixture(tmp_path)
    alternate = tmp_path / "configs/paper/alternate.yaml"
    alternate.write_bytes(config_path.read_bytes())

    with pytest.raises(
        publication.PublicationAuditError,
        match="publication_namespace",
    ):
        publication.validate_config_only(
            config_path=alternate,
            workspace_root=tmp_path,
        )

    assert not (tmp_path / "artifacts/reproducibility/paper_publication").exists()


def test_exclusive_lock_is_held_through_post_publish_deep_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, _ = _fixture(tmp_path)
    original = publication._validate_publication_receipt_loaded
    observed = False

    def assert_locked(
        *,
        root: Path,
        loaded: publication._Config,
        commit_path: str | Path,
        boundary: publication.PublicationBoundary,
    ) -> Mapping[str, Any]:
        nonlocal observed
        lock_paths = [
            (
                root
                / publication.CANONICAL_OUTPUT_ROOT
                / publication.PUBLICATION_LOCK_NAME
            ),
            (
                root
                / Path(publication.CANONICAL_OUTPUT_ROOT).parent
                / publication.NAMESPACE_LOCK_NAME
            ),
        ]
        for lock_path in lock_paths:
            descriptor = os.open(lock_path, os.O_RDWR)
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
            loaded=loaded,
            commit_path=commit_path,
            boundary=boundary,
        )

    monkeypatch.setattr(
        publication,
        "_validate_publication_receipt_loaded",
        assert_locked,
    )
    result = publication.audit_paper_publication(
        config_path=config_path,
        workspace_root=tmp_path,
    )

    assert result["status"] == "passed"
    assert observed is True


def test_output_root_inode_rebind_cannot_split_single_writer_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, _ = _fixture(tmp_path)
    original_build = publication._build_receipt
    original_publish = publication._publish_generation
    first_build_entered = threading.Event()
    root_rebound = threading.Event()
    guard = threading.Lock()
    build_calls = 0
    active_publishers = 0
    maximum_active_publishers = 0

    def block_first_build(
        root: Path,
        loaded: publication._Config,
    ) -> tuple[dict[str, Any], list[publication.FileRecord]]:
        nonlocal build_calls
        with guard:
            build_calls += 1
            call_number = build_calls
        if call_number == 1:
            first_build_entered.set()
            assert root_rebound.wait(timeout=5.0)
        return original_build(root, loaded)

    def overlap_probe(
        root: Path,
        generations_root: Path,
        boundary: publication.PublicationBoundary,
        generation_id: str,
        receipt_name: str,
        receipt_bytes: bytes,
    ) -> tuple[Path, publication.FileRecord]:
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
                root,
                generations_root,
                boundary,
                generation_id,
                receipt_name,
                receipt_bytes,
            )
        finally:
            with guard:
                active_publishers -= 1

    monkeypatch.setattr(publication, "_build_receipt", block_first_build)
    monkeypatch.setattr(publication, "_publish_generation", overlap_probe)

    def publish() -> tuple[str, object]:
        try:
            return (
                "ok",
                publication.audit_paper_publication(
                    config_path=config_path,
                    workspace_root=tmp_path,
                ),
            )
        except publication.PublicationAuditError as error:
            return ("error", error.code)

    output_root = tmp_path / publication.CANONICAL_OUTPUT_ROOT
    displaced_root = tmp_path / "artifacts/reproducibility/rebound-old-paper-output"
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
    assert not (displaced_root / publication.CANONICAL_COMMIT_NAME).exists()
    validated = publication.validate_publication_receipt(
        config_path=config_path,
        commit_path=(
            Path(publication.CANONICAL_OUTPUT_ROOT) / publication.CANONICAL_COMMIT_NAME
        ),
        workspace_root=tmp_path,
    )
    assert validated["status"] == "passed"


@pytest.mark.parametrize(
    ("target", "mode", "error"),
    [
        ("generation", 0o750, "receipt_generation_mode"),
        ("receipt", 0o640, "receipt_file_mode"),
        ("commit", 0o640, "publication_commit_mode"),
    ],
)
def test_deep_validation_rejects_noncanonical_modes(
    tmp_path: Path,
    target: str,
    mode: int,
    error: str,
) -> None:
    config_path, _ = _fixture(tmp_path)
    result = publication.audit_paper_publication(
        config_path=config_path,
        workspace_root=tmp_path,
    )
    receipt_path = tmp_path / result["receipt"]
    paths = {
        "generation": receipt_path.parent,
        "receipt": receipt_path,
        "commit": tmp_path / result["commit"],
    }
    os.chmod(paths[target], mode)

    with pytest.raises(publication.PublicationAuditError, match=error):
        publication.validate_publication_receipt(
            config_path=config_path,
            commit_path=result["commit"],
            workspace_root=tmp_path,
        )


@pytest.mark.parametrize(
    ("target", "error"),
    [
        ("receipt", "receipt_file_nlink"),
        ("commit", "publication_commit_nlink"),
    ],
)
def test_deep_validation_rejects_hardlinks(
    tmp_path: Path,
    target: str,
    error: str,
) -> None:
    config_path, _ = _fixture(tmp_path)
    result = publication.audit_paper_publication(
        config_path=config_path,
        workspace_root=tmp_path,
    )
    output_root = tmp_path / publication.CANONICAL_OUTPUT_ROOT
    source = (
        tmp_path / result["receipt"]
        if target == "receipt"
        else tmp_path / result["commit"]
    )
    hardlink = output_root / f"{target}-hardlink.json"
    os.link(source, hardlink)

    with pytest.raises(publication.PublicationAuditError, match=error):
        publication.validate_publication_receipt(
            config_path=config_path,
            commit_path=result["commit"],
            workspace_root=tmp_path,
        )


def test_generation_collision_rejects_receipt_hardlink(
    tmp_path: Path,
) -> None:
    config_path, _ = _fixture(tmp_path)
    result = publication.audit_paper_publication(
        config_path=config_path,
        workspace_root=tmp_path,
    )
    receipt_path = tmp_path / result["receipt"]
    hardlink = (
        tmp_path / publication.CANONICAL_OUTPUT_ROOT / "collision-receipt-hardlink.json"
    )
    os.link(receipt_path, hardlink)

    with pytest.raises(
        publication.PublicationAuditError,
        match="receipt_file_nlink",
    ):
        publication.audit_paper_publication(
            config_path=config_path,
            workspace_root=tmp_path,
        )


def test_deep_validation_rejects_receipt_symlink(
    tmp_path: Path,
) -> None:
    config_path, _ = _fixture(tmp_path)
    result = publication.audit_paper_publication(
        config_path=config_path,
        workspace_root=tmp_path,
    )
    receipt_path = tmp_path / result["receipt"]
    generation_path = receipt_path.parent
    moved_path = tmp_path / publication.CANONICAL_OUTPUT_ROOT / "symlink-target.json"
    os.chmod(generation_path, 0o750)
    receipt_path.rename(moved_path)
    receipt_path.symlink_to(moved_path)
    os.chmod(generation_path, publication.GENERATION_MODE)

    with pytest.raises(
        publication.PublicationAuditError,
        match="receipt_file_symlink",
    ):
        publication.validate_publication_receipt(
            config_path=config_path,
            commit_path=result["commit"],
            workspace_root=tmp_path,
        )


def test_deep_receipt_read_detects_path_replacement_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, _ = _fixture(tmp_path)
    result = publication.audit_paper_publication(
        config_path=config_path,
        workspace_root=tmp_path,
    )
    receipt_path = tmp_path / result["receipt"]
    generation_path = receipt_path.parent
    moved_path = tmp_path / publication.CANONICAL_OUTPUT_ROOT / "raced-receipt.json"
    receipt_inode = receipt_path.lstat().st_ino
    original_read = os.read
    replaced = False

    def replace_after_read(descriptor: int, count: int) -> bytes:
        nonlocal replaced
        value = original_read(descriptor, count)
        if not replaced and value and os.fstat(descriptor).st_ino == receipt_inode:
            replaced = True
            os.chmod(generation_path, 0o750)
            receipt_path.rename(moved_path)
            receipt_path.write_bytes(moved_path.read_bytes())
            os.chmod(receipt_path, publication.RECEIPT_MODE)
            os.chmod(generation_path, publication.GENERATION_MODE)
        return value

    monkeypatch.setattr(os, "read", replace_after_read)
    with pytest.raises(
        publication.PublicationAuditError,
        match=r"receipt_file_changed_(while_reading|after_read)",
    ):
        publication.validate_publication_receipt(
            config_path=config_path,
            commit_path=result["commit"],
            workspace_root=tmp_path,
        )
    assert replaced is True


def test_commit_namespace_tamper_is_rejected_even_with_valid_binding(
    tmp_path: Path,
) -> None:
    config_path, _ = _fixture(tmp_path)
    result = publication.audit_paper_publication(
        config_path=config_path,
        workspace_root=tmp_path,
    )
    commit_path = tmp_path / result["commit"]
    commit = json.loads(commit_path.read_text(encoding="utf-8"))
    commit["publication_namespace"]["config_sha256"] = "0" * 64
    commit_without_binding = dict(commit)
    del commit_without_binding["binding_sha256"]
    commit["binding_sha256"] = publication._binding(commit_without_binding)
    os.chmod(commit_path, 0o640)
    commit_path.write_bytes(publication._pretty_json_bytes(commit))
    os.chmod(commit_path, publication.COMMIT_MODE)

    with pytest.raises(
        publication.PublicationAuditError,
        match="publication_commit_namespace_mismatch",
    ):
        publication.validate_publication_receipt(
            config_path=config_path,
            commit_path=result["commit"],
            workspace_root=tmp_path,
        )


def test_post_commit_deep_failure_removes_new_commit_and_fsyncs_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, _ = _fixture(tmp_path)
    output_root = tmp_path / publication.CANONICAL_OUTPUT_ROOT
    fsynced: list[tuple[int, int]] = []
    original_fsync = publication._fsync_directory

    def record_fsync(path: Path) -> None:
        metadata = path.stat()
        fsynced.append((metadata.st_dev, metadata.st_ino))
        original_fsync(path)

    def fail_deep(
        *,
        root: Path,
        loaded: publication._Config,
        commit_path: str | Path,
        boundary: publication.PublicationBoundary,
    ) -> Mapping[str, Any]:
        del root, loaded, commit_path, boundary
        raise publication.PublicationAuditError("injected_post_commit_failure")

    monkeypatch.setattr(publication, "_fsync_directory", record_fsync)
    monkeypatch.setattr(
        publication,
        "_validate_publication_receipt_loaded",
        fail_deep,
    )
    with pytest.raises(
        publication.PublicationAuditError,
        match="injected_post_commit_failure",
    ):
        publication.audit_paper_publication(
            config_path=config_path,
            workspace_root=tmp_path,
        )

    assert not (output_root / publication.CANONICAL_COMMIT_NAME).exists()
    output_identity = output_root.stat()
    assert (output_identity.st_dev, output_identity.st_ino) in fsynced
    generations = list((output_root / "generations").iterdir())
    assert len(generations) == 1
    assert stat.S_IMODE(generations[0].lstat().st_mode) == publication.GENERATION_MODE


def test_post_commit_deep_failure_restores_previous_valid_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, _ = _fixture(tmp_path)
    first = publication.audit_paper_publication(
        config_path=config_path,
        workspace_root=tmp_path,
    )
    commit_path = tmp_path / first["commit"]
    previous_bytes = commit_path.read_bytes()
    original_validate = publication._validate_publication_receipt_loaded
    original_replace = publication._atomic_replace
    validation_calls = 0
    replacement_calls = 0

    def fail_post_only(
        *,
        root: Path,
        loaded: publication._Config,
        commit_path: str | Path,
        boundary: publication.PublicationBoundary,
    ) -> Mapping[str, Any]:
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls == 2:
            raise publication.PublicationAuditError("injected_post_commit_failure")
        return original_validate(
            root=root,
            loaded=loaded,
            commit_path=commit_path,
            boundary=boundary,
        )

    def record_replace(
        path: Path,
        value: bytes,
        *,
        mode: int = publication.COMMIT_MODE,
    ) -> None:
        nonlocal replacement_calls
        replacement_calls += 1
        original_replace(path, value, mode=mode)

    monkeypatch.setattr(
        publication,
        "_validate_publication_receipt_loaded",
        fail_post_only,
    )
    monkeypatch.setattr(publication, "_atomic_replace", record_replace)
    with pytest.raises(
        publication.PublicationAuditError,
        match="injected_post_commit_failure",
    ):
        publication.audit_paper_publication(
            config_path=config_path,
            workspace_root=tmp_path,
        )

    assert validation_calls == 2
    assert replacement_calls == 2
    assert commit_path.read_bytes() == previous_bytes
    assert stat.S_IMODE(commit_path.lstat().st_mode) == publication.COMMIT_MODE
    monkeypatch.setattr(
        publication,
        "_validate_publication_receipt_loaded",
        original_validate,
    )
    validated = publication.validate_publication_receipt(
        config_path=config_path,
        commit_path=first["commit"],
        workspace_root=tmp_path,
    )
    assert validated["generation_id"] == first["generation_id"]


def test_optional_network_observation_is_separate_and_noncanonical(
    tmp_path: Path,
) -> None:
    config_path, _ = _fixture(tmp_path)
    calls: list[str] = []

    def requester(url: str, timeout: float) -> tuple[int, str]:
        calls.append(url)
        assert timeout == 3.0
        return (202 if "doi.org" in url else 200), url

    observation = publication.observe_external_urls(
        config_path=config_path,
        workspace_root=tmp_path,
        observed_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
        timeout_seconds=3.0,
        requester=requester,
    )

    assert observation["status"] == "observation_only"
    assert observation["canonical_completion_evidence"] is False
    assert observation["request_count"] == 6
    assert len(calls) == 6
    output = publication.write_network_observation(
        document=observation,
        output_path=(
            "artifacts/reproducibility/paper_publication/"
            "network_observations/observed.json"
        ),
        workspace_root=tmp_path,
    )
    assert json.loads((tmp_path / output).read_text(encoding="utf-8")) == observation
    assert not (
        tmp_path / "artifacts/reproducibility/paper_publication/commit.json"
    ).exists()

    unsafe = dict(observation)
    unsafe["canonical_completion_evidence"] = True
    with pytest.raises(
        publication.PublicationAuditError,
        match="network_observation_contract",
    ):
        publication.write_network_observation(
            document=unsafe,
            output_path=(
                "artifacts/reproducibility/paper_publication/"
                "network_observations/unsafe.json"
            ),
            workspace_root=tmp_path,
        )


def test_cli_config_only_default_and_validate_modes(
    tmp_path: Path,
) -> None:
    config_path, _ = _fixture(tmp_path)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPOSITORY_ROOT / "src")
    base = [
        sys.executable,
        str(SCRIPT),
        "--workspace-root",
        str(tmp_path),
        "--config",
        str(config_path),
    ]
    config_only = subprocess.run(
        [*base, "--config-only"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert config_only.returncode == 0, config_only.stderr
    assert json.loads(config_only.stdout)["actual_state"]["outputs_written"] == 0

    audit = subprocess.run(
        base,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert audit.returncode == 0, audit.stderr
    result = json.loads(audit.stdout)
    validate = subprocess.run(
        [
            *base,
            "--validate-receipt-only",
            "--commit",
            result["commit"],
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert validate.returncode == 0, validate.stderr
    assert json.loads(validate.stdout)["generation_id"] == result["generation_id"]


def test_forbidden_config_path_is_rejected_before_source_access(
    tmp_path: Path,
) -> None:
    config_path, config = _fixture(tmp_path)
    config["canonical"]["local_assets"][0]["source"]["path"] = (
        "data/restricted/secret.md"
    )
    _rewrite_config(config_path, config)

    with pytest.raises(
        publication.PublicationAuditError,
        match="asset_0_path_forbidden",
    ):
        publication.validate_config_only(
            config_path=config_path,
            workspace_root=tmp_path,
        )
