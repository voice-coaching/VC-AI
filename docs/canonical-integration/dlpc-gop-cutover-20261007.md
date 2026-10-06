# DLPC GOP production cutover ? 2026-10-07

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
- Production upstream is still RunPod at this checkpoint. CUDA/TTS samples and cutover are pending.

## Actual sample findings

TTS returned HTTP 200, 63,679 bytes, 5,277 ms, matching audio SHA-256 in 1.62 seconds. Human listening quality is not verified.

Initial CUDA analysis QA exposed three migration defects: missing MFA optimization source, host/wheel cuDNN sublibrary mismatch, and a CPU-only observation device schema. The frozen MFA source (`ebecb960?fd7aee`) is now included in the recovery overlay. DLPC core processes select their own wheel cuDNN path; CUDA observations use the registered v2 frozen schema. Earlier failed QA attempts are preserved and never replayed.
