#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.reproducibility.wavlm_cache import CacheValidationError
from voice_coach.reproducibility.wavlm_large_cache_validation import (
    LargeCacheValidationError,
    failure_large_validation,
    safe_large_validation_output_path,
    validate_wavlm_large_feature_cache,
    write_large_json_atomic,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Independently validate the completed canonical WavLM Large cache, "
            "including its full source, gate, runtime, and resource chain."
        )
    )
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--builder", type=Path, required=True)
    parser.add_argument("--base-plus-head-report", type=Path, required=True)
    parser.add_argument("--smoke-review", type=Path, required=True)
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
        output = safe_large_validation_output_path(
            args.workspace_root,
            args.output,
        )
    except LargeCacheValidationError:
        print("configuration error: unsafe Large validation output", file=sys.stderr)
        return 2
    try:
        result = validate_wavlm_large_feature_cache(
            cache_root=args.cache_root,
            report_path=args.report,
            config_path=args.config,
            builder_path=args.builder,
            base_plus_head_report=args.base_plus_head_report,
            smoke_review=args.smoke_review,
            workspace_root=args.workspace_root,
            batch_rows=args.batch_rows,
        )
    except (LargeCacheValidationError, CacheValidationError, OSError, ValueError):
        result = failure_large_validation("large_validation_contract_error")
    except Exception:
        result = failure_large_validation("large_validator_internal_error")
    try:
        write_large_json_atomic(output, result)
    except (LargeCacheValidationError, OSError, ValueError):
        print(
            "configuration error: unsafe atomic Large validation output",
            file=sys.stderr,
        )
        return 2
    summary = {
        "status": result["status"],
        "error_kinds": len(result.get("errors", [])),
    }
    scan = result.get("scan")
    if isinstance(scan, dict):
        summary["rows_checked"] = scan.get("rows_checked", {})
        summary["embedding_vectors_checked"] = scan.get(
            "embedding_vectors_checked",
            {},
        )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    print(f"status={result['status']} report={output}")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
