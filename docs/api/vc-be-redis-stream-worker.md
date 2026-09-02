# VC-BE Redis Stream worker integration

## Scope and ownership

`voice_coach.backend_analysis` is a private worker boundary for the VC-BE
analysis API. It has no FastAPI route. VC-BE owns authentication, session and
recording ownership, explicit consent issuance, durable job state, and public
result retrieval. Seungun alone owns pronunciation diagnosis and selected
phone; visual feedback and LLMs cannot create or replace that decision.

```text
VC-BE PostgreSQL outbox
  -> analysis:request:v1 (analysis-request.v3 JSON)
  -> authorization HMAC verification (no media I/O before this point)
  -> private S3-compatible object materialization
  -> isolated ffmpeg: 16 kHz mono signed PCM WAV
  -> pinned g2pK expected phones -> one frozen Seungun detector call
  -> analysis:result:v1 -> XACK request
  -> VC-BE result consumer -> PostgreSQL
```

The exact cross-repository fields are authoritative in VC-BE
`docs/api/ai-redis-stream-contract.md`. `AnalysisRequest` rejects unsupported
versions, unknown keys, traversal, script integrity differences, grant/request
binding differences and unsupported focus before any adapter runs. Legacy
`voice-coaching.analysis-request.v1` and `v2` are deliberately unsupported.

## Signed authorization

Every v3 request contains `voice-coaching.analysis-authorization.v2`. It binds
the request event, analysis/content/prompt, script and object-key digests,
the backend-calculated canonical audio digest, registered media metadata,
learning focus, opaque consent receipt, policy,
time window, purpose, data category, mandatory deletion and denied remote
egress. The worker selects a key by `keyId`, reconstructs the documented
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
   response and recursively removes the mode `0700` attempt workspace.
3. `RestrictedFfmpegAudioDecoder` runs bounded ffmpeg behind seccomp socket
   denial, receives raw PCM, writes a valid 16 kHz mono WAV header, validates
   it, and removes it on every exit.
4. `SubprocessG2PKExpectedPhoneFrontend` supplies pinned expected phones and
   `IsolatedFrozenSeungunDetectorAdapter` executes exactly once. A phone
   mismatch or malformed detector document fails closed. Grounded feedback
   preserves the detector-selected phone and uses the approved catalog
   fallback; no free-form model is in this composition.

Completed responses leave transcript, numeric scores and segments absent
unless a future approved Seungun mapping explicitly supplies them.

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
| `AI_ANALYSIS_AUTHORIZATION_KEYS_JSON` | Y | JSON keyId-to-base64-secret keyring; each decoded secret is at least 32 bytes. |
| `AI_ANALYSIS_CONSENT_POLICY_REVISION` | Y | Exact policy revision shared with VC-BE. |
| `AI_ANALYSIS_AUTHORIZATION_MAX_TTL_SECONDS` | N | Default and maximum `600`. |
| `AI_ANALYSIS_AUTHORIZATION_CLOCK_SKEW_SECONDS` | N | Default `30`, maximum `60`. |
| `AI_ANALYSIS_OBJECT_STORAGE_BUCKET` | Y | Private recording bucket. |
| `AI_ANALYSIS_OBJECT_STORAGE_REGION` | Y | S3 signing region. |
| `AI_ANALYSIS_OBJECT_STORAGE_ENDPOINT` | Provider-specific | HTTPS-only S3-compatible endpoint. |
| `AI_ANALYSIS_OBJECT_STORAGE_PATH_STYLE` | N | Default `false`. |
| `AI_ANALYSIS_OBJECT_STORAGE_RECORDINGS_PREFIX` | N | Default `recordings/`. |
| `AI_ANALYSIS_OBJECT_STORAGE_MAXIMUM_AUDIO_BYTES` | N | Default `20971520`. |
| `AI_ANALYSIS_RESTRICTED_WORKSPACE_ROOT` | Y | Absolute local-only temporary root. |
| `AI_ANALYSIS_NLTK_DATA_ROOT` | Y | Absolute approved NLTK data root for pinned g2pK. |
| `AI_ANALYSIS_SEUNGUN_DEVICE` | N | `cpu` or `cuda`; default `cpu`. |
| `AI_ANALYSIS_FFMPEG_BINARY` | N | Absolute reviewed ffmpeg path; otherwise `PATH` discovery. |
| `AI_ANALYSIS_REDIS_HOST` / `AI_ANALYSIS_REDIS_PORT` | Y | Dedicated private Stream Redis endpoint. |
| `AI_ANALYSIS_REDIS_USERNAME` / `AI_ANALYSIS_REDIS_PASSWORD` | N/Y | Redis ACL identity and secret. |
| `AI_ANALYSIS_REDIS_SSL_ENABLED` | Y | Must be `true`; other values refuse startup. |
| `AI_ANALYSIS_REDIS_CA_CERT` | Private CA only | Absolute CA certificate path when it is not in the system trust store. |
| `AI_ANALYSIS_REQUEST_STREAM` | N | Default `analysis:request:v1`. |
| `AI_ANALYSIS_RESULT_STREAM` | N | Default `analysis:result:v1`. |
| `AI_ANALYSIS_REQUEST_DLQ_STREAM` | N | Default `analysis:request:dlq:v1`. |
| `AI_ANALYSIS_REQUEST_CONSUMER_GROUP` | N | Default `analysis-workers`. |
| `AI_ANALYSIS_REQUEST_CONSUMER_NAME` | Y | Unique worker instance name. |
| `AI_ANALYSIS_PENDING_CLAIM_IDLE_MS` | N | Default `300000`. |

AWS SDK credentials use the normal boto3 credential chain. Do not place keys
in the JSON request or a tracked `.env.local`. Run only after all settings and
the approved frozen artifacts are present:

```bash
.venv/bin/python scripts/run_backend_analysis_worker.py --once
```

## Delivery and verification

- The consumer reclaims PEL entries before new work. If a reclaimed batch
  satisfies the requested count it returns without a second blocking read.
- It writes a terminal result before `XACK`; result publication failure leaves
  the request pending for later `XAUTOCLAIM`.
- Malformed requests enter the restricted request DLQ before ACK. Replay is a
  deliberate operator action using a new Stream entry.
- Unit tests cover authorization-before-media, Java/Python canonical HMAC
  parity, restricted storage cleanup, one-call Seungun ownership, and all
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
