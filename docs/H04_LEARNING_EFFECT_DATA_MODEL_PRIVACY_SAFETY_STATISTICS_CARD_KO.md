# H04 학습 효과 분석 데이터·모델·개인정보·안전·통계 카드

최종 갱신: 2026-07-29 KST
상태: **production static readiness 구현, 실제 H04 분석·효과 추정·보고서 0**

## 1. 용도와 증거 경계

이 카드는 H04 학습 효과 분석의 데이터 계약과 통계 분석 경계를 고정한다.
현재 패키지는 공개 가능한 C1 문서와 A1 집계 schema·영수증만 검증하는
읽기 전용 preflight다. 다음 작업을 수행하지 않는다.

- 참여자·평정자 행, 음성, 전사, 가명키, restricted locator 또는 D1 분석
  자료 읽기
- mixed model, 다중대치, MNAR, complete-case, effect size 또는 CI 계산
- 모델·GPU·네트워크 사용
- 통계 보고서·registry·commit·failure artifact 쓰기
- 결과 공개 또는 H04 완료 주장

따라서 이 패키지의 정적 준비도는 교육 효과, 안전성, 통계적 유의성 또는
논문 결과를 뜻하지 않는다.

## 2. 비순환 선행 DAG

H01의 최종 표본수는 empirical nuisance parameter가 필요하므로,
15–20명/arm feasibility pilot과 powered confirmatory bundle을 같은
H02/H03 영수증으로 취급하면 순환이 생긴다. H04 계약은 두 단계를 다음과
같이 분리한다.

```text
H00 실제 공식 승인
  -> 별도 H02/H03 feasibility pilot 완료
     (15–20명/arm, blind reliability, aggregate nuisance, efficacy 결론 금지)
  -> H01 final N 수동 lock
     (pilot nuisance + 독립 target effect + full simulation + 통계/PI 승인)
  -> H04 SAP 공개 승인·동결
  -> powered confirmatory H02/H03 pre/post/retention blind bundle 완료·data lock
  -> 독립 분석자 attestation
  -> 별도 restricted D1 통계 executor
  -> A1 aggregate statistical report
  -> immutable H04 completion receipt
```

교차 결합 규칙은 다음과 같다.

- feasibility receipt는 H00 approval·attestation SHA를 포함한다.
- H01 lock은 feasibility receipt SHA와 그 receipt가 가리키는 aggregate
  nuisance report SHA를 모두 정확히 포함한다.
- confirmatory H02/H03 bundle은 feasibility receipt, H01 lock, frozen SAP
  SHA를 포함한다.
- confirmatory arm별 무작위 배정 수는 H01에서 수동 잠근 final N과 정확히
  일치해야 한다.
- feasibility receipt SHA를 confirmatory bundle SHA로 재사용하면 path
  resolution 전에 실패한다.
- SAP는 confirmatory data lock, condition unmask, 분석보다 먼저 동결한다.

## 3. 데이터 구역과 artifact

| 구역 | 허용 내용 | 금지 내용 |
|---|---|---|
| C1 | 공개 protocol·방법 문서, schema, template | 기관 제한 원문, 개인정보 |
| A1 | SHA lineage, arm/time별 집계 수, 집계 효과·CI, coded-reason receipt | 참여자/평정자 행, 개별 점수, 음성·전사, raw locator, free text |
| D1 | 승인 범위 안의 가명화 human-research 분석 자료 | 이 static package의 접근 |

정적 package가 정의하는 artifact는 다음과 같다.

| 파일 | 역할 |
|---|---|
| `h04_feasibility_pilot_completion_v1.schema.json` | H00 이후, H01 이전 feasibility pilot 집계 영수증 |
| `h04_h01_final_sample_size_lock_v1.schema.json` | empirical nuisance 기반 H01 수동 final N lock |
| `h04_statistical_analysis_plan_v1.template.json` | 실제 data 접근 전 동결할 fail-closed SAP |
| `h04_h02_h03_blind_bundle_completion_v1.schema.json` | powered confirmatory blind longitudinal bundle 영수증 |
| `h04_analyst_attestation_v1.template.json` | 독립·blind 분석자와 분석 순서 attestation |
| `h04_learning_effect_aggregate_input_v1.schema.json` | score/effect-free A1 분석 승인 manifest |
| `h04_learning_effect_statistical_report_v1.schema.json` | 실제 집계 통계 결과 |
| `h04_learning_effect_completion_v1.schema.json` | input/report SHA와 크기를 고정한 completion receipt |

모든 JSON 계약은 exact key set, duplicate key 거부, non-finite 거부,
canonical JSON binding SHA, workspace-relative safe path, symlink 거부,
no-overwrite를 적용한다. 정적 source는 SHA-256 pin과 TOCTOU 재검증을
사용한다.

## 4. Feasibility pilot과 H01 표본수

Feasibility receipt는 비교별 arm당 15–20명만 허용한다. 이 범위는
기술적·운영적 feasibility 범위이며 powered efficacy 표본수가 아니다.
receipt에는 다음 완료 증거가 필요하다.

- 승인된 동의·음성 범위 안의 실제 모집·무작위 배정
- 2주 intervention과 pre/post/retention 수집
- condition·timepoint·identity blind인 독립 평가자 2인 이상
- aggregate nuisance parameter와 blind-rater reliability report
- missingness/attrition, delivery fidelity, adverse feedback, data-lock receipt
- 참여자 행·식별자·개별 점수·음성·전사·locator·free text·관측 효과 0

H01 final N lock은 관측 pilot treatment effect를 power 입력으로 쓰지 않는다.
별도 최소 관련 차이, full mixed/cluster simulation, 통계 담당자와
연구책임자의 수동 승인, confirmatory 모집 전 공개 사전등록이 필요하다.
자동 final N lock은 금지한다.

## 5. Primary estimand와 model

- Primary population: 무작위 배정된 모든 참여자의 ITT
- Primary estimator: 모든 관측 ITT 자료를 쓰는 likelihood-based mixed model
- Fixed formula: `score ~ condition * time + L1 + baseline`
- Random intercept: participant, sentence, rater
- Primary outcome: blind expert comprehensibility 1–5

Primary family:

1. C3−C0 pre→post
2. C5−C3 pre→post

Key retention family:

1. C3−C0 pre→retention
2. C5−C3 pre→retention

네 contrast 모두 raw 1–5 difference, standardized effect와 95% CI를
보고해야 한다. Secondary family는 phone accuracy와 fluency의
post/retention을 두 비교에서 모두 보고한다.

## 6. Missingness, PP와 multiplicity

Primary missingness 처리는 likelihood model의 all-observed ITT다.
사전 지정 sensitivity는 중복 없는 정확한 목록이어야 한다.

1. MAR multilevel multiple imputation
2. delta-adjusted pattern-mixture MNAR
3. complete-case
4. 배정 노출 80% 이상 per-protocol sensitivity

LOCF와 post-randomization exclusion은 SAP, 분석자 attestation, 결과
report의 세 단계에서 모두 금지한다. PP는 sensitivity일 뿐 primary
claim에 사용할 수 없다.

양측 familywise alpha는 0.05다. `primary_post`, `key_retention`,
`secondary`를 별도 family로 두고 각 family 안에서 Holm을 적용한다.
결과 validator는 raw p-value에서 Holm adjusted p-value와 reject flag를
정확히 다시 계산한다.

## 7. 결과 완전성과 진단

실제 aggregate report는 다음을 모두 포함해야 한다.

- 네 confirmatory contrast와 정확히 여덟 secondary test
- 네 sensitivity 각각의 네 confirmatory contrast
- primary model·optimizer convergence, singular/boundary fit, support
  violation, fallback 사용 여부와 진단 receipt
- 두 비교의 모든 arm × pre/post/retention 결측 수·비율·coded reason receipt
- 두 비교의 모든 arm별 withdrawal, adverse feedback, ceiling, floor
- 각 분모가 confirmatory H02/H03의 잠긴 arm N과 일치하는 산술 검증

Primary model 미수렴, optimizer 미수렴, singular/boundary fit,
support violation, 미등록 fallback은 completion report로 수용하지 않는다.
이 경우 실패를 숨기지 않고 SAP에 따른 amendment·재분석 절차를 거쳐야 한다.

## 8. 실제 SAP에서만 확정할 항목

다음 값은 현재 모두 `TBD_PREREGISTRATION`이다. 실제 blind SAP와 자료가
없는데 임의로 선택하지 않는다.

- software와 exact version
- model family
- ordinal 대 linear primary 선택
- outcome support 처리
- MNAR delta grid
- MI 횟수와 imputation model
- optimizer와 convergence rule
- CI method
- bootstrap/resampling support
- fallback policy와 PP exposure denominator/window

이 필드가 하나라도 unresolved면 실제 SAP deep validation은 실패한다.

## 9. 개인정보·안전

- 공개 artifact에서 이름, 이메일, 전화번호, 주소, legal name,
  participant/rater/reviewer ID, 개별 점수·평정, 음성·전사, raw/restricted
  path·locator·URL·filename, free text·comment·note를 재귀적으로 거부한다.
- 철회 후 기존 자료의 보존·삭제는 승인된 동의서 선택과 H00 범위에 따른다.
- adverse feedback을 효과 지표와 별도로 arm별 보고한다.
- 실제 D1 executor 권한은 이 package가 부여하지 않는다.
- aggregate report도 자동 공개되지 않으며 독립 검토 전 publication flag는
  `false`다.

## 10. 완료 판정

H04 연구 완료는 다음을 모두 실제로 충족한 뒤에만 가능하다.

1. H00 공식 승인과 current scope deep validation
2. 별도 feasibility pilot 완료와 A1 receipt
3. H01 pilot-informed final N 수동 lock
4. confirmatory data lock 전 fully resolved SAP 공개 승인·동결
5. H01 final N과 일치하는 powered confirmatory H02/H03 blind bundle
6. 독립 분석자 attestation
7. 승인된 별도 D1 executor의 실제 통계 분석
8. strict aggregate input·report validation
9. exact artifact SHA·크기와 privacy assurance를 담은 completion receipt
10. 독립 결과 검토 뒤 별도 publication 승인

현재는 1번부터 실제 증거가 없으므로 실제 참여자·통계·효과·report와 H04
획득은 모두 0이다.
