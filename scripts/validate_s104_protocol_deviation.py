#!/usr/bin/env python3
"""Read-only validator for a committed DEV-S104-001 adjudication receipt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.reproducibility.s104_protocol_deviation import (
    S104ProtocolDeviationError,
    prepare_s104_protocol_deviation,
    validate_s104_protocol_deviation,
)


SCRIPT_PATH = Path(__file__).resolve()
REPOSITORY_ROOT = SCRIPT_PATH.parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate DEV-S104-001 against the current S104 source, data, model, "
            "registry, generation, commit, and independently recomputed metrics."
        )
    )
    parser.add_argument(
        "--receipt-commit",
        type=Path,
        default=Path(
            "artifacts/protocol_deviations/dev-s104-001/commit.json",
        ),
    )
    parser.add_argument(
        "--s104-config",
        type=Path,
        default=Path("configs/experiments/wavlm_s104_adapter_v1.yaml"),
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=REPOSITORY_ROOT,
    )
    parser.add_argument(
        "--require-decision",
        choices=("MINOR_ACCEPTED", "INVALIDATING"),
    )
    parser.add_argument(
        "--prepare",
        action="store_true",
        help=(
            "Create-only publish a content-addressed receipt after terminal S104 "
            "validation; a non-terminal run fails before creating any output."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.prepare:
            result = prepare_s104_protocol_deviation(
                workspace_root=args.workspace_root,
                receipt_commit_path=args.receipt_commit,
                s104_config_path=args.s104_config,
                require_decision=args.require_decision,
            )
        else:
            result = validate_s104_protocol_deviation(
                workspace_root=args.workspace_root,
                receipt_commit_path=args.receipt_commit,
                s104_config_path=args.s104_config,
                require_decision=args.require_decision,
            )
    except (S104ProtocolDeviationError, OSError, ValueError) as error:
        # Do not print exception text because it may contain a restricted path.
        print(
            "S104 protocol-deviation validation failed: "
            f"{type(error).__module__}.{type(error).__qualname__}",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
