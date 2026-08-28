#!/usr/bin/env python3
"""Pair each human utterance with a TTS rendering of the same text, same speaker.

The evaluator is trained to tell a synthesised rendering from a human one. The
obvious way to build that data — humans from the corpus, TTS from a handful of
prompt voices — teaches the wrong thing: with 142 human speakers and 6 prompt
voices, "is this synthetic?" is perfectly predicted by "is this one of the six
voices?", and the resulting evaluator is useless on a speaker it has not seen.

So each pair holds **speaker and text fixed** and varies only synthesis:

    human   = speaker S saying text T          (the corpus recording)
    tts     = speaker S's voice saying text T  (rendered, prompted by a *different*
                                                utterance of the same speaker)

The prompt utterance is drawn from the same speaker but is never the paired
utterance itself, so the generator is not handed the target recording it is being
compared against.

Only labels are read here. No audio is opened and no synthesis is run: this writes
the work list that the renderer and the trainer consume.

Usage:
    build_human_tts_pair_manifest.py LABEL_ZIP [LABEL_ZIP ...] --out PATH
                                     [--pairs-per-speaker 40]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

HANGUL_SYLLABLE = re.compile(r"[가-힣]")
SPEAKER_NAMESPACE = "voice-coach.aihub542-prompt-speaker.v1"
REQUIRED_QUALITY = "Good"
#: The paired utterance. Long enough to judge, short enough to render.
MINIMUM_TARGET_SYLLABLES = 8
MAXIMUM_TARGET_SYLLABLES = 24
#: The prompt utterance, bounded the same way the R01 prompt set is: CosyVoice
#: degrades when the text to synthesise is much shorter than the prompt text.
MINIMUM_PROMPT_SYLLABLES = 12
MAXIMUM_PROMPT_SYLLABLES = 20
PREFERRED_PROMPT_SYLLABLES = 16


class PairManifestError(RuntimeError):
    """Raised when no usable pair can be assembled."""


def speaker_digest(speaker_key: str) -> str:
    return hashlib.sha256(
        f"{SPEAKER_NAMESPACE}:{speaker_key}".encode("utf-8")
    ).hexdigest()


def read_utterances(label_zip: Path) -> list[dict]:
    """Every usable labelled utterance, with no audio read."""

    rows: list[dict] = []
    with zipfile.ZipFile(label_zip) as archive:
        for name in archive.namelist():
            if not name.lower().endswith(".json"):
                continue
            try:
                payload = json.loads(archive.read(name).decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            other = payload.get("기타정보") or {}
            if other.get("QualityStatus") != REQUIRED_QUALITY:
                continue
            speaker = payload.get("화자정보") or {}
            file_info = payload.get("파일정보") or {}
            text = (payload.get("전사정보") or {}).get("TransLabelText") or ""
            directory = str(file_info.get("DirectoryPath") or "").strip("/")
            file_name = file_info.get("FileName")
            if not text or not directory or not file_name:
                continue
            try:
                start = float(other["SpeechStart"])
                end = float(other["SpeechEnd"])
            except (KeyError, TypeError, ValueError):
                continue
            if end <= start:
                continue
            rows.append(
                {
                    "speaker_key": directory,
                    "file_name": file_name,
                    "text": text,
                    "syllables": len(HANGUL_SYLLABLE.findall(text)),
                    "speech_seconds": round(end - start, 4),
                    "gender": speaker.get("Gender", "unknown"),
                    "age_band": speaker.get("Age", "unknown"),
                }
            )
    return rows


def choose_prompt(utterances: list[dict], *, exclude: str) -> dict | None:
    """The speaker's own utterance that best conditions a rendering."""

    candidates = [
        row
        for row in utterances
        if row["file_name"] != exclude
        and MINIMUM_PROMPT_SYLLABLES <= row["syllables"] <= MAXIMUM_PROMPT_SYLLABLES
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda row: (
            abs(row["syllables"] - PREFERRED_PROMPT_SYLLABLES),
            row["file_name"],
        ),
    )


def build(
    label_zips: list[Path], *, pairs_per_speaker: int
) -> dict:
    utterances: list[dict] = []
    for path in label_zips:
        if not path.is_file():
            raise PairManifestError(f"label archive is absent: {path.name}")
        utterances.extend(read_utterances(path))
    if not utterances:
        raise PairManifestError("no label row met the criteria")

    by_speaker: dict[str, list[dict]] = {}
    for row in utterances:
        by_speaker.setdefault(row["speaker_key"], []).append(row)

    pairs: list[dict] = []
    speakers_without_prompt = 0
    for speaker_key in sorted(by_speaker):
        rows = sorted(by_speaker[speaker_key], key=lambda item: item["file_name"])
        targets = [
            row
            for row in rows
            if MINIMUM_TARGET_SYLLABLES <= row["syllables"] <= MAXIMUM_TARGET_SYLLABLES
        ][:pairs_per_speaker]
        if not targets:
            continue
        digest = speaker_digest(speaker_key)
        used_prompt = None
        for target in targets:
            prompt = choose_prompt(rows, exclude=target["file_name"])
            if prompt is None:
                # Another target for this speaker may still find a prompt, so this
                # skips the target rather than abandoning the speaker.
                continue
            used_prompt = prompt
            pairs.append(
                {
                    # The speaker name never leaves the corpus; both sides of the
                    # pair carry the same namespaced digest, which is the point.
                    "speaker_axis": f"aihub542-{digest[:16]}",
                    "gender": target["gender"],
                    "age_band": target["age_band"],
                    "text": target["text"],
                    "target_syllables": target["syllables"],
                    "human_file_name": target["file_name"],
                    "human_speech_seconds": target["speech_seconds"],
                    "tts_prompt_file_name": prompt["file_name"],
                    "tts_prompt_text": prompt["text"],
                    "tts_prompt_syllables": prompt["syllables"],
                }
            )
        if used_prompt is None:
            speakers_without_prompt += 1

    if not pairs:
        raise PairManifestError("no speaker yielded a usable pair")
    genders = {pair["gender"] for pair in pairs}
    return {
        "schema_version": "voice_coach.human_tts_pair_manifest.v1",
        "decision_record": "DEV-R01-002",
        # The corpus file name carries the speaker's initials, and this manifest has
        # to keep it to find the audio inside the archive. So this file is restricted
        # and says so, rather than claiming a pseudonymity it does not have. What is
        # pseudonymous is `speaker_axis` -- the only speaker field that travels
        # downstream into exemplars, receipts and anything a learner could see.
        "contains_corpus_file_names": True,
        "storage_zone": "R1_restricted_locator",
        "speaker_axis_is_pseudonymous": True,
        "pairing_rule": "same_speaker_same_text_prompt_from_a_different_utterance",
        "pairing_rationale_ko": (
            "화자와 문장을 고정하고 합성 여부만 다르게 둔다. 그렇지 않으면 판별자가"
            " 합성 흔적이 아니라 화자 정체를 배운다."
        ),
        "speakers": len(by_speaker),
        "speakers_without_usable_prompt": speakers_without_prompt,
        "pairs": len(pairs),
        "genders": sorted(genders),
        "sources": [path.name for path in label_zips],
        "pair_rows": pairs,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("label_zips", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--pairs-per-speaker", type=int, default=40)
    args = parser.parse_args()

    try:
        manifest = build(args.label_zips, pairs_per_speaker=args.pairs_per_speaker)
    except PairManifestError as error:
        print(f"failed: {error}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"{manifest['pairs']} pairs from {manifest['speakers']} speakers "
        f"({', '.join(manifest['genders'])})"
    )
    print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
