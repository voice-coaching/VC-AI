"""Versioned acoustic rules. A draft or unsupported rule produces UNKNOWN."""
from __future__ import annotations

import hashlib
import json
import math
import re

from .native_phone_contract import (
    ASSESSMENT_REVISION, FEATURE_REVISION, INVENTORY_REVISION, MODEL_REVISION,
    MODEL_WEIGHTS, TOKENS, UNVERIFIED_VOWELS, VERIFIED_PHONES,
)
from .phone_rubric import CRITERIA, require


def sha256_json(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def digest(value):
    return type(value) is str and re.fullmatch("[0-9a-f]{64}", value) is not None


def policy_contract(policy, *, approved=False):
    require(type(policy) is dict and set(policy) == {
        "assessmentRevision", "inventoryRevision", "featureRevision", "modelRevision",
        "modelWeightsSha256", "status", "mappingApprovals", "rules", "validation"}, "NATIVE_POLICY_SHAPE")
    require(policy["assessmentRevision"] == ASSESSMENT_REVISION
            and policy["inventoryRevision"] == INVENTORY_REVISION
            and policy["featureRevision"] == FEATURE_REVISION
            and policy["modelRevision"] == MODEL_REVISION
            and policy["modelWeightsSha256"] == MODEL_WEIGHTS
            and policy["status"] in {"DRAFT", "APPROVED"}, "NATIVE_POLICY_IDENTITY")
    mappings = dict(VERIFIED_PHONES)
    approvals = policy["mappingApprovals"]
    require(type(approvals) is list and len(approvals) <= 3, "NATIVE_MAPPING_APPROVALS")
    seen = set()
    for row in approvals:
        require(type(row) is dict and set(row) == {"tokenId", "phones", "labelInventorySha256", "annotationSha256"},
                "NATIVE_MAPPING_APPROVAL")
        token = row["tokenId"]
        require(type(token) is int and token in UNVERIFIED_VOWELS and token not in seen
                and row["phones"] == UNVERIFIED_VOWELS[token]
                and digest(row["labelInventorySha256"]) and digest(row["annotationSha256"]), "NATIVE_MAPPING_APPROVAL")
        seen.add(token)
        mappings[token] = row["phones"]
    rules = policy["rules"]
    require(type(rules) is list and len(rules) <= 72, "NATIVE_ASSESSMENT_RULES")
    keys, ids = set(), set()
    for rule in rules:
        require(type(rule) is dict and set(rule) == {"ruleId", "criterionId", "role", "operation", "threshold",
                                                   "minimumFrames", "calibrationSha256", "heldOutSha256"},
                "NATIVE_ASSESSMENT_RULE")
        key = rule["criterionId"], rule["role"], rule["operation"]
        require(type(rule["ruleId"]) is str and bool(re.fullmatch("[a-z0-9_-]{1,80}", rule["ruleId"]))
                and rule["ruleId"] not in ids and key not in keys
                and key[0] in {k for k, _, _ in CRITERIA[:-1]}
                and key[1] in {"onset", "nucleus", "coda"}
                and ((key[0] == "vowels") == (key[1] == "nucleus"))
                and key[2] in {"MATCH", "SUBSTITUTION", "DELETION_CANDIDATE"}
                and type(rule["threshold"]) in (int, float) and math.isfinite(rule["threshold"])
                and type(rule["minimumFrames"]) is int and 1 <= rule["minimumFrames"] <= 30000
                and digest(rule["calibrationSha256"]) and digest(rule["heldOutSha256"]), "NATIVE_ASSESSMENT_RULE")
        keys.add(key)
        ids.add(rule["ruleId"])
    if policy["status"] == "APPROVED" or approved:
        require(policy["status"] == "APPROVED" and seen == set(UNVERIFIED_VOWELS)
                and bool(rules), "NATIVE_POLICY_UNCALIBRATED")
        validation = policy["validation"]
        require(type(validation) is dict and set(validation) == {
            "acceptanceCriteriaSha256", "heldOutReportSha256", "reviewSha256"}
            and all(digest(v) for v in validation.values()), "NATIVE_POLICY_VALIDATION_MISSING")
    else:
        require(policy["validation"] is None, "NATIVE_DRAFT_VALIDATION")
    return mappings


def validate_vocabulary(vocabulary):
    require(type(vocabulary) is dict and vocabulary == {token: i for i, token in enumerate(TOKENS)}
            and all(type(v) is int for v in vocabulary.values()), "NATIVE_VOCABULARY_MISMATCH")
    return sha256_json(vocabulary)


def margin_features(logits, expected_ids, start, end):
    """Reuse CPU FP32 logits; float32 max/subtraction/mean, no second forward."""
    import numpy as np

    require(type(start) is int and type(end) is int and 0 <= start < end <= len(logits)
            and bool(expected_ids), "NATIVE_FRAME_REGION")
    competitors = sorted(set(range(40)) - set(expected_ids))
    require(bool(competitors), "NATIVE_COMPETITOR_CLASS")
    window = logits[start:end]
    target = window[:, expected_ids].max(axis=1)
    competitor = window[:, competitors].max(axis=1)
    margins = competitor - target
    value = float(np.mean(margins, dtype=np.float32))
    require(math.isfinite(value), "NATIVE_NONFINITE_FEATURE")
    return dict(definition=FEATURE_REVISION, startFrame=start, endFrame=end,
                expectedClassIds=list(expected_ids), competitorClassIds=competitors,
                margin=value, frameCount=end-start,
                logitsWindowSha256=hashlib.sha256(window.astype("<f4", copy=False).tobytes(order="C")).hexdigest())


def assess(phone, position, features, policy):
    if position["reasonCodes"]:
        return "UNKNOWN", None, list(position["reasonCodes"])
    if policy["status"] != "APPROVED":
        return "UNKNOWN", None, ["ASSESSMENT_POLICY_UNCALIBRATED"]
    if features is None:
        return "UNKNOWN", None, ["FRAME_REGION_UNAVAILABLE"]
    rule = next((row for row in policy["rules"] if
                 (row["criterionId"], row["role"], row["operation"]) ==
                 (phone.criterion, phone.role, position["alignmentOperation"])), None)
    if rule is None or features["frameCount"] < rule["minimumFrames"]:
        return "UNKNOWN", None, ["ASSESSMENT_RULE_UNSUPPORTED"]
    flagged = features["margin"] >= rule["threshold"]
    if position["alignmentOperation"] == "DELETION_CANDIDATE":
        return ("DELETED", rule["ruleId"], []) if flagged else ("UNKNOWN", rule["ruleId"], ["DELETION_UNRESOLVED"])
    return "FLAGGED" if flagged else "CLEAR", rule["ruleId"], []
