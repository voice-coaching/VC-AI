"""Combine trusted Seungun and promoted visual claims for one coaching LLM call.

The LLM remains a presentation selector.  It receives neither video, audio,
landmark arrays, filesystem locators, nor receipt digests.  A visual claim is
admitted only when the production sidecar returned its exact allowlisted
``visual_feedback_ready`` action candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping

from voice_coach.visual_feedback.errors import VisualFeedbackContractError
from voice_coach.visual_feedback.models.enums import (
    VisualCueState,
    VisualFeedbackStatus,
)
from voice_coach.visual_feedback.models.p6_lip_trajectory import (
    P6_LIP_TRAJECTORY_REVISION,
    P6_VISIBLE_LIP_FEATURE_IDS,
)
from voice_coach.visual_feedback.models.supplement import VisualFeedbackSupplement

from .coaching_payload import CoachingPayloadError
from .grounded_advice import (
    ApprovedAdviceClaim,
    GroundedCoachingPayload,
    render_grounded_advice_message,
    validate_grounded_advice_plan,
)
from .ports import GroundedAdviceGeneratorPort


_VISUAL_CLAIM_TEXT = {
    (
        "increase_visible_lip_opening",
        "visible_lip_opening_below_reference_v1",
        "visual_lip_opening_increase",
    ): "표시된 음소 구간에서 입술을 조금 더 벌려 다시 발음해 보세요.",
    (
        "reduce_visible_lip_opening",
        "visible_lip_opening_above_reference_v1",
        "visual_lip_opening_reduce",
    ): "표시된 음소 구간에서 입술 벌림을 조금 줄여 다시 발음해 보세요.",
}

_VISUAL_FAILURE_TEXT = {
    "production_evidence_unavailable": (
        "입술 피드백에 필요한 보정 및 평가 근거가 아직 없어, "
        "이번에는 음성 분석을 기준으로 안내해요."
    ),
    "production_visual_release_unavailable": (
        "입술 피드백 설정 파일을 불러오지 못해, 이번에는 음성 분석을 "
        "기준으로 안내해요."
    ),
    "production_visual_release_not_private": (
        "입술 피드백 설정의 보안 권한을 확인하지 못해, 이번에는 음성 분석을 "
        "기준으로 안내해요."
    ),
    "production_visual_release_digest_mismatch": (
        "입술 피드백 설정의 무결성 확인에 실패해, 이번에는 음성 분석을 "
        "기준으로 안내해요."
    ),
    "production_visual_release_policy_mismatch": (
        "입술 피드백의 품질 기준이 현재 설정과 맞지 않아, 이번에는 음성 "
        "분석을 기준으로 안내해요."
    ),
    "skipped_no_selected_phone": (
        "입술 모양을 확인할 발음 구간을 정하지 못해, 이번에는 음성 분석을 "
        "기준으로 안내해요."
    ),
    "video_decode_failed": (
        "입술 모양을 확인할 영상 프레임을 읽지 못해, 이번에는 음성 분석을 "
        "기준으로 안내해요."
    ),
    "detector_failed": (
        "영상에서 입술 위치를 안정적으로 찾지 못해, 이번에는 음성 분석을 "
        "기준으로 안내해요."
    ),
    "blocked_reliability": (
        "영상 품질이나 음성과 영상의 맞춤 상태가 기준을 통과하지 못해, "
        "이번에는 음성 분석을 기준으로 안내해요."
    ),
    "reliability_failed": (
        "입술 피드백의 신뢰도 확인을 완료하지 못해, 이번에는 음성 분석을 "
        "기준으로 안내해요."
    ),
}
_VISUAL_FAILURE_CLAIM_ID = "VISUAL_FEEDBACK_UNAVAILABLE_REASON"
_CLOSED_BETA_CLAIM_ID = "CLOSED_BETA_VISIBLE_LIP_GEOMETRY_OBSERVATION_V1"
_CLOSED_BETA_REASON = "closed_beta_unvalidated_observation"
_CLOSED_BETA_NOTICE = "closed_beta_lip_trajectory_observed"
_CLOSED_BETA_TEMPORAL_CUE = "shape_change_rate_per_second"
_CLOSED_BETA_CUE_UNITS = {
    "mouth_width_fraction": "image_fraction",
    "outer_aperture_ratio": "ratio",
    "inner_aperture_ratio": "ratio",
    "outer_area_ratio": "ratio",
    "inner_area_ratio": "ratio",
    "corner_height_asymmetry_ratio": "ratio",
    _CLOSED_BETA_TEMPORAL_CUE: "normalized_per_second",
}


@dataclass(frozen=True, slots=True)
class GroundedVideoFeedback:
    """Privacy-safe combined presentation result for one video attempt."""

    message: str
    payload: GroundedCoachingPayload
    modalities: tuple[str, ...]
    visual_claim_status: str
    generator_status: str
    generator_id: str | None
    generator_revision: str | None
    fallback_reason: str | None


def _audio_payload(audio_result: Mapping[str, object]) -> GroundedCoachingPayload:
    if audio_result.get("status") != "coaching_ready":
        raise CoachingPayloadError("video attempt has no coaching-ready audio result")
    return GroundedCoachingPayload.from_json_object(
        audio_result.get("grounded_payload")
    )


def _visual_claims(
    visual_supplement: Mapping[str, object] | None,
) -> tuple[tuple[ApprovedAdviceClaim, ...], str]:
    if visual_supplement is None:
        return (), "not_available"
    try:
        supplement = VisualFeedbackSupplement.from_json_object(visual_supplement)
    except VisualFeedbackContractError:
        return (), "invalid_supplement"

    claims: list[ApprovedAdviceClaim] = []
    if supplement.status is VisualFeedbackStatus.VISUAL_FEEDBACK_READY:
        candidate = supplement.supplemental_action_candidate
        if candidate is None:  # guarded by contract; defensive only
            return (), "invalid_supplement"
        key = (
            candidate.action_id,
            candidate.approved_claim_id,
            candidate.renderer_key,
        )
        text = _VISUAL_CLAIM_TEXT.get(key)
        if text is None:
            return (), "claim_renderer_not_allowlisted"
        action_claim = _visual_action_claim(supplement, action_text=text)
        if action_claim is None:
            return (), "action_evidence_not_admissible"
        claims.append(action_claim)

    beta_claim = _closed_beta_observation_claim(supplement)
    if beta_claim is not None:
        claims.append(beta_claim)
    if not claims:
        return (), supplement.status.value
    return (
        tuple(claims),
        "admitted" if len(claims) > 1 else (
            "closed_beta_observation_admitted"
            if beta_claim is not None
            else "admitted"
        ),
    )


def _video_span_text(start_ms: int, end_ms: int) -> str:
    return f"영상 {start_ms / 1000:.3f}~{end_ms / 1000:.3f}초"


def _visual_action_claim(
    supplement: VisualFeedbackSupplement,
    *,
    action_text: str,
) -> ApprovedAdviceClaim | None:
    """Keep the selected action bound to its measured video evidence.

    The reference artifacts authorize the existing action only. Their values
    are not an ideal lip shape, a pronunciation score, or a cause of the
    acoustic error. Do not derive another correction from this measurement.
    """

    candidate = supplement.supplemental_action_candidate
    if candidate is None:
        return None
    evidence = tuple(
        item
        for item in supplement.cue_evidence
        if item.evidence_id in candidate.supporting_visual_evidence_ids
    )
    if len(evidence) != 1:
        return None
    cue = evidence[0]
    if (
        cue.cue_id != "lip_aperture_ratio"
        or cue.cue_state is not VisualCueState.OBSERVED
        or cue.measurement_value is None
        or cue.measurement_value < 0.0
        or cue.measurement_unit != "ratio"
        or cue.calibrated_deviation is None
        or cue.calibration_artifact_sha256 is None
        or cue.video_start_ms is None
        or cue.video_end_ms is None
    ):
        return None
    span = _video_span_text(cue.video_start_ms, cue.video_end_ms)
    measurement = f"입 너비 대비 벌림 비율은 {cue.measurement_value:.3f}"
    action = (
        "입술을 조금 더 벌려"
        if candidate.action_id == "increase_visible_lip_opening"
        else "입술 벌림을 조금 줄여"
    )
    return ApprovedAdviceClaim(
        candidate.approved_claim_id,
        f"{span}에 대응해 측정한 {measurement}예요. {action_text} "
        "같은 구간을 다시 촬영해 입술 모양과 소리를 함께 비교해 보세요.",
        required_fragments=(span, measurement, action, "다시 촬영"),
    )


def _closed_beta_observation_claim(
    supplement: VisualFeedbackSupplement,
) -> ApprovedAdviceClaim | None:
    if _CLOSED_BETA_NOTICE not in supplement.notices:
        return None
    allowed_ids = {*P6_VISIBLE_LIP_FEATURE_IDS, _CLOSED_BETA_TEMPORAL_CUE}
    beta_evidence = tuple(
        evidence
        for evidence in supplement.cue_evidence
        if evidence.cue_id in allowed_ids
    )
    cue_values = {evidence.cue_id: evidence for evidence in beta_evidence}
    if (
        len(cue_values) != len(beta_evidence)
        or not set(P6_VISIBLE_LIP_FEATURE_IDS).issubset(cue_values)
        or set(cue_values) - allowed_ids
    ):
        return None
    artifact_digests = {
        evidence.calibration_artifact_sha256 for evidence in beta_evidence
    }
    spans = {
        (evidence.video_start_ms, evidence.video_end_ms)
        for evidence in beta_evidence
    }
    if (
        len(artifact_digests) != 1
        or None in artifact_digests
        or len(spans) != 1
        or any(start is None or end is None for start, end in spans)
    ):
        return None
    for evidence in beta_evidence:
        backend = evidence.measurement_backend
        if (
            evidence.cue_state is not VisualCueState.OBSERVED
            or evidence.measurement_value is None
            or evidence.measurement_value < 0.0
            or evidence.measurement_unit != _CLOSED_BETA_CUE_UNITS[evidence.cue_id]
            or evidence.calibrated_deviation is not None
            or evidence.reason_codes != (_CLOSED_BETA_REASON,)
            or backend.backend_id != "closed_beta_p6_lip_trajectory"
            or backend.backend_revision != P6_LIP_TRAJECTORY_REVISION
            or backend.status != "observed"
            or backend.reason_codes != (_CLOSED_BETA_REASON,)
        ):
            return None

    value = {
        cue_id: float(cue_values[cue_id].measurement_value)
        for cue_id in P6_VISIBLE_LIP_FEATURE_IDS
    }
    start_ms, end_ms = next(iter(spans))
    assert start_ms is not None and end_ms is not None
    span = _video_span_text(start_ms, end_ms)
    normalized = "입 너비를 1로 환산한"
    outer = f"바깥 벌림 {value['outer_aperture_ratio']:.3f}"
    inner = f"안쪽 벌림 {value['inner_aperture_ratio']:.3f}"
    corners = f"좌우 입꼬리 높이 차이 {value['corner_height_asymmetry_ratio']:.3f}"
    limitation = "발음 정답이나 새 교정 방향을 판정한 값은 아니에요"
    parts = [
        f"{span}의 입술 윤곽 관찰 평균은 {normalized} {outer}, {inner}, {corners}예요.",
        "입 너비의 제곱을 기준으로 한 "
        f"바깥 면적 {value['outer_area_ratio']:.3f}, "
        f"안쪽 면적 {value['inner_area_ratio']:.3f}이며, "
        "영상의 정규화 좌표로 잰 "
        f"입 너비는 {value['mouth_width_fraction']:.3f}예요.",
    ]
    temporal = cue_values.get(_CLOSED_BETA_TEMPORAL_CUE)
    if temporal is not None and temporal.measurement_value is not None:
        parts.append(
            "각 프레임을 직전 프레임과 비교한 정규화 모양 변화율의 최댓값은 "
            f"{temporal.measurement_value:.3f}/초예요. "
            "첫 프레임은 구간 직전 프레임과 비교할 수 있어, "
            "이 수치만으로 입술을 여닫은 시점이나 빠르기를 판정하지 않아요."
        )
    parts.append(f"이 값은 베타 관찰값이며 {limitation}.")
    return ApprovedAdviceClaim(
        _CLOSED_BETA_CLAIM_ID,
        " ".join(parts),
        required_fragments=(span, "관찰 평균", normalized, outer, inner, corners, limitation),
    )


def build_grounded_video_payload(
    *,
    audio_result: Mapping[str, object],
    visual_supplement: Mapping[str, object] | None,
    visual_failure_reason_codes: tuple[str, ...] = (),
) -> tuple[GroundedCoachingPayload, tuple[str, ...], str]:
    """Admit only validated public claims into a combined coaching payload."""

    audio = _audio_payload(audio_result)
    visual_claims, visual_status = _visual_claims(visual_supplement)
    if not visual_claims:
        if visual_failure_reason_codes:
            reason_text = next(
                (
                    _VISUAL_FAILURE_TEXT[code]
                    for code in visual_failure_reason_codes
                    if code in _VISUAL_FAILURE_TEXT
                ),
                "입술 피드백을 안전하게 확인하지 못해, 이번에는 음성 분석을 기준으로 안내해요.",
            )
            return (
                replace(
                    audio,
                    approved_claims=(
                        *audio.approved_claims,
                        ApprovedAdviceClaim(_VISUAL_FAILURE_CLAIM_ID, reason_text),
                    ),
                ),
                ("audio",),
                visual_status,
            )
        return audio, ("audio",), visual_status
    audio_claim_ids = {claim.claim_id for claim in audio.approved_claims}
    visual_claim_ids = {claim.claim_id for claim in visual_claims}
    if (
        len(visual_claim_ids) != len(visual_claims)
        or audio_claim_ids & visual_claim_ids
    ):
        raise CoachingPayloadError("audio and visual claim identities overlap")
    return (
        replace(
            audio,
            approved_claims=(*audio.approved_claims, *visual_claims),
        ),
        ("audio", "visual"),
        visual_status,
    )


def generate_grounded_video_feedback(
    *,
    audio_result: Mapping[str, object],
    visual_supplement: Mapping[str, object] | None,
    generator: GroundedAdviceGeneratorPort | None,
    visual_failure_reason_codes: tuple[str, ...] = (),
) -> GroundedVideoFeedback:
    """Select presentation with Clova, falling back to the exact admitted claims."""

    payload, modalities, visual_status = build_grounded_video_payload(
        audio_result=audio_result,
        visual_supplement=visual_supplement,
        visual_failure_reason_codes=visual_failure_reason_codes,
    )
    source_message = " ".join(claim.text for claim in payload.approved_claims)
    if generator is None:
        return GroundedVideoFeedback(
            message=source_message,
            payload=payload,
            modalities=modalities,
            visual_claim_status=visual_status,
            generator_status="catalog_fallback",
            generator_id=None,
            generator_revision=None,
            fallback_reason="generator_not_configured",
        )
    try:
        plan = generator.generate(payload)
        validate_grounded_advice_plan(payload=payload, plan=plan)
        allowed = {claim.claim_id for claim in payload.approved_claims}
        if set(plan.selected_claim_ids) != allowed:
            raise CoachingPayloadError(
                "combined grounded plan must preserve every admitted claim"
            )
        message = render_grounded_advice_message(payload=payload, plan=plan)
        failure_claims = tuple(
            claim.text
            for claim in payload.approved_claims
            if claim.claim_id == _VISUAL_FAILURE_CLAIM_ID
        )
        if any(text not in message for text in failure_claims):
            raise CoachingPayloadError(
                "combined grounded plan omits the visual failure reason"
            )
    except Exception as error:  # noqa: BLE001 - exact-claim fallback is mandatory
        return GroundedVideoFeedback(
            message=source_message,
            payload=payload,
            modalities=modalities,
            visual_claim_status=visual_status,
            generator_status="catalog_fallback",
            generator_id=generator.generator_id,
            generator_revision=generator.revision,
            fallback_reason=type(error).__name__,
        )
    return GroundedVideoFeedback(
        message=message,
        payload=payload,
        modalities=modalities,
        visual_claim_status=visual_status,
        generator_status="validated_grounded_plan",
        generator_id=generator.generator_id,
        generator_revision=generator.revision,
        fallback_reason=None,
    )


__all__ = [
    "GroundedVideoFeedback",
    "build_grounded_video_payload",
    "generate_grounded_video_feedback",
]
