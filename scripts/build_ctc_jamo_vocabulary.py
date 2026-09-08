#!/usr/bin/env python3
"""Build the Track E jamo CTC label set from the AI Hub target phone annotation.

The label space is a decision, so it is frozen into an artifact before any CTC
training rather than derived inside a training script.  Jamo were chosen over
syllables for three reasons that this script makes checkable:

* the manifest already carries target phone intervals labelled with
  compatibility jamo, so no restricted transcript text has to be opened;
* the expected-phone frontend (g2pK) emits the same compatibility jamo, so
  ``expected_phone_agreement`` compares one label space against itself; and
* the label set is small and closed, so there is no out-of-vocabulary case to
  paper over at inference time.

Reads the train split of the modeling manifest under exactly the guarded filters
the S105 contract uses (QC flags, reserved prompts, cohort scope). Opens no audio,
no dev/calibration/locked split, and writes only the vocabulary artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

SCRIPT_PATH = Path(__file__).resolve()
REPOSITORY_ROOT = SCRIPT_PATH.parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

import yaml  # noqa: E402

from voice_coach.pronunciation_feedback.ctc_alignment import (  # noqa: E402
    BLANK_TOKEN,
    WORD_DELIMITER_TOKEN,
    CtcVocabulary,
)
from voice_coach.pronunciation_feedback.track_e_contract import (  # noqa: E402
    DEFAULT_CONTRACT_PATH,
    load_track_e_contract,
)
from voice_coach.training.wavlm_adapter_training import (  # noqa: E402
    _is_sha256,
    _manifest_rows,
    load_reserved_prompt_hashes,
)

DEFAULT_S105_CONFIG = Path(
    "configs/experiments/wavlm_s105_hierarchy_v5_stream_cardinality.yaml"
)
DEFAULT_OUTPUT = Path("configs/pronunciation_feedback/ctc_jamo_vocabulary_v1.json")
ARTIFACT_SCHEMA_VERSION = "voice_coach.ctc_jamo_vocabulary.v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze the Track E jamo CTC label set from the train split.",
    )
    parser.add_argument("--s105-config", type=Path, default=DEFAULT_S105_CONFIG)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing artifact instead of failing closed.",
    )
    return parser.parse_args()


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    contract = load_track_e_contract(_resolve(args.contract))
    config = yaml.safe_load(_resolve(args.s105_config).read_text(encoding="utf-8"))
    data = config["data"]
    scope = data["primary_analysis_scope"]
    allowed_l1 = {str(value) for value in scope["l1"]}
    allowed_gender = {str(value) for value in scope["gender"]}
    manifest_path = _resolve(Path(data["modeling_manifest"]))
    manifest_sha256 = _sha256_file(manifest_path)
    if manifest_sha256 != data["modeling_manifest_sha256"]:
        print(
            json.dumps(
                {"status": "failed_closed", "error": "modeling manifest SHA differs"},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    reserved = load_reserved_prompt_hashes(
        _resolve(Path(data["prompt_challenge_reservation"]))
    )

    labels: dict[str, int] = {}
    utterances = 0
    for row in _manifest_rows(manifest_path, split="train"):
        if row.get("qc_flags") or row.get("prompt_sha256") in reserved:
            continue
        if (
            str(row.get("l1") or "") not in allowed_l1
            or str(row.get("gender") or "") not in allowed_gender
        ):
            continue
        if not _is_sha256(row.get("speaker_id")):
            continue
        intervals = row.get("target_phone_intervals")
        if not isinstance(intervals, list) or not intervals:
            continue
        utterances += 1
        for interval in intervals:
            label = interval.get("label")
            if not isinstance(label, str) or not label:
                continue
            labels[label] = labels.get(label, 0) + 1

    if not labels:
        print(
            json.dumps(
                {"status": "failed_closed", "error": "no target phone label found"},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1

    vocabulary = CtcVocabulary(
        tokens=(BLANK_TOKEN, WORD_DELIMITER_TOKEN, *sorted(labels))
    )
    document = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "label_unit": "korean_compatibility_jamo",
        "claim_scope": contract.claim_scope,
        "contract_sha256": contract.contract_sha256,
        "tokens": list(vocabulary.tokens),
        "digest": vocabulary.digest,
        "label_count": vocabulary.size,
        "source": {
            "modeling_manifest": data["modeling_manifest"],
            "modeling_manifest_sha256": manifest_sha256,
            "split": "train",
            "guarded_utterances": utterances,
            "cohort_l1": sorted(allowed_l1),
            "cohort_gender": sorted(allowed_gender),
            "excluded": [
                "qc_flagged",
                "reserved_prompt",
                "outside_cohort_scope",
                "zero_target_phone",
            ],
        },
        "label_frequencies": {label: labels[label] for label in sorted(labels)},
        "access_counters": {
            "audio_read_count": 0,
            "dev_split_read_count": 0,
            "calibration_split_read_count": 0,
            "locked_test_read_count": 0,
            "locked_test_path_resolution_count": 0,
            "expert_holdout_read_count": 0,
            "expert_holdout_path_resolution_count": 0,
            "human_rating_read_count": 0,
            "raw_audio_path_emitted_count": 0,
            "speaker_identifier_emitted_count": 0,
        },
    }
    contract.assert_counters_zero(
        {
            name: int(value)
            for name, value in document["access_counters"].items()
            if name in contract.zero_counters()
        }
    )

    output = _resolve(args.output)
    if output.exists() and not args.overwrite:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error": "vocabulary artifact already exists",
                    "digest": vocabulary.digest,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "frozen",
                "artifact": str(args.output),
                "label_count": vocabulary.size,
                "digest": vocabulary.digest,
                "guarded_utterances": utterances,
                "contract_sha256": contract.contract_sha256,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
