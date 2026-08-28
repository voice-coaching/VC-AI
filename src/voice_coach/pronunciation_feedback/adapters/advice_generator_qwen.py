"""Local coaching-model adapter (Qwen3-class), offline and deterministic.

Runs entirely on the machine: no learner-derived value leaves the process, which
is why a local model was chosen over an API. The model receives only
:class:`CoachingPayload` — no audio, no path — and returns a message bound to an
allowlisted advice id. Everything it returns passes
:func:`validate_generated_advice` before it can reach a learner.

Decoding is greedy with a pinned revision so the same payload yields the same
message: a coaching text that changes between runs cannot be audited.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..coaching_payload import (
    CoachingPayload,
    CoachingPayloadError,
    GeneratedAdvice,
    validate_generated_advice,
)

GENERATOR_ID = "local-qwen3-coaching-agent"
#: The model is told the rules, and then the rules are checked again on the way
#: out. The prompt is a convenience; the validator is the control.
SYSTEM_PROMPT = """당신은 한국어 발음 코칭 문장을 작성한다. 음성을 직접 채점하지 않는다.
입력 JSON의 수치와 판정만 근거로 사용한다.

규칙:
1. 잘된 점을 한 문장으로 먼저 말한다.
2. 핵심 오류 1개에만 집중한다.
3. 오류 위치를 밀리초 구간과 음절로 명시한다.
4. 혀·입술·호기 등 구체적인 조음 지시를 준다.
5. 대비되는 최소 짝 연습을 한 개 제시한다.
6. allowedAdviceIds에 있는 id만 사용한다.
7. 파일 경로, 해시, 화자 식별자를 쓰지 않는다.
8. 점수나 위치를 새로 만들지 않는다. 입력에 있는 값만 인용한다.

출력은 다음 JSON 배열만 반환한다:
[{"issueType": "...", "adviceId": "...", "message": "..."}]"""


def _fail(detail: str) -> CoachingPayloadError:
    return CoachingPayloadError(detail)


def parse_generated_response(raw: str) -> tuple[GeneratedAdvice, ...]:
    """Parse the model's JSON array, rejecting anything else.

    Kept separate from the model call so the parsing contract is testable without
    a checkpoint — and so a chatty model that wraps JSON in prose fails closed
    rather than being salvaged by a regex.
    """

    text = raw.strip()
    if text.startswith("```"):
        # Strip one fenced block, which instruction-tuned models add habitually.
        lines = [line for line in text.splitlines() if not line.startswith("```")]
        text = "\n".join(lines).strip()
    try:
        document = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise _fail("generator response is not valid JSON") from error
    if not isinstance(document, list) or not document:
        raise _fail("generator response must be a non-empty JSON array")
    advice: list[GeneratedAdvice] = []
    for entry in document:
        if not isinstance(entry, dict):
            raise _fail("generator response entry must be an object")
        if set(entry) != {"issueType", "adviceId", "message"}:
            raise _fail("generator response entry schema differs")
        for key in ("issueType", "adviceId", "message"):
            if not isinstance(entry[key], str) or not entry[key].strip():
                raise _fail(f"generator response {key} must be a non-empty string")
        advice.append(
            GeneratedAdvice(
                issue_type=entry["issueType"],
                advice_id=entry["adviceId"],
                message=entry["message"].strip(),
            )
        )
    return tuple(advice)


class LocalQwenAdviceGenerator:
    """``AdviceGeneratorPort`` over a locally resident instruction model."""

    def __init__(
        self,
        *,
        checkpoint_dir: str | Path,
        revision: str,
        generator_id: str = GENERATOR_ID,
        maximum_new_tokens: int = 512,
        device: str = "cpu",
    ) -> None:
        self._checkpoint_dir = Path(checkpoint_dir)
        if not self._checkpoint_dir.is_dir():
            raise _fail("local coaching model directory is unavailable")
        if not revision:
            raise _fail("local coaching model revision must be pinned")
        self._revision = revision
        self._generator_id = generator_id
        self._maximum_new_tokens = maximum_new_tokens
        self._device = device
        self._model: Any = None
        self._tokenizer: Any = None

    @property
    def generator_id(self) -> str:
        return self._generator_id

    @property
    def revision(self) -> str:
        return self._revision

    def _load(self) -> tuple[Any, Any]:  # pragma: no cover - needs a checkpoint
        if self._model is not None:
            return self._model, self._tokenizer
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as error:
            raise _fail("the local coaching model runtime is not installed") from error
        tokenizer = AutoTokenizer.from_pretrained(
            self._checkpoint_dir, local_files_only=True
        )
        model = AutoModelForCausalLM.from_pretrained(
            self._checkpoint_dir,
            local_files_only=True,
            dtype=torch.bfloat16 if self._device != "cpu" else torch.float32,
        )
        model.eval()
        model.to(self._device)
        self._torch = torch
        self._model = model
        self._tokenizer = tokenizer
        return model, tokenizer

    def _complete(self, payload: CoachingPayload) -> str:  # pragma: no cover
        model, tokenizer = self._load()
        torch = self._torch
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": payload.to_json()},
        ]
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = tokenizer(prompt, return_tensors="pt").to(self._device)
        with torch.inference_mode():
            output = model.generate(
                **inputs,
                max_new_tokens=self._maximum_new_tokens,
                do_sample=False,
                temperature=None,
                top_p=None,
            )
        completion = output[0][inputs["input_ids"].shape[-1] :]
        return tokenizer.decode(completion, skip_special_tokens=True)

    def generate(self, payload: CoachingPayload) -> tuple[GeneratedAdvice, ...]:
        raw = self._complete(payload)
        return validate_generated_advice(
            payload=payload,
            generated=parse_generated_response(raw),
        )


__all__ = [
    "GENERATOR_ID",
    "SYSTEM_PROMPT",
    "LocalQwenAdviceGenerator",
    "parse_generated_response",
]
