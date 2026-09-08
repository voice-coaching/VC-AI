"""Minimal production contract for visual pronunciation feedback.

P6/P8 are small release-time summaries. They contain no dataset, archive,
partition, speaker, or media-path fields and are never recomputed per video.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..errors import VisualFeedbackContractError
from .artifacts import VisualCalibrationArtifact, VisualClaimCatalog
from .enums import CalibrationScope
from .integrity import (
    require_exact_keys, require_integer, require_mapping,
    require_non_empty_string, require_safe_identifier, require_sha256,
    sha256_document,
)

IDENTITY_SCHEMA = "voice_coach.visual_runtime_pipeline_identity.v4"
CLAIM_POLICY_SCHEMA = "voice_coach.visual_claim_policy.v1"
P6_SCHEMA = "voice_coach.p6_calibration_receipt.v1"
P8_SCHEMA = "voice_coach.p8_evaluation_receipt.v2"
BUNDLE_SCHEMA = "voice_coach.visual_runtime_bundle.v4"
ATTEMPT_SCHEMA = "voice_coach.attempt_visual_reliability_receipt.v4"
_FORBIDDEN = frozenset(
    {"airflow", "diagnosis", "glottis", "medical", "tongue", "velum"}
)


def _identifiers(value: object, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise VisualFeedbackContractError(f"{name} must be an array")
    result = tuple(require_safe_identifier(item, f"{name}[]") for item in value)
    if len(set(result)) != len(result):
        raise VisualFeedbackContractError(f"{name} must not contain duplicates")
    return result


def _digest_fields(document: object, *, name: str, fields: set[str]) -> dict[str, object]:
    value = dict(require_mapping(document, name))
    require_exact_keys(value, fields, name)
    return value


@dataclass(frozen=True, slots=True)
class VisualRuntimePipelineIdentity:
    landmark_model_sha256: str
    video_qc_decision_policy_sha256: str
    av_sync_decision_policy_sha256: str
    media_capture_contract_id: str
    visual_observation_contract_id: str
    population_id_sha256: str
    identity_sha256: str
    schema_version: str = IDENTITY_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != IDENTITY_SCHEMA:
            raise VisualFeedbackContractError("visual runtime identity schema differs")
        for field in (
            "landmark_model_sha256", "video_qc_decision_policy_sha256",
            "av_sync_decision_policy_sha256", "population_id_sha256",
            "identity_sha256",
        ):
            require_sha256(getattr(self, field), field)
        require_safe_identifier(self.media_capture_contract_id, "media_capture_contract_id")
        require_safe_identifier(self.visual_observation_contract_id, "visual_observation_contract_id")
        if self.identity_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("visual runtime identity digest differs")

    @classmethod
    def issue(cls, **values: str) -> VisualRuntimePipelineIdentity:
        unsigned = {"schema_version": IDENTITY_SCHEMA, **values}
        return cls(**unsigned, identity_sha256=sha256_document(unsigned))

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "landmark_model_sha256": self.landmark_model_sha256,
            "video_qc_decision_policy_sha256": self.video_qc_decision_policy_sha256,
            "av_sync_decision_policy_sha256": self.av_sync_decision_policy_sha256,
            "media_capture_contract_id": self.media_capture_contract_id,
            "visual_observation_contract_id": self.visual_observation_contract_id,
            "population_id_sha256": self.population_id_sha256,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "identity_sha256": self.identity_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> VisualRuntimePipelineIdentity:
        fields = {
            "schema_version", "landmark_model_sha256",
            "video_qc_decision_policy_sha256", "av_sync_decision_policy_sha256",
            "media_capture_contract_id", "visual_observation_contract_id",
            "population_id_sha256", "identity_sha256",
        }
        doc = _digest_fields(value, name="pipeline_identity", fields=fields)
        return cls(
            schema_version=require_non_empty_string(doc["schema_version"], "pipeline_identity.schema_version"),
            landmark_model_sha256=require_sha256(doc["landmark_model_sha256"], "pipeline_identity.landmark_model_sha256"),
            video_qc_decision_policy_sha256=require_sha256(doc["video_qc_decision_policy_sha256"], "pipeline_identity.video_qc_decision_policy_sha256"),
            av_sync_decision_policy_sha256=require_sha256(doc["av_sync_decision_policy_sha256"], "pipeline_identity.av_sync_decision_policy_sha256"),
            media_capture_contract_id=require_safe_identifier(doc["media_capture_contract_id"], "pipeline_identity.media_capture_contract_id"),
            visual_observation_contract_id=require_safe_identifier(doc["visual_observation_contract_id"], "pipeline_identity.visual_observation_contract_id"),
            population_id_sha256=require_sha256(doc["population_id_sha256"], "pipeline_identity.population_id_sha256"),
            identity_sha256=require_sha256(doc["identity_sha256"], "pipeline_identity.identity_sha256"),
        )


@dataclass(frozen=True, slots=True)
class RuntimeClaimBinding:
    approved_claim_id: str
    action_id: str
    renderer_key: str
    cue_id: str

    def __post_init__(self) -> None:
        for field in ("approved_claim_id", "action_id", "renderer_key", "cue_id"):
            require_safe_identifier(getattr(self, field), field)

    def to_json_object(self) -> dict[str, object]:
        return {field: getattr(self, field) for field in (
            "approved_claim_id", "action_id", "renderer_key", "cue_id"
        )}

    @classmethod
    def from_json_object(cls, value: object) -> RuntimeClaimBinding:
        fields = {"approved_claim_id", "action_id", "renderer_key", "cue_id"}
        doc = _digest_fields(value, name="runtime_claim_binding", fields=fields)
        return cls(**{field: require_safe_identifier(doc[field], f"runtime_claim_binding.{field}") for field in fields})


@dataclass(frozen=True, slots=True)
class RuntimeVisualClaimPolicy:
    policy_revision: str
    allowed_cue_ids: tuple[str, ...]
    allowed_renderer_keys: tuple[str, ...]
    allowed_claim_bindings: tuple[RuntimeClaimBinding, ...]
    forbidden_identifier_tokens: tuple[str, ...]
    policy_sha256: str
    schema_version: str = CLAIM_POLICY_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != CLAIM_POLICY_SCHEMA:
            raise VisualFeedbackContractError("runtime claim policy schema differs")
        require_safe_identifier(self.policy_revision, "policy_revision")
        if not self.allowed_cue_ids or not self.allowed_renderer_keys or not self.allowed_claim_bindings:
            raise VisualFeedbackContractError("runtime claim policy allowlists are empty")
        for values, name in (
            (self.allowed_cue_ids, "allowed_cue_ids"),
            (self.allowed_renderer_keys, "allowed_renderer_keys"),
            (self.forbidden_identifier_tokens, "forbidden_identifier_tokens"),
        ):
            _identifiers(values, name)
        if len(set(self.allowed_claim_bindings)) != len(self.allowed_claim_bindings):
            raise VisualFeedbackContractError("runtime claim bindings contain duplicates")
        if not _FORBIDDEN.issubset({item.lower() for item in self.forbidden_identifier_tokens}):
            raise VisualFeedbackContractError("runtime forbidden targets are incomplete")
        require_sha256(self.policy_sha256, "policy_sha256")
        if self.policy_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("runtime claim policy digest differs")

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "policy_revision": self.policy_revision,
            "allowed_cue_ids": list(self.allowed_cue_ids),
            "allowed_renderer_keys": list(self.allowed_renderer_keys),
            "allowed_claim_bindings": [item.to_json_object() for item in self.allowed_claim_bindings],
            "forbidden_identifier_tokens": list(self.forbidden_identifier_tokens),
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "policy_sha256": self.policy_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> RuntimeVisualClaimPolicy:
        fields = {"schema_version", "policy_revision", "allowed_cue_ids", "allowed_renderer_keys", "allowed_claim_bindings", "forbidden_identifier_tokens", "policy_sha256"}
        doc = _digest_fields(value, name="claim_policy", fields=fields)
        bindings = doc["allowed_claim_bindings"]
        if not isinstance(bindings, list):
            raise VisualFeedbackContractError("runtime claim bindings must be an array")
        return cls(
            schema_version=require_non_empty_string(doc["schema_version"], "claim_policy.schema_version"),
            policy_revision=require_safe_identifier(doc["policy_revision"], "claim_policy.policy_revision"),
            allowed_cue_ids=_identifiers(doc["allowed_cue_ids"], "claim_policy.allowed_cue_ids"),
            allowed_renderer_keys=_identifiers(doc["allowed_renderer_keys"], "claim_policy.allowed_renderer_keys"),
            allowed_claim_bindings=tuple(RuntimeClaimBinding.from_json_object(item) for item in bindings),
            forbidden_identifier_tokens=_identifiers(doc["forbidden_identifier_tokens"], "claim_policy.forbidden_identifier_tokens"),
            policy_sha256=require_sha256(doc["policy_sha256"], "claim_policy.policy_sha256"),
        )


@dataclass(frozen=True, slots=True)
class P6CalibrationReceipt:
    revision: str
    pipeline_identity_sha256: str
    calibration_artifact_sha256: str
    calibration_case_count: int
    status: str
    receipt_sha256: str
    schema_version: str = P6_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != P6_SCHEMA or self.status != "calibrated":
            raise VisualFeedbackContractError("P6 calibration contract differs")
        require_safe_identifier(self.revision, "p6.revision")
        require_sha256(self.pipeline_identity_sha256, "p6.pipeline_identity_sha256")
        require_sha256(self.calibration_artifact_sha256, "p6.calibration_artifact_sha256")
        require_integer(self.calibration_case_count, "p6.calibration_case_count", minimum=1)
        require_sha256(self.receipt_sha256, "p6.receipt_sha256")
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("P6 calibration digest differs")

    @classmethod
    def issue(cls, *, revision: str, pipeline_identity: VisualRuntimePipelineIdentity,
              calibration_artifact: VisualCalibrationArtifact, calibration_case_count: int) -> P6CalibrationReceipt:
        unsigned = {"schema_version": P6_SCHEMA, "revision": revision,
                    "pipeline_identity_sha256": pipeline_identity.identity_sha256,
                    "calibration_artifact_sha256": calibration_artifact.artifact_sha256,
                    "calibration_case_count": calibration_case_count, "status": "calibrated"}
        return cls(**unsigned, receipt_sha256=sha256_document(unsigned))

    def unsigned_json_object(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "revision": self.revision,
                "pipeline_identity_sha256": self.pipeline_identity_sha256,
                "calibration_artifact_sha256": self.calibration_artifact_sha256,
                "calibration_case_count": self.calibration_case_count, "status": self.status}

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> P6CalibrationReceipt:
        fields = {"schema_version", "revision", "pipeline_identity_sha256", "calibration_artifact_sha256", "calibration_case_count", "status", "receipt_sha256"}
        doc = _digest_fields(value, name="p6_receipt", fields=fields)
        return cls(
            schema_version=require_non_empty_string(doc["schema_version"], "p6.schema_version"),
            revision=require_safe_identifier(doc["revision"], "p6.revision"),
            pipeline_identity_sha256=require_sha256(doc["pipeline_identity_sha256"], "p6.pipeline_identity_sha256"),
            calibration_artifact_sha256=require_sha256(doc["calibration_artifact_sha256"], "p6.calibration_artifact_sha256"),
            calibration_case_count=require_integer(doc["calibration_case_count"], "p6.calibration_case_count", minimum=1),
            status=require_safe_identifier(doc["status"], "p6.status"),
            receipt_sha256=require_sha256(doc["receipt_sha256"], "p6.receipt_sha256"),
        )


@dataclass(frozen=True, slots=True)
class P8EvaluationReceipt:
    revision: str
    p6_receipt_sha256: str
    claim_catalog_sha256: str
    claim_policy_sha256: str
    evaluation_case_count: int
    violation_count: int
    status: str
    receipt_sha256: str
    schema_version: str = P8_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != P8_SCHEMA or self.status != "evaluated":
            raise VisualFeedbackContractError("P8 evaluation contract differs")
        require_safe_identifier(self.revision, "p8.revision")
        for field in ("p6_receipt_sha256", "claim_catalog_sha256", "claim_policy_sha256", "receipt_sha256"):
            require_sha256(getattr(self, field), f"p8.{field}")
        require_integer(self.evaluation_case_count, "p8.evaluation_case_count", minimum=1)
        if require_integer(self.violation_count, "p8.violation_count", minimum=0) != 0:
            raise VisualFeedbackContractError("P8 evaluation has violations")
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("P8 evaluation digest differs")

    @classmethod
    def issue(cls, *, revision: str, p6_receipt: P6CalibrationReceipt,
              claim_catalog: VisualClaimCatalog, claim_policy: RuntimeVisualClaimPolicy,
              evaluation_case_count: int, violation_count: int) -> P8EvaluationReceipt:
        unsigned = {"schema_version": P8_SCHEMA, "revision": revision,
                    "p6_receipt_sha256": p6_receipt.receipt_sha256,
                    "claim_catalog_sha256": claim_catalog.catalog_sha256,
                    "claim_policy_sha256": claim_policy.policy_sha256,
                    "evaluation_case_count": evaluation_case_count,
                    "violation_count": violation_count, "status": "evaluated"}
        return cls(**unsigned, receipt_sha256=sha256_document(unsigned))

    def unsigned_json_object(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "revision": self.revision,
                "p6_receipt_sha256": self.p6_receipt_sha256,
                "claim_catalog_sha256": self.claim_catalog_sha256,
                "claim_policy_sha256": self.claim_policy_sha256,
                "evaluation_case_count": self.evaluation_case_count,
                "violation_count": self.violation_count, "status": self.status}

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> P8EvaluationReceipt:
        fields = {"schema_version", "revision", "p6_receipt_sha256", "claim_catalog_sha256", "claim_policy_sha256", "evaluation_case_count", "violation_count", "status", "receipt_sha256"}
        doc = _digest_fields(value, name="p8_receipt", fields=fields)
        return cls(
            schema_version=require_non_empty_string(doc["schema_version"], "p8.schema_version"),
            revision=require_safe_identifier(doc["revision"], "p8.revision"),
            p6_receipt_sha256=require_sha256(doc["p6_receipt_sha256"], "p8.p6_receipt_sha256"),
            claim_catalog_sha256=require_sha256(doc["claim_catalog_sha256"], "p8.claim_catalog_sha256"),
            claim_policy_sha256=require_sha256(doc["claim_policy_sha256"], "p8.claim_policy_sha256"),
            evaluation_case_count=require_integer(doc["evaluation_case_count"], "p8.evaluation_case_count", minimum=1),
            violation_count=require_integer(doc["violation_count"], "p8.violation_count", minimum=0),
            status=require_safe_identifier(doc["status"], "p8.status"),
            receipt_sha256=require_sha256(doc["receipt_sha256"], "p8.receipt_sha256"),
        )


@dataclass(frozen=True, slots=True)
class VisualRuntimeBundle:
    bundle_id: str
    revision: str
    pipeline_identity: VisualRuntimePipelineIdentity
    p6_receipt: P6CalibrationReceipt
    p8_receipt: P8EvaluationReceipt
    calibration_artifact: VisualCalibrationArtifact
    claim_catalog: VisualClaimCatalog
    claim_policy: RuntimeVisualClaimPolicy
    bundle_sha256: str
    schema_version: str = BUNDLE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != BUNDLE_SCHEMA:
            raise VisualFeedbackContractError("runtime bundle schema differs")
        require_safe_identifier(self.bundle_id, "runtime_bundle.bundle_id")
        require_safe_identifier(self.revision, "runtime_bundle.revision")
        identity = self.pipeline_identity
        if self.calibration_artifact.scope is not CalibrationScope.CALIBRATED or self.claim_catalog.scope is not CalibrationScope.CALIBRATED:
            raise VisualFeedbackContractError("runtime artifacts are not calibrated")
        if (self.p6_receipt.pipeline_identity_sha256 != identity.identity_sha256
                or self.p6_receipt.calibration_artifact_sha256 != self.calibration_artifact.artifact_sha256
                or self.p8_receipt.p6_receipt_sha256 != self.p6_receipt.receipt_sha256
                or self.p8_receipt.claim_catalog_sha256 != self.claim_catalog.catalog_sha256
                or self.p8_receipt.claim_policy_sha256 != self.claim_policy.policy_sha256
                or self.calibration_artifact.capture_contract_id != identity.visual_observation_contract_id
                or self.calibration_artifact.population_id_sha256 != identity.population_id_sha256):
            raise VisualFeedbackContractError("runtime bundle evidence binding differs")
        catalog = {(x.approved_claim_id, x.action_id, x.renderer_key, x.cue_id) for x in self.claim_catalog.claims}
        policy = {(x.approved_claim_id, x.action_id, x.renderer_key, x.cue_id) for x in self.claim_policy.allowed_claim_bindings}
        if catalog != policy:
            raise VisualFeedbackContractError("runtime claim bindings differ")
        require_sha256(self.bundle_sha256, "runtime_bundle.bundle_sha256")
        if self.bundle_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("runtime bundle digest differs")

    @classmethod
    def issue(cls, *, bundle_id: str, revision: str,
              pipeline_identity: VisualRuntimePipelineIdentity,
              p6_receipt: P6CalibrationReceipt, p8_receipt: P8EvaluationReceipt,
              calibration_artifact: VisualCalibrationArtifact,
              claim_catalog: VisualClaimCatalog,
              claim_policy: RuntimeVisualClaimPolicy) -> VisualRuntimeBundle:
        values = locals() | {"schema_version": BUNDLE_SCHEMA}
        values.pop("cls")
        unsigned = {key: (item.to_json_object() if hasattr(item, "to_json_object") else item) for key, item in values.items()}
        return cls(bundle_id=bundle_id, revision=revision, pipeline_identity=pipeline_identity,
                   p6_receipt=p6_receipt, p8_receipt=p8_receipt,
                   calibration_artifact=calibration_artifact, claim_catalog=claim_catalog,
                   claim_policy=claim_policy, bundle_sha256=sha256_document(unsigned))

    def unsigned_json_object(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "bundle_id": self.bundle_id,
                "revision": self.revision, "pipeline_identity": self.pipeline_identity.to_json_object(),
                "p6_receipt": self.p6_receipt.to_json_object(), "p8_receipt": self.p8_receipt.to_json_object(),
                "calibration_artifact": self.calibration_artifact.to_json_object(),
                "claim_catalog": self.claim_catalog.to_json_object(), "claim_policy": self.claim_policy.to_json_object()}

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "bundle_sha256": self.bundle_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> VisualRuntimeBundle:
        fields = {"schema_version", "bundle_id", "revision", "pipeline_identity", "p6_receipt", "p8_receipt", "calibration_artifact", "claim_catalog", "claim_policy", "bundle_sha256"}
        doc = _digest_fields(value, name="runtime_bundle", fields=fields)
        return cls(
            schema_version=require_non_empty_string(doc["schema_version"], "runtime_bundle.schema_version"),
            bundle_id=require_safe_identifier(doc["bundle_id"], "runtime_bundle.bundle_id"),
            revision=require_safe_identifier(doc["revision"], "runtime_bundle.revision"),
            pipeline_identity=VisualRuntimePipelineIdentity.from_json_object(doc["pipeline_identity"]),
            p6_receipt=P6CalibrationReceipt.from_json_object(doc["p6_receipt"]),
            p8_receipt=P8EvaluationReceipt.from_json_object(doc["p8_receipt"]),
            calibration_artifact=VisualCalibrationArtifact.from_json_object(doc["calibration_artifact"]),
            claim_catalog=VisualClaimCatalog.from_json_object(doc["claim_catalog"]),
            claim_policy=RuntimeVisualClaimPolicy.from_json_object(doc["claim_policy"]),
            bundle_sha256=require_sha256(doc["bundle_sha256"], "runtime_bundle.bundle_sha256"),
        )


@dataclass(frozen=True, slots=True)
class AttemptVisualReliabilityReceipt:
    attempt_id_sha256: str
    runtime_bundle_sha256: str
    binding_receipt_sha256: str
    detection_receipt_sha256: str
    phone_anchor_sha256: str
    video_qc_receipt_sha256: str
    av_sync_receipt_sha256: str
    phone_window_receipt_sha256: str
    receipt_sha256: str
    schema_version: str = ATTEMPT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != ATTEMPT_SCHEMA:
            raise VisualFeedbackContractError("attempt reliability schema differs")
        for field in ("attempt_id_sha256", "runtime_bundle_sha256", "binding_receipt_sha256", "detection_receipt_sha256", "phone_anchor_sha256", "video_qc_receipt_sha256", "av_sync_receipt_sha256", "phone_window_receipt_sha256", "receipt_sha256"):
            require_sha256(getattr(self, field), f"attempt_reliability.{field}")
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("attempt reliability digest differs")

    @classmethod
    def issue(cls, **values: str) -> AttemptVisualReliabilityReceipt:
        unsigned = {"schema_version": ATTEMPT_SCHEMA, **values}
        return cls(**unsigned, receipt_sha256=sha256_document(unsigned))

    def unsigned_json_object(self) -> dict[str, object]:
        return {field: getattr(self, field) for field in (
            "schema_version", "attempt_id_sha256", "runtime_bundle_sha256",
            "binding_receipt_sha256", "detection_receipt_sha256", "phone_anchor_sha256",
            "video_qc_receipt_sha256", "av_sync_receipt_sha256", "phone_window_receipt_sha256"
        )}

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> AttemptVisualReliabilityReceipt:
        fields = {"schema_version", "attempt_id_sha256", "runtime_bundle_sha256", "binding_receipt_sha256", "detection_receipt_sha256", "phone_anchor_sha256", "video_qc_receipt_sha256", "av_sync_receipt_sha256", "phone_window_receipt_sha256", "receipt_sha256"}
        doc = _digest_fields(value, name="attempt_reliability", fields=fields)
        return cls(**{field: (require_non_empty_string(doc[field], field) if field == "schema_version" else require_sha256(doc[field], f"attempt_reliability.{field}")) for field in fields})


def load_visual_runtime_bundle(value: object) -> VisualRuntimeBundle:
    return VisualRuntimeBundle.from_json_object(value)


__all__ = ["AttemptVisualReliabilityReceipt", "P6CalibrationReceipt",
           "P8EvaluationReceipt", "RuntimeClaimBinding", "RuntimeVisualClaimPolicy",
           "VisualRuntimeBundle", "VisualRuntimePipelineIdentity",
           "load_visual_runtime_bundle"]
