"""Network-isolated FFmpeg golden-vector verification for RGB colorimetry.

This adapter is intentionally separate from the v3 decoder.  It proves one
explicit limited-range BT.709 YUV420P-to-full-range RGB24 conversion contract;
it does not silently reinterpret source streams with missing color metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import fcntl
import math
import os
from pathlib import Path
import re
import shutil
import signal
import selectors
import subprocess
import sys
import time

from ...pronunciation_attempts.adapters.network_isolated_exec import (
    isolated_command,
)
from ...pronunciation_attempts.models import DecodedRgbFrame, Rgb24Sample
from ..models.integrity import sha256_document
from ..models.aihub538_photometric_calibration_v4 import (
    FFMPEG_RGB_COLORIMETRY_CONVERSION_DOCUMENT,
    FFMPEG_RGB_COLORIMETRY_GOLDEN_INPUT,
    FFMPEG_RGB_COLORIMETRY_GOLDEN_OUTPUT,
    FfmpegRgbColorimetryReceipt,
    FfmpegRgbSourceConversionReceiptV4,
    FfmpegSourceColorProbeReceiptV4,
)


_SUBPROCESS_PATH = "/usr/bin:/bin"
_ISOLATION_LAUNCHER = (
    Path(__file__).resolve().parents[2]
    / "pronunciation_attempts"
    / "adapters"
    / "network_isolated_exec.py"
)
_ISOLATION_LAUNCHER_SHA256 = (
    "d6b3f083756bbf0e4d223af991e88eab028481ae0bdb50be72fc0d94ab441b74"
)
_ADDRESS_SPACE_BYTES = 512 * 1024 * 1024
_MAXIMUM_TOOL_OUTPUT_BYTES = 64 * 1024
_MAXIMUM_RGB_FRAME_COUNT = 8
_MAXIMUM_RGB_PIXELS_PER_FRAME = 1920 * 1080
_MAXIMUM_RGB_OUTPUT_BYTES = _MAXIMUM_RGB_FRAME_COUNT * _MAXIMUM_RGB_PIXELS_PER_FRAME * 3

def _validate_rgb_geometry(width:int,height:int)->int:
    if isinstance(width,bool) or isinstance(height,bool) or not isinstance(width,int) or not isinstance(height,int) or width<=0 or height<=0 or width*height>_MAXIMUM_RGB_PIXELS_PER_FRAME:
        raise FfmpegRgbColorimetryVerificationError("ffmpeg_source_geometry_out_of_bounds")
    return width*height*3
_VERSION_TOKEN_PATTERN = re.compile(r"[^A-Za-z0-9._+-]+")
_ISSUED_COLOR_RECEIPTS: dict[str, list[FfmpegRgbColorimetryReceipt]] = {}
_ISSUED_PROBE_RECEIPTS: dict[str, list[FfmpegSourceColorProbeReceiptV4]] = {}
_ISSUED_CONVERSION_RECEIPTS: dict[str, list[FfmpegRgbSourceConversionReceiptV4]] = {}


def require_adapter_issued_color_lineage(
    color: FfmpegRgbColorimetryReceipt | None = None,
    probe: FfmpegSourceColorProbeReceiptV4 | None = None,
    conversion: FfmpegRgbSourceConversionReceiptV4 | None = None,
) -> None:
    if color is not None and not any(item is color for item in _ISSUED_COLOR_RECEIPTS.get(color.receipt_sha256,())):
        raise FfmpegRgbColorimetryVerificationError("ffmpeg_color_receipt_not_adapter_issued")
    if probe is not None and not any(item is probe for item in _ISSUED_PROBE_RECEIPTS.get(probe.receipt_sha256,())):
        raise FfmpegRgbColorimetryVerificationError("ffmpeg_probe_receipt_not_adapter_issued")
    if conversion is not None and not any(item is conversion for item in _ISSUED_CONVERSION_RECEIPTS.get(conversion.receipt_sha256,())):
        raise FfmpegRgbColorimetryVerificationError("ffmpeg_conversion_receipt_not_adapter_issued")


class FfmpegRgbColorimetryVerificationError(RuntimeError):
    """Stable failure that does not disclose a binary path or raw vector."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class _Capture:
    return_code: int
    stdout: bytes
    stderr: bytes


class FfmpegRgbColorimetryAdapter:
    """Verify exact color range/matrix/transfer handling before v4 measurement."""

    def __init__(
        self,
        *,
        ffmpeg_binary: str = "ffmpeg",
        timeout_seconds: float = 10.0,
    ) -> None:
        if (
            not isinstance(ffmpeg_binary, str)
            or not ffmpeg_binary
            or os.path.isabs(ffmpeg_binary)
            or "/" in ffmpeg_binary
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or not 0.0 < float(timeout_seconds) <= 30.0
        ):
            raise ValueError("ffmpeg colorimetry adapter configuration is invalid")
        resolved = shutil.which(ffmpeg_binary, path=_SUBPROCESS_PATH)
        if resolved is None:
            raise FfmpegRgbColorimetryVerificationError(
                "ffmpeg_colorimetry_tool_unavailable"
            )
        resolved_path = Path(resolved).resolve()
        if resolved_path.parent not in {Path("/usr/bin"), Path("/bin")}:
            raise FfmpegRgbColorimetryVerificationError(
                "ffmpeg_colorimetry_tool_path_not_allowed"
            )
        self._ffmpeg_binary = str(resolved_path)
        ffprobe = shutil.which("ffprobe", path=_SUBPROCESS_PATH)
        if ffprobe is None or Path(ffprobe).resolve().parent not in {Path("/usr/bin"), Path("/bin")}:
            raise FfmpegRgbColorimetryVerificationError("ffmpeg_colorimetry_tool_unavailable")
        self._ffprobe_binary = str(Path(ffprobe).resolve())
        self._timeout_seconds = float(timeout_seconds)

    def verify_conversion_contract(self) -> FfmpegRgbColorimetryReceipt:
        """Run all fixed goldens and issue a path-free, self-digested receipt."""

        self._verify_isolation_launcher()
        ffmpeg_revision = self._ffmpeg_revision()
        conversion_filter = FFMPEG_RGB_COLORIMETRY_CONVERSION_DOCUMENT[
            "conversion_filter"
        ]
        assert isinstance(conversion_filter, str)
        capture = self._run(
            [
                self._ffmpeg_binary,
                "-v",
                "error",
                "-nostdin",
                "-f",
                "rawvideo",
                "-pixel_format",
                "yuv420p",
                "-video_size",
                "2x2",
                "-framerate",
                "1",
                "-color_range",
                "tv",
                "-colorspace",
                "bt709",
                "-color_trc",
                "bt709",
                "-color_primaries",
                "bt709",
                "-i",
                "pipe:0",
                "-frames:v",
                "4",
                "-vf",
                conversion_filter,
                "-threads:v",
                "1",
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "pipe:1",
            ],
            stdin=FFMPEG_RGB_COLORIMETRY_GOLDEN_INPUT,
        )
        if (
            capture.return_code != 0
            or capture.stderr
            or capture.stdout != FFMPEG_RGB_COLORIMETRY_GOLDEN_OUTPUT
        ):
            raise FfmpegRgbColorimetryVerificationError(
                "ffmpeg_colorimetry_golden_vector_mismatch"
            )
        try:
            adapter_code_sha256 = hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest()
        except OSError:
            raise FfmpegRgbColorimetryVerificationError(
                "ffmpeg_colorimetry_adapter_identity_unavailable"
            ) from None
        receipt = FfmpegRgbColorimetryReceipt.issue(
            ffmpeg_revision=ffmpeg_revision,
            adapter_code_sha256=adapter_code_sha256,
            observed_output_sha256=hashlib.sha256(capture.stdout).hexdigest(),
            observed_output_byte_count=len(capture.stdout),
        )
        _ISSUED_COLOR_RECEIPTS.setdefault(receipt.receipt_sha256,[]).append(receipt)
        return receipt

    @staticmethod
    def _verify_isolation_launcher() -> None:
        try:
            observed = hashlib.sha256(_ISOLATION_LAUNCHER.read_bytes()).hexdigest()
        except OSError:
            raise FfmpegRgbColorimetryVerificationError(
                "ffmpeg_colorimetry_isolation_launcher_unavailable"
            ) from None
        if observed != _ISOLATION_LAUNCHER_SHA256:
            raise FfmpegRgbColorimetryVerificationError(
                "ffmpeg_colorimetry_isolation_launcher_identity_mismatch"
            )

    def _ffmpeg_revision(self) -> str:
        capture = self._run([self._ffmpeg_binary, "-version"], stdin=b"")
        if capture.return_code != 0:
            raise FfmpegRgbColorimetryVerificationError(
                "ffmpeg_colorimetry_tool_revision_unavailable"
            )
        try:
            first_line = capture.stdout.decode(
                "utf-8", errors="strict"
            ).splitlines()[0]
            words = first_line.split()
        except (UnicodeDecodeError, IndexError):
            raise FfmpegRgbColorimetryVerificationError(
                "ffmpeg_colorimetry_tool_revision_unavailable"
            ) from None
        if len(words) < 3 or words[0] != "ffmpeg" or words[1] != "version":
            raise FfmpegRgbColorimetryVerificationError(
                "ffmpeg_colorimetry_tool_revision_unavailable"
            )
        version_token = _VERSION_TOKEN_PATTERN.sub("_", words[2]).strip("_")[:48]
        if not version_token:
            raise FfmpegRgbColorimetryVerificationError(
                "ffmpeg_colorimetry_tool_revision_unavailable"
            )
        revision_digest = hashlib.sha256(
            capture.stdout + b"\0" + capture.stderr
        ).hexdigest()
        return f"ffmpeg:{version_token}:{revision_digest}"

    def probe_and_convert_fd(self, *, video_fd: int) -> tuple[FfmpegRgbColorimetryReceipt, FfmpegSourceColorProbeReceiptV4, FfmpegRgbSourceConversionReceiptV4, Rgb24Sample]:
        """Probe and explicitly convert one frame from an inherited read-only FD."""
        if isinstance(video_fd, bool) or not isinstance(video_fd, int) or video_fd < 0:
            raise ValueError("video_fd_invalid")
        self._verify_isolation_launcher()
        try:
            stat = os.fstat(video_fd)
            if (
                fcntl.fcntl(video_fd, fcntl.F_GETFL) & os.O_ACCMODE
                != os.O_RDONLY
                or not stat.st_size
                or os.lseek(video_fd, 0, os.SEEK_SET) != 0
            ):
                raise OSError
            container_sha = hashlib.sha256()
            offset = 0
            while offset < stat.st_size:
                chunk = os.pread(video_fd, min(1024 * 1024, stat.st_size - offset), offset)
                if not chunk:
                    raise OSError
                container_sha.update(chunk); offset += len(chunk)
        except OSError:
            raise FfmpegRgbColorimetryVerificationError("ffmpeg_source_fd_unreadable") from None
        locator = f"/proc/self/fd/{video_fd}"
        probe_capture = self._run([
            self._ffprobe_binary, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=index,width,height,pix_fmt,color_range,color_space,color_transfer,color_primaries",
            "-of", "json", locator,
        ], stdin=b"", pass_fds=(video_fd,))
        if probe_capture.return_code != 0 or probe_capture.stderr:
            raise FfmpegRgbColorimetryVerificationError("ffmpeg_source_probe_invalid")
        try:
            streams = json.loads(probe_capture.stdout)["streams"]
            stream = streams[0]
            width, height = int(stream["width"]), int(stream["height"])
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            raise FfmpegRgbColorimetryVerificationError("ffmpeg_source_probe_invalid") from None
        expected_rgb_bytes=_validate_rgb_geometry(width,height)
        probe = FfmpegSourceColorProbeReceiptV4.issue(
            container_sha256=container_sha.hexdigest(), video_stream_index=int(stream["index"]),
            ffprobe_revision=f"ffprobe:json:{hashlib.sha256(probe_capture.stdout).hexdigest()}",
            probe_adapter_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            pixel_format=stream.get("pix_fmt"), color_range=stream.get("color_range"),
            color_matrix=stream.get("color_space"), color_transfer=stream.get("color_transfer"),
            color_primaries=stream.get("color_primaries"),
            metadata_stdout_sha256=hashlib.sha256(probe_capture.stdout).hexdigest(),
            stderr_sha256=hashlib.sha256(probe_capture.stderr).hexdigest(),
        )
        ffmpeg_revision = self._ffmpeg_revision()
        capture = self._run([
            self._ffmpeg_binary, "-v", "error", "-nostdin", "-i", locator,
            "-map", "0:v:0", "-frames:v", "1", "-vf",
            str(FFMPEG_RGB_COLORIMETRY_CONVERSION_DOCUMENT["conversion_filter"]),
            "-threads:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
        ], stdin=b"", pass_fds=(video_fd,), maximum_output_bytes=expected_rgb_bytes + _MAXIMUM_TOOL_OUTPUT_BYTES)
        if capture.return_code != 0 or capture.stderr or len(capture.stdout) != expected_rgb_bytes:
            raise FfmpegRgbColorimetryVerificationError("ffmpeg_source_conversion_failed")
        sample = Rgb24Sample(
            asset_handle_id=hashlib.sha256((container_sha.hexdigest()+":v0").encode()).hexdigest(),
            container_sha256=container_sha.hexdigest(), video_stream_index=int(stream["index"]),
            frames=(DecodedRgbFrame(0, width, height, capture.stdout),), ffmpeg_revision=ffmpeg_revision,
        )
        decoded_digest = sha256_document({
            "schema_version":"voice_coach.decoded_rgb_sample_evidence.v1",
            "asset_handle_id":sample.asset_handle_id,"container_sha256":sample.container_sha256,
            "video_stream_index":sample.video_stream_index,"ffmpeg_revision":sample.ffmpeg_revision,
            "frames":[{"timestamp_us":0,"width":width,"height":height,"rgb24_sha256":hashlib.sha256(capture.stdout).hexdigest()}],
        })
        color_receipt = self.verify_conversion_contract()
        conversion = FfmpegRgbSourceConversionReceiptV4.issue(
            source_probe_receipt=probe, adapter_colorimetry_receipt=color_receipt,
            decoded_rgb_evidence_sha256=decoded_digest, ffmpeg_revision=ffmpeg_revision,
        )
        _ISSUED_PROBE_RECEIPTS.setdefault(probe.receipt_sha256,[]).append(probe)
        _ISSUED_CONVERSION_RECEIPTS.setdefault(conversion.receipt_sha256,[]).append(conversion)
        return color_receipt, probe, conversion, sample

    def issue_sample_lineage(
        self, *, sample: Rgb24Sample, metadata_stdout: bytes,
        metadata_stderr: bytes, color_receipt: FfmpegRgbColorimetryReceipt,
    ) -> tuple[FfmpegSourceColorProbeReceiptV4,FfmpegRgbSourceConversionReceiptV4]:
        """Bind an already decoded multi-frame sample to this adapter's exact probe."""
        require_adapter_issued_color_lineage(color=color_receipt)
        try:
            document=json.loads(metadata_stdout); stream=document["streams"][0]
            width,height=int(stream["width"]),int(stream["height"])
            if metadata_stderr or not sample.frames or any(frame.width!=width or frame.height!=height for frame in sample.frames): raise ValueError
            probe=FfmpegSourceColorProbeReceiptV4.issue(
                container_sha256=sample.container_sha256,video_stream_index=int(stream["index"]),
                ffprobe_revision=f"ffprobe:json:{hashlib.sha256(metadata_stdout).hexdigest()}",
                probe_adapter_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                pixel_format=stream.get("pix_fmt"),color_range=stream.get("color_range"),
                color_matrix=stream.get("color_space"),color_transfer=stream.get("color_transfer"),
                color_primaries=stream.get("color_primaries"),metadata_stdout_sha256=hashlib.sha256(metadata_stdout).hexdigest(),
                stderr_sha256=hashlib.sha256(metadata_stderr).hexdigest())
            decoded=sha256_document({"schema_version":"voice_coach.decoded_rgb_sample_evidence.v1","asset_handle_id":sample.asset_handle_id,"container_sha256":sample.container_sha256,"video_stream_index":sample.video_stream_index,"ffmpeg_revision":sample.ffmpeg_revision,"frames":[{"timestamp_us":frame.timestamp_us,"width":frame.width,"height":frame.height,"rgb24_sha256":hashlib.sha256(frame.rgb24).hexdigest()} for frame in sample.frames]})
            conversion=FfmpegRgbSourceConversionReceiptV4.issue(source_probe_receipt=probe,adapter_colorimetry_receipt=color_receipt,decoded_rgb_evidence_sha256=decoded,ffmpeg_revision=sample.ffmpeg_revision)
        except (KeyError,IndexError,TypeError,ValueError,json.JSONDecodeError):
            raise FfmpegRgbColorimetryVerificationError("ffmpeg_sample_lineage_invalid") from None
        _ISSUED_PROBE_RECEIPTS.setdefault(probe.receipt_sha256,[]).append(probe); _ISSUED_CONVERSION_RECEIPTS.setdefault(conversion.receipt_sha256,[]).append(conversion)
        return probe,conversion

    def _run(self, command: list[str], *, stdin: bytes, pass_fds: tuple[int, ...] = (), maximum_output_bytes: int = _MAXIMUM_TOOL_OUTPUT_BYTES) -> _Capture:
        wrapped = isolated_command(
            launcher_python=sys.executable,
            launcher_path=str(_ISOLATION_LAUNCHER),
            target=command,
            cpu_seconds=max(1, math.ceil(self._timeout_seconds) + 2),
            address_space_bytes=_ADDRESS_SPACE_BYTES,
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
                wrapped,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                shell=False,
                env=environment,
                start_new_session=True,
                pass_fds=pass_fds,
            )
        except (OSError, ValueError):
            raise FfmpegRgbColorimetryVerificationError(
                "ffmpeg_colorimetry_tool_execution_failed"
            ) from None
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        try:
            process.stdin.write(stdin); process.stdin.close()
            selector=selectors.DefaultSelector(); selector.register(process.stdout,selectors.EVENT_READ,"stdout"); selector.register(process.stderr,selectors.EVENT_READ,"stderr")
            chunks={"stdout":bytearray(),"stderr":bytearray()}; deadline=time.monotonic()+self._timeout_seconds
            while selector.get_map():
                remaining=deadline-time.monotonic()
                if remaining<=0: raise subprocess.TimeoutExpired(command,self._timeout_seconds)
                for key,_ in selector.select(remaining):
                    chunk=os.read(key.fileobj.fileno(),65536)
                    if not chunk: selector.unregister(key.fileobj); continue
                    chunks[key.data].extend(chunk)
                    if len(chunks["stdout"])+len(chunks["stderr"])>maximum_output_bytes:
                        raise OverflowError
            process.wait(timeout=max(0.01,deadline-time.monotonic())); stdout=bytes(chunks["stdout"]); stderr=bytes(chunks["stderr"])
        except subprocess.TimeoutExpired:
            self._kill_process_group(process); raise FfmpegRgbColorimetryVerificationError("ffmpeg_colorimetry_tool_timeout") from None
        except OverflowError:
            self._kill_process_group(process); raise FfmpegRgbColorimetryVerificationError("ffmpeg_colorimetry_tool_output_limit_exceeded") from None
        except (OSError,ValueError):
            self._kill_process_group(process); raise FfmpegRgbColorimetryVerificationError("ffmpeg_colorimetry_tool_execution_failed") from None
        return _Capture(
            return_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
        )

    @staticmethod
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
        except (OSError, subprocess.TimeoutExpired):
            pass


__all__ = [
    "FfmpegRgbColorimetryAdapter",
    "FfmpegRgbColorimetryVerificationError",
    "require_adapter_issued_color_lineage",
]
