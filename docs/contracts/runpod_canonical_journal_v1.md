# Canonical PostgreSQL handoff journal v1

Local implementation, not deployed. Schema: `runpod_canonical_journal_v1.schema.json`.
Existing public FE/API, callback v1-v3, recording upload and TTS contracts are unchanged.
This additional internal protocol does not enable admission.

## Authentication and routing

Base: `/api/internal/ai/analyses/{analysisId}/journal/{executionId}`.
Every request requires one existing internal Bearer Authorization and one
`X-Worker-Instance-Id` UUID header. Authentication precedes body reading.
Responses/errors are unwrapped, `Cache-Control: no-store`. No database credentials
are distributed to RunPod. The existing service JWT/user/recording visibility
checks are not replaced by journal ownership.

| Method and suffix | Input | Success |
|---|---|---|
| POST /reserve | Original request-v2 JSON, <=64 KiB; single X-Worker-Revision and X-Pipeline-Revision headers | 200 journalSnapshot; fixed server event ID |
| POST /start | Empty body | 204, one-shot producer start committed |
| GET (base) | No body | 200 journalSnapshot, current visibility required |
| POST /prepare | journalPrepare, <=64 KiB | 204, immutable association and artifact set |
| PUT /artifacts/{kind}/stage | application/octet-stream, original bytes <=16 MiB | 204, exact length/SHA and idempotent bytes |
| POST /artifacts/{kind}/begin | Empty body | 204, sole transition granting one PUT |
| POST /artifacts/{kind}/uploaded | journalReference JSON <=8 KiB | 204, immutable exact B2 version checkpoint |
| POST /artifacts/{kind}/readback | Same reference bytes <=8 KiB | 204, worker readback checkpoint only |
| POST /manifest | Existing evidenceManifest raw JSON <=64 KiB | 204, exact immutable manifest |
| GET /callback/ack | No callback body | 200 existing resultAck shape, DUPLICATE only |

All bodies reject compression/oversize input; JSON uses strict duplicate/UTF-8/schema
checks. Both control bytes and callback bytes retain separate raw and JCS identity.
Snapshot includes metadata/references only, never staged raw artifacts, request
text, tokens or callback raw body. Backend API authentication is not user access.

Errors use the existing evidence error shape
`{contractVersion:"voice-coaching.runpod-http.v1.2",reasonCode,retryable}`.
400/413/415/422 describe invalid input; 401 authentication; 404/409 missing,
stale, conflicting or inactive ownership; 503 unavailable/not ready. New stable
409 reasons: PRODUCER_OUTCOME_UNKNOWN, PRODUCER_NOT_STARTED,
EVIDENCE_UPLOAD_OUTCOME_UNKNOWN, EVIDENCE_STAGING_INCOMPLETE.
Do not automatically retry a failed/ambiguous /start or /begin.

## Durable authority and ordering

V35 is additive and requires V32-V34. Reserve binds original request bytes and
JCS digest to the immutable execution registry and the existing live claimed
worker. Revisions and result event cannot be replaced. /start is a one-way marker:
lost scratch, timeout or an existing marker is not permission to rerun AI/GPT.

Prepare binds all four mandatory artifact kinds and optional SELECTION_PROJECTION.
Per-artifact states are DECLARED -> PREPARED -> UPLOADING -> UPLOADED -> VERIFIED.
The complete byte set must be staged before any /begin. An UPLOADING outcome
without a returned version is uncertain: stop and reconcile, never issue another
PUT. UPLOADED can read back the stored exact version. Worker VERIFIED here is
not an independently VERIFIED evidence receipt.

Normal mutation locks analysis, then journal; each operation rechecks current
lease/deadline and mutable ownership. No transaction spans B2, GPT or a subprocess.
Late uploaded/readback completion is a special write-only path: immutable
execution/worker plus the previously authorized upload is required, it returns no
private state and cannot start uploads, read artifacts, renew leases or resurrect
cancelled/withdrawn work. Late evidence is private orphan retention.

The existing evidence registration API now requires identical durable manifest
bytes. The existing callback inbox requires the reserved event/revisions and
manifest identity. A prepared core cannot be hidden as a pre-core failure.
Neither metadata staging nor SHA checks replace offline frozen semantic verification.

## Callback and ACK recovery

The first existing /result POST is the durable handoff into the PostgreSQL inbox.
PENDING is committed before retryable 503; 503 itself is not proof of acceptance.
While still owning a live lease, retry only identical bytes/event. After lease
loss, use read-only GET ACK recovery; no new claim/heartbeat/AI/PUT.

Verification also queues an additive callback-apply task. The apply worker can
finish stored VERIFIED bytes without another Pod POST, under the existing
visibility, lease, deadline and verified receipt fences. Its 30-second claim and
bounded retry/backoff do not renew an execution lease. A lost Pod can therefore
still expire; this protocol never promises late application or lease resurrection.

The first APPLIED ACK bytes are persisted in the SAME transaction as result
application. A DUPLICATE ACK has its own immutable first response bytes. GET ACK
rechecks current APPLIED inbox/result/pointer before returning DUPLICATE; it does
not register or apply a result. Stored ACK records describe the server response,
not proof that a client received it.

## Storage and rollout limits

B2 remains the original-evidence store. PostgreSQL additionally holds a bounded
private pre-B2 handoff spool (at most five 16 MiB artifacts per execution), not a
public/long-term evidence query API. No deletion/TTL was added: cancelled, partial
or unknown outcomes are preserved. No automatic DB-spool cleanup policy is
claimed; normal B2 handoff must be independently verified before any later
approved copy-compaction policy. The configured global budget counts DECLARED
artifacts too and rejects new reservations at capacity; this is not infinite
storage capacity despite indefinite retention.

New settings (all inactive/unspecified in the example):
ANALYSIS_CANONICAL_EVIDENCE_JOURNAL_ENABLED,
ANALYSIS_CANONICAL_EVIDENCE_CALLBACK_APPLY_ENABLED,
ANALYSIS_CANONICAL_EVIDENCE_JOURNAL_STAGING_BUDGET_BYTES (16 MiB..16 GiB).
The byte budget bounds staged artifact bytes, not WAL/index/backups/request and
callback storage. Operators must plan total DB capacity/backup/restore and orphan
reconciliation before activation.

RunPod producer now requires the remote journal and private ephemeral scratch;
the old local SQLite outbox is retained as unselected implementation history, not
a fallback. Factory/boot/runtime scheduling, original-worker restart context,
full stage orchestration, proxy body/time limits, operational migration/provenance
checks, FE consumers, key injection and developer acceptance remain rollout work.
No automated tests/fixtures/inference/browser QA were run for this change.
