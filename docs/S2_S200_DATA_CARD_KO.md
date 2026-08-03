# S2 S200 독립 감사기 데이터 카드 계약

최초 작성: 2026-07-29 KST  
상태: **정적 계약만 준비 / 실제 S2 데이터 0 / 모델 0 / 결과 0**

## 1. 목적과 현재 경계

이 데이터 카드는 최종 S1의 점수를 다시 학습하는 자료가 아니라, S1의
심각한 과대평가와 불확실성을 독립적으로 감사할 S2 자료의 역할·분할·
라벨·개인정보·라이선스 경계를 사전 고정한다.

현재 로컬에서 S2용 행 단위 자료, 전문가 holdout, Whisper/HuBERT
checkpoint, feature, prediction, metric은 읽거나 생성하지 않았다. 따라서
이 문서는 `implementation_readiness_contract_only`이며 S200 완료, S2의
경험적 독립성, WER gate 통과, auditor 학습 또는 배포 준비의 증거가
아니다.

머신 판독 정본은
`configs/experiments/s2_s200_independent_auditor_v1.yaml`이다. 이 문서와
config가 충돌하면 실제 실행 전 검토를 중단하고 둘을 함께 개정·재고정한다.

## 2. 선행 조건과 단계 DAG

```text
S108 final S1 research-candidate passed commit
  -> S200 독립성 계약·데이터 카드 lock
  -> S201 Whisper transcript consistency / WER hard gate
  -> S202 HuBERT 또는 별도 non-WavLM 표현 pairwise·ordinal auditor
  -> S203 동결된 S1/S2의 별도 전문가 holdout 감사
  -> E01·C03·predicted-alignment sensitivity
  -> 별도 deployment 판정
```

S108 aggregate-only dry-run은 S200 선행자가 아니다. S200은 단일 locked
batch까지 끝나고 `research_candidate_passed`가 기록된 미래 정본 commit만
허용한다. 현재 그 commit이 없으므로 full preflight는 외부 attestation이나
출력 경로를 열기 전에 실패해야 한다.

## 3. 모집단과 분석 단위

| 항목 | 계약 |
|---|---|
| 1차 모집단 | 중국어·일본어 L1 한국어 학습자 |
| 분석 단위 | 발화, 통계 cluster는 가명 화자 |
| S2 역할 | S1과 독립된 감사기; primary scorer가 아님 |
| S2 출력 | `approve` / `disagreement` / `uncertain` |
| S1 출력 사용 | S203에서 양쪽 모델을 동결한 뒤 동일-item 평가 join에만 허용 |
| 일반화 | 다른 L1·기관·녹음조건은 별도 자료와 평가 전에는 주장 금지 |

## 4. 분할과 독립성

S2의 예정 분할은 다음 세 개다.

| split | 역할 | S1 자료 재사용 |
|---|---|---|
| `s2_auditor_train` | S202 모델 학습 | 금지 |
| `s2_auditor_validation` | S202 모델·ensemble 사전 선택 | 금지 |
| `s2_expert_holdout` | S203 단일 최종 감사 | 금지 |

세 분할은 S1의 `train`, `dev`, `stack`, `calibration`, `locked_test`와
item·speaker가 겹치지 않아야 한다. prompt-disjoint challenge 결과도
별도로 보고한다. S2 전문가/평가자 cohort는 S1 labeler cohort와 겹치지
않아야 하며, 각각의 cohort hash가 달라야 한다. 모든 overlap count는
0이어야 한다.

## 5. 라벨과 feature 역할

S202 학습에 허용하는 라벨은 별도 S2 전문가가 만든 pairwise preference와
ordinal audit rating뿐이다. 다음은 학습·선택 label 또는 model feature로
사용할 수 없다.

- AI Hub target–realized operation에서 파생한 S1 silver phone label
- S1 score, logit, probability, confidence, threshold 또는 예측
- realized phone, alignment operation, error tag/count
- S1 WavLM embedding과 explicit feature
- oracle/predicted phone·word interval과 S1 alignment confidence
- phone label을 합성한 word proxy label

S201의 입력은 가명 item/speaker ID, audio waveform, 고정 prompt text,
language code로 제한한다. S202의 원시 입력은 가명 ID, audio waveform과
S201의 `content_gate_pass` routing signal뿐이다. 이 routing signal은
S202의 학습 feature가 아니다.

## 6. alignment provenance

- S1의 개발 결과가 oracle target boundary인지 predicted alignment인지
  S108 commit에 명시한다.
- oracle-only S1 결과를 predicted-alignment 또는 deployment 결과라고
  표현할 수 없다.
- S201/S202는 `alignment_free_utterance` 계약을 사용한다.
- Whisper timestamp, S1 oracle boundary, S1 predicted boundary와 alignment
  confidence를 S2 feature로 넣지 않는다.
- 향후 별도 monotonic matching을 도입하려면 S2 전용 provenance·config·
  sensitivity를 새로 사전 고정하고 S1 alignment와 같은 이름으로 부르지
  않는다.

## 7. S201 내용 일치 hard gate

Whisper의 역할은 transcript consistency와 WER hard gate로 제한한다.
세밀한 phone·운율 감사 전체를 Whisper 하나에 맡기지 않는다.

현재 고정된 것은 다음과 같다.

- sampling 없는 `temperature=0`
- content gate를 S202 decision 전에 적용
- content gate 실패를 S202 점수로 구제 금지
- FLEURS ko-KR·ZEROTH-Korean은 외부 ASR 평가 전용이며 학습자 오류
  감독학습에 사용 금지
- 공식 Whisper source revision과 MIT license 원문 hash

정확한 checkpoint revision, 한국어 정규화 규칙과 WER threshold는 아직
실제 자료를 보지 않은 별도 S201 config에서 사전 고정해야 한다. 값이
미해결인 동안 S201 실행은 금지된다. 이는 임의 threshold를 결과처럼
기입하지 않기 위한 의도적 blocker다.

## 8. S202 별도 표현 감사기

우선 후보는 HuBERT이며 WavLM은 금지한다. 다른 SSL 표현을 쓰려면
`separately_approved_non_wavlm_ssl`로 문서화하고 checkpoint·source·license
hash를 다시 고정한다.

- 목적함수: pairwise preference + ordinal audit
- seed: 42, 43, 44
- seed별 checkpoint와 prediction을 분리 보존
- holdout을 열기 전에 ensemble 정책 고정
- exact checkpoint와 ensemble 정책이 미해결인 동안 실행 금지
- S201/S202 trainer는 S200 패키지에 포함하지 않음

## 9. S203 전문가 holdout과 지표

예정 표본은 1,000–2,000개 발화, 독립 평가자 2인, adjudicator 1인이다.
평가자는 S1/S2 출력에 blind이고, 원평정은 adjudication 뒤에도 보존한다.
기관 승인, 동의·철회, 보존·폐기와 제한 재배포 계획이 모두 확인돼야
aggregate attestation을 `ready`로 만들 수 있다.

| primary metric | gate |
|---|---:|
| 심각한 S1 오판 탐지 recall | ≥ 0.80 |
| S1 false-high-score 상대 감소 | ≥ 0.30 |
| 정상 표본 referral | ≤ 0.15 |
| S1/S2 불일치의 전문가 오류 적중 | ≥ 0.60 |

Gate statistic은 point estimate다. 화자 cluster 5,000회 bootstrap,
seed `20260729`, 95% CI도 함께 보고한다. 네 gate를 모두 만족해도 S203은
deployment 승인을 만들지 않는다. E01 metamorphic, C03 coaching,
predicted-alignment sensitivity와 별도 운영 승인이 남는다.

## 10. 개인정보·반출

| zone | S2 예 |
|---|---|
| R0 | 원음, 전문가 자유서술 |
| R1 | locator, reviewer 대응표 |
| D1 | 가명 feature·label·prediction |
| A1 | 집계 report·data card |
| C1 | 코드·config·문서 |

허용 ID는 연구별 namespace를 적용한 64자리 SHA-256 item/speaker/reviewer
ID뿐이다. 원시 ID·경로·전사·자유서술·행 단위 자료의 공개 반출은
금지한다. 모델 개발자는 holdout gold를 모든 모델·정책의 freeze 전에는
볼 수 없다.

## 11. 라이선스

- AI Hub 71490: 승인 기반 제한 자료, S1 context만 기록하며 S2 학습 재사용
  금지
- S2 전문가 자료: 별도 동의와 기관 정책, 재배포 금지
- Whisper code/weight: 현재 확인된 공식 범위는 MIT; 실제 checkpoint를
  받기 전 revision·license 재감사
- fairseq/HuBERT code/pretrained model: 현재 확인된 공식 범위는 MIT;
  실제 checkpoint를 받기 전 재감사
- FLEURS ko-KR·ZEROTH-Korean: CC-BY-4.0, attribution 필요, S201 외부 ASR
  평가 전용

Checksum은 재현 식별자이며 자료·weight의 재배포 허가를 뜻하지 않는다.

## 12. 현재 결손

1. S108 final research-candidate completed commit 없음
2. 별도 S2 expert dataset·holdout과 외부 attestation 없음
3. S201 exact checkpoint·정규화·WER threshold 미고정
4. S202 exact checkpoint·ensemble 정책 미고정
5. S201/S202 trainer·checkpoint·prediction 없음
6. S203 metric·CI·실패 사례 없음

위 결손이 해소되기 전 허용되는 유일한 주장은 “S200 정적 구현 준비 계약을
검증했다”이다.
