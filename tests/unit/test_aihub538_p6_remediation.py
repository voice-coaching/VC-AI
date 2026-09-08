from __future__ import annotations

from dataclasses import replace
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat

import pytest

from voice_coach.visual_feedback.adapters import (
    aihub538_p6_remediation_publish as publisher,
)
from voice_coach.visual_feedback.adapters.aihub538_p6_remediation_publish import (
    AIHUB538_P6_REMEDIATION_RECEIPT_NAME,
    Aihub538P6RemediationPublishError,
    load_aihub538_p6_remediation_receipt_descriptor,
    publish_aihub538_p6_remediation_receipt,
)
from voice_coach.visual_feedback.adapters.aihub538_decision_calibration_publish import (
    load_aihub538_decision_calibration_failure_artifact,
)
from voice_coach.visual_feedback.adapters.aihub538_p6_remediation_label_download import (
    AIHUB538_P6_REMEDIATION_LABEL_TRANSACTION_NAME,
    load_aihub538_p6_remediation_label_transaction_descriptor,
    publish_aihub538_p6_remediation_label_transaction,
)
from voice_coach.visual_feedback.models.aihub538_label_capacity import (
    Aihub538LabelCapacityAggregate,
    Aihub538LabelCapacityItem,
)
from voice_coach.visual_feedback.models import (
    aihub538_p6_remediation as remediation_model,
)
from voice_coach.visual_feedback.models.aihub538_p6_remediation import (
    AIHUB538_P6_ACTUAL_V3_ADMISSION_MANIFEST_SHA256,
    AIHUB538_P6_ACTUAL_V3_ADMISSION_RECEIPT_SHA256,
    AIHUB538_P6_ACTUAL_V3_CAPACITY_RECEIPT_SHA256,
    AIHUB538_P6_ACTUAL_V3_EVALUATOR_CODE_SHA256,
    AIHUB538_P6_ACTUAL_V3_FAILURE_ARTIFACT_SHA256,
    AIHUB538_P6_ACTUAL_V3_FAILURE_SUMMARY_SHA256,
    AIHUB538_P6_ACTUAL_V3_IDENTITY_NAMESPACE_COMMITMENT_SHA256,
    AIHUB538_P6_ACTUAL_V3_MEMBER_NAMESPACE_COMMITMENT_SHA256,
    AIHUB538_P6_ACTUAL_V3_SOURCE_MANIFEST_SHA256,
    AIHUB538_P6_ACTUAL_V3_DESIGN_LABEL_LINEAGE,
    AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS,
    AIHUB538_P6_REMEDIATION_DESIGN_USED_ROWS,
    AIHUB538_P6_REMEDIATION_POLICY,
    AIHUB538_P6_REMEDIATION_POLICY_SHA256,
    Aihub538P6RemediationAuditReceipt,
    aihub538_p6_full_member_tuple_commitment_sha256,
    aihub538_p6_relative_path_hmac_set_commitment_sha256,
    require_aihub538_p6_actual_v3_design_label_lineage,
    require_aihub538_p6_actual_v3_failure_artifact,
)
from voice_coach.visual_feedback.models.aihub538_p6_remediation_label_download import (
    Aihub538P6RemediationLabelDownloadTransaction,
    issue_aihub538_p6_remediation_label_download_transaction,
)
from voice_coach.visual_feedback.models.aihub538_decision_calibration import (
    Aihub538DecisionCalibrationFailureArtifact,
    Aihub538DecisionCalibrationFailureArtifactV2,
)
from voice_coach.visual_feedback.models.integrity import sha256_document
from voice_coach.visual_feedback.services.aihub538_p6_remediation import (
    Aihub538P6RemediationAuditRequest,
    Aihub538P6RemediationAuditService,
    Aihub538P6RemediationError,
)
from voice_coach.visual_feedback.services.aihub538_p6_remediation_label_download import (
    Aihub538P6RemediationLabelDownloadError,
    Aihub538P6RemediationLabelDownloadRequest,
    Aihub538P6RemediationLabelDownloadService,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _aggregate(
    name: str,
    actors: tuple[str, ...],
    *,
    samples_per_actor_angle: int = 2,
    unavailable: frozenset[tuple[str, str, int]] = frozenset(),
) -> Aihub538LabelCapacityAggregate:
    items: list[Aihub538LabelCapacityItem] = []
    paths: set[str] = set()
    for actor in actors:
        for angle in "ABCDEFGHI":
            for index in range(samples_per_actor_angle):
                marker = f"{name}:{actor}:{angle}:{index}"
                items.append(
                    Aihub538LabelCapacityItem(
                        sample_sha256=_digest(f"sample:{marker}"),
                        angle=angle,
                        actor_token_sha256=_digest(f"actor:{actor}"),
                        attempt_token_sha256=_digest(f"attempt:{marker}"),
                        metadata_bbox_observation_available=(
                            (actor, angle, index) not in unavailable
                        ),
                    )
                )
                paths.add(_digest(f"path:{marker}"))
    return Aihub538LabelCapacityAggregate(
        label_archive_sha256=_digest(f"archive:{name}"),
        label_content_root_sha256=_digest(f"content:{name}"),
        identity_namespace_commitment_sha256=(
            AIHUB538_P6_ACTUAL_V3_IDENTITY_NAMESPACE_COMMITMENT_SHA256
        ),
        member_namespace_commitment_sha256=(
            AIHUB538_P6_ACTUAL_V3_MEMBER_NAMESPACE_COMMITMENT_SHA256
        ),
        items=tuple(items),
        relative_path_hmac_sha256=frozenset(paths),
    )


def _design() -> tuple[Aihub538LabelCapacityAggregate, ...]:
    return (
        _aggregate("tl174", ("design-a",)),
        _aggregate("tl175", ("design-b",)),
        _aggregate("tl176", ("design-c",)),
    )


def _synthetic_design_lineage() -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "label_role_component": row["label_role_component"],
            "label_file_key": row["label_file_key"],
            "label_archive_sha256": aggregate.label_archive_sha256,
            "label_content_root_sha256": aggregate.label_content_root_sha256,
            "label_count": len(aggregate.items),
            "actor_count": len(
                {item.actor_token_sha256 for item in aggregate.items}
            ),
            "attempt_count": len(
                {item.attempt_token_sha256 for item in aggregate.items}
            ),
            "metadata_excluded_label_count": sum(
                not item.metadata_bbox_observation_available
                for item in aggregate.items
            ),
            "terminal_media_overrun_label_count": (
                aggregate.terminal_media_overrun_label_count
            ),
            "terminal_frame_tail_exception_label_count": (
                aggregate.terminal_frame_tail_exception_label_count
            ),
            "maximum_complete_tail_frames_observed": (
                aggregate.maximum_complete_tail_frames_observed
            ),
            "nonterminal_media_overrun_label_count": (
                aggregate.nonterminal_media_overrun_label_count
            ),
            "sentence_overlap_label_count": (
                aggregate.sentence_overlap_label_count
            ),
            "invalid_time_label_count": aggregate.invalid_time_label_count,
            "nonconforming_fps_label_count": (
                aggregate.nonconforming_fps_label_count
            ),
            "terminal_frame_tail_policy_sha256": (
                aggregate.terminal_frame_tail_policy_sha256
            ),
            "full_member_tuple_commitment_sha256": (
                aihub538_p6_full_member_tuple_commitment_sha256(aggregate)
            ),
            "relative_path_hmac_set_commitment_sha256": (
                aihub538_p6_relative_path_hmac_set_commitment_sha256(
                    aggregate
                )
            ),
            "face_bbox_observation_count": (
                aggregate.face_bbox_observation_count
            ),
            "lip_bbox_observation_count": aggregate.lip_bbox_observation_count,
            "face_reversed_bbox_unavailable_count": (
                aggregate.face_reversed_bbox_unavailable_count
            ),
            "lip_reversed_bbox_unavailable_count": (
                aggregate.lip_reversed_bbox_unavailable_count
            ),
            "reversed_bbox_affected_label_count": (
                aggregate.reversed_bbox_affected_label_count
            ),
        }
        for row, aggregate in zip(
            AIHUB538_P6_REMEDIATION_DESIGN_USED_ROWS,
            _design(),
            strict=True,
        )
    )


@pytest.fixture(autouse=True)
def _synthetic_design_binding_without_restricted_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep default unit tests hermetic while production pins stay unchanged."""

    monkeypatch.setattr(
        remediation_model,
        "AIHUB538_P6_ACTUAL_V3_DESIGN_LABEL_LINEAGE",
        _synthetic_design_lineage(),
    )
    original_open = os.open

    def guarded_open(path: object, *args: object, **kwargs: object) -> int:
        if isinstance(path, (str, bytes, os.PathLike)):
            rendered = os.fsdecode(os.fspath(path))
            if "/data/restricted/" in rendered:
                raise AssertionError("restricted fixture access forbidden")
        return original_open(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", guarded_open)


def _feasible(name: str = "tl177") -> Aihub538LabelCapacityAggregate:
    return _aggregate(name, tuple(f"fresh-{index}" for index in range(8)))


def _receipt() -> Aihub538P6RemediationAuditReceipt:
    return Aihub538P6RemediationAuditReceipt.issue(
        design_used_aggregates=_design(),
        evaluated_candidate_aggregates=(_feasible(),),
        design_failure_artifact=_synthetic_actual_v3_failure_artifact(),
    )


def _issue_chain(
    candidates: tuple[Aihub538LabelCapacityAggregate, ...],
) -> Aihub538P6RemediationAuditReceipt:
    prior = _issue_chain(candidates[:-1]) if len(candidates) > 1 else None
    return Aihub538P6RemediationAuditReceipt.issue(
        design_used_aggregates=_design(),
        evaluated_candidate_aggregates=candidates,
        design_failure_artifact=_synthetic_actual_v3_failure_artifact(),
        prior_prefix_receipt=prior,
    )


def _load_script(name: str):  # noqa: ANN202
    path = Path(__file__).resolve().parents[2] / "scripts" / name
    specification = importlib.util.spec_from_file_location(
        f"test_{path.stem}",
        path,
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_frozen_remediation_registry_is_separate_177_through_180() -> None:
    assert AIHUB538_P6_REMEDIATION_POLICY["policy_state"] == "frozen"
    assert len(AIHUB538_P6_REMEDIATION_POLICY_SHA256) == 64
    assert [
        item["label_role_component"]
        for item in AIHUB538_P6_REMEDIATION_DESIGN_USED_ROWS
    ] == [
        "TL174",
        "TL175",
        "TL176",
    ]
    assert [
        item["label_role_component"]
        for item in AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS
    ] == [
        "TL177",
        "TL178",
        "TL179",
        "TL180",
    ]
    assert [
        item["label_file_key"]
        for item in AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS
    ] == [
        "54728",
        "54729",
        "54730",
        "54732",
    ]
    assert [
        item["source_file_key"]
        for item in AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS
    ] == [
        "54947",
        "54948",
        "54949",
        "54951",
    ]
    assert not {"55082", "55114"} & {
        value
        for row in AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS
        for value in row.values()
    }
    assert AIHUB538_P6_REMEDIATION_POLICY["promotion_authorized"] is False
    assert AIHUB538_P6_REMEDIATION_POLICY["locked_partition_accessed"] is False


def test_production_actual_v3_pins_remain_frozen_without_restricted_io() -> None:
    assert AIHUB538_P6_ACTUAL_V3_DESIGN_LABEL_LINEAGE[0][
        "full_member_tuple_commitment_sha256"
    ] == "8acefb077d107f5061a1100266bd45d484cb200332dd079b5eb6a08722a9205e"
    assert AIHUB538_P6_ACTUAL_V3_DESIGN_LABEL_LINEAGE[1][
        "relative_path_hmac_set_commitment_sha256"
    ] == "a607500b53a2d3df6eb3ba87fbe18aac3e986c5f62eff50ec60bc9cd90ca6d32"
    assert AIHUB538_P6_ACTUAL_V3_DESIGN_LABEL_LINEAGE[2][
        "metadata_excluded_label_count"
    ] == 2
    assert AIHUB538_P6_REMEDIATION_POLICY[
        "design_used_label_lineage"
    ] == list(AIHUB538_P6_ACTUAL_V3_DESIGN_LABEL_LINEAGE)


def test_synthetic_design_fixture_exercises_every_lineage_pin() -> None:
    design = _design()
    require_aihub538_p6_actual_v3_design_label_lineage(design)
    receipt = _receipt()

    assert tuple(
        item.to_json_object() for item in receipt.design_used_lineage
    ) == _synthetic_design_lineage()
    assert {
        item.identity_namespace_commitment_sha256 for item in design
    } == {AIHUB538_P6_ACTUAL_V3_IDENTITY_NAMESPACE_COMMITMENT_SHA256}
    assert {
        item.member_namespace_commitment_sha256 for item in design
    } == {AIHUB538_P6_ACTUAL_V3_MEMBER_NAMESPACE_COMMITMENT_SHA256}
    assert set(AIHUB538_P6_ACTUAL_V3_DESIGN_LABEL_LINEAGE[0]) == {
        "label_role_component",
        "label_file_key",
        "label_archive_sha256",
        "label_content_root_sha256",
        "label_count",
        "actor_count",
        "attempt_count",
        "metadata_excluded_label_count",
        "terminal_media_overrun_label_count",
        "terminal_frame_tail_exception_label_count",
        "maximum_complete_tail_frames_observed",
        "nonterminal_media_overrun_label_count",
        "sentence_overlap_label_count",
        "invalid_time_label_count",
        "nonconforming_fps_label_count",
        "terminal_frame_tail_policy_sha256",
        "full_member_tuple_commitment_sha256",
        "relative_path_hmac_set_commitment_sha256",
        "face_bbox_observation_count",
        "lip_bbox_observation_count",
        "face_reversed_bbox_unavailable_count",
        "lip_reversed_bbox_unavailable_count",
        "reversed_bbox_affected_label_count",
    }


def test_receipt_is_exact_digest_bound_and_privacy_safe() -> None:
    receipt = _receipt()
    document = receipt.to_json_object()

    assert document["status"] == "completed_first_feasible_prefix"
    assert document["evaluated_label_prefix"] == ["TL177"]
    assert document["authorized_label_file_key_prefix"] == ["54728"]
    assert document["authorized_source_file_key_prefix"] == ["54947"]
    assert document["source_download_authorized"] is True
    assert document["prior_design_actor_intersection_count"] == 0
    assert document["prior_design_attempt_intersection_count"] == 0
    assert document["promotion_authorized"] is False
    assert document["locked_partition_accessed"] is False
    assert document["raw_identifiers_serialized"] is False
    assert document["sample_digests_serialized"] is False
    assert receipt.receipt_sha256 == sha256_document(receipt.unsigned_json_object())
    assert Aihub538P6RemediationAuditReceipt.from_json_object(document) == receipt

    serialized = json.dumps(document, sort_keys=True)
    for private_marker in (
        "design-a",
        "fresh-0",
        "actor:",
        "attempt:",
        "sample:",
        "path:",
    ):
        assert private_marker not in serialized


def test_actual_v3_design_artifact_binding_rejects_untyped_substitute() -> None:
    with pytest.raises(ValueError, match="actual_v3_failure_required"):
        Aihub538P6RemediationAuditReceipt.issue(
            design_used_aggregates=_design(),
            evaluated_candidate_aggregates=(_feasible(),),
            design_failure_artifact="not-a-typed-artifact",  # type: ignore[arg-type]
        )

    document = _receipt().to_json_object()
    document["design_failure_artifact_receipt_sha256"] = _digest(
        "substitute-v3"
    )
    unsigned = dict(document)
    unsigned.pop("receipt_sha256")
    document["receipt_sha256"] = sha256_document(unsigned)
    with pytest.raises(ValueError, match="receipt_invalid"):
        Aihub538P6RemediationAuditReceipt.from_json_object(document)


@pytest.mark.parametrize(
    "field",
    ("label_archive_sha256", "label_content_root_sha256"),
)
def test_actual_v3_design_label_lineage_rejects_synthetic_substitution(
    field: str,
) -> None:
    design = list(_design())
    design[0] = replace(design[0], **{field: _digest(f"forged-{field}")})
    with pytest.raises(ValueError, match="actual_v3_design_lineage_invalid"):
        Aihub538P6RemediationAuditReceipt.issue(
            design_used_aggregates=tuple(design),
            evaluated_candidate_aggregates=(_feasible(),),
            design_failure_artifact=_synthetic_actual_v3_failure_artifact(),
        )

    document = _receipt().to_json_object()
    document["design_used_lineage"][0][field] = _digest(
        f"forged-reloaded-{field}"
    )
    unsigned = dict(document)
    unsigned.pop("receipt_sha256")
    document["receipt_sha256"] = sha256_document(unsigned)
    with pytest.raises(
        ValueError,
        match="remediation_(?:receipt|lineage)_invalid",
    ):
        Aihub538P6RemediationAuditReceipt.from_json_object(document)


def _mutate_actual_design_aggregate(
    aggregate: Aihub538LabelCapacityAggregate,
    mutation: str,
) -> Aihub538LabelCapacityAggregate:
    if mutation == "items_sample":
        items = list(aggregate.items)
        items[0] = replace(items[0], sample_sha256=_digest("forged-sample"))
        return replace(aggregate, items=tuple(items))
    if mutation == "items_actor":
        items = list(aggregate.items)
        items[0] = replace(
            items[0],
            actor_token_sha256=_digest("forged-actor"),
        )
        return replace(aggregate, items=tuple(items))
    if mutation == "items_attempt":
        items = list(aggregate.items)
        items[0] = replace(
            items[0],
            attempt_token_sha256=_digest("forged-attempt"),
        )
        return replace(aggregate, items=tuple(items))
    if mutation == "items_bbox_availability":
        items = list(aggregate.items)
        items[0] = replace(
            items[0],
            metadata_bbox_observation_available=False,
        )
        return replace(aggregate, items=tuple(items))
    if mutation == "items_terminal_tail":
        items = list(aggregate.items)
        items[0] = replace(
            items[0],
            terminal_media_overrun=True,
            terminal_complete_tail_frames=1,
        )
        return replace(
            aggregate,
            items=tuple(items),
            terminal_media_overrun_label_count=1,
            maximum_complete_tail_frames_observed=1,
        )
    if mutation == "label_count":
        return replace(
            aggregate,
            items=aggregate.items[:-1],
            relative_path_hmac_sha256=frozenset(
                tuple(aggregate.relative_path_hmac_sha256)[:-1]
            ),
        )
    if mutation == "relative_path_hmac_sha256":
        paths = set(aggregate.relative_path_hmac_sha256)
        paths.remove(next(iter(paths)))
        paths.add(_digest("forged-relative-path"))
        return replace(
            aggregate,
            relative_path_hmac_sha256=frozenset(paths),
        )
    if mutation == "identity_namespace":
        return replace(
            aggregate,
            identity_namespace_commitment_sha256=_digest(
                "forged-identity-namespace"
            ),
        )
    if mutation == "member_namespace":
        return replace(
            aggregate,
            member_namespace_commitment_sha256=_digest(
                "forged-member-namespace"
            ),
        )
    if mutation == "face_bbox_observation_count":
        return replace(
            aggregate,
            face_bbox_observation_count=(
                aggregate.face_bbox_observation_count + 1
            ),
        )
    if mutation == "lip_bbox_observation_count":
        return replace(
            aggregate,
            lip_bbox_observation_count=(
                aggregate.lip_bbox_observation_count + 1
            ),
        )
    if mutation == "reversed_bbox_counts":
        return replace(
            aggregate,
            lip_bbox_observation_count=1,
            lip_reversed_bbox_unavailable_count=1,
            reversed_bbox_affected_label_count=1,
        )
    raise AssertionError(mutation)


@pytest.mark.parametrize(
    "mutation",
    (
        "items_sample",
        "items_actor",
        "items_attempt",
        "items_bbox_availability",
        "items_terminal_tail",
        "label_count",
        "relative_path_hmac_sha256",
        "identity_namespace",
        "member_namespace",
        "face_bbox_observation_count",
        "lip_bbox_observation_count",
        "reversed_bbox_counts",
    ),
)
def test_model_rejects_actual_v3_private_aggregate_substitution(
    mutation: str,
) -> None:
    design = list(_design())
    design[0] = _mutate_actual_design_aggregate(design[0], mutation)
    with pytest.raises(ValueError, match="actual_v3_design_lineage_invalid"):
        Aihub538P6RemediationAuditReceipt.issue(
            design_used_aggregates=tuple(design),
            evaluated_candidate_aggregates=(_feasible(),),
            design_failure_artifact=_synthetic_actual_v3_failure_artifact(),
        )


@pytest.mark.parametrize(
    "field,replacement_value",
    (
        ("label_count", 61),
        ("actor_count", 2),
        ("attempt_count", 13),
        ("metadata_excluded_label_count", 1),
        ("terminal_media_overrun_label_count", 1),
        ("terminal_frame_tail_exception_label_count", 1),
        ("maximum_complete_tail_frames_observed", 11),
        ("sentence_overlap_label_count", 1),
        ("terminal_frame_tail_policy_sha256", _digest("forged-tail-policy")),
        ("full_member_tuple_commitment_sha256", _digest("forged-members")),
        (
            "relative_path_hmac_set_commitment_sha256",
            _digest("forged-paths"),
        ),
        ("face_bbox_observation_count", 556067),
        ("lip_bbox_observation_count", 556067),
        ("face_reversed_bbox_unavailable_count", 1),
        ("lip_reversed_bbox_unavailable_count", 1),
        ("reversed_bbox_affected_label_count", 1),
    ),
)
def test_reload_rejects_forged_actual_v3_member_lineage(
    field: str,
    replacement_value: object,
) -> None:
    document = _receipt().to_json_object()
    document["design_used_lineage"][0][field] = replacement_value
    unsigned = dict(document)
    unsigned.pop("receipt_sha256")
    document["receipt_sha256"] = sha256_document(unsigned)
    with pytest.raises(
        ValueError,
        match="remediation_(?:receipt|lineage)_invalid",
    ):
        Aihub538P6RemediationAuditReceipt.from_json_object(document)


def _synthetic_actual_v3_failure_artifact(
    *,
    source_manifest_sha256: str = AIHUB538_P6_ACTUAL_V3_SOURCE_MANIFEST_SHA256,
) -> Aihub538DecisionCalibrationFailureArtifactV2:
    summary = object.__new__(Aihub538DecisionCalibrationFailureArtifact)
    values = {
        "artifact_sha256": AIHUB538_P6_ACTUAL_V3_FAILURE_SUMMARY_SHA256,
        "reason_code": "aihub538_calibration_per_angle_stop_policy_failed",
        "source_manifest_sha256": source_manifest_sha256,
        "admission_receipt_sha256": (
            AIHUB538_P6_ACTUAL_V3_ADMISSION_RECEIPT_SHA256
        ),
        "admission_manifest_sha256": (
            AIHUB538_P6_ACTUAL_V3_ADMISSION_MANIFEST_SHA256
        ),
        "capacity_audit_receipt_sha256": (
            AIHUB538_P6_ACTUAL_V3_CAPACITY_RECEIPT_SHA256
        ),
        "evaluator_code_sha256": AIHUB538_P6_ACTUAL_V3_EVALUATOR_CODE_SHA256,
        "admitted_item_count": 480,
        "eligible_item_count": 477,
        "development_selected_count": 72,
        "calibration_selected_count": 72,
    }
    for name, value in values.items():
        object.__setattr__(summary, name, value)
    artifact = object.__new__(Aihub538DecisionCalibrationFailureArtifactV2)
    object.__setattr__(artifact, "failure_summary", summary)
    object.__setattr__(
        artifact,
        "artifact_sha256",
        AIHUB538_P6_ACTUAL_V3_FAILURE_ARTIFACT_SHA256,
    )
    return artifact


def test_actual_v3_artifact_predicate_pins_lineage_not_only_type_reason() -> None:
    artifact = _synthetic_actual_v3_failure_artifact()
    assert (
        require_aihub538_p6_actual_v3_failure_artifact(artifact)
        == AIHUB538_P6_ACTUAL_V3_FAILURE_ARTIFACT_SHA256
    )

    forged = _synthetic_actual_v3_failure_artifact(
        source_manifest_sha256=_digest("forged-source-manifest")
    )
    with pytest.raises(ValueError, match="actual_v3_failure_binding_invalid"):
        require_aihub538_p6_actual_v3_failure_artifact(forged)


def test_actual_v3_artifact_predicate_accepts_strict_loaded_release() -> None:
    artifact_path = (
        Path(__file__).resolve().parents[2]
        / "artifacts"
        / "visual_feedback"
        / "p6"
        / "aihub538-multishard-actual-run-v3"
        / (
            "aihub538_ts174_tl174_ts175_tl175_ts176_tl176_"
            "decision_calibration_failure_v2.json"
        )
    )
    artifact = load_aihub538_decision_calibration_failure_artifact(
        artifact_path
    )
    assert (
        require_aihub538_p6_actual_v3_failure_artifact(artifact)
        == AIHUB538_P6_ACTUAL_V3_FAILURE_ARTIFACT_SHA256
    )
    assert len(artifact.failure_summary.evaluator_code_sha256) == 64


def test_prefix_requires_four_selected_actors_and_exact_angle_quota() -> None:
    first = _aggregate("tl177", ("fresh-a", "fresh-b"))
    second = _aggregate(
        "tl178",
        tuple(f"fresh-extra-{index}" for index in range(6)),
    )
    receipt = _issue_chain((first, second))

    assert [item.is_feasible for item in receipt.prefix_decisions] == [False, True]
    final = receipt.prefix_decisions[-1]
    assert final.development_selected_actor_count == 4
    assert final.calibration_selected_actor_count == 4
    assert all(
        item.development_selected_count == 8
        and item.calibration_selected_count == 8
        for item in final.angle_receipts
    )
    assert receipt.to_json_object()["authorized_source_file_key_prefix"] == [
        "54947",
        "54948",
    ]


def test_infeasible_partial_prefix_authorizes_only_next_label_audit() -> None:
    small = _aggregate("small", ("fresh-a", "fresh-b"))
    partial = Aihub538P6RemediationAuditReceipt.issue(
        design_used_aggregates=_design(),
        evaluated_candidate_aggregates=(small,),
        design_failure_artifact=_synthetic_actual_v3_failure_artifact(),
    )
    document = partial.to_json_object()
    assert document["status"] == "completed_prefix_infeasible"
    assert document["source_download_authorized"] is False
    assert document["authorized_source_file_key_prefix"] == []
    assert document["next_label_role_component"] == "TL178"
    assert document["next_label_file_key"] == "54729"


def test_second_prefix_requires_exact_prior_infeasible_receipt() -> None:
    first = _aggregate("first", ("fresh-a", "fresh-b"))
    second = _aggregate(
        "second",
        tuple(f"fresh-extra-{index}" for index in range(6)),
    )
    with pytest.raises(ValueError, match="prior_prefix_receipt_required"):
        Aihub538P6RemediationAuditReceipt.issue(
            design_used_aggregates=_design(),
            evaluated_candidate_aggregates=(first, second),
            design_failure_artifact=_synthetic_actual_v3_failure_artifact(),
        )

    wrong_prior = _issue_chain(
        (_aggregate("different-first", ("fresh-a", "fresh-b")),)
    )
    with pytest.raises(ValueError, match="prior_prefix_receipt_chain_invalid"):
        Aihub538P6RemediationAuditReceipt.issue(
            design_used_aggregates=_design(),
            evaluated_candidate_aggregates=(first, second),
            design_failure_artifact=_synthetic_actual_v3_failure_artifact(),
            prior_prefix_receipt=wrong_prior,
        )


def test_post_feasible_prefix_access_fails_closed() -> None:
    first = _feasible("tl177-feasible")
    with pytest.raises(ValueError, match="post_feasible_prefix_accessed"):
        Aihub538P6RemediationAuditReceipt.issue(
            design_used_aggregates=_design(),
            evaluated_candidate_aggregates=(
                first,
                _aggregate("tl178-extra", ("new-a",)),
            ),
            design_failure_artifact=_synthetic_actual_v3_failure_artifact(),
            prior_prefix_receipt=_issue_chain((first,)),
        )


def test_all_four_infeasible_rows_exhaust_without_source_authorization() -> None:
    receipt = _issue_chain(
        tuple(
            _aggregate(f"tl{177 + index}", (f"fresh-{index}",))
            for index in range(4)
        )
    )

    document = receipt.to_json_object()
    assert document["status"] == "completed_ladder_exhausted_infeasible"
    assert [item.is_feasible for item in receipt.prefix_decisions] == [
        False,
        False,
        False,
        False,
    ]
    assert document["source_download_authorized"] is False
    assert document["authorized_source_file_key_prefix"] == []


def test_prior_v3_actor_or_attempt_overlap_fails_without_receipt() -> None:
    design = _design()
    candidate = _feasible()
    actor_overlap_item = replace(
        candidate.items[0],
        actor_token_sha256=design[0].items[0].actor_token_sha256,
    )
    actor_overlap = replace(
        candidate,
        items=(actor_overlap_item, *candidate.items[1:]),
    )
    with pytest.raises(ValueError, match="prior_design_overlap"):
        Aihub538P6RemediationAuditReceipt.issue(
            design_used_aggregates=design,
            evaluated_candidate_aggregates=(actor_overlap,),
            design_failure_artifact=_synthetic_actual_v3_failure_artifact(),
        )

    first = candidate.items[0]
    overlapping_attempt = replace(
        first,
        attempt_token_sha256=design[0].items[0].attempt_token_sha256,
    )
    attempt_overlap = replace(
        candidate,
        items=(overlapping_attempt, *candidate.items[1:]),
    )
    with pytest.raises(ValueError, match="prior_design_overlap"):
        Aihub538P6RemediationAuditReceipt.issue(
            design_used_aggregates=design,
            evaluated_candidate_aggregates=(attempt_overlap,),
            design_failure_artifact=_synthetic_actual_v3_failure_artifact(),
        )


class _FixtureAdapter:
    def __init__(
        self,
        *,
        role: str,
        aggregates: dict[str, Aihub538LabelCapacityAggregate],
        calls: list[str],
    ) -> None:
        self._role = role
        self._aggregates = aggregates
        self._calls = calls

    def audit(
        self,
        *,
        label_zip_descriptor: int,
        identity_hash_key: bytes,
    ) -> Aihub538LabelCapacityAggregate:
        del label_zip_descriptor
        assert identity_hash_key == b"k" * 32
        self._calls.append(self._role)
        return self._aggregates[self._role]


def _key_descriptor(tmp_path: Path) -> int:
    key = tmp_path / "identity.key"
    key.write_bytes(b"k" * 32)
    key.chmod(0o400)
    return os.open(key, os.O_RDONLY | os.O_NOFOLLOW)


def test_service_requires_two_transactions_and_never_opens_tl179(
    tmp_path: Path,
) -> None:
    aggregates = {
        "TL174": _design()[0],
        "TL175": _design()[1],
        "TL176": _design()[2],
        "TL177": _aggregate("tl177-small", ("fresh-a", "fresh-b")),
        "TL178": _aggregate(
            "tl178-enough",
            tuple(f"fresh-extra-{index}" for index in range(6)),
        ),
        "TL179": _aggregate("must-not-read", ("unused",)),
        "TL180": _aggregate("must-not-read-2", ("unused-2",)),
    }
    calls: list[str] = []
    service = Aihub538P6RemediationAuditService(
        adapter_factory=lambda spec: _FixtureAdapter(
            role=spec.label_role_component,
            aggregates=aggregates,
            calls=calls,
        )
    )
    descriptor = _key_descriptor(tmp_path)
    first_output = (tmp_path / "receipt-run-1").absolute()
    second_output = (tmp_path / "receipt-run-2").absolute()
    try:
        first = service.audit_first_feasible_prefix(
            Aihub538P6RemediationAuditRequest(
                design_used_label_zip_descriptors=(100, 101, 102),
                prior_candidate_label_zip_descriptors=(),
                new_candidate_label_zip_descriptor=103,
                prior_prefix_receipt_descriptor=None,
                identity_key_descriptor=descriptor,
                output_directory=first_output,
                design_failure_artifact=_synthetic_actual_v3_failure_artifact(),
                dataset_access_permission_confirmed=True,
            )
        )
        assert first.is_feasible is False
        prior_descriptor = os.open(
            first_output / AIHUB538_P6_REMEDIATION_RECEIPT_NAME,
            os.O_RDONLY | os.O_NOFOLLOW,
        )
        try:
            receipt = service.audit_first_feasible_prefix(
                Aihub538P6RemediationAuditRequest(
                    design_used_label_zip_descriptors=(100, 101, 102),
                    prior_candidate_label_zip_descriptors=(103,),
                    new_candidate_label_zip_descriptor=104,
                    prior_prefix_receipt_descriptor=prior_descriptor,
                    identity_key_descriptor=descriptor,
                    output_directory=second_output,
                    design_failure_artifact=(
                        _synthetic_actual_v3_failure_artifact()
                    ),
                    dataset_access_permission_confirmed=True,
                )
            )
        finally:
            os.close(prior_descriptor)
    finally:
        os.close(descriptor)

    assert calls == [
        "TL174",
        "TL175",
        "TL176",
        "TL177",
        "TL174",
        "TL175",
        "TL176",
        "TL177",
        "TL178",
    ]
    assert receipt.to_json_object()["evaluated_label_prefix"] == [
        "TL177",
        "TL178",
    ]
    receipt_path = second_output / AIHUB538_P6_REMEDIATION_RECEIPT_NAME
    assert stat.S_IMODE(second_output.stat().st_mode) == 0o700
    assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o400
    assert receipt_path.stat().st_nlink == 1
    loaded_descriptor = os.open(receipt_path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        loaded = load_aihub538_p6_remediation_receipt_descriptor(
            loaded_descriptor
        )
        assert loaded == receipt
    finally:
        os.close(loaded_descriptor)


def test_audit_cli_rejects_multiple_new_label_descriptors(
    tmp_path: Path,
) -> None:
    module = _load_script("audit_aihub538_p6_remediation_ladder.py")
    with pytest.raises(SystemExit) as raised:
        module.main(
            [
                "--design-label-fd",
                "100",
                "--design-label-fd",
                "101",
                "--design-label-fd",
                "102",
                "--new-label-fd",
                "103",
                "--new-label-fd",
                "104",
                "--identity-key-fd",
                "105",
                "--v3-failure-artifact",
                str(tmp_path / "not-opened.json"),
                "--output-directory",
                str(tmp_path / "not-created"),
                "--dataset-access-permission-confirmed",
            ]
        )
    assert raised.value.code == 2


def test_permission_false_prevents_key_or_label_access_and_output(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    service = Aihub538P6RemediationAuditService(
        adapter_factory=lambda spec: (_ for _ in ()).throw(
            AssertionError(spec.shard_id)
        )
    )
    output = (tmp_path / "forbidden-output").absolute()
    with pytest.raises(
        Aihub538P6RemediationError,
        match="permission_not_confirmed",
    ):
        service.audit_first_feasible_prefix(
            Aihub538P6RemediationAuditRequest(
                design_used_label_zip_descriptors=(100, 101, 102),
                prior_candidate_label_zip_descriptors=(),
                new_candidate_label_zip_descriptor=103,
                prior_prefix_receipt_descriptor=None,
                identity_key_descriptor=107,
                output_directory=output,
                design_failure_artifact=_synthetic_actual_v3_failure_artifact(),
                dataset_access_permission_confirmed=False,
            )
        )
    assert calls == []
    assert not output.exists()


@pytest.mark.parametrize(
    "artifact",
    (
        "public-digest-string",
        None,
    ),
)
def test_direct_service_rejects_untyped_design_evidence_before_access(
    tmp_path: Path,
    artifact: object,
) -> None:
    calls: list[str] = []
    service = Aihub538P6RemediationAuditService(
        adapter_factory=lambda spec: (_ for _ in ()).throw(
            AssertionError(spec.shard_id)
        )
    )
    output = (tmp_path / "untyped-evidence").absolute()
    with pytest.raises(
        Aihub538P6RemediationError,
        match="design_evidence_invalid",
    ):
        service.audit_first_feasible_prefix(
            Aihub538P6RemediationAuditRequest(
                design_used_label_zip_descriptors=(100, 101, 102),
                prior_candidate_label_zip_descriptors=(),
                new_candidate_label_zip_descriptor=103,
                prior_prefix_receipt_descriptor=None,
                identity_key_descriptor=104,
                output_directory=output,
                design_failure_artifact=artifact,  # type: ignore[arg-type]
                dataset_access_permission_confirmed=True,
            )
        )
    assert calls == []
    assert not output.exists()


def test_direct_service_revalidates_typed_actual_v3_artifact_before_access(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    forged = _synthetic_actual_v3_failure_artifact(
        source_manifest_sha256=_digest("forged-service-source-manifest")
    )
    output = (tmp_path / "forged-evidence").absolute()
    with pytest.raises(
        Aihub538P6RemediationError,
        match="actual_v3_failure_binding_invalid",
    ):
        Aihub538P6RemediationAuditService(
            adapter_factory=lambda spec: (_ for _ in ()).throw(
                AssertionError(spec.shard_id)
            )
        ).audit_first_feasible_prefix(
            Aihub538P6RemediationAuditRequest(
                design_used_label_zip_descriptors=(100, 101, 102),
                prior_candidate_label_zip_descriptors=(),
                new_candidate_label_zip_descriptor=103,
                prior_prefix_receipt_descriptor=None,
                identity_key_descriptor=104,
                output_directory=output,
                design_failure_artifact=forged,
                dataset_access_permission_confirmed=True,
            )
        )
    assert calls == []
    assert not output.exists()


def test_service_checks_exact_design_lineage_before_candidate_access(
    tmp_path: Path,
) -> None:
    aggregates = {
        "TL174": _aggregate("forged-tl174", ("design-a",)),
        "TL175": _design()[1],
        "TL176": _design()[2],
        "TL177": _feasible(),
    }
    calls: list[str] = []
    service = Aihub538P6RemediationAuditService(
        adapter_factory=lambda spec: _FixtureAdapter(
            role=spec.label_role_component,
            aggregates=aggregates,
            calls=calls,
        )
    )
    key_descriptor = _key_descriptor(tmp_path)
    output = (tmp_path / "forged-design-lineage").absolute()
    try:
        with pytest.raises(
            Aihub538P6RemediationError,
            match="actual_v3_design_lineage_invalid",
        ):
            service.audit_first_feasible_prefix(
                Aihub538P6RemediationAuditRequest(
                    design_used_label_zip_descriptors=(100, 101, 102),
                    prior_candidate_label_zip_descriptors=(),
                    new_candidate_label_zip_descriptor=103,
                    prior_prefix_receipt_descriptor=None,
                    identity_key_descriptor=key_descriptor,
                    output_directory=output,
                    design_failure_artifact=(
                        _synthetic_actual_v3_failure_artifact()
                    ),
                    dataset_access_permission_confirmed=True,
                )
            )
    finally:
        os.close(key_descriptor)
    assert calls == ["TL174", "TL175", "TL176"]
    assert not output.exists()


@pytest.mark.parametrize(
    "mutation",
    (
        "items_sample",
        "relative_path_hmac_sha256",
        "identity_namespace",
        "face_bbox_observation_count",
    ),
)
def test_injected_service_rejects_private_design_substitution_before_tl177(
    tmp_path: Path,
    mutation: str,
) -> None:
    design = _design()
    aggregates = {
        "TL174": _mutate_actual_design_aggregate(design[0], mutation),
        "TL175": design[1],
        "TL176": design[2],
        "TL177": _feasible(),
    }
    calls: list[str] = []
    service = Aihub538P6RemediationAuditService(
        adapter_factory=lambda spec: _FixtureAdapter(
            role=spec.label_role_component,
            aggregates=aggregates,
            calls=calls,
        )
    )
    key_descriptor = _key_descriptor(tmp_path)
    output = (tmp_path / f"private-substitution-{mutation}").absolute()
    try:
        with pytest.raises(
            Aihub538P6RemediationError,
            match="actual_v3_design_lineage_invalid",
        ):
            service.audit_first_feasible_prefix(
                Aihub538P6RemediationAuditRequest(
                    design_used_label_zip_descriptors=(100, 101, 102),
                    prior_candidate_label_zip_descriptors=(),
                    new_candidate_label_zip_descriptor=103,
                    prior_prefix_receipt_descriptor=None,
                    identity_key_descriptor=key_descriptor,
                    output_directory=output,
                    design_failure_artifact=(
                        _synthetic_actual_v3_failure_artifact()
                    ),
                    dataset_access_permission_confirmed=True,
                )
            )
    finally:
        os.close(key_descriptor)
    assert calls == ["TL174", "TL175", "TL176"]
    assert "TL177" not in calls
    assert not output.exists()


def test_strict_reload_rejects_forged_locked_key_even_with_new_digest(
    tmp_path: Path,
) -> None:
    document = _receipt().to_json_object()
    document["authorized_source_file_key_prefix"] = ["55082"]
    unsigned = dict(document)
    unsigned.pop("receipt_sha256")
    document["receipt_sha256"] = sha256_document(unsigned)
    path = tmp_path / "forged.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    path.chmod(0o400)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with pytest.raises(
            Aihub538P6RemediationPublishError,
            match="receipt_reload_failed",
        ):
            load_aihub538_p6_remediation_receipt_descriptor(descriptor)
    finally:
        os.close(descriptor)


def test_strict_reload_rejects_extra_schema_field_and_wrong_mode(
    tmp_path: Path,
) -> None:
    document = _receipt().to_json_object()
    document["extra"] = True
    path = tmp_path / "extra.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    path.chmod(0o400)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with pytest.raises(Aihub538P6RemediationPublishError):
            load_aihub538_p6_remediation_receipt_descriptor(descriptor)
    finally:
        os.close(descriptor)

    path.chmod(0o600)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with pytest.raises(Aihub538P6RemediationPublishError):
            load_aihub538_p6_remediation_receipt_descriptor(descriptor)
    finally:
        os.close(descriptor)


def test_strict_reload_rejects_integer_boolean_even_with_new_digest(
    tmp_path: Path,
) -> None:
    document = _receipt().to_json_object()
    document["promotion_authorized"] = 0
    unsigned = dict(document)
    unsigned.pop("receipt_sha256")
    document["receipt_sha256"] = sha256_document(unsigned)
    path = tmp_path / "integer-boolean.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    path.chmod(0o400)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with pytest.raises(Aihub538P6RemediationPublishError):
            load_aihub538_p6_remediation_receipt_descriptor(descriptor)
    finally:
        os.close(descriptor)


def test_strict_reload_maps_malformed_lineage_type_to_stable_error(
    tmp_path: Path,
) -> None:
    document = _receipt().to_json_object()
    document["design_used_lineage"][0]["label_role_component"] = 1
    unsigned = dict(document)
    unsigned.pop("receipt_sha256")
    document["receipt_sha256"] = sha256_document(unsigned)
    path = tmp_path / "malformed-lineage.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    path.chmod(0o400)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with pytest.raises(
            Aihub538P6RemediationPublishError,
            match="aihub538_remediation_receipt_reload_failed",
        ):
            load_aihub538_p6_remediation_receipt_descriptor(descriptor)
    finally:
        os.close(descriptor)


def test_publish_failure_removes_new_output_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = (tmp_path / "failed-publish").absolute()

    def fail_write(descriptor: int, payload: bytes) -> None:
        del descriptor, payload
        raise OSError

    monkeypatch.setattr(publisher, "_write_all", fail_write)
    with pytest.raises(Aihub538P6RemediationPublishError, match="publish_failed"):
        publish_aihub538_p6_remediation_receipt(
            receipt=_receipt(),
            output_directory=output,
        )
    assert not output.exists()


def test_publish_final_parent_identity_failure_removes_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = (tmp_path / "identity-race").absolute()
    original = publisher._require_same_directory_identity
    calls = 0

    def fail_final_parent(expected: os.stat_result, observed: os.stat_result) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError
        original(expected, observed)

    monkeypatch.setattr(
        publisher,
        "_require_same_directory_identity",
        fail_final_parent,
    )
    with pytest.raises(Aihub538P6RemediationPublishError, match="publish_failed"):
        publish_aihub538_p6_remediation_receipt(
            receipt=_receipt(),
            output_directory=output,
        )
    assert not output.exists()


def test_publish_never_overwrites_existing_output(tmp_path: Path) -> None:
    output = (tmp_path / "existing").absolute()
    output.mkdir(mode=0o700)
    sentinel = output / "sentinel"
    sentinel.write_text("owned", encoding="utf-8")
    with pytest.raises(Aihub538P6RemediationPublishError, match="publish_failed"):
        publish_aihub538_p6_remediation_receipt(
            receipt=_receipt(),
            output_directory=output,
        )
    assert sentinel.read_text(encoding="utf-8") == "owned"


def test_secure_download_guard_accepts_only_a_pair_in_selected_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = (tmp_path / "authorization").absolute()
    receipt_path = publish_aihub538_p6_remediation_receipt(
        receipt=_receipt(),
        output_directory=output,
    )
    descriptor = os.open(receipt_path, os.O_RDONLY | os.O_NOFOLLOW)
    module = _load_script("download_aihub_secure.py")
    monkeypatch.setenv(
        "VOICE_COACH_AIHUB538_REMEDIATION_SOURCE_AUTHORIZATION_FD",
        str(descriptor),
    )
    try:
        guard = module._aihub538_source_authorization_guard(
            argparse.Namespace(
                dataset_key="538",
                file_keys="54728,54947",
                destination=tmp_path / "unused",
                timeout_seconds=1.0,
            )
        )
        guard()
        with pytest.raises(
            module.DownloadError,
            match="remediation_source_authorization_invalid",
        ):
            module._aihub538_source_authorization_guard(
                argparse.Namespace(
                    dataset_key="538",
                    file_keys="54729,54948",
                    destination=tmp_path / "unused-2",
                    timeout_seconds=1.0,
                )
            )
        with pytest.raises(
            module.DownloadError,
            match="remediation_source_pair_not_authorized",
        ):
            module._aihub538_source_authorization_guard(
                argparse.Namespace(
                    dataset_key="538",
                    file_keys="54728,54948",
                    destination=tmp_path / "unused-3",
                    timeout_seconds=1.0,
                )
            )
    finally:
        os.close(descriptor)


@pytest.mark.parametrize(
    "file_keys",
    (
        "54726,54945",
        "54728,54947",
    ),
)
def test_ladder_source_keys_reject_non_538_dataset_before_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    file_keys: str,
) -> None:
    module = _load_script("download_aihub_secure.py")
    monkeypatch.delenv(
        "VOICE_COACH_AIHUB538_SOURCE_AUTHORIZATION_FD",
        raising=False,
    )
    monkeypatch.delenv(
        "VOICE_COACH_AIHUB538_REMEDIATION_SOURCE_AUTHORIZATION_FD",
        raising=False,
    )
    with pytest.raises(
        module.DownloadError,
        match="aihub538_source_file_key_dataset_mismatch",
    ):
        module._aihub538_source_authorization_guard(
            argparse.Namespace(
                dataset_key="999",
                file_keys=file_keys,
                destination=tmp_path / "unused",
                timeout_seconds=1.0,
            )
        )


def test_label_transaction_initial_authority_is_exact_private_and_source_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = (tmp_path / "tl177-download").absolute()
    destination.mkdir(mode=0o700)
    authority_directory = (tmp_path / "tl177-authority").absolute()
    transaction, transaction_path = (
        Aihub538P6RemediationLabelDownloadService().issue(
            Aihub538P6RemediationLabelDownloadRequest(
                download_destination_directory=destination,
                transaction_output_directory=authority_directory,
                prior_prefix_receipt_descriptor=None,
                dataset_access_permission_confirmed=True,
            )
        )
    )
    document = transaction.to_json_object()
    assert transaction.label_role_component == "TL177"
    assert transaction.label_file_key == "54728"
    assert document["requested_label_file_key_count"] == 1
    assert document["requested_source_file_key_count"] == 0
    assert document["source_download_authorized"] is False
    assert stat.S_IMODE(authority_directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(transaction_path.stat().st_mode) == 0o400
    assert transaction_path.name == AIHUB538_P6_REMEDIATION_LABEL_TRANSACTION_NAME

    descriptor = os.open(transaction_path, os.O_RDONLY | os.O_NOFOLLOW)
    module = _load_script("download_aihub_secure.py")
    monkeypatch.setenv(
        "VOICE_COACH_AIHUB538_REMEDIATION_LABEL_TRANSACTION_FD",
        str(descriptor),
    )
    try:
        assert (
            load_aihub538_p6_remediation_label_transaction_descriptor(
                descriptor
            )
            == transaction
        )
        stable = module._aihub538_source_authorization_guard(
            argparse.Namespace(
                dataset_key="538",
                file_keys="54728",
                destination=destination,
                timeout_seconds=1.0,
            )
        )
        stable()
        with pytest.raises(
            module.DownloadError,
            match="remediation_label_transaction_invalid",
        ):
            module._aihub538_source_authorization_guard(
                argparse.Namespace(
                    dataset_key="538",
                    file_keys="54729",
                    destination=destination,
                    timeout_seconds=1.0,
                )
            )
    finally:
        os.close(descriptor)

    forged = transaction.to_json_object()
    forged["requested_label_file_key_count"] = True
    unsigned = dict(forged)
    unsigned.pop("transaction_sha256")
    forged["transaction_sha256"] = sha256_document(unsigned)
    with pytest.raises(ValueError, match="transaction_invalid"):
        Aihub538P6RemediationLabelDownloadTransaction.from_json_object(forged)


def test_label_transaction_rejects_changed_destination_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bound = (tmp_path / "bound").absolute()
    other = (tmp_path / "other").absolute()
    bound.mkdir(mode=0o700)
    other.mkdir(mode=0o700)
    transaction = issue_aihub538_p6_remediation_label_download_transaction(
        prior_prefix_receipt=None,
        output_directory_device=bound.stat().st_dev,
        output_directory_inode=bound.stat().st_ino,
        dataset_access_permission_confirmed=True,
    )
    path = publish_aihub538_p6_remediation_label_transaction(
        transaction=transaction,
        output_directory=(tmp_path / "authority-binding").absolute(),
    )
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    module = _load_script("download_aihub_secure.py")
    monkeypatch.setenv(
        "VOICE_COACH_AIHUB538_REMEDIATION_LABEL_TRANSACTION_FD",
        str(descriptor),
    )
    try:
        with pytest.raises(
            module.DownloadError,
            match="remediation_label_transaction_invalid",
        ):
            module._aihub538_source_authorization_guard(
                argparse.Namespace(
                    dataset_key="538",
                    file_keys="54728",
                    destination=other,
                    timeout_seconds=1.0,
                )
            )
    finally:
        os.close(descriptor)


def test_label_transaction_tl178_requires_exact_infeasible_prior_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _issue_chain(
        (_aggregate("tl177-small", ("fresh-a", "fresh-b")),)
    )
    prior_path = publish_aihub538_p6_remediation_receipt(
        receipt=prior,
        output_directory=(tmp_path / "prior").absolute(),
    )
    prior_descriptor = os.open(prior_path, os.O_RDONLY | os.O_NOFOLLOW)
    destination = (tmp_path / "tl178-download").absolute()
    destination.mkdir(mode=0o700)
    try:
        transaction, transaction_path = (
            Aihub538P6RemediationLabelDownloadService().issue(
                Aihub538P6RemediationLabelDownloadRequest(
                    download_destination_directory=destination,
                    transaction_output_directory=(
                        tmp_path / "tl178-authority"
                    ).absolute(),
                    prior_prefix_receipt_descriptor=prior_descriptor,
                    dataset_access_permission_confirmed=True,
                )
            )
        )
    finally:
        os.close(prior_descriptor)
    assert transaction.label_role_component == "TL178"
    assert transaction.label_file_key == "54729"
    assert transaction.prior_prefix_receipt_sha256 == prior.receipt_sha256

    transaction_descriptor = os.open(
        transaction_path,
        os.O_RDONLY | os.O_NOFOLLOW,
    )
    module = _load_script("download_aihub_secure.py")
    monkeypatch.setenv(
        "VOICE_COACH_AIHUB538_REMEDIATION_LABEL_TRANSACTION_FD",
        str(transaction_descriptor),
    )
    monkeypatch.delenv(
        "VOICE_COACH_AIHUB538_REMEDIATION_PRIOR_PREFIX_RECEIPT_FD",
        raising=False,
    )
    try:
        with pytest.raises(
            module.DownloadError,
            match="remediation_label_transaction_invalid",
        ):
            module._aihub538_source_authorization_guard(
                argparse.Namespace(
                    dataset_key="538",
                    file_keys="54729",
                    destination=destination,
                    timeout_seconds=1.0,
                )
            )
        prior_descriptor = os.open(
            prior_path,
            os.O_RDONLY | os.O_NOFOLLOW,
        )
        monkeypatch.setenv(
            "VOICE_COACH_AIHUB538_REMEDIATION_PRIOR_PREFIX_RECEIPT_FD",
            str(prior_descriptor),
        )
        try:
            stable = module._aihub538_source_authorization_guard(
                argparse.Namespace(
                    dataset_key="538",
                    file_keys="54729",
                    destination=destination,
                    timeout_seconds=1.0,
                )
            )
            stable()
        finally:
            os.close(prior_descriptor)
    finally:
        os.close(transaction_descriptor)

    with pytest.raises(ValueError, match="prior_prefix_receipt_invalid"):
        issue_aihub538_p6_remediation_label_download_transaction(
            prior_prefix_receipt=_receipt(),
            output_directory_device=1,
            output_directory_inode=1,
            dataset_access_permission_confirmed=True,
        )


def test_label_transaction_permission_false_prevents_path_access(
    tmp_path: Path,
) -> None:
    output = (tmp_path / "must-not-exist").absolute()
    with pytest.raises(
        Aihub538P6RemediationLabelDownloadError,
        match="permission_not_confirmed",
    ):
        Aihub538P6RemediationLabelDownloadService().issue(
            Aihub538P6RemediationLabelDownloadRequest(
                download_destination_directory=(
                    tmp_path / "missing-destination"
                ).absolute(),
                transaction_output_directory=output,
                prior_prefix_receipt_descriptor=None,
                dataset_access_permission_confirmed=False,
            )
        )
    assert not output.exists()


def test_remediation_label_guard_rejects_non_538_and_missing_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("download_aihub_secure.py")
    monkeypatch.delenv(
        "VOICE_COACH_AIHUB538_REMEDIATION_LABEL_TRANSACTION_FD",
        raising=False,
    )
    with pytest.raises(
        module.DownloadError,
        match="remediation_label_file_key_dataset_mismatch",
    ):
        module._aihub538_source_authorization_guard(
            argparse.Namespace(
                dataset_key="999",
                file_keys="54728",
                destination=tmp_path,
                timeout_seconds=1.0,
            )
        )
    with pytest.raises(
        module.DownloadError,
        match="remediation_label_transaction_required",
    ):
        module._aihub538_source_authorization_guard(
            argparse.Namespace(
                dataset_key="538",
                file_keys="54729",
                destination=tmp_path,
                timeout_seconds=1.0,
            )
        )


def test_remediation_label_download_uses_bound_dirfd_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = (tmp_path / "bound-download").absolute()
    destination.mkdir(mode=0o700)
    transaction = issue_aihub538_p6_remediation_label_download_transaction(
        prior_prefix_receipt=None,
        output_directory_device=destination.stat().st_dev,
        output_directory_inode=destination.stat().st_ino,
        dataset_access_permission_confirmed=True,
    )
    transaction_path = publish_aihub538_p6_remediation_label_transaction(
        transaction=transaction,
        output_directory=(tmp_path / "bound-authority").absolute(),
    )
    descriptor = os.open(transaction_path, os.O_RDONLY | os.O_NOFOLLOW)
    module = _load_script("download_aihub_secure.py")

    class _Response:
        status = 200
        headers = {"Content-Length": "4"}

        def __init__(self) -> None:
            self._payload = bytearray(b"test")

        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def read(self, count: int) -> bytes:
            del count
            if not self._payload:
                return b""
            payload = bytes(self._payload)
            self._payload.clear()
            return payload

    monkeypatch.setenv("AIHUB_API_KEY", "not-a-real-key")
    monkeypatch.setenv(
        "VOICE_COACH_AIHUB538_REMEDIATION_LABEL_TRANSACTION_FD",
        str(descriptor),
    )
    monkeypatch.setattr(module, "urlopen", lambda *_args, **_kwargs: _Response())
    try:
        archive = module.download(
            argparse.Namespace(
                dataset_key="538",
                file_keys="54728",
                destination=destination,
                timeout_seconds=1.0,
            )
        )
    finally:
        os.close(descriptor)
    assert archive.read_bytes() == b"test"
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    receipt = destination / "download.receipt.json"
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o400
    assert module._AIHUB538_REMEDIATION_MAXIMUM_LABEL_DOWNLOAD_BYTES == (
        128 * 1024**3
    )


def test_remediation_downloader_runs_entire_exact_prefix_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    authorization_directory = (tmp_path / "authorization").absolute()
    receipt_path = publish_aihub538_p6_remediation_receipt(
        receipt=_receipt(),
        output_directory=authorization_directory,
    )
    descriptor = os.open(receipt_path, os.O_RDONLY | os.O_NOFOLLOW)
    destination_root = tmp_path / "downloads"
    destination_root.mkdir(mode=0o700)
    module = _load_script("download_aihub538_p6_remediation_sources.py")
    completed: set[Path] = set()
    commands: list[list[str]] = []

    def verify(*, destination: Path, shard_spec: object) -> bool:
        del shard_spec
        return destination in completed

    def run(command: list[str], **kwargs: object):  # noqa: ANN202
        commands.append(command)
        assert kwargs["pass_fds"] == (descriptor,)
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        assert environment[
            "VOICE_COACH_AIHUB538_REMEDIATION_SOURCE_AUTHORIZATION_FD"
        ] == str(descriptor)
        assert "VOICE_COACH_AIHUB538_SOURCE_AUTHORIZATION_FD" not in environment
        completed.add(Path(command[-1]))
        return module.subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module, "_verify_completed_pair_if_present", verify)
    monkeypatch.setattr(module.subprocess, "run", run)
    try:
        assert module.main(
            [
                "--authorization-receipt-fd",
                str(descriptor),
                "--destination-root",
                str(destination_root),
            ]
        ) == 0
    finally:
        os.close(descriptor)
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "completed_remediation_prefix_download"
    assert output["first_feasible_prefix_complete"] is True
    assert [command[3] for command in commands] == ["54728,54947"]
    assert output["completed_pairs"] == [
        {
            "existing_completed_download_reused": False,
            "file_keys": ["54728", "54947"],
            "shard_id": "ts177_tl177",
        }
    ]
