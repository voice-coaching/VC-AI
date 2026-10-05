# FE → RunPod 직접 분석 구현

2026-10-05. 상태: 격리 작업본 구현·컴파일 확인, 미배포. 작업 유형: 외부 연동 구현·서비스 로직·UI 경계·문서화. 기존 Native 전용 DRAFT 변경 위에 구현했다. Native 승인 자료를 만들거나 구버전 Seungun fallback을 추가하지 않았다.

## 확정된 경계

사용자의 최종 결정에 따라 공개 RunPod 접수·조회·SSE·취소에 사용자 인증, 서버 토큰, 인증 게이트웨이를 두지 않는다. 브라우저는 Authorization/Cookie 없이 직접 HTTPS 요청한다. CORS는 브라우저 origin 설정이며 인증 수단이 아니다. jobId 또는 clientAttemptId를 아는 호출자는 결과를 조회할 수 있고 jobId로 취소할 수 있다.

`FE → RunPod 접수 → 로컬 큐 → Native/GPT·근거 검증 → 로컬 결과 확정 → SSE 결과 표시`

`RunPod → 별도 archive worker → B2 근거·기존 녹음 저장소`

`RunPod → 별도 history worker → AWS direct history inbox`

AWS claim/heartbeat/reserve/DB/outbox/결과 ACK, B2 보관, AWS 독립 검증은 직접 분석 결과 공개의 선행조건이 아니다. 로컬 파일 무결성·승인된 모델/하네스·근거/점수 검증은 분석 계산 자체의 일부로 유지한다. API 진입점을 바꾸어도 Native/MFA 계산 시간이 자동으로 줄어드는 것은 아니다.

## 공개 계약

별도 프로세스 `scripts/serve_direct_analysis.py`, 기본 localhost:8083, 단일 supervisor/worker. 기존 내부 v5 서버의 인증 정책은 변경하지 않는다.

| API | 의미 |
|---|---|
| POST `/api/v1/analyses` | raw PCM WAV를 영속 접수한 후 202와 현재 view 반환 |
| GET `/api/v1/analyses?clientAttemptId=UUID` | 응답 유실 시 같은 시도를 조회; 없으면 404 |
| GET `/api/v1/analyses/{jobId}` | 상태와 준비된 결과 반환 |
| GET `/api/v1/analyses/{jobId}/events` | SSE `analysis`, sequence를 id로 사용, 완료 이벤트에 결과 포함 |
| POST `/api/v1/analyses/{jobId}/cancellations` | 큐 취소 또는 실행 종료 요청. 완료된 결과는 취소로 덮어쓰지 않음 |
| GET `/readyz` | direct 실행 준비 상태; Native 미승인/접수 OFF이면 503 |

POST는 `Content-Type: audio/wav`, `X-Analysis-Metadata: base64(UTF-8 JSON)`을 사용한다. metadata는 `clientAttemptId`(UUID), `scriptText`(1–4000자), `contentId`(양의 정수 또는 null), `historyClaimSha256`(64자리 소문자 hex)이다. 음성은 16kHz·mono·16bit PCM, 0.5–180초, 최대 20MiB다. 헤더는 24000자, 업로드는 60초로 제한한다. digest는 실제 수신 바이트에서 서버가 계산한다. 임의 URL 다운로드는 없다.

historyClaimSha256은 접수 권한을 검사하는 토큰이 아니다. FE가 생성한 임의 값의 digest를 저장했다가 **결과 표시 후 AWS 계정 이력을 연결**할 때만 사용한다. raw 값은 RunPod로 보내지 않는다. RunPod가 익명 요청의 userId를 AWS 계정 소유자로 믿는 동작은 없다.

공개 envelope는 `voice-coaching.direct-analysis.v1`: jobId, executionId, clientAttemptId, status, sequence, createdAt, updatedAt, archiveStatus, historySyncStatus, failure, result. RESULT_READY 전 result=null. 결과는 decision, coreStatus, score, coaching, selection, sourceIdentity, failure다. 기존 9항목 점수와 후보별 scoped evidence ID·MFA 위치를 보존한다. transport의 UUID를 AWS 숫자 ID로 위장하지 않는다.

오류는 404(없음), 409(같은 시도에 다른 입력), 408(업로드 시간초과), 413(크기), 415(WAV 형식), 422(입력), 429(접수 용량), 503(준비/저장 공간)이다. 사용자 인증 오류는 없다. 같은 clientAttemptId와 같은 입력은 같은 job이며 새 분석을 자동 생성하지 않는다.

## 내구성·실행·복구

- `JobStore`: POSIX flock으로 supervisor 하나만 허용한다. audio/job/result/checkpoint는 temp→fsync→rename→directory fsync로 저장한다. 메모리 색인은 영속 job 파일에서 시작 시 복원한다. 상태 조회마다 전체 파일을 다시 읽지 않는다.
- 분석은 QUEUED/RUNNING/RESULT_READY/FAILED/CANCELLED. 동시 실행 1, 접수 중 작업 최대 3, 실행 deadline 900초, spool 2GiB/1000건이다. 접수 때 용량을 검사한다. 진행 중 산출물의 실제 디스크 사용량도 운영에서 모니터링해야 한다.
- 로컬 producer-started 기록은 한번만 생성한다. 통신 오류·후처리 실패에서 Native/GPT를 재실행하지 않는다.
- 재시작 시 완료 checkpoint가 있으면 결과를 복원한다. 완료를 확인하지 못한 RUNNING은 FAILED로 마감하고 `recovery-required.json`이 신규 실행을 차단한다. 운영자가 이전 프로세스 종료를 확인한 후 표식 제거·재기동한다. systemd는 KillMode=control-group이다.
- 취소/시간초과는 worker process group과 resident를 정리한다. 접수 OFF에서도 기존 결과 조회·후처리는 유지한다.
- 현재 자동 TTL 삭제와 tombstone/410 만료 API는 구현하지 않았다. 공간 한도 도달 시 신규 접수를 거절하며 미보관 자료를 자동 삭제하지 않는다. 장기 운영 전 보관 완료 자료의 청소·계정 삭제 연동을 별도 검증해야 한다.

## 결과 이후 저장

두 후처리 lane은 로컬 RESULT_READY 작업에서 독립적으로 진행한다. 지수 backoff와 jitter, 최대 300초 간격으로 재시도한다. 기록된 artifact별 B2 version/digest를 재사용하며 새 추론은 하지 않는다. B2 PUT 후 해당 version GET digest를 확인한다. 녹음은 기존 사용자 녹음 bucket의 전용 prefix에 PUT/HEAD한다.

AWS에는 callback bearer로 `voice-coaching.analysis-history-event.v1`을 POST한다. 이벤트는 job/execution/attempt, 입력 digest, 원고, content 참조, result와 원본 result digest, claim digest, archive 참조를 포함한다. 공개 결과는 B2 완료 이전에도 전달하며 이후 archive 참조를 보충한다. 실제 archive를 포함한 이벤트가 ACK된 때만 history COMPLETE다. 409/422는 CONFLICT로 남기고 결과는 유지한다.

AWS V40은 별도 direct history와 사용자 연결 테이블을 추가한다. 기존 analysis_results/training_sessions에 가짜 항목을 만들지 않는다. FE는 결과를 먼저 표시한 후 기존 로그인으로 raw historyClaim을 AWS에 전송한다. AWS가 저장한 digest와 일치하는 계정 연결만 이력 목록에 보인다. 브라우저가 연결 전에 닫히면 RunPod의 보관·이벤트 전송은 계속되고 계정 연결은 같은 브라우저의 재진입 때 재개된다.

첫 범위는 독립 음성 연습이다. courseId/courseStepId/titleExamId/기존 sessionId가 있는 요청은 기존 경로로 계속 처리한다. direct 이력은 별도 구역에 표시하며 기존 과정 완료·연속 학습일·시험 집계를 갱신하지 않는다.

## 파일과 설정

| 경로 | 책임 |
|---|---|
| `src/voice_coach/direct_analysis/api.py` | 공개 HTTP/SSE·업로드 transport |
| `domain.py`, `service.py`, `store.py` | UUID 입력·실행 수명·영속 작업 |
| `worker.py` | 로컬 journal로 Native/GPT 실행·검증·공개 결과 생성 |
| `delivery.py` | 독립 B2/녹음 보관·AWS 전송 |
| `backend_analysis/canonical_producer.py`, `canonical_result.py` | journal port 및 direct identity 연동 |
| `deploy/runpod/direct-analysis.*` | 별도 service/env/HTTPS reverse proxy 예시 |

AI: `AI_DIRECT_ENABLED=false`, `AI_DIRECT_SPOOL_ROOT`, `AI_DIRECT_ALLOWED_ORIGINS`, `AI_DIRECT_HISTORY_BASE_URL`, `AI_DIRECT_RECORDING_PREFIX`. 기존 승인 Native/core/H5·NLTK·OpenAI·B2·녹음 저장소·callback 비밀은 운영 env로만 공급한다. FE 번들에는 공개 HTTPS origin과 기능 flag만 둔다. BE `ANALYSIS_DIRECT_HISTORY_ENABLED=true`, FE `NEXT_PUBLIC_DIRECT_ANALYSIS_ENABLED=true`, `NEXT_PUBLIC_DIRECT_ANALYSIS_ORIGIN`은 검증 후 설정한다.

## 검증과 남은 배포 단계

Python 소스 컴파일, TypeScript noEmit, Java compileJava, diff/문서 정적 확인만 수행했다. 저장소 지침에 따라 자동 회귀 테스트·브라우저 QA·실제 추론을 실행하지 않았다. 실제 PostgreSQL V40 적용, POSIX 볼륨 복구, HTTPS/SSE/CORS, 저장 장애 복구는 개발자 확인 대상이다. 수치 latency 개선을 아직 측정하지 않았다.

Native 정책은 DRAFT이고 승인 실행 allowlist가 비어 있다. 따라서 이 작업본의 신규 추론은 readiness가 열리지 않는다. 승인된 보정·모음 매핑·artifact와 개발자 품질 확인이 먼저 필요하다. 구버전 scored core fallback은 없다. 이후 접수 OFF 배포 → 볼륨/저장 권한/모델 readiness → BE 이력 기능 → FE 공개 origin 빌드 → 신규 접수 순으로 활성화한다. 사용자 후속 요청에 따라 이 구현과 기존 Native 변경을 커밋·푸시하여 검토용 Draft PR로 인계한다. 병합·운영 배포는 수행하지 않는다.
