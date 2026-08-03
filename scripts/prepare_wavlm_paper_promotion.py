#!/usr/bin/env python3
"""Prepare a validated, review-only WavLM S103 paper promotion bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.reproducibility.wavlm_paper_promotion import (
    PaperPromotionError,
    prepare_wavlm_paper_promotion,
)
from voice_coach.reproducibility.wavlm_results import ResultsValidationError


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate immutable Base+/Large, S103, and results-table commits, "
            "then publish a review-only manuscript/evidence candidate bundle. "
            "Canonical paper files are never edited."
        )
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("docs/paper/wavlm-s103-paper-promotion.template.yaml"),
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=REPOSITORY_ROOT,
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("artifacts/reproducibility/wavlm_s103_paper_promotion"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = prepare_wavlm_paper_promotion(
            manifest_path=args.manifest,
            workspace_root=args.workspace_root,
            output_root=args.output_root,
        )
    except (PaperPromotionError, ResultsValidationError) as exc:
        code = exc.code
        print(f"paper promotion blocked: {code}", file=sys.stderr)
        return 1
    except OSError:
        print("paper promotion publication failed", file=sys.stderr)
        return 2
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
