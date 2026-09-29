# 처리 흐름과 코드

[목차](README.md) · [설정·계약](contracts-and-limits.md) · [배포 근거](deployment-evidence.md)

경로의 기준은 Pod `/opt/intelligentai/app`이다. 아래 로컬 소스 링크는 탐색 편의용이며, 배포 사실의 기준은 관측한 실행 사본과 [해시 목록](deployment-evidence.md)이다. 특히 로컬 `production.py`는 배포본과 다르다.

## 1. 실제 진입 경로

1. HTTP executor가 배포 `backend_analysis/production.py:create_http_core`로 분석 core를 구성한다. 영상 release·랜드마크 모델·geometry 자산의 세 설정을 함께 읽는다. 현재 세 값 모두 설정되어 있다.
2. [service.py](../../src/voice_coach/backend_analysis/service.py)의 `BackendAnalysisWorker`가 음성 분석 후 `_visual_supplement`를 호출한다. 영상은 선택 입력이다.
3. 현재 coaching이 활성화되어 있으므로 [adapters/visual.py](../../src/voice_coach/backend_analysis/adapters/visual.py)의 `analyze_coaching_observations`를 사용한다. 같은 파일의 `analyze_with_grounded_evidence`나 옛 단일 선택 음소 경로를 현재 호출로 혼동하지 않는다.
4. [composition.py](../../src/voice_coach/pronunciation_attempts/composition.py)의 `build_local_video_attempt_facade`가 구체 adapter들을 조립한다.
5. [video_attempt_facade.py](../../src/voice_coach/pronunciation_attempts/services/video_attempt_facade.py)의 `run_http_observations`가 후보별 `_run_visual_stages`를 실행한다.
6. 결과를 aggregate 관찰 배열로 투영하여 코칭 입력에 넣는다. GPT는 원본 영상이나 랜드마크 좌표 배열을 이 현재 시도 경로로 받지 않는다.

현재는 `http_admitted=True`다. HTTP 서버 인증, claim 및 저장소 무결성 검사 후 들어오는 내부 호출이며, public 영상 분석 endpoint가 별도로 생기는 것은 아니다. 옛 `_GrantConsentAuthority`와 동의 receipt가 코드에 남아 있어도 현재 Backend–RunPod HTTP에 요청별 grant·서명을 요구한다는 뜻은 아니다.

## 2. 관찰할 음소 선택

[coaching_evidence.py](../../src/voice_coach/backend_analysis/coaching_evidence.py)의 `build_coaching_input`이 전체 Seungun 음소 근거를 분류한다.

- 삭제 기본 특징, 정렬 모호성, 철자 역할 불확실성, 무음 초성 ㅇ, 유효 국소 음향 시간 부재 등은 `REVIEW_ONLY`다.
- 보류 이유가 없고 검출기가 표시한 행을 `ACTIONABLE`로 취급한다. 실제 발음 오류의 확정 진단이라는 뜻은 아니다.
- 기대 음소·관측 후보·철자 역할별로 묶고, 반복 수 내림차순 및 첫 등장 순서로 정렬한다. 보정되지 않은 detector score 순위가 아니다.
- 영상 adapter는 상위 3개 후보 각각의 **첫 `evidenceId`**에서 expected index를 가져온다. 반복 그룹의 모든 출현이나 전체 문장 프레임을 분석하지 않는다.
- 후보가 없으면 빈 관찰 배열을 반환한다. facade도 최대 3개·중복 없음·index 범위·검출 표지·비삭제·시작 시각을 재검사한다.

원래 Seungun selected phone은 원본 근거로 남는다. 후보별 `replace`는 영상 handoff용 선택 index를 바꾸며 원본 추론을 다시 수행하지 않는다.

## 3. 미디어 수용과 동일 시도 확인

저장소에서 materialize한 영상의 SHA-256이 요청값과 같아야 한다. request ID, execution ID, 음성·영상 SHA, 코칭 정책으로 attempt digest를 구성한다.

`run_http_observations`는 영상을 제한 workspace에 수용한 후 ffprobe 검사, 내장 음성 추출, trusted prompt 조회를 한 번씩 수행한다. `_PrecomputedSeungun.analyze`는 기존 분석의 기대 음소열과 영상에서 추출한 PCM의 SHA-256을 대조하여 이미 계산된 Seungun 근거를 반환한다. **단순히 같은 파일명이나 비슷한 녹음이라는 이유로 결합하지 않는다.**

관련 구현:

- [adapters/visual.py](../../src/voice_coach/backend_analysis/adapters/visual.py): `_PrecomputedSeungun`, `analyze_coaching_observations`
- [runtime.py](../../src/voice_coach/pronunciation_attempts/adapters/runtime.py): `HttpSeungunVisualHandoffAdapter.issue_http`
- [ffmpeg_av_container.py](../../src/voice_coach/pronunciation_attempts/adapters/ffmpeg_av_container.py): probe, 내장 음성 추출, RGB24 decode

후보마다 handoff·시간 기준·근거 digest를 새로 결속한다. 요청 내부 `decode_cache`는 전체 영상 RGB sample만 재사용한다. 후보별 landmark/QC/sync 계산과 receipt까지 모두 캐시된다고 설명하면 안 된다.

## 4. 전체 영상 sample, QC와 동기화

전체 영상에서 결정적인 균등 sample 8프레임을 RGB24로 decode한다. MediaPipe로 얼굴 수와 입술 좌표를 얻고 다음을 계산한다.

| 단계 | 관찰 및 역할 |
|---|---|
| 랜드마크 | 단일 얼굴의 입술 4좌표와 선택적인 40점 후보. 얼굴 수·시각·모델 digest 결속 |
| 영상 QC | 단일/복수 얼굴 비율, 연속 추적 구간, 입술 중심 이동, 밝기·노출 비율, 입술 ROI 이웃 밝기 차이 |
| A/V sync | 보이는 입 벌림 값과 16 kHz PCM 에너지의 상관으로 offset 후보 계산 |
| 정책 판정 | 활성 release의 QC·sync decision policy를 적용하고 receipt의 원본·정책 식별자를 대조 |

[video_qc.py](../../src/voice_coach/pronunciation_attempts/adapters/video_qc.py)의 측정과 배포 decision policy의 통과 기준은 서로 다르다. [lip_audio_av_sync.py](../../src/voice_coach/pronunciation_attempts/adapters/lip_audio_av_sync.py)는 최소 6개 유효 landmark 프레임, 40 ms 에너지 창, -300~+300 ms의 20 ms 간격 탐색, 최대 4개 anchor를 사용한다. 상관이 가장 높은 offset을 고르고 동률이면 작은 절대 offset을 선호한다. 평평한 입술 움직임이나 충분한 음성 중첩 구간 부재는 관찰 불가다.

현재 sync 판정의 `minimum_correlation=-1.0`은 강한 양의 상관을 요구하지 않는다. 정책상 `synchronized`를 높은 동기화 정확도의 실험적 증거로 해석하지 않는다.

## 5. 음소별 dense window

[runtime.py](../../src/voice_coach/pronunciation_attempts/adapters/runtime.py)의 `plan_phone_window`는 QC `passed`, sync `synchronized`와 유효 offset receipt를 요구한다. 원시 offset 후보만으로 구간을 만들지 않는다.

영상 로컬 시각은 다음 식으로 계산한다. 시작·종료에 동일하게 적용한다.

```text
video_ms = audio_origin_ms + Seungun_phone_ms + sync_offset_ms - video_origin_ms
```

양끝에 각각 100 ms padding을 주되 영상 범위로 제한한다. 창 길이 상한은 1,000 ms, 프레임 수는 2~12개다. 실제 PTS가 창에 들어가는 프레임을 고르고, 12개보다 많으면 균등하게 줄인다. FPS로 시각을 추정해서 새 프레임을 만드는 방식이 아니다.

decode receipt는 마이크로초 PTS와 RGB digest를 보존한다. 공개 `decodedSampleTimesMs`는 이 PTS를 1,000으로 나눈 값이다. CTC nonblank 구간은 실제 발음 전체 지속시간을 보장하지 않으며, 짧은 구간에는 유효 프레임이 없을 수 있다.

## 6. 두 가지 입술 형상 경로

### 기본 4점 관찰

[mediapipe_lip_landmarks.py](../../src/voice_coach/pronunciation_attempts/adapters/mediapipe_lip_landmarks.py)의 `MediaPipeLipGeometry.aperture_ratio`는 정규화된 좌표로 아래 값을 계산한다.

```text
lip_aperture_ratio = (lower_lip_y - upper_lip_y) / (right_corner_x - left_corner_x)
```

[production_visual_sidecar.py](../../src/voice_coach/pronunciation_attempts/adapters/production_visual_sidecar.py)의 `_center_cue`는 dense window 중 매핑된 음소 구간 중심에 가장 가까운 프레임 하나를 선택한다. 동률이면 이른 프레임이다. 이는 창 전체의 평균이 아니다. 중심에 가까운 프레임이라도 padding 구간에 위치할 수 있으므로, 출력 구간과 실제 프레임 시각을 동일시하지 않는다.

### Closed-beta 40점 → 24점 형상

[closed_beta_lip_trajectory.py](../../src/voice_coach/pronunciation_attempts/adapters/closed_beta_lip_trajectory.py)는 기본 landmark worker와 별도의 40점 worker를 실행한다. 얼굴 수와 공유 입술 좌표 `(61, 291, 13, 14)`가 허용 오차 `1e-6` 내에서 일치해야 추가 좌표를 사용한다. 추가 worker 실패 시 기본 4점 결과로 돌아간다.

활성 AI Hub 68 geometry 자산은 40개 후보에서 아래 24개를 외곽 12점·내곽 12점 순으로 매핑한다.

```text
outer: 61, 39, 37, 0, 267, 269, 291, 321, 314, 17, 84, 91
inner: 78, 80, 82, 13, 312, 310, 308, 318, 317, 14, 87, 88
```

[p6_lip_trajectory.py](../../src/voice_coach/visual_feedback/services/p6_lip_trajectory.py)는 양 입꼬리 중심을 원점으로 옮기고 입 너비로 정규화한다. 다음 값은 정규화된 2D 좌표에서 나온다. 실제 mm, 3D 돌출량이나 카메라 자세 보정값이 아니다.

| cue | 계산 의미 |
|---|---|
| `mouth_width_fraction` | 원래 정규화 좌표에서 양 입꼬리 간 거리 |
| `outer_aperture_ratio` | 외곽 위·아래 점 거리 / 입 너비 |
| `inner_aperture_ratio` | 내곽 위·아래 점 거리 / 입 너비 |
| `outer_area_ratio`, `inner_area_ratio` | 해당 다각형 면적 / 입 너비 제곱 |
| `corner_height_asymmetry_ratio` | 양 입꼬리 y 차이의 절댓값 / 입 너비 |
| `shape_change_rate_per_second` | 인접 유효 프레임의 정규화 24점 RMS 변위 / 시간 차 |

sidecar는 **정확한 음소 구간 내부**의 유효 형상만 정적 평균에 사용하고 변화율은 해당 구간에서 최대값을 사용한다. 첫 구간 프레임의 변화율에는 직전 padding 프레임이 쓰일 수 있다. 마이크로초 PTS로 경계를 검사하여 밀리초 내림 때문에 바깥 프레임이 섞이는 것을 방지한다. 구간 내부 프레임이 없으면 이 추가 형상은 unavailable이며, 기본 중심 프레임 관찰과 가용성이 다를 수 있다.

## 7. 신뢰성 검사, 투영과 정리

sidecar는 동일 시도 binding, 모델·정책·랜드마크 digest, QC·sync·phone-window receipt를 대조한다. `evaluate`의 supported는 이 실행 계약의 충족이지 발음 정답 판정의 검증이 아니다.

sidecar 내부에는 calibration 및 허용 action 선택 코드가 있다. 그러나 현재 coaching adapter는 `cue_evidence` 중 `observed`이고 유한한 숫자이며 영상 내 유효 구간인 값만 꺼낸다. **action, calibrated deviation, correction claim은 GPT용 관찰로 투영하지 않는다.**

facade의 `finally`에서 수용 미디어를 삭제하고 바깥 `TemporaryDirectory`도 정리한다. 상위 서비스는 영상 예외를 영상 상태로 바꾸어 음성 결과를 보존한다. 삭제 실패 여부를 이번 조사에서 실제 장애로 재현하거나 검증하지는 않았다.
