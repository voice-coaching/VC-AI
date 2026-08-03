# WP7 R03 다중 reference bank 데이터·선택 모델 카드

상태: **production 정적 계약 구현 중 / 실제 후보·audio·metric·선택·bank 0**

기준일: 2026-07-29 KST

## 1. 목적과 완료 경계

R03은 R02의 네 hard gate를 모두 통과한 후보만 대상으로, R00의 고정
20문장마다 3–5개 reference를 Pareto+diversity 규칙으로 선택하는 단계다.
선택 결과는 사람 원어민과 CosyVoice를 함께 보존하고, 화자·속도·pitch
변이를 유지하는 내부 다중 reference bank다.

이 문서는 다음을 완료로 주장하지 않는다.

- R01 인간 녹음 또는 CosyVoice 후보 생성
- R02 metric 실행 또는 hard-gate 완료
- R03 objective 계산, Pareto frontier 계산, diversity sub-sampling
- candidate audio/model/GPU 접근
- R03 bank/selection trace/aggregate/commit 생성
- R04 blind MOS·CMOS 또는 학습 효과
- reference 품질 우위, production 공개 또는 배포

R03 연구 완료는 실제 R01→R02 완료 artifact, 사전등록, 별도 selection
executor, 재현 실행과 독립 검증이 모두 있어야 판정한다. 정적 준비도에는
연구 점수를 부여하지 않는다.

## 2. 입력 계보와 강제 순서

R03 runtime 순서는 다음과 같다.

```text
정적 C1 pin/schema/template
  → R00 valid report 재계산·exact match
  → R01 completed commit/registry/manifest/attestation deep validation
  → R02 completed commit/registry/report/eligible partition deep validation
  → R03 preregistration approved·frozen·TBD 0
  → R02 eligible D1 candidate aggregate/lineage resolve
  → 별도 selection executor
```

R01 completion SHA가 `TBD_R01_COMPLETION`이면 commit path 존재 여부도
확인하지 않고 종료한다. R01이 완료되지 않으면 R02 completion path를 열지
않는다. R02가 완료되지
않으면 R03 preregistration과 eligible candidate data를 열지 않는다.
Preregistration이 승인되지 않으면 objective/selection data와 output path를
열지 않는다. Quarantine 후보는 selection view에 들어갈 수 없다.

R00/R01/R02의 config·contract·schema와 report/commit SHA는 R03 config에서
exact pin한다. 각 gate를 통과한 뒤 이전 파일의 device/inode/size/mtime/hash
snapshot을 다시 확인한다. Symlink, duplicate key, non-finite 수, hash drift,
TOCTOU, 기존 output 덮어쓰기는 fail-closed다.

## 3. 근거가 있는 설계와 아직 없는 설계

논문과 실행 계획이 직접 정한 범위는 다음뿐이다.

- hard constraint 뒤 Pareto frontier를 사용한다.
- objective 이름은 naturalness, prosody fit, pedagogical clarity, diversity다.
- 문장마다 3–5개 exemplar를 선택한다.
- 서로 다른 화자·말속도·운율 변이를 보존한다.
- 사람/TTS 다중 reference를 구성한다.

아래 operational choice에는 현재 재현 가능한 근거와 승인된 protocol이
없다. 따라서 임의 기본값을 발명하지 않고 모두
`TBD_PREREGISTRATION`으로 고정했다.

- 네 objective의 exact 측정값·revision·방향·결측 처리
- normalization 방식, fit population/scope, clipping, constant 축 처리
- dominance 정의와 epsilon/tolerance
- exact tie, frontier shortfall/overflow 처리
- diversity distance feature allowlist·metric·weights
- 최소 거리와 speaker/speed/pitch distance 정의
- clustering/sub-sampling 알고리즘
- seed, 입력 순서, 선택 개수, source balance tie-break, replay 횟수

이 값이 하나라도 TBD이면 R02 eligible data를 resolve하지 않고 종료한다.
향후 승인 artifact는 R02 eligible data 접근 전에 frozen되었다는 증거와
서명 receipt SHA를 포함해야 한다.

## 4. Source-blind와 outcome-blind 선택

Selector가 볼 수 있는 field는 다음으로 제한한다.

- opaque candidate ID와 R00 sentence ID
- target text SHA와 R02 eligible-row SHA
- preregistered objective vector
- speed/pitch bucket
- source를 드러내지 않는 blinded speaker cluster SHA

다음 field는 selector input에서 금지한다.

- `source_kind`, source attestation, human/TTS 표시, 모델 이름
- R04 label/score, MOS, CMOS
- learner outcome, learning gain, retention
- publication preference

Source kind는 immutable selection이 끝난 뒤 balance와 lineage 검증을 위해
별도 sidecar에서 join한다. R04 또는 학습 결과를 본 사후 재선택,
objective/threshold 변경, 유리한 후보 cherry-pick은 금지한다.

## 5. Bank 구성 계약

최종 D1 bank는 정확히 R00 20문장을 포함하고 문장마다 3–5개, 전체
60–100개 후보를 포함한다.

각 문장은 다음을 만족해야 한다.

- human recording과 CosyVoice TTS가 모두 존재
- source별 선택 수 차이 1 이하
- distinct pseudonymous speaker/voice 2 이상
- speed bucket 2개 이상
- pitch bucket 2개 이상
- 모든 후보가 R02 eligible partition의 member
- 모든 후보가 preregistered Pareto 규칙의 frontier member
- candidate ID, text/audio object/audio/source attestation/speaker/bucket과
  R02 eligible-row hash가 계보 sidecar와 exact match
- candidate 중복과 quarantined candidate 0

이 categorical floor는 R00/R01의 다중-source 및 speaker/speed/pitch 보존
목표와 문장당 최소 3개라는 명시적 범위에 따른 구조적 최소치다. 연속
공간의 최소 거리는 근거가 없어 TBD로 남긴다.

## 6. 개인정보·공개 경계

| 산출물 | 구역 | 허용 내용 | R04 전 공개 |
|---|---|---|---|
| reference bank manifest | D1 | pseudonymous row, immutable hash lineage | 금지 |
| selection trace | D1 | preregistered objective·dominance·tie 근거 | 금지 |
| aggregate report | A1 | 문장/선택/source/diversity 집계만 | 별도 승격 전 금지 |
| R04 raw label | R04 제한 구역 | R03 접근 금지 | 금지 |

A1에는 candidate row/ID, speaker ID, audio/object hash가 들어갈 수 없다.
D1에도 이름, raw speaker ID, audio/source/recording path, plaintext target,
transcript, 자유서술, R04 label/MOS/CMOS, learner outcome을 넣을 수 없다.
Audio content를 lineage 확인용으로 다시 읽는 것도 금지한다.

R04 완료는 자동 공개 승인이 아니다. R04 이후에도 별도 license/privacy/
evidence promotion이 필요하다.

## 7. Future artifact 계약

정적 package가 정의하는 future 산출물은 다음과 같다.

- D1 bank:
  `data/processed/references/r03/r03_reference_bank_manifest_v1.json`
- D1 selection trace:
  `artifacts/experiments/r03_reference_bank_selection_v1/r03_selection_trace_v1.json`
- A1 aggregate:
  `artifacts/experiments/r03_reference_bank_selection_v1/r03_reference_bank_aggregate_v1.json`
- immutable completion commit:
  `artifacts/experiments/r03_reference_bank_selection_v1/commit.json`
- registry:
  `artifacts/experiment_registry/r03-reference-bank-selection-v1/metadata.json`

현재 canonical R01/R02 completion SHA는 각각
`TBD_R01_COMPLETION`/`TBD_R02_COMPLETION`이다. Package는 위 경로를 쓰지
않는다. 모든 실제 실행은 별도 versioned
executor와 atomic staging/commit, no-overwrite, independent validation
계약을 추가해야 한다.

## 8. 현재 결론

R03 선택 문제를 구현 가능한 데이터·privacy·preregistration 계약으로
구체화했지만, actual R03은 0이다. R01/R02 실제 완료와 R03 사전등록
승인 전에는 candidate/audio/metric/selection/bank가 모두 0이며, reference
bank 품질이나 논문 결과를 주장할 수 없다.
