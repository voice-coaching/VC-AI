# RunPod HTTP v1.1 보완 수행 기록

수행일: 2026-09-13. 사용자 요청: 보완계획 재검토 후 수행. AWS Backend·Nginx는 담당자 작업 범위다. 이 문서는 코드 구현, 운영 관측, 미완료 연결을 구분한다. 후속 관측은 아래에 갱신한다.

**2026-09-13 20:51 KST 당시 판정: 8080 서버·HTTP Expose·Proxy HTTPS 연결 및 재기동 복원 완료. 분석 readiness는 503이며 실제 분석 접수 활성화는 미완료다.** Clova는 200 ready, Seungun executor는 저장소 등 필수 설정 부재로 시작하지 않았다. AWS worker-readiness는 보유 callback 토큰으로 401이었다.

최신 운영 변경은 [2026-09-15 A40 이전 수행 기록](runpod_a40_migration_20260915.md)을 따른다. A40에서 파일 검증·자동 서버 기동은 확인했으나 중지 후 GPU 재할당이 거절돼 현재 원본·새 Pod 모두 중지 상태다. 위 과거 health 관측을 현재 실행 상태로 해석하지 않는다.

## 1. 구현과 배포 자료

- 구현 브랜치: `agent/runpod-http-v1_1`.
- 구현 커밋: `b4057a008ad45c89f540e4cb6e19b6f29cd53aed`. 로컬 commit 수행, push·Backend PR 변경 없음.
- 계약: `voice-coaching.runpod-http.v1.1`, [제어 Schema](contracts/runpod_http_control_v1.schema.json), [결과 Schema](contracts/runpod_result_v1.schema.json).
- 새 overlay SHA-256: `f2eac20f3f7a4f5543e19fa175d4d845f6478503c475179ea9cdc87c77c9c4ad`. 기존 overlay와 이번 변경을 합친 54개 파일을 hash로 관리한다.
- 이전 overlay 보존: Pod의 `/workspace/intelligentai/deployment/pre-http-b4057a008ad4`. 이전 sourceRevision `6618a396fb4d1c0d69ab76402bc2450a253be248`.
- 원본 release·모델 가중치·B2 자료는 변경하지 않았다. 기존 사용자 문서와 특허 검색 문서는 구현 커밋에서 제외했다.

| 구현 영역 | 반영 내용 |
|---|---|
| HTTP 서버 | `0.0.0.0:8080`, health·readyz·인증된 상세 상태, 분석 접수·executionId 조회·취소 |
| 입력·결과 | 별도 HTTP 입력과 JCS digest, Schema·명시적 null·UTF-16 길이·본문 상한 검증, HTTP 전용 결과 투영 |
| 실행 수명 | 제어 프로세스 1개·spawn executor 1개·대기 슬롯 0, 서버 lease·heartbeat·callback·취소·제한된 종료 캐시 |
| 공통 분석 | Redis authorize 진입과 `process_admitted` 분리, 기존 Seungun·파일 무결성·Clova fallback 재사용 |
| 영상 | HTTP 전용 미디어·실행 binding·handoff, 기존 미디어 처리·측정·calibration·선택 음소 보완 단계 재사용. 가짜 consent receipt·grant 없음 |
| 기동 | HTTP/Redis/disabled 서비스 구성 분리, root 전용 설정 복원, HTTP 자산 attestation, UID/GID 10001 실행 |
| 의존성 | jsonschema·rfc8785와 의존 wheel의 버전·SHA 고정. 별도 `http-python` 디렉터리로 오프라인 복원 |

실행/결과 객체는 기존 코어 내부 모델을 재사용하고 HTTP serializer가 새 계약으로 투영한다. 기존 Redis serializer를 HTTP에 사용하지 않는다. supervisor는 원래부터 loopback `/health`를 지원하므로 URL 허용 검증을 느슨하게 바꾸지 않았다.

계획의 선택 설정 중 listen `0.0.0.0:8080`, 실행 슬롯 1/대기 0, heartbeat 15초, 통신 3초/10초 상한은 이번 구현에서 v1.1 상수로 고정했다. `AI_ANALYSIS_HTTP_HOST`·`AI_ANALYSIS_HTTP_PORT`·슬롯/시간 관련 임의 env를 읽는 가변 parser를 추가한 것으로 보고하지 않는다. Backend가 반환한 lease·deadline으로 실제 남은 실행·통신 시간을 제한한다.

## 2. 배포 순서의 재검토

기존 boot는 컨테이너 주 프로세스의 자식으로 실행 중이었다. 이를 먼저 수동 종료하는 대신 새 overlay·원복본·의존성 캐시·비밀 공급원을 준비하고 Pod 설정 변경을 적용했다. 해당 PATCH 뒤 컨테이너가 재생성됐으므로 별도 restart/reset 요청을 반복하지 않았다. 내부 8080 확인은 이 복원 뒤 수행한다.

이전 전송 승인 검토가 소스 전송을 한 차례 거부했으나, 공식 관리 API로 계정 Pod와 SSH 목적지 일치를 재확인하고 비밀 파일 없는 소스 목록·hash를 제시한 뒤 같은 전송이 승인됐다. 현재 이 사유로 대기 중인 승인 요청은 없다.

## 3. 직접 확인한 운영 관측

| 시각(KST) | 대상과 결과 |
|---|---|
| 20:29:42 | 관리 API: `urwnuesv7o4fv6` RUNNING, IP `194.68.245.215`, SSH 22121, 기존 Expose는 `22/tcp` |
| 20:41:42 | PATCH 수락 뒤 GET: `22/tcp` 보존·`8080/http` 추가, `AI_ANALYSIS_TRANSPORT=runpod_http`, HTTP 비밀 공급원 존재 확인 |
| 20:42:36 | reset 뒤 관리 API의 SSH mapping이 22048로 변경. API의 lastStartedAt 필드는 이전 값이어서 재생성의 단독 근거로 사용하지 않음 |
| 20:43:52 | 새 컨테이너 PID 1 환경에서 HTTP 모드·비밀 공급원 존재 확인. `/opt` 배포 및 `/run` 설정은 복원 진행 중 |
| 20:44:59 | Proxy `/health` 502, 고정 도메인 `/health` 503. 서버 기동 완료 전 관측 |
| 20:44:59 | `GET https://api.voice-coaching.site/api/internal/ai/worker-readiness`: 로컬 callback 토큰으로 401. 토큰 불일치·JWT 필터·미배포 등의 정확한 원인은 AWS 확인 필요 |
| 20:45:47 | boot의 rsync 복사가 진행 중. 이 시점에는 8080 기동 완료로 보고하지 않음 |
| 20:48:10 | 실행 사본의 sourceRevision `b4057a0...`, overlay SHA `f2eac20...` 대조 완료 |
| 20:48:51 | 내부 8080 `/health` 200 alive, `/readyz` 503 not_ready. 인증된 상세 상태 503 `http_settings_incomplete_or_invalid`. HTTP env root 소유·0600, 자산 bootstrap ready, 방향별 토큰 존재 |
| 20:49:44~45 | 실제 Proxy HTTPS `/health` 200 alive, `/readyz` 503. 무인증 상세 상태 401, API 토큰을 넣으면 503 준비 상태까지 접근. 고정 AI 도메인 `/health`는 503 유지 |
| 20:51 이전 후속 확인 | Proxy `/health`·`/readyz`가 각각 JSON `{"status":"alive"}`·`{"status":"not_ready"}`를 반환함을 확인. 상세 상태 무인증 응답도 JSON `UNAUTHENTICATED` |
| 20:51:44 | Clova 내부 `/health` 200 ready, 배포 revision `b4057a0...`. supervisor의 api·clova 둘 다 liveness ready. 분석 `/readyz`와 구분 |

Proxy 주소는 `https://urwnuesv7o4fv6-8080.proxy.runpod.net`이다. 고정 URL `https://ai.voice-coaching.site`는 유지한다. 관측 중 Nginx upstream 설정은 변경하지 않았다.

## 4. 설정과 AWS 미완료 항목

PC에 이미 준비된 서로 다른 API_TOKEN·CALLBACK_TOKEN을 RunPod root 전용 파일에 주입하고, 같은 설정의 reset 복원 공급원을 Pod 환경에 등록했다. callback base URL은 `https://api.voice-coaching.site`다. 실제 값은 이 문서·소스·일반 archive에 기록하지 않는다. 이는 AWS 프로세스에 같은 토큰이 반영됐다는 증명이 아니다.

20:51:44 KST의 RunPod 실행 env에서 미설정으로 확인한 이름은 `AI_ANALYSIS_OBJECT_STORAGE_BUCKET`, `AI_ANALYSIS_OBJECT_STORAGE_REGION`, `AI_ANALYSIS_OBJECT_STORAGE_RECORDINGS_PREFIX`, `AI_ANALYSIS_READINESS_OBJECT_KEY`, `AI_ANALYSIS_READINESS_OBJECT_SHA256`, `AI_ANALYSIS_READINESS_OBJECT_SIZE_BYTES`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`다. SDK의 다른 credential 공급 방식을 사용할 경우 그 방법과 갱신을 별도로 확인한다. 키 값은 회신하지 않는다.

AWS 담당자는 다음 항목을 완료/미완료로 회신해야 한다.

1. 현재 배포 SHA와 v1.1 worker-readiness·claim·heartbeat·result의 배포 여부. 관측된 worker-readiness 401의 원인과 해결 결과.
2. 방향별 서비스 토큰의 일치·서버 인증 경로 설정 완료 여부. 값은 회신하지 않는다.
3. canonical 녹음 저장소 bucket·region·prefix·endpoint/path-style, RunPod 읽기 자격증명의 비공개 공급·갱신 경로.
4. canonical prefix 안의 작은 readiness 객체 key·SHA-256·크기와 읽기 권한.
5. ffmpeg·ffprobe·Python 경로 및 canonical 생성 준비. 일반 사용자 분석 접수는 양쪽 준비와 개발자 검증 전까지 닫아 둔다.

## 5. 확인 범위

수행한 확인은 Python 구문, 실제 scorer 환경에서 변경 모듈 import, JSON Schema 구조, 소스·wheel hash, 관리 API와 HTTP/SSH 운영 상태 확인이다. 실제 모델 분석 요청이나 자동 회귀 테스트는 실행하지 않았다. 음성·영상 실제 처리, callback DB 반영, 중복·취소·장애 복구와 피드백 품질은 개발자의 직접 QA가 필요하다.

원복은 접수 차단·Nginx preparing 유지 후 이전 overlay와 서비스 모드를 복원하는 방식이다. 현재 실행 중인 작업의 무효화·만료 처리는 Backend 담당자가 수행한다. 기존 Redis 설정도 미완료이면 분석 disabled로 원복한다. 포트를 되돌릴 때도 컨테이너 재생성 가능성을 고려한다.

## 6. AWS 담당자에게 전달할 현재 정보

```text
Pod ID: urwnuesv7o4fv6
내부 분석 HTTP 포트: 0.0.0.0:8080
HTTP Expose: 8080/http 적용 확인 (기존 22/tcp 보존)
실제 Proxy URL: https://urwnuesv7o4fv6-8080.proxy.runpod.net
배포 SHA: b4057a008ad45c89f540e4cb6e19b6f29cd53aed
관측: 2026-09-13 20:48~20:51 KST
liveness: GET /health → 200 {"status":"alive"}
분석 readiness: GET /readyz → 503 {"status":"not_ready"}
업무 API: POST /v1/analysis-jobs
상태 조회: GET /v1/analysis-jobs/{requestId}?executionId=...
취소: POST /v1/analysis-jobs/{requestId}/cancel
서버 인증: Authorization: Bearer <AI_ANALYSIS_API_TOKEN>
상세 상태: GET /health/services, 서버 인증 필요
방향별 토큰 RunPod 주입/재기동 복원: 완료, AWS와 일치 여부 미확인
AWS worker-readiness: 보유 callback 토큰으로 401, 담당자 인증/배포 확인 필요
저장소/readiness 객체/RunPod 읽기 credential: 미설정
Nginx: 준비 미완료 동안 preparing 503 유지
일반 사용자 분석 접수: 비활성 유지
실제 음성/영상·callback DB·복구·품질 QA: 개발자 후속 확인 필요
```

Nginx를 연결할 때 Host·SNI는 `urwnuesv7o4fv6-8080.proxy.runpod.net`으로 맞추고, URI·executionId query·Authorization을 보존한다. 담당자가 위 미완료 설정과 양방향 인증을 해결한 뒤 Proxy `/readyz` 200을 다시 확인한다. 고정 도메인 적용과 개발자 검증을 마친 뒤 분석 접수를 활성화한다.

## 후속 운영 관측 — 2026-09-14 Pod 중지

사용자의 명시적인 중지 요청에 따라 Pod `urwnuesv7o4fv6`를 중지했다. 위 2026-09-13 health·배포 관측은 과거 기록이며 현재 실행 상태를 의미하지 않는다.

- 변경 전 RunPod GraphQL 조회: 대상 이름 `intelligentai-a6000-int8-120gb-migration`, `desiredStatus=RUNNING`, GPU 1개, runtime uptime 86,365초.
- 2026-09-14 20:41:36 KST: `podStop` 성공, 대상 ID와 `desiredStatus=EXITED` 반환.
- 2026-09-14 20:42:15 KST: 별도 GraphQL 재조회에서 `desiredStatus=EXITED`, `runtime=null` 확인. 중지 직후 남아 있던 runtime 정보가 종료된 것을 확인했다.
- Pod 삭제·볼륨 삭제·AWS/Nginx 변경·코드 배포는 수행하지 않았다. API 키와 인증 응답 원문은 기록하지 않는다.

이 중지는 8080 분석 서버와 Clova를 포함한 해당 Pod의 실행을 종료한다. 다음 기동 시 실제 상태·설정·readiness를 다시 확인해야 한다.
