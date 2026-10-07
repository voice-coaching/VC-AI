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
import subprocess
import time
from typing import Callable

from .canonical_bridge import FrozenCanonicalArtifacts, MAX_CORE_BYTES, _encode, _require
from .canonical_codec import read_document, validate_document
from .canonical_media import PreparedAudioIdentity, measure_prepared_wav
from typing import Protocol


class ProducerJournal(Protocol):
    def start_producer(self, request) -> None: ...
from .canonical_process import private_directory
from .http_models import AnalysisInput
from .ports import MaterializedAudio
from .canonical_provenance import check_provenance


CORE_PYTHON = Path('/workspace/intelligentai/validation/finish_20260916/envs/mac_prod311/bin/python')


def core_environment(nltk_data_root: Path, *, cpu_threads: int = 2) -> dict[str, str]:
    # Use the already-provisioned read-only corpus, never a download to the
    # service user's nonexistent home. Do not inherit API/storage credentials.
    _require(nltk_data_root.is_absolute() and nltk_data_root.is_dir()
             and os.access(nltk_data_root / 'corpora/cmudict/cmudict', os.R_OK),
             'CANONICAL_NLTK_DATA_UNAVAILABLE')
    _require(type(cpu_threads) is int and cpu_threads in (1, 2), 'CANONICAL_CPU_BUDGET_INVALID')
    environment = dict(PATH=str(CORE_PYTHON.parent) + ':/usr/bin:/bin',
                LANG='C.UTF-8', LC_ALL='C.UTF-8', NLTK_DATA=str(nltk_data_root),
                PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1',
                HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
                OMP_NUM_THREADS=str(cpu_threads), MKL_NUM_THREADS=str(cpu_threads),
                OPENBLAS_NUM_THREADS=str(cpu_threads), NUMEXPR_NUM_THREADS=str(cpu_threads),
                TOKENIZERS_PARALLELISM='false')
    if cpu_threads == 1:
        # Keep all cuDNN sublibraries in one observed 9.24 set. The bundled
        # 9.20 wheel omits tensor_ir and otherwise loads a mismatched host file.
        cudnn = Path('/opt/intelligentai/runtime-libs/cudnn-9.24.0')
        preload = [cudnn / name for name in ('libcudnn.so.9.24.0', 'libcudnn_graph.so.9.24.0')]
        _require(all(p.is_file() for p in preload), 'DLPC_CUDNN_RUNTIME_MISSING')
        environment['LD_LIBRARY_PATH'] = str(cudnn)
        environment['LD_PRELOAD'] = ':'.join(map(str, preload))
    return environment


def check_core_environment(nltk_data_root: Path, *, cpu_threads: int = 2) -> None:
    """Offline dependency initialization in the actual core interpreter, no inference."""
    environment = core_environment(nltk_data_root, cpu_threads=cpu_threads)
    try:
        result = subprocess.run(
            [str(CORE_PYTHON), '-B', '-c',
             'import nltk; nltk.data.find("corpora/cmudict"); '
             'from g2pk import G2p; G2p()'],
            env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        _require(False, 'CANONICAL_G2PK_UNAVAILABLE')
    _require(result.returncode == 0, 'CANONICAL_G2PK_UNAVAILABLE')


@dataclass(frozen=True, slots=True)
class ProducedCanonicalCore:
    raw: bytes = field(repr=False)
    workspace: Path = field(repr=False)
    prepared_wav: Path | None = field(repr=False)
    prepared_audio: PreparedAudioIdentity | None
    timings: dict[str, float] = field(default_factory=dict)


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
    def __init__(self, artifacts: FrozenCanonicalArtifacts, workspace_root: Path,
                 journal: ProducerJournal, *, nltk_data_root: Path, resident_socket: Path | None = None):
        _require(callable(getattr(journal, 'start_producer', None)), 'PRODUCER_JOURNAL_REQUIRED')
        self.journal = journal
        self.artifacts = artifacts
        self.workspace_root = private_directory(workspace_root)
        self.environment = core_environment(nltk_data_root)
        self.resident_socket = resident_socket
        from .native_execution import require_native_execution
        require_native_execution(artifacts)
        _require(resident_socket is not None, 'NATIVE_RESIDENT_REQUIRED_NO_ONESHOT_FALLBACK')
        check_provenance(artifacts.core_root)

    async def produce(self, request: AnalysisInput, audio: MaterializedAudio, *,
                      deadline_monotonic: float,
                      execution_is_active: Callable[[], bool], audio_branch: bool = False) -> ProducedCanonicalCore:
        _require(not request.is_legacy and (request.visual_input is None or (
                     audio_branch is True and request.is_audiovisual)),
                 'CANONICAL_PRODUCER_PROFILE')
        _require(execution_is_active() is True and time.monotonic() < deadline_monotonic,
                 'CANONICAL_EXECUTION_INACTIVE')
        # All immutable source digests are checked before any model subprocess.
        marks = {"started": time.monotonic()}
        await asyncio.to_thread(self.artifacts.verify)
        marks["artifactsVerified"] = time.monotonic()
        _require(audio.sha256 == request.audio_sha256
                 and audio.byte_count == request.file_size_bytes,
                 'ADMITTED_AUDIO_MISMATCH')
        def verify_audio():
            with audio.path.open('rb') as stream:
                _require(os.fstat(stream.fileno()).st_size == audio.byte_count
                         and hashlib.file_digest(stream, 'sha256').hexdigest() == audio.sha256,
                         'ADMITTED_AUDIO_MISMATCH')
        await asyncio.to_thread(verify_audio)
        marks["audioVerified"] = time.monotonic()
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
                                                   executionId=str(request.execution_id),
                                                   audio=str(audio.path), script=request.script_text)]))
        output = work / 'release-output'
        marks["prepared"] = time.monotonic()
        # The frozen conda build lacks pidfd bindings. Supervision stays in the
        # checked canonical executor environment; only the model uses CORE_PYTHON.
        if self.resident_socket is not None:
            from .canonical_resident import produce_resident
            await produce_resident(self.resident_socket, request=request, case_path=case_path,
                                   deadline=deadline_monotonic, active=execution_is_active)
        marks["inferenceFinished"] = time.monotonic()
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
        marks["outputValidated"] = time.monotonic()
        timings = {name: marks[end]-marks[start] for name, start, end in (
            ("artifactVerificationSeconds", "started", "artifactsVerified"),
            ("audioVerificationSeconds", "artifactsVerified", "audioVerified"),
            ("preparationSeconds", "audioVerified", "prepared"),
            ("residentSeconds", "prepared", "inferenceFinished"),
            ("outputValidationSeconds", "inferenceFinished", "outputValidated"))}
        return ProducedCanonicalCore(raw, work, prepared, identity, timings)
