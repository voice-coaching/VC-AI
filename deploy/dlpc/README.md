# DLPC deployment

## Unified v6 source contract — 2026-10-19

New internal admissions use request v4, result v6 and handoff v2 for both audio
and audiovisual inputs. Audio-only input has `video=null` and a NOT_PROVIDED
visual result; no media receipt or video evidence is fabricated. Production
startup and both handoff readiness paths require the same eleven schema hashes.
The offline verifier accepts only VERIFY_HANDOFF and VERIFY_PRECORE.

This is a source change, not an application deployment. The separate Backend
must support the same nullable-media v6 contract, v2 readiness and verifier.
Drain/reconcile existing handoffs before switching; retired journals are retained
for operator reconciliation and never relabeled or replayed. Video still requires
its existing execution identity/assets/admission gates. See the
[current contract and rollout prerequisites](../../docs/canonical-integration/v6-unification.md).

## Direct ingress tunnel recovery — 2026-10-19

The J406 AWS reverse tunnel stopped after SSH disconnected and four rapid
restarts failed to bind AWS loopback port 28080. Supervisor entered `FATAL`;
the local direct API stayed ready, while public nginx returned 502 without
CORS headers. Restarting only the tunnel restored public direct readiness.

`start_aws_tunnel.py` now supervises a persistent SSH reconnect loop with delays
of 5, 10, 20, 40 and at most 60 seconds. An SSH process lasting at least 90 seconds
resets the delay. Host-key pinning, key restrictions and forwarding targets stay
in the host identity configuration. Supervisor `RUNNING` reports the reconnect
manager's liveness; public API readiness must be observed separately.

After installing the reviewed launcher in the persistent `aws-tunnel` directory,
invoke it as the operator with `--upgrade-client` once. It accepts only the exact
previous generated supervisor configuration, replaces it atomically, and runs
`reread`/`update` for the tunnel alone. Other configuration drift is rejected.
Normal startup also reconciles the on-disk configuration with Supervisor.

The updated launcher is installed on J406. Public direct readiness and its
configured FE origin returned 200 at 11:27:29 UTC. The analysis and TTS processes
were not restarted. No tests, preflight replay, analysis submission or browser
verification were performed. Internal Backend handoff readiness remains a
separate blocker: its result-v5 schema digest differs from the running AI copy.
See [cause, observations and remaining scope](../../docs/canonical-integration/incident-20261019-direct-ingress-tunnel.md).

## Recovery completed — 2026-10-15

The recreated DLPC runtime was restored from the pinned base archive and the
2026-10-08 production overlay, including both 2026-10-10 resident patches.
At 12:24:46 UTC, public analysis/direct/TTS readiness returned 200, Backend
handoff support and admission were true with matching contracts, and FE CORS
preflight passed. Source remains `791ca549…`, bundle `d5ccbc76…`.
See [incident and recovery evidence](../../docs/canonical-integration/incident-20261015-dlpc-cold-recovery.md).

The installed persistent startup helpers now match the reviewed checkout.
Startup checks Backend support/contracts before starting the executor, then
requires full Backend admission after startup to avoid a circular cold-start
dependency. Public readiness includes TTS; failure after releasing this
recovery's direct hold restores that hold. Runtime preparation also provisions
the two exact configured direct work/spool directories as service UID/GID 10001,
mode 0700, without changing their parent permissions or moving job records.

The fixed deployment controller and runner program are installed. At 14:12 UTC,
GitHub runner `intelligentai-dlpc` was re-registered as ID 22 and confirmed online
and idle; its dedicated supervisor process was RUNNING. The old offline
registration (ID 21) was replaced after confirming there were no pending runs.
Actual inference/quality QA and a subsequent platform recreation were not tested.

## Production recovery update — 2026-10-12

`recover_on_start.py` now restores the pinned base archive and the already
deployed 2026-10-08 overlay, applies the 2026-10-10 resident fixes, restores
private settings and CUDA/TTS dependencies, and invokes `start_production.py`.
The previous diagnostic-only flow remains available as `bootstrap.py`.
`recover_on_start.py --restore-only` prepares assets/settings without starting
services; normal startup continues through production readiness checks.

The downloader accepts HTTP 200 only when the requested range is the entire
object with the exact expected length; resumed ranges still require matching
HTTP 206 headers. Full SHA-256 verification remains mandatory. `restore_assets.py`
resumes pinned partial downloads synchronously under the existing download lock.
Eight bounded 8 MiB range requests reduce stalled-connection delays; writes stay
in order so an interrupted file is always a resumable contiguous prefix.
Partial installations and hash mismatches still require reconciliation.

Production startup checks the pinned source/bundle, private settings, Backend
handoff contract digests and admission, and the TTS synthesis fingerprint. It
backs up local settings before enabling the four existing production flags and
the supervisor's `--admit` command. Analysis readiness precedes TTS startup.
Only this recovery's own direct-admission hold is removed; foreign operator holds
and interrupted-job receipts are preserved, and no producer is replayed.
Local analysis/direct/TTS readiness and public analysis/direct readiness are
required before recording `production_services_ready`.

The existing persistent Jupyter hook is the entry point. Its installed helper
directory must contain the complete `install_startup_hook.py` file list, including
`start_production.py` and `restore_tts_runtime.py`. The runtime state is
`/run/intelligentai-recovery-state.json`; root-only step output is in
`/run/intelligentai-recovery.log`. Failures retain `failedStep` and `errorCode`.
An existing healthy installation is checked without deliberately restarting it.
This does not recreate GitHub Actions runner credentials or recover recordings
lost from ephemeral storage. Readiness is not inference or quality QA.

At the 2026-10-12 observation, implementation had passed Python syntax and diff
checks, base restoration was running, and the detached `--restore-only`
continuation was staged in `recovery/candidate-production-20261012`. The installed
hook had not yet been updated. The 2026-10-15 completion above supersedes that
pending installation status. The dated observations below are historical.

## MFA/GOP performance candidate — 2026-10-07

See [implementation and remaining gates](../../docs/canonical-integration/dlpc-mfa-gop-implementation-20261007.md).
`build_performance_release.py` derives a pinned CPU GOP batching/overlap and MFA
timing candidate. It does not allowlist or deploy it. Developer parity/performance
QA is pending. `prepare_restored_runtime.py` provisions both GPU and analysis
locks; both analysis lanes must select the qualified new core together.

## Current production state — 2026-10-07

Analysis and TTS are deployed on DLPC, reached through AWS port 22132 reverse
SSH forwards. RunPod is stopped. The former GitHub RunPod deployment workflow
was disabled and its three deployment secrets and Pod variable removed; its
YAML is removed in this branch. Current runtime and recovery assets do not
require a RunPod API key or a running Pod.

See [production cutover](../../docs/canonical-integration/dlpc-gop-cutover-20261007.md)
and [dependency/reboot audit](../../docs/canonical-integration/dlpc-reboot-audit-20261007.md).
At that observation date the installed Jupyter hook restored diagnostic services
only. The 2026-10-12 update above supersedes that implementation once installed.
The dated sections below retain their original observation scope.

## AWS follow-up, 2026-10-06

`check_aws_connection.py` runs read-only on AWS as root. It reads only the relevant
private Backend settings, disables redirects/proxies for credentialed checks,
and checks the two loopback endpoints, unauthenticated rejection and Backend
handoff readiness. `--require-ready` returns exit 2 for known readiness/config
blockers. It does not approve quality, fingerprint compatibility, drain or cutover.
Actual checks found analysis 200/503, TTS ready 200, missing-auth 401 for both,
and Backend handoff 200. DLPC also reached Backend handoff directly with auth.
Backend's old TTS revision must change together with the new approved synthesis
fingerprint and upstream. The nginx candidate passes syntax validation but is
not active. Existing public TLS endpoints remain the integration boundary.

`store_aws_recovery_settings.py` accepts the same role-scoped stdin document as
`receive_settings.py` and stores it in a new AWS root-only directory. It never
prints values, changes live Backend settings or grants the tunnel key access.
The first transfer was rejected by automatic approval review. After explicit
user approval, analysis/TTS settings were stored at
`/root/.config/intelligentai/dlpc-recovery-20261006/` with root-only 0700/0600
permissions. This is a separate recovery source, not automatic secret delivery.

Persistent home now contains `~/.local/share/intelligentai/recovery/` with the
reviewed recovery tools, without tokens. `restore_assets.py` orchestrates the
pinned B2 downloader/installer in an empty container, retaining failed state for
inspection. Its stdin is the short-lived Native auth object, not a master key.
After verified installation it removes only the local 16.06GB compressed copy,
retains B2 objects/receipts, and replaces the historical supervisor config with
the diagnostic version that omits the obsolete AWS:22 tunnel. Private settings
are then separately supplied using `receive_settings.py`.

Run `sudo python3 ~/.local/share/intelligentai/recovery/bootstrap.py` from the
DLPC platform terminal once assets and settings are present. `--prepare-only`
only prepares private directories/token/lock. Preparation is idempotent and
preserves a live GPU lock inode; a mismatched existing token fails closed.
Bootstrap starts the separate AWS tunnel and diagnostic analysis/TTS managers,
never enables admission or silently replaces an existing production config.
The actual current-installation invocation succeeded. A platform recreation hook
is not installed; full cold recovery with this new wrapper is not verified.

The root-disk work area is `/var/lib/intelligentai/work`, not persistent storage.
The updated launcher moves generic temporary files there, but explicit old
canonical/media workspace settings remain authoritative until their pending
jobs are reconciled. The updated `dlpc-4cpu` ResourceBudget requires 14GiB disk
headroom and 10,000 available inodes, in addition to RAM admission. This is not a
hard disk quota, cache collector or proof of maximum-input memory/disk usage.
Source changes are not hot-patched into the running attested API/TTS package.

The active v5 executor uses AWS `BackendCanonicalJournal`. The legacy local
SQLite mount guard does not mandate a new DLPC ext4 volume for this path.
DLPC scratch preserves pre-handoff resend bytes only while its storage survives;
AWS accepted handoffs and delivery state remain authoritative after recreation.
Never automatically rerun a producer whose outcome is uncertain.

This deployment uses the existing API contracts and AWS HTTPS domain. Historical
`RUNPOD_HTTP` transport names remain wire identifiers, not a RunPod dependency.
The RunPod SSH relay is used for migration only.

## Implemented controls

- `AI_ANALYSIS_RESOURCE_PROFILE=dlpc-4cpu` limits active analysis to one. It refuses
  new work below 2 GiB available memory or when memory cannot be read. Duplicate
  execution lookup still precedes resource admission; claim/lease behavior stays
  in the existing runtime.
- The profile survives the canonical child environment allowlist. Core import
  preparation and resident subprocess environments use one math-library thread.
- The v2 package adapts the historical MFA memory watchdog to measured
  MemAvailable and finite cgroup v1/v2 headroom. Unavailable limits/OOM counters
  remain unknown. Its reserve is 2 GiB plus the preserved MFA combined RSS limit
  at admission; per-process RSS limits and monitoring remain enabled.
- `build_release.py` verifies the observed Native v4 hashes and creates a new
  immutable core/H5 pair. Native and MFA use one CPU thread; Native preparation
  precedes MFA startup. CUDA uses FP32 with TF32 disabled and records `cuda:0` in
  both evidence and its frozen schema/verifier. Parent CPU calibration remains
  historical evidence, not a claim of CUDA calibration.
- `build_tts.py` checks the existing TTS source digest, applies one Torch thread
  and a RAM admission budget, and shares `gpu_budget.py` with Native. The file
  lock serializes model loads and GPU execution across processes. TTS waits up
  to two seconds for an inference slot; Native waits up to 120 seconds within
  its externally supervised request lifetime. OS process exit releases the lock.
- The changed TTS executable has revision `melo-ko-practice-dlpc-r1`. Switch the
  Backend revision/fingerprint with its upstream, after validation. Keeping the
  old request revision intentionally returns `REVISION_MISMATCH`; do not mix
  cached audio from the two executable identities.
- Model weights, preprocessing, rate, seed, scoring policy and three-request
  resident recycling are preserved. Two-request overlap and standby generations
  are disabled pending developer measurements. No numerical parity is claimed.

## Asset migration

Run `copy_assets.py` on the verified source with `assets-20261005.json`, the
verified SSH alias and an isolated destination such as
`/opt/dlpc-migration-20261005/rootfs`. It never stops the source or deletes files.
The destination must already be owned by the migration user. The disk reserve is
5 GiB; a resumed directory is conservatively counted in full.

`verify_copy.py` compares file contents using rsync checksums and link targets.
Run it after all copy processes finish. Frozen manifests and model SHA-256 checks
remain additional runtime gates. This is transfer integrity, not inference QA.
The public `certifi/cacert.pem` roots are an explicit exception to PEM exclusion;
private keys and env files remain excluded. Removing these roots breaks HTTPS.

`install_staged.py` requires the completed verification receipt and empty final
paths. It preserves `/workspace` and `/opt/intelligentai` absolute paths needed
by the frozen conda environments. It installs a dedicated UID/GID 10001 and
private runtime directories. Existing installations are rejected. A partial
installation leaves `installation.json`; reconcile its moves before retrying.

Store a private, verified durable asset backup before production cutover. A
diagnostic installation may proceed while the verified source remains available,
but is not a completed migration. A tiny NFS write does not establish user quota.
`backup_staging.py` archives an intact verified staging tree as its owner; it is
not an installed-tree backup or restore command. Once installation has moved the
tree, do not rerun that helper against the now-empty staging path. A failed
partial is removed, while its failure receipt is retained.
The DLPC root overlay and `/run` do not survive container recreation. A platform
startup hook and restore procedure must be confirmed before declaring migration
complete. Do not use another UID to bypass a NAS quota.

The user selected private Backblaze B2 for durable assets after the NAS quota
failure. `b2_backup.py` archives the installed paths, derived v2 releases and
nonsecret receipts directly to B2 (64 MiB parts, at most two in flight). It does
not stage a large archive on NAS. A short-lived Native API authorization object
arrives on stdin; the master key is not sent to DLPC or stored in an archive.
Completion requires full download SHA-256/size, tar integrity, and a separately
uploaded/downloaded manifest. It does not back up OS images, private settings,
SSH keys, recordings or pending journals, and it is not a restore/boot test.
See the [B2 backup record](../../docs/canonical-integration/dlpc-b2-backup-20261006.md)
for exact object identities and operational results. Keep the original source
until the separate deployment cutover gates are satisfied.

## Service startup and cutover

Provision `/etc/intelligentai/analysis.env` as root:root 0600 and the separate TTS
token `/run/voice-coaching-tts/token` as UID/GID 10001, mode 0600. Transfer these
outside model archives. `launch.py` injects role settings explicitly and binds
both HTTP listeners to loopback. The supervisor owns restarts and bounded logs.
`receive_settings.py` accepts bounded role settings on SSH stdin, refuses existing
files, and never logs values. Container recreation needs a separate secure source
to inject them again. The SSH supervisor uses UID 120067, not the numeric login
name 2020067 (which Supervisor would interpret as a different UID).

`supervisord.conf` initially starts only the diagnostic analysis listener.
It keeps canonical bootstrap/handoff disabled so staging cannot restore a
production journal owned by the old host. Start TTS after Native preparation.
`launch.py analysis --admit` is only for the final, verified cutover; it does not
override the existing release identity/Backend readiness checks.

Before enabling the new core, register its reviewed core/H5/schema identities in
AI and the Backend verifier and complete developer QA. Builders deliberately do
not populate the execution allowlist. Do not substitute the old manifest hash,
remove policy checks, or report GPU evidence as CPU to obtain readiness.

Keep `ai.voice-coaching.site`; route AWS loopback 18080 to DLPC analysis 8080,
and 18083 to DLPC TTS 8083 through the separately authorized connection. Preserve
tokens, TLS, deadlines and callback ownership. Verify both services, drain old
leases and pending handoffs, then switch the upstream. Stop the old Pod only
after these conditions pass; deletion is a separate decision.

## Verification scope

Repository instructions permit static source/document checks, compilation and
operational status checks. They reserve inference, score comparison and feedback
quality QA for the developer. No automated regression suite or browser QA is
part of this migration. See the [execution record](../../docs/canonical-integration/dlpc-migration-execution-20261005.md)
for actual progress and blockers.

## After container recreation (2026-10-06)

Actual recreation erased `/opt`, `/workspace`, `/etc/intelligentai`, and `/run`.
The NAS home retained SSH keys. The prior ready state is historical, not current.
`b2_restore.py` downloads the pinned manifest and 16.06GB archive into the empty
root disk under `/opt/intelligentai-b2-restore-20261006`; `/var/tmp` is only a
1GB tmpfs. Credentials enter on stdin, and extraction is separate after digest
verification. Download completion does not mean installation or service readiness.

The downloader uses bounded HTTP byte ranges and retries interrupted transfers.
After inspecting a failed download and confirming there is no active restore,
an operator can invoke `b2_restore.py --resume` with fresh restricted B2 auth on
stdin. It retains the contiguous partial file, validates each range response,
and checks the complete pinned SHA-256 before marking the download verified.
Already completed objects are rehashed. A digest mismatch is never bypassed;
an extraction attempt or existing installation requires separate reconciliation.

`install_runtime_overlay.py` also reconciles the three legacy active prompt
symlinks from the base backup when the pinned overlay contains exactly the same
relative pointer names as regular Git blobs. It records the previous links in
`/opt/intelligentai/ops/overlay-link-recovery/<bundle-sha>/links.json` before
replacing the leaves. Other symlinks and changed targets remain rejected.

The overlay also assigns service group 10001 and mode 0750 to the MFA
optimization directory above `candidate/`. That directory is absent from the
base archive; leaving it root:root prevents the request-time `mfa.py` import
even when model readiness succeeds. Preserve the pinned adapter bytes.

The AWS SSH listener is prepared by `prepare_aws_tunnel_listener.py`: dedicated
22132, the existing approved key only, source IP/user restrictions, no sessions,
and only loopback 18080/18083 reverse forwards. Existing sshd/nginx stay in place.
The operator must allow DLPC's /32 at 22132 in the AWS security group.

The persistent DLPC home launcher is
`~/.local/share/intelligentai/aws-tunnel/start_aws_tunnel.py`.
Run it as user 2020067 (UID 120067), not root, after network access is confirmed.
`--prepare-only` creates its private configuration without starting a connection.
The separate supervisor uses `/tmp/intelligentai-aws-tunnel-120067`, bounded logs,
and the pinned AWS host identity (`HostKeyAlias=13.209.184.226`). It requires
explicit reinvocation after recreation until a platform startup hook is wired.
Do not also start the historical `aws-tunnel` entry from the archived analysis
supervisor. Apply the current configuration before restoring service operation.
The transport was verified across AWS 22132 on 2026-10-06 16:24 KST: analysis
health 200, readiness 503 (admission disabled), authenticated TTS ready 200.
See the execution record for the exact scope; no production upstream switch occurred.

After the downloader reports `download_verified`, `restore_downloaded.py` verifies
both pinned hashes and every archive member against the 141,602-entry inventory,
extracts into private staging, then installs only into absent target paths. It
uses absolute `/usr/sbin/groupadd` and `/usr/sbin/useradd` paths because its child
PATH is intentionally minimal. An interrupted/failed restore must be inspected;
do not delete state to force a retry over an existing installation.

Receive private settings separately with `receive_settings.py`, then use
`prepare_restored_runtime.py` to recreate worker directories, GPU lock and the TTS
token. Apply the current supervisor configuration (without the historical tunnel
program), start diagnostic analysis, and then start TTS. Keep canonical admission,
bootstrap and handoff disabled until the independent deployment gates pass.
The observed manual restore completed. Native preparation/GPT generation/quality
QA and automatic recovery after platform recreation were not revalidated.

The dedicated AWS authorized_keys file omits the invalid key option
`permitopen="none"`; key options require a host:port pair. Direct forwarding is
still prohibited by the separate daemon's `PermitOpen none` and
`AllowTcpForwarding remote`; MaxSessions 0 blocks shell/subsystem sessions.
## 2026-10-06 전환 전 후속 작업

현재 단계와 승인 중단점은 [전환 준비 기록](../../docs/canonical-integration/dlpc-cutover-preparation-20261006.md)을 따른다.
`recover_on_start.py`·Jupyter hook·고정 SSH exporter는 준비된 코드이며 아직 설치하지 않았다.
CUDA source/schema 등록과 AWS 비활성 verifier 설치는 실행 승인과 별개다.
`prepare_backend_tts.py`는 비활성 env 후보만 작성하며 drain·전환·재시작을 수행하지 않는다.

## GitHub Actions deployment (2026-10-08)

See the [Actions enrollment and deployment record](../../docs/canonical-integration/dlpc-github-actions-20261007.md)
for the approved AWS loopback administration tunnel, dedicated DLPC runner, fixed
root controller, and main-only workflow. PRs #37/#38 are merged and deployment
is enabled. The [activation record](../../docs/canonical-integration/dlpc-actions-activation-20261008.md)
pins deployed source `791ca549…`, the concise audio H5, Backend/schema activation
and successful Actions force-restart run `37751075955`. Compatible main source
updates use the controller; frozen contracts, prompts, dependencies and recovery
baseline/profile promotion still require coordinated operations.
Runner registration is independent of analysis/TTS readiness.
The new runner startup hook restarts an existing installation; it does not
restore runner credentials after container recreation erases the root disk.

The 2026-10-10 direct resident recovery patch retires a generation after a
published system-failure result, including a zero-exit worker completion. It
closes readiness during finalization and cleanup and never replays that job.
`restore_direct_resident.py` checks the exact original and replacement SHA-256,
requires analysis stopped, backs up the original, and records the override in
`deployment.json` under `operationalPatches`. `recover_on_start.py` invokes it
before bootstrap. The pinned replacement source is read from the persistent
`/home/2020067/intelligentAI/src/voice_coach/direct_analysis/service.py`; updating
that source requires reviewing and updating the patch hashes. The frozen
Native/MFA release and model parameters are unchanged.

The additional `restore_resident_health.py` patch is also applied before
bootstrap. Each resident now imports the request-time MFA adapter under the
service UID before model startup. Readiness checks the host, sidecar and actual
MFA worker from the private startup receipt using `/proc` state and kernel
start time. Exited/zombie processes and reused PIDs fail readiness. An idle
unhealthy generation is retired through the existing guarded cleanup and
reprepared; an active execution remains owned until completion/cancellation,
without inference replay. A structured `resident_mfa_unavailable` event records
retirement without recording audio, script or credentials. Both patches have
pinned hashes and separate entries in `deployment.json.operationalPatches`.

Operational interpretation: `RESULT_READY` can contain a system-failure result.
Inspect `result.decision`, `result.failure` and coaching dispatch status as well
as readiness; an archive/history ACK alone does not mean analysis succeeded.

## J406 Seungun performance candidate — 2026-10-16

The local observation found 16 affinity-allowed logical CPUs (Xeon Gold 6338),
no CPU quota at the container leaf, and a memory limit of 195,421,011,968 bytes
(182 GiB). `/proc/driver/nvidia/gpus/*/information` identifies three A100 80GB
PCIe devices. The coding environment does not expose `/dev/nvidia*`, so
`nvidia-smi` cannot report utilization or confirm CUDA access here. These are
resource observations, not a performance benchmark or a new service check.

The `dlpc-j406` candidate in `build_performance_release.py` targets single-request
latency. It keeps the original acoustic forward pass on `cuda:0`, runs FP64 CTC
candidate likelihoods concurrently on `cuda:1` and `cuda:2`, and overlaps GOP
with MFA. Native CPU work gets eight threads; MFA BLAS gets four. TTS keeps its
existing `cuda:0` coordinator, which still serializes TTS and Native GPU work.
GOP uses the other two GPUs within the global one-analysis lock. HTTP worker
count, job admission concurrency and the three-request generation lifecycle
are unchanged.

GOP batches up to 256 unique transcripts across phone positions and reuses the
reference likelihood. Each GPU retains one utterance log-probability tensor.
At most one candidate batch per GPU is in flight, and workers are joined before
request cleanup. Temporary workspace is estimated per candidate and limited to
the smaller of 2 GiB and one quarter of observed free VRAM per GPU. This is an
estimate, not a hard allocator cap; insufficient room or a CUDA error produces
the existing unavailable result, without CPU fallback or inference replay.
The existing candidate ordering, complete-utterance CTC formula, FP64 precision,
blank ID and evidence mapping are retained. Numerical parity is still unverified.
Native CUDA CTC accepts the existing long targets and FP64 log probabilities;
this does not switch to the cuDNN FP32 path. See [PyTorch CTC documentation](https://docs.pytorch.org/docs/stable/generated/torch.nn.CTCLoss.html).

Build from the pinned deployed GOP core and concise H5 parent, with no inference:

```sh
python3 deploy/dlpc/build_performance_release.py \
  --profile dlpc-j406 \
  --core /path/to/canonical_dlpc_gop_cuda_20261007_v2 \
  --h5 /path/to/canonical_dlpc_gop_announcer_20261007_v2_h5 \
  --destination /path/to/new-candidates
```

The builder verifies every parent file, updates source hashes and both manifests,
and compiles the packaged Python files. It preserves the concise GPT prompt and
model parameters. It does not register or activate the new immutable tuple.
`launch.py` now honors an explicitly selected resource profile; the default
remains `dlpc-4cpu`. The [profile example](j406-performance.env.example) requires
16 effective CPUs, checked using affinity and ancestor cgroup quotas. Selecting
it with the old frozen core fails instead of claiming eight-thread execution
while the core still pins one thread. J406 keeps its cuDNN and libstdc++ preload
when its CPU budget increases.

Before coordinated activation, developer QA must compare the old and candidate
releases on the same approved recordings: warm core wall time and GOP/MFA spans,
peak CPU RSS and per-GPU VRAM, and original evidence including candidate order,
likelihoods, selected phones and timestamps. Check short/long inputs, repeated
tokens, impossible CTC alignments, cancellation and generation cleanup. GPU
visibility and the provisioned `/run/intelligentai/analysis.lock` must be checked
in the actual service environment. No automated inference, regression or quality
QA was run while implementing this candidate; no speedup factor is established.
AI/Backend release identities and persistent recovery pins must be updated
together after review, using the coordinated immutable release procedure.


### 2026-11-11 existing-assets coordinated source promotion

`coordinated_release.disk_headroom` distinguishes a promotion whose frozen CORE/H5/model
files are already installed and hash-verified from an unknown/new-assets operation.
For installed assets and a complete capacity plan it retains the admission policy's
8 GiB media reserve, every admitted slot's disk budget, the 1 GiB tmp reserve and
every slot's tmp budget, twice the full source payload and all rollback bytes.
When root and tmp are different filesystems, each is checked separately. Missing
assets or an unknown plan retain the historical 14 GiB reserve plus source/rollback.
The check runs before holding admission and again after drain, before stopping services.
This does not change analysis admission, configured worker limits, model/search settings,
or permit installation without sufficient measured free space.

Main/tree verification keeps normal managed proxy settings and rejects redirects;
GitHub authentication is not sent through a proxy-bypass override. The staged reviewed
helper is verified against the immutable main source before updating the installed helper.
