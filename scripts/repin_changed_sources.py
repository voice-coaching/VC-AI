#!/usr/bin/env python3
"""Re-pin every config that names a file whose digest has moved.

A source pin says "this artifact was produced by this file". When a file changes on
purpose, every config that pins it has to be updated together or the next run fails
somewhere far from the change. Doing that by hand across a dozen YAML files is how a
stale pin survives, so this finds them by path and rewrites only the digest that sits
with that path.

It rewrites a digest **only** where the same file states the path immediately above or
beside it, so an unrelated hash that happens to share a value is never touched.

Usage:
    repin_changed_sources.py PATH [PATH ...] [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
#: `- path: X` / `sha256: Y` and `*_path: X` / `*_sha256: Y` are the two shapes the
#: configs use. Both keep the digest within a couple of lines of its path.
_PIN_PATTERNS = (
    r"(?P<head>-\s*path:\s*{path}\s*\n\s*sha256:\s*)(?P<sha>[0-9a-f]{{64}})",
    r"(?P<head>\w*path:\s*{path}\s*\n\s*\w*sha256:\s*)(?P<sha>[0-9a-f]{{64}})",
)


def repin(targets: list[Path], *, dry_run: bool = False) -> list[tuple[Path, str, int]]:
    digests = {
        target.relative_to(ROOT).as_posix(): hashlib.sha256(
            target.read_bytes()
        ).hexdigest()
        for target in targets
    }
    changes: list[tuple[Path, str, int]] = []
    for config in sorted(ROOT.glob("configs/**/*.yaml")):
        text = config.read_text(encoding="utf-8")
        updated = text
        for relative, digest in digests.items():
            for pattern in _PIN_PATTERNS:
                updated = re.sub(
                    pattern.format(path=re.escape(relative)),
                    lambda match, digest=digest: match.group("head") + digest,
                    updated,
                )
        if updated != text:
            count = sum(
                1
                for a, b in zip(text.splitlines(), updated.splitlines())
                if a != b
            )
            changes.append((config, "updated", count))
            if not dry_run:
                config.write_text(updated, encoding="utf-8")
    return changes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    targets = []
    for path in args.paths:
        resolved = path if path.is_absolute() else ROOT / path
        if not resolved.is_file():
            print(f"failed: not a file: {path}", file=sys.stderr)
            return 1
        targets.append(resolved)

    changes = repin(targets, dry_run=args.dry_run)
    for config, action, count in changes:
        print(f"{action} {config.relative_to(ROOT)} ({count} line(s))")
    print(f"{len(changes)} config(s) {'would be ' if args.dry_run else ''}updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
