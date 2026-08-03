# Phase 1 m4 Parselmouth Pitch contour primitive

작성: 2026-07-29 KST  
상태: **strict in-memory primitive와 합성 smoke 완료 / 실제 m4 실험 미완료**

## 1. 구현 범위

`src/voice_coach/features/parselmouth_pitch.py`는 PDF Phase 1 m4에서 요구하는
F0 contour 중 Parselmouth 경로만 좁게 구현한다.

- 입력은 메모리의 mono, 16 kHz, finite, non-empty real PCM뿐이다.
- `time_step_seconds`, `pitch_floor_hz`, `pitch_ceiling_hz`,
  `unvoiced_policy`는 호출자가 모두 명시해야 한다.
- 과학 파라미터에는 프로젝트 기본값이 없다.
- 실제 backend는 호출 시에만 lazy import하며
  `praat-parselmouth==0.4.7` distribution과 module version을 함께
  확인한다.
- 파일 경로, corpus row, split, GPU, network, cache를 읽거나 쓰지 않는다.
- scorer 환경 테스트는 Parselmouth 설치 없이 fake backend로 실행된다.

외부 호출자가 넘긴 floor/ceiling/time-step이 타당한 연구 설정이라는
판단은 이 함수의 책임이 아니다. 양의 finite 값과
`pitch_ceiling_hz > pitch_floor_hz`만 검사하며, 실제 데이터 실행 전에
화자군·성별·연령·억양 slice와 함께 별도 사전등록해야 한다.

## 2. 무성 프레임 정책

정확히 두 정책만 허용하며 기본값은 없다.

| 정책 | 반환 contour |
|---|---|
| `keep_zero` | Parselmouth의 무성 frame을 `0.0 Hz`로 유지하고 전체 time grid 반환 |
| `drop` | 무성 frame과 대응 time point를 함께 제거하고 유성 frame만 반환 |

두 정책 모두 유성 frame이 0개이면 요약 통계를 임의로 만들지 않고
fail closed한다. NaN으로 무성을 표현하는 정책과 interpolation은 이
primitive에 없다.

## 3. 반환 계약과 개인정보 경계

`ParselmouthPitchContour`에는 다음 값만 들어간다.

- 고정 backend version과 caller-explicit 추출 파라미터
- 적용된 무성 정책
- 선택 정책이 반영된 `time_seconds`, `f0_hz` tuple
- backend/returned/voiced/unvoiced frame count
- 유성 F0 median과 range(`max - min`)
- 파라미터, 정책, time grid, contour를 묶은 SHA-256

원시 PCM, audio path, speaker/utterance/candidate ID, 문장·전사, corpus
source는 반환하지 않는다. Digest도 익명화가 아니며 같은 설정과 contour의
연결 가능성이 있으므로 실제 데이터 결과에서는 제한 artifact로 취급해야
한다.

## 4. 실패 계약

다음 입력과 backend 결과는
`ParselmouthPitchExtractionError`로 실패한다.

- mono가 아닌 배열, 빈 PCM, 비실수·boolean PCM, NaN/Inf PCM
- 정수 16 kHz가 아닌 sample rate
- boolean·비실수·0 이하·NaN/Inf 추출 파라미터
- ceiling이 floor보다 크지 않거나 지원하지 않는 무성 정책
- backend와 version 중 하나만 주입하거나 version이 0.4.7이 아닌 경우
- backend contour가 빈 경우, 1차원이 아닌 경우, time/F0 shape 불일치
- time/F0 NaN·Inf, 음수·비증가 time grid, 음수 F0
- 유성 F0가 caller floor/ceiling 밖에 있거나 유성 frame이 없는 경우
- Parselmouth Sound/Pitch 호출 실패

backend 오류는 고정된 adapter 오류로 매핑하며 backend 상세 문자열을
공개 결과에 넣지 않는다.

## 5. 검증 근거

`.venv-scorer`의 fake backend 단위 테스트는 16 kHz 합성 180 Hz 입력,
두 무성 정책, count/median/range/digest와 위 실패 계약을 검증했다.

```bash
CUDA_VISIBLE_DEVICES='' .venv-scorer/bin/python -m pytest -q \
  tests/unit/test_parselmouth_pitch.py
```

결과: **35 passed**. Ruff check, Ruff format check, mypy도 통과했다.

별도 `.venv-paper-acoustics`에서 같은 공개 함수를 실제
Parselmouth 0.4.7에 연결했다. 아래 값은 16 kHz, 1초, 180 Hz sine에
`time_step=0.01`, `floor=75.0`, `ceiling=300.0`,
`unvoiced_policy=keep_zero`를 명시한 환경 smoke다. 이 파라미터는
합성 검증용이며 실제 논문 실험의 기본값이나 사전등록값이 아니다.

```json
{
  "parselmouth_version": "0.4.7",
  "sample_rate_hz": 16000,
  "backend_frame_count": 97,
  "returned_frame_count": 97,
  "voiced_frame_count": 97,
  "unvoiced_frame_count": 0,
  "voiced_median_hz": 180.0001702083946,
  "voiced_range_hz": 0.0012610355119875294,
  "contour_sha256": "da3b903af0bfda557affca6a6a0d569bca64c2ff3783eab160f508d60460af49"
}
```

같은 actual backend에 1초 무음을 넣었을 때
`Parselmouth pitch contour contains no voiced frames`로 실패하는 것도
확인했다.

## 6. 비주장과 남은 m4 작업

이 primitive와 합성 smoke는 다음 작업을 완료하거나 대체하지 않는다.

- torchcrepe 설치·고정·동일 전처리 비교
- torchcrepe와 Parselmouth contour agreement 또는 calibration
- 실제 외국어 사용자/한국어 사용자 발화의 F0 contour
- 음절·어절 정렬, 문장 말미 경계톤 특징 또는 오류 taxonomy 연결
- speaker normalization, reference-relative deviation
- non-locked corpus cache, split/privacy/license lineage
- m4 ablation, downstream 성능 개선, 논문 표·통계·결론

따라서 현재 판정은 **Parselmouth 추출 primitive 완료**이며
**PDF Phase 1 m4 실험 완료**가 아니다.
