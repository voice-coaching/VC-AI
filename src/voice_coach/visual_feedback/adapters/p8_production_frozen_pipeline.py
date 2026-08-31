"""Concrete, pinned P8 pipeline boundary.

Unlike :class:`P8FrozenLockedPipeline`, this class is not a caller supplied
port.  It can only be created after the exact artifacts, model bytes and
installed adapter sources have been checked against a read-zero seal.  Media
measurement remains owned by the concrete production media engine; callers
cannot substitute a hash-claiming test double at this boundary.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Final

from ...pronunciation_attempts.adapters.calibrated_runtime_decisions import (
    CalibratedAvSyncDecisionAdapter,
    CalibratedVideoQcDecisionAdapter,
    RuntimeAvSyncDecision,
    RuntimeVideoQcDecision,
    decision_adapter_code_sha256,
)
from ...pronunciation_attempts.adapters.lip_audio_av_sync import (
    LipAudioAvSyncPolicy,
    av_sync_measurement_code_sha256,
)
from ...pronunciation_attempts.adapters.video_qc import (
    VideoQcPolicy,
    video_qc_measurement_code_sha256,
)
from ...pronunciation_attempts.phone_window import phone_window_code_sha256
from ..models.artifacts import CalibrationScope, VisualCalibrationArtifact, VisualClaimCatalog
from ..models.integrity import sha256_document
from ..models.p8_locked_evaluation import P8ReadZeroSeal
from ..models.runtime_eligibility import AutomatedP6PromotionReceipt, RuntimeVisualClaimPolicy
from ..models.runtime_policy_artifact import P8RuntimePolicyArtifact, RuntimeDecisionPolicyEvidence
from .aihub538_locked_dataset import (
    P8ClaimPolicyReplayAggregate,
    P8LockedItemEvaluation,
    P8LockedItemHandle,
    p8_frozen_pipeline_pins_sha256,
)
from .p8_locked_media_reader import (
    P8LockedMediaMeasurement,
    P8LockedMediaReader,
    issue_p8_locked_media_reader,
    require_issued_p8_locked_media_measurement,
)


class P8ProductionPipelineError(RuntimeError):
    """Path-free production assembly or evaluation failure."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class ProductionP8ArtifactDescriptors:
    """Caller-owned read-only descriptors; none refer to locked source data."""

    p6_receipt: int
    calibration_artifact: int
    claim_catalog: int
    claim_policy: int
    decision_policy_evidence: int
    video_qc_measurement_policy: int
    av_sync_measurement_policy: int
    video_qc_decision_policy: int
    av_sync_decision_policy: int
    phone_window_policy: int
    landmark_model: int

    def __post_init__(self) -> None:
        for value in self.__dataclass_fields__:
            descriptor = getattr(self, value)
            if isinstance(descriptor, bool) or not isinstance(descriptor, int) or descriptor < 3:
                raise P8ProductionPipelineError("p8_production_descriptor_invalid")


_ISSUER: Final[object] = object()
_ROOT = Path(__file__).resolve().parents[4]
_CODE_PATHS = {
    "decoder_code_sha256": _ROOT / "src/voice_coach/pronunciation_attempts/adapters/ffmpeg_av_container.py",
    "tracker_code_sha256": _ROOT / "src/voice_coach/pronunciation_attempts/adapters/mediapipe_landmark_worker.py",
    "tracker_adapter_code_sha256": _ROOT / "src/voice_coach/pronunciation_attempts/adapters/mediapipe_lip_landmarks.py",
    "normalizer_code_sha256": _ROOT / "src/voice_coach/visual_feedback/adapters/p6_mediapipe_geometry.py",
    "cue_extractor_code_sha256": _ROOT / "src/voice_coach/pronunciation_attempts/adapters/production_visual_sidecar.py",
}
_PRODUCTION_CODE_PATHS = {
    "evaluation_pipeline_code_sha256": Path(__file__).resolve(),
    "source_evaluator_code_sha256": (
        _ROOT
        / "src/voice_coach/visual_feedback/services/p8_aihub538_source_evaluator.py"
    ),
    "media_engine_code_sha256": (
        _ROOT
        / "src/voice_coach/visual_feedback/adapters/p8_locked_media_reader.py"
    ),
}
_MAX_INSTALLED_CODE_BYTES: Final[int] = 16 * 1024 * 1024
_FORBIDDEN_TARGETS = frozenset({"tongue", "glottis", "larynx", "vocal_fold", "internal_anatomy"})


class ProductionP8FrozenLockedPipeline:
    """Final concrete implementation accepted by the production facade."""

    __slots__ = (
        "_pins", "_catalog", "_claim_policy", "_qc_decider", "_sync_decider",
        "_catalog_sha256", "_claim_policy_sha256", "_forbidden_fixture_sha256",
        "_media_reader", "_expected_landmark_model_sha256",
        "_expected_video_qc_measurement_code_sha256",
        "_expected_av_sync_measurement_code_sha256", "_issued_marker", "_closed",
    )

    def __init__(
        self,
        issuer: object,
        *,
        pins: str,
        catalog: VisualClaimCatalog,
        claim_policy: RuntimeVisualClaimPolicy,
        qc_decider: CalibratedVideoQcDecisionAdapter,
        sync_decider: CalibratedAvSyncDecisionAdapter,
        media_reader: P8LockedMediaReader | None = None,
        expected_landmark_model_sha256: str | None = None,
        expected_video_qc_measurement_code_sha256: str | None = None,
        expected_av_sync_measurement_code_sha256: str | None = None,
    ) -> None:
        if issuer is not _ISSUER:
            raise P8ProductionPipelineError("p8_production_pipeline_direct_construction_forbidden")
        self._pins = pins
        self._issued_marker = _ISSUER
        self._catalog = catalog
        self._claim_policy = claim_policy
        self._qc_decider = qc_decider
        self._sync_decider = sync_decider
        if type(media_reader) is not P8LockedMediaReader:
            raise P8ProductionPipelineError(
                "p8_production_concrete_media_reader_required"
            )
        expected_digests = (
            expected_landmark_model_sha256,
            expected_video_qc_measurement_code_sha256,
            expected_av_sync_measurement_code_sha256,
        )
        if any(
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in expected_digests
        ):
            raise P8ProductionPipelineError(
                "p8_production_measurement_lineage_pin_invalid"
            )
        self._media_reader = media_reader
        self._expected_landmark_model_sha256 = expected_landmark_model_sha256
        self._expected_video_qc_measurement_code_sha256 = (
            expected_video_qc_measurement_code_sha256
        )
        self._expected_av_sync_measurement_code_sha256 = (
            expected_av_sync_measurement_code_sha256
        )
        self._closed = False
        self._catalog_sha256 = catalog.catalog_sha256
        self._claim_policy_sha256 = claim_policy.policy_sha256
        self._forbidden_fixture_sha256 = sha256_document(
            {"schema_version": "voice_coach.p8_forbidden_claim_fixtures.v1", "targets": sorted(_FORBIDDEN_TARGETS)}
        )

    @property
    def pipeline_pins_sha256(self) -> str:
        return self._pins

    def evaluate(self, item: P8LockedItemHandle) -> P8LockedItemEvaluation:
        """Evaluate one sealed item through the exact issued media reader."""
        if not isinstance(item, P8LockedItemHandle):
            raise P8ProductionPipelineError("p8_production_item_invalid")
        if self._closed:
            raise P8ProductionPipelineError("p8_production_pipeline_closed")
        try:
            measurement = require_issued_p8_locked_media_measurement(
                self._media_reader.measure(item),
                expected_item_binding_sha256=item.item_binding_sha256,
            )
        except Exception as error:
            reason = getattr(error, "reason_code", None)
            if isinstance(reason, str):
                raise P8ProductionPipelineError(reason) from None
            raise P8ProductionPipelineError(
                "p8_production_media_evaluation_failed"
            ) from None
        return self._evaluate_issued_measurement(
            item=item,
            measurement=measurement,
        )

    def _evaluate_issued_measurement(
        self, *, item: P8LockedItemHandle, measurement: P8LockedMediaMeasurement
    ) -> P8LockedItemEvaluation:
        """Issue decisions only from reader-issued candidate lineage."""
        try:
            require_issued_p8_locked_media_measurement(
                measurement,
                expected_item_binding_sha256=item.item_binding_sha256,
            )
        except Exception:
            raise P8ProductionPipelineError(
                "p8_production_item_binding_mismatch"
            ) from None
        qc_candidate = measurement.video_qc
        sync_candidate = measurement.av_sync
        binding = measurement.binding
        if (
            qc_candidate.container_sha256 != binding.video_source_sha256
            or sync_candidate.video_source_sha256
            != binding.video_source_sha256
            or sync_candidate.audio_source_sha256
            != binding.audio_source_sha256
            or sync_candidate.audio_derivation_receipt_sha256
            != binding.audio_derivation_receipt_sha256
            or qc_candidate.asset_handle_id != sync_candidate.asset_handle_id
            or qc_candidate.landmark_evidence_sha256
            != sync_candidate.landmark_evidence_sha256
            or qc_candidate.landmark_model_sha256
            != self._expected_landmark_model_sha256
            or sync_candidate.landmark_model_sha256
            != self._expected_landmark_model_sha256
            or qc_candidate.measurement_adapter_code_sha256
            != self._expected_video_qc_measurement_code_sha256
            or sync_candidate.measurement_adapter_code_sha256
            != self._expected_av_sync_measurement_code_sha256
        ):
            raise P8ProductionPipelineError(
                "p8_production_measurement_lineage_invalid"
            )
        try:
            qc: RuntimeVideoQcDecision = self._qc_decider.decide(
                candidate=measurement.video_qc, binding_receipt=measurement.binding
            )
            sync: RuntimeAvSyncDecision = self._sync_decider.decide(
                candidate=measurement.av_sync, binding_receipt=measurement.binding
            )
        except (TypeError, ValueError) as error:
            raise P8ProductionPipelineError("p8_production_decision_lineage_invalid") from error
        absolute_offset = (
            abs(float(sync.observed_offset_ms)) if sync.observed_offset_ms is not None else None
        )
        return P8LockedItemEvaluation(
            item_binding_sha256=item.item_binding_sha256,
            pipeline_pins_sha256=self._pins,
            decode_succeeded=True,
            qc_decision_lineage_valid=True,
            supported_clip=qc.status == "passed",
            single_face_coverage=float(qc.observed_metrics.single_face_coverage),
            av_anchor_available=bool(sync.observed_anchors),
            ready=sync.status == "synchronized",
            sync_decision_lineage_valid=True,
            absolute_sync_offset_ms=absolute_offset,
            evaluation_succeeded=True,
        )

    @contextmanager
    def _bind_restricted_media_workspace(
        self,
        *,
        workspace_descriptor: int,
        access_transaction_sha256: str,
        issued_at_utc: str,
    ) -> Iterator[None]:
        """Bind only the source evaluator's covered restricted work tree."""

        if self._closed:
            raise P8ProductionPipelineError("p8_production_pipeline_closed")
        with self._media_reader.bind_restricted_workspace(
            workspace_descriptor=workspace_descriptor,
            access_transaction_sha256=access_transaction_sha256,
            issued_at_utc=issued_at_utc,
        ):
            yield

    def close(self) -> None:
        """Release the concrete model descriptor after the one-shot run."""

        if self._closed:
            return
        self._media_reader.close()
        self._closed = True

    def replay_claim_policy(self, *, evaluated_case_count: int) -> P8ClaimPolicyReplayAggregate:
        if isinstance(evaluated_case_count, bool) or evaluated_case_count <= 0:
            raise P8ProductionPipelineError("p8_production_replay_case_count_invalid")
        claims = self._catalog.claims
        allowed = set(self._claim_policy.allowed_claim_bindings)
        violations = 0
        for claim in claims:
            # P8 is intentionally lip-only and currently approves only ㅓ.
            if set(claim.expected_phones) != {"ㅓ"}:
                violations += 1
            identifiers = (claim.action_id, claim.approved_claim_id, claim.renderer_key, claim.cue_id)
            if any(token in identifier.lower() for identifier in identifiers for token in _FORBIDDEN_TARGETS):
                violations += 1
            if not any(
                binding.cue_id == claim.cue_id
                and binding.renderer_key == claim.renderer_key
                and binding.approved_claim_id == claim.approved_claim_id
                and binding.action_id == claim.action_id
                for binding in allowed
            ):
                violations += 1
        pair_count = evaluated_case_count * len(claims)
        return P8ClaimPolicyReplayAggregate.issue(
            pipeline_pins_sha256=self._pins,
            claim_catalog_sha256=self._catalog_sha256,
            claim_policy_sha256=self._claim_policy_sha256,
            forbidden_fixture_catalog_sha256=self._forbidden_fixture_sha256,
            evaluated_case_count=evaluated_case_count,
            catalog_claim_count=len(claims),
            catalog_claim_replayed_count=len(claims),
            expected_case_claim_pair_count=pair_count,
            replayed_case_claim_pair_count=pair_count,
            forbidden_fixture_count=len(_FORBIDDEN_TARGETS),
            forbidden_fixture_replayed_count=len(_FORBIDDEN_TARGETS),
            claim_policy_violation_count=violations,
            forbidden_claim_fixture_violation_count=0,
        )


def load_production_p8_frozen_pipeline(
    *, seal: P8ReadZeroSeal, descriptors: ProductionP8ArtifactDescriptors
) -> ProductionP8FrozenLockedPipeline:
    """Strict-load every pin before any locked item can be consumed."""
    if type(seal) is not P8ReadZeroSeal or type(descriptors) is not ProductionP8ArtifactDescriptors:
        raise P8ProductionPipelineError("p8_production_assembly_type_invalid")
    _validate_installed_production_code_pins(seal)
    p6 = AutomatedP6PromotionReceipt.from_json_object(_read_json(descriptors.p6_receipt))
    calibration = VisualCalibrationArtifact.from_json_object(_read_json(descriptors.calibration_artifact))
    catalog = VisualClaimCatalog.from_json_object(_read_json(descriptors.claim_catalog))
    claim_policy = RuntimeVisualClaimPolicy.from_json_object(_read_json(descriptors.claim_policy))
    decision_evidence = RuntimeDecisionPolicyEvidence.from_json_object(_read_json(descriptors.decision_policy_evidence))
    policies = tuple(
        P8RuntimePolicyArtifact.from_json_object(_read_json(fd))
        for fd in (
            descriptors.video_qc_measurement_policy, descriptors.av_sync_measurement_policy,
            descriptors.video_qc_decision_policy, descriptors.av_sync_decision_policy,
            descriptors.phone_window_policy,
        )
    )
    by_kind = {artifact.policy_kind: artifact for artifact in policies}
    if set(by_kind) != {"video_qc", "av_sync", "video_qc_decision", "av_sync_decision", "phone_window"}:
        raise P8ProductionPipelineError("p8_production_policy_set_invalid")
    actual = {
        "p6_evidence_receipt_sha256": p6.receipt_sha256,
        "calibration_artifact_sha256": calibration.artifact_sha256,
        "claim_catalog_sha256": catalog.catalog_sha256,
        "claim_policy_sha256": claim_policy.policy_sha256,
        "landmark_model_sha256": _sha256_descriptor(descriptors.landmark_model),
        **{name: _sha256_path(path) for name, path in _CODE_PATHS.items()},
        "video_qc_measurement_code_sha256": video_qc_measurement_code_sha256(),
        "video_qc_measurement_policy_sha256": by_kind["video_qc"].policy_sha256,
        "video_qc_decision_code_sha256": decision_adapter_code_sha256(),
        "video_qc_decision_policy_revision": by_kind["video_qc_decision"].policy_revision,
        "video_qc_decision_policy_sha256": by_kind["video_qc_decision"].policy_sha256,
        "av_sync_measurement_code_sha256": av_sync_measurement_code_sha256(),
        "av_sync_measurement_policy_sha256": by_kind["av_sync"].policy_sha256,
        "av_sync_decision_code_sha256": decision_adapter_code_sha256(),
        "av_sync_decision_policy_revision": by_kind["av_sync_decision"].policy_revision,
        "av_sync_decision_policy_sha256": by_kind["av_sync_decision"].policy_sha256,
        "phone_window_code_sha256": phone_window_code_sha256(),
        "phone_window_policy_revision": by_kind["phone_window"].policy_revision,
        "phone_window_policy_sha256": by_kind["phone_window"].policy_sha256,
    }
    if any(getattr(seal, name) != digest for name, digest in actual.items()):
        raise P8ProductionPipelineError("p8_production_pin_mismatch")
    if calibration.scope is not CalibrationScope.CALIBRATED or catalog.scope is not CalibrationScope.CALIBRATED:
        raise P8ProductionPipelineError("p8_production_artifact_scope_invalid")
    if any(set(claim.expected_phones) != {"ㅓ"} for claim in catalog.claims):
        raise P8ProductionPipelineError("p8_production_catalog_phone_scope_invalid")
    expected_bindings = {
        (claim.approved_claim_id, claim.action_id, claim.renderer_key, claim.cue_id)
        for claim in catalog.claims
    }
    actual_bindings = {
        (binding.approved_claim_id, binding.action_id, binding.renderer_key, binding.cue_id)
        for binding in claim_policy.allowed_claim_bindings
    }
    if actual_bindings != expected_bindings:
        raise P8ProductionPipelineError("p8_production_claim_policy_binding_invalid")
    if p6.catalog_sha256 != catalog.catalog_sha256 or p6.policy_sha256 != claim_policy.policy_sha256:
        raise P8ProductionPipelineError("p8_production_p6_binding_invalid")
    if (
        decision_evidence.video_qc_policy_artifact_sha256 != by_kind["video_qc_decision"].artifact_sha256
        or decision_evidence.av_sync_policy_artifact_sha256 != by_kind["av_sync_decision"].artifact_sha256
    ):
        raise P8ProductionPipelineError("p8_production_decision_evidence_binding_invalid")
    qc_policy = by_kind["video_qc_decision"].policy_document.to_runtime_policy()
    sync_policy = by_kind["av_sync_decision"].policy_document.to_runtime_policy()
    video_qc_document = by_kind["video_qc"].policy_document.to_json_object()
    av_sync_document = by_kind["av_sync"].policy_document.to_json_object()
    video_qc_measurement_policy = VideoQcPolicy(
        **{
            name: value
            for name, value in video_qc_document.items()
            if name != "schema_version"
        }
    )
    av_sync_measurement_policy = LipAudioAvSyncPolicy(
        **{
            name: value
            for name, value in av_sync_document.items()
            if name != "schema_version"
        }
    )
    if (
        video_qc_measurement_policy.receipt_sha256
        != by_kind["video_qc"].policy_sha256
        or av_sync_measurement_policy.policy_sha256
        != by_kind["av_sync"].policy_sha256
    ):
        raise P8ProductionPipelineError(
            "p8_production_measurement_policy_conversion_invalid"
        )
    media_reader = issue_p8_locked_media_reader(
        landmark_model_descriptor=descriptors.landmark_model,
        landmark_model_sha256=seal.landmark_model_sha256,
        video_qc_policy=video_qc_measurement_policy,
        av_sync_policy=av_sync_measurement_policy,
    )
    return ProductionP8FrozenLockedPipeline(
        _ISSUER, pins=p8_frozen_pipeline_pins_sha256(seal), catalog=catalog,
        claim_policy=claim_policy,
        qc_decider=CalibratedVideoQcDecisionAdapter(policy=qc_policy),
        sync_decider=CalibratedAvSyncDecisionAdapter(policy=sync_policy),
        media_reader=media_reader,
        expected_landmark_model_sha256=seal.landmark_model_sha256,
        expected_video_qc_measurement_code_sha256=(
            seal.video_qc_measurement_code_sha256
        ),
        expected_av_sync_measurement_code_sha256=(
            seal.av_sync_measurement_code_sha256
        ),
    )


def require_exact_production_p8_pipeline(value: object) -> ProductionP8FrozenLockedPipeline:
    """Reject subclasses, structural Protocol matches, and self-claimed hashes."""
    if (
        type(value) is not ProductionP8FrozenLockedPipeline
        or getattr(value, "_issued_marker", None) is not _ISSUER
    ):
        raise P8ProductionPipelineError("p8_production_concrete_pipeline_required")
    return value


@contextmanager
def bind_production_p8_media_workspace(
    value: object,
    *,
    workspace_descriptor: int,
    access_transaction_sha256: str,
    issued_at_utc: str,
) -> Iterator[None]:
    """Bind the exact production reader; fixture Protocols remain untouched."""

    if type(value) is ProductionP8FrozenLockedPipeline:
        pipeline = require_exact_production_p8_pipeline(value)
        with pipeline._bind_restricted_media_workspace(
            workspace_descriptor=workspace_descriptor,
            access_transaction_sha256=access_transaction_sha256,
            issued_at_utc=issued_at_utc,
        ):
            yield
        return
    # The private fixture runner still exercises archive/completeness contracts
    # with its typed deterministic pipeline.  The public one-shot runner rejects
    # that Protocol before this helper can be reached.
    yield


def _validate_installed_production_code_pins(seal: P8ReadZeroSeal) -> None:
    """Bind the v4 seal to the exact installed production composition files."""

    for field_name, path in _PRODUCTION_CODE_PATHS.items():
        if getattr(seal, field_name) != _sha256_path(path):
            raise P8ProductionPipelineError(
                f"p8_production_{field_name.removesuffix('_sha256')}_pin_mismatch"
            )


def _read_json(descriptor: int) -> object:
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or info.st_size > 8 * 1024 * 1024:
            raise P8ProductionPipelineError("p8_production_artifact_descriptor_invalid")
        before = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        raw = os.pread(descriptor, info.st_size, 0)
        if len(raw) != info.st_size:
            raise P8ProductionPipelineError("p8_production_artifact_read_incomplete")
        after_info = os.fstat(descriptor)
        after = (after_info.st_dev, after_info.st_ino, after_info.st_size, after_info.st_mtime_ns)
        if after != before:
            raise P8ProductionPipelineError("p8_production_artifact_changed")
        return json.loads(raw, object_pairs_hook=_unique_json_object)
    except P8ProductionPipelineError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise P8ProductionPipelineError("p8_production_artifact_invalid") from error


def _sha256_descriptor(descriptor: int) -> str:
    before_info = os.fstat(descriptor)
    if not stat.S_ISREG(before_info.st_mode) or before_info.st_size <= 0:
        raise P8ProductionPipelineError("p8_production_model_descriptor_invalid")
    before = (before_info.st_dev, before_info.st_ino, before_info.st_size, before_info.st_mtime_ns)
    digest = hashlib.sha256()
    offset = 0
    while True:
        block = os.pread(descriptor, 1024 * 1024, offset)
        if not block:
            break
        digest.update(block)
        offset += len(block)
    after_info = os.fstat(descriptor)
    after = (after_info.st_dev, after_info.st_ino, after_info.st_size, after_info.st_mtime_ns)
    if after != before or offset != before_info.st_size:
        raise P8ProductionPipelineError("p8_production_model_changed")
    return digest.hexdigest()


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise P8ProductionPipelineError("p8_production_artifact_duplicate_key")
        result[key] = value
    return result


def _sha256_path(path: Path) -> str:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise P8ProductionPipelineError("p8_production_installed_code_unavailable") from error
    try:
        before_info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before_info.st_mode)
            or before_info.st_size <= 0
            or before_info.st_size > _MAX_INSTALLED_CODE_BYTES
        ):
            raise P8ProductionPipelineError(
                "p8_production_installed_code_invalid"
            )
        before = (
            before_info.st_dev,
            before_info.st_ino,
            before_info.st_mode,
            before_info.st_size,
            before_info.st_mtime_ns,
            before_info.st_ctime_ns,
        )
        digest = hashlib.sha256()
        offset = 0
        while offset < before_info.st_size:
            block = os.pread(
                descriptor,
                min(1024 * 1024, before_info.st_size - offset),
                offset,
            )
            if not block:
                raise P8ProductionPipelineError(
                    "p8_production_installed_code_read_incomplete"
                )
            digest.update(block)
            offset += len(block)
        after_info = os.fstat(descriptor)
        after = (
            after_info.st_dev,
            after_info.st_ino,
            after_info.st_mode,
            after_info.st_size,
            after_info.st_mtime_ns,
            after_info.st_ctime_ns,
        )
        if after != before or offset != before_info.st_size:
            raise P8ProductionPipelineError(
                "p8_production_installed_code_changed"
            )
        return digest.hexdigest()
    except P8ProductionPipelineError:
        raise
    except OSError as error:
        raise P8ProductionPipelineError(
            "p8_production_installed_code_unavailable"
        ) from error
    finally:
        os.close(descriptor)


__all__ = [
    "P8ProductionPipelineError", "ProductionP8ArtifactDescriptors",
    "ProductionP8FrozenLockedPipeline", "bind_production_p8_media_workspace",
    "load_production_p8_frozen_pipeline", "require_exact_production_p8_pipeline",
]
