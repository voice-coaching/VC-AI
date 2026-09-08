#!/usr/bin/env python3
"""Run/recover or deeply validate S107 calibration-only evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.training.wavlm_s107_calibration_contract import S107ContractError
from voice_coach.training.wavlm_s107_calibration_training import (
    S107CalibrationError,
    run_s107_calibration,
    validate_s107_commit,
)


SCRIPT_PATH = Path(__file__).resolve()
REPOSITORY_ROOT = SCRIPT_PATH.parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fit one binary temperature and provisional issue threshold on "
            "frozen-S106 calibration predictions, or validate its hash chain."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/experiments/wavlm_s107_calibration_v1.yaml"),
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=REPOSITORY_ROOT,
    )
    parser.add_argument(
        "--validate-commit-only",
        action="store_true",
        help="Read-only deep recomputation; never write or recalibrate.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        root = args.workspace_root.resolve(strict=True)
        config_path = args.config
        if not config_path.is_absolute():
            config_path = root / config_path
        config_path = config_path.resolve(strict=True)
        if args.validate_commit_only:
            result = validate_s107_commit(
                workspace_root=root,
                config_path=config_path,
            )
            mode = "validate_commit_only"
        else:
            result = run_s107_calibration(
                workspace_root=root,
                config_path=config_path,
            )
            mode = "calibrate_or_recover"
    except (
        S107ContractError,
        S107CalibrationError,
        OSError,
        ValueError,
    ) as error:
        print(f"S107 calibration failed: {error}", file=sys.stderr)
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
                "temperature": result["temperature"],
                "issue_threshold": result["issue_threshold"],
                "ece_15_equal_width": result["ece_15_equal_width"],
                "brier": result["brier"],
                "issued_precision": result["issued_precision"],
                "label_status": result["label_status"],
                "commit_sha256": result["commit_sha256"],
                "registry_sha256": result["registry_sha256"],
                "promotion_decision_allowed": False,
                "s108_ready": False,
                "locked_test_accessed": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
