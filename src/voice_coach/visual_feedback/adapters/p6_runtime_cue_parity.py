"""Run the production MediaPipe worker independently on every P6B frame."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys

from ...pronunciation_attempts.adapters.mediapipe_landmark_worker import (
    BACKEND_REVISION,
)
from ...pronunciation_attempts.adapters.mediapipe_lip_landmarks import (
    MediaPipeLandmarkPolicy,
    MediaPipeLipLandmarkAdapter,
)
from ...pronunciation_attempts.models import DecodedRgbFrame, Rgb24Sample
from ..adapters.aihub68_geometry_dataset import Aihub68GeometrySample
from ..services.p6_phone_reference import (
    P6ProductionCueBatch,
    P6ProductionCueObservation,
)


class P6RuntimeCueParityError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class P6RuntimeCueParityAdapter:
    """Compare against the actual production VIDEO-mode worker, state-free.

    Each selected JPEG is decoded and resized exactly as the calibration worker,
    then sent as the only frame in a fresh production subprocess.  This prevents
    tracking state from one restricted actor/camera image crossing into another.
    """

    def __init__(
        self,
        *,
        model_path: str | Path,
        expected_model_sha256: str,
        repository_root: str | Path,
        worker_python: str | Path | None = None,
        maximum_image_dimension: int = 640,
    ) -> None:
        self._repository_root = Path(repository_root)
        self._maximum_image_dimension = maximum_image_dimension
        self._adapter = MediaPipeLipLandmarkAdapter(
            model_path=model_path,
            expected_model_sha256=expected_model_sha256,
            policy=MediaPipeLandmarkPolicy(
                minimum_face_detection_confidence=0.5,
                minimum_face_presence_confidence=0.5,
                minimum_tracking_confidence=0.5,
                maximum_frame_count=1,
                maximum_total_rgb_bytes=4 * 1024 * 1024,
                subprocess_timeout_seconds=45.0,
                maximum_subprocess_output_bytes=256 * 1024,
            ),
            worker_python=worker_python or sys.executable,
            repository_root=self._repository_root,
        )
        self._model_sha256 = expected_model_sha256

    def infer(
        self, samples: tuple[Aihub68GeometrySample, ...]
    ) -> P6ProductionCueBatch:
        if not samples:
            raise P6RuntimeCueParityError("p6b_production_samples_missing")
        observations: list[P6ProductionCueObservation] = []
        for sample in samples:
            rgb, width, height = self._decode_rgb(sample)
            batch = self._adapter.detect(
                Rgb24Sample(
                    asset_handle_id=sample.sample_sha256,
                    container_sha256=sample.sample_sha256,
                    video_stream_index=0,
                    frames=(
                        DecodedRgbFrame(
                            timestamp_us=0,
                            width=width,
                            height=height,
                            rgb24=rgb,
                        ),
                    ),
                    ffmpeg_revision="p6b_opencv_jpeg_decode_v1",
                )
            )
            frame = batch.frames[0]
            observations.append(
                P6ProductionCueObservation(
                    sample_sha256=sample.sample_sha256,
                    face_count=frame.face_count,
                    aperture_ratio=(
                        None
                        if frame.lip_geometry is None
                        else frame.lip_geometry.aperture_ratio
                    ),
                )
            )
        worker_source = self._repository_root / (
            "src/voice_coach/pronunciation_attempts/adapters/"
            "mediapipe_landmark_worker.py"
        )
        adapter_source = self._repository_root / (
            "src/voice_coach/pronunciation_attempts/adapters/"
            "mediapipe_lip_landmarks.py"
        )
        return P6ProductionCueBatch(
            observations=tuple(observations),
            backend_revision=BACKEND_REVISION,
            worker_sha256=_sha256_file(worker_source),
            adapter_sha256=_sha256_file(adapter_source),
            model_sha256=self._model_sha256,
        )

    def _decode_rgb(
        self, sample: Aihub68GeometrySample
    ) -> tuple[bytes, int, int]:
        try:
            import cv2
            import numpy as np
        except ImportError:
            raise P6RuntimeCueParityError("p6b_jpeg_backend_unavailable") from None
        jpeg = np.frombuffer(sample.jpeg_bytes, dtype=np.uint8)
        bgr = cv2.imdecode(jpeg, cv2.IMREAD_COLOR)
        if bgr is None or bgr.ndim != 3 or bgr.shape[2] != 3:
            raise P6RuntimeCueParityError("p6b_jpeg_decode_failed")
        height, width = bgr.shape[:2]
        if width != sample.width or height != sample.height:
            raise P6RuntimeCueParityError("p6b_jpeg_dimension_mismatch")
        scale = min(1.0, self._maximum_image_dimension / max(width, height))
        if scale < 1.0:
            bgr = cv2.resize(
                bgr,
                (round(width * scale), round(height * scale)),
                interpolation=cv2.INTER_AREA,
            )
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        output_height, output_width = rgb.shape[:2]
        return rgb.tobytes(order="C"), output_width, output_height


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["P6RuntimeCueParityAdapter", "P6RuntimeCueParityError"]
