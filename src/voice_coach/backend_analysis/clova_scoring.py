"""Versioned, evidence-bound CLOVA scoring. No score fallback on generation failure."""
from __future__ import annotations

import hashlib
import json
import math
import logging
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.request import Request, urlopen

from voice_coach.pronunciation_feedback.adapters.advice_generator_hyperclova import (
    DEFAULT_MODEL_ID, _completion_url, _completion_content,
)
from .ports import BackendAnalysisError

from voice_coach.pronunciation_feedback.clova_prompt_routing import load_scoring_prompt

RUBRIC = "clova-phone-rubric-v2"
STATES = ("matchedClear", "matchedFlagged", "substitutedClear", "substitutedFlagged", "deletedClear", "deletedFlagged")


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


def _score_document(content):
    """Accept one JSON value, optionally enclosed by one complete JSON fence.

    Do not search for a JSON substring, remove prose/thoughts, repair syntax,
    or coerce values. All field, duplicate-key and rubric checks still apply.
    """
    content = content.strip()
    fenced = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", content, re.DOTALL)
    return _strict_json(fenced.group(1) if fenced else content)


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


def detailed_evidence(analysis, rubric):
    counts = evidence_counts(analysis)
    rows = []
    expected_set = set(analysis.expected_phones)
    policy_phones = [p for c in rubric["criteria"] for p in c["phones"]]
    if len(policy_phones) != len(set(policy_phones)) or not expected_set <= set(policy_phones):
        raise ValueError("unsupported or overlapping phone groups")
    for criterion in rubric["criteria"]:
        phones = [p for p in analysis.analysis_evidence["phones"]
                  if criterion["id"] == "coverage" or p["expected_phone"] in criterion["phones"]]
        row = dict(criterionId=criterion["id"], sampleCount=len(phones), **dict.fromkeys(STATES, 0))
        for phone in phones:
            state = {"correct": "matched", "substitution": "substituted", "deletion": "deleted"}[phone["alignment_operation"]]
            row[state + ("Flagged" if phone["is_error"] else "Clear")] += 1
        rows.append(row)
    return counts, rows


def criterion_level(row, rubric):
    n = row["sampleCount"]
    if n == 0:
        return None
    units = (4 * (n - row["deletedClear"] - row["deletedFlagged"]) if row["criterionId"] == "coverage"
             else sum(row[state] * weight for state, weight in rubric["evidenceUnits"].items()))
    return next(band["level"] for band in rubric["levels"] if units * 100 >= band["minimumPercent"] * 4 * n)


class ClovaRubricScorer:
    def __init__(self, *, endpoint, model_id, revision, timeout, repository_root):
        self.endpoint = _completion_url(endpoint)
        self.model_id, self.revision, self.timeout = model_id, revision, timeout
        self.repository_root = repository_root

    def score(self, analysis):
        phase = "load_policy"
        diagnostic = {}
        try:
            system, rubric, rubric_digest, prompt_digest = load_scoring_prompt(self.repository_root)
            phase = "collect_evidence"
            counts, rows = detailed_evidence(analysis, rubric)
            # Only the scoring task reads the symlink target and includes the rubric.
            policy = {k: rubric[k] for k in ("rubricRevision", "evidenceUnits", "levels", "aggregation")}
            policy["criteria"] = [{k: c[k] for k in ("id", "label", "weight")} for c in rubric["criteria"]]
            payload = {"RUBRIC_JSON": policy, "INPUT_DATA": {"criteria": rows}}
            body = dict(model=self.model_id, messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
            ], max_tokens=512, temperature=0, chat_template_kwargs={"thinking": False})
            req = Request(self.endpoint, data=json.dumps(body, ensure_ascii=False).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
            phase = "model_completion"
            with urlopen(req, timeout=self.timeout) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError("oversized completion")
            document = _strict_json(raw.decode("utf-8"))
            if document["choices"][0].get("finish_reason") != "stop":
                raise ValueError("incomplete completion")
            content = _completion_content(document, expected_model=self.model_id, expected_revision=self.revision)
            phase = "decode_score_json"
            diagnostic = {"jsonFence": content.lstrip().startswith("```"),
                          "objectStart": content.lstrip().startswith("{")}
            result = _score_document(content)
            phase = "validate_fields"
            if not isinstance(result, dict) or set(result) != {"rubricRevision", "criteria", "overallScore"} or result["rubricRevision"] != RUBRIC:
                raise ValueError("invalid score fields")
            levels = result["criteria"]
            if not isinstance(levels, list) or len(levels) != len(rows):
                raise ValueError("missing rubric criteria")
            numerator, denominator = 0, 0
            for row, grade, criterion in zip(rows, levels, rubric["criteria"], strict=True):
                expected = criterion_level(row, rubric)
                if not isinstance(grade, dict) or set(grade) != {"criterionId", "level"} or grade["criterionId"] != row["criterionId"]:
                    raise ValueError("invalid criterion identity")
                level = grade["level"]
                phase = "validate_level"
                diagnostic = {"criterion": row["criterionId"], "expectedLevel": expected,
                              "actualLevel": level if level is None or type(level) is int else "invalid_type"}
                if (level is not None and type(level) is not int) or level != expected:
                    raise ValueError("rubric level mismatch")
                row["level"] = level
                if level is not None:
                    numerator += criterion["weight"] * level
                    denominator += criterion["weight"] * 4
            score = (Decimal(100 * numerator) / Decimal(denominator)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            actual = result["overallScore"]
            phase = "validate_total"
            diagnostic = {"expectedScore": str(score),
                          "actualScore": actual if type(actual) in (int, float) and math.isfinite(actual) else "invalid_type"}
            if type(actual) not in (float, int) or not math.isfinite(actual) or Decimal(str(actual)) != score:
                raise ValueError("rubric arithmetic mismatch")
            evidence = dict(rubricRevision=RUBRIC, generator="hyperclova", modelRevision=self.revision,
                rubricSha256=rubric_digest, promptSha256=prompt_digest, criteria=rows,
                evidenceSha256=hashlib.sha256(json.dumps(analysis.analysis_evidence, ensure_ascii=False,
                    sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(), **counts)
            return float(actual), evidence
        except Exception as error:
            # Only allowlisted phase names, types and numeric grades are logged.
            # Never log input text, model output, paths, credentials, or exception messages.
            logging.getLogger(__name__).warning("clova_scoring_rejected phase=%s error=%s diagnostic=%s",
                                               phase, type(error).__name__, diagnostic)
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
        model_id=config["modelId"], revision=config["revision"], timeout=timeout, repository_root=repository_root)
