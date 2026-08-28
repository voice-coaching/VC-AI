"""Conversion may change a file's container facts, never what was captured.

Every test here exists for one failure: a 16 kHz phone recording arriving as a file
whose header says 48000, with nothing downstream able to tell. The output header
does say 48000 — that is the point of the tool — so the provenance record has to
carry the difference.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import sys

import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "normalize_d08", ROOT / "scripts" / "normalize_d08_recordings.py"
)
normalize = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
# Registered before exec so the module's own dataclasses can resolve their module.
sys.modules[_spec.name] = normalize
_spec.loader.exec_module(normalize)

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are not installed",
)


def _write(
    path: Path,
    *,
    sample_rate: int = 44_100,
    channels: int = 2,
    seconds: float = 1.0,
    subtype: str = "PCM_16",
    amplitude: float = 0.3,
) -> Path:
    t = np.arange(int(sample_rate * seconds)) / sample_rate
    tone = amplitude * np.sin(2 * np.pi * 220 * t)
    data = np.column_stack([tone] * channels) if channels > 1 else tone
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, data.astype("float32"), sample_rate, subtype=subtype)
    return path


@requires_ffmpeg
def test_a_stereo_44100_recording_becomes_the_target_format(tmp_path: Path) -> None:
    _write(tmp_path / "in" / "SPK01_R00-01_natural_take1.wav")

    report = normalize.normalise_directory(tmp_path / "in", tmp_path / "out")

    assert report["converted"] == 1
    assert report["failed"] == 0
    out = tmp_path / "out" / report["recordings"][0]["output_relative_path"]
    info = sf.info(str(out))
    assert info.samplerate == 48_000
    assert info.channels == 1
    assert info.subtype == "PCM_24"


@requires_ffmpeg
def test_downmix_and_resample_are_recorded(tmp_path: Path) -> None:
    _write(tmp_path / "in" / "a.wav", sample_rate=44_100, channels=2)

    report = normalize.normalise_directory(tmp_path / "in", tmp_path / "out")
    record = report["recordings"][0]

    assert record["transformations"]["downmixed"] is True
    assert record["transformations"]["resampled"] is True
    assert record["capture"]["native_sample_rate_hz"] == 44_100
    assert record["capture"]["native_channel_count"] == 2


@requires_ffmpeg
def test_an_upsampled_file_is_not_claimed_as_verified_bandwidth(
    tmp_path: Path,
) -> None:
    """The header will read 48000 either way; this is the field that does not."""

    _write(tmp_path / "in" / "phone.wav", sample_rate=16_000, channels=1)

    report = normalize.normalise_directory(tmp_path / "in", tmp_path / "out")
    record = report["recordings"][0]

    out = tmp_path / "out" / record["output_relative_path"]
    assert sf.info(str(out)).samplerate == 48_000
    assert record["transformations"]["upsampled"] is True
    assert record["bandwidth_verified"] is False
    assert record["capture"]["native_sample_rate_hz"] == 16_000
    assert report["bandwidth_unverified_count"] == 1


@requires_ffmpeg
def test_a_natively_48k_file_keeps_verified_bandwidth(tmp_path: Path) -> None:
    _write(tmp_path / "in" / "good.wav", sample_rate=48_000, channels=1,
           subtype="PCM_24")

    report = normalize.normalise_directory(tmp_path / "in", tmp_path / "out")
    record = report["recordings"][0]

    assert record["bandwidth_verified"] is True
    assert record["transformations"]["upsampled"] is False
    assert report["bandwidth_unverified_count"] == 0


@requires_ffmpeg
def test_a_lossy_source_is_flagged(tmp_path: Path) -> None:
    import subprocess

    source = _write(tmp_path / "raw" / "a.wav", sample_rate=44_100, channels=1)
    lossy = tmp_path / "in" / "a.mp3"
    lossy.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(source), str(lossy)],
        check=True,
        timeout=120,
    )

    report = normalize.normalise_directory(tmp_path / "in", tmp_path / "out")
    record = report["recordings"][0]

    assert record["transformations"]["transcoded_from_lossy"] is True
    assert report["lossy_source_count"] == 1


@requires_ffmpeg
def test_clipping_is_measured_against_the_d08_limit(tmp_path: Path) -> None:
    _write(tmp_path / "in" / "hot.wav", sample_rate=48_000, channels=1,
           amplitude=2.0)

    report = normalize.normalise_directory(tmp_path / "in", tmp_path / "out")
    record = report["recordings"][0]

    assert record["clipping_ratio"] > 0.001
    assert record["clipping_within_d08_limit"] is False
    assert report["clipping_violation_count"] == 1


@requires_ffmpeg
def test_what_conversion_cannot_supply_stays_empty(tmp_path: Path) -> None:
    """No amount of signal processing recovers the microphone distance."""

    _write(tmp_path / "in" / "a.wav")

    record = normalize.normalise_directory(tmp_path / "in", tmp_path / "out")[
        "recordings"
    ][0]

    assert record["mouth_to_microphone_distance_cm"] is None
    assert record["consent_receipt_sha256"] is None
    assert "mouth_to_microphone_distance_cm" in record["requires_session_log_fields"]
    assert "consent_receipt_sha256" in record["requires_session_log_fields"]


@requires_ffmpeg
def test_the_source_stem_is_recorded_only_as_a_namespaced_digest(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "in" / "kim_minsu_take1.wav")

    record = normalize.normalise_directory(tmp_path / "in", tmp_path / "out")[
        "recordings"
    ][0]

    assert len(record["item_id_sha256"]) == 64
    assert "kim_minsu" not in str(record)


@requires_ffmpeg
def test_an_existing_output_is_never_overwritten(tmp_path: Path) -> None:
    _write(tmp_path / "in" / "a.wav")
    first = normalize.normalise_directory(tmp_path / "in", tmp_path / "out")
    produced = tmp_path / "out" / first["recordings"][0]["output_relative_path"]
    before = normalize.sha256_file(produced)

    report = normalize.normalise_directory(tmp_path / "in", tmp_path / "out")

    assert report["converted"] == 0
    assert report["failed"] == 1
    assert "already exists" in report["failures"][0]["error"]
    assert normalize.sha256_file(produced) == before


@requires_ffmpeg
def test_a_non_audio_file_fails_without_stopping_the_batch(tmp_path: Path) -> None:
    _write(tmp_path / "in" / "good.wav")
    (tmp_path / "in" / "notes.txt").write_text("not audio", encoding="utf-8")

    report = normalize.normalise_directory(tmp_path / "in", tmp_path / "out")

    assert report["converted"] == 1
    assert report["failed"] == 1


def test_an_absent_input_directory_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(normalize.NormalisationError, match="does not exist"):
        normalize.normalise_directory(tmp_path / "absent", tmp_path / "out")


def test_an_empty_input_directory_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "in").mkdir()

    with pytest.raises(normalize.NormalisationError, match="no files"):
        normalize.normalise_directory(tmp_path / "in", tmp_path / "out")


def test_the_target_constants_match_the_d08_schema() -> None:
    """If the schema moves, this test is where the mismatch shows up."""

    import json

    schema = json.loads(
        (ROOT / "configs/reference/d08_recording_metadata_v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    row = schema["$defs"]["recordingRow"]["properties"]

    assert row["sample_rate_hz"]["const"] == normalize.TARGET_SAMPLE_RATE
    assert row["bit_depth"]["const"] == normalize.TARGET_BIT_DEPTH
    assert row["channel_count"]["const"] == normalize.TARGET_CHANNELS
    assert row["container_format"]["const"] == normalize.TARGET_CONTAINER
    assert row["codec"]["const"] == normalize.TARGET_CODEC


# -- the naming convention ----------------------------------------------------


def test_the_convention_parses_into_manifest_fields() -> None:
    parsed = normalize.parse_stem("SPK01_R00-07_natural_take2")

    assert parsed == {
        "speaker_code": "SPK01",
        "sentence_id": "R00-07",
        "speaking_style": "natural",
        "take_index": 2,
    }


def test_clear_is_accepted_as_the_educational_style() -> None:
    """The instructions write `clear_take3`; the schema enum is clear_educational."""

    parsed = normalize.parse_stem("SPK02_R00-20_clear_take3")

    assert parsed is not None
    assert parsed["speaking_style"] == "clear_educational"


@pytest.mark.parametrize(
    "stem",
    [
        "kim_minsu_take1",
        "SPK01_R00-21_natural_take1",
        "SPK01_R00-07_shouted_take1",
        "SPK01_R00-07_natural",
        "SPK01_R00-07_natural_take0",
    ],
)
def test_a_name_outside_the_convention_does_not_parse(stem: str) -> None:
    assert normalize.parse_stem(stem) is None


@requires_ffmpeg
def test_a_conventional_name_yields_its_fields(tmp_path: Path) -> None:
    _write(tmp_path / "in" / "SPK03_R00-12_clear_take3.wav")

    record = normalize.normalise_directory(tmp_path / "in", tmp_path / "out")[
        "recordings"
    ][0]

    assert record["naming_convention_parsed"] is True
    assert record["parsed_fields"]["sentence_id"] == "R00-12"
    assert record["parsed_fields"]["speaking_style"] == "clear_educational"
    assert record["parsed_fields"]["take_index"] == 3


@requires_ffmpeg
def test_an_unparsed_name_pushes_those_fields_to_the_session_log(
    tmp_path: Path,
) -> None:
    """Guessing sentence or take from an arbitrary name would invent metadata."""

    _write(tmp_path / "in" / "recording 4 (final).wav")

    report = normalize.normalise_directory(tmp_path / "in", tmp_path / "out")
    record = report["recordings"][0]

    assert record["naming_convention_parsed"] is False
    assert record["parsed_fields"] is None
    assert "sentence_id" in record["requires_session_log_fields"]
    assert "take_index" in record["requires_session_log_fields"]
    assert report["unparsed_name_count"] == 1


@requires_ffmpeg
def test_the_output_file_is_named_by_digest_not_by_the_delivered_stem(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "in" / "kim_minsu_take1.wav")

    record = normalize.normalise_directory(tmp_path / "in", tmp_path / "out")[
        "recordings"
    ][0]

    assert record["output_relative_path"] == f"{record['item_id_sha256']}.wav"
    assert not (tmp_path / "out" / "kim_minsu_take1.wav").exists()
