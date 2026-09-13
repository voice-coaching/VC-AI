"""Reviewed production composition for the private VC-BE analysis worker."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from voice_coach.pronunciation_attempts.adapters.persistent_seungun import (
    PersistentIsolatedSeungunProductionAdapter,
)
from voice_coach.pronunciation_feedback.adapters.expected_phones_subprocess import (
    SubprocessG2PKExpectedPhoneFrontend,
)

from .adapters.hmac_authorization import (
    HmacAnalysisAuthorizationAdapter,
    HmacAuthorizationSettings,
)
from .adapters.s3_object_store import (
    S3CompatibleRestrictedObjectStore,
    S3ObjectStoreSettings,
    create_s3_client_from_environment,
)
from .adapters.seungun import (
    RestrictedFfmpegAudioDecoder,
    SeungunBackendAnalysisAdapter,
)
from .adapters.visual import (
    BackendVisualSettings,
    ProductionBackendVisualAnalysisAdapter,
)
from .deployment_attestation import verify_deployment_attestation
from .grounded_feedback import load_grounded_feedback
from .service import BackendAnalysisWorker


def create_worker(
    environment: Mapping[str, str] | None = None,
) -> BackendAnalysisWorker:
    """Compose authorized Seungun/visual analysis and optional local Clova advice."""

    return _create_core(os.environ if environment is None else environment, http=False)


def create_http_core(environment: Mapping[str, str]) -> BackendAnalysisWorker:
    """Only called by the HTTP executor after a validated deployment bootstrap."""
    return _create_core(environment, http=True)


def _create_core(env, *, http):
    try:
        repository_root = Path(env["VOICE_COACH_REPOSITORY_ROOT"])
        workspace_root = Path(env["AI_ANALYSIS_RESTRICTED_WORKSPACE_ROOT"])
        bucket = env["AI_ANALYSIS_OBJECT_STORAGE_BUCKET"]
        recordings_prefix = env.get(
            "AI_ANALYSIS_OBJECT_STORAGE_RECORDINGS_PREFIX", "recordings/"
        )
        nltk_data_root = Path(env["AI_ANALYSIS_NLTK_DATA_ROOT"])
        device = env.get("AI_ANALYSIS_SEUNGUN_DEVICE", "cpu")
        maximum_audio_bytes = int(
            env.get("AI_ANALYSIS_OBJECT_STORAGE_MAXIMUM_AUDIO_BYTES", str(20 * 1024 * 1024))
        )
        maximum_visual_bytes = int(
            env.get(
                "AI_ANALYSIS_OBJECT_STORAGE_MAXIMUM_VISUAL_BYTES",
                str(128 * 1024 * 1024),
            )
        )
        visual_release_root_text = env.get("AI_ANALYSIS_VISUAL_RELEASE_ROOT", "")
        visual_landmark_model_text = env.get(
            "AI_ANALYSIS_VISUAL_LANDMARK_MODEL_PATH", ""
        )
        visual_geometry_text = env.get(
            "AI_ANALYSIS_VISUAL_GEOMETRY_ARTIFACT_PATH", ""
        )
        seungun_startup_timeout = float(
            env.get("AI_ANALYSIS_SEUNGUN_STARTUP_TIMEOUT_SECONDS", "300")
        )
        seungun_request_timeout = float(
            env.get("AI_ANALYSIS_SEUNGUN_REQUEST_TIMEOUT_SECONDS", "180")
        )
        seungun_maximum_requests = int(
            env.get("AI_ANALYSIS_SEUNGUN_MAXIMUM_REQUESTS_PER_PROCESS", "100")
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("analysis_worker_production_configuration_invalid") from None
    if (
        not repository_root.is_absolute()
        or not workspace_root.is_absolute()
        or not bucket
        or not nltk_data_root.is_absolute()
        or device not in {"cpu", "cuda"}
        or len(
            {
                bool(visual_release_root_text),
                bool(visual_landmark_model_text),
                bool(visual_geometry_text),
            }
        )
        != 1
    ):
        raise ValueError("analysis_worker_production_configuration_invalid")

    if http:
        from .deployment_attestation import verify_http_deployment_attestation
        verify_http_deployment_attestation(env)
        authorization = None
    else:
        authorization_settings = HmacAuthorizationSettings.from_environment(env)
        verify_deployment_attestation(env, authorization_key_ids=authorization_settings.keys)
        authorization = HmacAnalysisAuthorizationAdapter(authorization_settings)
    grounded_feedback = load_grounded_feedback(env, repository_root=repository_root)
    object_store = S3CompatibleRestrictedObjectStore(
        create_s3_client_from_environment(env),
        S3ObjectStoreSettings(
            bucket=bucket,
            recordings_prefix=recordings_prefix,
            workspace_root=workspace_root,
            maximum_audio_bytes=maximum_audio_bytes,
            maximum_visual_bytes=maximum_visual_bytes,
        ),
    )
    expected_phone_frontend = SubprocessG2PKExpectedPhoneFrontend(
        nltk_data_root=nltk_data_root,
        repository_root=repository_root,
        # The frozen Seungun CTC vocabulary contains phones only.  Keeping the
        # frontend's display-only ``|`` boundary makes every multi-word prompt
        # fail closed as an unknown expected phone.
        word_boundary_policy="drop",
    )
    detector = PersistentIsolatedSeungunProductionAdapter(
        repository_root=repository_root,
        device=device,
        startup_timeout_seconds=seungun_startup_timeout,
        request_timeout_seconds=seungun_request_timeout,
        maximum_requests_per_process=seungun_maximum_requests,
        start_immediately=True,
    )
    seungun = SeungunBackendAnalysisAdapter(
        decoder=RestrictedFfmpegAudioDecoder(
            ffmpeg_binary=env.get("AI_ANALYSIS_FFMPEG_BINARY") or None
        ),
        expected_phone_frontend=expected_phone_frontend,
        detector=detector,
    )
    visual = None
    if visual_release_root_text:
        visual = ProductionBackendVisualAnalysisAdapter(
            BackendVisualSettings(
                repository_root=repository_root,
                workspace_root=workspace_root / "visual-attempts",
                nltk_data_root=nltk_data_root,
                landmark_model_path=Path(visual_landmark_model_text),
                production_release_root=Path(visual_release_root_text),
                closed_beta_geometry_artifact_path=Path(visual_geometry_text),
                artifact_group_id=int(env["AI_ANALYSIS_ARTIFACT_GID"]),
            ),
            http_admitted=http,
        )
    return BackendAnalysisWorker(
        authorization=authorization,
        object_store=object_store,
        seungun=seungun,
        visual=visual,
        grounded_feedback=grounded_feedback,
        **({"worker_revision": env["VOICE_COACH_BUILD_REVISION"]} if http else {}),
    )


__all__ = ["create_worker"]
