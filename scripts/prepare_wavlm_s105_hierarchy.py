#!/usr/bin/env python3
"""Validate S105 preregistration or publish its post-S104 execution lock."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.training.wavlm_hierarchy_contract import (
    S105ContractError,
    load_config,
    prepare_s105_execution,
    sha256_file,
    validate_config,
    validate_implementation_sources,
)


SCRIPT_PATH = Path(__file__).resolve()
REPOSITORY_ROOT = SCRIPT_PATH.parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the S105 hierarchy preregistration. Full mode fails "
            "closed until the independently valid S104 commit exists."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/experiments/wavlm_s105_hierarchy_v5_stream_cardinality.yaml"),
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=REPOSITORY_ROOT,
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "Read only the S105 config and pinned implementation sources; "
            "do not read S104, data, raw audio, CUDA, or write an output."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        workspace_root = args.workspace_root.resolve(strict=True)
        config_path = (
            args.config if args.config.is_absolute() else workspace_root / args.config
        ).resolve(strict=True)
        config = load_config(config_path)
        validate_config(config)
        if args.config_only:
            implementation, _ = validate_implementation_sources(
                workspace_root=workspace_root,
                config=config,
            )
            print(
                json.dumps(
                    {
                        "status": "config_valid",
                        "config_sha256": sha256_file(config_path),
                        "implementation": implementation,
                        "s104_read": False,
                        "s104_dev_result_read": False,
                        "data_read": False,
                        "raw_audio_opened": False,
                        "cuda_initialized": False,
                        "output_written": False,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        payload, output_path = prepare_s105_execution(
            workspace_root=workspace_root,
            config_path=config_path,
            output_path=args.output,
        )
    except (S105ContractError, OSError, ValueError) as error:
        print(f"S105 preflight failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": payload["status"],
                "execution_lock": output_path.relative_to(workspace_root).as_posix(),
                "execution_lock_sha256": sha256_file(output_path),
                "predecessor_validation": "valid",
                "gpu_training_executed": False,
                "raw_audio_opened": False,
                "locked_test_accessed": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
