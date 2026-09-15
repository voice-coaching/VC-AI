# Backend–RunPod HTTP 연결 수정 계획

작성일: 2026-09-10. 상태: **수정 계획이며 구현·배포 완료 기록이 아니다.**

검토 기준은 VC-BE의 `46-refactor-ai-분석-연동을-redis-stream에서-runpod-http-callback으로-전환` 브랜치 `a0dbd1e84e9ba7be6d9fe62158a00c9d471f420f`와 intelligentAI `6618a396fb4d1c0d69ab76402bc2450a253be248`이다. 이후 브랜치 변경 시 구현 전에 차이를 다시 확인한다.

2026-09-13 후속: AWS 수정은 담당자가 진행하고, RunPod의 코드 분리·기동·배포 순서는 [RunPod HTTP 수정 계획](runpod_http_implementation_plan_20260913.md)을 따른다. **현재 공통 DTO·시간·digest·오류·응답 형식의 기준은 [v1.1 계약 확정 회신](runpod_http_contract_v1_20260913.md)과 첨부 JSON Schema다.** 아래 초기 제안보다 확정 계약을 우선한다. 실제 관측은 [연결 인계 문서](runpod_http_handoff_20260913.md)와 구분한다.

## 1. 결정과 범위

사용자의 결정에 따라 서비스 이용 단계에서 개인정보 동의를 완료한 사용자만 분석을 요청한다는 전제를 적용한다. **Backend–RunPod 요청·응답에는 동의 증빙, 동의 정책 revision, 요청별 처리 권한 grant·서명을 포함하지 않는다.** 이번 연결 작업에서 해당 검증 프로토콜을 새로 구현하지 않는다.

Backend의 사용자 로그인·세션/녹음 소유 관계 확인과 양방향 서버 호출자 인증은 유지한다. 서버 토큰은 외부 사용자가 분석 API를 호출하거나 결과를 위조하지 못하게 하는 서비스 인증용이다. 파일 SHA-256은 실제 분석한 파일이 등록된 파일과 같은지 확인하는 용도다.

수정 범위는 Backend HTTP 발행·결과 수신, RunPod 분석 접수·기존 추론 코어 연결, 작업 복구·취소, 배포 설정과 문서다. 기존 공개 사용자 API와 서비스 이용 자격 관리 방식의 변경은 이 내부 연결 계획에 포함하지 않는다. 실제 공개 API 요청 DTO의 조정이 필요하면 별도 API 변경으로 명시한다.

## 2. 현재 상태와 목표

| 구분 | 확인한 현재 상태 | 목표 |
|---|---|---|
| Backend 전송 | Redis 전송을 끄면 Disabled publisher가 분석 요청을 거절 | HTTP 전송을 독립적으로 선택 가능 |
| HTTP 구현 | VC-BE 브랜치에 문서·env 예제만 추가됨 | HTTP client, 내부 callback, 실행 상태 처리 구현 |
| RunPod | Clova는 내부 loopback API, 분석 워커는 Redis 소비자 | 별도 HTTP 분석 접수 서버와 실행 루프 |
| 추론 코어 | Seungun → 같은 시도 입술 분석 → 선택적 Clova 연결 코드 존재 | 기존 코어 재사용, 전송 방식과 분리 |
| 결과 | 음소 근거·입술 보완·결과 유형·파일 digest를 검증 | HTTP에서도 같은 의미와 필드 보존 |
| 작업 복구 | timeout·취소·발행 기능이 Stream 설정에 연결됨 | Backend PostgreSQL을 기준으로 HTTP 작업 복구 |
| 사용자 녹음 저장소 | 준비·연결이 필요함 | Backend 쓰기·정규화, RunPod canonical 입력 읽기 |

직전 운영 확인은 Clova `/health` HTTP 200, 전체 `/health/services` HTTP 503, 분석 워커 `environment_file_missing`이다. 이는 HTTP 분석 서버나 전체 요청의 성공을 의미하지 않는다.

## 3. 전체 처리 방식

1. Backend가 로그인 사용자와 선택된 녹음을 확인한다. 분석 대상 대본은 서버 콘텐츠에서 결정한다.
2. 같은 DB transaction에서 분석 작업과 전달용 outbox를 저장한다. 사용자 요청 transaction 안에서 GPU 추론을 기다리지 않는다.
3. 별도 dispatcher가 RunPod의 `POST /v1/analysis-jobs`를 호출한다.
4. RunPod는 서버 인증·입력·접수 여력을 확인하고, Backend의 현재 실행 세대를 원자적으로 점유한 뒤 `202 Accepted`와 작업 식별자를 반환한다.
5. RunPod 실행 루프가 canonical 음성을 읽고 Seungun을 실행한다. 영상이 있으면 같은 시도·선택 음소의 입술 근거를 구하고, 기존 Clova adapter로 상세 설명을 생성한다.
6. RunPod가 Backend 내부 결과 API에 callback한다. Backend는 현재 작업인지 확인하고 결과 저장·상태 변경·캐시 갱신을 같은 transaction으로 수행한다.
7. 프론트는 기존 Backend 상태·결과 API로 조회한다.

PostgreSQL의 작업/outbox가 최종 작업 원장이다. RunPod의 메모리나 `/tmp`에만 남아 있는 작업 목록을 복구 기준으로 삼지 않는다. B2는 외부 데이터·자산 보관에 사용하고, 이번 단계에서 메시지 큐나 작업 원장으로 사용하지 않는다.

## 4. HTTP 계약 수정

아래 경로·필드는 구현 시 양쪽 저장소에 함께 확정할 제안이다. 현재 배포된 API가 아니다.

### 분석 요청

`POST /v1/analysis-jobs`

| 필드 | 규칙 |
|---|---|
| `schemaVersion` | `voice-coaching.runpod-analysis-request.v1` |
| `requestId` | Backend가 만든 논리적 분석 시도의 UUID. 같은 전달 재시도에서는 유지 |
| `executionId` | Backend가 만든 실행 세대 UUID. Pod 장애로 새 실행을 배정할 때 교체 |
| `analysisId`, `recordingId`, `contentId` | 서버에서 확인한 식별자 |
| `learningFocus` | 현재 `PRONUNCIATION` |
| `promptRevision`, `scriptText`, `scriptSha256` | 분석 대본 버전과 내용·digest |
| `audio` | `objectKey`, `mimeType`, `sha256`, `fileSizeBytes`, `durationMs`. canonical WAV |
| `video` | 선택 사항. `objectKey`, `mimeType`, `sha256`, `fileSizeBytes`, `durationMs`. 같은 업로드에서 파생한 canonical MP4 |
| `deadlineAt` | UTC 작업 만료 시각. 대기·실행 시간 제한용 |

동의·grant 필드는 없다. `deadlineAt`은 오래된 작업을 실행하지 않기 위한 작업 관리 정보이며 grant 만료 시각이 아니다.

요청의 callback URL은 제거한다. RunPod 설정에 승인된 Backend base URL을 주입하고, 분석 ID로 고정 callback 경로를 조합한다. 저장소 bucket·endpoint 역시 서버 설정으로 고정하며 요청이 임의 저장소나 다운로드 URL을 지정하지 못하게 한다.

요청 상한은 우선 기존 운영값인 64 KiB를 유지한다. 알 수 없는 필드·중복 JSON key·잘못된 타입·경로 이동·대본 SHA 불일치는 접수 전에 거절한다. 기존 Redis request v4/v5의 필수 필드를 생략한 채 해당 버전이라고 표시하지 않는다. HTTP 전용 parser와 입력 모델을 만든다.

### 접수와 실행 상태

| API | 역할 |
|---|---|
| RunPod `POST /v1/analysis-jobs` | 인증·검증·Backend 실행 점유가 끝난 작업에 `202` 반환 |
| RunPod `GET /v1/analysis-jobs/{requestId}` | 현재 Pod에서 알고 있는 실행 상태 확인. Backend DB가 최종 상태 |
| RunPod `POST /v1/analysis-jobs/{requestId}/cancel` | `executionId`로 특정 실행 취소 요청 |
| Backend `POST /api/internal/ai/analyses/{analysisId}/claim` | 현재 request/execution을 비교하고 단일 worker 실행 점유·기한을 DB에 기록 |
| Backend `POST /api/internal/ai/analyses/{analysisId}/heartbeat` | 같은 실행의 점유 기한 갱신·취소 여부 확인 |

claim에는 worker instance ID도 연결한다. 하나의 실행이 다른 프로세스에서 동시에 점유되지 않게 한다. 같은 살아 있는 프로세스에 대한 전달 중복은 기존 접수 결과를 반환한다. 접수 응답을 잃어버려도 Backend는 claim 기록과 heartbeat를 통해 실행 여부를 확인할 수 있어야 한다.

시작 설정 제안은 실행 슬롯 1개, 로컬 대기 슬롯 0개다. 대기 작업은 Backend outbox에 두고 RunPod가 포화되면 `429`와 `Retry-After`를 반환한다. 모델 준비 전에는 claim하지 않고 `503`을 반환한다. 처리량 측정 후 슬롯을 조정한다.

### 결과 callback

`POST /api/internal/ai/analyses/{analysisId}/result`

| 필드 | 규칙 |
|---|---|
| `schemaVersion` | `voice-coaching.runpod-analysis-result.v1` |
| `eventId` | 동일 callback 전달 재시도에서 고정하는 결과 이벤트 UUID |
| `requestId`, `executionId` | 요청·실행 세대와 일치 |
| `analysisId`, `recordingId` | path·현재 분석·선택 녹음과 일치 |
| `status` | `COMPLETED` 또는 `FAILED` |
| `outcome` | 완료 시 `COACHING_READY` 또는 `COMPLETED_NO_ISSUE` |
| `summaryFeedback` | 교정 필요 시 기존 근거에 기반한 상세 설명 |
| `pronunciationEvidence` | 교정 필요 시 필수. 기존 selected phone·index·시간 구간·detector 의미 보존 |
| `visualSupplement` | 선택 사항. 기존 계약의 같은 시도·selected index 연결 유지 |
| `audioSha256` | 완료 시 필수. 등록된 canonical 음성과 비교 |
| `workerRevision`, `pipelineRevision` | 완료 시 필수. 실제 실행 코드·파이프라인 버전 |
| `failureCode`, `failureReason` | 실패 시 필수. 고정 코드와 사용자 표시 가능한 설명 |

HTTP DTO를 검증한 뒤 기존 `AnalysisWorkerResult`의 운영용 result v3 및 결과 저장 서비스를 재사용한다. `requestId`는 기존 내부 `requestEventId`에 대응한다. `executionId` 확인은 결과 적용 전에 수행한다. 원본 미디어·base64·로컬 파일 경로·베타 debug는 callback에 포함하지 않는다.

현재 제공되지 않는 STT·종합 점수·억양 점수·segments를 새로 생성하거나 detector score를 발음 정답률로 변환하지 않는다. 공개 응답의 기존 미제공 값은 null/빈 목록으로 유지한다. `COMPLETED_NO_ISSUE`에는 억지 교정 문장·음소 근거를 만들지 않는다. 결과 상한은 1 MiB, summary 길이 등은 기존 도메인 검증 한도를 유지한다.

## 5. 작업 유실·중복·Pod 재시작 처리

| 상황 | 처리 계획 |
|---|---|
| Backend가 요청 전송 전에 종료 | 같은 transaction의 outbox를 다음 실행에서 재전송 |
| RunPod 접수 응답 유실 | 같은 request/execution으로 재전송. 기존 claim과 실행을 확인하고 중복 추론 억제 |
| 작업이 접수됐지만 Pod가 종료 | heartbeat 만료 후 Backend가 이전 실행을 무효화하고 새 execution을 발급 |
| 새 Pod로 이전 | 새 endpoint와 worker instance를 확인한 뒤 미완료 작업을 새 실행 세대로 재배정 |
| 이전 Pod가 늦게 결과 전송 | request/execution이 현재 세대와 다르면 저장하지 않음 |
| 결과는 저장됐지만 200 응답 유실 | 같은 결과 callback에 다시 200 반환, 결과 중복 저장 없음 |
| Backend 일시 장애 | RunPod는 같은 callback 본문으로 재전송. 그 사이 heartbeat도 끊기면 기한 내 복구만 허용 |
| Pod가 미전달 결과까지 잃음 | Backend의 만료 작업 복구로 재실행 가능. 외부 상태의 중복 반영은 막되 장애 시 추론 재수행은 허용 |
| 사용자가 재시도 | 새 requestId. 같은 전달 재시도와 구분 |
| 녹음·기록 삭제 또는 작업 취소 | Backend에서 먼저 작업을 terminal로 만들고 취소 신호를 전송. 늦은 결과는 복원하지 않음 |

DB migration은 새 버전으로 추가한다. 기존 migration checksum을 수정하지 않는다. 실행 세대, worker instance, heartbeat/점유 만료 시각, 재전달 시각·횟수, 마지막 반영 결과 ID와 본문 digest를 기록한다. 기존 필드와 겹치는 항목은 재사용한다.

claim과 결과 적용은 DB 잠금/조건부 갱신으로 경쟁을 제어한다. HTTP 호출 중 DB transaction이나 행 잠금을 길게 유지하지 않는다. 실행 상태 callback이 전달 완료 표시보다 먼저 도착해도 상태가 과거로 돌아가지 않도록 전이를 단조롭게 처리한다.

heartbeat는 초기 15초, 점유 만료는 90초를 제안한다. 대기 제한·분석 제한·callback 재전송 제한은 별도 설정으로 둔다. 장시간 추론과 heartbeat는 서로 막지 않아야 한다. 전체 작업 deadline은 초기 15분을 검토하되 담당자의 실제 처리 시간 측정 후 확정한다. 시작 준비 시간은 분석 실행 시간과 구분한다.

삭제·취소 후에는 실행 세대에 해당하는 child를 중단하고 임시 파일을 정리한다. heartbeat 갱신 불가로 점유가 만료된 worker는 작업을 중단한다. Clova처럼 즉시 중단이 어려운 호출은 결과 반영을 차단하고 제한시간 내 종료하도록 한다.

### Callback 응답과 재전송 규칙

| 응답 | 의미와 동작 |
|---|---|
| `200` | 신규 결과 반영 또는 내용이 동일한 중복. 재전송 종료 |
| `400` / `422` | 계약·필드·결과 의미 오류. 자동 재전송 종료, 운영 오류 기록 |
| `401` / `403` | 서버 인증 오류. 해당 전달 중단 및 설정 오류 알림 |
| `404` | 삭제 등으로 대상 없음. 해당 전달 종료 |
| `409` | stale 실행 또는 동일 ID의 다른 결과. 기계 판독 가능한 코드로 구분하고 종료 |
| `429`, 일시적 `5xx`, 네트워크 오류 | 지수 backoff와 jitter, 상한·deadline을 지키며 재전송 |

callback 전달 실패를 모델 분석 실패로 바꾸어 새로운 실패 결과로 덮어쓰지 않는다. 같은 결과 전달을 재시도한다. RunPod 처리 중지 후 실행 세대가 바뀌면 옛 결과의 전달도 끝낸다.

## 6. 저장소·접속·환경변수

사용자 녹음의 object key·digest·길이 등 메타데이터는 Backend PostgreSQL에 저장한다. 실제 파일은 두 서버가 접근 가능한 private 객체 저장소에 둔다. Backend는 업로드·정규화·삭제, RunPod는 canonical 파일 읽기를 담당한다. 현재 등록 시점의 정규화 경로를 재사용한다.

기존 B2 연구자료 환경을 사용자 녹음 저장소로 자동 간주하지 않는다. 사용자 녹음에도 B2를 선택한다면 S3-compatible adapter용 범위 제한 자격증명과 해당 저장소의 조건부 읽기 동작을 준비한다. 현재 B2 Master Native Key를 AWS/S3 credential로 사용하지 않는다. 별도 저장소 생성이나 키 발급은 이 계획 작성에서 실행하지 않는다.

RunPod에는 분석 접수 API용 포트를 추가한다. 기존 Clova는 `127.0.0.1:8000`을 유지하고 분석 코어가 내부에서 호출한다. 접수 API만 RunPod HTTPS proxy로 노출한다. 긴 추론은 background 실행하므로 접수 HTTP 연결이 전체 추론 완료를 기다리지 않는다.

| 설정 제안 | 대상 | 역할 |
|---|---|---|
| `AI_ANALYSIS_TRANSPORT=disabled\|runpod_http\|redis_stream` | Backend | 전송 방식 명시적 선택. 미설정이면 disabled |
| `RUNPOD_ENDPOINT_URL` | Backend | 분석 API의 HTTPS base URL. 각 작업 경로는 client가 추가 |
| `AI_ANALYSIS_API_TOKEN` | 양쪽 | Backend → 분석 접수·조회·취소 API 호출자 인증 |
| `AI_ANALYSIS_CALLBACK_TOKEN` | 양쪽 | RunPod → 결과·claim·heartbeat API 호출자 인증 |
| `AI_ANALYSIS_CALLBACK_BASE_URL` | RunPod | 승인된 Backend HTTPS base URL |
| 객체 저장소 설정 | 양쪽 | 같은 bucket·canonical prefix, 역할별 credential |
| `AI_ANALYSIS_CLOVA_ENABLED=true` 및 내부 endpoint | RunPod | 기존 상세 설명 adapter 사용 |
| 실행 슬롯·heartbeat·deadline·재시도 설정 | 양쪽 | 동일한 작업 수명 규칙 |

기존 문서의 `RUNPOD_API_KEY`를 분석 서비스 토큰 이름으로 사용하지 않는다. RunPod 계정 관리 키는 Pod 관리용으로만 분리한다. `AI_CALLBACK_TOKEN` 예제 이름은 위 새 이름으로 양쪽 문서·주입 설정을 함께 정리한다. 토큰 값은 요청 JSON이나 문서에 넣지 않는다.

Backend callback은 전용 서버 인증 경로로 구성한다. `/api/internal/...`이라는 이름만으로 보호된다고 가정하거나 단순 permitAll로 열지 않는다. 양방향 HTTPS·서버 토큰 검증을 적용한다. 토큰과 저장소 설정은 프로세스 환경/비공개 주입원에서 복원한다.

Pod 이전 시 HTTPS proxy URL의 Pod ID가 바뀔 수 있으므로 실제 upstream 갱신·health 확인·작업 재배정 순서를 운영 문서에 포함한다. 2026-09-13에 할당된 고정 도메인 `https://ai.voice-coaching.site`를 사용하는 구성에서는 Backend endpoint를 유지하고 담당자가 Nginx upstream과 Host/SNI를 변경한다. 이번 기능은 GPU를 자동 구매·시작·이전하는 기능까지 포함하지 않는다.

## 7. 저장소별 구현 작업

### VC-BE

1. `analysis` infrastructure 아래 HTTP client·내부 DTO·설정·callback 인증과 controller를 추가한다.
2. `AnalysisJobPublisher`가 HTTP 모드에서 요청/outbox를 저장하도록 구현한다. `DisabledAnalysisJobPublisher`와 capability 판정은 선택된 전송 방식에 맞춰 변경한다.
3. HTTP용 요청 builder는 공통 대본·미디어 입력을 사용한다. 기존 Redis 전용 입력 모델의 필수 필드를 가짜 값으로 채우는 변환은 하지 않는다.
4. 기존 `AnalysisRequestOutboxDispatcher`, `AnalysisExecutionTimeoutSweeper`, 취소 dispatcher에서 작업 수명 기능을 분리하고 HTTP adapter를 연결한다. Redis를 끈다고 timeout·취소까지 꺼지지 않게 한다.
5. claim·heartbeat·실행 세대 변경·callback을 현재 job과 원자적으로 대조한다. HTTP callback을 운영용 결과 모델로 변환한 뒤 `AnalysisResultIngestionService`의 저장·캐시·미디어 정리 흐름을 재사용한다.
6. `AnalysisCapabilitiesReaderImpl`는 HTTP 설정 여부를 반영한다. 설정 완료와 실제 모델 readiness를 혼동하지 않게 한다.
7. 필요한 DB migration, `.env.example`, HTTP 계약·API 명세·배포 문서를 함께 갱신한다. 오래된 Stream 기본 설명과 Clova 미연결 설명을 정리한다.

### intelligentAI / RunPod

1. `backend_analysis`에 HTTP 요청 모델·parser, 접수/조회/취소 서버, 실행 루프, Backend 상태/callback client를 추가한다.
2. `service.py`, `ports.py`, `production.py`에서 전송 방식과 독립적인 분석 입력·실행 코어를 분리한다. HTTP 입력에는 현재 Redis 전용 `AnalysisRequest.from_payload`를 호출하지 않는다. 기존 Redis 경로가 남는 경우 해당 adapter에서 자신의 입력을 공통 모델로 변환한다.
3. `s3_object_store.py`의 조건부 다운로드·크기/MIME/SHA 검증·정리를 재사용한다. Seungun·visual adapter가 공통 입력 모델을 받도록 조정한다.
4. Seungun은 하나의 실행 시도에서 한 번 호출한다. 입술·Clova는 그 결과를 재사용한다. `grounded_feedback.py`의 상세 설명과 승인된 문장 fallback을 유지한다.
5. HTTP 실행 구성은 기존 Redis/HMAC 필수 설정 없이 초기화되도록 분리한다. 현재 `prepare_worker.py`, `deployment_attestation.py`, `services.json`의 시작 조건도 함께 수정한다. 배포 revision·모델/분석 자산 hash·실행 권한 확인은 유지한다.
6. 자동 boot/supervisor에 HTTP 접수 서버와 실행 루프를 등록한다. worker instance ID는 프로세스 기동마다 새로 발급한다. 한 요청 종료 뒤 메모리·임시 파일과 슬롯을 해제한다.
7. readiness는 분석 자산 적재·저장소 설정·접수 준비 상태를 반영한다. 전체 서비스 상태와 Clova 단독 health를 구분한다. 별도 RunPod/Backend 배포 revision을 운영 기록에 남긴다.

## 8. 구현 순서와 인계 기준

| 단계 | 산출물 | 완료 조건 |
|---|---|---|
| 1. 계약 확정 | HTTP DTO·결과 매핑·상태/재전송 표 | 누락된 음소·입술 근거 복구, 서버 토큰 이름·path·null 처리 일치 |
| 2. Backend 구현 | HTTP outbox·claim·heartbeat·callback·취소·timeout | Stream 비활성 상태에서도 HTTP 작업 수명 기능이 구성됨 |
| 3. RunPod 구현 | HTTP adapter·기존 추론 코어·callback client | Redis 및 기존 요청별 검증 설정 없이 HTTP 실행 구성 가능 |
| 4. 배포 준비 | 새 overlay·비공개 설정·시작 명령·복구 절차 | 두 저장소 revision과 환경 목록을 고정, 기존 실행본 복구 가능 |
| 5. 담당자 검증 | 실제 음성/영상 요청과 장애 시나리오 결과 | 아래 항목을 담당자가 확인하고 admission 활성화 |

담당자 확인 항목:

- 음성만 있는 요청과 같은 원본의 음성·영상 요청이 기존 공개 API 결과까지 저장된다.
- 교정 필요/문제 없음, 입술 분석 미제공, Clova fallback을 각각 올바르게 표시한다.
- 동일 요청 재전송, callback 응답 유실, 다른 실행 세대의 늦은 결과에서 중복 저장이 없다.
- 처리 중 Pod 종료·새 Pod 이전 후 미완료 작업이 복구되거나 정해진 시간 내 실패로 종료된다.
- 사용자 취소·녹음 삭제 후 결과가 다시 나타나지 않는다.
- 슬롯 포화·저장소 접근 실패·잘못된 파일 digest·서버 인증 오류가 구분된다.
- 자동 기동 후 HTTP 분석 서버와 기존 Clova가 각자의 health와 배포 revision을 보고한다.

Backend는 `develop` 대상 PR로 인계하고 담당자가 검증·배포한다. RunPod 변경 배포와 실제 서비스 연결 완료는 별도로 기록한다. 전환 중에는 새 작업 접수를 닫고 진행 작업을 정리한 뒤 양쪽 설정을 맞춘다. 실패 시 접수를 닫고 실행 세대를 무효화한 후 이전 배포로 되돌린다. Redis가 미설정이면 rollback 상태도 분석 disabled로 두며 성공으로 표시하지 않는다.

이 문서 작성에서는 소스와 설정 의존성만 대조했다. API 구현·설정 변경·Pod 재기동·저장소 생성·commit/push는 수행하지 않았다. 브라우저·회귀·실제 추론 QA는 개발자 담당이며 자동 QA나 관련 실행 산출물을 생성하지 않았다.

## 참고

- [VC-BE HTTP 계약 검토본](https://github.com/voice-coaching/VC-BE/blob/a0dbd1e84e9ba7be6d9fe62158a00c9d471f420f/docs/api/ai-runpod-http-callback-contract.md)
- [VC-BE 구현 계획 검토본](https://github.com/voice-coaching/VC-BE/blob/a0dbd1e84e9ba7be6d9fe62158a00c9d471f420f/docs/ai_analysis_integration_implementation.md)
- [현재 분석 코어](../src/voice_coach/backend_analysis/service.py), [현재 실행 조합](../src/voice_coach/backend_analysis/production.py)
- [RunPod 자동 기동 안내](../deploy/runpod/README.md)
