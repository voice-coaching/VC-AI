"""Canonical JSON and strict primitive validation without external I/O."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any

from ..errors import VisualFeedbackContractError

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SAFE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def canonical_json_bytes(value: object) -> bytes:
    """Return the only JSON representation used for integrity digests."""

    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise VisualFeedbackContractError(
            "value is not canonical JSON data"
        ) from error
    return serialized.encode("utf-8")


def sha256_document(value: object) -> str:
    """Hash a JSON-compatible value using the canonical representation."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def require_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise VisualFeedbackContractError(f"{name} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise VisualFeedbackContractError(f"{name} keys must be strings")
    return value


def require_exact_keys(
    value: Mapping[str, Any], expected: set[str], name: str
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise VisualFeedbackContractError(
            f"{name} fields differ; missing={missing}, extra={extra}"
        )


def require_non_empty_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VisualFeedbackContractError(f"{name} must be a non-empty string")
    return value


def require_safe_identifier(value: object, name: str) -> str:
    identifier = require_non_empty_string(value, name)
    if not SAFE_IDENTIFIER_PATTERN.fullmatch(identifier):
        raise VisualFeedbackContractError(f"{name} is not a safe identifier")
    return identifier


def require_text_token(value: object, name: str, *, maximum_length: int = 64) -> str:
    """Validate a short Unicode token such as a Korean phone label.

    Public identifiers stay ASCII-only through ``require_safe_identifier``.  Phone
    labels are content, not identifiers, so they must retain canonical Hangul/Jamo.
    """

    token = require_non_empty_string(value, name)
    if len(token) > maximum_length:
        raise VisualFeedbackContractError(
            f"{name} must contain at most {maximum_length} characters"
        )
    if any(character.isspace() or ord(character) < 32 for character in token):
        raise VisualFeedbackContractError(
            f"{name} must not contain whitespace or control characters"
        )
    return token


def require_sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise VisualFeedbackContractError(f"{name} must be lowercase SHA-256")
    return value


def require_integer(value: object, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise VisualFeedbackContractError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise VisualFeedbackContractError(f"{name} must be >= {minimum}")
    return value


def require_finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VisualFeedbackContractError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise VisualFeedbackContractError(f"{name} must be finite")
    return number


def require_boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise VisualFeedbackContractError(f"{name} must be a boolean")
    return value


def require_string_tuple(value: object, name: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise VisualFeedbackContractError(f"{name} must be an array")
    result = tuple(require_non_empty_string(item, f"{name}[]") for item in value)
    if len(set(result)) != len(result):
        raise VisualFeedbackContractError(f"{name} must not contain duplicates")
    return result


def require_utc_timestamp(value: object, name: str) -> str:
    timestamp = require_non_empty_string(value, name)
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise VisualFeedbackContractError(f"{name} must be ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise VisualFeedbackContractError(f"{name} must include a UTC offset")
    if parsed.utcoffset().total_seconds() != 0:
        raise VisualFeedbackContractError(f"{name} must be UTC")
    return timestamp


__all__ = [
    "canonical_json_bytes",
    "require_boolean",
    "require_exact_keys",
    "require_finite_number",
    "require_integer",
    "require_mapping",
    "require_non_empty_string",
    "require_safe_identifier",
    "require_sha256",
    "require_string_tuple",
    "require_text_token",
    "require_utc_timestamp",
    "sha256_document",
]
