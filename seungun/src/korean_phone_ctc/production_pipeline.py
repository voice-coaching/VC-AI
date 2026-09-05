"""Versioned production policy over the frozen Seungun detector evidence.

The frozen detector remains the only acoustic decision source.  This module
does not reinterpret its ranking score as confidence; it preserves the whole
v1 document and records the deterministic publication decision separately.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from .frozen_detector import (
    SCHEMA_VERSION as FROZEN_ANALYSIS_SCHEMA_VERSION,
    SCORE_SEMANTICS,
    FrozenPronunciationDetector,
)


SCHEMA_VERSION = "korean_phone_ctc.production_analysis.v2"
DECISION_SCHEMA_VERSION = "korean_phone_ctc.production_decision.v1"
POLICY_REVISION = "seungun_frozen_threshold_publication_v1"
DecisionState = Literal[
    "coach_one",
    "no_issue",
    "rerecord",
    "abstain",
    "unsupported",
]


class SeungunProductionError(RuntimeError):
    """The detector output cannot be admitted to the production contract."""


class Detector(Protocol):
    def analyze(
        self, *, audio_path: str | Path, expected_phones: Sequence[str]
    ) -> Mapping[str, object] | object: ...

    def preload(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ProductionDecision:
    schema_version: str
    state: DecisionState
    reason_codes: tuple[str, ...]
    policy_revision: str
    selected_expected_index: int | None
    evidence_bundle_sha256: str


@dataclass(frozen=True, slots=True)
class ProductionPronunciationAnalysis:
    schema_version: str
    pipeline_revision: str
    detector_version: str
    expected_phones: tuple[str, ...]
    target_free_hypothesis: tuple[str, ...]
    evidence_bundle: dict[str, object]
    decision: ProductionDecision

    def to_json_object(self) -> dict[str, Any]:
        return asdict(self)


class SeungunProductionPipeline:
    """Produce a strict v2 envelope without changing the frozen verdict."""

    def __init__(self, detector: Detector) -> None:
        self._detector = detector

    @classmethod
    def from_repository(
        cls, *, repository_root: str | Path, device: str = "cpu"
    ) -> "SeungunProductionPipeline":
        return cls(
            FrozenPronunciationDetector(
                repository_root=repository_root,
                device=device,
            )
        )

    def preload(self) -> None:
        self._detector.preload()

    def capabilities(self) -> dict[str, object]:
        return {
            "analysis_schema_version": SCHEMA_VERSION,
            "decision_schema_version": DECISION_SCHEMA_VERSION,
            "policy_revision": POLICY_REVISION,
            "frozen_analysis_schema_version": FROZEN_ANALYSIS_SCHEMA_VERSION,
        }

    def analyze(
        self, *, audio_path: str | Path, expected_phones: Sequence[str]
    ) -> ProductionPronunciationAnalysis:
        raw = self._detector.analyze(
            audio_path=audio_path,
            expected_phones=expected_phones,
        )
        evidence = _snapshot(raw)
        expected = _string_sequence(evidence.get("expected_phones"), "expected_phones")
        requested = tuple(expected_phones)
        if expected != requested:
            raise SeungunProductionError("expected_phone_sequence_mismatch")
        hypothesis = _string_sequence(
            evidence.get("ctc_hypothesis"), "ctc_hypothesis", allow_empty=True
        )
        detector_version = _safe_identifier(
            evidence.get("detector_version"), "detector_version"
        )
        digest = hashlib.sha256(_canonical_json_bytes(evidence)).hexdigest()
        decision = _decision(
            evidence=evidence,
            expected_phones=expected,
            evidence_sha256=digest,
        )
        return ProductionPronunciationAnalysis(
            schema_version=SCHEMA_VERSION,
            pipeline_revision=f"{detector_version}|{POLICY_REVISION}",
            detector_version=detector_version,
            expected_phones=expected,
            target_free_hypothesis=hypothesis,
            evidence_bundle=evidence,
            decision=decision,
        )


def _snapshot(value: object) -> dict[str, object]:
    try:
        raw = value if isinstance(value, Mapping) else value.to_json_object()  # type: ignore[attr-defined]
        result = json.loads(_canonical_json_bytes(raw))
    except Exception as error:
        raise SeungunProductionError("frozen_analysis_not_serializable") from error
    if (
        not isinstance(result, dict)
        or result.get("schema_version") != FROZEN_ANALYSIS_SCHEMA_VERSION
    ):
        raise SeungunProductionError("frozen_analysis_schema_mismatch")
    return cast(dict[str, object], result)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _safe_identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 100:
        raise SeungunProductionError(f"{name}_invalid")
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-|"
    if any(character not in allowed for character in value):
        raise SeungunProductionError(f"{name}_invalid")
    return value


def _string_sequence(
    value: object, name: str, *, allow_empty: bool = False
) -> tuple[str, ...]:
    if (
        not isinstance(value, list | tuple)
        or (not value and not allow_empty)
        or len(value) > 4096
        or any(not isinstance(item, str) or not item or len(item) > 16 for item in value)
    ):
        raise SeungunProductionError(f"{name}_invalid")
    return tuple(value)


def _decision(
    *,
    evidence: Mapping[str, object],
    expected_phones: tuple[str, ...],
    evidence_sha256: str,
) -> ProductionDecision:
    phones = evidence.get("phones")
    if not isinstance(phones, list) or len(phones) != len(expected_phones):
        raise SeungunProductionError("phone_evidence_invalid")
    candidates: list[tuple[float, int]] = []
    for index, phone in enumerate(phones):
        if not isinstance(phone, Mapping):
            raise SeungunProductionError("phone_evidence_invalid")
        expected_index = phone.get("expected_index")
        score = phone.get("error_score")
        threshold = phone.get("operating_threshold")
        if (
            expected_index != index
            or phone.get("expected_phone") != expected_phones[index]
            or isinstance(score, bool)
            or not isinstance(score, int | float)
            or isinstance(threshold, bool)
            or not isinstance(threshold, int | float)
            or not math.isfinite(float(score))
            or not math.isfinite(float(threshold))
            or not 0.0 <= float(threshold) <= 1.0
            or not 0.0 <= float(score) <= 1.0
            or phone.get("score_semantics") != SCORE_SEMANTICS
            or not isinstance(phone.get("is_error"), bool)
        ):
            raise SeungunProductionError("phone_evidence_invalid")
        is_error = cast(bool, phone["is_error"])
        if is_error != (float(score) >= float(threshold)):
            raise SeungunProductionError("phone_threshold_decision_mismatch")
        if is_error:
            candidates.append((float(score), index))
    if not candidates:
        state: DecisionState = "no_issue"
        selected = None
        reasons = ("no_threshold_passed_phone",)
    else:
        _, selected = min(candidates, key=lambda item: (-item[0], item[1]))
        state = "coach_one"
        reasons = ("frozen_detector_threshold_passed",)
    return ProductionDecision(
        schema_version=DECISION_SCHEMA_VERSION,
        state=state,
        reason_codes=reasons,
        policy_revision=POLICY_REVISION,
        selected_expected_index=selected,
        evidence_bundle_sha256=evidence_sha256,
    )


__all__ = [
    "DECISION_SCHEMA_VERSION",
    "POLICY_REVISION",
    "SCHEMA_VERSION",
    "ProductionDecision",
    "ProductionPronunciationAnalysis",
    "SeungunProductionError",
    "SeungunProductionPipeline",
]
