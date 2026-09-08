#!/usr/bin/env python3
"""Read-only B05/S108 joint locked-evaluation STATIC READINESS preflight."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from voice_coach.evaluation.b05_locked_contract import (
    AccessAudit,
    B05ContractError,
    config_only_result,
    failure_result,
    load_config,
    run_full_preflight,
    validate_static_config,
)

DEFAULT_CONFIG = Path("configs/experiments/b05_explicit_locked_evaluation_v1.yaml")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the fail-closed B05/S108 STATIC READINESS contract. "
            "This command never resolves a locked-test locator, reads a locked "
            "row/audio/label/prediction, deserializes a checkpoint, runs a "
            "model or GPU, computes a metric/bootstrap/Holm result, writes an "
            "artifact, or awards B05/S108 completion. Full mode validates "
            "G04, D07, S107, immutable S108 freeze, no-TBD preregistration and "
            "the exact shared batch; only then may it hash the three B03 "
            "checkpoint files without deserializing them, before validating a "
            "separate one-shot authorization."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "validate only static pins, strict schemas/templates, the exact "
            "B03 boundary and B06 exclusion; all gates, checkpoints, locked "
            "data, models, metrics and output paths remain untouched"
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
                "g04_completion_sha256": result.g04_completion_sha256,
                "d07_completion_sha256": result.d07_completion_sha256,
                "s107_commit_sha256": result.s107_commit_sha256,
                "s108_arm_freeze_sha256": result.s108_arm_freeze_sha256,
                "preregistration_sha256": result.preregistration_sha256,
                "batch_manifest_sha256": result.batch_manifest_sha256,
                "authorization_sha256": result.authorization_sha256,
                **result.audit,
                "artifact_created": False,
                "actual_locked_access_performed": False,
                "actual_model_or_gpu_used": False,
                "numeric_result_produced": False,
                "b05_score_awarded": 0,
                "b05_completion_claim_allowed": False,
                "s108_completion_claim_allowed": False,
                "deployment_claim_allowed": False,
            }
    except B05ContractError as exc:
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
