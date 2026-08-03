#!/usr/bin/env python3
"""Validate S107 policy or publish its no-row/no-metric execution lock."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.training.wavlm_s107_calibration_contract import (
    S107ContractError,
    load_config,
    prepare_s107_execution,
    sha256_file,
    validate_config,
    validate_implementation_sources,
)


SCRIPT_PATH = Path(__file__).resolve()
REPOSITORY_ROOT = SCRIPT_PATH.parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate S107 frozen-scorer calibration policy. Normal mode first "
            "deep-validates S106, then hashes the calibration bundle without "
            "reading a row, and publishes a no-metric execution lock."
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
        "--output",
        type=Path,
        help="Override execution-lock output for a reviewed synthetic run.",
    )
    parser.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "Validate static policy/source pins only; do not import S106, "
            "resolve calibration paths, read data, or write output."
        ),
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
        config = load_config(config_path)
        validate_config(config)
        if args.config_only:
            implementation = validate_implementation_sources(
                workspace_root=root,
                config=config,
            )
            print(
                json.dumps(
                    {
                        "status": "config_valid",
                        "cpu_only": True,
                        "gpu_initialized": False,
                        "s106_validator_imported": False,
                        "s106_commit_read": False,
                        "calibration_path_resolved": False,
                        "calibration_rows_read": False,
                        "train_read": False,
                        "dev_read": False,
                        "stack_read": False,
                        "locked_test_read": False,
                        "metrics_computed": False,
                        "output_written": False,
                        "config_sha256": sha256_file(config_path),
                        "implementation": implementation,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        output = args.output
        if output is not None and not output.is_absolute():
            output = root / output
        lock, destination = prepare_s107_execution(
            workspace_root=root,
            config_path=config_path,
            output_path=output,
        )
    except (S107ContractError, OSError, ValueError) as error:
        print(f"S107 preflight failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": lock["status"],
                "execution_lock": destination.relative_to(root).as_posix(),
                "execution_lock_sha256": sha256_file(destination),
                "s106_validation_status": lock["predecessor"]["validation_status"],
                "label_status": lock["label_status"],
                "calibration_rows_read": False,
                "metrics_computed": False,
                "gpu_initialized": False,
                "promotion_decision_allowed": False,
                "s108_ready": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
