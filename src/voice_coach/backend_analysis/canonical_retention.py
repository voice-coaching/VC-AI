"""Retain validated private bytes and assemble a manifest, never a receipt.

The required private journal retains originals, upload checkpoints and manifest.
The worker must still register the manifest with Backend and persist delivery.
This module does not acknowledge callbacks, allocate events, or enable admission.
An upload can finish after cancellation: such an orphan remains private under
the configured retention policy and must never resurrect the execution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import time
from typing import Callable

from .canonical_evidence_store import PrivateB2EvidenceStore
from .canonical_journal_port import CanonicalJournalPort
from .canonical_codec import validate_document as _validate, encode_document as _wire
from .canonical_result import (
    ARTIFACT_SCHEMAS, MAX_ARTIFACT_BYTES, MAX_MANIFEST_BYTES, ArtifactReference,
    CanonicalPreflight, ExecutionAssociation,
)
from .canonical_bridge import _require


MANIFEST_VERSION = "voice-coaching.canonical-evidence-manifest.v1"


@dataclass(frozen=True, slots=True)
class RetainedCanonicalManifest:
    """Version-readback completed; Backend has NOT verified this manifest yet."""

    association: ExecutionAssociation
    artifacts: tuple[ArtifactReference, ...]
    raw: bytes = field(repr=False)
    sha256: str


def retain_canonical_preflight(
    preflight: CanonicalPreflight, *, store: PrivateB2EvidenceStore,
    journal: CanonicalJournalPort,
    deadline_monotonic: float, execution_is_active: Callable[[], bool],
) -> RetainedCanonicalManifest:
    """Upload only preflight-validated originals while the caller owns execution.

    Caller provides a monotonic deadline and a live lease/cancel check. Checks
    bracket each bounded SDK operation; process-level cancellation of an active
    socket remains the executor's responsibility. On partial failure no receipt
    or partial success is returned. Do not retry blindly: reconcile versioned
    artifacts from the execution journal before attempting recovery.
    """
    _require(type(preflight) is CanonicalPreflight, "CANONICAL_PREFLIGHT_REQUIRED")
    _require(type(deadline_monotonic) in (int, float)
             and time.monotonic() < deadline_monotonic < float("inf"),
             "EVIDENCE_DEADLINE_INVALID")

    def fence():
        _require(time.monotonic() < deadline_monotonic, "EVIDENCE_DEADLINE_EXCEEDED")
        _require(execution_is_active() is True, "EVIDENCE_EXECUTION_INACTIVE")

    # Validate the whole set before the first side effect. This is an internal
    # preflight result, not a client-supplied request body or arbitrary file list.
    originals = {
        "CORE": preflight.result.core_bytes,
        "BRIDGE_RESULT": preflight.result.expression_bytes,
        "BINDING": preflight.result.binding_bytes,
        "ASSOCIATION": preflight.result.association_bytes,
    }
    if preflight.result.selection_bytes is not None:
        originals["SELECTION_PROJECTION"] = preflight.result.selection_bytes
    kinds = [item.kind for item in preflight.artifacts]
    _require(len(kinds) == len(set(kinds)) and set(kinds) == set(originals),
             "EVIDENCE_ARTIFACT_SET_INVALID")
    for item in preflight.artifacts:
        _require(item.schema_version == ARTIFACT_SCHEMAS[item.kind]
                 and type(item.raw) is bytes and 0 < len(item.raw) <= MAX_ARTIFACT_BYTES
                 and item.raw == originals[item.kind], "EVIDENCE_PREFLIGHT_MISMATCH")

    fence()
    journal.prepare(preflight)
    references = []
    for item in preflight.artifacts:
        fence()
        stored = journal.reference(preflight.association, item)
        if stored is None:
            journal.begin_upload(preflight.association, item)
            # Do not fence the persistence callback: an upload that returned
            # after cancellation still needs its private immutable version saved.
            fence()
            reference = store.put(
                kind=item.kind, schema_version=item.schema_version, raw=item.raw,
                analysis_id=preflight.association.analysis_id,
                execution_id=str(preflight.association.execution_id),
                on_uploaded=lambda ref, artifact=item: journal.uploaded(preflight.association, artifact, ref),
            )
        else:
            reference = stored.to_document()
            fence()
            store.read_verified(reference, analysis_id=preflight.association.analysis_id,
                                execution_id=str(preflight.association.execution_id))
        journal.verified(preflight.association, item, reference)
        fence()
        references.append(ArtifactReference.from_document(reference))

    document = dict(schemaVersion=MANIFEST_VERSION,
                    association=preflight.association.to_document(),
                    artifacts=[reference.to_document() for reference in references])
    _validate(document, "evidenceManifest", schema_name="control")
    raw = _wire(document)
    _require(len(raw) <= MAX_MANIFEST_BYTES, "EVIDENCE_MANIFEST_TOO_LARGE")
    journal.retain_manifest(preflight.association, raw)
    fence()
    return RetainedCanonicalManifest(preflight.association, tuple(references), raw,
                                     hashlib.sha256(raw).hexdigest())

