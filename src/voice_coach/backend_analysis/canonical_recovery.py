"""Private transport journal. Recover the same bytes and worker, never inference."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import logging
from pathlib import Path
from uuid import UUID

from .canonical_bridge import _require
from .canonical_codec import encode_document, validate_document
from .canonical_process import private_directory
from .canonical_producer import read_private_result, write_private_new
from .http_models import parse_json, digest, timestamp, validate_analysis_request


@dataclass(frozen=True)
class TransferArtifact:
    kind: str
    raw: bytes


def persist_handoff(work, document, artifacts):
    """Fsync all replay bytes before the journal commit point and optional preview."""
    folder = work / "handoff-artifacts"
    folder.mkdir(mode=0o700, exist_ok=False)
    for artifact in artifacts:
        write_private_new(folder / artifact.kind, artifact.raw)
    write_private_new(work / "handoff.json", encode_document(document))


def discover(root: Path):
    """Only live transport journals. Legacy/incomplete scratch is never executed."""
    candidates = []
    for work in root.iterdir():
        if work.is_symlink() or not work.is_dir() or not (work / "handoff.json").is_file():
            continue
        try:
            private_directory(work)
            if (work / "completed.json").exists():
                continue
            descriptor = parse_json(read_private_result(work / "request.json", work, 70000))
            if descriptor["request"].get("schemaVersion") != "voice-coaching.runpod-analysis-request.v4":
                # Preserve retired transport bytes for operator reconciliation;
                # never relabel an admitted request or rerun its producer.
                logging.getLogger(__name__).warning("retired_handoff_requires_operator_reconciliation")
                continue
            body = validate_analysis_request(descriptor["request"])
            worker = str(UUID(descriptor["workerId"]))
            _require(work.name == body["executionId"], "CANONICAL_DESCRIPTOR")
            if timestamp(body["deadlineAt"]) <= datetime.now(UTC):
                continue
            candidates.append((work, body, worker))
        except Exception:
            logging.getLogger(__name__).warning("canonical_recovery_journal_invalid")
    return candidates


def load_handoff(work, body, worker):
    document = parse_json(read_private_result(work / "handoff.json", work, 2 * 1024 * 1024))
    validate_document(document, "handoff", schema_name="handoff2")
    meta = document["metadata"]
    _require(meta["workerInstanceId"] == worker and meta["requestSha256"] == digest(body)
             and document["handoffSha256"] == digest(meta)
             and meta["projectionSha256"] == digest(document["projection"])
             and all(meta[key] == body[key] for key in
                     ("requestId", "executionId", "analysisId", "recordingId", "contentId")),
             "HANDOFF_DIGEST_CONFLICT")
    projection = document["projection"]
    _require(all(projection[key] == meta[key] for key in
                 ("requestId", "executionId", "analysisId", "recordingId", "contentId", "eventId", "workerInstanceId")),
             "HANDOFF_DIGEST_CONFLICT")
    artifacts = []
    for metadata in meta["artifacts"]:
        kind = metadata["kind"]  # Closed enum validated above; never a caller-supplied path.
        folder = work / "handoff-artifacts"
        if folder.is_dir():
            private_directory(folder)
            raw = read_private_result(folder / kind, work, 16 * 1024 * 1024)
        else:
            # Compatible with earlier small inline journals; no reconstruction of missing evidence.
            raw = base64.b64decode(document["inlineArtifacts"][kind], validate=True)
        _require(len(raw) == metadata["byteSize"] and hashlib.sha256(raw).hexdigest() == metadata["sha256"],
                 "HANDOFF_DIGEST_CONFLICT")
        artifacts.append(TransferArtifact(kind, raw))
    return document, tuple(artifacts)
