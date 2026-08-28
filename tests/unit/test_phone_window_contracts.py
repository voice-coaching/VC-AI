"""Pure contract tests for selected-phone RGB decode and tracking receipts."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

from voice_coach.pronunciation_attempts.adapters import (
    ffmpeg_av_container as ffmpeg_adapter_module,
)
from voice_coach.pronunciation_attempts.adapters import (
    runtime as runtime_adapter_module,
)
from voice_coach.pronunciation_attempts.adapters.calibrated_runtime_decisions import (
    RuntimeAvSyncDecision,
)
from voice_coach.pronunciation_attempts.adapters.lip_audio_av_sync import (
    AvSyncAnchorCandidate,
)
from voice_coach.pronunciation_attempts.models import DecodedRgbFrame, Rgb24Sample
from voice_coach.pronunciation_attempts.phone_window import (
    DensePhoneWindowEvidence,
    PhoneWindowDecodePolicy,
    PhoneWindowDecodeReceipt,
    PhoneWindowDecodeRequest,
    PhoneWindowTrackingReceipt,
    PolicyValidatedAvSyncOffset,
    Rgb24PhoneWindow,
    RuntimePhoneWindowReceipt,
    decoded_rgb_sha256,
    phone_window_code_sha256,
)
from voice_coach.visual_feedback.models.integrity import sha256_document


def test_policy_validated_offset_is_self_digested() -> None:
    offset = _offset()

    assert offset.to_json_object()["receipt_sha256"] == offset.receipt_sha256
    assert offset.status == "synchronized"


def test_policy_validated_offset_rejects_numeric_tampering() -> None:
    with pytest.raises(ValueError, match="receipt digest differs"):
        replace(_offset(), offset_ms=31)


def test_phone_window_policy_rejects_invalid_resource_bounds() -> None:
    with pytest.raises(ValueError, match="maximum_frame_count"):
        replace(_policy(), minimum_frame_count=5, maximum_frame_count=4)


def test_decode_request_binds_policy_and_context() -> None:
    request = _request()

    assert request.decode_policy_sha256 == _policy().policy_sha256
    assert request.context_contract_sha256 == _policy().context_contract_sha256
    assert request.to_json_object()["request_sha256"] == request.request_sha256


def test_decode_request_rejects_span_tampering() -> None:
    with pytest.raises(ValueError, match="request digest differs"):
        replace(_request(), video_end_ms=260)


def test_decode_receipt_binds_exact_timestamps_and_rgb_digest() -> None:
    window = _window()

    assert window.decode_receipt.decoded_timestamps_us == (120_000, 200_000)
    assert window.decode_receipt.decoded_rgb_sha256 == decoded_rgb_sha256(
        window.sample
    )
    assert window.decode_receipt.decoder_code_sha256 == _module_sha256(
        ffmpeg_adapter_module
    )
    assert PhoneWindowDecodeReceipt.from_json_object(
        window.decode_receipt.to_json_object()
    ) == window.decode_receipt


def test_decode_receipt_rejects_missing_or_tampered_code_pin() -> None:
    receipt = _window().decode_receipt
    missing = receipt.to_json_object()
    missing.pop("decoder_code_sha256")

    with pytest.raises(ValueError, match="fields differ"):
        PhoneWindowDecodeReceipt.from_json_object(missing)
    with pytest.raises(ValueError, match="receipt digest differs"):
        replace(receipt, decoder_code_sha256="1" * 64)


def test_rgb_window_rejects_pixel_tampering() -> None:
    original = _window()
    tampered_sample = Rgb24Sample(
        asset_handle_id=original.sample.asset_handle_id,
        container_sha256=original.sample.container_sha256,
        video_stream_index=original.sample.video_stream_index,
        frames=(
            replace(original.sample.frames[0], rgb24=b"\xff" * 3),
            original.sample.frames[1],
        ),
        ffmpeg_revision=original.sample.ffmpeg_revision,
    )

    with pytest.raises(ValueError, match="lineage differs"):
        Rgb24PhoneWindow(
            request=original.request,
            sample=tampered_sample,
            decode_receipt=original.decode_receipt,
        )


def test_tracking_and_runtime_receipts_form_one_digest_dag() -> None:
    window = _window()
    tracking = _tracking(window)
    runtime = RuntimePhoneWindowReceipt.issue(
        request=window.request,
        video_qc_receipt_sha256="a" * 64,
        tracking_receipt=tracking,
        policy=_policy(),
    )
    bundle = DensePhoneWindowEvidence(
        decode_receipt=window.decode_receipt,
        tracking_receipt=tracking,
        runtime_receipt=runtime,
        landmark_evidence=object(),
    )

    assert bundle.runtime_receipt.phone_window_decode_receipt_sha256 == (
        bundle.decode_receipt.receipt_sha256
    )
    assert bundle.runtime_receipt.phone_window_tracking_receipt_sha256 == (
        bundle.tracking_receipt.receipt_sha256
    )
    assert bundle.tracking_receipt.tracking_code_sha256 == _module_sha256(
        runtime_adapter_module
    )
    assert bundle.runtime_receipt.phone_window_code_sha256 == (
        phone_window_code_sha256()
    )
    assert bundle.runtime_receipt.phone_window_policy_revision == (
        _policy().policy_revision
    )
    assert bundle.runtime_receipt.phone_window_policy_sha256 == (
        _policy().policy_sha256
    )
    assert PhoneWindowTrackingReceipt.from_json_object(
        tracking.to_json_object()
    ) == tracking
    assert RuntimePhoneWindowReceipt.from_json_object(
        runtime.to_json_object()
    ) == runtime


def test_tracking_and_runtime_receipts_reject_tampered_code_pins() -> None:
    window = _window()
    tracking = _tracking(window)
    runtime = RuntimePhoneWindowReceipt.issue(
        request=window.request,
        video_qc_receipt_sha256="a" * 64,
        tracking_receipt=tracking,
        policy=_policy(),
    )

    with pytest.raises(ValueError, match="receipt digest differs"):
        replace(tracking, tracking_code_sha256="1" * 64)
    with pytest.raises(ValueError, match="receipt digest differs"):
        replace(runtime, phone_window_code_sha256="2" * 64)
    with pytest.raises(ValueError, match="receipt digest differs"):
        replace(runtime, phone_window_policy_sha256="3" * 64)

    missing_tracking = tracking.to_json_object()
    missing_tracking.pop("tracking_code_sha256")
    with pytest.raises(ValueError, match="fields differ"):
        PhoneWindowTrackingReceipt.from_json_object(missing_tracking)

    missing_runtime = runtime.to_json_object()
    missing_runtime.pop("phone_window_code_sha256")
    with pytest.raises(ValueError, match="fields differ"):
        RuntimePhoneWindowReceipt.from_json_object(missing_runtime)


def test_runtime_receipt_rejects_policy_other_than_request_policy() -> None:
    window = _window()

    with pytest.raises(ValueError, match="request and policy lineage differ"):
        RuntimePhoneWindowReceipt.issue(
            request=window.request,
            video_qc_receipt_sha256="a" * 64,
            tracking_receipt=_tracking(window),
            policy=replace(_policy(), padding_after_ms=21),
        )


def test_runtime_receipt_rejects_tracking_from_another_anchor() -> None:
    window = _window()
    other_tracking = _tracking(_window(phone_anchor_sha256="b" * 64))

    with pytest.raises(ValueError, match="tracking and request lineage differ"):
        RuntimePhoneWindowReceipt.issue(
            request=window.request,
            video_qc_receipt_sha256="a" * 64,
            tracking_receipt=other_tracking,
            policy=_policy(),
        )


def _policy() -> PhoneWindowDecodePolicy:
    return PhoneWindowDecodePolicy(
        policy_revision="selected_phone_test_v1",
        expected_av_sync_policy_sha256="1" * 64,
        context_contract_sha256="2" * 64,
        maximum_absolute_offset_ms=100,
        padding_before_ms=20,
        padding_after_ms=20,
        minimum_frame_count=2,
        maximum_frame_count=8,
        maximum_window_duration_ms=500,
        maximum_rgb_bytes=1_000,
    )


def _offset() -> PolicyValidatedAvSyncOffset:
    anchors = (AvSyncAnchorCandidate(audio_ms=0, video_ms=30, strength=1.0),)
    unsigned = {
        "schema_version": "voice_coach.runtime_av_sync_decision.v2",
        "attempt_id_sha256": "3" * 64,
        "binding_receipt_sha256": "4" * 64,
        "audio_source_sha256": "5" * 64,
        "video_source_sha256": "6" * 64,
        "landmark_evidence_sha256": "7" * 64,
        "landmark_model_sha256": "e" * 64,
        "source_candidate_receipt_sha256": "f" * 64,
        "source_candidate_lineage_receipt_sha256": "0" * 64,
        "measurement_revision": "sync_test_v1",
        "measurement_policy_sha256": "1" * 64,
        "measurement_adapter_code_sha256": "a" * 64,
        "observed_offset_ms": 30,
        "observed_correlation": 0.95,
        "observed_anchors": [anchor.to_json_object() for anchor in anchors],
        "decision_policy_revision": "sync_decision_test_v1",
        "decision_policy_sha256": "b" * 64,
        "decision_adapter_code_sha256": "c" * 64,
        "status": "synchronized",
        "reason_codes": [],
    }
    decision = RuntimeAvSyncDecision(
        attempt_id_sha256="3" * 64,
        binding_receipt_sha256="4" * 64,
        audio_source_sha256="5" * 64,
        video_source_sha256="6" * 64,
        landmark_evidence_sha256="7" * 64,
        landmark_model_sha256="e" * 64,
        source_candidate_receipt_sha256="f" * 64,
        source_candidate_lineage_receipt_sha256="0" * 64,
        measurement_revision="sync_test_v1",
        measurement_policy_sha256="1" * 64,
        measurement_adapter_code_sha256="a" * 64,
        observed_offset_ms=30,
        observed_correlation=0.95,
        observed_anchors=anchors,
        decision_policy_revision="sync_decision_test_v1",
        decision_policy_sha256="b" * 64,
        decision_adapter_code_sha256="c" * 64,
        status="synchronized",
        reason_codes=(),
        receipt_sha256=sha256_document(unsigned),
    )
    return PolicyValidatedAvSyncOffset.issue_from_decision(decision=decision)


def _request(*, phone_anchor_sha256: str = "9" * 64) -> PhoneWindowDecodeRequest:
    return PhoneWindowDecodeRequest.issue(
        attempt_id_sha256="3" * 64,
        binding_receipt_sha256="4" * 64,
        container_sha256="6" * 64,
        detection_receipt_sha256="d" * 64,
        phone_anchor_sha256=phone_anchor_sha256,
        av_sync_receipt_sha256=_offset().av_sync_receipt_sha256,
        av_sync_offset_receipt_sha256=_offset().receipt_sha256,
        policy=_policy(),
        video_start_ms=110,
        video_end_ms=250,
    )


def _sample() -> Rgb24Sample:
    return Rgb24Sample(
        asset_handle_id="e" * 64,
        container_sha256="6" * 64,
        video_stream_index=0,
        frames=(
            DecodedRgbFrame(
                timestamp_us=120_000,
                width=1,
                height=1,
                rgb24=b"\x01" * 3,
            ),
            DecodedRgbFrame(
                timestamp_us=200_000,
                width=1,
                height=1,
                rgb24=b"\x02" * 3,
            ),
        ),
        ffmpeg_revision="ffmpeg-test",
    )


def _window(*, phone_anchor_sha256: str = "9" * 64) -> Rgb24PhoneWindow:
    request = _request(phone_anchor_sha256=phone_anchor_sha256)
    sample = _sample()
    receipt = PhoneWindowDecodeReceipt.issue(
        request=request,
        video_stream_index=0,
        selected_frame_indices=(3, 5),
        decoded_timestamps_us=(120_000, 200_000),
        width=1,
        height=1,
        decoded_rgb_sha256=decoded_rgb_sha256(sample),
        ffmpeg_revision="ffmpeg-test",
        decoder_code_sha256=_module_sha256(ffmpeg_adapter_module),
    )
    return Rgb24PhoneWindow(
        request=request,
        sample=sample,
        decode_receipt=receipt,
    )


def _tracking(window: Rgb24PhoneWindow) -> PhoneWindowTrackingReceipt:
    return PhoneWindowTrackingReceipt.issue(
        window=window,
        landmark_evidence_sha256="f" * 64,
        model_sha256="0" * 64,
        tracking_code_sha256=_module_sha256(runtime_adapter_module),
    )


def _module_sha256(module: object) -> str:
    module_path = getattr(module, "__file__", None)
    assert isinstance(module_path, str)
    return hashlib.sha256(Path(module_path).read_bytes()).hexdigest()
