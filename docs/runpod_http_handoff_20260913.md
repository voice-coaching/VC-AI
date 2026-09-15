# RunPod HTTP 연결 담당자 인계

관측: 2026-09-13 07:45:28–07:45:30 UTC (16:45 KST). 현재 배포본의 운영 상태이며 HTTP 분석 API 구현 완료 기록이 아니다.

**후속 관측 2026-09-13 19:28–19:29 KST:** AWS 공개 OpenAPI 200, AI 고정 도메인 health/readyz 503. 기존 Pod는 실행 중이고 Clova ready이지만 8080 listener·HTTP Expose·HTTP 전용 env 파일은 없다. 상세 시각·범위와 확정 DTO는 [HTTP 계약 확정 회신](runpod_http_contract_v1_20260913.md)에 기록했다. 아래 최초 관측 시각을 새 시각으로 덮어쓰지 않는다.

**AWS 담당자에게 전달할 통합 문서:** [AWS 담당자 작업 요청서 — RunPod HTTP 분석 연동](aws_runpod_http_work_order_20260913.md). Backend 구현·저장소·인증·Nginx·배포 순서와 회신 양식을 한 문서로 제공한다. 아래 내용은 기존 운영 관측 근거다.

## 현재 연결 정보

| 항목 | 확인 결과 |
|---|---|
| Backend base URL | `https://ai.voice-coaching.site` — 로컬 `.env`의 `RUNPOD_ENDPOINT_URL` 반영 완료 |
| Pod ID | `urwnuesv7o4fv6` |
| Pod 상태 | RUNNING, NVIDIA RTX A6000 1개 |
| 실제 배포 revision | `6618a396fb4d1c0d69ab76402bc2450a253be248` |
| 분석 접수 HTTP 포트 | 현재 배포본에 없음 |
| 기존 Clova 내부 주소 | `http://127.0.0.1:8000` — 내부 설명 생성 서버 |
| RunPod 노출 포트 | `22/tcp`만 설정됨. HTTP Expose 미설정 |
| 실제 분석 RunPod Proxy URL | 현재 없음. 포트 번호를 임의로 지정한 URL을 upstream으로 사용하지 않음 |

## 실제 확인한 응답

| 대상 | 결과 | 의미 |
|---|---|---|
| Pod 내부 `GET /health` (8000) | HTTP 200, `status: ready`, `quantization: bnb-int8` | Clova 모델 적재 및 HTTP 서버 준비 완료 |
| Pod 내부 `GET /health/services` (8000) | HTTP 503, `ready: false` | 전체 서비스는 준비되지 않음 |
| 분석 worker 상태 | `blocked`, `environment_file_missing` | 기존 Redis worker의 설정 파일 없음 |
| Pod 내부 `GET /openapi.json` (8000) | HTTP 200 | 등록 경로는 `GET /health`, `GET /health/services`, `POST /v1/chat/completions` |
| 고정 주소 HTTP `/` | HTTP 301 → HTTPS 동일 주소 | HTTPS 전환 확인 |
| 고정 주소 HTTPS `/health` | HTTP 503 | 외부 연결은 응답하지만 분석 서비스 준비 완료는 아님 |

`POST /v1/analysis-jobs`는 현재 원격 배포 소스와 실행 서버의 OpenAPI에 없다. 해당 경로는 [HTTP 수정 계획](runpod_http_callback_revision_plan_20260910.md)의 제안이다. Clova의 `/v1/chat/completions`를 분석 접수 API로 대체할 수 없다.

## Nginx 담당자에게 전달할 사항

현재 준비 중 503 응답을 유지한다. 연결할 분석 HTTP 서버가 없으므로 적용 가능한 실제 upstream URL은 아직 제공할 수 없다. 기존 내부 Clova의 8000 포트를 분석 접수 서버로 연결하지 않는다.

분석 HTTP 구현 위치와 배포본을 확정한 뒤 실제 수신 포트, HTTP Expose, HTTPS Proxy URL, 전체 분석 readiness 경로 및 응답, 서버 인증 방식을 확인하여 전달해야 한다. `POST /v1/analysis-jobs`는 구현·배포 후 실제 계약을 대조한다. Pod 교체 후에는 고정 도메인을 유지하면서 Nginx upstream과 HTTPS Host/SNI를 새 Proxy 호스트에 맞춰 갱신해야 한다.

## 이번 실행 범위

- 기존 Pod 시작, 사용자 승인에 따른 로컬 SSH 공개키 추가 및 기존 키 보존, 컨테이너 재기동 후 SSH 접속 확인.
- 실제 배포 revision, GPU, 수신 포트, 헬스와 OpenAPI 확인.
- 추론 생성, 실제 녹음 처리, 피드백 품질 QA는 수행하지 않음. Clova readiness를 추론 품질 또는 전체 분석 성공으로 해석하지 않음.
- HTTP Expose 및 외부 Nginx 설정은 변경하지 않음. Backend 코드 변경, commit/push/PR, HTTP 분석 API 배포는 수행하지 않음.
- Pod는 실행 상태로 유지. 시작 전후 API에서 조회한 Pod 요금은 시간당 USD 0.53이며 최종 청구액 확인은 아님.

## VC-BE develop 추가 대조

2026-09-13 원격 `develop`을 별도 검토 clone으로 읽었다. 확인 SHA는 `f7db547900617f300a99b7d29e9ec65ee2b3f133`이며 검토 clone은 clean 상태였다. 이 SHA가 현재 AWS에 실행 중인 버전인지는 확인하지 못했다.

**결론: 이 develop에는 AWS Backend → RunPod HTTP 전송이 구현되어 있지 않다.** 구현된 전송은 Backend outbox → Redis request Stream → worker → Redis result Stream → Backend 결과 반영이다. 이 코드 경로가 있다는 사실은 실제 Redis 연결 성공을 의미하지 않는다.

| 확인 항목 | 소스 근거와 영향 |
|---|---|
| 사용자 분석 요청 | [TrainingAnalysisController](https://github.com/voice-coaching/VC-BE/blob/f7db547900617f300a99b7d29e9ec65ee2b3f133/src/main/java/org/example/voice/training/controller/TrainingAnalysisController.java#L22)의 `POST /api/training-sessions/{sessionId}/analyze`. 사용자 → Backend 경로이며 RunPod 수신 API가 아님 |
| 분석 전송 선택 | [application.yaml](https://github.com/voice-coaching/VC-BE/blob/f7db547900617f300a99b7d29e9ec65ee2b3f133/src/main/resources/application.yaml#L92)의 `ANALYSIS_STREAM_ENABLED` 기본값은 false |
| 전송 비활성 동작 | [DisabledAnalysisJobPublisher](https://github.com/voice-coaching/VC-BE/blob/f7db547900617f300a99b7d29e9ec65ee2b3f133/src/main/java/org/example/voice/analysis/infrastructure/stream/DisabledAnalysisJobPublisher.java#L16)는 `ANALYSIS_INTEGRATION_UNAVAILABLE`(503)을 발생시킴. 같은 조건의 DisabledAnalysisAuthorizationIssuer도 존재 |
| 요청 발행 | [RedisAnalysisRequestPublisher](https://github.com/voice-coaching/VC-BE/blob/f7db547900617f300a99b7d29e9ec65ee2b3f133/src/main/java/org/example/voice/analysis/infrastructure/stream/RedisAnalysisRequestPublisher.java#L14)의 `XADD`. outbox dispatcher가 이 publisher를 직접 호출 |
| 결과 수신 | [RedisAnalysisResultConsumer](https://github.com/voice-coaching/VC-BE/blob/f7db547900617f300a99b7d29e9ec65ee2b3f133/src/main/java/org/example/voice/analysis/infrastructure/stream/RedisAnalysisResultConsumer.java#L78)가 Stream을 읽고 ingestionService에 전달 |
| HTTP client 및 callback | 추적 파일 전체와 분석 구현 소스에서 `RUNPOD_ENDPOINT_URL`, `/v1/analysis-jobs`, `AI_ANALYSIS_API_TOKEN`, `/api/internal/ai`를 사용하는 구현 없음. HTTP 결과 callback·claim·heartbeat controller 및 전용 서버 토큰 인증도 없음 |
| capability | AnalysisCapabilitiesReaderImpl은 저장소·정규화·Stream 활성화 설정을 판단하며 RunPod HTTP health를 호출하지 않음 |
| 기존 내부 계약 | Redis 경로에는 HMAC grant·consent policy 검증이 남아 있음. 이를 새 HTTP 연결의 필수 설정으로 추가하지 않고, [기존 HTTP 전환 계획](runpod_http_callback_revision_plan_20260910.md)에 따라 전송 경계를 분리해야 함 |

따라서 로컬 `.env`의 `RUNPOD_ENDPOINT_URL=https://ai.voice-coaching.site`는 연결용 주소를 보관한 상태이며, 현재 develop은 이를 소비하지 않는다. 이 파일 변경이 AWS Backend 프로세스에 자동 적용되는 것도 아니다. Nginx 연결이나 DNS 설정만으로 누락된 양쪽 HTTP 코드를 대체할 수 없다.

### AWS 공개 endpoint 관측

[deploy.sh](https://github.com/voice-coaching/VC-BE/blob/f7db547900617f300a99b7d29e9ec65ee2b3f133/scripts/deploy.sh#L16)의 public health 대상 `https://api.voice-coaching.site/v3/api-docs`를 조회했다. 두 번 모두 HTTP 502였으며 마지막 확인은 **2026-09-13 07:53:21 UTC (16:53 KST)**다. 공개 endpoint의 오류를 관측한 것이며, AWS 내부 프로세스 상태·실행 JAR SHA·환경변수·502 원인은 미확인이다. 기존 RunPod 관측 시각은 이 조회로 갱신하지 않는다.

### 남은 작업

1. AWS 담당자가 `alpha-backend` 서비스, Nginx upstream, 내부 Actuator와 실제 배포 SHA를 확인해 공개 502 원인을 진단한다.
2. HTTP 방식을 유지하려면 Backend HTTP publisher와 서버 인증·callback·작업 수명 처리, RunPod 분석 접수 서버 및 실행 루프를 구현하고 양쪽 계약을 맞춘다.
3. 분석 접수 서버의 실제 포트·인증·readiness를 확정한 뒤 HTTP Expose와 Nginx upstream을 적용한다.
4. Backend는 develop PR 및 담당자 배포, RunPod는 별도 배포로 인계하고 이후 실제 녹음 처리는 개발자가 검증한다.

이번 대조는 소스 정적 검토와 공개 GET 상태 확인만 수행했다. Backend 코드 수정·빌드·자동 테스트·배포·AWS SSH 조작은 수행하지 않았다.

## 후속 RunPod 수정 계획

사용자 지시에 따라 AWS 수정은 담당자가 진행한다. [RunPod HTTP 분석 서버 수정 계획](runpod_http_implementation_plan_20260913.md)에 코드 변경 대상, 8080 포트와 `/readyz` 제안, claim·heartbeat·결과·취소 계약, 설정 공급원, 배포·원복 순서와 개발자 완료 기준을 정리했다. 계획의 신규 포트·API·파일은 아직 구현·배포된 항목이 아니며, 위 운영 관측을 새 완료 상태로 대체하지 않는다.
