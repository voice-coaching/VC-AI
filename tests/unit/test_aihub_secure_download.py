"""Tests for the credential-safe resumable AI Hub downloader."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import stat
import sys
from urllib.request import Request

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "download_aihub_secure.py"
SPEC = importlib.util.spec_from_file_location("download_aihub_secure", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_response_range_requires_exact_resume_offset() -> None:
    headers = {
        "Content-Length": "10",
        "Content-Range": "bytes 5-14/15",
    }
    assert MODULE._expected_total(status=206, offset=5, headers=headers) == (
        15,
        True,
    )

    with pytest.raises(MODULE.DownloadError, match="content_range"):
        MODULE._expected_total(status=206, offset=4, headers=headers)


def test_full_response_restarts_instead_of_appending() -> None:
    assert MODULE._expected_total(
        status=200,
        offset=99,
        headers={"Content-Length": "123"},
    ) == (123, False)

    assert MODULE._expected_total(
        status=200,
        offset=99,
        headers={},
    ) == (None, False)


def test_p8_full_response_requires_unambiguous_length_or_chunked_framing() -> None:
    MODULE._require_p8_response_framing(
        status=200,
        headers={"Content-Length": "123"},
    )
    MODULE._require_p8_response_framing(
        status=200,
        headers={"Transfer-Encoding": "chunked"},
    )
    MODULE._require_p8_response_framing(status=206, headers={})

    with pytest.raises(MODULE.DownloadError, match="framing_required"):
        MODULE._require_p8_response_framing(status=200, headers={})
    with pytest.raises(MODULE.DownloadError, match="framing_invalid"):
        MODULE._require_p8_response_framing(
            status=200,
            headers={
                "Content-Length": "123",
                "Transfer-Encoding": "chunked",
            },
        )
    with pytest.raises(MODULE.DownloadError, match="framing_invalid"):
        MODULE._require_p8_response_framing(
            status=200,
            headers={"Transfer-Encoding": "gzip, chunked"},
        )


def test_cli_source_does_not_forward_secret_as_argument() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "import subprocess" not in source
    assert 'os.environ.get("AIHUB_API_KEY"' in source
    assert "headers = {\"apikey\": api_key}" in source


def _redirect(request: Request, location: str) -> Request:
    redirected = MODULE._PinnedOriginRedirectHandler().redirect_request(
        request,
        object(),
        302,
        "Found",
        {},
        location,
    )
    assert redirected is not None
    return redirected


def test_api_to_stream_redirect_strips_every_secret_and_preserves_range() -> None:
    request = Request(
        "https://api.aihub.or.kr/down/0.6/538.do?fileSn=54728",
        headers={
            "apikey": "fixture-secret",
            "Range": "bytes=123-",
            "Authorization": "Bearer must-not-forward",
            "Cookie": "session=must-not-forward",
        },
        method="GET",
    )
    signed = (
        "https://stream.aihub.or.kr/download/object?"
        "X-Amz-Credential=safe-signed-credential&X-Amz-Signature=abc123"
    )
    redirected = _redirect(request, signed)

    assert redirected.full_url == signed
    assert redirected.get_method() == "GET"
    assert {
        name.lower(): value for name, value in redirected.header_items()
    } == {"range": "bytes=123-"}


@pytest.mark.parametrize(
    "location",
    (
        "http://stream.aihub.or.kr/download?signed=1",
        "https://stream.aihub.or.kr:444/download?signed=1",
        "https://user@stream.aihub.or.kr/download?signed=1",
        "https://evil.example/download?signed=1",
        "https://api.aihub.or.kr/another",
    ),
)
def test_api_redirect_allows_only_exact_https_stream_origin(
    location: str,
) -> None:
    request = Request(
        "https://api.aihub.or.kr/down/0.6/538.do",
        headers={"apikey": "fixture-secret"},
    )
    with pytest.raises(
        MODULE.DownloadError,
        match="(?:cross_origin|origin_invalid)",
    ):
        _redirect(request, location)


def test_stream_chain_stays_secret_free_and_cannot_redirect_back() -> None:
    initial = Request(
        "https://api.aihub.or.kr/down/0.6/538.do",
        headers={"apikey": "fixture-secret", "Range": "bytes=9-"},
    )
    first = _redirect(
        initial,
        "https://stream.aihub.or.kr/first?X-Amz-Signature=one",
    )
    second = _redirect(
        first,
        "https://stream.aihub.or.kr/second?X-Amz-Signature=two",
    )
    assert second.full_url.endswith("X-Amz-Signature=two")
    assert dict(second.header_items()) == {"Range": "bytes=9-"}

    with pytest.raises(MODULE.DownloadError, match="cross_origin"):
        _redirect(second, "https://api.aihub.or.kr/back")


@pytest.mark.parametrize(
    "location",
    (
        "https://stream.aihub.or.kr/object?apikey=fixture-secret",
        "https://stream.aihub.or.kr/object?signed=fixture-secret",
        "https://stream.aihub.or.kr/object?signed=fixture%2Dsecret",
        "https://stream.aihub.or.kr/object?authorization=presigned",
    ),
)
def test_redirect_rejects_header_secret_or_sensitive_query_reflection(
    location: str,
) -> None:
    request = Request(
        "https://api.aihub.or.kr/down/0.6/538.do",
        headers={"apikey": "fixture-secret"},
    )
    with pytest.raises(MODULE.DownloadError, match="secret_reflection"):
        _redirect(request, location)


def test_initial_request_requires_api_get_key_and_safe_optional_range() -> None:
    MODULE._require_initial_aihub_request(
        Request(
            "https://api.aihub.or.kr/down/0.6/538.do",
            headers={"apikey": "fixture-secret", "Range": "bytes=7-"},
            method="GET",
        )
    )
    invalid = (
        Request("https://api.aihub.or.kr/down/0.6/538.do"),
        Request(
            "https://stream.aihub.or.kr/object?signed=1",
            headers={"apikey": "fixture-secret"},
        ),
        Request(
            "https://api.aihub.or.kr/down/0.6/538.do",
            headers={
                "apikey": "fixture-secret",
                "Authorization": "must-not-exist",
            },
        ),
        Request(
            "https://api.aihub.or.kr/down/0.6/538.do",
            headers={"apikey": "fixture-secret", "Range": "bytes=bad-"},
        ),
    )
    for request in invalid:
        with pytest.raises(MODULE.DownloadError):
            MODULE._require_initial_aihub_request(request)


def test_signed_location_is_internal_only_and_never_appears_in_receipt(
    tmp_path: Path,
) -> None:
    signed_marker = "private-signed-location-marker"
    redirected = _redirect(
        Request(
            "https://api.aihub.or.kr/down/0.6/538.do",
            headers={"apikey": "fixture-secret"},
        ),
        (
            "https://stream.aihub.or.kr/object?"
            f"X-Amz-Signature={signed_marker}"
        ),
    )
    assert signed_marker in redirected.full_url

    receipt_path = tmp_path / "download.receipt.json"
    MODULE._write_receipt(
        receipt_path=receipt_path,
        dataset_key="538",
        file_keys="54728",
        archive_size_bytes=1,
        archive_sha256="a" * 64,
    )
    serialized = receipt_path.read_text(encoding="utf-8")
    assert signed_marker not in serialized
    assert "stream.aihub.or.kr" not in serialized

    reflected = (
        "https://stream.aihub.or.kr/object?"
        "signed=fixture-secret&private-location-do-not-log=1"
    )
    with pytest.raises(MODULE.DownloadError) as raised:
        _redirect(
            Request(
                "https://api.aihub.or.kr/down/0.6/538.do",
                headers={"apikey": "fixture-secret"},
            ),
            reflected,
        )
    assert "private-location-do-not-log" not in str(raised.value)


def test_fixed_aihub_opener_rejects_downgrade_before_network() -> None:
    with pytest.raises(MODULE.DownloadError, match="origin_forbidden"):
        MODULE.urlopen(
            Request("http://api.aihub.or.kr/down/0.6/538.do"),
            timeout=1,
        )


def test_completed_receipt_is_read_only_and_contains_no_credential(
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "download.receipt.json"

    MODULE._write_receipt(
        receipt_path=receipt_path,
        dataset_key="538",
        file_keys="54725,54944",
        archive_size_bytes=123,
        archive_sha256="a" * 64,
    )

    assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o400
    document = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert document["dataset_key"] == "538"
    assert document["file_keys"] == ["54725", "54944"]
    assert document["credential_persisted"] is False
    assert "apikey" not in receipt_path.read_text(encoding="utf-8").lower()
