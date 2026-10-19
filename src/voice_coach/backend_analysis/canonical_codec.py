"""Private canonical document codec and schema boundary, without result assembly.

Keep this separate from the legacy HTTP/JCS codec: retained MFA decimals and
original artifact bytes must not be rounded through the HTTP parser. No storage,
receipt authority, frozen dispatch or admission is implemented here.
"""
from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from .canonical_bridge import MAX_CORE_BYTES, CanonicalIntegrationError, _require


def read_document(raw: bytes, limit: int = MAX_CORE_BYTES):
    """Strict UTF-8, duplicate rejection and lossless finite JSON decimals."""
    _require(type(raw) is bytes and 0 < len(raw) <= limit, "CANONICAL_DOCUMENT_SIZE")

    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def invalid(_):
        raise CanonicalIntegrationError("NONFINITE_JSON")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_float=Decimal, parse_constant=invalid)
        encode_document(value)  # Reject lone surrogates, including diagnostic map keys.
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise CanonicalIntegrationError("CANONICAL_JSON_INVALID") from None


def encode_document(value) -> bytes:
    """Preserve JSON decimal values; raw artifact SHA still uses original bytes.

    This is not JCS. The HTTP owner computes the separate callback JCS digest.
    Avoid a JSONB/float roundtrip before storing the original artifacts.
    """
    def encode(item):
        if isinstance(item, Decimal):
            _require(item.is_finite(), "NONFINITE_JSON")
            return str(item)
        if isinstance(item, dict):
            _require(all(type(k) is str for k in item), "CANONICAL_JSON_INVALID")
            return "{" + ",".join(encode(k) + ":" + encode(v) for k, v in item.items()) + "}"
        if isinstance(item, (list, tuple)):
            return "[" + ",".join(encode(v) for v in item) + "]"
        return json.dumps(item, ensure_ascii=False, allow_nan=False, separators=(",", ":"))

    return encode(value).encode("utf-8")


@lru_cache(maxsize=6)
def _schema(name: str):
    _require(name in {"parent", "input", "output", "v4", "v6", "handoff2", "control", "journal"}, "CANONICAL_SCHEMA_UNKNOWN")
    if name == "handoff2":
        filename = "runpod_canonical_handoff_v2.schema.json"
    elif name == "v6":
        filename = "runpod_result_v6.schema.json"
    elif name == "journal":
        filename = "runpod_canonical_journal_v1.schema.json"
    elif name == "control":
        filename = "runpod_http_control_v1_2.schema.json"
    elif name == "v4":
        filename = "runpod_result_v4.schema.json"
    else:
        filename = f"runpod_canonical_{name}.schema.json"
    path = Path(__file__).resolve().parents[3] / "docs" / "contracts" / filename
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise CanonicalIntegrationError("CANONICAL_SCHEMA_UNAVAILABLE") from None


def validate_document(value, definition: str | None = None, *, schema_name: str = "v4"):
    schema = _schema(schema_name)
    if definition is not None:
        schema = dict(schema, **{"$ref": "#/$defs/" + definition})
    try:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
    except ValidationError:
        # jsonschema exception messages contain private instance values.
        raise CanonicalIntegrationError("CANONICAL_SCHEMA_INVALID") from None
