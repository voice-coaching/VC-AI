# Phase 1 m4 torchcrepe Pitch contour primitive

작성: 2026-07-29 KST

상태: **strict CPU-only primitive와 합성 smoke 완료 / 실제 m4 실험 미완료**

## 1. 구현 범위

`src/voice_coach/features/torchcrepe_pitch.py`는 raw torchcrepe API를
사용자 음성 경로에 직접 노출하지 않는 좁은 in-memory adapter다.

- production 호출 시에만 `torchcrepe`와 `torch`를 lazy import한다.
- installed distribution이 정확히 `torchcrepe==0.0.24`인지 확인한다.
- 파일 경로, ID, 전사, corpus row, split, network, cache를 다루지 않는다.
- 입력은 base `numpy.ndarray`인 mono normalized PCM만 허용한다.
- dtype은 정확히 native `float32` 또는 `float64`, 값은 finite
  `[-1.0, 1.0]`, sample rate는 정수 16 kHz여야 한다.
- list, 임의 `__array__` 객체, ndarray subclass, memmap backing,
  integer/unsigned/boolean/complex PCM은 backend 전에 거부한다.
- float64 입력은 검증 뒤 별도 float32 copy로 변환하며 변환 결과도 다시
  finite인지 확인한다.
- actual backend tensor와 `torchcrepe.predict(..., device="cpu")`를
  명시해 GPU 경로를 제공하지 않는다.

현재 adapter는 resampling, amplitude normalization, silence 제거, model
선택 또는 threshold 튜닝을 대신하지 않는다.

## 2. 호출자가 반드시 고정하는 파라미터

과학 파라미터에는 프로젝트 기본값이 없다.

| 파라미터 | 허용 계약 |
|---|---|
| `hop_length_samples` | 양의 정수 |
| `pitch_floor_hz` / `pitch_ceiling_hz` | 양의 finite, `floor < ceiling <= 2006` |
| `model` | 정확히 `tiny` 또는 `full` |
| `decoder` | 정확히 `viterbi`, `weighted_argmax`, `argmax` |
| `batch_size_frames` | 양의 정수 |
| `pad` | 정확한 boolean |
| `periodicity_threshold` | finite `[0, 1]` |
| `silence_threshold_dbfs` | finite real |
| `numpy_random_seed` | 정수 `[0, 2**32-1]` |
| `torch_random_seed` | 정수 `[0, 2**63-1]` |
| `unvoiced_policy` | 정확히 `keep_zero` 또는 `drop` |

torchcrepe의 pitch-bin 변환은 Scipy를 통해 NumPy global RNG의 dither를
사용한다. adapter는 lock 안에서 기존 NumPy RNG state를 저장하고
caller seed를 적용한 뒤 성공·실패 모두에서 복원한다. actual backend는
PyTorch CPU default generator도 같은 방식으로 보존한다. CUDA RNG에는
접근하지 않는다.

이 보존은 이 adapter를 통과하는 동시 호출끼리는 직렬화하지만, 같은
process의 외부 코드가 lock을 무시하고 NumPy global RNG를 동시에 바꾸는
상황까지 보장하지는 않는다.

## 3. periodicity, 무음, time grid 정책

actual backend는 raw pitch/periodicity를 얻은 뒤 caller가 명시한
`torchcrepe.threshold.Silence`를 적용한다. adapter는 다음 두 조건을
모두 만족한 frame만 voiced로 판정한다.

1. silence-masked periodicity가 `0`보다 큼
2. silence-masked periodicity가 caller threshold 이상

voiced frame이 하나도 없으면 통계를 만들지 않고
`torchcrepe pitch contour contains no voiced frames`로 실패한다.

| unvoiced 정책 | 반환 |
|---|---|
| `keep_zero` | 전체 time grid를 유지하고 unvoiced F0·periodicity를 `0.0`으로 정규화 |
| `drop` | unvoiced time/F0/periodicity를 함께 제거 |

`pad=True`이면 frame center를 `0, hop, ...` sample로, `pad=False`이면
1024-sample CREPE window의 첫 center인 512 sample부터 계산한다.
backend frame 수가 이 expected grid와 다르면 실패한다. unpadded 입력은
최소 1024 samples를 요구한다.

## 4. 출력과 실패 계약

`TorchcrepePitchContour`는 다음만 반환한다.

- version과 caller-explicit 파라미터·seed·정책
- time/F0/periodicity tuple
- backend/returned/voiced/unvoiced frame count
- voiced F0 median과 range
- 위 계약과 반환 contour의 SHA-256

원시 PCM, path, speaker/utterance/candidate ID, 문장·전사는 보존하지
않는다. digest도 익명화가 아니므로 실제 음성 결과에서는 제한 artifact로
취급해야 한다.

다음 backend 결과는 모두 고정된 adapter 오류로 실패한다.

- `[1, frames]`가 아닌 pitch/periodicity shape 또는 expected frame 수 불일치
- 비수치, empty, NaN/Inf
- caller F0 범위 밖 pitch
- `[0, 1]` 밖 periodicity
- voiced frame 0개

backend 예외 문자열과 경로·ID는 public 오류 및 exception cause에 남기지
않는다.

## 5. fake-backend 계약 검증

scorer 환경에는 torchcrepe를 설치하지 않고 injectable fake backend로
검증했다.

```bash
CUDA_VISIBLE_DEVICES='' .venv-scorer/bin/python -m pytest -q \
  tests/unit/test_torchcrepe_pitch.py
```

결과: **63 passed**.

검증 범위에는 입력·모든 파라미터 사전검증, 지원 model/decoder 조합,
pad별 time grid, 두 unvoiced 정책, NumPy RNG 복원, deterministic digest,
output shape/finite/bounds, no-voiced 실패, backend 오류 비노출이 포함된다.
Ruff check/format과 mypy도 통과했다.

## 6. 실제 torchcrepe 0.0.24 smoke

별도 `.venv-paper-torchcrepe`에서 같은 공개 함수를 실제 backend에
연결했다. 환경 lock과 raw backend 공급망·수치 관찰은
`docs/paper/PHASE1_TORCHCREPE_ENV_GATE_20260729_KO.md`에 기록했다.

정본 입력은 다음 Torch float32 연산으로 만든 16 kHz, 1초, 180 Hz,
amplitude 0.2 sine다.

```python
t = torch.arange(16000, dtype=torch.float32) / 16000
samples = (0.2 * torch.sin(2 * torch.pi * 180 * t)).numpy()
```

input raw-byte SHA-256:
`7531ab1d54cd1a62972dded76dbd8e8d2cbf2654769dd91ea1c06469610617f1`

smoke 호출 파라미터는 다음과 같다.

```json
{
  "hop_length_samples": 160,
  "pitch_floor_hz": 75.0,
  "pitch_ceiling_hz": 300.0,
  "model": "tiny",
  "decoder": "viterbi",
  "batch_size_frames": 32,
  "pad": true,
  "periodicity_threshold": 0.21,
  "silence_threshold_dbfs": -80.0,
  "numpy_random_seed": 20260729,
  "torch_random_seed": 20260729,
  "unvoiced_policy": "keep_zero"
}
```

```json
{
  "torchcrepe_version": "0.0.24",
  "cuda_available": false,
  "frame_count": 101,
  "returned_frame_count": 101,
  "voiced_frame_count": 101,
  "unvoiced_frame_count": 0,
  "time_first_seconds": 0.0,
  "time_last_seconds": 1.0,
  "f0_median_hz": 181.39877319335938,
  "f0_min_hz": 179.5536651611328,
  "f0_max_hz": 183.1963348388672,
  "f0_range_hz": 3.642669677734375,
  "periodicity_min": 0.26093143224716187,
  "periodicity_median": 0.7372934818267822,
  "periodicity_max": 0.780695378780365,
  "contour_sha256": "1ba5feb8aac92181e8a5429705ed9799278c534a41ca54c9c03156302e3944ae",
  "repeat_exact": true,
  "numpy_rng_state_preserved": true,
  "torch_cpu_rng_state_preserved": true
}
```

같은 설정의 zero PCM은
`torchcrepe pitch contour contains no voiced frames`로 실패했다.

pure 180 Hz sine에 `silence_threshold_dbfs=-60.0`을 적용하면
A-weighted loudness 영향으로 4/101 frame만 voiced가 됐고, `-80.0`에서는
101/101이 유지됐다. 따라서 `-80.0`은 이 합성 실행성을 보기 위한
caller-explicit smoke 값일 뿐 실제 한국어 음성의 기본값이나
사전등록값이 아니다.

## 7. 비주장과 남은 m4 작업

이 구현과 smoke는 다음을 완료하거나 대체하지 않는다.

- 실제 외국어 사용자·한국어 사용자 발화 contour
- Parselmouth와 동일 전처리/time-grid에서 agreement·calibration
- silence/periodicity/F0 범위의 speaker·성별·연령·억양 slice 사전등록
- 화자 normalization과 문장 말미 경계톤 특징
- non-locked corpus cache, split/privacy/license lineage
- m4 ablation, downstream 개선, 논문 표·통계·결론

따라서 현재 판정은 **strict torchcrepe primitive와 합성 backend smoke
완료**이며 **PDF Phase 1 m4 실험 완료**가 아니다.
