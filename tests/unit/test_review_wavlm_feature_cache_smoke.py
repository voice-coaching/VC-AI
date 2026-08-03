from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml

from voice_coach.reproducibility import wavlm_cache as cache_module
from voice_coach.reproducibility import wavlm_smoke_review as review_module


REPOSITORY_ROOT = Path(__file__).parents[2]
SCRIPT = REPOSITORY_ROOT / "scripts" / "review_wavlm_feature_cache_smoke.py"
SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "review_wavlm_feature_cache_smoke_test_module",
    SCRIPT,
)
assert SCRIPT_SPEC is not None and SCRIPT_SPEC.loader is not None
SCRIPT_MODULE = importlib.util.module_from_spec(SCRIPT_SPEC)
sys.modules[SCRIPT_SPEC.name] = SCRIPT_MODULE
SCRIPT_SPEC.loader.exec_module(SCRIPT_MODULE)

EXPERIMENT_ID = "synthetic_wavlm_large_smoke_v1"
MODEL_ID = "example/wavlm-large-smoke"
MODEL_REVISION = "1" * 40
HIDDEN_SIZE = 4
SOURCE_HASH = "2" * 64
FULL_ROWS = {"utterance": 1_000, "phone": 2_000, "word": 1_000}
SHARD_ROWS = {"utterance": 40, "phone": 75, "word": 45}
ROW_GROUP_ROWS = {"utterance": 17, "phone": 23, "word": 19}
CONV_KERNEL = [10, 3, 3, 3, 3, 2, 2]
CONV_STRIDE = [5, 2, 2, 2, 2, 2, 2]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _identifier(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _preprocessor() -> dict[str, Any]:
    return {
        "do_normalize": True,
        "feature_extractor_type": "Wav2Vec2FeatureExtractor",
        "feature_size": 1,
        "padding_side": "right",
        "padding_value": 0.0,
        "return_attention_mask": True,
        "sampling_rate": 16_000,
    }


def _metadata(kind: str, *, config_sha256: str) -> dict[bytes, bytes]:
    inputs = {
        "utterance": ["embedding_mean_std"],
        "phone": ["embedding_mean", "target_label"],
        "word": ["embedding_mean"],
    }[kind]
    labels = {
        "utterance": [],
        "phone": ["binary_error_label"],
        "word": [],
    }[kind]
    return {
        b"voice_coach.schema_version": (
            f"voice_coach.wavlm_{kind}_features.v1".encode()
        ),
        b"voice_coach.model_id": MODEL_ID.encode(),
        b"voice_coach.model_revision": MODEL_REVISION.encode(),
        b"voice_coach.frozen_encoder": b"true",
        b"voice_coach.oracle_alignment": b"true",
        b"voice_coach.storage_precision": b"float16",
        b"voice_coach.allowed_splits": b"train,dev",
        b"voice_coach.metric_evaluation_performed": b"false",
        b"voice_coach.privacy_reviewed": b"true",
        b"voice_coach.contains_direct_identifiers": b"false",
        b"voice_coach.contains_transcript_text": b"false",
        b"voice_coach.contains_audio_bytes": b"false",
        b"voice_coach.contains_realized_phone_identity": b"false",
        b"voice_coach.contains_alignment_operation": b"false",
        b"voice_coach.access_scope": b"restricted_internal",
        b"voice_coach.shareable": b"false",
        b"voice_coach.source_manifest_sha256": SOURCE_HASH.encode(),
        b"voice_coach.config_sha256": config_sha256.encode(),
        b"voice_coach.input_feature_columns": json.dumps(
            inputs,
            separators=(",", ":"),
        ).encode(),
        b"voice_coach.label_columns": json.dumps(
            labels,
            separators=(",", ":"),
        ).encode(),
        b"voice_coach.slice_only_columns": (b'["split","l1","gender","proficiency"]'),
        b"voice_coach.forbidden_as_input": (
            b'["binary_error_label","realized_phone_identity",'
            b'"alignment_operation","error_tag","holistic_score"]'
        ),
    }


def _schema(
    kind: str,
    *,
    config_sha256: str,
    privacy_column: bool = False,
) -> pa.Schema:
    schema = cache_module._expected_schema(kind, HIDDEN_SIZE).with_metadata(
        _metadata(kind, config_sha256=config_sha256)
    )
    if privacy_column:
        schema = schema.append(pa.field("audio_path", pa.string(), nullable=False))
    return schema


def _common(index: int, *, output_split_mode: str) -> dict[str, Any]:
    if output_split_mode == "both":
        split = "train" if index < 50 else "dev"
    elif output_split_mode == "train_only":
        split = "train"
    elif output_split_mode == "forbidden":
        split = "locked_test"
    elif output_split_mode == "empty":
        split = ""
    else:
        raise ValueError(f"unsupported output split mode: {output_split_mode}")
    return {
        "oracle_alignment": True,
        "utterance_id_sha256": _identifier(f"utterance:{index}"),
        "speaker_id_sha256": _identifier(f"speaker:{index}"),
        "split": split,
        "prompt_sha256": _identifier(f"prompt:{index}"),
        "l1": "Chinese" if split == "train" else "Japanese",
        "gender": "F" if index % 2 == 0 else "M",
        "proficiency": "Intermediate",
    }


def _rows(
    kind: str,
    *,
    mode: str | None,
    output_split_mode: str,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for utterance_index in range(100):
        common = _common(
            utterance_index,
            output_split_mode=output_split_mode,
        )
        if kind == "utterance":
            vector = [float(utterance_index % 7 + 1)] * (HIDDEN_SIZE * 2)
            output.append(
                {
                    "schema_version": "voice_coach.wavlm_utterance_features.v1",
                    **common,
                    "audio_duration_seconds": 1.0,
                    "chunk_count": 1,
                    "frame_count": 10,
                    "embedding_mean_std": vector,
                }
            )
            continue
        child_count = 2 if kind == "phone" else 1
        for child_index in range(child_count):
            child_common = dict(common)
            if mode == "bad_join" and kind == "word" and utterance_index == 0:
                child_common["speaker_id_sha256"] = _identifier("other-speaker")
            index_value = child_index
            if (
                mode == "bad_index"
                and kind == "phone"
                and utterance_index == 0
                and child_index == 1
            ):
                index_value = 2
            vector = [float((utterance_index + child_index) % 7 + 1)] * HIDDEN_SIZE
            if mode == "nonfinite" and kind == "phone" and not output:
                vector[0] = float("nan")
            if mode == "all_zero" and kind == "word" and not output:
                vector = [0.0] * HIDDEN_SIZE
            row = {
                "schema_version": (
                    "voice_coach.wavlm_phone_features.v1"
                    if kind == "phone"
                    else "voice_coach.wavlm_word_features.v1"
                ),
                **child_common,
                f"{kind}_index": index_value,
                "oracle_start_seconds": child_index * 0.1,
                "oracle_end_seconds": child_index * 0.1 + 0.08,
                "pooled_frame_count": 3,
                "embedding_mean": vector,
            }
            if kind == "phone":
                row["target_label"] = "ㄱ"
                row["binary_error_label"] = child_index % 2
            if mode == "privacy" and kind == "word":
                row["audio_path"] = "never-export.wav"
            output.append(row)
    return output


def _write_shards(
    cache_root: Path,
    *,
    kind: str,
    rows: list[dict[str, Any]],
    schema: pa.Schema,
) -> dict[str, Any]:
    directory = cache_root / kind
    directory.mkdir(parents=True)
    shards: list[dict[str, Any]] = []
    for shard_index, start in enumerate(range(0, len(rows), SHARD_ROWS[kind])):
        path = directory / f"part-{shard_index:05d}.parquet"
        table = pa.Table.from_pylist(
            rows[start : start + SHARD_ROWS[kind]],
            schema=schema,
        )
        pq.write_table(
            table,
            path,
            compression="zstd",
            row_group_size=ROW_GROUP_ROWS[kind],
        )
        parquet = pq.ParquetFile(path)
        shards.append(
            {
                "name": path.name,
                "row_count": parquet.metadata.num_rows,
                "row_group_count": parquet.metadata.num_row_groups,
                "byte_count": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return {
        "row_count": len(rows),
        "shard_count": len(shards),
        "byte_count": sum(item["byte_count"] for item in shards),
        "content_sha256": _sha256_json(
            [
                {
                    "name": item["name"],
                    "row_count": item["row_count"],
                    "sha256": item["sha256"],
                }
                for item in shards
            ]
        ),
        "shards": shards,
    }


def _resource(outputs: dict[str, Any], safety_factor: float) -> dict[str, Any]:
    bytes_per_row = {
        kind: outputs[kind]["byte_count"] / outputs[kind]["row_count"]
        for kind in review_module.KINDS
    }
    projected = {
        kind: math.ceil(bytes_per_row[kind] * FULL_ROWS[kind])
        for kind in review_module.KINDS
    }
    projected_total = sum(projected.values())
    dimensions = {
        "utterance": HIDDEN_SIZE * 2,
        "phone": HIDDEN_SIZE,
        "word": HIDDEN_SIZE,
    }
    fixed = {
        kind: FULL_ROWS[kind] * dimensions[kind] * 2 for kind in review_module.KINDS
    }
    return {
        "method": "smoke compressed bytes per row scaled to audited full rows",
        "reference_rows": dict(FULL_ROWS),
        "observed_compressed_bytes_per_row": bytes_per_row,
        "projected_compressed_bytes_by_kind": projected,
        "projected_compressed_bytes_total": projected_total,
        "fixed_float16_embedding_bytes_by_kind": fixed,
        "fixed_float16_embedding_bytes_total": sum(fixed.values()),
        "fixed_embedding_estimate_excludes_metadata_and_parquet_overhead": True,
        "safety_factor": safety_factor,
        "estimated_full_cache_upper_bytes": math.ceil(projected_total * safety_factor),
        "estimate_is_capacity_planning_not_measured_full_output": True,
    }


@dataclass(frozen=True)
class Fixture:
    root: Path
    cache: Path
    report: Path
    config: Path
    builder: Path
    dependency: Path
    reviewer_cli: Path
    reviewer_core: Path
    output: Path
    canonical_cache: Path
    canonical_report: Path
    contract: review_module.SmokeVariantContract


def _fixture(
    tmp_path: Path,
    *,
    mode: str | None = None,
    output_split_mode: str = "both",
    variant_name: str = "large",
) -> Fixture:
    do_normalize = variant_name == "large"
    prompt = tmp_path / "prompt-reservation.json"
    prompt.write_text('{"privacy_safe":true}', encoding="utf-8")
    builder = tmp_path / "builder.py"
    builder.write_text("# synthetic large builder\n", encoding="utf-8")
    dependency = tmp_path / "base-builder.py"
    dependency.write_text("# synthetic pinned dependency\n", encoding="utf-8")
    reviewer_cli = tmp_path / review_module.REVIEWER_CLI_RELATIVE_PATH
    reviewer_cli.parent.mkdir(parents=True, exist_ok=True)
    reviewer_cli.write_text("# synthetic reviewer CLI\n", encoding="utf-8")
    reviewer_core = tmp_path / review_module.REVIEWER_CORE_RELATIVE_PATH
    reviewer_core.parent.mkdir(parents=True, exist_ok=True)
    reviewer_core.write_text("# synthetic reviewer core\n", encoding="utf-8")
    snapshot = (
        tmp_path
        / "model-cache"
        / "models--example--wavlm-large-smoke"
        / "snapshots"
        / MODEL_REVISION
    )
    snapshot.mkdir(parents=True)
    model_config = snapshot / "config.json"
    checkpoint = snapshot / "pytorch_model.bin"
    preprocessor = snapshot / "preprocessor_config.json"
    model_config.write_text(
        json.dumps(
            {
                "hidden_size": HIDDEN_SIZE,
                "conv_kernel": CONV_KERNEL,
                "conv_stride": CONV_STRIDE,
            }
        ),
        encoding="utf-8",
    )
    checkpoint.write_bytes(b"synthetic checkpoint")
    preprocessor.write_text(
        json.dumps(_preprocessor()),
        encoding="utf-8",
    )
    canonical_cache = tmp_path / "canonical" / "full-cache"
    canonical_report = tmp_path / "canonical" / "full-report.json"
    config_value = {
        "experiment": {
            "id": EXPERIMENT_ID,
            "stage": "feature_cache",
            "development_only": True,
        },
        "model": {
            "id": MODEL_ID,
            "revision": MODEL_REVISION,
            "cache_dir": "model-cache",
            "local_files_only": True,
            "frozen": True,
            "config_sha256": _sha256(model_config),
            "checkpoint_sha256": _sha256(checkpoint),
            "preprocessor_config_sha256": _sha256(preprocessor),
            "hidden_size": HIDDEN_SIZE,
            "conv_kernel": CONV_KERNEL,
            "conv_stride": CONV_STRIDE,
            "do_normalize": do_normalize,
            "return_attention_mask": True,
        },
        "data": {
            "modeling_manifest": "must-not-be-read.parquet",
            "modeling_manifest_sha256": SOURCE_HASH,
            "prompt_challenge_reservation": prompt.name,
            "allowed_splits": ["train", "dev"],
            "forbidden_metric_splits": ["stack", "calibration", "locked_test"],
        },
        "features": {
            "storage_precision": "float16",
        },
        "output": {
            "root": canonical_cache.relative_to(tmp_path).as_posix(),
            "report": canonical_report.relative_to(tmp_path).as_posix(),
            **{f"{kind}_shard_rows": SHARD_ROWS[kind] for kind in review_module.KINDS},
            **{
                f"{kind}_row_group_rows": ROW_GROUP_ROWS[kind]
                for kind in review_module.KINDS
            },
            "compression": "zstd",
            "atomic_publish": True,
        },
        "full_run_gate": {
            "require_completed_smoke_review": True,
            "require_resource_review": True,
            "smoke_utterances": 100,
            "resource_estimate_reference_rows": dict(FULL_ROWS),
            "resource_estimate_safety_factor": 1.25,
        },
    }
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(config_value, sort_keys=False),
        encoding="utf-8",
    )
    config_sha256 = _sha256(config)
    cache = tmp_path / "smoke-cache"
    cache.mkdir()
    rows = {
        kind: _rows(
            kind,
            mode=mode,
            output_split_mode=output_split_mode,
        )
        for kind in review_module.KINDS
    }
    included_rows_by_split = dict(
        Counter(str(row["split"]) for row in rows["utterance"])
    )
    if output_split_mode == "train_only":
        source_rows_by_split = {"train": 105}
        exclusions = {"reserved_prompt": 5}
    else:
        source_rows_by_split = dict(included_rows_by_split)
        first_output_split = next(iter(source_rows_by_split))
        source_rows_by_split[first_output_split] += 2
        source_rows_by_split["locked_test"] = (
            source_rows_by_split.get("locked_test", 0) + 1
        )
        exclusions = {
            "split_not_allowed": 1,
            "qc_flags_present": 1,
            "reserved_prompt": 1,
        }
    outputs = {
        kind: _write_shards(
            cache,
            kind=kind,
            rows=rows[kind],
            schema=_schema(
                kind,
                config_sha256=config_sha256,
                privacy_column=mode == "privacy" and kind == "word",
            ),
        )
        for kind in review_module.KINDS
    }
    report_value = {
        "schema_version": cache_module.CACHE_REPORT_SCHEMA_VERSION,
        "status": "completed",
        "source": {
            "modeling_manifest_sha256": SOURCE_HASH,
            "prompt_reservation_sha256": _sha256(prompt),
            "experiment_config_sha256": config_sha256,
            "builder_sha256": _sha256(builder),
            "builder_dependency_path": dependency.name,
            "builder_dependency_sha256": _sha256(dependency),
            "preprocessor_config_sha256": _sha256(preprocessor),
        },
        "model": {
            "id": MODEL_ID,
            "revision": MODEL_REVISION,
            "config_sha256": _sha256(model_config),
            "checkpoint_sha256": _sha256(checkpoint),
            "preprocessor_config_sha256": _sha256(preprocessor),
            "hidden_size": HIDDEN_SIZE,
            "parameter_count": 123,
            "frozen": True,
            "local_files_only": True,
            "do_normalize": do_normalize,
            "preprocessor_contract": {
                "sampling_rate_hz": 16_000,
                "do_normalize": do_normalize,
                "return_attention_mask": True,
                "source": "official_pinned_revision",
                "source_url": "https://example.invalid/pinned/preprocessor.json",
                "config_sha256": _sha256(preprocessor),
                "local_preprocessor_config_present": True,
                "local_preprocessor_config_validated": True,
                "auto_feature_extractor_used": False,
            },
        },
        "runtime": {
            "storage_precision": "float16",
            "cuda_autocast_precision": "bfloat16",
            "inference_precision": "bfloat16",
            "observed_model_output_dtypes": {"torch.float32": 100},
            "oom_count": 0,
            "peak_process_rss_bytes": 123_456_789,
            "peak_allocated_bytes": 1_000_000,
            "peak_reserved_bytes": 2_000_000,
        },
        "policy": {
            "allowed_splits": ["train", "dev"],
            "evaluation_splits": ["calibration", "locked_test", "stack"],
            "evaluation_split_metric_access": "forbidden",
            "holistic_score_columns_read": [],
            "metric_evaluation_performed": False,
            "rows_with_qc_flags_excluded": True,
            "reserved_prompts_excluded_from_every_split": True,
            "oracle_alignment": True,
            "utterance_id_namespace": review_module.UTTERANCE_NAMESPACE,
            "contains_direct_identifiers": False,
            "contains_transcript_text": False,
            "contains_audio_bytes": False,
            "contains_realized_phone_identity": False,
            "contains_alignment_operation": False,
            "access_scope": "restricted_internal",
            "shareable": False,
            "prohibited_output_columns": sorted(
                review_module.REPORTED_PROHIBITED_OUTPUT_COLUMNS
            ),
            "source_manifest_columns_deserialized_before_split_filter": list(
                review_module.SOURCE_COLUMNS
            ),
            "evaluation_manifest_annotations_may_be_deserialized_for_filtering": True,
            "evaluation_audio_decoded": False,
            "evaluation_rows_sent_to_encoder": False,
            "evaluation_labels_used_for_training_or_metrics": False,
        },
        "configuration": {
            "sample_rate_hz": 16_000,
            "max_chunk_seconds": 20.0,
            "chunking": "target_phone_label_aware",
            "limit": 100,
            "full_run": False,
            "full_run_gate": {},
            "utterance_pooling": "mean_std",
            "target_phone_pooling": "mean",
            "word_pooling": "mean",
            "frame_stride_samples": 320,
            "receptive_field_samples": 400,
            "streaming_writer": {
                kind: {
                    "shard_rows": SHARD_ROWS[kind],
                    "row_group_rows": ROW_GROUP_ROWS[kind],
                }
                for kind in review_module.KINDS
            },
            "embedding_dimensions": {
                "utterance_mean_std": HIDDEN_SIZE * 2,
                "phone_mean": HIDDEN_SIZE,
                "word_mean": HIDDEN_SIZE,
            },
            "frame_geometry_source": "pinned_local_wavlm_large_config",
        },
        "progress": {
            "source_rows_scanned": sum(source_rows_by_split.values()),
            "utterance_rows_written": len(rows["utterance"]),
            "phone_rows_written": len(rows["phone"]),
            "word_rows_written": len(rows["word"]),
            "decoded_audio_seconds": 100.0,
            "model_frame_count": 1_000,
            "chunk_count": 100,
            "padded_short_chunk_count": 0,
            "source_scan_complete": False,
            "source_rows_by_split": source_rows_by_split,
            "included_rows_by_split": included_rows_by_split,
            "exclusions": exclusions,
        },
        "outputs": outputs,
        "resource_estimate": _resource(outputs, 1.25),
        "frame_mapping_audit": {
            "validated_chunk_count": 100,
            "all_hidden_shapes_matched_pinned_geometry": True,
            "frame_stride_samples": 320,
            "receptive_field_samples": 400,
            "frame_center_mapping": "half_open_interval_by_frame_center",
        },
        "privacy_audit": {
            "schema_prohibited_column_hits": {kind: [] for kind in review_module.KINDS},
            "raw_locator_values_exported": False,
            "raw_file_stem_values_exported": False,
            "realized_phone_values_exported": False,
            "alignment_operation_values_exported": False,
            "word_transcript_values_exported": False,
        },
    }
    report = tmp_path / "smoke-report.json"
    report.write_text(
        json.dumps(report_value, ensure_ascii=False),
        encoding="utf-8",
    )
    contract = review_module.SmokeVariantContract(
        name=variant_name,
        experiment_id=EXPERIMENT_ID,
        model_id=MODEL_ID,
        revision=MODEL_REVISION,
        hidden_size=HIDDEN_SIZE,
        source_rows=1_000,
        config_sha256=config_sha256,
        builder_sha256=_sha256(builder),
        model_config_sha256=_sha256(model_config),
        model_checkpoint_sha256=_sha256(checkpoint),
        do_normalize=do_normalize,
        preprocessor_url="https://example.invalid/pinned/preprocessor.json",
        preprocessor_sha256=(
            _sha256(preprocessor) if variant_name == "large" else None
        ),
        builder_dependency_path=(dependency.name if variant_name == "large" else None),
        builder_dependency_sha256=(
            _sha256(dependency) if variant_name == "large" else None
        ),
        require_explicit_source_semantics=variant_name == "large",
        audited_full_rows=dict(FULL_ROWS),
    )
    return Fixture(
        root=tmp_path,
        cache=cache,
        report=report,
        config=config,
        builder=builder,
        dependency=dependency,
        reviewer_cli=reviewer_cli,
        reviewer_core=reviewer_core,
        output=tmp_path / "review.json",
        canonical_cache=canonical_cache,
        canonical_report=canonical_report,
        contract=contract,
    )


def _review(
    fixture: Fixture,
    monkeypatch: pytest.MonkeyPatch,
    *,
    output: Path | None = None,
) -> dict[str, Any]:
    monkeypatch.setitem(
        review_module.VARIANT_CONTRACTS,
        EXPERIMENT_ID,
        fixture.contract,
    )
    return review_module.review_wavlm_feature_cache_smoke(
        cache_root=fixture.cache,
        report_path=fixture.report,
        config_path=fixture.config,
        builder_path=fixture.builder,
        output_path=output or fixture.output,
        workspace_root=fixture.root,
        batch_rows=13,
    )


def _mutate_report(fixture: Fixture, path: tuple[str, ...], value: Any) -> None:
    report = json.loads(fixture.report.read_text(encoding="utf-8"))
    cursor = report
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    fixture.report.write_text(
        json.dumps(report, ensure_ascii=False),
        encoding="utf-8",
    )


def _codes(result: dict[str, Any]) -> set[str]:
    return {item["code"] for item in result.get("errors", [])}


def test_repository_variant_pins_match_without_reading_any_cache() -> None:
    base = review_module.VARIANT_CONTRACTS["aihub71490_wavlm_base_plus_frozen_v1"]
    large = review_module.VARIANT_CONTRACTS["aihub71490_wavlm_large_frozen_v1"]

    assert (
        _sha256(
            REPOSITORY_ROOT
            / "configs"
            / "experiments"
            / "wavlm_base_plus_frozen_v1.yaml"
        )
        == base.config_sha256
    )
    assert (
        _sha256(REPOSITORY_ROOT / "scripts" / "build_wavlm_feature_cache.py")
        == base.builder_sha256
    )
    assert (
        _sha256(
            REPOSITORY_ROOT / "configs" / "experiments" / "wavlm_large_frozen_v1.yaml"
        )
        == large.config_sha256
    )
    assert (
        _sha256(REPOSITORY_ROOT / "scripts" / "build_wavlm_large_feature_cache.py")
        == large.builder_sha256
    )
    assert large.builder_dependency_sha256 == base.builder_sha256


def test_synthetic_100_utterance_multi_shard_smoke_is_approved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)

    result = _review(fixture, monkeypatch)

    assert result["status"] == "approved"
    assert result["approved"] is True
    assert result["full_run_approved"] is True
    assert result["human_expert_review"] is False
    assert result["validation_summary"]["rows_checked"] == {
        "utterance": 100,
        "phone": 200,
        "word": 100,
    }
    assert result["validation_summary"]["shards_checked"] == {
        "utterance": 3,
        "phone": 3,
        "word": 3,
    }
    assert result["validation_summary"]["child_joins_checked"] == {
        "phone": 200,
        "word": 100,
    }
    assert result["technical_checks"]["all_embeddings_finite_and_nonzero"] is True
    assert result["technical_checks"]["frame_geometry_check_passed"] is True
    assert result["resource_review"]["approved"] is True
    assert result["resource_review"]["max_row_group_rows"] == 23
    assert result["source_contract"]["observed_output_splits"] == {
        "utterance": ["train", "dev"],
        "phone": ["train", "dev"],
        "word": ["train", "dev"],
    }
    assert result["source_contract"]["allowed_split_coverage_complete"] is True
    assert (
        result["source_contract"]["full_cache_validation_requires_all_allowed_splits"]
        is True
    )
    assert result["reviewer_source"] == {
        "cli_path": review_module.REVIEWER_CLI_RELATIVE_PATH,
        "cli_sha256": _sha256(fixture.reviewer_cli),
        "core_path": review_module.REVIEWER_CORE_RELATIVE_PATH,
        "core_sha256": _sha256(fixture.reviewer_core),
    }
    serialized = json.dumps(result, ensure_ascii=False)
    assert _identifier("utterance:0") not in serialized
    assert "must-not-be-read.parquet" not in serialized
    assert "embedding_mean" not in serialized


def test_train_only_ordered_prefix_smoke_is_approved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, output_split_mode="train_only")

    result = _review(fixture, monkeypatch)

    assert result["status"] == "approved"
    assert result["validation_summary"]["rows_checked"]["utterance"] == 100
    assert result["source_contract"]["observed_output_splits"] == {
        "utterance": ["train"],
        "phone": ["train"],
        "word": ["train"],
    }
    assert result["source_contract"]["allowed_split_coverage_complete"] is False


@pytest.mark.parametrize(
    ("output_split_mode", "expected_code"),
    [
        ("forbidden", "utterance_forbidden_split_present"),
        ("empty", "utterance_empty_split_present"),
    ],
)
def test_forbidden_or_empty_observed_split_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output_split_mode: str,
    expected_code: str,
) -> None:
    fixture = _fixture(tmp_path, output_split_mode=output_split_mode)

    result = _review(fixture, monkeypatch)

    assert result["status"] == "failed"
    assert expected_code in _codes(result)


def test_base_plus_smoke_uses_same_full_scan_without_large_only_requirements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, variant_name="base_plus")

    result = _review(fixture, monkeypatch)

    assert result["status"] == "approved"
    assert result["validation_summary"]["variant"] == "base_plus"
    assert result["source_contract"]["do_normalize"] is False
    assert result["source_contract"]["preprocessor_config_sha256"] is None
    assert "builder_dependency_sha256" not in result


def test_tampered_shard_fails_with_aggregate_only_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    shard = fixture.cache / "utterance" / "part-00000.parquet"
    with shard.open("ab") as handle:
        handle.write(b"tamper")

    result = _review(fixture, monkeypatch)

    assert result["status"] == "failed"
    assert result["approved"] is False
    assert result["full_run_approved"] is False
    assert "utterance_shard_hash_mismatch" in _codes(result)
    assert "smoke_report_path" not in result
    serialized = json.dumps(result)
    assert str(fixture.cache) not in serialized
    assert _identifier("utterance:0") not in serialized


@pytest.mark.parametrize(
    ("mode", "expected_code"),
    [
        ("nonfinite", "phone_embedding_nonfinite"),
        ("all_zero", "word_embedding_all_zero"),
        ("bad_index", "phone_index_not_contiguous"),
        ("bad_join", "word_utterance_join_mismatch"),
        ("privacy", "word_privacy_prohibited_column"),
    ],
)
def test_coordinated_content_mutations_fail_full_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_code: str,
) -> None:
    fixture = _fixture(tmp_path, mode=mode)

    result = _review(fixture, monkeypatch)

    assert result["status"] == "failed"
    assert expected_code in _codes(result)


@pytest.mark.parametrize(
    ("path", "value", "expected_code"),
    [
        (("configuration", "limit"), 99, "cache_report_not_exact_smoke"),
        (("runtime", "oom_count"), 1, "runtime_oom_count_nonzero"),
        (
            ("progress", "source_scan_complete"),
            True,
            "smoke_source_scan_must_be_incomplete",
        ),
        (
            ("frame_mapping_audit", "frame_stride_samples"),
            321,
            "frame_mapping_audit_invalid",
        ),
        (
            ("resource_estimate", "estimated_full_cache_upper_bytes"),
            1,
            "reported_resource_estimate_mismatch",
        ),
        (
            ("policy", "evaluation_audio_decoded"),
            True,
            "evaluation_source_semantics_mismatch",
        ),
        (
            ("source", "builder_dependency_sha256"),
            "f" * 64,
            "builder_dependency_pin_mismatch",
        ),
    ],
)
def test_report_contract_mutations_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: tuple[str, ...],
    value: Any,
    expected_code: str,
) -> None:
    fixture = _fixture(tmp_path)
    _mutate_report(fixture, path, value)

    result = _review(fixture, monkeypatch)

    assert result["status"] == "failed"
    assert expected_code in _codes(result)


def test_builder_pin_drift_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    fixture.builder.write_text("# changed builder\n", encoding="utf-8")

    result = _review(fixture, monkeypatch)

    assert result["status"] == "failed"
    assert {
        "cache_builder_pin_mismatch",
        "report_builder_hash_mismatch",
    }.issubset(_codes(result))


@pytest.mark.parametrize("target", ["cache", "report", "output"])
def test_canonical_full_paths_are_refused_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    fixture = _fixture(tmp_path)
    fixture.canonical_cache.mkdir(parents=True)
    fixture.canonical_report.parent.mkdir(parents=True, exist_ok=True)
    fixture.canonical_report.write_text("sentinel", encoding="utf-8")
    monkeypatch.setitem(
        review_module.VARIANT_CONTRACTS,
        EXPERIMENT_ID,
        fixture.contract,
    )
    kwargs = {
        "cache_root": fixture.cache,
        "report_path": fixture.report,
        "config_path": fixture.config,
        "builder_path": fixture.builder,
        "output_path": fixture.output,
        "workspace_root": fixture.root,
        "batch_rows": 13,
    }
    if target == "cache":
        kwargs["cache_root"] = fixture.canonical_cache
    elif target == "report":
        kwargs["report_path"] = fixture.canonical_report
    else:
        kwargs["output_path"] = fixture.canonical_report

    with pytest.raises(review_module.UnsafeSmokeReviewPath):
        review_module.review_wavlm_feature_cache_smoke(**kwargs)

    assert fixture.canonical_report.read_text(encoding="utf-8") == "sentinel"


def test_cli_atomically_writes_approved_and_failed_aggregate_reviews(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    monkeypatch.setitem(
        review_module.VARIANT_CONTRACTS,
        EXPERIMENT_ID,
        fixture.contract,
    )
    arguments = [
        str(SCRIPT),
        "--workspace-root",
        str(fixture.root),
        "--cache-root",
        str(fixture.cache),
        "--report",
        str(fixture.report),
        "--config",
        str(fixture.config),
        "--builder",
        str(fixture.builder),
        "--output",
        str(fixture.output),
        "--batch-rows",
        "13",
    ]
    monkeypatch.setattr(sys, "argv", arguments)

    assert SCRIPT_MODULE.main() == 0
    approved = json.loads(fixture.output.read_text(encoding="utf-8"))
    assert approved["status"] == "approved"
    assert not list(tmp_path.glob(".review.json.*.part"))

    shard = fixture.cache / "phone" / "part-00000.parquet"
    with shard.open("ab") as handle:
        handle.write(b"tamper")
    failed_output = tmp_path / "failed-review.json"
    arguments[arguments.index(str(fixture.output))] = str(failed_output)
    monkeypatch.setattr(sys, "argv", arguments)

    assert SCRIPT_MODULE.main() == 1
    failed = json.loads(failed_output.read_text(encoding="utf-8"))
    assert failed["status"] == "failed"
    assert failed["approved"] is False
    assert "smoke_report_path" not in failed
    assert not list(tmp_path.glob(".failed-review.json.*.part"))


def test_cli_rejects_pinned_canonical_output_before_reading_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = (
        tmp_path
        / "data"
        / "processed"
        / "aihub71490"
        / "features"
        / "wavlm_large_frozen_v1_report.json"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--workspace-root",
            str(tmp_path),
            "--cache-root",
            "missing-cache",
            "--report",
            "missing-report.json",
            "--config",
            "missing-config.yaml",
            "--builder",
            "missing-builder.py",
            "--output",
            str(canonical),
        ],
    )

    assert SCRIPT_MODULE.main() == 2
    assert not canonical.exists()
