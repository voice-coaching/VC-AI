#!/usr/bin/env python3
"""Validate P04/P05 integrity and semantic currency without paper writes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.reproducibility.p04_p05_gap_validation import (
    DEFAULT_OUTPUT_ROOT,
    GapValidationError,
    run_gap_audit,
    validate_committed_gap_audit,
    validate_config_only,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path("configs/experiments/p04_p05_gap_validation_v1.yaml")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Check the current 40-item P04 gap arithmetic, P05 evidence "
            "integrity, and exact semantic-update findings. The command never "
            "modifies the canonical manuscript, evidence map, gap maps, or tracker."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=REPOSITORY_ROOT,
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(DEFAULT_OUTPUT_ROOT),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--validate-config-only",
        action="store_true",
        help=(
            "Validate only the config structure; declared canonical, aggregate, "
            "locked, external-human, and output paths remain untouched."
        ),
    )
    mode.add_argument(
        "--validate-commit-only",
        action="store_true",
        help="Deeply revalidate the committed report without writing output.",
    )
    parser.add_argument(
        "--commit",
        type=Path,
        default=None,
        help="Commit path for --validate-commit-only.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.validate_config_only:
            result = validate_config_only(
                args.config,
                workspace_root=args.workspace_root,
            )
        elif args.validate_commit_only:
            commit = args.commit or args.output_root / "commit.json"
            result = validate_committed_gap_audit(
                commit_path=commit,
                workspace_root=args.workspace_root,
            )
        else:
            result = run_gap_audit(
                config_path=args.config,
                workspace_root=args.workspace_root,
                output_root=args.output_root,
            )
    except GapValidationError as exc:
        print(f"P04/P05 gap validation blocked: {exc.code}", file=sys.stderr)
        return 1
    except OSError:
        print("P04/P05 gap validation I/O failure", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
