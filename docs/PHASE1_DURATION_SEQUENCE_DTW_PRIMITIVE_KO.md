# Phase 1 m3 duration-sequence DTW primitive

작성: 2026-07-29 KST  
상태: **순수 수치 primitive 완료 / 실제 정렬·발화 실험 미실행**

## 1. 구현 경계

`src/voice_coach/evaluation/duration_sequence_dtw.py`는 호출자가 메모리에
제공한 두 duration tuple을 DTW로 비교하는 dependency-light 계산
모듈이다.

- audio, 파일 경로, corpus row, cache, dataset split을 읽지 않는다.
- MFA, CTC, TextGrid parser, pitch backend, network, GPU를 호출하지 않는다.
- locked evaluation item을 선택하거나 접근하지 않는다.
- 기존 `npvi()`처럼 한 sequence 내부 변동을 계산하지 않고, 두 sequence
  사이의 기술적 비용과 경로를 계산한다.
- 기존 `align_intervals()`처럼 timestamp overlap이나 phone label을 사용하지
  않는다.
- 입력 duration은 모두 seconds 단위의 finite positive real이어야 한다.
- 연구 판정 threshold와 성공 기준에는 기본값도 구현값도 없다.

따라서 이 모듈의 입력은 upstream segmentation이 끝난 뒤의 순수 수치
sequence다. 이 primitive 자체는 duration을 추출하거나 boundary의
타당성을 판정하지 않는다.

## 2. provenance 계약

`DurationSequence`는 exact tuple과 caller-attested
`DurationSequenceProvenance`를 함께 요구한다.

| 함수 인자 | 허용 provenance | 의미 |
|---|---|---|
| `predicted` | `predicted_automatic` | 평가 대상 자동 예측 sequence |
| `reference` | `reference_automatic` | 자동 절차에서 나온 비교 sequence |
| `reference` | `reference_human_reviewed` | 사람이 검토했다고 호출자가 명시한 비교 sequence |
| `reference` | `reference_caller_supplied_unverified` | origin을 이 모듈이 확인하지 못한 비교 sequence |

`predicted`와 `reference`를 바꾸거나 문자열로 enum을 우회하면 실패한다.
`reference`는 비용 방향과 비교 역할만 정한다. 이 모듈은 provenance
attestation을 외부 자료로 검증하지 않으며, reference boundary의 정확성도
추론하지 않는다.

## 3. caller-explicit DTW 정책

모든 항목에는 기본값이 없다. 호출자는 exact `DurationDtwPolicy`와 exact
enum 값으로 다음을 모두 지정해야 한다.

### 3.1 local cost

reference duration을 \(r_i\), predicted duration을 \(p_j\)라 하면:

- `absolute_seconds`: \(c(i,j)=|p_j-r_i|\)
- `absolute_log_ratio`:
  \(c(i,j)=|\ln(p_j)-\ln(r_i)|\)

log-ratio 구현은 먼저 비율을 계산하지 않아 극단적으로 작거나 큰 finite
positive 입력의 ratio underflow·overflow를 피한다. 파생 local 또는 누적
비용이 finite하지 않으면 결과를 반환하지 않는다.

### 3.2 band

- `unbounded`: 모든 index pair를 허용하며 `band_radius=None`이어야 한다.
- `sakoe_chiba_absolute_index`: zero-based index에서
  \(|i-j|\le radius\)인 cell만 허용한다. radius는 caller-explicit
  non-negative integer다.

이 band는 sequence 길이에 맞춰 정규화한 band가 아니라 absolute-index
band다. 두 endpoint를 연결할 수 없는 길이 차이와 radius 조합은
fail closed한다.

### 3.3 step과 tie

- `symmetric_unit`: `(1,1)`, `(1,0)`, `(0,1)` predecessor를 허용한다.
- `diagonal_only`: `(1,1)`만 허용하며 두 sequence 길이가 같아야 한다.

각 방문 cell의 local cost는 한 번 더한다. symmetric step의 이름은 다음과
같다.

- `diagonal`: reference와 predicted index를 함께 진행
- `reference_advance`: reference index만 진행
- `predicted_advance`: predicted index만 진행

누적 비용이 정확히 같을 때 숨은 epsilon을 적용하지 않는다. 호출자는
아래 tie 순서 중 하나를 명시한다.

- `diagonal_reference_predicted`
- `reference_diagonal_predicted`
- `predicted_diagonal_reference`

### 3.4 normalization

raw `total_cost`는 항상 반환한다. `normalized_cost`의 분모는 호출자가
다음 중 하나를 명시한다.

- `none`: 1
- `path_cell_count`: 선택 경로의 cell 수
- `reference_count`: reference sequence 길이
- `predicted_count`: predicted sequence 길이

어느 normalization도 논문용 선택으로 고정하지 않았다.

### 3.5 max-cell

`max_cells`는 admissible band cell 수에 적용되는 caller-explicit positive
integer다. 계산 전에 상한을 넘으면 실패한다. 구현 자체의 방어 상한은
`1,000,000` admissible cells다.

이 값들은 메모리·실행시간 보호 정책이며 연구 판정 threshold가 아니다.
`full_lattice_cell_count`와 `admissible_cell_count`를 따로 보고하므로,
narrow band에서는 full Cartesian lattice가 caller 상한보다 커도 실제
admissible cell 수가 상한 이하면 실행할 수 있다.

## 4. 반환과 path 공개 정책

`DurationDtwResult`는 다음 기술 요약만 보존한다.

- 두 입력의 provenance enum
- local cost, band/radius, step, tie, normalization, max-cell, path 정책
- 두 sequence 길이
- full/admissible/reachable cell 수
- path cell 수와 세 step 종류별 count
- raw cost, normalization 분모, normalized cost
- caller가 허용한 경우에만 zero-based index pair path
- 위 공개 필드를 canonical JSON으로 묶은 SHA-256

path 공개는 호출자가 다음 중 하나를 반드시 선택한다.

- `summary_only`: index path를 반환하지 않는다.
- `return_index_pairs`: duration 값 없는 `(reference_index,
  predicted_index)` tuple만 반환한다.

결과에는 입력 duration tuple, boundary timestamp, phone label, 전사, audio,
파일 경로, speaker/utterance/participant ID가 없다. 그러나 scalar 요약과
digest도 익명화 수단은 아니므로 실제 사람 데이터 artifact에서는 별도
접근 통제가 필요하다.

## 5. 실패 계약

다음 조건은 `DurationSequenceDtwError`로 fail closed한다.

- exact input/policy dataclass 또는 exact tuple/enum이 아님
- 빈 sequence, boolean·비실수·NaN·Inf·zero·negative duration
- reference/predicted provenance 역할 불일치
- unbounded band에 radius를 제공하거나 band radius가 유효하지 않음
- diagonal-only의 sequence 길이가 다름
- band가 두 endpoint를 연결하지 못함
- admissible cell 수가 caller 상한 또는 구현 방어 상한을 넘음
- caller 정책 아래 연결 경로가 없음
- local, 누적, normalized cost가 finite하지 않음

오류 메시지는 duration 값, 경로, identifier, backend 상세를 echo하지
않는다.

## 6. 검증

```bash
CUDA_VISIBLE_DEVICES='' .venv-scorer/bin/python -m pytest -q \
  tests/unit/test_duration_sequence_dtw.py
```

결과: **66 passed**.

검증 범위에는 다음이 포함된다.

- absolute-seconds와 natural-log-ratio 수식
- repeated predicted element의 symmetric warp
- 네 normalization 분모
- absolute-index band와 admissible max-cell 계산
- diagonal-only와 endpoint 연결 실패
- 세 tie 순서의 exact zero-cost path
- provenance role과 caller attestation 보존
- exact dataclass/tuple/enum, finite-positive, hard-limit 실패 계약
- summary-only와 index-only path 반환
- deterministic·policy-sensitive digest와 JSON finite 직렬화
- 극단적 finite duration의 log-ratio 및 누적 overflow fail-closed
- 오류 메시지의 raw duration 비노출

추가 정적 검증:

```bash
.venv-scorer/bin/python -m ruff check \
  src/voice_coach/evaluation/duration_sequence_dtw.py \
  tests/unit/test_duration_sequence_dtw.py
.venv-scorer/bin/python -m ruff format --check \
  src/voice_coach/evaluation/duration_sequence_dtw.py \
  tests/unit/test_duration_sequence_dtw.py
.venv-scorer/bin/python -m mypy \
  src/voice_coach/evaluation/duration_sequence_dtw.py \
  tests/unit/test_duration_sequence_dtw.py
```

결과: Ruff check/format 및 mypy 모두 통과.

## 7. 현재 비주장과 다음 gate

현재 결과는 **duration-sequence DTW 계산 배선**만 완료했다. 다음을
완료하거나 대체하지 않는다.

- MFA 또는 다른 segmentation runtime 설치·실행
- 실제 외국어 사용자·한국어 사용자 audio의 duration 추출
- 자동 boundary와 사람 검토 boundary의 일치도 감사
- phone/syllable/word 단위 선택과 insertion/deletion 해석 사전등록
- local cost, band, step, tie, normalization의 논문용 고정
- learner/reference slice 실행, bootstrap/CI, 독립 평가
- m3 성능, 개선 효과, 통계적 유의성 주장

다음 안전 gate는 승인된 runtime에서 non-locked 소수 샘플의 boundary를
사람이 먼저 감사한 뒤, 비교 단위와 모든 DTW 정책을 사전등록하고 이
primitive에 duration tuple만 넘기는 것이다.
