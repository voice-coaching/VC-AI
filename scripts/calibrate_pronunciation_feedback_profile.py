#!/usr/bin/env python3
"""Freeze a Track E threshold set from a calibration split.

**The rule is fixed here, before the split is read, and this file is pinned by
the artifact it writes.**  That is the whole of what ``engineering_provisional``
claims: not that the thresholds are good, but that they were not chosen after
seeing which choice flattered the result.

The rule
--------
Each *matched* item is a recording paired with the sentence it actually reads.
Each *mismatched* item is the same recording paired with a different sentence
from the same card, chosen by cycling the sentence order — so the mismatched
distribution is built by a rule too, not by picking confusable pairs.

* ``min_content_score``, ``min_alignment_confidence``, ``min_s1_confidence`` and
  ``min_s2_confidence`` are the **5th percentile of the matched split**, floored
  to two decimals.  A threshold set this way rejects about one in twenty
  recordings that *were* the right sentence, which is the cost of never coaching
  someone on a sentence they did not read.
* ``min_content_score`` is additionally required to sit **above the 95th
  percentile of the mismatched split**.  If it does not, the two conditions are
  not separable by this measurement and the script refuses to write an
  artifact rather than freezing a threshold that cannot do its job.
* ``min_issue_confidence`` and ``min_issue_severity`` come from a stated
  decision rule instead of the split: coach only where the error hypothesis
  holds at least ``ODDS_TO_COACH`` of the two-way posterior mass.  Both
  quantities are monotone functions of the same log-odds margin, so one margin
  fixes both and they cannot drift apart.
* ``required_branches`` are the branches observed on **every** matched item.  A
  branch that was ever missing cannot be required, or the gate would fail closed
  on recordings the calibration split says are normal.

What this artifact does not claim
---------------------------------
The split is synthesised read speech, not learner speech.  It can calibrate the
question "is this recording this sentence?" because that question does not
depend on who is speaking.  It cannot calibrate what counts as a *pronunciation
error worth coaching for a learner* — no learner recordings and no expert labels
enter here.  The cohort field says so, and the gate's claim scope stays
``engineering_provisional``.

Usage:
    calibrate_pronunciation_feedback_profile.py --bank BANK.json --out PROFILE.json
        [--profile NAME] [--s2-sample 60] [--limit N]
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from voice_coach.pronunciation_feedback.adapters.ctc_acoustic_backend import (  # noqa: E402
    CtcAcousticBackend,
)
from voice_coach.pronunciation_feedback.adapters.ctc_content_alignment import (  # noqa: E402
    CtcContentAlignment,
    jamo_similarity,
)
from voice_coach.pronunciation_feedback.adapters.gop_s1_scorer import (  # noqa: E402
    GopS1Scorer,
)
from voice_coach.pronunciation_feedback.adapters.qwen_audio_auditor import (  # noqa: E402
    AUDIT_AGREEMENT_APPROVE,
    QwenAudioAuditor,
)
from voice_coach.pronunciation_feedback.adapters.syllables import (  # noqa: E402
    decompose_syllables,
    syllable_map_sha256,
)
from voice_coach.pronunciation_feedback.domain import MetricState, PromptSpec  # noqa: E402
from voice_coach.pronunciation_feedback.errors import (  # noqa: E402
    PronunciationFeedbackError,
)

DEFAULT_CARD_PATH = Path("data/reference/r00/r00_sentence_card_v1.json")
DEFAULT_PROFILE = "ctc_gop_qwen_audit_v1"
#: Fraction of the matched split a frozen threshold is allowed to reject.
MATCHED_REJECTION_QUANTILE = 0.05
#: The mismatched split must sit entirely below the content threshold except for
#: this tail, or the measurement does not separate the two conditions.
MISMATCH_HEADROOM_QUANTILE = 0.95
#: Coach only when the error hypothesis holds at least these odds against the
#: expected pronunciation on the frames that carry the syllable's identity.
ODDS_TO_COACH = 7.0


class CalibrationError(RuntimeError):
    """Raised when the split cannot produce a usable threshold set."""


def _floor2(value: float) -> float:
    return math.floor(value * 100.0) / 100.0


def _quantile(values: list[float], fraction: float) -> float:
    """Nearest-rank quantile — no interpolation, so the value is an observed one."""

    if not values:
        raise CalibrationError("cannot take a quantile of an empty split")
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, int(math.ceil(fraction * len(ordered))) - 1))
    return ordered[rank]


def freeze_thresholds(
    *,
    matched: dict[str, list[float]],
    mismatched: dict[str, list[float]],
    branch_states: list[set[str]],
) -> dict:
    """Apply the rule above to a finished split.

    Kept separate from the run that produces the split so the rule can be
    checked without a model: what this artifact claims is the rule, and a rule
    only testable by running two neural networks is not really being checked.
    """

    if not matched.get("content"):
        raise CalibrationError("no calibration item survived the pipeline")
    if not matched.get("s2"):
        raise CalibrationError("the auditor scored no item; s2 cannot be frozen")

    min_content = _floor2(_quantile(matched["content"], MATCHED_REJECTION_QUANTILE))
    mismatch_ceiling = _quantile(mismatched["content"], MISMATCH_HEADROOM_QUANTILE)
    if min_content <= mismatch_ceiling:
        raise CalibrationError(
            "content score does not separate the two conditions: matched "
            f"p{int(MATCHED_REJECTION_QUANTILE * 100)}={min_content:.3f} is not "
            f"above mismatched p{int(MISMATCH_HEADROOM_QUANTILE * 100)}="
            f"{mismatch_ceiling:.3f}"
        )

    required = sorted(set.intersection(*branch_states)) if branch_states else []
    if not required:
        raise CalibrationError(
            "no S1 branch was observed on every matched item; nothing can be required"
        )

    margin = math.log(ODDS_TO_COACH)
    return {
        "min_content_score": min_content,
        "min_alignment_confidence": _floor2(
            _quantile(matched["alignment"], MATCHED_REJECTION_QUANTILE)
        ),
        "min_s1_confidence": _floor2(
            _quantile(matched["s1"], MATCHED_REJECTION_QUANTILE)
        ),
        "min_s2_confidence": _floor2(
            _quantile(matched["s2"], MATCHED_REJECTION_QUANTILE)
        ),
        "min_issue_confidence": _floor2(1.0 / (1.0 + math.exp(-margin))),
        "min_issue_severity": _floor2(1.0 - math.exp(-margin)),
        "required_branches": required,
        "mismatched_content_ceiling": mismatch_ceiling,
    }


def prompt_spec_for(sentence: dict) -> PromptSpec:
    slots = decompose_syllables(sentence["normalized_text"])
    return PromptSpec(
        prompt_id=sentence["sentence_id"],
        prompt_version=f"calibration/{sentence['normalized_text_sha256'][:12]}",
        normalized_text=sentence["normalized_text"],
        normalized_text_sha256=sentence["normalized_text_sha256"],
        syllables=slots,
        syllable_map_sha256=syllable_map_sha256(slots),
        expected_phones=None,
        expected_phone_state=MetricState.BACKEND_UNAVAILABLE,
    )


def manifest_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", type=Path, required=True)
    parser.add_argument("--card", type=Path, default=DEFAULT_CARD_PATH)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--dataset-id", default=None)
    parser.add_argument(
        "--cohort",
        default="tts_rendered_r00_read_sentences_no_learner_speech",
        help="what the split actually is; it is written into every feedback notice",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--s2-sample",
        type=int,
        default=60,
        help="how many recordings the audio LLM auditor scores (it is the slow one)",
    )
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    bank_path = args.bank if args.bank.is_absolute() else REPO_ROOT / args.bank
    card_path = args.card if args.card.is_absolute() else REPO_ROOT / args.card
    bank = json.loads(bank_path.read_text(encoding="utf-8"))
    card = json.loads(card_path.read_text(encoding="utf-8"))
    sentences = list(card["sentences"])
    by_id = {item["sentence_id"]: item for item in sentences}
    order = [item["sentence_id"] for item in sentences]
    #: The mismatched partner is the next sentence in card order, so which pairs
    #: get compared is a property of the card, not of this run.
    partner = {name: order[(index + 1) % len(order)] for index, name in enumerate(order)}

    backend = CtcAcousticBackend(repository_root=REPO_ROOT, device=args.device)
    content = CtcContentAlignment(backend, repository_root=REPO_ROOT)
    scorer = GopS1Scorer(
        backend=backend, content_alignment=content, repository_root=REPO_ROOT
    )
    auditor = QwenAudioAuditor(repository_root=REPO_ROOT, device=args.device)

    exemplars = list(bank["exemplars"])
    if args.limit is not None:
        exemplars = exemplars[: args.limit]
    # Spread the auditor's budget over sentences rather than taking a prefix,
    # which would land it all on the first few sentences of the card.
    stride = max(1, len(exemplars) // max(1, args.s2_sample))
    audited = {index for index in range(0, len(exemplars), stride)}

    matched: dict[str, list[float]] = {
        "content": [], "alignment": [], "s1": [], "s2": []
    }
    mismatched: dict[str, list[float]] = {"content": [], "s2": []}
    branch_states: list[set[str]] = []
    rows: list[dict] = []
    skipped: list[dict] = []

    for index, exemplar in enumerate(exemplars):
        prompt_id = exemplar["prompt_id"]
        audio = bank_path.parent / exemplar["audio_relative_path"]
        spec = prompt_spec_for(by_id[prompt_id])
        other = prompt_spec_for(by_id[partner[prompt_id]])
        try:
            observation = content.analyze(audio_path=audio, prompt_spec=spec)
            bundle = scorer.score(
                audio_path=audio, prompt_spec=spec, alignment=observation
            )
            wrong = content.analyze(audio_path=audio, prompt_spec=other)
        except PronunciationFeedbackError as error:
            skipped.append({"prompt_id": prompt_id, "reason": error.code.value})
            print(f"[{index}] skip {prompt_id}: {error.code.value}", flush=True)
            continue

        matched["content"].append(float(observation.content_score))
        matched["alignment"].append(float(observation.alignment_confidence))
        if bundle.overall_confidence is not None:
            matched["s1"].append(float(bundle.overall_confidence))
        mismatched["content"].append(float(wrong.content_score))
        branch_states.append(set(bundle.observed_names()))

        row = {
            "prompt_id": prompt_id,
            "matched_content": round(float(observation.content_score), 4),
            "mismatched_content": round(float(wrong.content_score), 4),
            "alignment_confidence": round(float(observation.alignment_confidence), 4),
            "s1_confidence": (
                None
                if bundle.overall_confidence is None
                else round(float(bundle.overall_confidence), 4)
            ),
            "issue_count": len(bundle.issues),
        }

        if index in audited:
            reply = auditor.transcribe(audio)
            row["s2_confidence"] = round(reply.mean_token_probability, 4)
            row["s2_matched_agreement"] = round(
                jamo_similarity(reply.text, spec.normalized_text), 4
            )
            row["s2_mismatched_agreement"] = round(
                jamo_similarity(reply.text, other.normalized_text), 4
            )
            matched["s2"].append(reply.mean_token_probability)
            mismatched["s2"].append(row["s2_mismatched_agreement"])
        rows.append(row)
        print(
            f"[{index + 1}/{len(exemplars)}] {prompt_id} "
            f"matched={row['matched_content']:.3f} "
            f"mismatched={row['mismatched_content']:.3f}"
            + (f" s2={row.get('s2_confidence')}" if index in audited else ""),
            flush=True,
        )

    frozen = freeze_thresholds(
        matched=matched, mismatched=mismatched, branch_states=branch_states
    )
    mismatch_ceiling = frozen["mismatched_content_ceiling"]
    required = frozen["required_branches"]

    artifact = {
        "schema_version": "voice_coach.pronunciation_feedback_thresholds.v1",
        "threshold_set_id": f"{args.profile}-{datetime.now(timezone.utc):%Y%m%d}",
        "analysis_profile": args.profile,
        "calibrated": True,
        "cohort": args.cohort,
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset_id": args.dataset_id or bank.get("selection_id") or bank_path.stem,
        "manifest_sha256": manifest_digest(bank_path),
        "min_content_score": frozen["min_content_score"],
        "min_alignment_confidence": frozen["min_alignment_confidence"],
        "min_s1_confidence": frozen["min_s1_confidence"],
        "min_s2_confidence": frozen["min_s2_confidence"],
        "min_issue_confidence": frozen["min_issue_confidence"],
        "min_issue_severity": frozen["min_issue_severity"],
        "required_branches": required,
        "freezing_rule": {
            "source": "scripts/calibrate_pronunciation_feedback_profile.py",
            "source_sha256": hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest(),
            "matched_rejection_quantile": MATCHED_REJECTION_QUANTILE,
            "mismatch_headroom_quantile": MISMATCH_HEADROOM_QUANTILE,
            "odds_to_coach": ODDS_TO_COACH,
            "audit_agreement_approve": AUDIT_AGREEMENT_APPROVE,
        },
        "split": {
            "matched_items": len(matched["content"]),
            "auditor_items": len(matched["s2"]),
            "skipped_items": skipped,
            "mismatch_partner_rule": "next sentence in card order, cyclically",
            "mismatched_content_p95": round(mismatch_ceiling, 4),
        },
        "claim_scope_note": (
            "engineering_provisional. 합성 낭독 음성으로 고정한 임계값이다. "
            "학습자 녹음과 전문가 라벨이 들어가지 않았으므로 "
            "'어떤 발음 오류가 코칭할 가치가 있는가'를 검증한 결과가 아니다."
        ),
        "rows": rows,
    }
    out = args.out if args.out.is_absolute() else REPO_ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print()
    print(f"profile           : {args.profile}")
    print(f"matched items     : {len(matched['content'])}")
    print(f"auditor items     : {len(matched['s2'])}")
    for name in (
        "min_content_score",
        "min_alignment_confidence",
        "min_s1_confidence",
        "min_s2_confidence",
        "min_issue_confidence",
        "min_issue_severity",
    ):
        print(f"{name:26s}: {artifact[name]}")
    print(f"required_branches         : {required}")
    print(f"mismatched content p95    : {mismatch_ceiling:.4f}")
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
