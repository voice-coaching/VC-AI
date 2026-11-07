"""Small public projection of server-computed scores; no regrading or raw evidence."""
from __future__ import annotations

from voice_coach.six_dimension.policy import CRITERIA


def score_details(six: dict) -> dict | None:
    """Keep partial scores visible without making details a gate on the total.

    Historical results without a complete six-criterion assessment simply omit
    this optional projection. The original score and private worksheet survive.
    """
    audio = six.get("compositeGrade")
    assessment = six.get("assessment")
    if not isinstance(audio, dict) or not isinstance(assessment, dict):
        return None
    if not {"overallScore", "validationStatus"} <= audio.keys():
        return None
    grades, observations = audio.get("criteria"), assessment.get("criteria")
    if not isinstance(grades, list) or not isinstance(observations, list):
        return None
    if (len(grades) != len(CRITERIA) or len(observations) != len(CRITERIA)
            or any(not isinstance(row, dict) for row in grades + observations)):
        return None
    graded = {row.get("criterionId"): row for row in grades}
    observed = {row.get("criterionId"): row for row in observations}
    if set(graded) != set(CRITERIA) or set(observed) != set(CRITERIA):
        return None
    if any(not {"score", "weight"} <= graded[key].keys()
           or not {"label", "status", "reasonCode"} <= observed[key].keys()
           for key in CRITERIA):
        return None
    visual = six.get("audiovisualGrade") or {}
    combined = visual.get("aggregationMode") == "AUDIO_VISUAL"
    return dict(
        schemaVersion="voice-coaching.score-details.v1",
        validationStatus=audio["validationStatus"],
        aggregationMode="AUDIO_VISUAL" if combined else "AUDIO_ONLY",
        audioScore=audio["overallScore"], visualScore=visual.get("visualScore"),
        audioWeight=visual["audioWeight"] if combined else 100,
        visualWeight=visual["visualWeight"] if combined else 0,
        criteria=[dict(criterionId=key, label=observed[key]["label"],
                       score=graded[key]["score"], status=observed[key]["status"],
                       reasonCode=observed[key]["reasonCode"], weight=graded[key]["weight"])
                  for key in CRITERIA],
    )
