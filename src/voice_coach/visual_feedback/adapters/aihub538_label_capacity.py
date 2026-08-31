"""Restricted TL175 label-only adapter for staged capacity admission."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import PurePosixPath
from zipfile import BadZipFile

from ..models.aihub538_label_capacity import (
    Aihub538LabelCapacityAggregate,
    Aihub538LabelCapacityItem,
)
from ..models.aihub538_balanced_cohort import (
    aihub538_label_selection_token_sha256,
)
from ..models.aihub538_nonlocked_shards import (
    AIHUB538_TL175_CAPACITY_AUDIT_SHARD,
    Aihub538NonlockedShardSpec,
    require_aihub538_nonlocked_shard_spec,
)
from ..models.integrity import sha256_document
from .aihub538_training_archive import (
    Aihub538ArchiveError,
    Aihub538ArchiveSecurityPolicy,
    DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY,
    open_validated_zip_descriptor,
)
from .aihub538_training_dataset import (
    Aihub538TrainingAdmissionError,
    _a_view_stem,
    _member_index,
    _parse_label_payload,
)


class Aihub538LabelCapacityError(RuntimeError):
    """Stable path-free failure for the label-only audit boundary."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class Aihub538LabelCapacityAdapter:
    """Strict-parse every TL175 label without opening source or locked data."""

    def __init__(
        self,
        *,
        shard_spec: Aihub538NonlockedShardSpec = (
            AIHUB538_TL175_CAPACITY_AUDIT_SHARD
        ),
        archive_policy: Aihub538ArchiveSecurityPolicy = (
            DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY
        ),
    ) -> None:
        try:
            self._shard_spec = require_aihub538_nonlocked_shard_spec(shard_spec)
        except ValueError as error:
            raise Aihub538LabelCapacityError(str(error)) from None
        self._archive_policy = archive_policy

    def audit(
        self,
        *,
        label_zip_descriptor: int,
        identity_hash_key: bytes,
    ) -> Aihub538LabelCapacityAggregate:
        """Return only private HMAC rows plus whole-archive commitments."""

        if (
            not isinstance(identity_hash_key, bytes)
            or not 32 <= len(identity_hash_key) <= 128
        ):
            raise Aihub538LabelCapacityError(
                "aihub538_label_capacity_identity_key_invalid"
            )
        try:
            with open_validated_zip_descriptor(
                label_zip_descriptor,
                policy=self._archive_policy,
            ) as opened:
                archive, infos, names, validation = opened
                member_index = _member_index(
                    infos=infos,
                    names=names,
                    role_component=(
                        self._shard_spec.label_role_component
                    ),
                    allowed_suffixes={".json"},
                )
                items: list[Aihub538LabelCapacityItem] = []
                path_hmacs: set[str] = set()
                seen_samples: set[str] = set()
                face_bbox_observation_count = 0
                lip_bbox_observation_count = 0
                face_reversed_bbox_unavailable_count = 0
                lip_reversed_bbox_unavailable_count = 0
                reversed_bbox_affected_label_count = 0
                for relative_path in sorted(member_index.members):
                    info = member_index.members[relative_path]
                    payload = archive.read(info)
                    private_label = _parse_label_payload(
                        payload,
                        allow_bounded_reversed_bbox_unavailable=True,
                        allow_label_capacity_terminal_frame_tail=True,
                    )
                    if PurePosixPath(relative_path).stem != private_label.session_stem:
                        raise Aihub538LabelCapacityError(
                            "aihub538_label_capacity_name_mismatch"
                        )
                    sample_sha256 = aihub538_label_selection_token_sha256(
                        private_relative_key_sha256=hashlib.sha256(
                            b"voice_coach.aihub538.private_relative_key.v1\x00"
                            + relative_path.encode("utf-8")
                        ).hexdigest(),
                        label_payload_sha256=(
                            private_label.label_payload_sha256
                        ),
                        label_archive_sha256=validation.archive_sha256,
                        angle=private_label.angle,
                    )
                    if sample_sha256 in seen_samples:
                        raise Aihub538LabelCapacityError(
                            "aihub538_label_capacity_sample_collision"
                        )
                    seen_samples.add(sample_sha256)
                    items.append(
                        Aihub538LabelCapacityItem(
                            sample_sha256=sample_sha256,
                            angle=private_label.angle,
                            actor_token_sha256=_hmac(
                                identity_hash_key,
                                b"voice_coach.aihub538.speaker.v1\x00",
                                private_label.speaker_id,
                            ),
                            attempt_token_sha256=_hmac(
                                identity_hash_key,
                                b"voice_coach.aihub538.session.v1\x00",
                                _a_view_stem(private_label.session_stem),
                            ),
                            metadata_bbox_observation_available=not bool(
                                private_label.face_reversed_bbox_unavailable_count
                                or private_label.lip_reversed_bbox_unavailable_count
                            ),
                            terminal_media_overrun=(
                                private_label.terminal_media_overrun
                            ),
                            terminal_frame_tail_exception_applied=(
                                private_label
                                .terminal_frame_tail_exception_applied
                            ),
                            terminal_complete_tail_frames=(
                                private_label.terminal_complete_tail_frames
                            ),
                        )
                    )
                    path_hmacs.add(
                        _hmac(
                            identity_hash_key,
                            b"voice_coach.aihub538.relative_label_path.v1\x00",
                            relative_path,
                        )
                    )
                    face_bbox_observation_count += (
                        private_label.face_bbox_frame_count
                    )
                    lip_bbox_observation_count += (
                        private_label.lip_bbox_frame_count
                    )
                    face_reversed_bbox_unavailable_count += (
                        private_label.face_reversed_bbox_unavailable_count
                    )
                    lip_reversed_bbox_unavailable_count += (
                        private_label.lip_reversed_bbox_unavailable_count
                    )
                    if (
                        private_label.face_reversed_bbox_unavailable_count
                        or private_label.lip_reversed_bbox_unavailable_count
                    ):
                        reversed_bbox_affected_label_count += 1
        except Aihub538LabelCapacityError:
            raise
        except (Aihub538ArchiveError, Aihub538TrainingAdmissionError) as error:
            raise Aihub538LabelCapacityError(error.reason_code) from None
        except (BadZipFile, KeyError, OSError, RuntimeError, ValueError):
            raise Aihub538LabelCapacityError(
                "aihub538_label_capacity_archive_or_schema_invalid"
            ) from None
        if not items or len(path_hmacs) != len(items):
            raise Aihub538LabelCapacityError(
                "aihub538_label_capacity_population_invalid"
            )
        items.sort(key=lambda item: item.sample_sha256)
        return Aihub538LabelCapacityAggregate(
            label_archive_sha256=validation.archive_sha256,
            label_content_root_sha256=validation.content_root_sha256,
            identity_namespace_commitment_sha256=_namespace(
                identity_hash_key,
                b"voice_coach.aihub538.identity_namespace.v1",
            ),
            member_namespace_commitment_sha256=_namespace(
                identity_hash_key,
                b"voice_coach.aihub538.member_namespace.v1",
            ),
            items=tuple(items),
            relative_path_hmac_sha256=frozenset(path_hmacs),
            face_bbox_observation_count=face_bbox_observation_count,
            lip_bbox_observation_count=lip_bbox_observation_count,
            face_reversed_bbox_unavailable_count=(
                face_reversed_bbox_unavailable_count
            ),
            lip_reversed_bbox_unavailable_count=(
                lip_reversed_bbox_unavailable_count
            ),
            reversed_bbox_affected_label_count=(
                reversed_bbox_affected_label_count
            ),
            terminal_media_overrun_label_count=sum(
                item.terminal_media_overrun for item in items
            ),
            terminal_frame_tail_exception_label_count=sum(
                item.terminal_frame_tail_exception_applied for item in items
            ),
            maximum_complete_tail_frames_observed=max(
                item.terminal_complete_tail_frames for item in items
            ),
            nonterminal_media_overrun_label_count=0,
            sentence_overlap_label_count=0,
            invalid_time_label_count=0,
            nonconforming_fps_label_count=0,
        )


def _hmac(key: bytes, domain: bytes, value: str) -> str:
    return hmac.new(key, domain + value.encode("utf-8"), hashlib.sha256).hexdigest()


def _namespace(key: bytes, domain: bytes) -> str:
    return hmac.new(key, domain, hashlib.sha256).hexdigest()


__all__ = [
    "Aihub538LabelCapacityAdapter",
    "Aihub538LabelCapacityError",
]
