# WP7 R05 GRPO go/no-go data·model·license·safety card

상태: **production 정적 판정 계약만 구현 / 실제 GRPO 진입 판정·학습 0**

기준일: 2026-07-29 KST

## 1. 범위와 비범위

R05는 모델 학습 stage가 아니다. S108, S203, R04, H00, H04의 실제
완결 증거가 모두 생긴 뒤, 독립 사전등록과 별도 인간 승인을 거쳐 정확히
다섯 gate를 A1 집계자료만으로 판정하기 위한 정적 계약이다.

이 package에는 다음 기능이 없다.

- GRPO, F5, StyleTTS 학습·추론·reward 실행
- 모델·가중치 download, network, GPU 접근
- audio, rating, participant row, protected/locked data 접근
- metric 재계산, 효과 추정, 실제 결과 수치 생성
- decision report·registry·commit 작성 또는 publication
- 인간 승인 생성, 배포 승인 또는 완료 주장

`scripts/prepare_r05_grpo_go_no_go.py`는 config-only 검증과 read-only
preflight만 제공한다. 별도 실행자나 publisher로 사용할 수 없다.

## 2. 고정된 다섯 gate

논문·실행계획이 지지하는 진입 조건만 고정했다.

| Gate | 고정 조건 | 현재 상태 |
|---|---|---|
| S1 phone | F1 ≥ 0.80 | 실제 final S108 completion 없음 |
| S1 utterance | ICC ≥ 0.70 | 실제 final S108 completion 없음 |
| S2 | severe error recall ≥ 0.80 | 실제 S203 expert holdout 없음 |
| reference | multi-reference blind superiority | 실제 R04 blind 결과 없음 |
| 학습 효과 | actual pilot learning-effect signal | H00/H04 actual 결과 없음 |

정확한 artifact pointer, metric/estimand ID, population, alignment, CI,
decision rule은 근거가 없으므로 `TBD_PREREGISTRATION`이다. Blind gate의
comparator·estimand·margin과 pilot gate의 estimand·effect-size
definition·signal rule도 모두 미정이다. 이를 추론하거나 임의 수치로
채우면 안 된다.

H01 power/preregistration 문서는 planning artifact다. 실제 participant,
pre/post/retention, blind rating과 H04 mixed-model 결과를 대신할 수 없다.
자동 점수도 blind superiority나 learning-effect gate를 대신할 수 없다.

## 3. 데이터 구역과 최소 접근

| 구역 | R05 허용 | 금지 |
|---|---|---|
| C1 | config, schema, template, contract, 근거 문서 exact pin | 환경파일·credential |
| A1 | predecessor commit/registry와 집계 gate evidence | row ID, PII, free text |
| D1/R0/R1 | 없음 | participant, listener, speaker, candidate row, rating, audio |
| model/GPU | 없음 | model path/weight/download/network/GPU/reward/training |

Public decision schema는 `A1_aggregate_only`만 허용한다. Row-level 표시,
직접 식별자, free text, training/download 실행 표시는 모두 `false`로
고정한다. 재귀 validator는 row/item/participant/listener/speaker/candidate
ID, 이름·연락처, transcript·target text, audio/model path와 raw row
container를 거부한다.

Decision report는 다섯 aggregate gate record, predecessor SHA-256,
preregistration·authorization SHA-256과 assurance boolean만 가진다.
개별 평가값이나 식별 가능한 자유기술은 공개할 수 없다. Publication은
R05 GO와 별개이며 별도 promotion 없이는 금지다.

## 4. 선행 증거와 순서

순서는 다음과 같이 고정돼 있으며 처음 누락·불일치에서 종료한다.

1. S108 final completion deep validation
2. S203 actual expert-holdout completion deep validation
3. R04 actual blind-evaluation completion deep validation
4. H00 actual human-research approval
5. H04 actual pilot statistical report
6. independent frozen R05 preregistration
7. independent human authorization
8. A1 aggregate-only five-gate decision

누락은 pass가 아니다. 사전등록된 정책에 따라 `BLOCKED` 또는 `NO_GO`,
명시적 실패는 사전등록된 `NO_GO` 또는 `BLOCKED`로만 처리한다. `GO`는
다섯 gate와 모든 model/license/reward/safety assurance가 모두 pass일
때만 가능하다.

Canonical config의 S108 completion SHA는 `TBD_S108_COMPLETION`이다.
따라서 현재 full preflight는 S108 artifact path도 resolve하지 않고
종료한다. S203/R04/H00/H04/preregistration/authorization에는 접근하지
않는다.

## 5. 모델 정체와 라이선스

현재 exact GRPO 대상 모델은 선택되지 않았다.

| 항목 | 현재 증거 | R05 요구 |
|---|---|---|
| CosyVoice | 공식 저장소의 CosyVoice2 GRPO recipe 공개 존재만 확인 | 실제 프로젝트 실행·효과 근거로 사용 금지 |
| F5 공식 artifact | 확인된 특정 artifact의 code MIT, pretrained weight 선언 CC-BY-NC 계열 | 제3자 artifact나 “F5R-TTS”에 일반화 금지 |
| exact model | family/artifact/revision/hash 미정 | 독립 승인 전 exact immutable identity |
| code license | 미정 | code license ID와 evidence SHA |
| weight license | 미정 | weight license ID와 evidence SHA |
| training-data license | 미정 | 허용 범위와 evidence SHA |

Code license와 weight license는 동일하다고 추정하지 않는다. Model-card
metadata를 standalone LICENSE로 간주하지 않는다. Exact “F5R-TTS”
artifact와 GRPO 구현 정체가 고정되기 전에는 download·실행·학습 승인을
낼 수 없다.

## 6. Reward와 reward-hacking 경계

실행계획의 reward는 다음 목적을 하나의 근거 없는 scalar로 축약하지
않는다.

- content accuracy hard gate
- key-phone hard gate
- artifact hard gate
- prosody fit
- naturalness
- pedagogical imitation

GRPO 전에 best-of-N 또는 rejection sampling으로 reward 방향성을
검증해야 한다. Exact reward implementation/hash, 방향성 판정,
reward-hacking audit protocol/pass rule, adversarial slice, safety
protocol/pass rule과 rollback rule은 모두 사전등록 대상이다.

Automatic score가 좋아져도 blind comprehensibility가 좋아지지 않으면
Loop A reward를 수정해야 한다. 실제 학습 효과가 없으면 TTS 최적화
진입을 정당화할 수 없다. 두 경우 모두 posthoc으로 gate를 낮추거나
blind/pilot evidence를 자동 점수로 치환할 수 없다.

## 7. 인간 승인과 안전

GO/no-go preregistration과 인간 authorization은 서로 다른 artifact다.
Authorization은 config, preregistration, S108/S203/R04/H00/H04 exact
hash를 모두 bind해야 한다. 다음 검토가 전부 명시적으로 완료돼야 한다.

- 다섯 gate의 실제 evidence
- exact model identity
- code·weight·training-data license
- reward directionality와 reward-hacking audit
- safety, privacy/retention, rollback

정적 template은 모든 assurance와 실행 scope를 `false`로 두며 승인을
만들지 않는다. 최종 authorization이 GO/no-go 판정을 허용하더라도 model
download, GPU, training, deployment, publication 권한은 각각 별도
범위로 남는다.

## 8. 무결성과 publication

Future completed bundle validator는 commit→registry→report의 path/hash/
schema/status/binding을 재계산한다. Symlink chain, path traversal, hash
drift, duplicate/extra field, non-finite value, no-overwrite 위반과
검증 중 변경(TOCTOU)을 fail-closed한다. Registry에는 A1 decision report
한 개만 허용한다.

현재 실제 값은 모두 0이다.

- S108/S203/R04/H00/H04 완결 증거: 0
- 승인·동결 R05 preregistration와 인간 authorization: 0
- actual metric/effect/decision report: 0
- model/download/network/GPU/reward/GRPO/F5/StyleTTS 실행: 0
- output/registry/commit/publication/completion claim: 0

따라서 이 문서는 GRPO의 안전성·효과·라이선스 적합성 또는 연구 완료를
주장하지 않는다. 구현된 것은 그 주장을 하기 전에 통과해야 할 정적
검증 경계뿐이다.
