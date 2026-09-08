"""Stage A0/A contracts: prompt catalog identity and syllable anchoring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voice_coach.pronunciation_feedback.adapters.catalog_r00 import (
    DEFAULT_CARD_PATH,
    R00PromptCatalog,
)
from voice_coach.pronunciation_feedback.adapters.syllables import (
    decompose_syllables,
    normalize_prompt_text,
    syllable_map_sha256,
    text_sha256,
)
from voice_coach.pronunciation_feedback.domain import FailureCode, TargetLanguage
from voice_coach.pronunciation_feedback.errors import PromptUnavailableError

CARD_PATH = Path(DEFAULT_CARD_PATH)


def _card() -> dict:
    return json.loads(CARD_PATH.read_text(encoding="utf-8"))


def _write_card(path: Path, card: dict) -> Path:
    target = path / "card.json"
    target.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    return target


def test_normalization_collapses_whitespace_and_applies_nfkc() -> None:
    assert normalize_prompt_text("쌀과  콩을\t담아요.") == "쌀과 콩을 담아요."


def test_decompose_syllables_anchors_offsets_and_jamo() -> None:
    slots = decompose_syllables("맑은 밤")

    assert [slot.syllable for slot in slots] == ["맑", "은", "밤"]
    assert [(slot.char_start, slot.char_end) for slot in slots] == [
        (0, 1),
        (1, 2),
        (3, 4),
    ]
    assert (slots[0].onset, slots[0].nucleus, slots[0].coda) == ("ㅁ", "ㅏ", "ㄺ")
    assert (slots[1].onset, slots[1].nucleus, slots[1].coda) == ("ㅇ", "ㅡ", "ㄴ")
    assert decompose_syllables("가")[0].coda is None


def test_syllable_map_digest_changes_with_offsets() -> None:
    first = syllable_map_sha256(decompose_syllables("가나"))
    second = syllable_map_sha256(decompose_syllables("가 나"))

    assert first != second


@pytest.mark.skipif(not CARD_PATH.is_file(), reason="R00 card asset is absent")
def test_catalog_serves_the_fixed_card() -> None:
    catalog = R00PromptCatalog(CARD_PATH)
    prompts = catalog.list_prompts()

    assert len(prompts) == 20
    assert catalog.revision.startswith("voice-coach-r00-fixed-korean-sentences-v1@")
    assert all(prompt.license_spdx == "CC0-1.0" for prompt in prompts)
    assert all(prompt.catalog_revision == catalog.revision for prompt in prompts)
    assert {prompt.target_language for prompt in prompts} == {TargetLanguage.KOREAN}
    difficulties = {prompt.difficulty for prompt in prompts}
    assert difficulties == {"beginner", "intermediate", "advanced"}


@pytest.mark.skipif(not CARD_PATH.is_file(), reason="R00 card asset is absent")
def test_catalog_prompt_text_matches_declared_digest() -> None:
    catalog = R00PromptCatalog(CARD_PATH)

    for prompt in catalog.list_prompts():
        assert normalize_prompt_text(prompt.display_text) == prompt.normalized_text
        assert text_sha256(prompt.normalized_text) == prompt.normalized_text_sha256
        assert decompose_syllables(prompt.normalized_text)


@pytest.mark.skipif(not CARD_PATH.is_file(), reason="R00 card asset is absent")
def test_catalog_filters_by_difficulty_and_target() -> None:
    catalog = R00PromptCatalog(CARD_PATH)

    beginner = catalog.list_prompts(difficulty="beginner")
    assert {prompt.difficulty for prompt in beginner} == {"beginner"}

    liquidization = catalog.list_prompts(target="liquidization")
    assert liquidization
    assert all(
        "liquidization" in prompt.practice_targets for prompt in liquidization
    )


@pytest.mark.skipif(not CARD_PATH.is_file(), reason="R00 card asset is absent")
def test_catalog_rejects_unknown_prompt_and_empty_filter() -> None:
    catalog = R00PromptCatalog(CARD_PATH)

    with pytest.raises(PromptUnavailableError) as unknown:
        catalog.get_prompt("R00-99")
    assert unknown.value.code is FailureCode.PROMPT_NOT_AVAILABLE

    with pytest.raises(PromptUnavailableError) as no_match:
        catalog.list_prompts(difficulty="expert")
    assert no_match.value.code is FailureCode.PROMPT_NOT_AVAILABLE


def test_catalog_fails_closed_on_missing_card(tmp_path: Path) -> None:
    with pytest.raises(PromptUnavailableError) as error:
        R00PromptCatalog(tmp_path / "absent.json")

    assert error.value.code is FailureCode.PROMPT_CATALOG_UNAVAILABLE


@pytest.mark.skipif(not CARD_PATH.is_file(), reason="R00 card asset is absent")
def test_catalog_fails_closed_on_tampered_text(tmp_path: Path) -> None:
    card = _card()
    card["sentences"][0]["text"] = "다른 문장입니다."

    with pytest.raises(PromptUnavailableError) as error:
        R00PromptCatalog(_write_card(tmp_path, card))

    assert error.value.code is FailureCode.PROMPT_CATALOG_UNAVAILABLE
    assert "normalized_text" in error.value.detail


@pytest.mark.skipif(not CARD_PATH.is_file(), reason="R00 card asset is absent")
def test_catalog_fails_closed_on_unexpected_status(tmp_path: Path) -> None:
    card = _card()
    card["status"] = "draft"

    with pytest.raises(PromptUnavailableError) as error:
        R00PromptCatalog(_write_card(tmp_path, card))

    assert error.value.code is FailureCode.PROMPT_CATALOG_UNAVAILABLE


@pytest.mark.skipif(not CARD_PATH.is_file(), reason="R00 card asset is absent")
def test_catalog_fails_closed_when_a_content_check_did_not_pass(
    tmp_path: Path,
) -> None:
    card = _card()
    card["sentences"][2]["pii_check"]["status"] = "pending"

    with pytest.raises(PromptUnavailableError) as error:
        R00PromptCatalog(_write_card(tmp_path, card))

    assert "pii_check" in error.value.detail


@pytest.mark.skipif(not CARD_PATH.is_file(), reason="R00 card asset is absent")
def test_rule_targets_expose_ids_without_pending_realizations() -> None:
    catalog = R00PromptCatalog(CARD_PATH)
    card = _card()
    pending_realizations = {
        entry["expected_realization_hangul"]
        for sentence in card["sentences"]
        for entry in sentence["phonological_rule_targets"]
    }
    assert pending_realizations  # the card does declare pending realizations

    served = {
        target
        for prompt in catalog.list_prompts()
        for target in prompt.practice_targets
    }
    assert "liquidization" in served
    assert served.isdisjoint(pending_realizations)
