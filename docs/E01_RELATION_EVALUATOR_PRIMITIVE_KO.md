# E01 numeric relation evaluator primitive

상태: **순수 수치 관계·동일 case 가중 count 집계 구현 / 실제 E01 실행 0**

기준일: 2026-07-29 KST

## 범위

`src/voice_coach/evaluation/e01_relation_evaluator.py`는 future E01
executor가 이미 계산한 base/transformed metric 한 쌍을 메모리에서
비교하는 최소 primitive다. 다음 다섯 family를 enum으로 표현한다.

- `silence`
- `gain`
- `duration`
- `f0`
- `phone`

기존 E01 정적 계약의 relation 네 종류인 `invariant`,
`monotonic_nonincreasing`, `monotonic_nondecreasing`,
`targeted_change`만 허용한다. 어느 family에 어느 relation 또는 metric을
사용할지는 추론하지 않는다.

## 명시 입력과 비교 의미

호출자는 case마다 다음 값을 모두 명시해야 한다.

- family
- relation
- direction
- boundary rule(`inclusive` 또는 `strict`)
- tolerance 또는 minimum margin
- base metric
- transformed metric

숨은 epsilon이나 기본 tolerance는 없다.

| relation | 허용 direction | 비교 |
|---|---|---|
| `invariant` | `none` | `abs(transformed - base)`가 tolerance 이내 |
| `monotonic_nonincreasing` | `decrease` | 증가 drift가 tolerance 이내 |
| `monotonic_nondecreasing` | `increase` | 감소 drift가 tolerance 이내 |
| `targeted_change` | `increase`, `decrease`, `either` | 지정 방향 변화가 minimum margin 이상 |

경계의 동일값을 통과시킬지는 `inclusive`/`strict`로 호출자가 고정한다.
Inclusive targeted change에서 margin 0은 무변화도 통과시키므로
fail-closed로 거부한다. 입력은 boolean이 아닌 finite real이어야 하고,
차이 계산이 overflow해도 실패한다.

## 집계와 개인정보 경계

집계 함수는 호출자가 다음을 명시할 때만 실행한다.

- 포함해야 할 family의 정확한 목록
- `equal_per_case` weighting policy

모든 입력 case를 다시 검증하고, 한 family 안에서
relation/direction/boundary/tolerance가 달라지면 실패한다. Missing family,
scope 밖 family와 invalid case도 제외하거나 보정하지 않고 전체 호출을
실패시킨다. 출력은 canonical five-family 순서의 family별/전체
case·pass·fail count와 count-derived pass rate뿐이다.

Per-case 결과는 base/transformed metric을 보존하지 않는다. Per-case와
aggregate 결과 모두 case/item/speaker/participant/audio ID, transcript,
target text, 파일·모델 경로를 받거나 반환하지 않는다. Aggregate에는
case payload가 포함되지 않는다.

## 의도적으로 하지 않는 일

이 primitive는 다음을 수행하지 않는다.

- perturbation magnitude, 대상 metric 또는 expected relation 선택
- audio/file/model/network/GPU 접근
- waveform 변형, inference 또는 metric 계산
- 여러 metric을 하나의 utterance case로 결합하는 규칙 선택
- missing/invalid 제외, family weighting, minimum support 또는 CI 계산
- E01의 `0.95` gate statistic 적용, pass/fail 결정 또는 artifact 발행

따라서 이 구현은 실제 metamorphic case 실행, robustness 통과,
사전등록 완료 또는 연구 완료의 증거가 아니다. S108 actual completion 뒤
독립 사전등록에서 metric, magnitude, relation, direction, tolerance,
boundary, case 정의와 집계 정책을 모두 동결한 후 별도 restricted
executor가 사용해야 한다.

## 합성 검증

Unit test는 네 relation의 경계, 세 targeted direction, inclusive/strict,
다섯 family 집계, input-order 독립성, enum 강제, non-finite/overflow,
policy drift, missing/out-of-scope family, invalid-case fail-closed와
결과 field allowlist를 확인한다.

실제 음성, 선택 모델, protected split과 출력 artifact에는 접근하지 않았다.

```bash
CUDA_VISIBLE_DEVICES='' PYTHONPATH=src .venv-scorer/bin/pytest -q \
  tests/unit/test_e01_relation_evaluator.py
```

- 신규 evaluator unit: **44 passed**
- 기존 E01 계약·CLI·silence/gain을 합친 회귀: **111 passed**
- Ruff check/format과 mypy: 통과
