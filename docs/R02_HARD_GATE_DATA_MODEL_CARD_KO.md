# WP7 R02 후보 hard gate 데이터·모델·라이선스 카드

최초 작성: 2026-07-29 KST  
상태: **production 정적 계약만 구현 / 실제 R01 후보·D1 audio·모델·metric·R02 결과 0**

## 1. 범위와 완료 경계

R02는 R01이 만든 문장별 8–16개 human/CosyVoice private 후보에 동일한
네 hard gate를 적용해 R03가 읽을 수 있는 후보와 D1에 보존할 탈락 후보를
분리하는 단계다.

1. ASR 내용 일치
2. 음향 artifact
3. 휴지·리듬
4. 목표 음소 coverage

본 package는 gate runner가 아니다. Config/schema/template, predecessor와
attestation deep validator, future report/completion validator 및 read-only
preflight만 구현한다. 따라서 현재 다음은 모두 0이다.

- R01 완료 후보와 audio
- R02 ASR/artifact/pause/phone model·checkpoint
- model download·weight read·GPU query
- metric 실행·candidate pass/fail
- gate report·eligible/quarantine manifest·registry·commit
- R03 실행·candidate publication·human blind 평가

정적 구현 완료는 R02 실험 완료나 후보 품질 우위를 뜻하지 않는다.

## 2. 근거가 있는 정책과 아직 없는 수치

정본 실행계획과 원고가 직접 고정한 범위는 다음뿐이다.

- 문장 `R00-01`–`R00-20`
- 문장당 후보 8–16개
- ASR 내용 일치, artifact, 휴지, 목표 음소를 hard constraint로 사용
- Whisper는 내용 일치/WER gate 후보, UTMOSv2는 artifact gate로만 사용
- duration/pause와 nPVI 계열을 휴지·리듬 분석에 사용
- hard gate 뒤 Pareto/diversity를 R03에서 수행
- hard constraint 통과 후보의 aggregate transcript error 완료 경계 2% 이하

그러나 candidate별 exact metric, threshold, 모델 ID, checkpoint revision,
normalization, segmentation, minimum support와 code/checkpoint license는
정본 계획에 없다. 다음 값은 임의로 채우지 않고 전부 fail-closed marker로
고정했다.

| Gate | 계획이 지지하는 역할 | 현재 exact 상태 |
|---|---|---|
| ASR content | `openai/whisper-large-v3` 계열 content/WER gate | metric·threshold·checkpoint·normalization·segmentation·support·license `TBD_*` |
| acoustic artifact | UTMOSv2 계열 artifact gate, 인간평가 대체 금지 | 동일 |
| pause/rhythm | duration/pause/nPVI 계열 | 동일 |
| target-phone coverage | R00 문장별 target phone·규칙 coverage audit | 독립 구현·checkpoint·metric·threshold·support·license `TBD_*` |

2%는 candidate별 pass threshold가 아니다. Exact aggregate transcript-error
metric ID, 정의 hash와 aggregation도 `TBD_PREREGISTRATION`이며, 이 정의를
사람이 승인·잠근 뒤에만 0.02 완료 경계를 적용할 수 있다.

## 3. R00 선행자와 허용 target

R02 full preflight는 고정 R00 validator를 다시 실행하고 immutable report와
canonical JSON exact 일치를 확인한다.

- `status=valid`
- 문장 20개
- `r00_completion_claim_allowed=true`
- raw audio·row-level read 0
- sentence card/report/validator SHA·identity 불변

R00 sentence card에서 읽을 수 있는 것은 문장별 normalized text SHA,
`target_phones`, phonological-rule stable ID와 prosody target뿐이다. 이는
어떤 phone·규칙을 coverage 대상으로 감사할지 정하는 target rule이다.
실제 후보가 그 phone을 올바르게 실현했다는 gold label이 아니며, 학습자
오류 정답이나 locked-test annotation으로 사용할 수 없다.

## 4. R01 완료 bundle 선행자

D1 audio 위치를 보기 전에 다음 R01 evidence가 모두 completed·immutable이어야
한다.

- `artifacts/experiments/r01_human_cosyvoice_candidates_v1/commit.json`
- `artifacts/experiment_registry/r01-human-cosyvoice-candidates-v1/metadata.json`
- `data/processed/references/r01/r01_candidate_manifest_v1.json`
- signed human recording attestation
- signed CosyVoice artifact/license/runtime/GPU attestation

R01 completion commit 계약은 다음을 exact bind한다.

- schema `voice_coach.r01_candidate_completion.v1`, `status=completed`
- R01 config/registry/manifest/human/CosyVoice attestation SHA
- sentence 20개, candidate/audio 160–320개
- 문장별 8–16개와 human=CosyVoice source balance
- `r02_hard_gate_status=not_run`
- `publication_allowed=false`

R01 candidate manifest는 기존 R01 validator로 전수 재검사한다. Opaque UUID,
sentence text SHA, source attestation, consent/model-use, speaker 다양성,
audio object/hash 중복, speed/pitch cell과 금지 field를 commit 선언값과
독립적으로 다시 계산한다.

현재 R01 completion SHA는 `TBD_R01_COMPLETION`이다. Full preflight는 R00을
검증한 다음 이 marker에서 종료하며 R01 commit path 존재 여부도 probing하지
않는다.

## 5. Preregistration과 artifact attestation

R01 완료 뒤에도 audio를 열 수 없다. 다음 세 문서를 이 순서로 검증한다.

1. `r02_gate_preregistration_v1`
2. `r02_asr_attestation_v1`
3. `r02_artifact_attestation_v1`

Preregistration은 네 gate 각각의 metric definition SHA, 비교 연산자·수치,
normalization/segmentation ID, minimum support, 문장별 R03 최소 eligible 수,
aggregate transcript-error 정의와 missing/nonfinite 처리까지 포함해야 한다.
External human approval과 “R02 audio 접근 전 lock”이 모두 true여야 한다.

ASR attestation은 exact model/checkpoint/code revision, code와 checkpoint의
분리 license evidence, preprocessing spec, offline bundle/environment를
서명한다. Artifact attestation은 UTMOSv2 계열 artifact model,
pause/rhythm 구현, target-phone 구현·checkpoint를 각각 같은 수준으로
서명한다.

Template는 승인 문서가 아니다. 모든 exact 값은 `TBD_ATTESTATION`,
`research_use_authorized=false`, `approved_by_external_human=false`이며
completion claim도 false다.

## 6. Human/TTS 동일 gate와 blindness

R01 manifest의 `source_kind`는 lineage 확인에 필요하지만 metric 구현에는
제공하지 않는다.

```text
R01 private manifest
  ├─ source-blind execution manifest
  │    candidate_id, sentence_id, text SHA, audio object/hash
  │    -> 네 gate 공통 metric/threshold/normalization/support
  └─ sealed source-lineage sidecar
       source_kind, attestation, pseudonymous speaker/design cells
       -> immutable gate commit 전 unblind 금지
```

Human과 TTS에 source별 threshold, normalization, support 예외를 둘 수 없다.
Gate report, eligible manifest와 quarantine manifest에도 source label을
넣지 않는다. Source-stratified audit는 immutable gate commit 뒤 별도
sidecar join으로만 수행한다.

Blind human evaluation은 R02가 아니며 이후에도 source label을 숨겨야 한다.
R02 자동 gate가 human 또는 TTS 품질 우위를 의미하지 않는다.

## 7. S1/S2·gold leakage 금지

R02 gate는 다음을 읽거나 재사용할 수 없다.

- S1/S2 prediction, score, threshold, checkpoint
- S1/S2 train/dev/stack/calibration manifest
- learner error label·annotation
- gold test와 locked-test split

R00 target phone/rule는 coverage audit target으로만 사용할 수 있다. Candidate
실현 판정은 별도 사전등록·attestation된 R02 pipeline이 만들어야 한다.
이 분리는 같은 scorer가 생성 후보를 유리하게 고르는 self-evaluation과
locked-test 누출을 차단한다.

## 8. Candidate decision과 실패 보존

Candidate는 네 gate를 모두 통과할 때만
`eligible_for_r03_private_selection`이다. Missing, nonfinite, minimum support
미달은 모두 fail이다. 한 gate라도 실패하면 `quarantined`다.

탈락은 삭제가 아니다.

- R01 audio와 lineage는 D1에 그대로 보존
- delete/move/overwrite 금지
- immutable report의 quarantine partition에 기록
- R03는 eligible partition만 읽고 quarantine을 읽거나 pass로 재표기 금지

문장별 입력 8–16개를 전수 확인한 뒤 preregistered minimum eligible support가
충족돼야 completed report가 될 수 있다. R02 pass 후에도 public release는
false다. R03 Pareto/diversity selection과 별도 license/privacy 승격이
남아 있다.

## 9. Future artifact 계약

R02 completion commit은 다음 private artifact를 SHA-256으로 묶는다.

- blinded execution manifest
- sealed source-lineage sidecar
- full gate report
- `eligible_for_r03.json`
- `quarantined.json`
- completed registry

Report는 R01 candidate 순서를 그대로 유지하고 candidate별 네 metric,
threshold, support, evidence SHA와 overall decision을 가진다. Eligible와
quarantine은 정확한 disjoint partition이며 합집합이 R01 candidate 전체와
같아야 한다. 문장별 input/eligible/quarantine 산술과 aggregate transcript
metric을 전수 재계산한다.

모든 artifact는 D1이고 `publication_allowed=false`다. R02 completion
validator는 report만 신뢰하지 않고 registry, 두 partition, blind manifest,
lineage sidecar와 predecessor hash를 다시 읽어 교차 검증한다.

## 10. 개인정보·라이선스 경계

- Human raw capture: R0
- identity mapping: R1
- pseudonymous R01 audio/manifest 및 R02 report: D1
- public artifact: 없음

Gate report는 plaintext prompt/transcript, path, speaker identity,
source kind, S1/S2 score, gold label, MOS/CMOS를 금지한다. Human biometric
audio는 가명화 뒤에도 개인정보다.

Whisper/UTMOS라는 family 이름이나 source ledger의 code license 관측만으로
checkpoint 사용 권한을 추론하지 않는다. Code와 checkpoint license,
local bundle과 environment를 artifact별 external attestation으로 고정하기
전에는 모델 path도 해석하지 않는다.

## 11. 현재 접근 증거

Canonical config-only는 C1/public source 16개만 읽고 다음을 모두 0으로
유지한다.

- R00 deep validation
- R01 commit/registry/manifest/attestation
- R02 preregistration/ASR/artifact attestation
- D1 candidate/audio path resolution·audio read
- model path/metadata/weight/download
- GPU, metric, S1/S2, gold/locked, source-label gate input
- output path/write, publication

Canonical full preflight는 R00 public/aggregate deep validation 뒤
`R01 completion commit is TBD_R01_COMPLETION`에서 의도적으로 종료한다.
R01과 R02 protected artifact, model/audio/output은 모두 0이다.

## 12. 해제 조건

1. R01 human/CosyVoice 승인과 실제 문장별 8–16개 private 후보 완료
2. Completed R01 commit/registry/manifest/attestation 독립 deep validation
3. Exact 네 gate 및 aggregate metric을 사람 승인·사전등록
4. ASR/artifact/pause/phone model·checkpoint·preprocessing·license attestation
5. 별도 source-blind metric executor와 atomic no-overwrite publisher 구현
6. R02 completed bundle 독립 검증
7. Eligible partition만 R03에 전달

그 전에는 실제 audio/model/GPU/metric 실행과 R02 완료 주장을 할 수 없다.
