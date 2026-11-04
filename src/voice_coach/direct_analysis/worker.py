"""One Native execution; no AWS lease, identity reservation, or result callback."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import time
from uuid import UUID

from voice_coach.backend_analysis.canonical_bridge import FrozenCanonicalArtifacts, FrozenCanonicalCoaching
from voice_coach.backend_analysis.canonical_producer import FrozenCanonicalProducer, write_private_new
from voice_coach.backend_analysis.canonical_result import preflight_canonical_result, _public_score, _public_selection
from voice_coach.backend_analysis.canonical_transport import FrozenOpenAITransport
from voice_coach.backend_analysis.native_execution import require_native_execution
from voice_coach.backend_analysis.ports import MaterializedAudio
from .domain import DirectInput
from .practice_result import practice_result
from .store import atomic, encoded


class LocalProducerJournal:
    def __init__(self, folder):
        self.folder = folder

    def start_producer(self, request):
        # Network spool has atomic/exclusive file creation but synthetic UID/mode.
        # Never apply scratch's private_directory check to this durable journal.
        fd = os.open(self.folder / "producer-started.json",
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded({"executionId": str(request.execution_id),
                                  "requestId": str(request.event_id)}))
            stream.flush()
            os.fsync(stream.fileno())
        fd = os.open(self.folder, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


async def execute(folder: Path, *, runtime_environment=None, shared_transport=None, shared_coaching=None):
    env = os.environ if runtime_environment is None else runtime_environment
    marks = {"workerStartedAt": time.time()}
    doc = json.loads((folder / "job.json").read_bytes())
    request = DirectInput(UUID(doc["jobId"]), UUID(doc["requestId"]), UUID(doc["executionId"]),
                          UUID(doc["audioAssetId"]), doc["scriptText"], doc["scriptSha256"],
                          doc["audioSha256"], doc["fileSizeBytes"], doc["contentId"])
    deadline = time.monotonic() + max(0, doc["deadlineAt"] - time.time())
    artifacts = FrozenCanonicalArtifacts(Path(env["AI_CANONICAL_CORE_ROOT"]),
                                         Path(env["AI_CANONICAL_LLM_ROOT"]),
                                         Path(env['AI_CANONICAL_VERIFIED_GENERATION'])
                                         if env.get('AI_CANONICAL_VERIFIED_GENERATION') else None)
    require_native_execution(artifacts)
    frozen = shared_coaching or FrozenCanonicalCoaching(artifacts)
    work = Path(env["AI_CANONICAL_WORKSPACE_ROOT"]) / str(request.execution_id)
    (work / "media").mkdir(parents=True, mode=0o700)
    audio_path = work / "media/audio.wav"
    # Local copy from the accepted spool, no AWS reservation or remote download.
    # Hardlinks cannot cross from the network spool to owned POSIX scratch.
    write_private_new(audio_path, (folder / "audio.wav").read_bytes())
    (work / "core").mkdir(mode=0o700)
    audio = MaterializedAudio(audio_path, request.file_size_bytes, request.audio_sha256)
    marks["coreStartedAt"] = time.time()
    # Parent owns cancellation and kills the bounded worker/resident subtree.
    # Do not poll the network spool on every resident response wait tick.
    active = lambda: time.monotonic() < deadline
    produced = await FrozenCanonicalProducer(artifacts, work / "core", LocalProducerJournal(folder),
        nltk_data_root=Path(env["AI_ANALYSIS_NLTK_DATA_ROOT"]),
        resident_socket=Path(env["AI_CANONICAL_RESIDENT_SOCKET"])).produce(
            request, audio, deadline_monotonic=deadline, execution_is_active=active)
    marks["coreFinishedAt"] = marks["expressionStartedAt"] = time.time()
    transport = shared_transport or FrozenOpenAITransport.for_harness(env["OPENAI_API_KEY"], artifacts.identity)
    try:
        expression = await asyncio.to_thread(frozen.express_admitted, request, audio=audio,
            core_bytes=produced.raw, prepared_wav=produced.prepared_wav,
            producer_workspace=produced.workspace, transport=transport)
    finally:
        # Keep closed provider diagnostics even if later preflight/publication fails.
        try:
            atomic(folder / 'provider-observation.json', encoded(dict(
                harnessManifestSha256=artifacts.identity.manifest_sha256, **transport.diagnostics)))
        except OSError:
            # Optional diagnostic I/O must not turn a valid generation into a failure.
            transport.diagnostics['observationPersistence'] = 'FAILED'
    marks["expressionFinishedAt"] = marks["validationStartedAt"] = time.time()
    preflight = await asyncio.to_thread(preflight_canonical_result, expression,
        request=request, frozen=frozen, prepared_audio=produced.prepared_audio)
    marks["validationFinishedAt"] = time.time()
    core = json.loads(expression.core_bytes)
    coaching = json.loads(expression.expression_bytes)
    selection = json.loads(expression.selection_bytes) if expression.selection_bytes else None
    failed = coaching["adapterStatus"] == "FAIL_CLOSED" or core["input_validation"]["status"] == "SYSTEM_FAILURE"
    public = dict(decision={k: core["input_validation"][k] for k in ("status", "reason_code", "stage")},
        coreStatus=core["core_result"]["status"],
        score=_public_score(coaching, selection, artifacts.identity),
        coaching={k: coaching[k] for k in ("schemaVersion", "adapterStatus", "generationStatus", "dispatchAttempts", "items", "feedback") if k in coaching},
        selection=_public_selection(selection, coaching),
        sourceIdentity=dict(requestId=str(request.event_id), executionId=str(request.execution_id),
            audioSha256=request.audio_sha256, scriptSha256=request.script_sha256,
            coreManifestSha256=artifacts.identity.core_sha256,
            harnessManifestSha256=artifacts.identity.manifest_sha256),
        failure="CANONICAL_ANALYSIS_FAILED" if failed else None)
    public["practiceResult"] = practice_result(
        script_text=request.script_text, decision=public["decision"],
        score=public["score"], coaching=public["coaching"], failed=failed)
    if artifacts.identity.six_dimension and "sixDimension" in coaching:
        from voice_coach.six_dimension.public import public_view
        public["coaching"]["sixDimension"] = public_view(coaching["sixDimension"])
    artifacts_root = folder / "artifacts"
    artifacts_root.mkdir(mode=0o700)
    await asyncio.gather(*(asyncio.to_thread(atomic,
        artifacts_root / (artifact.kind + ".json"), artifact.raw) for artifact in preflight.artifacts))
    # Publish only after originals are recoverable locally, never wait for B2/AWS.
    marks["artifactsDurableAt"] = time.time()
    atomic(folder / "result.json", encoded(public))
    marks["resultDurableAt"] = time.time()
    atomic(folder / "worker-complete.json", encoded({"timing": marks, "failed": failed,
        "executionId": str(request.execution_id), "requestId": str(request.event_id),
        "coreTiming": core.get("stage_durations_s"), "producerTiming": produced.timings,
        "providerTiming": transport.diagnostics,
        "generationStatus": coaching.get("generationStatus"),
        "dispatchAttempts": coaching.get("dispatchAttempts")}))
    if shared_transport is None:
        transport.close()


if __name__ == "__main__":
    try:
        asyncio.run(execute(Path(sys.argv[1]).resolve(strict=True)))
    except Exception as error:
        # Private exception contents can contain prompt/provider details.
        import re
        code = str(error)
        print("DIRECT_EXECUTION_FAILED " + (code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,100}", code)
                                            else type(error).__name__), flush=True)
        raise SystemExit(1)
