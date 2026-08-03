#!/usr/bin/env python3
"""Audit, prepare, or validate the interrupted canonical S104 attempt rollover."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.reproducibility.s104_interrupted_rollover import (
    IncidentEvidence,
    S104InterruptedRolloverError,
    audit_s104_interrupted_attempt,
    prepare_s104_interrupted_rollover,
    validate_s104_interrupted_rollover,
)


SCRIPT_PATH = Path(__file__).resolve()
REPOSITORY_ROOT = SCRIPT_PATH.parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit by default. --prepare performs a create-only, "
            "atomic archive rollover and never starts training."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--config-only",
        action="store_true",
        help="Validate arguments and canonical path contract without filesystem reads.",
    )
    mode.add_argument(
        "--prepare",
        action="store_true",
        help="Archive the pinned interrupted attempt and publish an incident receipt.",
    )
    mode.add_argument(
        "--validate-receipt",
        action="store_true",
        help="Read-only validate an already committed incident receipt.",
    )
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--observed-exit-code", type=int, required=True)
    parser.add_argument("--last-alive-at", required=True)
    parser.add_argument("--first-missing-at", required=True)
    parser.add_argument("--session-id", type=int, required=True)
    parser.add_argument("--signal-source", required=True)
    parser.add_argument("--last-rchar", type=int, required=True)
    parser.add_argument("--last-wchar", type=int, required=True)
    parser.add_argument("--last-phase", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    evidence = IncidentEvidence(
        observed_exit_code=args.observed_exit_code,
        last_alive_at=args.last_alive_at,
        first_missing_at=args.first_missing_at,
        session_id=args.session_id,
        signal_source=args.signal_source,
        last_rchar=args.last_rchar,
        last_wchar=args.last_wchar,
        last_phase=args.last_phase,
    )
    try:
        if args.prepare:
            result = prepare_s104_interrupted_rollover(
                workspace_root=args.workspace_root,
                evidence=evidence,
            )
        elif args.validate_receipt:
            result = validate_s104_interrupted_rollover(
                workspace_root=args.workspace_root,
                evidence=evidence,
            )
        else:
            result = audit_s104_interrupted_attempt(
                workspace_root=args.workspace_root,
                evidence=evidence,
                config_only=args.config_only,
            )
    except (OSError, S104InterruptedRolloverError, ValueError) as error:
        print(
            "S104 interrupted-attempt rollover failed: "
            f"{type(error).__module__}.{type(error).__qualname__}",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
