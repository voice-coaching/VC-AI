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
from .canonical_backend_journal import BackendCanonicalJournal
from .canonical_backend_delivery import BackendCanonicalDelivery
from .canonical_codec import encode_document
from .canonical_evidence_store import evidence_store_from_environment
from .canonical_process import CanonicalProcessFailed, check_guard_python, private_directory
from .canonical_producer import FrozenCanonicalProducer, read_private_result, write_private_new
from .canonical_receipt_client import CanonicalReceiptClient
from .canonical_result import preflight_canonical_result, serialize_canonical_result
from .canonical_retention import retain_canonical_preflight
from .canonical_transport import FrozenOpenAITransport
from .http_models import AnalysisInput, parse_json
from .http_settings import HttpSettings
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
    check_guard_python(Path(sys.executable))
    private_directory(Path(env["AI_CANONICAL_WORKSPACE_ROOT"]))
    _require(CORE_PYTHON.is_file() and os.access(CORE_PYTHON, os.X_OK),
             "CANONICAL_CORE_PYTHON_UNAVAILABLE")
    FrozenCanonicalCoaching(artifacts(env))._bridge()
    FrozenOpenAITransport(env["OPENAI_API_KEY"])
    store = evidence_store_from_environment(env)
    try:
        store.assert_private()
    finally:
        store.close()


async def execute(path: Path, env):
    work = private_directory(path.parent)
    descriptor = parse_json(read_private_result(path, work, 70000))
    _require(set(descriptor) == {"request", "workerId", "deadline"}, "CANONICAL_DESCRIPTOR")
    body, worker = descriptor["request"], UUID(descriptor["workerId"])
    settings = HttpSettings.from_environment(env)
    request = AnalysisInput.from_payload(body, settings.prefix)
    _require(not request.is_legacy and request.visual_input is None, "CANONICAL_PROFILE_UNSUPPORTED")
    deadline = descriptor["deadline"]
    _require(type(deadline) in (int, float) and math.isfinite(deadline), "CANONICAL_DEADLINE")

    def remaining():
        try:
            lease = parse_json(read_private_result(work / "lease.json", work, 1024))
            _require(set(lease) == {"executionId", "until"} and
                     lease["executionId"] == str(request.execution_id), "CANONICAL_LEASE")
            until = lease["until"]
            _require(type(until) in (int, float) and math.isfinite(until), "CANONICAL_LEASE")
            return max(0.0, min(deadline, until) - time.monotonic())
        except Exception:
            return 0.0

    def active():
        return remaining() > 0

    def fence():
        _require(active(), "CANONICAL_EXECUTION_INACTIVE")

    fence()
    frozen = FrozenCanonicalCoaching(artifacts(env))
    journal = BackendCanonicalJournal(settings, request_raw=encode_document(body), worker_id=worker,
                                      remaining_seconds=remaining, execution_is_active=active)
    receipt_client = CanonicalReceiptClient(settings, worker)
    delivery = BackendCanonicalDelivery(settings, journal)
    store = evidence_store_from_environment(env)
    recordings = create_s3_client_from_environment(env)
    audio_context = None
    acquired = False
    try:
        identity = await asyncio.to_thread(journal.reserve,
            worker_revision=env["VOICE_COACH_BUILD_REVISION"],
            pipeline_revision=env["AI_CANONICAL_PIPELINE_REVISION"])
        fence()
        media_root = work / "media"
        media_root.mkdir(mode=0o700)
        media = S3CompatibleRestrictedObjectStore(recordings,
            S3ObjectStoreSettings(env["AI_ANALYSIS_OBJECT_STORAGE_BUCKET"], settings.prefix, media_root))
        audio_context = media.materialize(request)
        audio = await asyncio.to_thread(audio_context.__enter__)
        acquired = True
        fence()
        core_root = work / "core"
        core_root.mkdir(mode=0o700)
        produced = await FrozenCanonicalProducer(frozen.artifacts, core_root, journal).produce(
            request, audio, deadline_monotonic=deadline, execution_is_active=active)
        fence()
        expression = await asyncio.to_thread(frozen.express_admitted, request, audio=audio,
            core_bytes=produced.raw, prepared_wav=produced.prepared_wav,
            producer_workspace=produced.workspace, transport=FrozenOpenAITransport(env["OPENAI_API_KEY"]))
        fence()
        preflight = await asyncio.to_thread(preflight_canonical_result, expression,
            request=request, frozen=frozen, prepared_audio=produced.prepared_audio)
        fence()
        retained = await asyncio.to_thread(retain_canonical_preflight, preflight,
            store=store, journal=journal, deadline_monotonic=deadline, execution_is_active=active)
        receipt = await receipt_client.await_verified(retained, journal=journal,
            deadline_monotonic=deadline, execution_is_active=active)
        fence()
        raw = await asyncio.to_thread(serialize_canonical_result, expression,
            request=request, identity=identity, frozen=frozen, receipt=receipt,
            prepared_audio=produced.prepared_audio)
        write_private_new(work / "callback.json", raw)
        ack = await delivery.deliver(raw, identity=identity,
            execution_is_active=active, remaining_seconds=remaining)
        _require(ack["status"] in ("APPLIED", "DUPLICATE"), "CANONICAL_ACK_INVALID")
        result = parse_json(raw)
        write_private_new(work / "completed.json",
                          encode_document({"status": result["status"]}))
    finally:
        # Parent guard handles abrupt death; never delete external retained evidence.
        if acquired:
            await asyncio.to_thread(audio_context.__exit__, None, None, None)
        recordings.close()
        store.close()
        await delivery.close()
        await receipt_client.close()
        journal.close()


def main():
    os.umask(0o077)
    try:
        if sys.argv[1:] == ["--check"]:
            check(os.environ)
            print("canonical_executor_configuration_ready")
        elif len(sys.argv) == 2:
            asyncio.run(execute(Path(sys.argv[1]), os.environ))
        else:
            raise ValueError()
    except Exception as error:
        # Do not leak provider/SDK exceptions, transcripts, object paths or keys.
        diagnostic = {"event": "canonical_executor_unavailable"}
        if isinstance(error, CanonicalProcessFailed):
            diagnostic.update(code="CANONICAL_PROCESS_FAILED", guardExitStatus=error.exit_status)
        elif isinstance(error, CanonicalIntegrationError) and str(error) in {
                "CANONICAL_GUARD_RUNTIME_UNAVAILABLE", "CANONICAL_PROCESS_CLEANUP_UNCONFIRMED"}:
            diagnostic["code"] = str(error)
        print(json.dumps(diagnostic), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
