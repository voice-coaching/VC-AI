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
from .coaching_visual_policy import (
    EXPLANATIONS, REVISION as VISUAL_POLICY, OUTPUT_SCHEMA, public_visual, render_visual_statements,
)

SCHEMA = "voice-coaching.coaching-result.v1"
PROMPT_REVISION = "pronunciation-coaching-v1"
MAX_FEEDBACK_SENTENCES = 5
MAX_ACTION_UTF16 = 140
_META_LANGUAGE = re.compile(r"모델|검출|정렬|후보|보류|불확실|대치|확정|판단|기대\s*소리|연습\s*항목|단정")
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
    visual_prompt = (directory / "coaching/visual-coaching-v2.md").read_text(encoding="utf-8")
    if hashlib.sha256(visual_prompt.encode()).hexdigest() != "abc69b9a3a46a00126034c2d4b143f4ba087f4697f4d2ff38d995cac6130308b":
        raise ValueError("coaching_visual_prompt_integrity_invalid")
    prompt += "\n" + visual_prompt
    return prompt, hashlib.sha256(prompt.encode()).hexdigest()


def guidance_for(candidate, facts):
    """General exercises, never reverse-inferred personal anatomy diagnoses."""
    phone, role = candidate["expectedPhone"], candidate["writtenRole"]
    words = list(dict.fromkeys(f["location"]["word"] for f in facts if f["location"]["word"]))
    word = words[0] if words else "해당 단어"
    # A quoted source word must not add sentences, markup or instructions to
    # the deterministic fallback. Keep the original location in evidence.
    if re.fullmatch(r"[가-힣ㄱ-ㅎㅏ-ㅣ]{1,24}", word) is None or _META_LANGUAGE.search(word):
        word = "해당 단어"
    action = f"‘{word}’ → 소리를 따로 끊지 말고 천천히 이어 말한 뒤 자연스러운 속도로 연결해 보세요."
    if role == "nucleus":
        action = f"‘{word}’ → ‘{phone}’ 소리를 천천히 낸 뒤 앞뒤 소리에 부드럽게 연결해 보세요."
    elif role == "onset" and phone in "ㅂㅃㅁ":
        action = f"‘{word}’ → ‘{phone}’을 낼 때 두 입술을 가볍게 맞댄 뒤 바로 뒤 모음으로 연결해 보세요."
    elif role == "onset" and phone in "ㅋㅌㅍ":
        action = f"‘{word}’ → ‘{phone}’의 시작에서 숨을 짧게 내보내며 뒤 모음에 붙여 연습해 보세요."
    elif role == "onset" and phone in "ㅅㅆ":
        action = f"‘{word}’ → ‘{phone}’의 마찰음을 짧게 낸 뒤 뒤 모음에 바로 연결해 보세요."
    return dict(guidanceId=f"practice-{phone}-{role or 'unknown'}", revision="basic-practice-v1",
        scope="GENERAL_TARGET_PRACTICE_NOT_PERSONAL_CAUSE", action=action,
        practice=f"‘{word}’ 부분을 천천히 한 번, 자연스러운 속도로 한 번 말하고 문장 안에서 이어 말해 보세요.",
        selfCheck=f"두 번 말한 ‘{word}’의 목표 소리가 유지되는지 같은 부분을 번갈아 들어 보세요.")


def prepare_view(source, count):
    view = project_candidates(source, count)
    by_id = {f["evidenceId"]: f for f in view["facts"]}
    for candidate in view["candidates"]:
        candidate["guidance"] = guidance_for(candidate, [by_id[i] for i in candidate["evidenceIds"]])
        candidate["allowedText"] = dict(explanation=list(EXPLANATIONS),
            **{k: [candidate["guidance"][k]] for k in ("action", "practice", "selfCheck")})
    return view


def messages_for(system, view, expert_examples=None):
    messages = [dict(role="system", content=system)]
    if expert_examples is not None:
        messages.append(expert_examples.message())
    messages.append(dict(role="user", content=json.dumps(
        dict(currentAttempt=view) if expert_examples is not None else view,
        ensure_ascii=False, separators=(",", ":"), allow_nan=False)))
    return messages


def pack(source, system, generator, expert_examples=None):
    for count in range(min(3, len(source["candidates"])), -1, -1):
        view = prepare_view(source, count)
        messages = messages_for(system, view, expert_examples)
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


def _action(value):
    """One short learner-facing sentence; diagnostics stay in evidence fields."""
    text = _text(value)
    if (len(text.encode("utf-16-le")) // 2 > MAX_ACTION_UTF16
            or not text.endswith(".")
            or re.search(r"[.!?。！？…\r\n\u2028\u2029]", text[:-1])):
        raise ValueError("coaching_action_length")
    if _META_LANGUAGE.search(text):
        raise ValueError("coaching_action_meta")
    return text


def validate_generated(document, view):
    if not isinstance(document, dict) or set(document) != {"items"}:
        raise ValueError("coaching_output_shape")
    rows = document["items"]
    candidates = {c["candidateId"]: c for c in view["candidates"]}
    if not isinstance(rows, list) or not 0 < len(rows) <= min(3, len(candidates)):
        raise ValueError("coaching_output_count")
    selected = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"candidateId", "guidanceId", "explanation", "action", "practice", "selfCheck", "usedVisualStatementIds"}:
            raise ValueError("coaching_item_shape")
        candidate = candidates.get(row["candidateId"])
        if candidate is None or row["candidateId"] in selected or row["guidanceId"] != candidate["guidance"]["guidanceId"]:
            raise ValueError("coaching_item_binding")
        selected.add(row["candidateId"])
        for name in ("explanation", "action", "practice", "selfCheck"):
            _text(row[name])
            if row[name] not in candidate["allowedText"][name]:
                raise ValueError("coaching_text_not_allowed")
        refs = row["usedVisualStatementIds"]
        if (not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs)
                or len(refs) != len(set(refs)) or not set(refs) <= set(candidate["allowedStatementIds"])):
            raise ValueError("coaching_visual_reference_invalid")
        _action(row["action"])
    # Avoid validating the same single anchor while ignoring equally admitted
    # different patterns. The bounded input already chose the learning agenda.
    if selected != set(candidates):
        raise ValueError("coaching_candidate_coverage")
    # Retain the server's agenda order even if the model reorders valid IDs.
    by_id = {row["candidateId"]: row for row in rows}
    return [by_id[candidate_id] for candidate_id in candidates]


def template_rows(view):
    return [dict(candidateId=c["candidateId"], guidanceId=c["guidance"]["guidanceId"],
        explanation=EXPLANATIONS[0], usedVisualStatementIds=[],
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
        visual_text = render_visual_statements(source, row)
        if visual_text:
            observation += " " + visual_text
        public_row = {k: v for k, v in row.items() if k != "usedVisualStatementIds"}
        items.append(dict(**public_row, evidenceIds=candidate["evidenceIds"],
            expectedPhone=first["expectedPhone"], observedCandidate=observed,
            location=deepcopy(first["location"]), observation=observation,
            claimScope=candidate["claimScope"]))
    limited = source["coverage"]["reviewOnlyPhoneCount"] > 0
    status = "READY" if items else "LIMITED_EVIDENCE" if limited else "NO_ACTIONABLE_ISSUE"
    if len(items) > MAX_FEEDBACK_SENTENCES:
        raise ValueError("coaching_feedback_length")
    summary = ("\n".join(_action(item["action"]) for item in items) if items else
               "문장을 편안한 속도로 한 번 읽고, 단어 사이의 연결이 자연스러운지 들어보세요.")
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
        visual=public_visual(source["visual"]),
        generation=dict(source="TEMPLATE" if fallback else "LLM", provider=provider,
            promptRevision=PROMPT_REVISION, promptSha256=prompt_digest, policyRevision=POLICY,
            fallbackReason=fallback))


class EvidenceBasedCoaching:
    def __init__(self, root, generator=None, *, expert_examples=None):
        self.system, self.prompt_digest = load_prompt(root)
        self.generator = generator
        self.expert_examples = expert_examples
        if expert_examples is not None:
            from .expert_examples import INSTRUCTIONS
            self.system += "\n" + INSTRUCTIONS
            self.prompt_digest = hashlib.sha256(
                (self.system + "\n" + expert_examples.sha256).encode()).hexdigest()
        # Uvicorn configures its own loggers; an unconfigured root would drop
        # this logger's INFO events. Install one secret-free, UTC stage stream.
        if not LOG.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
            formatter.converter = time.gmtime
            handler.setFormatter(formatter)
            LOG.addHandler(handler)
            LOG.propagate = False
        LOG.info("coaching_visual_policy_loaded revision=%s schema=%s prompt_sha256=%s corrective_claims=false",
                 VISUAL_POLICY, OUTPUT_SCHEMA, self.prompt_digest)
        if expert_examples is not None:
            LOG.info("coaching_expert_examples_loaded revision=%s sha256=%s cases=%s input_budget=%s",
                     expert_examples.revision, expert_examples.sha256,
                     ",".join(expert_examples.case_ids), generator.maximum_input_tokens if generator else None)

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
                view, messages = pack(source, self.system, self.generator, self.expert_examples)
                if self.expert_examples is not None:
                    LOG.info("coaching_expert_retrieval analysis=%s request=%s revision=%s sha256=%s cases=%s policy=ALL_CASES_EVERY_GENERATION",
                             request.analysis_id, request.event_id, self.expert_examples.revision,
                             self.expert_examples.sha256, ",".join(self.expert_examples.case_ids))
                stage = "generation"
                result = self.generator.generate(messages, analysis_id=request.analysis_id, request_id=str(request.event_id))
                stage = "validation"
                rows = validate_generated(result, view)
                fallback = None
            except Exception as error:
                # No exception body, prompt, media or secret in the log. Never
                # restore legacy uncertain selected-phone advice on failure.
                fallback = {"packing": "INPUT_BUDGET", "generation": "GENERATION_FAILED", "validation": "OUTPUT_REJECTED"}[stage]
                # Stable local validator codes only; never log provider bodies.
                reason = str(error) if type(error) is ValueError else "unclassified"
                if reason not in {"coaching_output_shape", "coaching_output_count", "coaching_item_shape",
                                  "coaching_item_binding", "coaching_text_invalid", "coaching_unsupported_text",
                                  "coaching_candidate_coverage", "coaching_action_length", "coaching_action_meta",
                                  "coaching_text_not_allowed", "coaching_visual_reference_invalid"}:
                    reason = "unclassified"
                LOG.warning("coaching_fallback analysis=%s request=%s stage=%s reason=%s",
                            request.analysis_id, request.event_id, stage, reason)
                rows = template_rows(view)
        result = render_document(source, view, rows, provider=provider,
                                 fallback=fallback, prompt_digest=self.prompt_digest)
        LOG.info("coaching_visual_usage analysis=%s request=%s policy=%s schema=%s provided=%d referenced=%d displayed=%d",
            request.analysis_id, request.event_id, VISUAL_POLICY, OUTPUT_SCHEMA,
            len(view["allowedStatements"]), sum(len(r["usedVisualStatementIds"]) for r in rows),
            sum(len(r["usedVisualStatementIds"]) for r in rows))
        LOG.info("coaching_result analysis=%s request=%s provider=%s status=%s fallback=%s candidates=%d review=%d elapsed_ms=%d",
            request.analysis_id, request.event_id, provider, result["status"], fallback,
            len(rows), source["coverage"]["reviewOnlyPhoneCount"], round((time.monotonic()-started)*1000))
        return result
