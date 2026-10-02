"""Invoke the verified frozen R launcher once for one admitted HTTP request.

This adapter changes neither R nor its MFA/native/content policy. It does not
download media, claim a Backend lease, dispatch GPT, or enable HTTP admission.
The execution workspace is private scratch. PostgreSQL owns the one-shot start
and downstream handoff checkpoints; missing scratch is never permission to replay.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import asyncio
import hashlib
import os
from pathlib import Path
import stat
import time
from typing import Callable

from .canonical_bridge import FrozenCanonicalArtifacts, MAX_CORE_BYTES, _encode, _require
from .canonical_codec import read_document, validate_document
from .canonical_media import PreparedAudioIdentity, measure_prepared_wav
from .canonical_backend_journal import BackendCanonicalJournal
from .canonical_process import private_directory, run_guarded
from .http_models import AnalysisInput
from .ports import MaterializedAudio


CORE_PYTHON = Path('/workspace/intelligentai/validation/finish_20260916/envs/mac_prod311/bin/python')


@dataclass(frozen=True, slots=True)
class ProducedCanonicalCore:
    raw: bytes = field(repr=False)
    workspace: Path = field(repr=False)
    prepared_wav: Path | None = field(repr=False)
    prepared_audio: PreparedAudioIdentity | None


def write_private_new(path: Path, raw: bytes) -> None:
    private_directory(path.parent)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_private_result(path: Path, root: Path, limit: int) -> bytes:
    _require(path.resolve(strict=True) == path and path.is_relative_to(root),
             'CANONICAL_OUTPUT_PATH')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        _require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
                 and stat.S_IMODE(before.st_mode) == 0o600 and 0 < before.st_size <= limit,
                 'CANONICAL_OUTPUT_PERMISSIONS_OR_SIZE')
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    _require(len(raw) == before.st_size
             and (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
             == (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
             'CANONICAL_OUTPUT_CHANGED')
    return raw


class FrozenCanonicalProducer:
    def __init__(self, artifacts: FrozenCanonicalArtifacts, workspace_root: Path, journal: BackendCanonicalJournal):
        _require(type(journal) is BackendCanonicalJournal, 'BACKEND_JOURNAL_REQUIRED')
        self.journal = journal
        self.artifacts = artifacts
        self.workspace_root = private_directory(workspace_root)

    async def produce(self, request: AnalysisInput, audio: MaterializedAudio, *,
                      deadline_monotonic: float,
                      execution_is_active: Callable[[], bool]) -> ProducedCanonicalCore:
        _require(not request.is_legacy and request.visual_input is None,
                 'CANONICAL_PRODUCER_PROFILE')
        _require(execution_is_active() is True and time.monotonic() < deadline_monotonic,
                 'CANONICAL_EXECUTION_INACTIVE')
        # All immutable source digests are checked before any model subprocess.
        await asyncio.to_thread(self.artifacts.verify)
        _require(audio.sha256 == request.audio_sha256
                 and audio.byte_count == request.file_size_bytes,
                 'ADMITTED_AUDIO_MISMATCH')
        def verify_audio():
            with audio.path.open('rb') as stream:
                _require(os.fstat(stream.fileno()).st_size == audio.byte_count
                         and hashlib.file_digest(stream, 'sha256').hexdigest() == audio.sha256,
                         'ADMITTED_AUDIO_MISMATCH')
        await asyncio.to_thread(verify_audio)
        _require(hashlib.sha256(request.script_text.encode('utf-8')).hexdigest() == request.script_sha256,
                 'ADMITTED_SCRIPT_MISMATCH')
        # Durable one-shot start precedes process creation, even on ephemeral scratch.
        # Timeout/duplicate start is NOT permission to rerun the frozen core/GPT.
        await asyncio.to_thread(self.journal.start_producer, request)
        work = self.workspace_root / str(request.execution_id)
        # Existing workspace is an unknown/previous outcome, never a replay cue.
        work.mkdir(mode=0o700, exist_ok=False)
        private_directory(work)
        case_path = work / 'admitted-case.json'
        write_private_new(case_path, _encode([dict(id=str(request.event_id),
                                                   audio=str(audio.path), script=request.script_text)]))
        output = work / 'release-output'
        environment = dict(PATH=str(CORE_PYTHON.parent) + ':/usr/bin:/bin',
                           LANG='C.UTF-8', LC_ALL='C.UTF-8',
                           PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1',
                           HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                           HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
                           OMP_NUM_THREADS='2', MKL_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2',
                           NUMEXPR_NUM_THREADS='2')
        await run_guarded(python=CORE_PYTHON,
                          command=[str(CORE_PYTHON), '-B', '-u',
                                   str(self.artifacts.core_root / 'scripts/run_release.py'),
                                   '--cases', str(case_path), '--output', str(output)],
                          cwd=self.artifacts.core_root, environment=environment,
                          log_path=work / 'producer.log', deadline_monotonic=deadline_monotonic,
                          execution_is_active=execution_is_active)
        raw = read_private_result(output / 'candidate/results' / (str(request.event_id) + '.json'),
                                  work, MAX_CORE_BYTES)
        core = read_document(raw, MAX_CORE_BYTES)
        validate_document(core, schema_name='parent')
        _require(core['request_id'] == str(request.event_id)
                 and core['source_audio_sha256'] == request.audio_sha256,
                 'CORE_ATTEMPT_MISMATCH')
        prepared = identity = None
        if core.get('prepared_wav_sha256') is not None:
            prepared = output / 'candidate/prepared_cache' / (str(request.event_id) + '.wav')
            _require(prepared.resolve(strict=True) == prepared and prepared.is_relative_to(work),
                     'PREPARED_AUDIO_PATH_INVALID')
            identity = measure_prepared_wav(prepared, core['prepared_wav_sha256'])
        _require(execution_is_active() is True and time.monotonic() < deadline_monotonic,
                 'CANONICAL_EXECUTION_INACTIVE')
        return ProducedCanonicalCore(raw, work, prepared, identity)

