#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.reproducibility.wavlm_cache import (
    CacheValidationError,
    validate_wavlm_feature_cache,
    write_json_atomic,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a completed full frozen-WavLM feature cache without "
            "reading the source modeling manifest or raw data."
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


def _safe_output(root: Path, configured: Path) -> Path:
    output = configured if configured.is_absolute() else root / configured
    output = output.resolve()
    try:
        relative = output.relative_to(root)
    except ValueError as exc:
        raise CacheValidationError("output must stay inside workspace") from exc
    lowered = [part.lower() for part in relative.parts]
    if any(
        part in {"raw", "restricted", ".env"} or part.startswith(".env")
        for part in lowered
    ):
        raise CacheValidationError("output is forbidden by privacy policy")
    return output


def main() -> int:
    args = parse_args()
    root = args.workspace_root.resolve()
    try:
        output = _safe_output(root, args.output)
    except CacheValidationError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    try:
        result = validate_wavlm_feature_cache(
            cache_root=args.cache_root,
            report_path=args.report,
            config_path=args.config,
            builder_path=args.builder,
            workspace_root=root,
            batch_rows=args.batch_rows,
        )
    except CacheValidationError as exc:
        result = {
            "schema_version": "voice_coach.wavlm_feature_cache_validation.v1",
            "status": "failed",
            "privacy": {
                "raw_data_read": False,
                "source_modeling_manifest_read": False,
                "environment_files_read": False,
                "aggregate_only_output": True,
            },
            "errors": [{"code": "validation_contract_error", "count": 1}],
        }
        write_json_atomic(output, result)
        print(f"validation error: {exc}", file=sys.stderr)
        print(f"status=failed report={output}")
        return 1
    write_json_atomic(output, result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "rows_checked": result["scan"]["rows_checked"],
                "embedding_vectors_checked": result["scan"][
                    "embedding_vectors_checked"
                ],
                "error_kinds": len(result["errors"]),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    print(f"status={result['status']} report={output}")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
