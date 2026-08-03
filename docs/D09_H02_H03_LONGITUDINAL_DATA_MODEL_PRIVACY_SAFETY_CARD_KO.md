# D09/H02/H03 종단 학습자 데이터 모델·개인정보·안전 카드

## 1. 범위와 현재 상태

이 카드는 WP1 D09 및 WP9 H02/H03의 실제 종단 학습자 데이터 수집을
위한 **production-static 계약**을 설명한다. 현재 패키지는 사람에게
접촉하거나 데이터를 수집하는 실행기가 아니다. 다음 행위는 모두 0이며
허용되지 않는다.

- 포털·기관 접촉, 모집, 동의, 보상 집행
- 참가자·세션·마이크·오디오·D1 행 접근
- 무작위배정, 모델·GPU·API·네트워크 호출
- 진단·코칭·참조음성·중재 전달
- 맹검 평정, 지표·통계·효과 추정
- 결과·registry·commit 작성, 논문 반영, 완료 주장

정적 계약은 승인과 선행 산출물이 실제 hash로 고정되기 전 보호 경로를
해석하지 않고 실패한다. tracker는 source pin 대상이 아니며 이 패키지는
tracker를 읽거나 수정하지 않는다.

## 2. 연구 phase와 주장 경계

### Phase A: feasibility

- 실제 H00 승인 이후에만 시작한다.
- `C3−C0`, `C5−C3` 각각 arm당 15–20명이다.
- 14일 중재와 `pre`, `post_at_2_weeks`,
  `retention_4_weeks_after_post` 수집을 수행한다.
- 조건·시점·참가자 신원에 맹검된 독립 평정자 2명 이상을 사용한다.
- 효능 추정·확증 주장을 하지 않는다.
- 관찰된 pilot treatment effect를 확증 표본수 결정에 사용하지 않는다.
- 완료 A1 handoff는 정확히
  `voice_coach.h04_feasibility_pilot_completion.v1`이다.

### Phase B: confirmatory

다음 순서를 모두 만족한 뒤 새 참가자를 별도로 모집한다.

1. 실제 feasibility 완료
2. nuisance parameter와 전체 simulation을 사용한 H01 수동 final-N lock
3. 공개 preregistration
4. 확증 모집 전 동결된 H04 SAP
5. 확증 phase용 별도 preregistration·manual·execution authorization

pilot의 participant key, audio, rating, 행, receipt를 복사·clone·재사용할
수 없다. 완료 A1 handoff는 정확히
`voice_coach.h04_h02_h03_blind_bundle_completion.v1`이다.

## 3. 두 비교는 서로 다른 participant-unit 병렬 비교다

한 phase 안에서도 공유 C3 arm을 만들지 않는다.

| 비교 | 허용 조건 | 배정 | 참가자 집합 |
|---|---|---|---|
| stage 1 `c3_minus_c0` | `C0`, `C3` | participant 1:1 | stage 1 전용 |
| stage 2 `c5_minus_c3` | `C3`, `C5` | participant 1:1 | stage 2 전용 |

두 비교 사이 participant/audio/rating intersection과 shared-C3 participant
intersection은 모두 0이어야 한다. 별도 randomization receipt와 집합
commitment hash를
`d09_h02_h03_comparison_stage_separation_receipt_v1.schema.json`에
기록한다.

stage 2 활성화는 다음만 볼 수 있다.

- H00 승인 범위와 현재성
- 안전·개인정보·중재 운영 준비
- stage 2 delivery predecessor 준비

stage 1의 비눈가림 outcome/effect는 활성화 시점에 이용 가능해서도,
접근되어서도 안 된다. aggregate receipt는 stage 1 randomization 종료,
stage 2 활성화 승인, stage 2 randomization 개시의 시간 순서와 활성화
전 unblinded-effect access count 0을 결합한다.

## 4. delivery DAG

실제 학습자에게 조건을 전달하려면 각 선행 artifact의 실제 completion
hash와 독립 deep-validation receipt가 필요하다.

### C0

시간·연습량이 일치하는 active control이다. 자동 진단·코칭, 다중 참조,
coached reattempt를 전달하지 않는다.

### C3 gate

`S108 + E01 + C02 + E02 + C03`

- S108: 선택 모델·checkpoint·calibrator·threshold policy
- E01: actual metamorphic robustness pass
- C02: deterministic coaching selection
- E02: confidence/OOD/API safe-action guard
- C03: expert coaching audit pass

### C5 gate

`C3 gate + E00 + R03 + R04`

- E00: closed-loop reference handoff
- R03: selected reference bank
- R04: actual blind multi-reference evaluation pass

R03의 현재 상태 `r03_selected_pending_r04`만으로 C5를 승인할 수 없다.
E00이 D08 권리 검증을 transitively 포함하더라도 D09 aggregate receipt에
E00의 D08/R03 rights-lineage hash를 명시적으로 결합한다.

현재 모든 실제 completion은 TBD이므로 predecessor artifact path read는
0이다.

## 5. 데이터 zone과 key 분리

| zone | 내용 | 공개 여부 |
|---|---|---|
| R0 | direct identity ↔ pseudonym key | 제한, D1과 물리·권한 분리 |
| R1 | 오디오·객체 locator | 제한, A1에 금지 |
| D1 | participant/session/audio/QC/randomization/exposure/rating/disposition 행 | 제한 |
| A1 | count·set commitment·completion·separation receipt | 행·오디오 없이 제한적 공유 |
| C1 | 정적 config/schema/template/docs | 공개 가능 |

D1에는 직접 식별자, raw locator, 자유 텍스트를 넣지 않는다. pseudonym
key는 R0에서만 관리하고 D1/A1 validator는 identity key나 locator를
해석하지 않는다.

## 6. D1 strict schema

모든 schema는 JSON Schema Draft 2020-12, root
`additionalProperties: false`이다.

1. `d09_h02_h03_participant_v1.schema.json`
   - phase, comparison, 허용 condition, consent receipt, L1·baseline strata
   - participant blinding 주장은 항상 false
2. `d09_h02_h03_session_v1.schema.json`
   - baseline/intervention/post/retention session과 coded deviation receipt
3. `d09_h02_h03_audio_v1.schema.json`
   - fixed practice/transfer item, content commitment, rating mask
4. `d09_h02_h03_audio_qc_v1.schema.json`
   - frozen QC policy, decode/sample-rate/duration/clipping/SNR
   - post-randomization QC 실패는 제외하지 않고 missingness로 보존
5. `d09_h02_h03_randomization_v1.schema.json`
   - participant 1:1, L1+baseline stratification, allocation concealment
   - comparison↔condition과 stage↔delivery gate를 fail-closed
6. `d09_h02_h03_intervention_exposure_v1.schema.json`
   - manual version/hash, 모든 gate hash, assigned/received exposure, adherence
7. `d09_h02_h03_blind_rating_v1.schema.json`
   - blind code, 1–5 outcome, condition/time/identity blind flag
8. `d09_h02_h03_disposition_v1.schema.json`
   - withdrawal, missingness, deviation, adverse feedback를 coded receipt로 보존
   - randomized participant는 ITT에 유지

## 7. 고정 평가와 평정

- 중재 기간은 정확히 14일이다.
- 평가 시점은 pre, 2주 종료 post, post 4주 뒤 retention이다.
- practice/transfer item card를 preregistration에 hash로 고정한다.
- transfer item은 학습 중 노출하지 않는다.
- 조건·시점·참가자 신원에 맹검된 독립 평정자 최소 2명이 평정한다.
- 참가자 맹검은 주장하지 않는다.
- rating anchor, rater qualification, blocking/randomization, adjudication,
  minimum support 규칙은 승인된 preregistration 전까지 TBD이며 자동
  추정하지 않는다.

## 8. 동의·철회·보존·안전

실제 authorization은 다음 workflow ID와 승인 receipt를 모두 요구한다.

- 동의와 철회
- 보상 일정과 미성년자 정책
- 보존·삭제 일정
- R0/R1/D1/A1 key·locator 분리
- incident response와 pause/stop
- adverse feedback taxonomy
- human escalation

철회, nonadherence, missing follow-up, protocol deviation, adverse feedback는
삭제하거나 결과 기반으로 제외하지 않는다. 승인된 consent의 삭제 요청
처리는 별도 보존·삭제 정책을 따르며 분석 disposition에는 coded aggregate
흔적을 남긴다.

## 9. 통계·출판 경계

이 패키지의 execution authorization은 모집·동의·수집·중재·맹검 평정과
제한된 D1 처리만 열 수 있다. 통계·효과 분석과 출판은 false이다.
확증 분석은 H04 frozen SAP, locked D1 aggregate input, 독립 analyst
attestation의 별도 계약을 통과해야 한다.

feasibility artifact에는 observed effect estimate가 없어야 하며 efficacy
claim을 할 수 없다. 정확한 eligibility, recruitment, N, QC threshold,
exposure denominator, rating anchor, adjudication, support rule은 실제
승인·동결 전까지 TBD로 유지한다.
