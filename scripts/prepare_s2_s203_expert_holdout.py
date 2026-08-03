#!/usr/bin/env python3
"""Validate S203 static readiness without opening the expert holdout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.training.s2_s203_holdout_contract import (
    S203ContractError,
    config_only_result,
    failure_result,
    load_config,
    run_full_preflight,
    validate_static_config,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path("configs/experiments/s2_s203_expert_holdout_audit_v1.yaml")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the S203 expert-holdout one-shot audit contract. This "
            "command does not open holdout data, read frozen predictions, "
            "join evaluation rows, retrain/reselect a model, use a GPU, "
            "compute metrics, or publish results."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "validate only C1 policy/source pins; predecessor commits, "
            "preregistration, expert attestation, model, prediction, data, "
            "holdout, metric, and output paths remain untouched"
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
            run_full_preflight(
                workspace_root=root,
                config_path=config_path,
                config=config,
            )
            raise AssertionError("S203 static preflight must stop fail-closed")
    except S203ContractError as exc:
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
                    "production_evaluator_implemented": False,
                    "holdout_access_allowed": False,
                    "metric_generation_allowed": False,
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
