from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

from voice_coach.training.b07_gop_contract import (
    B07ContractError,
    load_config,
    validate_config,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONFIG_RELATIVE = Path("configs/experiments/b07_gop_acoustic_posterior_v1.yaml")


def _config() -> dict[str, Any]:
    return load_config(REPOSITORY_ROOT / CONFIG_RELATIVE)


def _static_config() -> dict[str, Any]:
    config = _config()
    config["experiment"]["status"] = "production_ready_not_executed"
    config["experiment"]["canonical_execution_allowed_by_preflight"] = False
    config["execution"]["canonical_run_permitted_in_this_change"] = False
    return config


def _copy_static_workspace(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    config = _static_config()
    relatives = [CONFIG_RELATIVE]
    relatives.extend(
        Path(record["path"]) for record in config["canonical_sources"].values()
    )
    for relative in relatives:
        destination = workspace / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPOSITORY_ROOT / relative, destination)
    config_path = workspace / CONFIG_RELATIVE
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return workspace, config_path


def test_static_and_coherent_approved_permission_modes() -> None:
    static = _static_config()
    validate_config(static)

    approved = deepcopy(static)
    approved["experiment"]["status"] = "approved_for_canonical_execution"
    approved["experiment"]["canonical_execution_allowed_by_preflight"] = True
    approved["execution"]["canonical_run_permitted_in_this_change"] = True
    validate_config(approved)

    mixed = deepcopy(static)
    mixed["experiment"]["canonical_execution_allowed_by_preflight"] = True
    with pytest.raises(B07ContractError, match="mixed canonical"):
        validate_config(mixed)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda config: config["posterior"].update({"prior": "empirical"}),
            "prior",
        ),
        (
            lambda config: config["detector"]["selection"].update(
                {"candidate_probability_aggregation": "fold_mean"}
            ),
            "candidate probability aggregation",
        ),
        (
            lambda config: config["detector"]["selection"][
                "nested_acoustic_feature_rule"
            ].update(
                {"outer_validation_rows": ("prototype_fit_on_all_primary_match_train")}
            ),
            "nested acoustic feature rule",
        ),
        (
            lambda config: config["resource_contract"].update(
                {"deterministic_cpu_thread_cap": 8}
            ),
            "CPU thread cap",
        ),
        (
            lambda config: config["resource_contract"].update(
                {"canonical_peak_rss_gate_gib": 16.0}
            ),
            "peak RSS gate",
        ),
        (
            lambda config: config["data"]["wavlm_phone_cache"].update(
                {"report_sha256": "0" * 64}
            ),
            "wavlm report SHA",
        ),
        (
            lambda config: config["data"]["forbidden_columns"].remove(
                "realized_phone_identity"
            ),
            "forbidden columns",
        ),
        (
            lambda config: config["artifacts"]["exact_generation_layout"].pop(),
            "artifact layout",
        ),
        (
            lambda config: config.update({"unexpected": True}),
            "config keys differ",
        ),
    ],
)
def test_mutation_canaries_fail_closed(
    mutator: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    config = deepcopy(_config())
    mutator(config)
    with pytest.raises(B07ContractError, match=message):
        validate_config(config)


def test_config_only_reads_no_protected_input_and_writes_nothing(
    tmp_path: Path,
) -> None:
    workspace, config_path = _copy_static_workspace(tmp_path)
    environment = {
        **os.environ,
        "CUDA_VISIBLE_DEVICES": "",
    }
    result = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "scripts/prepare_b07_gop_acoustic_posterior.py"),
            "--workspace-root",
            str(workspace),
            "--config",
            str(config_path),
            "--config-only",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "config_valid"
    assert payload["source_count"] == 10
    for key, value in payload.items():
        if key.endswith("_read") or key.endswith("_written"):
            assert value is False
    assert not (workspace / "data").exists()
    assert not (workspace / "artifacts").exists()


def test_static_config_blocks_canonical_runner_before_registry(
    tmp_path: Path,
) -> None:
    workspace, config_path = _copy_static_workspace(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "scripts/train_b07_gop_acoustic_posterior.py"),
            "--workspace-root",
            str(workspace),
            "--config",
            str(config_path),
            "--execute-canonical",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "CUDA_VISIBLE_DEVICES": ""},
    )
    assert result.returncode == 1
    assert "reviewed config permission transition" in result.stderr
    assert not (workspace / "data").exists()
    assert not (workspace / "artifacts").exists()
