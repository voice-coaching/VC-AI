from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Callable

import pytest
import yaml

from voice_coach.reproducibility import paper_publication_v2 as publication
from voice_coach.reproducibility import protected_paths


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPOSITORY_ROOT / "scripts" / "audit_paper_publication_v2.py"
CONFIG_PATH = Path(publication.CANONICAL_CONFIG_PATH)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record(root: Path, path: str) -> dict[str, str | int]:
    target = root / path
    return {
        "path": path,
        "sha256": _sha(target),
        "size_bytes": target.stat().st_size,
    }


def _write_config(root: Path, document: dict[str, Any]) -> None:
    (root / CONFIG_PATH).write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _configured_records(document: dict[str, Any]) -> list[dict[str, Any]]:
    if document.get("status") == publication.BLOCKED_CONFIG_STATUS:
        return list(document["current_boundaries"].values())
    records = [document["base_content_contract"]]
    records.extend(document["implementation"].values())
    records.extend(
        document["evidence_gate"][name]
        for name in (
            "report",
            "validator_script",
            "validator_module",
            "validation_document",
        )
    )
    records.extend(
        document["canonical"][name]
        for name in ("manuscript", "evidence_map", "source_ledger")
    )
    records.extend(item["source"] for item in document["canonical"]["local_assets"])
    return records


ACTIVE_BLOCKED_FIXTURE_TESTS = {
    "test_config_only_reads_no_sources_and_writes_no_output",
    "test_completion_state_drift_is_blocked_config_only",
    "test_blocked_audit_fails_before_source_or_output_access",
}


@pytest.fixture(autouse=True)
def _retired_ready_contract_scope(request: pytest.FixtureRequest) -> None:
    original_name = getattr(request.node, "originalname", request.node.name)
    if original_name == "test_locked_key_contract_is_independently_exact":
        pytest.skip("legacy ready-state evidence receipt is retired")
    if (
        "publication_workspace" in request.fixturenames
        and original_name not in ACTIVE_BLOCKED_FIXTURE_TESTS
    ):
        pytest.skip("legacy ready-state publication fixture is retired")


@pytest.fixture
def publication_workspace(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    source_config = REPOSITORY_ROOT / CONFIG_PATH
    document = yaml.safe_load(source_config.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    for source_record in _configured_records(document):
        relative = Path(source_record["path"])
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPOSITORY_ROOT / relative, destination)
    destination_config = tmp_path / CONFIG_PATH
    destination_config.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_config, destination_config)
    return tmp_path, document


def test_locked_key_contract_is_independently_exact() -> None:
    document = yaml.safe_load(
        (REPOSITORY_ROOT / CONFIG_PATH).read_text(encoding="utf-8")
    )
    keys = document["evidence_gate"]["expected"]["locked_access_keys"]
    observed = json.loads(
        (
            REPOSITORY_ROOT
            / "artifacts/reproducibility/manuscript_evidence_validation.json"
        ).read_text(encoding="utf-8")
    )["locked_test_guard"]["accessed"]
    independent_digest = hashlib.sha256(
        "\n".join(sorted(keys)).encode("utf-8")
    ).hexdigest()
    assert len(keys) == 16
    assert keys == sorted(set(keys))
    assert keys == sorted(observed)
    assert independent_digest == publication.LOCKED_KEY_SET_SHA256
    assert (
        independent_digest
        == "de011d5deb430036e293b3b0b16593508cc22f200a30cdcb314c4996184d2f11"
    )


@pytest.mark.parametrize(
    "component",
    [
        "raw",
        "paper-raw-data",
        "paper.raw.data",
        "paper_raw_data",
        "locked",
        "locked-evaluation",
        "restricted",
        "env",
        "environment",
        "secret",
        "credentials",
        ".env",
        ".env.local",
        ".environment",
    ],
)
def test_separator_bounded_protected_components_are_blocked(component: str) -> None:
    assert protected_paths.is_protected_component(component)
    with pytest.raises(protected_paths.ProtectedPathError):
        protected_paths.validate_relative_path(f"docs/{component}/file.json")


@pytest.mark.parametrize(
    "component",
    ["drawer", "secretary", "credentialed", "unlocked"],
)
def test_unbounded_substrings_are_allowed(component: str) -> None:
    assert not protected_paths.is_protected_component(component)
    assert protected_paths.validate_relative_path(
        f"docs/{component}/file.json"
    ) == Path(f"docs/{component}/file.json")


@pytest.mark.parametrize("path", ["/absolute", "../escape", "a/../b", "a/./b", "a//b"])
def test_noncanonical_relative_paths_are_blocked(path: str) -> None:
    with pytest.raises(protected_paths.ProtectedPathError):
        protected_paths.validate_relative_path(path)


def test_config_only_reads_no_sources_and_writes_no_output(
    publication_workspace: tuple[Path, dict[str, Any]],
) -> None:
    root, _ = publication_workspace
    result = publication.validate_config_only(workspace_root=root)
    assert result["status"] == publication.BLOCKED_CONFIG_STATUS
    assert result["blocker"] == publication.BLOCKER_CODE
    assert result["source_record_schemas_validated"] == 4
    counters = result["access_counters"]
    assert counters["config_files_read"] == 1
    assert all(
        value == 0 for key, value in counters.items() if key != "config_files_read"
    )
    assert (
        result["privacy"] == publication.AccessCounters(config_files_read=1).privacy()
    )
    assert result["completion_state"] == publication.COMPLETION_STATE
    assert not (root / publication.CANONICAL_OUTPUT_ROOT).exists()
    assert not (root / publication.ROOT_NAMESPACE_LOCK_NAME).exists()


def test_blocked_audit_fails_before_source_or_output_access(
    publication_workspace: tuple[Path, dict[str, Any]],
) -> None:
    root, _ = publication_workspace
    with pytest.raises(publication.PublicationV2Error, match=publication.BLOCKER_CODE):
        publication.audit_paper_publication(workspace_root=root)
    assert not (root / publication.CANONICAL_OUTPUT_ROOT).exists()
    assert not (root / publication.ROOT_NAMESPACE_LOCK_NAME).exists()


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("paper_complete", True),
        ("paper_complete", 0),
        ("submission_status", "submitted"),
        ("manual_approval_required", False),
        ("manual_approval_required", 1),
        ("manual_approval_status", "approved"),
    ],
)
def test_completion_state_drift_is_blocked_config_only(
    publication_workspace: tuple[Path, dict[str, Any]],
    field: str,
    replacement: object,
) -> None:
    root, document = publication_workspace
    changed = deepcopy(document)
    changed["completion_state"][field] = replacement
    _write_config(root, changed)
    with pytest.raises(publication.PublicationV2Error, match="completion_state"):
        publication.validate_config_only(workspace_root=root)


def test_pending_regex_must_be_byte_exact(
    publication_workspace: tuple[Path, dict[str, Any]],
) -> None:
    root, document = publication_workspace
    changed = deepcopy(document)
    changed["policy"]["pending_marker_regex"] = r"\[PENDING:.+\]"
    _write_config(root, changed)
    with pytest.raises(publication.PublicationV2Error, match="policy_pending_regex"):
        publication.validate_config_only(workspace_root=root)


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("docs/raw/file.md", False),
        ("docs/a-locked-evaluation/file.md", False),
        ("docs/restricted/file.md", False),
        ("docs/env/file.md", False),
        ("docs/environment/file.md", False),
        ("docs/secret/file.md", False),
        ("docs/credential/file.md", False),
        ("docs/.env.production/file.md", False),
        ("docs/drawer/file.md", True),
        ("docs/secretary/file.md", True),
        ("docs/credentialed/file.md", True),
        ("docs/unlocked/file.md", True),
    ],
)
def test_config_path_denylist_is_separator_bounded(
    publication_workspace: tuple[Path, dict[str, Any]],
    path: str,
    allowed: bool,
) -> None:
    root, document = publication_workspace
    changed = deepcopy(document)
    changed["implementation"]["module"]["path"] = path
    _write_config(root, changed)
    if allowed:
        assert publication.validate_config_only(workspace_root=root)["status"] == (
            "valid_config_only"
        )
    else:
        with pytest.raises(publication.PublicationV2Error, match="forbidden"):
            publication.validate_config_only(workspace_root=root)


@pytest.mark.parametrize("mutation", ["shrink", "rename", "add"])
def test_locked_key_set_drift_is_blocked_config_only(
    publication_workspace: tuple[Path, dict[str, Any]],
    mutation: str,
) -> None:
    root, document = publication_workspace
    changed = deepcopy(document)
    expected = changed["evidence_gate"]["expected"]
    keys = list(expected["locked_access_keys"])
    if mutation == "shrink":
        keys.pop()
    elif mutation == "rename":
        keys[-1] = "wavlm_s103_results_json:renamed"
    else:
        keys.append("unexpected_locked_access_key")
    keys = sorted(keys)
    expected["locked_access_keys"] = keys
    expected["locked_access_entry_count"] = len(keys)
    expected["locked_access_keys_sha256"] = hashlib.sha256(
        "\n".join(keys).encode("utf-8")
    ).hexdigest()
    _write_config(root, changed)
    with pytest.raises(
        publication.PublicationV2Error,
        match="evidence_locked_key_contract",
    ):
        publication.validate_config_only(workspace_root=root)


def test_output_namespace_drift_is_blocked_config_only(
    publication_workspace: tuple[Path, dict[str, Any]],
) -> None:
    root, document = publication_workspace
    changed = deepcopy(document)
    changed["output"]["root"] = (
        "artifacts/reproducibility/paper_publication_v2-alternate"
    )
    _write_config(root, changed)
    with pytest.raises(publication.PublicationV2Error, match="output_namespace"):
        publication.validate_config_only(workspace_root=root)


def test_report_locked_key_shrink_is_blocked_before_canonical_reads(
    publication_workspace: tuple[Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, document = publication_workspace
    report_path = root / document["evidence_gate"]["report"]["path"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["locked_test_guard"]["accessed"].pop("wavlm_s103_results_json:15")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    changed = deepcopy(document)
    changed["evidence_gate"]["report"] = _record(
        root,
        document["evidence_gate"]["report"]["path"],
    )
    _write_config(root, changed)
    canonical_reads = 0
    original = publication._read_record

    def measured_read(*args: Any, **kwargs: Any) -> Any:
        nonlocal canonical_reads
        if kwargs.get("category") in {"canonical", "local_asset"}:
            canonical_reads += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(publication, "_read_record", measured_read)
    with pytest.raises(publication.PublicationV2Error, match="evidence_report_locked"):
        publication.audit_paper_publication(workspace_root=root)
    assert canonical_reads == 0


def test_publish_and_deep_validate_exact_state_and_observations(
    publication_workspace: tuple[Path, dict[str, Any]],
) -> None:
    root, _ = publication_workspace
    published = publication.audit_paper_publication(workspace_root=root)
    validated = publication.validate_publication_receipt(workspace_root=root)
    assert published["status"] == validated["status"] == "passed"
    assert published["generation_id"] == validated["generation_id"]
    assert validated["completion_state"] == publication.COMPLETION_STATE
    assert validated["locked_evaluation_contract"] == {
        "entry_count": 16,
        "keys_sha256": publication.LOCKED_KEY_SET_SHA256,
        "all_access_flags_false": True,
        "locked_test_files_read": 0,
    }
    assert (
        validated["privacy"]
        == publication.AccessCounters(**validated["access_counters"]).privacy()
    )
    commit_path = (
        root / publication.CANONICAL_OUTPUT_ROOT / publication.CANONICAL_COMMIT_NAME
    )
    commit = json.loads(commit_path.read_text(encoding="utf-8"))
    receipt_path = root / commit["receipt"]["path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert commit["completion_state"] == publication.COMPLETION_STATE
    assert receipt["completion_state"] == publication.COMPLETION_STATE
    assert (
        receipt["privacy"]
        == publication.AccessCounters(**receipt["access_counters"]).privacy()
    )
    assert receipt["network_observation"] == {
        "performed": bool(receipt["access_counters"]["network_requests"]),
        "request_count": receipt["access_counters"]["network_requests"],
        "canonical_completion_evidence": False,
    }
    assert receipt["canonical_files_modified"] is bool(
        receipt["access_counters"]["canonical_files_written"]
    )


def test_source_ancestor_rebind_is_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    file_path = source / "branch/value.json"
    file_path.parent.mkdir(parents=True)
    file_path.write_text('{"safe": true}\n', encoding="utf-8")
    original = protected_paths.read_file_at
    attacked = False

    def rebind_before_read(*args: Any, **kwargs: Any) -> Any:
        nonlocal attacked
        if not attacked:
            attacked = True
            os.rename(source, tmp_path / "source-displaced")
            replacement = source / "branch"
            replacement.mkdir(parents=True)
            (replacement / "value.json").write_text(
                '{"safe": false}\n',
                encoding="utf-8",
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(protected_paths, "read_file_at", rebind_before_read)
    with protected_paths.RootDirectory.open(tmp_path) as workspace:
        with pytest.raises(
            protected_paths.DescriptorIOError,
            match="directory_route_rebound",
        ):
            workspace.read_bytes(
                "source/branch/value.json",
                maximum_bytes=1024,
            )


def test_source_drift_between_initial_audit_and_commit_is_blocked(
    publication_workspace: tuple[Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, document = publication_workspace
    target = root / document["implementation"]["protected_paths"]["path"]
    original = publication._assert_records_unchanged
    calls = 0

    def mutate_after_first_recheck(*args: Any, **kwargs: Any) -> None:
        nonlocal calls
        original(*args, **kwargs)
        calls += 1
        if calls == 1:
            target.write_bytes(target.read_bytes() + b"\n# drift\n")

    monkeypatch.setattr(
        publication,
        "_assert_records_unchanged",
        mutate_after_first_recheck,
    )
    with pytest.raises(publication.PublicationV2Error, match="source_recheck"):
        publication.audit_paper_publication(workspace_root=root)
    commit = (
        root / publication.CANONICAL_OUTPUT_ROOT / publication.CANONICAL_COMMIT_NAME
    )
    assert not commit.exists()


def test_workspace_root_rebind_is_detected(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    with protected_paths.RootDirectory.open(root) as workspace:
        os.rename(root, tmp_path / "workspace-displaced")
        root.mkdir()
        with pytest.raises(
            protected_paths.DescriptorIOError,
            match="workspace_rebound",
        ):
            workspace.assert_bound()


def test_post_commit_output_rebind_rolls_back_through_held_fd(
    publication_workspace: tuple[Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = publication_workspace
    output = root / publication.CANONICAL_OUTPUT_ROOT
    displaced = root / "artifacts/reproducibility/paper-publication-v2-displaced"
    original = publication.PublicationStore.atomic_replace
    attacked = False

    def replace_then_rebind(
        self: publication.PublicationStore,
        name: str,
        content: bytes,
        *,
        mode: int,
    ) -> None:
        nonlocal attacked
        original(self, name, content, mode=mode)
        if name == publication.CANONICAL_COMMIT_NAME and not attacked:
            attacked = True
            os.rename(output, displaced)
            output.mkdir(mode=0o750)

    monkeypatch.setattr(
        publication.PublicationStore,
        "atomic_replace",
        replace_then_rebind,
    )
    with pytest.raises(publication.PublicationV2Error, match="output_root_rebound"):
        publication.audit_paper_publication(workspace_root=root)
    assert not (output / publication.CANONICAL_COMMIT_NAME).exists()
    assert not (displaced / publication.CANONICAL_COMMIT_NAME).exists()


@pytest.mark.parametrize("tamper", ["symlink", "hardlink", "wrong_mode"])
def test_structurally_invalid_existing_commit_is_never_overwritten(
    publication_workspace: tuple[Path, dict[str, Any]],
    tamper: str,
) -> None:
    root, _ = publication_workspace
    publication.audit_paper_publication(workspace_root=root)
    output = root / publication.CANONICAL_OUTPUT_ROOT
    commit = output / publication.CANONICAL_COMMIT_NAME
    original = commit.read_bytes()
    if tamper == "wrong_mode":
        commit.chmod(0o640)
    else:
        decoy = output / "decoy.json"
        decoy.write_bytes(b'{"decoy": true}\n')
        commit.unlink()
        if tamper == "symlink":
            commit.symlink_to(decoy.name)
        else:
            os.link(decoy, commit)
    with pytest.raises(
        publication.PublicationV2Error,
        match="existing_commit_contract",
    ):
        publication.audit_paper_publication(workspace_root=root)
    if tamper == "wrong_mode":
        assert commit.read_bytes() == original
    elif tamper == "symlink":
        assert commit.is_symlink()
    else:
        assert commit.stat().st_nlink == 2


def test_namespace_lock_path_replacement_is_detected(
    publication_workspace: tuple[Path, dict[str, Any]],
) -> None:
    root, _ = publication_workspace
    publication.audit_paper_publication(workspace_root=root)
    with pytest.raises(publication.PublicationV2Error, match="namespace_lock_contract"):
        with protected_paths.RootDirectory.open(root) as workspace:
            with publication.PublicationStore.open(
                workspace,
                exclusive=True,
                create=False,
            ):
                os.unlink(
                    publication.ROOT_NAMESPACE_LOCK_NAME,
                    dir_fd=workspace.descriptor,
                )
                replacement = os.open(
                    publication.ROOT_NAMESPACE_LOCK_NAME,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=workspace.descriptor,
                )
                os.close(replacement)


def test_atomic_replace_rejects_staging_name_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "commit.json"
    target.write_bytes(b"prior\n")
    descriptor = os.open(
        tmp_path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    original_replace = protected_paths.os.replace

    def substitute_before_replace(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
    ) -> None:
        os.unlink(source, dir_fd=src_dir_fd)
        replacement = os.open(
            source,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o440,
            dir_fd=src_dir_fd,
        )
        try:
            os.write(replacement, b"substituted\n")
            os.fsync(replacement)
        finally:
            os.close(replacement)
        original_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(protected_paths.os, "replace", substitute_before_replace)
    try:
        with pytest.raises(
            protected_paths.DescriptorIOError,
            match="atomic_target_identity",
        ):
            protected_paths.atomic_replace_at(
                descriptor,
                "commit.json",
                ".commit.tmp",
                b"intended\n",
                mode=0o440,
            )
    finally:
        os.close(descriptor)
    assert target.read_bytes() == b"substituted\n"


def test_no_prior_commit_is_removed_after_post_publish_failure(
    publication_workspace: tuple[Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = publication_workspace

    def fail_validation(*args: Any, **kwargs: Any) -> Any:
        raise publication.PublicationV2Error("injected_post_commit_failure")

    monkeypatch.setattr(publication, "_validate_loaded", fail_validation)
    with pytest.raises(
        publication.PublicationV2Error,
        match="injected_post_commit_failure",
    ):
        publication.audit_paper_publication(workspace_root=root)
    commit = (
        root / publication.CANONICAL_OUTPUT_ROOT / publication.CANONICAL_COMMIT_NAME
    )
    assert not commit.exists()


def test_prior_valid_commit_is_restored_after_post_publish_failure(
    publication_workspace: tuple[Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = publication_workspace
    publication.audit_paper_publication(workspace_root=root)
    commit = (
        root / publication.CANONICAL_OUTPUT_ROOT / publication.CANONICAL_COMMIT_NAME
    )
    prior = commit.read_bytes()
    original = publication._validate_loaded
    calls = 0

    def fail_second_validation(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise publication.PublicationV2Error("injected_post_commit_failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(publication, "_validate_loaded", fail_second_validation)
    with pytest.raises(
        publication.PublicationV2Error,
        match="injected_post_commit_failure",
    ):
        publication.audit_paper_publication(workspace_root=root)
    assert commit.read_bytes() == prior


def _fake_network_observation(
    root: Path,
    document: dict[str, Any],
) -> dict[str, Any]:
    base_path = root / document["base_content_contract"]["path"]
    base = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    statuses = {
        record["url"]: record["expected_status"]
        for record in base["external_url_contract"]["records"]
    }

    def requester(url: str, timeout: float) -> tuple[int, str]:
        assert timeout == 1.0
        return statuses[url], url

    return dict(
        publication.observe_external_urls(
            workspace_root=root,
            requester=requester,
            observed_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
            timeout_seconds=1.0,
        )
    )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value["completion_state"].update({"paper_complete": True}),
        lambda value: value["access_counters"].update({"network_requests": "36"}),
        lambda value: value["access_counters"].update(
            {"referenced_source_files_read": 777}
        ),
        lambda value: value["access_counters"].update({"raw_files_read": 1}),
        lambda value: value["privacy"].update({"aggregate_only": False}),
    ],
)
def test_invalid_network_observation_is_rejected_before_write(
    publication_workspace: tuple[Path, dict[str, Any]],
    mutator: Callable[[dict[str, Any]], None],
) -> None:
    root, document = publication_workspace
    observation = _fake_network_observation(root, document)
    mutator(observation)
    with pytest.raises(publication.PublicationV2Error):
        publication.write_network_observation(
            document=observation,
            output_path=(f"{publication.CANONICAL_NETWORK_ROOT}/20260729T000000Z.json"),
            workspace_root=root,
        )
    assert not (root / publication.CANONICAL_OUTPUT_ROOT).exists()


def test_network_observation_must_match_pinned_url_contract(
    publication_workspace: tuple[Path, dict[str, Any]],
) -> None:
    root, document = publication_workspace
    observation = _fake_network_observation(root, document)
    observation["observations"][0]["expected_status"] = 201
    observation["observations"][0]["matches_expected"] = False
    with pytest.raises(
        publication.PublicationV2Error,
        match="network_contract_record_0",
    ):
        publication.write_network_observation(
            document=observation,
            output_path=f"{publication.CANONICAL_NETWORK_ROOT}/bound.json",
            workspace_root=root,
        )
    assert not (root / publication.CANONICAL_OUTPUT_ROOT).exists()


def test_valid_network_observation_is_written_once_and_read_back(
    publication_workspace: tuple[Path, dict[str, Any]],
) -> None:
    root, document = publication_workspace
    observation = _fake_network_observation(root, document)
    output = f"{publication.CANONICAL_NETWORK_ROOT}/20260729T000000Z.json"
    assert (
        publication.write_network_observation(
            document=observation,
            output_path=output,
            workspace_root=root,
        )
        == output
    )
    written = json.loads((root / output).read_text(encoding="utf-8"))
    assert written == observation
    assert written["canonical_completion_evidence"] is False
    assert written["completion_state"] == publication.COMPLETION_STATE


def test_network_observation_binding_and_output_denylist_are_fail_closed(
    publication_workspace: tuple[Path, dict[str, Any]],
) -> None:
    root, document = publication_workspace
    observation = _fake_network_observation(root, document)
    forged = deepcopy(observation)
    forged["config"]["sha256"] = "0" * 64
    with pytest.raises(publication.PublicationV2Error, match="network_config_binding"):
        publication.write_network_observation(
            document=forged,
            output_path=(f"{publication.CANONICAL_NETWORK_ROOT}/20260729T000000Z.json"),
            workspace_root=root,
        )
    with pytest.raises(
        publication.PublicationV2Error,
        match="network_output_path_forbidden",
    ):
        publication.write_network_observation(
            document=observation,
            output_path=f"{publication.CANONICAL_NETWORK_ROOT}/.env-result.json",
            workspace_root=root,
        )
    assert not (root / publication.CANONICAL_OUTPUT_ROOT).exists()


def test_cli_config_publish_and_deep_modes(
    publication_workspace: tuple[Path, dict[str, Any]],
) -> None:
    root, _ = publication_workspace
    environment = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(REPOSITORY_ROOT / "src"),
    }
    base = [
        sys.executable,
        str(SCRIPT),
        "--workspace-root",
        str(root),
    ]
    config_only = subprocess.run(
        [*base, "--config-only"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert config_only.returncode == 0, config_only.stderr
    assert json.loads(config_only.stdout)["status"] == "valid_config_only"
    assert not (root / publication.CANONICAL_OUTPUT_ROOT).exists()
    published = subprocess.run(
        base,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert published.returncode == 0, published.stderr
    deep = subprocess.run(
        [*base, "--validate-receipt-only"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert deep.returncode == 0, deep.stderr
    assert (
        json.loads(published.stdout)["generation_id"]
        == json.loads(deep.stdout)["generation_id"]
    )
