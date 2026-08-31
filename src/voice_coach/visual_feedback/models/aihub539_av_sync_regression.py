"""Frozen, aggregate-only AI Hub 539 A/V-sync differential contracts.

The native audio/video relationship is deliberately not a label.  This module
defines a source-independent protocol that tests only whether a backend recovers
the *change* caused by a known offset and affine-drift injection.  Restricted
paths, archive members, transcripts, actor identifiers, anchors, and per-item
media hashes are input-boundary data and never enter the issued evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from .integrity import (
    canonical_json_bytes,
    require_boolean,
    require_exact_keys,
    require_finite_number,
    require_integer,
    require_mapping,
    require_safe_identifier,
    require_sha256,
    sha256_document,
)


AIHUB539_AV_SYNC_REGRESSION_PROTOCOL_SCHEMA_VERSION = (
    "voice_coach.aihub539_av_sync_differential_regression_protocol.v1"
)
AIHUB539_AV_SYNC_TEMPORAL_EVIDENCE_SCHEMA_VERSION = (
    "voice_coach.actual_media_av_sync_temporal_evidence.v1"
)
AIHUB539_AV_SYNC_TEMPORAL_COMPLETION_RECEIPT_SCHEMA_VERSION = (
    "voice_coach.actual_media_av_sync_temporal_completion_receipt.v1"
)
AIHUB539_AV_SYNC_DATASET_ID = 539
AIHUB539_AV_SYNC_DATASET_ROLE = (
    "nonlocked_actual_media_differential_sync_regression"
)

_FROZEN_INJECTION_CASES = (
    {
        "case_id": "offset_positive_120ms",
        "injected_offset_change_ms": 120.0,
        "injected_drift_change_parts_per_million": 0.0,
    },
    {
        "case_id": "offset_negative_120ms",
        "injected_offset_change_ms": -120.0,
        "injected_drift_change_parts_per_million": 0.0,
    },
    {
        "case_id": "affine_positive_60ms_2500ppm",
        "injected_offset_change_ms": 60.0,
        "injected_drift_change_parts_per_million": 2500.0,
    },
    {
        "case_id": "affine_negative_60ms_2500ppm",
        "injected_offset_change_ms": -60.0,
        "injected_drift_change_parts_per_million": -2500.0,
    },
)

_FROZEN_PROTOCOL_UNSIGNED: dict[str, object] = {
    "schema_version": AIHUB539_AV_SYNC_REGRESSION_PROTOCOL_SCHEMA_VERSION,
    "protocol_revision": "aihub539_av_sync_differential_regression_v1",
    "protocol_state": "frozen_pre_observation",
    "dataset_id": AIHUB539_AV_SYNC_DATASET_ID,
    "dataset_role": AIHUB539_AV_SYNC_DATASET_ROLE,
    "evaluation_scope": "known_injected_change_only_non_p8",
    "truth_basis": "injected_offset_and_affine_drift_change_only",
    "native_sync_ground_truth_validated": False,
    "metadata_sync_truth_used": False,
    "injection_cases": [dict(item) for item in _FROZEN_INJECTION_CASES],
    "offset_change_absolute_tolerance_ms": 5.0,
    "drift_change_absolute_tolerance_parts_per_million": 250.0,
    "minimum_anchor_count_per_measurement": 6,
    "minimum_anchor_span_ms": 4000.0,
    "maximum_affine_fit_root_mean_square_error_ms": 3.0,
    "minimum_measurement_pairs_per_case": 1,
    "aggregate_rule": "all_fixed_cases_and_all_measurement_pairs_must_pass",
    "promotion_authorized": False,
    "p8_authorized": False,
    "user_visual_claims_authorized": False,
    "runtime_authorized": False,
}
AIHUB539_AV_SYNC_REGRESSION_PROTOCOL_SHA256 = sha256_document(
    _FROZEN_PROTOCOL_UNSIGNED
)
AIHUB539_AV_SYNC_REGRESSION_PROTOCOL_DOCUMENT: dict[str, object] = {
    **_FROZEN_PROTOCOL_UNSIGNED,
    "protocol_sha256": AIHUB539_AV_SYNC_REGRESSION_PROTOCOL_SHA256,
}

_PROTOCOL_KEYS = set(AIHUB539_AV_SYNC_REGRESSION_PROTOCOL_DOCUMENT)
_INJECTION_CASE_KEYS = {
    "case_id",
    "injected_drift_change_parts_per_million",
    "injected_offset_change_ms",
}
_CASE_AGGREGATE_KEYS = {
    "case_id",
    "injected_drift_change_parts_per_million",
    "injected_offset_change_ms",
    "maximum_absolute_drift_change_error_parts_per_million",
    "maximum_absolute_offset_change_error_ms",
    "maximum_affine_fit_root_mean_square_error_ms",
    "measurement_pair_count",
    "minimum_anchor_count",
    "minimum_anchor_span_ms",
    "passing_measurement_pair_count",
    "thresholds_passed",
}
_PRIVACY_FLAG_NAMES = (
    "raw_paths_serialized",
    "archive_member_names_serialized",
    "transcripts_serialized",
    "actor_identifiers_serialized",
    "per_item_media_hashes_serialized",
)
_AUTHORIZATION_FLAG_NAMES = (
    "promotion_authorized",
    "p8_authorized",
    "p8_promotion_authorized",
    "locked_evaluation_authorized",
    "user_feedback_authorized",
    "user_visual_claims_authorized",
    "user_visual_runtime_authorized",
    "runtime_authorized",
    "runtime_publisher_authorized",
)
_EVIDENCE_KEYS = {
    "actual_media_source_validated_by_core",
    "aggregate_only",
    "anchor_backend_revision",
    "anchor_backend_sha256",
    "archive_member_names_serialized",
    "actor_identifiers_serialized",
    "aggregate_rule",
    "case_aggregates",
    "dataset_id",
    "dataset_role",
    "evaluated_case_count",
    "evidence_sha256",
    "evaluation_scope",
    "injection_backend_revision",
    "injection_backend_sha256",
    "locked_evaluation_authorized",
    "metadata_sync_truth_used",
    "native_sync_ground_truth_validated",
    "p8_authorized",
    "p8_promotion_authorized",
    "per_item_media_hashes_serialized",
    "promotion_authorized",
    "protocol_revision",
    "protocol_sha256",
    "raw_paths_serialized",
    "runtime_authorized",
    "runtime_publisher_authorized",
    "schema_version",
    "source_admission_receipt_sha256",
    "source_aggregate_manifest_sha256",
    "source_run_identity_sha256",
    "status",
    "total_measurement_pair_count",
    "transcripts_serialized",
    "truth_basis",
    "user_feedback_authorized",
    "user_visual_claims_authorized",
    "user_visual_runtime_authorized",
}
_COMPLETION_KEYS = {
    "aggregate_only",
    "archive_member_names_serialized",
    "actor_identifiers_serialized",
    "completed_case_count",
    "evidence_schema_version",
    "evidence_sha256",
    "locked_evaluation_authorized",
    "metadata_sync_truth_used",
    "native_sync_ground_truth_validated",
    "p8_authorized",
    "p8_promotion_authorized",
    "per_item_media_hashes_serialized",
    "promotion_authorized",
    "protocol_sha256",
    "raw_paths_serialized",
    "receipt_sha256",
    "runtime_authorized",
    "runtime_publisher_authorized",
    "schema_version",
    "source_run_identity_sha256",
    "status",
    "total_measurement_pair_count",
    "transcripts_serialized",
    "user_feedback_authorized",
    "user_visual_claims_authorized",
    "user_visual_runtime_authorized",
}


class Aihub539AvSyncRegressionError(ValueError):
    """Stable, privacy-safe failure raised by the differential contract."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class Aihub539AvSyncInjectionCase:
    """One public, pre-observation offset/drift perturbation."""

    case_id: str
    injected_offset_change_ms: float
    injected_drift_change_parts_per_million: float

    def __post_init__(self) -> None:
        try:
            require_safe_identifier(self.case_id, "case_id")
            require_finite_number(
                self.injected_offset_change_ms,
                "injected_offset_change_ms",
            )
            require_finite_number(
                self.injected_drift_change_parts_per_million,
                "injected_drift_change_parts_per_million",
            )
        except Exception as error:
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_protocol_invalid"
            ) from error
        if (
            self.injected_offset_change_ms == 0.0
            and self.injected_drift_change_parts_per_million == 0.0
        ):
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_protocol_invalid"
            )

    def to_json_object(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "injected_offset_change_ms": self.injected_offset_change_ms,
            "injected_drift_change_parts_per_million": (
                self.injected_drift_change_parts_per_million
            ),
        }


@dataclass(frozen=True, slots=True)
class Aihub539AvSyncRegressionProtocol:
    """Exact protocol mirrored by the checked-in frozen JSON config."""

    injection_cases: tuple[Aihub539AvSyncInjectionCase, ...]
    offset_change_absolute_tolerance_ms: float
    drift_change_absolute_tolerance_parts_per_million: float
    minimum_anchor_count_per_measurement: int
    minimum_anchor_span_ms: float
    maximum_affine_fit_root_mean_square_error_ms: float
    minimum_measurement_pairs_per_case: int
    protocol_sha256: str = AIHUB539_AV_SYNC_REGRESSION_PROTOCOL_SHA256
    schema_version: str = AIHUB539_AV_SYNC_REGRESSION_PROTOCOL_SCHEMA_VERSION
    protocol_revision: str = "aihub539_av_sync_differential_regression_v1"
    protocol_state: str = "frozen_pre_observation"
    dataset_id: int = AIHUB539_AV_SYNC_DATASET_ID
    dataset_role: str = AIHUB539_AV_SYNC_DATASET_ROLE
    evaluation_scope: str = "known_injected_change_only_non_p8"
    truth_basis: str = "injected_offset_and_affine_drift_change_only"
    native_sync_ground_truth_validated: bool = False
    metadata_sync_truth_used: bool = False
    aggregate_rule: str = (
        "all_fixed_cases_and_all_measurement_pairs_must_pass"
    )
    promotion_authorized: bool = False
    p8_authorized: bool = False
    user_visual_claims_authorized: bool = False
    runtime_authorized: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.injection_cases, tuple) or any(
            not isinstance(case, Aihub539AvSyncInjectionCase)
            for case in self.injection_cases
        ):
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_protocol_invalid"
            )
        document = self.to_json_object()
        if canonical_json_bytes(document) != canonical_json_bytes(
            AIHUB539_AV_SYNC_REGRESSION_PROTOCOL_DOCUMENT
        ):
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_protocol_invalid"
            )
        unsigned = dict(document)
        observed_sha256 = unsigned.pop("protocol_sha256")
        if sha256_document(unsigned) != observed_sha256:
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_protocol_invalid"
            )

    @classmethod
    def frozen(cls) -> "Aihub539AvSyncRegressionProtocol":
        """Return the only protocol accepted by this revision."""

        return cls(
            injection_cases=tuple(
                Aihub539AvSyncInjectionCase(**item)
                for item in _FROZEN_INJECTION_CASES
            ),
            offset_change_absolute_tolerance_ms=float(
                _FROZEN_PROTOCOL_UNSIGNED[
                    "offset_change_absolute_tolerance_ms"
                ]
            ),
            drift_change_absolute_tolerance_parts_per_million=float(
                _FROZEN_PROTOCOL_UNSIGNED[
                    "drift_change_absolute_tolerance_parts_per_million"
                ]
            ),
            minimum_anchor_count_per_measurement=int(
                _FROZEN_PROTOCOL_UNSIGNED[
                    "minimum_anchor_count_per_measurement"
                ]
            ),
            minimum_anchor_span_ms=float(
                _FROZEN_PROTOCOL_UNSIGNED["minimum_anchor_span_ms"]
            ),
            maximum_affine_fit_root_mean_square_error_ms=float(
                _FROZEN_PROTOCOL_UNSIGNED[
                    "maximum_affine_fit_root_mean_square_error_ms"
                ]
            ),
            minimum_measurement_pairs_per_case=int(
                _FROZEN_PROTOCOL_UNSIGNED[
                    "minimum_measurement_pairs_per_case"
                ]
            ),
        )

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> "Aihub539AvSyncRegressionProtocol":
        """Strictly validate the config, its canonical hash, and all constants."""

        try:
            document = require_mapping(value, "aihub539_av_sync_protocol")
            require_exact_keys(
                document,
                _PROTOCOL_KEYS,
                "aihub539_av_sync_protocol",
            )
            raw_cases = document["injection_cases"]
            if isinstance(raw_cases, (str, bytes)) or not isinstance(
                raw_cases, list
            ):
                raise ValueError
            cases: list[Aihub539AvSyncInjectionCase] = []
            for index, raw_case in enumerate(raw_cases):
                case_document = require_mapping(
                    raw_case,
                    f"injection_cases[{index}]",
                )
                require_exact_keys(
                    case_document,
                    _INJECTION_CASE_KEYS,
                    f"injection_cases[{index}]",
                )
                cases.append(
                    Aihub539AvSyncInjectionCase(
                        case_id=require_safe_identifier(
                            case_document["case_id"],
                            f"injection_cases[{index}].case_id",
                        ),
                        injected_offset_change_ms=require_finite_number(
                            case_document["injected_offset_change_ms"],
                            (
                                f"injection_cases[{index}]."
                                "injected_offset_change_ms"
                            ),
                        ),
                        injected_drift_change_parts_per_million=(
                            require_finite_number(
                                case_document[
                                    "injected_drift_change_parts_per_million"
                                ],
                                (
                                    f"injection_cases[{index}]."
                                    "injected_drift_change_parts_per_million"
                                ),
                            )
                        ),
                    )
                )
            protocol = cls(
                injection_cases=tuple(cases),
                offset_change_absolute_tolerance_ms=require_finite_number(
                    document["offset_change_absolute_tolerance_ms"],
                    "offset_change_absolute_tolerance_ms",
                ),
                drift_change_absolute_tolerance_parts_per_million=(
                    require_finite_number(
                        document[
                            "drift_change_absolute_tolerance_parts_per_million"
                        ],
                        (
                            "drift_change_absolute_tolerance_"
                            "parts_per_million"
                        ),
                    )
                ),
                minimum_anchor_count_per_measurement=require_integer(
                    document["minimum_anchor_count_per_measurement"],
                    "minimum_anchor_count_per_measurement",
                    minimum=2,
                ),
                minimum_anchor_span_ms=require_finite_number(
                    document["minimum_anchor_span_ms"],
                    "minimum_anchor_span_ms",
                ),
                maximum_affine_fit_root_mean_square_error_ms=(
                    require_finite_number(
                        document[
                            "maximum_affine_fit_root_mean_square_error_ms"
                        ],
                        "maximum_affine_fit_root_mean_square_error_ms",
                    )
                ),
                minimum_measurement_pairs_per_case=require_integer(
                    document["minimum_measurement_pairs_per_case"],
                    "minimum_measurement_pairs_per_case",
                    minimum=1,
                ),
                protocol_sha256=require_sha256(
                    document["protocol_sha256"],
                    "protocol_sha256",
                ),
                schema_version=require_safe_identifier(
                    document["schema_version"],
                    "schema_version",
                ),
                protocol_revision=require_safe_identifier(
                    document["protocol_revision"],
                    "protocol_revision",
                ),
                protocol_state=require_safe_identifier(
                    document["protocol_state"],
                    "protocol_state",
                ),
                dataset_id=require_integer(
                    document["dataset_id"], "dataset_id", minimum=1
                ),
                dataset_role=require_safe_identifier(
                    document["dataset_role"], "dataset_role"
                ),
                evaluation_scope=require_safe_identifier(
                    document["evaluation_scope"], "evaluation_scope"
                ),
                truth_basis=require_safe_identifier(
                    document["truth_basis"], "truth_basis"
                ),
                native_sync_ground_truth_validated=require_boolean(
                    document["native_sync_ground_truth_validated"],
                    "native_sync_ground_truth_validated",
                ),
                metadata_sync_truth_used=require_boolean(
                    document["metadata_sync_truth_used"],
                    "metadata_sync_truth_used",
                ),
                aggregate_rule=require_safe_identifier(
                    document["aggregate_rule"], "aggregate_rule"
                ),
                promotion_authorized=require_boolean(
                    document["promotion_authorized"],
                    "promotion_authorized",
                ),
                p8_authorized=require_boolean(
                    document["p8_authorized"], "p8_authorized"
                ),
                user_visual_claims_authorized=require_boolean(
                    document["user_visual_claims_authorized"],
                    "user_visual_claims_authorized",
                ),
                runtime_authorized=require_boolean(
                    document["runtime_authorized"],
                    "runtime_authorized",
                ),
            )
        except Aihub539AvSyncRegressionError:
            raise
        except Exception as error:
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_protocol_invalid"
            ) from error
        if canonical_json_bytes(document) != canonical_json_bytes(
            protocol.to_json_object()
        ):
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_protocol_invalid"
            )
        return protocol

    def to_json_object(self) -> dict[str, object]:
        unsigned = {
            "schema_version": self.schema_version,
            "protocol_revision": self.protocol_revision,
            "protocol_state": self.protocol_state,
            "dataset_id": self.dataset_id,
            "dataset_role": self.dataset_role,
            "evaluation_scope": self.evaluation_scope,
            "truth_basis": self.truth_basis,
            "native_sync_ground_truth_validated": (
                self.native_sync_ground_truth_validated
            ),
            "metadata_sync_truth_used": self.metadata_sync_truth_used,
            "injection_cases": [
                case.to_json_object() for case in self.injection_cases
            ],
            "offset_change_absolute_tolerance_ms": (
                self.offset_change_absolute_tolerance_ms
            ),
            "drift_change_absolute_tolerance_parts_per_million": (
                self.drift_change_absolute_tolerance_parts_per_million
            ),
            "minimum_anchor_count_per_measurement": (
                self.minimum_anchor_count_per_measurement
            ),
            "minimum_anchor_span_ms": self.minimum_anchor_span_ms,
            "maximum_affine_fit_root_mean_square_error_ms": (
                self.maximum_affine_fit_root_mean_square_error_ms
            ),
            "minimum_measurement_pairs_per_case": (
                self.minimum_measurement_pairs_per_case
            ),
            "aggregate_rule": self.aggregate_rule,
            "promotion_authorized": self.promotion_authorized,
            "p8_authorized": self.p8_authorized,
            "user_visual_claims_authorized": (
                self.user_visual_claims_authorized
            ),
            "runtime_authorized": self.runtime_authorized,
        }
        return {**unsigned, "protocol_sha256": self.protocol_sha256}


def parse_aihub539_av_sync_regression_protocol(
    payload: bytes,
) -> Aihub539AvSyncRegressionProtocol:
    """Parse JSON bytes while rejecting duplicate keys and non-frozen values."""

    def reject_duplicate_keys(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        document: dict[str, Any] = {}
        for key, value in pairs:
            if key in document:
                raise Aihub539AvSyncRegressionError(
                    "aihub539_av_sync_protocol_invalid"
                )
            document[key] = value
        return document

    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=reject_duplicate_keys)
    except Aihub539AvSyncRegressionError:
        raise
    except Exception as error:
        raise Aihub539AvSyncRegressionError(
            "aihub539_av_sync_protocol_invalid"
        ) from error
    return Aihub539AvSyncRegressionProtocol.from_json_object(value)


@dataclass(frozen=True, slots=True)
class Aihub539AvSyncRegressionRunIdentity:
    """Aggregate run lineage; it never identifies an individual media item."""

    source_admission_receipt_sha256: str
    source_aggregate_manifest_sha256: str
    anchor_backend_revision: str
    anchor_backend_sha256: str
    injection_backend_revision: str
    injection_backend_sha256: str
    dataset_id: int = AIHUB539_AV_SYNC_DATASET_ID
    dataset_role: str = AIHUB539_AV_SYNC_DATASET_ROLE

    def __post_init__(self) -> None:
        try:
            if self.dataset_id != AIHUB539_AV_SYNC_DATASET_ID:
                raise ValueError
            if self.dataset_role != AIHUB539_AV_SYNC_DATASET_ROLE:
                raise ValueError
            require_sha256(
                self.source_admission_receipt_sha256,
                "source_admission_receipt_sha256",
            )
            require_sha256(
                self.source_aggregate_manifest_sha256,
                "source_aggregate_manifest_sha256",
            )
            require_safe_identifier(
                self.anchor_backend_revision,
                "anchor_backend_revision",
            )
            require_sha256(
                self.anchor_backend_sha256,
                "anchor_backend_sha256",
            )
            require_safe_identifier(
                self.injection_backend_revision,
                "injection_backend_revision",
            )
            require_sha256(
                self.injection_backend_sha256,
                "injection_backend_sha256",
            )
        except Exception as error:
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_run_identity_invalid"
            ) from error

    def to_json_object(self) -> dict[str, object]:
        return {
            "dataset_id": self.dataset_id,
            "dataset_role": self.dataset_role,
            "source_admission_receipt_sha256": (
                self.source_admission_receipt_sha256
            ),
            "source_aggregate_manifest_sha256": (
                self.source_aggregate_manifest_sha256
            ),
            "anchor_backend_revision": self.anchor_backend_revision,
            "anchor_backend_sha256": self.anchor_backend_sha256,
            "injection_backend_revision": self.injection_backend_revision,
            "injection_backend_sha256": self.injection_backend_sha256,
        }

    @property
    def identity_sha256(self) -> str:
        return sha256_document(self.to_json_object())


@dataclass(frozen=True, slots=True)
class Aihub539AvSyncAnchor:
    """One ephemeral affine-fit anchor, never serialized in output evidence."""

    audio_time_ms: float
    video_time_ms: float


@dataclass(frozen=True, slots=True)
class Aihub539AvSyncDifferentialMeasurementPair:
    """Baseline/injected anchors for one private ordinal within a public case."""

    case_id: str
    pair_ordinal: int
    baseline_anchors: tuple[Aihub539AvSyncAnchor, ...]
    injected_anchors: tuple[Aihub539AvSyncAnchor, ...]


@dataclass(frozen=True, slots=True)
class Aihub539AvSyncCaseAggregate:
    """Privacy-safe metrics for one fixed public injection case."""

    case_id: str
    injected_offset_change_ms: float
    injected_drift_change_parts_per_million: float
    measurement_pair_count: int
    passing_measurement_pair_count: int
    maximum_absolute_offset_change_error_ms: float
    maximum_absolute_drift_change_error_parts_per_million: float
    minimum_anchor_count: int
    minimum_anchor_span_ms: float
    maximum_affine_fit_root_mean_square_error_ms: float
    thresholds_passed: bool

    def to_json_object(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "injected_offset_change_ms": self.injected_offset_change_ms,
            "injected_drift_change_parts_per_million": (
                self.injected_drift_change_parts_per_million
            ),
            "measurement_pair_count": self.measurement_pair_count,
            "passing_measurement_pair_count": (
                self.passing_measurement_pair_count
            ),
            "maximum_absolute_offset_change_error_ms": (
                self.maximum_absolute_offset_change_error_ms
            ),
            "maximum_absolute_drift_change_error_parts_per_million": (
                self.maximum_absolute_drift_change_error_parts_per_million
            ),
            "minimum_anchor_count": self.minimum_anchor_count,
            "minimum_anchor_span_ms": self.minimum_anchor_span_ms,
            "maximum_affine_fit_root_mean_square_error_ms": (
                self.maximum_affine_fit_root_mean_square_error_ms
            ),
            "thresholds_passed": self.thresholds_passed,
        }


@dataclass(frozen=True, slots=True)
class Aihub539AvSyncTemporalEvidence:
    """Aggregate differential evidence with no promotion or user authority."""

    source_admission_receipt_sha256: str
    source_aggregate_manifest_sha256: str
    source_run_identity_sha256: str
    anchor_backend_revision: str
    anchor_backend_sha256: str
    injection_backend_revision: str
    injection_backend_sha256: str
    case_aggregates: tuple[Aihub539AvSyncCaseAggregate, ...]
    evidence_sha256: str
    schema_version: str = AIHUB539_AV_SYNC_TEMPORAL_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_temporal_evidence(self)

    def unsigned_json_object(self) -> dict[str, object]:
        return _unsigned_temporal_evidence_json(
            source_admission_receipt_sha256=(
                self.source_admission_receipt_sha256
            ),
            source_aggregate_manifest_sha256=(
                self.source_aggregate_manifest_sha256
            ),
            source_run_identity_sha256=self.source_run_identity_sha256,
            anchor_backend_revision=self.anchor_backend_revision,
            anchor_backend_sha256=self.anchor_backend_sha256,
            injection_backend_revision=self.injection_backend_revision,
            injection_backend_sha256=self.injection_backend_sha256,
            case_aggregates=self.case_aggregates,
        )

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "evidence_sha256": self.evidence_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> "Aihub539AvSyncTemporalEvidence":
        try:
            document = require_mapping(value, "av_sync_temporal_evidence")
            require_exact_keys(
                document,
                _EVIDENCE_KEYS,
                "av_sync_temporal_evidence",
            )
            raw_aggregates = document["case_aggregates"]
            if isinstance(raw_aggregates, (str, bytes)) or not isinstance(
                raw_aggregates, list
            ):
                raise ValueError
            aggregates = tuple(
                _case_aggregate_from_json(item, index)
                for index, item in enumerate(raw_aggregates)
            )
            evidence = cls(
                source_admission_receipt_sha256=require_sha256(
                    document["source_admission_receipt_sha256"],
                    "source_admission_receipt_sha256",
                ),
                source_aggregate_manifest_sha256=require_sha256(
                    document["source_aggregate_manifest_sha256"],
                    "source_aggregate_manifest_sha256",
                ),
                source_run_identity_sha256=require_sha256(
                    document["source_run_identity_sha256"],
                    "source_run_identity_sha256",
                ),
                anchor_backend_revision=require_safe_identifier(
                    document["anchor_backend_revision"],
                    "anchor_backend_revision",
                ),
                anchor_backend_sha256=require_sha256(
                    document["anchor_backend_sha256"],
                    "anchor_backend_sha256",
                ),
                injection_backend_revision=require_safe_identifier(
                    document["injection_backend_revision"],
                    "injection_backend_revision",
                ),
                injection_backend_sha256=require_sha256(
                    document["injection_backend_sha256"],
                    "injection_backend_sha256",
                ),
                case_aggregates=aggregates,
                evidence_sha256=require_sha256(
                    document["evidence_sha256"], "evidence_sha256"
                ),
                schema_version=require_safe_identifier(
                    document["schema_version"], "schema_version"
                ),
            )
        except Aihub539AvSyncRegressionError:
            raise
        except Exception as error:
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_evidence_invalid"
            ) from error
        if canonical_json_bytes(document) != canonical_json_bytes(
            evidence.to_json_object()
        ):
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_evidence_invalid"
            )
        return evidence


@dataclass(frozen=True, slots=True)
class Aihub539AvSyncTemporalEvidenceCompletionReceipt:
    """Completion binding for one immutable, non-promotional evidence object."""

    source_run_identity_sha256: str
    evidence_sha256: str
    completed_case_count: int
    total_measurement_pair_count: int
    receipt_sha256: str
    schema_version: str = (
        AIHUB539_AV_SYNC_TEMPORAL_COMPLETION_RECEIPT_SCHEMA_VERSION
    )

    def __post_init__(self) -> None:
        _validate_completion_receipt(self)

    def unsigned_json_object(self) -> dict[str, object]:
        return _unsigned_completion_receipt_json(
            source_run_identity_sha256=self.source_run_identity_sha256,
            evidence_sha256=self.evidence_sha256,
            completed_case_count=self.completed_case_count,
            total_measurement_pair_count=self.total_measurement_pair_count,
        )

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> "Aihub539AvSyncTemporalEvidenceCompletionReceipt":
        try:
            document = require_mapping(value, "av_sync_temporal_completion")
            require_exact_keys(
                document,
                _COMPLETION_KEYS,
                "av_sync_temporal_completion",
            )
            receipt = cls(
                source_run_identity_sha256=require_sha256(
                    document["source_run_identity_sha256"],
                    "source_run_identity_sha256",
                ),
                evidence_sha256=require_sha256(
                    document["evidence_sha256"], "evidence_sha256"
                ),
                completed_case_count=require_integer(
                    document["completed_case_count"],
                    "completed_case_count",
                    minimum=1,
                ),
                total_measurement_pair_count=require_integer(
                    document["total_measurement_pair_count"],
                    "total_measurement_pair_count",
                    minimum=1,
                ),
                receipt_sha256=require_sha256(
                    document["receipt_sha256"], "receipt_sha256"
                ),
                schema_version=require_safe_identifier(
                    document["schema_version"], "schema_version"
                ),
            )
        except Aihub539AvSyncRegressionError:
            raise
        except Exception as error:
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_completion_receipt_invalid"
            ) from error
        if canonical_json_bytes(document) != canonical_json_bytes(
            receipt.to_json_object()
        ):
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_completion_receipt_invalid"
            )
        return receipt


@dataclass(frozen=True, slots=True)
class Aihub539AvSyncRegressionResult:
    """The two typed outputs issued together after every fixed case passes."""

    evidence: Aihub539AvSyncTemporalEvidence
    completion_receipt: Aihub539AvSyncTemporalEvidenceCompletionReceipt

    def __post_init__(self) -> None:
        if (
            not isinstance(self.evidence, Aihub539AvSyncTemporalEvidence)
            or not isinstance(
                self.completion_receipt,
                Aihub539AvSyncTemporalEvidenceCompletionReceipt,
            )
            or self.completion_receipt.evidence_sha256
            != self.evidence.evidence_sha256
            or self.completion_receipt.source_run_identity_sha256
            != self.evidence.source_run_identity_sha256
            or self.completion_receipt.completed_case_count
            != len(self.evidence.case_aggregates)
            or self.completion_receipt.total_measurement_pair_count
            != sum(
                aggregate.measurement_pair_count
                for aggregate in self.evidence.case_aggregates
            )
        ):
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_result_binding_invalid"
            )


def _issue_temporal_evidence(
    *,
    identity: Aihub539AvSyncRegressionRunIdentity,
    case_aggregates: tuple[Aihub539AvSyncCaseAggregate, ...],
) -> Aihub539AvSyncTemporalEvidence:
    unsigned = _unsigned_temporal_evidence_json(
        source_admission_receipt_sha256=(
            identity.source_admission_receipt_sha256
        ),
        source_aggregate_manifest_sha256=(
            identity.source_aggregate_manifest_sha256
        ),
        source_run_identity_sha256=identity.identity_sha256,
        anchor_backend_revision=identity.anchor_backend_revision,
        anchor_backend_sha256=identity.anchor_backend_sha256,
        injection_backend_revision=identity.injection_backend_revision,
        injection_backend_sha256=identity.injection_backend_sha256,
        case_aggregates=case_aggregates,
    )
    return Aihub539AvSyncTemporalEvidence(
        source_admission_receipt_sha256=(
            identity.source_admission_receipt_sha256
        ),
        source_aggregate_manifest_sha256=(
            identity.source_aggregate_manifest_sha256
        ),
        source_run_identity_sha256=identity.identity_sha256,
        anchor_backend_revision=identity.anchor_backend_revision,
        anchor_backend_sha256=identity.anchor_backend_sha256,
        injection_backend_revision=identity.injection_backend_revision,
        injection_backend_sha256=identity.injection_backend_sha256,
        case_aggregates=case_aggregates,
        evidence_sha256=sha256_document(unsigned),
    )


def _issue_completion_receipt(
    evidence: Aihub539AvSyncTemporalEvidence,
) -> Aihub539AvSyncTemporalEvidenceCompletionReceipt:
    completed_case_count = len(evidence.case_aggregates)
    total_measurement_pair_count = sum(
        item.measurement_pair_count for item in evidence.case_aggregates
    )
    unsigned = _unsigned_completion_receipt_json(
        source_run_identity_sha256=evidence.source_run_identity_sha256,
        evidence_sha256=evidence.evidence_sha256,
        completed_case_count=completed_case_count,
        total_measurement_pair_count=total_measurement_pair_count,
    )
    return Aihub539AvSyncTemporalEvidenceCompletionReceipt(
        source_run_identity_sha256=evidence.source_run_identity_sha256,
        evidence_sha256=evidence.evidence_sha256,
        completed_case_count=completed_case_count,
        total_measurement_pair_count=total_measurement_pair_count,
        receipt_sha256=sha256_document(unsigned),
    )


def _unsigned_temporal_evidence_json(
    *,
    source_admission_receipt_sha256: str,
    source_aggregate_manifest_sha256: str,
    source_run_identity_sha256: str,
    anchor_backend_revision: str,
    anchor_backend_sha256: str,
    injection_backend_revision: str,
    injection_backend_sha256: str,
    case_aggregates: tuple[Aihub539AvSyncCaseAggregate, ...],
) -> dict[str, object]:
    total_measurement_pair_count = sum(
        item.measurement_pair_count for item in case_aggregates
    )
    return {
        "schema_version": AIHUB539_AV_SYNC_TEMPORAL_EVIDENCE_SCHEMA_VERSION,
        "status": "differential_regression_passed_non_promotional",
        "dataset_id": AIHUB539_AV_SYNC_DATASET_ID,
        "dataset_role": AIHUB539_AV_SYNC_DATASET_ROLE,
        "evaluation_scope": "known_injected_change_only_non_p8",
        "truth_basis": "injected_offset_and_affine_drift_change_only",
        "aggregate_only": True,
        "actual_media_source_validated_by_core": False,
        "native_sync_ground_truth_validated": False,
        "metadata_sync_truth_used": False,
        "protocol_revision": "aihub539_av_sync_differential_regression_v1",
        "protocol_sha256": AIHUB539_AV_SYNC_REGRESSION_PROTOCOL_SHA256,
        "aggregate_rule": (
            "all_fixed_cases_and_all_measurement_pairs_must_pass"
        ),
        "source_admission_receipt_sha256": (
            source_admission_receipt_sha256
        ),
        "source_aggregate_manifest_sha256": (
            source_aggregate_manifest_sha256
        ),
        "source_run_identity_sha256": source_run_identity_sha256,
        "anchor_backend_revision": anchor_backend_revision,
        "anchor_backend_sha256": anchor_backend_sha256,
        "injection_backend_revision": injection_backend_revision,
        "injection_backend_sha256": injection_backend_sha256,
        "evaluated_case_count": len(case_aggregates),
        "total_measurement_pair_count": total_measurement_pair_count,
        "case_aggregates": [item.to_json_object() for item in case_aggregates],
        **{name: False for name in _PRIVACY_FLAG_NAMES},
        **{name: False for name in _AUTHORIZATION_FLAG_NAMES},
    }


def _unsigned_completion_receipt_json(
    *,
    source_run_identity_sha256: str,
    evidence_sha256: str,
    completed_case_count: int,
    total_measurement_pair_count: int,
) -> dict[str, object]:
    return {
        "schema_version": (
            AIHUB539_AV_SYNC_TEMPORAL_COMPLETION_RECEIPT_SCHEMA_VERSION
        ),
        "status": "completed_non_promotional",
        "evidence_schema_version": (
            AIHUB539_AV_SYNC_TEMPORAL_EVIDENCE_SCHEMA_VERSION
        ),
        "protocol_sha256": AIHUB539_AV_SYNC_REGRESSION_PROTOCOL_SHA256,
        "source_run_identity_sha256": source_run_identity_sha256,
        "evidence_sha256": evidence_sha256,
        "completed_case_count": completed_case_count,
        "total_measurement_pair_count": total_measurement_pair_count,
        "aggregate_only": True,
        "native_sync_ground_truth_validated": False,
        "metadata_sync_truth_used": False,
        **{name: False for name in _PRIVACY_FLAG_NAMES},
        **{name: False for name in _AUTHORIZATION_FLAG_NAMES},
    }


def _validate_temporal_evidence(evidence: Aihub539AvSyncTemporalEvidence) -> None:
    try:
        if evidence.schema_version != AIHUB539_AV_SYNC_TEMPORAL_EVIDENCE_SCHEMA_VERSION:
            raise ValueError
        require_sha256(
            evidence.source_admission_receipt_sha256,
            "source_admission_receipt_sha256",
        )
        require_sha256(
            evidence.source_aggregate_manifest_sha256,
            "source_aggregate_manifest_sha256",
        )
        require_sha256(
            evidence.source_run_identity_sha256,
            "source_run_identity_sha256",
        )
        require_safe_identifier(
            evidence.anchor_backend_revision,
            "anchor_backend_revision",
        )
        require_sha256(evidence.anchor_backend_sha256, "anchor_backend_sha256")
        require_safe_identifier(
            evidence.injection_backend_revision,
            "injection_backend_revision",
        )
        require_sha256(
            evidence.injection_backend_sha256,
            "injection_backend_sha256",
        )
        expected_identity = sha256_document(
            {
                "dataset_id": AIHUB539_AV_SYNC_DATASET_ID,
                "dataset_role": AIHUB539_AV_SYNC_DATASET_ROLE,
                "source_admission_receipt_sha256": evidence.source_admission_receipt_sha256,
                "source_aggregate_manifest_sha256": evidence.source_aggregate_manifest_sha256,
                "anchor_backend_revision": evidence.anchor_backend_revision,
                "anchor_backend_sha256": evidence.anchor_backend_sha256,
                "injection_backend_revision": evidence.injection_backend_revision,
                "injection_backend_sha256": evidence.injection_backend_sha256,
            }
        )
        if evidence.source_run_identity_sha256 != expected_identity:
            raise ValueError
        _validate_case_aggregates(evidence.case_aggregates)
        require_sha256(evidence.evidence_sha256, "evidence_sha256")
        if evidence.evidence_sha256 != sha256_document(
            evidence.unsigned_json_object()
        ):
            raise ValueError
    except Aihub539AvSyncRegressionError:
        raise
    except Exception as error:
        raise Aihub539AvSyncRegressionError(
            "aihub539_av_sync_evidence_invalid"
        ) from error


def _validate_case_aggregates(
    case_aggregates: tuple[Aihub539AvSyncCaseAggregate, ...],
) -> None:
    protocol = Aihub539AvSyncRegressionProtocol.frozen()
    if not isinstance(case_aggregates, tuple) or len(case_aggregates) != len(
        protocol.injection_cases
    ):
        raise Aihub539AvSyncRegressionError(
            "aihub539_av_sync_evidence_invalid"
        )
    for aggregate, injection_case in zip(
        case_aggregates, protocol.injection_cases, strict=True
    ):
        if not isinstance(aggregate, Aihub539AvSyncCaseAggregate):
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_evidence_invalid"
            )
        if (
            aggregate.case_id != injection_case.case_id
            or aggregate.injected_offset_change_ms
            != injection_case.injected_offset_change_ms
            or aggregate.injected_drift_change_parts_per_million
            != injection_case.injected_drift_change_parts_per_million
        ):
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_evidence_invalid"
            )
        pair_count = require_integer(
            aggregate.measurement_pair_count,
            "measurement_pair_count",
            minimum=protocol.minimum_measurement_pairs_per_case,
        )
        passing_count = require_integer(
            aggregate.passing_measurement_pair_count,
            "passing_measurement_pair_count",
            minimum=0,
        )
        offset_error = require_finite_number(
            aggregate.maximum_absolute_offset_change_error_ms,
            "maximum_absolute_offset_change_error_ms",
        )
        drift_error = require_finite_number(
            aggregate.maximum_absolute_drift_change_error_parts_per_million,
            "maximum_absolute_drift_change_error_parts_per_million",
        )
        anchor_count = require_integer(
            aggregate.minimum_anchor_count,
            "minimum_anchor_count",
            minimum=protocol.minimum_anchor_count_per_measurement,
        )
        anchor_span = require_finite_number(
            aggregate.minimum_anchor_span_ms,
            "minimum_anchor_span_ms",
        )
        fit_error = require_finite_number(
            aggregate.maximum_affine_fit_root_mean_square_error_ms,
            "maximum_affine_fit_root_mean_square_error_ms",
        )
        if (
            passing_count != pair_count
            or require_boolean(
                aggregate.thresholds_passed, "thresholds_passed"
            )
            is not True
            or offset_error < 0.0
            or offset_error > protocol.offset_change_absolute_tolerance_ms
            or drift_error < 0.0
            or drift_error
            > protocol.drift_change_absolute_tolerance_parts_per_million
            or anchor_count < protocol.minimum_anchor_count_per_measurement
            or anchor_span < protocol.minimum_anchor_span_ms
            or fit_error < 0.0
            or fit_error
            > protocol.maximum_affine_fit_root_mean_square_error_ms
        ):
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_evidence_invalid"
            )


def _validate_completion_receipt(
    receipt: Aihub539AvSyncTemporalEvidenceCompletionReceipt,
) -> None:
    try:
        if (
            receipt.schema_version
            != AIHUB539_AV_SYNC_TEMPORAL_COMPLETION_RECEIPT_SCHEMA_VERSION
        ):
            raise ValueError
        require_sha256(
            receipt.source_run_identity_sha256,
            "source_run_identity_sha256",
        )
        require_sha256(receipt.evidence_sha256, "evidence_sha256")
        if require_integer(
            receipt.completed_case_count,
            "completed_case_count",
            minimum=1,
        ) != len(_FROZEN_INJECTION_CASES):
            raise ValueError
        require_integer(
            receipt.total_measurement_pair_count,
            "total_measurement_pair_count",
            minimum=len(_FROZEN_INJECTION_CASES),
        )
        require_sha256(receipt.receipt_sha256, "receipt_sha256")
        if receipt.receipt_sha256 != sha256_document(
            receipt.unsigned_json_object()
        ):
            raise ValueError
    except Exception as error:
        raise Aihub539AvSyncRegressionError(
            "aihub539_av_sync_completion_receipt_invalid"
        ) from error


def _case_aggregate_from_json(
    value: object, index: int
) -> Aihub539AvSyncCaseAggregate:
    document = require_mapping(value, f"case_aggregates[{index}]")
    require_exact_keys(
        document,
        _CASE_AGGREGATE_KEYS,
        f"case_aggregates[{index}]",
    )
    return Aihub539AvSyncCaseAggregate(
        case_id=require_safe_identifier(
            document["case_id"], f"case_aggregates[{index}].case_id"
        ),
        injected_offset_change_ms=require_finite_number(
            document["injected_offset_change_ms"],
            f"case_aggregates[{index}].injected_offset_change_ms",
        ),
        injected_drift_change_parts_per_million=require_finite_number(
            document["injected_drift_change_parts_per_million"],
            (
                f"case_aggregates[{index}]."
                "injected_drift_change_parts_per_million"
            ),
        ),
        measurement_pair_count=require_integer(
            document["measurement_pair_count"],
            f"case_aggregates[{index}].measurement_pair_count",
            minimum=1,
        ),
        passing_measurement_pair_count=require_integer(
            document["passing_measurement_pair_count"],
            f"case_aggregates[{index}].passing_measurement_pair_count",
            minimum=0,
        ),
        maximum_absolute_offset_change_error_ms=require_finite_number(
            document["maximum_absolute_offset_change_error_ms"],
            (
                f"case_aggregates[{index}]."
                "maximum_absolute_offset_change_error_ms"
            ),
        ),
        maximum_absolute_drift_change_error_parts_per_million=(
            require_finite_number(
                document[
                    "maximum_absolute_drift_change_error_parts_per_million"
                ],
                (
                    f"case_aggregates[{index}]."
                    "maximum_absolute_drift_change_error_parts_per_million"
                ),
            )
        ),
        minimum_anchor_count=require_integer(
            document["minimum_anchor_count"],
            f"case_aggregates[{index}].minimum_anchor_count",
            minimum=1,
        ),
        minimum_anchor_span_ms=require_finite_number(
            document["minimum_anchor_span_ms"],
            f"case_aggregates[{index}].minimum_anchor_span_ms",
        ),
        maximum_affine_fit_root_mean_square_error_ms=require_finite_number(
            document["maximum_affine_fit_root_mean_square_error_ms"],
            (
                f"case_aggregates[{index}]."
                "maximum_affine_fit_root_mean_square_error_ms"
            ),
        ),
        thresholds_passed=require_boolean(
            document["thresholds_passed"],
            f"case_aggregates[{index}].thresholds_passed",
        ),
    )


__all__ = [
    "AIHUB539_AV_SYNC_DATASET_ID",
    "AIHUB539_AV_SYNC_DATASET_ROLE",
    "AIHUB539_AV_SYNC_REGRESSION_PROTOCOL_DOCUMENT",
    "AIHUB539_AV_SYNC_REGRESSION_PROTOCOL_SCHEMA_VERSION",
    "AIHUB539_AV_SYNC_REGRESSION_PROTOCOL_SHA256",
    "AIHUB539_AV_SYNC_TEMPORAL_COMPLETION_RECEIPT_SCHEMA_VERSION",
    "AIHUB539_AV_SYNC_TEMPORAL_EVIDENCE_SCHEMA_VERSION",
    "Aihub539AvSyncAnchor",
    "Aihub539AvSyncCaseAggregate",
    "Aihub539AvSyncDifferentialMeasurementPair",
    "Aihub539AvSyncInjectionCase",
    "Aihub539AvSyncRegressionError",
    "Aihub539AvSyncRegressionProtocol",
    "Aihub539AvSyncRegressionResult",
    "Aihub539AvSyncRegressionRunIdentity",
    "Aihub539AvSyncTemporalEvidence",
    "Aihub539AvSyncTemporalEvidenceCompletionReceipt",
    "parse_aihub539_av_sync_regression_protocol",
]
