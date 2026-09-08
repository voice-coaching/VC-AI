from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


WORKSPACE = Path(__file__).resolve().parents[2]
SCRIPT = WORKSPACE / "scripts" / "validate_r00_sentence_card.py"
CARD = WORKSPACE / "data/reference/r00/r00_sentence_card_v1.json"
CHALLENGE = (
    WORKSPACE / "data/processed/aihub71490/manifests/aihub71490_prompt_challenge.json"
)
SPEC = importlib.util.spec_from_file_location(
    "validate_r00_sentence_card",
    SCRIPT,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _card() -> dict:
    return json.loads(CARD.read_text(encoding="utf-8"))


def _reserved_hashes() -> set[str]:
    challenge = json.loads(CHALLENGE.read_text(encoding="utf-8"))
    return {item["prompt_sha256"] for item in challenge["reserved_prompts"]}


def test_canonical_bundle_validates_deterministically_with_zero_protected_reads() -> (
    None
):
    first = MODULE.validate_bundle(workspace_root=WORKSPACE)
    second = MODULE.validate_bundle(workspace_root=WORKSPACE)

    assert first == second
    assert first["status"] == "valid"
    assert first["completion"] == {
        "r00_completion_claim_allowed": True,
        "r01_completion_claim_allowed": False,
        "tts_or_audio_created": False,
        "human_evaluation_completed": False,
    }
    assert first["data_access"]["native_plaintext_files_read"] == 0
    assert first["data_access"]["row_level_files_read"] == 0
    assert first["data_access"]["raw_audio_files_read"] == 0
    assert first["data_access"]["locator_files_read"] == 0
    assert first["data_access"]["environment_files_read"] == 0
    assert first["findings"] == {"critical": 0, "high": 0, "medium": 0}


def test_canonical_card_has_exact_coverage_and_no_reserved_prompt_collision() -> None:
    report = MODULE.validate_bundle(workspace_root=WORKSPACE)
    checks = report["checks"]

    assert checks["sentence_count"] == 20
    assert checks["unique_normalized_hash_count"] == 20
    assert checks["aihub_reserved_prompt_hash_overlap_count"] == 0
    assert checks["difficulty_counts"] == {
        "advanced": 6,
        "beginner": 7,
        "intermediate": 7,
    }
    assert checks["covered_instructional_target_labels"] == list(
        MODULE.INSTRUCTIONAL_TARGETS
    )
    assert checks["covered_onset_consonants"] == list(MODULE.ONSET_CONSONANTS)
    assert checks["covered_observed_korean_vowels"] == list(
        MODULE.OBSERVED_KOREAN_VOWELS
    )
    assert checks["covered_surface_coda_classes"] == list(MODULE.SURFACE_CODA_CLASSES)
    assert checks["covered_supplemental_medials"] == list(MODULE.SUPPLEMENTAL_MEDIALS)
    assert checks["covered_phonological_rules"] == list(MODULE.PHONOLOGICAL_RULES)


def test_source_is_original_cc0_and_native_sentence_lineage_is_excluded() -> None:
    card = _card()

    assert card["source_license"]["license_spdx"] == "CC0-1.0"
    assert card["source_license"]["noncommercial_only"] is False
    assert card["source_license"]["redistribution_allowed"] is True
    assert card["source_license"]["corpus_text_derived"] is False
    assert {item["source_id"] for item in card["native_source_lineage_decisions"]} == {
        "fleurs-ko-kr",
        "kss",
        "seoul-corpus",
        "zeroth-korean",
    }
    assert all(
        item["sentence_text_accessed"] is False and item["sentence_selected"] is False
        for item in card["native_source_lineage_decisions"]
    )


def test_hash_tampering_fails_closed() -> None:
    card = _card()
    card["sentences"][0]["normalized_text_sha256"] = "0" * 64

    with pytest.raises(MODULE.R00ValidationError, match="SHA-256 mismatch"):
        MODULE.validate_card_data(card, reserved_hashes=_reserved_hashes())


def test_duplicate_sentence_fails_closed_after_recomputed_metadata() -> None:
    card = _card()
    source = card["sentences"][18]
    target = card["sentences"][19]
    for key in (
        "text",
        "normalized_text",
        "normalized_text_sha256",
        "syllable_count",
        "word_count",
        "target_phones",
        "target_onsets",
        "target_vowels",
        "target_surface_codas",
        "supplemental_medials",
        "prosody_targets",
    ):
        target[key] = deepcopy(source[key])

    with pytest.raises(MODULE.R00ValidationError, match="must be unique"):
        MODULE.validate_card_data(card, reserved_hashes=_reserved_hashes())


def test_reserved_prompt_hash_collision_fails_closed() -> None:
    card = _card()
    collision = card["sentences"][0]["normalized_text_sha256"]

    with pytest.raises(MODULE.R00ValidationError, match="collides"):
        MODULE.validate_card_data(card, reserved_hashes={collision})


def test_pii_like_ascii_and_digits_fail_before_declared_pass_can_hide_them() -> None:
    card = _card()
    sentence = card["sentences"][0]
    sentence["text"] = "연락처 test 일번을 말해요."
    sentence["normalized_text"] = MODULE.normalize_text(sentence["text"])
    sentence["normalized_text_sha256"] = MODULE.sha256_text(sentence["normalized_text"])

    with pytest.raises(MODULE.R00ValidationError, match="text safety findings"):
        MODULE.validate_card_data(card, reserved_hashes=_reserved_hashes())


def test_noncommercial_or_corpus_derived_license_drift_fails_closed() -> None:
    card = _card()
    card["source_license"]["noncommercial_only"] = True
    card["source_license"]["corpus_text_derived"] = True

    with pytest.raises(MODULE.R00ValidationError, match="CC0 source license"):
        MODULE.validate_card_data(card, reserved_hashes=_reserved_hashes())


def test_native_sentence_access_claim_fails_closed() -> None:
    card = _card()
    card["native_source_lineage_decisions"][0]["sentence_text_accessed"] = True

    with pytest.raises(MODULE.R00ValidationError, match="access prohibited"):
        MODULE.validate_card_data(card, reserved_hashes=_reserved_hashes())


def test_rule_trigger_and_expected_realization_are_fixed() -> None:
    card = _card()
    rule = card["sentences"][8]["phonological_rule_targets"][0]
    rule["expected_realization_hangul"] = "국물"

    with pytest.raises(MODULE.R00ValidationError, match="rule example drift"):
        MODULE.validate_card_data(card, reserved_hashes=_reserved_hashes())


def test_report_writer_is_idempotent_and_refuses_different_overwrite(
    tmp_path: Path,
) -> None:
    relative = "artifacts/reference/r00/validation.json"
    first = MODULE.write_immutable_report(
        workspace_root=tmp_path,
        output_relative_path=relative,
        report={"status": "valid"},
    )
    second = MODULE.write_immutable_report(
        workspace_root=tmp_path,
        output_relative_path=relative,
        report={"status": "valid"},
    )

    assert first == second
    with pytest.raises(MODULE.R00ValidationError, match="overwrite"):
        MODULE.write_immutable_report(
            workspace_root=tmp_path,
            output_relative_path=relative,
            report={"status": "invalid"},
        )


def test_cli_validate_only_writes_no_report(tmp_path: Path) -> None:
    report = tmp_path / "must-not-exist.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--workspace-root",
            str(WORKSPACE),
            "--report",
            str(report),
            "--validate-only",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "valid"
    assert not report.exists()


def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"same": 1, "same": 2}', encoding="utf-8")

    with pytest.raises(MODULE.R00ValidationError, match="duplicate JSON key"):
        MODULE.load_json(invalid)
