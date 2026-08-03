"""Strict, offline g2pK adapter for expected Korean phone sequences.

This module is intentionally separate from realized-phone TextGrid
normalization.  g2pK predicts a transcript-conditioned expected pronunciation;
it must never overwrite observed pronunciation labels or manual-audit gold.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import importlib
from importlib import metadata
import json
from pathlib import Path
import stat
import sys
from threading import Lock
from types import MappingProxyType
from typing import Literal, Protocol, cast
import unicodedata

from .phones import KOREAN_COMPATIBILITY_JAMO


EXPECTED_G2PK_VERSION = "0.9.4"
EXPECTED_G2PK_DEPENDENCIES: Mapping[str, str] = MappingProxyType(
    {
        "g2pK": EXPECTED_G2PK_VERSION,
        "jamo": "0.4.1",
        "nltk": "3.10.0",
        "python-mecab-ko": "1.3.7",
        "python-mecab-ko-dic": "2.1.1.post2",
    }
)
EXPECTED_CMUDICT_FILES: Mapping[str, str] = MappingProxyType(
    {
        "corpora/cmudict.zip": (
            "d07cca47fd72ad32ea9d8ad1219f85301eeaf4568f8b6b73747506a71fb5afd6"
        ),
        "corpora/cmudict/cmudict": (
            "cad209c39eb87677d64e93d97f8eed10b7e6f9bdd42de8e7ca8efc8e17d62e8a"
        ),
        "corpora/cmudict/README": (
            "b0556be7a2b12bea6a667277b75864f802dd4854480657ce298c62e6897e766b"
        ),
    }
)

WordBoundaryPolicy = Literal["drop", "preserve"]


class G2PKExpectedPhoneError(ValueError):
    """Raised when the expected-pronunciation contract is violated."""


class _G2PKBackend(Protocol):
    def __call__(
        self,
        text: str,
        *,
        descriptive: bool,
        verbose: bool,
        group_vowels: bool,
        to_syl: bool,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class G2PKExpectedPhones:
    """Expected phones and replay metadata without retaining the input text."""

    g2pk_version: str
    descriptive: bool
    group_vowels: bool
    word_boundary_policy: WordBoundaryPolicy
    allowed_punctuation: tuple[str, ...]
    phone_tokens: tuple[str, ...]
    phone_count: int
    word_boundary_count: int
    token_sha256: str


_ONSET_TO_COMPATIBILITY = dict(
    zip(
        "ᄀᄁᄂᄃᄄᄅᄆᄇᄈᄉᄊᄋᄌᄍᄎᄏᄐᄑᄒ",
        "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ",
        strict=True,
    )
)
_VOWEL_TO_COMPATIBILITY = dict(
    zip(
        "ᅡᅢᅣᅤᅥᅦᅧᅨᅩᅪᅫᅬᅭᅮᅯᅰᅱᅲᅳᅴᅵ",
        "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ",
        strict=True,
    )
)
_CODA_TO_COMPATIBILITY = dict(
    zip(
        "ᆨᆩᆫᆮᆯᆷᆸᆺᆻᆼᆽᆾᆿᇀᇁᇂ",
        "ㄱㄲㄴㄷㄹㅁㅂㅅㅆㅇㅈㅊㅋㅌㅍㅎ",
        strict=True,
    )
)
_CONJOINING_TO_COMPATIBILITY = {
    **_ONSET_TO_COMPATIBILITY,
    **_VOWEL_TO_COMPATIBILITY,
    **_CODA_TO_COMPATIBILITY,
}
_COMPATIBILITY_PHONE_SET = frozenset(KOREAN_COMPATIBILITY_JAMO)
_BACKEND_LOAD_LOCK = Lock()
_G2PK_IMPORTED_BY_THIS_ADAPTER = False


def _validate_bool(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        raise G2PKExpectedPhoneError(f"{field_name} must be a boolean")
    return cast(bool, value)


def _validate_text(text: object, maximum_codepoints: object) -> str:
    if (
        isinstance(maximum_codepoints, bool)
        or not isinstance(maximum_codepoints, int)
        or not 1 <= maximum_codepoints <= 1_000_000
    ):
        raise G2PKExpectedPhoneError(
            "maximum_codepoints must be an integer between 1 and 1000000"
        )
    if type(text) is not str:
        raise G2PKExpectedPhoneError("text must be a plain string")
    normalized = cast(str, text)
    if not normalized or not normalized.strip():
        raise G2PKExpectedPhoneError("text must not be empty or whitespace-only")
    if len(normalized) > maximum_codepoints:
        raise G2PKExpectedPhoneError("text exceeds maximum_codepoints")
    if normalized != normalized.strip():
        raise G2PKExpectedPhoneError(
            "text must not contain leading or trailing whitespace"
        )
    if unicodedata.normalize("NFC", normalized) != normalized:
        raise G2PKExpectedPhoneError("text must already be NFC-normalized")
    if any(
        unicodedata.category(character) in {"Cc", "Cs", "Co"}
        for character in normalized
    ):
        raise G2PKExpectedPhoneError(
            "text contains a forbidden control, surrogate, or private-use character"
        )
    return normalized


def _validate_output_policy(
    *,
    word_boundary_policy: object,
    allowed_punctuation: object,
) -> tuple[WordBoundaryPolicy, tuple[str, ...]]:
    if not isinstance(word_boundary_policy, str) or word_boundary_policy not in {
        "drop",
        "preserve",
    }:
        raise G2PKExpectedPhoneError(
            "word_boundary_policy must be exactly 'drop' or 'preserve'"
        )
    if type(allowed_punctuation) is not tuple:
        raise G2PKExpectedPhoneError("allowed_punctuation must be a tuple")
    punctuation = cast(tuple[object, ...], allowed_punctuation)
    if any(
        type(character) is not str or len(character) != 1 for character in punctuation
    ):
        raise G2PKExpectedPhoneError(
            "allowed_punctuation entries must be single plain-string codepoints"
        )
    typed_punctuation = cast(tuple[str, ...], punctuation)
    if len(set(typed_punctuation)) != len(typed_punctuation):
        raise G2PKExpectedPhoneError("allowed_punctuation must not contain duplicates")
    if tuple(sorted(typed_punctuation)) != typed_punctuation:
        raise G2PKExpectedPhoneError("allowed_punctuation must be sorted")
    if any(
        not unicodedata.category(character).startswith("P")
        for character in typed_punctuation
    ):
        raise G2PKExpectedPhoneError(
            "allowed_punctuation entries must be Unicode punctuation"
        )
    return (
        cast(WordBoundaryPolicy, word_boundary_policy),
        typed_punctuation,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_runtime_data_root(root_value: object) -> Path:
    if not isinstance(root_value, (str, Path)):
        raise G2PKExpectedPhoneError(
            "nltk_data_root must be an absolute directory path"
        )
    root = Path(root_value)
    if not root.is_absolute():
        raise G2PKExpectedPhoneError(
            "nltk_data_root must be an absolute directory path"
        )
    try:
        resolved = root.resolve(strict=True)
    except (OSError, RuntimeError):
        raise G2PKExpectedPhoneError(
            "the pinned NLTK data root is unavailable"
        ) from None
    if resolved != root or not root.is_dir() or root.is_symlink():
        raise G2PKExpectedPhoneError(
            "the pinned NLTK data root must be a real directory without symlinks"
        )
    for relative_path, expected_sha256 in EXPECTED_CMUDICT_FILES.items():
        artifact = root / relative_path
        try:
            artifact_stat = artifact.lstat()
        except OSError:
            raise G2PKExpectedPhoneError(
                "the pinned CMUdict artifact set is incomplete"
            ) from None
        if (
            not stat.S_ISREG(artifact_stat.st_mode)
            or artifact.is_symlink()
            or artifact.resolve(strict=True) != artifact
        ):
            raise G2PKExpectedPhoneError(
                "the pinned CMUdict artifact set contains an unsafe file"
            )
        try:
            actual_sha256 = _sha256_file(artifact)
        except OSError:
            raise G2PKExpectedPhoneError(
                "the pinned CMUdict artifact set could not be verified"
            ) from None
        if actual_sha256 != expected_sha256:
            raise G2PKExpectedPhoneError(
                "the pinned CMUdict artifact digest differs from the contract"
            )
    return root


def _validate_distribution_versions() -> None:
    for distribution_name, expected_version in EXPECTED_G2PK_DEPENDENCIES.items():
        try:
            actual_version = metadata.version(distribution_name)
        except metadata.PackageNotFoundError:
            raise G2PKExpectedPhoneError(
                "the pinned g2pK runtime is unavailable"
            ) from None
        if actual_version != expected_version:
            raise G2PKExpectedPhoneError(
                "the g2pK runtime differs from the pinned environment contract"
            )


def _load_pinned_backend(nltk_data_root: Path) -> _G2PKBackend:
    global _G2PK_IMPORTED_BY_THIS_ADAPTER

    _validate_distribution_versions()
    with _BACKEND_LOAD_LOCK:
        if "g2pk" in sys.modules and not _G2PK_IMPORTED_BY_THIS_ADAPTER:
            raise G2PKExpectedPhoneError(
                "g2pK was imported before the offline runtime gate"
            )
        try:
            nltk = importlib.import_module("nltk")
        except ImportError:
            raise G2PKExpectedPhoneError(
                "the pinned g2pK runtime is unavailable"
            ) from None

        data = getattr(nltk, "data", None)
        original_download = getattr(nltk, "download", None)
        original_paths = list(getattr(data, "path", ()))
        if data is None or original_download is None:
            raise G2PKExpectedPhoneError("the pinned NLTK runtime is invalid")

        def _forbid_download(*args: object, **kwargs: object) -> bool:
            del args, kwargs
            raise G2PKExpectedPhoneError(
                "network download is forbidden in the g2pK runtime"
            )

        try:
            data.path[:] = [str(nltk_data_root)]
            setattr(nltk, "download", _forbid_download)
            g2pk_module = importlib.import_module("g2pk")
            backend_factory = getattr(g2pk_module, "G2p", None)
            if not callable(backend_factory):
                raise G2PKExpectedPhoneError("the pinned g2pK runtime is invalid")
            backend = backend_factory()
            _G2PK_IMPORTED_BY_THIS_ADAPTER = True
        except G2PKExpectedPhoneError:
            raise
        except (ImportError, LookupError, OSError, RuntimeError, TypeError, ValueError):
            raise G2PKExpectedPhoneError(
                "the pinned offline g2pK runtime failed to initialize"
            ) from None
        finally:
            data.path[:] = original_paths
            setattr(nltk, "download", original_download)
    return cast(_G2PKBackend, backend)


def _canonicalize_backend_output(
    output: object,
    *,
    word_boundary_policy: WordBoundaryPolicy,
    allowed_punctuation: tuple[str, ...],
) -> tuple[str, ...]:
    if type(output) is not str:
        raise G2PKExpectedPhoneError(
            "g2pK returned an invalid expected-pronunciation result"
        )
    raw_output = cast(str, output)
    if not raw_output:
        raise G2PKExpectedPhoneError(
            "g2pK returned an empty expected-pronunciation result"
        )
    punctuation = frozenset(allowed_punctuation)
    tokens: list[str] = []
    pending_boundary = False
    for character in raw_output:
        mapped = _CONJOINING_TO_COMPATIBILITY.get(character)
        if mapped is not None:
            if (
                word_boundary_policy == "preserve"
                and pending_boundary
                and tokens
                and tokens[-1] != "|"
            ):
                tokens.append("|")
            pending_boundary = False
            tokens.append(mapped)
            continue
        if character.isspace():
            pending_boundary = True
            continue
        if character in punctuation:
            continue
        raise G2PKExpectedPhoneError(
            "g2pK output contains a token outside the caller-approved phone policy"
        )
    if not tokens or not any(token in _COMPATIBILITY_PHONE_SET for token in tokens):
        raise G2PKExpectedPhoneError("g2pK output contains no expected Korean phones")
    if any(token not in _COMPATIBILITY_PHONE_SET and token != "|" for token in tokens):
        raise G2PKExpectedPhoneError("g2pK output contains a non-canonical phone token")
    return tuple(tokens)


def _token_digest(
    *,
    g2pk_version: str,
    descriptive: bool,
    group_vowels: bool,
    word_boundary_policy: WordBoundaryPolicy,
    allowed_punctuation: tuple[str, ...],
    phone_tokens: tuple[str, ...],
) -> str:
    payload = {
        "allowed_punctuation": allowed_punctuation,
        "descriptive": descriptive,
        "g2pk_version": g2pk_version,
        "group_vowels": group_vowels,
        "phone_tokens": phone_tokens,
        "word_boundary_policy": word_boundary_policy,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def generate_g2pk_expected_phones(
    text: object,
    *,
    descriptive: bool,
    group_vowels: bool,
    word_boundary_policy: WordBoundaryPolicy,
    allowed_punctuation: tuple[str, ...],
    maximum_codepoints: int,
    nltk_data_root: str | Path | None = None,
    backend: _G2PKBackend | None = None,
    backend_version: str | None = None,
) -> G2PKExpectedPhones:
    """Convert one transcript into an expected compatibility-jamo phone stream.

    Production calls provide an absolute ``nltk_data_root`` and omit the
    injectable backend.  Contract tests provide ``backend`` and
    ``backend_version`` together and must omit the runtime-data path.
    """

    normalized_text = _validate_text(text, maximum_codepoints)
    descriptive_value = _validate_bool(descriptive, "descriptive")
    group_vowels_value = _validate_bool(group_vowels, "group_vowels")
    boundary_policy, punctuation = _validate_output_policy(
        word_boundary_policy=word_boundary_policy,
        allowed_punctuation=allowed_punctuation,
    )
    if (backend is None) != (backend_version is None):
        raise G2PKExpectedPhoneError(
            "backend and backend_version must be provided together"
        )
    if backend is not None and nltk_data_root is not None:
        raise G2PKExpectedPhoneError(
            "nltk_data_root must be omitted for an injected backend"
        )
    if backend is None:
        if nltk_data_root is None:
            raise G2PKExpectedPhoneError(
                "nltk_data_root is required for the offline production backend"
            )
        verified_root = _validate_runtime_data_root(nltk_data_root)
        selected_backend = _load_pinned_backend(verified_root)
        selected_version = EXPECTED_G2PK_VERSION
    else:
        if backend_version != EXPECTED_G2PK_VERSION:
            raise G2PKExpectedPhoneError(
                "backend version differs from the pinned g2pK contract"
            )
        selected_backend = backend
        selected_version = cast(str, backend_version)
    try:
        raw_output = selected_backend(
            normalized_text,
            descriptive=descriptive_value,
            verbose=False,
            group_vowels=group_vowels_value,
            to_syl=False,
        )
    except G2PKExpectedPhoneError:
        raise
    except Exception:
        raise G2PKExpectedPhoneError(
            "g2pK expected-pronunciation generation failed"
        ) from None
    phone_tokens = _canonicalize_backend_output(
        raw_output,
        word_boundary_policy=boundary_policy,
        allowed_punctuation=punctuation,
    )
    return G2PKExpectedPhones(
        g2pk_version=selected_version,
        descriptive=descriptive_value,
        group_vowels=group_vowels_value,
        word_boundary_policy=boundary_policy,
        allowed_punctuation=punctuation,
        phone_tokens=phone_tokens,
        phone_count=sum(token != "|" for token in phone_tokens),
        word_boundary_count=sum(token == "|" for token in phone_tokens),
        token_sha256=_token_digest(
            g2pk_version=selected_version,
            descriptive=descriptive_value,
            group_vowels=group_vowels_value,
            word_boundary_policy=boundary_policy,
            allowed_punctuation=punctuation,
            phone_tokens=phone_tokens,
        ),
    )
