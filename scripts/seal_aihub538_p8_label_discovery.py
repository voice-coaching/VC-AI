#!/usr/bin/env python3
"""Persist the read-zero all-32 P8 label-discovery seal in existing state."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from voice_coach.visual_feedback.adapters.p8_seal_store import (  # noqa: E402
    P8SealStateError,
    P8SealStateStore,
)
from voice_coach.visual_feedback.adapters.p8_locked_label_download import (  # noqa: E402
    open_p8_locked_label_state_directory,
)
from voice_coach.visual_feedback.models.p8_locked_cohort import (  # noqa: E402
    P8LockedCohortContractError,
    P8LockedCohortDiscoverySeal,
)
from voice_coach.visual_feedback.models.p8_locked_label_download import (  # noqa: E402
    P8LockedLabelDownloadError,
    p8_locked_reference_commitments_root_sha256,
)


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--seal-id", required=True)
    parser.add_argument("--protocol-revision", required=True)
    parser.add_argument("--sealed-at-utc", default=None)
    return parser.parse_args(argv)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main(argv: list[str] | None = None) -> int:
    arguments = _arguments(argv)
    try:
        store = P8SealStateStore(arguments.state_dir.absolute())
        discovery_seal = (
            store.recover_incomplete_locked_cohort_discovery_state(
                expected_seal_id=arguments.seal_id,
                expected_protocol_revision=arguments.protocol_revision,
            )
        )
        recovered_incomplete_state = discovery_seal is not None
        if discovery_seal is None:
            actual_source_seal = store.load_actual_source_read_zero_seal()
            state_descriptor = open_p8_locked_label_state_directory(
                arguments.state_dir.absolute()
            )
            try:
                state_metadata = os.fstat(state_descriptor)
            finally:
                os.close(state_descriptor)
            discovery_seal = P8LockedCohortDiscoverySeal.issue(
                seal_id=arguments.seal_id,
                protocol_revision=arguments.protocol_revision,
                reference_identity_set_commitments_sha256=(
                    p8_locked_reference_commitments_root_sha256(
                        actual_source_seal
                    )
                ),
                state_directory_device=state_metadata.st_dev,
                state_directory_inode=state_metadata.st_ino,
                sealed_at_utc=arguments.sealed_at_utc or _utc_now(),
            )
            store.create_locked_cohort_discovery_seal(discovery_seal)
        reloaded = store.load_locked_cohort_discovery_seal()
        state_root = store.load_locked_label_state_root_seal()
        if reloaded != discovery_seal:
            raise P8SealStateError(
                "p8_locked_cohort_discovery_seal_reload_mismatch"
            )
        print(
            json.dumps(
                {
                    "status": "p8_locked_cohort_discovery_sealed",
                    "dataset_id": 538,
                    "validation_pair_count": 32,
                    "locked_candidate_access_count": 0,
                    "sealed_before_any_locked_candidate_access": True,
                    "discovery_seal_sha256": discovery_seal.seal_sha256,
                    "state_root_seal_sha256": (
                        state_root.state_root_seal_sha256
                    ),
                    "incomplete_state_recovered": recovered_incomplete_state,
                    "label_download_authorized": False,
                    "label_census_access_authorized": False,
                    "source_download_authorized": False,
                    "source_media_open_authorized": False,
                    "promotion_authorized": False,
                    "aggregate_only": True,
                },
                sort_keys=True,
            )
        )
        return 0
    except (
        OSError,
        P8LockedCohortContractError,
        P8LockedLabelDownloadError,
        P8SealStateError,
        TypeError,
        ValueError,
    ) as error:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "reason_code": getattr(
                        error,
                        "reason_code",
                        str(error)
                        if str(error).startswith("p8_")
                        else "p8_locked_cohort_discovery_seal_failed",
                    ),
                    "locked_candidate_access_count": 0,
                    "label_download_authorized": False,
                    "source_download_authorized": False,
                    "source_media_open_authorized": False,
                    "promotion_authorized": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 65


if __name__ == "__main__":
    raise SystemExit(main())
