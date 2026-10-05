"""One isolated invocation; the HTTP parent owns heartbeat, lease and slot.

No fake core on failure, no SQLite fallback and no implicit inference replay.
The guard kills/reaps the complete process subtree on cancellation/lease loss.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
import sys
import time
from uuid import UUID

from .canonical_bridge import CanonicalIntegrationError, FrozenCanonicalArtifacts, FrozenCanonicalCoaching, _require
from .canonical_backend_journal import BackendCanonicalJournal, BackendJournalFailure
from .canonical_codec import encode_document
from .canonical_lease import LEASE_CODES, read_lease
from .canonical_process import CanonicalProcessFailed, check_guard_python, private_directory
from .canonical_producer import (
    FrozenCanonicalProducer, check_core_environment, read_private_result, write_private_new,
)
from .canonical_result import preflight_canonical_result, serialize_canonical_result, serialize_precore_failure, ServiceFailure, ResultIdentity
from .canonical_transport import FrozenOpenAITransport
from .http_models import AnalysisInput, parse_json, RESULT_V5
from .canonical_handoff import CanonicalHandoffClient, handoff_document
from .canonical_recovery import persist_handoff
from .http_settings import HttpSettings
from .pipeline_timing import PipelineTiming
from .canonical_async import drain_on_cancel
from .adapters.s3_object_store import (
    S3CompatibleRestrictedObjectStore, S3ObjectStoreSettings, create_s3_client_from_environment,
)


def artifacts(env):
    return FrozenCanonicalArtifacts(Path(env["AI_CANONICAL_CORE_ROOT"]),
                                    Path(env["AI_CANONICAL_LLM_ROOT"]))


def check(env):
    """Static import/pin/config and read-only private ACL, never generation."""
    from .canonical_producer import CORE_PYTHON
    HttpSettings.from_environment(env)
    from .native_execution import require_native_execution
    require_native_execution(artifacts(env))
    _require(env.get('AI_CANONICAL_RESIDENT_ENABLED') == 'true', 'NATIVE_RESIDENT_REQUIRED')
    check_guard_python(Path(sys.executable))
    private_directory(Path(env["AI_CANONICAL_WORKSPACE_ROOT"]))
    _require(CORE_PYTHON.is_file() and os.access(CORE_PYTHON, os.X_OK),
             "CANONICAL_CORE_PYTHON_UNAVAILABLE")
    check_core_environment(Path(env["AI_ANALYSIS_NLTK_DATA_ROOT"]))
    frozen = FrozenCanonicalCoaching(artifacts(env))
    from .canonical_provenance import check_provenance
    check_provenance(frozen.artifacts.core_root)
    bridge = frozen._bridge()
    if frozen.artifacts.identity.scoring:
        bridge.scoring_policy()
    FrozenOpenAITransport(env["OPENAI_API_KEY"])


async def execute(path: Path, env, diagnostic: dict):
    work = private_directory(path.parent)
    descriptor = parse_json(read_private_result(path, work, 70000))
    _require(set(descriptor) == {"request", "workerId", "deadline"}, "CANONICAL_DESCRIPTOR")
    body, worker = descriptor["request"], UUID(descriptor["workerId"])
    settings = HttpSettings.from_environment(env)
    request = AnalysisInput.from_payload(body, settings.prefix)
    _require(not request.is_legacy and request.visual_input is None, "CANONICAL_PROFILE_UNSUPPORTED")
    deadline = descriptor["deadline"]
    _require(type(deadline) in (int, float) and math.isfinite(deadline), "CANONICAL_DEADLINE")

    diagnostic.update(requestId=str(request.event_id), executionId=str(request.execution_id))
    timing = PipelineTiming(analysis_id=request.analysis_id, request_id=request.event_id,
                            execution_id=request.execution_id, revision=env["VOICE_COACH_BUILD_REVISION"])

    def stage(name: str) -> None:
        timing.stage(name)
        diagnostic["stage"] = name
        print(json.dumps({"event": "canonical_stage", **diagnostic}), file=sys.stderr, flush=True)

    def remaining():
        snapshot = read_lease(work, request.execution_id, deadline)
        if snapshot.replacements:
            print(json.dumps({"event": "canonical_lease_replaced",
                              "executionId": str(request.execution_id),
                              "replacements": snapshot.replacements}), file=sys.stderr, flush=True)
        return snapshot.remaining(deadline)

    def active():
        return remaining() > 0

    def fence():
        _require(active(), "CANONICAL_EXECUTION_INACTIVE")

    fence()
    frozen = FrozenCanonicalCoaching(artifacts(env))
    journal = BackendCanonicalJournal(settings, request_raw=encode_document(body), worker_id=worker,
                                      remaining_seconds=remaining, execution_is_active=active)
    handoff = CanonicalHandoffClient(settings, worker)
    recordings = create_s3_client_from_environment(env)
    audio_context = None
    acquired = False
    identity = produced = None
    try:
        stage("RESERVE_AND_MATERIALIZE")
        media_root = work / "media"
        media_root.mkdir(mode=0o700)
        media = S3CompatibleRestrictedObjectStore(recordings,
            S3ObjectStoreSettings(env["AI_ANALYSIS_OBJECT_STORAGE_BUCKET"], settings.prefix, media_root))
        audio_context = media.materialize(request)
        async def materialize():
            nonlocal acquired
            value = await asyncio.to_thread(audio_context.__enter__)
            acquired = True
            return value

        # Neither operation depends on the other's result. Drain both before
        # cleanup: cancelling to_thread does not stop its underlying download.
        prepared = asyncio.gather(
            asyncio.to_thread(journal.reserve,
                worker_revision=env["VOICE_COACH_BUILD_REVISION"],
                pipeline_revision=env["AI_CANONICAL_PIPELINE_REVISION"]),
            materialize(), return_exceptions=True)
        identity, audio = await drain_on_cancel(prepared)
        for value in (identity, audio):
            if isinstance(value, BaseException):
                raise value
        fence()
        stage("CORE")
        core_root = work / "core"
        core_root.mkdir(mode=0o700)
        produced = await FrozenCanonicalProducer(
            frozen.artifacts, core_root, journal,
            nltk_data_root=Path(env["AI_ANALYSIS_NLTK_DATA_ROOT"]),
            resident_socket=Path(env['AI_CANONICAL_RESIDENT_SOCKET'])
                if env.get('AI_CANONICAL_RESIDENT_SOCKET') else None).produce(
            request, audio, deadline_monotonic=deadline, execution_is_active=active)
        fence()
        stage("EXPRESSION")
        expression = await asyncio.to_thread(frozen.express_admitted, request, audio=audio,
            core_bytes=produced.raw, prepared_wav=produced.prepared_wav,
            producer_workspace=produced.workspace, transport=FrozenOpenAITransport(env["OPENAI_API_KEY"]))
        fence()
        stage("PREFLIGHT")
        preflight = await asyncio.to_thread(preflight_canonical_result, expression,
            request=request, frozen=frozen, prepared_audio=produced.prepared_audio)
        fence()
        stage("RESULT_PUBLISH")
        raw = await asyncio.to_thread(serialize_canonical_result, expression,
            request=request, identity=identity, frozen=frozen, prepared_audio=produced.prepared_audio)
        doc = handoff_document(body, raw, preflight, identity)
        await asyncio.to_thread(persist_handoff, work, doc, preflight.artifacts)
        published = await handoff.publish(raw)
        print(json.dumps({"event": "canonical_result_publish", "analysisId": request.analysis_id,
                          "executionId": str(request.execution_id), "published": published}), file=sys.stderr, flush=True)
        fence()
        stage("HANDOFF")
        await handoff.deliver(doc, preflight.artifacts, active=active, remaining=remaining)
        write_private_new(work / "completed.json", encode_document({"status": "HANDED_OFF"}))
        return
    except Exception:
        # A failure before a valid core has no fabricated core/artifact/score.
        # Post-core errors keep the private scratch for investigation, not a false success.
        if isinstance(identity, ResultIdentity) and produced is None and active():
            raw = serialize_precore_failure(request=request, identity=identity,
                failure=ServiceFailure("INTEGRATION", "CANONICAL_EXECUTION_FAILED", "EXECUTION"))
            doc = handoff_document(body, raw, None, identity)
            await asyncio.to_thread(persist_handoff, work, doc, ())
            stage("RESULT_PUBLISH")
            await handoff.publish(raw)
            fence()
            stage("HANDOFF")
            await handoff.deliver(doc, (), active=active, remaining=remaining)
            write_private_new(work / "completed.json", encode_document({"status": "HANDED_OFF"}))
            return
        raise
    finally:
        # Parent guard handles abrupt death; never delete external retained evidence.
        primary_failure = sys.exception()
        timing.finish("FAILED" if primary_failure is not None else "OK")
        cleanup_failure = None
        actions = [(recordings.close, False), (journal.close, False), (handoff.close, True)]
        if acquired:
            actions.insert(0, (lambda: audio_context.__exit__(None, None, None), False))
        for action, is_async in actions:
            try:
                if is_async:
                    await action()
                else:
                    await asyncio.to_thread(action)
            except Exception as error:
                cleanup_failure = cleanup_failure or error
        if cleanup_failure is not None:
            print(json.dumps({"event": "canonical_cleanup_failed", "code": "INTERNAL_ERROR",
                              "primaryFailure": primary_failure is not None,
                              "executionId": str(request.execution_id)}), file=sys.stderr, flush=True)
            if primary_failure is None:
                diagnostic["stage"] = "CLEANUP"
                raise cleanup_failure


def main():
    os.umask(0o077)
    context = {"stage": "CONFIGURATION"}
    try:
        if sys.argv[1:] == ["--check"]:
            check(os.environ)
            print("canonical_executor_configuration_ready")
        elif len(sys.argv) == 2:
            asyncio.run(execute(Path(sys.argv[1]), os.environ, context))
        else:
            raise ValueError()
    except Exception as error:
        # Do not leak provider/SDK exceptions, transcripts, object paths or keys.
        diagnostic = {"event": "canonical_executor_unavailable", "code": "INTERNAL_ERROR", **context}
        if isinstance(error, CanonicalProcessFailed):
            diagnostic.update(code="CANONICAL_PROCESS_FAILED", guardExitStatus=error.exit_status)
        elif isinstance(error, BackendJournalFailure):
            diagnostic.update(code=str(error), upstreamStatus=error.status)
        elif isinstance(error, CanonicalIntegrationError) and str(error) in {
                "CANONICAL_GUARD_RUNTIME_UNAVAILABLE", "CANONICAL_PROCESS_CLEANUP_UNCONFIRMED",
                "BACKEND_JOURNAL_RESPONSE_INVALID", "CANONICAL_EXECUTION_INACTIVE",
                "CANONICAL_SCHEMA_INVALID", "CANONICAL_OUTPUT_CHANGED"} | LEASE_CODES:
            diagnostic["code"] = str(error)
        print(json.dumps(diagnostic), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
