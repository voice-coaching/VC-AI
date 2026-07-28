"""Deterministic, speaker-disjoint split assignment."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


def speaker_disjoint_split(
    records: Iterable[Mapping[str, Any]],
    *,
    ratios: Mapping[str, float],
    stratify_keys: Sequence[str],
    seed: int = 42,
) -> dict[str, str]:
    items = list(records)
    if not items:
        return {}
    if not ratios or any(value <= 0 for value in ratios.values()):
        raise ValueError("split ratios must be positive")
    ratio_sum = sum(ratios.values())
    normalized = {key: value / ratio_sum for key, value in ratios.items()}
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in items:
        speaker = str(record.get("speaker_id", ""))
        if not speaker:
            raise ValueError("speaker_id must be non-empty")
        groups[speaker].append(record)

    def stratum(record: Mapping[str, Any]) -> tuple[str, ...]:
        return tuple(str(record.get(key, "<missing>")) for key in stratify_keys)

    totals = Counter(stratum(record) for record in items)
    group_counts = {
        speaker: Counter(stratum(record) for record in members)
        for speaker, members in groups.items()
    }

    def tie(speaker: str) -> str:
        return hashlib.sha256(f"{seed}:{speaker}".encode()).hexdigest()

    speakers = sorted(
        groups,
        key=lambda speaker: (
            -sum(count / totals[key] for key, count in group_counts[speaker].items()),
            -len(groups[speaker]),
            tie(speaker),
        ),
    )
    assigned_sizes: Counter[str] = Counter()
    assigned_strata: dict[str, Counter[tuple[str, ...]]] = {
        split: Counter() for split in ratios
    }
    assignments: dict[str, str] = {}
    for speaker in speakers:
        size = len(groups[speaker])

        def cost(
            split: str,
            *,
            current_size: int = size,
            current_speaker: str = speaker,
        ) -> tuple[float, str]:
            score = 0.0
            for candidate in ratios:
                projected_size = assigned_sizes[candidate] + (
                    current_size if candidate == split else 0
                )
                size_target = len(items) * normalized[candidate]
                score += ((projected_size - size_target) / max(size_target, 1)) ** 2
                for key, total in totals.items():
                    projected = assigned_strata[candidate][key]
                    if candidate == split:
                        projected += group_counts[current_speaker][key]
                    target = total * normalized[candidate]
                    score += ((projected - target) / max(target, 1)) ** 2
            return score, split

        selected = min(ratios, key=cost)
        assignments[speaker] = selected
        assigned_sizes[selected] += size
        assigned_strata[selected].update(group_counts[speaker])
    return assignments
