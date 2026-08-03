# Phase 0 9축 label·missing-mask 순수 primitive

기준일: 2026-07-29 KST  
상태: **canonical in-memory 계약 구현 / 실제 9축 annotation·학습·평가 0**

## 1. 선택한 다음 gate

설계 PDF의 원칙 P1은 다음 아홉 차원을 하나의 벡터로 계속 유지하도록
요구한다.

1. 내용(`content`)
2. 분절음(`segmental`)
3. duration
4. pause
5. 억양(`intonation`)
6. energy
7. rhythm
8. 자연스러움(`naturalness`)
9. 이해가능성(`comprehensibility`)

현재 공개 `DiagnosticProfile`은 segmental·fluency·prosody·
comprehensibility 네 축이고, S104/S105의 발화 결과도 pronunciation·
fluency·comprehensibility 세 outcome이다. 어느 계약도 PDF의 9축과 각
축의 결측 여부를 함께 보존하지 않는다. 이번 구현은 이 표현 공백만
닫는다.

## 2. 구현 범위

`src/voice_coach/evaluation/nine_axis_labels.py`는 filesystem, dataset,
audio, model, GPU, network 또는 환경변수를 읽지 않는 표준 라이브러리
순수 primitive다.

- `AssessmentAxis`: PDF의 정확한 9축 enum
- `CANONICAL_AXIS_ORDER`: 위 표와 같은 고정 순서
- `build_nine_axis_label_vector(...)`: 모든 축을 명시한 mapping에서
  dense score와 별도 boolean mask 생성
- `select_observed_axis_pairs(...)`: 관측된 축의 prediction/target
  pair만 canonical 순서로 선택

입력 mapping은 9개 enum key를 정확히 한 번씩 포함해야 한다. 관측 점수는
caller가 이미 `[0, 1]`로 정규화한 finite real이어야 하며, 결측은
`None`으로 표시한다. 결측 위치의 dense score는 tensor 호환용 `0.0`
fill이지만 mask는 `False`다. 실제 관측값 `0.0`은 mask가 `True`이므로
결측과 구분된다.

정규화 주장은
`AxisScoreNormalization.CALLER_NORMALIZED_UNIT_INTERVAL`을 호출자가
keyword로 명시해야 한다. primitive가 AI Hub 1–5 점수, 모델 logit,
UTMOS, F0 또는 다른 원점수를 임의 변환하지 않는다.

## 3. fail-closed 계약

다음 입력은 `NineAxisLabelError`로 거부한다.

- 문자열 key, 축 누락 또는 비정본 axis key
- boolean, 문자열, NaN, Inf 또는 `[0, 1]` 밖 관측 점수
- 아홉 축 모두 결측인 row
- explicit enum이 아닌 normalization 값
- 길이가 9가 아닌 prediction, invalid prediction
- 결측 mask가 `False`인데 dense fill이 `0.0`이 아닌 위조 객체
- 중복되거나 canonical 순서가 아닌 observed-pair 객체

결과 payload에는 정책, axis order, score, mask와 count만 들어간다.
speaker/participant/audio/text/path/utterance identifier는 받지도
반환하지도 않는다.

## 4. 의도적으로 결정하지 않은 항목

이 primitive는 loss, axis weight, aggregation, threshold, pass/fail 또는
model head를 제공하지 않는다. 따라서 다음을 완료한 근거가 아니다.

- 아홉 축의 한국어 rubric, raw scale과 정규화 방법
- 축별 annotation availability·결측 기전(MCAR/MAR/MNAR) 감사
- 축별 전문가 신뢰도, ICC/QWK와 calibration
- S1/S2 9-head 학습, missing-mask loss wiring과 ablation
- 실제 AI Hub/CoreaSpeech/학습자 row 변환
- locked 평가, 사람 연구 또는 논문 성능 결과

다음 과학 gate는 사람 자료나 locked split을 열기 전에 train/dev 전용
rubric·raw-to-normalized adapter·축별 source lineage를 사전등록하고,
synthetic batch에서 학습 loss가 `False` mask 위치의 target 변화에
불변인지 검증하는 것이다. 그 뒤에만 실제 9축 annotation 또는 model
head와 연결할 수 있다.

## 5. 검증 명령

```bash
CUDA_VISIBLE_DEVICES='' PYTHONPATH=src .venv-scorer/bin/pytest -q \
  tests/unit/test_nine_axis_labels.py

.venv-scorer/bin/ruff check \
  src/voice_coach/evaluation/nine_axis_labels.py \
  tests/unit/test_nine_axis_labels.py

.venv-scorer/bin/ruff format --check \
  src/voice_coach/evaluation/nine_axis_labels.py \
  tests/unit/test_nine_axis_labels.py

.venv-scorer/bin/mypy \
  src/voice_coach/evaluation/nine_axis_labels.py
```

실행 결과:

- primitive unit: **31 passed**
- primitive + 기존 domain/API contract·AI Hub missing-mask loader·S105
  hierarchy 인접 회귀: **52 passed**
- Ruff check: 통과
- Ruff format check: 통과
- mypy: 통과

모든 pytest 실행은 `CUDA_VISIBLE_DEVICES=''`와 단일 BLAS/OpenMP thread로
수행했다. protected S104/S106 파일, GPU, locked split, 실제 음성,
사람 자료와 외부 artifact를 읽거나 수정하지 않았다.
