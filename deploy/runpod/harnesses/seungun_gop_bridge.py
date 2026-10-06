"""One GPT request: complete Native GOP evidence, one rubric, Korean feedback."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema

PARENT = "__CORE_MANIFEST__"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, code):
    if not condition:
        raise ValueError(code)


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    def invalid(_):
        raise ValueError("NONFINITE_JSON")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


class Bridge:
    def __init__(self, root, *, expected_lock_sha256):
        self.root = Path(root).resolve()
        self.expected_lock_sha256 = expected_lock_sha256

    def integrity(self):
        raw = (self.root / "contracts/llm/dependency_lock.json").read_bytes()
        require(sha(raw) == self.expected_lock_sha256, "DEPENDENCY_LOCK_IDENTITY")
        for rel, digest in strict_json(raw)["files"].items():
            path = (self.root / rel).resolve()
            require(path.is_relative_to(self.root) and sha(path.read_bytes()) == digest, "DEPENDENCY_IDENTITY")

    def read(self, name):
        return strict_json((self.root / "contracts/llm" / name).read_bytes())

    def scoring_policy(self):
        self.integrity()
        return self.read("scoring_rubric.json")

    def prepare(self, core_bytes, script, binding):
        self.integrity()
        core = strict_json(core_bytes)
        jsonschema.Draft202012Validator(self.read("canonical_parent.schema.json")).validate(core)
        require(binding["parent_manifest_sha256"] == PARENT and binding["core_sha256"] == sha(core_bytes), "CORE_BINDING")
        require(binding["script_sha256"] == sha(script.encode("utf-8")), "SCRIPT_BINDING")
        require(binding["request_id"] == core["request_id"] and binding["source_audio_sha256"] == core["source_audio_sha256"], "REQUEST_BINDING")
        require(binding["prepared_wav_sha256"] == core["prepared_wav_sha256"], "PREPARED_BINDING")
        status = core["input_validation"]["status"]
        if status != "ACCEPT":
            return core, None, None, "GLOBAL_" + status
        # No candidate permission, calibration approval, frame-region, or score gate.
        # Original P1 observations remain in retained CORE, not a second GPT policy.
        data = dict(scriptText=script, SEUNGUN_OUTPUT=dict(
            phonemeAssessment=core.get("phonemeAssessment"), native=core["native"]),
            RUBRIC=self.read("scoring_rubric.json"))
        config = self.read("request_config.json")
        messages = [dict(role="system", content=(self.root / "contracts/llm/llm_system_prompt.txt").read_text(encoding="utf-8")),
                    dict(role="user", content=encode(data).decode("utf-8"))]
        require(sum(len(m["content"].encode("utf-8")) for m in messages) <= config["input_utf8_bound"], "INPUT_BUDGET_NO_SILENT_TRUNCATION")
        payload = encode(dict(model=config["model"], messages=messages,
            max_completion_tokens=config["max_completion_tokens"], reasoning_effort=config["reasoning_effort"],
            response_format=config["response_format"], store=config["store"]))
        return core, None, payload, None

    def verify_scoring(self, core, record, **_):
        if core["input_validation"]["status"] != "ACCEPT":
            require(record == {"status": "NOT_REQUESTED"}, "SCORING_NONACCEPT_RESULT")
        else:
            jsonschema.Draft202012Validator(self.read("llm_output.schema.json")["properties"]["scoring"]).validate(record)

    def dispatch(self, core_bytes, script, binding, *, transport, **_):
        core = strict_json(core_bytes)
        base = dict(schemaVersion="canonical-llm-expression-result-v1", coreInputValidation=core["input_validation"],
                    coreSha256=sha(core_bytes), items=[], dispatchAttempts=0)
        try:
            _, _, payload, skip = self.prepare(core_bytes, script, binding)
        except Exception as exc:
            return dict(base, adapterStatus="FAIL_CLOSED", generationStatus="NOT_DISPATCHED",
                        errorCode=type(exc).__name__, scoring={"status": "UNSCORABLE"})
        if skip:
            return dict(base, adapterStatus=skip, generationStatus="NOT_DISPATCHED", scoring={"status": "NOT_REQUESTED"})
        fallback = None
        result = dict(scoring={"status": "UNSCORABLE"}, feedback="분석 근거는 보관되었지만 GPT 응답을 생성하지 못했습니다. 점수와 발음 교정은 제공할 수 없습니다.")
        try:
            config = self.read("request_config.json")
            response = strict_json(transport(payload, timeout=config["timeout_seconds"]))
            require(response.get("model") == config["model"] or
                    isinstance(response.get("model"), str) and response["model"].startswith(config["model"] + "-"), "PROVIDER_MODEL")
            require(len(response["choices"]) == 1, "PROVIDER_CHOICES")
            choice = response["choices"][0]
            require(choice["finish_reason"] == "stop" and not choice["message"].get("refusal"), "PROVIDER_COMPLETION")
            candidate = strict_json(choice["message"]["content"])
            jsonschema.Draft202012Validator(self.read("llm_output.schema.json")).validate(candidate)
            self.verify_scoring(core, candidate["scoring"])
            result = candidate
        except Exception as exc:
            fallback = type(exc).__name__
        return dict(base, adapterStatus="READY", dispatchAttempts=1, **result,
                    generationStatus="DETERMINISTIC_FALLBACK" if fallback else "SCHEMA_AND_STRUCTURAL_SEMANTICS_VALID",
                    fallbackReason=fallback, naturalLanguageSemanticsFullyVerified=False,
                    visualCorrectiveClaimsAllowed=False)
