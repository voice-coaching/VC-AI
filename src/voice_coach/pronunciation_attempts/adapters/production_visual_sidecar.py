"""Fail-closed production contract for the optional visual sidecar.

This module deliberately does not load artifacts from ambient paths and does
not enable itself in the application composition root.  A caller must inject a
fully promoted :class:`VisualRuntimeBundle` and its exact automated eligibility
receipt.  Both values are serialized and strictly reloaded before they can be
used.

The adapter implements both host ports used after detection: it first issues a
typed, attempt-local reliability receipt and then turns only an exact
selected-phone, center-frame visible-lip cue into the canonical public
``VisualFeedbackSupplement``.  It never owns raw media; workspace cleanup stays
with ``VideoAttemptFacade``.
"""

from __future__ import annotations

from dataclasses import dataclass
import json

from voice_coach.visual_feedback.adapters.seungun import (
    FrozenSeungunPronunciationEvidenceAdapter,
    InMemorySeungunAnalysisSource,
)
from voice_coach.visual_feedback.errors import VisualFeedbackContractError
from voice_coach.visual_feedback.models.artifacts import CalibrationRule, VisualClaim
from voice_coach.visual_feedback.models.binding import SameAttemptBindingReceipt
from voice_coach.visual_feedback.models.enums import (
    VisualCueState,
    VisualEvidenceRelation,
    VisualFeedbackStatus,
)
from voice_coach.visual_feedback.models.evidence import (
    AdapterReceipt,
    LipCueMeasurement,
    MediaDeletionReceipt,
    SeungunEvidenceSnapshot,
    UpstreamPhoneAnchor,
)
from voice_coach.visual_feedback.models.integrity import (
    canonical_json_bytes,
    sha256_document,
)
from voice_coach.visual_feedback.models.runtime_eligibility import (
    AttemptVisualReliabilityReceipt,
    AutomatedVisualRuntimeEligibilityReceipt,
    VisualRuntimeBundle,
    issue_attempt_visual_reliability,
    load_automated_visual_runtime_eligibility,
    load_visual_runtime_bundle,
    validate_attempt_visual_reliability,
)
from voice_coach.visual_feedback.models.supplement import (
    SupplementalVisualActionCandidate,
    VisualCueEvidence,
    VisualFeedbackSupplement,
)
from voice_coach.visual_feedback.services.handoff import SeungunVisualHandoff

from ..phone_window import DensePhoneWindowEvidence, PolicyValidatedAvSyncOffset
from ..services.video_attempt_facade import (
    HostVisualHandoff,
    VisualDetection,
    VisualReliabilityDecision,
)
from .calibrated_runtime_decisions import (
    RuntimeAvSyncDecision,
    RuntimeVideoQcDecision,
)
from .lip_audio_av_sync import AvSyncCandidateEvidence
from .mediapipe_lip_landmarks import MediaPipeLandmarkBatch
from .runtime import RuntimeVisualDetectionEvidence
from .video_qc import VideoQcCandidateEvidence


_CUE_ID = "lip_aperture_ratio"
_CUE_UNIT = "ratio"
_CALIBRATION_CONTEXT_ID = "phone_center"
_BACKEND_REVISION = "production_visual_sidecar_v1"
_FORBIDDEN_FALLBACK_TOKENS = frozenset(
    {"airflow", "diagnosis", "glottis", "medical", "tongue", "velum"}
)


@dataclass(frozen=True, slots=True)
class _ResolvedAttempt:
    handoff: SeungunVisualHandoff
    snapshot: SeungunEvidenceSnapshot
    anchor: UpstreamPhoneAnchor
    detection: RuntimeVisualDetectionEvidence
    phone_window: DensePhoneWindowEvidence


class ProductionVisualSidecarAdapter:
    """Issue reliability and canonical supplements from injected frozen state.

    This object can be injected into both ``VisualReliabilityPort`` and
    ``VisualSupplementPort``.  Merely constructing it does not activate any
    production entrypoint.
    """

    def __init__(
        self,
        *,
        runtime_bundle: VisualRuntimeBundle,
        eligibility_receipt: AutomatedVisualRuntimeEligibilityReceipt,
    ) -> None:
        self._bundle = _strictly_reload_bundle(runtime_bundle)
        self._eligibility_receipt = _strictly_reload_eligibility(
            eligibility_receipt,
            bundle=self._bundle,
        )

    @property
    def runtime_bundle_sha256(self) -> str:
        """Return the only runtime bundle digest accepted by this instance."""

        return self._bundle.bundle_sha256

    def evaluate(
        self,
        *,
        detection: VisualDetection,
        handoff: HostVisualHandoff,
    ) -> VisualReliabilityDecision:
        """Fail closed unless the complete typed attempt DAG is reproducible."""

        try:
            resolved = self._resolve_attempt(detection=detection, handoff=handoff)
            receipt = self._issue_reliability(resolved)
        except Exception:
            # This is an application boundary: paths, raw values and exception
            # text must never escape in a public reason code.
            return VisualReliabilityDecision(
                supported=False,
                receipt=None,
                reason_codes=("visual_reliability_lineage_mismatch",),
            )
        return VisualReliabilityDecision(
            supported=True,
            receipt=receipt,
        )

    def create(
        self,
        *,
        detection: VisualDetection,
        handoff: HostVisualHandoff,
        reliability: VisualReliabilityDecision,
    ) -> dict[str, object]:
        """Return strict JSON for one supplement without owning raw media."""

        # Resolve the immutable handoff first so even a failed-closed result is
        # bound to a real upstream reference rather than a caller-made digest.
        resolved = self._resolve_attempt(detection=detection, handoff=handoff)
        try:
            if (
                type(reliability) is not VisualReliabilityDecision
                or reliability.supported is not True
                or type(reliability.receipt) is not AttemptVisualReliabilityReceipt
            ):
                raise VisualFeedbackContractError(
                    "visual reliability receipt is unavailable"
                )
            self._validate_reliability(
                resolved=resolved,
                receipt=reliability.receipt,
            )
            supplement = self._create_validated_supplement(
                resolved=resolved,
                reliability_receipt=reliability.receipt,
            )
        except Exception:
            supplement = self._supplement(
                resolved=resolved,
                status=VisualFeedbackStatus.VISUAL_FAILED_CLOSED,
                reason_codes=("visual_supplement_lineage_mismatch",),
            )
        return _strict_supplement_json(supplement)

    def _resolve_attempt(
        self,
        *,
        detection: VisualDetection,
        handoff: HostVisualHandoff,
    ) -> _ResolvedAttempt:
        if (
            type(detection) is not VisualDetection
            or type(handoff) is not HostVisualHandoff
        ):
            raise VisualFeedbackContractError(
                "typed host detection and handoff are required"
            )
        evidence = detection.evidence
        payload = handoff.payload
        binding = handoff.binding_receipt
        if (
            type(evidence) is not RuntimeVisualDetectionEvidence
            or type(payload) is not SeungunVisualHandoff
            or type(binding) is not SameAttemptBindingReceipt
            or type(evidence.phone_window_evidence) is not DensePhoneWindowEvidence
            or type(evidence.video_qc_evidence) is not VideoQcCandidateEvidence
            or type(evidence.video_qc_decision) is not RuntimeVideoQcDecision
            or type(evidence.av_sync_candidate_evidence)
            is not AvSyncCandidateEvidence
            or type(evidence.av_sync_decision) is not RuntimeAvSyncDecision
            or type(evidence.av_sync_evidence) is not PolicyValidatedAvSyncOffset
        ):
            raise VisualFeedbackContractError(
                "complete typed visual attempt evidence is required"
            )
        _validate_attempt_evidence_digests(evidence)
        if (
            payload.request.binding_receipt != binding
            or payload.request.upstream_phone_expected_index
            != handoff.selected_phone_expected_index
            or evidence.binding_receipt_sha256 != binding.receipt_sha256
            or evidence.container_sha256 != binding.video_source_sha256
        ):
            raise VisualFeedbackContractError("host visual handoff lineage differs")
        if (
            payload.request.capture_contract_id
            != self._bundle.pipeline_identity.media_capture_contract_id
            or payload.request.population_id_sha256
            != self._bundle.pipeline_identity.population_id_sha256
        ):
            raise VisualFeedbackContractError("runtime request scope differs")

        source = InMemorySeungunAnalysisSource(
            {payload.source_record.reference.reference_id: payload.source_record}
        )
        snapshot = FrozenSeungunPronunciationEvidenceAdapter(
            source,
            backend_revision="production_sidecar_seungun_snapshot_v1",
        ).resolve(
            analysis_ref=payload.request.analysis_ref,
            binding_receipt=binding,
            video_handle=payload.request.video_handle,
        )
        anchor = snapshot.anchor(handoff.selected_phone_expected_index)
        if (
            anchor is None
            or anchor.expected_index != handoff.selected_phone_expected_index
            or evidence.phone_window_evidence.runtime_receipt.phone_anchor_sha256
            != anchor.anchor_ref
        ):
            raise VisualFeedbackContractError(
                "selected phone anchor lineage differs"
            )
        return _ResolvedAttempt(
            handoff=payload,
            snapshot=snapshot,
            anchor=anchor,
            detection=evidence,
            phone_window=evidence.phone_window_evidence,
        )

    def _issue_reliability(
        self, resolved: _ResolvedAttempt
    ) -> AttemptVisualReliabilityReceipt:
        request = resolved.handoff.request
        detection = resolved.detection
        receipt = issue_attempt_visual_reliability(
            bundle=self._bundle,
            eligibility_receipt=self._eligibility_receipt,
            binding_receipt=request.binding_receipt,
            snapshot=resolved.snapshot,
            expected_index=request.upstream_phone_expected_index,
            media_capture_contract_id=request.capture_contract_id,
            detection_receipt=detection,
            video_qc_receipt=detection.video_qc_decision,
            av_sync_receipt=detection.av_sync_decision,
            phone_window_receipt=resolved.phone_window,
        )
        # Do not let an in-memory-only receipt become authority.  Strictly
        # reconstruct it and reproduce it from every input edge.
        reloaded = AttemptVisualReliabilityReceipt.from_json_object(
            json.loads(canonical_json_bytes(receipt.to_json_object()))
        )
        return self._validate_reliability(resolved=resolved, receipt=reloaded)

    def _validate_reliability(
        self,
        *,
        resolved: _ResolvedAttempt,
        receipt: AttemptVisualReliabilityReceipt,
    ) -> AttemptVisualReliabilityReceipt:
        request = resolved.handoff.request
        detection = resolved.detection
        return validate_attempt_visual_reliability(
            receipt=receipt,
            bundle=self._bundle,
            eligibility_receipt=self._eligibility_receipt,
            binding_receipt=request.binding_receipt,
            snapshot=resolved.snapshot,
            expected_index=request.upstream_phone_expected_index,
            media_capture_contract_id=request.capture_contract_id,
            detection_receipt=detection,
            video_qc_receipt=detection.video_qc_decision,
            av_sync_receipt=detection.av_sync_decision,
            phone_window_receipt=resolved.phone_window,
        )

    def _create_validated_supplement(
        self,
        *,
        resolved: _ResolvedAttempt,
        reliability_receipt: AttemptVisualReliabilityReceipt,
    ) -> VisualFeedbackSupplement:
        request = resolved.handoff.request
        anchor = resolved.anchor
        matching_rules = tuple(
            rule
            for rule in self._bundle.calibration_artifact.rules
            if rule.cue_id == _CUE_ID
            and anchor.expected_phone in rule.expected_phones
            and rule.context_id == _CALIBRATION_CONTEXT_ID
        )
        if not matching_rules:
            return self._supplement(
                resolved=resolved,
                status=VisualFeedbackStatus.NO_SUPPORTED_LIP_CUE,
                reason_codes=("selected_phone_has_no_supported_lip_cue",),
                reliability_receipt=reliability_receipt,
            )
        if len(matching_rules) != 1:
            raise VisualFeedbackContractError(
                "selected phone calibration rule is ambiguous"
            )
        rule = matching_rules[0]
        cue = self._center_cue(resolved=resolved, rule=rule)
        calibrated = self._bundle.calibration_artifact.calibrate(
            cue=cue,
            expected_phone=anchor.expected_phone,
            context_id=_CALIBRATION_CONTEXT_ID,
        )
        evidence = _public_cue_evidence(
            cue=cue,
            calibrated_state=calibrated.state,
            calibrated_deviation=calibrated.calibrated_deviation,
            calibration_artifact_sha256=calibrated.calibration_artifact_sha256,
            calibration_reason_codes=calibrated.reason_codes,
            binding_receipt_sha256=request.binding_receipt.receipt_sha256,
            phone_anchor_sha256=anchor.anchor_ref,
            phone_window_receipt_sha256=(
                resolved.phone_window.runtime_receipt.receipt_sha256
            ),
            reliability_receipt_sha256=reliability_receipt.receipt_sha256,
        )
        common = {
            "resolved": resolved,
            "cue_evidence": (evidence,),
            "reliability_receipt": reliability_receipt,
        }
        if calibrated.state is VisualCueState.OUT_OF_DISTRIBUTION:
            return self._supplement(
                status=VisualFeedbackStatus.VISUAL_UNSUPPORTED,
                reason_codes=("visual_cue_out_of_calibration_support",),
                **common,
            )
        if calibrated.state is not VisualCueState.OBSERVED:
            return self._supplement(
                status=VisualFeedbackStatus.VISUAL_UNCERTAIN,
                reason_codes=("selected_phone_center_lip_not_observed",),
                **common,
            )
        if calibrated.direction is None:
            return self._supplement(
                status=VisualFeedbackStatus.NO_SUPPORTED_LIP_CUE,
                reason_codes=("no_actionable_visible_lip_deviation",),
                **common,
            )

        matching_claims = tuple(
            claim
            for claim in self._bundle.claim_catalog.claims
            if claim.cue_id == calibrated.cue_id
            and anchor.expected_phone in claim.expected_phones
            and claim.context_id == _CALIBRATION_CONTEXT_ID
            and claim.direction is calibrated.direction
        )
        if not matching_claims:
            return self._supplement(
                status=VisualFeedbackStatus.NO_SUPPORTED_LIP_CUE,
                reason_codes=("approved_visual_claim_not_available",),
                **common,
            )
        if len(matching_claims) != 1:
            raise VisualFeedbackContractError("selected visual claim is ambiguous")
        claim = matching_claims[0]
        self._validate_claim_policy(claim)
        candidate = SupplementalVisualActionCandidate(
            action_id=claim.action_id,
            approved_claim_id=claim.approved_claim_id,
            catalog_revision=self._bundle.claim_catalog.revision,
            renderer_key=claim.renderer_key,
            upstream_phone_anchor_ref=anchor.anchor_ref,
            supporting_visual_evidence_ids=(evidence.evidence_id,),
        )
        return self._supplement(
            status=VisualFeedbackStatus.VISUAL_FEEDBACK_READY,
            reason_codes=("calibrated_visible_cue_supports_upstream",),
            relation=VisualEvidenceRelation.SUPPORTS_UPSTREAM,
            candidate=candidate,
            **common,
        )

    def _center_cue(
        self,
        *,
        resolved: _ResolvedAttempt,
        rule: CalibrationRule,
    ) -> LipCueMeasurement:
        dense = resolved.phone_window
        landmarks = dense.landmark_evidence
        if type(landmarks) is not MediaPipeLandmarkBatch:
            raise VisualFeedbackContractError(
                "production center cue requires MediaPipe landmark evidence"
            )
        decode = dense.decode_receipt
        tracking = dense.tracking_receipt
        binding = resolved.handoff.request.binding_receipt
        offset = resolved.detection.av_sync_evidence
        if type(offset) is not PolicyValidatedAvSyncOffset:
            raise VisualFeedbackContractError("validated A/V offset is unavailable")
        expected_timestamps_ms = tuple(
            timestamp_us // 1_000 for timestamp_us in decode.decoded_timestamps_us
        )
        actual_timestamps_ms = tuple(frame.timestamp_ms for frame in landmarks.frames)
        expected_timestamps_sha256 = sha256_document(
            {
                "video_stream_index": decode.video_stream_index,
                "timestamps_us": list(decode.decoded_timestamps_us),
                "ffmpeg_revision": decode.ffmpeg_revision,
            }
        )
        if (
            actual_timestamps_ms != expected_timestamps_ms
            or expected_timestamps_sha256 != tracking.sample_timestamps_sha256
            or landmarks.evidence_sha256 != tracking.landmark_evidence_sha256
            or landmarks.model_sha256
            != self._bundle.pipeline_identity.landmark_model_sha256
            or landmarks.container_sha256 != binding.video_source_sha256
        ):
            raise VisualFeedbackContractError(
                "dense landmark timestamp or model lineage differs"
            )

        anchor = resolved.anchor
        if anchor.start_ms is None or anchor.end_ms is None:
            raise VisualFeedbackContractError("selected phone has no timed span")
        mapped_start_ms = (
            binding.audio_timebase.origin_offset_ms
            + anchor.start_ms
            + offset.offset_ms
            - binding.video_timebase.origin_offset_ms
        )
        mapped_end_ms = (
            binding.audio_timebase.origin_offset_ms
            + anchor.end_ms
            + offset.offset_ms
            - binding.video_timebase.origin_offset_ms
        )
        runtime = dense.runtime_receipt
        if (
            mapped_start_ms < 0
            or mapped_end_ms <= mapped_start_ms
            or mapped_end_ms > binding.video_timebase.duration_ms
            or runtime.video_start_ms > mapped_start_ms
            or runtime.video_end_ms < mapped_end_ms
        ):
            raise VisualFeedbackContractError(
                "selected phone span differs from the dense window"
            )
        target_ms = (mapped_start_ms + mapped_end_ms) / 2.0
        center_frame = min(
            landmarks.frames,
            key=lambda frame: (abs(frame.timestamp_ms - target_ms), frame.timestamp_ms),
        )
        backend = AdapterReceipt(
            backend_id="dense_phone_window_center_cue",
            backend_revision=_BACKEND_REVISION,
            status=(
                "observed" if center_frame.lip_geometry is not None else "missing"
            ),
            reason_codes=(
                ()
                if center_frame.lip_geometry is not None
                else ("selected_phone_center_lip_not_observed",)
            ),
        )
        if center_frame.lip_geometry is None:
            return LipCueMeasurement(
                cue_id=_CUE_ID,
                state=VisualCueState.MISSING,
                value=None,
                unit=rule.measurement_unit,
                video_start_ms=mapped_start_ms,
                video_end_ms=mapped_end_ms,
                reason_codes=("selected_phone_center_lip_not_observed",),
                receipt=backend,
            )
        return LipCueMeasurement(
            cue_id=_CUE_ID,
            state=VisualCueState.OBSERVED,
            value=center_frame.lip_geometry.aperture_ratio,
            unit=_CUE_UNIT,
            video_start_ms=mapped_start_ms,
            video_end_ms=mapped_end_ms,
            reason_codes=(),
            receipt=backend,
        )

    def _validate_claim_policy(self, claim: VisualClaim) -> None:
        policy = self._bundle.claim_policy
        binding = (
            claim.approved_claim_id,
            claim.action_id,
            claim.renderer_key,
            claim.cue_id,
        )
        allowed = {
            (
                item.approved_claim_id,
                item.action_id,
                item.renderer_key,
                item.cue_id,
            )
            for item in policy.allowed_claim_bindings
        }
        forbidden = {
            token.lower() for token in policy.forbidden_identifier_tokens
        } | _FORBIDDEN_FALLBACK_TOKENS
        identifiers = tuple(value.lower() for value in binding)
        if (
            binding not in allowed
            or claim.cue_id not in policy.allowed_cue_ids
            or claim.renderer_key not in policy.allowed_renderer_keys
            or any(
                token in identifier
                for token in forbidden
                for identifier in identifiers
            )
        ):
            raise VisualFeedbackContractError(
                "visual claim is outside the frozen identifier policy"
            )

    def _supplement(
        self,
        *,
        resolved: _ResolvedAttempt,
        status: VisualFeedbackStatus,
        reason_codes: tuple[str, ...],
        cue_evidence: tuple[VisualCueEvidence, ...] = (),
        reliability_receipt: AttemptVisualReliabilityReceipt | None = None,
        relation: VisualEvidenceRelation = VisualEvidenceRelation.NOT_COMPARABLE,
        candidate: SupplementalVisualActionCandidate | None = None,
    ) -> VisualFeedbackSupplement:
        request = resolved.handoff.request
        visual_receipts = (
            ()
            if reliability_receipt is None
            else (
                AdapterReceipt(
                    backend_id="attempt_visual_reliability",
                    backend_revision=_BACKEND_REVISION,
                    status="validated",
                ),
            )
        )
        calibration_receipts = (
            ()
            if not cue_evidence
            else (
                AdapterReceipt(
                    backend_id="visual_calibration_artifact",
                    backend_revision=self._bundle.calibration_artifact.revision,
                    status="loaded",
                    reason_codes=(self._bundle.calibration_artifact.scope.value,),
                ),
                AdapterReceipt(
                    backend_id="visual_claim_catalog",
                    backend_revision=self._bundle.claim_catalog.revision,
                    status="loaded",
                    reason_codes=(self._bundle.claim_catalog.scope.value,),
                ),
            )
        )
        return VisualFeedbackSupplement(
            status=status,
            reason_codes=reason_codes,
            upstream_analysis_ref=request.analysis_ref.reference_id,
            upstream_binding_ref=request.binding_receipt.receipt_sha256,
            upstream_phone_anchor_ref=resolved.anchor.anchor_ref,
            cue_evidence=cue_evidence,
            evidence_relation=relation,
            supplemental_action_candidate=candidate,
            visual_backend_receipts=visual_receipts,
            reference_calibration_receipts=calibration_receipts,
            media_deletion_receipt=_no_raw_media_receipt(
                attempt_id_sha256=request.binding_receipt.attempt_id_sha256,
                binding_receipt_sha256=request.binding_receipt.receipt_sha256,
            ),
            notices=(
                "visual_supplement_only",
                "upstream_evidence_unchanged",
                "sidecar_owns_no_raw_media",
                "host_owns_media_cleanup",
            ),
        )


def _strictly_reload_bundle(value: VisualRuntimeBundle) -> VisualRuntimeBundle:
    if type(value) is not VisualRuntimeBundle:
        raise VisualFeedbackContractError("typed visual runtime bundle is required")
    reloaded = load_visual_runtime_bundle(
        json.loads(canonical_json_bytes(value.to_json_object()))
    )
    if reloaded != value:
        raise VisualFeedbackContractError("visual runtime bundle reload differs")
    return reloaded


def _validate_attempt_evidence_digests(
    evidence: RuntimeVisualDetectionEvidence,
) -> None:
    """Recompute every candidate/decision receipt before trusting its type."""

    qc_candidate = evidence.video_qc_evidence
    qc_decision = evidence.video_qc_decision
    sync_candidate = evidence.av_sync_candidate_evidence
    sync_decision = evidence.av_sync_decision
    offset = evidence.av_sync_evidence
    if (
        type(qc_candidate) is not VideoQcCandidateEvidence
        or type(qc_decision) is not RuntimeVideoQcDecision
        or type(sync_candidate) is not AvSyncCandidateEvidence
        or type(sync_decision) is not RuntimeAvSyncDecision
        or type(offset) is not PolicyValidatedAvSyncOffset
    ):
        raise VisualFeedbackContractError(
            "typed candidate and decision receipts are required"
        )
    checks = (
        qc_candidate.provenance_receipt_sha256
        == sha256_document(qc_candidate.provenance_json_object()),
        qc_candidate.receipt_sha256
        == sha256_document(qc_candidate.unsigned_json_object()),
        qc_decision.receipt_sha256
        == sha256_document(qc_decision.unsigned_json_object()),
        sync_candidate.lineage_receipt_sha256
        == sha256_document(sync_candidate.lineage_json_object()),
        sync_candidate.receipt_sha256
        == sha256_document(sync_candidate.unsigned_json_object()),
        sync_decision.receipt_sha256
        == sha256_document(sync_decision.unsigned_json_object()),
        offset.receipt_sha256 == sha256_document(offset.unsigned_json_object()),
        evidence.detection_receipt_sha256
        == sha256_document(evidence.unsigned_json_object()),
    )
    if not all(checks):
        raise VisualFeedbackContractError(
            "runtime candidate or decision receipt digest differs"
        )


def _strictly_reload_eligibility(
    value: AutomatedVisualRuntimeEligibilityReceipt,
    *,
    bundle: VisualRuntimeBundle,
) -> AutomatedVisualRuntimeEligibilityReceipt:
    if type(value) is not AutomatedVisualRuntimeEligibilityReceipt:
        raise VisualFeedbackContractError(
            "typed automated runtime eligibility receipt is required"
        )
    reloaded = load_automated_visual_runtime_eligibility(
        json.loads(canonical_json_bytes(value.to_json_object())),
        bundle=bundle,
    )
    if reloaded != value:
        raise VisualFeedbackContractError("runtime eligibility reload differs")
    return reloaded


def _public_cue_evidence(
    *,
    cue: LipCueMeasurement,
    calibrated_state: VisualCueState,
    calibrated_deviation: float | None,
    calibration_artifact_sha256: str,
    calibration_reason_codes: tuple[str, ...],
    binding_receipt_sha256: str,
    phone_anchor_sha256: str,
    phone_window_receipt_sha256: str,
    reliability_receipt_sha256: str,
) -> VisualCueEvidence:
    observed = calibrated_state is VisualCueState.OBSERVED
    evidence_id = sha256_document(
        {
            "schema_version": "voice_coach.selected_phone_center_cue.v1",
            "binding_receipt_sha256": binding_receipt_sha256,
            "phone_anchor_sha256": phone_anchor_sha256,
            "phone_window_receipt_sha256": phone_window_receipt_sha256,
            "reliability_receipt_sha256": reliability_receipt_sha256,
            "cue_id": cue.cue_id,
            "cue_state": calibrated_state.value,
            "video_start_ms": cue.video_start_ms,
            "video_end_ms": cue.video_end_ms,
            "measurement_value": cue.value if observed else None,
            "calibrated_deviation": calibrated_deviation,
            "calibration_artifact_sha256": calibration_artifact_sha256,
        }
    )
    return VisualCueEvidence(
        evidence_id=evidence_id,
        cue_id=cue.cue_id,
        cue_state=calibrated_state,
        video_start_ms=cue.video_start_ms,
        video_end_ms=cue.video_end_ms,
        measurement_value=cue.value if observed else None,
        measurement_unit=cue.unit if observed else None,
        calibrated_deviation=calibrated_deviation,
        measurement_backend=cue.receipt,
        calibration_artifact_sha256=calibration_artifact_sha256,
        reason_codes=tuple(
            dict.fromkeys((*cue.reason_codes, *calibration_reason_codes))
        ),
    )


def _no_raw_media_receipt(
    *,
    attempt_id_sha256: str,
    binding_receipt_sha256: str,
) -> MediaDeletionReceipt:
    status = "not_applicable_no_raw_media"
    digest = sha256_document(
        {
            "schema_version": "voice_coach.sidecar_media_ownership.v1",
            "attempt_id_sha256": attempt_id_sha256,
            "binding_receipt_sha256": binding_receipt_sha256,
            "sidecar_raw_media_owner": False,
            "workspace_cleanup_owner": "video_attempt_facade",
            "status": status,
        }
    )
    return MediaDeletionReceipt(
        status=status,
        deletion_receipt_sha256=digest,
        backend_receipt=AdapterReceipt(
            backend_id="visual_sidecar_media_ownership",
            backend_revision=_BACKEND_REVISION,
            status=status,
            reason_codes=("host_owns_media_cleanup",),
        ),
    )


def _strict_supplement_json(
    supplement: VisualFeedbackSupplement,
) -> dict[str, object]:
    document = json.loads(canonical_json_bytes(supplement.to_json_object()))
    reloaded = VisualFeedbackSupplement.from_json_object(document)
    if reloaded != supplement:
        raise VisualFeedbackContractError("visual supplement reload differs")
    return reloaded.to_json_object()


__all__ = ["ProductionVisualSidecarAdapter"]
