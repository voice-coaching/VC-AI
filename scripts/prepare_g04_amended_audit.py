#!/usr/bin/env python3
"""Prepare, serve, score, and attest the amended G04 T43/I30 audits."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import sys
from typing import Any, Mapping

import uvicorn

from voice_coach.evaluation.alignment_review import (
    ReviewDataset,
    atomic_write_json,
    sha256_file,
    strict_read_json,
)
from voice_coach.evaluation.g04_amended_contract import (
    G04AmendedContractError,
    G04AmendedReviewStore,
    G04ReviewProfile,
    build_amended_progress,
    build_completion_attestation,
    build_i30_report,
    build_t43_manifest,
    build_t43_manifest_receipt,
    build_t43_report,
    export_amended_reviews,
    selected_hashes_from_t43_manifest,
    validate_t43_manifest,
)
from voice_coach.evaluation.g04_amended_review_app import (
    create_amended_review_app,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _add_review_sources(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile",
        choices=[profile.value for profile in G04ReviewProfile],
        required=True,
    )
    parser.add_argument("--worksheet", type=Path, required=True)
    parser.add_argument("--details", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--selection-source", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare-t43")
    prepare.add_argument("--worksheet", type=Path, required=True)
    prepare.add_argument("--details", type=Path, required=True)
    prepare.add_argument("--dataset-root", type=Path, required=True)
    prepare.add_argument("--proxy-state", type=Path, required=True)
    prepare.add_argument(
        "--alignment-policy-source",
        type=Path,
        default=ROOT / "src/voice_coach/evaluation/g04_audio_proxy.py",
    )
    prepare.add_argument(
        "--align-intervals-source",
        type=Path,
        default=ROOT / "src/voice_coach/data/intervals.py",
    )
    prepare.add_argument("--manifest-output", type=Path, required=True)
    prepare.add_argument("--receipt-output", type=Path, required=True)

    serve = subparsers.add_parser("serve")
    _add_review_sources(serve)
    serve.add_argument("--host", default=DEFAULT_HOST)
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument("--allow-non-loopback", action="store_true")
    serve.add_argument("--reviewer-id")
    serve.add_argument("--reviewer-id-env", default="VOICE_COACH_REVIEWER_ID")
    serve.add_argument("--access-token")

    status = subparsers.add_parser("status")
    _add_review_sources(status)
    status.add_argument("--report-output", type=Path)

    export = subparsers.add_parser("export")
    _add_review_sources(export)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--report-output", type=Path, required=True)

    progress = subparsers.add_parser("progress")
    progress.add_argument("--t43-report", type=Path, required=True)
    progress.add_argument("--i30-report", type=Path, required=True)
    progress.add_argument("--consumer-migration-complete", action="store_true")
    progress.add_argument("--output", type=Path, required=True)

    completion = subparsers.add_parser("completion")
    completion.add_argument("--t43-report", type=Path, required=True)
    completion.add_argument("--i30-report", type=Path, required=True)
    completion.add_argument("--reviewer-attestation", type=Path, required=True)
    completion.add_argument("--reviewer-qualification-confirmed", action="store_true")
    completion.add_argument("--research-lead-approved", action="store_true")
    completion.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def _write_new_or_identical(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        if strict_read_json(path) != dict(payload):
            raise G04AmendedContractError(
                f"refusing to overwrite non-identical immutable artifact: {path}"
            )
        return
    atomic_write_json(path, payload)


def _prepare_t43(args: argparse.Namespace) -> Mapping[str, Any]:
    dataset = ReviewDataset.load(
        worksheet=args.worksheet,
        details=args.details,
        dataset_root=args.dataset_root,
    )
    proxy_state = strict_read_json(args.proxy_state)
    manifest = build_t43_manifest(
        dataset=dataset,
        proxy_state=proxy_state,
        proxy_state_sha256=sha256_file(args.proxy_state),
        alignment_policy_source_sha256=sha256_file(args.alignment_policy_source),
        align_intervals_source_sha256=sha256_file(args.align_intervals_source),
    )
    _write_new_or_identical(args.manifest_output, manifest)
    receipt = build_t43_manifest_receipt(
        manifest=manifest,
        manifest_sha256=sha256_file(args.manifest_output),
    )
    _write_new_or_identical(args.receipt_output, receipt)
    return receipt


def _load_i30_selection_source(
    *, source_path: Path, dataset: ReviewDataset
) -> tuple[tuple[str, ...], str]:
    metadata = strict_read_json(source_path)
    if metadata.get("worksheet_sha256") != dataset.worksheet_sha256:
        raise G04AmendedContractError("I30 metadata worksheet SHA differs")
    if metadata.get("alignment_details_sha256") != dataset.details_sha256:
        raise G04AmendedContractError("I30 metadata details SHA differs")
    if metadata.get("sample_size") != 30:
        raise G04AmendedContractError("I30 metadata sample size differs")
    return tuple(item.item_sha256 for item in dataset.items), sha256_file(source_path)


def _load_store(args: argparse.Namespace) -> G04AmendedReviewStore:
    dataset = ReviewDataset.load(
        worksheet=args.worksheet,
        details=args.details,
        dataset_root=args.dataset_root,
    )
    profile = G04ReviewProfile(args.profile)
    if profile is G04ReviewProfile.T43:
        manifest = strict_read_json(args.selection_source)
        validate_t43_manifest(manifest)
        source = manifest["source"]
        if source["worksheet_sha256"] != dataset.worksheet_sha256:
            raise G04AmendedContractError("T43 manifest worksheet SHA differs")
        if source["alignment_details_sha256"] != dataset.details_sha256:
            raise G04AmendedContractError("T43 manifest details SHA differs")
        selected = selected_hashes_from_t43_manifest(manifest)
        selection_sha = sha256_file(args.selection_source)
    else:
        selected, selection_sha = _load_i30_selection_source(
            source_path=args.selection_source,
            dataset=dataset,
        )
    return G04AmendedReviewStore(
        path=args.state,
        dataset=dataset,
        profile=profile,
        selected_item_hashes=selected,
        selection_source_sha256=selection_sha,
    )


def _report(store: G04AmendedReviewStore) -> dict[str, Any]:
    return (
        build_t43_report(store=store)
        if store.profile is G04ReviewProfile.T43
        else build_i30_report(store=store)
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "prepare-t43":
        output = _prepare_t43(args)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    if args.command == "progress":
        output = build_amended_progress(
            t43_report=strict_read_json(args.t43_report),
            i30_report=strict_read_json(args.i30_report),
            consumer_migration_complete=args.consumer_migration_complete,
        )
        atomic_write_json(args.output, output)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if output["status"] == "passed" else 2
    if args.command == "completion":
        t43_report = strict_read_json(args.t43_report)
        i30_report = strict_read_json(args.i30_report)
        output = build_completion_attestation(
            t43_report=t43_report,
            t43_report_sha256=sha256_file(args.t43_report),
            i30_report=i30_report,
            i30_report_sha256=sha256_file(args.i30_report),
            reviewer_attestation_sha256=sha256_file(args.reviewer_attestation),
            reviewer_qualification_confirmed=(args.reviewer_qualification_confirmed),
            research_lead_approved=args.research_lead_approved,
        )
        _write_new_or_identical(args.output, output)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0

    store = _load_store(args)
    if args.command == "serve":
        if args.host not in LOOPBACK_HOSTS and not args.allow_non_loopback:
            raise SystemExit("refusing non-loopback bind without explicit override")
        reviewer_id = args.reviewer_id or os.environ.get(args.reviewer_id_env)
        if not reviewer_id:
            raise SystemExit(
                f"set {args.reviewer_id_env} or pass --reviewer-id; it is hashed"
            )
        token = args.access_token or secrets.token_urlsafe(32)
        if len(token) < 16:
            raise SystemExit("--access-token must contain at least 16 characters")
        print(
            "WARNING: loopback-only review server; never expose restricted audio.",
            file=sys.stderr,
            flush=True,
        )
        print(f"Open http://{args.host}:{args.port}/#token={token}", flush=True)
        app = create_amended_review_app(
            store=store,
            reviewer_id=reviewer_id,
            access_token=token,
        )
        uvicorn.run(app, host=args.host, port=args.port, access_log=False)
        return 0
    if args.command == "status":
        output = _report(store)
        if args.report_output:
            atomic_write_json(args.report_output, output)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    output = export_amended_reviews(
        store=store,
        output=args.output,
        report_output=args.report_output,
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["status"] in {"passed", "failed"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
