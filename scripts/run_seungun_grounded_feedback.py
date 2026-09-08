#!/usr/bin/env python
"""Run frozen seungun phone analysis and optional grounded HyperCLOVA feedback."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
for source in (REPOSITORY_ROOT / "src", REPOSITORY_ROOT / "seungun/src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from korean_phone_ctc.frozen_detector import (  # noqa: E402
    FrozenDetectorError,
    FrozenPronunciationDetector,
)
from voice_coach.pronunciation_feedback.adapters.advice_generator_hyperclova import (  # noqa: E402
    DEFAULT_MODEL_ID,
    LocalHyperClovaAdviceGenerator,
)
from voice_coach.pronunciation_feedback.coaching_payload import (  # noqa: E402
    CoachingPayloadError,
)
from voice_coach.pronunciation_feedback.seungun_bridge import (  # noqa: E402
    generate_grounded_phone_feedback,
)


def _expected_phones(value: str) -> tuple[str, ...]:
    document = json.loads(value)
    if not isinstance(document, list) or not document or not all(
        isinstance(phone, str) and phone for phone in document
    ):
        raise argparse.ArgumentTypeError("expected phones must be a non-empty JSON array")
    return tuple(document)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--target-text", required=True)
    parser.add_argument("--expected-phones-json", type=_expected_phones, required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--advice-endpoint")
    parser.add_argument("--advice-revision")
    parser.add_argument("--advice-model-id", default=DEFAULT_MODEL_ID)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if bool(args.advice_endpoint) != bool(args.advice_revision):
        print(
            json.dumps(
                {
                    "status": "failed",
                    "reason": "advice endpoint and revision must be supplied together",
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    generator = None
    try:
        if args.advice_endpoint:
            generator = LocalHyperClovaAdviceGenerator(
                endpoint=args.advice_endpoint,
                model_id=args.advice_model_id,
                revision=args.advice_revision,
            )
        detector = FrozenPronunciationDetector(
            repository_root=REPOSITORY_ROOT, device=args.device
        )
        analysis = detector.analyze(
            audio_path=args.audio, expected_phones=args.expected_phones_json
        )
        feedback = generate_grounded_phone_feedback(
            analysis=analysis.to_json_object(),
            target_text=args.target_text,
            generator=generator,
        )
    except (FrozenDetectorError, CoachingPayloadError) as error:
        print(
            json.dumps(
                {"status": "failed", "reason": type(error).__name__},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    result = {
        "status": "completed",
        "analysis": analysis.to_json_object(),
        "feedback": asdict(feedback),
    }
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
