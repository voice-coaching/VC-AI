from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from voice_coach.references import d08_native_reference_contract as contract


WORKSPACE = Path(__file__).resolve().parents[2]
CONFIG_PATH = (
    WORKSPACE / "configs/experiments/d08_native_multispeaker_reference_v1.yaml"
)
H00_SHA = "1" * 64
CONSENT_SHA = "2" * 64
RIGHTS_SHA = "3" * 64
PREREG_SHA = "4" * 64
METADATA_SHA = "5" * 64
QC_SHA = "6" * 64
CONFIG_SHA = "7" * 64
SPEAKER_ID = hashlib.sha256(b"d08-speaker").hexdigest()
EQUIPMENT_ID = hashlib.sha256(b"d08-equipment").hexdigest()
MICROPHONE_ID = hashlib.sha256(b"d08-microphone").hexdigest()
ROOM_ID = hashlib.sha256(b"d08-room").hexdigest()
CONSENT_RECEIPT = hashlib.sha256(b"d08-consent-receipt").hexdigest()
RIGHTS_RECEIPT = hashlib.sha256(b"d08-rights-receipt").hexdigest()


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _opaque(prefix: str, label: str) -> str:
    return prefix + _digest(label)[:32]


def _config() -> dict[str, object]:
    return deepcopy(dict(contract.load_config(CONFIG_PATH)))


def _h00() -> dict[str, object]:
    return {
        "schema_version": "voice_coach.d08_h00_institutional_approval.v1",
        "attestation_id": "d08-h00-approved",
        "status": "approved_for_d08_recording",
        "signed_at_utc": "2030-01-01T00:00:00Z",
        "signed_by_role": "institutional_research_authority",
        "determination": {
            "approval_or_determination_id": "D08-APPROVAL-1",
            "protocol_revision": "v1",
            "approval_document_sha256": _digest("approval-document"),
            "valid_from": "2030-01-01",
            "valid_until": "2031-01-01",
        },
        "approved_scope": {
            "adult_native_korean_speaker_recruitment": True,
            "voice_recording": True,
            "automated_qc": True,
            "human_listening_qc": True,
            "research_reference_use": True,
            "model_input_reviewed_separately": True,
            "third_party_provision_reviewed_separately": True,
        },
        "privacy_governance": {
            "access_control_plan_sha256": _digest("access"),
            "retention_plan_sha256": _digest("retention"),
            "deletion_plan_sha256": _digest("deletion"),
            "withdrawal_process_sha256": _digest("withdrawal"),
            "incident_response_plan_sha256": _digest("incident"),
            "r0_r1_d1_a1_boundary_approved": True,
        },
        "d08_collection_allowed": True,
        "completion_claim_allowed": False,
    }


def _consent(*, floor: int = 1) -> dict[str, object]:
    return {
        "schema_version": "voice_coach.d08_individual_consent_attestation.v1",
        "attestation_id": "d08-consent-approved",
        "status": "approved_active_individual_consent",
        "signed_at_utc": "2030-01-01T00:00:00Z",
        "signed_by_role": "consent_custodian",
        "h00_approval_sha256": H00_SHA,
        "consent_protocol": {
            "protocol_id": "d08-consent-v1",
            "protocol_revision": "v1",
            "protocol_sha256": _digest("consent-protocol"),
            "consent_form_sha256": _digest("consent-form"),
            "voice_recording_allowed": True,
            "research_reference_use_allowed": True,
            "automated_qc_allowed": True,
            "human_listening_qc_allowed": True,
            "model_input_allowed": False,
            "third_party_provision_allowed": False,
            "public_release_allowed": False,
            "commercial_use_allowed": False,
            "withdrawal_deadline_policy_id": "before-aggregate-lock-v1",
            "withdrawal_process_sha256": _digest("withdrawal-process"),
            "retention_policy_sha256": _digest("retention-policy"),
            "deletion_protocol_sha256": _digest("deletion-protocol"),
        },
        "speaker_receipts": [
            {
                "speaker_id_sha256": SPEAKER_ID,
                "consent_receipt_sha256": CONSENT_RECEIPT,
                "scope_decision_sha256": _digest("scope"),
                "adult_attested": True,
                "native_korean_attested": True,
                "consent_active": True,
                "withdrawn": False,
                "expired": False,
                "valid_from": "2030-01-01",
                "valid_until": "2031-01-01",
                "permitted_sentence_ids": list(contract.SENTENCE_IDS),
            }
        ],
        "receipt_contract": {
            "planning_target_minimum_distinct_speakers_per_sentence": 4,
            "binding_completion_floor": floor,
            "speaker_id_format": "namespaced_lowercase_sha256",
            "direct_identifiers_allowed": False,
            "identity_mapping_zone": "R1_separate_access_controlled",
            "raw_audio_zone": "R0_restricted_raw_audio",
            "each_receipt_requires_scope_decision_sha256": True,
            "inactive_withdrawn_or_expired_receipts_prohibited": True,
        },
        "completion_claim_allowed": False,
    }


def _rights() -> dict[str, object]:
    return {
        "schema_version": "voice_coach.d08_recording_rights_attestation.v1",
        "attestation_id": "d08-rights-approved",
        "status": "approved_active_recording_rights",
        "signed_at_utc": "2030-01-01T00:00:00Z",
        "signed_by_role": "rights_custodian",
        "h00_approval_sha256": H00_SHA,
        "individual_consent_attestation_sha256": CONSENT_SHA,
        "source_classification": {
            "source_kind": "newly_recorded_consented_adult_native_korean_human",
            "public_corpus_audio_used": False,
            "tts_audio_used": False,
            "corpus_or_tts_disguised_as_human": False,
            "human_tts_source_label_required": True,
            "license_basis": "individual_recording_license_grants",
        },
        "recording_protocol": {
            "protocol_id": "d08-recording-v1",
            "protocol_revision": "v1",
            "protocol_sha256": _digest("recording-protocol"),
            "r00_card_sha256": contract.R00_CARD_SHA256,
            "natural_take_count_per_speaker_sentence": 2,
            "clear_educational_take_count_per_speaker_sentence": 1,
            "sample_rate_hz": 48000,
            "bit_depth": 24,
            "channel_count": 1,
            "container_format": "WAV",
            "codec": "PCM",
            "same_equipment_profile_required": True,
            "same_microphone_profile_required": True,
            "same_room_profile_required": True,
            "mouth_to_microphone_distance_min_cm": 15,
            "mouth_to_microphone_distance_max_cm": 20,
            "equipment_profile_id_sha256": EQUIPMENT_ID,
            "microphone_profile_id_sha256": MICROPHONE_ID,
            "room_profile_id_sha256": ROOM_ID,
        },
        "speaker_rights_receipts": [
            {
                "speaker_id_sha256": SPEAKER_ID,
                "recording_license_grant_sha256": RIGHTS_RECEIPT,
                "permitted_use_scope_sha256": _digest("permitted-scope"),
                "right_active": True,
                "withdrawn": False,
                "expired": False,
                "research_reference_use_allowed": True,
                "automated_qc_allowed": True,
                "human_listening_qc_allowed": True,
                "model_input_allowed": False,
                "third_party_provision_allowed": False,
                "public_release_allowed": False,
                "commercial_use_allowed": False,
                "permitted_sentence_ids": list(contract.SENTENCE_IDS),
            }
        ],
        "rights_contract": {
            "each_speaker_requires_recording_license_grant_sha256": True,
            "each_speaker_requires_all_20_sentence_permissions": True,
            "active_nonwithdrawn_nonexpired_right_required": True,
            "raw_audio_redistribution_allowed": False,
            "public_release_allowed": False,
            "commercial_use_allowed": False,
            "source_and_license_must_be_explicit_per_reference": True,
        },
        "completion_claim_allowed": False,
    }


def _preregistration(*, floor: int = 1) -> dict[str, object]:
    return {
        "schema_version": "voice_coach.d08_qc_preregistration.v1",
        "preregistration_id": "d08-qc-prereg-v1",
        "status": "registered_and_frozen",
        "registered_at_utc": "2030-01-01T00:00:00Z",
        "registered_by_role": "independent_qc_lead",
        "frozen_before_recruitment_or_audio_access": True,
        "r00_card_sha256": contract.R00_CARD_SHA256,
        "speaker_contract": {
            "planning_target_minimum_distinct_speakers_per_sentence": 4,
            "binding_minimum_distinct_speakers_per_sentence": floor,
            "target_shortfall_reporting_rule": "report_every_sentence_shortfall",
        },
        "fixed_qc_gates": {
            "clipping_ratio_operator": "<",
            "clipping_ratio_threshold": 0.001,
            "decoder_error_count_required": 0,
            "human_reviewer_pool_size": 2,
            "minimum_human_listener_confirmations": 1,
            "asr_role": "candidate_triage_only_not_final_qc",
        },
        "measurement_contracts": {
            "dc_offset": {
                "metric_id": "absolute_dc_mean_v1",
                "unit": "normalized_amplitude",
                "direction": "absolute_maximum",
                "threshold": 0.1,
            },
            "content_match": {
                "metric_id": "registered_content_score_v1",
                "unit": "proportion",
                "direction": "minimum",
                "threshold": 0.9,
            },
            "noise": {
                "metric_id": "registered_noise_score_v1",
                "unit": "ratio",
                "direction": "maximum",
                "threshold": 0.2,
            },
            "reverb": {
                "metric_id": "registered_reverb_score_v1",
                "unit": "ratio",
                "direction": "maximum",
                "threshold": 0.3,
            },
            "interruption": {
                "metric_id": "registered_interruption_count_v1",
                "unit": "count",
                "direction": "exact",
                "threshold": 0,
            },
        },
        "adjudication_contract": {
            "trigger_rule_id": "reviewer_disagreement_v1",
            "decision_rule_id": "independent_adjudicator_v1",
            "adjudicator_role": "independent_native_qc_adjudicator",
            "independence_required": True,
            "receipt_required_when_triggered": True,
        },
        "approval": {
            "decision": "approved",
            "protocol_sha256": _digest("qc-protocol"),
            "signed_receipt_sha256": _digest("qc-approval"),
        },
        "completion_claim_allowed": False,
    }


def _sentence_hashes() -> dict[str, str]:
    return {sentence_id: _digest(sentence_id) for sentence_id in contract.SENTENCE_IDS}


def _metadata() -> dict[str, object]:
    rows: list[dict[str, object]] = []
    sentence_hashes = _sentence_hashes()
    for sentence_id in contract.SENTENCE_IDS:
        for style, take_index in (
            ("natural", 1),
            ("natural", 2),
            ("clear_educational", 1),
        ):
            label = f"{sentence_id}-{style}-{take_index}"
            rows.append(
                {
                    "recording_id": _opaque("d08r_", label),
                    "speaker_id_sha256": SPEAKER_ID,
                    "sentence_id": sentence_id,
                    "target_text_sha256": sentence_hashes[sentence_id],
                    "speaking_style": style,
                    "take_index": take_index,
                    "session_id_sha256": _digest("session"),
                    "equipment_profile_id_sha256": EQUIPMENT_ID,
                    "microphone_profile_id_sha256": MICROPHONE_ID,
                    "room_profile_id_sha256": ROOM_ID,
                    "r0_audio_locator_token_sha256": _digest("locator-" + label),
                    "audio_sha256": _digest("audio-" + label),
                    "sample_rate_hz": 48000,
                    "bit_depth": 24,
                    "channel_count": 1,
                    "container_format": "WAV",
                    "codec": "PCM",
                    "mouth_to_microphone_distance_cm": 17.5,
                    "source_kind": "human_recording",
                    "consent_receipt_sha256": CONSENT_RECEIPT,
                    "recording_license_grant_sha256": RIGHTS_RECEIPT,
                    "native_korean_attested": True,
                    "raw_audio_zone": "R0_restricted_raw_audio",
                    "locator_manifest_zone": ("R1_access_controlled_locator_metadata"),
                    "publication_allowed": False,
                }
            )
    return {
        "schema_version": "voice_coach.d08_recording_metadata_manifest.v1",
        "manifest_id": _opaque("d08m_", "manifest"),
        "status": "recorded_pending_qc",
        "r00_card_sha256": contract.R00_CARD_SHA256,
        "h00_approval_sha256": H00_SHA,
        "individual_consent_attestation_sha256": CONSENT_SHA,
        "recording_rights_attestation_sha256": RIGHTS_SHA,
        "qc_preregistration_sha256": PREREG_SHA,
        "privacy_zone": "R1_access_controlled_locator_metadata",
        "publication_allowed": False,
        "contains_direct_identifiers": False,
        "contains_free_text": False,
        "rows": rows,
    }


def _validate_metadata(
    payload: dict[str, object] | None = None,
    *,
    preregistration: dict[str, object] | None = None,
) -> dict[str, object]:
    return dict(
        contract.validate_recording_metadata_payload(
            _metadata() if payload is None else payload,
            sentence_hashes=_sentence_hashes(),
            h00_sha256=H00_SHA,
            consent_sha256=CONSENT_SHA,
            rights_sha256=RIGHTS_SHA,
            preregistration_sha256=PREREG_SHA,
            consent_payload=_consent(),
            rights_payload=_rights(),
            preregistration_payload=(
                _preregistration() if preregistration is None else preregistration
            ),
        )
    )


def _qc(metadata_summary: dict[str, object]) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for recording_id in metadata_summary["recording_ids"]:
        rows.append(
            {
                "recording_id": recording_id,
                "decoder_error_count": 0,
                "clipping_ratio": 0.0005,
                "dc_offset_metric_id": "absolute_dc_mean_v1",
                "dc_offset_value": 0.01,
                "dc_offset_pass": True,
                "content_match_metric_id": "registered_content_score_v1",
                "content_match_value": 1.0,
                "content_match_pass": True,
                "asr_used_for_triage_only": True,
                "asr_used_as_final_qc": False,
                "human_reviewer_pool_size": 2,
                "human_listener_confirmation_count": 1,
                "human_content_and_pronunciation_confirmed": True,
                "noise_metric_id": "registered_noise_score_v1",
                "noise_value": 0.1,
                "noise_pass": True,
                "reverb_metric_id": "registered_reverb_score_v1",
                "reverb_value": 0.1,
                "reverb_pass": True,
                "interruption_metric_id": "registered_interruption_count_v1",
                "interruption_value": 0,
                "interruption_pass": True,
                "adjudication_required": False,
                "adjudication_status": "not_required",
                "adjudication_receipt_sha256": None,
                "final_qc_pass": True,
                "exclusion_reason_code": "none",
            }
        )
    return {
        "schema_version": "voice_coach.d08_qc_report.v1",
        "report_id": _opaque("d08q_", "qc-report"),
        "status": "qc_completed_pending_completion",
        "metadata_manifest_sha256": METADATA_SHA,
        "qc_preregistration_sha256": PREREG_SHA,
        "privacy_zone": "D1_pseudonymized_restricted_qc",
        "contains_r0_locator": False,
        "contains_direct_identifiers": False,
        "contains_free_text": False,
        "publication_allowed": False,
        "rows": rows,
    }


def _validate_qc(
    payload: dict[str, object],
    metadata_summary: dict[str, object],
) -> dict[str, object]:
    return dict(
        contract.validate_qc_report_payload(
            payload,
            metadata_manifest_sha256=METADATA_SHA,
            preregistration_sha256=PREREG_SHA,
            recording_ids=metadata_summary["recording_ids"],
            preregistration_payload=_preregistration(),
        )
    )


def _completion(
    metadata_summary: dict[str, object],
) -> dict[str, object]:
    sentence_summary = metadata_summary["sentence_summary"]
    sentences = []
    for sentence_id in contract.SENTENCE_IDS:
        summary = sentence_summary[sentence_id]
        distinct = summary["distinct_speaker_count"]
        sentences.append(
            {
                "sentence_id": sentence_id,
                **summary,
                "speaker_floor_pass": True,
                "planning_target_met": distinct >= 4,
                "exact_take_pattern_pass": True,
                "all_qc_pass": True,
            }
        )
    return {
        "schema_version": "voice_coach.d08_completion.v1",
        "completion_id": _opaque("d08c_", "completion"),
        "status": "completed",
        "config_sha256": CONFIG_SHA,
        "r00_card_sha256": contract.R00_CARD_SHA256,
        "h00_approval_sha256": H00_SHA,
        "individual_consent_attestation_sha256": CONSENT_SHA,
        "recording_rights_attestation_sha256": RIGHTS_SHA,
        "qc_preregistration_sha256": PREREG_SHA,
        "metadata_manifest_sha256": METADATA_SHA,
        "qc_report_sha256": QC_SHA,
        "privacy_zone": "A1_aggregate_review_required",
        "contains_row_level_data": False,
        "contains_direct_or_pseudonymous_ids": False,
        "contains_audio_or_locator_hashes": False,
        "contains_free_text": False,
        "sentence_count": 20,
        "binding_minimum_distinct_speakers_per_sentence": 1,
        "planning_target_minimum_distinct_speakers_per_sentence": 4,
        "planning_target_met_all_sentences": False,
        "same_equipment_and_space_verified": True,
        "format_and_distance_verified": True,
        "source_license_honesty_verified": True,
        "qc_adjudication_contract_verified": True,
        "sentences": sentences,
        "totals": {
            "distinct_speaker_sentence_pairs": 20,
            "accepted_natural_take_count": 40,
            "accepted_clear_educational_take_count": 20,
            "accepted_take_count": 60,
            "failed_take_count": 0,
            "decoder_error_count": 0,
            "clipping_failure_count": 0,
            "dc_offset_failure_count": 0,
            "content_mismatch_failure_count": 0,
            "noise_failure_count": 0,
            "reverb_failure_count": 0,
            "interruption_failure_count": 0,
            "human_review_failure_count": 0,
            "adjudication_failure_count": 0,
        },
        "actual_recruitment_completed": True,
        "actual_consent_completed": True,
        "actual_recording_completed": True,
        "actual_qc_completed": True,
        "completion_claim_allowed": True,
        "publication_allowed": False,
        "implementation_git_commit": "a" * 40,
    }


def test_canonical_static_config_is_valid_and_runtime_is_zero() -> None:
    audit = contract.AccessAudit()
    static = contract.validate_static_config(
        workspace_root=WORKSPACE,
        config_path=CONFIG_PATH,
        config=_config(),
        audit=audit,
    )

    assert len(static.source_snapshots) == 19
    assert audit.source_pin_read_count == 19
    assert all(audit.as_dict()[field] == 0 for field in contract.RUNTIME_FIELDS)


def test_duplicate_and_nonfinite_yaml_fail_closed(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text("schema_version: 1\nschema_version: 1\n", encoding="utf-8")
    with pytest.raises(contract.D08ContractError, match="duplicate key"):
        contract.load_config(duplicate)

    nonfinite = tmp_path / "nonfinite.yaml"
    nonfinite.write_text("value: .nan\n", encoding="utf-8")
    with pytest.raises(contract.D08ContractError, match="non-finite"):
        contract.load_config(nonfinite)


def test_duplicate_json_and_read_time_mutation_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(contract.D08ContractError, match="duplicate key"):
        contract._parse_json(b'{"value":1,"value":2}', "duplicate JSON")

    changing = tmp_path / "changing.json"
    changing.write_text("{}", encoding="utf-8")

    def _mutate() -> None:
        changing.write_text('{"changed":true}', encoding="utf-8")

    with pytest.raises(contract.D08ContractError, match="changed while"):
        contract._read_file(
            tmp_path,
            "changing.json",
            "changing source",
            before_second_stat_hook=_mutate,
        )


def test_source_hash_drift_and_binding_floor_invention_are_rejected() -> None:
    drifted = _config()
    drifted["canonical_sources"]["pins"][0]["sha256"] = "f" * 64
    with pytest.raises(contract.D08ContractError, match="SHA-256 drift"):
        contract.validate_static_config(
            workspace_root=WORKSPACE,
            config_path=CONFIG_PATH,
            config=drifted,
        )

    invented = _config()
    invented["recording_design"][
        "binding_completion_minimum_distinct_speakers_per_sentence"
    ] = 4
    with pytest.raises(contract.D08ContractError, match="binding speaker floor"):
        contract.validate_static_config(
            workspace_root=WORKSPACE,
            config_path=CONFIG_PATH,
            config=invented,
        )


def test_output_and_static_source_overlap_is_rejected() -> None:
    overlapped = _config()
    overlapped["outputs"]["metadata_manifest_path"] = (
        "configs/reference/d08_qc_report_v1.schema.json"
    )
    with pytest.raises(contract.D08ContractError, match="output/source overlap"):
        contract.validate_static_config(
            workspace_root=WORKSPACE,
            config_path=CONFIG_PATH,
            config=overlapped,
        )


def test_format_clipping_and_unresolved_qc_policy_are_exact() -> None:
    wrong_format = _config()
    wrong_format["recording_design"]["audio_format"]["bit_depth"] = 16
    with pytest.raises(contract.D08ContractError, match="audio format"):
        contract.validate_static_config(
            workspace_root=WORKSPACE,
            config_path=CONFIG_PATH,
            config=wrong_format,
        )

    wrong_clipping = _config()
    wrong_clipping["quality_control"]["fixed_gates"]["clipping_ratio_threshold"] = 0.01
    with pytest.raises(contract.D08ContractError, match="clipping"):
        contract.validate_static_config(
            workspace_root=WORKSPACE,
            config_path=CONFIG_PATH,
            config=wrong_clipping,
        )


def test_templates_cannot_be_used_as_actual_approval_or_preregistration() -> None:
    with pytest.raises(contract.D08ContractError, match="unresolved"):
        contract.validate_h00_approval_payload(
            json.loads(
                (
                    WORKSPACE / "configs/reference/"
                    "d08_h00_institutional_approval_v1.template.json"
                ).read_text(encoding="utf-8")
            )
        )
    with pytest.raises(contract.D08ContractError, match="unresolved"):
        contract.validate_qc_preregistration_payload(
            json.loads(
                (
                    WORKSPACE / "configs/reference/"
                    "d08_qc_preregistration_v1.template.json"
                ).read_text(encoding="utf-8")
            )
        )


def test_future_approval_consent_rights_and_preregistration_validate() -> None:
    contract.validate_h00_approval_payload(_h00())
    consent = contract.validate_individual_consent_payload(
        _consent(), h00_sha256=H00_SHA
    )
    rights = contract.validate_recording_rights_payload(
        _rights(),
        h00_sha256=H00_SHA,
        consent_sha256=CONSENT_SHA,
        consent_receipts=consent,
    )
    prereg = contract.validate_qc_preregistration_payload(_preregistration())

    assert set(consent) == {SPEAKER_ID}
    assert set(rights) == {SPEAKER_ID}
    assert prereg["binding_minimum_distinct_speakers_per_sentence"] == 1


def test_revoked_consent_and_disguised_source_are_rejected() -> None:
    revoked = _consent()
    revoked["speaker_receipts"][0]["withdrawn"] = True
    with pytest.raises(contract.D08ContractError, match="withdrawn"):
        contract.validate_individual_consent_payload(revoked, h00_sha256=H00_SHA)

    consent = contract.validate_individual_consent_payload(
        _consent(), h00_sha256=H00_SHA
    )
    disguised = _rights()
    disguised["source_classification"]["public_corpus_audio_used"] = True
    with pytest.raises(contract.D08ContractError, match="public_corpus"):
        contract.validate_recording_rights_payload(
            disguised,
            h00_sha256=H00_SHA,
            consent_sha256=CONSENT_SHA,
            consent_receipts=consent,
        )


def test_metadata_enforces_20_sentences_exact_takes_format_setup_and_rights() -> None:
    summary = _validate_metadata()

    assert summary["row_count"] == 60
    assert summary["distinct_speaker_sentence_pairs"] == 20
    assert summary["planning_target_met_all_sentences"] is False

    missing_take = _metadata()
    missing_take["rows"].pop()
    with pytest.raises(contract.D08ContractError, match="exactly natural"):
        _validate_metadata(missing_take)

    wrong_microphone = _metadata()
    wrong_microphone["rows"][0]["microphone_profile_id_sha256"] = _digest(
        "other-microphone"
    )
    with pytest.raises(contract.D08ContractError, match="microphone"):
        _validate_metadata(wrong_microphone)

    wrong_distance = _metadata()
    wrong_distance["rows"][0]["mouth_to_microphone_distance_cm"] = 21
    with pytest.raises(contract.D08ContractError, match="15-20"):
        _validate_metadata(wrong_distance)


def test_preregistered_speaker_floor_is_not_the_planning_target() -> None:
    prereg = _preregistration(floor=2)
    with pytest.raises(contract.D08ContractError, match="speaker floor"):
        _validate_metadata(preregistration=prereg)


def test_qc_report_enforces_fixed_and_preregistered_gates() -> None:
    metadata_summary = _validate_metadata()
    payload = _qc(metadata_summary)
    result = _validate_qc(payload, metadata_summary)

    assert result["all_qc_pass"] is True
    assert result["row_count"] == 60

    clipping = _qc(metadata_summary)
    clipping["rows"][0]["clipping_ratio"] = 0.001
    clipping["rows"][0]["final_qc_pass"] = False
    clipping["rows"][0]["exclusion_reason_code"] = "clipping"
    result = _validate_qc(clipping, metadata_summary)
    assert result["all_qc_pass"] is False
    assert result["failure_counts"]["clipping"] == 1

    metric_lie = _qc(metadata_summary)
    metric_lie["rows"][0]["noise_value"] = 0.9
    with pytest.raises(contract.D08ContractError, match="noise pass flag"):
        _validate_qc(metric_lie, metadata_summary)

    asr_final = _qc(metadata_summary)
    asr_final["rows"][0]["asr_used_as_final_qc"] = True
    with pytest.raises(contract.D08ContractError, match="ASR final"):
        _validate_qc(asr_final, metadata_summary)


def test_completion_is_aggregate_only_and_recomputes_counts() -> None:
    metadata_summary = _validate_metadata()
    qc_summary = _validate_qc(_qc(metadata_summary), metadata_summary)
    payload = _completion(metadata_summary)
    result = contract.validate_completion_payload(
        payload,
        config_sha256=CONFIG_SHA,
        h00_sha256=H00_SHA,
        consent_sha256=CONSENT_SHA,
        rights_sha256=RIGHTS_SHA,
        preregistration_sha256=PREREG_SHA,
        metadata_manifest_sha256=METADATA_SHA,
        qc_report_sha256=QC_SHA,
        metadata_summary=metadata_summary,
        qc_summary=qc_summary,
    )

    assert result["status"] == "valid_completed_aggregate"
    assert result["accepted_take_count"] == 60
    assert result["planning_target_met_all_sentences"] is False

    leaked = _completion(metadata_summary)
    leaked["speaker_id_sha256"] = SPEAKER_ID
    with pytest.raises(contract.D08ContractError, match="forbidden A1"):
        contract.validate_completion_payload(
            leaked,
            config_sha256=CONFIG_SHA,
            h00_sha256=H00_SHA,
            consent_sha256=CONSENT_SHA,
            rights_sha256=RIGHTS_SHA,
            preregistration_sha256=PREREG_SHA,
            metadata_manifest_sha256=METADATA_SHA,
            qc_report_sha256=QC_SHA,
            metadata_summary=metadata_summary,
            qc_summary=qc_summary,
        )


def test_no_overwrite_and_symlink_guards_are_read_only(tmp_path: Path) -> None:
    outputs = {
        "metadata_manifest_path": "future/metadata.json",
        "qc_report_path": "future/qc.json",
        "completion_path": "future/completion.json",
        "completion_commit_path": "future/commit.json",
        "failure_report_path": "future/failure.json",
    }
    contract.ensure_output_targets_absent(workspace_root=tmp_path, outputs=outputs)
    assert not (tmp_path / "future").exists()

    (tmp_path / "future").mkdir()
    (tmp_path / "future/metadata.json").write_text("existing", encoding="utf-8")
    with pytest.raises(contract.D08ContractError, match="no-overwrite"):
        contract.ensure_output_targets_absent(workspace_root=tmp_path, outputs=outputs)

    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(contract.D08ContractError, match="symlink"):
        contract._read_file(tmp_path, "link.json", "synthetic source")
