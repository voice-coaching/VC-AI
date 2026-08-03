#!/usr/bin/env python3
"""Validate the R00 fixed Korean sentence card without protected-data access.

The validator reads only the public sentence card, its schema, pinned public or
aggregate evidence, and the privacy-safe prompt-reservation artifact.  It never
opens native-corpus transcripts, audio, locators, AI Hub row-level manifests,
environment files, model files, or human-rating files.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import unicodedata
from typing import Any


SCHEMA_VERSION = "voice_coach.r00_sentence_card.v1"
VALIDATION_SCHEMA_VERSION = "voice_coach.r00_sentence_card_validation.v1"
DEFAULT_CARD = "data/reference/r00/r00_sentence_card_v1.json"
DEFAULT_SCHEMA = "configs/reference/r00_sentence_card_v1.schema.json"
DEFAULT_REPORT = "artifacts/reference/r00/r00_sentence_card_v1.validation.json"
LICENSE_NOTICE = "data/reference/r00/CC0_NOTICE.txt"
LICENSE_NOTICE_SHA256 = (
    "ffd86dc89cf0957dccc61f6ecb7af1c7ebbbdbff2bd87c951e54c40afc1262c9"
)

ONSET_CONSONANTS = tuple("ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ")
OBSERVED_KOREAN_VOWELS = (
    "ㅏ",
    "ㅐ",
    "ㅑ",
    "ㅓ",
    "ㅔ",
    "ㅕ",
    "ㅖ",
    "ㅗ",
    "ㅘ",
    "ㅚ",
    "ㅛ",
    "ㅜ",
    "ㅝ",
    "ㅟ",
    "ㅠ",
    "ㅡ",
    "ㅢ",
    "ㅣ",
)
SUPPLEMENTAL_MEDIALS = ("ㅒ", "ㅙ", "ㅞ")
SURFACE_CODA_CLASSES = tuple("ㄱㄴㄷㄹㅁㅂㅇ")
INSTRUCTIONAL_TARGETS = ONSET_CONSONANTS + OBSERVED_KOREAN_VOWELS
OBSERVED_TARGETS = ("spn",) + INSTRUCTIONAL_TARGETS
PHONOLOGICAL_RULES = (
    "tensification",
    "nasalization",
    "aspiration",
    "liquidization",
    "liaison_rule",
    "n_insertion",
    "palatalization",
    "phoneme_deletion",
)
EXPECTED_RULE_EXAMPLES = {
    "nasalization": ("국물", "궁물"),
    "liquidization": ("신라", "실라"),
    "aspiration": ("좋다", "조타"),
    "palatalization": ("같이", "가치"),
    "liaison_rule": ("옷이", "오시"),
    "tensification": ("국밥", "국빱"),
    "n_insertion": ("담요", "담뇨"),
    "phoneme_deletion": ("놓아", "노아"),
}
ALLOWED_PROSODY_TARGETS = (
    "contrastive_focus",
    "instructional_request_boundary",
    "list_rhythm",
    "long_phrase_chunking",
    "narrow_focus",
    "neutral_declarative_fall",
    "phrase_boundary_chunking",
    "rhythmic_repetition",
    "slow_clear_pacing",
    "wh_question_boundary",
    "yes_no_question_boundary",
)
DIFFICULTY_QUOTA = {
    "beginner": 7,
    "intermediate": 7,
    "advanced": 6,
}

CHOSEONG = ONSET_CONSONANTS
JUNGSEONG = (
    "ㅏ",
    "ㅐ",
    "ㅑ",
    "ㅒ",
    "ㅓ",
    "ㅔ",
    "ㅕ",
    "ㅖ",
    "ㅗ",
    "ㅘ",
    "ㅙ",
    "ㅚ",
    "ㅛ",
    "ㅜ",
    "ㅝ",
    "ㅞ",
    "ㅟ",
    "ㅠ",
    "ㅡ",
    "ㅢ",
    "ㅣ",
)
JONGSEONG = (
    "",
    "ㄱ",
    "ㄲ",
    "ㄳ",
    "ㄴ",
    "ㄵ",
    "ㄶ",
    "ㄷ",
    "ㄹ",
    "ㄺ",
    "ㄻ",
    "ㄼ",
    "ㄽ",
    "ㄾ",
    "ㄿ",
    "ㅀ",
    "ㅁ",
    "ㅂ",
    "ㅄ",
    "ㅅ",
    "ㅆ",
    "ㅇ",
    "ㅈ",
    "ㅊ",
    "ㅋ",
    "ㅌ",
    "ㅍ",
    "ㅎ",
)
CODA_TO_SURFACE = {
    "ㄱ": "ㄱ",
    "ㄲ": "ㄱ",
    "ㄳ": "ㄱ",
    "ㅋ": "ㄱ",
    "ㄺ": "ㄱ",
    "ㄴ": "ㄴ",
    "ㄵ": "ㄴ",
    "ㄶ": "ㄴ",
    "ㄷ": "ㄷ",
    "ㅅ": "ㄷ",
    "ㅆ": "ㄷ",
    "ㅈ": "ㄷ",
    "ㅊ": "ㄷ",
    "ㅌ": "ㄷ",
    "ㅎ": "ㄷ",
    "ㄹ": "ㄹ",
    "ㄼ": "ㄹ",
    "ㄽ": "ㄹ",
    "ㄾ": "ㄹ",
    "ㅀ": "ㄹ",
    "ㅁ": "ㅁ",
    "ㄻ": "ㅁ",
    "ㅂ": "ㅂ",
    "ㅍ": "ㅂ",
    "ㄿ": "ㅂ",
    "ㅄ": "ㅂ",
    "ㅇ": "ㅇ",
}

EXPECTED_EVIDENCE_PINS = {
    "data/processed/native-korean/manifests/native_korean_v1.report.json": (
        "d50e6f788f6e631c37b54631325dfe840c0460ecf342af71f5e38b6f638037b4"
    ),
    "data/manifests/fleurs-ko-kr.yaml": (
        "34759caacfd56e2feb08d56f70f31fb8c89cbcf90c54f86812d0399c0e0ccb84"
    ),
    "data/manifests/kss.yaml": (
        "409d4de4777900400adadf59a72372d202049f26f54fae0d8e5a84c9a0a9bd1a"
    ),
    "data/manifests/seoul-corpus.yaml": (
        "d099f0c1cba51055152d47ebff4314eef4dfbda1093f48732f6e7f40bcdf3d7e"
    ),
    "data/manifests/zeroth-korean.yaml": (
        "bc8a004272029f5c116c0efd345255f9ddb3295f4a36b8e0bf9bf049c801be51"
    ),
    "data/processed/aihub71490/manifests/aihub71490_data_card.json": (
        "4ab53c6837b1a28fbf153dd3a351a195f6931a43694467114cea6c21b8a57b13"
    ),
    "data/processed/aihub71490/manifests/aihub71490_prompt_challenge.json": (
        "3ea7f12ea76be07a0796d7e62f0c3727ca9858c0278df7c85ac1e08d9b653fa9"
    ),
    "configs/coaching/korean_error_advice_ontology_v1.json": (
        "1ba0c0198533abd51a3caa4b176f17aae17aa48d08899f6e7ced634a1eef0d60"
    ),
    "artifacts/experiments/aihub71490_wavlm_large_frozen_head_v1/report.json": (
        "ba869265c6e6c1381677fe922922b68da7df92cbb9cbaf5501fff5dafa3fdd23"
    ),
    "src/voice_coach/phonology/phones.py": (
        "ad87e6c8edb961dc3b031342f433143d7d1412ac07812a5b7d5fd7741e13ac3b"
    ),
}

EXPECTED_TOP_LEVEL_KEYS = {
    "schema_version",
    "card_id",
    "status",
    "created_date_kst",
    "source_license",
    "native_source_lineage_decisions",
    "evidence_pins",
    "selection_rule",
    "phone_inventory",
    "phonological_rule_inventory",
    "tts_usage_policy",
    "sentences",
    "aggregate_checks",
    "completion_boundary",
}
EXPECTED_SENTENCE_KEYS = {
    "sentence_id",
    "selection_order",
    "text",
    "normalized_text",
    "normalized_text_sha256",
    "source_id",
    "license_spdx",
    "syllable_count",
    "word_count",
    "difficulty",
    "difficulty_reason",
    "target_phones",
    "target_onsets",
    "target_vowels",
    "target_surface_codas",
    "supplemental_medials",
    "phonological_rule_targets",
    "prosody_targets",
    "duplicate_checks",
    "pii_check",
    "prohibited_content_check",
    "tts_usage_policy_id",
}
EXPECTED_SOURCE_LICENSE = {
    "source_type": "project_original_controlled_sentences",
    "source_id": "voice-coach-r00-original-v1",
    "source_uri": "r00://project-original/voice-coach-r00-original-v1",
    "license_spdx": "CC0-1.0",
    "license_name": "CC0 1.0 Universal",
    "license_url": ("https://creativecommons.org/publicdomain/zero/1.0/legalcode"),
    "license_notice_path": LICENSE_NOTICE,
    "license_applies_to": "the_exact_20_sentence_texts_in_this_card",
    "attribution_required": False,
    "noncommercial_only": False,
    "share_alike_required": False,
    "redistribution_allowed": True,
    "adaptation_allowed": True,
    "corpus_text_derived": False,
}
EXPECTED_NATIVE_DECISIONS = {
    "fleurs-ko-kr": {
        "license_spdx": "CC-BY-4.0",
        "noncommercial_only": False,
        "decision": (
            "excluded_by_project_policy_prohibiting_native_corpus_transcript_import"
        ),
    },
    "kss": {
        "license_spdx": "CC-BY-NC-SA-4.0",
        "noncommercial_only": True,
        "decision": (
            "excluded_noncommercial_sharealike_and_raw_redistribution_boundary"
        ),
    },
    "seoul-corpus": {
        "license_spdx": "CC-BY-NC-2.0",
        "noncommercial_only": True,
        "decision": "excluded_noncommercial_and_raw_redistribution_boundary",
    },
    "zeroth-korean": {
        "license_spdx": "CC-BY-4.0",
        "noncommercial_only": False,
        "decision": (
            "excluded_by_project_policy_prohibiting_native_corpus_transcript_import"
        ),
    },
}
EXPECTED_TTS_POLICY = {
    "policy_id": "r00-research-reference-text-only-v1",
    "license_noncommercial_only": False,
    "allowed": [
        "r01_isolated_research_tts_candidate_text_input",
        "d08_consented_human_reference_recording_prompt",
        "r02_asr_content_match_reference_text",
        "r04_blinded_evaluation_sentence_label",
    ],
    "prohibited_until_later_gates": [
        "tts_model_training_or_finetuning",
        "production_reference_bank_release",
        "automatic_pronunciation_correctness_claim",
        "human_reference_claim_without_d08_recording",
        "blind_quality_claim_without_r04_evaluation",
        "native_corpus_audio_or_transcript_attachment",
    ],
    "audio_or_model_created_by_r00": False,
}

ASCII_LETTER_PATTERN = re.compile(r"[A-Za-z]")
DIGIT_PATTERN = re.compile(r"\d")
URL_PATTERN = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
ALLOWED_TEXT_PATTERN = re.compile(r"^[가-힣 ]+[.?]$")
FORBIDDEN_TERMS = (
    "주민번호",
    "전화번호",
    "이메일",
    "주소",
    "국적",
    "성별",
    "종교",
    "장애",
    "질병",
    "정당",
    "혐오",
    "욕설",
    "자살",
    "마약",
)


class R00ValidationError(ValueError):
    """Raised when any R00 contract check fails closed."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise R00ValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except json.JSONDecodeError as exc:
        raise R00ValidationError(f"invalid JSON at {path}: {exc}") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise R00ValidationError(f"{name} must be an object")
    return value


def _require_list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise R00ValidationError(f"{name} must be an array")
    return value


def _assert_exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    name: str,
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise R00ValidationError(
            f"{name} keys mismatch; missing={missing}, extra={extra}"
        )


def _safe_input_path(workspace_root: Path, relative_path: str) -> Path:
    pure = PurePosixPath(relative_path)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in ("", ".", "..") for part in pure.parts)
        or "\\" in relative_path
    ):
        raise R00ValidationError(f"unsafe relative input path: {relative_path}")
    root = workspace_root.resolve()
    candidate = root.joinpath(*pure.parts)
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise R00ValidationError(
                f"symlinked input path is prohibited: {relative_path}"
            )
    resolved = candidate.resolve(strict=True)
    if root not in resolved.parents:
        raise R00ValidationError(f"input escapes workspace root: {relative_path}")
    if not resolved.is_file():
        raise R00ValidationError(f"input is not a file: {relative_path}")
    return resolved


def decompose_sentence(text: str) -> dict[str, Any]:
    onsets: set[str] = set()
    vowels: set[str] = set()
    supplemental: set[str] = set()
    codas: set[str] = set()
    syllable_count = 0
    for character in text:
        offset = ord(character) - 0xAC00
        if not 0 <= offset < 11_172:
            continue
        syllable_count += 1
        onset = CHOSEONG[offset // 588]
        medial = JUNGSEONG[(offset % 588) // 28]
        coda = JONGSEONG[offset % 28]
        onsets.add(onset)
        if medial in OBSERVED_KOREAN_VOWELS:
            vowels.add(medial)
        elif medial in SUPPLEMENTAL_MEDIALS:
            supplemental.add(medial)
        else:
            raise R00ValidationError(f"unsupported Hangul medial: {medial}")
        if coda:
            surface = CODA_TO_SURFACE.get(coda)
            if surface is None:
                raise R00ValidationError(f"unsupported Hangul coda: {coda}")
            codas.add(surface)
    ordered_onsets = [item for item in ONSET_CONSONANTS if item in onsets]
    ordered_vowels = [item for item in OBSERVED_KOREAN_VOWELS if item in vowels]
    ordered_codas = [item for item in SURFACE_CODA_CLASSES if item in codas]
    ordered_supplemental = [
        item for item in SUPPLEMENTAL_MEDIALS if item in supplemental
    ]
    target_set = onsets | vowels | codas
    ordered_targets = [item for item in INSTRUCTIONAL_TARGETS if item in target_set]
    return {
        "syllable_count": syllable_count,
        "target_phones": ordered_targets,
        "target_onsets": ordered_onsets,
        "target_vowels": ordered_vowels,
        "target_surface_codas": ordered_codas,
        "supplemental_medials": ordered_supplemental,
    }


def scan_text(text: str) -> list[str]:
    matches: list[str] = []
    if ASCII_LETTER_PATTERN.search(text):
        matches.append("ascii_letter")
    if DIGIT_PATTERN.search(text):
        matches.append("decimal_digit")
    if URL_PATTERN.search(text):
        matches.append("url")
    if EMAIL_PATTERN.search(text):
        matches.append("email")
    if CONTROL_PATTERN.search(text):
        matches.append("control_character")
    if not ALLOWED_TEXT_PATTERN.fullmatch(text):
        matches.append("disallowed_character_or_sentence_boundary")
    if any(term in text for term in FORBIDDEN_TERMS):
        matches.append("forbidden_sensitive_or_unsafe_term")
    return sorted(set(matches))


def _validate_schema_document(schema: Any) -> None:
    value = _require_mapping(schema, "schema")
    if value.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise R00ValidationError("schema draft must be JSON Schema 2020-12")
    if value.get("$id") != (
        "https://voice-coach.local/schemas/r00_sentence_card_v1.schema.json"
    ):
        raise R00ValidationError("schema $id drift")
    required = value.get("required")
    if not isinstance(required, list) or set(required) != EXPECTED_TOP_LEVEL_KEYS:
        raise R00ValidationError("schema required top-level keys drift")
    definitions = value.get("$defs")
    if not isinstance(definitions, Mapping) or "sentence" not in definitions:
        raise R00ValidationError("schema sentence definition is missing")


def _validate_evidence(
    workspace_root: Path,
    card: Mapping[str, Any],
) -> tuple[dict[str, str], set[str]]:
    pins = _require_list(card.get("evidence_pins"), "evidence_pins")
    declared: dict[str, str] = {}
    for index, raw_pin in enumerate(pins):
        pin = _require_mapping(raw_pin, f"evidence_pins[{index}]")
        if set(pin) != {"path", "sha256", "access_class", "values_read"}:
            raise R00ValidationError(f"evidence_pins[{index}] keys drift")
        path = pin.get("path")
        digest = pin.get("sha256")
        if not isinstance(path, str) or not isinstance(digest, str):
            raise R00ValidationError(f"evidence_pins[{index}] path/hash invalid")
        if path in declared:
            raise R00ValidationError(f"duplicate evidence pin path: {path}")
        declared[path] = digest
    if declared != EXPECTED_EVIDENCE_PINS:
        raise R00ValidationError("evidence pin path or expected SHA-256 drift")

    actual: dict[str, str] = {}
    for path, expected_sha256 in EXPECTED_EVIDENCE_PINS.items():
        resolved = _safe_input_path(workspace_root, path)
        observed = sha256_file(resolved)
        if observed != expected_sha256:
            raise R00ValidationError(
                f"evidence SHA-256 mismatch: {path}; "
                f"expected={expected_sha256}, observed={observed}"
            )
        actual[path] = observed

    challenge_path = _safe_input_path(
        workspace_root,
        "data/processed/aihub71490/manifests/aihub71490_prompt_challenge.json",
    )
    challenge = _require_mapping(load_json(challenge_path), "prompt challenge")
    if challenge.get("schema_version") != (
        "voice_coach.prompt_challenge_reservation.v1"
    ):
        raise R00ValidationError("prompt challenge schema drift")
    source = _require_mapping(challenge.get("source"), "prompt challenge source")
    if source.get("non_projected_value_columns_read") is not False:
        raise R00ValidationError(
            "prompt challenge must prove non-projected value reads are false"
        )
    selection = _require_mapping(
        challenge.get("selection"),
        "prompt challenge selection",
    )
    if (
        selection.get("selected_prompt_count") != 20
        or selection.get("requested_prompt_count") != 20
    ):
        raise R00ValidationError("prompt challenge must contain 20 hashes")
    reserved = _require_list(
        challenge.get("reserved_prompts"),
        "reserved_prompts",
    )
    hashes: set[str] = set()
    for index, raw_item in enumerate(reserved):
        item = _require_mapping(raw_item, f"reserved_prompts[{index}]")
        value = item.get("prompt_sha256")
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise R00ValidationError(
                f"reserved_prompts[{index}] has invalid prompt SHA-256"
            )
        hashes.add(value)
    if len(hashes) != 20:
        raise R00ValidationError("prompt challenge hashes must be unique")

    data_card_path = _safe_input_path(
        workspace_root,
        "data/processed/aihub71490/manifests/aihub71490_data_card.json",
    )
    data_card = _require_mapping(load_json(data_card_path), "AI Hub data card")
    if data_card.get("target_phone_label_count") != 38:
        raise R00ValidationError("aggregate target-phone count must remain 38")

    ontology_path = _safe_input_path(
        workspace_root,
        "configs/coaching/korean_error_advice_ontology_v1.json",
    )
    ontology = _require_mapping(load_json(ontology_path), "C01 ontology")
    tags = _require_list(ontology.get("tags"), "C01 tags")
    ontology_ids = {
        item.get("id")
        for item in tags
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    missing_rules = set(PHONOLOGICAL_RULES) - ontology_ids
    if missing_rules:
        raise R00ValidationError(
            f"C01 ontology is missing R00 rule IDs: {sorted(missing_rules)}"
        )

    frozen_report_path = _safe_input_path(
        workspace_root,
        "artifacts/experiments/aihub71490_wavlm_large_frozen_head_v1/report.json",
    )
    frozen_report = load_json(frozen_report_path)
    target_label_sets: list[tuple[str, ...]] = []

    def collect_target_label_sets(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if key == "target_label" and isinstance(nested, Mapping):
                    groups = nested.get("groups")
                    if isinstance(groups, list):
                        label_values: list[str] = []
                        for group in groups:
                            if not isinstance(group, Mapping):
                                continue
                            label = group.get("value")
                            if isinstance(label, str):
                                label_values.append(label)
                        labels = tuple(label_values)
                        if labels:
                            target_label_sets.append(labels)
                collect_target_label_sets(nested)
        elif isinstance(value, list):
            for nested in value:
                collect_target_label_sets(nested)

    collect_target_label_sets(frozen_report)
    if not target_label_sets:
        raise R00ValidationError("frozen aggregate report has no target_label groups")
    if any(labels != OBSERVED_TARGETS for labels in target_label_sets):
        raise R00ValidationError("frozen aggregate target-label inventory drift")
    return actual, hashes


def _validate_source_and_license(card: Mapping[str, Any]) -> None:
    source_license = _require_mapping(
        card.get("source_license"),
        "source_license",
    )
    if dict(source_license) != EXPECTED_SOURCE_LICENSE:
        raise R00ValidationError("project-original CC0 source license drift")

    raw_decisions = _require_list(
        card.get("native_source_lineage_decisions"),
        "native_source_lineage_decisions",
    )
    if len(raw_decisions) != 4:
        raise R00ValidationError("exactly four native lineage decisions required")
    seen: set[str] = set()
    for index, raw_decision in enumerate(raw_decisions):
        decision = _require_mapping(
            raw_decision,
            f"native_source_lineage_decisions[{index}]",
        )
        if set(decision) != {
            "source_id",
            "license_spdx",
            "noncommercial_only",
            "sentence_text_accessed",
            "sentence_selected",
            "decision",
        }:
            raise R00ValidationError("native lineage decision keys drift")
        source_id = decision.get("source_id")
        if not isinstance(source_id, str) or source_id in seen:
            raise R00ValidationError("native source IDs must be unique strings")
        seen.add(source_id)
        expected = EXPECTED_NATIVE_DECISIONS.get(source_id)
        if expected is None:
            raise R00ValidationError(f"unexpected native source: {source_id}")
        if decision.get("sentence_text_accessed") is not False:
            raise R00ValidationError(f"{source_id} sentence text access prohibited")
        if decision.get("sentence_selected") is not False:
            raise R00ValidationError(f"{source_id} sentence selection prohibited")
        for key, expected_value in expected.items():
            if decision.get(key) != expected_value:
                raise R00ValidationError(f"{source_id} lineage field drift: {key}")
    if seen != set(EXPECTED_NATIVE_DECISIONS):
        raise R00ValidationError("native source lineage coverage drift")


def _validate_global_contract(card: Mapping[str, Any]) -> None:
    _assert_exact_keys(card, EXPECTED_TOP_LEVEL_KEYS, "card")
    if card.get("schema_version") != SCHEMA_VERSION:
        raise R00ValidationError("card schema_version drift")
    if card.get("card_id") != "voice-coach-r00-fixed-korean-sentences-v1":
        raise R00ValidationError("card_id drift")
    if card.get("status") != "fixed_validated_sentence_text_only":
        raise R00ValidationError("card status drift")
    if card.get("created_date_kst") != "2026-07-29":
        raise R00ValidationError("created_date_kst drift")

    selection = _require_mapping(card.get("selection_rule"), "selection_rule")
    expected_selection = {
        "rule_id": "r00-educational-stratified-selection-v1",
        "frozen_before_r01": True,
        "source_gate": "project_original_cc0_only",
        "normalization": "NFKC_then_collapse_unicode_whitespace",
        "sentence_count": 20,
        "syllable_count_range": {"minimum": 10, "maximum": 18},
        "word_count_range": {"minimum": 4, "maximum": 8},
        "difficulty_quota": DIFFICULTY_QUOTA,
        "coverage_requirements": {
            "all_19_onset_consonants": True,
            "all_18_observed_korean_vowels": True,
            "all_7_surface_coda_classes": True,
            "all_8_localizable_phonological_rules": True,
            "prosody_target_count_minimum": 4,
            "exclude_spn_from_instruction": True,
            "supplemental_medials": list(SUPPLEMENTAL_MEDIALS),
        },
        "duplicate_rule": {
            "within_card": "normalized_text_sha256_must_be_unique",
            "aihub_reserved_prompt_hashes": ("exact_hash_intersection_must_be_zero"),
            "native_plaintext": "must_not_be_opened_for_overlap_search",
        },
        "privacy_and_content_rule": {
            "pii_matches_must_be_zero": True,
            "forbidden_terms_must_be_zero": True,
            "ascii_letters_must_be_zero": True,
            "decimal_digits_must_be_zero": True,
            "urls_must_be_zero": True,
        },
        "ordering_rule": "fixed_sentence_id_ascending_after_stratum_matrix",
    }
    if dict(selection) != expected_selection:
        raise R00ValidationError("selection rule drift")

    inventory = _require_mapping(card.get("phone_inventory"), "phone_inventory")
    expected_inventory = {
        "observed_target_label_count": 38,
        "observed_target_labels": list(OBSERVED_TARGETS),
        "instructional_target_labels": list(INSTRUCTIONAL_TARGETS),
        "excluded_non_instructional_labels": ["spn"],
        "onset_consonants": list(ONSET_CONSONANTS),
        "observed_korean_vowels": list(OBSERVED_KOREAN_VOWELS),
        "surface_coda_classes": list(SURFACE_CODA_CLASSES),
        "supplemental_orthographic_medials": list(SUPPLEMENTAL_MEDIALS),
    }
    for key, expected in expected_inventory.items():
        if inventory.get(key) != expected:
            raise R00ValidationError(f"phone inventory drift: {key}")
    interpretation = inventory.get("interpretation")
    if not isinstance(interpretation, str) or not interpretation:
        raise R00ValidationError("phone inventory interpretation missing")

    if card.get("phonological_rule_inventory") != list(PHONOLOGICAL_RULES):
        raise R00ValidationError("phonological rule inventory drift")
    if card.get("tts_usage_policy") != EXPECTED_TTS_POLICY:
        raise R00ValidationError("TTS usage policy drift")
    completion = card.get("completion_boundary")
    if completion != {
        "r00_completion_claim_allowed_after_validator_pass": True,
        "r01_completed": False,
        "tts_generated": False,
        "human_reference_recorded": False,
        "human_evaluation_completed": False,
        "model_trained": False,
    }:
        raise R00ValidationError("completion boundary drift")


def _validate_sentence(
    raw_sentence: Any,
    *,
    index: int,
    reserved_hashes: set[str],
) -> dict[str, Any]:
    sentence = _require_mapping(raw_sentence, f"sentences[{index - 1}]")
    _assert_exact_keys(
        sentence,
        EXPECTED_SENTENCE_KEYS,
        f"sentences[{index - 1}]",
    )
    expected_id = f"R00-{index:02d}"
    if sentence.get("sentence_id") != expected_id:
        raise R00ValidationError(f"sentence ID/order mismatch at position {index}")
    if sentence.get("selection_order") != index:
        raise R00ValidationError(f"{expected_id} selection_order mismatch")
    text = sentence.get("text")
    if not isinstance(text, str):
        raise R00ValidationError(f"{expected_id} text must be a string")
    normalized = normalize_text(text)
    if sentence.get("normalized_text") != normalized:
        raise R00ValidationError(f"{expected_id} normalized text mismatch")
    digest = sha256_text(normalized)
    if sentence.get("normalized_text_sha256") != digest:
        raise R00ValidationError(f"{expected_id} normalized text SHA-256 mismatch")
    if digest in reserved_hashes:
        raise R00ValidationError(
            f"{expected_id} collides with a reserved AI Hub prompt hash"
        )
    if sentence.get("source_id") != "voice-coach-r00-original-v1":
        raise R00ValidationError(f"{expected_id} source lineage drift")
    if sentence.get("license_spdx") != "CC0-1.0":
        raise R00ValidationError(f"{expected_id} license drift")
    if sentence.get("tts_usage_policy_id") != ("r00-research-reference-text-only-v1"):
        raise R00ValidationError(f"{expected_id} TTS policy drift")

    text_findings = scan_text(normalized)
    if text_findings:
        raise R00ValidationError(f"{expected_id} text safety findings: {text_findings}")
    for key in ("pii_check", "prohibited_content_check"):
        if sentence.get(key) != {"status": "passed", "matched_categories": []}:
            raise R00ValidationError(f"{expected_id} {key} declaration drift")
    if sentence.get("duplicate_checks") != {
        "within_card_unique": True,
        "aihub_reserved_prompt_hash_collision": False,
        "native_plaintext_overlap": "not_checked_prohibited_input",
    }:
        raise R00ValidationError(f"{expected_id} duplicate-check boundary drift")

    decomposition = decompose_sentence(normalized)
    for key, expected in decomposition.items():
        if key == "syllable_count":
            continue
        if sentence.get(key) != expected:
            raise R00ValidationError(f"{expected_id} {key} mismatch")
    if sentence.get("syllable_count") != decomposition["syllable_count"]:
        raise R00ValidationError(f"{expected_id} syllable_count mismatch")
    expected_word_count = len(normalized[:-1].split())
    if sentence.get("word_count") != expected_word_count:
        raise R00ValidationError(f"{expected_id} word_count mismatch")
    if not 10 <= decomposition["syllable_count"] <= 18:
        raise R00ValidationError(f"{expected_id} syllable count outside rule")
    if not 4 <= expected_word_count <= 8:
        raise R00ValidationError(f"{expected_id} word count outside rule")

    difficulty = sentence.get("difficulty")
    if difficulty not in DIFFICULTY_QUOTA:
        raise R00ValidationError(f"{expected_id} invalid difficulty")
    if not isinstance(sentence.get("difficulty_reason"), str) or not sentence.get(
        "difficulty_reason"
    ):
        raise R00ValidationError(f"{expected_id} difficulty reason missing")

    raw_rule_targets = _require_list(
        sentence.get("phonological_rule_targets"),
        f"{expected_id}.phonological_rule_targets",
    )
    rule_ids: list[str] = []
    for target_index, raw_target in enumerate(raw_rule_targets):
        target = _require_mapping(
            raw_target,
            f"{expected_id}.phonological_rule_targets[{target_index}]",
        )
        if set(target) != {
            "taxonomy_stable_id",
            "trigger_text",
            "expected_realization_hangul",
            "annotation_status",
        }:
            raise R00ValidationError(f"{expected_id} rule target keys drift")
        rule_id = target.get("taxonomy_stable_id")
        trigger_text = target.get("trigger_text")
        expected_realization = target.get("expected_realization_hangul")
        if not isinstance(rule_id, str) or rule_id not in PHONOLOGICAL_RULES:
            raise R00ValidationError(f"{expected_id} invalid rule target")
        if not isinstance(trigger_text, str) or not isinstance(
            expected_realization, str
        ):
            raise R00ValidationError(f"{expected_id} rule example must be text")
        expected_example = EXPECTED_RULE_EXAMPLES[rule_id]
        if (trigger_text, expected_realization) != expected_example:
            raise R00ValidationError(f"{expected_id} rule example drift")
        if trigger_text not in normalized:
            raise R00ValidationError(f"{expected_id} rule trigger absent from text")
        if target.get("annotation_status") != (
            "engineering_target_pending_human_pronunciation_review"
        ):
            raise R00ValidationError(f"{expected_id} rule evidence status drift")
        rule_ids.append(rule_id)
    if len(rule_ids) != len(set(rule_ids)):
        raise R00ValidationError(f"{expected_id} duplicate rule target")

    prosody = _require_list(
        sentence.get("prosody_targets"),
        f"{expected_id}.prosody_targets",
    )
    if not prosody or len(prosody) != len(set(prosody)):
        raise R00ValidationError(f"{expected_id} prosody targets invalid")
    if any(item not in ALLOWED_PROSODY_TARGETS for item in prosody):
        raise R00ValidationError(f"{expected_id} unknown prosody target")
    question_targets = {"wh_question_boundary", "yes_no_question_boundary"}
    if question_targets.intersection(prosody) and not normalized.endswith("?"):
        raise R00ValidationError(f"{expected_id} question target needs '?'")
    if "neutral_declarative_fall" in prosody and not normalized.endswith("."):
        raise R00ValidationError(
            f"{expected_id} declarative target needs sentence-final '.'"
        )
    return {
        "sentence_id": expected_id,
        "normalized_text_sha256": digest,
        "difficulty": difficulty,
        **decomposition,
        "phonological_rules": rule_ids,
        "prosody_targets": list(prosody),
    }


def validate_card_data(
    card: Mapping[str, Any],
    *,
    reserved_hashes: set[str],
) -> dict[str, Any]:
    """Validate a parsed card against the immutable R00 semantic contract."""

    _validate_global_contract(card)
    _validate_source_and_license(card)
    raw_sentences = _require_list(card.get("sentences"), "sentences")
    if len(raw_sentences) != 20:
        raise R00ValidationError("sentence card must contain exactly 20 items")
    validated = [
        _validate_sentence(
            raw_sentence,
            index=index,
            reserved_hashes=reserved_hashes,
        )
        for index, raw_sentence in enumerate(raw_sentences, start=1)
    ]
    hashes = [item["normalized_text_sha256"] for item in validated]
    if len(set(hashes)) != 20:
        raise R00ValidationError("normalized sentence hashes must be unique")

    difficulty_counts = dict(
        sorted(Counter(item["difficulty"] for item in validated).items())
    )
    if difficulty_counts != dict(sorted(DIFFICULTY_QUOTA.items())):
        raise R00ValidationError("difficulty quota mismatch")

    def union_ordered(field: str, inventory: Sequence[str]) -> list[str]:
        observed = {value for item in validated for value in item[field]}
        return [value for value in inventory if value in observed]

    aggregate: dict[str, Any] = {
        "sentence_count": len(validated),
        "unique_normalized_hash_count": len(set(hashes)),
        "aihub_reserved_prompt_hash_overlap_count": len(
            set(hashes).intersection(reserved_hashes)
        ),
        "native_plaintext_files_read": 0,
        "pii_match_count": 0,
        "forbidden_content_match_count": 0,
        "difficulty_counts": difficulty_counts,
        "covered_instructional_target_labels": union_ordered(
            "target_phones",
            INSTRUCTIONAL_TARGETS,
        ),
        "covered_onset_consonants": union_ordered(
            "target_onsets",
            ONSET_CONSONANTS,
        ),
        "covered_observed_korean_vowels": union_ordered(
            "target_vowels",
            OBSERVED_KOREAN_VOWELS,
        ),
        "covered_surface_coda_classes": union_ordered(
            "target_surface_codas",
            SURFACE_CODA_CLASSES,
        ),
        "covered_supplemental_medials": union_ordered(
            "supplemental_medials",
            SUPPLEMENTAL_MEDIALS,
        ),
        "covered_phonological_rules": union_ordered(
            "phonological_rules",
            PHONOLOGICAL_RULES,
        ),
        "covered_prosody_targets": union_ordered(
            "prosody_targets",
            ALLOWED_PROSODY_TARGETS,
        ),
    }
    expected_coverage = {
        "covered_instructional_target_labels": list(INSTRUCTIONAL_TARGETS),
        "covered_onset_consonants": list(ONSET_CONSONANTS),
        "covered_observed_korean_vowels": list(OBSERVED_KOREAN_VOWELS),
        "covered_surface_coda_classes": list(SURFACE_CODA_CLASSES),
        "covered_supplemental_medials": list(SUPPLEMENTAL_MEDIALS),
        "covered_phonological_rules": list(PHONOLOGICAL_RULES),
    }
    for key, expected in expected_coverage.items():
        if aggregate[key] != expected:
            raise R00ValidationError(f"educational coverage incomplete for {key}")
    if len(aggregate["covered_prosody_targets"]) < 4:
        raise R00ValidationError("prosody target coverage is below four")
    declared_aggregate = _require_mapping(
        card.get("aggregate_checks"),
        "aggregate_checks",
    )
    if dict(declared_aggregate) != aggregate:
        raise R00ValidationError("declared aggregate_checks mismatch recomputation")
    return aggregate


def validate_bundle(
    *,
    workspace_root: Path,
    card_relative_path: str = DEFAULT_CARD,
    schema_relative_path: str = DEFAULT_SCHEMA,
) -> dict[str, Any]:
    card_path = _safe_input_path(workspace_root, card_relative_path)
    schema_path = _safe_input_path(workspace_root, schema_relative_path)
    license_notice_path = _safe_input_path(workspace_root, LICENSE_NOTICE)
    if sha256_file(license_notice_path) != LICENSE_NOTICE_SHA256:
        raise R00ValidationError("CC0 notice SHA-256 drift")
    card = _require_mapping(load_json(card_path), "sentence card")
    schema = load_json(schema_path)
    _validate_schema_document(schema)
    evidence_hashes, reserved_hashes = _validate_evidence(workspace_root, card)
    aggregate = validate_card_data(card, reserved_hashes=reserved_hashes)
    return {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "status": "valid",
        "card_id": card["card_id"],
        "inputs": {
            "card": {
                "path": card_relative_path,
                "sha256": sha256_file(card_path),
            },
            "schema": {
                "path": schema_relative_path,
                "sha256": sha256_file(schema_path),
            },
            "license_notice": {
                "path": LICENSE_NOTICE,
                "sha256": LICENSE_NOTICE_SHA256,
            },
            "evidence_sha256": evidence_hashes,
        },
        "license": {
            "selected_source": "project_original_controlled_sentences",
            "spdx": "CC0-1.0",
            "noncommercial_only": False,
            "native_source_sentence_count": 0,
        },
        "checks": aggregate,
        "data_access": {
            "public_sentence_card_files_read": 1,
            "public_schema_files_read": 1,
            "public_license_notice_files_read": 1,
            "public_or_aggregate_evidence_files_read": 10,
            "prompt_hash_values_read": 20,
            "native_plaintext_files_read": 0,
            "row_level_files_read": 0,
            "raw_audio_files_read": 0,
            "locator_files_read": 0,
            "environment_files_read": 0,
            "model_files_read": 0,
            "human_rating_files_read": 0,
        },
        "completion": {
            "r00_completion_claim_allowed": True,
            "r01_completion_claim_allowed": False,
            "tts_or_audio_created": False,
            "human_evaluation_completed": False,
        },
        "findings": {
            "critical": 0,
            "high": 0,
            "medium": 0,
        },
    }


def write_immutable_report(
    *,
    workspace_root: Path,
    output_relative_path: str,
    report: Mapping[str, Any],
) -> Path:
    pure = PurePosixPath(output_relative_path)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in ("", ".", "..") for part in pure.parts)
        or "\\" in output_relative_path
    ):
        raise R00ValidationError(f"unsafe report path: {output_relative_path}")
    root = workspace_root.resolve()
    output = root.joinpath(*pure.parts)
    parent = output.parent
    parent.mkdir(parents=True, exist_ok=True)
    if parent.resolve() != parent or root not in parent.resolve().parents:
        raise R00ValidationError("report parent is symlinked or outside workspace")
    payload = (
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    if output.exists():
        if output.is_symlink() or not output.is_file():
            raise R00ValidationError("existing report is not a regular file")
        if output.read_text(encoding="utf-8") != payload:
            raise R00ValidationError("refusing to overwrite a different report")
        return output
    temporary = output.with_name(output.name + ".part")
    if temporary.exists() or temporary.is_symlink():
        raise R00ValidationError("temporary report path already exists")
    try:
        temporary.write_text(payload, encoding="utf-8", newline="\n")
        temporary.replace(output)
    finally:
        if temporary.exists() and temporary.is_file():
            temporary.unlink()
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    parser.add_argument("--card", default=DEFAULT_CARD)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate and print without writing the immutable report",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = validate_bundle(
            workspace_root=args.workspace_root,
            card_relative_path=args.card,
            schema_relative_path=args.schema,
        )
        output_path: Path | None = None
        if not args.validate_only:
            output_path = write_immutable_report(
                workspace_root=args.workspace_root,
                output_relative_path=args.report,
                report=report,
            )
    except (OSError, R00ValidationError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "invalid", "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    response = dict(report)
    if output_path is not None:
        response["report"] = {
            "path": args.report,
            "sha256": sha256_file(output_path),
        }
    print(json.dumps(response, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
