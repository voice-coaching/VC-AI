"""GPT renders the server worksheet; every numeric value is checked exactly."""
from __future__ import annotations

from decimal import Decimal
import json

from .phone_rubric import Unscorable, evidence_rows, require, validate_response, worksheet


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "SCORING_DUPLICATE_KEY")
            result[key] = value
        return result

    def invalid(_):
        raise Unscorable("SCORING_NONFINITE")

    return json.loads(raw, object_pairs_hook=pairs, parse_float=Decimal, parse_constant=invalid)


def score_core(core, *, evidence_units, prompt, transport):
    """Historical harness compatibility; new Native releases use native_gpt_rubric."""
    if core["input_validation"]["status"] != "ACCEPT":
        return dict(status="NOT_REQUESTED")
    try:
        sheet = worksheet(evidence_rows(core["core_result"]["pronunciation"]), evidence_units=evidence_units)
        return render_worksheet(sheet, prompt=prompt, transport=transport)
    except Exception:
        return dict(status="UNSCORABLE")


def render_worksheet(sheet, *, prompt, transport):
    """Provider IO is shared; evidence extraction belongs to each pinned harness."""
    try:
        body = dict(model="gpt-6-astra", max_completion_tokens=1200, reasoning_effort="low",
                    response_format={"type": "json_object"}, store=False,
                    messages=[dict(role="system", content=prompt),
                              dict(role="user", content=json.dumps(sheet, ensure_ascii=False, allow_nan=False))])
        raw = transport(json.dumps(body, ensure_ascii=False, allow_nan=False).encode(), timeout=120)
        require(type(raw) is bytes and 0 < len(raw) <= 131072, "SCORING_RESPONSE_SIZE")
        envelope = strict_json(raw)
        require(type(envelope) is dict and isinstance(envelope.get("model"), str)
                and (envelope["model"] == "gpt-6-astra" or envelope["model"].startswith("gpt-6-astra-")),
                "SCORING_RESPONSE_MODEL")
        choices = envelope.get("choices")
        require(type(choices) is list and len(choices) == 1
                and choices[0].get("finish_reason") == "stop", "SCORING_RESPONSE_INCOMPLETE")
        message = choices[0].get("message", {})
        content = message.get("content")
        require(not message.get("refusal") and type(content) is str
                and content.startswith("{") and content.endswith("}"), "SCORING_RESPONSE_FORMAT")
        result = validate_response(strict_json(content), sheet["RUBRIC_RESULT"])
        return dict(status="READY", result=result)
    except Exception:
        # No zero/partial/deterministic score fallback, no provider text in logs.
        return dict(status="UNSCORABLE")


def verify_score_record(core, record, *, evidence_units):
    """Offline verifier recomputes from retained full-attempt Seungun positions."""
    require(type(record) is dict, "SCORING_RECORD_MISSING")
    if core["input_validation"]["status"] != "ACCEPT":
        require(record == {"status": "NOT_REQUESTED"}, "SCORING_NONACCEPT_RESULT")
        return
    if record == {"status": "UNSCORABLE"}:
        return
    require(set(record) == {"status", "result"} and record["status"] == "READY", "SCORING_RECORD_INVALID")
    sheet = worksheet(evidence_rows(core["core_result"]["pronunciation"]), evidence_units=evidence_units)
    validate_response(record["result"], sheet["RUBRIC_RESULT"])
