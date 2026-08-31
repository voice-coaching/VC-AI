"""Fixture-only VS11/VL11 admission and exhaustive P8 evaluation boundary.

The adapter reuses the public OLKAVS creator-schema parser used for the
non-locked TS174/TL174 shard, but assigns the validation roles ``VL11`` and
``VS11``.  Member names, transcripts, raw participant identifiers, keyed item
bindings, and per-item outcomes stay inside this process.  Only aggregate slice
counts and identity-intersection counts leave the adapter.

No public inventory evidence guarantees that this small fixed pair contains
all required slices.  Production access is therefore blocked upstream until
the all-32 label discovery contract yields a separately sealed cohort.  This
adapter remains to test restricted-archive, privacy, completeness, and metric
behavior with synthetic fixtures.

No ambient path is accepted here.  Callers must provide already validated,
read-only merged ZIP descriptors after the one-shot access transaction exists.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import hmac
import math
from pathlib import PurePosixPath
from typing import BinaryIO, Protocol
from zipfile import BadZipFile, ZipFile, ZipInfo

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
    _restricted_identity_set,
    _validate_identity_key,
    _validate_mp4_envelope,
    _validate_wav_envelope,
)
from ..models.aihub538_training_evidence import (
    AIHUB538_TRAINING_ANGLES,
    Aihub538IdentityIntersectionReceipt,
    Aihub538RestrictedIdentitySet,
)
from ..models.integrity import (
    require_exact_keys,
    require_integer,
    require_mapping,
    require_sha256,
    sha256_document,
)
from ..models.p8_locked_evaluation import P8ReadZeroSeal


_LABEL_ROLE_COMPONENT = "VL11"
_SOURCE_ROLE_COMPONENT = "VS11"
_REFERENCE_ROLES = ("p6_calibration", "ts174_training")
_ENVIRONMENTS = ("J", "K")
_NOISE_CODES = tuple(range(1, 7))
_MAXIMUM_LABEL_BYTES = 32 * 1024 * 1024


class Aihub538LockedDatasetError(RuntimeError):
    """Path-free failure raised at the locked dataset boundary."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class P8LockedEvaluationCompletenessError(Aihub538LockedDatasetError):
    """Aggregate-safe proof that every admitted item failed evaluation."""

    def __init__(self, *, admitted_item_count: int) -> None:
        if (
            isinstance(admitted_item_count, bool)
            or not isinstance(admitted_item_count, int)
            or admitted_item_count <= 0
        ):
            raise ValueError("p8_locked_failure_completeness_count_invalid")
        self.admitted_item_count = admitted_item_count
        self.paired_item_count = 0
        self.evaluated_item_count = 0
        self.evaluation_exclusion_count = admitted_item_count
        super().__init__("p8_locked_no_evaluable_items")


def p8_frozen_pipeline_pins_sha256(seal: P8ReadZeroSeal) -> str:
    """Digest every pipeline edge that the read-zero seal froze."""

    if not isinstance(seal, P8ReadZeroSeal):
        raise Aihub538LockedDatasetError("p8_locked_read_zero_seal_invalid")
    return sha256_document(
        {
            "schema_version": "voice_coach.p8_frozen_pipeline_pins.v2",
            "protocol_revision": seal.protocol_revision,
            "p6_evidence_receipt_sha256": seal.p6_evidence_receipt_sha256,
            "calibration_artifact_sha256": seal.calibration_artifact_sha256,
            "claim_catalog_sha256": seal.claim_catalog_sha256,
            "claim_policy_sha256": seal.claim_policy_sha256,
            "decoder_code_sha256": seal.decoder_code_sha256,
            "tracker_code_sha256": seal.tracker_code_sha256,
            "tracker_adapter_code_sha256": seal.tracker_adapter_code_sha256,
            "landmark_model_sha256": seal.landmark_model_sha256,
            "normalizer_code_sha256": seal.normalizer_code_sha256,
            "cue_extractor_code_sha256": seal.cue_extractor_code_sha256,
            "video_qc_measurement_code_sha256": (
                seal.video_qc_measurement_code_sha256
            ),
            "video_qc_measurement_policy_sha256": (
                seal.video_qc_measurement_policy_sha256
            ),
            "video_qc_decision_code_sha256": (
                seal.video_qc_decision_code_sha256
            ),
            "video_qc_decision_policy_revision": (
                seal.video_qc_decision_policy_revision
            ),
            "video_qc_decision_policy_sha256": (
                seal.video_qc_decision_policy_sha256
            ),
            "av_sync_measurement_code_sha256": (
                seal.av_sync_measurement_code_sha256
            ),
            "av_sync_measurement_policy_sha256": (
                seal.av_sync_measurement_policy_sha256
            ),
            "av_sync_decision_code_sha256": seal.av_sync_decision_code_sha256,
            "av_sync_decision_policy_revision": (
                seal.av_sync_decision_policy_revision
            ),
            "av_sync_decision_policy_sha256": (
                seal.av_sync_decision_policy_sha256
            ),
            "phone_window_code_sha256": seal.phone_window_code_sha256,
            "phone_window_policy_revision": seal.phone_window_policy_revision,
            "phone_window_policy_sha256": seal.phone_window_policy_sha256,
            "evaluation_pipeline_code_sha256": (
                seal.evaluation_pipeline_code_sha256
            ),
            "source_evaluator_code_sha256": (
                seal.source_evaluator_code_sha256
            ),
            "media_engine_code_sha256": seal.media_engine_code_sha256,
        }
    )


@dataclass(frozen=True, slots=True)
class P8RestrictedReferencePopulation:
    """Pre-access, non-serializable identity population for overlap checks."""

    role: str
    manifest_sha256: str
    identities: Aihub538RestrictedIdentitySet = field(repr=False)

    def __post_init__(self) -> None:
        if self.role not in _REFERENCE_ROLES:
            raise ValueError("p8_locked_reference_identity_role_invalid")
        require_sha256(self.manifest_sha256, "manifest_sha256")
        if not isinstance(self.identities, Aihub538RestrictedIdentitySet):
            raise ValueError("p8_locked_reference_identity_set_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class P8LockedItemHandle:
    """Private stream handles for exactly one admitted label/video/audio pair."""

    _item_binding_sha256: str
    angle: str
    noise_code: int
    environment: str
    _label_archive: ZipFile = field(repr=False)
    _source_archive: ZipFile = field(repr=False)
    _label_info: ZipInfo = field(repr=False)
    _video_info: ZipInfo = field(repr=False)
    _audio_info: ZipInfo = field(repr=False)

    @property
    def item_binding_sha256(self) -> str:
        """Private exactly-once binding; callers must never serialize it."""

        return self._item_binding_sha256

    @contextmanager
    def open_label(self) -> Iterator[BinaryIO]:
        with self._label_archive.open(self._label_info, "r") as source:
            yield source

    @contextmanager
    def open_video(self) -> Iterator[BinaryIO]:
        with self._source_archive.open(self._video_info, "r") as source:
            yield source

    @contextmanager
    def open_audio(self) -> Iterator[BinaryIO]:
        with self._source_archive.open(self._audio_info, "r") as source:
            yield source


@dataclass(frozen=True, slots=True, repr=False)
class P8LockedItemEvaluation:
    """Private per-item result issued by the exact frozen pipeline."""

    item_binding_sha256: str = field(repr=False)
    pipeline_pins_sha256: str
    decode_succeeded: bool
    qc_decision_lineage_valid: bool
    supported_clip: bool
    single_face_coverage: float
    av_anchor_available: bool
    ready: bool
    sync_decision_lineage_valid: bool
    absolute_sync_offset_ms: float | None
    evaluation_succeeded: bool

    def __post_init__(self) -> None:
        require_sha256(self.item_binding_sha256, "item_binding_sha256")
        require_sha256(self.pipeline_pins_sha256, "pipeline_pins_sha256")
        for name in (
            "decode_succeeded",
            "qc_decision_lineage_valid",
            "supported_clip",
            "av_anchor_available",
            "ready",
            "sync_decision_lineage_valid",
            "evaluation_succeeded",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError("p8_locked_item_boolean_invalid")
        if (
            isinstance(self.single_face_coverage, bool)
            or not isinstance(self.single_face_coverage, (int, float))
            or not math.isfinite(float(self.single_face_coverage))
            or not 0.0 <= float(self.single_face_coverage) <= 1.0
        ):
            raise ValueError("p8_locked_single_face_coverage_invalid")
        if self.absolute_sync_offset_ms is not None and (
            isinstance(self.absolute_sync_offset_ms, bool)
            or not isinstance(self.absolute_sync_offset_ms, (int, float))
            or not math.isfinite(float(self.absolute_sync_offset_ms))
            or float(self.absolute_sync_offset_ms) < 0.0
        ):
            raise ValueError("p8_locked_sync_offset_invalid")
        if self.ready and (
            not self.av_anchor_available
            or not self.sync_decision_lineage_valid
            or self.absolute_sync_offset_ms is None
        ):
            raise ValueError("p8_locked_ready_lineage_incomplete")


@dataclass(frozen=True, slots=True)
class P8ClaimPolicyReplayReceipt:
    """Self-digested exhaustive case×claim and forbidden-fixture replay."""

    pipeline_pins_sha256: str
    claim_catalog_sha256: str
    claim_policy_sha256: str
    forbidden_fixture_catalog_sha256: str
    evaluated_case_count: int
    catalog_claim_count: int
    catalog_claim_replayed_count: int
    expected_case_claim_pair_count: int
    replayed_case_claim_pair_count: int
    forbidden_fixture_count: int
    forbidden_fixture_replayed_count: int
    claim_policy_violation_count: int
    forbidden_claim_fixture_violation_count: int
    receipt_sha256: str

    @classmethod
    def issue(
        cls,
        *,
        pipeline_pins_sha256: str,
        claim_catalog_sha256: str,
        claim_policy_sha256: str,
        forbidden_fixture_catalog_sha256: str,
        evaluated_case_count: int,
        catalog_claim_count: int,
        catalog_claim_replayed_count: int,
        expected_case_claim_pair_count: int,
        replayed_case_claim_pair_count: int,
        forbidden_fixture_count: int,
        forbidden_fixture_replayed_count: int,
        claim_policy_violation_count: int,
        forbidden_claim_fixture_violation_count: int,
    ) -> "P8ClaimPolicyReplayReceipt":
        values = {
            "pipeline_pins_sha256": pipeline_pins_sha256,
            "claim_catalog_sha256": claim_catalog_sha256,
            "claim_policy_sha256": claim_policy_sha256,
            "forbidden_fixture_catalog_sha256": (
                forbidden_fixture_catalog_sha256
            ),
            "evaluated_case_count": evaluated_case_count,
            "catalog_claim_count": catalog_claim_count,
            "catalog_claim_replayed_count": catalog_claim_replayed_count,
            "expected_case_claim_pair_count": (
                expected_case_claim_pair_count
            ),
            "replayed_case_claim_pair_count": (
                replayed_case_claim_pair_count
            ),
            "forbidden_fixture_count": forbidden_fixture_count,
            "forbidden_fixture_replayed_count": (
                forbidden_fixture_replayed_count
            ),
            "claim_policy_violation_count": claim_policy_violation_count,
            "forbidden_claim_fixture_violation_count": (
                forbidden_claim_fixture_violation_count
            ),
        }
        unsigned = cls._unsigned_from_values(values)
        return cls(**values, receipt_sha256=sha256_document(unsigned))

    def __post_init__(self) -> None:
        for name in (
            "pipeline_pins_sha256",
            "claim_catalog_sha256",
            "claim_policy_sha256",
            "forbidden_fixture_catalog_sha256",
            "receipt_sha256",
        ):
            require_sha256(getattr(self, name), name)
        counts = tuple(
            getattr(self, name)
            for name in self.__dataclass_fields__
            if not name.endswith("sha256")
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in counts
        ):
            raise ValueError("p8_locked_claim_replay_count_invalid")
        expected_pairs = self.evaluated_case_count * self.catalog_claim_count
        if (
            self.evaluated_case_count <= 0
            or self.catalog_claim_count <= 0
            or self.forbidden_fixture_count <= 0
            or self.catalog_claim_replayed_count != self.catalog_claim_count
            or self.expected_case_claim_pair_count != expected_pairs
            or self.replayed_case_claim_pair_count != expected_pairs
            or self.forbidden_fixture_replayed_count
            != self.forbidden_fixture_count
        ):
            raise ValueError("p8_locked_claim_replay_incomplete")
        if self.receipt_sha256 != sha256_document(self._unsigned_json_object()):
            raise ValueError("p8_locked_claim_replay_digest_invalid")

    def to_json_object(self) -> dict[str, object]:
        return {
            **self._unsigned_json_object(),
            "receipt_sha256": self.receipt_sha256,
        }

    @classmethod
    def from_json_object(cls, value: object) -> "P8ClaimPolicyReplayReceipt":
        document = require_mapping(value, "p8_claim_policy_replay_receipt")
        count_names = {
            "evaluated_case_count",
            "catalog_claim_count",
            "catalog_claim_replayed_count",
            "expected_case_claim_pair_count",
            "replayed_case_claim_pair_count",
            "forbidden_fixture_count",
            "forbidden_fixture_replayed_count",
            "claim_policy_violation_count",
            "forbidden_claim_fixture_violation_count",
        }
        digest_names = {
            "pipeline_pins_sha256",
            "claim_catalog_sha256",
            "claim_policy_sha256",
            "forbidden_fixture_catalog_sha256",
            "receipt_sha256",
        }
        constant_names = {
            "schema_version",
            "status",
            "case_claim_cartesian_product_validated",
            "raw_claim_payloads_serialized",
            "per_case_outcomes_serialized",
            "aggregate_only",
        }
        require_exact_keys(
            document,
            count_names | digest_names | constant_names,
            "p8_claim_policy_replay_receipt",
        )
        if (
            document["schema_version"]
            != "voice_coach.p8_claim_policy_replay_receipt.v1"
            or document["status"] != "exhaustive_typed_replay_completed"
            or document["case_claim_cartesian_product_validated"] is not True
            or document["raw_claim_payloads_serialized"] is not False
            or document["per_case_outcomes_serialized"] is not False
            or document["aggregate_only"] is not True
        ):
            raise ValueError("p8_locked_claim_replay_contract_invalid")
        return cls(
            **{
                name: require_sha256(document[name], name)
                for name in digest_names
            },
            **{
                name: require_integer(document[name], name, minimum=0)
                for name in count_names
            },
        )

    def _unsigned_json_object(self) -> dict[str, object]:
        values = {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
            if name != "receipt_sha256"
        }
        return self._unsigned_from_values(values)

    @staticmethod
    def _unsigned_from_values(
        values: dict[str, object],
    ) -> dict[str, object]:
        return {
            "schema_version": "voice_coach.p8_claim_policy_replay_receipt.v1",
            "status": "exhaustive_typed_replay_completed",
            **values,
            "case_claim_cartesian_product_validated": True,
            "raw_claim_payloads_serialized": False,
            "per_case_outcomes_serialized": False,
            "aggregate_only": True,
        }


# Temporary source compatibility for the concrete adapter while all production
# callers move to the explicit typed receipt name.
P8ClaimPolicyReplayAggregate = P8ClaimPolicyReplayReceipt


class P8FrozenLockedPipeline(Protocol):
    """Frozen pipeline port; concrete runtime owns all media processing."""

    @property
    def pipeline_pins_sha256(self) -> str: ...

    def evaluate(self, item: P8LockedItemHandle) -> P8LockedItemEvaluation: ...

    def replay_claim_policy(
        self, *, evaluated_case_count: int
    ) -> P8ClaimPolicyReplayReceipt: ...


@dataclass(frozen=True, slots=True)
class P8LockedSliceAggregate:
    """Public engineering counts for one non-identifying metadata slice."""

    slice_kind: str
    slice_value: str
    admitted_count: int
    evaluated_count: int
    successful_count: int
    decode_success_count: int
    supported_count: int
    av_anchor_available_count: int
    ready_count: int
    qc_lineage_valid_count: int
    ready_sync_lineage_valid_count: int

    def __post_init__(self) -> None:
        allowed = {
            "angle": set(AIHUB538_TRAINING_ANGLES),
            "noise": {str(value) for value in _NOISE_CODES},
            "environment": set(_ENVIRONMENTS),
        }
        if self.slice_kind not in allowed or self.slice_value not in allowed[
            self.slice_kind
        ]:
            raise ValueError("p8_locked_slice_identifier_invalid")
        counts = tuple(
            getattr(self, name)
            for name in self.__dataclass_fields__
            if name not in {"slice_kind", "slice_value"}
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in counts
        ):
            raise ValueError("p8_locked_slice_count_invalid")
        if (
            self.evaluated_count > self.admitted_count
            or any(
                value > self.evaluated_count
                for value in (
                    self.successful_count,
                    self.decode_success_count,
                    self.supported_count,
                    self.av_anchor_available_count,
                    self.ready_count,
                    self.qc_lineage_valid_count,
                    self.ready_sync_lineage_valid_count,
                )
            )
            or self.ready_sync_lineage_valid_count != self.ready_count
        ):
            raise ValueError("p8_locked_slice_count_invalid")

    def to_json_object(self) -> dict[str, object]:
        return {
            name: getattr(self, name) for name in self.__dataclass_fields__
        }


@dataclass(frozen=True, slots=True)
class P8LockedDatasetEvaluationAggregate:
    """Aggregate-only result retaining no per-item digest or private value."""

    label_archive_sha256: str
    source_archive_sha256: str
    label_content_root_sha256: str
    source_content_root_sha256: str
    locked_identity_manifest_sha256: str
    identity_namespace_commitment_sha256: str
    identity_intersections: tuple[
        Aihub538IdentityIntersectionReceipt,
        Aihub538IdentityIntersectionReceipt,
    ]
    admitted_item_count: int
    paired_item_count: int
    evaluated_item_count: int
    evaluation_exclusion_count: int
    successful_item_count: int
    failed_item_count: int
    qc_decision_lineage_valid_item_count: int
    decode_success_rate: float
    supported_clip_count: int
    supported_clip_below_single_face_threshold_count: int
    aggregate_supported_slice_coverage: float
    av_anchor_availability_rate: float
    ready_case_count: int
    ready_sync_decision_lineage_valid_case_count: int
    maximum_ready_absolute_sync_offset_ms: float
    claim_replay: P8ClaimPolicyReplayAggregate
    slice_aggregates: tuple[P8LockedSliceAggregate, ...]

    def __post_init__(self) -> None:
        for name in (
            "label_archive_sha256",
            "source_archive_sha256",
            "label_content_root_sha256",
            "source_content_root_sha256",
            "locked_identity_manifest_sha256",
            "identity_namespace_commitment_sha256",
        ):
            require_sha256(getattr(self, name), name)
        if len(self.identity_intersections) != len(_REFERENCE_ROLES) or any(
            not item.is_disjoint
            or item.right_manifest_sha256
            != self.locked_identity_manifest_sha256
            or item.namespace_commitment_sha256
            != self.identity_namespace_commitment_sha256
            for item in self.identity_intersections
        ):
            raise ValueError("p8_locked_identity_intersection_invalid")
        integer_counts = tuple(
            getattr(self, name)
            for name in (
                "admitted_item_count",
                "paired_item_count",
                "evaluated_item_count",
                "evaluation_exclusion_count",
                "successful_item_count",
                "failed_item_count",
                "qc_decision_lineage_valid_item_count",
                "supported_clip_count",
                "supported_clip_below_single_face_threshold_count",
                "ready_case_count",
                "ready_sync_decision_lineage_valid_case_count",
            )
        )
        if (
            any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                for value in integer_counts
            )
            or self.admitted_item_count <= 0
            or self.evaluated_item_count <= 0
            or self.supported_clip_count <= 0
            or self.ready_case_count <= 0
        ):
            raise ValueError("p8_locked_evaluation_count_invalid")
        if self.admitted_item_count != (
            self.paired_item_count + self.evaluation_exclusion_count
        ) or self.paired_item_count != self.evaluated_item_count:
            raise ValueError("p8_locked_evaluation_completeness_invalid")
        if self.successful_item_count + self.failed_item_count != (
            self.evaluated_item_count
        ):
            raise ValueError("p8_locked_evaluation_count_invalid")
        if self.qc_decision_lineage_valid_item_count != self.evaluated_item_count:
            raise ValueError("p8_locked_qc_lineage_incomplete")
        if self.ready_sync_decision_lineage_valid_case_count != self.ready_case_count:
            raise ValueError("p8_locked_sync_lineage_incomplete")
        if self.claim_replay.evaluated_case_count != self.evaluated_item_count:
            raise ValueError("p8_locked_claim_replay_population_mismatch")
        if (
            self.supported_clip_count > self.evaluated_item_count
            or self.supported_clip_below_single_face_threshold_count
            > self.supported_clip_count
            or self.ready_case_count > self.evaluated_item_count
        ):
            raise ValueError("p8_locked_evaluation_count_invalid")
        for value in (
            self.decode_success_rate,
            self.aggregate_supported_slice_coverage,
            self.av_anchor_availability_rate,
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0
            ):
                raise ValueError("p8_locked_evaluation_rate_invalid")
        if (
            isinstance(self.maximum_ready_absolute_sync_offset_ms, bool)
            or not isinstance(
                self.maximum_ready_absolute_sync_offset_ms, (int, float)
            )
            or not math.isfinite(
                float(self.maximum_ready_absolute_sync_offset_ms)
            )
            or self.maximum_ready_absolute_sync_offset_ms < 0.0
        ):
            raise ValueError("p8_locked_sync_offset_invalid")
        self._validate_slice_totals()

    def _validate_slice_totals(self) -> None:
        expected_keys = (
            tuple(("angle", value) for value in AIHUB538_TRAINING_ANGLES)
            + tuple(("noise", str(value)) for value in _NOISE_CODES)
            + tuple(("environment", value) for value in _ENVIRONMENTS)
        )
        if tuple(
            (item.slice_kind, item.slice_value)
            for item in self.slice_aggregates
        ) != expected_keys:
            raise ValueError("p8_locked_slice_coverage_incomplete")
        angle_slices = tuple(
            item
            for item in self.slice_aggregates
            if item.slice_kind == "angle"
        )
        decode_success_count = sum(
            item.decode_success_count for item in angle_slices
        )
        av_anchor_available_count = sum(
            item.av_anchor_available_count for item in angle_slices
        )
        for slice_kind in ("angle", "noise", "environment"):
            slices = tuple(
                item
                for item in self.slice_aggregates
                if item.slice_kind == slice_kind
            )
            expected_totals = {
                "admitted_count": self.admitted_item_count,
                "evaluated_count": self.evaluated_item_count,
                "successful_count": self.successful_item_count,
                "decode_success_count": decode_success_count,
                "supported_count": self.supported_clip_count,
                "av_anchor_available_count": av_anchor_available_count,
                "ready_count": self.ready_case_count,
                "qc_lineage_valid_count": (
                    self.qc_decision_lineage_valid_item_count
                ),
                "ready_sync_lineage_valid_count": (
                    self.ready_sync_decision_lineage_valid_case_count
                ),
            }
            if any(
                sum(getattr(item, field_name) for item in slices)
                != expected_total
                for field_name, expected_total in expected_totals.items()
            ):
                raise ValueError("p8_locked_slice_aggregate_totals_invalid")
        if not math.isclose(
            self.decode_success_rate,
            decode_success_count / self.evaluated_item_count,
            rel_tol=0.0,
            abs_tol=1e-12,
        ) or not math.isclose(
            self.av_anchor_availability_rate,
            av_anchor_available_count / self.evaluated_item_count,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("p8_locked_slice_rate_metric_invalid")
        observed_coverages = tuple(
            (
                item.supported_count / item.evaluated_count
                if item.evaluated_count > 0
                else 0.0
            )
            for item in self.slice_aggregates
        )
        if not math.isclose(
            self.aggregate_supported_slice_coverage,
            min(observed_coverages),
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("p8_locked_slice_coverage_metric_invalid")

    def to_aggregate_json_object(self) -> dict[str, object]:
        """Return lineage-safe aggregates; individual item digests are absent."""

        return {
            "schema_version": (
                "voice_coach.aihub538_p8_locked_dataset_evaluation.v1"
            ),
            "status": "all_admitted_items_accounted_for",
            "label_archive_sha256": self.label_archive_sha256,
            "source_archive_sha256": self.source_archive_sha256,
            "label_content_root_sha256": self.label_content_root_sha256,
            "source_content_root_sha256": self.source_content_root_sha256,
            "locked_identity_manifest_sha256": (
                self.locked_identity_manifest_sha256
            ),
            "identity_namespace_commitment_sha256": (
                self.identity_namespace_commitment_sha256
            ),
            "identity_intersections": [
                {
                    "reference_role": role,
                    "intersection": item.to_json_object(),
                }
                for role, item in zip(
                    _REFERENCE_ROLES,
                    self.identity_intersections,
                    strict=True,
                )
            ],
            "admitted_item_count": self.admitted_item_count,
            "paired_item_count": self.paired_item_count,
            "evaluated_item_count": self.evaluated_item_count,
            "evaluation_exclusion_count": self.evaluation_exclusion_count,
            "successful_item_count": self.successful_item_count,
            "failed_item_count": self.failed_item_count,
            "qc_decision_lineage_valid_item_count": (
                self.qc_decision_lineage_valid_item_count
            ),
            "decode_success_rate": self.decode_success_rate,
            "supported_clip_count": self.supported_clip_count,
            "supported_clip_below_single_face_threshold_count": (
                self.supported_clip_below_single_face_threshold_count
            ),
            "aggregate_supported_slice_coverage": (
                self.aggregate_supported_slice_coverage
            ),
            "av_anchor_availability_rate": (
                self.av_anchor_availability_rate
            ),
            "ready_case_count": self.ready_case_count,
            "ready_sync_decision_lineage_valid_case_count": (
                self.ready_sync_decision_lineage_valid_case_count
            ),
            "maximum_ready_absolute_sync_offset_ms": (
                self.maximum_ready_absolute_sync_offset_ms
            ),
            "slice_aggregates": [
                item.to_json_object() for item in self.slice_aggregates
            ],
            "claim_replay": self.claim_replay.to_json_object(),
            "source_paths_serialized": False,
            "member_names_serialized": False,
            "speaker_identifiers_serialized": False,
            "transcripts_serialized": False,
            "sample_digests_serialized": False,
            "individual_identity_digests_serialized": False,
            "per_item_outcomes_serialized": False,
            "aggregate_only": True,
        }


@dataclass(frozen=True, slots=True, repr=False)
class _PrivateAdmittedItem:
    handle: P8LockedItemHandle
    speaker_id: str = field(repr=False)
    session_id: str = field(repr=False)


class Aihub538LockedDatasetEvaluator:
    """Fixture-evaluate a VS11/VL11-shaped exact archive pair."""

    def __init__(
        self,
        *,
        archive_policy: Aihub538ArchiveSecurityPolicy = (
            DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY
        ),
    ) -> None:
        self._archive_policy = archive_policy

    def evaluate(
        self,
        *,
        label_zip_descriptor: int,
        source_zip_descriptor: int,
        sealed_references: object,
        frozen_pipeline: P8FrozenLockedPipeline,
        seal: P8ReadZeroSeal,
    ) -> P8LockedDatasetEvaluationAggregate:
        """Evaluate every exact pair or account for it as one exclusion."""

        expected_pins = p8_frozen_pipeline_pins_sha256(seal)
        if (
            getattr(frozen_pipeline, "pipeline_pins_sha256", None)
            != expected_pins
        ):
            raise Aihub538LockedDatasetError(
                "p8_locked_frozen_pipeline_pin_mismatch"
            )
        # Local import avoids an import cycle: the sealed-bundle factory uses
        # ``P8RestrictedReferencePopulation`` as its private legacy-domain
        # intersection representation.
        from .p8_reference_identity import P8SealedReferenceBundle

        if type(sealed_references) is not P8SealedReferenceBundle:
            raise Aihub538LockedDatasetError(
                "p8_locked_sealed_reference_bundle_required"
            )
        identity_material = (
            sealed_references._identity_key_for_locked_evaluation()
        )
        reference_populations = (
            sealed_references._populations_for_locked_evaluation()
        )
        identity_hash_key = identity_material.key
        _validate_references(reference_populations)
        try:
            _validate_identity_key(identity_hash_key)
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
                    role_component=_LABEL_ROLE_COMPONENT,
                    allowed_suffixes={".json"},
                )
                source_index = _member_index(
                    infos=source_infos,
                    names=source_names,
                    role_component=_SOURCE_ROLE_COMPONENT,
                    allowed_suffixes={".mp4", ".wav"},
                )
                return self._evaluate_opened(
                    label_zip=label_zip,
                    source_zip=source_zip,
                    label_members=label_index.members,
                    source_members=source_index.members,
                    label_archive_sha256=label_receipt.archive_sha256,
                    source_archive_sha256=source_receipt.archive_sha256,
                    label_content_root_sha256=label_receipt.content_root_sha256,
                    source_content_root_sha256=source_receipt.content_root_sha256,
                    identity_hash_key=identity_hash_key,
                    reference_populations=reference_populations,
                    frozen_pipeline=frozen_pipeline,
                    seal=seal,
                    expected_pins=expected_pins,
                )
        except Aihub538LockedDatasetError:
            raise
        except Aihub538TrainingAdmissionError as error:
            raise Aihub538LockedDatasetError(error.reason_code) from None
        except Aihub538ArchiveError as error:
            raise Aihub538LockedDatasetError(error.reason_code) from None
        except (BadZipFile, KeyError, OSError, RuntimeError, ValueError):
            raise Aihub538LockedDatasetError(
                "p8_locked_archive_schema_or_evaluation_invalid"
            ) from None

    def _evaluate_opened(
        self,
        *,
        label_zip: ZipFile,
        source_zip: ZipFile,
        label_members: dict[str, ZipInfo],
        source_members: dict[str, ZipInfo],
        label_archive_sha256: str,
        source_archive_sha256: str,
        label_content_root_sha256: str,
        source_content_root_sha256: str,
        identity_hash_key: bytes,
        reference_populations: tuple[
            P8RestrictedReferencePopulation,
            P8RestrictedReferencePopulation,
        ],
        frozen_pipeline: P8FrozenLockedPipeline,
        seal: P8ReadZeroSeal,
        expected_pins: str,
    ) -> P8LockedDatasetEvaluationAggregate:
        admitted: list[_PrivateAdmittedItem] = []
        expected_video_keys: set[str] = set()
        expected_audio_keys: set[str] = set()
        for label_key in sorted(label_members):
            label_info = label_members[label_key]
            if not 0 < label_info.file_size <= _MAXIMUM_LABEL_BYTES:
                raise Aihub538LockedDatasetError(
                    "p8_locked_label_size_outside_policy"
                )
            label_payload = label_zip.read(label_info)
            private_label = _parse_label_payload(label_payload)
            label_relative = PurePosixPath(label_key)
            if label_relative.stem != private_label.session_stem:
                raise Aihub538LockedDatasetError(
                    "p8_locked_label_name_mismatch"
                )
            video_key = label_relative.with_suffix(".mp4").as_posix()
            a_audio_stem = _a_view_stem(private_label.session_stem)
            audio_key = label_relative.with_name(
                f"{a_audio_stem}.wav"
            ).as_posix()
            try:
                video_info = source_members[video_key]
                audio_info = source_members[audio_key]
            except KeyError:
                raise Aihub538LockedDatasetError(
                    "p8_locked_exact_media_pairing_failed"
                ) from None
            if (
                private_label.video_name != f"{private_label.session_stem}.mp4"
                or private_label.audio_name
                != f"{private_label.session_stem}.wav"
            ):
                raise Aihub538LockedDatasetError(
                    "p8_locked_metadata_media_name_mismatch"
                )
            _validate_mp4_envelope(source_zip, video_info)
            _validate_wav_envelope(source_zip, audio_info)
            item_binding_sha256 = hmac.new(
                identity_hash_key,
                b"voice_coach.aihub538.p8.item_binding.v1\x00"
                + label_key.encode("utf-8")
                + b"\x00"
                + bytes.fromhex(private_label.label_payload_sha256)
                + video_info.CRC.to_bytes(4, "big")
                + audio_info.CRC.to_bytes(4, "big"),
                hashlib.sha256,
            ).hexdigest()
            admitted.append(
                _PrivateAdmittedItem(
                    handle=P8LockedItemHandle(
                        _item_binding_sha256=item_binding_sha256,
                        angle=private_label.angle,
                        noise_code=private_label.noise_code,
                        environment=private_label.environment,
                        _label_archive=label_zip,
                        _source_archive=source_zip,
                        _label_info=label_info,
                        _video_info=video_info,
                        _audio_info=audio_info,
                    ),
                    speaker_id=private_label.speaker_id,
                    session_id=_a_view_stem(private_label.session_stem),
                )
            )
            expected_video_keys.add(video_key)
            expected_audio_keys.add(audio_key)

        actual_video_keys = {
            key for key in source_members if PurePosixPath(key).suffix == ".mp4"
        }
        actual_audio_keys = {
            key for key in source_members if PurePosixPath(key).suffix == ".wav"
        }
        if (
            not admitted
            or expected_video_keys != actual_video_keys
            or expected_audio_keys != actual_audio_keys
            or len(admitted) != len(label_members)
        ):
            raise Aihub538LockedDatasetError(
                "p8_locked_exact_media_pairing_failed"
            )
        _validate_admitted_slice_coverage(admitted)

        locked_identities = _restricted_identity_set(
            identity_hash_key=identity_hash_key,
            speaker_ids={item.speaker_id for item in admitted},
            session_ids={item.session_id for item in admitted},
        )
        locked_identity_manifest_sha256 = sha256_document(
            {
                "schema_version": (
                    "voice_coach.aihub538_p8_locked_identity_manifest.v1"
                ),
                "label_content_root_sha256": label_content_root_sha256,
                "source_content_root_sha256": source_content_root_sha256,
                "admitted_item_count": len(admitted),
                "speaker_count": len(locked_identities.speaker_hmac_sha256),
                "session_count": len(locked_identities.session_hmac_sha256),
                "identity_namespace_commitment_sha256": (
                    locked_identities.namespace_commitment_sha256
                ),
                "individual_identity_digests_serialized": False,
            }
        )
        intersections = tuple(
            Aihub538IdentityIntersectionReceipt.compare(
                left_manifest_sha256=reference.manifest_sha256,
                right_manifest_sha256=locked_identity_manifest_sha256,
                left=reference.identities,
                right=locked_identities,
            )
            for reference in reference_populations
        )
        if any(not receipt.is_disjoint for receipt in intersections):
            raise Aihub538LockedDatasetError(
                "p8_locked_identity_overlap_detected"
            )

        evaluated: list[tuple[P8LockedItemHandle, P8LockedItemEvaluation]] = []
        exclusions = 0
        for item in admitted:
            try:
                result = frozen_pipeline.evaluate(item.handle)
                if (
                    not isinstance(result, P8LockedItemEvaluation)
                    or result.item_binding_sha256
                    != item.handle.item_binding_sha256
                    or result.pipeline_pins_sha256 != expected_pins
                    or not result.qc_decision_lineage_valid
                    or (result.ready and not result.sync_decision_lineage_valid)
                ):
                    raise ValueError("private locked evaluation lineage differs")
            except Exception:
                exclusions += 1
                continue
            evaluated.append((item.handle, result))
        if not evaluated:
            raise P8LockedEvaluationCompletenessError(
                admitted_item_count=len(admitted)
            )
        try:
            claim_replay = frozen_pipeline.replay_claim_policy(
                evaluated_case_count=len(evaluated)
            )
        except Exception:
            raise Aihub538LockedDatasetError(
                "p8_locked_claim_policy_replay_failed"
            ) from None
        if (
            not isinstance(claim_replay, P8ClaimPolicyReplayAggregate)
            or claim_replay.pipeline_pins_sha256 != expected_pins
            or claim_replay.claim_catalog_sha256 != seal.claim_catalog_sha256
            or claim_replay.claim_policy_sha256 != seal.claim_policy_sha256
            or claim_replay.evaluated_case_count != len(evaluated)
        ):
            raise Aihub538LockedDatasetError(
                "p8_locked_claim_policy_replay_failed"
            )

        supported = [result for _, result in evaluated if result.supported_clip]
        ready = [result for _, result in evaluated if result.ready]
        if not supported or not ready:
            raise Aihub538LockedDatasetError(
                "p8_locked_metric_denominator_unavailable"
            )
        slices = _slice_aggregates(admitted=admitted, evaluated=evaluated)
        observed_slice_coverages = [
            (
                item.supported_count / item.evaluated_count
                if item.evaluated_count > 0
                else 0.0
            )
            for item in slices
        ]
        single_face_minimum = (
            seal.evaluation_thresholds
            .supported_clip_single_face_coverage_minimum
        )
        intersection_pair = (intersections[0], intersections[1])
        return P8LockedDatasetEvaluationAggregate(
            label_archive_sha256=label_archive_sha256,
            source_archive_sha256=source_archive_sha256,
            label_content_root_sha256=label_content_root_sha256,
            source_content_root_sha256=source_content_root_sha256,
            locked_identity_manifest_sha256=locked_identity_manifest_sha256,
            identity_namespace_commitment_sha256=(
                locked_identities.namespace_commitment_sha256
            ),
            identity_intersections=intersection_pair,
            admitted_item_count=len(admitted),
            paired_item_count=len(evaluated),
            evaluated_item_count=len(evaluated),
            evaluation_exclusion_count=exclusions,
            successful_item_count=sum(
                int(result.evaluation_succeeded) for _, result in evaluated
            ),
            failed_item_count=sum(
                int(not result.evaluation_succeeded) for _, result in evaluated
            ),
            qc_decision_lineage_valid_item_count=len(evaluated),
            decode_success_rate=(
                sum(int(result.decode_succeeded) for _, result in evaluated)
                / len(evaluated)
            ),
            supported_clip_count=len(supported),
            supported_clip_below_single_face_threshold_count=sum(
                int(
                    result.supported_clip
                    and result.single_face_coverage
                    < single_face_minimum
                )
                for _, result in evaluated
            ),
            aggregate_supported_slice_coverage=min(observed_slice_coverages),
            av_anchor_availability_rate=(
                sum(int(result.av_anchor_available) for _, result in evaluated)
                / len(evaluated)
            ),
            ready_case_count=len(ready),
            ready_sync_decision_lineage_valid_case_count=len(ready),
            maximum_ready_absolute_sync_offset_ms=max(
                float(result.absolute_sync_offset_ms)
                for result in ready
                if result.absolute_sync_offset_ms is not None
            ),
            claim_replay=claim_replay,
            slice_aggregates=slices,
        )


def _validate_admitted_slice_coverage(
    admitted: Sequence[_PrivateAdmittedItem],
) -> None:
    angles = {item.handle.angle for item in admitted}
    noise_codes = {item.handle.noise_code for item in admitted}
    environments = {item.handle.environment for item in admitted}
    if (
        angles != set(AIHUB538_TRAINING_ANGLES)
        or noise_codes != set(_NOISE_CODES)
        or environments != set(_ENVIRONMENTS)
    ):
        raise Aihub538LockedDatasetError(
            "p8_locked_required_slice_coverage_incomplete"
        )


def _validate_references(
    references: Sequence[P8RestrictedReferencePopulation],
) -> None:
    if (
        tuple(reference.role for reference in references) != _REFERENCE_ROLES
        or len(references) != 2
    ):
        raise Aihub538LockedDatasetError(
            "p8_locked_reference_identity_roles_incomplete"
        )
    namespaces = {
        reference.identities.namespace_commitment_sha256
        for reference in references
    }
    if len(namespaces) != 1:
        raise Aihub538LockedDatasetError(
            "p8_locked_reference_identity_namespace_mismatch"
        )


def _slice_aggregates(
    *,
    admitted: Sequence[_PrivateAdmittedItem],
    evaluated: Sequence[tuple[P8LockedItemHandle, P8LockedItemEvaluation]],
) -> tuple[P8LockedSliceAggregate, ...]:
    admitted_by_slice: dict[tuple[str, str], int] = defaultdict(int)
    evaluated_by_slice: dict[
        tuple[str, str], list[P8LockedItemEvaluation]
    ] = defaultdict(list)

    def keys(handle: P8LockedItemHandle) -> tuple[tuple[str, str], ...]:
        return (
            ("angle", handle.angle),
            ("noise", str(handle.noise_code)),
            ("environment", handle.environment),
        )

    for item in admitted:
        for key in keys(item.handle):
            admitted_by_slice[key] += 1
    for handle, result in evaluated:
        for key in keys(handle):
            evaluated_by_slice[key].append(result)
    ordered_keys = (
        tuple(("angle", value) for value in AIHUB538_TRAINING_ANGLES)
        + tuple(("noise", str(value)) for value in _NOISE_CODES)
        + tuple(("environment", value) for value in _ENVIRONMENTS)
    )
    aggregates: list[P8LockedSliceAggregate] = []
    for slice_kind, slice_value in ordered_keys:
        results = evaluated_by_slice[(slice_kind, slice_value)]
        ready = [result for result in results if result.ready]
        aggregates.append(
            P8LockedSliceAggregate(
                slice_kind=slice_kind,
                slice_value=slice_value,
                admitted_count=admitted_by_slice[(slice_kind, slice_value)],
                evaluated_count=len(results),
                successful_count=sum(
                    int(result.evaluation_succeeded) for result in results
                ),
                decode_success_count=sum(
                    int(result.decode_succeeded) for result in results
                ),
                supported_count=sum(
                    int(result.supported_clip) for result in results
                ),
                av_anchor_available_count=sum(
                    int(result.av_anchor_available) for result in results
                ),
                ready_count=len(ready),
                qc_lineage_valid_count=sum(
                    int(result.qc_decision_lineage_valid) for result in results
                ),
                ready_sync_lineage_valid_count=sum(
                    int(result.sync_decision_lineage_valid) for result in ready
                ),
            )
        )
    return tuple(aggregates)


__all__ = [
    "Aihub538LockedDatasetError",
    "Aihub538LockedDatasetEvaluator",
    "P8ClaimPolicyReplayAggregate",
    "P8ClaimPolicyReplayReceipt",
    "P8FrozenLockedPipeline",
    "P8LockedDatasetEvaluationAggregate",
    "P8LockedItemEvaluation",
    "P8LockedItemHandle",
    "P8LockedEvaluationCompletenessError",
    "P8LockedSliceAggregate",
    "P8RestrictedReferencePopulation",
    "p8_frozen_pipeline_pins_sha256",
]
