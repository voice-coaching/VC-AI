from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from voice_coach.references import r04_blind_evaluation_contract as contract


WORKSPACE = Path(__file__).resolve().parents[2]
CANONICAL_CONFIG = (
    WORKSPACE / "configs/experiments/r04_blind_multireference_evaluation_v1.yaml"
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _opaque(prefix: str, value: int) -> str:
    return f"{prefix}{value:032x}"


def _randomization(listener_count: int = 1) -> dict[str, object]:
    stimuli: list[dict[str, object]] = []
    by_sentence: dict[str, list[str]] = {}
    stimulus_number = 1
    for sentence_index in range(1, 21):
        sentence_id = f"R00-{sentence_index:02d}"
        sentence_ids: list[str] = []
        for candidate_index in range(3):
            blind_id = _opaque("r04stim_", stimulus_number)
            sentence_ids.append(blind_id)
            stimuli.append(
                {
                    "stimulus_blind_id": blind_id,
                    "sentence_id": sentence_id,
                    "audio_object_id_sha256": _sha(
                        f"object-{sentence_index}-{candidate_index}"
                    ),
                    "audio_sha256": _sha(f"audio-{sentence_index}-{candidate_index}"),
                    "r03_candidate_lineage_sha256": _sha(
                        f"lineage-{sentence_index}-{candidate_index}"
                    ),
                }
            )
            stimulus_number += 1
        by_sentence[sentence_id] = sentence_ids

    schedules: list[dict[str, object]] = []
    assignment_number = 1
    for listener_index in range(listener_count):
        assignments: list[dict[str, object]] = []
        for sentence_index in range(1, 21):
            sentence_id = f"R00-{sentence_index:02d}"
            ordered = list(by_sentence[sentence_id])
            if listener_index % 2:
                ordered.reverse()
            for position, blind_id in enumerate(ordered, start=1):
                assignments.append(
                    {
                        "task_type": "mos",
                        "assignment_id": _opaque(
                            "r04assign_",
                            assignment_number,
                        ),
                        "sentence_id": sentence_id,
                        "stimulus_blind_id": blind_id,
                        "within_sentence_position": position,
                        "block_position": 0,
                    }
                )
                assignment_number += 1
            assignments.append(
                {
                    "task_type": "cmos",
                    "assignment_id": _opaque(
                        "r04assign_",
                        assignment_number,
                    ),
                    "pair_blind_id": _opaque(
                        "r04pair_",
                        listener_index * 20 + sentence_index,
                    ),
                    "sentence_id": sentence_id,
                    "left_stimulus_blind_id": ordered[0],
                    "right_stimulus_blind_id": ordered[1],
                    "within_sentence_pair_position": 1,
                    "block_position": 0,
                }
            )
            assignment_number += 1
        first = assignments[:40]
        second = assignments[40:]
        for block_assignments in (first, second):
            for position, assignment in enumerate(block_assignments, start=1):
                assignment["block_position"] = position
        schedules.append(
            {
                "listener_id_sha256": _sha(f"listener-{listener_index}"),
                "schedule_id": _opaque("r04sched_", listener_index + 1),
                "randomization_seed_commitment_sha256": _sha(f"seed-{listener_index}"),
                "blocks": [
                    {
                        "block_id": _opaque(
                            "r04block_",
                            listener_index * 2 + 1,
                        ),
                        "block_order": 1,
                        "mandatory_break_after": True,
                        "assignments": first,
                    },
                    {
                        "block_id": _opaque(
                            "r04block_",
                            listener_index * 2 + 2,
                        ),
                        "block_order": 2,
                        "mandatory_break_after": False,
                        "assignments": second,
                    },
                ],
            }
        )
    return {
        "schema_version": "voice_coach.r04_randomization_manifest.v1",
        "randomization_id": _opaque("r04rand_", 1),
        "status": "randomized_and_locked_before_rating",
        "r03_completion_commit_sha256": _sha("r03-commit"),
        "r03_bank_manifest_sha256": _sha("r03-bank"),
        "r04_config_sha256": _sha("r04-config"),
        "r04_preregistration_sha256": _sha("r04-prereg"),
        "statistical_analysis_plan_sha256": _sha("r04-sap"),
        "privacy_zone": "D1_pseudonymized_restricted_experiment",
        "per_listener_within_sentence_randomization": True,
        "fatigue_blocks_present": True,
        "system_names_present": False,
        "filenames_or_paths_present": False,
        "synthesis_status_present": False,
        "source_labels_present": False,
        "ratings_present": False,
        "stimuli": stimuli,
        "listener_schedules": schedules,
    }


def _ratings(
    randomization: dict[str, object],
) -> dict[str, object]:
    mos: list[dict[str, object]] = []
    cmos: list[dict[str, object]] = []
    sequence = 1
    for schedule in randomization["listener_schedules"]:  # type: ignore[index]
        listener_id = schedule["listener_id_sha256"]
        for block in schedule["blocks"]:
            for assignment in block["assignments"]:
                if assignment["task_type"] == "mos":
                    mos.append(
                        {
                            "listener_id_sha256": listener_id,
                            "assignment_id": assignment["assignment_id"],
                            "sentence_id": assignment["sentence_id"],
                            "stimulus_blind_id": assignment["stimulus_blind_id"],
                            "submission_sequence": sequence,
                            "naturalness": 3,
                            "clarity": 3,
                            "target_phone_accuracy": 3,
                            "pedagogical_suitability": 3,
                        }
                    )
                else:
                    cmos.append(
                        {
                            "listener_id_sha256": listener_id,
                            "assignment_id": assignment["assignment_id"],
                            "pair_blind_id": assignment["pair_blind_id"],
                            "sentence_id": assignment["sentence_id"],
                            "left_stimulus_blind_id": assignment[
                                "left_stimulus_blind_id"
                            ],
                            "right_stimulus_blind_id": assignment[
                                "right_stimulus_blind_id"
                            ],
                            "submission_sequence": sequence,
                            "cmos": 0,
                        }
                    )
                sequence += 1
    return {
        "schema_version": "voice_coach.r04_rating_manifest.v1",
        "rating_manifest_id": _opaque("r04ratings_", 1),
        "status": "ratings_locked_blinded",
        "randomization_manifest_sha256": hashlib.sha256(
            contract.canonical_json_bytes(randomization)
        ).hexdigest(),
        "human_research_approval_sha256": _sha("human-approval"),
        "r04_preregistration_sha256": _sha("r04-prereg"),
        "statistical_analysis_plan_sha256": _sha("r04-sap"),
        "listener_attestation_manifest_sha256": _sha("listener-attestations"),
        "privacy_zone": "D1_pseudonymized_restricted_experiment",
        "system_names_present": False,
        "filenames_or_paths_present": False,
        "synthesis_status_present": False,
        "source_labels_present": False,
        "direct_identifiers_present": False,
        "free_text_present": False,
        "mos_unit": "listener_x_sentence_x_candidate",
        "cmos_unit": "listener_x_sentence_x_same_sentence_pair",
        "mos_ratings": mos,
        "cmos_ratings": cmos,
    }


def test_canonical_static_config_is_valid_without_runtime_access() -> None:
    config = contract.load_config(CANONICAL_CONFIG)
    audit = contract.AccessAudit()

    result = contract.validate_static_config(
        workspace_root=WORKSPACE,
        config_path=CANONICAL_CONFIG,
        config=config,
        audit=audit,
    )

    assert result.config_sha256 == _sha(CANONICAL_CONFIG.read_text())
    assert len(result.source_snapshots) == 25
    assert audit.source_pin_read_count == 25
    assert audit.r00_deep_validation_count == 0
    assert audit.d1_audio_path_resolution_count == 0
    assert audit.d1_rating_path_resolution_count == 0
    assert audit.output_path_resolution_count == 0


def test_valid_randomization_is_blinded_per_listener_and_fatigue_blocked() -> None:
    result = contract.validate_randomization_manifest_payload(_randomization(2))

    assert result["sentence_count"] == 20
    assert result["stimulus_count"] == 60
    assert result["listener_count"] == 2
    assert result["assignment_count"] == 160
    assert result["blind_source_labels_present"] is False
    assert result["publication_allowed"] is False


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda payload: payload.update({"status": "human_recording"}),
            "blind source/system",
        ),
        (
            lambda payload: payload["listener_schedules"][0]["blocks"][0].update(  # type: ignore[index]
                {"mandatory_break_after": False}
            ),
            "mandatory fatigue break",
        ),
        (
            lambda payload: payload["listener_schedules"][0]["blocks"][0][  # type: ignore[index]
                "assignments"
            ][0].update({"within_sentence_position": 2}),
            "randomized order",
        ),
        (
            lambda payload: payload["listener_schedules"][0]["blocks"][0][  # type: ignore[index]
                "assignments"
            ][3].update(
                {
                    "right_stimulus_blind_id": payload["stimuli"][3][  # type: ignore[index]
                        "stimulus_blind_id"
                    ]
                }
            ),
            "same-sentence",
        ),
    ],
)
def test_randomization_rejects_blindness_order_and_pair_violations(
    mutation: object,
    message: str,
) -> None:
    payload = _randomization()
    mutation(payload)  # type: ignore[operator]

    with pytest.raises(contract.R04ContractError, match=message):
        contract.validate_randomization_manifest_payload(payload)


def test_randomization_enforces_preregistered_fatigue_maximum() -> None:
    with pytest.raises(contract.R04ContractError, match="fatigue block exceeds"):
        contract.validate_randomization_manifest_payload(
            _randomization(),
            maximum_assignments_per_block=39,
        )


def test_rating_manifest_binds_every_randomized_and_attested_unit() -> None:
    randomization = _randomization(2)
    ratings = _ratings(randomization)
    attested = [
        schedule["listener_id_sha256"]
        for schedule in randomization["listener_schedules"]  # type: ignore[index]
    ]

    result = contract.validate_rating_manifest_payload(
        ratings,
        randomization_manifest=randomization,
        attested_listener_ids=attested,
    )

    assert result == {
        "status": "valid_blinded_raw_ratings",
        "listener_count": 2,
        "mos_rating_count": 120,
        "cmos_rating_count": 40,
        "mos_or_cmos_computed": False,
        "statistics_computed": False,
        "publication_allowed": False,
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("naturalness", 6, "must be <= 5"),
        ("clarity", True, "must be an integer"),
    ],
)
def test_rating_rejects_invalid_mos(
    field: str,
    value: object,
    message: str,
) -> None:
    randomization = _randomization()
    ratings = _ratings(randomization)
    ratings["mos_ratings"][0][field] = value  # type: ignore[index]

    with pytest.raises(contract.R04ContractError, match=message):
        contract.validate_rating_manifest_payload(ratings)


def test_rating_rejects_invalid_cmos_and_free_text() -> None:
    randomization = _randomization()
    ratings = _ratings(randomization)
    ratings["cmos_ratings"][0]["cmos"] = -4  # type: ignore[index]
    with pytest.raises(contract.R04ContractError, match="must be >= -3"):
        contract.validate_rating_manifest_payload(ratings)

    ratings = _ratings(randomization)
    ratings["mos_ratings"][0]["free_text"] = "synthetic"  # type: ignore[index]
    with pytest.raises(contract.R04ContractError, match="identity/path"):
        contract.validate_rating_manifest_payload(ratings)


def test_rating_rejects_unscheduled_or_unattested_listener() -> None:
    randomization = _randomization()
    ratings = _ratings(randomization)
    ratings["mos_ratings"][0]["assignment_id"] = _opaque(  # type: ignore[index]
        "r04assign_",
        9999,
    )
    with pytest.raises(contract.R04ContractError, match="not randomized"):
        contract.validate_rating_manifest_payload(
            ratings,
            randomization_manifest=randomization,
        )

    ratings = _ratings(randomization)
    with pytest.raises(contract.R04ContractError, match="attested listeners"):
        contract.validate_rating_manifest_payload(
            ratings,
            attested_listener_ids=[_sha("different-listener")],
        )


def test_canonical_templates_remain_fail_closed() -> None:
    prereg = contract.load_json(
        WORKSPACE
        / "configs/reference/r04_blind_evaluation_preregistration_v1.template.json"
    )
    with pytest.raises(contract.R04ContractError, match="unresolved TBD"):
        contract.validate_preregistration_payload(
            prereg,
            r03_completion_commit_sha256=_sha("r03"),
            r04_config_sha256=_sha("r04"),
        )


def test_r03_completion_tbd_stops_before_path_resolution() -> None:
    config = contract.load_config(CANONICAL_CONFIG)
    audit = contract.AccessAudit()

    with pytest.raises(contract.R04ContractError, match="TBD_R03_COMPLETION"):
        contract.deep_validate_r03_completion(
            workspace_root=WORKSPACE,
            config=config,
            r03_module=object(),  # type: ignore[arg-type]
            r01=object(),
            r02=object(),
            audit=audit,
        )

    assert audit.r03_completion_path_resolution_count == 0
    assert audit.r03_completion_read_attempt_count == 0
    assert audit.r03_bank_manifest_read_count == 0


def test_future_outputs_reject_existing_target_and_parent_symlink(
    tmp_path: Path,
) -> None:
    outputs = {
        "randomization_schema_path": "static/randomization.json",
        "rating_schema_path": "static/rating.json",
        "randomization_manifest_path": "future/randomization.json",
        "raw_rating_manifest_path": "future/ratings.json",
        "no_overwrite": True,
        "atomic_publish_required": True,
        "write_enabled_in_this_package": False,
    }
    (tmp_path / "future").mkdir()
    (tmp_path / "future/randomization.json").write_text("occupied")
    with pytest.raises(contract.R04ContractError, match="no-overwrite"):
        contract.ensure_future_output_targets_absent(
            workspace_root=tmp_path,
            outputs=outputs,
        )

    (tmp_path / "future/randomization.json").unlink()
    (tmp_path / "future").rmdir()
    (tmp_path / "real").mkdir()
    (tmp_path / "future").symlink_to(tmp_path / "real", target_is_directory=True)
    with pytest.raises(contract.R04ContractError, match="symlink prohibited"):
        contract.ensure_future_output_targets_absent(
            workspace_root=tmp_path,
            outputs=outputs,
        )


def test_future_outputs_reject_path_traversal() -> None:
    outputs = {
        "randomization_manifest_path": "../outside.json",
        "no_overwrite": True,
        "atomic_publish_required": True,
        "write_enabled_in_this_package": False,
    }
    with pytest.raises(contract.R04ContractError, match="workspace-relative"):
        contract.ensure_future_output_targets_absent(
            workspace_root=WORKSPACE,
            outputs=outputs,
        )
