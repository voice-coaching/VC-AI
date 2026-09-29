# AWS–RunPod HTTP 계약 확정 회신

> 아래는 2026-09-13 제어 v1.1/결과 v1 인계와 당시 관측이다. 2026-09-28 실제 배포는 제어 v1.1을 유지하면서 GPT coaching 완료 결과 v3를 생성한다. provider·영상 관찰·null 점수 및 실제 소스 근거는 [배포 파이프라인](runpod_deployed_pipeline_20260928.md)을 따른다. 아래 과거 Clova·포트·readiness 관측을 현재 상태로 해석하지 않는다.

계약 revision: `voice-coaching.runpod-http.v1.1` · 작성일: 2026-09-13.

이 문서는 AWS 담당자의 PR #47 점검 회신에 대한 **RunPod 측 구현 기준 확정본**이다. 양쪽 구현·배포 완료를 뜻하지 않는다. 기존 제안과 충돌하면 이 문서와 아래 JSON Schema를 기준으로 PR #47과 RunPod 구현을 맞춘다. 요청·결과의 `schemaVersion`은 각각 기존 v1 이름을 사용하고, 이번에 확정한 세부 규칙은 위 계약 revision으로 식별한다.

- [요청·제어·오류 JSON Schema](contracts/runpod_http_control_v1.schema.json)
- [결과·음소·영상 JSON Schema](contracts/runpod_result_v1.schema.json)
- [AWS 전체 작업 요청서](aws_runpod_http_work_order_20260913.md)

## 1. AWS 질문에 대한 결정

| 질문 | 확정 내용 |
|---|---|
| worker-readiness 포함 여부 | **포함.** Backend의 인증된 내부 API 준비 확인용이며 RunPod readiness를 다시 호출하지 않음 |
| RunPod 상태 조회 GET 포함 여부 | **포함.** RunPod 서버와 Backend client 모두 구현. 접수 응답 유실·복구 판단의 보조 자료이며 DB 완료 상태를 대체하지 않음 |
| claim payload digest 필수 여부 | **필수.** `requestPayloadSha256`으로 Backend가 저장한 요청 본문과 비교 |
| leaseExpiresAt 계산 주체 | **Backend.** DB 상태 검증 시각 기준 90초, 다만 작업 deadline을 넘지 않음 |
| heartbeat 시각 | **Backend 수신 처리 시각.** RunPod의 `heartbeatAt`을 입력으로 받지 않음 |
| 기본 시간 | **heartbeat 15초 / lease 90초 / 전체 deadline 15분** |
| callback 토큰 | 양쪽 서버 인증에서 constant-time 비교. 사용자 JWT 필터와 분리 |
| result 하위 DTO | 첨부 Schema로 확정. 상태는 COMPLETED/FAILED만, beta/debug/STT/segments는 제외 |
| result의 worker 식별 | **`workerInstanceId`를 필수 추가.** 신규 결과 반영 때 현재 claim 소유 worker와 비교 |
| 분석 내부 포트 | **8080, `0.0.0.0:8080` HTTP로 확정. 현재 미배포** |
| Nginx 전환 | **아직 진행하지 않음.** 현재 8080 서버·Expose가 없으므로 preparing 503 유지 |

동의 증빙·정책 revision·요청별 권한 grant·서명은 넣지 않는다. HTTP 입력을 통과시키기 위한 가짜 grant 생성도 제거한다. 로그인·녹음 소유 관계, 방향별 서버 인증, canonical 파일 SHA 검증은 유지한다.

## 2. 실제 관측과 담당자 회신의 구분

| 구분 | 내용 | 근거 시각·출처 |
|---|---|---|
| AWS 담당자 회신 | 배포 SHA `33efef6ec90970003d1e8b66bcdb2a097038ed0f`, Actuator 200, alpha-backend active/running, DB V15 | 이번 사용자 전달 내용. EC2 내부·DB는 직접 조회하지 않음 |
| Backend 소스 | 최신 develop `33efef6ec90970003d1e8b66bcdb2a097038ed0f` | GitHub 읽기 전용 조회. 소스 SHA가 실제 JAR SHA임을 별도로 입증한 것은 아님 |
| AWS 공개 API | `/v3/api-docs` 200, 공개 spec에 `/api/internal/ai` 경로 없음 | 직접 GET, 2026-09-13 10:28:39 UTC / 19:28:39 KST. spec 부재만으로 숨겨진 내부 경로의 존재 여부를 단정하지 않음 |
| AI 고정 도메인 | `/health`, `/readyz` 모두 503 | 직접 GET, 2026-09-13 10:28:39 UTC |
| Pod 관리 API | ID `urwnuesv7o4fv6`, desiredStatus RUNNING, ports는 `22/tcp`뿐 | 직접 GET, 2026-09-13 10:28:46 UTC |
| Pod 내부 | 8080 TCP listener 없음. Clova 8000 `/health` 200 ready, 전체 서비스 503 | SSH 읽기 전용 조회, 2026-09-13 10:29:52 UTC / 19:29:52 KST |
| 배포된 API | `/health`, `/health/services`, `/v1/chat/completions`만 확인 | 같은 시각 Clova OpenAPI. `/v1/analysis-jobs`는 미배포 |
| Pod 배포 revision | `6618a396fb4d1c0d69ab76402bc2450a253be248` | 같은 시각 deployment metadata·health |
| RunPod HTTP 설정 | HTTP 전용 env JSON 관리 설정 및 `/root/.config/intelligentai/analysis-http.env`, `/run/intelligentai/analysis-http.env` 없음 | 관리 API의 설정 존재 여부와 SSH 파일 존재 여부만 조회 |

### Nginx 담당자에게 전달할 RunPod 정보

| 요청 항목 | 현재 확정 가능한 값 |
|---|---|
| 실제 Pod ID | `urwnuesv7o4fv6` |
| 사용할 내부 포트 | `8080` HTTP. 현재 listener 없음 |
| HTTP Expose | **미설정.** 현재는 `22/tcp`만 노출 |
| 해당 Pod의 예정 Proxy URL | `https://urwnuesv7o4fv6-8080.proxy.runpod.net` |
| 사용 가능한 upstream 여부 | **아직 아님.** 위 URL을 Nginx에 활성화하지 않음 |
| 배포 후 분석 접수 | `POST /v1/analysis-jobs` |
| 배포 후 liveness/readiness | `/health` 200 alive, `/readyz` 200 ready 또는 503 not_ready |

RunPod Proxy 주소는 Pod ID와 HTTP Expose 포트로 구성된다. 실제 배포 때 Pod ID를 다시 확인하고, 8080 listen·Expose·Proxy `/readyz`·업무 인증을 확인한 뒤 사용 가능한 upstream으로 다시 인계한다. [RunPod 공식 포트 안내](https://docs.runpod.io/pods/configuration/expose-ports)

서비스 토큰 값은 출력·공유하지 않았다. 관리 API에서 분석용 토큰 이름이 없는 것은 확인했지만 다른 위치의 값이나 AWS와의 일치 여부까지 확인한 것은 아니다. **RunPod HTTP 설정 주입 완료로 보고할 수 없는 상태**다. AWS 토큰·저장소 credential은 담당자가 비공개 설정 경로와 적용 여부만 회신하면 된다.

## 3. 공통 JSON·인증 규칙

- 업무 요청·응답은 `application/json`, UTF-8, **DTO 자체를 직접 반환**한다. 내부 API에서 기존 `{result,message,data}` 외피는 사용하지 않는다. 사용자 공개 API의 기존 외피는 유지할 수 있다.
- 미정의 필드와 중복 JSON key는 거절한다. Schema의 필수 키를 전부 보내며 미제공 값은 명시적 `null`로 표현한다. 예: video 없음은 `video:null`, video duration을 모르면 `durationMs:null`이다.
- UUID는 소문자 하이픈 표기다. `analysisId/recordingId/contentId`는 양의 JSON 정수이며 Java Long에 매핑한다. JCS 정밀도를 위해 상한은 `9007199254740991`이다.
- UTC 시각은 `YYYY-MM-DDTHH:mm:ss.SSSZ`로 통일한다. datetime·UUID format과 실제 날짜 유효성도 애플리케이션에서 검증한다.
- 문자열은 임의 trim·Unicode NFC/NFKC·개행 변환을 하지 않는다. 잘못된 Unicode surrogate는 거절한다. 기존 Java 길이 제한과 일치하도록 promptRevision 100, scriptText 10,000, selectedPhone 16 등의 제한은 UTF-16 code unit 길이도 검사한다.
- 접수 body 상한은 65,536 bytes, 결과 body 상한은 1,048,576 bytes다. 제어 요청은 65,536 bytes 이내다. 디코딩된 body의 상한을 수신 경계에서 적용하고 요청 압축은 이번 계약에서 받지 않는다.
- Backend → RunPod는 `AI_ANALYSIS_API_TOKEN`, RunPod → Backend는 별도의 `AI_ANALYSIS_CALLBACK_TOKEN`을 Bearer 헤더로 전달한다. 계정 관리 키를 재사용하지 않는다.
- 두 수신 경계 모두 서버 토큰 인증을 적용한다. Java는 `MessageDigest.isEqual`, Python은 `hmac.compare_digest` 같은 constant-time 비교 함수를 사용하고 일반 문자열 동등 비교로 인증하지 않는다. API key·Authorization·대본·원문을 오류나 로그에 복제하지 않는다.

## 4. 본문 digest 확정

`requestPayloadSha256 = lower_hex(SHA-256(UTF-8(JCS(analysisRequest))))`다. 대상은 **Backend가 outbox에 고정하고 RunPod가 실제 수신한 `/v1/analysis-jobs` JSON 객체 전체**다. claim body나 Authorization 헤더를 해시하지 않는다. `requestPayloadSha256`는 원 요청 body에는 넣지 않고 claim body에만 넣어 자기 참조를 피한다.

JCS는 RFC 8785를 사용한다. 단순히 키 정렬 옵션을 켠 JSON serializer를 JCS와 같은 것으로 취급하지 않는다. 숫자·Unicode 처리까지 맞는 구현을 양쪽에서 사용한다. 명시적 null 규칙을 적용한 요청을 고정한 뒤 해시하며 수신 후 임의로 필드를 추가·제거해 맞추지 않는다. [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785.html)

Backend는 `(requestId, executionId)`에 연결된 요청 본문과 digest를 저장한다. RunPod가 보낸 digest를 그대로 저장하고 통과시키는 구현은 금지한다. 동일 실행의 다른 본문은 `409 PAYLOAD_DIGEST_MISMATCH`다. 새 executionId를 배정하면 원래 논리 작업의 deadline을 유지한 새 본문과 digest를 함께 만든다.

`scriptSha256`은 **정규화하지 않은 scriptText의 UTF-8 바이트**에 대한 SHA-256이다. 파일 SHA는 canonical 파일 바이트에 대한 SHA다. 세 digest의 대상을 혼동하지 않는다. 결과 중복 비교도 고정된 result 객체의 JCS SHA-256으로 하며 eventId·workerInstanceId를 포함한 본문 전체가 동일해야 한다.

## 5. 경로와 Schema 연결

표의 이름은 JSON Schema `$defs` 이름이다. 제어 Schema와 결과 Schema는 각각 독립 파일이며 `additionalProperties:false`와 필수/null 규칙을 함께 적용한다.

| 방향·API | 요청 | 정상 응답 |
|---|---|---|
| Backend → RunPod `POST /v1/analysis-jobs` | 제어 `analysisRequest` | 신규·동일 실행 진행 중 202 `jobAccepted`; 캐시에 있는 동일 종료 실행은 200 `jobAccepted` |
| Backend → RunPod `GET /v1/analysis-jobs/{requestId}?executionId=...` | 두 UUID 필수 | 200 `jobStatus` |
| Backend → RunPod `POST /v1/analysis-jobs/{requestId}/cancel` | `cancelRequest` | 취소 처리 중 202, 이미 종료된 동일 실행 200 `cancelResponse` |
| RunPod → Backend `POST /api/internal/ai/analyses/{analysisId}/claim` | `claimRequest` | 신규·동일 claim 200 `leaseResponse` |
| RunPod → Backend `POST /api/internal/ai/analyses/{analysisId}/heartbeat` | `heartbeatRequest` | 갱신 성공 200 `leaseResponse` |
| RunPod → Backend `POST /api/internal/ai/analyses/{analysisId}/result` | 결과 `result` | 반영 또는 동일 중복 200 `resultAck` |
| RunPod → Backend `GET /api/internal/ai/worker-readiness` | 서버 토큰 | 200 ready 또는 503 not_ready의 `workerReadiness` |
| 공개 RunPod `GET /health` | 없음 | 200 `health` |
| 공개 RunPod `GET /readyz` | 없음 | 200 ready 또는 503 not_ready의 `readyz` |

worker-readiness는 서버 인증·내부 처리 경로·DB 접근과 계약 revision의 준비 확인이다. 사용자 녹음 처리·ffmpeg·RunPod 추론 품질의 성공을 뜻하지 않는다. 응답 `contractVersion`은 `voice-coaching.runpod-http.v1.1`이다. RunPod readiness를 호출하는 순환을 만들지 않는다.

RunPod GET은 해당 프로세스가 아는 실행 상태만 반환한다. 조회에 없으면 `404 UNKNOWN_EXECUTION`, 보유한 다른 실행 세대와 충돌하면 `409 STALE_EXECUTION`이다. Pod 재기동 뒤 404라는 이유로 Backend DB 작업을 완료·실패·미존재 처리하지 않는다. Backend는 자신의 claim·lease·terminal 상태를 먼저 확인한다.

## 6. claim·heartbeat·deadline 상태 규칙

### 단일 시간 기준

Backend는 DB 행 잠금/조건부 갱신 시 사용할 현재 시각 `serverTime`을 한 번 정한다. 여러 Backend 인스턴스가 일관된 DB 시간 기준을 사용하도록 한다. RunPod의 벽시계 값은 작업 원장의 시간 근거로 사용하지 않는다.

```text
deadlineAt = Backend의 논리 분석 시도 생성 시각 + 15분
leaseExpiresAt = min(serverTime + 90초, deadlineAt)
heartbeat 저장 시각 = serverTime
```

deadline은 outbox 대기·실행·결과 전달을 포함한다. 같은 requestId에서 executionId를 재배정해도 연장하지 않는다. 사용자의 명시적 새 분석 시도는 새 requestId와 새 deadline을 갖는다. Pod 기동 대기는 접수 활성화 전에 끝내며 새 작업을 무기한 대기시키지 않는다.

### claim

1. 경로 analysisId, requestId, executionId, 저장된 requestPayloadSha256, 작업 취소·삭제·terminal·deadline을 확인한다.
2. 미점유인 현재 실행만 해당 workerInstanceId가 최초 점유한다. workerInstanceId는 RunPod 제어 프로세스가 시작할 때 발급한 UUID다.
3. 같은 실행·본문·worker의 살아 있는 claim 재전송은 200을 반환한다. **claim 재전송은 lease를 연장하지 않고 현재 leaseExpiresAt을 반환**한다. 연장은 heartbeat만 수행한다.
4. 다른 worker가 점유했으면 `WORKER_CONFLICT`, 만료됐으면 `LEASE_EXPIRED`다. 동일 실행을 다른 프로세스가 자동 인수하지 않는다.
5. claim 성공 transaction이 commit된 뒤 응답한다. RunPod는 성공 확인 전 파일 읽기·모델 추론을 시작하지 않는다.

### heartbeat와 종료

- body는 requestId·executionId·workerInstanceId만 받는다. `claimedUntil`과 `heartbeatAt`은 미정의 필드로 거절한다.
- 현재 실행·같은 owner·미만료 lease·미종료 작업·deadline을 원자적으로 확인한다. claim 없는 heartbeat는 `CLAIM_REQUIRED`다.
- 성공 시 Backend 시간으로 갱신하여 serverTime과 leaseExpiresAt을 응답한다. 만료 lease나 취소 작업을 heartbeat로 되살리지 않는다.
- RunPod는 15초마다 heartbeat를 보내며 추론과 통신 루프가 서로 막히지 않게 한다. 응답을 받지 못한 갱신은 성공으로 간주하지 않는다.
- RunPod는 응답의 serverTime과 leaseExpiresAt의 차이에서 왕복 지연·안전 여유를 뺀 값을 로컬 단조 시계에 적용한다. 두 서버의 벽시계가 같다고 가정하지 않는다.
- lease/deadline을 잃으면 실행·결과 반영을 중단하고 정리한다. 같은 executionId를 재사용하지 않는다. Backend sweeper가 terminal 실패 또는 남은 deadline 안의 새 실행 배정을 결정한다.
- 결과 전달 대기에도 lease를 유지한다. 이미 반영한 동일 결과 재전송은 후술한 중복 규칙으로 응답한다.

### 시간·재시도 기본값

| 설정 | 확정 시작값 |
|---|---|
| 실행 슬롯 / 로컬 대기 | 1 / 0 |
| heartbeat / lease / 전체 deadline | 15초 / 90초 / 15분 |
| 서버 간 HTTP connect / 한 시도 전체 timeout | 3초 / 10초 |
| 실행 한계 안전 여유 | 최소 2초. 남은 lease/deadline이 짧으면 요청 timeout도 줄임 |
| 일시 장애 backoff | 1, 2, 4, 8초에서 시작, 최대 15초 + jitter. Retry-After 존중 |

재시도는 위 스케줄뿐 아니라 남은 lease/deadline을 항상 상한으로 적용한다. lease 유지가 필요한 구간에서는 예정된 heartbeat를 지연시키지 않는다. 정해진 한계에 도달하면 무한 재전송하지 않는다. Nginx가 업무 POST를 임의로 재전송하지 않게 한다.

## 7. 결과 하위 DTO와 검증

정확한 필수 키·타입·null·상수·길이는 결과 Schema가 기준이다. 기존 모델에서 이미 생성되는 근거를 보존하며 아래 조건을 서버에서 추가 검증한다.

| 상태 | 의미 |
|---|---|
| COMPLETED / COACHING_READY | summaryFeedback와 pronunciationEvidence 필수. visualSupplement는 유효할 때만 제공 |
| COMPLETED / COMPLETED_NO_ISSUE | summaryFeedback·pronunciationEvidence·visualSupplement 모두 null |
| FAILED | outcome·summaryFeedback·pronunciationEvidence·visualSupplement·audioSha256 null. failureCode·failureReason·workerRevision 필수, pipelineRevision은 확인 가능할 때 제공 |

`pronunciationEvidence`의 하위 필드는 schemaVersion, selectedPhone, selectedExpectedIndex, selectedStartMs, selectedEndMs, detectorScore, operatingThreshold, scoreSemantics, evidenceState다. start/end는 둘 다 null이거나 `0 <= start < end <= audio.durationMs`다. `0 <= operatingThreshold <= detectorScore <= 1`을 확인한다. 이 검출 순위값을 정답 확률이나 사용자 점수로 바꾸지 않는다.

`visualSupplement`는 schemaVersion, selectedExpectedIndex, evidenceRelation, approvedClaimId, rendererKey, upstreamPhoneAnchorRef, supplementSha256의 7개 필드다. evidenceRelation은 `supports_upstream`, 선택 index는 음소 근거와 같아야 한다. 영상 입력이 없는데 영상 보완을 반환하면 거절한다. supplementSha256은 내부 전체 영상 근거의 digest이므로 축약된 HTTP DTO를 다시 해시하여 비교하지 않는다. RunPod는 축약 전에 전체 근거를 검증한다.

Backend는 path/body 분석 ID·녹음·현재 실행·claim owner·deadline/lease, 완료 결과의 canonical audio SHA를 대조한다. score 비교·시간 구간·같은 index·DB 관계는 JSON Schema만으로 끝나지 않는다. 서버 애플리케이션 검증이 필수다.

분석 failureCode는 다음 8개로 확정한다. 인증·취소·lease·deadline·callback 전달 실패는 이 결과를 새로 합성하는 데 사용하지 않고 작업 제어 오류로 처리한다.

```text
audio_metadata_mismatch
audio_integrity_mismatch
restricted_audio_unavailable
audio_decode_unsupported
analysis_focus_not_supported
seungun_backend_unavailable
seungun_detector_failed
analysis_execution_failed_closed
```

알 수 없는 분석 예외는 마지막 generic 코드와 안전한 고정 문장으로 축약한다. Clova 생성 실패 시 기존 검증 문장 fallback, 영상 분석 실패 시 유효한 음성 결과 보존 규칙은 유지한다.

## 8. 오류 reason code와 결과 중복

업무 오류 body는 직접 `{reasonCode,message,requestId,executionId,retryable}`로 반환한다. 식별자를 안전하게 확정하지 못했으면 null이다. 사람이 읽는 message를 분기 조건으로 사용하지 않는다. 검증 실패에 대본·토큰·원문을 포함하지 않는다.

| HTTP | reasonCode | 처리 |
|---|---|---|
| 400 | INVALID_JSON | JSON 구문·중복 key 오류, 재시도 종료 |
| 401 / 403 | UNAUTHENTICATED / FORBIDDEN | 인증·허용되지 않은 서버 호출, 재시도 종료 |
| 404 | TARGET_NOT_FOUND / UNKNOWN_EXECUTION | Backend 대상 삭제·미존재 / RunPod 로컬 미보유. 둘을 구분 |
| 409 | STALE_EXECUTION, PAYLOAD_DIGEST_MISMATCH, WORKER_CONFLICT, CLAIM_REQUIRED | 세대·본문·소유권·선claim 충돌, 같은 요청 자동 재시도 종료 |
| 409 | LEASE_EXPIRED, DEADLINE_EXCEEDED, ANALYSIS_CANCELLED, ANALYSIS_TERMINAL | 실행 권한 수명 종료. Backend가 복구/새 시도를 결정 |
| 409 | RESULT_EVENT_CONFLICT, RESULT_ALREADY_FINALIZED | 같은 eventId의 다른 본문 / 다른 eventId로 이미 종료된 결과 덮어쓰기 |
| 413 / 415 / 422 | PAYLOAD_TOO_LARGE / UNSUPPORTED_MEDIA_TYPE / VALIDATION_FAILED | 크기·형식·필드·결과 의미 오류, 재시도 종료 |
| 429 | CAPACITY_EXCEEDED / RATE_LIMITED | 슬롯 포화 / 호출 제한, Retry-After 필수 |
| 503 | NOT_READY / DEPENDENCY_UNAVAILABLE | 준비 미완료 / 일시적 DB·연결 실패, 기한 안 재시도 |
| 500 | INTERNAL_ERROR | 안전한 일반 오류. 같은 본문으로 기한 안 재시도 |

error.retryable은 429/500/503일 때 true, 나머지는 false다. `/readyz`와 worker-readiness의 503은 표준 준비 body를 사용한다. 프록시의 HTML 오류나 응답 없는 네트워크 실패는 구조화된 업무 오류로 꾸미지 않고 별도 전송 오류로 처리한다.

신규 결과는 모든 현재 실행 검증을 통과한 뒤 eventId·result digest 저장과 결과 반영을 같은 transaction으로 commit한다. 같은 eventId·같은 본문이 이미 반영되었으면 lease/deadline 종료 뒤 재전송이어도 200 DUPLICATE로 확인한다. 이 경로는 결과를 다시 쓰거나 상태를 되살리지 않는다. 같은 eventId의 다른 본문은 409 RESULT_EVENT_CONFLICT, 다른 eventId로 이미 완료된 결과를 교체하려 하면 409 RESULT_ALREADY_FINALIZED다.

삭제된 분석은 404 TARGET_NOT_FOUND로 종료한다. 미반영 결과의 stale·owner·lease·deadline·취소 검증을 중복 확인이라는 이유로 건너뛰지 않는다. resultAck 200은 DB commit 완료 후 반환한다.

## 9. PR #47 추가 보완 목록

검토 PR head는 `c97e7fb8b16cbde6cb394b1f3a57f1a135b3603c`다. 이 소스 기준 발견이며 실행 QA 결과가 아니다. PR #47은 develop과 분기되어 Jackson 복구 커밋을 아직 포함하지 않는다.

| 항목 | 필요한 보완과 소스 근거 |
|---|---|
| Jackson 복구 | 최신 develop `33efef6...`를 반영. normalizer의 Jackson 패키지·예외 처리를 유지. [복구 merge](https://github.com/voice-coaching/VC-BE/commit/33efef6ec90970003d1e8b66bcdb2a097038ed0f) |
| 서버 인증 경로 | 내부 경로 permitAll만 추가하면 opaque callback 토큰을 기존 JWT 필터가 먼저 파싱하여 차단함. 별도 서버 인증 filter chain으로 보호. [JWT 필터](https://github.com/voice-coaching/VC-BE/blob/c97e7fb8b16cbde6cb394b1f3a57f1a135b3603c/src/main/java/org/example/voice/common/security/JwtAuthenticationFilter.java#L35) |
| claim·heartbeat 상태 검증 | 다른 worker 덮어쓰기, claim 없는 heartbeat, 만료 후 연장을 차단. Backend 시간과 owner·digest를 원자적으로 검사. [AnalysisResult](https://github.com/voice-coaching/VC-BE/blob/c97e7fb8b16cbde6cb394b1f3a57f1a135b3603c/src/main/java/org/example/voice/analysis/domain/entity/AnalysisResult.java#L237) |
| HTTP timeout 처리 | Redis 활성화 조건에 묶인 sweeper를 HTTP 모드에서도 실행. [timeout sweeper](https://github.com/voice-coaching/VC-BE/blob/c97e7fb8b16cbde6cb394b1f3a57f1a135b3603c/src/main/java/org/example/voice/analysis/infrastructure/stream/AnalysisExecutionTimeoutSweeper.java#L27) |
| DB transaction과 원격 호출 | outbox/취소 예약 transaction → HTTP 호출 → 조건부 반영 transaction으로 분리. claim callback이 잠금을 기다리는 교착·긴 대기를 방지. [outbox dispatcher](https://github.com/voice-coaching/VC-BE/blob/c97e7fb8b16cbde6cb394b1f3a57f1a135b3603c/src/main/java/org/example/voice/analysis/infrastructure/runpod/RunPodAnalysisRequestOutboxDispatcher.java#L49) |
| 가짜 grant 제거 | HTTP 전용 입력 builder와 전송 중립 코어를 사용. wire에서만 grant를 제거하는 것으로 끝내지 않음. [현재 issuer](https://github.com/voice-coaching/VC-BE/blob/c97e7fb8b16cbde6cb394b1f3a57f1a135b3603c/src/main/java/org/example/voice/analysis/infrastructure/runpod/RunPodAnalysisAuthorizationIssuer.java#L24) |
| DTO·응답 외피·오류 | claim/heartbeat 시간 필드 교체, lease 응답·digest·result workerInstanceId 추가, direct JSON와 reasonCode 적용. 필터/예외 handler/client까지 변경 |
| 누락 API | worker-readiness와 RunPod GET client 구현. 완료 상태 판단은 callback/DB 기준 유지 |

DB V15는 담당자 회신 기준이다. migration은 최신 develop과 PR 내용을 합친 뒤 충돌 여부를 확인하여 새 번호를 배정하고 기존 checksum을 바꾸지 않는다. 이 문서만 보고 다음 번호를 미리 예약하지 않는다.

## 10. EC2 미디어 런타임 처리

`MEDIA_NORMALIZATION_ENABLED=false`는 일반 Backend 복구 상태로 유지한다. **RunPod가 준비돼도 이것만으로 분석 접수를 열 수 없다.** canonical 파일을 생성·검증할 미디어 런타임이 별도로 준비되어야 한다.

출처가 확인되지 않은 바이너리를 급히 설치하지 않는다. EC2와 호환되는 빌드 환경에서 FFmpeg 공식 릴리스 소스와 서명을 검증하여 ffmpeg·ffprobe를 빌드하고, 버전·build 옵션·산출물 SHA를 고정한 사내 배포 artifact로 공급하는 안을 권장한다. 서명은 배포 소스 검증용이며 Backend–RunPod 요청 grant/서명과 관계없다. FFmpeg 공식 안내는 소스와 릴리스 서명 검증 방법을 제공한다. [공식 다운로드·검증 안내](https://ffmpeg.org/download.html)

Python도 현재 normalizer가 사용하는 sandbox launcher의 요구와 EC2 아키텍처에 맞춰 준비한다. 기존 JAR가 실행 파일 경로를 호출하는 구조이므로 별도 미디어 컨테이너로 전환하려면 단순 env 변경이 아니라 호출 adapter 변경이 필요하다.

| 실제 설정명 | AWS 처리 |
|---|---|
| MEDIA_NORMALIZATION_SANDBOX_PYTHON_BINARY | 실행 가능한 Python 실제 파일의 절대 경로 |
| MEDIA_NORMALIZATION_FFMPEG_BINARY | 검증된 ffmpeg 실제 파일의 절대 경로 |
| MEDIA_NORMALIZATION_FFPROBE_BINARY | 검증된 ffprobe 실제 파일의 절대 경로 |
| MEDIA_NORMALIZATION_ENABLED | 담당자 수동 확인 전 false, 정규화·저장소 확인 후 true |

현재 normalizer는 실행 가능한 regular file·절대 경로·심볼릭링크 아님을 확인한다. `/usr/bin/python3` 등이 symlink이면 실제 실행 파일 경로를 설정한다. [현재 develop 설정](https://github.com/voice-coaching/VC-BE/blob/33efef6ec90970003d1e8b66bcdb2a097038ed0f/src/main/resources/application.yaml#L73)

개발자가 음성/영상 입력 → canonical 생성 → MIME·duration·크기·SHA 등록 → 제한된 객체 읽기를 직접 확인한다. 운영 기본값 입력 100MiB·출력 20MiB·duration 500~180,000ms·정규화 timeout 30초와 양쪽 허용 범위를 대조한다. RunPod JSON의 video.durationMs는 null을 허용하므로 값을 추정해 채우지 않는다.

## 11. 다음 인계 순서

1. AWS: 이 계약·두 Schema를 PR #47에 반영하고 최신 develop의 Jackson 복구와 합친다. 정규화 런타임·서버 인증·DB 작업 수명을 준비한다.
2. RunPod: HTTP 입력·공통 분석 코어·서버/worker·설정 주입·배포를 구현한다. 현재 문서 작업에서 Pod 재시작·Expose 변경·토큰 발급은 수행하지 않았다.
3. AWS: 토큰·credential은 채팅에 보내지 않고 비공개 전달 위치와 설정 완료 여부, readiness·미디어 검수 결과를 회신한다.
4. RunPod: 실제 listen·Expose·Proxy·양방향 인증·readiness를 확인한 관측 시각과 배포 SHA를 반환한다.
5. AWS: 그 실제 upstream으로 Nginx를 연결하고 개발자가 실제 처리·중복·취소·복구를 검수한 뒤 일반 사용자 분석 접수를 연다.

현재 완료 범위는 소스 검토, 읽기 전용 운영 확인, 계약·Schema 문서화와 정적 확인이다. 실제 추론·품질 QA, Backend/RunPod 코드 구현, 토큰 주입, commit/push/PR 변경 또는 배포는 이번 작업에서 수행하지 않았다.
