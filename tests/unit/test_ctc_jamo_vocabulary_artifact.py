"""The frozen jamo CTC label set must agree with the spaces it has to interoperate with.

Three label spaces meet in Stage C: the acoustic model's output layer, the R00
card's scorable phone inventory, and the expected-phone frontend's tokens.  Jamo
were chosen so that all three coincide; these tests keep that true, because a
drift would mean the model can emit a label nothing can score, or that an
expected phone can never be produced.
"""

from __future__ import annotations

import json
from pathlib import Path

from voice_coach.pronunciation_feedback.adapters.content_alignment_ctc import (
    load_vocabulary,
)
from voice_coach.pronunciation_feedback.ctc_alignment import (
    BLANK_TOKEN,
    WORD_DELIMITER_TOKEN,
)
from voice_coach.pronunciation_feedback.track_e_contract import (
    DEFAULT_CONTRACT_PATH,
    load_track_e_contract,
)

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "configs/pronunciation_feedback/ctc_jamo_vocabulary_v1.json"
R00_CARD = ROOT / "data/reference/r00/r00_sentence_card_v1.json"


def _document() -> dict:
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def test_artifact_loads_and_its_digest_matches_its_tokens() -> None:
    document = _document()

    vocabulary = load_vocabulary(ARTIFACT, expected_digest=document["digest"])

    assert vocabulary.tokens[0] == BLANK_TOKEN
    assert vocabulary.tokens[1] == WORD_DELIMITER_TOKEN
    assert vocabulary.size == document["label_count"]


def test_label_unit_and_claim_scope_are_declared() -> None:
    document = _document()
    contract = load_track_e_contract(ROOT / DEFAULT_CONTRACT_PATH)

    assert document["label_unit"] == "korean_compatibility_jamo"
    assert document["claim_scope"] == contract.claim_scope
    assert document["contract_sha256"] == contract.contract_sha256


def test_label_space_coincides_with_the_r00_scorable_inventory() -> None:
    """No trainable label that cannot be scored, and no scorable label that
    cannot be produced."""

    document = _document()
    card = json.loads(R00_CARD.read_text(encoding="utf-8"))
    inventory = set(card["phone_inventory"]["observed_target_labels"])
    labels = set(document["tokens"]) - {BLANK_TOKEN, WORD_DELIMITER_TOKEN}

    assert labels - inventory == set()
    assert inventory - labels == set()


def test_provenance_records_the_guarded_train_scan() -> None:
    source = _document()["source"]

    assert source["split"] == "train"
    assert source["guarded_utterances"] > 0
    assert set(source["excluded"]) >= {
        "qc_flagged",
        "reserved_prompt",
        "outside_cohort_scope",
        "zero_target_phone",
    }


def test_no_protected_split_or_audio_was_read() -> None:
    counters = _document()["access_counters"]

    assert counters["audio_read_count"] == 0
    assert counters["dev_split_read_count"] == 0
    assert counters["calibration_split_read_count"] == 0
    assert counters["locked_test_read_count"] == 0
    assert counters["expert_holdout_read_count"] == 0


def test_label_frequencies_cover_every_label() -> None:
    document = _document()
    labels = set(document["tokens"]) - {BLANK_TOKEN, WORD_DELIMITER_TOKEN}

    assert set(document["label_frequencies"]) == labels
    assert all(count > 0 for count in document["label_frequencies"].values())
