"""Verify backend-issued, short-lived HMAC authorization grants."""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
import os
import re

from ..domain import AnalysisRequest
from ..ports import AnalysisAuthorization, BackendAnalysisError


_KEY_ID = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


class HmacAuthorizationSettings:
    """Keyring and policy allowlist without endpoint or user information."""

    def __init__(
        self,
        *,
        keys: Mapping[str, bytes],
        consent_policy_revision: str,
        maximum_grant_ttl_seconds: int = 600,
        allowed_clock_skew_seconds: int = 30,
    ) -> None:
        copied = dict(keys)
        if (
            not copied
            or any(_KEY_ID.fullmatch(key_id) is None for key_id in copied)
            or any(not isinstance(secret, bytes) or len(secret) < 32 for secret in copied.values())
            or not consent_policy_revision
            or len(consent_policy_revision) > 100
            or not 1 <= maximum_grant_ttl_seconds <= 600
            or not 0 <= allowed_clock_skew_seconds <= 60
        ):
            raise ValueError("analysis_authorization_configuration_invalid")
        self.keys = copied
        self.consent_policy_revision = consent_policy_revision
        self.maximum_grant_ttl = timedelta(seconds=maximum_grant_ttl_seconds)
        self.allowed_clock_skew = timedelta(seconds=allowed_clock_skew_seconds)

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "HmacAuthorizationSettings":
        env = os.environ if environment is None else environment
        try:
            encoded_keys = json.loads(env["AI_ANALYSIS_AUTHORIZATION_KEYS_JSON"])
            if not isinstance(encoded_keys, dict):
                raise ValueError
            keys = {
                key_id: base64.b64decode(value, validate=True)
                for key_id, value in encoded_keys.items()
                if isinstance(key_id, str) and isinstance(value, str)
            }
            if len(keys) != len(encoded_keys):
                raise ValueError
            return cls(
                keys=keys,
                consent_policy_revision=env["AI_ANALYSIS_CONSENT_POLICY_REVISION"],
                maximum_grant_ttl_seconds=int(
                    env.get("AI_ANALYSIS_AUTHORIZATION_MAX_TTL_SECONDS", "600")
                ),
                allowed_clock_skew_seconds=int(
                    env.get("AI_ANALYSIS_AUTHORIZATION_CLOCK_SKEW_SECONDS", "30")
                ),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise ValueError("analysis_authorization_configuration_invalid") from None


class HmacAnalysisAuthorizationAdapter:
    """Fail closed unless signature, time window, policy, and binding all pass."""

    def __init__(
        self,
        settings: HmacAuthorizationSettings,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(tz=UTC))

    def authorize(self, request: AnalysisRequest) -> AnalysisAuthorization:
        grant = request.authorization_grant
        secret = self._settings.keys.get(grant.key_id)
        if secret is None:
            raise BackendAnalysisError(
                "consent_evidence_unavailable",
                "분석 처리 권한을 확인할 수 없습니다.",
            )
        expected = hmac.new(
            secret, grant.canonical_signing_input(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, grant.signature):
            raise BackendAnalysisError(
                "consent_evidence_unavailable",
                "분석 처리 권한을 확인할 수 없습니다.",
            )
        now = self._clock()
        if now.tzinfo is None:
            raise BackendAnalysisError(
                "consent_evidence_unavailable",
                "분석 처리 권한을 확인할 수 없습니다.",
            )
        if (
            grant.expires_at - grant.issued_at > self._settings.maximum_grant_ttl
            or grant.issued_at > now + self._settings.allowed_clock_skew
            or grant.expires_at <= now
        ):
            raise BackendAnalysisError(
                "consent_evidence_expired",
                "분석 처리 권한이 만료되었습니다.",
            )
        if grant.consent_policy_revision != self._settings.consent_policy_revision:
            raise BackendAnalysisError(
                "consent_policy_not_permitted",
                "현재 처리 정책으로는 분석할 수 없습니다.",
            )
        return AnalysisAuthorization(
            receipt_sha256=grant.consent_receipt_sha256,
            policy_revision=grant.consent_policy_revision,
            expires_at=grant.expires_at,
            delete_on_completion=grant.delete_on_completion,
            remote_egress_allowed=grant.remote_egress_allowed,
        )


__all__ = [
    "HmacAnalysisAuthorizationAdapter",
    "HmacAuthorizationSettings",
]
