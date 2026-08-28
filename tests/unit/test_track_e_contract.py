"""The Track E contract must be frozen, self-consistent, and hash-stable.

These tests fix the properties later stages depend on: the contract cannot grant
a validated claim, cannot make a forbidden split readable, cannot derive
thresholds anywhere but the calibration split, and cannot silently drop an
access counter.  Every mutation below is a way a run could over-claim.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from voice_coach.pronunciation_feedback.track_e_contract import (
    DEFAULT_CONTRACT_PATH,
    REQUIRED_METRICS,
    REQUIRED_THRESHOLD_RULES,
    REQUIRED_ZERO_COUNTERS,
    TrackEContractError,
    canonical_contract_bytes,
    contract_sha256,
    load_track_e_contract,
    validate_contract_document,
)

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / DEFAULT_CONTRACT_PATH


def _document() -> dict[str, Any]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _write(tmp_path: Path, document: Any) -> Path:
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def test_repository_contract_is_frozen_and_engineering_scoped() -> None:
    contract = load_track_e_contract(CONTRACT_PATH)

    assert contract.claim_scope == "engineering_provisional"
    assert contract.cohort == "zh_ja_l1_korean_l2"
    assert contract.derivation_split == "calibration"
    assert contract.document["authority"][
        "grants_research_completion_points"
    ] is False
    assert contract.document["authority"][
        "grants_validated_performance_claim"
    ] is False


def test_hash_is_key_order_independent_but_content_sensitive() -> None:
    document = _document()
    reordered = dict(reversed(list(document.items())))

    assert contract_sha256(document) == contract_sha256(reordered)

    changed = copy.deepcopy(document)
    changed["gate_thresholds"]["s107_calibration"]["ece"]["value"] = 0.2
    assert contract_sha256(changed) != contract_sha256(document)


def test_canonical_bytes_are_stable_and_compact() -> None:
    document = _document()

    first = canonical_contract_bytes(document)
    second = canonical_contract_bytes(json.loads(first.decode("utf-8")))

    assert first == second
    # Compact separators: the default json.dumps spacing would change the hash.
    assert first != json.dumps(
        document, ensure_ascii=False, sort_keys=True
    ).encode("utf-8")


def test_expected_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(TrackEContractError, match="contract hash differs"):
        load_track_e_contract(CONTRACT_PATH, expected_sha256="0" * 64)


def test_every_gate_threshold_has_a_derivation_rule() -> None:
    document = _document()
    rules = document["threshold_derivation_rules"]["rules"]

    assert tuple(sorted(rules)) == tuple(sorted(REQUIRED_THRESHOLD_RULES))
    for name in REQUIRED_METRICS:
        assert name in document["metric_definitions"]


def test_targeted_rules_reference_a_defined_metric() -> None:
    document = _document()
    rules = document["threshold_derivation_rules"]["rules"]

    targeted = [
        rule
        for rule in rules.values()
        if rule["statistic"] == "smallest_value_satisfying_target"
    ]
    assert targeted
    for rule in targeted:
        assert rule["target"]["metric"] in document["metric_definitions"]


@pytest.mark.parametrize("split", ["locked", "expert_holdout"])
def test_making_a_forbidden_split_readable_fails_closed(split: str) -> None:
    document = _document()
    document["split_roles"][split]["read_allowed"] = True

    with pytest.raises(TrackEContractError, match="must stay unreadable"):
        validate_contract_document(document)


@pytest.mark.parametrize("split", ["train", "dev"])
def test_deriving_thresholds_off_calibration_fails_closed(split: str) -> None:
    document = _document()
    document["split_roles"][split]["threshold_derivation_allowed"] = True

    with pytest.raises(TrackEContractError, match="calibration split may derive"):
        validate_contract_document(document)


def test_unfrozen_status_fails_closed() -> None:
    document = _document()
    document["status"] = "draft"

    with pytest.raises(TrackEContractError, match="must be frozen"):
        validate_contract_document(document)


def test_research_validated_scope_cannot_be_declared_here() -> None:
    document = _document()
    document["claim_scope"] = "research_validated"

    with pytest.raises(TrackEContractError, match="engineering_provisional"):
        validate_contract_document(document)


@pytest.mark.parametrize(
    "flag",
    ["grants_research_completion_points", "grants_validated_performance_claim"],
)
def test_claiming_authority_fails_closed(flag: str) -> None:
    document = _document()
    document["authority"][flag] = True

    with pytest.raises(TrackEContractError, match=flag):
        validate_contract_document(document)


@pytest.mark.parametrize("counter", REQUIRED_ZERO_COUNTERS)
def test_dropping_an_access_counter_fails_closed(counter: str) -> None:
    document = _document()
    counters = document["access_instrumentation"]["counters_that_must_be_zero"]
    document["access_instrumentation"]["counters_that_must_be_zero"] = [
        name for name in counters if name != counter
    ]

    with pytest.raises(TrackEContractError, match="must cover"):
        validate_contract_document(document)


def test_nonzero_access_counter_fails_closed() -> None:
    contract = load_track_e_contract(CONTRACT_PATH)
    observed = {name: 0 for name in contract.zero_counters()}

    contract.assert_counters_zero(observed)

    observed["locked_test_read_count"] = 1
    with pytest.raises(TrackEContractError, match="must be zero"):
        contract.assert_counters_zero(observed)


def test_unreported_access_counter_is_not_evidence_of_no_access() -> None:
    contract = load_track_e_contract(CONTRACT_PATH)
    observed = {name: 0 for name in contract.zero_counters()}
    del observed["expert_holdout_read_count"]

    with pytest.raises(TrackEContractError, match="not reported"):
        contract.assert_counters_zero(observed)


def test_tolerating_a_nonzero_counter_fails_closed() -> None:
    document = _document()
    document["access_instrumentation"]["on_nonzero"] = "warn"

    with pytest.raises(TrackEContractError, match="fail closed"):
        validate_contract_document(document)


def test_out_of_range_quantile_fails_closed() -> None:
    document = _document()
    document["threshold_derivation_rules"]["rules"]["min_content_score"][
        "quantile"
    ] = 1.5

    with pytest.raises(TrackEContractError, match="must be in"):
        validate_contract_document(document)


def test_reordering_the_derivation_order_fails_closed() -> None:
    document = _document()
    order = document["threshold_derivation_rules"]["derivation_order"]
    document["threshold_derivation_rules"]["derivation_order"] = list(reversed(order))

    with pytest.raises(TrackEContractError, match="derivation_order"):
        validate_contract_document(document)


def test_missing_stage_c_acceptance_gate_fails_closed() -> None:
    document = _document()
    del document["gate_thresholds"]["stage_c_content_backend_acceptance"]

    with pytest.raises(TrackEContractError, match="stage_c_content_backend_acceptance"):
        validate_contract_document(document)


def test_absent_artifact_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(TrackEContractError, match="not found"):
        load_track_e_contract(tmp_path / "missing.json")


def test_non_object_artifact_fails_closed(tmp_path: Path) -> None:
    path = _write(tmp_path, ["not", "an", "object"])

    with pytest.raises(TrackEContractError, match="must be an object"):
        load_track_e_contract(path)


def test_invalid_json_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "contract.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(TrackEContractError, match="not valid JSON"):
        load_track_e_contract(path)


def test_cli_reports_the_hash_and_rejects_a_wrong_expectation() -> None:
    command = [sys.executable, "scripts/validate_track_e_contract.py"]

    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "valid"
    assert payload["contract_sha256"] == contract_sha256(_document())

    rejected = subprocess.run(
        [*command, "--expect-sha256", "0" * 64],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rejected.returncode == 1
    assert json.loads(rejected.stderr)["status"] == "failed_closed"
