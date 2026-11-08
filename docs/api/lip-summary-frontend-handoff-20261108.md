# Frontend 인계: 영상 최종 점수·피드백 API

2026-11-08 AI 구현 명세. 운영 배포와 Frontend 적용은 아직 수행하지 않았다.
JSON Schema: [browser_video_summary_v1.schema.json](../contracts/browser_video_summary_v1.schema.json).
업로드·start·상태 조회는 [기존 원본 영상 API](browser-original-video-v1.md)를 유지한다.

## 결과 조회

`GET /browser/v1/video-analysis-jobs/{jobId}/summary`

기존과 같은 Backend 발급 업로드 권한 `Authorization: Bearer <grant>`와 허용된 `Origin`을
사용한다. 소유자·세션·clientRequestId 결속과 만료 검증은 그대로 적용된다.
완료 후 화면용 결과는 기존 `/result` 대신 이 경로에서 읽는다.
음소별 측정·시간 구간·세부 조음 성분·원본 CORE는 포함하지 않는다.

200 응답 예시(식별자·피드백은 설명용):

```json
{
  "schemaVersion": "voice-coaching.browser-video-summary.v1",
  "jobId": "10000000-0000-4000-8000-000000000001",
  "executionId": "10000000-0000-4000-8000-000000000002",
  "clientRequestId": "10000000-0000-4000-8000-000000000003",
  "status": "COMPLETED",
  "score": {
    "overallScore": 76.1,
    "validity": "RUBRIC_COMPUTED",
    "reason": "EVIDENCE_BOUND_PRACTICE_SCORE"
  },
  "visual": {
    "score": 51.0,
    "scoreKind": "PROVISIONAL",
    "scoreScope": "ASSESSED_PHONES_ONLY"
  },
  "coaching": {
    "feedback": "* 입술 → 안내된 발음을 읽으며 입모양을 비교해 보세요."
  },
  "failure": null
}
```

| 화면 | 사용할 값 | 처리 |
|---|---|---|
| 종합점수 | `score.overallScore` | 0~100 또는 null. 0도 정상 점수이므로 truthy 조건으로 숨기지 않음 |
| 입술점수 | `visual.score` | 0~100 또는 null. 종합점수와 별개로 표시 |
| 입술점수 범위 | `visual.scoreScope` | `ASSESSED_PHONES_ONLY`이면 ‘관찰된 부분 기준’ 표기 |
| 입술점수 성격 | `visual.scoreKind` | `PROVISIONAL`은 임시 비교 점수이며 발음 정확도 백분율로 표시하지 않음 |
| 피드백 | `coaching.feedback` | 서버 문자열을 그대로 텍스트로 표시, 음소별 교정을 새로 만들지 않음 |

`scoreScope`는 `WHOLE_CLIP`, `ASSESSED_PHONES_ONLY`, `UNAVAILABLE` 중 하나다.
`scoreKind`는 `CALIBRATED`, `PROVISIONAL`, `NONE` 중 하나다.
입술점수가 null이어도 종합점수와 피드백을 계속 표시한다. 측정할 수 없는 부분을
0점으로 대체하거나 입술점수 null만으로 전체 분석 실패를 표시하지 않는다.

화면에서 ‘발음별 관찰 보기’, 각 음소 시간·프레임 수·세부 성분의 ‘평가 정보 부족’ 목록은
제거한다. 이 응답에는 해당 필드가 없으므로 기존 전체 결과의 엄격한 스키마 파서를 재사용하지 않는다.
`jobId`, `executionId`, `clientRequestId`는 현재 시도와 대조해 이전 시도의 응답이 덮어쓰지 않게 한다.
점수 재계산·H5 해시·공식 검산표 검증은 Frontend의 책임이 아니다.

## HTTP와 무결성

| 상태 | 의미 |
|---|---|
| 200 | 최종 요약. 본문 `status`와 `failure`도 확인 |
| 401 | 권한 만료·무효. 같은 시도의 권한 갱신 절차 사용 |
| 403 | 허용하지 않는 Origin |
| 404 | 없는 작업 또는 다른 소유자의 작업 |
| 409 | 결과 준비 전 또는 결과 없는 종료 상태. 상태 조회 후 판단 |
| 5xx | 서버 오류. 새 분석·GPT 실행으로 자동 우회하지 않음 |

`X-Result-SHA256`과 `ETag`는 **이 요약 응답 bytes**의 digest다. `Cache-Control: no-store`다.
상태 조회의 기존 `resultSha256`은 전체 보존 결과의 digest이므로 요약 bytes와 비교하지 않는다.
기존 `/result`는 호환을 위해 원본 결과를 계속 반환하지만 새 화면에서는 `/summary`를 사용한다.
요약 GET·동일 결과 재조회는 분석/GPT를 다시 실행하지 않는다.
