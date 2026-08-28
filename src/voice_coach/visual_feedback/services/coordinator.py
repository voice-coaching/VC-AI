"""Fail-closed orchestration for Seungun-grounded visual feedback."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import math

from ..errors import (
    SeungunEvidenceUnavailableError,
    VisualArtifactUnavailableError,
    VisualFeedbackContractError,
)
from ..models.artifacts import (
    VisualCalibrationArtifact,
    VisualClaim,
    VisualClaimCatalog,
)
from ..models.enums import (
    CalibrationScope,
    ExecutionScope,
    UpstreamReliabilityStatus,
    VisualCueState,
    VisualEvidenceRelation,
    VisualFeedbackStatus,
    VideoQualityStatus,
)
from ..models.evidence import (
    AdapterReceipt,
    AvSyncObservation,
    LipCueMeasurement,
    MediaDeletionReceipt,
    SeungunEvidenceSnapshot,
    UpstreamPhoneAnchor,
)
from ..models.integrity import sha256_document
from ..models.request import VisualFeedbackRequest
from ..models.supplement import (
    SupplementalVisualActionCandidate,
    VisualCueEvidence,
    VisualFeedbackSupplement,
)
from ..ports.evidence import SeungunPronunciationEvidencePort
from ..ports.visual import (
    AvSyncPort,
    ConsentRightsPort,
    LipTrackingPort,
    RestrictedVideoPort,
    VideoQualityPort,
    VisualCalibrationPort,
    VisualClaimCatalogPort,
)


@dataclass(frozen=True, slots=True)
class _Draft:
    status: VisualFeedbackStatus
    reason_codes: tuple[str, ...]
    binding_ref: str | None = None
    phone_anchor_ref: str | None = None
    cue_evidence: tuple[VisualCueEvidence, ...] = ()
    evidence_relation: VisualEvidenceRelation = VisualEvidenceRelation.NOT_COMPARABLE
    candidate: SupplementalVisualActionCandidate | None = None
    visual_receipts: tuple[AdapterReceipt, ...] = ()
    calibration_receipts: tuple[AdapterReceipt, ...] = ()


class VisualFeedbackCoordinator:
    """Create one privacy-safe supplement without changing upstream evidence."""

    def __init__(
        self,
        *,
        evidence_port: SeungunPronunciationEvidencePort,
        consent_port: ConsentRightsPort,
        video_port: RestrictedVideoPort,
        quality_port: VideoQualityPort,
        tracking_port: LipTrackingPort,
        sync_port: AvSyncPort,
        calibration_port: VisualCalibrationPort,
        claim_catalog_port: VisualClaimCatalogPort,
        execution_scope: ExecutionScope,
    ) -> None:
        self._evidence_port = evidence_port
        self._consent_port = consent_port
        self._video_port = video_port
        self._quality_port = quality_port
        self._tracking_port = tracking_port
        self._sync_port = sync_port
        self._calibration_port = calibration_port
        self._claim_catalog_port = claim_catalog_port
        self._execution_scope = execution_scope

    def create_visual_feedback(
        self, request: VisualFeedbackRequest
    ) -> VisualFeedbackSupplement:
        """Evaluate the optional sidecar and always attempt restricted-media cleanup."""

        try:
            draft = self._evaluate(request)
        except SeungunEvidenceUnavailableError:
            draft = _Draft(
                status=VisualFeedbackStatus.DEPENDENCY_UNAVAILABLE,
                reason_codes=("seungun_evidence_unavailable",),
            )
        except VisualArtifactUnavailableError:
            draft = _Draft(
                status=VisualFeedbackStatus.VISUAL_FAILED_CLOSED,
                reason_codes=("visual_artifact_unavailable",),
                binding_ref=request.binding_receipt.receipt_sha256,
            )
        except VisualFeedbackContractError:
            draft = _Draft(
                status=VisualFeedbackStatus.VISUAL_FAILED_CLOSED,
                reason_codes=("visual_contract_violation",),
            )
        except Exception:  # pragma: no cover - last-resort privacy boundary
            draft = _Draft(
                status=VisualFeedbackStatus.VISUAL_FAILED_CLOSED,
                reason_codes=("visual_backend_unexpected_failure",),
            )

        deletion_receipt = self._delete_restricted_media(request)
        if deletion_receipt.status == "failed":
            draft = replace(
                draft,
                status=VisualFeedbackStatus.VISUAL_FAILED_CLOSED,
                reason_codes=_unique((*draft.reason_codes, "media_deletion_failed")),
                candidate=None,
                evidence_relation=VisualEvidenceRelation.NOT_COMPARABLE,
            )

        return VisualFeedbackSupplement(
            status=draft.status,
            reason_codes=draft.reason_codes,
            upstream_analysis_ref=request.analysis_ref.reference_id,
            upstream_binding_ref=draft.binding_ref,
            upstream_phone_anchor_ref=draft.phone_anchor_ref,
            cue_evidence=draft.cue_evidence,
            evidence_relation=draft.evidence_relation,
            supplemental_action_candidate=draft.candidate,
            visual_backend_receipts=draft.visual_receipts,
            reference_calibration_receipts=draft.calibration_receipts,
            media_deletion_receipt=deletion_receipt,
            notices=(
                "visual_supplement_only",
                "upstream_evidence_unchanged",
                "no_raw_video_in_output",
            ),
        )

    def _evaluate(self, request: VisualFeedbackRequest) -> _Draft:
        snapshot = self._evidence_port.resolve(
            analysis_ref=request.analysis_ref,
            binding_receipt=request.binding_receipt,
            video_handle=request.video_handle,
        )
        _validate_snapshot_binding(snapshot=snapshot, request=request)
        base_receipts = (snapshot.adapter_receipt,)
        binding_ref = snapshot.binding_receipt_sha256
        anchor = snapshot.anchor(request.upstream_phone_expected_index)
        if anchor is None:
            raise SeungunEvidenceUnavailableError(
                "requested phone anchor is absent from immutable evidence"
            )
        if (
            request.binding_receipt.consent_receipt_sha256
            != request.consent_receipt.receipt_sha256
        ):
            return _Draft(
                status=VisualFeedbackStatus.VISUAL_FAILED_CLOSED,
                reason_codes=("bound_consent_receipt_mismatch",),
                binding_ref=binding_ref,
                phone_anchor_ref=anchor.anchor_ref,
                visual_receipts=base_receipts,
            )
        if not _binding_was_issued_during_consent(request):
            return _Draft(
                status=VisualFeedbackStatus.VISUAL_FAILED_CLOSED,
                reason_codes=("binding_outside_consent_interval",),
                binding_ref=binding_ref,
                phone_anchor_ref=anchor.anchor_ref,
                visual_receipts=base_receipts,
            )

        consent_ok, consent_reasons = self._consent_port.validate(
            receipt=request.consent_receipt,
            video_handle=request.video_handle,
        )
        if not consent_ok:
            return _Draft(
                status=VisualFeedbackStatus.VISUAL_FAILED_CLOSED,
                reason_codes=consent_reasons or ("visual_consent_not_valid",),
                binding_ref=binding_ref,
                phone_anchor_ref=anchor.anchor_ref,
                visual_receipts=base_receipts,
            )

        self._video_port.assert_available(request.video_handle)
        quality = self._quality_port.observe(video_handle=request.video_handle)
        quality_receipts = (*base_receipts, quality.receipt)
        if quality.status is VideoQualityStatus.RERECORD_REQUIRED:
            return _Draft(
                status=VisualFeedbackStatus.VISUAL_RERECORD_REQUIRED,
                reason_codes=quality.reason_codes or ("video_quality_retake_needed",),
                binding_ref=binding_ref,
                phone_anchor_ref=anchor.anchor_ref,
                visual_receipts=quality_receipts,
            )
        if quality.status is VideoQualityStatus.UNSUPPORTED:
            return _Draft(
                status=VisualFeedbackStatus.VISUAL_UNSUPPORTED,
                reason_codes=quality.reason_codes or ("video_scope_unsupported",),
                binding_ref=binding_ref,
                phone_anchor_ref=anchor.anchor_ref,
                visual_receipts=quality_receipts,
            )
        if quality.status is not VideoQualityStatus.PASSED:
            return _Draft(
                status=VisualFeedbackStatus.VISUAL_FAILED_CLOSED,
                reason_codes=quality.reason_codes or ("video_quality_failed_closed",),
                binding_ref=binding_ref,
                phone_anchor_ref=anchor.anchor_ref,
                visual_receipts=quality_receipts,
            )

        sync = self._sync_port.measure(
            video_handle=request.video_handle,
            audio_timebase=request.binding_receipt.audio_timebase,
            video_timebase=request.binding_receipt.video_timebase,
        )
        sync_receipts = (*quality_receipts, sync.receipt)
        if sync.state is not VisualCueState.OBSERVED:
            return _Draft(
                status=VisualFeedbackStatus.VISUAL_UNCERTAIN,
                reason_codes=sync.reason_codes or ("av_sync_not_observed",),
                binding_ref=binding_ref,
                phone_anchor_ref=anchor.anchor_ref,
                visual_receipts=sync_receipts,
            )

        video_span = _map_phone_span(anchor=anchor, sync=sync)
        if video_span is None:
            return _Draft(
                status=VisualFeedbackStatus.NO_SUPPORTED_LIP_CUE,
                reason_codes=("upstream_phone_span_not_comparable",),
                binding_ref=binding_ref,
                phone_anchor_ref=anchor.anchor_ref,
                visual_receipts=sync_receipts,
            )
        video_start_ms, video_end_ms = video_span
        video_duration = request.binding_receipt.video_timebase.duration_ms
        if video_start_ms < 0 or video_end_ms > video_duration:
            return _Draft(
                status=VisualFeedbackStatus.VISUAL_UNCERTAIN,
                reason_codes=("mapped_span_outside_video_timebase",),
                binding_ref=binding_ref,
                phone_anchor_ref=anchor.anchor_ref,
                visual_receipts=sync_receipts,
            )

        tracking = self._tracking_port.track(
            video_handle=request.video_handle,
            video_start_ms=video_start_ms,
            video_end_ms=video_end_ms,
        )
        visual_receipts = (*sync_receipts, tracking.receipt)
        if not tracking.cues:
            return _Draft(
                status=VisualFeedbackStatus.NO_SUPPORTED_LIP_CUE,
                reason_codes=("tracker_returned_no_supported_cues",),
                binding_ref=binding_ref,
                phone_anchor_ref=anchor.anchor_ref,
                visual_receipts=visual_receipts,
            )

        # Artifact lookup cannot turn an unavailable measurement backend into
        # an observation.  Stop before loading calibration/catalog data so the
        # real-media composition root can report the missing tracker exactly,
        # instead of masking it as an artifact failure.
        tracking_states = {cue.state for cue in tracking.cues}
        tracking_reasons = _unique(
            tuple(reason for cue in tracking.cues for reason in cue.reason_codes)
        )
        if VisualCueState.BACKEND_UNAVAILABLE in tracking_states:
            return _Draft(
                status=VisualFeedbackStatus.VISUAL_UNCERTAIN,
                reason_codes=tracking_reasons
                or ("lip_landmark_backend_unavailable",),
                binding_ref=binding_ref,
                phone_anchor_ref=anchor.anchor_ref,
                visual_receipts=visual_receipts,
            )
        if VisualCueState.OUT_OF_DISTRIBUTION in tracking_states:
            return _Draft(
                status=VisualFeedbackStatus.VISUAL_UNSUPPORTED,
                reason_codes=tracking_reasons
                or ("visual_cue_out_of_tracking_support",),
                binding_ref=binding_ref,
                phone_anchor_ref=anchor.anchor_ref,
                visual_receipts=visual_receipts,
            )
        if VisualCueState.OBSERVED not in tracking_states:
            if tracking_states == {VisualCueState.NOT_OBSERVABLE}:
                return _Draft(
                    status=VisualFeedbackStatus.NO_SUPPORTED_LIP_CUE,
                    reason_codes=tracking_reasons
                    or ("selected_phone_has_no_supported_lip_cue",),
                    binding_ref=binding_ref,
                    phone_anchor_ref=anchor.anchor_ref,
                    visual_receipts=visual_receipts,
                )
            return _Draft(
                status=VisualFeedbackStatus.VISUAL_UNCERTAIN,
                reason_codes=tracking_reasons
                or ("visual_cue_not_reliably_observed",),
                binding_ref=binding_ref,
                phone_anchor_ref=anchor.anchor_ref,
                visual_receipts=visual_receipts,
            )

        calibration = self._calibration_port.load(
            capture_contract_id=request.capture_contract_id
        )
        catalog = self._claim_catalog_port.load()
        calibration_receipts = _artifact_receipts(calibration, catalog)
        scope_failure = self._scope_failure(
            calibration=calibration,
            catalog=catalog,
            request=request,
        )
        if scope_failure is not None:
            status, reason = scope_failure
            return _Draft(
                status=status,
                reason_codes=(reason,),
                binding_ref=binding_ref,
                phone_anchor_ref=anchor.anchor_ref,
                visual_receipts=visual_receipts,
                calibration_receipts=calibration_receipts,
            )

        return self._calibrate_and_select(
            request=request,
            anchor=anchor,
            cues=tracking.cues,
            calibration=calibration,
            catalog=catalog,
            binding_ref=binding_ref,
            visual_receipts=visual_receipts,
            calibration_receipts=calibration_receipts,
        )

    def _scope_failure(
        self,
        *,
        calibration: VisualCalibrationArtifact,
        catalog: VisualClaimCatalog,
        request: VisualFeedbackRequest,
    ) -> tuple[VisualFeedbackStatus, str] | None:
        if calibration.capture_contract_id != request.capture_contract_id:
            raise VisualArtifactUnavailableError("calibration capture contract differs")
        if calibration.population_id_sha256 != request.population_id_sha256:
            return VisualFeedbackStatus.VISUAL_UNSUPPORTED, "population_not_calibrated"
        if calibration.scope is not catalog.scope:
            raise VisualArtifactUnavailableError("artifact scopes differ")
        if (
            self._execution_scope is ExecutionScope.USER_RUNTIME
            and calibration.scope is CalibrationScope.SYNTHETIC_CONTRACT_ONLY
        ):
            return (
                VisualFeedbackStatus.VISUAL_UNCERTAIN,
                "synthetic_artifact_not_user_eligible",
            )
        return None

    def _calibrate_and_select(
        self,
        *,
        request: VisualFeedbackRequest,
        anchor: UpstreamPhoneAnchor,
        cues: tuple[LipCueMeasurement, ...],
        calibration: VisualCalibrationArtifact,
        catalog: VisualClaimCatalog,
        binding_ref: str,
        visual_receipts: tuple[AdapterReceipt, ...],
        calibration_receipts: tuple[AdapterReceipt, ...],
    ) -> _Draft:
        evidence: list[VisualCueEvidence] = []
        actionable: tuple[VisualCueEvidence, VisualClaim] | None = None
        states: set[VisualCueState] = set()
        claim_missing_for_direction = False

        for cue in cues:
            calibrated = calibration.calibrate(
                cue=cue,
                expected_phone=anchor.expected_phone,
                context_id=request.context_id,
            )
            states.add(calibrated.state)
            public_evidence = _public_cue_evidence(
                cue=cue,
                state=calibrated.state,
                calibrated_deviation=calibrated.calibrated_deviation,
                artifact_sha256=calibrated.calibration_artifact_sha256,
                reason_codes=calibrated.reason_codes,
                anchor=anchor,
                binding_ref=binding_ref,
            )
            evidence.append(public_evidence)
            if calibrated.state is VisualCueState.OBSERVED:
                if calibrated.direction is None:
                    continue
                claim = catalog.lookup(
                    cue_id=calibrated.cue_id,
                    expected_phone=anchor.expected_phone,
                    context_id=request.context_id,
                    direction=calibrated.direction,
                )
                if claim is not None and actionable is None:
                    actionable = (public_evidence, claim)
                elif claim is None:
                    claim_missing_for_direction = True

        common = {
            "binding_ref": binding_ref,
            "phone_anchor_ref": anchor.anchor_ref,
            "cue_evidence": tuple(evidence),
            "visual_receipts": visual_receipts,
            "calibration_receipts": calibration_receipts,
        }
        if VisualCueState.OUT_OF_DISTRIBUTION in states:
            return _Draft(
                status=VisualFeedbackStatus.VISUAL_UNSUPPORTED,
                reason_codes=("visual_cue_out_of_calibration_support",),
                **common,
            )
        uncertain_states = {
            VisualCueState.MISSING,
            VisualCueState.LOW_CONFIDENCE,
            VisualCueState.BACKEND_UNAVAILABLE,
        }
        if states & uncertain_states:
            return _Draft(
                status=VisualFeedbackStatus.VISUAL_UNCERTAIN,
                reason_codes=("visual_cue_not_reliably_observed",),
                **common,
            )
        if actionable is None:
            if claim_missing_for_direction:
                return _Draft(
                    status=VisualFeedbackStatus.VISUAL_UNCERTAIN,
                    reason_codes=("approved_visual_claim_not_available",),
                    **common,
                )
            return _Draft(
                status=VisualFeedbackStatus.NO_SUPPORTED_LIP_CUE,
                reason_codes=("no_actionable_visible_lip_deviation",),
                **common,
            )

        selected_evidence, selected_claim = actionable
        if not anchor.is_error:
            return _Draft(
                status=VisualFeedbackStatus.VISUAL_UNCERTAIN,
                reason_codes=("visual_evidence_conflicts_with_upstream",),
                evidence_relation=VisualEvidenceRelation.CONFLICTS_WITH_UPSTREAM,
                **common,
            )

        candidate = SupplementalVisualActionCandidate(
            action_id=selected_claim.action_id,
            approved_claim_id=selected_claim.approved_claim_id,
            catalog_revision=catalog.revision,
            renderer_key=selected_claim.renderer_key,
            upstream_phone_anchor_ref=anchor.anchor_ref,
            supporting_visual_evidence_ids=(selected_evidence.evidence_id,),
        )
        return _Draft(
            status=VisualFeedbackStatus.VISUAL_FEEDBACK_READY,
            reason_codes=("calibrated_visible_cue_supports_upstream",),
            evidence_relation=VisualEvidenceRelation.SUPPORTS_UPSTREAM,
            candidate=candidate,
            **common,
        )

    def _delete_restricted_media(
        self, request: VisualFeedbackRequest
    ) -> MediaDeletionReceipt:
        try:
            return self._video_port.delete(request.video_handle)
        except Exception:  # pragma: no cover - adapter failure is normalized below
            backend_receipt = AdapterReceipt(
                backend_id="restricted_video_lifecycle",
                backend_revision="contract_v1",
                status="failed",
                reason_codes=("media_deletion_adapter_failed",),
            )
            return MediaDeletionReceipt(
                status="failed",
                deletion_receipt_sha256=sha256_document(
                    {
                        "handle_id_sha256": request.video_handle.handle_id,
                        "attempt_id_sha256": request.video_handle.attempt_id_sha256,
                        "status": "failed",
                        "reason": "media_deletion_adapter_failed",
                    }
                ),
                backend_receipt=backend_receipt,
            )


def _map_phone_span(
    *, anchor: UpstreamPhoneAnchor, sync: AvSyncObservation
) -> tuple[int, int] | None:
    if anchor.alignment_operation == "deletion":
        return None
    if anchor.start_ms is None or anchor.end_ms is None:
        return None
    start = sync.video_time_ms(anchor.start_ms)
    end = sync.video_time_ms(anchor.end_ms)
    if not math.isfinite(start) or not math.isfinite(end) or end <= start:
        raise VisualFeedbackContractError("mapped video span is invalid")
    return math.floor(start), math.ceil(end)


def _validate_snapshot_binding(
    *,
    snapshot: SeungunEvidenceSnapshot,
    request: VisualFeedbackRequest,
) -> None:
    binding = request.binding_receipt
    checks = (
        snapshot.analysis_reference_id == request.analysis_ref.reference_id,
        snapshot.analysis_sha256 == request.analysis_ref.analysis_sha256,
        snapshot.attempt_id_sha256 == request.analysis_ref.attempt_id_sha256,
        snapshot.attempt_id_sha256 == request.video_handle.attempt_id_sha256,
        snapshot.binding_receipt_sha256 == binding.receipt_sha256,
        snapshot.prompt_id_sha256 == binding.prompt_id_sha256,
        snapshot.prompt_revision == binding.prompt_revision,
        snapshot.expected_phone_sha256 == binding.expected_phone_sha256,
        snapshot.audio_derivation_receipt_sha256
        == binding.audio_derivation_receipt_sha256,
        snapshot.reliability_receipt_sha256
        == binding.upstream_reliability_receipt_sha256,
        snapshot.reliability_status is UpstreamReliabilityStatus.SUPPORTED,
    )
    if not all(checks):
        raise SeungunEvidenceUnavailableError(
            "resolved Seungun snapshot differs from the bound request"
        )


def _binding_was_issued_during_consent(request: VisualFeedbackRequest) -> bool:
    binding_time = datetime.fromisoformat(
        request.binding_receipt.issued_at_utc.replace("Z", "+00:00")
    )
    consent_start = datetime.fromisoformat(
        request.consent_receipt.issued_at_utc.replace("Z", "+00:00")
    )
    consent_end = datetime.fromisoformat(
        request.consent_receipt.expires_at_utc.replace("Z", "+00:00")
    )
    return consent_start <= binding_time < consent_end


def _public_cue_evidence(
    *,
    cue: LipCueMeasurement,
    state: VisualCueState,
    calibrated_deviation: float | None,
    artifact_sha256: str,
    reason_codes: tuple[str, ...],
    anchor: UpstreamPhoneAnchor,
    binding_ref: str,
) -> VisualCueEvidence:
    observed = state is VisualCueState.OBSERVED
    evidence_id = sha256_document(
        {
            "binding_receipt_sha256": binding_ref,
            "phone_anchor_ref": anchor.anchor_ref,
            "cue_id": cue.cue_id,
            "cue_state": state.value,
            "video_start_ms": cue.video_start_ms,
            "video_end_ms": cue.video_end_ms,
            "measurement_value": cue.value if observed else None,
            "calibrated_deviation": calibrated_deviation,
            "calibration_artifact_sha256": artifact_sha256,
        }
    )
    return VisualCueEvidence(
        evidence_id=evidence_id,
        cue_id=cue.cue_id,
        cue_state=state,
        video_start_ms=cue.video_start_ms,
        video_end_ms=cue.video_end_ms,
        measurement_value=cue.value if observed else None,
        measurement_unit=cue.unit if observed else None,
        calibrated_deviation=calibrated_deviation,
        measurement_backend=cue.receipt,
        calibration_artifact_sha256=artifact_sha256,
        reason_codes=_unique((*cue.reason_codes, *reason_codes)),
    )


def _artifact_receipts(
    calibration: VisualCalibrationArtifact,
    catalog: VisualClaimCatalog,
) -> tuple[AdapterReceipt, ...]:
    return (
        AdapterReceipt(
            backend_id="visual_calibration_artifact",
            backend_revision=calibration.revision,
            status="loaded",
            reason_codes=(calibration.scope.value,),
        ),
        AdapterReceipt(
            backend_id="visual_claim_catalog",
            backend_revision=catalog.revision,
            status="loaded",
            reason_codes=(catalog.scope.value,),
        ),
    )


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


__all__ = ["VisualFeedbackCoordinator"]
