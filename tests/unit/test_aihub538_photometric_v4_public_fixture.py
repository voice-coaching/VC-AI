from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helpers"))
from aihub538_photometric_v4_public_fixture import build_public_photometric_v4_fixture
from voice_coach.visual_feedback.adapters.aihub538_admitted_archive_binding import load_aihub538_training_admission_receipt_descriptor
from voice_coach.visual_feedback.adapters.aihub538_actual_media_calibration import Aihub538ActualMediaCalibrationAdapter
from voice_coach.visual_feedback.adapters.aihub538_p6_label_preparation import open_aihub538_p6_prepared_label_directory
from voice_coach.visual_feedback.adapters.aihub538_photometric_v4_production_factory import Aihub538PhotometricV4ProductionFactory
from voice_coach.visual_feedback.adapters.aihub538_training_archive import aihub538_archive_security_policy_for_shard, validate_zip_descriptor
from voice_coach.visual_feedback.models.aihub538_nonlocked_shards import aihub538_nonlocked_shard_by_id


def test_public_fixture_passes_strict_archives_receipts_and_inventory(tmp_path: Path) -> None:
    fixture = build_public_photometric_v4_fixture(tmp_path / "fixture")
    key = fixture.identity_key_path.read_bytes()
    factory = Aihub538PhotometricV4ProductionFactory()
    assert len(fixture.pairs) == 3
    for pair in fixture.pairs:
        shard = aihub538_nonlocked_shard_by_id(pair.shard_id)
        with ExitStack() as stack:
            label_fd = os.open(pair.label_zip, os.O_RDONLY | os.O_NOFOLLOW); stack.callback(os.close, label_fd)
            source_fd = os.open(pair.source_zip, os.O_RDONLY | os.O_NOFOLLOW); stack.callback(os.close, source_fd)
            admission_fd = os.open(pair.admission_receipt_path, os.O_RDONLY | os.O_NOFOLLOW); stack.callback(os.close, admission_fd)
            assert validate_zip_descriptor(label_fd, policy=aihub538_archive_security_policy_for_shard(shard)).archive_sha256 == pair.admission_receipt.label_archive_sha256
            assert validate_zip_descriptor(source_fd, policy=aihub538_archive_security_policy_for_shard(shard)).archive_sha256 == pair.admission_receipt.source_archive_sha256
            admission = load_aihub538_training_admission_receipt_descriptor(admission_fd)
            with open_aihub538_p6_prepared_label_directory(pair.prepared_label_directory, identity_hash_key=key) as (_fd, prepared):
                assert prepared.label_zip_sha256 == admission.label_archive_sha256
            runtime_label_fd = os.open(pair.runtime_label_zip, os.O_RDONLY | os.O_NOFOLLOW); stack.callback(os.close, runtime_label_fd)
            runtime_source_fd = os.open(pair.runtime_source_zip, os.O_RDONLY | os.O_NOFOLLOW); stack.callback(os.close, runtime_source_fd)
            adapter = Aihub538ActualMediaCalibrationAdapter(
                label_zip_descriptor=runtime_label_fd, source_zip_descriptor=runtime_source_fd,
                identity_hash_key=key, admission_receipt=admission, shard_spec=shard,
                restricted_workspace_root=tmp_path / (pair.shard_id + "-workspace"),
                landmark_model_path=tmp_path / "unused.task",
                landmark_model_sha256=factory.landmark_model_sha256,
                video_qc_policy=factory.video_qc_policy(), av_sync_policy=factory.av_sync_policy(),
                container_policy=factory.container_policy(), landmark_policy=factory.landmark_policy(),
                visual_sample_frame_count=8,
            )
            try:
                assert len(adapter.inventory) == 144
            finally:
                adapter.close()
