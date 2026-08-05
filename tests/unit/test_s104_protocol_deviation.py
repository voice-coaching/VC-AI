from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from voice_coach.reproducibility import s104_protocol_deviation as deviation


TIMESTAMP = {
    "utc": "2026-07-29T15:00:00+00:00",
    "kst": "2026-07-30T00:00:00+09:00",
}
ACCEPTED_SCOPE = {
    "s104_development_result_allowed": True,
    "s105_development_predecessor_allowed": True,
    "paper_dev_reporting_allowed": True,
    "promotion_claim_allowed": False,
    "locked_evidence_allowed": False,
    "continuous_immutability_claim_allowed": False,
    "rerun_required": False,
}
INVALID_SCOPE = {
    "s104_development_result_allowed": False,
    "s105_development_predecessor_allowed": False,
    "paper_dev_reporting_allowed": False,
    "promotion_claim_allowed": False,
    "locked_evidence_allowed": False,
    "continuous_immutability_claim_allowed": False,
    "rerun_required": True,
}


def _conditions(*, triggered_index: int | None = None) -> list[dict[str, Any]]:
    return [
        {
            "id": condition_id,
            "triggered": index == triggered_index,
            "evidence_refs": list(
                deviation.INVALIDATING_CONDITION_EVIDENCE_REFS[condition_id],
            ),
        }
        for index, condition_id in enumerate(
            deviation.INVALIDATING_CONDITION_IDS,
        )
    ]


def _decision(*, invalidating: bool = False) -> dict[str, Any]:
    return {
        "value": "INVALIDATING" if invalidating else "MINOR_ACCEPTED",
        "rationale_code": (
            "RERUN_REQUIRED" if invalidating else "ACCEPT_WITH_RATIONALE"
        ),
        "adjudicated_at": dict(TIMESTAMP),
        "scope": dict(INVALID_SCOPE if invalidating else ACCEPTED_SCOPE),
    }


def _derived_sections() -> dict[str, Any]:
    implementation = [
        {"role": role, "match": True}
        for role in deviation.IMPLEMENTATION_ROLES
    ]
    data = [
        {"role": role, "match": True}
        for role in (
            "modeling_manifest",
            "prompt_challenge_reservation",
            "explicit_utterance_labels",
        )
    ]
    model = [
        {"role": role, "match": True}
        for role in ("model_config", "model_checkpoint")
    ]
    return {
        "experiment": {"derived": "experiment"},
        "event": {
            "during": {
                "sha256": deviation.EXPECTED_TRANSIENT_SHA256,
                "size_bytes": deviation.EXPECTED_TRANSIENT_SIZE,
            },
            "after": {
                "sha256": deviation.EXPECTED_TARGET_SHA256,
                "size_bytes": deviation.EXPECTED_TARGET_SIZE,
                "mtime_ns": deviation.RECOVERY_MTIME_NS,
                "ctime_ns": deviation.RECOVERY_MTIME_NS,
            },
        },
        "start_bindings": {
            "project_manifest_fingerprint": {
                "sha256": deviation.EXPECTED_TARGET_SHA256,
            },
            "scorer_lock_fingerprint": {
                "sha256": deviation.EXPECTED_SCORER_LOCK_SHA256,
            },
        },
        "scorer_environment": {
            "package_maps_equal": True,
            "lock": {
                "sha256": deviation.EXPECTED_SCORER_LOCK_SHA256,
                "entry_count": 79,
                "mismatches": [],
            },
            "venv_non_pyc_newer_than_process_start_count": 0,
            "dist_info_newer_than_process_start_count": 0,
            "markdown_it": {
                "installed_before_run": True,
                "installed_voice_coach_direct_requirement_present": False,
            },
        },
        "bindings": {
            "config": {"sha256": deviation.EXPECTED_CONFIG_SHA256},
            "execution_lock": {
                "sha256": deviation.EXPECTED_EXECUTION_LOCK_SHA256,
            },
            "config_lock_match": True,
            "implementation": implementation,
            "implementation_count": len(implementation),
            "implementation_mismatch_count": 0,
            "non_manifest_production_input_mismatch_count": 0,
            "data": data,
            "data_mismatch_count": 0,
            "model": model,
            "model_mismatch_count": 0,
        },
        "code_path_audit": {
            "target_semantically_parsed": False,
            "calculation_effect_path_found": False,
            "core_markdown_it_reference_count": 0,
            "dependency_resolution_or_install_call_present": False,
        },
        "terminal_evidence": {
            "trainer_exit_code": 0,
            "commit": {
                "sha256": "a" * 64,
                "status": "committed",
                "artifact_count": 16,
            },
            "registry": {
                "status": "completed",
                "failure": None,
                "artifact_count": 16,
            },
            "generation": {
                "artifact_count": 16,
                "artifacts": [{"index": index} for index in range(16)],
            },
            "public_validator": {
                "exit_code": 0,
                "output": {
                    "status": "valid",
                    "artifact_count": 16,
                    "locked_test_accessed": False,
                },
            },
            "independent_audit": {
                "artifact_hashes_match": True,
                "checkpoint_allowlist_match": True,
                "row_identity_match": True,
                "slices_recomputed": True,
                "all_match": True,
                "locked_test_accessed": False,
            },
        },
        "validator": {
            "schema_path": deviation.SCHEMA_RELATIVE,
            "schema_sha256": "b" * 64,
            "validator_path": deviation.VALIDATOR_RELATIVE,
            "validator_sha256": "c" * 64,
            "cli_path": deviation.CLI_RELATIVE,
            "cli_sha256": "d" * 64,
        },
    }


def _receipt(sections: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": deviation.RECEIPT_SCHEMA_VERSION,
        "deviation_id": deviation.DEVIATION_ID,
        "status": "adjudicated",
        "created_at": dict(TIMESTAMP),
        **sections,
        "invalidating_conditions": _conditions(),
        "decision": _decision(),
        "limitations": sorted(deviation.REQUIRED_LIMITATIONS),
    }


def _write_receipt_commit(
    root: Path,
    receipt: dict[str, Any],
) -> tuple[Path, Path]:
    commit_path = root / "artifacts/protocol_deviations/dev-s104-001/commit.json"
    receipt_bytes = deviation.canonical_json_bytes(receipt)
    receipt_sha256 = hashlib.sha256(receipt_bytes).hexdigest()
    receipt_path = commit_path.parent / "generations" / receipt_sha256 / "receipt.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_bytes(receipt_bytes)
    unsigned_commit = {
        "schema_version": deviation.RECEIPT_COMMIT_SCHEMA_VERSION,
        "status": "committed",
        "deviation_id": deviation.DEVIATION_ID,
        "receipt_path": receipt_path.relative_to(root).as_posix(),
        "receipt_sha256": receipt_sha256,
        "decision": receipt["decision"]["value"],
    }
    commit = {
        **unsigned_commit,
        "binding_sha256": hashlib.sha256(
            deviation.canonical_json_bytes(unsigned_commit),
        ).hexdigest(),
    }
    commit_path.parent.mkdir(parents=True, exist_ok=True)
    commit_path.write_bytes(deviation.canonical_json_bytes(commit))
    return commit_path, receipt_path


def _patch_live_derivations(
    monkeypatch: pytest.MonkeyPatch,
    sections: dict[str, Any],
) -> None:
    monkeypatch.setattr(deviation, "_load_config", lambda _path: {})
    monkeypatch.setattr(
        deviation,
        "_derive_experiment_event_start",
        lambda **_kwargs: (
            sections["experiment"],
            sections["event"],
            sections["start_bindings"],
        ),
    )
    monkeypatch.setattr(
        deviation,
        "_derive_scorer_environment",
        lambda **_kwargs: sections["scorer_environment"],
    )
    monkeypatch.setattr(
        deviation,
        "_derive_bindings",
        lambda **_kwargs: sections["bindings"],
    )
    monkeypatch.setattr(
        deviation,
        "_derive_code_path_audit",
        lambda **_kwargs: sections["code_path_audit"],
    )
    monkeypatch.setattr(
        deviation,
        "_derive_terminal_evidence",
        lambda **_kwargs: sections["terminal_evidence"],
    )
    monkeypatch.setattr(
        deviation,
        "_derive_validator_identity",
        lambda _root: sections["validator"],
    )


def _create_config(root: Path) -> Path:
    config_path = root / deviation.S104_CONFIG_RELATIVE
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("{}\n", encoding="utf-8")
    return config_path


def test_schema_identity_and_closed_top_level() -> None:
    root = Path(__file__).resolve().parents[2]
    schema_path = root / deviation.SCHEMA_RELATIVE
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == deviation.RECEIPT_SCHEMA_VERSION
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == deviation.TOP_LEVEL_KEYS
    assert schema["properties"]["invalidating_conditions"]["minItems"] == 17
    assert schema["properties"]["invalidating_conditions"]["maxItems"] == 17


def test_transient_bytes_reconstruct_from_recovered_manifest() -> None:
    root = Path(__file__).resolve().parents[2]
    recovered = (root / deviation.TARGET_RELATIVE).read_bytes()
    reconstructed = (
        recovered[: deviation.INSERT_OFFSET]
        + deviation.INSERTED_LINE
        + recovered[deviation.INSERT_OFFSET :]
    )

    assert hashlib.sha256(recovered).hexdigest() == deviation.EXPECTED_TARGET_SHA256
    assert len(recovered) == deviation.EXPECTED_TARGET_SIZE
    assert (
        hashlib.sha256(reconstructed).hexdigest() == deviation.EXPECTED_TRANSIENT_SHA256
    )
    assert len(reconstructed) == deviation.EXPECTED_TRANSIENT_SIZE


def test_canonical_scorer_lock_has_79_exact_requirements() -> None:
    root = Path(__file__).resolve().parents[2]
    requirements = deviation._locked_requirements(
        root / deviation.SCORER_LOCK_RELATIVE,
    )

    assert len(requirements) == 79
    assert len(set(requirements)) == 79
    assert ("markdown-it-py", "4.2.0") in requirements


def test_content_addressed_pointer_rejects_receipt_tamper(
    tmp_path: Path,
) -> None:
    receipt = _receipt(_derived_sections())
    commit_path, receipt_path = _write_receipt_commit(tmp_path, receipt)
    receipt_path.write_bytes(receipt_path.read_bytes() + b"\n")

    with pytest.raises(
        deviation.S104ProtocolDeviationError,
        match="receipt content hash differs",
    ):
        deviation._load_receipt(
            root=tmp_path.resolve(),
            receipt_commit_path=commit_path,
        )


def test_decision_consistency_for_minor_and_invalidating() -> None:
    minor = _receipt(_derived_sections())
    result = deviation._validate_conditions_and_decision(
        receipt=minor,
        require_decision="MINOR_ACCEPTED",
    )
    assert result["value"] == "MINOR_ACCEPTED"
    assert result["triggered"] == []

    invalidating = _receipt(_derived_sections())
    invalidating["invalidating_conditions"] = _conditions(triggered_index=3)
    invalidating["decision"] = _decision(invalidating=True)
    result = deviation._validate_conditions_and_decision(
        receipt=invalidating,
        require_decision="INVALIDATING",
    )
    assert result["value"] == "INVALIDATING"
    assert result["triggered"] == [deviation.INVALIDATING_CONDITION_IDS[3]]


def test_decision_rejects_scope_that_overclaims() -> None:
    receipt = _receipt(_derived_sections())
    receipt["decision"]["scope"]["promotion_claim_allowed"] = True

    with pytest.raises(
        deviation.S104ProtocolDeviationError,
        match="decision is inconsistent",
    ):
        deviation._validate_conditions_and_decision(
            receipt=receipt,
            require_decision=None,
        )


def test_validator_matches_every_self_reported_live_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sections = _derived_sections()
    _patch_live_derivations(monkeypatch, sections)
    config_path = _create_config(tmp_path)
    receipt = _receipt(sections)
    commit_path, _ = _write_receipt_commit(tmp_path, receipt)

    result = deviation.validate_s104_protocol_deviation(
        workspace_root=tmp_path,
        receipt_commit_path=commit_path,
        s104_config_path=config_path,
        require_decision="MINOR_ACCEPTED",
    )
    assert result["status"] == "valid"
    assert result["artifact_count"] == 16
    assert result["s105_development_predecessor_allowed"] is True
    assert result["continuous_immutability_claim_allowed"] is False

    mismatched = _receipt(sections)
    mismatched["terminal_evidence"] = {"self_reported": "not-derived"}
    _write_receipt_commit(tmp_path, mismatched)
    with pytest.raises(
        deviation.S104ProtocolDeviationError,
        match="self-reported receipt section differs.*terminal_evidence",
    ):
        deviation.validate_s104_protocol_deviation(
            workspace_root=tmp_path,
            receipt_commit_path=commit_path,
            s104_config_path=config_path,
        )


def test_validator_fails_closed_when_terminal_validation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sections = _derived_sections()
    _patch_live_derivations(monkeypatch, sections)
    monkeypatch.setattr(
        deviation,
        "_derive_terminal_evidence",
        lambda **_kwargs: (_ for _ in ()).throw(
            deviation.S104ProtocolDeviationError(
                "public S104 validator did not pass",
            )
        ),
    )
    config_path = _create_config(tmp_path)
    commit_path, _ = _write_receipt_commit(tmp_path, _receipt(sections))

    with pytest.raises(
        deviation.S104ProtocolDeviationError,
        match="public S104 validator did not pass",
    ):
        deviation.validate_s104_protocol_deviation(
            workspace_root=tmp_path,
            receipt_commit_path=commit_path,
            s104_config_path=config_path,
        )
