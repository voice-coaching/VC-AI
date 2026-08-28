"""Composition-policy tests for the internal video pronunciation attempt."""

from __future__ import annotations

from pathlib import Path

from voice_coach.pronunciation_attempts.composition import (
    build_local_video_attempt_facade,
)
from voice_coach.visual_feedback.models.enums import UpstreamReliabilityStatus


class _UnusedDetector:
    """Build-only detector double; composition must not invoke it."""

    def analyze(self, *, audio_path: str | Path, expected_phones: object) -> object:
        del audio_path, expected_phones
        raise AssertionError("composition unexpectedly invoked the detector")


def test_current_frozen_seungun_is_supported_only_as_upstream_anchor(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    facade = build_local_video_attempt_facade(
        repository_root=repository_root,
        restricted_media_root=tmp_path / "media",
        restricted_audio_root=tmp_path / "audio",
        nltk_data_root=repository_root / ".nltk_data",
        seungun_detector=_UnusedDetector(),
    )

    handoff = facade._handoff_port  # noqa: SLF001 - composition wiring assertion
    receipt = handoff._reliability_receipt  # noqa: SLF001

    assert receipt.status is UpstreamReliabilityStatus.SUPPORTED
    assert receipt.policy_revision == (
        "seungun_final_pronunciation_detector_v1_locked_test_v1"
    )
    assert receipt.reason_codes == ()
    assert facade._reliability_port.__class__.__name__ == (  # noqa: SLF001
        "BlockedVisualReliabilityAdapter"
    )

