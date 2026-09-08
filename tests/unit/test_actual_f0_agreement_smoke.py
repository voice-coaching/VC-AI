from __future__ import annotations

import json
from pathlib import Path

import pytest

from voice_coach.evaluation.pitch_contour_agreement import (
    PitchContourSeries,
    PitchEndpointPolicy,
    PitchInterpolationPolicy,
    PitchUnvoicedPolicy,
    evaluate_pitch_contour_agreement,
)
from voice_coach.experiments.actual_f0_agreement_smoke import (
    ActualF0AgreementSmokeError,
    MAXIMUM_SAMPLE_SIZE,
    ModelingManifestMetadataRow,
    aggregate_pitch_agreements,
    attach_payload_sha256,
    select_project_dev_validation_audio,
    verify_payload_sha256,
)


def _inventory_root(tmp_path: Path, *, count: int = 12) -> Path:
    root = (tmp_path / "dataset").resolve()
    source = root / "원천데이터" / "Validation" / "extracted_pronunciation_audio"
    source.mkdir(parents=True)
    for index in range(count):
        (source / f"private-source-{index:02d}.wav").write_bytes(
            f"content-{index}".encode()
        )
    return root


def _manifest_rows(
    root: Path,
    *,
    splits: tuple[str, ...] | None = None,
) -> tuple[ModelingManifestMetadataRow, ...]:
    source = root / "원천데이터" / "Validation" / "extracted_pronunciation_audio"
    paths = tuple(sorted(source.glob("private-source-*.wav")))
    normalized_splits = splits or ("dev",) * len(paths)
    assert len(normalized_splits) == len(paths)
    return tuple(
        ModelingManifestMetadataRow(
            audio_path=path.relative_to(root).as_posix(),
            official_partitions=("Validation",),
            pcm_sha256=f"{index + 1:064x}",
            split=split,
        )
        for index, (path, split) in enumerate(
            zip(paths, normalized_splits, strict=True)
        )
    )


def _select(
    root: Path,
    *,
    seed: int,
    sample_size: int,
    rows: tuple[ModelingManifestMetadataRow, ...] | None = None,
    manifest_sha256: str = "a" * 64,
):
    return select_project_dev_validation_audio(
        root,
        manifest_metadata_rows=(rows if rows is not None else _manifest_rows(root)),
        manifest_sha256=manifest_sha256,
        seed=seed,
        sample_size=sample_size,
    )


def _agreement(
    reference_f0: tuple[float, ...],
    candidate_f0: tuple[float, ...],
):
    times = tuple(index * 0.01 for index in range(len(reference_f0)))
    return evaluate_pitch_contour_agreement(
        PitchContourSeries(times, reference_f0),
        PitchContourSeries(times, candidate_f0),
        times,
        interpolation_policy=PitchInterpolationPolicy.LINEAR,
        endpoint_policy=PitchEndpointPolicy.TRIM_TO_SHARED_SUPPORT,
        unvoiced_policy=PitchUnvoicedPolicy.PRESERVE_GAPS,
        octave_tolerance_cents=50.0,
        max_frame_count=100,
    )


def test_filename_selection_is_deterministic_and_content_independent(
    tmp_path: Path,
) -> None:
    root = _inventory_root(tmp_path)

    first = _select(root, seed=20_260_729, sample_size=4)
    for path in first.selected_paths:
        path.write_bytes(b"changed-after-filename-selection")
    repeated = _select(root, seed=20_260_729, sample_size=4)

    assert repeated.selected_paths == first.selected_paths
    assert (
        repeated.official_inventory_digest_sha256
        == first.official_inventory_digest_sha256
    )
    assert (
        repeated.project_dev_inventory_digest_sha256
        == first.project_dev_inventory_digest_sha256
    )
    assert repeated.selection_digest_sha256 == first.selection_digest_sha256
    assert first.official_filename_count == 12
    assert first.eligible_regular_file_count == 12
    assert first.rejected_inventory_entry_count == 0


def test_seed_is_bound_into_selection_digest_even_if_sample_size_is_one(
    tmp_path: Path,
) -> None:
    root = _inventory_root(tmp_path)

    first = _select(root, seed=1, sample_size=1)
    second = _select(root, seed=2, sample_size=1)

    assert first.selection_digest_sha256 != second.selection_digest_sha256


def test_inventory_scope_excludes_training_speech_and_non_wav(tmp_path: Path) -> None:
    root = _inventory_root(tmp_path, count=2)
    extras = root / "원천데이터" / "Training" / "extracted_pronunciation_audio"
    extras.mkdir(parents=True)
    (extras / "training.wav").write_bytes(b"x")
    speech = root / "원천데이터" / "Validation" / "extracted_speech_audio"
    speech.mkdir(parents=True)
    (speech / "speech.wav").write_bytes(b"x")
    valid_source = root / "원천데이터" / "Validation" / "extracted_pronunciation_audio"
    (valid_source / "label.json").write_text("{}", encoding="utf-8")

    result = _select(root, seed=7, sample_size=2)

    assert result.official_filename_count == 2
    assert result.eligible_regular_file_count == 2


def test_symlink_filename_is_counted_then_rejected(tmp_path: Path) -> None:
    root = _inventory_root(tmp_path, count=2)
    source = root / "원천데이터" / "Validation" / "extracted_pronunciation_audio"
    (source / "linked.wav").symlink_to(source / "private-source-00.wav")

    result = _select(root, seed=7, sample_size=2)

    assert result.official_filename_count == 3
    assert result.eligible_regular_file_count == 2
    assert result.rejected_inventory_entry_count == 1


@pytest.mark.parametrize(
    ("seed", "sample_size", "message"),
    [
        (0, 1, "seed"),
        (True, 1, "seed"),
        (1, 0, "sample_size"),
        (1, MAXIMUM_SAMPLE_SIZE + 1, "sample_size"),
    ],
)
def test_selection_rejects_invalid_resource_contracts(
    tmp_path: Path,
    seed: int,
    sample_size: int,
    message: str,
) -> None:
    root = _inventory_root(tmp_path)

    with pytest.raises(ActualF0AgreementSmokeError, match=message):
        _select(root, seed=seed, sample_size=sample_size)


def test_selection_rejects_relative_or_symlink_dataset_root(tmp_path: Path) -> None:
    root = _inventory_root(tmp_path)
    linked = tmp_path / "linked-dataset"
    linked.symlink_to(root, target_is_directory=True)

    with pytest.raises(ActualF0AgreementSmokeError, match="absolute"):
        _select(Path("relative"), seed=1, sample_size=1)
    with pytest.raises(ActualF0AgreementSmokeError, match="real directory"):
        _select(linked, seed=1, sample_size=1)


def test_selection_uses_project_dev_only_before_audio_read(tmp_path: Path) -> None:
    root = _inventory_root(tmp_path, count=6)
    rows = _manifest_rows(
        root,
        splits=("locked_test", "train", "calibration", "dev", "dev", "stack"),
    )

    result = _select(root, seed=7, sample_size=2, rows=rows)

    selected_names = {path.name for path in result.selected_paths}
    assert selected_names == {"private-source-03.wav", "private-source-04.wav"}
    assert result.manifest_metadata_row_count == 6
    assert dict(result.official_inventory_project_split_counts) == {
        "calibration": 1,
        "dev": 2,
        "locked_test": 1,
        "stack": 1,
        "train": 1,
    }
    assert result.project_dev_eligible_count == 2
    assert all(
        source.expected_pcm_sha256 in {rows[3].pcm_sha256, rows[4].pcm_sha256}
        for source in result.selected_sources
    )


def test_selection_fails_closed_on_non_unique_manifest_join(
    tmp_path: Path,
) -> None:
    root = _inventory_root(tmp_path, count=2)
    rows = _manifest_rows(root)

    with pytest.raises(ActualF0AgreementSmokeError, match="one-to-one"):
        _select(root, seed=7, sample_size=1, rows=rows + (rows[0],))


def test_selection_digest_binds_pinned_manifest(tmp_path: Path) -> None:
    root = _inventory_root(tmp_path, count=2)

    first = _select(root, seed=7, sample_size=1, manifest_sha256="a" * 64)
    second = _select(root, seed=7, sample_size=1, manifest_sha256="b" * 64)

    assert first.selection_digest_sha256 != second.selection_digest_sha256


def test_aggregate_pools_frame_weighted_statistics() -> None:
    first = _agreement((100.0, 0.0, 200.0), (110.0, 110.0, 220.0))
    second = _agreement((100.0, 100.0), (90.0, 0.0))

    result = aggregate_pitch_agreements((first, second))

    assert result is not None
    assert result.comparison_completed_count == 2
    assert result.evaluated_common_frame_count == 5
    assert result.jointly_voiced_frame_count == 3
    assert result.parselmouth_only_voiced_frame_count == 1
    assert result.torchcrepe_only_voiced_frame_count == 1
    assert result.jointly_unvoiced_frame_count == 0
    assert result.pooled_voicing_agreement_rate == pytest.approx(3.0 / 5.0)
    assert result.pooled_jointly_voiced_rate == pytest.approx(3.0 / 5.0)
    assert result.pooled_mean_torchcrepe_minus_parselmouth_hz == pytest.approx(
        20.0 / 3.0
    )
    assert result.pooled_mean_absolute_hz_error == pytest.approx(40.0 / 3.0)
    assert result.pooled_root_mean_squared_hz_error == pytest.approx(
        (600.0 / 3.0) ** 0.5
    )
    assert result.maximum_absolute_hz_error == 20.0


def test_empty_aggregate_is_explicitly_none() -> None:
    assert aggregate_pitch_agreements(()) is None


def test_aggregate_rejects_loose_collection() -> None:
    with pytest.raises(ActualF0AgreementSmokeError, match="exact tuple"):
        aggregate_pitch_agreements([])  # type: ignore[arg-type]


def test_payload_digest_round_trip_and_tamper_detection() -> None:
    report = attach_payload_sha256(
        {
            "schema_version": "synthetic.aggregate.v1",
            "counts": {"selected": 4},
        }
    )

    assert verify_payload_sha256(report)
    report["counts"] = {"selected": 5}
    assert not verify_payload_sha256(report)


def test_payload_digest_rejects_nan_and_existing_digest() -> None:
    with pytest.raises(ActualF0AgreementSmokeError, match="canonical JSON"):
        attach_payload_sha256({"metric": float("nan")})
    with pytest.raises(ActualF0AgreementSmokeError, match="already"):
        attach_payload_sha256({"payload_sha256": "0" * 64})


def test_selection_public_evidence_contains_no_filename(tmp_path: Path) -> None:
    root = _inventory_root(tmp_path)
    selection = _select(root, seed=7, sample_size=4)
    public = {
        "official_filename_count": selection.official_filename_count,
        "official_inventory_digest_sha256": (
            selection.official_inventory_digest_sha256
        ),
        "project_dev_inventory_digest_sha256": (
            selection.project_dev_inventory_digest_sha256
        ),
        "selection_digest_sha256": selection.selection_digest_sha256,
    }
    serialized = json.dumps(public, sort_keys=True)

    for index in range(12):
        assert f"private-source-{index:02d}" not in serialized
