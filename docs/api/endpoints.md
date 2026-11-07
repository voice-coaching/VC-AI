# 분석 API endpoint 목록

현재 소스 계약이며 배포 완료 선언이 아니다. 상세는 [API 명세](specification.md),
전환 조건은 [v6 통합](../canonical-integration/v6-unification.md)을 따른다.

분석 API의 요청 처리 오류는 실제 HTTP 4xx·5xx와 JSON으로 응답한다. 내부·Direct의
기존 오류 본문 형식과 비동기 상태 조회 계약은 유지한다.
[오류 응답 상세](specification.md#http-오류-응답)를 참조한다.

| Method | Path | 인증 | 목적 |
|---|---|---|---|
| GET | `/gateway/health` | 없음 | AWS gateway liveness |
| GET | `/health` | 없음 | 분석 liveness |
| GET | `/readyz` | 없음 | 내부 분석 readiness |
| GET | `/health/services` | Analysis Bearer | 의존성·실행기·인계 readiness |
| GET | `/health/deployment` | Analysis Bearer | 설치 identity·진행 작업 수 |
| GET | `/health/handoff` | Analysis Bearer | 공통 v6/handoff v2·지원 학습 유형·11개 digest |
| GET | `/health/audiovisual` | Analysis Bearer | v6 영상 실행·지원 학습 유형·자산·admission |
| POST | `/v1/analysis-jobs` | Analysis Bearer | 음성·음성영상 request v4/result v6 신규 접수 |
| GET | `/v1/analysis-jobs/{requestId}?executionId={executionId}` | Analysis Bearer | 해당 실행 상태 |
| POST | `/v1/analysis-jobs/{requestId}/cancel` | Analysis Bearer | 해당 실행 취소 |
| GET | `/direct/readyz` | 없음 | 익명 direct 준비 상태 |
| POST | `/direct/api/v1/analyses` | 없음 | WAV·metadata를 UUID 작업으로 접수 |
| GET | `/direct/api/v1/analyses?clientAttemptId=UUID` | 없음 | 접수 응답 유실 조정 |
| GET | `/direct/api/v1/analyses/{jobId}` | 없음 | 작업·inline direct 결과 |
| GET | `/direct/api/v1/analyses/{jobId}/events` | 없음 | SSE 결과 |
| POST | `/direct/api/v1/analyses/{jobId}/cancellations` | 없음 | 익명 작업 취소 |
| GET | `/health/live` | TTS Bearer | 독립 TTS liveness |
| GET | `/health/ready` | TTS Bearer | 독립 TTS readiness |
| POST | `/v1/tts/synthesize` | TTS Bearer | 독립 음성 합성 |

## 별도 Backend 저장소의 연동 경계

아래는 Backend 적용·확인이 필요한 경계다. 이 저장소 변경만으로 Backend의 구현·
배포를 보고하지 않는다. 내부 handoff는 v2, 신규 내부 결과는 v6를 사용해야 한다.

| Method | Path | 인증 | 목적 |
|---|---|---|---|
| GET | `/api/internal/ai/worker-readiness/handoff` | Callback Bearer | 공통 v2 지원·11개 digest |
| GET | `/api/internal/ai/worker-readiness/audiovisual` | Callback Bearer | 같은 v2 계약과 영상 지원 |
| POST | `/api/internal/ai/analyses/{id}/handoffs` | Callback Bearer + worker ID | v2 원본 인계 |
| PUT | `/api/internal/ai/analyses/{id}/handoffs/{handoffId}/artifacts/{kind}` | Callback Bearer + worker ID | 원본 staging |
| POST | `/api/internal/ai/analyses/{id}/handoffs/{handoffId}/seal` | Callback Bearer + worker ID | 검증·소유권 인계 |
| GET | `/api/internal/ai/analyses/{id}/handoffs/{handoffId}` | Callback Bearer + worker ID | 동일 인계 상태 조정 |
| POST | `/api/internal/ai/direct-analysis-history` | Callback Bearer | direct 이력 비동기 저장 |
| POST | `/api/direct-analysis-history/{jobId}/link` | 기존 로그인·소유 확인 | 계정에 direct 이력 연결 |
| GET | `/api/direct-analysis-history/{jobId}` | 기존 로그인·소유 확인 | direct 이력 저장 상태 |
| GET | `/api/direct-analysis-history` | 기존 로그인 | 독립 연습 이력 |

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


## 문장별 독립 연습

| Method | Path | 인증 | 목적 |
|---|---|---|---|
| POST | `/direct/api/v1/practice-batches` | 없음 | 문장 목록을 연습 ID에 고정 |
| POST | `/direct/api/v1/practice-batches/{batchId}/sentences/{sequenceNo}` | 없음 | 문장 WAV 한 개 접수·분석 시작 |
| GET | `/direct/api/v1/practice-batches/{batchId}` | 없음 | 진행 또는 전 문장 피드백 일괄 조회 |
| POST | `/direct/api/v1/practice-batches/{batchId}/cancellations` | 없음 | 미공개 연습과 접수 작업 취소 |
