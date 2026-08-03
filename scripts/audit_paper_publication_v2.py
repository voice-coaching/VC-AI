#!/usr/bin/env python3
"""Run the isolated, aggregate-only paper publication audit v2."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.reproducibility.paper_publication_v2 import (
    CANONICAL_CONFIG_PATH,
    PublicationV2Error,
    audit_paper_publication,
    validate_config_only,
    validate_publication_receipt,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate or publish the descriptor-anchored paper audit v2. "
            "All modes are offline and never edit canonical paper sources."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(CANONICAL_CONFIG_PATH),
        help="must remain the canonical publication v2 config path",
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
        help="validate only config structure; read no declared sources or outputs",
    )
    mode.add_argument(
        "--validate-receipt-only",
        action="store_true",
        help="deep-validate the committed receipt and all current source pins",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.config_only:
            result = validate_config_only(
                config_path=args.config,
                workspace_root=args.workspace_root,
            )
        elif args.validate_receipt_only:
            result = validate_publication_receipt(
                config_path=args.config,
                workspace_root=args.workspace_root,
            )
        else:
            result = audit_paper_publication(
                config_path=args.config,
                workspace_root=args.workspace_root,
            )
    except PublicationV2Error as exc:
        print(f"paper publication audit v2 blocked: {exc.code}", file=sys.stderr)
        return 1
    except OSError:
        print("paper publication audit v2 I/O failure", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
