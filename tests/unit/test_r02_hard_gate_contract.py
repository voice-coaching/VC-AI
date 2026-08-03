from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from voice_coach.references import r02_hard_gate_contract as contract


WORKSPACE = Path(__file__).resolve().parents[2]
CONFIG_PATH = WORKSPACE / "configs/experiments/r02_candidate_hard_gate_v1.yaml"


def _config() -> dict:
    return dict(contract.load_config(CONFIG_PATH))


def _candidate_rows() -> list[dict]:
    rows: list[dict] = []
    counter = 1
    for sentence_id in contract.SENTENCE_IDS:
        for within_sentence in range(8):
            rows.append(
                {
                    "candidate_id": f"r01c_{counter:032x}",
                    "sentence_id": sentence_id,
                    "target_text_sha256": f"{int(sentence_id[-2:]):064x}",
                    "source_kind": contract.SOURCE_KINDS[within_sentence % 2],
                    "source_attestation_sha256": (
                        "1" * 64 if within_sentence % 2 == 0 else "2" * 64
                    ),
                    "speaker_id_sha256": f"{counter + 1000:064x}",
                    "audio_object_id_sha256": f"{counter + 2000:064x}",
                    "audio_sha256": f"{counter + 3000:064x}",
                    "preservation_group_id_sha256": f"{counter + 4000:064x}",
                    "speed_bucket": ("slow", "neutral", "fast")[within_sentence % 3],
                    "pitch_bucket": ("low", "neutral", "high")[within_sentence % 3],
                }
            )
            counter += 1
    return rows


def _policy() -> dict:
    gates = {
        name: {
            "metric_id": f"metric_{name}",
            "metric_definition_sha256": f"{index + 3:064x}",
            "threshold_operator": "ge",
            "threshold_value": 0.5,
            "normalization_id": f"normalization_{name}",
            "segmentation_id": f"segmentation_{name}",
            "minimum_support": 1,
        }
        for index, name in enumerate(contract.GATE_NAMES)
    }
    return {
        "minimum_eligible_candidates_per_sentence": 1,
        "aggregate_transcript_error_metric_id": "aggregate_transcript_error",
        "aggregate_transcript_error_metric_definition_sha256": "8" * 64,
        "aggregate_transcript_error_aggregation": "candidate_error_rate",
        "gates": gates,
    }


def _report(rows: list[dict], policy: dict) -> dict:
    results: list[dict] = []
    for row in rows:
        result: dict = {
            "candidate_id": row["candidate_id"],
            "sentence_id": row["sentence_id"],
            "target_text_sha256": row["target_text_sha256"],
            "overall_decision": "eligible",
        }
        for name in contract.GATE_NAMES:
            gate = policy["gates"][name]
            result[name] = {
                "metric_id": gate["metric_id"],
                "metric_definition_sha256": gate["metric_definition_sha256"],
                "value": 1.0,
                "threshold_operator": gate["threshold_operator"],
                "threshold_value": gate["threshold_value"],
                "support": 1,
                "decision": "pass",
                "evidence_sha256": "9" * 64,
            }
        results.append(result)
    return {
        "schema_version": "voice_coach.r02_hard_gate_report.v1",
        "report_id": "r02-hard-gate-" + "a" * 32,
        "status": "completed_private_gate",
        "privacy_zone": "D1_pseudonymized_restricted_experiment",
        "r01_completion_commit_sha256": "a" * 64,
        "r01_candidate_manifest_sha256": "b" * 64,
        "preregistration_sha256": "c" * 64,
        "asr_attestation_sha256": "d" * 64,
        "artifact_attestation_sha256": "e" * 64,
        "blind_gate_input": True,
        "source_labels_present": False,
        "same_gate_for_human_and_tts": True,
        "input_candidate_count": len(rows),
        "results": results,
        "eligible_candidate_ids": [row["candidate_id"] for row in rows],
        "quarantined_candidate_ids": [],
        "sentence_summaries": [
            {
                "sentence_id": sentence_id,
                "input_count": 8,
                "eligible_count": 8,
                "quarantined_count": 0,
                "minimum_eligible_support_met": True,
            }
            for sentence_id in contract.SENTENCE_IDS
        ],
        "aggregate_transcript_error": {
            "metric_id": "aggregate_transcript_error",
            "metric_definition_sha256": "8" * 64,
            "aggregation": "candidate_error_rate",
            "value": 0.0,
            "threshold_operator": "le",
            "threshold_value": 0.02,
            "support": len(rows),
            "decision": "pass",
            "evidence_sha256": "f" * 64,
        },
        "failed_candidates_preserved_in_d1": True,
        "r03_may_read_only_eligible_candidates": True,
        "publication_allowed": False,
        "r02_completion_claim_allowed": True,
    }


def _validate_report(payload: dict, rows: list[dict], policy: dict) -> dict:
    return dict(
        contract.validate_gate_report_payload(
            payload,
            candidate_rows=rows,
            r01_completion_commit_sha256="a" * 64,
            r01_candidate_manifest_sha256="b" * 64,
            preregistration_sha256="c" * 64,
            asr_attestation_sha256="d" * 64,
            artifact_attestation_sha256="e" * 64,
            preregistered_policy=policy,
        )
    )


def test_canonical_config_only_has_zero_runtime_access() -> None:
    audit = contract.AccessAudit()
    static = contract.validate_static_config(
        workspace_root=WORKSPACE,
        config_path=CONFIG_PATH,
        config=_config(),
        audit=audit,
    )
    result = contract.config_only_result(static, audit)

    assert result["status"] == "valid_config_only"
    assert result["source_pin_count"] == 16
    assert result["source_pin_read_count"] == 16
    for name in (
        "r00_deep_validation_count",
        "r01_completion_commit_read_attempt_count",
        "r01_candidate_manifest_read_count",
        "preregistration_read_attempt_count",
        "asr_attestation_read_attempt_count",
        "artifact_attestation_read_attempt_count",
        "d1_audio_root_path_resolution_count",
        "d1_audio_read_count",
        "model_path_resolution_count",
        "model_weight_read_count",
        "model_download_count",
        "gpu_access_count",
        "metric_execution_count",
        "s1_artifact_read_count",
        "s2_artifact_read_count",
        "gold_or_locked_test_read_count",
        "source_label_gate_input_read_count",
        "output_path_resolution_count",
        "output_write_count",
    ):
        assert result[name] == 0


def test_static_policy_is_fail_closed_tbd_and_same_for_both_sources() -> None:
    config = _config()
    assert config["hard_gates"]["order"] == list(contract.GATE_NAMES)
    assert all(
        config["hard_gates"][name]["threshold"] == contract.TBD_PREREGISTRATION
        for name in contract.GATE_NAMES
    )
    assert config["blindness"]["gate_input_is_source_blind"] is True
    assert config["blindness"]["source_kind_in_gate_input_allowed"] is False
    assert (
        config["blindness"][
            "human_and_tts_use_identical_gate_metric_threshold_normalization_and_support"
        ]
        is True
    )
    assert config["leakage_boundary"]["locked_test_split_allowed"] is False
    assert (
        config["leakage_boundary"][
            "r00_sentence_target_phones_and_rules_for_coverage_audit_only"
        ]
        is True
    )


def test_templates_do_not_fake_approval_or_exact_artifacts() -> None:
    prereg = contract.load_json(
        WORKSPACE / "configs/reference/r02_gate_preregistration_v1.template.json"
    )
    asr = contract.load_json(
        WORKSPACE / "configs/reference/r02_asr_attestation_v1.template.json"
    )
    artifact = contract.load_json(
        WORKSPACE / "configs/reference/r02_artifact_attestation_v1.template.json"
    )

    assert prereg["status"] == contract.TBD_PREREGISTRATION
    assert prereg["approved_by_external_human"] is False
    assert asr["status"] == contract.TBD_ATTESTATION
    assert asr["research_use_authorized"] is False
    assert asr["s1_or_s2_artifact_used"] is False
    assert artifact["status"] == contract.TBD_ATTESTATION
    assert artifact["same_pipeline_for_human_and_tts"] is True
    assert (
        artifact["target_phone_pipeline"]["gold_or_locked_test_artifact_used"] is False
    )


def test_report_schema_has_no_source_label_plaintext_or_s1_s2_fields() -> None:
    schema = contract.load_json(
        WORKSPACE / "configs/reference/r02_hard_gate_report_v1.schema.json"
    )
    leaked = contract.FORBIDDEN_REPORT_KEYS & contract._collect_keys(schema)

    assert leaked == set()
    assert set(schema["properties"]) == contract.REPORT_KEYS
    assert schema["properties"]["source_labels_present"]["const"] is False
    assert schema["properties"]["publication_allowed"]["const"] is False


def test_valid_report_preserves_all_input_and_exposes_only_eligible_to_r03() -> None:
    rows = _candidate_rows()
    policy = _policy()
    summary = _validate_report(_report(rows, policy), rows, policy)

    assert summary["candidate_count"] == 160
    assert summary["eligible_count"] == 160
    assert summary["quarantined_count"] == 0
    assert summary["publication_allowed"] is False


def test_single_gate_failure_is_quarantined_and_preserved() -> None:
    rows = _candidate_rows()
    policy = _policy()
    report = _report(rows, policy)
    first_id = rows[0]["candidate_id"]
    report["results"][0]["asr_content_match"]["value"] = 0.0
    report["results"][0]["asr_content_match"]["decision"] = "fail"
    report["results"][0]["overall_decision"] = "quarantined"
    report["eligible_candidate_ids"] = report["eligible_candidate_ids"][1:]
    report["quarantined_candidate_ids"] = [first_id]
    report["sentence_summaries"][0].update(
        {"eligible_count": 7, "quarantined_count": 1}
    )

    summary = _validate_report(report, rows, policy)
    assert summary["eligible_count"] == 159
    assert summary["quarantined_candidate_ids"] == (first_id,)
    assert report["failed_candidates_preserved_in_d1"] is True


def test_report_rejects_source_label_leak() -> None:
    rows = _candidate_rows()
    policy = _policy()
    report = _report(rows, policy)
    report["results"][0]["source_kind"] = "human_recording"

    with pytest.raises(contract.R02ContractError, match="forbidden fields"):
        _validate_report(report, rows, policy)


def test_report_rejects_source_specific_gate_override() -> None:
    rows = _candidate_rows()
    policy = _policy()
    report = _report(rows, policy)
    report["results"][0]["asr_content_match"]["threshold_value"] = 0.4

    with pytest.raises(contract.R02ContractError, match="threshold"):
        _validate_report(report, rows, policy)


def test_report_rejects_false_pass_and_insufficient_metric_support() -> None:
    rows = _candidate_rows()
    policy = _policy()
    policy["gates"]["target_phone_coverage"]["minimum_support"] = 2
    report = _report(rows, policy)

    with pytest.raises(contract.R02ContractError, match="decision"):
        _validate_report(report, rows, policy)


def test_report_rejects_nonfinite_and_aggregate_boundary_failure() -> None:
    rows = _candidate_rows()
    policy = _policy()
    report = _report(rows, policy)
    report["results"][0]["pause_rhythm"]["value"] = float("nan")
    with pytest.raises(contract.R02ContractError, match="finite"):
        _validate_report(report, rows, policy)

    report = _report(rows, policy)
    report["aggregate_transcript_error"]["value"] = 0.03
    report["aggregate_transcript_error"]["decision"] = "fail"
    with pytest.raises(contract.R02ContractError, match="0.02"):
        _validate_report(report, rows, policy)


def test_report_rejects_below_preregistered_per_sentence_eligible_support() -> None:
    rows = _candidate_rows()
    policy = _policy()
    policy["minimum_eligible_candidates_per_sentence"] = 8
    report = _report(rows, policy)
    first_id = rows[0]["candidate_id"]
    report["results"][0]["acoustic_artifact"]["value"] = 0.0
    report["results"][0]["acoustic_artifact"]["decision"] = "fail"
    report["results"][0]["overall_decision"] = "quarantined"
    report["eligible_candidate_ids"] = report["eligible_candidate_ids"][1:]
    report["quarantined_candidate_ids"] = [first_id]
    report["sentence_summaries"][0].update(
        {
            "eligible_count": 7,
            "quarantined_count": 1,
            "minimum_eligible_support_met": False,
        }
    )

    with pytest.raises(contract.R02ContractError, match="insufficient eligible"):
        _validate_report(report, rows, policy)


def test_partition_manifest_rejects_quarantine_as_r03_input() -> None:
    payload = {
        "schema_version": "voice_coach.r02_quarantine_manifest.v1",
        "status": "quarantined_preserved_in_d1",
        "privacy_zone": "D1_pseudonymized_restricted_experiment",
        "gate_report_sha256": "a" * 64,
        "candidate_count": 1,
        "candidate_ids": ["r01c_" + "1" * 32],
        "source_labels_present": False,
        "r03_input_allowed": True,
        "publication_allowed": False,
    }

    with pytest.raises(contract.R02ContractError, match="R03 access"):
        contract.validate_partition_manifest_payload(
            payload,
            gate_report_sha256="a" * 64,
            expected_candidate_ids=payload["candidate_ids"],
            eligible=False,
        )


def test_static_rejects_partial_r01_and_prerequisite_pins() -> None:
    config = _config()
    config["predecessor_r01"]["completion_commit_sha256"] = "a" * 64
    with pytest.raises(contract.R02ContractError, match="all TBD or all exact"):
        contract._validate_static_values(config)

    config = _config()
    config["prerequisites"]["preregistration"]["sha256"] = "a" * 64
    with pytest.raises(contract.R02ContractError, match="all TBD or all exact"):
        contract._validate_static_values(config)


def test_resolved_preregistration_and_attestations_bind_exactly() -> None:
    config = _config()
    for field_name in (
        "completion_commit_sha256",
        "registry_sha256",
        "candidate_manifest_sha256",
        "human_attestation_sha256",
        "cosyvoice_attestation_sha256",
    ):
        config["predecessor_r01"][field_name] = "a" * 64

    prereg = deepcopy(
        contract.load_json(
            WORKSPACE / "configs/reference/r02_gate_preregistration_v1.template.json"
        )
    )
    prereg.update(
        {
            "preregistration_id": "r02-preregistered-test",
            "status": "approved_locked_before_r02_audio_access",
            "approved_document_sha256": "1" * 64,
            "approved_by_external_human": True,
            "locked_before_r02_audio_access": True,
        }
    )
    policy = _policy()
    prereg["candidate_policy"].update(
        {
            "minimum_eligible_candidates_per_sentence": 1,
            "aggregate_transcript_error_metric_id": (
                policy["aggregate_transcript_error_metric_id"]
            ),
            "aggregate_transcript_error_metric_definition_sha256": (
                policy["aggregate_transcript_error_metric_definition_sha256"]
            ),
            "aggregate_transcript_error_aggregation": (
                policy["aggregate_transcript_error_aggregation"]
            ),
        }
    )
    prereg["gates"] = deepcopy(policy["gates"])
    prereg["license_decisions"] = {
        "asr_code_license": "MIT",
        "asr_checkpoint_license": "MIT",
        "artifact_code_license": "MIT",
        "artifact_checkpoint_license": "research-only",
        "pause_rhythm_pipeline_license": "MIT",
        "target_phone_code_license": "MIT",
        "target_phone_checkpoint_license": "research-only",
    }

    asr = deepcopy(
        contract.load_json(
            WORKSPACE / "configs/reference/r02_asr_attestation_v1.template.json"
        )
    )
    asr.update(
        {
            "attestation_id": "r02-asr-test",
            "status": "approved_for_r02_content_gate",
            "exact_model_id": "openai/whisper-large-v3",
            "exact_checkpoint_revision": "revision-asr",
            "checkpoint_manifest_sha256": "2" * 64,
            "code_revision": "revision-code",
            "code_license_spdx": "MIT",
            "code_license_evidence_sha256": "3" * 64,
            "checkpoint_license_spdx": "MIT",
            "checkpoint_license_evidence_sha256": "4" * 64,
            "research_use_authorized": True,
            "normalization_id": policy["gates"]["asr_content_match"][
                "normalization_id"
            ],
            "normalization_spec_sha256": "5" * 64,
            "segmentation_id": policy["gates"]["asr_content_match"]["segmentation_id"],
            "segmentation_spec_sha256": "6" * 64,
            "local_bundle_manifest_sha256": "7" * 64,
            "runtime_environment_lock_sha256": "8" * 64,
            "approved_by_external_human": True,
        }
    )

    artifact = deepcopy(
        contract.load_json(
            WORKSPACE / "configs/reference/r02_artifact_attestation_v1.template.json"
        )
    )
    artifact.update(
        {
            "attestation_id": "r02-artifact-test",
            "status": "approved_for_r02_noncontent_gates",
            "approved_by_external_human": True,
        }
    )
    acoustic = artifact["acoustic_artifact"]
    acoustic.update(
        {
            "exact_model_id": "utmosv2-exact",
            "exact_checkpoint_revision": "revision-artifact",
            "checkpoint_manifest_sha256": "9" * 64,
            "code_revision": "revision-artifact-code",
            "code_license_spdx": "MIT",
            "code_license_evidence_sha256": "a" * 64,
            "checkpoint_license_spdx": "research-only",
            "checkpoint_license_evidence_sha256": "b" * 64,
            "normalization_id": policy["gates"]["acoustic_artifact"][
                "normalization_id"
            ],
            "normalization_spec_sha256": "c" * 64,
            "segmentation_id": policy["gates"]["acoustic_artifact"]["segmentation_id"],
            "segmentation_spec_sha256": "d" * 64,
            "research_use_authorized": True,
        }
    )
    pause = artifact["pause_rhythm_pipeline"]
    pause.update(
        {
            "implementation_id": "pause-pipeline",
            "implementation_revision": "revision-pause",
            "implementation_sha256": "e" * 64,
            "license_spdx": "MIT",
            "license_evidence_sha256": "f" * 64,
            "normalization_id": policy["gates"]["pause_rhythm"]["normalization_id"],
            "normalization_spec_sha256": "1" * 64,
            "segmentation_id": policy["gates"]["pause_rhythm"]["segmentation_id"],
            "segmentation_spec_sha256": "2" * 64,
            "research_use_authorized": True,
        }
    )
    phone = artifact["target_phone_pipeline"]
    phone.update(
        {
            "implementation_id": "phone-pipeline",
            "implementation_revision": "revision-phone",
            "implementation_sha256": "3" * 64,
            "model_checkpoint_revision": "revision-phone-model",
            "model_checkpoint_manifest_sha256": "4" * 64,
            "code_license_spdx": "MIT",
            "checkpoint_license_spdx": "research-only",
            "license_evidence_sha256": "5" * 64,
            "normalization_id": policy["gates"]["target_phone_coverage"][
                "normalization_id"
            ],
            "normalization_spec_sha256": "6" * 64,
            "segmentation_id": policy["gates"]["target_phone_coverage"][
                "segmentation_id"
            ],
            "segmentation_spec_sha256": "7" * 64,
            "research_use_authorized": True,
        }
    )
    artifact["runtime"].update(
        {
            "local_bundle_manifest_sha256": "8" * 64,
            "runtime_environment_lock_sha256": "9" * 64,
        }
    )

    config["prerequisites"]["preregistration"]["sha256"] = "b" * 64
    config["prerequisites"]["asr_attestation"]["sha256"] = "c" * 64
    config["prerequisites"]["artifact_attestation"]["sha256"] = "d" * 64
    for gate_name in contract.GATE_NAMES:
        source = policy["gates"][gate_name]
        config["hard_gates"][gate_name].update(
            {
                "metric_id": source["metric_id"],
                "threshold": {
                    "operator": source["threshold_operator"],
                    "value": source["threshold_value"],
                },
                "normalization_id": source["normalization_id"],
                "segmentation_id": source["segmentation_id"],
                "minimum_support": source["minimum_support"],
            }
        )
    config["hard_gates"]["asr_content_match"].update(
        {
            "exact_model_id": asr["exact_model_id"],
            "exact_checkpoint_revision": asr["exact_checkpoint_revision"],
            "code_and_checkpoint_license": {
                "code_spdx": "MIT",
                "code_evidence_sha256": asr["code_license_evidence_sha256"],
                "checkpoint_spdx": "MIT",
                "checkpoint_evidence_sha256": (
                    asr["checkpoint_license_evidence_sha256"]
                ),
            },
        }
    )
    config["hard_gates"]["acoustic_artifact"].update(
        {
            "exact_model_id": acoustic["exact_model_id"],
            "exact_checkpoint_revision": acoustic["exact_checkpoint_revision"],
            "code_and_checkpoint_license": {
                "code_spdx": "MIT",
                "code_evidence_sha256": acoustic["code_license_evidence_sha256"],
                "checkpoint_spdx": "research-only",
                "checkpoint_evidence_sha256": (
                    acoustic["checkpoint_license_evidence_sha256"]
                ),
            },
        }
    )
    config["hard_gates"]["pause_rhythm"].update(
        {
            "exact_implementation_revision": pause["implementation_revision"],
            "implementation_license": {
                "spdx": "MIT",
                "evidence_sha256": pause["license_evidence_sha256"],
            },
        }
    )
    config["hard_gates"]["target_phone_coverage"].update(
        {
            "exact_implementation_revision": phone["implementation_revision"],
            "exact_model_checkpoint_revision": phone["model_checkpoint_revision"],
            "implementation_and_checkpoint_license": {
                "code_spdx": "MIT",
                "checkpoint_spdx": "research-only",
                "evidence_sha256": phone["license_evidence_sha256"],
            },
        }
    )
    config["candidate_flow"].update(
        {
            "minimum_eligible_candidates_per_sentence": 1,
            "aggregate_transcript_error_metric_id": (
                policy["aggregate_transcript_error_metric_id"]
            ),
            "aggregate_transcript_error_metric_definition_sha256": (
                policy["aggregate_transcript_error_metric_definition_sha256"]
            ),
            "aggregate_transcript_error_aggregation": (
                policy["aggregate_transcript_error_aggregation"]
            ),
        }
    )

    contract._validate_static_values(config)
    contract.validate_preregistration_payload(prereg, config=config)
    contract.validate_asr_attestation_payload(asr)
    contract.validate_artifact_attestation_payload(artifact)
    contract._validate_attestation_config_bindings(
        config=config,
        preregistration=prereg,
        asr=asr,
        artifact=artifact,
    )

    drifted = deepcopy(prereg)
    drifted["license_decisions"]["target_phone_checkpoint_license"] = "MIT"
    with pytest.raises(contract.R02ContractError, match="license decisions"):
        contract._validate_attestation_config_bindings(
            config=config,
            preregistration=drifted,
            asr=asr,
            artifact=artifact,
        )


def test_static_rejects_source_hash_drift_and_order_change() -> None:
    config = _config()
    config["canonical_sources"]["pins"][0]["sha256"] = "0" * 64
    with pytest.raises(contract.R02ContractError, match="SHA-256 mismatch"):
        contract.validate_static_config(
            workspace_root=WORKSPACE,
            config_path=CONFIG_PATH,
            config=config,
        )

    config = _config()
    config["canonical_sources"]["pins"][0:2] = reversed(
        config["canonical_sources"]["pins"][0:2]
    )
    with pytest.raises(contract.R02ContractError, match="paths/order"):
        contract.validate_static_config(
            workspace_root=WORKSPACE,
            config_path=CONFIG_PATH,
            config=config,
        )


def test_duplicate_yaml_json_and_nonfinite_are_rejected(tmp_path: Path) -> None:
    duplicate_yaml = tmp_path / "duplicate.yaml"
    duplicate_yaml.write_text("same: 1\nsame: 2\n", encoding="utf-8")
    with pytest.raises(contract.R02ContractError, match="duplicate YAML key"):
        contract.load_config(duplicate_yaml)

    duplicate_json = tmp_path / "duplicate.json"
    duplicate_json.write_text('{"same": 1, "same": 2}', encoding="utf-8")
    with pytest.raises(contract.R02ContractError, match="duplicate JSON key"):
        contract.load_json(duplicate_json)

    nonfinite_json = tmp_path / "nonfinite.json"
    nonfinite_json.write_text('{"value": NaN}', encoding="utf-8")
    with pytest.raises(contract.R02ContractError, match="non-finite"):
        contract.load_json(nonfinite_json)


def test_symlink_source_and_config_are_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(contract.R02ContractError, match="symlink"):
        contract._read_file(tmp_path, "link.json", "source")

    config_link = tmp_path / "config.yaml"
    config_link.symlink_to(CONFIG_PATH)
    loaded = contract.load_config(config_link)
    with pytest.raises(contract.R02ContractError, match="symlink"):
        contract.validate_static_config(
            workspace_root=tmp_path,
            config_path=config_link,
            config=loaded,
        )


def test_snapshot_toctou_and_no_overwrite_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text('{"a": 1}', encoding="utf-8")
    snapshot = contract._snapshot_file(tmp_path, "source.json", "source")
    source.write_text('{"a": 2}', encoding="utf-8")
    with pytest.raises(contract.R02ContractError, match="changed after"):
        contract._verify_snapshot(tmp_path, snapshot, "source")

    outputs = deepcopy(_config()["outputs"])
    outputs["gate_report_path"] = "already.json"
    (tmp_path / "already.json").write_text("{}", encoding="utf-8")
    with pytest.raises(contract.R02ContractError, match="no-overwrite"):
        contract.ensure_output_targets_absent(
            workspace_root=tmp_path,
            outputs=outputs,
        )


def test_completion_payload_requires_preservation_and_exact_partition() -> None:
    class _Snapshot:
        sha256 = "a" * 64

    class _R01:
        commit_snapshot = _Snapshot()
        manifest_snapshot = _Snapshot()
        candidate_count = 160

    payload = {
        "schema_version": "voice_coach.r02_hard_gate_completion.v1",
        "status": "completed",
        "run_id": "r02-test",
        "config_sha256": "b" * 64,
        "r01_completion_commit_sha256": "a" * 64,
        "r01_candidate_manifest_sha256": "a" * 64,
        "preregistration_sha256": "c" * 64,
        "asr_attestation_sha256": "d" * 64,
        "artifact_attestation_sha256": "e" * 64,
        "gate_report_sha256": "f" * 64,
        "blinded_execution_manifest_sha256": "1" * 64,
        "source_lineage_sidecar_sha256": "2" * 64,
        "eligible_manifest_sha256": "3" * 64,
        "quarantine_manifest_sha256": "4" * 64,
        "registry_sha256": "5" * 64,
        "candidate_count": 160,
        "eligible_count": 150,
        "quarantined_count": 10,
        "failed_candidates_preserved": False,
        "r03_input_pass_only": True,
        "publication_allowed": False,
    }
    with pytest.raises(contract.R02ContractError, match="preservation"):
        contract._validate_r02_completion_payload(
            payload,
            r02_config_sha256="b" * 64,
            r01=_R01(),  # type: ignore[arg-type]
        )
