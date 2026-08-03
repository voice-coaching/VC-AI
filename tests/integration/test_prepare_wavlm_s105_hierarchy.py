from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml  # type: ignore[import-untyped]

from voice_coach.training import wavlm_hierarchy_contract as contract


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/experiments/wavlm_s105_hierarchy_v1.yaml"
SCRIPT = ROOT / "scripts/prepare_wavlm_s105_hierarchy.py"
TRAIN_SCRIPT = ROOT / "scripts/train_wavlm_s105_hierarchy.py"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load() -> dict[str, object]:
    value = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _pin_current_implementation(config: dict[str, object], root: Path) -> None:
    implementation = config["implementation"]
    assert isinstance(implementation, dict)
    for value in implementation.values():
        assert isinstance(value, dict)
        path = root / str(value["path"])
        value["sha256"] = _sha(path)


def _absent_predecessor_fixture(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = _load()
    implementation = config["implementation"]
    assert isinstance(implementation, dict)
    for value in implementation.values():
        assert isinstance(value, dict)
        source = ROOT / str(value["path"])
        target = workspace / str(value["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        value["sha256"] = _sha(target)
    predecessor = config["predecessor"]
    assert isinstance(predecessor, dict)
    source_s104 = ROOT / str(predecessor["config"])
    target_s104 = workspace / str(predecessor["config"])
    target_s104.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_s104, target_s104)
    predecessor["config_sha256"] = _sha(target_s104)
    predecessor["commit_manifest"] = "artifacts/absent-s104/commit.json"
    outputs = config["outputs"]
    assert isinstance(outputs, dict)
    outputs["execution_lock"] = "artifacts/s105/execution_lock.json"
    outputs["generation_root"] = "artifacts/s105/generations"
    outputs["commit_manifest"] = "artifacts/s105/commit.json"
    outputs["registry_root"] = "artifacts/registry"
    config_path = workspace / "configs/s105.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )
    return workspace, config_path


def test_repository_config_and_config_only_cli_are_read_only() -> None:
    config = contract.load_config(CONFIG)
    contract.validate_config(config)
    implementation, _ = contract.validate_implementation_sources(
        workspace_root=ROOT,
        config=config,
    )
    assert set(implementation) == set(contract.IMPLEMENTATION_ROLES)

    before = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "artifacts/experiments").rglob("*")
        if path.is_file()
    }
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config",
            str(CONFIG),
            "--workspace-root",
            str(ROOT),
            "--config-only",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    after = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "artifacts/experiments").rglob("*")
        if path.is_file()
    }
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "config_valid"
    assert payload["s104_read"] is False
    assert payload["s104_dev_result_read"] is False
    assert payload["data_read"] is False
    assert payload["raw_audio_opened"] is False
    assert payload["cuda_initialized"] is False
    assert payload["output_written"] is False
    assert before == after


def test_full_preflight_fails_before_data_when_s104_commit_is_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path = _absent_predecessor_fixture(tmp_path)

    def forbidden_data(**_: object) -> contract.HierarchyDataAudit:
        raise AssertionError("data audit ran before S104 became valid")

    monkeypatch.setattr(contract, "validate_hierarchy_data", forbidden_data)
    with pytest.raises(contract.S105ContractError, match="completed S104 commit"):
        contract.prepare_s105_execution(
            workspace_root=workspace,
            config_path=config_path,
        )
    assert not (workspace / "artifacts/s105").exists()
    assert not (workspace / "artifacts/registry").exists()


def test_default_trainer_cli_fails_closed_and_writes_no_result(
    tmp_path: Path,
) -> None:
    workspace, config_path = _absent_predecessor_fixture(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(workspace / "scripts/train_wavlm_s105_hierarchy.py"),
            "--config",
            str(config_path),
            "--workspace-root",
            str(workspace),
        ],
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(workspace / "src")},
    )
    assert result.returncode == 1
    assert "S105 execution failed:" in result.stderr
    assert not (workspace / "artifacts/s105").exists()
    assert not (workspace / "artifacts/registry").exists()


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("data", "allowed_splits"), ["train", "dev", "stack"]),
        (("data", "word_supervision", "word_loss_allowed"), True),
        (("optimization", "word_loss_weight"), 1.0),
        (
            ("evaluation", "primary_utterance_outcome"),
            "pronunciation_score",
        ),
        (("evaluation", "dev_inference_passes_per_variant_task_seed"), 2),
        (("adoption", "minimum_absolute_improvement"), 0.02),
        (("adoption", "bootstrap", "n_resamples"), 1000),
        (("adoption", "multiplicity", "primary_method"), "none"),
        (("adoption", "subgroup", "maximum_exclusive"), 0.10),
        (("adoption", "adoption_gate", "require_holm_significance"), True),
        (
            ("variants", "layerwise_weighted_hierarchy", "hidden_source"),
            "final_layer",
        ),
        (("runtime", "pooled_final_layer_cache_allowed"), True),
    ],
)
def test_static_contract_rejects_posthoc_or_leaky_changes(
    path: tuple[str, ...],
    value: object,
) -> None:
    config = _load()
    current: object = config
    for component in path[:-1]:
        assert isinstance(current, dict)
        current = current[component]
    assert isinstance(current, dict)
    current[path[-1]] = value
    _pin_current_implementation(config, ROOT)

    with pytest.raises(contract.S105ContractError):
        contract.validate_config(config)


def test_implementation_tamper_fails_without_reading_s104_or_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _load()
    _pin_current_implementation(config, ROOT)
    tampered = copy.deepcopy(config)
    implementation = tampered["implementation"]
    assert isinstance(implementation, dict)
    model = implementation["model_module"]
    assert isinstance(model, dict)
    model["sha256"] = "0" * 64

    def forbidden(**_: object) -> object:
        raise AssertionError("S104 or data was read after source-pin failure")

    monkeypatch.setattr(contract, "validate_s104_predecessor", forbidden)
    monkeypatch.setattr(contract, "validate_hierarchy_data", forbidden)
    contract.validate_config(tampered)
    with pytest.raises(contract.S105ContractError, match="model_module.sha256"):
        contract.validate_implementation_sources(
            workspace_root=ROOT,
            config=tampered,
        )


def test_config_module_does_not_import_torch() -> None:
    command = (
        "import sys; "
        "import voice_coach.training.wavlm_hierarchy_contract; "
        "raise SystemExit(1 if 'torch' in sys.modules else 0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", command],
        cwd=ROOT,
        check=False,
        env={"PYTHONPATH": str(ROOT / "src")},
    )
    assert result.returncode == 0
