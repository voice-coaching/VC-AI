"""A ``ReferenceBankPort`` backed by an on-disk manifest of generated exemplars.

Loop A's output has to survive between the run that generated it and the attempt
that plays it, so the bank lives in a JSON manifest next to the audio. This adapter
reads that manifest and hands back a :class:`ReferenceBank`.

What it enforces is narrower than what it reads. Every exemplar is re-checked against
the licence allowlist, its audio must still be present, and its digest must still
match — an exemplar whose file changed since the manifest was written is dropped, not
served. The learner-facing path has no way to say "you may hear this but it is not
what was selected", so the only safe representation of doubt is absence.

The manifest is never written by this class. A reader that could repair what it reads
would let a failed selection quietly become a passing one.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from ..domain import ReferenceBank, ReferenceExemplar
from ..multi_reference import filter_rights_cleared

MANIFEST_SCHEMA_VERSION = "voice_coach.reference_bank_store.v1"
#: Verifying a digest costs a full read of every exemplar, so the caller chooses.
DEFAULT_VERIFY_DIGESTS = True


class ReferenceBankStoreError(ValueError):
    """Raised when a manifest cannot be read within the contract."""


@dataclass(frozen=True, slots=True)
class StoredExemplar:
    """One manifest row: an exemplar plus where its audio lives."""

    exemplar: ReferenceExemplar
    audio_path: Path


def _require(row: Mapping[str, Any], key: str) -> Any:
    if key not in row:
        raise ReferenceBankStoreError(f"exemplar row is missing {key}")
    return row[key]


def exemplar_from_row(row: Mapping[str, Any], *, base_dir: Path) -> StoredExemplar:
    """Build an exemplar from a manifest row, failing closed on a missing field."""

    audio_relative = str(_require(row, "audio_relative_path"))
    if Path(audio_relative).is_absolute() or ".." in Path(audio_relative).parts:
        # A manifest that can point outside its own directory can serve any file
        # on the machine to a learner.
        raise ReferenceBankStoreError("audio_relative_path must stay inside the bank")
    exemplar = ReferenceExemplar(
        exemplar_id=str(_require(row, "exemplar_id")),
        prompt_id=str(_require(row, "prompt_id")),
        generator_id=str(_require(row, "generator_id")),
        generator_revision=str(_require(row, "generator_revision")),
        audio_sha256=str(_require(row, "audio_sha256")),
        duration_seconds=float(_require(row, "duration_seconds")),
        sample_rate=int(_require(row, "sample_rate")),
        speaker_axis=str(_require(row, "speaker_axis")),
        speed_axis=str(_require(row, "speed_axis")),
        license_spdx=str(_require(row, "license_spdx")),
        rights_basis=str(_require(row, "rights_basis")),
        synthetic=bool(_require(row, "synthetic")),
        disclosure=row.get("disclosure"),
    )
    return StoredExemplar(exemplar=exemplar, audio_path=base_dir / audio_relative)


def row_from_exemplar(exemplar: ReferenceExemplar, *, audio_relative_path: str) -> dict:
    """The inverse, so a generator run can write a manifest this store can read."""

    return {
        "exemplar_id": exemplar.exemplar_id,
        "prompt_id": exemplar.prompt_id,
        "generator_id": exemplar.generator_id,
        "generator_revision": exemplar.generator_revision,
        "audio_sha256": exemplar.audio_sha256,
        "audio_relative_path": audio_relative_path,
        "duration_seconds": exemplar.duration_seconds,
        "sample_rate": exemplar.sample_rate,
        "speaker_axis": exemplar.speaker_axis,
        "speed_axis": exemplar.speed_axis,
        "license_spdx": exemplar.license_spdx,
        "rights_basis": exemplar.rights_basis,
        "synthetic": exemplar.synthetic,
        "disclosure": exemplar.disclosure,
    }


class JsonReferenceBankStore:
    """Serves banks from a manifest, dropping anything it cannot vouch for."""

    def __init__(
        self,
        *,
        manifest_path: str | Path,
        allowed_licenses: tuple[str, ...],
        verify_digests: bool = DEFAULT_VERIFY_DIGESTS,
        bank_id: str = "json-reference-bank",
    ) -> None:
        if not allowed_licenses:
            # An empty allowlist admits nothing, which is safe but silently empty;
            # saying so here is clearer than an unexplained empty bank later.
            raise ReferenceBankStoreError("allowed_licenses must not be empty")
        self._manifest_path = Path(manifest_path)
        self._allowed = tuple(allowed_licenses)
        self._verify = verify_digests
        self._bank_id = bank_id

    @property
    def bank_id(self) -> str:
        return self._bank_id

    def _read_manifest(self) -> Mapping[str, Any]:
        if not self._manifest_path.is_file():
            return {}
        try:
            payload = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ReferenceBankStoreError("the bank manifest is not valid JSON") from error
        if not isinstance(payload, Mapping):
            raise ReferenceBankStoreError("the bank manifest is not an object")
        if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise ReferenceBankStoreError("the bank manifest schema version differs")
        return payload

    def _playable(self, stored: StoredExemplar) -> bool:
        if not stored.audio_path.is_file():
            return False
        if not self._verify:
            return True
        from .reference_generator import sha256_file

        return sha256_file(stored.audio_path) == stored.exemplar.audio_sha256

    def bank_for(
        self,
        *,
        prompt_id: str,
        speed_axis: str | None = None,
        speaker_axis: str | None = None,
    ) -> ReferenceBank:
        payload = self._read_manifest()
        if not payload:
            return ReferenceBank(
                prompt_id=prompt_id,
                exemplars=(),
                selection_id="",
                selection_rule="no_bank_manifest_present",
            )
        base_dir = self._manifest_path.parent
        rows = payload.get("exemplars")
        if not isinstance(rows, list):
            raise ReferenceBankStoreError("the bank manifest has no exemplar list")

        stored: list[StoredExemplar] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise ReferenceBankStoreError("an exemplar row is not an object")
            if row.get("prompt_id") != prompt_id:
                continue
            item = exemplar_from_row(row, base_dir=base_dir)
            if speed_axis is not None and item.exemplar.speed_axis != speed_axis:
                continue
            if speaker_axis is not None and item.exemplar.speaker_axis != speaker_axis:
                continue
            stored.append(item)

        cleared = filter_rights_cleared(
            [item.exemplar for item in stored], allowed_licenses=self._allowed
        )
        cleared_ids = {item.exemplar_id for item in cleared}
        playable = tuple(
            item.exemplar
            for item in stored
            if item.exemplar.exemplar_id in cleared_ids and self._playable(item)
        )
        return ReferenceBank(
            prompt_id=prompt_id,
            exemplars=playable,
            selection_id=str(payload.get("selection_id", "")) if playable else "",
            selection_rule=str(payload.get("selection_rule", "unstated")),
            diversity_axes=tuple(payload.get("diversity_axes", ())),
        )

    def audio_path_for(self, exemplar: ReferenceExemplar) -> Path:
        """Resolve an exemplar back to its file, for the distance adapter."""

        payload = self._read_manifest()
        rows = payload.get("exemplars", []) if payload else []
        for row in rows:
            if isinstance(row, Mapping) and row.get("exemplar_id") == exemplar.exemplar_id:
                return exemplar_from_row(
                    row, base_dir=self._manifest_path.parent
                ).audio_path
        raise ReferenceBankStoreError("the exemplar is not in this bank")


__all__ = [
    "DEFAULT_VERIFY_DIGESTS",
    "MANIFEST_SCHEMA_VERSION",
    "JsonReferenceBankStore",
    "ReferenceBankStoreError",
    "StoredExemplar",
    "exemplar_from_row",
    "row_from_exemplar",
]
