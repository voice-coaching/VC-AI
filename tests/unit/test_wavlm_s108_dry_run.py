from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

from voice_coach.training import wavlm_s108_dry_run as s108


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs/experiments/wavlm_s108_no_locked_dry_run_v1.yaml"


def _write_json(path: Path, value: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = s108.canonical_json_bytes(value, pretty=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _base_flags() -> dict[str, Any]:
    return {
        "aggregate_only": True,
        "contains_identifiers": False,
        "contains_rows": False,
        "contains_audio": False,
        "contains_labels": False,
        "contains_predictions": False,
        "locked_access_count": 0,
    }


def _copy_static_workspace(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    records: list[dict[str, str]] = []
    for stage in s108.STAGES:
        stage_config = config["predecessors"][stage]
        records.append(stage_config["config"])
        records.append(
            stage_config["validator_source"]
            if stage == "s103"
            else stage_config["validator"]["module"]
        )
    records.extend(config["implementation"].values())
    for record in records:
        source = ROOT / record["path"]
        destination = tmp_path / record["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    target = tmp_path / CONFIG_PATH.relative_to(ROOT)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return target, config


def _copy_s103(tmp_path: Path, config: dict[str, Any]) -> None:
    stage = config["predecessors"]["s103"]
    commit_path = ROOT / stage["commit"]["path"]
    commit = json.loads(commit_path.read_text(encoding="utf-8"))
    paths = [stage["commit"]["path"], commit["comparison_report"]["path"]]
    paths.extend(item["path"] for item in stage["registries"])
    for relative in paths:
        source = ROOT / relative
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def _report(stage: str, previous_sha: str | None) -> dict[str, Any]:
    if stage == "s104":
        assert previous_sha is not None
        return {
            "schema_version": "voice_coach.wavlm_s104_adapter_report.v1",
            "status": "completed",
            "development_only": True,
            "promotion_decision_allowed": False,
            "s103_branch_evidence": {"commit_sha256": previous_sha},
            "leakage": {
                "stack_accessed": False,
                "calibration_accessed": False,
                "locked_test_accessed": False,
            },
        }
    if stage == "s105":
        return {
            "schema_version": "voice_coach.wavlm_s105_hierarchy_comparison.v1",
            "status": "completed",
            "promotion_decision_allowed": False,
            "word_supervision": {
                "word_loss_performed": False,
                "word_pcc_status": "not_evaluable",
            },
            "leakage": {
                "stack_accessed": False,
                "calibration_accessed": False,
                "locked_test_accessed": False,
            },
        }
    if stage == "s106":
        return {
            "schema_version": "voice_coach.wavlm_s106_stack_report.v1",
            "status": "completed",
            "promotion_decision_allowed": False,
            "leakage": {
                "stack_accessed": True,
                "train_accessed": False,
                "dev_accessed": False,
                "calibration_accessed": False,
                "locked_test_accessed": False,
            },
        }
    return {
        "schema_version": "voice_coach.wavlm_s107_calibration_report.v1",
        "status": "completed_provisional_engineering",
        "promotion_decision_allowed": False,
        "s108_ready": False,
        "split_access": {
            "train": False,
            "dev": False,
            "stack": False,
            "calibration": True,
            "locked_test": False,
        },
    }


def _fake_stage(
    tmp_path: Path,
    config: dict[str, Any],
    *,
    stage: str,
    previous_sha: str,
) -> str:
    layout = s108.STAGE_LAYOUT[stage]
    report = _report(stage, previous_sha)
    report_bytes = s108.canonical_json_bytes(report, pretty=True)
    report_name = layout["report_name"]
    artifacts: list[dict[str, Any]] = [
        {
            "name": report_name,
            "path": f"{report_name}.json",
            "sha256": hashlib.sha256(report_bytes).hexdigest(),
            "size_bytes": len(report_bytes),
        }
    ]
    special_names: list[str] = []
    if stage == "s106":
        special_names.append("final_fusion_checkpoint")
    if stage == "s107":
        special_names.extend(["calibrator_parameters", "threshold_policy_report"])
    for name in special_names:
        artifacts.append(
            {
                "name": name,
                "path": f"{name}.json",
                "sha256": hashlib.sha256(name.encode()).hexdigest(),
                "size_bytes": 1,
            }
        )
    while len(artifacts) < layout["artifact_count"]:
        index = len(artifacts)
        artifacts.append(
            {
                "name": f"opaque_artifact_{index}",
                "path": f"opaque/artifact_{index}.parquet",
                "sha256": hashlib.sha256(f"{stage}-{index}".encode()).hexdigest(),
                "size_bytes": index + 1,
            }
        )
    schema = config["predecessors"][stage]["commit"]["schema_version"]
    generation_payload = s108.canonical_json_bytes(
        {"schema_version": schema, "artifacts": artifacts},
        pretty=False,
    )
    compact_newline = s108.STAGE_LAYOUT[stage]["compact_json_trailing_newline"]
    if compact_newline:
        generation_payload += b"\n"
    generation_id = hashlib.sha256(generation_payload).hexdigest()
    generation_path = f"artifacts/experiments/fake_{stage}/generations/{generation_id}"
    _write_json(tmp_path / generation_path / f"{report_name}.json", report)

    registry_count = 5 if stage == "s105" else 1
    registry_records: list[dict[str, Any]] = []
    for index in range(registry_count):
        run_id = f"fake-{stage}-{index}"
        registry_path = f"artifacts/experiment_registry/{run_id}/metadata.json"
        registry = {
            "schema_version": 1,
            "run_id": run_id,
            "status": "completed",
            "failure": None,
            "artifacts": [{"name": f"a-{i}"} for i in range(len(artifacts))],
        }
        registry_sha = _write_json(tmp_path / registry_path, registry)
        registry_records.append(
            {
                "path": registry_path,
                "sha256": registry_sha,
                "run_id": run_id,
                "status": "completed",
                "artifact_count": len(artifacts),
            }
        )
    commit: dict[str, Any] = {
        "schema_version": schema,
        "status": "committed",
        "generation_id": generation_id,
        "generation_path": generation_path,
        "artifacts": artifacts,
    }
    if stage == "s104":
        commit["registry"] = registry_records[0]
        commit["experiment_report_sha256"] = artifacts[0]["sha256"]
    elif stage == "s105":
        commit["registries"] = registry_records
        commit["predecessor"] = {"commit_sha256": previous_sha}
        commit["comparison_report_sha256"] = artifacts[0]["sha256"]
    elif stage == "s106":
        commit["registry"] = registry_records[0]
    else:
        commit["registry"] = registry_records[0]
        commit["s106_commit"] = {"sha256": previous_sha}
        commit["promotion_decision_allowed"] = False
        commit["s108_ready"] = False
    commit["binding_sha256"] = s108._binding_sha256(
        commit,
        trailing_newline=compact_newline,
    )
    return _write_json(
        tmp_path / config["predecessors"][stage]["commit"]["path"],
        commit,
    )


def _external_ready(
    tmp_path: Path,
    config: dict[str, Any],
    hashes: dict[str, str],
) -> None:
    paths = config["external_aggregate_evidence"]
    prereg = {
        "schema_version": "voice_coach.wavlm_s108_preregistration_freeze.v1",
        "status": "frozen",
        **_base_flags(),
        "approved_by_research_lead": True,
        "approved_before_locked_access": True,
        "locked_results_unseen": True,
        "policy": {
            "primary_icc": {
                "outcome": "comprehensibility_score",
                "metric": "icc_2_1",
                "gate_minimum": 0.70,
            },
            "word_and_primary_family": {
                "decision": "approved_protocol_amendment",
                "approved_protocol_amendment": True,
                "protocol_amendment_sha256": "1" * 64,
                "primary_metrics": [
                    "phone_macro_f1",
                    "utterance_primary_icc_2_1",
                ],
                "successes_required": 2,
            },
            "issued_phone_precision": {
                "gate_minimum": 0.90,
                "minimum_issued_phone_count": 30,
                "minimum_issued_coverage": 0.01,
                "ci_method": "percentile_cluster_bootstrap",
                "confidence_level": 0.95,
                "gate_statistic": "ci_lower_bound",
                "cluster_unit": "speaker_id_sha256",
            },
            "subgroup_support": {
                "minimum_speakers_per_group": 5,
                "minimum_positive_phones_per_group": 20,
                "minimum_negative_phones_per_group": 20,
                "missing_group_policy": "exclude_missing_from_primary_report_separately",
            },
            "one_shot_batch": {
                "includes_models": ["b05_explicit_baseline", "s108_final_s1"],
                "access_count": 1,
                "result_based_reselection_allowed": False,
                "rerun_after_gate_failure_allowed": False,
            },
        },
        "frozen_hashes": hashes,
    }
    _write_json(tmp_path / paths["preregistration"]["attestation_path"], prereg)
    progress = {
        "schema_version": "voice_coach.alignment_review_report.v1",
        "status": "complete",
        "expected_count": 240,
        "completed_count": 240,
        "correct_count": 230,
        "privacy": {"raw_paths_or_utterance_ids_exported": False},
    }
    _write_json(tmp_path / paths["g04"]["progress_path"], progress)
    g04 = {
        "schema_version": "voice_coach.g04_alignment_completion_attestation.v1",
        "status": "passed",
        **_base_flags(),
        "expected_count": 240,
        "completed_count": 240,
        "correct_count": 230,
        "alignment_accuracy": 230 / 240,
        "scorer_status": "passed",
        "privacy_safe_report_sha256": "2" * 64,
        "reviewer_qualification_confirmed": True,
        "research_lead_approved": True,
    }
    _write_json(tmp_path / paths["g04"]["attestation_path"], g04)
    d07 = {
        "schema_version": "voice_coach.d07_gold_completion_attestation.v1",
        "status": "completed",
        **_base_flags(),
        "calibration_count": 800,
        "locked_gold_count": 1000,
        "independent_rater_count": 2,
        "adjudicator_count": 1,
        "two_original_ratings_retained": True,
        "adjudication_completed": True,
        "scalar_agreement": 0.71,
        "taxonomy_agreement": 0.66,
        "agreement_gate_passed": True,
        "label_definition_frozen": True,
        "model_results_unseen": True,
        "research_lead_approved": True,
        "calibration_gold_sha256": "3" * 64,
        "sealed_locked_gold_sha256": "4" * 64,
    }
    _write_json(tmp_path / paths["d07"]["attestation_path"], d07)


def _ready_workspace(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    config_path, config = _copy_static_workspace(tmp_path)
    _copy_s103(tmp_path, config)
    previous = config["predecessors"]["s103"]["commit"]["sha256"]
    evidence: list[s108.StageEvidence] = [
        s108.StageEvidence(
            "s103", "valid", None, previous, "1" * 64, (), "2" * 64, {}, "aggregate"
        )
    ]
    artifact_hashes: dict[str, dict[str, str]] = {}
    for stage in ("s104", "s105", "s106", "s107"):
        previous = _fake_stage(
            tmp_path,
            config,
            stage=stage,
            previous_sha=previous,
        )
        commit = json.loads(
            (tmp_path / config["predecessors"][stage]["commit"]["path"]).read_text()
        )
        artifact_hashes[stage] = {
            item["name"]: item["sha256"] for item in commit["artifacts"]
        }
        evidence.append(
            s108.StageEvidence(
                stage,
                "valid",
                None,
                previous,
                commit["generation_id"],
                (),
                None,
                artifact_hashes[stage],
                "aggregate",
            )
        )
    hashes = dict(s108._expected_hash_bindings(evidence) or {})
    _external_ready(tmp_path, config, hashes)
    return config_path, config


def test_repository_static_config_has_zero_artifact_access() -> None:
    config = s108.load_config(CONFIG_PATH)
    result = s108.validate_static_config(
        workspace_root=ROOT,
        config_path=CONFIG_PATH,
        config=config,
    )
    assert result["status"] == "valid"
    assert result["protected_data_access_count"] == 0
    assert result["locked_access_count"] == 0
    assert result["output_count"] == 0


def test_actual_s103_commit_uses_pinned_trailing_newline_binding() -> None:
    document = json.loads(
        (
            ROOT / "artifacts/experiments/"
            "aihub71490_wavlm_frozen_head_comparison_v1/commit.json"
        ).read_text()
    )
    assert document["binding_sha256"] == s108._binding_sha256(
        document,
        trailing_newline=True,
        compact=False,
    )


def test_current_missing_successor_reports_engineering_without_locked_access(
    tmp_path: Path,
) -> None:
    config_path, config = _copy_static_workspace(tmp_path)
    _copy_s103(tmp_path, config)
    progress_path = (
        tmp_path / config["external_aggregate_evidence"]["g04"]["progress_path"]
    )
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        ROOT / config["external_aggregate_evidence"]["g04"]["progress_path"],
        progress_path,
    )
    checklist = s108.build_no_locked_checklist(
        workspace_root=tmp_path,
        config_path=config_path,
        config=config,
    )
    assert checklist["overall_state"] == "engineering_in_progress"
    assert checklist["ready_for_locked_batch"] is False
    assert checklist["external_gates"]["preregistration"]["state"] == (
        "blocked_preregistration"
    )
    assert checklist["external_gates"]["g04"]["state"] == "blocked_g04"
    assert checklist["external_gates"]["d07"]["state"] == "blocked_d07"
    assert checklist["access_audit"]["locked_access_count"] == 0
    assert checklist["access_audit"]["row_level_artifact_read_count"] == 0


def test_bounded_synthetic_ready_e2e_and_deep_validation(tmp_path: Path) -> None:
    config_path, _ = _ready_workspace(tmp_path)
    published = s108.run_no_locked_dry_run(
        workspace_root=tmp_path,
        config_path=config_path,
    )
    assert published.checklist["overall_state"] == "ready_for_locked_batch"
    assert (
        published.checklist["locked_batch_launch_allowed_by_this_implementation"]
        is False
    )
    result = s108.validate_dry_run_receipt(
        workspace_root=tmp_path,
        config_path=config_path,
        receipt_path=published.receipt_path,
    )
    assert result["status"] == "valid"
    assert result["historical_snapshot"] is False
    assert result["ready_for_locked_batch"] is True
    assert result["s108_completion_claim_allowed"] is False
    assert result["locked_access_count"] == 0


@pytest.mark.parametrize(
    ("gate", "expected_state"),
    [
        ("preregistration", "blocked_preregistration"),
        ("g04", "blocked_g04"),
        ("d07", "blocked_d07"),
    ],
)
def test_each_external_gate_blocks_fail_closed(
    tmp_path: Path,
    gate: str,
    expected_state: str,
) -> None:
    config_path, config = _ready_workspace(tmp_path)
    path = config["external_aggregate_evidence"][gate]["attestation_path"]
    (tmp_path / path).unlink()
    checklist = s108.build_no_locked_checklist(
        workspace_root=tmp_path,
        config_path=config_path,
        config=config,
    )
    assert checklist["overall_state"] == expected_state
    assert checklist["ready_for_locked_batch"] is False
    assert checklist["s108_score_allowed"] is False


def test_config_duplicate_key_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.yaml"
    path.write_text("schema_version: 1\nschema_version: 1\n", encoding="utf-8")
    with pytest.raises(s108.S108DryRunError, match="duplicate key"):
        s108.load_config(path)


def test_config_symlink_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target.yaml"
    target.write_text("schema_version: 1\n", encoding="utf-8")
    link = tmp_path / "config.yaml"
    link.symlink_to(target)
    with pytest.raises(s108.S108DryRunError, match="non-symlink"):
        s108.load_config(link)


def test_static_source_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    config_path, config = _copy_static_workspace(tmp_path)
    source = tmp_path / config["implementation"]["preflight_script"]["path"]
    source.write_text("changed", encoding="utf-8")
    with pytest.raises(s108.S108DryRunError, match="source hash differs"):
        s108.validate_static_config(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )


def test_static_symlink_source_is_rejected(tmp_path: Path) -> None:
    config_path, config = _copy_static_workspace(tmp_path)
    source = tmp_path / config["implementation"]["preflight_script"]["path"]
    source.unlink()
    source.symlink_to("/dev/null")
    with pytest.raises(s108.S108DryRunError, match="symlink"):
        s108.validate_static_config(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )


def test_traversal_artifact_metadata_is_rejected() -> None:
    artifact = {
        "name": "report",
        "path": "../report.json",
        "sha256": "1" * 64,
        "size_bytes": 1,
    }
    with pytest.raises(s108.S108DryRunError, match="normalized"):
        s108._artifact_index(
            artifacts=[artifact],
            expected_count=1,
            commit_schema="schema",
            generation_id="1" * 64,
            stage="synthetic",
            compact_json_trailing_newline=False,
        )


def test_aggregate_reader_detects_toctou_change(tmp_path: Path) -> None:
    path = tmp_path / "aggregate.json"
    _write_json(path, {"status": "one"})
    reader = s108.AggregateReader(tmp_path)
    reader.json(
        "aggregate.json",
        category="external_attestation_aggregate",
        name="aggregate",
    )
    _write_json(path, {"status": "two"})
    with pytest.raises(s108.S108DryRunError, match="changed before publication"):
        reader.revalidate()


@pytest.mark.parametrize(
    "fault",
    [
        "after_temporary_write",
        "before_generation_publish",
        "after_generation_publish",
        "before_receipt_publish",
    ],
)
def test_fault_injection_never_publishes_partial_receipt(
    tmp_path: Path,
    fault: str,
) -> None:
    config_path, config = _ready_workspace(tmp_path)
    checklist = s108.build_no_locked_checklist(
        workspace_root=tmp_path,
        config_path=config_path,
        config=config,
    )
    with pytest.raises(s108.S108PublicationError, match="injected fault"):
        s108.publish_no_locked_checklist(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
            checklist=checklist,
            fault_injection=fault,
        )
    generation_root = tmp_path / config["outputs"]["generation_root"]
    if generation_root.exists():
        assert not any(
            path.name.startswith(".part-") for path in generation_root.iterdir()
        )
    receipt_root = tmp_path / config["outputs"]["receipt_root"]
    assert not receipt_root.exists() or list(receipt_root.iterdir()) == []


def test_recovery_after_generation_publish_fault(tmp_path: Path) -> None:
    config_path, config = _ready_workspace(tmp_path)
    checklist = s108.build_no_locked_checklist(
        workspace_root=tmp_path,
        config_path=config_path,
        config=config,
    )
    with pytest.raises(s108.S108PublicationError):
        s108.publish_no_locked_checklist(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
            checklist=checklist,
            fault_injection="after_generation_publish",
        )
    recovered = s108.publish_no_locked_checklist(
        workspace_root=tmp_path,
        config_path=config_path,
        config=config,
        checklist=checklist,
    )
    assert recovered.created_generation is False
    assert recovered.created_receipt is True
    assert (
        s108.validate_dry_run_receipt(
            workspace_root=tmp_path,
            config_path=config_path,
            receipt_path=recovered.receipt_path,
        )["status"]
        == "valid"
    )


def test_concurrent_publication_converges_to_one_generation(tmp_path: Path) -> None:
    config_path, _ = _ready_workspace(tmp_path)

    def run() -> s108.PublishedDryRun:
        return s108.run_no_locked_dry_run(
            workspace_root=tmp_path,
            config_path=config_path,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert results[0].generation_id == results[1].generation_id
    assert sum(int(item.created_generation) for item in results) == 1
    assert sum(int(item.created_receipt) for item in results) == 1


def test_existing_different_receipt_is_never_overwritten(tmp_path: Path) -> None:
    config_path, _ = _ready_workspace(tmp_path)
    published = s108.run_no_locked_dry_run(
        workspace_root=tmp_path,
        config_path=config_path,
    )
    published.receipt_path.write_text('{"tampered":true}\n', encoding="utf-8")
    with pytest.raises(s108.S108PublicationError, match="overwrite"):
        s108.run_no_locked_dry_run(
            workspace_root=tmp_path,
            config_path=config_path,
        )


def test_checklist_rejects_nonzero_locked_access(tmp_path: Path) -> None:
    config_path, config = _ready_workspace(tmp_path)
    checklist = dict(
        s108.build_no_locked_checklist(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )
    )
    checklist["access_audit"] = dict(checklist["access_audit"])
    checklist["access_audit"]["locked_access_count"] = 1
    with pytest.raises(s108.S108DryRunError, match="locked_access_count"):
        s108.validate_checklist_document(checklist)


def test_checklist_cannot_claim_score_or_completion(tmp_path: Path) -> None:
    config_path, config = _ready_workspace(tmp_path)
    checklist = dict(
        s108.build_no_locked_checklist(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )
    )
    checklist["s108_score_allowed"] = True
    with pytest.raises(s108.S108DryRunError, match="s108_score_allowed"):
        s108.validate_checklist_document(checklist)


def test_invalid_prereg_hash_binding_remains_blocked(tmp_path: Path) -> None:
    config_path, config = _ready_workspace(tmp_path)
    path = (
        tmp_path
        / config["external_aggregate_evidence"]["preregistration"]["attestation_path"]
    )
    document = json.loads(path.read_text())
    document["frozen_hashes"]["s107_commit_sha256"] = "f" * 64
    _write_json(path, document)
    checklist = s108.build_no_locked_checklist(
        workspace_root=tmp_path,
        config_path=config_path,
        config=config,
    )
    assert checklist["overall_state"] == "blocked_preregistration"
    assert (
        "hash bindings differ"
        in checklist["external_gates"]["preregistration"]["reason"]
    )


def test_predecessor_commit_binding_tamper_is_rejected(tmp_path: Path) -> None:
    config_path, config = _ready_workspace(tmp_path)
    path = tmp_path / config["predecessors"]["s105"]["commit"]["path"]
    document = json.loads(path.read_text())
    document["selected_variant"] = "tampered"
    _write_json(path, document)
    with pytest.raises(s108.S108DryRunError, match="commit binding"):
        s108.build_no_locked_checklist(
            workspace_root=tmp_path,
            config_path=config_path,
            config=config,
        )


def test_invalid_g04_count_remains_blocked(tmp_path: Path) -> None:
    config_path, config = _ready_workspace(tmp_path)
    path = tmp_path / config["external_aggregate_evidence"]["g04"]["attestation_path"]
    document = json.loads(path.read_text())
    document["correct_count"] = 227
    document["alignment_accuracy"] = 227 / 240
    _write_json(path, document)
    checklist = s108.build_no_locked_checklist(
        workspace_root=tmp_path,
        config_path=config_path,
        config=config,
    )
    assert checklist["overall_state"] == "blocked_g04"


def test_invalid_d07_agreement_remains_blocked(tmp_path: Path) -> None:
    config_path, config = _ready_workspace(tmp_path)
    path = tmp_path / config["external_aggregate_evidence"]["d07"]["attestation_path"]
    document = json.loads(path.read_text())
    document["scalar_agreement"] = 0.69
    document["taxonomy_agreement"] = 0.64
    document["agreement_gate_passed"] = False
    _write_json(path, document)
    checklist = s108.build_no_locked_checklist(
        workspace_root=tmp_path,
        config_path=config_path,
        config=config,
    )
    assert checklist["overall_state"] == "blocked_d07"


def test_aggregate_validation_opens_no_parquet_audio_or_prediction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, config = _ready_workspace(tmp_path)
    opened: list[Path] = []
    original = s108._regular_file_bytes

    def audited(path: Path, name: str) -> bytes:
        opened.append(path)
        return original(path, name)

    monkeypatch.setattr(s108, "_regular_file_bytes", audited)
    checklist = s108.build_no_locked_checklist(
        workspace_root=tmp_path,
        config_path=config_path,
        config=config,
    )
    assert checklist["ready_for_locked_batch"] is True
    assert not any(path.suffix in {".parquet", ".wav", ".flac"} for path in opened)
    assert not any("prediction" in path.name for path in opened)
