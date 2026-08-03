#!/usr/bin/env python3
"""Run an approved canonical B07 experiment or deeply validate its commit."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


# Set the CPU-only boundary before importing NumPy/scikit-learn transitively.
os.environ["CUDA_VISIBLE_DEVICES"] = ""
for _thread_variable in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "BLIS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ[_thread_variable] = "4"

from voice_coach.training.b07_gop_contract import B07ContractError  # noqa: E402
from voice_coach.training.b07_gop_training import (  # noqa: E402
    B07TrainingError,
    run_b07_training,
    validate_b07_commit,
)


SCRIPT_PATH = Path(__file__).resolve()
REPOSITORY_ROOT = SCRIPT_PATH.parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Execute the explicitly approved CPU-only B07 run or perform a "
            "read-only full commit recomputation."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/experiments/b07_gop_acoustic_posterior_v1.yaml"),
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=REPOSITORY_ROOT,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--execute-canonical",
        action="store_true",
        help=(
            "Execute only after both reviewed config permission flags and the "
            "approved status have been committed."
        ),
    )
    mode.add_argument(
        "--validate-commit-only",
        action="store_true",
        help="Read-only full bounded recomputation of an existing commit.",
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
            result = validate_b07_commit(
                workspace_root=workspace_root,
                config_path=config_path,
            )
            mode = "validate_commit_only"
        else:
            result = run_b07_training(
                workspace_root=workspace_root,
                config_path=config_path,
                execute_canonical=True,
            )
            mode = "execute_canonical"
    except (
        B07ContractError,
        B07TrainingError,
        OSError,
        ValueError,
    ) as error:
        print(
            f"B07 {mode if 'mode' in locals() else 'runner'} failed: {error}",
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                **dict(result),
                "mode": mode,
                "cpu_only": True,
                "accelerator_probe_performed": False,
                "torch_imported": False,
                "cuda_initialized": False,
                "stack_accessed": False,
                "calibration_accessed": False,
                "locked_test_accessed": False,
                "raw_manifest_accessed": False,
                "environment_file_accessed": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
