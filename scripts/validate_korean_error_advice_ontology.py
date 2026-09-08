#!/usr/bin/env python3
"""Validate the C01 Korean error/advice ontology from aggregate evidence only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from voice_coach.coaching.ontology import (
    OntologyValidationError,
    sha256_file,
    validate_ontology_bundle,
    write_immutable_report,
)


DEFAULT_CATALOG = "configs/coaching/korean_error_advice_ontology_v1.json"
DEFAULT_SCHEMA = "configs/coaching/korean_error_advice_ontology_v1.schema.json"
DEFAULT_REPORT = (
    "artifacts/coaching/c01_korean_error_advice_ontology_v1.validation.json"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    parser.add_argument("--catalog", default=DEFAULT_CATALOG)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate and print without creating the immutable report",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = validate_ontology_bundle(
            workspace_root=args.workspace_root,
            catalog_relative_path=args.catalog,
            schema_relative_path=args.schema,
        )
        output_path: Path | None = None
        if not args.validate_only:
            output_path = write_immutable_report(
                workspace_root=args.workspace_root,
                output_relative_path=args.report,
                report=report,
            )
    except (OntologyValidationError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "invalid",
                    "error": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1

    response = dict(report)
    if output_path is not None:
        response["report"] = {
            "path": args.report,
            "sha256": sha256_file(output_path),
        }
    print(json.dumps(response, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
