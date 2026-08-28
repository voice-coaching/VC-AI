# intelligentAI prompt-refiner context

This file is a compact routing context for prompt refinement. It is not a
completion record and does not replace the current canonical documents under
`docs/`.

## Project purpose

- `intelligentAI` is a Python/FastAPI research system for evidence-constrained
  Korean L2 pronunciation diagnosis, coaching, reference speech, and
  re-recording comparison.
- The immediate priority is reproducible diagnostic and evaluation evidence,
  not an unsupported production or educational-effect claim.
- Keep plans, static readiness, dry runs, development results, actual model
  runs, independent validation, and completed human evaluation distinct.

## Mandatory work posture

- Preserve the user's exact objective, scope, language, constraints, and
  requested deliverable. Do not turn an answer, review, or diagnosis request
  into an implementation request.
- Before changing the repository, read all applicable files under
  `.codex/ai_rule_developer/`, then inspect the current task-relevant canonical
  documents under `docs/`. Current files win over this summary.
- Preserve unrelated working-tree changes. Never infer that a missing or
  deleted document should be restored.
- For ML work, keep the staged gate order: data/label/license/privacy audit,
  speaker-disjoint split and QC, small smoke run, reload/evaluation, then human
  validation. Never begin full training by bypassing an unresolved gate.
- Treat `TBD`, unresolved labels, missing consent/license/checksums, stale
  registries, partial artifacts, SIGTERM, and failed runs as blockers or
  observed failures, never as success.
- Do not commit or expose restricted audio, transcripts, detailed annotations,
  raw identifiers, source paths, manifests, checkpoints, `.env.local`, tokens,
  or secrets. Authentication must remain in a user-controlled session.
- Keep filesystem, network, subprocess, model runtime, dataset portal, GPU
  worker, and external LLM calls behind explicit adapter/client boundaries.
- Keep API/presentation, application/service, domain models, and
  repository/adapter responsibilities separate. Keep entities, DTOs, expected
  phones, realized phones, features, predictions, and evidence semantically
  distinct.
- Update the relevant canonical docs when behavior, contracts, gates,
  privacy/license rules, external integrations, API, state, or architecture
  actually changes. Never document an unexecuted result as completed.
- Prefer the smallest in-scope implementation and proportional verification.
  Explicitly name anything that was not verified.

## Closed-loop voice-coaching architecture

- Loop A generates Korean reference candidates and scores them. The generator
  roadmap is CosyVoice 2 (MVP), StyleTTS 2 (adversarial R&D), then F5R-TTS with
  GRPO/reward guidance. This is not one undifferentiated GAN pipeline.
- S1 combines a WavLM-large plus HiPAMA-style phone-to-utterance hierarchy with
  explicit evidence: phone CTC, GOP, MFA/DTW duration and pause, F0,
  eGeMAPS energy, rhythm/nPVI, UTMOS naturalness, and WavLM speaker similarity.
- S2 must be independent from S1 (for example HuBERT-large or
  Whisper-large-v3), producing approval, disagreement, or uncertainty rather
  than reusing the same evidence as proof.
- Candidate selection is hard constraints, then a Pareto frontier, then
  diversity-preserving reference-bank selection. A weighted sum must not allow
  one extreme score to hide a hard defect.
- Loop B compares a learner utterance against multiple acceptable references,
  produces segmental and suprasegmental evidence, prioritizes only the top one
  to three trainable issues, and gives evidence-constrained coaching. Low
  confidence must lead to abstention, re-recording, or expert review.
- Loop C periodically performs expert multi-label calibration, scorer threshold
  refresh, metamorphic tests, reward-hacking audits, and curriculum updates.
- Separate generator/reference quality from learner outcomes. Claims about
  learning effectiveness require their own blinded or controlled human-study
  evidence; test-bench metrics do not establish that causal result.

## Prompt-refinement output policy

- Produce one improved task prompt, in the user's language unless the user asks
  otherwise.
- Add concrete goal, relevant context pointers, boundaries, deliverables, and
  verification criteria only when supported by the original request and this
  project context.
- Resolve harmless omissions with conservative assumptions. Preserve material
  ambiguity as an explicit question or verification item instead of inventing
  facts.
- Do not solve the task, claim work was performed, request secrets, or add
  unrelated features.
