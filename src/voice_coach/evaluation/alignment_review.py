"""Local-only manual alignment review with privacy-safe persisted results."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import statistics
import threading
from typing import Any, Mapping
import unicodedata
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator


ITEM_HASH_NAMESPACE = "voice-coach.aihub71490.alignment-review-item.v1"
REVIEWER_HASH_NAMESPACE = "voice-coach.alignment-reviewer.v1"
STATE_SCHEMA_VERSION = "voice_coach.alignment_review_state.v1"
REPORT_SCHEMA_VERSION = "voice_coach.alignment_review_report.v1"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

LEGACY_REVIEW_FIELDS = frozenset(
    {
        "reviewer_id_sha256",
        "alignment_correct",
        "maximum_boundary_error_ms",
        "notes",
        "updated_at_utc",
    }
)
CURRENT_REVIEW_FIELDS = LEGACY_REVIEW_FIELDS | {"incorrect_interval_count"}
REQUIRED_WORKSHEET_COLUMNS = {
    "audit_index",
    "utterance_id",
    "split",
    "l1",
    "gender",
    "proficiency",
    "error_presence",
    "duration_seconds",
    "target_phone_count",
    "realized_phone_count",
    "audio_path",
    "alignment_detail_line",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _namespaced_hash(namespace: str, value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized:
        raise ValueError("identifier must not be empty")
    return hashlib.sha256(f"{namespace}:{normalized}".encode("utf-8")).hexdigest()


def hash_item_id(raw_utterance_id: str) -> str:
    """Return the only utterance identifier allowed outside the server."""

    return _namespaced_hash(ITEM_HASH_NAMESPACE, raw_utterance_id)


def hash_reviewer_id(raw_reviewer_id: str) -> str:
    """Hash a local reviewer pseudonym before browser response or storage."""

    return _namespaced_hash(REVIEWER_HASH_NAMESPACE, raw_reviewer_id)


def atomic_write_text(path: Path, text: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part-{uuid4().hex}")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
    )


def resolve_restricted_path(dataset_root: Path, relative_path: object) -> Path:
    """Resolve a worksheet locator while blocking absolute and symlink escapes."""

    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("audio_path must be a non-empty relative path")
    relative = Path(relative_path)
    if relative.is_absolute():
        raise ValueError("audio_path must be relative")
    root = dataset_root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("audio_path escapes dataset root") from exc
    if not candidate.is_file():
        raise ValueError("audio_path is not a file")
    return candidate


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def strict_read_json(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_strict_object,
    )
    if not isinstance(value, dict):
        raise ValueError("JSON document must be an object")
    return value


@dataclass(frozen=True, slots=True)
class AuditItem:
    audit_index: int
    item_sha256: str
    raw_utterance_id: str
    split: str
    l1: str
    gender: str
    proficiency: str
    error_presence: bool
    duration_seconds: float
    target_phone_count: int
    realized_phone_count: int
    audio_path: Path
    target_phone_intervals: list[dict[str, Any]]
    realized_phone_intervals: list[dict[str, Any]]
    automatic_phone_alignments: list[dict[str, Any]]

    def public_summary(self, *, reviewed: bool) -> dict[str, Any]:
        return {
            "audit_index": self.audit_index,
            "item_sha256": self.item_sha256,
            "split": self.split,
            "l1": self.l1,
            "gender": self.gender,
            "proficiency": self.proficiency,
            "error_presence": self.error_presence,
            "duration_seconds": self.duration_seconds,
            "target_phone_count": self.target_phone_count,
            "realized_phone_count": self.realized_phone_count,
            "reviewed": reviewed,
        }


class ReviewDataset:
    """Restricted locator map; public views emit no raw ID or path."""

    def __init__(
        self,
        *,
        worksheet: Path,
        details: Path,
        dataset_root: Path,
        items: list[AuditItem],
    ) -> None:
        self.worksheet_sha256 = sha256_file(worksheet)
        self.details_sha256 = sha256_file(details)
        self.items = tuple(sorted(items, key=lambda item: item.audit_index))
        self.by_hash = {item.item_sha256: item for item in self.items}
        self.dataset_root = dataset_root.resolve()

    @classmethod
    def load(
        cls,
        *,
        worksheet: Path,
        details: Path,
        dataset_root: Path,
    ) -> "ReviewDataset":
        with worksheet.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            missing = REQUIRED_WORKSHEET_COLUMNS - set(reader.fieldnames or ())
            if missing:
                raise ValueError(
                    "worksheet is missing columns: " + ", ".join(sorted(missing))
                )
            worksheet_rows = list(reader)

        detail_rows: dict[int, dict[str, Any]] = {}
        with details.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line, object_pairs_hook=_strict_object)
                if not isinstance(value, dict):
                    raise ValueError(
                        f"alignment detail line {line_number} is not an object"
                    )
                index = int(value.get("audit_index"))
                if index in detail_rows:
                    raise ValueError(f"duplicate alignment detail index: {index}")
                detail_rows[index] = value

        indices: set[int] = set()
        raw_ids: set[str] = set()
        item_hashes: set[str] = set()
        items: list[AuditItem] = []
        for row_number, row in enumerate(worksheet_rows, start=2):
            try:
                audit_index = int(row["audit_index"])
                raw_id = row["utterance_id"].strip()
                detail_index = int(row["alignment_detail_line"])
                duration = float(row["duration_seconds"])
                target_count = int(row["target_phone_count"])
                realized_count = int(row["realized_phone_count"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid worksheet row {row_number}") from exc
            if audit_index <= 0 or audit_index in indices:
                raise ValueError(f"duplicate/invalid audit_index: {audit_index}")
            if not raw_id or raw_id in raw_ids:
                raise ValueError("duplicate/empty worksheet utterance_id")
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError(f"invalid duration at audit_index {audit_index}")
            if target_count < 0 or realized_count < 0:
                raise ValueError(f"negative phone count at {audit_index}")
            error_presence_text = row["error_presence"].strip().lower()
            if error_presence_text not in {"true", "false"}:
                raise ValueError(f"invalid error_presence at audit_index {audit_index}")
            if detail_index != audit_index:
                raise ValueError(f"alignment_detail_line mismatch at {audit_index}")
            detail = detail_rows.get(detail_index)
            if detail is None or detail.get("utterance_id") != raw_id:
                raise ValueError(f"worksheet/detail ID mismatch at {audit_index}")
            for key in (
                "target_phone_intervals",
                "realized_phone_intervals",
                "automatic_phone_alignments",
            ):
                if not isinstance(detail.get(key), list):
                    raise ValueError(f"{key} must be a list at {audit_index}")
            if len(detail["target_phone_intervals"]) != target_count:
                raise ValueError(f"target phone count mismatch at {audit_index}")
            if len(detail["realized_phone_intervals"]) != realized_count:
                raise ValueError(f"realized phone count mismatch at {audit_index}")
            item_hash = hash_item_id(raw_id)
            if item_hash in item_hashes:
                raise ValueError("duplicate namespaced item hash")
            audio_path = resolve_restricted_path(dataset_root, row["audio_path"])
            indices.add(audit_index)
            raw_ids.add(raw_id)
            item_hashes.add(item_hash)
            items.append(
                AuditItem(
                    audit_index=audit_index,
                    item_sha256=item_hash,
                    raw_utterance_id=raw_id,
                    split=row["split"],
                    l1=row["l1"],
                    gender=row["gender"],
                    proficiency=row["proficiency"],
                    error_presence=error_presence_text == "true",
                    duration_seconds=duration,
                    target_phone_count=target_count,
                    realized_phone_count=realized_count,
                    audio_path=audio_path,
                    target_phone_intervals=detail["target_phone_intervals"],
                    realized_phone_intervals=detail["realized_phone_intervals"],
                    automatic_phone_alignments=detail["automatic_phone_alignments"],
                )
            )
        if set(detail_rows) != indices:
            raise ValueError("alignment details contain missing or trailing items")
        if indices != set(range(1, len(items) + 1)):
            raise ValueError("audit_index values must be contiguous from 1")
        return cls(
            worksheet=worksheet,
            details=details,
            dataset_root=dataset_root,
            items=items,
        )


class ReviewSubmission(BaseModel):
    """A completed judgment for one audit item.

    ``incorrect_interval_count`` supports a phone-level accuracy metric
    in addition to the whole-utterance ``alignment_correct`` judgment.
    Whole-utterance pass/fail conflates utterance length with apparent
    difficulty: a 35-phone utterance needs 35 independent phones to all
    be correct to pass, while a 6-phone utterance only needs 6, so
    utterance-level failure rate rises with phone count even when
    per-phone accuracy is constant. Recording how many displayed
    automatic_phone_alignments entries are actually wrong allows scoring
    accuracy per phone instead of per utterance.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    alignment_correct: bool
    maximum_boundary_error_ms: float = Field(ge=0)
    incorrect_interval_count: int = Field(ge=0, default=0)
    notes: str = Field(max_length=2_000)

    @field_validator("maximum_boundary_error_ms")
    @classmethod
    def finite_boundary_error(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("maximum_boundary_error_ms must be finite")
        return value

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value: str) -> str:
        return unicodedata.normalize("NFKC", value).strip()


class ReviewStore:
    """Atomic, resumable result store containing hashed identifiers only."""

    def __init__(self, path: Path, dataset: ReviewDataset) -> None:
        self.path = path
        self.dataset = dataset
        self._lock = threading.Lock()
        self.reviews: dict[str, dict[str, Any]] = {}
        if path.exists():
            self._load()

    def _load(self) -> None:
        document = strict_read_json(self.path)
        if document.get("schema_version") != STATE_SCHEMA_VERSION:
            raise ValueError("unsupported review-state schema")
        source = document.get("source")
        if not isinstance(source, Mapping):
            raise ValueError("review state has no source hashes")
        if source.get("worksheet_sha256") != self.dataset.worksheet_sha256:
            raise ValueError("review state worksheet hash mismatch")
        if source.get("alignment_details_sha256") != self.dataset.details_sha256:
            raise ValueError("review state details hash mismatch")
        reviews = document.get("reviews")
        if not isinstance(reviews, Mapping):
            raise ValueError("review state reviews must be an object")
        validated: dict[str, dict[str, Any]] = {}
        for item_hash, review in reviews.items():
            if item_hash not in self.dataset.by_hash:
                raise ValueError("review state contains an unknown item hash")
            if not isinstance(review, Mapping):
                raise ValueError("review state item must be an object")
            if set(review) == LEGACY_REVIEW_FIELDS:
                # Predates the phone-level accuracy field: whole-utterance
                # judgment only, incorrect-interval count unknown.
                review = {**review, "incorrect_interval_count": None}
            elif set(review) != CURRENT_REVIEW_FIELDS:
                raise ValueError("review state item has incomplete/extra fields")
            reviewer_hash = review["reviewer_id_sha256"]
            if not isinstance(reviewer_hash, str) or not SHA256_PATTERN.fullmatch(
                reviewer_hash
            ):
                raise ValueError("reviewer_id_sha256 is invalid")
            if not isinstance(review["alignment_correct"], bool):
                raise ValueError("alignment_correct must be boolean")
            boundary = review["maximum_boundary_error_ms"]
            if (
                isinstance(boundary, bool)
                or not isinstance(boundary, (int, float))
                or not math.isfinite(float(boundary))
                or float(boundary) < 0
                or float(boundary)
                > self.dataset.by_hash[item_hash].duration_seconds * 1_000
            ):
                raise ValueError("maximum_boundary_error_ms is out of range")
            incorrect_count = review["incorrect_interval_count"]
            total_intervals = len(
                self.dataset.by_hash[item_hash].automatic_phone_alignments
            )
            if incorrect_count is not None:
                if isinstance(incorrect_count, bool) or not isinstance(
                    incorrect_count, int
                ):
                    raise ValueError("incorrect_interval_count must be an integer")
                if incorrect_count < 0 or incorrect_count > total_intervals:
                    raise ValueError("incorrect_interval_count is out of range")
                if review["alignment_correct"] and incorrect_count != 0:
                    raise ValueError(
                        "alignment_correct=true requires incorrect_interval_count=0"
                    )
                if not review["alignment_correct"] and incorrect_count == 0:
                    raise ValueError(
                        "alignment_correct=false requires incorrect_interval_count>=1"
                    )
            notes = review["notes"]
            if not isinstance(notes, str) or len(notes) > 2_000:
                raise ValueError("notes are invalid")
            validated[item_hash] = dict(review)
        self.reviews = validated

    def _document(self) -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "source": {
                "worksheet_sha256": self.dataset.worksheet_sha256,
                "alignment_details_sha256": self.dataset.details_sha256,
            },
            "privacy": {
                "item_ids_namespaced_sha256_only": True,
                "reviewer_ids_namespaced_sha256_only": True,
                "raw_paths_or_utterance_ids_stored": False,
            },
            "reviews": {key: self.reviews[key] for key in sorted(self.reviews)},
        }

    def save_review(
        self,
        *,
        item_hash: str,
        reviewer_hash: str,
        submission: ReviewSubmission,
        replace: bool,
    ) -> dict[str, Any]:
        item = self.dataset.by_hash.get(item_hash)
        if item is None:
            raise KeyError(item_hash)
        if not SHA256_PATTERN.fullmatch(reviewer_hash):
            raise ValueError("reviewer_id_sha256 is invalid")
        if submission.maximum_boundary_error_ms > item.duration_seconds * 1_000:
            raise ValueError("maximum_boundary_error_ms exceeds utterance duration")
        total_intervals = len(item.automatic_phone_alignments)
        if submission.incorrect_interval_count > total_intervals:
            raise ValueError(
                "incorrect_interval_count exceeds displayed alignment count"
            )
        if submission.alignment_correct and submission.incorrect_interval_count != 0:
            raise ValueError(
                "alignment_correct=true requires incorrect_interval_count=0"
            )
        if (
            not submission.alignment_correct
            and submission.incorrect_interval_count == 0
        ):
            raise ValueError(
                "alignment_correct=false requires incorrect_interval_count>=1"
            )
        for sensitive in self.dataset.items:
            if sensitive.raw_utterance_id in submission.notes:
                raise ValueError("notes must not contain raw utterance IDs")
            if str(sensitive.audio_path) in submission.notes:
                raise ValueError("notes must not contain restricted paths")
        with self._lock:
            if item_hash in self.reviews and not replace:
                raise FileExistsError("item already has a completed review")
            review = {
                "reviewer_id_sha256": reviewer_hash,
                "alignment_correct": submission.alignment_correct,
                "maximum_boundary_error_ms": (submission.maximum_boundary_error_ms),
                "incorrect_interval_count": submission.incorrect_interval_count,
                "notes": submission.notes,
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            self.reviews[item_hash] = review
            atomic_write_json(self.path, self._document())
            return dict(review)

    def status(self) -> dict[str, Any]:
        completed = len(self.reviews)
        total = len(self.dataset.items)
        return {
            "expected_count": total,
            "completed_count": completed,
            "remaining_count": total - completed,
            "completion_fraction": completed / total if total else 0.0,
        }


def _require_access(request: Request, access_token: str) -> None:
    provided = request.headers.get("X-Review-Token")
    if provided is None or not secrets.compare_digest(provided, access_token):
        raise HTTPException(status_code=403, detail="invalid review access token")


def _review_payload(item: AuditItem, store: ReviewStore) -> dict[str, Any]:
    payload = item.public_summary(reviewed=item.item_sha256 in store.reviews)
    payload.update(
        {
            "target_phone_intervals": item.target_phone_intervals,
            "realized_phone_intervals": item.realized_phone_intervals,
            "automatic_phone_alignments": item.automatic_phone_alignments,
            "review": store.reviews.get(item.item_sha256),
        }
    )
    return payload


def create_review_app(
    *,
    dataset: ReviewDataset,
    store: ReviewStore,
    reviewer_id_sha256: str,
    access_token: str,
) -> FastAPI:
    if not SHA256_PATTERN.fullmatch(reviewer_id_sha256):
        raise ValueError("reviewer_id_sha256 is invalid")
    if len(access_token) < 16:
        raise ValueError("access token must contain at least 16 characters")
    app = FastAPI(
        title="Local Alignment Review",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        # Static shell only. The URL-fragment token never reaches this server
        # or its logs; JavaScript uses it solely in the API request header.
        return HTMLResponse(
            _review_html(reviewer_hash=reviewer_id_sha256),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/status")
    def status(request: Request) -> dict[str, Any]:
        _require_access(request, access_token)
        return {
            **store.status(),
            "reviewer_id_sha256": reviewer_id_sha256,
            "access_control_warning": (
                "Local review token only; no institutional authentication. "
                "Do not expose this server outside the review machine."
            ),
        }

    @app.get("/api/items")
    def items(request: Request) -> list[dict[str, Any]]:
        _require_access(request, access_token)
        return [
            item.public_summary(reviewed=item.item_sha256 in store.reviews)
            for item in dataset.items
        ]

    @app.get("/api/items/{item_hash}")
    def item(item_hash: str, request: Request) -> dict[str, Any]:
        _require_access(request, access_token)
        selected = dataset.by_hash.get(item_hash)
        if selected is None:
            raise HTTPException(status_code=404, detail="unknown review item")
        return _review_payload(selected, store)

    @app.get("/api/items/{item_hash}/audio")
    def audio(item_hash: str, request: Request) -> FileResponse:
        _require_access(request, access_token)
        selected = dataset.by_hash.get(item_hash)
        if selected is None:
            raise HTTPException(status_code=404, detail="unknown review item")
        try:
            selected.audio_path.resolve().relative_to(dataset.dataset_root)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="audio locator escaped dataset root",
            ) from exc
        if not selected.audio_path.is_file():
            raise HTTPException(status_code=404, detail="audio is unavailable")
        return FileResponse(
            selected.audio_path,
            media_type="audio/wav",
            filename=f"{item_hash}.wav",
            content_disposition_type="inline",
            headers={"Cache-Control": "no-store"},
        )

    def save(
        item_hash: str,
        submission: ReviewSubmission,
        *,
        replace: bool,
    ) -> dict[str, Any]:
        try:
            review = store.save_review(
                item_hash=item_hash,
                reviewer_hash=reviewer_id_sha256,
                submission=submission,
                replace=replace,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown review item") from exc
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "item_sha256": item_hash,
            "review": review,
            "status": store.status(),
        }

    @app.post("/api/items/{item_hash}/review")
    def create_review(
        item_hash: str,
        submission: ReviewSubmission,
        request: Request,
    ) -> dict[str, Any]:
        _require_access(request, access_token)
        return save(item_hash, submission, replace=False)

    @app.put("/api/items/{item_hash}/review")
    def replace_review(
        item_hash: str,
        submission: ReviewSubmission,
        request: Request,
    ) -> dict[str, Any]:
        _require_access(request, access_token)
        return save(item_hash, submission, replace=True)

    return app


def _review_html(*, reviewer_hash: str) -> str:
    reviewer_json = json.dumps(reviewer_hash)
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Local Alignment Review</title>
<style>
body{{font-family:system-ui,sans-serif;margin:0;background:#f4f5f7;color:#17202a}}
header{{background:#801c1c;color:white;padding:12px 18px}}
main{{display:grid;grid-template-columns:280px 1fr;gap:16px;padding:16px}}
#items{{max-height:82vh;overflow:auto;background:white;padding:8px}}
button{{margin:3px;padding:7px}} pre{{white-space:pre-wrap;background:white;padding:12px}}
label{{display:block;margin:10px 0}} textarea{{width:100%;min-height:80px}}
.warning{{font-weight:700}}
</style></head><body>
<header><div class="warning">로컬 전용 · 기관 인증/TLS 없음 · 외부 공개 금지</div>
<small>reviewer hash: <span id="reviewer"></span> · notes에 개인정보를 쓰지 마세요.</small></header>
<main><aside id="items"></aside><section>
<div id="status"></div><h2 id="title">항목을 선택하세요</h2>
<audio id="audio" controls></audio><pre id="detail"></pre>
<form id="form"><label>정렬 정확성 <select id="correct" required>
<option value="">선택</option><option value="true">correct</option>
<option value="false">incorrect</option></select></label>
<label>최대 경계 오차(ms) <input id="boundary" type="number" min="0" step="0.1" required></label>
<label>메모 <textarea id="notes" maxlength="2000"></textarea></label>
<button type="submit">원자 저장</button></form></section></main>
<script>
const TOKEN=new URLSearchParams(location.hash.slice(1)).get('token')||'', REVIEWER={reviewer_json};
document.getElementById('reviewer').textContent=REVIEWER;
const headers={{'X-Review-Token':TOKEN,'Content-Type':'application/json'}};
let current=null, reviewed=false;
async function api(path,options={{}}){{options.headers={{...headers,...(options.headers||{{}})}};
 const r=await fetch(path,options);if(!r.ok)throw new Error(await r.text());return r.json();}}
async function refresh(){{const [status,items]=await Promise.all([api('/api/status'),api('/api/items')]);
 document.getElementById('status').textContent=`완료 ${{status.completed_count}} / ${{status.expected_count}}`;
 const box=document.getElementById('items');box.innerHTML='';
 for(const x of items){{const b=document.createElement('button');
 b.textContent=`${{x.audit_index}} · ${{x.item_sha256.slice(0,12)}} ${{x.reviewed?'✓':''}}`;
 b.onclick=()=>load(x.item_sha256);box.appendChild(b);box.appendChild(document.createElement('br'));}}}}
async function load(hash){{const x=await api('/api/items/'+hash);current=hash;reviewed=x.reviewed;
 document.getElementById('title').textContent=`#${{x.audit_index}} · ${{hash}}`;
 const ar=await fetch('/api/items/'+hash+'/audio',{{headers:{{'X-Review-Token':TOKEN}}}});
 if(!ar.ok)throw new Error(await ar.text());
 document.getElementById('audio').src=URL.createObjectURL(await ar.blob());
 document.getElementById('detail').textContent=JSON.stringify({{
 metadata:{{split:x.split,l1:x.l1,gender:x.gender,proficiency:x.proficiency,duration_seconds:x.duration_seconds}},
 target_phone_intervals:x.target_phone_intervals,realized_phone_intervals:x.realized_phone_intervals,
 automatic_phone_alignments:x.automatic_phone_alignments}},null,2);
 const r=x.review||{{}};document.getElementById('correct').value=
 r.alignment_correct===true?'true':r.alignment_correct===false?'false':'';
 document.getElementById('boundary').value=r.maximum_boundary_error_ms??'';
 document.getElementById('notes').value=r.notes??'';}}
document.getElementById('form').onsubmit=async e=>{{e.preventDefault();if(!current)return;
 const body={{alignment_correct:document.getElementById('correct').value==='true',
 maximum_boundary_error_ms:Number(document.getElementById('boundary').value),
 notes:document.getElementById('notes').value}};
 await api('/api/items/'+current+'/review',{{method:reviewed?'PUT':'POST',body:JSON.stringify(body)}});
 reviewed=true;await refresh();}};refresh();
</script></body></html>"""


def build_aggregate_report(
    *,
    dataset: ReviewDataset,
    store: ReviewStore,
    minimum_accuracy: float,
    export_path: Path | None = None,
) -> dict[str, Any]:
    if not 0 <= minimum_accuracy <= 1:
        raise ValueError("minimum accuracy must be between 0 and 1")
    status = store.status()
    reviews = list(store.reviews.values())
    complete = status["remaining_count"] == 0 and status["expected_count"] > 0
    correct = sum(bool(review["alignment_correct"]) for review in reviews)
    boundaries = [float(review["maximum_boundary_error_ms"]) for review in reviews]
    accuracy = correct / len(reviews) if complete else None

    # Phone-level accuracy: unlike utterance-level accuracy above, this is
    # not skewed by utterance length/phone count, since it counts wrong
    # phone intervals rather than "did every phone in this utterance pass".
    # Only computable when every review carries incorrect_interval_count
    # (reviews predating that field have it as None).
    legacy_review_count = sum(
        1 for review in reviews if review.get("incorrect_interval_count") is None
    )
    phone_level_accuracy = None
    total_phone_intervals = 0
    total_incorrect_phone_intervals = 0
    if complete and legacy_review_count == 0:
        for item_hash, review in store.reviews.items():
            item = dataset.by_hash[item_hash]
            total_phone_intervals += len(item.automatic_phone_alignments)
            total_incorrect_phone_intervals += int(review["incorrect_interval_count"])
        if total_phone_intervals > 0:
            phone_level_accuracy = 1 - (
                total_incorrect_phone_intervals / total_phone_intervals
            )
    gate_status = (
        "passed"
        if complete and accuracy is not None and accuracy >= minimum_accuracy
        else "failed"
        if complete
        else "incomplete"
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": gate_status,
        **status,
        "correct_count": correct if reviews else 0,
        "incorrect_count": len(reviews) - correct if reviews else 0,
        "alignment_accuracy": accuracy,
        "minimum_accuracy": minimum_accuracy,
        "phone_level_accuracy": {
            "value": phone_level_accuracy,
            "total_phone_intervals": total_phone_intervals,
            "total_incorrect_phone_intervals": total_incorrect_phone_intervals,
            "legacy_reviews_missing_interval_count": legacy_review_count,
        },
        "maximum_boundary_error_ms": {
            "median": statistics.median(boundaries) if boundaries else None,
            "maximum": max(boundaries) if boundaries else None,
        },
        "reviewer_count": len({review["reviewer_id_sha256"] for review in reviews}),
        "source": {
            "worksheet_sha256": dataset.worksheet_sha256,
            "alignment_details_sha256": dataset.details_sha256,
            "review_state_sha256": (
                sha256_file(store.path) if store.path.is_file() else None
            ),
        },
        "export": {
            "written": bool(export_path and export_path.is_file() and complete),
            "sha256": (
                sha256_file(export_path)
                if export_path and export_path.is_file() and complete
                else None
            ),
            "scorer_compatible": True,
        },
        "privacy": {
            "item_ids_namespaced_sha256_only": True,
            "reviewer_ids_namespaced_sha256_only": True,
            "raw_paths_or_utterance_ids_exported": False,
        },
    }


def export_completed_reviews(
    *,
    dataset: ReviewDataset,
    store: ReviewStore,
    output: Path,
    report_output: Path,
    minimum_accuracy: float = 0.95,
) -> dict[str, Any]:
    status = store.status()
    if status["remaining_count"] != 0 or status["expected_count"] == 0:
        report = build_aggregate_report(
            dataset=dataset,
            store=store,
            minimum_accuracy=minimum_accuracy,
        )
        atomic_write_json(report_output, report)
        return report

    fields = [
        "audit_index",
        "utterance_id",
        "reviewer_id",
        "alignment_correct",
        "maximum_boundary_error_ms",
        "incorrect_interval_count",
        "total_alignment_interval_count",
        "notes",
    ]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, delimiter="\t")
    writer.writeheader()
    for item in dataset.items:
        review = store.reviews[item.item_sha256]
        incorrect_count = review.get("incorrect_interval_count")
        writer.writerow(
            {
                "audit_index": item.audit_index,
                "utterance_id": item.item_sha256,
                "reviewer_id": review["reviewer_id_sha256"],
                "alignment_correct": str(review["alignment_correct"]).lower(),
                "maximum_boundary_error_ms": (review["maximum_boundary_error_ms"]),
                "incorrect_interval_count": (
                    "" if incorrect_count is None else incorrect_count
                ),
                "total_alignment_interval_count": len(item.automatic_phone_alignments),
                "notes": review["notes"],
            }
        )
    atomic_write_text(output, buffer.getvalue())
    report = build_aggregate_report(
        dataset=dataset,
        store=store,
        minimum_accuracy=minimum_accuracy,
        export_path=output,
    )
    atomic_write_json(report_output, report)
    return report
