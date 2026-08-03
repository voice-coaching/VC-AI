from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from voice_coach.features.gop import PHONE_INVENTORY, assign_speaker_fold
from voice_coach.training.b07_gop_contract import (
    ARTIFACT_LAYOUT,
    load_config,
    sha256_file,
)
from voice_coach.training.b07_gop_training import (
    B07TrainingError,
    ParquetEmbeddingSource,
    _accumulate_prototype_statistics,
    _prototypes_excluding,
    _seed_folds,
    build_synthetic_input_bundle,
    run_b07_training,
    validate_b07_commit,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONFIG_RELATIVE = Path("configs/experiments/b07_gop_acoustic_posterior_v1.yaml")


def _unique_speakers_covering_every_seed_fold() -> list[str]:
    output: list[str] = []
    counter = 0
    for seed in (42, 43, 44):
        for fold in range(5):
            while True:
                candidate = hashlib.sha256(
                    f"synthetic-speaker-{counter}".encode()
                ).hexdigest()
                counter += 1
                if candidate in output:
                    continue
                if assign_speaker_fold(candidate, seed=seed) == fold:
                    output.append(candidate)
                    break
    return output


def _synthetic_rows() -> tuple[pa.Table, np.ndarray]:
    speakers = _unique_speakers_covering_every_seed_fold()
    rows: list[dict[str, Any]] = []
    embeddings: list[np.ndarray] = []

    def append_row(
        *,
        split: str,
        speaker: str,
        target_index: int,
        error: int,
        l1: str,
        gender: str,
        row_tag: str,
    ) -> None:
        target = PHONE_INVENTORY[target_index]
        row_index = len(rows)
        rows.append(
            {
                "utterance_id_sha256": hashlib.sha256(
                    f"synthetic-utterance-{row_tag}-{row_index}".encode()
                ).hexdigest(),
                "speaker_id_sha256": speaker,
                "split": split,
                "phone_index": 0,
                "target_label": target,
                "binary_error_label": error,
                "previous_target_label": (
                    PHONE_INVENTORY[(target_index - 1) % len(PHONE_INVENTORY)]
                    if target_index % 7
                    else None
                ),
                "next_target_label": (
                    PHONE_INVENTORY[(target_index + 1) % len(PHONE_INVENTORY)]
                    if target_index % 11
                    else None
                ),
                "oracle_duration_seconds": 0.04 + 0.001 * (target_index % 9),
                "segment_rms": 0.1 + 0.003 * (target_index % 13) + 0.02 * error,
                "segment_zero_crossing_rate": (
                    0.03 + 0.002 * (target_index % 10) + 0.01 * error
                ),
                "l1": l1,
                "gender": gender,
                "proficiency": f"P{1 + target_index % 3}",
            }
        )
        vector = np.zeros(768, dtype=np.float32)
        realized_index = (
            (target_index + 1) % len(PHONE_INVENTORY) if error else target_index
        )
        vector[realized_index] = 1.0
        vector[64 + (row_index % 32)] = 0.01
        embeddings.append(vector)

    for speaker_index, speaker in enumerate(speakers):
        for target_index in range(len(PHONE_INVENTORY)):
            append_row(
                split="train",
                speaker=speaker,
                target_index=target_index,
                error=0,
                l1="Chinese" if speaker_index % 2 == 0 else "Japanese",
                gender="F" if speaker_index % 2 == 0 else "M",
                row_tag=f"train-match-{speaker_index}",
            )
        for error_offset in range(2):
            append_row(
                split="train",
                speaker=speaker,
                target_index=(speaker_index * 3 + error_offset) % 38,
                error=1,
                l1="Chinese" if speaker_index % 2 == 0 else "Japanese",
                gender="F" if speaker_index % 2 == 0 else "M",
                row_tag=f"train-error-{speaker_index}",
            )

    dev_speaker = hashlib.sha256(b"synthetic-dev-speaker").hexdigest()
    for target_index in range(len(PHONE_INVENTORY)):
        for error in (0, 1):
            append_row(
                split="dev",
                speaker=dev_speaker,
                target_index=target_index,
                error=error,
                l1="Chinese" if target_index % 2 == 0 else "Japanese",
                gender="F" if target_index % 2 == 0 else "M",
                row_tag="dev-primary",
            )
    append_row(
        split="dev",
        speaker=dev_speaker,
        target_index=0,
        error=0,
        l1="Korean",
        gender="F",
        row_tag="dev-nonprimary",
    )
    return pa.Table.from_pylist(rows), np.asarray(embeddings, dtype=np.float32)


def _copy_static_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    source_config = REPOSITORY_ROOT / CONFIG_RELATIVE
    config = load_config(source_config)
    destinations = [CONFIG_RELATIVE]
    destinations.extend(
        Path(record["path"]) for record in config["canonical_sources"].values()
    )
    for relative in destinations:
        source = REPOSITORY_ROOT / relative
        destination = workspace / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return workspace


def _tree_fingerprints(root: Path) -> dict[str, tuple[int, str]]:
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_mtime_ns,
            sha256_file(path),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_parquet_embedding_source_uses_bounded_arrow_batches(tmp_path: Path) -> None:
    dimension = 4
    identifiers = ["id-a", "id-b", "id-c"]
    phone_indexes = [0, 1, 2]
    splits = ["train", "train", "dev"]
    matrix = np.arange(12, dtype=np.float32).reshape(3, dimension)
    embeddings = pa.FixedSizeListArray.from_arrays(
        pa.array(matrix.reshape(-1)),
        dimension,
    )
    table = pa.table(
        {
            "utterance_id_sha256": identifiers,
            "phone_index": phone_indexes,
            "split": splits,
            "embedding_mean": embeddings,
        }
    )
    shard_path = tmp_path / "part-00000.parquet"
    pq.write_table(table, shard_path)
    metadata = table.select(["utterance_id_sha256", "phone_index", "split"])
    source = ParquetEmbeddingSource(
        paths=[shard_path],
        metadata_table=metadata,
        dimension=dimension,
        batch_rows=1,
    )

    batches = list(source.iter_split("train"))

    assert [indexes.tolist() for indexes, _ in batches] == [[0], [1]]
    assert np.array_equal(
        np.vstack([block for _, block in batches]),
        matrix[:2],
    )


def test_nested_exclusion_has_no_outer_fold_numeric_dependence() -> None:
    table, embeddings = _synthetic_rows()
    bundle = build_synthetic_input_bundle(
        table=table,
        embeddings=embeddings,
        batch_rows=37,
    )
    folds = _seed_folds(bundle.arrays, 42)
    statistics = _accumulate_prototype_statistics(bundle, folds)
    outer_fold = 2
    baseline = _prototypes_excluding(statistics, (outer_fold,))

    changed = embeddings.copy()
    outer_indexes = np.flatnonzero(folds == outer_fold)
    changed[outer_indexes] = 0.0
    for offset, row_index in enumerate(outer_indexes):
        changed[row_index, (offset * 17 + 9) % 128] = -1.0 if offset % 2 else 1.0
    changed_bundle = build_synthetic_input_bundle(
        table=table,
        embeddings=changed,
        batch_rows=37,
    )
    changed_statistics = _accumulate_prototype_statistics(changed_bundle, folds)
    changed_prototypes = _prototypes_excluding(
        changed_statistics,
        (outer_fold,),
    )

    assert np.array_equal(baseline, changed_prototypes)


def test_synthetic_e2e_fault_recovery_tamper_and_read_only_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    workspace = _copy_static_workspace(tmp_path)
    config_path = workspace / CONFIG_RELATIVE
    table, embeddings = _synthetic_rows()
    bundle = build_synthetic_input_bundle(
        table=table,
        embeddings=embeddings,
        batch_rows=41,
    )
    config = load_config(config_path)
    artifacts = config["artifacts"]
    registry_path = workspace / artifacts["registry_path"]
    commit_path = workspace / artifacts["commit_path"]

    with pytest.raises(B07TrainingError, match="injected fault"):
        run_b07_training(
            workspace_root=workspace,
            config_path=config_path,
            execute_canonical=False,
            input_bundle=bundle,
            allow_synthetic_bundle=True,
            fault_stage="after_generation",
        )
    failed_registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert failed_registry["status"] == "failed"
    orphan_generation = failed_registry["generation_id"]
    assert isinstance(orphan_generation, str)
    assert not commit_path.exists()

    recovered = run_b07_training(
        workspace_root=workspace,
        config_path=config_path,
        execute_canonical=False,
        input_bundle=bundle,
        allow_synthetic_bundle=True,
    )
    assert recovered["status"] == "valid"
    assert recovered["generation_id"] == orphan_generation
    generation = workspace / artifacts["generation_root"] / recovered["generation_id"]
    assert {
        path.relative_to(generation).as_posix()
        for path in generation.rglob("*")
        if path.is_file()
    } == set(ARTIFACT_LAYOUT)
    assert json.loads(registry_path.read_text(encoding="utf-8"))["status"] == (
        "completed"
    )
    assert commit_path.is_file()

    before = _tree_fingerprints(workspace / "artifacts")
    validated = validate_b07_commit(
        workspace_root=workspace,
        config_path=config_path,
        input_bundle=bundle,
        allow_synthetic_bundle=True,
    )
    after = _tree_fingerprints(workspace / "artifacts")
    assert validated["status"] == "valid"
    assert validated["read_only"] is True
    assert validated["full_train_selection_dev_recomputed"] is True
    assert before == after

    metric_path = generation / "seed_42" / "metrics.json"
    original_metric = metric_path.read_bytes()
    metric_path.write_bytes(original_metric + b" ")
    with pytest.raises(B07TrainingError, match="generation hash chain"):
        validate_b07_commit(
            workspace_root=workspace,
            config_path=config_path,
            input_bundle=bundle,
            allow_synthetic_bundle=True,
        )
    metric_path.write_bytes(original_metric)

    original_registry = registry_path.read_bytes()
    original_commit = commit_path.read_bytes()
    registry = json.loads(original_registry)
    registry["metrics"][0]["values"]["phone_macro_f1"] = 0.123
    registry_path.write_text(
        json.dumps(registry, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    commit = json.loads(original_commit)
    commit["registry"]["sha256"] = sha256_file(registry_path)
    commit_path.write_text(
        json.dumps(commit, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(B07TrainingError, match="registry metric records"):
        validate_b07_commit(
            workspace_root=workspace,
            config_path=config_path,
            input_bundle=bundle,
            allow_synthetic_bundle=True,
        )
    registry_path.write_bytes(original_registry)
    commit_path.write_bytes(original_commit)

    commit = json.loads(original_commit)
    commit["seeds"] = [42]
    commit_path.write_text(
        json.dumps(commit, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(B07TrainingError, match="commit seeds"):
        validate_b07_commit(
            workspace_root=workspace,
            config_path=config_path,
            input_bundle=bundle,
            allow_synthetic_bundle=True,
        )
