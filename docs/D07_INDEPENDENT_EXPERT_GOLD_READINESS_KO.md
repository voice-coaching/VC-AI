# D07 독립 전문가 gold STATIC readiness

## 1. 결론

**정적 구현 준비 완료 / 실제 D07 0%**

다음을 구현했다.

- strict item/rating/calibration/locked/completion schema 5개
- D07-specific institutional approval, reviewer attestation, frozen
  preregistration, locked authorization template 4개
- source-pinned config
- read-only fail-closed contract와 CLI
- unit/integration test
- data/privacy/statistics card

실제 전문가·평정·audio·locked transaction·metric·result·commit은 모두
0이다. Tracker와 G04 UI/state는 수정하지 않았다.

## 2. 구현 artifact와 SHA-256

| artifact | SHA-256 |
|---|---|
| `configs/experiments/d07_independent_expert_gold_v1.yaml` | `dc6f74155ee9220193825681e9047fb08b488e8ce2397dedcefece41b8e81a16` |
| `configs/expert_evaluation/d07_gold_item_manifest_v1.schema.json` | `756d8e954077800e5b3de975b8839796b375c5d29d7ce343832d45fdfc994546` |
| `configs/expert_evaluation/d07_expert_rating_manifest_v1.schema.json` | `e9c233837d9693ba5de04d4ad86fb10b12dacbc36c496571f23115fd58a0b42f` |
| `configs/expert_evaluation/d07_calibration_agreement_report_v1.schema.json` | `fe7ae40123f5d70bd5a99af7e8597656bcb6a6bd9813adcc26a3529e7eff29ce` |
| `configs/expert_evaluation/d07_locked_evaluation_report_v1.schema.json` | `c5a352fa3d08631b09c2ec183779e2300a8d075ea9be2db78f0b2b1f79256a2e` |
| `configs/expert_evaluation/d07_gold_completion_attestation_v1.schema.json` | `1b9dcb492531cbdd305d13c3e79a87027cd297ba0d41fe0f04bb05683e2a910f` |
| `configs/expert_evaluation/d07_institutional_expert_approval_v1.template.json` | `3e8a0c6314e42ce6fa9ae36cea4bf568f618957d99769592679c7218194abc4d` |
| `configs/expert_evaluation/d07_reviewer_attestation_v1.template.json` | `fddc578c52392a3e5c72c35ce9eda1fcc59e8080c2f914403d782cc1788d53c2` |
| `configs/expert_evaluation/d07_preregistration_v1.template.json` | `2e1bc517b8651f9aed09697fffcfaa8361074efdaedd66317cdd4af7fae82c79` |
| `configs/expert_evaluation/d07_locked_access_authorization_v1.template.json` | `8479443a2d181e8a8190af81eacd8c51e2353cd1b1e095a5f21b1d2eb65aa3e6` |
| `src/voice_coach/evaluation/d07_expert_gold_contract.py` | `77ad2f1acb1d2dff06b5d010248c83260f1eca9eab815b897047dba96bbc1a95` |
| `scripts/prepare_d07_independent_expert_gold.py` | `869f952f84c2f9aec463e5edf43ed1a134a750429ca3cbc5ba0fe9225292d98b` |
| `tests/unit/test_d07_expert_gold_contract.py` | `1809dd8998e743610c861976a875a57937545edcd31b5119de8ff4115c4eb218` |
| `tests/integration/test_prepare_d07_independent_expert_gold.py` | `28f9b6ecb5b7cb8e3c8e39190c3412110c73c354e67fdc8d66bac9fa8ccb6166` |

Readiness/card 문서는 config source pin에 포함하지 않아 문서 hash와 config
hash의 순환 의존을 만들지 않았다.

## 3. Static source와 G04 보존

Config-only는 C1/A1 source 25개를 SHA-256으로 검증한다.

- D07 schema/template/contract/CLI
- C01 17-tag ontology
- H00 config/contract
- S108 G04/D07 aggregate contract
- external evaluation, G04 procedure/UI, privacy, S2 cards
- G04 A1 progress aggregate

G04 aggregate는 정확히 0/240으로만 읽는다.

- status: incomplete
- completed: 0
- reviewer: 0
- review-state SHA: null
- export: false

Restricted G04 UI state path는 source pin에도 없고 path resolution/read
counter도 0이다. G04 worksheet/detail/audio를 열지 않았고 기존 파일을
쓰지 않았다.

## 4. 현재 full fail-closed 결과

Full preflight는 static source 25개와 TOCTOU 25개, G04 A1 aggregate
1개를 검증한 뒤 최초 외부 blocker인 H00에서 멈춘다.

```text
stage = h00_approval
H00 module load = 0
H00 approval path/read = 0
H00 attestation path/read = 0
H00 restricted document path/read = 0
D07 institutional approval path/read = 0
reviewer attestation path/read = 0
preregistration path/read = 0
D1 item path/read/row = 0
R1 locator path/read = 0
calibration audio/rating/metric/result = 0
G04 UI state path/read = 0
G04 completion path/read = 0
locked authorization/item/audio/rating/transaction = 0
developer locked/model/GPU/network/metric/result/output/commit = 0
```

`TBD_H00_APPROVAL`/`TBD_H00_ATTESTATION`을 실제 SHA로 바꾸기 전에는
H00 module도 load하지 않으므로 external path/read 0이 보장된다.

## 5. 고정값과 의도적 TBD

고정:

- 총 1,000–2,000
- calibration 800
- locked 200–1,200
- L1/성별/숙련도/길이/목표음소/기존 오류 층화
- primary expert 2명 독립·상호/model blind
- adjudicator 1명, disagreement-only, 원평정 보존
- phone realization, 17-tag ontology+uncertain, boundary, 세 1–5 점수,
  confidence 1–4
- scalar agreement 0.70 또는 taxonomy 0.65
- 미달 시 재교육+별도 재표집, 실패 표본 재사용 금지
- locked 전 G04 completion
- locked expert access exactly one transaction
- locked 시작 뒤 rubric/primary 변경 금지
- original-1/original-2/adjudicated sensitivity 모두 보고

TBD:

- exact stratum allocation·small-group minimum·weight
- reviewer qualification evidence policy의 actual receipt
- disagreement definition/trigger/protocol·reason taxonomy·note max
- gate에 사용할 exact ICC/QWK와 taxonomy statistic
- CI·cluster·multiplicity·minimum support·small support
- one-shot recovery/failure 절차

Frozen preregistration validator는 모든 TBD 문자열을 재귀적으로
거부한다.

## 6. 테스트 범위

Unit test는 다음을 확인한다.

- source pin 25와 G04 aggregate 1만 읽고 보호 runtime 0
- H00 TBD에서 module/path/read 0
- G04 0/240 state/export 미생성과 D07 reuse 금지
- H00 → D07 institutional approval → 3-role reviewer → frozen prereg 결합
- calibration 800 + locked 200의 split count
- item/speaker hash, 여섯 strata, G04/S203 reuse false
- 두 original rating의 mutual/model blindness
- disagreement-only adjudication과 원평정 2개 보존
- note 길이/PII 차단
- scalar threshold 경계, taxonomy threshold 경계, OR gate
- 두 값 미달 시 retraining·separate resample·locked 금지
- G04 completion과 D07 locked authorization 분리
- one-shot replay 차단
- locked rubric/primary 불변과 세 sensitivity basis
- S108-compatible aggregate completion과 agreement 재검증
- 모든 공개 schema의 strict root와 template TBD
- G04 restricted UI state 미접근

Integration test는 repository CLI의 config-only success와 H00 최초
fail-closed JSON을 검증한다.

### 6.1 2026-07-29 검증 결과

- D07 unit/integration + H00/G04/S108/S2 연동 회귀: 166 passed
- repository 전체: 1,091 tests collected, all passed
- Ruff lint: passed
- Ruff format check: 4 files already formatted
- mypy: 2 source files, no issues
- `git diff --check`: passed

기존 FastAPI/Starlette deprecation 1건과 tiny WavLM test의 PyTorch mask
type warning 6건만 있었고 D07 실패는 없었다.

Config-only 실측 counter는 source pin 25/25, G04 aggregate read 1,
G04 UI state path/read 0이며 보호 runtime counter는 모두 0이었다.
Full 실측 counter는 source pin 25/25, TOCTOU 25/25, G04 aggregate read
1 이후 `stage=h00_approval`에서 fail-closed 되었고, H00 module/path/read와
모든 외부·D1/R1/R0·평정·locked·model·GPU·metric·output counter가
0이었다.

## 7. 다음 실제 실행 순서

1. H00 actual public approval과 official-document attestation을 발급·검증한다.
2. H00을 D07 승인으로 대체하지 말고 D07-specific institutional
   approval을 별도로 발급한다.
3. 자격 evidence를 확인한 primary expert 2명과 adjudicator 1명을
   attestation에 등록한다.
4. exact allocation, disagreement, statistics, note limit, one-shot policy를
   preregistration으로 동결한다.
5. 별도 executor에서 800 calibration을 두 primary가 독립 평정한다.
6. adjudication 전 원평정 agreement를 계산한다.
7. gate 미달이면 rubric 재교육 후 별도 800 표본으로 다시 calibration한다.
8. gate 통과 후 rubric/primary를 동결한다.
9. G04 240건을 별도 전문가 stage로 실제 완료하고 attestation을 발급한다.
10. prior expert locked access 0을 확인해 one-shot authorization을 발급한다.
11. locked 200–1,200을 단일 expert transaction으로 평정한다.
12. 두 원평정과 adjudicated gold sensitivity를 각각 보고한다.
13. 독립 검증 뒤 aggregate D07 completion attestation만 S108에 전달한다.
