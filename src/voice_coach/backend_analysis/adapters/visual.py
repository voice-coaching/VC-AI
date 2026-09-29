"""Same-attempt bridge from a Backend request into the promoted visual sidecar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
import secrets
import stat
import tempfile

from voice_coach.pronunciation_attempts.composition import (
    build_local_video_attempt_facade,
)
from voice_coach.pronunciation_attempts.models import (
    VideoConsentAuthorization,
    VideoConsentDecision,
)
from voice_coach.pronunciation_attempts.services.video_attempt_facade import (
    SeungunRuntimeResult,
    TrustedPrompt,
    VideoAttemptCommand,
    VideoAttemptStatus,
)
from voice_coach.visual_feedback.adapters.production_visual_release import (
    load_production_visual_release,
)
from voice_coach.visual_feedback.adapters.p6_geometry_reload import (
    load_p6_geometry_artifact,
)
from voice_coach.visual_feedback.models.binding import ConsentReceipt
from voice_coach.visual_feedback.models.enums import ConsentDecision
from voice_coach.visual_feedback.models.integrity import (
    canonical_json_bytes,
    sha256_document,
)

from ..domain import AnalysisOutcome, AnalysisRequest, VisualSupplement
from ..ports import (
    BackendAnalysisError,
    MaterializedAudio,
    MaterializedVideo,
    SeungunAnalysis,
)


_CONSENT_REFERENCE = "backend_signed_visual_grant_v1"
_RETENTION_POLICY = "delete_on_completion_v1"


@dataclass(frozen=True, slots=True)
class BackendVisualSettings:
    repository_root: Path
    workspace_root: Path
    nltk_data_root: Path
    landmark_model_path: Path
    production_release_root: Path
    artifact_group_id: int | None = None
    closed_beta_geometry_artifact_path: Path | None = None

    def __post_init__(self) -> None:
        for path in (
            self.repository_root,
            self.workspace_root,
            self.nltk_data_root,
            self.landmark_model_path,
            self.production_release_root,
        ):
            if not path.is_absolute():
                raise ValueError("backend_visual_configuration_invalid")
        if not self.landmark_model_path.is_file():
            raise ValueError("backend_visual_configuration_invalid")
        if (
            self.closed_beta_geometry_artifact_path is not None
            and (
                not self.closed_beta_geometry_artifact_path.is_absolute()
                or not self.closed_beta_geometry_artifact_path.is_file()
            )
        ):
            raise ValueError("backend_visual_configuration_invalid")
        if self.artifact_group_id is not None and (
            isinstance(self.artifact_group_id, bool)
            or not 1 <= self.artifact_group_id <= 2_147_483_647
        ):
            raise ValueError("backend_visual_configuration_invalid")


class _GrantConsentAuthority:
    def __init__(self, request: AnalysisRequest) -> None:
        visual = request.visual_input
        if visual is None:
            raise ValueError("visual input is required")
        grant = request.authorization_grant
        attempt_id = sha256_document(
            {
                "schema_version": "voice_coach.backend_visual_attempt.v1",
                "request_event_id": str(request.event_id),
                "visual_sha256": visual.sha256,
                "backend_visual_consent_receipt_sha256": (
                    grant.visual_consent_receipt_sha256
                ),
            }
        )
        receipt = ConsentReceipt.issue(
            attempt_id_sha256=attempt_id,
            decision=ConsentDecision.GRANTED,
            retention_policy_id=_RETENTION_POLICY,
            # The video-attempt lineage canonicalizes timestamps to six
            # fractional digits.  Reusing the JSON spelling (which may omit
            # ``.000000``) makes equivalent Instants fail the exact receipt
            # comparison in the handoff.
            issued_at_utc=_canonical_utc_text(grant.issued_at),
            expires_at_utc=_canonical_utc_text(grant.expires_at),
        )
        self._receipt = receipt
        self._authorization = VideoConsentAuthorization(
            attempt_id_sha256=attempt_id,
            consent_receipt_sha256=receipt.receipt_sha256,
            decision=VideoConsentDecision.GRANTED,
            purpose=receipt.purpose,
            data_category=receipt.data_category,
            retention_policy_id=receipt.retention_policy_id,
            delete_on_completion=receipt.delete_on_completion,
            remote_egress_allowed=receipt.remote_egress_allowed,
            issued_at_utc=grant.issued_at,
            expires_at_utc=grant.expires_at,
        )

    def authorize(
        self, *, prompt_id: str, consent_reference: str
    ) -> VideoConsentAuthorization:
        del prompt_id
        if not secrets.compare_digest(consent_reference, _CONSENT_REFERENCE):
            raise BackendAnalysisError(
                "visual_consent_unavailable", "영상 처리 권한을 확인할 수 없습니다."
            )
        return self._authorization

    def receipt_for(
        self, authorization: VideoConsentAuthorization
    ) -> ConsentReceipt:
        if authorization != self._authorization:
            raise BackendAnalysisError(
                "visual_consent_unavailable", "영상 처리 권한을 확인할 수 없습니다."
            )
        return self._receipt


def _canonical_utc_text(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


class _RequestPrompt:
    def __init__(self, request: AnalysisRequest, analysis: SeungunAnalysis) -> None:
        self._prompt = TrustedPrompt(
            prompt_id=f"analysis-{request.analysis_id}",
            revision=request.prompt_revision,
            normalized_text=request.script_text,
            expected_phones=analysis.expected_phones,
        )

    def resolve(self, *, prompt_id: str) -> TrustedPrompt:
        if prompt_id != self._prompt.prompt_id:
            raise ValueError("trusted prompt mismatch")
        return self._prompt


class _PrecomputedSeungun:
    def __init__(self, analysis: SeungunAnalysis) -> None:
        self._analysis = analysis

    def analyze(self, *, audio: object, prompt: TrustedPrompt) -> SeungunRuntimeResult:
        derivation = getattr(audio, "derivation_receipt", None)
        if (
            self._analysis.analysis_evidence is None
            or not self._analysis.expected_phones
            or prompt.expected_phones != self._analysis.expected_phones
            or derivation is None
            or derivation.pcm_sha256 != self._analysis.analysis_pcm_sha256
        ):
            raise BackendAnalysisError(
                "visual_same_attempt_mismatch", "동일 시도 근거를 확인할 수 없습니다."
            )
        selected = self._analysis.pronunciation_evidence
        return SeungunRuntimeResult(
            audio_result={
                "status": (
                    "coaching_ready"
                    if self._analysis.outcome is AnalysisOutcome.COACHING_READY
                    else "completed_no_issue"
                ),
                "message": self._analysis.summary_feedback,
            },
            analysis_evidence=self._analysis.analysis_evidence,
            selected_phone_expected_index=(
                selected.selected_expected_index if selected is not None else None
            ),
        )


class ProductionBackendVisualAnalysisAdapter:
    """Run only promoted visual evidence after the authoritative audio result."""

    def __init__(self, settings: BackendVisualSettings, *, http_admitted: bool = False) -> None:
        self._settings = settings
        self._http_admitted = http_admitted
        _prepare_workspace_root(settings.workspace_root)
        release = load_production_visual_release(
            settings.production_release_root,
            trusted_group_id=settings.artifact_group_id,
        )
        if release.status != "enabled":
            raise ValueError("backend_visual_release_not_enabled")
        self._release = release
        self._closed_beta_geometry_artifact = (
            load_p6_geometry_artifact(
                settings.closed_beta_geometry_artifact_path,
                repository_root=settings.repository_root,
                trusted_group_id=settings.artifact_group_id,
            )
            if settings.closed_beta_geometry_artifact_path is not None
            else None
        )

    def analyze(
        self,
        *,
        request: AnalysisRequest,
        audio: MaterializedAudio,
        video: MaterializedVideo,
        seungun: SeungunAnalysis,
    ) -> VisualSupplement | None:
        public, _ = self.analyze_with_grounded_evidence(
            request=request, audio=audio, video=video, seungun=seungun
        )
        return public

    def analyze_coaching_observations(self, *, request, audio, video, seungun):
        """Return same-attempt observations even without an approved action."""
        from ..coaching_evidence import build_coaching_input
        from voice_coach.visual_feedback.models.supplement import VisualFeedbackSupplement
        import math
        if (not self._http_admitted or request.visual_input is None
                or video.sha256 != request.visual_input.sha256 or audio.sha256 != request.audio_sha256):
            raise ValueError("coaching_visual_binding_invalid")
        source = build_coaching_input(analysis=seungun, text=request.script_text,
                                     duration_ms=request.duration_ms)
        indices = [int(c["evidenceIds"][0].split("-")[-1]) for c in source["candidates"][:3]]
        if not indices:
            return []
        with tempfile.TemporaryDirectory(prefix="coaching-", dir=self._settings.workspace_root) as directory:
            temporary_root = Path(directory)
            facade = build_local_video_attempt_facade(
                repository_root=self._settings.repository_root,
                restricted_media_root=temporary_root / "media", restricted_audio_root=temporary_root / "audio",
                nltk_data_root=self._settings.nltk_data_root, seungun_detector=None,
                landmark_model_path=self._settings.landmark_model_path,
                video_qc_decision_policy=self._release.video_qc_decision_policy,
                av_sync_decision_policy=self._release.av_sync_decision_policy,
                visual_runtime_bundle=self._release.runtime_bundle,
                closed_beta_lip_geometry_artifact=self._closed_beta_geometry_artifact,
                consent_authority=None, trusted_prompt_port=_RequestPrompt(request, seungun),
                seungun_runtime_port=_PrecomputedSeungun(seungun), http_admitted=True,
                clock=lambda: datetime.now(tz=UTC))
            attempt = sha256_document(dict(request_id=str(request.event_id),
                execution_id=str(request.execution_id), audio_sha256=audio.sha256,
                video_sha256=video.sha256, policy="pronunciation-coaching-evidence-v1"))
            captures = facade.run_http_observations(video_locator=str(video.path), attempt_id_sha256=attempt,
                expected_sha256=video.sha256, prompt_id=f"analysis-{request.analysis_id}", expected_indices=indices)
        observations = []
        candidate_by_index = {int(c["evidenceIds"][0].split("-")[-1]): c for c in source["candidates"][:3]}
        for capture in captures:
            measures = []
            supplement = capture["supplement"]
            if supplement is not None:
                # Strict aggregate schema; do not require or export action claims.
                VisualFeedbackSupplement.from_json_object(supplement)
                for cue in supplement["cue_evidence"]:
                    value, start, end = cue.get("measurement_value"), cue.get("video_start_ms"), cue.get("video_end_ms")
                    if (cue.get("cue_state") != "observed" or type(value) not in (int, float)
                            or not math.isfinite(value) or type(start) is not int or type(end) is not int
                            or not 0 <= start < end <= capture["videoDurationMs"]):
                        continue
                    measures.append(dict(cueId=cue["cue_id"], value=value, unit=cue["measurement_unit"],
                        startMs=start, endMs=end, sampleCount=None,
                        samplingProvenance="AGGREGATE_CONTRACT_NO_SAMPLE_TIMESTAMPS"))
            candidate = candidate_by_index[capture["expectedIndex"]]
            observations.append(dict(expectedIndex=capture["expectedIndex"], stage=capture["stage"],
                observationId=f"visual-{capture['expectedIndex']}-0", candidateId=candidate["candidateId"],
                audioEvidenceId=candidate["evidenceIds"][0],
                provenance=dict(attemptSha256=attempt, audioSha256=audio.sha256,
                    videoSha256=video.sha256, measurementReceipt=capture["measurementReceipt"]),
                reasonCodes=capture["reasonCodes"], measurements=measures,
                decodedSampleTimesMs=capture["decodedSampleTimesMs"],
                samplingScope="DECODED_WINDOW_NOT_VALID_LANDMARK_COUNT"))
        return observations

    def analyze_with_grounded_evidence(
        self,
        *,
        request: AnalysisRequest,
        audio: MaterializedAudio,
        video: MaterializedVideo,
        seungun: SeungunAnalysis,
    ) -> tuple[VisualSupplement | None, dict[str, object] | None]:
        """Retain validated aggregate evidence for local, same-attempt presentation.

        The second value stays inside the service and is never serialized into
        the backend result. This path reuses Seungun and executes visual only once.
        """

        del audio
        visual = request.visual_input
        selected = seungun.pronunciation_evidence
        if (
            visual is None
            or selected is None
            or video.sha256 != visual.sha256
            or seungun.outcome is not AnalysisOutcome.COACHING_READY
        ):
            return None, None
        authority = None if self._http_admitted else _GrantConsentAuthority(request)
        prompt = _RequestPrompt(request, seungun)
        precomputed = _PrecomputedSeungun(seungun)
        with tempfile.TemporaryDirectory(
            prefix="attempt-", dir=self._settings.workspace_root
        ) as temporary_root_text:
            temporary_root = Path(temporary_root_text)
            facade = build_local_video_attempt_facade(
                repository_root=self._settings.repository_root,
                restricted_media_root=temporary_root / "media",
                restricted_audio_root=temporary_root / "audio",
                nltk_data_root=self._settings.nltk_data_root,
                seungun_detector=None,
                landmark_model_path=self._settings.landmark_model_path,
                video_qc_decision_policy=self._release.video_qc_decision_policy,
                av_sync_decision_policy=self._release.av_sync_decision_policy,
                visual_runtime_bundle=self._release.runtime_bundle,
                closed_beta_lip_geometry_artifact=(
                    self._closed_beta_geometry_artifact
                ),
                consent_authority=authority,
                trusted_prompt_port=prompt,
                seungun_runtime_port=precomputed,
                http_admitted=self._http_admitted,
                clock=lambda: datetime.now(tz=UTC),
            )
            if self._http_admitted:
                attempt_digest = sha256_document({
                    "schema_version": "voice_coach.http_video_execution.v1",
                    "request_id": str(request.event_id), "execution_id": str(request.execution_id),
                    "audio_sha256": request.audio_sha256, "video_sha256": visual.sha256,
                    "selected_expected_index": selected.selected_expected_index,
                })
                supplement = facade.run_http(video_locator=str(video.path),
                    attempt_id_sha256=attempt_digest, expected_sha256=visual.sha256,
                    prompt_id=f"analysis-{request.analysis_id}")
                if supplement is None:
                    return None, None
                return _public_projection(supplement, selected.selected_expected_index), supplement
            result = facade.run(
                VideoAttemptCommand(
                    prompt_id=f"analysis-{request.analysis_id}",
                    video_locator=str(video.path),
                    consent_reference=_CONSENT_REFERENCE,
                )
            )
        if (
            result.status is not VideoAttemptStatus.AUDIO_COMPLETED_VISUAL_SUPPLEMENTED
            or result.visual_supplement is None
        ):
            return None, None
        public = _public_projection(result.visual_supplement, selected.selected_expected_index)
        return public, result.visual_supplement


def _public_projection(
    supplement: object, selected_expected_index: int
) -> VisualSupplement:
    if not isinstance(supplement, dict):
        raise ValueError("visual supplement is invalid")
    candidate = supplement.get("supplemental_action_candidate")
    if (
        supplement.get("status") != "visual_feedback_ready"
        or supplement.get("evidence_relation") != "supports_upstream"
        or not isinstance(candidate, dict)
    ):
        raise ValueError("visual supplement is not publishable")
    canonical = canonical_json_bytes(supplement)
    return VisualSupplement(
        selected_expected_index=selected_expected_index,
        evidence_relation="supports_upstream",
        approved_claim_id=str(candidate.get("approved_claim_id", "")),
        renderer_key=str(candidate.get("renderer_key", "")),
        upstream_phone_anchor_ref=str(
            candidate.get("upstream_phone_anchor_ref", "")
        ),
        supplement_sha256=hashlib.sha256(canonical).hexdigest(),
        closed_beta_lip_observation=_closed_beta_lip_observation(
            supplement,
            selected_expected_index,
        ),
    )


def _closed_beta_lip_observation(
    supplement: dict[str, object], selected_expected_index: int
) -> dict[str, object] | None:
    notices = supplement.get("notices")
    if (
        not isinstance(notices, list | tuple)
        or "closed_beta_lip_trajectory_observed" not in notices
    ):
        return None
    evidence = supplement.get("cue_evidence")
    if not isinstance(evidence, list):
        raise ValueError("closed beta visual evidence is invalid")
    beta: list[dict[str, object]] = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        backend = item.get("measurement_backend")
        if (
            isinstance(backend, dict)
            and backend.get("backend_id") == "closed_beta_p6_lip_trajectory"
        ):
            beta.append(item)
    if not beta:
        raise ValueError("closed beta visual evidence is unavailable")
    start_ms = beta[0].get("video_start_ms")
    end_ms = beta[0].get("video_end_ms")
    geometry_sha256 = beta[0].get("calibration_artifact_sha256")
    measurements: dict[str, object] = {}
    for item in beta:
        cue_id = item.get("cue_id")
        measurement_value = item.get("measurement_value")
        measurement_unit = item.get("measurement_unit")
        if (
            item.get("cue_state") != "observed"
            or item.get("video_start_ms") != start_ms
            or item.get("video_end_ms") != end_ms
            or item.get("calibration_artifact_sha256") != geometry_sha256
            or not isinstance(cue_id, str)
            or cue_id in measurements
            or isinstance(measurement_value, bool)
            or not isinstance(measurement_value, int | float)
            or not isinstance(measurement_unit, str)
        ):
            raise ValueError("closed beta visual evidence is invalid")
        measurements[cue_id] = {
            "value": float(measurement_value),
            "unit": measurement_unit,
        }
    observation = {
        "schemaVersion": "voice-coaching.closed-beta-lip-observation.v1",
        "status": "OBSERVED",
        "selectedExpectedIndex": selected_expected_index,
        "videoStartMs": start_ms,
        "videoEndMs": end_ms,
        "geometryArtifactSha256": geometry_sha256,
        "measurements": measurements,
        "containsPronunciationTruth": False,
        "containsActionTruth": False,
    }
    # Reuse the public contract validator before the observation leaves AI.
    VisualSupplement(
        selected_expected_index=selected_expected_index,
        evidence_relation="supports_upstream",
        approved_claim_id="closed_beta.validation",
        renderer_key="closed_beta_validation",
        upstream_phone_anchor_ref="0" * 64,
        supplement_sha256="0" * 64,
        closed_beta_lip_observation=observation,
    )
    return observation


def _prepare_workspace_root(root: Path) -> None:
    if root in {Path("/"), Path("/tmp"), Path.home()}:
        raise ValueError("backend_visual_configuration_invalid")
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError:
        pass
    except OSError:
        raise ValueError("backend_visual_configuration_invalid") from None
    try:
        metadata = os.lstat(root)
    except OSError:
        raise ValueError("backend_visual_configuration_invalid") from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ValueError("backend_visual_configuration_invalid")


__all__ = ["BackendVisualSettings", "ProductionBackendVisualAnalysisAdapter"]
