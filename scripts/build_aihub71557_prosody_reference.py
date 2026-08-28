from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import parselmouth


HANGUL = re.compile(r"[가-힣]")

METRICS = [
    "speech_rate",
    "articulation_rate",
    "pause_count_per_10_syllables",
    "pause_duration_per_10_syllables",
    "f0_median_hz",
    "f0_range_st",
    "f0_coverage",
    "intensity_range_db",
]


def frame_audio(audio, sr):
    frame_size = max(1, round(sr * 0.025))
    hop_size = max(1, round(sr * 0.010))

    if len(audio) < frame_size:
        audio = np.pad(audio, (0, frame_size - len(audio)))

    count = 1 + (len(audio) - frame_size) // hop_size

    shape = (count, frame_size)
    strides = (audio.strides[0] * hop_size, audio.strides[0])

    return np.lib.stride_tricks.as_strided(
        audio,
        shape=shape,
        strides=strides,
    ).copy()


def internal_pause_stats(mask):
    padded = np.pad(mask.astype(np.int8), (1, 1))
    changes = np.diff(padded)

    starts = np.flatnonzero(changes == 1)
    ends = np.flatnonzero(changes == -1)

    count = 0
    frames = 0

    for start, end in zip(starts, ends):
        length = end - start

        # 최소 100 ms
        if length < 10:
            continue

        # 앞/뒤 무음은 internal pause에서 제외
        if start == 0 or end == len(mask):
            continue

        count += 1
        frames += length

    return count, frames * 0.010


def analyze(row):
    try:
        wav_path = row["wav_path"]
        start = float(row["utterance_start"])
        end = float(row["utterance_end"])
        text = row["text"]

        syllables = len(HANGUL.findall(text))
        if syllables < 5:
            raise ValueError("too_few_syllables")

        snd = parselmouth.Sound(wav_path)

        start = max(0.0, start)
        end = min(float(snd.duration), end)

        if end <= start:
            raise ValueError("invalid_interval")

        snd = snd.extract_part(
            from_time=start,
            to_time=end,
            preserve_times=False,
        )

        duration = float(snd.duration)

        # ---------- pause ----------
        audio = np.asarray(snd.values, dtype=np.float64)

        if audio.ndim == 2:
            audio = np.mean(audio, axis=0)

        sr = int(round(snd.sampling_frequency))

        frames = frame_audio(audio, sr)
        frame_rms = np.sqrt(np.mean(np.square(frames), axis=1))

        peak_rms = float(np.max(frame_rms))

        silence_threshold = (
            peak_rms * 10 ** (-40.0 / 20.0)
        )

        silence = frame_rms <= max(
            silence_threshold,
            np.finfo(float).eps,
        )

        pause_count, pause_duration = internal_pause_stats(silence)

        speech_time = duration - pause_duration

        speech_rate = syllables / duration

        articulation_rate = (
            syllables / speech_time
            if speech_time > 0
            else None
        )

        # ---------- F0 ----------
        pitch = snd.to_pitch_ac(
            time_step=0.01,
            pitch_floor=60.0,
            pitch_ceiling=600.0,
        )

        f0_all = np.asarray(
            pitch.selected_array["frequency"],
            dtype=float,
        )

        voiced = f0_all[
            np.isfinite(f0_all) & (f0_all > 0)
        ]

        f0_median = None
        f0_range_st = None

        if len(voiced) >= 5:
            p10, p50, p90 = np.percentile(
                voiced,
                [10, 50, 90],
            )

            f0_median = float(p50)

            if p10 > 0:
                f0_range_st = float(
                    12.0 * math.log2(p90 / p10)
                )

        f0_coverage = (
            float(len(voiced) / len(f0_all))
            if len(f0_all)
            else None
        )

        # ---------- intensity ----------
        # silence가 아닌 speech-active frame만 사용
        active_rms = frame_rms[
            (~silence) & np.isfinite(frame_rms) & (frame_rms > 0)
        ]

        intensity_range = None

        if len(active_rms) >= 5:
            active_db = 20.0 * np.log10(active_rms)

            ip10, ip90 = np.percentile(
                active_db,
                [10, 90],
            )

            intensity_range = float(ip90 - ip10)

        return {
            "speaker_id": row["speaker_id"],
            "job": row["job"],
            "sex": row["sex"],
            "speech_rate": speech_rate,
            "articulation_rate": articulation_rate,
            "pause_count_per_10_syllables":
                pause_count * 10.0 / syllables,
            "pause_duration_per_10_syllables":
                pause_duration * 10.0 / syllables,
            "f0_median_hz": f0_median,
            "f0_range_st": f0_range_st,
            "f0_coverage": f0_coverage,
            "intensity_range_db": intensity_range,
            "status": "ok",
        }

    except Exception as e:
        return {
            "speaker_id": row.get("speaker_id", ""),
            "job": row.get("job", ""),
            "sex": row.get("sex", ""),
            **{m: None for m in METRICS},
            "status": f"error:{type(e).__name__}:{e}",
        }


def stats(values):
    a = np.asarray(values, dtype=float)

    return {
        "n": int(len(a)),
        "mean": float(np.mean(a)),
        "std": float(np.std(a, ddof=1)) if len(a) > 1 else 0.0,
        "p10": float(np.percentile(a, 10)),
        "p25": float(np.percentile(a, 25)),
        "p50": float(np.percentile(a, 50)),
        "p75": float(np.percentile(a, 75)),
        "p90": float(np.percentile(a, 90)),
    }


def build_summary(rows):
    speaker_values = defaultdict(
        lambda: defaultdict(list)
    )
    speaker_job = {}

    for row in rows:
        if row["status"] != "ok":
            continue

        spk = row["speaker_id"]
        speaker_job[spk] = row["job"]

        for metric in METRICS:
            value = row[metric]
            if value is not None:
                speaker_values[spk][metric].append(float(value))

    speaker_medians = defaultdict(
        lambda: defaultdict(list)
    )

    for spk, values in speaker_values.items():
        job = speaker_job[spk]

        for metric in METRICS:
            vals = values.get(metric, [])
            if vals:
                speaker_medians[job][metric].append(
                    float(np.median(vals))
                )
                speaker_medians["전체"][metric].append(
                    float(np.median(vals))
                )

    result = {
        "aggregation":
            "utterance metrics -> per-speaker median -> speaker-balanced distribution",
        "groups": {},
    }

    jobs = [
        "현직아나운서",
        "전직아나운서",
        "아나운서준비생",
        "전체",
    ]

    for job in jobs:
        result["groups"][job] = {}

        for metric in METRICS:
            vals = speaker_medians[job].get(metric, [])
            if vals:
                result["groups"][job][metric] = stats(vals)

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out-csv", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0)

    args = parser.parse_args()

    with open(args.manifest, encoding="utf-8") as f:
        input_rows = list(csv.DictReader(f))

    if args.limit:
        input_rows = input_rows[:args.limit]

    output_rows = []

    with ProcessPoolExecutor(
        max_workers=args.workers
    ) as executor:

        for i, row in enumerate(
            executor.map(
                analyze,
                input_rows,
                chunksize=32,
            ),
            1,
        ):
            output_rows.append(row)

            if i % 1000 == 0:
                print(
                    f"{i:,}/{len(input_rows):,}",
                    flush=True,
                )

    fields = [
        "speaker_id",
        "job",
        "sex",
        *METRICS,
        "status",
    ]

    out_csv = Path(args.out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    with out_csv.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fields,
        )
        writer.writeheader()
        writer.writerows(output_rows)

    summary = build_summary(output_rows)

    summary["total"] = len(output_rows)
    summary["success"] = sum(
        r["status"] == "ok"
        for r in output_rows
    )
    summary["failed"] = sum(
        r["status"] != "ok"
        for r in output_rows
    )

    Path(args.out_json).write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        "DONE",
        "total=", summary["total"],
        "success=", summary["success"],
        "failed=", summary["failed"],
    )


if __name__ == "__main__":
    main()