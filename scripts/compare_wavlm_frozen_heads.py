#!/usr/bin/env python3
"""Compare completed WavLM Base+ and Large frozen-head dev predictions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.evaluation.wavlm_comparison import (
    ComparisonContractError,
    compare_wavlm_frozen_heads,
    load_and_validate_config,
)


SCRIPT_PATH = Path(__file__).resolve()
REPOSITORY_ROOT = SCRIPT_PATH.parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate completed Base+ and Large frozen-head artifacts and "
            "transactionally commit the aggregate-only S103 report and "
            "execution receipt."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/experiments/wavlm_frozen_head_comparison_v1.yaml"),
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=REPOSITORY_ROOT,
    )
    parser.add_argument(
        "--commit-manifest",
        type=Path,
        help="Override output.commit_manifest from the comparison config.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = compare_wavlm_frozen_heads(
            config_path=args.config,
            workspace_root=args.workspace_root,
            script_path=SCRIPT_PATH,
            commit_path=args.commit_manifest,
        )
    except (ComparisonContractError, OSError, ValueError) as error:
        print(f"comparison failed: {error}", file=sys.stderr)
        return 1

    bootstrap = report["primary_comparison"]["paired_speaker_bootstrap"]
    gate = report["primary_comparison"]["gate"]
    resolved_config = (
        args.config if args.config.is_absolute() else args.workspace_root / args.config
    )
    config = load_and_validate_config(resolved_config)
    configured_commit = (
        args.commit_manifest
        if args.commit_manifest is not None
        else Path(config["output"]["commit_manifest"])
    )
    commit_path = (
        configured_commit
        if configured_commit.is_absolute()
        else args.workspace_root / configured_commit
    )
    commit = json.loads(commit_path.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "status": report["status"],
                "development_only": report["development_only"],
                "oracle_alignment": report["oracle_alignment"],
                "phone_macro_f1_large_minus_base": bootstrap["estimate"],
                "confidence_lower": bootstrap["confidence_lower"],
                "confidence_upper": bootstrap["confidence_upper"],
                "gate_status": gate["status"],
                "commit_manifest": commit_path.resolve().as_posix(),
                "comparison_report": commit["comparison_report"]["path"],
                "execution_receipt": commit["execution_receipt"]["path"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
