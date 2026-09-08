#!/usr/bin/env python3
"""Select CosyVoice zero-shot prompt voices from AI Hub 542, pseudonymously.

The reference bank needs several speakers or its speaker axis has no spread, and a
tolerance derived from one speaker is not a tolerance. This script picks a small,
diverse set of speakers from the 542 corpus and copies one clean utterance each into
the prompt directory.

What it will not do, per DEV-R01-002:

Speaker names never leave the corpus. `LJS` in the label becomes a namespaced digest,
and the prompt file is named by that digest — a `speaker_axis` that carried a real
name would put it in every exemplar row and every learner-facing record.

Selection is by declared metadata (gender, age band), not by listening, because
nothing here can listen. A speaker whose label says `QualityStatus != Good` is
skipped rather than judged.

The corpus audio itself stays under `data/restricted/`; only the chosen prompt copies
move, and they stay restricted too.

Usage:
    build_prompt_voices_from_aihub542.py AUDIO_ROOT LABEL_ZIP OUTPUT_DIR [--speakers 6]
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import hashlib
from itertools import zip_longest
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import zipfile

SPEAKER_NAMESPACE = "voice-coach.aihub542-prompt-speaker.v1"
#: Prompt length is bounded by the *prompt transcript*, not by taste. CosyVoice warns
#: when the text to synthesise is shorter than half the prompt text, and measured
#: against the R00 sentences a ~45-syllable prompt made the model run past the end of
#: every sentence — 12 to 18 seconds of audio for a three-second sentence. R00's
#: shortest sentence is 10 syllables, so the prompt stays at or under twice that.
MINIMUM_PROMPT_SYLLABLES = 12
MAXIMUM_PROMPT_SYLLABLES = 20
#: Preferred rather than maximal. "Longest available" is what produced the runaway
#: renderings; the middle of the window is what the vendor's own 3.5 s demo prompt
#: looks like.
PREFERRED_PROMPT_SYLLABLES = 16
#: A loose sanity window on duration. The syllable count above is the real bound; this
#: only rejects a label whose boundaries disagree with its transcript.
MINIMUM_PROMPT_SECONDS = 1.5
MAXIMUM_PROMPT_SECONDS = 6.0
HANGUL_SYLLABLE = re.compile(r"[가-힣]")
REQUIRED_QUALITY = "Good"
REQUIRED_SAMPLE_RATE = "48000"
REQUIRED_CHANNELS = "1"


class PromptSelectionError(RuntimeError):
    """Raised when a usable prompt set cannot be assembled."""


def speaker_digest(speaker_key: str) -> str:
    return hashlib.sha256(
        f"{SPEAKER_NAMESPACE}:{speaker_key}".encode("utf-8")
    ).hexdigest()


def speaker_key(speaker: dict, file_info: dict) -> str:
    """What actually identifies one speaker in this corpus.

    ``SpeakerName`` is initials — ``JHJ`` — and 1,529 speakers do not have 1,529
    distinct initials. Keying on it would merge different people into one prompt
    voice and hand the merged group a single digest, which is both wrong about who
    spoke and wrong about how much speaker spread the bank has. ``DirectoryPath``
    (``/0049_G1A2E7_JHJ``) carries the enrolment number and is unique.
    """

    directory = str(file_info.get("DirectoryPath") or "").strip("/")
    return directory or str(speaker.get("SpeakerName", ""))


def read_labels(label_zip: Path) -> list[dict]:
    """Collect the label rows that could serve as a prompt, with no audio read."""

    rows: list[dict] = []
    with zipfile.ZipFile(label_zip) as archive:
        for name in archive.namelist():
            if not name.lower().endswith(".json"):
                continue
            try:
                payload = json.loads(archive.read(name).decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            speech = payload.get("음성정보") or {}
            speaker = payload.get("화자정보") or {}
            file_info = payload.get("파일정보") or {}
            other = payload.get("기타정보") or {}
            transcript = (payload.get("전사정보") or {}).get("TransLabelText") or ""

            if other.get("QualityStatus") != REQUIRED_QUALITY:
                continue
            if speech.get("SamplingRate") != REQUIRED_SAMPLE_RATE:
                continue
            if speech.get("NumberOfChannel") != REQUIRED_CHANNELS:
                continue
            try:
                length = float(file_info.get("FileLength", 0.0))
            except (TypeError, ValueError):
                continue
            if not MINIMUM_PROMPT_SECONDS <= length <= MAXIMUM_PROMPT_SECONDS:
                continue
            syllables = len(HANGUL_SYLLABLE.findall(transcript))
            if not MINIMUM_PROMPT_SYLLABLES <= syllables <= MAXIMUM_PROMPT_SYLLABLES:
                continue
            if not speaker.get("SpeakerName") or not file_info.get("FileName"):
                continue
            rows.append(
                {
                    "speaker_key": speaker_key(speaker, file_info),
                    "syllable_count": syllables,
                    "gender": speaker.get("Gender", "unknown"),
                    "age": speaker.get("Age", "unknown"),
                    "region": speaker.get("Region", "unknown"),
                    "file_name": file_info["FileName"],
                    "directory": file_info.get("DirectoryPath", ""),
                    "length_seconds": length,
                    "transcript": transcript,
                }
            )
    return rows


def group_order(groups: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Visit (gender, age) groups so that consecutive picks change gender.

    Sorting the groups and walking them in order is not enough. 542 has six age
    bands per gender, so ``sorted`` puts all six female groups before the first male
    one and a request for six speakers returns six women — spread across age, and
    across nothing else. Interleaving the genders makes a request for N speakers
    alternate, which is what "spread across gender and age" has to mean.
    """

    by_gender: dict[str, list[tuple[str, str]]] = {}
    for key in sorted(groups):
        by_gender.setdefault(key[0], []).append(key)
    ordered: list[tuple[str, str]] = []
    for row in zip_longest(*by_gender.values()):
        ordered.extend(key for key in row if key is not None)
    return ordered


def choose_speakers(rows: list[dict], *, count: int) -> list[dict]:
    """One utterance per speaker, spreading across gender and age band.

    Round-robin over (gender, age) groups rather than taking the first N rows: the
    corpus is ordered by speaker, so the first N would often be one demographic.
    """

    def distance_from_preferred(row: dict) -> tuple[int, str]:
        # Ties broken on the speaker key so the same corpus yields the same prompts.
        return (
            abs(row["syllable_count"] - PREFERRED_PROMPT_SYLLABLES),
            row["file_name"],
        )

    by_speaker: dict[str, dict] = {}
    for row in rows:
        current = by_speaker.get(row["speaker_key"])
        # Closest to the preferred length, not the longest. Longest is what made the
        # model keep talking past the end of the sentence.
        if current is None or distance_from_preferred(row) < distance_from_preferred(
            current
        ):
            by_speaker[row["speaker_key"]] = row

    groups: dict[tuple[str, str], list[dict]] = {}
    for row in by_speaker.values():
        groups.setdefault((row["gender"], row["age"]), []).append(row)
    for group in groups.values():
        group.sort(key=lambda item: item["speaker_key"])

    chosen: list[dict] = []
    keys = group_order(list(groups))
    index = 0
    while len(chosen) < count and any(groups[key] for key in keys):
        key = keys[index % len(keys)]
        if groups[key]:
            chosen.append(groups[key].pop(0))
        index += 1
    if len(chosen) < count:
        raise PromptSelectionError(
            f"only {len(chosen)} usable speakers found, needed {count}"
        )
    return chosen


def locate_audio(audio_root: Path, row: dict) -> Path | None:
    """Find the utterance under the extracted audio tree."""

    # `is_file` matters: a directory can share the name, and copying one would
    # fail late instead of being reported as "audio not found".
    matches = [path for path in audio_root.rglob(row["file_name"]) if path.is_file()]
    return matches[0] if matches else None


class DirectoryAudioSource:
    """An already-extracted corpus tree."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def locate(self, file_name: str):
        return locate_audio(self._root, {"file_name": file_name})

    def copy_to(self, handle, destination: Path) -> None:
        shutil.copy2(handle, destination)


class ZipAudioSource:
    """The corpus archive, read in place.

    The 542 source archives are tens of gigabytes each and hold ~2,000 utterances
    per speaker. Extracting one to copy six files out would cost more disk than
    everything else in this project put together, so the member is streamed
    straight out of the archive instead.

    Member names are stored in the archive's legacy encoding and come back
    mojibaked, but only the directory components carry Korean — the file name
    itself is ASCII, which is what the label rows key on.
    """

    def __init__(self, archive: Path) -> None:
        self._zip = zipfile.ZipFile(archive)
        self._index: dict[str, zipfile.ZipInfo] = {}
        for info in self._zip.infolist():
            if info.is_dir():
                continue
            self._index.setdefault(PurePosixPath(info.filename).name, info)

    def locate(self, file_name: str):
        return self._index.get(file_name)

    def copy_to(self, handle, destination: Path) -> None:
        with self._zip.open(handle) as source, destination.open("wb") as target:
            shutil.copyfileobj(source, target)


def open_audio_source(audio_root: Path):
    if audio_root.is_dir():
        return DirectoryAudioSource(audio_root)
    if audio_root.is_file() and audio_root.suffix.lower() == ".zip":
        return ZipAudioSource(audio_root)
    raise PromptSelectionError("audio root does not exist")


def build(
    audio_root: Path,
    label_zip: Path,
    output_dir: Path,
    *,
    count: int,
    extra_sources: Sequence[tuple[Path, Path]] = (),
) -> dict:
    """Assemble the prompt set, pooling every (audio, label) pair given.

    542 partitions its archives by gender, so a single pair is a single gender. A
    bank built from one gender would put every female learner outside a male-only
    spread on the pitch axis — the selection has to be able to read more than one
    archive to avoid that.
    """

    pairs = [(audio_root, label_zip), *((Path(a), Path(b)) for a, b in extra_sources)]
    sources = []
    rows: list[dict] = []
    for index, (audio, labels) in enumerate(pairs):
        sources.append(open_audio_source(audio))
        if not labels.is_file():
            raise PromptSelectionError("label archive does not exist")
        for row in read_labels(labels):
            row["source_index"] = index
            rows.append(row)
    if not rows:
        raise PromptSelectionError("no label row met the prompt criteria")
    chosen = choose_speakers(rows, count=count)

    output_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    missing: list[str] = []
    for row in chosen:
        digest = speaker_digest(row["speaker_key"])
        source = sources[row["source_index"]]
        handle = source.locate(row["file_name"])
        if handle is None:
            missing.append(digest[:12])
            continue
        destination = output_dir / f"{digest}.wav"
        if not destination.exists():
            source.copy_to(handle, destination)
        entries.append(
            {
                # The corpus speaker name never appears here or anywhere downstream.
                "prompt_voice_id": f"aihub542-{digest[:16]}",
                "speaker_axis": f"aihub542-{digest[:16]}",
                "audio_relative_path": destination.name,
                "gender": row["gender"],
                "age_band": row["age"],
                "region": row["region"],
                "length_seconds": row["length_seconds"],
                "prompt_transcript": row["transcript"],
                "prompt_syllable_count": row["syllable_count"],
                "license_spdx": "AIHub-Terms",
                "rights_basis": (
                    "AI Hub 542 dataset use approved for this account; used as a "
                    "zero-shot prompt under DEV-R01-002 for non-commercial research. "
                    "Generated audio is not redistributed or published."
                ),
                "source_dataset": "aihub-542",
                "third_party_speaker_cloning": True,
            }
        )

    if not entries:
        raise PromptSelectionError("no chosen speaker's audio could be located")
    manifest = {
        "schema_version": "voice_coach.prompt_voice_manifest.v1",
        "decision_record": "DEV-R01-002",
        "speaker_names_stored": False,
        "requested": count,
        "selected": len(entries),
        "audio_not_found": len(missing),
        "prompt_voices": entries,
    }
    (output_dir / "prompt_voices.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio_root", type=Path)
    parser.add_argument("label_zip", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--speakers", type=int, default=6)
    parser.add_argument(
        "--also",
        nargs=2,
        metavar=("AUDIO", "LABELS"),
        action="append",
        default=[],
        help="another (audio, label) archive pair to pool, e.g. the other gender",
    )
    args = parser.parse_args()

    try:
        manifest = build(
            args.audio_root,
            args.label_zip,
            args.output_dir,
            count=args.speakers,
            extra_sources=[(Path(a), Path(b)) for a, b in args.also],
        )
    except PromptSelectionError as error:
        print(f"failed: {error}", file=sys.stderr)
        return 1
    print(f"selected {manifest['selected']}/{manifest['requested']} prompt voices")
    if manifest["audio_not_found"]:
        print(f"WARNING: {manifest['audio_not_found']} chosen speaker(s) had no audio")
    for entry in manifest["prompt_voices"]:
        print(
            f"  {entry['prompt_voice_id']}  {entry['gender']:8s} {entry['age_band']:8s}"
            f" {entry['length_seconds']:.2f}s"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
