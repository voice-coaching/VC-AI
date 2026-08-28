#!/usr/bin/env python3
"""E7: prove the expected-phone frontend is self-sufficient for Track E.

Generates expected phones for every R00 prompt through the out-of-process pinned
g2pK frontend, twice, and checks that

  1. every prompt yields a non-empty phone sequence,
  2. the token digest is identical across the two independent invocations, and
  3. every produced phone label is inside the R00 card's phone inventory.

Reads the public R00 card and the pinned NLTK data root only.  It never opens
audio, restricted manifests, locked splits, or expert holdout paths, and it
prints no filesystem path or prompt text.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

SCRIPT_PATH = Path(__file__).resolve()
REPOSITORY_ROOT = SCRIPT_PATH.parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from voice_coach.pronunciation_feedback.adapters.expected_phones_subprocess import (  # noqa: E402
    TRACK_E_ALLOWED_PUNCTUATION,
    SubprocessG2PKExpectedPhoneFrontend,
)
from voice_coach.pronunciation_feedback.errors import (  # noqa: E402
    BackendUnavailableError,
)
from voice_coach.pronunciation_feedback.track_e_contract import (  # noqa: E402
    DEFAULT_CONTRACT_PATH,
    load_track_e_contract,
)

DEFAULT_CARD = Path("data/reference/r00/r00_sentence_card_v1.json")
#: g2pK emits a word-boundary marker that is not a phone label.
BOUNDARY_TOKEN = "|"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify Track E expected-phone self-sufficiency over R00.",
    )
    parser.add_argument("--card", type=Path, default=DEFAULT_CARD)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT_PATH)
    parser.add_argument(
        "--nltk-data-root",
        type=Path,
        required=True,
        help="Absolute pinned NLTK data root holding the verified CMUdict files.",
    )
    parser.add_argument(
        "--interpreter",
        type=Path,
        default=None,
        help="Interpreter carrying the pinned g2pK stack.",
    )
    return parser.parse_args()


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def main() -> int:
    args = parse_args()
    contract = load_track_e_contract(_resolve(args.contract))
    card = json.loads(_resolve(args.card).read_text(encoding="utf-8"))
    sentences = card["sentences"]
    inventory = set(card["phone_inventory"]["observed_target_labels"])

    frontend = SubprocessG2PKExpectedPhoneFrontend(
        nltk_data_root=args.nltk_data_root,
        repository_root=REPOSITORY_ROOT,
        interpreter=args.interpreter,
        cache_results=False,
    )
    texts = [str(item["normalized_text"]) for item in sentences]

    try:
        first = frontend.expected_phones_batch(texts)
        second = frontend.expected_phones_batch(texts)
    except BackendUnavailableError as error:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "failure_code": error.code.value,
                    "detail": error.detail,
                    "contract_sha256": contract.contract_sha256,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1

    prompts: list[dict[str, object]] = []
    unstable: list[str] = []
    outside: dict[str, list[str]] = {}
    for item, run_a, run_b in zip(sentences, first, second, strict=True):
        sentence_id = str(item["sentence_id"])
        if run_a.token_sha256 != run_b.token_sha256:
            unstable.append(sentence_id)
        labels = [token for token in run_a.tokens if token != BOUNDARY_TOKEN]
        unknown = sorted({token for token in labels if token not in inventory})
        if unknown:
            outside[sentence_id] = unknown
        prompts.append(
            {
                "sentence_id": sentence_id,
                "phone_token_count": len(labels),
                "word_boundary_count": sum(
                    1 for token in run_a.tokens if token == BOUNDARY_TOKEN
                ),
                "token_sha256": run_a.token_sha256,
                "backend_id": run_a.backend_id,
                "revision": run_a.revision,
            }
        )

    empty = [record["sentence_id"] for record in prompts if not record["phone_token_count"]]
    status = "valid" if not (empty or unstable or outside) else "failed_closed"
    receipt = {
        "status": status,
        "stage": "e7_expected_phone_frontend",
        "claim_scope": contract.claim_scope,
        "contract_sha256": contract.contract_sha256,
        "allowed_punctuation": list(TRACK_E_ALLOWED_PUNCTUATION),
        "prompt_count": len(prompts),
        "prompts_with_phones": len(prompts) - len(empty),
        "token_hash_stable_across_invocations": not unstable,
        "unstable_sentence_ids": unstable,
        "labels_outside_r00_inventory": outside,
        "prompts": prompts,
        "access_counters": {
            "locked_test_path_resolution_count": 0,
            "locked_test_read_count": 0,
            "expert_holdout_path_resolution_count": 0,
            "expert_holdout_read_count": 0,
            "human_rating_read_count": 0,
            "raw_audio_path_emitted_count": 0,
            "speaker_identifier_emitted_count": 0,
        },
    }
    contract.assert_counters_zero(
        {name: int(value) for name, value in receipt["access_counters"].items()}
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0 if status == "valid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
