from __future__ import annotations

from collections.abc import Sequence
import hashlib
import json
from pathlib import Path
import statistics
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

from voice_coach.reproducibility import (
    s104_s105_b07_paper_amendment as amendment,
)
from voice_coach.reproducibility.evidence import validate_evidence_map


RESULTS_ANCHOR = "[PENDING: S101–S108 — explicit/Base+/Large/adapter/HiPAMA/fusion 표]"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha(path),
        "size_bytes": path.stat().st_size,
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _aggregate(values: Sequence[float | None]) -> dict[str, Any]:
    numeric = [float(value) for value in values if value is not None]
    return {
        "mean": statistics.mean(numeric) if numeric else None,
        "sample_std": statistics.stdev(numeric) if len(numeric) > 1 else None,
    }


def _pending_hash(markers: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(markers)).encode("utf-8")).hexdigest()


def _s104_report() -> dict[str, Any]:
    phone = [0.51, 0.53, 0.52]
    auprc = [0.61, 0.64, 0.62]
    icc = [0.41, 0.43, 0.42]
    runs = []
    for index, seed in enumerate(amendment.EXPECTED_SEEDS):
        runs.append(
            {
                "seed": seed,
                "dev": {
                    "metrics": {
                        "phone": {
                            "macro_f1": phone[index],
                            "auprc": auprc[index],
                        },
                        "utterance": {
                            "outcomes": {
                                "comprehensibility_score": {
                                    "icc_2_1": icc[index],
                                }
                            }
                        },
                    }
                },
            }
        )
    return {
        "status": "completed",
        "development_only": True,
        "oracle_alignment": True,
        "silver_label": True,
        "promotion_decision_allowed": False,
        "leakage": {
            "stack_accessed": False,
            "calibration_accessed": False,
            "locked_test_accessed": False,
        },
        "runs": runs,
        "summary": {
            "phone_macro_f1": _aggregate(phone),
            "comprehensibility_icc_2_1": _aggregate(icc),
            "engineering_targets": {
                "mean_phone_passed": True,
                "mean_primary_utterance_passed": False,
            },
        },
    }


def _effect(offset: float, *, null_interval: bool = False) -> dict[str, Any]:
    return {
        "paired_speaker_bootstrap": {
            "estimate": offset,
            "confidence_lower": None if null_interval else offset - 0.01,
            "confidence_upper": None if null_interval else offset + 0.01,
            "p_value_two_sided_zero": 0.04,
        },
        "holm_adjusted_p_value": 0.08,
        "holm_rejected": False,
    }


def _s105_documents() -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    str,
]:
    reports: dict[str, dict[str, Any]] = {}
    table_rows: list[tuple[str, float, float | None]] = []
    for variant_index, variant in enumerate(amendment.S105_VARIANTS):
        phone = [
            0.45 + variant_index * 0.01 + seed_index * 0.005 for seed_index in range(3)
        ]
        icc: list[float | None]
        if variant == "simple_head_control":
            icc = [None, None, None]
        else:
            icc = [
                0.30 + variant_index * 0.02 + seed_index * 0.005
                for seed_index in range(3)
            ]
        reports[variant] = {
            "status": "completed",
            "metrics_by_seed": {
                str(seed): {
                    "phone": {"macro_f1": phone[index]},
                    "utterance": {
                        "outcomes": {
                            "comprehensibility_score": {
                                "icc_2_1": icc[index],
                            }
                        }
                    },
                }
                for index, seed in enumerate(amendment.EXPECTED_SEEDS)
            },
            "aggregate": {
                "phone_macro_f1": _aggregate(phone),
                "comprehensibility_score_icc_2_1": _aggregate(icc),
            },
        }
        table_rows.append(
            (
                variant,
                float(_aggregate(phone)["mean"]),
                _aggregate(icc)["mean"],
            )
        )
    outcomes = {
        "phone_macro_f1",
        "comprehensibility_score_icc_2_1",
    }
    pairs = amendment.S105_INCREMENTAL_PAIRS
    comparison = {
        "status": "completed",
        "development_only": True,
        "oracle_alignment": True,
        "silver_label": True,
        "promotion_decision_allowed": False,
        "leakage": {
            "stack_accessed": False,
            "calibration_accessed": False,
            "locked_test_accessed": False,
        },
        "primary_family": {
            outcome: _effect(0.02 if outcome == "phone_macro_f1" else 0.03)
            for outcome in outcomes
        },
        "incremental_comparisons": {
            pair: {outcome: _effect(0.01 + pair_index * 0.001) for outcome in outcomes}
            for pair_index, pair in enumerate(pairs)
        },
        "subgroup": {
            "eligible_group_count": 1,
            "worst_group_drop": 0.06,
            "maximum_exclusive": 0.05,
            "passed": False,
        },
        "adoption_gate": {
            "status": "failed",
            "adopt_candidate": False,
            "point_improvement_passed": True,
            "worst_group_drop_passed": False,
        },
    }
    lines = [
        "# S105 development-only hierarchy ablation",
        "",
        "| Variant | Phone macro-F1 mean | Comprehensibility ICC(2,1) mean |",
        "|---|---:|---:|",
    ]
    for variant, phone_mean, icc_mean in table_rows:
        icc_text = "NA" if icc_mean is None else f"{icc_mean:.6f}"
        lines.append(f"| {variant} | {phone_mean:.6f} | {icc_text} |")
    lines.extend(
        [
            "",
            "Adoption gate: **failed**.",
            "",
            (
                "Development-only, oracle-alignment, silver-label evidence; "
                "not a promotion decision."
            ),
            "",
        ]
    )
    return comparison, reports, "\n".join(lines)


def _b07_documents() -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[int, dict[str, Any]],
    dict[int, dict[str, Any]],
]:
    phone = [0.60, 0.62, 0.61]
    auprc = [0.70, 0.73, 0.71]
    explicit = [0.58, 0.59, 0.60]
    delta = [phone[index] - explicit[index] for index in range(len(phone))]
    metrics: dict[int, dict[str, Any]] = {}
    selections: dict[int, dict[str, Any]] = {}
    rows = []
    for index, seed in enumerate(amendment.EXPECTED_SEEDS):
        metrics[seed] = {
            "gop_acoustic_posterior": {
                "phone_macro_f1": phone[index],
                "error_auprc": auprc[index],
                "threshold": 0.50 + index * 0.01,
            },
            "dev_access_audit": {
                "stack_accessed": False,
                "calibration_accessed": False,
                "locked_test_accessed": False,
                "stack_count": 0,
                "calibration_count": 0,
                "locked_test_count": 0,
            },
        }
        selections[seed] = {
            "selected_final_temperature": 1.0 + index * 0.1,
            "gop_selected": {
                "regularization_c": 0.1 + index * 0.1,
                "threshold": 0.50 + index * 0.01,
            },
            "explicit_refit_control_selected": {
                "regularization_c": 1.0,
                "threshold": 0.5,
            },
        }
        rows.append(
            {
                "seed": seed,
                "gop_phone_macro_f1": phone[index],
                "explicit_refit_phone_macro_f1": explicit[index],
                "phone_macro_f1_delta": delta[index],
            }
        )
    aggregate = {
        "status": "completed",
        "oracle_alignment": True,
        "deployable": False,
        "canonical_b03_metric_claimed": False,
        "dev_access_audit": {
            "stack_accessed": False,
            "calibration_accessed": False,
            "locked_test_accessed": False,
        },
        "gop_acoustic_posterior": {
            "mean_seed_phone_macro_f1": statistics.mean(phone),
            "sample_std_seed_phone_macro_f1": statistics.stdev(phone),
            "mean_seed_error_auprc": statistics.mean(auprc),
            "sample_std_seed_error_auprc": statistics.stdev(auprc),
            "seed_summary_ddof": 1,
        },
        "adoption_gate": {"value": 0.02, "passed": True},
    }
    paired = {
        "seed_rows": rows,
        "mean_seed_gop_phone_macro_f1": statistics.mean(phone),
        "mean_seed_explicit_refit_phone_macro_f1": statistics.mean(explicit),
        "mean_seed_phone_macro_f1_delta": statistics.mean(delta),
        "sample_std_seed_phone_macro_f1_delta": statistics.stdev(delta),
        "canonical_b03_checkpoint_or_prediction_read": False,
    }
    return aggregate, paired, metrics, selections


def _registry(run_id: str = "synthetic-run") -> dict[str, Any]:
    return {
        "status": "completed",
        "run_id": run_id,
        "metrics": [{"split": "dev", "name": "synthetic"}],
    }


def _bind_commit(
    root: Path,
    commit_path: Path,
    artifacts: list[Path],
    *,
    generation_root: Path | None = None,
) -> None:
    records = [_record(root, path) for path in artifacts]
    document: dict[str, Any] = {
        "status": "committed",
        "artifacts": records,
    }
    if generation_root is not None:
        document["generation_path"] = generation_root.relative_to(root).as_posix()
        document["artifacts"] = [
            {
                **record,
                "path": path.relative_to(generation_root).as_posix(),
            }
            for record, path in zip(records, artifacts, strict=True)
        ]
    _write_json(
        commit_path,
        document,
    )


def _active_paper(root: Path) -> tuple[Path, Path, str]:
    markers = [
        RESULTS_ANCHOR,
        *[f"[PENDING: synthetic-{index:02d}]" for index in range(17)],
    ]
    pending_sha = _pending_hash(markers)
    manuscript = root / amendment.CANONICAL_MANUSCRIPT
    manuscript.parent.mkdir(parents=True, exist_ok=True)
    manuscript.write_text(
        "\n".join(
            [
                "Base observation: 1.",
                "METHODS_ANCHOR",
                "ANALYSIS_STATUS_ANCHOR",
                RESULTS_ANCHOR,
                "DISCUSSION_EXACT_SENTENCE",
                "REPRODUCIBILITY_ANCHOR",
                "APPENDIX_ANCHOR",
                *markers[1:],
                "",
            ]
        ),
        encoding="utf-8",
    )
    summary = root / "evidence/base-summary.json"
    registry = root / "evidence/base-registry.json"
    _write_json(summary, {"count": 1, "accessed": False})
    _write_json(registry, _registry())
    evidence = {
        "schema_version": 1,
        "manuscript": manuscript.relative_to(root).as_posix(),
        "artifacts": {
            "base_summary": {
                "path": summary.relative_to(root).as_posix(),
                "sha256": _sha(summary),
            },
            "base_registry": {
                "path": registry.relative_to(root).as_posix(),
                "sha256": _sha(registry),
            },
        },
        "claims": [
            {
                "id": "base_count",
                "source": {
                    "artifact": "base_summary",
                    "kind": "json_pointer",
                    "pointer": "/count",
                },
                "document": {"regex": r"Base observation: (?P<value>\d+)\."},
                "comparison": {"type": "numeric", "decimals": 0},
            }
        ],
        "pending_contract": {
            "marker_regex": r"\[PENDING:[^\]]+\]",
            "expected_count": 18,
            "expected_markers_sha256": pending_sha,
            "forbidden_numeric_regexes": [],
        },
        "locked_test_guard": {
            "access_checks": [{"artifact": "base_summary", "pointer": "/accessed"}],
            "registries": [
                {
                    "artifact": "base_registry",
                    "array_pointer": "/metrics",
                    "split_field": "split",
                    "forbidden_splits": [
                        "stack",
                        "calibration",
                        "locked_test",
                    ],
                }
            ],
            "forbidden_document_regexes": [],
        },
    }
    evidence_path = root / amendment.CANONICAL_EVIDENCE_MAP
    evidence_path.write_text(
        yaml.safe_dump(evidence, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    assert (
        validate_evidence_map(evidence_path, workspace_root=root)["status"] == "passed"
    )
    return manuscript, evidence_path, pending_sha


def _fixture(root: Path) -> tuple[Path, Path, Path, str]:
    manuscript, evidence, pending_sha = _active_paper(root)
    validator_paths: dict[str, Path] = {}
    for stage in amendment.EXPECTED_VALIDATORS:
        path = root / f"validators/{stage}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# synthetic public {stage} validator\n", encoding="utf-8")
        validator_paths[stage] = path

    inputs: dict[str, Path] = {}
    for stage in amendment.EXPECTED_VALIDATORS:
        config = root / f"configs/{stage}.yaml"
        commit = root / f"completed/{stage}/commit.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            yaml.safe_dump(
                {"outputs": {"commit": commit.relative_to(root).as_posix()}},
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        inputs[f"{stage}_config"] = config
        inputs[f"{stage}_commit"] = commit

    s104_root = root / "completed/s104/generation"
    inputs["s104_report"] = s104_root / "experiment_report.json"
    inputs["s104_registry"] = s104_root / "registry.json"
    inputs["s104_execution_lock"] = s104_root / "execution_lock.json"
    _write_json(inputs["s104_report"], _s104_report())
    _write_json(inputs["s104_registry"], _registry())
    _write_json(inputs["s104_execution_lock"], {"status": "locked"})
    _bind_commit(
        root,
        inputs["s104_commit"],
        [
            inputs["s104_report"],
            inputs["s104_registry"],
            inputs["s104_execution_lock"],
        ],
    )

    comparison, variant_reports, ablation_table = _s105_documents()
    s105_root = root / "completed/s105/generation"
    inputs["s105_comparison_report"] = s105_root / "ablation_comparison_report.json"
    inputs["s105_ablation_table"] = s105_root / "ablation_table.md"
    inputs["s105_execution_lock"] = s105_root / "execution_lock.json"
    _write_json(inputs["s105_comparison_report"], comparison)
    inputs["s105_ablation_table"].parent.mkdir(parents=True, exist_ok=True)
    inputs["s105_ablation_table"].write_text(
        ablation_table,
        encoding="utf-8",
    )
    _write_json(inputs["s105_execution_lock"], {"status": "locked"})
    s105_artifacts = [
        inputs["s105_comparison_report"],
        inputs["s105_ablation_table"],
        inputs["s105_execution_lock"],
    ]
    for variant in amendment.S105_VARIANTS:
        report_name = f"s105_variant_{variant}_report"
        registry_name = f"s105_variant_{variant}_registry"
        inputs[report_name] = s105_root / variant / "report.json"
        inputs[registry_name] = s105_root / variant / "registry.json"
        _write_json(inputs[report_name], variant_reports[variant])
        _write_json(inputs[registry_name], _registry())
        s105_artifacts.extend([inputs[report_name], inputs[registry_name]])
    _bind_commit(
        root,
        inputs["s105_commit"],
        s105_artifacts,
        generation_root=s105_root,
    )

    aggregate, paired, metrics, selections = _b07_documents()
    b07_root = root / "completed/b07/generation"
    inputs["b07_aggregate_report"] = b07_root / "aggregate_report.json"
    inputs["b07_paired_control_report"] = b07_root / "paired_control.json"
    inputs["b07_registry"] = b07_root / "registry.json"
    inputs["b07_execution_lock"] = b07_root / "execution_lock.json"
    _write_json(inputs["b07_aggregate_report"], aggregate)
    _write_json(inputs["b07_paired_control_report"], paired)
    _write_json(inputs["b07_registry"], _registry())
    _write_json(inputs["b07_execution_lock"], {"status": "locked"})
    b07_artifacts = [
        inputs["b07_aggregate_report"],
        inputs["b07_paired_control_report"],
        inputs["b07_registry"],
        inputs["b07_execution_lock"],
    ]
    for seed in amendment.EXPECTED_SEEDS:
        metric_name = f"b07_seed_{seed}_metrics"
        selection_name = f"b07_seed_{seed}_selection_trace"
        inputs[metric_name] = b07_root / f"seed_{seed}/metrics.json"
        inputs[selection_name] = b07_root / f"seed_{seed}/inner_selection_trace.json"
        _write_json(inputs[metric_name], metrics[seed])
        _write_json(inputs[selection_name], selections[seed])
        b07_artifacts.extend([inputs[metric_name], inputs[selection_name]])
    _bind_commit(root, inputs["b07_commit"], b07_artifacts)

    assert set(inputs) == set(amendment.EXPECTED_INPUTS)
    manifest = {
        "schema_version": amendment.MANIFEST_SCHEMA,
        "status": "ready",
        "actual_state": {
            "metric_values_read": 0,
            "artifact_or_registry_files_read": 0,
            "canonical_files_read": 0,
            "outputs_written": 0,
        },
        "contract": {
            "pending_marker_count": 18,
            "pending_markers_sha256": pending_sha,
            "null_input": None,
            "null_manuscript_token": "NA",
            "candidate_only": True,
            "canonical_write_allowed": False,
            "validator_timeout_seconds": 60,
        },
        "validators": {
            stage: _record(root, path) for stage, path in validator_paths.items()
        },
        "inputs": {
            name: _record(root, inputs[name]) for name in amendment.EXPECTED_INPUTS
        },
        "targets": {
            "manuscript": _record(root, manuscript),
            "evidence_map": _record(root, evidence),
        },
        "edits": {
            "methods": {
                "operation": "insert_before",
                "anchor": "METHODS_ANCHOR",
                "expected_matches": 1,
            },
            "analysis_status": {
                "operation": "insert_before",
                "anchor": "ANALYSIS_STATUS_ANCHOR",
                "expected_matches": 1,
            },
            "results": {
                "operation": "insert_before",
                "anchor": RESULTS_ANCHOR,
                "expected_matches": 1,
            },
            "discussion": {
                "operation": "replace_exact",
                "anchor": "DISCUSSION_EXACT_SENTENCE",
                "expected_matches": 1,
            },
            "reproducibility": {
                "operation": "insert_before",
                "anchor": "REPRODUCIBILITY_ANCHOR",
                "expected_matches": 1,
            },
            "appendix": {
                "operation": "insert_before",
                "anchor": "APPENDIX_ANCHOR",
                "expected_matches": 1,
            },
        },
    }
    manifest_path = root / "amendment-ready.yaml"
    manifest_path.write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return manifest_path, manuscript, evidence, pending_sha


def test_synthetic_candidate_bundle_is_atomic_and_generic_evidence_valid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, manuscript, evidence, pending_sha = _fixture(tmp_path)
    monkeypatch.setattr(
        amendment,
        "CURRENT_PENDING_MARKERS_SHA256",
        pending_sha,
    )
    synthetic_implementation = tmp_path / "implementation.py"
    synthetic_implementation.write_text(
        "# synthetic amendment implementation pin\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(amendment, "__file__", str(synthetic_implementation))
    original_manuscript = manuscript.read_bytes()
    original_evidence = evidence.read_bytes()
    calls: list[str] = []

    def runner(
        stage: str,
        workspace_root: Path,
        script: amendment.Fingerprint,
        config: amendment.Fingerprint,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        assert workspace_root == tmp_path
        assert script.path.is_file()
        assert config.path.is_file()
        assert timeout_seconds == 60
        calls.append(stage)
        return {"status": "valid", **amendment.VALIDATOR_COUNTS[stage]}

    result = amendment.prepare_paper_amendment(
        manifest_path=manifest,
        workspace_root=tmp_path,
        validator_runner=runner,
    )

    assert calls == ["s104", "s105", "b07"]
    assert result["status"] == "completed"
    assert result["pending_marker_count"] == 18
    assert result["pending_markers_sha256"] == pending_sha
    assert result["canonical_files_modified"] is False
    assert result["manual_apply_required"] is True
    assert manuscript.read_bytes() == original_manuscript
    assert evidence.read_bytes() == original_evidence

    loaded = amendment.load_committed_paper_amendment(
        commit_path=result["commit"],
        workspace_root=tmp_path,
    )
    assert loaded["candidate_validation"]["status"] == "passed"
    outputs = loaded["outputs"]
    candidate = outputs["manuscript_candidate"].path.read_text(encoding="utf-8")
    assert candidate.count("[PENDING:") == 18
    assert "NA <!-- evidence:" in candidate
    assert "<!-- BEGIN WAVLM_S104_ADAPTER_RESULTS_TABLE -->" in candidate
    assert "<!-- BEGIN WAVLM_S105_HIERARCHY_RESULTS_TABLE -->" in candidate
    assert "<!-- BEGIN B07_GOP_RESULTS_TABLE -->" in candidate

    payload = json.loads(outputs["paper_results_json"].path.read_text())
    assert payload["null_contract"] == {
        "source_json_null": None,
        "paper_json_and_markdown_token": "NA",
        "zero_imputation_allowed": False,
        "row_omission_allowed": False,
    }
    assert "NA" in payload["evidence_values"].values()
    assert 0 not in [
        value for value in payload["evidence_values"].values() if value == "NA"
    ]

    result_json_path = outputs["paper_results_json"].path
    result_json_path.write_bytes(result_json_path.read_bytes() + b" ")
    with pytest.raises(
        amendment.PaperAmendmentError,
        match="fingerprint_mismatch",
    ):
        amendment.load_committed_paper_amendment(
            commit_path=result["commit"],
            workspace_root=tmp_path,
        )


def test_first_deep_validator_failure_precedes_report_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, _, _, pending_sha = _fixture(tmp_path)
    monkeypatch.setattr(
        amendment,
        "CURRENT_PENDING_MARKERS_SHA256",
        pending_sha,
    )
    document = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    document["inputs"]["s104_report"] = {
        "path": "completed/s104/missing-report.json",
        "sha256": "0" * 64,
        "size_bytes": 1,
    }
    manifest.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    calls: list[str] = []

    def failing_runner(
        stage: str,
        workspace_root: Path,
        script: amendment.Fingerprint,
        config: amendment.Fingerprint,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        del workspace_root, script, config, timeout_seconds
        calls.append(stage)
        raise amendment.PaperAmendmentError("fixture_deep_failure")

    with pytest.raises(
        amendment.PaperAmendmentError,
        match="fixture_deep_failure",
    ):
        amendment.prepare_paper_amendment(
            manifest_path=manifest,
            workspace_root=tmp_path,
            validator_runner=failing_runner,
        )

    assert calls == ["s104"]
    assert not (tmp_path / amendment.DEFAULT_OUTPUT_ROOT).exists()
