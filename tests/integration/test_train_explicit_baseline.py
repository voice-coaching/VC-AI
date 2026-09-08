from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import pickle

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml

from voice_coach.experiments import registry as registry_module


SCRIPT = (
    Path(__file__).parents[2] / "scripts" / "train_explicit_baseline.py"
)
CONFIG = (
    Path(__file__).parents[2]
    / "configs"
    / "experiments"
    / "explicit_baseline_v1.yaml"
)
SPEC = importlib.util.spec_from_file_location("train_explicit_baseline", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _metadata(manifest_sha: str, *, phone: bool) -> dict[bytes, bytes]:
    inputs = (
        MODULE.PHONE_CATEGORICAL_FEATURES + MODULE.PHONE_NUMERIC_FEATURES
        if phone
        else MODULE.UTTERANCE_NUMERIC_FEATURES
    )
    labels = ["binary_error_label"] if phone else MODULE.UTTERANCE_OUTCOMES
    return {
        b"voice_coach.schema_version": b"synthetic.v1",
        b"voice_coach.oracle_alignment": b"true",
        b"voice_coach.privacy_safe": b"true",
        b"voice_coach.source_manifest_sha256": manifest_sha.encode(),
        b"voice_coach.allowed_splits": b"train,dev",
        b"voice_coach.metric_evaluation_performed": b"false",
        b"voice_coach.input_feature_columns": json.dumps(list(inputs)).encode(),
        b"voice_coach.label_columns": json.dumps(list(labels)).encode(),
        b"voice_coach.slice_only_columns": b"[]",
        b"voice_coach.forbidden_as_input": json.dumps(
            [
                "binary_error_label",
                "pronunciation_score",
                "fluency_score",
                "comprehensibility_score",
                "realized_label",
                "alignment_operation",
                "error_tags",
            ]
        ).encode(),
    }


def _utterance_row(index: int, split: str) -> dict:
    score = 1 + (index % 5)
    return {
        "oracle_alignment": True,
        "utterance_id_sha256": hashlib.sha256(
            f"utt-{split}-{index}".encode()
        ).hexdigest(),
        "speaker_id_sha256": hashlib.sha256(
            f"speaker-{split}-{index // 2}".encode()
        ).hexdigest(),
        "split": split,
        "l1": "Chinese" if index % 2 == 0 else "Japanese",
        "gender": "F" if index % 3 else "M",
        "proficiency": "Beginner" if score < 3 else "Intermediate",
        "audio_duration_seconds": 2.0 + index * 0.2,
        "rms": 0.01 * score,
        "peak": 0.1 * score,
        "clipping_ratio": 0.0,
        "zero_crossing_rate": 0.02 * score,
        "pause_count": index % 3,
        "pause_ratio": 0.03 * (index % 3),
        "voiced_ratio": 0.5 + 0.05 * score,
        "f0_median_hz": None if index == 0 else 100.0 + 10 * score,
        "f0_range_hz": None if index == 0 else 20.0 + score,
        "word_count": 3 + index % 4,
        "target_phone_count": 8 + index,
        "speech_rate_phones_per_second": 3.0 + 0.2 * score,
        "articulation_rate_phones_per_second": 4.0 + 0.2 * score,
        "target_phone_label_duration_npvi": 5.0 * (index % 4),
        "pronunciation_score": score,
        "fluency_score": None if split == "dev" and index == 0 else score,
        "comprehensibility_score": 1 + ((index + 1) % 5),
    }


def _phone_rows(utterance: dict, index: int) -> list[dict]:
    rows = []
    for phone_index in range(3):
        target = ["ㄱ", "ㅏ", "ㄴ"][(index + phone_index) % 3]
        rows.append(
            {
                "oracle_alignment": True,
                "utterance_id_sha256": utterance["utterance_id_sha256"],
                "speaker_id_sha256": utterance["speaker_id_sha256"],
                "split": utterance["split"],
                "phone_index": phone_index,
                "target_label": target,
                "previous_target_label": None if phone_index == 0 else "ㄱ",
                "next_target_label": None if phone_index == 2 else "ㅏ",
                "oracle_duration_seconds": 0.07 + 0.03 * phone_index,
                "segment_rms": 0.02 + 0.04 * ((index + phone_index) % 2),
                "segment_zero_crossing_rate": 0.01 * (phone_index + 1),
                "binary_error_label": (index + phone_index) % 2,
                "l1": utterance["l1"],
                "gender": utterance["gender"],
                "proficiency": utterance["proficiency"],
            }
        )
    return rows


def _fixture(tmp_path: Path) -> argparse.Namespace:
    manifest_sha = "a" * 64
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config["data"]["modeling_manifest_sha256"] = manifest_sha
    config_path = tmp_path / "explicit_baseline.yaml"
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    utterance_rows = [
        *[_utterance_row(index, "train") for index in range(12)],
        *[_utterance_row(index, "dev") for index in range(8)],
    ]
    # Keep one all-dev audit row outside the predeclared F/M primary scope.
    utterance_rows[-1]["gender"] = "O"
    utterance_cache = tmp_path / "utterances.parquet"
    utterance_table = pa.Table.from_pylist(utterance_rows).replace_schema_metadata(
        _metadata(manifest_sha, phone=False)
    )
    pq.write_table(utterance_table, utterance_cache)

    phone_rows = [
        phone
        for index, utterance in enumerate(utterance_rows)
        for phone in _phone_rows(utterance, index)
    ]
    phone_cache = tmp_path / "phones"
    phone_cache.mkdir()
    phone_path = phone_cache / "part-00000.parquet"
    phone_table = pa.Table.from_pylist(phone_rows).replace_schema_metadata(
        _metadata(manifest_sha, phone=True)
    )
    pq.write_table(phone_table, phone_path)

    cache_report = tmp_path / "cache_report.json"
    cache_report.write_text(
        json.dumps(
            {
                "status": "completed",
                "source": {
                    "modeling_manifest_sha256": manifest_sha,
                    "prompt_reservation_sha256": "b" * 64,
                },
                "policy": {
                    "allowed_splits": ["train", "dev"],
                    "metric_evaluation_performed": False,
                    "rows_with_qc_flags_excluded": True,
                    "reserved_prompts_excluded_from_every_split": True,
                    "privacy_safe": True,
                    "oracle_alignment": True,
                    "speaker_id_sha256_is_pseudonymous_hash_not_raw_user_id": True,
                    "utterance_id_sha256_is_namespaced_hash_not_raw_file_stem": True,
                    "raw_file_stem_contains_user_id_and_is_never_exported": True,
                    "column_roles": {
                        "utterance": {
                            "input_feature_columns": list(
                                MODULE.UTTERANCE_NUMERIC_FEATURES
                            ),
                            "label_columns": list(MODULE.UTTERANCE_OUTCOMES),
                            "slice_only_columns": [
                                "split",
                                "l1",
                                "gender",
                                "proficiency",
                            ],
                        },
                        "phone": {
                            "input_feature_columns": list(
                                MODULE.PHONE_CATEGORICAL_FEATURES
                                + MODULE.PHONE_NUMERIC_FEATURES
                            ),
                            "label_columns": ["binary_error_label"],
                            "slice_only_columns": [
                                "split",
                                "l1",
                                "gender",
                                "proficiency",
                            ],
                        },
                    },
                },
                "configuration": {"limit": None},
                "progress": {"source_scan_complete": True},
                "outputs": {
                    "utterance": {
                        "row_count": len(utterance_rows),
                        "sha256": _sha256(utterance_cache),
                    },
                    "phone": {
                        "enabled": True,
                        "row_count": len(phone_rows),
                        "shards": [
                            {
                                "path": phone_path.name,
                                "row_count": len(phone_rows),
                                "sha256": _sha256(phone_path),
                            }
                        ],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return argparse.Namespace(
        config=config_path,
        utterance_cache=utterance_cache,
        phone_cache=phone_cache,
        cache_report=cache_report,
        output_dir=tmp_path / "experiment",
    )


def test_tiny_train_dev_cache_produces_complete_atomic_experiment(
    tmp_path: Path,
) -> None:
    args = _fixture(tmp_path)

    report = MODULE.train_explicit_baseline(args)

    assert report["status"] == "completed"
    assert report["summary"]["seeds_completed"] == [42, 43, 44]
    assert report["summary"]["locked_test_remains_unread"] is True
    assert report["data"]["utterance_rows"]["dev_primary"] == 7
    assert report["data"]["utterance_rows"]["dev_all_audit"] == 8
    assert report["experiment_registry"]["integrated"] is False
    assert report["experiment_registry"]["t03_complete"] is False
    assert report["leakage_controls"]["gold_realized_phone_loaded"] is False
    assert report["cache_contract"]["reserved_prompts_excluded"] is True
    assert len(report["runs"]) == 3
    for run in report["runs"]:
        assert 0.0 <= run["phone"]["metrics"]["macro_f1"] <= 1.0
        assert run["phone"]["selected_c"] in [0.1, 1.0, 10.0]
        assert set(run["utterance"]) == set(MODULE.UTTERANCE_OUTCOMES)
        seed_dir = args.output_dir / f"seed_{run['seed']}"
        assert (seed_dir / "checkpoint.pkl").is_file()
        assert (seed_dir / "phone_dev_predictions.parquet").is_file()
        assert (seed_dir / "utterance_dev_predictions.parquet").is_file()
        assert (seed_dir / "slice_metrics.json").is_file()
        phone_predictions = pq.read_table(
            seed_dir / "phone_dev_predictions.parquet"
        )
        assert phone_predictions.num_rows == 24
        assert "utterance_id" not in phone_predictions.column_names
        assert "utterance_id_sha256" in phone_predictions.column_names
        assert all(
            len(value) == 64
            for value in phone_predictions.column(
                "utterance_id_sha256"
            ).to_pylist()
        )
        with (seed_dir / "checkpoint.pkl").open("rb") as handle:
            checkpoint = pickle.load(handle)
        phone_features = set(checkpoint["phone"]["features"])
        assert not (phone_features & MODULE.PROHIBITED_PHONE_INPUTS)
        assert run["phone"]["metric_scope"] == "primary_dev"
        assert "all_dev_audit_metrics" in run["phone"]
    assert (args.output_dir / "report.json").is_file()
    assert not list(tmp_path.glob(".experiment.part-*"))


def test_forbidden_split_contract_is_rejected_without_partial_output(
    tmp_path: Path,
) -> None:
    args = _fixture(tmp_path)
    report = json.loads(args.cache_report.read_text(encoding="utf-8"))
    report["policy"]["allowed_splits"].append("locked_test")
    args.cache_report.write_text(json.dumps(report), encoding="utf-8")
    args.output_dir = tmp_path / "must-not-exist"

    with pytest.raises(ValueError, match="exactly train and dev"):
        MODULE.train_explicit_baseline(args)

    assert not args.output_dir.exists()


def test_cache_policy_must_attest_qc_and_prompt_filtering(
    tmp_path: Path,
) -> None:
    args = _fixture(tmp_path)
    report = json.loads(args.cache_report.read_text(encoding="utf-8"))
    report["policy"]["reserved_prompts_excluded_from_every_split"] = False
    args.cache_report.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(ValueError, match="reserved_prompts"):
        MODULE.train_explicit_baseline(args)

    assert not args.output_dir.exists()


def test_phone_run_report_can_reference_existing_canonical_utterance_cache(
    tmp_path: Path,
) -> None:
    args = _fixture(tmp_path)
    report = json.loads(args.cache_report.read_text(encoding="utf-8"))
    canonical_sha = _sha256(args.utterance_cache)
    report["configuration"]["canonical_utterance_cache_sha256"] = canonical_sha
    report["outputs"]["utterance"]["sha256"] = "c" * 64
    report["outputs"]["utterance"][
        "canonical_utterance_cache_sha256"
    ] = canonical_sha
    args.cache_report.write_text(json.dumps(report), encoding="utf-8")

    contract = MODULE.validate_cache_contract(
        config=MODULE.load_config(args.config),
        utterance_cache=args.utterance_cache,
        phone_cache=args.phone_cache,
        cache_report_path=args.cache_report,
    )

    assert contract["utterance_cache_sha256"] == canonical_sha
    assert contract["utterance_cache_role"] == "canonical_reused_by_phone_run"


def test_registered_wrapper_records_inputs_metrics_and_all_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _fixture(tmp_path)
    args.registry_root = tmp_path / "registry"
    args.run_id = "synthetic-explicit-baseline"
    monkeypatch.setattr(
        registry_module,
        "collect_runtime_metadata",
        lambda package_names: {
            "python": {"version": "3.12.0"},
            "platform": {"system": "TestOS"},
            "packages": {name: "test" for name in package_names},
        },
    )
    monkeypatch.setattr(
        registry_module,
        "collect_accelerator_metadata",
        lambda: {
            "cuda_available": True,
            "torch_devices": [{"name": "Synthetic GPU"}],
            "nvidia_smi": {"available": True, "gpus": []},
        },
    )
    monkeypatch.setattr(
        registry_module,
        "collect_git_metadata",
        lambda repo_path: {
            "available": True,
            "commit": "d" * 40,
            "dirty": True,
        },
    )

    report, metadata_path = MODULE.run_registered_experiment(args)

    assert report["experiment_registry"]["integrated"] is True
    assert report["experiment_registry"]["t03_complete"] is True
    assert report["experiment_registry"]["registered_artifact_count"] == 13
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["status"] == "completed"
    assert metadata["seeds"] == {
        "experiment_seed_42": 42,
        "experiment_seed_43": 43,
        "experiment_seed_44": 44,
    }
    assert len(metadata["inputs"]["config"]) == 1
    assert len(metadata["inputs"]["data"]) == 3
    assert len(metadata["metrics"]) == 4
    assert len(metadata["artifacts"]) == 13
    assert {item["name"] for item in metadata["artifacts"]} >= {
        "final_report",
        "seed_42_checkpoint",
        "seed_43_phone_predictions",
        "seed_44_utterance_predictions",
    }
    serialized = metadata_path.read_text(encoding="utf-8")
    assert ".env" not in serialized
