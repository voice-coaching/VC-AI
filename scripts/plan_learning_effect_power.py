#!/usr/bin/env python3
"""Plan H01 learning-effect power without inventing empirical estimates."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import hashlib
from itertools import product
import json
import math
from pathlib import Path
import os
from statistics import NormalDist
import sys
import tempfile
from typing import Any, NoReturn

import yaml  # type: ignore[import-untyped]


CONFIG_SCHEMA = "voice_coach.learning_effect_power_config.v1"
PILOT_SCHEMA = "voice_coach.learning_effect_pilot_estimates.v1"
OUTPUT_SCHEMA = "voice_coach.learning_effect_power_plan.v1"
SHA256_LENGTH = 64
MAX_INPUT_BYTES = 2 * 1024 * 1024
FORBIDDEN_PATH_PARTS = frozenset({"raw", "restricted"})
EXPECTED_CONTRASTS = (
    "C3_minus_C0_at_post_relative_to_pre",
    "C5_minus_C3_at_post_relative_to_pre",
)


class PowerPlanError(RuntimeError):
    """Raised when a planning input violates the preregistration contract."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: yaml.SafeLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    output: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in output:
            raise PowerPlanError("yaml_duplicate_key")
        output[key] = loader.construct_object(value_node, deep=deep)
    return output


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _fail(code: str) -> NoReturn:
    raise PowerPlanError(code)


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


def _integer(value: object, code: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(code)
    if minimum is not None and value < minimum:
        _fail(code)
    return value


def _number(
    value: object,
    code: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    strict_minimum: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(code)
    result = float(value)
    if not math.isfinite(result):
        _fail(code)
    if minimum is not None:
        if strict_minimum and result <= minimum:
            _fail(code)
        if not strict_minimum and result < minimum:
            _fail(code)
    if maximum is not None and result > maximum:
        _fail(code)
    return result


def _sha256(value: object, code: str) -> str:
    result = _string(value, code)
    if len(result) != SHA256_LENGTH or any(
        character not in "0123456789abcdef" for character in result
    ):
        _fail(code)
    return result


def _expect(value: object, expected: object, code: str) -> None:
    if value != expected:
        _fail(code)


def _path_is_safe(path: Path) -> bool:
    for part in path.parts:
        lowered = part.casefold()
        if (
            lowered in FORBIDDEN_PATH_PARTS
            or lowered.startswith(("raw_", "restricted_"))
            or lowered == ".env"
            or lowered.startswith(".env.")
        ):
            return False
    return True


def _safe_path(
    root: Path,
    configured: str | Path,
    *,
    must_exist: bool,
) -> Path:
    lexical = Path(configured)
    if not _path_is_safe(lexical):
        _fail("forbidden_path")
    candidate = lexical if lexical.is_absolute() else root / lexical
    if candidate.is_symlink():
        _fail("symlink_path")
    try:
        resolved = candidate.resolve(strict=must_exist)
        relative = resolved.relative_to(root)
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        _fail("path_outside_workspace_or_missing")
    if not _path_is_safe(relative):
        _fail("forbidden_path")
    return resolved


def _safe_input(root: Path, configured: str | Path) -> Path:
    path = _safe_path(root, configured, must_exist=True)
    if not path.is_file():
        _fail("input_not_regular_file")
    if path.stat().st_size > MAX_INPUT_BYTES:
        _fail("input_too_large")
    return path


def _safe_output(root: Path, configured: str | Path) -> Path:
    path = _safe_path(root, configured, must_exist=False)
    if path.exists() and (path.is_symlink() or not path.is_file()):
        _fail("output_not_regular_file")
    return path


def _file_sha256(path: Path) -> str:
    before = path.stat()
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        _fail("input_hash_failed")
    after = path.stat()
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        _fail("input_changed_while_hashing")
    return digest.hexdigest()


def _load_yaml(path: Path) -> Mapping[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = yaml.load(handle, Loader=_UniqueKeyLoader)
    except PowerPlanError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError):
        _fail("config_read_failed")
    return _mapping(value, "config_root")


def _no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            _fail("json_duplicate_key")
        output[key] = value
    return output


def _reject_json_constant(_: str) -> None:
    _fail("json_nonfinite")


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(
                handle,
                object_pairs_hook=_no_duplicate_object,
                parse_constant=_reject_json_constant,
            )
    except PowerPlanError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("pilot_read_failed")
    return _mapping(value, "pilot_root")


def _numeric_grid(
    value: object,
    code: str,
    *,
    minimum: float,
    maximum: float,
    strict_minimum: bool = False,
) -> tuple[float, ...]:
    items = _list(value, code)
    if not items:
        _fail(code)
    numbers = tuple(
        _number(
            item,
            code,
            minimum=minimum,
            maximum=maximum,
            strict_minimum=strict_minimum,
        )
        for item in items
    )
    if len(set(numbers)) != len(numbers) or list(numbers) != sorted(numbers):
        _fail(f"{code}_order")
    return numbers


def validate_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return only the numeric planning inputs."""

    _expect(config.get("schema_version"), CONFIG_SCHEMA, "config_schema")
    _expect(
        config.get("status"),
        "planning_only_no_empirical_pilot",
        "config_status",
    )
    external = _mapping(
        config.get("current_external_status"),
        "external_status",
    )
    required_external = {
        "irb_or_equivalent_approval_obtained",
        "participants_recruited",
        "learner_recordings_collected",
        "empirical_pilot_nuisance_estimates_available",
        "confirmatory_effect_estimate_available",
    }
    if set(external) != required_external or any(
        not isinstance(external.get(key), bool) for key in required_external
    ):
        _fail("external_status_contract")
    if external.get("confirmatory_effect_estimate_available") is not False:
        _fail("confirmatory_effect_must_not_drive_power")
    if external.get("empirical_pilot_nuisance_estimates_available") is True and (
        external.get("irb_or_equivalent_approval_obtained") is not True
        or external.get("participants_recruited") is not True
        or external.get("learner_recordings_collected") is not True
    ):
        _fail("empirical_pilot_status_inconsistent")
    if external.get("learner_recordings_collected") is True and (
        external.get("participants_recruited") is not True
        or external.get("irb_or_equivalent_approval_obtained") is not True
    ):
        _fail("learner_collection_status_inconsistent")
    if external.get("participants_recruited") is True and (
        external.get("irb_or_equivalent_approval_obtained") is not True
    ):
        _fail("participant_recruitment_without_approval")

    study = _mapping(config.get("study"), "study")
    for key, expected in (
        ("design", "two_stage_parallel_pairwise_randomized"),
        ("randomization_unit", "participant"),
        ("crossover_allowed", False),
        (
            "crossover_rejection_reason",
            "persistent_learning_and_retention_carryover",
        ),
        ("allocation_ratio", [1, 1]),
    ):
        _expect(study.get(key), expected, f"study_{key}")
    arms = _mapping(study.get("arms"), "study_arms")
    if set(arms) != {"C0", "C3", "C5"}:
        _fail("study_arm_set")
    stages = _list(study.get("stages"), "study_stages")
    expected_stage_contract = [
        ("stage_1_c0_vs_c3", "C3_minus_C0", ["C0", "C3"]),
        ("stage_2_c3_vs_c5", "C5_minus_C3", ["C3", "C5"]),
    ]
    if len(stages) != len(expected_stage_contract):
        _fail("study_stage_count")
    for raw_stage, (stage_id, contrast, arms_expected) in zip(
        stages,
        expected_stage_contract,
        strict=True,
    ):
        stage = _mapping(raw_stage, "study_stage")
        if (
            stage.get("id") != stage_id
            or stage.get("contrast") != contrast
            or stage.get("arms") != arms_expected
        ):
            _fail("study_stage_contract")
    stage_2_activation = _mapping(
        study.get("stage_2_activation"),
        "stage_2_activation",
    )
    _expect(
        stage_2_activation.get("based_on_unblinded_effect_estimate"),
        False,
        "stage_2_effect_activation",
    )
    if not _list(
        stage_2_activation.get("requirements"),
        "stage_2_activation_requirements",
    ):
        _fail("stage_2_activation_requirements")
    _expect(study.get("intervention_duration_weeks"), 2, "study_duration")
    assessments = _list(study.get("assessments"), "study_assessments")
    if [_mapping(item, "assessment").get("id") for item in assessments] != [
        "pre",
        "post",
        "retention",
    ]:
        _fail("study_assessment_order")
    if _mapping(assessments[1], "post").get("timing") != "end_of_week_2":
        _fail("post_timing")
    if _mapping(assessments[2], "retention").get("timing") != "four_weeks_after_post":
        _fail("retention_timing")

    outcomes = _mapping(config.get("outcomes"), "outcomes")
    primary = _mapping(outcomes.get("primary"), "primary_outcome")
    if (
        primary.get("id") != "blind_expert_comprehensibility"
        or primary.get("scale") != [1, 5]
        or primary.get("timepoint") != "post"
        or primary.get("estimand") != "between_arm_difference_in_pre_to_post_change"
    ):
        _fail("primary_outcome_contract")
    key_secondary = _list(
        outcomes.get("key_secondary"),
        "secondary_outcomes",
    )
    secondary_ids = {
        _mapping(item, "secondary_outcome").get("id") for item in key_secondary
    }
    if secondary_ids != {
        "blind_expert_comprehensibility_retention",
        "blind_expert_phone_accuracy",
        "blind_expert_fluency",
    }:
        _fail("secondary_outcome_contract")

    blinding = _mapping(config.get("blinding"), "blinding")
    _expect(
        blinding.get("outcome_raters_blinded_to"),
        ["condition", "timepoint", "participant_identity"],
        "rater_blinding",
    )
    if (
        _integer(
            blinding.get("minimum_independent_raters"),
            "minimum_raters",
            minimum=2,
        )
        < 2
    ):
        _fail("minimum_raters")
    for key in (
        "randomized_item_order_per_rater",
        "masked_file_names",
        "analyst_arm_labels_masked_until_data_lock",
    ):
        _expect(blinding.get(key), True, f"blinding_{key}")
    _expect(
        blinding.get("participant_blinding_feasible"),
        False,
        "participant_blinding",
    )

    analysis = _mapping(config.get("analysis"), "analysis")
    _expect(
        analysis.get("primary_population"),
        "intention_to_treat_as_randomized",
        "analysis_population",
    )
    _expect(
        analysis.get("primary_model"),
        "linear_mixed_effects_approximation",
        "analysis_model",
    )
    _expect(
        analysis.get("random_intercepts"),
        ["participant", "sentence", "rater"],
        "analysis_random_intercepts",
    )
    _expect(
        analysis.get("primary_contrasts"),
        list(EXPECTED_CONTRASTS),
        "analysis_primary_contrasts",
    )
    missingness = _mapping(analysis.get("missingness"), "missingness")
    _expect(
        missingness.get("primary"),
        "likelihood_based_mixed_model_using_all_observed_itt_data",
        "missingness_primary",
    )
    forbidden_missing = _list(
        missingness.get("forbidden"),
        "missingness_forbidden",
    )
    if set(forbidden_missing) != {
        "last_observation_carried_forward",
        "post_randomization_exclusion",
    }:
        _fail("missingness_forbidden")
    per_protocol = _mapping(
        analysis.get("per_protocol_sensitivity"),
        "per_protocol",
    )
    _expect(
        per_protocol.get("primary_claim_allowed"),
        False,
        "per_protocol_primary_claim",
    )

    multiplicity = _mapping(config.get("multiplicity"), "multiplicity")
    _expect(multiplicity.get("method"), "holm", "multiplicity_method")
    alpha = _number(
        multiplicity.get("alpha_two_sided"),
        "multiplicity_alpha",
        minimum=0.0,
        maximum=1.0,
        strict_minimum=True,
    )
    _expect(
        multiplicity.get("power_planning_family"),
        "primary_post",
        "power_family",
    )
    planning_alpha = _number(
        multiplicity.get("conservative_planning_alpha"),
        "planning_alpha",
        minimum=0.0,
        maximum=1.0,
        strict_minimum=True,
    )
    if not math.isclose(planning_alpha, alpha / len(EXPECTED_CONTRASTS)):
        _fail("planning_alpha_not_familywise_conservative")

    power = _mapping(config.get("power"), "power")
    target_power = _number(
        power.get("target_power"),
        "target_power",
        minimum=0.5,
        maximum=0.999,
    )
    _expect(power.get("test_sidedness"), "two_sided", "test_sidedness")
    _expect(
        power.get("approximation"),
        "two_arm_difference_in_repeated_change_scores",
        "power_approximation",
    )
    grid = _mapping(power.get("sensitivity_grid"), "sensitivity_grid")
    _expect(
        grid.get("provenance"),
        "assumed_sensitivity_without_empirical_pilot",
        "grid_provenance",
    )
    raw_effects = _numeric_grid(
        grid.get("raw_mean_difference_points"),
        "grid_raw_effect",
        minimum=0.0,
        maximum=4.0,
        strict_minimum=True,
    )
    standard_deviations = _numeric_grid(
        grid.get("participant_level_outcome_sd"),
        "grid_sd",
        minimum=0.0,
        maximum=4.0,
        strict_minimum=True,
    )
    correlations = _numeric_grid(
        grid.get("pre_followup_correlation"),
        "grid_correlation",
        minimum=-0.5,
        maximum=0.95,
    )
    cluster_iccs = _numeric_grid(
        grid.get("delivery_cluster_icc"),
        "grid_cluster_icc",
        minimum=0.0,
        maximum=0.95,
    )
    attrition_values = _numeric_grid(
        grid.get("attrition_fraction"),
        "grid_attrition",
        minimum=0.0,
        maximum=0.80,
    )
    mean_cluster_size = _number(
        grid.get("mean_delivery_cluster_size"),
        "mean_cluster_size",
        minimum=1.0,
        maximum=10_000.0,
        strict_minimum=True,
    )
    cluster_size_cv = _number(
        grid.get("delivery_cluster_size_cv"),
        "cluster_size_cv",
        minimum=0.0,
        maximum=5.0,
    )

    feasibility = _mapping(
        config.get("feasibility_pilot"),
        "feasibility_pilot",
    )
    _expect(
        feasibility.get("descriptive_initial_range_per_arm"),
        [15, 20],
        "feasibility_range",
    )
    _expect(
        feasibility.get("powered_efficacy_claim_allowed"),
        False,
        "feasibility_efficacy_claim",
    )
    exclusions = _mapping(config.get("exclusions"), "exclusions")
    after_randomization = _mapping(
        exclusions.get("after_randomization"),
        "post_randomization_exclusions",
    )
    _expect(
        after_randomization.get("primary_itt_exclusion_allowed"),
        False,
        "post_randomization_itt_exclusion",
    )
    stopping = _mapping(config.get("stopping"), "stopping")
    _expect(
        stopping.get("efficacy_or_futility_interim_testing"),
        "none",
        "interim_testing",
    )
    _expect(
        stopping.get("effect_based_early_stop_allowed"),
        False,
        "effect_based_stop",
    )
    if not _list(stopping.get("pause_or_stop_triggers"), "stop_triggers"):
        _fail("stop_triggers")
    output = _mapping(config.get("output"), "output")
    for key, expected in (
        ("aggregate_only", True),
        ("identifiers_allowed", False),
        ("atomic_publish", True),
        ("final_sample_size_field_must_remain_null_without_manual_lock", True),
    ):
        _expect(output.get(key), expected, f"output_{key}")

    return {
        "alpha": planning_alpha,
        "familywise_alpha": alpha,
        "target_power": target_power,
        "raw_effects": raw_effects,
        "standard_deviations": standard_deviations,
        "correlations": correlations,
        "cluster_iccs": cluster_iccs,
        "attrition_values": attrition_values,
        "mean_cluster_size": mean_cluster_size,
        "cluster_size_cv": cluster_size_cv,
        "external_status": dict(external),
    }


def validate_pilot_estimates(pilot: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an aggregate nuisance-parameter pilot record."""

    required_keys = {
        "schema_version",
        "status",
        "evidence_type",
        "outcome",
        "participant_count",
        "participant_level_outcome_sd",
        "pre_followup_correlation",
        "delivery_cluster_icc",
        "attrition_fraction",
        "mean_delivery_cluster_size",
        "delivery_cluster_size_cv",
        "rater_icc_2_1",
        "source_report_sha256",
        "irb_or_equivalent_approval_confirmed",
        "consent_confirmed",
        "aggregate_only",
        "identifiers_present",
        "observed_treatment_effect_used_for_power",
    }
    if set(pilot) != required_keys:
        _fail("pilot_key_set")
    _expect(pilot.get("schema_version"), PILOT_SCHEMA, "pilot_schema")
    _expect(pilot.get("status"), "completed", "pilot_status")
    _expect(
        pilot.get("evidence_type"),
        "empirical_pilot_nuisance_estimates",
        "pilot_evidence_type",
    )
    _expect(
        pilot.get("outcome"),
        "blind_expert_comprehensibility",
        "pilot_outcome",
    )
    for key in (
        "irb_or_equivalent_approval_confirmed",
        "consent_confirmed",
        "aggregate_only",
    ):
        _expect(pilot.get(key), True, f"pilot_{key}")
    for key in (
        "identifiers_present",
        "observed_treatment_effect_used_for_power",
    ):
        _expect(pilot.get(key), False, f"pilot_{key}")
    return {
        "participant_count": _integer(
            pilot.get("participant_count"),
            "pilot_participant_count",
            minimum=2,
        ),
        "standard_deviation": _number(
            pilot.get("participant_level_outcome_sd"),
            "pilot_sd",
            minimum=0.0,
            maximum=4.0,
            strict_minimum=True,
        ),
        "correlation": _number(
            pilot.get("pre_followup_correlation"),
            "pilot_correlation",
            minimum=-0.5,
            maximum=0.95,
        ),
        "cluster_icc": _number(
            pilot.get("delivery_cluster_icc"),
            "pilot_cluster_icc",
            minimum=0.0,
            maximum=0.95,
        ),
        "attrition": _number(
            pilot.get("attrition_fraction"),
            "pilot_attrition",
            minimum=0.0,
            maximum=0.80,
        ),
        "mean_cluster_size": _number(
            pilot.get("mean_delivery_cluster_size"),
            "pilot_mean_cluster_size",
            minimum=1.0,
            maximum=10_000.0,
            strict_minimum=True,
        ),
        "cluster_size_cv": _number(
            pilot.get("delivery_cluster_size_cv"),
            "pilot_cluster_size_cv",
            minimum=0.0,
            maximum=5.0,
        ),
        "rater_icc_2_1": _number(
            pilot.get("rater_icc_2_1"),
            "pilot_rater_icc",
            minimum=-1.0,
            maximum=1.0,
        ),
        "source_report_sha256": _sha256(
            pilot.get("source_report_sha256"),
            "pilot_source_report_sha256",
        ),
    }


def repeated_change_sample_size(
    *,
    outcome_sd: float,
    raw_mean_difference: float,
    correlation: float,
    alpha: float,
    power: float,
) -> tuple[float, int, float]:
    """Return unrounded/rounded complete participants per arm and standardized d.

    This is a two-arm normal approximation for a difference in change scores:
    ``4(1-r)(z_alpha+z_power)^2 / d^2``.
    """

    for value, name in (
        (outcome_sd, "outcome_sd"),
        (raw_mean_difference, "raw_mean_difference"),
        (alpha, "alpha"),
        (power, "power"),
    ):
        if not math.isfinite(value):
            _fail(f"formula_{name}")
    if outcome_sd <= 0.0 or raw_mean_difference <= 0.0:
        _fail("formula_positive_scale")
    if not -1.0 < correlation < 1.0:
        _fail("formula_correlation")
    if not 0.0 < alpha < 1.0 or not 0.5 < power < 1.0:
        _fail("formula_probability")
    standardized_effect = raw_mean_difference / outcome_sd
    z_alpha = NormalDist().inv_cdf(1.0 - alpha / 2.0)
    z_power = NormalDist().inv_cdf(power)
    unrounded = (
        4.0 * (1.0 - correlation) * (z_alpha + z_power) ** 2 / standardized_effect**2
    )
    return unrounded, math.ceil(unrounded), standardized_effect


def cluster_design_effect(
    *,
    mean_cluster_size: float,
    cluster_icc: float,
    cluster_size_cv: float,
) -> float:
    """Return the unequal-cluster-size design-effect approximation."""

    if (
        not math.isfinite(mean_cluster_size)
        or mean_cluster_size <= 0.0
        or not math.isfinite(cluster_icc)
        or not 0.0 <= cluster_icc < 1.0
        or not math.isfinite(cluster_size_cv)
        or cluster_size_cv < 0.0
    ):
        _fail("cluster_formula_input")
    return 1.0 + (((1.0 + cluster_size_cv**2) * mean_cluster_size) - 1.0) * cluster_icc


def _scenario(
    *,
    scenario_id: str,
    evidence_basis: str,
    raw_effect: float,
    standard_deviation: float,
    correlation: float,
    cluster_icc: float,
    attrition: float,
    mean_cluster_size: float,
    cluster_size_cv: float,
    alpha: float,
    power: float,
) -> dict[str, Any]:
    unrounded, complete_per_arm, standardized_effect = repeated_change_sample_size(
        outcome_sd=standard_deviation,
        raw_mean_difference=raw_effect,
        correlation=correlation,
        alpha=alpha,
        power=power,
    )
    individual_enrollment = math.ceil(complete_per_arm / (1.0 - attrition))
    design_effect = cluster_design_effect(
        mean_cluster_size=mean_cluster_size,
        cluster_icc=cluster_icc,
        cluster_size_cv=cluster_size_cv,
    )
    cluster_unrounded_complete = unrounded * design_effect
    cluster_minimum_complete = math.ceil(cluster_unrounded_complete)
    clusters_per_arm = math.ceil(cluster_minimum_complete / mean_cluster_size)
    cluster_rounded_complete = math.ceil(clusters_per_arm * mean_cluster_size)
    cluster_enrollment = math.ceil(cluster_rounded_complete / (1.0 - attrition))
    return {
        "scenario_id": scenario_id,
        "evidence_basis": evidence_basis,
        "inputs": {
            "raw_mean_difference_points": raw_effect,
            "participant_level_outcome_sd": standard_deviation,
            "standardized_mean_difference": standardized_effect,
            "pre_followup_correlation": correlation,
            "delivery_cluster_icc": cluster_icc,
            "attrition_fraction": attrition,
            "mean_delivery_cluster_size": mean_cluster_size,
            "delivery_cluster_size_cv": cluster_size_cv,
            "alpha_two_sided_per_contrast": alpha,
            "target_power": power,
        },
        "individual_randomized_repeated_change": {
            "unrounded_complete_per_arm": unrounded,
            "complete_per_arm": complete_per_arm,
            "enroll_per_arm_after_attrition": individual_enrollment,
            "enroll_per_two_arm_contrast": individual_enrollment * 2,
            "enroll_if_both_stages_are_separate": individual_enrollment * 4,
        },
        "cluster_randomized_mixed_model_approximation": {
            "design_effect": design_effect,
            "unrounded_complete_per_arm": cluster_unrounded_complete,
            "minimum_complete_participants_per_arm": cluster_minimum_complete,
            "minimum_complete_clusters_per_arm": clusters_per_arm,
            "cluster_rounded_complete_per_arm": cluster_rounded_complete,
            "enroll_per_arm_after_attrition": cluster_enrollment,
            "enroll_per_two_arm_contrast": cluster_enrollment * 2,
            "enroll_if_both_stages_are_separate": cluster_enrollment * 4,
        },
    }


def _assumed_scenarios(planning: Mapping[str, Any]) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    combinations = product(
        planning["raw_effects"],
        planning["standard_deviations"],
        planning["correlations"],
        planning["cluster_iccs"],
        planning["attrition_values"],
    )
    for index, (effect, sd, correlation, icc, attrition) in enumerate(
        combinations,
        start=1,
    ):
        scenarios.append(
            _scenario(
                scenario_id=f"assumed-{index:04d}",
                evidence_basis="assumed_sensitivity_not_empirical",
                raw_effect=float(effect),
                standard_deviation=float(sd),
                correlation=float(correlation),
                cluster_icc=float(icc),
                attrition=float(attrition),
                mean_cluster_size=float(planning["mean_cluster_size"]),
                cluster_size_cv=float(planning["cluster_size_cv"]),
                alpha=float(planning["alpha"]),
                power=float(planning["target_power"]),
            )
        )
    return scenarios


def _pilot_scenarios(
    planning: Mapping[str, Any],
    pilot: Mapping[str, Any],
) -> list[dict[str, Any]]:
    return [
        _scenario(
            scenario_id=f"pilot-informed-{index:03d}",
            evidence_basis=("empirical_pilot_nuisance_plus_assumed_target_effect"),
            raw_effect=float(effect),
            standard_deviation=float(pilot["standard_deviation"]),
            correlation=float(pilot["correlation"]),
            cluster_icc=float(pilot["cluster_icc"]),
            attrition=float(pilot["attrition"]),
            mean_cluster_size=float(pilot["mean_cluster_size"]),
            cluster_size_cv=float(pilot["cluster_size_cv"]),
            alpha=float(planning["alpha"]),
            power=float(planning["target_power"]),
        )
        for index, effect in enumerate(planning["raw_effects"], start=1)
    ]


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                value,
                handle,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def plan_learning_effect_power(
    *,
    config_path: str | Path,
    output_path: str | Path,
    workspace_root: str | Path,
    pilot_estimates_path: str | Path | None = None,
) -> dict[str, Any]:
    """Validate H01 inputs and atomically emit a planning-only power report."""

    root = Path(workspace_root).resolve(strict=True)
    if not root.is_dir():
        _fail("workspace_not_directory")
    config_file = _safe_input(root, config_path)
    output_file = _safe_output(root, output_path)
    if output_file == config_file:
        _fail("output_overwrites_input")
    config = _load_yaml(config_file)
    planning = validate_config(config)
    config_sha256 = _file_sha256(config_file)

    pilot_file: Path | None = None
    pilot_values: dict[str, Any] | None = None
    pilot_sha256: str | None = None
    if pilot_estimates_path is not None:
        pilot_file = _safe_input(root, pilot_estimates_path)
        if pilot_file in {config_file, output_file}:
            _fail("pilot_path_collision")
        pilot_values = validate_pilot_estimates(_load_json(pilot_file))
        pilot_sha256 = _file_sha256(pilot_file)
        external_status = _mapping(
            planning["external_status"],
            "validated_external_status",
        )
        if any(
            external_status.get(key) is not True
            for key in (
                "irb_or_equivalent_approval_obtained",
                "participants_recruited",
                "learner_recordings_collected",
                "empirical_pilot_nuisance_estimates_available",
            )
        ):
            _fail("pilot_input_without_updated_external_status")
    elif planning["external_status"]["empirical_pilot_nuisance_estimates_available"]:
        _fail("empirical_pilot_status_without_input")

    assumed = _assumed_scenarios(planning)
    pilot_informed = (
        _pilot_scenarios(planning, pilot_values) if pilot_values is not None else []
    )
    report: dict[str, Any] = {
        "schema_version": OUTPUT_SCHEMA,
        "status": (
            "pilot_informed_sensitivity"
            if pilot_values is not None
            else "assumed_sensitivity_only"
        ),
        "h01_completion_claim_allowed": False,
        "external_status": {
            "irb_or_equivalent_approval_obtained": planning["external_status"][
                "irb_or_equivalent_approval_obtained"
            ],
            "participants_recruited": planning["external_status"][
                "participants_recruited"
            ],
            "learner_recordings_collected": planning["external_status"][
                "learner_recordings_collected"
            ],
            "empirical_pilot_input_supplied": pilot_values is not None,
            "confirmatory_effect_observed": False,
        },
        "preregistered_scope": {
            "design": "two_stage_parallel_pairwise_randomized",
            "primary_outcome": "blind_expert_comprehensibility",
            "primary_timepoint": "end_of_week_2",
            "retention_timepoint": "four_weeks_after_post",
            "primary_contrasts": list(EXPECTED_CONTRASTS),
            "analysis_population": "intention_to_treat_as_randomized",
            "mixed_model_random_intercepts": [
                "participant",
                "sentence",
                "rater",
            ],
            "multiplicity": "holm",
        },
        "planning_parameters": {
            "familywise_alpha_two_sided": planning["familywise_alpha"],
            "conservative_per_contrast_alpha": planning["alpha"],
            "target_power": planning["target_power"],
            "approximation": "two_arm_repeated_change_with_cluster_design_effect",
        },
        "provenance": {
            "config_sha256": config_sha256,
            "pilot_input_sha256": pilot_sha256,
            "pilot_source_report_sha256": (
                pilot_values["source_report_sha256"]
                if pilot_values is not None
                else None
            ),
            "assumed_grid_is_not_empirical": True,
            "observed_treatment_effect_used_for_power": False,
        },
        "assumed_sensitivity_scenarios": assumed,
        "pilot_informed_scenarios": pilot_informed,
        "pilot_nuisance_summary": (
            {
                "participant_count": pilot_values["participant_count"],
                "rater_icc_2_1": pilot_values["rater_icc_2_1"],
                "rater_icc_used_in_sample_size_formula": False,
                "nuisance_estimates_are_empirical": True,
                "target_effects_remain_assumed": True,
            }
            if pilot_values is not None
            else None
        ),
        "final_sample_size": None,
        "final_sample_size_locked": False,
        "manual_actions_required_before_lock": [
            "obtain_irb_or_equivalent_approval",
            "complete_feasibility_pilot",
            "validate_blind_rater_reliability",
            "review_pilot_sd_correlation_icc_and_attrition",
            "choose_minimum_relevant_target_effect_without_using_observed_effect",
            "run_full_mixed_model_or_cluster_simulation",
            "obtain_statistician_and_principal_investigator_signoff",
            "version_and_register_protocol_before_confirmatory_recruitment",
        ],
        "limitations": [
            "normal_approximation_not_full_mixed_model_simulation",
            "cluster_design_effect_is_an_approximation",
            "pilot_nuisance_estimates_can_be_unstable",
            "sensitivity_grid_values_are_not_results",
            "feasibility_range_15_to_20_per_arm_is_not_a_power_conclusion",
        ],
        "privacy": {
            "raw_or_restricted_data_read": False,
            "environment_files_read": False,
            "participant_rows_read": False,
            "identifiers_written": False,
            "aggregate_only_output": True,
        },
    }
    _atomic_write_json(output_file, report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate planning-only repeated-measures and clustered power "
            "sensitivity analyses for H01 without inventing pilot estimates."
        )
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pilot-estimates", type=Path)
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = plan_learning_effect_power(
            config_path=args.config,
            output_path=args.output,
            workspace_root=args.workspace_root,
            pilot_estimates_path=args.pilot_estimates,
        )
    except PowerPlanError as exc:
        print(f"power planning failed: {exc.code}", file=sys.stderr)
        return 1
    except OSError:
        print("power planning output publication failed", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": report["status"],
                "assumed_scenarios": len(report["assumed_sensitivity_scenarios"]),
                "pilot_informed_scenarios": len(report["pilot_informed_scenarios"]),
                "final_sample_size_locked": False,
                "h01_completion_claim_allowed": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
