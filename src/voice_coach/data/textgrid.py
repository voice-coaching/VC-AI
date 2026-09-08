"""Small dependency-free parser for Praat long-text TextGrid files."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re

from .intervals import Interval


_ITEM_RE = re.compile(r"^\s*item\s*\[\d+\]\s*:\s*$")
_INTERVAL_RE = re.compile(r"^\s*intervals\s*\[\d+\]\s*:\s*$")
_ASSIGNMENT_RE = re.compile(r'^\s*(\w+)\s*=\s*(.*?)\s*$')


def _praat_string(value: str) -> str:
    value = value.strip()
    if len(value) < 2 or not (value.startswith('"') and value.endswith('"')):
        raise ValueError(f"expected quoted Praat string, got {value!r}")
    return value[1:-1].replace('""', '"')


def parse_textgrid(
    source: str | Path,
    *,
    encoding: str = "utf-8-sig",
) -> dict[str, list[Interval]]:
    """Return interval tiers keyed by tier name.

    ``source`` may be TextGrid text or a path. Point tiers are intentionally
    ignored because the scorer consumes only interval evidence.
    """

    if isinstance(source, Path):
        text = source.read_text(encoding=encoding)
    else:
        candidate = Path(source)
        text = (
            candidate.read_text(encoding=encoding)
            if "\n" not in source and candidate.is_file()
            else source
        )

    tiers: dict[str, list[Interval]] = defaultdict(list)
    tier_name: str | None = None
    interval_values: dict[str, str] | None = None

    def flush_interval() -> None:
        nonlocal interval_values
        if interval_values is None:
            return
        if tier_name is None:
            raise ValueError("TextGrid interval appeared before a tier name")
        missing = {"xmin", "xmax", "text"} - interval_values.keys()
        if missing:
            raise ValueError(
                f"incomplete interval in tier {tier_name!r}: missing {sorted(missing)}"
            )
        tiers[tier_name].append(
            Interval(
                start=float(interval_values["xmin"]),
                end=float(interval_values["xmax"]),
                label=_praat_string(interval_values["text"]),
            )
        )
        interval_values = None

    for line in text.splitlines():
        if _ITEM_RE.match(line):
            flush_interval()
            tier_name = None
            continue
        if _INTERVAL_RE.match(line):
            flush_interval()
            interval_values = {}
            continue
        match = _ASSIGNMENT_RE.match(line)
        if match is None:
            continue
        key, value = match.groups()
        if interval_values is not None and key in {"xmin", "xmax", "text"}:
            interval_values[key] = value
        elif interval_values is None and key == "name":
            tier_name = _praat_string(value)

    flush_interval()
    return dict(tiers)
