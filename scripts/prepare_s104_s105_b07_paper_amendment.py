#!/usr/bin/env python3
"""Prepare or statically validate the S104/S105/B07 paper amendment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.reproducibility.s104_s105_b07_paper_amendment import (
    DEFAULT_OUTPUT_ROOT,
    PaperAmendmentError,
    prepare_paper_amendment,
    validate_config_only,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = Path("configs/experiments/s104_s105_b07_paper_amendment_v1.yaml")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run S104, S105, and B07 public deep validators, render paper-safe "
            "development evidence, and publish a review-only candidate bundle. "
            "The command never writes the canonical manuscript or evidence map."
        )
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
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
    parser.add_argument(
        "--validate-config-only",
        action="store_true",
        help=(
            "Validate the blocked static manifest without resolving any "
            "experiment, registry, artifact, manuscript, or evidence-map path."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.validate_config_only:
            report = validate_config_only(args.manifest)
        else:
            report = prepare_paper_amendment(
                manifest_path=args.manifest,
                workspace_root=args.workspace_root,
                output_root=args.output_root,
            )
    except PaperAmendmentError as exc:
        print(f"paper amendment blocked: {exc.code}", file=sys.stderr)
        return 1
    except OSError:
        print("paper amendment publication failed", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
