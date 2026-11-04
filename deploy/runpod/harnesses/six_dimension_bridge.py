"""Pinned service adapter: existing Seungun CORE -> six scores -> one GPT call."""
from pathlib import Path

import jsonschema

from .policy import encode, require, sha, strict_json, validate_policy
from .assessment import assess, validate_binding
from .pipeline import evaluate, request_payload, validate_coaching

PARENT = "__CORE_MANIFEST__"


class Bridge:
    def __init__(self, root, *, expected_lock_sha256):
        self.root = Path(root).resolve()
        self.expected_lock_sha256 = expected_lock_sha256

    def read(self, name):
        return strict_json((self.root / "contracts/llm" / name).read_bytes())

    def integrity(self):
        raw = (self.root / "contracts/llm/dependency_lock.json").read_bytes()
        require(sha(raw) == self.expected_lock_sha256, "DEPENDENCY_LOCK_IDENTITY")
        for relative, digest in strict_json(raw)["files"].items():
            path = (self.root / relative).resolve(strict=True)
            require(path.is_relative_to(self.root) and sha(path.read_bytes()) == digest, "DEPENDENCY_IDENTITY")

    def scoring_policy(self):
        self.integrity()
        policy = self.read("six_dimension_policy.json")
        validate_policy(policy)
        return policy

    def _configuration(self):
        policy = self.scoring_policy()
        policy_sha = sha((self.root / "contracts/llm/six_dimension_policy.json").read_bytes())
        prompt = (self.root / "contracts/llm/llm_system_prompt.txt").read_text(encoding="utf-8")
        require(self.read("request_config.json") == policy["request"], "REQUEST_POLICY_MISMATCH")
        return policy, policy_sha, prompt

    def _binding(self, core_bytes, script, binding):
        require(binding["parent_manifest_sha256"] == PARENT, "CORE_PARENT_BINDING")
        core = strict_json(core_bytes)
        jsonschema.Draft202012Validator(self.read("canonical_parent.schema.json")).validate(core)
        projected = dict(coreSha256=binding["core_sha256"], scriptSha256=binding["script_sha256"],
                         requestId=binding["request_id"], sourceAudioSha256=binding["source_audio_sha256"],
                         preparedWavSha256=binding["prepared_wav_sha256"])
        # Non-ACCEPT may have no prepared WAV; still verify original request bytes.
        require(projected["coreSha256"] == sha(core_bytes)
                and projected["scriptSha256"] == sha(script.encode("utf-8"))
                and projected["requestId"] == core["request_id"]
                and projected["sourceAudioSha256"] == core["source_audio_sha256"]
                and projected["preparedWavSha256"] == core.get("prepared_wav_sha256"), "CORE_BINDING")
        if core["input_validation"]["status"] == "ACCEPT":
            validate_binding(core_bytes, script, projected)
        return core, projected

    def prepare(self, core_bytes, script, binding, **_):
        policy, digest, prompt = self._configuration()
        core, projected = self._binding(core_bytes, script, binding)
        status = core["input_validation"]["status"]
        if status != "ACCEPT":
            return core, None, None, "GLOBAL_" + status
        assessment = assess(core, script, projected, policy, digest)
        payload = None
        if assessment["selectedCriterionIds"]:
            try:
                payload = request_payload(assessment, core, script, policy, prompt)
            except ValueError as exc:
                # dispatch/evaluate retains numeric scores when the input is over budget.
                require(str(exc) == "INPUT_BUDGET_NO_SILENT_TRUNCATION", "SIX_DIMENSION_PREPARATION")
        return core, None, payload, None

    @staticmethod
    def feedback_text(result):
        labels = {c["criterionId"]: c["label"] for c in result["assessment"]["criteria"]}
        lines = ["* " + labels[item["criterionId"]] + " → " + item["observation"] + " " + item["practice"]
                 for item in result["feedback"]]
        if lines:
            return "\n".join(lines)
        return "분석 가능한 항목의 점수는 보관했습니다. " + (
            "피드백 생성에 실패했습니다." if result["generationStatus"] in ("FAILED", "NOT_DISPATCHED")
            else "구체적인 연습을 제안할 측정 근거가 부족합니다.")

    def dispatch(self, core_bytes, script, binding, *, transport, **_):
        core = strict_json(core_bytes)
        base = dict(schemaVersion="canonical-llm-expression-result-v1", coreInputValidation=core["input_validation"],
                    coreSha256=sha(core_bytes), items=[], dispatchAttempts=0)
        try:
            _, _, _, skip = self.prepare(core_bytes, script, binding)
            if skip:
                return dict(base, adapterStatus=skip, generationStatus="NOT_DISPATCHED")
            policy, digest, prompt = self._configuration()
            _, projected = self._binding(core_bytes, script, binding)
            result = evaluate(core_bytes=core_bytes, script=script, binding=projected, policy=policy,
                              policy_sha256=digest, prompt=prompt, transport=transport)
            jsonschema.Draft202012Validator(self.read("six_dimension_result.schema.json")).validate(result)
        except Exception as exc:
            return dict(base, adapterStatus="FAIL_CLOSED", generationStatus="NOT_DISPATCHED", errorCode=type(exc).__name__)
        return dict(base, adapterStatus="READY", generationStatus=result["generationStatus"],
                    dispatchAttempts=result["dispatchAttempts"], feedback=self.feedback_text(result),
                    sixDimension=result, naturalLanguageSemanticsFullyVerified=False,
                    visualCorrectiveClaimsAllowed=False)

    def verify_result(self, core_bytes, script, binding, expression_bytes):
        expression = strict_json(expression_bytes)
        policy, digest, prompt = self._configuration()
        core, projected = self._binding(core_bytes, script, binding)
        result = expression["sixDimension"]
        jsonschema.Draft202012Validator(self.read("six_dimension_result.schema.json")).validate(result)
        expected = assess(core, script, projected, policy, digest)
        require(expression["coreInputValidation"] == core["input_validation"]
                and expression["coreSha256"] == sha(core_bytes), "SIX_DIMENSION_CORE_MISMATCH")
        require(result["assessment"] == expected, "SIX_DIMENSION_ASSESSMENT_MISMATCH")
        if not expected["selectedCriterionIds"]:
            require(result["dispatchAttempts"] == 0 and result["generationStatus"] == "NOT_REQUESTED",
                    "SIX_DIMENSION_UNAVAILABLE_DISPATCH")
        require(result["model"] == policy["request"]["model"] and result["promptSha256"] == sha(prompt.encode())
                and result["requestConfigSha256"] == sha(encode(policy["request"])), "SIX_DIMENSION_CONFIG_MISMATCH")
        if result["generationStatus"] == "STRUCTURALLY_VALID":
            validate_coaching({key: result[key] for key in ("assessments", "feedback")}, expected)
        if result["dispatchAttempts"]:
            payload = request_payload(expected, core, script, policy, prompt)
            require(result["requestSha256"] == sha(payload), "SIX_DIMENSION_REQUEST_MISMATCH")
        elif expected["selectedCriterionIds"]:
            # A scored request can be undispatched only if deterministic preparation failed.
            try:
                request_payload(expected, core, script, policy, prompt)
            except ValueError as exc:
                require(result["generationStatus"] == "NOT_DISPATCHED" and result["errorCode"] == str(exc),
                        "SIX_DIMENSION_PREPARATION_MISMATCH")
            else:
                raise ValueError("SIX_DIMENSION_DISPATCH_MISSING")
        require(expression["feedback"] == self.feedback_text(result)
                and expression["generationStatus"] == result["generationStatus"]
                and expression["dispatchAttempts"] == result["dispatchAttempts"], "SIX_DIMENSION_EXPRESSION_MISMATCH")
