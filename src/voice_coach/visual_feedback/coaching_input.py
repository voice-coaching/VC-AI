"""Bounded public measurements for the single frozen audio+visual GPT dispatch."""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


@lru_cache(maxsize=1)
def _validator():
    path = Path(__file__).resolve().parents[3] / "docs/contracts/runpod_visual_coaching_input_v1.schema.json"
    return Draft202012Validator(json.loads(path.read_bytes()), format_checker=FormatChecker())


def prepare_visual_coaching(evidence_bytes: bytes, *, core_bytes: bytes):
    """Call only after evidence replay and the same-attempt binding passed.

Selection is explicit and stable, with omitted counts. It does not select the
lowest unvalidated distance as a defect or allow GPT to promote observations to
corrective actions. Private frame coordinates/receipts never enter the prompt.
"""
    from .feature_protocol import strict_json
    evidence = strict_json(evidence_bytes)
    visual = evidence["visual"]
    scored = visual["status"] == "SCORED"
    sort_key = ((lambda r: (r["score"] if r["status"] == "SCORED" and r["score"] is not None else 101,
                           r["expectedIndex"])) if scored else (lambda r: r["expectedIndex"]))
    rows = sorted((r for r in visual["phoneAssessments"] if r["observations"]), key=sort_key)
    fields = ("expectedIndex", "expectedPhone", "role", "status", "score", "scoreKind",
              "audioStartSeconds", "audioEndSeconds", "observations", "allowedActionIds", "reasonCodes")
    value = dict(schemaVersion="voice-coaching.visual-coaching-input.v1",
        requestId=evidence["requestId"], executionId=evidence["executionId"],
        coreSha256=hashlib.sha256(core_bytes).hexdigest(),
        visualEvidenceSha256=hashlib.sha256(evidence_bytes).hexdigest(), status=visual["status"],
        correctiveClaimsAllowed=False, score=visual["score"], scoreKind=visual["scoreKind"],
        reasonCodes=visual["reasonCodes"], selectionPolicy=("LOWEST_12_SCORED_THEN_OBSERVED_V1" if scored
                                                         else "FIRST_12_OBSERVED_MFA_INDICES_V1"),
        availablePhoneCount=len(rows), omittedPhoneCount=max(0, len(rows) - 12),
        phones=[{k: r[k] for k in fields} for r in rows[:12]])
    _validator().validate(value)
    return value
