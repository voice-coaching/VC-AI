# 프론트엔드 종합점수 API 명세서

갱신 기준일: 2026-11-06. 대상: 프론트엔드 개발자. 기존 공개 계약의 점수 조회·파싱·표시 명세다.

## 1. 먼저 적용할 사항

**종합점수 필드명은 `score.overallScore`이며, 값은 `number | null`이다.**
화면에 표시할 값은 AI 서버가 계산한 0~100점 종합점수다. 프론트에서 채점표를 검증하거나 점수를 다시 계산하지 않는다.

| 응답 경계 | HTTP 응답 JSON에서 읽을 위치 | 현재 프론트 클라이언트 반환값에서 읽을 위치 |
| --- | --- | --- |
| Backend 분석 조회 | `body.data.canonicalAnalysis.score.overallScore` | `view.canonicalAnalysis.score.overallScore` |
| AI Direct 분석 조회·SSE | `body.result.score.overallScore` | `view.result.score.overallScore` |
| Direct 연습용 결과 | `body.result.practiceResult.score.overallScore` | 같은 위치. `practiceResult`는 선택적 |
| Direct 저장 이력 목록 | `body.data[i].result.score.overallScore` | `items[i].result.score.overallScore` |
| Direct 문장별 연습 묶음 | `body.results[i].analysis.result.score.overallScore` | 같은 위치 |

Backend HTTP 응답의 최상위 `result`는 **boolean**이다. Direct 응답의 `result`는 **분석 결과 객체 또는 null**이다.
두 응답을 같은 envelope 파서로 처리하면 점수 경로가 깨진다.

## 2. 공통 Score 객체

정상 점수의 최소 객체는 다음과 같다.

```json
{
  "overallScore": 73.6,
  "validity": "RUBRIC_COMPUTED",
  "reason": "EVIDENCE_BOUND_PRACTICE_SCORE"
}
```

| 필드 | 타입 | 필수 | 의미 |
| --- | --- | --- | --- |
| `overallScore` | `number \| null` | 필수 | 0~100의 유한한 숫자. 소수점 첫째 자리까지 제공하며 정수도 가능 |
| `validity` | `string` | 필수 | 점수 산출 상태 |
| `reason` | `string` | 필수 | 점수 상태의 사유 코드 |
| `details` | 선택 객체 | 선택 | 음성 6항목 점수·상태·가중치. [세부점수 명세](frontend-score-details-response.md) 참조 |
| `rubricRevision` | 선택적 메타데이터 | 선택 | 과거 결과에 존재할 수 있음. 총점 표시 조건이 아님 |
| `criteria` | 선택적 메타데이터 | 선택 | 과거 음소별 채점표. 현재 6항목 배열이 아니며 총점 표시 조건이 아님 |

### 상태 조합과 화면 처리

| `overallScore` | `validity` | `reason` | 처리 |
| --- | --- | --- | --- |
| 0~100 숫자 | `RUBRIC_COMPUTED` | `EVIDENCE_BOUND_PRACTICE_SCORE` | 숫자 점수 표시 |
| `null` | `UNSCORABLE` | `SCORING_FAILED` | 점수 미제공 표시. 0점으로 변환하지 않음 |
| `null` | `NOT_AVAILABLE` | `INPUT_NOT_ACCEPTED` | 입력 평가 불가 안내 |
| `null` | `NOT_AVAILABLE` | `RESULT_NOT_DELIVERED` | 결과 전달 상태 안내 |
| `null` | `INSUFFICIENT_EVIDENCE`, `NOT_CALIBRATED`, `NOT_AVAILABLE` 중 하나 | `NO_APPROVED_SCORING_CONTRACT` | 과거 계약의 점수 미제공 상태 |

`0`은 정상 점수다. `"73.6"`처럼 문자열로 온 값은 계약 오류이며 임의로 숫자로 바꾸지 않는다.
`null`, 누락, `NaN`, 빈 문자열을 `Number(...)`로 바꿔 0점으로 표시하지 않는다.

`RUBRIC_COMPUTED`는 기존 상태명을 유지한 것이며, 현재 응답에서는 음성 6항목의 종합점수 산출 완료를 뜻한다.
100분위·백분위 순위나 발음 정확성 단독 점수가 아니다.

## 3. Backend를 통한 조회

### 요청

```http
GET /api/v3/analyses/{analysisId}?waitSeconds=8
Accept: application/json
```

| 항목 | 명세 |
| --- | --- |
| 인증 | 기존 로그인 인증과 해당 분석의 소유자 확인. 기존 공통 클라이언트 사용 |
| `analysisId` | Backend 분석 ID인 양의 정수. Direct UUID와 구분 |
| `waitSeconds` | 선택, 기본 0. 현재 프론트 결과 대기는 8초 사용. 대기 후 현재 상태 반환 |
| 성공 HTTP 상태 | `200 OK` |
| 응답 헤더 | `Cache-Control: no-store` |
| 브라우저 경로 | `/api/backend/api/v3/analyses/{analysisId}?waitSeconds=8` |
| 프론트 기존 함수 | `canonicalApi.get({ analysisId, recordingId }, signal, 8)` |

### 응답 예시

아래는 **점수 경로를 설명하기 위한 발췌 예시**다. 생략한 식별자·상태·동작 필드가 있으므로 전체 응답 파서 테스트 fixture로 사용하지 않는다.
숫자 73.6은 실제 운영 Direct 검증에서도 확인한 값이지만, 아래 Backend 분석 ID 123은 예시다.

```json
{
  "result": true,
  "message": "분석 상태와 근거를 조회했습니다.",
  "data": {
    "schemaVersion": "voice-coaching.canonical-analysis-view.v3",
    "analysisId": 123,
    "jobStatus": "COMPLETED",
    "canonicalAnalysis": {
      "score": {
        "overallScore": 73.6,
        "validity": "RUBRIC_COMPUTED",
        "reason": "EVIDENCE_BOUND_PRACTICE_SCORE"
      }
    },
    "serviceFailure": null,
    "persistenceStatus": "AVAILABLE"
  }
}
```

**HTTP 원문:** `body.data.canonicalAnalysis.score.overallScore`.

**기존 `createHttpClient(...).request()` 반환값:** 이 함수는 이미 `payload.data`를 반환한다.
따라서 `view.canonicalAnalysis.score.overallScore`로 읽으며 `view.data`를 다시 찾지 않는다.
일반 `fetch()`의 `response.json()`은 자동으로 `data`를 벗기지 않는다.

신규 결과의 공개 뷰는 `voice-coaching.canonical-analysis-view.v3`, profile은 `CANONICAL_AUDIOVISUAL_20261007_V6`다.
이 profile 이름은 영상이 반드시 포함됐다는 뜻이 아니다. 음성 전용도 같은 v6 계약을 사용한다.
과거 결과의 공개 뷰 v2와 신규 v3는 각각 지원되는 파서로 읽는다.

### 작업 상태

| `data.jobStatus` | 의미 | 점수 화면 |
| --- | --- | --- |
| `PENDING`, `PROCESSING` | 대기·분석 중. `canonicalAnalysis=null` 가능 | 로딩 상태 유지 |
| `COMPLETED` | 분석 결과 생성 완료 | Score 객체에 따라 숫자 또는 미제공 표시 |
| `FAILED` | 분석 실패 | 서버 실패 안내. 임의 점수 생성 금지 |

HTTP 200과 `result=true`는 **조회 성공**을 뜻한다. 분석 완료나 숫자 점수 존재를 보장하지 않는다.
`COMPLETED`도 Score가 `null`인 과거 결과를 포함할 수 있다.

`persistenceStatus=SAVING/RETRYING`인 결과라도 유효한 점수는 화면에 보존한다.
저장 완료와 학습 완료 버튼 활성화는 별도 상태·서버 `actions`로 판단한다.
Backend가 추가하는 `rawResult` 같은 envelope 필드 때문에 공개 뷰 전체를 거부하지 않는다.
UI 점수는 `canonicalAnalysis.score`에서 읽고 `rawResult`를 화면 모델로 직접 사용하지 않는다.

### 조회 실패

400은 잘못된 분석 ID, 404는 조회할 수 없는 결과, 409는 분석 시도 변경, 503은 결과 조회 불가 등이다.
오류는 HTTP 상태와 응답 `code`로 처리한다. 예: `INVALID_ANALYSIS_ID`, `RESOURCE_NOT_FOUND`,
`CANONICAL_ANALYSIS_CHANGED`, `CANONICAL_RESULT_UNAVAILABLE`.
조회 오류를 점수 0으로 바꾸거나, 점수가 없다는 이유로 분석 POST를 자동 재실행하지 않는다.

## 4. AI Direct 조회·SSE

### 요청

```http
GET https://ai.voice-coaching.site/direct/api/v1/analyses/{jobId}
Accept: application/json
```

- `jobId`: Direct 접수 시 받은 UUID. Backend 정수 분석 ID와 다르다.
- 현재 익명 Direct 경로는 `credentials: "omit"`을 사용한다. Backend ApiResponse로 감싸지 않는다.
- 기존 함수: `readDirect(jobId, signal)`.

점수 경로만 발췌한 응답 예시:

```json
{
  "schemaVersion": "voice-coaching.direct-analysis.v1",
  "status": "RESULT_READY",
  "result": {
    "decision": { "status": "ACCEPT", "reason_code": null, "stage": null },
    "score": {
      "overallScore": 73.6,
      "validity": "RUBRIC_COMPUTED",
      "reason": "EVIDENCE_BOUND_PRACTICE_SCORE"
    },
    "failure": null
  },
  "failure": null
}
```

실제 전체 응답에는 `jobId`, `executionId`, `clientAttemptId`, `sequence`, `coaching`, `sourceIdentity`,
보관·이력 상태 등이 포함된다. 기존 `directViewSchema`로 전체 응답을 검증한다.

| `status` | 처리 |
| --- | --- |
| `QUEUED`, `RUNNING` | 결과 대기. `result=null`일 수 있음 |
| `RESULT_READY` | `view.result.score`에서 점수 확인 |
| `FAILED`, `CANCELLED` | 실패·취소 상태 안내 |

SSE는 `GET .../analyses/{jobId}/events`이며 이벤트 이름은 `analysis`다.
`data:`를 JSON 파싱하면 위와 같은 DirectView 객체가 된다. SSE에서 `result`만 떼어 기존 화면 state에 저장하지 않는다.
최종 이벤트에도 `onView(view)`로 state를 갱신한 뒤 구독을 종료한다. 다른 `jobId`·`executionId`의 결과를 덮어쓰지 않는다.

`practiceResult`가 있다면 `view.result.practiceResult.score`는 `view.result.score`의 같은 점수다.
이 필드는 과거 응답에서 없을 수 있으므로 필수로 요구하지 않고, 두 점수를 합산·평균하지 않는다.
현재 녹음 단위의 점수이며 한 녹음의 문장별 점수를 자동 제공하는 필드가 아니다.

`archiveStatus`와 `historySyncStatus`가 완료되지 않았다는 이유로 이미 도착한 숫자 점수를 숨기지 않는다.
피드백 생성 상태와 숫자 점수의 산출 상태도 구분한다.

## 5. 기존 프론트 코드에 연결하는 방법

기존 파서·변환기를 재사용한다. 아래는 앱 내부 연결 예시이며 새로운 채점 로직이 아니다.

```ts
// Backend 경로: canonicalApi.get()은 ApiResponse.data를 반환한다.
const view = await canonicalApi.get({ analysisId, recordingId }, signal, 8);
const analysis = canonicalPresentation(view);
const overallScore = analysis.overallScore;
// 화면에는 <ReportScore score={overallScore} /> 전달.
```

```ts
// Direct 경로: readDirect()는 DirectView를 반환한다.
const view = await readDirect(jobId, signal);
if (view.status === "RESULT_READY" && view.result !== null) {
  const analysis = directAnalysisPresentation(view.result);
  const overallScore = analysis.overallScore;
  // 화면에는 <ReportScore score={overallScore} /> 전달.
}
```

점수 객체를 직접 다루는 경계에서 필요한 최소 검사는 다음과 같다. 전체 응답의 기존 인증·식별자·시도 검증은 유지한다.

```ts
type PublicScore = {
  overallScore: number | null;
  validity: string;
  reason: string;
  rubricRevision?: unknown;
  criteria?: unknown;
};

function scoreForDisplay(score: PublicScore | null | undefined): number | null {
  if (score?.validity !== "RUBRIC_COMPUTED" ||
      score.reason !== "EVIDENCE_BOUND_PRACTICE_SCORE") return null;
  const value = score.overallScore;
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 100
    ? value : null;
}

function formatOverallScore(score: number | null): string {
  return score === null
    ? "점수 미제공"
    : `${new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 1 }).format(score)}점`;
}
```

표시 문구는 `formatOverallScore(scoreForDisplay(...))`로 생성할 수 있다.
`ReportScore`는 현재 0점과 소수점을 표시할 수 있으며 `null`에는 `—`/`점수 미제공`을 표시한다.
현재의 미표시가 컴포넌트 앞에서 값을 누락시키는 문제인지 개발자가 아래 순서로 확인한다.

## 6. 점수 미표시 디버깅 순서

1. **Network 원문:** 현재 화면이 Backend/Direct/이력 중 어떤 API를 호출하는지 확인한다. 1절 경로에서 숫자인지 `null`인지 확인한다.
2. **envelope 처리:** Backend 공통 클라이언트가 이미 `data`를 벗겼는데 다시 `data`를 찾거나, Direct를 Backend envelope로 파싱하지 않았는지 확인한다.
3. **파서:** `canonicalScoreSchema`에서 최소 3필드 객체를 허용한다. `rubricRevision`, 9개 `criteria`, `scoreHierarchy`, `coaching.scoring`을 요구하지 않는다. 새 릴리스 ID·공식 검산표 해시로 총점을 거부하지 않는다.
4. **변환기:** `canonicalPresentation(view).overallScore` 또는 `directAnalysisPresentation(view.result).overallScore`가 원래 숫자를 유지하는지 확인한다. `pronunciationScore`, `compositeGrade`, `visual.score`로 대신 읽지 않는다.
5. **state·렌더링:** 최종 조회/SSE가 최신 시도의 state를 갱신하는지, `<ReportScore>`에 숫자가 전달되는지 확인한다. `if (score)`, `score || null`, 고정 mock 점수를 사용하지 않는다.
6. **저장·이력 화면:** 정상 점수를 보관/이력 동기화 대기 때문에 지우지 않는다. 다른 응답이 기존 state를 `null`로 덮어쓰는지 확인한다.

Network에 숫자가 있으면 프론트 파싱·변환·표시 경로를 점검한다.
Network부터 `null`이면 `validity/reason`, 분석 ID와 실행 ID를 기준으로 서버 측 채점·전달을 점검한다.
프론트가 임의 점수를 채우지 않는다. 이전에 저장된 `null` 점수는 새 AI 배포만으로 소급 변경되지 않는다.

### 프론트 개발자 확인 항목

| 입력/상태 | 기대 결과 |
| --- | --- |
| `73.6 / RUBRIC_COMPUTED` | `73.6점` 표시 |
| `0 / RUBRIC_COMPUTED` | `0점` 표시 |
| `100 / RUBRIC_COMPUTED` | `100점` 표시 |
| 정상 최소 객체, `criteria`와 `rubricRevision` 없음 | 숫자 표시 유지 |
| 과거 상세 메타데이터를 해석할 수 없음 | 총점 표시 유지. 상세만 생략 가능 |
| `null / UNSCORABLE / SCORING_FAILED` | 점수 미제공, 0점 생성 금지 |
| `PENDING/PROCESSING`, `canonicalAnalysis=null` | 로딩 |
| 유효한 점수와 `SAVING/RETRYING` | 점수 유지, 저장 안내 별도 |
| Direct 최종 SSE | 최신 state 및 점수 갱신 |
| `rawResult` 같은 추가 envelope 필드 | 기존 공개 필드 파싱 유지 |
| `"73.6"`, 음수, 100 초과 | 계약 오류, 보정해서 표시하지 않음 |

위 표는 개발자 확인 기준이며 이번 문서 작업에서 브라우저 QA를 실행했다는 뜻은 아니다.

## 7. 이력·일괄 조회와 현재 점수 범위

- Backend Direct 이력: `GET /api/direct-analysis-history`, 로그인 필요. HTTP 원문은 `data` 배열이며 항목별 `result.score.overallScore`를 사용한다. `storageStatus`는 저장 상태다.
- Direct 묶음: `GET https://ai.voice-coaching.site/direct/api/v1/practice-batches/{batchId}?waitSeconds=20`. `results`가 있으면 항목별 `analysis.result.score.overallScore`를 읽는다. 처리 중 `results=null` 가능. 묶음 전체를 합친 별도 점수는 제공하지 않는다.
- 세션 상태 조회와 구형 `GET /api/analyses/{analysisId}`의 `overallScore`는 별도 DTO다. 현재 canonical 상세 화면은 3절의 API와 필드 경로를 기준으로 연결한다.

현재 종합점수는 발음 정확성 40%, 발음 속도 10%, 조음 속도 10%, 쉼 10%, 억양·음높이 15%, 음량·강도 15%를 서버에서 합산한다.
상세 6항목 점수와 공식 검산표는 프론트 총점 표시의 필수 입력이 아니다.
현재 운영 점수에 입술 모양은 포함되지 않는다. 제안된 영상 60:40 가중치를 프론트에서 미리 계산하지 않는다.

## 8. 확인 근거와 이번 작업 범위

| 구분 | 확인 사항 |
| --- | --- |
| AI 설치 버전 | 이번 작업 중 `/health/deployment`를 읽어 `2aa5f8719a7752d5e0ef1deaa6ba76a096baf4fb` 확인 |
| 기존 운영 분석 검증 | 2026-11-05 09:37:09 UTC, 외부 Direct 테스트에서 73.6점·정상 피드백·GPT 1회 확인 |
| 기존 내부 회귀 검증 | 같은 배포의 사전 격리 검사에서 46.6점과 Backend 공개 응답 변환 유지 확인. 운영 DB 저장 검증과 구분 |
| Backend 검토 소스 | `ebc17e8a40bacc47d9d05d97e5a16849dc05bb37` |
| Frontend 검토 소스 | `e120716cc77ea63c416970871d98b60a4f785963` |

이 명세서의 초안 작성은 소스·기존 검증 기록·설치 identity의 읽기와 문서화 작업이었다.
초안 작성 시 새 음성 분석, GPT 호출, 브라우저 검증, 애플리케이션 코드 수정은 수행하지 않았다. 문서 공유 PR과 소스 배포는 기능 구현·실제 분석 검증과 구분한다.
따라서 현재 사용자의 점수 미표시 원인을 이 문서만으로 확정하지 않는다.

### 구현 정본

- [AI 공개 점수 생성](https://github.com/voice-coaching/intelligentAI/blob/2aa5f8719a7752d5e0ef1deaa6ba76a096baf4fb/src/voice_coach/backend_analysis/canonical_result.py)
- [AI Direct 조회·SSE](https://github.com/voice-coaching/intelligentAI/blob/2aa5f8719a7752d5e0ef1deaa6ba76a096baf4fb/src/voice_coach/direct_analysis/api.py)
- [Backend v3 조회](https://github.com/voice-coaching/VC-BE/blob/ebc17e8a40bacc47d9d05d97e5a16849dc05bb37/src/main/java/org/example/voice/analysis/controller/CanonicalHandoffAnalysisController.java)
- [Backend 공개 Score DTO](https://github.com/voice-coaching/VC-BE/blob/ebc17e8a40bacc47d9d05d97e5a16849dc05bb37/src/main/java/org/example/voice/analysis/domain/model/CanonicalAnalysisView.java)
- [Frontend 점수 파서](https://github.com/voice-coaching/VC-FE/blob/e120716cc77ea63c416970871d98b60a4f785963/src/lib/canonical-score.ts)
- [Frontend Backend 응답 변환](https://github.com/voice-coaching/VC-FE/blob/e120716cc77ea63c416970871d98b60a4f785963/src/lib/canonical-presentation.ts)
- [Frontend Direct 응답 변환](https://github.com/voice-coaching/VC-FE/blob/e120716cc77ea63c416970871d98b60a4f785963/src/lib/direct-analysis-presentation.ts)
- [Frontend 숫자 표시](https://github.com/voice-coaching/VC-FE/blob/e120716cc77ea63c416970871d98b60a4f785963/src/components/report-score.tsx)
