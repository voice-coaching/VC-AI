#!/usr/bin/env python3
"""Run or deeply validate WP4 S106 stack-only OOF fusion.

The command requires a validated S106 execution lock and a content-addressed
four-component frozen bundle.  With the canonical config it fails before any
stack row is opened while S104/S105 commits are absent.  B07 is replayed only
from serialized acoustic/error checkpoints; ``--validate-commit-only`` is
strictly read-only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.training.wavlm_s106_fusion_contract import S106ContractError
from voice_coach.training.wavlm_s106_fusion_training import (
    S106TrainingError,
    run_s106_training,
    validate_s106_commit,
)


SCRIPT_PATH = Path(__file__).resolve()
REPOSITORY_ROOT = SCRIPT_PATH.parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Train the deterministic five-fold S106 stack fusion or deeply "
            "validate its immutable commit."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/experiments/wavlm_s106_stack_fusion_v1.yaml"),
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=REPOSITORY_ROOT,
    )
    parser.add_argument(
        "--validate-commit-only",
        action="store_true",
        help="Recompute and validate existing evidence without writing.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        workspace_root = args.workspace_root.resolve(strict=True)
        config_path = args.config
        if not config_path.is_absolute():
            config_path = workspace_root / config_path
        config_path = config_path.resolve(strict=True)
        if args.validate_commit_only:
            result = validate_s106_commit(
                workspace_root=workspace_root,
                config_path=config_path,
            )
            mode = "validate_commit_only"
        else:
            result = run_s106_training(
                workspace_root=workspace_root,
                config_path=config_path,
            )
            mode = "train_or_recover"
    except (S106ContractError, S106TrainingError, OSError, ValueError) as error:
        print(f"S106 training failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": result["status"],
                "mode": mode,
                "generation_id": result["generation_id"],
                "artifact_count": result["artifact_count"],
                "row_count": result["row_count"],
                "speaker_count": result["speaker_count"],
                "selected_regularization_c": result["selected_regularization_c"],
                "selected_threshold": result["selected_threshold"],
                "selected_oof_metric": result["selected_oof_metric"],
                "commit_sha256": result["commit_sha256"],
                "registry_sha256": result["registry_sha256"],
                "stack_only": True,
                "full_stack_in_sample_metric_reported": False,
                "calibration_accessed": False,
                "locked_test_accessed": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
