"""Concrete local media adapters for pronunciation attempts."""

from .ffmpeg_av_container import FfmpegAvContainerAdapter
from .restricted_local_media import LocalRestrictedVideoAdmissionAdapter

__all__ = [
    "FfmpegAvContainerAdapter",
    "LocalRestrictedVideoAdmissionAdapter",
]
