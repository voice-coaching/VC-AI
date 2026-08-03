#!/usr/bin/env python3
"""Generate or deeply validate the production S106 component-input bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.training.wavlm_s106_component_generator import (
    S106ComponentGeneratorError,
    run_component_generation,
    validate_component_generation_commit,
    validate_static_generator,
)
from voice_coach.training.wavlm_s106_fusion_contract import S106ContractError


SCRIPT_PATH = Path(__file__).resolve()
REPOSITORY_ROOT = SCRIPT_PATH.parents[1]
DEFAULT_CONFIG = Path("configs/experiments/wavlm_s106_component_inputs_v1.yaml")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create the exact 4-component x 3-seed S106 stack projection "
            "bundle with a producer-side label-isolation barrier."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=REPOSITORY_ROOT,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "Validate generator/main config and pinned implementation sources; "
            "do not read predecessors, checkpoints, stack, audio, GPU, or output."
        ),
    )
    mode.add_argument(
        "--validate-commit-only",
        action="store_true",
        help=(
            "Deeply revalidate the completed generator commit, registry, "
            "feature-freeze receipt, 12 Parquet files, labels, and final bundle."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        root = args.workspace_root.resolve(strict=True)
        config = args.config
        if not config.is_absolute():
            config = root / config
        config = config.resolve(strict=True)
        if args.config_only:
            result = validate_static_generator(
                workspace_root=root,
                generator_config_path=config,
            )
        elif args.validate_commit_only:
            result = validate_component_generation_commit(
                workspace_root=root,
                generator_config_path=config,
            )
        else:
            generated = run_component_generation(
                workspace_root=root,
                generator_config_path=config,
            )
            result = {
                "status": "completed",
                "generation_id": generated.generation_id,
                "prediction_file_count": 12,
                "feature_rows": generated.commit["feature_rows"],
                "label_rows": generated.commit["label_rows"],
                "feature_freeze_receipt_sha256": (
                    generated.commit["feature_freeze_receipt"]["sha256"]
                ),
                "component_bundle_sha256": (
                    generated.registry["component_bundle"]["sha256"]
                ),
                "bundle_final_visibility_commit": True,
            }
    except (
        S106ComponentGeneratorError,
        S106ContractError,
        OSError,
        ValueError,
    ) as error:
        print(f"S106 component generator failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
