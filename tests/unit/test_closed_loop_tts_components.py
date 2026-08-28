from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import torch

from voice_coach.application.closed_loop import ClosedLoopSession
from voice_coach.data.adversarial_speech import NativeSpeechRecord
from voice_coach.domain.models import (
    AspectScores,
    AuditResult,
    AuditStatus,
    ContentMatch,
    DiagnosticIssue,
    DiagnosticProfile,
    EvidenceId,
)
from voice_coach.evaluation.closed_loop_metrics import (
    ClosedLoopMetricError,
    compute_s1_metrics,
    compute_s1_scorer_evaluation,
    normalized_pairwise_variability_index,
    run_s2_independent_audit,
)
from voice_coach.features.egemaps import EGeMAPSFunctionals
from voice_coach.features.gop import GOPFeatures
from voice_coach.features.parselmouth_pitch import ParselmouthPitchContour
from voice_coach.models.generator_adapters import (
    GeneratorFamily,
    GeneratorFamilyChain,
    OptionalExternalGeneratorAdapter,
)
from voice_coach.references.native_reference_bank import (
    NativeReferenceBankError,
    build_native_reference_bank,
    select_reference_entries,
)


def _sha(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()


def _native(stable_id: str) -> NativeSpeechRecord:
    return NativeSpeechRecord(
        stable_id=stable_id,
        source_id="kss",
        audio_storage="relative_file",
        relative_path="audio.wav",
        embedded_reference=None,
        duration_seconds=1.0,
        sample_rate_hz=16_000,
        channels=1,
        text_tokens=(1, 2),
        quality_target=1.0,
        aspect_targets=(0.0, 0.0, 0.0, 0.0),
        aspect_mask=(False, False, False, False),
        content_supervised=False,
    )


def test_native_reference_bank_requires_explicit_speaker_metadata() -> None:
    records = (_native("r0"), _native("r1"))
    with pytest.raises(NativeReferenceBankError, match="speaker metadata"):
        build_native_reference_bank(records, speaker_ids_by_record=None)

    speakers = {"r0": _sha("speaker0"), "r1": _sha("speaker1")}
    sentence = {"r0": _sha("sentence"), "r1": _sha("sentence")}
    bank = build_native_reference_bank(
        records,
        speaker_ids_by_record=speakers,
        sentence_keys_by_record=sentence,
    )
    selected = select_reference_entries(
        bank,
        sentence_key_sha256=_sha("sentence"),
        count=2,
        seed=42,
    )
    assert len({entry.speaker_id_sha256 for entry in selected}) == 2
    assert bank.to_report()["contains_audio_bytes"] is False


def test_pdf_s1_metrics_and_independent_s2_metrics() -> None:
    gop = GOPFeatures(
        target_log_posterior=np.array([-0.2, -0.3]),
        gop_margin=np.array([0.1, 0.2]),
        posterior_entropy=np.array([0.5, 0.4]),
        target_is_top1=np.array([1.0, 0.0]),
    )
    egemaps = EGeMAPSFunctionals(
        open_smile_version="2.6.0",
        feature_set="eGeMAPSv02",
        feature_level="Functionals",
        sample_rate_hz=16_000,
        names=tuple(f"f{index}" for index in range(88)),
        values=tuple(float(index) for index in range(88)),
        column_order_sha256=_sha("columns"),
    )
    f0 = ParselmouthPitchContour(
        parselmouth_version="0.4.7",
        sample_rate_hz=16_000,
        time_step_seconds=0.01,
        pitch_floor_hz=70.0,
        pitch_ceiling_hz=500.0,
        unvoiced_policy="keep_zero",
        time_seconds=(0.0, 0.01),
        f0_hz=(120.0, 130.0),
        backend_frame_count=2,
        returned_frame_count=2,
        voiced_frame_count=2,
        unvoiced_frame_count=0,
        voiced_median_hz=125.0,
        voiced_range_hz=10.0,
        contour_sha256=_sha("contour"),
    )
    bundle = compute_s1_metrics(
        gop_features=gop,
        ctc_log_probabilities=np.log(np.array([[0.8, 0.2], [0.1, 0.9]])),
        ctc_frame_indices=(0, 1),
        ctc_target_class_indices=(0, 1),
        f0_contour=f0,
        egemaps=egemaps,
        phone_durations_seconds=(0.1, 0.2, 0.15),
        utmos_score=0.8,
        speaker_similarity_score=0.9,
    )
    assert bundle.missing_metrics == ("mfa_dtw",)
    assert bundle.metrics["ctc_target_top1_rate"] == 1.0
    assert normalized_pairwise_variability_index((0.1, 0.2)) > 0

    s2 = run_s2_independent_audit(
        "안녕하세요",
        "안녕하새요",
        wer_threshold=0.5,
        cer_threshold=0.1,
        auditor_backend_id="whisper_s2_v1",
        s1_backend_id="wavlm_s1_v1",
    )
    assert s2.decision.content_mismatch is True
    with pytest.raises(ClosedLoopMetricError, match="independently"):
        run_s2_independent_audit(
            "가",
            "가",
            wer_threshold=0.5,
            cer_threshold=0.5,
            auditor_backend_id="same",
            s1_backend_id="same",
        )


def test_pdf_s1_scorer_metrics_cover_phone_regression_and_calibration() -> None:
    bundle = compute_s1_scorer_evaluation(
        phone_labels=(0, 1, 0, 1),
        phone_scores=(0.1, 0.9, 0.2, 0.8),
        word_targets=(0.1, 0.4, 0.8),
        word_predictions=(0.2, 0.5, 0.7),
        utterance_targets=(0.2, 0.5, 0.9),
        utterance_predictions=(0.3, 0.4, 0.8),
        calibration_confidence=(0.1, 0.9, 0.8, 0.2),
        calibration_correctness=(0.0, 1.0, 1.0, 0.0),
    )

    assert bundle.missing_metrics == ()
    assert bundle.metrics["phone_f1"] == 1.0
    assert bundle.metrics["phone_auc"] == 1.0
    assert "word_pcc" in bundle.metrics
    assert "utterance_icc" in bundle.metrics
    assert "calibration_ece" in bundle.metrics
    assert bundle.to_dict()["targets"]["phone"]["f1_min"] == 0.8


def test_external_generator_family_chain_is_explicit() -> None:
    def backend(source: torch.Tensor, tokens: torch.Tensor, native: torch.Tensor) -> torch.Tensor:
        return source.clone()

    adapter = OptionalExternalGeneratorAdapter(
        GeneratorFamily.COSYVOICE2,
        "local.cosyvoice2.attested.v1",
        backend,
    )
    chain = GeneratorFamilyChain({GeneratorFamily.COSYVOICE2: adapter})
    result = chain.generate_candidates(
        torch.zeros(1, 32),
        torch.ones(1, 2, dtype=torch.long),
        torch.zeros(1, 32),
        families=(GeneratorFamily.COSYVOICE2,),
    )
    assert result[0].family is GeneratorFamily.COSYVOICE2
    assert result[0].backend_id.startswith("local.")


def test_closed_loop_session_transitions_to_rerecord_then_coach() -> None:
    profile = DiagnosticProfile(
        attempt_id="00000000-0000-0000-0000-000000000001",
        target_text="가",
        content_match=ContentMatch(score=0.5, confidence=0.9),
        aspects=AspectScores(segmental=0.5, fluency=0.5, prosody=0.5, comprehensibility=0.5),
        issues=[],
        audit=AuditResult(status=AuditStatus.NOT_RUN),
        coaching={"action": "rerecord", "message": ""},
        analysis_source="model",
    )
    session = ClosedLoopSession(max_attempts=2)
    first = session.decide(profile)
    assert first.action == "rerecord"
    session.start_rerecord()
    second_profile = profile.model_copy(update={
        "content_match": ContentMatch(score=1.0, confidence=0.9),
        "issues": [DiagnosticIssue(
            type="vowel_quality",
            location={"startMs": 0, "endMs": 100},
            severity=0.8,
            confidence=0.95,
            evidence=[EvidenceId.GOP_LOW],
        )],
    })
    second = session.decide(second_profile)
    assert second.action == "coach"
    assert session.state == "completed"
