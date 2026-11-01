## 문장별 녹음·일괄 피드백 (2026-11-01)

독립 음성 연습은 원고를 FE의 기존 `splitSentences`로 나눈 순서대로 별도 녹음한다. 문장 완료 때 실제 녹음기를 정지·flush하고 mono/16kHz/PCM16 WAV 하나를 보낸다. 접수 202 뒤 다음 문장을 녹음하며 이전 문장 추론 완료를 기다리지 않는다. 마지막 문장 업로드 뒤 비동기 GET long polling으로 모든 문장의 결과를 기다린다. 전 문장의 분석이 종료되면 순서대로 n개의 원본 결과를 한 응답에 제공한다. 전체 점수를 평균하거나 한 녹음의 점수를 여러 문장에 복사하지 않는다.

### 경로와 요청

아래 경로는 명시 활성화된 익명 `/direct` API이며 인증·cookie·서비스 token을 추가하지 않는다. 기존 정확한 origin CORS 설정을 사용한다. 모든 응답은 Cache-Control: no-store다.

| Method | Path | 요청 | 성공 |
|---|---|---|---|
| POST | `/direct/api/v1/practice-batches` | application/json, 최대 32768 bytes | 202, 연습 상태 |
| POST | `/direct/api/v1/practice-batches/{batchId}/sentences/{sequenceNo}` | audio/wav 원본 bytes | 202, 접수 상태 |
| GET | `/direct/api/v1/practice-batches/{batchId}?waitSeconds=20` | body 없음 | 200, 진행 또는 전체 결과 |
| POST | `/direct/api/v1/practice-batches/{batchId}/cancellations` | body 없음 | 200, 취소/이미 공개된 결과 상태 |

생성 JSON은 아래 **네 필드만** 가진다. `clientBatchId`는 FE가 제출 전에 저장한 UUID다. `sentences`는 순서가 고정된 비어 있지 않은 문자열 1~50개, 합계 최대 4000자다. `contentId`는 기존 콘텐츠 양의 정수 또는 null이다. `historyClaimSha256`는 계정 이력 연결용 임의값의 소문자 hex SHA-256(64자)이며 분석 권한 증명이 아니다. 요청 JSON의 필드 순서는 멱등성에 영향을 주지 않는다. 같은 batchId의 원고·content·claim 변경은 409 IDEMPOTENCY_CONFLICT다.

업로드 path의 `sequenceNo`는 1부터 n까지다. WAV는 문장마다 0.5~180초·최대 20MiB이며 기존 PCM 검증을 공유한다. 각 문장의 attempt UUID는 batchId와 index로 고정된다. 같은 index·동일 bytes 재전송은 기존 작업을 반환하고, 다른 bytes는 409다. 재전송·상태 조회는 추론/GPT를 다시 실행하지 않는다. 동시에 본문을 받는 수는 기존 3개, 전체 batch 대기/실행 작업은 최대 50개로 제한한다. 독립 단일 작업의 기존 대기 상한 3개와 전체 1000 jobs·2GiB spool 한도는 유지한다. batch journal도 최대 1000개다. 초과는 429 CAPACITY_EXCEEDED/UPLOAD_CAPACITY_EXCEEDED 또는 503 SPOOL_FULL이다.

### 응답·공개 시점

공개 버전은 `voice-coaching.direct-practice-batch.v1`이다. [JSON schema](../contracts/direct_practice_batch_v1.schema.json)와 필드는 아래와 같다.

| 필드 | 타입·null | 의미 |
|---|---|---|
| schemaVersion | string, 필수 | 위 버전 literal |
| batchId | UUID, 필수 | 생성 시 고정한 연습 ID |
| status | enum, 필수 | RECORDING / PROCESSING / RESULT_READY / CANCELLED |
| sentenceCount | integer, 필수 | n, 1~50 |
| uploadedCount | integer, 필수 | 접수된 문장 수 |
| completedCount | integer, 필수 | RESULT_READY/FAILED/CANCELLED로 종료된 문장 수 |
| sentences | object[n], 필수 | 원고 순서의 sequenceNo(1-based), scriptText, jobId(UUID 또는 null), status |
| sentences[].status | enum, 필수 | AWAITING_UPLOAD / QUEUED / RUNNING / RESULT_READY / FAILED / CANCELLED |
| results | null 또는 object[n], 필수 | 전 문장 종료 전에는 null. 전체 완료 때 sequenceNo/scriptText/analysis를 순서대로 제공 |
| results[].analysis | 기존 direct-analysis.v1 view, 필수 | job/execution identity, 상태, 기존 score·feedback·digest를 포함하는 result 또는 null, failure, archiveStatus/historySyncStatus·sequence·시각 |

RECORDING은 미접수 문장이 남은 상태, PROCESSING은 n개 모두 접수됐지만 분석 중인 상태다. RESULT_READY는 n개 모두 **종료**됐다는 뜻이며 전 문장의 품질 성공을 뜻하지 않는다. 실패한 문장은 analysis.status=FAILED와 실제 failure, result=null을 보존하고 다른 문장 결과와 함께 반환한다. 실행기가 명시적 ready_error로 닫히면 해당 batch의 아직 실행하지 않은 QUEUED 작업은 BATCH_EXECUTION_UNAVAILABLE로 종료한다. 임의 재추론이나 가짜 피드백을 만들지 않는다.

서버는 각 기존 one-shot job journal에 결과를 먼저 영속 저장하고 `batches/{batchId}.json`의 published 장벽을 전 문장 종료 시에만 연다. 장벽 전에는 개별 child GET/find/SSE의 result도 null이며 Backend 이력 전송도 대기한다. B2 private 보관은 먼저 진행할 수 있다. 결과 공개 후 기존 문장별 이력 이벤트와 계정 link API를 사용한다. Backend에 새 batch/정수 session 계약을 가정하지 않는다.

GET의 waitSeconds는 0~20(기본 0), 매번 최대 20초만 기다린다. 시간 만료는 실패가 아니라 현재 진행 응답이다. FE 요청 timeout은 30초다. 네트워크 오류 후 자동 재추론은 없으며 사용자의 상태 재확인으로 polling을 다시 시작한다. 취소는 아직 공개되지 않은 batch의 추가 접수를 막고 접수된 비종료 child를 취소한다. 이미 공개한 결과를 취소로 덮어쓰지 않는다. 취소 응답 유실 시 동일 취소를 재전송할 수 있다.

공통 오류 body는 `{code:string}`이다. 404 BATCH_NOT_FOUND, 415 JSON_REQUIRED/PCM_WAV_REQUIRED, 422 INVALID_BATCH/INVALID_BATCH_SENTENCES/INVALID_SENTENCE_INDEX/INVALID_WAIT/INVALID_PCM_WAV/INVALID_CONTENT/INVALID_HISTORY_LINK_DIGEST, 409 BATCH_CANCELLED/IDEMPOTENCY_CONFLICT, 413 PAYLOAD_TOO_LARGE, 408 UPLOAD_TIMEOUT, 기존 429/503이 가능하다. 그 외 FastAPI path/query 정수 파싱 실패는 기본 422다.

### FE 상태·복구·검증 경계

FE는 batch ID·문장 목록·이력 claim을 사용자/콘텐츠별 localStorage에 저장하고, 준비한 문장 WAV bytes를 같은 사용자·batch·index의 IndexedDB에 저장한다. 업로드 실패 뒤 동일 bytes로 재전송하며, 재녹음 전 서버에 접수가 없음을 확인한다. 페이지 재열기 시 같은 batch를 조회·복구하고 첫 미접수 문장부터 계속한다. 이미 접수된 문장은 다시 분석하지 않는다. 원고 변경은 기존 batch의 새 원고로 해석하지 않는다. 저장 공간 오류는 표시하고 녹음 전송 성공으로 숨기지 않는다. 계정별 미완료 history-link queue는 연습 메타데이터와 분리해 보관·재시도하므로 새 연습 시작이나 페이지 재열기로 claim을 잃지 않는다. 파일 보관은 새 연습 시작 시 정리하며 임의의 다른 사용자 녹음을 지우지 않는다.

결과 화면은 전 문장 완료 뒤 n개 카드에 각각 해당 녹음의 점수·총평·로컬 재생을 연결한다. 재생 bytes의 SHA-256이 해당 결과의 audioSha256과 일치할 때만 사용한다. 실패 문장의 점수는 0으로 만들지 않는다. 가이드 원본이 전체 원고 음성이면 여러 문장 연습에는 기존 기기 TTS를 문장별로 사용한다. MyPage/리포트 저장은 기존 문장별 direct history ID를 사용하며 새로운 Backend 집계 API를 추가하지 않는다.

적용 범위는 독립 direct 음성 연습(문장/뉴스/아나운서 등)이다. 기존 Backend 정수 session을 요구하는 클래스·승급 시험·영상 흐름은 별도 계약을 유지한다. 새 API가 없는 서버로 자동 단일 파일 fallback하지 않는다. 서버 API를 먼저 배포하고 FE를 배포해야 한다. GPU 시작·실제 녹음·GPT 생성·브라우저/회귀 QA 및 배포는 이 소스 변경의 검증 범위에 포함하지 않는다.
