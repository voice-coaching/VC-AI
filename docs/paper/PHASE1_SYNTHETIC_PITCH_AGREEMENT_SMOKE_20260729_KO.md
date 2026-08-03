# Phase 1 m4 synthetic pitch backend agreement smoke

기준일: 2026-07-29 KST  
판정: **동일 합성 입력 배선·반복성 확인 / 실제 한국어 agreement 미실행**

## 범위

Parselmouth 0.4.7과 torchcrepe 0.0.24의 strict in-memory adapter를 같은
합성 PCM에 연결해 두 backend가 실제로 실행되고 결정적으로 반복되는지만
확인했다. 실제 외국어 사용자·한국어 사용자 음성, corpus cache, speaker
normalization, 경계톤, threshold 선택과 model ablation에는 접근하지
않았다.

입력은 16 kHz, 1초, amplitude 0.2의 순수 sine이며 주파수는
`90, 120, 180, 250 Hz`다. 두 격리 환경에서 다음 연산 순서를 그대로
사용하고 little-endian float32 byte SHA가 backend 사이에 같은지
확인했다.

```python
t = np.arange(16000, dtype=np.float64) / 16000
x = (0.2 * np.sin(2 * np.pi * hz * t)).astype(np.float32)
```

공통 F0 범위는 75–300 Hz, hop/time-step은 10 ms다. Parselmouth는
`keep_zero`를 사용했다. torchcrepe는 `tiny`, Viterbi, batch 32,
`pad=true`, periodicity threshold 0.21, silence threshold -80 dBFS,
NumPy/Torch seed 20260729와 `keep_zero`를 명시했다. 이 값들은 합성
실행성 확인용이며 논문 기본값이나 사전등록값이 아니다.

## 결과

| target Hz | input SHA-256 앞 12자 | torchcrepe median Hz | voiced/frames | Parselmouth median Hz | voiced/frames | median 차이 Hz |
|---:|---|---:|---:|---:|---:|---:|
| 90 | `b8f84fc89576` | 89.661362 | 99/101 | 90.002531 | 97/97 | 0.341169 |
| 120 | `9e101ca8e9dc` | 119.707214 | 100/101 | 120.002150 | 97/97 | 0.294935 |
| 180 | `8c25cb39f2be` | 181.398773 | 101/101 | 180.000170 | 97/97 | 1.398603 |
| 250 | `303c9d11cd23` | 250.677307 | 101/101 | 250.001691 | 97/97 | 0.675616 |

두 backend median의 절대 차이는 네 조건에서 평균 `0.677581 Hz`, 최대
`1.398603 Hz`였다. Target 대비 median absolute error는 torchcrepe
`0.676876 Hz`, Parselmouth `0.001635 Hz`였다. 이는 순수 정상상태 sine
네 개에서의 기술 수치이며 실제 음성 정확도나 한 backend의 우월성을
증명하지 않는다.

같은 10 ms 간격을 요청해도 frame 수는 torchcrepe `101`,
Parselmouth `97`이었다. 각각 padding/window-center 정책이 다르므로
frame index를 그대로 일대일 대응시키면 안 된다. 실제 contour agreement는
공통 시간축 resampling/interpolation, 무성-frame mask와 endpoint 정책을
결과를 보기 전에 고정해야 한다.

각 backend에서 전체 sweep를 같은 process 안에서 두 번 실행해 row와
contour digest가 정확히 같음을 확인했다.

- torchcrepe canonical sweep SHA-256:
  `c8e788aa69eae823c29f5bed8835d2341f9e514b975ccf27aa88fb30a9de0151`
- Parselmouth canonical sweep SHA-256:
  `840e1e31e5470683d254b2295ffce6dc88029098d4e6d476c011fc49264c1928`

## 남은 gate

이 smoke 뒤에도 m4를 완료하려면 다음이 필요하다.

1. 동일 non-locked 실제 발화에 적용할 amplitude, segmentation,
   floor/ceiling, time-grid, interpolation과 무성 정책 사전등록
2. learner/native와 성별·연령·L1·억양 slice의 contour agreement,
   octave error와 voiced/unvoiced confusion
3. speaker normalization 및 reference-relative F0 deviation
4. 문장 말미 경계톤·prominence 특징과 독립 사람 annotation
5. proxy/Parselmouth/torchcrepe ablation과 불확실성 보고

따라서 이 결과는 **합성 backend 배선·반복성 근거**이며 **실제 m4
타당도·성능 또는 논문 완료 근거가 아니다**.
