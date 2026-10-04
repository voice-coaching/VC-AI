"""Private durable callback outbox; not an execution, lease or receipt authority.

Reserve identity once, serialize once, checkpoint exact bytes, then deliver.
Recovery must not regenerate a result or claim a new worker identity. PREPARING
is deliberately not replayable. The factory/executor must supply live fencing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import math
import re
import time
from uuid import UUID, uuid4

from .canonical_bridge import _require
from .canonical_codec import read_document, validate_document
from .canonical_journal import CanonicalEvidenceJournal
from .canonical_result import MAX_RESULT_BYTES, ResultIdentity
from .http_models import AnalysisInput, MAX_CONTROL_BYTES, digest, parse_json, timestamp


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class CallbackDelivery:
    identity: ResultIdentity
    request: AnalysisInput = field(repr=False)
    deadline: datetime
    raw: bytes = field(repr=False)
    raw_sha256: str
    payload_sha256: str
    claim_id: UUID
    attempts: int
    ack_only: bool


class CanonicalResultOutbox:
    def __init__(self, journal: CanonicalEvidenceJournal, recordings_prefix: str):
        _require(type(journal) is CanonicalEvidenceJournal, 'DELIVERY_JOURNAL_REQUIRED')
        _require(type(recordings_prefix) is str and recordings_prefix.endswith('/'), 'DELIVERY_PREFIX_INVALID')
        self._journal = journal
        self._prefix = recordings_prefix

    def _request(self, raw: bytes):
        _require(type(raw) is bytes and 0 < len(raw) <= MAX_CONTROL_BYTES, 'DELIVERY_REQUEST_SIZE')
        document = parse_json(raw)
        request = AnalysisInput.from_payload(document, self._prefix)
        _require(not request.is_legacy, 'CANONICAL_PROFILE_REQUIRED')
        return document, request

    def reserve(self, request_raw: bytes, *, worker_id: UUID, worker_revision: str,
                pipeline_revision: str) -> ResultIdentity:
        """Internal admitted caller only. Does not start/restart inference or assert a lease."""
        body, request = self._request(request_raw)
        _require(type(worker_id) is UUID, 'DELIVERY_WORKER_INVALID')
        validate_document(worker_revision, 'revision')
        validate_document(pipeline_revision, 'revision')
        execution = str(request.execution_id)
        with self._journal._transaction() as db:
            db.execute('''INSERT OR IGNORE INTO result_deliveries
                (execution_id,request_id,analysis_id,worker_id,event_id,worker_revision,pipeline_revision,
                 request_bytes,request_raw_sha,request_payload_sha,state)
                VALUES (?,?,?,?,?,?,?,?,?,?,'PREPARING')''',
                (execution, str(request.event_id), request.analysis_id, str(worker_id), str(uuid4()),
                 worker_revision, pipeline_revision, request_raw, _sha(request_raw), digest(body)))
            row = self._row(db, request.execution_id)
            _require(bytes(row['request_bytes']) == request_raw and row['worker_id'] == str(worker_id)
                     and row['worker_revision'] == worker_revision and row['pipeline_revision'] == pipeline_revision,
                     'DELIVERY_RESERVATION_CONFLICT')
            return self._identity(row)

    @staticmethod
    def _identity(row):
        return ResultIdentity(UUID(row['event_id']), UUID(row['worker_id']),
                              row['worker_revision'], row['pipeline_revision'])

    def _row(self, db, execution_id: UUID):
        _require(type(execution_id) is UUID, 'DELIVERY_EXECUTION_INVALID')
        row = db.execute('SELECT * FROM result_deliveries WHERE execution_id=?', (str(execution_id),)).fetchone()
        _require(row is not None, 'DELIVERY_NOT_RESERVED')
        raw = bytes(row['request_bytes'])
        body, request = self._request(raw)
        _require(row['request_raw_sha'] == _sha(raw) and row['request_payload_sha'] == digest(body)
                 and row['execution_id'] == str(request.execution_id)
                 and row['request_id'] == str(request.event_id) and row['analysis_id'] == request.analysis_id,
                 'DELIVERY_REQUEST_CONFLICT')
        return row

    def _callback(self, row, raw: bytes):
        document = read_document(raw, MAX_RESULT_BYTES)
        validate_document(document)
        _, request = self._request(bytes(row['request_bytes']))
        identity = self._identity(row)
        _require(all(document[key] == expected for key, expected in {
            'eventId': str(identity.event_id), 'workerInstanceId': str(identity.worker_instance_id),
            'workerRevision': identity.worker_revision, 'pipelineRevision': identity.pipeline_revision,
            'requestId': str(request.event_id), 'executionId': str(request.execution_id),
            'analysisId': request.analysis_id, 'recordingId': request.recording_id, 'contentId': request.content_id,
        }.items()) and document['sourceIdentity']['audioSha256'] == request.audio_sha256
            and document['sourceIdentity']['scriptSha256'] == request.script_sha256, 'DELIVERY_CALLBACK_CONFLICT')
        # The separate JCS digest is for transport idempotency only. Never send its re-encoded bytes.
        return document, digest(parse_json(raw))

    def enqueue(self, execution_id: UUID, raw: bytes) -> None:
        """Checkpoint serializer output, not a replacement for preflight or Backend verification."""
        with self._journal._transaction() as db:
            row = self._row(db, execution_id)
            document, payload_sha = self._callback(row, raw)
            if row['callback_bytes'] is not None:
                _require(bytes(row['callback_bytes']) == raw and row['callback_raw_sha'] == _sha(raw)
                         and row['callback_payload_sha'] == payload_sha, 'DELIVERY_CALLBACK_CONFLICT')
                return
            self._retention(db, row, document)
            db.execute('''UPDATE result_deliveries SET callback_bytes=?,callback_raw_sha=?,callback_payload_sha=?,
                          state='PENDING' WHERE execution_id=? AND state='PREPARING' ''',
                       (raw, _sha(raw), payload_sha, str(execution_id)))

    @staticmethod
    def _retention(db, row, document):
        execution = row['execution_id']
        association = db.execute('SELECT association FROM executions WHERE execution_id=?', (execution,)).fetchone()
        retained = document['retainedEvidence']
        if document['canonical'] is None:
            _require(association is None and retained is None, 'DELIVERY_CORE_CANNOT_BE_DISCARDED')
            return
        _require(association is not None and retained is not None, 'DELIVERY_RETENTION_REQUIRED')
        expected = dict(requestId=row['request_id'], executionId=execution, analysisId=row['analysis_id'],
                        recordingId=document['recordingId'], contentId=document['contentId'],
                        canonicalAnalysisId=document['sourceIdentity']['canonicalAnalysisId'],
                        coreSha256=document['sourceIdentity']['coreSha256'])
        _require(read_document(bytes(association['association'])) == expected, 'DELIVERY_RETENTION_CONFLICT')
        manifest = db.execute('SELECT raw,sha256 FROM manifests WHERE execution_id=?', (execution,)).fetchone()
        receipt = db.execute('SELECT * FROM receipts WHERE execution_id=?', (execution,)).fetchone()
        _require(manifest is not None and receipt is not None and receipt['verified_response'] is not None,
                 'DELIVERY_RETENTION_REQUIRED')
        raw = bytes(manifest['raw'])
        _require(manifest['sha256'] == _sha(raw) == retained['manifestSha256'] == receipt['manifest_sha256']
                 and retained['evidenceReceiptId'] == receipt['receipt_id']
                 and receipt['worker_instance_id'] == row['worker_id'], 'DELIVERY_RETENTION_CONFLICT')
        original = read_document(raw, MAX_CONTROL_BYTES)
        validate_document(original, 'evidenceManifest', schema_name='control')
        _require(original['association'] == expected and original['artifacts'] == retained['artifacts'],
                 'DELIVERY_RETENTION_CONFLICT')
        # This checkpoint is not reconstituted as a TrustedBackendVerifiedReceipt.
        # Backend still independently verifies the originals before applying callback bytes.

    def candidates(self, limit: int = 32) -> tuple[UUID, ...]:
        """Bounded startup inventory; PREPARING never triggers an AI/GPT replay."""
        _require(type(limit) is int and 1 <= limit <= 128, 'DELIVERY_LIMIT_INVALID')
        with self._journal._transaction() as db:
            rows = db.execute('''SELECT execution_id FROM result_deliveries
                WHERE next_attempt_at<=? AND (state='PENDING' OR (state='SENDING' AND claim_until<=?))
                ORDER BY next_attempt_at,execution_id LIMIT ?''', (time.time(), time.time(), limit))
            return tuple(UUID(row['execution_id']) for row in rows)

    def status(self, execution_id: UUID) -> tuple[str, bool]:
        with self._journal._transaction() as db:
            row = self._row(db, execution_id)
            return row['state'], bool(row['ack_only'])

    def claim(self, execution_id: UUID, *, ack_only: bool = False) -> CallbackDelivery | None:
        _require(type(ack_only) is bool, 'DELIVERY_MODE_INVALID')
        now = time.time()
        with self._journal._transaction() as db:
            row = self._row(db, execution_id)
            if row['state'] in ('PREPARING', 'ACKNOWLEDGED', 'STOPPED'):
                return None
            if row['next_attempt_at'] > now or (row['state'] == 'SENDING' and row['claim_until'] > now):
                return None
            ack_only = ack_only or bool(row['ack_only'])
            confirmation_until = row['confirmation_until']
            if ack_only:
                confirmation_until = confirmation_until if confirmation_until is not None else now + 60
                if now >= confirmation_until:
                    db.execute('''UPDATE result_deliveries SET state='STOPPED',claim_id=NULL,claim_until=NULL,
                                  reason='CALLBACK_ACK_UNCONFIRMED' WHERE execution_id=?''', (str(execution_id),))
                    return None
            raw = bytes(row['callback_bytes'])
            _, payload_sha = self._callback(row, raw)
            _require(row['callback_raw_sha'] == _sha(raw) and row['callback_payload_sha'] == payload_sha,
                     'DELIVERY_CALLBACK_CONFLICT')
            body, request = self._request(bytes(row['request_bytes']))
            claim = uuid4()
            db.execute('''UPDATE result_deliveries SET state='SENDING',claim_id=?,claim_until=?,
                          attempts=attempts+1,reason=NULL,ack_only=?,confirmation_until=? WHERE execution_id=?''',
                       (str(claim), now + 30, int(ack_only), confirmation_until, str(execution_id)))
            return CallbackDelivery(self._identity(row), request, timestamp(body['deadlineAt']), raw,
                                    row['callback_raw_sha'], payload_sha, claim, row['attempts'] + 1, ack_only)

    @staticmethod
    def _owned(db, delivery):
        _require(type(delivery) is CallbackDelivery, 'DELIVERY_ATTEMPT_REQUIRED')
        row = db.execute('''SELECT * FROM result_deliveries WHERE execution_id=?
                            AND state='SENDING' AND claim_id=?''',
                         (str(delivery.request.execution_id), str(delivery.claim_id))).fetchone()
        _require(row is not None and row['event_id'] == str(delivery.identity.event_id)
                 and bytes(row['callback_bytes']) == delivery.raw, 'DELIVERY_CLAIM_LOST')

    def acknowledge(self, delivery: CallbackDelivery, ack_raw: bytes) -> None:
        ack = read_document(ack_raw, MAX_CONTROL_BYTES)
        validate_document(ack, 'resultAck', schema_name='control')
        _require(all(ack[k] == v for k, v in {
            'eventId': str(delivery.identity.event_id), 'requestId': str(delivery.request.event_id),
            'executionId': str(delivery.request.execution_id), 'analysisId': delivery.request.analysis_id,
        }.items()) and (not delivery.ack_only or ack['status'] == 'DUPLICATE'), 'DELIVERY_ACK_CONFLICT')
        with self._journal._transaction() as db:
            self._owned(db, delivery)
            db.execute('''UPDATE result_deliveries SET state='ACKNOWLEDGED',ack_bytes=?,claim_id=NULL,
                          claim_until=NULL,reason=NULL WHERE execution_id=?''',
                       (ack_raw, str(delivery.request.execution_id)))

    def release(self, delivery: CallbackDelivery, *, reason: str, retry_after: float | None,
                confirmation_only: bool = False) -> None:
        _require(type(confirmation_only) is bool, 'DELIVERY_MODE_INVALID')
        _require(type(reason) is str and re.fullmatch('[A-Z_]{1,80}', reason) is not None, 'DELIVERY_REASON_INVALID')
        _require(retry_after is None or (type(retry_after) in (float, int)
                 and math.isfinite(retry_after) and 0 <= retry_after <= 60), 'DELIVERY_DELAY_INVALID')
        with self._journal._transaction() as db:
            # A canceled task can race the thread persisting an already received ACK.
            # Never roll that committed terminal checkpoint back into pending work.
            row = db.execute('SELECT state,event_id,callback_bytes FROM result_deliveries WHERE execution_id=?',
                             (str(delivery.request.execution_id),)).fetchone()
            if row is not None and row['state'] == 'ACKNOWLEDGED':
                _require(row['event_id'] == str(delivery.identity.event_id)
                         and bytes(row['callback_bytes']) == delivery.raw, 'DELIVERY_CALLBACK_CONFLICT')
                return
            self._owned(db, delivery)
            db.execute('''UPDATE result_deliveries SET state=?,claim_id=NULL,claim_until=NULL,
                          reason=?,next_attempt_at=?,ack_only=MAX(ack_only,?) WHERE execution_id=?''',
                       ('STOPPED' if retry_after is None else 'PENDING', reason,
                        0 if retry_after is None else time.time() + retry_after, int(confirmation_only),
                        str(delivery.request.execution_id)))
