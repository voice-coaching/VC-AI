"""One GPT call grades original Seungun observations and writes coaching.

No acoustic reassessment, calibration policy, worksheet or numeric re-grading.
The parent owns original observation identity and coaching candidate projection.
"""
_SeungunBridge = Bridge


class Bridge(_SeungunBridge):
    def scoring_policy(self):
        self.integrity()
        return self.read("scoring_rubric.json")

    def prepare(self, core_bytes, script, binding):
        core, view, _, skip = super().prepare(core_bytes, script, binding)
        if view is None:
            return core, view, None, skip
        # No-correction candidates does not mean the whole recording is unscorable.
        config = self.read("request_config.json")
        messages = [dict(role="system", content=(self.root / "contracts/llm/llm_system_prompt.txt").read_text(encoding="utf-8")),
                    dict(role="user", content=encode(dict(
                        scriptText=script, SEUNGUN_OUTPUT=core,
                        RUBRIC=self.scoring_policy(), COACHING_VIEW=view)).decode("utf-8"))]
        require(sum(len(m["content"].encode("utf-8")) for m in messages) <= config["input_utf8_bound"],
                "INPUT_BUDGET_NO_SILENT_TRUNCATION")
        payload = encode(dict(model=config["model"], messages=messages,
            max_completion_tokens=config["max_completion_tokens"], reasoning_effort=config["reasoning_effort"],
            response_format=config["response_format"], store=config["store"]))
        return core, view, payload, None

    def verify_scoring(self, core, record, *, native_binding=None):
        if core["input_validation"]["status"] != "ACCEPT":
            require(record == {"status": "NOT_REQUESTED"}, "SCORING_NONACCEPT_RESULT")
            return
        # Only JSON contract/range checks; GPT's levels and total are not replaced.
        jsonschema.Draft202012Validator(self.read("llm_output.schema.json")["properties"]["scoring"]).validate(record)

    def dispatch(self, core_bytes, script, binding, *, transport, native_binding=None):
        original = strict_json(core_bytes)["input_validation"]
        base = dict(schemaVersion="canonical-llm-expression-result-v1", coreInputValidation=original,
                    coreSha256=sha(core_bytes), items=[], dispatchAttempts=0)
        try:
            core, view, payload, skip = self.prepare(core_bytes, script, binding)
        except Exception as exc:
            return dict(base, adapterStatus="FAIL_CLOSED", generationStatus="NOT_DISPATCHED",
                        errorCode=type(exc).__name__, scoring={"status": "UNSCORABLE"})
        if skip:
            return dict(base, adapterStatus=skip, generationStatus="NOT_DISPATCHED", scoring={"status": "NOT_REQUESTED"})
        fallback = None
        scoring = {"status": "UNSCORABLE"}
        try:
            response = transport(payload, timeout=self.read("request_config.json")["timeout_seconds"])
            # Parent validates actual provider model/finish reason and candidate IDs.
            rows = self.validate_response(response, view)
            scoring = strict_json(strict_json(response)["choices"][0]["message"]["content"])["scoring"]
            self.verify_scoring(core, scoring)
        except Exception as exc:
            fallback = type(exc).__name__
            rows = template_rows(view)
        items = [dict(candidate=copy.deepcopy(c), expression=copy.deepcopy(r))
                 for c, r in zip(view["candidates"], rows)]
        return dict(base, adapterStatus="READY", dispatchAttempts=1, items=items, scoring=scoring,
                    generationStatus="DETERMINISTIC_FALLBACK" if fallback else "SCHEMA_AND_STRUCTURAL_SEMANTICS_VALID",
                    fallbackReason=fallback, naturalLanguageSemanticsFullyVerified=False,
                    visualCorrectiveClaimsAllowed=False)
