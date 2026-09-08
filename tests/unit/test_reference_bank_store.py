"""The bank store must drop anything it cannot vouch for, rather than serve it.

Every test here is about the same question: when the manifest and the filesystem
disagree, does a learner hear the file anyway? The answer must be no, because the
player has no way to render "this is playable but not what was selected".
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from voice_coach.pronunciation_feedback.adapters.reference_bank_store import (
    MANIFEST_SCHEMA_VERSION,
    JsonReferenceBankStore,
    ReferenceBankStoreError,
    exemplar_from_row,
    row_from_exemplar,
)
from voice_coach.pronunciation_feedback.domain import ReferenceExemplar

ALLOWED = ("Apache-2.0", "CC0-1.0")


def _write_audio(path: Path, *, hz: float = 200.0, seconds: float = 1.0) -> str:
    sample_rate = 24_000
    t = np.arange(int(sample_rate * seconds)) / sample_rate
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, (0.2 * np.sin(2 * np.pi * hz * t)).astype("float32"), sample_rate)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _row(
    tmp_path: Path,
    *,
    exemplar_id: str,
    prompt_id: str = "R00-01",
    license_spdx: str = "Apache-2.0",
    speaker_axis: str = "demo-a",
    speed_axis: str = "neutral",
    hz: float = 200.0,
) -> dict:
    relative = f"audio/{exemplar_id}.wav"
    digest = _write_audio(tmp_path / relative, hz=hz)
    return {
        "exemplar_id": exemplar_id,
        "prompt_id": prompt_id,
        "generator_id": "cosyvoice2-0.5b",
        "generator_revision": "074ca6dc9e80+eec1ae6c7987",
        "audio_sha256": digest,
        "audio_relative_path": relative,
        "duration_seconds": 1.0,
        "sample_rate": 24_000,
        "speaker_axis": speaker_axis,
        "speed_axis": speed_axis,
        "license_spdx": license_spdx,
        "rights_basis": "vendor demo asset",
        "synthetic": True,
        "disclosure": "합성된 참조음입니다.",
    }


def _manifest(tmp_path: Path, rows: list[dict], **extra) -> Path:
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "selection_id": "sel-1",
        "selection_rule": "test",
        "diversity_axes": ["speaker", "speed"],
        "exemplars": rows,
    }
    payload.update(extra)
    path = tmp_path / "bank.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _store(manifest: Path, **overrides) -> JsonReferenceBankStore:
    options = {"manifest_path": manifest, "allowed_licenses": ALLOWED}
    options.update(overrides)
    return JsonReferenceBankStore(**options)


# -- normal service -----------------------------------------------------------


def test_a_manifest_yields_its_exemplars(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path, [_row(tmp_path, exemplar_id="a"), _row(tmp_path, exemplar_id="b")]
    )

    bank = _store(manifest).bank_for(prompt_id="R00-01")

    assert {item.exemplar_id for item in bank.exemplars} == {"a", "b"}
    assert bank.selection_id == "sel-1"
    assert bank.diversity_axes == ("speaker", "speed")


def test_other_prompts_are_not_served(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path,
        [
            _row(tmp_path, exemplar_id="a", prompt_id="R00-01"),
            _row(tmp_path, exemplar_id="b", prompt_id="R00-02"),
        ],
    )

    bank = _store(manifest).bank_for(prompt_id="R00-02")

    assert [item.exemplar_id for item in bank.exemplars] == ["b"]


def test_axes_narrow_the_bank(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path,
        [
            _row(tmp_path, exemplar_id="a", speed_axis="slow"),
            _row(tmp_path, exemplar_id="b", speed_axis="fast"),
        ],
    )

    bank = _store(manifest).bank_for(prompt_id="R00-01", speed_axis="fast")

    assert [item.exemplar_id for item in bank.exemplars] == ["b"]


def test_a_row_round_trips_through_the_manifest_shape(tmp_path: Path) -> None:
    original = exemplar_from_row(_row(tmp_path, exemplar_id="a"), base_dir=tmp_path)
    rebuilt = exemplar_from_row(
        row_from_exemplar(original.exemplar, audio_relative_path="audio/a.wav"),
        base_dir=tmp_path,
    )

    assert rebuilt.exemplar == original.exemplar


# -- what must never be served -----------------------------------------------


def test_a_disallowed_licence_is_dropped(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path,
        [
            _row(tmp_path, exemplar_id="a"),
            _row(tmp_path, exemplar_id="b", license_spdx="CC-BY-NC-SA-4.0"),
        ],
    )

    bank = _store(manifest).bank_for(prompt_id="R00-01")

    assert [item.exemplar_id for item in bank.exemplars] == ["a"]


def test_a_changed_file_is_dropped(tmp_path: Path) -> None:
    """The manifest recorded one rendering; the disk now holds another."""

    rows = [_row(tmp_path, exemplar_id="a"), _row(tmp_path, exemplar_id="b")]
    manifest = _manifest(tmp_path, rows)
    _write_audio(tmp_path / "audio/b.wav", hz=440.0)

    bank = _store(manifest).bank_for(prompt_id="R00-01")

    assert [item.exemplar_id for item in bank.exemplars] == ["a"]


def test_an_absent_file_is_dropped(tmp_path: Path) -> None:
    rows = [_row(tmp_path, exemplar_id="a"), _row(tmp_path, exemplar_id="b")]
    manifest = _manifest(tmp_path, rows)
    (tmp_path / "audio/b.wav").unlink()

    bank = _store(manifest).bank_for(prompt_id="R00-01")

    assert [item.exemplar_id for item in bank.exemplars] == ["a"]


def test_a_path_escaping_the_bank_is_rejected(tmp_path: Path) -> None:
    """Otherwise a manifest could serve any file on the machine to a learner."""

    row = _row(tmp_path, exemplar_id="a")
    row["audio_relative_path"] = "../../etc/hostname"
    manifest = _manifest(tmp_path, [row])

    with pytest.raises(ReferenceBankStoreError, match="stay inside the bank"):
        _store(manifest).bank_for(prompt_id="R00-01")


def test_an_absolute_path_is_rejected(tmp_path: Path) -> None:
    row = _row(tmp_path, exemplar_id="a")
    row["audio_relative_path"] = "/etc/hostname"
    manifest = _manifest(tmp_path, [row])

    with pytest.raises(ReferenceBankStoreError, match="stay inside the bank"):
        _store(manifest).bank_for(prompt_id="R00-01")


def test_a_row_missing_its_rights_basis_fails_closed(tmp_path: Path) -> None:
    row = _row(tmp_path, exemplar_id="a")
    del row["rights_basis"]
    manifest = _manifest(tmp_path, [row])

    with pytest.raises(ReferenceBankStoreError, match="rights_basis"):
        _store(manifest).bank_for(prompt_id="R00-01")


def test_synthetic_audio_without_a_disclosure_fails_closed(tmp_path: Path) -> None:
    row = _row(tmp_path, exemplar_id="a")
    row["disclosure"] = None
    manifest = _manifest(tmp_path, [row])

    with pytest.raises(ValueError, match="disclosure"):
        _store(manifest).bank_for(prompt_id="R00-01")


# -- absence is a valid answer ------------------------------------------------


def test_no_manifest_yields_an_empty_bank(tmp_path: Path) -> None:
    bank = _store(tmp_path / "absent.json").bank_for(prompt_id="R00-01")

    assert bank.is_empty
    assert bank.selection_rule == "no_bank_manifest_present"


def test_an_empty_allowlist_is_rejected_at_construction(tmp_path: Path) -> None:
    with pytest.raises(ReferenceBankStoreError, match="allowed_licenses"):
        JsonReferenceBankStore(manifest_path=tmp_path / "b.json", allowed_licenses=())


def test_a_schema_version_mismatch_fails_closed(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path, [_row(tmp_path, exemplar_id="a")], schema_version="v0"
    )

    with pytest.raises(ReferenceBankStoreError, match="schema version"):
        _store(manifest).bank_for(prompt_id="R00-01")


def test_invalid_json_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "bank.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ReferenceBankStoreError, match="valid JSON"):
        _store(path).bank_for(prompt_id="R00-01")


# -- resolving audio back for the distance adapter ----------------------------


def test_audio_path_resolves_for_a_known_exemplar(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, [_row(tmp_path, exemplar_id="a")])
    store = _store(manifest)
    bank = store.bank_for(prompt_id="R00-01")

    path = store.audio_path_for(bank.exemplars[0])

    assert path == tmp_path / "audio/a.wav"
    assert path.is_file()


def test_an_unknown_exemplar_cannot_be_resolved(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, [_row(tmp_path, exemplar_id="a")])
    stranger = ReferenceExemplar(
        exemplar_id="zzz",
        prompt_id="R00-01",
        generator_id="g",
        generator_revision="r",
        audio_sha256="a" * 64,
        duration_seconds=1.0,
        sample_rate=24_000,
        speaker_axis="x",
        speed_axis="neutral",
        license_spdx="Apache-2.0",
        rights_basis="test",
        synthetic=False,
    )

    with pytest.raises(ReferenceBankStoreError, match="not in this bank"):
        _store(manifest).audio_path_for(stranger)
