# E01 음소 대치 v2 정적 계약 readiness

상태: **CPU-only 정적 계약 구현 / 실제 음소 대치·음성 접근·평가 실행 0**

기준일: 2026-07-29 KST

`paper_complete=false`, `actual_execution=false`

## 1. 결론과 비범위

PDF의 E01 `phone` family에는 “특정 음소 교체 합성”이라는 방향만 있고,
합성 알고리즘, 관측 경계, donor 선택, tolerance, 최소 support,
invalid-case 처리와 독립 검증 방법은 정해져 있지 않다. 따라서 이번
변경은 실제 splice나 음소 대치를 만들지 않는다. 실행 전에 다음 오류를
거부하는 **새 v2 정적 계약**만 추가한다.

- 예상 발음(G2P)을 실제 관측 경계나 정답 음소로 오용
- 서로 다른 화자·세션·문맥 donor 혼합
- 음성 파생 처리가 허용되지 않은 동의 범위 사용
- identity control 없이 원본과 treatment만 비교
- edit mask 밖 waveform 변경 또는 sample 수 변경
- S1의 weight·feature·prediction·threshold를 재사용한 “독립” 검증
- S201을 음소 gold로 사용하거나 S202를 음소 정답 판정기로 사용
- 사전등록되지 않은 tolerance·support·invalid policy 사용
- A1 report에 case·audio·speaker·phone·boundary·mask 또는 개별 hash 노출

이 계약은 audio/data/model/path/environment/network/GPU/locked
test/S203을 읽거나 쓰지 않는다. 음소 대치 구현, 독립 verifier inference,
관계 metric 계산, 결과 생성과 논문 승격은 모두 별도 작업이다.

## 2. 추가 파일과 소유 경계

| 파일 | 역할 |
|---|---|
| `configs/reference/e01_phone_case_sidecar_v2.schema.json` | 관측 경계·동의·동일 lineage와 비공개 source/donor/mask identity |
| `configs/reference/e01_phone_worker_receipt_v2.schema.json` | U/I/T output, sample/mask 불변식, 독립 verifier와 세 관계 결과 |
| `configs/reference/e01_phone_paired_aggregate_report_v2.schema.json` | 단일 private bundle digest와 count만 허용하는 A1 report |
| `src/voice_coach/evaluation/e01_phone_substitution_v2_contract.py` | 이미 decode된 in-memory object만 받는 pure cross-field validator |
| `tests/unit/test_e01_phone_substitution_v2_contract.py` | 계약 drift·privacy leak·산술 위조를 거부하는 적대적 단위 테스트 |

기존 E01 v1 config/schema/contract, duration primitive/worker, tracker,
publication package와 S104 파일은 수정하지 않았다. v2는 v1의 phone
placeholder를 완료 상태로 바꾸지 않으며 v1 production 실행기에 자동
연결되지 않는다.

## 3. U/I/T paired-role 계약

한 case는 다음 세 signal role과 세 비교를 모두 가져야 한다.

| signal | 의미 |
|---|---|
| `U` | 변경하지 않은 source waveform |
| `I` | source 음소를 유지한 identity-control 재합성 |
| `T` | human-observed donor 음소를 목표로 한 treatment 재합성 |

| 관계 | 고정 expectation |
|---|---|
| `U_to_I` | `identity_control_invariance` |
| `U_to_T` | `targeted_phone_change` |
| `I_to_T` | `targeted_phone_change` |

세 관계는 이 순서와 role mapping을 바꿀 수 없다. `T`의 PCM digest는
`U`, `I`와 달라야 한다. `I`는 source의 observed phone label digest를,
`T`는 donor의 observed phone label digest를 사용해야 한다. source와
donor label digest가 같으면 phone substitution case가 아니므로
거부한다.

`U_to_T`만 보고 성공을 주장할 수 없다. `U_to_I`는 splice/resynthesis
자체의 collateral effect를 드러내고, `I_to_T`는 같은 처리 경계에서
targeted change를 비교하기 위한 필수 control이다. 이 계약은 실제 metric
값이나 tolerance를 발명하지 않고 frozen relation-policy digest에만
결합한다.

## 4. 사람 관측 경계·동의·lineage

restricted case sidecar는 다음을 모두 강제한다.

- boundary source:
  `human_observed_adjudicated_not_expected_g2p`
- source/donor boundary annotation과 phone label의 nonzero SHA-256
- source와 donor의 서로 다른 observed phone label
- boundary observer가 S1과 독립이라는 attestation
- source와 donor 모두 derivative processing 및 phone
  splice/resynthesis가 허용된 동일 consent-scope digest
- public audio release는 false
- source/donor의 speaker, recording session, prompt context lineage
  digest가 각각 exact equality
- locked test와 S203 expert holdout 포함 false
- raw audio와 path embedding false

이는 사람이 실제 annotation을 수행했다는 경험적 증거를 생성하지 않는다.
future executor는 승인된 annotation·consent artifact를 restricted
sidecar에 결합한 뒤 별도 심사를 받아야 한다.

## 5. restricted material과 waveform 불변식

source/donor audio, source/donor region, edit mask와 U/I/T output digest는
`D1_pseudonymized_restricted_non_locked` sidecar/receipt 밖으로 이동할
수 없다. worker receipt validator는 다음을 교차 검산한다.

1. U PCM digest가 frozen source digest와 동일
2. U/I/T sample count가 source sample count와 모두 동일
3. mask sample count가 case와 동일하고
   `masked + outside = source sample count`
4. edit mask 안과 밖에 각각 최소 한 sample 존재
5. U/I/T의 outside-mask PCM digest가 모두 동일
6. same-count와 세 outside-mask identity attestation이 모두 true
7. 자동 clipping/normalization 적용 false

digest equality와 attestation은 실제 waveform 검증기를 대신하지 않는다.
future worker가 PCM과 mask에서 digest와 count를 계산하고, 독립 verifier가
같은 원자료에서 재계산해야 한다. 현재 모듈은 그 자료를 열지 않는다.

## 6. S1 독립 verifier와 S201/S202 역할 분리

restricted receipt는 S1 model identity와 phone verifier identity,
각 training lineage를 모두 고정한다. model identity와 training lineage
각각은 S1과 verifier 사이에 달라야 하며 다음 접근은 모두 false다.

- S1 weights
- S1 features
- S1 predictions
- S1 thresholds
- S1 output을 verifier training label로 사용

verifier는 human-observed boundary를 사용해야 한다. 단순히 digest가
다르다는 사실만으로 과학적 독립성이 입증되지는 않으므로 실제 model/data
lineage audit도 후속 gate다.

S201은 `content_consistency_routing_only`다. S201의 transcript/WER/content
score를 phone gold, feature, target 또는 phone decision으로 쓸 수 없다.
S202는 `independent_utterance_quality_auditor_not_phone_gold`이며 phone
label/boundary/gold나 phone decision rescue에 사용할 수 없다.

## 7. 사전등록 binding과 invalid 처리

case와 worker receipt는 다음 nonzero SHA-256을 exact equality로
교차 결합한다.

- approved preregistration
- phone-case policy
- tolerance policy
- minimum-support policy
- invalid-case policy
- `U_to_I`, `U_to_T`, `I_to_T` relation policy 각 1개

relation status는 `passed`, `failed`, `invalid`만 허용한다. `invalid`에는
사전 정의된 reason code가 반드시 있어야 하고 pass/fail에는 reason이
없어야 한다. 현재 reason vocabulary는 verifier unavailable, metric
undefined, preregistered quality guard, other preregistered로 제한된다.
이 vocabulary는 post-hoc 자유서술 사유를 허용하지 않는다.

## 8. A1 aggregate-only 경계

A1 report의 유일한 digest는 canonicalized private worker-receipt bundle
SHA-256 하나다. report에는 다음 항목이 없다.

- row/case/item/participant ID
- audio/path/source/donor/output digest
- speaker/session/context lineage
- phone label 또는 transcript
- boundary/edit mask
- 개별 preregistration/model/implementation/policy digest
- free text

허용 결과는 고정된 세 relation role의 case/evaluable/pass/fail/invalid
count, minimum-support count, required-pass count, pass rate, decision과
overall count뿐이다. validator는 다음을 재계산한다.

- `passed + failed = evaluable`
- `evaluable + invalid = case`
- pass rate
- minimum support와 required-pass count에 따른 relation decision
- relation 합과 overall count/rate
- 세 relation을 모두 통과한 case count의 가능한 범위
- invalid relation이 하나 이상인 case count의 가능한 범위
- relation decision으로부터 overall decision

private bundle과 함께 검증할 때는 receipt를 case ID로 정렬해 bundle
digest를 다시 만들고, relation count, all-three-pass case count,
any-invalid case count를 restricted receipt에서 exact 재계산한다.
`publication_allowed=false`, `paper_complete=false`는 고정이다.

## 9. 검증 결과

CPU-only focused 검증:

```bash
.venv-scorer/bin/python -m pytest -q \
  tests/unit/test_e01_phone_substitution_v2_contract.py

.venv-scorer/bin/ruff check \
  src/voice_coach/evaluation/e01_phone_substitution_v2_contract.py \
  tests/unit/test_e01_phone_substitution_v2_contract.py

.venv-scorer/bin/ruff format --check \
  src/voice_coach/evaluation/e01_phone_substitution_v2_contract.py \
  tests/unit/test_e01_phone_substitution_v2_contract.py

.venv-scorer/bin/mypy --strict \
  --follow-untyped-imports --ignore-missing-imports \
  src/voice_coach/evaluation/e01_phone_substitution_v2_contract.py \
  tests/unit/test_e01_phone_substitution_v2_contract.py
```

관측 결과:

- focused pytest: **49 passed**
- Ruff check: **passed**
- Ruff format check: **passed**
- strict mypy: **passed**
- 세 schema JSON parse: **passed**
- system `jsonschema` 4.10.3
  `Draft202012Validator.check_schema`: **3/3 passed**
- valid synthetic in-memory fixture의 schema validation: **3/3 passed**

테스트의 waveform/hash/phone 값은 계약 분기만 검사하는 합성 object다.
실제 음성, 실제 참가자, 실제 경계, 실제 모델 결과가 아니다.

## 10. 실제 실행 전 남은 필수 gate

1. 연구·개인정보 승인과 source/donor별 동의 범위를 사람 검토로 확정
2. non-locked E01 population과 same-speaker/session/context donor pool 확정
3. source/donor phone boundary를 사람이 관측·이중검토하고 restricted
   artifact로 동결
4. identity control과 treatment를 동일 처리 경로로 만드는 실제 synthesis
   알고리즘, runtime, implementation digest 고정
5. edit-mask 밖 exact identity를 실제 PCM에서 독립 재검산하는 worker와
   verifier 구현
6. final S1 identity와 독립 phone verifier의 model/training lineage 감사
7. S201 content-only 및 S202 non-phone-gold production evidence 확인
8. relation별 metric, tolerance, minimum support, invalid policy,
   multiplicity와 gate를 결과 보기 전에 승인·동결
9. 소규모 non-locked smoke 후 사전등록대로 평가
10. privacy review를 거친 A1 aggregate만 논문 evidence gate로 전달

이 열 단계가 완료되기 전에는 E01 phone relation pass rate, 개선 효과,
독립 검증 성공 또는 논문 완료를 주장할 수 없다.
