from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from voice_coach.visual_feedback.adapters.p8_production_frozen_pipeline import (
    P8ProductionPipelineError,
    ProductionP8ArtifactDescriptors,
    ProductionP8FrozenLockedPipeline,
    require_exact_production_p8_pipeline,
)
from voice_coach.visual_feedback.services.p8_production_one_shot import (
    P8ProductionOneShotFacade,
    P8ProductionRunRequest,
)
from voice_coach.visual_feedback.adapters.p8_seal_store import (
    P8SealStateError,
    P8SealStateStore,
)
import voice_coach.visual_feedback.adapters.p8_production_frozen_pipeline as pipeline_module
import voice_coach.visual_feedback.services.p8_production_one_shot as facade_module


def test_direct_construction_is_forbidden() -> None:
    with pytest.raises(
        P8ProductionPipelineError,
        match="p8_production_pipeline_direct_construction_forbidden",
    ):
        ProductionP8FrozenLockedPipeline(
            object(),
            pins="0" * 64,
            catalog=None,  # type: ignore[arg-type]
            claim_policy=None,  # type: ignore[arg-type]
            qc_decider=None,  # type: ignore[arg-type]
            sync_decider=None,  # type: ignore[arg-type]
        )


@dataclass
class _SimpleHashSelfClaim:
    pipeline_pins_sha256: str = "0" * 64

    def evaluate(self, _item):  # noqa: ANN001
        raise AssertionError

    def replay_claim_policy(self, *, evaluated_case_count: int):  # noqa: ANN201
        raise AssertionError


def test_simple_hash_self_claim_is_not_a_production_pipeline() -> None:
    with pytest.raises(
        P8ProductionPipelineError,
        match="p8_production_concrete_pipeline_required",
    ):
        require_exact_production_p8_pipeline(_SimpleHashSelfClaim())


def test_unissued_exact_instance_is_rejected() -> None:
    forged = object.__new__(ProductionP8FrozenLockedPipeline)
    with pytest.raises(
        P8ProductionPipelineError,
        match="p8_production_concrete_pipeline_required",
    ):
        require_exact_production_p8_pipeline(forged)


def test_descriptor_contract_rejects_stdio_and_boolean() -> None:
    values = {name: 3 for name in ProductionP8ArtifactDescriptors.__dataclass_fields__}
    values["claim_catalog"] = True
    with pytest.raises(P8ProductionPipelineError, match="p8_production_descriptor_invalid"):
        ProductionP8ArtifactDescriptors(**values)


def test_facade_has_no_arbitrary_pipeline_parameter() -> None:
    parameters = P8ProductionOneShotFacade.execute_from_completed_directory.__annotations__
    assert set(parameters) == {"request", "return"}


def test_production_request_uses_restricted_reference_and_companion_seal() -> None:
    fields = set(P8ProductionRunRequest.__dataclass_fields__)
    assert "reference_populations" not in fields
    assert {
        "restricted_reference_directory_descriptor",
        "expected_actual_source_seal_sha256",
        "pending_at_utc",
        "cleanup_recorded_at_utc",
        "reload_verified_at_utc",
        "committed_at_utc",
    } <= fields


def test_missing_companion_seal_fails_before_source_open_or_consumption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_directory = tmp_path / "state"
    state_directory.mkdir(mode=0o700)
    store = P8SealStateStore(state_directory)
    seal = SimpleNamespace(seal_sha256="a" * 64)
    sentinel_pipeline = object()
    monkeypatch.setattr(P8SealStateStore, "load_read_zero_seal", lambda self: seal)
    monkeypatch.setattr(
        P8SealStateStore,
        "load_actual_source_read_zero_seal",
        lambda self: (_ for _ in ()).throw(P8SealStateError("missing")),
    )
    monkeypatch.setattr(
        facade_module, "load_production_p8_frozen_pipeline", lambda **kwargs: sentinel_pipeline
    )
    monkeypatch.setattr(
        facade_module, "require_exact_production_p8_pipeline", lambda value: value
    )
    monkeypatch.setattr(
        pipeline_module, "require_exact_production_p8_pipeline", lambda value: value
    )
    descriptors = ProductionP8ArtifactDescriptors(
        **{name: 3 for name in ProductionP8ArtifactDescriptors.__dataclass_fields__}
    )
    request = P8ProductionRunRequest(
        confirm_irreversible_consumption=True,
        input_directory=tmp_path / "must-not-be-opened",
        identity_key_descriptor=3,
        restricted_reference_directory_descriptor=4,
        artifact_descriptors=descriptors,
        output_directory=tmp_path / "output",
        expected_seal_sha256="a" * 64,
        expected_actual_source_seal_sha256="b" * 64,
        transaction_id="transaction_001",
        consumed_at_utc="2026-08-28T00:00:00Z",
        report_id="report_001",
        pending_at_utc="2026-08-28T00:00:01Z",
        cleanup_recorded_at_utc="2026-08-28T00:00:02Z",
        reload_verified_at_utc="2026-08-28T00:00:03Z",
        committed_at_utc="2026-08-28T00:00:04Z",
    )
    with pytest.raises(P8SealStateError, match="missing"):
        P8ProductionOneShotFacade(state_store=store).execute_from_completed_directory(
            request
        )
    assert not request.input_directory.exists()
    assert not (state_directory / "p8_access_transaction.json").exists()


@pytest.mark.parametrize(
    "pin_name",
    (
        "evaluation_pipeline_code_sha256",
        "source_evaluator_code_sha256",
        "media_engine_code_sha256",
    ),
)
def test_installed_production_code_pin_tamper_fails_closed(
    pin_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_paths = dict(pipeline_module._PRODUCTION_CODE_PATHS)
    seal_values = {
        name: pipeline_module._sha256_path(path)
        for name, path in original_paths.items()
    }
    tampered = tmp_path / "tampered.py"
    tampered.write_bytes(original_paths[pin_name].read_bytes() + b"\n# tampered\n")
    changed_paths = dict(original_paths)
    changed_paths[pin_name] = tampered
    monkeypatch.setattr(pipeline_module, "_PRODUCTION_CODE_PATHS", changed_paths)

    with pytest.raises(
        P8ProductionPipelineError,
        match=f"p8_production_{pin_name.removesuffix('_sha256')}_pin_mismatch",
    ):
        pipeline_module._validate_installed_production_code_pins(
            SimpleNamespace(**seal_values)
        )


def test_installed_production_code_symlink_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_paths = dict(pipeline_module._PRODUCTION_CODE_PATHS)
    seal_values = {
        name: pipeline_module._sha256_path(path)
        for name, path in original_paths.items()
    }
    symlink = tmp_path / "evaluation-pipeline.py"
    symlink.symlink_to(original_paths["evaluation_pipeline_code_sha256"])
    changed_paths = dict(original_paths)
    changed_paths["evaluation_pipeline_code_sha256"] = symlink
    monkeypatch.setattr(pipeline_module, "_PRODUCTION_CODE_PATHS", changed_paths)

    with pytest.raises(
        P8ProductionPipelineError,
        match="p8_production_installed_code_unavailable",
    ):
        pipeline_module._validate_installed_production_code_pins(
            SimpleNamespace(**seal_values)
        )
