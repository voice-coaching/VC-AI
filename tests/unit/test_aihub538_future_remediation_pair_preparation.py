from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager
import os
import stat

import pytest

from voice_coach.visual_feedback.models.aihub538_label_capacity import (
    AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256,
)
from voice_coach.visual_feedback.models.aihub538_nonlocked_shards import (
    AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS,
    AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS,
)
from voice_coach.visual_feedback.models.aihub538_training_evidence import (
    aihub538_shard_admission_policy_sha256,
)
from voice_coach.visual_feedback.services.aihub538_future_remediation_pair_preparation import (
    Aihub538FuturePairPreparationError,
    Aihub538FutureRemediationPairPreparationService,
)
import voice_coach.visual_feedback.services.aihub538_future_remediation_pair_preparation as future_module
from voice_coach.visual_feedback.services.aihub538_training_pipeline import (
    Aihub538TrainingPipeline,
)


def test_future_pipeline_uses_exact_terminal_policy_without_changing_historical() -> None:
    future = AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS[2]
    future_label_audit = AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[2]
    historical = AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS[1]
    future_pipeline = Aihub538TrainingPipeline(shard_spec=future)
    historical_pipeline = Aihub538TrainingPipeline(shard_spec=historical)
    assert future_pipeline._smoke_service._dataset_adapter._uses_nonlocked_label_schema
    assert not historical_pipeline._smoke_service._dataset_adapter._uses_nonlocked_label_schema
    assert len(AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256) == 64
    assert future_label_audit.shard_id == "tl177_capacity_audit"
    assert future_label_audit.shard_id != future.shard_id
    assert aihub538_shard_admission_policy_sha256(future) != (
        aihub538_shard_admission_policy_sha256(historical)
    )


def test_permission_fails_before_descriptors_or_output_are_opened(tmp_path: Path) -> None:
    output = tmp_path / "output"
    with pytest.raises(
        Aihub538FuturePairPreparationError,
        match="permission_not_confirmed",
    ):
        Aihub538FutureRemediationPairPreparationService().run(
            remediation_receipt_descriptor=-1,
            identity_key_descriptor=-1,
            pairs=(),
            output_directory=output,
            dataset_access_permission_confirmed=False,
        )
    assert not output.exists()


def test_cli_rejects_unpaired_input_lists_without_opening_them(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / (
        "prepare_aihub538_future_remediation_pairs.py"
    )
    spec = importlib.util.spec_from_file_location("future_pair_cli", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.main(
        [
            "--remediation-receipt-fd",
            "3",
            "--identity-key-fd",
            "4",
            "--pair-download-directory",
            str(tmp_path / "never-opened"),
            "--pair-download-directory",
            str(tmp_path / "also-never-opened"),
            "--prepared-label-directory",
            str(tmp_path / "never-opened-label"),
            "--output-directory",
            str(tmp_path / "output"),
            "--dataset-access-permission-confirmed",
        ]
    )
    assert result == 65
    assert not (tmp_path / "output").exists()


def _fake_receipt(*, include_tl180: bool = False):
    shards = AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS[2 : 6 if include_tl180 else 5]
    lineages = tuple(
        SimpleNamespace(
            label_role_component=shard.label_role_component,
            label_file_key=shard.ordered_file_keys[0],
            label_archive_sha256=f"{index + 3:064x}",
            label_content_root_sha256=f"{index + 13:064x}",
        )
        for index, shard in enumerate(shards)
    )
    document = {
        "authorized_label_role_prefix": [s.label_role_component for s in shards],
        "authorized_label_file_key_prefix": [s.ordered_file_keys[0] for s in shards],
        "authorized_source_role_prefix": [s.source_role_component for s in shards],
        "authorized_source_file_key_prefix": [s.ordered_file_keys[1] for s in shards],
    }
    return SimpleNamespace(
        is_feasible=True,
        evaluated_prefix_lineage=lineages,
        receipt_sha256="1" * 64,
        identity_namespace_commitment_sha256="2" * 64,
        _validate=lambda: None,
        to_json_object=lambda: document,
    )


def test_existing_output_is_preserved_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    marker = output / "marker.bin"
    marker.write_bytes(b"existing-owner-data")
    monkeypatch.setattr(
        future_module,
        "load_aihub538_p6_remediation_receipt_descriptor",
        lambda _fd: _fake_receipt(),
    )
    monkeypatch.setattr(
        future_module,
        "load_aihub538_identity_key_descriptor",
        lambda _fd: SimpleNamespace(key=b"k" * 32),
    )
    with pytest.raises(Aihub538FuturePairPreparationError):
        Aihub538FutureRemediationPairPreparationService().run(
            remediation_receipt_descriptor=3,
            identity_key_descriptor=4,
            pairs=tuple(
                SimpleNamespace(
                    download_directory=tmp_path / f"download-{index}",
                    prepared_label_directory=tmp_path / f"label-{index}",
                )
                for index in range(3)
            ),
            output_directory=output,
            dataset_access_permission_confirmed=True,
        )
    assert marker.read_bytes() == b"existing-owner-data"
    assert set(output.iterdir()) == {marker}


def test_post_feasible_tl180_prefix_is_rejected_before_any_input_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        future_module,
        "load_aihub538_p6_remediation_receipt_descriptor",
        lambda _fd: _fake_receipt(include_tl180=True),
    )
    with pytest.raises(Aihub538FuturePairPreparationError):
        Aihub538FutureRemediationPairPreparationService().run(
            remediation_receipt_descriptor=3,
            identity_key_descriptor=-1,
            pairs=tuple(
                SimpleNamespace(
                    download_directory=tmp_path / f"never-opened-{index}",
                    prepared_label_directory=tmp_path / f"never-opened-label-{index}",
                )
                for index in range(4)
            ),
            output_directory=tmp_path / "output",
            dataset_access_permission_confirmed=True,
        )
    assert not (tmp_path / "output").exists()


def _install_valid_three_pair_fakes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receipt = _fake_receipt()
    monkeypatch.setattr(
        future_module,
        "load_aihub538_p6_remediation_receipt_descriptor",
        lambda _fd: receipt,
    )
    monkeypatch.setattr(
        future_module,
        "load_aihub538_identity_key_descriptor",
        lambda _fd: SimpleNamespace(key=b"k" * 32),
    )
    admissions = []
    validations = {}
    for index, shard in enumerate(AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS[2:5]):
        lineage = receipt.evaluated_prefix_lineage[index]
        label_validation = SimpleNamespace(
            archive_sha256=lineage.label_archive_sha256,
            content_root_sha256=lineage.label_content_root_sha256,
        )
        source_validation = SimpleNamespace(
            archive_sha256=f"{index + 23:064x}",
            content_root_sha256=f"{index + 33:064x}",
        )
        validations[index] = (label_validation, source_validation)
        admissions.append(
            SimpleNamespace(
                label_archive_sha256=label_validation.archive_sha256,
                label_zip_content_root_sha256=label_validation.content_root_sha256,
                source_archive_sha256=source_validation.archive_sha256,
                source_zip_content_root_sha256=source_validation.content_root_sha256,
                admission_policy_sha256=aihub538_shard_admission_policy_sha256(shard),
                receipt_sha256=f"{index + 43:064x}",
            )
        )

    class Pipeline:
        def __init__(self, *, shard_spec):
            self.index = tuple(AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS[2:5]).index(shard_spec)

        def run_from_completed_directory(self, *, output_directory, **_kwargs):
            output_directory.mkdir(mode=0o700)
            for candidate_index in range(2):
                path = output_directory / f"candidate-{candidate_index}.zip"
                path.write_bytes(bytes((self.index, candidate_index)))
                path.chmod(0o600)
            admission = output_directory / "50-dataset-admission.json"
            admission.write_text(str(self.index), encoding="utf-8")
            admission.chmod(0o400)
            label, source = validations[self.index]
            return SimpleNamespace(
                label_zip_validation=label,
                source_zip_validation=source,
                completion_receipt_sha256=f"{self.index + 53:064x}",
            )

    monkeypatch.setattr(future_module, "Aihub538TrainingPipeline", Pipeline)
    monkeypatch.setattr(
        future_module,
        "load_aihub538_training_admission_receipt_descriptor",
        lambda fd: admissions[int(os.pread(fd, 1, 0))],
    )

    def validate(fd, **_kwargs):
        payload = os.pread(fd, 2, 0)
        return validations[payload[0]][payload[1]]

    monkeypatch.setattr(future_module, "validate_zip_descriptor", validate)

    class LabelAdapter:
        def __init__(self, *, shard_spec, **_kwargs):
            self.index = tuple(AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS[2:5]).index(shard_spec)

        def audit(self, **_kwargs):
            lineage = receipt.evaluated_prefix_lineage[self.index]
            return SimpleNamespace(
                label_archive_sha256=lineage.label_archive_sha256,
                label_content_root_sha256=lineage.label_content_root_sha256,
                member_namespace_commitment_sha256="6" * 64,
            )

    monkeypatch.setattr(future_module, "Aihub538LabelCapacityAdapter", LabelAdapter)
    prepared_calls = iter(range(3))

    @contextmanager
    def prepared_opener(_directory, **_kwargs):
        index = next(prepared_calls)
        lineage = receipt.evaluated_prefix_lineage[index]
        yield -1, SimpleNamespace(
            shard_id=AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[index + 2].shard_id,
            label_zip_sha256=lineage.label_archive_sha256,
            label_zip_content_root_sha256=lineage.label_content_root_sha256,
            member_namespace_commitment_sha256="6" * 64,
            terminal_frame_tail_policy_sha256=AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256,
            receipt_sha256=f"{index + 63:064x}",
        )

    monkeypatch.setattr(
        future_module,
        "open_aihub538_p6_prepared_label_directory",
        prepared_opener,
    )


def test_dependency_injected_valid_three_pair_e2e_is_private_and_mode_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_valid_three_pair_fakes(monkeypatch, tmp_path)
    output = tmp_path / "output"
    result = Aihub538FutureRemediationPairPreparationService().run(
        remediation_receipt_descriptor=3,
        identity_key_descriptor=4,
        pairs=tuple(
            SimpleNamespace(
                download_directory=tmp_path / f"download-{index}",
                prepared_label_directory=tmp_path / f"prepared-{index}",
            )
            for index in range(3)
        ),
        output_directory=output,
        dataset_access_permission_confirmed=True,
    )
    assert result["ordered_shard_ids"] == ["ts177_tl177", "ts178_tl178", "ts179_tl179"]
    assert result["locked_partition_accessed"] is False
    assert result["promotion_authorized"] is False
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert stat.S_IMODE((output / "99-completion.json").stat().st_mode) == 0o400
    serialized = (output / "99-completion.json").read_text(encoding="utf-8")
    assert "download-" not in serialized and "prepared-" not in serialized
    for shard in result["ordered_shard_ids"]:
        directory = output / shard
        assert stat.S_IMODE((directory / "candidate-0.zip").stat().st_mode) == 0o400
        assert stat.S_IMODE((directory / "70-photometric-v4-handoff.json").stat().st_mode) == 0o400


def test_source_candidate_replacement_before_handoff_is_rejected_and_cleaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_valid_three_pair_fakes(monkeypatch, tmp_path)
    output = tmp_path / "output"
    original = Aihub538FutureRemediationPairPreparationService._write_receipt

    def replacing_write(path: Path, document: dict[str, object]) -> None:
        if path.name == "70-photometric-v4-handoff.json" and path.parent.name == "ts177_tl177":
            source = path.parent / "candidate-1.zip"
            source.unlink()
            source.write_bytes(b"replacement")
            source.chmod(0o600)
        original(path, document)

    monkeypatch.setattr(
        Aihub538FutureRemediationPairPreparationService,
        "_write_receipt",
        staticmethod(replacing_write),
    )
    with pytest.raises(Aihub538FuturePairPreparationError):
        Aihub538FutureRemediationPairPreparationService().run(
            remediation_receipt_descriptor=3,
            identity_key_descriptor=4,
            pairs=tuple(
                SimpleNamespace(
                    download_directory=tmp_path / f"download-{index}",
                    prepared_label_directory=tmp_path / f"prepared-{index}",
                )
                for index in range(3)
            ),
            output_directory=output,
            dataset_access_permission_confirmed=True,
        )
    assert not output.exists()


def test_same_inode_truncate_through_retained_writer_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_valid_three_pair_fakes(monkeypatch, tmp_path)
    base_pipeline = future_module.Aihub538TrainingPipeline
    retained: list[int] = []

    class RetainingPipeline(base_pipeline):
        def run_from_completed_directory(self, **kwargs):
            evidence = super().run_from_completed_directory(**kwargs)
            if self.index == 0:
                retained.append(
                    os.open(
                        kwargs["output_directory"] / "candidate-1.zip",
                        os.O_WRONLY,
                    )
                )
            return evidence

    monkeypatch.setattr(future_module, "Aihub538TrainingPipeline", RetainingPipeline)
    original = Aihub538FutureRemediationPairPreparationService._write_receipt

    def truncating_write(path: Path, document: dict[str, object]) -> None:
        if path.name == "70-photometric-v4-handoff.json" and retained:
            os.ftruncate(retained[0], 0)
        original(path, document)

    monkeypatch.setattr(
        Aihub538FutureRemediationPairPreparationService,
        "_write_receipt",
        staticmethod(truncating_write),
    )
    try:
        with pytest.raises(Aihub538FuturePairPreparationError):
            Aihub538FutureRemediationPairPreparationService().run(
                remediation_receipt_descriptor=3,
                identity_key_descriptor=4,
                pairs=tuple(
                    SimpleNamespace(
                        download_directory=tmp_path / f"download-{index}",
                        prepared_label_directory=tmp_path / f"prepared-{index}",
                    )
                    for index in range(3)
                ),
                output_directory=tmp_path / "output",
                dataset_access_permission_confirmed=True,
            )
    finally:
        for fd in retained:
            os.close(fd)
    assert not (tmp_path / "output").exists()


def test_prior_shard_replacement_during_next_shard_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_valid_three_pair_fakes(monkeypatch, tmp_path)
    base_pipeline = future_module.Aihub538TrainingPipeline

    class ReplacingPipeline(base_pipeline):
        def run_from_completed_directory(self, **kwargs):
            if self.index == 1:
                prior = kwargs["output_directory"].parent / "ts177_tl177" / "candidate-1.zip"
                prior.unlink()
                prior.write_bytes(b"postcheck-replacement")
                prior.chmod(0o400)
            return super().run_from_completed_directory(**kwargs)

    monkeypatch.setattr(future_module, "Aihub538TrainingPipeline", ReplacingPipeline)
    with pytest.raises(Aihub538FuturePairPreparationError):
        Aihub538FutureRemediationPairPreparationService().run(
            remediation_receipt_descriptor=3,
            identity_key_descriptor=4,
            pairs=tuple(
                SimpleNamespace(
                    download_directory=tmp_path / f"download-{index}",
                    prepared_label_directory=tmp_path / f"prepared-{index}",
                )
                for index in range(3)
            ),
            output_directory=tmp_path / "output",
            dataset_access_permission_confirmed=True,
        )
    assert not (tmp_path / "output").exists()


def test_admission_same_inode_truncate_through_retained_writer_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_valid_three_pair_fakes(monkeypatch, tmp_path)
    base_pipeline = future_module.Aihub538TrainingPipeline
    retained: list[int] = []

    class RetainingAdmissionPipeline(base_pipeline):
        def run_from_completed_directory(self, **kwargs):
            evidence = super().run_from_completed_directory(**kwargs)
            if self.index == 0:
                retained.append(
                    os.open(
                        kwargs["output_directory"] / "50-dataset-admission.json",
                        os.O_WRONLY,
                    )
                )
            return evidence

    monkeypatch.setattr(
        future_module,
        "Aihub538TrainingPipeline",
        RetainingAdmissionPipeline,
    )
    original = Aihub538FutureRemediationPairPreparationService._write_receipt

    def truncating_write(path: Path, document: dict[str, object]) -> None:
        if path.name == "70-photometric-v4-handoff.json" and retained:
            os.ftruncate(retained[0], 0)
        original(path, document)

    monkeypatch.setattr(
        Aihub538FutureRemediationPairPreparationService,
        "_write_receipt",
        staticmethod(truncating_write),
    )
    try:
        with pytest.raises(Aihub538FuturePairPreparationError):
            Aihub538FutureRemediationPairPreparationService().run(
                remediation_receipt_descriptor=3,
                identity_key_descriptor=4,
                pairs=tuple(
                    SimpleNamespace(
                        download_directory=tmp_path / f"download-{index}",
                        prepared_label_directory=tmp_path / f"prepared-{index}",
                    )
                    for index in range(3)
                ),
                output_directory=tmp_path / "output",
                dataset_access_permission_confirmed=True,
            )
    finally:
        for fd in retained:
            os.close(fd)
    assert not (tmp_path / "output").exists()


def test_prior_admission_replacement_during_next_shard_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_valid_three_pair_fakes(monkeypatch, tmp_path)
    base_pipeline = future_module.Aihub538TrainingPipeline

    class ReplacingAdmissionPipeline(base_pipeline):
        def run_from_completed_directory(self, **kwargs):
            if self.index == 1:
                prior = (
                    kwargs["output_directory"].parent
                    / "ts177_tl177"
                    / "50-dataset-admission.json"
                )
                prior.unlink()
                prior.write_text("0", encoding="utf-8")
                prior.chmod(0o400)
            return super().run_from_completed_directory(**kwargs)

    monkeypatch.setattr(
        future_module,
        "Aihub538TrainingPipeline",
        ReplacingAdmissionPipeline,
    )
    with pytest.raises(Aihub538FuturePairPreparationError):
        Aihub538FutureRemediationPairPreparationService().run(
            remediation_receipt_descriptor=3,
            identity_key_descriptor=4,
            pairs=tuple(
                SimpleNamespace(
                    download_directory=tmp_path / f"download-{index}",
                    prepared_label_directory=tmp_path / f"prepared-{index}",
                )
                for index in range(3)
            ),
            output_directory=tmp_path / "output",
            dataset_access_permission_confirmed=True,
        )
    assert not (tmp_path / "output").exists()


def test_duplicate_pair_directory_is_rejected_before_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        future_module,
        "load_aihub538_p6_remediation_receipt_descriptor",
        lambda _fd: _fake_receipt(),
    )
    duplicate = tmp_path / "duplicate"
    with pytest.raises(Aihub538FuturePairPreparationError):
        Aihub538FutureRemediationPairPreparationService().run(
            remediation_receipt_descriptor=3,
            identity_key_descriptor=-1,
            pairs=tuple(
                SimpleNamespace(
                    download_directory=duplicate,
                    prepared_label_directory=tmp_path / f"prepared-{index}",
                )
                for index in range(3)
            ),
            output_directory=tmp_path / "output",
            dataset_access_permission_confirmed=True,
        )
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("mutation", ("order", "key", "missing"))
def test_receipt_prefix_order_key_and_missing_rows_fail_before_input_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    receipt = _fake_receipt()
    document = receipt.to_json_object()
    if mutation == "order":
        document["authorized_label_role_prefix"] = list(
            reversed(document["authorized_label_role_prefix"])
        )
    elif mutation == "key":
        document["authorized_source_file_key_prefix"][1] = "99999"
    else:
        receipt.evaluated_prefix_lineage = receipt.evaluated_prefix_lineage[:2]
    monkeypatch.setattr(
        future_module,
        "load_aihub538_p6_remediation_receipt_descriptor",
        lambda _fd: receipt,
    )
    with pytest.raises(Aihub538FuturePairPreparationError):
        Aihub538FutureRemediationPairPreparationService().run(
            remediation_receipt_descriptor=3,
            identity_key_descriptor=-1,
            pairs=tuple(
                SimpleNamespace(
                    download_directory=tmp_path / f"never-{index}",
                    prepared_label_directory=tmp_path / f"never-label-{index}",
                )
                for index in range(3)
            ),
            output_directory=tmp_path / "output",
            dataset_access_permission_confirmed=True,
        )
    assert not (tmp_path / "output").exists()
