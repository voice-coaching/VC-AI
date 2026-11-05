"""Fixed phone grade + five Gaussian-shaped reference-proximity scores.

No audio inference, inferred phone states, percentile-as-quality conversion or
provider calls. The original nine-category calculator remains authoritative.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP, localcontext
import json
import re

from . import phone_rubric
from .policy import CRITERIA, METRICS, encode, number, require, sha, strict_json

REVISION = "announcer-composite-exponential-v2"
PHONE_REVISION = "phone-rubric-20261003-v1"
MAPPING = {
    "speaking_rate": ("speech_rate",),
    "articulation_rate": ("articulation_rate",),
    "pauses": ("pause_count_per_10_syllables", "pause_duration_per_10_syllables"),
    "pitch": ("f0_range_st",),
    "intensity": ("intensity_range_db",),
}


def validate_composite_policy(policy: dict) -> None:
    require(type(policy) is dict and policy.get("revision") == REVISION,
            "COMPOSITE_REVISION")
    require(policy.get("validationStatus") == "EXPERT_REVIEW_PENDING"
            and policy.get("scoreMeaning") == "FIXED_PHONE_RUBRIC_AND_PROSODY_REFERENCE_PROXIMITY",
            "COMPOSITE_SCOPE")
    require(policy.get("formula") == "100 * exp(-lambda * ((userValue - mean) / std) ** 2)",
            "COMPOSITE_FORMULA")
    coefficients = policy["lambdaByMetric"]
    require(set(coefficients) == set(METRICS)
            and all(number(v) and v > 0 for v in coefficients.values()), "COMPOSITE_LAMBDA")
    weights = policy["weights"]
    require(set(weights) == set(CRITERIA) and all(type(v) is int and v > 0 for v in weights.values())
            and sum(weights.values()) == 100, "COMPOSITE_WEIGHTS")
    pauses = policy["pauseWeights"]
    require(set(pauses) == set(MAPPING["pauses"])
            and all(number(v) and v > 0 for v in pauses.values())
            and sum(Decimal(str(v)) for v in pauses.values()) == 1, "COMPOSITE_PAUSE_WEIGHTS")
    require(policy["pronunciation"] == dict(source="RUBRIC_RESULT.overallScore",
            rubricRevision=PHONE_REVISION, mode="COPY_FIXED_SERVER_GRADE",
            additionalGopMeanPenalty=False), "COMPOSITE_PRONUNCIATION_CONTRACT")
    require(policy["rounding"] == dict(mode="HALF_UP", decimalPlaces=1,
            intermediateProsodyRounding=False, pronunciationUsesExistingFinalGrade=True)
            and policy["missingDimension"] == "UNSCORABLE_TOTAL_NO_WEIGHT_REDISTRIBUTION"
            and policy["selection"] == "LOWEST_DISPLAYED_SCORE_THEN_CRITERIA_ORDER"
            and policy["feedbackCount"] == 3, "COMPOSITE_AGGREGATION")
    reference = policy["reference"]
    require(reference["version"] == "aihub71557_prosody_reference_v1"
            and reference["metricVersion"] == "aihub71557_compatible_metrics_v2"
            and reference["analysisWindowSource"] == "mfa_speech_bounds"
            and reference["referenceWindowSource"] == "aihub_json_utterance_bounds"
            and reference["extractorCompatibility"] == "aihub71557_parity_fixture_v1"
            and reference["group"] in ("현직아나운서", "전직아나운서", "아나운서준비생", "전체")
            and re.fullmatch(r"[0-9a-f]{64}", reference["fileSha256"]) is not None
            and isinstance(reference["aggregation"], str) and bool(reference["aggregation"]),
            "COMPOSITE_REFERENCE_IDENTITY")
    require(set(reference["statistics"]) == set(METRICS), "COMPOSITE_REFERENCE_METRICS")
    for stats in reference["statistics"].values():
        require(type(stats) is dict and set(stats) == {"n", "mean", "std", "p10", "p25", "p50", "p75", "p90"}
                and type(stats["n"]) is int and stats["n"] >= 0
                and (stats["mean"] is None or number(stats["mean"]))
                and (stats["std"] is None or number(stats["std"]) and stats["std"] >= 0)
                and all(number(stats[k]) for k in ("p10", "p25", "p50", "p75", "p90"))
                and stats["p10"] <= stats["p25"] <= stats["p50"] <= stats["p75"] <= stats["p90"],
                "COMPOSITE_REFERENCE_STATISTICS")
    require(isinstance(policy["limitations"], list) and bool(policy["limitations"])
            and all(isinstance(v, str) and bool(v.strip()) for v in policy["limitations"]),
            "COMPOSITE_LIMITATIONS")


def load_composite_policy(path) -> tuple[dict, str]:
    raw = path.read_bytes()
    require(0 < len(raw) <= 131072, "COMPOSITE_POLICY_SIZE")
    policy = strict_json(raw)
    validate_composite_policy(policy)
    return policy, sha(raw)


def validate_reference_asset(policy: dict, raw: bytes) -> None:
    """Check that the embedded means/stds really came from the pinned asset."""
    require(type(raw) is bytes and sha(raw) == policy["reference"]["fileSha256"],
            "COMPOSITE_REFERENCE_ASSET_DIGEST")
    asset = strict_json(raw)
    reference = policy["reference"]
    require(asset["aggregation"] == reference["aggregation"]
            and all(asset["groups"][reference["group"]][key] == stats
                    for key, stats in reference["statistics"].items()), "COMPOSITE_REFERENCE_ASSET_STATISTICS")


def validate_worksheet(envelope: dict, *, binding: dict, rubric: dict) -> dict:
    """Validate a server/operator-owned worksheet bound to this exact attempt.

    Binding proves association, not authentication or human correctness. This
    function does not expose a public endpoint accepting client-supplied scores.
    """
    require(type(envelope) is dict and set(envelope) == {
        "association", "RUBRIC_JSON", "INPUT_DATA", "RUBRIC_RESULT"}, "RUBRIC_WORKSHEET_SHAPE")
    require(envelope["association"] == binding, "RUBRIC_WORKSHEET_BINDING")
    phone_rubric.validate_policy(rubric)
    rows = envelope["INPUT_DATA"]["criteria"]
    require(type(rows) is list and len(rows) == 9, "RUBRIC_WORKSHEET_CRITERIA")
    keys = ("criterionId", "sampleCount", "matchedClear", "matchedFlagged", "substitutedClear",
            "substitutedFlagged", "deletedClear", "deletedFlagged")
    counts = [{key: row[key] for key in keys} for row in rows]
    expected = phone_rubric.worksheet(counts, evidence_units=rubric["evidenceUnits"],
                                     revision=rubric["rubricRevision"])
    for key in ("RUBRIC_JSON", "INPUT_DATA"):
        require(json.dumps(envelope[key], sort_keys=True, allow_nan=False)
                == json.dumps(expected[key], sort_keys=True, allow_nan=False), "RUBRIC_WORKSHEET_CHANGED")
    phone_rubric.validate_response(envelope["RUBRIC_RESULT"], expected["RUBRIC_RESULT"])
    return expected


def _display(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def exponential_score(value, mean, std, coefficient) -> tuple[Decimal, dict]:
    """Return an unrounded score and finite, serializable arithmetic evidence."""
    require(number(value) and number(mean), "COMPOSITE_METRIC_OR_MEAN_UNAVAILABLE")
    require(number(std) and std > 0, "COMPOSITE_STD_UNAVAILABLE_OR_ZERO")
    require(number(coefficient) and coefficient > 0, "COMPOSITE_LAMBDA")
    with localcontext() as context:
        context.prec = 50
        delta = Decimal(str(value)) - Decimal(str(mean))
        squared = delta * delta
        normalized = squared / (Decimal(str(std)) ** 2)
        score = Decimal(100) * (-Decimal(str(coefficient)) * normalized).exp()
        # Decimal strings preserve huge but finite squared distances; JSON
        # floats could overflow to Infinity for otherwise finite inputs.
        return score, dict(userValue=value, mean=mean, std=std, coefficient=coefficient,
                           squaredDeviation=str(squared), normalizedSquaredDeviation=str(normalized),
                           unroundedScore=str(score), score=_display(score))


def _reference_failure(core: dict, policy: dict) -> str | None:
    reference = policy["reference"]
    actual = (core.get("core_result", {}).get("prosody") or {}).get("aihub71557_reference_comparison_v1")
    if not isinstance(actual, dict):
        return "COMPOSITE_REFERENCE_UNAVAILABLE"
    fields = {"reference_version": "version", "reference_group": "group",
              "reference_file_sha256": "fileSha256", "reference_aggregation": "aggregation",
              "metric_version": "metricVersion", "analysis_window_source": "analysisWindowSource",
              "reference_window_source": "referenceWindowSource", "extractor_compatibility": "extractorCompatibility"}
    if any(actual.get(key) != reference[value] for key, value in fields.items()):
        return "COMPOSITE_REFERENCE_MISMATCH"
    rows = actual.get("metrics")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return "COMPOSITE_REFERENCE_METRICS_UNAVAILABLE"
    names = [row.get("metric_name") for row in rows]
    if not all(isinstance(name, str) for name in names):
        return "COMPOSITE_REFERENCE_METRICS_UNAVAILABLE"
    if len(names) != len(set(names)):
        return "COMPOSITE_REFERENCE_DUPLICATE_METRIC"
    return None


def composite_assessment(base: dict, core: dict, scoring: dict, policy: dict) -> dict:
    """Replace v1 temporary bands with the selected v2 rules, retaining evidence."""
    validate_composite_policy(policy)
    result = deepcopy(base)
    result.update(policyRevision=policy["revision"], policySha256=sha(encode(policy)))
    result["limitations"] = list(policy["limitations"])
    grade = scoring["RUBRIC_RESULT"]
    accuracy = result["criteria"][0]
    unrounded, worksheet = {}, []
    accuracy.update(score=None, status="UNSCORABLE", reasonCode="PHONE_RUBRIC_UNSCORABLE",
                    scope="FIXED_NINE_CATEGORY_SERVER_RUBRIC", components=[])
    if "RUBRIC_EVIDENCE" in scoring:
        accuracy["reasonCode"] = scoring["RUBRIC_EVIDENCE"]["reasonCode"]
    accepted = core.get("input_validation", {}).get("status") == "ACCEPT"
    if not accepted:
        accuracy["reasonCode"] = "INPUT_NOT_ACCEPTED"
    if accepted and grade != {"status": "unscorable"}:
        require(grade.get("rubricRevision") == policy["pronunciation"]["rubricRevision"]
                and number(grade.get("overallScore")) and 0 <= grade["overallScore"] <= 100,
                "COMPOSITE_PHONE_GRADE")
        accuracy.update(score=grade["overallScore"], status="SCORED", reasonCode="FIXED_SERVER_RUBRIC")
        unrounded["pronunciation_accuracy"] = Decimal(str(grade["overallScore"]))
        for row, definition in zip(grade["criteria"], scoring["RUBRIC_JSON"]["criteria"], strict=True):
            if row["level"] is None:
                continue
            evidence_id = "rubric:" + row["criterionId"]
            result["evidence"].append(dict(evidenceId=evidence_id, criterionId="pronunciation_accuracy",
                source="RUBRIC_RESULT.criteria", unit="rubric_level", value=row["level"]))
            accuracy["evidenceIds"].append(evidence_id)
            accuracy["components"].append(dict(metric=row["criterionId"], value=row["level"],
                unit="rubric_level", score=25 * row["level"], weight=definition["weight"],
                direction="FIXED_SERVER_RUBRIC"))
    reference_failure = _reference_failure(core, policy)
    prosody = core.get("core_result", {}).get("prosody") or {}
    comparison = prosody.get("aihub71557_reference_comparison_v1") or {}
    rows = comparison.get("metrics", [])
    comparisons = {row["metric_name"]: row for row in rows
                   if isinstance(row, dict) and isinstance(row.get("metric_name"), str)} if isinstance(rows, list) else {}
    metrics = (prosody.get("aihub71557_compatible_metrics_v2") or {}).get("metrics") or {}
    with localcontext() as context:
        context.prec = 50
        for item in result["criteria"][1:]:
            key = item["criterionId"]
            failure = item["reasonCode"] if item["status"] != "SCORED" else reference_failure
            item.update(score=None, status="UNSCORABLE", scope="PROSODY_REFERENCE_PROXIMITY", components=[])
            parts, total = [], Decimal(0)
            if not failure:
                for metric in MAPPING[key]:
                    stats = policy["reference"]["statistics"][metric]
                    row, value = comparisons.get(metric), metrics.get(metric)
                    if (not isinstance(row, dict) or row.get("user_value") != value
                            or row.get("reference_n") != stats["n"]
                            or any(row.get("reference_"+p) != stats[p] for p in ("p10", "p25", "p50", "p75", "p90"))):
                        failure = "COMPOSITE_REFERENCE_METRIC_MISMATCH"
                        break
                    if stats["n"] < 2:
                        failure = "COMPOSITE_REFERENCE_TOO_FEW_SPEAKERS"
                        break
                    try:
                        score, evidence = exponential_score(value, stats["mean"], stats["std"], policy["lambdaByMetric"][metric])
                    except ValueError as exc:
                        failure = str(exc)
                        break
                    weight = policy["pauseWeights"][metric] if key == "pauses" else 1
                    total += Decimal(str(weight)) * score
                    worksheet.append(dict(criterionId=key, metric=metric, referenceN=stats["n"], weight=weight, **evidence))
                    direction = "BELOW_REFERENCE_MEAN" if value < stats["mean"] else "ABOVE_REFERENCE_MEAN" if value > stats["mean"] else "AT_REFERENCE_MEAN"
                    parts.append(dict(metric=metric, value=value, unit=next(e["unit"] for e in result["evidence"] if e["evidenceId"] == "prosody:"+metric),
                                      score=_display(score), weight=weight, direction=direction))
            if failure:
                item["reasonCode"] = failure
            else:
                unrounded[key] = total
                item.update(score=_display(total), status="SCORED", reasonCode="EXPONENTIAL_REFERENCE_PROXIMITY", components=parts)
        missing = [key for key in CRITERIA if key not in unrounded]
        overall = None if missing else _display(sum(Decimal(policy["weights"][key]) * unrounded[key] for key in CRITERIA) / 100)
    available = [item for item in result["criteria"] if item["status"] == "SCORED"]
    selected = sorted(available, key=lambda item: (item["score"], CRITERIA.index(item["criterionId"])))[:3]
    status = "READY" if not missing else "PARTIAL" if available else "UNSCORABLE"
    result.update(status=status, selectedCriterionIds=[item["criterionId"] for item in selected])
    summary = dict(rubricRevision=REVISION, validationStatus=policy["validationStatus"],
        scoreMeaning=policy["scoreMeaning"], status=status, overallScore=overall,
        criteria=[dict(criterionId=item["criterionId"], score=item["score"], weight=policy["weights"][item["criterionId"]]) for item in result["criteria"]],
        missingCriterionIds=missing, selectedCriterionIds=result["selectedCriterionIds"],
        policySha256=sha(encode(policy)), referenceSha256=policy["reference"]["fileSha256"])
    result["auxiliary"]["composite"] = dict(result=summary, worksheet=worksheet)
    return result
