"""Audit TextGrid phone tiers as candidate observed-pronunciation CTC targets."""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from korean_phone_ctc.io_utils import (
    atomic_write_csv,
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_text,
)

from .aihub_reader import parse_json_record, parse_textgrid_tiers

MODERN_KOREAN_PHONE_INVENTORY = {
    "spn",
    "ㄱ",
    "ㄲ",
    "ㄴ",
    "ㄷ",
    "ㄸ",
    "ㄹ",
    "ㅁ",
    "ㅂ",
    "ㅃ",
    "ㅅ",
    "ㅆ",
    "ㅇ",
    "ㅈ",
    "ㅉ",
    "ㅊ",
    "ㅋ",
    "ㅌ",
    "ㅍ",
    "ㅎ",
    "ㅏ",
    "ㅐ",
    "ㅑ",
    "ㅒ",
    "ㅓ",
    "ㅔ",
    "ㅕ",
    "ㅖ",
    "ㅗ",
    "ㅘ",
    "ㅙ",
    "ㅚ",
    "ㅛ",
    "ㅜ",
    "ㅝ",
    "ㅞ",
    "ㅟ",
    "ㅠ",
    "ㅡ",
    "ㅢ",
    "ㅣ",
}


def normalize_textgrid_label(label: str) -> list[str]:
    """Apply the candidate structural normalization without phone remapping.

    Empty intervals are temporal gaps. A hyphen is a deletion placeholder
    aligned to an expected phone. Whitespace-delimited labels represent
    multiple phones sharing an interval. No phone symbol is otherwise changed.
    """

    stripped = label.strip()
    if not stripped or stripped == "-":
        return []
    return stripped.split()


def split_known_phone_sequence(
    label: str, inventory: set[str]
) -> tuple[list[str] | None, str]:
    """Split only exact phones or concatenations of known one-codepoint phones."""

    stripped = label.strip()
    if not stripped:
        return [], "empty_interval"
    if stripped == "-":
        return [], "deletion_placeholder"
    result: list[str] = []
    concatenated = False
    for chunk in stripped.split():
        if chunk in inventory:
            result.append(chunk)
        elif chunk and all(character in inventory for character in chunk):
            result.extend(chunk)
            concatenated = True
        else:
            return None, "unresolved"
    return result, "known_concatenation" if concatenated or len(result) > 1 else "exact"


def _files(directory: Path, suffix: str) -> dict[str, Path]:
    return {
        path.stem.casefold(): path
        for path in directory.iterdir()
        if path.is_file()
        and not path.name.startswith(".")
        and path.suffix.casefold() == suffix.casefold()
    }


def _distribution(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _render_markdown(report: Mapping[str, Any]) -> str:
    training = report["partitions"]["Training"]
    validation = report["partitions"]["Validation"]
    training_row = (
        f"| Training | {training['wav_count']} | {training['json_count']} | "
        f"{training['textgrid_count']} | {training['matched_count']} |"
    )
    validation_row = (
        f"| Validation | {validation['wav_count']} | "
        f"{validation['json_count']} | {validation['textgrid_count']} | "
        f"{validation['matched_count']} |"
    )
    unsafe_top = list(report["unsafe_raw_label_distribution"].items())[:20]
    return f"""# TextGrid observed-target audit

## Coverage

| Partition | WAV | JSON | TextGrid | Matched |
|---|---:|---:|---:|---:|
{training_row}
{validation_row}

## Speaker leakage

- Training UserID: {training["speaker_count"]}
- Validation UserID: {validation["speaker_count"]}
- Official split overlap: {report["official_split_speaker_overlap_count"]}

## TextGrid representation

- Phone intervals: {report["total_phone_intervals"]}
- Empty intervals: {report["empty_interval_count"]}
- Deletion placeholders (`-`): {report["deletion_placeholder_count"]}
- Whitespace compound labels: {report["compound_label_count"]}
- Missing phone tiers: {report["missing_phone_tier_count"]}
- Normalized empty target sequences: {report["normalized_empty_sequence_count"]}
- JSON/raw TextGrid different: {report["json_raw_different_count"]}
- JSON/normalized TextGrid different: {report["json_normalized_different_count"]}

## Candidate target gate

- Raw label inventory size: {report["raw_label_inventory_size"]}
- Structurally normalized inventory size: {report["normalized_phone_inventory_size"]}
- Safely splittable raw labels: {report["safe_raw_label_count"]}
- Safely splittable concatenated intervals: {report["safe_concatenated_interval_count"]}
- Unresolved raw labels: {report["unsafe_raw_label_count"]}
- Unresolved intervals: {report["unsafe_interval_count"]}
- Utterances containing unresolved labels: {report["unresolved_utterance_count"]}
- Top unresolved labels: {unsafe_top}
- Status: **{report["gate"]["status"]}**
- Reason: {report["gate"]["reason"]}
"""


def run_textgrid_target_audit(
    *,
    partition_directories: Mapping[str, Mapping[str, Path]],
    output_dir: Path,
    special_sample_limit: int = 200,
    progress_every: int = 50_000,
) -> dict[str, Any]:
    raw_labels: Counter[str] = Counter()
    normalized_phones: Counter[str] = Counter()
    json_phones: Counter[str] = Counter()
    compound_labels: Counter[str] = Counter()
    speaker_sets: dict[str, set[str]] = {}
    partition_reports: dict[str, dict[str, Any]] = {}
    special_samples: list[dict[str, Any]] = []
    total_intervals = 0
    empty_intervals = 0
    deletion_placeholders = 0
    missing_phone_tiers = 0
    invalid_intervals = 0
    broken_json = 0
    broken_textgrid = 0
    normalized_empty_sequences = 0
    json_raw_different = 0
    json_normalized_different = 0
    unresolved_utterance_count = 0

    for partition, directories in partition_directories.items():
        wav_files = _files(directories["wav"], ".wav")
        json_files = _files(directories["json"], ".json")
        textgrid_files = _files(directories["textgrid"], ".TextGrid")
        matched = set(wav_files) & set(json_files) & set(textgrid_files)
        all_stems = set(wav_files) | set(json_files) | set(textgrid_files)
        speakers: set[str] = set()

        for index, stem in enumerate(sorted(matched), start=1):
            try:
                record = parse_json_record(
                    json_files[stem].read_bytes(), utterance_id=stem
                )
            except (OSError, TypeError, ValueError):
                broken_json += 1
                continue
            speakers.add(record.speaker_id)
            expected = [phone.phone for phone in record.phones]
            json_phones.update(expected)
            try:
                tiers = parse_textgrid_tiers(
                    textgrid_files[stem].read_text(encoding="utf-8-sig")
                )
            except (OSError, UnicodeDecodeError, ValueError):
                broken_textgrid += 1
                continue
            if "phones" not in tiers:
                missing_phone_tiers += 1
                continue

            intervals = tiers["phones"]
            raw_sequence: list[str] = []
            normalized_sequence: list[str] = []
            utterance_special: list[dict[str, Any]] = []
            utterance_has_unresolved = False
            for interval in intervals:
                total_intervals += 1
                raw_labels[interval.phone] += 1
                invalid_intervals += int(
                    interval.start < 0
                    or interval.end < 0
                    or interval.end < interval.start
                )
                stripped = interval.phone.strip()
                if not stripped:
                    empty_intervals += 1
                else:
                    raw_sequence.append(stripped)
                if stripped == "-":
                    deletion_placeholders += 1
                tokens = normalize_textgrid_label(interval.phone)
                if len(tokens) > 1:
                    compound_labels[stripped] += 1
                normalized_phones.update(tokens)
                normalized_sequence.extend(tokens)
                if not stripped or stripped == "-" or len(tokens) > 1:
                    utterance_special.append(asdict(interval))
                policy_tokens, _ = split_known_phone_sequence(
                    interval.phone, MODERN_KOREAN_PHONE_INVENTORY
                )
                utterance_has_unresolved |= policy_tokens is None

            unresolved_utterance_count += int(utterance_has_unresolved)
            if not normalized_sequence:
                normalized_empty_sequences += 1
            json_raw_different += int(expected != raw_sequence)
            json_normalized_different += int(expected != normalized_sequence)
            if utterance_special and len(special_samples) < special_sample_limit:
                special_samples.append(
                    {
                        "partition": partition,
                        "utterance_id": stem,
                        "speaker_id": record.speaker_id,
                        "prompt": record.prompt,
                        "expected_json_phones": expected,
                        "raw_textgrid_phones": raw_sequence,
                        "normalized_observed_phones": normalized_sequence,
                        "error_tags": list(record.error_tags),
                        "special_intervals": utterance_special,
                    }
                )
            if progress_every and index % progress_every == 0:
                print(f"{partition}: processed {index}/{len(matched)}", flush=True)

        speaker_sets[partition] = speakers
        partition_reports[partition] = {
            "wav_count": len(wav_files),
            "json_count": len(json_files),
            "textgrid_count": len(textgrid_files),
            "matched_count": len(matched),
            "missing_count": len(all_stems - matched),
            "speaker_count": len(speakers),
        }

    overlap = set.intersection(*speaker_sets.values()) if speaker_sets else set()
    json_inventory = set(json_phones)
    safe_labels: Counter[str] = Counter()
    safe_concatenated_labels: Counter[str] = Counter()
    unsafe_labels: Counter[str] = Counter()
    ctc_safe_inventory: Counter[str] = Counter()
    for label, count in raw_labels.items():
        classified_tokens, classification = split_known_phone_sequence(
            label, MODERN_KOREAN_PHONE_INVENTORY
        )
        if classified_tokens is None:
            unsafe_labels[label] += count
            continue
        safe_labels[label] += count
        for token in classified_tokens:
            ctc_safe_inventory[token] += count
        if classification == "known_concatenation":
            safe_concatenated_labels[label] += count
    absent_from_json = sorted(set(normalized_phones) - json_inventory)
    status = "review_required"
    reason = (
        "All normalized symbols occur in the JSON inventory, but deletion and "
        "compound-label policies require explicit approval before manifest creation."
        if not unsafe_labels and not normalized_empty_sequences
        else (
            f"{len(unsafe_labels)} raw labels across {sum(unsafe_labels.values())} "
            "intervals remain unresolved; empty targets and missing tiers also "
            "require an explicit exclusion policy."
        )
    )
    report: dict[str, Any] = {
        "audit_timestamp": dt.datetime.now(dt.UTC).isoformat(),
        "partitions": partition_reports,
        "official_split_speaker_overlap_count": len(overlap),
        "official_split_speaker_overlap": sorted(overlap),
        "total_phone_intervals": total_intervals,
        "empty_interval_count": empty_intervals,
        "deletion_placeholder_count": deletion_placeholders,
        "compound_label_count": sum(compound_labels.values()),
        "compound_label_distribution": _distribution(compound_labels),
        "raw_label_inventory": sorted(raw_labels),
        "raw_label_inventory_size": len(raw_labels),
        "raw_label_distribution": _distribution(raw_labels),
        "normalized_phone_inventory": sorted(normalized_phones),
        "normalized_phone_inventory_size": len(normalized_phones),
        "normalized_phone_distribution": _distribution(normalized_phones),
        "json_phone_inventory": sorted(json_phones),
        "policy_candidate_inventory": sorted(MODERN_KOREAN_PHONE_INVENTORY),
        "normalized_tokens_absent_from_json_inventory": absent_from_json,
        "safe_raw_label_count": len(safe_labels),
        "safe_concatenated_raw_label_count": len(safe_concatenated_labels),
        "safe_concatenated_interval_count": sum(safe_concatenated_labels.values()),
        "ctc_safe_candidate_inventory": sorted(ctc_safe_inventory),
        "unsafe_raw_label_count": len(unsafe_labels),
        "unsafe_interval_count": sum(unsafe_labels.values()),
        "unresolved_utterance_count": unresolved_utterance_count,
        "unsafe_raw_label_distribution": _distribution(unsafe_labels),
        "missing_phone_tier_count": missing_phone_tiers,
        "invalid_interval_count": invalid_intervals,
        "broken_json_count": broken_json,
        "broken_textgrid_count": broken_textgrid,
        "normalized_empty_sequence_count": normalized_empty_sequences,
        "json_raw_different_count": json_raw_different,
        "json_normalized_different_count": json_normalized_different,
        "special_sample_count": len(special_samples),
        "candidate_normalization": {
            "empty_interval": "drop",
            "deletion_placeholder_-": "drop",
            "whitespace_compound_label": "split_on_whitespace",
            "other_phone": "preserve_exactly",
        },
        "gate": {"status": status, "reason": reason},
    }
    atomic_write_json(output_dir / "textgrid_target_audit.json", report)
    atomic_write_text(output_dir / "textgrid_target_audit.md", _render_markdown(report))
    atomic_write_csv(
        output_dir / "textgrid_raw_label_distribution.csv",
        ["label", "count"],
        ({"label": label, "count": count} for label, count in raw_labels.most_common()),
    )
    atomic_write_jsonl(output_dir / "textgrid_special_cases.jsonl", special_samples)
    atomic_write_csv(
        output_dir / "textgrid_unresolved_labels.csv",
        ["label", "count"],
        (
            {"label": label, "count": count}
            for label, count in unsafe_labels.most_common()
        ),
    )
    return report


def extracted_partition_directories(
    dataset_root: Path,
) -> dict[str, dict[str, Path]]:
    data_roots = list(dataset_root.rglob("01-1.정식개방데이터"))
    if len(data_roots) != 1:
        raise ValueError(
            f"expected one 01-1.정식개방데이터 directory, found {len(data_roots)}"
        )
    data = data_roots[0]
    return {
        "Training": {
            "wav": data / "Training" / "01.원천데이터" / "extracted_pronunciation",
            "json": data
            / "Training"
            / "02.라벨링데이터"
            / "extracted"
            / "TL_pronunciation_lab",
            "textgrid": data
            / "Training"
            / "02.라벨링데이터"
            / "extracted"
            / "TL_pronunciation_tag",
        },
        "Validation": {
            "wav": data / "Validation" / "01.원천데이터" / "extracted_pronunciation",
            "json": data
            / "Validation"
            / "02.라벨링데이터"
            / "extracted"
            / "VL_pronunciation_lab",
            "textgrid": data
            / "Validation"
            / "02.라벨링데이터"
            / "extracted"
            / "VL_pronunciation_tag",
        },
    }
