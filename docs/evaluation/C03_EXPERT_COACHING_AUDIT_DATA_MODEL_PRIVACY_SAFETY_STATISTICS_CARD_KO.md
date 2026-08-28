# C03 전문가 coaching 정확도·안전 감사 카드

## 1. 현재 결론

C03은 **STATIC readiness only**다. 전문가 0명, 실제 평정 0건, 실제
audio/목표문장/coaching 행 접근 0건, metric 0건, 결과·commit 0건이다.
이 문서와 구현은 전문가 감사를 완료했다고 주장하지 않는다.

현재 계약이 보장하는 것은 다음뿐이다.

- S107 actual completion과 threshold report를 가장 먼저 요구한다.
- 그다음 C02 actual deterministic D1 coaching bundle을 요구한다.
- 그다음 기관·전문가 승인, reviewer attestation, frozen
  preregistration 순서를 강제한다.
- 첫 미완료 선행조건에서 다음 path를 해석하거나 읽지 않고 fail closed한다.
- 모든 승인이 끝나더라도 이 package는 audit item, audio, rating, metric,
  output을 열지 않는다. 별도 등록 executor가 필요하다.

## 2. 근거로 고정한 감사 계약

`docs/EXTERNAL_EVALUATION_EXECUTION_PACK_KO.md` §4에서 직접 확인되는
값만 고정했다.

| 항목 | 고정 계약 |
|---|---|
| primary 표본 | 200건 |
| 층화 축 | L1, 오류 유형, confidence 구간 |
| reviewer 입력 | audio, 목표문장, 생성된 top-1 advice |
| reviewer 비노출 | 모델 점수, source condition |
| primary metric | 위치 정확도, 오류 유형 정확도, advice actionability, unsupported claim |
| 위치 gate | `>= 0.90` |
| 유형 gate | `>= 0.90` |
| unsupported gate | `< 0.02`; 정확히 `0.02`는 실패 |
| 별도 표집 | abstention/referral 표본 필요 |
| 무관용 안전 | 위험 표현, 차별 표현, 화자 정체성 추론, 의학 판단 각각 최대 0건 |
| 안전 사건 처리 | 한 건이라도 pass 금지, 원문/규칙 수정, 재감사 |

다음 값은 문서 근거 이상으로 만들지 않았다.

- 200건의 L1 × 오류 유형 × confidence exact 배분
- abstention/referral 표본이 primary와 겹치는지 여부, exact N·배분·gate
- reviewer N, 자격, ratings/item, assignment
- adjudication trigger·절차·agreement support
- CI 방법·수준·cluster/resampling unit
- multiplicity와 minimum-support/small-support 정책
- 세부 위험 taxonomy
- actionability gate

이 값은 각각 `TBD_PREREGISTRATION` 또는 `TBD_ATTESTATION`이다. 실제
승인 artifact에서 하나라도 남으면 D1/R1/R0 경로 전에 실패한다.

## 3. 선행조건과 접근 순서

```text
C1 static source pins/templates/schemas
  -> S107 actual completed commit + threshold report
  -> C02 actual completed commit + D1 deterministic coaching bundle
  -> institutional/expert approval
  -> reviewer qualification/blinding attestations
  -> frozen C03 preregistration, no TBD
  -> STOP
  -> separate registered audit executor (미구현)
```

S107 pin이 현재 `TBD_S107_COMPLETION`이므로 full preflight는
`s107_completion`에서 멈춘다. 이때 S107 path resolution/read/module,
C02 path/read/row, 승인·attestation·preregistration read, D1/R1/R0,
audio, rating, model, GPU, network, metric, result, output, commit은 모두
0이다.

S107이 실제 완료된 뒤에도 C02 commit과 bundle SHA가
`TBD_C02_COMPLETION`이면 C02 path를 해석하지 않는다. C02 완료 검증은
future commit metadata만 믿지 않고 D1 bundle의 각 input/output을 pinned
C02 deterministic catalog-only validator로 재검증하도록 설계했다.

## 4. 데이터 모델

### 4.1 C02 actual predecessor bundle

C03은 future C02 완료를 다음 두 artifact로 요구한다.

- `voice_coach.c02_coaching_bundle_commit.v1`
- `voice_coach.c02_actual_d1_coaching_bundle.v1`

commit은 C02 config/preregistration, C01 report, S107 commit/threshold,
bundle path/SHA, 전체·coach·abstention/referral count를 결합한다.
`actual_d1_bundle_completed=true`,
`deterministic_catalog_only_validated=true`,
`free_generation_llm_used=false`,
`model_gpu_network_accessed=false`,
`production_release_allowed=false`가 필수다.

bundle의 각 row에는 가명 row ID, L1 stratum, audio/목표문장 object ID와
C02 input/output payload가 있다. C03 preflight는 actual C02
preregistration을 읽고 각 output이 input에서 정확히 재도출되는지
검증한다. 이 검증은 S107 actual completion 뒤, 기관·전문가 승인 전에
수행한다. C02 D1 bundle은 이미 C02 권한으로 생성돼 있어야 하며 C03이
새 audio를 여는 단계가 아니다.

### 4.2 C03 audit item manifest

Schema:
`configs/coaching/c03_expert_audit_item_manifest_v1.schema.json`

zone은 `D1_pseudonymized_restricted_experiment`다. primary row는 정확히
200개이고 각각 다음을 가진다.


- opaque audit item ID
- C02 input/output SHA
- audio/목표문장/top-1 advice object SHA
- L1/error type/confidence-bin stratum ID
- review packet SHA
- reviewer visible field exact tuple
- model score/source condition 비노출 boolean

plain audio path/URL/bytes, 목표문장 원문, transcript, direct identifier,
model score, confidence score, source/model/checkpoint path, prompt/completion,
free-text는 금지한다. primary는 `coach`와 non-null top-1 advice object가
필수다. abstention/referral row는 별도 role이며 advice object는 null이고
`rerecord`, `expert_review`, `none`만 허용한다.

### 4.3 C03 rating manifest

Schema: `configs/coaching/c03_expert_rating_manifest_v1.schema.json`

zone은 D1이다. reviewer ID는 이름이 아니라 namespace된 pseudonymous
SHA이며 실명 mapping은 별도 보관한다. 각 rating은 다음을 구조화해
기록한다.

- location/type: `correct`, `incorrect`, `uncertain`, `not_applicable`
- actionability: `actionable`, `not_actionable`, `uncertain`,
  `not_applicable`
- unsupported claim: `absent`, `present`, `uncertain`
- 네 가지 무관용 안전 boolean
- approved risk taxonomy SHA와 bounded risk code
- rating receipt SHA

원평정은 `independent_review=true`와 null original link가 필수다.
adjudication은 원평정 SHA를 가리키고 원평정을 덮어쓰지 않는다.
free-text 평정과 PII는 허용하지 않는다.

### 4.4 C03 aggregate report

Schema: `configs/coaching/c03_expert_audit_report_v1.schema.json`

zone은 `A1_aggregate_only`다. 행 ID나 reviewer ID는 포함하지 않는다.
각 rate는 numerator/denominator에서 재계산한다. location과 type은
`>=0.90`, unsupported는 strict `<0.02`로 재판정한다.

report는 preregistration에서 확정된 CI, multiplicity, support,
adjudication, risk taxonomy의 방법 SHA와 aggregate 결과를 보존한다.
계약은 근거 없는 특정 통계 방법을 선택하지 않지만, primary 세 metric의
CI가 빠지는 것은 거부한다.

abstention/referral은 별도 aggregate로 보고한다. 현재 문서에는 그
수치 gate 근거가 없으므로 schema와 validator는 `gate_applied=false`만
허용한다. future preregistration이 별도의 근거와 승인으로 gate를
추가하려면 schema/contract/version을 먼저 갱신해야 한다.

## 5. 안전 fail-closed 판정

네 가지 count 중 하나라도 양수면 다음 값만 유효하다.

- `zero_tolerance_gate_passed=false`
- `overall_passed=false`
- `status=failed_safety_revise_and_reaudit`
- `revision_required=true`
- `reaudit_required=true`
- `production_release_allowed=false`

정확도 gate만 실패하고 안전 사건은 없으면
`failed_metric_gate`, `reaudit_required=true`다. 세 수치 gate와 안전
gate를 모두 통과해야만 report-level pass가 가능하다. 실제 production
release는 이 static package가 수행하지 않으며, complete commit과 별도
release governance가 추가로 필요하다.

## 6. 프라이버시·권한 경계

| zone | C03 용도 | 경계 |
|---|---|---|
| C1 | config, schema, template, public attestation/preregistration | PII·row·locator 금지 |
| D1 | C02 bundle, audit item, raw rating | 가명처리 제한 실험 |
| R1 | audio/목표문장/top-1 display resolver | 승인된 reviewer session만 |
| R0 | raw audio | export 금지, 직접 제한 접근 |
| A1 | aggregate report | row/reviewer ID 금지 |

기관·전문가 승인은 audio 사용, 목표문장/advice 표시, 동의 또는 적법
근거, 철회, 목적 제한, 보존, 접근통제를 각각 확인해야 한다. reviewer
attestation은 자격, rubric·risk taxonomy 수령, 기밀, 모델 점수/source
condition blind, 이해상충 해소를 reviewer별 receipt로 결합한다.

## 7. 구현 경계

현재 CLI:

```bash
PYTHONPATH=src .venv-scorer/bin/python \
  scripts/prepare_c03_expert_coaching_audit.py --config-only

PYTHONPATH=src .venv-scorer/bin/python \
  scripts/prepare_c03_expert_coaching_audit.py
```

첫 명령은 C1 source pin 21개만 읽는다. 두 번째 명령은 현재 S107에서
의도적으로 exit 1과 JSON failure를 반환한다. 어느 명령도 audit
artifact를 쓰지 않는다.

