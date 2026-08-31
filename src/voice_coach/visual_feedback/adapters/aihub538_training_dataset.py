"""Exact-pairing admission for the AI Hub 538 TS174/TL174 smoke shard.

The adapter follows the public OLKAVS creator contract: each label JSON is an
array whose first and only item is the record, TL174 maps to TS174 at the same
relative stem, non-A views reuse the A-view WAV, sentence windows come from
``Sentence_info``, and lip boxes come from
``Bounding_box_info.Lip_bounding_box.xtl_ytl_xbr_ybr``.

Restricted names, paths, transcripts, and speaker identifiers are discarded
before this adapter returns.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import math
import os
from pathlib import PurePosixPath
import re
from weakref import WeakKeyDictionary, ref
from zipfile import BadZipFile, ZipFile, ZipInfo

from .aihub538_training_archive import (
    Aihub538ArchiveError,
    Aihub538ArchiveSecurityPolicy,
    _safe_zip_info,
    aihub538_archive_security_policy_for_shard,
    open_validated_zip_descriptor,
)
from .aihub538_admitted_archive_binding import (
    Aihub538AdmittedArchiveBinding,
    assert_aihub538_admitted_archive_binding,
    bind_aihub538_legacy_ts174_replay_aggregate,
)
from ..aihub538_capability_lifetime import (
    AIHUB538_CAPABILITY_LIFETIME_LOCK,
)
from ..models.aihub538_training_evidence import (
    AIHUB538_TRAINING_ANGLES,
    Aihub538TrainingAdmissionAggregate,
    Aihub538TrainingAdmissionReceipt,
    Aihub538TrainingAngleAggregate,
    Aihub538RestrictedIdentitySet,
    Aihub538RestrictedMemberSet,
    Aihub538TrainingSampleAggregate,
    aihub538_shard_admission_policy_sha256,
)
from ..models.aihub538_nonlocked_shards import (
    AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS,
    AIHUB538_TS174_TL174_SHARD,
    Aihub538NonlockedShardSpec,
    require_aihub538_nonlocked_shard_spec,
)
from ..models.aihub538_bbox_observation import (
    AIHUB538_BOUNDED_REVERSED_BBOX_OBSERVATION_POLICY_SHA256,
)
from ..models.aihub538_balanced_cohort import (
    aihub538_label_selection_token_sha256,
)
from ..models.aihub538_label_capacity import (
    AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY,
    AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256,
)
from ..models.integrity import sha256_document


_MAXIMUM_LABEL_BYTES = 32 * 1024 * 1024
_MAXIMUM_SENTENCE_COUNT = 10_000
_MAXIMUM_TRANSCRIPT_CHARACTERS = 100_000
_MAXIMUM_FRAME_COUNT = 100_000
_MAXIMUM_DURATION_SECONDS = 3_600.0
_MAXIMUM_FRAME_RATE = 240.0
_FRAME_COUNT_ABSOLUTE_TOLERANCE = 2
_FRAME_COUNT_RELATIVE_TOLERANCE = 0.01
# Actual TS174/TL174 sentence endpoints can exceed the shorter rounded media
# duration metadata by up to 0.355 s.  The pinned creator preprocess slices
# NumPy arrays and therefore naturally clips such sub-second tail overrun.  We
# admit at most 0.5 s here; actual calibration still decodes bounded windows and
# independently applies its stricter runtime policy.
_SENTENCE_OVERLAP_TOLERANCE_SECONDS = 0.5

# The creator name rule uses J/K in the media stem, while the official AI Hub
# JSON example and the admitted TS174/TL174 shard use a separate categorical
# label such as ``indoor_light``.  They are independently validated because an
# actual K-stem record may still carry ``indoor_light``; treating the two fields
# as aliases would reject the published corpus rather than detect corruption.
_VIDEO_ENVIRONMENT_CATEGORIES = frozenset(
    {"indoor_light", "outdoor_light"}
)

_SESSION_STEM_PATTERN = re.compile(
    r"^lip_(?P<environment>[JK])_(?P<noise>[1-6])_"
    r"(?P<gender>[FM])_(?P<age>[0-9]{2})_"
    r"(?P<specificity>[CE])(?P<speaker>[0-9]+)_"
    r"(?P<angle>[A-I])_(?P<session>[0-9]{3})$"
)
_DURATION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")
_RESOLUTION_PATTERN = re.compile(
    r"^\s*\[?\s*(?P<width>[0-9]+)\s*[,xX*]\s*"
    r"(?P<height>[0-9]+)\s*\]?\s*$"
)

_TOP_LEVEL_KEYS = {
    "Audio_env",
    "Audio_info",
    "Bounding_box_info",
    "Sentence_info",
    "Video_env",
    "Video_info",
    "dataSet",
    "speaker_info",
}
_DATASET_KEYS = {"description", "url", "version", "year"}
_VIDEO_INFO_KEYS = {
    "FPS",
    "Resolution",
    "video_Duration",
    "video_Format",
    "video_Name",
}
_AUDIO_INFO_KEYS = {
    "Audio_Duration",
    "Audio_Format",
    "Audio_Name",
    "Channel(s)",
    "Sampling_rate",
}
_AUDIO_ENV_KEYS = {"Noise"}
_VIDEO_ENV_KEYS = {"Angle", "env"}
_SENTENCE_KEYS = {"ID", "end_time", "sentence_text", "start_time", "topic"}
_SPEAKER_KEYS = {"Accent", "Age", "Gender", "Specificity", "speaker_ID"}
_BOUNDING_BOX_KEYS = {"Face_bounding_box", "Lip_bounding_box"}
_BOX_SEQUENCE_KEYS = {"xtl_ytl_xbr_ybr"}


class Aihub538TrainingAdmissionError(RuntimeError):
    """Fail-closed error that never embeds a restricted source value."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class _DuplicateJsonKeyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _PrivateMemberIndex:
    members: dict[str, ZipInfo]


@dataclass(frozen=True, slots=True)
class _PrivateLabel:
    label_payload_sha256: str
    session_stem: str
    environment: str
    noise_code: int
    gender: str
    age_code: int
    specificity: str
    speaker_id: str
    angle: str
    video_name: str
    audio_name: str
    video_duration_seconds: float
    audio_duration_seconds: float
    frames_per_second: float
    width: int
    height: int
    lip_bbox_frame_count: int
    face_bbox_frame_count: int
    lip_reversed_bbox_unavailable_count: int
    face_reversed_bbox_unavailable_count: int
    sentence_count: int
    sentence_duration_seconds: float
    terminal_media_overrun: bool
    terminal_frame_tail_exception_applied: bool
    terminal_complete_tail_frames: int


class _PrevalidatedAggregateCapabilityToken:
    __slots__ = ("__weakref__",)


_PREVALIDATED_AGGREGATE_CAPABILITIES: WeakKeyDictionary[
    _PrevalidatedAggregateCapabilityToken,
    tuple[object, ...],
] = WeakKeyDictionary()


@dataclass(frozen=True, slots=True, weakref_slot=True)
class Aihub538PrevalidatedTrainingAdmission:
    """Opaque exact aggregate issued only by full dataset admission."""

    aggregate: Aihub538TrainingAdmissionAggregate
    archive_binding: Aihub538AdmittedArchiveBinding = field(repr=False)
    admission_receipt: Aihub538TrainingAdmissionReceipt = field(repr=False)
    archive_policy: Aihub538ArchiveSecurityPolicy = field(repr=False)
    _capability: _PrevalidatedAggregateCapabilityToken = field(
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not self._capability_matches():
            raise ValueError(
                "aihub538_prevalidated_aggregate_capability_invalid"
            )

    def _capability_matches(self) -> bool:
        with AIHUB538_CAPABILITY_LIFETIME_LOCK:
            try:
                record = _PREVALIDATED_AGGREGATE_CAPABILITIES.get(
                    self._capability
                )
            except (AttributeError, TypeError):
                return False
            return bool(
                record is not None
                and record[0] == os.getpid()
                and (record[1] is None or record[1]() is self)
                and record[2] is self.aggregate
                and record[3] is self.archive_binding
                and record[4] is self.admission_receipt
                and record[5] == self.archive_policy
            )

    def assert_authorizable(
        self,
        *,
        archive_binding: Aihub538AdmittedArchiveBinding,
        admission_receipt: Aihub538TrainingAdmissionReceipt,
        archive_policy: Aihub538ArchiveSecurityPolicy,
    ) -> Aihub538TrainingAdmissionAggregate:
        with AIHUB538_CAPABILITY_LIFETIME_LOCK:
            return self._assert_authorizable_locked(
                archive_binding=archive_binding,
                admission_receipt=admission_receipt,
                archive_policy=archive_policy,
            )

    def _assert_authorizable_locked(
        self,
        *,
        archive_binding: Aihub538AdmittedArchiveBinding,
        admission_receipt: Aihub538TrainingAdmissionReceipt,
        archive_policy: Aihub538ArchiveSecurityPolicy,
    ) -> Aihub538TrainingAdmissionAggregate:
        if (
            not self._capability_matches()
            or self.archive_binding is not archive_binding
            or self.admission_receipt is not admission_receipt
            or self.archive_policy != archive_policy
        ):
            raise Aihub538TrainingAdmissionError(
                "aihub538_prevalidated_aggregate_capability_invalid"
            )
        assert_aihub538_admitted_archive_binding(
            archive_binding,
            admission_receipt=admission_receipt,
            policy=archive_policy,
        )
        try:
            replayed = Aihub538TrainingAdmissionReceipt.issue(
                self.aggregate
            )
        except (TypeError, ValueError):
            replayed = None
        if replayed != admission_receipt:
            raise Aihub538TrainingAdmissionError(
                "aihub538_prevalidated_aggregate_capability_invalid"
            )
        return self.aggregate

    def bind_legacy_ts174_replay(
        self,
    ) -> "Aihub538PrevalidatedTrainingAdmission":
        with AIHUB538_CAPABILITY_LIFETIME_LOCK:
            return self._bind_legacy_ts174_replay_locked()

    def _bind_legacy_ts174_replay_locked(
        self,
    ) -> "Aihub538PrevalidatedTrainingAdmission":
        if not self._capability_matches():
            raise Aihub538TrainingAdmissionError(
                "aihub538_prevalidated_aggregate_capability_invalid"
            )
        record = _PREVALIDATED_AGGREGATE_CAPABILITIES[self._capability]
        if record[6] is True:
            raise Aihub538TrainingAdmissionError(
                "aihub538_prevalidated_legacy_replay_already_issued"
            )
        legacy = bind_aihub538_legacy_ts174_replay_aggregate(
            current_aggregate=self.aggregate,
            legacy_receipt=self.admission_receipt,
        )
        assert_aihub538_admitted_archive_binding(
            self.archive_binding,
            admission_receipt=self.admission_receipt,
            policy=self.archive_policy,
        )
        capability = _PrevalidatedAggregateCapabilityToken()
        derived_record = (
            os.getpid(),
            None,
            legacy,
            self.archive_binding,
            self.admission_receipt,
            self.archive_policy,
            True,
        )
        _PREVALIDATED_AGGREGATE_CAPABILITIES[capability] = derived_record
        try:
            derived = Aihub538PrevalidatedTrainingAdmission(
                aggregate=legacy,
                archive_binding=self.archive_binding,
                admission_receipt=self.admission_receipt,
                archive_policy=self.archive_policy,
                _capability=capability,
            )
            _PREVALIDATED_AGGREGATE_CAPABILITIES[capability] = (
                derived_record[0],
                ref(derived),
                *derived_record[2:],
            )
        except BaseException:
            _PREVALIDATED_AGGREGATE_CAPABILITIES.pop(capability, None)
            raise
        _PREVALIDATED_AGGREGATE_CAPABILITIES[self._capability] = (
            *record[:6],
            True,
        )
        return derived


def revoke_aihub538_prevalidated_training_admissions(
    archive_binding: Aihub538AdmittedArchiveBinding,
) -> None:
    with AIHUB538_CAPABILITY_LIFETIME_LOCK:
        for capability, record in tuple(
            _PREVALIDATED_AGGREGATE_CAPABILITIES.items()
        ):
            if record[3] is archive_binding:
                _PREVALIDATED_AGGREGATE_CAPABILITIES.pop(capability, None)


class Aihub538TrainingDatasetAdapter:
    """Admit two read-only merged ZIP descriptors with exact media binding."""

    def __init__(
        self,
        *,
        shard_spec: Aihub538NonlockedShardSpec = AIHUB538_TS174_TL174_SHARD,
        archive_policy: Aihub538ArchiveSecurityPolicy | None = None,
        nonlocked_label_schema_policy_sha256: str | None = None,
    ) -> None:
        try:
            self._shard_spec = require_aihub538_nonlocked_shard_spec(
                shard_spec,
                require_pair=True,
            )
        except ValueError as error:
            raise Aihub538TrainingAdmissionError(str(error)) from None
        self._archive_policy = (
            aihub538_archive_security_policy_for_shard(self._shard_spec)
            if archive_policy is None
            else archive_policy
        )
        self._uses_nonlocked_label_schema = self._shard_spec in (
            AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS[2:]
        )
        if self._uses_nonlocked_label_schema and (
            nonlocked_label_schema_policy_sha256
            != AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256
        ):
            raise Aihub538TrainingAdmissionError(
                "aihub538_nonlocked_label_schema_policy_required"
            )

    def admit(
        self,
        *,
        label_zip_descriptor: int,
        source_zip_descriptor: int,
        identity_hash_key: bytes,
    ) -> Aihub538TrainingAdmissionAggregate:
        """Validate every label/media payload and return sanitized aggregates."""

        _validate_identity_key(identity_hash_key)
        try:
            with open_validated_zip_descriptor(
                label_zip_descriptor,
                policy=self._archive_policy,
            ) as label_opened, open_validated_zip_descriptor(
                source_zip_descriptor,
                policy=self._archive_policy,
            ) as source_opened:
                label_zip, label_infos, label_names, label_receipt = label_opened
                source_zip, source_infos, source_names, source_receipt = source_opened
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
                aggregate = _admit_pairs(
                    label_zip=label_zip,
                    source_zip=source_zip,
                    label_index=label_index,
                    source_index=source_index,
                    label_archive_sha256=label_receipt.archive_sha256,
                    source_archive_sha256=source_receipt.archive_sha256,
                    label_content_root_sha256=label_receipt.content_root_sha256,
                    source_content_root_sha256=source_receipt.content_root_sha256,
                    identity_hash_key=identity_hash_key,
                    shard_spec=self._shard_spec,
                    use_nonlocked_label_schema=(
                        self._uses_nonlocked_label_schema
                    ),
                )
        except Aihub538TrainingAdmissionError:
            raise
        except Aihub538ArchiveError as error:
            raise Aihub538TrainingAdmissionError(error.reason_code) from None
        except (BadZipFile, KeyError, OSError, RuntimeError, ValueError):
            raise Aihub538TrainingAdmissionError(
                "aihub538_training_archive_or_schema_invalid"
            ) from None
        return aggregate

    def admit_prevalidated(
        self,
        *,
        archive_binding: Aihub538AdmittedArchiveBinding,
        admission_receipt: Aihub538TrainingAdmissionReceipt,
        identity_hash_key: bytes,
    ) -> Aihub538PrevalidatedTrainingAdmission:
        """Admit through a live same-process validation capability.

        This path deliberately accepts neither serialized ZIP receipts nor raw
        descriptor/receipt pairs.  The capability is reasserted before and
        after all central-directory and label reads.
        """

        if type(admission_receipt) is not Aihub538TrainingAdmissionReceipt:
            raise Aihub538TrainingAdmissionError(
                "aihub538_training_prevalidated_admission_invalid"
            )
        _validate_identity_key(identity_hash_key)
        assert_aihub538_admitted_archive_binding(
            archive_binding,
            admission_receipt=admission_receipt,
            policy=self._archive_policy,
        )
        try:
            with _open_prevalidated_zip_file(
                archive_binding.label.descriptor
            ) as label_file, _open_prevalidated_zip_file(
                archive_binding.source.descriptor
            ) as source_file:
                with ZipFile(label_file, mode="r") as label_zip, ZipFile(
                    source_file,
                    mode="r",
                ) as source_zip:
                    label_infos = tuple(label_zip.infolist())
                    source_infos = tuple(source_zip.infolist())
                    label_names = tuple(
                        _safe_zip_info(info, policy=self._archive_policy)
                        for info in label_infos
                    )
                    source_names = tuple(
                        _safe_zip_info(info, policy=self._archive_policy)
                        for info in source_infos
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
                    aggregate = _admit_pairs(
                        label_zip=label_zip,
                        source_zip=source_zip,
                        label_index=label_index,
                        source_index=source_index,
                        label_archive_sha256=(
                            archive_binding.label.validation_receipt.archive_sha256
                        ),
                        source_archive_sha256=(
                            archive_binding.source.validation_receipt.archive_sha256
                        ),
                        label_content_root_sha256=(
                            archive_binding.label.validation_receipt.content_root_sha256
                        ),
                        source_content_root_sha256=(
                            archive_binding.source.validation_receipt.content_root_sha256
                        ),
                        identity_hash_key=identity_hash_key,
                        shard_spec=self._shard_spec,
                        use_nonlocked_label_schema=(
                            self._uses_nonlocked_label_schema
                        ),
                    )
        except Aihub538TrainingAdmissionError:
            raise
        except Aihub538ArchiveError as error:
            raise Aihub538TrainingAdmissionError(error.reason_code) from None
        except (BadZipFile, KeyError, OSError, RuntimeError, ValueError):
            raise Aihub538TrainingAdmissionError(
                "aihub538_training_archive_or_schema_invalid"
            ) from None
        finally:
            assert_aihub538_admitted_archive_binding(
                archive_binding,
                admission_receipt=admission_receipt,
                policy=self._archive_policy,
            )
        with AIHUB538_CAPABILITY_LIFETIME_LOCK:
            assert_aihub538_admitted_archive_binding(
                archive_binding,
                admission_receipt=admission_receipt,
                policy=self._archive_policy,
            )
            capability = _PrevalidatedAggregateCapabilityToken()
            record = (
                os.getpid(),
                None,
                aggregate,
                archive_binding,
                admission_receipt,
                self._archive_policy,
                False,
            )
            _PREVALIDATED_AGGREGATE_CAPABILITIES[capability] = record
            try:
                admission = Aihub538PrevalidatedTrainingAdmission(
                    aggregate=aggregate,
                    archive_binding=archive_binding,
                    admission_receipt=admission_receipt,
                    archive_policy=self._archive_policy,
                    _capability=capability,
                )
                _PREVALIDATED_AGGREGATE_CAPABILITIES[capability] = (
                    record[0],
                    ref(admission),
                    *record[2:],
                )
                return admission
            except BaseException:
                _PREVALIDATED_AGGREGATE_CAPABILITIES.pop(capability, None)
                raise


def _open_prevalidated_zip_file(descriptor: int):  # noqa: ANN202
    duplicate: int | None = None
    try:
        duplicate = os.dup(descriptor)
        os.set_inheritable(duplicate, False)
        return os.fdopen(duplicate, "rb", closefd=True)
    except OSError:
        if duplicate is not None:
            try:
                os.close(duplicate)
            except OSError:
                pass
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_prevalidated_descriptor_invalid"
        ) from None


def _member_index(
    *,
    infos: tuple[ZipInfo, ...],
    names: tuple[str, ...],
    role_component: str,
    allowed_suffixes: set[str],
) -> _PrivateMemberIndex:
    indexed: dict[str, ZipInfo] = {}
    for info, normalized in zip(infos, names, strict=True):
        if info.is_dir():
            continue
        path = PurePosixPath(normalized)
        role_positions = [
            index for index, part in enumerate(path.parts) if part == role_component
        ]
        if len(role_positions) != 1:
            raise Aihub538TrainingAdmissionError(
                "aihub538_training_role_component_invalid"
            )
        relative_parts = path.parts[role_positions[0] + 1 :]
        if not relative_parts:
            raise Aihub538TrainingAdmissionError(
                "aihub538_training_relative_member_invalid"
            )
        relative = PurePosixPath(*relative_parts)
        suffix = relative.suffix
        if suffix not in allowed_suffixes:
            raise Aihub538TrainingAdmissionError(
                "aihub538_training_member_type_invalid"
            )
        key = relative.as_posix()
        if key in indexed:
            raise Aihub538TrainingAdmissionError(
                "aihub538_training_semantic_member_duplicate"
            )
        indexed[key] = info
    if not indexed:
        raise Aihub538TrainingAdmissionError("aihub538_training_members_missing")
    return _PrivateMemberIndex(members=indexed)


def _admit_pairs(
    *,
    label_zip: ZipFile,
    source_zip: ZipFile,
    label_index: _PrivateMemberIndex,
    source_index: _PrivateMemberIndex,
    label_archive_sha256: str,
    source_archive_sha256: str,
    label_content_root_sha256: str,
    source_content_root_sha256: str,
    identity_hash_key: bytes,
    shard_spec: Aihub538NonlockedShardSpec,
    use_nonlocked_label_schema: bool,
) -> Aihub538TrainingAdmissionAggregate:
    samples: list[Aihub538TrainingSampleAggregate] = []
    speaker_ids: set[str] = set()
    session_ids: set[str] = set()
    expected_video_keys: set[str] = set()
    expected_audio_keys: set[str] = set()
    angle_samples: dict[str, list[Aihub538TrainingSampleAggregate]] = defaultdict(list)

    for label_key in sorted(label_index.members):
        label_info = label_index.members[label_key]
        if not 0 < label_info.file_size <= _MAXIMUM_LABEL_BYTES:
            raise Aihub538TrainingAdmissionError(
                "aihub538_training_label_size_outside_policy"
            )
        try:
            label_payload = label_zip.read(label_info)
        except (BadZipFile, RuntimeError, OSError):
            raise Aihub538TrainingAdmissionError(
                "aihub538_training_label_payload_invalid"
            ) from None
        private_label = _parse_label_payload(
            label_payload,
            allow_bounded_reversed_bbox_unavailable=(
                shard_spec in AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS
            ),
            allow_label_capacity_terminal_frame_tail=(
                use_nonlocked_label_schema
            ),
        )
        label_relative = PurePosixPath(label_key)
        if label_relative.stem != private_label.session_stem:
            raise Aihub538TrainingAdmissionError(
                "aihub538_training_label_name_mismatch"
            )
        video_key = label_relative.with_suffix(".mp4").as_posix()
        a_audio_stem = _a_view_stem(private_label.session_stem)
        audio_key = label_relative.with_name(f"{a_audio_stem}.wav").as_posix()
        expected_video_keys.add(video_key)
        expected_audio_keys.add(audio_key)
        try:
            video_info = source_index.members[video_key]
            audio_info = source_index.members[audio_key]
        except KeyError:
            raise Aihub538TrainingAdmissionError(
                "aihub538_training_exact_media_pairing_failed"
            ) from None
        if (
            private_label.video_name != f"{private_label.session_stem}.mp4"
            # Actual JSON names the label's own view even though the pinned
            # creator preprocess rewrites non-A source lookup to the shared
            # A-view WAV.  Validate the metadata name and payload binding as
            # two separate facts.
            or private_label.audio_name != f"{private_label.session_stem}.wav"
        ):
            raise Aihub538TrainingAdmissionError(
                "aihub538_training_metadata_media_name_mismatch"
            )
        _validate_mp4_envelope(source_zip, video_info)
        _validate_wav_envelope(source_zip, audio_info)

        sample_sha256 = sha256_document(
            {
                "schema_version": "voice_coach.aihub538_training_sample_binding.v1",
                "private_relative_key_sha256": hashlib.sha256(
                    b"voice_coach.aihub538.private_relative_key.v1\x00"
                    + label_key.encode("utf-8")
                ).hexdigest(),
                "label_payload_sha256": private_label.label_payload_sha256,
                "source_archive_sha256": source_archive_sha256,
                "video_size_bytes": video_info.file_size,
                "video_crc32": video_info.CRC,
                "audio_size_bytes": audio_info.file_size,
                "audio_crc32": audio_info.CRC,
                "angle": private_label.angle,
            }
        )
        label_selection_token_sha256 = (
            aihub538_label_selection_token_sha256(
                private_relative_key_sha256=hashlib.sha256(
                    b"voice_coach.aihub538.private_relative_key.v1\x00"
                    + label_key.encode("utf-8")
                ).hexdigest(),
                label_payload_sha256=private_label.label_payload_sha256,
                label_archive_sha256=label_archive_sha256,
                angle=private_label.angle,
            )
        )
        sample = Aihub538TrainingSampleAggregate(
            sample_sha256=sample_sha256,
            angle=private_label.angle,
            noise_code=private_label.noise_code,
            video_duration_seconds=private_label.video_duration_seconds,
            audio_duration_seconds=private_label.audio_duration_seconds,
            frames_per_second=private_label.frames_per_second,
            width=private_label.width,
            height=private_label.height,
            lip_bbox_frame_count=private_label.lip_bbox_frame_count,
            face_bbox_frame_count=private_label.face_bbox_frame_count,
            sentence_count=private_label.sentence_count,
            sentence_duration_seconds=private_label.sentence_duration_seconds,
            video_size_bytes=video_info.file_size,
            audio_size_bytes=audio_info.file_size,
            video_crc32=video_info.CRC,
            audio_crc32=audio_info.CRC,
            face_reversed_bbox_unavailable_count=(
                private_label.face_reversed_bbox_unavailable_count
            ),
            lip_reversed_bbox_unavailable_count=(
                private_label.lip_reversed_bbox_unavailable_count
            ),
            label_selection_token_sha256=label_selection_token_sha256,
        )
        samples.append(sample)
        angle_samples[sample.angle].append(sample)
        speaker_ids.add(private_label.speaker_id)
        session_ids.add(_a_view_stem(private_label.session_stem))

    actual_video_keys = {
        key for key in source_index.members if PurePosixPath(key).suffix == ".mp4"
    }
    actual_audio_keys = {
        key for key in source_index.members if PurePosixPath(key).suffix == ".wav"
    }
    if (
        expected_video_keys != actual_video_keys
        or expected_audio_keys != actual_audio_keys
        or len(samples) != len(label_index.members)
    ):
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_exact_media_pairing_failed"
        )
    if not samples or not speaker_ids or not expected_audio_keys:
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_admitted_population_empty"
        )

    samples.sort(key=lambda sample: sample.sample_sha256)
    admission_policy_sha256 = aihub538_shard_admission_policy_sha256(shard_spec)
    sample_selection_sha256 = sha256_document(
        {
            "schema_version": "voice_coach.aihub538_training_sample_selection.v1",
            "selection_rule": shard_spec.sample_selection_rule,
            "admission_policy_sha256": admission_policy_sha256,
            "sample_sha256": [sample.sample_sha256 for sample in samples],
        }
    )
    angle_aggregates = tuple(
        _angle_aggregate(angle, tuple(angle_samples.get(angle, ())))
        for angle in AIHUB538_TRAINING_ANGLES
    )
    restricted_identity_set = _restricted_identity_set(
        identity_hash_key=identity_hash_key,
        speaker_ids=speaker_ids,
        session_ids=session_ids,
    )
    restricted_member_set = _restricted_member_set(
        identity_hash_key=identity_hash_key,
        relative_label_paths=set(label_index.members),
    )
    manifest_document: dict[str, object] = {
            "schema_version": "voice_coach.aihub538_training_admission_manifest.v1",
            "label_archive_sha256": label_archive_sha256,
            "source_archive_sha256": source_archive_sha256,
            "label_content_root_sha256": label_content_root_sha256,
            "source_content_root_sha256": source_content_root_sha256,
            "sample_selection_sha256": sample_selection_sha256,
            "paired_sample_count": len(samples),
            "unique_audio_count": len(expected_audio_keys),
            "speaker_count": len(speaker_ids),
            "session_count": len(session_ids),
            "identity_namespace_commitment_sha256": (
                restricted_identity_set.namespace_commitment_sha256
            ),
            "view_aggregates": [item.to_json_object() for item in angle_aggregates],
    }
    if shard_spec != AIHUB538_TS174_TL174_SHARD:
        manifest_document.update(
            {
                "schema_version": (
                    "voice_coach.aihub538_training_admission_manifest.v2"
                ),
                "shard_spec_sha256": shard_spec.spec_sha256,
                "admission_policy_sha256": admission_policy_sha256,
                "bbox_observation_policy_sha256": (
                    AIHUB538_BOUNDED_REVERSED_BBOX_OBSERVATION_POLICY_SHA256
                ),
                "face_reversed_bbox_unavailable_count": sum(
                    item.face_reversed_bbox_unavailable_count
                    for item in samples
                ),
                "lip_reversed_bbox_unavailable_count": sum(
                    item.lip_reversed_bbox_unavailable_count
                    for item in samples
                ),
                "reversed_bbox_affected_sample_count": sum(
                    bool(
                        item.face_reversed_bbox_unavailable_count
                        or item.lip_reversed_bbox_unavailable_count
                    )
                    for item in samples
                ),
                "reversed_bbox_coordinates_reordered": False,
                "selected_unavailable_bbox_is_qc_failure": True,
            }
        )
        if shard_spec in AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS[2:]:
            manifest_document.update(
                {
                    "schema_version": (
                        "voice_coach.aihub538_training_admission_manifest.v3"
                    ),
                    "terminal_frame_tail_policy_sha256": (
                        AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256
                    ),
                }
            )
    admission_manifest_sha256 = sha256_document(manifest_document)
    return Aihub538TrainingAdmissionAggregate(
        label_archive_sha256=label_archive_sha256,
        source_archive_sha256=source_archive_sha256,
        label_zip_content_root_sha256=label_content_root_sha256,
        source_zip_content_root_sha256=source_content_root_sha256,
        samples=tuple(samples),
        speaker_count=len(speaker_ids),
        unique_audio_count=len(expected_audio_keys),
        angle_aggregates=angle_aggregates,
        sample_selection_sha256=sample_selection_sha256,
        admission_manifest_sha256=admission_manifest_sha256,
        restricted_identity_set=restricted_identity_set,
        shard_id=shard_spec.shard_id,
        dataset_partition=shard_spec.dataset_partition,
        source_role_component=shard_spec.source_role_component,
        label_role_component=shard_spec.label_role_component,
        ordered_file_keys=shard_spec.ordered_file_keys,
        admission_policy_sha256=admission_policy_sha256,
        sample_selection_rule=shard_spec.sample_selection_rule,
        restricted_member_set=restricted_member_set,
    )


def _validate_identity_key(identity_hash_key: bytes) -> None:
    if (
        not isinstance(identity_hash_key, bytes)
        or not 32 <= len(identity_hash_key) <= 128
    ):
        raise Aihub538TrainingAdmissionError(
            "aihub538_identity_hash_key_invalid"
        )


def _restricted_identity_set(
    *,
    identity_hash_key: bytes,
    speaker_ids: set[str],
    session_ids: set[str],
) -> Aihub538RestrictedIdentitySet:
    return Aihub538RestrictedIdentitySet(
        namespace_commitment_sha256=hmac.new(
            identity_hash_key,
            b"voice_coach.aihub538.identity_namespace.v1",
            hashlib.sha256,
        ).hexdigest(),
        speaker_hmac_sha256=frozenset(
            hmac.new(
                identity_hash_key,
                b"voice_coach.aihub538.speaker.v1\x00" + value.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            for value in speaker_ids
        ),
        session_hmac_sha256=frozenset(
            hmac.new(
                identity_hash_key,
                b"voice_coach.aihub538.session.v1\x00" + value.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            for value in session_ids
        ),
    )


def _restricted_member_set(
    *,
    identity_hash_key: bytes,
    relative_label_paths: set[str],
) -> Aihub538RestrictedMemberSet:
    return Aihub538RestrictedMemberSet(
        namespace_commitment_sha256=hmac.new(
            identity_hash_key,
            b"voice_coach.aihub538.member_namespace.v1",
            hashlib.sha256,
        ).hexdigest(),
        relative_label_path_hmac_sha256=frozenset(
            hmac.new(
                identity_hash_key,
                b"voice_coach.aihub538.relative_label_path.v1\x00"
                + value.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            for value in relative_label_paths
        ),
    )


def _parse_label_payload(
    payload: bytes,
    *,
    allow_bounded_reversed_bbox_unavailable: bool = False,
    allow_label_capacity_terminal_frame_tail: bool = False,
) -> _PrivateLabel:
    document = _load_creator_json(payload)
    if not isinstance(document, list) or len(document) != 1:
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_label_root_not_single_item_array"
        )
    record = _strict_mapping(document[0], _TOP_LEVEL_KEYS)
    dataset = _strict_mapping(record["dataSet"], _DATASET_KEYS)
    video_info = _strict_mapping(record["Video_info"], _VIDEO_INFO_KEYS)
    audio_info = _strict_mapping(record["Audio_info"], _AUDIO_INFO_KEYS)
    audio_env = _strict_mapping(record["Audio_env"], _AUDIO_ENV_KEYS)
    video_env = _strict_mapping(record["Video_env"], _VIDEO_ENV_KEYS)
    speaker_info = _strict_mapping(record["speaker_info"], _SPEAKER_KEYS)
    bounding_info = _strict_mapping(
        record["Bounding_box_info"],
        _BOUNDING_BOX_KEYS,
    )
    if (
        not isinstance(dataset["description"], str)
        or not dataset["description"].strip()
        or not isinstance(dataset["url"], str)
        or not dataset["url"].strip()
        or isinstance(dataset["version"], bool)
        or not isinstance(dataset["version"], (int, float))
        or not isinstance(dataset["year"], str)
        or not dataset["year"].strip()
    ):
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_dataset_metadata_invalid"
        )

    video_name = _strict_basename(video_info["video_Name"], suffix=".mp4")
    audio_name = _strict_basename(audio_info["Audio_Name"], suffix=".wav")
    session_match = _SESSION_STEM_PATTERN.fullmatch(PurePosixPath(video_name).stem)
    if session_match is None:
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_session_name_invalid"
        )
    environment = session_match.group("environment")
    noise_code = int(session_match.group("noise"))
    gender = session_match.group("gender")
    age_code = int(session_match.group("age"))
    specificity = session_match.group("specificity")
    speaker_id = f"{specificity}{session_match.group('speaker')}"
    angle = session_match.group("angle")
    if (
        not _normalize_environment_category(video_env["env"])
        or _normalize_code(video_env["Angle"]) != angle
        or _strict_integer(audio_env["Noise"], minimum=1, maximum=6) != noise_code
        or _normalize_code(speaker_info["Gender"]) != gender
        or _normalize_code(speaker_info["Specificity"]) != specificity
        or _normalize_age_code(speaker_info["Age"]) != age_code
        or speaker_info["speaker_ID"] != speaker_id
        or not isinstance(speaker_info["Accent"], str)
        or not speaker_info["Accent"].strip()
    ):
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_session_metadata_mismatch"
        )
    if _normalize_format(video_info["video_Format"]) != "mp4":
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_video_format_invalid"
        )
    if _normalize_format(audio_info["Audio_Format"]) != "wav":
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_audio_format_invalid"
        )
    video_duration = _duration_seconds(video_info["video_Duration"])
    audio_duration = _duration_seconds(audio_info["Audio_Duration"])
    frames_per_second = _strict_number(
        video_info["FPS"],
        minimum=1.0,
        maximum=_MAXIMUM_FRAME_RATE,
    )
    width, height = _resolution(video_info["Resolution"])
    _validate_audio_metadata(audio_info)

    face_sequence = _box_sequence(
        _strict_mapping(
            bounding_info["Face_bounding_box"],
            _BOX_SEQUENCE_KEYS,
        )["xtl_ytl_xbr_ybr"],
        width=width,
        height=height,
        allow_bounded_reversed_unavailable=(
            allow_bounded_reversed_bbox_unavailable
        ),
    )
    lip_sequence = _box_sequence(
        _strict_mapping(
            bounding_info["Lip_bounding_box"],
            _BOX_SEQUENCE_KEYS,
        )["xtl_ytl_xbr_ybr"],
        width=width,
        height=height,
        allow_bounded_reversed_unavailable=(
            allow_bounded_reversed_bbox_unavailable
        ),
    )
    expected_frames = round(video_duration * frames_per_second)
    allowed_delta = max(
        _FRAME_COUNT_ABSOLUTE_TOLERANCE,
        math.ceil(expected_frames * _FRAME_COUNT_RELATIVE_TOLERANCE),
    )
    if (
        len(face_sequence) != len(lip_sequence)
        or abs(len(lip_sequence) - expected_frames) > allowed_delta
    ):
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_bbox_frame_count_invalid"
        )
    (
        sentence_count,
        sentence_duration,
        terminal_media_overrun,
        terminal_frame_tail_exception_applied,
        terminal_complete_tail_frames,
    ) = _sentence_summary(
        record["Sentence_info"],
        maximum_duration_seconds=min(video_duration, audio_duration),
        frames_per_second=frames_per_second,
        allow_label_capacity_terminal_frame_tail=(
            allow_label_capacity_terminal_frame_tail
        ),
    )
    return _PrivateLabel(
        label_payload_sha256=hashlib.sha256(payload).hexdigest(),
        session_stem=PurePosixPath(video_name).stem,
        environment=environment,
        noise_code=noise_code,
        gender=gender,
        age_code=age_code,
        specificity=specificity,
        speaker_id=speaker_id,
        angle=angle,
        video_name=video_name,
        audio_name=audio_name,
        video_duration_seconds=video_duration,
        audio_duration_seconds=audio_duration,
        frames_per_second=frames_per_second,
        width=width,
        height=height,
        lip_bbox_frame_count=len(lip_sequence),
        face_bbox_frame_count=len(face_sequence),
        lip_reversed_bbox_unavailable_count=sum(
            row_start > row_end or column_start > column_end
            for row_start, column_start, row_end, column_end in lip_sequence
        ),
        face_reversed_bbox_unavailable_count=sum(
            row_start > row_end or column_start > column_end
            for row_start, column_start, row_end, column_end in face_sequence
        ),
        sentence_count=sentence_count,
        sentence_duration_seconds=sentence_duration,
        terminal_media_overrun=terminal_media_overrun,
        terminal_frame_tail_exception_applied=(
            terminal_frame_tail_exception_applied
        ),
        terminal_complete_tail_frames=terminal_complete_tail_frames,
    )


def _load_creator_json(payload: bytes) -> object:
    def reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise _DuplicateJsonKeyError(key)
            result[key] = value
        return result

    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateJsonKeyError,
        ValueError,
    ):
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_label_json_invalid"
        ) from None


def _strict_mapping(value: object, expected_keys: set[str]) -> dict[str, object]:
    if (
        not isinstance(value, dict)
        or not all(isinstance(key, str) for key in value)
        or set(value) != expected_keys
    ):
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_label_schema_invalid"
        )
    return value


def _strict_basename(value: object, *, suffix: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or PurePosixPath(value).name != value
        or "\\" in value
        or PurePosixPath(value).suffix != suffix
    ):
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_media_name_invalid"
        )
    return value


def _normalize_format(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower().lstrip(".")


def _normalize_code(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    return value.strip().upper()[0]


def _normalize_environment_category(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    normalized = value.strip().casefold()
    if normalized not in _VIDEO_ENVIRONMENT_CATEGORIES:
        return ""
    return normalized


def _normalize_age_code(value: object) -> int:
    if isinstance(value, bool):
        return -1
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return -1


def _strict_integer(value: object, *, minimum: int, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_integer_metadata_invalid"
        )
    return value


def _strict_number(value: object, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_numeric_metadata_invalid"
        )
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_numeric_metadata_invalid"
        )
    return number


def _duration_seconds(value: object) -> float:
    if isinstance(value, bool):
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_duration_invalid"
        )
    if isinstance(value, (int, float)):
        duration = float(value)
    elif isinstance(value, str) and _DURATION_PATTERN.fullmatch(value.strip()):
        duration = float(value.strip())
    elif isinstance(value, str):
        parts = value.strip().split(":")
        if len(parts) != 3:
            raise Aihub538TrainingAdmissionError(
                "aihub538_training_duration_invalid"
            )
        try:
            hours, minutes, seconds = (
                float(parts[0]),
                float(parts[1]),
                float(parts[2]),
            )
        except ValueError:
            raise Aihub538TrainingAdmissionError(
                "aihub538_training_duration_invalid"
            ) from None
        if (
            not hours.is_integer()
            or not minutes.is_integer()
            or hours < 0
            or not 0 <= minutes < 60
            or not 0 <= seconds < 60
        ):
            raise Aihub538TrainingAdmissionError(
                "aihub538_training_duration_invalid"
            )
        duration = hours * 3600 + minutes * 60 + seconds
    else:
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_duration_invalid"
        )
    if not math.isfinite(duration) or not 0 < duration <= _MAXIMUM_DURATION_SECONDS:
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_duration_invalid"
        )
    return duration


def _resolution(value: object) -> tuple[int, int]:
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(
            not isinstance(item, bool) and isinstance(item, int) and item > 0
            for item in value
        )
    ):
        width, height = value
    elif isinstance(value, str):
        match = _RESOLUTION_PATTERN.fullmatch(value)
        if match is None:
            raise Aihub538TrainingAdmissionError(
                "aihub538_training_resolution_invalid"
            )
        width = int(match.group("width"))
        height = int(match.group("height"))
    else:
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_resolution_invalid"
        )
    if not 16 <= width <= 16_384 or not 16 <= height <= 16_384:
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_resolution_invalid"
        )
    return width, height


def _validate_audio_metadata(audio_info: dict[str, object]) -> None:
    sampling_rate = audio_info["Sampling_rate"]
    if isinstance(sampling_rate, int):
        valid_rate = 8_000 <= sampling_rate <= 384_000
    elif isinstance(sampling_rate, str):
        normalized = sampling_rate.strip().lower().replace("khz", "000")
        normalized = normalized.replace("hz", "")
        valid_rate = normalized.isdigit() and 8_000 <= int(normalized) <= 384_000
    else:
        valid_rate = False
    channels = audio_info["Channel(s)"]
    if (
        not valid_rate
        or isinstance(channels, bool)
        or not isinstance(channels, int)
        or not 1 <= channels <= 8
    ):
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_audio_metadata_invalid"
        )


def _box_sequence(
    value: object,
    *,
    width: int,
    height: int,
    allow_bounded_reversed_unavailable: bool = False,
) -> tuple[tuple[float, float, float, float], ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= _MAXIMUM_FRAME_COUNT:
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_bbox_sequence_invalid"
        )
    boxes: list[tuple[float, float, float, float]] = []
    for raw_box in value:
        if not isinstance(raw_box, list) or len(raw_box) != 4:
            raise Aihub538TrainingAdmissionError(
                "aihub538_training_bbox_sequence_invalid"
            )
        numbers: list[float] = []
        for coordinate in raw_box:
            if isinstance(coordinate, bool) or not isinstance(
                coordinate, (int, float)
            ):
                raise Aihub538TrainingAdmissionError(
                    "aihub538_training_bbox_coordinate_invalid"
                )
            number = float(coordinate)
            if not math.isfinite(number):
                raise Aihub538TrainingAdmissionError(
                    "aihub538_training_bbox_coordinate_invalid"
                )
            numbers.append(number)
        # The pinned creator preprocess implementation applies these values as
        # ``frame[first:third, second:fourth]``.  In other words the first pair
        # bounds image rows (height) and the second pair bounds columns (width),
        # despite the public JSON field's xtl/ytl/xbr/ybr spelling.
        row_start, column_start, row_end, column_end = numbers
        coordinates_within_bounds = (
            0 <= row_start <= height
            and 0 <= row_end <= height
            and 0 <= column_start <= width
            and 0 <= column_end <= width
        )
        endpoints_increasing = (
            row_start < row_end and column_start < column_end
        )
        has_bounded_reversal = (
            coordinates_within_bounds
            and (row_start > row_end or column_start > column_end)
            and row_start != row_end
            and column_start != column_end
        )
        if not coordinates_within_bounds or not (
            endpoints_increasing
            or (
                allow_bounded_reversed_unavailable
                and has_bounded_reversal
            )
        ):
            raise Aihub538TrainingAdmissionError(
                "aihub538_training_bbox_coordinate_invalid"
            )
        boxes.append((row_start, column_start, row_end, column_end))
    return tuple(boxes)


def _sentence_summary(
    value: object,
    *,
    maximum_duration_seconds: float,
    frames_per_second: float,
    allow_label_capacity_terminal_frame_tail: bool,
) -> tuple[int, float, bool, bool, int]:
    if not isinstance(value, list) or not 1 <= len(value) <= _MAXIMUM_SENTENCE_COUNT:
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_sentence_sequence_invalid"
        )
    identifiers: set[str] = set()
    previous_end = 0.0
    total_duration = 0.0
    total_characters = 0
    terminal_media_overrun = False
    terminal_frame_tail_exception_applied = False
    terminal_complete_tail_frames = 0
    for index, sentence in enumerate(value):
        item = _strict_mapping(sentence, _SENTENCE_KEYS)
        identifier = item["ID"]
        if isinstance(identifier, bool) or not isinstance(identifier, (str, int)):
            raise Aihub538TrainingAdmissionError(
                "aihub538_training_sentence_identifier_invalid"
            )
        identifier_text = str(identifier)
        if not identifier_text or identifier_text in identifiers:
            raise Aihub538TrainingAdmissionError(
                "aihub538_training_sentence_identifier_invalid"
            )
        identifiers.add(identifier_text)
        transcript = item["sentence_text"]
        topic = item["topic"]
        if (
            not isinstance(transcript, str)
            or not transcript.strip()
            or not isinstance(topic, str)
            or not topic.strip()
        ):
            raise Aihub538TrainingAdmissionError(
                "aihub538_training_sentence_text_invalid"
            )
        total_characters += len(transcript)
        if total_characters > _MAXIMUM_TRANSCRIPT_CHARACTERS:
            raise Aihub538TrainingAdmissionError(
                "aihub538_training_sentence_text_outside_policy"
            )
        start = _strict_number(
            item["start_time"],
            minimum=0.0,
            maximum=_MAXIMUM_DURATION_SECONDS,
        )
        end = _strict_number(
            item["end_time"],
            minimum=0.0,
            maximum=_MAXIMUM_DURATION_SECONDS,
        )
        overrun_seconds = end - maximum_duration_seconds
        is_terminal = index == len(value) - 1
        tail_invalid = (
            end > (
                maximum_duration_seconds
                + AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY[
                    "ordinary_media_tail_tolerance_seconds"
                ]
            )
        )
        exception_applied = False
        if allow_label_capacity_terminal_frame_tail and overrun_seconds > 0:
            if not is_terminal:
                tail_invalid = True
            elif tail_invalid:
                exception_applied = _terminal_frame_tail_is_bounded(
                    overrun_seconds=overrun_seconds,
                    frames_per_second=frames_per_second,
                )
                tail_invalid = not exception_applied
        overlap_tolerance = (
            AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY[
                "sentence_overlap_comparison_epsilon"
            ]
            if allow_label_capacity_terminal_frame_tail
            else _SENTENCE_OVERLAP_TOLERANCE_SECONDS
        )
        if (
            end <= start
            or start + overlap_tolerance < previous_end
            or tail_invalid
        ):
            raise Aihub538TrainingAdmissionError(
                "aihub538_training_sentence_time_invalid"
            )
        if is_terminal and overrun_seconds > 0:
            terminal_media_overrun = True
            terminal_frame_tail_exception_applied = exception_applied
            terminal_complete_tail_frames = _stable_complete_tail_frames(
                overrun_seconds=overrun_seconds,
                frames_per_second=frames_per_second,
            )
        previous_end = end
        total_duration += end - start
    return (
        len(value),
        total_duration,
        terminal_media_overrun,
        terminal_frame_tail_exception_applied,
        terminal_complete_tail_frames,
    )


def _terminal_frame_tail_is_bounded(
    *,
    overrun_seconds: float,
    frames_per_second: float,
) -> bool:
    policy = AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY
    epsilon = policy["frame_integer_epsilon"]
    required_fps = policy["required_frames_per_second"]
    if (
        not isinstance(epsilon, float)
        or not isinstance(required_fps, float)
        or abs(frames_per_second - required_fps) > epsilon
    ):
        return False
    complete_frames = _stable_complete_tail_frames(
        overrun_seconds=overrun_seconds,
        frames_per_second=frames_per_second,
    )
    return complete_frames <= policy["maximum_complete_tail_frames"]


def _stable_complete_tail_frames(
    *,
    overrun_seconds: float,
    frames_per_second: float,
) -> int:
    scaled_frames = overrun_seconds * frames_per_second
    # Advance by one representable float only.  This corrects an exact integer
    # product rounded one ULP downward without converting a real fractional
    # value such as 15.9999999995 into 16 complete frames.
    return math.floor(math.nextafter(scaled_frames, math.inf))


def _validate_mp4_envelope(zip_file: ZipFile, info: ZipInfo) -> None:
    try:
        with zip_file.open(info, "r") as source:
            envelope = source.read(16)
    except (BadZipFile, RuntimeError, OSError):
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_mp4_envelope_invalid"
        ) from None
    if len(envelope) < 12 or envelope[4:8] != b"ftyp":
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_mp4_envelope_invalid"
        )


def _validate_wav_envelope(zip_file: ZipFile, info: ZipInfo) -> None:
    try:
        with zip_file.open(info, "r") as source:
            envelope = source.read(12)
    except (BadZipFile, RuntimeError, OSError):
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_wav_envelope_invalid"
        ) from None
    if (
        len(envelope) != 12
        or envelope[:4] not in {b"RIFF", b"RF64"}
        or envelope[8:12] != b"WAVE"
    ):
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_wav_envelope_invalid"
        )


def _a_view_stem(session_stem: str) -> str:
    match = _SESSION_STEM_PATTERN.fullmatch(session_stem)
    if match is None:
        raise Aihub538TrainingAdmissionError(
            "aihub538_training_session_name_invalid"
        )
    return (
        f"lip_{match.group('environment')}_{match.group('noise')}_"
        f"{match.group('gender')}_{match.group('age')}_"
        f"{match.group('specificity')}{match.group('speaker')}_A_"
        f"{match.group('session')}"
    )


def _angle_aggregate(
    angle: str,
    samples: tuple[Aihub538TrainingSampleAggregate, ...],
) -> Aihub538TrainingAngleAggregate:
    sample_count = len(samples)
    return Aihub538TrainingAngleAggregate(
        angle=angle,
        paired_sample_count=sample_count,
        label_count=sample_count,
        video_count=sample_count,
        audio_reference_count=sample_count,
        lip_bbox_frame_count=sum(sample.lip_bbox_frame_count for sample in samples),
        sentence_count=sum(sample.sentence_count for sample in samples),
    )


__all__ = [
    "Aihub538TrainingAdmissionError",
    "Aihub538TrainingDatasetAdapter",
]
