> Current analysis API: [v5-only migration](../canonical-integration/v5-only-migration-20261004.md). Earlier v4 entries below are legacy documentation.

# API 명세 - intelligentAI

현재 운영과 별도 연구 API의 계약을 구분한다. **2026-09-30 01:38~01:44 KST 실행 소스·설정 재조사** 기준이다. 생성 요청을 새로 보내 검증한 결과는 아니며 [운영 관측](../runpod-current-pipeline.md)을 함께 읽는다.

## 현재 운영: 공통 경계

AWS Backend의 기존 base URL은 `https://ai.voice-coaching.site`, nginx upstream은 RunPod의 `https://s8hb5b0l5k4qlx-8082.proxy.runpod.net`이다. gateway는 허용 경로만 분석 8080/TTS 8083에 전달하고 나머지는 404로 거부한다. `/docs`·OpenAPI·연구/검수 endpoint는 공개하지 않는다.

분석은 `Authorization: Bearer <analysis-token>`, TTS는 별도 `<tts-token>`이다. 문서에 실제 토큰을 넣지 않는다. 분석 JSON은 `application/json`, identity content encoding, 최대 65,536 bytes이며 JSON/schema 오류와 추가 필드를 거부한다. 전체 제어 계약은 [runpod_http_control_v1.schema.json](../contracts/runpod_http_control_v1.schema.json)의 `voice-coaching.runpod-http.v1.1`을 따른다. 계약 파일명과 요청의 `schemaVersion`을 혼동하지 않는다.

## 현재 운영: health

| GET 경로 | 인증 | 응답/상태 |
|---|---|---|
| `/gateway/health` | 없음 | gateway `alive`, 200 |
| `/health` | 없음 | 분석 `{"status":"alive"}`, 200 |
| `/readyz` | 없음 | 분석 `ready` 200 / `not_ready` 503 |
| `/health/services` | 분석 Bearer | status, workerInstanceId, reasonCode, executorReady, coachingProvider, coachingModel, workerRevision; 200/503 |
| `/health/live` | TTS Bearer | 독립 TTS liveness |
| `/health/ready` | TTS Bearer | 독립 TTS readiness·synthesis revision; 준비 시 200 |

10/2 11:39~11:41 KST 추가 관측: `/health/canonical`은 분석 Bearer로 내부 8080 및
8082/AWS gateway에서 제공한다. `executorConfigured`, `admissionEnabled`, `reasonCode`,
`executionQaPerformed`, `schemaDigests`(공유 계약 9개)를 반환한다.
설정 준비 true / 접수 true / 실행 QA false를 관측했다. 신규 경로의 schema는
control v1.2의 `$defs.canonicalExecutorReadiness`다. 인증 없이 공개하는 health가 아니다.
기존 health/API 응답 shape는 변경하지 않았다.

이번 AWS 도메인의 인증 상세 health는 분석 `OPENAI/gpt-6-astra`, workerRevision `32d98e39767b56697bc7e58fb50e3e9d61432349`, TTS `melo-ko-practice-r1`로 모두 ready였다. 분석 readiness의 OpenAI 모델 접근 성공은 300초 캐시되며 실제 GPT 생성·영상 완주·품질 QA를 뜻하지 않는다.

## 현재 운영: POST /v1/analysis-jobs

- 인증: 분석 Bearer.
- 요청: 제어 schema의 `$defs.analysisRequest`. `schemaVersion=voice-coaching.runpod-analysis-request.v1`, requestId/executionId, analysisId/recordingId/contentId, learningFocus, promptRevision, scriptText/scriptSha256, audio, video(없으면 null), deadlineAt.
- media 본문을 업로드하는 API가 아니다. audio/video 객체의 제한 저장소 key·크기·SHA 등을 받고 서버가 읽는다. 임의 callback URL·bucket·grant·서명·동의 증빙은 받지 않는다.
- 성공: claim 후 `202`, 동일 요청의 완료된 실행 재접수는 캐시에 남아 있는 경우 `200`. 응답은 requestId/executionId/workerInstanceId/status이며 최종 분석은 Backend callback으로 전달한다.
- 주요 오류: `401 UNAUTHENTICATED`, `413 PAYLOAD_TOO_LARGE`, `415 UNSUPPORTED_MEDIA_TYPE`, schema 위반 `422`, 충돌/오래된 실행/만료 deadline `409`, 단일 슬롯 점유 `429 CAPACITY_EXCEEDED`, 준비/의존성 실패 `503`. 429/503은 Retry-After를 제공한다. 완전한 오류 enum은 제어 계약과 실행 소스를 따른다.

동일 requestId/executionId는 JCS payload digest로 대조한다. 새 요청이 Backend claim을 통과해야 executor에 전달된다. RunPod 상태 캐시는 메모리이며 영속 작업 상태의 정본은 Backend다.

## 현재 운영: 상태 조회·취소

- `GET /v1/analysis-jobs/{requestId}?executionId={executionId}`: 분석 Bearer. UUID executionId query를 정확히 하나만 받으며 추가 query는 422다. 응답은 식별자·status·reasonCode다. 없는 실행은 404, 같은 requestId의 다른 실행과 충돌하면 409다.
- `POST /v1/analysis-jobs/{requestId}/cancel`: 분석 Bearer. 본문은 `$defs.cancelRequest`의 `{"executionId":"<UUID>"}`다. 진행 중 취소 접수는 202, 이미 종료된 실행은 200이다.
- 취소 접수 성공과 GPU 작업/슬롯 해제는 다르다. 현재 runtime에는 180초 drain이 남아 있으며 즉시 재접수를 보장하지 않는다. 완료 캐시는 15분/1024항목 정책으로 정리된다.

## 현재 운영: POST /v1/tts/synthesize

인증은 분석과 다른 TTS Bearer다. 본문은 최대 32,768 bytes이고 다음 **8개 필드만** 허용한다. 중복·추가 key는 거부한다.

| 필드 | 현재 계약 |
|---|---|
| schemaVersion | `voice-coaching.tts-request.v1` |
| requestId | canonical UUID |
| synthesisRevision | `melo-ko-practice-r1` |
| text | 비어 있지 않은 텍스트, UTF-8 최대 4500 bytes |
| language | `ko-KR` |
| voice | `ko-KR-practice-v1` |
| speakingRate | `0.92` |
| format | `mp3` |

성공 응답은 `audio/mpeg`, 헤더 `X-Request-Id`, `X-TTS-Revision`, `X-Audio-SHA256`, `X-Audio-Duration-Ms`다. revision 또는 같은 requestId의 payload 충돌은 409, 미준비는 503, queue 포화는 429, 합성 대기 초과는 504다. 단일 엔진 lock의 대기 상한은 2초, 합성 결과 대기 상한은 22초다. 동일 요청 결과 캐시는 메모리 최대 8개·120초다. gateway read timeout은 30초, AWS의 TTS upstream read는 35초이며 자동 upstream 재시도는 하지 않는다.

이번 작업에서는 실제 합성 POST를 보내지 않았다. 저장된 음성의 재생과 신규 합성을 구분한다.

## Canonical v4 운영 연결 (명시 opt-in)

2026-10-02 서버 배포·readiness 관측 기준이다. 실제 업무 E2E 통과를 뜻하지 않는다.

- `POST /v1/analysis-jobs`의 request v2는 [별도 schema](../contracts/runpod_analysis_request_v2.schema.json)의 고정 canonical profile/result v4를 사용한다. 실행기와 같은 슬롯/claim/heartbeat에 연결했다. 최신 Backend readiness·계약 SHA가 맞지 않으면 claim 전에 `503 NOT_READY`로 차단한다. 기존 v1 body·digest·응답은 유지하며 canonical 실패를 legacy 결과로 바꾸지 않는다.
- Backend `/api/training-sessions/{sessionId}/analyze`, `/{sessionId}/analysis/retry`는 기존 body/envelope를 유지한다. `X-Analysis-Result-Schema`가 없으면 legacy, 정확히 `voice-coaching.runpod-analysis-result.v4`이면 준비된 서버에서 canonical을 선택한다. 미준비는 `503 ANALYSIS_INTEGRATION_UNAVAILABLE`, 알 수 없거나 중복된 헤더는 `400 INVALID_INPUT_VALUE`다. 초기 범위는 품질 PASS 단독 음성 PRONUNCIATION이며 영상/course/title exam은 제외한다. origin/JWT/소유 관계 검증은 유지한다.
- 인증 `GET /api/internal/ai/worker-readiness/v2`는 기존 readiness와 별도다. [제어 v1.2](../contracts/runpod_http_control_v1_2.schema.json)에 따라 준비 시 HTTP 200 ready, v1~v4 지원 목록, canonical/evidence READY 및 실제 admission을 반환한다. 미준비는 HTTP 503/NOT_READY다. `Cache-Control: no-store`와 독립 legacyReady 필드를 유지한다. 이번 관측은 canonicalSupported/admission 모두 true다.
- [result v4](../contracts/runpod_result_v4.schema.json)의 Backend reader·독립 verifier·journal/apply·ACK 경로를 활성화했다. private evidence 등록/receipt와 별도 canonical 조회는 기존 legacy DTO와 분리한다. 실제 원본 PUT/readback·callback E2E 및 FE 소비 테스트는 수행하지 않았다.

남은 경계와 담당자 인계는 [v4 구현 현황](../canonical-integration/callback-v4-plan.md#14-구현-진행-현황)을 따른다. 별도 코드 작성·컴파일은 요청 완주나 품질 QA 통과가 아니다.

## 별도 연구·loopback 검수 API

아래는 연구/검수 애플리케이션의 계약이다. 현재 RunPod 8082 gateway 경로 또는 실제 자동 분석 계약으로 사용하지 않는다.

### 연구 API의 GET /health

- 설명: API 프로세스의 기본 상태를 확인한다.
- 인증: 없음
- Response body: `{"status": "ok"}`
- Status codes: `200`

## POST /v1/oracle-attempts

- 설명: 모델 없이 정답 오류 라벨을 사용해 `DiagnosticProfile`과 근거 제한
  코칭 계약을 검증한다. 연구·테스트 전용이며 실제 자동 진단 endpoint가 아니다.
- 인증: 없음
- Request body:
  - `attemptId`: UUID
  - `targetText`: 목표 문장
  - `contentMatch`: 내용 일치 점수와 confidence
  - `aspects`: segmental, fluency, prosody, comprehensibility 점수
  - `goldIssues`: 시간 구간, 오류 유형, severity, confidence, evidence
  - `confidenceThreshold`: 선택, 기본값 0.75
- Response body: `DiagnosticProfile` schema version `1.0`
- Status codes:
  - `200`: 계약 생성 성공
  - `422`: 입력 schema 위반
- Error cases:
  - 역전되거나 음수인 시간 구간
  - 0–1 범위를 벗어난 점수
  - 허용되지 않은 evidence ID 또는 추가 필드

## POST /v1/closed-loop/oracle-attempts

- 설명: 한 번의 Oracle `DiagnosticProfile`을 만든 뒤 stateless
  코칭·재녹음 전이를 계산한다. 역시 정답 오류 라벨을 요청으로 받는
  연구·계약 검증용이며 음성 업로드, 모델 추론, 사용자 session 저장을
  제공하지 않는다.
- 인증: 없음
- Request body:
  - `attempt`: `POST /v1/oracle-attempts`와 같은 Oracle request
  - `attemptIndex`: 1부터 시작하는 현재 attempt 번호, 기본값 1
  - `maxAttempts`: 1 이상의 최대 attempt 수, 기본값 3
- Response body:
  - `profile`: `DiagnosticProfile` schema version `1.0`
  - `decision.schema_version`: `voice_coach.closed_loop_decision.v1`
  - `decision`: `action`, `reason`, `attempt_index`, `advice_id`,
    `next_state`, `reference_family`
- Status codes:
  - `200`: profile과 전이 계산 성공
  - `422`: envelope 또는 내부 Oracle request schema 위반
- 상태 보존: 없음. 각 요청은 `attemptIndex`로 전달된 현재 순번에서
  하나의 결정만 반환한다.

## 외부 API 매핑
- upstream 계약이 확인된 경우에만 내부 endpoint와 upstream 호출 관계를 기록한다.
- 현재 외부 API 매핑은 없다.

## G04 T43/I30 로컬 검수 API

이 API는 `scripts/prepare_g04_amended_audit.py serve`가 기본
`127.0.0.1:8766`에서 제공하는 전문가용 로컬 인터페이스다. 학습자용 서비스
API가 아니며 기관 인증·TLS·다중 사용자 권한 관리를 제공하지 않는다. `/`와
`/assets/*`는 정적 shell만 반환한다. 제한 metadata와 audio를 반환하거나 state를
바꾸는 `/api/v1/*` 요청에는 실행 시 발급한 `X-Review-Token` header가 필수다.

공통 privacy 계약:

- 항목 식별자는 salt된 `item_sha256`만 반환한다.
- 원본 utterance ID, 파일명, 절대·상대 경로는 응답과 state에 반환하지 않는다.
- reviewer pseudonym은 서버에서 즉시 hash하고 원문은 저장·반환하지 않는다.
- audio locator는 dataset root 내부인지 다시 검증하고 `Cache-Control: no-store`로
  반환한다.
- 브라우저 shell에는 CSP, `no-referrer`, `nosniff`, frame 거부 header를 적용한다.

### GET /api/v1/review-session

- Response: `profile`, `expected_count`, `completed_count`, `remaining_count`,
  `completion_fraction`, `reviewer_id_sha256`, `access_control_warning`
- Status: `200`, token 누락·불일치 `403`

### GET /api/v1/review-items

- Response item: `audit_index`, `item_sha256`, `split`, `l1`, `gender`,
  `proficiency`, `error_presence`, `duration_seconds`, `target_phone_count`,
  `realized_phone_count`, `reviewed`
- 원본 ID·audio path·interval은 목록 응답에 포함하지 않는다.
- Status: `200`, token 누락·불일치 `403`

### GET /api/v1/review-items/{item_hash}

- Response: 목록 item 필드와 `target_phone_intervals`,
  `realized_phone_intervals`, `automatic_phone_alignments`, `review`, `profile`
- Status: `200`, token 누락·불일치 `403`, 선택 집합 밖 hash `404`

### GET /api/v1/review-items/{item_hash}/audio

- Response: `audio/wav`, inline, `Cache-Control: no-store`
- Status: `200`, token 누락·불일치 `403`, unknown/unavailable `404`, dataset root
  탈출 locator `400`

### POST 또는 PUT /api/v1/review-items/{item_hash}/review

공통 request는 `alignment_correct`, `maximum_boundary_error_ms`,
`incorrect_interval_count`, `notes`를 받는다. 빈 선택은 허용하지 않으며 추가
field는 거부한다.

- T43은 `annotation_fidelity`, `root_cause`, `resolution`도 필수다. 문제 후보
  원인·조치 판정이며 accuracy 분모로 사용하지 않는다.
- I30은 위 T43 전용 세 field를 반드시 `null`로 보내고 phone interval 오류 수로
  accuracy를 계산한다.
- `POST`는 최초 저장만 허용하고 기존 판정이면 `409`다. `PUT`은 기존 판정을
  의도적으로 교체한다. 두 동작 모두 state 전체를 원자 저장한다.
- Response: `item_sha256`, hash reviewer와 submission을 포함한 `review`, 갱신된
  `status`
- Status: `200`, token 누락·불일치 `403`, unknown hash `404`, 중복 POST `409`,
  profile·field·notes·interval 계약 위반 `422`


## Canonical v5 handoff (2026-10-04)

[Contract, ownership, worker states and rollout](../canonical-integration/handoff-v5-20261004.md). RunPod transfers immutable originals to Backend PostgreSQL; result verification/commit precedes the independent B2 archive outbox. v4 receipt semantics remain unchanged. Implementation is not deployment or inference QA.


## Canonical capability와 결과 처리 보완 (2026-10-04)

### GET /api/analysis-capabilities/canonical

사용자 로그인 Bearer 인증, 요청 body/path/query 없음. 200은 표준 ApiResponse envelope의 data이며 Cache-Control은 no-store다. 미인증은 기존 인증 계층의 401/403이다. 설정/점검 중에도 조회 자체는 200이고 admissionEnabled=false, resultSchemas=[]로 표현한다. 기존 /api/analysis-capabilities의 CONFIGURED는 설정 존재 의미를 유지한다.

| 필드 | 타입/필수/null | 의미 |
|---|---|---|
| capabilityVersion | string/필수/non-null | voice-coaching.analysis-capabilities.v1 |
| analysisProfile | string/필수/non-null | CANONICAL_HANDOFF_20261004_V5 |
| resultSchemas | string[]/필수/non-null | 접수 가능 시 voice-coaching.runpod-analysis-result.v5 하나, 불가 시 빈 배열 |
| admissionEnabled | boolean/필수/non-null | worker/readiness/용량/maintenance 접수 조건. 개별 사용자 권한을 대신하지 않음 |
| scopes | object/필수/non-null | STANDALONE_AUDIO, COURSE, TITLE_EXAM, VIDEO 네 키 |
| scopes.*.supported | boolean/필수/non-null | 해당 목적의 구현 지원 여부. 현재 단독 음성만 true |
| scopes.*.reasonCode | string/필수/nullable | 단독 음성은 null, 미지원은 각각 COURSE_ANALYSIS_UNSUPPORTED, TITLE_EXAM_ANALYSIS_UNSUPPORTED, VIDEO_ANALYSIS_UNSUPPORTED |

FE는 지원 범위를 녹음/업로드 전에 확인하고, 위 필드가 빠진 과거 응답은 전체 지원으로 추정하지 않는다. 구버전 응답은 클라이언트 ANALYSIS_CAPABILITIES_UNAVAILABLE(503)로 안내한다. 미지원 목적은 반환 reasonCode로 안내하고, 접수 불가는 ANALYSIS_INTEGRATION_UNAVAILABLE로 안내한다. 이들은 FE의 로컬 오류 분류이며 capability GET의 HTTP 200을 서버 409/503으로 바꾼 것은 아니다. 실제 analyze/retry의 소유·선택·동의·용량·접수 검사는 계속 Backend가 수행한다.

### 기존 GET /api/v3/analyses/{analysisId} 및 complete/upload-url

- request/profile/result/view 버전은 v3/v5/v5/view-v2 그대로다. JSON schema 9개 바이트는 변경하지 않는다.
- v5 COMMITTED handoff의 검증과 현재 request/execution/녹음/소유/원본 hash가 일치한 결과를 학습 완료·재녹음의 저장 증명으로 사용한다. v5에 retainedEvidence를 요구하지 않는다. v4 보관 자료는 기존 receipt 증명을 유지한다.
- 학습 완료는 ACCEPT/INLINE/feedbackDeliveryAllowed와 기존 adapter/generation 조건이 필요하다. REJECT/INCONCLUSIVE는 적격 결과의 재녹음만 허용한다. 미검증/SAVING/다른 실행은 완료 불가다. B2 archive는 독립 처리다.
- 결과 표시 권한과 actions.canComplete는 별개다. canComplete=false인 유효 ACCEPT 결과를 일반 AI 실패로 표시하지 않는다. FAILED, REJECT, INCONCLUSIVE의 안내를 구분한다.
- 선공개 뒤 서버 재시작으로 동일 attempt가 일시 PROCESSING/NONE이 되어도 FE는 잠정 결과를 읽기 전용으로 보존하며 상태를 다시 조회한다. 전체 identity가 바뀌거나 인증/소유가 무효면 보존하지 않는다.
- 저장 조회는 한 번에 최대 120초, 요청별 15초, 5초 간격으로 제한한다. 지연 후 사용자가 조회만 다시 시작할 수 있고 자동 analyze/retry 제출은 없다.
- complete 요청의 totalLearningSeconds는 새 페이지 recorder의 0초가 아닌 현재 선택된 서버 녹음 durationMs로 계산한다. duration이 없으면 임의의 1초를 만들지 않는다.
- 실행 상태 조회로 확인한 RunPod INTERNAL_ERROR/DEPENDENCY_UNAVAILABLE terminal FAILED는 Backend failure_code=runpod_execution_failed, 공개 serviceFailure={origin:RUNPOD, code:CANONICAL_EXECUTION_FAILED, stage:EXECUTION}으로 구분한다. UNKNOWN/404/409/5xx만으로 terminal 실패를 만들지 않는다.

## 2026-10-07 음성·영상 v6 (기본 비활성, 미배포)

기존 음성 v5 계약을 유지하며, 위 capability 표의 VIDEO 미지원 상태는 v6 readiness와 admission이 열린 경우에만 확장된다. 그때 `resultSchemas`에 v6를 추가하고 VIDEO supported를 제공한다. 음성 요청은 v5, 영상 요청은 v6다. 위 v5의 schema 바이트 불변 설명은 당시 변경 범위에 관한 기록이며 이번 후보는 새 frozen tuple 등록과 선택적 MFA 확장으로 공통 schema digest를 함께 갱신한다.

### POST /api/training-sessions/{sessionId}/analyze 및 /analysis/retry

로그인 및 session/선택 recording 소유 확인. `sessionId`는 필수 정수 path, query 없음. body는 기존 `accepted`(필수 boolean, 동의 true), `policyRevision`(필수 nonempty string, 최대 100자)를 유지한다. 영상은 `X-Analysis-Result-Schema: voice-coaching.runpod-analysis-result.v6`, 음성은 v5. 중복·쉼표 결합·알 수 없는 schema 헤더는 거부한다. MP4를 body로 다시 보내지 않으며 같은 등록 recording의 immutable preparation을 사용한다.

200의 표준 ApiResponse에는 기존 분석 접수 DTO가 들어간다. analyze의 `analysisId`(정수), `status`(상태 문자열), `requestedAt`(시간)은 모두 필수다. retry는 여기에 필수 정수 `retryCount`가 추가되는 기존 DTO를 유지한다. 미인증/소유 실패는 기존 401/403, 입력 오류는 400, 지원하지 않는 recording/profile 또는 preparation 불일치는 422(`ANALYSIS_MEDIA_BINDING_INVALID` 포함), 접수 준비 미완료는 503이다. 기존 상태 충돌/동의 정책 검증은 그대로 적용된다.

### GET /api/v3/analyses/{analysisId}

로그인 및 analysis 소유 확인. 정수 path 외 query/body 없음. 200의 v6 public view는 `schemaVersion=voice-coaching.canonical-analysis-view.v3`, `analysisProfile=CANONICAL_AUDIOVISUAL_20261007_V6`다. 기존 v5는 view-v2를 유지한다. 기존 작업/결정/실패/음성 채점/피드백/actions/persistence 필드의 의미를 유지하며 `canonicalAnalysis.visual`만 확장한다. 원시 CORE, 얼굴 좌표, receipt, 파일 경로, 비밀값은 공개하지 않는다.

`visual`은 status, correctiveClaimsAllowed(false), nullable score, scoreKind(NONE/CALIBRATED), coverage, phoneAssessments, reasonCodes, alignmentScope, unresolvedExpectedIndices, omittedPhoneAssessmentCount가 필수다. 전체 상태·음소/관찰 하위 필드의 타입·enum·null·범위는 [v6 schema의 visual 정의](../contracts/runpod_result_v6.schema.json)를 따른다. 공개 음소 상세는 앞 128개 및 256KiB로 제한하며 생략 수를 제공한다. 미확정 음소도 coverage 분모에 남긴다. null은 0이 아니며 연구용 유사도·입술 점수를 음성 점수에 합산하지 않는다. 원본을 전달받아 검증한 결과만 SAVING/RETRYING으로 공개하며 저장 완료는 SAVED다. 조회 실패/소유 및 미발견 오류는 기존 GET 계약을 유지한다.

### GET /health/audiovisual (AI)

기존 Analysis Bearer 인증, path/query/body 없음. body는 `contractVersion`(handoff-v2 string), `executorConfigured`/`admissionEnabled`(boolean), `llmManifestSha256`(pin string), `schemaDigests`(5개 파일 이름→SHA map)가 필수다. 설치 및 자산 확인 시 200, executor 미준비 시 동일 구조의 503. 미인증은 기존 서버 인증 오류다. liveness나 실제 영상/GPT 품질 통과를 뜻하지 않는다.

### GET /api/internal/ai/worker-readiness/audiovisual (Backend)

기존 Callback Bearer 인증, path/query/body 없음. 200 body의 필수 필드는 `contractVersion`(handoff-v2 string), `supported`(boolean), `schemaDigests`(5개 파일 이름→SHA map)다. `supported`는 worker 설정·기존 handoff 지원·V43·offline verifier의 v2 core/H5/lock pin 확인 결과이며 사용자 admission과 다르다. 비활성/미설치는 supported false로 표현한다. admission에는 별도로 AI 설치와 spool 예산을 확인한다. 인증 누락/오류는 401이다. 실제 추론이나 품질 확인은 수행하지 않는다.

## 2026-10-05 직접 음성 분석 (별도 서비스, 미배포)

사용자 결정으로 FE가 RunPod에 인증/게이트웨이 없이 raw WAV를 제출한다. direct-analysis.v1 UUID 계약은 AWS numeric ID 기반 v5 요청을 대체하여 위장하지 않는다. 일반 독립 연습에서만 기능 flag로 선택하며 기존 세션·과정·시험은 기존 계약을 유지한다.

원자적 로컬 결과 공개 뒤 SSE 결과를 FE에 제공하고, B2 및 AWS direct history 저장은 별도 worker가 재시도한다. AWS 계정 이력 연결은 결과 표시 후 기존 로그인으로 처리하며 RunPod 접수에 영향을 주지 않는다. Native DRAFT allowlist가 비어 있어 운영 활성화는 미완료다. 전체 입력·응답·실패·내구성 계약은 [직접 분석 구현 기록](../canonical-integration/direct-ingress-implementation-20261005.md)을 따른다.
