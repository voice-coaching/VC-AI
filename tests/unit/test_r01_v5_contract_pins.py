"""The v3 contract pins its own sources; this is what notices when they drift.

v2 pinned the generator module and worker by path only, so the code that produced
its bank could change without anything failing — which is how the trim defect
recorded in DEV-R01-003 survived. Every digest the contract states is checked here
against the file on disk, so a change to the worker breaks a test instead of
quietly producing a differently-generated bank under the same contract id.

A failure here means one of two things, and they are not the same repair:

- the code changed on purpose → write a v4 contract, do not re-pin v3
- the code changed by accident → revert it
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/experiments/r01_multi_speaker_candidates_v5.yaml"
PROMPT_MANIFEST_KEY = "prompt_voice_manifest"


@pytest.fixture(scope="module")
def config() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_every_pinned_source_matches_its_digest(config: dict) -> None:
    drifted = []
    for name, spec in config["canonical_sources"].items():
        if "sha256" not in spec:
            continue
        path = ROOT / spec["path"]
        if not path.is_file():
            drifted.append(f"{name}: absent")
            continue
        if _digest(path) != spec["sha256"]:
            drifted.append(f"{name}: {spec['path']}")

    assert drifted == [], "pinned sources drifted: " + ", ".join(drifted)


def test_the_generator_and_worker_are_pinned_by_digest_not_only_path(
    config: dict,
) -> None:
    """The gap v2 had. Naming a path pins nothing."""

    sources = config["canonical_sources"]
    for name in ("generator_module", "worker", "read_speech_rate_band", "driver"):
        assert "sha256" in sources[name], f"{name} is pinned by path only"


def test_the_declared_band_evidence_matches_the_measured_artifact(
    config: dict,
) -> None:
    band = json.loads(
        (ROOT / config["canonical_sources"]["read_speech_rate_band"]["path"]).read_text(
            encoding="utf-8"
        )
    )
    gate = config["candidate_design"]["plausibility_gate"]

    assert gate["band_source_utterances"] == band["utterances"]
    assert band["band"]["low_syllables_per_second"] < band["band"][
        "high_syllables_per_second"
    ]
    assert band["speaker_level_data_stored"] is False


def test_the_prompt_voice_count_matches_the_declared_limitation(config: dict) -> None:
    voices = config["prompt_voices"]

    assert len(voices) == config["known_limitations"]["distinct_prompt_voices"]
    assert (
        len(voices)
        == config["candidate_design"]["diversity_preservation"][
            "required_distinct_speakers"
        ]
    )


def test_the_prompt_set_spans_both_genders(config: dict) -> None:
    """Not balanced — screening removed four female voices — but not single-gender.

    Every usable female voice the screening found is here. A bank that had ended up
    male-only would still be usable now that the f0 axis compares pitch range rather
    than register, but it would cost representativeness, so the minority gender is
    kept deliberately rather than by luck.
    """

    genders = [
        entry["demographic_axis"].split("/")[0] for entry in config["prompt_voices"]
    ]

    assert set(genders) == {"Female", "Male"}
    assert genders.count("Female") == 2
    assert genders.count("Male") == 4


def test_no_prompt_voice_carries_a_corpus_speaker_name(config: dict) -> None:
    """`speaker_axis` reaches every exemplar row, so it must be a digest."""

    for entry in config["prompt_voices"]:
        axis = entry["speaker_axis"]
        assert axis.startswith("aihub542-")
        assert len(axis) == len("aihub542-") + 16
        assert entry["prompt_voice_id"] == axis
        assert entry["third_party_speaker_cloning"] is True
        assert "DEV-R01-002" in entry["rights_basis"]


def test_the_prompt_manifest_digest_matches_and_stores_no_names(config: dict) -> None:
    spec = config["canonical_sources"][PROMPT_MANIFEST_KEY]
    path = ROOT / spec["path"]
    if not path.is_file():
        pytest.skip("the restricted prompt manifest is not present in this checkout")

    manifest = json.loads(path.read_text(encoding="utf-8"))

    assert _digest(path) == spec["sha256"]
    assert manifest["speaker_names_stored"] is False
    assert manifest["decision_record"] == "DEV-R01-002"


# -- what the contract must keep refusing --------------------------------------


def test_the_contract_still_closes_the_human_comparison_claim(config: dict) -> None:
    """542 speakers were cloned as prompts; they did not read the R00 sentences."""

    closed = config["claims_closed_by_this_amendment"]

    assert closed["human_comparison_available"] is False
    assert closed["r04_human_versus_tts_comparison_available"] is False
    assert config["candidate_design"]["source_balance"]["human_candidates_expected"] == 0


def test_repair_and_publication_stay_forbidden(config: dict) -> None:
    gate = config["candidate_design"]["plausibility_gate"]

    assert gate["repair_or_trim_allowed"] is False
    assert gate["rejected_audio_retained"] is True
    assert config["publication"]["publication_allowed"] is False
    assert config["publication"]["r02_hard_gate_passed"] is False
    assert config["cosyvoice"]["redistribution_allowed"] is False
    assert config["cosyvoice"]["fine_tuning_allowed"] is False


def test_the_predecessor_is_recorded_rather_than_removed(config: dict) -> None:
    """Both predecessors stay on disk: they are the evidence for this contract."""

    predecessor = config["superseded_predecessor"]

    assert predecessor["status"] == "abandoned_before_completion"
    assert predecessor["bank_manifest_written"] is False
    rejected = ROOT / predecessor["rejected_audio_kept"]
    assert rejected.is_dir(), "the rejected renderings are the evidence"
    # v2's defective bank is still on disk and still marked.
    earlier = ROOT / "artifacts/reference/r01/cosyvoice_only_v2"
    assert (earlier / "bank.json").is_file()
    assert (earlier / "SUPERSEDED.md").is_file()


def test_the_screening_that_chose_these_voices_is_recorded(config: dict) -> None:
    """A bank built from a screening verdict must say which verdict, and how many
    candidates it rejected. Otherwise the gender skew below looks like a choice."""

    screened = config["canonical_sources"]["prompt_voice_manifest"]["screened_by"]

    assert screened["candidates_screened"] == 12
    assert screened["candidates_usable"] == 8
    limitation = config["known_limitations"]["generator_limitation_observed"]
    assert limitation["affected_voices"] == 4
    assert limitation["affected_voices_female"] == 4
    assert limitation["affected_voices_male"] == 0
    assert config["known_limitations"]["prompt_voice_gender_balance"] == (
        "2_female_4_male"
    )


def test_the_worker_is_pinned_to_zero_shot_and_every_prompt_has_a_transcript(
    config: dict,
) -> None:
    """Zero-shot without the prompt transcript is what overran every sentence."""

    assert config["canonical_sources"]["worker"]["inference_mode"] == "zero_shot"
    gate = config["candidate_design"]["plausibility_gate"]
    # Retrying is measured to be worthless: generation is deterministic.
    assert gate["max_attempts"] == 1
    # The band was measured on the corpus's marked speech region, so the rate must
    # be too; comparing a whole-file duration against it compares two quantities.
    assert gate["measured_on"] == "spoken_region_not_whole_file"
    for entry in config["prompt_voices"]:
        assert entry["prompt_text"].strip()
        # Bounded by twice the shortest R00 sentence (10 syllables).
        assert 12 <= entry["prompt_syllable_count"] <= 20
        assert 1.5 <= entry["prompt_seconds"] <= 6.0


def test_the_locked_and_holdout_paths_stay_closed(config: dict) -> None:
    runtime = config["runtime"]

    assert runtime["locked_test_access_allowed"] is False
    assert runtime["expert_holdout_access_allowed"] is False
    assert runtime["actual_gpu_access_allowed"] is False
    assert runtime["environment_files_forbidden"] is True
