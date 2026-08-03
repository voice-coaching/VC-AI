from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from voice_coach.features.gop import PHONE_INVENTORY
from voice_coach.training import wavlm_s106_component_projection as projection
from voice_coach.training import wavlm_s106_fusion_contract as contract


ROOT = Path(__file__).resolve().parents[2]


def _checkpoint(sha256: str) -> dict[str, Any]:
    return {
        "path": f"artifacts/synthetic/{sha256[:8]}.bin",
        "sha256": sha256,
        "size_bytes": 1,
    }


def _file_record(path: Path, workspace: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(workspace).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size,
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contract.canonical_json_bytes(value, pretty=True))


def _predecessors(workspace: Path) -> dict[str, Any]:
    source_paths = {
        "s104": (
            contract.S104_CONFIG_PATH,
            contract.S104_VALIDATOR_MODULE_PATH,
        ),
        "s105": (
            contract.S105_CONFIG_PATH,
            contract.S105_VALIDATOR_MODULE_PATH,
        ),
        "b07": (
            contract.B07_CONFIG_PATH,
            contract.B07_VALIDATOR_MODULE_PATH,
        ),
    }
    for config_path, module_path in source_paths.values():
        for relative in (config_path, module_path):
            destination = workspace / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)

    output: dict[str, Any] = {}
    for index, stage in enumerate(("s104", "s105", "b07"), start=1):
        generation = workspace / f"artifacts/synthetic/{stage}/generation"
        report_name = {
            "s104": "experiment_report.json",
            "s105": "ablation_comparison_report.json",
            "b07": "aggregate_report.json",
        }[stage]
        report = generation / report_name
        _write_json(report, {"status": "completed", "stage": stage})
        report_record = _file_record(report, workspace)
        commit_path = workspace / f"artifacts/synthetic/{stage}/commit.json"
        if stage == "s104":
            commit = {
                "generation_path": generation.relative_to(workspace).as_posix(),
                "artifacts": [
                    {
                        "name": "experiment_report",
                        "path": report_name,
                        "sha256": report_record["sha256"],
                        "size_bytes": report_record["size_bytes"],
                    }
                ],
                "experiment_report_sha256": report_record["sha256"],
            }
        elif stage == "s105":
            commit = {
                "generation_path": generation.relative_to(workspace).as_posix(),
                "artifacts": [
                    {
                        "name": "ablation_comparison_report",
                        "path": report_name,
                        "sha256": report_record["sha256"],
                        "size_bytes": report_record["size_bytes"],
                    }
                ],
            }
        else:
            commit = {
                "generation": {
                    "path": generation.relative_to(workspace).as_posix(),
                    "records": [
                        {
                            "path": report_name,
                            "sha256": report_record["sha256"],
                            "size_bytes": report_record["size_bytes"],
                        }
                    ],
                }
            }
        _write_json(commit_path, commit)
        config_path, module_path = source_paths[stage]
        output[stage] = {
            "commit_path": commit_path.relative_to(workspace).as_posix(),
            "commit_sha256": hashlib.sha256(commit_path.read_bytes()).hexdigest(),
            "generation_id": f"{index:x}" * 64,
            "report_path": report_record["path"],
            "report_sha256": report_record["sha256"],
            "canonical_sources": {
                "config": _file_record(workspace / config_path, workspace),
                "validator_module": _file_record(
                    workspace / module_path,
                    workspace,
                ),
                "report": report_record,
            },
        }
    return output


def _execution_lock(workspace: Path) -> dict[str, Any]:
    components: dict[str, Any] = {
        "order": list(projection.COMPONENTS),
        "explicit": {
            "sources": {"registry": _checkpoint("1" * 64)},
            "projection_checkpoints": [
                {
                    "seed": seed,
                    "checkpoint": _checkpoint(f"{index + 2:x}" * 64),
                }
                for index, seed in enumerate(projection.SEEDS)
            ],
        },
        "s104_adapter": {
            "source_commit_sha256": "5" * 64,
            "projection_checkpoints": [
                {
                    "seed": seed,
                    "checkpoint": _checkpoint(f"{index + 6:x}" * 64),
                }
                for index, seed in enumerate(projection.SEEDS)
            ],
        },
        "s105_selected_hierarchy": {
            "source_commit_sha256": "9" * 64,
            "projection_checkpoints": [
                {
                    "seed": seed,
                    "checkpoint": _checkpoint(f"{index + 10:x}" * 64),
                }
                for index, seed in enumerate(projection.SEEDS)
            ],
        },
        "gop_acoustic_posterior": {
            "source_commit_sha256": "d" * 64,
            "projection_checkpoints": [
                {
                    "seed": seed,
                    "acoustic_checkpoint": _checkpoint(f"{index + 1:x}" * 64),
                    "error_checkpoint": _checkpoint(f"{index + 4:x}" * 64),
                }
                for index, seed in enumerate(projection.SEEDS)
            ],
        },
    }
    return {
        "schema_version": projection.EXECUTION_LOCK_SCHEMA_VERSION,
        "status": "ready",
        "predecessors": _predecessors(workspace),
        "frozen_components": components,
        "stack_source": {
            "path": "data/synthetic/stack.parquet",
            "sha256": "e" * 64,
        },
    }


def _prediction_records(lock: dict[str, Any]) -> dict[str, Any]:
    records: dict[str, Any] = {}
    frozen = lock["frozen_components"]
    for component in projection.COMPONENTS:
        seeds: dict[str, Any] = {}
        source_lineage = (
            frozen[component]["sources"]["registry"]["sha256"]
            if component == "explicit"
            else frozen[component]["source_commit_sha256"]
        )
        for seed in projection.SEEDS:
            checkpoint = next(
                value
                for value in frozen[component]["projection_checkpoints"]
                if value["seed"] == seed
            )
            checkpoint_hashes = (
                [
                    checkpoint["acoustic_checkpoint"]["sha256"],
                    checkpoint["error_checkpoint"]["sha256"],
                ]
                if component == "gop_acoustic_posterior"
                else [checkpoint["checkpoint"]["sha256"]]
            )
            seeds[f"seed_{seed}"] = {
                "path": (
                    f"{projection.COMPONENT_PREDICTION_ROOT}/{component}/"
                    f"seed_{seed}_stack_predictions.parquet"
                ),
                "sha256": "f" * 64,
                "size_bytes": 1,
                "row_count": 2,
                "schema_version": projection.PREDICTION_RECORD_SCHEMA_VERSION,
                "component": component,
                "seed": seed,
                "split": "stack",
                "source_lineage_sha256": source_lineage,
                "source_checkpoint_sha256s": checkpoint_hashes,
                "projection_attestation": {
                    "label_free": True,
                    "stack_only": True,
                    "binary_error_label_used": False,
                    "realized_phone_identity_used": False,
                    "dev_prediction_reused": False,
                    "representation": (
                        projection.PROJECTION_REPRESENTATIONS[component]
                    ),
                },
            }
        records[component] = seeds
    return records


def _base_record(path: str) -> dict[str, Any]:
    return {
        "path": path,
        "sha256": "a" * 64,
        "size_bytes": 1,
        "row_count": 2,
    }


def _producer_identity() -> dict[str, Any]:
    config = contract.load_config(
        ROOT / "configs/experiments/wavlm_s106_stack_fusion_v1.yaml"
    )
    roles = (
        "component_projection_module",
        "component_generator_config",
        "component_generator_script",
        "component_generator_module",
    )
    return {
        "mode": "production",
        "production_schema_allowed": True,
        "dependency_injection_allowed": False,
        "backend_factory": (
            "voice_coach.training.wavlm_s106_component_generator."
            "production_backend_factory"
        ),
        "preflight": (
            "voice_coach.training.wavlm_s106_fusion_contract.prepare_s106_execution"
        ),
        "bundle_builder": (
            "voice_coach.training.wavlm_s106_component_projection."
            "build_component_bundle_document"
        ),
        "pinned_sources": {
            role: dict(config["implementation"][role]) for role in roles
        },
    }


def _build_bundle(
    workspace: Path,
    lock: dict[str, Any],
    records: dict[str, Any],
    *,
    lock_path: str = projection.EXECUTION_LOCK_RELATIVE_PATH,
) -> dict[str, Any]:
    lock_sha256 = hashlib.sha256(
        contract.canonical_json_bytes(lock, pretty=True)
    ).hexdigest()
    return dict(
        projection.build_component_bundle_document(
            workspace_root=workspace,
            execution_lock=lock,
            execution_lock_record={
                "path": lock_path,
                "sha256": lock_sha256,
            },
            stack_source_record=dict(lock["stack_source"]),
            feature_identity_matrix_record=_base_record(
                "data/synthetic/identity.parquet"
            ),
            label_matrix_record=_base_record("data/synthetic/labels.parquet"),
            component_records=records,
            generator_commit_record={
                "path": (
                    "artifacts/experiments/"
                    "aihub71490_wavlm_s106_stack_fusion_v1/"
                    "component_input_commit.json"
                ),
                "sha256": "b" * 64,
                "size_bytes": 1,
            },
            feature_freeze_receipt_record={
                "path": (
                    "artifacts/experiments/"
                    "aihub71490_wavlm_s106_stack_fusion_v1/"
                    "component_feature_freeze_receipt.json"
                ),
                "sha256": "c" * 64,
                "size_bytes": 1,
            },
            producer_identity=_producer_identity(),
        )
    )


def test_bundle_binds_exact_lock_path_and_twelve_stack_predictions(
    tmp_path: Path,
) -> None:
    lock = _execution_lock(tmp_path)
    document = _build_bundle(tmp_path, lock, _prediction_records(lock))
    assert document["projection_audit"]["prediction_file_count"] == 12
    assert tuple(document["components"]) == projection.COMPONENTS
    with pytest.raises(projection.S106ProjectionError, match="execution lock"):
        _build_bundle(
            tmp_path,
            lock,
            _prediction_records(lock),
            lock_path="configs/experiments/wavlm_s106_stack_fusion_v1.yaml",
        )


def test_bundle_rejects_dev_path_even_with_valid_hashes(tmp_path: Path) -> None:
    lock = _execution_lock(tmp_path)
    records = _prediction_records(lock)
    records["gop_acoustic_posterior"]["seed_42"]["path"] = (
        f"{projection.COMPONENT_PREDICTION_ROOT}/gop_acoustic_posterior/"
        "seed_42_dev_predictions.parquet"
    )
    with pytest.raises(projection.S106ProjectionError, match="provenance"):
        _build_bundle(tmp_path, lock, records)


def test_bundle_rechecks_predecessor_canonical_source_bytes(tmp_path: Path) -> None:
    lock = _execution_lock(tmp_path)
    report = (
        tmp_path / lock["predecessors"]["s104"]["canonical_sources"]["report"]["path"]
    )
    report.write_bytes(report.read_bytes() + b"drift")
    with pytest.raises(projection.S106ProjectionError, match="canonical-source"):
        _build_bundle(tmp_path, lock, _prediction_records(lock))


def test_alignment_confidence_absence_is_not_a_projection_blocker(
    tmp_path: Path,
) -> None:
    assert projection.production_projection_blockers(tmp_path) == (
        "s104_validated_commit_absent",
        "s105_validated_commit_absent",
    )


def test_s104_rejects_raw_frozen_embedding_attestation() -> None:
    with pytest.raises(
        projection.S106ProjectionError,
        match="adapter-applied",
    ):
        projection.project_s104_label_free_embeddings(
            model=object(),
            phone_embeddings=object(),
            target_indices=object(),
            representation_attestation={
                "representation": "raw_frozen_embedding",
                "adapter_applied": False,
                "target_interval_pooling": True,
                "label_free": True,
            },
        )


def test_explicit_projection_binds_feature_and_class_order() -> None:
    class Pipeline:
        def __init__(self, classes: list[int]) -> None:
            self.classes_ = np.asarray(classes)
            self.observed: np.ndarray | None = None

        def predict_proba(self, values: np.ndarray) -> np.ndarray:
            self.observed = values.copy()
            return np.asarray([[0.75, 0.25]], dtype=np.float64)

    features = [
        "target_label",
        "previous_target_label",
        "next_target_label",
        "oracle_duration_seconds",
        "segment_rms",
        "segment_zero_crossing_rate",
    ]
    pipeline = Pipeline([0, 1])
    checkpoint: dict[str, Any] = {
        "schema_version": "voice_coach.explicit_baseline_checkpoint.v1",
        "phone": {"features": features, "pipeline": pipeline},
    }
    matrix = np.asarray([["a", None, "b", 0.1, 0.2, 0.3]], dtype=object)
    output = projection.project_explicit_label_free_features(
        checkpoint=checkpoint,
        feature_rows=matrix,
    )
    assert output.tolist() == [0.25]
    assert pipeline.observed is not None
    assert pipeline.observed.tolist() == matrix.tolist()

    reordered = {
        **checkpoint,
        "phone": {
            **checkpoint["phone"],
            "features": [*features[1:], features[0]],
        },
    }
    with pytest.raises(projection.S106ProjectionError, match="feature contract"):
        projection.project_explicit_label_free_features(
            checkpoint=reordered,
            feature_rows=matrix,
        )
    reversed_classes = {
        **checkpoint,
        "phone": {
            **checkpoint["phone"],
            "pipeline": Pipeline([1, 0]),
        },
    }
    with pytest.raises(projection.S106ProjectionError, match="class order"):
        projection.project_explicit_label_free_features(
            checkpoint=reversed_classes,
            feature_rows=matrix,
        )


def test_s105_projection_uses_exact_all_layer_shape_and_head_path() -> None:
    torch = pytest.importorskip("torch")

    class Head(torch.nn.Module):  # type: ignore[name-defined]
        def __init__(self) -> None:
            super().__init__()
            self.observed: tuple[Any, ...] | None = None

        def forward(self, **values: Any) -> Any:
            self.observed = (
                values["phone_layers"],
                values["word_layers"],
                values["utterance_layers"],
                values["phone_to_word"],
                values["target_indices"],
            )
            return SimpleNamespace(
                phone_logits=torch.tensor([0.0, 1.0], dtype=torch.float32)
            )

    head = Head()
    phone_layers = torch.zeros((12, 2, 768), dtype=torch.float32)
    word_layers = torch.zeros((12, 1, 768), dtype=torch.float32)
    utterance_layers = torch.zeros((12, 768), dtype=torch.float32)
    phone_to_word = torch.tensor([0, 0], dtype=torch.long)
    target_indices = torch.tensor([1, 2], dtype=torch.long)
    output = projection.project_s105_label_free_layers(
        head=head,
        phone_layers=phone_layers,
        word_layers=word_layers,
        utterance_layers=utterance_layers,
        phone_to_word=phone_to_word,
        target_indices=target_indices,
        representation_attestation={
            "representation": (
                "adapter_applied_all_encoder_layers_target_interval_pooled"
            ),
            "adapter_applied": True,
            "all_encoder_layers": True,
            "target_interval_pooling": True,
            "label_free": True,
        },
    )
    assert output.shape == (2,)
    assert head.observed == (
        phone_layers,
        word_layers,
        utterance_layers,
        phone_to_word,
        target_indices,
    )
    with pytest.raises(projection.S106ProjectionError, match="input differs"):
        projection.project_s105_label_free_layers(
            head=head,
            phone_layers=phone_layers[:, :0],
            word_layers=word_layers,
            utterance_layers=utterance_layers,
            phone_to_word=phone_to_word[:0],
            target_indices=target_indices[:0],
            representation_attestation={
                "representation": (
                    "adapter_applied_all_encoder_layers_target_interval_pooled"
                ),
                "adapter_applied": True,
                "all_encoder_layers": True,
                "target_interval_pooling": True,
                "label_free": True,
            },
        )


def _serialized_b07_fixture(
    seed: int = 42,
) -> tuple[dict[str, Any], dict[str, Any]]:
    dimension = 768
    prototypes: np.ndarray = np.zeros(
        (len(PHONE_INVENTORY), dimension), dtype=np.float64
    )
    prototypes[np.arange(len(PHONE_INVENTORY)), np.arange(len(PHONE_INVENTORY))] = 1
    vocabularies = {
        "target_label": ["__MISSING__", "__UNKNOWN__", PHONE_INVENTORY[0]],
        "previous_target_label": ["__MISSING__", "__UNKNOWN__"],
        "next_target_label": ["__MISSING__", "__UNKNOWN__"],
    }
    numeric_names = [
        "oracle_duration_seconds",
        "segment_rms",
        "segment_zero_crossing_rate",
        "target_log_posterior",
        "gop_margin",
        "posterior_entropy",
        "target_is_top1",
    ]
    encoded_dimension = sum(len(value) for value in vocabularies.values()) + len(
        numeric_names
    )
    acoustic = {
        "schema_version": "voice_coach.b07_gop_acoustic_checkpoint.v1",
        "seed": seed,
        "inventory": list(PHONE_INVENTORY),
        "embedding_dimension": dimension,
        "selected_temperature": 1.0,
        "full_primary_train_match_prototypes": prototypes.tolist(),
        "stack_inference_sufficient": True,
    }
    error = {
        "schema_version": "voice_coach.b07_gop_error_checkpoint.v1",
        "seed": seed,
        "feature_order": [
            "target_label",
            "previous_target_label",
            "next_target_label",
            *numeric_names,
        ],
        "categorical_vocabularies": vocabularies,
        "numeric_feature_order": numeric_names,
        "numeric_imputation_medians": [0.0] * len(numeric_names),
        "numeric_standardization_means": [0.0] * len(numeric_names),
        "numeric_standardization_scales": [1.0] * len(numeric_names),
        "coefficients": [0.0] * encoded_dimension,
        "intercept": 0.0,
        "selected_c": 1.0,
        "selected_threshold": 0.5,
        "class_weights": {"0": 1.0, "1": 1.0},
        "explicit_refit_control": {},
        "stack_inference_sufficient": True,
    }
    return acoustic, error


def test_serialized_b07_projection_is_label_free_and_finite() -> None:
    acoustic, error = _serialized_b07_fixture()
    validation = projection.validate_b07_projection_checkpoints(
        acoustic_checkpoint=acoustic,
        error_checkpoint=error,
        expected_seed=42,
    )
    assert validation["status"] == "projection_ready"
    embedding: np.ndarray = np.zeros((1, 768), dtype=np.float32)
    embedding[0, 0] = 1.0
    output = projection.project_b07_label_free_batch(
        seed=42,
        identifiers=(
            {
                "utterance_id_sha256": "1" * 64,
                "speaker_id_sha256": "2" * 64,
                "phone_index": 0,
                "split": "stack",
            },
        ),
        embeddings=embedding,
        categorical_rows={
            "target_label": [PHONE_INVENTORY[0]],
            "previous_target_label": [None],
            "next_target_label": [None],
        },
        explicit_numeric_rows=np.asarray([[0.1, 0.2, 0.3]]),
        acoustic_checkpoint=acoustic,
        error_checkpoint=error,
    )
    assert output[0]["probability"] == pytest.approx(0.5)
    assert "reference_binary_error_label" not in output[0]


def test_b07_projection_rejects_cross_seed_checkpoint_pair() -> None:
    acoustic, error = _serialized_b07_fixture()
    error["seed"] = 43
    with pytest.raises(projection.S106ProjectionError, match="contract differs"):
        projection.validate_b07_projection_checkpoints(
            acoustic_checkpoint=acoustic,
            error_checkpoint=error,
            expected_seed=42,
        )
