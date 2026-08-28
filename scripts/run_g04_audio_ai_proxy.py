#!/usr/bin/env python3
"""Run a local-only, resumable G04 audio proxy without editing canonical reviews.

The restricted state retains Qwen transcripts and item-level phone evidence in
R1. The aggregate output contains counts and hashes only. Neither output is a
human/expert G04 completion attestation.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any, Mapping
import unicodedata

from voice_coach.evaluation.alignment_review import (
    ReviewDataset,
    atomic_write_json,
    sha256_file,
)
from voice_coach.evaluation.g04_audio_proxy import (
    G04AudioProxyThresholds,
    alignment_record_error_count,
    classify_annotation_fidelity,
    normalize_phone_sequence,
    normalized_sequence_similarity,
    recompute_alignment_records,
)
from voice_coach.pronunciation_feedback.adapters.expected_phones_subprocess import (
    SubprocessG2PKExpectedPhoneFrontend,
)
from voice_coach.pronunciation_feedback.adapters.qwen_audio_auditor import (
    QwenAudioAuditor,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_ROOT = Path("data/restricted/aihub/dataset-71490")
DEFAULT_WORKSHEET = Path(
    "data/processed/aihub71490/manifests/aihub71490_manual_alignment_audit.tsv"
)
DEFAULT_DETAILS = Path(
    "data/processed/aihub71490/manifests/"
    "aihub71490_manual_alignment_audit_details.jsonl"
)
DEFAULT_RESTRICTED_OUTPUT = Path(
    "data/restricted/aihub/dataset-71490/review/g04_audio_ai_proxy_state_v1.json"
)
DEFAULT_AGGREGATE_OUTPUT = Path(
    "artifacts/evaluation/g04_audio_ai_proxy_aggregate_v1.json"
)
DEFAULT_NLTK_DATA_ROOT = Path(".venv-paper-g2pk/nltk_data")
STATE_SCHEMA_VERSION = "voice_coach.g04_audio_ai_proxy_state.v1"
AGGREGATE_SCHEMA_VERSION = "voice_coach.g04_audio_ai_proxy_aggregate.v1"


G04_MAXIMUM_PROMPT_CODEPOINTS = 256


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the local Qwen2-Audio engineering proxy over the fixed G04 "
            "sample. Restricted audio and transcripts never leave R1, and the "
            "canonical review state is never modified."
        )
    )
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--worksheet", type=Path, default=DEFAULT_WORKSHEET)
    parser.add_argument("--details", type=Path, default=DEFAULT_DETAILS)
    parser.add_argument(
        "--restricted-output",
        type=Path,
        default=DEFAULT_RESTRICTED_OUTPUT,
    )
    parser.add_argument(
        "--aggregate-output",
        type=Path,
        default=DEFAULT_AGGREGATE_OUTPUT,
    )
    parser.add_argument(
        "--nltk-data-root",
        type=Path,
        default=DEFAULT_NLTK_DATA_ROOT,
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument(
        "--dtype",
        choices=("float32", "bfloat16", "float16"),
        default="bfloat16",
    )
    parser.add_argument("--minimum-model-confidence", type=float, default=0.45)
    parser.add_argument(
        "--minimum-target-source-similarity",
        type=float,
        default=0.80,
    )
    parser.add_argument(
        "--minimum-realized-audio-similarity",
        type=float,
        default=0.65,
    )
    parser.add_argument(
        "--minimum-any-audio-similarity",
        type=float,
        default=0.45,
    )
    return parser


def _absolute(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_korean_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", unicodedata.normalize("NFKC", value))
    allowed: list[str] = []
    for character in normalized:
        codepoint = ord(character)
        if (
            character.isspace()
            or character in {".", "?"}
            or 0xAC00 <= codepoint <= 0xD7A3
            or 0x1100 <= codepoint <= 0x11FF
            or 0x3130 <= codepoint <= 0x318F
        ):
            allowed.append(character)
    cleaned = " ".join("".join(allowed).split()).strip()
    has_korean_letter = any(
        0xAC00 <= ord(character) <= 0xD7A3
        or 0x1100 <= ord(character) <= 0x11FF
        or 0x3130 <= ord(character) <= 0x318F
        for character in cleaned
    )
    return cleaned if has_korean_letter else ""


def _discover_source_metadata(
    dataset_root: Path,
    raw_ids: set[str],
) -> dict[str, dict[str, Any]]:
    matches: dict[str, dict[str, Any]] = {}
    for path in dataset_root.rglob("*.json"):
        raw_id = path.stem
        if raw_id not in raw_ids:
            continue
        if raw_id in matches:
            raise ValueError("duplicate source JSON for a G04 item")
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
            recording = document["RecordingMetadata"]
            phonemic = recording["phonemic"]
            prompt = _normalize_korean_text(str(recording["prompt"]))
            source_phones = [
                str(record["phone"])
                for record in phonemic["phones"]
                if str(record["phone"]).strip()
            ]
        except (
            KeyError,
            TypeError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as error:
            raise ValueError("invalid AI Hub source JSON schema") from error
        if not prompt or not source_phones:
            raise ValueError("AI Hub source JSON has empty prompt or phones")
        matches[raw_id] = {
            "prompt": prompt,
            "source_phones": source_phones,
            "source_json_sha256": sha256_file(path),
        }
    if set(matches) != raw_ids:
        raise ValueError("G04 source JSON coverage differs from the fixed sample")
    return matches


def _threshold_record(thresholds: G04AudioProxyThresholds) -> dict[str, float]:
    return {
        "minimum_model_confidence": thresholds.minimum_model_confidence,
        "minimum_target_source_similarity": (
            thresholds.minimum_target_source_similarity
        ),
        "minimum_realized_audio_similarity": (
            thresholds.minimum_realized_audio_similarity
        ),
        "minimum_any_audio_similarity": thresholds.minimum_any_audio_similarity,
    }


def _load_or_initialize_state(
    path: Path,
    *,
    source: Mapping[str, Any],
    model: Mapping[str, Any],
    thresholds: Mapping[str, float],
) -> dict[str, Any]:
    expected = {
        "schema_version": STATE_SCHEMA_VERSION,
        "source": dict(source),
        "model": dict(model),
        "thresholds": dict(thresholds),
    }
    if not path.exists():
        return {
            **expected,
            "status": "running",
            "privacy": {
                "zone": "R1_restricted",
                "raw_audio_external_transfer": False,
                "raw_paths_or_identifiers_stored": False,
                "restricted_transcript_stored": True,
            },
            "rows": {},
            "last_error_type": None,
        }
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("existing proxy state must be an object")
    for key, value in expected.items():
        if document.get(key) != value:
            raise ValueError(f"existing proxy state {key} differs")
    rows = document.get("rows")
    if not isinstance(rows, dict):
        raise ValueError("existing proxy state rows must be an object")
    return document


def _aggregate(
    state: Mapping[str, Any],
    *,
    expected_count: int,
    restricted_state_sha256: str,
) -> dict[str, Any]:
    rows = state["rows"]
    fidelity_counts = Counter(row["annotation_fidelity"] for row in rows.values())
    alignment_correct_count = sum(
        bool(row["alignment_correct"]) for row in rows.values()
    )
    processed_count = len(rows)
    completed = processed_count == expected_count
    return {
        "schema_version": AGGREGATE_SCHEMA_VERSION,
        "status": "completed_proxy" if completed else "partial_proxy",
        "expected_count": expected_count,
        "processed_count": processed_count,
        "remaining_count": expected_count - processed_count,
        "annotation_fidelity_counts": {
            key: fidelity_counts.get(key, 0)
            for key in (
                "consistent",
                "target_annotation_mismatch",
                "realized_annotation_mismatch",
                "both_annotations_mismatch",
                "uncertain",
            )
        },
        "uncertain_count": fidelity_counts.get("uncertain", 0),
        "recomputed_alignment_correct_count": alignment_correct_count,
        "recomputed_alignment_accuracy": (
            alignment_correct_count / processed_count if processed_count else 0.0
        ),
        "source": state["source"],
        "model": state["model"],
        "thresholds": state["thresholds"],
        "restricted_state_sha256": restricted_state_sha256,
        "raw_audio_external_transfer": False,
        "substitutes_expert_review": False,
        "canonical_g04_completion_claim_allowed": False,
        "next_gate": "independent_expert_or_approved_contract_amendment",
    }


def main() -> int:
    args = _parser().parse_args()
    if args.limit < 0:
        raise ValueError("--limit must be non-negative")

    root = REPOSITORY_ROOT.resolve(strict=True)
    dataset_root = _absolute(root, args.dataset_root).resolve(strict=True)
    worksheet = _absolute(root, args.worksheet).resolve(strict=True)
    details = _absolute(root, args.details).resolve(strict=True)
    restricted_output = _absolute(root, args.restricted_output)
    aggregate_output = _absolute(root, args.aggregate_output)
    nltk_data_root = _absolute(root, args.nltk_data_root).resolve(strict=True)

    dataset = ReviewDataset.load(
        worksheet=worksheet,
        details=details,
        dataset_root=dataset_root,
    )
    expected_count = len(dataset.items)
    selected_count = (
        expected_count if args.limit == 0 else min(args.limit, expected_count)
    )

    source_metadata = _discover_source_metadata(
        dataset_root,
        {item.raw_utterance_id for item in dataset.items},
    )
    frontend = SubprocessG2PKExpectedPhoneFrontend(
        nltk_data_root=nltk_data_root,
        repository_root=root,
        word_boundary_policy="drop",
        allowed_punctuation=(".", "?"),
        maximum_codepoints=G04_MAXIMUM_PROMPT_CODEPOINTS,
    )
    prompt_texts = [
        source_metadata[item.raw_utterance_id]["prompt"] for item in dataset.items
    ]
    prompt_phone_sequences = frontend.expected_phones_batch(prompt_texts)

    auditor = QwenAudioAuditor(
        repository_root=root,
        device=args.device,
        dtype=args.dtype,
    )
    model = {
        "backend_id": auditor.backend_id,
        "revision": auditor.revision,
        "device": args.device,
        "dtype": args.dtype,
        "fine_tuned_for_g04": False,
    }
    thresholds = G04AudioProxyThresholds(
        minimum_model_confidence=args.minimum_model_confidence,
        minimum_target_source_similarity=(args.minimum_target_source_similarity),
        minimum_realized_audio_similarity=(args.minimum_realized_audio_similarity),
        minimum_any_audio_similarity=args.minimum_any_audio_similarity,
    )
    source = {
        "worksheet_sha256": dataset.worksheet_sha256,
        "details_sha256": dataset.details_sha256,
        "expected_count": expected_count,
    }
    state = _load_or_initialize_state(
        restricted_output,
        source=source,
        model=model,
        thresholds=_threshold_record(thresholds),
    )

    started = time.monotonic()
    try:
        for item, prompt_sequence in zip(
            dataset.items[:selected_count],
            prompt_phone_sequences[:selected_count],
            strict=True,
        ):
            if item.item_sha256 in state["rows"]:
                continue
            metadata = source_metadata[item.raw_utterance_id]
            reply = auditor.transcribe(item.audio_path)
            transcript = _normalize_korean_text(reply.text)
            if transcript:
                heard_sequence = frontend.expected_phones(transcript)
                heard_phones = heard_sequence.tokens
                transcript_phone_sha256 = heard_sequence.token_sha256
            else:
                heard_phones = ()
                transcript_phone_sha256 = None

            target_phones = [
                str(record["label"]) for record in item.target_phone_intervals
            ]
            realized_phones = [
                str(record["label"]) for record in item.realized_phone_intervals
            ]
            source_phones = metadata["source_phones"]
            prompt_phones = prompt_sequence.tokens
            target_source_similarity = min(
                normalized_sequence_similarity(target_phones, source_phones),
                normalized_sequence_similarity(target_phones, prompt_phones),
            )
            target_audio_similarity = normalized_sequence_similarity(
                target_phones,
                heard_phones,
            )
            realized_audio_similarity = normalized_sequence_similarity(
                realized_phones,
                heard_phones,
            )
            decision = classify_annotation_fidelity(
                target_source_similarity=target_source_similarity,
                target_audio_similarity=target_audio_similarity,
                realized_audio_similarity=realized_audio_similarity,
                model_confidence=reply.mean_token_probability,
                thresholds=thresholds,
            )
            recomputed = recompute_alignment_records(
                item.target_phone_intervals,
                item.realized_phone_intervals,
            )
            error_count = alignment_record_error_count(
                item.automatic_phone_alignments,
                recomputed,
            )
            state["rows"][item.item_sha256] = {
                "audio_sha256": sha256_file(item.audio_path),
                "source_json_sha256": metadata["source_json_sha256"],
                "prompt_sha256": hashlib.sha256(
                    metadata["prompt"].encode("utf-8")
                ).hexdigest(),
                "prompt_phone_sha256": prompt_sequence.token_sha256,
                "source_phone_sha256": _sha256_json(
                    normalize_phone_sequence(source_phones)
                ),
                "target_phone_sha256": _sha256_json(
                    normalize_phone_sequence(target_phones)
                ),
                "realized_phone_sha256": _sha256_json(
                    normalize_phone_sequence(realized_phones)
                ),
                "restricted_transcript": transcript,
                "transcript_sha256": hashlib.sha256(
                    transcript.encode("utf-8")
                ).hexdigest(),
                "transcript_phone_sha256": transcript_phone_sha256,
                "model_confidence": reply.mean_token_probability,
                "target_source_similarity": target_source_similarity,
                "target_audio_similarity": target_audio_similarity,
                "realized_audio_similarity": realized_audio_similarity,
                "annotation_fidelity": decision.annotation_fidelity.value,
                "abstained": decision.abstained,
                "reason": decision.reason,
                "alignment_correct": error_count == 0,
                "incorrect_interval_count": error_count,
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            state["status"] = "running"
            state["last_error_type"] = None
            atomic_write_json(restricted_output, state)
            elapsed = round(time.monotonic() - started, 1)
            print(
                json.dumps(
                    {
                        "status": "running",
                        "processed_count": len(state["rows"]),
                        "selected_count": selected_count,
                        "elapsed_seconds": elapsed,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    except Exception as error:
        state["status"] = "failed_partial"
        state["last_error_type"] = type(error).__name__
        atomic_write_json(restricted_output, state)
        raise

    state["status"] = (
        "completed_proxy" if len(state["rows"]) == expected_count else "partial_proxy"
    )
    atomic_write_json(restricted_output, state)
    aggregate = _aggregate(
        state,
        expected_count=expected_count,
        restricted_state_sha256=sha256_file(restricted_output),
    )
    atomic_write_json(aggregate_output, aggregate)
    print(json.dumps(aggregate, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "canonical_g04_completion_claim_allowed": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise
