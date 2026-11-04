"""Six fixed scores -> deterministic lowest three -> one grounded GPT request.

The detailed result is private; service adapters project the existing feedback
contract and keep the six scores in retained analysis artifacts.
"""
from __future__ import annotations

import re

import jsonschema

from .assessment import assess, validate_binding
from .policy import encode, error_code, number, require, sha, strict_json, validate_policy
from . import phone_rubric

MAX_FEEDBACK_LINES = 3
MAX_FEEDBACK_SENTENCES = 5
MAX_FEEDBACK_LINE_CHARACTERS = 100
MAX_FEEDBACK_CHARACTERS = 300
MAX_PRACTICE_CHARACTERS = MAX_FEEDBACK_LINE_CHARACTERS - len("* ")
_TERMINATOR = re.compile(r"[.!?。！？]+(?!\d)")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_INTERNAL_MEASUREMENT = re.compile(r"\b(?:GOP|CTC|F0)\b|\d\s*(?:dB|Hz|음절\s*/\s*초)|초당\s*\d", re.IGNORECASE)


def _sentence_count(value: str) -> int:
    # Count a final unpunctuated instruction as a sentence too. Decimal points
    # are not sentence boundaries. This is a format check, not a semantic proof.
    ends = list(_TERMINATOR.finditer(value))
    return len(ends) + int(bool(value[ends[-1].end() if ends else 0:].strip(" \"'’”)]}")))


def validate_public_feedback(value: str) -> None:
    require(isinstance(value, str) and 0 < len(value) <= MAX_FEEDBACK_CHARACTERS, "FEEDBACK_TOTAL_LENGTH")
    lines = value.split("\n")
    require(1 <= len(lines) <= MAX_FEEDBACK_LINES, "FEEDBACK_LINE_LIMIT")
    require(all(line == line.strip() and len(line) <= MAX_FEEDBACK_LINE_CHARACTERS
                and not _CONTROL.search(line) for line in lines), "FEEDBACK_LINE_FORMAT")
    counts = [_sentence_count(line) for line in lines]
    require(all(count == 1 for count in counts) and sum(counts) <= MAX_FEEDBACK_SENTENCES,
            "FEEDBACK_SENTENCE_LIMIT")


def public_feedback(items: list[dict]) -> str:
    """Project only coaching instructions; observations stay in private artifacts."""
    require(isinstance(items, list) and len(items) <= MAX_FEEDBACK_LINES, "FEEDBACK_LINE_LIMIT")
    if not items:
        return ""
    lines = []
    for item in items:
        practice = item["practice"]
        require(isinstance(practice, str) and practice == practice.strip()
                and not _CONTROL.search(practice) and practice.count(" → ") == 1,
                "FEEDBACK_PRACTICE_FORMAT")
        target, instruction = practice.split(" → ")
        require(bool(target.strip()) and bool(instruction.strip())
                and target == target.strip() and instruction == instruction.strip()
                and not practice.startswith(("*", "-", "•")), "FEEDBACK_PRACTICE_FORMAT")
        require(not _INTERNAL_MEASUREMENT.search(instruction), "FEEDBACK_INTERNAL_MEASUREMENT")
        require(not re.search(r"(?:세요|십시오|습니다|입니다|합니다|해요|줘요)[’”\"']*\s+\S", instruction),
                "FEEDBACK_MULTIPLE_INSTRUCTIONS")
        lines.append("* " + practice)
    result = "\n".join(lines)
    validate_public_feedback(result)
    return result


def _text(maximum: int) -> dict:
    return dict(type="string", minLength=1, maxLength=maximum, pattern="[가-힣]")


def rubric_context(core: dict, rubric: dict) -> dict:
    """Only complete measured state evidence can populate the fixed worksheet.

    GOP/alignment candidates and selected pronunciation warnings do not supply
    the calculator's six per-position observation states. Never invent them.
    """
    try:
        phone_rubric.validate_policy(rubric)
        rows = phone_rubric.evidence_rows(core.get("core_result", {}).get("pronunciation"))
        return phone_rubric.worksheet(rows, evidence_units=rubric["evidenceUnits"],
                                     revision=rubric["rubricRevision"])
    except phone_rubric.Unscorable as exc:
        return dict(RUBRIC_JSON=rubric, INPUT_DATA=None,
                    RUBRIC_RESULT={"status": "unscorable"}, SCORING_FAILURE=error_code(exc))


def coaching_schema(assessment: dict, rubric_result: dict | None = None) -> dict:
    """Provider response contract pins score, order and criterion-owned evidence."""
    summaries, feedback = [], []
    by_id = {item["criterionId"]: item for item in assessment["criteria"]}
    for item in assessment["criteria"]:
        summaries.append(dict(type="object", additionalProperties=False,
            required=["criterionId", "score", "evidenceIds", "observation"], properties={
                "criterionId": {"const": item["criterionId"]}, "score": {"const": item["score"]},
                "evidenceIds": {"const": item["evidenceIds"]}, "observation": _text(240)}))
    for key in assessment["selectedCriterionIds"]:
        feedback.append(dict(type="object", additionalProperties=False,
            required=["criterionId", "evidenceIds", "observation", "practice"], properties={
                "criterionId": {"const": key},
                "evidenceIds": dict(type="array", minItems=1, uniqueItems=True,
                                    items={"enum": by_id[key]["evidenceIds"]}),
                "observation": _text(240), "practice": _text(MAX_PRACTICE_CHARACTERS)}))
    schema = dict(type="object", additionalProperties=False, required=["assessments", "feedback"], properties={
        "assessments": dict(type="array", prefixItems=summaries, items=False, minItems=6, maxItems=6),
        "feedback": dict(type="array", prefixItems=feedback, items=False,
                         minItems=len(feedback), maxItems=len(feedback))})
    if rubric_result is not None:
        schema["required"].append("legacyGrade")
        schema["properties"]["legacyGrade"] = {"const": rubric_result}
    return schema


def request_payload(assessment: dict, core: dict, script: str, policy: dict, prompt: str,
                    legacy_rubric: dict | None = None, *, core_bytes: bytes) -> bytes:
    require(isinstance(prompt, str) and bool(prompt.strip()), "SYSTEM_PROMPT_MISSING")
    require(type(core_bytes) is bytes and sha(core_bytes) == assessment["association"]["coreSha256"],
            "RAW_CORE_IDENTITY")
    require(strict_json(core_bytes) == core, "RAW_CORE_DATA_MISMATCH")
    raw_core_text = core_bytes.decode("utf-8", errors="strict")
    require(raw_core_text.encode("utf-8") == core_bytes, "RAW_CORE_UTF8_IDENTITY")
    config = policy["request"]
    # Supplemental scoring metadata stays separate from the original CORE.
    # Never re-encode, select fields, summarize or truncate the raw JSON message.
    projected = {key: assessment[key] for key in (
        "status", "policyRevision", "validationStatus", "criteria", "selectedCriterionIds",
        "evidence", "limitations", "dependencies")}
    scoring = rubric_context(core, legacy_rubric) if legacy_rubric is not None else None
    data = dict(TASK_MODE="COACHING_WITH_SCORING", scriptText=script, ASSESSMENT=projected,
                RAW_CORE_DESCRIPTOR=dict(sha256=sha(core_bytes), utf8Bytes=len(core_bytes),
                                         association=assessment["association"]),
                RUBRIC=dict(revision=policy["revision"], validationStatus=policy["validationStatus"],
                            bands=policy["bands"], pronunciation=policy["pronunciation"],
                            scopes=policy["scopes"], pauseWeights=policy["pauseWeights"]),
                OUTPUT_SCHEMA=coaching_schema(assessment, scoring["RUBRIC_RESULT"] if scoring else None))
    if scoring is not None:
        data.update(scoring)
    messages = [dict(role="system", content=prompt),
                dict(role="user", content=encode(data).decode("utf-8")),
                dict(role="user", content=raw_core_text)]
    require(sum(len(m["content"].encode("utf-8")) for m in messages) <= config["input_utf8_bound"],
            "INPUT_BUDGET_NO_SILENT_TRUNCATION")
    payload = encode({key: config[key] for key in
                   ("model", "max_completion_tokens", "reasoning_effort", "response_format", "store", "service_tier")}
                  | {"messages": messages})
    require(len(payload) <= 1024 * 1024, "INPUT_BUDGET_NO_SILENT_TRUNCATION")
    return payload


def validate_coaching(candidate: dict, assessment: dict, rubric_result: dict | None = None) -> None:
    jsonschema.Draft202012Validator(coaching_schema(assessment, rubric_result)).validate(candidate)
    if rubric_result is not None:
        if rubric_result == {"status": "unscorable"}:
            require(candidate["legacyGrade"] == rubric_result, "SCORING_UNSCORABLE_CHANGED")
        else:
            phone_rubric.validate_response(candidate["legacyGrade"], rubric_result)
    for supplied, expected in zip(candidate["assessments"], assessment["criteria"]):
        require(supplied["score"] is None if expected["score"] is None else
                number(supplied["score"]) and supplied["score"] == expected["score"], "SCORE_CHANGED")
    # Schema verifies references/shape, not natural-language truth.
    for item in candidate["feedback"]:
        require(bool(re.search(r"[가-힣]", item["practice"].strip())), "FEEDBACK_LANGUAGE")
    public_feedback(candidate["feedback"])


def evaluate(*, core_bytes: bytes, script: str, binding: dict, policy: dict,
             policy_sha256: str, prompt: str, transport=None, measurements_only: bool = False,
             legacy_rubric: dict | None = None) -> dict:
    validate_policy(policy)
    require(re.fullmatch(r"[0-9a-f]{64}", policy_sha256) is not None, "POLICY_DIGEST")
    core = validate_binding(core_bytes, script, binding)
    assessment = assess(core, script, binding, policy, policy_sha256)
    result = dict(schemaVersion="voice-coaching.six-dimension-result.v1", assessment=assessment,
                  generationStatus="NOT_REQUESTED", dispatchAttempts=0, feedback=[], assessments=[],
                  model=policy["request"]["model"], promptSha256=sha(prompt.encode("utf-8")),
                  requestConfigSha256=sha(encode(policy["request"])), errorCode=None,
                  naturalLanguageSemanticsFullyVerified=False)
    rubric_result = rubric_context(core, legacy_rubric)["RUBRIC_RESULT"] if legacy_rubric is not None else None
    if rubric_result is not None:
        result["legacyGrade"] = rubric_result
    if measurements_only or not assessment["selectedCriterionIds"]:
        return result
    require(transport is not None, "TRANSPORT_REQUIRED")
    try:
        payload = request_payload(assessment, core, script, policy, prompt, legacy_rubric, core_bytes=core_bytes)
    except ValueError as exc:
        result.update(generationStatus="NOT_DISPATCHED", errorCode=str(exc))
        return result
    result.update(dispatchAttempts=1, requestSha256=sha(payload))
    try:
        config = policy["request"]
        raw = transport(payload, timeout=config["timeout_seconds"])
        require(type(raw) is bytes and 0 < len(raw) <= 131072, "PROVIDER_RESPONSE_SIZE")
        response = strict_json(raw)
        model = response.get("model")
        require(isinstance(model, str) and (model == config["model"] or model.startswith(config["model"]+"-")),
                "PROVIDER_MODEL")
        choices = response.get("choices")
        require(isinstance(choices, list) and len(choices) == 1, "PROVIDER_CHOICES")
        choice = choices[0]
        require(choice["finish_reason"] == "stop" and not choice["message"].get("refusal"), "PROVIDER_COMPLETION")
        candidate = strict_json(choice["message"]["content"])
        validate_coaching(candidate, assessment, rubric_result)
        result.update(generationStatus="STRUCTURALLY_VALID", **candidate)
    except Exception as exc:
        # Scores survive provider failure; never manufacture coaching or rerun audio/GPT.
        result.update(generationStatus="FAILED", errorCode=error_code(exc))
    return result
