"""A single grounded Clova boundary combines audio and promoted visual claims."""

from __future__ import annotations

from voice_coach.pronunciation_feedback.grounded_advice import (
    GROUNDED_INPUT_SCHEMA_VERSION,
    GROUNDED_OUTPUT_SCHEMA_VERSION,
    ApprovedAdviceClaim,
    CandidateDisclosure,
    GroundedAdvicePlan,
    GroundedAdviceStyle,
    GroundedCoachingPayload,
    GroundedLocation,
    GroundedPlanStatus,
)
from voice_coach.pronunciation_feedback.video_grounded_advice import (
    generate_grounded_video_feedback,
)
from voice_coach.visual_feedback.models.enums import (
    VisualCueState,
    VisualEvidenceRelation,
    VisualFeedbackStatus,
)
from voice_coach.visual_feedback.models.evidence import (
    AdapterReceipt,
    MediaDeletionReceipt,
)
from voice_coach.visual_feedback.models.supplement import (
    SupplementalVisualActionCandidate,
    VisualCueEvidence,
    VisualFeedbackSupplement,
)


AUDIO_CLAIM_ID = "PHONE_RETRY_RULE_001:SOURCE_MESSAGE"
VISUAL_CLAIM_ID = "visible_lip_opening_below_reference_v1"


def _audio_payload() -> GroundedCoachingPayload:
    return GroundedCoachingPayload(
        schema_version=GROUNDED_INPUT_SCHEMA_VERSION,
        target_text="쌀을 깨끗이 씻어요",
        issue_id="phone-0",
        issue_type="phone_pronunciation_error",
        location=GroundedLocation(
            "phone",
            0,
            "ㅆ",
            120,
            240,
            syllable_index=0,
            syllable_unit="쌀",
            word_unit="쌀을",
            learner_anchor="‘쌀을’의 첫 번째 음절 ‘쌀’",
        ),
        observed_candidate=None,
        observed_candidate_state="not_available",
        evidence_state="frozen_detector_threshold_passed",
        severity=None,
        confidence=None,
        detector_score=0.91,
        operating_threshold=0.75,
        score_semantics=(
            "detector_ranking_score_not_calibrated_correctness_confidence"
        ),
        advice_id="PHONE_RETRY_RULE_001",
        approved_claims=(
            ApprovedAdviceClaim(
                AUDIO_CLAIM_ID,
                "‘쌀을’의 첫 번째 음절 ‘쌀’에 해당하는 목표 음소 ‘ㅆ’ 소리를 "
                "천천히 다시 발음해 보세요.",
            ),
        ),
        claim_scope="engineering_provisional",
    )


def _audio_result() -> dict[str, object]:
    return {
        "status": "coaching_ready",
        "grounded_payload": _audio_payload().to_json_object(),
    }


def _receipt(backend_id: str) -> AdapterReceipt:
    return AdapterReceipt(backend_id, "v1", "observed")


def _visual_supplement() -> dict[str, object]:
    evidence = VisualCueEvidence(
        evidence_id="d" * 64,
        cue_id="lip_aperture_ratio",
        cue_state=VisualCueState.OBSERVED,
        video_start_ms=130,
        video_end_ms=250,
        measurement_value=0.2,
        measurement_unit="ratio",
        calibrated_deviation=-3.1,
        measurement_backend=_receipt("dense_phone_window_center_cue"),
        calibration_artifact_sha256="e" * 64,
    )
    supplement = VisualFeedbackSupplement(
        status=VisualFeedbackStatus.VISUAL_FEEDBACK_READY,
        reason_codes=("calibrated_visible_cue_supports_upstream",),
        upstream_analysis_ref="a" * 64,
        upstream_binding_ref="b" * 64,
        upstream_phone_anchor_ref="c" * 64,
        cue_evidence=(evidence,),
        evidence_relation=VisualEvidenceRelation.SUPPORTS_UPSTREAM,
        supplemental_action_candidate=SupplementalVisualActionCandidate(
            action_id="increase_visible_lip_opening",
            approved_claim_id=VISUAL_CLAIM_ID,
            catalog_revision="visible_only_two_direction_v2",
            renderer_key="visual_lip_opening_increase",
            upstream_phone_anchor_ref="c" * 64,
            supporting_visual_evidence_ids=(evidence.evidence_id,),
        ),
        visual_backend_receipts=(_receipt("mediapipe"),),
        reference_calibration_receipts=(_receipt("p6_calibration"),),
        media_deletion_receipt=MediaDeletionReceipt(
            status="not_applicable_no_raw_media",
            deletion_receipt_sha256="f" * 64,
            backend_receipt=_receipt("restricted_media_cleanup"),
        ),
        notices=("visible_lip_cue_only",),
    )
    return supplement.to_json_object()


class _Generator:
    generator_id = "fixed-clova"
    revision = "pinned-revision"

    def __init__(self, *, include_visual: bool = True) -> None:
        self.include_visual = include_visual

    def generate(self, payload: GroundedCoachingPayload) -> GroundedAdvicePlan:
        selected = tuple(claim.claim_id for claim in payload.approved_claims)
        if not self.include_visual:
            selected = selected[:1]
        return GroundedAdvicePlan(
            schema_version=GROUNDED_OUTPUT_SCHEMA_VERSION,
            status=GroundedPlanStatus.RENDERED,
            issue_id=payload.issue_id,
            advice_id=payload.advice_id,
            selected_claim_ids=selected,
            style=GroundedAdviceStyle.FOCUS,
            candidate_disclosure=CandidateDisclosure.OMIT,
        )


class _GeneratorOmittingFailureText(_Generator):
    def generate(self, payload: GroundedCoachingPayload) -> GroundedAdvicePlan:
        plan = super().generate(payload)
        return GroundedAdvicePlan(
            schema_version=plan.schema_version,
            status=plan.status,
            issue_id=plan.issue_id,
            advice_id=plan.advice_id,
            selected_claim_ids=plan.selected_claim_ids,
            style=plan.style,
            candidate_disclosure=plan.candidate_disclosure,
            learner_message=(
                "‘쌀을’의 첫 번째 음절 ‘쌀’에 해당하는 ‘ㅆ’ 소리를 천천히 "
                "다시 발음한 뒤 원래 속도로 다시 녹음해 보세요."
            ),
        )


def test_combines_audio_and_promoted_visual_claims_for_one_clova_call() -> None:
    result = generate_grounded_video_feedback(
        audio_result=_audio_result(),
        visual_supplement=_visual_supplement(),
        generator=_Generator(),
    )

    assert result.modalities == ("audio", "visual")
    assert result.payload.claim_scope == "engineering_provisional"
    assert result.visual_claim_status == "admitted"
    assert result.generator_status == "validated_grounded_plan"
    assert "목표 음소" in result.message
    assert "입술을 조금 더 벌려" in result.message
    serialized = result.payload.to_json()
    for forbidden in ("/data/", ".mp4", "landmarks", "upstream_analysis_ref"):
        assert forbidden not in serialized


def test_invalid_or_unready_visual_data_never_reaches_clova_payload() -> None:
    result = generate_grounded_video_feedback(
        audio_result=_audio_result(),
        visual_supplement={"status": "visual_feedback_ready"},
        generator=_Generator(),
    )

    assert result.modalities == ("audio",)
    assert result.visual_claim_status == "invalid_supplement"
    assert [claim.claim_id for claim in result.payload.approved_claims] == [
        AUDIO_CLAIM_ID
    ]


def test_clova_may_not_drop_an_admitted_visual_claim() -> None:
    result = generate_grounded_video_feedback(
        audio_result=_audio_result(),
        visual_supplement=_visual_supplement(),
        generator=_Generator(include_visual=False),
    )

    assert result.generator_status == "catalog_fallback"
    assert result.fallback_reason == "CoachingPayloadError"
    assert "목표 음소" in result.message
    assert "입술을 조금 더 벌려" in result.message


def test_clova_answer_includes_safe_production_visual_failure_reason() -> None:
    result = generate_grounded_video_feedback(
        audio_result=_audio_result(),
        visual_supplement=None,
        generator=_Generator(),
        visual_failure_reason_codes=("production_evidence_unavailable",),
    )

    assert result.modalities == ("audio",)
    assert result.generator_status == "validated_grounded_plan"
    assert "보정 및 평가 근거가 아직 없어" in result.message
    assert [claim.claim_id for claim in result.payload.approved_claims][-1] == (
        "VISUAL_FEEDBACK_UNAVAILABLE_REASON"
    )


def test_clova_failure_reason_omission_uses_exact_claim_fallback() -> None:
    result = generate_grounded_video_feedback(
        audio_result=_audio_result(),
        visual_supplement=None,
        generator=_GeneratorOmittingFailureText(),
        visual_failure_reason_codes=("production_evidence_unavailable",),
    )

    assert result.generator_status == "catalog_fallback"
    assert result.fallback_reason == "CoachingPayloadError"
    assert "보정 및 평가 근거가 아직 없어" in result.message
