"""Cancellable, network-isolated ffprobe/ffmpeg/MediaPipe feature port.

Only verified local MP4s enter this adapter. RGB is streamed, never accumulated
or archived. Each child is isolated in its own process group and killed/reaped
on success, failure and cancellation before its input file is released.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import struct
import sys

from ..feature_protocol import (MAX_FRAMES, MAX_DURATION_SECONDS, MAX_OUTPUT_BYTES,
    MAX_HEADER_BYTES, PROTOCOL, validate_features, validate_header, strict_json as _strict_json)
from ..parallel_analysis import MediaIntegrityFailure, VisualObservationUnavailable
from ...pronunciation_attempts.adapters.network_isolated_exec import isolated_command


@dataclass(frozen=True)
class VideoFeatureConfiguration:
    python: Path
    model: Path
    model_sha256: str
    quality: dict
    ffprobe: Path = Path("/usr/bin/ffprobe")
    ffmpeg: Path = Path("/usr/bin/ffmpeg")


def check_feature_runtime(python: Path, model: Path, model_sha256: str):
    """Check the selected backend's dependencies without images or inference."""
    import subprocess
    from .yunet_landmarks import read_bundle
    from ...pronunciation_attempts.adapters.mediapipe_landmark_worker import _read_model_bytes
    from ...backend_analysis.canonical_process import check_guard_python
    check_guard_python(python)
    bundle = read_bundle(_read_model_bytes(str(model), model_sha256))
    if bundle is None:
        code = ('import importlib.util; assert importlib.util.find_spec("mediapipe"); '
                'assert importlib.util.find_spec("cv2")')
    else:
        code = ('import importlib.metadata as m; '
                'assert m.version("ai-edge-litert")=="2.3.0"; '
                'assert m.version("opencv-python-headless")=="4.12.0.88"; '
                'assert m.version("numpy")=="2.2.6"')
    result = subprocess.run([str(python), '-I', '-B', '-c', code],
        env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'},
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
    if result.returncode:
        raise ValueError('VISUAL_RUNTIME_UNAVAILABLE')


async def _bounded(stream, limit):
    result = bytearray()
    while chunk := await stream.read(min(65536, limit + 1 - len(result))):
        result.extend(chunk)
        if len(result) > limit:
            raise ValueError("VISUAL_OUTPUT_LIMIT")
    return bytes(result)


async def _reap(processes, tasks=()):
    # Even a leader that exited may have left its own group alive. The launcher
    # prohibits fork/setpgid, so only native threads are possible, but kill the
    # group as well rather than relying on that implementation detail.
    for process in processes:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    # Cancelled readers must release their StreamReader before we drain it.
    # Process.wait() can otherwise hang after SIGKILL when a full pipe has
    # paused its transport, so drain discarded output before waiting/reaping.
    await asyncio.gather(*tasks, return_exceptions=True)
    async def discard(stream):
        if stream is not None:
            while await stream.read(65536):
                pass
    await asyncio.gather(*(discard(stream) for p in processes for stream in (p.stdout, p.stderr)))
    await asyncio.gather(*(p.wait() for p in processes))


async def _drain_cleanup(processes, tasks):
    for task in tasks:
        if not task.done():
            task.cancel()
    async def cleanup():
        await _reap(processes, tasks)
    task = asyncio.create_task(cleanup())
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError


class CanonicalVideoFeatures:
    def __init__(self, configuration: VideoFeatureConfiguration):
        self.configuration = configuration

    async def _spawn(self, command, *, descriptor):
        launcher = Path(__file__).parents[2] / "pronunciation_attempts/adapters/network_isolated_exec.py"
        command = isolated_command(launcher_python=sys.executable, launcher_path=str(launcher),
            target=list(map(str, command)), cpu_seconds=180, address_space_bytes=4 * 1024**3)
        spawn = asyncio.create_task(asyncio.create_subprocess_exec(*command, pass_fds=(descriptor,), start_new_session=True,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                 "PYTHONDONTWRITEBYTECODE": "1", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                 "MKL_NUM_THREADS": "1", "TF_NUM_INTRAOP_THREADS": "1", "TF_NUM_INTEROP_THREADS": "1"}))
        try:
            return await asyncio.shield(spawn)
        except asyncio.CancelledError:
            # Cancellation during creation must not strand a process whose PID
            # has not yet been returned to the caller's cleanup list.
            while not spawn.done():
                try:
                    await asyncio.shield(spawn)
                except asyncio.CancelledError:
                    continue
            if not spawn.cancelled() and spawn.exception() is None:
                await _drain_cleanup([spawn.result()], [])
            raise

    async def _capture(self, command, *, descriptor, limit):
        process = await self._spawn(command, descriptor=descriptor)
        process.stdin.close()
        task = asyncio.create_task(_bounded(process.stdout, limit))
        try:
            raw = await task
            if await process.wait() != 0:
                raise ValueError("VISUAL_DECODER_FAILED")
            return raw
        finally:
            await _drain_cleanup([process], [task])

    async def extract(self, *, video_path: Path, video_sha256: str, byte_count: int):
        c = self.configuration
        if sys.platform != "linux" or any(not p.is_absolute() for p in (c.python, c.ffmpeg, c.ffprobe, c.model)):
            raise ValueError("VISUAL_RUNTIME_UNAVAILABLE")
        descriptor = os.open(video_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            with os.fdopen(os.dup(descriptor), "rb") as source:
                before = os.fstat(descriptor)
                if (not stat.S_ISREG(before.st_mode) or before.st_size != byte_count
                        or not 0 < byte_count <= 256 * 1024**2
                        or hashlib.file_digest(source, "sha256").hexdigest() != video_sha256):
                    raise MediaIntegrityFailure("VIDEO_IDENTITY_MISMATCH")
            locator = f"/proc/self/fd/{descriptor}"
            raw = await self._capture([c.ffprobe, "-v", "error", "-protocol_whitelist", "file,pipe",
                "-show_streams", "-show_frames", "-show_entries",
                "stream=index,codec_type,codec_name,width,height,time_base,sample_rate:"
                "stream_tags=rotate:"
                "frame=stream_index,best_effort_timestamp,nb_samples", "-of", "json", locator],
                descriptor=descriptor, limit=8 * 1024**2)
            metadata = _strict_json(raw)
            streams = metadata.get("streams", [])
            videos = [s for s in streams if s.get("codec_type") == "video"]
            audios = [s for s in streams if s.get("codec_type") == "audio"]
            if len(videos) != 1 or len(audios) != 1 or len(streams) != 2:
                raise ValueError("VISUAL_STREAMS_UNSUPPORTED")
            v, a = videos[0], audios[0]
            if v.get("codec_name") not in {"h264", "hevc"} or a.get("codec_name") != "aac":
                raise ValueError("VISUAL_CODECS_UNSUPPORTED")
            rotations = [v.get("tags", {}).get("rotate", 0),
                         *(s.get("rotation", 0) for s in v.get("side_data_list", []))]
            if any(Fraction(str(r)) % 360 for r in rotations):
                # No implicit rotation: probe, RGB shape and landmark axes must agree.
                raise VisualObservationUnavailable("UNSUPPORTED_ORIENTATION")
            frames = metadata.get("frames", [])
            pts = [float(int(f["best_effort_timestamp"]) * Fraction(v["time_base"]))
                   for f in frames if f["stream_index"] == v["index"]]
            if not 1 <= len(pts) <= MAX_FRAMES:
                raise VisualObservationUnavailable("FRAME_LIMIT_EXCEEDED")
            header = validate_header(dict(protocol=PROTOCOL, width=v["width"], height=v["height"], pts=pts,
                modelPath=str(c.model), modelSha256=c.model_sha256, quality=c.quality))
            feature_result = await self._features(header, locator, descriptor)
            # Preserve uncertainty unless the decoded audio has a continuous
            # clock. Reproduce its PCM for a sample-exact equality check later;
            # this is verification, not a second audio analysis or STT call.
            clock = self._audio_clock(a, frames)
            pcm_hash, pcm_frames = None, None
            if clock is not None:
                pcm = await self._capture([c.ffmpeg, "-nostdin", "-v", "error", "-threads", "1",
                    "-protocol_whitelist", "file,pipe", "-i", locator, "-map", "0:a:0", "-vn",
                    "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1"],
                    descriptor=descriptor, limit=MAX_DURATION_SECONDS * 16000 * 2)
                if not pcm or len(pcm) % 2:
                    raise ValueError("VISUAL_PCM_INVALID")
                pcm_hash, pcm_frames = hashlib.sha256(pcm).hexdigest(), len(pcm) // 2
            after = os.fstat(descriptor)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise MediaIntegrityFailure("VIDEO_CHANGED_DURING_DECODE")
            return dict(videoSha256=video_sha256, modelSha256=c.model_sha256,
                features=feature_result, audioClock=clock, decodedPcmSha256=pcm_hash,
                decodedPcmFrames=pcm_frames, decodedPcmSampleRate=16000)
        finally:
            os.close(descriptor)

    @staticmethod
    def _audio_clock(stream, frames):
        rows = [f for f in frames if f.get("stream_index") == stream["index"]]
        try:
            base, rate = Fraction(stream["time_base"]), int(stream["sample_rate"])
            if not rows or not 8000 <= rate <= 192000 or base <= 0:
                return None
            start = int(rows[0]["best_effort_timestamp"]) * base
            end = start
            for row in rows:
                pts, count = int(row["best_effort_timestamp"]) * base, int(row["nb_samples"])
                if count <= 0 or abs(pts - end) > Fraction(1, rate):
                    return None
                end = pts + Fraction(count, rate)
            if not 0 < end - start <= MAX_DURATION_SECONDS or abs(start) > 86400:
                return None
            return dict(firstPtsNumerator=start.numerator, firstPtsDenominator=start.denominator,
                        source="CONTINUOUS_DECODED_AUDIO_FRAMES", sampleRate=rate)
        except (KeyError, ValueError, TypeError, ZeroDivisionError):
            return None

    async def _features(self, header, locator, descriptor):
        c = self.configuration
        raw_header = json.dumps(header, allow_nan=False, separators=(",", ":")).encode()
        if len(raw_header) > MAX_HEADER_BYTES:
            raise ValueError("VISUAL_HEADER_LIMIT")
        processes, tasks = [], []
        try:
            decoder = await self._spawn([c.ffmpeg, "-nostdin", "-v", "error", "-threads", "1",
                "-protocol_whitelist", "file,pipe", "-noautorotate", "-copyts", "-i", locator,
                "-map", "0:v:0", "-an", "-vsync", "0", "-threads", "1",
                "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"], descriptor=descriptor)
            processes.append(decoder)
            decoder.stdin.close()
            worker = await self._spawn([c.python, "-I", "-B", Path(__file__).with_name("canonical_feature_worker.py")],
                                       descriptor=descriptor)
            processes.append(worker)
            async def pump():
                worker.stdin.write(struct.pack("!I", len(raw_header)) + raw_header)
                await worker.stdin.drain()
                expected = header["width"] * header["height"] * 3 * len(header["pts"])
                count = 0
                while chunk := await decoder.stdout.read(65536):
                    count += len(chunk)
                    if count > expected:
                        raise ValueError("VISUAL_RGB_LENGTH")
                    worker.stdin.write(chunk)
                    await worker.stdin.drain()
                if count != expected:
                    raise ValueError("VISUAL_RGB_LENGTH")
                worker.stdin.close()
            tasks = [asyncio.create_task(pump()), asyncio.create_task(_bounded(worker.stdout, MAX_OUTPUT_BYTES))]
            _, raw = await asyncio.gather(*tasks)
            codes = await asyncio.gather(*(p.wait() for p in processes))
            if any(codes):
                raise ValueError("VISUAL_FEATURE_WORKER_FAILED")
            return validate_features(_strict_json(raw), header["pts"])
        finally:
            await _drain_cleanup(processes, tasks)
