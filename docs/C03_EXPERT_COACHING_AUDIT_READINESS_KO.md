# C03 전문가 coaching 감사 STATIC readiness

## 1. 판정

**구현 상태: STATIC readiness complete / actual C03 incomplete**

정적 config, 엄격 item/rating/report schema, 승인·reviewer·사전등록
template, read-only fail-closed contract/CLI, unit/integration test,
data-model/privacy/safety/statistics card를 구현했다. 실제 전문가·평정,
audio/보호행 접근, metric, 결과, commit은 모두 0이다.

현재 canonical full preflight는 최초 미완료 S107에서 멈춘다.

```text
stage = s107_completion
error = S107 actual completion is TBD_S107_COMPLETION
S107 path resolution/read/module/deep/threshold read = 0
C02 completion/bundle path/read/row = 0
approval/reviewer/preregistration path/read = 0
D1/R1/R0/audio/rating/model/GPU/network/metric/result/output/commit = 0
```

## 2. 구현 artifact와 SHA-256

| artifact | SHA-256 |
|---|---|
| `configs/experiments/c03_expert_coaching_audit_v1.yaml` | `5b5deefc52770fedeb6449fd698f0583423aeeef741f6ece5ad01729c7197d0e` |
| `configs/coaching/c03_expert_audit_item_manifest_v1.schema.json` | `52be547d3db0fe8226c138322e6a0fd1d014b8344dec15dae349db3032471e97` |
| `configs/coaching/c03_expert_rating_manifest_v1.schema.json` | `be1bda4b88c1fe1ef5b399267e69aa71c5c084e1a48a85cca7bf2ad102bd3ae9` |
| `configs/coaching/c03_expert_audit_report_v1.schema.json` | `5c3102aa2fdf9c2a58b100f928a16971dbb22c5a28fabed22b92a4370bb9b1dd` |
| `configs/coaching/c03_institutional_expert_approval_v1.template.json` | `242befd186382078b558af6fa03b8a5f92e0a4caaa070d0d404e26fab5afd58c` |
| `configs/coaching/c03_reviewer_attestation_v1.template.json` | `ba34657c4a115099207c242b484e38620b7d921961127d7f552dfd2c24cdf724` |
| `configs/coaching/c03_expert_audit_preregistration_v1.template.json` | `2b5a149c37633560e5d0920fef917b91bf8c88ba7ed0c675c43955504e10a47e` |
| `src/voice_coach/coaching/c03_expert_audit_contract.py` | `641107be437439eb17e8ad32cfc531b085ea6a6f6ee436c6c2a9423b4d9a531c` |
| `scripts/prepare_c03_expert_coaching_audit.py` | `6dec5c22a0d18150de8f1d48ce4896ed1bc0580f14d6356cb1d919e1ee1b0b27` |
| `tests/unit/test_c03_expert_audit_contract.py` | `d9ba5817f8d3835822e9938b4e866e5b449c7a85ceb346ccb799b82b081d2993` |
| `tests/integration/test_prepare_c03_expert_coaching_audit.py` | `cba27993946c4f244cb430a548aae5157738a61add7ef7154a210b4fd1827b8f` |

이 readiness 문서와 companion card는 config source pin에 포함하지 않아
config-hash ↔ 문서-hash 순환 의존을 만들지 않았다. tracker는 수정하지
않았다.

## 3. source pin

config-only mode는 C1 public/aggregate source 21개를 정확한 SHA-256으로
검증한다.

- C03 schema 3개, template 3개, contract, CLI
- C02 config, contract, input/output schema, preregistration template
- S107 config와 completion validator
- external evaluation §4, C01/C02/S107 card/readiness, privacy boundary

config-only 결과:

- `source_pin_count=21`
- `source_pin_read_count=21`
- predecessor, external approval, D1/R1/R0, model/GPU/network, metric,
  output runtime counter는 전부 0
- artifact 생성 0

## 4. 선행 순서

1. C03 static source/template/schema
2. S107 actual completed commit와 threshold SHA pin
3. pinned C02 contract를 통한 actual S107 deep validation
4. C02 actual completed commit와 D1 bundle SHA pin
5. actual C02 preregistration 및 모든 D1 input/output deterministic 재검증
6. 기관·전문가 승인
7. reviewer 자격·기밀·blind attestation
8. no-TBD frozen C03 preregistration
9. protected audit data 전에 정지

S107 marker가 미해결이면 S107 path를 해석하기 전에 멈춘다. S107이
완료돼도 C02 marker가 미해결이면 C02 commit/bundle path를 해석하지
않는다. 이후 승인/attestation/preregistration도 각각 자기 SHA가
미해결이면 자기 path 전에 멈춘다.

## 5. 고정값과 미정값

고정:

- primary 200
- L1 × error type × confidence-bin 층화
- reviewer는 audio/목표문장/top-1 advice만 봄
- model score/source condition blind
- 위치·유형 `>=0.90`
- unsupported `<0.02`
- abstention/referral 별도 표집
- 위험·차별·정체성 추론·의학 판단 최대 0
- 안전 사건 1건 이상이면 pass 금지, 수정·재감사

미정:

- exact strata allocation
- abstention/referral exact N·primary 관계·metric/gate
- reviewer N·자격·ratings/item·assignment
- adjudication·agreement support
- CI·cluster/resampling·multiplicity·minimum support
- actionability gate
- detailed risk taxonomy

미정값은 `TBD_PREREGISTRATION`/`TBD_ATTESTATION`으로 남겼고 frozen
preregistration validator는 TBD 문자열을 재귀적으로 거부한다.

## 6. 검증 범위

unit test는 다음을 검증한다.

- source pin 21개와 config-only runtime 0
- full 최초 S107 failure에서 모든 runtime counter 0
- 정확히 200 primary와 별도 abstention/referral count
- 세 층화 ID, reviewer view, score/source blind
- raw path/plain target text 차단
- original/adjudication 분리와 원평정 보존
- 위치·유형 0.90 inclusive 및 unsupported 0.02 strict failure
- 네 가지 safety event의 revision+reaudit 강제
- 기관 승인 → reviewer → frozen prereg binding
- unresolved design과 reviewer N drift 차단
- root `additionalProperties=false` schema

integration test는 repository CLI config-only success와 full S107
fail-closed JSON을 검증한다.

## 7. 실제 C03을 위한 다음 순서

1. S106 결과에 이어 S107 calibration을 실제 실행하고 completed commit,
   threshold report SHA를 확정한다.
2. C02 preregistration을 승인하고 deterministic coaching selection을
   별도 executor로 실행한다.
3. C02 D1 bundle commit/bundle을 현재 C03 predecessor schema에 맞춰
   발행하고 모든 row 재검증을 통과한다.
4. 기관·전문가 평가 승인을 발급한다.
5. reviewer N·자격을 결정하고 reviewer별 attestation을 발급한다.
6. exact 표집·adjudication·CI·multiplicity·support·risk taxonomy를
   승인된 preregistration에서 동결한다.
7. 별도 등록 C03 executor를 구현·검토한다.
8. 200 primary와 별도 abstention/referral을 표집하고 blinded rating을
   수집한다.
9. aggregate report를 검증하고, safety 사건이 있으면 수정 후 새
   표본으로 재감사한다.
10. 실제 commit이 있을 때만 tracker/manuscript에 C03 결과를 반영한다.
