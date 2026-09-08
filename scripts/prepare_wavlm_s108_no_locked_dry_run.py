#!/usr/bin/env python3
"""Validate or publish the WP4 S108 aggregate-only no-locked checklist."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.training.wavlm_s108_dry_run import (
    S108DryRunError,
    load_config,
    run_no_locked_dry_run,
    validate_dry_run_receipt,
    validate_static_config,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path("configs/experiments/wavlm_s108_no_locked_dry_run_v1.yaml")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run an aggregate-only S108 readiness state check. This command "
            "does not implement or access a locked evaluation batch."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--config-only",
        action="store_true",
        help="validate static policy/source pins with zero artifact/output access",
    )
    parser.add_argument(
        "--validate-receipt",
        type=Path,
        help="read-only deep validation of one published dry-run receipt",
    )
    parser.add_argument(
        "--fault-injection",
        choices=[
            "after_temporary_write",
            "before_generation_publish",
            "after_generation_publish",
            "before_receipt_publish",
        ],
        help=argparse.SUPPRESS,
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        root = args.workspace_root.resolve(strict=True)
        config_path = args.config
        if not config_path.is_absolute():
            config_path = root / config_path
        if args.config_only:
            result = validate_static_config(
                workspace_root=root,
                config_path=config_path,
                config=load_config(config_path),
            )
        elif args.validate_receipt is not None:
            result = validate_dry_run_receipt(
                workspace_root=root,
                config_path=config_path,
                receipt_path=args.validate_receipt,
            )
        else:
            published = run_no_locked_dry_run(
                workspace_root=root,
                config_path=config_path,
                fault_injection=args.fault_injection,
            )
            result = {
                "status": "published_no_locked_dry_run_only",
                "generation_id": published.generation_id,
                "generation_path": published.generation_path.relative_to(
                    root
                ).as_posix(),
                "receipt_path": published.receipt_path.relative_to(root).as_posix(),
                "checklist_sha256": published.checklist_sha256,
                "overall_state": published.checklist["overall_state"],
                "ready_for_locked_batch": published.checklist["ready_for_locked_batch"],
                "created_generation": published.created_generation,
                "created_receipt": published.created_receipt,
                "s108_completion_claim_allowed": False,
                "s108_score_allowed": False,
                "locked_access_count": 0,
            }
    except (OSError, S108DryRunError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error": str(exc),
                    "locked_access_count": 0,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
