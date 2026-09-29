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
