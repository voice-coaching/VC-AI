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
    if evidence.get('assessmentPolicy') in {'PHONE_ROLE_PARTIAL_PROVISIONAL_V1','PHONE_ROLE_SPARSE_30FPS_V1'}:
        value.update(comparisonPolicy=evidence['assessmentPolicy'],coverage=visual['coverage'],
            limitations=[
                '임시 점수는 실제로 관찰·비교한 음소에만 해당하며, 전체 발음의 정답률이 아니다.',
                '다른 단어의 같은 음소·초중종성 참조는 비교 연습용이며, 입을 더 벌리거나 오므려야 한다는 확정 근거가 아니다.',
                '다른 단어 참조로 입술 동작 타이밍을 채점하지 않는다. 없는 측정이나 참조를 추측하지 않는다.'])
        for projected,row in zip(value['phones'],rows[:12]):
            if 'subScores' in row: projected['subScores']=row['subScores']
            if evidence['assessmentPolicy'] == 'PHONE_ROLE_SPARSE_30FPS_V1':
                projected['frameCount']=row['frameCount']
        if evidence['assessmentPolicy'] == 'PHONE_ROLE_SPARSE_30FPS_V1':
            value['limitations'].append('1프레임은 정적 형상 관찰이며 움직임이나 타이밍이 아니다. 2프레임의 폐쇄 값도 완전한 닫힘 동작을 보증하지 않는다.')
        if 'PARTIAL_REFERENCE_COVERAGE' in visual['reasonCodes']:
            # A displayed partial comparison is not a representative whole-clip
            # grade. The unchanged frozen composite therefore keeps audio-only
            # weighting, while its existing selector still accepts local lip
            # practice. Give GPT the measured subset separately and explicitly.
            value['partialScore']=dict(score=visual['score'],scoreKind='PROVISIONAL',
                scope='ASSESSED_PHONES_ONLY')
            value.update(status='OBSERVED',score=None,scoreKind='NONE')
    _validator().validate(value)
    return value
