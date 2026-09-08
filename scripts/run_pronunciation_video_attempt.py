#!/usr/bin/env python
"""Run one local video -> Seungun pronunciation-feedback attempt.

The input contract is a single H.264-or-HEVC/AAC MP4.  The admitted copy and
decoded PCM are kept in a mode-restricted temporary workspace and deleted
after the attempt.  The caller's original file is never deleted.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from enum import Enum
import json
from pathlib import Path
import sys
import tempfile
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from voice_coach.pronunciation_attempts.adapters.runtime import (  # noqa: E402
    IsolatedFrozenSeungunDetectorAdapter,
)
from voice_coach.pronunciation_attempts.composition import (  # noqa: E402
    LOCAL_CONSENT_REFERENCE,
    build_local_video_attempt_facade,
)
from voice_coach.pronunciation_attempts.services.video_attempt_facade import (  # noqa: E402
    VideoAttemptCommand,
    VideoAttemptStatus,
)
from voice_coach.pronunciation_feedback.adapters.advice_generator_hyperclova import (  # noqa: E402
    DEFAULT_MODEL_ID as DEFAULT_ADVICE_MODEL_ID,
    LocalHyperClovaAdviceGenerator,
)
from voice_coach.pronunciation_feedback.video_grounded_advice import (  # noqa: E402
    generate_grounded_video_feedback,
)
from voice_coach.visual_feedback.adapters.production_visual_release import (  # noqa: E402
    classify_production_visual_release_error,
    load_production_visual_release,
)
from voice_coach.visual_feedback.adapters.p6_geometry_reload import (  # noqa: E402
    P6GeometryReloadError,
    load_p6_geometry_artifact,
)
from voice_coach.visual_feedback.errors import VisualFeedbackContractError  # noqa: E402


DEFAULT_NLTK_DATA_ROOT = Path.home() / "nltk_data"
LOCAL_TEST_PROMPT_ID = "LOCAL-TEST-RICE-CLEAN-V1"
LOCAL_TEST_PROMPT_TEXT = "쌀을 깨끗이 씻어요"


def _source_file_descriptor(value: str) -> int:
    try:
        descriptor = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "video fd must be an already-open descriptor >= 3"
        ) from None
    if descriptor < 3:
        raise argparse.ArgumentTypeError(
            "video fd must be an already-open descriptor >= 3"
        )
    return descriptor


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return value.name
    return value


def _emit(value: object) -> None:
    json.dump(_jsonable(value), sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Internal local video pronunciation-feedback loop",
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--video",
        type=Path,
        help=(
            "local MP4 path; for a participant-sensitive locator prefer "
            "--video-fd so the path is not placed in process arguments"
        ),
    )
    source_group.add_argument(
        "--video-fd",
        type=_source_file_descriptor,
        help="already-open read-only regular-file descriptor >= 3",
    )
    parser.add_argument("--prompt-id", required=True)
    parser.add_argument(
        "--consent-local-processing",
        action="store_true",
        help=(
            "attest that local facial-video processing was consented before "
            "ingestion and confirm deletion of the restricted working copy"
        ),
    )
    parser.add_argument(
        "--landmark-model",
        type=Path,
        help="optional pinned MediaPipe face_landmarker.task",
    )
    parser.add_argument("--nltk-data-root", type=Path, default=DEFAULT_NLTK_DATA_ROOT)
    parser.add_argument("--card-path", type=Path)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--advice-endpoint")
    parser.add_argument("--advice-revision")
    parser.add_argument("--advice-model-id", default=DEFAULT_ADVICE_MODEL_ID)
    parser.add_argument(
        "--production-visual-release",
        type=Path,
        help=(
            "private immutable production visual release directory; individual "
            "runtime artifacts are not accepted by this entrypoint"
        ),
    )
    parser.add_argument(
        "--closed-beta-lip-geometry-artifact",
        type=Path,
        help=(
            "private immutable P6 geometry artifact; enables aggregate-only "
            "40-point lip observations alongside an enabled production release"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.consent_local_processing:
        _emit(
            {
                "status": "failed_closed",
                "reason_codes": ["explicit_local_processing_consent_required"],
            }
        )
        return 2
    if bool(args.advice_endpoint) != bool(args.advice_revision):
        _emit(
            {
                "status": "failed_closed",
                "reason_codes": ["advice_generator_configuration_invalid"],
            }
        )
        return 2

    try:
        advice_generator = (
            LocalHyperClovaAdviceGenerator(
                endpoint=args.advice_endpoint,
                model_id=args.advice_model_id,
                revision=args.advice_revision,
            )
            if args.advice_endpoint is not None
            else None
        )
        visual_bundle = None
        qc_decision_policy = None
        sync_decision_policy = None
        visual_release_id = None
        visual_release_status = "not_configured"
        visual_release_reason_codes: tuple[str, ...] = ()
        closed_beta_geometry_artifact = None
        closed_beta_status = "not_configured"
        closed_beta_reason_codes: tuple[str, ...] = ()
        if args.production_visual_release is not None:
            try:
                release = load_production_visual_release(
                    args.production_visual_release
                )
            except (OSError, VisualFeedbackContractError) as error:
                visual_release_status = "failed_closed"
                visual_release_reason_codes = (
                    classify_production_visual_release_error(error),
                )
            else:
                visual_release_id = release.release_id
                visual_release_status = release.status
                visual_release_reason_codes = release.reason_codes
                if release.status == "enabled":
                    visual_bundle = release.runtime_bundle
                    qc_decision_policy = release.video_qc_decision_policy
                    sync_decision_policy = release.av_sync_decision_policy
        if args.closed_beta_lip_geometry_artifact is not None:
            if visual_bundle is None or args.landmark_model is None:
                closed_beta_status = "failed_closed"
                closed_beta_reason_codes = (
                    "closed_beta_production_dependencies_unavailable",
                )
            else:
                try:
                    closed_beta_geometry_artifact = load_p6_geometry_artifact(
                        args.closed_beta_lip_geometry_artifact,
                        repository_root=REPOSITORY_ROOT,
                    )
                except (OSError, P6GeometryReloadError):
                    closed_beta_status = "failed_closed"
                    closed_beta_reason_codes = (
                        "closed_beta_geometry_artifact_invalid",
                    )
                else:
                    if (
                        closed_beta_geometry_artifact.model_sha256
                        != visual_bundle.pipeline_identity.landmark_model_sha256
                    ):
                        closed_beta_geometry_artifact = None
                        closed_beta_status = "failed_closed"
                        closed_beta_reason_codes = (
                            "closed_beta_geometry_model_identity_mismatch",
                        )
                    else:
                        closed_beta_status = "enabled"
        video_locator = (
            str(args.video)
            if args.video is not None
            else f"fd:{args.video_fd}"
        )
        detector = IsolatedFrozenSeungunDetectorAdapter(
            repository_root=REPOSITORY_ROOT,
            device=args.device,
        )
        with tempfile.TemporaryDirectory(
            prefix="voice-coach-video-attempt-"
        ) as temporary_root_text:
            temporary_root = Path(temporary_root_text)
            facade = build_local_video_attempt_facade(
                repository_root=REPOSITORY_ROOT,
                restricted_media_root=temporary_root / "media",
                restricted_audio_root=temporary_root / "audio",
                nltk_data_root=args.nltk_data_root,
                seungun_detector=detector,
                landmark_model_path=args.landmark_model,
                card_path=args.card_path,
                fixed_local_prompt=(
                    (LOCAL_TEST_PROMPT_ID, LOCAL_TEST_PROMPT_TEXT)
                    if args.prompt_id == LOCAL_TEST_PROMPT_ID
                    else None
                ),
                video_qc_decision_policy=qc_decision_policy,
                av_sync_decision_policy=sync_decision_policy,
                visual_runtime_bundle=visual_bundle,
                closed_beta_lip_geometry_artifact=(
                    closed_beta_geometry_artifact
                ),
            )
            result = facade.run(
                VideoAttemptCommand(
                    prompt_id=args.prompt_id,
                    video_locator=video_locator,
                    consent_reference=LOCAL_CONSENT_REFERENCE,
                )
            )
    except Exception:
        _emit(
            {
                "status": "failed_closed",
                "reason_codes": ["video_attempt_configuration_failed"],
            }
        )
        return 2

    response = _jsonable(result)
    assert isinstance(response, dict)
    response["production_visual_release"] = {
        "status": visual_release_status,
        "release_id": visual_release_id,
        "reason_codes": list(visual_release_reason_codes),
    }
    supplement_notices = (
        ()
        if result.visual_supplement is None
        else tuple(result.visual_supplement.get("notices", ()))
    )
    closed_beta_attempt_status = "not_run"
    if "closed_beta_lip_trajectory_observed" in supplement_notices:
        closed_beta_attempt_status = "observed"
    elif "closed_beta_lip_trajectory_unavailable" in supplement_notices:
        closed_beta_attempt_status = "unavailable"
    response["closed_beta_lip_trajectory"] = {
        "status": closed_beta_status,
        "attempt_status": closed_beta_attempt_status,
        "geometry_artifact_sha256": (
            None
            if closed_beta_geometry_artifact is None
            else closed_beta_geometry_artifact.artifact_sha256
        ),
        "reason_codes": list(closed_beta_reason_codes),
        "contains_pronunciation_truth": False,
        "contains_action_truth": False,
    }
    response["combined_feedback"] = None
    if result.audio_result is not None and result.audio_result.get("status") == (
        "coaching_ready"
    ):
        response["combined_feedback"] = _jsonable(
            generate_grounded_video_feedback(
                audio_result=result.audio_result,
                visual_supplement=result.visual_supplement,
                generator=advice_generator,
                visual_failure_reason_codes=(
                    visual_release_reason_codes
                    or (
                        (result.visual_stage_status.value, *result.reason_codes)
                        if visual_release_status == "enabled"
                        and result.status
                        is VideoAttemptStatus.AUDIO_COMPLETED_VISUAL_UNAVAILABLE
                        else ()
                    )
                ),
            )
        )
    _emit(response)
    return (
        0
        if result.status
        in {
            VideoAttemptStatus.AUDIO_COMPLETED_VISUAL_UNAVAILABLE,
            VideoAttemptStatus.AUDIO_COMPLETED_VISUAL_SUPPLEMENTED,
        }
        else 3
    )


if __name__ == "__main__":
    raise SystemExit(main())
