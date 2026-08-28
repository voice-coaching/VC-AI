#!/usr/bin/env python3
"""Render the TTS half of each human/TTS pair and extract the human half.

Reads the pair manifest, pulls the human recording and the prompt utterance
straight out of the corpus archives, renders the same text in the same speaker's
voice, and writes both halves side by side with one row describing the pair.

Two choices worth stating.

The speech-rate gate still applies to the rendered half. A discriminator trained
against obviously broken synthesis -- the runaways that fill their token budget --
learns "long means synthetic" and nothing about synthesis. Rejected renderings are
kept, so the rejection rate is itself a measurement of the generator.

The human half is never gated. It is the definition of the positive class; filtering
it by anything would make the class mean "human recordings that passed a filter I
wrote", which is not what the evaluator should learn.

Usage:
    render_human_tts_pairs.py PAIR_MANIFEST --human-audio ZIP [--human-audio ZIP]
                              --out DIR [--limit N] [--workers 6]
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import threading
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from voice_coach.pronunciation_feedback.adapters.reference_generator import (  # noqa: E402
    CosyVoiceReferenceGenerator,
    ReferenceGeneratorError,
    VoicePrompt,
)

_spec = importlib.util.spec_from_file_location(
    "generate_r01", Path(__file__).resolve().parent / "generate_r01_candidates.py"
)
_generate = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules[_spec.name] = _generate
_spec.loader.exec_module(_generate)
speech_region_seconds = _generate.speech_region_seconds
rate_bounds = _generate.rate_bounds

MODEL_DIR = Path(
    "data/cache/huggingface/hub/models--FunAudioLLM--CosyVoice2-0.5B/snapshots"
    "/eec1ae6c79877dbd9379285cf8789c9e0879293d"
)
RATE_BAND = Path("artifacts/reference/r01/korean_read_speech_rate_band_v1.json")
DISCLOSURE = "이 참조 음성은 AI가 합성한 것입니다."
THREADS_PER_WORKER = 3


class PairRenderError(RuntimeError):
    """Raised when the pair set cannot be rendered."""


class CorpusAudio:
    """Every corpus archive, indexed by member basename."""

    def __init__(self, archives: list[Path]) -> None:
        self._zips = [zipfile.ZipFile(path) for path in archives]
        self._index: dict[str, tuple[int, zipfile.ZipInfo]] = {}
        for position, archive in enumerate(self._zips):
            for info in archive.infolist():
                if info.is_dir():
                    continue
                self._index.setdefault(
                    PurePosixPath(info.filename).name, (position, info)
                )

    def extract(self, file_name: str, destination: Path) -> bool:
        found = self._index.get(file_name)
        if found is None:
            return False
        position, info = found
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._zips[position].open(info) as source, destination.open("wb") as out:
            shutil.copyfileobj(source, out)
        return True


def _band() -> tuple[float, float]:
    payload = json.loads(RATE_BAND.read_text(encoding="utf-8"))["band"]
    return (
        float(payload["low_syllables_per_second"]),
        float(payload["high_syllables_per_second"]),
    )


def render(
    manifest_path: Path,
    human_archives: list[Path],
    output_dir: Path,
    *,
    limit: int | None,
    workers: int,
) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = manifest["pair_rows"][:limit] if limit else manifest["pair_rows"]
    if not rows:
        raise PairRenderError("the pair manifest holds no row")

    corpus = CorpusAudio(human_archives)
    low, high = _band()
    root = Path(__file__).resolve().parents[1]
    human_dir = output_dir / "human"
    tts_dir = output_dir / "tts"
    prompt_dir = output_dir / "prompts"
    rejected_dir = output_dir / "tts_rejected"

    if workers > 1:
        os.environ.setdefault("OMP_NUM_THREADS", str(THREADS_PER_WORKER))
        os.environ.setdefault("MKL_NUM_THREADS", str(THREADS_PER_WORKER))
    generator = CosyVoiceReferenceGenerator(
        repository_root=root,
        model_dir=root / MODEL_DIR,
        output_dir=tts_dir,
        disclosure=DISCLOSURE,
        gpu_allowed=False,
    )

    produced: list[dict] = []
    failures: list[dict] = []
    lock = threading.Lock()
    done = 0

    def one(index_row: tuple[int, dict]) -> None:
        nonlocal done
        _, row = index_row
        # Named by a digest of the pair, so the corpus file name -- which carries the
        # speaker's initials -- never becomes a path under the output tree.
        pair_id = hashlib.sha256(
            f"{row['speaker_axis']}|{row['human_file_name']}".encode("utf-8")
        ).hexdigest()
        note = None
        human_path = human_dir / f"{pair_id}.wav"
        prompt_path = prompt_dir / f"{pair_id}.prompt.wav"
        if not corpus.extract(row["human_file_name"], human_path):
            note = "human_audio_not_found"
        elif not corpus.extract(row["tts_prompt_file_name"], prompt_path):
            note = "prompt_audio_not_found"
        if note is not None:
            with lock:
                done += 1
                failures.append({"pair_id": pair_id, "error": note})
                print(f"[{done}/{len(rows)}] FAILED {note}", flush=True)
            return

        try:
            voice = VoicePrompt(
                prompt_id=row["speaker_axis"],
                audio_path=prompt_path,
                license_spdx="AIHub-Terms",
                rights_basis=(
                    "AI Hub 542 dataset use approved for this account; the speaker's "
                    "own utterance prompts a rendering of the speaker's own sentence "
                    "under DEV-R01-002. Generated audio is not redistributed."
                ),
                speaker_axis=row["speaker_axis"],
                prompt_text=row["tts_prompt_text"],
            )
            exemplar, rendered = generator.synthesise(
                prompt_id=pair_id,
                text=row["text"],
                voice=voice,
                speed_bucket="neutral",
            )
        except ReferenceGeneratorError as error:
            with lock:
                done += 1
                failures.append({"pair_id": pair_id, "error": str(error)})
                print(f"[{done}/{len(rows)}] FAILED {error}", flush=True)
            return

        spoken = speech_region_seconds(rendered)
        seconds = spoken or exemplar.duration_seconds
        rate = row["target_syllables"] / seconds
        bounds = rate_bounds(low, high, 1.0)
        if not bounds[0] <= rate <= bounds[1]:
            rejected_dir.mkdir(parents=True, exist_ok=True)
            rendered.replace(rejected_dir / f"{pair_id}.wav")
            with lock:
                done += 1
                failures.append(
                    {
                        "pair_id": pair_id,
                        "error": "tts_outside_speech_rate_band",
                        "speech_rate_syllables_per_second": round(rate, 4),
                    }
                )
                print(
                    f"[{done}/{len(rows)}] REJECTED {rate:.2f} syl/s", flush=True
                )
            return

        with lock:
            done += 1
            produced.append(
                {
                    "pair_id": pair_id,
                    "speaker_axis": row["speaker_axis"],
                    "gender": row["gender"],
                    "age_band": row["age_band"],
                    "text": row["text"],
                    "target_syllables": row["target_syllables"],
                    "human_relative_path": f"human/{pair_id}.wav",
                    "tts_relative_path": f"tts/{rendered.name}",
                    "tts_speech_rate_syllables_per_second": round(rate, 4),
                    "tts_generator_revision": exemplar.generator_revision,
                    "synthetic_disclosure": DISCLOSURE,
                }
            )
            print(f"[{done}/{len(rows)}] ok {rate:.2f} syl/s", flush=True)

    numbered = list(enumerate(rows))
    if workers == 1:
        for item in numbered:
            one(item)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(one, numbered))

    produced.sort(key=lambda item: item["pair_id"])
    failures.sort(key=lambda item: item["pair_id"])
    report = {
        "schema_version": "voice_coach.human_tts_pair_render.v1",
        "storage_zone": "R1_restricted_locator",
        "speaker_axis_is_pseudonymous": True,
        "pairs_requested": len(rows),
        "pairs_rendered": len(produced),
        "pairs_failed": len(failures),
        "tts_rate_band": [round(low, 4), round(high, 4)],
        "human_side_gated": False,
        "human_side_note_ko": (
            "사람 쪽은 어떤 필터도 걸지 않는다. 양성 클래스의 정의 자체이므로,"
            " 거르면 '내가 쓴 필터를 통과한 사람 녹음'이라는 다른 것을 배우게 된다."
        ),
        "failures": failures,
        "pairs": produced,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "pair_render_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pair_manifest", type=Path)
    parser.add_argument("--human-audio", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    try:
        report = render(
            args.pair_manifest,
            args.human_audio,
            args.out,
            limit=args.limit,
            workers=args.workers,
        )
    except (PairRenderError, OSError) as error:
        print(f"failed: {error}", file=sys.stderr)
        return 1
    print(
        f"rendered {report['pairs_rendered']}/{report['pairs_requested']} pairs, "
        f"{report['pairs_failed']} failed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
