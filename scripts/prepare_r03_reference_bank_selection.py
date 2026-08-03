#!/usr/bin/env python3
"""Validate R03 static policy or its read-only predecessor-first preflight."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.references.r03_selection_contract import (
    AccessAudit,
    R03ContractError,
    config_only_result,
    failure_result,
    load_config,
    run_full_preflight,
    validate_static_config,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path("configs/experiments/r03_reference_bank_selection_v1.yaml")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate R03 Pareto/diversity reference-bank static readiness. "
            "This command does not open candidate audio, run a model or GPU, "
            "compute an objective or selection, read R04 labels, create a "
            "bank, or publish anything."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "validate pinned C1/static sources, bank schema, and the fail-closed "
            "preregistration template only; predecessor completion, D1 eligible "
            "candidate data, audio/model/GPU/metrics/R04/output remain untouched"
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
                "r00_report_sha256": preflight.r00_report_sha256,
                "r01_commit_sha256": preflight.r01_commit_sha256,
                "r02_commit_sha256": preflight.r02_commit_sha256,
                "preregistration_sha256": preflight.preregistration_sha256,
                **preflight.audit,
                "actual_audio_or_model_or_gpu_execution": False,
                "actual_metric_or_selection_execution": False,
                "bank_manifest_created": False,
                "r03_completion_claim_allowed": False,
                "publication_allowed": False,
            }
    except R03ContractError as exc:
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
                    "actual_audio_or_model_or_gpu_execution": False,
                    "actual_metric_or_selection_execution": False,
                    "bank_manifest_created": False,
                    "r03_completion_claim_allowed": False,
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
