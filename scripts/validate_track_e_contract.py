#!/usr/bin/env python3
"""Read-only validation of the frozen Track E contract.

Prints the canonical contract hash that every Track E run receipt must cite.
Opens nothing but the contract artifact: no data, no model, no locked path.
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

from voice_coach.pronunciation_feedback.track_e_contract import (  # noqa: E402
    DEFAULT_CONTRACT_PATH,
    TrackEContractError,
    load_track_e_contract,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and hash the frozen Track E contract.",
    )
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT_PATH)
    parser.add_argument(
        "--expect-sha256",
        help="Fail unless the canonical contract hash equals this value.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = (
        args.contract
        if args.contract.is_absolute()
        else REPOSITORY_ROOT / args.contract
    )
    try:
        contract = load_track_e_contract(path, expected_sha256=args.expect_sha256)
    except TrackEContractError as error:
        print(
            json.dumps(
                {"status": "failed_closed", "error": str(error)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "status": "valid",
                "contract_id": contract.contract_id,
                "claim_scope": contract.claim_scope,
                "cohort": contract.cohort,
                "frozen_at_kst": contract.frozen_at_kst,
                "contract_sha256": contract.contract_sha256,
                "derivation_split": contract.derivation_split,
                "zero_counters": list(contract.zero_counters()),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
