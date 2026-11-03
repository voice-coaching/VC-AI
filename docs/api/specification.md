# API 명세 — intelligentAI

현재 checkout의 소스 계약이다. 운영 활성화·실제 Backend/FE 배포를 뜻하지 않는다.
신규 내부 분석은 **request v4 → result v6 → handoff v2**로 단일화한다.
[통합 명세·Backend 적용 조건](../canonical-integration/v6-unification.md),
[endpoint 목록](endpoints.md)을 함께 사용한다. 과거 v4/v5 접수·전환 기록은 이
명세에 유지하지 않는다. 외부 공개 direct와 독립 TTS·연구 API의 버전은 독립적이다.

## 공통 경계

내부 분석은 Analysis Bearer, Backend callback/handoff는 Callback Bearer 및
해당 요청의 worker identity를 사용한다. 사용자 로그인·선택 녹음 소유 확인은
Backend 책임이다. 내부 JSON은 `application/json`, identity content encoding,
최대 65,536 bytes다. 중복 key·추가 필드·잘못된 type/schema를 거부한다.
비밀값·원본 근거·private 오류를 공개 응답에 넣지 않는다.

## POST /v1/analysis-jobs

인증은 Analysis Bearer다. [request v4 schema](../contracts/runpod_analysis_request_v4.schema.json)의
`analysisRequest`만 수용한다. `schemaVersion=voice-coaching.runpod-analysis-request.v4`,
`resultSchemaVersion=voice-coaching.runpod-analysis-result.v6`,
`analysisProfile=CANONICAL_AUDIOVISUAL_20261007_V6`가 필수 고정값이다.

requestId/executionId는 UUID, analysisId/recordingId/contentId는 정수 Backend ID다.
learningFocus=PRONUNCIATION, promptRevision, scriptText/scriptSha256, audio, video, deadlineAt,
mediaPreparation을 명시한다. audio는 제한 저장소 key·MIME·SHA·byte size·duration을
갖는 WAV다. script digest, object key 제한, UTF-16 길이와 deadline도 검증한다.
임의 callback URL·bucket·grant·서명·동의 증빙은 받지 않는다.

- 음성 전용은 `video=null`이다. `mediaPreparation=null` 또는 같은 PCM에 대한 실제
  receipt를 받는다. receipt가 없으면 영상·normalization 근거를 생성하지 않는다.
- 영상 입력은 실제 video descriptor와 같은 원본의 preparation/receipt digest가
  필수다. 등록 media의 원본 SHA, PCM SHA, video SHA와 시간 출처를 검증한다.
- 두 입력 모두 result v6/handoff v2를 사용한다. video 유무가 실행 경로를 결정하며
  영상 없는 요청에 영상 전용 자산·readiness를 요구하지 않는다.

claim 후 202, 캐시에 있는 동일 요청/실행은 진행 중 202·종료 후 200이다. 응답은
requestId/executionId/workerInstanceId/status다. 동일 identity에 다른 JCS payload는
409로 거절한다. 준비·계약 불일치 503, 용량 부족 429, 미인증 401, JSON/추가 필드·
schema 위반 400/422, 크기 초과 413, 잘못된 MIME/encoding 415, 오래된 실행·만료
409다. 429/503에는 Retry-After를 제공한다. 오류 본문은 reasonCode/message/
requestId/executionId/retryable이며 최종 분석 결과는 이 응답에 넣지 않는다.

## 결과와 인계

[result v6 schema](../contracts/runpod_result_v6.schema.json)를 사용한다. COMPLETED/FAILED,
실행·worker identity, sourceIdentity, nullable canonical/selection/coaching, score,
prosodyPolicy, visual, mediaBinding이 필수다. null은 미측정이며 0점으로 바꾸지 않는다.
원본 core·prepared WAV·음소 index·시간 출처·GPT expression의 의미 검증을 유지한다.

음성 전용은 visual.status=NOT_PROVIDED, correctiveClaimsAllowed=false, score=null,
scoreKind=NONE, 빈 상세와 null coverage를 반환한다. mediaBinding의 videoSha256과
visualEvidenceSha256은 null이며 receipt가 없으면 mediaReceiptSha256도 null이다.
영상 요청은 실제 영상 상태·근거를 전달하고 원본 평가 전체를 보존한다. 공개 영상
상세는 최대 128개/256 KiB와 omittedPhoneAssessmentCount로 제한한다. 전체 결과는
1 MiB 이내이며 초과하면 원본을 보존한 RETAINED_ONLY 실패로 인계한다.

[handoff v2](../contracts/runpod_canonical_handoff_v2.schema.json)만 작성한다. CORE,
BRIDGE_RESULT, BINDING, ASSOCIATION v2와 실제 존재하는 selection/media/visual
artifact를 SHA·byte size로 묶는다. Backend 원본 검증 전 result-publish는 하지 않는다.
업로드·seal·상태 조정·재전송은 동일 bytes와 event identity를 사용한다. 과거 요청을
새 버전으로 재작성하거나 인계 재시도를 추론/GPT 재실행으로 바꾸지 않는다.

## 상태 조회와 취소

`GET /v1/analysis-jobs/{requestId}?executionId={executionId}`는 Analysis Bearer이며
UUID executionId 하나만 받는다. 응답은 identity/status/reasonCode다. 알려지지 않은
실행은 404, 같은 요청의 다른 실행과 충돌하면 409, 중복·추가 query는 422다.

`POST /v1/analysis-jobs/{requestId}/cancel`은 같은 인증과 `{"executionId":"<UUID>"}`
본문을 사용한다. 진행 중 취소 접수는 202, 종료된 실행은 200이다. 취소 응답과
프로세스 종료·슬롯 해제는 별도다. 종료 상태 캐시는 15분/1024개로 제한된다.

## Health와 readiness

내부 canonical lane 준비는 H5의 manifest/lock/prompt 등록뿐 아니라 v6의
`result`·`audioResult` 양쪽 scoring/GPT 분류가 실행 identity와 일치하는지도 확인한다.
불일치하면 `CANONICAL_HARNESS_SCHEMA_MISMATCH`로 준비를 중단하며 해당 resident는
ready가 되지 않는다. 이는 문서·identity 계약 확인이며 실제 추론/GPT 성공 검증은 아니다.
익명 Direct의 공통 artifact 검증에는 내부 v6 분류를 적용하지 않는다.

| 경로 | 인증 | 응답과 조건 |
|---|---|---|
| `/health` | 없음 | alive 200 |
| `/readyz` | 없음 | 전체 내부 의존성 준비 시 ready 200, 아니면 not_ready 503 |
| `/health/services` | Analysis Bearer | status/workerInstanceId/reasonCode/executorReady/handoffReady/coachingProvider/coachingModel/workerRevision, 200/503 |
| `/health/deployment` | Analysis Bearer | 설치 identity와 activeJobs/directJobs, Cache-Control: no-store |
| `/health/handoff` | Analysis Bearer | handoff v2, executorConfigured, admissionEnabled, supportedLearningFocuses, 공통 11개 schemaDigests, 200/503 |
| `/health/audiovisual` | Analysis Bearer | 같은 v2·11개 digest·supportedLearningFocuses, 영상 llmManifestSha256, 영상 executor/admission, 200/503 |

두 readiness 응답의 `supportedLearningFocuses`는 request v4 schema의 허용값에서
읽으며 현재 `["PRONUNCIATION"]`이다. 실행용 request/result나 11개 schema digest는
바꾸지 않는 additive 필드다. Backend는 지원 유형·계약 버전·digest·executor/admission을
함께 확인해야 한다. 이 AI 변경을 먼저 반영하고 이를 요구하는 Backend를 이어 배포한다.
readiness 선언을 실제 녹음·GPT 생성·품질 QA 성공으로 해석하지 않는다.

Backend의 `/api/internal/ai/worker-readiness/handoff`와 `/audiovisual`은 이 저장소의
구현 endpoint가 아니다. 통합을 위해 handoff v2, supported와 [공통 11개 digest](../canonical-integration/v6-unification.md)를
반환해야 한다. 영상 경로는 실제 영상 지원도 확인한다. 모델 접근 readiness와 실제
GPT 생성, API 생존과 분석 성공, 공개 direct와 내부 handoff readiness는 각각 다르다.

## 공개 direct 분석

명시 활성화된 경우 `/direct`에 mount된다. Backend 정수 identity·lease·내부 v6
결과 envelope와 구분하며 응답 버전은 `voice-coaching.direct-analysis.v1`이다.
사용자 인증·서버 토큰 없이 WAV를 접수하며, UUID jobId를 아는 호출자는 조회·취소할
수 있다. CORS는 인증 수단이 아니며 AI_DIRECT_ALLOWED_ORIGINS의 정확한 origin만
허용한다. credentials=false, GET/POST 및 Content-Type/X-Analysis-Metadata/
Last-Event-ID를 허용한다.

`POST /direct/api/v1/analyses`는 Content-Type: audio/wav 본문과 base64 JSON인
X-Analysis-Metadata를 받는다. metadata는 clientAttemptId(UUID), scriptText,
선택 contentId와 historyClaimSha256을 가진다. WAV는 mono/16 kHz/16-bit PCM,
0.5~180초, 최대 20 MiB다. admission 전 로컬 spool과 멱등성·digest를 확인한다.
202에는 jobId/status/sequence를 반환하며 오류는 `{"code":"..."}`와 4xx/5xx다.

`GET /direct/api/v1/analyses?clientAttemptId=UUID`는 유실된 접수 응답을 조회한다.
job 조회는 `/direct/api/v1/analyses/{jobId}`, SSE는 `/{jobId}/events`, 취소는
`POST /{jobId}/cancellations`다. SSE는 sequence를 id로 한 analysis event와 keepalive를
보내고 RESULT_READY/FAILED/CANCELLED에서 종료한다. 결과 공개, B2 archive와
Backend 이력 동기화는 별도 상태이며 계정 이력 연결은 기존 로그인으로 수행한다.
`/direct/readyz`는 ready/reasonCode/authentication/maximumAudioBytes와 200/503을
반환한다. 공개 direct의 준비가 내부 Backend 인계의 준비를 뜻하지 않는다.

### direct 결과의 연습 평가 범위 (2026-11-01)

신규 실행의 `result.practiceResult`는 [direct practice result v1](../contracts/direct_practice_result_v1.schema.json)을 사용한다. `POST` 접수 후 `GET`·SSE가 반환하는 동일 `result.json`과 Backend 이력 이벤트에 포함된다. 기존 결과를 재분석하거나 다시 쓰지 않으며, 이전 결과에는 필드가 없다. FE는 그 경우 기존 `score`, `decision`, `coaching.feedback`를 전체 녹음 결과로 표시한다.

| 필드 | 타입·필수·null | 의미 |
|---|---|---|
| schemaVersion | string, 필수 | `voice-coaching.direct-practice-result.v1` |
| scope | string, 필수 | `UTTERANCE`: 전체 녹음 평가 |
| scriptText | string, 필수, 1~4000자 | 접수된 원고 원문 |
| decisionStatus | string, 필수 | 기존 decision.status와 동일한 ACCEPT/REJECT/INCONCLUSIVE/SYSTEM_FAILURE |
| score | object, 필수 | 기존 공개 score 그대로. 새 채점·문장별 분배 없음 |
| feedback | string 또는 null, 필수 | ACCEPT이고 실행 실패가 없을 때 기존 GPT feedback(1~4000자), 그 외 null |
| sentenceResults | null, 필수 | 문장별 점수·판정을 생성하지 않는 계약 |
| sentenceResultsReason | string, 필수 | `UTTERANCE_ONLY` |

GOP 경로의 `coaching.items=[]`는 전체 녹음 피드백·점수 부재를 뜻하지 않는다. FE의 기존 문장 구간 모델(sequenceNo/expectedText/pronunciationScore/resultStatus 등)은 이 경로의 응답이 아니다. 빈 구간을 결과 미제공으로 표현하는 대신 검증된 전체 녹음 결과를 사용한다. 점수 임계값으로 정상/주의 판정을 만들거나 근거 없는 STT·타임스탬프를 추가하지 않는다. 내부 v6/handoff 및 frozen core/H5 계약은 변경하지 않는다.

확인 범위는 소스·schema·문서 정적 확인과 Python 컴파일이다. 실제 녹음·GPT 생성·화면 QA는 개발자가 수행해야 하며, 본 변경은 배포 완료 선언이 아니다.

## 독립 TTS: POST /v1/tts/synthesize

인증은 분석과 다른 TTS Bearer다. 본문은 최대 32,768 bytes이고 다음 **8개 필드만** 허용한다. 중복·추가 key는 거부한다.

| 필드 | 현재 계약 |
|---|---|
| schemaVersion | `voice-coaching.tts-request.v1` |
| requestId | canonical UUID |
| synthesisRevision | 배포된 synthesis identity의 revision |
| text | 비어 있지 않은 텍스트, UTF-8 최대 4500 bytes |
| language | `ko-KR` |
| voice | `ko-KR-practice-v1` |
| speakingRate | `0.92` |
| format | `mp3` |

성공 응답은 `audio/mpeg`, 헤더 `X-Request-Id`, `X-TTS-Revision`, `X-Audio-SHA256`, `X-Audio-Duration-Ms`다. revision 또는 같은 requestId의 payload 충돌은 409, 미준비는 503, queue 포화는 429, 합성 대기 초과는 504다. 단일 엔진 lock의 대기 상한은 2초, 합성 결과 대기 상한은 22초다. 동일 요청 결과 캐시는 메모리 최대 8개·120초다. gateway read timeout은 30초, AWS의 TTS upstream read는 35초이며 자동 upstream 재시도는 하지 않는다.

이번 작업에서는 실제 합성 POST를 보내지 않았다. 저장된 음성의 재생과 신규 합성을 구분한다.

## 별도 연구·loopback 검수 API

아래는 연구/검수 애플리케이션의 계약이다. 공개 운영 gateway 경로 또는 실제 자동 분석 계약으로 사용하지 않는다.

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


## 문장별 녹음 묶음 계약

[문장별 녹음·일괄 피드백 API](sentence-practice-batches.md)의 네 endpoint, 상태·실패·복구 계약을 따른다. 기존 단일 direct 결과와 내부 canonical 계약의 버전은 유지한다.
