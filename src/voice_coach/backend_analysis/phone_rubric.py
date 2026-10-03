"""Server-owned phone rubric arithmetic; no provider, IO, or inferred evidence."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
import math

REVISION = "phone-rubric-20261003-v1"
STATES = ("matchedClear", "matchedFlagged", "substitutedClear", "substitutedFlagged",
          "deletedClear", "deletedFlagged")
EVIDENCE_UNITS = dict(zip(STATES, (4, 2, 3, 1, 0, 0), strict=True))
CRITERIA = (
    ("vowels", 25, "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"),
    ("plain_stops", 10, "ㄱㄷㅂ"), ("tense_stops", 10, "ㄲㄸㅃ"),
    ("aspirated_stops", 10, "ㅋㅌㅍ"), ("fricatives", 10, "ㅅㅆㅎ"),
    ("affricates", 10, "ㅈㅉㅊ"), ("nasals", 10, "ㄴㅁㅇ"),
    ("liquid", 5, "ㄹ"), ("coverage", 10, ""),
)
LEVELS = ((4, 95), (3, 80), (2, 60), (1, 40), (0, 0))


class Unscorable(ValueError):
    """A missing/inconsistent input is a failed score, never a zero or fallback."""


def require(condition, code):
    if not condition:
        raise Unscorable(code)


def evidence_rows(pronunciation):
    """Consume all Seungun expected positions; never use selected P1 candidates."""
    require(isinstance(pronunciation, dict), "SCORING_EVIDENCE_MISSING")
    require(pronunciation.get("status") in {"ok", "partial"}, "SCORING_DETECTOR_UNAVAILABLE")
    expected, phones = pronunciation.get("expected_phones"), pronunciation.get("phones")
    require(isinstance(expected, list) and 0 < len(expected) <= 4096
            and isinstance(phones, list) and len(phones) == len(expected), "SCORING_EVIDENCE_INCOMPLETE")
    supported = {phone for _, _, group in CRITERIA for phone in group}
    require(all(type(p) is str and p in supported for p in expected), "SCORING_PHONE_UNSUPPORTED")
    rows = [dict(criterionId=key, sampleCount=0, **dict.fromkeys(STATES, 0))
            for key, _, _ in CRITERIA]
    for index, phone in enumerate(phones):
        require(isinstance(phone, dict) and type(phone.get("expected_index")) is int
                and phone["expected_index"] == index and phone.get("expected_phone") == expected[index],
                "SCORING_PHONE_IDENTITY")
        operation, flagged = phone.get("alignment_operation"), phone.get("is_error")
        require(operation in {"correct", "substitution", "deletion"}
                and type(flagged) is bool, "SCORING_ASSESSMENT_MISSING")
        score, threshold = phone.get("error_score"), phone.get("operating_threshold")
        require(all(type(v) in (int, float, Decimal) and math.isfinite(v) and 0 <= v <= 1
                    for v in (score, threshold)), "SCORING_DETECTOR_EVIDENCE")
        require(flagged == (score >= threshold), "SCORING_DETECTOR_DECISION")
        state = {"correct": "matched", "substitution": "substituted", "deletion": "deleted"}[operation]
        state += "Flagged" if flagged else "Clear"
        for row, (key, _, group) in zip(rows, CRITERIA, strict=True):
            if key == "coverage" or expected[index] in group:
                row["sampleCount"] += 1
                row[state] += 1
    return rows


def worksheet(rows, *, evidence_units):
    """Explicit versioned state weights are mandatory; never infer missing units."""
    require(type(evidence_units) is dict and set(evidence_units) == set(STATES)
            and all(type(v) is int for v in evidence_units.values())
            and evidence_units == EVIDENCE_UNITS,
            "SCORING_UNITS_INVALID")
    require(type(rows) is list and len(rows) == len(CRITERIA), "SCORING_CRITERIA_MISSING")
    model_rows, grades = [], []
    weighted, applicable = 0, 0
    for row, (key, weight, _) in zip(rows, CRITERIA, strict=True):
        require(type(row) is dict and set(row) == {"criterionId", "sampleCount", *STATES}
                and row["criterionId"] == key, "SCORING_CRITERION_IDENTITY")
        require(all(type(row[k]) is int and 0 <= row[k] <= 4096 for k in ("sampleCount", *STATES))
                and sum(row[k] for k in STATES) == row["sampleCount"], "SCORING_COUNTS_INVALID")
        n = row["sampleCount"]
        units = (4 * (n - row["deletedClear"] - row["deletedFlagged"]) if key == "coverage"
                 else sum(row[state] * evidence_units[state] for state in STATES))
        thresholds = [dict(level=level, minimumUnits=(percent * 4 * n + 99) // 100)
                      for level, percent in LEVELS] if n else []
        level = next((band["level"] for band in thresholds if units >= band["minimumUnits"]), None)
        if level is not None:
            weighted += weight * level
            applicable += weight
        model_rows.append(row | dict(evidenceUnits=units, possibleUnits=4*n,
                                    minimumUnitsByLevel=thresholds))
        grades.append(dict(criterionId=key, level=level))
    require(all(sum(row[state] for row in rows[:-1]) == rows[-1][state] for state in STATES),
            "SCORING_COVERAGE_COUNTS_MISMATCH")
    require(applicable > 0, "SCORING_NO_SAMPLES")
    total = (Decimal(100 * weighted) / Decimal(4 * applicable)).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP)
    return dict(
        RUBRIC_JSON=dict(rubricRevision=REVISION, evidenceUnits=dict(evidence_units),
                        criteria=[dict(id=k, weight=w, phones=list(p)) for k, w, p in CRITERIA],
                        levels=[dict(level=l, minimumPercent=p) for l, p in LEVELS],
                        aggregation=dict(rounding="HALF_UP", decimalPlaces=1,
                                         missingCategory="not_applicable_exclude_weight")),
        INPUT_DATA=dict(criteria=model_rows, applicableWeight=applicable,
                        aggregationDenominator=4*applicable),
        RUBRIC_RESULT=dict(rubricRevision=REVISION, criteria=grades, overallScore=float(total)))


def validate_response(result, expected):
    require(type(result) is dict and set(result) == {"rubricRevision", "criteria", "overallScore"}
            and result["rubricRevision"] == REVISION, "SCORING_RESPONSE_INVALID")
    require(type(result["criteria"]) is list and len(result["criteria"]) == len(CRITERIA),
            "SCORING_RESPONSE_CRITERIA")
    for actual, wanted in zip(result["criteria"], expected["criteria"], strict=True):
        require(type(actual) is dict and set(actual) == {"criterionId", "level"}
                and (actual["level"] is None or type(actual["level"]) is int)
                and actual == wanted, "SCORING_RESPONSE_LEVEL")
    score = result["overallScore"]
    require(type(score) in (int, float, Decimal) and math.isfinite(score)
            and Decimal(str(score)) == Decimal(str(expected["overallScore"])), "SCORING_RESPONSE_TOTAL")
    return dict(expected)


def validate_policy(policy):
    require(type(policy) is dict and policy.get("rubricRevision") == REVISION
            and policy.get("evidenceUnits") == EVIDENCE_UNITS
            and all(type(v) is int for v in policy["evidenceUnits"].values()), "SCORING_POLICY_IDENTITY")
    rows = policy.get("criteria")
    require(type(rows) is list and len(rows) == len(CRITERIA), "SCORING_POLICY_CRITERIA")
    for row, (key, weight, phones) in zip(rows, CRITERIA, strict=True):
        require(type(row) is dict and row.get("id") == key and type(row.get("weight")) is int
                and row["weight"] == weight and row.get("phones") == list(phones), "SCORING_POLICY_CRITERIA")
    levels = policy.get("levels")
    require(type(levels) is list and len(levels) == len(LEVELS), "SCORING_POLICY_LEVELS")
    for row, (level, percent) in zip(levels, LEVELS, strict=True):
        require(type(row) is dict and type(row.get("level")) is int and row["level"] == level
                and type(row.get("minimumPercent")) is int and row["minimumPercent"] == percent,
                "SCORING_POLICY_LEVELS")
    require(policy.get("aggregation") == dict(missingCategory="not_applicable_exclude_weight",
            minimumPhoneCount=1, rounding="HALF_UP", decimalPlaces=1,
            formula="100 * sum(weight * level) / (4 * sum(applicable weights))"), "SCORING_POLICY_AGGREGATION")
