#!/usr/bin/env python3
"""Expected-phone worker that runs inside the pinned g2pK virtualenv.

The Track E runtime (`.venv-scorer`) deliberately does not carry g2pK, mecab, or
their NLTK data: installing them there would change the environment that the
S104/S105 lock files pin.  This worker is therefore executed by the *other*
interpreter (`.venv-paper-g2pk/bin/python`) and speaks one JSON request/response
over stdin/stdout.

Contract:
  * request and response are single JSON objects; no logging goes to stdout
  * the reply carries only phone tokens, counts, and digests — never the input
    text, a filesystem path, or an environment value
  * any failure exits non-zero with a path-free error string; the caller must
    fail closed rather than fall back to written graphemes
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

SCRIPT_PATH = Path(__file__).resolve()
REPOSITORY_ROOT = SCRIPT_PATH.parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from voice_coach.phonology.g2pk_expected import (  # noqa: E402
    EXPECTED_G2PK_VERSION,
    G2PKExpectedPhoneError,
    generate_g2pk_expected_phones,
)

PROTOCOL_VERSION = "voice_coach.g2pk_expected_phones_worker.v1"
_REQUIRED_FIELDS = (
    "protocol_version",
    "texts",
    "descriptive",
    "group_vowels",
    "word_boundary_policy",
    "allowed_punctuation",
    "maximum_codepoints",
    "nltk_data_root",
)


def _fail(detail: str) -> int:
    json.dump(
        {"status": "failed_closed", "error": detail},
        sys.stdout,
        ensure_ascii=False,
        sort_keys=True,
    )
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 1


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _fail("request is not valid JSON")
    if not isinstance(request, dict):
        return _fail("request must be an object")
    missing = [name for name in _REQUIRED_FIELDS if name not in request]
    if missing:
        return _fail("request is missing fields: " + ", ".join(missing))
    if request["protocol_version"] != PROTOCOL_VERSION:
        return _fail("worker protocol version differs")

    texts = request["texts"]
    if not isinstance(texts, list) or not texts:
        return _fail("texts must be a non-empty array")
    if not all(isinstance(item, str) and item for item in texts):
        return _fail("texts must contain non-empty strings")

    try:
        allowed_punctuation = tuple(request["allowed_punctuation"])
        results = []
        for text in texts:
            phones = generate_g2pk_expected_phones(
                text,
                descriptive=bool(request["descriptive"]),
                group_vowels=bool(request["group_vowels"]),
                word_boundary_policy=request["word_boundary_policy"],
                allowed_punctuation=allowed_punctuation,
                maximum_codepoints=int(request["maximum_codepoints"]),
                nltk_data_root=Path(request["nltk_data_root"]),
            )
            results.append(
                {
                    "phone_tokens": list(phones.phone_tokens),
                    "phone_count": phones.phone_count,
                    "word_boundary_count": phones.word_boundary_count,
                    "token_sha256": phones.token_sha256,
                    "g2pk_version": phones.g2pk_version,
                }
            )
    except G2PKExpectedPhoneError as error:
        return _fail(str(error))
    except (ImportError, LookupError, OSError, TypeError, ValueError):
        return _fail("the pinned offline g2pK runtime failed")

    json.dump(
        {
            "status": "ok",
            "protocol_version": PROTOCOL_VERSION,
            "expected_g2pk_version": EXPECTED_G2PK_VERSION,
            "results": results,
        },
        sys.stdout,
        ensure_ascii=False,
        sort_keys=True,
    )
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
