> Current analysis API: [v5-only migration](../canonical-integration/v5-only-migration-20261004.md). Earlier v4 entries below are legacy documentation.

# 엔드포인트 목록 - intelligentAI

이 문서는 목록 전용이다. Request/Response 상세를 작성하지 않는다.

## 현재 운영 — 2026-09-30 01:38~01:44 KST

AWS의 기존 호출 주소는 `https://ai.voice-coaching.site`다. RunPod 8082 gateway를 통해 분석 8080과 독립 한국어 TTS 8083으로 분기한다. [관측 근거](../runpod-current-pipeline.md), [상세 명세](specification.md)

| Method | URL | Auth | 설명 |
| --- | --- | --- | --- |
| GET | /gateway/health | public | gateway 자체 alive |
| GET | /health | public | 분석 API alive |
| GET | /readyz | public | 분석 전체 준비 상태 |
| GET | /health/services | 분석 Bearer | 분석 의존성·provider·model·revision |
| POST | /v1/analysis-jobs | 분석 Bearer | 비동기 작업 접수 |
| GET | /v1/analysis-jobs/{requestId}?executionId={executionId} | 분석 Bearer | 해당 실행의 상태 |
| POST | /v1/analysis-jobs/{requestId}/cancel | 분석 Bearer | 해당 실행 취소 요청 |
| GET | /health/live | TTS Bearer | 독립 TTS liveness |
| GET | /health/ready | TTS Bearer | 독립 TTS 준비 상태 |
| POST | /v1/tts/synthesize | TTS Bearer | 한국어 MP3 생성 |

분석·TTS 토큰은 별개다. 아래 연구/검수 경로와 `/docs`, `/openapi.json` 등은 현재 8082 gateway 공개 API가 아니다.

## 연구·loopback 검수 — 현재 운영 gateway와 별개

| Method | URL | Auth | 설명 |
| --- | --- | --- | --- |
| GET | /health | public | 상태 확인 |
| POST | /v1/oracle-attempts | research-only | 정답 라벨로 진단·코칭 계약을 검증 |
| POST | /v1/closed-loop/oracle-attempts | research-only | 정답 라벨로 stateless 코칭·재녹음 전이 계약을 검증 |
| GET | / | loopback | T43/I30 전문가 검수 GUI shell |
| GET | /assets/styles.css | loopback | 검수 GUI stylesheet |
| GET | /assets/app.js | loopback | 검수 GUI application |
| GET | /api/v1/review-session | `X-Review-Token` | 현재 profile과 완료 진행률 |
| GET | /api/v1/review-items | `X-Review-Token` | hash 기반 검수 항목 목록 |
| GET | /api/v1/review-items/{item_hash} | `X-Review-Token` | 선택 항목의 interval·기존 판정 |
| GET | /api/v1/review-items/{item_hash}/audio | `X-Review-Token` | 제한 WAV의 loopback stream |
| POST | /api/v1/review-items/{item_hash}/review | `X-Review-Token` | 최초 판정 원자 저장 |
| PUT | /api/v1/review-items/{item_hash}/review | `X-Review-Token` | 기존 판정 원자 교체 |


## Canonical v5 handoff

| Method | Path | Authentication | Purpose |
|---|---|---|---|
| GET | `/health/handoff` (RunPod) | API Bearer | Executor configuration and schema digests |
| GET | `/api/internal/ai/worker-readiness/handoff` | Callback Bearer | Backend verification readiness |
| POST | `/api/internal/ai/analyses/{id}/handoffs` | Callback Bearer + worker ID | Durable original handoff |
| PUT | `/api/internal/ai/analyses/{id}/handoffs/{handoffId}/artifacts/{kind}` | Callback Bearer + worker ID | Stage raw artifact |
| POST | `/api/internal/ai/analyses/{id}/handoffs/{handoffId}/seal` | Callback Bearer + worker ID | Transfer ownership atomically |
| GET | `/api/internal/ai/analyses/{id}/handoffs/{handoffId}` | Callback Bearer + worker ID | Reconcile delivery outcome |
| POST | `/api/internal/ai/archives/{handoffId}/artifacts/{kind}/reconciliation` | Separate operations Bearer | Verify an observed B2 version |
| GET | `/api/analysis-capabilities/canonical` | User login | Available submission contracts and supported analysis scopes |
| GET | `/api/analyses/{id}/result-contract` | User login + ownership | Current attempt contract |
| GET | `/api/v3/analyses/{id}` | User login + ownership | Committed v5 public result |

## Direct analysis (separate service, default OFF)

Implemented in an isolated worktree on 2026-10-05; not deployed. [Contract and rollout prerequisites](../canonical-integration/direct-ingress-implementation-20261005.md).

| Method | Path | Authentication | Purpose |
|---|---|---|---|
| POST | `/api/v1/analyses` | None | Persist raw WAV and enqueue UUID job |
| GET | `/api/v1/analyses?clientAttemptId=UUID` | None | Recover admission after lost response |
| GET | `/api/v1/analyses/{jobId}` | None | State and inline result |
| GET | `/api/v1/analyses/{jobId}/events` | None | SSE with inline result |
| POST | `/api/v1/analyses/{jobId}/cancellations` | None | Cancel queued/running job |
| GET | `/readyz` | None | Direct Native execution readiness |
| POST | `/api/internal/ai/direct-analysis-history` (AWS) | Callback bearer | Background history event |
| POST | `/api/direct-analysis-history/{jobId}/link` (AWS) | Existing user login | Link history after displaying result |
| GET | `/api/direct-analysis-history/{jobId}` (AWS) | Existing user login | History save state |
| GET | `/api/direct-analysis-history` (AWS) | Existing user login | Independent practice history |
