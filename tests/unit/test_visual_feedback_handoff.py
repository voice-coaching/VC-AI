"""Production handoff contract for completed, immutable Seungun analyses."""

from __future__ import annotations

from inspect import Parameter, signature

import pytest

from voice_coach.visual_feedback.adapters.seungun import (
    FrozenSeungunPronunciationEvidenceAdapter,
    InMemorySeungunAnalysisSource,
)
from voice_coach.visual_feedback.errors import (
    SeungunEvidenceUnavailableError,
    VisualFeedbackContractError,
)
from voice_coach.visual_feedback.models.binding import (
    ConsentReceipt,
    MediaTimebaseReceipt,
    RestrictedVideoHandle,
    SameAttemptBindingReceipt,
    UpstreamReliabilityReceipt,
)
from voice_coach.visual_feedback.models.enums import (
    ConsentDecision,
    UpstreamReliabilityStatus,
)
from voice_coach.visual_feedback.models.evidence import (
    SEUNGUN_ANALYSIS_SCHEMA_VERSION,
    SEUNGUN_SCORE_SEMANTICS,
)
from voice_coach.visual_feedback.models.integrity import sha256_document
from voice_coach.visual_feedback.services.handoff import (
    SeungunAttemptBindingFacade,
)


class CompletedAnalysis:
    """Structural double for the concrete Seungun result, never its detector."""

    def __init__(self, document: dict[str, object]) -> None:
        self.document = document
        self.serialization_count = 0

    def to_json_object(self) -> dict[str, object]:
        self.serialization_count += 1
        return self.document

    def analyze(self) -> None:  # pragma: no cover - must never be invoked
        raise AssertionError("the handoff facade must not run a detector")


def test_completed_analysis_is_snapshotted_once_and_resolves_without_detector() -> None:
    document = _analysis_document()
    completed = CompletedAnalysis(document)
    derivation_receipt = _sha("audio_derivation")

    handoff = SeungunAttemptBindingFacade().bind_completed_analysis(
        analysis=completed,
        audio_derivation_receipt_sha256=derivation_receipt,
        **_handoff_arguments(),
    )

    assert completed.serialization_count == 1
    assert handoff.source_record.analysis is not document
    assert (
        handoff.source_record.binding_receipt_sha256
        == handoff.request.binding_receipt.receipt_sha256
    )
    assert handoff.source_record.audio_derivation_receipt_sha256 == derivation_receipt
    assert (
        handoff.source_record.reliability_receipt.receipt_sha256
        == handoff.request.binding_receipt.upstream_reliability_receipt_sha256
    )

    phones = document["phones"]
    assert isinstance(phones, list)
    phone = phones[0]
    assert isinstance(phone, dict)
    soft_features = phone["soft_features"]
    assert isinstance(soft_features, dict)
    soft_features["caller_mutation"] = 0.5

    adapter = _adapter_for(handoff)
    snapshot = adapter.resolve(
        analysis_ref=handoff.request.analysis_ref,
        binding_receipt=handoff.request.binding_receipt,
        video_handle=handoff.request.video_handle,
    )

    assert snapshot.phone_anchors[0].expected_phone == "ㅏ"
    assert snapshot.phone_anchors[0].error_score == pytest.approx(0.91)
    stored_phones = handoff.source_record.analysis["phones"]
    assert isinstance(stored_phones, list)
    stored_phone = stored_phones[0]
    assert isinstance(stored_phone, dict)
    assert stored_phone["soft_features"] == {}


def test_adapter_rejects_a_valid_but_unregistered_reissued_binding() -> None:
    handoff = SeungunAttemptBindingFacade().bind_completed_analysis(
        analysis=_analysis_document(),
        audio_derivation_receipt_sha256=_sha("audio_derivation"),
        **_handoff_arguments(),
    )
    original = handoff.request.binding_receipt
    reissued = SameAttemptBindingReceipt.issue(
        attempt_id_sha256=original.attempt_id_sha256,
        prompt_id_sha256=original.prompt_id_sha256,
        prompt_revision=original.prompt_revision,
        expected_phone_sha256=original.expected_phone_sha256,
        analysis_sha256=original.analysis_sha256,
        audio_source_sha256=original.audio_source_sha256,
        audio_derivation_receipt_sha256=(
            original.audio_derivation_receipt_sha256
        ),
        video_source_sha256=original.video_source_sha256,
        audio_timebase=original.audio_timebase,
        video_timebase=original.video_timebase,
        upstream_reliability_receipt_sha256=(
            original.upstream_reliability_receipt_sha256
        ),
        consent_receipt_sha256=original.consent_receipt_sha256,
        issued_at_utc="2026-08-26T00:00:02Z",
    )
    adapter = _adapter_for(handoff)

    with pytest.raises(SeungunEvidenceUnavailableError):
        adapter.resolve(
            analysis_ref=handoff.request.analysis_ref,
            binding_receipt=reissued,
            video_handle=handoff.request.video_handle,
        )


def test_mapping_input_is_supported_and_prompt_phone_mismatch_fails_closed() -> None:
    facade = SeungunAttemptBindingFacade()
    arguments = _handoff_arguments()

    handoff = facade.bind_completed_analysis(
        analysis=_analysis_document(),
        audio_derivation_receipt_sha256=_sha("audio_derivation"),
        **arguments,
    )
    assert handoff.request.binding_receipt.expected_phone_sha256 == sha256_document(
        {"expected_phones": ["ㅏ"]}
    )

    with pytest.raises(VisualFeedbackContractError):
        facade.bind_completed_analysis(
            analysis=_analysis_document(),
            audio_derivation_receipt_sha256=_sha("audio_derivation"),
            **{**arguments, "expected_phones": ("ㅓ",)},
        )


def test_caller_reliability_receipt_is_required_and_never_synthesized() -> None:
    parameter = signature(
        SeungunAttemptBindingFacade.bind_completed_analysis
    ).parameters["reliability_receipt"]
    assert parameter.default is Parameter.empty

    reliability = UpstreamReliabilityReceipt.issue(
        status=UpstreamReliabilityStatus.UNSUPPORTED,
        policy_revision="production_reliability_v1",
        reason_codes=("audio_qc_unsupported",),
    )
    handoff = SeungunAttemptBindingFacade().bind_completed_analysis(
        analysis=_analysis_document(),
        audio_derivation_receipt_sha256=_sha("audio_derivation"),
        **{**_handoff_arguments(), "reliability_receipt": reliability},
    )

    assert handoff.source_record.reliability_receipt is reliability
    with pytest.raises(SeungunEvidenceUnavailableError):
        _adapter_for(handoff).resolve(
            analysis_ref=handoff.request.analysis_ref,
            binding_receipt=handoff.request.binding_receipt,
            video_handle=handoff.request.video_handle,
        )


def _adapter_for(handoff) -> FrozenSeungunPronunciationEvidenceAdapter:  # noqa: ANN001
    return FrozenSeungunPronunciationEvidenceAdapter(
        InMemorySeungunAnalysisSource(
            {
                handoff.request.analysis_ref.reference_id: handoff.source_record,
            }
        )
    )


def _handoff_arguments() -> dict[str, object]:
    attempt_id = _sha("attempt")
    audio_timebase = MediaTimebaseReceipt(
        clock_id_sha256=_sha("audio_clock"),
        duration_ms=1_000,
        origin_offset_ms=0,
        transform_revision="canonical_mono_16khz_v1",
    )
    video_timebase = MediaTimebaseReceipt(
        clock_id_sha256=_sha("video_clock"),
        duration_ms=1_000,
        origin_offset_ms=0,
        transform_revision="restricted_video_clock_v1",
    )
    return {
        "attempt_id_sha256": attempt_id,
        "prompt_id_sha256": _sha("prompt"),
        "prompt_revision": "prompt_binding_v1",
        "expected_phones": ("ㅏ",),
        "audio_source_sha256": _sha("canonical_audio"),
        "audio_timebase": audio_timebase,
        "video_handle": RestrictedVideoHandle(
            handle_id=_sha("video_handle"),
            attempt_id_sha256=attempt_id,
            source_sha256=_sha("video_source"),
            timebase_sha256=video_timebase.timebase_sha256,
        ),
        "video_timebase": video_timebase,
        "reliability_receipt": UpstreamReliabilityReceipt.issue(
            status=UpstreamReliabilityStatus.SUPPORTED,
            policy_revision="production_reliability_v1",
        ),
        "consent_receipt": ConsentReceipt.issue(
            attempt_id_sha256=attempt_id,
            decision=ConsentDecision.GRANTED,
            retention_policy_id="delete_on_completion_v1",
            issued_at_utc="2026-08-26T00:00:00Z",
            expires_at_utc="2026-08-27T00:00:00Z",
        ),
        "issued_at_utc": "2026-08-26T00:00:01Z",
        "upstream_phone_expected_index": 0,
        "capture_contract_id": "production_capture_v1",
        "population_id_sha256": _sha("population"),
        "context_id": "production_context_v1",
    }


def _analysis_document() -> dict[str, object]:
    return {
        "schema_version": SEUNGUN_ANALYSIS_SCHEMA_VERSION,
        "detector_version": "frozen_detector_v1",
        "detector_config_sha256": _sha("detector_config"),
        "backbone_name": "frozen_backbone",
        "backbone_tree_sha256": _sha("backbone_tree"),
        "expected_phones": ["ㅏ"],
        "ctc_hypothesis": ["ㅓ"],
        "insertions": [],
        "phones": [
            {
                "expected_index": 0,
                "expected_phone": "ㅏ",
                "observed_candidate": "ㅓ",
                "alignment_operation": "substitution",
                "start_ms": 200,
                "end_ms": 400,
                "error_score": 0.91,
                "operating_threshold": 0.8,
                "is_error": True,
                "score_semantics": SEUNGUN_SCORE_SEMANTICS,
                "soft_features": {},
            }
        ],
        "notices": ["completed analysis fixture"],
    }


def _sha(label: str) -> str:
    return sha256_document({"visual_handoff_fixture": label})
