#!/usr/bin/env python3
"""Consume a sealed P8 discovery authority and download all 32 VL labels.

This entrypoint never accepts file keys and never requests or opens VS source
media.  The first invocation consumes one durable state transaction before
network I/O.  An interrupted exact request may be continued only with
``--resume-existing-transaction`` in the same state and output directories.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from voice_coach.visual_feedback.adapters.aihub538_training_archive import (  # noqa: E402
    Aihub538ArchiveError,
    verify_aihub538_p8_label_download,
)
from voice_coach.visual_feedback.adapters.p8_locked_label_download import (  # noqa: E402
    P8LockedLabelDownloadAdapterError,
    assert_p8_locked_label_descriptor_identity,
    capture_p8_locked_label_descriptor_identity,
    load_p8_locked_cohort_discovery_seal_descriptor,
    load_p8_locked_label_download_completion_at,
    load_p8_locked_label_download_transaction_descriptor,
    lock_p8_locked_label_download_transaction_descriptor,
    lock_p8_locked_label_state_directory,
    open_p8_locked_label_output_directory,
    open_p8_locked_label_output_member,
    open_p8_locked_label_state_directory,
    publish_p8_locked_label_download_completion,
    reconcile_p8_locked_label_pending_publications,
    require_p8_locked_label_transaction_output_binding,
    require_p8_locked_label_transaction_state_binding,
)
from voice_coach.visual_feedback.adapters.p8_seal_store import (  # noqa: E402
    P8SealStateError,
    P8SealStateStore,
)
from voice_coach.visual_feedback.models.p8_locked_label_download import (  # noqa: E402
    AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS,
    P8LockedLabelDownloadError,
    issue_p8_locked_label_download_completion,
    issue_p8_locked_label_download_transaction,
)


_TRANSACTION_FD_ENV = "VOICE_COACH_AIHUB538_P8_LABEL_TRANSACTION_FD"
_STATE_DIRECTORY_FD_ENV = "VOICE_COACH_AIHUB538_P8_STATE_DIRECTORY_FD"


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
        "--state-dir",
        type=Path,
        required=True,
        help="existing P8 state directory containing the base and actual-source seals",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="preexisting owner-only 0700 directory for the unextracted label TAR",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--discovery-seal-fd",
        type=_descriptor,
        help="caller-owned read-only 0400 discovery seal descriptor",
    )
    mode.add_argument(
        "--resume-existing-transaction",
        action="store_true",
        help="resume only the already-consumed exact request",
    )
    parser.add_argument(
        "--dataset-access-permission-confirmed",
        action="store_true",
        help="confirm AI Hub portal access; this does not authorize promotion",
    )
    parser.add_argument(
        "--authorized-at-utc",
        default=None,
        help="optional UTC timestamp for deterministic internal tests",
    )
    return parser.parse_args(argv)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main(argv: list[str] | None = None) -> int:
    arguments = _arguments(argv)
    transaction_descriptor: int | None = None
    output_descriptor: int | None = None
    archive_descriptor: int | None = None
    receipt_descriptor: int | None = None
    state_directory_descriptor: int | None = None
    try:
        output_directory = arguments.output_dir.absolute()
        state_directory = arguments.state_dir.absolute()
        output_descriptor, output_identity = (
            open_p8_locked_label_output_directory(output_directory)
        )
        state_directory_descriptor = open_p8_locked_label_state_directory(
            state_directory
        )
        lock_p8_locked_label_state_directory(state_directory_descriptor)
        state_store = P8SealStateStore(state_directory)
        actual_source_seal = state_store.load_actual_source_read_zero_seal()
        state_root_seal = state_store.load_locked_label_state_root_seal()

        if arguments.resume_existing_transaction:
            if arguments.dataset_access_permission_confirmed:
                raise P8LockedLabelDownloadAdapterError(
                    "p8_locked_label_resume_must_not_reconsume_authorization"
                )
            if arguments.authorized_at_utc is not None:
                raise P8LockedLabelDownloadAdapterError(
                    "p8_locked_label_resume_timestamp_forbidden"
                )
            transaction = state_store.load_locked_label_download_transaction()
        else:
            if not arguments.dataset_access_permission_confirmed:
                raise P8LockedLabelDownloadAdapterError(
                    "p8_locked_label_portal_access_confirmation_required"
                )
            _require_initial_output_empty(output_descriptor)
            discovery_identity = capture_p8_locked_label_descriptor_identity(
                arguments.discovery_seal_fd
            )
            discovery_seal = (
                load_p8_locked_cohort_discovery_seal_descriptor(
                    arguments.discovery_seal_fd
                )
            )
            transaction = issue_p8_locked_label_download_transaction(
                discovery_seal=discovery_seal,
                actual_source_seal=actual_source_seal,
                state_root_seal=state_root_seal,
                output_directory_device=output_identity.device,
                output_directory_inode=output_identity.inode,
                authorized_at_utc=arguments.authorized_at_utc or _utc_now(),
                dataset_access_permission_confirmed=True,
            )
            assert_p8_locked_label_descriptor_identity(
                arguments.discovery_seal_fd,
                discovery_identity,
            )
            state_store.create_locked_label_download_transaction(
                transaction,
                discovery_seal=discovery_seal,
                state_directory_descriptor=state_directory_descriptor,
            )
            assert_p8_locked_label_descriptor_identity(
                arguments.discovery_seal_fd,
                discovery_identity,
            )

        require_p8_locked_label_transaction_output_binding(
            transaction,
            output_directory,
        )
        transaction_descriptor = (
            state_store.open_locked_label_download_transaction_descriptor()
        )
        transaction_identity = capture_p8_locked_label_descriptor_identity(
            transaction_descriptor
        )
        if (
            load_p8_locked_label_download_transaction_descriptor(
                transaction_descriptor
            )
            != transaction
        ):
            raise P8LockedLabelDownloadAdapterError(
                "p8_locked_label_transaction_state_mismatch"
            )
        lock_p8_locked_label_download_transaction_descriptor(
            transaction_descriptor
        )
        assert_p8_locked_label_descriptor_identity(
            transaction_descriptor,
            transaction_identity,
        )
        if require_p8_locked_label_transaction_state_binding(
            state_directory_descriptor=state_directory_descriptor,
            transaction_descriptor=transaction_descriptor,
        ) != transaction:
            raise P8LockedLabelDownloadAdapterError(
                "p8_locked_label_transaction_state_mismatch"
            )

        archive_exists = _output_entry_exists(
            output_descriptor, "download.tar"
        )
        receipt_exists = _output_entry_exists(
            output_descriptor, "download.receipt.json"
        )
        completion_exists = _output_entry_exists(
            output_descriptor,
            "p8_locked_label_download_completion.json",
        )
        if receipt_exists and not archive_exists or (
            completion_exists and not (archive_exists and receipt_exists)
        ):
            raise P8LockedLabelDownloadAdapterError(
                "p8_locked_label_completed_pair_incomplete"
            )
        completed_pair_reused = archive_exists and receipt_exists
        if completed_pair_reused and not arguments.resume_existing_transaction:
            raise P8LockedLabelDownloadAdapterError(
                "p8_locked_label_existing_pair_requires_resume"
            )
        if archive_exists and not receipt_exists and not (
            arguments.resume_existing_transaction
        ):
            raise P8LockedLabelDownloadAdapterError(
                "p8_locked_label_archive_only_requires_resume"
            )
        if not completed_pair_reused:
            environment = os.environ.copy()
            environment[_TRANSACTION_FD_ENV] = str(transaction_descriptor)
            environment[_STATE_DIRECTORY_FD_ENV] = str(
                state_directory_descriptor
            )
            command = [
                sys.executable,
                str(REPOSITORY_ROOT / "scripts/download_aihub_secure.py"),
                "538",
                ",".join(AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS),
                str(output_directory),
            ]
            completed = subprocess.run(
                command,
                check=False,
                env=environment,
                pass_fds=(
                    transaction_descriptor,
                    state_directory_descriptor,
                ),
            )
            environment[_TRANSACTION_FD_ENV] = ""
            environment[_STATE_DIRECTORY_FD_ENV] = ""
            if completed.returncode != 0:
                return completed.returncode
        assert_p8_locked_label_descriptor_identity(
            transaction_descriptor,
            transaction_identity,
        )
        if require_p8_locked_label_transaction_state_binding(
            state_directory_descriptor=state_directory_descriptor,
            transaction_descriptor=transaction_descriptor,
        ) != transaction:
            raise P8LockedLabelDownloadAdapterError(
                "p8_locked_label_transaction_state_mismatch"
            )
        reconcile_p8_locked_label_pending_publications(output_descriptor)

        archive_mode = _output_member_mode(output_descriptor, "download.tar")
        if archive_mode not in {0o600, 0o400} or (
            completion_exists and archive_mode != 0o400
        ):
            raise P8LockedLabelDownloadAdapterError(
                "p8_locked_label_archive_mode_invalid"
            )
        archive_descriptor = open_p8_locked_label_output_member(
            output_descriptor,
            "download.tar",
            require_mode=archive_mode,
        )
        receipt_descriptor = open_p8_locked_label_output_member(
            output_descriptor,
            "download.receipt.json",
            require_mode=0o400,
        )
        archive_before = _file_identity(archive_descriptor)
        receipt_identity = capture_p8_locked_label_descriptor_identity(
            receipt_descriptor
        )
        verified = verify_aihub538_p8_label_download(
            archive_descriptor=archive_descriptor,
            receipt_descriptor=receipt_descriptor,
        )
        if _file_identity(archive_descriptor) != archive_before:
            raise P8LockedLabelDownloadAdapterError(
                "p8_locked_label_archive_changed_during_validation"
            )
        assert_p8_locked_label_descriptor_identity(
            receipt_descriptor,
            receipt_identity,
        )
        assert_p8_locked_label_descriptor_identity(
            transaction_descriptor,
            transaction_identity,
        )
        if archive_mode == 0o600:
            os.fchmod(archive_descriptor, 0o400)
            os.fsync(archive_descriptor)
        if stat.S_IMODE(os.fstat(archive_descriptor).st_mode) != 0o400:
            raise P8LockedLabelDownloadAdapterError(
                "p8_locked_label_archive_read_only_transition_failed"
            )
        sealed_archive_identity = _file_identity(archive_descriptor)
        if completion_exists:
            completion = load_p8_locked_label_download_completion_at(
                output_descriptor
            )
            if (
                completion.transaction_sha256
                != transaction.transaction_sha256
                or completion.discovery_seal_sha256
                != transaction.discovery_seal_sha256
                or completion.archive_size_bytes
                != verified.archive_size_bytes
                or completion.archive_sha256 != verified.archive_sha256
                or completion.secure_download_receipt_sha256
                != verified.receipt_sha256
            ):
                raise P8LockedLabelDownloadAdapterError(
                    "p8_locked_label_download_completion_lineage_mismatch"
                )
        else:
            completion = issue_p8_locked_label_download_completion(
                transaction=transaction,
                archive_size_bytes=verified.archive_size_bytes,
                archive_sha256=verified.archive_sha256,
                secure_download_receipt_sha256=verified.receipt_sha256,
                completed_at_utc=_utc_now(),
            )
            completion = publish_p8_locked_label_download_completion(
                directory_descriptor=output_descriptor,
                completion=completion,
            )
        assert_p8_locked_label_descriptor_identity(
            transaction_descriptor,
            transaction_identity,
        )
        if require_p8_locked_label_transaction_state_binding(
            state_directory_descriptor=state_directory_descriptor,
            transaction_descriptor=transaction_descriptor,
        ) != transaction:
            raise P8LockedLabelDownloadAdapterError(
                "p8_locked_label_transaction_state_mismatch"
            )
        if _file_identity(archive_descriptor) != sealed_archive_identity:
            raise P8LockedLabelDownloadAdapterError(
                "p8_locked_label_archive_changed_after_sealing"
            )
        assert_p8_locked_label_descriptor_identity(
            receipt_descriptor,
            receipt_identity,
        )
        print(
            json.dumps(
                {
                    "status": "completed_p8_validation_label_download_bytes",
                    "dataset_id": 538,
                    "validation_pair_count": 32,
                    "requested_label_file_key_count": 32,
                    "requested_source_file_key_count": 0,
                    "archive_size_bytes": verified.archive_size_bytes,
                    "archive_sha256": verified.archive_sha256,
                    "secure_download_receipt_sha256": verified.receipt_sha256,
                    "transaction_sha256": transaction.transaction_sha256,
                    "completion_sha256": completion.completion_sha256,
                    "archive_mode": "0400",
                    "archive_extracted": False,
                    "payload_admitted": False,
                    "label_archive_presence_verified": False,
                    "source_payload_presence_verified": False,
                    "label_archive_opened": False,
                    "label_census_executed": False,
                    "source_file_keys_requested": False,
                    "source_media_opened": False,
                    "credential_persisted": False,
                    "permission_grants_technical_promotion": False,
                    "promotion_authorized": False,
                    "aggregate_only": True,
                    "existing_completed_download_reused": (
                        completed_pair_reused
                    ),
                },
                sort_keys=True,
            )
        )
        return 0
    except (
        Aihub538ArchiveError,
        OSError,
        P8LockedLabelDownloadAdapterError,
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
                        else "p8_locked_label_download_failed",
                    ),
                    "label_download_completed": False,
                    "payload_admitted": False,
                    "label_archive_opened": False,
                    "source_file_keys_requested": False,
                    "source_media_opened": False,
                    "promotion_authorized": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 65
    finally:
        for descriptor in (
            receipt_descriptor,
            archive_descriptor,
            output_descriptor,
            state_directory_descriptor,
            transaction_descriptor,
        ):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def _require_initial_output_empty(directory_descriptor: int) -> None:
    try:
        entries = os.listdir(directory_descriptor)
    except OSError:
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_output_inspection_failed"
        ) from None
    if entries:
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_initial_output_not_empty"
        )


def _output_entry_exists(directory_descriptor: int, filename: str) -> bool:
    try:
        os.stat(filename, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError:
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_output_inspection_failed"
        ) from None
    return True


def _output_member_mode(directory_descriptor: int, filename: str) -> int:
    try:
        metadata = os.stat(
            filename,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except OSError:
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_output_member_invalid"
        ) from None
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
        raise P8LockedLabelDownloadAdapterError(
            "p8_locked_label_output_member_invalid"
        )
    return stat.S_IMODE(metadata.st_mode)


def _file_identity(descriptor: int) -> tuple[int, int, int, int, int, int]:
    metadata = os.fstat(descriptor)
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_nlink,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


if __name__ == "__main__":
    raise SystemExit(main())
