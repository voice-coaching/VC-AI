#!/usr/bin/env python3
"""Validate D08 static policy or its read-only R00-first preflight."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.references.d08_native_reference_contract import (
    AccessAudit,
    D08ContractError,
    config_only_result,
    failure_result,
    load_config,
    run_full_preflight,
    validate_static_config,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path("configs/experiments/d08_native_multispeaker_reference_v1.yaml")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate D08 native multi-speaker reference static readiness. "
            "This command does not recruit speakers, collect consent, access "
            "a microphone, read or write audio, run ASR or another model, use "
            "the network/GPU, compute QC, write outputs, commit results, or "
            "publish anything."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "validate C1/public source pins, schemas, and templates only; "
            "R00 revalidation, H00/consent/rights/preregistration, protected "
            "data, microphone/audio/model/network/GPU, and outputs remain "
            "untouched"
        ),
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        workspace_root = args.workspace_root.resolve(strict=True)
        config_path = args.config
        if not config_path.is_absolute():
            config_path = workspace_root / config_path
        config = load_config(config_path)
        if args.config_only:
            audit = AccessAudit()
            static = validate_static_config(
                workspace_root=workspace_root,
                config_path=config_path,
                config=config,
                audit=audit,
            )
            result = config_only_result(static, audit)
        else:
            preflight = run_full_preflight(
                workspace_root=workspace_root,
                config_path=config_path,
                config=config,
            )
            result = {
                "status": preflight.status,
                "config_sha256": preflight.config_sha256,
                "r00_report_sha256": preflight.r00_report_sha256,
                **preflight.audit,
                "actual_recruitment_completed": False,
                "actual_consent_completed": False,
                "actual_recording_completed": False,
                "actual_qc_completed": False,
                "d08_completion_claim_allowed": False,
                "publication_allowed": False,
            }
    except D08ContractError as exc:
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
                    "actual_recruitment_completed": False,
                    "actual_consent_completed": False,
                    "actual_recording_completed": False,
                    "actual_qc_completed": False,
                    "d08_completion_claim_allowed": False,
                    "publication_allowed": False,
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
