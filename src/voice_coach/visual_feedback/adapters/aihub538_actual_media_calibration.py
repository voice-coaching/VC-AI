"""Restricted actual-media adapter for allowlisted AI Hub 538 calibration shards.

The adapter consumes only merged non-locked ZIP descriptors that have already
passed :class:`Aihub538TrainingDatasetAdapter`.  Private member names, raw
speaker/session values, sentence timestamps, and extracted media never leave
this module.  One deterministic sentence window is locally muxed into the same
H.264/AAC MP4 envelope accepted by the production runtime, then processed by
the production ffmpeg, MediaPipe, video-QC, and PCM sync adapters.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import os
from pathlib import Path, PurePosixPath
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from typing import BinaryIO
from weakref import WeakKeyDictionary, ref
from zipfile import BadZipFile, ZipFile, ZipInfo

from ...pronunciation_attempts.adapters.ffmpeg_av_container import (
    FfmpegAvContainerAdapter,
)
from ...pronunciation_attempts.adapters.lip_audio_av_sync import (
    LipAudioAvSyncAdapter,
    LipAudioAvSyncPolicy,
)
from ...pronunciation_attempts.adapters.mediapipe_lip_landmarks import (
    MediaPipeLandmarkPolicy,
    MediaPipeLipLandmarkAdapter,
)
from ...pronunciation_attempts.adapters.network_isolated_exec import (
    isolated_command,
)
from ...pronunciation_attempts.adapters.restricted_local_media import (
    LocalRestrictedVideoAdmissionAdapter,
)
from ...pronunciation_attempts.adapters.video_qc import (
    VideoQcCandidateAdapter,
    VideoQcPolicy,
)
from ...pronunciation_attempts.models import (
    AvContainerPolicy,
    PronunciationAttemptMediaError,
    VideoAdmissionPolicy,
    VideoConsentAuthorization,
    VideoConsentDecision,
)
from ..models.aihub538_training_evidence import (
    Aihub538TrainingAdmissionReceipt,
)
from ..models.aihub538_nonlocked_shards import (
    AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS,
    AIHUB538_TS174_TL174_SHARD,
    Aihub538NonlockedShardSpec,
    require_aihub538_nonlocked_shard_spec,
)
from ..models.aihub538_balanced_cohort import (
    aihub538_label_selection_token_sha256,
)
from ..models.binding import MediaTimebaseReceipt, SameAttemptBindingReceipt
from ..models.integrity import sha256_document
from ..services.aihub538_decision_calibration import (
    Aihub538CalibrationMeasurement,
    Aihub538CalibrationMediaItem,
    Aihub538DecisionCalibrationError,
)
from .aihub538_admitted_archive_binding import (
    Aihub538AdmittedArchiveBinding,
    assert_aihub538_admitted_archive_binding,
    bind_aihub538_admitted_archive_descriptors,
    capture_aihub538_descriptor_identity,
    revoke_aihub538_admitted_archive_binding,
)
from .aihub538_training_archive import (
    _safe_zip_info,
    aihub538_archive_security_policy_for_shard,
)
from .aihub538_training_dataset import (
    _a_view_stem,
    _load_creator_json,
    _member_index,
    _parse_label_payload,
)


_CALIBRATION_PURPOSE = "restricted_dataset_calibration"
_CALIBRATION_DATA_CATEGORY = "facial_video"
_CALIBRATION_RETENTION_POLICY = "aihub538_ephemeral_calibration_v1"
_CALIBRATION_ISSUED_AT = datetime(2026, 8, 28, 0, 0, tzinfo=timezone.utc)
_CALIBRATION_ISSUED_AT_TEXT = "2026-08-28T00:00:00Z"
_MINIMUM_SENTENCE_SECONDS = 0.50
_MAXIMUM_SENTENCE_SECONDS = 20.0
_SENTENCE_PADDING_SECONDS = 0.10
_MAXIMUM_MUXED_BYTES = 256 * 1024 * 1024
_MUX_TIMEOUT_SECONDS = 180.0
_ISOLATION_LAUNCHER = Path(
    __file__
).parents[2] / "pronunciation_attempts/adapters/network_isolated_exec.py"
_LIVE_ACTUAL_MEDIA_ADAPTERS: WeakKeyDictionary[object, tuple[object, ...]] = WeakKeyDictionary()


class _PrivateItem:
    """Private member/timestamp binding kept out of dataclass repr/serialization."""

    __slots__ = (
        "public",
        "video_info",
        "audio_info",
        "sentence_start_seconds",
        "sentence_duration_seconds",
        "has_reversed_bbox_unavailable",
    )

    def __init__(
        self,
        *,
        public: Aihub538CalibrationMediaItem,
        video_info: ZipInfo,
        audio_info: ZipInfo,
        sentence_start_seconds: float,
        sentence_duration_seconds: float,
        has_reversed_bbox_unavailable: bool,
    ) -> None:
        self.public = public
        self.video_info = video_info
        self.audio_info = audio_info
        self.sentence_start_seconds = sentence_start_seconds
        self.sentence_duration_seconds = sentence_duration_seconds
        self.has_reversed_bbox_unavailable = has_reversed_bbox_unavailable


class Aihub538ActualMediaCalibrationAdapter:
    """Inventory and measure an admitted allowlisted non-locked shard pair."""

    def __init__(
        self,
        *,
        label_zip_descriptor: int,
        source_zip_descriptor: int,
        identity_hash_key: bytes,
        admission_receipt: Aihub538TrainingAdmissionReceipt,
        shard_spec: Aihub538NonlockedShardSpec = AIHUB538_TS174_TL174_SHARD,
        archive_binding: Aihub538AdmittedArchiveBinding | None = None,
        restricted_workspace_root: Path,
        landmark_model_path: Path,
        landmark_model_sha256: str,
        video_qc_policy: VideoQcPolicy,
        av_sync_policy: LipAudioAvSyncPolicy,
        container_policy: AvContainerPolicy,
        landmark_policy: MediaPipeLandmarkPolicy,
        visual_sample_frame_count: int = 8,
        ffmpeg_binary: str = "ffmpeg",
        ffprobe_binary: str = "ffprobe",
        repository_root: Path | None = None,
    ) -> None:
        if not isinstance(admission_receipt, Aihub538TrainingAdmissionReceipt):
            raise Aihub538DecisionCalibrationError(
                "aihub538_actual_media_admission_receipt_invalid"
            )
        try:
            self._shard_spec = require_aihub538_nonlocked_shard_spec(
                shard_spec,
                require_pair=True,
            )
        except ValueError as error:
            raise Aihub538DecisionCalibrationError(str(error)) from None
        if admission_receipt.shard_id != self._shard_spec.shard_id:
            raise Aihub538DecisionCalibrationError(
                "aihub538_actual_media_shard_receipt_mismatch"
            )
        self._archive_policy = aihub538_archive_security_policy_for_shard(
            self._shard_spec
        )
        if not isinstance(identity_hash_key, bytes) or not 32 <= len(
            identity_hash_key
        ) <= 128:
            raise Aihub538DecisionCalibrationError(
                "aihub538_actual_media_identity_key_invalid"
            )
        if (
            isinstance(visual_sample_frame_count, bool)
            or not isinstance(visual_sample_frame_count, int)
            or visual_sample_frame_count <= 0
            or visual_sample_frame_count > container_policy.maximum_rgb_sample_frames
        ):
            raise Aihub538DecisionCalibrationError(
                "aihub538_actual_media_frame_count_invalid"
            )
        self._identity_hash_key = identity_hash_key
        self._admission_receipt = admission_receipt
        self._workspace_root = restricted_workspace_root.absolute()
        self._repository_root = (
            repository_root.absolute()
            if repository_root is not None
            else Path(__file__).resolve().parents[4]
        )
        self._container_policy = container_policy
        self._visual_sample_frame_count = visual_sample_frame_count
        self._ffmpeg_binary = _absolute_binary(ffmpeg_binary)
        self._ffprobe_binary = _absolute_binary(ffprobe_binary)
        self._owns_archive_binding = archive_binding is None
        if archive_binding is None:
            # Standalone callers still establish freshness here by hashing and
            # CRC-scanning both complete ZIPs.  A serialized receipt never
            # substitutes for this validation boundary.
            self._archive_binding = bind_aihub538_admitted_archive_descriptors(
                label_zip_descriptor=label_zip_descriptor,
                source_zip_descriptor=source_zip_descriptor,
                admission_receipt=admission_receipt,
                policy=self._archive_policy,
            )
        else:
            # The authorized-run path may reuse only the live, process-local
            # capability issued while the same descriptors were validated.
            assert_aihub538_admitted_archive_binding(
                archive_binding,
                admission_receipt=admission_receipt,
                policy=self._archive_policy,
            )
            if (
                label_zip_descriptor != archive_binding.label.descriptor
                or source_zip_descriptor != archive_binding.source.descriptor
            ):
                raise Aihub538DecisionCalibrationError(
                    "aihub538_admitted_archive_binding_mismatch"
                )
            self._archive_binding = archive_binding
        self._label_file = None
        self._source_file = None
        try:
            self._label_file = _open_zip_file(label_zip_descriptor)
            self._source_file = _open_zip_file(source_zip_descriptor)
            self._assert_archive_descriptors_stable()
            self._label_zip = ZipFile(self._label_file, mode="r")
            self._source_zip = ZipFile(self._source_file, mode="r")
        except Aihub538DecisionCalibrationError:
            self._close_files()
            self._revoke_owned_archive_binding()
            raise
        except (BadZipFile, OSError):
            self._close_files()
            self._revoke_owned_archive_binding()
            raise Aihub538DecisionCalibrationError(
                "aihub538_actual_media_zip_invalid"
            ) from None
        self._private_items: dict[str, _PrivateItem] = {}
        # Capacity/cohort receipts address the label-only selection token,
        # while the training admission receipt addresses the full paired-media
        # binding. Keep both indexes so neither authority is weakened.
        self._private_by_label_selection_token: dict[str, _PrivateItem] = {}
        self._closed = False
        try:
            self._inventory = self._build_inventory()
            self._assert_archive_descriptors_stable()
            _ensure_private_directory(self._workspace_root)
            self._media_store = LocalRestrictedVideoAdmissionAdapter(
                restricted_root=self._workspace_root / "media-store",
                clock=lambda: _CALIBRATION_ISSUED_AT,
            )
            self._container = FfmpegAvContainerAdapter(
                self._media_store,
                ffprobe_binary=self._ffprobe_binary,
                ffmpeg_binary=self._ffmpeg_binary,
            )
            self._tracker = MediaPipeLipLandmarkAdapter(
                model_path=landmark_model_path,
                expected_model_sha256=landmark_model_sha256,
                policy=landmark_policy,
                repository_root=self._repository_root,
            )
            self._qc = VideoQcCandidateAdapter(policy=video_qc_policy)
            self._sync = LipAudioAvSyncAdapter(
                media_store=self._media_store,
                policy=av_sync_policy,
            )
            _LIVE_ACTUAL_MEDIA_ADAPTERS[self] = (
                os.getpid(), self._archive_binding, self._admission_receipt.receipt_sha256,
                tuple(item.sample_sha256 for item in self._inventory), ref(self),
            )
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> Aihub538ActualMediaCalibrationAdapter:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @property
    def inventory(self) -> tuple[Aihub538CalibrationMediaItem, ...]:
        """Return only HMAC-bound private rows consumed by the service."""

        if self._closed:
            raise Aihub538DecisionCalibrationError(
                "aihub538_actual_media_adapter_closed"
            )
        self._assert_archive_descriptors_stable()
        return self._inventory

    def measure(
        self, item: Aihub538CalibrationMediaItem
    ) -> Aihub538CalibrationMeasurement:
        """Crop, mux, decode, track, and measure one exact selected item."""

        if self._closed:
            raise Aihub538DecisionCalibrationError(
                "aihub538_actual_media_adapter_closed"
            )
        self._assert_archive_descriptors_stable()
        try:
            return self._measure_bound_item(item)
        finally:
            # A source archive mutation during extraction/decode invalidates
            # the measurement even when the runtime adapters otherwise pass.
            self._assert_archive_descriptors_stable()

    def _measure_bound_item(
        self, item: Aihub538CalibrationMediaItem
    ) -> Aihub538CalibrationMeasurement:
        private = self._private_items.get(item.sample_sha256)
        if private is None or private.public != item:
            raise Aihub538DecisionCalibrationError(
                "aihub538_actual_media_item_unknown"
            )
        if private.has_reversed_bbox_unavailable:
            raise Aihub538DecisionCalibrationError(
                "aihub538_actual_media_metadata_exclusion_contract_violation"
            )
        asset = None
        body_error: BaseException | None = None
        with tempfile.TemporaryDirectory(
            prefix="measure-", dir=self._workspace_root
        ) as temporary_name:
            temporary_root = Path(temporary_name)
            os.chmod(temporary_root, 0o700)
            muxed_path = self._materialize_muxed_clip(
                private=private,
                temporary_root=temporary_root,
            )
            muxed_descriptor = _open_read_only_regular(muxed_path)
            try:
                authorization = _dataset_authorization(item)
                asset = self._media_store.admit(
                    f"fd:{muxed_descriptor}",
                    attempt_id_sha256=item.attempt_token_sha256,
                    authorization=authorization,
                    policy=VideoAdmissionPolicy(
                        maximum_source_bytes=_MAXIMUM_MUXED_BYTES,
                        required_purpose=_CALIBRATION_PURPOSE,
                        required_data_category=_CALIBRATION_DATA_CATEGORY,
                        allowed_retention_policy_ids=(
                            _CALIBRATION_RETENTION_POLICY,
                        ),
                    ),
                )
            finally:
                os.close(muxed_descriptor)
            try:
                probe = self._container.probe(asset, self._container_policy)
                sample = self._container.decode_rgb24_sample(
                    asset,
                    self._container_policy,
                    frame_count=self._visual_sample_frame_count,
                )
                landmarks = self._tracker.detect(sample)
                qc_candidate = self._qc.measure(
                    sample=sample,
                    landmarks=landmarks,
                )
                audio = self._container.extract_embedded_audio(
                    asset,
                    self._container_policy,
                )
                sync_candidate = self._sync.measure(
                    audio=audio,
                    landmarks=landmarks,
                    probe=probe,
                )
                binding = _calibration_binding(
                    item=item,
                    authorization=authorization,
                    probe=probe,
                    audio=audio,
                )
                result = Aihub538CalibrationMeasurement(
                    item=item,
                    binding_receipt=binding,
                    video_qc_candidate=qc_candidate,
                    av_sync_candidate=sync_candidate,
                )
            except BaseException as error:
                body_error = error
                result = None
            finally:
                try:
                    self._media_store.delete(asset)
                except BaseException:
                    raise Aihub538DecisionCalibrationError(
                        "aihub538_actual_media_cleanup_failed"
                    ) from None
            if body_error is not None:
                if isinstance(body_error, Aihub538DecisionCalibrationError):
                    raise body_error
                if isinstance(body_error, PronunciationAttemptMediaError):
                    raise Aihub538DecisionCalibrationError(
                        f"aihub538_actual_media_{body_error.code.value}"
                    ) from None
                reason = getattr(body_error, "reason_code", None)
                if isinstance(reason, str) and reason.isascii():
                    raise Aihub538DecisionCalibrationError(
                        f"aihub538_actual_media_{reason}"
                    ) from None
                raise Aihub538DecisionCalibrationError(
                    "aihub538_actual_media_measurement_failed"
                ) from None
            assert result is not None
            return result

    def close(self) -> None:
        if getattr(self, "_closed", False):
            return
        self._closed = True
        _LIVE_ACTUAL_MEDIA_ADAPTERS.pop(self, None)
        label_zip = getattr(self, "_label_zip", None)
        source_zip = getattr(self, "_source_zip", None)
        if label_zip is not None:
            label_zip.close()
        if source_zip is not None:
            source_zip.close()
        self._close_files()
        self._revoke_owned_archive_binding()

    def _revoke_owned_archive_binding(self) -> None:
        if getattr(self, "_owns_archive_binding", False):
            binding = getattr(self, "_archive_binding", None)
            if binding is not None:
                revoke_aihub538_admitted_archive_binding(binding)
            self._owns_archive_binding = False

    def _close_files(self) -> None:
        for name in ("_label_file", "_source_file"):
            file_object = getattr(self, name, None)
            if file_object is not None and not file_object.closed:
                file_object.close()

    def _assert_archive_descriptors_stable(self) -> None:
        binding = self._archive_binding
        assert_aihub538_admitted_archive_binding(
            binding,
            admission_receipt=self._admission_receipt,
            policy=self._archive_policy,
        )
        for file_object, stable in (
            (self._label_file, binding.label),
            (self._source_file, binding.source),
        ):
            if file_object is None or file_object.closed:
                raise Aihub538DecisionCalibrationError(
                    "aihub538_archive_descriptor_changed"
                )
            duplicate_identity = capture_aihub538_descriptor_identity(
                file_object.fileno()
            )
            if duplicate_identity != stable.identity:
                raise Aihub538DecisionCalibrationError(
                    "aihub538_archive_descriptor_changed"
                )

    def _build_inventory(self) -> tuple[Aihub538CalibrationMediaItem, ...]:
        policy = self._archive_policy
        label_infos = tuple(self._label_zip.infolist())
        source_infos = tuple(self._source_zip.infolist())
        label_names = tuple(_safe_zip_info(info, policy=policy) for info in label_infos)
        source_names = tuple(
            _safe_zip_info(info, policy=policy) for info in source_infos
        )
        label_index = _member_index(
            infos=label_infos,
            names=label_names,
            role_component=self._shard_spec.label_role_component,
            allowed_suffixes={".json"},
        )
        source_index = _member_index(
            infos=source_infos,
            names=source_names,
            role_component=self._shard_spec.source_role_component,
            allowed_suffixes={".mp4", ".wav"},
        )
        public_items: list[Aihub538CalibrationMediaItem] = []
        for label_key in sorted(label_index.members):
            try:
                label_payload = self._label_zip.read(label_index.members[label_key])
                private_label = _parse_label_payload(
                    label_payload,
                    allow_bounded_reversed_bbox_unavailable=(
                        self._shard_spec
                        in AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS
                    ),
                    allow_label_capacity_terminal_frame_tail=(
                        self._shard_spec
                        in AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS[2:]
                    ),
                )
                document = _load_creator_json(label_payload)
                record = document[0]
                assert isinstance(record, dict)
                sentences = record["Sentence_info"]
                assert isinstance(sentences, list)
            except (AssertionError, BadZipFile, KeyError, OSError, RuntimeError):
                raise Aihub538DecisionCalibrationError(
                    "aihub538_actual_media_label_invalid"
                ) from None
            label_relative = PurePosixPath(label_key)
            video_key = label_relative.with_suffix(".mp4").as_posix()
            a_audio_stem = _a_view_stem(private_label.session_stem)
            audio_key = label_relative.with_name(f"{a_audio_stem}.wav").as_posix()
            try:
                video_info = source_index.members[video_key]
                audio_info = source_index.members[audio_key]
            except KeyError:
                raise Aihub538DecisionCalibrationError(
                    "aihub538_actual_media_pairing_failed"
                ) from None
            private_relative_key_sha256 = hashlib.sha256(
                b"voice_coach.aihub538.private_relative_key.v1\x00"
                + label_key.encode("utf-8")
            ).hexdigest()
            label_selection_token_sha256 = aihub538_label_selection_token_sha256(
                private_relative_key_sha256=private_relative_key_sha256,
                label_payload_sha256=private_label.label_payload_sha256,
                label_archive_sha256=self._admission_receipt.label_archive_sha256,
                angle=private_label.angle,
            )
            sample_sha256 = sha256_document(
                {
                    "schema_version": "voice_coach.aihub538_training_sample_binding.v1",
                    "private_relative_key_sha256": private_relative_key_sha256,
                    "label_payload_sha256": private_label.label_payload_sha256,
                    "source_archive_sha256": self._admission_receipt.source_archive_sha256,
                    "video_size_bytes": video_info.file_size,
                    "video_crc32": video_info.CRC,
                    "audio_size_bytes": audio_info.file_size,
                    "audio_crc32": audio_info.CRC,
                    "angle": private_label.angle,
                }
            )
            has_reversed_bbox_unavailable = bool(
                private_label.face_reversed_bbox_unavailable_count
                or private_label.lip_reversed_bbox_unavailable_count
            )
            if has_reversed_bbox_unavailable:
                # This item remains in admission and actor/attempt lineage, but
                # the frozen multi-shard protocol excludes it before cohort
                # selection.  Do not let a sentence-window defect in an item
                # that cannot be measured abort inventory construction.
                sentence_start, sentence_duration = 0.0, 0.0
            else:
                sentence_start, sentence_duration = _sentence_window(
                    sentences=sentences,
                    sample_sha256=sample_sha256,
                    media_duration_seconds=min(
                        private_label.video_duration_seconds,
                        private_label.audio_duration_seconds,
                    ),
                )
            public = Aihub538CalibrationMediaItem(
                sample_sha256=sample_sha256,
                actor_token_sha256=_identity_hmac(
                    self._identity_hash_key,
                    b"voice_coach.aihub538.speaker.v1\x00",
                    private_label.speaker_id,
                ),
                attempt_token_sha256=_identity_hmac(
                    self._identity_hash_key,
                    b"voice_coach.aihub538.session.v1\x00",
                    a_audio_stem,
                ),
                angle=private_label.angle,
                metadata_bbox_observation_available=(
                    not has_reversed_bbox_unavailable
                ),
                label_selection_token_sha256=(
                    label_selection_token_sha256
                ),
            )
            if sample_sha256 in self._private_items:
                raise Aihub538DecisionCalibrationError(
                    "aihub538_actual_media_sample_duplicate"
                )
            private_item = _PrivateItem(
                public=public,
                video_info=video_info,
                audio_info=audio_info,
                sentence_start_seconds=sentence_start,
                sentence_duration_seconds=sentence_duration,
                has_reversed_bbox_unavailable=has_reversed_bbox_unavailable,
            )
            self._private_items[sample_sha256] = private_item
            self._private_by_label_selection_token[
                label_selection_token_sha256
            ] = private_item
            public_items.append(public)
        public_items.sort(key=lambda item: item.sample_sha256)
        selection_sha256 = sha256_document(
            {
                "schema_version": "voice_coach.aihub538_training_sample_selection.v1",
                "selection_rule": self._admission_receipt.sample_selection_rule,
                "admission_policy_sha256": (
                    self._admission_receipt.admission_policy_sha256
                ),
                "sample_sha256": [item.sample_sha256 for item in public_items],
            }
        )
        if (
            selection_sha256 != self._admission_receipt.sample_selection_sha256
            or len(public_items) != self._admission_receipt.paired_sample_count
        ):
            raise Aihub538DecisionCalibrationError(
                "aihub538_actual_media_inventory_admission_mismatch"
            )
        return tuple(public_items)

    def _materialize_muxed_clip(
        self,
        *,
        private: _PrivateItem,
        temporary_root: Path,
    ) -> Path:
        video_path = temporary_root / "source-video"
        audio_path = temporary_root / "source-audio"
        output_path = temporary_root / "runtime-envelope.mp4"
        _extract_member(self._source_zip, private.video_info, video_path)
        _extract_member(self._source_zip, private.audio_info, audio_path)
        video_descriptor = _open_read_only_regular(video_path)
        audio_descriptor = _open_read_only_regular(audio_path)
        output_descriptor = os.open(
            output_path,
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            _run_mux(
                ffmpeg_binary=self._ffmpeg_binary,
                video_descriptor=video_descriptor,
                audio_descriptor=audio_descriptor,
                output_descriptor=output_descriptor,
                start_seconds=private.sentence_start_seconds,
                duration_seconds=private.sentence_duration_seconds,
                repository_root=self._repository_root,
            )
            metadata = os.fstat(output_descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or not 0 < metadata.st_size <= _MAXIMUM_MUXED_BYTES
            ):
                raise Aihub538DecisionCalibrationError(
                    "aihub538_actual_media_mux_size_invalid"
                )
            os.fsync(output_descriptor)
            os.fchmod(output_descriptor, 0o400)
        finally:
            os.close(video_descriptor)
            os.close(audio_descriptor)
            os.close(output_descriptor)
        return output_path

    @contextmanager
    def _open_photometric_source_clip(
        self,
        *,
        label_selection_token_sha256: str,
    ):
        """Yield one policy-sized derived clip without exposing private keys.

        This is intentionally a private boundary used by the P6 source
        admission capability.  The opaque label-selection token is resolved
        inside this adapter; member names and sentence timestamps never cross
        the boundary.
        """

        if self._closed:
            raise Aihub538DecisionCalibrationError(
                "aihub538_actual_media_adapter_closed"
            )
        self._assert_archive_descriptors_stable()
        private = self._private_by_label_selection_token.get(
            label_selection_token_sha256
        )
        if private is None or private.has_reversed_bbox_unavailable:
            raise Aihub538DecisionCalibrationError(
                "aihub538_actual_media_photometric_source_unknown"
            )
        try:
            with tempfile.TemporaryDirectory(
                prefix="photometric-source-", dir=self._workspace_root
            ) as temporary_name:
                temporary_root = Path(temporary_name)
                os.chmod(temporary_root, 0o700)
                clip_path = self._materialize_muxed_clip(
                    private=private,
                    temporary_root=temporary_root,
                )
                descriptor = _open_read_only_regular(clip_path)
                try:
                    yield descriptor
                finally:
                    os.close(descriptor)
        finally:
            self._assert_archive_descriptors_stable()


def assert_aihub538_live_actual_media_adapter(value: object) -> None:
    issued = _LIVE_ACTUAL_MEDIA_ADAPTERS.get(value)
    if (
        type(value) is not Aihub538ActualMediaCalibrationAdapter
        or issued is None
        or issued[0] != os.getpid()
        or issued[1] is not value._archive_binding
        or issued[2] != value._admission_receipt.receipt_sha256
        or issued[3] != tuple(item.sample_sha256 for item in value.inventory)
        or issued[4]() is not value
    ):
        raise Aihub538DecisionCalibrationError(
            "aihub538_actual_media_adapter_capability_invalid"
        )
    value._assert_archive_descriptors_stable()


def _sentence_window(
    *,
    sentences: list[object],
    sample_sha256: str,
    media_duration_seconds: float,
) -> tuple[float, float]:
    candidates: list[tuple[float, float]] = []
    for sentence in sentences:
        if not isinstance(sentence, dict):
            continue
        try:
            start = float(sentence["start_time"])
            end = float(sentence["end_time"])
        except (KeyError, TypeError, ValueError):
            continue
        duration = end - start
        if (
            start >= 0.0
            and end <= media_duration_seconds + 0.25
            and _MINIMUM_SENTENCE_SECONDS <= duration <= _MAXIMUM_SENTENCE_SECONDS
        ):
            padded_start = max(0.0, start - _SENTENCE_PADDING_SECONDS)
            padded_end = min(
                media_duration_seconds,
                end + _SENTENCE_PADDING_SECONDS,
            )
            candidates.append((padded_start, padded_end - padded_start))
    if not candidates:
        raise Aihub538DecisionCalibrationError(
            "aihub538_actual_media_sentence_window_unavailable"
        )
    index = int(
        hashlib.sha256(
            b"voice_coach.aihub538.sentence_window.v1\x00"
            + bytes.fromhex(sample_sha256)
        ).hexdigest(),
        16,
    ) % len(candidates)
    return candidates[index]


def _dataset_authorization(
    item: Aihub538CalibrationMediaItem,
) -> VideoConsentAuthorization:
    return VideoConsentAuthorization(
        attempt_id_sha256=item.attempt_token_sha256,
        consent_receipt_sha256=sha256_document(
            {
                "schema_version": "voice_coach.aihub538_dataset_media_authorization.v1",
                "sample_binding_sha256": item.sample_sha256,
                "purpose": _CALIBRATION_PURPOSE,
                "delete_on_completion": True,
                "remote_egress_allowed": False,
            }
        ),
        decision=VideoConsentDecision.GRANTED,
        purpose=_CALIBRATION_PURPOSE,
        data_category=_CALIBRATION_DATA_CATEGORY,
        retention_policy_id=_CALIBRATION_RETENTION_POLICY,
        delete_on_completion=True,
        remote_egress_allowed=False,
        issued_at_utc=_CALIBRATION_ISSUED_AT - timedelta(days=1),
        expires_at_utc=_CALIBRATION_ISSUED_AT + timedelta(days=1),
    )


def _calibration_binding(*, item, authorization, probe, audio):  # noqa: ANN001
    audio_timebase = MediaTimebaseReceipt(
        clock_id_sha256=sha256_document(
            {"source": audio.derivation_receipt.pcm_sha256, "clock": "audio"}
        ),
        duration_ms=probe.audio_stream.duration_ms,
        origin_offset_ms=round(probe.audio_stream.start_time_us / 1_000),
        transform_revision="ffmpeg_pcm_s16le_16khz_v1",
    )
    video_timebase = MediaTimebaseReceipt(
        clock_id_sha256=sha256_document(
            {"source": probe.container_sha256, "clock": "video"}
        ),
        duration_ms=probe.video_stream.duration_ms,
        origin_offset_ms=round(probe.video_stream.start_time_us / 1_000),
        transform_revision="ffprobe_best_effort_timestamp_v1",
    )
    return SameAttemptBindingReceipt.issue(
        attempt_id_sha256=item.attempt_token_sha256,
        prompt_id_sha256=sha256_document(
            {"dataset": "aihub_538", "role": "calibration_sentence_window"}
        ),
        prompt_revision="aihub538_calibration_window_v1",
        expected_phone_sha256=sha256_document(
            {"expected_phone_role": "not_used_for_qc_sync_calibration"}
        ),
        analysis_sha256=sha256_document(
            {"sample_binding_sha256": item.sample_sha256}
        ),
        audio_source_sha256=audio.derivation_receipt.pcm_sha256,
        audio_derivation_receipt_sha256=(
            audio.derivation_receipt.receipt_sha256
        ),
        video_source_sha256=probe.container_sha256,
        audio_timebase=audio_timebase,
        video_timebase=video_timebase,
        upstream_reliability_receipt_sha256=sha256_document(
            {"scope": "dataset_qc_sync_calibration_not_seungun_inference"}
        ),
        consent_receipt_sha256=authorization.consent_receipt_sha256,
        issued_at_utc=_CALIBRATION_ISSUED_AT_TEXT,
    )


def _run_mux(
    *,
    ffmpeg_binary: str,
    video_descriptor: int,
    audio_descriptor: int,
    output_descriptor: int,
    start_seconds: float,
    duration_seconds: float,
    repository_root: Path,
) -> None:
    target = [
        ffmpeg_binary,
        "-v",
        "error",
        "-nostdin",
        "-y",
        "-protocol_whitelist",
        "file,pipe",
        "-ss",
        f"{start_seconds:.6f}",
        "-t",
        f"{duration_seconds:.6f}",
        "-i",
        f"/proc/self/fd/{video_descriptor}",
        "-ss",
        f"{start_seconds:.6f}",
        "-t",
        f"{duration_seconds:.6f}",
        "-i",
        f"/proc/self/fd/{audio_descriptor}",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-map_metadata",
        "-1",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-color_range",
        "tv",
        "-colorspace",
        "bt709",
        "-color_trc",
        "bt709",
        "-color_primaries",
        "bt709",
        "-threads",
        "1",
        "-c:a",
        "aac",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-b:a",
        "96k",
        "-shortest",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        f"/proc/self/fd/{output_descriptor}",
    ]
    command = isolated_command(
        launcher_python=sys.executable,
        launcher_path=str(_ISOLATION_LAUNCHER),
        target=target,
        cpu_seconds=math_ceil(_MUX_TIMEOUT_SECONDS) + 2,
        address_space_bytes=6 * 1024 * 1024 * 1024,
    )
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(video_descriptor, audio_descriptor, output_descriptor),
            close_fds=True,
            shell=False,
            cwd=repository_root,
            env={
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
                "PYTHONHASHSEED": "0",
            },
            start_new_session=True,
        )
    except (OSError, ValueError):
        raise Aihub538DecisionCalibrationError(
            "aihub538_actual_media_mux_backend_unavailable"
        ) from None
    try:
        return_code = process.wait(timeout=_MUX_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _kill_process_group(process)
        raise Aihub538DecisionCalibrationError(
            "aihub538_actual_media_mux_timeout"
        ) from None
    if return_code != 0:
        raise Aihub538DecisionCalibrationError(
            "aihub538_actual_media_mux_failed"
        )


def _extract_member(zip_file: ZipFile, info: ZipInfo, destination: Path) -> None:
    descriptor = os.open(
        destination,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    observed = 0
    try:
        with zip_file.open(info, "r") as source:
            while chunk := source.read(4 * 1024 * 1024):
                _write_all(descriptor, chunk)
                observed += len(chunk)
        if observed != info.file_size:
            raise Aihub538DecisionCalibrationError(
                "aihub538_actual_media_member_size_mismatch"
            )
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o400)
    except (BadZipFile, OSError, RuntimeError):
        raise Aihub538DecisionCalibrationError(
            "aihub538_actual_media_member_read_failed"
        ) from None
    finally:
        os.close(descriptor)


def _open_zip_file(descriptor: int) -> BinaryIO:
    duplicated = _duplicate_read_only_regular(descriptor)
    return os.fdopen(duplicated, "rb", closefd=True)


def _duplicate_read_only_regular(descriptor: int) -> int:
    if isinstance(descriptor, bool) or not isinstance(descriptor, int) or descriptor < 3:
        raise Aihub538DecisionCalibrationError(
            "aihub538_actual_media_descriptor_invalid"
        )
    try:
        duplicate = os.dup(descriptor)
        os.set_inheritable(duplicate, False)
        flags = fcntl.fcntl(duplicate, fcntl.F_GETFL)
        metadata = os.fstat(duplicate)
        if (
            flags & os.O_ACCMODE != os.O_RDONLY
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
        ):
            raise OSError
        os.lseek(duplicate, 0, os.SEEK_SET)
        return duplicate
    except OSError:
        try:
            os.close(duplicate)
        except (OSError, UnboundLocalError):
            pass
        raise Aihub538DecisionCalibrationError(
            "aihub538_actual_media_descriptor_invalid"
        ) from None


def _open_read_only_regular(path: Path) -> int:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW,
        )
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError
        return descriptor
    except OSError:
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass
        raise Aihub538DecisionCalibrationError(
            "aihub538_actual_media_private_file_invalid"
        ) from None


def _ensure_private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=False, exist_ok=True)
        metadata = path.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise OSError
    except OSError:
        raise Aihub538DecisionCalibrationError(
            "aihub538_actual_media_workspace_invalid"
        ) from None


def _identity_hmac(key: bytes, domain: bytes, value: str) -> str:
    return hmac.new(key, domain + value.encode("utf-8"), hashlib.sha256).hexdigest()


def _absolute_binary(value: str) -> str:
    resolved = shutil.which(value)
    if resolved is None:
        raise Aihub538DecisionCalibrationError(
            "aihub538_actual_media_backend_unavailable"
        )
    return str(Path(resolved).resolve())


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise Aihub538DecisionCalibrationError(
                "aihub538_actual_media_private_write_failed"
            )
        view = view[written:]


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        pass


def math_ceil(value: float) -> int:
    integer = int(value)
    return integer if value == integer else integer + 1


__all__ = ["Aihub538ActualMediaCalibrationAdapter", "assert_aihub538_live_actual_media_adapter"]
