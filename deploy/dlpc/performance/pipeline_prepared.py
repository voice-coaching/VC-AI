"""Strict PCM copy and generation-owned tool versions; no acoustic result cache."""
import hashlib
import os
from pathlib import Path
import shutil
import stat
import struct
import time
from uuid import uuid4

REVISION = 'pcm-copy-and-tool-versions-v1'


def pcm_identity(raw, maximum_seconds):
    if len(raw) < 44 or raw[:4] != b'RIFF' or raw[8:12] != b'WAVE':
        return None
    if struct.unpack_from('<I', raw, 4)[0] != len(raw)-8:
        return None
    offset, fmt, data, chunks = 12, None, None, 0
    while offset < len(raw):
        if offset+8 > len(raw) or chunks >= 64:
            return None
        tag, size = struct.unpack_from('<4sI', raw, offset)
        start, end = offset+8, offset+8+size
        if end > len(raw):
            return None
        if tag == b'fmt ':
            if fmt is not None or size != 16:
                return None
            fmt = struct.unpack_from('<HHIIHH', raw, start)
        elif tag == b'data':
            if fmt is None or data is not None or not size or size % 2:
                return None
            data = size
        elif tag not in (b'LIST', b'JUNK') or size > 4096:
            return None
        offset = end + (size % 2)
        if offset > len(raw) or (size % 2 and raw[end] != 0):
            return None
        chunks += 1
    if offset != len(raw) or fmt != (1, 1, 16000, 32000, 2, 16) or data is None:
        return None
    frames = data // 2
    if not 0 < frames <= maximum_seconds * 16000:
        return None
    return frames, hashlib.sha256(raw).hexdigest()


def prepare_audio(source, workdir, config, original):
    from korean_pronunciation_poc.audio import AudioResult
    from korean_pronunciation_poc.schemas import AudioMetadata
    started = time.perf_counter()
    source_path = Path(source).expanduser().resolve(strict=False)
    raw, identity = None, None
    try:
        fd = os.open(source_path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            maximum = int(config.analysis.max_audio_s * 32000 + 65536)
            if stat.S_ISREG(before.st_mode) and 44 <= before.st_size <= maximum:
                raw = stream.read(maximum+1)
                after = os.fstat(stream.fileno())
                if (before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns):
                    identity = pcm_identity(raw, config.analysis.max_audio_s)
    except OSError:
        pass
    project = config.paths.project_root.resolve(strict=True)
    target_root = Path(workdir).expanduser().resolve(strict=False)
    if raw is None or identity is None or not target_root.is_relative_to(project):
        return original(source, workdir, config)
    target_root.mkdir(parents=True, exist_ok=True)
    target_root = target_root.resolve(strict=True)
    if not target_root.is_relative_to(project):
        return original(source, workdir, config)
    target = target_root / ('prepared-' + uuid4().hex + '.wav')
    try:
        with target.open('xb') as stream:
            stream.write(raw)
        frames, digest = identity
        result = AudioResult(prepared_wav=target, metadata=AudioMetadata(
            duration_s=frames/16000, sample_rate_hz=16000, channels=1,
            encoding='pcm_s16le', sha256=digest))
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    print(__import__('json').dumps(dict(event='canonical_pcm_copy', revision=REVISION,
        frames=frames, elapsedSeconds=time.perf_counter()-started)), flush=True)
    return result


class PreparedToolVersions:
    def __init__(self, original):
        self.original = original
        self.values = {}

    @staticmethod
    def identity(command):
        resolved = shutil.which(command)
        if resolved is None:
            raise RuntimeError('PREPARED_AUDIO_TOOL_UNAVAILABLE')
        path = Path(resolved).resolve(strict=True)
        info = path.stat()
        env = tuple(os.environ.get(k) for k in ('PATH', 'LD_LIBRARY_PATH', 'LD_PRELOAD'))
        return str(path), info.st_ino, info.st_size, info.st_mtime_ns, env

    def __call__(self, command, *, cwd, runner):
        identity = self.identity(command)
        if command in self.values:
            old, version = self.values[command]
            if identity != old:
                raise RuntimeError('PREPARED_AUDIO_TOOL_CHANGED')
            return version
        version = self.original(command, cwd=cwd, runner=runner)
        self.values[command] = identity, version
        return version


def install(pipeline, config):
    from korean_pronunciation_poc import manifest
    versions = PreparedToolVersions(manifest._tool_version)
    for command in (config.audio.ffmpeg_command, config.audio.ffprobe_command):
        versions(command, cwd=config.paths.project_root, runner=manifest.subprocess.run)
    manifest._tool_version = versions
    original = pipeline.prepare_audio
    pipeline.prepare_audio = lambda source, workdir, settings: prepare_audio(source, workdir, settings, original)
    return dict(revision=REVISION, tools=len(versions.values), preparedAtStartup=True)
