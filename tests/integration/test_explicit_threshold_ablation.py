from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import pyarrow.parquet as pq
import yaml


ROOT = Path(__file__).parents[2]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASELINE_TEST = _load(
    "baseline_test_fixture",
    ROOT / "tests" / "integration" / "test_train_explicit_baseline.py",
)
FAILURE = _load(
    "failure_analysis_script",
    ROOT / "scripts" / "analyze_explicit_baseline_failure.py",
)
ABLATION = _load(
    "threshold_ablation_script",
    ROOT / "scripts" / "run_explicit_threshold_ablation.py",
)


def test_failure_analysis_and_registered_train_only_threshold_ablation(
    tmp_path: Path,
) -> None:
    baseline_args = BASELINE_TEST._fixture(tmp_path)
    baseline_report = BASELINE_TEST.MODULE.train_explicit_baseline(baseline_args)
    assert baseline_report["status"] == "completed"
    # The synthetic baseline need not fail its numeric smoke threshold, so
    # explicitly preserve a failed gate in this failure-path fixture.
    report_path = baseline_args.output_dir / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["summary"]["phone"]["smoke_gate_passed_by_mean"] = False
    report["summary"]["phone"]["engineering_gate_passed_by_mean"] = False
    BASELINE_TEST.MODULE.write_json(report_path, report)

    failure_dir = tmp_path / "failure-analysis"
    failure = FAILURE.analyze(
        experiment_dir=baseline_args.output_dir,
        output_dir=failure_dir,
        min_group_n=1,
    )
    assert failure["b06_status"] == "failed_unchanged"
    assert failure["privacy"]["identifier_columns_read"] is False
    assert (failure_dir / "failure_analysis.md").is_file()

    config = yaml.safe_load(
        (
            ROOT
            / "configs"
            / "experiments"
            / "explicit_threshold_ablation_v1.yaml"
        ).read_text(encoding="utf-8")
    )
    config["data"]["modeling_manifest_sha256"] = "a" * 64
    config["model"]["threshold_grid"] = {
        "start": 0.2,
        "stop": 0.8,
        "step": 0.1,
    }
    config_path = tmp_path / "ablation.yaml"
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        config=config_path,
        utterance_cache=baseline_args.utterance_cache,
        phone_cache=baseline_args.phone_cache,
        cache_report=baseline_args.cache_report,
        base_report=report_path,
        failure_analysis=failure_dir / "failure_analysis.json",
        output_dir=tmp_path / "threshold-ablation",
        registry_root=tmp_path / "registry",
        run_id="synthetic-threshold-ablation",
    )

    ablation, metadata_path = ABLATION.run_registered_ablation(args)

    assert ablation["status"] == "completed"
    assert ablation["post_hoc_exploratory"] is True
    assert ablation["b06_status"] == "failed_unchanged"
    assert ablation["summary"]["promotion_decision_allowed"] is False
    assert ablation["experiment_registry"]["t03_complete"] is True
    assert len(ablation["runs"]) == 3
    for run in ablation["runs"]:
        assert run["inner_split"]["speaker_overlap"] == 0
        assert run["selection"]["dev_was_used"] is False
        assert run["primary_dev"]["evaluation_passes"] == 1
        assert run["all_dev_audit"]["same_prediction_pass"] is True
        predictions = pq.read_table(
            args.output_dir
            / run["artifacts"]["predictions"]["path"],
            columns=["split", "primary_scope"],
        )
        assert set(predictions.column("split").to_pylist()) == {"dev"}
        assert sum(predictions.column("primary_scope").to_pylist()) < len(
            predictions
        )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["status"] == "completed"
    assert len(metadata["metrics"]) == 7
    assert len(metadata["artifacts"]) == 10
    assert ".env" not in metadata_path.read_text(encoding="utf-8")
