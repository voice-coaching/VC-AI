"""Isolated streaming RGB24 -> visible lip geometry. No network, scores or GPT.

The parent starts this file through network_isolated_exec, which denies network
and child processes. It sends a four-byte header length, JSON, then exact RGB
frames. Model contents are pinned before MediaPipe is imported.
"""
from __future__ import annotations

import json
import math
from contextlib import contextmanager
import fcntl
import os
import ctypes
from pathlib import Path
import struct
import sys
from types import ModuleType


def _bootstrap():
    root = Path(__file__).resolve().parents[2]
    # Do not import the app's legacy orchestration or its optional dependencies.
    for name, directory in (("voice_coach", root),
        ("voice_coach.visual_feedback", root / "visual_feedback"),
        ("voice_coach.pronunciation_attempts", root / "pronunciation_attempts"),
        ("voice_coach.pronunciation_attempts.adapters", root / "pronunciation_attempts/adapters")):
        package = ModuleType(name)
        package.__path__ = [str(directory)]
        sys.modules[name] = package


def exact(size):
    raw = bytearray()
    while len(raw) < size:
        chunk = sys.stdin.buffer.read(size - len(raw))
        if not chunk:
            raise ValueError("VISUAL_RGB_TRUNCATED")
        raw.extend(chunk)
    return bytes(raw)


@contextmanager
def sealed_model_path(model):
    """Keep verified bytes immutable and alive through the native file loader."""
    # The deployed CPython build omits these Linux wrappers. Use the system
    # libc entry point and stable Linux UAPI flags, with an exact ctypes ABI.
    libc = ctypes.CDLL(None, use_errno=True)
    create = libc.memfd_create
    create.argtypes = (ctypes.c_char_p, ctypes.c_uint)
    create.restype = ctypes.c_int
    fd = create(b'verified-landmark-model', 0x0001 | 0x0002)
    if fd < 0: raise OSError(ctypes.get_errno(), 'VISUAL_MODEL_SNAPSHOT_FAILED')
    try:
        remaining = memoryview(model)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0: raise ValueError('VISUAL_MODEL_SNAPSHOT_FAILED')
            remaining = remaining[written:]
        os.lseek(fd, 0, os.SEEK_SET)
        # F_ADD_SEALS; WRITE | GROW | SHRINK | SEAL (linux/fcntl.h).
        fcntl.fcntl(fd, 1033, 0x0008 | 0x0004 | 0x0002 | 0x0001)
        yield f'/proc/self/fd/{fd}'
    finally:
        os.close(fd)


@contextmanager
def landmarker(mp, model, quality):
    # The 1.0.1 buffer loader intermittently corrupts its heap during native
    # initialization on J406. Its file loader uses the same pinned task bytes.
    # A sealed anonymous file avoids a path race and survives until native close.
    with sealed_model_path(model) as path:
        options = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=path),
            running_mode=mp.tasks.vision.RunningMode.VIDEO, num_faces=2,
            min_face_detection_confidence=quality['detectionConfidence'],
            min_face_presence_confidence=quality['presenceConfidence'],
            min_tracking_confidence=quality['trackingConfidence'],
            output_face_blendshapes=False, output_facial_transformation_matrixes=True)
        with mp.tasks.vision.FaceLandmarker.create_from_options(options) as detector:
            yield detector


def main():
    _bootstrap()
    from voice_coach.visual_feedback.feature_protocol import (
        MAPPING, PROTOCOL, MAX_HEADER_BYTES, validate_header, require)
    from voice_coach.visual_feedback.lip_geometry import normalize_lips
    from voice_coach.visual_feedback.reference_distance import UnscorableTrajectory
    from voice_coach.pronunciation_attempts.adapters.mediapipe_landmark_worker import (
        _read_model_bytes, _deny_network_syscalls)
    _deny_network_syscalls()  # Also fail closed if launched without the outer filter.
    size = struct.unpack("!I", exact(4))[0]
    require(0 < size <= MAX_HEADER_BYTES)
    header = validate_header(json.loads(exact(size)))
    model = _read_model_bytes(header["modelPath"], header["modelSha256"])
    import mediapipe as mp
    import numpy as np
    q = header["quality"]
    width, height = header["width"], header["height"]
    frames = []
    with landmarker(mp, model, q) as detector:
        for pts in header["pts"]:
            pixels = np.frombuffer(exact(width * height * 3), dtype=np.uint8).reshape(height, width, 3)
            result = detector.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=pixels),
                                               round((pts - header["pts"][0]) * 1000))
            row = dict(pts=pts, reason=None, landmarks=[], features=[])
            if len(result.face_landmarks) != 1:
                row["reason"] = "NO_FACE" if not result.face_landmarks else "MULTIPLE_FACES"
            else:
                matrices = result.facial_transformation_matrixes
                if len(matrices) != 1 or np.shape(matrices[0]) != (4, 4) or not np.isfinite(matrices[0]).all():
                    row["reason"] = "HEAD_POSE_UNSUPPORTED"
                else:
                    r = matrices[0]
                    yaw = math.degrees(math.atan2(-r[2, 0], math.hypot(r[0, 0], r[1, 0])))
                    pitch = math.degrees(math.atan2(r[2, 1], r[2, 2]))
                    if abs(yaw) > q["maximumYawDegrees"] or abs(pitch) > q["maximumPitchDegrees"]:
                        row["reason"] = "HEAD_POSE_UNSUPPORTED"
                    else:
                        try:
                            points, features = normalize_lips(tuple((p.x, p.y) for p in result.face_landmarks[0]),
                                width=width, height=height, mapping=MAPPING,
                                minimum_face_scale_px=q["minimumFaceScalePixels"])
                            row.update(landmarks=points, features=features)
                        except UnscorableTrajectory:
                            row["reason"] = "LOW_VISIBILITY"
            frames.append(row)
    require(sys.stdin.buffer.read(1) == b"")
    sys.stdout.write(json.dumps(dict(protocol=PROTOCOL, mappingRevision=MAPPING.revision, frames=frames),
                               ensure_ascii=False, allow_nan=False, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Errors contain no filenames, transcript, model output or runtime exceptions.
        sys.stderr.write("VISUAL_FEATURE_WORKER_FAILED\n")
        raise SystemExit(1)
