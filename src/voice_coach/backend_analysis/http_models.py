"""Strict versioned requests and legacy HTTP controls; no grant conversion."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
import hashlib
import json
import logging
from fractions import Fraction
from pathlib import Path
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker, ValidationError, validators
import rfc8785

from .domain import AnalysisWorkerResult

CONTRACT = "voice-coaching.runpod-http.v1.1"
REQUEST_V1 = "voice-coaching.runpod-analysis-request.v1"
REQUEST_V2 = "voice-coaching.runpod-analysis-request.v2"
REQUEST_V3 = "voice-coaching.runpod-analysis-request.v3"
REQUEST_V4 = "voice-coaching.runpod-analysis-request.v4"
RESULT_V6 = "voice-coaching.runpod-analysis-result.v6"
AUDIOVISUAL_PROFILE = "CANONICAL_AUDIOVISUAL_20261007_V6"
RESULT_V5 = "voice-coaching.runpod-analysis-result.v5"
HANDOFF_PROFILE = "CANONICAL_HANDOFF_20261004_V5"
RESULT_V3 = "voice-coaching.runpod-analysis-result.v3"
RESULT_V4 = "voice-coaching.runpod-analysis-result.v4"
LEGACY_PROFILE = "LEGACY_SEUNGUN_V3"
CANONICAL_PROFILE = "CANONICAL_FROZEN_20260928_V4"
MAX_CONTROL_BYTES = 65536
MAX_RESULT_BYTES = 1048576


def _decimal_multiple_of(validator, divisor, instance, schema):
    """Use the JSON decimal spelling, not binary float division, for multiples."""
    if not validator.is_type(instance, "number"):
        return
    try:
        valid = (Fraction(str(instance)) / Fraction(str(divisor))).denominator == 1
    except (ValueError, ZeroDivisionError, OverflowError):
        valid = False
    if not valid:
        yield ValidationError("number is not an exact decimal multiple")


DecimalDraft202012Validator = validators.extend(
    Draft202012Validator, {"multipleOf": _decimal_multiple_of})


class HttpFailure(Exception):
    def __init__(self, status: int, code: str, *, retry_after: float = 1):
        super().__init__(code)
        self.status, self.code, self.retry_after = status, code, retry_after

    def payload(self, request_id=None, execution_id=None):
        return dict(reasonCode=self.code, message=self.code, requestId=request_id,
                    executionId=execution_id, retryable=self.status in (429, 503, 502, 504))


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("invalid number")


def parse_json(raw: bytes):
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=_invalid_constant)
        # JCS also rejects lone Unicode surrogates, out-of-range integers and NaN.
        rfc8785.dumps(value)
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise HttpFailure(400, "INVALID_JSON") from None


@lru_cache(maxsize=32)
def validator(kind: str, result: bool = False, *, request_version: str = REQUEST_V1):
    root = Path(__file__).resolve().parents[3]
    filename = "runpod_result_v1.schema.json" if result else "runpod_http_control_v1.schema.json"
    if request_version != REQUEST_V1:
        if result or kind != "analysisRequest" or request_version not in (REQUEST_V2, REQUEST_V3, REQUEST_V4):
            raise HttpFailure(422, "VALIDATION_FAILED")
        filename = "runpod_analysis_request_v3.schema.json" if request_version == REQUEST_V3 else "runpod_analysis_request_v2.schema.json"
        if request_version == REQUEST_V4:
            filename = "runpod_analysis_request_v4.schema.json"
    schema = json.loads((root / "docs/contracts" / filename).read_text(encoding="utf-8"))
    schema["$ref"] = "#/$defs/" + kind
    return DecimalDraft202012Validator(schema, format_checker=FormatChecker())


def validate(value, kind: str, *, result: bool = False, request_version: str = REQUEST_V1):
    check = validator(kind, result, request_version=request_version)
    error = next(check.iter_errors(value), None)
    if error is not None:
        if result:
            # Schema-owned names only; never log input values or exception text.
            logging.getLogger(__name__).warning(
                "result_schema_rejected keyword=%s schemaPath=%s", error.validator,
                "/".join(map(str, error.absolute_schema_path)))
        raise HttpFailure(422, "VALIDATION_FAILED")
    _utf16_lengths(value, check.schema, check.schema)
    return value


def validate_analysis_request(body):
    """Select a closed request schema without modifying the wire document."""
    if not isinstance(body, dict):
        raise HttpFailure(422, "VALIDATION_FAILED")
    version = body.get("schemaVersion")
    if not isinstance(version, str) or version not in (REQUEST_V3, REQUEST_V4):
        raise HttpFailure(422, "VALIDATION_FAILED")
    validate(body, "analysisRequest", request_version=version)
    if version == REQUEST_V4:
        from .media_preparation import MediaPreparation
        from voice_coach.visual_feedback.parallel_analysis import MediaIntegrityFailure
        if body.get("video") is None:
            raise HttpFailure(422, "VALIDATION_FAILED")
        try:
            MediaPreparation.from_wire(body["mediaPreparation"], audio_sha256=body["audio"]["sha256"],
                                       video_sha256=body["video"]["sha256"])
        except MediaIntegrityFailure:
            raise HttpFailure(422, "VALIDATION_FAILED") from None
    if version == REQUEST_V2 and (
            body.get("analysisProfile") != CANONICAL_PROFILE
            or body.get("resultSchemaVersion") != RESULT_V4):
        raise HttpFailure(422, "VALIDATION_FAILED")
    return body


def _utf16_lengths(value, node, root):
    if "$ref" in node:
        target = root
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        _utf16_lengths(value, target, root)
    if isinstance(value, str) and "maxLength" in node:
        if len(value.encode("utf-16-le")) // 2 > node["maxLength"]:
            raise HttpFailure(422, "VALIDATION_FAILED")
    if isinstance(value, dict):
        for key, child in node.get("properties", {}).items():
            if key in value:
                _utf16_lengths(value[key], child, root)
    if isinstance(value, list) and isinstance(node.get("items"), dict):
        for child in value:
            _utf16_lengths(child, node["items"], root)
    for keyword in ("anyOf", "oneOf", "allOf"):
        for child in node.get(keyword, []):
            _utf16_lengths(value, child, root)


def digest(value) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def object_key(value: str, prefix: str):
    if (not prefix or not prefix.endswith("/") or not value.startswith(prefix)
            or ":" in value or "\\" in value or value.startswith("/")
            or any(part in ("", ".", "..", "users", "sessions") for part in value.split("/"))
            or any(ord(c) < 32 for c in value)):
        raise HttpFailure(422, "VALIDATION_FAILED")


@dataclass(frozen=True, slots=True)
class HttpVisualInput:
    object_key: str
    sha256: str
    mime_type: str
    file_size_bytes: int
    duration_ms: int | None


@dataclass(frozen=True, slots=True)
class AnalysisInput:
    """Admitted media and prompt passed to the core, without authorization claims."""
    event_id: UUID
    execution_id: UUID
    analysis_id: int
    recording_id: int
    content_id: int
    prompt_revision: str
    script_text: str
    script_sha256: str
    audio_object_key: str
    audio_sha256: str
    mime_type: str
    file_size_bytes: int
    duration_ms: int
    learning_focus: str
    visual_input: HttpVisualInput | None
    request_schema_version: str = REQUEST_V1
    analysis_profile: str = LEGACY_PROFILE
    # Internal legacy routing default; v1 wire bytes and v1-v3 output stay unchanged.
    result_schema_version: str = RESULT_V3
    media_preparation: dict | None = field(default=None, repr=False)

    @property
    def is_audiovisual(self):
        return (self.request_schema_version, self.result_schema_version, self.analysis_profile) == (
            REQUEST_V4, RESULT_V6, AUDIOVISUAL_PROFILE)

    @property
    def is_legacy(self):
        return (self.request_schema_version == REQUEST_V1
                and self.analysis_profile == LEGACY_PROFILE
                and self.result_schema_version == RESULT_V3)

    @property
    def closed_beta_context(self):
        # Legacy core debug capture is never enabled by the HTTP transport.
        return None

    @classmethod
    def from_payload(cls, body: dict, prefix: str):
        validate_analysis_request(body)
        for key, limit in (("scriptText", 20000), ("promptRevision", 100)):
            if len(body[key].encode("utf-16-le")) // 2 > limit:
                raise HttpFailure(422, "VALIDATION_FAILED")
        audio, video = body["audio"], body["video"]
        for media in (audio, video):
            if media is not None:
                object_key(media["objectKey"], prefix)
                if len(media["objectKey"].encode("utf-16-le")) // 2 > 1000:
                    raise HttpFailure(422, "VALIDATION_FAILED")
        if hashlib.sha256(body["scriptText"].encode("utf-8")).hexdigest() != body["scriptSha256"]:
            raise HttpFailure(422, "VALIDATION_FAILED")
        return cls(UUID(body["requestId"]), UUID(body["executionId"]), body["analysisId"],
                   body["recordingId"], body["contentId"], body["promptRevision"], body["scriptText"],
                   body["scriptSha256"], audio["objectKey"], audio["sha256"], audio["mimeType"],
                   audio["fileSizeBytes"], audio["durationMs"], body["learningFocus"],
                   HttpVisualInput(object_key=video["objectKey"], sha256=video["sha256"],
                                       mime_type=video["mimeType"], file_size_bytes=video["fileSizeBytes"],
                                       duration_ms=video["durationMs"]) if video else None,
                   request_schema_version=body["schemaVersion"],
                   analysis_profile=(body["analysisProfile"] if body["schemaVersion"] in (REQUEST_V2, REQUEST_V3, REQUEST_V4)
                                     else LEGACY_PROFILE),
                   result_schema_version=(body["resultSchemaVersion"] if body["schemaVersion"] in (REQUEST_V2, REQUEST_V3, REQUEST_V4)
                                          else RESULT_V3),
                   media_preparation=body.get("mediaPreparation"))


def result_payload(result: AnalysisWorkerResult, request: AnalysisInput, worker_id: str):
    if not request.is_legacy:
        # This serializer cannot represent canonical results, including failures.
        raise HttpFailure(422, "VALIDATION_FAILED")
    evidence = result.pronunciation_evidence
    if evidence and evidence.selected_end_ms is not None and evidence.selected_end_ms > request.duration_ms:
        raise HttpFailure(422, "VALIDATION_FAILED")
    visual = result.visual_supplement.to_payload() if result.visual_supplement else None
    if visual:
        visual = {k: v for k, v in visual.items() if k in validator("result", True).schema["$defs"]["visualSupplement"]["properties"]}
    body = dict(schemaVersion="voice-coaching.runpod-analysis-result.v1", eventId=str(result.event_id),
                requestId=str(request.event_id), executionId=str(request.execution_id),
                workerInstanceId=worker_id, analysisId=request.analysis_id, recordingId=request.recording_id,
                status=result.status, outcome=result.outcome.value if result.outcome else None,
                summaryFeedback=result.summary_feedback, pronunciationEvidence=evidence.to_payload() if evidence else None,
                visualSupplement=visual, audioSha256=result.audio_sha256, workerRevision=result.worker_revision,
                pipelineRevision=result.pipeline_revision, failureCode=result.failure_code, failureReason=result.failure_reason)
    if result.scoring_evidence is not None:
        body.update(schemaVersion="voice-coaching.runpod-analysis-result.v2",
                    overallScore=result.overall_score, scoringEvidence=result.scoring_evidence)
    if result.coaching is not None:
        body.update(schemaVersion="voice-coaching.runpod-analysis-result.v3",
                    overallScore=None, scoringEvidence=None, coaching=result.coaching)
    validate(body, "result", result=True)
    if len(rfc8785.dumps(body)) > MAX_RESULT_BYTES:
        raise HttpFailure(413, "PAYLOAD_TOO_LARGE")
    return body
