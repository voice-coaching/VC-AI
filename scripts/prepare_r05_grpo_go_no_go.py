#!/usr/bin/env python3
"""Validate R05 static policy or its read-only, predecessor-first preflight."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.references.r05_grpo_decision_contract import (
    AccessAudit,
    R05ContractError,
    config_only_result,
    failure_result,
    load_config,
    run_full_preflight,
    validate_static_config,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path("configs/experiments/r05_grpo_go_no_go_v1.yaml")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate aggregate-only R05 GRPO entry decision readiness. "
            "This command does not download or open a model, run F5/StyleTTS/"
            "GRPO or a reward, use a GPU, compute a metric/effect, read audio/"
            "ratings/participant rows, write a report, or authorize training."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "validate pinned C1 sources, the strict A1 schema, and unresolved "
            "draft templates only; every predecessor/external/prereg/auth/model/"
            "GPU/reward/metric/effect/output path remains untouched"
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
                "decision": preflight.decision,
                **preflight.audit,
                "actual_training_download_model_gpu_reward_execution": False,
                "actual_metric_or_effect_estimation": False,
                "decision_report_created": False,
                "r05_completion_claim_allowed": False,
                "publication_allowed": False,
            }
    except R05ContractError as exc:
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
                    "actual_training_download_model_gpu_reward_execution": False,
                    "actual_metric_or_effect_estimation": False,
                    "decision_report_created": False,
                    "grpo_entry_authorized": False,
                    "r05_completion_claim_allowed": False,
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
