"""Process-bound authenticated Backend journal; no DB credentials or local SQLite.

Methods are synchronous for bounded storage stages. The async executor MUST run
them off its heartbeat loop. No automatic network retries, inference replay, or
unknown-outcome PUT retries. Late checkpoint writes cannot read/resurrect jobs.
"""
from __future__ import annotations

import hashlib
import math
import asyncio
from typing import Callable
from uuid import UUID
import httpx

from .canonical_bridge import CanonicalIntegrationError, _require
from .canonical_codec import encode_document, read_document, validate_document
from .canonical_result import CanonicalPreflight, ExecutionAssociation, PrivateArtifact, ArtifactReference, ResultIdentity
from .http_models import AnalysisInput, parse_json
from .http_settings import HttpSettings


class BackendJournalFailure(CanonicalIntegrationError):
    def __init__(self, status: int):
        super().__init__('BACKEND_JOURNAL_UNAVAILABLE' if status >= 500 else 'BACKEND_JOURNAL_REJECTED')
        self.status = status


class BackendCanonicalJournal:
    def __init__(self, settings: HttpSettings, *, request_raw: bytes, worker_id: UUID,
                 remaining_seconds: Callable[[], float], execution_is_active: Callable[[], bool]):
        _require(type(settings) is HttpSettings and type(worker_id) is UUID
                 and type(request_raw) is bytes and 0 < len(request_raw) <= 65536,
                 'BACKEND_JOURNAL_CONFIGURATION')
        self.request = AnalysisInput.from_payload(parse_json(request_raw), settings.prefix)
        _require(not self.request.is_legacy, 'CANONICAL_PROFILE_REQUIRED')
        self.worker_id, self._raw = worker_id, request_raw
        self._remaining, self._active = remaining_seconds, execution_is_active
        self._base = f'/api/internal/ai/analyses/{self.request.analysis_id}/journal/{self.request.execution_id}'
        self._base_url = settings.callback_base
        self._headers = {'Authorization': 'Bearer ' + settings.callback_token,
                         'X-Worker-Instance-Id': str(worker_id), 'Accept-Encoding': 'identity'}
        self._closed = False

    def close(self):
        self._closed = True

    def _call(self, method: str, suffix: str, raw: bytes | None = None, *,
              content_type='application/json', late=False, headers=None):
        budget = 10.0 if late else self._remaining()
        _require(not self._closed and type(budget) in (int, float) and math.isfinite(budget) and budget > 0
                 and (late or self._active() is True), 'EVIDENCE_EXECUTION_INACTIVE')
        async def request():
            async with httpx.AsyncClient(base_url=self._base_url, headers=self._headers,
                    follow_redirects=False, trust_env=False, timeout=httpx.Timeout(10, connect=3)) as client:
                async with client.stream(method, self._base + suffix, content=raw,
                        headers={'Content-Type': content_type, **(headers or {})}) as reply:
                    _require(reply.headers.get('Content-Encoding', 'identity') == 'identity', 'JOURNAL_CONTENT_ENCODING')
                    body = bytearray()
                    async for chunk in reply.aiter_raw():
                        body.extend(chunk)
                        _require(len(body) <= 65536, 'JOURNAL_RESPONSE_SIZE')
                    if reply.status_code not in (200, 204):
                        # Never log server diagnostics or trust a failure as successful commit.
                        raise BackendJournalFailure(reply.status_code)
                    if reply.status_code == 204:
                        _require(not body, 'JOURNAL_RESPONSE_INVALID')
                        return None
                    _require(reply.headers.get('Content-Type', '').split(';')[0].strip() == 'application/json',
                             'JOURNAL_CONTENT_TYPE')
                    return read_document(bytes(body), 65536)
        async def bounded():
            return await asyncio.wait_for(request(), min(10, budget))
        try:
            return asyncio.run(bounded())  # Private storage-stage thread, never heartbeat's loop.
        except (httpx.HTTPError, TimeoutError):
            # In particular /start and /begin may already be committed. Never auto-repeat.
            raise BackendJournalFailure(503) from None

    def _snapshot(self, value):
        # Identify the upstream wire boundary without exposing private schema instances.
        # Keep schema/configuration failures distinct from a malformed Backend reply.
        try:
            validate_document(value, 'journalSnapshot', schema_name='journal')
        except CanonicalIntegrationError as error:
            if str(error) == 'CANONICAL_SCHEMA_INVALID':
                raise CanonicalIntegrationError('BACKEND_JOURNAL_RESPONSE_INVALID') from None
            raise
        for key, expected in {'requestId': str(self.request.event_id), 'executionId': str(self.request.execution_id),
                              'analysisId': self.request.analysis_id, 'workerInstanceId': str(self.worker_id)}.items():
            _require(value[key] == expected, 'JOURNAL_BINDING_MISMATCH')
        return value

    def status(self):
        # Read-only current-visibility recovery may outlive the lease; not a new authority.
        return self._snapshot(self._call('GET', '', late=True))

    def reserve(self, *, worker_revision: str, pipeline_revision: str) -> ResultIdentity:
        validate_document(worker_revision, 'revision')
        validate_document(pipeline_revision, 'revision')
        value = self._snapshot(self._call('POST', '/reserve', self._raw,
            headers={'X-Worker-Revision': worker_revision, 'X-Pipeline-Revision': pipeline_revision}))
        _require(value['workerRevision'] == worker_revision and value['pipelineRevision'] == pipeline_revision,
                 'JOURNAL_REVISION_MISMATCH')
        return ResultIdentity(UUID(value['eventId']), self.worker_id, worker_revision, pipeline_revision)

    def start_producer(self, request: AnalysisInput):
        _require(request == self.request, 'JOURNAL_BINDING_MISMATCH')
        self._call('POST', '/start')

    def _association(self, association: ExecutionAssociation, snapshot):
        _require(snapshot['association'] == association.to_document(), 'JOURNAL_ASSOCIATION_CONFLICT')

    @staticmethod
    def _metadata(item: PrivateArtifact):
        _require(type(item) is PrivateArtifact and type(item.raw) is bytes, 'JOURNAL_ARTIFACT_INVALID')
        value = dict(kind=item.kind, schemaVersion=item.schema_version,
                     sha256=hashlib.sha256(item.raw).hexdigest(), byteSize=len(item.raw))
        validate_document(value, 'journalMetadata', schema_name='journal')
        return value

    def prepare(self, preflight: CanonicalPreflight):
        _require(type(preflight) is CanonicalPreflight, 'CANONICAL_PREFLIGHT_REQUIRED')
        value = dict(association=preflight.association.to_document(),
                     artifacts=[self._metadata(item) for item in preflight.artifacts])
        validate_document(value, 'journalPrepare', schema_name='journal')
        self._call('POST', '/prepare', encode_document(value))
        for item in preflight.artifacts:
            self._call('PUT', f'/artifacts/{item.kind}/stage', item.raw, content_type='application/octet-stream')
        snapshot = self.status()
        self._association(preflight.association, snapshot)
        _require({i['metadata']['kind'] for i in snapshot['artifacts']} == {i.kind for i in preflight.artifacts}
                 and all(i['state'] != 'DECLARED' for i in snapshot['artifacts']), 'JOURNAL_STAGING_INCOMPLETE')

    def reference(self, association: ExecutionAssociation, item: PrivateArtifact):
        snapshot = self.status()
        self._association(association, snapshot)
        rows = [row for row in snapshot['artifacts'] if row['metadata'] == self._metadata(item)]
        _require(len(rows) == 1, 'JOURNAL_ARTIFACT_CONFLICT')
        row = rows[0]
        _require(row['state'] != 'UPLOADING', 'EVIDENCE_UPLOAD_OUTCOME_UNKNOWN')
        if row['state'] == 'PREPARED':
            return None
        _require(row['state'] in ('UPLOADED', 'VERIFIED'), 'JOURNAL_STAGING_INCOMPLETE')
        return ArtifactReference.from_document(row['reference'])

    def begin_upload(self, association, item):
        self._metadata(item)
        self._association(association, self.status())
        self._call('POST', f'/artifacts/{item.kind}/begin')

    def uploaded(self, association, item, reference):
        self._checkpoint(association, item, reference, 'uploaded')

    def verified(self, association, item, reference):
        self._checkpoint(association, item, reference, 'readback')

    def _checkpoint(self, association, item, reference, action):
        _require(association.execution_id == self.request.execution_id
                 and association.request_id == self.request.event_id, 'JOURNAL_ASSOCIATION_CONFLICT')
        parsed = ArtifactReference.from_document(reference)
        _require({k: reference[k] for k in self._metadata(item)} == self._metadata(item), 'JOURNAL_REFERENCE_CONFLICT')
        self._call('POST', f'/artifacts/{parsed.kind}/{action}', encode_document(reference), late=True)

    def retain_manifest(self, association, raw):
        doc = read_document(raw, 65536)
        validate_document(doc, 'evidenceManifest', schema_name='control')
        _require(doc['association'] == association.to_document(), 'JOURNAL_MANIFEST_CONFLICT')
        self._call('POST', '/manifest', raw)

    def receipt_id(self, association, worker, manifest):
        snapshot = self.status()
        self._association(association, snapshot)
        _require(worker == self.worker_id and snapshot['manifestSha256'] == hashlib.sha256(manifest).hexdigest(),
                 'JOURNAL_RECEIPT_CONFLICT')
        return UUID(snapshot['receiptId']) if snapshot['receiptId'] is not None else None

    def received_receipt(self, association, worker, manifest, response):
        # The Backend receipt table is already durable. A worker cannot POST a
        # self-declared VERIFIED checkpoint; keep using the authenticated live receipt API.
        validate_document(response, 'evidenceReceipt', schema_name='control')
        _require(response['association'] == association.to_document()
                 and response['workerInstanceId'] == str(worker)
                 and response['manifestSha256'] == hashlib.sha256(manifest).hexdigest()
                 and self.receipt_id(association, worker, manifest) == UUID(response['receiptId']),
                 'JOURNAL_RECEIPT_CONFLICT')
