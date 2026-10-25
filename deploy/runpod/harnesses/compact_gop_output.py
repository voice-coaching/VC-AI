"""Expand compact GPT output without judging or recalculating its scores.

Packaged into the immutable adapter by build_compact_h5.py. The public result
continues to use the parent's scoring/feedback schema.
"""
import math


COMPACT_CRITERION_IDS = (
    "vowels", "plain_stops", "tense_stops", "aspirated_stops", "fricatives",
    "affricates", "nasals", "liquid", "coverage",
)
COMPACT_RUBRIC_REVISION = "phone-rubric-gop-ctc-af-sd-v1"


def expand_compact_output(candidate, rubric):
    def check(condition):
        if not condition:
            raise ValueError("COMPACT_OUTPUT_INVALID")

    check(isinstance(candidate, dict) and set(candidate) == {"levels", "score", "feedback"})
    check(rubric.get("rubricRevision") == COMPACT_RUBRIC_REVISION)
    check(tuple(item["id"] for item in rubric["criteria"]) == COMPACT_CRITERION_IDS)
    feedback = candidate["feedback"]
    check(isinstance(feedback, str) and 0 < len(feedback) <= 4000)
    levels, score = candidate["levels"], candidate["score"]
    if levels is None:
        check(score is None)
        return {"scoring": {"status": "UNSCORABLE"}, "feedback": feedback}
    check(type(levels) is list and len(levels) == len(COMPACT_CRITERION_IDS))
    check(all(value is None or type(value) is int and 0 <= value <= 4 for value in levels))
    check(any(value is not None for value in levels))
    check(type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 100)
    return {
        "scoring": {
            "status": "READY",
            "result": {
                "rubricRevision": rubric["rubricRevision"],
                "criteria": [{"criterionId": key, "level": value}
                             for key, value in zip(COMPACT_CRITERION_IDS, levels)],
                "overallScore": score,
            },
        },
        "feedback": feedback,
    }
