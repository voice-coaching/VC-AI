# 최근 음성 분석 실패와 FE–AWS–RunPod 연결 조사 — 2026-10-03

작업 유형은 운영 장애 조사·외부 연동 평가다. ?? ?? ?? ?? `GLOBAL_RULES.md`, `.codex/runpod.md`, `.codex/backend-http.md`, `.codex/env.md`와 VC-BE의 운영·외부 연동 규칙을 적용했다. 2026-10-03 23:21–23:38 KST에 Pod `s8hb5b0l5k4qlx`, AWS `alpha-backend.service`, VC-BE/VC-FE 소스와 운영 FE 자산을 **읽기 전용**으로 대조했다. 새 분석 요청, 서비스 재시작, 배포, 자동 QA는 수행하지 않았다.

## 현재 배포와 연결 확인

| 경계 | 확인 근거와 결과 |
|---|---|
| RunPod 대상 | 관리 API의 Pod ID·RUNNING·공개 IP·SSH 포트 매핑이 이번 요청의 접속 정보와 일치했다. GPU 1개, API 표시 비용 $0.49/h. |
| AWS Backend | 실행 중 JAR의 release `6393d76d5ccc4896edd168d743d865577374843e`는 원격 VC-BE `develop` SHA와 일치했다. `/opt/alpha/app.jar`의 실제 SHA-256이 `current.json`과 일치하고 배포 stage는 COMPLETE였다. 내부 health 200, 신규 접수 차단 marker 없음. |
| RunPod 분석 API | `/health`, `/readyz`, `/health/services`, `/health/canonical`, 인증된 `/health/deployment`가 모두 200이었다. canonical admission ON, activeJobs 0. 배포 AI source `5a1fba59aefb3fca24df0215534eabf0c0568d5b`, bundle `d31b79fdb2a131d45e16de9adcd27831149e98d30229cbc9990d3a5afccf7b04`. |
| AWS–RunPod 계약 | 양쪽 현재 manifest의 schema 9개를 파일명과 SHA-256으로 대조해 전부 일치했다. core/H5 pin도 현재 배포 기록의 scored v2/H5 v3와 일치한다. |
| FE–AWS | FE 운영 manifest의 revision은 VC-FE `main` `36a49772c3ddc3df00cd44542064c502953469ac`; 현재 Vercel JS 자산 18개가 manifest SHA와 일치했다. FE `/api/backend/api/analysis-capabilities`와 AWS 원본은 인증 없는 요청에 각각 401을 반환했다. 이 401은 보호 API의 인증 경계이며 분석 성공 증거는 아니다. |

현재의 liveness·readiness와 자산 일치는 **동시 요청의 접수 가능성, 실제 추론·GPT·callback, 브라우저 QA 통과를 보장하지 않는다.**

## 확인된 실패 원인: 단일 실행 슬롯과 유한 전송 재시도

AWS PostgreSQL과 `alpha-backend.service` 로그를 대조했다. 시각은 UTC이며 KST는 9시간 뒤다.

| 분석 | 접수 | 최종 상태 | RunPod 전송 |
|---|---|---|---|
| 67 | 13:47:27 | 13:53:43 COMPLETED | 즉시 PUBLISHED, 실패 0회 |
| 68 | 13:47:52 | 13:53:09 FAILED, `runpod_analysis_request_delivery_failed` | `runpod_http_429` 5회, outbox FAILED, Pod 실행 전 종료 |
| 69 | 13:52:14 | 14:01:25 COMPLETED | 429 2회와 503 1회 뒤 13:55:31 PUBLISHED, 실패 누적 3회 |

RunPod `HttpRuntime.accept()`는 canonical 작업이 활성 슬롯을 차지하면 `429 CAPACITY_EXCEEDED`를 반환한다. `RunPodAnalysisClient`는 429를 재시도 가능 오류로 바꾸지만, VC-BE `AnalysisRequestOutbox.recordDispatchFailure()`는 이를 일반 전달 실패 횟수에 포함한다. AWS 운영 설정은 최대 5회이며 실패마다 `min(300, 30 × attemptCount)`초 뒤 재시도한다. 긴 분석 하나가 처리되는 동안 다음 요청이 다섯 번 슬롯에 부딪히면, 음성 파일을 실행하지도 못하고 영구 FAILED가 된다. 분석 68에서 이 경로가 실제 발생했다. [운영 병목 조사](pipeline-bottleneck-analysis-20261003.md)는 성공한 분석 69의 접수부터 RunPod 실행 시작까지 198.1초, 전체 완료까지 552.972초를 계측했다.

13:53:56 UTC의 503은 AWS 전달 로그에서 확인했지만, 당시 RunPod의 정확한 `reasonCode`는 보존된 로그만으로 확정되지 않는다. `HttpRuntime.accept()`가 canonical 요청에도 legacy executor `ready()`를 요구하고 canonical 작업 뒤 legacy executor를 재기동하는 구조는 소스로 확인했다. 이 결합이 해당 503의 직접 원인인지는 별도 계측이 필요하다.

## 다른 오류와 현재 범위

- 최근 24시간 DB 집계는 COMPLETED 8건, FAILED 8건이다. 실패 7건은 `analysis_execution_timeout`, 1건은 위 전송 실패다. 53–57은 이전 journal/배포 문제 시기의 실패, 62–63은 scored v1의 Git provenance 확인 실패다. Pod의 62–63 `canonical.stderr`는 `_git_state`의 “Git commit을 확인할 수 없습니다”를 가리킨다. 64의 한 실행은 PREFLIGHT `CANONICAL_SCHEMA_INVALID`였다. 후속 scored v2/H5 v3 배포 뒤 64·65·67·69는 완료됐다. 과거 실패를 현재 모든 요청의 원인으로 일반화하지 않는다. [scored 복구 기록](testvideo1-scored-verification-20261003.md)을 참조한다.
- 07:09:46 UTC에는 AWS nginx의 분석 상태 GET 502 한 건과 Backend PostgreSQL 연결 오류 `08006`이 같은 시각에 있다. 하루 동안 Backend 로그에는 `08006` 12건, `57P01` 5건이 있었다. DB 연결 중단은 별도 가용성 위험이며 각 건의 DB 측 원인·사용자 영향은 이번 조회만으로 확정할 수 없다.
- 13:46:40 UTC에는 녹음 선택 PATCH가 400이었고 DB의 `uq_voice_recordings_one_selected_per_session` 위반이 기록됐다. 같은 시각대에 녹음 등록 두 건이 있었다. 중복 선택 요청인지 트랜잭션 순서 문제인지는 추가 요청별 조사 대상이다. 13:46:39의 분석 POST 503은 서비스 접수 불가 메시지였고, 현재 접수 차단 marker는 없다.
- 성공한 분석 69에서도 Backend 내부 callback `/result`는 최종 적용 전 503을 여러 번 반환했다. RunPod가 같은 본문을 재제출해 최종 200/완료에 도달했다. 현 계약의 재시도 동작이며, 후처리 지연과 추가 부하의 위험이다. Backend DB에는 만료된 PENDING/PROCESSING 분석이 현재 0건이었다.

## FE 계약과 남은 실패 가능성

운영 FE revision의 `analysis-polling.ts`, canonical API client, `/api/backend` proxy를 VC-FE 소스와 대조했다. 상태 GET의 실제 upstream 502/503/504·네트워크 오류만 같은 analysisId로 재시도하며, 인증 오류·계약 오류와 새 분석 POST는 자동 재시도하지 않는다. proxy는 upstream 상태와 `Retry-After`를 전달하고 자체 연결 실패는 503으로 반환한다. 이 경계에서 잘못된 API 경로·현재 계약 SHA 불일치는 발견하지 못했다.

FE는 연속 상태 조회 장애를 최대 90초, 전체 분석 대기를 10분으로 제한한다. AWS 실행 deadline 기본값은 15분이다. 실제 완료한 분석 69가 FE 프록시 요청부터 약 9분 15초 걸렸으므로, 조금 더 긴 대기열·후처리에서는 서버가 계속 분석 중인데 FE가 대기 시간 초과를 표시할 수 있다. FE에는 기존 분석을 다시 확인하는 경로가 있으나 첫 화면의 완료 인지는 놓칠 수 있다. 실제 사용자 브라우저에서 이 경로를 QA하지 않았다.

## 수정 우선순위와 확인 조건

1. **접수/전송:** 429 capacity busy를 영구 전달 실패 횟수와 분리하고, Backend deadline 안에서 공정하게 대기·재시도한다. 슬롯이 비는 즉시 다음 작업이 접수되며 장시간 정상 처리 중인 다른 작업 때문에 후속 분석이 FAILED로 끝나지 않아야 한다. 단순 최대 재시도 횟수 상향만으로는 대기 정책이 해결되지 않는다.
2. **RunPod readiness:** canonical 접수 가능 여부를 legacy executor 준비와 분리하고, 503의 고정 사유를 비밀 없이 관측할 수 있게 한다. 재기동 중에도 기존 작업의 lease·callback은 유지한다.
3. **사용자 대기:** FE의 10분 대기 한도와 Backend 15분 deadline 및 실제 큐 대기를 맞추고, 대기 만료를 서버 FAILED로 표현하지 않는다. 같은 analysisId를 조회하는 복구 경로를 수동 QA한다.
4. **별도 안정성:** 녹음 선택 23505와 PostgreSQL `08006`/`57P01`의 요청·DB 이벤트 상관관계를 조사한다. callback의 영속 접수와 최종 적용 응답을 분리하는 개선은 양쪽 계약·멱등성·FE 완료 의미를 함께 설계한다.

이번 조사는 로그·DB 최소 SELECT·현재 파일 hash·HTTP health·Git 소스의 정적 대조다. 새 실제 녹음, GPT 생성, 품질 QA, 자동 회귀 테스트, 재시작, 코드 변경, commit/push/PR, 배포는 하지 않았다. 제공된 키와 사용자 원문은 명령 출력·문서에 기록하지 않았다.
