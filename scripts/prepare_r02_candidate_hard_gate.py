#!/usr/bin/env python3
"""Validate R02 static policy or its read-only predecessor-first preflight."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.references.r02_hard_gate_contract import (
    AccessAudit,
    R02ContractError,
    config_only_result,
    failure_result,
    load_config,
    run_full_preflight,
    validate_static_config,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path("configs/experiments/r02_candidate_hard_gate_v1.yaml")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate WP7 R02 candidate hard-gate static readiness. This "
            "command does not resolve D1 audio before R00/R01/preregistration/"
            "attestations, read audio or models, download checkpoints, query "
            "a GPU, compute metrics, open S1/S2 or gold/locked data, write a "
            "gate report, unblind source labels, or publish candidates."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "validate only C1/public source pins, schema, templates and policy; "
            "R00/R01, attestations, D1 audio, model, GPU, metric and output "
            "paths remain untouched"
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
                "policy_sha256": preflight.policy_sha256,
                **preflight.audit,
                "r02_metric_runner_implemented": False,
                "gate_report_created": False,
                "r02_completion_claim_allowed": False,
                "publication_allowed": False,
            }
    except R02ContractError as exc:
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
                    "r02_metric_runner_implemented": False,
                    "actual_audio_read": False,
                    "actual_model_read": False,
                    "actual_gpu_access": False,
                    "gate_report_created": False,
                    "r02_completion_claim_allowed": False,
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
