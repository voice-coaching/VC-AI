"""Fail-closed ffprobe/ffmpeg adapter for allowlisted video/AAC MP4 attempts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time
from typing import BinaryIO, Callable, Mapping, Sequence, cast

from ..models import (
    AudioDerivationReceipt,
    AudioStreamProbe,
    AvContainerPolicy,
    DecodedRgbFrame,
    MediaErrorCode,
    MediaProbe,
    PronunciationAttemptMediaError,
    RestrictedPcmAudio,
    RestrictedVideoAsset,
    Rgb24Sample,
    VideoStreamProbe,
)
from ..phone_window import (
    PhoneWindowDecodeReceipt,
    PhoneWindowDecodeRequest,
    Rgb24PhoneWindow,
    decoded_rgb_sha256,
)
from ..ports import RestrictedArtifactWriter, RestrictedMediaStorePort
from .network_isolated_exec import isolated_command


_VERSION_TOKEN_PATTERN = re.compile(r"[^A-Za-z0-9._+-]+")
_PROCESS_CHUNK_BYTES = 64 * 1024
_SUBPROCESS_PATH = "/usr/bin:/bin"
_SUBPROCESS_ADDRESS_SPACE_BYTES = 4 * 1024 * 1024 * 1024
_ISOLATION_LAUNCHER = Path(__file__).with_name("network_isolated_exec.py")
_SUPPORTED_MP4_MAJOR_BRANDS = frozenset(
    {
        "isom",
        "iso2",
        "iso3",
        "iso4",
        "iso5",
        "iso6",
        "mp41",
        "mp42",
        "avc1",
        "M4V ",
    }
)
_SUPPORTED_VIDEO_CODECS = frozenset({"h264", "hevc"})


@dataclass(frozen=True, slots=True)
class _ProcessCapture:
    return_code: int
    stdout: bytes
    stderr: bytes


class FfmpegAvContainerAdapter:
    """Probe and decode only the explicitly supported local MP4 contract."""

    def __init__(
        self,
        store: RestrictedMediaStorePort,
        *,
        ffprobe_binary: str = "ffprobe",
        ffmpeg_binary: str = "ffmpeg",
    ) -> None:
        if not ffprobe_binary or not ffmpeg_binary:
            raise ValueError("ffprobe_binary and ffmpeg_binary must not be empty")
        self._store = store
        self._ffprobe_binary = ffprobe_binary
        self._ffmpeg_binary = ffmpeg_binary
        self._tool_revisions: dict[str, str] = {}

    def probe(
        self,
        asset: RestrictedVideoAsset,
        policy: AvContainerPolicy,
    ) -> MediaProbe:
        """Return strict metadata and reject unsupported or ambiguous streams."""

        ffprobe_revision = self._tool_revision(
            binary=self._ffprobe_binary,
            expected_tool="ffprobe",
            policy=policy,
        )
        metadata = self._probe_stream_metadata(asset, policy)
        video_stream, audio_stream, duration_ms = self._parse_stream_metadata(
            metadata,
            policy,
        )
        timestamps_us = self._probe_video_timestamps(
            asset=asset,
            policy=policy,
            expected_stream_index=video_stream.stream_index,
            expected_frame_count=video_stream.frame_count,
        )
        try:
            return MediaProbe(
                asset_handle_id=asset.handle_id,
                container_sha256=asset.container_sha256,
                format_name="mp4",
                duration_ms=duration_ms,
                video_stream=video_stream,
                audio_stream=audio_stream,
                video_frame_timestamps_us=timestamps_us,
                ffprobe_revision=ffprobe_revision,
            )
        except ValueError:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.CONTAINER_INVALID
            ) from None

    def decode_rgb24_sample(
        self,
        asset: RestrictedVideoAsset,
        policy: AvContainerPolicy,
        *,
        frame_count: int,
    ) -> Rgb24Sample:
        """Decode a bounded, deterministic whole-clip sample as RGB24."""

        if isinstance(frame_count, bool) or not isinstance(frame_count, int):
            raise PronunciationAttemptMediaError(
                MediaErrorCode.CONTAINER_POLICY_REJECTED
            )
        probe = self.probe(asset, policy)
        if (
            frame_count <= 0
            or frame_count > policy.maximum_rgb_sample_frames
            or frame_count > probe.video_stream.frame_count
        ):
            raise PronunciationAttemptMediaError(
                MediaErrorCode.CONTAINER_POLICY_REJECTED
            )

        frame_byte_count = (
            probe.video_stream.width * probe.video_stream.height * 3
        )
        expected_byte_count = frame_byte_count * frame_count
        if expected_byte_count > policy.maximum_rgb_sample_bytes:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.CONTAINER_POLICY_REJECTED
            )

        ffmpeg_revision = self._tool_revision(
            binary=self._ffmpeg_binary,
            expected_tool="ffmpeg",
            policy=policy,
        )
        selected_indices = self._uniform_frame_indices(
            total_frame_count=probe.video_stream.frame_count,
            sample_frame_count=frame_count,
        )
        select_expression = "+".join(
            f"eq(n\\,{frame_index})" for frame_index in selected_indices
        )
        with self._store.open_container(asset) as input_file:
            input_fd = input_file.fileno()
            command = [
                self._ffmpeg_binary,
                "-v",
                "error",
                "-nostdin",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                self._descriptor_locator(input_fd),
                "-map",
                f"0:{probe.video_stream.stream_index}",
                "-an",
                "-sn",
                "-dn",
                "-vf",
                f"select={select_expression}",
                "-fps_mode",
                "passthrough",
                "-frames:v",
                str(frame_count),
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "pipe:1",
            ]
            decoded = bytearray()
            return_code = self._stream_process_stdout(
                command=command,
                pass_fds=(input_fd,),
                timeout_seconds=policy.subprocess_timeout_seconds,
                maximum_stdout_bytes=expected_byte_count,
                maximum_stderr_bytes=policy.maximum_subprocess_output_bytes,
                stdout_limit_error=MediaErrorCode.VIDEO_DECODE_FAILED,
                consume_stdout=decoded.extend,
            )
        if return_code != 0 or len(decoded) != expected_byte_count:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.VIDEO_DECODE_FAILED
            )

        frames = tuple(
            DecodedRgbFrame(
                timestamp_us=probe.video_frame_timestamps_us[selected_indices[frame_index]],
                width=probe.video_stream.width,
                height=probe.video_stream.height,
                rgb24=bytes(
                    decoded[
                        frame_index * frame_byte_count : (
                            frame_index + 1
                        )
                        * frame_byte_count
                    ]
                ),
            )
            for frame_index in range(frame_count)
        )
        try:
            return Rgb24Sample(
                asset_handle_id=asset.handle_id,
                container_sha256=asset.container_sha256,
                video_stream_index=probe.video_stream.stream_index,
                frames=frames,
                ffmpeg_revision=ffmpeg_revision,
            )
        except ValueError:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.VIDEO_DECODE_FAILED
            ) from None

    def decode_rgb24_window(
        self,
        asset: RestrictedVideoAsset,
        policy: AvContainerPolicy,
        *,
        probe: MediaProbe,
        request: PhoneWindowDecodeRequest,
    ) -> Rgb24PhoneWindow:
        """Decode exact PTS-selected frames from one trusted phone window."""

        if (
            not isinstance(probe, MediaProbe)
            or not isinstance(request, PhoneWindowDecodeRequest)
            or asset.handle_id != probe.asset_handle_id
            or asset.container_sha256 != probe.container_sha256
            or asset.attempt_id_sha256 != request.attempt_id_sha256
            or asset.container_sha256 != request.container_sha256
            or request.video_end_ms > probe.video_stream.duration_ms
        ):
            raise PronunciationAttemptMediaError(
                MediaErrorCode.CONTAINER_POLICY_REJECTED
            )

        absolute_start_us = (
            probe.video_stream.start_time_us + request.video_start_ms * 1_000
        )
        absolute_end_us = (
            probe.video_stream.start_time_us + request.video_end_ms * 1_000
        )
        candidate_indices = tuple(
            frame_index
            for frame_index, timestamp_us in enumerate(
                probe.video_frame_timestamps_us
            )
            if absolute_start_us <= timestamp_us < absolute_end_us
        )
        if len(candidate_indices) < request.minimum_frame_count:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.CONTAINER_POLICY_REJECTED
            )
        if len(candidate_indices) > request.maximum_frame_count:
            positions = self._uniform_frame_indices(
                total_frame_count=len(candidate_indices),
                sample_frame_count=request.maximum_frame_count,
            )
            selected_indices = tuple(
                candidate_indices[position] for position in positions
            )
        else:
            selected_indices = candidate_indices

        frame_byte_count = (
            probe.video_stream.width * probe.video_stream.height * 3
        )
        expected_byte_count = frame_byte_count * len(selected_indices)
        if expected_byte_count > request.maximum_rgb_bytes:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.CONTAINER_POLICY_REJECTED
            )

        ffmpeg_revision = self._tool_revision(
            binary=self._ffmpeg_binary,
            expected_tool="ffmpeg",
            policy=policy,
        )
        select_expression = "+".join(
            f"eq(n\\,{frame_index})" for frame_index in selected_indices
        )
        with self._store.open_container(asset) as input_file:
            input_fd = input_file.fileno()
            command = [
                self._ffmpeg_binary,
                "-v",
                "error",
                "-nostdin",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                self._descriptor_locator(input_fd),
                "-map",
                f"0:{probe.video_stream.stream_index}",
                "-an",
                "-sn",
                "-dn",
                "-vf",
                f"select={select_expression}",
                "-fps_mode",
                "passthrough",
                "-frames:v",
                str(len(selected_indices)),
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "pipe:1",
            ]
            decoded = bytearray()
            return_code = self._stream_process_stdout(
                command=command,
                pass_fds=(input_fd,),
                timeout_seconds=policy.subprocess_timeout_seconds,
                maximum_stdout_bytes=expected_byte_count,
                maximum_stderr_bytes=policy.maximum_subprocess_output_bytes,
                stdout_limit_error=MediaErrorCode.VIDEO_DECODE_FAILED,
                consume_stdout=decoded.extend,
            )
        if return_code != 0 or len(decoded) != expected_byte_count:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.VIDEO_DECODE_FAILED
            )

        frames = tuple(
            DecodedRgbFrame(
                timestamp_us=probe.video_frame_timestamps_us[frame_index],
                width=probe.video_stream.width,
                height=probe.video_stream.height,
                rgb24=bytes(
                    decoded[
                        position * frame_byte_count : (position + 1)
                        * frame_byte_count
                    ]
                ),
            )
            for position, frame_index in enumerate(selected_indices)
        )
        try:
            sample = Rgb24Sample(
                asset_handle_id=asset.handle_id,
                container_sha256=asset.container_sha256,
                video_stream_index=probe.video_stream.stream_index,
                frames=frames,
                ffmpeg_revision=ffmpeg_revision,
            )
            receipt = PhoneWindowDecodeReceipt.issue(
                request=request,
                video_stream_index=probe.video_stream.stream_index,
                selected_frame_indices=selected_indices,
                decoded_timestamps_us=tuple(
                    frame.timestamp_us for frame in sample.frames
                ),
                width=probe.video_stream.width,
                height=probe.video_stream.height,
                decoded_rgb_sha256=decoded_rgb_sha256(sample),
                ffmpeg_revision=ffmpeg_revision,
                decoder_code_sha256=_adapter_code_sha256(),
            )
            return Rgb24PhoneWindow(
                request=request,
                sample=sample,
                decode_receipt=receipt,
            )
        except ValueError:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.VIDEO_DECODE_FAILED
            ) from None

    @staticmethod
    def _uniform_frame_indices(
        *, total_frame_count: int, sample_frame_count: int
    ) -> tuple[int, ...]:
        if sample_frame_count == 1:
            return (0,)
        denominator = sample_frame_count - 1
        maximum_index = total_frame_count - 1
        return tuple(
            (position * maximum_index + denominator // 2) // denominator
            for position in range(sample_frame_count)
        )

    def extract_embedded_audio(
        self,
        asset: RestrictedVideoAsset,
        policy: AvContainerPolicy,
    ) -> RestrictedPcmAudio:
        """Extract embedded audio to restricted 16 kHz mono signed PCM."""

        probe = self.probe(asset, policy)
        ffmpeg_revision = self._tool_revision(
            binary=self._ffmpeg_binary,
            expected_tool="ffmpeg",
            policy=policy,
        )
        pcm_digest = hashlib.sha256()
        pcm_byte_count = 0

        with self._store.create_derived_audio(asset) as writer:

            def consume_pcm(payload: bytes) -> None:
                nonlocal pcm_byte_count
                writer.write(payload)
                pcm_digest.update(payload)
                pcm_byte_count += len(payload)

            with self._store.open_container(asset) as input_file:
                input_fd = input_file.fileno()
                command = [
                    self._ffmpeg_binary,
                    "-v",
                    "error",
                    "-nostdin",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-i",
                    self._descriptor_locator(input_fd),
                    "-map",
                    f"0:{probe.audio_stream.stream_index}",
                    "-vn",
                    "-sn",
                    "-dn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    "-f",
                    "s16le",
                    "pipe:1",
                ]
                return_code = self._stream_process_stdout(
                    command=command,
                    pass_fds=(input_fd,),
                    timeout_seconds=policy.subprocess_timeout_seconds,
                    maximum_stdout_bytes=policy.maximum_pcm_bytes,
                    maximum_stderr_bytes=policy.maximum_subprocess_output_bytes,
                    stdout_limit_error=MediaErrorCode.DERIVED_AUDIO_TOO_LARGE,
                    consume_stdout=consume_pcm,
                )
            if return_code != 0 or pcm_byte_count <= 0 or pcm_byte_count % 2:
                raise PronunciationAttemptMediaError(
                    MediaErrorCode.AUDIO_EXTRACTION_FAILED
                )

            try:
                receipt = AudioDerivationReceipt.issue(
                    container_sha256=asset.container_sha256,
                    audio_stream_index=probe.audio_stream.stream_index,
                    tool_revision=ffmpeg_revision,
                    pcm_sha256=pcm_digest.hexdigest(),
                    source_time_base=probe.audio_stream.time_base,
                )
                audio = RestrictedPcmAudio(
                    artifact_id=writer.artifact_id,
                    asset_handle_id=asset.handle_id,
                    attempt_id_sha256=asset.attempt_id_sha256,
                    byte_count=pcm_byte_count,
                    sample_count=pcm_byte_count // 2,
                    derivation_receipt=receipt,
                )
            except ValueError:
                raise PronunciationAttemptMediaError(
                    MediaErrorCode.AUDIO_EXTRACTION_FAILED
                ) from None
            writer.commit(audio)
        return audio

    def _probe_stream_metadata(
        self,
        asset: RestrictedVideoAsset,
        policy: AvContainerPolicy,
    ) -> Mapping[str, object]:
        with self._store.open_container(asset) as input_file:
            input_fd = input_file.fileno()
            command = [
                self._ffprobe_binary,
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-count_frames",
                "-show_streams",
                "-show_format",
                "-show_entries",
                (
                    "format=format_name,duration:"
                    "format_tags=major_brand:"
                    "stream=index,codec_type,codec_name,width,height,"
                    "avg_frame_rate,r_frame_rate,time_base,start_time,duration,"
                    "nb_frames,nb_read_frames,sample_rate,channels"
                ),
                "-print_format",
                "json",
                self._descriptor_locator(input_fd),
            ]
            capture = self._run_bounded_capture(
                command=command,
                pass_fds=(input_fd,),
                timeout_seconds=policy.subprocess_timeout_seconds,
                maximum_output_bytes=policy.maximum_subprocess_output_bytes,
            )
        if capture.return_code != 0:
            raise PronunciationAttemptMediaError(MediaErrorCode.CONTAINER_INVALID)
        return self._decode_json_object(capture.stdout)

    def _probe_video_timestamps(
        self,
        *,
        asset: RestrictedVideoAsset,
        policy: AvContainerPolicy,
        expected_stream_index: int,
        expected_frame_count: int,
    ) -> tuple[int, ...]:
        with self._store.open_container(asset) as input_file:
            input_fd = input_file.fileno()
            command = [
                self._ffprobe_binary,
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-select_streams",
                "v:0",
                "-show_frames",
                "-show_entries",
                "frame=stream_index,best_effort_timestamp_time",
                "-print_format",
                "json",
                self._descriptor_locator(input_fd),
            ]
            capture = self._run_bounded_capture(
                command=command,
                pass_fds=(input_fd,),
                timeout_seconds=policy.subprocess_timeout_seconds,
                maximum_output_bytes=policy.maximum_subprocess_output_bytes,
            )
        if capture.return_code != 0:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.FRAME_TIMESTAMPS_INVALID
            )
        document = self._decode_json_object(
            capture.stdout,
            invalid_code=MediaErrorCode.FRAME_TIMESTAMPS_INVALID,
        )
        frames = document.get("frames")
        if not isinstance(frames, list) or len(frames) != expected_frame_count:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.FRAME_TIMESTAMPS_INVALID
            )
        timestamps: list[int] = []
        for frame in frames:
            if not isinstance(frame, Mapping):
                raise PronunciationAttemptMediaError(
                    MediaErrorCode.FRAME_TIMESTAMPS_INVALID
                )
            if self._strict_integer(frame.get("stream_index")) != expected_stream_index:
                raise PronunciationAttemptMediaError(
                    MediaErrorCode.FRAME_TIMESTAMPS_INVALID
                )
            timestamps.append(
                self._seconds_to_units(
                    frame.get("best_effort_timestamp_time"),
                    units_per_second=1_000_000,
                    error_code=MediaErrorCode.FRAME_TIMESTAMPS_INVALID,
                )
            )
        if any(
            current <= previous
            for previous, current in zip(timestamps, timestamps[1:])
        ):
            raise PronunciationAttemptMediaError(
                MediaErrorCode.FRAME_TIMESTAMPS_INVALID
            )
        return tuple(timestamps)

    def _parse_stream_metadata(
        self,
        document: Mapping[str, object],
        policy: AvContainerPolicy,
    ) -> tuple[VideoStreamProbe, AudioStreamProbe, int]:
        streams = document.get("streams")
        format_document = document.get("format")
        if not isinstance(streams, list) or not isinstance(format_document, Mapping):
            raise PronunciationAttemptMediaError(MediaErrorCode.CONTAINER_INVALID)
        if len(streams) != 2 or not all(isinstance(stream, Mapping) for stream in streams):
            raise PronunciationAttemptMediaError(
                MediaErrorCode.CONTAINER_UNSUPPORTED
            )
        typed_streams = cast(list[Mapping[str, object]], streams)
        video_candidates = [
            stream for stream in typed_streams if stream.get("codec_type") == "video"
        ]
        audio_candidates = [
            stream for stream in typed_streams if stream.get("codec_type") == "audio"
        ]
        if len(video_candidates) != 1 or len(audio_candidates) != 1:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.CONTAINER_UNSUPPORTED
            )

        format_names = format_document.get("format_name")
        if not isinstance(format_names, str) or "mp4" not in format_names.split(","):
            raise PronunciationAttemptMediaError(
                MediaErrorCode.CONTAINER_UNSUPPORTED
            )
        format_tags = format_document.get("tags")
        if (
            not isinstance(format_tags, Mapping)
            or format_tags.get("major_brand") not in _SUPPORTED_MP4_MAJOR_BRANDS
        ):
            raise PronunciationAttemptMediaError(
                MediaErrorCode.CONTAINER_UNSUPPORTED
            )
        video_document = video_candidates[0]
        audio_document = audio_candidates[0]
        video_codec = video_document.get("codec_name")
        if video_codec not in _SUPPORTED_VIDEO_CODECS or audio_document.get(
            "codec_name"
        ) != "aac":
            raise PronunciationAttemptMediaError(
                MediaErrorCode.CONTAINER_UNSUPPORTED
            )

        average_fps = self._strict_fraction(video_document.get("avg_frame_rate"))
        nominal_fps = self._strict_fraction(video_document.get("r_frame_rate"))
        if average_fps != nominal_fps:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.CONTAINER_UNSUPPORTED
            )
        frame_count = self._strict_integer(video_document.get("nb_read_frames"))
        declared_frame_count = self._strict_integer(video_document.get("nb_frames"))
        if frame_count <= 0 or declared_frame_count != frame_count:
            raise PronunciationAttemptMediaError(MediaErrorCode.CONTAINER_INVALID)

        duration_ms = self._seconds_to_units(
            format_document.get("duration"),
            units_per_second=1_000,
            error_code=MediaErrorCode.CONTAINER_INVALID,
        )
        video_duration_ms = self._seconds_to_units(
            video_document.get("duration"),
            units_per_second=1_000,
            error_code=MediaErrorCode.CONTAINER_INVALID,
        )
        audio_duration_ms = self._seconds_to_units(
            audio_document.get("duration"),
            units_per_second=1_000,
            error_code=MediaErrorCode.CONTAINER_INVALID,
        )
        width = self._strict_integer(video_document.get("width"))
        height = self._strict_integer(video_document.get("height"))
        frames_per_second = float(average_fps)
        if not (
            policy.minimum_duration_ms
            <= duration_ms
            <= policy.maximum_duration_ms
            and policy.minimum_duration_ms
            <= video_duration_ms
            <= policy.maximum_duration_ms
            and policy.minimum_duration_ms
            <= audio_duration_ms
            <= policy.maximum_duration_ms
            and policy.minimum_width <= width <= policy.maximum_width
            and policy.minimum_height <= height <= policy.maximum_height
            and policy.minimum_frames_per_second
            <= frames_per_second
            <= policy.maximum_frames_per_second
            and policy.minimum_frame_count
            <= frame_count
            <= policy.maximum_frame_count
        ):
            raise PronunciationAttemptMediaError(
                MediaErrorCode.CONTAINER_POLICY_REJECTED
            )

        try:
            video_stream = VideoStreamProbe(
                stream_index=self._strict_integer(video_document.get("index")),
                codec_name=cast(str, video_codec),
                width=width,
                height=height,
                frames_per_second_numerator=average_fps.numerator,
                frames_per_second_denominator=average_fps.denominator,
                frame_count=frame_count,
                time_base=self._strict_time_base(video_document.get("time_base")),
                start_time_us=self._seconds_to_units(
                    video_document.get("start_time"),
                    units_per_second=1_000_000,
                    error_code=MediaErrorCode.CONTAINER_INVALID,
                ),
                duration_ms=video_duration_ms,
            )
            audio_stream = AudioStreamProbe(
                stream_index=self._strict_integer(audio_document.get("index")),
                codec_name="aac",
                sample_rate_hz=self._strict_integer(
                    audio_document.get("sample_rate")
                ),
                channels=self._strict_integer(audio_document.get("channels")),
                time_base=self._strict_time_base(audio_document.get("time_base")),
                start_time_us=self._seconds_to_units(
                    audio_document.get("start_time"),
                    units_per_second=1_000_000,
                    error_code=MediaErrorCode.CONTAINER_INVALID,
                ),
                duration_ms=audio_duration_ms,
            )
        except ValueError:
            raise PronunciationAttemptMediaError(
                MediaErrorCode.CONTAINER_INVALID
            ) from None
        return video_stream, audio_stream, duration_ms

    def _tool_revision(
        self,
        *,
        binary: str,
        expected_tool: str,
        policy: AvContainerPolicy,
    ) -> str:
        cache_key = f"{expected_tool}\0{binary}"
        cached = self._tool_revisions.get(cache_key)
        if cached is not None:
            return cached
        capture = self._run_bounded_capture(
            command=[binary, "-version"],
            pass_fds=(),
            timeout_seconds=policy.subprocess_timeout_seconds,
            maximum_output_bytes=policy.maximum_subprocess_output_bytes,
        )
        if capture.return_code != 0:
            raise PronunciationAttemptMediaError(MediaErrorCode.TOOL_EXECUTION_FAILED)
        try:
            first_line = capture.stdout.decode("utf-8", errors="strict").splitlines()[0]
            words = first_line.split()
        except (UnicodeDecodeError, IndexError):
            raise PronunciationAttemptMediaError(
                MediaErrorCode.TOOL_EXECUTION_FAILED
            ) from None
        if len(words) < 3 or words[0] != expected_tool or words[1] != "version":
            raise PronunciationAttemptMediaError(MediaErrorCode.TOOL_EXECUTION_FAILED)
        version_token = _VERSION_TOKEN_PATTERN.sub("_", words[2]).strip("_")[:48]
        if not version_token:
            raise PronunciationAttemptMediaError(MediaErrorCode.TOOL_EXECUTION_FAILED)
        version_digest = hashlib.sha256(
            capture.stdout + b"\0" + capture.stderr
        ).hexdigest()
        revision = f"{expected_tool}:{version_token}:{version_digest}"
        self._tool_revisions[cache_key] = revision
        return revision

    @staticmethod
    def _descriptor_locator(file_descriptor: int) -> str:
        return f"/proc/self/fd/{file_descriptor}"

    @staticmethod
    def _strict_integer(value: object) -> int:
        if isinstance(value, bool):
            raise PronunciationAttemptMediaError(MediaErrorCode.CONTAINER_INVALID)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value):
            return int(value)
        raise PronunciationAttemptMediaError(MediaErrorCode.CONTAINER_INVALID)

    @staticmethod
    def _strict_fraction(value: object) -> Fraction:
        if not isinstance(value, str) or re.fullmatch(r"[1-9][0-9]*/[1-9][0-9]*", value) is None:
            raise PronunciationAttemptMediaError(MediaErrorCode.CONTAINER_INVALID)
        try:
            fraction = Fraction(value)
        except (ValueError, ZeroDivisionError):
            raise PronunciationAttemptMediaError(
                MediaErrorCode.CONTAINER_INVALID
            ) from None
        if fraction <= 0:
            raise PronunciationAttemptMediaError(MediaErrorCode.CONTAINER_INVALID)
        return fraction

    @staticmethod
    def _strict_time_base(value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(r"[1-9][0-9]*/[1-9][0-9]*", value) is None:
            raise PronunciationAttemptMediaError(MediaErrorCode.CONTAINER_INVALID)
        return value

    @staticmethod
    def _seconds_to_units(
        value: object,
        *,
        units_per_second: int,
        error_code: MediaErrorCode,
    ) -> int:
        if not isinstance(value, str):
            raise PronunciationAttemptMediaError(error_code)
        try:
            seconds = Decimal(value)
        except InvalidOperation:
            raise PronunciationAttemptMediaError(error_code) from None
        if not seconds.is_finite():
            raise PronunciationAttemptMediaError(error_code)
        scaled = seconds * units_per_second
        if abs(scaled) > 9_223_372_036_854_775_807:
            raise PronunciationAttemptMediaError(error_code)
        return int(scaled.to_integral_value(rounding=ROUND_HALF_EVEN))

    @staticmethod
    def _decode_json_object(
        payload: bytes,
        *,
        invalid_code: MediaErrorCode = MediaErrorCode.CONTAINER_INVALID,
    ) -> Mapping[str, object]:
        try:
            decoded = json.loads(payload.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise PronunciationAttemptMediaError(invalid_code) from None
        if not isinstance(decoded, Mapping):
            raise PronunciationAttemptMediaError(invalid_code)
        return cast(Mapping[str, object], decoded)

    @classmethod
    def _run_bounded_capture(
        cls,
        *,
        command: Sequence[str],
        pass_fds: tuple[int, ...],
        timeout_seconds: float,
        maximum_output_bytes: int,
    ) -> _ProcessCapture:
        stdout = bytearray()
        stderr = bytearray()
        return_code = cls._stream_process_stdout(
            command=command,
            pass_fds=pass_fds,
            timeout_seconds=timeout_seconds,
            maximum_stdout_bytes=maximum_output_bytes,
            maximum_stderr_bytes=maximum_output_bytes,
            maximum_combined_bytes=maximum_output_bytes,
            stdout_limit_error=MediaErrorCode.TOOL_OUTPUT_LIMIT_EXCEEDED,
            consume_stdout=stdout.extend,
            consume_stderr=stderr.extend,
        )
        return _ProcessCapture(
            return_code=return_code,
            stdout=bytes(stdout),
            stderr=bytes(stderr),
        )

    @staticmethod
    def _stream_process_stdout(
        *,
        command: Sequence[str],
        pass_fds: tuple[int, ...],
        timeout_seconds: float,
        maximum_stdout_bytes: int,
        maximum_stderr_bytes: int,
        stdout_limit_error: MediaErrorCode,
        consume_stdout: Callable[[bytes], object],
        consume_stderr: Callable[[bytes], object] | None = None,
        maximum_combined_bytes: int | None = None,
    ) -> int:
        if not command:
            raise PronunciationAttemptMediaError(MediaErrorCode.TOOL_UNAVAILABLE)
        executable = shutil.which(command[0], path=_SUBPROCESS_PATH)
        if executable is None:
            raise PronunciationAttemptMediaError(MediaErrorCode.TOOL_UNAVAILABLE)
        target_command = [executable, *command[1:]]
        wrapped_command = isolated_command(
            launcher_python=sys.executable,
            launcher_path=str(_ISOLATION_LAUNCHER),
            target=target_command,
            cpu_seconds=max(1, math.ceil(timeout_seconds) + 2),
            address_space_bytes=_SUBPROCESS_ADDRESS_SPACE_BYTES,
        )
        environment = {
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": _SUBPROCESS_PATH,
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "VECLIB_MAXIMUM_THREADS": "1",
            "TF_NUM_INTRAOP_THREADS": "1",
            "TF_NUM_INTEROP_THREADS": "1",
        }
        try:
            process = subprocess.Popen(
                wrapped_command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                pass_fds=pass_fds,
                shell=False,
                env=environment,
                start_new_session=True,
            )
        except (OSError, ValueError):
            raise PronunciationAttemptMediaError(MediaErrorCode.TOOL_UNAVAILABLE) from None
        if process.stdout is None or process.stderr is None:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
            raise PronunciationAttemptMediaError(
                MediaErrorCode.TOOL_EXECUTION_FAILED
            )

        deadline = time.monotonic() + timeout_seconds
        selector = selectors.DefaultSelector()
        stdout_count = 0
        stderr_count = 0
        os.set_blocking(process.stdout.fileno(), False)
        os.set_blocking(process.stderr.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PronunciationAttemptMediaError(MediaErrorCode.TOOL_TIMEOUT)
                events = selector.select(timeout=min(remaining, 0.1))
                if not events and process.poll() is not None:
                    events = [
                        (key, selectors.EVENT_READ)
                        for key in tuple(selector.get_map().values())
                    ]
                for key, _ in events:
                    try:
                        payload = os.read(key.fd, _PROCESS_CHUNK_BYTES)
                    except BlockingIOError:
                        # A synthetic drain event after the session leader
                        # exits can race a descendant that still owns the
                        # pipe.  Return to the deadline instead of blocking.
                        continue
                    except OSError:
                        raise PronunciationAttemptMediaError(
                            MediaErrorCode.TOOL_EXECUTION_FAILED
                        ) from None
                    if not payload:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == "stdout":
                        stdout_count += len(payload)
                        if stdout_count > maximum_stdout_bytes:
                            raise PronunciationAttemptMediaError(stdout_limit_error)
                        consume_stdout(payload)
                    else:
                        stderr_count += len(payload)
                        if stderr_count > maximum_stderr_bytes:
                            raise PronunciationAttemptMediaError(
                                MediaErrorCode.TOOL_OUTPUT_LIMIT_EXCEEDED
                            )
                        if consume_stderr is not None:
                            consume_stderr(payload)
                    if (
                        maximum_combined_bytes is not None
                        and stdout_count + stderr_count > maximum_combined_bytes
                    ):
                        raise PronunciationAttemptMediaError(
                            MediaErrorCode.TOOL_OUTPUT_LIMIT_EXCEEDED
                        )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PronunciationAttemptMediaError(MediaErrorCode.TOOL_TIMEOUT)
            try:
                return process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                raise PronunciationAttemptMediaError(
                    MediaErrorCode.TOOL_TIMEOUT
                ) from None
        except BaseException:
            # The launcher/session leader can exit before a decoder descendant
            # releases inherited pipes.  Kill the group even when poll() says
            # the leader is gone, otherwise the timeout is not a hard bound.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                if process.poll() is None:
                    try:
                        process.kill()
                    except OSError:
                        pass
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
            raise
        finally:
            selector.close()
            process.stdout.close()
            process.stderr.close()


def _adapter_code_sha256() -> str:
    """Return the loaded adapter source pin without accepting caller input."""

    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:
        raise ValueError("FFmpeg adapter source code is unavailable") from None


__all__ = ["FfmpegAvContainerAdapter"]
