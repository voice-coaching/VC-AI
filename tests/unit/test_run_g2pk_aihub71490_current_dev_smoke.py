from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
from types import ModuleType
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from voice_coach.phonology.g2pk_expected import (
    EXPECTED_G2PK_VERSION,
    G2PKExpectedPhones,
    generate_g2pk_expected_phones,
)


SCRIPT = (
    Path(__file__).parents[2] / "scripts" / "run_g2pk_aihub71490_current_dev_smoke.py"
)
SPEC = importlib.util.spec_from_file_location(
    "run_g2pk_aihub71490_current_dev_smoke",
    SCRIPT,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class _FakeBackend:
    def __call__(
        self,
        text: str,
        *,
        descriptive: bool,
        verbose: bool,
        group_vowels: bool,
        to_syl: bool,
    ) -> str:
        del text, descriptive, verbose, group_vowels, to_syl
        return "가"


def _fake_generator(
    text: object,
    *,
    descriptive: bool,
    group_vowels: bool,
    word_boundary_policy: str,
    allowed_punctuation: tuple[str, ...],
    maximum_codepoints: int,
    nltk_data_root: str | Path | None = None,
) -> G2PKExpectedPhones:
    del nltk_data_root
    return generate_g2pk_expected_phones(
        text,
        descriptive=descriptive,
        group_vowels=group_vowels,
        word_boundary_policy=word_boundary_policy,  # type: ignore[arg-type]
        allowed_punctuation=allowed_punctuation,
        maximum_codepoints=maximum_codepoints,
        backend=_FakeBackend(),
        backend_version=EXPECTED_G2PK_VERSION,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assignment_digest(rows: list[dict[str, object]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        MODULE._update_framed(
            digest,
            str(row["id"]).encode("utf-8"),
        )
        MODULE._update_framed(
            digest,
            str(row["split"]).encode("ascii"),
        )
    return digest.hexdigest()


def _write_fixture(
    tmp_path: Path,
) -> tuple[ModuleType, Any, list[dict[str, object]]]:
    dataset_root = (tmp_path / "dataset").resolve()
    dataset_root.mkdir()
    rows: list[dict[str, object]] = []
    for index in range(16):
        transcript = f"비공개 합성 문장 {index}"
        relative_path = f"dev/private-label-{index:02d}.json"
        raw_path = dataset_root / relative_path
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(
            json.dumps(
                {
                    "RecordingMetadata": {
                        "prompt": transcript,
                        "phonemic": {
                            "phones": [{"phone": "ㄱ"}, {"phone": "ㅏ"}],
                        },
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        rows.append(
            {
                "schema_version": MODULE.CURRENT_MANIFEST_SCHEMA_VERSION,
                "id": f"private-dev-row-{index:02d}",
                "split": "dev",
                "json_path": relative_path,
                "prompt_sha256": hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
            }
        )
    for index, split in enumerate(
        ("train", "stack", "calibration", "locked_test"),
    ):
        rows.append(
            {
                "schema_version": MODULE.CURRENT_MANIFEST_SCHEMA_VERSION,
                "id": f"private-non-dev-row-{index:02d}",
                "split": split,
                "json_path": f"missing/{split}-{index:02d}.json",
                "prompt_sha256": hashlib.sha256(
                    f"never opened {index}".encode("utf-8")
                ).hexdigest(),
            }
        )
    modeling_manifest = (tmp_path / "modeling.parquet").resolve()
    pq.write_table(
        pa.Table.from_pylist(rows),
        modeling_manifest,
        compression="NONE",
    )
    modeling_sha256 = _sha256(modeling_manifest)
    split_audit = (tmp_path / "split-audit.json").resolve()
    split_audit.write_text(
        json.dumps(
            {
                "schema_version": MODULE.SPLIT_AUDIT_SCHEMA_VERSION,
                "status": "split_currency_audit_complete",
                "provenance": {
                    "input_sha256": {
                        "current_manifest": modeling_sha256,
                    },
                    "current_split_assignment_sha256": _assignment_digest(rows),
                },
                "join_audit": {
                    "structural_error_count": 0,
                },
                "legacy_evidence_disposition": {
                    "disposition": "superseded_current_split_drift",
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    nltk_data_root = (tmp_path / "nltk_data").resolve()
    nltk_data_root.mkdir()
    output = (tmp_path / "current-dev.json").resolve()
    configuration = MODULE.CurrentDevSmokeConfiguration(
        modeling_manifest_path=modeling_manifest,
        expected_modeling_manifest_sha256=modeling_sha256,
        split_audit_path=split_audit,
        expected_split_audit_sha256=_sha256(split_audit),
        expected_runner_sha256=_sha256(MODULE._IMPLEMENTATION_PATHS["runner"]),
        expected_agreement_source_sha256=_sha256(
            MODULE._IMPLEMENTATION_PATHS["agreement_source"]
        ),
        expected_g2pk_adapter_source_sha256=_sha256(
            MODULE._IMPLEMENTATION_PATHS["g2pk_adapter_source"]
        ),
        expected_phone_inventory_source_sha256=_sha256(
            MODULE._IMPLEMENTATION_PATHS["phone_inventory_source"]
        ),
        expected_modeling_manifest_builder_sha256=_sha256(
            MODULE._IMPLEMENTATION_PATHS["modeling_manifest_builder"]
        ),
        expected_g2pk_environment_lock_sha256=_sha256(
            MODULE._IMPLEMENTATION_PATHS["g2pk_environment_lock"]
        ),
        expected_scorer_environment_lock_sha256=_sha256(
            MODULE._IMPLEMENTATION_PATHS["scorer_environment_lock"]
        ),
        dataset_root=dataset_root,
        nltk_data_root=nltk_data_root,
        output_path=output,
        seed=MODULE.FIXED_SEED,
        sample_size=MODULE.MAXIMUM_SAMPLE_SIZE,
        descriptive=False,
        group_vowels=False,
        punctuation_policy=MODULE.PUNCTUATION_POLICY_ID,
        word_boundary_policy="drop",
        maximum_codepoints=100,
        max_alignment_cells=100,
    )
    return MODULE, configuration, rows


def test_current_dev_sample_opens_only_selected_dev_expected_json(
    tmp_path: Path,
) -> None:
    module, configuration, rows = _write_fixture(tmp_path)

    report = module.run_current_dev_smoke(
        configuration,
        generator=_fake_generator,
    )

    assert report["status"] == "passed_current_project_dev_diagnostic"
    metadata = report["metadata_audit"]
    assert metadata["manifest_row_count"] == 20
    assert metadata["dev_population_row_count"] == 16
    assert metadata["non_dev_row_skipped_without_raw_source_access_count"] == 4
    selected = report["selected_expected_label_audit"]
    assert selected["expected_label_json_access_attempt_count"] == 16
    assert selected["expected_label_json_access_completed_count"] == 16
    assert selected["access_count_by_current_split"] == {
        "calibration": 0,
        "dev": 16,
        "locked_test": 0,
        "stack": 0,
        "train": 0,
    }
    smoke = report["smoke"]
    assert smoke["comparison_completed_count"] == 16
    assert smoke["exact_match_count"] == 16
    access = report["runtime_access_audit"]
    assert access["expected_label_json_non_dev_access_count"] == 0
    assert access["audio_file_access_count"] == 0
    assert access["textgrid_file_access_count"] == 0
    assert access["network_socket_attempt_count"] == 0

    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    for row in rows:
        assert str(row["id"]) not in serialized
        assert str(row["json_path"]) not in serialized
    assert "비공개 합성 문장" not in serialized
    assert '"ㄱ"' not in serialized
    assert '"ㅏ"' not in serialized


def test_socket_operations_are_fail_closed_without_replacing_socket_type() -> None:
    module = MODULE

    with module._network_socket_block() as attempts:
        assert isinstance(socket.socket(), socket.socket)
        try:
            socket.create_connection(("example.invalid", 443))
        except OSError as error:
            assert str(error) == "network socket access is blocked"

    assert sum(attempts.values()) == 1


def test_atomic_writer_uses_private_mode_and_round_trips(
    tmp_path: Path,
) -> None:
    module, configuration, _ = _write_fixture(tmp_path)
    report = module.run_current_dev_smoke(
        configuration,
        generator=_fake_generator,
    )

    module._write_json_atomic(configuration.output_path, report)

    assert json.loads(configuration.output_path.read_text(encoding="utf-8")) == report
    assert os.stat(configuration.output_path).st_mode & 0o777 == 0o600
