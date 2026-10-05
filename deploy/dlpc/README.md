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

`install_staged.py` requires the completed verification receipt and empty final
paths. It preserves `/workspace` and `/opt/intelligentai` absolute paths needed
by the frozen conda environments. It installs a dedicated UID/GID 10001 and
private runtime directories. Existing installations are rejected. A partial
installation leaves `installation.json`; reconcile its moves before retrying.

Store a private, verified durable asset backup before moving staging into final
paths. A successful tiny NFS write does not establish available user quota.
The DLPC root overlay and `/run` do not survive container recreation. A platform
startup hook and restore procedure must be confirmed before declaring migration
complete. Do not use another UID to bypass a NAS quota.

## Service startup and cutover

Provision `/etc/intelligentai/analysis.env` as root:root 0600 and the separate TTS
token `/run/voice-coaching-tts/token` as UID/GID 10001, mode 0600. Transfer these
outside model archives. `launch.py` injects role settings explicitly and binds
both HTTP listeners to loopback. The supervisor owns restarts and bounded logs.

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
