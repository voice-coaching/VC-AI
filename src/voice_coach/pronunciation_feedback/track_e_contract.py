"""Load and verify the frozen Track E contract.

The contract fixes *rules* — threshold derivation, metric definitions, split
roles, gate thresholds, access instrumentation — before any Track E run reads a
result.  Its canonical hash is what a run receipt cites; changing a rule changes
the hash and therefore invalidates the runs that cited the old one.

This module is read-only with respect to the contract: it never writes, patches,
or defaults a missing field.  A structurally incomplete contract raises rather
than degrading to a permissive one.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .thresholds import CLAIM_SCOPES, ENGINEERING_PROVISIONAL

CONTRACT_SCHEMA_VERSION = "voice_coach.track_e_contract.v1"
DEFAULT_CONTRACT_PATH = Path(
    "configs/pronunciation_feedback/track_e_contract_v1.json"
)

#: Splits Track E may read at all.  ``locked`` and ``expert_holdout`` must be
#: declared and must be marked unreadable.
FORBIDDEN_SPLITS = ("locked", "expert_holdout")
REQUIRED_SPLITS = ("train", "dev", "calibration", *FORBIDDEN_SPLITS)
REQUIRED_METRICS = (
    "phone_macro_f1",
    "utterance_icc",
    "content_wer",
    "content_cer",
    "ece",
    "issued_precision",
)
#: Every threshold the gate can veto on must have a derivation rule, so that no
#: threshold can be introduced after a result has been seen.
REQUIRED_THRESHOLD_RULES = (
    "min_content_score",
    "min_alignment_confidence",
    "min_s1_confidence",
    "min_s2_confidence",
    "min_issue_confidence",
    "min_issue_severity",
)
REQUIRED_ZERO_COUNTERS = (
    "locked_test_path_resolution_count",
    "locked_test_read_count",
    "expert_holdout_path_resolution_count",
    "expert_holdout_read_count",
)


class TrackEContractError(Exception):
    """The contract is absent, malformed, or self-inconsistent."""


@dataclass(frozen=True, slots=True)
class TrackEContract:
    contract_id: str
    claim_scope: str
    cohort: str
    frozen_at_kst: str
    contract_sha256: str
    document: Mapping[str, Any]

    @property
    def derivation_split(self) -> str:
        rules = self.document["threshold_derivation_rules"]
        return str(rules["derivation_split"])

    def threshold_rule(self, name: str) -> Mapping[str, Any]:
        rules = self.document["threshold_derivation_rules"]["rules"]
        if name not in rules:
            raise TrackEContractError(f"no derivation rule for {name}")
        return rules[name]

    def metric_definition(self, name: str) -> Mapping[str, Any]:
        definitions = self.document["metric_definitions"]
        if name not in definitions:
            raise TrackEContractError(f"no metric definition for {name}")
        return definitions[name]

    def gate_threshold(self, group: str, metric: str) -> Mapping[str, Any]:
        groups = self.document["gate_thresholds"]
        if group not in groups or metric not in groups[group]:
            raise TrackEContractError(f"no gate threshold for {group}.{metric}")
        return groups[group][metric]

    def zero_counters(self) -> tuple[str, ...]:
        counters = self.document["access_instrumentation"][
            "counters_that_must_be_zero"
        ]
        return tuple(str(item) for item in counters)

    def assert_counters_zero(self, observed: Mapping[str, int]) -> None:
        """Raise unless every declared counter is present and zero.

        A counter that was never measured is not evidence of no access, so an
        absent key fails exactly like a nonzero one.
        """

        for name in self.zero_counters():
            if name not in observed:
                raise TrackEContractError(f"access counter not reported: {name}")
            if observed[name] != 0:
                raise TrackEContractError(
                    f"access counter must be zero: {name}={observed[name]}"
                )


def canonical_contract_bytes(document: Mapping[str, Any]) -> bytes:
    """Serialize the contract the one way its hash is defined over."""

    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def contract_sha256(document: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_contract_bytes(document)).hexdigest()


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TrackEContractError(f"{name} must be an object")
    return value


def _comparison(value: object, name: str) -> None:
    record = _mapping(value, name)
    if record.get("comparator") not in ("<=", ">=", "<", ">", "=="):
        raise TrackEContractError(f"{name}.comparator is not a comparison operator")
    threshold = record.get("value")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise TrackEContractError(f"{name}.value must be a number")


def validate_contract_document(document: Mapping[str, Any]) -> None:
    """Structural and self-consistency checks, in the order a run depends on."""

    if document.get("schema_version") != CONTRACT_SCHEMA_VERSION:
        raise TrackEContractError("schema_version differs")
    if document.get("status") != "frozen":
        raise TrackEContractError("contract status must be frozen before any run")
    claim_scope = document.get("claim_scope")
    if claim_scope not in CLAIM_SCOPES:
        raise TrackEContractError("claim_scope is not a known scope")
    if claim_scope != ENGINEERING_PROVISIONAL:
        raise TrackEContractError(
            "this contract may only carry the engineering_provisional scope"
        )
    for field in ("contract_id", "cohort", "frozen_at_kst"):
        if not isinstance(document.get(field), str) or not document[field]:
            raise TrackEContractError(f"{field} must be a non-empty string")

    authority = _mapping(document.get("authority"), "authority")
    for flag in (
        "grants_research_completion_points",
        "grants_validated_performance_claim",
    ):
        if authority.get(flag) is not False:
            raise TrackEContractError(f"authority.{flag} must be false")
    if authority.get("research_validated_requires_human_gate_receipt") is not True:
        raise TrackEContractError(
            "authority.research_validated_requires_human_gate_receipt must be true"
        )

    splits = _mapping(document.get("split_roles"), "split_roles")
    for name in REQUIRED_SPLITS:
        record = _mapping(splits.get(name), f"split_roles.{name}")
        if not isinstance(record.get("role"), str) or not record["role"]:
            raise TrackEContractError(f"split_roles.{name}.role must be named")
        if not isinstance(record.get("threshold_derivation_allowed"), bool):
            raise TrackEContractError(
                f"split_roles.{name}.threshold_derivation_allowed must be a boolean"
            )
        if not isinstance(record.get("read_allowed"), bool):
            raise TrackEContractError(
                f"split_roles.{name}.read_allowed must be a boolean"
            )
    for name in FORBIDDEN_SPLITS:
        if splits[name]["read_allowed"] or splits[name]["threshold_derivation_allowed"]:
            raise TrackEContractError(f"split_roles.{name} must stay unreadable")
    deriving = tuple(
        name
        for name in REQUIRED_SPLITS
        if splits[name]["threshold_derivation_allowed"]
    )
    if deriving != ("calibration",):
        raise TrackEContractError(
            "exactly the calibration split may derive thresholds, got "
            + ", ".join(deriving)
        )

    metrics = _mapping(document.get("metric_definitions"), "metric_definitions")
    for name in REQUIRED_METRICS:
        record = _mapping(metrics.get(name), f"metric_definitions.{name}")
        for field in ("unit", "definition", "aggregation"):
            if not isinstance(record.get(field), str) or not record[field]:
                raise TrackEContractError(
                    f"metric_definitions.{name}.{field} must be a non-empty string"
                )

    derivation = _mapping(
        document.get("threshold_derivation_rules"),
        "threshold_derivation_rules",
    )
    if derivation.get("derivation_split") != "calibration":
        raise TrackEContractError("thresholds may only be derived on calibration")
    if not isinstance(derivation.get("post_hoc_change_policy"), str):
        raise TrackEContractError("post_hoc_change_policy must be stated")
    clamp = derivation.get("clamp_interval")
    if clamp != [0.0, 1.0]:
        raise TrackEContractError("clamp_interval must be [0.0, 1.0]")
    rules = _mapping(derivation.get("rules"), "threshold_derivation_rules.rules")
    if tuple(sorted(rules)) != tuple(sorted(REQUIRED_THRESHOLD_RULES)):
        raise TrackEContractError(
            "threshold rules must cover exactly the gate thresholds"
        )
    order = derivation.get("derivation_order")
    if not isinstance(order, list) or tuple(order) != REQUIRED_THRESHOLD_RULES:
        raise TrackEContractError("derivation_order must fix the documented order")
    for name, raw_rule in rules.items():
        rule = _mapping(raw_rule, f"rules.{name}")
        statistic = rule.get("statistic")
        if statistic not in ("quantile", "smallest_value_satisfying_target", "fixed"):
            raise TrackEContractError(f"rules.{name}.statistic is not a known rule")
        if statistic == "quantile":
            quantile = rule.get("quantile")
            if isinstance(quantile, bool) or not isinstance(quantile, (int, float)):
                raise TrackEContractError(f"rules.{name}.quantile must be a number")
            if not 0.0 < float(quantile) < 1.0:
                raise TrackEContractError(f"rules.{name}.quantile must be in (0, 1)")
            if not isinstance(rule.get("population"), str):
                raise TrackEContractError(f"rules.{name}.population must be named")
        elif statistic == "smallest_value_satisfying_target":
            target = _mapping(rule.get("target"), f"rules.{name}.target")
            metric = target.get("metric")
            if metric not in metrics:
                raise TrackEContractError(
                    f"rules.{name}.target.metric is not a defined metric"
                )
            _comparison(target, f"rules.{name}.target")
            if not isinstance(rule.get("fallback"), str):
                raise TrackEContractError(f"rules.{name}.fallback must be stated")
        else:
            value = rule.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TrackEContractError(f"rules.{name}.value must be a number")

    branches = _mapping(
        derivation.get("required_branches_rule"),
        "required_branches_rule",
    )
    names = branches.get("value")
    if not isinstance(names, list) or not names:
        raise TrackEContractError("required_branches_rule.value must list branches")
    if len(set(names)) != len(names):
        raise TrackEContractError("required_branches_rule.value has duplicates")

    gates = _mapping(document.get("gate_thresholds"), "gate_thresholds")
    for group in ("s107_calibration", "stage_c_content_backend_acceptance"):
        if group not in gates:
            raise TrackEContractError(f"gate_thresholds.{group} is required")
    _comparison(gates["s107_calibration"].get("ece"), "s107_calibration.ece")
    _comparison(
        gates["s107_calibration"].get("issued_precision"),
        "s107_calibration.issued_precision",
    )
    stage_c = _mapping(
        gates["stage_c_content_backend_acceptance"],
        "stage_c_content_backend_acceptance",
    )
    for metric in ("content_cer", "content_wer"):
        _comparison(stage_c.get(metric), f"stage_c_content_backend_acceptance.{metric}")
    if not isinstance(stage_c.get("on_violation"), str):
        raise TrackEContractError(
            "stage_c_content_backend_acceptance.on_violation must be stated"
        )

    access = _mapping(document.get("access_instrumentation"), "access_instrumentation")
    counters = access.get("counters_that_must_be_zero")
    if not isinstance(counters, list) or not all(
        isinstance(item, str) and item for item in counters
    ):
        raise TrackEContractError("counters_that_must_be_zero must be a name array")
    missing = [name for name in REQUIRED_ZERO_COUNTERS if name not in counters]
    if missing:
        raise TrackEContractError(
            "access instrumentation must cover: " + ", ".join(missing)
        )
    if access.get("on_nonzero") != "failed_closed":
        raise TrackEContractError("a nonzero access counter must fail closed")

    stops = document.get("stop_conditions")
    if not isinstance(stops, list) or len(stops) < 6:
        raise TrackEContractError("stop_conditions must enumerate the halt triggers")


def load_track_e_contract(
    path: str | Path = DEFAULT_CONTRACT_PATH,
    *,
    expected_sha256: str | None = None,
) -> TrackEContract:
    """Load, validate, and hash the contract.

    ``expected_sha256`` is how a later stage proves it is running under the same
    rules it was configured with; a mismatch raises instead of continuing.
    """

    contract_path = Path(path)
    if not contract_path.is_file():
        raise TrackEContractError(f"contract artifact not found: {contract_path}")
    try:
        document = json.loads(contract_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TrackEContractError("contract artifact is not valid JSON") from error
    if not isinstance(document, Mapping):
        raise TrackEContractError("contract artifact must be an object")

    validate_contract_document(document)
    digest = contract_sha256(document)
    if expected_sha256 is not None and digest != expected_sha256:
        raise TrackEContractError(
            f"contract hash differs: expected {expected_sha256}, got {digest}"
        )
    return TrackEContract(
        contract_id=str(document["contract_id"]),
        claim_scope=str(document["claim_scope"]),
        cohort=str(document["cohort"]),
        frozen_at_kst=str(document["frozen_at_kst"]),
        contract_sha256=digest,
        document=document,
    )


__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "DEFAULT_CONTRACT_PATH",
    "FORBIDDEN_SPLITS",
    "REQUIRED_METRICS",
    "REQUIRED_SPLITS",
    "REQUIRED_THRESHOLD_RULES",
    "REQUIRED_ZERO_COUNTERS",
    "TrackEContract",
    "TrackEContractError",
    "canonical_contract_bytes",
    "contract_sha256",
    "load_track_e_contract",
    "validate_contract_document",
]
