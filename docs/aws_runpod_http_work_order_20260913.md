# AWS 개발자 작업 요청서 — RunPod HTTP 분석 연동 마무리

작성일: 2026-09-13 · 수신: VC-BE 개발·AWS 운영·Nginx 담당자

> **2026-09-15 후속 운영 알림:** A40 Pod `s8hb5b0l5k4qlx`로 파일 이전·검증 및 8080·Clova 자동 기동을 확인했습니다. 그러나 실제 중지 후 재가동에서 GPU 부족이 발생해 현재 원본·새 Pod 모두 중지 상태입니다. 새 Proxy `https://s8hb5b0l5k4qlx-8080.proxy.runpod.net`은 현재 전환 가능한 upstream이 아닙니다. Nginx preparing 503을 유지하고 재가동·readiness 회신을 기다려 주세요. 아래 2026-09-13 표는 과거 관측이며 최신 근거는 [A40 이전 수행 기록](runpod_a40_migration_20260915.md)을 적용합니다. v1.1 계약과 AWS 준비 항목은 유지합니다.

**RunPod 분석용 8080 서버와 HTTP Expose 배포를 완료했습니다. AWS 내부 API 인증·계약 구현, canonical 녹음 저장소, 미디어 정규화 환경을 준비한 뒤 고정 도메인을 연결해 주세요.** 분석 readiness는 아직 503이므로 아래 전환 조건을 충족할 때까지 일반 사용자 분석 접수를 비활성화하고 Nginx preparing 503을 유지해 주세요.

적용 계약은 **`voice-coaching.runpod-http.v1.1`**입니다. 이 요청서와 다음 세 파일을 함께 전달합니다. 필드·타입·null·오류 코드의 정본은 계약과 두 Schema이며, 이번 요청서에서 계약을 변경하지 않습니다.

- [HTTP 계약 확정본](runpod_http_contract_v1_20260913.md)
- [요청·제어·오류 Schema](contracts/runpod_http_control_v1.schema.json)
- [결과·음소·영상 Schema](contracts/runpod_result_v1.schema.json)

계약 문서 1~2절에 남아 있는 “8080 미배포·Expose 미설정”은 배포 전 관측입니다. 운영 상태는 아래 시각이 있는 배포 기록을 적용합니다. 이번 요청서 작성 중 서버를 다시 조회하지 않았습니다.

## 1. RunPod 인계 정보

| 항목 | 전달 값·확인 결과 |
|---|---|
| 실제 Pod ID | `urwnuesv7o4fv6` |
| 분석 서버 | `0.0.0.0:8080` HTTP |
| HTTP Expose | `8080/http` 적용, 기존 `22/tcp` 보존 |
| 실제 Proxy URL | `https://urwnuesv7o4fv6-8080.proxy.runpod.net` |
| Backend가 사용할 고정 base URL | `https://ai.voice-coaching.site` |
| RunPod 배포 SHA | `b4057a008ad45c89f540e4cb6e19b6f29cd53aed` |
| Proxy liveness | 2026-09-13 20:49 KST: `GET /health` → 200 `{"status":"alive"}` |
| Proxy 분석 readiness | 같은 관측 구간: `GET /readyz` → 503 `{"status":"not_ready"}` |
| 업무 인증 경계 | 무인증 `/health/services`는 401, API 토큰 사용 시 상세 준비 상태 503에 접근 가능 |
| RunPod 상세 상태 | 20:48 KST: `http_settings_incomplete_or_invalid`, executor 미준비 |
| Clova | 20:51 KST: 내부 health 200 ready. 전체 분석 준비 완료를 뜻하지 않음 |
| 방향별 서버 토큰 | RunPod 주입·재기동 복원 완료. AWS 프로세스와 일치는 미확인 |
| AWS 내부 readiness | 20:44:59 KST: 보유 callback 토큰으로 `GET /api/internal/ai/worker-readiness` 호출 시 **401** |
| 미완료 설정 | 사용자 녹음 저장소·readiness 객체·RunPod 읽기 자격증명 |
| 고정 AI 도메인 | 20:49 KST: `/health` 503 preparing 유지, Nginx upstream 미전환 |

8080 서버의 실제 Proxy HTTPS 연결은 확인했습니다. **AWS 내부 readiness 401의 원인은 아직 확정하지 않았습니다.** 토큰 불일치, 사용자 JWT 필터와 서버 토큰의 충돌, 내부 API 미배포 여부를 실제 AWS 배포본에서 확인해 주세요.

AWS에서 앞서 회신한 배포 SHA는 `33efef6ec90970003d1e8b66bcdb2a097038ed0f`, Actuator 200, `alpha-backend` active/running, DB V15입니다. 이는 담당자 회신 당시 상태입니다. 변경 후 실제 JAR SHA·서비스·DB 버전을 다시 회신해 주세요. 과거 공개 API 502를 현재 장애로 취급하지 않습니다.

## 2. 작업 순서와 완료 기준

| 순서 | AWS 담당자 작업 | 완료 기준 |
|---|---|---|
| A1 | 현재 배포본과 내부 readiness 401 원인 확인 | 실제 배포 SHA, 내부 API 구현·배포 여부, 인증 실패 원인과 수정 위치 회신 |
| A2 | PR #47을 최신 develop 및 v1.1 계약에 맞춰 보완 | 3~4절 반영, develop 대상 PR·migration·설정 변경 목록 제공 |
| A3 | canonical 녹음 저장소와 RunPod 읽기 권한 준비 | 5절 저장소 정보·readiness 객체·비공개 credential 공급 방식 제공 |
| A4 | ffmpeg·ffprobe·Python 및 정규화 경로 복구 | 6절 실행 환경과 canonical 생성 준비 확인 |
| B1 | Backend 보완본 배포 및 내부 API 확인 | 인증된 worker-readiness 200과 v1.1 직접 DTO, claim·heartbeat·result 동작 근거 |
| B2 | RunPod 담당자에게 설정 정보 전달 | A3 정보와 토큰 일치 확인 전달. RunPod 담당자가 설정 주입 후 실제 Proxy `/readyz` 200 회신 |
| B3 | Nginx 실제 upstream 전환 | 고정 도메인 health·readyz, 인증·경로·query 전달 확인 |
| B4 | 개발자 직접 실연동 확인 후 일반 분석 접수 활성화 | 결과 DB 반영·취소·복구·품질 확인, 활성화 시각·원복 책임자 기록 |

**A1~A3 결과부터 우선 회신해 주세요.** 미디어 환경 준비와 Backend 수정은 병행할 수 있습니다. URL 설정 또는 health 200만으로 전체 분석 연동 완료를 판정하지 않습니다.

## 3. PR #47 보완 요청

이전 검토 대상은 PR #47의 `c97e7fb8b16cbde6cb394b1f3a57f1a135b3603c`입니다. 현재 PR에서 이미 수정한 항목은 완료 근거로 회신하고, 남은 차이를 최신 develop 기준으로 반영해 주세요.

| 변경 영역 | 요청 사항 |
|---|---|
| develop 동기화 | Jackson 기동 복구 변경을 포함한 최신 develop과 대조·통합. 실제 배포 JAR와 PR SHA를 별도로 기록 |
| 서버 인증 | opaque Bearer 토큰이 사용자 JWT 필터에서 거절되지 않도록 내부 서버 인증 흐름 분리. 단순 `permitAll` 금지. callback 토큰 constant-time 비교 |
| worker-readiness | 인증된 `GET /api/internal/ai/worker-readiness` 구현. 내부 처리 경로·DB·계약 준비 확인. RunPod readiness를 다시 호출하지 않음 |
| HTTP 전송 선택 | `disabled / runpod_http / redis_stream` 분리. Redis가 꺼져 있어도 HTTP 전송·timeout sweeper·취소·복구 동작 |
| 요청 생성 | HTTP 전용 DTO·outbox 본문 생성. Redis parser 통과용 가짜 grant 제거. 사용자 로그인·녹음 소유 관계·지원 입력 검증 유지 |
| claim·heartbeat | 원본 요청 digest와 현재 실행·worker 소유권 원자적 검증. Backend 시간 사용, `claimedUntil`·`heartbeatAt` 입력 제거. 다른 worker 덮어쓰기·만료 lease 부활 방지 |
| 상태 조회·취소 | RunPod GET의 필수 `executionId` query와 cancel body 지원. 로컬 404를 Backend DB 작업 부재로 해석하지 않음 |
| outbox·transaction | 분석 작업과 outbox를 같은 transaction에 저장. 예약·원격 전송·완료 갱신을 나눠 네트워크 I/O 중 DB transaction/잠금을 길게 유지하지 않음 |
| 수명·복구 | 전송 재시도는 같은 requestId/executionId·본문. 재배정은 새 executionId. 늦은 전송 완료·heartbeat·callback의 상태 되돌림 차단 |
| 결과 수신 | `workerInstanceId`·하위 DTO·null 규칙 반영. 기존 결과 저장·캐시·녹음 정리 흐름에 HTTP DTO를 별도로 변환 |
| DB migration | 실제 현재 버전 확인 후 필요한 새 migration 추가. 기존 필드 재사용 여부 대조, 기존 migration checksum 수정 금지 |
| 설정·접수 | parser·bean 선택·client·작업 수명까지 연결. 전송 방식 선택과 일반 사용자 분석 접수 활성화를 분리 |

변경 진입점은 `AnalysisJobPublisher`, `OutboxAnalysisJobPublisher`, `DisabledAnalysisJobPublisher`, `AnalysisRequestOutboxDispatcher`, `TrainingAnalysisRequestService`, `AnalysisCapabilitiesReaderImpl`, `AnalysisWorkerResult`, `AnalysisResultIngestionService`, timeout sweeper·취소 dispatcher·보안 설정입니다. 최신 코드에서 구조가 달라졌다면 동등한 실행 경로에 적용해 주세요.

Backend–RunPod HTTP에 동의 증빙·정책 revision·요청별 처리 권한 grant·서명을 추가하지 않습니다. 양방향 서버 인증과 파일 SHA 검증은 유지합니다.

## 4. 확정 HTTP 계약 적용

| 방향 | 메서드·경로 | 정상 동작 |
|---|---|---|
| Backend → RunPod | `POST /v1/analysis-jobs` | 신규·동일 실행 진행 중 202 `jobAccepted`, 캐시된 동일 종료 실행 200 |
| Backend → RunPod | `GET /v1/analysis-jobs/{requestId}?executionId=...` | 200 `jobStatus`. 두 UUID 필수 |
| Backend → RunPod | `POST /v1/analysis-jobs/{requestId}/cancel` | body에 executionId. 처리 중 202·동일 종료 실행 200 |
| RunPod → Backend | `POST /api/internal/ai/analyses/{analysisId}/claim` | 200 `leaseResponse` |
| RunPod → Backend | `POST /api/internal/ai/analyses/{analysisId}/heartbeat` | 200 `leaseResponse` |
| RunPod → Backend | `POST /api/internal/ai/analyses/{analysisId}/result` | 200 `resultAck` |
| RunPod → Backend | `GET /api/internal/ai/worker-readiness` | 준비 시 200·미준비 503, `status`·`contractVersion`·`serverTime` |

Backend → RunPod는 `Authorization: Bearer <AI_ANALYSIS_API_TOKEN>`, 반대 방향은 별도 `AI_ANALYSIS_CALLBACK_TOKEN`을 사용합니다. callback base URL은 `https://api.voice-coaching.site`입니다. 내부 정상·오류 응답은 **DTO 자체**이며 `{result,message,data}` 외피를 붙이지 않습니다.

- 요청은 제어 Schema의 `analysisRequest`, 결과는 결과 Schema의 `result`를 적용합니다. 미정의·중복 JSON key를 거절하고 nullable 필수 키를 생략하지 않고 null로 보냅니다. UTC 시각은 밀리초 3자리 `Z`, UUID는 소문자 하이픈 표기입니다.
- claim은 `requestId`, `executionId`, `workerInstanceId`, **`requestPayloadSha256`**를 받습니다. digest는 `lower_hex(SHA-256(UTF-8(RFC 8785 JCS(전체 분석 요청 객체))))`이며 Backend가 저장한 원본과 비교합니다. 단순 키 정렬 serializer로 대체하거나 RunPod가 보낸 값을 저장만 하고 통과시키지 않습니다. digest 필드를 원본 요청 자체에 넣지 않습니다.
- heartbeat body는 `requestId`, `executionId`, `workerInstanceId`뿐입니다. lease 응답은 `analysisId`, `requestId`, `executionId`, `workerInstanceId`, `serverTime`, `leaseExpiresAt`, `continueProcessing`입니다.
- **heartbeat 15초 / lease 90초 / 전체 deadline 15분**입니다. Backend가 `leaseExpiresAt=min(serverTime+90초,deadlineAt)`을 계산합니다. 같은 worker의 유효한 claim 재전송은 현재 lease를 반환하고 연장하지 않습니다. 연장은 heartbeat로만 수행합니다.
- deadline은 논리적 시도 생성부터 outbox 대기·추론·callback을 포함하며 실행 재배정으로 연장하지 않습니다. Backend DB가 최종 작업 원장입니다. 취소·삭제는 DB에서 먼저 확정하고 늦은 결과로 복원하지 않습니다.
- 통신 connect timeout **3초**, 한 시도 전체 **10초**, 실행 한계 안전 여유 **최소 2초**입니다. 남은 lease/deadline에 맞춰 timeout을 줄입니다. 일시 장애 backoff는 1·2·4·8초부터 최대 15초와 jitter이며 Retry-After·남은 수명을 지킵니다.
- 접수·제어 본문 상한은 **65,536 bytes**, 결과는 **1,048,576 bytes**입니다. 요청 압축을 받지 않습니다.
- 결과 eventId·본문은 재전송에서 고정합니다. 같은 eventId·같은 JCS 본문이 이미 반영됐다면 lease가 끝났어도 200 `DUPLICATE`, 신규 반영은 200 `APPLIED`입니다. ack 필드는 `eventId`, `analysisId`, `requestId`, `executionId`, `status`, `serverTime`입니다.
- 같은 eventId의 다른 본문은 `409 RESULT_EVENT_CONFLICT`, 다른 이벤트로 최종 결과 덮어쓰기는 `409 RESULT_ALREADY_FINALIZED`입니다. 신규 결과에는 현재 실행·claim owner·lease·deadline·완료 음성 SHA 검증이 필요합니다.

`COMPLETED_NO_ISSUE`는 설명·음소 근거·영상 보완을 null로 유지합니다. 음소 index·시간 구간·동일 입력 영상 관계 등 Schema 외 애플리케이션 검증도 유지합니다. 없는 교정·STT·점수를 생성하지 않습니다. callback 전달 실패를 새 모델 분석 FAILED 결과로 덮어쓰지 않습니다.

업무 오류는 직접 `{reasonCode,message,requestId,executionId,retryable}`로 응답하며 식별자를 확정할 수 없으면 null입니다. 401/403 인증, 400/422 입력, 413 크기, 415 Content-Type, 404 대상 없음, 409 실행·결과 충돌, 429 포화·제한, 503 준비 미완료를 구분합니다. 정확한 reason code는 첨부 제어 Schema와 계약 8절을 적용하고 영구 오류를 무한 재시도하지 않습니다.

## 5. 녹음 저장소·프로세스 설정

canonical WAV/MP4를 둘이 접근 가능한 **private 객체 저장소**에 준비해 주세요. 연구·학습자료 B2와 같은 저장소라고 가정하지 않습니다. 원본과 canonical 파일을 구분하고 요청의 MIME·크기·duration·SHA를 실제 canonical 바이트 기준으로 등록합니다.

| AWS가 제공할 정보 | RunPod 반영 설정·조건 |
|---|---|
| 저장소 종류·bucket·region | `AI_ANALYSIS_OBJECT_STORAGE_BUCKET`, `AI_ANALYSIS_OBJECT_STORAGE_REGION` |
| canonical key prefix | `AI_ANALYSIS_OBJECT_STORAGE_RECORDINGS_PREFIX`. 요청 objectKey 허용 범위와 일치 |
| custom endpoint·path-style | `AI_ANALYSIS_OBJECT_STORAGE_ENDPOINT`, `AI_ANALYSIS_OBJECT_STORAGE_PATH_STYLE`. AWS S3 기본이면 custom endpoint는 비움 |
| readiness 객체 | **canonical prefix 안**의 사용자 정보 없는 1~65,536 bytes 객체. key·실제 SHA-256·크기 제공 |
| readiness 설정 | `AI_ANALYSIS_READINESS_OBJECT_KEY`, `AI_ANALYSIS_READINESS_OBJECT_SHA256`, `AI_ANALYSIS_READINESS_OBJECT_SIZE_BYTES` |
| 파일 식별·변경 방지 | VersionId 또는 ETag/If-Match 조건부 읽기, HEAD/GET 메타데이터 일치 확인 |
| 읽기 credential | canonical prefix·readiness 객체 읽기만 허용. 비공개 공급 위치·권한·만료·갱신·서비스 반영 방법 제공 |

RunPod에는 위 저장소·readiness 설정이 미주입 상태입니다. AWS access key 방식도 미설정으로 확인했으며, 다른 SDK credential 공급 방식을 선택하면 RunPod 적용 방법과 갱신 경로를 명시해 주세요. EC2 IAM role은 RunPod에 자동 전달되지 않습니다. bucket 전체 listing·쓰기·삭제 권한은 기본 요구 사항이 아닙니다. SSE-KMS를 사용한다면 해당 읽기에 필요한 복호화 권한을 확인합니다.

방향별 서버 토큰은 RunPod에 이미 준비되어 있습니다. 합의한 **비공개 설정 경로**로 AWS와 값을 맞추고, 회신에는 일치 확인·주입 완료 여부만 적어 주세요. RunPod 관리 키를 서비스 토큰이나 저장소 credential로 재사용하지 않습니다.

| Backend 설정 | 적용 요청 |
|---|---|
| `AI_ANALYSIS_TRANSPORT=runpod_http` | parser·전송 선택에 연결. 일반 사용자 접수는 별도로 비활성 유지 |
| `RUNPOD_ENDPOINT_URL=https://ai.voice-coaching.site` | HTTP client base URL, client가 업무 경로 추가 |
| `AI_ANALYSIS_API_TOKEN` | Backend 발신용, RunPod 검증값과 일치 |
| `AI_ANALYSIS_CALLBACK_TOKEN` | Backend 수신 검증용, RunPod 발신값과 일치 |
| 저장소·정규화 설정 | 실제 VC-BE 설정 클래스가 읽는 이름으로 구현·주입 |

systemd EnvironmentFile·컨테이너 환경·secret manager 중 실제 주입 방법과 적용 revision을 회신해 주세요. PC `.env` 편집만으로 AWS 설정이 바뀌지 않습니다. RunPod 전용 설정 주입·재기동 복원 갱신은 RunPod 담당자가 수행합니다. **토큰·AWS 키·Authorization 헤더 값은 채팅·문서·Git·로그에 넣지 않습니다.**

## 6. EC2 미디어 실행 환경

이전 회신의 ffmpeg·ffprobe 미설치, Python 경로 무효 상태를 해결해 주세요. 현재 설치 상태는 AWS에서 다시 확인합니다.

1. 출처·버전·무결성을 확인할 수 있는 배포물 또는 재현 가능한 빌드로 ffmpeg·ffprobe를 준비하고 검증 근거를 기록합니다.
2. 실제 Backend 서비스 사용자 권한으로 ffmpeg·ffprobe·Python 절대 경로, 실행 권한, 필수 의존성과 임시 작업 경로를 확인합니다.
3. canonical WAV와 같은 업로드에서 파생한 MP4의 생성·저장·메타데이터 등록을 연결합니다. 크기·duration·SHA를 추정값으로 채우지 않습니다.
4. 준비 전에는 `MEDIA_NORMALIZATION_ENABLED=false`로 일반 API 복구 상태를 유지합니다. 준비 후 정규화 활성화와 일반 사용자 분석 접수 활성화를 별도 단계로 관리합니다.

실제 녹음의 정규화·처리·품질은 개발자가 직접 확인합니다. worker-readiness 200이나 바이너리 실행 확인만으로 이 단계의 성공을 보고하지 않습니다.

## 7. Nginx 연결 요청

실제 upstream은 **`https://urwnuesv7o4fv6-8080.proxy.runpod.net`**입니다. 구조는 `ai.voice-coaching.site → HTTPS RunPod Proxy → Pod HTTP :8080`입니다. 기존 Clova 내부 8000을 분석 upstream으로 사용하지 않습니다.

현재 preparing 503을 유지하고, AWS 내부 인증·계약·저장소 연결 후 RunPod 담당자가 **Proxy `/readyz` 200**을 회신하면 전환해 주세요.

| 항목 | 적용 기준 |
|---|---|
| TLS·Host·SNI | upstream HTTPS 인증서 검증 유지. Host·SNI는 `urwnuesv7o4fv6-8080.proxy.runpod.net` |
| URI·query | `/v1/analysis-jobs`, 조회 executionId, 취소 경로를 그대로 전달. 접두사 삭제·변경 금지 |
| Authorization | Bearer 헤더 보존. query·로그로 옮기지 않음 |
| 본문 상한 | AI 접수 방향 64 KiB, Backend result callback ingress 방향 1 MiB를 각각 적용 |
| timeout | 3초 connect·10초 단일 요청 계약과 정합되게 설정. 전체 추론 15분 동안 연결을 기다리지 않음 |
| 재시도·캐시 | 업무 POST 임의 재전송·업무 응답 캐시 금지. 202·4xx·503을 임의 성공으로 변환하지 않음 |
| health·readyz | liveness 200 alive와 분석 준비 200 ready 구분. 슬롯 포화는 접수 429이며 그 이유만으로 readyz를 장애 처리하지 않음 |
| 상세 상태 | `/health/services` 서버 인증 유지 |
| 고정 도메인 | 기존 인증서·HTTP→HTTPS 301 유지. Pod 교체 시 upstream·Host·SNI 갱신, Backend base URL 유지 |

## 8. 완료 확인·전환·원복

- **AWS 배포:** 실제 JAR SHA·서비스·공개 API·migration, 서버 토큰 인증, worker-readiness v1.1 응답.
- **저장소·연결:** canonical/readiness 객체·제한 읽기 권한, Proxy와 고정 도메인 health·readyz, 양방향 인증.
- **개발자 직접 실연동:** 음성만·동일 업로드 음성+영상, 202 이후 callback DB 반영, 문제 없음·Clova fallback·영상 보완 불가의 의미 보존.
- **개발자 직접 수명 확인:** 중복·응답 유실·잘못된 SHA/토큰·429·lease 만료·Pod 재시작·재배정·취소·삭제 후 늦은 결과 차단.

QA는 개발자가 직접 수행합니다. Claude·브라우저 자동화·자동 회귀 테스트 및 관련 설정·산출물 생성은 이번 요청에 포함하지 않습니다. 컴파일·Schema·health 확인을 실제 추론·피드백 품질 통과로 표시하지 않습니다.

전체 검증 전 일반 사용자 분석 접수를 열지 않습니다. 장애 시 접수를 닫고 해당 실행 세대를 무효화한 뒤 이전 Backend release·Nginx 설정으로 원복합니다. DB의 이전 release 호환성을 확인하고 검증되지 않은 역방향 migration은 실행하지 않습니다. 기존 Redis도 미설정이면 분석 disabled로 원복합니다. PR merge·배포·migration·Nginx 전환 시각을 각각 기록해 주세요.

## 9. 회신 양식

항목별로 **미완료 / 구현 완료 / 배포 완료 / 검증 완료**를 구분해 주세요. 키 값은 적지 않습니다.

```text
[AWS → RunPod 작업 결과]
확인 시각(KST 또는 UTC):
담당자:

1. Backend·PR #47
- 최신 develop / 보완 PR URL / merge SHA:
- 실제 AWS 배포 SHA / 서비스·공개 API 상태:
- DB migration 버전 / 적용 결과:
- v1.1 계약·Schema 반영 여부 / 남은 차이:
- HTTP 전송·outbox·조회·취소·sweeper·결과 멱등 처리 상태:

2. 내부 API·서버 인증
- worker-readiness 401의 확인된 원인 / 조치:
- 인증된 readiness HTTP 상태 / contractVersion / 확인 시각:
- claim·heartbeat·result 확인 결과 / 직접 DTO·reason code 적용:
- 방향별 토큰 일치 확인·AWS 프로세스 주입 완료 여부(값 제외):
- 주입 방식 / 비공개 공급 위치·식별자 / 회전·재시작 방법:

3. 사용자 녹음 저장소
- 종류 / bucket / region / endpoint / path-style:
- canonical prefix / MIME·정규화·메타데이터 규칙:
- VersionId 또는 ETag/If-Match 확인 결과:
- readiness 객체 key / SHA-256 / 크기(1~65,536 bytes):
- RunPod credential 비공개 공급 위치 / 권한 / 만료·갱신 방식:

4. 미디어 실행 환경
- ffmpeg·ffprobe 출처·버전·무결성 확인 / 실행 경로:
- Python 경로·의존성·서비스 사용자 권한 확인:
- canonical 생성·저장 확인 결과:
- MEDIA_NORMALIZATION_ENABLED 적용 상태:

5. 전환·남은 작업
- RunPod Proxy readiness 회신 수령 여부:
- Nginx upstream / Host·SNI·URI·query·인증·상한·timeout 적용:
- 고정 도메인 health·readyz 결과 / 확인 시각:
- 개발자 직접 검증 담당자·일정·결과:
- 일반 사용자 분석 접수 상태·활성화 조건 / 원복 책임자:
- 미완료 항목 / RunPod 담당자에게 필요한 후속 작업:
```

운영 근거: [RunPod HTTP v1.1 보완 수행 기록](runpod_http_deployment_20260913.md). 원래 계획: [RunPod 서버 수정 계획](runpod_http_implementation_plan_20260913.md).

이번 요청서 작성 범위는 기존 계약·배포 기록·AWS 회신의 정리와 문서 정적 확인입니다. 이번 문서 작성에서 AWS·RunPod 설정 변경, 추가 서버 조회, 코드 수정, 테스트, commit/push/PR 변경 또는 배포는 수행하지 않았습니다.
