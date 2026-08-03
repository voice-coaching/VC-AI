"""Manifest-level quality gates used before any model experiment."""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from typing import Any, Iterable, Mapping


def _present_sources(record: Mapping[str, Any]) -> bool:
    paths = record.get("source_paths")
    if not isinstance(paths, Mapping):
        return False
    required = ["wav", "json"]
    if record.get("source_kind") == "pronunciation":
        required.append("textgrid")
    return all(paths.get(name) for name in required)


def _intervals_in_bounds(record: Mapping[str, Any]) -> bool:
    audio = record.get("audio")
    duration = audio.get("duration_seconds") if isinstance(audio, Mapping) else None
    if not isinstance(duration, (int, float)) or duration < 0:
        return False
    for field in ("word_intervals", "phone_intervals", "error_intervals"):
        values = record.get(field, [])
        if values is None:
            continue
        if not isinstance(values, list):
            return False
        for interval in values:
            if not isinstance(interval, Mapping):
                return False
            start = interval.get("start", interval.get("xmin"))
            end = interval.get("end", interval.get("xmax"))
            if (
                not isinstance(start, (int, float))
                or not isinstance(end, (int, float))
                or start < 0
                or end < start
                or end > duration + 1e-6
            ):
                return False
    return True


def _cross_split_duplicates(
    records: list[Mapping[str, Any]], field: str
) -> list[dict[str, Any]]:
    locations: dict[str, set[str]] = defaultdict(set)
    for record in records:
        value = record.get(field)
        split = record.get("split")
        if value not in (None, "") and split not in (None, ""):
            locations[str(value)].add(str(split))
    return [
        {"value": value, "splits": sorted(splits)}
        for value, splits in sorted(locations.items())
        if len(splits) > 1
    ]


def audit_manifest_records(
    records: Iterable[Mapping[str, Any]],
    *,
    min_pairing_rate: float = 0.99,
    max_decoder_failure_rate: float = 0.001,
    max_time_bound_error_rate: float = 0.01,
) -> dict[str, Any]:
    """Calculate G0 metrics and a single machine-checkable pass decision."""

    items = list(records)
    total = len(items)
    if total == 0:
        return {
            "status": "failed",
            "record_count": 0,
            "failed_gates": ["non_empty_manifest"],
        }

    paired = sum(_present_sources(record) for record in items)
    decoder_failures = sum(
        not isinstance(record.get("audio"), Mapping)
        or bool(
            record.get("qc", {}).get("errors", {}).get("wav")
            if isinstance(record.get("qc"), Mapping)
            else True
        )
        for record in items
    )
    time_bound_errors = sum(not _intervals_in_bounds(record) for record in items)
    speaker_duplicates = _cross_split_duplicates(items, "speaker_id")
    pcm_duplicates = _cross_split_duplicates(items, "pcm_sha256")

    pairing_rate = paired / total
    decoder_failure_rate = decoder_failures / total
    time_bound_error_rate = time_bound_errors / total
    failed: list[str] = []
    if pairing_rate < min_pairing_rate:
        failed.append("pairing_rate")
    if decoder_failure_rate > max_decoder_failure_rate:
        failed.append("decoder_failure_rate")
    if time_bound_error_rate > max_time_bound_error_rate:
        failed.append("time_bound_error_rate")
    if speaker_duplicates:
        failed.append("cross_split_speaker_overlap")
    if pcm_duplicates:
        failed.append("cross_split_pcm_duplicate")

    split_names = sorted(
        {str(record["split"]) for record in items if record.get("split") is not None}
    )
    split_pairs = [list(pair) for pair in combinations(split_names, 2)]
    return {
        "status": "passed" if not failed else "failed",
        "record_count": total,
        "pairing_rate": pairing_rate,
        "decoder_failure_rate": decoder_failure_rate,
        "time_bound_error_rate": time_bound_error_rate,
        "split_pairs_checked": split_pairs,
        "cross_split_speaker_overlaps": speaker_duplicates,
        "cross_split_pcm_duplicates": pcm_duplicates,
        "failed_gates": failed,
        "thresholds": {
            "min_pairing_rate": min_pairing_rate,
            "max_decoder_failure_rate": max_decoder_failure_rate,
            "max_time_bound_error_rate": max_time_bound_error_rate,
        },
    }
