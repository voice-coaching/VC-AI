"""Frozen visual calibration and claim-catalog artifact contracts."""

from __future__ import annotations

from dataclasses import dataclass
from ..errors import VisualArtifactUnavailableError, VisualFeedbackContractError
from .enums import CalibrationScope, DeviationDirection, VisualCueState
from .evidence import LipCueMeasurement
from .integrity import (
    require_exact_keys,
    require_finite_number,
    require_mapping,
    require_non_empty_string,
    require_safe_identifier,
    require_sha256,
    require_string_tuple,
    require_text_token,
    sha256_document,
)

CALIBRATION_SCHEMA_VERSION = "voice_coach.visual_calibration.v1"
CLAIM_CATALOG_SCHEMA_VERSION = "voice_coach.visual_claim_catalog.v1"


@dataclass(frozen=True, slots=True)
class CalibrationRule:
    """Frozen reference center, support interval, and feedback trigger."""

    cue_id: str
    measurement_unit: str
    expected_phones: tuple[str, ...]
    context_id: str
    reference_center: float
    reference_scale: float
    support_minimum: float
    support_maximum: float
    feedback_minimum_absolute_deviation: float

    def __post_init__(self) -> None:
        require_safe_identifier(self.cue_id, "cue_id")
        require_safe_identifier(self.measurement_unit, "measurement_unit")
        if not self.expected_phones:
            raise VisualFeedbackContractError("expected_phones must not be empty")
        for phone in self.expected_phones:
            require_text_token(phone, "expected_phones[]")
        if len(set(self.expected_phones)) != len(self.expected_phones):
            raise VisualFeedbackContractError("expected_phones must be unique")
        require_safe_identifier(self.context_id, "context_id")
        center = require_finite_number(self.reference_center, "reference_center")
        scale = require_finite_number(self.reference_scale, "reference_scale")
        minimum = require_finite_number(self.support_minimum, "support_minimum")
        maximum = require_finite_number(self.support_maximum, "support_maximum")
        trigger = require_finite_number(
            self.feedback_minimum_absolute_deviation,
            "feedback_minimum_absolute_deviation",
        )
        if scale <= 0:
            raise VisualFeedbackContractError("reference_scale must be positive")
        if maximum <= minimum or not minimum <= center <= maximum:
            raise VisualFeedbackContractError(
                "calibration support must contain its reference center"
            )
        if trigger <= 0:
            raise VisualFeedbackContractError(
                "feedback_minimum_absolute_deviation must be positive"
            )

    def to_json_object(self) -> dict[str, object]:
        return {
            "cue_id": self.cue_id,
            "measurement_unit": self.measurement_unit,
            "expected_phones": list(self.expected_phones),
            "context_id": self.context_id,
            "reference_center": self.reference_center,
            "reference_scale": self.reference_scale,
            "support_minimum": self.support_minimum,
            "support_maximum": self.support_maximum,
            "feedback_minimum_absolute_deviation": (
                self.feedback_minimum_absolute_deviation
            ),
        }

    @classmethod
    def from_json_object(cls, value: object) -> CalibrationRule:
        document = require_mapping(value, "calibration_rule")
        require_exact_keys(
            document,
            {
                "cue_id",
                "measurement_unit",
                "expected_phones",
                "context_id",
                "reference_center",
                "reference_scale",
                "support_minimum",
                "support_maximum",
                "feedback_minimum_absolute_deviation",
            },
            "calibration_rule",
        )
        return cls(
            cue_id=require_safe_identifier(
                document["cue_id"], "calibration_rule.cue_id"
            ),
            measurement_unit=require_safe_identifier(
                document["measurement_unit"],
                "calibration_rule.measurement_unit",
            ),
            expected_phones=require_string_tuple(
                document["expected_phones"], "calibration_rule.expected_phones"
            ),
            context_id=require_safe_identifier(
                document["context_id"], "calibration_rule.context_id"
            ),
            reference_center=require_finite_number(
                document["reference_center"], "calibration_rule.reference_center"
            ),
            reference_scale=require_finite_number(
                document["reference_scale"], "calibration_rule.reference_scale"
            ),
            support_minimum=require_finite_number(
                document["support_minimum"], "calibration_rule.support_minimum"
            ),
            support_maximum=require_finite_number(
                document["support_maximum"], "calibration_rule.support_maximum"
            ),
            feedback_minimum_absolute_deviation=require_finite_number(
                document["feedback_minimum_absolute_deviation"],
                "calibration_rule.feedback_minimum_absolute_deviation",
            ),
        )


@dataclass(frozen=True, slots=True)
class CalibratedCue:
    """Result of applying one frozen visual rule to one observed cue."""

    cue_id: str
    state: VisualCueState
    calibrated_deviation: float | None
    direction: DeviationDirection | None
    calibration_artifact_sha256: str
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_safe_identifier(self.cue_id, "cue_id")
        require_sha256(
            self.calibration_artifact_sha256,
            "calibration_artifact_sha256",
        )
        if self.calibrated_deviation is not None:
            require_finite_number(
                self.calibrated_deviation,
                "calibrated_deviation",
            )
            if self.state is not VisualCueState.OBSERVED:
                raise VisualFeedbackContractError(
                    "only observed calibration may contain a deviation"
                )
        if self.direction is not None and (
            self.state is not VisualCueState.OBSERVED
            or self.calibrated_deviation is None
        ):
            raise VisualFeedbackContractError(
                "calibration direction requires an observed deviation"
            )
        for reason in self.reason_codes:
            require_safe_identifier(reason, "reason_codes[]")


@dataclass(frozen=True, slots=True)
class VisualCalibrationArtifact:
    """Immutable calibration artifact; synthetic scope is never user-ready."""

    artifact_id: str
    revision: str
    scope: CalibrationScope
    capture_contract_id: str
    population_id_sha256: str
    rights_status: str
    rules: tuple[CalibrationRule, ...]
    artifact_sha256: str
    schema_version: str = CALIBRATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CALIBRATION_SCHEMA_VERSION:
            raise VisualFeedbackContractError("calibration schema_version differs")
        require_safe_identifier(self.artifact_id, "artifact_id")
        require_safe_identifier(self.revision, "revision")
        require_safe_identifier(self.capture_contract_id, "capture_contract_id")
        require_sha256(self.population_id_sha256, "population_id_sha256")
        if self.rights_status != "approved":
            raise VisualArtifactUnavailableError(
                "calibration rights_status must be approved"
            )
        if not self.rules:
            raise VisualFeedbackContractError("calibration rules must not be empty")
        keys = [
            (rule.cue_id, rule.expected_phones, rule.context_id) for rule in self.rules
        ]
        if len(set(keys)) != len(keys):
            raise VisualFeedbackContractError("calibration rules must be unique")
        require_sha256(self.artifact_sha256, "artifact_sha256")
        if self.artifact_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("calibration artifact digest differs")

    @classmethod
    def issue(
        cls,
        *,
        artifact_id: str,
        revision: str,
        scope: CalibrationScope,
        capture_contract_id: str,
        population_id_sha256: str,
        rules: tuple[CalibrationRule, ...],
        rights_status: str = "approved",
    ) -> VisualCalibrationArtifact:
        unsigned = {
            "schema_version": CALIBRATION_SCHEMA_VERSION,
            "artifact_id": artifact_id,
            "revision": revision,
            "scope": scope.value,
            "capture_contract_id": capture_contract_id,
            "population_id_sha256": population_id_sha256,
            "rights_status": rights_status,
            "rules": [rule.to_json_object() for rule in rules],
        }
        return cls(
            artifact_id=artifact_id,
            revision=revision,
            scope=scope,
            capture_contract_id=capture_contract_id,
            population_id_sha256=population_id_sha256,
            rights_status=rights_status,
            rules=rules,
            artifact_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "artifact_id": self.artifact_id,
            "revision": self.revision,
            "scope": self.scope.value,
            "capture_contract_id": self.capture_contract_id,
            "population_id_sha256": self.population_id_sha256,
            "rights_status": self.rights_status,
            "rules": [rule.to_json_object() for rule in self.rules],
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "artifact_sha256": self.artifact_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> VisualCalibrationArtifact:
        document = require_mapping(value, "calibration_artifact")
        require_exact_keys(
            document,
            {
                "schema_version",
                "artifact_id",
                "revision",
                "scope",
                "capture_contract_id",
                "population_id_sha256",
                "rights_status",
                "rules",
                "artifact_sha256",
            },
            "calibration_artifact",
        )
        try:
            scope = CalibrationScope(document["scope"])
        except (TypeError, ValueError) as error:
            raise VisualFeedbackContractError("calibration scope is unknown") from error
        raw_rules = document["rules"]
        if not isinstance(raw_rules, list):
            raise VisualFeedbackContractError("calibration rules must be an array")
        return cls(
            schema_version=require_non_empty_string(
                document["schema_version"], "calibration_artifact.schema_version"
            ),
            artifact_id=require_safe_identifier(
                document["artifact_id"], "calibration_artifact.artifact_id"
            ),
            revision=require_safe_identifier(
                document["revision"], "calibration_artifact.revision"
            ),
            scope=scope,
            capture_contract_id=require_safe_identifier(
                document["capture_contract_id"],
                "calibration_artifact.capture_contract_id",
            ),
            population_id_sha256=require_sha256(
                document["population_id_sha256"],
                "calibration_artifact.population_id_sha256",
            ),
            rights_status=require_safe_identifier(
                document["rights_status"], "calibration_artifact.rights_status"
            ),
            rules=tuple(CalibrationRule.from_json_object(item) for item in raw_rules),
            artifact_sha256=require_sha256(
                document["artifact_sha256"],
                "calibration_artifact.artifact_sha256",
            ),
        )

    def calibrate(
        self,
        *,
        cue: LipCueMeasurement,
        expected_phone: str,
        context_id: str,
    ) -> CalibratedCue:
        """Apply the exact matching rule without cross-phone fallback."""

        if cue.state is not VisualCueState.OBSERVED or cue.value is None:
            return CalibratedCue(
                cue_id=cue.cue_id,
                state=cue.state,
                calibrated_deviation=None,
                direction=None,
                calibration_artifact_sha256=self.artifact_sha256,
                reason_codes=cue.reason_codes,
            )
        rule = next(
            (
                item
                for item in self.rules
                if item.cue_id == cue.cue_id
                and expected_phone in item.expected_phones
                and item.context_id == context_id
            ),
            None,
        )
        if rule is None:
            return CalibratedCue(
                cue_id=cue.cue_id,
                state=VisualCueState.NOT_OBSERVABLE,
                calibrated_deviation=None,
                direction=None,
                calibration_artifact_sha256=self.artifact_sha256,
                reason_codes=("calibration_rule_not_available",),
            )
        if cue.unit != rule.measurement_unit:
            return CalibratedCue(
                cue_id=cue.cue_id,
                state=VisualCueState.NOT_OBSERVABLE,
                calibrated_deviation=None,
                direction=None,
                calibration_artifact_sha256=self.artifact_sha256,
                reason_codes=("calibration_measurement_unit_mismatch",),
            )
        if not rule.support_minimum <= cue.value <= rule.support_maximum:
            return CalibratedCue(
                cue_id=cue.cue_id,
                state=VisualCueState.OUT_OF_DISTRIBUTION,
                calibrated_deviation=None,
                direction=None,
                calibration_artifact_sha256=self.artifact_sha256,
                reason_codes=("cue_outside_calibration_support",),
            )
        deviation = (cue.value - rule.reference_center) / rule.reference_scale
        if abs(deviation) < rule.feedback_minimum_absolute_deviation:
            return CalibratedCue(
                cue_id=cue.cue_id,
                state=VisualCueState.OBSERVED,
                calibrated_deviation=deviation,
                direction=None,
                calibration_artifact_sha256=self.artifact_sha256,
                reason_codes=("deviation_below_feedback_trigger",),
            )
        direction = (
            DeviationDirection.ABOVE_REFERENCE
            if deviation > 0
            else DeviationDirection.BELOW_REFERENCE
        )
        return CalibratedCue(
            cue_id=cue.cue_id,
            state=VisualCueState.OBSERVED,
            calibrated_deviation=deviation,
            direction=direction,
            calibration_artifact_sha256=self.artifact_sha256,
        )


@dataclass(frozen=True, slots=True)
class VisualClaim:
    """Allowlisted action identifier; it contains no learner-facing free prose."""

    action_id: str
    approved_claim_id: str
    renderer_key: str
    cue_id: str
    expected_phones: tuple[str, ...]
    context_id: str
    direction: DeviationDirection

    def __post_init__(self) -> None:
        for name in ("action_id", "approved_claim_id", "renderer_key", "cue_id"):
            require_safe_identifier(getattr(self, name), name)
        require_safe_identifier(self.context_id, "context_id")
        if not self.expected_phones:
            raise VisualFeedbackContractError("claim expected_phones must not be empty")
        for phone in self.expected_phones:
            require_text_token(phone, "expected_phones[]")
        if len(set(self.expected_phones)) != len(self.expected_phones):
            raise VisualFeedbackContractError("claim expected_phones must be unique")

    def to_json_object(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "approved_claim_id": self.approved_claim_id,
            "renderer_key": self.renderer_key,
            "cue_id": self.cue_id,
            "expected_phones": list(self.expected_phones),
            "context_id": self.context_id,
            "direction": self.direction.value,
        }

    @classmethod
    def from_json_object(cls, value: object) -> VisualClaim:
        document = require_mapping(value, "visual_claim")
        require_exact_keys(
            document,
            {
                "action_id",
                "approved_claim_id",
                "renderer_key",
                "cue_id",
                "expected_phones",
                "context_id",
                "direction",
            },
            "visual_claim",
        )
        try:
            direction = DeviationDirection(document["direction"])
        except (TypeError, ValueError) as error:
            raise VisualFeedbackContractError("claim direction is unknown") from error
        return cls(
            action_id=require_safe_identifier(
                document["action_id"], "visual_claim.action_id"
            ),
            approved_claim_id=require_safe_identifier(
                document["approved_claim_id"], "visual_claim.approved_claim_id"
            ),
            renderer_key=require_safe_identifier(
                document["renderer_key"], "visual_claim.renderer_key"
            ),
            cue_id=require_safe_identifier(
                document["cue_id"], "visual_claim.cue_id"
            ),
            expected_phones=require_string_tuple(
                document["expected_phones"], "visual_claim.expected_phones"
            ),
            context_id=require_safe_identifier(
                document["context_id"], "visual_claim.context_id"
            ),
            direction=direction,
        )


@dataclass(frozen=True, slots=True)
class VisualClaimCatalog:
    """Frozen catalog mapping calibrated cues to action and claim identifiers."""

    catalog_id: str
    revision: str
    scope: CalibrationScope
    claims: tuple[VisualClaim, ...]
    catalog_sha256: str
    schema_version: str = CLAIM_CATALOG_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CLAIM_CATALOG_SCHEMA_VERSION:
            raise VisualFeedbackContractError("claim catalog schema_version differs")
        require_safe_identifier(self.catalog_id, "catalog_id")
        require_safe_identifier(self.revision, "revision")
        if not self.claims:
            raise VisualFeedbackContractError("claim catalog must not be empty")
        claim_ids = [claim.approved_claim_id for claim in self.claims]
        if len(set(claim_ids)) != len(claim_ids):
            raise VisualFeedbackContractError("approved claim ids must be unique")
        keys = [
            (claim.cue_id, claim.expected_phones, claim.context_id, claim.direction)
            for claim in self.claims
        ]
        if len(set(keys)) != len(keys):
            raise VisualFeedbackContractError("claim lookup keys must be unique")
        require_sha256(self.catalog_sha256, "catalog_sha256")
        if self.catalog_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("claim catalog digest differs")

    @classmethod
    def issue(
        cls,
        *,
        catalog_id: str,
        revision: str,
        scope: CalibrationScope,
        claims: tuple[VisualClaim, ...],
    ) -> VisualClaimCatalog:
        unsigned = {
            "schema_version": CLAIM_CATALOG_SCHEMA_VERSION,
            "catalog_id": catalog_id,
            "revision": revision,
            "scope": scope.value,
            "claims": [claim.to_json_object() for claim in claims],
        }
        return cls(
            catalog_id=catalog_id,
            revision=revision,
            scope=scope,
            claims=claims,
            catalog_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "catalog_id": self.catalog_id,
            "revision": self.revision,
            "scope": self.scope.value,
            "claims": [claim.to_json_object() for claim in self.claims],
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "catalog_sha256": self.catalog_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> VisualClaimCatalog:
        document = require_mapping(value, "visual_claim_catalog")
        require_exact_keys(
            document,
            {
                "schema_version",
                "catalog_id",
                "revision",
                "scope",
                "claims",
                "catalog_sha256",
            },
            "visual_claim_catalog",
        )
        try:
            scope = CalibrationScope(document["scope"])
        except (TypeError, ValueError) as error:
            raise VisualFeedbackContractError(
                "claim catalog scope is unknown"
            ) from error
        raw_claims = document["claims"]
        if not isinstance(raw_claims, list):
            raise VisualFeedbackContractError("claim catalog claims must be an array")
        return cls(
            schema_version=require_non_empty_string(
                document["schema_version"], "visual_claim_catalog.schema_version"
            ),
            catalog_id=require_safe_identifier(
                document["catalog_id"], "visual_claim_catalog.catalog_id"
            ),
            revision=require_safe_identifier(
                document["revision"], "visual_claim_catalog.revision"
            ),
            scope=scope,
            claims=tuple(VisualClaim.from_json_object(item) for item in raw_claims),
            catalog_sha256=require_sha256(
                document["catalog_sha256"], "visual_claim_catalog.catalog_sha256"
            ),
        )

    def lookup(
        self,
        *,
        cue_id: str,
        expected_phone: str,
        context_id: str,
        direction: DeviationDirection,
    ) -> VisualClaim | None:
        return next(
            (
                claim
                for claim in self.claims
                if claim.cue_id == cue_id
                and expected_phone in claim.expected_phones
                and claim.context_id == context_id
                and claim.direction is direction
            ),
            None,
        )


__all__ = [
    "CALIBRATION_SCHEMA_VERSION",
    "CLAIM_CATALOG_SCHEMA_VERSION",
    "CalibratedCue",
    "CalibrationRule",
    "VisualCalibrationArtifact",
    "VisualClaim",
    "VisualClaimCatalog",
]
