# WP7 R03 Pareto+diversity reference bank production 정적 준비도

상태: **production 정적 package 구현 완료 / 실제 R03 실행 0**

기준일: 2026-07-29 KST

## 1. 구현 범위

R03 전용 신규 파일만 사용해 다음 정적 계약을 구현했다.

| 파일 | 역할 |
|---|---|
| `configs/experiments/r03_reference_bank_selection_v1.yaml` | predecessor 순서, TBD prereg, blind selection, lineage/privacy/output 계약 |
| `configs/reference/r03_reference_bank_manifest_v1.schema.json` | 20문장×3–5 D1 bank strict schema |
| `configs/reference/r03_pareto_diversity_preregistration_v1.template.json` | 미근거 operational choice를 노출하는 fail-closed template |
| `src/voice_coach/references/r03_selection_contract.py` | strict config/schema, ordered deep gate, bank/A1 validator, symlink/hash/TOCTOU/no-overwrite guard |
| `scripts/prepare_r03_reference_bank_selection.py` | config-only와 read-only full preflight CLI |
| `tests/unit/test_r03_selection_contract.py` | bank/privacy/order/path bounded fault tests |
| `tests/integration/test_prepare_r03_reference_bank_selection.py` | canonical zero-runtime와 R00→missing R01 fail 순서 |
| [retired contract 통합 안내](../README.md#retired-contracts) | data/model/privacy/prereg/publication 경계의 이전 카드와 통합 위치 |
| `docs/constraints/R03_REFERENCE_BANK_SELECTION_READINESS_KO.md` | 본 증거 문서 |

R00/R01/R02 파일, 정본 tracker, 원고와 evidence map은 수정하지 않았다.
R03 actual selector, audio loader, objective model, GPU runner, publisher는
만들지 않았다.

## 2. Fail-closed 실행 순서

Full preflight는 아래 순서를 바꿀 수 없다.

1. R03 config/source/schema/template exact pin
2. R00 report 재계산과 immutable report exact match
3. R01 completed commit/registry/manifest/human·CosyVoice attestation deep validation
4. R02 completed commit/registry/hard-gate report/eligible partition deep validation
5. 승인·동결된 R03 preregistration, unresolved TBD 0
6. R02 eligible candidate aggregate/lineage path resolve
7. 별도 selection executor 요구

현재 canonical R01 completion SHA는 `TBD_R01_COMPLETION`이다. 따라서
canonical full preflight는 R00을 검증한 뒤 R01 commit path 존재 여부도
확인하지 않고 R01 gate에서 종료해야 한다. R02 completion,
R03 preregistration, eligible/quarantine data, audio/model/GPU, metric,
selection과 output에는 접근하지 않아야 한다.

## 3. 선택·다양성 경계

Paper-backed 값은 objective 이름 네 개, Pareto 사용, 3–5개 선택,
화자·속도·pitch 보존뿐이다. Exact objective implementation, normalization,
tie/dominance, distance/minimum separation과 seed에는 근거가 없다.
Canonical config와 template은 이 값을 `TBD_PREREGISTRATION`으로 두며,
full preflight는 prereg gate에서 fail-closed한다.

Future D1 bank validator는 다음을 강제한다.

- 정확히 20문장, 문장당 3–5개, 전체 60–100개
- 문장별 human+TTS 모두 포함, 수 차이≤1
- distinct speaker/speed/pitch 각각 ≥2/≥2/≥2
- unique opaque candidate와 SHA-256 immutable lineage
- Pareto member와 R02 eligible-row binding
- R04/MOS/CMOS/outcome field와 raw identity/path/plaintext 0

Selector는 source kind에 blind하고, source balance는 선택 commit 뒤
lineage join으로 확인한다. R04 label과 learner outcome은 R03에 들어오지
않는다. 결과를 보고 재선택하거나 objective/threshold를 바꿀 수 없다.

## 4. D1/A1와 공개

- Bank manifest와 selection trace: D1
- 집계 report: A1 counts only
- R04 raw labels: R03 접근 불가
- R04 전 public zone: 금지
- R04 뒤 공개: 자동 허용 아님, 별도 promotion 필요

A1 validator는 candidate row/ID, speaker ID와 audio hash를 거부한다.
Bank schema 자체도 `publication_allowed=false`,
`r04_blind_evaluation_completed=false`만 허용한다.

## 5. 정적 검증 결과

R02 final config/report-schema/preregistration/contract/CLI SHA
`d3e73343…f6b7`/`60525a3b…a92b`/`07b53aa9…28c0`/
`a50e6287…f5bf`/`c40ab4b0…3be5`를 exact pin한 뒤 검증했다.

- R03 unit: **26 passed**
- R03 integration: **3 passed**
- R03 targeted: **29 passed**
- R00→R03 관련 회귀: **88 passed**
- Ruff check/format: **passed**
- mypy contract/CLI: **passed**

Canonical config-only 결과는 `valid_config_only`다.

- static source pin read: 17/17
- R00/R01/R02 deep validation: 0/0/0
- preregistration과 eligible/quarantine data: 0
- audio/model/GPU/objective/Pareto/diversity/selection/R04: 0
- output path/write/publication: 0

Canonical full preflight는 의도된 exit 1,
`stage=r01_completion`,
`error=R01 completion commit is TBD_R01_COMPLETION`이다.

- source pin read: 17
- R00 validator/deep validation: 1/1
- R00 public·aggregate evidence read: 10
- R00 raw audio/row-level read: 0/0
- static+R00 immutable snapshot 재검사: 18
- R01 commit path resolution/read attempt: 0/0
- R02 completion/preregistration/eligible/quarantine selection input: 0
- audio/model/GPU/metric/selection/R04/output/publication: 모두 0
- 선언 output 6개: 생성 0

Bounded fault audit의 open implementation finding은 C/H/M **0/0/0**이다.
감사 범위는 predecessor 순서 우회, source hash/order/symlink/TOCTOU,
TBD 사후 해제, output/source overlap·no-overwrite, source unblinding,
R04/outcome cherry-pick, R01 audio/R02 eligible-row 계보 drift, quarantine
유입, 문장/source/speaker/speed/pitch 다양성, D1/A1 privacy 경계와 허위
완료·공개 주장이다. 실제 audio·objective·human 평가가 없으므로 empirical
품질·외부 승인 위험이 해소됐다는 뜻은 아니다.

검증 시점 SHA-256:

| 파일 | SHA-256 |
|---|---|
| config | `235b1f979850bbbd2c987e5445c2bd43a5a826189c3354540749049e96298c6c` |
| bank schema | `2e6c4728943fbfb5519f7e961fca68da816b82e44fc65f003938644ade68ca83` |
| preregistration template | `aa36d33a28ad445acc63bb8d2b88ddaae1f3742b443bb50be72f05bcda79cdc2` |
| contract | `626e5641e86c19fdac2582d3d1f62e006a2f12ba448dad5127936c241fd00bb8` |
| CLI | `36c21580b433d8bea2669eb504ef86cdc36c0d45889d287739edb05d18fe2517` |
| unit test | `299cbecd56f67d4feb5577ace37961cad1078a65477d987a252597db0ee1c374` |
| integration test | `d431add1a94a139183beb6e765851158cf737d74b2abdaccdf969980163d1487` |
| data/model card | `93edf4a288998d2c2956aad9f9b1bf55943c50ff474189c3c137d4052689f20d` |

Readiness 문서 자체 SHA는 자기참조를 피하고 tracker log 59에 기록한다.

## 6. Actual과 점수 경계

현재 actual 값은 모두 0이다.

- R01/R02 completion artifact: 0
- R03 approved preregistration: 0
- candidate/audio/model/GPU access: 0
- objective/Pareto/diversity computation: 0
- selected candidate/bank/trace/aggregate/commit: 0
- R04 label/MOS/CMOS: 0
- publication: 0

따라서 R03 연구 완료율은 0%, 획득 점수는 0이다. Production static
readiness는 실제 선택이나 논문 결과가 아니다.

## 7. 다음 실행

1. R01을 외부 승인·녹음·CosyVoice artifact와 실제 후보로 완료한다.
2. R02 preregistration/attestation 뒤 hard gate를 실행·commit한다.
3. R02 final commit의 eligible partition과 lineage를 독립 검증한다.
4. R02 eligible data를 보기 전에 R03 operational choice를 사전등록한다.
5. 별도 R03 selection executor를 구현하고 frozen seed로 replay한다.
6. D1 bank와 A1 aggregate를 독립 검증한 뒤 R04 blind 평가에 전달한다.

그 전에는 실제 audio/model/GPU/metric/selection과 bank 작성이 금지된다.
