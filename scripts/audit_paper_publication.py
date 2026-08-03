#!/usr/bin/env python3
"""Audit the paper publication surface without opening protected data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.reproducibility.paper_publication import (
    PublicationAuditError,
    audit_paper_publication,
    observe_external_urls,
    validate_config_only,
    validate_publication_receipt,
    write_network_observation,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path("configs/paper/publication_v1.yaml")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the aggregate-only, offline manuscript publication audit. "
            "Canonical manuscript/evidence files are never modified."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=REPOSITORY_ROOT,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "validate configuration structure only; read zero referenced "
            "sources and write zero outputs"
        ),
    )
    mode.add_argument(
        "--validate-receipt-only",
        action="store_true",
        help="deep-validate the committed offline receipt without writing outputs",
    )
    mode.add_argument(
        "--observe-network",
        action="store_true",
        help=(
            "perform optional timestamped URL observations; these are separate "
            "and never canonical completion evidence"
        ),
    )
    parser.add_argument(
        "--commit",
        type=Path,
        default=Path("artifacts/reproducibility/paper_publication/commit.json"),
        help="commit path used by --validate-receipt-only",
    )
    parser.add_argument(
        "--network-output",
        type=Path,
        help=(
            "required with --observe-network; must be below "
            "artifacts/reproducibility/paper_publication/network_observations"
        ),
    )
    parser.add_argument(
        "--network-timeout-seconds",
        type=float,
        default=20.0,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.config_only:
            if args.network_output is not None:
                raise PublicationAuditError("network_output_without_observation")
            result = validate_config_only(
                config_path=args.config,
                workspace_root=args.workspace_root,
            )
        elif args.validate_receipt_only:
            if args.network_output is not None:
                raise PublicationAuditError("network_output_without_observation")
            result = validate_publication_receipt(
                config_path=args.config,
                commit_path=args.commit,
                workspace_root=args.workspace_root,
            )
        elif args.observe_network:
            if args.network_output is None:
                raise PublicationAuditError("network_output_required")
            observation = observe_external_urls(
                config_path=args.config,
                workspace_root=args.workspace_root,
                timeout_seconds=args.network_timeout_seconds,
            )
            output = write_network_observation(
                document=observation,
                output_path=args.network_output,
                workspace_root=args.workspace_root,
            )
            result = {
                "status": observation["status"],
                "canonical_completion_evidence": False,
                "request_count": observation["request_count"],
                "output": output,
            }
        else:
            if args.network_output is not None:
                raise PublicationAuditError("network_output_without_observation")
            result = audit_paper_publication(
                config_path=args.config,
                workspace_root=args.workspace_root,
            )
    except PublicationAuditError as exc:
        print(f"paper publication audit blocked: {exc.code}", file=sys.stderr)
        return 1
    except OSError:
        print("paper publication audit I/O failure", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
