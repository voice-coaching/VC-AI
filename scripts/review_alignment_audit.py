#!/usr/bin/env python3
"""Serve, resume, and export the local AI Hub alignment review."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import sys

import uvicorn

from voice_coach.evaluation.alignment_review import (
    ReviewDataset,
    ReviewStore,
    atomic_write_json,
    build_aggregate_report,
    create_review_app,
    export_completed_reviews,
    hash_reviewer_id,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _add_sources(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--worksheet", type=Path, required=True)
    parser.add_argument("--details", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve")
    _add_sources(serve)
    serve.add_argument("--host", default=DEFAULT_HOST)
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument("--allow-non-loopback", action="store_true")
    serve.add_argument("--reviewer-id")
    serve.add_argument("--reviewer-id-env", default="VOICE_COACH_REVIEWER_ID")
    serve.add_argument("--access-token")

    status = subparsers.add_parser("status")
    _add_sources(status)
    status.add_argument("--report-output", type=Path)
    status.add_argument("--minimum-accuracy", type=float, default=0.95)

    export = subparsers.add_parser("export")
    _add_sources(export)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--report-output", type=Path, required=True)
    export.add_argument("--minimum-accuracy", type=float, default=0.95)
    return parser.parse_args(argv)


def _load(args: argparse.Namespace) -> tuple[ReviewDataset, ReviewStore]:
    dataset = ReviewDataset.load(
        worksheet=args.worksheet,
        details=args.details,
        dataset_root=args.dataset_root,
    )
    return dataset, ReviewStore(args.state, dataset)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "serve":
        if args.host not in LOOPBACK_HOSTS and not args.allow_non_loopback:
            raise SystemExit(
                "Refusing non-loopback bind without --allow-non-loopback. "
                "This review server has no institutional identity provider."
            )
    dataset, store = _load(args)
    if args.command == "serve":
        reviewer_raw = args.reviewer_id or os.environ.get(args.reviewer_id_env)
        if not reviewer_raw:
            raise SystemExit(
                f"Set {args.reviewer_id_env} or pass --reviewer-id. "
                "The value is hashed and never stored raw."
            )
        reviewer_hash = hash_reviewer_id(reviewer_raw)
        token = args.access_token or secrets.token_urlsafe(32)
        if len(token) < 16:
            raise SystemExit("--access-token must contain at least 16 characters")
        print(
            "WARNING: local review token only; no TLS or institutional "
            "authentication. Never expose restricted audio externally.",
            file=sys.stderr,
            flush=True,
        )
        print(
            f"Open http://{args.host}:{args.port}/#token={token}",
            flush=True,
        )
        app = create_review_app(
            dataset=dataset,
            store=store,
            reviewer_id_sha256=reviewer_hash,
            access_token=token,
        )
        # The access token lives in a URL fragment and is sent only as a
        # header. Disable request logs as defense in depth.
        uvicorn.run(app, host=args.host, port=args.port, access_log=False)
        return 0

    if args.command == "status":
        report = build_aggregate_report(
            dataset=dataset,
            store=store,
            minimum_accuracy=args.minimum_accuracy,
        )
        if args.report_output:
            atomic_write_json(args.report_output, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    report = export_completed_reviews(
        dataset=dataset,
        store=store,
        output=args.output,
        report_output=args.report_output,
        minimum_accuracy=args.minimum_accuracy,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"passed", "failed"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
