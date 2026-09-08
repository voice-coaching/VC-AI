"""The measured Korean read-speech rate band, read from its artifact.

Two stages need this band and they must read the same one.  The alignment uses
its slow edge to bound how much silence a syllable may absorb, and the S1
fluency branch uses both edges to score a rate; if either hard-coded its own
numbers, a recording could be called well-paced by one and out of band by the
other with nothing in the output explaining why.

The band is measured, not chosen: AI Hub 542, 1,225,582 utterances, p0.5-p99.5
of syllables per second over the spoken region of each file.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..domain import FailureCode
from ..errors import BackendUnavailableError

DEFAULT_RATE_BAND_PATH = Path(
    "artifacts/reference/r01/korean_read_speech_rate_band_v1.json"
)


def read_rate_band(path: str | Path) -> tuple[float, float]:
    """``(low, high)`` syllables per second from the measured artifact."""

    try:
        band = json.loads(Path(path).read_text(encoding="utf-8"))["band"]
        low = float(band["low_syllables_per_second"])
        high = float(band["high_syllables_per_second"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BackendUnavailableError(
            FailureCode.S1_BACKEND_UNAVAILABLE,
            f"measured speech-rate band unreadable: {exc}",
        ) from exc
    if not 0.0 < low < high:
        raise BackendUnavailableError(
            FailureCode.S1_BACKEND_UNAVAILABLE,
            "measured speech-rate band is not an ordered positive interval",
        )
    return low, high


def slowest_syllable_seconds(path: str | Path) -> float:
    """How long one syllable takes at the slow edge of the measured band.

    This is the longest a syllable slot can plausibly run in read speech, so it
    is also the most silence the aligner may fold into one syllable before the
    silence is better described as a pause.
    """

    low, _ = read_rate_band(path)
    return 1.0 / low
