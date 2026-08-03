from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import shutil

import pytest

from voice_coach.references import r03_selection_contract as contract


WORKSPACE = Path(__file__).resolve().parents[2]
CANONICAL_CONFIG = (
    WORKSPACE / "configs/experiments/r03_reference_bank_selection_v1.yaml"
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _candidate(sentence_index: int, candidate_index: int) -> dict[str, object]:
    sources = ("human_recording", "cosyvoice_tts")
    speeds = ("slow", "neutral", "fast")
    pitches = ("low", "neutral", "high")
    token = f"{sentence_index:02d}-{candidate_index}"
    return {
        "candidate_id": f"r01c_{sentence_index:02x}{candidate_index:02x}" + "0" * 28,
        "target_text_sha256": _sha(f"text-{sentence_index}"),
        "audio_object_id_sha256": _sha(f"object-{token}"),
        "audio_sha256": _sha(f"audio-{token}"),
        "source_kind": sources[candidate_index % 2],
        "source_attestation_sha256": _sha(
            f"attestation-{sources[candidate_index % 2]}"
        ),
        "speaker_id_sha256": _sha(f"speaker-{candidate_index % 3}"),
        "speed_bucket": speeds[candidate_index % 3],
        "pitch_bucket": pitches[(candidate_index + 1) % 3],
        "r02_eligible_row_sha256": _sha(f"r02-row-{token}"),
        "objective_vector_sha256": _sha(f"objective-{token}"),
        "pareto_member": True,
        "immutable_lineage_verified": True,
    }


def _valid_bank() -> dict[str, object]:
    sentences: list[dict[str, object]] = []
    for index in range(1, 21):
        candidates = [_candidate(index, item) for item in range(4)]
        sentences.append(
            {
                "sentence_id": f"R00-{index:02d}",
                "eligible_candidate_count": 8,
                "selected_count": 4,
                "source_balance_verified_after_blind_selection": True,
                "speaker_diversity_verified": True,
                "speed_diversity_verified": True,
                "pitch_diversity_verified": True,
                "selected_candidates": candidates,
            }
        )
    return {
        "schema_version": "voice_coach.r03_reference_bank_manifest.v1",
        "bank_id": "r03-private-reference-bank-" + "a" * 32,
        "status": "r03_selected_pending_r04",
        "r00_card_sha256": _sha("r00"),
        "r01_commit_sha256": _sha("r01"),
        "r02_commit_sha256": _sha("r02"),
        "r03_config_sha256": _sha("r03"),
        "r03_preregistration_sha256": _sha("prereg"),
        "privacy_zone": "D1_pseudonymized_restricted_experiment",
        "publication_allowed": False,
        "r04_blind_evaluation_completed": False,
        "selection_blinded_to_source_kind": True,
        "selection_blinded_to_r04_and_outcomes": True,
        "sentences": sentences,
    }


def _valid_aggregate() -> dict[str, object]:
    return {
        "schema_version": "voice_coach.r03_reference_bank_aggregate.v1",
        "status": "aggregate_pending_r04",
        "privacy_zone": "A1_aggregate_only",
        "r03_commit_sha256": _sha("commit"),
        "sentence_count": 20,
        "selected_candidate_count": 80,
        "selected_count_histogram": {"3": 0, "4": 20, "5": 0},
        "source_kind_counts": {"cosyvoice_tts": 40, "human_recording": 40},
        "minimum_distinct_speakers_per_sentence": 3,
        "minimum_distinct_speed_buckets_per_sentence": 3,
        "minimum_distinct_pitch_buckets_per_sentence": 3,
        "individual_candidate_rows_present": False,
        "candidate_ids_present": False,
        "speaker_ids_present": False,
        "audio_hashes_present": False,
        "r04_blind_evaluation_completed": False,
        "publication_allowed": False,
    }


def test_valid_private_bank_enforces_twenty_sentences_and_balance() -> None:
    result = contract.validate_bank_manifest_payload(_valid_bank())

    assert result["status"] == "valid_private_bank_pending_r04"
    assert result["sentence_count"] == 20
    assert result["selected_candidate_count"] == 80
    assert result["source_kind_counts"] == {
        "cosyvoice_tts": 40,
        "human_recording": 40,
    }
    assert result["minimum_distinct_speakers_per_sentence"] == 3
    assert result["publication_allowed"] is False


def _lineage_fixture() -> tuple[
    dict[str, object],
    list[dict[str, object]],
    dict[str, object],
]:
    bank = _valid_bank()
    r01_rows: list[dict[str, object]] = []
    results: list[dict[str, object]] = []
    eligible_ids: list[str] = []
    for sentence in bank["sentences"]:  # type: ignore[union-attr]
        sentence_id = sentence["sentence_id"]  # type: ignore[index]
        for selected in sentence["selected_candidates"]:  # type: ignore[index]
            candidate_id = selected["candidate_id"]
            r01_rows.append(
                {
                    "candidate_id": candidate_id,
                    "sentence_id": sentence_id,
                    **{
                        field: selected[field]
                        for field in (
                            "target_text_sha256",
                            "audio_object_id_sha256",
                            "audio_sha256",
                            "source_kind",
                            "source_attestation_sha256",
                            "speaker_id_sha256",
                            "speed_bucket",
                            "pitch_bucket",
                        )
                    },
                }
            )
            result = {
                "candidate_id": candidate_id,
                "sentence_id": sentence_id,
                "target_text_sha256": selected["target_text_sha256"],
                "overall_decision": "eligible",
                "gate_evidence_sha256": _sha(f"gate-{candidate_id}"),
            }
            selected["r02_eligible_row_sha256"] = hashlib.sha256(
                contract.canonical_json_bytes(result)
            ).hexdigest()
            results.append(result)
            eligible_ids.append(candidate_id)
    report = {
        "eligible_candidate_ids": eligible_ids,
        "results": results,
    }
    return bank, r01_rows, report


def test_bank_lineage_binds_r01_audio_and_r02_eligible_rows() -> None:
    bank, r01_rows, report = _lineage_fixture()

    result = contract.validate_bank_lineage_bindings(
        bank,
        r01_candidate_rows=r01_rows,
        r02_gate_report=report,
    )

    assert result["immutable_lineage_binding_count"] == 80
    assert result["r02_eligible_only"] is True


def test_bank_lineage_rejects_audio_hash_drift() -> None:
    bank, r01_rows, report = _lineage_fixture()
    bank["sentences"][0]["selected_candidates"][0]["audio_sha256"] = _sha(  # type: ignore[index]
        "tampered"
    )

    with pytest.raises(contract.R03ContractError, match="audio_sha256 lineage"):
        contract.validate_bank_lineage_bindings(
            bank,
            r01_candidate_rows=r01_rows,
            r02_gate_report=report,
        )


def test_bank_lineage_rejects_quarantined_candidate() -> None:
    bank, r01_rows, report = _lineage_fixture()
    candidate_id = bank["sentences"][0]["selected_candidates"][0][  # type: ignore[index]
        "candidate_id"
    ]
    report["eligible_candidate_ids"].remove(candidate_id)  # type: ignore[union-attr]

    with pytest.raises(contract.R03ContractError, match="not R02-eligible"):
        contract.validate_bank_lineage_bindings(
            bank,
            r01_candidate_rows=r01_rows,
            r02_gate_report=report,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda payload: payload["sentences"].pop(),  # type: ignore[union-attr]
            "exactly 20",
        ),
        (
            lambda payload: payload["sentences"][0].update(  # type: ignore[index,union-attr]
                {
                    "selected_count": 2,
                    "selected_candidates": payload["sentences"][0][  # type: ignore[index]
                        "selected_candidates"
                    ][:2],
                }
            ),
            "3-5",
        ),
        (
            lambda payload: [
                row.update({"source_kind": "human_recording"})
                for row in payload["sentences"][0]["selected_candidates"]  # type: ignore[index,union-attr]
            ],
            "both sources",
        ),
        (
            lambda payload: payload["sentences"][0]["selected_candidates"][1].update(  # type: ignore[index,union-attr]
                {"source_kind": "human_recording"}
            ),
            "source imbalance",
        ),
        (
            lambda payload: [
                row.update({"speaker_id_sha256": _sha("one")})
                for row in payload["sentences"][0]["selected_candidates"]  # type: ignore[index,union-attr]
            ],
            "speaker diversity",
        ),
        (
            lambda payload: payload["sentences"][0]["selected_candidates"][0].update(  # type: ignore[index,union-attr]
                {
                    "candidate_id": payload["sentences"][1]["selected_candidates"][0][  # type: ignore[index]
                        "candidate_id"
                    ]
                }
            ),
            "invalid/duplicate candidate_id",
        ),
        (
            lambda payload: payload["sentences"][0]["selected_candidates"][0].update(  # type: ignore[index,union-attr]
                {"r04_score": 4.8}
            ),
            "forbidden fields",
        ),
    ],
)
def test_bank_faults_fail_closed(mutation: object, message: str) -> None:
    payload = _valid_bank()
    assert callable(mutation)
    mutation(payload)  # type: ignore[operator]

    with pytest.raises(contract.R03ContractError, match=message):
        contract.validate_bank_manifest_payload(payload)


def test_a1_aggregate_has_counts_only() -> None:
    result = contract.validate_a1_aggregate_payload(_valid_aggregate())

    assert result == {
        "status": "valid_a1_aggregate_pending_r04",
        "sentence_count": 20,
        "selected_candidate_count": 80,
        "publication_allowed": False,
    }


def test_a1_aggregate_rejects_candidate_identifier() -> None:
    payload = _valid_aggregate()
    payload["candidate_ids"] = ["r01c_" + "0" * 32]

    with pytest.raises(contract.R03ContractError, match="keys drift"):
        contract.validate_a1_aggregate_payload(payload)


def test_duplicate_yaml_key_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.yaml"
    path.write_text("schema_version: 1\nschema_version: 1\n", encoding="utf-8")

    with pytest.raises(contract.R03ContractError, match="duplicate YAML key"):
        contract.load_config(path)


def test_nonfinite_json_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"value": NaN}', encoding="utf-8")

    with pytest.raises(contract.R03ContractError, match="non-finite"):
        contract.load_json(path)


def test_symlink_input_is_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text("{}\n", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(real)

    with pytest.raises(contract.R03ContractError, match="symlink prohibited"):
        contract._read_file(tmp_path, "link.json", "fixture")


def test_snapshot_detects_toctou_drift(tmp_path: Path) -> None:
    path = tmp_path / "source.json"
    path.write_text('{"value": 1}\n', encoding="utf-8")
    _, snapshot = contract._read_file(tmp_path, "source.json", "fixture")
    path.write_text('{"value": 2}\n', encoding="utf-8")

    with pytest.raises(contract.R03ContractError, match="snapshot drift"):
        contract._verify_snapshot(tmp_path, snapshot, "fixture")


def _copy_static_bundle(tmp_path: Path) -> Path:
    config_relative = Path("configs/experiments/r03_reference_bank_selection_v1.yaml")
    paths = (config_relative, *(Path(item) for item in contract.SOURCE_PATHS))
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(WORKSPACE / relative, target)
    return tmp_path / config_relative


def test_static_source_hash_drift_is_rejected(tmp_path: Path) -> None:
    config_path = _copy_static_bundle(tmp_path)
    source = tmp_path / "docs/IMPLEMENTATION_PLAN_KO.md"
    source.write_text(
        source.read_text(encoding="utf-8") + "\ndrift\n", encoding="utf-8"
    )

    with pytest.raises(contract.R03ContractError, match="SHA-256 drift"):
        contract.validate_static_config(
            workspace_root=tmp_path,
            config_path=config_path,
            config=contract.load_config(config_path),
        )


def test_static_config_symlink_is_rejected(tmp_path: Path) -> None:
    config_path = _copy_static_bundle(tmp_path)
    real = config_path.with_name("real.yaml")
    config_path.rename(real)
    config_path.symlink_to(real.name)

    with pytest.raises(contract.R03ContractError, match="symlink prohibited"):
        contract.validate_static_config(
            workspace_root=tmp_path,
            config_path=config_path,
            config=contract.load_config(real),
        )


def test_execution_order_drift_is_rejected() -> None:
    config = copy.deepcopy(contract.load_config(CANONICAL_CONFIG))
    config["execution_order"] = list(reversed(config["execution_order"]))  # type: ignore[index]

    with pytest.raises(contract.R03ContractError, match="execution order drift"):
        contract._validate_config_values(config)


def test_no_overwrite_rejects_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "artifacts/result.json"
    output.parent.mkdir()
    output.write_text("{}\n", encoding="utf-8")

    with pytest.raises(contract.R03ContractError, match="already exists"):
        contract.ensure_output_targets_absent(
            workspace_root=tmp_path,
            outputs={
                "bank_schema_path": "schema.json",
                "commit_path": "artifacts/result.json",
            },
        )


def test_tbd_preregistration_fails_before_path_resolution() -> None:
    audit = contract.AccessAudit()
    config = {
        "preregistration": {
            "approved_artifact_sha256": "TBD_PREREGISTRATION",
            "approved_artifact_path": "must/not/be/resolved.json",
        }
    }

    with pytest.raises(
        contract.R03ContractError,
        match="TBD_PREREGISTRATION",
    ):
        contract.validate_approved_preregistration(
            workspace_root=Path("/does-not-matter"),
            config=config,
            audit=audit,
        )
    assert audit.preregistration_path_resolution_count == 0
    assert audit.preregistration_read_attempt_count == 0


def test_r01_absence_fails_before_r02_or_candidate_data(tmp_path: Path) -> None:
    audit = contract.AccessAudit()
    config = {
        "predecessor_r01": {
            "completion_commit_path": "artifacts/r01/commit.json",
            "completion_commit_sha256": _sha("missing-r01"),
        }
    }

    with pytest.raises(contract.R03ContractError, match="R01 must complete"):
        contract.deep_validate_r01_completion(
            workspace_root=tmp_path,
            config=config,
            r02_module=object(),  # type: ignore[arg-type]
            audit=audit,
        )
    assert audit.r01_completion_read_attempt_count == 1
    assert audit.r01_completion_read_count == 0
    assert audit.r02_completion_read_attempt_count == 0
    assert audit.eligible_candidate_data_path_resolution_count == 0
    assert audit.output_path_resolution_count == 0


def test_r02_absence_fails_before_prereg_candidate_or_output(tmp_path: Path) -> None:
    audit = contract.AccessAudit()
    config = {
        "predecessor_r02": {
            "completion_commit_path": "artifacts/r02/commit.json",
            "completion_commit_sha256": _sha("missing-r02"),
        }
    }

    with pytest.raises(contract.R03ContractError, match="hard gate must complete"):
        contract.deep_validate_r02_completion(
            workspace_root=tmp_path,
            config=config,
            r02_module=object(),  # type: ignore[arg-type]
            r01=object(),  # type: ignore[arg-type]
            audit=audit,
        )
    assert audit.r02_completion_read_attempt_count == 1
    assert audit.r02_completion_read_count == 0
    assert audit.preregistration_read_attempt_count == 0
    assert audit.eligible_candidate_data_path_resolution_count == 0
    assert audit.output_path_resolution_count == 0


def test_failure_result_never_claims_execution_or_completion() -> None:
    audit = contract.AccessAudit(source_pin_read_count=17)
    result = contract.failure_result(
        contract.R03ContractError("stop", stage="test", audit=audit)
    )

    assert result["status"] == "failed_closed"
    assert result["source_pin_read_count"] == 17
    assert result["actual_audio_or_model_or_gpu_execution"] is False
    assert result["actual_metric_or_selection_execution"] is False
    assert result["bank_manifest_created"] is False
    assert result["r03_completion_claim_allowed"] is False
    assert result["publication_allowed"] is False


def test_canonical_json_is_order_independent() -> None:
    left = {"b": 2, "a": 1}
    right = copy.deepcopy({"a": 1, "b": 2})

    assert contract.canonical_json_bytes(left) == contract.canonical_json_bytes(right)
