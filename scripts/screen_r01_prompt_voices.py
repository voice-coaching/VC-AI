#!/usr/bin/env python3
"""Screen prompt voices before committing a full R01 generation run.

One bad prompt voice costs 60 cells x max_attempts renderings before the run is
over, and the failure is not random: a voice that overruns does it every time, at
exactly the same length, because the model runs to its token cap rather than
emitting an end-of-speech token.  So each voice gets one rendering of one sentence
first, and only the voices that land inside the measured speech-rate band are used.

**Screening removes voices, it does not certify them.**  Measured on the full R01 v5
run, a voice that passed screening at 4.61 syl/s went on to fail 61.5% of its 60
cells: whether the model finishes a sentence depends on the (voice, sentence) pair,
not on the voice alone.  So a passing screen means "not obviously broken", and the
generation-time gate is what actually decides each cell.  Screening on more
sentences would sharpen this, at one model load per rendering.

The second question this answers is whether retrying is worth anything at all. If
two renderings of the same cell come back byte-identical, generation is
deterministic and `max_attempts` above 1 is pure cost.

Usage:
    screen_r01_prompt_voices.py --config CONFIG [--sentence R00-01]
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from voice_coach.pronunciation_feedback.adapters.reference_generator import (  # noqa: E402
    CosyVoiceReferenceGenerator,
    ReferenceGeneratorError,
    VoicePrompt,
)

_spec = importlib.util.spec_from_file_location(
    "generate_r01", Path(__file__).resolve().parent / "generate_r01_candidates.py"
)
_generate = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules[_spec.name] = _generate
_spec.loader.exec_module(_generate)
#: Screening must measure exactly what the generation gate measures, or a voice can
#: pass here and fail there.
speech_region_seconds = _generate.speech_region_seconds

MODEL_DIR = Path(
    "data/cache/huggingface/hub/models--FunAudioLLM--CosyVoice2-0.5B/snapshots"
    "/eec1ae6c79877dbd9379285cf8789c9e0879293d"
)


def _load_yaml(path: Path) -> dict:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def voices_from_manifest(manifest_path: Path, *, workspace_root: Path) -> list[dict]:
    """Read a prompt-voice manifest into the shape a contract's section has.

    Screening a candidate set should not require writing a contract first: the
    point of screening is to find out which voices are worth putting in one.
    """

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base = manifest_path.parent
    return [
        {
            "prompt_voice_id": entry["prompt_voice_id"],
            "audio_path": str(
                (base / entry["audio_relative_path"]).relative_to(workspace_root)
            ),
            "license_spdx": entry["license_spdx"],
            "rights_basis": entry["rights_basis"],
            "speaker_axis": entry["speaker_axis"],
            "prompt_text": entry["prompt_transcript"],
            "demographic_axis": f"{entry['gender']}/{entry['age_band']}",
        }
        for entry in manifest["prompt_voices"]
    ]


def screen(
    *,
    workspace_root: Path,
    config: dict,
    sentence_id: str,
    output_dir: Path,
    repeat_first: bool = True,
) -> dict:
    card = json.loads(
        (workspace_root / config["canonical_sources"]["r00_card"]["path"]).read_text(
            encoding="utf-8"
        )
    )
    sentence = next(
        item for item in card["sentences"] if item["sentence_id"] == sentence_id
    )
    band = json.loads(
        (
            workspace_root
            / config["canonical_sources"]["read_speech_rate_band"]["path"]
        ).read_text(encoding="utf-8")
    )["band"]
    low = float(band["low_syllables_per_second"])
    high = float(band["high_syllables_per_second"])
    syllables = int(sentence["syllable_count"])

    generator = CosyVoiceReferenceGenerator(
        repository_root=workspace_root,
        model_dir=workspace_root / MODEL_DIR,
        output_dir=output_dir,
        disclosure=config["learner_facing"]["synthetic_disclosure_ko"],
        gpu_allowed=False,
    )

    rows: list[dict] = []
    repeat_digests: list[str] = []
    for index, entry in enumerate(config["prompt_voices"]):
        voice = VoicePrompt(
            prompt_id=entry["prompt_voice_id"],
            audio_path=workspace_root / entry["audio_path"],
            license_spdx=entry["license_spdx"],
            rights_basis=entry["rights_basis"],
            speaker_axis=entry["speaker_axis"],
            prompt_text=entry["prompt_text"],
        )
        attempts = 2 if (index == 0 and repeat_first) else 1
        for attempt in range(attempts):
            target = output_dir / f"{voice.prompt_id}.attempt{attempt}"
            target.mkdir(parents=True, exist_ok=True)
            generator._output_dir = target  # noqa: SLF001 - screening only
            try:
                exemplar, path = generator.synthesise(
                    prompt_id=sentence_id,
                    text=sentence["normalized_text"],
                    voice=voice,
                    speed_bucket="neutral",
                )
            except ReferenceGeneratorError as error:
                rows.append(
                    {
                        "prompt_voice_id": voice.prompt_id,
                        "attempt": attempt,
                        "error": str(error),
                    }
                )
                print(f"  {voice.prompt_id[-16:]}  FAILED {error}", flush=True)
                continue
            spoken = speech_region_seconds(path)
            seconds = spoken or exemplar.duration_seconds
            rate = syllables / seconds
            usable = low <= rate <= high
            if index == 0 and repeat_first:
                repeat_digests.append(
                    hashlib.sha256(path.read_bytes()).hexdigest()
                )
            rows.append(
                {
                    "prompt_voice_id": voice.prompt_id,
                    "demographic_axis": entry.get("demographic_axis"),
                    "attempt": attempt,
                    "duration_seconds": exemplar.duration_seconds,
                    "speech_region_seconds": round(seconds, 4),
                    "speech_rate_measured_on": (
                        "speech_region" if spoken else "whole_file"
                    ),
                    "speech_rate_syllables_per_second": round(rate, 4),
                    "inside_band": usable,
                }
            )
            print(
                f"  {voice.prompt_id[-16:]}  {str(entry.get('demographic_axis')):14s}"
                f"  file {exemplar.duration_seconds:6.2f}s  spoken {seconds:6.2f}s"
                f"  {rate:5.2f} syl/s  {'ok' if usable else 'OUT OF BAND'}",
                flush=True,
            )

    usable_ids = sorted(
        {
            row["prompt_voice_id"]
            for row in rows
            if row.get("inside_band") and row["attempt"] == 0
        }
    )
    deterministic = (
        len(repeat_digests) == 2 and repeat_digests[0] == repeat_digests[1]
    )
    return {
        "schema_version": "voice_coach.r01_prompt_voice_screening.v1",
        "sentence_id": sentence_id,
        "band_syllables_per_second": [low, high],
        "generation_is_deterministic": deterministic if repeat_digests else None,
        "usable_prompt_voice_ids": usable_ids,
        "unusable_prompt_voice_ids": sorted(
            {entry["prompt_voice_id"] for entry in config["prompt_voices"]}
            - set(usable_ids)
        ),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--prompt-manifest",
        type=Path,
        default=None,
        help="screen these voices instead of the contract's, for candidate sets",
    )
    parser.add_argument("--sentence", default="R00-01")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    config = _load_yaml(root / args.config if not args.config.is_absolute() else args.config)
    if args.prompt_manifest is not None:
        config = dict(config)
        config["prompt_voices"] = voices_from_manifest(
            args.prompt_manifest.resolve(), workspace_root=root
        )
    report = screen(
        workspace_root=root,
        config=config,
        sentence_id=args.sentence,
        output_dir=args.out,
    )
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "screening_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print()
    print(f"usable: {len(report['usable_prompt_voice_ids'])}"
          f"/{len(config['prompt_voices'])}")
    print(f"deterministic: {report['generation_is_deterministic']}")
    for voice_id in report["unusable_prompt_voice_ids"]:
        print(f"  unusable: {voice_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
