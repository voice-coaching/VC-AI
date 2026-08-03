from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import threading
from typing import Any, Callable
import xml.etree.ElementTree as ET

import pytest
import yaml  # type: ignore[import-untyped]

import voice_coach.reproducibility.cohort_flow_svg as cohort
from voice_coach.reproducibility.cohort_flow_svg import (
    CANONICAL_DESCRIPTION,
    CANONICAL_TITLE,
    CONFIG_SCHEMA,
    CohortFlowValidationError,
    ImplementationSnapshot,
    RECEIPT_MODE,
    RenderConfig,
    RenderNamespace,
    SVG_MODE,
    capture_test_implementation_snapshot,
    config_summary,
    load_render_config,
    render_and_publish,
    validate_published,
)


REPOSITORY_ROOT = Path(__file__).parents[2]
CANONICAL_MMD = (
    REPOSITORY_ROOT / "docs" / "paper" / "figures" / "aihub71490_cohort_flow.mmd"
)
CANONICAL_LINEAGE = {
    "data_card": "4ab53c6837b1a28fbf153dd3a351a195f6931a43694467114cea6c21b8a57b13",
    "g0_audit": "1a41814f04062c1f1242287e05e34f46673e0e6e2152075c0dd1cf019bce81fd",
    "prompt_reservation": (
        "3ea7f12ea76be07a0796d7e62f0c3727ca9858c0278df7c85ac1e08d9b653fa9"
    ),
    "filter_report": (
        "2f7b540b5fb6d2aaad3e2efa29da5f5b23c967645da0eb98d9a6d68ffc5b762e"
    ),
}


@dataclass(frozen=True)
class Fixture:
    root: Path
    config_path: Path
    namespace: RenderNamespace


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_yaml(path: Path, value: Any) -> None:
    path.write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _namespace() -> RenderNamespace:
    return RenderNamespace(
        config_path="config.yaml",
        source_paths={
            "source_mmd": "docs/paper/figures/flow.mmd",
            "data_card": "data/card.json",
            "g0_audit": "data/g0.json",
            "prompt_reservation": "data/prompt.json",
            "filter_report": "data/filter.json",
        },
        renderer_path="implementation/cohort_flow_svg.py",
        cli_path="implementation/render_aihub71490_cohort_flow.py",
        output_svg="docs/paper/figures/flow.svg",
        receipt="artifacts/flow-receipt.json",
        lock_path="artifacts/.flow.lock",
        title=CANONICAL_TITLE,
        description=CANONICAL_DESCRIPTION,
        production=False,
    )


def _fixture(tmp_path: Path) -> Fixture:
    namespace = _namespace()
    modeling_sha = "1" * 64
    split_sha = "2" * 64
    split_counts = {
        "calibration": 35106,
        "dev": 35074,
        "locked_test": 66312,
        "stack": 35211,
        "train": 150677,
    }
    qc_counts = {
        "interval_out_of_bounds": 1070,
        "missing_realized_phone_tier": 1,
    }
    phone_operations = {
        "deletion": 38385,
        "insertion": 25783,
        "match": 3991194,
        "substitution": 267049,
    }
    data_card = {
        "schema_version": "aihub71490.data_card.v1",
        "manifest_sha256": modeling_sha,
        "split_assignment_sha256": split_sha,
        "record_count": 322380,
        "split_counts": split_counts,
        "speaker_count": 1830,
        "unique_pcm_count": 322368,
        "qc_flag_counts": qc_counts,
        "phone_alignment_operations": phone_operations,
        "prompt_count": 229,
        "unknown_error_tag_occurrences": 0,
    }
    g0 = {
        "schema_version": "aihub71490.g0.audit.v1",
        "automated_status": "passed",
        "overall_g0_status": "pending_manual_alignment_audit",
        "record_count": 322380,
        "speaker_count": 1830,
        "unique_pcm_count": 322368,
        "split_counts": split_counts,
        "split_assignment_sha256": split_sha,
        "parquet_sha256": modeling_sha,
        "pairing_rate": 0.9999968980705999,
        "decoder_failure_rate": 0.0,
        "time_bound_error_rate": 0.003319064458092934,
        "annotation_parse_failures": 0,
        "qc_flag_counts": qc_counts,
        "phone_alignment_operations": phone_operations,
        "cross_split_speaker_overlap_examples": [],
        "cross_split_pcm_duplicate_examples": [],
        "thresholds": {
            "expected_records": 322380,
            "min_pairing_rate": 0.99,
            "max_decoder_failure_rate": 0.001,
            "max_time_bound_error_rate": 0.01,
        },
        "failed_automated_gates": [],
        "manual_alignment_audit": {
            "status": "pending",
            "required_samples": 240,
            "minimum_accuracy": 0.95,
        },
    }
    prompt = {
        "schema_version": "voice_coach.prompt_challenge_reservation.v1",
        "source": {
            "modeling_manifest_sha256": modeling_sha,
            "row_count": 322380,
        },
        "selection": {
            "requested_prompt_count": 20,
            "selected_prompt_count": 20,
            "locked_test_candidate_prompt_count": 229,
        },
        "counts": {
            "source_rows_by_split": split_counts,
            "reserved_rows_by_source_split": {
                "calibration": 2941,
                "dev": 2887,
                "locked_test": 5522,
                "stack": 2971,
                "train": 12441,
            },
            "row_dispositions": {
                "prompt_challenge": 5522,
                "exclude_from_training_and_tuning": 21240,
                "preserve_source_split": 295618,
            },
        },
        "reserved_prompts": [{"index": index} for index in range(20)],
    }
    data_path = tmp_path / namespace.source_paths["data_card"]
    g0_path = tmp_path / namespace.source_paths["g0_audit"]
    prompt_path = tmp_path / namespace.source_paths["prompt_reservation"]
    _write_json(data_path, data_card)
    _write_json(g0_path, g0)
    _write_json(prompt_path, prompt)
    filter_report = {
        "schema_version": "voice_coach.explicit_feature_cache_report.v1",
        "status": "completed",
        "source": {
            "modeling_manifest_sha256": modeling_sha,
            "prompt_reservation_sha256": _sha(prompt_path),
        },
        "progress": {
            "source_rows_scanned": 322380,
            "source_rows_by_split": split_counts,
            "source_scan_complete": True,
            "utterance_rows_written": 169837,
            "included_rows_by_split": {"train": 137759, "dev": 32078},
            "exclusions": {
                "split_not_allowed": 136629,
                "qc_flags_present": 642,
                "reserved_prompt": 15272,
            },
        },
        "outputs": {
            "utterance": {
                "row_count": 169837,
                "role": "canonical",
                "sha256": "3" * 64,
            },
            "phone": {"enabled": False, "row_count": 0},
        },
    }
    filter_path = tmp_path / namespace.source_paths["filter_report"]
    _write_json(filter_path, filter_report)

    source_paths = {
        "data_card": data_path,
        "g0_audit": g0_path,
        "prompt_reservation": prompt_path,
        "filter_report": filter_path,
    }
    records = {
        key: {"path": namespace.source_paths[key], "sha256": _sha(path)}
        for key, path in source_paths.items()
    }
    mmd_path = tmp_path / namespace.source_paths["source_mmd"]
    mmd_path.parent.mkdir(parents=True, exist_ok=True)
    mmd_text = CANONICAL_MMD.read_text(encoding="utf-8")
    for key, canonical_digest in CANONICAL_LINEAGE.items():
        mmd_text = mmd_text.replace(canonical_digest, records[key]["sha256"])
    mmd_path.write_text(mmd_text, encoding="utf-8")

    renderer_path = tmp_path / namespace.renderer_path
    cli_path = tmp_path / namespace.cli_path
    renderer_path.parent.mkdir(parents=True)
    renderer_path.write_bytes(b"# isolated renderer snapshot v1\n")
    cli_path.write_bytes(b"# isolated CLI snapshot v1\n")
    config = {
        "schema_version": CONFIG_SCHEMA,
        "title": namespace.title,
        "description": namespace.description,
        "canvas": {"width": 1440, "height": 900},
        "source_mmd": {
            "path": namespace.source_paths["source_mmd"],
            "sha256": _sha(mmd_path),
        },
        "sources": records,
        "implementation": {
            "renderer": {
                "path": namespace.renderer_path,
                "sha256": _sha(renderer_path),
            },
            "cli": {"path": namespace.cli_path, "sha256": _sha(cli_path)},
        },
        "output_svg": namespace.output_svg,
        "receipt": namespace.receipt,
    }
    config_path = tmp_path / namespace.config_path
    _write_yaml(config_path, config)
    return Fixture(tmp_path, config_path, namespace)


def _load(fixture: Fixture) -> RenderConfig:
    return load_render_config(
        fixture.config_path,
        workspace_root=fixture.root,
        namespace=fixture.namespace,
    )


def _implementation(config: RenderConfig) -> ImplementationSnapshot:
    return capture_test_implementation_snapshot(config)


def _publish(fixture: Fixture) -> dict[str, Any]:
    config = _load(fixture)
    return render_and_publish(config, implementation=_implementation(config))


def _repin_source(fixture: Fixture, key: str) -> None:
    config = yaml.safe_load(fixture.config_path.read_text(encoding="utf-8"))
    source_path = fixture.root / fixture.namespace.source_paths[key]
    old_digest = config["sources"][key]["sha256"]
    new_digest = _sha(source_path)
    config["sources"][key]["sha256"] = new_digest
    mmd_path = fixture.root / fixture.namespace.source_paths["source_mmd"]
    mmd_path.write_text(
        mmd_path.read_text(encoding="utf-8").replace(old_digest, new_digest),
        encoding="utf-8",
    )
    config["source_mmd"]["sha256"] = _sha(mmd_path)
    _write_yaml(fixture.config_path, config)


def _mutate_json(
    fixture: Fixture,
    key: str,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    path = fixture.root / fixture.namespace.source_paths[key]
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value)
    _write_json(path, value)
    _repin_source(fixture, key)


def _published_paths(fixture: Fixture) -> tuple[Path, Path]:
    return (
        fixture.root / fixture.namespace.output_svg,
        fixture.root / fixture.namespace.receipt,
    )


def test_render_is_deterministic_portable_and_deep_valid(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    first = _publish(fixture)
    svg_path, receipt_path = _published_paths(fixture)
    first_svg = svg_path.read_bytes()
    first_receipt = receipt_path.read_bytes()
    second = _publish(fixture)
    assert first == second
    assert svg_path.read_bytes() == first_svg
    assert receipt_path.read_bytes() == first_receipt
    assert stat.S_IMODE(svg_path.stat().st_mode) == SVG_MODE
    assert stat.S_IMODE(receipt_path.stat().st_mode) == RECEIPT_MODE
    assert svg_path.stat().st_nlink == receipt_path.stat().st_nlink == 1
    root = ET.fromstring(first_svg)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert not root.findall(".//{http://www.w3.org/2000/svg}script")
    assert not root.findall(".//{http://www.w3.org/2000/svg}foreignObject")
    g_to_h = root.find(".//{http://www.w3.org/2000/svg}path[@id='edge-G-H']")
    assert g_to_h is not None
    assert g_to_h.attrib["d"] == "M 1270 90 H 1400 V 550 H 1295"
    edge_label = next(
        element
        for element in root.findall(".//{http://www.w3.org/2000/svg}text")
        if element.text == "대체하지 않음"
    )
    assert edge_label.attrib["x"] == "1348"
    assert edge_label.attrib["y"] == "315"
    assert "transform" not in edge_label.attrib
    config = _load(fixture)
    assert validate_published(config, implementation=_implementation(config)) == first


def test_config_only_reads_no_sources_or_implementation(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    shutil.rmtree(tmp_path / "data")
    (tmp_path / fixture.namespace.source_paths["source_mmd"]).unlink()
    shutil.rmtree(tmp_path / "implementation")
    summary = config_summary(_load(fixture))
    assert summary["source_reads"] == 0
    assert summary["output_writes"] == 0
    assert not _published_paths(fixture)[0].exists()
    assert not _published_paths(fixture)[1].exists()


def test_alternate_config_and_output_paths_are_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    alternate = tmp_path / "alternate.yaml"
    alternate.write_bytes(fixture.config_path.read_bytes())
    with pytest.raises(CohortFlowValidationError, match="CONFIG_PATH_NOT_CANONICAL"):
        load_render_config(
            alternate,
            workspace_root=tmp_path,
            namespace=fixture.namespace,
        )
    value = yaml.safe_load(fixture.config_path.read_text(encoding="utf-8"))
    value["output_svg"] = "docs/paper/figures/alternate.svg"
    _write_yaml(fixture.config_path, value)
    with pytest.raises(CohortFlowValidationError, match="CONFIG_OUTPUT_PATH_INVALID"):
        _load(fixture)


def test_test_namespace_cannot_claim_production(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    with pytest.raises(CohortFlowValidationError, match="NAMESPACE_INVALID"):
        load_render_config(
            fixture.config_path,
            workspace_root=tmp_path,
            namespace=replace(fixture.namespace, production=True),
        )


@pytest.mark.parametrize("source_key", list(_namespace().source_paths))
@pytest.mark.parametrize("target", ["output_svg", "receipt"])
def test_all_source_output_lexical_aliases_are_rejected(
    tmp_path: Path,
    source_key: str,
    target: str,
) -> None:
    fixture = _fixture(tmp_path)
    duplicate = fixture.namespace.source_paths[source_key]
    namespace = (
        replace(fixture.namespace, output_svg=duplicate)
        if target == "output_svg"
        else replace(fixture.namespace, receipt=duplicate)
    )
    with pytest.raises(CohortFlowValidationError, match="NAMESPACE_INVALID"):
        load_render_config(
            fixture.config_path,
            workspace_root=tmp_path,
            namespace=namespace,
        )


def test_source_and_existing_output_hardlinks_are_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    card = tmp_path / fixture.namespace.source_paths["data_card"]
    os.link(card, tmp_path / "card-alias.json")
    with pytest.raises(CohortFlowValidationError, match="NLINK_INVALID"):
        _publish(fixture)

    (tmp_path / "card-alias.json").unlink()
    output, _ = _published_paths(fixture)
    output.parent.mkdir(parents=True, exist_ok=True)
    harmless = tmp_path / "harmless.svg"
    harmless.write_bytes(b"prior")
    os.link(harmless, output)
    with pytest.raises(CohortFlowValidationError, match="EXISTING_SVG_NLINK_INVALID"):
        _publish(fixture)


def test_symlink_source_and_output_are_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    card = tmp_path / fixture.namespace.source_paths["data_card"]
    target = tmp_path / "card-target.json"
    card.rename(target)
    card.symlink_to(target)
    with pytest.raises(CohortFlowValidationError, match="NOT_REGULAR"):
        _publish(fixture)
    card.unlink()
    target.rename(card)

    output, _ = _published_paths(fixture)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.symlink_to(card)
    with pytest.raises(CohortFlowValidationError, match="EXISTING_SVG_NOT_REGULAR"):
        _publish(fixture)


def test_source_path_swap_is_detected_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    original = cohort._revalidate_inputs
    raced = False

    def race(
        config: RenderConfig,
        implementation: ImplementationSnapshot,
        sources: cohort.ValidatedSources,
    ) -> None:
        nonlocal raced
        if not raced:
            raced = True
            path = fixture.root / fixture.namespace.source_paths["filter_report"]
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(path.read_bytes())
            os.replace(replacement, path)
        original(config, implementation, sources)

    monkeypatch.setattr(cohort, "_revalidate_inputs", race)
    with pytest.raises(CohortFlowValidationError, match="SNAPSHOT_CHANGED"):
        _publish(fixture)
    assert not _published_paths(fixture)[0].exists()
    assert not _published_paths(fixture)[1].exists()


def test_stale_implementation_snapshot_is_rejected_before_publish(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    config = _load(fixture)
    implementation = _implementation(config)
    (tmp_path / fixture.namespace.renderer_path).write_bytes(b"# stale bytes\n")
    with pytest.raises(CohortFlowValidationError, match="HASH_MISMATCH"):
        render_and_publish(config, implementation=implementation)
    assert not _published_paths(fixture)[0].exists()
    assert not _published_paths(fixture)[1].exists()


def test_second_write_failure_removes_new_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    original = cohort._atomic_write
    calls = 0

    def fail_second(path: Path, payload: bytes, *, mode: int) -> cohort.FileSnapshot:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second write failure")
        return original(path, payload, mode=mode)

    monkeypatch.setattr(cohort, "_atomic_write", fail_second)
    with pytest.raises(OSError, match="injected"):
        _publish(fixture)
    assert not _published_paths(fixture)[0].exists()
    assert not _published_paths(fixture)[1].exists()


def test_second_write_failure_restores_previous_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    _publish(fixture)
    output, receipt = _published_paths(fixture)
    prior = (output.read_bytes(), receipt.read_bytes())
    original = cohort._atomic_write
    calls = 0

    def fail_second_once(
        path: Path,
        payload: bytes,
        *,
        mode: int,
    ) -> cohort.FileSnapshot:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second write failure")
        return original(path, payload, mode=mode)

    monkeypatch.setattr(cohort, "_atomic_write", fail_second_once)
    with pytest.raises(OSError, match="injected"):
        _publish(fixture)
    assert (output.read_bytes(), receipt.read_bytes()) == prior
    assert stat.S_IMODE(output.stat().st_mode) == SVG_MODE
    assert stat.S_IMODE(receipt.stat().st_mode) == RECEIPT_MODE


def test_post_publish_deep_failure_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)

    def fail_deep(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise CohortFlowValidationError("INJECTED_DEEP_FAILURE")

    monkeypatch.setattr(cohort, "_validate_published_locked", fail_deep)
    with pytest.raises(CohortFlowValidationError, match="INJECTED_DEEP_FAILURE"):
        _publish(fixture)
    assert not _published_paths(fixture)[0].exists()
    assert not _published_paths(fixture)[1].exists()


def test_concurrent_writers_are_serialized_and_pair_is_deep_valid(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: _publish(fixture), range(2)))
    assert results[0] == results[1]
    config = _load(fixture)
    assert (
        validate_published(config, implementation=_implementation(config)) == results[0]
    )


def test_output_root_inode_rebind_cannot_create_a_parallel_lock_domain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    original = cohort._validate_sources
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    call_guard = threading.Lock()
    call_count = 0

    def pause_first(config: RenderConfig) -> cohort.ValidatedSources:
        nonlocal call_count
        with call_guard:
            call_count += 1
            current = call_count
        if current == 1:
            first_entered.set()
            assert release_first.wait(timeout=5)
        else:
            second_entered.set()
        return original(config)

    monkeypatch.setattr(cohort, "_validate_sources", pause_first)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(_publish, fixture)
        assert first_entered.wait(timeout=5)

        artifact_root = tmp_path / "artifacts"
        detached_root = tmp_path / "artifacts-detached"
        artifact_root.rename(detached_root)
        artifact_root.mkdir()
        rebound_lock = artifact_root / ".flow.lock"
        rebound_lock.write_bytes(b"")
        rebound_lock.chmod(0o600)

        second = executor.submit(_publish, fixture)
        assert not second_entered.wait(timeout=0.25)
        release_first.set()
        with pytest.raises(
            CohortFlowValidationError,
            match="LOCK_SNAPSHOT_CHANGED",
        ):
            first.result(timeout=5)
        result = second.result(timeout=5)

    config = _load(fixture)
    assert validate_published(config, implementation=_implementation(config)) == result


def test_deep_validation_rejects_modes_and_link_counts(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _publish(fixture)
    output, receipt = _published_paths(fixture)
    output.chmod(0o666)
    config = _load(fixture)
    with pytest.raises(CohortFlowValidationError, match="SVG_PUBLISHED_STATE_INVALID"):
        validate_published(config, implementation=_implementation(config))
    output.chmod(SVG_MODE)
    os.link(receipt, tmp_path / "receipt-hardlink.json")
    with pytest.raises(CohortFlowValidationError, match="RECEIPT_NLINK_INVALID"):
        validate_published(config, implementation=_implementation(config))


def test_g0_gate_is_recomputed_from_metrics(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _mutate_json(fixture, "g0_audit", lambda value: value.update(pairing_rate=0.0))
    with pytest.raises(CohortFlowValidationError, match="G0_GATE_RECOMPUTATION_FAILED"):
        _publish(fixture)


def test_g0_thresholds_cannot_be_relaxed(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    def mutate(value: dict[str, Any]) -> None:
        value["thresholds"]["min_pairing_rate"] = 0.0

    _mutate_json(fixture, "g0_audit", mutate)
    with pytest.raises(CohortFlowValidationError, match="G0_GATE_RECOMPUTATION_FAILED"):
        _publish(fixture)


@pytest.mark.parametrize(
    ("key", "mutate", "error"),
    [
        (
            "g0_audit",
            lambda value: value.update(speaker_count=1829),
            "G0_DATA_CARD_DISAGREEMENT",
        ),
        (
            "prompt_reservation",
            lambda value: value["source"].update(modeling_manifest_sha256="9" * 64),
            "SOURCE_LINEAGE_DISAGREEMENT",
        ),
        (
            "filter_report",
            lambda value: value["source"].update(modeling_manifest_sha256="9" * 64),
            "SOURCE_LINEAGE_DISAGREEMENT",
        ),
        (
            "filter_report",
            lambda value: value["progress"]["source_rows_by_split"].update(train=1),
            "PROMPT_SPLIT_DISAGREEMENT",
        ),
    ],
)
def test_cross_source_lineage_and_counts_are_enforced(
    tmp_path: Path,
    key: str,
    mutate: Callable[[dict[str, Any]], None],
    error: str,
) -> None:
    fixture = _fixture(tmp_path)
    _mutate_json(fixture, key, mutate)
    if key == "prompt_reservation":
        filter_path = tmp_path / fixture.namespace.source_paths["filter_report"]
        report = json.loads(filter_path.read_text(encoding="utf-8"))
        report["source"]["prompt_reservation_sha256"] = _sha(
            tmp_path / fixture.namespace.source_paths["prompt_reservation"]
        )
        _write_json(filter_path, report)
        _repin_source(fixture, "filter_report")
    with pytest.raises(CohortFlowValidationError, match=error):
        _publish(fixture)


def test_prompt_filter_overlap_bounds_are_enforced(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    def mutate(value: dict[str, Any]) -> None:
        value["counts"]["reserved_rows_by_source_split"]["train"] = 1
        value["counts"]["reserved_rows_by_source_split"]["dev"] = 1

    _mutate_json(fixture, "prompt_reservation", mutate)
    filter_path = tmp_path / fixture.namespace.source_paths["filter_report"]
    report = json.loads(filter_path.read_text(encoding="utf-8"))
    report["source"]["prompt_reservation_sha256"] = _sha(
        tmp_path / fixture.namespace.source_paths["prompt_reservation"]
    )
    _write_json(filter_path, report)
    _repin_source(fixture, "filter_report")
    with pytest.raises(CohortFlowValidationError, match="PROMPT_FILTER_RESERVED"):
        _publish(fixture)


def test_duplicate_json_and_yaml_keys_are_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    card = tmp_path / fixture.namespace.source_paths["data_card"]
    card.write_text(
        card.read_text(encoding="utf-8").replace(
            '"record_count": 322380,',
            '"record_count": 322380,\n  "record_count": 322380,',
        ),
        encoding="utf-8",
    )
    _repin_source(fixture, "data_card")
    with pytest.raises(CohortFlowValidationError, match="DUPLICATE_KEY"):
        _publish(fixture)

    fixture = _fixture(tmp_path / "yaml")
    fixture.config_path.write_text(
        fixture.config_path.read_text(encoding="utf-8") + f"title: {CANONICAL_TITLE}\n",
        encoding="utf-8",
    )
    with pytest.raises(CohortFlowValidationError, match="CONFIG_DUPLICATE_KEY"):
        _load(fixture)


def test_xml_control_characters_and_unsafe_uris_are_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    mmd = tmp_path / fixture.namespace.source_paths["source_mmd"]
    mmd.write_bytes(
        mmd.read_bytes().replace(
            "전체 source metadata scan".encode(),
            "전체 source metadata \x01scan".encode(),
        )
    )
    config = yaml.safe_load(fixture.config_path.read_text(encoding="utf-8"))
    config["source_mmd"]["sha256"] = _sha(mmd)
    _write_yaml(fixture.config_path, config)
    with pytest.raises(CohortFlowValidationError, match="MMD_LABEL_XML_INVALID"):
        _publish(fixture)

    with pytest.raises(CohortFlowValidationError, match="SVG_EXTERNAL_REFERENCE"):
        cohort._validate_svg_payload(
            b'<svg xmlns="http://www.w3.org/2000/svg">'
            b'<image href="https://example.invalid/x.png"/></svg>'
        )


def test_optional_libreoffice_pdf_smoke(tmp_path: Path) -> None:
    libreoffice = shutil.which("libreoffice")
    if libreoffice is None:
        pytest.skip("LibreOffice is not installed")
    fixture = _fixture(tmp_path)
    _publish(fixture)
    output, _ = _published_paths(fixture)
    conversion = tmp_path / "pdf"
    conversion.mkdir()
    completed = subprocess.run(
        [
            libreoffice,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(conversion),
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    pdf = conversion / "flow.pdf"
    assert pdf.read_bytes().startswith(b"%PDF-")
    pdfinfo = shutil.which("pdfinfo")
    if pdfinfo is not None:
        info = subprocess.run(
            [pdfinfo, str(pdf)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "Pages:           1" in info.stdout
