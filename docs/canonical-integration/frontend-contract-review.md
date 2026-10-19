# VC-FE 공개 소스 기준 v4 호환성 재점검

확인일: 2026-10-01. 대상은 [VC-FE 공개 저장소](https://github.com/voice-coaching/VC-FE)와 [현재 v6 계약](v6-unification.md)이다. 공개 원격 refs 조회·fetch 후 immutable commit의 source를 읽었다. FE/Backend/RunPod 운영 배포 SHA·실제 요청·E2E는 이번에 확인하지 않았다. FE 파일·운영 설정은 변경하지 않았다.

## 1. 기준 브랜치 차이와 판정

| 기준 | 확인한 commit | 의미 |
|---|---|---|
| 원격 기본 브랜치 HEAD → main | `81b27dee88ae1c09bb4f9c2581be3552ac283137` | commit 시각 2026-09-29 15:18:06 +09:00. 이번 공개 계약 검토의 우선 기준 |
| 원격 dev | `d54ceae5065880ccabd8f00ffe83500ab9077dfd` | commit 시각 2026-09-19 23:54:36 +09:00. 이전 통합 작업의 기준이며 이번에도 같은 SHA |

main/dev는 같지 않다. `src/lib/api/{client,remote,types}.ts`, `practice-session.tsx`, `analysis-view.tsx`에 차이가 있다. 특히 main은 상대 업로드 URL의 Backend 경로 결합/인증과 영상 동의 입력을 지원한다. dev 기반 작업을 main 위에 통째로 배포하면 이 기능을 잃을 수 있다. 반대로 dev의 코칭 화면 연결이 main에 존재한다고 가정해도 안 된다.

양쪽에서 `api/index.ts`, Backend proxy/TTS route, `analysis-polling.ts`, `coaching.ts`, `course-result-progress.ts`, `tts-practice-player.tsx`는 바이트 diff가 없었다. 별도 로컬 worktree의 canonical client/view 네 파일은 미추적 신규 파일이며 **공개 main/dev에 배포된 기능이 아니다**.

판정: 기존 계획의 dual reader·header 없는 legacy 유지 방향은 맞지만, **FE의 기존 동작까지 호환된다는 근거는 부족했다**. 아래 경계를 고정하고 실제 소비 경로를 연결해야 한다. 기존 클라이언트를 그대로 두고 frozen canonical 전체 의미를 표시하는 것은 불가능하다. 기존 API 계약은 유지하고, canonical은 별도 opt-in 계약으로 추가한다.

## 2. 실제 FE 경계와 보존할 계약

분석 요청은 `createRemoteApi("/api/backend")` → Next 서버 proxy → Backend 공개 API다. 점검한 `src`에서는 RunPod URL/8082/내부 분석 토큰 직접 사용이 발견되지 않았다. 녹음 파일 PUT은 Backend가 발급한 upload URL로 별도 전송된다. [API 진입점](https://github.com/voice-coaching/VC-FE/blob/81b27dee88ae1c09bb4f9c2581be3552ac283137/src/lib/api/index.ts), [프록시](https://github.com/voice-coaching/VC-FE/blob/81b27dee88ae1c09bb4f9c2581be3552ac283137/src/app/api/backend/%5B...proxyPath%5D/route.ts)

아래 URL은 proxy 뒤 Backend 경로다. 브라우저는 기존처럼 `/api/backend` prefix를 사용한다. callback v4의 내부 문서를 FE 공개 응답으로 그대로 반환하지 않는다.

| 기능 | 실제 요청/소비 | 전환 시 고정할 것 |
|---|---|---|
| HTTP/인증 | JSON `ApiEnvelope<T>={result,message,data,code?}`, client는 `data` 반환. Bearer + `credentials:include`, 401 refresh 및 세션 세대 검사 | raw callback JSON/`success` envelope로 교체 금지. JWT·cookie·refresh·401 처리 유지. RunPod/B2 키는 브라우저에 주지 않음 |
| 설정 조회 | `GET /api/analysis-capabilities` | upload/analysis의 CONFIGURED enum, MIME/크기/기간/동의 필드 유지. canonical 장애로 legacy까지 NOT_CONFIGURED로 바꾸거나 실시간 readiness로 재정의하지 않음 |
| 생성·업로드 | `POST /api/training-sessions`, `POST .../{s}/recordings/upload-url`, 발급 URL로 PUT, `POST .../{s}/recordings`, `PATCH .../{s}/recordings/{r}/select` | 기존 body와 `{objectKey,uploadUrl,expiresAt,requiredHeaders}` 유지. 상대 URL의 proxy 결합, Backend 대상에만 JWT 추가하는 main 동작 보존. B2 근거 저장소로 사용자 업로드 URL을 바꾸지 않음 |
| 분석·재시도 | `POST .../{s}/analyze`, `POST .../{s}/analysis/retry`, body `{accepted:true,policyRevision}` | 기존 성공 DTO `analysisId/status/requestedAt/retryCount?` 및 오류 유지. 영상 등록의 `videoProcessingConsentAccepted/videoProcessingConsentPolicyRevision`도 유지 |
| 상태 | `GET .../{s}/analysis/status`; 1초 간격, 기본 600초 대기 | `analysisId,status,stage,progressPercent,failureReason,updatedAt` 유지. status는 PENDING/PROCESSING/COMPLETED/FAILED. 여기에 ACCEPT/REJECT enum을 넣지 않음 |
| 결과 | `GET /api/analyses/{a}`, `GET .../{a}/segments?page=0&size=100`, 세션 요약 `GET .../{s}/analysis` | legacy 결과 DTO·nullable 필드·배열·페이지 유지. v4 COMPLETED의 구 API는 안전 요약/미제공 값만 반환; frozen 상태·후보는 새 조회 계약으로 분리 |
| 완료·진도·시험 | `POST .../{s}/complete` body `{totalLearningSeconds}`, course progress/complete, `POST /api/users/me/title-exams/{e}/submit` body `{analysisId}` | legacy 의미 유지. v4에서는 서버 actions/eligibility를 먼저 확인. REJECT/INCONCLUSIVE를 학습 완료로, null 점수를 시험 합격으로 바꾸지 않음 |
| 재생·TTS | 녹음 playback URL, DB 예문 `GET /api/practice-examples/{id}/audio`, 호환 `/api/tts?exampleId=...` | TTS 성공은 HTTP 200 `audio/mpeg` binary, client 한도 2,000,000 bytes. JSON/base64/오디오 URL 응답으로 교체 금지. 임의 text/voice를 받는 RunPod 직접 호출로 전환하지 않음 |

요청/DTO 근거: [remote.ts](https://github.com/voice-coaching/VC-FE/blob/81b27dee88ae1c09bb4f9c2581be3552ac283137/src/lib/api/remote.ts), [types.ts](https://github.com/voice-coaching/VC-FE/blob/81b27dee88ae1c09bb4f9c2581be3552ac283137/src/lib/api/types.ts). HTTP/TTS 근거: [client.ts](https://github.com/voice-coaching/VC-FE/blob/81b27dee88ae1c09bb4f9c2581be3552ac283137/src/lib/api/client.ts), [호환 TTS route](https://github.com/voice-coaching/VC-FE/blob/81b27dee88ae1c09bb4f9c2581be3552ac283137/src/app/api/tts/route.ts).

내부 Backend–RunPod HTTP에 동의/grant를 넣지 않는 결정은 위 **공개 FE–Backend 동의 계약**을 없애는 결정이 아니다. 기존 proxy base URL을 RunPod gateway로 바꾸면 로그인·DB·녹음·예문 API가 우회되므로 금지한다. proxy의 cookie 재작성·no-store·header 전달도 유지한다.

## 3. 발견한 실패 지점과 필요한 계획 보완

### F1. 단순 reader 교체로는 완료 부작용을 막지 못함 — 활성화 차단

main `practice-session.tsx`는 결과+segments를 `Promise.all`로 읽은 뒤 `training.complete`를 호출한다. 신규 결과와 `ANALYSIS_STATUS` 재진입 모두 해당하며, 신규 처리에서는 이어서 course progress/complete와 title exam submit도 수행한다. canonical 상태를 확인하는 분기는 없다. [일반 연습 처리](https://github.com/voice-coaching/VC-FE/blob/81b27dee88ae1c09bb4f9c2581be3552ac283137/src/components/practice-session.tsx#L401-L441), [재진입](https://github.com/voice-coaching/VC-FE/blob/81b27dee88ae1c09bb4f9c2581be3552ac283137/src/components/practice-session.tsx#L207-L242)

canonical REJECT/INCONCLUSIVE도 작업 상태는 COMPLETED라서 이 경로를 그대로 타면 complete 409가 나거나, 서버 gate가 빠져 있다면 잘못된 진도 반영이 일어난다. **Backend가 안전하게 409를 주는 것과 FE가 호환되는 것은 다르다.** canonical 결과는 별도 분기에서 `actions.canComplete` 등을 확인한 뒤 명령을 보내고, 불가 시 결과/재녹음 안내를 보여줘야 한다. 결과 표시 실패를 분석 재시도로 바꾸지 않는다. legacy 부작용 경로는 그대로 둔다.

### F2. FAILED에서 상세를 읽지 않음 — 활성화 차단

기존 `pollAnalysis`는 FAILED에서 `AnalysisFailed`를 던지고 완료 analysisId를 반환하지 않는다. 단순히 완료 후 GET만 v2로 바꾸면 SYSTEM_FAILURE/core 부재/전달 실패의 canonical 상세에 도달하지 못한다. 새 흐름에서는 요청 또는 상태 응답의 analysisId를 유지하고 FAILED에서도 별도 view를 조회한다. 대기 timeout은 재조회이지 자동 retry/analyze가 아니다. 기존 polling 함수의 반환/예외 계약은 legacy에 유지한다. [pollAnalysis](https://github.com/voice-coaching/VC-FE/blob/81b27dee88ae1c09bb4f9c2581be3552ac283137/src/lib/analysis-polling.ts)

### F3. 결과 renderer와 MFA 의미 불일치 — 활성화 차단

main의 `AnalysisView`는 score/summary/segments를 렌더링하고 `supportedCoaching`/`CoachingView`를 호출하지 않는다. dev는 해당 분기가 있으나 coaching v1·CTC millisecond/writtenRole을 전제로 한다. 두 경우 모두 frozen canonical을 기존 coaching으로 type cast하거나 MFA 초를 CTC ms 필드로 이름만 바꿔서는 안 된다. 별도 canonical renderer를 실제 entry point에 연결한다. [main 화면](https://github.com/voice-coaching/VC-FE/blob/81b27dee88ae1c09bb4f9c2581be3552ac283137/src/components/analysis-view.tsx), [dev 화면](https://github.com/voice-coaching/VC-FE/blob/d54ceae5065880ccabd8f00ffe83500ab9077dfd/src/components/analysis-view.tsx#L70-L72), [coaching 계약/seek](https://github.com/voice-coaching/VC-FE/blob/81b27dee88ae1c09bb4f9c2581be3552ac283137/src/lib/coaching.ts)

구 화면의 nullable score/summary/빈 segments 렌더링은 축소 안내에 불과하다. 이를 “구 FE가 canonical 근거를 무손실 표시함”으로 보고하지 않는다. 기존 `AnalysisResult`/`AnalysisCoaching` 타입을 canonical union으로 전역 교체하지 않는다.

### F4. 입술 화면은 별도 호출자이며 무조건 complete함 — 초기 opt-in 제외

main `lip-practice.tsx`는 capabilities·영상 동의·비디오 업로드/등록·quality 확인·선택·analyze·poll·결과/segments 조회·complete를 독립 수행한다. 일반 연습 화면만 고쳐서는 보호되지 않는다. 현재 canonical visual은 NOT_CONNECTED이므로 초기 rollout에서는 이 화면을 header 없는 기존 legacy 경로에 남긴다. 전역 HTTP client에 v4 header를 주입하지 않는다. 영상 알고리즘을 수정하거나 연결된 것처럼 표시하지 않는다. [입술 분석 호출자](https://github.com/voice-coaching/VC-FE/blob/81b27dee88ae1c09bb4f9c2581be3552ac283137/src/routes/lip-practice.tsx#L263-L394)

### F5. 이력/재진입/캐시도 reader 전환 대상 — 활성화 차단

main 이력 상세는 `myPage.getTrainingSession` 후 결과/segments/content를 `Promise.all`로 읽고 user별 localStorage bundle을 먼저 표시한다. 기본 cache 수명은 7일이고 갱신 실패 시 cached bundle을 유지한다. 신규 연습 화면만 연결하면 이력은 legacy summary로 남고 캐시의 이전 실행이 새 canonical 상태를 덮을 수 있다. [이력 상세](https://github.com/voice-coaching/VC-FE/blob/81b27dee88ae1c09bb4f9c2581be3552ac283137/src/routes/mypage/history-detail.tsx#L50-L103), [cache](https://github.com/voice-coaching/VC-FE/blob/81b27dee88ae1c09bb4f9c2581be3552ac283137/src/lib/client-cache.ts)

canonical 조회/표현은 별도 메모리 상태와 no-store를 기본으로 하고 legacy bundle에 저장하지 않는다. retry/녹음 선택/삭제/탈퇴/사용자 전환 시 이전 상태를 지우며 pending/failed에 이전 세대 core를 표시하지 않는다. v4 history/resume entry에서도 새 reader로 확인한 뒤 렌더링한다. canonical 전용 404 코드에서만 legacy 조회로 분기하고 네트워크 오류·401/403·일반 404·5xx에 cached canonical을 재노출하지 않는다. 서버의 접근 회수는 이미 구 클라이언트에 저장된 복사본의 원격 삭제 보장이 아님도 구분한다.

### F6. opt-in은 기능별·요청별이며 rollback도 reader를 남겨야 함

공개 소스에는 `X-Analysis-Result-Schema`나 `/api/v2/analyses` 호출이 없다. 새 API/header는 **추가 계약**이다. 기존 `api.training.analyze/retryAnalysis`, `api.analyses.get`, 업로드·TTS method의 시그니처/반환 타입을 바꾸지 말고 별도 canonical client에서만 명시 opt-in한다. 인증 refresh 후에도 선택한 header/body를 유지한다. 새 요청은 분석 접수만 하고 20초 기본 HTTP timeout 안에서 응답하며, 전체 추론을 동기 대기하지 않는다. proxy의 60초 timeout도 추론 전체 budget과 다르다.

초기 접수 범위는 **canonical 상태/실패/이력 처리가 연결된 독립 음성 연습 화면**으로 제한한다. 입술·시험·클래스는 legacy 유지, 각 소비 경로의 수용 후 별도 확대한다. server gate는 header뿐 아니라 세션/content/대상 적격성을 검사하며 클라이언트만 믿지 않는다. 클래스 미수용 상태의 전역 flag, 시험 점수의 가짜 대체, canonical visual을 이유로 기존 video capability 제거는 금지한다.

새 FE의 canonical retry는 v4 header를 유지한다. 구 FE의 header 없는 FAILED retry는 기존 계획대로 legacy의 **새 execution**이 될 수 있지만 기존 v4 원본은 보존한다. 이것을 현재 v4 execution의 in-place downgrade나 GPT 전송 retry로 혼동하지 않는다.

rollback은 신규 opt-in만 끄고 이미 만들어진 v4의 **FE reader와 Backend reader를 모두 유지**한다. 구 FE 바이너리 전체로 되돌리면 이력/재진입/실패 상태의 의미가 사라질 수 있다. 구 클라이언트의 legacy-shaped 조회는 안전한 축소 안내까지만 보장하고 canonical 전체 UX 호환이라고 선언하지 않는다.

## 4. 배포 수용과 이번 확인 범위

아래 FE 수용 조건은 새 canonical 기능의 **사용자 화면 활성화**에 적용한다. FE 실제 URL은 접수 off 상태의 Backend/RunPod 구현·배포(D0~D2)나 제한 키 준비의 선행조건이 아니다. 소비자 확인은 대상 release/commit·artifact와 개발자 수용 기록으로 가능하며 URL 자체를 필수로 요구하지 않는다. 기존 header 없는 legacy API는 그대로 유지한다.

각 확인은 개발자가 직접 수행한다. 이번에는 source 대조·문서 정적 확인만 했으며 테스트·추론·브라우저 자동화·운영 호출을 하지 않았다.

- main의 일반 녹음, 상대/외부 업로드, 기존 분석/retry/status/result/segments/complete, 재진입·이력, 영상 동의·입술 분석, TTS binary가 legacy대로 동작하는지 확인한다.
- canonical의 ACCEPT/REJECT/INCONCLUSIVE/FAILED/core 부재를 새 reader로 표시하며 complete/진도/시험/재시도 부작용이 eligibility를 따르는지 확인한다.
- HTTP timeout/상태 timeout/갱신 실패가 새 분석이나 GPT 중복 실행을 만들지 않는지, 동일 사용자 다른 기기·구 FE와 새 FE를 오가도 기존 API shape가 유지되는지 확인한다.
- v4 header가 opt-in된 접수에만 전달되고 proxy/401 refresh/재시도에서 보존되는지 확인한다. B2 제한 키·RunPod 내부 callback을 FE로 전달하지 않는다.
- 위 수용 전에는 계획의 D5/D6로 진입하지 않는다. 공개 main SHA를 실제 배포 SHA로 단정하지 않으며, FE 대상 브랜치와 운영 배포 SHA를 확인한 뒤 최소 변경을 이식한다.
