#!/usr/bin/env python3
"""Create a private content-bound manifest for one worker release."""

from __future__ import annotations

import argparse
from pathlib import Path

from voice_coach.backend_analysis.deployment_attestation import (
    DeploymentArtifactPaths,
    DeploymentAttestationError,
    create_deployment_attestation,
    write_deployment_attestation,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--authorization-key-id", required=True)
    parser.add_argument("--consent-policy-revision", required=True)
    parser.add_argument("--worker-device", choices=("cpu", "cuda"), required=True)
    parser.add_argument("--artifact-gid", type=int, required=True)
    parser.add_argument("--detector-bundle", type=Path, required=True)
    parser.add_argument("--seungun-model", type=Path, required=True)
    parser.add_argument("--seungun-processor", type=Path, required=True)
    parser.add_argument("--nltk-data", type=Path, required=True)
    parser.add_argument("--visual-release", type=Path)
    parser.add_argument("--visual-landmark-model", type=Path)
    parser.add_argument("--visual-geometry-artifact", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    try:
        document = create_deployment_attestation(
            release_id=args.release_id,
            authorization_key_id=args.authorization_key_id,
            consent_policy_revision=args.consent_policy_revision,
            worker_device=args.worker_device,
            artifact_group_id=args.artifact_gid,
            artifacts=DeploymentArtifactPaths(
                detector_bundle=args.detector_bundle,
                seungun_model=args.seungun_model,
                seungun_processor=args.seungun_processor,
                nltk_data=args.nltk_data,
                visual_release=args.visual_release,
                visual_landmark_model=args.visual_landmark_model,
                visual_geometry_artifact=args.visual_geometry_artifact,
            ),
        )
        write_deployment_attestation(
            args.output, document, artifact_group_id=args.artifact_gid
        )
    except DeploymentAttestationError as error:
        print(str(error))
        return 2
    print("analysis_worker_deployment_attestation_created")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
