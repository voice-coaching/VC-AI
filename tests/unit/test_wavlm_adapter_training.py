from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from safetensors import safe_open
import soundfile as sf
import torch
from torch import nn

from voice_coach.data import Interval
from voice_coach.experiments import registry as registry_module
from voice_coach.training import wavlm_adapter_training as training


class FakeOnlineEncoder(nn.Module):
    def __init__(self, hidden_size: int = 4) -> None:
        super().__init__()
        self.hidden_size = hidden_size
        self.scale = nn.Parameter(torch.tensor(1.0))
        self.calls: list[torch.Tensor] = []

    def encode(
        self,
        input_values: torch.Tensor,
        *,
        attention_mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        del attention_mask
        frames = training.WAVLM_BASE_PLUS_FRAME_GEOMETRY.output_frame_count(
            input_values.shape[1]
        )
        values = (
            torch.arange(
                frames * self.hidden_size,
                device=input_values.device,
                dtype=torch.float32,
            ).reshape(1, frames, self.hidden_size)
            * self.scale
        )
        self.calls.append(values)
        return values


def _example(audio_path: Path, duration: float) -> training.OnlineExample:
    return training.OnlineExample(
        utterance_id_sha256="a" * 64,
        speaker_id_sha256="b" * 64,
        split="train",
        audio_path=audio_path,
        pcm_sha256="c" * 64,
        intervals=(Interval(0.0, duration, "AA"),),
        binary_labels=(1,),
        target_indices=(1,),
        l1="Chinese",
        gender="F",
        proficiency="beginner",
        scores=(3.0, 4.0, 5.0),
    )


def test_online_pooling_shares_chunk_forwards_and_preserves_gradient(
    tmp_path: Path,
) -> None:
    sample_rate = 16_000
    duration = 1.2
    audio = np.linspace(-0.5, 0.5, round(duration * sample_rate), dtype=np.float32)
    audio_path = tmp_path / "sample.wav"
    sf.write(audio_path, audio, sample_rate)
    model = FakeOnlineEncoder()

    pooled = training.pool_online_example(
        model=model,  # type: ignore[arg-type]
        example=_example(audio_path, duration),
        device=torch.device("cpu"),
        do_normalize=False,
        maximum_chunk_seconds=0.5,
        use_bfloat16=False,
    )

    assert pooled.chunk_count == 3
    assert len(model.calls) == pooled.chunk_count
    concatenated = torch.cat([value[0] for value in model.calls], dim=0)
    expected_mean = concatenated.mean(dim=0)
    expected_std = concatenated.std(dim=0, correction=0)
    torch.testing.assert_close(pooled.phone_embeddings[0], expected_mean)
    torch.testing.assert_close(
        pooled.utterance_embedding,
        torch.cat((expected_mean, expected_std)),
    )
    pooled.phone_embeddings.sum().backward()
    assert model.scale.grad is not None
    assert torch.isfinite(model.scale.grad)


def test_one_frame_utterance_standard_deviation_has_finite_gradient(
    tmp_path: Path,
) -> None:
    sample_rate = 16_000
    duration = 400 / sample_rate
    audio_path = tmp_path / "short.wav"
    sf.write(
        audio_path,
        np.linspace(-0.1, 0.1, 400, dtype=np.float32),
        sample_rate,
    )
    model = FakeOnlineEncoder()
    pooled = training.pool_online_example(
        model=model,  # type: ignore[arg-type]
        example=_example(audio_path, duration),
        device=torch.device("cpu"),
        do_normalize=False,
        maximum_chunk_seconds=20.0,
        use_bfloat16=False,
    )

    assert pooled.utterance_frame_count == 1
    assert torch.equal(
        pooled.utterance_embedding[model.hidden_size :],
        torch.zeros(model.hidden_size),
    )
    pooled.utterance_embedding.sum().backward()
    assert model.scale.grad is not None
    assert torch.isfinite(model.scale.grad)


def test_nonfinite_trainable_parameter_guard_fails_closed() -> None:
    model = nn.Linear(2, 1)
    with torch.no_grad():
        model.weight[0, 0] = float("nan")

    with pytest.raises(training.S104TrainingError, match="non-finite"):
        training._assert_trainable_parameters_finite(model)


@pytest.mark.parametrize("sample_count", [1, 79, 80, 399])
def test_real_tiny_wavlm_accepts_padded_short_tail(
    tmp_path: Path,
    sample_count: int,
) -> None:
    from transformers import WavLMConfig, WavLMModel

    configuration = WavLMConfig(
        hidden_size=8,
        num_hidden_layers=1,
        num_attention_heads=2,
        intermediate_size=16,
        conv_dim=(4, 4, 4, 4, 4, 4, 4),
        conv_stride=(5, 2, 2, 2, 2, 2, 2),
        conv_kernel=(10, 3, 3, 3, 3, 2, 2),
        num_conv_pos_embeddings=8,
        num_conv_pos_embedding_groups=2,
        apply_spec_augment=False,
        layerdrop=0.0,
    )
    model = training.AdapterTunedWavLM(
        wavlm=WavLMModel(configuration),
        hidden_size=8,
        encoder_layer_count=1,
        adapter_bottleneck_dim=2,
        adapter_dropout=0.0,
        zero_initialize_adapter_output=True,
        target_vocabulary_size=2,
        target_embedding_dim=2,
        phone_hidden_dim=4,
        phone_dropout=0.0,
        utterance_hidden_dim=4,
        utterance_dropout=0.0,
    )
    model.eval()
    audio_path = tmp_path / f"tail-{sample_count}.wav"
    sf.write(
        audio_path,
        np.zeros(sample_count, dtype=np.float32),
        16_000,
    )
    duration = sample_count / 16_000

    pooled = training.pool_online_example(
        model=model,
        example=_example(audio_path, duration),
        device=torch.device("cpu"),
        do_normalize=False,
        maximum_chunk_seconds=20.0,
        use_bfloat16=False,
    )

    assert pooled.utterance_frame_count == 1
    assert pooled.phone_frame_counts == (1,)


def test_bounded_shuffle_is_deterministic_and_a_permutation() -> None:
    rows = [{"value": index} for index in range(100)]
    first = list(training.bounded_shuffle(rows, buffer_size=7, seed=42))
    repeated = list(training.bounded_shuffle(rows, buffer_size=7, seed=42))
    other = list(training.bounded_shuffle(rows, buffer_size=7, seed=43))

    assert first == repeated
    assert first != other
    assert sorted(item["value"] for item in first) == list(range(100))


def test_guarded_scanner_never_resolves_or_decodes_filtered_raw_audio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interval_type = pa.list_(
        pa.struct(
            [
                pa.field("start", pa.float64()),
                pa.field("end", pa.float64()),
                pa.field("label", pa.string()),
            ]
        )
    )
    alignment_type = pa.list_(
        pa.struct(
            [
                pa.field("target_label", pa.string()),
                pa.field("operation", pa.string()),
            ]
        )
    )
    schema = pa.schema(
        [
            pa.field("id", pa.string()),
            pa.field("speaker_id", pa.string()),
            pa.field("split", pa.string()),
            pa.field("audio_path", pa.string()),
            pa.field("pcm_sha256", pa.string()),
            pa.field("prompt_sha256", pa.string()),
            pa.field("qc_flags", pa.list_(pa.string())),
            pa.field("l1", pa.string()),
            pa.field("gender", pa.string()),
            pa.field("proficiency", pa.string()),
            pa.field("target_phone_intervals", interval_type),
            pa.field("phone_alignments", alignment_type),
        ]
    )
    reserved = "1" * 64
    rows = [
        {
            "id": "qc",
            "speaker_id": "a" * 64,
            "split": "dev",
            "audio_path": "must-not-resolve-qc.wav",
            "pcm_sha256": "5" * 64,
            "prompt_sha256": "2" * 64,
            "qc_flags": ["bad"],
            "l1": "Chinese",
            "gender": "F",
            "proficiency": "beginner",
            "target_phone_intervals": [],
            "phone_alignments": [],
        },
        {
            "id": "reserved",
            "speaker_id": "b" * 64,
            "split": "dev",
            "audio_path": "must-not-resolve-reserved.wav",
            "pcm_sha256": "6" * 64,
            "prompt_sha256": reserved,
            "qc_flags": [],
            "l1": "Chinese",
            "gender": "F",
            "proficiency": "beginner",
            "target_phone_intervals": [],
            "phone_alignments": [],
        },
        {
            "id": "scope",
            "speaker_id": "c" * 64,
            "split": "dev",
            "audio_path": "must-not-resolve-scope.wav",
            "pcm_sha256": "7" * 64,
            "prompt_sha256": "3" * 64,
            "qc_flags": [],
            "l1": "English",
            "gender": "F",
            "proficiency": "beginner",
            "target_phone_intervals": [],
            "phone_alignments": [],
        },
        {
            "id": "protected",
            "speaker_id": "d" * 64,
            "split": "stack",
            "audio_path": "must-not-resolve-protected.wav",
            "pcm_sha256": "8" * 64,
            "prompt_sha256": "4" * 64,
            "qc_flags": [],
            "l1": "Chinese",
            "gender": "F",
            "proficiency": "beginner",
            "target_phone_intervals": [],
            "phone_alignments": [],
        },
    ]
    manifest = tmp_path / "manifest.parquet"
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), manifest)

    def forbidden(*_: object, **__: object) -> None:
        raise AssertionError("filtered raw audio was touched")

    monkeypatch.setattr(training, "resolve_audio_path", forbidden)
    monkeypatch.setattr(training, "decode_audio", forbidden)
    assert (
        list(
            training.iter_examples(
                manifest_path=manifest,
                dataset_root=tmp_path,
                labels={},
                reserved_prompts={reserved},
                target_vocabulary={"<UNK>": 0},
                subset="dev",
                modulus=10,
                validation_buckets={0, 1},
                allowed_l1={"Chinese", "Japanese"},
                allowed_gender={"F", "M"},
            )
        )
        == []
    )


def test_audio_path_rejects_lexical_and_resolved_environment_components(
    tmp_path: Path,
) -> None:
    environment_directory = tmp_path / ".env.audio"
    environment_directory.mkdir()
    target = environment_directory / "sample.wav"
    target.write_bytes(b"not-opened")
    innocent = tmp_path / "innocent.wav"
    innocent.symlink_to(target)

    with pytest.raises(training.S104TrainingError, match="environment"):
        training.resolve_audio_path(tmp_path, ".env.audio/sample.wav")
    with pytest.raises(training.S104TrainingError, match="environment"):
        training.resolve_audio_path(tmp_path, "innocent.wav")


def test_audio_integrity_verifier_hashes_once_and_detects_identity_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "audio.wav"
    sf.write(path, np.zeros(800, dtype=np.float32), 16_000)
    expected = training.pcm_sha256(path)
    calls = 0
    original = training.pcm_sha256

    def counted(candidate: Path) -> str:
        nonlocal calls
        calls += 1
        return original(candidate)

    monkeypatch.setattr(training, "pcm_sha256", counted)
    verifier = training.AudioIntegrityVerifier()
    verifier.verify(path, expected)
    verifier.verify(path, expected)
    assert calls == 1

    sf.write(path, np.ones(800, dtype=np.float32), 16_000)
    with pytest.raises(training.S104TrainingError, match="identity changed"):
        verifier.assert_stable()


def test_threshold_ties_choose_closest_to_half_then_lower() -> None:
    targets = np.asarray([0, 1], dtype=np.int8)
    probabilities = np.asarray([0.5, 0.5], dtype=np.float32)
    threshold, score = training.select_phone_threshold(
        targets,
        probabilities,
        np.asarray([0.4, 0.6]),
    )
    assert threshold == pytest.approx(0.4)
    assert score == pytest.approx(1 / 3)

    threshold, _ = training.select_phone_threshold(
        targets,
        probabilities,
        np.asarray([0.4, 0.5, 0.6]),
    )
    assert threshold == pytest.approx(0.5)


def test_selection_order_uses_f1_then_defined_icc_and_keeps_earliest() -> None:
    assert training._is_better_selection(
        phone_f1=0.8,
        comprehensibility_icc=None,
        best_phone_f1=0.7,
        best_comprehensibility_icc=0.9,
    )
    assert not training._is_better_selection(
        phone_f1=0.8,
        comprehensibility_icc=None,
        best_phone_f1=0.8,
        best_comprehensibility_icc=0.1,
    )
    assert training._is_better_selection(
        phone_f1=0.8,
        comprehensibility_icc=0.2,
        best_phone_f1=0.8,
        best_comprehensibility_icc=None,
    )
    assert not training._is_better_selection(
        phone_f1=0.8,
        comprehensibility_icc=0.2,
        best_phone_f1=0.8,
        best_comprehensibility_icc=0.2,
    )


def test_dev_pass_guard_requires_exactly_one_completed_pass() -> None:
    guard = training.DevPassGuard()
    guard.enter()
    guard.complete()
    guard.assert_complete()
    with pytest.raises(training.S104TrainingError, match="more than once"):
        guard.enter()

    incomplete = training.DevPassGuard()
    incomplete.enter()
    with pytest.raises(training.S104TrainingError, match="did not complete"):
        incomplete.assert_complete()


def test_last_partial_gradient_window_divides_by_actual_utterances() -> None:
    parameter = nn.Parameter(torch.tensor(0.0))
    losses = ((parameter - 1).square(), (parameter - 3).square())
    for loss in losses:
        training._backward_window_loss(loss, actual_utterances=2)
    # Mean derivative at zero: ((-2) + (-6)) / 2 = -4.
    assert float(parameter.grad) == pytest.approx(-4.0)


def test_synthetic_cuda_oom_records_exact_failure_contract_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        registry_module,
        "collect_runtime_metadata",
        lambda package_names: {
            "python": {},
            "platform": {},
            "packages": {},
        },
    )
    monkeypatch.setattr(
        registry_module,
        "collect_accelerator_metadata",
        lambda: {"cuda_available": True},
    )
    monkeypatch.setattr(
        registry_module,
        "collect_git_metadata",
        lambda repo_path: {"available": False},
    )
    attempts = 0
    run = registry_module.ExperimentRun(
        tmp_path / "registry",
        run_id="s104-synthetic-oom",
        argv=["train.py"],
        repo_path=tmp_path,
        package_names=(),
    )
    secret_message = "/restricted/raw-speaker.wav"

    with pytest.raises(torch.cuda.OutOfMemoryError):
        with run:
            try:
                attempts += 1
                raise torch.cuda.OutOfMemoryError(secret_message)
            except BaseException as error:
                training._record_failure(
                    run,
                    stage="seed_training",
                    error=error,
                )
                raise

    payload_text = run.metadata_path.read_text(encoding="utf-8")
    payload = json.loads(payload_text)
    assert attempts == 1
    assert payload["status"] == "failed"
    assert payload["failure"] == {
        "exception_type": "torch.OutOfMemoryError",
        "stage": "seed_training",
        "oom": True,
    }
    assert secret_message not in payload_text
    assert not (tmp_path / "generations").exists()
    assert not (tmp_path / "commit.json").exists()


def _selection(
    state: dict[str, torch.Tensor],
) -> training.SelectionResult:
    return training.SelectionResult(
        best_epoch=2,
        threshold=0.5,
        phone_macro_f1=0.75,
        comprehensibility_icc_2_1=0.5,
        stopped_early=False,
        history=(),
        trainable_state=state,
    )


def test_safetensors_checkpoint_contains_only_trainable_adapter_and_heads(
    tmp_path: Path,
) -> None:
    path = tmp_path / "checkpoint.safetensors"
    state = {
        "wavlm.encoder.layers.0.adapter.up.weight": torch.ones((2, 2)),
        "phone_head.classifier.0.weight": torch.ones((1, 2)),
        "utterance_head.network.0.weight": torch.ones((2,)),
    }
    record = training.save_adapter_checkpoint(
        path,
        selection=_selection(state),
        seed=42,
        selected_candidate={
            "model_id": "microsoft/wavlm-base-plus",
            "revision": "a" * 40,
        },
        metadata=training.FitMetadata(
            target_vocabulary=("<UNK>", "AA"),
            positive_phone_rows=1,
            negative_phone_rows=1,
            pos_weight=1.0,
            fit_utterances=1,
        ),
    )
    assert record["full_backbone_tensor_count"] == 0
    with safe_open(path, framework="pt") as handle:
        assert set(handle.keys()) == set(state)
        assert handle.metadata()["full_backbone_tensor_count"] == "0"

    state["wavlm.feature_extractor.conv.weight"] = torch.ones((1,))
    with pytest.raises(training.S104TrainingError, match="non-adapter"):
        training.save_adapter_checkpoint(
            tmp_path / "bad.safetensors",
            selection=_selection(state),
            seed=42,
            selected_candidate={
                "model_id": "microsoft/wavlm-base-plus",
                "revision": "a" * 40,
            },
            metadata=training.FitMetadata(
                target_vocabulary=("<UNK>", "AA"),
                positive_phone_rows=1,
                negative_phone_rows=1,
                pos_weight=1.0,
                fit_utterances=1,
            ),
        )


def _write_exact_artifact_tree(root: Path) -> None:
    for name, relative in training._artifact_layout():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"name": name}, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _write_valid_artifact_tree(root: Path) -> None:
    state = {
        "wavlm.encoder.layers.0.adapter.up.weight": torch.ones((2, 2)),
        "phone_head.classifier.0.weight": torch.ones((1, 2)),
        "utterance_head.network.0.weight": torch.ones((2,)),
    }
    metadata = training.FitMetadata(
        target_vocabulary=("<UNK>", "AA"),
        positive_phone_rows=1,
        negative_phone_rows=1,
        pos_weight=1.0,
        fit_utterances=1,
    )
    seed_metrics: dict[int, dict[str, object]] = {}
    for seed in training.SEEDS:
        seed_directory = root / f"seed_{seed}"
        seed_directory.mkdir(parents=True)
        checkpoint_path = seed_directory / "adapter_and_heads.safetensors"
        checkpoint = training.save_adapter_checkpoint(
            checkpoint_path,
            selection=_selection(state),
            seed=seed,
            selected_candidate={
                "model_id": "microsoft/wavlm-base-plus",
                "revision": "a" * 40,
            },
            metadata=metadata,
        )
        with training.PredictionWriters(
            phone_path=seed_directory / "phone_dev_predictions.parquet",
            utterance_path=(seed_directory / "utterance_dev_predictions.parquet"),
        ) as writers:
            writers.add_phone(
                {
                    "schema_version": (training.PHONE_PREDICTION_SCHEMA_VERSION),
                    "seed": seed,
                    "split": "dev",
                    "development_only": True,
                    "oracle_alignment": True,
                    "utterance_id_sha256": "a" * 64,
                    "speaker_id_sha256": "b" * 64,
                    "phone_index": 0,
                    "target_label": "AA",
                    "binary_error_label": 1,
                    "error_probability": 0.75,
                    "error_prediction": 1,
                    "l1": "Chinese",
                    "gender": "F",
                    "proficiency": "beginner",
                }
            )
            utterance_row = {
                "schema_version": (training.UTTERANCE_PREDICTION_SCHEMA_VERSION),
                "seed": seed,
                "split": "dev",
                "development_only": True,
                "oracle_alignment": True,
                "utterance_id_sha256": "a" * 64,
                "speaker_id_sha256": "b" * 64,
                "l1": "Chinese",
                "gender": "F",
                "proficiency": "beginner",
            }
            for outcome in training.OUTCOMES:
                utterance_row[f"{outcome}_true"] = 3.0
                utterance_row[f"{outcome}_pred"] = 3.25
                utterance_row[f"{outcome}_observed"] = True
            writers.add_utterance(utterance_row)
        phone_path = seed_directory / "phone_dev_predictions.parquet"
        utterance_path = seed_directory / "utterance_dev_predictions.parquet"
        slices = training.slice_metrics_from_prediction_files(
            phone_path=phone_path,
            utterance_path=utterance_path,
            threshold=0.5,
        )
        metrics = training.metric_bundle_from_prediction_files(
            phone_path=phone_path,
            utterance_path=utterance_path,
            threshold=0.5,
        )
        normalized_metrics = training._safe_json(metrics)
        seed_metrics[seed] = normalized_metrics
        training.atomic_write_json(
            seed_directory / "dev_slice_metrics.json",
            {
                "schema_version": training.REPORT_SCHEMA_VERSION,
                "seed": seed,
                "development_only": True,
                "dev_inference_passes": 1,
                "metrics": slices,
            },
        )
        training.atomic_write_json(
            seed_directory / "resource_and_trainable_parameter_report.json",
            {
                "schema_version": training.REPORT_SCHEMA_VERSION,
                "seed": seed,
                "trainable_parameter_audit": {
                    "total_parameter_count": (
                        sum(value.numel() for value in state.values()) + 1
                    ),
                    "trainable_parameter_count": sum(
                        value.numel() for value in state.values()
                    ),
                    "frozen_parameter_count": 1,
                    "trainable_fraction": (
                        sum(value.numel() for value in state.values())
                        / (sum(value.numel() for value in state.values()) + 1)
                    ),
                    "trainable_tensor_count": len(state),
                    "trainable_parameter_names": sorted(state),
                    "allow_patterns": [],
                    "backbone_non_adapter_trainable_count": 0,
                    "full_fine_tuning": False,
                },
                "checkpoint": checkpoint,
                "fit_metadata": {
                    "target_vocabulary": ["<UNK>", "AA"],
                    "positive_phone_rows": 1,
                    "negative_phone_rows": 1,
                    "pos_weight": 1.0,
                    "fit_utterances": 1,
                },
                "peak_process_rss_bytes": 1,
                "peak_cuda_allocated_bytes": 1,
                "peak_cuda_reserved_bytes": 2,
                "automatic_oom_retry_count": 0,
            },
        )
    phone_summary = training._aggregate(
        [float(seed_metrics[seed]["phone"]["macro_f1"]) for seed in training.SEEDS]
    )
    icc_summary = training._aggregate(
        [
            seed_metrics[seed]["utterance"]["outcomes"]["comprehensibility_score"][
                "icc_2_1"
            ]
            for seed in training.SEEDS
        ]
    )
    training.atomic_write_json(
        root / "experiment_report.json",
        {
            "schema_version": training.REPORT_SCHEMA_VERSION,
            "status": "completed",
            "runs": [
                {
                    "seed": seed,
                    "selection": {
                        "best_epoch": 2,
                        "decision_threshold": 0.5,
                        "refit_performed": False,
                    },
                    "dev": {
                        "inference_passes": 1,
                        "used_for_selection": False,
                        "metrics": seed_metrics[seed],
                        "engineering_targets": {
                            "phone_macro_f1_threshold": 0.75,
                            "primary_utterance_icc_2_1_threshold": 0.65,
                            "phone_passed": (
                                float(seed_metrics[seed]["phone"]["macro_f1"]) >= 0.75
                            ),
                            "primary_utterance_passed": False,
                            "development_only_not_promotion": True,
                        },
                        "word_metric_status": "not_evaluable",
                    },
                }
                for seed in training.SEEDS
            ],
            "summary": {
                "seeds_completed": list(training.SEEDS),
                "phone_macro_f1": phone_summary,
                "comprehensibility_icc_2_1": icc_summary,
                "engineering_targets": {
                    "phone_macro_f1_threshold": 0.75,
                    "primary_utterance_icc_2_1_threshold": 0.65,
                    "mean_phone_passed": (
                        phone_summary["mean"] is not None
                        and float(phone_summary["mean"]) >= 0.75
                    ),
                    "mean_primary_utterance_passed": False,
                    "development_only_not_promotion": True,
                },
                "word_metric_status": "not_evaluable",
                "dev_inference_passes_per_seed": 1,
                "dev_used_for_selection": False,
                "locked_test_remains_unread": True,
                "stack_remains_unread": True,
                "calibration_remains_unread": True,
            },
        },
    )


def test_content_addressed_generation_publishes_exactly_16_files(
    tmp_path: Path,
) -> None:
    temporary = tmp_path / ".part"
    temporary.mkdir()
    _write_exact_artifact_tree(temporary)
    generation_root = tmp_path / "generations"

    published = training.publish_content_addressed_generation(
        temporary=temporary,
        generation_root=generation_root,
    )

    assert len(published.artifacts) == 16
    assert published.generation_path.name == published.generation_id
    assert len(list(published.generation_path.rglob("*.*"))) == 16
    assert not temporary.exists()


def test_generation_rejects_unexpected_empty_directory(tmp_path: Path) -> None:
    temporary = tmp_path / ".part"
    temporary.mkdir()
    _write_exact_artifact_tree(temporary)
    (temporary / "unexpected").mkdir()

    with pytest.raises(training.S104TrainingError, match="tree differs"):
        training.publish_content_addressed_generation(
            temporary=temporary,
            generation_root=tmp_path / "generations",
        )


def test_three_seed_artifact_validator_and_atomic_publish(tmp_path: Path) -> None:
    temporary = tmp_path / ".valid-part"
    temporary.mkdir()
    _write_valid_artifact_tree(temporary)

    training.validate_generated_artifact_tree(temporary)
    published = training.publish_content_addressed_generation(
        temporary=temporary,
        generation_root=tmp_path / "generations",
    )

    assert len(published.artifacts) == 16
    training.validate_generated_artifact_tree(published.generation_path)


def test_post_rename_directory_fsync_failure_preserves_complete_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temporary = tmp_path / ".part"
    temporary.mkdir()
    _write_exact_artifact_tree(temporary)
    generation_root = tmp_path / "generations"
    records = training._artifact_records(temporary)
    expected = generation_root / training._generation_id(records)
    original = training._fsync_directory

    def fail_generation_root(path: Path) -> None:
        if path == generation_root:
            raise OSError("synthetic post-rename durability failure")
        original(path)

    monkeypatch.setattr(training, "_fsync_directory", fail_generation_root)
    with pytest.raises(OSError, match="durability"):
        training.publish_content_addressed_generation(
            temporary=temporary,
            generation_root=generation_root,
        )

    assert expected.is_dir()
    assert training._artifact_records(expected) == records
    assert not temporary.exists()
