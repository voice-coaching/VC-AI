from __future__ import annotations

from contextlib import nullcontext
import hashlib
from pathlib import Path
from uuid import UUID

import pytest

from voice_coach.backend_analysis.adapters.seungun import (
    DecodedWav,
    SeungunBackendAnalysisAdapter,
)
from voice_coach.backend_analysis.domain import (
    AnalysisAuthorizationGrant,
    AnalysisOutcome,
    AnalysisRequest,
)
from voice_coach.backend_analysis.ports import BackendAnalysisError, MaterializedAudio
from voice_coach.pronunciation_feedback.domain import ExpectedPhoneSequence
from voice_coach.pronunciation_feedback.seungun_bridge import (
    SEUNGUN_SCHEMA_VERSION,
    SEUNGUN_SCORE_SEMANTICS,
)


class _Decoder:
    def __init__(self) -> None:
        self.calls = 0

    def decode(self, source: Path):
        self.calls += 1
        assert source == Path("/restricted/source.audio")
        return nullcontext(DecodedWav(Path("/restricted/decoded.wav"), 1000))


class _Frontend:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.calls = 0

    def expected_phones(self, normalized_text: str) -> ExpectedPhoneSequence:
        self.calls += 1
        assert normalized_text == "가"
        if self.failure:
            raise self.failure
        return ExpectedPhoneSequence(
            tokens=("ㄱ", "ㅏ"),
            token_sha256="a" * 64,
            backend_id="g2pk",
            revision="2.0.0",
        )


class _Detector:
    def __init__(self, analysis: dict[str, object]) -> None:
        self.analysis = analysis
        self.calls = 0

    def analyze(self, *, audio_path: Path, expected_phones: tuple[str, ...]):
        self.calls += 1
        assert audio_path == Path("/restricted/decoded.wav")
        assert expected_phones == ("ㄱ", "ㅏ")
        return self.analysis


def _phone(index: int, expected: str, *, is_error: bool) -> dict[str, object]:
    return {
        "expected_index": index,
        "expected_phone": expected,
        "observed_candidate": "ㅋ" if is_error else expected,
        "alignment_operation": "substitution" if is_error else "correct",
        "start_ms": index * 100,
        "end_ms": (index + 1) * 100,
        "error_score": 0.91 if is_error else 0.1,
        "operating_threshold": 0.8,
        "is_error": is_error,
        "score_semantics": SEUNGUN_SCORE_SEMANTICS,
        "soft_features": {},
    }


def _analysis(*, issue: bool) -> dict[str, object]:
    return {
        "schema_version": SEUNGUN_SCHEMA_VERSION,
        "detector_version": "frozen-v1",
        "expected_phones": ["ㄱ", "ㅏ"],
        "phones": [
            _phone(0, "ㄱ", is_error=issue),
            _phone(1, "ㅏ", is_error=False),
        ],
    }


def _request() -> AnalysisRequest:
    key = "recordings/attempt.wav"
    grant = AnalysisAuthorizationGrant(
        key_id="key-v1",
        request_event_id=UUID("4adfe173-0691-4e89-b94e-a5c5c5085826"),
        analysis_id=35,
        content_id=12,
        prompt_revision="revision-v1",
        script_sha256=hashlib.sha256("가".encode()).hexdigest(),
        audio_object_key_sha256=hashlib.sha256(key.encode()).hexdigest(),
        audio_sha256="d" * 64,
        mime_type="audio/wav",
        file_size_bytes=4,
        duration_ms=1000,
        learning_focus="PRONUNCIATION",
        consent_receipt_sha256="b" * 64,
        consent_policy_revision="consent-v1",
        issued_at_utc_text="2026-09-02T00:00:00Z",
        expires_at_utc_text="2026-09-02T00:05:00Z",
        purpose="pronunciation_coaching",
        data_category="learner_voice_recording",
        delete_on_completion=True,
        remote_egress_allowed=False,
        signature="c" * 64,
    )
    return AnalysisRequest(
        event_id=grant.request_event_id,
        analysis_id=35,
        content_id=12,
        prompt_revision="revision-v1",
        script_text="가",
        script_sha256=grant.script_sha256,
        audio_object_key=key,
        audio_sha256="d" * 64,
        mime_type="audio/wav",
        file_size_bytes=4,
        duration_ms=1000,
        learning_focus="PRONUNCIATION",
        authorization_grant=grant,
    )


def _audio() -> MaterializedAudio:
    return MaterializedAudio(
        path=Path("/restricted/source.audio"),
        byte_count=4,
        sha256="d" * 64,
    )


def test_preserves_one_seungun_selected_phone_as_grounded_feedback() -> None:
    decoder = _Decoder()
    frontend = _Frontend()
    detector = _Detector(_analysis(issue=True))

    result = SeungunBackendAnalysisAdapter(
        decoder=decoder,
        expected_phone_frontend=frontend,
        detector=detector,
    ).analyze(request=_request(), audio=_audio())

    assert result.outcome is AnalysisOutcome.COACHING_READY
    assert result.summary_feedback is not None and "‘ㄱ’" in result.summary_feedback
    assert result.pronunciation_evidence is not None
    assert result.pronunciation_evidence.selected_phone == "ㄱ"
    assert result.pronunciation_evidence.selected_expected_index == 0
    assert result.pronunciation_evidence.detector_score == 0.91
    assert result.pronunciation_evidence.operating_threshold == 0.8
    assert result.pipeline_revision == "g2pk:2.0.0|seungun:frozen-v1"
    assert frontend.calls == decoder.calls == detector.calls == 1


def test_reports_no_issue_only_when_every_expected_phone_is_clean() -> None:
    result = SeungunBackendAnalysisAdapter(
        decoder=_Decoder(),
        expected_phone_frontend=_Frontend(),
        detector=_Detector(_analysis(issue=False)),
    ).analyze(request=_request(), audio=_audio())

    assert result.outcome is AnalysisOutcome.COMPLETED_NO_ISSUE
    assert result.summary_feedback is None
    assert result.pronunciation_evidence is None


def test_expected_phone_failure_stops_before_audio_decode_or_detector() -> None:
    decoder = _Decoder()
    detector = _Detector(_analysis(issue=True))

    with pytest.raises(BackendAnalysisError) as error:
        SeungunBackendAnalysisAdapter(
            decoder=decoder,
            expected_phone_frontend=_Frontend(RuntimeError("unavailable")),
            detector=detector,
        ).analyze(request=_request(), audio=_audio())

    assert error.value.code == "seungun_backend_unavailable"
    assert decoder.calls == detector.calls == 0


def test_mismatched_detector_phone_sequence_fails_closed() -> None:
    analysis = _analysis(issue=True)
    analysis["expected_phones"] = ["ㄴ", "ㅏ"]

    with pytest.raises(BackendAnalysisError) as error:
        SeungunBackendAnalysisAdapter(
            decoder=_Decoder(),
            expected_phone_frontend=_Frontend(),
            detector=_Detector(analysis),
        ).analyze(request=_request(), audio=_audio())

    assert error.value.code == "seungun_detector_failed"
