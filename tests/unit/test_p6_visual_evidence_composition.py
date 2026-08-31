"""Focused contract tests for the non-promotional P6 evidence composition."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat

import pytest

import voice_coach.visual_feedback.adapters.p6_visual_evidence_composition_store as composition_store
from voice_coach.visual_feedback.adapters.p6_visual_evidence_composition_store import (
    P6VisualEvidenceCompositionStoreError,
    load_p6_visual_evidence_composition_receipt_descriptor,
    write_p6_visual_evidence_composition_receipt,
)
from voice_coach.visual_feedback.models.integrity import (
    canonical_json_bytes,
    sha256_document,
)
from voice_coach.visual_feedback.models.p6_visual_evidence_composition import (
    P6AvSyncTemporalEvidenceLane,
    P6GeometryReferenceEvidenceLane,
    P6PhotometricQualificationEvidenceLane,
    P6VisualEvidenceCompositionReceipt,
)
from voice_coach.visual_feedback.errors import VisualFeedbackContractError
from voice_coach.visual_feedback.services.p6_visual_evidence_composition import (
    P6VisualEvidenceCompositionService,
)


def test_receipt_has_exactly_three_typed_lanes_and_no_authority() -> None:
    receipt = _receipt()
    document = receipt.to_json_object()

    assert set(document) >= {
        "geometry_reference_lane",
        "photometric_qualification_lane",
        "av_sync_temporal_evidence_lane",
    }
    assert all(
        document[name] is False
        for name in (
            "runtime_authorized",
            "runtime_publisher_authorized",
            "promotion_authorized",
            "p8_authorized",
            "p8_promotion_authorized",
            "locked_authorized",
            "locked_evaluation_authorized",
            "claim_authorized",
            "visual_claim_authorized",
            "user_visual_runtime_authorized",
        )
    )
    assert P6VisualEvidenceCompositionReceipt.from_json_object(document) == receipt


def test_receipt_rejects_any_authorization_or_cross_lane_substitution() -> None:
    document = _receipt().to_json_object()
    document["promotion_authorized"] = True
    with pytest.raises(VisualFeedbackContractError):
        P6VisualEvidenceCompositionReceipt.from_json_object(document)


@pytest.mark.parametrize(
    "flag_name",
    (
        "runtime_authorized",
        "runtime_publisher_authorized",
        "promotion_authorized",
        "p8_authorized",
        "p8_promotion_authorized",
        "locked_authorized",
        "locked_evaluation_authorized",
        "claim_authorized",
        "visual_claim_authorized",
        "user_visual_runtime_authorized",
    ),
)
def test_each_authorization_flag_is_rejected_with_recomputed_digest(
    flag_name: str,
) -> None:
    document = _receipt().to_json_object()
    document[flag_name] = True
    document["receipt_sha256"] = sha256_document(
        {key: value for key, value in document.items() if key != "receipt_sha256"}
    )

    with pytest.raises(VisualFeedbackContractError):
        P6VisualEvidenceCompositionReceipt.from_json_object(document)


def test_aihub539_label_audit_cannot_substitute_for_actual_media_sync() -> None:
    document = _receipt().to_json_object()
    temporal = document["av_sync_temporal_evidence_lane"]
    assert isinstance(temporal, dict)
    temporal["source_artifact_schema_version"] = (
        "voice_coach.aihub539_sync_label_audit_receipt.v1"
    )

    with pytest.raises(VisualFeedbackContractError):
        P6VisualEvidenceCompositionReceipt.from_json_object(document)

    document = _receipt().to_json_object()
    document["av_sync_temporal_evidence_lane"] = document[
        "geometry_reference_lane"
    ]
    with pytest.raises(VisualFeedbackContractError):
        P6VisualEvidenceCompositionReceipt.from_json_object(document)


def test_service_calls_all_three_source_completion_cross_validation_hooks() -> None:
    validator = _Validator()
    receipt = P6VisualEvidenceCompositionService(validator).compose(**_lanes())

    assert validator.calls == ["geometry", "photometric", "temporal"]
    assert receipt.p8_authorized is False


def test_no_public_issue_bypass_and_validator_failure_prevents_composition() -> None:
    assert not hasattr(P6VisualEvidenceCompositionReceipt, "issue")
    validator = _RejectingValidator()

    with pytest.raises(RuntimeError, match="source_validation_failed"):
        P6VisualEvidenceCompositionService(validator).compose(**_lanes())

    assert validator.calls == ["geometry", "photometric"]


def test_owner_only_store_round_trips_exact_canonical_receipt(tmp_path: Path) -> None:
    output_directory = tmp_path / "owner-only"
    output_directory.mkdir(mode=0o700)
    output_directory.chmod(0o700)
    output = output_directory / "composition.json"

    written = write_p6_visual_evidence_composition_receipt(
        receipt=_receipt(), output_path=output
    )

    assert written == output
    assert stat.S_IMODE(output.stat().st_mode) == 0o400
    descriptor = os.open(output, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        assert load_p6_visual_evidence_composition_receipt_descriptor(
            descriptor
        ) == _receipt()
    finally:
        os.close(descriptor)


def test_loader_rejects_noncanonical_or_non_0400_receipts(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    path.write_bytes(canonical_json_bytes(_receipt().to_json_object()) + b"\n")
    path.chmod(0o400)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        assert load_p6_visual_evidence_composition_receipt_descriptor(
            descriptor
        ).receipt_sha256 == _receipt().receipt_sha256
    finally:
        os.close(descriptor)
    path.chmod(0o600)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with pytest.raises(P6VisualEvidenceCompositionStoreError):
            load_p6_visual_evidence_composition_receipt_descriptor(descriptor)
    finally:
        os.close(descriptor)

    path.chmod(0o600)
    path.write_text(json.dumps(_receipt().to_json_object(), indent=2) + "\n")
    path.chmod(0o400)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with pytest.raises(P6VisualEvidenceCompositionStoreError):
            load_p6_visual_evidence_composition_receipt_descriptor(descriptor)
    finally:
        os.close(descriptor)


def test_writer_does_not_unlink_preexisting_dangling_symlink(tmp_path: Path) -> None:
    output_directory = tmp_path / "owner-only"
    output_directory.mkdir(mode=0o700)
    output_directory.chmod(0o700)
    output = output_directory / "composition.json"
    output.symlink_to("absent-target.json")

    with pytest.raises(P6VisualEvidenceCompositionStoreError):
        write_p6_visual_evidence_composition_receipt(
            receipt=_receipt(), output_path=output
        )

    assert output.is_symlink()
    assert os.readlink(output) == "absent-target.json"


def test_writer_failure_does_not_unlink_substituted_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_directory = tmp_path / "owner-only"
    output_directory.mkdir(mode=0o700)
    output_directory.chmod(0o700)
    output = output_directory / "composition.json"
    replacement = b"replacement-must-survive\n"
    real_write_all = composition_store._write_all

    def substitute_then_fail(descriptor: int, payload: bytes) -> None:
        real_write_all(descriptor, payload)
        output.unlink()
        replacement_descriptor = os.open(
            output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400
        )
        try:
            real_write_all(replacement_descriptor, replacement)
        finally:
            os.close(replacement_descriptor)
        raise OSError("injected_write_failure")

    monkeypatch.setattr(composition_store, "_write_all", substitute_then_fail)

    with pytest.raises(P6VisualEvidenceCompositionStoreError):
        write_p6_visual_evidence_composition_receipt(
            receipt=_receipt(), output_path=output
        )

    assert output.read_bytes() == replacement


def test_loader_rejects_duplicate_keys_hardlinks_and_read_write_fd(
    tmp_path: Path,
) -> None:
    document = _receipt().to_json_object()
    canonical = canonical_json_bytes(document)

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_bytes(
        b'{"schema_version":"voice_coach.p6_visual_evidence_composition_receipt.v1",'
        + canonical[1:]
        + b"\n"
    )
    duplicate.chmod(0o400)
    descriptor = os.open(duplicate, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with pytest.raises(P6VisualEvidenceCompositionStoreError):
            load_p6_visual_evidence_composition_receipt_descriptor(descriptor)
    finally:
        os.close(descriptor)

    hardlinked = tmp_path / "hardlinked.json"
    hardlinked.write_bytes(canonical + b"\n")
    hardlinked.chmod(0o400)
    os.link(hardlinked, tmp_path / "second-link.json")
    descriptor = os.open(hardlinked, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with pytest.raises(P6VisualEvidenceCompositionStoreError):
            load_p6_visual_evidence_composition_receipt_descriptor(descriptor)
    finally:
        os.close(descriptor)

    read_write = tmp_path / "read-write.json"
    read_write.write_bytes(canonical + b"\n")
    descriptor = os.open(read_write, os.O_RDWR | os.O_NOFOLLOW)
    try:
        read_write.chmod(0o400)
        with pytest.raises(P6VisualEvidenceCompositionStoreError):
            load_p6_visual_evidence_composition_receipt_descriptor(descriptor)
    finally:
        os.close(descriptor)


def _lanes() -> dict[str, object]:
    return {
        "geometry_reference_lane": P6GeometryReferenceEvidenceLane.bind(
            source_artifact_sha256="1" * 64,
            completion_receipt_sha256="2" * 64,
        ),
        "photometric_qualification_lane": (
            P6PhotometricQualificationEvidenceLane.bind(
                source_artifact_sha256="3" * 64,
                completion_receipt_sha256="4" * 64,
            )
        ),
        "av_sync_temporal_evidence_lane": P6AvSyncTemporalEvidenceLane.bind(
            source_artifact_sha256="5" * 64,
            completion_receipt_sha256="6" * 64,
        ),
    }


def _receipt() -> P6VisualEvidenceCompositionReceipt:
    return P6VisualEvidenceCompositionService(_Validator()).compose(**_lanes())


class _Validator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def validate_geometry_reference(
        self, lane: P6GeometryReferenceEvidenceLane
    ) -> None:
        assert lane.source_artifact_sha256 == "1" * 64
        self.calls.append("geometry")

    def validate_photometric_qualification(
        self, lane: P6PhotometricQualificationEvidenceLane
    ) -> None:
        assert lane.completion_receipt_sha256 == "4" * 64
        self.calls.append("photometric")

    def validate_av_sync_temporal(self, lane: P6AvSyncTemporalEvidenceLane) -> None:
        assert lane.source_completion_binding_sha256
        self.calls.append("temporal")


class _RejectingValidator(_Validator):
    def validate_photometric_qualification(
        self, lane: P6PhotometricQualificationEvidenceLane
    ) -> None:
        assert lane.source_artifact_sha256 == "3" * 64
        self.calls.append("photometric")
        raise RuntimeError("source_validation_failed")
