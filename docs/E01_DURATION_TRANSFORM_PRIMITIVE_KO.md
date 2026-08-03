# E01 정렬 구간 duration 변형 primitive

상태: **seeded in-memory primitive와 유·무성 혼합 합성 smoke 완료 /
production worker receipt·실제 발화·S1·S2 평가 0**

기준일: 2026-07-29 KST

## 범위

`src/voice_coach/evaluation/e01_duration_transform.py`는 caller가 이미
정렬한 한 구간의 평균 duration factor를 제어하는 in-memory primitive다.
`praat-parselmouth==0.4.7`과 runtime `PRAAT_VERSION==6.1.38`을 함께
검증하고, 그 안의
`Manipulation`/`DurationTier`와 overlap-add resynthesis를 사용한다.
파일·network·GPU·모델 inference는 사용하지 않는다.

Praat의 공식 설명에 따르면 DurationTier 값은 원본 대비 상대 duration이며,
점 사이에서는 선형 보간된다. Manipulation은 원본 sound, pulse,
PitchTier, DurationTier를 사용해 overlap-add로 재합성한다.

- <https://praat.org/manual/DurationTier.html>
- <https://praat.org/manual/Intro_8_2__Manipulation_of_duration.html>
- <https://praat.org/manual/Manipulation.html>
- <https://parselmouth.readthedocs.io/_/downloads/en/stable/pdf/>

## 독립 감사에서 교정한 두 결함

첫 구현은 ramp 중심을 정렬 경계에 놓아 target 구간 밖까지 duration
control이 번졌고, target 구간의 실제 평균 factor도 요청값과 달랐다.
또한 순수 sine에서 관측한 양 끝 비결정성만 보고 원본 prefix/suffix를
hard splice했으나, 독립 감사에서 내부 무성 구간도 매번 달라지고 splice
jump도 커지는 것을 확인했다. 이 방식과 첫 smoke digest는 폐기했다.

Parselmouth 0.4.7에 포함된 Praat source의 DurationTier overlap-add는
모든 무성 구간에서 `NUMrandomUniform(0.008, 0.012)`를 호출한다.
overlap-add 명령 자체에는 seed 인자가 없고 RNG는 process-global이다.

- <https://github.com/YannickJadoul/Parselmouth/blob/326cad5acfa5b952ffe28e8ccf0fe57e2a7d404e/praat/fon/Manipulation.cpp#L329-L478>
- <https://github.com/YannickJadoul/Parselmouth/blob/326cad5acfa5b952ffe28e8ccf0fe57e2a7d404e/praat/fon/praat_Fon.cpp#L1291-L1295>
- <https://github.com/YannickJadoul/Parselmouth/blob/326cad5acfa5b952ffe28e8ccf0fe57e2a7d404e/praat/fon/manual_Script.cpp#L870-L874>
- <https://github.com/YannickJadoul/Parselmouth/blob/326cad5acfa5b952ffe28e8ccf0fe57e2a7d404e/praat/melder/NUMrandom.cpp#L173-L214>

고정 seed 없이 내부에 noise 구간을 넣은 합성 입력을 12회 반복했을 때
outside splice 뒤에도 내부 1,330 sample이 변했고 12개 digest가 모두
달랐다. 같은 splice는 고정 seed sine의 suffix 경계 jump를
`0.0010257372`에서 `0.0189407425`로 키웠다. 따라서 원본 hard splice는
현재 구현에서 완전히 제거했다.

## 정렬 구간 안에 한정한 제어 곡선

입력 구간을 `[s, e]`, 길이를 `L=e-s`, transition을 `t`, 요청한
구간 평균 factor를 `k`라고 한다. control plateau `p`는 다음처럼
계산한다.

`p = (kL - t) / (L - t)`

DurationTier의 6개 점은 다음과 같다.

| time | relative duration |
|---:|---:|
| input start | 1 |
| `s` | 1 |
| `s + t` | `p` |
| `e - t` | `p` |
| `e` | 1 |
| input end | 1 |

두 ramp와 plateau의 `[s,e]` 면적은 정확히 `kL`이고, control support는
정렬 구간 밖으로 나가지 않는다. 예를 들어 `[0.2,0.4]`, `t=0.02`,
`k=1.2`이면 `p=1.2222222222222223`이고 전체 DurationTier 면적은
`1.04 s`다. metadata에는 요청 평균 factor와 실제 plateau factor,
연속시간 목표와 tier area/error를 모두 분리해 남긴다.

다음 조건은 backend 호출 전에 강제한다.

- mono finite base NumPy `float32`/`float64`, 정확한 16 kHz
- start, end, transition이 16 kHz sample grid에 정확히 위치
- `e > s`, `L > 2t`, 입력 양 끝에 분석 margin 존재
- positive factor, `k != 1`, positive plateau
- sample rounding은 pinned `half_up`만 허용
- sample error allowance는 analysis time step 이하
- 이론 output은 caller budget과 Praat 3× input cap 이내
- clipping·normalization 없이 명시 peak limit 유지
- output `xmin=0`, `xmax-xmin=DurationTier`의 연속시간 면적
- 이론 sample count는 연속시간 목표에 pinned `half_up`을 적용하고,
  실제 resynthesis sample count는 caller가 허용한 오차 안인지 별도 검증
- DurationTier exact, PitchTier control point exact 불변

PitchTier object의 불변은 output F0 보존 증거가 아니다. F0/voicing은
별도 backend로 다시 측정해야 한다.

## RNG와 process isolation 계약

caller는 seed와 다음 exact policy를 반드시 전달한다.

- seed 범위: 정수 `1..2147483647`
- isolation:
  `dedicated_single_job_subprocess`
- reset:
  `safe_unpredictable_finally_then_process_exit`

backend는 overlap-add 직전에 고정 script
`random_initializeWithSeedUnsafelyButPredictably(seed)`를 실행하고,
`finally`에서 `random_initializeSafelyAndUnpredictably()`를 호출한다.
후자는 이전 RNG state 복원이 아니라 새 상태 초기화다. 공개 snapshot/
restore API가 없으므로 실제 호출은 한 job만 수행하는 별도 process에서
끝나야 한다.

injected test backend 결과는 metadata에서
`forbidden_test_injection`으로 표시한다. pinned runtime도 독립
worker receipt가 없으면 곧바로 논문 근거가 되지 않으며
`requires_external_worker_receipt` 상태다. waveform digest는 동일 음성
연결에 사용될 수 있으므로 공개 결과가 아니라 제한된 내부 재현 근거다.

## 유·무성 혼합 합성 smoke

`.venv-paper-acoustics`의 별도 Python process 다섯 개에서 각각 한 job만
실행했다. 입력은 16 kHz, 1초, 180 Hz sine에 NumPy seed 777로 만든
고정 noise 구간 두 개를 삽입한 합성 파형이다.

- target `[0.2, 0.4]`초
- transition `0.02`초
- requested mean factor `1.2`
- Praat seed `5489`
- analysis `10 ms`, `75–300 Hz`
- sample rounding `half_up`
- maximum duration error `0`
- peak limit `0.5`

다섯 process의 결과가 모두 같았다.

- input/theoretical/actual sample: `16000 / 16640 / 16640`
- output domain: `1.04 s`
- duration sample error: `0`
- requested factor / plateau: `1.2 / 1.2222222222222223`
- tier area / error: `1.04 / 0.0 s`
- DurationTier/PitchTier points: `6 / 79`
- output peak: `0.13579523998843224`
- output digest:
  `9b3f28ab549da65dc18a452409cd67d68a816a34d8f3996c65021b8c2fafa798`

이는 shell이 새 process를 다섯 번 시작해 확인한 smoke이며 아직
content-addressed production worker receipt는 아니다. 단위 계약은
`92 passed`, Ruff와 mypy도 통과했다.

인접 E01 전체 묶음에서는 이 신규 코드와 무관하게 현재
`EXPERIMENTAL_MANUSCRIPT_KO.md`의 동시 수정 후 E01 config source pin이
stale하여 기존 contract 테스트 4개가 static 단계에서 실패했다. 이
실패를 duration primitive 통과로 대체하지 않는다.

## 해석 한계와 다음 gate

현재 확인한 것은 caller alignment를 받아 정확한 in-segment
DurationTier area를 만들고, pinned seed의 격리 process에서 유·무성 혼합
합성 output을 반복하는 기술 primitive다. 다음을 증명하지 않는다.

- 음소·음절 경계의 사람 정확성
- 실제 발화에서 duration만 음향적으로 변했다는 주장
- local DurationTier support와 달리 PSOLA waveform 변화가 target 구간에만
  한정된다는 주장
- output F0·내용·화자·energy·spectral·자연스러움 보존
- splice/click 부재와 변환된 TextGrid 전체 remap
- pronunciation/fluency/duration score의 기대 관계
- S1·S2 독립 일치, E01 pass rate 또는 `>=0.95`
- 실제 한국어 유·무성/휴지 slice와 cross-platform bit exact
- `k=1` identity-PSOLA matched control에 의한 reconstruction collateral 분리

다음 gate는 content-addressed single-job worker와 receipt를 구현하고,
non-locked 실제 발화에서 승인된 정렬 구간을 고정한 뒤 원본/변형본의
duration, F0/voicing, phone/content, energy, spectral, seam,
naturalness와 speaker similarity를 함께 측정하는 것이다.
