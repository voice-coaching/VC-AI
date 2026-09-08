"""Expected-phone frontend that reaches the pinned g2pK stack in another venv.

``.venv-scorer`` is pinned by the S104/S105 execution locks, so g2pK cannot be
installed into it without changing the environment those runs recorded.  This
adapter therefore keeps the pinned stack where it already is and crosses the
interpreter boundary once per request, re-verifying the returned token digest on
this side so a corrupted or substituted reply cannot pass.

Failure is always ``expected_phones_unavailable``.  There is no grapheme
fallback: a written jamo stream is not a rule-applied expected pronunciation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping, Sequence

from voice_coach.phonology.g2pk_expected import (
    EXPECTED_G2PK_VERSION,
    WordBoundaryPolicy,
    expected_phone_token_digest,
)

from ..domain import ExpectedPhoneSequence, FailureCode
from ..errors import BackendUnavailableError
from .expected_phones import BACKEND_ID, DEFAULT_MAXIMUM_CODEPOINTS

WORKER_PROTOCOL_VERSION = "voice_coach.g2pk_expected_phones_worker.v1"
#: Punctuation the R00 prompt catalog actually contains: 18 prompts end in a
#: period and 2 are questions.  A narrower set would fail closed on the two
#: interrogatives, and the set is part of the token digest, so it is declared
#: here rather than being widened silently per call.
TRACK_E_ALLOWED_PUNCTUATION: tuple[str, ...] = (".", "?")
DEFAULT_WORKER_RELATIVE = Path("scripts/g2pk_expected_phones_worker.py")
DEFAULT_INTERPRETER_RELATIVE = Path(".venv-paper-g2pk/bin/python")
DEFAULT_TIMEOUT_SECONDS = 120.0
#: The subprocess needs no inherited configuration; anything else is a way for
#: ambient state (proxies, tokens, alternate data roots) to change the result.
_ALLOWED_ENVIRONMENT_KEYS = ("LANG", "LC_ALL", "PATH", "TMPDIR")


def _fail(detail: str) -> BackendUnavailableError:
    return BackendUnavailableError(FailureCode.EXPECTED_PHONES_UNAVAILABLE, detail)


class SubprocessG2PKExpectedPhoneFrontend:
    """``ExpectedPhonePort`` over the pinned g2pK interpreter in another venv."""

    def __init__(
        self,
        *,
        nltk_data_root: str | Path,
        repository_root: str | Path,
        interpreter: str | Path | None = None,
        worker_script: str | Path | None = None,
        descriptive: bool = False,
        group_vowels: bool = False,
        word_boundary_policy: WordBoundaryPolicy = "preserve",
        allowed_punctuation: tuple[str, ...] = TRACK_E_ALLOWED_PUNCTUATION,
        maximum_codepoints: int = DEFAULT_MAXIMUM_CODEPOINTS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        cache_results: bool = True,
    ) -> None:
        root = Path(repository_root)
        if not root.is_absolute():
            raise ValueError("repository_root must be absolute")
        self._root = root
        self._nltk_data_root = Path(nltk_data_root)
        self._interpreter = Path(
            interpreter if interpreter is not None else root / DEFAULT_INTERPRETER_RELATIVE
        )
        self._worker = Path(
            worker_script if worker_script is not None else root / DEFAULT_WORKER_RELATIVE
        )
        self._descriptive = descriptive
        self._group_vowels = group_vowels
        self._word_boundary_policy = word_boundary_policy
        self._allowed_punctuation = allowed_punctuation
        self._maximum_codepoints = maximum_codepoints
        self._timeout_seconds = timeout_seconds
        self._cache: dict[str, ExpectedPhoneSequence] | None = (
            {} if cache_results else None
        )

    # -- port ------------------------------------------------------------

    def expected_phones(self, normalized_text: str) -> ExpectedPhoneSequence:
        return self.expected_phones_batch((normalized_text,))[0]

    def expected_phones_batch(
        self,
        normalized_texts: Sequence[str],
    ) -> tuple[ExpectedPhoneSequence, ...]:
        """One interpreter round trip for many prompts.

        g2pK startup dominates a single call, so E8-scale batches go through
        here.  Results are deterministic, so repeated prompts are served from an
        in-process cache rather than re-crossing the boundary.
        """

        if not normalized_texts:
            raise _fail("normalized_texts must not be empty")
        if any(not isinstance(text, str) or not text for text in normalized_texts):
            raise _fail("normalized_texts must contain non-empty strings")

        unique = list(dict.fromkeys(normalized_texts))
        if self._cache is None:
            computed = dict(zip(unique, self._invoke(unique), strict=True))
            return tuple(computed[text] for text in normalized_texts)
        pending = [text for text in unique if text not in self._cache]
        if pending:
            self._cache.update(zip(pending, self._invoke(pending), strict=True))
        return tuple(self._cache[text] for text in normalized_texts)

    # -- internals -------------------------------------------------------

    def _environment(self) -> dict[str, str]:
        environment = {
            key: os.environ[key]
            for key in _ALLOWED_ENVIRONMENT_KEYS
            if key in os.environ
        }
        environment["PYTHONPATH"] = str(self._root / "src")
        environment["PYTHONHASHSEED"] = "0"
        environment["PYTHONNOUSERSITE"] = "1"
        return environment

    def _request(self, texts: Sequence[str]) -> str:
        return json.dumps(
            {
                "protocol_version": WORKER_PROTOCOL_VERSION,
                "texts": list(texts),
                "descriptive": self._descriptive,
                "group_vowels": self._group_vowels,
                "word_boundary_policy": self._word_boundary_policy,
                "allowed_punctuation": list(self._allowed_punctuation),
                "maximum_codepoints": self._maximum_codepoints,
                "nltk_data_root": str(self._nltk_data_root),
            },
            ensure_ascii=False,
        )

    def _invoke(self, texts: Sequence[str]) -> tuple[ExpectedPhoneSequence, ...]:
        if not self._interpreter.is_file():
            raise _fail("the pinned g2pK interpreter is unavailable")
        if not self._worker.is_file():
            raise _fail("the expected-phone worker script is unavailable")
        try:
            completed = subprocess.run(
                [str(self._interpreter), str(self._worker)],
                input=self._request(texts),
                capture_output=True,
                text=True,
                cwd=str(self._root),
                env=self._environment(),
                timeout=self._timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise _fail("the expected-phone worker timed out") from error
        except OSError as error:
            raise _fail("the expected-phone worker could not be started") from error

        try:
            payload = json.loads(completed.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _fail("the expected-phone worker returned invalid JSON") from error
        if not isinstance(payload, Mapping):
            raise _fail("the expected-phone worker returned a non-object")
        if completed.returncode != 0 or payload.get("status") != "ok":
            detail = payload.get("error")
            raise _fail(
                "g2pK expected-phone frontend unusable: "
                + (str(detail) if detail else "worker failed")
            )
        if payload.get("protocol_version") != WORKER_PROTOCOL_VERSION:
            raise _fail("the expected-phone worker protocol version differs")
        if payload.get("expected_g2pk_version") != EXPECTED_G2PK_VERSION:
            raise _fail("the expected-phone worker g2pK pin differs")

        results = payload.get("results")
        if not isinstance(results, list) or len(results) != len(texts):
            raise _fail("the expected-phone worker returned the wrong result count")
        return tuple(self._sequence(record) for record in results)

    def _sequence(self, record: Any) -> ExpectedPhoneSequence:
        if not isinstance(record, Mapping):
            raise _fail("expected-phone record must be an object")
        tokens = record.get("phone_tokens")
        if not isinstance(tokens, list) or not tokens:
            raise _fail("expected-phone record has no tokens")
        if not all(isinstance(token, str) and token for token in tokens):
            raise _fail("expected-phone tokens must be non-empty strings")
        digest = record.get("token_sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise _fail("expected-phone record has no token digest")
        revision = record.get("g2pk_version")
        if revision != EXPECTED_G2PK_VERSION:
            raise _fail("expected-phone record g2pK revision differs")
        recomputed = expected_phone_token_digest(
            g2pk_version=revision,
            descriptive=self._descriptive,
            group_vowels=self._group_vowels,
            word_boundary_policy=self._word_boundary_policy,
            allowed_punctuation=self._allowed_punctuation,
            phone_tokens=tuple(tokens),
        )
        if recomputed != digest:
            # The digest is the only integrity anchor across the boundary, and it
            # covers the option set too: a worker run under different options
            # cannot pass its result off as this configuration's output.
            raise _fail("expected-phone token digest does not match the tokens")
        return ExpectedPhoneSequence(
            tokens=tuple(tokens),
            token_sha256=digest,
            backend_id=BACKEND_ID,
            revision=revision,
            per_syllable_token_counts=None,
        )


__all__ = [
    "DEFAULT_INTERPRETER_RELATIVE",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_WORKER_RELATIVE",
    "TRACK_E_ALLOWED_PUNCTUATION",
    "WORKER_PROTOCOL_VERSION",
    "SubprocessG2PKExpectedPhoneFrontend",
]
