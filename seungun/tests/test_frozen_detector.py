from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from korean_phone_ctc.frozen_detector import (
    SCORE_SEMANTICS,
    FrozenDetectorError,
    FrozenPronunciationDetector,
    build_detector_rows,
    raw_ctc_spans,
)

ROOT = Path(__file__).resolve().parents[2]


def test_raw_ctc_spans_collapses_runs_and_removes_blank() -> None:
    assert raw_ctc_spans([0, 3, 3, 0, 4, 4, 0], blank_id=0) == [
        (3, 1, 3),
        (4, 4, 6),
    ]


def test_feature_extraction_preserves_candidate_location_and_score_semantics() -> None:
    vocabulary = {"<pad>": 0, "ㄱ": 1, "ㅏ": 2}
    logits = np.full((5, 3), -4.0, dtype=np.float32)
    logits[0, 0] = 4.0
    logits[1:3, 1] = 4.0
    logits[3, 0] = 4.0
    logits[4, 2] = 4.0
    hidden = np.ones((5, 1024), dtype=np.float32)

    rows, hypothesis, insertions = build_detector_rows(
        logits=logits,
        hidden_layer_18=hidden,
        expected_phones=("ㄱ", "ㄱ"),
        vocabulary=vocabulary,
        id_to_token={value: key for key, value in vocabulary.items()},
        blank_id=0,
        waveform_samples=16_000,
    )

    assert hypothesis == ("ㄱ", "ㅏ")
    assert insertions == ()
    assert rows[0].alignment_operation == "correct"
    assert rows[1].alignment_operation == "substitution"
    assert rows[1].observed_candidate == "ㅏ"
    assert (rows[1].start_ms, rows[1].end_ms) == (800, 1000)
    assert SCORE_SEMANTICS.endswith("not_calibrated_correctness_confidence")


def test_feature_extraction_represents_deletion_without_inventing_a_span() -> None:
    vocabulary = {"<pad>": 0, "ㄱ": 1, "ㅏ": 2}
    logits = np.asarray([[-4.0, 4.0, -4.0]], dtype=np.float32)
    hidden = np.ones((1, 1024), dtype=np.float32)

    rows, _, _ = build_detector_rows(
        logits=logits,
        hidden_layer_18=hidden,
        expected_phones=("ㄱ", "ㅏ"),
        vocabulary=vocabulary,
        id_to_token={value: key for key, value in vocabulary.items()},
        blank_id=0,
        waveform_samples=16_000,
    )

    assert rows[1].alignment_operation == "deletion"
    assert rows[1].observed_candidate is None
    assert rows[1].start_ms is None and rows[1].end_ms is None


def test_feature_extraction_fails_closed_for_unknown_expected_phone() -> None:
    with pytest.raises(FrozenDetectorError, match="unknown expected phone"):
        build_detector_rows(
            logits=np.asarray([[-4.0, 4.0]], dtype=np.float32),
            hidden_layer_18=np.ones((1, 1024), dtype=np.float32),
            expected_phones=("ㅏ",),
            vocabulary={"<pad>": 0, "ㄱ": 1},
            id_to_token={0: "<pad>", 1: "ㄱ"},
            blank_id=0,
            waveform_samples=16_000,
        )


def test_frozen_bundle_hash_and_architecture_gate_passes() -> None:
    detector = FrozenPronunciationDetector(repository_root=ROOT)

    assert detector.config["version"] == "final_pronunciation_detector_v1"
    assert detector.config["operating_point"]["threshold"] == pytest.approx(
        0.8134703636169434
    )
