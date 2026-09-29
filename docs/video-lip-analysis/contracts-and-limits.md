# 설정·입출력·제약

[목차](README.md) · [처리 흐름](pipeline.md) · [배포 근거](deployment-evidence.md)

## 활성 설정

2026-09-29 runtime env 파일 `/run/intelligentai/analysis-http.env`의 비밀값이 아닌 지정 필드만 읽었다. 프로세스 환경 전체를 출력하거나 복사하지 않았다.

| 변수 | 관측값 |
|---|---|
| `AI_ANALYSIS_TRANSPORT` | `runpod_http` |
| `AI_ANALYSIS_COACHING_ENABLED` | `true` |
| `AI_ANALYSIS_COACHING_PROVIDER` | `OPENAI` |
| `AI_ANALYSIS_COACHING_OPENAI_MODEL` | `gpt-6-astra` |
| `AI_ANALYSIS_SEUNGUN_DEVICE` | `cuda` — MediaPipe가 CUDA에서 실행된다는 뜻은 아님 |
| `AI_ANALYSIS_RESTRICTED_WORKSPACE_ROOT` | `/run/intelligentai/worker/work` |
| `AI_ANALYSIS_VISUAL_RELEASE_ROOT` | `/opt/intelligentai/app/artifacts/visual_feedback/releases/production_visual` |
| `AI_ANALYSIS_VISUAL_LANDMARK_MODEL_PATH` | `/opt/intelligentai/app/models/mediapipe/face_landmarker.task` |
| `AI_ANALYSIS_VISUAL_GEOMETRY_ARTIFACT_PATH` | `/opt/intelligentai/app/artifacts/visual_feedback/p6/aihub68_geometry_artifact_v2.json` |

영상 adapter의 임시 부모는 위 workspace의 `visual-attempts`다. `AI_ANALYSIS_OBJECT_STORAGE_MAXIMUM_VISUAL_BYTES`는 미설정이므로 배포 `production.py`의 기본값 128 MiB를 사용한다.

## 입력과 실행 한도

HTTP의 `visualInput`은 객체 key, SHA-256, MIME, byte size와 선택적인 미디어 metadata를 가진다. 서버가 제한 객체 저장소에서 읽는 구조이며, 임의 외부 URL이나 client의 로컬 경로를 그대로 열지 않는다. 영상 내장 음성의 PCM은 기존 Seungun 분석 PCM과 일치해야 한다. 영상은 주 음성 결과의 보조 입력이다.

아래 값은 실제 배포 composition의 한도다. 다른 HTTP schema·저장소 검사를 모두 통과한다는 충분조건이나 클라이언트 업로드 권장값이 아니다.

| 항목 | 구성값 |
|---|---|
| 파일 / container | 영상 수용 128 MiB 이하, MP4 경로의 H.264 또는 HEVC와 AAC 내장 음성 |
| 길이 | 500~30,000 ms |
| 가로 / 세로 | 160~1920 / 120~1920 |
| FPS / 전체 프레임 | 15~60 / 8~1800 |
| 전체 영상 sample | 8프레임, RGB 합계 64 MiB 이하 |
| PCM | 추출 결과 2 MiB 이하 |
| 음소 창 | 앞뒤 100 ms, 최대 1000 ms, 2~12프레임, RGB 합계 80 MiB 이하 |
| landmark | detection/presence/tracking confidence 각각 0.5 |
| subprocess | container/landmark 구성 timeout 각각 120초; 전체 요청 SLA와는 다름 |

## 실제 release 정책값

활성 release는 `production_visual_v2`, bundle은 `universal_visible_lip_runtime_v1`, revision은 `same_cohort_user_video_v1`이다. 아래 값은 원격 JSON에서 읽은 구성이지 이번 작업에서 재보정한 값이 아니다.

### QC decision: `user_video_visible_lip_qc_v1`

| 조건 | 값 |
|---|---|
| 최소 single-face coverage | 0.5 |
| 최대 multiple-face frame fraction | 0.5 |
| 최소 longest single-face run fraction | 0.4 |
| 최소 track transition coverage | 0.4 |
| 최대 normalized lip-center step | 0.35 |
| mean luma 범위 | 8.0~247.0 |
| 최대 under/overexposed pixel fraction | 각각 0.75 |
| 최소 lip ROI mean neighbor luma delta | 0.5 |

측정 policy의 underexposed bin은 luma ≤16, overexposed bin은 ≥239이며 lip ROI padding은 0.05다. 최소 single-face 측정 프레임 6개 조건도 있어 coverage threshold 하나만으로 통과 여부가 정해지지 않는다.

### Sync decision: `user_video_visible_lip_sync_v1`

최대 절대 offset 300 ms, 최소 anchor 1개, 최소 anchor strength 0.0, 최소 correlation **-1.0**이다. 측정 단계의 최소 6프레임·평탄 신호 차단은 별도로 적용된다. 이 완화된 threshold를 강한 동기화 정확도 보장으로 설명하지 않는다.

### Calibration 자산과 현재 사용의 차이

bundle의 `lip_aperture_ratio` 규칙은 40개 기대 음소에 **같은 하나의 reference**를 사용한다.

| 필드 | 값 |
|---|---|
| `reference_center` | 0.040438737635200096 |
| `reference_scale` | 0.03241351358994795 |
| `support_minimum` / `support_maximum` | 0.0 / 1.25 |
| `feedback_minimum_absolute_deviation` | 0.25 |
| P6 receipt | `calibrated`, calibration_case_count=2 |
| P8 receipt | `evaluated`, evaluation_case_count=2, violation_count=0 |

자산에 `reduce_visible_lip_opening` / `increase_visible_lip_opening` claim도 들어 있다. 하지만 자산 필드명과 상태 문자열은 독립적인 음소별·개인별 발음 정확도 검증을 뜻하지 않는다. 이번에는 그 두 사례를 재평가하지 않았으며 일반화 성능을 확인하지 않았다. 현재 코칭 경로는 이 action을 사용자 교정으로 내보내지 않는다.

AI Hub 68 geometry artifact 역시 `gate_status=passed`지만 `contains_action_truth=false`, `contains_pronunciation_truth=false`다. 좌표 매핑의 자산 상태와 발음 교정 정답을 구분한다.

## 현재 코칭 출력

다음은 **계약 설명용 가상 예시**다. 실제 사용자 영상 처리 결과나 운영 로그 캡처가 아니다. 수치와 index는 예시이며 다른 JSON 전체의 유효성을 나타내지 않는다.

```json
{
  "visualSupplement": null,
  "coaching": {
    "visual": {
      "status": "OBSERVED_NOT_VALIDATED",
      "referenceStatus": "NOT_VALIDATED",
      "correctiveClaimsAllowed": false,
      "observations": [
        {
          "expectedIndex": 5,
          "stage": "completed",
          "reasonCodes": [],
          "measurements": [
            {
              "cueId": "lip_aperture_ratio",
              "value": 0.08,
              "unit": "ratio",
              "startMs": 1000,
              "endMs": 1020,
              "sampleCount": null,
              "samplingProvenance": "AGGREGATE_CONTRACT_NO_SAMPLE_TIMESTAMPS"
            }
          ],
          "decodedSampleTimesMs": [933.333, 966.667, 1000.0, 1033.333, 1066.667, 1100.0],
          "samplingScope": "DECODED_WINDOW_NOT_VALID_LANDMARK_COUNT"
        }
      ]
    }
  }
}
```

- `expectedIndex`: 해당 Seungun 기대 음소의 0-based index다.
- `startMs/endMs`: 매핑된 영상 로컬 음소 구간이다. padding 창 또는 유효 landmark 표본 전체 범위가 아니다.
- `decodedSampleTimesMs`: dense window에서 decode한 시각이다. 얼굴 검출 성공 시각 목록이나 분석값별 사용 시각 목록이 아니다.
- `sampleCount=null`: aggregate 계약에 유효 landmark 표본 수가 없기 때문이다. decode 수로 채워 넣으면 안 된다.
- 기본 `lip_aperture_ratio`는 중심에 가장 가까운 1프레임 값, closed-beta 정적 값은 구간 내부 평균이다. 동일한 집계 방법으로 보지 않는다.
- 현재 시도의 이 aggregate 구조가 GPT 입력 및 최종 `coaching.visual`로 이어진다. raw 영상·이미지·좌표 배열을 GPT에 보내는 경로가 아니다. 별도 전문가 참고 사례나 debug 경로 전체에 대한 개인정보 감사까지 수행한 것은 아니다.
- GPT 입력 예산으로 코칭 후보를 더 줄일 수 있다. `visual.observations`와 최종 `items`의 개수가 항상 같다고 가정하지 않는다.

## 실패와 관찰 상태

| 상태/사유 | 해석 |
|---|---|
| `NOT_PROVIDED` | 영상 입력 없음 |
| `NOT_CONFIGURED` | 영상 adapter 미구성 |
| `OBSERVED_NOT_VALIDATED` | 하나 이상의 유효 숫자 관찰 있음; 교정 검증 완료가 아님 |
| `NO_VALID_OBSERVATIONS` | 후보 없음 또는 관찰값 없음; 정상 발음 판정이 아님 |
| `VISUAL_INPUT_PROCESSING_FAILED` | 영상 수용·binding·공통 처리·정리 등에서 상위 예외 발생 |
| 후보별 `reasonCodes` | decode, detector, window planning, reliability, backend 등의 세부 실패 |

예를 들어 facade는 `video_decode_failed`, `visual_detector_failed`, `phone_window_planning_failed`, `phone_window_decode_failed`, `phone_window_detector_failed`, `visual_reliability_failed`, `visual_reliability_not_supported`, `visual_backend_failed` 등의 reason을 기록한다. 실제 미디어 오류에서는 더 구체적인 enum code가 사용될 수 있다.

후보별 단계 실패는 일반적으로 빈 measurements와 stage/reason으로 남는다. 반면 후보 admission/handoff나 공통 처리에서 예외가 탈출하면 상위에서 영상 분기 전체를 `VISUAL_INPUT_PROCESSING_FAILED`로 바꾸며, 부분 관찰이 보존된다고 보장하지 않는다. 이미 승인된 음성 분석은 유지된다.

## 해석 금지와 남은 확인

- 혀 위치·기류·성문·구개·3D 입술 돌출·의학적 이상을 2D 입술 관찰로 진단하지 않는다.
- 입 벌림 ratio가 크거나 작다는 이유만으로 그 사용자의 발음 오류, 입을 더 벌리거나 닫아야 한다는 교정을 확정하지 않는다.
- 전체 영상 8프레임과 짧은 CTC 구간만으로 말하기 속도·억양·문장 전체 조음을 판정하지 않는다.
- 배포 준비 상태, receipt 존재, geometry gate 통과, 실제 영상 완주, 피드백 품질은 각각 다른 근거가 필요하다.
- 신규 영상 추론·성능/지연·다양한 자세/조명/카메라에 대한 QA는 이번 조사 범위 밖이며 개발자 확인 대상으로 남긴다.
