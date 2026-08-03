# Phase 1 m4 공통 시간축 pitch contour agreement primitive

작성: 2026-07-29 KST  
상태: **순수 통계 primitive 완료 / 실제 한국어 발화 agreement 미실행**

## 1. 구현 경계

`src/voice_coach/evaluation/pitch_contour_agreement.py`는 Parselmouth와
torchcrepe처럼 서로 다른 time grid를 반환하는 두 F0 contour를
호출자가 제공한 공통 시간축에서 비교하는 순수 계산 모듈이다.

- audio·파일 경로·corpus row·split·cache를 읽지 않는다.
- pitch backend, GPU, network를 호출하지 않는다.
- `PitchContourSeries`는 `time_seconds`와 `f0_hz`의 exact tuple만 받는다.
- time은 finite, non-negative, strictly increasing이어야 한다.
- F0는 finite, non-negative이며 `0.0 Hz`만 unvoiced로 해석한다.
- common grid와 모든 정책, octave 허용폭, frame 상한에는 기본값이 없다.
- `reference`는 signed error의 방향만 정하며 사람 정답이라는 주장이 아니다.

기존 두 strict adapter의 `time_seconds`와 `f0_hz` tuple을
`PitchContourSeries`로 감싸면 되지만, 이 primitive가 adapter 객체나
실제 backend에 의존하지는 않는다.

## 2. 호출자 명시 정책

| 정책 | 허용 값 | 의미 |
|---|---|---|
| interpolation | `linear` | 양쪽 source point로 선형 보간 |
| interpolation | `nearest_earlier_on_tie` | nearest, 정확한 동률이면 이른 frame |
| interpolation | `nearest_later_on_tie` | nearest, 정확한 동률이면 늦은 frame |
| endpoint | `require_shared_support` | common grid 전체가 두 contour의 닫힌 공통 지원구간 안에 없으면 실패 |
| endpoint | `trim_to_shared_support` | 닫힌 공통 지원구간 밖 common point를 제거하고 제거 수를 보고 |
| unvoiced | `preserve_gaps` | exact zero를 유지하며 linear 보간은 양쪽 frame이 모두 voiced일 때만 수행 |
| unvoiced | `bridge_internal_gaps` | voiced source point만 사용해 첫/마지막 voiced point 사이의 내부 gap을 연결 |

`bridge_internal_gaps`도 첫 voiced point 이전이나 마지막 voiced point 이후를
외삽하지 않는다. endpoint 비교에는 숨은 epsilon이 없고 source endpoint는
포함한다.

`octave_tolerance_cents`는 호출자가 `0 <= tolerance < 600` 범위에서
명시한다. 600 cents 제한은 0-octave와 1-octave 최근접 영역의
수학적 중점을 넘지 않게 하는 계약이며, 논문용 허용폭은 별도
사전등록해야 한다. octave error는 jointly voiced frame의 signed cents
error가 최근접 non-zero `1200 cents` 정수배에서 이 허용폭 이내인지를
inclusive하게 센 기술 통계다.

## 3. 반환 통계

`PitchContourAgreementResult`는 다음만 보존한다.

- 적용 정책과 caller-explicit octave 허용폭
- source/requested/evaluated/trimmed frame count
- 공통 grid에서 resample된 reference/candidate voiced count
- `jointly voiced`, `reference only voiced`, `candidate only voiced`,
  `jointly unvoiced`의 4분할 count
- voicing agreement rate와 jointly voiced rate
- jointly voiced frame의 candidate-minus-reference signed mean
- Hz와 cents의 mean absolute, median absolute, RMSE, maximum absolute error
- octave error count/rate
- 위 요약 결과만 canonical JSON으로 묶은 SHA-256

Hz error는 `candidate - reference`, cents error는
`1200 * (log2(candidate) - log2(reference))`다. voiced/unvoiced 4분할은
gold accuracy·precision·recall이라는 이름을 사용하지 않는다.

결과에는 source contour, common time grid, PCM, path, speaker/utterance/
participant ID, 전사가 없다. 결과 digest도 익명화 수단은 아니므로 실제
데이터 artifact에서는 접근을 제한해야 한다.

## 4. 실패 계약

다음 조건은 `PitchContourAgreementError`로 고정된 메시지와 함께
fail closed한다.

- exact dataclass/tuple이 아닌 입력, 빈 contour/grid, frame 수 불일치
- boolean·비실수·NaN·Inf, 음수 time/F0, 중복·감소 time
- caller frame 상한 초과 또는 유효하지 않은 정책/tolerance
- 두 contour에 공통 time support가 없음
- strict endpoint 위반, trim 뒤 common point가 없음
- 공통 grid에서 jointly voiced frame이 하나도 없음
- 보간 또는 파생 통계가 finite하게 표현되지 않음

오류에는 contour 값, backend 상세, 경로, ID를 넣지 않는다.

## 5. 검증과 비주장

```bash
CUDA_VISIBLE_DEVICES='' .venv-scorer/bin/python -m pytest -q \
  tests/unit/test_pitch_contour_agreement.py
```

결과: **69 passed**.

검증에는 linear/nearest 보간, nearest 동률 방향, 두 endpoint·unvoiced
정책, 4분할 count, Hz/cents/RMSE/octave 수식, caller tolerance 변화,
single-frame/large-finite 입력, deterministic digest, 개인정보 최소 출력,
입력·정책·무 overlap 실패 계약이 포함된다.

이 결과는 다음을 완료하거나 대체하지 않는다.

- 실제 외국어 사용자·한국어 사용자 발화 실행
- amplitude/segmentation/F0 floor-ceiling/common-grid 정책 사전등록
- learner/native 및 성별·연령·L1·억양 slice agreement
- speaker normalization, 경계톤·prominence, 독립 사람 annotation
- backend calibration, m4 ablation, bootstrap/CI, 논문 성능 주장

따라서 현재 판정은 **공통 시간축 기술 통계 배선 완료**이며
**PDF Phase 1 m4 실험 완료**가 아니다.
