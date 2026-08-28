#!/usr/bin/env python3
"""m7 naturalness (UTMOSv2) from the pinned MOS venv.

UTMOSv2 pulls in torch, timm and transformers, and the scorer environment is pinned
by the S104/S105 execution locks. Installing it there would change the environment
those runs recorded, so this worker is executed by `.venv-paper-mos` and speaks one
JSON request/response over stdin/stdout — the same boundary the acoustic worker uses.

Unlike m4/m5 this worker *does* return a score, and the reason is worth stating: a
predicted MOS is already an opinion score on a defined 1–5 scale, so mapping it onto
``[0, 1]`` is an affine map of a bounded scale, not an invented comparison. Hertz has
no such endpoints, which is why F0 stays a measurement until a reference exists.

The prediction is run on CPU by default. UTMOSv2 needs no GPU, and this machine's GPU
is held by training.

No path, digest, or speaker value is echoed back.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import sys

PROTOCOL_VERSION = "voice_coach.naturalness_mos_worker.v1"
_REQUIRED_FIELDS = ("protocol_version", "audio_path", "folds")
#: UTMOSv2 ships five fold checkpoints trained with seed 42. Which folds ran is part
#: of the result, because a single fold and the five-fold mean are different numbers.
SUPPORTED_FOLDS = (0, 1, 2, 3, 4)
CHECKPOINT_SEED = 42
CONFIG_NAME = "fusion_stage3"
#: MOS is defined on 1–5. These are the scale's own endpoints, not a chosen curve.
MOS_MINIMUM = 1.0
MOS_MAXIMUM = 5.0


def _fail(detail: str) -> int:
    json.dump(
        {"status": "failed_closed", "error": detail},
        sys.stdout,
        ensure_ascii=False,
        sort_keys=True,
    )
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 1


def _predict(audio_path: str, folds: list[int], device: str) -> dict:
    import utmosv2

    per_fold: dict[str, float] = {}
    for fold in folds:
        # UTMOSv2 prints checkpoint and progress lines to stdout, which is this
        # worker's response channel. They go to stderr so the reply stays parseable.
        with contextlib.redirect_stdout(sys.stderr):
            model = utmosv2.create_model(
                pretrained=True,
                config=CONFIG_NAME,
                fold=fold,
                seed=CHECKPOINT_SEED,
                device=device,
            )
            value = float(model.predict(input_path=audio_path, device=device))
        if not MOS_MINIMUM <= value <= MOS_MAXIMUM:
            # The model is not contractually bounded even though the scale is, so an
            # out-of-scale prediction is reported rather than silently clipped.
            return {
                "state": "missing",
                "reason": "prediction_outside_mos_scale",
                "unit": "mos_1_to_5",
            }
        per_fold[str(fold)] = value
    mean = sum(per_fold.values()) / len(per_fold)
    return {
        "state": "observed",
        "unit": "mos_1_to_5",
        "mos_mean": mean,
        "mos_by_fold": per_fold,
        "folds": sorted(int(key) for key in per_fold),
        "config_name": CONFIG_NAME,
        "checkpoint_seed": CHECKPOINT_SEED,
        "device": device,
    }


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _fail("request is not valid JSON")
    if not isinstance(request, dict):
        return _fail("request must be an object")
    missing = [name for name in _REQUIRED_FIELDS if name not in request]
    if missing:
        return _fail("request is missing fields: " + ", ".join(missing))
    if request["protocol_version"] != PROTOCOL_VERSION:
        return _fail("worker protocol version differs")

    audio_path = request["audio_path"]
    if not isinstance(audio_path, str) or not Path(audio_path).is_file():
        return _fail("audio file is unavailable")
    folds = request["folds"]
    if not isinstance(folds, list) or not folds:
        return _fail("folds must be a non-empty array")
    unknown = [item for item in folds if item not in SUPPORTED_FOLDS]
    if unknown:
        return _fail("unsupported fold requested")
    device = request.get("device", "cpu")
    if device not in ("cpu", "cuda"):
        return _fail("device must be cpu or cuda")

    # Keep the run reproducible and off the GPU that training holds.
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "" if device == "cpu" else "0")
    try:
        measurement = _predict(audio_path, sorted(set(folds)), device)
    except ImportError:
        return _fail("the pinned MOS runtime is not installed")
    except FileNotFoundError:
        return _fail("the UTMOSv2 checkpoint is unavailable")
    except (OSError, RuntimeError, ValueError):
        return _fail("the pinned MOS runtime failed on this recording")

    json.dump(
        {
            "status": "ok",
            "protocol_version": PROTOCOL_VERSION,
            "measurements": {"naturalness_utmosv2": measurement},
        },
        sys.stdout,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
    )
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
