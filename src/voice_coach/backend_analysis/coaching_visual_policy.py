"""Private same-candidate visual policy. No validated corrective claims in P0/P1."""
from copy import deepcopy
import math
import re

REVISION = "coaching-visual-policy-v2"
INPUT_SCHEMA = "voice-coaching.coaching-input.v2"
OUTPUT_SCHEMA = "coaching_generation_v2"
EXPLANATIONS = (
    "목표 소리를 단어 안에서 자연스럽게 연결하는 연습입니다.",
    "목표 소리를 천천히 연습한 뒤 문장 안에서 이어 말하는 연습입니다.",
)
PUBLIC_OBSERVATION = ("expectedIndex", "stage", "reasonCodes", "measurements",
                      "decodedSampleTimesMs", "samplingScope")
PUBLIC_MEASUREMENT = ("cueId", "value", "unit", "startMs", "endMs", "sampleCount", "samplingProvenance")


def public_visual(visual):
    """Explicit v1 projection; richer private data never leaks to old readers."""
    result = {k: deepcopy(visual[k]) for k in ("status", "referenceStatus", "correctiveClaimsAllowed")}
    result["observations"] = []
    for observation in visual["observations"]:
        row = {k: deepcopy(observation[k]) for k in PUBLIC_OBSERVATION}
        row["measurements"] = [{k: deepcopy(m[k]) for k in PUBLIC_MEASUREMENT}
                               for m in observation["measurements"]]
        result["observations"].append(row)
    return result


def _valid_measurement(m, span):
    if not isinstance(m, dict):
        return False
    times, context = m.get("usedFrameTimesMs"), m.get("contextFrameTimesMs")
    if (m.get("aggregation") not in {"NEAREST_FRAME", "MEAN_IN_PHONE_SPAN", "MAX_IN_PHONE_SPAN"}
            or type(m.get("value")) not in (int, float) or not math.isfinite(m["value"])
            or not isinstance(times, list) or not times or not isinstance(context, list)
            or type(m.get("validSampleCount")) is not int or m["validSampleCount"] != len(times)
            or m.get("startMs") != span["start"] or m.get("endMs") != span["end"]):
        return False
    if any(type(t) not in (int, float) or not math.isfinite(t)
           or not span["start"] <= t <= span["end"] for t in times):
        return False
    if times != sorted(set(times)):
        return False
    if m["aggregation"] == "NEAREST_FRAME":
        return len(times) == 1 and m.get("selectedFrameInsidePhoneSpan") is True and not context
    if m["aggregation"] == "MAX_IN_PHONE_SPAN":
        return (m.get("unit") == "normalized_per_second" and len(context) == len(times)
                and all(type(t) in (int, float) and math.isfinite(t) and 0 <= t < end
                        for t, end in zip(context, times)))
    return not context


def bind_visual(source):
    """Fail closed per observation while retaining all original receipts locally."""
    facts = {f["evidenceId"]: f for f in source["facts"]}
    candidates = {c["candidateId"]: c for c in source["candidates"]}
    allowed, seen = [], set()
    for observation in source["visual"]["observations"]:
        observation["interpretationMode"] = "UNAVAILABLE"
        observation["policyReason"] = "BINDING_OR_RELIABILITY_UNAVAILABLE"
        candidate = candidates.get(observation.get("candidateId"))
        evidence_id = observation.get("audioEvidenceId")
        oid = observation.get("observationId")
        if (candidate is None or evidence_id not in candidate["evidenceIds"]
                or not isinstance(oid, str) or oid in seen
                or evidence_id not in facts or facts[evidence_id]["expectedIndex"] != observation["expectedIndex"]):
            continue
        seen.add(oid)
        provenance = observation.get("provenance") or {}
        receipt = provenance.get("measurementReceipt")
        if (not isinstance(receipt, dict) or receipt.get("schemaVersion") != "coaching-measurement-receipt.v2"
                or observation.get("stage") != "completed"):
            continue
        if receipt.get("qc") != "PASSED" or receipt.get("sync") != "POLICY_ACCEPTED":
            continue
        if any(re.fullmatch(r"[0-9a-f]{64}", str(receipt.get(k, ""))) is None for k in
               ("bindingSha256", "reliabilitySha256", "runtimeBundleSha256", "phoneAnchorSha256", "windowSha256")):
            continue
        span = receipt.get("videoPhoneSpanMs")
        if (not isinstance(span, dict) or type(span.get("start")) is not int
                or type(span.get("end")) is not int or not 0 <= span["start"] < span["end"]):
            continue
        valid = [deepcopy(m) for m in receipt.get("measurements", []) if _valid_measurement(m, span)]
        observation["validatedMeasurements"] = valid
        observation["policyReason"] = "NO_VALID_IN_SPAN_SAMPLES"
        if not valid:
            continue
        observation["interpretationMode"] = "OBSERVATION_ONLY"
        observation["policyReason"] = "REFERENCE_NOT_VALIDATED"
        allowed.append(dict(statementId=f"{oid}-replay", candidateId=candidate["candidateId"],
            observationIds=[oid], kind="NEUTRAL_REPLAY_GUIDANCE",
            text="같은 구간을 다시 보며 입술 움직임을 확인해 보세요."))
    source["allowedStatements"] = allowed
    source["visualPolicyRevision"] = REVISION
    for candidate in candidates.values():
        candidate["allowedStatementIds"] = [s["statementId"] for s in allowed if s["candidateId"] == candidate["candidateId"]]
        candidate["visualObservationIds"] = [o["observationId"] for o in source["visual"]["observations"]
            if o.get("candidateId") == candidate["candidateId"] and o.get("interpretationMode") == "OBSERVATION_ONLY"]


def generation_schema(view):
    """Per-candidate text allowlists prevent free-text circumvention of policy."""
    variants = []
    for candidate in view["candidates"]:
        properties = {k: {"type": "string", "enum": v} for k, v in candidate["allowedText"].items()}
        properties.update(candidateId={"type": "string", "enum": [candidate["candidateId"]]},
            guidanceId={"type": "string", "enum": [candidate["guidance"]["guidanceId"]]},
            usedVisualStatementIds={"type": "array", "maxItems": len(candidate["allowedStatementIds"]),
                "items": {"type": "string", **({"enum": candidate["allowedStatementIds"]} if candidate["allowedStatementIds"] else {})}})
        variants.append(dict(type="object", properties=properties, required=list(properties), additionalProperties=False))
    return dict(type="object", properties={"items": dict(type="array", minItems=len(variants), maxItems=len(variants),
                items={"anyOf": variants})}, required=["items"], additionalProperties=False)


def render_visual_statements(source, row):
    referenced = set(row.get("usedVisualStatementIds", []))
    statements = [s for s in source["allowedStatements"] if s["statementId"] in referenced]
    observations = {o.get("observationId"): o for o in source["visual"]["observations"]}
    rendered = []
    for statement in statements:
        for oid in statement["observationIds"]:
            observation = observations[oid]
            # Numbers are rendered only from validated server measurements, never LLM text.
            m = observation["validatedMeasurements"][0]
            rendered.append(f"영상 {m['startMs']}~{m['endMs']} ms 구간의 {m['cueId']} 관측값은 {m['value']:.4g} {m['unit']}입니다.")
        rendered.append(statement["text"])
    return " ".join(rendered)
