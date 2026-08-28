#!/usr/bin/env python3
"""Explicit acoustic measurements (m4 F0, m5 energy) from the pinned acoustics venv.

openSMILE and Parselmouth live in `.venv-paper-acoustics`, not in the scorer
environment that the S104/S105 execution locks pin. Installing them there would
change the environment those runs recorded, so this worker is executed by the other
interpreter and speaks one JSON request/response over stdin/stdout — the same
boundary the expected-phone worker uses.

The reply carries **measurements with units**, not scores. Turning an F0 median in
hertz into a "prosody score" requires something to compare against, and in this
architecture that comparison is ``d(x, R)`` against a reference bank. A worker that
invented a 0–1 score here would be inventing the reference.

No path, digest, or speaker value is echoed back.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

PROTOCOL_VERSION = "voice_coach.acoustic_features_worker.v1"
_REQUIRED_FIELDS = ("protocol_version", "audio_path", "features")
SUPPORTED_FEATURES = ("f0_parselmouth", "egemaps_functionals")
#: eGeMAPS functionals kept for the energy branch. The full set is 88 columns; the
#: worker returns only what a declared branch consumes, so an unused column cannot
#: quietly become a model input.
EGEMAPS_KEEP = (
    "loudness_sma3_amean",
    "loudness_sma3_stddevNorm",
    "loudness_sma3_percentile20.0",
    "loudness_sma3_percentile50.0",
    "loudness_sma3_percentile80.0",
    "equivalentSoundLevel_dBp",
)


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


def _f0_parselmouth(audio_path: str) -> dict:
    import numpy as np
    import parselmouth

    sound = parselmouth.Sound(audio_path)
    pitch = sound.to_pitch()
    values = pitch.selected_array["frequency"]
    voiced = values[values > 0]
    if voiced.size == 0:
        # Unvoiced or silent: no F0 exists to report, and 0 Hz is not an F0.
        return {
            "state": "missing",
            "reason": "no_voiced_frame",
            "unit": "hertz",
        }
    log_values = np.log2(voiced)
    return {
        "state": "observed",
        "unit": "hertz",
        "median_hz": float(np.median(voiced)),
        "mean_hz": float(np.mean(voiced)),
        "min_hz": float(np.min(voiced)),
        "max_hz": float(np.max(voiced)),
        # Range in semitones is the comparable quantity across speakers; hertz
        # range would make a low-pitched speaker look monotone by construction.
        "range_semitones": float(12.0 * (log_values.max() - log_values.min())),
        "voiced_frame_count": int(voiced.size),
        "total_frame_count": int(values.size),
        "voiced_fraction": float(voiced.size / values.size),
    }


def _egemaps(audio_path: str) -> dict:
    import opensmile

    smile = opensmile.Smile(
        feature_set=opensmile.FeatureSet.eGeMAPSv02,
        feature_level=opensmile.FeatureLevel.Functionals,
    )
    frame = smile.process_file(audio_path)
    if frame.empty:
        return {"state": "missing", "reason": "empty_feature_frame"}
    row = frame.iloc[0]
    values: dict[str, float] = {}
    missing: list[str] = []
    for name in EGEMAPS_KEEP:
        if name not in frame.columns:
            missing.append(name)
            continue
        values[name] = float(row[name])
    if not values:
        return {"state": "missing", "reason": "no_requested_functional_present"}
    return {
        "state": "observed",
        "feature_set": "eGeMAPSv02",
        "feature_level": "Functionals",
        "total_columns": int(frame.shape[1]),
        "values": values,
        "absent_requested": missing,
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
    features = request["features"]
    if not isinstance(features, list) or not features:
        return _fail("features must be a non-empty array")
    unknown = [name for name in features if name not in SUPPORTED_FEATURES]
    if unknown:
        return _fail("unsupported feature: " + ", ".join(sorted(unknown)))

    results: dict[str, dict] = {}
    try:
        if "f0_parselmouth" in features:
            results["f0_parselmouth"] = _f0_parselmouth(audio_path)
        if "egemaps_functionals" in features:
            results["egemaps_functionals"] = _egemaps(audio_path)
    except ImportError:
        return _fail("the pinned acoustic runtime is not installed")
    except (OSError, RuntimeError, ValueError):
        return _fail("the pinned acoustic runtime failed on this recording")

    json.dump(
        {
            "status": "ok",
            "protocol_version": PROTOCOL_VERSION,
            "measurements": results,
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
