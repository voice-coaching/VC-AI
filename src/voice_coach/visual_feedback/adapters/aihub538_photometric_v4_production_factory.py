"""No-override constructors for the frozen P6 v4 production execution bundle."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models.aihub538_photometric_v4_production_policy import (
    Aihub538PhotometricV4ProductionExecutionPolicy,
)
from .aihub538_ffmpeg_colorimetry import FfmpegRgbColorimetryAdapter
from .aihub538_photometric_v4_sampling import (
    Aihub538PhotometricV4EightFrameSampler,
)
from ...pronunciation_attempts.adapters.lip_audio_av_sync import LipAudioAvSyncPolicy


@dataclass(frozen=True, slots=True)
class Aihub538PhotometricV4ProductionFactory:
    """Construct only the reviewed pre-observation policies; no CLI overrides."""

    policy: Aihub538PhotometricV4ProductionExecutionPolicy = field(
        default_factory=Aihub538PhotometricV4ProductionExecutionPolicy.frozen
    )

    def __post_init__(self) -> None:
        if (
            type(self.policy) is not Aihub538PhotometricV4ProductionExecutionPolicy
            or self.policy is not Aihub538PhotometricV4ProductionExecutionPolicy.frozen()
        ):
            raise ValueError("aihub538_v4_live_frozen_policy_capability_required")

    def video_qc_policy(self):
        return self.policy.video_qc_policy()

    def landmark_policy(self):
        return self.policy.landmark_policy()

    def container_policy(self):
        return self.policy.container_policy()

    def av_sync_policy(self) -> LipAudioAvSyncPolicy:
        return LipAudioAvSyncPolicy(**dict(self.policy.document["av_sync"]))

    @property
    def landmark_model_sha256(self) -> str:
        return str(self.policy.document["landmark_model_sha256"])

    def color_sampler(self) -> Aihub538PhotometricV4EightFrameSampler:
        return Aihub538PhotometricV4EightFrameSampler(
            policy=self.policy,
            color_adapter=FfmpegRgbColorimetryAdapter(
                timeout_seconds=float(
                    self.policy.document["colorimetry"]["subprocess_timeout_seconds"]
                )
            ),
        )

    def worker_environment(self) -> dict[str, str]:
        """Environment pins to pass unchanged to every native worker launcher."""

        return {
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "VECLIB_MAXIMUM_THREADS": "1",
            "TF_NUM_INTRAOP_THREADS": "1",
            "TF_NUM_INTEROP_THREADS": "1",
        }

    def resource_role_binding(self) -> dict[str, object]:
        """Disambiguate the frozen mux and decode address-space roles."""

        return {
            "container_mux_address_space_bytes": 6_442_450_944,
            "video_decode_address_space_bytes": 4_294_967_296,
            "landmark_address_space_bytes": 4_294_967_296,
            "native_thread_limit": 1,
            "python_worker_processes": 1,
        }


__all__ = ["Aihub538PhotometricV4ProductionFactory"]
