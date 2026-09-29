"""Optional local Clova presentation over the current attempt's approved facts.

A versioned evidence projection can accompany the selected issue. Request
identifiers, object keys, media and provenance hashes never reach the model.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import logging
import hashlib
import hmac
import json
import math
from pathlib import Path
import re

from voice_coach.pronunciation_feedback.adapters.advice_generator_hyperclova import (
    DEFAULT_MODEL_ID,
    DEFAULT_TIMEOUT_SECONDS,
    LocalHyperClovaAdviceGenerator,
    OfflineChatTokenCounter,
)
from voice_coach.pronunciation_feedback.ports import GroundedAdviceGeneratorPort
from voice_coach.pronunciation_feedback.seungun_bridge import (
    build_grounded_payload_from_seungun,
)
from voice_coach.pronunciation_feedback.video_grounded_advice import (
    generate_grounded_video_feedback,
)
from voice_coach.visual_feedback.models.integrity import canonical_json_bytes
from voice_coach.visual_feedback.models.supplement import VisualFeedbackSupplement

from voice_coach.pronunciation_feedback.feedback_evidence import build_feedback_evidence

from .domain import AnalysisOutcome, AnalysisRequest, VisualSupplement
from .ports import SeungunAnalysis


class BackendGroundedFeedback:
    """Change presentation only; retain the original result on invalid evidence."""

    def __init__(self, generator: GroundedAdviceGeneratorPort, *, full_evidence=False) -> None:
        self._generator = generator
        self._full_evidence = full_evidence

    def render(
        self,
        *,
        request: AnalysisRequest,
        analysis: SeungunAnalysis,
        visual_supplement: VisualSupplement | None,
        visual_evidence: Mapping[str, object] | None,
        overall_score=None,
        scoring_evidence=None,
        visual_state="UNAVAILABLE",
    ) -> str | None:
        if analysis.outcome is not AnalysisOutcome.COACHING_READY:
            return analysis.summary_feedback
        try:
            selected = analysis.pronunciation_evidence
            if selected is None or not isinstance(analysis.analysis_evidence, Mapping):
                return analysis.summary_feedback
            payload = build_grounded_payload_from_seungun(
                analysis=analysis.analysis_evidence,
                target_text=request.script_text,
            )
            location = payload.location
            if (
                location is None
                or location.unit_index != selected.selected_expected_index
                or location.target_unit != selected.selected_phone
            ):
                return analysis.summary_feedback
            admitted_visual = self._bound_visual_evidence(
                visual_supplement,
                visual_evidence,
                selected_index=selected.selected_expected_index,
            )
            if self._full_evidence:
                state = visual_state
                if admitted_visual is not None:
                    state = "OBSERVED_NOT_PRONUNCIATION_TRUTH"
                elif visual_evidence is not None:
                    state = "EVIDENCE_BINDING_REJECTED"
                payload = replace(payload, feedback_evidence=build_feedback_evidence(
                    analysis=analysis.analysis_evidence, target_text=request.script_text,
                    selected_index=selected.selected_expected_index,
                    visual_evidence=admitted_visual, visual_state=state,
                    overall_score=overall_score, scoring_evidence=scoring_evidence))
            feedback = generate_grounded_video_feedback(
                audio_result={
                    "status": "coaching_ready",
                    "grounded_payload": payload.to_json_object(),
                },
                visual_supplement=admitted_visual,
                generator=self._generator,
                visual_failure_reason_codes=(
                    ("visual_evidence_unavailable",)
                    if request.visual_input is not None and admitted_visual is None
                    else ()
                ),
            )
            # The shared renderer already falls back to the exact admitted
            # audio/visual claims on HTTP, model identity, or output failures.
            logging.getLogger("voice_coach.feedback_transfer").info(
                "feedback_result analysis=%s generator_status=%s fallback=%s",
                request.analysis_id, feedback.generator_status, feedback.fallback_reason)
            return feedback.message
        except Exception as error:
            logging.getLogger("voice_coach.feedback_transfer").warning(
                "feedback_result analysis=%s generator_status=catalog_fallback error=%s",
                request.analysis_id, type(error).__name__)
            # A presentation failure cannot replace the detector's outcome,
            # erase its approved advice, or leak diagnostic exception text.
            return analysis.summary_feedback

    @staticmethod
    def _bound_visual_evidence(
        public: VisualSupplement | None,
        evidence: Mapping[str, object] | None,
        *,
        selected_index: int,
    ) -> Mapping[str, object] | None:
        if public is None or evidence is None:
            return None
        try:
            parsed = VisualFeedbackSupplement.from_json_object(evidence)
            candidate = parsed.supplemental_action_candidate
            digest = hashlib.sha256(canonical_json_bytes(evidence)).hexdigest()
            if (
                public.selected_expected_index != selected_index
                or not hmac.compare_digest(digest, public.supplement_sha256)
                or candidate is None
                or candidate.approved_claim_id != public.approved_claim_id
                or candidate.renderer_key != public.renderer_key
                or candidate.upstream_phone_anchor_ref != public.upstream_phone_anchor_ref
            ):
                return None
            return evidence
        except Exception:
            return None


def load_grounded_feedback(
    environment: Mapping[str, str], *, repository_root: Path
) -> BackendGroundedFeedback | None:
    """Require explicit opt-in and the repository's immutable model identity."""

    enabled = environment.get("AI_ANALYSIS_CLOVA_ENABLED", "false").strip().lower()
    if enabled == "false":
        return None
    if enabled != "true":
        raise ValueError("analysis_clova_configuration_invalid")
    try:
        config = json.loads(
            (
                repository_root
                / "configs/pronunciation_feedback/hyperclovax_seed_think_32b_v1.json"
            ).read_text(encoding="utf-8")
        )
        model_id = config["modelId"]
        revision = config["revision"]
        if (
            config["schemaVersion"] != "voice_coach.local_llm_runtime.v1"
            or model_id != DEFAULT_MODEL_ID
            or not isinstance(revision, str)
            or re.fullmatch(r"[0-9a-f]{40}", revision) is None
        ):
            raise ValueError
        timeout = float(
            environment.get("AI_ANALYSIS_CLOVA_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
        )
        if not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise ValueError
        full_evidence = environment.get("AI_ANALYSIS_CLOVA_FULL_EVIDENCE_ENABLED", "false").lower()
        if full_evidence not in {"true", "false"}:
            raise ValueError("invalid feedback evidence switch")
        counter = None
        if full_evidence == "true":
            path = Path(environment["AI_ANALYSIS_CLOVA_TOKENIZER_PATH"])
            if path.name != revision or not (path / "tokenizer.json").is_file():
                raise ValueError("feedback tokenizer pin differs")
            counter = OfflineChatTokenCounter(path)
        generator = LocalHyperClovaAdviceGenerator(
            endpoint=environment.get("AI_ANALYSIS_CLOVA_ENDPOINT", "http://127.0.0.1:8000/v1"),
            model_id=model_id,
            revision=revision,
            timeout_seconds=timeout,
            token_counter=counter,
        )
    except (OSError, KeyError, TypeError, ValueError):
        raise ValueError("analysis_clova_configuration_invalid") from None
    return BackendGroundedFeedback(generator, full_evidence=full_evidence == "true")


__all__ = ["BackendGroundedFeedback", "load_grounded_feedback"]
