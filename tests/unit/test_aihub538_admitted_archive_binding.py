"""Whole-ZIP admission and candidate-role binding fixture tests."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from zipfile import ZIP_STORED, ZipFile

import pytest

from voice_coach.visual_feedback.adapters import (
    aihub538_admitted_archive_binding as binding_adapter,
    aihub538_actual_media_calibration as actual_media_adapter,
    aihub538_training_dataset as training_dataset_adapter,
)
from voice_coach.visual_feedback.adapters.aihub538_admitted_archive_binding import (
    assert_aihub538_admitted_archive_binding,
    bind_aihub538_admitted_archive_descriptors,
    bind_aihub538_prevalidated_archive_descriptors,
    load_aihub538_training_admission_receipt_descriptor,
    resolve_aihub538_candidate_role_descriptors,
    resolve_aihub538_immutable_candidate_role_descriptors,
    revoke_aihub538_admitted_archive_binding,
    validate_aihub538_stable_zip_descriptor,
    validate_aihub538_immutable_zip_descriptor,
)
from voice_coach.visual_feedback.adapters.aihub538_actual_media_calibration import (
    Aihub538ActualMediaCalibrationAdapter,
)
from voice_coach.visual_feedback.adapters.aihub538_training_archive import (
    ZipValidationReceipt,
    validate_zip_descriptor,
)
from voice_coach.visual_feedback.adapters.aihub538_training_dataset import (
    Aihub538TrainingAdmissionError,
    Aihub538TrainingDatasetAdapter,
)
from voice_coach.visual_feedback.models.aihub538_training_evidence import (
    AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY_SHA256,
    AIHUB538_TRAINING_ANGLES,
    Aihub538TrainingAdmissionReceipt,
    Aihub538TrainingAngleAggregate,
)
from voice_coach.visual_feedback.models.integrity import (
    canonical_json_bytes,
    sha256_document,
)
from voice_coach.visual_feedback.services.aihub538_decision_calibration import (
    Aihub538DecisionCalibrationError,
)


ROOT = Path(__file__).resolve().parents[2]


def _sha(label: str) -> str:
    return sha256_document({"fixture": label})


def _zip(path: Path, *, member: str, payload: bytes) -> Path:
    with ZipFile(path, mode="x", compression=ZIP_STORED) as archive:
        archive.writestr(member, payload)
    path.chmod(0o600)
    return path


def _readonly(stack: ExitStack, path: Path) -> int:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    stack.callback(os.close, descriptor)
    return descriptor


def _validation(path: Path) -> ZipValidationReceipt:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        return validate_zip_descriptor(descriptor)
    finally:
        os.close(descriptor)


def _admission_receipt(
    *,
    label: ZipValidationReceipt,
    source: ZipValidationReceipt,
    label_root_override: str | None = None,
    label_sha_override: str | None = None,
) -> Aihub538TrainingAdmissionReceipt:
    angle_aggregates = tuple(
        Aihub538TrainingAngleAggregate(
            angle=angle,
            paired_sample_count=1,
            label_count=1,
            video_count=1,
            audio_reference_count=1,
            lip_bbox_frame_count=1,
            sentence_count=1,
        )
        for angle in AIHUB538_TRAINING_ANGLES
    )
    draft = Aihub538TrainingAdmissionReceipt(
        label_archive_sha256=label_sha_override or label.archive_sha256,
        source_archive_sha256=source.archive_sha256,
        label_zip_content_root_sha256=(
            label_root_override or label.content_root_sha256
        ),
        source_zip_content_root_sha256=source.content_root_sha256,
        paired_sample_count=9,
        label_count=9,
        video_count=9,
        unique_audio_count=9,
        audio_reference_count=9,
        speaker_count=9,
        lip_bbox_frame_count=9,
        sentence_count=9,
        angle_aggregates=angle_aggregates,
        sample_selection_sha256=_sha("selection"),
        admission_manifest_sha256=_sha("manifest"),
        identity_namespace_commitment_sha256=_sha("identity-namespace"),
        receipt_sha256="0" * 64,
    )
    receipt = replace(
        draft,
        receipt_sha256=sha256_document(draft._unsigned_json()),
    )
    receipt._validate()
    return receipt


def _fixture_archives(
    tmp_path: Path,
) -> tuple[Path, Path, Aihub538TrainingAdmissionReceipt]:
    label_path = _zip(
        tmp_path / "candidate-label.zip",
        member="label.json",
        payload=b'{"kind":"label"}',
    )
    source_path = _zip(
        tmp_path / "candidate-source.zip",
        member="source.bin",
        payload=b"actual-media-bytes",
    )
    receipt = _admission_receipt(
        label=_validation(label_path),
        source=_validation(source_path),
    )
    return label_path, source_path, receipt


def _admission_receipt_file(
    path: Path,
    receipt: Aihub538TrainingAdmissionReceipt,
) -> Path:
    path.write_bytes(canonical_json_bytes(receipt.to_json_object()) + b"\n")
    path.chmod(0o400)
    return path


def test_explicit_legacy_v1_policy_receipt_reloads_with_original_self_digest(
    tmp_path: Path,
) -> None:
    _label_path, _source_path, current = _fixture_archives(tmp_path)
    legacy_draft = replace(
        current,
        admission_policy_sha256=(
            AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY_SHA256
        ),
        receipt_sha256="0" * 64,
    )
    legacy = replace(
        legacy_draft,
        receipt_sha256=sha256_document(legacy_draft._unsigned_json()),
    )
    legacy._validate()
    path = _admission_receipt_file(tmp_path / "legacy-50.json", legacy)

    with ExitStack() as stack:
        loaded = load_aihub538_training_admission_receipt_descriptor(
            _readonly(stack, path)
        )

    assert loaded == legacy
    assert loaded.to_json_object() == legacy.to_json_object()
    assert "shard_id" not in loaded.to_json_object()


def _load_runner_module():
    path = ROOT / "scripts/calibrate_aihub538_runtime_decisions.py"
    specification = importlib.util.spec_from_file_location(
        "calibrate_aihub538_runtime_decisions_test",
        path,
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_candidate_zero_one_are_mapped_by_admission_hash_not_filename(
    tmp_path: Path,
) -> None:
    label_path, source_path, receipt = _fixture_archives(tmp_path)
    receipt_path = _admission_receipt_file(
        tmp_path / "50-dataset-admission.json",
        receipt,
    )

    # Deliberately make candidate-0 the source and candidate-1 the label.
    with ExitStack() as stack:
        candidate_0 = _readonly(stack, source_path)
        candidate_1 = _readonly(stack, label_path)
        receipt_descriptor = _readonly(stack, receipt_path)
        reloaded = load_aihub538_training_admission_receipt_descriptor(
            receipt_descriptor
        )
        binding = resolve_aihub538_candidate_role_descriptors(
            candidate_0_descriptor=candidate_0,
            candidate_1_descriptor=candidate_1,
            admission_receipt=reloaded,
        )

    assert binding.label_candidate_index == 1
    assert binding.source_candidate_index == 0
    assert binding.label_archive_sha256 == receipt.label_archive_sha256
    assert binding.source_archive_sha256 == receipt.source_archive_sha256
    serialized = json.dumps(binding.to_json_object(), sort_keys=True)
    assert str(tmp_path) not in serialized
    assert "label.json" not in serialized
    assert "candidate_names_trusted\": false" in serialized


def test_runner_candidate_mode_resolves_descriptors_from_pipeline_receipt(
    tmp_path: Path,
) -> None:
    label_path, source_path, receipt = _fixture_archives(tmp_path)
    receipt_path = _admission_receipt_file(
        tmp_path / "50-dataset-admission.json",
        receipt,
    )
    runner = _load_runner_module()
    with ExitStack() as stack:
        candidate_0 = _readonly(stack, source_path)
        candidate_1 = _readonly(stack, label_path)
        receipt_descriptor = _readonly(stack, receipt_path)
        arguments = SimpleNamespace(
            label_zip_fd=None,
            source_zip_fd=None,
            candidate_0_zip_fd=candidate_0,
            candidate_1_zip_fd=candidate_1,
            admission_receipt_fd=receipt_descriptor,
        )
        label_fd, source_fd, reloaded, binding = runner._resolve_archive_input(
            arguments
        )
        assert label_fd == candidate_1
        assert source_fd == candidate_0
        assert reloaded == receipt
        assert binding.label_candidate_index == 1
        assert binding.source_candidate_index == 0


def test_explicit_actual_media_binding_requires_archive_sha_and_content_root(
    tmp_path: Path,
) -> None:
    label_path, source_path, receipt = _fixture_archives(tmp_path)
    wrong_root_receipt = _admission_receipt(
        label=_validation(label_path),
        source=_validation(source_path),
        label_root_override=_sha("wrong-label-content-root"),
    )
    with ExitStack() as stack:
        label_fd = _readonly(stack, label_path)
        source_fd = _readonly(stack, source_path)
        bound = bind_aihub538_admitted_archive_descriptors(
            label_zip_descriptor=label_fd,
            source_zip_descriptor=source_fd,
            admission_receipt=receipt,
        )
        assert bound.label.validation_receipt.archive_sha256 == (
            receipt.label_archive_sha256
        )
        with pytest.raises(
            Aihub538DecisionCalibrationError,
            match="aihub538_admitted_archive_binding_mismatch",
        ):
            bind_aihub538_admitted_archive_descriptors(
                label_zip_descriptor=label_fd,
                source_zip_descriptor=source_fd,
                admission_receipt=wrong_root_receipt,
            )


def test_actual_media_adapter_rebinds_archives_before_inventory_or_decode(
    tmp_path: Path,
) -> None:
    label_path, source_path, _receipt = _fixture_archives(tmp_path)
    wrong_root_receipt = _admission_receipt(
        label=_validation(label_path),
        source=_validation(source_path),
        label_root_override=_sha("wrong-label-content-root"),
    )
    with ExitStack() as stack, pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_admitted_archive_binding_mismatch",
    ):
        Aihub538ActualMediaCalibrationAdapter(
            label_zip_descriptor=_readonly(stack, label_path),
            source_zip_descriptor=_readonly(stack, source_path),
            identity_hash_key=b"k" * 32,
            admission_receipt=wrong_root_receipt,
            restricted_workspace_root=tmp_path / "unused-workspace",
            landmark_model_path=tmp_path / "unused-model",
            landmark_model_sha256=_sha("unused-model"),
            video_qc_policy=None,  # type: ignore[arg-type]
            av_sync_policy=None,  # type: ignore[arg-type]
            container_policy=SimpleNamespace(maximum_rgb_sample_frames=8),
            landmark_policy=None,  # type: ignore[arg-type]
            ffmpeg_binary=sys.executable,
            ffprobe_binary=sys.executable,
        )
    assert not (tmp_path / "unused-workspace").exists()


def test_candidate_mapping_rejects_archive_sha_mismatch(tmp_path: Path) -> None:
    label_path, source_path, _receipt = _fixture_archives(tmp_path)
    mismatch = _admission_receipt(
        label=_validation(label_path),
        source=_validation(source_path),
        label_sha_override=_sha("not-candidate-0-or-1"),
    )
    with ExitStack() as stack, pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_candidate_role_binding_failed",
    ):
        resolve_aihub538_candidate_role_descriptors(
            candidate_0_descriptor=_readonly(stack, source_path),
            candidate_1_descriptor=_readonly(stack, label_path),
            admission_receipt=mismatch,
        )


def test_descriptor_identity_change_during_whole_zip_validation_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    label_path, source_path, receipt = _fixture_archives(tmp_path)
    real_validate = binding_adapter.validate_zip_descriptor
    mutated = False

    def changing_validate(descriptor: int, *, policy):  # noqa: ANN001, ANN202
        nonlocal mutated
        result = real_validate(descriptor, policy=policy)
        if not mutated:
            metadata = label_path.stat()
            os.utime(
                label_path,
                ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000),
            )
            mutated = True
        return result

    monkeypatch.setattr(
        binding_adapter,
        "validate_zip_descriptor",
        changing_validate,
    )
    with ExitStack() as stack, pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_archive_descriptor_changed",
    ):
        resolve_aihub538_candidate_role_descriptors(
            candidate_0_descriptor=_readonly(stack, label_path),
            candidate_1_descriptor=_readonly(stack, source_path),
            admission_receipt=receipt,
        )


def test_process_local_validation_capability_cannot_be_reconstructed(
    tmp_path: Path,
) -> None:
    assert not hasattr(
        binding_adapter,
        "register_aihub538_prevalidated_aggregate",
    )
    assert not hasattr(
        training_dataset_adapter,
        "_issue_prevalidated_training_admission",
    )
    label_path, _source_path, _receipt = _fixture_archives(tmp_path)
    with ExitStack() as stack:
        stable = validate_aihub538_stable_zip_descriptor(
            _readonly(stack, label_path)
        )
        with pytest.raises(
            ValueError,
            match="aihub538_stable_zip_capability_invalid",
        ):
            replace(stable)
        with pytest.raises(
            ValueError,
            match="aihub538_stable_zip_capability_invalid",
        ):
            replace(stable, _capability=object())
        with pytest.raises(
            ValueError,
            match="aihub538_stable_zip_capability_invalid",
        ):
            replace(
                stable,
                validation_receipt=replace(
                    stable.validation_receipt,
                    archive_sha256=_sha("self-consistent-unvalidated-archive"),
                ),
            )


def test_immutable_candidate_validation_is_distinct_and_resolves_roles(tmp_path:Path)->None:
    label_path,source_path,receipt=_fixture_archives(tmp_path)
    label_path.chmod(0o400); source_path.chmod(0o400)
    with ExitStack() as stack:
        label_fd=_readonly(stack,label_path); source_fd=_readonly(stack,source_path)
        with pytest.raises(Aihub538DecisionCalibrationError):
            validate_aihub538_stable_zip_descriptor(label_fd)
        immutable=validate_aihub538_immutable_zip_descriptor(label_fd)
        immutable_source=validate_aihub538_immutable_zip_descriptor(source_fd)
        binding=resolve_aihub538_immutable_candidate_role_descriptors(candidate_0_descriptor=source_fd,candidate_1_descriptor=label_fd,admission_receipt=receipt)
        assert immutable.validation_receipt.archive_sha256==receipt.label_archive_sha256
        assert (binding.label_candidate_index,binding.source_candidate_index)==(1,0)
        with pytest.raises(
            Aihub538DecisionCalibrationError,
            match="aihub538_mutable_archive_validation_required",
        ):
            bind_aihub538_prevalidated_archive_descriptors(
                label=immutable,
                source=immutable_source,
                admission_receipt=receipt,
            )
        label_path.chmod(0o600)
        with pytest.raises(Aihub538DecisionCalibrationError,match="archive_descriptor_changed"):
            binding_adapter.assert_aihub538_stable_zip_descriptor(immutable)
    with ExitStack() as stack:
        with pytest.raises(Aihub538DecisionCalibrationError):
            validate_aihub538_immutable_zip_descriptor(_readonly(stack,label_path))


def test_archive_capabilities_reject_exact_copy_and_fork_replay(
    tmp_path: Path,
) -> None:
    label_path, source_path, receipt = _fixture_archives(tmp_path)
    with ExitStack() as stack:
        binding = bind_aihub538_admitted_archive_descriptors(
            label_zip_descriptor=_readonly(stack, label_path),
            source_zip_descriptor=_readonly(stack, source_path),
            admission_receipt=receipt,
        )
        with pytest.raises(
            ValueError,
            match="aihub538_admitted_archive_capability_invalid",
        ):
            replace(binding)

        read_descriptor, write_descriptor = os.pipe()
        child = os.fork()
        if child == 0:  # pragma: no cover - assertion is read by the parent
            os.close(read_descriptor)
            reasons: list[str] = []
            try:
                binding_adapter.assert_aihub538_stable_zip_descriptor(
                    binding.label
                )
            except Aihub538DecisionCalibrationError as error:
                reasons.append(error.reason_code)
            try:
                assert_aihub538_admitted_archive_binding(
                    binding,
                    admission_receipt=receipt,
                    policy=binding.security_policy,
                )
            except Aihub538DecisionCalibrationError as error:
                reasons.append(error.reason_code)
            os.write(write_descriptor, "|".join(reasons).encode("ascii"))
            os.close(write_descriptor)
            os._exit(0)
        os.close(write_descriptor)
        child_result = os.read(read_descriptor, 4096).decode("ascii")
        os.close(read_descriptor)
        _, child_status = os.waitpid(child, 0)
        assert os.waitstatus_to_exitcode(child_status) == 0
        assert child_result == (
            "aihub538_archive_validation_capability_invalid|"
            "aihub538_admitted_archive_capability_invalid"
        )
        revoke_aihub538_admitted_archive_binding(binding)


def test_prevalidated_admission_reasserts_descriptor_freshness(
    tmp_path: Path,
) -> None:
    label_path, source_path, receipt = _fixture_archives(tmp_path)
    with ExitStack() as stack:
        binding = bind_aihub538_admitted_archive_descriptors(
            label_zip_descriptor=_readonly(stack, label_path),
            source_zip_descriptor=_readonly(stack, source_path),
            admission_receipt=receipt,
        )
        metadata = label_path.stat()
        os.utime(
            label_path,
            ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000),
        )
        with pytest.raises(
            Aihub538DecisionCalibrationError,
            match="aihub538_archive_descriptor_changed",
        ):
            Aihub538TrainingDatasetAdapter().admit_prevalidated(
                archive_binding=binding,
                admission_receipt=receipt,
                identity_hash_key=b"k" * 32,
            )


def test_actual_media_accepts_only_live_prevalidated_binding_without_rescan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    label_path, source_path, receipt = _fixture_archives(tmp_path)
    with ExitStack() as stack:
        label_descriptor = _readonly(stack, label_path)
        source_descriptor = _readonly(stack, source_path)
        binding = bind_aihub538_admitted_archive_descriptors(
            label_zip_descriptor=label_descriptor,
            source_zip_descriptor=source_descriptor,
            admission_receipt=receipt,
        )

        def unexpected_rescan(**_ignored: object) -> None:
            raise AssertionError("whole ZIP validation unexpectedly repeated")

        monkeypatch.setattr(
            actual_media_adapter,
            "bind_aihub538_admitted_archive_descriptors",
            unexpected_rescan,
        )
        # The intentionally minimal fixture has no TL174 role component, so it
        # fails at inventory schema admission.  Reaching that boundary proves
        # that the live capability path did not invoke the full-ZIP binder.
        with pytest.raises(
            Aihub538TrainingAdmissionError,
            match="aihub538_training_role_component_invalid",
        ):
            Aihub538ActualMediaCalibrationAdapter(
                label_zip_descriptor=label_descriptor,
                source_zip_descriptor=source_descriptor,
                identity_hash_key=b"k" * 32,
                admission_receipt=receipt,
                archive_binding=binding,
                restricted_workspace_root=tmp_path / "unused-workspace",
                landmark_model_path=tmp_path / "unused-model",
                landmark_model_sha256=_sha("unused-model"),
                video_qc_policy=None,  # type: ignore[arg-type]
                av_sync_policy=None,  # type: ignore[arg-type]
                container_policy=SimpleNamespace(maximum_rgb_sample_frames=8),
                landmark_policy=None,  # type: ignore[arg-type]
                ffmpeg_binary=sys.executable,
                ffprobe_binary=sys.executable,
            )
    assert not (tmp_path / "unused-workspace").exists()


def test_strict_admission_reload_rejects_non_0400_and_tampering(
    tmp_path: Path,
) -> None:
    _label_path, _source_path, receipt = _fixture_archives(tmp_path)
    receipt_path = _admission_receipt_file(
        tmp_path / "50-dataset-admission.json",
        receipt,
    )
    receipt_path.chmod(0o600)
    with ExitStack() as stack, pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_archive_descriptor_invalid",
    ):
        load_aihub538_training_admission_receipt_descriptor(
            _readonly(stack, receipt_path)
        )

    receipt_path.chmod(0o600)
    document = receipt.to_json_object()
    document["source_archive_sha256"] = _sha("tampered")
    receipt_path.write_bytes(canonical_json_bytes(document) + b"\n")
    receipt_path.chmod(0o400)
    with ExitStack() as stack, pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_admission_receipt_reload_failed",
    ):
        load_aihub538_training_admission_receipt_descriptor(
            _readonly(stack, receipt_path)
        )
