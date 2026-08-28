"""Fresh-process parser/parity worker for a frozen P6 geometry artifact."""

from __future__ import annotations

import json
import sys

from ..errors import VisualFeedbackContractError
from ..models.integrity import sha256_document
from ..models.p6_geometry import P6GeometryArtifact, p6_mapping_parity_probe_sha256


LOADER_REVISION = "p6_geometry_strict_loader_v2"


def main() -> int:
    try:
        document = json.load(sys.stdin)
        artifact = P6GeometryArtifact.from_json_object(document)
        parity_sha256 = p6_mapping_parity_probe_sha256(
            artifact.mapping_mediapipe_indices
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, VisualFeedbackContractError):
        sys.stdout.write(
            '{"reason_code":"p6_reload_validation_failed","status":"failed_closed"}\n'
        )
        return 2
    response = {
        "status": "completed",
        "loader_revision": LOADER_REVISION,
        "artifact_sha256": artifact.artifact_sha256,
        "artifact_document_sha256": sha256_document(artifact.to_json_object()),
        "mapping_sha256": artifact.mapping_sha256,
        "threshold_protocol_sha256": artifact.threshold_protocol_sha256,
        "parity_probe_sha256": parity_sha256,
    }
    sys.stdout.write(
        json.dumps(response, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
