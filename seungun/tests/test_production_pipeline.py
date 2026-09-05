from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from korean_phone_ctc.frozen_detector import SCHEMA_VERSION, SCORE_SEMANTICS
from korean_phone_ctc.production_pipeline import (
    DECISION_SCHEMA_VERSION,
    POLICY_REVISION,
    SeungunProductionError,
    SeungunProductionPipeline,
)


@dataclass
class _Analysis:
    document: dict[str, object]

    def to_json_object(self) -> dict[str, object]:
        return self.document


class _Detector:
    def __init__(self, document: dict[str, object]) -> None:
        self.document = document
        self.preloaded = False

    def preload(self) -> None:
        self.preloaded = True

    def analyze(self, *, audio_path: str | Path, expected_phones: tuple[str, ...]):
        assert Path(audio_path).name == "attempt.wav"
        assert expected_phones == ("ㄱ", "ㅏ")
        return _Analysis(self.document)


def _phone(index: int, score: float, threshold: float = 0.8) -> dict[str, object]:
    return {
        "expected_index": index,
        "expected_phone": ("ㄱ", "ㅏ")[index],
        "observed_candidate": ("ㄴ", "ㅏ")[index],
        "alignment_operation": "substitution" if index == 0 else "correct",
        "start_ms": index * 100,
        "end_ms": index * 100 + 50,
        "error_score": score,
        "operating_threshold": threshold,
        "is_error": score >= threshold,
        "score_semantics": SCORE_SEMANTICS,
        "soft_features": {},
    }


def _document(*, scores: tuple[float, float] = (0.9, 0.2)) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "detector_version": "frozen-v1",
        "detector_config_sha256": "a" * 64,
        "backbone_name": "xls-r",
        "backbone_tree_sha256": "b" * 64,
        "expected_phones": ["ㄱ", "ㅏ"],
        "ctc_hypothesis": ["ㄴ", "ㅏ"],
        "insertions": [],
        "phones": [_phone(0, scores[0]), _phone(1, scores[1])],
        "notices": [],
    }


def test_preserves_all_evidence_and_selects_one_threshold_passed_phone() -> None:
    detector = _Detector(_document())
    pipeline = SeungunProductionPipeline(detector)

    result = pipeline.analyze(
        audio_path="attempt.wav", expected_phones=("ㄱ", "ㅏ")
    )

    assert result.evidence_bundle == detector.document
    assert result.target_free_hypothesis == ("ㄴ", "ㅏ")
    assert result.decision.state == "coach_one"
    assert result.decision.selected_expected_index == 0
    assert result.decision.schema_version == DECISION_SCHEMA_VERSION
    assert result.pipeline_revision == f"frozen-v1|{POLICY_REVISION}"
    assert len(result.decision.evidence_bundle_sha256) == 64


def test_no_threshold_passed_phone_is_a_completed_no_issue_decision() -> None:
    result = SeungunProductionPipeline(_Detector(_document(scores=(0.2, 0.3)))).analyze(
        audio_path="attempt.wav", expected_phones=("ㄱ", "ㅏ")
    )

    assert result.decision.state == "no_issue"
    assert result.decision.selected_expected_index is None
    assert result.decision.reason_codes == ("no_threshold_passed_phone",)


def test_preload_and_capabilities_are_explicit() -> None:
    detector = _Detector(_document())
    pipeline = SeungunProductionPipeline(detector)

    pipeline.preload()

    assert detector.preloaded is True
    assert pipeline.capabilities() == {
        "analysis_schema_version": "korean_phone_ctc.production_analysis.v2",
        "decision_schema_version": DECISION_SCHEMA_VERSION,
        "policy_revision": POLICY_REVISION,
        "frozen_analysis_schema_version": SCHEMA_VERSION,
    }


def test_rejects_schema_and_threshold_decision_drift() -> None:
    invalid_schema = _document()
    invalid_schema["schema_version"] = "other"
    with pytest.raises(SeungunProductionError, match="schema_mismatch"):
        SeungunProductionPipeline(_Detector(invalid_schema)).analyze(
            audio_path="attempt.wav", expected_phones=("ㄱ", "ㅏ")
        )

    invalid_threshold = _document()
    invalid_threshold["phones"][0]["is_error"] = False  # type: ignore[index]
    with pytest.raises(SeungunProductionError, match="threshold_decision_mismatch"):
        SeungunProductionPipeline(_Detector(invalid_threshold)).analyze(
            audio_path="attempt.wav", expected_phones=("ㄱ", "ㅏ")
        )

    invalid_phone = _document()
    invalid_phone["phones"][1]["expected_phone"] = "ㅗ"  # type: ignore[index]
    with pytest.raises(SeungunProductionError, match="phone_evidence_invalid"):
        SeungunProductionPipeline(_Detector(invalid_phone)).analyze(
            audio_path="attempt.wav", expected_phones=("ㄱ", "ㅏ")
        )
