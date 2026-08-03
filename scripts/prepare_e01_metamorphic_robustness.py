#!/usr/bin/env python3
"""Validate E01 static policy or its read-only predecessor-first preflight."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.evaluation.e01_metamorphic_contract import (
    AccessAudit,
    E01ContractError,
    config_only_result,
    failure_result,
    load_config,
    run_full_preflight,
    validate_static_config,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path("configs/experiments/e01_metamorphic_robustness_v1.yaml")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate E01 metamorphic robustness static readiness. This command "
            "does not read audio or a locked/S203 holdout row, execute silence/"
            "gain/duration/F0/phone perturbation, open/run a model, use network/"
            "GPU, compute a metric/decision, or write/publish an output."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "validate C1 source pins, strict input/output schemas, the fixed "
            "0.95 gate, and unresolved preregistration template only; all "
            "predecessor/data/model/network/GPU/metric/output paths stay untouched"
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
                "s108_commit_sha256": preflight.s108_commit_sha256,
                "preregistration_sha256": preflight.preregistration_sha256,
                **preflight.audit,
                "actual_audio_perturbation_or_model_execution": False,
                "actual_metric_or_decision": False,
                "locked_test_accessed": False,
                "s203_expert_holdout_accessed": False,
                "report_created": False,
                "e01_completion_claim_allowed": False,
                "publication_allowed": False,
            }
    except E01ContractError as exc:
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
                    "actual_audio_perturbation_or_model_execution": False,
                    "actual_metric_or_decision": False,
                    "locked_test_accessed": False,
                    "s203_expert_holdout_accessed": False,
                    "report_created": False,
                    "e01_completion_claim_allowed": False,
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
