"""Reviewed production composition for the private VC-BE analysis worker."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from voice_coach.pronunciation_attempts.adapters.runtime import (
    IsolatedFrozenSeungunDetectorAdapter,
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
from .service import BackendAnalysisWorker


def create_worker(
    environment: Mapping[str, str] | None = None,
) -> BackendAnalysisWorker:
    """Compose signed authorization, private S3, g2pK, and frozen Seungun."""

    env = os.environ if environment is None else environment
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
    except (KeyError, TypeError, ValueError):
        raise ValueError("analysis_worker_production_configuration_invalid") from None
    if (
        not repository_root.is_absolute()
        or not workspace_root.is_absolute()
        or not bucket
        or not nltk_data_root.is_absolute()
        or device not in {"cpu", "cuda"}
    ):
        raise ValueError("analysis_worker_production_configuration_invalid")

    authorization = HmacAnalysisAuthorizationAdapter(
        HmacAuthorizationSettings.from_environment(env)
    )
    object_store = S3CompatibleRestrictedObjectStore(
        create_s3_client_from_environment(env),
        S3ObjectStoreSettings(
            bucket=bucket,
            recordings_prefix=recordings_prefix,
            workspace_root=workspace_root,
            maximum_audio_bytes=maximum_audio_bytes,
        ),
    )
    expected_phone_frontend = SubprocessG2PKExpectedPhoneFrontend(
        nltk_data_root=nltk_data_root,
        repository_root=repository_root,
    )
    detector = IsolatedFrozenSeungunDetectorAdapter(
        repository_root=repository_root,
        device=device,
    )
    seungun = SeungunBackendAnalysisAdapter(
        decoder=RestrictedFfmpegAudioDecoder(
            ffmpeg_binary=env.get("AI_ANALYSIS_FFMPEG_BINARY") or None
        ),
        expected_phone_frontend=expected_phone_frontend,
        detector=detector,
    )
    return BackendAnalysisWorker(
        authorization=authorization,
        object_store=object_store,
        seungun=seungun,
    )


__all__ = ["create_worker"]
