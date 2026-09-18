"""Multi-candidate teaching with one optional generation call and honest fallback."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import logging
from pathlib import Path
import re
import time
from typing import Protocol

from .coaching_evidence import POLICY, build_coaching_input, project_candidates

SCHEMA = "voice-coaching.coaching-result.v1"
PROMPT_REVISION = "pronunciation-coaching-v1"
LOG = logging.getLogger("voice_coach.coaching")
LOG.setLevel(logging.INFO)


class CoachingGenerator(Protocol):
    provider: str
    maximum_input_tokens: int
    def count_tokens(self, messages: list[dict]) -> int: ...
    def generate(self, messages: list[dict], *, analysis_id: int, request_id: str) -> dict: ...


def load_prompt(root):
    directory = Path(root) / "configs/pronunciation_feedback/prompts"
    active = directory / "coaching/active.md"
    if active.is_symlink():
        target = active.resolve(strict=True)
    else:
        # Git on Windows can check out a symlink as its literal target name.
        name = active.read_text(encoding="utf-8").strip()
        if name != "pronunciation-coaching-v1.md":
            raise ValueError("coaching_prompt_link_invalid")
        target = (active.parent / name).resolve(strict=True)
    if target.parent != (directory / "coaching").resolve():
        raise ValueError("coaching_prompt_target_invalid")
    prompt = (directory / "system.md").read_text(encoding="utf-8") + "\n" + target.read_text(encoding="utf-8")
    return prompt, hashlib.sha256(prompt.encode()).hexdigest()


def guidance_for(candidate, facts):
    """General exercises, never reverse-inferred personal anatomy diagnoses."""
    phone, role = candidate["expectedPhone"], candidate["writtenRole"]
    words = list(dict.fromkeys(f["location"]["word"] for f in facts if f["location"]["word"]))
    word = words[0] if words else "해당 단어"
    if len(word) > 160:
        word = "해당 단어"
    action = "소리를 따로 끊기보다 단어를 천천히 이어 말한 뒤 자연스러운 속도로 연결해 보세요."
    if role == "nucleus":
        action = "목표 모음을 천천히 내고 입 모양을 유지한 채 앞뒤 소리에 연결해 보세요. 억지로 크게 벌리지는 마세요."
    elif role == "onset" and phone in "ㅂㅃㅁ":
        action = "두 입술이 닿는 준비 동작을 확인한 뒤 뒤따르는 모음으로 연결해 보세요. 이는 목표 소리 연습이며 입술 오류를 관찰했다는 뜻은 아닙니다."
    elif role == "onset" and phone in "ㅋㅌㅍ":
        action = "목표 거센소리를 뒤의 모음과 붙여 짧게 연습하며 소리 시작의 숨이 나오는 느낌을 비교해 보세요. 목에 과하게 힘을 주지 마세요."
    elif role == "onset" and phone in "ㅅㅆ":
        action = "목표 소리를 짧게 이어 낸 뒤 뒤의 모음에 연결해 보세요. 숨이 이어지는 소리를 비교하되 이나 턱에 과하게 힘을 주지 마세요."
    return dict(guidanceId=f"practice-{phone}-{role or 'unknown'}", revision="basic-practice-v1",
        scope="GENERAL_TARGET_PRACTICE_NOT_PERSONAL_CAUSE", action=action,
        practice=f"‘{word}’을 천천히 한 번, 자연스러운 속도로 한 번 말하고 문장 안에서 이어 말해 보세요.",
        selfCheck=f"두 번 말한 ‘{word}’의 목표 소리가 유지되는지 같은 부분을 번갈아 들어 보세요. 분석 후보와 실제 들리는 소리가 다르면 오류로 단정하지 마세요.")


def prepare_view(source, count):
    view = project_candidates(source, count)
    by_id = {f["evidenceId"]: f for f in view["facts"]}
    for candidate in view["candidates"]:
        candidate["guidance"] = guidance_for(candidate, [by_id[i] for i in candidate["evidenceIds"]])
    return view


def messages_for(system, view):
    return [dict(role="system", content=system), dict(role="user", content=json.dumps(
        view, ensure_ascii=False, separators=(",", ":"), allow_nan=False))]


def pack(source, system, generator):
    for count in range(min(3, len(source["candidates"])), -1, -1):
        view = prepare_view(source, count)
        messages = messages_for(system, view)
        if generator.count_tokens(messages) <= generator.maximum_input_tokens:
            if source["candidates"] and count == 0:
                raise ValueError("coaching_mandatory_candidate_exceeds_budget")
            return view, messages
    raise ValueError("coaching_mandatory_context_exceeds_budget")


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-16-le")) // 2 > 1200:
        raise ValueError("coaching_text_invalid")
    if re.search(r"(?:https?://|[A-Za-z]:[\\/]|/(?:tmp|run|opt|home)/|sk-proj-|Bearer\s|\b\d+(?:\.\d+)?\s*(?:점|%))", value):
        raise ValueError("coaching_unsupported_text")
    return value.strip()


def validate_generated(document, view):
    if not isinstance(document, dict) or set(document) != {"items"}:
        raise ValueError("coaching_output_shape")
    rows = document["items"]
    candidates = {c["candidateId"]: c for c in view["candidates"]}
    if not isinstance(rows, list) or not 0 < len(rows) <= min(3, len(candidates)):
        raise ValueError("coaching_output_count")
    selected = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"candidateId", "guidanceId", "explanation", "action", "practice", "selfCheck"}:
            raise ValueError("coaching_item_shape")
        candidate = candidates.get(row["candidateId"])
        if candidate is None or row["candidateId"] in selected or row["guidanceId"] != candidate["guidance"]["guidanceId"]:
            raise ValueError("coaching_item_binding")
        selected.add(row["candidateId"])
        for name in ("explanation", "action", "practice", "selfCheck"):
            _text(row[name])
    # Avoid validating the same single anchor while ignoring equally admitted
    # different patterns. The bounded input already chose the learning agenda.
    if selected != set(candidates):
        raise ValueError("coaching_candidate_coverage")
    return rows


def template_rows(view):
    return [dict(candidateId=c["candidateId"], guidanceId=c["guidance"]["guidanceId"],
        explanation="분석 모델이 목표 소리와 다른 가능성을 표시했습니다. 실제 대치나 조음 원인이 확정된 것은 아니므로 해당 부분을 비교하며 연습해 보세요.",
        **{k: c["guidance"][k] for k in ("action", "practice", "selfCheck")}) for c in view["candidates"]]


def render_document(source, view, generated, *, provider, fallback, prompt_digest):
    candidates = {c["candidateId"]: c for c in view["candidates"]}
    facts = {f["evidenceId"]: f for f in source["facts"]}
    items = []
    for row in generated:
        candidate = candidates[row["candidateId"]]
        observations = [facts[i] for i in candidate["evidenceIds"]]
        first = observations[0]
        observed = first["observedCandidate"]
        observation = f"목표 표지 ‘{first['expectedPhone']}’ 구간에서 모델의 후보 표지는 ‘{observed}’입니다." if observed else "해당 구간에서 발음 검출기가 확인 후보를 표시했습니다."
        if observed == first["expectedPhone"]:
            observation = f"모델 표지는 목표 ‘{observed}’와 같지만 발음 검출기가 이 구간을 확인 후보로 표시했습니다. 대치가 확인된 것은 아닙니다."
        items.append(dict(**row, evidenceIds=candidate["evidenceIds"],
            expectedPhone=first["expectedPhone"], observedCandidate=observed,
            location=deepcopy(first["location"]), observation=observation,
            claimScope=candidate["claimScope"]))
    limited = source["coverage"]["reviewOnlyPhoneCount"] > 0
    status = "READY" if items else "LIMITED_EVIDENCE" if limited else "NO_ACTIONABLE_ISSUE"
    summary = ("확인할 소리를 짧게 연습하고 같은 단어를 다시 비교해 보세요." if items else
               "정렬 근거가 불확실해 특정 소리를 잘못 발음했다고 판단하지 않았습니다." if limited else
               "평가 가능한 구간에서 우선 교정할 근거를 찾지 못했습니다. 전체 발음의 완벽함을 보증하는 결과는 아닙니다.")
    limitations = []
    if limited:
        limitations.append("정렬이 모호하거나 국소 근거가 없는 소리는 확정 오류와 점수에서 보류했습니다.")
    if view["coverage"]["omittedCandidateIds"]:
        limitations.append("일부 후보는 입력 범위 제한으로 이번 코칭에서 제외됐습니다.")
    if source["visual"]["status"] != "NOT_PROVIDED":
        limitations.append("영상 관측은 발음 정답이나 개인별 입 벌림 교정으로 검증되지 않았습니다.")
    return dict(schemaVersion=SCHEMA, status=status, summary=summary, strengths=[], items=items,
        practicePlan=items[0]["practice"] if items else None, limitations=limitations,
        comparison=None, coverage=deepcopy(view["coverage"]), score=deepcopy(source["score"]),
        visual=deepcopy(source["visual"]),
        generation=dict(source="TEMPLATE" if fallback else "LLM", provider=provider,
            promptRevision=PROMPT_REVISION, promptSha256=prompt_digest, policyRevision=POLICY,
            fallbackReason=fallback))


class EvidenceBasedCoaching:
    def __init__(self, root, generator=None):
        self.system, self.prompt_digest = load_prompt(root)
        self.generator = generator
        # Uvicorn configures its own loggers; an unconfigured root would drop
        # this logger's INFO events. Install one secret-free, UTC stage stream.
        if not LOG.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
            formatter.converter = time.gmtime
            handler.setFormatter(formatter)
            LOG.addHandler(handler)
            LOG.propagate = False

    def render(self, *, request, analysis, visual_state="NOT_PROVIDED", visual_observations=()):
        started = time.monotonic()
        source = build_coaching_input(analysis=analysis, text=request.script_text,
            duration_ms=request.duration_ms, visual_state=visual_state, visual_observations=visual_observations)
        view = prepare_view(source, min(3, len(source["candidates"])))
        fallback = "GENERATOR_NOT_CONFIGURED"
        rows = template_rows(view)
        provider = self.generator.provider if self.generator else "NONE"
        if not rows:
            fallback = "NO_ACTIONABLE_CANDIDATES"
        elif self.generator:
            stage = "packing"
            try:
                view, messages = pack(source, self.system, self.generator)
                stage = "generation"
                result = self.generator.generate(messages, analysis_id=request.analysis_id, request_id=str(request.event_id))
                stage = "validation"
                rows = validate_generated(result, view)
                fallback = None
            except Exception:
                # No exception body, prompt, media or secret in the log. Never
                # restore legacy uncertain selected-phone advice on failure.
                fallback = {"packing": "INPUT_BUDGET", "generation": "GENERATION_FAILED", "validation": "OUTPUT_REJECTED"}[stage]
                rows = template_rows(view)
        result = render_document(source, view, rows, provider=provider,
                                 fallback=fallback, prompt_digest=self.prompt_digest)
        LOG.info("coaching_result analysis=%s request=%s provider=%s status=%s fallback=%s candidates=%d review=%d elapsed_ms=%d",
            request.analysis_id, request.event_id, provider, result["status"], fallback,
            len(rows), source["coverage"]["reviewOnlyPhoneCount"], round((time.monotonic()-started)*1000))
        return result
