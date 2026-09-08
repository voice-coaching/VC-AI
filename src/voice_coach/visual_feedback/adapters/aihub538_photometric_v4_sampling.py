"""Deterministic eight-frame RGB sampler for actual P6 v4 measurement."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path

from ...pronunciation_attempts.models import DecodedRgbFrame, Rgb24Sample
from ..models.aihub538_photometric_calibration_v4 import (
    FFMPEG_RGB_COLORIMETRY_CONVERSION_DOCUMENT,
    FfmpegRgbColorimetryReceipt,FfmpegRgbSourceConversionReceiptV4,FfmpegSourceColorProbeReceiptV4,
)
from ..models.aihub538_photometric_v4_production_policy import (
    Aihub538PhotometricV4ProductionExecutionPolicy,
)
from ..models.integrity import sha256_document
from .aihub538_ffmpeg_colorimetry import (
    FfmpegRgbColorimetryAdapter,
    FfmpegRgbColorimetryVerificationError,
    require_adapter_issued_color_lineage,
)


class Aihub538PhotometricV4SamplingError(RuntimeError):
    pass


def aihub538_v4_interior_timestamps_us(
    duration_us: int,
    *,
    frame_count: int = 8,
) -> tuple[int, ...]:
    """Return midpoint-bin timestamps using exact positive half-up rounding."""

    if (
        type(duration_us) is not int
        or duration_us <= 0
        or duration_us > 25_000_000
        or type(frame_count) is not int
        or frame_count != 8
    ):
        raise ValueError("aihub538_v4_timestamp_policy_invalid")
    denominator = 2 * frame_count
    timestamps = tuple(
        ((2 * index + 1) * duration_us + denominator // 2) // denominator
        for index in range(frame_count)
    )
    if (
        len(set(timestamps)) != frame_count
        or timestamps != tuple(sorted(timestamps))
        or timestamps[0] <= 0
        or timestamps[-1] >= duration_us
    ):
        raise ValueError("aihub538_v4_eight_usable_timestamps_unavailable")
    return timestamps


@dataclass(frozen=True, slots=True)
class Aihub538PhotometricV4FrameReceipt:
    timestamp_us: int
    rgb24_sha256: str
    ffmpeg_argv_sha256: str
    colorimetry_receipt_sha256: str

    def __post_init__(self) -> None:
        if type(self.timestamp_us) is not int or self.timestamp_us <= 0:
            raise ValueError("aihub538_v4_frame_receipt_invalid")
        if any(not _is_sha256(value) for value in (
            self.rgb24_sha256, self.ffmpeg_argv_sha256,
            self.colorimetry_receipt_sha256,
        )):
            raise ValueError("aihub538_v4_frame_receipt_invalid")

    def to_json_object(self) -> dict[str, object]:
        return {
            "timestamp_us": self.timestamp_us,
            "rgb24_sha256": self.rgb24_sha256,
            "ffmpeg_argv_sha256": self.ffmpeg_argv_sha256,
            "colorimetry_receipt_sha256": self.colorimetry_receipt_sha256,
        }


@dataclass(frozen=True, slots=True)
class Aihub538PhotometricV4SamplingReceipt:
    production_execution_policy_sha256: str
    container_sha256: str
    width: int
    height: int
    duration_us: int
    frame_receipts: tuple[Aihub538PhotometricV4FrameReceipt, ...]
    adapter_code_sha256: str
    subprocess_environment_sha256: str
    receipt_sha256: str

    def __post_init__(self) -> None:
        if (
            not _is_sha256(self.production_execution_policy_sha256)
            or not _is_sha256(self.container_sha256)
            or not _is_sha256(self.adapter_code_sha256)
            or self.subprocess_environment_sha256 != _NATIVE_ENVIRONMENT_SHA256
            or not _is_sha256(self.receipt_sha256)
            or type(self.width) is not int or type(self.height) is not int
            or self.width <= 0 or self.height <= 0
            or type(self.duration_us) is not int
            or len(self.frame_receipts) != 8
            or tuple(item.timestamp_us for item in self.frame_receipts)
            != aihub538_v4_interior_timestamps_us(self.duration_us)
        ):
            raise ValueError("aihub538_v4_sampling_receipt_invalid")
        if self.receipt_sha256 != "0" * 64 and self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise ValueError("aihub538_v4_sampling_receipt_digest_invalid")

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": "voice_coach.aihub538_photometric_v4_rgb_sampling.v1",
            "production_execution_policy_sha256": self.production_execution_policy_sha256,
            "container_sha256": self.container_sha256,
            "width": self.width,
            "height": self.height,
            "duration_us": self.duration_us,
            "frame_count": len(self.frame_receipts),
            "frame_receipts": [item.to_json_object() for item in self.frame_receipts],
            "timestamp_rule": "eight_equal_bins_interior_midpoint_integer_us_half_up",
            "adapter_code_sha256": self.adapter_code_sha256,
            "subprocess_environment_sha256": self.subprocess_environment_sha256,
            "native_thread_limit": 1,
            "worker_processes": 1,
            "raw_frames_serialized": False,
            "source_paths_serialized": False,
            "locked_partition_accessed": False,
            "promotion_authorized": False,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def strict_from_json_object(cls, value: object) -> "Aihub538PhotometricV4SamplingReceipt":
        try:
            if not isinstance(value, dict):
                raise ValueError
            frames = value["frame_receipts"]
            if not isinstance(frames, list):
                raise ValueError
            receipt = cls(
                production_execution_policy_sha256=value["production_execution_policy_sha256"],
                container_sha256=value["container_sha256"], width=value["width"],
                height=value["height"], duration_us=value["duration_us"],
                frame_receipts=tuple(Aihub538PhotometricV4FrameReceipt(**item) for item in frames),
                adapter_code_sha256=value["adapter_code_sha256"],
                subprocess_environment_sha256=value["subprocess_environment_sha256"],
                receipt_sha256=value["receipt_sha256"],
            )
            if value != receipt.to_json_object():
                raise ValueError
            return receipt
        except Exception:
            raise ValueError("aihub538_v4_sampling_receipt_reload_failed") from None

@dataclass(frozen=True,slots=True,eq=False)
class Aihub538PhotometricV4SampleBundle:
    sample: Rgb24Sample
    sampling_receipt: Aihub538PhotometricV4SamplingReceipt
    colorimetry_receipt: FfmpegRgbColorimetryReceipt
    source_probe_receipt: FfmpegSourceColorProbeReceiptV4
    source_conversion_receipt: FfmpegRgbSourceConversionReceiptV4
    def __iter__(self):
        # Compatibility for callers which previously unpacked sample, receipt.
        yield self.sample; yield self.sampling_receipt


class Aihub538PhotometricV4EightFrameSampler:
    """Decode exactly eight policy-bound frames from one read-only video FD."""

    def __init__(
        self,
        *,
        policy: Aihub538PhotometricV4ProductionExecutionPolicy,
        color_adapter: FfmpegRgbColorimetryAdapter | None = None,
    ) -> None:
        if (
            type(policy) is not Aihub538PhotometricV4ProductionExecutionPolicy
            or policy is not Aihub538PhotometricV4ProductionExecutionPolicy.frozen()
            or (
                color_adapter is not None
                and type(color_adapter) is not FfmpegRgbColorimetryAdapter
            )
        ):
            raise ValueError("aihub538_v4_frozen_execution_policy_required")
        policy.__post_init__()
        self._policy = policy
        self._color = color_adapter or FfmpegRgbColorimetryAdapter(
            timeout_seconds=float(policy.document["colorimetry"]["subprocess_timeout_seconds"])
        )

    def sample(self, *, video_fd: int) -> Aihub538PhotometricV4SampleBundle:
        try:
            container_sha256 = _sha256_fd(video_fd)
            locator = f"/proc/self/fd/{video_fd}"
            probe_argv = [
                self._color._ffprobe_binary,
                "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=index,width,height,pix_fmt,color_range,color_space,color_transfer,color_primaries,avg_frame_rate,r_frame_rate,nb_frames:format=duration",
                "-of", "json", locator,
            ]
            probe = self._color._run(probe_argv, stdin=b"", pass_fds=(video_fd,))
            document = json.loads(probe.stdout)
            stream = document["streams"][0]
            width, height = int(stream["width"]), int(stream["height"])
            duration_value = Decimal(document["format"]["duration"])
            duration_us = int((duration_value * 1_000_000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            fps = _strict_frame_rate(stream)
            frame_count = _strict_positive_int(stream["nb_frames"])
            container = self._policy.document["container"]
            if (
                probe.return_code != 0
                or probe.stderr
                or stream.get("pix_fmt") != "yuv420p"
                or stream.get("color_range") != "tv"
                or stream.get("color_space") != "bt709"
                or stream.get("color_transfer") != "bt709"
                or stream.get("color_primaries") != "bt709"
                or not container["minimum_width"] <= width <= container["maximum_width"]
                or not container["minimum_height"] <= height <= container["maximum_height"]
                or width * height > container["maximum_pixels_per_frame"]
                or not Fraction(15, 1) <= fps <= Fraction(60, 1)
                or not container["minimum_frame_count"] <= frame_count <= container["maximum_frame_count"]
                or not container["minimum_duration_ms"] * 1000
                <= duration_us
                <= container["maximum_duration_ms"] * 1000
            ):
                raise ValueError
            timestamps = aihub538_v4_interior_timestamps_us(duration_us)
            expected_bytes = width * height * 3
            color_receipt = self._color.verify_conversion_contract()
            require_adapter_issued_color_lineage(color=color_receipt)
            frames: list[DecodedRgbFrame] = []
            frame_receipts: list[Aihub538PhotometricV4FrameReceipt] = []
            for timestamp_us in timestamps:
                argv = [
                    self._color._ffmpeg_binary, "-v", "error", "-nostdin",
                    "-ss", f"{timestamp_us / 1_000_000:.6f}",
                    # This option must precede the input.  The later option
                    # constrains the rawvideo encoder but does not constrain
                    # H.264 decoder threads; at 1080p that auto-threaded
                    # decoder exceeds the frozen 512 MiB address-space gate.
                    "-threads:v", "1", "-i", locator,
                    "-map", "0:v:0", "-frames:v", "1", "-vf",
                    str(FFMPEG_RGB_COLORIMETRY_CONVERSION_DOCUMENT["conversion_filter"]),
                    "-threads:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
                ]
                capture = self._color._run(
                    argv,
                    stdin=b"",
                    pass_fds=(video_fd,),
                    maximum_output_bytes=expected_bytes + 65_536,
                )
                if capture.return_code != 0 or capture.stderr or len(capture.stdout) != expected_bytes:
                    raise ValueError
                frames.append(DecodedRgbFrame(timestamp_us, width, height, capture.stdout))
                frame_receipts.append(
                    Aihub538PhotometricV4FrameReceipt(
                        timestamp_us=timestamp_us,
                        rgb24_sha256=hashlib.sha256(capture.stdout).hexdigest(),
                        ffmpeg_argv_sha256=sha256_document({"argv": argv}),
                        colorimetry_receipt_sha256=color_receipt.receipt_sha256,
                    )
                )
            if sum(len(frame.rgb24) for frame in frames) > 67_108_864:
                raise ValueError
            sample = Rgb24Sample(
                asset_handle_id=hashlib.sha256((container_sha256 + ":p6-v4-eight").encode()).hexdigest(),
                container_sha256=container_sha256,
                video_stream_index=int(stream["index"]),
                frames=tuple(frames),
                ffmpeg_revision=self._color._ffmpeg_revision(),
            )
            adapter_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            draft = Aihub538PhotometricV4SamplingReceipt(
                self._policy.policy_sha256, container_sha256, width, height,
                duration_us, tuple(frame_receipts), adapter_sha,
                _NATIVE_ENVIRONMENT_SHA256, "0" * 64,
            )
            receipt = Aihub538PhotometricV4SamplingReceipt(
                self._policy.policy_sha256, container_sha256, width, height,
                duration_us, tuple(frame_receipts), adapter_sha, _NATIVE_ENVIRONMENT_SHA256,
                sha256_document(draft.unsigned_json_object()),
            )
            probe_receipt,conversion_receipt=self._color.issue_sample_lineage(sample=sample,metadata_stdout=probe.stdout,metadata_stderr=probe.stderr,color_receipt=color_receipt)
            bundle=Aihub538PhotometricV4SampleBundle(sample,receipt,color_receipt,probe_receipt,conversion_receipt)
            _ISSUED_SAMPLES[id(receipt)] = (sample, receipt, self._policy, color_receipt, bundle)
            return bundle
        except (FfmpegRgbColorimetryVerificationError, InvalidOperation, KeyError, IndexError, TypeError, ValueError, OSError, json.JSONDecodeError):
            raise Aihub538PhotometricV4SamplingError(
                "aihub538_photometric_v4_eight_frame_sampling_failed"
            ) from None


def require_aihub538_photometric_v4_eight_frame_sample(
    sample: Rgb24Sample,
    *,
    receipt: Aihub538PhotometricV4SamplingReceipt,
    policy: Aihub538PhotometricV4ProductionExecutionPolicy,
) -> Rgb24Sample:
    """Typed integration hook used immediately before landmark and QC calls."""

    issued = _ISSUED_SAMPLES.pop(id(receipt), None)
    if (
        type(policy) is not Aihub538PhotometricV4ProductionExecutionPolicy
        or policy is not Aihub538PhotometricV4ProductionExecutionPolicy.frozen()
        or issued is None
        or issued[:3] != (sample, receipt, policy)
        or len(sample.frames) != 8
        or tuple(frame.timestamp_us for frame in sample.frames)
        != tuple(sorted({frame.timestamp_us for frame in sample.frames}))
        or sum(len(frame.rgb24) for frame in sample.frames)
        > policy.document["video_qc"]["maximum_total_rgb_bytes"]
        or sample.container_sha256 != receipt.container_sha256
        or tuple(frame.timestamp_us for frame in sample.frames)
        != tuple(item.timestamp_us for item in receipt.frame_receipts)
        or any(
            frame.width != receipt.width
            or frame.height != receipt.height
            or hashlib.sha256(frame.rgb24).hexdigest() != item.rgb24_sha256
            for frame, item in zip(sample.frames, receipt.frame_receipts, strict=True)
        )
    ):
        raise Aihub538PhotometricV4SamplingError(
            "aihub538_photometric_v4_eight_frame_sample_required"
        )
    _VALIDATED_BUNDLES[id(issued[4])]=issued[4]
    return sample

def consume_aihub538_photometric_v4_sample_bundle(bundle:Aihub538PhotometricV4SampleBundle)->None:
    if type(bundle) is not Aihub538PhotometricV4SampleBundle or _VALIDATED_BUNDLES.pop(id(bundle),None) is not bundle:
        raise Aihub538PhotometricV4SamplingError("aihub538_photometric_v4_sample_bundle_invalid")


def _sha256_fd(descriptor: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while True:
        chunk = os.pread(descriptor, 1024 * 1024, offset)
        if not chunk:
            return digest.hexdigest()
        digest.update(chunk)
        offset += len(chunk)


def _strict_positive_int(value: object) -> int:
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        raise ValueError
    parsed = int(value)
    if parsed <= 0:
        raise ValueError
    return parsed


def _strict_frame_rate(stream: dict[str, object]) -> Fraction:
    values = []
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = stream[key]
        if not isinstance(raw, str) or raw.count("/") != 1:
            raise ValueError
        numerator, denominator = raw.split("/")
        if not numerator.isascii() or not numerator.isdigit() or not denominator.isascii() or not denominator.isdigit():
            raise ValueError
        if int(denominator) == 0:
            raise ValueError
        values.append(Fraction(int(numerator), int(denominator)))
    if values[0] != values[1]:
        raise ValueError
    return values[0]


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


_ISSUED_SAMPLES: dict[int, tuple[object, ...]] = {}
_VALIDATED_BUNDLES: dict[int,Aihub538PhotometricV4SampleBundle] = {}
_NATIVE_ENVIRONMENT = {
    "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
    "TF_NUM_INTEROP_THREADS": "1", "TF_NUM_INTRAOP_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
}
_NATIVE_ENVIRONMENT_SHA256 = sha256_document(_NATIVE_ENVIRONMENT)


__all__ = [
    "Aihub538PhotometricV4EightFrameSampler",
    "Aihub538PhotometricV4FrameReceipt",
    "Aihub538PhotometricV4SamplingError",
    "Aihub538PhotometricV4SamplingReceipt",
    "Aihub538PhotometricV4SampleBundle",
    "aihub538_v4_interior_timestamps_us",
    "require_aihub538_photometric_v4_eight_frame_sample",
    "consume_aihub538_photometric_v4_sample_bundle",
]
