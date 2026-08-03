# H04 학습 효과 통계 분석 실행 준비도

최종 갱신: 2026-07-29 KST
상태: **정적 preflight 구현 완료, 실제 H00–H04 인간 연구·통계 실행 0**

## 1. 이번 구현 결과

H04를 실제 결과 없이 완료로 오인하지 않도록, 다음 읽기 전용 fail-closed
package를 구현했다.

- config: `configs/experiments/h04_learning_effect_analysis_v1.yaml`
- contract: `src/voice_coach/human_research/h04_learning_effect_contract.py`
- CLI: `scripts/prepare_h04_learning_effect_analysis.py`
- unit test: `tests/unit/test_h04_learning_effect_contract.py`
- integration test:
  `tests/integration/test_prepare_h04_learning_effect_analysis.py`
- data/stat/privacy card:
  `docs/H04_LEARNING_EFFECT_DATA_MODEL_PRIVACY_SAFETY_STATISTICS_CARD_KO.md`

H01 표본수 순환을 막기 위해 predecessor를 다음처럼 분리했다.

```text
actual H00
  -> distinct 15–20/arm feasibility pilot receipt
  -> H01 manual final-N lock
  -> frozen SAP
  -> powered confirmatory H02/H03 bundle
  -> independent analyst attestation
  -> stop before D1/statistics/effect/report/output
```

Feasibility receipt SHA는 H01 lock과 confirmatory bundle에 각각 교차
결합한다. H01의 nuisance report SHA도 feasibility receipt 안의 source
receipt와 일치해야 한다. 같은 feasibility receipt를 confirmatory bundle로
재사용하면 protected path resolution 전에 실패한다. Confirmatory
arm별 N은 H01에서 수동 잠근 값과 정확히 같아야 한다.

## 2. 정적 산출물

| 구분 | 파일 | 현재 기능 |
|---|---|---|
| feasibility schema | `h04_feasibility_pilot_completion_v1.schema.json` | H00 뒤 15–20/arm pilot, blind reliability, aggregate nuisance, efficacy 해석 금지 |
| H01 schema | `h04_h01_final_sample_size_lock_v1.schema.json` | pilot receipt/nuisance 교차 결합, full simulation, 수동 final N |
| SAP template | `h04_statistical_analysis_plan_v1.template.json` | exact 분석 선택을 `TBD_PREREGISTRATION`으로 fail-closed |
| confirmatory schema | `h04_h02_h03_blind_bundle_completion_v1.schema.json` | H01 N·SAP와 결합된 powered blind pre/post/retention bundle |
| analyst template | `h04_analyst_attestation_v1.template.json` | 독립성·blind 순서·ITT/민감도/Holm 약속 |
| aggregate input schema | `h04_learning_effect_aggregate_input_v1.schema.json` | participant row·score·effect 없는 A1 manifest |
| report schema | `h04_learning_effect_statistical_report_v1.schema.json` | effect/CI/Holm/sensitivity/disposition 집계 report |
| completion schema | `h04_learning_effect_completion_v1.schema.json` | input/report SHA·크기와 최종 assurance |

## 3. 고정된 통계 계약

- ITT primary
- `score ~ condition * time + L1 + baseline`
- participant, sentence, rater random intercept
- likelihood-based mixed model primary
- C3−C0와 C5−C3 pre→post primary
- C3−C0와 C5−C3 pre→retention key
- raw 1–5 difference, standardized effect, 95% CI
- MAR multilevel MI, delta-adjusted pattern-mixture MNAR, complete-case
- LOCF와 post-randomization exclusion 금지
- 배정 노출 80% 이상 PP는 sensitivity only
- primary, retention, secondary family별 Holm
- 모든 arm/time 결측, 모든 arm별 철회·adverse feedback·ceiling·floor

Software/model family/ordinal-vs-linear/support/delta grid/MI m/optimizer/
convergence/CI/bootstrap·resampling은 실제 blind SAP 전까지
`TBD_PREREGISTRATION`이다.

## 4. 현재 CLI 결과

정적 검증:

```bash
PYTHONPATH=src .venv-scorer/bin/python \
  scripts/prepare_h04_learning_effect_analysis.py --config-only
```

현재 canonical config에서 기대하고 검증할 경계는 다음과 같다.

- C1/A1 static source pin: 19
- source read: 19
- TOCTOU 재검증: config-only 0
- H00/feasibility/H01/SAP/confirmatory/analyst path·read: 0
- participant/rating/audio/transcript/D1/personal data: 0
- model/GPU/network/statistics/effect/report/output/registry/commit/publication: 0
- artifact 작성·H04 completion·publication: false

Full read-only preflight:

```bash
PYTHONPATH=src .venv-scorer/bin/python \
  scripts/prepare_h04_learning_effect_analysis.py
```

현재는 `stage=h00_authorization`,
`error=H00 approval is TBD_H00_APPROVAL`에서 종료해야 한다.
이 경로에서는 19개 source를 읽고 19개를 TOCTOU 재검증한 뒤 H00 config,
contract, approval/attestation path와 모든 후속 evidence/runtime/output
counter가 0이어야 한다.

## 5. 검증 범위

Targeted test는 다음을 확인한다.

- exact 19-source static pin과 config drift
- 중복 missingness sensitivity 거부
- H00 placeholder가 해소되기 전 모든 protected path/read 0
- feasibility arm당 15–20명, observed effect power 사용 금지
- feasibility→H01 receipt/nuisance SHA 교차 결합
- H01 final N과 confirmatory bundle arm N 일치
- feasibility receipt의 confirmatory 재사용을 path 전에 거부
- SAP freeze, exact method, LOCF/exclusion 금지
- 분석자 blind/ITT/민감도/Holm attestation
- score/effect-free aggregate input
- 네 confirmatory, 여덟 secondary, 네 sensitivity의 완전성
- primary/retention/secondary Holm exact 재계산
- raw·standardized effect CI와 model convergence/support
- 모든 arm/time missingness와 모든 arm별 withdrawal/adverse/ceiling/floor
- completion artifact SHA·크기 binding
- traversal, symlink와 기존 output no-overwrite
- CLI config-only/full/help nonexecution 경계

JSON Schema draft 2020-12 자체 유효성도 별도로 검사한다.

## 6. 실제 실행 전 순차 체크리스트

### Gate 1 — H00

- 공식 기관 판단 원문과 custodian attestation을 승인된 제한 archive에 보관
- H00 public receipt의 approval·attestation SHA를 config에 exact pin
- H00 full validator에서 pilot와 analysis scope/current status 확인

이 작업은 기관 portal 제출·법률/윤리 판단을 자동화하지 않는다.

### Gate 2 — 별도 feasibility pilot

- H00 범위 안에서만 모집·동의·무작위 배정
- 비교별 arm당 15–20명으로 feasibility pilot 수행
- pre/post/retention과 blind 평가자 2인 이상 완료
- nuisance parameter·blind reliability·missingness/attrition을 집계
- 관측 treatment effect를 power input이나 efficacy 결론으로 사용하지 않음
- A1 feasibility completion receipt를 생성하고 independent validation

### Gate 3 — H01 final N

- feasibility aggregate nuisance와 불확실성 사용
- pilot effect와 독립적인 최소 관련 차이 고정
- participant/sentence/rater 또는 실제 cluster 구조를 반영한 full simulation
- 통계 담당자·연구책임자가 final N 수동 승인
- confirmatory 모집 전 공개 사전등록
- H01 lock receipt를 생성하고 feasibility SHA와 교차 검증

### Gate 4 — SAP

- exact software/version, model family, ordinal/linear, support 결정
- MI 횟수/model, MNAR delta grid, optimizer, convergence, CI,
  bootstrap/resampling, fallback과 PP denominator/window 고정
- LOCF·post-randomization exclusion false 확인
- confirmatory data lock·condition unmask·분석 전에 통계/PI 승인·동결

### Gate 5 — Confirmatory H02/H03

- H01에서 잠근 arm별 N과 정확히 일치하게 모집·무작위 배정
- 2주 intervention 및 pre/post/retention 수집
- condition·timepoint·participant identity blind인 평가자 2인 이상
- delivery, missingness, withdrawal, adverse, ceiling/floor와 data-lock receipt
- frozen SAP SHA와 feasibility/H01 lineage를 포함한 A1 completion receipt

### Gate 6 — 분석자와 별도 executor

- 독립 분석자가 data/SAP lock까지 arm label blind였음을 attestation
- 승인된 별도 D1 executor와 exact environment/code hash 사용
- ITT primary와 등록된 sensitivity·Holm만 실행
- 미수렴·support 위반·fallback을 숨기지 않음
- A1 input/report와 completion receipt를 strict validator로 검증
- 독립 결과 감사 전 publication 금지

## 7. 현재 완료도

| 구분 | 완료도 | 근거 |
|---|---:|---|
| H04 production static readiness | 100% | config/schema/template/contract/CLI/test/card/readiness 구현 |
| 실제 H00 승인 | 0% | placeholder 유지 |
| 실제 feasibility participant/rating/audio | 0% | receipt 없음 |
| H01 pilot-informed final N lock | 0% | planning-only artifact만 존재 |
| 실제 confirmatory H02/H03 | 0% | bundle 없음 |
| 실제 H04 statistics/effect/report | 0% | 실행·결과 artifact 없음 |
| H04 연구 완료·논문 결과 반영 | 0% | completion/publication 근거 없음 |

정적 준비도 100%는 WP9 점수나 H04 연구 완료를 올리지 않는다.
