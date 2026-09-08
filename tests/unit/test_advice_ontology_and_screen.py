"""The 17-tag catalog must widen coverage without pretending to be reviewed.

Two things are easy to get wrong here and both would mislead: serving draft advice
as if it were approved, and letting an automated safety screen read as an expert
audit. The tests below make each impossible by type.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voice_coach.pronunciation_feedback.adapters.advice_ontology import (
    ABSTAIN_ACTION,
    CATALOG_ID,
    AdviceOntologyError,
    OntologyAdviceCatalog,
)
from voice_coach.pronunciation_feedback.coaching_screen import (
    SCREEN_ID,
    ZERO_TOLERANCE_CATEGORIES,
    CoachingScreenError,
    ScreenResult,
    screen_catalog,
    screen_coaching_text,
)

ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY = ROOT / "configs/coaching/korean_error_advice_ontology_v1.json"


def _catalog(**kwargs: object) -> OntologyAdviceCatalog:
    return OntologyAdviceCatalog(ONTOLOGY, **kwargs)  # type: ignore[arg-type]


def _write_ontology(tmp_path: Path, tags: list[dict]) -> Path:
    path = tmp_path / "ontology.json"
    path.write_text(
        json.dumps({"ontology_id": "test-v1", "tags": tags}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _tag(
    tag_id: str = "initial_consonant",
    *,
    action: str = "draft_coach",
    review_status: str = "engineering_draft",
    advice_id: str | None = "KO_X_001",
    message: str | None = "표시된 구간을 다시 읽어 보세요.",
) -> dict:
    return {
        "id": tag_id,
        "raw_label": "초성",
        "target_scope": "onset_consonant",
        "localization_policy": "phone_overlap_required",
        "advice": {
            "advice_id": advice_id,
            "action": action,
            "message_ko": message,
            "review_status": review_status,
        },
    }


# -- coverage widened ---------------------------------------------------------


def test_repository_ontology_serves_the_annotated_taxonomy() -> None:
    catalog = _catalog()

    assert catalog.catalog_id == CATALOG_ID
    # 17 annotated tags: 16 coachable drafts plus one declared abstention.
    assert len(catalog.allowed_issue_types) == 16
    assert catalog.abstaining_issue_types == ("other",)
    assert "tensification" in catalog.allowed_issue_types
    assert "final_consonant" in catalog.allowed_issue_types


def test_lookup_keeps_the_port_shape() -> None:
    catalog = _catalog()

    entry = catalog.lookup("tensification")

    assert entry is not None
    advice_id, message = entry
    assert advice_id.startswith("KO_")
    assert message


def test_every_served_entry_reports_a_draft_review_status() -> None:
    catalog = _catalog()

    assert catalog.review_states_served == ("engineering_draft",)
    for issue_type in catalog.allowed_issue_types:
        record = catalog.entry(issue_type)
        assert record is not None
        assert record.review_status == "engineering_draft"


def test_the_catch_all_tag_abstains_instead_of_advising() -> None:
    """An unclassified error routes to review; a generic "try again" is not advice."""

    catalog = _catalog()

    assert catalog.abstains_for("other") is True
    assert catalog.lookup("other") is None
    assert catalog.entry("other") is None


def test_unknown_issue_type_yields_nothing() -> None:
    assert _catalog().lookup("not_a_tag") is None


# -- review status is a gate, not decoration ----------------------------------


def test_narrowing_accepted_states_withholds_draft_entries(tmp_path: Path) -> None:
    path = _write_ontology(tmp_path, [_tag(), _tag("final_consonant")])

    with pytest.raises(AdviceOntologyError, match="no ontology entry is servable"):
        OntologyAdviceCatalog(path, accepted_review_states=("c03_approved",))


def test_entries_outside_the_accepted_states_are_reported_as_withheld(
    tmp_path: Path,
) -> None:
    path = _write_ontology(
        tmp_path,
        [_tag(), _tag("final_consonant", review_status="c03_approved")],
    )

    catalog = OntologyAdviceCatalog(path, accepted_review_states=("c03_approved",))

    assert catalog.allowed_issue_types == ("final_consonant",)
    assert catalog.withheld_issue_types == {"initial_consonant": "engineering_draft"}


def test_empty_accepted_state_set_is_rejected(tmp_path: Path) -> None:
    path = _write_ontology(tmp_path, [_tag()])

    with pytest.raises(AdviceOntologyError, match="must not be empty"):
        OntologyAdviceCatalog(path, accepted_review_states=())


def test_an_abstaining_tag_carrying_content_fails_closed(tmp_path: Path) -> None:
    path = _write_ontology(
        tmp_path,
        [_tag(), _tag("other", action=ABSTAIN_ACTION, message="아무 조언")],
    )

    with pytest.raises(AdviceOntologyError, match="abstains but carries advice content"):
        OntologyAdviceCatalog(path)


@pytest.mark.parametrize(
    ("tags", "message"),
    [
        ([], "carries no tags"),
        ([_tag(action="promote_to_production")], "unknown action"),
        ([_tag(), _tag()], "duplicate tag id"),
    ],
)
def test_malformed_ontology_fails_closed(
    tmp_path: Path, tags: list[dict], message: str
) -> None:
    path = _write_ontology(tmp_path, tags)

    with pytest.raises(AdviceOntologyError, match=message):
        OntologyAdviceCatalog(path)


def test_absent_or_unnamed_ontology_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(AdviceOntologyError, match="artifact is unavailable"):
        OntologyAdviceCatalog(tmp_path / "absent.json")

    unnamed = tmp_path / "unnamed.json"
    unnamed.write_text(json.dumps({"tags": [_tag()]}), encoding="utf-8")
    with pytest.raises(AdviceOntologyError, match="must name itself"):
        OntologyAdviceCatalog(unnamed)


# -- the screen cannot pose as an audit ---------------------------------------


def test_screen_verdict_vocabulary_excludes_pass_and_approved() -> None:
    result = screen_coaching_text("표시된 받침을 유지해 보세요.")

    assert result.verdict == "no_flag"
    assert result.claim_scope == "engineering_screen"
    assert result.substitutes_expert_review is False
    assert result.expert_review_still_required is True
    assert result.may_serve is True


def test_a_screen_result_cannot_claim_to_replace_review() -> None:
    with pytest.raises(CoachingScreenError, match="never substitutes"):
        ScreenResult(
            screen_id=SCREEN_ID,
            verdict="no_flag",
            findings=(),
            substitutes_expert_review=True,
        )


def test_expert_review_cannot_be_waived_by_a_screen() -> None:
    with pytest.raises(CoachingScreenError, match="cannot be waived"):
        ScreenResult(
            screen_id=SCREEN_ID,
            verdict="no_flag",
            findings=(),
            expert_review_still_required=False,
        )


def test_a_screen_result_cannot_carry_another_claim_scope() -> None:
    with pytest.raises(CoachingScreenError, match="only engineering_screen"):
        ScreenResult(
            screen_id=SCREEN_ID,
            verdict="no_flag",
            findings=(),
            claim_scope="research_validated",
        )


# -- the four zero-tolerance categories --------------------------------------


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("조음 장애가 의심되니 병원에서 진료를 받아 보세요.", "medical_judgment"),
        ("발음으로 보아 중국 출신이신 것 같습니다.", "speaker_identity_inference"),
        ("발음이 어눌해서 알아듣기 어렵습니다.", "discriminatory_expression"),
        ("아플 때까지 반드시 해야 합니다.", "risk_expression"),
        ("성대에 힘을 주어 눌러 발음하세요.", "risk_expression"),
        ("이 방법이면 100% 완치됩니다.", "risk_expression"),
        ("중국인이라서 이 소리가 어렵습니다.", "speaker_identity_inference"),
    ],
)
def test_each_zero_tolerance_category_is_detected(text: str, category: str) -> None:
    result = screen_coaching_text(text)

    assert result.verdict == "flagged"
    assert category in result.flagged_categories
    assert result.may_serve is False


def test_all_four_categories_are_covered_by_patterns() -> None:
    assert set(ZERO_TOLERANCE_CATEGORIES) == {
        "risk_expression",
        "discriminatory_expression",
        "speaker_identity_inference",
        "medical_judgment",
    }


def test_a_flag_reports_where_to_fix_the_wording() -> None:
    result = screen_coaching_text("혀끝을 대고 읽되 아플 때까지 계속하세요.")

    assert result.findings
    finding = result.findings[0]
    assert finding.end > finding.start
    assert finding.matched_text


def test_findings_must_agree_with_the_verdict() -> None:
    with pytest.raises(CoachingScreenError, match="must name its findings"):
        ScreenResult(screen_id=SCREEN_ID, verdict="flagged", findings=())


def test_empty_text_is_rejected() -> None:
    with pytest.raises(CoachingScreenError, match="non-empty"):
        screen_coaching_text("   ")


# -- catalog-level triage -----------------------------------------------------


def test_repository_ontology_messages_carry_no_screen_finding() -> None:
    """A clean screen means "nothing this screen detects", not "expert approved"."""

    catalog = _catalog()
    entries = [
        (issue_type, catalog.lookup(issue_type)[1])  # type: ignore[index]
        for issue_type in catalog.allowed_issue_types
    ]

    report = screen_catalog(entries)

    assert report.entry_count == 16
    assert report.flagged == {}
    assert report.all_clear_of_screen is True
    assert report.substitutes_expert_review is False


def test_legacy_catalog_messages_carry_no_screen_finding() -> None:
    from voice_coach.coaching.catalog import ADVICE_CATALOG

    report = screen_catalog(
        [(issue_type, message) for issue_type, (_id, message) in ADVICE_CATALOG.items()]
    )

    assert report.flagged == {}


def test_catalog_screen_names_the_flagged_entries() -> None:
    report = screen_catalog(
        [
            ("safe_type", "받침을 유지해 보세요."),
            ("unsafe_type", "질환이 의심되니 치료를 받으세요."),
        ]
    )

    assert report.flagged_count == 1
    assert "unsafe_type" in report.flagged
    assert "medical_judgment" in report.flagged["unsafe_type"]


def test_empty_catalog_screen_is_rejected() -> None:
    with pytest.raises(CoachingScreenError, match="no catalog entry"):
        screen_catalog([])
