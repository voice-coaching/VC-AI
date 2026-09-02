#!/usr/bin/env python
"""Create an explicit fail-closed production visual release."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from voice_coach.visual_feedback.adapters.production_visual_release import (  # noqa: E402
    create_disabled_production_visual_release,
)
from voice_coach.visual_feedback.errors import (  # noqa: E402
    VisualFeedbackContractError,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument(
        "--reason-code",
        action="append",
        required=True,
        help="safe reason for disabling visual claims; repeat when needed",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        release = create_disabled_production_visual_release(
            output_directory=arguments.output_directory,
            release_id=arguments.release_id,
            reason_codes=arguments.reason_code,
        )
    except (OSError, VisualFeedbackContractError):
        json.dump(
            {
                "status": "failed_closed",
                "reason_code": "production_visual_release_creation_failed",
            },
            sys.stderr,
            sort_keys=True,
            separators=(",", ":"),
        )
        sys.stderr.write("\n")
        return 2
    json.dump(
        {
            "status": release.status,
            "release_id": release.release_id,
            "release_sha256": release.release_sha256,
            "reason_codes": list(release.reason_codes),
        },
        sys.stdout,
        sort_keys=True,
        separators=(",", ":"),
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
