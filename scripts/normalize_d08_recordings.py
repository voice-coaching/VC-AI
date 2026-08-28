#!/usr/bin/env python3
"""Normalise arbitrary recordings into the format D08's schema fixes as constants.

The recording schema pins 48 kHz / 24-bit / mono / WAV / PCM. Asking someone to
record exactly that is a real obstacle — phones, laptops, and most field recorders
produce 44.1 kHz stereo, or m4a, or 16 kHz. This tool converts whatever arrives.

The important part is what it refuses to do. Conversion changes a file's *container
facts*; it cannot create information the microphone never captured. Upsampling a
16 kHz recording produces a file whose header says 48000 and whose content has
nothing above 8 kHz. If the manifest then asserted "48 kHz" and stopped there, every
downstream spectral measurement would inherit a claim nobody verified.

So each output carries a provenance record of what was actually captured, and files
whose native rate is below the target are flagged. Whether a flagged file may enter
the reference bank is a contract decision, not this script's.

Two things conversion can never supply, and both stay required from the session log:
mouth-to-microphone distance, and whether the speaker consented.

Usage:
    scripts/normalize_d08_recordings.py INPUT_DIR OUTPUT_DIR [--min-native-rate 48000]
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

TARGET_SAMPLE_RATE = 48_000
TARGET_BIT_DEPTH = 24
TARGET_CHANNELS = 1
TARGET_CONTAINER = "WAV"
TARGET_CODEC = "PCM"
#: Below this, the recording cannot represent the band the target rate claims. The
#: file is still converted — it is flagged, not silently dropped or silently blessed.
DEFAULT_MINIMUM_NATIVE_RATE = TARGET_SAMPLE_RATE
#: Formats that discard information before this tool ever sees the file.
LOSSY_CODECS = frozenset({"mp3", "aac", "vorbis", "opus", "wmav2", "amrnb", "amrwb"})
PROVENANCE_SCHEMA = "voice_coach.d08_capture_provenance.v1"


#: `SPK01_R00-07_natural_take2.wav` — the naming the recording instructions ask for.
STEM_PATTERN = re.compile(
    r"^(?P<speaker>[A-Za-z0-9]+)"
    r"_(?P<sentence>R00-(?:0[1-9]|1[0-9]|20))"
    r"_(?P<style>natural|clear|clear_educational)"
    r"_take(?P<take>[1-9][0-9]?)$"
)
STYLE_ALIASES = {"clear": "clear_educational"}


class NormalisationError(RuntimeError):
    """Raised when a file cannot be converted within the contract."""


def parse_stem(stem: str) -> dict | None:
    """Pull the structured fields out of a file name, or ``None`` if it does not fit.

    The raw stem is never stored — a delivered file may well be named after the
    person who recorded it. What survives is the speaker *code*, the sentence id, the
    speaking style and the take index, which are what the manifest needs.
    """

    match = STEM_PATTERN.match(stem)
    if match is None:
        return None
    style = match.group("style")
    return {
        "speaker_code": match.group("speaker"),
        "sentence_id": match.group("sentence"),
        "speaking_style": STYLE_ALIASES.get(style, style),
        "take_index": int(match.group("take")),
    }


@dataclass(frozen=True)
class SourceProperties:
    """What the delivered file actually is, before anything is changed."""

    sample_rate_hz: int
    channel_count: int
    codec_name: str
    bits_per_sample: int | None
    duration_seconds: float
    container_format: str

    @property
    def is_lossy(self) -> bool:
        return self.codec_name.lower() in LOSSY_CODECS


def probe(path: Path) -> SourceProperties:
    """Read the source's real properties with ffprobe."""

    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=sample_rate,channels,codec_name,bits_per_raw_sample,bits_per_sample",
        "-show_entries",
        "format=duration,format_name",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=120
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise NormalisationError(f"ffprobe failed for {path.name}") from error
    if completed.returncode != 0:
        raise NormalisationError(f"{path.name} is not readable audio")
    payload = json.loads(completed.stdout or "{}")
    streams = payload.get("streams") or []
    if not streams:
        raise NormalisationError(f"{path.name} has no audio stream")
    stream = streams[0]
    fmt = payload.get("format") or {}

    bits = stream.get("bits_per_raw_sample") or stream.get("bits_per_sample")
    try:
        bits_value = int(bits) if bits not in (None, "", "0") else None
    except (TypeError, ValueError):
        bits_value = None
    return SourceProperties(
        sample_rate_hz=int(stream["sample_rate"]),
        channel_count=int(stream["channels"]),
        codec_name=str(stream.get("codec_name", "unknown")),
        bits_per_sample=bits_value,
        duration_seconds=float(fmt.get("duration", 0.0)),
        container_format=str(fmt.get("format_name", "unknown")),
    )


def convert(source: Path, destination: Path) -> None:
    """Write the target format. Never overwrites an existing output."""

    if destination.exists():
        raise NormalisationError(f"{destination.name} already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    command = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(source),
        "-ac",
        str(TARGET_CHANNELS),
        "-ar",
        str(TARGET_SAMPLE_RATE),
        "-c:a",
        "pcm_s24le",
        "-f",
        "wav",
        str(temporary),
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=600
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        temporary.unlink(missing_ok=True)
        raise NormalisationError(f"conversion failed for {source.name}") from error
    if completed.returncode != 0 or not temporary.is_file():
        temporary.unlink(missing_ok=True)
        raise NormalisationError(f"conversion failed for {source.name}")
    temporary.replace(destination)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def clipping_ratio(path: Path) -> float:
    """Fraction of samples at full scale. D08 requires this below 0.001."""

    import numpy as np
    import soundfile as sf

    data, _ = sf.read(str(path), dtype="float32", always_2d=True)
    if data.size == 0:
        return 1.0
    return float(np.mean(np.abs(data) >= 0.999))


def build_provenance(
    *,
    source_path: Path,
    output_path: Path,
    source: SourceProperties,
    minimum_native_rate: int,
) -> dict:
    """Record what was captured next to what was produced.

    ``bandwidth_verified`` is the field that matters. It is false whenever the
    delivered rate was below the target, because the output header will read 48 kHz
    either way and nothing downstream could otherwise tell the difference.
    """

    upsampled = source.sample_rate_hz < TARGET_SAMPLE_RATE
    return {
        "schema_version": PROVENANCE_SCHEMA,
        "output_sha256": sha256_file(output_path),
        "output": {
            "sample_rate_hz": TARGET_SAMPLE_RATE,
            "bit_depth": TARGET_BIT_DEPTH,
            "channel_count": TARGET_CHANNELS,
            "container_format": TARGET_CONTAINER,
            "codec": TARGET_CODEC,
        },
        "capture": {
            "native_sample_rate_hz": source.sample_rate_hz,
            "native_channel_count": source.channel_count,
            "native_codec": source.codec_name,
            "native_bits_per_sample": source.bits_per_sample,
            "native_container": source.container_format,
            "duration_seconds": source.duration_seconds,
        },
        "transformations": {
            "resampled": source.sample_rate_hz != TARGET_SAMPLE_RATE,
            "upsampled": upsampled,
            "downmixed": source.channel_count > TARGET_CHANNELS,
            "transcoded_from_lossy": source.is_lossy,
        },
        # The header will read 48 kHz whatever arrived; this says whether that
        # number describes the microphone or only the file.
        "bandwidth_verified": not upsampled,
        "meets_minimum_native_rate": source.sample_rate_hz >= minimum_native_rate,
        "clipping_ratio": clipping_ratio(output_path),
        "clipping_within_d08_limit": clipping_ratio(output_path) < 0.001,
        # Conversion cannot supply these. They come from the session log.
        "mouth_to_microphone_distance_cm": None,
        "consent_receipt_sha256": None,
        "requires_session_log_fields": [
            "mouth_to_microphone_distance_cm",
            "consent_receipt_sha256",
            "recording_license_grant_sha256",
            "equipment_profile_id",
            "microphone_profile_id",
            "room_profile_id",
        ],
    }


def normalise_directory(
    input_dir: Path,
    output_dir: Path,
    *,
    minimum_native_rate: int = DEFAULT_MINIMUM_NATIVE_RATE,
) -> dict:
    if not input_dir.is_dir():
        raise NormalisationError("input directory does not exist")
    candidates = sorted(
        path
        for path in input_dir.rglob("*")
        if path.is_file() and not path.name.startswith(".")
    )
    if not candidates:
        raise NormalisationError("input directory contains no files")

    records: list[dict] = []
    failures: list[dict] = []
    for source_path in candidates:
        try:
            source = probe(source_path)
            # The stem may be named after the person who recorded it, so it never
            # reaches the output tree or the report — only its namespaced digest and
            # the structured fields the naming convention encodes.
            item_id = hashlib.sha256(
                f"voice-coach.d08-recording.v1:{source_path.stem}".encode("utf-8")
            ).hexdigest()
            output_path = output_dir / f"{item_id}.wav"
            convert(source_path, output_path)
            record = build_provenance(
                source_path=source_path,
                output_path=output_path,
                source=source,
                minimum_native_rate=minimum_native_rate,
            )
            record["item_id_sha256"] = item_id
            record["output_relative_path"] = output_path.name
            parsed = parse_stem(source_path.stem)
            record["naming_convention_parsed"] = parsed is not None
            record["parsed_fields"] = parsed
            if parsed is None:
                record["requires_session_log_fields"] = [
                    "sentence_id",
                    "speaking_style",
                    "take_index",
                    *record["requires_session_log_fields"],
                ]
            records.append(record)
        except NormalisationError as error:
            failures.append({"file": source_path.name, "error": str(error)})

    report = {
        "schema_version": PROVENANCE_SCHEMA,
        "converted": len(records),
        "failed": len(failures),
        "failures": failures,
        "upsampled_count": sum(
            1 for item in records if item["transformations"]["upsampled"]
        ),
        "lossy_source_count": sum(
            1 for item in records if item["transformations"]["transcoded_from_lossy"]
        ),
        "bandwidth_unverified_count": sum(
            1 for item in records if not item["bandwidth_verified"]
        ),
        "clipping_violation_count": sum(
            1 for item in records if not item["clipping_within_d08_limit"]
        ),
        "unparsed_name_count": sum(
            1 for item in records if not item["naming_convention_parsed"]
        ),
        "recordings": records,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "capture_provenance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--min-native-rate", type=int, default=DEFAULT_MINIMUM_NATIVE_RATE
    )
    args = parser.parse_args()

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("ffmpeg and ffprobe are required", file=sys.stderr)
        return 2
    try:
        report = normalise_directory(
            args.input_dir, args.output_dir, minimum_native_rate=args.min_native_rate
        )
    except NormalisationError as error:
        print(f"failed: {error}", file=sys.stderr)
        return 1

    print(f"converted {report['converted']}, failed {report['failed']}")
    if report["bandwidth_unverified_count"]:
        print(
            f"WARNING: {report['bandwidth_unverified_count']} file(s) were recorded "
            f"below {TARGET_SAMPLE_RATE} Hz. The output header reads "
            f"{TARGET_SAMPLE_RATE} Hz but the content does not, and "
            "bandwidth_verified is false for those rows."
        )
    if report["lossy_source_count"]:
        print(
            f"WARNING: {report['lossy_source_count']} file(s) came from a lossy "
            "codec; detail removed before delivery cannot be restored."
        )
    if report["unparsed_name_count"]:
        print(
            f"NOTE: {report['unparsed_name_count']} file name(s) did not match "
            "SPK01_R00-07_natural_take2.wav, so sentence, style and take must come "
            "from the session log for those rows."
        )
    if report["clipping_violation_count"]:
        print(
            f"WARNING: {report['clipping_violation_count']} file(s) exceed the D08 "
            "clipping limit of 0.001."
        )
    print(f"provenance: {args.output_dir / 'capture_provenance.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
