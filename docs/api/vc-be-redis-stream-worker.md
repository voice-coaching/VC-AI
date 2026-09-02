# VC-BE Redis Stream worker integration

## Scope and ownership

`voice_coach.backend_analysis` is a private worker boundary for the VC-BE
analysis API. It has no FastAPI route. VC-BE owns authentication, session and
recording ownership, explicit consent issuance, durable job state, and public
result retrieval. Seungun alone owns pronunciation diagnosis and selected
phone; visual feedback and LLMs cannot create or replace that decision.

```text
VC-BE PostgreSQL outbox
  -> analysis:request:v1 (analysis-request.v4 JSON)
  -> shared analysis:canceled:v1:<request-event-id> tombstone checks
  -> authorization HMAC verification (no media I/O before this point)
  -> private S3-compatible object materialization
  -> isolated ffmpeg: 16 kHz mono signed PCM WAV
  -> pinned g2pK expected phones -> one call to the warm frozen Seungun detector
  -> optional canonical MP4 -> same-attempt visual supplement after Seungun
  -> analysis:result:v1 (analysis-result.v3 JSON) -> XACK request
  -> VC-BE result consumer -> PostgreSQL
```

The exact cross-repository fields are authoritative in VC-BE
`docs/api/ai-redis-stream-contract.md`. `AnalysisRequest` rejects unsupported
versions, unknown keys, traversal, script integrity differences, grant/request
binding differences and unsupported focus before any adapter runs. Legacy
`voice-coaching.analysis-request.v1` through `v3` are deliberately unsupported.
Result v3 requires a quiesced coordinated deployment: stop admission and the worker,
drain or resolve pending entries, deploy both repositories, and only then resume.

## Signed authorization

Every v4 request contains `voice-coaching.analysis-authorization.v3`. It binds
the request event, analysis/content/prompt, script and object-key digests,
the backend-calculated canonical audio digest, registered media metadata,
learning focus, opaque voice consent receipt, policy,
time window, purpose, data category, mandatory deletion and denied remote
egress. Optional visual input additionally binds its opaque canonical MP4 key,
digest, size, MIME, and separate face-processing consent receipt and policy.
The worker selects a key by `keyId`, reconstructs the documented
length-prefixed canonical form, compares HMAC-SHA256 in constant time, and
enforces a maximum ten-minute lifetime. Missing, unknown, altered, expired,
future, or policy-incompatible grants fail before storage access.

The receipt is an opaque SHA-256 digest, not a raw consent record or user ID.
The request also excludes session ID, recording ID, presigned URL and local
path. A retry must carry a newly issued request event, receipt and signature.

## Restricted object storage and Seungun

The built-in production composition is
`voice_coach.backend_analysis.production:create_worker`:

1. `HmacAnalysisAuthorizationAdapter` verifies the signed grant.
2. `S3CompatibleRestrictedObjectStore` accepts only one bucket and prefix,
   streams with a 20 MiB default bound, verifies Content-Length/MIME and the
   registered byte count, writes mode `0400`, calculates SHA-256, compares it
   to the signed backend claim, closes the
   response and recursively removes the mode `0700` attempt workspace. Visual
   materialization uses its own 128 MiB bound and workspace with identical
   metadata, integrity, and cleanup checks.
3. `RestrictedFfmpegAudioDecoder` runs bounded ffmpeg behind seccomp socket
   denial, receives raw PCM, writes a valid 16 kHz mono WAV header, validates
   it, and removes it on every exit.
4. `SubprocessG2PKExpectedPhoneFrontend` supplies pinned expected phones and
   `PersistentIsolatedFrozenSeungunDetectorAdapter` preloads the model once,
   executes exactly one detector call per request, serializes access, and recycles
   the isolated process after a bounded request count. The child retains seccomp
   network denial, a 64 GiB address-space bound, a one-hour aggregate CPU limit,
   request/startup timeouts, parent-death binding, bounded line-framed output, and
   no stderr forwarding. A phone
   mismatch or malformed detector document fails closed. Grounded feedback
   preserves the detector-selected phone and uses the approved catalog
   fallback; no free-form model is in this composition.
5. For a face-consented visual input, the promoted visual release reuses the exact
   Seungun snapshot and decoded PCM digest after the selected phone exists. It never
   invokes Seungun again. Only an allowlisted `supports_upstream` action with the
   same selected index is emitted as `visualSupplement`; failure or uncertainty
   leaves the visual field absent without changing the audio result.

`COACHING_READY` includes a `voice-coaching.pronunciation-evidence.v1` object
containing the same-attempt Seungun-selected phone, expected-phone index, optional
time window, threshold-passed detector ranking score and its explicit non-confidence
semantics. A coaching result without this evidence fails contract construction.
Completed responses leave transcript, numeric correctness scores and segments absent;
future fields require an approved Seungun-owned mapping rather than placeholder data.
The current production worker accepts only `PRONUNCIATION`; `INTONATION` and `BOTH`
fail before media access until an approved intonation pipeline exists.

## Environment

Install the worker-only locked dependencies without changing the base research
API environment:

```bash
.venv/bin/python -m pip install -e . -r requirements/backend-analysis-worker.txt
```

Secrets come from the deployment secret manager and must not be printed or
committed. The two services share key bytes, key ID, consent revision, bucket,
prefix, and Stream names. They use independent least-privilege storage roles:
VC-BE needs presign and HEAD; the worker needs GET only.

| Variable | Required | Meaning |
| --- | --- | --- |
| `VOICE_COACH_BACKEND_ANALYSIS_FACTORY` | Y | Set to `voice_coach.backend_analysis.production:create_worker`. |
| `VOICE_COACH_REPOSITORY_ROOT` | Y | Absolute checked-out intelligentAI root. |
| `AI_ANALYSIS_AUTHORIZATION_KEYS_JSON` or `AI_ANALYSIS_AUTHORIZATION_KEYS_FILE` | Y | Exactly one direct value or absolute regular-file secret containing the JSON keyId-to-base64 keyring; each decoded secret is at least 32 bytes. |
| `AI_ANALYSIS_CONSENT_POLICY_REVISION` | Y | Exact policy revision shared with VC-BE. |
| `AI_ANALYSIS_AUTHORIZATION_MAX_TTL_SECONDS` | N | Default and maximum `600`. |
| `AI_ANALYSIS_AUTHORIZATION_CLOCK_SKEW_SECONDS` | N | Default `30`, maximum `60`. |
| `AI_ANALYSIS_OBJECT_STORAGE_BUCKET` | Y | Private recording bucket. |
| `AI_ANALYSIS_OBJECT_STORAGE_REGION` | Y | S3 signing region. |
| `AI_ANALYSIS_OBJECT_STORAGE_ENDPOINT` | Provider-specific | HTTPS-only S3-compatible endpoint. |
| `AI_ANALYSIS_OBJECT_STORAGE_PATH_STYLE` | N | Default `false`. |
| `AI_ANALYSIS_OBJECT_STORAGE_RECORDINGS_PREFIX` | N | Default `recordings/`. |
| `AI_ANALYSIS_OBJECT_STORAGE_MAXIMUM_AUDIO_BYTES` | N | Default `20971520`. |
| `AI_ANALYSIS_OBJECT_STORAGE_MAXIMUM_VISUAL_BYTES` | N | Default `134217728`. |
| `AI_ANALYSIS_RESTRICTED_WORKSPACE_ROOT` | Y | Absolute local-only temporary root. |
| `AI_ANALYSIS_NLTK_DATA_ROOT` | Y | Absolute approved NLTK data root for pinned g2pK. |
| `AI_ANALYSIS_SEUNGUN_DEVICE` | N | `cpu` or `cuda`; default `cpu`. |
| `AI_ANALYSIS_SEUNGUN_STARTUP_TIMEOUT_SECONDS` | N | Warm-model startup bound, default `300`, maximum `900`. |
| `AI_ANALYSIS_SEUNGUN_REQUEST_TIMEOUT_SECONDS` | N | Per-request response bound, default `180`, maximum `600`. |
| `AI_ANALYSIS_SEUNGUN_MAXIMUM_REQUESTS_PER_PROCESS` | N | Warm-process recycle cap, default `100`, maximum `1000`. |
| `AI_ANALYSIS_FFMPEG_BINARY` | N | Absolute reviewed ffmpeg path; otherwise `PATH` discovery. |
| `AI_ANALYSIS_VISUAL_RELEASE_ROOT` | Production Y | Absolute immutable promoted visual release directory; pair with the landmark model. |
| `AI_ANALYSIS_VISUAL_LANDMARK_MODEL_PATH` | Production Y | Absolute pinned MediaPipe model path; pair with the release root. |
| `AI_ANALYSIS_REDIS_HOST` / `AI_ANALYSIS_REDIS_PORT` | Y | Dedicated private Stream Redis endpoint. |
| `AI_ANALYSIS_REDIS_USERNAME` | N | Redis ACL identity. |
| `AI_ANALYSIS_REDIS_PASSWORD` or `AI_ANALYSIS_REDIS_PASSWORD_FILE` | Y | Exactly one direct value or absolute regular-file secret. |
| `AI_ANALYSIS_REDIS_SSL_ENABLED` | Y | Must be `true`; other values refuse startup. |
| `AI_ANALYSIS_REDIS_CA_CERT` | Private CA only | Absolute CA certificate path when it is not in the system trust store. |
| `AI_ANALYSIS_REQUEST_STREAM` | N | Default `analysis:request:v1`. |
| `AI_ANALYSIS_RESULT_STREAM` | N | Default `analysis:result:v1`. |
| `AI_ANALYSIS_REQUEST_DLQ_STREAM` | N | Default `analysis:request:dlq:v1`. |
| `AI_ANALYSIS_REQUEST_CONSUMER_GROUP` | N | Default `analysis-workers`. |
| `AI_ANALYSIS_REQUEST_CONSUMER_NAME` | Y | Unique worker instance name. |
| `AI_ANALYSIS_CANCELLATION_KEY_PREFIX` | N | Must match VC-BE; default `analysis:canceled:v1:`. The suffix is the opaque request event UUID. |
| `AI_ANALYSIS_CANCELLATION_POLL_INTERVAL_SECONDS` | N | Running-job tombstone poll interval, default `0.25`, range `0.05..5`. |
| `AI_ANALYSIS_PENDING_CLAIM_IDLE_MS` | N | Default `300000`. |
| `AI_ANALYSIS_PROCESSING_HEARTBEAT_INTERVAL_SECONDS` | N | PEL lease refresh interval, default `60`; must remain below claim idle. |
| `AI_ANALYSIS_REDIS_CONNECT_TIMEOUT_SECONDS` | N | Default `5`, maximum `30`. |
| `AI_ANALYSIS_REDIS_SOCKET_TIMEOUT_SECONDS` | N | Default `30`, maximum `300`. |
| `AI_ANALYSIS_REDIS_HEALTH_CHECK_INTERVAL_SECONDS` | N | Default `30`, range `1..300`. |
| `AI_ANALYSIS_MAXIMUM_REQUEST_PAYLOAD_BYTES` | N | Default `65536`, maximum `1048576`; oversized content is not copied to the DLQ. |
| `AI_ANALYSIS_MAXIMUM_REQUEST_DLQ_ENTRIES` | N | Approximate DLQ cap, default `10000`; request/result streams are never trimmed before safe acknowledgement. |
| `AI_ANALYSIS_MAX_CONSECUTIVE_POLL_FAILURES` | N | Default `5`; the supervisor restarts the worker after exhaustion. |
| `AI_ANALYSIS_POLL_FAILURE_BACKOFF_SECONDS` | N | Default `5`, range `0.1..60`. |
| `AI_ANALYSIS_WORKER_STATE_PATH` | Production Y | Absolute path on a private writable runtime mount. Contains only status, timestamps and counters. |
| `AI_ANALYSIS_WORKER_HEALTH_MAX_AGE_SECONDS` | N | Default `600`, range `5..3600`; it exceeds the current bounded decode, g2pK and detector path. |
| `AI_ANALYSIS_WORKER_HEALTH_START_PERIOD` | Compose N | Docker health startup grace, default `5m`; keep it at least as large as the model startup timeout. |

AWS SDK credentials use the normal boto3 credential chain. Do not place keys
in the JSON request or a tracked `.env.local`. Run only after all settings and
the approved frozen artifacts are present:

The worker Redis ACL must permit its Stream commands plus `EXISTS`, `DEL`, and `EVAL`.
The Lua script is fixed in code and only atomically checks the opaque cancellation
key before adding the bounded result payload; no operator-supplied script is accepted.

```bash
.venv/bin/python scripts/run_backend_analysis_worker.py --once
```

The reviewed container definition is in `deploy/backend-analysis-worker`. It runs
as UID/GID 10001, uses a read-only root filesystem, drops all capabilities, mounts
the detector bundle/model/processor, visual release, landmark model and NLTK data read-only, accepts the HMAC keyring
and Redis password through external Docker secrets, and keeps only restricted media
and the health state on bounded tmpfs mounts. Build or start it only in the approved
GPU deployment environment; model weights and protected media never enter the image.

The container health start period is five minutes so readiness is not asserted before
the frozen model preload completes. The worker handles `SIGTERM`/`SIGINT` by stopping after the current poll. A result is
still written before request ACK; if the supervisor's grace period expires during an
inference, the unacknowledged request remains in the PEL for reclaim. Repeated Redis
poll failures use bounded backoff and exit after the configured limit. The state-file
healthcheck never includes request IDs, object keys, endpoints, or exception text.

## Delivery and verification

- The consumer reclaims PEL entries before new work. If a reclaimed batch
  satisfies the requested count it returns without a second blocking read.
- While a valid request is executing, the worker refreshes its PEL idle time with
  `XCLAIM JUSTID`; it synchronously refreshes once more before result publication.
  This prevents another healthy worker from reclaiming a long inference under the
  normal claim-idle policy. Duplicate execution after partitions remains harmless
  because VC-BE applies only the active request generation idempotently.
- Before media access and throughout execution, the worker checks the shared
  request-generation cancellation tombstone. A newly observed tombstone interrupts
  the warm Seungun child process, suppresses the result, and ACKs the canceled request.
  The final tombstone check and result `XADD` run atomically in one Redis script, so a
  cancellation that wins that Redis ordering cannot race into a result publication.
- It writes a terminal result before `XACK`; result publication failure leaves
  the request pending for later `XAUTOCLAIM`. Once the durable handoff exists, a fixed
  Lua script performs request `XACK` and `XDEL` together and removes any observed
  cancellation tombstone. VC-BE retains the event-to-Stream-ID index until its
  terminal DB-aware retention sweep proves that cleanup is safe.
- Malformed requests enter the restricted request DLQ before ACK. Replay is a
  deliberate operator action using a new Stream entry.
- Unit tests cover authorization-before-media, Java/Python canonical HMAC
  parity, restricted storage cleanup, warm-process reuse/recycle/timeout,
  one-call Seungun ownership, and all
  failure mappings.
- A synthetic TLS Redis integration test covers real redis-py list-shaped
  replies, result-before-ACK, PEL reclaim and malformed-request DLQ. It is
  skipped unless `AI_TEST_REDIS_*` variables name an ephemeral test broker.
  `scripts/run_backend_analysis_redis_integration.sh` creates and removes a
  digest-pinned local Redis container and one-day synthetic TLS certificate.

Current state: code and synthetic local integration are **verified**. Real
object-store credentials, production consent/key issuance, restricted learner
media and the frozen Seungun model runtime remain deployment verification work;
they are not implied by the synthetic smoke.
