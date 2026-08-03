# S203 전문가 holdout 감사 논문 amendment template

상태: **PENDING — 실제 S203 결과로 사용 금지**

이 문서는 S203 정적 준비 계약이 실제 holdout 결과처럼 서술되는 것을
막기 위한 빈 템플릿이다. `TBD_*`가 하나라도 남거나 정본 commit의 독립
deep validation이 없으면 본문·초록·결론·표에 승격하지 않는다.

## A. 불변 provenance

| 항목 | 값 |
|---|---|
| S108 final commit SHA-256 | `TBD_S108_COMMIT_SHA256` |
| S200 readiness lock SHA-256 | `TBD_S200_LOCK_SHA256` |
| S201 completed commit SHA-256 | `TBD_S201_COMMIT_SHA256` |
| S202 aggregate completed commit SHA-256 | `TBD_S202_COMMIT_SHA256` |
| S202 seed commit SHA-256 42/43/44 | `TBD_S202_SEED_COMMIT_SHA256S` |
| S203 preregistration SHA-256 | `TBD_S203_PREREGISTRATION_SHA256` |
| S203 holdout attestation SHA-256 | `TBD_S203_ATTESTATION_SHA256` |
| S203 one-shot access receipt SHA-256 | `TBD_S203_ACCESS_RECEIPT_SHA256` |
| S203 result commit SHA-256 | `TBD_S203_RESULT_COMMIT_SHA256` |
| 독립 validation receipt SHA-256 | `TBD_S203_VALIDATION_SHA256` |

## B. Holdout 분리·접근

| 항목 | 결과 |
|---|---:|
| item overlap | `TBD_ITEM_OVERLAP` |
| speaker overlap | `TBD_SPEAKER_OVERLAP` |
| prompt overlap | `TBD_PROMPT_OVERLAP` |
| rater overlap | `TBD_RATER_OVERLAP` |
| D07 item/rater overlap | `TBD_D07_OVERLAP` |
| S1 locked-test overlap | `TBD_LOCKED_OVERLAP` |
| prior holdout access count | `TBD_PRIOR_ACCESS_COUNT` |
| final access transaction count | `TBD_FINAL_ACCESS_COUNT` |

모든 overlap과 prior access는 0, final access transaction은 정확히 1이어야
한다. S1/S2 retrain·reselection·threshold change count도 모두 0이어야 한다.

## C. Label·metric·통계 사전등록

- 심각도 taxonomy·threshold: `TBD_SEVERITY_DEFINITION`
- 정상/referral/false-high/불일치 정의: `TBD_DECISION_DEFINITIONS`
- adjudication protocol: `TBD_ADJUDICATION`
- primary metric family: `TBD_PRIMARY_METRIC_FAMILY`
- numerator/denominator: `TBD_NUMERATOR_DENOMINATOR`
- CI method·level: `TBD_CI`
- resampling·cluster unit·replicates: `TBD_RESAMPLING`
- slice·minimum support·small-slice policy: `TBD_SLICE_POLICY`
- multiplicity control: `TBD_MULTIPLICITY`
- gate statistic: `TBD_GATE_STATISTIC`
- one-shot access family·transaction: `TBD_ONE_SHOT_PROTOCOL`

## D. 집계 결과 표

| Metric | Estimate | 95% CI | Gate | Decision |
|---|---:|---:|---:|---|
| serious S1 misjudgment recall | `TBD` | `TBD` | `>=0.80` | `TBD` |
| false-high-score relative reduction | `TBD` | `TBD` | `>=0.30` | `TBD` |
| normal-sample referral rate | `TBD` | `TBD` | `<=0.15` | `TBD` |
| disagreement expert-error hit rate | `TBD` | `TBD` | `>=0.60` | `TBD` |

Slice/failure 결과: `TBD_SLICE_AND_FAILURE_REPORT`

Metamorphic expectation `>=0.95`는 별도 non-holdout Phase-3 결과로 보고하며
위 expert-holdout primary 표에 섞지 않는다.

## E. 허용 결과 문장

> 동결된 S1과 S2를 서로 분리된 전문가 holdout에서 사전등록된 단일
> transaction으로 평가했다. 네 primary gate의 estimate와 CI는 표 X와
> 같았으며, 사전등록된 gate statistic 기준으로 `TBD_DECISION`이었다.

위 문장은 모든 `TBD` 치환, 정본 artifact hash 검증, 독립 감사와
evidence-map 반영 후에만 사용할 수 있다. Static preflight만으로 “S2가
S1 오류를 탐지했다”, “전문가 평가를 통과했다” 또는 “배포 가능하다”고
쓸 수 없다.
