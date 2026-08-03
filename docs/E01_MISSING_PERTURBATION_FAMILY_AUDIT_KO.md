# E01 잔여 metamorphic 변형 family 감사와 F0 primitive

상태: **F0 제어 primitive 및 합성 backend smoke 완료 / duration·phone 변형과 실제 E01 실행 미완료**

기준 시각: 2026-07-29 20:21 KST

## 1. 감사 범위와 결론

PDF 표 4의 다섯 변형은 silence, gain, 특정 음절 duration 확대, 전역 F0
scaling, 음소 교체 합성이다. 이 감사 전 저장소에는 silence/gain의 pure
waveform primitive와 수치 relation evaluator가 있었지만, 실제
duration/F0/phone waveform 변형은 없었다.

이번 감사에서는 단순 resampling을 duration-only 또는 pitch-only
변형이라고 부르지 않았다. 현재 exact-pinned 연구 환경에서 공식
알고리즘과 실행 경계를 확인할 수 있는 F0 family 하나만 다음 파일로
구현했다.

- `src/voice_coach/evaluation/e01_f0_transform.py`
- `tests/unit/test_e01_f0_transform.py`

duration과 phone family는 필요한 alignment·합성·독립 검증 artifact가
없으므로 구현하지 않았다. 따라서 현재 상태는 다섯 family 중
silence/gain/F0에 대한 **wiring primitive**가 존재한다는 뜻일 뿐,
E01 suite 완성이나 metamorphic pass를 뜻하지 않는다.

이 작업은 실제 audio, corpus row, locked test, S203 expert holdout, 모델,
GPU, network, E01 output artifact를 읽거나 쓰지 않았다. magnitude,
relation tolerance, pass-rate 계산도 정하지 않았다.

## 2. F0 family를 구현할 수 있었던 근거

연구 전용 `.venv-paper-acoustics`에는
`praat-parselmouth==0.4.7`이 exact pin되어 있고 `pip check`가
통과한다. lock은
`requirements/paper-acoustic-tools-linux-aarch64.lock.txt`이며,
로컬 빌드 wheel SHA-256도 그 파일에 기록돼 있다.

Parselmouth 0.4.7 공식 예제는 다음 순서로 pitch를 바꾼다.

1. Sound를 Praat `Manipulation`으로 변환
2. `PitchTier` 추출
3. 지정 시간 범위의 frequency에 factor 곱하기
4. 수정된 `PitchTier` 교체
5. overlap-add로 재합성

이는 단순 sample-rate 변환이 아니다. Praat 공식 설명상
`Manipulation`은 원본 Sound, pulse, PitchTier, DurationTier를 포함하며,
overlap-add는 이 구조로 재합성한다.

- [Parselmouth 0.4.7 pitch manipulation 예제](https://parselmouth.readthedocs.io/_/downloads/en/stable/pdf/)
- [Praat Manipulation 구조와 overlap-add](https://praat.org/manual/Manipulation.html)
- [Praat PitchTier 의미](https://praat.org/manual/PitchTier.html)
- [Praat Manipulation: Replace pitch tier](https://praat.org/manual/Manipulation__Replace_pitch_tier.html)

## 3. 구현 계약

`scale_global_f0`에는 실험 기본값이 없다. 호출자가 다음을 모두
명시한다.

- 정확히 16 kHz인 mono `float32`/`float64` base NumPy PCM
- Praat analysis time step과 source pitch floor/ceiling
- `1.0`이 아닌 양의 전역 F0 scale factor
- scale 후 허용할 pitch floor/ceiling
- 최소 PitchTier point 수
- normalized PCM peak limit과 최대 output sample 수

함수는 실제 호출 시에만 Parselmouth를 import하고 distribution과
module version이 모두 `0.4.7`인지 확인한다. 파일·network·GPU·RNG는
사용하지 않는다. 테스트용 injected backend도 `str` subclass나 묵시적
문자열 변환을 받지 않고, exact base `str` 타입의 `0.4.7`만 허용한다.

다음 조건은 fail closed한다.

- input이 base NumPy mono finite float PCM이 아니거나 16 kHz가 아닌 경우
- input peak 또는 sample budget 위반
- 과학 파라미터가 boolean, 비실수, NaN/Inf, 역전된 범위인 경우
- source PitchTier가 비거나 최소 point 수보다 작은 경우
- source PitchTier time이 input duration 밖인 경우
- source PitchTier F0가 analysis 범위 밖인 경우
- multiply 전후 PitchTier time grid가 정확히 같지 않은 경우
- 모든 PitchTier value가 명시 factor로 곱해지지 않은 경우
- scaled PitchTier F0가 caller 허용 범위 밖인 경우
- 조작 전후 DurationTier point 수가 `0`이 아닌 경우
- output sample rate 또는 sample 수가 input과 달라진 경우
- output이 source dtype에서 finite하지 않거나 peak limit를 넘는 경우

peak 초과 시 자동 normalization이나 clipping을 하지 않는다. 그런
후처리는 gain이라는 두 번째 제어 변수를 추가하므로 고정 오류로
실패한다.

반환 metadata에는 algorithm/version과 caller parameter, sample 수,
PitchTier point 수·범위·scale error, input/output peak와 output
digest만 포함한다. PCM, path, transcript, speaker/participant/item/audio
ID는 보존하지 않는다.

## 4. “한 factor”의 정확한 의미와 비주장

이 구현에서 직접 조작하는 Praat control은 **PitchTier frequency의 전역
multiplier 하나**다.

- PitchTier time grid는 exact equality로 검사한다.
- DurationTier는 조작 전후 모두 empty여야 한다.
- sample rate와 sample count가 같으므로 discrete waveform duration은
  정확히 같다.
- gain normalization, clipping, resampling, randomization은 없다.

그러나 PSOLA는 waveform 재합성이다. 따라서 spectral envelope, energy,
perceived naturalness, unvoiced 경계 또는 artifact가 완전히
불변이라고 주장하지 않는다. “F0 외 모든 음향 속성이 동일하다”는
주장이 아니라, **명목상 조작 factor와 duration 불변 조건을 코드에서
제한한 primitive**다. 실제 E01에서는 독립 F0 extractor, content
scorer, energy/naturalness 진단으로 collateral change를 별도 측정해야
한다.

또한 PDF의 기대 관계인 “정규화 contour 유사 유지”에 대한
normalization, common time grid, unvoiced policy, tolerance는 아직
사전등록되지 않았다. 이 primitive는 그 relation을 계산하거나
통과시키지 않는다.

## 5. 합성 actual-backend smoke

실제 Parselmouth 0.4.7 backend에 16 kHz, 1초, amplitude `0.05`,
180 Hz sine를 넣고 factor `1.2`를 한 번 적용했다. `time_step=0.01`,
analysis 범위 `75–300 Hz`, scaled 허용 범위 `80–300 Hz`, 최소 tier
point `90`은 이 합성 smoke 호출값이며 논문 기본값이나
사전등록값이 아니다.

정확한 PCM 생성 순서와 extractor 호출까지 포함한 재현 명령은 다음과
같다. `sample_indices`를 먼저 `float64`로 만들고, 이를 sample rate로
먼저 나눈 `time_seconds`에 각주파수를 곱하는 연산 순서도 input
identity의 일부다.

```bash
PYTHONPATH=src CUDA_VISIBLE_DEVICES='' \
  .venv-paper-acoustics/bin/python - <<'PY'
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np

module_path = Path(
    "src/voice_coach/evaluation/e01_f0_transform.py"
).resolve()
spec = importlib.util.spec_from_file_location(
    "_e01_f0_transform_documented_smoke",
    module_path,
)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

from voice_coach.features.parselmouth_pitch import (  # noqa: E402
    extract_parselmouth_pitch_contour,
)

sample_rate_hz = 16_000
sample_indices = np.arange(sample_rate_hz, dtype=np.float64)
time_seconds = sample_indices / float(sample_rate_hz)
waveform = np.asarray(
    0.05 * np.sin((2.0 * np.pi * 180.0) * time_seconds),
    dtype=np.float64,
)

transform_arguments = {
    "sample_rate_hz": sample_rate_hz,
    "time_step_seconds": 0.01,
    "analysis_pitch_floor_hz": 75.0,
    "analysis_pitch_ceiling_hz": 300.0,
    "f0_scale_factor": 1.2,
    "allowed_scaled_pitch_floor_hz": 80.0,
    "allowed_scaled_pitch_ceiling_hz": 300.0,
    "minimum_pitch_tier_points": 90,
    "peak_limit": 1.0,
    "max_output_samples": sample_rate_hz,
}
first = module.scale_global_f0(waveform, **transform_arguments)
second = module.scale_global_f0(waveform, **transform_arguments)

extractor_arguments = {
    "time_step_seconds": 0.01,
    "pitch_floor_hz": 75.0,
    "pitch_ceiling_hz": 300.0,
    "unvoiced_policy": "keep_zero",
}
source_contour = extract_parselmouth_pitch_contour(
    waveform,
    sample_rate_hz,
    **extractor_arguments,
)
output_contour = extract_parselmouth_pitch_contour(
    first.waveform,
    sample_rate_hz,
    **extractor_arguments,
)

print(
    json.dumps(
        {
            "input_sha256": hashlib.sha256(
                waveform.tobytes(order="C")
            ).hexdigest(),
            "output_digest_sha256": (
                first.metadata.output_digest_sha256
            ),
            "input_sample_count": first.metadata.input_sample_count,
            "output_sample_count": first.metadata.output_sample_count,
            "pitch_tier_point_count": (
                first.metadata.pitch_tier_point_count
            ),
            "source_pitch_tier_min_hz": (
                first.metadata.source_pitch_tier_min_hz
            ),
            "source_pitch_tier_max_hz": (
                first.metadata.source_pitch_tier_max_hz
            ),
            "scaled_pitch_tier_min_hz": (
                first.metadata.scaled_pitch_tier_min_hz
            ),
            "scaled_pitch_tier_max_hz": (
                first.metadata.scaled_pitch_tier_max_hz
            ),
            "maximum_pitch_tier_scale_error_hz": (
                first.metadata.maximum_pitch_tier_scale_error_hz
            ),
            "source_extracted_median_hz": (
                source_contour.voiced_median_hz
            ),
            "output_extracted_median_hz": (
                output_contour.voiced_median_hz
            ),
            "extracted_median_ratio": (
                output_contour.voiced_median_hz
                / source_contour.voiced_median_hz
            ),
            "input_peak": first.metadata.input_peak,
            "output_peak": first.metadata.output_peak,
            "repeat_exact": bool(
                np.array_equal(first.waveform, second.waveform)
                and first.metadata == second.metadata
            ),
        },
        indent=2,
        sort_keys=True,
    )
)
PY
```

| 항목 | 관측값 |
|---|---:|
| input/output sample 수 | 16,000 / 16,000 |
| PitchTier point 수 | 97 |
| source PitchTier 범위 | 179.99942589664892–180.0006869321609 Hz |
| scaled PitchTier 범위 | 215.99931107597868–216.0008243185931 Hz |
| 최대 PitchTier scale 오차 | 0.0 Hz |
| 독립 호출한 동일 Parselmouth extractor source median | 180.0001702083946 Hz |
| output median | 216.04288209373354 Hz |
| output/source median ratio | 1.2002370989072433 |
| input/output peak | 0.05 / 0.04999998560526843 |
| repeat | waveform와 metadata exact |

input raw-byte SHA-256:
`689a2d283c129f8443a48b93c3e4d0aacf23ee39edd76a1a3fad0ecb29d352fd`

primitive output digest:
`3bf4c8a5c091c78950321a8ee3e94b8d0e0865a7e59a9a4ec6e3fd3ab6c3e4d8`

다음처럼 정수 `arange`에 각주파수를 먼저 곱한 뒤 sample rate로 나누는
식은 부동소수점 연산 순서가 다르다.

```python
0.05 * np.sin(
    2.0 * np.pi * 180.0 * np.arange(sample_rate_hz) / sample_rate_hz
)
```

같은 환경에서도 이 alternate PCM의 raw SHA-256은
`bea636f481e50f3fa3e034c16fc6dedc3e9699b2ac60ffad265bd40eb0e59ff7`이며,
문서화한 PCM과 5,292 samples가 bitwise 다르다. 최대 절대 차이는
약 `1.1368e-14`다. alternate 결과가 과학적으로 잘못됐다는 뜻은
아니지만, 위 표와 digest의 replay input은 아니다.

output 확인에도 같은 Parselmouth 계열 extractor를 사용했으므로 이는
독립 방법 간 agreement가 아니다. 정상상태 sine에서 실행 경로와 명시
factor가 작동한다는 smoke다.

연구 전용 acoustic environment에는 FastAPI 등 application dependency가
없어서 `voice_coach.evaluation` package 전체를 정상 import하지 못한다.
이번 actual smoke는 해당 source module을 `importlib`로 직접 load했다.
실제 E01 executor 전에는 Parselmouth pin과 application dependency를
함께 만족하는 별도 exact environment를 만들고 정상 package import를
검증해야 한다. scorer/GPU 환경에 Parselmouth를 임의 설치하지 않았다.

## 6. 검증

scorer 환경의 fake backend 단위 테스트:

```bash
.venv-scorer/bin/python -m pytest -q \
  tests/unit/test_e01_f0_transform.py
```

결과: **58 passed**.

기존 silence/gain, relation evaluator, static E01 contract까지 묶은 선택
회귀는 다음 4개 파일의 **166 tests**를 모두 통과했다.

```bash
.venv-scorer/bin/python -m pytest -q \
  tests/unit/test_e01_f0_transform.py \
  tests/unit/test_e01_waveform_transforms.py \
  tests/unit/test_e01_relation_evaluator.py \
  tests/unit/test_e01_metamorphic_contract.py
```

정적 검사:

```bash
.venv-scorer/bin/ruff check \
  src/voice_coach/evaluation/e01_f0_transform.py \
  tests/unit/test_e01_f0_transform.py
.venv-scorer/bin/ruff format --check \
  src/voice_coach/evaluation/e01_f0_transform.py \
  tests/unit/test_e01_f0_transform.py
.venv-scorer/bin/mypy \
  src/voice_coach/evaluation/e01_f0_transform.py \
  tests/unit/test_e01_f0_transform.py
```

결과: Ruff check/format과 mypy 모두 통과.

source SHA-256:
`dc0829ec56a6d954721c45199845dd304bb392fe3b895c4b4e388894921cf40b`

test SHA-256:
`186273db6e167815e280a34b43bdf813b32e66137a70c9ddbfdb41620706fc61`

## 7. duration family가 아직 막힌 이유

Praat에는 DurationTier/overlap-add 경로가 있지만, waveform primitive만
만들어서는 PDF의 **“특정 음절 duration 확대”**를 입증할 수 없다.
현재 필요한 gate는 다음과 같다.

1. expected JSON phone이 아니라 observed 발화에서 검증된
   syllable/phone boundary artifact
2. target syllable 선택 규칙과 경계 confidence
3. exact time-stretch algorithm, transition ramp, output alignment 갱신
4. source/target F0 비교를 통한 pitch-preservation 검증
5. phone/content·energy·naturalness collateral-change 감사
6. 실제 data에서 algorithm failure와 invalid-case 처리 사전등록

Praat 공식 duration 설명도 DurationTier interpolation이 exact segment
duration을 근사하며, TextGrid 기반 사용 시 pitch measurement 품질을
확인해야 한다고 명시한다.

- [Praat duration manipulation](https://praat.org/manual/Intro_8_2__Manipulation_of_duration.html)
- [Praat TextGrid to DurationTier](https://praat.org/manual/TextGrid__To_DurationTier___.html)

현재 ARM64 MFA gate와 expected-vs-observed label 경계는
`docs/paper/MFA_KOREAN_ARTIFACT_ENV_GATE_AUDIT_20260729_KO.md`에
정리돼 있다. 단순 구간 반복, sample 삭제, 전체 resampling은
pitch/content/pause를 함께 바꿀 수 있으므로 duration-only 구현으로
채택하지 않았다.

## 8. phone-substitution family가 아직 막힌 이유

equal-length waveform splice만으로는 “음소 교체 합성”을 입증할 수 없다.
잘못된 phone을 생성했다는 보장이 없고, boundary discontinuity,
coarticulation, speaker/timbre, energy, naturalness를 함께 바꿀 수 있기
때문이다.

필요한 gate는 다음과 같다.

1. observed phone boundary와 source/target phone 정책
2. 동일 화자·문맥을 제어할 replacement synthesis 또는 검증된 donor
   artifact
3. replacement count와 duration/content preservation 규칙
4. crossfade/coarticulation·경계 artifact 처리
5. S1과 다른 backbone/정렬의 독립 phone verifier
6. 해당 phone score만 악화됐는지 확인할 targeted relation과
   collateral metric
7. donor/생성 음성의 라이선스·동의·privacy lineage

현재 저장소의 stdlib/NumPy/Praat extraction 경계만으로는 이 semantic
계약을 충족하지 못하므로 placeholder synth나 splice primitive를
추가하지 않았다.

## 9. E01 완료 전 남은 실행 gate

F0 primitive가 추가돼도 다음은 그대로 미완료다.

- S108 final selected-model completion과 E01 frozen preregistration
- 다섯 family의 magnitude·expected relation·tolerance
- approved non-locked input manifest와 speaker/prompt slice
- duration·phone 변형 구현과 독립 검증
- 실제 발화에서 F0 voicing/floor/ceiling failure audit
- common time grid, normalized contour, invalid/missing-case policy
- case/perturbation weighting, confidence interval, multiplicity control
- S1/S2 actual inference와 aggregate pass-rate

따라서 이 문서는 F0 family primitive의 기술 gate를 하나 닫고,
duration/phone family가 왜 아직 열려 있는지 기록한다. E01
`>=0.95`, robustness 통과, 모델 재학습 필요 여부 또는 논문 완료를
주장하지 않는다.
