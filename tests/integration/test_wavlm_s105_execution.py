from __future__ import annotations

import copy
from contextlib import ExitStack
import json
import math
from pathlib import Path
import shutil
from types import SimpleNamespace
from typing import Any, cast
import wave

import numpy as np
import pytest
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from safetensors import safe_open
from safetensors.torch import save_file as save_safetensors
import torch
import yaml  # type: ignore[import-untyped]

from voice_coach.models.wavlm_hierarchy import IncrementalHierarchyHead, VARIANT_ORDER
from voice_coach.data.intervals import Interval as AudioInterval
from voice_coach.data.wav_qc import pcm_sha256
from voice_coach.training.wavlm_adapter_training import (
    AudioIntegrityVerifier,
    ExpectedDevUtterance,
    FitMetadata,
    _safe_json,
    atomic_write_json,
    slice_metrics_from_prediction_files,
)
from voice_coach.training.wavlm_hierarchy_execution import (
    HeadSelection,
    HierarchyOnlineExample,
    PooledHierarchy,
    WordInterval,
    evaluate_dev_shared,
    pool_all_encoder_layers,
    run_s105_training,
    save_hierarchy_checkpoint,
)
from voice_coach.training import wavlm_hierarchy_contract as hierarchy_contract
from voice_coach.training import wavlm_hierarchy_execution as execution
from voice_coach.training import wavlm_hierarchy_training as hierarchy_training
from voice_coach.training.wavlm_hierarchy_training import (
    REPORT_SCHEMA_VERSION,
    DeepValidationContext,
    DevPassLedger,
    HierarchyPredictionWriters,
    S105TrainingError,
    SEEDS,
    artifact_records,
    assert_head_only_trainable,
    build_variant_report,
    recompute_ablation_comparison,
    render_ablation_table,
    phone_prediction_schema,
    publish_content_addressed_generation,
    validate_generation,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/experiments/wavlm_s105_hierarchy_v1.yaml"


def _sha(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()


def _config() -> dict[str, Any]:
    value = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    config = copy.deepcopy(value)
    dimensions = config["variants"]["dimensions"]
    dimensions.update(
        {
            "projection_dim": 4,
            "phone_target_embedding_dim": 2,
            "attention_hidden_dim": 3,
            "attention_aspects": 2,
            "dropout": 0.0,
        }
    )
    return cast(dict[str, Any], config)


def _truth() -> dict[str, ExpectedDevUtterance]:
    output: dict[str, ExpectedDevUtterance] = {}
    for index in range(20):
        identifier = _sha(f"utterance-{index}")
        output[identifier] = ExpectedDevUtterance(
            speaker_id_sha256=_sha(f"speaker-{index}"),
            l1="Chinese",
            gender="F",
            proficiency="Beginner",
            phones=(("a", 0), ("b", 1)),
            scores=(
                float(index % 5 + 1),
                float((index + 1) % 5 + 1),
                float((index + 2) % 5 + 1),
            ),
        )
    return output


def _context(config: dict[str, Any]) -> DeepValidationContext:
    truth = _truth()
    checkpoints = [
        {
            "seed": seed,
            "path": f"predecessor/seed_{seed}.safetensors",
            "sha256": _sha(f"checkpoint-{seed}"),
            "size_bytes": 1,
        }
        for seed in SEEDS
    ]
    lock = {
        "predecessor": {
            "selected_candidate": {
                "model_id": "fixture/wavlm",
                "revision": "fixture-revision",
                "cache_dir": "fixture-cache",
                "hidden_size": 4,
                "encoder_layer_count": 2,
                "do_normalize": False,
                "source_config_sha256": _sha("model-config"),
                "source_checkpoint_sha256": _sha("model-checkpoint"),
            },
            "same_seed_checkpoints": checkpoints,
        },
        "data": {
            "inner_fit_utterances": 40,
            "split_utterances": {"train": 50, "dev": len(truth)},
        },
    }
    metadata = FitMetadata(
        target_vocabulary=("<UNK>", "a", "b"),
        positive_phone_rows=40,
        negative_phone_rows=40,
        pos_weight=1.0,
        fit_utterances=40,
    )
    return DeepValidationContext(
        config=config,
        execution_lock=lock,
        expected_dev_truth=truth,
        expected_fit_metadata=metadata,
    )


def _head(
    *,
    config: dict[str, Any],
    context: DeepValidationContext,
    variant: str,
) -> IncrementalHierarchyHead:
    dimensions = config["variants"]["dimensions"]
    selected = context.execution_lock["predecessor"]["selected_candidate"]
    metadata = context.expected_fit_metadata
    return IncrementalHierarchyHead(
        variant=cast(Any, variant),
        hidden_size=int(selected["hidden_size"]),
        encoder_layer_count=int(selected["encoder_layer_count"]),
        projection_dim=int(dimensions["projection_dim"]),
        target_vocabulary_size=len(metadata.target_vocabulary),
        target_embedding_dim=int(dimensions["phone_target_embedding_dim"]),
        attention_hidden_dim=int(dimensions["attention_hidden_dim"]),
        attention_aspects=int(dimensions["attention_aspects"]),
        dropout=float(dimensions["dropout"]),
        score_range=dimensions["score_range"],
    )


def write_synthetic_generation(
    root: Path,
    context: DeepValidationContext,
) -> None:
    config = cast(dict[str, Any], context.config)
    metadata = cast(FitMetadata, context.expected_fit_metadata)
    truth = cast(dict[str, ExpectedDevUtterance], context.expected_dev_truth)
    selected = context.execution_lock["predecessor"]["selected_candidate"]
    checkpoints = {
        item["seed"]: item
        for item in context.execution_lock["predecessor"]["same_seed_checkpoints"]
    }
    for variant_index, variant_value in enumerate(VARIANT_ORDER):
        variant = str(variant_value)
        for seed in SEEDS:
            seed_root = root / variant / f"seed_{seed}"
            seed_root.mkdir(parents=True, exist_ok=True)
            torch.manual_seed(seed + variant_index * 100)
            head = _head(config=config, context=context, variant=variant)
            state = {
                name: tensor.detach().cpu().clone()
                for name, tensor in head.state_dict().items()
            }
            selection = HeadSelection(
                best_epoch=1,
                threshold=0.5,
                phone_macro_f1=1.0,
                comprehensibility_icc_2_1=1.0,
                stopped_early=False,
                history=(
                    {
                        "epoch": 1,
                        "train_mean_joint_loss": 1.0,
                        "optimizer_steps": 5,
                        "inner_phone_macro_f1": 1.0,
                        "inner_phone_threshold": 0.5,
                        "inner_comprehensibility_icc_2_1": 1.0,
                        "inner_comprehensibility_icc_defined": True,
                        "selected_as_best": True,
                    },
                ),
                state=state,
            )
            checkpoint_record = save_hierarchy_checkpoint(
                seed_root / "hierarchy_head.safetensors",
                head=head,
                selection=selection,
                variant=variant,
                seed=seed,
                selected_candidate=selected,
                metadata=metadata,
                s104_checkpoint_sha256=checkpoints[seed]["sha256"],
            )
            with HierarchyPredictionWriters(
                phone_path=seed_root / "phone_dev_predictions.parquet",
                utterance_path=seed_root / "utterance_dev_predictions.parquet",
                row_group_size=7,
            ) as writers:
                for identifier, expected in truth.items():
                    for phone_index, (target_label, binary) in enumerate(
                        expected.phones
                    ):
                        probability = 0.1 if binary == 0 else 0.9
                        writers.add_phone(
                            {
                                "schema_version": (
                                    "voice_coach.wavlm_s105_phone_predictions.v1"
                                ),
                                "variant": variant,
                                "seed": seed,
                                "split": "dev",
                                "development_only": True,
                                "oracle_alignment": True,
                                "utterance_id_sha256": identifier,
                                "speaker_id_sha256": (expected.speaker_id_sha256),
                                "phone_index": phone_index,
                                "target_label": target_label,
                                "binary_error_label": binary,
                                "error_probability": probability,
                                "error_prediction": binary,
                                "l1": expected.l1,
                                "gender": expected.gender,
                                "proficiency": expected.proficiency,
                            }
                        )
                    utterance: dict[str, Any] = {
                        "schema_version": (
                            "voice_coach.wavlm_s105_utterance_predictions.v1"
                        ),
                        "variant": variant,
                        "seed": seed,
                        "split": "dev",
                        "development_only": True,
                        "oracle_alignment": True,
                        "utterance_id_sha256": identifier,
                        "speaker_id_sha256": expected.speaker_id_sha256,
                        "l1": expected.l1,
                        "gender": expected.gender,
                        "proficiency": expected.proficiency,
                    }
                    for index, outcome in enumerate(
                        (
                            "pronunciation_score",
                            "fluency_score",
                            "comprehensibility_score",
                        )
                    ):
                        utterance[f"{outcome}_true"] = expected.scores[index]
                        utterance[f"{outcome}_pred"] = expected.scores[index]
                        utterance[f"{outcome}_observed"] = True
                    writers.add_utterance(utterance)
            slices = slice_metrics_from_prediction_files(
                phone_path=seed_root / "phone_dev_predictions.parquet",
                utterance_path=seed_root / "utterance_dev_predictions.parquet",
                threshold=selection.threshold,
            )
            atomic_write_json(
                seed_root / "dev_slice_metrics.json",
                {
                    "schema_version": REPORT_SCHEMA_VERSION,
                    "variant": variant,
                    "seed": seed,
                    "development_only": True,
                    "dev_inference_passes_by_task": {
                        "phone_binary_error": 1,
                        "utterance_scores": 1,
                    },
                    "metrics": _safe_json(slices),
                },
            )
            atomic_write_json(
                seed_root / "resource_report.json",
                {
                    "schema_version": REPORT_SCHEMA_VERSION,
                    "variant": variant,
                    "seed": seed,
                    "trainable_parameter_audit": assert_head_only_trainable(head),
                    "checkpoint": checkpoint_record,
                    "fit_metadata": {
                        "target_vocabulary": list(metadata.target_vocabulary),
                        "positive_phone_rows": metadata.positive_phone_rows,
                        "negative_phone_rows": metadata.negative_phone_rows,
                        "pos_weight": metadata.pos_weight,
                        "fit_utterances": metadata.fit_utterances,
                    },
                    "selection": {
                        "best_epoch": 1,
                        "phone_decision_threshold": 0.5,
                        "inner_phone_macro_f1": 1.0,
                        "inner_comprehensibility_icc_2_1": 1.0,
                        "stopped_early": False,
                        "history": list(selection.history),
                    },
                    "device": {
                        "type": "cuda",
                        "index": 0,
                        "name": "synthetic-gpu",
                        "compute_capability": "9.0",
                        "total_memory_bytes": 1024,
                        "torch_version": torch.__version__,
                        "cuda_version": "synthetic",
                        "mixed_precision": "bfloat16",
                    },
                    "peak_process_rss_bytes": 1,
                    "peak_cuda_allocated_bytes": 0,
                    "peak_cuda_reserved_bytes": 0,
                    "automatic_oom_retry_count": 0,
                    "shared_encoder_forward_across_variants": True,
                    "dev_passes": {
                        "phone_binary_error": 1,
                        "utterance_scores": 1,
                    },
                    "s104_checkpoint_sha256": checkpoints[seed]["sha256"],
                },
            )
        atomic_write_json(
            root / variant / "variant_report.json",
            build_variant_report(generation=root, variant=variant),
        )
    comparison = recompute_ablation_comparison(
        generation=root,
        config=config,
    )
    atomic_write_json(
        root / "ablation_comparison_report.json",
        comparison,
    )
    (root / "ablation_table.md").write_text(
        render_ablation_table(generation=root, comparison=comparison),
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def synthetic_generation(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, DeepValidationContext]:
    root = tmp_path_factory.mktemp("s105-generation")
    config = _config()
    context = _context(config)
    write_synthetic_generation(root, context)
    return root, context


def test_deep_validator_recomputes_complete_82_file_evidence(
    synthetic_generation: tuple[Path, DeepValidationContext],
) -> None:
    root, context = synthetic_generation
    result = validate_generation(root, context=context)
    assert result["validation_depth"] == "deep"
    assert result["artifact_count"] == 82
    assert len(artifact_records(root)) == 82


def test_online_pool_preserves_all_encoder_layers_with_one_forward(
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "short.wav"
    samples = np.zeros(400, dtype=np.int16)
    with wave.open(str(audio_path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(samples.tobytes())

    class FakeWavLM:
        calls = 0

        def __call__(self, **kwargs: Any) -> Any:
            self.calls += 1
            input_values = kwargs["input_values"]
            frames = execution.WAVLM_BASE_PLUS_FRAME_GEOMETRY.output_frame_count(
                int(input_values.shape[1])
            )
            return SimpleNamespace(
                hidden_states=(
                    torch.zeros((1, frames, 4)),
                    torch.ones((1, frames, 4)),
                    torch.full((1, frames, 4), 2.0),
                )
            )

    wavlm = FakeWavLM()
    encoder = SimpleNamespace(wavlm=wavlm)
    example = HierarchyOnlineExample(
        utterance_id_sha256=_sha("pool-utterance"),
        speaker_id_sha256=_sha("pool-speaker"),
        split="dev",
        audio_path=audio_path,
        pcm_sha256=pcm_sha256(audio_path),
        phone_intervals=(AudioInterval(0.0, 0.02, "a"),),
        word_intervals=(WordInterval(0.0, 0.02),),
        phone_to_word=(0,),
        binary_labels=(0,),
        target_indices=(1,),
        l1="Chinese",
        gender="F",
        proficiency="Beginner",
        scores=(1.0, 2.0, 3.0),
    )
    pooled = pool_all_encoder_layers(
        model=cast(Any, encoder),
        example=example,
        device=torch.device("cpu"),
        do_normalize=False,
        maximum_chunk_seconds=20.0,
        encoder_layer_count=2,
        use_bfloat16=False,
        audio_integrity=AudioIntegrityVerifier(),
    )
    assert wavlm.calls == 1
    assert pooled.phone_layers.shape == (2, 1, 4)
    assert pooled.word_layers.shape == (2, 1, 4)
    assert pooled.utterance_layers.shape == (2, 4)
    assert torch.equal(pooled.phone_layers[:, 0, 0], torch.tensor([1.0, 2.0]))


def test_shared_dev_scan_feeds_all_five_variants_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    context = _context(config)
    heads = {
        str(variant): _head(
            config=config,
            context=context,
            variant=str(variant),
        )
        for variant in VARIANT_ORDER
    }
    selections = {
        variant: HeadSelection(
            best_epoch=1,
            threshold=0.5,
            phone_macro_f1=0.5,
            comprehensibility_icc_2_1=0.5,
            stopped_early=False,
            history=(),
            state={
                name: value.detach().clone()
                for name, value in head.state_dict().items()
            },
        )
        for variant, head in heads.items()
    }
    example = HierarchyOnlineExample(
        utterance_id_sha256=_sha("shared-utterance"),
        speaker_id_sha256=_sha("shared-speaker"),
        split="dev",
        audio_path=tmp_path / "unused.wav",
        pcm_sha256=_sha("unused-pcm"),
        phone_intervals=(
            AudioInterval(0.0, 0.1, "a"),
            AudioInterval(0.1, 0.2, "b"),
        ),
        word_intervals=(WordInterval(0.0, 0.2),),
        phone_to_word=(0, 0),
        binary_labels=(0, 1),
        target_indices=(1, 2),
        l1="Chinese",
        gender="F",
        proficiency="Beginner",
        scores=(1.0, 2.0, 3.0),
    )
    calls = 0

    def fake_pool(**_: Any) -> PooledHierarchy:
        nonlocal calls
        calls += 1
        return PooledHierarchy(
            phone_layers=torch.ones((2, 2, 4)),
            word_layers=torch.ones((2, 1, 4)),
            utterance_layers=torch.ones((2, 4)),
            phone_frame_counts=(1, 1),
            word_frame_counts=(2,),
            utterance_frame_count=2,
            chunk_count=1,
        )

    monkeypatch.setattr(execution, "pool_all_encoder_layers", fake_pool)
    ledger = DevPassLedger(
        variants=[str(value) for value in VARIANT_ORDER],
        tasks=["phone_binary_error", "utterance_scores"],
        seeds=[42],
    )
    with ExitStack() as stack:
        writers = {
            str(variant): stack.enter_context(
                HierarchyPredictionWriters(
                    phone_path=tmp_path / f"{variant}-phone.parquet",
                    utterance_path=tmp_path / f"{variant}-utterance.parquet",
                )
            )
            for variant in VARIANT_ORDER
        }
        rows = evaluate_dev_shared(
            heads=heads,
            selections=selections,
            encoder=cast(Any, object()),
            examples=[example],
            device=torch.device("cpu"),
            selected_candidate={
                "do_normalize": False,
                "encoder_layer_count": 2,
            },
            maximum_chunk_seconds=20.0,
            seed=42,
            writers=writers,
            ledger=ledger,
            audio_integrity=AudioIntegrityVerifier(),
        )
    assert rows == 1
    assert calls == 1
    ledger.assert_exact()
    for variant in VARIANT_ORDER:
        assert pq.read_table(tmp_path / f"{variant}-phone.parquet").num_rows == 2
        assert pq.read_table(tmp_path / f"{variant}-utterance.parquet").num_rows == 1


def test_symlink_ancestor_and_generation_destination_are_rejected(
    synthetic_generation: tuple[Path, DeepValidationContext],
    tmp_path: Path,
) -> None:
    source, _ = synthetic_generation
    workspace = tmp_path / "workspace"
    real = workspace / "real"
    real.mkdir(parents=True)
    alias = workspace / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(
        hierarchy_contract.S105ContractError,
        match="symlink",
    ):
        hierarchy_contract._resolve_output(  # type: ignore[attr-defined]
            workspace.resolve(),
            "alias/generations",
            "fixture output",
        )
    with pytest.raises(S105TrainingError, match="symlink"):
        execution._resolve_output(  # type: ignore[attr-defined]
            workspace.resolve(),
            "alias/generations",
            "fixture output",
        )

    temporary = tmp_path / "candidate"
    shutil.copytree(source, temporary)
    generation_root = tmp_path / "generations"
    generation_root.mkdir()
    generation_id = validate_generation(temporary)["generation_id"]
    claimant = tmp_path / "claimant"
    claimant.mkdir()
    destination = generation_root / generation_id
    destination.symlink_to(claimant, target_is_directory=True)
    with pytest.raises(S105TrainingError, match="symlink"):
        publish_content_addressed_generation(
            temporary=temporary,
            generation_root=generation_root,
        )
    assert destination.is_symlink()
    assert temporary.is_dir()


def test_generation_publish_never_overwrites_racing_claimant(
    synthetic_generation: tuple[Path, DeepValidationContext],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _ = synthetic_generation
    temporary = tmp_path / "candidate"
    shutil.copytree(source, temporary)
    generation_root = tmp_path / "generations"
    generation_root.mkdir()
    original = hierarchy_training._rename_no_replace  # type: ignore[attr-defined]

    def racing_claim(source_path: Path, destination: Path) -> bool:
        destination.mkdir()
        (destination / "claimant.txt").write_text(
            "must-not-be-overwritten",
            encoding="utf-8",
        )
        return original(source_path, destination)

    monkeypatch.setattr(
        hierarchy_training,
        "_rename_no_replace",
        racing_claim,
    )
    with pytest.raises(S105TrainingError, match="tree differs|already differs"):
        publish_content_addressed_generation(
            temporary=temporary,
            generation_root=generation_root,
        )
    destinations = list(generation_root.iterdir())
    assert len(destinations) == 1
    assert (destinations[0] / "claimant.txt").read_text(
        encoding="utf-8"
    ) == "must-not-be-overwritten"
    assert temporary.is_dir()


def test_immutable_json_never_overwrites_racing_claimant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "execution_lock.json"
    original_link = hierarchy_contract.os.link

    def racing_link(
        source: Path,
        target: Path,
        *,
        follow_symlinks: bool,
    ) -> None:
        Path(target).write_text("claimant", encoding="utf-8")
        original_link(source, target, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(hierarchy_contract.os, "link", racing_link)
    with pytest.raises(
        hierarchy_contract.S105ContractError,
        match="already differs",
    ):
        hierarchy_contract.publish_immutable_json(
            destination,
            {"schema_version": "fixture"},
        )
    assert destination.read_text(encoding="utf-8") == "claimant"


def test_immutable_commit_never_overwrites_racing_claimant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "commit.json"
    original_link = execution.os.link

    def racing_link(
        source: Path,
        target: Path,
        *,
        follow_symlinks: bool,
    ) -> None:
        Path(target).write_text("claimant", encoding="utf-8")
        original_link(source, target, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(execution.os, "link", racing_link)
    with pytest.raises(S105TrainingError, match="already differs"):
        execution._publish_immutable_commit(  # type: ignore[attr-defined]
            destination,
            {"schema_version": "fixture"},
        )
    assert destination.read_text(encoding="utf-8") == "claimant"


def test_initial_registry_claim_prevents_concurrent_fixed_id_owner(
    synthetic_generation: tuple[Path, DeepValidationContext],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, source_context = synthetic_generation
    workspace = tmp_path / "workspace"
    config_path, lock_path, config, lock, _, builder = _runner_fixture(
        workspace=workspace,
        source_generation=source,
        source_context=source_context,
        monkeypatch=monkeypatch,
    )
    registry_root = workspace / config["outputs"]["registry_root"]
    with execution._exclusive_registry_claim(  # type: ignore[attr-defined]
        registry_root=registry_root,
        prefix="synthetic-s105",
    ):
        with pytest.raises(S105TrainingError, match="claimant is active"):
            run_s105_training(
                workspace_root=workspace,
                config_path=config_path,
                execution_lock_path=lock_path,
                execution_lock=lock,
                argv=["synthetic-concurrent"],
                generation_builder=builder,
            )
    assert not any(
        path.is_dir() and path.name.startswith("synthetic-s105-")
        for path in registry_root.iterdir()
    )


def test_deep_validator_rejects_metric_and_checkpoint_tampering(
    synthetic_generation: tuple[Path, DeepValidationContext],
    tmp_path: Path,
) -> None:
    source, context = synthetic_generation
    import shutil

    metric_root = tmp_path / "metric"
    shutil.copytree(source, metric_root)
    metric_path = (
        metric_root / "simple_head_control" / "seed_42" / "dev_slice_metrics.json"
    )
    metric = json.loads(metric_path.read_text(encoding="utf-8"))
    metric["metrics"]["phone"]["l1"]["Chinese"]["macro_f1"] = 0.0
    metric_path.write_text(json.dumps(metric), encoding="utf-8")
    with pytest.raises(S105TrainingError, match="slice metrics"):
        validate_generation(metric_root, context=context)

    checkpoint_root = tmp_path / "checkpoint"
    shutil.copytree(source, checkpoint_root)
    checkpoint_path = (
        checkpoint_root
        / "simple_head_control"
        / "seed_42"
        / "hierarchy_head.safetensors"
    )
    with safe_open(checkpoint_path, framework="pt") as handle:
        metadata = handle.metadata()
        tensors = {name: handle.get_tensor(name) for name in handle.keys()}
    first = next(iter(tensors))
    tensors[first] = tensors[first].clone()
    tensors[first].reshape(-1)[0] = float("nan")
    save_safetensors(tensors, checkpoint_path, metadata=metadata)
    assert not math.isfinite(float(tensors[first].reshape(-1)[0]))
    with pytest.raises(S105TrainingError, match="checkpoint tensor"):
        validate_generation(checkpoint_root, context=context)

    allowlist_root = tmp_path / "allowlist"
    shutil.copytree(source, allowlist_root)
    allowlist_path = (
        allowlist_root
        / "simple_head_control"
        / "seed_42"
        / "hierarchy_head.safetensors"
    )
    with safe_open(allowlist_path, framework="pt") as handle:
        metadata = handle.metadata()
        tensors = {name: handle.get_tensor(name) for name in handle.keys()}
    tensors["wavlm.forbidden.weight"] = torch.ones(1)
    save_safetensors(tensors, allowlist_path, metadata=metadata)
    with pytest.raises(S105TrainingError, match="allowlist"):
        validate_generation(allowlist_root, context=context)

    identity_root = tmp_path / "identity"
    shutil.copytree(source, identity_root)
    identity_path = (
        identity_root
        / "flat_shared_multitask"
        / "seed_43"
        / "phone_dev_predictions.parquet"
    )
    rows = pq.read_table(identity_path, use_threads=False).to_pylist()
    rows[0]["utterance_id_sha256"] = _sha("wrong-utterance")
    pq.write_table(
        pa.Table.from_pylist(rows, schema=phone_prediction_schema()),
        identity_path,
        compression="zstd",
    )
    with pytest.raises(S105TrainingError, match="source identity"):
        validate_generation(identity_root, context=context)


def _runner_fixture(
    *,
    workspace: Path,
    source_generation: Path,
    source_context: DeepValidationContext,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    Path,
    Path,
    dict[str, Any],
    dict[str, Any],
    DeepValidationContext,
    Any,
]:
    workspace.mkdir()
    config = copy.deepcopy(cast(dict[str, Any], source_context.config))
    for relative in (
        "data/manifest.parquet",
        "data/reservation.json",
        "data/labels.parquet",
        "model/config.json",
        "model/pytorch_model.bin",
    ):
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode())
    config["data"]["modeling_manifest"] = "data/manifest.parquet"
    config["data"]["prompt_challenge_reservation"] = "data/reservation.json"
    config["data"]["explicit_utterance_labels"] = "data/labels.parquet"
    config["outputs"].update(
        {
            "execution_lock": "artifacts/s105/execution_lock.json",
            "generation_root": "artifacts/s105/generations",
            "commit_manifest": "artifacts/s105/commit.json",
            "registry_root": "artifacts/registry",
            "registry_run_id_prefix": "synthetic-s105",
        }
    )
    config_path = workspace / "configs/s105.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("fixture: true\n", encoding="utf-8")
    context_lock = copy.deepcopy(cast(dict[str, Any], source_context.execution_lock))
    checkpoint_records = []
    for seed in SEEDS:
        path = workspace / f"predecessor/seed_{seed}.safetensors"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"checkpoint-{seed}".encode())
        checkpoint_records.append(
            {
                "seed": seed,
                "path": path.relative_to(workspace).as_posix(),
                "sha256": execution.sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    context_lock["predecessor"]["same_seed_checkpoints"] = checkpoint_records
    context_lock["predecessor"].update(
        {
            "commit_sha256": _sha("s104-commit"),
            "generation_id": _sha("s104-generation"),
        }
    )
    lock = {
        **context_lock,
        "schema_version": execution.EXECUTION_LOCK_SCHEMA_VERSION,
        "status": "ready_for_s105_gpu_training",
        "config_sha256": execution.sha256_file(config_path),
        "implementation": {},
    }
    lock_path = workspace / "artifacts/s105/execution_lock.json"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    context = DeepValidationContext(
        config=config,
        execution_lock=lock,
        expected_dev_truth=source_context.expected_dev_truth,
        expected_fit_metadata=source_context.expected_fit_metadata,
    )
    model_config = workspace / "model/config.json"
    model_checkpoint = workspace / "model/pytorch_model.bin"
    monkeypatch.setattr(execution, "load_config", lambda _: config)
    monkeypatch.setattr(execution, "validate_config", lambda _: None)
    monkeypatch.setattr(
        execution,
        "validate_implementation_sources",
        lambda **_: ({}, ()),
    )
    monkeypatch.setattr(
        hierarchy_contract,
        "validate_s104_predecessor",
        lambda **_: SimpleNamespace(
            commit_sha256=_sha("s104-commit"),
            generation_id=_sha("s104-generation"),
        ),
    )
    monkeypatch.setattr(
        execution,
        "build_deep_validation_context",
        lambda **_: context,
    )
    monkeypatch.setattr(
        execution,
        "local_snapshot_paths",
        lambda **_: (model_config.parent, model_config, model_checkpoint),
    )

    def validate_commit(*, workspace_root: Path, config_path: Path) -> Any:
        del config_path
        commit_path = workspace_root / config["outputs"]["commit_manifest"]
        commit = json.loads(commit_path.read_text(encoding="utf-8"))
        generation = workspace_root / commit["generation_path"]
        result = validate_generation(generation, context=context)
        assert result["artifact_count"] == 82
        assert len(commit["registries"]) == 5
        return {"status": "valid"}

    monkeypatch.setattr(execution, "validate_s105_commit", validate_commit)

    def builder(temporary: Path, received: DeepValidationContext) -> None:
        assert received is context
        shutil.copytree(source_generation, temporary, dirs_exist_ok=True)

    return config_path, lock_path, config, lock, context, builder


def test_fault_after_publication_is_fail_closed_and_records_safe_failures(
    synthetic_generation: tuple[Path, DeepValidationContext],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, source_context = synthetic_generation
    workspace = tmp_path / "workspace"
    config_path, lock_path, config, lock, _, builder = _runner_fixture(
        workspace=workspace,
        source_generation=source,
        source_context=source_context,
        monkeypatch=monkeypatch,
    )

    def fault(stage: str) -> None:
        if stage == "artifact_publication":
            raise RuntimeError("synthetic publication fault")

    with pytest.raises(RuntimeError, match="synthetic publication"):
        run_s105_training(
            workspace_root=workspace,
            config_path=config_path,
            execution_lock_path=lock_path,
            execution_lock=lock,
            argv=["synthetic"],
            generation_builder=builder,
            fault_injector=fault,
        )
    generation_root = workspace / config["outputs"]["generation_root"]
    assert list(generation_root.iterdir()) == []
    assert not (workspace / config["outputs"]["commit_manifest"]).exists()
    registry_root = workspace / config["outputs"]["registry_root"]
    for variant in VARIANT_ORDER:
        registry = json.loads(
            (registry_root / f"synthetic-s105-{variant}" / "metadata.json").read_text(
                encoding="utf-8"
            )
        )
        assert registry["status"] == "failed"
        assert set(registry["failure"]) == {"exception_type", "stage", "oom"}
        assert registry["failure"]["stage"] == "artifact_publication"
        assert registry["failure"]["oom"] is False


def test_oom_is_recorded_without_retry_or_partial_generation(
    synthetic_generation: tuple[Path, DeepValidationContext],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, source_context = synthetic_generation
    workspace = tmp_path / "workspace"
    config_path, lock_path, config, lock, _, _ = _runner_fixture(
        workspace=workspace,
        source_generation=source,
        source_context=source_context,
        monkeypatch=monkeypatch,
    )

    def oom_builder(*_: object) -> None:
        raise torch.OutOfMemoryError("synthetic OOM")

    with pytest.raises(torch.OutOfMemoryError):
        run_s105_training(
            workspace_root=workspace,
            config_path=config_path,
            execution_lock_path=lock_path,
            execution_lock=lock,
            argv=["synthetic-oom"],
            generation_builder=oom_builder,
        )
    generation_root = workspace / config["outputs"]["generation_root"]
    assert list(generation_root.iterdir()) == []
    registry_root = workspace / config["outputs"]["registry_root"]
    for variant in VARIANT_ORDER:
        registry = json.loads(
            (registry_root / f"synthetic-s105-{variant}" / "metadata.json").read_text(
                encoding="utf-8"
            )
        )
        assert registry["status"] == "failed"
        assert registry["failure"]["stage"] == "seed_training"
        assert registry["failure"]["oom"] is True
        assert registry["artifacts"] == []


def test_commit_fault_recovers_from_five_completed_registries_without_rebuild(
    synthetic_generation: tuple[Path, DeepValidationContext],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, source_context = synthetic_generation
    workspace = tmp_path / "workspace"
    config_path, lock_path, config, lock, context, builder = _runner_fixture(
        workspace=workspace,
        source_generation=source,
        source_context=source_context,
        monkeypatch=monkeypatch,
    )

    def fault(stage: str) -> None:
        if stage == "commit_publication":
            raise RuntimeError("synthetic commit fault")

    with pytest.raises(RuntimeError, match="synthetic commit"):
        run_s105_training(
            workspace_root=workspace,
            config_path=config_path,
            execution_lock_path=lock_path,
            execution_lock=lock,
            argv=["synthetic"],
            generation_builder=builder,
            fault_injector=fault,
        )
    commit_path = workspace / config["outputs"]["commit_manifest"]
    assert not commit_path.exists()
    registry_root = workspace / config["outputs"]["registry_root"]
    assert all(
        json.loads(
            (registry_root / f"synthetic-s105-{variant}" / "metadata.json").read_text(
                encoding="utf-8"
            )
        )["status"]
        == "completed"
        for variant in VARIANT_ORDER
    )

    def forbidden_builder(*_: object) -> None:
        raise AssertionError("recovery attempted to rebuild the generation")

    payload = run_s105_training(
        workspace_root=workspace,
        config_path=config_path,
        execution_lock_path=lock_path,
        execution_lock=lock,
        argv=["synthetic-recovery"],
        generation_builder=forbidden_builder,
    )
    assert commit_path.exists()
    assert payload["status"] == "committed"
    generation = workspace / payload["generation_path"]
    assert validate_generation(generation, context=context)["artifact_count"] == 82
    assert len(payload["registries"]) == 5

    # Exercise the public commit validator itself.  Only predecessor/data
    # reconstruction is patched; generation, five registries, binding, and
    # every derived artifact are validated by production code.
    monkeypatch.setattr(hierarchy_contract, "load_config", lambda _: config)
    monkeypatch.setattr(hierarchy_contract, "validate_config", lambda _: None)
    monkeypatch.setattr(
        hierarchy_contract,
        "validate_implementation_sources",
        lambda **_: ({}, ()),
    )
    monkeypatch.setattr(
        hierarchy_training,
        "build_deep_validation_context",
        lambda **_: context,
    )
    validated = hierarchy_training.validate_s105_commit(
        workspace_root=workspace,
        config_path=config_path,
    )
    assert validated["status"] == "valid"
    assert validated["registry_count"] == 5

    # The S105 commit is transitively bound to the independently revalidated
    # S104 commit/generation.  A changed predecessor identity must fail.
    monkeypatch.setattr(
        hierarchy_contract,
        "validate_s104_predecessor",
        lambda **_: SimpleNamespace(
            commit_sha256=_sha("different-s104-commit"),
            generation_id=_sha("s104-generation"),
        ),
    )
    with pytest.raises(S105TrainingError, match="predecessor binding"):
        hierarchy_training.validate_s105_commit(
            workspace_root=workspace,
            config_path=config_path,
        )
