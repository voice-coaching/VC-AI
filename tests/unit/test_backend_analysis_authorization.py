from __future__ import annotations

import base64
from datetime import UTC, datetime
import hashlib
import hmac
from pathlib import Path

import pytest

from voice_coach.backend_analysis.adapters.hmac_authorization import (
    HmacAnalysisAuthorizationAdapter,
    HmacAuthorizationSettings,
)
from voice_coach.backend_analysis.domain import (
    AnalysisAuthorizationGrant,
    AnalysisRequest,
    BackendAnalysisContractError,
)
from voice_coach.backend_analysis.ports import BackendAnalysisError


_EVENT_ID = "4adfe173-0691-4e89-b94e-a5c5c5085826"
_NOW = datetime(2026, 9, 2, tzinfo=UTC)
_SECRET = b"s" * 32


def _grant_payload(*, signature: str = "0" * 64) -> dict[str, object]:
    return {
        "grantVersion": "voice-coaching.analysis-authorization.v3",
        "keyId": "backend-test-v1",
        "requestEventId": _EVENT_ID,
        "analysisId": 35,
        "contentId": 12,
        "promptRevision": "2026-09-02T00:00:00Z",
        "scriptSha256": "a" * 64,
        "audioObjectKeySha256": "7a8ccd3755d589e1a3d59811c8e1131a975bc5d6e53469fe8356686cfcfec39a",
        "audioSha256": "d" * 64,
        "mimeType": "audio/wav",
        "fileSizeBytes": 1234,
        "durationMs": 1200,
        "learningFocus": "PRONUNCIATION",
        "consentReceiptSha256": "4bb06f8e4e3a7715d201d573d0aa423762e55dabd61a2c02278fa56cc6d294e0",
        "consentPolicyRevision": "voice-analysis-consent-v1",
        "visualObjectKeySha256": None,
        "visualSha256": None,
        "visualMimeType": None,
        "visualFileSizeBytes": None,
        "visualConsentReceiptSha256": None,
        "visualConsentPolicyRevision": None,
        "issuedAtUtc": "2026-09-02T00:00:00Z",
        "expiresAtUtc": "2026-09-02T00:05:00Z",
        "purpose": "pronunciation_coaching",
        "dataCategory": "learner_voice_recording",
        "deleteOnCompletion": True,
        "remoteEgressAllowed": False,
        "signature": signature,
    }


def _request_payload(grant: dict[str, object]) -> dict[str, object]:
    script = "연습 문장입니다."
    script_sha256 = hashlib.sha256(script.encode()).hexdigest()
    audio_key = "recordings/analysis-audio/00000000-0000-0000-0000-000000000001.wav"
    grant = dict(grant)
    grant["scriptSha256"] = script_sha256
    grant["audioObjectKeySha256"] = hashlib.sha256(audio_key.encode()).hexdigest()
    return {
        "schemaVersion": "voice-coaching.analysis-request.v4",
        "eventId": _EVENT_ID,
        "analysisId": 35,
        "contentId": 12,
        "promptRevision": "2026-09-02T00:00:00Z",
        "scriptText": script,
        "scriptSha256": script_sha256,
        "audioObjectKey": audio_key,
        "audioSha256": "d" * 64,
        "mimeType": "audio/wav",
        "fileSizeBytes": 1234,
        "durationMs": 1200,
        "learningFocus": "PRONUNCIATION",
        "visualInput": None,
        "authorizationGrant": grant,
    }


def _signed_request() -> AnalysisRequest:
    grant_payload = _grant_payload()
    request_payload = _request_payload(grant_payload)
    unsigned = AnalysisAuthorizationGrant.from_payload(request_payload["authorizationGrant"])
    request_payload["authorizationGrant"]["signature"] = hmac.new(  # type: ignore[index]
        _SECRET, unsigned.canonical_signing_input(), hashlib.sha256
    ).hexdigest()
    return AnalysisRequest.from_payload(request_payload)


def _settings(policy: str = "voice-analysis-consent-v1") -> HmacAuthorizationSettings:
    return HmacAuthorizationSettings(
        keys={"backend-test-v1": _SECRET},
        consent_policy_revision=policy,
    )


def test_java_and_python_canonical_hmac_test_vector_match() -> None:
    grant = AnalysisAuthorizationGrant.from_payload(_grant_payload())

    assert hmac.new(_SECRET, grant.canonical_signing_input(), hashlib.sha256).hexdigest() == (
        "94416ceb8712c06e9b3798867eb7e8a0e13dfb547f965090aae66da50a1dc353"
    )


def test_java_and_python_visual_hmac_test_vector_match() -> None:
    payload = _grant_payload()
    visual_key = "recordings/analysis-video/00000000-0000-0000-0000-000000000001.mp4"
    payload.update(
        {
            "visualObjectKeySha256": hashlib.sha256(visual_key.encode()).hexdigest(),
            "visualSha256": "f" * 64,
            "visualMimeType": "video/mp4",
            "visualFileSizeBytes": 4567,
            "visualConsentReceiptSha256": "e" * 64,
            "visualConsentPolicyRevision": "voice-video-processing-consent-v1",
        }
    )
    grant = AnalysisAuthorizationGrant.from_payload(payload)

    assert hmac.new(_SECRET, grant.canonical_signing_input(), hashlib.sha256).hexdigest() == (
        "dc132de88e2cba1bb2e1a6916d7bfc30525eafb77d8b2b943b3a4232c8906208"
    )


def test_verifies_signed_bound_grant() -> None:
    authorization = HmacAnalysisAuthorizationAdapter(
        _settings(), clock=lambda: _NOW
    ).authorize(_signed_request())

    assert authorization.policy_revision == "voice-analysis-consent-v1"
    assert authorization.delete_on_completion is True
    assert authorization.remote_egress_allowed is False


def test_rejects_an_unsafe_policy_identifier_at_composition_time() -> None:
    with pytest.raises(
        ValueError, match="analysis_authorization_configuration_invalid"
    ):
        _settings("voice analysis latest")


def test_rejects_tampered_signature_and_stale_policy() -> None:
    request = _signed_request()
    tampered_payload = _request_payload(_grant_payload(signature="f" * 64))
    tampered = AnalysisRequest.from_payload(tampered_payload)

    with pytest.raises(BackendAnalysisError) as signature_error:
        HmacAnalysisAuthorizationAdapter(_settings(), clock=lambda: _NOW).authorize(tampered)
    assert signature_error.value.code == "consent_evidence_unavailable"

    with pytest.raises(BackendAnalysisError) as policy_error:
        HmacAnalysisAuthorizationAdapter(
            _settings("voice-analysis-consent-v2"), clock=lambda: _NOW
        ).authorize(request)
    assert policy_error.value.code == "consent_policy_not_permitted"


def test_v1_request_is_explicitly_unsupported() -> None:
    with pytest.raises(BackendAnalysisContractError) as error:
        AnalysisRequest.from_payload({"schemaVersion": "voice-coaching.analysis-request.v1"})

    assert error.value.code == "analysis_request_schema_unsupported"


def test_settings_decode_rotation_keyring_without_exposing_secrets() -> None:
    settings = HmacAuthorizationSettings.from_environment(
        {
            "AI_ANALYSIS_AUTHORIZATION_KEYS_JSON": (
                '{"backend-test-v1":"' + base64.b64encode(_SECRET).decode() + '"}'
            ),
            "AI_ANALYSIS_CONSENT_POLICY_REVISION": "voice-analysis-consent-v1",
        }
    )

    assert set(settings.keys) == {"backend-test-v1"}


def test_settings_read_rotation_keyring_from_secret_file(tmp_path: Path) -> None:
    keyring = tmp_path / "authorization-keyring.json"
    keyring.write_text(
        '{"backend-test-v1":"' + base64.b64encode(_SECRET).decode() + '"}\n',
        encoding="utf-8",
    )

    settings = HmacAuthorizationSettings.from_environment(
        {
            "AI_ANALYSIS_AUTHORIZATION_KEYS_FILE": str(keyring),
            "AI_ANALYSIS_CONSENT_POLICY_REVISION": "voice-analysis-consent-v1",
        }
    )

    assert set(settings.keys) == {"backend-test-v1"}
