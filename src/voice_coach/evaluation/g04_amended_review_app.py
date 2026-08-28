"""Loopback-only server for the amended G04 T43 and I30 review GUI."""

from __future__ import annotations

from pathlib import Path
import secrets
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from voice_coach.evaluation.alignment_review import hash_reviewer_id
from voice_coach.evaluation.g04_amended_contract import (
    AmendedReviewSubmission,
    G04AmendedContractError,
    G04AmendedReviewStore,
)


API_PREFIX = "/api/v1"
FRONTEND_ROOT = Path(__file__).with_name("g04_review_frontend")
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "media-src 'self' blob:; img-src 'self'; connect-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def _require_access(request: Request, access_token: str) -> None:
    provided = request.headers.get("X-Review-Token")
    if provided is None or not secrets.compare_digest(provided, access_token):
        raise HTTPException(status_code=403, detail="invalid review access token")


def _item_payload(*, item_hash: str, store: G04AmendedReviewStore) -> dict[str, Any]:
    item = store.dataset.by_hash[item_hash]
    payload = item.public_summary(reviewed=item_hash in store.reviews)
    payload.update(
        {
            "target_phone_intervals": item.target_phone_intervals,
            "realized_phone_intervals": item.realized_phone_intervals,
            "automatic_phone_alignments": item.automatic_phone_alignments,
            "review": store.reviews.get(item_hash),
            "profile": store.profile.value,
        }
    )
    return payload


def create_amended_review_app(
    *,
    store: G04AmendedReviewStore,
    reviewer_id: str,
    access_token: str,
    frontend_root: Path = FRONTEND_ROOT,
) -> FastAPI:
    """Create a local-only API that never returns raw IDs or source paths."""

    if len(access_token) < 16:
        raise ValueError("access token must contain at least 16 characters")
    frontend_files = {
        "index": frontend_root / "index.html",
        "styles": frontend_root / "styles.css",
        "app": frontend_root / "app.js",
    }
    missing_frontend_files = [
        str(path) for path in frontend_files.values() if not path.is_file()
    ]
    if missing_frontend_files:
        raise FileNotFoundError(
            f"G04 review frontend is incomplete: {missing_frontend_files}"
        )
    reviewer_hash = hash_reviewer_id(reviewer_id)
    app = FastAPI(
        title="Local Amended G04 Review",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/", response_class=FileResponse)
    def index() -> FileResponse:
        return FileResponse(
            frontend_files["index"],
            media_type="text/html",
            headers=SECURITY_HEADERS,
        )

    @app.get("/assets/styles.css", response_class=FileResponse)
    def styles() -> FileResponse:
        return FileResponse(
            frontend_files["styles"],
            media_type="text/css",
            headers=SECURITY_HEADERS,
        )

    @app.get("/assets/app.js", response_class=FileResponse)
    def frontend_application() -> FileResponse:
        return FileResponse(
            frontend_files["app"],
            media_type="text/javascript",
            headers=SECURITY_HEADERS,
        )

    @app.get(f"{API_PREFIX}/review-session")
    def status(request: Request) -> dict[str, Any]:
        _require_access(request, access_token)
        return {
            **store.status(),
            "reviewer_id_sha256": reviewer_hash,
            "access_control_warning": (
                "Loopback review token only. Do not expose restricted audio."
            ),
        }

    @app.get(f"{API_PREFIX}/review-items")
    def items(request: Request) -> list[dict[str, Any]]:
        _require_access(request, access_token)
        return [
            item.public_summary(reviewed=item.item_sha256 in store.reviews)
            for item in store.items
        ]

    @app.get(f"{API_PREFIX}/review-items/{{item_hash}}")
    def item(item_hash: str, request: Request) -> dict[str, Any]:
        _require_access(request, access_token)
        if item_hash not in store.selected_hash_set:
            raise HTTPException(status_code=404, detail="unknown review item")
        return _item_payload(item_hash=item_hash, store=store)

    @app.get(f"{API_PREFIX}/review-items/{{item_hash}}/audio")
    def audio(item_hash: str, request: Request) -> FileResponse:
        _require_access(request, access_token)
        if item_hash not in store.selected_hash_set:
            raise HTTPException(status_code=404, detail="unknown review item")
        selected = store.dataset.by_hash[item_hash]
        try:
            selected.audio_path.resolve().relative_to(store.dataset.dataset_root)
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
        submission: AmendedReviewSubmission,
        *,
        replace: bool,
    ) -> dict[str, Any]:
        try:
            review = store.save_review(
                item_hash=item_hash,
                reviewer_id=reviewer_id,
                submission=submission,
                replace=replace,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown review item") from exc
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except G04AmendedContractError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "item_sha256": item_hash,
            "review": review,
            "status": store.status(),
        }

    @app.post(f"{API_PREFIX}/review-items/{{item_hash}}/review")
    def create_review(
        item_hash: str,
        submission: AmendedReviewSubmission,
        request: Request,
    ) -> dict[str, Any]:
        _require_access(request, access_token)
        return save(item_hash, submission, replace=False)

    @app.put(f"{API_PREFIX}/review-items/{{item_hash}}/review")
    def replace_review(
        item_hash: str,
        submission: AmendedReviewSubmission,
        request: Request,
    ) -> dict[str, Any]:
        _require_access(request, access_token)
        return save(item_hash, submission, replace=True)

    return app


__all__ = ["API_PREFIX", "FRONTEND_ROOT", "create_amended_review_app"]
