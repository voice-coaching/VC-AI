# DLPC deployment

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
The transport has not yet been verified across AWS 22132.
