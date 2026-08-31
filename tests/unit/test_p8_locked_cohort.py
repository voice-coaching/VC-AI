from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from voice_coach.visual_feedback.errors import VisualFeedbackContractError
from voice_coach.visual_feedback.models.p8_locked_cohort import (
    P8_LOCKED_COHORT_DISCOVERY_CONTRACT,
    P8_LOCKED_COHORT_SELECTION_RULE_ID,
    P8_LOCKED_VALIDATION_SHARD_PAIRS,
    P8_REQUIRED_SLICE_KEYS,
    P8LockedCohortContractError,
    P8LockedCohortDiscoverySeal,
    P8LockedCohortSelectionReceipt,
    P8LockedLabelCensusEntry,
    P8LockedSliceCount,
    require_label_census_production_adapter_and_consume_authorization,
    require_selected_cohort_source_seal_for_production,
    select_p8_locked_cohort,
)
from voice_coach.visual_feedback.adapters import (
    p8_production_frozen_pipeline as production_pipeline_module,
)
from voice_coach.visual_feedback.services.p8_aihub538_source_evaluator import (
    P8Aihub538OneShotRunner,
)


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _distributed_counts(
    *,
    kind: str,
    allowed_values: tuple[str, ...],
    present_values: tuple[str, ...],
    total: int,
) -> dict[tuple[str, str], int]:
    if not present_values or total < len(present_values):
        raise AssertionError("invalid fixture distribution")
    result = {(kind, value): 0 for value in allowed_values}
    for value in present_values:
        result[(kind, value)] = 1
    result[(kind, present_values[0])] += total - len(present_values)
    return result


def _entry(
    pair_id: int,
    *,
    angles: tuple[str, ...] = ("A",),
    noises: tuple[str, ...] = ("1",),
    environments: tuple[str, ...] = ("J",),
    eligible: int | None = None,
    excluded: int = 0,
) -> P8LockedLabelCensusEntry:
    eligible_count = eligible or max(len(angles), len(noises), len(environments))
    by_key: dict[tuple[str, str], int] = {}
    by_key.update(
        _distributed_counts(
            kind="angle",
            allowed_values=tuple("ABCDEFGHI"),
            present_values=angles,
            total=eligible_count,
        )
    )
    by_key.update(
        _distributed_counts(
            kind="noise",
            allowed_values=tuple(str(value) for value in range(1, 7)),
            present_values=noises,
            total=eligible_count,
        )
    )
    by_key.update(
        _distributed_counts(
            kind="environment",
            allowed_values=("J", "K"),
            present_values=environments,
            total=eligible_count,
        )
    )
    pair = P8_LOCKED_VALIDATION_SHARD_PAIRS[pair_id - 1]
    return P8LockedLabelCensusEntry(
        pair_id=pair_id,
        label_file_key=pair.label_file_key,
        label_archive_sha256=_sha(f"label-archive-{pair_id}"),
        label_content_root_sha256=_sha(f"label-content-{pair_id}"),
        admitted_label_count=eligible_count + excluded,
        identity_overlap_exclusion_count=excluded,
        eligible_label_count=eligible_count,
        slice_counts=tuple(
            P8LockedSliceCount(
                kind=kind,
                value=value,
                eligible_item_count=by_key[(kind, value)],
            )
            for kind, value in P8_REQUIRED_SLICE_KEYS
        ),
    )


def _seal() -> P8LockedCohortDiscoverySeal:
    return P8LockedCohortDiscoverySeal.issue(
        seal_id="p8_cohort_discovery_fixture_001",
        protocol_revision="p8_locked_cohort_discovery_v1",
        reference_identity_set_commitments_sha256=_sha(
            "p6-and-training-reference-identity-commitments"
        ),
        state_directory_device=1,
        state_directory_inode=2,
        sealed_at_utc="2026-08-28T00:00:00Z",
    )


def test_discovery_contract_pins_all_32_pairs_and_authorizes_nothing() -> None:
    contract = P8_LOCKED_COHORT_DISCOVERY_CONTRACT

    assert len(contract.candidate_pairs) == 32
    assert tuple(pair.pair_id for pair in contract.candidate_pairs) == tuple(
        range(1, 33)
    )
    assert len({pair.source_file_key for pair in contract.candidate_pairs}) == 32
    assert len({pair.label_file_key for pair in contract.candidate_pairs}) == 32
    assert contract.candidate_pairs[10].to_json_object() == {
        "pair_id": 11,
        "pair_name": "V11",
        "source": {
            "file_key": "55114",
            "partition_id": "VS11",
            "role": "locked_media_candidate",
            "listed_size": "18_GB",
        },
        "labels": {
            "file_key": "55082",
            "partition_id": "VL11",
            "role": "locked_label_census_candidate",
            "listed_size": "168_MB",
        },
    }
    assert contract.fixed_pair_preselection_forbidden is True
    assert contract.selection_rule_id == P8_LOCKED_COHORT_SELECTION_RULE_ID
    assert contract.label_download_authorized is False
    assert contract.label_census_access_authorized is False
    assert contract.source_download_authorized is False
    assert contract.source_media_open_authorized is False
    assert len(contract.required_slice_keys) == 17


def test_discovery_seal_is_read_zero_and_tamper_evident() -> None:
    seal = _seal()

    assert P8LockedCohortDiscoverySeal.from_json_object(
        seal.to_json_object()
    ) == seal
    document = seal.to_json_object()
    document["source_download_authorized"] = True
    with pytest.raises(
        P8LockedCohortContractError,
        match="premature_authorization",
    ):
        P8LockedCohortDiscoverySeal.from_json_object(document)


def test_label_census_can_select_one_pair_only_after_structural_proof() -> None:
    entries = tuple(
        _entry(
            pair_id,
            angles=tuple("ABCDEFGHI") if pair_id == 11 else ("A",),
            noises=tuple(str(value) for value in range(1, 7))
            if pair_id == 11
            else ("1",),
            environments=("J", "K") if pair_id == 11 else ("J",),
            eligible=18 if pair_id == 11 else 1,
        )
        for pair_id in range(1, 33)
    )

    receipt = select_p8_locked_cohort(
        discovery_seal=_seal(), census_entries=entries
    )

    assert receipt.selected_pair_ids == (11,)
    assert tuple(pair.pair_id for pair in receipt.selected_pairs) == (11,)
    assert all(item.eligible_item_count > 0 for item in receipt.selected_slice_counts)
    assert receipt.source_download_authorized is False
    assert receipt.source_media_open_authorized is False
    assert receipt.p8_evaluation_authorized is False
    serialized = receipt.to_json_object()
    assert P8LockedCohortSelectionReceipt.from_json_object(serialized) == receipt
    assert serialized["decode_tracker_sync_qc_claim_outcomes_used"] is False
    assert serialized["label_census_provenance_verified"] is False
    assert serialized["production_selection_receipt"] is False
    assert serialized["reselection_under_same_protocol_allowed"] is False
    assert serialized["source_media_access_count"] == 0
    assert serialized["locked_label_archive_access_count"] == 32
    assert serialized["raw_paths_serialized"] is False
    assert serialized["transcripts_serialized"] is False
    assert serialized["individual_identity_hmacs_serialized"] is False
    assert serialized["per_item_rows_serialized"] is False
    assert "speaker" not in json.dumps(serialized)

    tampered = dict(serialized)
    tampered["decode_tracker_sync_qc_claim_outcomes_used"] = True
    with pytest.raises(
        P8LockedCohortContractError,
        match="privacy_outcome_or_authority_invalid",
    ):
        P8LockedCohortSelectionReceipt.from_json_object(tampered)


def test_minimum_cover_uses_numeric_tie_break_not_portal_or_file_key_order() -> None:
    entries = []
    for pair_id in range(1, 33):
        if pair_id in {2, 4}:
            entries.append(
                _entry(
                    pair_id,
                    angles=tuple("BCDEFGHI"),
                    noises=tuple("23456"),
                    environments=("K",),
                    eligible=8,
                )
            )
        else:
            entries.append(_entry(pair_id))

    receipt = select_p8_locked_cohort(
        discovery_seal=_seal(), census_entries=tuple(entries)
    )

    # (1, 2) and (3, 4) are both two-pair covers.  The frozen numeric
    # lexicographic tie break selects (1, 2), independently of file-key order.
    assert receipt.selected_pair_ids == (1, 2)


def test_incomplete_full_candidate_census_requires_new_protocol() -> None:
    entries = tuple(_entry(pair_id) for pair_id in range(1, 33))

    with pytest.raises(
        P8LockedCohortContractError,
        match="new_protocol_required",
    ):
        select_p8_locked_cohort(
            discovery_seal=_seal(), census_entries=entries
        )


def test_missing_candidate_and_outcome_field_are_rejected() -> None:
    entries = tuple(_entry(pair_id) for pair_id in range(1, 33))
    with pytest.raises(
        P8LockedCohortContractError,
        match="census_universe_incomplete",
    ):
        select_p8_locked_cohort(
            discovery_seal=_seal(), census_entries=entries[:-1]
        )

    document = entries[0].to_json_object()
    document["decode_succeeded"] = True
    with pytest.raises(VisualFeedbackContractError):
        P8LockedLabelCensusEntry.from_json_object(document)


def test_production_source_access_remains_fail_closed_without_selected_seal() -> None:
    with pytest.raises(
        P8LockedCohortContractError,
        match="label_census_production_adapter_and_consume_authorization_required",
    ):
        require_label_census_production_adapter_and_consume_authorization()

    with pytest.raises(
        P8LockedCohortContractError,
        match="selected_cohort_source_seal_required",
    ):
        require_selected_cohort_source_seal_for_production()


def test_public_runner_guard_precedes_deferred_directory_and_consumption(
    tmp_path, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    monkeypatch.setattr(
        production_pipeline_module,
        "require_exact_production_p8_pipeline",
        lambda value: value,
    )
    input_directory = tmp_path / "must-not-be-opened"
    state_store = SimpleNamespace(
        load_actual_source_read_zero_seal=lambda: object()
    )
    runner = P8Aihub538OneShotRunner(state_store=state_store)

    with pytest.raises(
        P8LockedCohortContractError,
        match="selected_cohort_source_seal_required",
    ):
        runner.execute_from_completed_directory(
            confirm_irreversible_consumption=True,
            input_directory=input_directory,
            identity_key_descriptor=3,
            restricted_reference_directory_descriptor=4,
            frozen_pipeline=object(),
            output_directory=tmp_path / "output",
            expected_seal_sha256="a" * 64,
            expected_actual_source_seal_sha256="b" * 64,
            transaction_id="transaction_001",
            consumed_at_utc="2026-08-28T00:00:00Z",
            report_id="report_001",
            pending_at_utc="2026-08-28T00:00:01Z",
            cleanup_recorded_at_utc="2026-08-28T00:00:02Z",
            reload_verified_at_utc="2026-08-28T00:00:03Z",
            committed_at_utc="2026-08-28T00:00:04Z",
        )

    assert input_directory.exists() is False
