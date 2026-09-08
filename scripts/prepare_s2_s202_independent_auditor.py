#!/usr/bin/env python3
"""Validate the S202 static policy or its read-only predecessor preflight."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.training.s2_s202_auditor_contract import (
    S202ContractError,
    config_only_result,
    failure_result,
    load_config,
    run_full_preflight,
    validate_static_config,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path("configs/experiments/s2_s202_independent_auditor_v1.yaml")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate S202 independent-auditor static readiness. This command "
            "does not download or run a model, read row-level data/audio, "
            "train pairwise or ordinal heads, use a GPU, compute metrics, "
            "create checkpoints, or access the expert holdout."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "validate C1 policy/source pins only; predecessor, preregistration, "
            "external, data, model, protected, holdout, and output paths stay "
            "untouched"
        ),
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        root = args.workspace_root.resolve(strict=True)
        config_path = args.config
        if not config_path.is_absolute():
            config_path = root / config_path
        config = load_config(config_path)
        if args.config_only:
            static = validate_static_config(
                workspace_root=root,
                config_path=config_path,
                config=config,
            )
            result = config_only_result(static)
        else:
            preflight = run_full_preflight(
                workspace_root=root,
                config_path=config_path,
                config=config,
            )
            result = {
                "status": preflight.status,
                "config_sha256": preflight.config_sha256,
                "s108_commit_sha256": preflight.s108_commit_sha256,
                "s200_readiness_lock_sha256": preflight.s200_readiness_lock_sha256,
                "s201_commit_sha256": preflight.s201_commit_sha256,
                **preflight.audit,
                "production_trainer_implemented": False,
                "execution_allowed": False,
                "completion_claim_allowed": False,
            }
    except S202ContractError as exc:
        print(
            json.dumps(failure_result(exc), ensure_ascii=False, sort_keys=True),
            file=sys.stderr,
        )
        return 1
    except OSError as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "stage": "filesystem",
                    "error": str(exc),
                    "production_trainer_implemented": False,
                    "execution_allowed": False,
                    "completion_claim_allowed": False,
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
