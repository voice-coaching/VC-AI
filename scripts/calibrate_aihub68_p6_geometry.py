#!/usr/bin/env python3
"""Run real AI Hub 68 P6 geometry calibration from read-only ZIP FDs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from voice_coach.visual_feedback.adapters.aihub68_geometry_dataset import (  # noqa: E402
    Aihub68AdmissionError,
    Aihub68GeometryDatasetAdapter,
)
from voice_coach.visual_feedback.adapters.p6_geometry_reload import (  # noqa: E402
    P6GeometryReloadAdapter,
    P6GeometryReloadError,
)
from voice_coach.visual_feedback.adapters.p6_mediapipe_geometry import (  # noqa: E402
    P6MediaPipeGeometryAdapter,
    P6MediaPipeRuntimeError,
)
from voice_coach.visual_feedback.models.integrity import canonical_json_bytes  # noqa: E402
from voice_coach.visual_feedback.models.p6_geometry import (  # noqa: E402
    P6GeometryEvidenceBundle,
)
from voice_coach.visual_feedback.services.p6_geometry_calibration import (  # noqa: E402
    P6GeometryCalibrationError,
    P6GeometryCalibrationService,
)


def _descriptor(value: str) -> int:
    try:
        descriptor = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("descriptor must be an integer >= 3") from None
    if descriptor < 3:
        raise argparse.ArgumentTypeError("descriptor must be an integer >= 3")
    return descriptor


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels-fd", type=_descriptor, required=True)
    parser.add_argument("--images-fd", type=_descriptor, required=True)
    parser.add_argument("--landmark-model", type=Path, required=True)
    parser.add_argument("--landmark-model-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--artifact-output",
        type=Path,
        help="optional aggregate-only artifact document",
    )
    parser.add_argument(
        "--reload-receipt-output",
        type=Path,
        help="optional aggregate-only fresh-process receipt document",
    )
    parser.add_argument(
        "--rights-confirmed",
        action="store_true",
        help="confirm the project user obtained AI Hub 68 access for this use",
    )
    parser.add_argument("--replace", action="store_true")
    return parser


def _write_atomic(path: Path, payload: bytes, *, replace: bool) -> None:
    if not path.is_absolute() or not path.parent.is_dir() or path.is_symlink():
        raise ValueError("output boundary invalid")
    if path.exists() and not replace:
        raise ValueError("output already exists")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".p6-geometry-",
            suffix=".partial",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # The aggregate evidence is a frozen input to P8.  Keep the containing
        # directory operator-controlled, but make the completed generation
        # read-only so ordinary follow-on code cannot mutate it in place.
        os.chmod(temporary_path, 0o400)
        if not replace and path.exists():
            raise ValueError("output already exists")
        os.replace(temporary_path, path)
        temporary_path = None
        directory_descriptor = os.open(
            path.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _model_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if _model_digest(arguments.landmark_model) != arguments.landmark_model_sha256:
            raise P6MediaPipeRuntimeError("p6_landmark_model_integrity_failed")
        dataset = Aihub68GeometryDatasetAdapter().admit(
            label_archive_descriptor=arguments.labels_fd,
            image_archive_descriptor=arguments.images_fd,
        )
        inference = P6MediaPipeGeometryAdapter(
            model_path=arguments.landmark_model,
            expected_model_sha256=arguments.landmark_model_sha256,
            repository_root=REPOSITORY_ROOT,
        )
        artifact = P6GeometryCalibrationService(inference).calibrate(
            dataset,
            rights_confirmed=arguments.rights_confirmed,
        )
        reload_receipt = P6GeometryReloadAdapter(
            repository_root=REPOSITORY_ROOT
        ).reload(artifact)
        bundle = P6GeometryEvidenceBundle.issue(
            artifact=artifact,
            reload_receipt=reload_receipt,
        )
        requested_outputs = tuple(
            path
            for path in (
                arguments.output,
                arguments.artifact_output,
                arguments.reload_receipt_output,
            )
            if path is not None
        )
        if len(set(requested_outputs)) != len(requested_outputs):
            raise ValueError("output targets must be distinct")
        if arguments.artifact_output is not None:
            _write_atomic(
                arguments.artifact_output,
                canonical_json_bytes(artifact.to_json_object()) + b"\n",
                replace=arguments.replace,
            )
        if arguments.reload_receipt_output is not None:
            _write_atomic(
                arguments.reload_receipt_output,
                canonical_json_bytes(reload_receipt.to_json_object()) + b"\n",
                replace=arguments.replace,
            )
        _write_atomic(
            arguments.output,
            canonical_json_bytes(bundle.to_json_object()) + b"\n",
            replace=arguments.replace,
        )
    except P6GeometryCalibrationError as error:
        response: dict[str, object] = {
            "status": "failed_closed",
            "reason_codes": list(error.reason_codes),
        }
        if error.metric_summary is not None:
            response["aggregate_metrics"] = error.metric_summary.to_json_object()
        print(json.dumps(response, sort_keys=True, separators=(",", ":")))
        return 2
    except (
        Aihub68AdmissionError,
        P6MediaPipeRuntimeError,
        P6GeometryReloadError,
    ) as error:
        reason_code = getattr(error, "reason_code", str(error))
        print(
            json.dumps(
                {"status": "failed_closed", "reason_codes": [reason_code]},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2
    except (OSError, ValueError):
        print(
            '{"reason_codes":["p6_execution_boundary_failed"],'
            '"status":"failed_closed"}'
        )
        return 2
    print(
        json.dumps(
            {
                "status": "completed",
                "gate_status": artifact.gate_status,
                "artifact_sha256": artifact.artifact_sha256,
                "bundle_sha256": bundle.bundle_sha256,
                "reload_receipt_sha256": reload_receipt.receipt_sha256,
                "development_actor_count": artifact.development_actor_count,
                "calibration_actor_count": artifact.calibration_actor_count,
                "calibration_frame_count": (
                    artifact.metrics.calibration_sample_count
                ),
                "calibration_coverage_fraction": (
                    artifact.metrics.calibration_coverage_fraction
                ),
                "calibration_normalized_landmark_error": (
                    artifact.metrics.calibration_normalized_landmark_error
                ),
                "repeatability_normalized_cue_rmse": (
                    artifact.metrics.repeatability_normalized_cue_rmse
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
