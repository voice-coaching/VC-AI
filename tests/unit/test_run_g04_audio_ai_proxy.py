from __future__ import annotations

from scripts.run_g04_audio_ai_proxy import _normalize_korean_text


def test_g04_transcript_normalization_rejects_punctuation_only() -> None:
    assert _normalize_korean_text("...?!!") == ""


def test_g04_transcript_normalization_keeps_korean_and_safe_punctuation() -> None:
    assert _normalize_korean_text("  안녕, 세계?  ") == "안녕 세계?"
