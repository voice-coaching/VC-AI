"""Separate historical read identities from approved Native execution identities."""
from __future__ import annotations

from .canonical_bridge import _require as require

# Operator-authorized analysis releases. This does not approve a scoring policy
# or assert inference/quality QA. Scoring checks its own calibrated evidence.
APPROVED_NATIVE_EXECUTIONS: frozenset[tuple[str, str]] = frozenset({(
    "818a3c16cba39dfcfa9f1083865db7f122a3eba40bc332d85256da94d1d6fb1e",
    "f90440b764f78f32c989dc0785f41262397cc22e1d1ca48c40361189b81f86d5",
)})


def require_native_execution(artifacts):
    identity = artifacts.identity
    require(identity.native, "RETIRED_CORE_EXECUTION_FORBIDDEN")
    require(identity.gop_grading, "RETIRED_SCORING_EXECUTION_FORBIDDEN")
    require((identity.core_sha256, identity.manifest_sha256) in APPROVED_NATIVE_EXECUTIONS,
            "NATIVE_RELEASE_DEVELOPER_QA_REQUIRED")


def admitted_binding(request, *, core_sha256, prepared_sha256):
    # Historical artifact reads only. The active GOP path has no scoring adapter.
    from .native_phone_assessment import sha256_json
    from .native_phone_contract import TOKENS
    return dict(requestId=str(request.event_id), executionId=str(request.execution_id),
                sourceAudioSha256=request.audio_sha256, preparedWavSha256=prepared_sha256,
                scriptSha256=request.script_sha256, coreManifestSha256=core_sha256,
                vocabularySha256=sha256_json({token: i for i, token in enumerate(TOKENS)}))
