#!/usr/bin/env python3
"""Validate R04 static policy or its read-only predecessor-first preflight."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.references.r04_blind_evaluation_contract import (
    AccessAudit,
    R04ContractError,
    config_only_result,
    failure_result,
    load_config,
    run_full_preflight,
    validate_static_config,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path("configs/experiments/r04_blind_multireference_evaluation_v1.yaml")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate R04 blind multi-reference human-evaluation static readiness. "
            "This command does not recruit listeners, open or write audio/ratings, "
            "run a model or GPU, randomize stimuli, compute MOS/CMOS/statistics, "
            "decide superiority/completion, create artifacts, or publish results."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "validate only pinned C1/static sources, fail-closed approval "
            "templates, and randomization/rating schemas; predecessor commits, "
            "D1 audio/ratings, listeners, models/GPUs, metrics, and outputs "
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
                "r00_report_sha256": preflight.r00_report_sha256,
                "r01_commit_sha256": preflight.r01_commit_sha256,
                "r02_commit_sha256": preflight.r02_commit_sha256,
                "r03_commit_sha256": preflight.r03_commit_sha256,
                "human_approval_sha256": preflight.human_approval_sha256,
                "preregistration_sha256": preflight.preregistration_sha256,
                "pilot_attestation_sha256": preflight.pilot_attestation_sha256,
                "sap_sha256": preflight.sap_sha256,
                "listener_attestation_sha256": (preflight.listener_attestation_sha256),
                **preflight.audit,
                "actual_listener_or_audio_or_rating_execution": False,
                "actual_model_or_gpu_execution": False,
                "actual_mos_cmos_or_statistics_computation": False,
                "actual_superiority_or_completion_decision": False,
                "artifact_created": False,
                "r04_completion_claim_allowed": False,
                "publication_allowed": False,
            }
    except R04ContractError as exc:
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
                    "actual_listener_or_audio_or_rating_execution": False,
                    "actual_model_or_gpu_execution": False,
                    "actual_mos_cmos_or_statistics_computation": False,
                    "actual_superiority_or_completion_decision": False,
                    "artifact_created": False,
                    "r04_completion_claim_allowed": False,
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
