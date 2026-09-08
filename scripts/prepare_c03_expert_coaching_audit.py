#!/usr/bin/env python3
"""Validate C03 static readiness without executing the expert audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.coaching.c03_expert_audit_contract import (
    C03ContractError,
    config_only_result,
    failure_result,
    load_config,
    run_full_preflight,
    validate_static_config,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path("configs/experiments/c03_expert_coaching_audit_v1.yaml")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the fail-closed C03 expert coaching audit readiness "
            "contract. This command does not resolve audit items, read audio "
            "or reviewer display packets, collect ratings, adjudicate, use a "
            "model/GPU/network, compute metrics, write results, or publish."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "validate only C1 source pins, templates, schemas, and fixed policy; "
            "S107/C02 completion, approvals, reviewer attestations, "
            "preregistration, D1/R1/R0 data, ratings, metrics, and output paths "
            "remain untouched"
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
                "s107_commit_sha256": preflight.s107_commit_sha256,
                "c02_commit_sha256": preflight.c02_commit_sha256,
                "approval_sha256": preflight.approval_sha256,
                "reviewer_attestation_sha256": (preflight.reviewer_attestation_sha256),
                "preregistration_sha256": preflight.preregistration_sha256,
                **preflight.audit,
                "actual_expert_or_rating_count": 0,
                "actual_audio_or_protected_row_count": 0,
                "actual_metric_or_result_count": 0,
                "actual_model_gpu_network_count": 0,
                "artifact_created": False,
                "c03_completion_claim_allowed": False,
                "production_release_allowed": False,
                "publication_allowed": False,
            }
    except C03ContractError as exc:
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
                    "actual_expert_or_rating_count": 0,
                    "actual_audio_or_protected_row_count": 0,
                    "actual_metric_or_result_count": 0,
                    "actual_model_gpu_network_count": 0,
                    "artifact_created": False,
                    "c03_completion_claim_allowed": False,
                    "production_release_allowed": False,
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
