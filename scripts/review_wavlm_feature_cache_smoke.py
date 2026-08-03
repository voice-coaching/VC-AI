#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.reproducibility.wavlm_cache import (
    CacheValidationError,
    write_json_atomic,
)
from voice_coach.reproducibility.wavlm_smoke_review import (
    SmokeReviewError,
    UnsafeSmokeReviewPath,
    failure_review,
    review_wavlm_feature_cache_smoke,
    safe_review_output_path,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Independently review an exact 100-utterance Base+ or Large "
            "frozen-WavLM smoke cache and atomically publish an aggregate gate."
        )
    )
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--builder", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--batch-rows", type=int, default=2_048)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        output = safe_review_output_path(args.workspace_root, args.output)
    except UnsafeSmokeReviewPath:
        print("configuration error: unsafe review output", file=sys.stderr)
        return 2
    try:
        review = review_wavlm_feature_cache_smoke(
            cache_root=args.cache_root,
            report_path=args.report,
            config_path=args.config,
            builder_path=args.builder,
            output_path=output,
            workspace_root=args.workspace_root,
            batch_rows=args.batch_rows,
        )
    except UnsafeSmokeReviewPath:
        print("configuration error: unsafe reviewer path", file=sys.stderr)
        return 2
    except (SmokeReviewError, CacheValidationError, OSError, ValueError):
        review = failure_review({"review_input_contract_error": 1})
    except Exception:
        review = failure_review({"reviewer_internal_error": 1})
    write_json_atomic(output, review)
    approved = review.get("status") == "approved"
    print(
        json.dumps(
            {
                "status": review["status"],
                "approved": approved,
                "error_kinds": len(review.get("errors", [])),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    print(f"status={review['status']} report={output}")
    return 0 if approved else 1


if __name__ == "__main__":
    raise SystemExit(main())
