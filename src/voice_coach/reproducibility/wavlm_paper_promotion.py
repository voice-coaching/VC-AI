"""Fail-closed preparation of WavLM S103 manuscript evidence.

This module deliberately does not edit the canonical manuscript or evidence map.
It validates the complete immutable renderer lineage and publishes a reviewable,
content-addressed candidate bundle.  A human must still apply the candidate with
an explicit patch and rerun the canonical manuscript evidence validator.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, NoReturn

import yaml  # type: ignore[import-untyped]

from voice_coach.reproducibility.evidence import validate_evidence_map
from voice_coach.reproducibility import wavlm_results as results


MANIFEST_SCHEMA = "voice_coach.wavlm_paper_promotion_manifest.v1"
RECEIPT_SCHEMA = "voice_coach.wavlm_paper_promotion_receipt.v1"
COMMIT_SCHEMA = "voice_coach.wavlm_paper_promotion_commit.v1"
EXPECTED_INPUTS = (
    "base_report",
    "base_registry",
    "large_report",
    "large_registry",
    "comparison_commit",
    "results_commit",
)
CANONICAL_MANUSCRIPT = "docs/paper/EXPERIMENTAL_MANUSCRIPT_KO.md"
CANONICAL_EVIDENCE_MAP = "docs/paper/evidence-map.yaml"
OUTPUT_PREFIX = ("artifacts", "reproducibility")
EXPECTED_PENDING_MARKER = (
    "[PENDING: S101–S108 — explicit/Base+/Large/adapter/HiPAMA/fusion 표]"
)
ARTIFACT_ALIASES = (
    "wavlm_large_head_report",
    "wavlm_large_head_registry",
    "wavlm_s103_comparison_commit",
    "wavlm_s103_comparison_report",
    "wavlm_s103_comparison_receipt",
    "wavlm_s103_results_commit",
    "wavlm_s103_results_markdown",
    "wavlm_s103_results_json",
)
BEGIN_MARKER = "<!-- BEGIN WAVLM_S103_RESULTS_TABLE -->"
END_MARKER = "<!-- END WAVLM_S103_RESULTS_TABLE -->"
NUMBER_PATTERN = r"[+-]?\d+(?:\.\d+)?"
AGGREGATE_PATTERN = rf"{NUMBER_PATTERN} ± {NUMBER_PATTERN}"
MAX_MANIFEST_BYTES = 1024 * 1024


class PaperPromotionError(RuntimeError):
    """Raised when evidence is not safe to prepare for manuscript review."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: yaml.Loader,
    node: yaml.Node,
    deep: bool = False,
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in output:
            _fail("manifest_duplicate_key")
        output[key] = loader.construct_object(value_node, deep=deep)
    return output


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True)
class PromotionTargets:
    manuscript: results.FileFingerprint
    evidence_map: results.FileFingerprint
    pending_marker: str


@dataclass(frozen=True)
class ValidatedLineage:
    inputs: Mapping[str, results.FileFingerprint]
    comparison: results.ComparisonCommit
    markdown: str
    payload: Mapping[str, Any]
    output_commit: Mapping[str, Any]
    output_markdown: results.FileFingerprint
    output_json: results.FileFingerprint
    phone: Mapping[int, Mapping[str, Mapping[str, float]]]
    utterance: Mapping[
        int,
        Mapping[str, Mapping[str, Mapping[str, float]]],
    ]
    primary: Mapping[str, Any]


def _fail(code: str) -> NoReturn:
    raise PaperPromotionError(code)


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _list(value: object, code: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(code)
    return value


def _string(value: object, code: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(code)
    return value


def _sha(value: object, code: str) -> str:
    candidate = _string(value, code)
    if results.SHA256_RE.fullmatch(candidate) is None:
        _fail(code)
    return candidate


def _reject_nonfinite(value: Any, *, code: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        _fail(code)
    if isinstance(value, Mapping):
        for child in value.values():
            _reject_nonfinite(child, code=code)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_nonfinite(child, code=code)


def _load_manifest(path: Path) -> Mapping[str, Any]:
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            _fail("manifest_too_large")
        text = path.read_text(encoding="utf-8")
        value = yaml.load(text, Loader=_UniqueKeyLoader)
    except PaperPromotionError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError):
        _fail("manifest_read_failed")
    document = _mapping(value, "manifest_root")
    _reject_nonfinite(document, code="manifest_nonfinite")
    return document


def _fingerprint_record(
    root: Path,
    record: object,
    *,
    code_prefix: str,
) -> results.FileFingerprint:
    item = _mapping(record, f"{code_prefix}_record")
    if set(item) != {"path", "sha256", "size_bytes"}:
        _fail(f"{code_prefix}_record_keys")
    try:
        return results._validate_fingerprint_record(  # noqa: SLF001
            root,
            item,
            base=None,
            code_prefix=code_prefix,
        )
    except results.ResultsValidationError as exc:
        _fail(f"{code_prefix}_{exc.code}")


def _relative_record(
    root: Path,
    fingerprint: results.FileFingerprint,
) -> dict[str, Any]:
    return {
        "path": fingerprint.path.relative_to(root).as_posix(),
        "sha256": fingerprint.sha256,
        "size_bytes": fingerprint.size_bytes,
    }


def _record_without_size(
    root: Path,
    fingerprint: results.FileFingerprint,
) -> dict[str, str]:
    return {
        "path": fingerprint.path.relative_to(root).as_posix(),
        "sha256": fingerprint.sha256,
    }


def _load_ready_manifest(
    manifest_path: str | Path,
    *,
    root: Path,
) -> tuple[results.FileFingerprint, Mapping[str, Any]]:
    try:
        resolved = results._existing_file(root, manifest_path)  # noqa: SLF001
        before = results._fingerprint(resolved)  # noqa: SLF001
    except results.ResultsValidationError as exc:
        _fail(f"manifest_{exc.code}")
    manifest = _load_manifest(resolved)
    try:
        after = results._fingerprint(resolved)  # noqa: SLF001
    except results.ResultsValidationError as exc:
        _fail(f"manifest_{exc.code}")
    if before != after:
        _fail("manifest_changed_while_reading")
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        _fail("manifest_schema")
    # This check intentionally precedes all reads of referenced artifacts.  The
    # repository template therefore cannot accidentally touch incomplete runs.
    if manifest.get("status") != "ready":
        _fail("manifest_not_ready")
    if set(manifest) != {"schema_version", "status", "inputs", "targets"}:
        _fail("manifest_keys")
    return after, manifest


def _assert_fingerprint_unchanged(
    expected: results.FileFingerprint,
    *,
    code: str,
) -> None:
    try:
        actual = results._fingerprint(expected.path)  # noqa: SLF001
    except results.ResultsValidationError as exc:
        _fail(f"{code}_{exc.code}")
    if actual != expected:
        _fail(f"{code}_changed")


def _assert_promotion_sources_unchanged(
    *,
    manifest: results.FileFingerprint,
    targets: PromotionTargets,
    lineage: ValidatedLineage,
) -> None:
    _assert_fingerprint_unchanged(manifest, code="manifest")
    _assert_fingerprint_unchanged(targets.manuscript, code="manuscript")
    _assert_fingerprint_unchanged(targets.evidence_map, code="evidence_map")
    checked: set[Path] = set()
    for fingerprint in (
        *lineage.inputs.values(),
        lineage.comparison.report_file,
        lineage.comparison.receipt_file,
        lineage.output_markdown,
        lineage.output_json,
    ):
        if fingerprint.path in checked:
            continue
        checked.add(fingerprint.path)
        _assert_fingerprint_unchanged(fingerprint, code="lineage_source")


def _validate_ready_inputs(
    manifest: Mapping[str, Any],
    *,
    root: Path,
) -> tuple[dict[str, results.FileFingerprint], PromotionTargets]:
    configured_inputs = _mapping(manifest.get("inputs"), "manifest_inputs")
    if set(configured_inputs) != set(EXPECTED_INPUTS):
        _fail("manifest_input_set")
    inputs = {
        name: _fingerprint_record(
            root,
            configured_inputs.get(name),
            code_prefix=f"manifest_{name}",
        )
        for name in EXPECTED_INPUTS
    }
    if len({item.path for item in inputs.values()}) != len(inputs):
        _fail("manifest_input_paths_collide")

    targets = _mapping(manifest.get("targets"), "manifest_targets")
    if set(targets) != {
        "manuscript",
        "evidence_map",
        "insert_before_pending_marker",
    }:
        _fail("manifest_target_keys")
    manuscript = _fingerprint_record(
        root,
        targets.get("manuscript"),
        code_prefix="manifest_manuscript",
    )
    evidence_map = _fingerprint_record(
        root,
        targets.get("evidence_map"),
        code_prefix="manifest_evidence_map",
    )
    if manuscript.path in {item.path for item in inputs.values()}:
        _fail("manifest_manuscript_is_input_artifact")
    if evidence_map.path in {item.path for item in inputs.values()}:
        _fail("manifest_evidence_map_is_input_artifact")
    if manuscript.path.relative_to(root).as_posix() != CANONICAL_MANUSCRIPT:
        _fail("manifest_manuscript_not_canonical")
    if evidence_map.path.relative_to(root).as_posix() != CANONICAL_EVIDENCE_MAP:
        _fail("manifest_evidence_map_not_canonical")
    pending_marker = _string(
        targets.get("insert_before_pending_marker"),
        "manifest_pending_marker",
    )
    if re.fullmatch(r"\[PENDING:[^\]]+\]", pending_marker) is None:
        _fail("manifest_pending_marker_contract")
    if pending_marker != EXPECTED_PENDING_MARKER:
        _fail("manifest_pending_marker_target")
    return inputs, PromotionTargets(
        manuscript=manuscript,
        evidence_map=evidence_map,
        pending_marker=pending_marker,
    )


def _validate_active_paper(
    *,
    root: Path,
    targets: PromotionTargets,
) -> tuple[str, dict[str, Any], Mapping[str, Any]]:
    try:
        active_report = validate_evidence_map(
            targets.evidence_map.path,
            workspace_root=root,
        )
    except (OSError, ValueError):
        _fail("active_evidence_validation_error")
    if active_report.get("status") != "passed":
        _fail("active_evidence_not_passed")
    manuscript_record = _mapping(
        active_report.get("manuscript"),
        "active_evidence_manuscript",
    )
    if (
        manuscript_record.get("path")
        != targets.manuscript.path.relative_to(root).as_posix()
        or manuscript_record.get("sha256") != targets.manuscript.sha256
    ):
        _fail("active_evidence_manuscript_binding")
    pending = _mapping(
        active_report.get("pending_contract"),
        "active_pending_contract",
    )
    if pending.get("status") != "passed":
        _fail("active_pending_contract")
    try:
        manuscript = targets.manuscript.path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        _fail("active_manuscript_read")
    if manuscript.count(targets.pending_marker) != 1:
        _fail("pending_marker_not_unique")
    if BEGIN_MARKER in manuscript or END_MARKER in manuscript:
        _fail("paper_results_block_already_present")
    evidence_config = _load_manifest(targets.evidence_map.path)
    return manuscript, active_report, evidence_config


def _validate_results_provenance(
    *,
    root: Path,
    payload: Mapping[str, Any],
    inputs: Mapping[str, results.FileFingerprint],
    comparison: results.ComparisonCommit,
    comparison_sources: Mapping[str, results.FileFingerprint],
) -> dict[str, Any]:
    provenance = _mapping(payload.get("provenance"), "lineage_provenance")
    if set(comparison_sources) != {
        "config",
        "comparison_script",
        "comparison_module",
    }:
        _fail("lineage_comparison_source_set")
    comparison_record = _relative_record(root, inputs["comparison_commit"])
    expected_provenance = {
        "base_plus_report_sha256": inputs["base_report"].sha256,
        "base_plus_registry_sha256": inputs["base_registry"].sha256,
        "large_report_sha256": inputs["large_report"].sha256,
        "large_registry_sha256": inputs["large_registry"].sha256,
        "comparison_report_sha256": comparison.report_file.sha256,
        "comparison_receipt_sha256": comparison.receipt_file.sha256,
        "comparison_commit": comparison_record,
        "comparison_config_sha256": comparison_sources["config"].sha256,
        "comparison_script_sha256": comparison_sources["comparison_script"].sha256,
        "comparison_module_sha256": comparison_sources["comparison_module"].sha256,
        "hash_chain_validated": True,
    }
    if provenance != expected_provenance:
        _fail("lineage_output_provenance_mismatch")
    return comparison_record


def _validate_lineage(
    *,
    root: Path,
    inputs: Mapping[str, results.FileFingerprint],
) -> ValidatedLineage:
    try:
        heads = {
            "base_plus": results._validate_head(  # noqa: SLF001
                root,
                inputs["base_report"].path,
                inputs["base_registry"].path,
                variant="base_plus",
            ),
            "large": results._validate_head(  # noqa: SLF001
                root,
                inputs["large_report"].path,
                inputs["large_registry"].path,
                variant="large",
            ),
        }
        comparison = results._validate_comparison_commit(  # noqa: SLF001
            root,
            inputs["comparison_commit"].path,
        )
        results._validate_comparison_contract(comparison.report)  # noqa: SLF001
        comparison_sources = results._validate_comparison_sources(  # noqa: SLF001
            root,
            comparison.report,
            heads,
        )
        results._validate_privacy(comparison.report)  # noqa: SLF001
        phone, utterance = results._validate_comparison_metrics(  # noqa: SLF001
            comparison.report,
            heads,
        )
        primary = results._validate_primary_and_multiplicity(  # noqa: SLF001
            comparison.report,
            phone,
            utterance,
        )
        markdown, payload, output_commit = results.load_committed_wavlm_results(
            commit_manifest_path=inputs["results_commit"].path,
            workspace_root=root,
        )
        output_records = _mapping(
            output_commit.get("outputs"),
            "lineage_output_records",
        )
        output_markdown = _fingerprint_record(
            root,
            output_records.get("markdown"),
            code_prefix="lineage_output_markdown",
        )
        output_json = _fingerprint_record(
            root,
            output_records.get("json"),
            code_prefix="lineage_output_json",
        )
    except results.ResultsValidationError as exc:
        _fail(f"lineage_{exc.code}")

    if markdown != results._render_markdown(  # noqa: SLF001
        phone,
        utterance,
        primary,
    ):
        _fail("lineage_markdown_recompute_mismatch")
    if payload.get("phone") != results._safe_numeric_copy(phone):  # noqa: SLF001
        _fail("lineage_phone_payload_mismatch")
    if payload.get("utterance") != results._safe_numeric_copy(  # noqa: SLF001
        utterance
    ):
        _fail("lineage_utterance_payload_mismatch")
    if payload.get("primary_comparison") != results._safe_numeric_copy(  # noqa: SLF001
        primary
    ):
        _fail("lineage_primary_payload_mismatch")
    if set(payload) != {
        "schema_version",
        "status",
        "scope",
        "provenance",
        "seeds",
        "phone",
        "utterance",
        "primary_comparison",
        "privacy",
    }:
        _fail("lineage_payload_keys")
    if (
        payload.get("schema_version") != results.OUTPUT_SCHEMA
        or payload.get("status") != "completed"
        or payload.get("seeds") != list(results.EXPECTED_SEEDS)
    ):
        _fail("lineage_payload_contract")

    scope = _mapping(payload.get("scope"), "lineage_scope")
    expected_scope = {
        "development_only": True,
        "oracle_alignment": True,
        "locked_test_accessed": False,
        "stack_accessed": False,
        "calibration_accessed": False,
        "promotion_decision_allowed": False,
    }
    if scope != expected_scope:
        _fail("lineage_scope_contract")
    privacy = _mapping(payload.get("privacy"), "lineage_privacy")
    if privacy != {
        "raw_data_read": False,
        "environment_files_read": False,
        "prediction_contents_read": False,
        "direct_or_pseudonymous_identifiers_exported": False,
        "artifact_paths_exported": False,
        "aggregate_only": True,
    }:
        _fail("lineage_privacy_contract")
    comparison_record = _validate_results_provenance(
        root=root,
        payload=payload,
        inputs=inputs,
        comparison=comparison,
        comparison_sources=comparison_sources,
    )
    if output_commit.get("comparison_commit") != comparison_record:
        _fail("lineage_commit_comparison_commit")

    return ValidatedLineage(
        inputs=inputs,
        comparison=comparison,
        markdown=markdown,
        payload=payload,
        output_commit=output_commit,
        output_markdown=output_markdown,
        output_json=output_json,
        phone=phone,
        utterance=utterance,
        primary=primary,
    )


def _row_regex(
    leading_cells: Sequence[str],
    value_patterns: Sequence[str],
) -> str:
    cells = [*(re.escape(cell) for cell in leading_cells), *value_patterns]
    return r"^\| " + r" \| ".join(cells) + r" \|$"


def _numeric_claim(
    *,
    claim_id: str,
    artifact: str,
    pointer: str,
    regex: str,
    decimals: int,
) -> dict[str, Any]:
    return {
        "id": claim_id,
        "source": {
            "artifact": artifact,
            "kind": "json_pointer",
            "pointer": pointer,
        },
        "document": {"regex": regex},
        "comparison": {"type": "numeric", "decimals": decimals},
    }


def _exact_claim(
    *,
    claim_id: str,
    artifact: str,
    pointer: str,
    regex: str,
) -> dict[str, Any]:
    return {
        "id": claim_id,
        "source": {
            "artifact": artifact,
            "kind": "json_pointer",
            "pointer": pointer,
        },
        "document": {"regex": regex},
        "comparison": {"type": "exact"},
    }


def _paper_claims() -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    labels = {
        "base_plus": "WavLM Base+",
        "large": "WavLM Large",
        "large_minus_base": "Large − Base+",
    }
    phone_metrics = ("macro_f1", "auprc", "event_f1")
    utterance_metrics = ("qwk", "spearman", "mae", "icc_2_1")
    variants = ("base_plus", "large", "large_minus_base")

    for variant in variants:
        for seed in results.EXPECTED_SEEDS:
            for metric_index, metric in enumerate(phone_metrics):
                patterns = [NUMBER_PATTERN] * len(phone_metrics)
                patterns[metric_index] = rf"(?P<value>{NUMBER_PATTERN})"
                claims.append(
                    _numeric_claim(
                        claim_id=f"wavlm_s103_phone_{variant}_{seed}_{metric}",
                        artifact="wavlm_s103_results_json",
                        pointer=f"/phone/{seed}/{variant}/{metric}",
                        regex=_row_regex(
                            (labels[variant], str(seed)),
                            tuple(patterns),
                        ),
                        decimals=6,
                    )
                )
        for metric_index, metric in enumerate(phone_metrics):
            for statistic, capture in (("mean", 0), ("sample_std", 1)):
                patterns = [AGGREGATE_PATTERN] * len(phone_metrics)
                patterns[metric_index] = (
                    rf"{NUMBER_PATTERN} ± (?P<value>{NUMBER_PATTERN})"
                    if capture == 1
                    else rf"(?P<value>{NUMBER_PATTERN}) ± {NUMBER_PATTERN}"
                )
                regex = _row_regex(
                    (labels[variant], "Mean ± sample SD"),
                    tuple(patterns),
                )
                claims.append(
                    _numeric_claim(
                        claim_id=(f"wavlm_s103_phone_{variant}_{metric}_{statistic}"),
                        artifact="wavlm_s103_comparison_report",
                        pointer=(
                            f"/metrics/aggregate/phone/{metric}/{variant}/{statistic}"
                        ),
                        regex=regex,
                        decimals=6,
                    )
                )

    for variant in variants:
        for outcome in results.OUTCOMES:
            for seed in results.EXPECTED_SEEDS:
                for metric_index, metric in enumerate(utterance_metrics):
                    patterns = [NUMBER_PATTERN] * len(utterance_metrics)
                    patterns[metric_index] = rf"(?P<value>{NUMBER_PATTERN})"
                    claims.append(
                        _numeric_claim(
                            claim_id=(
                                f"wavlm_s103_utterance_{variant}_{outcome}_"
                                f"{seed}_{metric}"
                            ),
                            artifact="wavlm_s103_results_json",
                            pointer=(f"/utterance/{seed}/{outcome}/{variant}/{metric}"),
                            regex=_row_regex(
                                (labels[variant], outcome, str(seed)),
                                tuple(patterns),
                            ),
                            decimals=6,
                        )
                    )
            for metric_index, metric in enumerate(utterance_metrics):
                for statistic, capture in (("mean", 0), ("sample_std", 1)):
                    patterns = [AGGREGATE_PATTERN] * len(utterance_metrics)
                    patterns[metric_index] = (
                        rf"{NUMBER_PATTERN} ± (?P<value>{NUMBER_PATTERN})"
                        if capture == 1
                        else rf"(?P<value>{NUMBER_PATTERN}) ± {NUMBER_PATTERN}"
                    )
                    regex = _row_regex(
                        (labels[variant], outcome, "Mean ± sample SD"),
                        tuple(patterns),
                    )
                    claims.append(
                        _numeric_claim(
                            claim_id=(
                                f"wavlm_s103_utterance_{variant}_{outcome}_"
                                f"{metric}_{statistic}"
                            ),
                            artifact="wavlm_s103_comparison_report",
                            pointer=(
                                f"/metrics/aggregate/utterance/{outcome}/"
                                f"{metric}/{variant}/{statistic}"
                            ),
                            regex=regex,
                            decimals=6,
                        )
                    )

    primary_patterns = (
        NUMBER_PATTERN,
        rf"\[{NUMBER_PATTERN}, {NUMBER_PATTERN}\]",
        rf"≥ {NUMBER_PATTERN}",
        r"[^|]+",
    )
    primary_leading = ("Phone macro F1",)
    primary_sources = (
        (
            "estimate",
            "/primary_comparison/paired_speaker_bootstrap/estimate",
            0,
            NUMBER_PATTERN,
        ),
        (
            "confidence_lower",
            "/primary_comparison/paired_speaker_bootstrap/confidence_lower",
            1,
            rf"\[(?P<value>{NUMBER_PATTERN}), {NUMBER_PATTERN}\]",
        ),
        (
            "confidence_upper",
            "/primary_comparison/paired_speaker_bootstrap/confidence_upper",
            1,
            rf"\[{NUMBER_PATTERN}, (?P<value>{NUMBER_PATTERN})\]",
        ),
        (
            "threshold_absolute",
            "/primary_comparison/gate/threshold_absolute",
            2,
            rf"≥ (?P<value>{NUMBER_PATTERN})",
        ),
    )
    for name, pointer, index, target_pattern in primary_sources:
        patterns = list(primary_patterns)
        patterns[index] = target_pattern
        if name == "estimate":
            patterns[index] = rf"(?P<value>{NUMBER_PATTERN})"
        regex = _row_regex(
            primary_leading,
            tuple(patterns),
        )
        claims.append(
            _numeric_claim(
                claim_id=f"wavlm_s103_primary_{name}",
                artifact="wavlm_s103_results_json",
                pointer=pointer,
                regex=regex,
                decimals=6,
            )
        )
    decision_regex = _row_regex(
        primary_leading,
        (
            NUMBER_PATTERN,
            rf"\[{NUMBER_PATTERN}, {NUMBER_PATTERN}\]",
            rf"≥ {NUMBER_PATTERN}",
            r"(?P<value>[^| ]+)",
        ),
    )
    claims.append(
        _exact_claim(
            claim_id="wavlm_s103_primary_gate_status",
            artifact="wavlm_s103_results_json",
            pointer="/primary_comparison/gate/status",
            regex=decision_regex,
        )
    )
    for name in ("n_resamples", "n_valid_resamples"):
        prefix = (
            r"Bootstrap unit: speaker; repetitions: "
            if name == "n_resamples"
            else r"Bootstrap unit: speaker; repetitions: [\d,]+; "
        )
        suffix = (
            r"; valid repetitions: [\d,]+\."
            if name == "n_resamples"
            else r"valid repetitions: "
        )
        regex = prefix + (
            rf"(?P<value>[\d,]+){suffix}" if name == "n_resamples" else suffix
        )
        if name == "n_valid_resamples":
            regex += r"(?P<value>[\d,]+)\."
        claims.append(
            _numeric_claim(
                claim_id=f"wavlm_s103_primary_{name}",
                artifact="wavlm_s103_results_json",
                pointer=f"/primary_comparison/paired_speaker_bootstrap/{name}",
                regex=regex,
                decimals=0,
            )
        )
    return claims


def _artifact_additions(
    *,
    root: Path,
    lineage: ValidatedLineage,
) -> dict[str, Any]:
    return {
        "wavlm_large_head_report": _record_without_size(
            root,
            lineage.inputs["large_report"],
        ),
        "wavlm_large_head_registry": _record_without_size(
            root,
            lineage.inputs["large_registry"],
        ),
        "wavlm_s103_comparison_commit": _record_without_size(
            root,
            lineage.inputs["comparison_commit"],
        ),
        "wavlm_s103_comparison_report": _record_without_size(
            root,
            lineage.comparison.report_file,
        ),
        "wavlm_s103_comparison_receipt": _record_without_size(
            root,
            lineage.comparison.receipt_file,
        ),
        "wavlm_s103_results_commit": _record_without_size(
            root,
            lineage.inputs["results_commit"],
        ),
        "wavlm_s103_results_markdown": _record_without_size(
            root,
            lineage.output_markdown,
        ),
        "wavlm_s103_results_json": _record_without_size(
            root,
            lineage.output_json,
        ),
    }


def _guard_additions() -> dict[str, Any]:
    return {
        "access_checks": [
            {
                "artifact": "wavlm_large_head_report",
                "pointer": "/leakage_controls/stack_accessed",
            },
            {
                "artifact": "wavlm_large_head_report",
                "pointer": "/leakage_controls/calibration_accessed",
            },
            {
                "artifact": "wavlm_large_head_report",
                "pointer": "/leakage_controls/locked_test_accessed",
            },
            {
                "artifact": "wavlm_s103_comparison_report",
                "pointer": "/contract/leakage/stack_accessed",
            },
            {
                "artifact": "wavlm_s103_comparison_report",
                "pointer": "/contract/leakage/calibration_accessed",
            },
            {
                "artifact": "wavlm_s103_comparison_report",
                "pointer": "/contract/leakage/locked_test_accessed",
            },
            {
                "artifact": "wavlm_s103_results_json",
                "pointer": "/scope/stack_accessed",
            },
            {
                "artifact": "wavlm_s103_results_json",
                "pointer": "/scope/calibration_accessed",
            },
            {
                "artifact": "wavlm_s103_results_json",
                "pointer": "/scope/locked_test_accessed",
            },
            {
                "artifact": "wavlm_s103_results_json",
                "pointer": "/scope/promotion_decision_allowed",
            },
        ],
        "registries": [
            {
                "artifact": "wavlm_large_head_registry",
                "array_pointer": "/metrics",
                "split_field": "split",
                "forbidden_splits": [
                    "stack",
                    "calibration",
                    "locked_test",
                ],
            }
        ],
    }


def _merge_candidate_evidence(
    active: Mapping[str, Any],
    *,
    artifacts: Mapping[str, Any],
    claims: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    candidate = deepcopy(dict(active))
    active_artifacts = candidate.get("artifacts")
    if not isinstance(active_artifacts, dict):
        _fail("active_artifacts_contract")
    collision = set(active_artifacts) & set(ARTIFACT_ALIASES)
    if collision:
        _fail("paper_artifact_alias_already_exists")
    active_artifacts.update(deepcopy(dict(artifacts)))

    active_claims = candidate.get("claims")
    if not isinstance(active_claims, list):
        _fail("active_claims_contract")
    existing_ids = {
        item.get("id") for item in active_claims if isinstance(item, Mapping)
    }
    new_ids = {item.get("id") for item in claims}
    if len(new_ids) != len(claims) or existing_ids & new_ids:
        _fail("paper_claim_id_collision")
    active_claims.extend(deepcopy(list(claims)))

    guard = candidate.get("locked_test_guard")
    if not isinstance(guard, dict):
        _fail("active_locked_guard_contract")
    additions = _guard_additions()
    for key in ("access_checks", "registries"):
        existing = guard.get(key)
        if not isinstance(existing, list):
            _fail(f"active_locked_guard_{key}")
        existing.extend(deepcopy(additions[key]))
    return candidate


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _validate_document_binding(
    document: Mapping[str, Any],
    *,
    code: str,
) -> None:
    binding = _sha(document.get("binding_sha256"), f"{code}_binding")
    core = dict(document)
    del core["binding_sha256"]
    if hashlib.sha256(_canonical_json_bytes(core)).hexdigest() != binding:
        _fail(f"{code}_binding_mismatch")


def _yaml_bytes(value: Mapping[str, Any]) -> bytes:
    return yaml.safe_dump(
        dict(value),
        allow_unicode=True,
        sort_keys=False,
        width=1000,
    ).encode("utf-8")


def _fingerprint_bytes(path: Path, value: bytes) -> results.FileFingerprint:
    return results.FileFingerprint(
        path=path,
        sha256=hashlib.sha256(value).hexdigest(),
        size_bytes=len(value),
    )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_new_file(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    created = False
    try:
        descriptor = os.open(path, flags, 0o600)
        created = True
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(descriptor)
        _fsync_directory(path.parent)
    except BaseException:
        if created:
            try:
                path.unlink(missing_ok=True)
                _fsync_directory(path.parent)
            except OSError:
                pass
        raise


def _atomic_replace(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_generation(paths: Sequence[Path], generation_root: Path) -> None:
    for path in reversed(paths):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        generation_root.rmdir()
        _fsync_directory(generation_root.parent)
    except OSError:
        pass


def _manuscript_table(markdown: str) -> str:
    if not markdown.startswith("# WavLM frozen-head development results\n"):
        _fail("results_markdown_heading_contract")
    lines = markdown.splitlines(keepends=True)
    transformed = [f"###{line}" if line.startswith("#") else line for line in lines]
    output = "".join(transformed)
    if not output.endswith("\n"):
        _fail("results_markdown_trailing_newline")
    return output


def _prepare_candidate_documents(
    *,
    root: Path,
    targets: PromotionTargets,
    active_manuscript: str,
    active_evidence: Mapping[str, Any],
    lineage: ValidatedLineage,
    generation_root: Path,
) -> tuple[bytes, bytes, bytes, bytes]:
    table = _manuscript_table(lineage.markdown)
    block = f"{BEGIN_MARKER}\n{table}{END_MARKER}\n\n"
    candidate_manuscript = active_manuscript.replace(
        targets.pending_marker,
        block + targets.pending_marker,
        1,
    )
    if candidate_manuscript.count(targets.pending_marker) != 1:
        _fail("candidate_pending_marker_changed")
    if candidate_manuscript.count(BEGIN_MARKER) != 1:
        _fail("candidate_results_block_count")

    artifact_additions = _artifact_additions(root=root, lineage=lineage)
    claims = _paper_claims()
    canonical_candidate = _merge_candidate_evidence(
        active_evidence,
        artifacts=artifact_additions,
        claims=claims,
    )
    canonical_candidate["manuscript"] = targets.manuscript.path.relative_to(
        root
    ).as_posix()
    validation_candidate = deepcopy(canonical_candidate)
    validation_candidate["manuscript"] = (
        (generation_root / "manuscript.candidate.md").relative_to(root).as_posix()
    )
    additions = {
        "schema_version": 1,
        "artifacts": artifact_additions,
        "claims": claims,
        "locked_test_guard_additions": _guard_additions(),
        "insertion": {
            "before_pending_marker": targets.pending_marker,
            "begin_marker": BEGIN_MARKER,
            "end_marker": END_MARKER,
            "pending_marker_must_remain": True,
        },
    }
    return (
        candidate_manuscript.encode("utf-8"),
        _yaml_bytes(canonical_candidate),
        _yaml_bytes(validation_candidate),
        _yaml_bytes(additions),
    )


def load_committed_wavlm_paper_promotion(
    *,
    commit_path: str | Path,
    workspace_root: str | Path,
) -> Mapping[str, Any]:
    """Load and revalidate a committed review bundle without editing paper files."""

    root = Path(workspace_root).resolve(strict=True)
    try:
        resolved_commit = results._existing_file(root, commit_path)  # noqa: SLF001
        commit = results._load_json(resolved_commit)  # noqa: SLF001
    except results.ResultsValidationError as exc:
        _fail(f"promotion_commit_{exc.code}")
    relative_commit = resolved_commit.relative_to(root)
    if relative_commit.parts[: len(OUTPUT_PREFIX)] != OUTPUT_PREFIX:
        _fail("promotion_commit_not_reproducibility")
    if set(commit) != {
        "schema_version",
        "status",
        "generation_id",
        "promotion_receipt",
        "binding_sha256",
    }:
        _fail("promotion_commit_keys")
    if (
        commit.get("schema_version") != COMMIT_SCHEMA
        or commit.get("status") != "committed"
    ):
        _fail("promotion_commit_contract")
    generation_id = _sha(
        commit.get("generation_id"),
        "promotion_commit_generation",
    )
    _validate_document_binding(commit, code="promotion_commit")
    receipt_file = _fingerprint_record(
        root,
        commit.get("promotion_receipt"),
        code_prefix="promotion_receipt",
    )
    if receipt_file.path.parent.name != generation_id:
        _fail("promotion_receipt_generation_path")
    try:
        receipt = results._load_json(receipt_file.path)  # noqa: SLF001
    except results.ResultsValidationError as exc:
        _fail(f"promotion_receipt_{exc.code}")
    if set(receipt) != {
        "schema_version",
        "status",
        "generation_id",
        "manifest",
        "inputs",
        "derived_inputs",
        "targets",
        "pending_contract",
        "canonical_files_modified",
        "outputs",
        "binding_sha256",
    }:
        _fail("promotion_receipt_keys")
    if (
        receipt.get("schema_version") != RECEIPT_SCHEMA
        or receipt.get("status") != "completed"
        or receipt.get("generation_id") != generation_id
        or receipt.get("canonical_files_modified") is not False
    ):
        _fail("promotion_receipt_contract")
    _validate_document_binding(receipt, code="promotion_receipt")

    _fingerprint_record(
        root,
        receipt.get("manifest"),
        code_prefix="promotion_source_manifest",
    )
    input_records = _mapping(receipt.get("inputs"), "promotion_source_inputs")
    if set(input_records) != set(EXPECTED_INPUTS):
        _fail("promotion_source_input_set")
    for name, record in input_records.items():
        _fingerprint_record(
            root,
            record,
            code_prefix=f"promotion_source_{name}",
        )
    derived = _mapping(
        receipt.get("derived_inputs"),
        "promotion_derived_inputs",
    )
    if set(derived) != {
        "comparison_report",
        "comparison_receipt",
        "results_markdown",
        "results_json",
    }:
        _fail("promotion_derived_input_set")
    for name, record in derived.items():
        _fingerprint_record(
            root,
            record,
            code_prefix=f"promotion_derived_{name}",
        )

    pending = _mapping(
        receipt.get("pending_contract"),
        "promotion_pending_contract",
    )
    if pending.get("preserved") is not True:
        _fail("promotion_pending_not_preserved")
    _sha(pending.get("markers_sha256"), "promotion_pending_hash")
    if set(pending) != {
        "preserved",
        "marker_count",
        "markers_sha256",
    }:
        _fail("promotion_pending_keys")
    marker_count = pending.get("marker_count")
    if (
        isinstance(marker_count, bool)
        or not isinstance(marker_count, int)
        or marker_count < 1
    ):
        _fail("promotion_pending_count")

    outputs = _mapping(receipt.get("outputs"), "promotion_outputs")
    expected_outputs = {
        "manuscript_candidate",
        "evidence_map_candidate",
        "evidence_map_validation",
        "evidence_map_additions",
        "candidate_validation",
    }
    if set(outputs) != expected_outputs:
        _fail("promotion_output_set")
    output_files = {
        name: _fingerprint_record(
            root,
            record,
            code_prefix=f"promotion_output_{name}",
        )
        for name, record in outputs.items()
    }
    if any(
        item.path.parent != receipt_file.path.parent for item in output_files.values()
    ):
        _fail("promotion_output_generation_paths")
    try:
        validation = results._load_json(  # noqa: SLF001
            output_files["candidate_validation"].path
        )
    except results.ResultsValidationError as exc:
        _fail(f"promotion_validation_{exc.code}")
    if validation.get("status") != "passed":
        _fail("promotion_candidate_validation_status")
    try:
        manuscript = output_files["manuscript_candidate"].path.read_text(
            encoding="utf-8"
        )
    except (OSError, UnicodeError):
        _fail("promotion_candidate_manuscript_read")
    targets = _mapping(receipt.get("targets"), "promotion_targets")
    if set(targets) != {
        "manuscript",
        "evidence_map",
        "insert_before_pending_marker",
    }:
        _fail("promotion_target_keys")
    for name, expected_path in (
        ("manuscript", CANONICAL_MANUSCRIPT),
        ("evidence_map", CANONICAL_EVIDENCE_MAP),
    ):
        record = _mapping(
            targets.get(name),
            f"promotion_target_{name}",
        )
        if set(record) != {"path", "sha256", "size_bytes"}:
            _fail(f"promotion_target_{name}_keys")
        if record.get("path") != expected_path:
            _fail(f"promotion_target_{name}_path")
        _sha(record.get("sha256"), f"promotion_target_{name}_sha")
        size = record.get("size_bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            _fail(f"promotion_target_{name}_size")
    marker = _string(
        targets.get("insert_before_pending_marker"),
        "promotion_target_pending_marker",
    )
    if marker != EXPECTED_PENDING_MARKER:
        _fail("promotion_target_pending_marker_value")
    if (
        manuscript.count(BEGIN_MARKER) != 1
        or manuscript.count(END_MARKER) != 1
        or manuscript.count(marker) != 1
    ):
        _fail("promotion_candidate_manuscript_contract")
    validation_manuscript = _mapping(
        validation.get("manuscript"),
        "promotion_validation_manuscript",
    )
    validation_map = _mapping(
        validation.get("evidence_map"),
        "promotion_validation_evidence_map",
    )
    if (
        validation_manuscript.get("path")
        != output_files["manuscript_candidate"].path.relative_to(root).as_posix()
        or validation_manuscript.get("sha256")
        != output_files["manuscript_candidate"].sha256
        or validation_map.get("path")
        != output_files["evidence_map_validation"].path.relative_to(root).as_posix()
        or validation_map.get("sha256")
        != output_files["evidence_map_validation"].sha256
    ):
        _fail("promotion_candidate_validation_binding")
    validation_pending = _mapping(
        validation.get("pending_contract"),
        "promotion_validation_pending",
    )
    if validation_pending.get("marker_count") != marker_count or validation_pending.get(
        "markers_sha256"
    ) != pending.get("markers_sha256"):
        _fail("promotion_candidate_pending_binding")
    return {
        "commit": commit,
        "receipt": receipt,
        "outputs": output_files,
        "candidate_validation": validation,
    }


def prepare_wavlm_paper_promotion(
    *,
    manifest_path: str | Path,
    workspace_root: str | Path,
    output_root: str | Path,
) -> Mapping[str, Any]:
    """Validate immutable evidence and publish a review-only candidate bundle."""

    root = Path(workspace_root).resolve(strict=True)
    manifest_file, manifest = _load_ready_manifest(manifest_path, root=root)
    inputs, targets = _validate_ready_inputs(manifest, root=root)
    active_manuscript, active_report, active_evidence = _validate_active_paper(
        root=root,
        targets=targets,
    )
    lineage = _validate_lineage(root=root, inputs=inputs)
    _assert_promotion_sources_unchanged(
        manifest=manifest_file,
        targets=targets,
        lineage=lineage,
    )

    try:
        output_base = results._candidate_inside_workspace(  # noqa: SLF001
            root,
            output_root,
            must_exist=False,
        )
    except results.ResultsValidationError as exc:
        _fail(f"output_{exc.code}")
    if output_base.exists() and (output_base.is_symlink() or not output_base.is_dir()):
        _fail("output_root_not_directory")
    output_relative = output_base.relative_to(root)
    if output_relative.parts[: len(OUTPUT_PREFIX)] != OUTPUT_PREFIX:
        _fail("output_root_not_reproducibility")
    if output_base in {
        *(item.path for item in inputs.values()),
        targets.manuscript.path,
        targets.evidence_map.path,
    }:
        _fail("output_collides_with_input")

    generation_material = {
        "manifest_sha256": manifest_file.sha256,
        "inputs": {
            name: _relative_record(root, item) for name, item in sorted(inputs.items())
        },
        "manuscript_sha256": targets.manuscript.sha256,
        "evidence_map_sha256": targets.evidence_map.sha256,
        "results_generation_id": lineage.output_commit.get("generation_id"),
        "pending_marker": targets.pending_marker,
    }
    generation_id = hashlib.sha256(
        _canonical_json_bytes(generation_material)
    ).hexdigest()
    generation_root = output_base / "generations" / generation_id
    commit_path = output_base / "commit.json"
    if generation_root.exists():
        _fail("promotion_generation_exists")
    if commit_path.exists() and (commit_path.is_symlink() or not commit_path.is_file()):
        _fail("promotion_commit_not_regular_file")

    (
        manuscript_bytes,
        canonical_map_bytes,
        validation_map_bytes,
        additions_bytes,
    ) = _prepare_candidate_documents(
        root=root,
        targets=targets,
        active_manuscript=active_manuscript,
        active_evidence=active_evidence,
        lineage=lineage,
        generation_root=generation_root,
    )
    files = {
        "manuscript_candidate": (
            generation_root / "manuscript.candidate.md",
            manuscript_bytes,
        ),
        "evidence_map_candidate": (
            generation_root / "evidence-map.candidate.yaml",
            canonical_map_bytes,
        ),
        "evidence_map_validation": (
            generation_root / "evidence-map.validation.yaml",
            validation_map_bytes,
        ),
        "evidence_map_additions": (
            generation_root / "evidence-map.additions.yaml",
            additions_bytes,
        ),
    }
    created: list[Path] = []
    commit_bytes: bytes | None = None
    try:
        for path, value in files.values():
            _write_new_file(path, value)
            created.append(path)
        candidate_validation = validate_evidence_map(
            files["evidence_map_validation"][0],
            workspace_root=root,
        )
        if candidate_validation.get("status") != "passed":
            _fail("candidate_evidence_not_passed")
        pending = _mapping(
            candidate_validation.get("pending_contract"),
            "candidate_pending_contract",
        )
        active_pending = _mapping(
            active_report.get("pending_contract"),
            "active_pending_contract",
        )
        if pending.get("marker_count") != active_pending.get(
            "marker_count"
        ) or pending.get("markers_sha256") != active_pending.get("markers_sha256"):
            _fail("candidate_pending_contract_changed")

        validation_bytes = (
            json.dumps(
                candidate_validation,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        validation_path = generation_root / "candidate-validation.json"
        _write_new_file(validation_path, validation_bytes)
        created.append(validation_path)

        output_records = {
            name: _relative_record(root, _fingerprint_bytes(path, value))
            for name, (path, value) in files.items()
        }
        output_records["candidate_validation"] = _relative_record(
            root,
            _fingerprint_bytes(validation_path, validation_bytes),
        )
        receipt: dict[str, Any] = {
            "schema_version": RECEIPT_SCHEMA,
            "status": "completed",
            "generation_id": generation_id,
            "manifest": _relative_record(root, manifest_file),
            "inputs": {
                name: _relative_record(root, item)
                for name, item in sorted(inputs.items())
            },
            "derived_inputs": {
                "comparison_report": _relative_record(
                    root,
                    lineage.comparison.report_file,
                ),
                "comparison_receipt": _relative_record(
                    root,
                    lineage.comparison.receipt_file,
                ),
                "results_markdown": _relative_record(
                    root,
                    lineage.output_markdown,
                ),
                "results_json": _relative_record(
                    root,
                    lineage.output_json,
                ),
            },
            "targets": {
                "manuscript": _relative_record(root, targets.manuscript),
                "evidence_map": _relative_record(root, targets.evidence_map),
                "insert_before_pending_marker": targets.pending_marker,
            },
            "pending_contract": {
                "preserved": True,
                "marker_count": pending.get("marker_count"),
                "markers_sha256": pending.get("markers_sha256"),
            },
            "canonical_files_modified": False,
            "outputs": output_records,
        }
        receipt["binding_sha256"] = hashlib.sha256(
            _canonical_json_bytes(receipt)
        ).hexdigest()
        receipt_bytes = (
            json.dumps(
                receipt,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        receipt_path = generation_root / "promotion-receipt.json"
        _write_new_file(receipt_path, receipt_bytes)
        created.append(receipt_path)

        _assert_promotion_sources_unchanged(
            manifest=manifest_file,
            targets=targets,
            lineage=lineage,
        )
        commit: dict[str, Any] = {
            "schema_version": COMMIT_SCHEMA,
            "status": "committed",
            "generation_id": generation_id,
            "promotion_receipt": _relative_record(
                root,
                _fingerprint_bytes(receipt_path, receipt_bytes),
            ),
        }
        commit["binding_sha256"] = hashlib.sha256(
            _canonical_json_bytes(commit)
        ).hexdigest()
        commit_bytes = (
            json.dumps(
                commit,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        _atomic_replace(commit_path, commit_bytes)
        loaded = load_committed_wavlm_paper_promotion(
            commit_path=commit_path,
            workspace_root=root,
        )
        loaded_commit = _mapping(
            loaded.get("commit"),
            "published_promotion_commit",
        )
        if loaded_commit.get("generation_id") != generation_id:
            _fail("published_promotion_generation")
    except BaseException:
        commit_points_to_generation = False
        if commit_bytes is not None:
            try:
                commit_points_to_generation = commit_path.read_bytes() == commit_bytes
            except OSError:
                pass
        if not commit_points_to_generation:
            _remove_generation(created, generation_root)
        raise

    return {
        "status": "completed",
        "generation_id": generation_id,
        "commit": commit_path.relative_to(root).as_posix(),
        "candidate_manuscript": files["manuscript_candidate"][0]
        .relative_to(root)
        .as_posix(),
        "candidate_evidence_map": files["evidence_map_candidate"][0]
        .relative_to(root)
        .as_posix(),
        "evidence_map_additions": files["evidence_map_additions"][0]
        .relative_to(root)
        .as_posix(),
        "canonical_files_modified": False,
        "pending_contract_preserved": True,
    }


__all__ = [
    "BEGIN_MARKER",
    "COMMIT_SCHEMA",
    "END_MARKER",
    "MANIFEST_SCHEMA",
    "PaperPromotionError",
    "RECEIPT_SCHEMA",
    "ValidatedLineage",
    "load_committed_wavlm_paper_promotion",
    "prepare_wavlm_paper_promotion",
]
