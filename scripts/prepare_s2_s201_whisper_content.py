#!/usr/bin/env python3
"""Validate S201 static policy or run its read-only fail-closed preflight."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.training.s2_s201_whisper_contract import (
    S201ContractError,
    config_only_result,
    failure_result,
    load_config,
    run_full_preflight,
    validate_static_config,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path("configs/experiments/s2_s201_whisper_content_gate_v1.yaml")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the S201 Whisper content-gate contract. This command "
            "does not download or run a model, read row-level audio or "
            "transcripts, compute WER/CER, create predictions, use a GPU, "
            "or publish results."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "validate C1 policy/source pins only; predecessor, threshold, "
            "external, data, model, protected, and output paths remain untouched"
        ),
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        root = args.workspace_root.resolve(strict=True)
        config_path = args.config
        if not config_path.is_absolute():
            config_path = root / config_path
        config = load_config(config_path)
        if args.config_only:
            static = validate_static_config(
                workspace_root=root,
                config_path=config_path,
                config=config,
            )
            result = config_only_result(static)
        else:
            preflight = run_full_preflight(
                workspace_root=root,
                config_path=config_path,
                config=config,
            )
            result = {
                "status": preflight.status,
                "config_sha256": preflight.config_sha256,
                "s108_commit_sha256": preflight.s108_commit_sha256,
                "s200_readiness_lock_sha256": (preflight.s200_readiness_lock_sha256),
                "threshold_preregistration_sha256": (
                    preflight.threshold_preregistration_sha256
                ),
                "data_attestation_hashes": preflight.data_attestation_hashes,
                "model_attestation_sha256": preflight.model_attestation_sha256,
                "manifest_hashes": preflight.manifest_hashes,
                **preflight.audit,
                "production_runner_implemented": False,
                "execution_allowed": False,
                "completion_claim_allowed": False,
            }
    except S201ContractError as exc:
        print(
            json.dumps(failure_result(exc), ensure_ascii=False, sort_keys=True),
            file=sys.stderr,
        )
        return 1
    except OSError as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "stage": "filesystem",
                    "error": str(exc),
                    "production_runner_implemented": False,
                    "execution_allowed": False,
                    "completion_claim_allowed": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
