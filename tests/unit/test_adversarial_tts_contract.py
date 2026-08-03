from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable

import pytest

from voice_coach.training.adversarial_tts_contract import (
    AdversarialTTSConfigError,
    load_adversarial_tts_config,
    preflight_adversarial_tts,
    validate_adversarial_tts_config,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/experiments/adversarial_tts_feedback_v1.yaml"


def test_canonical_config_and_manifest_pins_pass_torch_free_preflight() -> None:
    result = preflight_adversarial_tts(
        workspace_root=ROOT,
        config_path=CONFIG,
    )

    assert result["status"] == "validated_prototype_config"
    assert result["production_ready"] is False
    assert result["native_source_ids"] == ["kss"]
    assert result["sampling_strategy"] == "unpaired_conditional"
    assert result["production_s2_remains_independent"] is True
    assert result["torch_imported"] is False
    assert result["audio_opened"] is False
    assert result["model_or_gpu_accessed"] is False


def test_contract_module_does_not_import_torch_in_a_clean_process() -> None:
    source = (
        "import json,sys;"
        "import voice_coach.training.adversarial_tts_contract as contract;"
        "print(json.dumps({'torch': 'torch' in sys.modules,"
        "'schema': contract.SCHEMA_VERSION}))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert result["torch"] is False
    assert result["schema"] == "voice_coach.adversarial_tts.v1"


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (
            lambda config: config["data"]["learner"].update(
                {"allowed_splits": ["train", "locked_test"]}
            ),
            "protected learner splits",
        ),
        (
            lambda config: config["data"]["learner"].update(
                {"treat_all_as_incorrect": True}
            ),
            "learner correctness policy",
        ),
        (
            lambda config: config["data"]["native"].update(
                {"paired_reconstruction_allowed": True}
            ),
            "cross-speaker reconstruction",
        ),
        (
            lambda config: config["model"]["evaluator2_meta_critic"].update(
                {"consumes_evaluator1_outputs": True}
            ),
            "evaluator2 independence",
        ),
        (
            lambda config: config["model"]["evaluator2_meta_critic"].update(
                {"direct_gradient_to_generator": True}
            ),
            "evaluator2 gradient boundary",
        ),
        (
            lambda config: config["safety"].update(
                {"forbid_nationality_as_model_input": False}
            ),
            "forbid_nationality",
        ),
    ],
)
def test_contract_rejects_leakage_and_unsafe_shortcuts(
    mutation: Callable[[dict[str, Any]], None],
    error: str,
) -> None:
    config = deepcopy(load_adversarial_tts_config(CONFIG))
    mutation(config)
    with pytest.raises(AdversarialTTSConfigError, match=error):
        validate_adversarial_tts_config(config)


def test_yaml_loader_rejects_duplicate_keys(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text(
        "schema_version: voice_coach.adversarial_tts.v1\nschema_version: duplicate\n",
        encoding="utf-8",
    )
    with pytest.raises(AdversarialTTSConfigError, match="duplicate"):
        load_adversarial_tts_config(duplicate)
