#!/usr/bin/env python3
"""Validate WP4 S106 preregistration or publish its CPU-only execution lock.

``--config-only`` does not import predecessor validators, resolve the stack
locator, read any data split, initialize a GPU, or write output.  Normal mode
is fail-closed: completed S104, the finalized S105 adoption commit, and the
exact B07 commit are independently validated before stack locator resolution.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.training.wavlm_s106_fusion_contract import (
    S106ContractError,
    load_config,
    prepare_s106_execution,
    sha256_file,
    validate_config,
    validate_implementation_sources,
)


SCRIPT_PATH = Path(__file__).resolve()
REPOSITORY_ROOT = SCRIPT_PATH.parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the frozen-component S106 stack-OOF fusion contract. "
            "Normal mode publishes only a no-metric CPU preflight lock."
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
        "--output",
        type=Path,
        help="Override outputs.execution_lock for a reviewed synthetic run.",
    )
    parser.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "Validate static policy and S106 source pins only; do not import "
            "S104/S105/B07 validators, resolve/read stack, or write output."
        ),
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
        if args.config_only:
            implementation = validate_implementation_sources(
                workspace_root=workspace_root,
                config=config,
            )
            print(
                json.dumps(
                    {
                        "status": "config_valid",
                        "cpu_only": True,
                        "gpu_initialized": False,
                        "predecessor_validators_imported": False,
                        "s104_read": False,
                        "s105_read": False,
                        "b07_read": False,
                        "predecessor_audit_train_read": False,
                        "predecessor_audit_dev_read": False,
                        "predecessor_audit_dev_predictions_read": False,
                        "fusion_feature_train_read": False,
                        "fusion_feature_dev_read": False,
                        "b07_dev_predictions_reused_as_component": False,
                        "component_prediction_files_written": 0,
                        "explicit_component_read": False,
                        "stack_path_resolved": False,
                        "stack_read": False,
                        "calibration_read": False,
                        "locked_test_read": False,
                        "output_written": False,
                        "config_sha256": sha256_file(config_path),
                        "implementation": implementation,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        output_path = args.output
        if output_path is not None and not output_path.is_absolute():
            output_path = workspace_root / output_path
        payload, destination = prepare_s106_execution(
            workspace_root=workspace_root,
            config_path=config_path,
            output_path=output_path,
        )
    except (S106ContractError, OSError, ValueError) as error:
        print(f"S106 preflight failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": payload["status"],
                "execution_lock": destination.relative_to(workspace_root).as_posix(),
                "execution_lock_sha256": sha256_file(destination),
                "selected_s104_variant": payload["predecessors"]["s104"][
                    "selected_variant"
                ],
                "selected_s105_variant": payload["predecessors"]["s105"][
                    "selected_variant"
                ],
                "selected_b07_variant": payload["predecessors"]["b07"][
                    "selected_variant"
                ],
                "predecessor_audit_train_read": True,
                "predecessor_audit_dev_read": True,
                "predecessor_audit_dev_predictions_read": True,
                "s104_deep_validator_invocation_count": 2,
                "s105_deep_validator_invocation_count": 1,
                "b07_deep_validator_invocation_count": 1,
                "fusion_feature_train_read": False,
                "fusion_feature_dev_read": False,
                "b07_dev_predictions_reused_as_component": False,
                "component_prediction_files_written": 0,
                "gpu_initialized": False,
                "stack_rows_read_by_preflight": False,
                "metrics_computed": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
