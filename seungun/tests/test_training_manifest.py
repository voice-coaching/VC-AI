import json
from pathlib import Path

import pytest
import yaml

from korean_phone_ctc.data.training_manifest import load_training_and_dev_records


def _write_jsonl(path: Path, records: list[dict[str, str]]) -> None:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def test_loader_opens_only_training_and_reviewed_dev_manifests(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "train.jsonl",
        [
            {"split": "train", "source_split": "train"},
            {"split": "train", "source_split": "stack"},
        ],
    )
    _write_jsonl(
        tmp_path / "dev.jsonl",
        [{"split": "dev", "source_split": "dev"}],
    )
    (tmp_path / "vocab.json").write_text(
        '{"<pad>": 0, "<unk>": 1}\n',
        encoding="utf-8",
    )
    (tmp_path / "test.jsonl").write_text("not-json-and-must-not-be-opened\n")

    bundle = load_training_and_dev_records(
        tmp_path,
        workspace_root=tmp_path,
        train_limit=None,
        dev_limit=None,
    )

    assert len(bundle.train) == 2
    assert len(bundle.dev) == 1
    assert bundle.vocabulary == {"<pad>": 0, "<unk>": 1}


def test_loader_rejects_locked_test_provenance_in_dev(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "train.jsonl",
        [{"split": "train", "source_split": "train"}],
    )
    _write_jsonl(
        tmp_path / "dev.jsonl",
        [{"split": "dev", "source_split": "locked_test"}],
    )

    with pytest.raises(ValueError, match="unreviewed source split"):
        load_training_and_dev_records(
            tmp_path,
            workspace_root=tmp_path,
            train_limit=None,
            dev_limit=None,
        )


def test_loader_rejects_locked_test_provenance_in_training(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "train.jsonl",
        [{"split": "train", "source_split": "locked_test"}],
    )
    _write_jsonl(
        tmp_path / "dev.jsonl",
        [{"split": "dev", "source_split": "dev"}],
    )

    with pytest.raises(ValueError, match="unreviewed source split"):
        load_training_and_dev_records(
            tmp_path,
            workspace_root=tmp_path,
            train_limit=None,
            dev_limit=None,
        )


def test_loader_rejects_dev_symlink_to_locked_test(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "train.jsonl",
        [{"split": "train", "source_split": "train"}],
    )
    _write_jsonl(
        tmp_path / "test.jsonl",
        [{"split": "test", "source_split": "locked_test"}],
    )
    (tmp_path / "dev.jsonl").symlink_to("test.jsonl")

    with pytest.raises(ValueError, match="cannot safely open reviewed manifest"):
        load_training_and_dev_records(
            tmp_path,
            workspace_root=tmp_path,
            train_limit=None,
            dev_limit=None,
        )


def test_loader_rejects_train_manifest_symlink(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "test.jsonl",
        [{"split": "test", "source_split": "locked_test"}],
    )
    _write_jsonl(
        tmp_path / "dev.jsonl",
        [{"split": "dev", "source_split": "dev"}],
    )
    (tmp_path / "train.jsonl").symlink_to("test.jsonl")

    with pytest.raises(ValueError, match="cannot safely open reviewed manifest"):
        load_training_and_dev_records(
            tmp_path,
            workspace_root=tmp_path,
            train_limit=None,
            dev_limit=None,
        )


def test_loader_rejects_manifest_directory_symlink(tmp_path: Path) -> None:
    real_manifest = tmp_path / "real_manifest"
    real_manifest.mkdir()
    _write_jsonl(
        real_manifest / "train.jsonl",
        [{"split": "train", "source_split": "train"}],
    )
    _write_jsonl(
        real_manifest / "dev.jsonl",
        [{"split": "dev", "source_split": "dev"}],
    )
    linked_manifest = tmp_path / "linked_manifest"
    linked_manifest.symlink_to(real_manifest, target_is_directory=True)

    with pytest.raises(ValueError, match="cannot safely open manifest directory"):
        load_training_and_dev_records(
            linked_manifest,
            workspace_root=tmp_path,
            train_limit=None,
            dev_limit=None,
        )


def test_loader_rejects_symlink_in_manifest_parent_path(tmp_path: Path) -> None:
    real_parent = tmp_path / "real_parent"
    manifest = real_parent / "manifest"
    manifest.mkdir(parents=True)
    _write_jsonl(
        manifest / "train.jsonl",
        [{"split": "train", "source_split": "train"}],
    )
    _write_jsonl(
        manifest / "dev.jsonl",
        [{"split": "dev", "source_split": "dev"}],
    )
    linked_parent = tmp_path / "linked_parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(ValueError, match="cannot safely open manifest directory"):
        load_training_and_dev_records(
            linked_parent / "manifest",
            workspace_root=tmp_path,
            train_limit=None,
            dev_limit=None,
        )


def test_loader_validates_provenance_after_smoke_limit(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "train.jsonl",
        [
            {"split": "train", "source_split": "train"},
            {"split": "train", "source_split": "locked_test"},
        ],
    )
    _write_jsonl(
        tmp_path / "dev.jsonl",
        [{"split": "dev", "source_split": "dev"}],
    )

    with pytest.raises(ValueError, match="unreviewed source split"):
        load_training_and_dev_records(
            tmp_path,
            workspace_root=tmp_path,
            train_limit=1,
            dev_limit=1,
        )


def test_loader_rejects_manifest_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_manifest = tmp_path / "outside_manifest"
    outside_manifest.mkdir()

    with pytest.raises(ValueError, match="outside workspace root"):
        load_training_and_dev_records(
            outside_manifest,
            workspace_root=workspace,
            train_limit=None,
            dev_limit=None,
        )


def test_training_and_target_policy_gates_remain_closed() -> None:
    project_root = Path(__file__).parents[1]
    target_policy = yaml.safe_load(
        (project_root / "configs/textgrid_target_policy.yaml").read_text(
            encoding="utf-8"
        )
    )
    smoke_config = yaml.safe_load(
        (project_root / "configs/xlsr300m_smoke.yaml").read_text(encoding="utf-8")
    )
    full_config = yaml.safe_load(
        (project_root / "configs/xlsr300m_full.yaml").read_text(encoding="utf-8")
    )

    assert target_policy["training_allowed"] is False
    assert target_policy["manual_alignment_gate"]["status"] == "pending"
    assert smoke_config["gate"]["full_training_allowed"] is False
    assert smoke_config["gate"]["locked_test_access_allowed"] is False
    assert full_config["enabled"] is False
    assert full_config["locked_test_access_allowed"] is False
