# D09/H02/H03 종단 학습자 수집 production-static 준비도

## 1. 결론

정적 실행 준비 패키지는 구현되어 있다. 현재 상태는
`production_static_preflight_only`이며 실제 사람 연구를 시작할 수 있다는
뜻이 아니다. H00과 delivery DAG가 실제 completion hash로 바뀌기 전 full
preflight는 predecessor-first로 실패한다.

구성 요소:

- config:
  `configs/experiments/d09_h02_h03_longitudinal_collection_v1.yaml`
- contract:
  `src/voice_coach/human_research/d09_h02_h03_collection_contract.py`
- CLI:
  `scripts/prepare_d09_h02_h03_longitudinal_collection.py`
- D1 schema 8개
- A1 delivery, within-phase comparison separation, between-phase separation
  schema 3개
- preregistration, C0/C3/C5 manual, execution authorization template 3개
- 데이터·개인정보·안전 카드와 이 readiness 문서

tracker는 canonical source pin에 포함하지 않았고 이 작업에서 수정하지
않는다.

## 2. 실행 명령

정적 source/schema/template만 검사:

```bash
PYTHONPATH=src python scripts/prepare_d09_h02_h03_longitudinal_collection.py \
  --config-only --phase feasibility
```

feasibility 선행조건 검사:

```bash
PYTHONPATH=src python scripts/prepare_d09_h02_h03_longitudinal_collection.py \
  --phase feasibility
```

confirmatory 선행조건 검사:

```bash
PYTHONPATH=src python scripts/prepare_d09_h02_h03_longitudinal_collection.py \
  --phase confirmatory
```

CLI는 어떤 모드에서도 모집·수집·배정·중재·평정·분석·출력을 실행하지
않는다.

## 3. fail-closed 순서

### feasibility

1. 46개 C1/A1 정적 source hash와 strict schema/template 검증
2. H00 approval·attestation actual hash가 있는지 경로 해석 전에 확인
3. pinned H00 contract로 승인 범위를 deep validation
4. 실제 delivery predecessor A1 receipt 검증
5. 동결된 feasibility operational preregistration 검증
6. 동결된 feasibility C0/C3/C5 manual bundle 검증
7. 현재 유효한 feasibility execution authorization 검증
8. 모집 직전에 정지

### confirmatory

1. 정적 검증과 H00 deep validation
2. 실제 feasibility completion
3. 실제 H01 수동 final-N lock
4. 실제 frozen H04 SAP
5. delivery predecessor A1 receipt
6. confirmatory preregistration/manual/authorization
7. 새 confirmatory 모집 직전에 정지

feasibility artifact를 confirmatory completion으로 재사용할 수 없고,
confirmatory final N은 H01 lock과 정확히 같아야 한다.

## 4. delivery predecessor receipt

정적 패키지는 predecessor의 보호 artifact를 직접 읽지 않는다. 별도
운영자가 각 canonical contract로 deep validation한 A1 receipt만 읽는다.

| gate | 실제 필수 predecessor |
|---|---|
| stage 1 / C3 | S108, E01, C02, E02, C03 |
| stage 2 / C5 | stage 1 전체 + E00, R03, R04 |

receipt는 각 config/contract/completion hash, 독립 deep-validation receipt,
pass/status를 결합한다. E00 section은 reference handoff와 D08/R03
rights-lineage hash를 함께 가진다. 하나라도 TBD이면 aggregate receipt
경로도 해석하지 않는다.

## 5. 무작위배정과 분리 증거

행 validator는 다음을 강제한다.

- `c3_minus_c0`에는 `C0/C3`만, stage 1 receipt만 허용
- `c5_minus_c3`에는 `C3/C5`만, stage 2 receipt만 허용
- participant-unit 1:1, L1+baseline comprehensibility stratification
- assignment 전 allocation concealment
- stage 1과 stage 2 randomization receipt 분리
- stage 2 activation에 unblinded stage 1 effect 사용 금지

within-phase A1 separation receipt는 두 comparison의 participant/audio/rating
교집합, shared-C3 participant 교집합, copied/cloned row가 모두 0임을
증명한다. between-phase receipt는 feasibility와 confirmatory의 같은 네
종류 재사용이 0임을 증명한다.

## 6. 승인 전 TBD로 남기는 항목

다음 값은 문헌이나 현재 데이터에서 임의로 채우지 않는다.

- exact eligibility/exclusion/recruitment/screening
- 미성년자·보상·coercion review
- phase별 exact N
- recording/QC threshold와 coded reason
- session/exposure/adherence denominator
- rater qualification, anchor, blocking, adjudication, minimum support
- consent/withdrawal/retention/deletion/incident/pause-stop workflow
- C0/C3/C5 exact manual version/hash

template는 의도적으로 `draft_unapproved` 또는 `draft_inactive`와 TBD를
포함한다. 실제 artifact는 TBD가 하나라도 남으면 실패한다.

## 7. 현재 blocker

현재 config의 실제 값은 다음과 같이 미확정이다.

- `TBD_H00_APPROVAL`, `TBD_H00_ATTESTATION`
- S108, E01, C02, E02, C03 actual completion
- E00 completion과 D08 rights lineage
- R03 actual bank와 R04 actual blind-evaluation pass
- feasibility/confirmatory preregistration, manual, authorization
- feasibility completion
- H01 final-N lock와 H04 frozen SAP
- confirmatory completion 및 두 종류 separation receipt

따라서 full preflight는 H00에서 먼저 정지해야 하며 delivery/H04/D1/output
경로 접근은 0이어야 한다.

## 8. 검증 기준

필수 검증:

```bash
PYTHONPATH=src .venv-scorer/bin/pytest -q \
  tests/unit/test_d09_h02_h03_collection_contract.py \
  tests/integration/test_prepare_d09_h02_h03_longitudinal_collection.py
```

추가 회귀:

```bash
PYTHONPATH=src .venv-scorer/bin/pytest -q \
  tests/unit/test_h00_approval_contract.py \
  tests/unit/test_h04_learning_effect_contract.py \
  tests/integration/test_prepare_h00_human_research_approval.py \
  tests/integration/test_prepare_h04_learning_effect_analysis.py
```

정적 성공 기준:

- canonical source pin 46개 일치
- JSON schema가 Draft 2020-12 strict root
- template root/section exact-key와 unresolved 상태 확인
- config-only에서 H00/H04/delivery/D1/runtime/output counter 0
- full에서 H00 TBD를 확인한 뒤 H00 evidence path resolution 0
- feasibility/confirmatory 모두 같은 predecessor-first 경계 유지

실제 사람 연구의 시작 조건은 정적 테스트 성공이 아니라, 모든 실제
승인·completion·preregistration·manual·authorization hash를 수동 검토 후
config에 고정하고 full preflight가 성공하는 것이다.
