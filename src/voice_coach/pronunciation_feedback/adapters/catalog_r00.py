"""Read-only practice prompt catalog backed by the fixed R00 sentence card.

The card (``data/reference/r00/r00_sentence_card_v1.json``) is the only prompt
source this pipeline serves by default: its 20 sentences are project-original
CC0 text with frozen difficulty quota, phone coverage, PII and prohibited
content checks, and a reserved-prompt collision check against AI Hub.

The adapter validates identity before serving anything.  It never rewrites the
card, and it never exposes an expected pronunciation to the learner payload.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from ..domain import FailureCode, PracticePrompt, TargetLanguage
from ..errors import PromptUnavailableError
from .syllables import normalize_prompt_text, text_sha256

DEFAULT_CARD_PATH = Path("data/reference/r00/r00_sentence_card_v1.json")
EXPECTED_SCHEMA_VERSION = "voice_coach.r00_sentence_card.v1"
EXPECTED_CARD_ID = "voice-coach-r00-fixed-korean-sentences-v1"
EXPECTED_STATUS = "fixed_validated_sentence_text_only"


def _fail(detail: str) -> PromptUnavailableError:
    return PromptUnavailableError(FailureCode.PROMPT_CATALOG_UNAVAILABLE, detail)


def _require_mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(f"{field_name} must be an object")
    return value


def _require_str(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(f"{field_name} must be a non-empty string")
    return value


def _require_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise _fail(f"{field_name} must be a positive integer")
    return value


def _require_str_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _fail(f"{field_name} must be an array")
    return tuple(_require_str(item, f"{field_name}[]") for item in value)


def _rule_target_ids(value: object) -> tuple[str, ...]:
    """Read practice-target ids from the card's phonological rule entries.

    Only the taxonomy id becomes a practice target.  ``expected_realization_hangul``
    stays behind: its ``annotation_status`` is still pending human pronunciation
    review, so it is not a scoring target and must not be served as one.
    """

    if not isinstance(value, list):
        raise _fail("phonological_rule_targets must be an array")
    ids: list[str] = []
    for position, entry in enumerate(value):
        record = _require_mapping(entry, f"phonological_rule_targets[{position}]")
        ids.append(
            _require_str(
                record.get("taxonomy_stable_id"),
                "phonological_rule_targets[].taxonomy_stable_id",
            )
        )
    return tuple(ids)


class R00PromptCatalog:
    """``PromptCatalogPort`` implementation over the fixed sentence card."""

    def __init__(self, card_path: str | Path = DEFAULT_CARD_PATH) -> None:
        path = Path(card_path)
        if not path.is_file():
            raise _fail(f"sentence card not found: {path.name}")
        raw_bytes = path.read_bytes()
        try:
            card = json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _fail(f"sentence card is not valid UTF-8 JSON: {path.name}") from exc
        card_mapping = _require_mapping(card, "sentence card")

        schema_version = card_mapping.get("schema_version")
        card_id = card_mapping.get("card_id")
        status = card_mapping.get("status")
        if schema_version != EXPECTED_SCHEMA_VERSION:
            raise _fail(f"unexpected schema_version: {schema_version!r}")
        if card_id != EXPECTED_CARD_ID:
            raise _fail(f"unexpected card_id: {card_id!r}")
        if status != EXPECTED_STATUS:
            raise _fail(f"unexpected card status: {status!r}")

        license_spdx = _require_str(
            _require_mapping(
                card_mapping.get("source_license"), "source_license"
            ).get("license_spdx"),
            "source_license.license_spdx",
        )
        card_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        self._revision = f"{EXPECTED_CARD_ID}@{card_sha256[:16]}"
        self._prompts = self._load_prompts(
            card_mapping.get("sentences"),
            license_spdx=license_spdx,
        )
        if not self._prompts:
            raise _fail("sentence card contains no sentences")

    def _load_prompts(
        self,
        sentences: object,
        *,
        license_spdx: str,
    ) -> dict[str, PracticePrompt]:
        if not isinstance(sentences, list):
            raise _fail("sentences must be an array")
        prompts: dict[str, PracticePrompt] = {}
        for position, entry in enumerate(sentences):
            record = _require_mapping(entry, f"sentences[{position}]")
            prompt_id = _require_str(record.get("sentence_id"), "sentence_id")
            if prompt_id in prompts:
                raise _fail(f"duplicate sentence_id: {prompt_id}")
            display_text = _require_str(record.get("text"), "text")
            declared_normalized = _require_str(
                record.get("normalized_text"), "normalized_text"
            )
            declared_digest = _require_str(
                record.get("normalized_text_sha256"), "normalized_text_sha256"
            )
            recomputed_normalized = normalize_prompt_text(display_text)
            if recomputed_normalized != declared_normalized:
                raise _fail(
                    f"{prompt_id}: normalized_text does not match the card "
                    "normalization rule"
                )
            if text_sha256(declared_normalized) != declared_digest:
                raise _fail(f"{prompt_id}: normalized_text_sha256 mismatch")
            for check_field in ("pii_check", "prohibited_content_check"):
                check = _require_mapping(record.get(check_field), check_field)
                if check.get("status") != "passed":
                    raise _fail(f"{prompt_id}: {check_field} did not pass")
            targets = _rule_target_ids(
                record.get("phonological_rule_targets")
            ) + _require_str_tuple(record.get("prosody_targets"), "prosody_targets")
            prompts[prompt_id] = PracticePrompt(
                prompt_id=prompt_id,
                display_text=display_text,
                normalized_text=declared_normalized,
                normalized_text_sha256=declared_digest,
                difficulty=_require_str(record.get("difficulty"), "difficulty"),
                syllable_count=_require_int(
                    record.get("syllable_count"), "syllable_count"
                ),
                word_count=_require_int(record.get("word_count"), "word_count"),
                practice_targets=targets,
                license_spdx=_require_str(
                    record.get("license_spdx"), "license_spdx"
                )
                or license_spdx,
                catalog_revision=self._revision,
                target_language=TargetLanguage.KOREAN,
            )
        return prompts

    @property
    def revision(self) -> str:
        return self._revision

    def list_prompts(
        self,
        *,
        difficulty: str | None = None,
        target: str | None = None,
        limit: int | None = None,
    ) -> tuple[PracticePrompt, ...]:
        if limit is not None and limit <= 0:
            raise PromptUnavailableError(
                FailureCode.PROMPT_INVALID, "limit must be positive"
            )
        selected = [
            prompt
            for prompt in self._prompts.values()
            if (difficulty is None or prompt.difficulty == difficulty)
            and (target is None or target in prompt.practice_targets)
        ]
        if not selected:
            raise PromptUnavailableError(
                FailureCode.PROMPT_NOT_AVAILABLE,
                "no prompt matches the requested difficulty/target",
            )
        selected.sort(key=lambda prompt: prompt.prompt_id)
        if limit is not None:
            selected = selected[:limit]
        return tuple(selected)

    def get_prompt(self, prompt_id: str) -> PracticePrompt:
        prompt = self._prompts.get(prompt_id)
        if prompt is None:
            raise PromptUnavailableError(
                FailureCode.PROMPT_NOT_AVAILABLE,
                f"unknown prompt_id: {prompt_id}",
            )
        return prompt
