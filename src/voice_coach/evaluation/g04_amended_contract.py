"""Fail-closed domain contract for the amended G04 T43 and I30 audits.

T43 is an enriched root-cause set and is never an accuracy denominator.  I30
is an independent long-utterance sample and is the only source of the amended
phone-level accuracy gate.  This module contains no model or network access.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import io
import math
from pathlib import Path
import re
from typing import Any, Literal
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator

from voice_coach.evaluation.alignment_review import (
    ANNOTATION_FIDELITY_VALUES,
    AuditItem,
    ReviewDataset,
    atomic_write_json,
    atomic_write_text,
    hash_reviewer_id,
    sha256_file,
    strict_read_json,
)
from voice_coach.evaluation.g04_audio_proxy import (
    alignment_record_error_count,
    recompute_alignment_records,
)


T43_MANIFEST_SCHEMA_VERSION = "voice_coach.g04_t43_manifest.v1"
T43_RECEIPT_SCHEMA_VERSION = "voice_coach.g04_t43_manifest_receipt.v1"
AMENDED_STATE_SCHEMA_VERSION = "voice_coach.g04_amended_review_state.v1"
T43_REPORT_SCHEMA_VERSION = "voice_coach.g04_t43_review_report.v1"
I30_REPORT_SCHEMA_VERSION = "voice_coach.g04_i30_phone_report.v1"
PROGRESS_SCHEMA_VERSION = "voice_coach.g04_amended_progress.v1"
COMPLETION_SCHEMA_VERSION = "voice_coach.g04_amended_completion_attestation.v1"

EXPECTED_T43_COUNT = 43
EXPECTED_ALIGNMENT_DRIFT_COUNT = 19
EXPECTED_AI_MISMATCH_COUNT = 26
EXPECTED_INTERSECTION_COUNT = 2
EXPECTED_I30_COUNT = 30
EXPECTED_I30_PER_ERROR_STRATUM = 15
MINIMUM_PHONE_ACCURACY = 0.95
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

AI_MISMATCH_OUTCOMES = frozenset(
    {
        "target_annotation_mismatch",
        "realized_annotation_mismatch",
        "both_annotations_mismatch",
    }
)


class G04AmendedContractError(ValueError):
    """Raised when amended G04 evidence cannot be trusted."""


class G04ReviewProfile(StrEnum):
    """Review profiles with deliberately different statistical roles."""

    T43 = "t43_root_cause"
    I30 = "i30_phone_accuracy"


class T43SelectionReason(StrEnum):
    """Deterministic reasons an item enters the enriched T43 set."""

    ALIGNMENT_DRIFT = "alignment_recomputation_drift"
    AI_NON_ABSTAIN_MISMATCH = "ai_non_abstain_annotation_mismatch"


class G04RootCause(StrEnum):
    """Mutually exclusive reviewer root-cause outcomes for T43."""

    ALIGNMENT_ALGORITHM = "alignment_algorithm"
    TARGET_ANNOTATION = "target_annotation"
    REALIZED_ANNOTATION = "realized_annotation"
    BOTH_ANNOTATIONS = "both_annotations"
    AUDIO_UNUSABLE = "audio_unusable"
    NO_DEFECT_CONFIRMED = "no_defect_confirmed"
    OTHER = "other"


class G04Resolution(StrEnum):
    """Resolution states that distinguish remediation from an open defect."""

    RETAINED_NO_DEFECT = "retained_no_defect"
    REVERIFIED_AFTER_ALIGNMENT_FIX = "reverified_after_alignment_fix"
    REVERIFIED_AFTER_ANNOTATION_FIX = "reverified_after_annotation_fix"
    EXCLUDED_PREDECLARED_QC = "excluded_predeclared_qc"
    OTHER_RESOLVED = "other_resolved"
    UNRESOLVED = "unresolved"


class AmendedReviewSubmission(BaseModel):
    """One T43 or I30 reviewer decision before profile-specific validation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    alignment_correct: bool
    maximum_boundary_error_ms: float = Field(ge=0)
    incorrect_interval_count: int = Field(ge=0)
    annotation_fidelity: (
        Literal[
            "consistent",
            "target_annotation_mismatch",
            "realized_annotation_mismatch",
            "both_annotations_mismatch",
            "uncertain",
        ]
        | None
    ) = None
    root_cause: G04RootCause | None = None
    resolution: G04Resolution | None = None
    notes: str = Field(max_length=2_000)

    @field_validator("maximum_boundary_error_ms")
    @classmethod
    def finite_boundary_error(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("maximum_boundary_error_ms must be finite")
        return value

    @field_validator("root_cause", mode="before")
    @classmethod
    def parse_root_cause(cls, value: object) -> object:
        """Accept the enum's JSON string while keeping every other field strict."""

        if isinstance(value, str):
            return G04RootCause(value)
        return value

    @field_validator("resolution", mode="before")
    @classmethod
    def parse_resolution(cls, value: object) -> object:
        """Accept the enum's JSON string while keeping every other field strict."""

        if isinstance(value, str):
            return G04Resolution(value)
        return value

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value: str) -> str:
        return unicodedata.normalize("NFKC", value).strip()


@dataclass(frozen=True, slots=True)
class T43Selection:
    """Privacy-safe selection result for one T43 item."""

    item_sha256: str
    reasons: tuple[T43SelectionReason, ...]
    alignment_record_error_count: int
    ai_annotation_fidelity: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_sha256": self.item_sha256,
            "selection_reasons": [reason.value for reason in self.reasons],
            "alignment_record_error_count": self.alignment_record_error_count,
            "ai_annotation_fidelity": self.ai_annotation_fidelity,
        }


def _require_sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise G04AmendedContractError(f"{name} must be a lowercase SHA-256")
    return value


def _require_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise G04AmendedContractError(f"{name} must be a string-keyed object")
    return value


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], name: str
) -> None:
    observed = set(value)
    if observed != expected:
        raise G04AmendedContractError(
            f"{name} keys differ; missing={sorted(expected - observed)}, "
            f"extra={sorted(observed - expected)}"
        )


def _proxy_rows_for_dataset(
    *, dataset: ReviewDataset, proxy_state: Mapping[str, Any]
) -> Mapping[str, Mapping[str, Any]]:
    if proxy_state.get("schema_version") != "voice_coach.g04_audio_ai_proxy_state.v1":
        raise G04AmendedContractError("unsupported G04 AI proxy state schema")
    source = _require_mapping(proxy_state.get("source"), "proxy source")
    if source.get("worksheet_sha256") != dataset.worksheet_sha256:
        raise G04AmendedContractError("proxy worksheet SHA does not match dataset")
    if source.get("details_sha256") != dataset.details_sha256:
        raise G04AmendedContractError("proxy details SHA does not match dataset")
    if source.get("expected_count") != len(dataset.items):
        raise G04AmendedContractError("proxy expected count does not match dataset")
    raw_rows = _require_mapping(proxy_state.get("rows"), "proxy rows")
    if set(raw_rows) != set(dataset.by_hash):
        raise G04AmendedContractError("proxy item hashes do not match dataset")
    rows: dict[str, Mapping[str, Any]] = {}
    for item_hash, value in raw_rows.items():
        rows[item_hash] = _require_mapping(value, "proxy row")
    return rows


def select_t43_items(
    *, dataset: ReviewDataset, proxy_state: Mapping[str, Any]
) -> tuple[T43Selection, ...]:
    """Recompute the exact T43 union without exposing raw IDs or paths."""

    rows = _proxy_rows_for_dataset(dataset=dataset, proxy_state=proxy_state)
    selected: list[T43Selection] = []
    for item in dataset.items:
        recomputed = recompute_alignment_records(
            item.target_phone_intervals,
            item.realized_phone_intervals,
        )
        drift_count = alignment_record_error_count(
            item.automatic_phone_alignments,
            recomputed,
        )
        proxy_outcome = rows[item.item_sha256].get("annotation_fidelity")
        if proxy_outcome not in ANNOTATION_FIDELITY_VALUES:
            raise G04AmendedContractError("proxy annotation_fidelity is invalid")
        reasons: list[T43SelectionReason] = []
        if drift_count > 0:
            reasons.append(T43SelectionReason.ALIGNMENT_DRIFT)
        if proxy_outcome in AI_MISMATCH_OUTCOMES:
            reasons.append(T43SelectionReason.AI_NON_ABSTAIN_MISMATCH)
        if reasons:
            selected.append(
                T43Selection(
                    item_sha256=item.item_sha256,
                    reasons=tuple(reasons),
                    alignment_record_error_count=drift_count,
                    ai_annotation_fidelity=str(proxy_outcome),
                )
            )
    selected.sort(key=lambda item: item.item_sha256)
    return tuple(selected)


def build_t43_manifest(
    *,
    dataset: ReviewDataset,
    proxy_state: Mapping[str, Any],
    proxy_state_sha256: str,
    alignment_policy_source_sha256: str,
    align_intervals_source_sha256: str,
) -> dict[str, Any]:
    """Build and validate the immutable privacy-safe T43 selection manifest."""

    selected = select_t43_items(dataset=dataset, proxy_state=proxy_state)
    drift = sum(T43SelectionReason.ALIGNMENT_DRIFT in item.reasons for item in selected)
    ai_mismatch = sum(
        T43SelectionReason.AI_NON_ABSTAIN_MISMATCH in item.reasons for item in selected
    )
    intersection = sum(len(item.reasons) == 2 for item in selected)
    counts = {
        "alignment_drift_count": drift,
        "ai_non_abstain_mismatch_count": ai_mismatch,
        "intersection_count": intersection,
        "union_count": len(selected),
    }
    expected = {
        "alignment_drift_count": EXPECTED_ALIGNMENT_DRIFT_COUNT,
        "ai_non_abstain_mismatch_count": EXPECTED_AI_MISMATCH_COUNT,
        "intersection_count": EXPECTED_INTERSECTION_COUNT,
        "union_count": EXPECTED_T43_COUNT,
    }
    if counts != expected:
        raise G04AmendedContractError(
            f"T43 selection counts differ from amendment: {counts} != {expected}"
        )
    manifest = {
        "schema_version": T43_MANIFEST_SCHEMA_VERSION,
        "status": "frozen_selection",
        "expected_count": EXPECTED_T43_COUNT,
        "selection_policy": {
            "set_operation": "alignment_drift_union_ai_non_abstain_mismatch",
            "alignment_drift_rule": "alignment_record_error_count_gt_0",
            "ai_mismatch_outcomes": sorted(AI_MISMATCH_OUTCOMES),
            "accuracy_denominator_allowed": False,
            "alignment_parameters": {
                "tolerance_seconds": 0.03,
                "minimum_overlap_ratio": 0.50,
                "preserve_one_to_many": False,
            },
        },
        "counts": counts,
        "source": {
            "worksheet_sha256": dataset.worksheet_sha256,
            "alignment_details_sha256": dataset.details_sha256,
            "proxy_state_sha256": _require_sha256(
                proxy_state_sha256, "proxy state SHA"
            ),
            "alignment_policy_source_sha256": _require_sha256(
                alignment_policy_source_sha256,
                "alignment policy source SHA",
            ),
            "align_intervals_source_sha256": _require_sha256(
                align_intervals_source_sha256,
                "align_intervals source SHA",
            ),
        },
        "items": [item.as_dict() for item in selected],
        "privacy": {
            "item_ids_namespaced_sha256_only": True,
            "raw_ids_paths_audio_or_annotations_included": False,
            "storage_zone": "R1_restricted_manifest",
        },
    }
    validate_t43_manifest(manifest)
    return manifest


def validate_t43_manifest(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate the immutable T43 contract and deterministic item ordering."""

    _require_exact_keys(
        payload,
        {
            "schema_version",
            "status",
            "expected_count",
            "selection_policy",
            "counts",
            "source",
            "items",
            "privacy",
        },
        "T43 manifest",
    )
    if payload.get("schema_version") != T43_MANIFEST_SCHEMA_VERSION:
        raise G04AmendedContractError("T43 manifest schema differs")
    if payload.get("status") != "frozen_selection":
        raise G04AmendedContractError("T43 manifest is not frozen")
    if payload.get("expected_count") != EXPECTED_T43_COUNT:
        raise G04AmendedContractError("T43 expected count differs")
    counts = _require_mapping(payload.get("counts"), "T43 counts")
    expected_counts = {
        "alignment_drift_count": EXPECTED_ALIGNMENT_DRIFT_COUNT,
        "ai_non_abstain_mismatch_count": EXPECTED_AI_MISMATCH_COUNT,
        "intersection_count": EXPECTED_INTERSECTION_COUNT,
        "union_count": EXPECTED_T43_COUNT,
    }
    if dict(counts) != expected_counts:
        raise G04AmendedContractError("T43 counts differ")
    source = _require_mapping(payload.get("source"), "T43 source")
    _require_exact_keys(
        source,
        {
            "worksheet_sha256",
            "alignment_details_sha256",
            "proxy_state_sha256",
            "alignment_policy_source_sha256",
            "align_intervals_source_sha256",
        },
        "T43 source",
    )
    for key, value in source.items():
        _require_sha256(value, f"T43 source {key}")
    items = payload.get("items")
    if not isinstance(items, list) or len(items) != EXPECTED_T43_COUNT:
        raise G04AmendedContractError("T43 items must contain exactly 43 rows")
    item_hashes: list[str] = []
    for raw_item in items:
        item = _require_mapping(raw_item, "T43 item")
        _require_exact_keys(
            item,
            {
                "item_sha256",
                "selection_reasons",
                "alignment_record_error_count",
                "ai_annotation_fidelity",
            },
            "T43 item",
        )
        item_hashes.append(_require_sha256(item.get("item_sha256"), "T43 item hash"))
        reasons = item.get("selection_reasons")
        if (
            not isinstance(reasons, list)
            or not reasons
            or len(set(reasons)) != len(reasons)
        ):
            raise G04AmendedContractError("T43 selection reasons are invalid")
        if not set(reasons) <= {reason.value for reason in T43SelectionReason}:
            raise G04AmendedContractError("T43 selection reason is unsupported")
        error_count = item.get("alignment_record_error_count")
        if isinstance(error_count, bool) or not isinstance(error_count, int):
            raise G04AmendedContractError("T43 drift count must be an integer")
        has_drift_reason = T43SelectionReason.ALIGNMENT_DRIFT.value in reasons
        if has_drift_reason != (error_count > 0):
            raise G04AmendedContractError("T43 drift reason/count disagree")
        outcome = item.get("ai_annotation_fidelity")
        if outcome not in ANNOTATION_FIDELITY_VALUES:
            raise G04AmendedContractError("T43 AI outcome is invalid")
        has_ai_reason = T43SelectionReason.AI_NON_ABSTAIN_MISMATCH.value in reasons
        if has_ai_reason != (outcome in AI_MISMATCH_OUTCOMES):
            raise G04AmendedContractError("T43 AI reason/outcome disagree")
    observed_counts = {
        "alignment_drift_count": sum(
            T43SelectionReason.ALIGNMENT_DRIFT.value in item["selection_reasons"]
            for item in items
        ),
        "ai_non_abstain_mismatch_count": sum(
            T43SelectionReason.AI_NON_ABSTAIN_MISMATCH.value
            in item["selection_reasons"]
            for item in items
        ),
        "intersection_count": sum(
            len(item["selection_reasons"]) == 2 for item in items
        ),
        "union_count": len(items),
    }
    if observed_counts != expected_counts:
        raise G04AmendedContractError("T43 item rows do not reproduce counts")
    if item_hashes != sorted(item_hashes) or len(set(item_hashes)) != len(item_hashes):
        raise G04AmendedContractError("T43 item hashes must be unique and sorted")
    privacy = _require_mapping(payload.get("privacy"), "T43 privacy")
    if privacy.get("item_ids_namespaced_sha256_only") is not True:
        raise G04AmendedContractError("T43 privacy hash policy differs")
    if privacy.get("raw_ids_paths_audio_or_annotations_included") is not False:
        raise G04AmendedContractError("T43 manifest contains restricted fields")
    return payload


def build_t43_manifest_receipt(
    *, manifest: Mapping[str, Any], manifest_sha256: str
) -> dict[str, Any]:
    """Build the A1 aggregate receipt without copying the 43 item hashes."""

    validate_t43_manifest(manifest)
    return {
        "schema_version": T43_RECEIPT_SCHEMA_VERSION,
        "status": "prepared_for_human_review",
        "manifest_sha256": _require_sha256(manifest_sha256, "T43 manifest SHA"),
        "counts": dict(_require_mapping(manifest.get("counts"), "T43 counts")),
        "human_review_completed_count": 0,
        "canonical_g04_completion_claim_allowed": False,
        "privacy": {
            "aggregate_only": True,
            "contains_item_hashes": False,
            "contains_raw_ids_paths_audio_or_annotations": False,
        },
    }


def _valid_resolution(root_cause: G04RootCause, resolution: G04Resolution) -> bool:
    allowed: Mapping[G04RootCause, frozenset[G04Resolution]] = {
        G04RootCause.ALIGNMENT_ALGORITHM: frozenset(
            {
                G04Resolution.REVERIFIED_AFTER_ALIGNMENT_FIX,
                G04Resolution.EXCLUDED_PREDECLARED_QC,
            }
        ),
        G04RootCause.TARGET_ANNOTATION: frozenset(
            {
                G04Resolution.REVERIFIED_AFTER_ANNOTATION_FIX,
                G04Resolution.EXCLUDED_PREDECLARED_QC,
            }
        ),
        G04RootCause.REALIZED_ANNOTATION: frozenset(
            {
                G04Resolution.REVERIFIED_AFTER_ANNOTATION_FIX,
                G04Resolution.EXCLUDED_PREDECLARED_QC,
            }
        ),
        G04RootCause.BOTH_ANNOTATIONS: frozenset(
            {
                G04Resolution.REVERIFIED_AFTER_ANNOTATION_FIX,
                G04Resolution.EXCLUDED_PREDECLARED_QC,
            }
        ),
        G04RootCause.AUDIO_UNUSABLE: frozenset({G04Resolution.EXCLUDED_PREDECLARED_QC}),
        G04RootCause.NO_DEFECT_CONFIRMED: frozenset({G04Resolution.RETAINED_NO_DEFECT}),
        G04RootCause.OTHER: frozenset(
            {G04Resolution.OTHER_RESOLVED, G04Resolution.EXCLUDED_PREDECLARED_QC}
        ),
    }
    return resolution in allowed[root_cause]


class G04AmendedReviewStore:
    """Atomic review state bound to one immutable T43 or I30 selection source."""

    def __init__(
        self,
        *,
        path: Path,
        dataset: ReviewDataset,
        profile: G04ReviewProfile,
        selected_item_hashes: Sequence[str],
        selection_source_sha256: str,
    ) -> None:
        self.path = path
        self.dataset = dataset
        self.profile = profile
        self.selection_source_sha256 = _require_sha256(
            selection_source_sha256, "selection source SHA"
        )
        selected = tuple(selected_item_hashes)
        if not selected or len(selected) != len(set(selected)):
            raise G04AmendedContractError("selected item hashes must be unique")
        if any(item_hash not in dataset.by_hash for item_hash in selected):
            raise G04AmendedContractError("selection contains an unknown item hash")
        expected = (
            EXPECTED_T43_COUNT
            if profile is G04ReviewProfile.T43
            else EXPECTED_I30_COUNT
        )
        if len(selected) != expected:
            raise G04AmendedContractError(
                f"{profile.value} selection must contain {expected} items"
            )
        self.selected_item_hashes = selected
        self.selected_hash_set = frozenset(selected)
        self.items = tuple(dataset.by_hash[item_hash] for item_hash in selected)
        self.reviews: dict[str, dict[str, Any]] = {}
        if path.exists():
            self._load()

    def _source(self) -> dict[str, str]:
        return {
            "worksheet_sha256": self.dataset.worksheet_sha256,
            "alignment_details_sha256": self.dataset.details_sha256,
            "selection_source_sha256": self.selection_source_sha256,
        }

    def _load(self) -> None:
        document = strict_read_json(self.path)
        if document.get("schema_version") != AMENDED_STATE_SCHEMA_VERSION:
            raise G04AmendedContractError("unsupported amended review state schema")
        if document.get("profile") != self.profile.value:
            raise G04AmendedContractError("amended review profile differs")
        if document.get("source") != self._source():
            raise G04AmendedContractError("amended review source binding differs")
        reviews = _require_mapping(document.get("reviews"), "amended reviews")
        validated: dict[str, dict[str, Any]] = {}
        for item_hash, raw_review in reviews.items():
            if item_hash not in self.selected_hash_set:
                raise G04AmendedContractError("state contains an unselected item")
            review = _require_mapping(raw_review, "amended review")
            submission = AmendedReviewSubmission.model_validate(
                {
                    key: review.get(key)
                    for key in (
                        "alignment_correct",
                        "maximum_boundary_error_ms",
                        "incorrect_interval_count",
                        "annotation_fidelity",
                        "root_cause",
                        "resolution",
                        "notes",
                    )
                },
                strict=False,
            )
            reviewer_hash = _require_sha256(
                review.get("reviewer_id_sha256"), "reviewer hash"
            )
            updated_at = review.get("updated_at_utc")
            if not isinstance(updated_at, str) or not updated_at:
                raise G04AmendedContractError("updated_at_utc is invalid")
            self._validate_submission(item_hash=item_hash, submission=submission)
            validated[item_hash] = {
                "reviewer_id_sha256": reviewer_hash,
                **submission.model_dump(mode="json"),
                "updated_at_utc": updated_at,
            }
        self.reviews = validated

    def _document(self) -> dict[str, Any]:
        return {
            "schema_version": AMENDED_STATE_SCHEMA_VERSION,
            "profile": self.profile.value,
            "source": self._source(),
            "privacy": {
                "item_ids_namespaced_sha256_only": True,
                "reviewer_ids_namespaced_sha256_only": True,
                "raw_ids_paths_audio_or_annotations_stored": False,
            },
            "reviews": {key: self.reviews[key] for key in sorted(self.reviews)},
        }

    def _validate_submission(
        self, *, item_hash: str, submission: AmendedReviewSubmission
    ) -> None:
        item = self.dataset.by_hash[item_hash]
        total_intervals = len(item.automatic_phone_alignments)
        if submission.maximum_boundary_error_ms > item.duration_seconds * 1_000:
            raise G04AmendedContractError(
                "maximum_boundary_error_ms exceeds utterance duration"
            )
        if submission.incorrect_interval_count > total_intervals:
            raise G04AmendedContractError(
                "incorrect_interval_count exceeds displayed alignment count"
            )
        if submission.alignment_correct != (submission.incorrect_interval_count == 0):
            raise G04AmendedContractError(
                "alignment_correct and incorrect_interval_count disagree"
            )
        if self.profile is G04ReviewProfile.T43:
            if submission.annotation_fidelity is None:
                raise G04AmendedContractError("T43 requires annotation_fidelity")
            if submission.root_cause is None or submission.resolution is None:
                raise G04AmendedContractError("T43 requires root_cause and resolution")
            if (
                submission.resolution is not G04Resolution.UNRESOLVED
                and not _valid_resolution(submission.root_cause, submission.resolution)
            ):
                raise G04AmendedContractError(
                    "T43 root_cause and resolution are incompatible"
                )
        else:
            if any(
                value is not None
                for value in (
                    submission.annotation_fidelity,
                    submission.root_cause,
                    submission.resolution,
                )
            ):
                raise G04AmendedContractError(
                    "I30 must not collect T43 fidelity/root-cause fields"
                )
        for sensitive in self.dataset.items:
            if sensitive.raw_utterance_id in submission.notes:
                raise G04AmendedContractError("notes contain a raw utterance ID")
            if str(sensitive.audio_path) in submission.notes:
                raise G04AmendedContractError("notes contain a restricted path")

    def save_review(
        self,
        *,
        item_hash: str,
        reviewer_id: str,
        submission: AmendedReviewSubmission,
        replace: bool,
    ) -> dict[str, Any]:
        """Validate and atomically store one profile-specific review."""

        if item_hash not in self.selected_hash_set:
            raise KeyError(item_hash)
        self._validate_submission(item_hash=item_hash, submission=submission)
        if item_hash in self.reviews and not replace:
            raise FileExistsError("item already has a completed review")
        review = {
            "reviewer_id_sha256": hash_reviewer_id(reviewer_id),
            **submission.model_dump(mode="json"),
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        self.reviews[item_hash] = review
        atomic_write_json(self.path, self._document())
        return dict(review)

    def status(self) -> dict[str, Any]:
        completed = len(self.reviews)
        expected = len(self.selected_item_hashes)
        return {
            "profile": self.profile.value,
            "expected_count": expected,
            "completed_count": completed,
            "remaining_count": expected - completed,
            "completion_fraction": completed / expected,
        }


def selected_hashes_from_t43_manifest(
    manifest: Mapping[str, Any],
) -> tuple[str, ...]:
    """Return the frozen T43 item order after strict manifest validation."""

    validate_t43_manifest(manifest)
    items = manifest["items"]
    assert isinstance(items, list)
    return tuple(str(item["item_sha256"]) for item in items)


def _base_report(
    *, store: G04AmendedReviewStore, schema_version: str
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        **store.status(),
        "source": {
            **store._source(),
            "review_state_sha256": (
                sha256_file(store.path) if store.path.is_file() else None
            ),
        },
        "reviewer_count": len(
            {review["reviewer_id_sha256"] for review in store.reviews.values()}
        ),
        "privacy": {
            "aggregate_only": True,
            "contains_item_hashes_rows_audio_labels_or_paths": False,
        },
    }


def build_t43_report(*, store: G04AmendedReviewStore) -> dict[str, Any]:
    """Score T43 resolution completeness without estimating population accuracy."""

    if store.profile is not G04ReviewProfile.T43:
        raise G04AmendedContractError("T43 report requires a T43 store")
    complete = store.status()["remaining_count"] == 0
    fidelity_counts = Counter(
        str(review["annotation_fidelity"]) for review in store.reviews.values()
    )
    root_cause_counts = Counter(
        str(review["root_cause"]) for review in store.reviews.values()
    )
    resolution_counts = Counter(
        str(review["resolution"]) for review in store.reviews.values()
    )
    uncertain_count = fidelity_counts["uncertain"]
    unresolved_count = resolution_counts[G04Resolution.UNRESOLVED.value]
    invalid_resolution_count = 0
    for review in store.reviews.values():
        root_cause = G04RootCause(str(review["root_cause"]))
        resolution = G04Resolution(str(review["resolution"]))
        if not _valid_resolution(root_cause, resolution):
            invalid_resolution_count += 1
    passed = (
        complete
        and uncertain_count == 0
        and unresolved_count == 0
        and invalid_resolution_count == 0
    )
    return {
        **_base_report(store=store, schema_version=T43_REPORT_SCHEMA_VERSION),
        "status": "passed" if passed else "failed" if complete else "incomplete",
        "accuracy_denominator_allowed": False,
        "annotation_fidelity_counts": {
            value: fidelity_counts[value]
            for value in sorted(ANNOTATION_FIDELITY_VALUES)
        },
        "root_cause_counts": {
            value.value: root_cause_counts[value.value] for value in G04RootCause
        },
        "resolution_counts": {
            value.value: resolution_counts[value.value] for value in G04Resolution
        },
        "uncertain_count": uncertain_count,
        "unresolved_count": unresolved_count,
        "invalid_resolution_count": invalid_resolution_count,
    }


def _phone_summary(
    items: Sequence[AuditItem], reviews: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    total = sum(len(item.automatic_phone_alignments) for item in items)
    incorrect = sum(
        int(reviews[item.item_sha256]["incorrect_interval_count"])
        for item in items
        if item.item_sha256 in reviews
    )
    complete = bool(items) and all(item.item_sha256 in reviews for item in items)
    accuracy = 1 - incorrect / total if complete and total > 0 else None
    return {
        "utterance_count": len(items),
        "total_phone_intervals": total if complete else 0,
        "incorrect_phone_intervals": incorrect if complete else 0,
        "phone_level_accuracy": accuracy,
    }


def build_i30_report(*, store: G04AmendedReviewStore) -> dict[str, Any]:
    """Score the independent I30 phone-level accuracy and fixed strata."""

    if store.profile is not G04ReviewProfile.I30:
        raise G04AmendedContractError("I30 report requires an I30 store")
    complete = store.status()["remaining_count"] == 0
    overall = _phone_summary(store.items, store.reviews)
    strata: dict[str, Any] = {}
    strata_valid = True
    for error_presence in (False, True):
        items = tuple(
            item for item in store.items if item.error_presence is error_presence
        )
        if len(items) != EXPECTED_I30_PER_ERROR_STRATUM:
            strata_valid = False
        strata[str(error_presence).lower()] = _phone_summary(items, store.reviews)
    accuracy = overall["phone_level_accuracy"]
    passed = (
        complete
        and strata_valid
        and isinstance(accuracy, float)
        and accuracy >= MINIMUM_PHONE_ACCURACY
    )
    return {
        **_base_report(store=store, schema_version=I30_REPORT_SCHEMA_VERSION),
        "status": "passed" if passed else "failed" if complete else "incomplete",
        "minimum_phone_level_accuracy": MINIMUM_PHONE_ACCURACY,
        "overall": overall,
        "error_presence_strata": strata,
        "strata_valid": strata_valid,
        "utterance_alignment_accuracy_gate_allowed": False,
    }


def export_amended_reviews(
    *,
    store: G04AmendedReviewStore,
    output: Path,
    report_output: Path,
) -> dict[str, Any]:
    """Export privacy-safe completed rows and the profile aggregate report."""

    report = (
        build_t43_report(store=store)
        if store.profile is G04ReviewProfile.T43
        else build_i30_report(store=store)
    )
    if report["remaining_count"] != 0:
        atomic_write_json(report_output, report)
        return report
    fields = [
        "audit_index",
        "item_sha256",
        "reviewer_id_sha256",
        "alignment_correct",
        "maximum_boundary_error_ms",
        "incorrect_interval_count",
        "total_alignment_interval_count",
        "annotation_fidelity",
        "root_cause",
        "resolution",
        "notes",
    ]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, delimiter="\t")
    writer.writeheader()
    for item in store.items:
        review = store.reviews[item.item_sha256]
        writer.writerow(
            {
                "audit_index": item.audit_index,
                "item_sha256": item.item_sha256,
                "reviewer_id_sha256": review["reviewer_id_sha256"],
                "alignment_correct": str(review["alignment_correct"]).lower(),
                "maximum_boundary_error_ms": review["maximum_boundary_error_ms"],
                "incorrect_interval_count": review["incorrect_interval_count"],
                "total_alignment_interval_count": len(item.automatic_phone_alignments),
                "annotation_fidelity": review["annotation_fidelity"] or "",
                "root_cause": review["root_cause"] or "",
                "resolution": review["resolution"] or "",
                "notes": review["notes"],
            }
        )
    atomic_write_text(output, buffer.getvalue())
    report["export"] = {
        "written": True,
        "sha256": sha256_file(output),
        "contains_raw_ids_paths_audio_or_annotations": False,
    }
    atomic_write_json(report_output, report)
    return report


def build_amended_progress(
    *,
    t43_report: Mapping[str, Any],
    i30_report: Mapping[str, Any],
    consumer_migration_complete: bool,
) -> dict[str, Any]:
    """Build the A1 progress payload consumed by D07 and S108 preflights."""

    if t43_report.get("schema_version") != T43_REPORT_SCHEMA_VERSION:
        raise G04AmendedContractError("T43 progress report schema differs")
    if i30_report.get("schema_version") != I30_REPORT_SCHEMA_VERSION:
        raise G04AmendedContractError("I30 progress report schema differs")
    passed = (
        t43_report.get("status") == "passed"
        and i30_report.get("status") == "passed"
        and consumer_migration_complete
    )
    payload = {
        "schema_version": PROGRESS_SCHEMA_VERSION,
        "status": "passed" if passed else "incomplete",
        "t43": {
            "expected_count": EXPECTED_T43_COUNT,
            "completed_count": int(t43_report.get("completed_count", 0)),
            "remaining_count": int(
                t43_report.get("remaining_count", EXPECTED_T43_COUNT)
            ),
            "status": t43_report.get("status"),
            "uncertain_count": int(t43_report.get("uncertain_count", 0)),
            "unresolved_count": int(t43_report.get("unresolved_count", 0)),
        },
        "i30": {
            "expected_count": EXPECTED_I30_COUNT,
            "completed_count": int(i30_report.get("completed_count", 0)),
            "remaining_count": int(
                i30_report.get("remaining_count", EXPECTED_I30_COUNT)
            ),
            "status": i30_report.get("status"),
            "phone_level_accuracy": _require_mapping(
                i30_report.get("overall"), "I30 overall"
            ).get("phone_level_accuracy"),
            "minimum_phone_level_accuracy": MINIMUM_PHONE_ACCURACY,
        },
        "consumer_migration_complete": consumer_migration_complete,
        "canonical_g04_completion_claim_allowed": passed,
        "privacy": {
            "aggregate_only": True,
            "contains_item_hashes_rows_audio_labels_or_paths": False,
        },
    }
    validate_amended_progress(payload)
    return payload


def validate_amended_progress(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate the aggregate-only progress snapshot shared by D07 and S108."""

    _require_exact_keys(
        payload,
        {
            "schema_version",
            "status",
            "t43",
            "i30",
            "consumer_migration_complete",
            "canonical_g04_completion_claim_allowed",
            "privacy",
        },
        "G04 amended progress",
    )
    if payload.get("schema_version") != PROGRESS_SCHEMA_VERSION:
        raise G04AmendedContractError("G04 amended progress schema differs")
    t43 = _require_mapping(payload.get("t43"), "G04 progress T43")
    _require_exact_keys(
        t43,
        {
            "expected_count",
            "completed_count",
            "remaining_count",
            "status",
            "uncertain_count",
            "unresolved_count",
        },
        "G04 progress T43",
    )
    i30 = _require_mapping(payload.get("i30"), "G04 progress I30")
    _require_exact_keys(
        i30,
        {
            "expected_count",
            "completed_count",
            "remaining_count",
            "status",
            "phone_level_accuracy",
            "minimum_phone_level_accuracy",
        },
        "G04 progress I30",
    )
    for section, expected in ((t43, EXPECTED_T43_COUNT), (i30, EXPECTED_I30_COUNT)):
        completed = section.get("completed_count")
        remaining = section.get("remaining_count")
        if (
            section.get("expected_count") != expected
            or isinstance(completed, bool)
            or not isinstance(completed, int)
            or isinstance(remaining, bool)
            or not isinstance(remaining, int)
            or completed < 0
            or remaining < 0
            or completed + remaining != expected
        ):
            raise G04AmendedContractError("G04 progress count arithmetic differs")
        allowed_statuses = {"incomplete"} if remaining else {"passed", "failed"}
        if section.get("status") not in allowed_statuses:
            raise G04AmendedContractError("G04 progress section status differs")
    for field in ("uncertain_count", "unresolved_count"):
        value = t43.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= t43["completed_count"]
        ):
            raise G04AmendedContractError(f"G04 progress T43 {field} is invalid")
    accuracy = i30.get("phone_level_accuracy")
    if i30["remaining_count"]:
        if accuracy is not None:
            raise G04AmendedContractError("incomplete I30 accuracy must be null")
    elif (
        isinstance(accuracy, bool)
        or not isinstance(accuracy, (int, float))
        or not math.isfinite(float(accuracy))
        or not 0 <= float(accuracy) <= 1
    ):
        raise G04AmendedContractError("complete I30 accuracy is invalid")
    if i30.get("minimum_phone_level_accuracy") != MINIMUM_PHONE_ACCURACY:
        raise G04AmendedContractError("I30 progress threshold differs")
    migration = payload.get("consumer_migration_complete")
    if not isinstance(migration, bool):
        raise G04AmendedContractError("consumer migration flag is invalid")
    passed = (
        t43.get("status") == "passed" and i30.get("status") == "passed" and migration
    )
    if payload.get("status") != ("passed" if passed else "incomplete"):
        raise G04AmendedContractError("G04 amended aggregate status differs")
    if payload.get("canonical_g04_completion_claim_allowed") is not passed:
        raise G04AmendedContractError("G04 completion claim flag differs")
    privacy = _require_mapping(payload.get("privacy"), "G04 progress privacy")
    if privacy.get("aggregate_only") is not True:
        raise G04AmendedContractError("G04 progress must be aggregate-only")
    if privacy.get("contains_item_hashes_rows_audio_labels_or_paths") is not False:
        raise G04AmendedContractError("G04 progress contains restricted fields")
    return payload


def build_completion_attestation(
    *,
    t43_report: Mapping[str, Any],
    t43_report_sha256: str,
    i30_report: Mapping[str, Any],
    i30_report_sha256: str,
    reviewer_attestation_sha256: str,
    reviewer_qualification_confirmed: bool,
    research_lead_approved: bool,
) -> dict[str, Any]:
    """Build completion only after both reports and human attestations pass."""

    if t43_report.get("status") != "passed":
        raise G04AmendedContractError("T43 report has not passed")
    if i30_report.get("status") != "passed":
        raise G04AmendedContractError("I30 report has not passed")
    if not reviewer_qualification_confirmed or not research_lead_approved:
        raise G04AmendedContractError(
            "reviewer and research-lead attestations required"
        )
    overall = _require_mapping(i30_report.get("overall"), "I30 overall")
    payload = {
        "schema_version": COMPLETION_SCHEMA_VERSION,
        "status": "passed",
        "aggregate_only": True,
        "contains_identifiers": False,
        "contains_rows": False,
        "contains_audio": False,
        "contains_labels": False,
        "contains_predictions": False,
        "locked_access_count": 0,
        "t43": {
            "expected_count": EXPECTED_T43_COUNT,
            "completed_count": EXPECTED_T43_COUNT,
            "uncertain_count": 0,
            "unresolved_count": 0,
            "invalid_resolution_count": 0,
            "report_sha256": _require_sha256(t43_report_sha256, "T43 report SHA"),
        },
        "i30": {
            "expected_count": EXPECTED_I30_COUNT,
            "completed_count": EXPECTED_I30_COUNT,
            "phone_level_accuracy": overall.get("phone_level_accuracy"),
            "minimum_phone_level_accuracy": MINIMUM_PHONE_ACCURACY,
            "total_phone_intervals": overall.get("total_phone_intervals"),
            "incorrect_phone_intervals": overall.get("incorrect_phone_intervals"),
            "strata_valid": i30_report.get("strata_valid"),
            "report_sha256": _require_sha256(i30_report_sha256, "I30 report SHA"),
        },
        "scorer_status": "passed",
        "reviewer_attestation_sha256": _require_sha256(
            reviewer_attestation_sha256, "reviewer attestation SHA"
        ),
        "reviewer_qualification_confirmed": True,
        "research_lead_approved": True,
        "consumer_contract_version": "g04_t43_i30_v1",
    }
    validate_completion_attestation(payload)
    return payload


def validate_completion_attestation(
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Validate the aggregate-only amended G04 payload used downstream."""

    _require_exact_keys(
        payload,
        {
            "schema_version",
            "status",
            "aggregate_only",
            "contains_identifiers",
            "contains_rows",
            "contains_audio",
            "contains_labels",
            "contains_predictions",
            "locked_access_count",
            "t43",
            "i30",
            "scorer_status",
            "reviewer_attestation_sha256",
            "reviewer_qualification_confirmed",
            "research_lead_approved",
            "consumer_contract_version",
        },
        "G04 completion",
    )
    if payload.get("schema_version") != COMPLETION_SCHEMA_VERSION:
        raise G04AmendedContractError("G04 completion schema differs")
    if payload.get("status") != "passed" or payload.get("scorer_status") != "passed":
        raise G04AmendedContractError("G04 completion is not passed")
    if payload.get("aggregate_only") is not True:
        raise G04AmendedContractError("G04 completion must be aggregate-only")
    for field in (
        "contains_identifiers",
        "contains_rows",
        "contains_audio",
        "contains_labels",
        "contains_predictions",
    ):
        if payload.get(field) is not False:
            raise G04AmendedContractError(f"G04 completion {field} must be false")
    if payload.get("locked_access_count") != 0:
        raise G04AmendedContractError("G04 completion accessed locked data")
    t43 = _require_mapping(payload.get("t43"), "G04 completion T43")
    if (
        t43.get("expected_count") != EXPECTED_T43_COUNT
        or t43.get("completed_count") != EXPECTED_T43_COUNT
    ):
        raise G04AmendedContractError("T43 completion count differs")
    for field in ("uncertain_count", "unresolved_count", "invalid_resolution_count"):
        if t43.get(field) != 0:
            raise G04AmendedContractError(f"T43 {field} is not zero")
    _require_sha256(t43.get("report_sha256"), "T43 report SHA")
    i30 = _require_mapping(payload.get("i30"), "G04 completion I30")
    if (
        i30.get("expected_count") != EXPECTED_I30_COUNT
        or i30.get("completed_count") != EXPECTED_I30_COUNT
    ):
        raise G04AmendedContractError("I30 completion count differs")
    accuracy = i30.get("phone_level_accuracy")
    if isinstance(accuracy, bool) or not isinstance(accuracy, (int, float)):
        raise G04AmendedContractError("I30 phone accuracy is invalid")
    accuracy = float(accuracy)
    if not math.isfinite(accuracy) or accuracy < MINIMUM_PHONE_ACCURACY:
        raise G04AmendedContractError("I30 phone accuracy gate failed")
    total = i30.get("total_phone_intervals")
    incorrect = i30.get("incorrect_phone_intervals")
    if (
        isinstance(total, bool)
        or not isinstance(total, int)
        or total <= 0
        or isinstance(incorrect, bool)
        or not isinstance(incorrect, int)
        or not 0 <= incorrect <= total
    ):
        raise G04AmendedContractError("I30 phone counts are invalid")
    expected_accuracy = 1 - incorrect / total
    if not math.isclose(accuracy, expected_accuracy, abs_tol=1e-12):
        raise G04AmendedContractError("I30 phone accuracy/count arithmetic differs")
    if i30.get("minimum_phone_level_accuracy") != MINIMUM_PHONE_ACCURACY:
        raise G04AmendedContractError("I30 minimum accuracy differs")
    if i30.get("strata_valid") is not True:
        raise G04AmendedContractError("I30 strata are invalid")
    _require_sha256(i30.get("report_sha256"), "I30 report SHA")
    _require_sha256(
        payload.get("reviewer_attestation_sha256"), "reviewer attestation SHA"
    )
    if payload.get("reviewer_qualification_confirmed") is not True:
        raise G04AmendedContractError("reviewer qualification is not confirmed")
    if payload.get("research_lead_approved") is not True:
        raise G04AmendedContractError("research lead approval is missing")
    if payload.get("consumer_contract_version") != "g04_t43_i30_v1":
        raise G04AmendedContractError("G04 consumer contract differs")
    return payload


__all__ = [
    "AMENDED_STATE_SCHEMA_VERSION",
    "COMPLETION_SCHEMA_VERSION",
    "EXPECTED_I30_COUNT",
    "EXPECTED_T43_COUNT",
    "G04AmendedContractError",
    "G04AmendedReviewStore",
    "G04Resolution",
    "G04ReviewProfile",
    "G04RootCause",
    "I30_REPORT_SCHEMA_VERSION",
    "MINIMUM_PHONE_ACCURACY",
    "PROGRESS_SCHEMA_VERSION",
    "T43_MANIFEST_SCHEMA_VERSION",
    "T43_REPORT_SCHEMA_VERSION",
    "AmendedReviewSubmission",
    "build_amended_progress",
    "build_completion_attestation",
    "build_i30_report",
    "build_t43_manifest",
    "build_t43_manifest_receipt",
    "build_t43_report",
    "export_amended_reviews",
    "selected_hashes_from_t43_manifest",
    "select_t43_items",
    "validate_completion_attestation",
    "validate_amended_progress",
    "validate_t43_manifest",
]
