"""Frozen pre-observation execution policy for AI Hub 538 P6 photometric v4."""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import json
import os
import stat

from ...pronunciation_attempts.adapters.mediapipe_lip_landmarks import (
    MediaPipeLandmarkPolicy,
)
from ...pronunciation_attempts.adapters.video_qc import VideoQcPolicy
from ...pronunciation_attempts.models import AvContainerPolicy
from .integrity import sha256_document


AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY = {
    "schema_version": "voice_coach.aihub538_photometric_v4_production_execution_policy.v1",
    "policy_state": "frozen_pre_observation",
    "landmark_model_sha256": "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff",
    "av_sync": {
        "maximum_absolute_offset_ms": 300,
        "offset_step_ms": 20,
        "energy_window_ms": 40,
        "minimum_landmark_frames": 6,
        "maximum_anchor_count": 4,
    },
    "video_qc": {
        "underexposed_luma_max_8bit": 16,
        "overexposed_luma_min_8bit": 239,
        "lip_roi_padding_fraction": 0.05,
        "minimum_single_face_frames": 6,
        "maximum_total_rgb_bytes": 67_108_864,
    },
    "mediapipe": {
        "minimum_face_detection_confidence": 0.5,
        "minimum_face_presence_confidence": 0.5,
        "minimum_tracking_confidence": 0.5,
        "maximum_frame_count": 8,
        "maximum_total_rgb_bytes": 67_108_864,
        "subprocess_timeout_seconds": 120.0,
        "maximum_subprocess_output_bytes": 2_097_152,
    },
    "container": {
        "minimum_duration_ms": 500,
        "maximum_duration_ms": 25_000,
        "minimum_width": 160,
        "maximum_width": 1920,
        "minimum_height": 120,
        "maximum_height": 1920,
        "maximum_pixels_per_frame": 2_073_600,
        "minimum_frames_per_second": 15.0,
        "maximum_frames_per_second": 60.0,
        "minimum_frame_count": 8,
        "maximum_frame_count": 1_500,
        "rgb_sample_frame_count": 8,
        "maximum_rgb_sample_bytes": 67_108_864,
        "maximum_pcm_bytes": 2_097_152,
        "subprocess_timeout_seconds": 120.0,
        "maximum_subprocess_output_bytes": 4_194_304,
    },
    "colorimetry": {
        "sample_frame_count": 8,
        "subprocess_timeout_seconds": 10.0,
        "address_space_bytes": 536_870_912,
        "maximum_tool_output_bytes": 65_536,
        "ffmpeg_threads": 1,
    },
    "worker_resources": {
        "landmark_address_space_bytes": 4_294_967_296,
        "container_address_space_bytes": 6_442_450_944,
        "native_thread_limit": 1,
        "network_policy": "seccomp_socket_denied",
        "python_worker_processes": 1,
    },
    "outcome_tuned": False,
    "locked_partition_accessed": False,
    "promotion_authorized": False,
}
AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256 = (
    "e8da8270b167610e3ba70b04e02fedba59b03f5800179dd9b77582b7a6a83fd4"
)
AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_CANONICAL_BYTES = json.dumps(
    AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
).encode("utf-8")
if sha256_document(AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY) != (
    AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256
):
    raise RuntimeError("aihub538_photometric_v4_execution_policy_pin_invalid")


@dataclass(frozen=True, slots=True)
class Aihub538PhotometricV4ProductionExecutionPolicy:
    """Typed capability whose constructors expose no outcome-tunable knobs."""

    policy_sha256: str

    @classmethod
    def frozen(cls) -> "Aihub538PhotometricV4ProductionExecutionPolicy":
        global _FROZEN_POLICY
        if _FROZEN_POLICY is None:
            _FROZEN_POLICY = cls(
                policy_sha256=AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256,
            )
        return _FROZEN_POLICY

    def __post_init__(self) -> None:
        if (
            self.policy_sha256
            != AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256
        ):
            raise ValueError("aihub538_photometric_v4_execution_policy_invalid")

    @property
    def document(self) -> dict[str, object]:
        return json.loads(json.dumps(AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY))

    def video_qc_policy(self) -> VideoQcPolicy:
        return VideoQcPolicy(**dict(self.document["video_qc"]))

    def landmark_policy(self) -> MediaPipeLandmarkPolicy:
        return MediaPipeLandmarkPolicy(**dict(self.document["mediapipe"]))

    def container_policy(self) -> AvContainerPolicy:
        values = dict(self.document["container"])
        values.pop("maximum_pixels_per_frame")
        values["maximum_rgb_sample_frames"] = values.pop("rgb_sample_frame_count")
        return AvContainerPolicy(**values)

    def integration_binding(self) -> dict[str, object]:
        return {
            "production_execution_policy_sha256": self.policy_sha256,
            "production_execution_policy_state": "frozen_pre_observation",
            "outcome_tuned": False,
            "locked_partition_accessed": False,
            "promotion_authorized": False,
        }


def load_aihub538_photometric_v4_production_policy_descriptor(
    descriptor: int,
) -> Aihub538PhotometricV4ProductionExecutionPolicy:
    """Strictly load an owner-only 0400 canonical policy document."""

    try:
        if fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY:
            raise ValueError
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or metadata.st_size > 64 * 1024
            or metadata.st_mode & 0o777 != 0o400
        ):
            raise ValueError
        stable = _stable_stat(metadata)
        payload = os.pread(descriptor, metadata.st_size, 0)
        if len(payload) != metadata.st_size or _stable_stat(os.fstat(descriptor)) != stable:
            raise ValueError
        if payload != AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_CANONICAL_BYTES:
            raise ValueError
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        if document != AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY:
            raise ValueError
        return Aihub538PhotometricV4ProductionExecutionPolicy.frozen()
    except Exception:
        raise ValueError("aihub538_photometric_v4_execution_policy_reload_failed") from None


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _stable_stat(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
        value.st_ctime_ns, value.st_mode, value.st_nlink,
    )


_FROZEN_POLICY: Aihub538PhotometricV4ProductionExecutionPolicy | None = None


__all__ = [
    "AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY",
    "AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256",
    "AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_CANONICAL_BYTES",
    "Aihub538PhotometricV4ProductionExecutionPolicy",
    "load_aihub538_photometric_v4_production_policy_descriptor",
]
