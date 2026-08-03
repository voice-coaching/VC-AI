#!/usr/bin/env python3
"""Validate R01 static policy or its read-only R00-first preflight."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.references.r01_candidate_contract import (
    AccessAudit,
    R01ContractError,
    config_only_result,
    failure_result,
    load_config,
    run_full_preflight,
    validate_static_config,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path("configs/experiments/r01_human_cosyvoice_candidates_v1.yaml")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate R01 human/CosyVoice candidate static readiness. This "
            "command does not record or read audio, download or run a model, "
            "query a GPU, create candidate IDs/manifests, compute metrics, "
            "open R02 results, or publish anything."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "validate C1/public source pins and templates only; R00 runtime "
            "revalidation, external attestations, data/audio/model/environment/"
            "GPU/output paths all remain untouched"
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
            audit = AccessAudit()
            static = validate_static_config(
                workspace_root=root,
                config_path=config_path,
                config=config,
                audit=audit,
            )
            result = config_only_result(static, audit)
        else:
            preflight = run_full_preflight(
                workspace_root=root,
                config_path=config_path,
                config=config,
            )
            result = {
                "status": preflight.status,
                "config_sha256": preflight.config_sha256,
                "r00_validation_report_sha256": (
                    preflight.r00_validation_report_sha256
                ),
                **preflight.audit,
                "human_recording_implemented": False,
                "tts_inference_implemented": False,
                "candidate_manifest_created": False,
                "audio_created": False,
                "r01_completion_claim_allowed": False,
                "publication_allowed": False,
            }
    except R01ContractError as exc:
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
                    "human_recording_implemented": False,
                    "tts_inference_implemented": False,
                    "candidate_manifest_created": False,
                    "audio_created": False,
                    "r01_completion_claim_allowed": False,
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
