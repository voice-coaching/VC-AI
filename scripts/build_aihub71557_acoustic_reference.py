from __future__ import annotations

import argparse
import csv
import math
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import parselmouth


FIELDS = [
    "speaker_id", "job", "sex", "wav_path",
    "f0_median_hz",
    "f0_p10_hz",
    "f0_p90_hz",
    "f0_p90_p10_st",
    "f0_coverage",
    "intensity_p10_db",
    "intensity_p90_db",
    "intensity_p90_p10_db",
    "status",
]


def analyze(row):
    spk, job, sex, _, wav_path, start, end, _ = row

    out = {
        "speaker_id": spk,
        "job": job,
        "sex": sex,
        "wav_path": wav_path,
        "f0_median_hz": "",
        "f0_p10_hz": "",
        "f0_p90_hz": "",
        "f0_p90_p10_st": "",
        "f0_coverage": "",
        "intensity_p10_db": "",
        "intensity_p90_db": "",
        "intensity_p90_p10_db": "",
        "status": "ok",
    }

    try:
        start = float(start)
        end = float(end)

        snd = parselmouth.Sound(wav_path)

        # JSON의 실제 발화 구간만 사용
        start = max(0.0, start)
        end = min(float(snd.duration), end)

        if end <= start:
            raise ValueError("invalid utterance interval")

        snd = snd.extract_part(
            from_time=start,
            to_time=end,
            preserve_times=False,
        )

        # F0
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

        if len(f0_all) > 0:
            out["f0_coverage"] = float(len(voiced) / len(f0_all))

        if len(voiced) >= 5:
            p10 = float(np.percentile(voiced, 10))
            p50 = float(np.percentile(voiced, 50))
            p90 = float(np.percentile(voiced, 90))

            out["f0_p10_hz"] = p10
            out["f0_median_hz"] = p50
            out["f0_p90_hz"] = p90

            if p10 > 0 and p90 > 0:
                out["f0_p90_p10_st"] = (
                    12.0 * math.log2(p90 / p10)
                )

        # Intensity
        intensity = snd.to_intensity(
            minimum_pitch=60.0,
            time_step=0.01,
        )

        vals = np.asarray(
            intensity.values,
            dtype=float,
        ).reshape(-1)

        vals = vals[np.isfinite(vals)]

        if len(vals) >= 5:
            ip10 = float(np.percentile(vals, 10))
            ip90 = float(np.percentile(vals, 90))

            out["intensity_p10_db"] = ip10
            out["intensity_p90_db"] = ip90
            out["intensity_p90_p10_db"] = ip90 - ip10

    except Exception as e:
        out["status"] = f"error:{type(e).__name__}:{e}"

    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    rows = []

    with open(args.manifest, encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)

        for row in r:
            rows.append(row)
            if args.limit and len(rows) >= args.limit:
                break

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    ok = 0
    fail = 0

    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()

        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            for i, result in enumerate(
                ex.map(analyze, rows, chunksize=32),
                1,
            ):
                w.writerow(result)

                if result["status"] == "ok":
                    ok += 1
                else:
                    fail += 1

                if i % 1000 == 0:
                    print(
                        f"{i:,}/{len(rows):,} "
                        f"ok={ok:,} fail={fail:,}",
                        flush=True,
                    )

    print(f"DONE total={len(rows):,} ok={ok:,} fail={fail:,}")
    print(f"WRITTEN {out}")


if __name__ == "__main__":
    main()
