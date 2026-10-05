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
from .store import atomic, encoded


class LocalProducerJournal:
    def __init__(self, folder):
        self.folder = folder

    def start_producer(self, request):
        write_private_new(self.folder / "producer-started.json", encoded({
            "executionId": str(request.execution_id), "requestId": str(request.event_id)}))


async def execute(folder: Path):
    doc = json.loads((folder / "job.json").read_bytes())
    request = DirectInput(UUID(doc["jobId"]), UUID(doc["requestId"]), UUID(doc["executionId"]),
                          UUID(doc["audioAssetId"]), doc["scriptText"], doc["scriptSha256"],
                          doc["audioSha256"], doc["fileSizeBytes"], doc["contentId"])
    deadline = time.monotonic() + max(0, doc["deadlineAt"] - time.time())
    artifacts = FrozenCanonicalArtifacts(Path(os.environ["AI_CANONICAL_CORE_ROOT"]),
                                         Path(os.environ["AI_CANONICAL_LLM_ROOT"]),
                                         Path(os.environ['AI_CANONICAL_VERIFIED_GENERATION'])
                                         if os.environ.get('AI_CANONICAL_VERIFIED_GENERATION') else None)
    require_native_execution(artifacts)
    frozen = FrozenCanonicalCoaching(artifacts)
    work = Path(os.environ["AI_CANONICAL_WORKSPACE_ROOT"]) / str(request.execution_id)
    (work / "media").mkdir(parents=True, mode=0o700)
    audio_path = work / "media/audio.wav"
    # Same persistent volume: immutable bytes, no remote audio download.
    os.link(folder / "audio.wav", audio_path)
    audio = MaterializedAudio(audio_path, request.file_size_bytes, request.audio_sha256)
    marks = {"coreStartedAt": time.time()}
    active = lambda: time.monotonic() < deadline and json.loads((folder / "job.json").read_bytes())["status"] == "RUNNING"
    produced = await FrozenCanonicalProducer(artifacts, work / "core", LocalProducerJournal(folder),
        nltk_data_root=Path(os.environ["AI_ANALYSIS_NLTK_DATA_ROOT"]),
        resident_socket=Path(os.environ["AI_CANONICAL_RESIDENT_SOCKET"])).produce(
            request, audio, deadline_monotonic=deadline, execution_is_active=active)
    marks["coreFinishedAt"] = marks["gptStartedAt"] = time.time()
    expression = await asyncio.to_thread(frozen.express_admitted, request, audio=audio,
        core_bytes=produced.raw, prepared_wav=produced.prepared_wav,
        producer_workspace=produced.workspace, transport=FrozenOpenAITransport(os.environ["OPENAI_API_KEY"]))
    marks["gptFinishedAt"] = time.time()
    preflight = await asyncio.to_thread(preflight_canonical_result, expression,
        request=request, frozen=frozen, prepared_audio=produced.prepared_audio)
    core = json.loads(expression.core_bytes)
    coaching = json.loads(expression.expression_bytes)
    selection = json.loads(expression.selection_bytes) if expression.selection_bytes else None
    failed = coaching["adapterStatus"] == "FAIL_CLOSED" or core["input_validation"]["status"] == "SYSTEM_FAILURE"
    public = dict(decision={k: core["input_validation"][k] for k in ("status", "reason_code", "stage")},
        coreStatus=core["core_result"]["status"],
        score=_public_score(coaching, selection, artifacts.identity),
        coaching={k: coaching[k] for k in ("schemaVersion", "adapterStatus", "generationStatus", "dispatchAttempts", "items")},
        selection=_public_selection(selection, coaching),
        sourceIdentity=dict(requestId=str(request.event_id), executionId=str(request.execution_id),
            audioSha256=request.audio_sha256, scriptSha256=request.script_sha256,
            coreManifestSha256=artifacts.identity.core_sha256,
            harnessManifestSha256=artifacts.identity.manifest_sha256),
        failure="CANONICAL_ANALYSIS_FAILED" if failed else None)
    artifacts_root = folder / "artifacts"
    artifacts_root.mkdir(mode=0o700)
    for artifact in preflight.artifacts:
        atomic(artifacts_root / (artifact.kind + ".json"), artifact.raw)
    # Publish only after originals are recoverable locally, never wait for B2/AWS.
    atomic(folder / "result.json", encoded(public))
    atomic(folder / "worker-complete.json", encoded({"timing": marks, "failed": failed}))


if __name__ == "__main__":
    try:
        asyncio.run(execute(Path(sys.argv[1]).resolve(strict=True)))
    except Exception:
        # Private exception contents can contain prompt/provider details.
        print("DIRECT_EXECUTION_FAILED", flush=True)
        raise SystemExit(1)
