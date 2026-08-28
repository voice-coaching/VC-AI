from __future__ import annotations

from voice_coach.evaluation.g04_audio_proxy import (
    AnnotationFidelity,
    G04AudioProxyThresholds,
    alignment_record_error_count,
    classify_annotation_fidelity,
    normalized_sequence_similarity,
    recompute_alignment_records,
)


def test_similarity_normalizes_conjoining_jamo() -> None:
    assert normalized_sequence_similarity(("ᄀ", "ㅏ"), ("ㄱ", "ㅏ")) == 1.0
    assert normalized_sequence_similarity(("ㄱ", "ㅏ"), ("ㄴ", "ㅏ")) == 0.5


def test_genuine_learner_error_does_not_invalidate_target() -> None:
    decision = classify_annotation_fidelity(
        target_source_similarity=1.0,
        target_audio_similarity=0.25,
        realized_audio_similarity=0.90,
        model_confidence=0.90,
        thresholds=G04AudioProxyThresholds(),
    )

    assert decision.annotation_fidelity is AnnotationFidelity.CONSISTENT
    assert decision.target_source_is_consistent is True
    assert decision.realized_audio_is_consistent is True


def test_low_model_confidence_abstains() -> None:
    decision = classify_annotation_fidelity(
        target_source_similarity=1.0,
        target_audio_similarity=1.0,
        realized_audio_similarity=1.0,
        model_confidence=0.20,
        thresholds=G04AudioProxyThresholds(),
    )

    assert decision.annotation_fidelity is AnnotationFidelity.UNCERTAIN
    assert decision.abstained is True


def test_high_confidence_but_no_audio_match_abstains() -> None:
    decision = classify_annotation_fidelity(
        target_source_similarity=1.0,
        target_audio_similarity=0.0,
        realized_audio_similarity=0.0,
        model_confidence=0.90,
        thresholds=G04AudioProxyThresholds(),
    )

    assert decision.annotation_fidelity is AnnotationFidelity.UNCERTAIN
    assert decision.abstained is True
    assert decision.reason == "audio_matches_neither_annotation_with_sufficient_support"


def test_each_mismatch_state_is_explicit() -> None:
    thresholds = G04AudioProxyThresholds()
    target = classify_annotation_fidelity(
        target_source_similarity=0.20,
        target_audio_similarity=0.40,
        realized_audio_similarity=0.90,
        model_confidence=0.90,
        thresholds=thresholds,
    )
    realized = classify_annotation_fidelity(
        target_source_similarity=1.0,
        target_audio_similarity=0.90,
        realized_audio_similarity=0.20,
        model_confidence=0.90,
        thresholds=thresholds,
    )
    both = classify_annotation_fidelity(
        target_source_similarity=0.20,
        target_audio_similarity=0.50,
        realized_audio_similarity=0.20,
        model_confidence=0.90,
        thresholds=thresholds,
    )

    assert target.annotation_fidelity is AnnotationFidelity.TARGET_MISMATCH
    assert realized.annotation_fidelity is AnnotationFidelity.REALIZED_MISMATCH
    assert both.annotation_fidelity is AnnotationFidelity.BOTH_MISMATCH


def test_alignment_recomputation_detects_record_drift() -> None:
    targets = [{"start": 0.0, "end": 0.4, "label": "ㄱ"}]
    realized = [{"start": 0.0, "end": 0.4, "label": "ㅋ"}]
    recomputed = recompute_alignment_records(targets, realized)

    assert alignment_record_error_count(recomputed, recomputed) == 0
    drifted = [dict(recomputed[0], operation="match")]
    assert alignment_record_error_count(drifted, recomputed) == 1
