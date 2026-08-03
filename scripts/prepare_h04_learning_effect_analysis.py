#!/usr/bin/env python3
"""Validate H04 static policy or predecessor-first read-only preflight."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.human_research.h04_learning_effect_contract import (
    AccessAudit,
    H04ContractError,
    config_only_result,
    failure_result,
    load_config,
    run_full_preflight,
    validate_static_config,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path("configs/experiments/h04_learning_effect_analysis_v1.yaml")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate H04 learning-effect statistical-analysis readiness. "
            "This command does not resolve or read participant/rating rows, "
            "audio, transcripts, restricted D1 data, or raw locators; run a "
            "statistical model, missing-data analysis, effect estimate, model, "
            "GPU, or network operation; generate a statistical report; write "
            "an artifact; publish results; or claim H04 completion. The gated "
            "order is actual H00 authorization, a distinct completed "
            "feasibility-pilot aggregate, H01 manual sample-size lock, frozen "
            "SAP, confirmatory H02/H03 blind bundle, and independent analyst "
            "attestation."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "validate only 19 pinned C1/A1 static sources, strict schemas, and "
            "fail-closed SAP/analyst templates; H00 evidence, feasibility and "
            "confirmatory bundles, participant/rating/audio/D1 data, "
            "statistics/effects, model/GPU/network, and outputs remain "
            "untouched"
        ),
    )
    return parser


def _boundary_result() -> dict[str, bool]:
    return {
        "actual_participant_rating_audio_or_restricted_data_access": False,
        "actual_model_gpu_network_or_statistics_execution": False,
        "actual_effect_estimate_or_report": False,
        "artifact_created": False,
        "h04_completion_claim_allowed": False,
        "publication_allowed": False,
    }


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
                "h00_approval_sha256": preflight.h00_approval_sha256,
                "h00_attestation_sha256": preflight.h00_attestation_sha256,
                "feasibility_pilot_completion_sha256": (
                    preflight.feasibility_pilot_completion_sha256
                ),
                "h01_completion_sha256": preflight.h01_completion_sha256,
                "sap_sha256": preflight.sap_sha256,
                "h02_h03_completion_sha256": (preflight.h02_h03_completion_sha256),
                "analyst_attestation_sha256": (preflight.analyst_attestation_sha256),
                **preflight.audit,
                **_boundary_result(),
            }
    except H04ContractError as exc:
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
                    **_boundary_result(),
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
