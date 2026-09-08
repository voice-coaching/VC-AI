from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import soundfile as sf

from voice_coach.data.adversarial_speech import (
    AdversarialSpeechDataError,
    AdversarialSpeechPair,
    DurationMatchedPairSampler,
    LearnerSpeechRecord,
    NativeAudioResolver,
    NativeSpeechRecord,
    PHONE_TO_TOKEN_ID,
    UNKNOWN_TEXT_TOKEN_ID,
    collate_adversarial_pairs,
    load_aihub_learner_records,
    load_native_speech_records,
    native_policy_from_catalog,
    parse_embedded_audio_reference,
)
from voice_coach.training.adversarial_tts import validate_adversarial_batch


def _audio_bytes(
    *,
    sample_rate: int = 8_000,
    seconds: float = 0.1,
) -> bytes:
    stream = io.BytesIO()
    samples = np.linspace(-0.1, 0.1, round(sample_rate * seconds), dtype=np.float32)
    sf.write(stream, samples, sample_rate, format="WAV", subtype="PCM_16")
    return stream.getvalue()


def _write_audio(
    path: Path,
    *,
    sample_rate: int = 16_000,
    seconds: float = 0.1,
    audio_format: str = "WAV",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = np.linspace(-0.1, 0.1, round(sample_rate * seconds), dtype=np.float32)
    sf.write(path, samples, sample_rate, format=audio_format)


def _phone_intervals(*labels: str) -> list[dict[str, object]]:
    return [
        {"start": index * 0.02, "end": (index + 1) * 0.02, "label": label}
        for index, label in enumerate(labels)
    ]


def _learner_row(
    *,
    row_id: str,
    audio_path: str,
    split: str = "train",
    prompt_hash: str = "1" * 64,
    qc_flags: list[str] | None = None,
    target_labels: tuple[str, ...] = ("ㄱ", "ㅏ"),
    fluency_score: int | None = None,
) -> dict[str, object]:
    return {
        "id": row_id,
        "split": split,
        "audio_path": audio_path,
        "duration_seconds": 0.1,
        "l1": "audit-only-l1",
        "gender": "audit-only-gender",
        "proficiency": "audit-only-proficiency",
        "error_presence": True,
        "target_phone_intervals": _phone_intervals(*target_labels),
        "phone_alignments": [
            {"operation": "match"},
            {"operation": "substitution"},
        ],
        "qc_flags": qc_flags or [],
        "prompt_sha256": prompt_hash,
        "pronunciation_score": 5,
        "fluency_score": fluency_score,
        "comprehensibility_score": 3,
    }


def _reservation(path: Path, reserved_hash: str = "2" * 64) -> None:
    path.write_text(
        json.dumps({"reserved_prompts": [{"prompt_sha256": reserved_hash}]}),
        encoding="utf-8",
    )


def test_aihub_loader_filters_before_decode_and_masks_missing_scores(
    tmp_path: Path,
) -> None:
    root = tmp_path / "aihub"
    for name in ("eligible.wav", "flagged.wav", "reserved.wav", "dev.wav"):
        _write_audio(root / name)
    manifest = tmp_path / "learner.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                _learner_row(row_id="eligible", audio_path="eligible.wav"),
                _learner_row(
                    row_id="flagged",
                    audio_path="flagged.wav",
                    qc_flags=["bad"],
                ),
                _learner_row(
                    row_id="reserved",
                    audio_path="reserved.wav",
                    prompt_hash="2" * 64,
                ),
                _learner_row(
                    row_id="dev",
                    audio_path="dev.wav",
                    split="dev",
                ),
                _learner_row(
                    row_id="empty",
                    audio_path="missing-is-never-opened.wav",
                    target_labels=(),
                ),
            ]
        ),
        manifest,
    )
    reservation = tmp_path / "reservation.json"
    _reservation(reservation)

    result = load_aihub_learner_records(
        manifest,
        dataset_root=root,
        prompt_reservation_path=reservation,
    )

    assert result.source_rows == 5
    assert result.exclusion_counts() == {
        "empty_target_phone_sequence": 1,
        "qc_flags_present": 1,
        "reserved_prompt": 1,
        "split_not_allowed": 1,
    }
    assert len(result.records) == 1
    record = result.records[0]
    assert record.text_tokens == (
        PHONE_TO_TOKEN_ID["ㄱ"],
        PHONE_TO_TOKEN_ID["ㅏ"],
    )
    # phone accuracy=.5 and pronunciation=1.0 -> segmental=.75;
    # comprehensibility=.5; missing fluency and unavailable prosody stay masked.
    assert record.aspect_targets == pytest.approx((0.75, 0.0, 0.0, 0.5))
    assert record.aspect_mask == (True, False, False, True)
    assert record.quality_target == pytest.approx(0.625)


def test_aihub_loader_rejects_path_escape_instead_of_skipping_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "aihub"
    root.mkdir()
    manifest = tmp_path / "learner.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [_learner_row(row_id="escape", audio_path="../outside.wav")]
        ),
        manifest,
    )
    reservation = tmp_path / "reservation.json"
    _reservation(reservation)

    with pytest.raises(AdversarialSpeechDataError, match="escapes"):
        load_aihub_learner_records(
            manifest,
            dataset_root=root,
            prompt_reservation_path=reservation,
        )


def _native_manifest(
    path: Path,
    *,
    kss_duration: float = 0.1,
    zeroth_duration: float = 0.1,
) -> None:
    kss_policy = native_policy_from_catalog("kss")
    zeroth_policy = native_policy_from_catalog("zeroth-korean")
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "stable_id": "kss:one",
                    "source_id": "kss",
                    "audio_storage": "parquet_embedded",
                    "relative_path": None,
                    "embedded_reference": (
                        "train-00000-of-00001.parquet#row=0:audio.bytes"
                    ),
                    "sample_rate_hz": 8_000,
                    "channels": 1,
                    "duration_seconds": kss_duration,
                    "split": "train",
                    "license_id": kss_policy.license_id,
                    "noncommercial_only": kss_policy.noncommercial_only,
                    "intended_role": kss_policy.intended_role,
                    "access_scope": "restricted_internal",
                },
                {
                    "stable_id": "zeroth:one",
                    "source_id": "zeroth-korean",
                    "audio_storage": "relative_file",
                    "relative_path": "extracted/example.flac",
                    "embedded_reference": None,
                    "sample_rate_hz": 16_000,
                    "channels": 1,
                    "duration_seconds": zeroth_duration,
                    "split": "train",
                    "license_id": zeroth_policy.license_id,
                    "noncommercial_only": zeroth_policy.noncommercial_only,
                    "intended_role": zeroth_policy.intended_role,
                    "access_scope": "restricted_internal",
                },
            ]
        ),
        path,
    )


def test_native_loader_and_resolver_handle_embedded_and_file_audio(
    tmp_path: Path,
) -> None:
    kss_root = tmp_path / "kss"
    kss_root.mkdir()
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "audio": {"bytes": _audio_bytes(), "path": None},
                    "original_script": "not returned by the loader",
                }
            ]
        ),
        kss_root / "train-00000-of-00001.parquet",
    )
    zeroth_root = tmp_path / "zeroth"
    _write_audio(
        zeroth_root / "extracted" / "example.flac",
        audio_format="FLAC",
    )
    manifest = tmp_path / "native.parquet"
    _native_manifest(manifest)

    loaded = load_native_speech_records(
        manifest,
        policies={
            "kss": native_policy_from_catalog("kss"),
            "zeroth-korean": native_policy_from_catalog("zeroth-korean"),
        },
        native_tokens_by_id={"zeroth:one": (1, 2, 3)},
    )

    assert loaded.source_rows == 2
    assert loaded.exclusion_counts() == {}
    assert loaded.records[0].text_tokens == (UNKNOWN_TEXT_TOKEN_ID,)
    assert loaded.records[0].content_supervised is False
    assert loaded.records[0].aspect_mask == (False, False, False, False)
    assert loaded.records[1].text_tokens == (1, 2, 3)
    assert loaded.records[1].content_supervised is True

    resolver = NativeAudioResolver({"kss": kss_root, "zeroth-korean": zeroth_root})
    decoded = resolver.decode_many(loaded.records)
    assert [item.sample_rate for item in decoded] == [16_000, 16_000]
    assert [item.samples.size for item in decoded] == [1_600, 1_600]


def test_embedded_reference_and_native_policy_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(AdversarialSpeechDataError, match="escapes"):
        parse_embedded_audio_reference("../escape.parquet#row=0:audio.bytes")

    manifest = tmp_path / "native.parquet"
    _native_manifest(manifest)
    table = pq.read_table(manifest).to_pylist()
    table[0]["license_id"] = "wrong-license"
    pq.write_table(pa.Table.from_pylist(table), manifest)

    with pytest.raises(AdversarialSpeechDataError, match="policy mismatch"):
        load_native_speech_records(
            manifest,
            policies={"kss": native_policy_from_catalog("kss")},
        )


def _learner_record(
    name: str,
    *,
    audio_path: Path,
    duration: float,
    stratum: str,
) -> LearnerSpeechRecord:
    return LearnerSpeechRecord(
        stable_id=name,
        audio_path=audio_path,
        duration_seconds=duration,
        text_tokens=(1, 2),
        quality_target=0.5,
        aspect_targets=(0.5, 0.0, 0.0, 0.5),
        aspect_mask=(True, False, False, True),
        content_supervised=True,
        l1_group=stratum,
        gender_group=stratum,
        proficiency_group=stratum,
        error_presence=True,
    )


def _native_record(
    name: str,
    *,
    duration: float,
    relative_path: str,
    tokens: tuple[int, ...] = (UNKNOWN_TEXT_TOKEN_ID,),
) -> NativeSpeechRecord:
    return NativeSpeechRecord(
        stable_id=name,
        source_id="zeroth-korean",
        audio_storage="relative_file",
        relative_path=relative_path,
        embedded_reference=None,
        duration_seconds=duration,
        sample_rate_hz=16_000,
        channels=1,
        text_tokens=tokens,
        quality_target=1.0,
        aspect_targets=(0.0, 0.0, 0.0, 0.0),
        aspect_mask=(False, False, False, False),
        content_supervised=tokens != (UNKNOWN_TEXT_TOKEN_ID,),
    )


def test_duration_sampler_is_deterministic_and_uses_nearest_bucket(
    tmp_path: Path,
) -> None:
    learners = [
        _learner_record(
            f"learner-{index}",
            audio_path=tmp_path / f"{index}.wav",
            duration=duration,
            stratum=str(index % 2),
        )
        for index, duration in enumerate((1.0, 1.5, 5.0, 5.5))
    ]
    natives = [
        _native_record("short", duration=1.2, relative_path="short.flac"),
        _native_record("long", duration=5.2, relative_path="long.flac"),
    ]
    sampler = DurationMatchedPairSampler(
        learners,
        natives,
        seed=7,
        duration_boundaries=(2.0, 4.0),
    )

    first = list(sampler.iter_epoch(3))
    second = list(sampler.iter_epoch(3))
    assert [(pair.learner.stable_id, pair.native.stable_id) for pair in first] == [
        (pair.learner.stable_id, pair.native.stable_id) for pair in second
    ]
    assert all(
        (pair.learner.duration_seconds < 2) == (pair.native.stable_id == "short")
        for pair in first
    )


def test_collate_builds_explicit_missing_label_and_native_content_masks(
    tmp_path: Path,
) -> None:
    learner_paths = [tmp_path / "learner-0.wav", tmp_path / "learner-1.wav"]
    native_root = tmp_path / "zeroth"
    for path in learner_paths:
        _write_audio(path, seconds=0.08)
    for name in ("native-0.flac", "native-1.flac"):
        _write_audio(
            native_root / name,
            seconds=0.1,
            audio_format="FLAC",
        )
    learners = [
        _learner_record(
            f"learner-{index}",
            audio_path=path,
            duration=0.08,
            stratum=str(index),
        )
        for index, path in enumerate(learner_paths)
    ]
    natives = [
        _native_record(
            "native-0",
            duration=0.1,
            relative_path="native-0.flac",
        ),
        _native_record(
            "native-1",
            duration=0.1,
            relative_path="native-1.flac",
            tokens=(3, 4, 5),
        ),
    ]
    pairs = [
        AdversarialSpeechPair(learner=learner, native=native)
        for learner, native in zip(learners, natives, strict=True)
    ]

    batch = collate_adversarial_pairs(
        pairs,
        native_audio_resolver=NativeAudioResolver({"zeroth-korean": native_root}),
    )

    validate_adversarial_batch(batch, aspect_count=4)
    assert batch.learner_waveform.shape == (2, 1_280)
    assert batch.native_waveform.shape == (2, 1_600)
    assert batch.learner_aspect_mask is not None
    assert batch.learner_aspect_mask.tolist() == [
        [True, False, False, True],
        [True, False, False, True],
    ]
    assert batch.native_aspect_mask is not None
    assert not batch.native_aspect_mask.any()
    assert batch.native_content_mask is not None
    assert batch.native_content_mask.tolist() == [False, True]
