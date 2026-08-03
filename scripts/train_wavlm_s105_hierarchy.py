#!/usr/bin/env python3
"""Fail-closed entry point for the WP4 S105 online hierarchy stage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


SCRIPT_PATH = Path(__file__).resolve()
REPOSITORY_ROOT = SCRIPT_PATH.parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate or execute WP4 S105.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/experiments/wavlm_s105_hierarchy_v1.yaml"),
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=REPOSITORY_ROOT,
    )
    parser.add_argument(
        "--validate-commit-only",
        action="store_true",
        help="Read-only validate a completed S105 commit and artifact chain.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace_root = args.workspace_root.resolve(strict=True)
    config_path = (
        args.config if args.config.is_absolute() else workspace_root / args.config
    ).resolve(strict=True)
    try:
        if args.validate_commit_only:
            from voice_coach.training.wavlm_hierarchy_training import (
                validate_s105_commit,
            )

            result = validate_s105_commit(
                workspace_root=workspace_root,
                config_path=config_path,
            )
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        from voice_coach.training.wavlm_hierarchy_contract import (
            prepare_s105_execution,
        )
        from voice_coach.training.wavlm_hierarchy_execution import (
            run_s105_training,
        )

        execution_lock, execution_lock_path = prepare_s105_execution(
            workspace_root=workspace_root,
            config_path=config_path,
        )
        result = run_s105_training(
            workspace_root=workspace_root,
            config_path=config_path,
            execution_lock_path=execution_lock_path,
            execution_lock=execution_lock,
            argv=sys.argv,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:
        # Do not print exception text because it could contain a restricted path.
        print(
            "S105 execution failed: "
            f"{type(error).__module__}.{type(error).__qualname__}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
