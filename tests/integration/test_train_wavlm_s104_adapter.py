from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import torch
from torch import nn

from test_prepare_wavlm_s104_adapter import _fixture
from voice_coach.experiments.registry import ExperimentRun
from voice_coach.experiments import registry as registry_module
from voice_coach.training import wavlm_adapter_contract as contract
from voice_coach.training import wavlm_adapter_training as training


def _state(
    config: Mapping[str, Any],
    lock: Mapping[str, Any],
) -> dict[str, torch.Tensor]:
    selected = config["candidates"][lock["selected_candidate"]["variant"]]
    shapes = training._expected_trainable_tensor_shapes(
        selected_candidate=selected,
        adapter=config["adapter"],
        heads=config["heads"],
        target_vocabulary_size=2,
    )
    return {
        name: torch.zeros(shape, dtype=torch.float32) for name, shape in shapes.items()
    }


def _audit(
    state: Mapping[str, torch.Tensor],
    *,
    allow_patterns: list[str],
) -> dict[str, Any]:
    count = sum(value.numel() for value in state.values())
    frozen = 1
    return {
        "total_parameter_count": count + frozen,
        "trainable_parameter_count": count,
        "frozen_parameter_count": frozen,
        "trainable_fraction": count / (count + frozen),
        "trainable_tensor_count": len(state),
        "trainable_parameter_names": sorted(state),
        "allow_patterns": allow_patterns,
        "backbone_non_adapter_trainable_count": 0,
        "full_fine_tuning": False,
    }


def _initialize_artifact_tree(root: Path) -> None:
    """Create only the scaffold that this integration fixture rewrites."""

    for seed in training.SEEDS:
        seed_directory = root / f"seed_{seed}"
        seed_directory.mkdir(parents=True)
        training.atomic_write_json(
            seed_directory / "resource_and_trainable_parameter_report.json",
            {
                "schema_version": training.REPORT_SCHEMA_VERSION,
                "seed": seed,
                "trainable_parameter_audit": {},
                "checkpoint": {},
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
    training.atomic_write_json(
        root / "experiment_report.json",
        {
            "schema_version": training.REPORT_SCHEMA_VERSION,
            "status": "completed",
            "runs": [
                {
                    "seed": seed,
                    "selection": {},
                    "dev": {
                        "inference_passes": 1,
                        "used_for_selection": False,
                        "metrics": {},
                        "engineering_targets": {},
                        "word_metric_status": "not_evaluable",
                    },
                }
                for seed in training.SEEDS
            ],
            "summary": {
                "seeds_completed": list(training.SEEDS),
                "phone_macro_f1": {},
                "comprehensibility_icc_2_1": {},
                "engineering_targets": {},
                "word_metric_status": "not_evaluable",
                "dev_inference_passes_per_seed": 1,
                "dev_used_for_selection": False,
                "locked_test_remains_unread": True,
                "stack_remains_unread": True,
                "calibration_remains_unread": True,
            },
        },
    )


def _rewrite_truth_artifacts(
    root: Path,
    expected: Mapping[str, training.ExpectedDevUtterance],
) -> dict[str, Any]:
    assert len(expected) == 1
    identifier, truth = next(iter(expected.items()))
    report = dict(
        contract.load_json_strict(
            root / "experiment_report.json",
            "fixture report",
        )
    )
    metrics_by_seed: dict[int, Mapping[str, Any]] = {}
    for seed in training.SEEDS:
        seed_directory = root / f"seed_{seed}"
        phone_path = seed_directory / "phone_dev_predictions.parquet"
        utterance_path = seed_directory / "utterance_dev_predictions.parquet"
        guard = training.DevPassGuard()
        with training.PredictionWriters(
            phone_path=phone_path,
            utterance_path=utterance_path,
        ) as writers:
            bundle = _fake_prediction_bundle(
                seed=seed,
                identifier=identifier,
                truth=truth,
                writers=writers,
                dev_guard=guard,
            )
        guard.assert_complete()
        metrics = training._safe_json(training.metric_bundle(bundle, threshold=0.5))
        metrics_by_seed[seed] = metrics
        slices = training.slice_metrics_from_prediction_files(
            phone_path=phone_path,
            utterance_path=utterance_path,
            threshold=0.5,
        )
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
        run = next(item for item in report["runs"] if item["seed"] == seed)
        run["selection"] = {
            "best_epoch": 2,
            "decision_threshold": 0.5,
            "inner_phone_macro_f1": 1.0,
            "inner_comprehensibility_icc_2_1": None,
            "secondary_metric_defined": False,
            "undefined_secondary_failure_reason": ("icc_2_1_undefined_no_improvement"),
            "stopped_early": False,
            "history": [
                {
                    "epoch": 1,
                    "train_mean_joint_loss": 1.0,
                    "optimizer_steps": 1,
                    "inner_phone_macro_f1": 0.5,
                    "inner_phone_threshold": 0.5,
                    "inner_comprehensibility_icc_2_1": None,
                    "inner_comprehensibility_icc_defined": False,
                    "selected_as_best": False,
                },
                {
                    "epoch": 2,
                    "train_mean_joint_loss": 0.5,
                    "optimizer_steps": 1,
                    "inner_phone_macro_f1": 1.0,
                    "inner_phone_threshold": 0.5,
                    "inner_comprehensibility_icc_2_1": None,
                    "inner_comprehensibility_icc_defined": False,
                    "selected_as_best": True,
                },
            ],
            "refit_performed": False,
        }
        run["dev"]["metrics"] = metrics
        phone_f1 = float(metrics["phone"]["macro_f1"])
        primary_icc = metrics["utterance"]["outcomes"]["comprehensibility_score"][
            "icc_2_1"
        ]
        run["dev"]["engineering_targets"] = {
            "phone_macro_f1_threshold": 0.75,
            "primary_utterance_icc_2_1_threshold": 0.65,
            "phone_passed": phone_f1 >= 0.75,
            "primary_utterance_passed": (
                primary_icc is not None and float(primary_icc) >= 0.65
            ),
            "development_only_not_promotion": True,
        }
    phone_summary = training._aggregate(
        [float(metrics_by_seed[seed]["phone"]["macro_f1"]) for seed in training.SEEDS]
    )
    icc_summary = training._aggregate(
        [
            metrics_by_seed[seed]["utterance"]["outcomes"]["comprehensibility_score"][
                "icc_2_1"
            ]
            for seed in training.SEEDS
        ]
    )
    report["summary"]["phone_macro_f1"] = phone_summary
    report["summary"]["comprehensibility_icc_2_1"] = icc_summary
    report["summary"]["engineering_targets"] = {
        "phone_macro_f1_threshold": 0.75,
        "primary_utterance_icc_2_1_threshold": 0.65,
        "mean_phone_passed": (
            phone_summary["mean"] is not None and float(phone_summary["mean"]) >= 0.75
        ),
        "mean_primary_utterance_passed": (
            icc_summary["mean"] is not None and float(icc_summary["mean"]) >= 0.65
        ),
        "development_only_not_promotion": True,
    }
    return report


def _complete_publication(
    tmp_path: Path,
    *,
    publish_commit: bool,
) -> tuple[Path, Path, Mapping[str, Any], Mapping[str, Any], Path]:
    workspace, config_path = _fixture(tmp_path, passed=False)
    lock, lock_path = contract.prepare_s104_execution(
        workspace_root=workspace,
        config_path=config_path,
    )
    config = contract.load_config(config_path)
    outputs = config["outputs"]
    generation_root = workspace / outputs["generation_root"]
    temporary = generation_root / ".part-fixture"
    temporary.mkdir(parents=True)
    _initialize_artifact_tree(temporary)
    expected_truth = training._load_expected_dev_truth(
        workspace_root=workspace,
        config=config,
    )

    selected_variant = lock["selected_candidate"]["variant"]
    selected = config["candidates"][selected_variant]
    state = _state(config, lock)
    fit_metadata = training.FitMetadata(
        target_vocabulary=("<UNK>", "AA"),
        positive_phone_rows=1,
        negative_phone_rows=1,
        pos_weight=1.0,
        fit_utterances=1,
    )
    for seed in training.SEEDS:
        seed_directory = temporary / f"seed_{seed}"
        checkpoint = training.save_adapter_checkpoint(
            seed_directory / "adapter_and_heads.safetensors",
            selection=training.SelectionResult(
                best_epoch=2,
                threshold=0.5,
                phone_macro_f1=1.0,
                comprehensibility_icc_2_1=None,
                stopped_early=False,
                history=(),
                trainable_state=state,
            ),
            seed=seed,
            selected_candidate=selected,
            metadata=fit_metadata,
        )
        resource_path = seed_directory / "resource_and_trainable_parameter_report.json"
        resource = dict(contract.load_json_strict(resource_path, "fixture resource"))
        resource["trainable_parameter_audit"] = _audit(
            state,
            allow_patterns=config["adapter"]["trainable_name_patterns"],
        )
        resource["checkpoint"] = checkpoint
        training.atomic_write_json(resource_path, resource)
    _, model_config, model_checkpoint = training.local_snapshot_paths(
        workspace_root=workspace,
        selected_candidate=selected,
    )
    implementation, implementation_paths = contract.validate_implementation_sources(
        workspace_root=workspace,
        config=config,
    )
    report_path = temporary / "experiment_report.json"
    report = _rewrite_truth_artifacts(temporary, expected_truth)
    report.update(
        {
            "experiment_id": config["experiment"]["id"],
            "development_only": True,
            "oracle_alignment": True,
            "silver_label": True,
            "promotion_decision_allowed": False,
            "started_at_utc": "2026-01-01T00:00:00+00:00",
            "completed_at_utc": "2026-01-01T00:01:00+00:00",
            "selected_candidate": dict(selected),
            "s103_branch_evidence": dict(lock["s103_branch_evidence"]),
            "sources": {
                "config_sha256": contract.sha256_file(config_path),
                "execution_lock_sha256": contract.sha256_file(lock_path),
                "model_config_sha256": contract.sha256_file(model_config),
                "model_checkpoint_sha256": contract.sha256_file(model_checkpoint),
                "implementation": implementation,
            },
            "selection_contract": {
                "source": "train_inner_validation_only",
                "order": [
                    "phone_macro_f1",
                    "comprehensibility_score_icc_2_1",
                    "earliest_epoch",
                ],
                "undefined_secondary_metric_policy": "no_improvement",
                "refit_after_inner_selection": False,
                "dev_used_for_selection": False,
                "phone_loss_reduction": "mean_per_utterance",
                "utterance_loss_reduction": ("mean_observed_outcomes_per_utterance"),
            },
            "leakage": {
                "primary_analysis_scope": config["data"]["primary_analysis_scope"],
                "stack_accessed": False,
                "calibration_accessed": False,
                "locked_test_accessed": False,
                "environment_files_read": [],
                "pooled_feature_cache_read": False,
                "raw_audio_streaming": True,
            },
            "runtime": {
                "device": {
                    "type": "cuda",
                    "index": 0,
                    "name": "Synthetic GPU",
                    "compute_capability": "12.0",
                    "torch_version": torch.__version__,
                    "cuda_version": torch.version.cuda,
                    "mixed_precision": "bfloat16",
                    "total_memory_bytes": 1024,
                },
                "deterministic_algorithms": True,
                "cublas_workspace_config": ":4096:8",
                "gradient_checkpointing": True,
                "encoder_layerdrop": 0.0,
                "apply_spec_augment": False,
                "freeze_feature_encoder_internal_gradient": True,
                "short_chunk_attention_policy": ("padded_receptive_field_marked_valid"),
                "frozen_backbone_mode_during_training": "train_with_dropout",
            },
        }
    )
    training.atomic_write_json(report_path, report)
    published = training.publish_content_addressed_generation(
        temporary=temporary,
        generation_root=generation_root,
    )

    data = config["data"]
    registry_root = workspace / outputs["registry_root"]
    registry = ExperimentRun(
        registry_root,
        run_id=outputs["planned_registry_run_id"],
        input_files={
            "config": [config_path, lock_path, *implementation_paths],
            "data": [
                workspace / data["modeling_manifest"],
                workspace / data["prompt_challenge_reservation"],
                workspace / data["explicit_utterance_labels"],
            ],
            "model": [model_config, model_checkpoint],
            "checkpoint": [],
        },
        seeds={f"experiment_seed_{seed}": seed for seed in training.SEEDS},
        argv=["synthetic-s104"],
        repo_path=workspace,
        package_names=(),
    )
    with registry as run:
        for seed_report in report["runs"]:
            values = {
                "phone_macro_f1": float(
                    seed_report["dev"]["metrics"]["phone"]["macro_f1"]
                )
            }
            primary_icc = seed_report["dev"]["metrics"]["utterance"]["outcomes"][
                "comprehensibility_score"
            ]["icc_2_1"]
            if primary_icc is not None:
                values["comprehensibility_icc_2_1"] = float(primary_icc)
            run.log_metrics(values, step=seed_report["seed"], split="dev")
        for record in published.artifacts:
            run.add_artifact(
                str(record["name"]),
                published.generation_path / str(record["path"]),
                kind="s104_development_evidence",
            )
    registry_document = dict(
        contract.load_json_strict(
            registry.metadata_path,
            "fixture registry",
        )
    )
    registry_document["accelerator"] = {
        "cuda_available": True,
        "cuda_runtime_version": "13.0",
        "torch_device_count": 1,
        "torch_devices": [
            {
                "index": 0,
                "name": "Synthetic GPU",
                "compute_capability": "12.0",
                "memory_total_bytes": 1024,
            }
        ],
        "nvidia_smi": {"available": True, "gpus": []},
    }
    registry_document["git"] = {
        "available": True,
        "commit": "a" * 40,
        "dirty": False,
    }
    training.atomic_write_json(registry.metadata_path, registry_document)

    commit_path = workspace / outputs["commit_manifest"]
    if publish_commit:
        commit = training._build_commit_payload(
            workspace_root=workspace,
            generation=published,
            registry_path=registry.metadata_path,
            execution_lock_path=lock_path,
            expected_execution_lock_sha256=contract.sha256_file(lock_path),
        )
        training.publish_immutable_json(commit_path, commit)
    return workspace, config_path, config, lock, lock_path


def test_completed_commit_validator_cross_binds_registry_and_generation(
    tmp_path: Path,
) -> None:
    workspace, config_path, _, _, _ = _complete_publication(
        tmp_path,
        publish_commit=True,
    )

    result = training.validate_s104_commit(
        workspace_root=workspace,
        config_path=config_path,
    )

    assert result["status"] == "valid"
    assert result["artifact_count"] == 16
    assert result["locked_test_accessed"] is False


def test_completed_commit_validator_rejects_registry_hash_chain_change(
    tmp_path: Path,
) -> None:
    workspace, config_path, config, _, _ = _complete_publication(
        tmp_path,
        publish_commit=True,
    )
    registry_path = (
        workspace
        / config["outputs"]["registry_root"]
        / config["outputs"]["planned_registry_run_id"]
        / "metadata.json"
    )
    registry = dict(contract.load_json_strict(registry_path, "registry"))
    registry["metrics"][0]["values"]["phone_macro_f1"] = 0.123
    training.atomic_write_json(registry_path, registry)

    with pytest.raises(training.S104TrainingError, match="registry record"):
        training.validate_s104_commit(
            workspace_root=workspace,
            config_path=config_path,
        )


def test_completed_commit_validator_rejects_s103_predecessor_change(
    tmp_path: Path,
) -> None:
    workspace, config_path, config, _, _ = _complete_publication(
        tmp_path,
        publish_commit=True,
    )
    s103_commit = contract.load_json_strict(
        workspace / config["s103"]["commit_manifest"],
        "fixture S103 commit",
    )
    s103_report = contract.load_json_strict(
        workspace / s103_commit["comparison_report"]["path"],
        "fixture S103 report",
    )
    predecessor = workspace / s103_report["sources"]["config"]["path"]
    predecessor.write_bytes(predecessor.read_bytes() + b"tamper")

    with pytest.raises(
        training.S104TrainingError,
        match="S103 predecessor lineage is invalid",
    ):
        training.validate_s104_commit(
            workspace_root=workspace,
            config_path=config_path,
        )


def test_artifact_validator_rejects_prediction_ground_truth_substitution(
    tmp_path: Path,
) -> None:
    workspace, _, config, lock, _ = _complete_publication(
        tmp_path,
        publish_commit=True,
    )
    commit = contract.load_json_strict(
        workspace / config["outputs"]["commit_manifest"],
        "commit",
    )
    generation = workspace / commit["generation_path"]
    phone_path = generation / "seed_42/phone_dev_predictions.parquet"
    rows = pq.read_table(phone_path).to_pylist()
    rows[0]["target_label"] = "ZZ"
    pq.write_table(
        pa.Table.from_pylist(
            rows,
            schema=training._phone_prediction_schema(),
        ),
        phone_path,
    )
    selected = config["candidates"][lock["selected_candidate"]["variant"]]

    with pytest.raises(training.S104TrainingError, match="ground truth"):
        training.validate_generated_artifact_tree(
            generation,
            allowed_l1={"Chinese", "Japanese"},
            allowed_gender={"F", "M"},
            expected_fit_utterances=1,
            expected_dev_utterances=1,
            engineering_phone_threshold=0.75,
            engineering_icc_threshold=0.65,
            selected_candidate=selected,
            adapter_config=config["adapter"],
            heads_config=config["heads"],
            expected_dev_truth=training._load_expected_dev_truth(
                workspace_root=workspace,
                config=config,
            ),
            expected_fit_metadata=training._load_expected_fit_metadata(
                workspace_root=workspace,
                config=config,
            ),
        )


def test_runner_recovers_completed_registry_when_commit_is_absent(
    tmp_path: Path,
) -> None:
    workspace, config_path, config, lock, lock_path = _complete_publication(
        tmp_path,
        publish_commit=False,
    )

    commit = training.run_online_training(
        workspace_root=workspace,
        config_path=config_path,
        config=config,
        execution_lock_path=lock_path,
        execution_lock=lock,
        argv=["synthetic-recovery"],
    )

    assert commit["status"] == "committed"
    assert (workspace / config["outputs"]["commit_manifest"]).is_file()


class _FakeModel(nn.Module):
    pass


def _fake_prediction_bundle(
    *,
    seed: int,
    identifier: str,
    truth: training.ExpectedDevUtterance,
    writers: training.PredictionWriters | None,
    dev_guard: training.DevPassGuard | None,
) -> training.PredictionBundle:
    phone_targets = np.asarray(
        [binary for _, binary in truth.phones],
        dtype=np.int8,
    )
    phone_probabilities = np.asarray(
        [0.75 if binary else 0.25 for binary in phone_targets],
        dtype=np.float32,
    )
    utterance_targets = np.asarray([truth.scores], dtype=np.float32)
    utterance_predictions = np.asarray(
        [[3.25, 3.25, 3.25]],
        dtype=np.float32,
    )
    utterance_observed = np.isfinite(utterance_targets)
    if writers is not None:
        assert dev_guard is not None
        dev_guard.enter()
        for index, ((target_label, binary), probability) in enumerate(
            zip(truth.phones, phone_probabilities, strict=True)
        ):
            writers.add_phone(
                {
                    "schema_version": training.PHONE_PREDICTION_SCHEMA_VERSION,
                    "seed": seed,
                    "split": "dev",
                    "development_only": True,
                    "oracle_alignment": True,
                    "utterance_id_sha256": identifier,
                    "speaker_id_sha256": truth.speaker_id_sha256,
                    "phone_index": index,
                    "target_label": target_label,
                    "binary_error_label": binary,
                    "error_probability": float(probability),
                    "error_prediction": int(float(probability) >= 0.5),
                    "l1": truth.l1,
                    "gender": truth.gender,
                    "proficiency": truth.proficiency,
                }
            )
        row: dict[str, Any] = {
            "schema_version": training.UTTERANCE_PREDICTION_SCHEMA_VERSION,
            "seed": seed,
            "split": "dev",
            "development_only": True,
            "oracle_alignment": True,
            "utterance_id_sha256": identifier,
            "speaker_id_sha256": truth.speaker_id_sha256,
            "l1": truth.l1,
            "gender": truth.gender,
            "proficiency": truth.proficiency,
        }
        for index, outcome in enumerate(training.OUTCOMES):
            observed = bool(utterance_observed[0, index])
            row[f"{outcome}_true"] = (
                float(utterance_targets[0, index]) if observed else None
            )
            row[f"{outcome}_pred"] = 3.25
            row[f"{outcome}_observed"] = observed
        writers.add_utterance(row)
        dev_guard.complete()
    return training.PredictionBundle(
        phone_targets=phone_targets,
        phone_probabilities=phone_probabilities,
        utterance_targets=utterance_targets,
        utterance_predictions=utterance_predictions,
        utterance_observed=utterance_observed,
        phone_slices={},
        utterance_slices={},
        utterance_rows=1,
        phone_rows=len(phone_targets),
    )


def _patch_cpu_runner(
    monkeypatch: pytest.MonkeyPatch,
    *,
    workspace: Path,
    config: Mapping[str, Any],
    lock: Mapping[str, Any],
    oom: bool,
) -> list[int]:
    attempts: list[int] = []
    monkeypatch.setenv("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    state = _state(config, lock)
    expected_truth = training._load_expected_dev_truth(
        workspace_root=workspace,
        config=config,
    )
    assert len(expected_truth) == 1
    identifier, truth = next(iter(expected_truth.items()))
    data = config["data"]
    actual_labels = training.load_utterance_labels(
        workspace / data["explicit_utterance_labels"]
    )
    actual_reserved = training.load_reserved_prompt_hashes(
        workspace / data["prompt_challenge_reservation"]
    )
    monkeypatch.setattr(training, "_select_device", lambda config: torch.device("cpu"))
    monkeypatch.setattr(
        training,
        "_device_metadata",
        lambda device: {
            "type": "cuda",
            "index": 0,
            "name": "Synthetic GPU",
            "compute_capability": "12.0",
            "torch_version": torch.__version__,
            "cuda_version": torch.version.cuda,
            "mixed_precision": "bfloat16",
            "total_memory_bytes": 1024,
        },
    )
    monkeypatch.setattr(
        registry_module,
        "collect_accelerator_metadata",
        lambda: {
            "cuda_available": True,
            "cuda_runtime_version": "13.0",
            "torch_device_count": 1,
            "torch_devices": [
                {
                    "index": 0,
                    "name": "Synthetic GPU",
                    "compute_capability": "12.0",
                    "memory_total_bytes": 1024,
                }
            ],
            "nvidia_smi": {"available": True, "gpus": []},
        },
    )
    monkeypatch.setattr(
        registry_module,
        "collect_git_metadata",
        lambda repo_path: {
            "available": True,
            "commit": "a" * 40,
            "dirty": False,
        },
    )
    monkeypatch.setattr(training, "set_deterministic_seed", lambda seed: None)
    monkeypatch.setattr(torch.cuda, "empty_cache", lambda: None)
    monkeypatch.setattr(torch.cuda, "reset_peak_memory_stats", lambda device: None)
    monkeypatch.setattr(torch.cuda, "max_memory_allocated", lambda device: 1)
    monkeypatch.setattr(torch.cuda, "max_memory_reserved", lambda device: 2)
    monkeypatch.setattr(
        training,
        "load_reserved_prompt_hashes",
        lambda path: actual_reserved,
    )
    monkeypatch.setattr(
        training,
        "load_utterance_labels",
        lambda path: actual_labels,
    )
    metadata = training.FitMetadata(
        target_vocabulary=("<UNK>", "AA"),
        positive_phone_rows=1,
        negative_phone_rows=1,
        pos_weight=1.0,
        fit_utterances=1,
    )
    monkeypatch.setattr(
        training,
        "scan_fit_metadata",
        lambda **kwargs: metadata,
    )
    monkeypatch.setattr(
        training,
        "iter_examples",
        lambda **kwargs: iter(()),
    )
    monkeypatch.setattr(
        training,
        "build_adapter_model",
        lambda **kwargs: (
            _FakeModel(),
            _audit(
                state,
                allow_patterns=config["adapter"]["trainable_name_patterns"],
            ),
        ),
    )

    def fake_train(**kwargs: Any) -> training.SelectionResult:
        seed = int(kwargs["seed"])
        attempts.append(seed)
        if oom:
            raise torch.OutOfMemoryError("synthetic secret path")
        return training.SelectionResult(
            best_epoch=1,
            threshold=0.5,
            phone_macro_f1=1.0,
            comprehensibility_icc_2_1=None,
            stopped_early=False,
            history=(
                {
                    "epoch": 1,
                    "train_mean_joint_loss": 1.0,
                    "optimizer_steps": 1,
                    "inner_phone_macro_f1": 1.0,
                    "inner_phone_threshold": 0.5,
                    "inner_comprehensibility_icc_2_1": None,
                    "inner_comprehensibility_icc_defined": False,
                    "selected_as_best": True,
                },
            ),
            trainable_state=state,
        )

    monkeypatch.setattr(training, "train_and_select", fake_train)

    def fake_evaluate(**kwargs: Any) -> training.PredictionBundle:
        return _fake_prediction_bundle(
            seed=int(kwargs["seed"]),
            identifier=identifier,
            truth=truth,
            writers=kwargs.get("writers"),
            dev_guard=kwargs.get("dev_guard"),
        )

    monkeypatch.setattr(training, "evaluate_examples", fake_evaluate)
    return attempts


def test_cpu_synthetic_runner_completes_all_three_seeds_and_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path = _fixture(tmp_path, passed=False)
    lock, lock_path = contract.prepare_s104_execution(
        workspace_root=workspace,
        config_path=config_path,
    )
    config = contract.load_config(config_path)
    attempts = _patch_cpu_runner(
        monkeypatch,
        workspace=workspace,
        config=config,
        lock=lock,
        oom=False,
    )

    commit = training.run_online_training(
        workspace_root=workspace,
        config_path=config_path,
        config=config,
        execution_lock_path=lock_path,
        execution_lock=lock,
        argv=["synthetic-full-run"],
    )

    assert attempts == [42, 43, 44]
    assert commit["status"] == "committed"
    assert len(commit["artifacts"]) == 16
    assert (
        training.validate_s104_commit(
            workspace_root=workspace,
            config_path=config_path,
        )["status"]
        == "valid"
    )


def test_cpu_synthetic_runner_records_oom_once_and_leaves_no_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path = _fixture(tmp_path, passed=False)
    lock, lock_path = contract.prepare_s104_execution(
        workspace_root=workspace,
        config_path=config_path,
    )
    config = contract.load_config(config_path)
    attempts = _patch_cpu_runner(
        monkeypatch,
        workspace=workspace,
        config=config,
        lock=lock,
        oom=True,
    )

    with pytest.raises(torch.OutOfMemoryError):
        training.run_online_training(
            workspace_root=workspace,
            config_path=config_path,
            config=config,
            execution_lock_path=lock_path,
            execution_lock=lock,
            argv=["synthetic-oom"],
        )

    registry_path = (
        workspace
        / config["outputs"]["registry_root"]
        / config["outputs"]["planned_registry_run_id"]
        / "metadata.json"
    )
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert attempts == [42]
    assert registry["status"] == "failed"
    assert registry["failure"] == {
        "exception_type": "torch.OutOfMemoryError",
        "stage": "seed_training",
        "oom": True,
    }
    generation_root = workspace / config["outputs"]["generation_root"]
    assert list(generation_root.iterdir()) == []
    assert not (workspace / config["outputs"]["commit_manifest"]).exists()


def test_registry_registration_failure_removes_new_uncommitted_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path = _fixture(tmp_path, passed=False)
    lock, lock_path = contract.prepare_s104_execution(
        workspace_root=workspace,
        config_path=config_path,
    )
    config = contract.load_config(config_path)
    _patch_cpu_runner(
        monkeypatch,
        workspace=workspace,
        config=config,
        lock=lock,
        oom=False,
    )

    def fail_registration(
        self: ExperimentRun,
        name: str,
        path: str | Path,
        *,
        kind: str = "file",
    ) -> None:
        del self, name, path, kind
        raise OSError("synthetic registry registration failure")

    monkeypatch.setattr(ExperimentRun, "add_artifact", fail_registration)
    with pytest.raises(OSError, match="registration failure"):
        training.run_online_training(
            workspace_root=workspace,
            config_path=config_path,
            config=config,
            execution_lock_path=lock_path,
            execution_lock=lock,
            argv=["synthetic-registry-failure"],
        )

    registry_path = (
        workspace
        / config["outputs"]["registry_root"]
        / config["outputs"]["planned_registry_run_id"]
        / "metadata.json"
    )
    registry = contract.load_json_strict(registry_path, "failed registry")
    assert registry["status"] == "failed"
    assert registry["failure"]["stage"] == "registry_registration"
    assert list((workspace / config["outputs"]["generation_root"]).iterdir()) == []
    assert not (workspace / config["outputs"]["commit_manifest"]).exists()


def test_generation_postrename_fsync_failure_removes_orphan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path = _fixture(tmp_path, passed=False)
    lock, lock_path = contract.prepare_s104_execution(
        workspace_root=workspace,
        config_path=config_path,
    )
    config = contract.load_config(config_path)
    _patch_cpu_runner(
        monkeypatch,
        workspace=workspace,
        config=config,
        lock=lock,
        oom=False,
    )
    generation_root = workspace / config["outputs"]["generation_root"]
    original = training._fsync_directory
    failed = False

    def fail_once(path: Path) -> None:
        nonlocal failed
        if path == generation_root and not failed:
            failed = True
            raise OSError("synthetic generation durability failure")
        original(path)

    monkeypatch.setattr(training, "_fsync_directory", fail_once)
    with pytest.raises(
        training.GenerationPublicationDurabilityError,
        match="durability",
    ):
        training.run_online_training(
            workspace_root=workspace,
            config_path=config_path,
            config=config,
            execution_lock_path=lock_path,
            execution_lock=lock,
            argv=["synthetic-generation-fsync-failure"],
        )

    registry_path = (
        workspace
        / config["outputs"]["registry_root"]
        / config["outputs"]["planned_registry_run_id"]
        / "metadata.json"
    )
    registry = contract.load_json_strict(registry_path, "failed registry")
    assert registry["status"] == "failed"
    assert registry["failure"]["stage"] == "artifact_publication"
    assert list(generation_root.iterdir()) == []
    assert not (workspace / config["outputs"]["commit_manifest"]).exists()


def test_commit_prerename_failure_is_recovered_without_retraining(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path = _fixture(tmp_path, passed=False)
    lock, lock_path = contract.prepare_s104_execution(
        workspace_root=workspace,
        config_path=config_path,
    )
    config = contract.load_config(config_path)
    attempts = _patch_cpu_runner(
        monkeypatch,
        workspace=workspace,
        config=config,
        lock=lock,
        oom=False,
    )
    original = training.publish_immutable_json

    def fail_before_rename(
        path: Path,
        value: Mapping[str, Any],
    ) -> None:
        del path, value
        raise OSError("synthetic commit pre-rename failure")

    monkeypatch.setattr(training, "publish_immutable_json", fail_before_rename)
    with pytest.raises(OSError, match="pre-rename"):
        training.run_online_training(
            workspace_root=workspace,
            config_path=config_path,
            config=config,
            execution_lock_path=lock_path,
            execution_lock=lock,
            argv=["synthetic-commit-failure"],
        )
    commit_path = workspace / config["outputs"]["commit_manifest"]
    assert not commit_path.exists()
    assert len(list((workspace / config["outputs"]["generation_root"]).iterdir())) == 1
    registry_path = (
        workspace
        / config["outputs"]["registry_root"]
        / config["outputs"]["planned_registry_run_id"]
        / "metadata.json"
    )
    assert (
        contract.load_json_strict(
            registry_path,
            "recoverable registry",
        )["status"]
        == "completed"
    )

    monkeypatch.setattr(training, "publish_immutable_json", original)
    recovered = training.run_online_training(
        workspace_root=workspace,
        config_path=config_path,
        config=config,
        execution_lock_path=lock_path,
        execution_lock=lock,
        argv=["synthetic-commit-recovery"],
    )
    assert recovered["status"] == "committed"
    assert attempts == [42, 43, 44]


def test_commit_postrename_fsync_failure_is_idempotently_validated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path = _fixture(tmp_path, passed=False)
    lock, lock_path = contract.prepare_s104_execution(
        workspace_root=workspace,
        config_path=config_path,
    )
    config = contract.load_config(config_path)
    attempts = _patch_cpu_runner(
        monkeypatch,
        workspace=workspace,
        config=config,
        lock=lock,
        oom=False,
    )
    commit_path = workspace / config["outputs"]["commit_manifest"]
    original_fsync = training._fsync_directory

    def fail_commit_parent(path: Path) -> None:
        if path == commit_path.parent and commit_path.exists():
            raise OSError("synthetic commit post-rename fsync failure")
        original_fsync(path)

    monkeypatch.setattr(training, "_fsync_directory", fail_commit_parent)
    with pytest.raises(OSError, match="post-rename"):
        training.run_online_training(
            workspace_root=workspace,
            config_path=config_path,
            config=config,
            execution_lock_path=lock_path,
            execution_lock=lock,
            argv=["synthetic-postrename-failure"],
        )
    assert commit_path.is_file()

    monkeypatch.setattr(training, "_fsync_directory", original_fsync)
    repeated = training.run_online_training(
        workspace_root=workspace,
        config_path=config_path,
        config=config,
        execution_lock_path=lock_path,
        execution_lock=lock,
        argv=["synthetic-postrename-retry"],
    )
    assert repeated["status"] == "committed"
    assert attempts == [42, 43, 44]


def test_precommit_validation_failure_downgrades_registry_and_cleans_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path = _fixture(tmp_path, passed=False)
    lock, lock_path = contract.prepare_s104_execution(
        workspace_root=workspace,
        config_path=config_path,
    )
    config = contract.load_config(config_path)
    _patch_cpu_runner(
        monkeypatch,
        workspace=workspace,
        config=config,
        lock=lock,
        oom=False,
    )

    def fail_precommit(**kwargs: Any) -> None:
        del kwargs
        raise training.S104TrainingError("synthetic precommit mismatch")

    monkeypatch.setattr(
        training,
        "_validate_precommit_evidence",
        fail_precommit,
    )
    with pytest.raises(training.S104TrainingError, match="precommit mismatch"):
        training.run_online_training(
            workspace_root=workspace,
            config_path=config_path,
            config=config,
            execution_lock_path=lock_path,
            execution_lock=lock,
            argv=["synthetic-precommit-failure"],
        )

    registry_path = (
        workspace
        / config["outputs"]["registry_root"]
        / config["outputs"]["planned_registry_run_id"]
        / "metadata.json"
    )
    registry = contract.load_json_strict(registry_path, "failed registry")
    assert registry["status"] == "failed"
    assert registry["failure"]["stage"] == "commit_publication"
    assert list((workspace / config["outputs"]["generation_root"]).iterdir()) == []
    assert not (workspace / config["outputs"]["commit_manifest"]).exists()


@pytest.mark.parametrize("post_rename", [False, True])
def test_registry_initialization_fault_is_retryable_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    post_rename: bool,
) -> None:
    workspace, config_path = _fixture(tmp_path, passed=False)
    lock, lock_path = contract.prepare_s104_execution(
        workspace_root=workspace,
        config_path=config_path,
    )
    config = contract.load_config(config_path)
    _patch_cpu_runner(
        monkeypatch,
        workspace=workspace,
        config=config,
        lock=lock,
        oom=False,
    )
    original = registry_module._atomic_write_json

    def fail_registry_start(
        path: Path,
        payload: Mapping[str, Any],
    ) -> None:
        if post_rename:
            original(path, payload)
        raise OSError("synthetic registry initialization failure")

    monkeypatch.setattr(
        registry_module,
        "_atomic_write_json",
        fail_registry_start,
    )
    with pytest.raises(OSError, match="initialization failure"):
        training.run_online_training(
            workspace_root=workspace,
            config_path=config_path,
            config=config,
            execution_lock_path=lock_path,
            execution_lock=lock,
            argv=["synthetic-registry-init-failure"],
        )

    run_directory = (
        workspace
        / config["outputs"]["registry_root"]
        / config["outputs"]["planned_registry_run_id"]
    )
    assert not run_directory.exists()
    assert list((workspace / config["outputs"]["generation_root"]).iterdir()) == []
    assert not (workspace / config["outputs"]["commit_manifest"]).exists()


@pytest.mark.parametrize("post_rename", [False, True])
def test_registry_finalization_fault_is_recorded_failed_and_cleans_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    post_rename: bool,
) -> None:
    workspace, config_path = _fixture(tmp_path, passed=False)
    lock, lock_path = contract.prepare_s104_execution(
        workspace_root=workspace,
        config_path=config_path,
    )
    config = contract.load_config(config_path)
    _patch_cpu_runner(
        monkeypatch,
        workspace=workspace,
        config=config,
        lock=lock,
        oom=False,
    )
    original = registry_module._atomic_write_json
    failed_once = False

    def fail_registry_completion(
        path: Path,
        payload: Mapping[str, Any],
    ) -> None:
        nonlocal failed_once
        if payload.get("status") == "completed" and not failed_once:
            failed_once = True
            if post_rename:
                original(path, payload)
            raise OSError("synthetic registry finalization failure")
        original(path, payload)

    monkeypatch.setattr(
        registry_module,
        "_atomic_write_json",
        fail_registry_completion,
    )
    with pytest.raises(OSError, match="finalization failure"):
        training.run_online_training(
            workspace_root=workspace,
            config_path=config_path,
            config=config,
            execution_lock_path=lock_path,
            execution_lock=lock,
            argv=["synthetic-registry-finalize-failure"],
        )

    registry_path = (
        workspace
        / config["outputs"]["registry_root"]
        / config["outputs"]["planned_registry_run_id"]
        / "metadata.json"
    )
    registry = contract.load_json_strict(registry_path, "failed registry")
    assert registry["status"] == "failed"
    assert registry["failure"]["stage"] == "registry_registration"
    assert list((workspace / config["outputs"]["generation_root"]).iterdir()) == []
    assert not (workspace / config["outputs"]["commit_manifest"]).exists()
