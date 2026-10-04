# canonical v5 영속 인계와 B2 후처리

상태: 코드 구현. 운영 배포·DB migration 실행·실제 추론·장애 QA·지연 재측정 전이다. GPT H5, 서버 rubric, Native/MFA 근거는 바꾸지 않는다.

## v4와 달라지는 완료 조건

v4는 B2 업로드와 exact-version readback, Backend VERIFIED receipt, callback 검증을 마쳐야 결과를 공개한다. testvideo1의 기존 보존/journal 85.2초와 receipt 29.5초는 이 대기 경로에 포함됐다. v5는 원본을 PostgreSQL에 먼저 인계하고 같은 frozen 의미 검증 후 결과를 공개한다. B2는 commit 이후 별도 archive worker가 처리한다. 이 수치가 그대로 단축된다는 측정 결과는 아직 없다.

버전 조합은 request `voice-coaching.runpod-analysis-request.v3`, profile `CANONICAL_HANDOFF_20261004_V5`, result `voice-coaching.runpod-analysis-result.v5`다. 기존 v4 schema bytes와 receipt 조건은 유지한다. v5에는 `retainedEvidence`가 없으며 가짜 `TrustedBackendVerifiedReceipt`를 만들지 않는다.

## 인계 계약

정본은 `docs/contracts/runpod_canonical_handoff_v1.schema.json`과 `runpod_result_v5.schema.json`이다. 내부 HTTP는 기존 callback Bearer와 `X-Worker-Instance-Id`로 인증한다.

| 메서드·경로 | 입력·응답 |
|---|---|
| POST `/api/internal/ai/analyses/{id}/handoffs` | JSON 최대 2 MiB. `metadata`, `handoffSha256`, `projection`, `inlineArtifacts` 모두 필수. 202와 snapshot |
| PUT `.../handoffs/{handoffId}/artifacts/{kind}` | `application/octet-stream`, 원본 최대 16 MiB. 동일 bytes 멱등 204 |
| POST `.../handoffs/{handoffId}/seal` | 빈 body. 전체 원본이 있어야 RECEIVED, 200 snapshot |
| GET `.../handoffs/{handoffId}` | 200 snapshot, 미등록 404. PENDING을 503으로 표현하지 않음 |

`metadata`에는 계약 버전, handoff/event/request/execution/worker UUID, analysis/recording/content 정수 ID, request/projection JCS SHA-256, artifact 배열이 들어간다. artifact는 kind/schemaVersion/raw SHA-256/byteSize이며 최대 5개다. core가 있으면 CORE·BRIDGE_RESULT·BINDING·ASSOCIATION은 필수이고 SELECTION_PROJECTION은 선택이다. core 이전 실패는 빈 배열과 명시적 FAILED projection을 사용한다. 모든 필드는 schema에 정의된 범위에서 필수이고 null을 허용하지 않는다.

`handoffSha256`은 metadata의 JCS SHA다. metadata의 projection digest와 각 원본 raw digest가 projection·원본을 결속한다. projection의 sourceIdentity가 core/H5/prompt/lock revision을 포함한다. `inlineArtifacts`는 kind→base64 원본 map이며 대형 경로에서는 빈 map이다. snapshot은 `handoffId`, `handoffSha256`, `state`만 반환한다. 상태는 STAGING/RECEIVED/VERIFYING/COMMITTED/REJECTED/REVOKED다.

인계 ID는 journal이 한 번 발급한 event ID를 재사용한다. 다른 event/digest는 409, 인증 실패 401, 형식·hash 불일치 422, 크기 초과 413, 용량 부족 429, DB 장애 503이다. 응답 유실 시 같은 인계를 GET으로 조정하고 동일 bytes만 재전송한다. 추론·GPT를 재실행하지 않는다.

## 소유권·결과 검증

RECEIVED transaction은 모든 원본과 verification 작업을 저장하고 `analysis_results.handoff_received_at`을 설정한다. 이 뒤 RunPod는 HANDED_OFF로 슬롯을 해제한다. 기존 worker 상태 API에서는 DELIVERING_RESULT로 표현하며 사용자 COMPLETED를 뜻하지 않는다.

Backend timeout sweeper는 인계된 실행에 옛 Pod heartbeat 만료를 적용하지 않는다. 최초 전체 deadline과 현재 시도·사용자·선택 녹음·취소/삭제 fence는 계속 적용한다. verifier는 DB 원본을 고립된 Python 프로세스에 프레임으로 전달한다. `VERIFY_HANDOFF`는 source·association·rubric·GPT 응답을 검증하며 B2를 읽지 않는다.

결과 원본 + analysis 완료 + cache effects + archive outbox를 같은 transaction에서 commit한다. 의미상 유효한 FAILED/REJECT/INCONCLUSIVE도 저장한다. 무효 근거는 REJECTED로 비공개 보존하고 현재 실행을 실패 처리한다. 검증기 일시 장애는 같은 원본으로 재시도한다. verifier claim은 2분이며 재시작 후 회수한다.

## B2 archive

별도 Backend writer는 원본 2개까지 병렬 업로드한다. PENDING → UPLOADING(intent) → VERIFYING(version checkpoint) → ARCHIVED 순서다. 알려진 버전은 exact-version GET만 재시도한다. PUT 응답 유실·intent 뒤 crash는 RECONCILE_REQUIRED이며 자동 재PUT하지 않는다. PUT 전 장애, 명시적 PUT 거절 응답(400/401/403/404/413/429), readback 장애는 5초부터 최대 약 5분 backoff+jitter로 재시도한다. 서버 오류·timeout을 명시적 거절로 간주하지 않는다. archive claim은 5분이다.

조정 API는 POST `/api/internal/ai/archives/{handoffId}/artifacts/{kind}/reconciliation`, body `{"versionId":"관측한 버전"}`이다. 기존 Pod callback token과 다른 운영 전용 Bearer를 사용한다. 서버가 key를 유도하고 원본 크기·SHA·AES256·정확한 version readback을 확인한 뒤에만 checkpoint한다. 임의 URL/key, 객체 삭제, 재업로드는 허용하지 않는다. 성공은 204, 없는 대상 404, 상태 충돌 409, 인증 실패 401이다.

archive 실패는 사용자 점수를 취소하거나 GPT를 재실행하지 않는다. raw bytes·checkpoint는 INDEFINITE 보존하고 자동 삭제/TTL을 추가하지 않는다. 실패 또는 취소된 미완성 STAGING 자료도 용량에 계속 포함한다.

## 공개 API와 FE

- GET `/api/analysis-capabilities/canonical`: 로그인 필요. `resultSchemas` 문자열 배열(0~2개)이 현재 신규 접수 가능한 v4/v5를 알려준다.
- GET `/api/analyses/{id}/result-contract`: 로그인·현재 소유 관계 확인. analysisId/recordingId/requestId/executionId/analysisProfile/resultSchemaVersion을 반환한다.
- GET `/api/v3/analyses/{id}`: v5 전용. 공개 view `voice-coaching.canonical-analysis-view.v2`, profile v5이며 안전한 코칭·점수·actions 구조는 v1과 같다. 기존 `/api/v2/analyses/{id}`는 v4 전용이다.

FE는 신규 접수 시 capability가 허용하면 v5를 선택한다. 재진입·이력 조회는 저장된 현재 profile을 조회하고 해당 parser/endpoint로 연결한다. routing/capability API가 없는 구 Backend의 404에만 v4 호환 경로를 사용한다. 인증·5xx·형식 오류로 다른 profile을 선택하지 않는다. archive 상태는 사용자 완료 조건이나 UI에 포함하지 않는다.

## 설정과 배포

Backend `analysis.canonical.handoff.*`:

| 설정 | 의미 |
|---|---|
| `worker-enabled`, `archive-enabled` | 영속 작업 소비. 명시적 true 필요 |
| `admission-enabled` | 신규 v5 접수. 기본 OFF |
| `spool-budget-bytes` | 85,065,728 이상, 16 GiB 이하 명시. 실제 원본+metadata+projection과 접수된 실행별 최대 예약량을 계산 |
| `retention` | `INDEFINITE` 필수 |
| `b2.endpoint`, `b2.region`, `b2.bucket`, `b2.prefix` | 전용 보존 대상 |
| `b2.writer-key-id`, `b2.writer-application-key` | 별도 Backend writer. 기존 reader/AWS 녹음 credential로 fallback하지 않음 |
| `reconciliation-token` | 32자 이상 운영 전용 token. Pod에 전달하지 않음 |

기존 semantic verifier·journal·cache effect 설정도 유지한다. B2 liveness는 v5 결과 readiness의 전제에서 제외하며 writer 설정·worker 가동·spool headroom을 신규 접수 조건으로 확인한다. DB/WAL/백업 용량은 운영자가 별도 준비해야 한다. 기존 v4 journal 용량은 기존 budget으로 유지한다.

순서: 새 verifier bundle 설치 → Backend V39와 JAR(worker ON, admission OFF) → RunPod overlay와 `AI_CANONICAL_HANDOFF_ENABLED=true` → FE parser 배포 → 개발자 수용 → Backend admission ON. v5 Pod는 B2 writer envelope를 읽지 않으며 v4 신규 admission을 끈다. 교체 전에 v4 실행을 drain한다. core/H5 패키지를 새로 변경하는 작업은 아니다.

양쪽 `/health/handoff`와 `/api/internal/ai/worker-readiness/handoff`가 기존 9개 + 신규 3개 schema SHA를 대조한다. verifier 설치 확인도 handoff 지원 버전을 확인한다. 설정을 켠 것만으로 운영 완료를 주장하지 않는다.

롤백은 Backend 신규 admission OFF부터 수행한다. 이미 접수된 request와 RECEIVED/COMMITTED archive는 계속 처리한다. 새 결과를 읽는 Backend/FE와 verifier를 유지하며 원본·테이블을 제거하지 않는다.

## 확인 범위와 남은 수용

에이전트는 소스·schema 정적 확인과 Python/Java/TypeScript 컴파일만 수행한다. 자동 회귀 테스트·fixture·브라우저 QA·실제 요청 실행은 하지 않는다. 개발자는 인계 응답 유실, B2 장애, PUT 후 재시작, 취소/재시도 세대 교체, 기존 v4 이력, testvideo1 점수·warm latency를 확인해야 한다. 최대 활성 추론 1개는 유지하며 P2의 추론/GPT 두 실행 겹치기는 이번 인계 구현과 별도다.
