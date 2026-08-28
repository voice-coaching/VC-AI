#!/usr/bin/env python3
"""Measure what an untuned audio LLM already does on human-versus-synthetic.

A single "합성" answer to a single synthetic clip proves nothing: a model that
answers "합성" to everything scores 100% on synthetic clips and 0% on human ones.
So this asks both classes and reports the two accuracies separately, plus the
balanced accuracy that a constant answer cannot inflate.

The baseline matters because fine-tuning has to be shown to have added something.
Without it, a tuned model that reaches 85% is unreadable — the base model might
have been at 84%.

Audio comes straight out of the corpus archive for the human side, so nothing is
copied out of `data/restricted/` beyond the clips being measured.

Usage:
    measure_evaluator_zero_shot_baseline.py PAIR_MANIFEST --human-audio ZIP
        --tts-dir DIR --out PATH [--samples 40] [--model ID]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import zipfile

DEFAULT_MODEL = "Qwen/Qwen2-Audio-7B-Instruct"
#: Asked in Korean because the audio is Korean and the model is instruction-tuned
#: multilingually; an English prompt makes the reply language another variable.
QUESTION = "이 음성은 사람이 직접 녹음한 것입니까, 아니면 기계가 합성한 것입니까? '사람' 또는 '합성' 한 단어로만 답하세요."
HUMAN_TOKENS = ("사람", "human", "real", "녹음")
SYNTHETIC_TOKENS = ("합성", "synth", "machine", "tts", "기계", "인공")


class BaselineError(RuntimeError):
    """Raised when the baseline cannot be measured."""


def classify_reply(reply: str) -> str | None:
    """Map a free-text reply onto a class, or ``None`` when it says neither.

    An unparsable reply is recorded as such rather than folded into one class:
    a model that answers "잘 모르겠습니다" has not voted, and counting that as a
    wrong answer would flatter whichever class is rarer.
    """

    lowered = reply.strip().lower()
    human = any(token in lowered for token in HUMAN_TOKENS)
    synthetic = any(token in lowered for token in SYNTHETIC_TOKENS)
    if human == synthetic:
        return None
    return "human" if human else "synthetic"


def balanced_accuracy(counts: dict) -> float | None:
    """Mean of the two per-class accuracies.

    Reported instead of overall accuracy because a constant answer scores 50% here
    however the sample is balanced, which is what makes the number readable.
    """

    per_class = []
    for label in ("human", "synthetic"):
        total = counts[label]["correct"] + counts[label]["wrong"]
        if total == 0:
            return None
        per_class.append(counts[label]["correct"] / total)
    return sum(per_class) / len(per_class)


def _extract(archives: list[zipfile.ZipFile], file_name: str, target: Path) -> bool:
    for archive in archives:
        for info in archive.infolist():
            if PurePosixPath(info.filename).name == file_name:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as src, target.open("wb") as out:
                    shutil.copyfileobj(src, out)
                return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pair_manifest", type=Path)
    parser.add_argument("--human-audio", type=Path, action="append", required=True)
    parser.add_argument("--tts-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--scratch", type=Path, required=True)
    args = parser.parse_args()

    import numpy as np
    import soundfile as sf
    import torch
    from scipy.signal import resample_poly
    from transformers import AutoProcessor, Qwen2AudioForConditionalGeneration

    manifest = json.loads(args.pair_manifest.read_text(encoding="utf-8"))
    tts_files = sorted(args.tts_dir.glob("*.wav"))
    if not tts_files:
        raise BaselineError("no synthesised audio to measure")

    processor = AutoProcessor.from_pretrained(args.model)
    model = Qwen2AudioForConditionalGeneration.from_pretrained(
        args.model, dtype=torch.bfloat16
    ).eval()
    rate = processor.feature_extractor.sampling_rate

    def ask(path: Path) -> str:
        audio, sample_rate = sf.read(str(path))
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sample_rate != rate:
            audio = resample_poly(audio, rate, sample_rate)
        conversation = [
            {
                "role": "user",
                "content": [
                    {"type": "audio", "audio_url": "x"},
                    {"type": "text", "text": QUESTION},
                ],
            }
        ]
        text = processor.apply_chat_template(
            conversation, add_generation_prompt=True, tokenize=False
        )
        inputs = processor(
            text=text,
            audio=[audio.astype("float32")],
            sampling_rate=rate,
            return_tensors="pt",
            padding=True,
        )
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=12, do_sample=False)
        generated = out[:, inputs["input_ids"].shape[1] :]
        return processor.batch_decode(generated, skip_special_tokens=True)[0].strip()

    archives = [zipfile.ZipFile(path) for path in args.human_audio]
    half = max(1, args.samples // 2)
    counts = {
        "human": {"correct": 0, "wrong": 0, "unparsed": 0},
        "synthetic": {"correct": 0, "wrong": 0, "unparsed": 0},
    }
    rows: list[dict] = []

    for index, row in enumerate(manifest["pair_rows"][:half]):
        target = args.scratch / f"human_{index}.wav"
        if not _extract(archives, row["human_file_name"], target):
            continue
        reply = ask(target)
        verdict = classify_reply(reply)
        bucket = "unparsed" if verdict is None else (
            "correct" if verdict == "human" else "wrong"
        )
        counts["human"][bucket] += 1
        rows.append({"class": "human", "reply": reply, "verdict": verdict})
        print(f"[human {index}] {reply!r} -> {verdict}", flush=True)

    for index, path in enumerate(tts_files[:half]):
        reply = ask(path)
        verdict = classify_reply(reply)
        bucket = "unparsed" if verdict is None else (
            "correct" if verdict == "synthetic" else "wrong"
        )
        counts["synthetic"][bucket] += 1
        rows.append({"class": "synthetic", "reply": reply, "verdict": verdict})
        print(f"[synthetic {index}] {reply!r} -> {verdict}", flush=True)

    report = {
        "schema_version": "voice_coach.evaluator_zero_shot_baseline.v1",
        "model_id": args.model,
        "fine_tuned": False,
        "question_ko": QUESTION,
        "counts": counts,
        "balanced_accuracy": balanced_accuracy(counts),
        "note_ko": (
            "균형 정확도로 보고한다. 한쪽으로만 답하는 모델은 전체 정확도가 표본"
            " 구성에 따라 부풀지만 균형 정확도는 0.5에 머문다."
        ),
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print()
    for label in ("human", "synthetic"):
        c = counts[label]
        print(f"{label:10s} correct {c['correct']:3d}  wrong {c['wrong']:3d}  "
              f"unparsed {c['unparsed']:3d}")
    accuracy = report["balanced_accuracy"]
    print(f"balanced accuracy: {accuracy if accuracy is None else round(accuracy, 4)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
