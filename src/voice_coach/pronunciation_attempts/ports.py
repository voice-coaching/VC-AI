"""Typed boundaries for restricted media storage and A/V container tooling."""

from __future__ import annotations

from contextlib import AbstractContextManager
from os import PathLike
from typing import BinaryIO, Protocol, runtime_checkable

from .models import (
    AvContainerPolicy,
    MediaProbe,
    RestrictedMediaDeletionReceipt,
    RestrictedPcmAudio,
    RestrictedVideoAsset,
    Rgb24Sample,
    VideoAdmissionPolicy,
    VideoConsentAuthorization,
)
from .phone_window import PhoneWindowDecodeRequest, Rgb24PhoneWindow


class RestrictedArtifactWriter(Protocol):
    """Write and integrity-bind one derived artifact without exposing its path."""

    @property
    def artifact_id(self) -> str: ...

    def write(self, payload: bytes) -> int: ...

    def commit(self, audio: RestrictedPcmAudio) -> None: ...


@runtime_checkable
class RestrictedMediaStorePort(Protocol):
    """Admit and own local attempt media behind opaque handles."""

    def admit(
        self,
        source_path: str | PathLike[str],
        *,
        attempt_id_sha256: str,
        authorization: VideoConsentAuthorization,
        policy: VideoAdmissionPolicy,
    ) -> RestrictedVideoAsset: ...

    def open_container(
        self, asset: RestrictedVideoAsset
    ) -> AbstractContextManager[BinaryIO]: ...

    def create_derived_audio(
        self, asset: RestrictedVideoAsset
    ) -> AbstractContextManager[RestrictedArtifactWriter]: ...

    def open_derived_audio(
        self, audio: RestrictedPcmAudio
    ) -> AbstractContextManager[BinaryIO]: ...

    def delete(
        self, asset: RestrictedVideoAsset
    ) -> RestrictedMediaDeletionReceipt: ...


@runtime_checkable
class AvContainerPort(Protocol):
    """Probe and locally decode the admitted single-A/V MP4 contract."""

    def probe(
        self,
        asset: RestrictedVideoAsset,
        policy: AvContainerPolicy,
    ) -> MediaProbe: ...

    def decode_rgb24_sample(
        self,
        asset: RestrictedVideoAsset,
        policy: AvContainerPolicy,
        *,
        frame_count: int,
    ) -> Rgb24Sample: ...

    def extract_embedded_audio(
        self,
        asset: RestrictedVideoAsset,
        policy: AvContainerPolicy,
    ) -> RestrictedPcmAudio: ...


@runtime_checkable
class PhoneWindowDecoderPort(Protocol):
    """Decode one trusted host-issued selected-phone window under hard bounds."""

    def decode_rgb24_window(
        self,
        asset: RestrictedVideoAsset,
        policy: AvContainerPolicy,
        *,
        probe: MediaProbe,
        request: PhoneWindowDecodeRequest,
    ) -> Rgb24PhoneWindow: ...


__all__ = [
    "AvContainerPort",
    "PhoneWindowDecoderPort",
    "RestrictedArtifactWriter",
    "RestrictedMediaStorePort",
]
