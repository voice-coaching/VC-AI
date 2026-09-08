#!/usr/bin/env python3
"""Validate E02 static policy or predecessor-first read-only preflight."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.evaluation.e02_confidence_guard_contract import (
    AccessAudit,
    E02ContractError,
    config_only_result,
    failure_result,
    load_config,
    run_full_preflight,
    validate_static_config,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path("configs/experiments/e02_confidence_guard_v1.yaml")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate E02 confidence-boundary automatic-correction guard "
            "readiness. This command does not read a request or guard input, "
            "invoke an API, deliver coaching advice/reference/corrective "
            "actions, copy a numeric S107 threshold into E02, call a "
            "model/GPU/metric/network/LLM, freely generate text, write an "
            "artifact, release to production, or claim E02 completion."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "validate only 25 pinned C1/static sources, strict schemas, and "
            "the fail-closed preregistration template; S107/C02 evidence, "
            "request/API/coaching/reference, data/model/GPU/metric/network/"
            "LLM, and outputs remain untouched"
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
                "s107_commit_sha256": preflight.s107_commit_sha256,
                "s107_threshold_report_sha256": (
                    preflight.s107_threshold_report_sha256
                ),
                "c02_completion_commit_sha256": (
                    preflight.c02_completion_commit_sha256
                ),
                "c02_selection_output_sha256": (preflight.c02_selection_output_sha256),
                "e02_preregistration_sha256": (preflight.e02_preregistration_sha256),
                **preflight.audit,
                "actual_request_coaching_reference_or_api_execution": False,
                "actual_model_gpu_metric_network_llm_execution": False,
                "artifact_created": False,
                "e02_completion_claim_allowed": False,
                "production_release_allowed": False,
                "publication_allowed": False,
            }
    except E02ContractError as exc:
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
                    **AccessAudit().as_dict(),
                    "actual_request_coaching_reference_or_api_execution": False,
                    "actual_model_gpu_metric_network_llm_execution": False,
                    "artifact_created": False,
                    "e02_completion_claim_allowed": False,
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
