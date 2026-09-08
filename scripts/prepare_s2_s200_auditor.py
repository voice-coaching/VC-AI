#!/usr/bin/env python3
"""Validate the S200 contract or fail closed before any S2 data/model access."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.training.s2_s200_auditor_contract import (
    S200ContractError,
    config_only_result,
    failure_result,
    load_config,
    run_full_preflight,
    validate_static_config,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path("configs/experiments/s2_s200_independent_auditor_v1.yaml")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate WP5 S200 independence/data-card policy. This command "
            "does not implement S201/S202, download models, read row-level "
            "data, open holdouts, use a GPU, or compute metrics."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "validate only C1 policy/source pins; predecessor, external "
            "attestation, data, models, outputs, and GPUs remain untouched"
        ),
    )
    parser.add_argument(
        "--fault-injection",
        choices=["after_temporary_write", "before_publish"],
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
                fault_injection=args.fault_injection,
            )
            result = {
                "status": "readiness_contract_published",
                "readiness_lock_path": preflight.readiness_lock_path.relative_to(
                    root
                ).as_posix(),
                "readiness_lock_sha256": preflight.readiness_lock_sha256,
                **preflight.audit,
                "s200_completion_claim_allowed": False,
                "s201_trainer_implemented": False,
                "s202_trainer_implemented": False,
            }
    except S200ContractError as exc:
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
                    "data_read_count": 0,
                    "model_read_count": 0,
                    "protected_data_read_count": 0,
                    "holdout_read_count": 0,
                    "locked_test_read_count": 0,
                    "gpu_access_count": 0,
                    "metric_count": 0,
                    "result_count": 0,
                    "output_count": 0,
                    "s200_completion_claim_allowed": False,
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
