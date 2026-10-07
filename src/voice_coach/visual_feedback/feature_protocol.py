"""Closed, bounded feature-worker protocol shared by the isolated process and port."""
from __future__ import annotations

import math

from .lip_geometry import LipMapping

PROTOCOL = "voice-coaching.lip-features.v1"
MAX_FRAMES = 3600
MAX_DURATION_SECONDS = 120
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_HEADER_BYTES = 256 * 1024
# Ordered outer/inner contours from the existing MediaPipe 468-point topology.
# Eye corners set the scale; the current mouth width never sets it.
MAPPING = LipMapping("mediapipe-468-eye-scale-lips-v1",
    (61, 40, 37, 0, 267, 270, 291, 321, 314, 17, 84, 91,
     78, 81, 13, 311, 308, 402, 14, 178),
    33, 263, 61, 291, 13, 14,
    (78, 191, 80, 81, 82, 13, 312, 311, 310, 415, 308, 324, 318, 402, 317, 14, 87, 178, 88, 95))


def require(condition: bool):
    if not condition:
        raise ValueError("VISUAL_WORKER_PROTOCOL_INVALID")


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_header(value):
    require(type(value) is dict and set(value) == {
        "protocol", "width", "height", "pts", "modelPath", "modelSha256", "quality"})
    require(value["protocol"] == PROTOCOL)
    require(all(type(value[k]) is int and 16 <= value[k] <= 1920 for k in ("width", "height")))
    require(value["width"] * value["height"] <= 1920 * 1080)
    pts = value["pts"]
    require(type(pts) is list and 1 <= len(pts) <= MAX_FRAMES)
    require(all(finite(p) and abs(p) <= 86400 for p in pts))
    # VIDEO mode accepts only strictly increasing integer millisecond timestamps.
    require(all(round((b - pts[0]) * 1000) > round((a - pts[0]) * 1000) for a, b in zip(pts, pts[1:])))
    require(pts[-1] - pts[0] <= MAX_DURATION_SECONDS)
    q = value["quality"]
    require(type(q) is dict and set(q) == {"minimumFaceScalePixels", "maximumYawDegrees",
        "maximumPitchDegrees", "detectionConfidence", "presenceConfidence", "trackingConfidence"})
    require(all(finite(v) for v in q.values()))
    require(1 <= q["minimumFaceScalePixels"] <= 1000)
    require(all(0 < q[k] <= 45 for k in ("maximumYawDegrees", "maximumPitchDegrees")))
    require(all(0 < q[k] <= 1 for k in ("detectionConfidence", "presenceConfidence", "trackingConfidence")))
    return value


def strict_json(raw):
    import json
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result
    def invalid(_):
        raise ValueError("VISUAL_NONFINITE_JSON")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def validate_features(value, pts):
    require(type(value) is dict and set(value) == {"protocol", "mappingRevision", "frames"})
    require(value["protocol"] == PROTOCOL and value["mappingRevision"] == MAPPING.revision)
    frames = value["frames"]
    require(type(frames) is list and len(frames) == len(pts) <= MAX_FRAMES)
    require(all(finite(t) and abs(t) <= 86400 for t in pts)
            and all(a < b for a, b in zip(pts, pts[1:])))
    require(not pts or pts[-1] - pts[0] <= MAX_DURATION_SECONDS)
    for row, timestamp in zip(frames, pts):
        require(type(row) is dict and set(row) == {"pts", "reason", "landmarks", "features"})
        require(row["pts"] == timestamp and row["reason"] in {
            None, "NO_FACE", "MULTIPLE_FACES", "LOW_VISIBILITY", "HEAD_POSE_UNSUPPORTED"})
        if row["reason"] is not None:
            require(row["landmarks"] == [] and row["features"] == [])
        else:
            require(type(row["landmarks"]) is list and len(row["landmarks"]) == len(MAPPING.lip_indices))
            require(all(type(p) is list and len(p) == 2 and all(finite(v) and abs(v) <= 100 for v in p)
                        for p in row["landmarks"]))
            require(type(row["features"]) is list and len(row["features"]) == 3
                    and all(finite(v) and 0 <= v <= 100 for v in row["features"]))
    return value
