"""Optional local Clova presentation over the current attempt's approved facts.

Only the existing grounded payload reaches the LLM. Request identifiers, grants,
object keys, media, and the full visual supplement never enter that payload.
"""

from __future__ import annotations

from collections.abc import Mapping
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

from .domain import AnalysisOutcome, AnalysisRequest, VisualSupplement
from .ports import SeungunAnalysis


class BackendGroundedFeedback:
    """Change presentation only; retain the original result on invalid evidence."""

    def __init__(self, generator: GroundedAdviceGeneratorPort) -> None:
        self._generator = generator

    def render(
        self,
        *,
        request: AnalysisRequest,
        analysis: SeungunAnalysis,
        visual_supplement: VisualSupplement | None,
        visual_evidence: Mapping[str, object] | None,
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
            return feedback.message
        except Exception:
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
        generator = LocalHyperClovaAdviceGenerator(
            endpoint=environment.get("AI_ANALYSIS_CLOVA_ENDPOINT", "http://127.0.0.1:8000/v1"),
            model_id=model_id,
            revision=revision,
            timeout_seconds=timeout,
        )
    except (OSError, KeyError, TypeError, ValueError):
        raise ValueError("analysis_clova_configuration_invalid") from None
    return BackendGroundedFeedback(generator)


__all__ = ["BackendGroundedFeedback", "load_grounded_feedback"]
