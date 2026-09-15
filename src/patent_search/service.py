"""Single-user local job service with durable states and cancellation fencing."""
from __future__ import annotations

import hashlib
import json
import queue
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .engine import SearchEngine


class ServiceError(Exception):
    def __init__(self, code: str, status: int = 409):
        self.code, self.status = code, status
        super().__init__(code)


class JobService:
    def __init__(self, root: Path, engine: SearchEngine):
        self.root, self.engine = root, engine
        self.db_path = root / "papers.sqlite3"
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.wake = threading.Event()
        self._filelock = (root / ".service.lock").open("a+b")
        self._filelock.seek(0)
        if __import__("os").name == "nt":
            import msvcrt
            msvcrt.locking(self._filelock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(self._filelock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS papers(
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, text TEXT NOT NULL,
                    version INTEGER NOT NULL, features TEXT NOT NULL, confirmed INTEGER NOT NULL,
                    digest TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(
                    id TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                    idem TEXT UNIQUE NOT NULL, digest TEXT NOT NULL, request TEXT NOT NULL,
                    status TEXT NOT NULL, result TEXT, error TEXT, created REAL NOT NULL,
                    updated REAL NOT NULL);
            """)
            if "source_json" not in {r[1] for r in db.execute("PRAGMA table_info(papers)")}:
                db.execute("ALTER TABLE papers ADD COLUMN source_json TEXT NOT NULL DEFAULT '{}'")
            # A crash never repeats expensive external/model work invisibly.
            db.execute("UPDATE jobs SET status='FAILED',error='WORKER_RESTARTED',updated=? WHERE status IN ('RUNNING','QUEUED')", (time.time(),))
        self.thread = threading.Thread(target=self.work, daemon=True, name="patent-search-worker")
        self.thread.start()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA secure_delete=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def add_paper(self, title: str, text: str, provenance: dict | None = None) -> dict:
        if not text.strip():
            raise ServiceError("TEXT_EXTRACTION_EMPTY", 422)
        pid = str(uuid.uuid4())
        # Actual source sentences for review, never hallucinated technical features.
        sentences = [x.strip() for x in __import__("re").split(r"(?<=[.!?。])\s+|\n\n+", text) if x.strip()]
        features = [x[:1500] for x in sentences[:8]]
        with self.lock, self.connect() as db:
            db.execute("INSERT INTO papers VALUES(?,?,?,?,?,?,?,?,?)", (pid, title, text, 1, json.dumps(features), 0,
                hashlib.sha256(text.encode()).hexdigest(), time.time(), json.dumps(provenance or {"kind": "user_text"})))
        return self.paper(pid)

    def paper(self, pid: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM papers WHERE id=?", (pid,)).fetchone()
        if row is None:
            raise ServiceError("PAPER_NOT_FOUND", 404)
        out = dict(row)
        out["paperVersion"] = out.pop("version")
        out["features"] = json.loads(out["features"])
        out["source"] = json.loads(out.pop("source_json"))
        out["featureEvidence"] = []
        for text in out["features"]:
            start = out["text"].find(text)
            out["featureEvidence"].append({"text": text, "start": start if start >= 0 else None,
                "end": start+len(text) if start >= 0 else None,
                "origin": "source_excerpt" if start >= 0 else "user_added_not_source_evidence"})
        out["state"] = "CONFIRMED" if out["confirmed"] else "READY_FOR_REVIEW"
        out["featureExtraction"] = "SOURCE_SENTENCE_BASELINE_USER_REVIEW_REQUIRED"
        return out

    def features(self, pid: str, version: int, features: list[str] | None, confirm: bool = False) -> dict:
        with self.lock, self.connect() as db:
            row = db.execute("SELECT * FROM papers WHERE id=?", (pid,)).fetchone()
            if row is None:
                raise ServiceError("PAPER_NOT_FOUND", 404)
            if row["version"] != version:
                raise ServiceError("PAPER_VERSION_CONFLICT")
            if confirm:
                db.execute("UPDATE papers SET confirmed=1 WHERE id=?", (pid,))
            else:
                db.execute("UPDATE papers SET version=version+1,features=?,confirmed=0 WHERE id=?", (json.dumps(features), pid))
        return self.paper(pid)

    def submit(self, body: dict, idem: str) -> dict:
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with self.lock, self.connect() as db:
            old = db.execute("SELECT * FROM jobs WHERE idem=?", (idem,)).fetchone()
            if old:
                if old["digest"] != digest:
                    raise ServiceError("IDEMPOTENCY_CONFLICT")
                return self.job(old["id"])
            paper = self.paper(body["paperId"])
            if body["paperVersion"] != paper["paperVersion"]:
                raise ServiceError("PAPER_VERSION_CONFLICT")
            if not paper["confirmed"]:
                raise ServiceError("FEATURES_NOT_CONFIRMED")
            pending = db.execute("SELECT count(*) FROM jobs WHERE status IN ('QUEUED','RUNNING')").fetchone()[0]
            if pending >= 4:
                raise ServiceError("CAPACITY_EXCEEDED", 429)
            pinned = {**body, "summary": body.get("queryText") or paper["text"], "features": paper["features"],
                      "indexRevision": self.engine.manifest["indexRevision"]}
            jid = str(uuid.uuid4())
            now = time.time()
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)", (jid, paper["id"], idem, digest,
                json.dumps(pinned), "QUEUED", None, None, now, now))
        self.wake.set()
        return self.job(jid)

    def job(self, jid: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if row is None:
            raise ServiceError("JOB_NOT_FOUND", 404)
        return {"jobId": row["id"], "paperId": row["paper_id"], "status": row["status"],
                "errorCode": row["error"], "created": row["created"], "updated": row["updated"],
                "result": json.loads(row["result"]) if row["result"] else None}

    def list_jobs(self, pid: str) -> list[dict]:
        self.paper(pid)
        with self.connect() as db:
            ids = [x[0] for x in db.execute("SELECT id FROM jobs WHERE paper_id=? ORDER BY created DESC", (pid,))]
        return [self.job(x) for x in ids]

    def cancel(self, jid: str) -> dict:
        self.job(jid)
        with self.lock, self.connect() as db:
            db.execute("UPDATE jobs SET status='CANCELLED',updated=? WHERE id=? AND status IN ('QUEUED','RUNNING')", (time.time(), jid))
        return self.job(jid)

    def delete(self, pid: str) -> None:
        self.paper(pid)
        with self.lock, self.connect() as db:
            db.execute("DELETE FROM papers WHERE id=?", (pid,))
        # No source PDF files are retained. CAS result write cannot recreate rows.
        with self.connect() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def interrupted(self, jid: str, deadline: float) -> bool:
        if self.closed.is_set() or time.time() >= deadline:
            return True
        with self.connect() as db:
            row = db.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
        return row is None or row["status"] != "RUNNING"

    def work(self):
        while not self.closed.is_set():
            with self.lock, self.connect() as db:
                row = db.execute("SELECT * FROM jobs WHERE status='QUEUED' ORDER BY created LIMIT 1").fetchone()
                if row:
                    db.execute("UPDATE jobs SET status='RUNNING',updated=? WHERE id=? AND status='QUEUED'", (time.time(), row["id"]))
            if not row:
                self.wake.wait(1)
                self.wake.clear()
                continue
            jid, payload = row["id"], json.loads(row["request"])
            deadline = row["created"]+180
            try:
                if payload["indexRevision"] != self.engine.manifest["indexRevision"]:
                    raise ValueError("PINNED_INDEX_UNAVAILABLE")
                result = self.engine.search(payload["summary"], payload["features"],
                    countries=payload.get("countries"), date_from=payload.get("dateFrom"), date_to=payload.get("dateTo"),
                    mode=payload.get("mode", "hybrid_semantic"), rerank=payload.get("rerank", False),
                    cancel=lambda: self.interrupted(jid, deadline))
                with self.lock, self.connect() as db:
                    db.execute("UPDATE jobs SET status=?,result=?,updated=? WHERE id=? AND status='RUNNING'", (
                        result["status"], json.dumps(result), time.time(), jid))
            except Exception as error:
                code = "DEADLINE_EXCEEDED" if time.time() >= deadline else "SEARCH_FAILED"
                if isinstance(error, ValueError) and str(error) in {"PINNED_INDEX_UNAVAILABLE", "ENCODER_INPUT_TOO_LONG", "QUERY_TOO_LONG_SELECT_SHORTER_PASSAGES"}:
                    code = str(error)
                with self.lock, self.connect() as db:
                    db.execute("UPDATE jobs SET status='FAILED',error=?,updated=? WHERE id=? AND status='RUNNING'", (code, time.time(), jid))

    def close(self):
        self.closed.set()
        self.wake.set()
        self.thread.join(timeout=10)
        if not self.thread.is_alive():
            self._filelock.close()
