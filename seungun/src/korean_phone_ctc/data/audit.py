"""Streaming AI Hub pronunciation-data audit."""

from __future__ import annotations

import datetime as dt
import json
import platform
import statistics
import wave
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile, is_zipfile

from korean_phone_ctc.io_utils import (
    atomic_write_csv,
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_text,
)

from .aihub_reader import parse_json_record, parse_textgrid_tiers

ARCHIVES: dict[str, dict[str, tuple[str, ...]]] = {
    "Training": {
        "wav": ("TS_pronunciation_sound_1.zip", "TS_pronunciation_sound_2.zip"),
        "json": ("TL_pronunciation_lab.zip",),
        "textgrid": ("TL_pronunciation_tag.zip",),
    },
    "Validation": {
        "wav": ("VS_pronunciation_sound.zip",),
        "json": ("VL_pronunciation_lab.zip",),
        "textgrid": ("VL_pronunciation_tag.zip",),
    },
}


def _find_archive(root: Path, filename: str) -> Path | None:
    candidates = sorted(root.rglob(filename))
    candidates.extend(sorted(root.rglob(f"{filename}.part0")))
    for candidate in candidates:
        if is_zipfile(candidate):
            return candidate
    return None


def discover_archives(
    root: Path, partitions: Iterable[str]
) -> tuple[dict[str, dict[str, list[Path]]], list[str]]:
    found: dict[str, dict[str, list[Path]]] = {}
    missing: list[str] = []
    for partition in partitions:
        found[partition] = {}
        for modality, names in ARCHIVES[partition].items():
            found[partition][modality] = []
            for name in names:
                archive = _find_archive(root, name)
                if archive is None:
                    missing.append(f"{partition}/{name}")
                else:
                    found[partition][modality].append(archive)
    return found, missing


def _member_map(
    archives: Iterable[Path], suffix: str
) -> tuple[dict[str, tuple[Path, str]], list[dict[str, str]], int]:
    members: dict[str, tuple[Path, str]] = {}
    duplicates: list[dict[str, str]] = []
    count = 0
    for archive in archives:
        with ZipFile(archive) as zipped:
            for info in zipped.infolist():
                if info.is_dir() or not info.filename.casefold().endswith(
                    suffix.casefold()
                ):
                    continue
                count += 1
                stem = Path(info.filename).stem.casefold()
                if stem in members:
                    duplicates.append(
                        {
                            "stem": stem,
                            "first": f"{members[stem][0]}::{members[stem][1]}",
                            "duplicate": f"{archive}::{info.filename}",
                        }
                    )
                else:
                    members[stem] = (archive, info.filename)
    return members, duplicates, count


def _counter(metadata: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = metadata.get(key)
        if value not in (None, ""):
            return str(value)
    return "<missing>"


def _quantiles(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "median": None, "p95": None, "max": None}
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, int(0.95 * len(ordered)))
    return {
        "count": len(values),
        "min": ordered[0],
        "median": statistics.median(ordered),
        "p95": ordered[p95_index],
        "max": ordered[-1],
    }


def _raw_textgrid_phones(
    zipped: ZipFile, member: str
) -> tuple[list[dict[str, Any]], list[str]]:
    text = zipped.read(member).decode("utf-8-sig")
    tiers = parse_textgrid_tiers(text)
    intervals = tiers.get("phones", [])
    return [asdict(item) for item in intervals], [
        item.phone for item in intervals if item.phone != ""
    ]


def _audio_headers(
    member_maps: Mapping[str, tuple[Path, str]],
) -> tuple[Counter[int], Counter[int], list[float], int]:
    sample_rates: Counter[int] = Counter()
    channels: Counter[int] = Counter()
    durations: list[float] = []
    broken = 0
    archives: dict[Path, ZipFile] = {}
    try:
        for archive, member in member_maps.values():
            try:
                zipped = archives.get(archive)
                if zipped is None:
                    zipped = ZipFile(archive)
                    archives[archive] = zipped
                with zipped.open(member) as raw, wave.open(raw, "rb") as reader:
                    rate = reader.getframerate()
                    frames = reader.getnframes()
                    sample_rates[rate] += 1
                    channels[reader.getnchannels()] += 1
                    if rate > 0:
                        durations.append(frames / rate)
            except (BadZipFile, EOFError, OSError, ValueError, wave.Error):
                broken += 1
    finally:
        for zipped in archives.values():
            zipped.close()
    return sample_rates, channels, durations, broken


def _distribution(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def run_audit(
    dataset_root: Path,
    output_dir: Path,
    *,
    partitions: tuple[str, ...],
    sample_error_count: int = 100,
    inspect_audio_headers: bool = True,
    verify_crc: bool = False,
    requested_dataset_id: str = "71479",
    actual_dataset_id: str = "71490",
) -> dict[str, Any]:
    archives, missing_archives = discover_archives(dataset_root, partitions)
    modality_maps: dict[str, dict[str, tuple[Path, str]]] = {}
    modality_counts: Counter[str] = Counter()
    duplicates: dict[str, list[dict[str, str]]] = defaultdict(list)
    suffixes = {"wav": ".wav", "json": ".json", "textgrid": ".TextGrid"}
    for partition in partitions:
        for modality, suffix in suffixes.items():
            key = f"{partition}:{modality}"
            member_map, repeated, count = _member_map(
                archives[partition][modality], suffix
            )
            modality_maps[key] = member_map
            modality_counts[modality] += count
            duplicates[modality].extend(repeated)

    combined: dict[str, dict[str, tuple[Path, str]]] = {}
    for modality in suffixes:
        combined[modality] = {}
        for partition in partitions:
            for stem, location in modality_maps[f"{partition}:{modality}"].items():
                if stem in combined[modality]:
                    duplicates[modality].append(
                        {
                            "stem": stem,
                            "first": "::".join(map(str, combined[modality][stem])),
                            "duplicate": "::".join(map(str, location)),
                        }
                    )
                else:
                    combined[modality][stem] = location

    all_stems = set().union(*(set(value) for value in combined.values()))
    matched = set.intersection(*(set(value) for value in combined.values()))
    missing_rows = [
        {
            "stem": stem,
            "wav": stem in combined["wav"],
            "json": stem in combined["json"],
            "textgrid": stem in combined["textgrid"],
        }
        for stem in sorted(all_stems - matched)
    ]

    phone_counts: Counter[str] = Counter()
    error_counts: Counter[str] = Counter()
    speakers: Counter[str] = Counter()
    gender: Counter[str] = Counter()
    nationality: Counter[str] = Counter()
    language: Counter[str] = Counter()
    proficiency: Counter[str] = Counter()
    prompt_types: Counter[str] = Counter()
    durations: list[float] = []
    empty_phone_sequences = 0
    invalid_phone_intervals = 0
    broken_json = 0
    normal_samples = 0
    error_samples = 0
    json_tg_exact = 0
    json_tg_different = 0
    error_json_tg_different = 0
    broken_textgrids = 0
    invalid_textgrid_intervals = 0
    sampled_errors: list[dict[str, Any]] = []

    archive_handles: dict[Path, ZipFile] = {}
    try:
        for stem, (archive, member) in combined["json"].items():
            try:
                zipped = archive_handles.get(archive)
                if zipped is None:
                    zipped = ZipFile(archive)
                    archive_handles[archive] = zipped
                record = parse_json_record(zipped.read(member), utterance_id=stem)
            except (BadZipFile, json.JSONDecodeError, KeyError, TypeError, ValueError):
                broken_json += 1
                continue
            speakers[record.speaker_id or "<missing>"] += 1
            gender[_counter(record.speaker_metadata, "gender", "Gender")] += 1
            nationality[
                _counter(record.speaker_metadata, "nationality", "Nationality")
            ] += 1
            language[
                _counter(
                    record.speaker_metadata,
                    "language",
                    "native_language",
                    "mother_tongue",
                )
            ] += 1
            proficiency[
                _counter(record.speaker_metadata, "proficiency", "Proficiency")
            ] += 1
            prompt_types["sentence" if "-RS" in stem.upper() else "word"] += 1
            if record.recorded_time is not None:
                durations.append(record.recorded_time)
            json_phones = [item.phone for item in record.phones]
            phone_counts.update(json_phones)
            if not json_phones:
                empty_phone_sequences += 1
            invalid_phone_intervals += sum(
                item.start < 0 or item.end < 0 or item.end < item.start
                for item in record.phones
            )
            if record.error_tags:
                error_samples += 1
                for tag in record.error_tags:
                    error_counts[str(tag.get("error_tag", "<missing>"))] += 1
            else:
                normal_samples += 1

            textgrid_location = combined["textgrid"].get(stem)
            if textgrid_location is None:
                continue
            try:
                tg_archive, tg_member = textgrid_location
                tg_zip = archive_handles.get(tg_archive)
                if tg_zip is None:
                    tg_zip = ZipFile(tg_archive)
                    archive_handles[tg_archive] = tg_zip
                intervals, tg_phones = _raw_textgrid_phones(tg_zip, tg_member)
                invalid_textgrid_intervals += sum(
                    item["start"] < 0 or item["end"] < 0 or item["end"] < item["start"]
                    for item in intervals
                )
            except (BadZipFile, UnicodeDecodeError, ValueError):
                broken_textgrids += 1
                continue
            same = json_phones == tg_phones
            json_tg_exact += int(same)
            json_tg_different += int(not same)
            if record.error_tags and not same:
                error_json_tg_different += 1
            if record.error_tags and len(sampled_errors) < sample_error_count:
                sampled_errors.append(
                    {
                        "utterance_id": stem,
                        "speaker_id": record.speaker_id,
                        "prompt": record.prompt,
                        "json_phones": json_phones,
                        "error_tags": list(record.error_tags),
                        "textgrid_phone_tier": intervals,
                        "textgrid_phones": tg_phones,
                        "json_textgrid_exact": same,
                    }
                )
    finally:
        for zipped in archive_handles.values():
            zipped.close()

    sample_rates: Counter[int] = Counter()
    channels: Counter[int] = Counter()
    audio_durations: list[float] = []
    broken_audio_headers = 0
    if inspect_audio_headers:
        (
            sample_rates,
            channels,
            audio_durations,
            broken_audio_headers,
        ) = _audio_headers(combined["wav"])

    crc_failures: list[str] = []
    if verify_crc:
        for partition in partitions:
            for modality_archives in archives[partition].values():
                for archive in modality_archives:
                    with ZipFile(archive) as zipped:
                        bad = zipped.testzip()
                        if bad:
                            crc_failures.append(f"{archive}::{bad}")

    error_sample_evidence = [
        item for item in sampled_errors if not item["json_textgrid_exact"]
    ]
    label_semantics = (
        "json_expected_textgrid_observed"
        if len(sampled_errors) >= sample_error_count and error_sample_evidence
        else "inconclusive"
    )
    report: dict[str, Any] = {
        "audit_timestamp": dt.datetime.now(dt.UTC).isoformat(),
        "requested_dataset_id": requested_dataset_id,
        "actual_dataset_id": actual_dataset_id,
        "dataset_id_mismatch": requested_dataset_id != actual_dataset_id,
        "dataset_root": str(dataset_root.resolve()),
        "partitions": list(partitions),
        "archives": {
            partition: {
                modality: [str(path.resolve()) for path in paths]
                for modality, paths in values.items()
            }
            for partition, values in archives.items()
        },
        "missing_archives": missing_archives,
        "file_counts": dict(modality_counts),
        "unique_stem_counts": {
            modality: len(values) for modality, values in combined.items()
        },
        "matched_triplets": len(matched),
        "matching_rate_union": len(matched) / len(all_stems) if all_stems else 0.0,
        "missing_pair_count": len(missing_rows),
        "duplicate_counts": {
            modality: len(values) for modality, values in duplicates.items()
        },
        "broken_json": broken_json,
        "broken_textgrid": broken_textgrids,
        "broken_audio_headers": broken_audio_headers,
        "crc_verified": verify_crc,
        "crc_failures": crc_failures,
        "utterance_count": len(combined["json"]),
        "independent_user_ids": len(speakers),
        "total_recorded_time_seconds": sum(durations),
        "recorded_time_distribution": _quantiles(durations),
        "audio_duration_distribution": _quantiles(audio_durations),
        "sample_rate_distribution": dict(sample_rates),
        "channel_distribution": dict(channels),
        "gender_distribution": _distribution(gender),
        "nationality_distribution": _distribution(nationality),
        "native_language_distribution": _distribution(language),
        "proficiency_distribution": _distribution(proficiency),
        "prompt_type_distribution": _distribution(prompt_types),
        "speaker_utterance_distribution": _quantiles(
            [float(value) for value in speakers.values()]
        ),
        "normal_samples": normal_samples,
        "error_samples": error_samples,
        "normal_sample_ratio": normal_samples / max(normal_samples + error_samples, 1),
        "error_sample_ratio": error_samples / max(normal_samples + error_samples, 1),
        "phone_inventory": sorted(phone_counts),
        "phone_frequencies": _distribution(phone_counts),
        "error_tag_distribution": _distribution(error_counts),
        "empty_phone_sequences": empty_phone_sequences,
        "invalid_json_phone_intervals": invalid_phone_intervals,
        "invalid_textgrid_phone_intervals": invalid_textgrid_intervals,
        "json_textgrid_phone_sequence": {
            "compared": json_tg_exact + json_tg_different,
            "exact": json_tg_exact,
            "different": json_tg_different,
            "error_samples_different": error_json_tg_different,
        },
        "label_semantics": {
            "conclusion": label_semantics,
            "sampled_error_cases": len(sampled_errors),
            "sampled_different_cases": len(error_sample_evidence),
            "ctc_target_allowed": label_semantics != "json_expected_textgrid_observed",
            "reason": (
                "Error-tagged cases contain expected phones in JSON while the "
                "TextGrid phone tier encodes substitutions/deletions actually heard."
                if label_semantics == "json_expected_textgrid_observed"
                else "At least 100 error-tagged paired samples are required."
            ),
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
    }

    atomic_write_json(output_dir / "data_audit.json", report)
    atomic_write_json(
        output_dir / "phone_inventory.json",
        {
            "inventory": report["phone_inventory"],
            "frequencies": report["phone_frequencies"],
            "source": "RecordingMetadata.phonemic.phones",
        },
    )
    atomic_write_csv(
        output_dir / "error_tag_distribution.csv",
        ["error_tag", "count"],
        (
            {"error_tag": error_tag, "count": count}
            for error_tag, count in error_counts.most_common()
        ),
    )
    atomic_write_csv(
        output_dir / "missing_pairs.csv",
        ["stem", "wav", "json", "textgrid"],
        missing_rows,
    )
    atomic_write_jsonl(output_dir / "sample_error_cases.jsonl", sampled_errors)
    atomic_write_text(output_dir / "data_audit.md", render_markdown(report))
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    files = report["file_counts"]
    semantics = report["label_semantics"]
    pairing_row = (
        f"| {files.get('wav', 0)} | {files.get('json', 0)} | "
        f"{files.get('textgrid', 0)} | {report['matched_triplets']} | "
        f"{report['matching_rate_union']:.6%} |"
    )
    return f"""# AI Hub pronunciation data audit

- Requested dataset ID: `{report["requested_dataset_id"]}`
- Actual dataset ID: `{report["actual_dataset_id"]}`
- Dataset root: `{report["dataset_root"]}`
- Partitions: {", ".join(report["partitions"])}

## Pairing

| WAV | JSON | TextGrid | Matched triplets | Union matching rate |
|---:|---:|---:|---:|---:|
{pairing_row}

## Integrity

- Missing archives: {report["missing_archives"]}
- Missing pairs: {report["missing_pair_count"]}
- Duplicate counts: {report["duplicate_counts"]}
- Broken JSON: {report["broken_json"]}
- Broken TextGrid: {report["broken_textgrid"]}
- Broken audio headers: {report["broken_audio_headers"]}
- Empty phone sequences: {report["empty_phone_sequences"]}
- Invalid JSON phone intervals: {report["invalid_json_phone_intervals"]}
- Invalid TextGrid phone intervals: {report["invalid_textgrid_phone_intervals"]}

## Distribution

- Utterances: {report["utterance_count"]}
- Independent UserID: {report["independent_user_ids"]}
- Recorded time: {report["total_recorded_time_seconds"]:.3f} seconds
- Normal/error samples: {report["normal_samples"]}/{report["error_samples"]}
- Phone inventory: {json.dumps(report["phone_inventory"], ensure_ascii=False)}
- Error tags: {json.dumps(report["error_tag_distribution"], ensure_ascii=False)}

## Label semantics gate

- Conclusion: **{semantics["conclusion"]}**
- Error cases sampled: {semantics["sampled_error_cases"]}
- Different JSON/TextGrid cases: {semantics["sampled_different_cases"]}
- CTC target allowed: **{semantics["ctc_target_allowed"]}**
- Reason: {semantics["reason"]}
"""
