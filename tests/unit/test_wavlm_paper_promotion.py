from __future__ import annotations

import hashlib
import json
from pathlib import Path
import statistics
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

from voice_coach.reproducibility import wavlm_paper_promotion as promotion
from voice_coach.reproducibility import wavlm_results as results


PENDING_MARKER = "[PENDING: S101–S108 — explicit/Base+/Large/adapter/HiPAMA/fusion 표]"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha(path),
        "size_bytes": path.stat().st_size,
    }


def _fingerprint(path: Path) -> results.FileFingerprint:
    return results.FileFingerprint(
        path=path,
        sha256=_sha(path),
        size_bytes=path.stat().st_size,
    )


def _pending_sha(markers: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(markers)).encode()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _aggregate(values: list[float]) -> dict[str, Any]:
    return {
        "mean": statistics.mean(values),
        "sample_std": statistics.stdev(values),
        "n": len(values),
    }


def _synthetic_metrics() -> tuple[
    dict[int, dict[str, dict[str, float]]],
    dict[int, dict[str, dict[str, dict[str, float]]]],
    dict[str, Any],
    dict[str, Any],
]:
    variants = ("base_plus", "large", "large_minus_base")
    phone_metrics = ("macro_f1", "auprc", "event_f1")
    utterance_metrics = ("qwk", "spearman", "mae", "icc_2_1")
    phone: dict[int, dict[str, dict[str, float]]] = {}
    utterance: dict[int, dict[str, dict[str, dict[str, float]]]] = {}
    for seed_index, seed in enumerate(results.EXPECTED_SEEDS):
        phone[seed] = {}
        for variant_index, variant in enumerate(variants):
            phone[seed][variant] = {
                metric: 0.1
                + 0.01 * seed_index
                + 0.02 * variant_index
                + 0.001 * metric_index
                for metric_index, metric in enumerate(phone_metrics)
            }
        utterance[seed] = {}
        for outcome_index, outcome in enumerate(results.OUTCOMES):
            utterance[seed][outcome] = {}
            for variant_index, variant in enumerate(variants):
                utterance[seed][outcome][variant] = {
                    metric: 0.2
                    + 0.01 * seed_index
                    + 0.02 * outcome_index
                    + 0.03 * variant_index
                    + 0.001 * metric_index
                    for metric_index, metric in enumerate(utterance_metrics)
                }

    comparison_aggregate: dict[str, Any] = {"phone": {}, "utterance": {}}
    for metric in phone_metrics:
        comparison_aggregate["phone"][metric] = {
            variant: _aggregate(
                [phone[seed][variant][metric] for seed in results.EXPECTED_SEEDS]
            )
            for variant in variants
        }
    for outcome in results.OUTCOMES:
        comparison_aggregate["utterance"][outcome] = {}
        for metric in utterance_metrics:
            comparison_aggregate["utterance"][outcome][metric] = {
                variant: _aggregate(
                    [
                        utterance[seed][outcome][variant][metric]
                        for seed in results.EXPECTED_SEEDS
                    ]
                )
                for variant in variants
            }

    primary = {
        "metric": "phone_macro_f1",
        "effect": "large_minus_base",
        "paired_speaker_bootstrap": {
            "estimate": 0.04,
            "confidence_lower": 0.01,
            "confidence_upper": 0.07,
            "p_value_two_sided_zero": 0.02,
            "n_resamples": 5000,
            "n_valid_resamples": 5000,
            "confidence_level": 0.95,
            "cluster_unit": "speaker",
            "seed": 42,
        },
        "gate": {
            "threshold_absolute": 0.03,
            "rule": "point_estimate_gte_threshold",
            "point_estimate_passed": True,
            "confidence_lower_gte_threshold": False,
            "status": "passed",
        },
    }
    return phone, utterance, primary, comparison_aggregate


def _active_paper(root: Path) -> tuple[Path, Path]:
    manuscript = root / promotion.CANONICAL_MANUSCRIPT
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text(
        f"기존 관측은 1건이다.\n\n{PENDING_MARKER}\n",
        encoding="utf-8",
    )
    summary = root / "artifacts/summary.json"
    registry = root / "artifacts/registry.json"
    _write_json(summary, {"count": 1, "accessed": False})
    _write_json(registry, {"metrics": [{"split": "dev"}]})
    evidence = {
        "schema_version": 1,
        "manuscript": manuscript.relative_to(root).as_posix(),
        "artifacts": {
            "summary": {
                "path": summary.relative_to(root).as_posix(),
                "sha256": _sha(summary),
            },
            "registry": {
                "path": registry.relative_to(root).as_posix(),
                "sha256": _sha(registry),
            },
        },
        "claims": [
            {
                "id": "existing_count",
                "source": {
                    "artifact": "summary",
                    "kind": "json_pointer",
                    "pointer": "/count",
                },
                "document": {"regex": r"기존 관측은 (?P<value>\d+)건"},
                "comparison": {"type": "numeric", "decimals": 0},
            }
        ],
        "pending_contract": {
            "marker_regex": r"\[PENDING:[^\]]+\]",
            "expected_count": 1,
            "expected_markers_sha256": _pending_sha([PENDING_MARKER]),
            "forbidden_numeric_regexes": [],
        },
        "locked_test_guard": {
            "access_checks": [
                {"artifact": "summary", "pointer": "/accessed"},
            ],
            "registries": [
                {
                    "artifact": "registry",
                    "array_pointer": "/metrics",
                    "split_field": "split",
                    "forbidden_splits": ["locked_test"],
                }
            ],
            "forbidden_document_regexes": [],
        },
    }
    evidence_path = root / "docs/paper/evidence-map.yaml"
    evidence_path.write_text(
        yaml.safe_dump(evidence, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return manuscript, evidence_path


def _lineage_files(
    root: Path,
) -> tuple[dict[str, Path], promotion.ValidatedLineage]:
    phone, utterance, primary, aggregate = _synthetic_metrics()
    base_report = root / "artifacts/base/report.json"
    base_registry = root / "artifacts/base/registry.json"
    large_report = root / "artifacts/large/report.json"
    large_registry = root / "artifacts/large/registry.json"
    comparison_commit = root / "artifacts/comparison/commit.json"
    comparison_report = root / "artifacts/comparison/report.json"
    comparison_receipt = root / "artifacts/comparison/receipt.json"
    results_commit = root / "artifacts/results/commit.json"
    results_markdown = root / "artifacts/results/table.md"
    results_json = root / "artifacts/results/table.json"

    _write_json(base_report, {"status": "completed"})
    _write_json(base_registry, {"status": "completed"})
    _write_json(
        large_report,
        {
            "status": "completed",
            "leakage_controls": {
                "stack_accessed": False,
                "calibration_accessed": False,
                "locked_test_accessed": False,
            },
        },
    )
    _write_json(
        large_registry,
        {"status": "completed", "metrics": [{"split": "dev"}]},
    )
    comparison_value = {
        "status": "completed",
        "metrics": {"aggregate": aggregate},
        "contract": {
            "leakage": {
                "stack_accessed": False,
                "calibration_accessed": False,
                "locked_test_accessed": False,
            }
        },
    }
    _write_json(comparison_report, comparison_value)
    _write_json(comparison_receipt, {"status": "completed"})
    _write_json(comparison_commit, {"status": "committed"})

    markdown = results._render_markdown(phone, utterance, primary)
    results_markdown.parent.mkdir(parents=True, exist_ok=True)
    results_markdown.write_text(markdown, encoding="utf-8")
    payload = {
        "schema_version": results.OUTPUT_SCHEMA,
        "status": "completed",
        "scope": {
            "development_only": True,
            "oracle_alignment": True,
            "locked_test_accessed": False,
            "stack_accessed": False,
            "calibration_accessed": False,
            "promotion_decision_allowed": False,
        },
        "phone": phone,
        "utterance": utterance,
        "primary_comparison": primary,
    }
    _write_json(results_json, payload)
    output_commit_value = {
        "generation_id": "a" * 64,
        "outputs": {
            "markdown": _record(root, results_markdown),
            "json": _record(root, results_json),
        },
        "comparison_commit": _record(root, comparison_commit),
    }
    _write_json(results_commit, output_commit_value)

    paths = {
        "base_report": base_report,
        "base_registry": base_registry,
        "large_report": large_report,
        "large_registry": large_registry,
        "comparison_commit": comparison_commit,
        "results_commit": results_commit,
    }
    inputs = {name: _fingerprint(path) for name, path in paths.items()}
    comparison = results.ComparisonCommit(
        manifest={"status": "committed"},
        receipt={"status": "completed"},
        report=comparison_value,
        manifest_file=inputs["comparison_commit"],
        receipt_file=_fingerprint(comparison_receipt),
        report_file=_fingerprint(comparison_report),
    )
    lineage = promotion.ValidatedLineage(
        inputs=inputs,
        comparison=comparison,
        markdown=markdown,
        payload=payload,
        output_commit=output_commit_value,
        output_markdown=_fingerprint(results_markdown),
        output_json=_fingerprint(results_json),
        phone=phone,
        utterance=utterance,
        primary=primary,
    )
    return paths, lineage


def _ready_manifest(
    root: Path,
    *,
    inputs: dict[str, Path],
    manuscript: Path,
    evidence_map: Path,
) -> Path:
    manifest = {
        "schema_version": promotion.MANIFEST_SCHEMA,
        "status": "ready",
        "inputs": {name: _record(root, path) for name, path in inputs.items()},
        "targets": {
            "manuscript": _record(root, manuscript),
            "evidence_map": _record(root, evidence_map),
            "insert_before_pending_marker": PENDING_MARKER,
        },
    }
    path = root / "docs/paper/promotion.yaml"
    path.write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def test_pending_manifest_fails_before_referenced_input_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "pending.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "schema_version": promotion.MANIFEST_SCHEMA,
                "status": "pending",
                "inputs": {"must_not_be_read": {"path": "missing"}},
                "targets": {},
            }
        ),
        encoding="utf-8",
    )

    def unexpected(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("referenced artifacts must not be inspected")

    monkeypatch.setattr(promotion, "_validate_ready_inputs", unexpected)

    with pytest.raises(
        promotion.PaperPromotionError,
        match="manifest_not_ready",
    ):
        promotion.prepare_wavlm_paper_promotion(
            manifest_path=manifest,
            workspace_root=tmp_path,
            output_root="artifacts/reproducibility/promotion",
        )

    assert not (tmp_path / "artifacts/reproducibility/promotion").exists()


def test_prepares_validated_candidate_without_editing_canonical_paper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manuscript, evidence_map = _active_paper(tmp_path)
    input_paths, lineage = _lineage_files(tmp_path)
    manifest = _ready_manifest(
        tmp_path,
        inputs=input_paths,
        manuscript=manuscript,
        evidence_map=evidence_map,
    )
    original_manuscript = manuscript.read_bytes()
    original_evidence = evidence_map.read_bytes()

    def validated_lineage(
        *,
        root: Path,
        inputs: dict[str, results.FileFingerprint],
    ) -> promotion.ValidatedLineage:
        assert root == tmp_path
        assert {name: item.sha256 for name, item in inputs.items()} == {
            name: item.sha256 for name, item in lineage.inputs.items()
        }
        return lineage

    monkeypatch.setattr(promotion, "_validate_lineage", validated_lineage)

    report = promotion.prepare_wavlm_paper_promotion(
        manifest_path=manifest,
        workspace_root=tmp_path,
        output_root="artifacts/reproducibility/promotion",
    )

    assert report["status"] == "completed"
    assert report["canonical_files_modified"] is False
    assert report["pending_contract_preserved"] is True
    assert manuscript.read_bytes() == original_manuscript
    assert evidence_map.read_bytes() == original_evidence

    candidate_manuscript = tmp_path / report["candidate_manuscript"]
    candidate_evidence = tmp_path / report["candidate_evidence_map"]
    additions = tmp_path / report["evidence_map_additions"]
    assert candidate_manuscript.read_text().count(PENDING_MARKER) == 1
    assert candidate_manuscript.read_text().count(promotion.BEGIN_MARKER) == 1
    assert "WavLM Large" in candidate_manuscript.read_text()
    assert "\n# WavLM frozen-head development results\n" not in (
        candidate_manuscript.read_text()
    )
    assert "\n#### WavLM frozen-head development results\n" in (
        candidate_manuscript.read_text()
    )

    candidate_config = yaml.safe_load(candidate_evidence.read_text())
    assert set(promotion.ARTIFACT_ALIASES).issubset(candidate_config["artifacts"])
    new_claims = [
        item
        for item in candidate_config["claims"]
        if item["id"].startswith("wavlm_s103_")
    ]
    assert len(new_claims) == 232
    additions_value = yaml.safe_load(additions.read_text())
    assert additions_value["insertion"]["pending_marker_must_remain"] is True

    commit = json.loads((tmp_path / report["commit"]).read_text())
    receipt_path = tmp_path / commit["promotion_receipt"]["path"]
    receipt = json.loads(receipt_path.read_text())
    assert receipt["canonical_files_modified"] is False
    assert receipt["pending_contract"]["preserved"] is True
    validation_path = tmp_path / receipt["outputs"]["candidate_validation"]["path"]
    validation = json.loads(validation_path.read_text())
    assert validation["status"] == "passed"


def test_target_hash_drift_fails_without_candidate_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manuscript, evidence_map = _active_paper(tmp_path)
    input_paths, _ = _lineage_files(tmp_path)
    manifest = _ready_manifest(
        tmp_path,
        inputs=input_paths,
        manuscript=manuscript,
        evidence_map=evidence_map,
    )
    manuscript.write_text(
        manuscript.read_text() + "\nconcurrent edit\n",
        encoding="utf-8",
    )

    def unexpected(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("lineage must not run after target hash drift")

    monkeypatch.setattr(promotion, "_validate_lineage", unexpected)

    with pytest.raises(
        promotion.PaperPromotionError,
        match="manifest_manuscript_.*hash_mismatch",
    ):
        promotion.prepare_wavlm_paper_promotion(
            manifest_path=manifest,
            workspace_root=tmp_path,
            output_root="artifacts/reproducibility/promotion",
        )

    assert not (tmp_path / "artifacts/reproducibility/promotion").exists()


def test_committed_bundle_loader_rejects_tampered_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manuscript, evidence_map = _active_paper(tmp_path)
    input_paths, lineage = _lineage_files(tmp_path)
    manifest = _ready_manifest(
        tmp_path,
        inputs=input_paths,
        manuscript=manuscript,
        evidence_map=evidence_map,
    )
    monkeypatch.setattr(
        promotion,
        "_validate_lineage",
        lambda **_: lineage,
    )
    prepared = promotion.prepare_wavlm_paper_promotion(
        manifest_path=manifest,
        workspace_root=tmp_path,
        output_root="artifacts/reproducibility/promotion",
    )
    loaded = promotion.load_committed_wavlm_paper_promotion(
        commit_path=prepared["commit"],
        workspace_root=tmp_path,
    )
    candidate = loaded["outputs"]["manuscript_candidate"].path
    candidate.write_text(
        candidate.read_text() + "\ntampered\n",
        encoding="utf-8",
    )

    with pytest.raises(
        promotion.PaperPromotionError,
        match="promotion_output_manuscript_candidate_.*hash_mismatch",
    ):
        promotion.load_committed_wavlm_paper_promotion(
            commit_path=prepared["commit"],
            workspace_root=tmp_path,
        )


def test_commit_replace_failure_cleans_uncommitted_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manuscript, evidence_map = _active_paper(tmp_path)
    input_paths, lineage = _lineage_files(tmp_path)
    manifest = _ready_manifest(
        tmp_path,
        inputs=input_paths,
        manuscript=manuscript,
        evidence_map=evidence_map,
    )
    monkeypatch.setattr(
        promotion,
        "_validate_lineage",
        lambda **_: lineage,
    )

    def fail_before_replace(path: Path, value: bytes) -> None:
        raise OSError("injected commit failure")

    monkeypatch.setattr(promotion, "_atomic_replace", fail_before_replace)

    with pytest.raises(OSError, match="injected commit failure"):
        promotion.prepare_wavlm_paper_promotion(
            manifest_path=manifest,
            workspace_root=tmp_path,
            output_root="artifacts/reproducibility/promotion",
        )

    output = tmp_path / "artifacts/reproducibility/promotion"
    assert not (output / "commit.json").exists()
    assert not [path for path in output.rglob("*") if path.is_file()]


def test_post_replace_failure_keeps_valid_committed_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manuscript, evidence_map = _active_paper(tmp_path)
    input_paths, lineage = _lineage_files(tmp_path)
    manifest = _ready_manifest(
        tmp_path,
        inputs=input_paths,
        manuscript=manuscript,
        evidence_map=evidence_map,
    )
    monkeypatch.setattr(
        promotion,
        "_validate_lineage",
        lambda **_: lineage,
    )
    real_replace = promotion._atomic_replace

    def fail_after_replace(path: Path, value: bytes) -> None:
        real_replace(path, value)
        raise OSError("injected post-replace failure")

    monkeypatch.setattr(promotion, "_atomic_replace", fail_after_replace)

    with pytest.raises(OSError, match="injected post-replace failure"):
        promotion.prepare_wavlm_paper_promotion(
            manifest_path=manifest,
            workspace_root=tmp_path,
            output_root="artifacts/reproducibility/promotion",
        )

    commit = tmp_path / "artifacts/reproducibility/promotion/commit.json"
    loaded = promotion.load_committed_wavlm_paper_promotion(
        commit_path=commit,
        workspace_root=tmp_path,
    )
    assert loaded["candidate_validation"]["status"] == "passed"


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("comparison_config_sha256", "0" * 64),
        ("comparison_script_sha256", "1" * 64),
        ("comparison_module_sha256", "2" * 64),
        ("hash_chain_validated", False),
        ("unexpected", True),
    ],
)
def test_results_provenance_rejects_source_hash_or_key_tampering(
    tmp_path: Path,
    field: str,
    replacement: Any,
) -> None:
    _, lineage = _lineage_files(tmp_path)
    sources = {
        "config": lineage.inputs["base_report"],
        "comparison_script": lineage.inputs["base_registry"],
        "comparison_module": lineage.inputs["large_report"],
    }
    comparison_record = _record(
        tmp_path,
        lineage.inputs["comparison_commit"].path,
    )
    provenance = {
        "base_plus_report_sha256": lineage.inputs["base_report"].sha256,
        "base_plus_registry_sha256": lineage.inputs["base_registry"].sha256,
        "large_report_sha256": lineage.inputs["large_report"].sha256,
        "large_registry_sha256": lineage.inputs["large_registry"].sha256,
        "comparison_report_sha256": lineage.comparison.report_file.sha256,
        "comparison_receipt_sha256": lineage.comparison.receipt_file.sha256,
        "comparison_commit": comparison_record,
        "comparison_config_sha256": sources["config"].sha256,
        "comparison_script_sha256": sources["comparison_script"].sha256,
        "comparison_module_sha256": sources["comparison_module"].sha256,
        "hash_chain_validated": True,
    }
    promotion._validate_results_provenance(
        root=tmp_path,
        payload={"provenance": provenance},
        inputs=lineage.inputs,
        comparison=lineage.comparison,
        comparison_sources=sources,
    )
    tampered = dict(provenance)
    tampered[field] = replacement

    with pytest.raises(
        promotion.PaperPromotionError,
        match="lineage_output_provenance_mismatch",
    ):
        promotion._validate_results_provenance(
            root=tmp_path,
            payload={"provenance": tampered},
            inputs=lineage.inputs,
            comparison=lineage.comparison,
            comparison_sources=sources,
        )
