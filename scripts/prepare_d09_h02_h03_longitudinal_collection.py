#!/usr/bin/env python3
"""Read-only D09/H02/H03 longitudinal human-research readiness preflight."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.human_research.d09_h02_h03_collection_contract import (
    AccessAudit,
    D09ContractError,
    config_only_result,
    failure_result,
    load_config,
    run_full_preflight,
    validate_static_config,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path("configs/experiments/d09_h02_h03_longitudinal_collection_v1.yaml")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate D09/H02/H03 production-static readiness and stop before "
            "recruitment, consent, participant/session/audio access, QC, "
            "randomization, model/API/GPU access, coaching/reference delivery, "
            "blind rating, metric/statistics/effect analysis, output, or "
            "publication. Feasibility is a separate 15-20-per-arm no-efficacy "
            "pilot. Confirmatory readiness additionally requires the actual "
            "feasibility completion, manual H01 final-N lock, and frozen SAP."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--phase",
        choices=("feasibility", "confirmatory"),
        default="feasibility",
        help="select the predecessor chain to validate (default: feasibility)",
    )
    parser.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "validate only pinned public/static sources, strict schemas, "
            "unresolved templates, and zero-runtime policy; no actual evidence "
            "or protected artifact path is resolved"
        ),
    )
    return parser


def _boundary_result() -> dict[str, bool]:
    return {
        "actual_portal_or_institution_contact": False,
        "actual_recruitment_or_consent": False,
        "actual_participant_session_audio_or_d1_access": False,
        "actual_randomization_or_intervention": False,
        "actual_model_gpu_api_or_network_access": False,
        "actual_coaching_reference_or_blind_rating": False,
        "actual_metric_statistics_or_effect": False,
        "artifact_created": False,
        "completion_or_publication_claim_allowed": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
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
            payload = {
                **config_only_result(static, audit),
                "requested_phase": args.phase,
                **_boundary_result(),
            }
        else:
            result = run_full_preflight(
                workspace_root=root,
                config_path=config_path,
                config=config,
                phase=args.phase,
            )
            payload = {
                "status": result.status,
                "phase": result.phase,
                "config_sha256": result.config_sha256,
                "h00_approval_sha256": result.h00_approval_sha256,
                "h00_attestation_sha256": result.h00_attestation_sha256,
                "feasibility_completion_sha256": (result.feasibility_completion_sha256),
                "h01_lock_sha256": result.h01_lock_sha256,
                "sap_sha256": result.sap_sha256,
                "delivery_receipt_sha256": result.delivery_receipt_sha256,
                "preregistration_sha256": result.preregistration_sha256,
                "manual_sha256": result.manual_sha256,
                "authorization_sha256": result.authorization_sha256,
                **result.audit,
                **_boundary_result(),
            }
    except D09ContractError as exc:
        print(
            json.dumps(
                {**failure_result(exc), **_boundary_result()},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    except OSError as exc:
        print(
            json.dumps(
                {
                    "status": "blocked_fail_closed",
                    "stage": "filesystem",
                    "error": str(exc),
                    "audit": AccessAudit().as_dict(),
                    **_boundary_result(),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
