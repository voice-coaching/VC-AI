#!/usr/bin/env python3
"""Read-only E00 closed-loop production-static preflight."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from voice_coach.evaluation.e00_closed_loop_contract import (
    AccessAudit,
    E00ContractError,
    config_only_result,
    failure_result,
    load_config,
    run_full_preflight,
    validate_static_config,
)

DEFAULT_CONFIG = Path("configs/experiments/e00_closed_loop_demo_v1.yaml")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the fail-closed E00 production-static contract. "
            "This command never resolves a session, reads audio, runs S108, "
            "invokes E02/API, delivers C02 coaching or R03/D08 reference, "
            "records attempt 2, computes a comparison, or writes output. "
            "A nonhuman fixture label applies only to learner attempt input; "
            "the separately licensed D08 human reference remains outside that "
            "scope. An independent R01 human candidate requires an exact "
            "D08 metadata-row, audio, and rights lineage receipt."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "validate only C1 static pins, strict schemas, unresolved templates "
            "and zero-runtime policy; all predecessor, governance, session, "
            "audio, model, API, delivery, metric and output paths remain untouched"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.workspace_root.resolve()
    config_path = args.config
    if not config_path.is_absolute():
        config_path = root / config_path
    try:
        config = load_config(config_path)
        if args.config_only:
            audit = AccessAudit()
            static = validate_static_config(
                workspace_root=root,
                config_path=config_path,
                config=config,
                audit=audit,
            )
            payload = config_only_result(static, audit)
        else:
            result = run_full_preflight(
                workspace_root=root,
                config_path=config_path,
                config=config,
            )
            payload = {
                "status": result.status,
                "config_sha256": result.config_sha256,
                "execution_mode": result.execution_mode,
                "preregistration_sha256": result.preregistration_sha256,
                "authorization_sha256": result.authorization_sha256,
                "reference_handoff_sha256": result.reference_handoff_sha256,
                "predecessor_chain_sha256": result.predecessor_chain_sha256,
                **result.audit,
                "artifact_created": False,
                "completion_claim_allowed": False,
                "production_deployment_claim_allowed": False,
            }
    except E00ContractError as exc:
        print(
            json.dumps(
                failure_result(exc),
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
