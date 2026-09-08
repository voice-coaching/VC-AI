"""The learner view must not invent, move, or soften what the gate decided.

The text renderer is the only place in this pipeline where a decision becomes
something a person reads, so the two things it could get wrong are pinned here:
putting the highlight on the wrong syllable, and dropping a notice or a state
that qualifies what the learner is being told.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "feedback_cli", ROOT / "scripts" / "run_pronunciation_feedback.py"
)
cli = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules[_spec.name] = cli
_spec.loader.exec_module(cli)

SENTENCE = "국물 냄새가 정말 구수해요."


def _result(**overrides) -> dict:
    feedback = {
        "display_text": SENTENCE,
        "next_action": "coach",
        "advice": [
            {
                "issue_type": "vowel_quality",
                "advice_id": "ARTICULATION_RULE_021",
                "message": "목표 모음의 혀 높이와 입술 모양을 확인해 보세요.",
                # "수" is the 12th character of the sentence.
                "span": {
                    "syllable_index": 8,
                    "char_start": 11,
                    "char_end": 12,
                    "start_ms": 2360,
                    "end_ms": 2500,
                    "syllable": "수",
                },
                "severity": 0.91,
                "confidence": 0.92,
            }
        ],
        "aspects": [
            {"name": "content", "score": 0.836, "state": "observed"},
            {"name": "rhythm", "score": None, "state": "low_confidence"},
        ],
        "unlocated_issue_types": [],
        "reference_audio": [],
        "notices": ["이 진단의 임계값은 'fixture-cohort' 코호트에서 고정되었다."],
    }
    feedback.update(overrides.pop("feedback", {}))
    result = {
        "status": "coaching_ready",
        "failure_code": None,
        "reason": "gate passed",
        "learner_feedback": feedback,
    }
    result.update(overrides)
    return result


def _render(result: dict) -> str:
    original = cli._to_jsonable
    cli._to_jsonable = lambda value: value
    try:
        return cli._render_text(result)
    finally:
        cli._to_jsonable = original


def test_the_highlight_lands_on_the_syllable_the_span_names() -> None:
    rendered = _render(_result())

    assert "국물 냄새가 정말 구[수]해요." in rendered


def test_a_repeated_syllable_is_highlighted_by_offset_not_by_search() -> None:
    """``또박또박`` repeats a syllable.  Searching for the text would mark the
    first occurrence, which is not the one the alignment found."""

    sentence = "또박또박 말해요."
    result = _result()
    result["learner_feedback"]["display_text"] = sentence
    result["learner_feedback"]["advice"][0]["span"] = {
        "syllable_index": 2,
        "char_start": 2,
        "char_end": 3,
        "start_ms": 500,
        "end_ms": 640,
        "syllable": "또",
    }

    assert "또박[또]박 말해요." in _render(result)


def test_an_unmeasured_axis_is_shown_as_its_state_not_as_a_zero() -> None:
    """A dash and a state say "not measured"; a 0.000 says "measured, and bad"."""

    rendered = _render(_result())

    assert "rhythm" in rendered
    assert "low_confidence" in rendered
    assert "rhythm      0.000" not in rendered


def test_every_notice_reaches_the_reader() -> None:
    result = _result()
    result["learner_feedback"]["notices"] = ["첫째 고지", "둘째 고지"]

    rendered = _render(result)

    assert "첫째 고지" in rendered
    assert "둘째 고지" in rendered


def test_an_issue_with_no_location_says_so_instead_of_guessing_one() -> None:
    result = _result()
    result["learner_feedback"]["advice"][0]["span"] = None
    result["learner_feedback"]["unlocated_issue_types"] = ["vowel_quality"]

    rendered = _render(result)

    assert "위치 미확정" in rendered
    assert "vowel_quality" in rendered


def test_a_failed_attempt_reports_its_code_and_reason() -> None:
    result = _result(
        status="rerecord_required",
        failure_code="prompt_mismatch",
        reason="read text does not match the prompt",
        learner_feedback=None,
    )

    rendered = _render(result)

    assert "prompt_mismatch" in rendered
    assert "read text does not match the prompt" in rendered


def test_the_severity_and_confidence_of_an_advice_item_are_shown() -> None:
    """Coaching without its confidence reads as a verdict rather than a finding."""

    rendered = _render(_result())

    assert "0.91" in rendered and "0.92" in rendered


def test_cli_requires_endpoint_and_pinned_revision_together(capsys) -> None:
    exit_code = cli.main(
        [
            "--advice-endpoint",
            "http://127.0.0.1:8000/v1",
            "prompts",
        ]
    )

    assert exit_code == 2
    assert "advice_generator_invalid" in capsys.readouterr().out


def test_cli_rejects_a_non_loopback_advice_endpoint(capsys) -> None:
    exit_code = cli.main(
        [
            "--advice-endpoint",
            "http://example.com/v1",
            "--advice-revision",
            "pinned-commit",
            "prompts",
        ]
    )

    assert exit_code == 2
    assert "loopback" in capsys.readouterr().out
