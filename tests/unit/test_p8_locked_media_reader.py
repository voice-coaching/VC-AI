from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from typing import Iterator
from zipfile import ZIP_STORED, ZipFile

import pytest

from voice_coach.pronunciation_attempts.adapters.calibrated_runtime_decisions import (
    CalibratedAvSyncDecisionAdapter,
    CalibratedAvSyncDecisionPolicy,
    CalibratedVideoQcDecisionAdapter,
    CalibratedVideoQcDecisionPolicy,
)
from voice_coach.pronunciation_attempts.adapters.lip_audio_av_sync import (
    LipAudioAvSyncPolicy,
)
from voice_coach.pronunciation_attempts.adapters.mediapipe_landmark_worker import (
    BACKEND_REVISION,
)
from voice_coach.pronunciation_attempts.adapters.mediapipe_lip_landmarks import (
    MediaPipeFaceFrame,
    MediaPipeLandmarkBatch,
    MediaPipeLipGeometry,
)
from voice_coach.pronunciation_attempts.adapters.video_qc import VideoQcPolicy
from voice_coach.visual_feedback.adapters.aihub538_locked_dataset import (
    P8LockedItemHandle,
)
from voice_coach.visual_feedback.adapters.p8_locked_media_reader import (
    P8LockedMediaMeasurement,
    P8LockedMediaReaderError,
    issue_p8_locked_media_reader,
    require_issued_p8_locked_media_measurement,
)
from voice_coach.visual_feedback.adapters.p8_production_frozen_pipeline import (
    P8ProductionPipelineError,
    ProductionP8FrozenLockedPipeline,
    bind_production_p8_media_workspace,
)
from voice_coach.visual_feedback.adapters.p8_seal_store import P8SealStateStore
from voice_coach.visual_feedback.models.p8_locked_evaluation import P8ReadZeroSeal
from voice_coach.visual_feedback.models.runtime_eligibility import (
    media_capture_contract_sha256,
    visual_observation_contract_sha256,
)
import voice_coach.visual_feedback.adapters.p8_locked_media_reader as reader_module
import voice_coach.visual_feedback.adapters.p8_production_frozen_pipeline as pipeline_module


_ITEM_BINDING = hashlib.sha256(b"p8-locked-item-fixture").hexdigest()
_TRANSACTION = hashlib.sha256(b"p8-locked-transaction-fixture").hexdigest()
_ISSUED_AT = "2026-08-28T00:00:00Z"


class _FixtureTracker:
    """Deterministic fixture detector; production has no injection parameter."""

    def __init__(self, **values: object) -> None:
        self._model_sha256 = str(values["expected_model_sha256"])

    def detect(self, sample) -> MediaPipeLandmarkBatch:  # noqa: ANN001
        frames = tuple(
            MediaPipeFaceFrame(
                timestamp_ms=frame.timestamp_us // 1_000,
                face_count=1,
                lip_geometry=MediaPipeLipGeometry(
                    upper_lip_y=0.40,
                    lower_lip_y=0.45 + 0.01 * (index % 3),
                    left_corner_x=0.35,
                    right_corner_x=0.65,
                ),
            )
            for index, frame in enumerate(sample.frames)
        )
        return MediaPipeLandmarkBatch(
            asset_handle_id=sample.asset_handle_id,
            container_sha256=sample.container_sha256,
            backend_revision=BACKEND_REVISION,
            model_sha256=self._model_sha256,
            network_isolation="seccomp_socket_denied",
            frames=frames,
        )


class _FailingFixtureTracker(_FixtureTracker):
    def detect(self, sample):  # noqa: ANN001, ANN201
        del sample
        raise P8LockedMediaReaderError("p8_locked_media_fixture_tracker_failed")


@pytest.fixture(scope="module")
def media_payloads(tmp_path_factory: pytest.TempPathFactory) -> tuple[bytes, bytes]:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is required for the concrete media fixture")
    directory = tmp_path_factory.mktemp("p8-concrete-media")
    video = directory / "fixture.mp4"
    audio = directory / "fixture.wav"
    commands = (
        [
            ffmpeg,
            "-v", "error",
            "-nostdin",
            "-f", "lavfi",
            "-i", "testsrc=size=320x240:rate=25:duration=2",
            "-an",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-threads", "1",
            "-y", str(video),
        ],
        [
            ffmpeg,
            "-v", "error",
            "-nostdin",
            "-f", "lavfi",
            "-i", "sine=frequency=440:sample_rate=16000:duration=2",
            "-c:a", "pcm_s16le",
            "-y", str(audio),
        ],
    )
    for command in commands:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
        if completed.returncode != 0:
            pytest.skip("ffmpeg fixture backend is unavailable")
    return video.read_bytes(), audio.read_bytes()


@contextmanager
def _locked_item(
    directory: Path,
    *,
    video_payload: bytes,
    audio_payload: bytes,
) -> Iterator[P8LockedItemHandle]:
    label_path = directory / "labels.zip"
    source_path = directory / "source.zip"
    label_document = [
        {
            "Video_info": {"video_Duration": 2.0},
            "Audio_info": {"Audio_Duration": 2.0},
            "Sentence_info": [
                {
                    "ID": "1",
                    "start_time": 0.25,
                    "end_time": 1.75,
                    "sentence_text": "fixture transcript remains private",
                    "topic": "fixture",
                }
            ],
        }
    ]
    with ZipFile(label_path, "w", compression=ZIP_STORED) as archive:
        archive.writestr(
            "VL11/private-item.json",
            json.dumps(label_document).encode("utf-8"),
        )
    with ZipFile(source_path, "w", compression=ZIP_STORED) as archive:
        archive.writestr("VS11/private-item.mp4", video_payload)
        archive.writestr("VS11/private-item.wav", audio_payload)
    with ZipFile(label_path, "r") as labels, ZipFile(source_path, "r") as sources:
        yield P8LockedItemHandle(
            _item_binding_sha256=_ITEM_BINDING,
            angle="A",
            noise_code=1,
            environment="J",
            _label_archive=labels,
            _source_archive=sources,
            _label_info=labels.getinfo("VL11/private-item.json"),
            _video_info=sources.getinfo("VS11/private-item.mp4"),
            _audio_info=sources.getinfo("VS11/private-item.wav"),
        )


@contextmanager
def _reader(
    directory: Path,
) -> Iterator[tuple[object, int]]:
    model_path = directory / "fixture-model.task"
    model_payload = b"p8-fixture-model-bytes"
    model_path.write_bytes(model_payload)
    model_path.chmod(0o400)
    descriptor = os.open(model_path, os.O_RDONLY | os.O_NOFOLLOW)
    reader = issue_p8_locked_media_reader(
        landmark_model_descriptor=descriptor,
        landmark_model_sha256=hashlib.sha256(model_payload).hexdigest(),
        video_qc_policy=VideoQcPolicy(
            underexposed_luma_max_8bit=16,
            overexposed_luma_min_8bit=239,
            lip_roi_padding_fraction=0.05,
            minimum_single_face_frames=6,
            maximum_total_rgb_bytes=64 * 1024 * 1024,
        ),
        av_sync_policy=LipAudioAvSyncPolicy(
            maximum_absolute_offset_ms=300,
            offset_step_ms=20,
            energy_window_ms=40,
            minimum_landmark_frames=6,
            maximum_anchor_count=4,
        ),
    )
    try:
        yield reader, descriptor
    finally:
        reader.close()
        os.close(descriptor)


@contextmanager
def _workspace(directory: Path) -> Iterator[tuple[Path, int]]:
    workspace = directory / "work"
    workspace.mkdir(mode=0o700)
    workspace.chmod(0o700)
    descriptor = os.open(
        workspace,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        yield workspace, descriptor
    finally:
        os.close(descriptor)


def _measure(
    *,
    tmp_path: Path,
    item: P8LockedItemHandle,
    monkeypatch: pytest.MonkeyPatch,
) -> P8LockedMediaMeasurement:
    monkeypatch.setattr(
        reader_module, "MediaPipeLipLandmarkAdapter", _FixtureTracker
    )
    with _reader(tmp_path) as (reader, _), _workspace(tmp_path) as (
        workspace,
        workspace_descriptor,
    ):
        with reader.bind_restricted_workspace(
            workspace_descriptor=workspace_descriptor,
            access_transaction_sha256=_TRANSACTION,
            issued_at_utc=_ISSUED_AT,
        ):
            measurement = reader.measure(item)
        assert list(workspace.iterdir()) == []
        return measurement


def test_concrete_reader_evaluates_fixture_and_cleans_workspace(
    tmp_path: Path,
    media_payloads: tuple[bytes, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video, audio = media_payloads
    with _locked_item(
        tmp_path, video_payload=video, audio_payload=audio
    ) as item:
        measurement = _measure(
            tmp_path=tmp_path,
            item=item,
            monkeypatch=monkeypatch,
        )

    assert type(measurement) is P8LockedMediaMeasurement
    assert measurement.binding.attempt_id_sha256 == _ITEM_BINDING
    assert measurement.video_qc.container_sha256 == (
        measurement.binding.video_source_sha256
    )
    assert measurement.av_sync.video_source_sha256 == (
        measurement.binding.video_source_sha256
    )


def test_production_pipeline_uses_concrete_reader_not_unbound_sentinel(
    tmp_path: Path,
    media_payloads: tuple[bytes, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video, audio = media_payloads
    monkeypatch.setattr(
        reader_module, "MediaPipeLipLandmarkAdapter", _FixtureTracker
    )
    with _reader(tmp_path) as (reader, _), _workspace(tmp_path) as (
        workspace,
        workspace_descriptor,
    ), _locked_item(
        tmp_path, video_payload=video, audio_payload=audio
    ) as item:
        qc_policy = CalibratedVideoQcDecisionPolicy(
            policy_revision="fixture_qc_decision_v1",
            expected_measurement_policy_sha256=(
                reader._video_qc_policy.receipt_sha256
            ),
            minimum_single_face_coverage=0.0,
            maximum_multiple_face_frame_fraction=1.0,
            minimum_mean_luma_8bit=0.0,
            maximum_mean_luma_8bit=255.0,
            maximum_underexposed_pixel_fraction=1.0,
            maximum_overexposed_pixel_fraction=1.0,
            minimum_lip_roi_mean_neighbor_luma_delta_8bit=0.0,
            minimum_track_transition_coverage=0.0,
            minimum_longest_single_face_run_fraction=0.0,
            maximum_lip_center_step_normalized=2**0.5,
        )
        sync_policy = CalibratedAvSyncDecisionPolicy(
            policy_revision="fixture_sync_decision_v1",
            expected_measurement_policy_sha256=(
                reader._av_sync_policy.policy_sha256
            ),
            maximum_absolute_offset_ms=300,
            minimum_correlation=-1.0,
            minimum_anchor_count=1,
            minimum_anchor_strength=0.0,
        )
        pipeline = ProductionP8FrozenLockedPipeline(
            pipeline_module._ISSUER,
            pins="a" * 64,
            catalog=SimpleNamespace(catalog_sha256="b" * 64),
            claim_policy=SimpleNamespace(policy_sha256="c" * 64),
            qc_decider=CalibratedVideoQcDecisionAdapter(policy=qc_policy),
            sync_decider=CalibratedAvSyncDecisionAdapter(policy=sync_policy),
            media_reader=reader,
            expected_landmark_model_sha256=reader._model_sha256,
            expected_video_qc_measurement_code_sha256=(
                pipeline_module.video_qc_measurement_code_sha256()
            ),
            expected_av_sync_measurement_code_sha256=(
                pipeline_module.av_sync_measurement_code_sha256()
            ),
        )
        with bind_production_p8_media_workspace(
            pipeline,
            workspace_descriptor=workspace_descriptor,
            access_transaction_sha256=_TRANSACTION,
            issued_at_utc=_ISSUED_AT,
        ):
            result = pipeline.evaluate(item)
        assert result.decode_succeeded is True
        assert result.evaluation_succeeded is True
        assert result.qc_decision_lineage_valid is True
        assert result.sync_decision_lineage_valid is True
        assert list(workspace.iterdir()) == []


def test_malformed_media_fails_closed_and_cleans_workspace(
    tmp_path: Path,
    media_payloads: tuple[bytes, bytes],
) -> None:
    _, audio = media_payloads
    with _locked_item(
        tmp_path,
        video_payload=b"not-an-mp4",
        audio_payload=audio,
    ) as item, _reader(tmp_path) as (reader, _), _workspace(tmp_path) as (
        workspace,
        workspace_descriptor,
    ):
        with reader.bind_restricted_workspace(
            workspace_descriptor=workspace_descriptor,
            access_transaction_sha256=_TRANSACTION,
            issued_at_utc=_ISSUED_AT,
        ):
            with pytest.raises(
                P8LockedMediaReaderError,
                match="p8_locked_media_mux_failed",
            ):
                reader.measure(item)
        assert list(workspace.iterdir()) == []


def test_resource_bound_fails_before_decode_and_cleans_workspace(
    tmp_path: Path,
    media_payloads: tuple[bytes, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video, audio = media_payloads
    monkeypatch.setattr(reader_module, "_MAXIMUM_VIDEO_BYTES", 64)
    with _locked_item(
        tmp_path, video_payload=video, audio_payload=audio
    ) as item, _reader(tmp_path) as (reader, _), _workspace(tmp_path) as (
        workspace,
        workspace_descriptor,
    ):
        with reader.bind_restricted_workspace(
            workspace_descriptor=workspace_descriptor,
            access_transaction_sha256=_TRANSACTION,
            issued_at_utc=_ISSUED_AT,
        ):
            with pytest.raises(
                P8LockedMediaReaderError,
                match="p8_locked_media_video_resource_limit_exceeded",
            ):
                reader.measure(item)
        assert list(workspace.iterdir()) == []


def test_tracker_failure_deletes_admitted_media_and_item_workspace(
    tmp_path: Path,
    media_payloads: tuple[bytes, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video, audio = media_payloads
    monkeypatch.setattr(
        reader_module, "MediaPipeLipLandmarkAdapter", _FailingFixtureTracker
    )
    with _locked_item(
        tmp_path, video_payload=video, audio_payload=audio
    ) as item, _reader(tmp_path) as (reader, _), _workspace(tmp_path) as (
        workspace,
        workspace_descriptor,
    ):
        with reader.bind_restricted_workspace(
            workspace_descriptor=workspace_descriptor,
            access_transaction_sha256=_TRANSACTION,
            issued_at_utc=_ISSUED_AT,
        ):
            with pytest.raises(
                P8LockedMediaReaderError,
                match="p8_locked_media_fixture_tracker_failed",
            ):
                reader.measure(item)
        assert list(workspace.iterdir()) == []


def test_issued_measurement_rejects_different_item_lineage(
    tmp_path: Path,
    media_payloads: tuple[bytes, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video, audio = media_payloads
    with _locked_item(
        tmp_path, video_payload=video, audio_payload=audio
    ) as item:
        measurement = _measure(
            tmp_path=tmp_path,
            item=item,
            monkeypatch=monkeypatch,
        )
    with pytest.raises(
        P8LockedMediaReaderError,
        match="p8_locked_media_measurement_lineage_invalid",
    ):
        require_issued_p8_locked_media_measurement(
            measurement,
            expected_item_binding_sha256="f" * 64,
        )


def test_issued_measurement_rejects_post_issue_integrity_tamper(
    tmp_path: Path,
    media_payloads: tuple[bytes, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video, audio = media_payloads
    with _locked_item(
        tmp_path, video_payload=video, audio_payload=audio
    ) as item:
        measurement = _measure(
            tmp_path=tmp_path,
            item=item,
            monkeypatch=monkeypatch,
        )
    with pytest.raises(AttributeError, match="immutable"):
        measurement._lineage_sha256 = "0" * 64
    object.__setattr__(measurement, "_lineage_sha256", "0" * 64)
    with pytest.raises(
        P8LockedMediaReaderError,
        match="p8_locked_media_measurement_lineage_invalid",
    ):
        require_issued_p8_locked_media_measurement(
            measurement,
            expected_item_binding_sha256=_ITEM_BINDING,
        )


def test_media_engine_installed_code_pin_is_the_concrete_reader() -> None:
    path = pipeline_module._PRODUCTION_CODE_PATHS[
        "media_engine_code_sha256"
    ]
    assert path.name == "p8_locked_media_reader.py"
    seal = SimpleNamespace(
        **{
            name: pipeline_module._sha256_path(installed)
            for name, installed in pipeline_module._PRODUCTION_CODE_PATHS.items()
        }
    )
    seal.media_engine_code_sha256 = hashlib.sha256(
        b"old-production-sidecar-pin"
    ).hexdigest()
    with pytest.raises(
        P8ProductionPipelineError,
        match="p8_production_media_engine_code_pin_mismatch",
    ):
        pipeline_module._validate_installed_production_code_pins(seal)


def test_concrete_media_engine_pin_survives_seal_store_reload(
    tmp_path: Path,
) -> None:
    """The durable seal reloads with exactly the installed reader pin."""

    installed_pins = {
        name: pipeline_module._sha256_path(path)
        for name, path in pipeline_module._PRODUCTION_CODE_PATHS.items()
    }

    def digest(label: str) -> str:
        return hashlib.sha256(label.encode("utf-8")).hexdigest()

    media_capture_contract_id = "p8_fixture_media_capture_v1"
    visual_observation_contract_id = "p8_fixture_visible_lips_v1"
    seal = P8ReadZeroSeal.issue(
        seal_id="p8_fixture_reader_seal_v1",
        protocol_revision="p8_fixture_protocol_v1",
        source_revision="aihub_538_fixture_v1",
        p6_evidence_receipt_sha256=digest("p6"),
        calibration_artifact_sha256=digest("calibration"),
        claim_catalog_sha256=digest("catalog"),
        claim_policy_sha256=digest("claim-policy"),
        media_capture_contract_id=media_capture_contract_id,
        media_capture_contract_sha256=media_capture_contract_sha256(
            media_capture_contract_id
        ),
        visual_observation_contract_id=visual_observation_contract_id,
        visual_observation_contract_sha256=visual_observation_contract_sha256(
            visual_observation_contract_id
        ),
        decoder_code_sha256=digest("decoder"),
        tracker_code_sha256=digest("tracker"),
        tracker_adapter_code_sha256=digest("tracker-adapter"),
        landmark_model_sha256=digest("model"),
        normalizer_code_sha256=digest("normalizer"),
        cue_extractor_code_sha256=digest("cue-extractor"),
        video_qc_measurement_code_sha256=digest("video-qc-measurement"),
        video_qc_measurement_policy_sha256=digest("video-qc-policy"),
        video_qc_decision_code_sha256=digest("video-qc-decision"),
        video_qc_decision_policy_revision="p8_fixture_video_qc_decision_v1",
        video_qc_decision_policy_sha256=digest("video-qc-decision-policy"),
        av_sync_measurement_code_sha256=digest("av-sync-measurement"),
        av_sync_measurement_policy_sha256=digest("av-sync-policy"),
        av_sync_decision_code_sha256=digest("av-sync-decision"),
        av_sync_decision_policy_revision="p8_fixture_av_sync_decision_v1",
        av_sync_decision_policy_sha256=digest("av-sync-decision-policy"),
        phone_window_code_sha256=digest("phone-window"),
        phone_window_policy_revision="p8_fixture_phone_window_v1",
        phone_window_policy_sha256=digest("phone-window-policy"),
        evaluation_pipeline_code_sha256=installed_pins[
            "evaluation_pipeline_code_sha256"
        ],
        source_evaluator_code_sha256=installed_pins[
            "source_evaluator_code_sha256"
        ],
        media_engine_code_sha256=installed_pins["media_engine_code_sha256"],
        sealed_at_utc="2026-08-28T00:00:00Z",
    )
    state_directory = tmp_path / "state"
    state_directory.mkdir(mode=0o700)
    store = P8SealStateStore(state_directory)
    store.create_read_zero_seal(seal)

    reloaded = store.load_read_zero_seal()

    assert reloaded == seal
    assert reloaded.media_engine_code_sha256 == installed_pins[
        "media_engine_code_sha256"
    ]
    pipeline_module._validate_installed_production_code_pins(reloaded)
