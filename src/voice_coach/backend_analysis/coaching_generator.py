"""Provider adapters for the same bounded coaching messages (no hidden retry)."""
from __future__ import annotations

import json
import math
import logging
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler

from voice_coach.pronunciation_feedback.adapters.advice_generator_hyperclova import (
    _completion_url, _completion_content, OfflineChatTokenCounter,
)
from .clova_scoring import _strict_json
from .coaching import EvidenceBasedCoaching
from .expert_examples import ExpertCoachingExamples

LOG = logging.getLogger("voice_coach.coaching")


def _usage_count(document, name):
    usage = document.get("usage")
    value = usage.get(name) if isinstance(usage, dict) else None
    return value if type(value) is int and 0 <= value <= 1000000 else None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("coaching_redirect_rejected")


class ChatCoachingGenerator:
    def __init__(self, *, provider, model, endpoint, revision=None, api_key=None,
                 counter=None, timeout=120, maximum_input_tokens=3900, maximum_output_tokens=1200):
        if provider not in {"CLOVA", "OPENAI"} or not model or not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise ValueError("coaching_provider_invalid")
        if not 1 <= maximum_input_tokens <= 100000 or not 256 <= maximum_output_tokens <= 8192:
            raise ValueError("coaching_limits_invalid")
        if provider == "CLOVA":
            self.endpoint = _completion_url(endpoint)
            if not revision or counter is None or maximum_output_tokens > 2048:
                raise ValueError("coaching_clova_pin_or_tokenizer_missing")
        else:
            if endpoint != "https://api.openai.com/v1/chat/completions" or not api_key:
                raise ValueError("coaching_openai_configuration_invalid")
            self.endpoint = endpoint
        self.provider, self.model, self.revision = provider, model, revision
        self._api_key, self.counter, self.timeout = api_key, counter, timeout
        self.maximum_input_tokens = maximum_input_tokens
        self.maximum_output_tokens = maximum_output_tokens

    def count_tokens(self, messages):
        if self.counter:
            return self.counter(messages)
        # Conservative UTF-8 byte upper bound, not an exact OpenAI token count.
        # The response's actual usage remains the provider's token accounting.
        return 64 + sum(len(m["content"].encode("utf-8")) + 16 for m in messages)

    def generate(self, messages, *, analysis_id, request_id):
        started = time.monotonic()
        headers = {"Content-Type": "application/json"}
        body = dict(model=self.model, messages=messages)
        if self.provider == "CLOVA":
            body.update(max_tokens=self.maximum_output_tokens, temperature=0,
                        chat_template_kwargs={"thinking": False})
        else:
            headers["Authorization"] = "Bearer " + self._api_key
            body.update(max_completion_tokens=self.maximum_output_tokens, reasoning_effort="low",
                        response_format={"type": "json_object"}, store=False)
        request = Request(self.endpoint, data=json.dumps(body, ensure_ascii=False, allow_nan=False).encode(),
                          headers=headers, method="POST")
        try:
            with build_opener(_NoRedirect()).open(request, timeout=self.timeout) as response:
                raw = response.read(131073)
        except HTTPError as error:
            LOG.warning("coaching_provider_error analysis=%s request=%s provider=%s http_status=%d elapsed_ms=%d",
                analysis_id, request_id, self.provider, error.code, round((time.monotonic()-started)*1000))
            raise ValueError("coaching_provider_http_failure") from None
        if len(raw) > 131072:
            raise ValueError("coaching_response_too_large")
        document = _strict_json(raw.decode("utf-8"))
        choice = document["choices"][0]
        finish = choice.get("finish_reason")
        LOG.info("coaching_provider_response analysis=%s request=%s provider=%s http_status=200 finish=%s prompt_tokens=%s completion_tokens=%s counter=%s elapsed_ms=%d",
            analysis_id, request_id, self.provider,
            finish if finish in {"stop", "length", "content_filter", "tool_calls"} else "unknown",
            _usage_count(document, "prompt_tokens"), _usage_count(document, "completion_tokens"),
            "clova_checkpoint" if self.counter else "utf8_conservative_bound", round((time.monotonic()-started)*1000))
        if choice.get("finish_reason") != "stop" or choice["message"].get("refusal"):
            raise ValueError("coaching_incomplete_or_refused")
        if self.provider == "CLOVA":
            content = _completion_content(document, expected_model=self.model, expected_revision=self.revision)
        else:
            returned = document.get("model", "")
            if returned != self.model and not returned.startswith(self.model + "-"):
                raise ValueError("coaching_model_identity_mismatch")
            content = choice["message"]["content"]
        return _strict_json(content)


def load_coaching(environment, *, repository_root):
    enabled = environment.get("AI_ANALYSIS_COACHING_ENABLED", "false")
    if enabled == "false":
        return None
    if enabled != "true" or environment.get("AI_ANALYSIS_COACHING_RESULT_CONTRACT") != "v3":
        raise ValueError("coaching_requires_explicit_v3_reader_rollout")
    provider = environment.get("AI_ANALYSIS_COACHING_PROVIDER", "CLOVA")
    if provider == "TEMPLATE":
        return EvidenceBasedCoaching(repository_root)
    args = dict(provider=provider,
        timeout=float(environment.get("AI_ANALYSIS_COACHING_TIMEOUT_SECONDS", "120")),
        maximum_input_tokens=int(environment.get("AI_ANALYSIS_COACHING_INPUT_TOKENS", "3900" if provider == "CLOVA" else "16000")),
        maximum_output_tokens=int(environment.get("AI_ANALYSIS_COACHING_OUTPUT_TOKENS", "1200")))
    if provider == "CLOVA":
        identity = json.loads((repository_root / "configs/pronunciation_feedback/hyperclovax_seed_think_32b_v1.json").read_text(encoding="utf-8"))
        tokenizer = Path(environment["AI_ANALYSIS_CLOVA_TOKENIZER_PATH"])
        if tokenizer.name != identity["revision"] or not (tokenizer / "tokenizer.json").is_file():
            raise ValueError("coaching_tokenizer_pin_mismatch")
        args.update(model=identity["modelId"], revision=identity["revision"],
            endpoint=environment.get("AI_ANALYSIS_CLOVA_ENDPOINT", "http://127.0.0.1:8000/v1"),
            counter=OfflineChatTokenCounter(tokenizer))
    elif provider == "OPENAI":
        args.update(model=environment["AI_ANALYSIS_COACHING_OPENAI_MODEL"],
            endpoint="https://api.openai.com/v1/chat/completions", api_key=environment["OPENAI_API_KEY"])
    else:
        raise ValueError("coaching_provider_invalid")
    generator = ChatCoachingGenerator(**args)
    examples = ExpertCoachingExamples(repository_root) if provider == "OPENAI" else None
    if examples is not None:
        examples.reserve_for(generator)
    return EvidenceBasedCoaching(repository_root, generator, expert_examples=examples)
