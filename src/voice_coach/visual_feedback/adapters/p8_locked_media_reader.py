"""Concrete, descriptor-safe media engine for sealed P8 locked items.

The reader is issued only by the production P8 assembly boundary.  It accepts
the opaque streams already paired by :mod:`aihub538_locked_dataset`, creates
bounded files below the source evaluator's restricted ``work`` descriptor,
and runs the same ffmpeg, MediaPipe, video-QC, and lip/audio sync adapters used
for actual AI Hub 538 calibration.  Member names, transcripts, source paths,
and per-item measurements never leave this module.

Every item directory is removed and its parent directory is fsynced before a
measurement can return.  If local removal itself fails, evaluation fails
closed and the enclosing P8 source evaluator's durable cleanup state remains
responsible for the entire restricted ``work`` subtree.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import time
from typing import Final

from ...pronunciation_attempts.adapters.ffmpeg_av_container import (
    FfmpegAvContainerAdapter,
)
from ...pronunciation_attempts.adapters.lip_audio_av_sync import (
    AvSyncCandidateEvidence,
    LipAudioAvSyncAdapter,
    LipAudioAvSyncPolicy,
)
from ...pronunciation_attempts.adapters.mediapipe_lip_landmarks import (
    MediaPipeLandmarkPolicy,
    MediaPipeLipLandmarkAdapter,
)
from ...pronunciation_attempts.adapters.network_isolated_exec import (
    isolated_command,
)
from ...pronunciation_attempts.adapters.restricted_local_media import (
    LocalRestrictedVideoAdmissionAdapter,
)
from ...pronunciation_attempts.adapters.video_qc import (
    VideoQcCandidateAdapter,
    VideoQcCandidateEvidence,
    VideoQcPolicy,
)
from ...pronunciation_attempts.models import (
    AvContainerPolicy,
    PronunciationAttemptMediaError,
    RestrictedVideoAsset,
    VideoAdmissionPolicy,
    VideoConsentAuthorization,
    VideoConsentDecision,
)
from ..models.binding import MediaTimebaseReceipt, SameAttemptBindingReceipt
from ..models.integrity import sha256_document
from .aihub538_locked_dataset import P8LockedItemHandle


_PURPOSE: Final[str] = "p8_locked_evaluation"
_DATA_CATEGORY: Final[str] = "facial_video"
_RETENTION_POLICY: Final[str] = "p8_ephemeral_locked_media_v1"
_MAXIMUM_LABEL_BYTES: Final[int] = 32 * 1024 * 1024
_MAXIMUM_VIDEO_BYTES: Final[int] = 512 * 1024 * 1024
_MAXIMUM_AUDIO_BYTES: Final[int] = 256 * 1024 * 1024
_MAXIMUM_MODEL_BYTES: Final[int] = 64 * 1024 * 1024
_MAXIMUM_MUXED_BYTES: Final[int] = 256 * 1024 * 1024
_COPY_CHUNK_BYTES: Final[int] = 1024 * 1024
_MINIMUM_WINDOW_SECONDS: Final[float] = 0.50
_MAXIMUM_WINDOW_SECONDS: Final[float] = 20.0
_WINDOW_PADDING_SECONDS: Final[float] = 0.10
_MUX_TIMEOUT_SECONDS: Final[float] = 180.0
_ITEM_DIRECTORY_ATTEMPTS: Final[int] = 32
_VISUAL_SAMPLE_FRAME_COUNT: Final[int] = 8
_ISSUER: Final[object] = object()
_MEASUREMENT_ISSUER: Final[object] = object()
_ROOT = Path(__file__).resolve().parents[4]
_ISOLATION_LAUNCHER = (
    _ROOT
    / "src/voice_coach/pronunciation_attempts/adapters/network_isolated_exec.py"
)


class P8LockedMediaReaderError(RuntimeError):
    """Path-free failure from the exact locked-media evaluation engine."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class P8LockedMediaMeasurement:
    """Unforgeable typed measurement returned only by the concrete reader."""

    __slots__ = (
        "binding",
        "video_qc",
        "av_sync",
        "_issued_marker",
        "_lineage_sha256",
        "_frozen",
    )

    def __init__(
        self,
        issuer: object,
        *,
        binding: SameAttemptBindingReceipt,
        video_qc: VideoQcCandidateEvidence,
        av_sync: AvSyncCandidateEvidence,
    ) -> None:
        if issuer is not _MEASUREMENT_ISSUER:
            raise P8LockedMediaReaderError(
                "p8_locked_media_measurement_direct_construction_forbidden"
            )
        if (
            type(binding) is not SameAttemptBindingReceipt
            or type(video_qc) is not VideoQcCandidateEvidence
            or type(av_sync) is not AvSyncCandidateEvidence
        ):
            raise P8LockedMediaReaderError(
                "p8_locked_media_measurement_type_invalid"
            )
        object.__setattr__(self, "binding", binding)
        object.__setattr__(self, "video_qc", video_qc)
        object.__setattr__(self, "av_sync", av_sync)
        object.__setattr__(self, "_issued_marker", _MEASUREMENT_ISSUER)
        object.__setattr__(
            self,
            "_lineage_sha256",
            _measurement_lineage_sha256(
                binding=binding,
                video_qc=video_qc,
                av_sync=av_sync,
            ),
        )
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("P8LockedMediaMeasurement is immutable")


class P8LockedMediaReader:
    """Measure exact sealed item streams with fixed bounded production tools."""

    __slots__ = (
        "_model_descriptor",
        "_model_identity",
        "_model_sha256",
        "_video_qc_policy",
        "_av_sync_policy",
        "_container_policy",
        "_landmark_policy",
        "_ffmpeg_binary",
        "_ffprobe_binary",
        "_workspace_descriptor",
        "_access_transaction_sha256",
        "_issued_at_utc",
        "_issued_datetime",
        "_closed",
        "_issued_marker",
    )

    def __init__(
        self,
        issuer: object,
        *,
        landmark_model_descriptor: int,
        landmark_model_sha256: str,
        video_qc_policy: VideoQcPolicy,
        av_sync_policy: LipAudioAvSyncPolicy,
    ) -> None:
        if issuer is not _ISSUER:
            raise P8LockedMediaReaderError(
                "p8_locked_media_reader_direct_construction_forbidden"
            )
        if type(video_qc_policy) is not VideoQcPolicy or type(
            av_sync_policy
        ) is not LipAudioAvSyncPolicy:
            raise P8LockedMediaReaderError(
                "p8_locked_media_reader_policy_type_invalid"
            )
        model_descriptor, model_identity = _duplicate_model_descriptor(
            landmark_model_descriptor,
            expected_sha256=landmark_model_sha256,
        )
        self._model_descriptor = model_descriptor
        self._model_identity = model_identity
        self._model_sha256 = landmark_model_sha256
        self._video_qc_policy = video_qc_policy
        self._av_sync_policy = av_sync_policy
        self._container_policy = AvContainerPolicy(
            minimum_duration_ms=500,
            maximum_duration_ms=25_000,
            minimum_width=160,
            maximum_width=1920,
            minimum_height=120,
            maximum_height=1920,
            minimum_frames_per_second=15.0,
            maximum_frames_per_second=60.0,
            minimum_frame_count=8,
            maximum_frame_count=1_500,
            maximum_rgb_sample_frames=_VISUAL_SAMPLE_FRAME_COUNT,
            maximum_rgb_sample_bytes=64 * 1024 * 1024,
            maximum_pcm_bytes=2 * 1024 * 1024,
            subprocess_timeout_seconds=120.0,
            maximum_subprocess_output_bytes=4 * 1024 * 1024,
        )
        self._landmark_policy = MediaPipeLandmarkPolicy(
            minimum_face_detection_confidence=0.5,
            minimum_face_presence_confidence=0.5,
            minimum_tracking_confidence=0.5,
            maximum_frame_count=_VISUAL_SAMPLE_FRAME_COUNT,
            maximum_total_rgb_bytes=64 * 1024 * 1024,
            subprocess_timeout_seconds=120.0,
            maximum_subprocess_output_bytes=2 * 1024 * 1024,
        )
        self._ffmpeg_binary = _absolute_binary("ffmpeg")
        self._ffprobe_binary = _absolute_binary("ffprobe")
        self._workspace_descriptor: int | None = None
        self._access_transaction_sha256: str | None = None
        self._issued_at_utc: str | None = None
        self._issued_datetime: datetime | None = None
        self._closed = False
        self._issued_marker = _ISSUER

    @contextmanager
    def bind_restricted_workspace(
        self,
        *,
        workspace_descriptor: int,
        access_transaction_sha256: str,
        issued_at_utc: str,
    ) -> Iterator[None]:
        """Bind one source-evaluator work descriptor for one evaluation run."""

        if self._closed or self._workspace_descriptor is not None:
            raise P8LockedMediaReaderError(
                "p8_locked_media_workspace_state_invalid"
            )
        if not _is_sha256(access_transaction_sha256):
            raise P8LockedMediaReaderError(
                "p8_locked_media_transaction_binding_invalid"
            )
        try:
            issued_datetime = datetime.fromisoformat(
                issued_at_utc.replace("Z", "+00:00")
            )
        except (AttributeError, ValueError):
            raise P8LockedMediaReaderError(
                "p8_locked_media_issued_at_invalid"
            ) from None
        if issued_datetime.tzinfo is None or issued_datetime.utcoffset() != timedelta(0):
            raise P8LockedMediaReaderError(
                "p8_locked_media_issued_at_invalid"
            )
        duplicate = _duplicate_private_directory(workspace_descriptor)
        self._workspace_descriptor = duplicate
        self._access_transaction_sha256 = access_transaction_sha256
        self._issued_at_utc = issued_at_utc
        self._issued_datetime = issued_datetime.astimezone(timezone.utc)
        try:
            yield
        finally:
            self._workspace_descriptor = None
            self._access_transaction_sha256 = None
            self._issued_at_utc = None
            self._issued_datetime = None
            os.close(duplicate)

    def measure(self, item: P8LockedItemHandle) -> P8LockedMediaMeasurement:
        """Materialize, mux, decode, track, QC, sync, and delete one item."""

        if type(item) is not P8LockedItemHandle:
            raise P8LockedMediaReaderError("p8_locked_media_item_invalid")
        workspace_descriptor = self._workspace_descriptor
        if self._closed or workspace_descriptor is None:
            raise P8LockedMediaReaderError(
                "p8_locked_media_workspace_not_bound"
            )
        _assert_model_descriptor_stable(
            self._model_descriptor,
            self._model_identity,
            expected_sha256=self._model_sha256,
        )
        item_name, item_descriptor = _create_item_directory(workspace_descriptor)
        body_error: BaseException | None = None
        result: P8LockedMediaMeasurement | None = None
        try:
            result = self._measure_in_item_directory(
                item=item,
                item_descriptor=item_descriptor,
            )
        except BaseException as error:
            body_error = error
        cleanup_error: BaseException | None = None
        try:
            _remove_item_directory(
                workspace_descriptor=workspace_descriptor,
                item_descriptor=item_descriptor,
                item_name=item_name,
            )
        except BaseException as error:
            cleanup_error = error
        finally:
            os.close(item_descriptor)
        if cleanup_error is not None:
            raise P8LockedMediaReaderError(
                "p8_locked_media_item_cleanup_failed"
            ) from None
        if body_error is not None:
            raise _path_free_error(body_error) from None
        if result is None:
            raise P8LockedMediaReaderError(
                "p8_locked_media_measurement_failed"
            )
        return result

    def close(self) -> None:
        """Release the duplicated model descriptor after one-shot completion."""

        if self._closed:
            return
        if self._workspace_descriptor is not None:
            raise P8LockedMediaReaderError(
                "p8_locked_media_workspace_still_bound"
            )
        self._closed = True
        os.close(self._model_descriptor)

    def _measure_in_item_directory(
        self,
        *,
        item: P8LockedItemHandle,
        item_descriptor: int,
    ) -> P8LockedMediaMeasurement:
        label_payload = _read_bounded_item_stream(
            item.open_label,
            maximum_bytes=_MAXIMUM_LABEL_BYTES,
            reason="p8_locked_media_label_resource_limit_exceeded",
        )
        start_seconds, duration_seconds = _select_sentence_window(
            payload=label_payload,
            item_binding_sha256=item.item_binding_sha256,
        )
        video_descriptor: int | None = None
        audio_descriptor: int | None = None
        muxed_descriptor: int | None = None
        try:
            video_descriptor = _copy_item_stream_to_file(
                item.open_video,
                item_descriptor=item_descriptor,
                filename="source-video",
                maximum_bytes=_MAXIMUM_VIDEO_BYTES,
                reason="p8_locked_media_video_resource_limit_exceeded",
            )
            audio_descriptor = _copy_item_stream_to_file(
                item.open_audio,
                item_descriptor=item_descriptor,
                filename="source-audio",
                maximum_bytes=_MAXIMUM_AUDIO_BYTES,
                reason="p8_locked_media_audio_resource_limit_exceeded",
            )
            muxed_descriptor = _create_private_regular(
                item_descriptor, "runtime-envelope.mp4"
            )
            _run_mux(
                ffmpeg_binary=self._ffmpeg_binary,
                video_descriptor=video_descriptor,
                audio_descriptor=audio_descriptor,
                output_descriptor=muxed_descriptor,
                start_seconds=start_seconds,
                duration_seconds=duration_seconds,
            )
            muxed_metadata = os.fstat(muxed_descriptor)
            if not 0 < muxed_metadata.st_size <= _MAXIMUM_MUXED_BYTES:
                raise P8LockedMediaReaderError(
                    "p8_locked_media_mux_resource_limit_exceeded"
                )
            os.fsync(muxed_descriptor)
            os.fchmod(muxed_descriptor, 0o400)
            read_only_muxed_descriptor = _reopen_read_only_regular(
                directory_descriptor=item_descriptor,
                filename="runtime-envelope.mp4",
                expected_metadata=os.fstat(muxed_descriptor),
            )
            os.close(muxed_descriptor)
            muxed_descriptor = read_only_muxed_descriptor
            authorization = self._dataset_authorization(item)
            media_store = LocalRestrictedVideoAdmissionAdapter(
                restricted_root=(
                    Path(f"/proc/self/fd/{item_descriptor}") / "media-store"
                ),
                clock=lambda: self._issued_datetime,
            )
            asset = media_store.admit(
                f"fd:{muxed_descriptor}",
                attempt_id_sha256=item.item_binding_sha256,
                authorization=authorization,
                policy=VideoAdmissionPolicy(
                    maximum_source_bytes=_MAXIMUM_MUXED_BYTES,
                    required_purpose=_PURPOSE,
                    required_data_category=_DATA_CATEGORY,
                    allowed_retention_policy_ids=(_RETENTION_POLICY,),
                ),
            )
            return self._measure_admitted_asset(
                item=item,
                media_store=media_store,
                asset=asset,
                authorization=authorization,
            )
        finally:
            for descriptor in (
                video_descriptor,
                audio_descriptor,
                muxed_descriptor,
            ):
                if descriptor is not None:
                    os.close(descriptor)

    def _measure_admitted_asset(
        self,
        *,
        item: P8LockedItemHandle,
        media_store: LocalRestrictedVideoAdmissionAdapter,
        asset: RestrictedVideoAsset,
        authorization: VideoConsentAuthorization,
    ) -> P8LockedMediaMeasurement:
        body_error: BaseException | None = None
        result: P8LockedMediaMeasurement | None = None
        try:
            container = FfmpegAvContainerAdapter(
                media_store,
                ffprobe_binary=self._ffprobe_binary,
                ffmpeg_binary=self._ffmpeg_binary,
            )
            probe = container.probe(asset, self._container_policy)
            sample = container.decode_rgb24_sample(
                asset,
                self._container_policy,
                frame_count=_VISUAL_SAMPLE_FRAME_COUNT,
            )
            tracker = MediaPipeLipLandmarkAdapter(
                model_path="descriptor_bound_model",
                model_descriptor=self._model_descriptor,
                expected_model_sha256=self._model_sha256,
                policy=self._landmark_policy,
                repository_root=_ROOT,
            )
            landmarks = tracker.detect(sample)
            video_qc = VideoQcCandidateAdapter(
                policy=self._video_qc_policy
            ).measure(sample=sample, landmarks=landmarks)
            audio = container.extract_embedded_audio(
                asset, self._container_policy
            )
            av_sync = LipAudioAvSyncAdapter(
                media_store=media_store,
                policy=self._av_sync_policy,
            ).measure(audio=audio, landmarks=landmarks, probe=probe)
            binding = self._same_attempt_binding(
                item=item,
                authorization=authorization,
                probe=probe,
                audio=audio,
            )
            result = P8LockedMediaMeasurement(
                _MEASUREMENT_ISSUER,
                binding=binding,
                video_qc=video_qc,
                av_sync=av_sync,
            )
        except BaseException as error:
            body_error = error
        try:
            media_store.delete(asset)
        except BaseException:
            raise P8LockedMediaReaderError(
                "p8_locked_media_restricted_asset_cleanup_failed"
            ) from None
        if body_error is not None:
            raise body_error
        if result is None:
            raise P8LockedMediaReaderError(
                "p8_locked_media_measurement_failed"
            )
        return result

    def _dataset_authorization(
        self, item: P8LockedItemHandle
    ) -> VideoConsentAuthorization:
        issued = self._issued_datetime
        transaction = self._access_transaction_sha256
        if issued is None or transaction is None:
            raise P8LockedMediaReaderError(
                "p8_locked_media_workspace_not_bound"
            )
        return VideoConsentAuthorization(
            attempt_id_sha256=item.item_binding_sha256,
            consent_receipt_sha256=sha256_document(
                {
                    "schema_version": (
                        "voice_coach.p8_locked_dataset_media_authorization.v1"
                    ),
                    "item_binding_sha256": item.item_binding_sha256,
                    "access_transaction_sha256": transaction,
                    "purpose": _PURPOSE,
                    "delete_on_completion": True,
                    "remote_egress_allowed": False,
                }
            ),
            decision=VideoConsentDecision.GRANTED,
            purpose=_PURPOSE,
            data_category=_DATA_CATEGORY,
            retention_policy_id=_RETENTION_POLICY,
            delete_on_completion=True,
            remote_egress_allowed=False,
            issued_at_utc=issued - timedelta(seconds=1),
            expires_at_utc=issued + timedelta(days=1),
        )

    def _same_attempt_binding(
        self,
        *,
        item: P8LockedItemHandle,
        authorization: VideoConsentAuthorization,
        probe: object,
        audio: object,
    ) -> SameAttemptBindingReceipt:
        issued_at_utc = self._issued_at_utc
        transaction = self._access_transaction_sha256
        if issued_at_utc is None or transaction is None:
            raise P8LockedMediaReaderError(
                "p8_locked_media_workspace_not_bound"
            )
        # The concrete runtime types are deliberately accessed only after the
        # adapters created them; any absent or incompatible attribute maps to
        # one path-free failure at the caller boundary.
        audio_timebase = MediaTimebaseReceipt(
            clock_id_sha256=sha256_document(
                {
                    "source": audio.derivation_receipt.pcm_sha256,
                    "clock": "audio",
                }
            ),
            duration_ms=probe.audio_stream.duration_ms,
            origin_offset_ms=round(probe.audio_stream.start_time_us / 1_000),
            transform_revision="ffmpeg_pcm_s16le_16khz_v1",
        )
        video_timebase = MediaTimebaseReceipt(
            clock_id_sha256=sha256_document(
                {"source": probe.container_sha256, "clock": "video"}
            ),
            duration_ms=probe.video_stream.duration_ms,
            origin_offset_ms=round(probe.video_stream.start_time_us / 1_000),
            transform_revision="ffprobe_best_effort_timestamp_v1",
        )
        return SameAttemptBindingReceipt.issue(
            attempt_id_sha256=item.item_binding_sha256,
            prompt_id_sha256=sha256_document(
                {"dataset": "aihub_538", "role": "p8_locked_window"}
            ),
            prompt_revision="aihub538_p8_locked_window_v1",
            expected_phone_sha256=sha256_document(
                {"expected_phone_role": "not_used_for_p8_qc_sync"}
            ),
            analysis_sha256=sha256_document(
                {
                    "item_binding_sha256": item.item_binding_sha256,
                    "access_transaction_sha256": transaction,
                }
            ),
            audio_source_sha256=audio.derivation_receipt.pcm_sha256,
            audio_derivation_receipt_sha256=(
                audio.derivation_receipt.receipt_sha256
            ),
            video_source_sha256=probe.container_sha256,
            audio_timebase=audio_timebase,
            video_timebase=video_timebase,
            upstream_reliability_receipt_sha256=sha256_document(
                {"scope": "p8_locked_qc_sync_not_seungun_inference"}
            ),
            consent_receipt_sha256=authorization.consent_receipt_sha256,
            issued_at_utc=issued_at_utc,
        )


def issue_p8_locked_media_reader(
    *,
    landmark_model_descriptor: int,
    landmark_model_sha256: str,
    video_qc_policy: VideoQcPolicy,
    av_sync_policy: LipAudioAvSyncPolicy,
) -> P8LockedMediaReader:
    """Issue the one exact concrete engine accepted by production assembly."""

    return P8LockedMediaReader(
        _ISSUER,
        landmark_model_descriptor=landmark_model_descriptor,
        landmark_model_sha256=landmark_model_sha256,
        video_qc_policy=video_qc_policy,
        av_sync_policy=av_sync_policy,
    )


def require_issued_p8_locked_media_measurement(
    value: object,
    *,
    expected_item_binding_sha256: str,
) -> P8LockedMediaMeasurement:
    """Reject structural or directly constructed measurement substitutes."""

    if (
        type(value) is not P8LockedMediaMeasurement
        or getattr(value, "_issued_marker", None) is not _MEASUREMENT_ISSUER
        or value.binding.attempt_id_sha256 != expected_item_binding_sha256
        or value._lineage_sha256
        != _measurement_lineage_sha256(
            binding=value.binding,
            video_qc=value.video_qc,
            av_sync=value.av_sync,
        )
    ):
        raise P8LockedMediaReaderError(
            "p8_locked_media_measurement_lineage_invalid"
        )
    return value


def _measurement_lineage_sha256(
    *,
    binding: SameAttemptBindingReceipt,
    video_qc: VideoQcCandidateEvidence,
    av_sync: AvSyncCandidateEvidence,
) -> str:
    return sha256_document(
        {
            "schema_version": "voice_coach.p8_locked_media_measurement.v1",
            "binding_receipt_sha256": binding.receipt_sha256,
            "video_qc_receipt_sha256": video_qc.receipt_sha256,
            "av_sync_receipt_sha256": av_sync.receipt_sha256,
        }
    )


def p8_locked_media_engine_code_sha256() -> str:
    """Digest the concrete engine source installed for the v4 media pin."""

    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:
        raise P8LockedMediaReaderError(
            "p8_locked_media_engine_code_unavailable"
        ) from None


def _duplicate_model_descriptor(
    descriptor: int, *, expected_sha256: str
) -> tuple[int, tuple[int, int, int, int, int, int]]:
    if (
        isinstance(descriptor, bool)
        or not isinstance(descriptor, int)
        or descriptor < 3
        or not _is_sha256(expected_sha256)
    ):
        raise P8LockedMediaReaderError(
            "p8_locked_media_model_descriptor_invalid"
        )
    duplicate = -1
    try:
        duplicate = os.dup(descriptor)
        os.set_inheritable(duplicate, False)
        flags = fcntl.fcntl(duplicate, fcntl.F_GETFL)
        metadata = os.fstat(duplicate)
        if (
            flags & os.O_ACCMODE != os.O_RDONLY
            or not stat.S_ISREG(metadata.st_mode)
            or not 0 < metadata.st_size <= _MAXIMUM_MODEL_BYTES
        ):
            raise OSError
        identity = _descriptor_identity(metadata)
        if _sha256_stable_descriptor(duplicate, identity) != expected_sha256:
            raise P8LockedMediaReaderError(
                "p8_locked_media_model_integrity_failed"
            )
        return duplicate, identity
    except P8LockedMediaReaderError:
        if duplicate >= 0:
            os.close(duplicate)
        raise
    except OSError:
        if duplicate >= 0:
            os.close(duplicate)
        raise P8LockedMediaReaderError(
            "p8_locked_media_model_descriptor_invalid"
        ) from None


def _assert_model_descriptor_stable(
    descriptor: int,
    identity: tuple[int, int, int, int, int, int],
    *,
    expected_sha256: str,
) -> None:
    try:
        metadata = os.fstat(descriptor)
        if _descriptor_identity(metadata) != identity:
            raise OSError
        digest = _sha256_stable_descriptor(descriptor, identity)
    except OSError:
        raise P8LockedMediaReaderError(
            "p8_locked_media_model_changed"
        ) from None
    if digest != expected_sha256:
        raise P8LockedMediaReaderError(
            "p8_locked_media_model_changed"
        )


def _descriptor_identity(
    metadata: os.stat_result,
) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _sha256_stable_descriptor(
    descriptor: int,
    identity: tuple[int, int, int, int, int, int],
) -> str:
    digest = hashlib.sha256()
    offset = 0
    expected_size = identity[3]
    while offset < expected_size:
        block = os.pread(
            descriptor,
            min(_COPY_CHUNK_BYTES, expected_size - offset),
            offset,
        )
        if not block:
            raise OSError
        digest.update(block)
        offset += len(block)
    if _descriptor_identity(os.fstat(descriptor)) != identity:
        raise OSError
    return digest.hexdigest()


def _duplicate_private_directory(descriptor: int) -> int:
    if isinstance(descriptor, bool) or not isinstance(descriptor, int) or descriptor < 3:
        raise P8LockedMediaReaderError(
            "p8_locked_media_workspace_descriptor_invalid"
        )
    duplicate = -1
    try:
        duplicate = os.dup(descriptor)
        os.set_inheritable(duplicate, False)
        metadata = os.fstat(duplicate)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise OSError
        return duplicate
    except OSError:
        if duplicate >= 0:
            os.close(duplicate)
        raise P8LockedMediaReaderError(
            "p8_locked_media_workspace_descriptor_invalid"
        ) from None


def _create_item_directory(workspace_descriptor: int) -> tuple[str, int]:
    for _ in range(_ITEM_DIRECTORY_ATTEMPTS):
        name = f"media-item-{secrets.token_hex(16)}"
        try:
            os.mkdir(name, 0o700, dir_fd=workspace_descriptor)
        except FileExistsError:
            continue
        except OSError:
            break
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY
                | os.O_DIRECTORY
                | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=workspace_descriptor,
            )
            os.fchmod(descriptor, 0o700)
            os.fsync(workspace_descriptor)
            return name, descriptor
        except OSError:
            try:
                os.rmdir(name, dir_fd=workspace_descriptor)
            except OSError:
                pass
            break
    raise P8LockedMediaReaderError(
        "p8_locked_media_item_workspace_creation_failed"
    )


def _remove_item_directory(
    *,
    workspace_descriptor: int,
    item_descriptor: int,
    item_name: str,
) -> None:
    _remove_contents(item_descriptor)
    os.rmdir(item_name, dir_fd=workspace_descriptor)
    os.fsync(workspace_descriptor)


def _remove_contents(directory_descriptor: int) -> None:
    for name in os.listdir(directory_descriptor):
        metadata = os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if stat.S_ISDIR(metadata.st_mode):
            child = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory_descriptor,
            )
            try:
                _remove_contents(child)
            finally:
                os.close(child)
            os.rmdir(name, dir_fd=directory_descriptor)
        elif stat.S_ISREG(metadata.st_mode):
            os.unlink(name, dir_fd=directory_descriptor)
        else:
            raise OSError
    os.fsync(directory_descriptor)


def _read_bounded_item_stream(
    opener: object,
    *,
    maximum_bytes: int,
    reason: str,
) -> bytes:
    payload = bytearray()
    try:
        with opener() as source:
            while True:
                chunk = source.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                if not isinstance(chunk, bytes) or len(payload) + len(chunk) > maximum_bytes:
                    raise P8LockedMediaReaderError(reason)
                payload.extend(chunk)
    except P8LockedMediaReaderError:
        raise
    except Exception:
        raise P8LockedMediaReaderError(
            "p8_locked_media_member_read_failed"
        ) from None
    if not payload:
        raise P8LockedMediaReaderError(
            "p8_locked_media_member_empty"
        )
    return bytes(payload)


def _copy_item_stream_to_file(
    opener: object,
    *,
    item_descriptor: int,
    filename: str,
    maximum_bytes: int,
    reason: str,
) -> int:
    descriptor = _create_private_regular(item_descriptor, filename)
    observed = 0
    try:
        with opener() as source:
            while True:
                chunk = source.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                if not isinstance(chunk, bytes) or observed + len(chunk) > maximum_bytes:
                    raise P8LockedMediaReaderError(reason)
                _write_all(descriptor, chunk)
                observed += len(chunk)
        if observed <= 0:
            raise P8LockedMediaReaderError(
                "p8_locked_media_member_empty"
            )
        os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except BaseException:
        os.close(descriptor)
        try:
            os.unlink(filename, dir_fd=item_descriptor)
            os.fsync(item_descriptor)
        except OSError:
            pass
        raise


def _create_private_regular(directory_descriptor: int, filename: str) -> int:
    try:
        return os.open(
            filename,
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=directory_descriptor,
        )
    except OSError:
        raise P8LockedMediaReaderError(
            "p8_locked_media_private_file_creation_failed"
        ) from None


def _reopen_read_only_regular(
    *,
    directory_descriptor: int,
    filename: str,
    expected_metadata: os.stat_result,
) -> int:
    descriptor = -1
    try:
        descriptor = os.open(
            filename,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory_descriptor,
        )
        observed = os.fstat(descriptor)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_dev != expected_metadata.st_dev
            or observed.st_ino != expected_metadata.st_ino
            or observed.st_size != expected_metadata.st_size
            or observed.st_mtime_ns != expected_metadata.st_mtime_ns
        ):
            raise OSError
        return descriptor
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        raise P8LockedMediaReaderError(
            "p8_locked_media_private_file_changed"
        ) from None


def _select_sentence_window(
    *, payload: bytes, item_binding_sha256: str
) -> tuple[float, float]:
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        if not isinstance(document, list) or len(document) != 1:
            raise ValueError
        record = document[0]
        if not isinstance(record, dict):
            raise ValueError
        video_info = record["Video_info"]
        audio_info = record["Audio_info"]
        sentences = record["Sentence_info"]
        if (
            not isinstance(video_info, dict)
            or not isinstance(audio_info, dict)
            or not isinstance(sentences, list)
        ):
            raise ValueError
        media_duration = min(
            _duration_seconds(video_info["video_Duration"]),
            _duration_seconds(audio_info["Audio_Duration"]),
        )
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise P8LockedMediaReaderError(
            "p8_locked_media_label_window_invalid"
        ) from None
    candidates: list[tuple[float, float]] = []
    previous_end = 0.0
    for sentence in sentences:
        if not isinstance(sentence, dict) or set(sentence) != {
            "ID", "end_time", "sentence_text", "start_time", "topic"
        }:
            raise P8LockedMediaReaderError(
                "p8_locked_media_label_window_invalid"
            )
        start = _finite_number(sentence["start_time"])
        end = _finite_number(sentence["end_time"])
        duration = end - start
        if start < 0.0 or end <= start or start + 0.5 < previous_end:
            raise P8LockedMediaReaderError(
                "p8_locked_media_label_window_invalid"
            )
        previous_end = end
        if (
            end <= media_duration + 0.5
            and _MINIMUM_WINDOW_SECONDS <= duration <= _MAXIMUM_WINDOW_SECONDS
        ):
            padded_start = max(0.0, start - _WINDOW_PADDING_SECONDS)
            padded_end = min(media_duration, end + _WINDOW_PADDING_SECONDS)
            candidates.append((padded_start, padded_end - padded_start))
    if not candidates:
        raise P8LockedMediaReaderError(
            "p8_locked_media_sentence_window_unavailable"
        )
    index = int(
        hashlib.sha256(
            b"voice_coach.aihub538.p8_sentence_window.v1\x00"
            + bytes.fromhex(item_binding_sha256)
        ).hexdigest(),
        16,
    ) % len(candidates)
    return candidates[index]


def _duration_seconds(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        stripped = value.strip()
        parts = stripped.split(":")
        if len(parts) == 1:
            result = float(stripped)
        elif len(parts) == 3:
            hours, minutes, seconds = (float(part) for part in parts)
            if (
                not hours.is_integer()
                or not minutes.is_integer()
                or hours < 0
                or not 0 <= minutes < 60
                or not 0 <= seconds < 60
            ):
                raise ValueError
            result = hours * 3600 + minutes * 60 + seconds
        else:
            raise ValueError
    else:
        raise ValueError
    if not math.isfinite(result) or not 0 < result <= 3_600:
        raise ValueError
    return result


def _finite_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise P8LockedMediaReaderError(
            "p8_locked_media_label_window_invalid"
        )
    result = float(value)
    if not math.isfinite(result):
        raise P8LockedMediaReaderError(
            "p8_locked_media_label_window_invalid"
        )
    return result


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _run_mux(
    *,
    ffmpeg_binary: str,
    video_descriptor: int,
    audio_descriptor: int,
    output_descriptor: int,
    start_seconds: float,
    duration_seconds: float,
) -> None:
    target = [
        ffmpeg_binary,
        "-v", "error",
        "-nostdin",
        "-y",
        "-protocol_whitelist", "file,pipe",
        "-ss", f"{start_seconds:.6f}",
        "-t", f"{duration_seconds:.6f}",
        "-i", f"/proc/self/fd/{video_descriptor}",
        "-ss", f"{start_seconds:.6f}",
        "-t", f"{duration_seconds:.6f}",
        "-i", f"/proc/self/fd/{audio_descriptor}",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-map_metadata", "-1",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-threads", "1",
        "-c:a", "aac",
        "-ar", "16000",
        "-ac", "1",
        "-b:a", "96k",
        "-shortest",
        "-movflags", "+faststart",
        "-f", "mp4",
        f"/proc/self/fd/{output_descriptor}",
    ]
    command = isolated_command(
        launcher_python=sys.executable,
        launcher_path=str(_ISOLATION_LAUNCHER),
        target=target,
        cpu_seconds=math.ceil(_MUX_TIMEOUT_SECONDS) + 2,
        address_space_bytes=6 * 1024 * 1024 * 1024,
    )
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(video_descriptor, audio_descriptor, output_descriptor),
            close_fds=True,
            shell=False,
            cwd=_ROOT,
            env={
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
                "PYTHONHASHSEED": "0",
            },
            start_new_session=True,
        )
    except (OSError, ValueError):
        raise P8LockedMediaReaderError(
            "p8_locked_media_mux_backend_unavailable"
        ) from None
    deadline = time.monotonic() + _MUX_TIMEOUT_SECONDS
    while process.poll() is None:
        if time.monotonic() >= deadline:
            _kill_process_group(process)
            raise P8LockedMediaReaderError("p8_locked_media_mux_timeout")
        try:
            if os.fstat(output_descriptor).st_size > _MAXIMUM_MUXED_BYTES:
                _kill_process_group(process)
                raise P8LockedMediaReaderError(
                    "p8_locked_media_mux_resource_limit_exceeded"
                )
        except OSError:
            _kill_process_group(process)
            raise P8LockedMediaReaderError(
                "p8_locked_media_mux_output_unavailable"
            ) from None
        time.sleep(0.02)
    if process.returncode != 0:
        raise P8LockedMediaReaderError("p8_locked_media_mux_failed")


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        pass


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise P8LockedMediaReaderError(
                "p8_locked_media_private_write_failed"
            )
        view = view[written:]


def _absolute_binary(value: str) -> str:
    resolved = shutil.which(value)
    if resolved is None:
        raise P8LockedMediaReaderError(
            "p8_locked_media_backend_unavailable"
        )
    return str(Path(resolved).resolve())


def _path_free_error(error: BaseException) -> P8LockedMediaReaderError:
    if isinstance(error, P8LockedMediaReaderError):
        return error
    if isinstance(error, PronunciationAttemptMediaError):
        return P8LockedMediaReaderError(
            f"p8_locked_media_{error.code.value}"
        )
    reason = getattr(error, "reason_code", None)
    if (
        isinstance(reason, str)
        and reason.isascii()
        and reason.replace("_", "").isalnum()
    ):
        return P8LockedMediaReaderError(f"p8_locked_media_{reason}")
    return P8LockedMediaReaderError("p8_locked_media_measurement_failed")


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


__all__ = [
    "P8LockedMediaMeasurement",
    "P8LockedMediaReader",
    "P8LockedMediaReaderError",
    "issue_p8_locked_media_reader",
    "p8_locked_media_engine_code_sha256",
    "require_issued_p8_locked_media_measurement",
]
