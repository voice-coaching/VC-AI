#!/usr/bin/env python3
"""Download every exact pair in the first-feasible P6 remediation prefix."""

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
from voice_coach.visual_feedback.adapters.aihub538_p6_remediation_publish import (  # noqa: E402
    Aihub538P6RemediationPublishError,
    load_aihub538_p6_remediation_receipt_descriptor,
)
from voice_coach.visual_feedback.adapters.aihub538_training_archive import (  # noqa: E402
    Aihub538ArchiveError,
    verify_aihub538_nonlocked_shard_download,
)
from voice_coach.visual_feedback.models.aihub538_nonlocked_shards import (  # noqa: E402
    aihub538_nonlocked_shard_by_id,
)
from voice_coach.visual_feedback.models.aihub538_p6_remediation import (  # noqa: E402
    require_aihub538_p6_remediation_source_pair,
)


_REMEDIATION_AUTHORIZATION_FD_ENV = (
    "VOICE_COACH_AIHUB538_REMEDIATION_SOURCE_AUTHORIZATION_FD"
)
_HISTORICAL_AUTHORIZATION_FD_ENV = (
    "VOICE_COACH_AIHUB538_SOURCE_AUTHORIZATION_FD"
)


def _descriptor(value: str) -> int:
    try:
        descriptor = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "descriptor must be an integer >= 3"
        ) from None
    if descriptor < 3:
        raise argparse.ArgumentTypeError("descriptor must be an integer >= 3")
    return descriptor


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--authorization-receipt-fd",
        type=_descriptor,
        required=True,
    )
    parser.add_argument(
        "--destination-root",
        type=Path,
        required=True,
        help="existing private 0700 directory for exact prefix pair children",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _arguments(argv)
    try:
        destination_root = arguments.destination_root.absolute()
        if (
            destination_root.resolve(strict=True) != destination_root
            or not destination_root.is_dir()
            or stat.S_IMODE(
                destination_root.stat(follow_symlinks=False).st_mode
            )
            != 0o700
        ):
            raise ValueError("aihub538_remediation_destination_root_invalid")
        identity = capture_aihub538_descriptor_identity(
            arguments.authorization_receipt_fd,
            require_mode=0o400,
        )
        receipt = load_aihub538_p6_remediation_receipt_descriptor(
            arguments.authorization_receipt_fd
        )
        document = receipt.to_json_object()
        label_roles = document["authorized_label_role_prefix"]
        label_keys = document["authorized_label_file_key_prefix"]
        source_roles = document["authorized_source_role_prefix"]
        source_keys = document["authorized_source_file_key_prefix"]
        if (
            not receipt.is_feasible
            or not isinstance(label_roles, list)
            or not isinstance(label_keys, list)
            or not isinstance(source_roles, list)
            or not isinstance(source_keys, list)
            or not (
                len(label_roles)
                == len(label_keys)
                == len(source_roles)
                == len(source_keys)
                >= 1
            )
        ):
            raise ValueError("aihub538_remediation_source_prefix_not_authorized")
        completed_pairs: list[dict[str, object]] = []
        for label_role, label_key, source_role, source_key in zip(
            label_roles,
            label_keys,
            source_roles,
            source_keys,
            strict=True,
        ):
            if (
                not isinstance(label_role, str)
                or not isinstance(label_key, str)
                or not isinstance(source_role, str)
                or not isinstance(source_key, str)
            ):
                raise ValueError(
                    "aihub538_remediation_source_prefix_not_authorized"
                )
            pair = (label_key, source_key)
            require_aihub538_p6_remediation_source_pair(receipt, pair)
            number = label_role.removeprefix("TL")
            if source_role != f"TS{number}":
                raise ValueError(
                    "aihub538_remediation_source_prefix_not_authorized"
                )
            shard_spec = aihub538_nonlocked_shard_by_id(
                f"ts{number}_tl{number}"
            )
            destination = destination_root / f"ts{number}_tl{number}"
            assert_aihub538_descriptor_identity(
                arguments.authorization_receipt_fd,
                identity,
            )
            already_completed = _verify_completed_pair_if_present(
                destination=destination,
                shard_spec=shard_spec,
            )
            if not already_completed:
                environment = os.environ.copy()
                environment.pop(_HISTORICAL_AUTHORIZATION_FD_ENV, None)
                environment[_REMEDIATION_AUTHORIZATION_FD_ENV] = str(
                    arguments.authorization_receipt_fd
                )
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(REPOSITORY_ROOT / "scripts/download_aihub_secure.py"),
                        "538",
                        f"{label_key},{source_key}",
                        str(destination),
                    ],
                    check=False,
                    env=environment,
                    pass_fds=(arguments.authorization_receipt_fd,),
                )
                if completed.returncode != 0:
                    return completed.returncode
            assert_aihub538_descriptor_identity(
                arguments.authorization_receipt_fd,
                identity,
            )
            if not _verify_completed_pair_if_present(
                destination=destination,
                shard_spec=shard_spec,
            ):
                raise ValueError("aihub538_remediation_completed_pair_missing")
            completed_pairs.append(
                {
                    "shard_id": shard_spec.shard_id,
                    "file_keys": [label_key, source_key],
                    "existing_completed_download_reused": already_completed,
                }
            )
        assert_aihub538_descriptor_identity(
            arguments.authorization_receipt_fd,
            identity,
        )
        print(
            json.dumps(
                {
                    "status": "completed_remediation_prefix_download",
                    "dataset_id": 538,
                    "completed_pairs": completed_pairs,
                    "first_feasible_prefix_complete": True,
                    "authorization_receipt_sha256": receipt.receipt_sha256,
                    "locked_partition_accessed": False,
                    "promotion_authorized": False,
                },
                sort_keys=True,
            )
        )
        return 0
    except (
        Aihub538P6RemediationPublishError,
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
                        else "aihub538_remediation_source_download_failed",
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


def _verify_completed_pair_if_present(
    *,
    destination: Path,
    shard_spec: object,
) -> bool:
    """Reuse only a fully verified exact pair; partials remain resumable."""

    archive = destination / "download.tar"
    receipt = destination / "download.receipt.json"
    archive_exists = archive.exists()
    receipt_exists = receipt.exists()
    if not archive_exists and not receipt_exists:
        if destination.exists():
            metadata = destination.stat(follow_symlinks=False)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise ValueError("aihub538_remediation_source_destination_invalid")
        return False
    if not archive_exists or not receipt_exists:
        raise ValueError("aihub538_remediation_completed_pair_incomplete")
    if (
        not destination.is_dir()
        or stat.S_IMODE(destination.stat(follow_symlinks=False).st_mode) != 0o700
    ):
        raise ValueError("aihub538_remediation_source_destination_invalid")
    archive_descriptor = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW)
    receipt_descriptor = os.open(receipt, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if (
            stat.S_IMODE(os.fstat(archive_descriptor).st_mode) != 0o600
            or stat.S_IMODE(os.fstat(receipt_descriptor).st_mode) != 0o400
        ):
            raise ValueError("aihub538_remediation_completed_pair_mode_invalid")
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
