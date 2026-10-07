# DLPC GOP production cutover — 2026-10-07

## Authority and scope

The user explicitly approved the fixed SSH recovery command, private settings return, creation/storage of a prefix-restricted B2 reader, actual CUDA/TTS samples as an AGENTS exception, and deployment of the latest merged GOP version. This does not claim human audio-quality review.

## Implemented source

- `build_gop_release.py`: pinned merged GOP parent; FP32 CUDA Native forward, one math thread, shared TTS GPU lock, serial Native/MFA startup and measured RAM admission. GOP formula and GPT prompt remain unchanged.
- AI and Backend register the additive immutable DLPC GOP tuple. Historical identities remain readable.
- Remove the stale undefined `DLPC_CUDA` verifier reference left after the GOP merge.
- Runtime overlay includes source/schema and GOP packages only. Models remain in the pinned B2 base backup; secrets are supplied separately by AWS.

## Observations

- AWS dedicated port 22132 now accepts only the fixed recovery exporter and the two existing loopback reverse forwards.
- B2 key is restricted to `readFiles`, one bucket and `intelligentai/recovery/dlpc-20261006/`; stored root-only on AWS.
- DLPC received both settings and restricted B2 auth successfully. Secret values were not logged.
- Production cutover completed. At 2026-10-07 01:45 KST, RunPod `s8hb5b0l5k4qlx` was `EXITED`, while analysis and TTS remained ready through AWS. Backend maintenance was off. The Pod was stopped, not deleted; its 120 GB volume was retained. Storage billing and eventual volume removal are separate from compute shutdown.

## Actual sample findings

TTS returned HTTP 200, 63,679 bytes, 5,277 ms, matching audio SHA-256 in 1.62 seconds. Human listening quality is not verified.

Initial CUDA analysis QA exposed three migration defects: missing MFA optimization source, host/wheel cuDNN sublibrary mismatch, and a CPU-only observation device schema. The frozen MFA source (`ebecb9600d0cfc90264042db5b1cd43d0f7ca5aa83275924a5e3fef8b7fd7aee`) is now included in the recovery overlay. CUDA observations use the registered v2 frozen schema. Earlier failed QA attempts are preserved and never replayed.

The initial wheel-only `LD_LIBRARY_PATH` change did not fix cuDNN. The 9.20 wheel omits `tensor_ir`, so it loads DLPC's 9.24 system sublibrary. A process-scoped coherent 9.24 preload passed a real CUDA convolution. The deployment copies that observed set into a private immutable runtime directory and backs it up separately. The wheel files themselves matched their RECORD hashes. This matches the upstream report: https://github.com/pytorch/pytorch/issues/188892 .

The final full audio sample job `57edfbac-1d53-4f18-88c3-be77b9fcb16b` reached `RESULT_READY`, `ACCEPT`, coaching `READY`, B2 archive `COMPLETE` and Backend history sync `COMPLETE`. Public result lookup still succeeded after the upstream switch. This establishes one actual end-to-end CUDA/GPT sample, not broad quality or load coverage. Public TTS synthesis returned the same verified audio in 0.83 seconds.

With internal and direct Native residents loaded, observed available RAM was 6,416,952 kB and GPU usage was 3,888/24,564 MiB. This is a sample observation, not a maximum-input capacity guarantee. No automated regression suite or browser QA was run.

## Live path and identities

Frontend → `https://ai.voice-coaching.site/direct` → AWS nginx → loopback reverse SSH forward → DLPC analysis. Backend analysis uses the same AWS gateway; TTS uses its independent forward.

| Component | Deployed value |
| --- | --- |
| AI source | `e5c612f51850a0a3f51e901b53d4d806667eb3c3` |
| AI runtime bundle SHA-256 | `906df1334b8482a6c1fade401534dd8ea977edec8130da61acca9818b29f344c` |
| Backend source | `66f8ea8aaf4325cd87f1b4e783257f9f9ea3fedf` |
| Backend JAR SHA-256 | `d5eb1a370d5eba1e78a1335f570a3783a0fefde038caa2562636e23c42d417d7` |
| AWS verifier bundle SHA-256 | `65bb6af462f61d4ac59ecf5e7c073f7f7e756279fa05cffd7675132f448eb09b` |
| Frontend already deployed source | `bb5e6a1f4a5418922d3ed35fdafae1b96afad9b5` |
| Pipeline | `canonical-dlpc-gop-cuda-20261007-v2` |
| Core SHA-256 | `f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391` |
| H5 SHA-256 | `1612539f82a0b6e2c5c0b3551440b8094ca979bd8ab5d898386713bd0d1f54b7` |
| Lock SHA-256 | `3e2e81b43b1c1298266df428bc01ef0761d37f413073824cae2000623ea2eb3c` |
| TTS revision | `melo-ko-practice-dlpc-r1` |
| TTS fingerprint | `ace97bc52df31f304513bc5f18177669723fe996f5cd19235691a5449d9f3766` |

Later recovery-tool/documentation commits do not replace the live AI/JAR identities above. Persistent home recovery tools were updated separately.

DLPC opens SSH to AWS port 22132; AWS binds analysis at `127.0.0.1:18080` and TTS at `127.0.0.1:18083`. The tunnel supervisor's parent is PID 1. Neither application path depends on RunPod. The old RunPod-mediated administration connection ends when the Pod stops; subsequent DLPC administration uses its platform terminal.

## Cutover sequence and evidence

1. Completed actual CUDA/GPT and independent TTS samples; registered matching AI/Backend tuple and contracts.
2. Installed the AWS frozen verifier; compiled Backend with `bootJar --offline --no-daemon -x test -x check`. Verified all nine packaged schema digests against the verifier.
3. Held admission and observed zero active Backend analysis/transactions and zero active/pending/retry TTS work on two consecutive drain checks.
4. Changed Backend TTS revision/fingerprint, deployed the JAR, switched nginx to DLPC forwards and resumed admission. Backend release state became `COMPLETE`.
5. Copied six terminal historical direct jobs privately to DLPC. No recordings or journals were added to the general infrastructure backup.
6. Verified public analysis/direct readiness 200, authenticated deployment/TTS health 200, unauthenticated protected endpoints 401, frontend CORS and public TTS generation. Frontend deployed JavaScript already used the stable AWS direct URL; no new frontend change was required.
7. Stopped RunPod with the stop API, retaining its volume. After stop, public analysis/direct readiness remained 200; authenticated deployment reported the new bundle and zero active/direct jobs; TTS remained ready with the new revision.

Private observations and rollback configuration are on AWS under `/root/.config/intelligentai/dlpc-final-cutover-20261007/`. `public-observation.json` records switch checks; `after-runpod-stop.json` records independent operation at `2026-10-06T16:45:11Z`. Rollback must coordinate the backend verifier, contracts, TTS identity and upstream; changing only the JAR is insufficient.

## Recovery and B2

AWS's dedicated SSH listener permits only the fixed recovery exporter and the two loopback reverse forwards. User-approved settings are stored root-only under `/root/.config/intelligentai/dlpc-recovery-20261006/`. The B2 reader has only `readFiles`, restricted to bucket `speakai2026` and prefix `intelligentai/recovery/dlpc-20261006/`. A separate one-hour prefix-scoped writer was used for the CUDA archive upload. Master credentials were not copied to DLPC.

| Recovery artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `runtime-assets.tar.gz` | 16,062,581,191 | `b30e4d258ce42f4d40188d4a295784748fcba94c31dad78e5cc51f24334c7eed` |
| `source-manifest.json` | 42,449,956 | `f806086d4196c1df6ff2f63e0ea3b8565197a0a390139aca0beacc8a20920bda` |
| `gop-runtime-overlay-e5c612f5-20261007.json` | 14,689,470 | `906df1334b8482a6c1fade401534dd8ea977edec8130da61acca9818b29f344c` |
| `cudnn-9.24.0-20261007.tar.gz` | 818,749,951 | `3470515d874d346202cc1f09b8fa0be558cf8e471b8616e49bd4f508c87a0cce` |

The final overlay and CUDA object were downloaded fully from B2 and their digests verified on DLPC. Models and runtime libraries reside on DLPC's local root disk during execution; B2 supplies durable backups, not a mounted POSIX journal volume.

The persistent Jupyter startup hook is installed at `/home/2020067/.jupyter/jupyter_server_config.py`, with recovery tools under `/home/2020067/.local/share/intelligentai/recovery/startup-20261006/`. Recovery restores the pinned base, applies the GOP overlay, verifies/restores private CUDA libraries, fetches private settings through the fixed AWS command, and starts diagnostic services and the AWS tunnel.

**Recreation recovery is not automatic production failover.** Recovery forces admission off. An operator must reconcile durable Backend/B2 state and incomplete jobs before admitting work. B2 does not make DLPC's ephemeral journal durable. Full container recreation was not performed during this cutover. The installed hook depends on persistent home, preserved SSH identity/known host, a compatible base image and CUDA driver, and sufficient local restore space (approximately 46 GB for base download/extraction).

From the DLPC platform terminal, the fixed recovery entry point is:

```sh
sudo python3 "$HOME/.local/share/intelligentai/recovery/startup-20261006/recover_on_start.py"
```

Use after recreation or for operator-approved recovery; it intentionally rejects an existing production-admitted configuration instead of replacing it. Private state is `/run/intelligentai-recovery-state.json`. Never enable admission merely because liveness passes.
