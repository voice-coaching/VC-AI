#!/usr/bin/env python3
"""Download the entire first-feasible AI Hub 538 source prefix under v5."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from voice_coach.visual_feedback.adapters.aihub538_admitted_archive_binding import (  # noqa: E402
    assert_aihub538_descriptor_identity,
    capture_aihub538_descriptor_identity,
)
from voice_coach.visual_feedback.services.aihub538_label_capacity_audit import (  # noqa: E402
    Aihub538LabelCapacityPipelineError,
    load_aihub538_label_selection_authorization_receipt_descriptor,
)
from voice_coach.visual_feedback.adapters.aihub538_training_archive import (  # noqa: E402
    Aihub538ArchiveError,
    verify_aihub538_nonlocked_shard_download,
)
from voice_coach.visual_feedback.models.aihub538_nonlocked_shards import (  # noqa: E402
    AIHUB538_TS175_TL175_SHARD,
    AIHUB538_TS176_TL176_SHARD,
)
from voice_coach.visual_feedback.models.aihub538_label_selection_authorization import (  # noqa: E402
    require_aihub538_frozen_source_authorization_receipt,
)


_PAIR_DOWNLOADS = (
    (
        "ts175_tl175",
        "TL175",
        "54726",
        "TS175",
        "54945",
        AIHUB538_TS175_TL175_SHARD,
    ),
    (
        "ts176_tl176",
        "TL176",
        "54727",
        "TS176",
        "54946",
        AIHUB538_TS176_TL176_SHARD,
    ),
)


def _descriptor(value: str) -> int:
    try:
        descriptor = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("descriptor must be an integer >= 3") from None
    if descriptor < 3:
        raise argparse.ArgumentTypeError("descriptor must be an integer >= 3")
    return descriptor


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--authorization-receipt-fd",
        type=_descriptor,
        required=True,
        help="caller-owned read-only 0400 v5 authorization descriptor",
    )
    parser.add_argument(
        "--ts175-destination",
        type=Path,
        required=True,
        help="private destination for exact pair 54726,54945",
    )
    parser.add_argument(
        "--ts176-destination",
        type=Path,
        required=True,
        help="private destination for exact pair 54727,54946",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _arguments(argv)
    try:
        before = capture_aihub538_descriptor_identity(
            arguments.authorization_receipt_fd,
            require_mode=0o400,
        )
        receipt = (
            load_aihub538_label_selection_authorization_receipt_descriptor(
                arguments.authorization_receipt_fd
            )
        )
        require_aihub538_frozen_source_authorization_receipt(receipt)
        document = receipt.to_json_object()
        expected_label_roles = [item[1] for item in _PAIR_DOWNLOADS]
        expected_label_keys = [item[2] for item in _PAIR_DOWNLOADS]
        expected_source_roles = [item[3] for item in _PAIR_DOWNLOADS]
        expected_source_keys = [item[4] for item in _PAIR_DOWNLOADS]
        if (
            not receipt.is_feasible
            or document["evaluated_label_prefix"] != expected_label_roles
            or document["evaluated_label_file_key_prefix"]
            != expected_label_keys
            or document["authorized_source_role_prefix"]
            != expected_source_roles
            or document["authorized_source_file_key_prefix"]
            != expected_source_keys
            or [item.label_role_component for item in receipt.prefix_lineage]
            != expected_label_roles
            or [item.label_file_key for item in receipt.prefix_lineage]
            != expected_label_keys
            or document["source_download_authorized"] is not True
        ):
            raise ValueError("aihub538_source_prefix_not_authorized")
        destinations = (
            arguments.ts175_destination.absolute(),
            arguments.ts176_destination.absolute(),
        )
        if destinations[0] == destinations[1]:
            raise ValueError("aihub538_source_prefix_destination_collision")
        completed_pairs: list[dict[str, object]] = []
        for pair, destination in zip(
            _PAIR_DOWNLOADS,
            destinations,
            strict=True,
        ):
            (
                shard_id,
                _label_role,
                label_key,
                _source_role,
                source_key,
                shard_spec,
            ) = pair
            # Close the parse-to-launch TOCTOU window before every external
            # downloader invocation, not only after it returns.
            assert_aihub538_descriptor_identity(
                arguments.authorization_receipt_fd,
                before,
            )
            already_completed = _verify_completed_pair_if_present(
                destination=destination,
                shard_spec=shard_spec,
            )
            if not already_completed:
                command = [
                    sys.executable,
                    str(REPOSITORY_ROOT / "scripts/download_aihub_secure.py"),
                    "538",
                    f"{label_key},{source_key}",
                    str(destination),
                ]
                environment = os.environ.copy()
                environment[
                    "VOICE_COACH_AIHUB538_SOURCE_AUTHORIZATION_FD"
                ] = str(arguments.authorization_receipt_fd)
                completed = subprocess.run(
                    command,
                    check=False,
                    env=environment,
                    pass_fds=(arguments.authorization_receipt_fd,),
                )
            else:
                completed = subprocess.CompletedProcess([], 0)
            assert_aihub538_descriptor_identity(
                arguments.authorization_receipt_fd,
                before,
            )
            if completed.returncode != 0:
                return completed.returncode
            if not _verify_completed_pair_if_present(
                destination=destination,
                shard_spec=shard_spec,
            ):
                raise ValueError("aihub538_completed_pair_missing")
            assert_aihub538_descriptor_identity(
                arguments.authorization_receipt_fd,
                before,
            )
            completed_pairs.append(
                {
                    "shard_id": shard_id,
                    "file_keys": [label_key, source_key],
                    "existing_completed_download_reused": already_completed,
                }
            )
        assert_aihub538_descriptor_identity(
            arguments.authorization_receipt_fd,
            before,
        )
        print(
            json.dumps(
                {
                    "status": "completed_authorized_prefix_download",
                    "dataset_id": 538,
                    "completed_pairs": completed_pairs,
                    "first_feasible_prefix_complete": True,
                    "label_selection_authorization_receipt_sha256": (
                        receipt.receipt_sha256
                    ),
                    "authorization_descriptor_stable": True,
                    "locked_partition_accessed": False,
                    "promotion_authorized": False,
                },
                sort_keys=True,
            )
        )
        return 0
    except (
        Aihub538LabelCapacityPipelineError,
        OSError,
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
                        if str(error).startswith("aihub538_")
                        else "aihub538_authorized_source_download_failed",
                    ),
                    "source_download_authorized": False,
                    "locked_partition_accessed": False,
                    "promotion_authorized": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 65


def _verify_completed_pair_if_present(*, destination: Path, shard_spec: object) -> bool:
    """Skip only an exact completed pair; retain native partial-file resume."""

    absolute = destination.absolute()
    archive = absolute / "download.tar"
    receipt = absolute / "download.receipt.json"
    archive_exists = archive.exists()
    receipt_exists = receipt.exists()
    if not archive_exists and not receipt_exists:
        if absolute.exists():
            metadata = absolute.stat(follow_symlinks=False)
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(
                metadata.st_mode
            ) != 0o700:
                raise ValueError("aihub538_source_destination_invalid")
        return False
    if not archive_exists or not receipt_exists:
        raise ValueError("aihub538_completed_pair_incomplete")
    if not absolute.is_dir() or stat.S_IMODE(
        absolute.stat(follow_symlinks=False).st_mode
    ) != 0o700:
        raise ValueError("aihub538_source_destination_invalid")
    archive_descriptor = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW)
    receipt_descriptor = os.open(receipt, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        archive_metadata = os.fstat(archive_descriptor)
        receipt_metadata = os.fstat(receipt_descriptor)
        if (
            stat.S_IMODE(archive_metadata.st_mode) != 0o600
            or stat.S_IMODE(receipt_metadata.st_mode) != 0o400
        ):
            raise ValueError("aihub538_completed_pair_mode_invalid")
        verify_aihub538_nonlocked_shard_download(
            archive_descriptor=archive_descriptor,
            receipt_descriptor=receipt_descriptor,
            shard_spec=shard_spec,
        )
    except Aihub538ArchiveError as error:
        raise ValueError(error.reason_code) from None
    finally:
        os.close(receipt_descriptor)
        os.close(archive_descriptor)
    return True


if __name__ == "__main__":
    raise SystemExit(main())
