# DLPC deployment

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
The installed Jupyter hook restores diagnostic services only. It does not yet
automatically resume production admission. Full reboot automation is a separate
pending change, not a capability established by the earlier successful cutover.
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
