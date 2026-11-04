"""Strict, editable provisional policy; no provider or acoustic imports."""
from __future__ import annotations

import hashlib
import json
import math
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

CRITERIA = ("pronunciation_accuracy", "speaking_rate", "articulation_rate", "pauses", "pitch", "intensity")
METRICS = ("speech_rate", "articulation_rate", "pause_count_per_10_syllables",
           "pause_duration_per_10_syllables", "f0_range_st", "intensity_range_db")


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError(code)


def error_code(error: Exception) -> str:
    """Expose only our bounded symbolic codes, never arbitrary exception text."""
    value = str(error)
    return value if isinstance(error, ValueError) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", value) else type(error).__name__


def number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encode(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def strict_json(raw: bytes):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, "DUPLICATE_JSON_KEY")
            value[key] = item
        return value

    def invalid(_):
        raise ValueError("NONFINITE_JSON")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def rounded(value: float) -> float:
    require(number(value), "NONFINITE_SCORE")
    return float(Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def validate_policy(policy: dict) -> None:
    require(isinstance(policy, dict) and isinstance(policy.get("revision"), str)
            and bool(policy["revision"]), "POLICY_REVISION")
    require(policy.get("validationStatus") == "EXPERT_REVIEW_PENDING", "POLICY_REVIEW_STATUS")
    require(policy.get("task") == "KOREAN_READ_ALOUD", "POLICY_TASK")
    require(tuple(policy["criteriaOrder"]) == CRITERIA and policy["feedbackCount"] == 3,
            "POLICY_CRITERIA")
    require(set(policy["labels"]) == set(CRITERIA) and set(policy["scopes"]) == set(CRITERIA),
            "POLICY_LABELS")
    for value in [*policy["labels"].values(), *policy["scopes"].values(), *policy["limitations"]]:
        require(isinstance(value, str) and bool(value.strip()), "POLICY_TEXT")
    quality = policy["quality"]
    for key in ("minimumGopValidRatio", "minimumF0Coverage"):
        require(number(quality[key]) and 0 < quality[key] <= 1, "POLICY_QUALITY_RATIO")
    require(type(quality["minimumSyllables"]) is int and quality["minimumSyllables"] >= 1,
            "POLICY_SYLLABLES")
    require(number(quality["minimumDurationSeconds"]) and quality["minimumDurationSeconds"] > 0,
            "POLICY_DURATION")
    require(set(policy["bands"]) == set(METRICS), "POLICY_BANDS")
    for band in policy["bands"].values():
        low, high = band["target"]
        zero_low, zero_high = band["zeroAt"]
        require(all(number(v) for v in (zero_low, low, high, zero_high))
                and zero_low < low <= high < zero_high, "POLICY_BAND_ORDER")
        require(isinstance(band["unit"], str) and bool(band["unit"]), "POLICY_UNIT")
    knots = policy["pronunciation"]["gopScoreKnots"]
    require(isinstance(knots, list) and len(knots) >= 2, "POLICY_GOP_KNOTS")
    for index, (x, y) in enumerate(knots):
        require(number(x) and x <= 0 and number(y) and 0 <= y <= 100, "POLICY_GOP_KNOT")
        if index:
            require(knots[index-1][0] < x and knots[index-1][1] <= y, "POLICY_GOP_ORDER")
    require(knots[-1] == [0.0, 100], "POLICY_GOP_ENDPOINT")
    phones, group_ids = set(), set()
    for group in policy["pronunciation"]["groups"]:
        require(isinstance(group["id"], str) and group["id"] not in group_ids
                and number(group["weight"]) and group["weight"] > 0, "POLICY_PHONE_GROUP")
        group_ids.add(group["id"])
        require(isinstance(group["phones"], str) and bool(group["phones"])
                and not phones.intersection(group["phones"]), "POLICY_PHONE_OVERLAP")
        phones.update(group["phones"])
    require(bool(group_ids), "POLICY_PHONE_GROUPS_EMPTY")
    weights = policy["pauseWeights"]
    require(set(weights) == {"pause_count_per_10_syllables", "pause_duration_per_10_syllables"}
            and all(number(v) and v > 0 for v in weights.values()), "POLICY_PAUSE_WEIGHTS")
    config = policy["request"]
    require(isinstance(config["model"], str) and config["model"].startswith("gpt-")
            and config["reasoning_effort"] == "low" and config["store"] is False
            and config["response_format"] == {"type": "json_object"}
            and config["service_tier"] in ("default", "priority")
            and type(config["max_completion_tokens"]) is int and 100 <= config["max_completion_tokens"] <= 8000
            and type(config["input_utf8_bound"]) is int and 1024 <= config["input_utf8_bound"] <= 800000
            and type(config["timeout_seconds"]) is int and 1 <= config["timeout_seconds"] <= 120,
            "POLICY_REQUEST")


def load_policy(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    require(len(raw) <= 131072, "POLICY_SIZE")
    policy = strict_json(raw)
    validate_policy(policy)
    return policy, sha(raw)


def band_score(value: float, band: dict) -> tuple[float, str]:
    require(number(value) and value >= 0, "METRIC_VALUE")
    low, high = band["target"]
    zero_low, zero_high = band["zeroAt"]
    if value < low:
        return max(0.0, 100.0 * (value-zero_low)/(low-zero_low)), "BELOW_TARGET"
    if value > high:
        return max(0.0, 100.0 * (zero_high-value)/(zero_high-high)), "ABOVE_TARGET"
    return 100.0, "WITHIN_TARGET"


def gop_score(value: float, knots: list) -> float:
    require(number(value) and value <= 0, "GOP_VALUE")
    if value <= knots[0][0]:
        return float(knots[0][1])
    for (x0, y0), (x1, y1) in zip(knots, knots[1:]):
        if value <= x1:
            return y0 + (y1-y0)*(value-x0)/(x1-x0)
    return float(knots[-1][1])
