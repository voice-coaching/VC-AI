#!/usr/bin/env python3
"""Validate the B07 static contract without opening protected phone caches."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


# These values are set before NumPy/scikit-learn can be imported transitively.
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

from voice_coach.training.b07_gop_contract import (  # noqa: E402
    B07ContractError,
    load_config,
    sha256_file,
    validate_config,
    validate_implementation_sources,
)


SCRIPT_PATH = Path(__file__).resolve()
REPOSITORY_ROOT = SCRIPT_PATH.parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate frozen B07 grids, boundaries, sources, and publication "
            "contracts without opening train/dev caches or writing output."
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
    parser.add_argument(
        "--config-only",
        action="store_true",
        required=True,
        help="Required static-only mode; protected data and outputs remain untouched.",
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
        config = load_config(config_path)
        validate_config(config)
        sources = validate_implementation_sources(
            workspace_root=workspace_root,
            config=config,
        )
    except (B07ContractError, OSError, ValueError) as error:
        print(f"B07 config-only preflight failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "config_valid",
                "mode": "config_only",
                "config_sha256": sha256_file(config_path),
                "source_count": len(sources),
                "protected_cache_path_resolved": False,
                "train_metadata_read": False,
                "train_embedding_read": False,
                "dev_metadata_read": False,
                "dev_embedding_read": False,
                "dev_metric_opened": False,
                "stack_read": False,
                "calibration_read": False,
                "locked_test_read": False,
                "raw_manifest_read": False,
                "environment_file_read": False,
                "accelerator_probe_performed": False,
                "torch_imported": False,
                "cuda_initialized": False,
                "output_written": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
