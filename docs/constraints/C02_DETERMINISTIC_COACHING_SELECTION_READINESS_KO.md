# C02 결정론적 top-1/top-3 coaching selection STATIC readiness

작성일: 2026-07-29 KST

## 1. 결론

C02의 strict input/output schema, unresolved preregistration template,
production static config, read-only predecessor-first CLI, 결정론적
catalog-only selection contract와 synthetic tests를 구현했다.

- config-only static validation: 통과
- canonical source pin: 19개
- full preflight: C01 deep validation 후 S107
  `TBD_S107_COMPLETION`에서 fail closed
- C01 privacy-safe aggregate read: 1
- C01 row/raw/locator/environment/identifier read: 0
- S107 completion path/read: 0/0
- preregistration path/read: 0/0
- diagnostic/coaching/effect execution: 0
- model/GPU/network/LLM/free generation: 0
- output/registry/commit/publication: 0

이는 실제 C02 완료가 아니다. 현재 package는 잘못된 threshold나 evidence
matrix를 추정하지 않고, 첫 미완료 predecessor 전에 중단하는 readiness
계약이다.

## 2. 근거와 고정 범위

검토 근거:

- `docs/constraints/C01_KOREAN_ERROR_ADVICE_ONTOLOGY_DATA_CARD_KO.md`
- `docs/IMPLEMENTATION_PLAN_KO.md` Phase 4
- `docs/constraints/WAVLM_S107_CALIBRATION_READINESS_KO.md`
- `docs/paper/EXPERIMENTAL_MANUSCRIPT_KO.md`
- C01 immutable validation report
- S107 static config와 completion deep-validator contract

근거로 고정한 규칙:

- top-1/top-3만 허용, 기본 top-1
- priority:
  severity×calibrated confidence×comprehensibility impact×trainability
- base-10 Decimal, 소수 12자리, round-half-even
- start/end/type/target/observed/evidence/issue-ID tie-break
- blocked top-k no-backfill
- exact threshold equality는 통과
- low-confidence `rerecord`
- unsupported/unknown/other `expert_review`
- phone-localized claim은 positive overlap receipt 필수
- C01 advice ID와 한국어 문구 exact copy만 허용
- free-generation LLM, prompt/completion, runtime model, network 금지
- C03 전 production release 금지

확정 근거가 없는 exact threshold, issue-confidence aggregation, evidence
support matrix와 최소 evidence 수는 모두 `TBD_PREREGISTRATION`으로 남겼다.

## 3. 생성한 package

| 파일 | 역할 | SHA-256 |
|---|---|---|
| `configs/experiments/c02_deterministic_coaching_selection_v1.yaml` | canonical static policy와 predecessor-first 순서 | `79fa2d037383424036fb40ff66eae26a6fd992a731913393cc92f9d7b61eaa05` |
| `configs/coaching/c02_coaching_selection_preregistration_v1.template.json` | threshold·aggregation·evidence 승인 템플릿 | `6d34fd0feb2af5ab0a1b0d08560883be39ae4d55fca89f4f9f42484eeb05040b` |
| `configs/coaching/c02_diagnostic_issues_input_v1.schema.json` | strict D1 diagnostic input schema | `cd2588ffac9ff10870977e125969c7709ecde25c050c4845ab0c9b9f1f1d7742` |
| `configs/coaching/c02_coaching_selection_output_v1.schema.json` | strict D1 catalog-only output schema | `935db571a2371e96b082a59979af7b24a508318f5385f62032d4ce735ed6c643` |
| `src/voice_coach/coaching/c02_selection_contract.py` | static/deep/payload fail-closed contract | `59941428030e653b013459b003ce99d1169cd940ee9a06fb320634d75dd11667` |
| `scripts/prepare_c02_coaching_selection.py` | config-only/full read-only CLI | `4dbbd04b408922808f622011dffba06297540c9a917589c4e21e73333a1f292f` |
| `tests/unit/test_c02_selection_contract.py` | synthetic selection/safety/path tests | `b59708c6c5f49e7a75ccc93fc5c64a145740f14b97d98fac401934e10299ae26` |
| `tests/integration/test_prepare_c02_coaching_selection.py` | CLI access-counter tests | `27eae98cac4a146ef701de259a03c380ef88794818ec4babc1c48c78a04ca4d0` |
| `docs/constraints/C02_COACHING_SELECTION_DATA_MODEL_SAFETY_CARD_KO.md` | 데이터·모델·개인정보·안전 카드 | `88749a14a61da78b612f8c22edcb13278f4906081971bcda2d55dd119e72b7b1` |

## 4. 강제 실행 순서

1. C02 config, 19개 source pin, prereg template, input/output schema 검증
2. C01 engineering ontology와 immutable report deep validation
3. exact S107 completed commit SHA가 있는지 path 해석 전에 확인
4. 실제 S107 completion, registry, threshold report deep validation
5. no-TBD frozen C02 preregistration 검증
6. diagnostic input, coaching, model/network, output path 전에 정지

현재 full preflight는 1–2를 통과한 뒤 3에서 중단한다. S107 commit과
threshold SHA가 `TBD_S107_COMPLETION`이므로 해당 path resolution/read와
validator module load는 모두 0이다.

## 5. CLI

정적 검증:

```bash
PYTHONPATH=src .venv-scorer/bin/python \
  scripts/prepare_c02_coaching_selection.py --config-only
```

예상:

- exit 0
- `status=valid_config_only`
- `source_pin_count=19`
- source pin read 19
- C01/S107/prereg/D1/coaching/model/GPU/network/LLM/output count 0

predecessor-first full preflight:

```bash
PYTHONPATH=src .venv-scorer/bin/python \
  scripts/prepare_c02_coaching_selection.py
```

현재 예상:

- exit 1
- `status=failed_closed`
- `stage=s107_completion`
- `error=S107 completion commit is TBD_S107_COMPLETION`
- C01 validator/deep validation/report/aggregate read `1/1/1/1`
- C01 protected read 0
- S107 completion path/read/module/deep 0
- preregistration 이후 모든 runtime/output count 0

두 명령 모두 artifact를 쓰지 않는다. 실패 JSON도 stderr로만 반환한다.

## 6. payload 계약 검증

순수 함수와 synthetic fixture로 다음을 고정했다.

- top-1 및 top-3 deterministic rank
- 입력 row 순서와 무관한 tie 결과
- priority 12자리 Decimal round-half-even
- threshold equality 통과 및 below-threshold rerecord
- unknown/other/evidence 부족/localization 위반 expert review
- blocked top-k no-backfill
- empty input `no_issues/none`
- exact C01 advice ID/message와 message SHA
- message tamper와 LLM-used flag tamper 거부
- duplicate issue, top-k 2, NaN, prompt field 거부
- approved preregistration의 exact S107 threshold binding
- output traversal/existing target/symlink/no-overwrite 거부

이 fixture는 합성 hash, 합성 phone label, 짧은 합성 catalog만 사용한다.
실제 diagnostic row나 coaching result를 읽거나 만들지 않는다.

검사 명령:

```bash
.venv-scorer/bin/ruff check \
  src/voice_coach/coaching/c02_selection_contract.py \
  scripts/prepare_c02_coaching_selection.py \
  tests/unit/test_c02_selection_contract.py \
  tests/integration/test_prepare_c02_coaching_selection.py

.venv-scorer/bin/ruff format --check \
  src/voice_coach/coaching/c02_selection_contract.py \
  scripts/prepare_c02_coaching_selection.py \
  tests/unit/test_c02_selection_contract.py \
  tests/integration/test_prepare_c02_coaching_selection.py

.venv-scorer/bin/mypy \
  src/voice_coach/coaching/c02_selection_contract.py \
  scripts/prepare_c02_coaching_selection.py

.venv-scorer/bin/pytest -q \
  tests/unit/test_c02_selection_contract.py \
  tests/integration/test_prepare_c02_coaching_selection.py
```

검사 결과:

- Ruff check: 통과
- Ruff format check: 통과
- mypy: 통과
- C02 targeted unit/integration: `21 passed`
- C01 ontology/prioritizer + C02 regression: `33 passed`
- R00–R04 + C01 + C02 unit/integration regression: `140 passed`

## 7. 현재 blocker

순서상 blocker:

1. 실제 S107 completion commit SHA
2. 실제 S107 threshold policy report SHA
3. S107 deep recomputation 통과
4. exact confidence semantics
5. phone probability→issue confidence aggregation
6. exact threshold를 S107 선택값과 동일하게 승인하는 규칙
7. 17개 C01 issue type별 supported evidence matrix
8. minimum supported evidence count
9. 독립 protocol owner의 frozen preregistration와 signed receipt
10. C03 한국어 음성학 전문가 200건 안전 감사

1–9가 끝나기 전 실제 D1 selection executor를 등록할 수 없다. C03 전에는
실험용 output이 있더라도 production release를 허용할 수 없다.

## 8. 미래 executor 인계 조건

- S107이 실제 완료되면 commit/threshold SHA만 추출하지 말고 S107
  deep validator로 generation, registry, threshold 값까지 재검증한다.
- C02 config의 두 S107 SHA를 exact 값으로 교체한 뒤 static source pin
  drift가 없는지 다시 검사한다.
- template을 덮어쓰지 않고 별도 approved artifact를 만든다.
- approved artifact에는 `TBD_*`가 없어야 하며 C01/S107 SHA와 exact
  threshold를 결합한다.
- preregistration을 diagnostic issue 접근 전에 SHA-256으로 동결한다.
- 실제 D1 path reader와 output publisher는 현재 readiness CLI에 추가하지
  않고 별도 등록 executor로 구현한다.
- publish 직전 input/output payload를 다시 검증하고 future target의
  traversal/symlink/no-overwrite를 확인한다.
- 같은 디렉터리 임시 파일, fsync, 원자 rename으로만 publish한다.
- D1 개별 결과와 A1 aggregate를 물리적으로 분리한다.
- C03 전 production/completion/publication flag는 false를 유지한다.

## 9. tracker 정책

이 package 작업에서는 `docs/constraints/RESEARCH_COMPLETION_TRACKER_KO.md`를 수정하지
않았다. 상위 작업자는 최종 파일 hash, targeted/regression test evidence,
그리고 현재 S107 blocker를 확인한 뒤 tracker에 C02 static readiness만
기록해야 한다. 실제 C02 완료로 표기하면 안 된다.
