"""The tag → target-phone match must follow the declared interval rules exactly.

C01 forbids an automatic target-phone claim "without positive interval overlap"
and fixes the rest: half-open intervals, strictly positive overlap, maximum
overlap duration as the primary match, then lowest phone index, then lexicographic
label. An error tag that claims the wrong phone becomes a coaching message
pointing at the wrong sound, so these rules are tested rather than trusted.

The aggregate itself is also checked for the two ways it could mislead: by
covering splits it must not read, and by letting a prosodic-span tag claim a phone.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/build_c01_target_phone_joint_aggregate.py"
AGGREGATE = ROOT / "configs/coaching/c01_target_phone_joint_aggregate_v1.json"
ONTOLOGY = ROOT / "configs/coaching/korean_error_advice_ontology_v1.json"


def _load_module():
    spec = importlib.util.spec_from_file_location("c01_aggregate", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load_module()
match_target_phone = MODULE.match_target_phone


def _phone(label: str, start: float, end: float) -> dict:
    return {"label": label, "start": start, "end": end}


# -- the declared interval rules ----------------------------------------------


def test_maximum_overlap_duration_wins() -> None:
    phones = [_phone("ㄱ", 0.0, 0.10), _phone("ㅏ", 0.10, 0.40)]

    label, overlap = match_target_phone(error_start=0.05, error_end=0.35, phones=phones)

    assert label == "ㅏ"
    assert overlap == pytest.approx(0.25)


def test_touching_endpoints_do_not_overlap() -> None:
    """Half-open semantics: an error starting where a phone ends does not match it."""

    phones = [_phone("ㄱ", 0.0, 0.10)]

    label, overlap = match_target_phone(error_start=0.10, error_end=0.20, phones=phones)

    assert label is None
    assert overlap == 0.0


def test_zero_duration_overlap_is_not_positive() -> None:
    phones = [_phone("ㄱ", 0.0, 0.10), _phone("ㅏ", 0.10, 0.20)]

    label, _ = match_target_phone(error_start=0.10, error_end=0.10, phones=phones)

    assert label is None


def test_no_overlap_abstains_instead_of_taking_the_nearest() -> None:
    """The contract forbids a claim without overlap; proximity is not evidence."""

    phones = [_phone("ㄱ", 0.0, 0.10)]

    label, _ = match_target_phone(error_start=0.50, error_end=0.60, phones=phones)

    assert label is None


def test_equal_overlap_breaks_to_the_lowest_phone_index() -> None:
    phones = [_phone("ㅌ", 0.00, 0.10), _phone("ㄱ", 0.10, 0.20)]

    label, overlap = match_target_phone(error_start=0.05, error_end=0.15, phones=phones)

    assert label == "ㅌ"
    assert overlap == pytest.approx(0.05)


def test_equal_durations_that_differ_only_in_float_bits_still_tie() -> None:
    """0.15-0.10 != 0.10-0.05 in binary floating point.

    Without a tolerance the declared tie-breakers never fire and float noise picks
    the target phone. Both orderings must resolve to the lower-index phone.
    """

    assert (0.15 - 0.10) != (0.10 - 0.05)

    first = [_phone("ㅌ", 0.00, 0.10), _phone("ㄱ", 0.10, 0.20)]
    reordered = [_phone("ㄱ", 0.10, 0.20), _phone("ㅌ", 0.00, 0.10)]

    a, _ = match_target_phone(error_start=0.05, error_end=0.15, phones=first)
    b, _ = match_target_phone(error_start=0.05, error_end=0.15, phones=reordered)

    # Lowest index wins in each ordering, by the rule rather than by float noise.
    assert a == "ㅌ"
    assert b == "ㄱ"


def test_error_fully_inside_one_phone_matches_it() -> None:
    phones = [_phone("ㅅ", 0.0, 1.0)]

    label, overlap = match_target_phone(error_start=0.4, error_end=0.6, phones=phones)

    assert label == "ㅅ"
    assert overlap == pytest.approx(0.2)


def test_phone_fully_inside_the_error_matches_it() -> None:
    phones = [_phone("ㅅ", 0.4, 0.6)]

    label, overlap = match_target_phone(error_start=0.0, error_end=1.0, phones=phones)

    assert label == "ㅅ"
    assert overlap == pytest.approx(0.2)


def test_malformed_phone_entries_are_skipped() -> None:
    phones = [
        {"label": None, "start": 0.0, "end": 1.0},
        {"label": "ㅅ", "start": None, "end": 1.0},
        _phone("ㄴ", 0.0, 1.0),
    ]

    label, _ = match_target_phone(error_start=0.2, error_end=0.4, phones=phones)

    assert label == "ㄴ"


def test_empty_phone_list_abstains() -> None:
    label, overlap = match_target_phone(error_start=0.0, error_end=1.0, phones=[])

    assert label is None
    assert overlap == 0.0


# -- the published aggregate --------------------------------------------------


def _aggregate() -> dict:
    return json.loads(AGGREGATE.read_text(encoding="utf-8"))


def test_aggregate_withholds_the_protected_splits() -> None:
    """Equality with the all-split count would mean the access contract broke."""

    totals = _aggregate()["totals"]

    assert totals["scan_covers_all_splits"] is False
    assert set(totals["splits_withheld"]) == {"stack", "calibration", "locked_test"}
    assert (
        totals["error_intervals_in_scanned_splits"]
        < totals["declared_all_split_error_interval_count"]
    )


def test_aggregate_retains_counts_only() -> None:
    document = _aggregate()

    assert document["access_scope"] == "privacy_safe_aggregate_only"
    assert document["model_input_use"] == "prohibited"
    for withheld in ("utterance_id", "speaker_id", "audio_path"):
        assert withheld in document["withheld_fields"]
    serialized = json.dumps(document, ensure_ascii=False)
    assert "audio_path" not in serialized.replace('"audio_path"', "")


def test_only_phone_overlap_tags_claim_a_target_phone() -> None:
    """A prosodic span belongs to no single phone; a catch-all tag claims nothing."""

    for record in _aggregate()["tag_support"].values():
        policy = record["localization_policy"]
        if policy == "phone_overlap_required":
            continue
        assert record["matched_count"] == 0
        assert record["target_phone_counts"] == {}


def test_every_interval_is_accounted_for_in_one_bucket() -> None:
    for tag_id, record in _aggregate()["tag_support"].items():
        accounted = (
            record["matched_count"]
            + record["unmatched_count"]
            + record["abstained_count"]
            + record["prosodic_span_count"]
        )
        assert accounted == record["interval_count"], tag_id


def test_no_unknown_error_tag_appeared() -> None:
    assert _aggregate()["totals"]["unknown_error_tags"] == {}


def test_aggregate_covers_every_ontology_tag() -> None:
    ontology = json.loads(ONTOLOGY.read_text(encoding="utf-8"))
    expected = {str(tag["id"]) for tag in ontology["tags"]}

    assert set(_aggregate()["tag_support"]) == expected


@pytest.mark.parametrize(
    ("tag_id", "expected_phones"),
    [
        ("aspiration", {"ㅋ", "ㅌ", "ㅊ", "ㅍ"}),
        ("tensification", {"ㄲ", "ㄸ", "ㅉ", "ㅃ", "ㅆ"}),
        ("nasalization", {"ㄴ", "ㅇ", "ㅁ"}),
        ("palatalization", {"ㅈ", "ㅊ"}),
    ],
)
def test_top_target_phones_match_the_named_phonological_process(
    tag_id: str, expected_phones: set[str]
) -> None:
    """Empirical support: each tag's top phones are the sounds the tag is about.

    This is what ``empirical_target_phone_support: null`` was a placeholder for.
    """

    top = set(_aggregate()["tag_support"][tag_id]["top_target_phones"])

    assert top & expected_phones, f"{tag_id} top phones were {top}"


def test_interval_rules_recorded_match_the_ontology() -> None:
    ontology = json.loads(ONTOLOGY.read_text(encoding="utf-8"))

    assert (
        _aggregate()["interval_rules_applied"]
        == ontology["target_phone_mapping"]["interval_rules"]
    )


def test_ontology_status_flip_is_blocked_by_its_own_schema() -> None:
    """Adopting this evidence needs a schema v2, not an in-place edit.

    The schema pins ``empirical_status`` as a const and fixes both evidence arrays
    at exactly three items, so the ontology cannot record the new evidence without
    a deliberate version bump.
    """

    schema = json.loads(
        (ROOT / "configs/coaching/korean_error_advice_ontology_v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    mapping = schema["properties"]["target_phone_mapping"]

    assert mapping["properties"]["empirical_status"]["const"] == (
        "pending_privacy_safe_joint_aggregate"
    )
    assert mapping["additionalProperties"] is False
    for field in ("available_aggregate_evidence", "unavailable_evidence"):
        assert mapping["properties"][field]["minItems"] == 3
        assert mapping["properties"][field]["maxItems"] == 3
