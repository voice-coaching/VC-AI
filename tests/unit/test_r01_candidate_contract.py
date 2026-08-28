from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from voice_coach.references import r01_candidate_contract as contract


WORKSPACE = Path(__file__).resolve().parents[2]
CONFIG_PATH = WORKSPACE / "configs/experiments/r01_human_cosyvoice_candidates_v1.yaml"
HUMAN_TEMPLATE_PATH = (
    WORKSPACE / "configs/reference/r01_human_recording_attestation_v1.template.json"
)
COSY_TEMPLATE_PATH = (
    WORKSPACE / "configs/reference/r01_cosyvoice_model_attestation_v1.template.json"
)
SCHEMA_PATH = WORKSPACE / "configs/reference/r01_candidate_manifest_v1.schema.json"


def _config() -> dict:
    return dict(contract.load_config(CONFIG_PATH))


def test_canonical_config_only_is_static_and_has_zero_runtime_access() -> None:
    audit = contract.AccessAudit()
    static = contract.validate_static_config(
        workspace_root=WORKSPACE,
        config_path=CONFIG_PATH,
        config=_config(),
        audit=audit,
    )
    result = contract.config_only_result(static, audit)

    assert result["status"] == "valid_config_only"
    assert result["source_pin_count"] == 12
    assert result["source_pin_read_count"] == 12
    assert result["r00_deep_validation_count"] == 0
    assert result["human_attestation_read_attempt_count"] == 0
    assert result["cosyvoice_attestation_read_attempt_count"] == 0
    for field in (
        "recording_data_path_resolution_count",
        "candidate_data_path_resolution_count",
        "audio_read_count",
        "audio_write_count",
        "model_path_resolution_count",
        "model_metadata_read_count",
        "model_weight_read_count",
        "model_download_count",
        "runtime_environment_path_resolution_count",
        "runtime_environment_read_count",
        "gpu_access_count",
        "candidate_id_generation_count",
        "candidate_manifest_read_count",
        "candidate_manifest_write_count",
        "metric_count",
        "r02_gate_read_count",
        "publication_count",
        "output_path_resolution_count",
        "output_count",
    ):
        assert result[field] == 0
    assert result["r01_completion_claim_allowed"] is False
    assert result["publication_allowed"] is False


def test_static_policy_fixes_8_to_16_balanced_candidates_and_diversity() -> None:
    config = _config()
    design = config["candidate_design"]

    assert design["sentence_ids"] == list(contract.SENTENCE_IDS)
    assert design["candidates_per_sentence"] == {
        "minimum": 8,
        "planned": 12,
        "maximum": 16,
    }
    assert design["source_balance"] == {
        "rule": "exact_equal_human_and_cosyvoice_within_each_sentence",
        "minimum_per_source_per_sentence": 4,
        "planned_per_source_per_sentence": 6,
        "maximum_per_source_per_sentence": 8,
    }
    diversity = design["diversity_preservation"]
    assert diversity["dimensions"] == ["speaker", "speed", "pitch"]
    assert diversity["minimum_distinct_speakers_per_source_per_sentence"] == 2
    assert diversity["required_speed_buckets"] == list(contract.SPEED_BUCKETS)
    assert diversity["required_pitch_buckets"] == list(contract.PITCH_BUCKETS)
    assert diversity["preserve_all_design_cells_until_r02"] is True
    assert diversity["posthoc_relabel_or_collapse_allowed"] is False


def test_cosyvoice_ledger_observation_never_implies_weight_authorization() -> None:
    config = _config()
    cosy = config["cosyvoice"]

    assert cosy["ledger_observation"] == contract.LEDGER_COSYVOICE
    assert cosy["ledger_observation"]["authorization_inferred_from_ledger"] is False
    assert (
        cosy["ledger_observation"]["standalone_weight_license_file_observed"] is False
    )
    assert cosy["authorization_decision"] == contract.TBD
    assert cosy["exact_weight_license_decision"] == contract.TBD
    assert cosy["grpo_allowed"] is False
    assert cosy["fine_tuning_allowed"] is False
    assert cosy["network_download_allowed"] is False


def test_templates_are_non_authorizing_and_create_no_fake_receipts() -> None:
    human = contract.load_json(HUMAN_TEMPLATE_PATH)
    cosy = contract.load_json(COSY_TEMPLATE_PATH)

    assert human["status"] == contract.TBD
    assert human["speaker_receipts"] == []
    assert human["completion_claim_allowed"] is False
    assert human["consent_protocol"]["tts_training_allowed"] is False
    assert human["consent_protocol"]["public_release_allowed"] is False
    assert cosy["status"] == contract.TBD
    assert cosy["artifact_authorization"]["decision"] == contract.TBD
    assert cosy["runtime_environment"]["network_download_allowed"] is False
    assert cosy["gpu_pin"]["concurrent_training_process_allowed"] is False
    assert cosy["completion_claim_allowed"] is False


def test_manifest_schema_is_d1_only_and_has_no_plaintext_or_path_fields() -> None:
    schema = contract.load_json(SCHEMA_PATH)
    contract._validate_manifest_schema(schema)
    row = schema["$defs"]["candidateRow"]

    assert set(row["properties"]) == contract.CANDIDATE_ROW_KEYS
    assert not set(contract.FORBIDDEN_MANIFEST_FIELDS) & set(row["properties"])
    assert row["properties"]["privacy_zone"]["const"] == (
        "D1_pseudonymized_restricted_experiment"
    )
    assert row["properties"]["r02_hard_gate_status"]["const"] == "not_run"
    assert row["properties"]["publication_allowed"]["const"] is False
    assert schema["properties"]["candidate_rows"]["minItems"] == 160
    assert schema["properties"]["candidate_rows"]["maxItems"] == 320


def test_manifest_validator_rejects_empty_or_leaking_payload_without_audio() -> None:
    payload = {
        "schema_version": "voice_coach.r01_candidate_manifest.v1",
        "manifest_id": "r01-private-candidates-" + "0" * 32,
        "status": "r01_private_candidates_pending_r02",
        "r00_card_sha256": contract.R00_EXPECTED["card_sha256"],
        "human_attestation_sha256": "1" * 64,
        "cosyvoice_attestation_sha256": "2" * 64,
        "privacy_zone": "D1_pseudonymized_restricted_experiment",
        "publication_allowed": False,
        "r02_hard_gate_passed": False,
        "candidate_rows": [],
    }
    sentence_hashes = {item: "3" * 64 for item in contract.SENTENCE_IDS}

    with pytest.raises(contract.R01ContractError, match="160-320"):
        contract.validate_candidate_manifest_payload(
            payload,
            r00_sentence_hashes=sentence_hashes,
            human_attestation_sha256="1" * 64,
            cosyvoice_attestation_sha256="2" * 64,
        )

    leaking = deepcopy(payload)
    leaking["target_text"] = "금지된 평문"
    with pytest.raises(contract.R01ContractError, match="keys differ"):
        contract.validate_candidate_manifest_payload(
            leaking,
            r00_sentence_hashes=sentence_hashes,
            human_attestation_sha256="1" * 64,
            cosyvoice_attestation_sha256="2" * 64,
        )


def test_uuid4_candidate_id_rejects_order_encoded_and_non_v4_values() -> None:
    with pytest.raises(contract.R01ContractError, match="opaque"):
        contract._uuid4_candidate_id("R00-01-human-0001", "candidate ID")
    with pytest.raises(contract.R01ContractError, match="UUIDv4 bits"):
        contract._uuid4_candidate_id("r01c_" + "0" * 32, "candidate ID")


def test_config_source_hash_drift_and_pin_order_fail_closed() -> None:
    config = _config()
    drift = deepcopy(config)
    drift["canonical_sources"]["pins"][0]["sha256"] = "0" * 64
    with pytest.raises(contract.R01ContractError, match="SHA-256 mismatch"):
        contract.validate_static_config(
            workspace_root=WORKSPACE,
            config_path=CONFIG_PATH,
            config=drift,
        )

    reordered = deepcopy(config)
    reordered["canonical_sources"]["pins"][0:2] = reversed(
        reordered["canonical_sources"]["pins"][0:2]
    )
    with pytest.raises(contract.R01ContractError, match="paths/order"):
        contract.validate_static_config(
            workspace_root=WORKSPACE,
            config_path=CONFIG_PATH,
            config=reordered,
        )


def test_duplicate_yaml_and_json_keys_are_rejected(tmp_path: Path) -> None:
    duplicate_yaml = tmp_path / "duplicate.yaml"
    duplicate_yaml.write_text(
        "schema_version: 1\nschema_version: 1\n", encoding="utf-8"
    )
    with pytest.raises(contract.R01ContractError, match="duplicate key"):
        contract.load_config(duplicate_yaml)

    duplicate_json = tmp_path / "duplicate.json"
    duplicate_json.write_text('{"same": 1, "same": 2}', encoding="utf-8")
    with pytest.raises(contract.R01ContractError, match="duplicate key"):
        contract.load_json(duplicate_json)


def test_symlink_source_is_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(real)

    with pytest.raises(contract.R01ContractError, match="symlink"):
        contract._read_file(tmp_path, "link.json", "source")


def test_symlink_config_is_rejected_before_source_resolution(tmp_path: Path) -> None:
    link = tmp_path / "config.yaml"
    link.symlink_to(CONFIG_PATH)
    loaded = contract.load_config(link)

    with pytest.raises(contract.R01ContractError, match="symlink"):
        contract.validate_static_config(
            workspace_root=tmp_path,
            config_path=link,
            config=loaded,
        )


def test_snapshot_change_is_detected_as_toctou(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text('{"value": 1}', encoding="utf-8")
    snapshot = contract._snapshot_file(tmp_path, "source.json", "source")
    source.write_text('{"value": 2}', encoding="utf-8")

    with pytest.raises(contract.R01ContractError, match="changed after validation"):
        contract._verify_snapshot(tmp_path, snapshot, "source")


def test_no_overwrite_refuses_existing_target_and_symlink_parent(
    tmp_path: Path,
) -> None:
    outputs = {
        "candidate_manifest_path": "d1/manifest.json",
        "candidate_audio_root": "d1/audio",
        "failure_report_path": "reports/failure.json",
    }
    (tmp_path / "d1").mkdir()
    (tmp_path / "d1/manifest.json").write_text("sentinel", encoding="utf-8")
    with pytest.raises(contract.R01ContractError, match="already exists"):
        contract.ensure_output_targets_absent(
            workspace_root=tmp_path,
            outputs=outputs,
        )
    assert (tmp_path / "d1/manifest.json").read_text(encoding="utf-8") == "sentinel"

    (tmp_path / "d1/manifest.json").unlink()
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "reports").symlink_to(real, target_is_directory=True)
    with pytest.raises(contract.R01ContractError, match="symlink"):
        contract.ensure_output_targets_absent(
            workspace_root=tmp_path,
            outputs=outputs,
        )


def test_no_overwrite_accepts_only_fully_missing_target_suffixes(
    tmp_path: Path,
) -> None:
    contract.ensure_output_targets_absent(
        workspace_root=tmp_path,
        outputs={
            "candidate_manifest_path": "missing/d1/manifest.json",
            "candidate_audio_root": "missing/d1/audio",
            "failure_report_path": "missing/reports/failure.json",
        },
    )
    assert list(tmp_path.iterdir()) == []


def test_human_template_cannot_pass_as_signed_attestation() -> None:
    human = contract.load_json(HUMAN_TEMPLATE_PATH)
    with pytest.raises(contract.R01ContractError, match="TBD_EXTERNAL_ATTESTATION"):
        contract.validate_human_attestation(
            human,
            r00_card_sha256=contract.R00_EXPECTED["card_sha256"],
        )


def test_cosyvoice_template_cannot_pass_as_signed_attestation() -> None:
    cosy = contract.load_json(COSY_TEMPLATE_PATH)
    with pytest.raises(contract.R01ContractError, match="TBD_EXTERNAL_ATTESTATION"):
        contract.validate_cosyvoice_attestation(cosy)


def test_output_overlap_with_source_is_rejected_lexically() -> None:
    config = _config()
    config["outputs"]["candidate_manifest_path"] = (
        "data/reference/r00/r00_sentence_card_v1.json"
    )

    with pytest.raises(contract.R01ContractError, match="overlaps canonical source"):
        contract.validate_static_config(
            workspace_root=WORKSPACE,
            config_path=CONFIG_PATH,
            config=config,
        )


def test_cosyvoice_attestation_sha_and_exact_bindings_move_together() -> None:
    config = _config()
    config["external_attestations"]["cosyvoice"]["sha256"] = "9" * 64

    with pytest.raises(
        contract.R01ContractError, match="must become attested together"
    ):
        contract.validate_static_config(
            workspace_root=WORKSPACE,
            config_path=CONFIG_PATH,
            config=config,
        )


def test_failure_result_never_claims_candidate_or_publication() -> None:
    audit = contract.AccessAudit(r00_deep_validation_count=1)
    result = contract.failure_result(
        contract.R01ContractError(
            "missing",
            stage="human_attestation",
            audit=audit,
        )
    )

    assert result["status"] == "failed_closed"
    assert result["stage"] == "human_attestation"
    assert result["candidate_manifest_created"] is False
    assert result["audio_created"] is False
    assert result["r01_completion_claim_allowed"] is False
    assert result["publication_allowed"] is False


def test_strict_json_rejects_nonfinite_values() -> None:
    with pytest.raises(contract.R01ContractError, match="non-finite"):
        contract._load_json_bytes(b'{"value": NaN}', "nonfinite")


def test_manifest_schema_rejects_new_plaintext_property() -> None:
    schema = deepcopy(contract.load_json(SCHEMA_PATH))
    schema["$defs"]["candidateRow"]["properties"]["target_text"] = {"type": "string"}

    with pytest.raises(contract.R01ContractError, match="properties"):
        contract._validate_manifest_schema(schema)
