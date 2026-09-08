#!/usr/bin/env python3
"""Validate D07 static readiness without opening human-review data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.evaluation.d07_expert_gold_contract import (
    AccessAudit,
    D07ContractError,
    config_only_result,
    failure_result,
    load_config,
    run_full_preflight,
    validate_static_config,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path("configs/experiments/d07_independent_expert_gold_v1.yaml")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the fail-closed D07 independent expert gold contract. "
            "This command never opens the G04 UI state, D07 item/locator/audio/"
            "rating or locked-test paths, recruits reviewers, accesses model "
            "outputs, computes agreement, writes a result, or publishes."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "validate C1/A1 source pins, strict schemas, unresolved templates, "
            "and the G04 T43/I30 aggregate only; reviewers, D1/R1/R0, audio, "
            "ratings, locked access, models, metrics, and outputs remain "
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
            audit = AccessAudit()
            static = validate_static_config(
                workspace_root=root,
                config_path=config_path,
                config=config,
                audit=audit,
            )
            result = config_only_result(static, audit)
        else:
            preflight = run_full_preflight(
                workspace_root=root,
                config_path=config_path,
                config=config,
            )
            result = {
                "status": preflight.status,
                "config_sha256": preflight.config_sha256,
                "reviewer_attestation_sha256": (preflight.reviewer_attestation_sha256),
                "preregistration_sha256": preflight.preregistration_sha256,
                **preflight.audit,
                "g04_t43_completed_count": 0,
                "g04_t43_expected_count": 43,
                "g04_i30_completed_count": 0,
                "g04_i30_expected_count": 30,
                "actual_expert_count": 0,
                "actual_rating_count": 0,
                "actual_locked_access_count": 0,
                "actual_metric_count": 0,
                "actual_result_count": 0,
                "artifact_created": False,
                "d07_completion_claim_allowed": False,
                "publication_allowed": False,
            }
    except D07ContractError as exc:
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
                    "g04_t43_completed_count": 0,
                    "g04_t43_expected_count": 43,
                    "g04_i30_completed_count": 0,
                    "g04_i30_expected_count": 30,
                    "actual_expert_count": 0,
                    "actual_rating_count": 0,
                    "actual_locked_access_count": 0,
                    "actual_metric_count": 0,
                    "actual_result_count": 0,
                    "artifact_created": False,
                    "d07_completion_claim_allowed": False,
                    "publication_allowed": False,
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
