"""Private durable evidence checkpoints. Never an admission/lease/receipt authority.

Linux local persistent filesystem only (not /run, NFS or a shared public volume).
The deployment owner must provision and attest the mount separately. Raw private
bytes are retained here until B2 retention and delivery ownership are resolved;
there is deliberately no deletion, auto-reupload, or inference replay method.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import sys
from uuid import UUID

from .canonical_bridge import _require
from .canonical_filesystem import require_local_durable_mount
from .canonical_codec import (
    read_document as _read_json, validate_document as _validate, encode_document as _wire,
)
from .canonical_result import (
    ArtifactReference, CanonicalPreflight, ExecutionAssociation,
    MAX_MANIFEST_BYTES, PrivateArtifact,
)


class CanonicalEvidenceJournal:
    """Record upload intent BEFORE PUT and version reference BEFORE readback.

    A crash in UPLOADING is an unknown outcome. The caller must stop and arrange
    operator reconciliation; a new PUT is not an automatic recovery operation.
    UPLOADED can recover by reading the stored exact version, without another PUT.
    Backend verification remains external. CanonicalResultOutbox uses the same
    durable transaction boundary for reserved identity and immutable callback bytes.
    """

    def __init__(self, root: Path):
        root = Path(root)
        _require(sys.platform == 'linux' and root.is_absolute(), 'JOURNAL_LINUX_PATH_REQUIRED')
        _require(root.exists() and root.resolve() == root
                 and not root.is_relative_to('/run')
                 and not root.is_relative_to('/tmp'), 'JOURNAL_PERSISTENT_ROOT_REQUIRED')
        require_local_durable_mount(root)
        info = root.lstat()
        _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
                 and stat.S_IMODE(info.st_mode) == 0o700, 'JOURNAL_ROOT_PERMISSIONS')
        self._path = root / 'canonical-evidence.sqlite3'
        try:
            descriptor = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        with self._transaction() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            _require(version in (0, 1, 2, 3), 'JOURNAL_VERSION_UNSUPPORTED')
            db.execute('''CREATE TABLE IF NOT EXISTS executions (
                execution_id TEXT PRIMARY KEY, association BLOB NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS artifacts (
                execution_id TEXT NOT NULL REFERENCES executions(execution_id),
                kind TEXT NOT NULL, schema_version TEXT NOT NULL,
                sha256 TEXT NOT NULL, byte_size INTEGER NOT NULL,
                raw BLOB NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('PREPARED','UPLOADING','UPLOADED','VERIFIED')),
                reference BLOB,
                PRIMARY KEY(execution_id,kind),
                CHECK(byte_size = length(raw)),
                CHECK((state IN ('PREPARED','UPLOADING') AND reference IS NULL)
                    OR (state IN ('UPLOADED','VERIFIED') AND reference IS NOT NULL)))''')
            db.execute('''CREATE TABLE IF NOT EXISTS manifests (
                execution_id TEXT PRIMARY KEY REFERENCES executions(execution_id),
                raw BLOB NOT NULL, sha256 TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS receipts (
                execution_id TEXT PRIMARY KEY REFERENCES executions(execution_id),
                receipt_id TEXT NOT NULL UNIQUE, worker_instance_id TEXT NOT NULL,
                manifest_sha256 TEXT NOT NULL, verified_response BLOB)''')
            db.execute('''CREATE TABLE IF NOT EXISTS result_deliveries (
                execution_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                analysis_id INTEGER NOT NULL, worker_id TEXT NOT NULL, event_id TEXT NOT NULL UNIQUE,
                worker_revision TEXT NOT NULL, pipeline_revision TEXT NOT NULL,
                request_bytes BLOB NOT NULL, request_raw_sha TEXT NOT NULL, request_payload_sha TEXT NOT NULL,
                callback_bytes BLOB, callback_raw_sha TEXT, callback_payload_sha TEXT,
                state TEXT NOT NULL CHECK(state IN ('PREPARING','PENDING','SENDING','ACKNOWLEDGED','STOPPED')),
                attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
                claim_id TEXT, claim_until REAL, next_attempt_at REAL NOT NULL DEFAULT 0,
                ack_only INTEGER NOT NULL DEFAULT 0 CHECK(ack_only IN (0,1)), confirmation_until REAL,
                ack_bytes BLOB, reason TEXT,
                CHECK((callback_bytes IS NULL AND state='PREPARING'
                       AND callback_raw_sha IS NULL AND callback_payload_sha IS NULL)
                    OR (callback_bytes IS NOT NULL AND state<>'PREPARING'
                        AND callback_raw_sha IS NOT NULL AND callback_payload_sha IS NOT NULL)),
                CHECK((state='SENDING' AND claim_id IS NOT NULL AND claim_until IS NOT NULL)
                    OR (state<>'SENDING' AND claim_id IS NULL AND claim_until IS NULL)),
                CHECK((state='ACKNOWLEDGED' AND ack_bytes IS NOT NULL)
                    OR (state<>'ACKNOWLEDGED' AND ack_bytes IS NULL)))''')
            db.execute('PRAGMA user_version=3')

    @contextmanager
    def _transaction(self):
        info = self._path.lstat()
        _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                 and stat.S_IMODE(info.st_mode) == 0o600, 'JOURNAL_FILE_PERMISSIONS')
        db = sqlite3.connect(str(self._path), timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            # DELETE rollback journal + FULL fsync: do not inherit unsafe settings.
            mode = db.execute('PRAGMA journal_mode=DELETE').fetchone()[0]
            _require(mode == 'delete', 'JOURNAL_DURABILITY_UNAVAILABLE')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.execute('COMMIT')
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise
        finally:
            db.close()

    @staticmethod
    def _execution(db, association: ExecutionAssociation):
        execution = str(association.execution_id)
        row = db.execute('SELECT association FROM executions WHERE execution_id=?', (execution,)).fetchone()
        _require(row is not None and bytes(row['association']) == _wire(association.to_document()),
                 'JOURNAL_ASSOCIATION_CONFLICT')
        return execution

    @staticmethod
    def _artifact(db, execution: str, item: PrivateArtifact):
        row = db.execute('SELECT * FROM artifacts WHERE execution_id=? AND kind=?',
                         (execution, item.kind)).fetchone()
        _require(row is not None and row['schema_version'] == item.schema_version
                 and row['sha256'] == hashlib.sha256(item.raw).hexdigest()
                 and row['byte_size'] == len(item.raw) and bytes(row['raw']) == item.raw,
                 'JOURNAL_ARTIFACT_CONFLICT')
        return row

    def prepare(self, preflight: CanonicalPreflight) -> None:
        """Commit the complete preflight original set before the first upload."""
        _require(type(preflight) is CanonicalPreflight, 'CANONICAL_PREFLIGHT_REQUIRED')
        association = preflight.association
        execution = str(association.execution_id)
        raw = _wire(association.to_document())
        with self._transaction() as db:
            db.execute('INSERT OR IGNORE INTO executions VALUES (?,?)', (execution, raw))
            self._execution(db, association)
            for item in preflight.artifacts:
                db.execute('''INSERT OR IGNORE INTO artifacts
                    (execution_id,kind,schema_version,sha256,byte_size,raw,state)
                    VALUES (?,?,?,?,?,?,'PREPARED')''',
                    (execution, item.kind, item.schema_version, hashlib.sha256(item.raw).hexdigest(),
                     len(item.raw), item.raw))
                self._artifact(db, execution, item)
            stored = {row[0] for row in db.execute('SELECT kind FROM artifacts WHERE execution_id=?', (execution,))}
            _require(stored == {item.kind for item in preflight.artifacts}, 'JOURNAL_ARTIFACT_SET_CONFLICT')

    def reference(self, association: ExecutionAssociation, item: PrivateArtifact) -> ArtifactReference | None:
        with self._transaction() as db:
            execution = self._execution(db, association)
            row = self._artifact(db, execution, item)
            _require(row['state'] != 'UPLOADING', 'EVIDENCE_UPLOAD_OUTCOME_UNKNOWN')
            if row['state'] == 'PREPARED':
                return None
            reference = ArtifactReference.from_document(_read_json(bytes(row['reference'])))
            _require(reference.kind == item.kind and reference.schema_version == item.schema_version
                     and reference.sha256 == row['sha256'] and reference.byte_size == len(item.raw),
                     'JOURNAL_REFERENCE_CONFLICT')
            return reference

    def begin_upload(self, association: ExecutionAssociation, item: PrivateArtifact) -> None:
        with self._transaction() as db:
            execution = self._execution(db, association)
            row = self._artifact(db, execution, item)
            _require(row['state'] == 'PREPARED', 'EVIDENCE_UPLOAD_ALREADY_STARTED')
            db.execute("UPDATE artifacts SET state='UPLOADING' WHERE execution_id=? AND kind=?",
                       (execution, item.kind))

    def uploaded(self, association: ExecutionAssociation, item: PrivateArtifact, reference: dict) -> None:
        parsed = ArtifactReference.from_document(reference)
        _require(parsed.kind == item.kind and parsed.schema_version == item.schema_version
                 and parsed.sha256 == hashlib.sha256(item.raw).hexdigest()
                 and parsed.byte_size == len(item.raw), 'JOURNAL_REFERENCE_CONFLICT')
        with self._transaction() as db:
            execution = self._execution(db, association)
            row = self._artifact(db, execution, item)
            _require(row['state'] == 'UPLOADING', 'JOURNAL_UPLOAD_STATE_INVALID')
            db.execute("UPDATE artifacts SET state='UPLOADED',reference=? WHERE execution_id=? AND kind=?",
                       (_wire(reference), execution, item.kind))

    def verified(self, association: ExecutionAssociation, item: PrivateArtifact, reference: dict) -> None:
        with self._transaction() as db:
            execution = self._execution(db, association)
            row = self._artifact(db, execution, item)
            _require(row['state'] in ('UPLOADED', 'VERIFIED')
                     and bytes(row['reference']) == _wire(reference), 'JOURNAL_READBACK_STATE_INVALID')
            db.execute("UPDATE artifacts SET state='VERIFIED' WHERE execution_id=? AND kind=?",
                       (execution, item.kind))

    def retain_manifest(self, association: ExecutionAssociation, raw: bytes) -> None:
        manifest = _read_json(raw, MAX_MANIFEST_BYTES)
        _validate(manifest, 'evidenceManifest', schema_name='control')
        _require(manifest['association'] == association.to_document(), 'JOURNAL_MANIFEST_CONFLICT')
        with self._transaction() as db:
            execution = self._execution(db, association)
            rows = db.execute('SELECT kind,state,reference FROM artifacts WHERE execution_id=?', (execution,)).fetchall()
            _require(all(row['state'] == 'VERIFIED' for row in rows), 'JOURNAL_READBACK_INCOMPLETE')
            stored = {row['kind']: _read_json(bytes(row['reference'])) for row in rows}
            expected = {item['kind']: item for item in manifest['artifacts']}
            _require(len(expected) == len(manifest['artifacts']) and expected == stored,
                     'JOURNAL_MANIFEST_CONFLICT')
            sha = hashlib.sha256(raw).hexdigest()
            db.execute('INSERT OR IGNORE INTO manifests VALUES (?,?,?)', (execution, raw, sha))
            row = db.execute('SELECT raw,sha256 FROM manifests WHERE execution_id=?', (execution,)).fetchone()
            _require(bytes(row['raw']) == raw and row['sha256'] == sha, 'JOURNAL_MANIFEST_CONFLICT')

    def receipt_id(self, association: ExecutionAssociation, worker: UUID, manifest: bytes) -> UUID | None:
        """Recover registration identity only; a local record is never VERIFIED authority."""
        with self._transaction() as db:
            execution = self._execution(db, association)
            self._manifest(db, execution, manifest)
            row = db.execute('SELECT * FROM receipts WHERE execution_id=?', (execution,)).fetchone()
            if row is None:
                return None
            _require(row['worker_instance_id'] == str(worker)
                     and row['manifest_sha256'] == hashlib.sha256(manifest).hexdigest(),
                     'JOURNAL_RECEIPT_CONFLICT')
            return UUID(row['receipt_id'])

    @staticmethod
    def _manifest(db, execution, raw):
        row = db.execute('SELECT raw,sha256 FROM manifests WHERE execution_id=?', (execution,)).fetchone()
        _require(row is not None and bytes(row['raw']) == raw
                 and row['sha256'] == hashlib.sha256(raw).hexdigest(), 'JOURNAL_MANIFEST_CONFLICT')

    def received_receipt(self, association: ExecutionAssociation, worker: UUID,
                         manifest: bytes, response: dict) -> None:
        """Checkpoint an authenticated response after caller binding checks, not a local receipt issuer."""
        _validate(response, 'evidenceReceipt', schema_name='control')
        _require(response['association'] == association.to_document()
                 and response['workerInstanceId'] == str(worker)
                 and response['manifestSha256'] == hashlib.sha256(manifest).hexdigest(),
                 'JOURNAL_RECEIPT_CONFLICT')
        with self._transaction() as db:
            execution = self._execution(db, association)
            self._manifest(db, execution, manifest)
            db.execute('''INSERT OR IGNORE INTO receipts
                (execution_id,receipt_id,worker_instance_id,manifest_sha256) VALUES (?,?,?,?)''',
                (execution, response['receiptId'], str(worker), response['manifestSha256']))
            row = db.execute('SELECT * FROM receipts WHERE execution_id=?', (execution,)).fetchone()
            _require(row is not None and row['receipt_id'] == response['receiptId']
                     and row['worker_instance_id'] == str(worker)
                     and row['manifest_sha256'] == response['manifestSha256'], 'JOURNAL_RECEIPT_CONFLICT')
            if response['status'] == 'VERIFIED' and row['verified_response'] is None:
                # Keep the first authenticated VERIFIED response; do not update its serverTime on polling.
                db.execute('UPDATE receipts SET verified_response=? WHERE execution_id=?',
                           (_wire(response), execution))
