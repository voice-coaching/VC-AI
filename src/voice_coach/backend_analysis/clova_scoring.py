"""Versioned, evidence-bound CLOVA scoring. No score fallback on generation failure."""
from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.request import Request, urlopen

from voice_coach.pronunciation_feedback.adapters.advice_generator_hyperclova import (
    DEFAULT_MODEL_ID, _completion_url, _completion_content,
)
from .ports import BackendAnalysisError

RUBRIC = "clova-phone-rubric-v1"
PROMPT = """당신은 한국어 발음 학습 서비스의 기준표 채점기다. 입력은 현재 녹음의 전체 음소 분석 집계다.
이 점수는 서비스 승급용 기준표 점수이며 실제 발음 정확도 확률이나 공인 평가 점수가 아니다.
N=expectedPhoneCount, C=correctPhoneCount, A=alignedPhoneCount, U=unflaggedPhoneCount.
기준표: alignmentScore=60*C/N, coverageScore=20*A/N, stabilityScore=20*U/N.
각 항목을 소수점 첫째 자리까지 사사오입(HALF_UP)한 뒤 세 항목을 합해 overallScore를 만든다.
correct는 CTC 정렬 일치, aligned는 CTC 삭제 후보가 아닌 음소, unflagged는 검출 임계값 미통과 음소다.
CTC 후보를 실제 오류로 단정하거나 검출기 순위 점수를 정확도 확률로 바꾸지 않는다.
억양, 속도, 입술, 음색, 개인 특성에 추가 점수를 부여하지 않는다. 추가 필드와 설명은 금지한다.
다음 네 숫자 필드만 있는 JSON 객체 하나를 출력하라:
{"alignmentScore":0.0,"coverageScore":0.0,"stabilityScore":0.0,"overallScore":0.0}"""


def _strict_json(raw):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError("duplicate key")
            result[k] = v
        return result
    def constant(_):
        raise ValueError("non-finite number")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def evidence_counts(analysis):
    raw = analysis.analysis_evidence
    expected = analysis.expected_phones
    if not isinstance(raw, dict) or not expected or len(expected) > 4096:
        raise ValueError("missing full-attempt evidence")
    phones = raw.get("phones")
    if raw.get("expected_phones") != list(expected) or not isinstance(phones, list) or len(phones) != len(expected):
        raise ValueError("incomplete evidence")
    counts = dict(expectedPhoneCount=len(expected), correctPhoneCount=0, alignedPhoneCount=0, unflaggedPhoneCount=0)
    for i, p in enumerate(phones):
        if not isinstance(p, dict) or type(p.get("expected_index")) is not int or p["expected_index"] != i or p.get("expected_phone") != expected[i]:
            raise ValueError("phone identity mismatch")
        operation = p.get("alignment_operation")
        flagged, score, threshold = p.get("is_error"), p.get("error_score"), p.get("operating_threshold")
        if operation not in {"correct", "substitution", "deletion"} or type(flagged) is not bool:
            raise ValueError("missing assessment")
        if any(type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 1 for x in (score, threshold)):
            raise ValueError("invalid detector evidence")
        if flagged != (score >= threshold):
            raise ValueError("detector decision mismatch")
        counts["correctPhoneCount"] += operation == "correct"
        counts["alignedPhoneCount"] += operation != "deletion"
        counts["unflaggedPhoneCount"] += not flagged
    return counts


class ClovaRubricScorer:
    def __init__(self, *, endpoint, model_id, revision, timeout):
        self.endpoint = _completion_url(endpoint)
        self.model_id, self.revision, self.timeout = model_id, revision, timeout

    def score(self, analysis):
        try:
            counts = evidence_counts(analysis)
            body = dict(model=self.model_id, messages=[
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": json.dumps(counts, sort_keys=True)},
            ], max_tokens=256, temperature=0, chat_template_kwargs={"thinking": False})
            req = Request(self.endpoint, data=json.dumps(body, ensure_ascii=False).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(req, timeout=self.timeout) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError("oversized completion")
            document = _strict_json(raw.decode("utf-8"))
            if document["choices"][0].get("finish_reason") != "stop":
                raise ValueError("incomplete completion")
            result = _strict_json(_completion_content(document, expected_model=self.model_id, expected_revision=self.revision))
            if not isinstance(result, dict) or set(result) != {"alignmentScore", "coverageScore", "stabilityScore", "overallScore"}:
                raise ValueError("invalid score fields")
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in result.values()):
                raise ValueError("invalid score numbers")
            expected = {}
            for name, count, weight in [("alignmentScore", "correctPhoneCount", 60), ("coverageScore", "alignedPhoneCount", 20), ("stabilityScore", "unflaggedPhoneCount", 20)]:
                expected[name] = (Decimal(counts[count]*weight)/Decimal(counts["expectedPhoneCount"])).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            expected["overallScore"] = sum(expected.values())
            if any(Decimal(str(result[k])) != v for k,v in expected.items()):
                raise ValueError("rubric arithmetic mismatch")
            evidence = dict(rubricRevision=RUBRIC, generator="hyperclova", modelRevision=self.revision,
                evidenceSha256=hashlib.sha256(json.dumps(analysis.analysis_evidence, ensure_ascii=False,
                    sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(), **counts)
            evidence.update({k:float(v) for k,v in expected.items() if k != "overallScore"})
            return float(expected["overallScore"]), evidence
        except Exception:
            raise BackendAnalysisError("analysis_execution_failed_closed", "CLOVA 채점을 완료하지 못했습니다. 다시 시도해 주세요.") from None


def load_clova_scorer(env, *, repository_root):
    enabled = env.get("AI_ANALYSIS_CLOVA_SCORING_ENABLED", "false").lower()
    if enabled == "false":
        return None
    if enabled != "true":
        raise ValueError("invalid scoring configuration")
    config = json.loads((Path(repository_root)/"configs/pronunciation_feedback/hyperclovax_seed_think_32b_v1.json").read_text())
    if config["modelId"] != DEFAULT_MODEL_ID:
        raise ValueError("unexpected scoring model")
    timeout = float(env.get("AI_ANALYSIS_CLOVA_TIMEOUT_SECONDS", "180"))
    if not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError("invalid scoring timeout")
    return ClovaRubricScorer(endpoint=env.get("AI_ANALYSIS_CLOVA_ENDPOINT", "http://127.0.0.1:8000/v1"),
        model_id=config["modelId"], revision=config["revision"], timeout=timeout)
