"""Hierarchical audio evidence and non-scoring, selected-span visual observations."""
from __future__ import annotations
import hashlib
import json
import math
from decimal import Decimal, ROUND_HALF_UP
from urllib.request import Request, urlopen

from .clova_scoring import (STATES, detailed_evidence, criterion_level, _strict_json, _score_document)
from voice_coach.pronunciation_feedback.clova_prompt_routing import load_scoring_prompt
from voice_coach.pronunciation_feedback.adapters.advice_generator_hyperclova import _completion_content

REVISION = "clova-phone-rubric-v3"
MEASUREMENTS = ("inner_aperture_ratio", "outer_aperture_ratio", "inner_area_ratio", "outer_area_ratio")


def visual_observation(analysis, video_requested, supplement):
    result = dict(status="UNAVAILABLE" if video_requested else "NOT_PROVIDED",
                  selectedExpectedIndex=None, videoStartMs=None, videoEndMs=None,
                  supplementSha256=None, measurements={})
    selected = analysis.pronunciation_evidence
    if not video_requested or supplement is None or selected is None:
        return result
    observation = supplement.closed_beta_lip_observation
    if not isinstance(observation, dict):
        return result
    if (supplement.selected_expected_index != selected.selected_expected_index
            or observation.get("selectedExpectedIndex") != selected.selected_expected_index
            or observation.get("containsPronunciationTruth") is not False
            or observation.get("containsActionTruth") is not False):
        return result
    measurements = observation.get("measurements", {})
    values = {}
    for key in MEASUREMENTS:
        item = measurements.get(key)
        if not isinstance(item, dict) or item.get("unit") != "ratio":
            continue
        value = item.get("value")
        if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1000000:
            values[key] = value
    start, end = observation.get("videoStartMs"), observation.get("videoEndMs")
    if not values or type(start) is not int or type(end) is not int or not 0 <= start < end <= 180000:
        return result
    return dict(status="NOT_VALIDATED", selectedExpectedIndex=selected.selected_expected_index,
                videoStartMs=start, videoEndMs=end, supplementSha256=supplement.supplement_sha256,
                measurements=values)


def collect_hierarchy(analysis, rubric):
    counts, parents = detailed_evidence(analysis, rubric)
    rules = rubric["phoneCriteria"]
    tokens = [r["phone"] for r in rules]
    if len(tokens) != 40 or len(set(tokens)) != 40:
        raise ValueError("invalid leaf catalog")
    leaves = []
    for rule in rules:
        parent = next(c for c in rubric["criteria"] if c["id"] == rule["parentId"])
        if rule["phone"] not in parent["phones"]:
            raise ValueError("invalid leaf parent")
        phones = [p for p in analysis.analysis_evidence["phones"] if p["expected_phone"] == rule["phone"]]
        row = dict(criterionId=rule["id"], sampleCount=len(phones), **dict.fromkeys(STATES, 0))
        for p in phones:
            state = {"correct": "matched", "substitution": "substituted", "deletion": "deleted"}[p["alignment_operation"]]
            row[state + ("Flagged" if p["is_error"] else "Clear")] += 1
        row["level"] = criterion_level(row, rubric)
        leaves.append(row)
    for row in parents:
        row["level"] = criterion_level(row, rubric)
    return counts, parents, leaves


def score_hierarchy(scorer, analysis, *, video_requested=False, visual_supplement=None):
    system, rubric, rubric_digest, prompt_digest = load_scoring_prompt(scorer.repository_root, REVISION)
    counts, parents, leaves = collect_hierarchy(analysis, rubric)
    visual = visual_observation(analysis, video_requested, visual_supplement)
    numerator = sum(r["weight"] * p["level"] for r, p in zip(rubric["criteria"], parents, strict=True) if p["level"] is not None)
    weight = sum(r["weight"] for r, p in zip(rubric["criteria"], parents, strict=True) if p["level"] is not None)
    score = (Decimal(100 * numerator) / Decimal(4 * weight)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    # Only present leaves need full counts. Inactive IDs explicitly mean no expected occurrence.
    compact = [[r["criterionId"], r["sampleCount"], [r[s] for s in STATES], r["level"]]
               for r in leaves if r["sampleCount"]]
    payload = {"RUBRIC_JSON": {"rubricRevision": REVISION,
                    "parents": [[r["id"], r["weight"]] for r in rubric["criteria"]],
                    "leavesByParent": {p["id"]: [[r["id"], r["phone"]] for r in rubric["phoneCriteria"] if r["parentId"] == p["id"]]
                                       for p in rubric["criteria"] if p["phones"]},
                    "visual": [[r["id"], r["parentId"]] for r in rubric["visualCriteria"]],
                    "levels": rubric["levels"], "evidenceUnits": rubric["evidenceUnits"],
                    "aggregation": rubric["aggregation"]},
               "INPUT_DATA": {"audioColumns": ["criterionId", "sampleCount", "counts", "level"], "audio": compact,
                    "notApplicable": [r["criterionId"] for r in leaves if not r["sampleCount"]],
                    "visual": {k: v for k, v in visual.items() if k != "supplementSha256"}},
               "RUBRIC_RESULT": {"overallScore": float(score),
                    "parents": [[r["criterionId"], r["level"]] for r in parents]}}
    body = dict(model=scorer.model_id, messages=[{"role": "system", "content": system},
                  {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}],
                max_tokens=512, temperature=0, chat_template_kwargs={"thinking": False})
    request = Request(scorer.endpoint, data=json.dumps(body, ensure_ascii=False).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=scorer.timeout) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError("oversized completion")
    document = _strict_json(raw.decode("utf-8"))
    if document["choices"][0].get("finish_reason") != "stop":
        raise ValueError("incomplete completion")
    result = _score_document(_completion_content(document, expected_model=scorer.model_id, expected_revision=scorer.revision))
    if not isinstance(result, dict) or set(result) != {"rubricRevision", "overallScore", "attentionCriterionIds"}:
        raise ValueError("invalid hierarchy response")
    actual, attention = result["overallScore"], result["attentionCriterionIds"]
    allowed = {r["criterionId"] for r in leaves if r["level"] is not None and r["level"] < 4}
    if (result["rubricRevision"] != REVISION or type(actual) not in (int, float) or not math.isfinite(actual)
            or Decimal(str(actual)) != score or not isinstance(attention, list) or len(attention) > 3
            or any(not isinstance(i, str) or i not in allowed for i in attention)
            or len(set(attention)) != len(attention) or (allowed and not attention)):
        raise ValueError("invalid hierarchy review")
    evidence = dict(rubricRevision=REVISION, generator="hyperclova", modelRevision=scorer.revision,
                    evidenceSha256=hashlib.sha256(json.dumps(analysis.analysis_evidence, ensure_ascii=False,
                        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
                    rubricSha256=rubric_digest, promptSha256=prompt_digest, criteria=parents,
                    phoneCriteria=leaves, visualObservation=visual, attentionCriterionIds=attention, **counts)
    return float(score), evidence
