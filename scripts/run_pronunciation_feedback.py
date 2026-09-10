#!/usr/bin/env python
"""Run the prompt -> audio -> feedback pipeline from the command line.

Two commands:

    prompts   list the practice sentences the pipeline may present
    attempt   score one recording of one prompt

The entrypoint only parses arguments, builds the pipeline, and serializes the
result.  It makes no judgement of its own and never retries a failed gate.

Examples::

    python scripts/run_pronunciation_feedback.py prompts --difficulty beginner
    python scripts/run_pronunciation_feedback.py attempt \
        --prompt-id R00-01 --audio /restricted/tmp/attempt.wav
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from voice_coach.pronunciation_feedback import (  # noqa: E402
    UNCALIBRATED_PROFILE,
    AttemptStatus,
    PronunciationFeedbackError,
    build_default_pipeline,
)
from voice_coach.pronunciation_feedback.adapters.advice_generator_hyperclova import (  # noqa: E402
    DEFAULT_MODEL_ID,
    DEFAULT_TIMEOUT_SECONDS,
    LocalHyperClovaAdviceGenerator,
)
from voice_coach.pronunciation_feedback.coaching_payload import (  # noqa: E402
    CoachingPayloadError,
)

#: g2pK is installed in its own virtualenv and reads its nltk corpora from the
#: user data root the smoke scripts already populate.  Defaulting to it keeps a
#: calibrated profile runnable without a flag, while leaving the flag as the way
#: to point at a different pinned root.
DEFAULT_NLTK_DATA_ROOT = Path.home() / "nltk_data"

_SUCCESS_STATUSES = frozenset(
    {
        AttemptStatus.COACHING_READY,
        AttemptStatus.COMPLETED_NO_ISSUE,
        AttemptStatus.RERECORD_REQUIRED,
        AttemptStatus.UNCERTAIN,
    }
)


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, Path):
        return value.name
    if hasattr(value, "value") and type(value).__mro__[1].__name__ == "str":
        return str(value.value)
    return value


def _emit(payload: Any) -> None:
    json.dump(_to_jsonable(payload), sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _underline(text: str, span: Any) -> str:
    """Mark the coached syllable in the prompt with the span's own offsets.

    Uses ``char_start``/``char_end`` rather than searching for the syllable
    text: a sentence can repeat a syllable, and highlighting the first
    occurrence would point the learner at a syllable that was not the finding.
    """

    start, end = span["char_start"], span["char_end"]
    return f"{text[:start]}[{text[start:end]}]{text[end:]}"


def _render_text(result: Any) -> str:
    """The learner-facing view.  Adds nothing the pipeline did not decide."""

    payload = _to_jsonable(result)
    feedback = payload.get("learner_feedback")
    lines = [
        f"판정      : {payload['status']}",
        f"다음 행동 : {(feedback or {}).get('next_action', 'none')}",
    ]
    if payload.get("failure_code"):
        lines.append(f"사유      : {payload['failure_code']} — {payload['reason']}")
    if feedback is None:
        return "\n".join(lines)

    lines.append(f"문장      : {feedback['display_text']}")
    if feedback["advice"]:
        lines.append("")
        lines.append("코칭:")
        for item in feedback["advice"]:
            span = item.get("span")
            located = (
                _underline(feedback["display_text"], span)
                if span
                else "(위치 미확정)"
            )
            lines.append(f"  · {located}")
            if span:
                lines.append(f"    구간 {span['start_ms']}–{span['end_ms']}ms")
            lines.append(f"    {item['message']}")
            lines.append(
                f"    [{item['advice_id']}] "
                f"심각도 {item['severity']:.2f} / 확신 {item['confidence']:.2f}"
            )
    if feedback["unlocated_issue_types"]:
        lines.append(
            "  위치를 확정하지 못한 항목: "
            + ", ".join(feedback["unlocated_issue_types"])
        )

    lines.append("")
    lines.append("측정 축:")
    for aspect in feedback["aspects"]:
        score = aspect["score"]
        shown = "—" if score is None else f"{score:.3f}"
        lines.append(f"  {aspect['name']:<10s} {shown:>6s}  ({aspect['state']})")

    if feedback["reference_audio"]:
        lines.append("")
        lines.append(f"기준 음성 {len(feedback['reference_audio'])}건 제공")
    lines.append("")
    lines.append("고지:")
    lines.extend(f"  - {notice}" for notice in feedback["notices"])
    return "\n".join(lines)


def _add_pipeline_options(
    parser: argparse.ArgumentParser, *, suppress: bool
) -> None:
    """Options that configure the pipeline rather than one command.

    Added to the top-level parser *and* to each subcommand.  Argparse would
    otherwise reject ``attempt --profile x``, which is the order anyone types
    first; on the subcommand copies the defaults are suppressed so a flag given
    before the subcommand is not overwritten by the copy's default.
    """

    default = argparse.SUPPRESS if suppress else None
    parser.add_argument("--card-path", type=Path, default=default)
    parser.add_argument("--threshold-dir", type=Path, default=default)
    parser.add_argument(
        "--profile",
        default=argparse.SUPPRESS if suppress else UNCALIBRATED_PROFILE,
        help="analysis profile naming a calibration artifact to load",
    )
    parser.add_argument(
        "--nltk-data-root",
        type=Path,
        default=default,
        help=(
            "enable the pinned offline g2pK expected-phone frontend; "
            f"defaults to {DEFAULT_NLTK_DATA_ROOT} when that directory exists"
        ),
    )
    parser.add_argument(
        "--device",
        default=argparse.SUPPRESS if suppress else "cpu",
        help="torch device for the analysis backends",
    )
    parser.add_argument(
        "--no-analysis-backends",
        action="store_true",
        default=argparse.SUPPRESS if suppress else False,
        help="leave the analysis ports unconnected so scoring fails closed",
    )
    parser.add_argument(
        "--advice-endpoint",
        default=default,
        help=(
            "optional loopback OpenAI-compatible HyperCLOVA endpoint, for "
            "example http://127.0.0.1:8000/v1"
        ),
    )
    parser.add_argument(
        "--advice-model-id",
        default=argparse.SUPPRESS if suppress else DEFAULT_MODEL_ID,
        help="model id served by --advice-endpoint",
    )
    parser.add_argument(
        "--advice-revision",
        default=default,
        help="required immutable model revision when --advice-endpoint is set",
    )
    parser.add_argument(
        "--advice-timeout-seconds",
        type=float,
        default=argparse.SUPPRESS if suppress else DEFAULT_TIMEOUT_SECONDS,
        help="single local generation timeout; the pipeline does not retry",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Korean pronunciation feedback pipeline (research use)",
    )
    _add_pipeline_options(parser, suppress=False)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prompts = subparsers.add_parser("prompts", help="list practice prompts")
    _add_pipeline_options(prompts, suppress=True)
    prompts.add_argument("--difficulty", default=None)
    prompts.add_argument("--target", default=None)
    prompts.add_argument("--limit", type=int, default=None)

    attempt = subparsers.add_parser("attempt", help="score one recording")
    _add_pipeline_options(attempt, suppress=True)
    attempt.add_argument("--prompt-id", required=True)
    attempt.add_argument("--audio", type=Path, required=True)
    attempt.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="json is the full result with receipts; text is the learner view",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    nltk_data_root = args.nltk_data_root
    if nltk_data_root is None and DEFAULT_NLTK_DATA_ROOT.is_dir():
        nltk_data_root = DEFAULT_NLTK_DATA_ROOT
    if bool(args.advice_endpoint) != bool(args.advice_revision):
        _emit(
            {
                "error": "advice_generator_invalid",
                "detail": (
                    "--advice-endpoint and --advice-revision must be supplied together"
                ),
            }
        )
        return 2
    advice_generator = None
    if args.advice_endpoint is not None:
        try:
            advice_generator = LocalHyperClovaAdviceGenerator(
                endpoint=args.advice_endpoint,
                model_id=args.advice_model_id,
                revision=args.advice_revision,
                timeout_seconds=args.advice_timeout_seconds,
            )
        except CoachingPayloadError as exc:
            _emit({"error": "advice_generator_invalid", "detail": str(exc)})
            return 2
    try:
        pipeline = build_default_pipeline(
            card_path=args.card_path,
            threshold_dir=args.threshold_dir,
            analysis_profile=args.profile,
            nltk_data_root=nltk_data_root,
            repository_root=REPO_ROOT,
            connect_analysis_backends=not args.no_analysis_backends,
            advice_generator=advice_generator,
            device=args.device,
        )
    except PronunciationFeedbackError as exc:
        _emit({"error": exc.code.value, "detail": exc.detail})
        return 2

    if args.command == "prompts":
        try:
            prompts = pipeline.list_prompts(
                difficulty=args.difficulty, target=args.target, limit=args.limit
            )
        except PronunciationFeedbackError as exc:
            _emit({"error": exc.code.value, "detail": exc.detail})
            return 2
        _emit(
            {
                "promptCatalogRevision": prompts[0].catalog_revision,
                "prompts": [
                    {
                        "promptId": prompt.prompt_id,
                        "displayText": prompt.display_text,
                        "difficulty": prompt.difficulty,
                        "syllableCount": prompt.syllable_count,
                        "wordCount": prompt.word_count,
                        "practiceTargets": list(prompt.practice_targets),
                        "licenseSpdx": prompt.license_spdx,
                    }
                    for prompt in prompts
                ],
            }
        )
        return 0

    result = pipeline.run_attempt(prompt_id=args.prompt_id, audio_path=args.audio)
    if args.format == "text":
        sys.stdout.write(_render_text(result) + "\n")
    else:
        _emit(result)
    return 0 if result.status in _SUCCESS_STATUSES else 3


if __name__ == "__main__":
    raise SystemExit(main())
