#!/usr/bin/env python3
"""Prepare or check private group access for worker artifact bind mounts."""

from __future__ import annotations

import argparse
from pathlib import Path

from voice_coach.backend_analysis.deployment_attestation import (
    DeploymentAttestationError,
    prepare_artifact_group_access,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "apply"))
    parser.add_argument("--artifact-gid", type=int, required=True)
    parser.add_argument(
        "--artifact", type=Path, action="append", required=True, dest="artifacts"
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    try:
        prepare_artifact_group_access(
            args.artifacts,
            artifact_group_id=args.artifact_gid,
            apply=args.action == "apply",
        )
    except DeploymentAttestationError as error:
        print(str(error))
        return 2
    print("analysis_worker_artifact_access_ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
