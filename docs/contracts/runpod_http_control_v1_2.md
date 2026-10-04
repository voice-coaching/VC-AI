# Backend readiness v2 and evidence registration contracts

## Deployed update — 2026-10-02

Backend and RunPod now expose actual READY/NOT_READY states; admission was observed
enabled at 11:39–11:41 KST. See the [deployment record](../canonical-integration/rollout-execution-20261002.md#제한-v4-접수-활성화).
`workerReadinessV2` conditionally advertises legacy-only or legacy plus canonical v4
support. `canonicalExecutorReadiness` describes the authenticated RunPod
`/health/canonical` response, available through the 8082 gateway. Both sides compare
nine exact schema digests; failed or stale readiness closes canonical admission.
The new control-v1.2 SHA-256 is
`7acd63e1a0ff9aa68bd2d86c2c5ec37827df10b85b186efac08c1d338287e7a4`.
Frozen R/S7, result-v4 content and legacy v1.1 message semantics are unchanged.
This is deployment/readiness evidence, not inference or callback E2E acceptance.

The following sections retain their original inspection dates and implementation
stages; references to disabled readiness or local-only deployment are historical.

Inspected 2026-09-30 in Backend worktree `canonical-v4-be-20260930`, branch
`feat/canonical-callback-v4-20260930`, base HEAD
`b48db988e7485943097acaa4c71308ef306175a8`, including uncommitted controller/DTO changes.
This is source inspection, not a deployed observation or readiness approval.

Static timestamp correction (2026-09-30): the new request-v2 and control-v1.2
schemas use `[.]` for the fractional-second separator. The reported pre-existing
local control-v1 separator escaping issue is intentionally left unchanged:
legacy deployment drift is outside this slice, and Backend v1 was inspected
separately. Earlier JSON/reference/pattern-compilation checks did not establish
timestamp matching semantics. Corrected new schemas are copied byte-for-byte
to Backend; this change does not alter the legacy contract or readiness gate.

The shared [control v1.2 schema](runpod_http_control_v1_2.schema.json) uses local
references and root `$defs/workerReadinessV2`. It describes only the response
currently constructed by `InternalRunPodAnalysisController.readinessV2` and
`RunPodWorkerReadinessV2ResponseDto`. All response and digest-entry properties
are required; both objects reject additional properties. No readiness field is nullable.
The 2026-10-01 implementation adds evidenceReceipt/evidenceError definitions and
separate evidence endpoints. It does not enable readiness or v4 admission.

## Canonical callback delivery and ACK-only recovery (local, not deployed)

`POST /api/internal/ai/analyses/{analysisId}/result/ack` accepts the exact
stored result-v4 bytes (1 MiB maximum) and existing internal callback Bearer.
It is a confirmation operation, not another ingestion endpoint. Authenticate
before reading the body; unknown/legacy schemas, identity conflicts and changed
bytes are rejected. No request URL, token or new execution identity is taken
from the body beyond the existing closed callback fields.

- HTTP 200 uses the existing unwrapped result ACK fields eventId, analysisId,
  requestId, executionId, status and serverTime, with status=DUPLICATE only.
  Success has Cache-Control: no-store. `$defs/resultAck` in control-v1.2
  describes this same shape using its UTC timestamp definition; v1.1 is unchanged.
- Backend requires current visible ownership/execution/worker, matching exact
  callback bytes and raw/JCS hashes in a verified APPLIED inbox, immutable result
  and current result pointer. Lease/deadline expiry alone does not invalidate an
  already applied result; cancellation/deletion/deselection/new execution does.
- No inbox/result/receipt insertion, verification scheduling, commit, heartbeat,
  inference or media/B2 work occurs. Read fencing takes the existing short
  analysis lock. The endpoint remains a reader when new callback admission is off.
- Missing inbox: 404 UNKNOWN_EXECUTION. Matching but non-APPLIED/unverified
  inbox: 503 DEPENDENCY_UNAVAILABLE. Other errors retain existing internal
  error schema/codes (401, 404/409 identity/visibility, 422 validation).

Normal result ingestion still requires a live fenced execution and preserves
its APPLIED/DUPLICATE 200, pending-verification 503 behavior. RunPod persists
request identity, one result event and exact callback bytes before the first
send. After restart, recover_once may revalidate the original worker through the
existing heartbeat endpoint. Only an authenticated matching, still-live lease
response permits same-byte ingestion; no new claim, worker change or persisted
local lease is accepted. The new leaseResponse definition mirrors the existing
shape using the v1.2 UTC definition. After terminal/expired ownership, or without
fresh lease authority, only the ACK-only endpoint may be used; never replay
result ingestion merely to discover whether it was already committed.
Pending/unknown confirmation is not success and must not restart AI/GPT.

RunPod local journal format v3 adds the callback outbox, fixed worker/event/body,
raw SHA plus separate JCS SHA, send claims and first authenticated ACK bytes.
An abandoned 30-second send claim permits same-byte recovery. ACK-only mode
is sticky and has a persisted 60-second confirmation window (each HTTP call is
at most 10 seconds); expiry leaves STOPPED/unconfirmed originals for reconciliation.
PREPARING is not inference replay permission. No cleanup/deletion is added.
Both runtime scheduling/executor integration and operational recovery acceptance
remain incomplete. No readiness/admission flag is enabled by these additions.

## Endpoint and exact fields

Authenticated `GET /api/internal/ai/worker-readiness/v2` currently returns
**HTTP 503**, `Cache-Control: no-store`, and the unwrapped response below.
Authentication failures retain the existing internal error handling.

| Field | Implemented value or type |
|---|---|
| `status` | `"not_ready"` |
| `contractVersion` | `"voice-coaching.runpod-http.v1.2"` |
| `serverTime` | UTC timestamp `YYYY-MM-DDTHH:mm:ss.SSSZ`; validate calendar correctness |
| `legacyReady` | boolean from the existing `RunPodBackendReadiness.isReady()` |
| `acceptedResultSchemaVersions` | ordered array of full result schema versions v1, v2, v3 only |
| `analysisProfiles` | `["LEGACY_SEUNGUN_V3"]` |
| `schemaDigests` | three closed `{file, sha256}` entries; exactly one for each file listed below |
| `canonicalSupported` | `false` |
| `canonicalAdmissionEnabled` | `false` |
| `canonicalStatus` | `"NOT_READY"` |
| `evidenceStoreStatus` | `"NOT_READY"` |
| `reasonCode` | `"NOT_READY"` |

Digest filenames are exactly `runpod_http_control_v1.schema.json`,
`runpod_analysis_request_v2.schema.json`, and `runpod_result_v1.schema.json`.
The controller emits that order; consumers identify entries by filename.
Each digest is 64 lowercase hexadecimal characters, computed over the packaged
schema's original bytes, not JCS or reserialized JSON. It must be compared with
the corresponding local schema bytes. Copying this new v1.2 schema does not add
it to the controller's advertised digest list.

The response intentionally does not advertise result v4 or the canonical
profile. Request-v2 parsing support does not establish result-v4 ingestion.
Even `legacyReady=true` leaves this endpoint at 503 and canonical admission
disabled. A future enabled shape must be explicitly implemented and reviewed
on both sides before relaxing this closed disabled contract.

## Packaging and consumer boundary

Copy this schema byte-for-byte into both repositories' `docs/contracts`.
Backend `build.gradle` already includes `runpod_*.schema.json` in
`processResources` under `contracts/`. A source copy establishes neither a
completed build nor runtime schema validation.

Existing `runpod_http_control_v1.schema.json`, global control VERSION/CONTRACT,
legacy readiness, claim, heartbeat, ACK and errors remain unchanged.
This slice does not wire the new readiness response into readiness clients.
The Python result/retention modules do validate the separate evidenceManifest
definition; this does not implement Backend evidence receipt verification.
A future client must distinguish this authenticated 503 capability document
from a legacy error envelope; the current Python `BackendHttp.call` accepts
only HTTP 200 response documents and otherwise parses the legacy error schema.
Do not route this endpoint through that client unchanged or claim it is ready.

## Evidence manifest and verification boundary

The callback-v4 plan describes immutable uploads, an authenticated
`POST /api/internal/ai/analyses/{analysisId}/evidence`, registration acceptance
at 202 or an existing receipt, verification to `VERIFIED`, and an authenticated
receipt-status query. The manifest is defined
at `#/$defs/evidenceManifest`; its SHA-256 covers the exact serialized UTF-8
manifest bytes, not JCS. Registration/status endpoints are now implemented locally
in CanonicalEvidenceController/CanonicalEvidenceRegistrationService, with additive
V33 receipt/job persistence. Registration writes only the initial PENDING state.
The separate verifier worker is now implemented locally; it is not yet wired
into the running Backend or migrated production DB.

### Evidence endpoints (2026-10-01 local implementation)

- POST `/api/internal/ai/analyses/{analysisId}/evidence`: body is the exact UTF-8
  evidenceManifest bytes, not a reserialized wrapper. New registration returns
  202; identical stored bytes return 200 with the same receipt. One execution
  cannot replace its manifest, immutable versions or original worker.
- GET `/api/internal/ai/analyses/{analysisId}/evidence/{receiptId}`: 200 status,
  no-store. Both operations require the existing internal Bearer and exactly one
  `X-Worker-Instance-Id` lowercase UUID header matching the claimed owner.
  This is not an additional credential/grant/signature.
- Response is the closed `$defs/evidenceReceipt`: contractVersion, receiptId,
  manifest association, workerInstanceId, manifestSha256, status, reasonCode,
  serverTime. Status is PENDING/VERIFYING/VERIFIED/REJECTED/EXPIRED/CANCELED.
  The first three have null reason; terminal failures use fixed schema codes.
  PENDING/VERIFYING responses carry Retry-After: 1. Registration is not verification.
- Errors use `$defs/evidenceError` only on these endpoints: contractVersion,
  reasonCode, retryable. HTTP 400 malformed JSON, 401 authentication, 404 missing
  receipt, 409 execution/owner/lease/manifest conflict, 413 size, 415 media type,
  422 schema/binding, 503 disabled/dependency, 500 unexpected are distinguished.
  Legacy control error shapes remain unchanged. Errors never contain raw evidence.
- Registration locks analysis before receipt, checks current execution/visibility,
  ownership/deadline/lease, derived namespace and CORE hash. It commits exact
  manifest bytes/SHA and a durable job without B2 I/O under lock. A terminal
  receipt does not override current visibility or authorize a callback by itself.
- Backend CanonicalEvidenceReader uses only `analysis.canonical.evidence.*`,
  explicit role credentials and a fixed HTTPS B2 origin, bounded reads/timeouts,
  no SDK retry or ambient proxy. It is not a global S3Client bean, so recording/TTS
  injection is unchanged. It checks private ACL, exact version, MIME, encryption,
  length and SHA. The separate CanonicalSemanticVerifier invokes the pinned
  offline Python verifier on those originals and the Backend's admitted outbox
  request. It replays frozen prepare without transport, checks scope/order/
  guidance/MFA and expression semantics. No GPT/AI execution occurs.
- Registration/verifier switches default off. Migrations, semantic verifier/queue
  consumer installation and secret provisioning must be completed before enabling them.
  Parsing a manifest or downloading matching bytes alone must never issue VERIFIED.

RunPod `canonical_receipt_client.py` validates response identity and manifest SHA,
handles 200/202 separately from the legacy client, and bounds polling by the
execution deadline/lease/cancel fence. A lost registration response resends only
the same journaled bytes, never PUT/core/GPT. Journal v2 retains receipt identity
and the first authenticated VERIFIED response. Restart recovery must use the
original worker identity and requery Backend; local stored VERIFIED is not
authority to deliver or revive an expired execution.

The result-v4 schema's artifact shape is exactly
`{kind, schemaVersion, sha256, byteSize, objectKey, versionId}`.
The Python manifest builder uses the actual returned
`CanonicalExpressionResult` byte fields without reserialization:

| Byte field | Artifact kind | Artifact schemaVersion |
|---|---|---|
| `core_bytes` | `CORE` | `canonical_ai_predeploy_v1` |
| `expression_bytes` | `BRIDGE_RESULT` | `canonical-llm-expression-result-v1` |
| `selection_bytes` when present | `SELECTION_PROJECTION` | `canonical-llm-expression-input-v1` |
| `binding_bytes` | `BINDING` | `voice-coaching.canonical-binding.v1` |
| `association_bytes` | `ASSOCIATION` | `voice-coaching.canonical-association.v1` |

SHA-256 and byteSize describe those exact bytes. Private storage returns the
derived objectKey and immutable versionId. Missing selection remains absent,
not an invented empty object. A storage reference is not a Backend VERIFIED
receipt. `canonical_retention.py` assembles a closed manifest with schemaVersion,
execution association and four required artifact kinds plus optional selection.
It caps the serialized manifest at 65,536 bytes and brackets uploads with the
caller's lease/cancellation/deadline fence. The 2026-10-01 local change requires
`canonical_journal.py` to checkpoint originals, upload intent, exact version and
manifest before returning. The separate receipt client now registers and polls.
Executor recovery orchestration and callback delivery outbox remain unconnected.
Backend verification code exists but production wiring/DB/role credentials are
not yet deployed. This local
code has syntax/static review only, not a production recovery acceptance result.

Result/store artifacts both cap versionId at 1024 characters and bytes at
16 MiB; version `null` is not an immutable version. The service rejects control
characters. Result objectKey has a 1000-character limit and a restricted pattern;
storage caps its configured prefix at 800 characters and derives the remainder
from analysis/execution/kind/SHA. The builder validates every returned reference
against the result artifact contract. Do not silently increase these limits.

The user's evidence-retention and cancellation/member-deletion grace decisions
are indefinite, with no automatic deletion. RunPod deployment documentation
records the required settings. Native metadata inspection on 2026-10-01 identified
the private bucket `speakai2026`; `canonical-evidence/v1/` is the planned isolated
prefix. Restricted keys were issued and Native-authenticated on 2026-10-01;
runtime injection and technical readiness remain pending;
the user has already authorized v4 admission. See the intelligentAI repository's
production rollout plan at `docs/canonical-integration/production-rollout-plan.md`
(the shared Backend contract copy does not contain that AI-side document).
This schema and documentation do not configure storage or enable canonical
admission.

## Callback verification queue (2026-10-01 local continuation)

The existing result URL now has a separate v4 branch; legacy result v1/v2/v3,
claim/heartbeat and the 200 identity-echo ACK remain unchanged. The wire schemas
are unchanged. Callback reader/worker switches default off and readiness v2
continues to advertise only the disabled canonical shape.

Evidence VERIFIED is necessary for a core-bearing callback, but does not validate
an arbitrary callback body. Backend durably records the exact callback bytes in
a separate inbox (V34), then an independent lane re-reads the receipt's exact
object versions and uses the frozen offline verifier against that callback.
A pre-core failure instead uses the admitted request and no fabricated receipt;
an execution with a registered core manifest cannot switch to this path.

While callback validation is pending, the same POST returns the existing
503 DEPENDENCY_UNAVAILABLE error with Retry-After: 1, not 202 or a successful ACK.
The transaction recording the inbox is committed before returning that error.
After validation, a retry of the identical event/body performs the final fenced
result/identity/projection/outbox transaction, then returns 200 APPLIED.
Already committed exact bytes receive DUPLICATE after current visibility checks;
lease expiry alone does not invalidate this duplicate acknowledgement.

v4 retries must preserve raw bytes as well as the JCS digest. Equal JCS with
different raw bytes is RESULT_EVENT_CONFLICT; it cannot replace the retained
original or exploit numeric rounding. No GPT/core regeneration is a delivery retry.
The worker's verification claim is recoverable and dependency attempts are bounded.

The media-cleanup hook was blocked by safety review pending separate approval.
Current commits with a visual object or old legacy segments explicitly return
NOT_READY; they do not silently skip cleanup or receive a successful ACK.
Per-user/analysis/session cache keys are invalidated outside the transaction,
and canonical-history reads bypass stale legacy caches. No global cache clear
or media/evidence deletion was added.

This is compiled local implementation, not an applied V34 migration, operational
callback acceptance, complete public v4 consumer, or production readiness.
