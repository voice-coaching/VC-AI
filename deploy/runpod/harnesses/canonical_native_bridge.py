# Appended to the separately pinned H5 bridge by build_canonical_native.py.
from .native_gpt_rubric import score_core, verify_score_record
from .native_phone_assessment import policy_contract
from .native_phone_contract import RUBRIC_REVISION
from .native_phone_evidence import verify_archived_core
from .phone_rubric import validate_policy

_NativeCoachingBridge = Bridge


class Bridge(_NativeCoachingBridge):
    def scoring_policy(self):
        self.integrity()
        policy_contract(self.read("native_phone_assessment.json"))
        rubric = self.read("scoring_rubric.json")
        validate_policy(rubric, revision=RUBRIC_REVISION)
        return rubric

    def dispatch(self, core_bytes, script, binding, *, transport, native_binding=None):
        try:
            core, _, _, _ = self.prepare(core_bytes, script, binding)
            self.scoring_policy()
            scoring = score_core(core, binding=native_binding, policy=self.read("native_phone_assessment.json"),
                                 prompt=(self.root / "contracts/llm/scoring_system_prompt.txt").read_text(encoding="utf-8"),
                                 transport=transport)
        except Exception:
            scoring = {"status": "UNSCORABLE"}
            core = strict_json(core_bytes)
        if scoring["status"] == "UNSCORABLE":
            return dict(schemaVersion="canonical-llm-expression-result-v1", coreInputValidation=core["input_validation"],
                        coreSha256=sha(core_bytes), adapterStatus="FAIL_CLOSED", generationStatus="NOT_DISPATCHED",
                        errorCode="SCORING_UNSCORABLE", items=[], dispatchAttempts=0, scoring=scoring)
        result = super().dispatch(core_bytes, script, binding, transport=transport)
        result["scoring"] = scoring
        return result

    def verify_scoring(self, core, record, *, native_binding=None):
        self.integrity()
        verify_score_record(core, record, binding=native_binding, policy=self.read("native_phone_assessment.json"))

    def verify_archive(self, core, *, native_binding):
        self.integrity()
        if core["input_validation"]["status"] == "ACCEPT":
            verify_archived_core(core, binding=native_binding, policy=self.read("native_phone_assessment.json"))
