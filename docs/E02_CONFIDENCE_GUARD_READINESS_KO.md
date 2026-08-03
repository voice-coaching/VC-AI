# WP8 E02 confidence-boundary automatic-correction guard 정적 준비도

기준일: 2026-07-29 KST  
판정: **production static package 구현 / 실제 E02 실행 0**

## 1. 구현 파일

| 파일 | 역할 |
|---|---|
| `configs/experiments/e02_confidence_guard_v1.yaml` | S107→C02→E02 prereg 순서와 non-execution 계약 |
| `configs/coaching/e02_confidence_guard_input_v1.schema.json` | score-free D1 guard input |
| `configs/coaching/e02_confidence_guard_api_response_v1.schema.json` | blocked reason/provenance/safe-action API envelope |
| `configs/coaching/e02_c02_coaching_bundle_commit_v1.schema.json` | actual C02 deterministic bundle handoff |
| `configs/coaching/e02_confidence_guard_preregistration_v1.template.json` | OOD·support·abstain·combination fail-closed template |
| `src/voice_coach/evaluation/e02_confidence_guard_contract.py` | static/deep/pure guard/read-only contract |
| `scripts/prepare_e02_confidence_guard.py` | config-only/full read-only preflight |
| `tests/unit/test_e02_confidence_guard_contract.py` | policy·privacy·lineage·block synthetic tests |
| `tests/integration/test_prepare_e02_confidence_guard.py` | CLI ordering/access/output counter tests |
| `docs/E02_CONFIDENCE_GUARD_DATA_MODEL_API_SAFETY_CARD_KO.md` | data/model/API/privacy/safety card |

Tracker와 기존 S107/C02/E01/API 코드는 수정하지 않았다. Actual endpoint,
request reader, renderer, reference resolver와 publisher는 구현하지 않았다.

## 2. 근거 감사와 고정 범위

`IMPLEMENTATION_PLAN_KO.md` Phase 4는 confidence가 기준보다 낮으면
재녹음을 요청하고 evidence 없는 오류를 언급하지 않도록 한다. C02는
low-confidence `rerecord`, unsupported `expert_review`, blocked top-k
no-backfill과 free-generation 금지를 고정한다. S107은 actual threshold
policy의 유일한 정본이다.

이에 따라 E02는 다음만 고정한다.

- `out_of_domain`, `low_confidence`, `unsupported`, `abstain_all`은 모두
  자동 advice/reference/corrective action 차단
- blocked safe action은 `rerecord` 또는 `expert_review`
- S107 numeric threshold를 E02 config/prereg/input/API response에 복제 금지
- actual S107 threshold report를 deep validation해 categorical gate만 사용
- C02 actual bundle을 deterministic recomputation
- blocked/no-backfill/catalog-only/free-generation 금지 승계
- API response에는 reason code, evidence hash와 safe action code만 허용
- score·PII·raw path·free text·교정 payload 금지

근거가 없는 OOD 정의·rule/threshold, unsupported/abstain-all 운영 정의와
mixed-reason precedence/action 조합은 `TBD_PREREGISTRATION`이다.

## 3. predecessor-first 순서

Full preflight 순서는 다음과 같다.

1. E02 config, 25개 static pin, 세 schema와 fail-closed template 검증
2. exact S107 commit/threshold SHA가 있는지 path 해석 전에 확인
3. actual S107 commit·registry·generation·threshold deep validation
4. exact C02 completed bundle SHA가 있는지 path 해석 전에 확인
5. pinned C02 contract로 C01/S107/prereg/input/output/registry 재검증
6. no-TBD frozen E02 preregistration 검증
7. guard input/request/API/model/output path 전에 정지

현재 S107 commit와 threshold report가 `TBD_S107_COMPLETION`이다. 따라서
full preflight는 `stage=s107_completion`에서 끝나며 S107 path/read/module,
C02 path/read/deep, E02 preregistration과 이후 모든 runtime 접근은 0이다.

S107이 해결돼도 C02가 `TBD_C02_COMPLETION`이면 C02 commit path resolution
전에 종료한다.

## 4. Pure guard 검증

Synthetic fixture는 실제 score나 threshold를 사용하지 않고 categorical
상태와 hash만으로 다음을 확인한다.

- 각 단일 block condition이 세 자동 capability를 모두 false로 만듦
- 복합 block reason의 순서와 safe action이 frozen combination policy를
  정확히 따름
- low-confidence rerecord와 unsupported/expert-review 정책
- C02 abstain-all을 output count에서 재계산
- blocked top-k no-backfill 및 free-generation false 승계
- in-domain·within-policy·supported·selected만 별도 renderer eligibility
- no-issues는 교정 없음
- response exact recomputation과 tamper 거부
- model score, PII, raw path, free text, advice payload 주입 거부
- S107/C02/E02 lineage hash mismatch 거부
- unresolved preregistration와 numeric threshold 추가 거부
- traversal, symlink, existing output/no-overwrite 위반 거부

Fixture의 `allowed_for_separate_renderer`는 actual API call이나 coaching
delivery가 아니다.

## 5. CLI

정적 검사:

```bash
PYTHONPATH=src .venv-scorer/bin/python \
  scripts/prepare_e02_confidence_guard.py --config-only
```

예상:

- `status=valid_config_only`
- source pin read 25
- predecessor/request/API/model/GPU/metric/network/LLM/output 0

현재 predecessor-first 검사:

```bash
PYTHONPATH=src .venv-scorer/bin/python \
  scripts/prepare_e02_confidence_guard.py
```

예상:

```text
stage=s107_completion
S107 completion commit is TBD_S107_COMPLETION
```

Static source TOCTOU만 25회이며 S107/C02 path/read는 0이다. 두 명령 모두
artifact를 쓰지 않고 실패 JSON은 stderr로만 반환한다.

## 6. 정적 검증 결과

2026-07-29 KST 현재 다음 검증을 통과했다.

- E02 unit/integration: 25 passed
- S107/C02/E01/API 인접 회귀: 69 passed
- Ruff check와 Ruff format check: 통과
- mypy: E02 contract/CLI 2개 파일, issue 0
- config-only: `status=valid_config_only`, source read 25, 그 외 접근 0
- full: 의도대로 `stage=s107_completion`에서 fail-closed, static source
  TOCTOU 25, S107/C02/E02/runtime/output 접근 0

현재 config SHA-256은
`2223aa6c692485027810bcf1b1e309e141192baf774e660902eaa72c2db48b27`,
정규화된 policy SHA-256은
`1b89c8fd8848467d20d9096f5e2757806d41617c8181b7d25e7762113d56a41e`이다.

## 7. 현재 blocker와 미래 인수인계

순서상 blocker:

1. actual S107 completion commit·threshold report
2. `selected_provisional_engineering`이 아닌 abstain-all 상태 해소
3. actual C02 config/prereg/diagnostic input/selection output/registry bundle
4. C02 deterministic deep recomputation
5. OOD population·detector/rule·threshold policy
6. unsupported와 abstain-all 운영 정의
7. mixed-reason precedence와 reason별 safe action
8. 독립 protocol owner의 E02 approval/freeze
9. 별도 API/renderer executor의 안전·개인정보 검토

향후에는 template을 덮어쓰지 않고 별도 approved preregistration을 만든다.
S107 numeric threshold는 복사하지 않고 actual report SHA를 결합한다.
Actual C02 bundle과 E02 policy를 깊게 검증한 뒤에도 현재 CLI에 request/API
실행을 추가하지 않는다. 별도 executor가 input/output payload를 재검증하고
blocked 상태에서 세 capability가 모두 false인지 확인해야 한다.

## 8. 완료·배점 경계

정적 준비 완료는 다음을 의미하지 않는다.

- actual S107 또는 C02 완료
- OOD detector 성능
- 자동 교정 안전성·정확도
- FastAPI production endpoint
- C03 사람 감사 또는 deployment
- E02 연구 완료와 WP8 배점 획득

실제 E02 completion bundle과 독립 검토가 생기기 전 tracker E02 상태와
점수는 그대로 유지한다.
