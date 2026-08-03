# E01 silence·gain waveform primitive

상태: **in-memory primitive 구현 및 합성 배열 검증 / 실제 E01 실행 0**

기준일: 2026-07-29 KST

## 범위

`src/voice_coach/evaluation/e01_waveform_transforms.py`는 1차원
`float32`/`float64` NumPy PCM에 대해서만 다음 두 순수 변형을 제공한다.

- 앞·뒤에 정확한 0 PCM sample 추가
- 전역 gain dB를 linear gain으로 변환해 적용

파일과 실제 음성을 읽거나 쓰지 않고, 모델·network·GPU를 사용하지 않는다.
원본 배열을 변경하거나 output과 메모리를 공유하지 않는다.

## 호출자 고정 계약

함수에는 실험 기본값이 없다. 호출자는 sample rate, 앞·뒤 silence 초,
sample-count rounding(`floor`, `ceil`, `half_up`), gain dB,
clipping policy(`error`, `clip`), peak limit, 최대 output sample 수를
모두 명시해야 한다.

- PCM은 non-empty mono, finite `float32`/`float64`여야 한다.
- sample rate와 최대 sample 수는 양의 정수여야 한다.
- silence 초는 finite non-negative 값이어야 한다.
- gain factor가 overflow 또는 0으로 underflow하면 실패한다.
- 입력 peak가 명시된 limit를 넘으면 `clip`을 선택해도 실패한다.
- gain 결과가 limit를 넘으면 `error`는 실패하고, `clip`만 명시적으로
  제한한다.
- silence output sample 수가 caller budget을 넘으면 allocation 전에
  실패한다.

반환 metadata에는 변형 종류와 명시 파라미터, input/output sample 수와
peak, output SHA-256 digest만 포함한다. 배열, 파일 경로, transcript,
speaker/participant/item/audio ID는 포함하지 않는다. Digest도 실제 E01
aggregate report에 그대로 게시하는 계약은 아니다.

## 검증 경계

Synthetic NumPy 배열 테스트는 다음을 확인한다.

- exact appended zeros와 세 rounding policy
- 알려진 dB-to-linear ratio
- 원본 불변과 새 allocation
- 결정적 output/digest
- clipping fail 및 explicit clip
- empty/2-D/integer/NaN/Inf/non-array 입력 거부
- invalid sample rate, seconds, policy, peak, budget 거부
- gain overflow/underflow와 output-budget 초과 거부
- metadata field allowlist와 raw array/identifier 부재

이는 silence/gain executor의 가장 작은 wiring primitive일 뿐이다.
Magnitude, expected relation, tolerance, aggregation, invalid-case policy는
여전히 독립 사전등록 대상이다. Duration/F0/phone-substitution 변형,
실제 audio/model inference, metric과 pass-rate 계산은 구현하지 않았다.
따라서 E01 robustness 통과, `>=0.95` 달성, 사전등록 완료 또는 연구
완료를 주장하지 않는다.
