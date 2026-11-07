# Frontend 종합점수·세부점수 API 명세

작성 기준: 2026-11-08, `feat/public-score-details-20261108` 구현 후보.
운영 배포 완료를 뜻하지 않는다. Backend 전달 코드와 AI 응답 writer의 반영이 필요하다.
기존 총점 계약은 [종합점수 명세](frontend-overall-score-response.md)를 따른다.

## 1. 읽을 위치

기존 `score.overallScore`, `validity`, `reason`은 유지한다. 선택 객체 **`score.details`**를 추가한다.
기존 `score.criteria`는 과거 9개 음소 채점표이며 새 6항목 배열로 재사용하지 않는다.

| 경계 | 세부점수 경로 |
| --- | --- |
| Backend HTTP 원문 | `body.data.canonicalAnalysis.score.details` |
| 기존 `canonicalApi.get()` 반환값 | `view.canonicalAnalysis.score.details` |
| AI Direct 조회·SSE 원문 | `body.result.score.details` |
| Direct 연습 결과 | `body.result.practiceResult.score.details` |
| Direct 저장 이력 목록 | `body.data[i].result.score.details` |
| 문장별 독립 연습 묶음 | `body.results[i].analysis.result.score.details` |

위 경로의 `.details` 대신 `.overallScore`를 읽으면 기존 총점이다.
Backend 클라이언트가 이미 `data`를 풀어서 반환하면 `.data`를 한 번 더 찾지 않는다.
Backend 최상위 `result`는 boolean이고 Direct의 `result`는 분석 결과 객체 또는 null이다.

## 2. API

새 endpoint나 추가 GPT·분석 요청은 필요하지 않다.

| Method | Path | 인증·상태 |
| --- | --- | --- |
| GET | `/api/v3/analyses/{analysisId}?waitSeconds=8` | 기존 Backend 로그인·분석 소유권 확인. 조회 성공 200, `Cache-Control: no-store` |
| GET | `https://ai.voice-coaching.site/direct/api/v1/analyses/{jobId}` | 기존 익명 Direct UUID 조회. 성공 200 |
| GET | `https://ai.voice-coaching.site/direct/api/v1/analyses/{jobId}/events` | 기존 SSE. 결과 snapshot의 score 사용 |
| GET | `/api/direct-analysis-history` | 기존 Backend 로그인. 각 이력의 result에 같은 선택 필드 사용 |

Backend analysisId는 양의 정수, Direct jobId는 UUID다. 기존 상태·에러·인증 계약은 유지한다.
HTTP 200은 조회 성공이며 분석 완료를 보장하지 않는다. 대기 중 result/canonicalAnalysis는 null일 수 있다.
오류·세부점수 누락을 0점으로 바꾸거나 분석 POST 자동 재실행의 이유로 사용하지 않는다.

## 3. ScoreDetails

`details`가 있으면 아래 필드는 모두 있다. 과거 결과나 6항목 계산표가 없는 결과는 details를 생략한다.
필드 부재는 총점·피드백 표시를 막는 조건이 아니다. 서버는 표시용 점수만 전달한다.
원본 CORE, 내부 검산표·파일 해시·저장소 경로·음성·얼굴 좌표는 추가하지 않는다.

| 필드 | 타입 | 의미 |
| --- | --- | --- |
| `schemaVersion` | string | `voice-coaching.score-details.v1` |
| `validationStatus` | string | `EXPERT_REVIEW_PENDING`: 전문가 검증 전 기준에 따른 연습 점수 |
| `aggregationMode` | string | `AUDIO_ONLY` 또는 `AUDIO_VISUAL` |
| `audioScore` | number 또는 null | 음성 6항목으로 계산한 음성 종합점수, 0~100 |
| `visualScore` | number 또는 null | 유효한 입술 총점. 영상 부재·총점 평가 불가 시 null |
| `audioWeight` | integer | 최종 총점에서 음성이 차지하는 비율, % |
| `visualWeight` | integer | 최종 총점에서 영상이 차지하는 비율, % |
| `criteria` | 배열, 6개 | 아래 고정 순서의 음성 세부점수 |

`AUDIO_ONLY`는 가중치 100/0, `AUDIO_VISUAL`은 현재 60/40이다.
유효한 입술 점수만으로 음성 총점 부재를 대체하지 않는다. AUDIO_ONLY에서 visualScore가
남아 있더라도 영상이 총점에 합산된 것으로 해석하지 않는다.

### criteria 항목

| 필드 | 타입 | 의미 |
| --- | --- | --- |
| `criterionId` | string | 아래 항목 식별자 |
| `label` | string | 화면 표시명 |
| `score` | number 또는 null | 각 항목의 0~100점 |
| `status` | string | `SCORED` 또는 `UNSCORABLE` |
| `reasonCode` | string | 측정·평가 보류 사유. 필요 시 사용자용 문구로 매핑 |
| `weight` | integer | **음성 종합점수 내부**의 배점 비율, % |

| 순서 | criterionId | 기본 label | 현재 weight |
| --- | --- | --- | --- |
| 1 | `pronunciation_accuracy` | 발음 정확성 | 40 |
| 2 | `speaking_rate` | 발음 속도 | 10 |
| 3 | `articulation_rate` | 조음 속도 | 10 |
| 4 | `pauses` | 쉼 | 10 |
| 5 | `pitch` | 억양·음높이 | 15 |
| 6 | `intensity` | 음량·강도 | 15 |

SCORED는 숫자이고 UNSCORABLE은 null이다. **0점은 유효하며 결측과 다르다.**
영상 합산에서도 이 6항목은 음성 내부 점수다. 입술 점수를 7번째 음성 항목으로 추가하지 않는다.
가중치·합산·검산표 revision을 Frontend에 고정하거나 응답을 거부하는 조건으로 사용하지 않는다.

## 4. 응답 예시

경로 설명용 부분 예시이며 전체 응답 fixture가 아니다. 수치는 기존 시험 결과를 사용했지만
운영에 새 필드가 배포됐다는 의미는 아니다.

```json
{
  "result": true,
  "data": {
    "canonicalAnalysis": {
      "score": {
        "overallScore": 46.6,
        "validity": "RUBRIC_COMPUTED",
        "reason": "EVIDENCE_BOUND_PRACTICE_SCORE",
        "details": {
          "schemaVersion": "voice-coaching.score-details.v1",
          "validationStatus": "EXPERT_REVIEW_PENDING",
          "aggregationMode": "AUDIO_ONLY",
          "audioScore": 46.6,
          "visualScore": null,
          "audioWeight": 100,
          "visualWeight": 0,
          "criteria": [
            {"criterionId":"pronunciation_accuracy","label":"발음 정확성","score":94.4,"status":"SCORED","reasonCode":"FIXED_SERVER_RUBRIC","weight":40},
            {"criterionId":"speaking_rate","label":"발음 속도","score":7.6,"status":"SCORED","reasonCode":"EXPONENTIAL_REFERENCE_PROXIMITY","weight":10},
            {"criterionId":"articulation_rate","label":"조음 속도","score":28.0,"status":"SCORED","reasonCode":"EXPONENTIAL_REFERENCE_PROXIMITY","weight":10},
            {"criterionId":"pauses","label":"쉼","score":46.2,"status":"SCORED","reasonCode":"EXPONENTIAL_REFERENCE_PROXIMITY","weight":10},
            {"criterionId":"pitch","label":"억양·음높이","score":2.1,"status":"SCORED","reasonCode":"EXPONENTIAL_REFERENCE_PROXIMITY","weight":15},
            {"criterionId":"intensity","label":"음량·강도","score":2.1,"status":"SCORED","reasonCode":"EXPONENTIAL_REFERENCE_PROXIMITY","weight":15}
          ]
        }
      }
    }
  }
}
```

영상 예: audioScore=80, visualScore=70이면 기존 계산기의 overallScore는 76이고
aggregationMode=AUDIO_VISUAL, 가중치는 60/40이다. Frontend는 서버 값을 표시한다.

총점이 null이어도 세부항목 일부는 SCORED일 수 있다. pitch만 평가 불가라면 해당 항목은
`score=null, status=UNSCORABLE, reasonCode=F0_INSUFFICIENT_VALID_FRAMES`로 오고,
나머지 항목의 유효한 숫자는 유지된다. 화면에서 ‘평가 불가’와 ‘세부점수 미제공’을 구분한다.

## 5. 영상 입술 하위 항목

기존 `canonicalAnalysis.visual` 구조는 유지한다. 선택적인 입술 필드의 Backend·Frontend
reader 적용과 운영 영상 접수 활성화는 별도로 확인한다.

| Backend HTTP 원문 경로 | 의미 |
| --- | --- |
| `body.data.canonicalAnalysis.visual.score` | 입술 총점 |
| `body.data.canonicalAnalysis.visual.subScores` | 입술 전체 하위 항목 요약 |
| `body.data.canonicalAnalysis.visual.phoneAssessments[i].subScores` | 해당 발생 위치의 하위 항목 |

식별자는 `lip_aperture`(열림), `lip_rounding`(원순성), `lip_closure`(폐쇄),
`lip_timing`(타이밍)이다. 형식·평가 제외 상태는 [입술 계약](../contracts/lip-provisional-score-v1.md)을 따른다.
score.details 추가는 위 기존 입술 필드의 의미나 공개 범위를 바꾸지 않는다.

## 6. Frontend 타입·표시

```ts
type CriterionId = 'pronunciation_accuracy' | 'speaking_rate'
  | 'articulation_rate' | 'pauses' | 'pitch' | 'intensity';
type ScoreDetails = {
  schemaVersion: 'voice-coaching.score-details.v1';
  validationStatus: 'EXPERT_REVIEW_PENDING';
  aggregationMode: 'AUDIO_ONLY' | 'AUDIO_VISUAL';
  audioScore: number | null;
  visualScore: number | null;
  audioWeight: number;
  visualWeight: number;
  criteria: Array<{
    criterionId: CriterionId;
    label: string;
    score: number | null;
    status: 'SCORED' | 'UNSCORABLE';
    reasonCode: string;
    weight: number;
  }>;
};
type PublicScore = {
  overallScore: number | null;
  validity: string;
  reason: string;
  details?: ScoreDetails;
  rubricRevision?: unknown;
  criteria?: unknown; // 과거 음소 채점표. details.criteria와 다름.
};
const score = view.canonicalAnalysis?.score; // canonicalApi.get() 반환값
const rows = score?.details?.criteria ?? [];
const formatScore = (value: number | null | undefined) =>
  typeof value === 'number' && Number.isFinite(value) ? value.toFixed(1) : '—';
```

기존 runtime parser·presentation 변환기도 details를 보존하도록 확장한다.
`if (!score)`로 숫자 0을 누락시키지 않는다. details 없는 과거 결과에서도 총점과 피드백은 표시한다.
검산표·CORE 해시·모델 릴리스의 일치 여부를 클라이언트 점수 표시 조건으로 추가하지 않는다.

서버는 중간 계산에서 정밀도를 유지한다. 반올림된 세부점수를 다시 합산하면 0.1점 정도
차이가 날 수 있으므로 총점은 항상 overallScore를 사용한다.

## 7. Backend 전달·적용 순서

AI result v6와 handoff v2 projection의 score.details가 Backend 공개 DTO까지 보존돼야 한다.
Backend에는 optional Score.details DTO와 구조 schema 수용 변경이 필요하다.
계산식·공식 검산표·모델 버전을 검증하는 게이트는 추가하지 않는다.

1. Backend reader가 details를 저장·투영하도록 반영한다. 필드가 없는 이전 응답도 허용한다.
2. AI 공개 응답 writer를 반영한다. H5·원본 GPT 입력·호출 횟수·채점 계산은 유지한다.
3. Frontend parser·타입·presentation에서 details를 보존하고 위 경로로 표시한다.

이미 저장된 JSON에 details가 없으면 소급 생성되지 않는다. 과거 점수 보완이나 재분석은
별도 작업이며 이번 변경에서 원본을 덮어쓰거나 GPT를 자동 재호출하지 않는다.
