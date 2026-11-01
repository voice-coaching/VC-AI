"""Public practice projection; utterance scores never become sentence grades."""
from __future__ import annotations

from typing import Any


def practice_result(*, script_text: str, decision: dict[str, Any],
                    score: dict[str, Any], coaching: dict[str, Any],
                    failed: bool) -> dict[str, Any]:
    """Copy validated output without another inference or a new grading policy.

    A missing candidate list does not mean missing analysis. GOP coaching can
    contain only whole-recording feedback and a rubric score. Sentence grading
    is unavailable even when the script happens to contain a single sentence.
    """
    accepted = decision["status"] == "ACCEPT" and not failed
    return dict(
        schemaVersion="voice-coaching.direct-practice-result.v1",
        scope="UTTERANCE",
        scriptText=script_text,
        decisionStatus=decision["status"],
        score=score,
        feedback=coaching.get("feedback") if accepted else None,
        sentenceResults=None,
        sentenceResultsReason="UTTERANCE_ONLY",
    )
