"""Progress logging must be declared, privacy-safe, and outside the commit.

The first S105 attempts ran for over an hour with no observable progress, which
made a working run indistinguishable from a hung one.  Logging fixes that, but it
introduces two risks the tests below close: a log line could leak an identifier or
a restricted path, and progress could drift into the generation tree and make the
82-artifact commit depend on how often a run logged.
"""

from __future__ import annotations

import io
import json
import re
from contextlib import redirect_stdout
from pathlib import Path

import pytest
import yaml

from voice_coach.training.wavlm_hierarchy_contract import (
    PROGRESS_EVENT_SCHEMA_VERSION,
    S105ContractError,
    validate_config,
)
from voice_coach.training.wavlm_hierarchy_execution import (
    ProgressLoggingContract,
    _emit_progress,
)
from voice_coach.training.wavlm_hierarchy_training import S105TrainingError

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = (
    ROOT / "configs/experiments/wavlm_s105_hierarchy_v5_stream_cardinality.yaml"
)
DECLARED_EVENTS = (
    "run_started",
    "seed_started",
    "epoch_started",
    "fit_heartbeat",
    "fit_epoch_finished",
    "epoch_summary",
    "inner_selection_finished",
    "seed_finished",
)


def _config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def test_repository_config_declares_the_logging_contract() -> None:
    declared = _config()["runtime"]["progress_logging"]

    assert declared["destination"] == "stdout_json_lines"
    assert declared["schema_version"] == PROGRESS_EVENT_SCHEMA_VERSION
    assert declared["heartbeat_utterances"] == 2000
    assert declared["emit_epoch_summary"] is True
    assert declared["generation_tree_writes_allowed"] is False
    assert declared["identifier_fields_allowed"] is False
    assert tuple(declared["events"]) == DECLARED_EVENTS
    validate_config(_config())


def test_missing_logging_block_fails_the_contract() -> None:
    config = _config()
    del config["runtime"]["progress_logging"]

    with pytest.raises(S105ContractError, match="progress_logging"):
        validate_config(config)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("destination", "file", "destination"),
        ("schema_version", "voice_coach.other.v9", "schema_version"),
        ("generation_tree_writes_allowed", True, "generation_tree_writes_allowed"),
        ("identifier_fields_allowed", True, "identifier_fields_allowed"),
        ("heartbeat_utterances", 0, "heartbeat_utterances"),
        ("heartbeat_utterances", -5, "heartbeat_utterances"),
        ("heartbeat_utterances", 2000.5, "heartbeat_utterances"),
        ("heartbeat_utterances", True, "heartbeat_utterances"),
        ("emit_epoch_summary", "yes", "emit_epoch_summary"),
    ],
)
def test_undeclared_logging_values_fail_the_contract(
    field: str,
    value: object,
    message: str,
) -> None:
    config = _config()
    config["runtime"]["progress_logging"][field] = value

    with pytest.raises(S105ContractError, match=message):
        validate_config(config)


def test_event_list_drift_fails_the_contract() -> None:
    config = _config()
    config["runtime"]["progress_logging"]["events"] = ["run_started"]

    with pytest.raises(S105ContractError, match="progress_logging.events"):
        validate_config(config)


def test_contract_resolves_from_the_repository_config() -> None:
    resolved = ProgressLoggingContract.from_config(_config())

    assert resolved.heartbeat_utterances == 2000
    assert resolved.emit_epoch_summary is True


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"destination": "file"}, "destination is not declared"),
        ({"schema_version": "x"}, "schema version differs"),
        ({"heartbeat_utterances": 0}, "positive integer"),
        ({"emit_epoch_summary": "yes"}, "must be a boolean"),
    ],
)
def test_runtime_contract_resolution_fails_closed(
    mutation: dict,
    message: str,
) -> None:
    config = _config()
    config["runtime"]["progress_logging"].update(mutation)

    with pytest.raises(S105TrainingError, match=message):
        ProgressLoggingContract.from_config(config)


def test_emitted_event_is_one_canonical_json_line() -> None:
    buffer = io.StringIO()

    with redirect_stdout(buffer):
        _emit_progress("fit_heartbeat", seed=42, epoch=1, fit_utterances_done=2000)

    lines = buffer.getvalue().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["schema_version"] == PROGRESS_EVENT_SCHEMA_VERSION
    assert payload["event"] == "fit_heartbeat"
    assert payload["fit_utterances_done"] == 2000
    assert list(payload) == sorted(payload)


@pytest.mark.parametrize(
    "field",
    [
        "utterance_id_sha256",
        "speaker_id_sha256",
        "audio_path",
        "dataset_root",
        "pcm_sha256",
        "path",
    ],
)
def test_emitting_a_protected_field_fails_closed(field: str) -> None:
    with pytest.raises(S105TrainingError, match="leak a protected field"):
        _emit_progress("fit_heartbeat", **{field: "value"})


def test_non_finite_numbers_are_refused_rather_than_written_as_nan() -> None:
    with pytest.raises(ValueError):
        _emit_progress("fit_heartbeat", loss=float("nan"))


def test_declared_events_match_what_the_execution_module_emits() -> None:
    """A declared event that is never emitted, or vice versa, is a contract lie."""

    source = (
        ROOT / "src/voice_coach/training/wavlm_hierarchy_execution.py"
    ).read_text(encoding="utf-8")
    emitted = set(re.findall(r'_emit_progress\(\s*"([a-z_]+)"', source))

    assert emitted == set(DECLARED_EVENTS)
