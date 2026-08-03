#!/usr/bin/env python3
"""Validate C02 static policy or its predecessor-first read-only preflight."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.coaching.c02_selection_contract import (
    AccessAudit,
    C02ContractError,
    config_only_result,
    failure_result,
    load_config,
    run_full_preflight,
    validate_static_config,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path(
    "configs/experiments/c02_deterministic_coaching_selection_v1.yaml"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate C02 deterministic top-1/top-3 ontology-only coaching "
            "readiness. This command does not read diagnostic issue rows, "
            "select or generate actual coaching, estimate effects, call a "
            "runtime model/GPU/network/LLM, resolve output paths, write "
            "artifacts, claim C02 completion, or release to production."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "validate only 19 pinned C1/static sources, strict schemas, and "
            "the unresolved preregistration template; C01/S107 deep evidence, "
            "D1 issue rows, coaching, models, GPUs, network, LLMs, and outputs "
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
                "c01_report_sha256": preflight.c01_report_sha256,
                "s107_commit_sha256": preflight.s107_commit_sha256,
                "s107_threshold_report_sha256": (
                    preflight.s107_threshold_report_sha256
                ),
                "preregistration_sha256": preflight.preregistration_sha256,
                **preflight.audit,
                "actual_diagnostic_or_coaching_execution": False,
                "actual_effect_estimation": False,
                "actual_model_gpu_network_or_llm_execution": False,
                "artifact_created": False,
                "c02_completion_claim_allowed": False,
                "production_release_allowed": False,
                "publication_allowed": False,
            }
    except C02ContractError as exc:
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
                    "actual_diagnostic_or_coaching_execution": False,
                    "actual_effect_estimation": False,
                    "actual_model_gpu_network_or_llm_execution": False,
                    "artifact_created": False,
                    "c02_completion_claim_allowed": False,
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
