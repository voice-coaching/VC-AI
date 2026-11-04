"""Project existing CORE evidence and compute six provisional, reproducible scores.

No acoustic rerun, logits export, reference percentile-as-quality assumption, or
LLM grading. Original acoustic bytes remain the authoritative evidence.
"""
from __future__ import annotations

import re

from .policy import CRITERIA, band_score, gop_score, number, require, rounded, sha, strict_json


def validate_binding(core_bytes: bytes, script: str, binding: dict) -> dict:
    require(type(core_bytes) is bytes and 0 < len(core_bytes) <= 32*1024*1024, "CORE_SIZE")
    require(isinstance(script, str) and 1 <= len(script.strip()) <= 500, "SCRIPT_SIZE")
    core = strict_json(core_bytes)
    require(isinstance(core, dict), "CORE_DOCUMENT")
    require(binding["coreSha256"] == sha(core_bytes), "CORE_BINDING")
    require(binding["scriptSha256"] == sha(script.encode("utf-8")), "SCRIPT_BINDING")
    for field, source in (("requestId", "request_id"), ("sourceAudioSha256", "source_audio_sha256"),
                          ("preparedWavSha256", "prepared_wav_sha256")):
        require(isinstance(binding[field], str) and bool(binding[field])
                and binding[field] == core.get(source), "MEDIA_BINDING")
    for field in ("coreSha256", "scriptSha256", "sourceAudioSha256", "preparedWavSha256"):
        require(re.fullmatch(r"[0-9a-f]{64}", binding[field]) is not None, "DIGEST_FORMAT")
    return core


def _criterion(key: str, policy: dict, *, score=None, reason: str,
               evidence=None, components=None) -> dict:
    return dict(criterionId=key, label=policy["labels"][key], scope=policy["scopes"][key],
                status="SCORED" if score is not None else "UNSCORABLE",
                score=rounded(score) if score is not None else None, reasonCode=reason,
                evidenceIds=list(evidence or []), components=list(components or []))


def _pronunciation(core: dict, policy: dict, evidence: list) -> dict:
    key = "pronunciation_accuracy"
    assessment = core.get("phonemeAssessment") or {}
    samples = assessment.get("samples")
    if assessment.get("revision") != "gop-ctc-af-sd-native-v1" or not isinstance(samples, list) or not samples:
        return _criterion(key, policy, reason="GOP_UNAVAILABLE")
    groups = policy["pronunciation"]["groups"]
    phone_group = {phone: group["id"] for group in groups for phone in group["phones"]}
    scores = {group["id"]: [] for group in groups}
    seen, expected, ids = set(), {group["id"]: 0 for group in groups}, []
    for sample in samples:
        require(isinstance(sample, dict) and type(sample.get("index")) is int
                and sample["index"] >= 0 and sample["index"] not in seen, "GOP_INDEX")
        seen.add(sample["index"])
        group = phone_group.get(sample.get("phone"))
        require(group is not None and sample.get("role") in ("onset", "nucleus", "coda"), "GOP_PHONE")
        require(not (sample["phone"] == "ㅇ" and sample["role"] == "onset"), "GOP_SILENT_ONSET")
        expected[group] += 1
        value = sample.get("gop")
        if not number(value) or value > 0:
            continue
        reference = "gop:" + str(sample["index"])
        evidence.append(dict(evidenceId=reference, criterionId=key, source="phonemeAssessment.samples",
                             unit="log_ratio", value=value,
                             location={name: sample.get(name) for name in
                                       ("index", "sourceIndex", "wordIndex", "syllableIndex", "phone", "role")},
                             bestAlternative=sample.get("bestAlternative"),
                             indistinguishablePhones=sample.get("indistinguishablePhones")))
        ids.append(reference)
        scores[group].append(gop_score(value, policy["pronunciation"]["gopScoreKnots"]))
    minimum = policy["quality"]["minimumGopValidRatio"]
    if any(expected[g] and len(scores[g])/expected[g] < minimum for g in expected):
        return _criterion(key, policy, reason="GOP_INSUFFICIENT_VALID_SAMPLES", evidence=ids)
    components, numerator, denominator = [], 0.0, 0.0
    for group in groups:
        values = scores[group["id"]]
        if not values:
            continue
        mean = sum(values)/len(values)
        components.append(dict(metric=group["id"], value=rounded(mean), unit="provisional_score",
                               score=rounded(mean), weight=group["weight"], direction="MODEL_SUPPORT",
                               validSampleCount=len(values), expectedSampleCount=expected[group["id"]]))
        numerator += mean*group["weight"]
        denominator += group["weight"]
    if not denominator:
        return _criterion(key, policy, reason="GOP_UNAVAILABLE")
    return _criterion(key, policy, score=numerator/denominator, reason="PROVISIONAL_GOP_MAPPING",
                      evidence=ids, components=components)


def assess(core: dict, script: str, binding: dict, policy: dict, policy_sha: str) -> dict:
    evidence, criteria = [], []
    accepted = core.get("input_validation", {}).get("status") == "ACCEPT"
    if accepted:
        criteria.append(_pronunciation(core, policy, evidence))
    else:
        criteria.append(_criterion(CRITERIA[0], policy, reason="INPUT_NOT_ACCEPTED"))
    prosody = core.get("core_result", {}).get("prosody") or {}
    observation = prosody.get("aihub71557_compatible_metrics_v2") or {}
    metrics = observation.get("metrics") or {}
    start, end = observation.get("analysis_start_s"), observation.get("analysis_end_s")
    duration = end-start if number(start) and number(end) and 0 <= start < end else None
    quality = policy["quality"]
    reason = None
    if not accepted:
        reason = "INPUT_NOT_ACCEPTED"
    elif metrics.get("status") != "ok" or metrics.get("metric_version") != "aihub71557_compatible_metrics_v2":
        reason = "PROSODY_V2_UNAVAILABLE"
    elif len(re.findall(r"[가-힣]", script)) < quality["minimumSyllables"]:
        reason = "SCRIPT_TOO_SHORT"
    elif duration is None or duration < quality["minimumDurationSeconds"]:
        reason = "ANALYSIS_WINDOW_TOO_SHORT_OR_MISSING"
    mapping = {
        "speaking_rate": {"speech_rate": 1.0},
        "articulation_rate": {"articulation_rate": 1.0},
        "pauses": policy["pauseWeights"],
        "pitch": {"f0_range_st": 1.0},
        "intensity": {"intensity_range_db": 1.0},
    }
    for key, weights in mapping.items():
        failure = reason
        if key == "pitch" and failure is None:
            coverage = metrics.get("f0_coverage")
            if not number(coverage) or not quality["minimumF0Coverage"] <= coverage <= 1:
                failure = "F0_INSUFFICIENT_VALID_FRAMES"
        if failure:
            criteria.append(_criterion(key, policy, reason=failure))
            continue
        parts, ids, total = [], [], 0.0
        for metric, weight in weights.items():
            value = metrics.get(metric)
            if not number(value) or value < 0 or (metric in ("speech_rate", "articulation_rate") and value == 0):
                failure = "METRIC_UNAVAILABLE"
                break
            band = policy["bands"][metric]
            score, direction = band_score(value, band)
            reference = "prosody:" + metric
            evidence.append(dict(evidenceId=reference, criterionId=key,
                                 source="core_result.prosody.aihub71557_compatible_metrics_v2.metrics",
                                 metric=metric, value=value, unit=band["unit"],
                                 startSeconds=start, endSeconds=end))
            ids.append(reference)
            parts.append(dict(metric=metric, value=value, unit=band["unit"], score=rounded(score),
                              weight=weight, direction=direction, target=band["target"]))
            total += score*weight
        criteria.append(_criterion(key, policy, score=None if failure else total/sum(weights.values()),
                                   reason=failure or "PROVISIONAL_TARGET_BAND", evidence=ids, components=parts))
    order = {key: index for index, key in enumerate(CRITERIA)}
    available = [c for c in criteria if c["status"] == "SCORED"]
    selected = sorted(available, key=lambda c: (c["score"], order[c["criterionId"]]))[:policy["feedbackCount"]]
    return dict(schemaVersion="voice-coaching.six-dimension-assessment.v1",
                status="READY" if len(available) == 6 else "PARTIAL" if available else "UNSCORABLE",
                policyRevision=policy["revision"], policySha256=policy_sha,
                validationStatus=policy["validationStatus"], association=binding,
                criteria=criteria, selectedCriterionIds=[c["criterionId"] for c in selected],
                evidence=evidence, limitations=policy["limitations"],
                dependencies=[dict(criterionIds=["speaking_rate", "articulation_rate", "pauses"],
                                   reasonCode="SHARED_TIMING_AND_PAUSE_EVIDENCE")],
                auxiliary=dict(coverage=(core.get("phonemeAssessment") or {}).get("coverage"),
                               alignmentAmbiguous=(core.get("phonemeAssessment") or {}).get("alignmentAmbiguous"),
                               f0Coverage=metrics.get("f0_coverage"),
                               referenceComparison=prosody.get("aihub71557_reference_comparison_v1")))
