"""One-call visual feedback selection and a separate audiovisual grade.

The audio assessment/composite stays unchanged. Visual inputs have already
passed same-attempt media/evidence verification at the canonical boundary.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from .policy import number, require

REVISION = "audiovisual-lip-composite-v1"
AUDIO_WEIGHT = 60
VISUAL_WEIGHT = 40


def visual_context(assessment, visual):
    if visual is None:
        return None
    require(isinstance(visual, dict) and visual.get("coreSha256") == assessment["association"]["coreSha256"]
            and visual.get("requestId") == assessment["association"]["requestId"], "VISUAL_CORE_BINDING")
    score = visual.get("score")
    valid = (visual.get("status") == "SCORED" and visual.get("scoreKind") in {"CALIBRATED", "PROVISIONAL"}
             and number(score) and 0 <= score <= 100)
    if valid:
        score = float(Decimal(str(score)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
    targets = []
    # Lowest two different measured occurrences, never two copies of one phone.
    # A withheld whole-clip score must not erase a valid local observation.
    # Coverage controls the total only; each practice needs its own action.
    rows = sorted((r for r in visual.get("phones", []) if r.get("status") == "SCORED"
                   and number(r.get("score")) and 0 <= r["score"] <= 100
                   and r.get("scoreKind") in {"CALIBRATED", "PROVISIONAL"}
                   and "COMPARE_REFERENCE" in r.get("allowedActionIds", [])),
                  key=lambda r: (r["score"], r["expectedIndex"])) if visual.get("status") in {
                      "SCORED", "OBSERVED", "REFERENCE_ONLY"} else []
    seen = set()
    for row in rows:
        index = row["expectedIndex"]
        if index in seen:
            continue
        seen.add(index)
        target_id = "lip_articulation:" + str(index)
        targets.append(dict(criterionId=target_id, label="입술 조음", score=row["score"],
            evidenceIds=["visual:" + visual["visualEvidenceSha256"] + ":phone-" + str(index)],
            expectedPhone=row["expectedPhone"], role=row["role"],
            audioStartSeconds=row["audioStartSeconds"], audioEndSeconds=row["audioEndSeconds"],
            practiceScope="COMPARE_REFERENCE_VISIBLE_MOVEMENT_ONLY"))
        if len(targets) == 2:
            break
    audio = assessment.get("auxiliary", {}).get("composite", {}).get("result", {}).get("overallScore")
    combined = valid and number(audio)
    total = (float(((Decimal(str(audio)) * AUDIO_WEIGHT + Decimal(str(score)) * VISUAL_WEIGHT) / 100)
                   .quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)) if combined else audio)
    grade = dict(rubricRevision=REVISION, validationStatus="EXPERT_REVIEW_PENDING",
        overallScore=total, audioScore=audio, visualScore=score if valid else None,
        audioWeight=AUDIO_WEIGHT if valid else 100, visualWeight=VISUAL_WEIGHT if valid else 0,
        aggregationMode="AUDIO_VISUAL" if valid else "AUDIO_ONLY",
        scoreMeaning="VISIBLE_LIP_REFERENCE_PROXIMITY_AND_AUDIO",
        visualScoreKind=visual["scoreKind"] if valid else "NONE")
    return dict(grade=grade, targets=targets, input=visual)


def feedback_targets(assessment, context):
    by_id = {item["criterionId"]: item for item in assessment["criteria"]}
    audio = [by_id[key] for key in assessment["selectedCriterionIds"]]
    visual = context["targets"] if context else []
    return visual + audio[:3-len(visual)] if visual else audio
