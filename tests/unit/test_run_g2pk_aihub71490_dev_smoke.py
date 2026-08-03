from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
from dataclasses import replace
from types import ModuleType
from typing import Any

import pytest

from voice_coach.phonology.g2pk_expected import (
    EXPECTED_G2PK_VERSION,
    G2PKExpectedPhones,
    generate_g2pk_expected_phones,
)


SCRIPT = Path(__file__).parents[2] / "scripts" / "run_g2pk_aihub71490_dev_smoke.py"
SPEC = importlib.util.spec_from_file_location(
    "run_g2pk_aihub71490_dev_smoke",
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


def _raw_payload(
    *,
    transcript: str = "합성 입력",
    phones: tuple[str, ...] = ("ㄱ", "ㅏ"),
) -> dict[str, object]:
    return {
        "RecordingMetadata": {
            "prompt": transcript,
            "phonemic": {
                "phones": [{"phone": phone} for phone in phones],
            },
        }
    }


def _manifest_row(
    row_id: str,
    relative_path: str,
    *,
    split: str = "dev",
    phone_count: int = 2,
) -> dict[str, object]:
    return {
        "schema_version": MODULE.EXPECTED_MANIFEST_SCHEMA_VERSION,
        "dataset_id": MODULE.EXPECTED_DATASET_ID,
        "id": row_id,
        "speaker_id": hashlib.sha256(f"speaker-{row_id}".encode()).hexdigest(),
        "split": split,
        "source_paths": {"json": relative_path},
        "phone_sources": {
            "target": {
                "path": relative_path,
                "format": "aihub_pronunciation_json",
                "json_pointer": "/RecordingMetadata/phonemic/phones",
            }
        },
        "annotation_counts": {"target_phones": phone_count},
    }


def _write_fixture(
    tmp_path: Path,
    *,
    dev_payloads: list[dict[str, object]],
    include_unopened_non_dev: bool = True,
) -> tuple[ModuleType, Any]:
    dataset_root = (tmp_path / "dataset").resolve()
    dataset_root.mkdir()
    rows: list[dict[str, object]] = []
    for index, payload in enumerate(dev_payloads):
        relative_path = f"dev/source-{index}.json"
        raw_path = dataset_root / relative_path
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        phone_entries = payload["RecordingMetadata"]["phonemic"]["phones"]  # type: ignore[index]
        rows.append(
            _manifest_row(
                f"row-{index}",
                relative_path,
                phone_count=len(phone_entries),
            )
        )
    if include_unopened_non_dev:
        rows.append(
            _manifest_row(
                "out-of-scope",
                "missing/source.json",
                split="outside_scope",
            )
        )

    manifest = (tmp_path / "manifest.jsonl").resolve()
    manifest.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        ),
        encoding="utf-8",
    )
    data_config = (tmp_path / "data.yaml").resolve()
    data_config.write_text("split: dev\nseed: 42\n", encoding="utf-8")
    nltk_data_root = (tmp_path / "nltk_data").resolve()
    nltk_data_root.mkdir()
    output = (tmp_path / "aggregate.json").resolve()
    configuration = MODULE.SmokeConfiguration(
        manifest_path=manifest,
        expected_manifest_sha256=_sha256(manifest),
        data_config_path=data_config,
        expected_data_config_sha256=_sha256(data_config),
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
        expected_g2pk_environment_lock_sha256=_sha256(
            MODULE._IMPLEMENTATION_PATHS["g2pk_environment_lock"]
        ),
        dataset_root=dataset_root,
        nltk_data_root=nltk_data_root,
        output_path=output,
        seed=42,
        sample_size=len(dev_payloads),
        descriptive=False,
        group_vowels=False,
        punctuation_policy=MODULE.PUNCTUATION_POLICY_ID,
        word_boundary_policy="drop",
        maximum_codepoints=100,
        max_alignment_cells=100,
    )
    return MODULE, configuration


def test_complete_dev_audit_skips_non_dev_sources_and_emits_only_aggregates(
    tmp_path: Path,
) -> None:
    module, configuration = _write_fixture(
        tmp_path,
        dev_payloads=[_raw_payload(), _raw_payload(transcript="다른 합성 입력")],
    )

    report = module.run_dev_smoke(
        configuration,
        generator=_fake_generator,
    )

    assert report["status"] == "passed_development_diagnostic"
    assert report["dev_audit"]["manifest_row_count"] == 3
    assert report["dev_audit"]["non_dev_row_skipped_without_source_access_count"] == 1
    assert report["dev_audit"]["dev_row_count"] == 2
    assert report["smoke"]["sample_fixed_before_eligibility_count"] == 2
    assert report["smoke"]["sample_policy_rejected_after_selection_count"] == 0
    assert report["smoke"]["g2pk_attempted_count"] == 2
    assert report["smoke"]["comparison_completed_count"] == 2
    assert report["failure_reason_counts"] == {}
    assert report["runtime"]["network_socket_attempt_count"] == 0
    assert report["provenance"]["implementation_verified_at_start"] is True
    assert report["provenance"]["implementation_unchanged_at_end"] is True
    assert set(report["provenance"]["implementation_sha256"]) == {
        "agreement_source",
        "g2pk_adapter_source",
        "g2pk_environment_lock",
        "phone_inventory_source",
        "runner",
    }

    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        "합성 입력",
        "다른 합성 입력",
        "row-0",
        "row-1",
        "source-0",
        "source-1",
        "speaker-row",
    ):
        assert forbidden not in serialized


def test_sample_is_fixed_from_all_dev_rows_before_noncanonical_rejection(
    tmp_path: Path,
) -> None:
    module, configuration = _write_fixture(
        tmp_path,
        dev_payloads=[
            _raw_payload(),
            _raw_payload(phones=("sil",)),
            _raw_payload(transcript="세 번째 합성 입력"),
        ],
    )

    report = module.run_dev_smoke(
        configuration,
        generator=_fake_generator,
    )

    assert report["policies"]["sample_population"] == (
        "all_dev_rows_before_annotation_eligibility"
    )
    assert report["dev_audit"]["dev_noncanonical_phone_occurrence_count"] == 1
    assert report["dev_audit"]["dev_row_with_noncanonical_phone_count"] == 1
    assert report["dev_audit"]["dev_exact_sil_occurrence_count"] == 1
    assert report["dev_audit"]["dev_primary_rejection_reason_counts"] == {
        "noncanonical_annotation_token": 1
    }
    assert report["dev_audit"]["dev_rejection_condition_counts"] == {
        "noncanonical_annotation_token": 1
    }
    assert report["smoke"]["sample_fixed_before_eligibility_count"] == 3
    assert report["smoke"]["sample_policy_rejected_after_selection_count"] == 1
    assert report["smoke"]["g2pk_attempted_count"] == 2
    assert report["smoke"]["post_attempt_failure_count"] == 0
    assert report["smoke"]["comparison_completed_count"] == 2
    assert report["smoke"]["sample_rejected_after_selection_count"] == 1
    assert report["failure_reason_counts"] == {"noncanonical_annotation_token": 1}
    assert report["status"] == "incomplete_development_diagnostic"


def test_fixed_punctuation_policy_rejects_after_selection_without_echoing_text(
    tmp_path: Path,
) -> None:
    module, configuration = _write_fixture(
        tmp_path,
        dev_payloads=[_raw_payload(transcript="합성 입력…")],
    )

    report = module.run_dev_smoke(
        configuration,
        generator=_fake_generator,
    )

    assert report["dev_audit"]["dev_transcript_outside_punctuation_policy_count"] == 1
    assert report["smoke"]["sample_policy_rejected_after_selection_count"] == 1
    assert report["smoke"]["g2pk_attempted_count"] == 0
    assert report["smoke"]["comparison_completed_count"] == 0
    assert report["failure_reason_counts"] == {"punctuation_outside_fixed_policy": 1}
    assert "합성 입력" not in json.dumps(report, ensure_ascii=False)


def test_phone_inventory_is_counted_even_when_transcript_contract_rejects_row(
    tmp_path: Path,
) -> None:
    module, configuration = _write_fixture(
        tmp_path,
        dev_payloads=[
            _raw_payload(
                transcript="synthetic\ncontrol",
                phones=("sil",),
            )
        ],
    )

    report = module.run_dev_smoke(
        configuration,
        generator=_fake_generator,
    )

    assert report["dev_audit"]["dev_annotation_phone_occurrence_count"] == 1
    assert report["dev_audit"]["dev_noncanonical_phone_occurrence_count"] == 1
    assert report["dev_audit"]["dev_row_with_noncanonical_phone_count"] == 1
    assert report["dev_audit"]["dev_primary_rejection_reason_counts"] == {
        "transcript_contract": 1
    }
    assert report["dev_audit"]["dev_rejection_condition_counts"] == {
        "noncanonical_annotation_token": 1,
        "transcript_contract": 1,
    }
    assert report["smoke"]["sample_policy_rejected_after_selection_count"] == 1
    assert report["smoke"]["g2pk_attempted_count"] == 0


def test_socket_connect_attempt_is_blocked_and_counted(
    tmp_path: Path,
) -> None:
    module, configuration = _write_fixture(
        tmp_path,
        dev_payloads=[_raw_payload()],
    )

    def connecting_generator(*args: object, **kwargs: object) -> G2PKExpectedPhones:
        del args, kwargs
        socket.create_connection(("example.invalid", 443))
        raise AssertionError("unreachable")

    report = module.run_dev_smoke(
        configuration,
        generator=connecting_generator,
    )

    assert report["runtime"]["network_socket_connect_blocked"] is True
    assert report["runtime"]["network_socket_attempt_count"] == 1
    assert report["smoke"]["g2pk_attempted_count"] == 1
    assert report["smoke"]["post_attempt_failure_count"] == 1
    assert report["smoke"]["comparison_completed_count"] == 0
    assert report["failure_reason_counts"] == {"unexpected_bounded_smoke_error": 1}


def test_provenance_digest_mismatch_fails_without_source_detail(
    tmp_path: Path,
) -> None:
    module, configuration = _write_fixture(
        tmp_path,
        dev_payloads=[_raw_payload()],
    )
    invalid = replace(
        configuration,
        expected_manifest_sha256="0" * 64,
    )

    with pytest.raises(module.DevSmokeError, match="manifest digest differs"):
        module.run_dev_smoke(invalid, generator=_fake_generator)


def test_implementation_digest_mismatch_fails_before_dev_audit(
    tmp_path: Path,
) -> None:
    module, configuration = _write_fixture(
        tmp_path,
        dev_payloads=[_raw_payload()],
    )
    invalid = replace(
        configuration,
        expected_runner_sha256="0" * 64,
    )

    with pytest.raises(module.DevSmokeError, match="implementation"):
        module.run_dev_smoke(invalid, generator=_fake_generator)


def test_atomic_writer_uses_private_mode_and_round_trips_report(
    tmp_path: Path,
) -> None:
    module, configuration = _write_fixture(
        tmp_path,
        dev_payloads=[_raw_payload()],
    )
    report = module.run_dev_smoke(configuration, generator=_fake_generator)

    module._write_json_atomic(configuration.output_path, report)

    assert json.loads(configuration.output_path.read_text(encoding="utf-8")) == report
    assert stat_mode(configuration.output_path) == 0o600


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777
