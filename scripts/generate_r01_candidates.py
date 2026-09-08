#!/usr/bin/env python3
"""Generate the R01 TTS-only reference candidate bank declared by the v2 contract.

Runs the gates the contract lists, in the order it lists them, and stops at the
first one that fails. The gates are cheap and the synthesis is not — an hour of CPU
inference that ends in an unusable bank is the outcome this ordering avoids.

The bank manifest it writes is the one `JsonReferenceBankStore` reads, so a run of
this script is what turns Loop A from a set of types into something the pipeline can
serve.

Usage:
    scripts/generate_r01_candidates.py [--config PATH] [--limit N] [--dry-run]
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from voice_coach.pronunciation_feedback.adapters.reference_bank_store import (  # noqa: E402
    MANIFEST_SCHEMA_VERSION,
    row_from_exemplar,
)
from voice_coach.pronunciation_feedback.adapters.reference_generator import (  # noqa: E402
    SPEED_BUCKETS,
    CosyVoiceReferenceGenerator,
    ReferenceGeneratorError,
    VoicePrompt,
    sha256_file,
)

DEFAULT_CONFIG = Path("configs/experiments/r01_cosyvoice_only_candidates_v2.yaml")
MODEL_DIR = Path(
    "data/cache/huggingface/hub/models--FunAudioLLM--CosyVoice2-0.5B/snapshots"
    "/eec1ae6c79877dbd9379285cf8789c9e0879293d"
)


#: Cores each worker may use. The bridge starts a fresh process per cell, so several
#: run at once; left unset every one of them would claim the whole machine and they
#: would spend their time contending. Threads only affect how fast a cell renders.
THREADS_PER_WORKER = 3
#: Rejected renderings are moved here rather than deleted. A rejected exemplar is the
#: evidence for the rejection, and a bank whose rejections cannot be inspected cannot
#: be argued with.
REJECTED_DIRECTORY_NAME = "rejected"


class GenerationError(RuntimeError):
    """Raised when a contract gate fails."""


def _gate_rate_band(config: dict, root: Path) -> tuple[float, float]:
    """The plausible speech-rate band, read from a measured and pinned artifact.

    Not constants in this file. The band decides which renderings are allowed to
    become references, so it has to come from a population of real read speech and
    stay checkable against the archives it was measured from.
    """

    spec = config["canonical_sources"]["read_speech_rate_band"]
    path = root / spec["path"]
    if not path.is_file():
        raise GenerationError("the measured speech-rate band is absent")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != spec["sha256"]:
        raise GenerationError("the speech-rate band digest differs")
    payload = json.loads(path.read_text(encoding="utf-8"))
    band = payload["band"]
    low = float(band["low_syllables_per_second"])
    high = float(band["high_syllables_per_second"])
    if not 0 < low < high:
        raise GenerationError("the speech-rate band is not an interval")
    declared = config["candidate_design"]["plausibility_gate"]
    if declared["band_source_utterances"] != payload["utterances"]:
        # The contract states how much evidence the band rests on; if the artifact
        # disagrees, one of the two is describing a different measurement.
        raise GenerationError("the band's utterance count differs from the contract")
    return low, high


#: Frame length and relative floor for locating the spoken region of a rendering.
#: These only affect a *measurement*; the audio file is never modified.
SPEECH_REGION_FRAME_SECONDS = 0.02
SPEECH_REGION_RELATIVE_FLOOR = 0.05


def speech_region_seconds(audio_path: Path) -> float | None:
    """Seconds between the first and last frame carrying speech.

    The band this feeds is measured on the corpus's own ``SpeechEnd -
    SpeechStart`` markers, so comparing it against a whole-file duration compares
    two different quantities: a rendering carries leading and trailing silence that
    the corpus rate excluded. Measured on the R00 prompt set the difference decided
    the verdict for five of twelve voices -- 3.39 syl/s whole-file versus 5.12 on
    the spoken region.

    This measures. It does not trim: the exemplar stays exactly as rendered, and an
    earlier attempt to *edit* audio on an energy heuristic removed 63% of one
    recording. A measurement that is wrong costs one rejected rendering; an edit
    that is wrong corrupts the reference every learner is scored against.

    ``None`` when the file cannot be read or carries no frame above the floor.
    """

    try:
        import numpy as np
        import soundfile as sf

        data, sample_rate = sf.read(str(audio_path))
    except (OSError, RuntimeError, ImportError):
        return None
    if data.ndim > 1:
        data = data.mean(axis=1)
    frame = max(1, int(SPEECH_REGION_FRAME_SECONDS * sample_rate))
    count = len(data) // frame
    if count < 1:
        return None
    energy = np.sqrt((data[: count * frame].reshape(count, frame) ** 2).mean(axis=1))
    peak = float(energy.max())
    if peak <= 0:
        return None
    voiced = np.flatnonzero(energy > SPEECH_REGION_RELATIVE_FLOOR * peak)
    if voiced.size == 0:
        return None
    return float((voiced[-1] - voiced[0] + 1) * frame) / float(sample_rate)


def rate_bounds(low: float, high: float, speed_multiplier: float) -> tuple[float, float]:
    """The band scales with the requested speed.

    A rendering asked to run at 0.8× should be slower than natural speech by that
    factor; judging it against the unscaled band would reject it for obeying.
    """

    return low * speed_multiplier, high * speed_multiplier


@dataclass(frozen=True, slots=True)
class CellOutcome:
    """What one design cell produced: at most one row, and everything discarded."""

    row: dict | None
    rejections: list[dict]
    failure: dict | None
    message: str


def render_cell(
    generator,
    *,
    sentence: dict,
    voice: VoicePrompt,
    bucket: str,
    band: tuple[float, float],
    max_attempts: int,
    rejected_dir: Path,
    manifest_dir: Path,
) -> CellOutcome:
    """Render once, and accept it only if the rate is plausible.

    Retrying is measured to be worthless here: two renderings of one cell come back
    byte-identical, so `max_attempts` above 1 buys nothing but time. A cell that
    fails the band fails it every time, and the fix is a different prompt voice, not
    another attempt. The loop is kept because the contract still declares a maximum
    and a future non-deterministic generator would need it.

    Repair is not an option either: an exemplar edited into range is no longer a
    rendering, and every learner scored against it would be scored against an edit.
    """

    label = f"{sentence['sentence_id']}/{voice.prompt_id}/{bucket}"
    syllables = int(sentence["syllable_count"])
    low, high = rate_bounds(band[0], band[1], SPEED_BUCKETS[bucket])
    attempts: list[dict] = []

    for attempt in range(1, max_attempts + 1):
        try:
            exemplar, path = generator.synthesise(
                prompt_id=sentence["sentence_id"],
                text=sentence["normalized_text"],
                voice=voice,
                speed_bucket=bucket,
            )
        except ReferenceGeneratorError as error:
            return CellOutcome(
                row=None,
                rejections=attempts,
                failure={"cell": label, "error": str(error), "attempts": attempt},
                message=f"FAILED {label}: {error}",
            )

        # Measured on the spoken region, because that is what the band was measured
        # on. Falling back to the whole file would silently compare two different
        # quantities, so the fallback is recorded rather than hidden.
        spoken = speech_region_seconds(path)
        measured_on = "speech_region" if spoken else "whole_file"
        seconds = spoken or exemplar.duration_seconds
        rate = syllables / seconds
        if low <= rate <= high:
            row = row_from_exemplar(
                exemplar,
                audio_relative_path=str(path.relative_to(manifest_dir)),
            )
            row["speech_rate_syllables_per_second"] = round(rate, 4)
            row["speech_region_seconds"] = round(seconds, 4)
            row["speech_rate_measured_on"] = measured_on
            return CellOutcome(
                row=row,
                rejections=attempts,
                failure=None,
                message=(
                    f"ok {label} {exemplar.duration_seconds:.2f}s {rate:.2f}syl/s"
                    + (f" (attempt {attempt})" if attempt > 1 else "")
                ),
            )

        # Kept, not deleted: the rejected rendering is the evidence, and moving it
        # also frees the content-addressed path the retry needs.
        rejected_dir.mkdir(parents=True, exist_ok=True)
        path.replace(rejected_dir / f"{path.stem}.attempt{attempt}.wav")
        attempts.append(
            {
                "cell": label,
                "attempt": attempt,
                "duration_seconds": exemplar.duration_seconds,
                "speech_region_seconds": round(seconds, 4),
                "speech_rate_measured_on": measured_on,
                "speech_rate_syllables_per_second": round(rate, 4),
                "band": [round(low, 4), round(high, 4)],
                "verdict": "too_slow_for_the_band"
                if rate < low
                else "too_fast_for_the_band",
            }
        )

    last = attempts[-1]["speech_rate_syllables_per_second"]
    return CellOutcome(
        row=None,
        rejections=attempts,
        failure={
            "cell": label,
            "error": "no rendering fell inside the measured speech-rate band",
            "attempts": max_attempts,
            "last_speech_rate_syllables_per_second": last,
            "band": [round(low, 4), round(high, 4)],
        },
        message=(
            f"REJECTED {label} after {max_attempts} attempts, last {last} syl/s "
            f"outside {low:.2f}-{high:.2f}"
        ),
    )


def _load_yaml(path: Path) -> dict:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _gate_r00_card(config: dict, root: Path) -> list[dict]:
    """Both the card's own status and the validator's verdict must agree.

    Checking only the card would pass one that was never validated; checking only
    the report would pass a stale verdict for a card that has since changed.
    """

    spec = config["canonical_sources"]["r00_card"]
    card = json.loads((root / spec["path"]).read_text(encoding="utf-8"))
    if card.get("status") != spec["required_card_status"]:
        raise GenerationError("r00 card status differs from the contract")
    report_path = root / spec["validation_report_path"]
    if not report_path.is_file():
        raise GenerationError("the r00 validation report is absent")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != spec["required_validation_status"]:
        raise GenerationError("the r00 validation report is not valid")
    if report.get("card_id") != card.get("card_id"):
        raise GenerationError("the r00 validation report is for another card")
    sentences = card.get("sentences") or []
    if len(sentences) != spec["required_sentence_count"]:
        raise GenerationError("r00 sentence count differs from the contract")
    return sentences


def _gate_attestation(config: dict, root: Path) -> None:
    spec = config["canonical_sources"]["cosyvoice_attestation"]
    path = root / spec["path"]
    if not path.is_file():
        raise GenerationError("the CosyVoice attestation is absent")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != spec["sha256"]:
        raise GenerationError("the CosyVoice attestation digest differs")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("status") != spec["required_status"]:
        raise GenerationError("the CosyVoice attestation is not approved")


def _gate_prompt_voices(config: dict, root: Path) -> list[VoicePrompt]:
    voices: list[VoicePrompt] = []
    for entry in config["prompt_voices"]:
        voices.append(
            VoicePrompt(
                prompt_id=entry["prompt_voice_id"],
                audio_path=root / entry["audio_path"],
                license_spdx=entry["license_spdx"],
                rights_basis=entry["rights_basis"],
                speaker_axis=entry["speaker_axis"],
                prompt_text=entry["prompt_text"],
            )
        )
    if not voices:
        raise GenerationError("the contract declares no prompt voice")
    declared = config["known_limitations"]["distinct_prompt_voices"]
    if len(voices) != declared:
        raise GenerationError(
            "prompt voice count differs from the declared limitation"
        )
    return voices


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--limit", type=int, default=None, help="sentences to render")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="cells to render concurrently, each in its own worker process",
    )
    args = parser.parse_args()
    if args.workers < 1:
        raise SystemExit("--workers must be at least 1")

    root = Path(__file__).resolve().parents[1]
    config = _load_yaml(root / args.config)

    sentences = _gate_r00_card(config, root)
    _gate_attestation(config, root)
    voices = _gate_prompt_voices(config, root)
    band_low, band_high = _gate_rate_band(config, root)
    max_attempts = int(config["candidate_design"]["plausibility_gate"]["max_attempts"])
    buckets = config["candidate_design"]["diversity_preservation"][
        "required_speed_buckets"
    ]

    outputs = config["outputs"]
    for key in ("selection_id_prefix", "selection_rule"):
        # Named by the contract, not built from the experiment id: the selection rule
        # is a claim about how the bank was assembled, and a script that invents one
        # would put a claim in the manifest that no contract made.
        if key not in outputs:
            raise GenerationError(f"the contract does not declare outputs.{key}")
    audio_dir = root / outputs["audio_dir"]
    manifest_path = root / outputs["bank_manifest"]["path"]
    receipt_path = root / outputs["receipt"]["path"]

    if args.workers > 1:
        os.environ.setdefault("OMP_NUM_THREADS", str(THREADS_PER_WORKER))
        os.environ.setdefault("MKL_NUM_THREADS", str(THREADS_PER_WORKER))

    if args.limit is not None:
        sentences = sentences[: args.limit]
    cells = [
        (sentence, voice, bucket)
        for sentence in sentences
        for voice in voices
        for bucket in buckets
    ]
    print(
        f"sentences={len(sentences)} voices={len(voices)} "
        f"buckets={len(buckets)} cells={len(cells)}",
        flush=True,
    )
    if args.dry_run:
        return 0

    generator = CosyVoiceReferenceGenerator(
        repository_root=root,
        model_dir=root / MODEL_DIR,
        output_dir=audio_dir,
        disclosure=config["learner_facing"]["synthetic_disclosure_ko"],
        gpu_allowed=config["runtime"]["actual_gpu_access_allowed"],
    )

    rows: list[dict] = []
    failures: list[dict] = []
    rejections: list[dict] = []
    report = threading.Lock()
    done = 0
    rejected_dir = audio_dir / REJECTED_DIRECTORY_NAME

    def render(numbered: tuple[int, tuple]) -> None:
        nonlocal done
        _, (sentence, voice, bucket) = numbered
        outcome = render_cell(
            generator,
            sentence=sentence,
            voice=voice,
            bucket=bucket,
            band=(band_low, band_high),
            max_attempts=max_attempts,
            rejected_dir=rejected_dir,
            manifest_dir=manifest_path.parent,
        )
        with report:
            done += 1
            rejections.extend(outcome.rejections)
            if outcome.row is not None:
                rows.append(outcome.row)
            if outcome.failure is not None:
                failures.append(outcome.failure)
            print(f"[{done}/{len(cells)}] {outcome.message}", flush=True)

    numbered = list(enumerate(cells, start=1))
    if args.workers == 1:
        for item in numbered:
            render(item)
    else:
        # Threads, not processes: each cell already forks its own interpreter and the
        # thread only waits on it.
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(render, numbered))

    # Concurrency must not reorder the bank, or two runs of the same contract would
    # produce manifests that differ only by scheduling.
    rows.sort(key=lambda item: item["exemplar_id"])
    failures.sort(key=lambda item: item["cell"])
    rejections.sort(key=lambda item: (item["cell"], item["attempt"]))

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "selection_id": f"{outputs['selection_id_prefix']}-{len(rows):04d}",
        "selection_rule": outputs["selection_rule"],
        "diversity_axes": config["candidate_design"]["diversity_preservation"][
            "dimensions"
        ],
        "exemplars": rows,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    receipt = {
        "schema_version": "voice_coach.r01_generation_receipt.v2",
        "config_path": str(args.config),
        "config_sha256": hashlib.sha256(
            (root / args.config).read_bytes()
        ).hexdigest(),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "cells_planned": len(cells),
        "cells_generated": len(rows),
        "cells_failed": len(failures),
        "concurrent_workers": args.workers,
        "distinct_prompt_voices": len(voices),
        "failures": failures,
        "plausibility_gate": {
            "band_syllables_per_second": [round(band_low, 4), round(band_high, 4)],
            "band_scales_with_speed_bucket": True,
            "max_attempts_per_cell": max_attempts,
            "renderings_rejected": len(rejections),
            "rejected_audio_kept_under": REJECTED_DIRECTORY_NAME,
            "rejections": rejections,
        },
        "manifest_sha256": sha256_file(manifest_path),
        "human_candidates": 0,
        "r04_human_versus_tts_comparison_available": False,
        "r02_hard_gate_passed": False,
        "publication_allowed": False,
    }
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"manifest: {manifest_path}", flush=True)
    print(f"generated {len(rows)}/{len(cells)} cells", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
