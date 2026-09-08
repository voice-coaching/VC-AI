#!/usr/bin/env python3
"""Build aggregate, privacy-safe data-card artifacts from the modeling manifest."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from voice_coach.phonology import (
    UNKNOWN_PHONE_TOKEN,
    normalize_error_tag,
    normalize_realized_phone_label,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def render_markdown(report: dict) -> str:
    duration = report["duration_seconds"]
    scores = report["score_coverage"]
    operations = report["phone_alignment_operations"]
    lines = [
        "# AI Hub 71490 발음 데이터 카드",
        "",
        "생성일: 2026-07-29 KST",
        "",
        "이 문서는 제한 데이터의 원문·원시 화자 ID를 포함하지 않는 집계",
        "data card다. 모델 성능 결과가 아니라 데이터 구성과 품질을 기록한다.",
        "",
        "## 규모와 고정 분할",
        "",
        f"- 발화: {report['record_count']:,}건",
        f"- 가명 화자: {report['speaker_count']:,}명",
        f"- 총 길이: {report['total_hours']:.3f}시간",
        f"- 고유 prompt: {report['prompt_count']:,}개",
        f"- 고유 PCM: {report['unique_pcm_count']:,}개",
        f"- 동일 split 내부 중복 record: {report['within_split_pcm_duplicate_records']:,}건",
        "",
        "| split | 발화 수 |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {name} | {count:,} |"
        for name, count in report["split_counts"].items()
    )
    lines.extend(
        [
            "",
            "모든 prompt가 다섯 split에 나타나므로 현 speaker split은",
            "prompt-disjoint 평가가 아니다. 별도 예약 prompt challenge를 사용한다.",
            "",
            "## 길이와 오디오",
            "",
            f"- median: {duration['p50']:.3f}초",
            f"- p95: {duration['p95']:.3f}초",
            f"- p99: {duration['p99']:.3f}초",
            f"- max: {duration['max']:.3f}초",
            f"- 20초 초과: {duration['over_20_seconds']:,}건",
            f"- 60초 초과: {duration['over_60_seconds']:,}건",
            f"- sample rate: {', '.join(str(x) for x in report['sample_rates_hz'])} Hz",
            "",
            "SSL 입력은 decoder 이후 16 kHz로 resample하며, 20초 초과 발화는",
            "label-aware chunking 대상으로 처리한다.",
            "",
            "## 감독 label coverage",
            "",
            "| label | 유효 | 결측(0 sentinel 포함) |",
            "|---|---:|---:|",
        ]
    )
    for name, item in scores.items():
        lines.append(
            f"| {name} | {item['valid']:,} | {item['missing']:,} |"
        )
    lines.extend(
        [
            "",
            "평정 척도는 1–5다. 원본 0은 실제 최저 점수로 학습하지 않고",
            "미평정 sentinel로 mask한다.",
            "",
            "## Phone과 오류 annotation",
            "",
            f"- target phone label: {report['target_phone_label_count']}종",
            f"- realized raw label: {report['realized_raw_label_count']:,}종",
            f"- realized occurrence canonical coverage: "
            f"{report['realized_phone_canonical_coverage']:.6%}",
            f"- `<unk>` occurrence: {report['realized_unknown_occurrences']:,}건",
            f"- 공식 error tag: {report['raw_error_tag_count']}종, "
            f"{report['error_interval_count']:,}구간",
            f"- ontology 미매핑: {report['unknown_error_tag_occurrences']:,}건",
            "",
            "| operation | 수 |",
            "|---|---:|",
        ]
    )
    lines.extend(f"| {key} | {value:,} |" for key, value in operations.items())
    lines.extend(
        [
            "",
            "Gold TextGrid boundary를 사용하는 결과는 `oracle alignment`로",
            "표기하고, 실제 운용용 predicted alignment 결과와 분리한다.",
            "",
            "## 자동 G0와 제외 정책",
            "",
            f"- QC flag 없는 발화: {report['records_without_qc_flags']:,}건",
            f"- `interval_out_of_bounds`: "
            f"{report['qc_flag_counts'].get('interval_out_of_bounds', 0):,}건",
            f"- `missing_realized_phone_tier`: "
            f"{report['qc_flag_counts'].get('missing_realized_phone_tier', 0):,}건",
            "- 위 flag 발화는 primary 학습에서 제외하고 감사 slice에는 보존한다.",
            "- split 간 화자·PCM 중복은 0이다.",
            "- 최종 G0는 240건 전문가 정렬 감사 전까지 pending이다.",
            "",
            "## 재현 식별자",
            "",
            f"- modeling manifest SHA-256: `{report['manifest_sha256']}`",
            f"- split assignment SHA-256: `{report['split_assignment_sha256']}`",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    parquet = pq.ParquetFile(args.manifest)
    columns = [
        "speaker_id",
        "split",
        "pcm_sha256",
        "duration_seconds",
        "sample_rate_hz",
        "l1",
        "gender",
        "proficiency",
        "prompt_sha256",
        "pronunciation_score",
        "fluency_score",
        "comprehensibility_score",
        "target_phone_intervals",
        "realized_phone_intervals",
        "error_intervals",
        "phone_alignments",
        "qc_flags",
    ]
    records = records_without_flags = 0
    total_seconds = 0.0
    durations: list[float] = []
    speakers: set[str] = set()
    pcm_splits: dict[str, str] = {}
    pcm_counts: Counter[str] = Counter()
    prompts: set[str] = set()
    prompt_splits: defaultdict[str, set[str]] = defaultdict(set)
    splits: Counter[str] = Counter()
    sample_rates: Counter[int] = Counter()
    slices = {
        "l1": Counter(),
        "gender": Counter(),
        "proficiency": Counter(),
    }
    qc_flags: Counter[str] = Counter()
    score_values = {
        "pronunciation_score": Counter(),
        "fluency_score": Counter(),
        "comprehensibility_score": Counter(),
    }
    score_missing = Counter()
    target_labels: Counter[str] = Counter()
    realized_raw: Counter[str] = Counter()
    realized_tokens: Counter[str] = Counter()
    error_raw: Counter[str] = Counter()
    error_ids: Counter[str] = Counter()
    operations: Counter[str] = Counter()

    for batch in parquet.iter_batches(batch_size=2048, columns=columns):
        for row in batch.to_pylist():
            records += 1
            duration = float(row["duration_seconds"])
            durations.append(duration)
            total_seconds += duration
            speakers.add(row["speaker_id"])
            split = row["split"]
            splits[split] += 1
            pcm = row["pcm_sha256"]
            if pcm:
                pcm_splits.setdefault(pcm, split)
                pcm_counts[pcm] += 1
            prompt = row["prompt_sha256"]
            prompts.add(prompt)
            prompt_splits[prompt].add(split)
            sample_rates[int(row["sample_rate_hz"])] += 1
            for name in slices:
                slices[name][str(row[name])] += 1
            flags = row["qc_flags"] or []
            if not flags:
                records_without_flags += 1
            qc_flags.update(flags)
            for name, counts in score_values.items():
                value = row[name]
                if value is None:
                    score_missing[name] += 1
                else:
                    counts[str(value)] += 1
            target_labels.update(
                item["label"] for item in row["target_phone_intervals"] or []
            )
            for item in row["realized_phone_intervals"] or []:
                label = item["label"]
                realized_raw[label] += 1
                realized_tokens.update(normalize_realized_phone_label(label))
            for item in row["error_intervals"] or []:
                label = item["label"]
                error_raw[label] += 1
                error_ids[str(normalize_error_tag(label))] += 1
            operations.update(
                item["operation"] for item in row["phone_alignments"] or []
            )

    duration_values = np.asarray(durations, dtype=np.float64)
    source_occurrences = sum(realized_raw.values())
    split_assignment_payload = json.dumps(
        sorted(
            {
                speaker: split
                for batch in parquet.iter_batches(
                    batch_size=8192, columns=["speaker_id", "split"]
                )
                for speaker, split in zip(
                    batch.column(0).to_pylist(),
                    batch.column(1).to_pylist(),
                    strict=True,
                )
            }.items()
        ),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    report = {
        "schema_version": "aihub71490.data_card.v1",
        "record_count": records,
        "speaker_count": len(speakers),
        "total_hours": total_seconds / 3600,
        "split_counts": dict(sorted(splits.items())),
        "unique_pcm_count": len(pcm_counts),
        "within_split_pcm_duplicate_records": sum(
            count - 1 for count in pcm_counts.values() if count > 1
        ),
        "prompt_count": len(prompts),
        "prompt_split_membership_counts": dict(
            sorted(Counter(map(len, prompt_splits.values())).items())
        ),
        "duration_seconds": {
            "min": float(duration_values.min()),
            "p50": float(np.percentile(duration_values, 50)),
            "p95": float(np.percentile(duration_values, 95)),
            "p99": float(np.percentile(duration_values, 99)),
            "max": float(duration_values.max()),
            "over_20_seconds": int(np.sum(duration_values > 20)),
            "over_60_seconds": int(np.sum(duration_values > 60)),
        },
        "sample_rates_hz": sorted(sample_rates),
        "sample_rate_counts": dict(sorted(sample_rates.items())),
        "slice_counts": {
            name: dict(sorted(counts.items()))
            for name, counts in slices.items()
        },
        "score_coverage": {
            name: {
                "valid": sum(counts.values()),
                "missing": score_missing[name],
                "distribution": dict(sorted(counts.items())),
            }
            for name, counts in score_values.items()
        },
        "target_phone_label_count": len(target_labels),
        "target_phone_occurrences": sum(target_labels.values()),
        "realized_raw_label_count": len(realized_raw),
        "realized_phone_occurrences": source_occurrences,
        "realized_unknown_occurrences": realized_tokens[UNKNOWN_PHONE_TOKEN],
        "realized_phone_canonical_coverage": (
            (source_occurrences - realized_tokens[UNKNOWN_PHONE_TOKEN])
            / source_occurrences
        ),
        "raw_error_tag_count": len(error_raw),
        "error_interval_count": sum(error_raw.values()),
        "error_tag_counts": dict(sorted(error_raw.items())),
        "normalized_error_tag_counts": dict(sorted(error_ids.items())),
        "unknown_error_tag_occurrences": error_ids["unknown"],
        "phone_alignment_operations": dict(sorted(operations.items())),
        "records_without_qc_flags": records_without_flags,
        "qc_flag_counts": dict(sorted(qc_flags.items())),
        "manifest_sha256": sha256_file(args.manifest),
        "split_assignment_sha256": hashlib.sha256(
            split_assignment_payload
        ).hexdigest(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.write_text(
        render_markdown(report),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
