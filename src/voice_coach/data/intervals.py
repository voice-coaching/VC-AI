"""Time-interval contracts and target-to-realized phone alignment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
import unicodedata


def _build_hangul_jamo_normalization_table() -> dict[str, str]:
    """Map Hangul conjoining Choseong/Jungseong/Jongseong (U+1100-11FF) to
    their Hangul Compatibility Jamo (U+3131-318E) equivalents.

    Derived from ``unicodedata`` names rather than a hand-copied character
    table: a conjoining letter (e.g. "HANGUL JONGSEONG KIYEOK") and its
    compatibility form ("HANGUL LETTER KIYEOK") share a name suffix, so the
    mapping is self-verifying at import time instead of trusting a
    transcribed Unicode chart.
    """

    compat_by_suffix: dict[str, str] = {}
    for codepoint in range(0x3131, 0x318F):
        name = unicodedata.name(chr(codepoint), "")
        prefix = "HANGUL LETTER "
        if name.startswith(prefix):
            compat_by_suffix[name[len(prefix) :]] = chr(codepoint)

    table: dict[str, str] = {}
    for prefix, codepoint_range in (
        ("HANGUL CHOSEONG ", range(0x1100, 0x1113)),
        ("HANGUL JUNGSEONG ", range(0x1161, 0x1176)),
        ("HANGUL JONGSEONG ", range(0x11A8, 0x11C3)),
    ):
        for codepoint in codepoint_range:
            name = unicodedata.name(chr(codepoint), "")
            if not name.startswith(prefix):
                continue
            suffix = name[len(prefix) :]
            compat = compat_by_suffix.get(suffix)
            if compat is not None:
                table[chr(codepoint)] = compat
    return table


_HANGUL_JAMO_NORMALIZATION_TABLE = _build_hangul_jamo_normalization_table()


def normalize_phone_label(label: str) -> str:
    """Fold conjoining Hangul jamo to compatibility jamo for comparison.

    AI Hub target JSON and TextGrid realized labels are expected to use
    Hangul Compatibility Jamo throughout, but a source label occasionally
    uses the conjoining Choseong/Jungseong/Jongseong form of the same
    phoneme (e.g. an audit reviewer flagged "target ᄊ / realized ᄉ" where
    both were meant to denote the same plain/tense phoneme distinction as
    the compatibility set). Comparing raw strings then misclassifies a
    same-phoneme pair as a substitution. This does not change what a
    reviewer sees; it only normalizes the equality check used to decide
    match vs. substitution.
    """

    return "".join(
        _HANGUL_JAMO_NORMALIZATION_TABLE.get(character, character)
        for character in label
    )


@dataclass(frozen=True, slots=True)
class Interval:
    start: float
    end: float
    label: str

    def __post_init__(self) -> None:
        if self.start < 0:
            raise ValueError("interval start must be non-negative")
        if self.end < self.start:
            raise ValueError("interval end must not precede start")

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class Alignment:
    """One aligned pair; either side is absent for deletion/insertion."""

    target: Interval | None
    realized: Interval | None
    overlap_seconds: float
    overlap_ratio: float
    operation: str


def _overlap(left: Interval, right: Interval, tolerance: float) -> float:
    return max(
        0.0,
        min(left.end + tolerance, right.end) - max(left.start - tolerance, right.start),
    )


def _ratio(left: Interval, right: Interval, overlap: float) -> float:
    denominator = min(left.duration, right.duration)
    if denominator <= 0:
        return float(left.start == right.start)
    return min(1.0, overlap / denominator)


def align_intervals(
    targets: Iterable[Interval],
    realized: Iterable[Interval],
    *,
    tolerance_seconds: float = 0.03,
    min_overlap_ratio: float = 0.5,
    preserve_one_to_many: bool = True,
) -> list[Alignment]:
    """Align ordered target and realized intervals by temporal overlap.

    Blank intervals are ignored. With ``preserve_one_to_many`` every qualifying
    overlap is retained; otherwise only the strongest unused realized interval
    is selected for each target.
    """

    if tolerance_seconds < 0:
        raise ValueError("tolerance_seconds must be non-negative")
    if not 0 <= min_overlap_ratio <= 1:
        raise ValueError("min_overlap_ratio must be between 0 and 1")

    target_items = sorted(
        (item for item in targets if item.label.strip()),
        key=lambda item: (item.start, item.end, item.label),
    )
    realized_items = sorted(
        (item for item in realized if item.label.strip()),
        key=lambda item: (item.start, item.end, item.label),
    )
    candidates: dict[int, list[tuple[int, float, float]]] = {}

    for target_index, target in enumerate(target_items):
        matches: list[tuple[int, float, float]] = []
        for realized_index, observed in enumerate(realized_items):
            if observed.start > target.end + tolerance_seconds:
                break
            if observed.end < target.start - tolerance_seconds:
                continue
            overlap = _overlap(target, observed, tolerance_seconds)
            ratio = _ratio(target, observed, overlap)
            if overlap > 0 and ratio >= min_overlap_ratio:
                matches.append((realized_index, overlap, ratio))
        matches.sort(key=lambda item: (-item[2], -item[1], item[0]))
        candidates[target_index] = matches

    output: list[Alignment] = []
    used_realized: set[int] = set()
    for target_index, target in enumerate(target_items):
        matches = candidates[target_index]
        if not preserve_one_to_many:
            matches = next(
                ([match] for match in matches if match[0] not in used_realized),
                [],
            )
        if not matches:
            output.append(Alignment(target, None, 0.0, 0.0, "deletion"))
            continue
        for realized_index, overlap, ratio in matches:
            used_realized.add(realized_index)
            observed = realized_items[realized_index]
            operation = (
                "match"
                if normalize_phone_label(target.label)
                == normalize_phone_label(observed.label)
                else "substitution"
            )
            output.append(Alignment(target, observed, overlap, ratio, operation))

    for realized_index, observed in enumerate(realized_items):
        if realized_index not in used_realized:
            output.append(Alignment(None, observed, 0.0, 0.0, "insertion"))

    return sorted(
        output,
        key=lambda item: (
            item.target.start if item.target is not None else item.realized.start,
            item.realized.start if item.realized is not None else float("inf"),
        ),
    )
