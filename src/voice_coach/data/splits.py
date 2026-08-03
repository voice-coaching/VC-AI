"""Deterministic speaker-disjoint stratified dataset splitting."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
from typing import Any, Iterable, Mapping, Sequence


def stratified_group_split(
    records: Iterable[Mapping[str, Any]],
    *,
    group_key: str,
    stratify_keys: Sequence[str],
    ratios: Mapping[str, float],
    seed: int = 42,
) -> dict[str, str]:
    """Assign each group to one split while approximately preserving strata."""

    items = list(records)
    if not items:
        return {}
    if not ratios or any(value <= 0 for value in ratios.values()):
        raise ValueError("split ratios must all be positive")
    total_ratio = sum(ratios.values())
    normalized = {name: value / total_ratio for name, value in ratios.items()}
    split_names = list(ratios)

    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in items:
        if group_key not in record or record[group_key] in (None, ""):
            raise ValueError(f"record is missing non-empty group key {group_key!r}")
        grouped[str(record[group_key])].append(record)

    def stratum(record: Mapping[str, Any]) -> tuple[str, ...]:
        return tuple(str(record.get(key, "<missing>")) for key in stratify_keys)

    total_strata = Counter(stratum(record) for record in items)
    group_strata = {
        group: Counter(stratum(record) for record in members)
        for group, members in grouped.items()
    }
    rarity = {
        group: sum(
            count / total_strata[key] for key, count in strata.items()
        )
        for group, strata in group_strata.items()
    }

    def tie(group: str) -> str:
        return hashlib.sha256(f"{seed}:{group}".encode()).hexdigest()

    ordered_groups = sorted(
        grouped,
        key=lambda group: (-rarity[group], -len(grouped[group]), tie(group)),
    )
    assigned_counts = Counter()
    assigned_strata: dict[str, Counter[tuple[str, ...]]] = {
        name: Counter() for name in split_names
    }
    assignments: dict[str, str] = {}
    total_records = len(items)

    for group in ordered_groups:
        size = len(grouped[group])
        strata = group_strata[group]

        def assignment_cost(split: str) -> tuple[float, str]:
            cost = 0.0
            for candidate in split_names:
                projected_size = assigned_counts[candidate]
                if candidate == split:
                    projected_size += size
                size_target = total_records * normalized[candidate]
                cost += (
                    (projected_size - size_target) / max(size_target, 1.0)
                ) ** 2
                for key, total in total_strata.items():
                    projected = assigned_strata[candidate][key]
                    if candidate == split:
                        projected += strata[key]
                    target = total * normalized[candidate]
                    cost += ((projected - target) / max(target, 1.0)) ** 2
            return cost, split

        selected = min(split_names, key=assignment_cost)
        assignments[group] = selected
        assigned_counts[selected] += size
        assigned_strata[selected].update(strata)

    return assignments
