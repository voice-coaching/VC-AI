from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import stat
import statistics
import subprocess
import sys
from typing import Any

import pytest

import voice_coach.reproducibility.wavlm_results as results_module
from voice_coach.evaluation.wavlm_comparison import (
    _publish_comparison_generation,
)
from voice_coach.reproducibility.wavlm_results import (
    COMPARISON_REPORT_SCHEMA,
    EXPECTED_SEEDS,
    HEAD_REPORT_SCHEMA,
    OUTCOMES,
    ResultsValidationError,
    load_committed_wavlm_results,
    render_wavlm_results,
)


REPOSITORY_ROOT = Path(__file__).parents[2]
SCRIPT = REPOSITORY_ROOT / "scripts" / "render_wavlm_results_table.py"
UTTERANCE_METRICS = ("qwk", "spearman", "mae", "icc_2_1")


@dataclass(frozen=True)
class Fixture:
    root: Path
    base_report: Path
    base_registry: Path
    large_report: Path
    large_registry: Path
    comparison_report: Path
    comparison_commit: Path
    output_markdown: Path
    output_json: Path
    output_commit: Path


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _record(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha(path),
        "size_bytes": path.stat().st_size,
    }


def _aggregate(values: list[float]) -> dict[str, float | int]:
    return {
        "mean": statistics.fmean(values),
        "sample_std": statistics.stdev(values),
        "valid_seeds": len(values),
    }


def _phone_values(variant: str, index: int) -> dict[str, float]:
    offset = 0.04 if variant == "large" else 0.0
    return {
        "macro_f1": 0.60 + offset + index * 0.01,
        "auprc": 0.50 + offset + index * 0.01,
    }


def _utterance_values(
    variant: str,
    outcome_index: int,
    seed_index: int,
) -> dict[str, float]:
    large = variant == "large"
    return {
        "qwk": 0.40 + outcome_index * 0.02 + seed_index * 0.01 + (0.03 if large else 0),
        "spearman": 0.45
        + outcome_index * 0.02
        + seed_index * 0.01
        + (0.03 if large else 0),
        "mae": 0.80 + outcome_index * 0.03 + seed_index * 0.01 - (0.04 if large else 0),
        "icc_2_1": 0.42
        + outcome_index * 0.02
        + seed_index * 0.01
        + (0.03 if large else 0),
    }


def _create_sources(root: Path) -> dict[str, Path]:
    sources = {
        "trainer": root / "implementation" / "trainer.py",
        "head": root / "implementation" / "head.py",
        "comparison_config": (
            root / "configs" / "experiments" / "wavlm_frozen_head_comparison_v1.yaml"
        ),
        "comparison_script": root / "scripts" / "compare_wavlm_frozen_heads.py",
        "comparison_module": (
            root / "src" / "voice_coach" / "evaluation" / "wavlm_comparison.py"
        ),
    }
    for name, path in sources.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# synthetic {name}\n", encoding="utf-8")
    return sources


def _create_head(
    root: Path,
    sources: dict[str, Path],
    variant: str,
) -> tuple[Path, Path]:
    head_dir = root / "artifacts" / variant
    registry_path = root / "artifacts" / "registry" / variant / "metadata.json"
    config = root / "configs" / f"{variant}.yaml"
    config.write_text(f"variant: {variant}\n", encoding="utf-8")
    runs: list[dict[str, Any]] = []
    artifact_paths: dict[str, Path] = {}
    for seed_index, seed in enumerate(EXPECTED_SEEDS):
        seed_dir = head_dir / f"seed_{seed}"
        seed_dir.mkdir(parents=True)
        for filename in (
            "checkpoint.pt",
            "slice_metrics.json",
            "phone_dev_predictions.parquet",
            "utterance_dev_predictions.parquet",
        ):
            path = seed_dir / filename
            path.write_bytes(f"{variant}:{seed}:{filename}".encode())
        artifact_paths.update(
            {
                f"seed_{seed}_checkpoint": seed_dir / "checkpoint.pt",
                f"seed_{seed}_slice_metrics": seed_dir / "slice_metrics.json",
                f"seed_{seed}_phone_predictions": (
                    seed_dir / "phone_dev_predictions.parquet"
                ),
                f"seed_{seed}_utterance_predictions": (
                    seed_dir / "utterance_dev_predictions.parquet"
                ),
            }
        )
        phone = _phone_values(variant, seed_index)
        outcomes = {
            outcome: {
                "observed_rows": 20,
                "metrics": _utterance_values(variant, outcome_index, seed_index),
            }
            for outcome_index, outcome in enumerate(OUTCOMES)
        }

        def relative(path: Path) -> str:
            return path.relative_to(head_dir).as_posix()

        runs.append(
            {
                "seed": seed,
                "selection": {"dev_values_used_for_selection": False},
                "phone": {
                    "metric_scope": "primary_dev_single_final_pass",
                    "rows": 100,
                    "metrics": {
                        **phone,
                        "decision_threshold": 0.5,
                    },
                    "predictions": {
                        "path": relative(
                            artifact_paths[f"seed_{seed}_phone_predictions"]
                        ),
                        "sha256": _sha(
                            artifact_paths[f"seed_{seed}_phone_predictions"]
                        ),
                    },
                },
                "utterance": {
                    "metric_scope": "primary_dev_single_final_pass",
                    "rows": 20,
                    "outcomes": outcomes,
                    "predictions": {
                        "path": relative(
                            artifact_paths[f"seed_{seed}_utterance_predictions"]
                        ),
                        "sha256": _sha(
                            artifact_paths[f"seed_{seed}_utterance_predictions"]
                        ),
                    },
                },
                "artifacts": {
                    "checkpoint": {
                        "path": relative(artifact_paths[f"seed_{seed}_checkpoint"]),
                        "sha256": _sha(artifact_paths[f"seed_{seed}_checkpoint"]),
                    },
                    "slice_metrics": {
                        "path": relative(artifact_paths[f"seed_{seed}_slice_metrics"]),
                        "sha256": _sha(artifact_paths[f"seed_{seed}_slice_metrics"]),
                    },
                },
            }
        )
    phone_summary = {
        metric: _aggregate([float(run["phone"]["metrics"][metric]) for run in runs])
        for metric in ("macro_f1", "auprc")
    }
    phone_summary["thresholds_by_seed"] = {str(seed): 0.5 for seed in EXPECTED_SEEDS}
    utterance_summary = {
        outcome: {
            metric: _aggregate(
                [
                    float(run["utterance"]["outcomes"][outcome]["metrics"][metric])
                    for run in runs
                ]
            )
            for metric in UTTERANCE_METRICS
        }
        for outcome in OUTCOMES
    }
    encoder_id = (
        "microsoft/wavlm-base-plus"
        if variant == "base_plus"
        else "microsoft/wavlm-large"
    )
    report = {
        "schema_version": HEAD_REPORT_SCHEMA,
        "status": "completed",
        "development_only": True,
        "oracle_alignment": True,
        "deployable_alignment_claim_allowed": False,
        "encoder": {
            "variant": variant,
            "id": encoder_id,
            "frozen": True,
        },
        "sources": {
            "training_config_sha256": _sha(config),
            "trainer_source_sha256": _sha(sources["trainer"]),
            "head_source_sha256": _sha(sources["head"]),
            "feature_cache_report_sha256": "1" * 64,
            "explicit_utterance_labels_sha256": "2" * 64,
            "modeling_manifest_sha256": "3" * 64,
            "prompt_reservation_sha256": "4" * 64,
            "cache_builder_config_sha256": "5" * 64,
            "cache_builder_sha256": "6" * 64,
            "encoder_config_sha256": "7" * 64,
            "encoder_checkpoint_sha256": "8" * 64,
            "model_hashes_bound_by_validated_config_and_cache_report": True,
        },
        "cache_contract": {
            "full_cache_validated": True,
            "cache_report_status": "completed",
            "source_scan_complete": True,
            "smoke_cache_rejected": True,
            "every_shard_hash_validated": True,
            "every_shard_schema_validated": True,
            "every_shard_split_policy_validated": True,
            "every_shard_privacy_metadata_validated": True,
        },
        "leakage_controls": {
            "accepted_splits": ["train", "dev"],
            "stack_accessed": False,
            "calibration_accessed": False,
            "locked_test_accessed": False,
            "dev_used_for_selection": False,
            "raw_manifest_read": False,
            "raw_path_columns_read": [],
            "environment_files_read": [],
            "environment_files_registered": [],
        },
        "runs": runs,
        "summary": {
            "seeds_completed": list(EXPECTED_SEEDS),
            "phone": phone_summary,
            "utterance": utterance_summary,
            "results_are_development_only": True,
            "locked_test_remains_unread": True,
            "stack_remains_unread": True,
            "calibration_remains_unread": True,
            "dev_used_for_selection": False,
            "train_inner_validation_used": True,
            "dev_inference_passes_per_seed": 1,
            "oracle_alignment": True,
            "promotion_decision_allowed": False,
        },
        "experiment_registry": {
            "integrated": True,
            "t03_complete": True,
            "run_id": f"{variant}-run",
            "metadata_path": registry_path.relative_to(root).as_posix(),
            "implementation_source_hashes_match_registry_inputs": True,
            "runtime_packages_gpu_git_recorded": True,
            "seeds_recorded": True,
            "metrics_recorded": True,
            "artifacts_recorded": True,
            "registered_artifact_count": 13,
        },
    }
    report_path = head_dir / "report.json"
    _write_json(report_path, report)
    metric_events = []
    for seed_index, seed in enumerate(EXPECTED_SEEDS):
        values: dict[str, float] = {
            "phone_macro_f1": _phone_values(variant, seed_index)["macro_f1"],
            "phone_auprc": _phone_values(variant, seed_index)["auprc"],
        }
        for outcome_index, outcome in enumerate(OUTCOMES):
            for metric, value in _utterance_values(
                variant,
                outcome_index,
                seed_index,
            ).items():
                values[f"{outcome}_{metric}"] = value
        metric_events.append(
            {
                "step": seed,
                "split": "dev",
                "values": values,
            }
        )
    metric_events.append(
        {
            "step": None,
            "split": "dev",
            "values": {
                "phone_macro_f1_mean": phone_summary["macro_f1"]["mean"],
                "phone_macro_f1_sample_std": phone_summary["macro_f1"]["sample_std"],
                "phone_auprc_mean": phone_summary["auprc"]["mean"],
                "phone_auprc_sample_std": phone_summary["auprc"]["sample_std"],
            },
        }
    )
    registry_artifacts = [
        {
            "name": name,
            "kind": "evaluation",
            **_record(root, path),
        }
        for name, path in artifact_paths.items()
    ]
    registry_artifacts.append(
        {
            "name": "final_report",
            "kind": "evaluation_report",
            **_record(root, report_path),
        }
    )
    registry = {
        "schema_version": 1,
        "run_id": f"{variant}-run",
        "status": "completed",
        "timestamps": {"ended": {"utc": "synthetic"}},
        "seeds": {f"experiment_seed_{seed}": seed for seed in EXPECTED_SEEDS},
        "inputs": {
            "config": [_record(root, config)],
            "model": [
                _record(root, sources["trainer"]),
                _record(root, sources["head"]),
            ],
            "data": [
                {
                    "path": f"data/processed/{variant}.parquet",
                    "sha256": "9" * 64,
                    "size_bytes": 1,
                }
            ],
            "checkpoint": [],
        },
        "metrics": metric_events,
        "artifacts": registry_artifacts,
        "failure": None,
    }
    _write_json(registry_path, registry)
    return report_path, registry_path


def _event_record(
    macro_f1: float,
    auprc: float,
    *,
    variant: str,
    seed_index: int,
) -> dict[str, Any]:
    if variant == "large":
        counts = {"tp": 65 + seed_index, "fp": 15, "fn": 15}
    else:
        counts = {"tp": 60 + seed_index, "fp": 20, "fn": 20}
    denominator = 2 * counts["tp"] + counts["fp"] + counts["fn"]
    return {
        "macro_f1": macro_f1,
        "auprc": auprc,
        "event_f1": 2 * counts["tp"] / denominator,
        "event_counts": counts,
    }


def _bootstrap(
    estimate: float,
    *,
    lower: float | None = None,
    upper: float | None = None,
    p_value: float = 0.10,
) -> dict[str, Any]:
    return {
        "estimate": estimate,
        "confidence_lower": estimate - 0.03 if lower is None else lower,
        "confidence_upper": estimate + 0.03 if upper is None else upper,
        "confidence_level": 0.95,
        "n_resamples": 5000,
        "n_valid_resamples": 5000,
        "n_speakers": 20,
        "seed": 42,
        "p_value_two_sided_zero": p_value,
    }


def _create_comparison(
    root: Path,
    sources: dict[str, Path],
    head_paths: dict[str, tuple[Path, Path]],
) -> tuple[Path, Path]:
    per_seed: dict[str, Any] = {}
    for seed_index, seed in enumerate(EXPECTED_SEEDS):
        phone_models: dict[str, Any] = {}
        for variant in ("base_plus", "large"):
            head_values = _phone_values(variant, seed_index)
            phone_models[variant] = _event_record(
                head_values["macro_f1"],
                head_values["auprc"],
                variant=variant,
                seed_index=seed_index,
            )
        phone_models["large_minus_base"] = {
            metric: phone_models["large"][metric] - phone_models["base_plus"][metric]
            for metric in ("macro_f1", "auprc", "event_f1")
        }
        utterance: dict[str, Any] = {}
        for outcome_index, outcome in enumerate(OUTCOMES):
            base = _utterance_values("base_plus", outcome_index, seed_index)
            large = _utterance_values("large", outcome_index, seed_index)
            utterance[outcome] = {
                "base_plus": base,
                "large": large,
                "large_minus_base": {
                    metric: large[metric] - base[metric] for metric in UTTERANCE_METRICS
                },
            }
        per_seed[f"seed_{seed}"] = {
            "phone": phone_models,
            "utterance": utterance,
        }
    aggregate_phone: dict[str, Any] = {}
    for metric in ("macro_f1", "auprc", "event_f1"):
        aggregate_phone[metric] = {
            variant: _aggregate(
                [
                    per_seed[f"seed_{seed}"]["phone"][variant][metric]
                    for seed in EXPECTED_SEEDS
                ]
            )
            for variant in ("base_plus", "large", "large_minus_base")
        }
    aggregate_utterance: dict[str, Any] = {}
    for outcome in OUTCOMES:
        aggregate_utterance[outcome] = {
            metric: {
                variant: _aggregate(
                    [
                        per_seed[f"seed_{seed}"]["utterance"][outcome][variant][metric]
                        for seed in EXPECTED_SEEDS
                    ]
                )
                for variant in ("base_plus", "large", "large_minus_base")
            }
            for metric in UTTERANCE_METRICS
        }

    report_sources: dict[str, Any] = {
        "config": _record(root, sources["comparison_config"]),
        "comparison_script": _record(root, sources["comparison_script"]),
        "comparison_module": _record(root, sources["comparison_module"]),
    }
    for variant, (report_path, registry_path) in head_paths.items():
        report = _read_json(report_path)
        prediction_sources: dict[str, Any] = {}
        for run, seed in zip(report["runs"], EXPECTED_SEEDS, strict=True):
            prediction_sources[f"seed_{seed}"] = {}
            for task in ("phone", "utterance"):
                relative = Path(run[task]["predictions"]["path"])
                prediction_path = report_path.parent / relative
                prediction_sources[f"seed_{seed}"][task] = {
                    "path": prediction_path.relative_to(root).as_posix(),
                    "sha256": _sha(prediction_path),
                    "size_bytes": prediction_path.stat().st_size,
                    "rows": run[task]["rows"],
                }
        registry = _read_json(registry_path)
        report_sources[variant] = {
            "report": _record(root, report_path),
            "registry": {
                **_record(root, registry_path),
                "run_id": registry["run_id"],
                "status": "completed",
            },
            "predictions": prediction_sources,
        }
    comparisons = {}
    for outcome_index, outcome in enumerate(OUTCOMES):
        estimate = 0.03
        raw_p = 0.02 + outcome_index * 0.03
        adjusted_p = (0.06, 0.10, 0.10)[outcome_index]
        comparisons[outcome] = {
            "effect": estimate,
            "raw_p_value": raw_p,
            "adjusted_p_value": adjusted_p,
            "rejected": False,
            "paired_speaker_bootstrap": _bootstrap(
                estimate,
                p_value=raw_p,
            ),
        }
    comparison = {
        "schema_version": COMPARISON_REPORT_SCHEMA,
        "status": "completed",
        "experiment_id": "synthetic-s103",
        "started_at_utc": "2026-07-29T00:00:00+00:00",
        "completed_at_utc": "2026-07-29T00:01:00+00:00",
        "development_only": True,
        "oracle_alignment": True,
        "comparison_scope": "primary_dev_paired",
        "sources": report_sources,
        "contract": {
            "seeds": list(EXPECTED_SEEDS),
            "registry_completion": {
                variant: {
                    "status": "completed",
                    "run_id": f"{variant}-run",
                    "registered_artifact_count": 13,
                }
                for variant in ("base_plus", "large")
            },
            "paired_identity": {
                "phone_ids_targets_speakers_equal": True,
                "utterance_ids_targets_speakers_equal": True,
                "cross_seed_equal": True,
            },
            "leakage": {
                "stack_accessed": False,
                "calibration_accessed": False,
                "locked_test_accessed": False,
                "raw_data_read": False,
                "environment_files_read": [],
            },
        },
        "metrics": {
            "per_seed": per_seed,
            "aggregate": {
                "phone": aggregate_phone,
                "utterance": aggregate_utterance,
            },
        },
        "primary_comparison": {
            "metric": "phone_macro_f1",
            "effect": "large_minus_base",
            "paired_speaker_bootstrap": _bootstrap(
                0.04,
                lower=0.01,
                upper=0.07,
                p_value=0.02,
            ),
            "gate": {
                "threshold_absolute": 0.03,
                "rule": "point_estimate_gte_threshold",
                "point_estimate_passed": True,
                "confidence_lower_gte_threshold": False,
                "status": "passed",
            },
        },
        "multiplicity": {
            "family": "utterance_icc_2_1_across_outcomes",
            "method": "holm",
            "alpha": 0.05,
            "comparisons": comparisons,
        },
        "summary": {
            "large_adoption_gate_passed": True,
            "large_adoption_gate_status": "passed",
            "results_are_development_only": True,
            "locked_test_remains_unread": True,
            "stack_remains_unread": True,
            "calibration_remains_unread": True,
        },
        "reproducibility": {
            "execution_receipt_created": True,
            "atomic_commit_manifest_created": True,
            "minimum_hash_chain_complete": True,
            "source_reports_registry_completed": True,
            "implementation_sources_hashed": True,
            "all_prediction_artifacts_hashed": True,
        },
        "privacy": {
            "aggregate_only_output": True,
            "direct_identifiers_written": False,
            "pseudonymous_row_ids_written": False,
            "prediction_rows_written": False,
            "raw_data_read": False,
            "source_feature_caches_read": False,
            "environment_files_read": [],
            "input_access_scope": "restricted_internal",
            "shareable": False,
        },
    }
    commit_path = root / "artifacts" / "comparison" / "commit.json"
    _publish_comparison_generation(
        workspace_root=root,
        generation_directory=(root / "artifacts" / "comparison" / "generations"),
        commit_path=commit_path,
        report=comparison,
        sources=report_sources,
    )
    commit = _read_json(commit_path)
    comparison_path = root / commit["comparison_report"]["path"]
    return comparison_path, commit_path


def _fixture(tmp_path: Path) -> Fixture:
    sources = _create_sources(tmp_path)
    head_paths = {
        variant: _create_head(tmp_path, sources, variant)
        for variant in ("base_plus", "large")
    }
    comparison, comparison_commit = _create_comparison(
        tmp_path,
        sources,
        head_paths,
    )
    return Fixture(
        root=tmp_path,
        base_report=head_paths["base_plus"][0],
        base_registry=head_paths["base_plus"][1],
        large_report=head_paths["large"][0],
        large_registry=head_paths["large"][1],
        comparison_report=comparison,
        comparison_commit=comparison_commit,
        output_markdown=tmp_path / "rendered" / "wavlm.md",
        output_json=tmp_path / "rendered" / "wavlm.json",
        output_commit=tmp_path / "rendered" / "commit.json",
    )


def _render(fixture: Fixture) -> dict[str, Any]:
    return render_wavlm_results(
        base_report_path=fixture.base_report,
        base_registry_path=fixture.base_registry,
        large_report_path=fixture.large_report,
        large_registry_path=fixture.large_registry,
        comparison_commit_path=fixture.comparison_commit,
        output_markdown_path=fixture.output_markdown,
        output_json_path=fixture.output_json,
        output_commit_path=fixture.output_commit,
        workspace_root=fixture.root,
    )


def _committed_output_paths(fixture: Fixture) -> tuple[Path, Path]:
    commit = _read_json(fixture.output_commit)
    return (
        fixture.root / commit["outputs"]["markdown"]["path"],
        fixture.root / commit["outputs"]["json"]["path"],
    )


def test_renders_verified_aggregate_tables_without_identifiers(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    result = _render(fixture)

    assert result["status"] == "completed"
    assert result["seeds"] == [42, 43, 44]
    assert result["primary_comparison"]["gate"]["status"] == "passed"
    markdown, committed_payload, commit = load_committed_wavlm_results(
        commit_manifest_path=fixture.output_commit,
        workspace_root=fixture.root,
    )
    assert "WavLM Base+" in markdown
    assert "Mean ± sample SD" in markdown
    assert "Large − Base+" in markdown
    serialized = json.dumps(committed_payload)
    assert "seed_42" not in serialized
    assert "utterance_id" not in serialized
    assert "speaker_id" not in serialized
    assert "phone_dev_predictions" not in serialized
    assert str(tmp_path) not in serialized
    assert commit["status"] == "committed"
    assert not list(fixture.output_json.parent.rglob("*.tmp"))


def test_tampered_head_artifact_is_rejected_before_output(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    artifact = fixture.base_report.parent / "seed_42" / "checkpoint.pt"
    artifact.write_bytes(b"tampered")

    with pytest.raises(ResultsValidationError, match="hash_mismatch"):
        _render(fixture)

    assert not fixture.output_markdown.exists()
    assert not fixture.output_json.exists()


def test_prediction_source_drift_is_rejected(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    prediction = (
        fixture.large_report.parent / "seed_42" / "phone_dev_predictions.parquet"
    )
    prediction.write_bytes(prediction.read_bytes() + b"tampered")

    with pytest.raises(ResultsValidationError, match="hash_mismatch"):
        _render(fixture)

    assert not fixture.output_commit.exists()


@pytest.mark.parametrize(
    "source",
    [
        "implementation/trainer.py",
        "scripts/compare_wavlm_frozen_heads.py",
    ],
)
def test_tampered_implementation_source_is_rejected(
    tmp_path: Path,
    source: str,
) -> None:
    fixture = _fixture(tmp_path)
    (tmp_path / source).write_text(
        "# changed after report\n",
        encoding="utf-8",
    )

    with pytest.raises(ResultsValidationError, match="hash_mismatch"):
        _render(fixture)


@pytest.mark.parametrize(
    ("target", "key", "value"),
    [
        ("base_report", "status", "running"),
        ("base_report", "oracle_alignment", False),
        ("base_report", "locked_test_accessed", True),
        ("base_registry", "status", "running"),
    ],
)
def test_incomplete_or_leaking_head_inputs_are_rejected(
    tmp_path: Path,
    target: str,
    key: str,
    value: object,
) -> None:
    fixture = _fixture(tmp_path)
    path = getattr(fixture, target)
    payload = _read_json(path)
    if key == "locked_test_accessed":
        payload["leakage_controls"][key] = value
    else:
        payload[key] = value
    _write_json(path, payload)

    with pytest.raises(ResultsValidationError):
        _render(fixture)


def test_comparison_report_and_prediction_hash_chain_are_enforced(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    comparison = _read_json(fixture.comparison_report)
    comparison["sources"]["large"]["report"]["sha256"] = "0" * 64
    _write_json(fixture.comparison_report, comparison)

    with pytest.raises(
        ResultsValidationError,
        match="comparison_committed_report_hash_mismatch",
    ):
        _render(fixture)


def test_tampered_execution_receipt_is_rejected(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    commit = _read_json(fixture.comparison_commit)
    receipt_path = fixture.root / commit["execution_receipt"]["path"]
    receipt = _read_json(receipt_path)
    receipt["statistics_sha256"] = "0" * 64
    _write_json(receipt_path, receipt)

    with pytest.raises(
        ResultsValidationError,
        match="comparison_committed_receipt_hash_mismatch",
    ):
        _render(fixture)


@pytest.mark.parametrize("kind", ["duplicate", "nonfinite"])
def test_renderer_rejects_strict_json_violations(
    tmp_path: Path,
    kind: str,
) -> None:
    fixture = _fixture(tmp_path)
    original = fixture.comparison_commit.read_text(encoding="utf-8")
    injected = '"status": "committed",' if kind == "duplicate" else '"poison": 1e999,'
    fixture.comparison_commit.write_text(
        original.replace("{", "{" + injected, 1),
        encoding="utf-8",
    )

    expected = "json_duplicate_key" if kind == "duplicate" else "json_nonfinite_number"
    with pytest.raises(ResultsValidationError, match=expected):
        _render(fixture)


@pytest.mark.parametrize(
    "mutation",
    ["head_metric", "event_count", "aggregate", "gate", "holm", "seeds", "privacy"],
)
def test_metric_and_policy_inconsistencies_are_rejected(
    tmp_path: Path,
    mutation: str,
) -> None:
    fixture = _fixture(tmp_path)
    comparison = _read_json(fixture.comparison_report)
    if mutation == "head_metric":
        comparison["metrics"]["per_seed"]["seed_42"]["phone"]["base_plus"][
            "macro_f1"
        ] += 0.01
    elif mutation == "event_count":
        comparison["metrics"]["per_seed"]["seed_42"]["phone"]["large"]["event_counts"][
            "tp"
        ] += 1
    elif mutation == "aggregate":
        comparison["metrics"]["aggregate"]["phone"]["auprc"]["large"]["mean"] += 0.01
    elif mutation == "gate":
        comparison["primary_comparison"]["gate"]["point_estimate_passed"] = False
    elif mutation == "holm":
        comparison["multiplicity"]["comparisons"]["fluency_score"][
            "adjusted_p_value"
        ] = 0.09
    elif mutation == "seeds":
        comparison["contract"]["seeds"] = [42, 43]
    else:
        comparison["privacy"]["pseudonymous_row_ids_written"] = True
    _write_json(fixture.comparison_report, comparison)

    with pytest.raises(ResultsValidationError):
        _render(fixture)


def test_changed_bootstrap_interval_is_rejected_by_execution_receipt(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    comparison = _read_json(fixture.comparison_report)
    bootstrap = comparison["primary_comparison"]["paired_speaker_bootstrap"]
    bootstrap["confidence_lower"] = 0.05
    bootstrap["confidence_upper"] = 0.08
    comparison["primary_comparison"]["gate"]["confidence_lower_gte_threshold"] = True
    _write_json(fixture.comparison_report, comparison)

    with pytest.raises(
        ResultsValidationError,
        match="comparison_committed_report_hash_mismatch",
    ):
        _render(fixture)


def test_changed_p_values_are_rejected_by_execution_receipt(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    comparison = _read_json(fixture.comparison_report)
    raw_values = (0.01, 0.02, 0.05)
    adjusted_values = (0.03, 0.04, 0.05)
    for outcome, raw, adjusted in zip(
        OUTCOMES,
        raw_values,
        adjusted_values,
        strict=True,
    ):
        item = comparison["multiplicity"]["comparisons"][outcome]
        item["raw_p_value"] = raw
        item["adjusted_p_value"] = adjusted
        item["rejected"] = True
        item["paired_speaker_bootstrap"]["p_value_two_sided_zero"] = raw
    _write_json(fixture.comparison_report, comparison)

    with pytest.raises(
        ResultsValidationError,
        match="comparison_committed_report_hash_mismatch",
    ):
        _render(fixture)


@pytest.mark.parametrize(
    "forbidden_part",
    [
        "raw",
        "raw-zone",
        "restricted_area",
        "stack-zone",
        "calibration_area",
        "locked-zone",
        ".env.local",
        ".env_backup",
        ".ENV-local",
        ".envrc",
        ".environment",
    ],
)
def test_forbidden_path_is_rejected_without_reading_it(
    tmp_path: Path,
    forbidden_part: str,
) -> None:
    fixture = _fixture(tmp_path)
    forbidden = tmp_path / forbidden_part / "report.json"

    with pytest.raises(ResultsValidationError, match="forbidden_path"):
        render_wavlm_results(
            base_report_path=forbidden,
            base_registry_path=fixture.base_registry,
            large_report_path=fixture.large_report,
            large_registry_path=fixture.large_registry,
            comparison_commit_path=fixture.comparison_commit,
            output_markdown_path=fixture.output_markdown,
            output_json_path=fixture.output_json,
            output_commit_path=fixture.output_commit,
            workspace_root=fixture.root,
        )


@pytest.mark.parametrize(
    ("role", "forbidden_path"),
    [
        ("registry", "restricted-zone/metadata.json"),
        ("comparison_commit", "raw_area/commit.json"),
        ("prediction", "stack_area/phone.parquet"),
        ("markdown", "calibration-zone/table.md"),
        ("json", "locked_area/table.json"),
        ("output_commit", "raw-zone/commit.json"),
        ("registry", "safe/.env_backup/metadata.json"),
        ("comparison_commit", "safe/.ENV-local/commit.json"),
        ("prediction", "safe/.envrc/phone.parquet"),
        ("markdown", "safe/.environment/table.md"),
        ("json", "safe/.env-secret/table.json"),
        ("output_commit", "safe/.env_cache/commit.json"),
    ],
)
def test_prefixed_forbidden_paths_cover_inputs_predictions_and_outputs(
    tmp_path: Path,
    role: str,
    forbidden_path: str,
) -> None:
    fixture = _fixture(tmp_path)
    arguments: dict[str, Path] = {
        "base_report_path": fixture.base_report,
        "base_registry_path": fixture.base_registry,
        "large_report_path": fixture.large_report,
        "large_registry_path": fixture.large_registry,
        "comparison_commit_path": fixture.comparison_commit,
        "output_markdown_path": fixture.output_markdown,
        "output_json_path": fixture.output_json,
        "output_commit_path": fixture.output_commit,
        "workspace_root": fixture.root,
    }
    if role == "registry":
        arguments["base_registry_path"] = fixture.root / forbidden_path
    elif role == "comparison_commit":
        arguments["comparison_commit_path"] = fixture.root / forbidden_path
    elif role == "prediction":
        report = _read_json(fixture.base_report)
        report["runs"][0]["phone"]["predictions"]["path"] = forbidden_path
        _write_json(fixture.base_report, report)
    elif role == "markdown":
        arguments["output_markdown_path"] = fixture.root / forbidden_path
    elif role == "json":
        arguments["output_json_path"] = fixture.root / forbidden_path
    else:
        arguments["output_commit_path"] = fixture.root / forbidden_path

    with pytest.raises(ResultsValidationError, match="forbidden_path"):
        render_wavlm_results(**arguments)


@pytest.mark.parametrize(
    ("output_role", "target_kind"),
    [
        ("commit", "checkpoint"),
        ("markdown", "training_config"),
        ("json", "trainer"),
        ("commit", "registry_data"),
        ("markdown", "comparison_receipt"),
        ("json", "comparison_config"),
        ("commit", "comparison_module"),
    ],
)
def test_renderer_rejects_output_collision_with_every_transitive_source(
    tmp_path: Path,
    output_role: str,
    target_kind: str,
) -> None:
    fixture = _fixture(tmp_path)
    base_registry = _read_json(fixture.base_registry)
    comparison_commit = _read_json(fixture.comparison_commit)
    comparison = _read_json(fixture.comparison_report)
    if target_kind == "checkpoint":
        target = fixture.base_report.parent / "seed_42/checkpoint.pt"
    elif target_kind == "training_config":
        target = fixture.root / base_registry["inputs"]["config"][0]["path"]
    elif target_kind == "trainer":
        target = fixture.root / base_registry["inputs"]["model"][0]["path"]
    elif target_kind == "registry_data":
        target = fixture.root / base_registry["inputs"]["data"][0]["path"]
    elif target_kind == "comparison_receipt":
        target = fixture.root / comparison_commit["execution_receipt"]["path"]
    else:
        source_key = {
            "comparison_config": "config",
            "comparison_module": "comparison_module",
        }[target_kind]
        target = fixture.root / comparison["sources"][source_key]["path"]
    before = target.read_bytes() if target.exists() else None
    arguments: dict[str, Path] = {
        "base_report_path": fixture.base_report,
        "base_registry_path": fixture.base_registry,
        "large_report_path": fixture.large_report,
        "large_registry_path": fixture.large_registry,
        "comparison_commit_path": fixture.comparison_commit,
        "output_markdown_path": fixture.output_markdown,
        "output_json_path": fixture.output_json,
        "output_commit_path": fixture.output_commit,
        "workspace_root": fixture.root,
    }
    arguments[f"output_{output_role}_path"] = target

    with pytest.raises(ResultsValidationError, match="output_overwrites_input"):
        render_wavlm_results(**arguments)

    if before is None:
        assert not target.exists()
    else:
        assert target.read_bytes() == before


@pytest.mark.parametrize("output_name", ["markdown", "json"])
def test_renderer_rejects_generation_collision_before_write(
    tmp_path: Path,
    output_name: str,
) -> None:
    markdown_path = tmp_path / "rendered/table.md"
    json_path = tmp_path / "rendered/table.json"
    commit_path = tmp_path / "rendered/commit.json"
    markdown = "# synthetic\n"
    payload = {
        "provenance": {
            "comparison_commit": {
                "path": "artifacts/comparison/commit.json",
                "sha256": "0" * 64,
                "size_bytes": 1,
            }
        }
    }
    json_text = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )
    generation_id = hashlib.sha256(
        markdown.encode("utf-8") + b"\0" + json_text.encode("utf-8")
    ).hexdigest()
    base_path = markdown_path if output_name == "markdown" else json_path
    protected = base_path.parent / "generations" / generation_id / base_path.name

    with pytest.raises(ResultsValidationError, match="output_overwrites_input"):
        results_module._publish_outputs(
            tmp_path,
            markdown_path,
            json_path,
            commit_path,
            markdown,
            payload,
            protected_paths={protected},
        )

    assert not list(tmp_path.rglob("*.json"))
    assert not list(tmp_path.rglob("*.md"))


@pytest.mark.parametrize("output_name", ["markdown", "json"])
def test_renderer_rejects_commit_generation_self_collision_before_write(
    tmp_path: Path,
    output_name: str,
) -> None:
    markdown_path = tmp_path / "rendered/table.md"
    json_path = tmp_path / "rendered/table.json"
    markdown = "# synthetic\n"
    payload = {
        "provenance": {
            "comparison_commit": {
                "path": "artifacts/comparison/commit.json",
                "sha256": "0" * 64,
                "size_bytes": 1,
            }
        }
    }
    json_text = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )
    generation_id = hashlib.sha256(
        markdown.encode("utf-8") + b"\0" + json_text.encode("utf-8")
    ).hexdigest()
    base_path = markdown_path if output_name == "markdown" else json_path
    commit_path = base_path.parent / "generations" / generation_id / base_path.name

    with pytest.raises(ResultsValidationError, match="output_paths_collide"):
        results_module._publish_outputs(
            tmp_path,
            markdown_path,
            json_path,
            commit_path,
            markdown,
            payload,
        )

    assert not list(tmp_path.rglob("*.json"))
    assert not list(tmp_path.rglob("*.md"))


def _minimal_output_payload(nonce: int) -> dict[str, Any]:
    return {
        "nonce": nonce,
        "provenance": {
            "comparison_commit": {
                "path": "artifacts/comparison/commit.json",
                "sha256": "0" * 64,
                "size_bytes": 1,
            }
        },
    }


def _assert_output_commit_references_exist(root: Path, commit_path: Path) -> None:
    commit = _read_json(commit_path)
    for record in commit["outputs"].values():
        path = root / record["path"]
        assert path.is_file()
        assert _sha(path) == record["sha256"]
        assert path.stat().st_size == record["size_bytes"]


@pytest.mark.parametrize("file_fsync_index", [1, 2, 3])
def test_renderer_file_fsync_failure_preserves_previous_commit_without_orphans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    file_fsync_index: int,
) -> None:
    markdown_path = tmp_path / "rendered/table.md"
    json_path = tmp_path / "rendered/table.json"
    commit_path = tmp_path / "rendered/commit.json"
    results_module._publish_outputs(
        tmp_path,
        markdown_path,
        json_path,
        commit_path,
        "# old\n",
        _minimal_output_payload(0),
    )
    previous_commit = commit_path.read_bytes()
    previous_files = {
        path.relative_to(tmp_path)
        for path in tmp_path.rglob("*")
        if path.is_file() and path != commit_path
    }
    real_fsync = results_module.os.fsync
    calls = 0

    def fail_selected_file_fsync(descriptor: int) -> None:
        nonlocal calls
        if not stat.S_ISDIR(results_module.os.fstat(descriptor).st_mode):
            calls += 1
            if calls == file_fsync_index:
                raise OSError("injected file fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(results_module.os, "fsync", fail_selected_file_fsync)

    with pytest.raises(OSError, match="injected file fsync failure"):
        results_module._publish_outputs(
            tmp_path,
            markdown_path,
            json_path,
            commit_path,
            "# new\n",
            _minimal_output_payload(1),
        )

    assert commit_path.read_bytes() == previous_commit
    assert {
        path.relative_to(tmp_path)
        for path in tmp_path.rglob("*")
        if path.is_file() and path != commit_path
    } == previous_files
    assert not list(tmp_path.rglob("*.tmp"))
    _assert_output_commit_references_exist(tmp_path, commit_path)


@pytest.mark.parametrize("directory_fsync_index", [1, 2, 3])
def test_renderer_directory_fsync_failure_has_consistent_commit_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    directory_fsync_index: int,
) -> None:
    markdown_path = tmp_path / "rendered/table.md"
    json_path = tmp_path / "rendered/table.json"
    commit_path = tmp_path / "rendered/commit.json"
    results_module._publish_outputs(
        tmp_path,
        markdown_path,
        json_path,
        commit_path,
        "# old\n",
        _minimal_output_payload(0),
    )
    previous_commit = commit_path.read_bytes()
    previous_files = {
        path.relative_to(tmp_path)
        for path in tmp_path.rglob("*")
        if path.is_file() and path != commit_path
    }
    real_fsync_directory = results_module._fsync_directory
    calls = 0

    def fail_selected_directory_fsync(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == directory_fsync_index:
            raise OSError("injected directory fsync failure")
        real_fsync_directory(path)

    monkeypatch.setattr(
        results_module,
        "_fsync_directory",
        fail_selected_directory_fsync,
    )

    with pytest.raises(OSError, match="injected directory fsync failure"):
        results_module._publish_outputs(
            tmp_path,
            markdown_path,
            json_path,
            commit_path,
            "# new\n",
            _minimal_output_payload(1),
        )

    if directory_fsync_index < 3:
        assert commit_path.read_bytes() == previous_commit
        assert {
            path.relative_to(tmp_path)
            for path in tmp_path.rglob("*")
            if path.is_file() and path != commit_path
        } == previous_files
    else:
        assert commit_path.read_bytes() != previous_commit
        assert previous_files.issubset(
            {
                path.relative_to(tmp_path)
                for path in tmp_path.rglob("*")
                if path.is_file() and path != commit_path
            }
        )
    assert not list(tmp_path.rglob("*.tmp"))
    _assert_output_commit_references_exist(tmp_path, commit_path)


def test_second_generation_output_failure_never_commits_mixed_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    real_replace = results_module.os.replace

    def fail_json_generation(source: str | Path, destination: str | Path) -> None:
        target = Path(destination)
        if target.name == fixture.output_json.name and "generations" in target.parts:
            raise OSError("injected second-output failure")
        real_replace(source, destination)

    monkeypatch.setattr(results_module.os, "replace", fail_json_generation)

    with pytest.raises(OSError, match="injected second-output failure"):
        _render(fixture)

    assert not fixture.output_commit.exists()
    generation_files = [
        path for path in fixture.output_commit.parent.rglob("*") if path.is_file()
    ]
    assert generation_files == []


def test_manifest_replace_failure_preserves_previous_committed_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    _render(fixture)
    previous_commit = fixture.output_commit.read_bytes()
    _, _, previous_manifest = load_committed_wavlm_results(
        commit_manifest_path=fixture.output_commit,
        workspace_root=fixture.root,
    )
    previous_generation = previous_manifest["generation_id"]
    real_render_markdown = results_module._render_markdown
    real_replace = results_module.os.replace

    def changed_markdown(*args: Any, **kwargs: Any) -> str:
        return real_render_markdown(*args, **kwargs) + "\n<!-- new generation -->\n"

    def fail_manifest(source: str | Path, destination: str | Path) -> None:
        if Path(destination) == fixture.output_commit:
            raise OSError("injected manifest failure")
        real_replace(source, destination)

    monkeypatch.setattr(results_module, "_render_markdown", changed_markdown)
    monkeypatch.setattr(results_module.os, "replace", fail_manifest)

    with pytest.raises(OSError, match="injected manifest failure"):
        _render(fixture)

    assert fixture.output_commit.read_bytes() == previous_commit
    _, _, committed = load_committed_wavlm_results(
        commit_manifest_path=fixture.output_commit,
        workspace_root=fixture.root,
    )
    assert committed["generation_id"] == previous_generation
    markdown_path, json_path = _committed_output_paths(fixture)
    assert markdown_path.is_file()
    assert json_path.is_file()


def test_cli_fails_closed_without_creating_placeholder_outputs(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    comparison = _read_json(fixture.comparison_report)
    comparison["summary"]["locked_test_remains_unread"] = False
    _write_json(fixture.comparison_report, comparison)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--workspace-root",
            str(fixture.root),
            "--base-report",
            str(fixture.base_report),
            "--base-registry",
            str(fixture.base_registry),
            "--large-report",
            str(fixture.large_report),
            "--large-registry",
            str(fixture.large_registry),
            "--comparison-commit",
            str(fixture.comparison_commit),
            "--output-markdown",
            str(fixture.output_markdown),
            "--output-json",
            str(fixture.output_json),
            "--output-commit",
            str(fixture.output_commit),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "comparison_committed_report_hash_mismatch" in completed.stderr
    assert not fixture.output_markdown.exists()
    assert not fixture.output_json.exists()
    assert not fixture.output_commit.exists()
