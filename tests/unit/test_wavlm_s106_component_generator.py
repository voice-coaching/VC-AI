from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import inspect
import os
from pathlib import Path
import shutil
import stat
from types import SimpleNamespace
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml  # type: ignore[import-untyped]

from voice_coach.training import wavlm_s106_component_generator as generator
from voice_coach.training import wavlm_s106_component_projection as projection
from voice_coach.training import wavlm_s106_fusion_contract as contract


ROOT = Path(__file__).resolve().parents[2]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(generator.canonical_json_bytes(value, pretty=True))


def _stack_schema() -> pa.Schema:
    interval = pa.struct(
        [
            pa.field("start", pa.float64()),
            pa.field("end", pa.float64()),
            pa.field("label", pa.string()),
        ]
    )
    alignment = pa.struct(
        [
            pa.field("target_label", pa.string()),
            pa.field("realized_label", pa.string()),
            pa.field("start", pa.float64()),
            pa.field("end", pa.float64()),
            pa.field("overlap_seconds", pa.float64()),
            pa.field("overlap_ratio", pa.float64()),
            pa.field("operation", pa.string()),
        ]
    )
    return pa.schema(
        [
            pa.field("id", pa.string()),
            pa.field("speaker_id", pa.string()),
            pa.field("split", pa.string()),
            pa.field("audio_path", pa.string()),
            pa.field("pcm_sha256", pa.string()),
            pa.field("prompt_sha256", pa.string()),
            pa.field("qc_flags", pa.list_(pa.string())),
            pa.field("word_intervals", pa.list_(interval)),
            pa.field("target_phone_intervals", pa.list_(interval)),
            pa.field("phone_alignments", pa.list_(alignment)),
        ]
    )


def _intervals() -> list[dict[str, Any]]:
    return [
        {"start": 0.0, "end": 0.1, "label": " a "},
        {"start": 0.1, "end": 0.2, "label": "b"},
    ]


def _alignments() -> list[dict[str, Any]]:
    return [
        {
            "target_label": "a",
            "realized_label": "a",
            "start": 0.0,
            "end": 0.1,
            "overlap_seconds": 0.1,
            "overlap_ratio": 1.0,
            "operation": "match",
        },
        {
            "target_label": "b",
            "realized_label": "c",
            "start": 0.1,
            "end": 0.2,
            "overlap_seconds": 0.1,
            "overlap_ratio": 1.0,
            "operation": "substitution",
        },
    ]


def _write_stack(path: Path) -> tuple[str, str]:
    reserved = "9" * 64
    eligible_prompt = "8" * 64
    base = {
        "speaker_id": "1" * 64,
        "audio_path": "audio/fake.wav",
        "pcm_sha256": "2" * 64,
        "qc_flags": [],
        "word_intervals": [],
        "target_phone_intervals": _intervals(),
        "phone_alignments": _alignments(),
    }
    rows = [
        {
            **base,
            "id": "raw-sensitive-source-id",
            "split": "stack",
            "prompt_sha256": eligible_prompt,
        },
        {
            **base,
            "id": "reserved",
            "split": "stack",
            "prompt_sha256": reserved,
        },
        {
            **base,
            "id": "qc",
            "split": "stack",
            "prompt_sha256": "7" * 64,
            "qc_flags": ["invalid"],
        },
        {
            **base,
            "id": "train-must-be-filtered",
            "split": "train",
            "prompt_sha256": "6" * 64,
            "speaker_id": "not-a-hash",
        },
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.Table.from_pylist(rows, schema=_stack_schema()),
        path,
        compression="zstd",
        use_dictionary=False,
        write_statistics=True,
    )
    return reserved, eligible_prompt


def _checkpoint(value: str) -> dict[str, Any]:
    return {
        "path": f"artifacts/fake/{value[:8]}.bin",
        "sha256": value,
        "size_bytes": 1,
    }


def _lock(stack_path: Path, workspace: Path) -> dict[str, Any]:
    frozen: dict[str, Any] = {
        "order": list(projection.COMPONENTS),
        "explicit": {
            "sources": {"registry": _checkpoint("1" * 64)},
            "projection_checkpoints": [
                {"seed": seed, "checkpoint": _checkpoint(f"{index + 2:x}" * 64)}
                for index, seed in enumerate(projection.SEEDS)
            ],
        },
        "s104_adapter": {
            "source_commit_sha256": "5" * 64,
            "projection_checkpoints": [
                {"seed": seed, "checkpoint": _checkpoint(f"{index + 6:x}" * 64)}
                for index, seed in enumerate(projection.SEEDS)
            ],
        },
        "s105_selected_hierarchy": {
            "source_commit_sha256": "9" * 64,
            "selected_variant": "simple_head_control",
            "projection_checkpoints": [
                {"seed": seed, "checkpoint": _checkpoint(f"{index + 10:x}" * 64)}
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
        "frozen_components": frozen,
        "stack_source": {
            "path": stack_path.relative_to(workspace).as_posix(),
            "sha256": _sha(stack_path),
            "size_bytes": stack_path.stat().st_size,
            "split_value": "stack",
            "row_data_read_by_preflight": False,
        },
    }


class _FakeBackend:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def project(
        self,
        utterance: generator.LabelFreeUtterance,
    ) -> dict[str, dict[int, list[float]]]:
        self.events.append("project")
        assert utterance.word_intervals == ()
        count = len(utterance.target_intervals)
        return {
            component: {
                seed: [
                    0.1 + component_index * 0.1 + seed_index * 0.01
                    for _ in range(count)
                ]
                for seed_index, seed in enumerate(projection.SEEDS)
            }
            for component_index, component in enumerate(projection.COMPONENTS)
        }

    def close(self) -> None:
        self.events.append("backend_close")


def _bundle_builder(**values: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": projection.COMPONENT_BUNDLE_SCHEMA_VERSION,
        "status": "committed",
        "generator_commit": dict(values["generator_commit_record"]),
        "feature_freeze_receipt": dict(values["feature_freeze_receipt_record"]),
        "producer_identity": dict(values["producer_identity"]),
        "execution_lock": dict(values["execution_lock_record"]),
        "stack_source": dict(values["stack_source_record"]),
        "frozen_components": dict(values["execution_lock"]["frozen_components"]),
        "feature_identity_matrix": dict(values["feature_identity_matrix_record"]),
        "label_matrix": dict(values["label_matrix_record"]),
        "components": dict(values["component_records"]),
        "projection_audit": {
            "component_order": list(projection.COMPONENTS),
            "seed_order": list(projection.SEEDS),
            "prediction_file_count": 12,
            "seed_aggregation_occurs_before_join": True,
            "feature_identity_built_before_label_open": True,
            "label_content_and_computed_hash_deferred_until_feature_freeze": True,
            "feature_builder_received_labels": False,
            "alignment_confidence_source": "absent_from_modeling_manifest",
            "alignment_confidence_non_null_allowed": False,
            "alignment_confidence_missing_indicator_forced": True,
            "b07_dev_predictions_reused": False,
            "realized_phone_identity_used": False,
            "binary_error_label_used_by_projectors": False,
        },
    }
    document["binding_sha256"] = hashlib.sha256(
        generator.canonical_json_bytes(document, pretty=False)
    ).hexdigest()
    return document


def _workspace(tmp_path: Path) -> tuple[Path, Path, dict[str, Any], list[str]]:
    workspace = tmp_path / "workspace"
    main = workspace / "configs/experiments/wavlm_s106_stack_fusion_v1.yaml"
    main.parent.mkdir(parents=True)
    shutil.copyfile(
        ROOT / "configs/experiments/wavlm_s106_stack_fusion_v1.yaml",
        main,
    )
    stack = workspace / "data/synthetic/stack.parquet"
    reserved, _ = _write_stack(stack)
    reservation = workspace / "data/synthetic/reservation.json"
    _write_json(
        reservation,
        {"reserved_prompts": [{"prompt_sha256": reserved}]},
    )
    source_config = yaml.safe_load(
        (ROOT / "configs/experiments/wavlm_s106_component_inputs_v1.yaml").read_text(
            encoding="utf-8"
        )
    )
    source_config["inputs"]["prompt_challenge_reservation"] = reservation.relative_to(
        workspace
    ).as_posix()
    source_config["inputs"]["prompt_challenge_reservation_sha256"] = _sha(reservation)
    config_path = workspace / "configs/experiments/wavlm_s106_component_inputs_v1.yaml"
    config_path.write_text(
        yaml.safe_dump(source_config, sort_keys=False),
        encoding="utf-8",
    )
    execution_lock = _lock(stack, workspace)
    lock_path = (
        workspace
        / "artifacts/experiments/aihub71490_wavlm_s106_stack_fusion_v1"
        / "execution_lock.json"
    )
    _write_json(lock_path, execution_lock)
    events: list[str] = []
    return workspace, config_path, execution_lock, events


def _run(
    workspace: Path,
    config_path: Path,
    execution_lock: dict[str, Any],
    events: list[str],
    *,
    fault: generator.FaultInjector | None = None,
) -> generator.GeneratedComponentInputs:
    lock_path = (
        workspace
        / "artifacts/experiments/aihub71490_wavlm_s106_stack_fusion_v1"
        / "execution_lock.json"
    )

    def preflight(
        root: Path,
        main_config: Path,
    ) -> tuple[dict[str, Any], Path]:
        assert root == workspace
        assert main_config.is_file()
        events.append("preflight")
        return execution_lock, lock_path

    def backend_factory(
        root: Path,
        config: Mapping[str, Any],
        lock: Mapping[str, Any],
        utterances: Sequence[generator.LabelFreeUtterance],
    ) -> _FakeBackend:
        del config, lock
        assert root == workspace
        assert len(utterances) == 1
        events.append("backend_factory")
        return _FakeBackend(events)

    def label_event(value: str) -> None:
        receipt = (
            workspace
            / "artifacts/experiments/aihub71490_wavlm_s106_stack_fusion_v1"
            / "component_feature_freeze_receipt.json"
        )
        assert receipt.is_file()
        events.append(value)

    return generator.run_component_generation_for_test(
        workspace_root=workspace,
        generator_config_path=config_path,
        backend_factory=backend_factory,
        preflight=preflight,
        bundle_builder=_bundle_builder,
        fault_injector=fault,
        label_event_hook=label_event,
    )


def test_config_and_label_free_scan_use_pushdown_and_allow_empty_words(
    tmp_path: Path,
) -> None:
    workspace, config_path, execution_lock, _ = _workspace(tmp_path)
    generator.load_generator_config(config_path)
    stack = workspace / execution_lock["stack_source"]["path"]
    scan = generator.scan_label_free_stack(
        stack_path=stack,
        reserved_prompt_hashes=frozenset({"9" * 64}),
        expected_sha256=_sha(stack),
    )
    assert len(scan.utterances) == 1
    assert scan.utterances[0].word_intervals == ()
    assert tuple(value.label for value in scan.utterances[0].target_intervals) == (
        "a",
        "b",
    )
    assert scan.eligible_source_ids == ("raw-sensitive-source-id",)
    assert scan.excluded_qc_utterances == 1
    assert scan.excluded_reserved_prompt_utterances == 1


def test_label_free_scan_uses_bounded_filtering_scanner_not_full_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _, execution_lock, _ = _workspace(tmp_path)
    original_batches = generator._source_batches  # noqa: SLF001
    observed_batch_sizes: list[int] = []

    def guarded_batches(*args: Any, **kwargs: Any) -> Any:
        for batch in original_batches(*args, **kwargs):
            observed_batch_sizes.append(batch.num_rows)
            assert batch.num_rows <= int(kwargs["batch_size"])
            yield batch

    monkeypatch.setattr(generator, "_source_batches", guarded_batches)
    monkeypatch.setattr(
        generator.pq,
        "read_table",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("full-table Parquet load is forbidden")
        ),
    )
    scan = generator.scan_label_free_stack(
        stack_path=workspace / execution_lock["stack_source"]["path"],
        reserved_prompt_hashes=frozenset({"9" * 64}),
        expected_sha256=execution_lock["stack_source"]["sha256"],
        batch_size=1,
    )
    assert len(scan.utterances) == 1
    assert observed_batch_sizes == [1, 1, 1]


def test_production_runner_has_no_dependency_injection_surface() -> None:
    assert tuple(inspect.signature(generator.run_component_generation).parameters) == (
        "workspace_root",
        "generator_config_path",
    )


def test_production_publication_lock_is_single_writer_and_crash_released(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = workspace / "generator.yaml"
    config_path.write_text("schema_version: 1\n", encoding="utf-8")
    with generator._production_publish_lock(  # noqa: SLF001
        workspace_root=workspace,
        generator_config_path=config_path,
    ) as lock_path:
        assert stat.S_IMODE(os.lstat(lock_path).st_mode) == 0o600
        with pytest.raises(
            generator.S106ComponentGeneratorError,
            match="already active",
        ):
            with generator._production_publish_lock(  # noqa: SLF001
                workspace_root=workspace,
                generator_config_path=config_path,
            ):
                raise AssertionError("concurrent writer entered")
    with generator._production_publish_lock(  # noqa: SLF001
        workspace_root=workspace,
        generator_config_path=config_path,
    ):
        pass


def test_default_production_run_deep_validates_after_visibility_and_cleans_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path, execution_lock, events = _workspace(tmp_path)
    main_path = workspace / "configs/experiments/wavlm_s106_stack_fusion_v1.yaml"
    main = yaml.safe_load(main_path.read_text(encoding="utf-8"))
    main["implementation"]["component_generator_config"] = {
        "path": config_path.relative_to(workspace).as_posix(),
        "sha256": _sha(config_path),
    }
    main_path.write_text(yaml.safe_dump(main, sort_keys=False), encoding="utf-8")
    lock_path = (
        workspace
        / "artifacts/experiments/aihub71490_wavlm_s106_stack_fusion_v1"
        / "execution_lock.json"
    )

    def production_backend(
        root: Path,
        config: Mapping[str, Any],
        lock: Mapping[str, Any],
        utterances: Sequence[generator.LabelFreeUtterance],
    ) -> _FakeBackend:
        del config, lock, utterances
        assert root == workspace
        return _FakeBackend(events)

    def production_preflight(
        root: Path,
        main_config: Path,
    ) -> tuple[Mapping[str, Any], Path]:
        assert root == workspace
        assert main_config == main_path
        return execution_lock, lock_path

    validation_calls: list[bool] = []

    def fail_deep_validation(**values: Any) -> Mapping[str, Any]:
        del values
        outputs = generator.load_generator_config(config_path)["outputs"]
        assert (workspace / outputs["component_bundle_commit"]).is_file()
        assert (workspace / outputs["registry"]).is_file()
        validation_calls.append(True)
        raise generator.S106ComponentGeneratorError("post-publish deep failure")

    monkeypatch.setattr(generator, "production_backend_factory", production_backend)
    monkeypatch.setattr(generator, "_default_preflight", production_preflight)
    monkeypatch.setattr(projection, "build_component_bundle_document", _bundle_builder)
    monkeypatch.setattr(
        generator,
        "_validate_component_generation_commit",
        fail_deep_validation,
    )
    with pytest.raises(
        generator.S106ComponentGeneratorError,
        match="post-publish deep failure",
    ):
        generator.run_component_generation(
            workspace_root=workspace,
            generator_config_path=config_path,
        )
    assert validation_calls == [True]
    outputs = generator.load_generator_config(config_path)["outputs"]
    for name in ("component_bundle_commit", "generator_commit", "registry"):
        assert not (workspace / outputs[name]).exists()


def test_alternate_generator_config_cannot_claim_production_identity(
    tmp_path: Path,
) -> None:
    alternate = tmp_path / "alternate-generator.yaml"
    shutil.copyfile(
        ROOT / "configs/experiments/wavlm_s106_component_inputs_v1.yaml",
        alternate,
    )
    main_config = contract.load_config(
        ROOT / "configs/experiments/wavlm_s106_stack_fusion_v1.yaml"
    )
    with pytest.raises(
        generator.S106ComponentGeneratorError,
        match="canonical pinned source",
    ):
        generator._validate_canonical_generator_config_binding(  # noqa: SLF001
            root=ROOT,
            generator_config_path=alternate,
            main_config=main_config,
        )


def test_generator_config_only_has_zero_runtime_and_output_access() -> None:
    result = generator.validate_static_generator(
        workspace_root=ROOT,
        generator_config_path=(
            ROOT / "configs/experiments/wavlm_s106_component_inputs_v1.yaml"
        ),
    )
    assert result["status"] == "config_valid"
    assert result["predecessor_read"] is False
    assert result["explicit_checkpoint_read"] is False
    assert result["b07_checkpoint_read"] is False
    assert result["stack_path_resolved"] is False
    assert result["stack_rows_read"] == 0
    assert result["raw_audio_read"] is False
    assert result["gpu_initialized"] is False
    assert result["output_written"] is False


def test_synthetic_e2e_freezes_twelve_files_before_label_open(
    tmp_path: Path,
) -> None:
    workspace, config_path, execution_lock, events = _workspace(tmp_path)
    generated = _run(workspace, config_path, execution_lock, events)
    assert (
        generated.bundle["schema_version"] == generator.SYNTHETIC_BUNDLE_SCHEMA_VERSION
    )
    assert (
        generated.commit["schema_version"]
        == generator.SYNTHETIC_GENERATOR_COMMIT_SCHEMA_VERSION
    )
    assert generated.commit["producer_identity"] == {
        "mode": "synthetic_test_only",
        "production_schema_allowed": False,
        "dependency_injection_allowed": True,
    }
    assert generated.commit["feature_rows"] == 2
    assert generated.commit["label_rows"] == 2
    assert generated.commit["excluded_utterances"] == {
        "qc_flagged": 1,
        "reserved_prompt": 1,
    }
    assert events.index("preflight") < events.index("backend_factory")
    assert events.index("backend_factory") < events.index(
        "validate_feature_freeze_receipt"
    )
    assert events.index("validate_feature_freeze_receipt") < events.index(
        "open_phone_alignments"
    )
    receipt = generated.feature_freeze_receipt
    assert generated.generation_id == receipt["feature_digest_sha256"]
    assert receipt["audit"]["all_twelve_predictions_exact_key_order"] is True
    components = receipt["components"]
    assert sum(len(value) for value in components.values()) == 12
    identity = pq.read_table(workspace / receipt["feature_identity_matrix"]["path"])
    identity_keys = list(
        zip(
            identity["utterance_id_sha256"].to_pylist(),
            identity["phone_index"].to_pylist(),
            strict=True,
        )
    )
    for component in projection.COMPONENTS:
        for seed in projection.SEEDS:
            public_path = workspace / components[component][f"seed_{seed}"]["path"]
            generation_path = (
                workspace
                / generator.load_generator_config(config_path)["outputs"][
                    "generation_root"
                ]
                / generated.generation_id
                / "predictions"
                / component
                / f"seed_{seed}_stack_predictions.parquet"
            )
            table = pq.read_table(public_path)
            assert (
                list(
                    zip(
                        table["utterance_id_sha256"].to_pylist(),
                        table["phone_index"].to_pylist(),
                        strict=True,
                    )
                )
                == identity_keys
            )
            assert public_path.read_bytes() == generation_path.read_bytes()
            assert (public_path.stat().st_dev, public_path.stat().st_ino) != (
                generation_path.stat().st_dev,
                generation_path.stat().st_ino,
            )
    bundle_path = (
        workspace
        / "artifacts/experiments/aihub71490_wavlm_s106_stack_fusion_v1"
        / "component_bundle_commit.json"
    )
    registry_path = (
        workspace
        / "artifacts/experiment_registry"
        / "aihub71490-wavlm-s106-component-inputs-v1"
        / "metadata.json"
    )
    assert registry_path.stat().st_mtime_ns <= bundle_path.stat().st_mtime_ns
    for artifact in (workspace / "artifacts").rglob("*"):
        if artifact.is_file():
            assert b"raw-sensitive-source-id" not in artifact.read_bytes()


def test_generator_streams_label_rows_into_bounded_sink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path, execution_lock, events = _workspace(tmp_path)
    original = generator.materialize_label_rows
    observed: list[bool] = []

    def guarded(**values: Any) -> tuple[Mapping[str, Any], ...]:
        observed.append(callable(values.get("row_consumer")))
        return original(**values)

    monkeypatch.setattr(generator, "materialize_label_rows", guarded)
    generated = _run(workspace, config_path, execution_lock, events)
    assert generated.commit["label_rows"] == 2
    assert observed == [True]


def test_feature_freeze_receipt_rejects_extra_self_bound_claim(
    tmp_path: Path,
) -> None:
    workspace, config_path, execution_lock, events = _workspace(tmp_path)
    generated = _run(workspace, config_path, execution_lock, events)
    receipt = dict(generated.feature_freeze_receipt)
    receipt["extra_claim"] = True
    receipt["binding_sha256"] = generator._binding(receipt)  # noqa: SLF001
    with pytest.raises(
        generator.S106ComponentGeneratorError,
        match="feature-freeze receipt differs",
    ):
        generator.validate_feature_freeze_receipt(receipt)


def test_barrier_rejects_prediction_tamper_before_alignment_open(
    tmp_path: Path,
) -> None:
    workspace, config_path, execution_lock, events = _workspace(tmp_path)
    generated = _run(workspace, config_path, execution_lock, events)
    record = generated.feature_freeze_receipt["components"]["explicit"]["seed_42"]
    prediction = workspace / record["path"]
    prediction.chmod(0o640)
    prediction.write_bytes(prediction.read_bytes() + b"tamper")
    scan = generator.scan_label_free_stack(
        stack_path=workspace / execution_lock["stack_source"]["path"],
        reserved_prompt_hashes=frozenset({"9" * 64}),
        expected_sha256=execution_lock["stack_source"]["sha256"],
    )
    label_events: list[str] = []
    receipt_path = (
        workspace
        / "artifacts/experiments/aihub71490_wavlm_s106_stack_fusion_v1"
        / "component_feature_freeze_receipt.json"
    )
    with pytest.raises(
        generator.S106ComponentGeneratorError,
        match="copy binding|stable-byte binding",
    ):
        generator.materialize_label_rows(
            workspace_root=workspace,
            stack_path=workspace / execution_lock["stack_source"]["path"],
            receipt_path=receipt_path,
            expected_receipt_sha256=_sha(receipt_path),
            expected_utterances=scan.utterances,
            eligible_source_ids=scan.eligible_source_ids,
            event_hook=label_events.append,
        )
    assert "open_phone_alignments" not in label_events


def test_barrier_rejects_receipt_replacement_before_alignment_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path, execution_lock, events = _workspace(tmp_path)
    _run(workspace, config_path, execution_lock, events)
    original_mkdtemp = generator.tempfile.mkdtemp  # noqa: SLF001

    def no_restricted_parquet_copy(*args: Any, **kwargs: Any) -> str:
        if str(kwargs.get("prefix", "")).startswith("s106-private-parquet-"):
            raise AssertionError("restricted Parquet must never be copied to disk")
        return original_mkdtemp(*args, **kwargs)

    monkeypatch.setattr(generator.tempfile, "mkdtemp", no_restricted_parquet_copy)
    stack_path = workspace / execution_lock["stack_source"]["path"]
    scan = generator.scan_label_free_stack(
        stack_path=stack_path,
        reserved_prompt_hashes=frozenset({"9" * 64}),
        expected_sha256=execution_lock["stack_source"]["sha256"],
    )
    receipt_path = (
        workspace
        / "artifacts/experiments/aihub71490_wavlm_s106_stack_fusion_v1"
        / "component_feature_freeze_receipt.json"
    )
    expected_receipt_sha256 = _sha(receipt_path)
    receipt_path.chmod(0o640)
    receipt_path.write_bytes(receipt_path.read_bytes() + b"\n")
    label_events: list[str] = []
    with pytest.raises(
        generator.S106ComponentGeneratorError,
        match="changed before label open",
    ):
        generator.materialize_label_rows(
            workspace_root=workspace,
            stack_path=stack_path,
            receipt_path=receipt_path,
            expected_receipt_sha256=expected_receipt_sha256,
            expected_utterances=scan.utterances,
            eligible_source_ids=scan.eligible_source_ids,
            event_hook=label_events.append,
        )
    assert "open_phone_alignments" not in label_events


def test_label_scanner_pushes_exact_eligible_raw_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path, execution_lock, events = _workspace(tmp_path)
    _run(workspace, config_path, execution_lock, events)
    original_mkdtemp = generator.tempfile.mkdtemp  # noqa: SLF001

    def no_restricted_parquet_copy(*args: Any, **kwargs: Any) -> str:
        if str(kwargs.get("prefix", "")).startswith("s106-private-parquet-"):
            raise AssertionError("restricted Parquet must never be copied to disk")
        return original_mkdtemp(*args, **kwargs)

    monkeypatch.setattr(generator.tempfile, "mkdtemp", no_restricted_parquet_copy)
    stack_path = workspace / execution_lock["stack_source"]["path"]
    scan = generator.scan_label_free_stack(
        stack_path=stack_path,
        reserved_prompt_hashes=frozenset({"9" * 64}),
        expected_sha256=execution_lock["stack_source"]["sha256"],
    )
    original = generator._source_batches  # noqa: SLF001
    observed: list[tuple[str, ...]] = []

    def guarded_batches(
        descriptor: int,
        *,
        columns: tuple[str, ...],
        batch_size: int,
        eligible_source_ids: tuple[str, ...] | None = None,
    ) -> Any:
        held = os.fstat(descriptor)
        source = os.stat(stack_path, follow_symlinks=False)
        assert (held.st_dev, held.st_ino) == (source.st_dev, source.st_ino)
        batches = tuple(
            original(
                descriptor,
                columns=columns,
                batch_size=batch_size,
                eligible_source_ids=eligible_source_ids,
            )
        )
        if columns == generator.LABEL_MATERIALIZER_COLUMNS:
            assert eligible_source_ids is not None
            observed.append(eligible_source_ids)
            assert [row["id"] for batch in batches for row in batch.to_pylist()] == [
                "raw-sensitive-source-id"
            ]
        yield from batches

    monkeypatch.setattr(generator, "_source_batches", guarded_batches)
    receipt_path = (
        workspace
        / "artifacts/experiments/aihub71490_wavlm_s106_stack_fusion_v1"
        / "component_feature_freeze_receipt.json"
    )
    rows = generator.materialize_label_rows(
        workspace_root=workspace,
        stack_path=stack_path,
        receipt_path=receipt_path,
        expected_receipt_sha256=_sha(receipt_path),
        expected_utterances=scan.utterances,
        eligible_source_ids=scan.eligible_source_ids,
    )
    assert len(rows) == 2
    assert observed == [("raw-sensitive-source-id",)]


def test_held_stack_descriptor_rejects_original_path_swap_without_disk_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _, execution_lock, _ = _workspace(tmp_path)
    stack_path = workspace / execution_lock["stack_source"]["path"]
    replacement = stack_path.with_name("replacement.parquet")
    shutil.copyfile(stack_path, replacement)
    original = generator._source_batches  # noqa: SLF001
    swapped = False

    def swapping_batches(descriptor: int, **values: Any) -> Any:
        nonlocal swapped
        held = os.fstat(descriptor)
        source = os.stat(stack_path, follow_symlinks=False)
        assert (held.st_dev, held.st_ino) == (source.st_dev, source.st_ino)
        if not swapped:
            swapped = True
            replacement.replace(stack_path)
        yield from original(descriptor, **values)

    monkeypatch.setattr(generator, "_source_batches", swapping_batches)
    with pytest.raises(
        generator.S106ComponentGeneratorError,
        match="changed during safe scan",
    ):
        generator.scan_label_free_stack(
            stack_path=stack_path,
            reserved_prompt_hashes=frozenset({"9" * 64}),
            expected_sha256=execution_lock["stack_source"]["sha256"],
        )
    assert swapped is True


def test_missing_predecessor_fails_before_runtime_and_output(
    tmp_path: Path,
) -> None:
    workspace, config_path, _, events = _workspace(tmp_path)

    def blocked(root: Path, main: Path) -> tuple[dict[str, Any], Path]:
        del root, main
        events.append("blocked_preflight")
        raise contract.S106ContractError("S104 commit is absent")

    def forbidden_backend(*args: Any) -> _FakeBackend:
        raise AssertionError("backend must not be initialized")

    with pytest.raises(contract.S106ContractError, match="S104 commit"):
        generator.run_component_generation_for_test(
            workspace_root=workspace,
            generator_config_path=config_path,
            backend_factory=forbidden_backend,
            preflight=blocked,
            bundle_builder=_bundle_builder,
        )
    assert events == ["blocked_preflight"]
    assert not (
        workspace
        / "artifacts/experiments/aihub71490_wavlm_s106_stack_fusion_v1"
        / "component_bundle_commit.json"
    ).exists()
    assert not (
        workspace
        / "artifacts/experiment_registry"
        / "aihub71490-wavlm-s106-component-inputs-v1"
    ).exists()


def test_fault_before_visibility_bundle_cleans_public_views(
    tmp_path: Path,
) -> None:
    workspace, config_path, execution_lock, events = _workspace(tmp_path)

    def fault(stage: str) -> None:
        if stage == "after_registry_before_bundle":
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        _run(
            workspace,
            config_path,
            execution_lock,
            events,
            fault=fault,
        )
    base = workspace / "artifacts/experiments/aihub71490_wavlm_s106_stack_fusion_v1"
    assert not (base / "component_bundle_commit.json").exists()
    assert not (base / "component_input_commit.json").exists()
    assert not (
        workspace
        / "artifacts/experiment_registry"
        / "aihub71490-wavlm-s106-component-inputs-v1"
        / "metadata.json"
    ).exists()


@pytest.mark.parametrize(
    "stage",
    ["after_generation_publish_and_fsync", "after_bundle_visibility_commit"],
)
def test_fault_at_durable_publication_boundaries_cleans_public_views(
    tmp_path: Path,
    stage: str,
) -> None:
    workspace, config_path, execution_lock, events = _workspace(tmp_path)

    def fault(observed: str) -> None:
        if observed == stage:
            raise RuntimeError("durability-boundary fault")

    with pytest.raises(RuntimeError, match="durability-boundary"):
        _run(
            workspace,
            config_path,
            execution_lock,
            events,
            fault=fault,
        )
    outputs = generator.load_generator_config(config_path)["outputs"]
    for name in (
        "component_bundle_commit",
        "generator_commit",
        "registry",
        "feature_identity_matrix",
        "label_matrix",
        "feature_freeze_receipt",
    ):
        assert not (workspace / outputs[name]).exists()


def test_label_temporary_and_backend_are_cleaned_on_publication_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path, execution_lock, events = _workspace(tmp_path)
    outputs = generator.load_generator_config(config_path)["outputs"]
    label_path = workspace / outputs["label_matrix"]
    original = generator._materialize_descriptor  # noqa: SLF001

    def fail_label(source_descriptor: int, destination: Path) -> bool:
        if destination == label_path:
            raise RuntimeError("label publication failed")
        return original(source_descriptor, destination)

    monkeypatch.setattr(generator, "_materialize_descriptor", fail_label)
    with pytest.raises(RuntimeError, match="label publication"):
        _run(workspace, config_path, execution_lock, events)
    staging_root = workspace / outputs["staging_root"]
    assert list(staging_root.glob(".labels-*.parquet")) == []
    assert "backend_close" in events


def test_backend_is_closed_when_post_probe_fault_fires(tmp_path: Path) -> None:
    workspace, config_path, execution_lock, events = _workspace(tmp_path)

    def fault(stage: str) -> None:
        if stage == "after_checkpoint_probes":
            raise RuntimeError("post-probe fault")

    with pytest.raises(RuntimeError, match="post-probe"):
        _run(
            workspace,
            config_path,
            execution_lock,
            events,
            fault=fault,
        )
    assert events[-1] == "backend_close"


def test_parent_directories_are_fsynced_across_visibility_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path, execution_lock, events = _workspace(tmp_path)
    observed: list[Path] = []
    monkeypatch.setattr(
        generator,
        "_fsync_directory",
        lambda path: observed.append(path),
    )
    _run(workspace, config_path, execution_lock, events)
    outputs = generator.load_generator_config(config_path)["outputs"]
    required = {
        (workspace / outputs["generation_root"]),
        (workspace / outputs["feature_identity_matrix"]).parent,
        (workspace / outputs["feature_freeze_receipt"]).parent,
        (workspace / outputs["generator_commit"]).parent,
        (workspace / outputs["registry"]).parent,
        (workspace / outputs["component_bundle_commit"]).parent,
    }
    assert required <= set(observed)


def test_content_addressed_collision_is_compared_not_blindly_reused(
    tmp_path: Path,
) -> None:
    workspace, config_path, execution_lock, events = _workspace(tmp_path)
    generated = _run(workspace, config_path, execution_lock, events)
    outputs = generator.load_generator_config(config_path)["outputs"]
    for name in (
        "component_bundle_commit",
        "generator_commit",
        "registry",
        "feature_identity_matrix",
        "label_matrix",
        "feature_freeze_receipt",
    ):
        (workspace / outputs[name]).unlink()
    for component in projection.COMPONENTS:
        for seed in projection.SEEDS:
            (
                workspace
                / projection.COMPONENT_PREDICTION_ROOT
                / component
                / f"seed_{seed}_stack_predictions.parquet"
            ).unlink()
    generation_path = (
        workspace
        / outputs["generation_root"]
        / generated.generation_id
        / "predictions/explicit/seed_42_stack_predictions.parquet"
    )
    generation_path.chmod(0o640)
    generation_path.write_bytes(generation_path.read_bytes() + b"collision")
    with pytest.raises(
        generator.S106ComponentGeneratorError,
        match="generation collision",
    ):
        _run(workspace, config_path, execution_lock, [])


def test_generation_collision_rejects_extra_empty_directory_before_reuse(
    tmp_path: Path,
) -> None:
    workspace, config_path, execution_lock, events = _workspace(tmp_path)
    generated = _run(workspace, config_path, execution_lock, events)
    outputs = generator.load_generator_config(config_path)["outputs"]
    for name in (
        "component_bundle_commit",
        "generator_commit",
        "registry",
        "feature_identity_matrix",
        "label_matrix",
        "feature_freeze_receipt",
    ):
        (workspace / outputs[name]).unlink()
    for component in projection.COMPONENTS:
        for seed in projection.SEEDS:
            (
                workspace
                / projection.COMPONENT_PREDICTION_ROOT
                / component
                / f"seed_{seed}_stack_predictions.parquet"
            ).unlink()
    extra = (
        workspace / outputs["generation_root"] / generated.generation_id / "unexpected"
    )
    extra.parent.chmod(0o750)
    extra.mkdir()
    extra.chmod(0o550)
    extra.parent.chmod(0o550)
    with pytest.raises(
        generator.S106ComponentGeneratorError,
        match="generation collision",
    ):
        _run(workspace, config_path, execution_lock, [])


def test_generation_collision_recovers_exact_unsealed_post_rename_tree(
    tmp_path: Path,
) -> None:
    workspace, config_path, execution_lock, events = _workspace(tmp_path)
    generated = _run(workspace, config_path, execution_lock, events)
    outputs = generator.load_generator_config(config_path)["outputs"]
    for name in (
        "component_bundle_commit",
        "generator_commit",
        "registry",
        "feature_identity_matrix",
        "label_matrix",
        "feature_freeze_receipt",
    ):
        (workspace / outputs[name]).unlink()
    for component in projection.COMPONENTS:
        for seed in projection.SEEDS:
            (
                workspace
                / projection.COMPONENT_PREDICTION_ROOT
                / component
                / f"seed_{seed}_stack_predictions.parquet"
            ).unlink()
    generation_path = workspace / outputs["generation_root"] / generated.generation_id
    generation_path.chmod(0o700)
    unsealed_file = generation_path / "feature_identity.parquet"
    unsealed_file.chmod(0o600)
    recovered = _run(workspace, config_path, execution_lock, [])
    assert recovered.generation_id == generated.generation_id
    assert stat.S_IMODE(os.lstat(generation_path).st_mode) == 0o550
    assert stat.S_IMODE(os.lstat(unsealed_file).st_mode) == 0o440


def test_existing_generation_symlink_is_rejected_without_following(
    tmp_path: Path,
) -> None:
    workspace, config_path, execution_lock, events = _workspace(tmp_path)
    generated = _run(workspace, config_path, execution_lock, events)
    outputs = generator.load_generator_config(config_path)["outputs"]
    for name in (
        "component_bundle_commit",
        "generator_commit",
        "registry",
        "feature_identity_matrix",
        "label_matrix",
        "feature_freeze_receipt",
    ):
        (workspace / outputs[name]).unlink()
    for component in projection.COMPONENTS:
        for seed in projection.SEEDS:
            (
                workspace
                / projection.COMPONENT_PREDICTION_ROOT
                / component
                / f"seed_{seed}_stack_predictions.parquet"
            ).unlink()
    generation_path = workspace / outputs["generation_root"] / generated.generation_id
    moved = generation_path.with_name(f"{generated.generation_id}.external")
    generation_path.rename(moved)
    generation_path.symlink_to(moved, target_is_directory=True)
    with pytest.raises(
        generator.S106ComponentGeneratorError,
        match="no-follow directory",
    ):
        _run(workspace, config_path, execution_lock, events)


def test_generation_and_public_views_require_exact_mode_and_single_link(
    tmp_path: Path,
) -> None:
    workspace, config_path, execution_lock, events = _workspace(tmp_path)
    generated = _run(workspace, config_path, execution_lock, events)
    outputs = generator.load_generator_config(config_path)["outputs"]
    generation_file = (
        workspace
        / outputs["generation_root"]
        / generated.generation_id
        / "predictions/explicit/seed_42_stack_predictions.parquet"
    )
    generation_file.chmod(0o640)
    with pytest.raises(
        generator.S106ComponentGeneratorError,
        match="file seal",
    ):
        generator._validate_test_component_generation_commit(  # noqa: SLF001
            workspace_root=workspace,
            generator_config_path=config_path,
        )
    generation_file.chmod(0o440)
    external_link = tmp_path / "external-generation-link.parquet"
    os.link(generation_file, external_link)
    with pytest.raises(
        generator.S106ComponentGeneratorError,
        match="file seal",
    ):
        generator._validate_test_component_generation_commit(  # noqa: SLF001
            workspace_root=workspace,
            generator_config_path=config_path,
        )


def test_public_json_mode_and_hardlink_alias_are_rejected(tmp_path: Path) -> None:
    workspace, config_path, execution_lock, events = _workspace(tmp_path)
    _run(workspace, config_path, execution_lock, events)
    outputs = generator.load_generator_config(config_path)["outputs"]
    bundle = workspace / outputs["component_bundle_commit"]
    bundle.chmod(0o640)
    with pytest.raises(
        generator.S106ComponentGeneratorError,
        match="sealed single-link",
    ):
        generator._validate_test_component_generation_commit(  # noqa: SLF001
            workspace_root=workspace,
            generator_config_path=config_path,
        )
    bundle.chmod(0o440)
    commit = workspace / outputs["generator_commit"]
    external_link = tmp_path / "external-commit-link.json"
    os.link(commit, external_link)
    with pytest.raises(
        generator.S106ComponentGeneratorError,
        match="sealed single-link",
    ):
        generator._validate_test_component_generation_commit(  # noqa: SLF001
            workspace_root=workspace,
            generator_config_path=config_path,
        )


def test_atomic_json_identical_fast_path_rejects_mode_and_hardlink(
    tmp_path: Path,
) -> None:
    path = tmp_path / "immutable.json"
    document = {"status": "committed"}
    assert generator._atomic_json(path, document) is True  # noqa: SLF001
    path.chmod(0o640)
    with pytest.raises(
        generator.S106ComponentGeneratorError,
        match="sealed single-link",
    ):
        generator._atomic_json(path, document)  # noqa: SLF001
    path.chmod(0o440)
    alias = tmp_path / "alias.json"
    os.link(path, alias)
    with pytest.raises(
        generator.S106ComponentGeneratorError,
        match="sealed single-link",
    ):
        generator._atomic_json(path, document)  # noqa: SLF001


def test_atomic_publication_sets_sealed_mode_before_visibility_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[int] = []
    original_link = generator.os.link

    def inspect_link(source: Path, destination: Path) -> None:
        observed.append(stat.S_IMODE(os.lstat(source).st_mode))
        original_link(source, destination)

    monkeypatch.setattr(generator.os, "link", inspect_link)
    generator._atomic_json(  # noqa: SLF001
        tmp_path / "document.json",
        {"status": "committed"},
    )
    source = tmp_path / "source.bin"
    source.write_bytes(b"immutable bytes")
    generator._materialize_file(source, tmp_path / "published.bin")  # noqa: SLF001
    assert observed == [0o440, 0o440]


def test_deep_commit_validator_replays_exact_twelve_key_orders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path, execution_lock, events = _workspace(tmp_path)
    generated = _run(workspace, config_path, execution_lock, events)
    from voice_coach.training import wavlm_s106_fusion_training as training

    document = generated.bundle
    component_records = {
        component: {
            seed: document["components"][component][f"seed_{seed}"]
            for seed in projection.SEEDS
        }
        for component in projection.COMPONENTS
    }
    evidence = SimpleNamespace(
        document=document,
        frozen_components=document["frozen_components"],
        feature_identity_record=document["feature_identity_matrix"],
        label_record=document["label_matrix"],
        component_records=component_records,
    )
    monkeypatch.setattr(
        training,
        "validate_execution_lock",
        lambda **values: execution_lock,
    )
    monkeypatch.setattr(
        training,
        "validate_component_bundle_commit",
        lambda **values: evidence,
    )
    monkeypatch.setattr(
        training,
        "build_component_matrix",
        lambda **values: SimpleNamespace(
            rows=(object(), object()),
            feature_freeze_sha256="f" * 64,
        ),
    )
    validation = generator._validate_test_component_generation_commit(  # noqa: SLF001
        workspace_root=workspace,
        generator_config_path=config_path,
    )
    assert validation["prediction_file_count"] == 12
    assert validation["all_twelve_predictions_exact_key_order"] is True

    prediction = workspace / document["components"]["s104_adapter"]["seed_43"]["path"]
    prediction.chmod(0o640)
    table = pq.read_table(prediction)
    reversed_table = table.take(pa.array([1, 0], type=pa.int64()))
    pq.write_table(
        reversed_table,
        prediction,
        compression="zstd",
        use_dictionary=False,
        write_statistics=True,
    )
    with pytest.raises(
        generator.S106ComponentGeneratorError,
        match="copy binding|stable-byte binding",
    ):
        generator._validate_test_component_generation_commit(  # noqa: SLF001
            workspace_root=workspace,
            generator_config_path=config_path,
        )


def test_audio_swap_is_rejected_while_same_fd_bytes_are_held(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import struct
    import wave

    from voice_coach.data.wav_qc import pcm_sha256

    def write_wav(path: Path, sample: int) -> None:
        with wave.open(str(path), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(16_000)
            writer.writeframes(struct.pack("<" + "h" * 320, *([sample] * 320)))

    audio = tmp_path / "audio.wav"
    replacement = tmp_path / "replacement.wav"
    write_wav(audio, 100)
    write_wav(replacement, 200)
    expected = pcm_sha256(audio)
    original_stat = generator.os.stat
    swapped = False

    def swapping_stat(path: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal swapped
        if Path(path) == audio and not swapped:
            swapped = True
            replacement.replace(audio)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(generator.os, "stat", swapping_stat)
    with pytest.raises(
        generator.S106ComponentGeneratorError,
        match="changed during stable byte read",
    ):
        generator._read_and_decode_pinned_audio(  # noqa: SLF001
            audio,
            expected_pcm_sha256=expected,
        )
    assert swapped is True


def test_partial_production_backend_construction_closes_loaded_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    torch = pytest.importorskip("torch")
    workspace = tmp_path / "workspace"
    dataset_root = workspace / "data/restricted/aihub/dataset-71490"
    dataset_root.mkdir(parents=True)
    config = yaml.safe_load(
        (ROOT / "configs/experiments/wavlm_s106_component_inputs_v1.yaml").read_text(
            encoding="utf-8"
        )
    )
    closed: list[bool] = []

    def fail_partial(self: generator.ProductionProjectionBackend) -> None:
        self.s104_models[42] = object()
        raise generator.S106ComponentGeneratorError("partial load")

    def close_partial(self: generator.ProductionProjectionBackend) -> None:
        closed.append(bool(self.s104_models))
        self.s104_models.clear()

    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "is_bf16_supported", lambda: True)
    monkeypatch.setattr(torch, "use_deterministic_algorithms", lambda value: None)
    monkeypatch.setattr(
        generator.ProductionProjectionBackend, "_load_all", fail_partial
    )
    monkeypatch.setattr(generator.ProductionProjectionBackend, "close", close_partial)
    monkeypatch.setenv("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    with pytest.raises(generator.S106ComponentGeneratorError, match="partial load"):
        generator.ProductionProjectionBackend(
            workspace_root=workspace,
            generator_config=config,
            execution_lock={},
        )
    assert closed == [True]


@pytest.mark.parametrize("fail_after_seal", [False, True])
def test_private_model_staging_is_removed_after_success_or_failure(
    tmp_path: Path,
    fail_after_seal: bool,
) -> None:
    def exercise() -> None:
        private_root = tmp_path / "s106-private-model-inputs-regression"
        snapshot = private_root / "snapshot"
        snapshot.mkdir(parents=True, mode=0o700)
        (private_root / "s104.yaml").write_text("fixture\n", encoding="utf-8")
        checkpoint = snapshot / "pytorch_model.bin"
        checkpoint.write_bytes(b"checkpoint")
        checkpoint.chmod(0o400)
        snapshot.chmod(0o500)
        try:
            if fail_after_seal:
                raise RuntimeError("partial model load")
        finally:
            generator._remove_private_model_staging(private_root)  # noqa: SLF001

    if fail_after_seal:
        with pytest.raises(RuntimeError, match="partial model load"):
            exercise()
    else:
        exercise()
    assert not list(tmp_path.glob("s106-private-model-inputs-*"))
