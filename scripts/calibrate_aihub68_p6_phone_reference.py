#!/usr/bin/env python3
"""Produce strict aggregate-only P6B reference/catalog evidence from AI Hub 68."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from voice_coach.visual_feedback.adapters.aihub68_geometry_dataset import (  # noqa: E402
    Aihub68AdmissionError,
    Aihub68GeometryDatasetAdapter,
)
from voice_coach.visual_feedback.adapters.p6_mediapipe_geometry import (  # noqa: E402
    P6MediaPipeGeometryAdapter,
    P6MediaPipeRuntimeError,
)
from voice_coach.visual_feedback.adapters.p6_phone_reference_reload import (  # noqa: E402
    P6PhoneReferenceReloadAdapter,
    P6PhoneReferenceReloadError,
)
from voice_coach.visual_feedback.adapters.p6_runtime_cue_parity import (  # noqa: E402
    P6RuntimeCueParityAdapter,
    P6RuntimeCueParityError,
)
from voice_coach.visual_feedback.errors import VisualFeedbackContractError  # noqa: E402
from voice_coach.visual_feedback.models.integrity import canonical_json_bytes  # noqa: E402
from voice_coach.visual_feedback.models.p6_geometry import (  # noqa: E402
    P6GeometryEvidenceBundle,
)
from voice_coach.visual_feedback.models.p6_phone_reference import (  # noqa: E402
    P6PhoneReferenceEvidenceBundle,
)
from voice_coach.visual_feedback.services.p6_phone_reference import (  # noqa: E402
    P6PhoneReferenceCalibrationError,
    P6PhoneReferenceCalibrationService,
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
    parser.add_argument("--geometry-bundle", type=Path, required=True)
    parser.add_argument("--landmark-model", type=Path, required=True)
    parser.add_argument("--landmark-model-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--rights-confirmed",
        action="store_true",
        help="confirm the user obtained AI Hub 68 portal permission",
    )
    return parser


def _read_geometry_bundle(path: Path) -> P6GeometryEvidenceBundle:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("geometry bundle boundary invalid")
    with path.open("rb") as stream:
        payload = stream.read(1024 * 1024 + 1)
    if not payload or len(payload) > 1024 * 1024:
        raise ValueError("geometry bundle size invalid")
    return P6GeometryEvidenceBundle.from_json_object(json.loads(payload))


def _write_exclusive(path: Path, value: object) -> None:
    if not path.is_absolute() or path.parent.is_symlink() or not path.parent.is_dir():
        raise ValueError("output boundary invalid")
    payload = canonical_json_bytes(value) + b"\n"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags, 0o400)
    try:
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise OSError("short output write")
            written += count
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o400)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    outputs = {
        "producer": arguments.output_dir / "aihub68_phone_reference_producer_v2.json",
        "calibration": arguments.output_dir / "aihub68_visual_calibration_v2.json",
        "catalog": arguments.output_dir / "aihub68_visual_claim_catalog_v2.json",
        "policy": arguments.output_dir / "aihub68_visual_claim_policy_v2.json",
        "parity": arguments.output_dir / "aihub68_runtime_cue_parity_v2.json",
        "reload": arguments.output_dir / "aihub68_phone_reference_reload_v2.json",
        "bundle": arguments.output_dir / "aihub68_phone_reference_bundle_v2.json",
    }
    try:
        if (
            not arguments.output_dir.is_absolute()
            or arguments.output_dir.is_symlink()
            or not arguments.output_dir.is_dir()
            or any(path.exists() for path in outputs.values())
        ):
            raise ValueError("output boundary invalid")
        geometry_bundle = _read_geometry_bundle(arguments.geometry_bundle)
        dataset = Aihub68GeometryDatasetAdapter().admit(
            label_archive_descriptor=arguments.labels_fd,
            image_archive_descriptor=arguments.images_fd,
        )
        calibration_inference = P6MediaPipeGeometryAdapter(
            model_path=arguments.landmark_model,
            expected_model_sha256=arguments.landmark_model_sha256,
            repository_root=REPOSITORY_ROOT,
        )
        production_parity = P6RuntimeCueParityAdapter(
            model_path=arguments.landmark_model,
            expected_model_sha256=arguments.landmark_model_sha256,
            repository_root=REPOSITORY_ROOT,
        )
        producer = P6PhoneReferenceCalibrationService(
            calibration_inference=calibration_inference,
            production_parity=production_parity,
        ).calibrate(
            dataset=dataset,
            geometry_bundle=geometry_bundle,
            rights_confirmed=arguments.rights_confirmed,
        )
        reload_receipt = P6PhoneReferenceReloadAdapter(
            repository_root=REPOSITORY_ROOT
        ).reload(producer)
        bundle = P6PhoneReferenceEvidenceBundle.issue(
            producer=producer, reload_receipt=reload_receipt
        )
        documents = {
            "producer": producer.to_json_object(),
            "calibration": producer.calibration_artifact.to_json_object(),
            "catalog": producer.claim_catalog.to_json_object(),
            "policy": producer.claim_policy.to_json_object(),
            "parity": producer.runtime_parity_receipt.to_json_object(),
            "reload": reload_receipt.to_json_object(),
            "bundle": bundle.to_json_object(),
        }
        for name in (
            "producer",
            "calibration",
            "catalog",
            "policy",
            "parity",
            "reload",
            "bundle",
        ):
            _write_exclusive(outputs[name], documents[name])
        _fsync_directory(arguments.output_dir)
    except P6PhoneReferenceCalibrationError as error:
        response: dict[str, object] = {
            "status": "failed_closed",
            "reason_codes": list(error.reason_codes),
        }
        if error.metrics is not None:
            response["aggregate_metrics"] = error.metrics.to_json_object()
        if error.parity_receipt is not None:
            response["runtime_parity"] = error.parity_receipt.to_json_object()
        print(json.dumps(response, sort_keys=True, separators=(",", ":")))
        return 2
    except (
        Aihub68AdmissionError,
        P6MediaPipeRuntimeError,
        P6RuntimeCueParityError,
        P6PhoneReferenceReloadError,
    ) as error:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "reason_codes": [getattr(error, "reason_code", str(error))],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2
    except (
        OSError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        VisualFeedbackContractError,
    ):
        print('{"reason_codes":["p6b_execution_boundary_failed"],"status":"failed_closed"}')
        return 2
    print(
        json.dumps(
            {
                "status": "completed",
                "gate_status": "passed",
                "producer_sha256": producer.producer_sha256,
                "bundle_sha256": bundle.bundle_sha256,
                "reload_receipt_sha256": reload_receipt.receipt_sha256,
                "calibration_artifact_sha256": producer.calibration_artifact.artifact_sha256,
                "catalog_sha256": producer.claim_catalog.catalog_sha256,
                "policy_sha256": producer.claim_policy.policy_sha256,
                "development_sample_count": producer.metrics.development_sample_count,
                "calibration_sample_count": producer.metrics.calibration_sample_count,
                "single_face_coverage": producer.metrics.single_face_coverage,
                "calibration_support_coverage": producer.metrics.calibration_support_coverage,
                "runtime_parity_coverage": producer.runtime_parity_receipt.parity_coverage,
                "runtime_parity_maximum_absolute_difference": (
                    producer.runtime_parity_receipt.maximum_absolute_cue_difference
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
