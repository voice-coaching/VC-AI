"""Six fixed scores -> deterministic lowest three -> one grounded GPT request.

Uses a new result contract. It must not be serialized as canonical result.v6 or
registered as an old immutable H5 identity.
"""
from __future__ import annotations

import re

import jsonschema

from .assessment import assess, validate_binding
from .policy import encode, error_code, number, require, sha, strict_json, validate_policy


def _text(maximum: int) -> dict:
    return dict(type="string", minLength=1, maxLength=maximum, pattern="[가-힣]")


def coaching_schema(assessment: dict) -> dict:
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
                "observation": _text(240), "practice": _text(400)}))
    return dict(type="object", additionalProperties=False, required=["assessments", "feedback"], properties={
        "assessments": dict(type="array", prefixItems=summaries, items=False, minItems=6, maxItems=6),
        "feedback": dict(type="array", prefixItems=feedback, items=False,
                         minItems=len(feedback), maxItems=len(feedback))})


def request_payload(assessment: dict, core: dict, script: str, policy: dict, prompt: str) -> bytes:
    require(isinstance(prompt, str) and bool(prompt.strip()), "SYSTEM_PROMPT_MISSING")
    config = policy["request"]
    # Explicit projection: no audio bytes, private logits, credentials or raw errors.
    projected = {key: assessment[key] for key in (
        "status", "policyRevision", "validationStatus", "criteria", "selectedCriterionIds",
        "evidence", "limitations", "dependencies")}
    data = dict(scriptText=script, ASSESSMENT=projected,
                SEUNGUN_OUTPUT=dict(phonemeAssessment=core.get("phonemeAssessment")),
                RUBRIC=dict(revision=policy["revision"], validationStatus=policy["validationStatus"],
                            bands=policy["bands"], pronunciation=policy["pronunciation"],
                            scopes=policy["scopes"], pauseWeights=policy["pauseWeights"]),
                OUTPUT_SCHEMA=coaching_schema(assessment))
    messages = [dict(role="system", content=prompt), dict(role="user", content=encode(data).decode("utf-8"))]
    require(sum(len(m["content"].encode("utf-8")) for m in messages) <= config["input_utf8_bound"],
            "INPUT_BUDGET_NO_SILENT_TRUNCATION")
    return encode({key: config[key] for key in
                   ("model", "max_completion_tokens", "reasoning_effort", "response_format", "store", "service_tier")}
                  | {"messages": messages})


def validate_coaching(candidate: dict, assessment: dict) -> None:
    jsonschema.Draft202012Validator(coaching_schema(assessment)).validate(candidate)
    for supplied, expected in zip(candidate["assessments"], assessment["criteria"]):
        require(supplied["score"] is None if expected["score"] is None else
                number(supplied["score"]) and supplied["score"] == expected["score"], "SCORE_CHANGED")
    # Schema verifies references/shape, not natural-language truth.
    for item in candidate["feedback"]:
        require(bool(re.search(r"[가-힣]", item["practice"].strip())), "FEEDBACK_LANGUAGE")


def evaluate(*, core_bytes: bytes, script: str, binding: dict, policy: dict,
             policy_sha256: str, prompt: str, transport=None, measurements_only: bool = False) -> dict:
    validate_policy(policy)
    require(re.fullmatch(r"[0-9a-f]{64}", policy_sha256) is not None, "POLICY_DIGEST")
    core = validate_binding(core_bytes, script, binding)
    assessment = assess(core, script, binding, policy, policy_sha256)
    result = dict(schemaVersion="voice-coaching.six-dimension-result.v1", assessment=assessment,
                  generationStatus="NOT_REQUESTED", dispatchAttempts=0, feedback=[], assessments=[],
                  model=policy["request"]["model"], promptSha256=sha(prompt.encode("utf-8")),
                  requestConfigSha256=sha(encode(policy["request"])), errorCode=None,
                  naturalLanguageSemanticsFullyVerified=False)
    if measurements_only or not assessment["selectedCriterionIds"]:
        return result
    require(transport is not None, "TRANSPORT_REQUIRED")
    try:
        payload = request_payload(assessment, core, script, policy, prompt)
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
        validate_coaching(candidate, assessment)
        result.update(generationStatus="STRUCTURALLY_VALID", **candidate)
    except Exception as exc:
        # Scores survive provider failure; never manufacture coaching or rerun audio/GPT.
        result.update(generationStatus="FAILED", errorCode=error_code(exc))
    return result
