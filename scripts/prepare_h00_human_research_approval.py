#!/usr/bin/env python3
"""Validate H00 static governance or official-evidence read-only preflight."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.human_research.h00_approval_contract import (
    AccessAudit,
    H00ContractError,
    config_only_result,
    failure_result,
    load_config,
    run_full_preflight,
    validate_static_config,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path("configs/experiments/h00_human_research_approval_v1.yaml")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate H00 institution/IRB or equivalent official-determination "
            "static readiness. This command does not infer approval or exemption, "
            "browse or submit an institution portal, contact an institution, "
            "resolve or read the restricted official document locator, recruit "
            "or consent participants, randomize or deliver an intervention, "
            "record/read/write audio, access personal or sensitive data, call a "
            "model/GPU/TTS service, write artifacts, or claim H00 completion."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workspace-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--config-only",
        action="store_true",
        help=(
            "validate only 12 pinned C1/A1 static sources and fail-closed "
            "approval/attestation schemas and templates; external evidence, "
            "network/browser/portal, participants, audio, models/GPUs/TTS, and "
            "outputs remain untouched"
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
                "approval_sha256": preflight.approval_sha256,
                "attestation_sha256": preflight.attestation_sha256,
                **preflight.audit,
                "actual_external_browser_submission_or_contact": False,
                "actual_official_determination_or_approval": False,
                "actual_participant_recruitment_consent_or_randomization": False,
                "actual_audio_personal_or_sensitive_data_access": False,
                "actual_intervention_model_gpu_or_tts_execution": False,
                "artifact_created": False,
                "h00_completion_claim_allowed": False,
                "publication_allowed": False,
            }
    except H00ContractError as exc:
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
                    **AccessAudit().as_dict(),
                    "actual_external_browser_submission_or_contact": False,
                    "actual_official_determination_or_approval": False,
                    "actual_participant_recruitment_consent_or_randomization": False,
                    "actual_audio_personal_or_sensitive_data_access": False,
                    "actual_intervention_model_gpu_or_tts_execution": False,
                    "artifact_created": False,
                    "h00_completion_claim_allowed": False,
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
