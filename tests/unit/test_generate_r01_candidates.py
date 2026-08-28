"""The gate that decides which renderings are allowed to become references.

A reference exemplar sets the tolerance a learner is scored against, so a rendering
that ran away into babble or stopped mid-sentence does more than look bad — it moves
the band every learner of that sentence is judged by. These tests pin the two things
that make the gate trustworthy: it is measured rather than chosen, and what it
discards stays on disk.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
_spec = importlib.util.spec_from_file_location(
    "generate_r01", ROOT / "scripts" / "generate_r01_candidates.py"
)
generate = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules[_spec.name] = generate
_spec.loader.exec_module(generate)

from voice_coach.pronunciation_feedback.adapters.reference_generator import (  # noqa: E402
    ReferenceGeneratorError,
    VoicePrompt,
)
from voice_coach.pronunciation_feedback.domain import ReferenceExemplar  # noqa: E402

#: 12 syllables. At 4 s that is 3.0 syl/s, at 2 s it is 6.0, at 1 s it is 12.0.
SENTENCE = {
    "sentence_id": "R00-01",
    "normalized_text": "쌀과 콩을 깨끗한 그릇에 담아요.",
    "syllable_count": 12,
}
BAND = (3.846, 8.543)


def _voice(tmp_path: Path) -> VoicePrompt:
    prompt = tmp_path / "prompt.wav"
    prompt.write_bytes(b"RIFF-prompt")
    return VoicePrompt(
        prompt_id="prompt-a",
        audio_path=prompt,
        license_spdx="CC0-1.0",
        rights_basis="test fixture",
        speaker_axis="speaker-a",
        prompt_text="프롬프트 문장입니다.",
    )


class _Generator:
    """Returns the scripted durations in order, writing a file for each."""

    def __init__(self, output_dir: Path, durations: list[float | Exception]) -> None:
        self._output_dir = output_dir
        self._durations = list(durations)
        self.calls = 0

    def synthesise(self, *, prompt_id, text, voice, speed_bucket):
        self.calls += 1
        outcome = self._durations.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        # One content-addressed name for these inputs, exactly as the real generator.
        name = hashlib.sha256(
            f"{prompt_id}|{text}|{voice.prompt_id}|{speed_bucket}".encode()
        ).hexdigest()
        path = self._output_dir / f"{name}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise ReferenceGeneratorError("output_path_already_exists")
        path.write_bytes(b"RIFF-stub")
        exemplar = ReferenceExemplar(
            exemplar_id=name,
            prompt_id=prompt_id,
            generator_id="stub",
            generator_revision="stub-v1",
            audio_sha256="0" * 64,
            duration_seconds=float(outcome),
            sample_rate=24_000,
            speaker_axis=voice.speaker_axis,
            speed_axis=speed_bucket,
            license_spdx=voice.license_spdx,
            rights_basis=voice.rights_basis,
            synthetic=True,
            disclosure="이 참조 음성은 AI가 합성한 것입니다.",
        )
        return exemplar, path

    def _render(self, tmp_path: Path, bucket: str = "neutral", attempts: int = 3):
        return generate.render_cell(
            self,
            sentence=SENTENCE,
            voice=_voice(tmp_path),
            bucket=bucket,
            band=BAND,
            max_attempts=attempts,
            rejected_dir=tmp_path / "audio" / "rejected",
            manifest_dir=tmp_path,
        )


# -- the rate is measured on the same quantity the band was measured on -------


def _rendering(
    path: Path,
    *,
    speech_seconds: float,
    lead_silence: float = 0.0,
    trail_silence: float = 0.0,
    sample_rate: int = 24_000,
) -> Path:
    """Speech surrounded by silence, as a real rendering is."""

    t = np.arange(int(sample_rate * speech_seconds)) / sample_rate
    speech = 0.3 * np.sin(2 * np.pi * 180 * t)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(
        path,
        np.concatenate(
            [
                np.zeros(int(sample_rate * lead_silence)),
                speech,
                np.zeros(int(sample_rate * trail_silence)),
            ]
        ).astype("float32"),
        sample_rate,
    )
    return path


def test_the_spoken_region_excludes_leading_and_trailing_silence(
    tmp_path: Path,
) -> None:
    """The band came from the corpus's SpeechEnd - SpeechStart, not file length.

    Measured on the real prompt set this decided the verdict for five of twelve
    voices: 3.39 syl/s whole-file against 5.12 on the spoken region.
    """

    path = _rendering(
        tmp_path / "a.wav", speech_seconds=2.0, lead_silence=0.6, trail_silence=0.8
    )

    assert sf.info(str(path)).duration == pytest.approx(3.4, abs=0.05)
    assert generate.speech_region_seconds(path) == pytest.approx(2.0, abs=0.06)


def test_a_runaway_rendering_stays_out_of_band_on_the_spoken_region_too(
    tmp_path: Path,
) -> None:
    """The gate must still catch the real defect after the measurement changes."""

    path = _rendering(tmp_path / "runaway.wav", speech_seconds=13.0)

    seconds = generate.speech_region_seconds(path)
    assert seconds is not None
    assert 12 / seconds < 3.846


def test_a_silent_rendering_yields_no_spoken_region(tmp_path: Path) -> None:
    """Silence is absence of a measurement, not a measurement of zero speech."""

    path = tmp_path / "silent.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, np.zeros(24_000).astype("float32"), 24_000)

    assert generate.speech_region_seconds(path) is None


def test_an_unreadable_file_yields_no_spoken_region(tmp_path: Path) -> None:
    assert generate.speech_region_seconds(tmp_path / "absent.wav") is None


# -- the band scales with what was asked for ----------------------------------


def test_the_band_scales_with_the_speed_bucket() -> None:
    """A rendering asked for 0.8x must not be rejected for obeying."""

    slow = generate.rate_bounds(*BAND, 0.8)
    fast = generate.rate_bounds(*BAND, 1.2)

    assert slow == pytest.approx((3.0768, 6.8344))
    assert fast == pytest.approx((4.6152, 10.2516))
    assert slow[0] < BAND[0] < fast[0]


def test_a_slow_rendering_passes_in_the_slow_bucket_and_fails_in_the_fast_one(
    tmp_path: Path,
) -> None:
    """12 syllables in 3.5 s is 3.43 syl/s: inside 0.8x, below 1.2x."""

    accepted = _Generator(tmp_path / "audio", [3.5])._render(tmp_path, bucket="slow")
    rejected = _Generator(tmp_path / "other", [3.5, 3.5, 3.5])._render(
        tmp_path, bucket="fast"
    )

    assert accepted.row is not None
    assert rejected.row is None
    assert rejected.failure["error"].startswith("no rendering fell inside")


# -- retrying, and what happens to what was thrown away -----------------------


def test_a_plausible_rendering_is_accepted_on_the_first_attempt(
    tmp_path: Path,
) -> None:
    generator = _Generator(tmp_path / "audio", [2.0])

    outcome = generator._render(tmp_path)

    assert generator.calls == 1
    assert outcome.rejections == []
    assert outcome.failure is None
    assert outcome.row["speech_rate_syllables_per_second"] == pytest.approx(6.0)


def test_a_runaway_rendering_is_retried_and_the_next_one_accepted(
    tmp_path: Path,
) -> None:
    """12 syllables in 12 s is 1.0 syl/s — the babble tail this gate exists for."""

    generator = _Generator(tmp_path / "audio", [12.0, 2.0])

    outcome = generator._render(tmp_path)

    assert generator.calls == 2
    assert outcome.row is not None
    assert len(outcome.rejections) == 1
    assert outcome.rejections[0]["verdict"] == "too_slow_for_the_band"
    assert "attempt 2" in outcome.message


def test_a_truncated_rendering_is_retried(tmp_path: Path) -> None:
    """12 syllables in 0.8 s is 15 syl/s — nobody speaks that fast."""

    generator = _Generator(tmp_path / "audio", [0.8, 2.0])

    outcome = generator._render(tmp_path)

    assert outcome.row is not None
    assert outcome.rejections[0]["verdict"] == "too_fast_for_the_band"


def test_a_rejected_rendering_is_kept_not_deleted(tmp_path: Path) -> None:
    """The rejected audio is the evidence for the rejection."""

    generator = _Generator(tmp_path / "audio", [12.0, 2.0])

    generator._render(tmp_path)

    kept = sorted((tmp_path / "audio" / "rejected").glob("*.wav"))
    assert len(kept) == 1
    assert kept[0].name.endswith(".attempt1.wav")
    assert kept[0].read_bytes() == b"RIFF-stub"


def test_giving_up_records_every_attempt_and_produces_no_row(tmp_path: Path) -> None:
    generator = _Generator(tmp_path / "audio", [12.0, 13.0, 14.0])

    outcome = generator._render(tmp_path, attempts=3)

    assert generator.calls == 3
    assert outcome.row is None
    assert len(outcome.rejections) == 3
    assert outcome.failure["attempts"] == 3
    assert outcome.failure["last_speech_rate_syllables_per_second"] == pytest.approx(
        12 / 14.0, abs=1e-4
    )
    assert len(list((tmp_path / "audio" / "rejected").glob("*.wav"))) == 3


def test_a_generator_error_stops_the_cell_without_inventing_a_row(
    tmp_path: Path,
) -> None:
    generator = _Generator(
        tmp_path / "audio", [ReferenceGeneratorError("runtime unavailable")]
    )

    outcome = generator._render(tmp_path)

    assert outcome.row is None
    assert outcome.failure["error"] == "runtime unavailable"
    assert outcome.failure["attempts"] == 1


# -- the band comes from a pinned measurement ---------------------------------


def _band_artifact(tmp_path: Path, *, utterances: int = 100, low=3.846, high=8.543):
    path = tmp_path / "band.json"
    path.write_text(
        json.dumps(
            {
                "utterances": utterances,
                "band": {
                    "low_syllables_per_second": low,
                    "high_syllables_per_second": high,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _config(path: Path, *, utterances: int = 100, digest: str | None = None) -> dict:
    return {
        "canonical_sources": {
            "read_speech_rate_band": {
                "path": path.name,
                "sha256": digest
                if digest is not None
                else hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        },
        "candidate_design": {
            "plausibility_gate": {
                "band_source_utterances": utterances,
                "max_attempts": 3,
            }
        },
    }


def test_the_band_is_read_from_the_pinned_artifact(tmp_path: Path) -> None:
    path = _band_artifact(tmp_path)

    assert generate._gate_rate_band(_config(path), tmp_path) == pytest.approx(
        (3.846, 8.543)
    )


def test_a_band_whose_digest_drifted_is_refused(tmp_path: Path) -> None:
    path = _band_artifact(tmp_path)
    config = _config(path)
    path.write_text(
        json.dumps(
            {
                "utterances": 100,
                "band": {
                    "low_syllables_per_second": 0.5,
                    "high_syllables_per_second": 30.0,
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(generate.GenerationError, match="digest differs"):
        generate._gate_rate_band(config, tmp_path)


def test_a_band_measured_on_different_evidence_than_declared_is_refused(
    tmp_path: Path,
) -> None:
    """The contract states how much evidence the band rests on."""

    path = _band_artifact(tmp_path, utterances=100)

    with pytest.raises(generate.GenerationError, match="utterance count"):
        generate._gate_rate_band(_config(path, utterances=999), tmp_path)


def test_an_inverted_band_is_refused(tmp_path: Path) -> None:
    path = _band_artifact(tmp_path, low=9.0, high=3.0)

    with pytest.raises(generate.GenerationError, match="not an interval"):
        generate._gate_rate_band(_config(path), tmp_path)


def test_an_absent_band_fails_closed(tmp_path: Path) -> None:
    config = {
        "canonical_sources": {
            "read_speech_rate_band": {"path": "gone.json", "sha256": "0" * 64}
        }
    }

    with pytest.raises(generate.GenerationError, match="absent"):
        generate._gate_rate_band(config, tmp_path)
