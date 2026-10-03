# Appended to the separately pinned H5 adapter by build_canonical_scored.py.
from .gpt_rubric import score_core, verify_score_record
from .phone_rubric import validate_policy

_CoachingBridge = Bridge


class Bridge(_CoachingBridge):
    def scoring_policy(self):
        self.integrity()
        policy = self.read("scoring_rubric.json")
        validate_policy(policy)
        return policy

    def dispatch(self, core_bytes, script, binding, *, transport):
        try:
            core, _, _, _ = self.prepare(core_bytes, script, binding)
        except Exception:
            result = super().dispatch(core_bytes, script, binding, transport=transport)
            result["scoring"] = dict(status="UNSCORABLE")
            return result
        scoring = score_core(core,
            evidence_units=self.scoring_policy()["evidenceUnits"],
            prompt=(self.root / "contracts/llm/scoring_system_prompt.txt").read_text(encoding="utf-8"),
            transport=transport)
        if scoring["status"] == "UNSCORABLE":
            return dict(schemaVersion="canonical-llm-expression-result-v1", coreInputValidation=core["input_validation"],
                          coreSha256=sha(core_bytes), adapterStatus="FAIL_CLOSED",
                          generationStatus="NOT_DISPATCHED", errorCode="SCORING_UNSCORABLE",
                          items=[], dispatchAttempts=0, scoring=scoring)
        result = super().dispatch(core_bytes, script, binding, transport=transport)
        result["scoring"] = scoring
        return result

    def verify_scoring(self, core, record):
        self.integrity()
        verify_score_record(core, record, evidence_units=self.scoring_policy()["evidenceUnits"])
