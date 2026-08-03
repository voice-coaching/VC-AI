# Phase 1 m4 torchcrepe 격리 환경 게이트

기준 시각: 2026-07-29 KST

판정: **Python 3.12 ARM64 CPU 설치·합성 backend smoke 통과 / 실제 m4 실험 미완료**

## 1. 범위와 격리

이 게이트는 PDF Phase 1 m4에서 요구하는 torchcrepe 경로가 현재 머신에서
실제로 설치되고 CPU로 실행되는지만 확인한다. 다음 환경과 작업은 변경하지
않았다.

- 실행 중인 S104와 그 protected source/config
- `.venv-scorer`
- `.venv-paper-acoustics`
- S106~S108 source-pinned 파일
- 프로젝트 feature adapter와 논문 실험 기본값

설치는 새 `.venv-paper-torchcrepe` 안에서만 수행했다. 모든 추론 명령에
`CUDA_VISIBLE_DEVICES=''`를 적용하고 PyTorch thread를 1개로 제한했다.
검증 종료 뒤 이 venv를 사용하는 잔여 process가 없었고, S104 PID 386918은
계속 실행 중이었다.

## 2. 공식 배포 메타데이터와 공급망 경계

[공식 PyPI 프로젝트](https://pypi.org/project/torchcrepe/)와
[공식 저장소](https://github.com/maxrmorrison/torchcrepe)를 확인했다.

- 최신 PyPI release: `torchcrepe==0.0.24` (2025-05-16)
- wheel tag: `py3-none-any`
- wheel SHA-256:
  `ec054c23c9d45328f213f93a0131570a3f0e5903e9382792bed95f17a8c36d5a`
- license metadata와 저장소 LICENSE: MIT
- 공식 `setup.py` dependencies:
  `librosa>=0.9.1`, `resampy`, `scipy`, `torch`, `torchaudio`, `tqdm`
- `Requires-Python`: 선언 없음
- 2026-07-29 조회 기준 공식 저장소 `master`:
  `19e2ec3d494c0797a5ff2a11408ec5838fba6681`

따라서 universal wheel 표기만으로 Python 3.12/aarch64 호환성을 주장하지
않고 아래 실제 설치로 확인했다. 또한 PyPI 파일 metadata에서 Trusted
Publishing을 사용하지 않은 업로드로 표시되고 2026-07-29 조회에서 공식
저장소 tag 목록이 비어 있었으므로, PyPI wheel을 특정 Git tag와
암호학적으로 연결했다고 주장하지 않는다.

이번 smoke에 사용한 wheel 내 `tiny.pth`는 1,962,363 bytes이고 SHA-256은
`d4993eea36ed1a0ad9ac549c740dae5265b049ce72004f00c2f59e01c0be8432`다.
설치된 `full.pth`는 실행하지 않았다.

## 3. 설치 결과

환경은 Ubuntu 24.04, Linux aarch64, CPython 3.12.3이다. 공식 PyTorch
CPU index에서 서로 맞는 ARM64 CPython 3.12 wheel을 먼저 설치하고 PyPI의
torchcrepe를 설치했다.

```bash
python3.12 -m venv .venv-paper-torchcrepe
CUDA_VISIBLE_DEVICES='' \
  .venv-paper-torchcrepe/bin/python -m pip install \
  --index-url https://download.pytorch.org/whl/cpu \
  'torch==2.11.0+cpu' 'torchaudio==2.11.0+cpu'
CUDA_VISIBLE_DEVICES='' \
  .venv-paper-torchcrepe/bin/python -m pip install \
  --index-url https://pypi.org/simple 'torchcrepe==0.0.24'
CUDA_VISIBLE_DEVICES='' \
  .venv-paper-torchcrepe/bin/python -m pip check
```

결과는 `No broken requirements found`였다. runtime은 다음과 같이 확인됐다.

```json
{
  "python": "3.12.3",
  "machine": "aarch64",
  "torchcrepe": "0.0.24",
  "torch": "2.11.0+cpu",
  "torchaudio": "2.11.0+cpu",
  "cuda_available": false
}
```

성공 환경의 `pip freeze --all`은
`requirements/paper-torchcrepe-linux-aarch64.lock.txt`에 고정했다.
이 lock은 exact version snapshot이지만 모든 transitive wheel에 hash를
강제하는 `--require-hashes` lock은 아니다.

## 4. 180 Hz 합성 F0 smoke

논문 기본값으로 오인되지 않도록 입력·추출 파라미터를 모두 명시했다.

| 항목 | smoke 설정 |
|---|---:|
| 입력 | `float32`, mono `[1, 16000]` |
| 파형 | 아래 Torch `float32` 두 식으로 생성 |
| sample rate / 길이 | 16,000 Hz / 1.0 s |
| hop | 160 samples / 10 ms |
| F0 범위 | 75.0~300.0 Hz |
| model | `tiny` |
| decoder | `torchcrepe.decode.viterbi` |
| batch | 32 frames |
| device / pad | `cpu` / `True` |
| 반환 | periodicity 포함, deprecated harmonicity 미사용 |
| NumPy·PyTorch RNG seed | `20260729` |
| PyTorch thread | 1 |
| MKLDNN / deterministic algorithms | enabled / disabled |

정본 smoke 파형은 다음 **두 식과 연산 순서**로 생성했다.

```python
t = torch.arange(16000, dtype=torch.float32) / 16000
audio = (0.2 * torch.sin(2 * torch.pi * 180 * t)).unsqueeze(0)
```

이 파형의 little-endian float32 raw-byte SHA-256은
`7531ab1d54cd1a62972dded76dbd8e8d2cbf2654769dd91ea1c06469610617f1`다.

torchcrepe의 bin-to-frequency 변환은 dither를 위해 NumPy/Scipy RNG를
사용한다. seed를 고정하지 않은 같은-process 두 번의 호출에서 pitch의
최대 절대 차이가 약 2.7751 Hz였으므로 RNG seed도 재현 파라미터로
고정했다. 각 호출 직전에 같은 seed를 다시 설정한 두 번의 smoke는
pitch와 periodicity가 byte-for-byte 같았다.

독립 재현 중 다음 NumPy 식을 사용하면 같은 180 Hz/16 kHz/1초 표기라도
float64 연산 뒤 float32로 변환되므로 다른 파형이 된다는 점을 확인했다.

```python
audio = (
    0.2
    * np.sin(2 * np.pi * 180 * np.arange(16000, dtype=np.float64) / 16000)
).astype(np.float32)
```

대체 파형 SHA-256은
`20601c399ff42b9676c0697dca117e6929097e028584890896c086c76ed7f606`다.
정본과 16,000 sample 중 15,796개가 다르고 최대 절대 차이는 약
`2.4164e-05`였다. 이 입력에서는 decoded pitch SHA는 정본과 같았지만
periodicity SHA가
`f08b3dae947f41103a0fbf4da6511b9b3a9986885dc8f345dab25b89c56a43f7`,
중앙값이 `0.7372920513153076`이었다. 정본 입력의 periodicity SHA는
아래의 `f06a...`, 중앙값은 `0.7372934818267822`로 별도 process 세 번
모두 재현됐다. 관측된 차이는 backend 비결정성이 아니라 합성 파형의
dtype·연산 순서를 처음에 충분히 고정하지 않은 데서 발생했다.
별도 확인에서 MKLDNN을 끄면 또 다른 periodicity byte digest가 나왔으므로
정본처럼 backend 설정도 함께 기록해야 하지만, 이는 위 `f06a`와 `f08b`
차이의 원인은 아니었다.

```json
{
  "pitch_shape": [1, 101],
  "periodicity_shape": [1, 101],
  "pitch_finite": true,
  "periodicity_finite": true,
  "pitch_median_hz": 181.39877319335938,
  "pitch_min_hz": 179.5536651611328,
  "pitch_max_hz": 183.1963348388672,
  "pitch_mae_from_180_hz": 1.4193154573440552,
  "periodicity_median": 0.7372934818267822,
  "pitch_sha256_le_float32": "80c229c1db9d9c5e4f2e8373ba995b276db6b1f722ec74244fb36675edc93eeb",
  "periodicity_sha256_le_float32": "f06a85297503a5dbb22b72f1540940295f2236380f0f031dee8a3af3f7a50a2d",
  "seed_reset_repeat_pitch_exact": true,
  "seed_reset_repeat_periodicity_exact": true
}
```

이는 합성 sine의 작은 backend 실행성 검증이다. 실제 한국어 발화 F0
정확도, 화자군 calibration, Parselmouth agreement, 경계톤 검출 또는
downstream 개선 근거가 아니다. 같은 seed와 package라도 다른 CPU/library
환경에서 byte-level hash가 같다고 주장하지 않는다.

## 5. 무음과 오류 경계

공식 README는 CREPE가 무음으로 학습되지 않았으므로 별도 silence
threshold를 적용하도록 설명한다. 1초 zero PCM에 같은 추출 설정과
seed `20260730`을 적용했을 때 raw backend는 finite F0를 반환했고 중앙값은
약 205.9955 Hz였다. raw periodicity 중앙값은 약 `4.7796e-05`였다.

smoke 전용으로 다음 후처리를 명시 적용했다.

- `torchcrepe.threshold.Silence(-60.0)`: periodicity 101/101을 0으로 설정
- `torchcrepe.threshold.At(0.21)`: pitch 101/101을 unvoiced `NaN`으로 설정

`-60.0 dBFS`와 `0.21`은 공식 문서 예시를 확인하기 위한 합성 smoke
파라미터일 뿐 실제 논문 기본값이나 사전등록값이 아니다.

공식 public API의 입력 실패도 직접 확인했다.

| 입력 | 실제 결과 | 판정 |
|---|---|---|
| 지원하지 않는 model 문자열 | `ValueError` | 명시적 실패 |
| rank-1 audio | `IndexError` | 실패하지만 안정된 연구 계약 오류는 아님 |
| 첫 sample이 `NaN`인 `[1, 16000]` audio | 예외 없음; pitch 101개는 finite, periodicity 32/101이 `-Inf` | fail-open |

따라서 raw torchcrepe를 사용자 음성 경로에 직접 연결하면 안 된다. 같은
날 구현한 strict adapter는 mono/shape/dtype/sample-rate/non-empty/finite
입력, 모든 과학 파라미터, RNG seed, 출력 shape·finite, 무성 정책을
검증하고 backend 예외를 개인정보 없는 고정 오류로 매핑한다.

## 6. 현재 판정과 다음 gate

이 문서의 환경 gate가 완료한 범위는 다음뿐이다.

- Python 3.12 Linux aarch64에서 exact-version CPU 환경 설치
- `pip check`
- bundled tiny model의 180 Hz 합성 실행
- dither RNG, 무음 후처리, 비정상 입력의 backend 경계 관찰
- exact environment freeze

같은 날 strict CPU-only in-memory adapter와 fake-backend 단위 테스트,
실제 180 Hz/무음 smoke까지 후속 완료했다. 계약과 결과는
`docs/PHASE1_TORCHCREPE_PITCH_PRIMITIVE_KO.md`에 분리 기록했다.

남은 gate는 다음과 같다.

1. non-locked 합성 suite와 허가된 실제 한국어 발화에서 Parselmouth와
   동일한 입력/time-grid를 사용한 contour
   agreement/calibration
2. 화자별 normalization, 문장 말미 경계톤, slice 및 ablation 사전등록
3. 실제 corpus split/privacy/license lineage와 cache digest
4. 모델 선택과 논문 표에 반영하기 전 독립 평가

따라서 현재 판정은 **torchcrepe 환경·실제 backend smoke 완료**이며
**Phase 1 m4 실험 완료**가 아니다.
