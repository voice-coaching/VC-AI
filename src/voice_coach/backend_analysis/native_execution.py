"""Separate historical read identities from approved Native execution identities."""
from __future__ import annotations

from .canonical_bridge import _require as require
from .canonical_harness import SIX_DIMENSION, SIX_DIMENSION_INTERNAL, SIX_DIMENSION_V2, SIX_DIMENSION_INTERNAL_V2, SIX_DIMENSION_V3, SIX_DIMENSION_INTERNAL_V3
from .canonical_harness import DLPC_LUNA, DLPC_LUNA_NONE, DLPC_MFA_TRACE, DLPC_MFA_PREPARED, DLPC_MFA_DISAMBIGUATION, DLPC_SEUNGUN_SERVER, DLPC_PIPELINE_LATENCY, DLPC_INTERNAL_BOUNDED
from .canonical_harness import DLPC_PIPELINE_SOL61_LIGHT

# Execution identity only: prevent an old release from entering new analysis.
# GPT judges the single rubric; no calibration approval is consulted here.
APPROVED_NATIVE_EXECUTIONS: frozenset[tuple[str, str]] = frozenset({
    (SIX_DIMENSION_V2.core_sha256, SIX_DIMENSION_V2.manifest_sha256),
    (SIX_DIMENSION_INTERNAL_V2.core_sha256, SIX_DIMENSION_INTERNAL_V2.manifest_sha256),
    (SIX_DIMENSION_V3.core_sha256, SIX_DIMENSION_V3.manifest_sha256),
    (SIX_DIMENSION_INTERNAL_V3.core_sha256, SIX_DIMENSION_INTERNAL_V3.manifest_sha256),
    (SIX_DIMENSION.core_sha256, SIX_DIMENSION.manifest_sha256),
    (SIX_DIMENSION_INTERNAL.core_sha256, SIX_DIMENSION_INTERNAL.manifest_sha256),
    (
    # User-selected Sol 6.1 low; prepared numerical core and compact prompt retained.
    DLPC_PIPELINE_SOL61_LIGHT.core_sha256, DLPC_PIPELINE_SOL61_LIGHT.manifest_sha256,
), (
    # Internal lane diagnostic bounds only; same numerical and provider policy.
    DLPC_INTERNAL_BOUNDED.core_sha256, DLPC_INTERNAL_BOUNDED.manifest_sha256,
), (
    # Prepared pipeline, full-input-keyed FST copies and compact Luna output.
    DLPC_PIPELINE_LATENCY.core_sha256, DLPC_PIPELINE_LATENCY.manifest_sha256,
), (
    # J406 Core-row disambiguation, four-thread MFA and unchanged GOP overlap.
    DLPC_SEUNGUN_SERVER.core_sha256, DLPC_SEUNGUN_SERVER.manifest_sha256,
), (
    # Fixed-source equivalent prefix reduction; database and call ordering preserved.
    DLPC_MFA_DISAMBIGUATION.core_sha256, DLPC_MFA_DISAMBIGUATION.manifest_sha256,
), (
    # Prepared dependencies only; the numerical path remains single-threaded.
    DLPC_MFA_PREPARED.core_sha256, DLPC_MFA_PREPARED.manifest_sha256,
), (
    # M0 observations only; no alignment algorithm or resource setting change.
    DLPC_MFA_TRACE.core_sha256, DLPC_MFA_TRACE.manifest_sha256,
), (
    # Only P1 is activated; compact/cached candidates remain read-only.
    DLPC_LUNA_NONE.core_sha256, DLPC_LUNA_NONE.manifest_sha256,
), (
    # User-authorized model-only migration; core and feedback policy are unchanged.
    DLPC_LUNA.core_sha256, DLPC_LUNA.manifest_sha256,
), (
    "818a3c16cba39dfcfa9f1083865db7f122a3eba40bc332d85256da94d1d6fb1e",
    "f90440b764f78f32c989dc0785f41262397cc22e1d1ca48c40361189b81f86d5",
), (
    'f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391',
    '1612539f82a0b6e2c5c0b3551440b8094ca979bd8ab5d898386713bd0d1f54b7',
), (
    'f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391',
    'def65f68a849a2452b0e4dde600f0ef85d99beaa97123d30d97262653ebf8e77',
), (
    # 2026-10-08 user-authorized prompt-only rollout; the admitted GOP core is unchanged.
    'f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391',
    '3e430635a4d714ead5d8e08352e41ff9a2b06c893d73af7f018fb43b4c37f61c',
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
