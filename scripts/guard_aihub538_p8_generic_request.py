#!/usr/bin/env python3
"""Side-effect-free guard for globally reserved AI Hub 538 P8 file keys."""

from __future__ import annotations

import re
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from voice_coach.visual_feedback.models.p8_locked_label_download import (  # noqa: E402
    AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS,
    AIHUB538_P8_VALIDATION_SOURCE_FILE_KEYS,
)


_CANONICAL_DECIMAL = re.compile(r"^[1-9][0-9]*$")
_CANONICAL_FILE_KEYS = re.compile(r"^[1-9][0-9]*(?:,[1-9][0-9]*)*$")
_RESERVED_P8_FILE_KEYS = frozenset(
    AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS
    + AIHUB538_P8_VALIDATION_SOURCE_FILE_KEYS
)


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if len(values) != 2:
        print("dataset_key_and_file_keys_required", file=sys.stderr)
        return 2
    dataset_key, file_keys = values
    if not _CANONICAL_DECIMAL.fullmatch(dataset_key):
        print("dataset_key_invalid", file=sys.stderr)
        return 2
    if not _CANONICAL_FILE_KEYS.fullmatch(file_keys):
        print("file_keys_invalid", file=sys.stderr)
        return 2
    if set(file_keys.split(",")) & _RESERVED_P8_FILE_KEYS:
        print(
            "aihub538_p8_file_key_requires_typed_entrypoint",
            file=sys.stderr,
        )
        return 65
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
