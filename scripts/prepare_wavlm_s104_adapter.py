#!/usr/bin/env python3
"""Prepare the immutable CPU-only execution lock for WP4 S104.

This command never imports torch, initializes CUDA, opens raw audio, or runs
training.  ``--config-only`` is intentionally usable before S103 completes;
the default mode fails closed until the committed S103 comparison and all
train/dev source pins are present and valid.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.training.wavlm_adapter_contract import (
    S104ContractError,
    load_config,
    prepare_s104_execution,
    sha256_file,
    validate_config,
    validate_implementation_sources,
)


SCRIPT_PATH = Path(__file__).resolve()
REPOSITORY_ROOT = SCRIPT_PATH.parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the S104 adapter preregistration and, after S103, "
            "publish an immutable aggregate-only execution lock."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/experiments/wavlm_s104_adapter_v1.yaml"),
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=REPOSITORY_ROOT,
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Override outputs.execution_lock (tests or reviewed reruns only).",
    )
    parser.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "Validate only static policy and source pins; do not read S103, "
            "data, artifacts, raw audio, or CUDA and do not write a file."
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
                        "cpu_only": True,
                        "gpu_training_executed": False,
                        "raw_audio_opened": False,
                        "s103_read": False,
                        "data_read": False,
                        "output_written": False,
                        "config_sha256": sha256_file(config_path),
                        "implementation": implementation,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        payload, output_path = prepare_s104_execution(
            workspace_root=workspace_root,
            config_path=config_path,
            output_path=args.output,
        )
    except (S104ContractError, OSError, ValueError) as error:
        print(f"S104 preflight failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": payload["status"],
                "selected_candidate": payload["selected_candidate"]["variant"],
                "s103_gate_status": payload["s103_branch_evidence"]["gate_status"],
                "included_utterances": payload["data_contract"]["included_utterances"],
                "execution_lock": output_path.as_posix(),
                "execution_lock_sha256": sha256_file(output_path),
                "gpu_training_executed": False,
                "raw_audio_opened": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
