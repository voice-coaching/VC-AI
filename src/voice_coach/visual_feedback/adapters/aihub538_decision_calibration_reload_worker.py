"""Fresh-process strict loader for AI Hub 538 decision calibration artifacts."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import stat
import sys

from ..errors import VisualFeedbackContractError
from ..models.aihub538_decision_calibration import (
    Aihub538DecisionCalibrationEvidence,
    Aihub538DecisionCalibrationReloadReceipt,
)
from ..models.integrity import canonical_json_bytes
from ..models.runtime_policy_artifact import P8RuntimePolicyArtifact
from ..services.aihub538_decision_calibration import (
    decision_calibration_evaluator_code_sha256,
)


_MAXIMUM_JSON_BYTES = 16 * 1024 * 1024


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--video-qc-fd", required=True, type=int)
    parser.add_argument("--av-sync-fd", required=True, type=int)
    parser.add_argument("--evidence-fd", required=True, type=int)
    return parser


def _strict_document(descriptor: int) -> object:
    if descriptor < 3:
        raise VisualFeedbackContractError("reload descriptor is invalid")
    duplicate = os.dup(descriptor)
    try:
        metadata = os.fstat(duplicate)
        access_mode = fcntl.fcntl(duplicate, fcntl.F_GETFL) & os.O_ACCMODE
        if (
            access_mode != os.O_RDONLY
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o400
            or not 0 < metadata.st_size <= _MAXIMUM_JSON_BYTES
        ):
            raise VisualFeedbackContractError("reload input is not frozen")
        os.lseek(duplicate, 0, os.SEEK_SET)
        payload = bytearray()
        while len(payload) <= _MAXIMUM_JSON_BYTES:
            chunk = os.read(
                duplicate,
                min(1024 * 1024, _MAXIMUM_JSON_BYTES + 1 - len(payload)),
            )
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(duplicate)
        if (
            len(payload) != metadata.st_size
            or (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise VisualFeedbackContractError("reload input changed")
    finally:
        os.close(duplicate)

    def reject_duplicates(pairs):  # noqa: ANN001
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise VisualFeedbackContractError("reload JSON has duplicate keys")
            result[key] = value
        return result

    try:
        return json.loads(
            bytes(payload),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                VisualFeedbackContractError("reload JSON has non-finite value")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VisualFeedbackContractError("reload JSON is invalid") from error


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        video_qc = P8RuntimePolicyArtifact.from_json_object(
            _strict_document(arguments.video_qc_fd)
        )
        av_sync = P8RuntimePolicyArtifact.from_json_object(
            _strict_document(arguments.av_sync_fd)
        )
        evidence = Aihub538DecisionCalibrationEvidence.from_json_object(
            _strict_document(arguments.evidence_fd)
        )
        if (
            video_qc.policy_kind != "video_qc_decision"
            or av_sync.policy_kind != "av_sync_decision"
            or evidence.video_qc_decision_policy_artifact_sha256
            != video_qc.artifact_sha256
            or evidence.video_qc_decision_policy_sha256 != video_qc.policy_sha256
            or evidence.av_sync_decision_policy_artifact_sha256
            != av_sync.artifact_sha256
            or evidence.av_sync_decision_policy_sha256 != av_sync.policy_sha256
            or evidence.evaluator_code_sha256
            != decision_calibration_evaluator_code_sha256()
        ):
            raise VisualFeedbackContractError("reload artifact lineage differs")
        video_qc.policy_document.to_runtime_policy()
        av_sync.policy_document.to_runtime_policy()
        receipt = Aihub538DecisionCalibrationReloadReceipt.issue(
            calibration_evidence=evidence
        )
        sys.stdout.buffer.write(canonical_json_bytes(receipt.to_json_object()) + b"\n")
        return 0
    except (OSError, ValueError, VisualFeedbackContractError):
        sys.stderr.write('{"status":"failed_closed"}\n')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
