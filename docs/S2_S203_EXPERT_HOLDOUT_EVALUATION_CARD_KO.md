# WP5 S203 전문가 holdout one-shot 감사 평가 카드

최초 작성: 2026-07-29 KST  
상태: **정적 평가 계약만 준비 / 전문가 holdout·prediction·metric·결과 0**

## 1. 목적과 현재 주장 경계

S203의 목적은 서로 독립적으로 동결된 S1과 S2의 판정을 별도 전문가
holdout에서 한 번 결합해, S2가 S1의 심각한 오판을 실제로 걸러 내는지
감사하는 것이다. 이 카드는 실제 evaluator나 결과 카드가 아니다.
현재 허용되는 주장은 “선행자·동결·분리·사전등록·단일 접근을 강제하는
정적 fail-closed 계약을 구현했다”뿐이다.

현재 다음은 모두 0이다.

- S203 holdout attestation 및 행 단위 자료 접근
- S1/S2 holdout prediction 생성·읽기·결합
- 모델 재학습·재선택
- metric·CI·slice·gate 판정
- generation·registry·commit·논문 결과 승격

## 2. 불변 선행 순서

```text
S108 final research_candidate_passed commit
  -> 그 S108에 결합된 S200 readiness lock
  -> S108/S200에 결합된 S201 content_hard_gate_passed commit
  -> S202 completed/frozen seed 42·43·44 commits
  -> S202 aggregate frozen commit
  -> S202 public metadata validation
  -> S202 registry·artifact 전수 deep validation
  -> 사람 승인 S203 preregistration
  -> S203 expert-holdout attestation
  -> 별도 production evaluator
  -> sealed holdout 단일 transaction
```

S108, S200, S201, S202 중 하나라도 없거나 hash·binding·registry·artifact가
변조됐으면 preregistration, expert attestation, prediction, holdout data와
output 경로를 해석하지 않는다.

## 3. D07과 S203의 관계

D07은 S1의 gold·정렬·calibration 근거를 만드는 외부 전문가 재평가다.
S203은 동결된 S1과 별도 S2를 평가하는 최종 독립 holdout이다. 표본 수
1,000–2,000, 독립 평가자 2인과 adjudicator 1인이라는 상위 계획의
구조를 공유할 수는 있지만, 동일 자료나 동일 평가 cohort가 아니다.

다음을 명시적으로 금지한다.

- D07 item·speaker·prompt·rater를 S203 holdout에 재사용
- D07 label을 S2 학습·선택 또는 S203 gold로 복사
- D07 prediction이나 calibration output을 S203 평가 join에 사용
- D07 완료를 S203 attestation 또는 S203 완료로 대체

미래 attestation은 D07, S1 train/dev/stack/calibration/locked-test,
S2 train/validation과 S203 사이 item·speaker·prompt overlap 0과,
S1/D07/S2-development 평가 cohort와 S203 rater overlap 0을 각각
증명해야 한다.

## 4. Holdout과 locked-test 분리

`s2_expert_holdout`은 S1 `locked_test`와 다른 자료다. S108 locked-test의
행, label, prediction, threshold-tuning 결과를 S203 표본으로 재사용할 수
없다. S108에서 동결된 S1 모델과 S202에서 동결된 S2 ensemble만 새 S203
holdout을 평가할 수 있다.

Holdout open 전 다음이 모두 동결돼야 한다.

- S108 S1 checkpoint·fusion·calibrator·threshold
- S201 내용 hard-gate checkpoint·정규화·threshold·결합 규칙
- S202 세 seed checkpoint와 ensemble·3상태 decision rule
- S203 label severity, metric, CI, slice, multiplicity와 one-shot transaction

Holdout을 본 뒤 재학습, seed/ensemble 재선택, threshold 변경, metric
교체, slice 추가는 금지한다.

## 5. Prediction 결합의 유일한 역할

허용 join 열은 다음 다섯 개뿐이다.

- `item_id_sha256`
- `speaker_id_sha256`
- `frozen_s1_decision`
- `frozen_s2_decision`
- `adjudicated_s2_expert_gold`

S1/S2 prediction은 평가 입력으로만 결합한다. 학습 feature, model input,
target, pseudo-label, threshold selection, slice selection 또는 hard-negative
mining에 사용할 수 없다. Join 전에 두 모델은 이미 동결돼 있어야 하며,
결합 이후 어떤 모델 산출물도 갱신할 수 없다.

## 6. 문서에 근거가 있는 수치 gate

상위 구현 계획과 S200 계약에서 확인되는 수치는 다음뿐이다.

| Gate | 방향·값 | 현재 상태 |
|---|---:|---|
| 심각한 S1 오판 탐지 recall | `>= 0.80` | 정의 미확정 |
| S1 false-high-score 상대 감소 | `>= 0.30` | 정의 미확정 |
| 정상 표본 referral 비율 | `<= 0.15` | 정의 미확정 |
| S1–S2 불일치 표본 전문가 오류 적중률 | `>= 0.60` | 정의 미확정 |

Phase 3의 metamorphic expectation 통과율 `>= 0.95`도 문서화돼 있지만,
이는 expert holdout의 추가 primary metric으로 임의 승격하지 않고 별도
non-holdout 완료 gate로 보존한다.

숫자가 문서에 있다는 사실만으로 metric이 실행 가능해지지 않는다.
특히 “심각”, “오판”, “false-high”, “정상”, “referral”, “불일치”와
“전문가 오류”의 label·numerator·denominator 정의가 아직 없다.

## 7. 반드시 사전등록할 미해결 항목

다음은 근거 없이 채우지 않고 모두 `TBD_PREREGISTRATION`으로 둔다.

- primary metric family와 네 gate의 numerator/denominator
- 심각도 taxonomy·threshold와 정상/referral/불일치 정의
- missing·abstention·동률·중복 item 처리
- CI 방법·수준·resampling unit·반복 수·speaker clustering
- slice 차원, 최소 support, 작은 slice의 판정/공개 규칙
- 다중비교 보정과 gate statistic(point estimate/CI bound 등)
- 평가자 자격·blind·agreement gate
- adjudication trigger·절차·원평정 보존
- seed prediction aggregation과 S2 ensemble 판정
- 단 한 번으로 묶을 access family와 원자 transaction/recovery 규칙

이 중 하나라도 미해결이면 holdout attestation이나 data manifest를 열지
않는다.

## 8. 개인정보·반출·라이선스

- 원음과 전문가 자유서술: R0
- locator·원 ID·rater 대응표: R1
- 가명 label·prediction·join: D1
- 집계 metric·CI·failure summary: A1

행 단위 holdout, 가명 prediction, rater mapping, 원평정과 자유서술은
공개하지 않는다. 별도 동의, 기관 정책, 철회·보존·폐기, 접근 로그,
제한 재배포 조건이 attestation에 결합돼야 한다. Checksum은 식별과
무결성 근거이지 자료 재배포 허가가 아니다.

## 9. 완료 증거

S203 완료는 정적 preflight 통과가 아니다. 최소한 다음이 모두 필요하다.

1. 사람 승인·동결 preregistration
2. disjointness·governance·prior-access 0 attestation
3. 동결 S1/S2 prediction과 one-shot access receipt
4. 사전등록 metric·CI·cluster·slice·multiplicity report
5. 네 문서화 gate의 판정과 failure report
6. 원자 generation, completed registry, commit, 독립 deep validation
7. 집계 결과의 논문 evidence-map 승격

현재는 이 목록을 충족하지 않았으며 S203 0/2, WP5 실제 결과 0이다.
