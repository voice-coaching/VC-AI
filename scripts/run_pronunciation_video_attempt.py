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
import os
from pathlib import Path
import stat
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
from voice_coach.visual_feedback.models.runtime_eligibility import (  # noqa: E402
    load_automated_visual_runtime_eligibility,
    load_visual_runtime_bundle,
)
from voice_coach.visual_feedback.models.runtime_policy_artifact import (  # noqa: E402
    P8RuntimePolicyArtifact,
)


DEFAULT_NLTK_DATA_ROOT = Path.home() / "nltk_data"
LOCAL_TEST_PROMPT_ID = "LOCAL-TEST-RICE-CLEAN-V1"
LOCAL_TEST_PROMPT_TEXT = "쌀을 깨끗이 씻어요"
_MAXIMUM_RUNTIME_ARTIFACT_BYTES = 16 * 1024 * 1024


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


def _read_runtime_artifact(path: Path) -> object:
    """Read one immutable aggregate artifact without following symlinks."""

    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o400
            or before.st_size <= 0
            or before.st_size > _MAXIMUM_RUNTIME_ARTIFACT_BYTES
        ):
            raise ValueError("visual runtime artifact identity differs")
        payload = os.pread(descriptor, before.st_size, 0)
        after = os.fstat(descriptor)
        if len(payload) != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("visual runtime artifact changed while reading")

        def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("visual runtime artifact has duplicate keys")
                result[key] = value
            return result

        return json.loads(
            payload,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    finally:
        os.close(descriptor)


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
    parser.add_argument("--visual-runtime-bundle", type=Path)
    parser.add_argument("--visual-runtime-eligibility", type=Path)
    parser.add_argument("--video-qc-decision-policy", type=Path)
    parser.add_argument("--av-sync-decision-policy", type=Path)
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

    try:
        visual_paths = (
            args.visual_runtime_bundle,
            args.visual_runtime_eligibility,
            args.video_qc_decision_policy,
            args.av_sync_decision_policy,
        )
        if any(path is not None for path in visual_paths) and not all(
            path is not None for path in visual_paths
        ):
            raise ValueError("all visual runtime artifacts are required together")
        visual_bundle = None
        visual_eligibility = None
        qc_decision_policy = None
        sync_decision_policy = None
        if all(path is not None for path in visual_paths):
            visual_bundle = load_visual_runtime_bundle(
                _read_runtime_artifact(args.visual_runtime_bundle)
            )
            visual_eligibility = load_automated_visual_runtime_eligibility(
                _read_runtime_artifact(args.visual_runtime_eligibility),
                bundle=visual_bundle,
            )
            qc_artifact = P8RuntimePolicyArtifact.from_json_object(
                _read_runtime_artifact(args.video_qc_decision_policy)
            )
            sync_artifact = P8RuntimePolicyArtifact.from_json_object(
                _read_runtime_artifact(args.av_sync_decision_policy)
            )
            if (
                qc_artifact.policy_kind != "video_qc_decision"
                or sync_artifact.policy_kind != "av_sync_decision"
            ):
                raise ValueError("visual runtime decision policy kind differs")
            qc_decision_policy = qc_artifact.policy_document.to_runtime_policy()
            sync_decision_policy = sync_artifact.policy_document.to_runtime_policy()
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
                visual_runtime_eligibility=visual_eligibility,
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

    _emit(result)
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
