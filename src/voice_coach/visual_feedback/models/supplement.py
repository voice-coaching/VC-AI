"""Strict privacy-safe serialization for ``VisualFeedbackSupplement``."""

from __future__ import annotations

from dataclasses import dataclass
import json
from ..errors import VisualFeedbackContractError
from .enums import VisualCueState, VisualEvidenceRelation, VisualFeedbackStatus
from .evidence import AdapterReceipt, MediaDeletionReceipt
from .integrity import (
    canonical_json_bytes,
    require_exact_keys,
    require_finite_number,
    require_integer,
    require_mapping,
    require_non_empty_string,
    require_safe_identifier,
    require_sha256,
)

VISUAL_FEEDBACK_SUPPLEMENT_SCHEMA_VERSION = "voice_coach.visual_feedback_supplement.v1"


@dataclass(frozen=True, slots=True)
class VisualCueEvidence:
    """Public aggregate cue evidence without frames or landmark sequences."""

    evidence_id: str
    cue_id: str
    cue_state: VisualCueState
    video_start_ms: int | None
    video_end_ms: int | None
    measurement_value: float | None
    measurement_unit: str | None
    calibrated_deviation: float | None
    measurement_backend: AdapterReceipt
    calibration_artifact_sha256: str | None
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_sha256(self.evidence_id, "evidence_id")
        require_safe_identifier(self.cue_id, "cue_id")
        if (self.video_start_ms is None) != (self.video_end_ms is None):
            raise VisualFeedbackContractError("visual evidence span must be complete")
        if self.video_start_ms is not None and self.video_end_ms is not None:
            require_integer(self.video_start_ms, "video_start_ms", minimum=0)
            require_integer(self.video_end_ms, "video_end_ms", minimum=1)
            if self.video_end_ms <= self.video_start_ms:
                raise VisualFeedbackContractError(
                    "visual evidence span must move forward"
                )
        if self.cue_state is VisualCueState.OBSERVED:
            if self.measurement_value is None or self.measurement_unit is None:
                raise VisualFeedbackContractError(
                    "observed visual evidence requires a measurement summary"
                )
            require_finite_number(self.measurement_value, "measurement_value")
            require_safe_identifier(self.measurement_unit, "measurement_unit")
        elif self.measurement_value is not None or self.measurement_unit is not None:
            raise VisualFeedbackContractError(
                "unobserved visual evidence must not carry a measurement"
            )
        if self.calibrated_deviation is not None:
            require_finite_number(self.calibrated_deviation, "calibrated_deviation")
            if self.cue_state is not VisualCueState.OBSERVED:
                raise VisualFeedbackContractError(
                    "only observed visual evidence may contain a deviation"
                )
            if self.calibration_artifact_sha256 is None:
                raise VisualFeedbackContractError(
                    "calibrated deviation requires an artifact reference"
                )
        if self.calibration_artifact_sha256 is not None:
            require_sha256(
                self.calibration_artifact_sha256, "calibration_artifact_sha256"
            )
        for reason in self.reason_codes:
            require_safe_identifier(reason, "reason_codes[]")

    def to_json_object(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "cue_id": self.cue_id,
            "cue_state": self.cue_state.value,
            "video_start_ms": self.video_start_ms,
            "video_end_ms": self.video_end_ms,
            "measurement_value": self.measurement_value,
            "measurement_unit": self.measurement_unit,
            "calibrated_deviation": self.calibrated_deviation,
            "measurement_backend": self.measurement_backend.to_json_object(),
            "calibration_artifact_sha256": self.calibration_artifact_sha256,
            "reason_codes": list(self.reason_codes),
        }

    @classmethod
    def from_json_object(cls, value: object) -> VisualCueEvidence:
        document = require_mapping(value, "visual_cue_evidence")
        require_exact_keys(
            document,
            {
                "evidence_id",
                "cue_id",
                "cue_state",
                "video_start_ms",
                "video_end_ms",
                "measurement_value",
                "measurement_unit",
                "calibrated_deviation",
                "measurement_backend",
                "calibration_artifact_sha256",
                "reason_codes",
            },
            "visual_cue_evidence",
        )
        try:
            cue_state = VisualCueState(document["cue_state"])
        except (TypeError, ValueError) as error:
            raise VisualFeedbackContractError("visual cue state is unknown") from error
        reasons = _reason_codes(document["reason_codes"], "visual_cue_evidence")
        return cls(
            evidence_id=require_sha256(
                document["evidence_id"], "visual_cue_evidence.evidence_id"
            ),
            cue_id=require_safe_identifier(
                document["cue_id"], "visual_cue_evidence.cue_id"
            ),
            cue_state=cue_state,
            video_start_ms=_optional_integer(
                document["video_start_ms"], "visual_cue_evidence.video_start_ms"
            ),
            video_end_ms=_optional_integer(
                document["video_end_ms"], "visual_cue_evidence.video_end_ms"
            ),
            measurement_value=_optional_number(
                document["measurement_value"],
                "visual_cue_evidence.measurement_value",
            ),
            measurement_unit=_optional_safe_identifier(
                document["measurement_unit"],
                "visual_cue_evidence.measurement_unit",
            ),
            calibrated_deviation=_optional_number(
                document["calibrated_deviation"],
                "visual_cue_evidence.calibrated_deviation",
            ),
            measurement_backend=_adapter_receipt(
                document["measurement_backend"],
                "visual_cue_evidence.measurement_backend",
            ),
            calibration_artifact_sha256=_optional_sha256(
                document["calibration_artifact_sha256"],
                "visual_cue_evidence.calibration_artifact_sha256",
            ),
            reason_codes=reasons,
        )


@dataclass(frozen=True, slots=True)
class SupplementalVisualActionCandidate:
    """Allowlisted candidate returned to the host, not a final user message."""

    action_id: str
    approved_claim_id: str
    catalog_revision: str
    renderer_key: str
    upstream_phone_anchor_ref: str
    supporting_visual_evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "action_id",
            "approved_claim_id",
            "catalog_revision",
            "renderer_key",
        ):
            require_safe_identifier(getattr(self, name), name)
        require_sha256(self.upstream_phone_anchor_ref, "upstream_phone_anchor_ref")
        if not self.supporting_visual_evidence_ids:
            raise VisualFeedbackContractError(
                "action candidate requires supporting visual evidence"
            )
        for evidence_id in self.supporting_visual_evidence_ids:
            require_sha256(evidence_id, "supporting_visual_evidence_ids[]")
        if len(set(self.supporting_visual_evidence_ids)) != len(
            self.supporting_visual_evidence_ids
        ):
            raise VisualFeedbackContractError(
                "supporting visual evidence ids must be unique"
            )

    def to_json_object(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "approved_claim_id": self.approved_claim_id,
            "catalog_revision": self.catalog_revision,
            "renderer_key": self.renderer_key,
            "upstream_phone_anchor_ref": self.upstream_phone_anchor_ref,
            "supporting_visual_evidence_ids": list(
                self.supporting_visual_evidence_ids
            ),
        }

    @classmethod
    def from_json_object(cls, value: object) -> SupplementalVisualActionCandidate:
        document = require_mapping(value, "supplemental_action_candidate")
        require_exact_keys(
            document,
            {
                "action_id",
                "approved_claim_id",
                "catalog_revision",
                "renderer_key",
                "upstream_phone_anchor_ref",
                "supporting_visual_evidence_ids",
            },
            "supplemental_action_candidate",
        )
        evidence_ids = document["supporting_visual_evidence_ids"]
        if not isinstance(evidence_ids, list):
            raise VisualFeedbackContractError(
                "supporting_visual_evidence_ids must be an array"
            )
        return cls(
            action_id=require_safe_identifier(
                document["action_id"], "supplemental_action_candidate.action_id"
            ),
            approved_claim_id=require_safe_identifier(
                document["approved_claim_id"],
                "supplemental_action_candidate.approved_claim_id",
            ),
            catalog_revision=require_safe_identifier(
                document["catalog_revision"],
                "supplemental_action_candidate.catalog_revision",
            ),
            renderer_key=require_safe_identifier(
                document["renderer_key"],
                "supplemental_action_candidate.renderer_key",
            ),
            upstream_phone_anchor_ref=require_sha256(
                document["upstream_phone_anchor_ref"],
                "supplemental_action_candidate.upstream_phone_anchor_ref",
            ),
            supporting_visual_evidence_ids=tuple(
                require_sha256(
                    evidence_id,
                    "supplemental_action_candidate.supporting_visual_evidence_ids[]",
                )
                for evidence_id in evidence_ids
            ),
        )


@dataclass(frozen=True, slots=True)
class VisualFeedbackSupplement:
    """The only public output of the visual sidecar."""

    status: VisualFeedbackStatus
    reason_codes: tuple[str, ...]
    upstream_analysis_ref: str
    upstream_binding_ref: str | None
    upstream_phone_anchor_ref: str | None
    cue_evidence: tuple[VisualCueEvidence, ...]
    evidence_relation: VisualEvidenceRelation
    supplemental_action_candidate: SupplementalVisualActionCandidate | None
    visual_backend_receipts: tuple[AdapterReceipt, ...]
    reference_calibration_receipts: tuple[AdapterReceipt, ...]
    media_deletion_receipt: MediaDeletionReceipt
    notices: tuple[str, ...]
    schema_version: str = VISUAL_FEEDBACK_SUPPLEMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != VISUAL_FEEDBACK_SUPPLEMENT_SCHEMA_VERSION:
            raise VisualFeedbackContractError("visual supplement schema differs")
        require_sha256(self.upstream_analysis_ref, "upstream_analysis_ref")
        if self.upstream_binding_ref is not None:
            require_sha256(self.upstream_binding_ref, "upstream_binding_ref")
        if self.upstream_phone_anchor_ref is not None:
            require_sha256(
                self.upstream_phone_anchor_ref, "upstream_phone_anchor_ref"
            )
        for reason in self.reason_codes:
            require_safe_identifier(reason, "reason_codes[]")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise VisualFeedbackContractError("supplement reason_codes must be unique")
        for notice in self.notices:
            require_safe_identifier(notice, "notices[]")
        evidence_ids = [evidence.evidence_id for evidence in self.cue_evidence]
        if len(set(evidence_ids)) != len(evidence_ids):
            raise VisualFeedbackContractError("cue evidence ids must be unique")
        if self.status is VisualFeedbackStatus.VISUAL_FEEDBACK_READY:
            self._validate_ready_result(evidence_ids)
        elif self.supplemental_action_candidate is not None:
            raise VisualFeedbackContractError(
                "only visual_feedback_ready may contain an action candidate"
            )
        if (
            self.media_deletion_receipt.status == "failed"
            and self.status is not VisualFeedbackStatus.VISUAL_FAILED_CLOSED
        ):
            raise VisualFeedbackContractError(
                "failed media deletion requires visual_failed_closed"
            )
        if (
            self.evidence_relation is VisualEvidenceRelation.SUPPORTS_UPSTREAM
            and self.status is not VisualFeedbackStatus.VISUAL_FEEDBACK_READY
        ):
            raise VisualFeedbackContractError(
                "supports_upstream is reserved for visual_feedback_ready"
            )
        if (
            self.evidence_relation is VisualEvidenceRelation.CONFLICTS_WITH_UPSTREAM
            and self.status is not VisualFeedbackStatus.VISUAL_UNCERTAIN
        ):
            raise VisualFeedbackContractError(
                "conflicts_with_upstream requires visual_uncertain"
            )

    def _validate_ready_result(self, evidence_ids: list[str]) -> None:
        if self.supplemental_action_candidate is None:
            raise VisualFeedbackContractError(
                "visual_feedback_ready requires one action candidate"
            )
        if not self.visual_backend_receipts or not self.reference_calibration_receipts:
            raise VisualFeedbackContractError(
                "visual_feedback_ready requires backend and calibration receipts"
            )
        if self.evidence_relation is not VisualEvidenceRelation.SUPPORTS_UPSTREAM:
            raise VisualFeedbackContractError(
                "visual_feedback_ready must support upstream"
            )
        if self.upstream_binding_ref is None or self.upstream_phone_anchor_ref is None:
            raise VisualFeedbackContractError(
                "visual_feedback_ready requires binding and phone anchor references"
            )
        if not any(
            evidence.cue_state is VisualCueState.OBSERVED
            for evidence in self.cue_evidence
        ):
            raise VisualFeedbackContractError(
                "visual_feedback_ready requires observed cue evidence"
            )
        if not set(
            self.supplemental_action_candidate.supporting_visual_evidence_ids
        ).issubset(evidence_ids):
            raise VisualFeedbackContractError(
                "action candidate references unknown visual evidence"
            )
        if (
            self.supplemental_action_candidate.upstream_phone_anchor_ref
            != self.upstream_phone_anchor_ref
        ):
            raise VisualFeedbackContractError(
                "action candidate must preserve the supplement phone anchor"
            )

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": self.status.value,
            "reason_codes": list(self.reason_codes),
            "upstream_analysis_ref": self.upstream_analysis_ref,
            "upstream_binding_ref": self.upstream_binding_ref,
            "upstream_phone_anchor_ref": self.upstream_phone_anchor_ref,
            "cue_evidence": [item.to_json_object() for item in self.cue_evidence],
            "evidence_relation": self.evidence_relation.value,
            "supplemental_action_candidate": (
                None
                if self.supplemental_action_candidate is None
                else self.supplemental_action_candidate.to_json_object()
            ),
            "visual_backend_receipts": [
                receipt.to_json_object() for receipt in self.visual_backend_receipts
            ],
            "reference_calibration_receipts": [
                receipt.to_json_object()
                for receipt in self.reference_calibration_receipts
            ],
            "media_deletion_receipt": self.media_deletion_receipt.to_json_object(),
            "notices": list(self.notices),
        }

    def to_json(self) -> str:
        """Serialize with canonical ordering and no non-finite numbers."""

        return canonical_json_bytes(self.to_json_object()).decode("utf-8")

    @classmethod
    def from_json(cls, value: str) -> VisualFeedbackSupplement:
        try:
            document = json.loads(value)
        except json.JSONDecodeError as error:
            raise VisualFeedbackContractError(
                "visual supplement is not JSON"
            ) from error
        return cls.from_json_object(document)

    @classmethod
    def from_json_object(cls, value: object) -> VisualFeedbackSupplement:
        document = require_mapping(value, "visual_feedback_supplement")
        require_exact_keys(
            document,
            {
                "schema_version",
                "status",
                "reason_codes",
                "upstream_analysis_ref",
                "upstream_binding_ref",
                "upstream_phone_anchor_ref",
                "cue_evidence",
                "evidence_relation",
                "supplemental_action_candidate",
                "visual_backend_receipts",
                "reference_calibration_receipts",
                "media_deletion_receipt",
                "notices",
            },
            "visual_feedback_supplement",
        )
        try:
            status = VisualFeedbackStatus(document["status"])
            relation = VisualEvidenceRelation(document["evidence_relation"])
        except (TypeError, ValueError) as error:
            raise VisualFeedbackContractError(
                "visual supplement contains an unknown enum"
            ) from error
        cue_evidence = document["cue_evidence"]
        visual_receipts = document["visual_backend_receipts"]
        calibration_receipts = document["reference_calibration_receipts"]
        if not isinstance(cue_evidence, list):
            raise VisualFeedbackContractError("cue_evidence must be an array")
        if not isinstance(visual_receipts, list) or not isinstance(
            calibration_receipts, list
        ):
            raise VisualFeedbackContractError("adapter receipts must be arrays")
        raw_candidate = document["supplemental_action_candidate"]
        return cls(
            schema_version=require_non_empty_string(
                document["schema_version"], "visual_feedback_supplement.schema_version"
            ),
            status=status,
            reason_codes=_reason_codes(
                document["reason_codes"], "visual_feedback_supplement"
            ),
            upstream_analysis_ref=require_sha256(
                document["upstream_analysis_ref"],
                "visual_feedback_supplement.upstream_analysis_ref",
            ),
            upstream_binding_ref=_optional_sha256(
                document["upstream_binding_ref"],
                "visual_feedback_supplement.upstream_binding_ref",
            ),
            upstream_phone_anchor_ref=_optional_sha256(
                document["upstream_phone_anchor_ref"],
                "visual_feedback_supplement.upstream_phone_anchor_ref",
            ),
            cue_evidence=tuple(
                VisualCueEvidence.from_json_object(item) for item in cue_evidence
            ),
            evidence_relation=relation,
            supplemental_action_candidate=(
                None
                if raw_candidate is None
                else SupplementalVisualActionCandidate.from_json_object(raw_candidate)
            ),
            visual_backend_receipts=tuple(
                _adapter_receipt(item, "visual_backend_receipts[]")
                for item in visual_receipts
            ),
            reference_calibration_receipts=tuple(
                _adapter_receipt(item, "reference_calibration_receipts[]")
                for item in calibration_receipts
            ),
            media_deletion_receipt=_deletion_receipt(
                document["media_deletion_receipt"]
            ),
            notices=_reason_codes(document["notices"], "visual_feedback_supplement"),
        )


def _reason_codes(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise VisualFeedbackContractError(f"{name} reason codes must be an array")
    return tuple(
        require_safe_identifier(item, f"{name}.reason_codes[]") for item in value
    )


def _optional_integer(value: object, name: str) -> int | None:
    if value is None:
        return None
    return require_integer(value, name, minimum=0)


def _optional_number(value: object, name: str) -> float | None:
    if value is None:
        return None
    return require_finite_number(value, name)


def _optional_safe_identifier(value: object, name: str) -> str | None:
    if value is None:
        return None
    return require_safe_identifier(value, name)


def _optional_sha256(value: object, name: str) -> str | None:
    if value is None:
        return None
    return require_sha256(value, name)


def _adapter_receipt(value: object, name: str) -> AdapterReceipt:
    document = require_mapping(value, name)
    require_exact_keys(
        document,
        {"backend_id", "backend_revision", "status", "reason_codes"},
        name,
    )
    return AdapterReceipt(
        backend_id=require_safe_identifier(
            document["backend_id"], f"{name}.backend_id"
        ),
        backend_revision=require_safe_identifier(
            document["backend_revision"], f"{name}.backend_revision"
        ),
        status=require_safe_identifier(document["status"], f"{name}.status"),
        reason_codes=_reason_codes(document["reason_codes"], name),
    )


def _deletion_receipt(value: object) -> MediaDeletionReceipt:
    document = require_mapping(value, "media_deletion_receipt")
    require_exact_keys(
        document,
        {"status", "deletion_receipt_sha256", "backend_receipt"},
        "media_deletion_receipt",
    )
    return MediaDeletionReceipt(
        status=require_safe_identifier(
            document["status"], "media_deletion_receipt.status"
        ),
        deletion_receipt_sha256=require_sha256(
            document["deletion_receipt_sha256"],
            "media_deletion_receipt.deletion_receipt_sha256",
        ),
        backend_receipt=_adapter_receipt(
            document["backend_receipt"], "media_deletion_receipt.backend_receipt"
        ),
    )


__all__ = [
    "VISUAL_FEEDBACK_SUPPLEMENT_SCHEMA_VERSION",
    "SupplementalVisualActionCandidate",
    "VisualCueEvidence",
    "VisualFeedbackSupplement",
]
