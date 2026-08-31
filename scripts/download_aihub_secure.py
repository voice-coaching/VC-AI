#!/usr/bin/env python3
"""Resume an approved AI Hub download without placing the API key in argv.

The official shell client forwards ``-aihubapikey`` to a child process.  That
makes the credential observable in ordinary process listings.  This small
wrapper implements the documented HTTP request directly, reads the key only
from ``AIHUB_API_KEY``, preserves interrupted partial files, and emits a
credential-free integrity receipt after the response is complete.

It intentionally downloads but does not extract the returned TAR.  Archive
admission/extraction belongs to the dataset-specific, path-safe adapter.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
from typing import BinaryIO, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


# AI Hub identifiers are canonical positive decimals.  Accepting leading zeroes
# would create a second spelling that bypasses exact protected-key comparisons
# if the remote service normalizes numeric IDs.
_DIGITS = re.compile(r"^[1-9][0-9]*$")
_FILE_KEYS = re.compile(r"^[1-9][0-9]*(?:,[1-9][0-9]*)*$")
_CONTENT_RANGE = re.compile(r"^bytes ([0-9]+)-([0-9]+)/([0-9]+)$")
_UNSATISFIED_CONTENT_RANGE = re.compile(r"^bytes \*/([0-9]+)$")
_REQUEST_RANGE = re.compile(r"^bytes=[0-9]+-$")
_CHUNK_BYTES = 8 * 1024 * 1024
_PROGRESS_BYTES = 256 * 1024 * 1024
_SCHEMA_VERSION = "voice_coach.aihub_secure_download_receipt.v1"
_AIHUB538_SOURCE_AUTHORIZATION_FD_ENV = (
    "VOICE_COACH_AIHUB538_SOURCE_AUTHORIZATION_FD"
)
_AIHUB538_REMEDIATION_SOURCE_AUTHORIZATION_FD_ENV = (
    "VOICE_COACH_AIHUB538_REMEDIATION_SOURCE_AUTHORIZATION_FD"
)
_AIHUB538_REMEDIATION_LABEL_TRANSACTION_FD_ENV = (
    "VOICE_COACH_AIHUB538_REMEDIATION_LABEL_TRANSACTION_FD"
)
_AIHUB538_REMEDIATION_PRIOR_PREFIX_RECEIPT_FD_ENV = (
    "VOICE_COACH_AIHUB538_REMEDIATION_PRIOR_PREFIX_RECEIPT_FD"
)
_AIHUB538_P8_LABEL_TRANSACTION_FD_ENV = (
    "VOICE_COACH_AIHUB538_P8_LABEL_TRANSACTION_FD"
)
_AIHUB538_P8_STATE_DIRECTORY_FD_ENV = (
    "VOICE_COACH_AIHUB538_P8_STATE_DIRECTORY_FD"
)
_AIHUB539_ACTUAL_MEDIA_AUTHORIZATION_FD_ENV = (
    "VOICE_COACH_AIHUB539_ACTUAL_MEDIA_AUTHORIZATION_FD"
)
_AIHUB539_ACTUAL_MEDIA_FILE_KEYS = ("54575", "54620")
_AIHUB538_LADDER_SOURCE_KEYS = {
    "54945",
    "54946",
    "54947",
    "54948",
    "54949",
    "54951",
}
_AIHUB538_AUTHORIZED_PAIR_KEYS = {
    ("54726", "54945"),
    ("54727", "54946"),
}
_AIHUB538_REMEDIATION_PAIR_KEYS = {
    ("54728", "54947"),
    ("54729", "54948"),
    ("54730", "54949"),
    ("54732", "54951"),
}
_AIHUB538_REMEDIATION_SOURCE_KEYS = {
    pair[1] for pair in _AIHUB538_REMEDIATION_PAIR_KEYS
}
_AIHUB538_REMEDIATION_LABEL_KEYS = {
    pair[0] for pair in _AIHUB538_REMEDIATION_PAIR_KEYS
}


class DownloadError(RuntimeError):
    """A public, credential-free download failure."""


_AIHUB_DOWNLOAD_ORIGIN = ("https", "api.aihub.or.kr", 443)
_AIHUB_STREAM_ORIGIN = ("https", "stream.aihub.or.kr", 443)
_REDIRECT_SENSITIVE_NAMES = frozenset(
    {"apikey", "authorization", "cookie", "proxy-authorization"}
)


def _normalized_origin(url: str) -> tuple[str, str, int]:
    try:
        parsed = urlsplit(url)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError
        scheme = parsed.scheme.lower()
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        raise DownloadError("aihub_download_origin_invalid") from None
    if not hostname or scheme not in {"http", "https"}:
        raise DownloadError("aihub_download_origin_invalid")
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, hostname, port


class _PinnedOriginRedirectHandler(HTTPRedirectHandler):
    """Perform a one-way API-to-stream handoff without forwarding secrets."""

    def redirect_request(  # noqa: PLR0913
        self,
        request: Request,
        file_pointer: object,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> Request | None:
        resolved = urljoin(request.full_url, new_url)
        current_origin = _normalized_origin(request.full_url)
        target_origin = _normalized_origin(resolved)
        request_headers = {
            name.lower(): value for name, value in request.header_items()
        }
        reflected_secrets = tuple(
            value
            for name, value in request_headers.items()
            if name in _REDIRECT_SENSITIVE_NAMES and value
        )
        _require_redirect_target_has_no_credential_reflection(
            resolved,
            reflected_secrets=reflected_secrets,
        )
        if current_origin == _AIHUB_DOWNLOAD_ORIGIN:
            if target_origin != _AIHUB_STREAM_ORIGIN:
                raise DownloadError("aihub_cross_origin_redirect_forbidden")
        elif current_origin == _AIHUB_STREAM_ORIGIN:
            if target_origin != _AIHUB_STREAM_ORIGIN:
                raise DownloadError("aihub_cross_origin_redirect_forbidden")
            if set(request_headers) & _REDIRECT_SENSITIVE_NAMES:
                raise DownloadError("aihub_redirect_secret_reflection_forbidden")
        else:
            raise DownloadError("aihub_cross_origin_redirect_forbidden")
        del file_pointer, code, message, headers
        safe_headers: dict[str, str] = {}
        range_value = request_headers.get("range")
        if range_value is not None:
            if not _REQUEST_RANGE.fullmatch(range_value):
                raise DownloadError("aihub_redirect_range_invalid")
            safe_headers["Range"] = range_value
        return Request(resolved, headers=safe_headers, method="GET")


def _require_redirect_target_has_no_credential_reflection(
    resolved: str,
    *,
    reflected_secrets: tuple[str, ...],
) -> None:
    """Reject header-secret reflection without exposing the signed Location."""

    parsed = urlsplit(resolved)
    try:
        query_names = {
            name.lower() for name, _value in parse_qsl(
                parsed.query,
                keep_blank_values=True,
                strict_parsing=False,
            )
        }
    except ValueError:
        raise DownloadError("aihub_redirect_query_invalid") from None
    if query_names & _REDIRECT_SENSITIVE_NAMES:
        raise DownloadError("aihub_redirect_secret_reflection_forbidden")
    decoded_target = unquote(resolved)
    if any(secret in decoded_target for secret in reflected_secrets):
        raise DownloadError("aihub_redirect_secret_reflection_forbidden")


def _require_initial_aihub_request(request: Request) -> None:
    if (
        _normalized_origin(request.full_url) != _AIHUB_DOWNLOAD_ORIGIN
        or request.get_method() != "GET"
    ):
        raise DownloadError("aihub_download_origin_forbidden")
    headers = {name.lower(): value for name, value in request.header_items()}
    if set(headers) - {"apikey", "range"} or not headers.get("apikey"):
        raise DownloadError("aihub_download_initial_headers_invalid")
    range_value = headers.get("range")
    if range_value is not None and not _REQUEST_RANGE.fullmatch(range_value):
        raise DownloadError("aihub_download_initial_headers_invalid")


def urlopen(request: Request, *, timeout: float):  # noqa: ANN201
    """Open at API origin, then permit only a secret-free stream handoff."""

    _require_initial_aihub_request(request)
    opener = build_opener(_PinnedOriginRedirectHandler())
    return opener.open(request, timeout=timeout)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_key")
    parser.add_argument("file_keys")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    return parser.parse_args()


def _validate_inputs(arguments: argparse.Namespace) -> None:
    if not _DIGITS.fullmatch(arguments.dataset_key):
        raise DownloadError("dataset_key_invalid")
    if not _FILE_KEYS.fullmatch(arguments.file_keys):
        raise DownloadError("file_keys_invalid")
    if arguments.timeout_seconds <= 0:
        raise DownloadError("timeout_invalid")


def _aihub539_actual_media_authorization_guard(
    arguments: argparse.Namespace,
) -> Callable[[], None]:
    """Bind the exact non-locked Training TL8/TS8 request to a receipt."""

    if not _DIGITS.fullmatch(arguments.dataset_key):
        raise DownloadError("dataset_key_invalid")
    if not _FILE_KEYS.fullmatch(arguments.file_keys):
        raise DownloadError("file_keys_invalid")
    requested = tuple(arguments.file_keys.split(","))
    protected_keys = set(_AIHUB539_ACTUAL_MEDIA_FILE_KEYS)
    descriptor_text = os.environ.get(
        _AIHUB539_ACTUAL_MEDIA_AUTHORIZATION_FD_ENV,
        "",
    )
    if arguments.dataset_key == "539":
        if requested != _AIHUB539_ACTUAL_MEDIA_FILE_KEYS:
            if len(requested) == len(_AIHUB539_ACTUAL_MEDIA_FILE_KEYS) and set(requested) == protected_keys:
                raise DownloadError("aihub539_actual_media_file_key_order_invalid")
            reason = (
                "aihub539_actual_media_file_keys_invalid"
                if set(requested) & protected_keys
                else "aihub539_actual_media_file_keys_not_authorized"
            )
            raise DownloadError(reason)
    elif set(requested) & protected_keys:
        raise DownloadError("aihub539_actual_media_dataset_mismatch")
    elif descriptor_text:
        raise DownloadError("aihub539_actual_media_authorization_request_mismatch")
    try:
        descriptor = int(descriptor_text)
        if descriptor < 3:
            raise ValueError
    except ValueError:
        raise DownloadError(
            "aihub539_actual_media_authorization_required"
        ) from None

    repository_root = Path(__file__).resolve().parents[1]
    source_root = str(repository_root / "src")
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    try:
        from voice_coach.visual_feedback.adapters.aihub539_actual_media_download import (
            Aihub539ActualMediaDownloadRequest,
            bind_aihub539_actual_media_download,
        )

        binding = bind_aihub539_actual_media_download(
            Aihub539ActualMediaDownloadRequest(
                dataset_key=arguments.dataset_key,
                ordered_file_keys=requested,
                destination=arguments.destination.absolute(),
                authorization_descriptor=descriptor,
            )
        )
    except Exception as error:
        reason_code = getattr(
            error,
            "reason_code",
            "aihub539_actual_media_authorization_invalid",
        )
        if not isinstance(reason_code, str) or not reason_code.startswith(
            "aihub539_"
        ):
            reason_code = "aihub539_actual_media_authorization_invalid"
        raise DownloadError(reason_code) from None

    def assert_stable() -> None:
        try:
            binding.assert_stable()
        except Exception:
            raise DownloadError(
                "aihub539_actual_media_authorization_changed"
            ) from None

    return assert_stable


def _aihub538_source_authorization_guard(
    arguments: argparse.Namespace,
) -> Callable[[], None]:
    """Require a stable inherited v5 descriptor for restricted source keys."""

    if not _DIGITS.fullmatch(arguments.dataset_key):
        raise DownloadError("dataset_key_invalid")
    if not _FILE_KEYS.fullmatch(arguments.file_keys):
        raise DownloadError("file_keys_invalid")
    requested = tuple(arguments.file_keys.split(","))
    requested_set = set(requested)
    p8_label_keys, p8_source_keys = _aihub538_p8_validation_file_keys()
    p8_label_key_set = set(p8_label_keys)
    p8_source_key_set = set(p8_source_keys)
    if requested_set & (p8_label_key_set | p8_source_key_set) and (
        arguments.dataset_key != "538"
    ):
        raise DownloadError("aihub538_p8_file_key_dataset_mismatch")
    if (
        arguments.dataset_key == "538"
        and {"55082", "55114"} & requested_set
        and requested != p8_label_keys
    ):
        # Preserve the established V11 fail-closed reason while the production
        # all-label path uses its stricter full-inventory transaction.
        raise DownloadError("aihub538_locked_download_entrypoint_required")
    if requested_set & p8_source_key_set:
        # No P8 validation source key is downloadable from the label-only or
        # non-locked calibration entrypoint.
        raise DownloadError("aihub538_p8_source_download_entrypoint_required")
    if requested_set & p8_label_key_set:
        if requested != p8_label_keys:
            raise DownloadError("aihub538_p8_label_request_not_exact")
        p8_transaction_descriptor_text = os.environ.get(
            _AIHUB538_P8_LABEL_TRANSACTION_FD_ENV,
            "",
        )
        try:
            descriptor = int(p8_transaction_descriptor_text)
            if descriptor < 3:
                raise ValueError
            state_directory_descriptor = int(
                os.environ.get(_AIHUB538_P8_STATE_DIRECTORY_FD_ENV, "")
            )
            if state_directory_descriptor < 3:
                raise ValueError
        except ValueError:
            raise DownloadError(
                "aihub538_p8_label_transaction_required"
            ) from None
        repository_root = Path(__file__).resolve().parents[1]
        source_root = str(repository_root / "src")
        if source_root not in sys.path:
            sys.path.insert(0, source_root)
        try:
            from voice_coach.visual_feedback.adapters.p8_locked_label_download import (
                assert_p8_locked_label_descriptor_identity,
                capture_p8_locked_label_descriptor_identity,
                load_p8_locked_label_download_transaction_descriptor,
                lock_p8_locked_label_download_transaction_descriptor,
                lock_p8_locked_label_state_directory,
                require_p8_locked_label_transaction_output_binding,
                require_p8_locked_label_transaction_state_binding,
            )

            identity = capture_p8_locked_label_descriptor_identity(descriptor)
            lock_p8_locked_label_state_directory(
                state_directory_descriptor
            )
            transaction = (
                load_p8_locked_label_download_transaction_descriptor(
                    descriptor
                )
            )
            if require_p8_locked_label_transaction_state_binding(
                state_directory_descriptor=state_directory_descriptor,
                transaction_descriptor=descriptor,
            ) != transaction:
                raise ValueError
            require_p8_locked_label_transaction_output_binding(
                transaction,
                arguments.destination,
            )
            lock_p8_locked_label_download_transaction_descriptor(descriptor)
            assert_p8_locked_label_descriptor_identity(descriptor, identity)
        except Exception:
            raise DownloadError(
                "aihub538_p8_label_transaction_invalid"
            ) from None

        def assert_p8_stable() -> None:
            try:
                assert_p8_locked_label_descriptor_identity(
                    descriptor, identity
                )
                require_p8_locked_label_transaction_output_binding(
                    transaction,
                    arguments.destination,
                )
                if require_p8_locked_label_transaction_state_binding(
                    state_directory_descriptor=state_directory_descriptor,
                    transaction_descriptor=descriptor,
                ) != transaction:
                    raise ValueError
            except Exception:
                raise DownloadError(
                    "aihub538_p8_label_transaction_changed"
                ) from None

        return assert_p8_stable
    if os.environ.get(_AIHUB538_P8_LABEL_TRANSACTION_FD_ENV, ""):
        raise DownloadError("aihub538_p8_label_transaction_request_mismatch")
    if (
        requested_set & _AIHUB538_LADDER_SOURCE_KEYS
        and arguments.dataset_key != "538"
    ):
        raise DownloadError("aihub538_source_file_key_dataset_mismatch")
    if requested_set & _AIHUB538_REMEDIATION_LABEL_KEYS and (
        arguments.dataset_key != "538"
    ):
        raise DownloadError(
            "aihub538_remediation_label_file_key_dataset_mismatch"
        )
    if (
        arguments.dataset_key == "538"
        and requested_set & _AIHUB538_REMEDIATION_LABEL_KEYS
        and not requested_set & _AIHUB538_REMEDIATION_SOURCE_KEYS
    ):
        if len(requested) != 1 or requested[0] not in (
            _AIHUB538_REMEDIATION_LABEL_KEYS
        ):
            raise DownloadError(
                "aihub538_remediation_label_request_not_exact"
            )
        return _aihub538_remediation_label_authorization_guard(
            arguments=arguments,
            requested_file_key=requested[0],
        )
    if os.environ.get(
        _AIHUB538_REMEDIATION_LABEL_TRANSACTION_FD_ENV,
        "",
    ):
        raise DownloadError(
            "aihub538_remediation_label_transaction_request_mismatch"
        )
    if os.environ.get(
        _AIHUB538_REMEDIATION_PRIOR_PREFIX_RECEIPT_FD_ENV,
        "",
    ):
        raise DownloadError(
            "aihub538_remediation_prior_prefix_receipt_request_mismatch"
        )
    if (
        arguments.dataset_key != "538"
        or not (_AIHUB538_LADDER_SOURCE_KEYS & set(requested))
    ):
        if os.environ.get(
            _AIHUB538_REMEDIATION_SOURCE_AUTHORIZATION_FD_ENV,
            "",
        ):
            raise DownloadError(
                "aihub538_remediation_source_authorization_request_mismatch"
            )
        return lambda: None
    if requested_set & _AIHUB538_REMEDIATION_SOURCE_KEYS:
        if requested not in _AIHUB538_REMEDIATION_PAIR_KEYS:
            raise DownloadError(
                "aihub538_remediation_source_pair_not_authorized"
            )
        return _aihub538_remediation_source_authorization_guard(
            requested_file_keys=requested
        )
    if os.environ.get(
        _AIHUB538_REMEDIATION_SOURCE_AUTHORIZATION_FD_ENV,
        "",
    ):
        raise DownloadError(
            "aihub538_remediation_source_authorization_request_mismatch"
        )
    if requested not in _AIHUB538_AUTHORIZED_PAIR_KEYS:
        raise DownloadError("aihub538_source_pair_not_authorized")
    descriptor_text = os.environ.get(
        _AIHUB538_SOURCE_AUTHORIZATION_FD_ENV,
        "",
    )
    try:
        descriptor = int(descriptor_text)
        if descriptor < 3:
            raise ValueError
    except ValueError:
        raise DownloadError("aihub538_source_authorization_required") from None

    repository_root = Path(__file__).resolve().parents[1]
    source_root = str(repository_root / "src")
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    try:
        from voice_coach.visual_feedback.adapters.aihub538_admitted_archive_binding import (
            assert_aihub538_descriptor_identity,
            capture_aihub538_descriptor_identity,
        )
        from voice_coach.visual_feedback.services.aihub538_label_capacity_audit import (
            load_aihub538_label_selection_authorization_receipt_descriptor,
        )
        from voice_coach.visual_feedback.models.aihub538_label_selection_authorization import (
            require_aihub538_frozen_source_authorization_receipt,
        )

        identity = capture_aihub538_descriptor_identity(
            descriptor,
            require_mode=0o400,
        )
        receipt = (
            load_aihub538_label_selection_authorization_receipt_descriptor(
                descriptor
            )
        )
        require_aihub538_frozen_source_authorization_receipt(receipt)
        document = receipt.to_json_object()
        if (
            not receipt.is_feasible
            or document["evaluated_label_prefix"] != ["TL175", "TL176"]
            or document["evaluated_label_file_key_prefix"]
            != ["54726", "54727"]
            or document["authorized_source_role_prefix"]
            != ["TS175", "TS176"]
            or document["authorized_source_file_key_prefix"]
            != ["54945", "54946"]
            or [item.label_file_key for item in receipt.prefix_lineage]
            != ["54726", "54727"]
            or document["source_download_authorized"] is not True
        ):
            raise ValueError
        assert_aihub538_descriptor_identity(descriptor, identity)
    except Exception:
        raise DownloadError("aihub538_source_authorization_invalid") from None

    def assert_stable() -> None:
        try:
            assert_aihub538_descriptor_identity(descriptor, identity)
        except Exception:
            raise DownloadError(
                "aihub538_source_authorization_changed"
            ) from None

    return assert_stable


def _aihub538_remediation_label_authorization_guard(
    *,
    arguments: argparse.Namespace,
    requested_file_key: str,
) -> Callable[[], None]:
    """Require a stable one-label transaction bound to the destination inode."""

    try:
        descriptor = int(
            os.environ.get(
                _AIHUB538_REMEDIATION_LABEL_TRANSACTION_FD_ENV,
                "",
            )
        )
        if descriptor < 3:
            raise ValueError
    except ValueError:
        raise DownloadError(
            "aihub538_remediation_label_transaction_required"
        ) from None
    repository_root = Path(__file__).resolve().parents[1]
    source_root = str(repository_root / "src")
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    try:
        from voice_coach.visual_feedback.adapters.aihub538_admitted_archive_binding import (
            assert_aihub538_descriptor_identity,
            capture_aihub538_descriptor_identity,
        )
        from voice_coach.visual_feedback.adapters.aihub538_p6_remediation_label_download import (
            assert_aihub538_p6_remediation_label_descriptor_identity,
            capture_aihub538_p6_remediation_label_descriptor_identity,
            load_aihub538_p6_remediation_label_transaction_descriptor,
            lock_aihub538_p6_remediation_label_transaction_descriptor,
            require_aihub538_p6_remediation_label_transaction_output_binding,
        )
        from voice_coach.visual_feedback.adapters.aihub538_p6_remediation_publish import (
            load_aihub538_p6_remediation_receipt_descriptor,
        )

        identity = (
            capture_aihub538_p6_remediation_label_descriptor_identity(
                descriptor
            )
        )
        transaction = (
            load_aihub538_p6_remediation_label_transaction_descriptor(
                descriptor
            )
        )
        if (
            transaction.label_file_key != requested_file_key
            or transaction.to_json_object()[
                "requested_source_file_key_count"
            ]
            != 0
        ):
            raise ValueError
        prior_descriptor: int | None = None
        prior_identity = None
        if transaction.prefix_sequence_number == 1:
            if os.environ.get(
                _AIHUB538_REMEDIATION_PRIOR_PREFIX_RECEIPT_FD_ENV,
                "",
            ):
                raise ValueError
        else:
            prior_descriptor = int(
                os.environ.get(
                    _AIHUB538_REMEDIATION_PRIOR_PREFIX_RECEIPT_FD_ENV,
                    "",
                )
            )
            if prior_descriptor < 3 or prior_descriptor == descriptor:
                raise ValueError
            prior_identity = capture_aihub538_descriptor_identity(
                prior_descriptor,
                require_mode=0o400,
            )
            prior = load_aihub538_p6_remediation_receipt_descriptor(
                prior_descriptor
            )
            prior_document = prior.to_json_object()
            if (
                prior.is_feasible
                or prior.receipt_sha256
                != transaction.prior_prefix_receipt_sha256
                or prior_document["next_label_role_component"]
                != transaction.label_role_component
                or prior_document["next_label_file_key"]
                != transaction.label_file_key
            ):
                raise ValueError
            fcntl.flock(prior_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert_aihub538_descriptor_identity(
                prior_descriptor,
                prior_identity,
            )
        require_aihub538_p6_remediation_label_transaction_output_binding(
            transaction,
            arguments.destination.absolute(),
        )
        lock_aihub538_p6_remediation_label_transaction_descriptor(descriptor)
        assert_aihub538_p6_remediation_label_descriptor_identity(
            descriptor,
            identity,
        )
    except Exception:
        raise DownloadError(
            "aihub538_remediation_label_transaction_invalid"
        ) from None

    def assert_stable() -> None:
        try:
            assert_aihub538_p6_remediation_label_descriptor_identity(
                descriptor,
                identity,
            )
            require_aihub538_p6_remediation_label_transaction_output_binding(
                transaction,
                arguments.destination.absolute(),
            )
            if prior_descriptor is not None:
                assert prior_identity is not None
                assert_aihub538_descriptor_identity(
                    prior_descriptor,
                    prior_identity,
                )
        except Exception:
            raise DownloadError(
                "aihub538_remediation_label_transaction_changed"
            ) from None

    return assert_stable


def _aihub538_remediation_source_authorization_guard(
    *,
    requested_file_keys: tuple[str, ...],
) -> Callable[[], None]:
    """Require one stable first-feasible remediation receipt descriptor."""

    descriptor_text = os.environ.get(
        _AIHUB538_REMEDIATION_SOURCE_AUTHORIZATION_FD_ENV,
        "",
    )
    try:
        descriptor = int(descriptor_text)
        if descriptor < 3:
            raise ValueError
    except ValueError:
        raise DownloadError(
            "aihub538_remediation_source_authorization_required"
        ) from None

    repository_root = Path(__file__).resolve().parents[1]
    source_root = str(repository_root / "src")
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    try:
        from voice_coach.visual_feedback.adapters.aihub538_admitted_archive_binding import (
            assert_aihub538_descriptor_identity,
            capture_aihub538_descriptor_identity,
        )
        from voice_coach.visual_feedback.adapters.aihub538_p6_remediation_publish import (
            load_aihub538_p6_remediation_receipt_descriptor,
        )
        from voice_coach.visual_feedback.models.aihub538_p6_remediation import (
            require_aihub538_p6_remediation_source_pair,
        )

        identity = capture_aihub538_descriptor_identity(
            descriptor,
            require_mode=0o400,
        )
        receipt = load_aihub538_p6_remediation_receipt_descriptor(descriptor)
        require_aihub538_p6_remediation_source_pair(
            receipt,
            requested_file_keys,
        )
        assert_aihub538_descriptor_identity(descriptor, identity)
    except Exception:
        raise DownloadError(
            "aihub538_remediation_source_authorization_invalid"
        ) from None

    def assert_stable() -> None:
        try:
            assert_aihub538_descriptor_identity(descriptor, identity)
        except Exception:
            raise DownloadError(
                "aihub538_remediation_source_authorization_changed"
            ) from None

    return assert_stable


def _aihub538_p8_validation_file_keys() -> tuple[
    tuple[str, ...], tuple[str, ...]
]:
    repository_root = Path(__file__).resolve().parents[1]
    source_root = str(repository_root / "src")
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    from voice_coach.visual_feedback.models.p8_locked_label_download import (
        AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS,
        AIHUB538_P8_VALIDATION_SOURCE_FILE_KEYS,
    )

    return (
        AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS,
        AIHUB538_P8_VALIDATION_SOURCE_FILE_KEYS,
    )


def _is_exact_aihub538_p8_label_request(
    arguments: argparse.Namespace,
) -> bool:
    labels, _sources = _aihub538_p8_validation_file_keys()
    return (
        arguments.dataset_key == "538"
        and tuple(arguments.file_keys.split(",")) == labels
    )


def _is_exact_aihub538_remediation_label_request(
    arguments: argparse.Namespace,
) -> bool:
    requested = tuple(arguments.file_keys.split(","))
    return (
        arguments.dataset_key == "538"
        and len(requested) == 1
        and requested[0] in _AIHUB538_REMEDIATION_LABEL_KEYS
    )


def _expected_total(
    *, status: int, offset: int, headers: object
) -> tuple[int | None, bool]:
    """Return expected total bytes and whether the response can be appended."""

    content_length_text = headers.get("Content-Length")  # type: ignore[attr-defined]
    try:
        content_length = int(content_length_text) if content_length_text else None
    except ValueError:
        raise DownloadError("response_content_length_invalid") from None

    if status == 206:
        content_range = headers.get("Content-Range")  # type: ignore[attr-defined]
        match = _CONTENT_RANGE.fullmatch(content_range or "")
        if match is None or int(match.group(1)) != offset:
            raise DownloadError("response_content_range_invalid")
        end = int(match.group(2))
        total = int(match.group(3))
        if end < offset or total != end + 1:
            raise DownloadError("response_content_range_invalid")
        if content_length is not None and content_length != total - offset:
            raise DownloadError("response_content_length_mismatch")
        return total, True

    if status == 200:
        # AI Hub currently streams some multi-file TAR responses without a
        # Content-Length.  urllib still validates HTTP chunk framing and raises
        # if that framing is truncated; archive admission performs the second,
        # format-level completeness check before any member is trusted.
        if content_length is None:
            return None, False
        if content_length <= 0:
            raise DownloadError("response_content_length_invalid")
        return content_length, False

    raise DownloadError(f"http_status_{status}")


def _stream_response(
    response: BinaryIO,
    *,
    partial_path: Path,
    append: bool,
    expected_total: int | None,
) -> int:
    flags = os.O_WRONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if append:
        flags |= os.O_APPEND
    else:
        flags |= os.O_CREAT | os.O_EXCL
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(partial_path, flags, 0o600)
        except FileExistsError:
            if append:
                raise
            descriptor = os.open(
                partial_path,
                os.O_WRONLY | os.O_TRUNC | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
        ):
            raise DownloadError("aihub_partial_output_invalid")
        completed = metadata.st_size
    except (OSError, DownloadError):
        if descriptor is not None:
            os.close(descriptor)
        raise DownloadError("aihub_partial_output_invalid") from None
    next_progress = ((completed // _PROGRESS_BYTES) + 1) * _PROGRESS_BYTES
    try:
        while True:
            chunk = response.read(_CHUNK_BYTES)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError
                view = view[written:]
            completed += len(chunk)
            if completed >= next_progress:
                print(f"downloaded_bytes={completed}", flush=True)
                next_progress = ((completed // _PROGRESS_BYTES) + 1) * _PROGRESS_BYTES
        os.fsync(descriptor)
        if expected_total is not None and completed != expected_total:
            raise DownloadError("response_truncated_partial_preserved")
        return completed
    except DownloadError:
        raise
    except (OSError, TypeError):
        raise DownloadError("network_or_storage_failure_partial_preserved") from None
    finally:
        os.close(descriptor)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise OSError
        while chunk := os.read(descriptor, _CHUNK_BYTES):
            digest.update(chunk)
    except OSError:
        raise DownloadError("aihub_archive_output_invalid") from None
    finally:
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass
    return digest.hexdigest()


def _write_receipt(
    *,
    receipt_path: Path,
    dataset_key: str,
    file_keys: str,
    archive_size_bytes: int,
    archive_sha256: str,
) -> None:
    receipt = {
        "schema_version": _SCHEMA_VERSION,
        "dataset_key": dataset_key,
        "file_keys": file_keys.split(","),
        "archive_size_bytes": archive_size_bytes,
        "archive_sha256": archive_sha256,
        "download_completed_unix_seconds": int(time.time()),
        "credential_persisted": False,
        "archive_extracted": False,
    }
    temporary = receipt_path.with_suffix(receipt_path.suffix + ".tmp")
    payload = json.dumps(receipt, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(payload)
            output.flush()
            os.fchmod(output.fileno(), 0o400)
            os.fsync(output.fileno())
        os.replace(temporary, receipt_path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def download(arguments: argparse.Namespace) -> Path:
    _validate_inputs(arguments)
    assert_aihub538_authorization_stable = (
        _aihub538_source_authorization_guard(arguments)
    )
    assert_aihub539_authorization_stable = (
        _aihub539_actual_media_authorization_guard(arguments)
    )

    def assert_source_authorization_stable() -> None:
        assert_aihub538_authorization_stable()
        assert_aihub539_authorization_stable()

    assert_source_authorization_stable()
    api_key = os.environ.get("AIHUB_API_KEY", "")
    if not api_key:
        raise DownloadError("aihub_api_key_missing")

    if (
        _is_exact_aihub538_p8_label_request(arguments)
        or _is_exact_aihub538_remediation_label_request(arguments)
    ):
        try:
            return _download_p8_label_request(
                arguments,
                api_key=api_key,
                assert_authorization_stable=(
                    assert_source_authorization_stable
                ),
            )
        finally:
            api_key = ""

    destination = arguments.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination, 0o700)
    archive_path = destination / "download.tar"
    partial_path = destination / "download.tar.partial"
    receipt_path = destination / "download.receipt.json"
    if archive_path.exists() or receipt_path.exists():
        raise DownloadError("completed_download_already_exists")

    offset = partial_path.stat().st_size if partial_path.exists() else 0
    url = (
        "https://api.aihub.or.kr/down/0.6/"
        f"{arguments.dataset_key}.do?fileSn={arguments.file_keys}"
    )
    headers = {"apikey": api_key}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = Request(url, headers=headers, method="GET")
    # The authorization descriptor is checked immediately before network I/O,
    # closing the CLI-parse-to-request TOCTOU window.
    assert_source_authorization_stable()
    try:
        with urlopen(request, timeout=arguments.timeout_seconds) as response:
            status = int(response.status)
            expected_total, append = _expected_total(
                status=status,
                offset=offset,
                headers=response.headers,
            )
            if offset and not append:
                print("range_not_supported_restarting_from_zero", flush=True)
            completed = _stream_response(
                response,
                partial_path=partial_path,
                append=append,
                expected_total=expected_total,
            )
    except HTTPError as error:
        raise DownloadError(f"http_status_{error.code}") from None
    except (TimeoutError, URLError, HTTPException, OSError):
        raise DownloadError("network_or_storage_failure_partial_preserved") from None
    finally:
        api_key = ""

    assert_source_authorization_stable()

    os.replace(partial_path, archive_path)
    try:
        archive_metadata = os.stat(archive_path, follow_symlinks=False)
        if not stat.S_ISREG(archive_metadata.st_mode):
            raise OSError
    except OSError:
        raise DownloadError("aihub_archive_output_invalid") from None
    os.chmod(archive_path, 0o600, follow_symlinks=False)
    archive_sha256 = _sha256(archive_path)
    _write_receipt(
        receipt_path=receipt_path,
        dataset_key=arguments.dataset_key,
        file_keys=arguments.file_keys,
        archive_size_bytes=completed,
        archive_sha256=archive_sha256,
    )
    assert_source_authorization_stable()
    print(f"download_complete bytes={completed} sha256={archive_sha256}")
    return archive_path


_P8_MAXIMUM_LABEL_DOWNLOAD_BYTES = 64 * 1024**3
_AIHUB538_REMEDIATION_MAXIMUM_LABEL_DOWNLOAD_BYTES = 128 * 1024**3


def _require_p8_response_framing(
    *,
    status: int,
    headers: object,
) -> None:
    """Require framing that makes a clean EOF distinguishable from truncation."""

    if status != 200:
        return
    getter = getattr(headers, "get", None)
    if not callable(getter):
        raise DownloadError("p8_label_response_framing_required") from None
    content_length = getter("Content-Length")
    transfer_encoding = getter("Transfer-Encoding")
    if transfer_encoding is None:
        if content_length is None:
            raise DownloadError("p8_label_response_framing_required")
        return
    if (
        not isinstance(transfer_encoding, str)
        or transfer_encoding.strip().lower() != "chunked"
        or content_length is not None
    ):
        raise DownloadError("p8_label_response_framing_invalid")


def _download_p8_label_request(
    arguments: argparse.Namespace,
    *,
    api_key: str,
    assert_authorization_stable: Callable[[], None],
) -> Path:
    """Download one authorized label request through no-follow dirfd operations."""

    repository_root = Path(__file__).resolve().parents[1]
    source_root = str(repository_root / "src")
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    from voice_coach.visual_feedback.adapters.p8_locked_label_download import (
        open_p8_locked_label_output_directory,
    )

    remediation_request = (
        _is_exact_aihub538_remediation_label_request(arguments)
    )
    maximum_label_download_bytes = (
        _AIHUB538_REMEDIATION_MAXIMUM_LABEL_DOWNLOAD_BYTES
        if remediation_request
        else _P8_MAXIMUM_LABEL_DOWNLOAD_BYTES
    )

    directory_descriptor, _identity = open_p8_locked_label_output_directory(
        arguments.destination
    )
    try:
        try:
            if remediation_request:
                from voice_coach.visual_feedback.adapters.aihub538_p6_remediation_label_download import (
                    load_aihub538_p6_remediation_label_transaction_descriptor,
                )

                transaction_descriptor = int(
                    os.environ[
                        _AIHUB538_REMEDIATION_LABEL_TRANSACTION_FD_ENV
                    ]
                )
                transaction = (
                    load_aihub538_p6_remediation_label_transaction_descriptor(
                        transaction_descriptor
                    )
                )
            else:
                from voice_coach.visual_feedback.adapters.p8_locked_label_download import (
                    load_p8_locked_label_download_transaction_descriptor,
                )

                transaction_descriptor = int(
                    os.environ[_AIHUB538_P8_LABEL_TRANSACTION_FD_ENV]
                )
                transaction = (
                    load_p8_locked_label_download_transaction_descriptor(
                        transaction_descriptor
                    )
                )
        except Exception:
            raise DownloadError(
                "aihub538_p8_label_transaction_required"
            ) from None
        if (
            transaction.output_directory_device != _identity.device
            or transaction.output_directory_inode != _identity.inode
        ):
            raise DownloadError(
                "aihub538_p8_label_output_directory_changed"
            )
        archive_exists = _entry_exists_at(directory_descriptor, "download.tar")
        receipt_exists = _entry_exists_at(
            directory_descriptor, "download.receipt.json"
        )
        if receipt_exists and not archive_exists:
            raise DownloadError("p8_label_completed_pair_incomplete")
        if archive_exists:
            if receipt_exists:
                raise DownloadError("completed_download_already_exists")
            completed, archive_sha256 = _recover_p8_archive_receipt(
                directory_descriptor=directory_descriptor,
                dataset_key=arguments.dataset_key,
                file_keys=arguments.file_keys,
                maximum_total_bytes=maximum_label_download_bytes,
            )
            assert_authorization_stable()
            print(
                f"download_complete bytes={completed} sha256={archive_sha256}"
            )
            return arguments.destination / "download.tar"

        partial_descriptor, partial_identity = _open_p8_partial_bound(
            directory_descriptor,
            maximum_total_bytes=maximum_label_download_bytes,
        )
        offset = partial_identity[5]
        url = (
            "https://api.aihub.or.kr/down/0.6/"
            f"{arguments.dataset_key}.do?fileSn={arguments.file_keys}"
        )
        headers = {"apikey": api_key}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = Request(url, headers=headers, method="GET")
        assert_authorization_stable()
        try:
            with urlopen(request, timeout=arguments.timeout_seconds) as response:
                status = int(response.status)
                _require_p8_response_framing(
                    status=status,
                    headers=response.headers,
                )
                expected_total, append = _expected_total(
                    status=status,
                    offset=offset,
                    headers=response.headers,
                )
                if (
                    expected_total is not None
                    and expected_total > maximum_label_download_bytes
                ):
                    raise DownloadError(
                        "p8_label_response_size_outside_policy"
                    )
                if offset and not append:
                    print(
                        "range_not_supported_restarting_from_zero",
                        flush=True,
                    )
                _assert_p8_partial_descriptor_binding(
                    directory_descriptor,
                    partial_descriptor,
                    expected=partial_identity,
                    maximum_total_bytes=maximum_label_download_bytes,
                )
                if append:
                    if os.lseek(partial_descriptor, 0, os.SEEK_END) != offset:
                        raise DownloadError("p8_label_partial_changed")
                    initial_bytes = offset
                else:
                    os.ftruncate(partial_descriptor, 0)
                    os.lseek(partial_descriptor, 0, os.SEEK_SET)
                    initial_bytes = 0
                completed = _stream_response_descriptor(
                    response,
                    descriptor=partial_descriptor,
                    initial_bytes=initial_bytes,
                    expected_total=expected_total,
                    maximum_total_bytes=maximum_label_download_bytes,
                )
        except HTTPError as error:
            if not _p8_partial_already_complete_from_416(
                error,
                offset=offset,
            ):
                raise DownloadError(f"http_status_{error.code}") from None
            _assert_p8_partial_descriptor_binding(
                directory_descriptor,
                partial_descriptor,
                expected=partial_identity,
                maximum_total_bytes=maximum_label_download_bytes,
            )
            completed = offset
        except DownloadError:
            raise
        except (TimeoutError, URLError, HTTPException, OSError):
            raise DownloadError(
                "network_or_storage_failure_partial_preserved"
            ) from None

        assert_authorization_stable()
        completed_identity = _assert_p8_partial_descriptor_binding(
            directory_descriptor,
            partial_descriptor,
            maximum_total_bytes=maximum_label_download_bytes,
        )
        if completed_identity[5] != completed or completed <= 0:
            raise DownloadError("p8_label_archive_size_changed")
        try:
            os.link(
                "download.tar.partial",
                "download.tar",
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            linked_metadata = os.stat(
                "download.tar",
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            partial_metadata = os.fstat(partial_descriptor)
            if (
                linked_metadata.st_dev,
                linked_metadata.st_ino,
                linked_metadata.st_size,
                partial_metadata.st_nlink,
            ) != (
                partial_metadata.st_dev,
                partial_metadata.st_ino,
                partial_metadata.st_size,
                2,
            ):
                raise DownloadError("p8_label_archive_finalize_failed")
            os.unlink("download.tar.partial", dir_fd=directory_descriptor)
            os.fsync(directory_descriptor)
        except FileExistsError:
            raise DownloadError("completed_download_already_exists") from None
        except OSError:
            raise DownloadError("p8_label_archive_finalize_failed") from None

        try:
            archive_identity = _assert_p8_archive_descriptor_binding(
                directory_descriptor,
                partial_descriptor,
            )
            if archive_identity[5] != completed:
                raise DownloadError("p8_label_archive_size_changed")
            archive_sha256 = _sha256_descriptor(partial_descriptor)
            if _assert_p8_archive_descriptor_binding(
                directory_descriptor,
                partial_descriptor,
            ) != archive_identity:
                raise DownloadError("p8_label_archive_changed")
        finally:
            os.close(partial_descriptor)
            partial_descriptor = -1
        _write_receipt_at(
            directory_descriptor=directory_descriptor,
            dataset_key=arguments.dataset_key,
            file_keys=arguments.file_keys,
            archive_size_bytes=completed,
            archive_sha256=archive_sha256,
        )
        assert_authorization_stable()
        print(
            f"download_complete bytes={completed} sha256={archive_sha256}"
        )
        return arguments.destination / "download.tar"
    finally:
        try:
            if partial_descriptor >= 0:
                os.close(partial_descriptor)
        except (OSError, UnboundLocalError):
            pass
        os.close(directory_descriptor)


def _p8_partial_size(directory_descriptor: int) -> int:
    descriptor: int | None = None
    try:
        descriptor = _open_existing_p8_partial(directory_descriptor)
    except FileNotFoundError:
        return 0
    try:
        return _assert_p8_partial_descriptor_binding(
            directory_descriptor,
            descriptor,
        )[5]
    finally:
        os.close(descriptor)


def _p8_partial_already_complete_from_416(
    error: HTTPError,
    *,
    offset: int,
) -> bool:
    if error.code != 416 or offset <= 0:
        return False
    try:
        content_range = error.headers.get("Content-Range")
    except AttributeError:
        return False
    match = _UNSATISFIED_CONTENT_RANGE.fullmatch(content_range or "")
    return match is not None and int(match.group(1)) == offset


def _open_existing_p8_partial(directory_descriptor: int) -> int:
    flags = (
        os.O_RDWR
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        return os.open(
            "download.tar.partial",
            flags,
            dir_fd=directory_descriptor,
        )
    except FileNotFoundError:
        raise
    except OSError:
        raise DownloadError("p8_label_partial_invalid") from None


def _open_p8_partial_bound(
    directory_descriptor: int,
    *,
    maximum_total_bytes: int = _P8_MAXIMUM_LABEL_DOWNLOAD_BYTES,
) -> tuple[int, tuple[int, int, int, int, int, int, int, int]]:
    try:
        descriptor = _open_existing_p8_partial(directory_descriptor)
    except FileNotFoundError:
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(
                "download.tar.partial",
                flags,
                0o600,
                dir_fd=directory_descriptor,
            )
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
            os.fsync(directory_descriptor)
        except OSError:
            try:
                os.close(descriptor)
            except (OSError, UnboundLocalError):
                pass
            raise DownloadError("p8_label_partial_create_failed") from None
    try:
        identity = _assert_p8_partial_descriptor_binding(
            directory_descriptor,
            descriptor,
            maximum_total_bytes=maximum_total_bytes,
        )
        return descriptor, identity
    except Exception:
        os.close(descriptor)
        raise


def _assert_p8_partial_descriptor_binding(
    directory_descriptor: int,
    descriptor: int,
    *,
    expected: tuple[int, int, int, int, int, int, int, int] | None = None,
    maximum_total_bytes: int = _P8_MAXIMUM_LABEL_DOWNLOAD_BYTES,
) -> tuple[int, int, int, int, int, int, int, int]:
    try:
        metadata = os.fstat(descriptor)
        named = os.stat(
            "download.tar.partial",
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except OSError:
        raise DownloadError("p8_label_partial_invalid") from None
    identity = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )
    named_identity = (
        named.st_dev,
        named.st_ino,
        named.st_mode,
        named.st_uid,
        named.st_nlink,
        named.st_size,
        named.st_mtime_ns,
        named.st_ctime_ns,
    )
    if (
        identity != named_identity
        or (expected is not None and identity != expected)
        or not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or metadata.st_size < 0
        or metadata.st_size > maximum_total_bytes
    ):
        raise DownloadError("p8_label_partial_invalid")
    return identity


def _stream_response_descriptor(
    response: BinaryIO,
    *,
    descriptor: int,
    initial_bytes: int,
    expected_total: int | None,
    maximum_total_bytes: int,
) -> int:
    completed = initial_bytes
    next_progress = ((completed // _PROGRESS_BYTES) + 1) * _PROGRESS_BYTES
    while True:
        chunk = response.read(_CHUNK_BYTES)
        if not chunk:
            break
        completed += len(chunk)
        if completed > maximum_total_bytes:
            raise DownloadError("p8_label_response_size_outside_policy")
        _write_all(descriptor, chunk)
        if completed >= next_progress:
            print(f"downloaded_bytes={completed}", flush=True)
            next_progress = (
                (completed // _PROGRESS_BYTES) + 1
            ) * _PROGRESS_BYTES
    os.fsync(descriptor)
    if expected_total is not None and completed != expected_total:
        raise DownloadError("response_truncated_partial_preserved")
    return completed


def _open_p8_regular_at(
    directory_descriptor: int,
    filename: str,
    *,
    flags: int,
    require_mode: int,
    require_nonempty: bool,
    allowed_link_counts: tuple[int, ...] = (1,),
) -> int:
    try:
        descriptor = os.open(
            filename,
            flags | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_descriptor,
        )
        metadata = os.fstat(descriptor)
    except OSError:
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass
        raise DownloadError("p8_label_output_member_invalid") from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != require_mode
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink not in allowed_link_counts
        or (require_nonempty and metadata.st_size <= 0)
    ):
        os.close(descriptor)
        raise DownloadError("p8_label_output_member_invalid")
    return descriptor


def _assert_p8_archive_descriptor_binding(
    directory_descriptor: int,
    descriptor: int,
) -> tuple[int, int, int, int, int, int, int, int]:
    try:
        metadata = os.fstat(descriptor)
        named = os.stat(
            "download.tar",
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except OSError:
        raise DownloadError("p8_label_archive_invalid") from None
    identity = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )
    named_identity = (
        named.st_dev,
        named.st_ino,
        named.st_mode,
        named.st_uid,
        named.st_nlink,
        named.st_size,
        named.st_mtime_ns,
        named.st_ctime_ns,
    )
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or identity != named_identity
    ):
        raise DownloadError("p8_label_archive_invalid")
    return identity


def _write_receipt_at(
    *,
    directory_descriptor: int,
    dataset_key: str,
    file_keys: str,
    archive_size_bytes: int,
    archive_sha256: str,
) -> None:
    receipt = {
        "schema_version": _SCHEMA_VERSION,
        "dataset_key": dataset_key,
        "file_keys": file_keys.split(","),
        "archive_size_bytes": archive_size_bytes,
        "archive_sha256": archive_sha256,
        "download_completed_unix_seconds": int(time.time()),
        "credential_persisted": False,
        "archive_extracted": False,
    }
    payload = (
        json.dumps(receipt, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    pending_filename = "download.receipt.json.pending"
    final_filename = "download.receipt.json"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    pending_already_exists = False
    try:
        descriptor = os.open(
            pending_filename,
            flags,
            0o600,
            dir_fd=directory_descriptor,
        )
    except FileExistsError:
        if _remove_abandoned_p8_receipt_pending(
            directory_descriptor,
            pending_filename=pending_filename,
            final_filename=final_filename,
        ):
            try:
                descriptor = os.open(
                    pending_filename,
                    flags,
                    0o600,
                    dir_fd=directory_descriptor,
                )
            except OSError:
                raise DownloadError(
                    "p8_label_receipt_create_failed"
                ) from None
        else:
            pending_already_exists = True
    except OSError:
        raise DownloadError("p8_label_receipt_create_failed") from None
    if not pending_already_exists:
        assert descriptor is not None
        try:
            _write_all(descriptor, payload)
            os.fchmod(descriptor, 0o400)
            os.fsync(descriptor)
        except OSError:
            raise DownloadError("p8_label_receipt_write_failed") from None
        finally:
            os.close(descriptor)
    pending_descriptor: int | None = None
    try:
        pending_descriptor = _open_p8_regular_at(
            directory_descriptor,
            pending_filename,
            flags=os.O_RDONLY,
            require_mode=0o400,
            require_nonempty=True,
        )
        _validate_pending_p8_receipt(
            pending_descriptor,
            dataset_key=dataset_key,
            file_keys=file_keys,
            archive_size_bytes=archive_size_bytes,
            archive_sha256=archive_sha256,
        )
        os.fsync(pending_descriptor)
        _assert_p8_receipt_pending_binding(
            directory_descriptor,
            pending_descriptor=pending_descriptor,
            pending_filename=pending_filename,
            final_filename=final_filename,
            linked=False,
        )
        os.link(
            pending_filename,
            final_filename,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        _assert_p8_receipt_pending_binding(
            directory_descriptor,
            pending_descriptor=pending_descriptor,
            pending_filename=pending_filename,
            final_filename=final_filename,
            linked=True,
        )
        os.fsync(directory_descriptor)
        os.unlink(pending_filename, dir_fd=directory_descriptor)
        os.fsync(directory_descriptor)
    except FileExistsError:
        raise DownloadError("completed_download_already_exists") from None
    except DownloadError:
        raise
    except OSError:
        raise DownloadError("p8_label_receipt_publish_failed") from None
    finally:
        if pending_descriptor is not None:
            os.close(pending_descriptor)


def _validate_pending_p8_receipt(
    descriptor: int,
    *,
    dataset_key: str,
    file_keys: str,
    archive_size_bytes: int,
    archive_sha256: str,
) -> None:
    try:
        metadata = os.fstat(descriptor)
        if metadata.st_size > 256 * 1024:
            raise ValueError
        payload = bytearray()
        os.lseek(descriptor, 0, os.SEEK_SET)
        while len(payload) <= 256 * 1024:
            chunk = os.read(descriptor, min(64 * 1024, 256 * 1024 + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)

        def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
            document: dict[str, object] = {}
            for key, value in pairs:
                if key in document:
                    raise ValueError
                document[key] = value
            return document

        document = json.loads(
            bytes(payload),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        if not isinstance(document, dict):
            raise ValueError
        completed_at = document.get("download_completed_unix_seconds")
        if (
            len(payload) != metadata.st_size
            or set(document) != {
                "schema_version",
                "dataset_key",
                "file_keys",
                "archive_size_bytes",
                "archive_sha256",
                "download_completed_unix_seconds",
                "credential_persisted",
                "archive_extracted",
            }
            or document["schema_version"] != _SCHEMA_VERSION
            or document["dataset_key"] != dataset_key
            or document["file_keys"] != file_keys.split(",")
            or document["archive_size_bytes"] != archive_size_bytes
            or document["archive_sha256"] != archive_sha256
            or isinstance(completed_at, bool)
            or not isinstance(completed_at, int)
            or completed_at < 0
            or document["credential_persisted"] is not False
            or document["archive_extracted"] is not False
        ):
            raise ValueError
        os.lseek(descriptor, 0, os.SEEK_SET)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise DownloadError("p8_label_receipt_pending_invalid") from None


def _remove_abandoned_p8_receipt_pending(
    directory_descriptor: int,
    *,
    pending_filename: str,
    final_filename: str,
) -> bool:
    try:
        pending = os.stat(
            pending_filename,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return False
    except OSError:
        raise DownloadError("p8_label_receipt_pending_invalid") from None
    if _entry_exists_at(directory_descriptor, final_filename):
        return False
    if (
        not stat.S_ISREG(pending.st_mode)
        or stat.S_IMODE(pending.st_mode) != 0o600
        or pending.st_uid != os.geteuid()
        or pending.st_nlink != 1
    ):
        return False
    try:
        os.unlink(pending_filename, dir_fd=directory_descriptor)
        os.fsync(directory_descriptor)
    except OSError:
        raise DownloadError(
            "p8_label_receipt_pending_cleanup_failed"
        ) from None
    return True


def _assert_p8_receipt_pending_binding(
    directory_descriptor: int,
    *,
    pending_descriptor: int,
    pending_filename: str,
    final_filename: str,
    linked: bool,
) -> None:
    try:
        descriptor = os.fstat(pending_descriptor)
        pending = os.stat(
            pending_filename,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        final = (
            os.stat(
                final_filename,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if linked
            else None
        )
    except OSError:
        raise DownloadError("p8_label_receipt_pending_changed") from None
    expected_links = 2 if linked else 1
    expected = (
        descriptor.st_dev,
        descriptor.st_ino,
        descriptor.st_mode,
        descriptor.st_uid,
        descriptor.st_nlink,
        descriptor.st_size,
    )
    if (
        not stat.S_ISREG(descriptor.st_mode)
        or stat.S_IMODE(descriptor.st_mode) != 0o400
        or descriptor.st_uid != os.geteuid()
        or descriptor.st_nlink != expected_links
        or expected
        != (
            pending.st_dev,
            pending.st_ino,
            pending.st_mode,
            pending.st_uid,
            pending.st_nlink,
            pending.st_size,
        )
        or (
            linked
            and final is not None
            and expected
            != (
                final.st_dev,
                final.st_ino,
                final.st_mode,
                final.st_uid,
                final.st_nlink,
                final.st_size,
            )
        )
    ):
        raise DownloadError("p8_label_receipt_pending_changed")


def _recover_p8_archive_receipt(
    *,
    directory_descriptor: int,
    dataset_key: str,
    file_keys: str,
    maximum_total_bytes: int = _P8_MAXIMUM_LABEL_DOWNLOAD_BYTES,
) -> tuple[int, str]:
    """Finish the receipt-only crash window without another network request."""

    archive_descriptor = _open_p8_regular_at(
        directory_descriptor,
        "download.tar",
        flags=os.O_RDONLY,
        require_mode=0o600,
        require_nonempty=True,
        allowed_link_counts=(1, 2),
    )
    partial_descriptor: int | None = None
    try:
        archive_metadata = os.fstat(archive_descriptor)
        if (
            archive_metadata.st_size > maximum_total_bytes
            or archive_metadata.st_nlink not in {1, 2}
        ):
            raise DownloadError("p8_label_archive_invalid")
        if _entry_exists_at(directory_descriptor, "download.tar.partial"):
            try:
                partial_descriptor = _open_existing_p8_partial(
                    directory_descriptor
                )
                partial_metadata = os.fstat(partial_descriptor)
            except FileNotFoundError:
                raise DownloadError("p8_label_partial_invalid") from None
            if (
                partial_metadata.st_dev,
                partial_metadata.st_ino,
                partial_metadata.st_size,
                partial_metadata.st_nlink,
            ) != (
                archive_metadata.st_dev,
                archive_metadata.st_ino,
                archive_metadata.st_size,
                2,
            ):
                raise DownloadError("p8_label_partial_archive_mismatch")
            os.unlink("download.tar.partial", dir_fd=directory_descriptor)
            os.fsync(directory_descriptor)
        elif archive_metadata.st_nlink != 1:
            raise DownloadError("p8_label_archive_link_count_invalid")
        archive_identity = _assert_p8_archive_descriptor_binding(
            directory_descriptor,
            archive_descriptor,
        )
        completed = archive_identity[5]
        archive_sha256 = _sha256_descriptor(archive_descriptor)
        if _assert_p8_archive_descriptor_binding(
            directory_descriptor,
            archive_descriptor,
        ) != archive_identity:
            raise DownloadError("p8_label_archive_changed")
    finally:
        if partial_descriptor is not None:
            os.close(partial_descriptor)
        os.close(archive_descriptor)
    _write_receipt_at(
        directory_descriptor=directory_descriptor,
        dataset_key=dataset_key,
        file_keys=file_keys,
        archive_size_bytes=completed,
        archive_sha256=archive_sha256,
    )
    return completed, archive_sha256


def _entry_exists_at(directory_descriptor: int, filename: str) -> bool:
    try:
        os.stat(filename, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError:
        raise DownloadError("p8_label_output_inspection_failed") from None
    return True


def _descriptor_identity(descriptor: int) -> tuple[int, int, int, int, int]:
    metadata = os.fstat(descriptor)
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _sha256_descriptor(descriptor: int) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while chunk := os.read(descriptor, _CHUNK_BYTES):
        digest.update(chunk)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return digest.hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError
        view = view[written:]


def main() -> int:
    try:
        download(_arguments())
    except DownloadError as error:
        print(f"AI Hub download failed: {error}", file=sys.stderr)
        return 65
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
