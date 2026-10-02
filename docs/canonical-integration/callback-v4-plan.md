# Canonical callback v4 전환 계획

2026-10-02 11:39~11:41 KST: [최신 제한 v4 접수 활성화](rollout-execution-20261002.md#제한-v4-접수-활성화).
canonical **V33~V36**, RunPod 실행기·부팅·writer, Backend reader·검증·journal/apply를
운영 반영했고 양쪽 admission은 ON이다. 명시적 v4 헤더의 단독 음성 발음 학습만 대상이다.
기존 헤더 없는 API·FE·TTS 계약은 유지한다. 실제 추론/GPT/B2/callback E2E는 미수행이다.
FE 테스트는 FE 개발자 책임이다. 아래 OFF/미배포 표기는 각 작성 시점의 이력이다.

작성일: 2026-09-30. 대상은 RunPod producer, Backend callback·저장·조회, Frontend 소비 경계다. **사용자 결정은 기존 callback v3를 전면 개편하여 v4로 승계하는 것**이다. ID만 늘리거나 v3 필드의 의미를 바꾸는 호환 shim으로 끝내지 않는다. 아래 필드·저장 구조·전환 단계는 구현할 설계안이며, v4 구현·검증·배포 완료 기록이 아니다.

핵심은 **작업 완료 상태, canonical 입력 판정, adapter 상태, GPT 생성 상태를 분리하고**, 원본 scoped ID·G2P role·MFA 위치를 그대로 보존하는 것이다. 전환 중에는 구 reader와 진행 중 v3 작업을 수용하되, 최종 신규 canonical 작업의 결과 writer는 v4 하나로 통일한다. 기존 기록을 가짜 v4로 변환하지 않는다.

호환성 개정: 기존 공개 API의 URL·요청 body·응답 envelope와 v3 기록의 의미를 유지한다. canonical 전체 조회는 별도 API로 제공하고, 신규 v4 실행은 지원을 명시한 클라이언트와 준비된 서버에만 허용한다. **현재 Backend에 v4를 그대로 전송할 수 있다는 뜻이 아니라, 양쪽 reader를 먼저 확장하는 전환 계획**이다. 후속 구현에서 요청 dual reader·차단 gate·별도 readiness부터 추가했으며 실제 진행 범위는 [14절](#14-구현-진행-현황)에 구분한다.

후속 사용자 승인: **전체 통합·배포 및 v4 접수를 승인받았다.** v4 접수의 사용자 승인 대기는 해소됐지만 미구현 경로·저장소·검증 상태를 ready로 간주하는 승인은 아니다. [15절](#15-접수-승인과-배포-전-재확인)에 현재 관측과 남은 외부 조건을 기록한다.

## 1. 근거와 작업 범위

10/1 후속 사용자 승인으로 [PostgreSQL journal](backend-journal.md)을 운영 방향으로 확정했다. B2 원본 정본과 공개 계약은 유지하고, V35의 실행/업로드/ACK 및 서버 callback apply로 로컬 SQLite 영구 mount 의존을 제거한다. 이는 로컬 구현이며 전체 runtime·운영 적용 완료는 아니다. 아래 이전 SQLite 설명은 새 운영 fallback으로 적용하지 않는다.

2026-10-01 후속 [운영 제한 키·통합·배포 실행 계획](production-rollout-plan.md)에 역할별 B2 권한·비밀 공급, 남은 구현, 단계별 활성화/원복 gate를 구체화했다. 기존 승인을 유지하는 계획이며 키 발급·주입·배포 완료 기록은 아니다.

같은 날 [공개 FE main/dev 재점검](frontend-contract-review.md)에서 기본 main `81b27dee88ae1c09bb4f9c2581be3552ac283137`과 아래 최초 dev 검토 기준이 다름을 확인했다. main의 공개 API·업로드 인증·영상 동의를 보존하고, 무조건 complete 호출·FAILED 상세·이력/cache·renderer를 연결하기 전 v4를 활성화하지 않는다. 초기 opt-in은 독립 음성 연습에 한정하며 입술·시험·클래스는 legacy 유지다. 아래 최초 조사 SHA를 최신 운영 FE SHA로 해석하지 않는다.

[원본 조사·계약 비교](README.md)의 immutable source를 기준으로 한다. intelligentAI HEAD는 `cfe89ebca8131d0be46cb78b2b7dfd505d65bf63`, 운영 관측 revision은 `32d98e39767b56697bc7e58fb50e3e9d61432349`, Backend 검토 SHA는 `b48db988e7485943097acaa4c71308ef306175a8`의 develop, Frontend는 `d54ceae5065880ccabd8f00ffe83500ab9077dfd`의 dev다. 이번 계획 작성에서는 Pod·AWS 재조회나 새 fetch를 하지 않았다. 구현 시작 시 변경된 SHA를 다시 대조한다.

앞선 공개 저장소 재점검에서 원격 develop은 위 SHA와 같았고, main `931f90ccdf6e1a982ecbeef4baaea5c333803869`과 분석 패키지 차이는 없었다. 이번 개정은 그 점검과 동일 SHA 소스를 근거로 하며 AWS 운영 revision을 새로 확인한 기록이 아니다. 확인된 비호환 여섯 항목은 아래 2절(요청 버전), 5절(재시도·업무 판정), 9절(후처리), 10절(조회·부가 API)에 반영했다.

계획 수립 기준의 운영 Python `http_models.py`와 Backend `RunPodContract.java`는 결과를 기존 `runpod_result_v1.schema.json` 하나로 검증한다. Backend DTO는 이를 legacy worker result로 변환하고, `AnalysisCoaching`은 짧은 ID·CTC timing·writtenRole을 요구한다. FE도 coaching v1과 CTC replay만 수용한다. v4에서는 이 경로를 그대로 재사용하지 않는다. 후속 로컬 구현과 운영 배포를 혼동하지 않는다.

보존할 것: Backend 소유권·로그인, 양방향 Bearer, media/script 무결성, claim/lease/heartbeat/deadline/cancel, 중복 ACK, frozen R/S7/S8·전문가 원문. 바꾸지 않을 것: 모델·P1 threshold·MFA 알고리즘·frozen prompt/validator·독립 TTS·영상 알고리즘. 내부 HTTP에 동의 증빙·요청별 권한 grant·서명을 추가하지 않는다.

## 2. 버전과 전환 원칙

| 경계 | v4 설계 |
|---|---|
| 결과 schemaVersion | `voice-coaching.runpod-analysis-result.v4` |
| 결과 schema 파일 | 신규 `docs/contracts/runpod_result_v4.schema.json`; v1/v2/v3 정본은 이력 reader용으로 유지 |
| callback 경로 | 기존 `POST /api/internal/ai/analyses/{analysisId}/result` 유지, schemaVersion별 명시적 parser/DTO/validator 분기 |
| 제어 계약 | `voice-coaching.runpod-http.v1.2` 제안; 요청 v2와 결과 capability 협상 추가 |
| 요청 schemaVersion | `voice-coaching.runpod-analysis-request.v2`; 기존 입력 + `resultSchemaVersion`·`analysisProfile`을 서버가 고정 |
| 내부 core/LLM | `canonical_ai_predeploy_v1`, `canonical-llm-expression-input-v1`, `canonical-llm-expression-result-v1` 유지 |
| 사용자 조회 projection | 신규 `voice-coaching.canonical-analysis-view.v1`을 별도 typed 필드로 제공; 옛 coaching v1로 위장하지 않음 |

이 HTTP v4는 과거 Redis `voice-coaching.analysis-result.v4`와 **다른 계약**이다. 모든 코드·문서에서 전체 schemaVersion을 사용한다. old enum의 일괄 치환이나 기존 JSON schema의 의미 변경은 하지 않는다.

`analysisProfile`은 서버 routing 값으로 `LEGACY_SEUNGUN_V3` / `CANONICAL_FROZEN_20260928_V4`를 구별한다. 정책 revision이나 사용자 권한 grant를 요청에 싣는 필드가 아니다. v2 요청 digest에 두 필드가 포함되므로 같은 execution 도중 profile/결과 버전을 변경할 수 없다. 신형 worker는 v4 작업 실패도 v1 결과로 내려보내지 않는다.

기존 readiness는 `contractVersion=v1.1`에 닫혀 있으므로 필드를 무단 추가하지 않는다. Backend에 인증 `GET /api/internal/ai/worker-readiness/v2`를 추가하여 accepted result versions, analysis profiles, 계약 schema digest, evidence 저장 준비 여부를 제공하고 기존 경로는 유지한다. worker도 지원 profile·실제 로드한 manifest·의존성별 준비 상태를 공개한다. readiness끼리 서로를 재귀 호출하지 않는다. 선언만 v4이고 DB migration/reader가 없으면 ready로 응답하지 않는다.

### 2.1 요청과 제어의 양방향 호환

현재 [RunPodAnalysisPayloadCodec](https://github.com/voice-coaching/VC-BE/blob/b48db988e7485943097acaa4c71308ef306175a8/src/main/java/org/example/voice/analysis/infrastructure/runpod/RunPodAnalysisPayloadCodec.java#L23-L39)은 고정 request DTO를 직렬화하고 unknown property를 거부한다. [callback service](https://github.com/voice-coaching/VC-BE/blob/b48db988e7485943097acaa4c71308ef306175a8/src/main/java/org/example/voice/analysis/application/AnalysisRunPodCallbackService.java#L133-L144)는 claim·heartbeat·result의 deadline 확인에도 저장된 요청 schema를 사용한다. publisher만 v2로 바꾸면 이 경로에서 실패한다.

- `RunPodAnalysisJobRequest`, `RunPodAnalysisPayloadCodec`, `RunPodContract`, outbox dispatcher/client, callback deadline parser와 RunPod 요청 parser 모두 v1/v2를 구별한다. legacy profile은 요청 v1, canonical profile은 요청 v2/result v4 조합으로 고정한다.
- 기존 v1 outbox bytes를 v2로 변환하거나 새 기본값을 주입하지 않는다. v2의 profile·resultSchemaVersion도 최초 outbox transaction에 저장한다. 재전송은 저장된 문서의 동일 JCS digest를 유지하며 decode/re-encode 후에도 digest가 달라지지 않아야 한다.
- 기존 `POST /v1/analysis-jobs`, `POST /v1/analysis-jobs/{requestId}/cancel`, claim·heartbeat·result URL, 접수 200/202, callback ACK 200 및 identity echo는 유지한다. request schemaVersion으로 해석을 분리하며 경로의 v1과 요청 schema v2는 다른 버전 축이다.
- 제어 v1.1의 claim/heartbeat/ACK/error와 기존 readiness 응답은 그대로 지원한다. v1.2 추가 메시지와 오류는 버전이 명시된 새 readiness/evidence 경계에서만 사용한다. 전역 VERSION 상수 하나를 바꾸어 구 readiness 응답까지 v1.2로 바꾸지 않는다.
- Backend schema 정본은 `docs/contracts`이며 `build.gradle`의 `processResources`가 `runpod_*.schema.json`을 classpath로 복사한다. 신규 v4 및 제어 schema와 외부 참조 파일도 실제 package에 포함되는지 확인한다. Python/Java의 64 KiB 제어·1 MiB 결과 제한, UTC 시각·UUID·JCS·UTF-16 검증을 함께 유지한다.

### 2.2 구형 클라이언트의 접수 보호

기존 `/analyze`와 `/analysis/retry`의 JSON body 및 `ApiResponse` 성공 DTO는 유지한다. 공개 API의 기존 동의 검증·녹음 소유권·음질·동시 실행 제한도 유지한다. 내부 HTTP에 동의/서명을 넣지 않는 결정이 공개 API의 동의 body 삭제를 뜻하지 않는다.

새 FE만 선택적 `X-Analysis-Result-Schema: voice-coaching.runpod-analysis-result.v4` header로 지원 능력을 알린다. 이 header는 권한 grant가 아니며 사용자가 모델·manifest·analysisProfile을 지정할 수 없다. Backend가 인증·입력 검증 후 허용된 서버 profile을 선택한다.

| 접수 조건 | 신규 작업 처리 |
|---|---|
| header 없음 | 기존 요청 v1/legacy writer 유지. 서버 기본값을 v4로 바꾸지 않음 |
| v4 명시 + 양쪽 계약/저장소/FE 전환 준비 | 요청 v2/result v4를 outbox에 고정 |
| v4 명시 + 준비 미완료 | 기존 `ANALYSIS_INTEGRATION_UNAVAILABLE` 오류 envelope로 거절. pending row/outbox를 남기거나 v3로 자동 downgrade하지 않음 |
| 알 수 없거나 중복된 header | 400 `INVALID_INPUT_VALUE`; 신규 작업 미생성 |

`SecurityConfig` CORS allowed headers에 이 header를 추가하되 기존 origin·인증 정책은 유지한다. `/api/analysis-capabilities`는 기존 설정 조회 의미를 유지하며 실시간 readiness로 바꾸지 않는다. 최종 admission은 서버의 별도 v4 gate가 담당한다. 구형 클라이언트의 신규 분석 지원 종료는 자동 전환이 아니라 별도 호환성 종료 결정이다.

## 3. 무손실의 정확한 범위

두 보존 단위를 구분한다.

1. **의미 보존:** callback 본문과 DB가 네 canonical 상태·reason/stage, candidate/evidence scope·순서·guidance·역할·MFA 시간을 정식 필드로 수용한다. 유효한 원본 값을 이름만 바꾸거나 누락시키지 않는다.
2. **원본 바이트 보존:** core 원문, frozen bridge 결과, 실제 projection, 서버 binding/association을 각각 비공개 immutable 객체로 저장하고 SHA·byte size·객체 version으로 연결한다. JSONB 재직렬화나 JCS digest로 원본 bytes SHA를 대체하지 않는다.

분석하지 않은 위치/모델 출력은 만들지 않는다. 최대3 후보 밖의 원래 P1 관측, omitted ID, prosody 및 상세 MFA/provenance는 원본 객체에서 복원할 수 있어야 한다. `coaching.items`만 저장하고 전체 원본을 버리면 무손실 완료가 아니다.

callback 수신자와 public API의 권한은 다르다. 내부 원본의 로컬 경로·original_error·상세 trace는 일반 사용자에게 그대로 반환하거나 application 로그에 출력하지 않는다. 비공개 원본 보존과 공개 allowlist projection을 별도로 구현한다. 삭제 요청·보존기간 종료 후까지 영구 복구를 약속하는 의미의 “무손실”은 아니다.

## 4. v4 본문 구성

다음은 필드 설계이며 현재 실행되는 payload 예시가 아니다. root와 서비스 소유 객체는 `additionalProperties=false`로 고정한다. frozen 객체는 존재하는 동결 schema와 validator로 검사한다. 별도 결과 schema가 없는 bridge 반환 구조는 실제 동결 소스에서 서비스 소유 typed wrapper를 정의하고 원본 필드와 대조한다. 존재하지 않는 frozen schema 파일을 가정하거나 일반 자유 Map으로 검증을 우회하지 않는다.

| 필드 | 계약 및 처리 |
|---|---|
| `schemaVersion` | v4 const |
| `eventId`, `requestId`, `executionId`, `workerInstanceId` | 기존 UUID 식별자 유지; 결과 event는 한 번 생성하고 재전송마다 재사용 |
| `analysisId`, `recordingId`, `contentId` | Backend 원본 정수 식별자·현재 안전 범위 유지; URL/outbox/녹음 관계와 대조 |
| `status` | 작업 종결 상태 `COMPLETED` / `FAILED`; canonical ACCEPT와 별개 |
| `failure` | null 또는 서비스 실패 `{origin, code, stage}`; 임의 traceback/사용자 원문 대신 제한된 코드. core 오류를 덮어쓰지 않음 |
| `workerRevision`, `pipelineRevision`, `analysisProfile` | 실제 실행 revision과 고정된 canonical profile |
| `sourceIdentity` | admitted script/audio SHA, canonical prepared WAV SHA(nullable), core UUID(nullable), core raw SHA(nullable), R manifest·S7 manifest·lock·prompt SHA |
| `canonical` | 유효한 core가 있을 때만 객체. `schemaVersion`, `representation`, `decision`, `inputValidation`, `coreStatus`, `feedbackDeliveryAllowed`, `pronunciationFeedbackSource`, `evidence` |
| `canonical.representation` | `INLINE` 또는 크기 초과 실패 전용 `RETAINED_ONLY`. 후자도 core 부재를 뜻하는 null과 구별 |
| `canonical.decision` | 원래 input_validation의 status·reason_code·stage를 작은 정식 필드로 보존. 검증된 원본과 일치해야 하며 서비스 실패 코드로 대체하지 않음 |
| `canonical.inputValidation` | INLINE에서는 원래 `input_validation` 전체 의미 보존: schema/gate version, status, reason_code, stage, evidence, source_provenance, original_error, subject, decision_basis. RETAINED_ONLY에서는 null이며 검증된 원본 reference로 전체 내용을 복원. 서비스 내부 typed wrapper에서 명시적으로 매핑하고 원문도 보관 |
| `canonical.evidence` | 전체 pronunciation/prosody/MFA/content gate의 immutable 원본 reference와 고정 JSON Pointer. raw arrays를 선택된 3개로 대체하지 않음 |
| `selection` | prepare 성공 시 attemptScope, 원본 coverage·reviewReasonCounts·선택 candidate 배열 전체. 선택된/누락된 ID 배열 순서 유지; 미생성은 null |
| `coaching` | frozen expression-result 객체를 독립 slot으로 보존. sourceIdentity의 core SHA, 원래 coreInputValidation과 대조. dispatch 전 통합 실패면 null |
| `visual` | 첫 canonical 릴리스는 `{status: NOT_CONNECTED, correctiveClaimsAllowed: false}`. 기존 legacy 경로의 관찰 상태와 구별 |
| `prosodyPolicy` | `expressionPermission=NO_APPROVED_GUIDANCE_MAPPING`; 실제 prosody 데이터/상태는 canonical evidence에 보존 |
| `score` | overallScore=null, validity 및 불가 사유. legacy Clova scoringEvidence는 v4 필드에서 제거 |
| `retainedEvidence` | 서버 검증된 비공개 객체 목록과 `evidenceReceiptId`; client URL·signed URL·로컬 path 아님 |

v3의 `outcome`, legacy selected-phone `pronunciationEvidence`, `visualSupplement`, `scoringEvidence`, 옛 `coaching` 타입과 summaryFeedback를 v4의 필수 필드로 유지하지 않는다. 특히 Native margin을 기존 detectorScore에 넣거나, 가짜 selected phone을 만들어 legacy `AnalysisWorkerResult` 생성자를 통과시키지 않는다. 요약·표시 문구는 별도의 deterministic public renderer 책임이며 frozen GPT 출력에 새 필드를 요구하지 않는다.

유효 core가 생성되기 전에 worker 기동/무결성/저장소가 실패하면 `canonical=null`, 관련 core 식별자는 null이고 `failure.origin=INTEGRATION`이다. **없던 core SYSTEM_FAILURE 결과를 만들어 채우지 않는다.** 유효 core가 SYSTEM_FAILURE를 반환한 경우에는 그 core와 inputValidation을 그대로 보존한다. schema 위반 원문은 별도 격리 진단 객체이며 정상 canonical 근거로 승격하지 않는다.

## 5. 상태와 생성 결과의 조합

| 상황 | 작업 status | canonical 상태 | LLM/adapter | 표시 원칙 |
|---|---|---|---|---|
| 허용 발음 후보와 정상 생성 | COMPLETED | ACCEPT | READY / SCHEMA_AND_STRUCTURAL_SEMANTICS_VALID | 모델 관측과 일반 목표 소리 연습 |
| 생성 오류 또는 생성 validator 거부 | COMPLETED | ACCEPT | READY / DETERMINISTIC_FALLBACK | 기본 연습 제공, GPT 성공으로 표시하지 않음 |
| 허용 후보 없음 | COMPLETED | ACCEPT | NO_PERMITTED_COACHING_CONTENT / NOT_DISPATCHED | 생성 없음; 정상·완벽 판정 아님 |
| 입력 거절 | COMPLETED | REJECT | GLOBAL_REJECT / NOT_DISPATCHED | 원래 입력 사유에 맞는 안내 |
| 판단 보류 | COMPLETED | INCONCLUSIVE | GLOBAL_INCONCLUSIVE / NOT_DISPATCHED | 근거 부족, 사용자 과실 단정 금지 |
| core 자체 시스템 실패 | FAILED | SYSTEM_FAILURE | GLOBAL_SYSTEM_FAILURE / NOT_DISPATCHED | 시스템 문제로 안내 |
| 유효 core 이후 adapter/binding/budget 실패 | FAILED | 원래 상태 유지 | FAIL_CLOSED / NOT_DISPATCHED 또는 coaching=null | 통합 실패; REJECT로 변환 금지 |
| core 생성 전 시스템 실패 | FAILED | null | coaching=null | 분석 결론 없음 |

REJECT/INCONCLUSIVE의 COMPLETED는 **판정 처리가 끝났다는 뜻**이다. 정상 발음·학습 성공·진도/시험 통과의 근거로 사용하지 않는다. Backend의 완료 집계·재녹음·시험/진도 eligibility까지 이 표를 반영한다. 기존 코드는 COMPLETED만 보던 곳을 반드시 검색해 수정한다.

non-ACCEPT는 dispatchAttempts=0/items=[]를 검증한다. 생성 시도는 최대1이며 fallback을 위해 두 번째 GPT 호출을 하지 않는다. frozen bridge가 보존하는 errorCode/fallbackReason이 예외 종류 수준이면 원인 세부값을 추측해 추가하지 않는다. 추가 서비스 진단 코드는 별도 failure/관측 필드다.

취소·deadline 만료·stale execution은 core 상태가 아니라 작업 제어 상태다. 이미 취소된 시도의 뒤늦은 v4 callback으로 결과를 다시 살리지 않는다. 사용자 취소를 AI REJECT 결과로 생성하지 않는다.

### 5.1 사용자 재시도와 재녹음

현재 [TrainingAnalysisRequestService](https://github.com/voice-coaching/VC-BE/blob/b48db988e7485943097acaa4c71308ef306175a8/src/main/java/org/example/voice/training/application/TrainingAnalysisRequestService.java#L51-L127)는 같은 녹음의 중복 analyze를 거절하고 retry는 FAILED만 허용한다. 첫 v4 릴리스도 이 의미와 기존 최대 3회 사용자 재시도 제한을 유지한다.

| 상황 | 공개 API 및 identity 규칙 |
|---|---|
| 요청 전달/결과 callback 재전송 | 동일 requestId·executionId와 해당 event/body 유지; AI/GPT 추가 실행 없음 |
| FAILED 사용자 재시도 | 기존 `/analysis/retry` 사용. 같은 analysis row에서 새 requestId와 새 executionId를 생성하고 retryCount 증가. 동의·소유권·동시 실행·세션 상태를 다시 검사 |
| COMPLETED + REJECT | retry는 기존대로 409 `ANALYSIS_NOT_FAILED`. 원인별 재녹음 안내 후 새 recording을 등록·선택하여 `/analyze` 사용 |
| COMPLETED + INCONCLUSIVE | 첫 릴리스에서는 동일 녹음 재분석을 열지 않음. 재녹음 안내와 새 recording 경로 사용. 제한된 동일 입력 재분석은 후속 별도 계약 |
| COMPLETED + ACCEPT, GPT fallback 포함 | 기존 FAILED retry 대상 아님. 기본 연습을 제공하며 생성 실패만으로 자동 재분석하지 않음 |
| 취소·삭제·만료 세대 | 완료 callback 재적용 금지. 사용자 재시도는 기존 세션/녹음 적격성 검사를 통과한 경우에만 새 세대로 생성 |

실패한 canonical 작업을 구형 클라이언트가 header 없이 retry하면 legacy profile의 **새** request/execution으로 처리할 수 있다. 이는 기존 v4 결과를 v3로 바꾸는 동작이 아니다. 과거 v4 원본은 보존하고 현재 포인터만 새 세대로 전환한다. 새 FE가 v4를 계속 요청했는데 준비가 안 되어 있으면 조용히 legacy로 내리지 않는다.

재녹음 UI는 원래 완료 기록을 지우거나 같은 recording row를 덮어쓰지 않는다. 진행 가능한 세션에서 새 녹음을 선택하고, 완료/취소되어 재사용 불가한 세션이면 새 세션을 만든다. 실제 등록·선택·세션 상태 전이의 수용 여부를 개발자 E2E 항목에 포함한다.

2026-10-01 로컬 구현: 기존 `POST /api/training-sessions/{sessionId}/recordings/upload-url`이 session→analysis 잠금과 현재 검증 원문을 확인하여, 초기 독립 음성 범위의 INLINE/COMPLETED REJECT·INCONCLUSIVE에 한해 ANALYZING→UPLOADING을 허용한다. 기존 register/select/analyze의 URL·DTO·동의는 그대로이고 새 녹음은 별도 row/attempt다. upload intent 발급 실패 시 상태 전이도 rollback되며 이전 선택/결과/근거는 삭제하지 않는다. GET actions와 명령은 `CanonicalRerecordEligibility`를 공유한다. 요청/재시도는 선택 녹음을 읽기 전에 session 잠금을 잡아 중간 선택 변경을 막는다. ACCEPT/FAILED/legacy/미검증 결과와 완료·취소 세션은 이 복구 대상이 아니다. 컴파일 범위이며 운영 DB·HTTP·FE 수용은 아직 미수행이다.

### 5.2 학습 완료와 시험의 호환 분기

현재 [TrainingSessionService.complete](https://github.com/voice-coaching/VC-BE/blob/b48db988e7485943097acaa4c71308ef306175a8/src/main/java/org/example/voice/training/application/TrainingSessionService.java#L66-L75)는 `existsCompletedAnalysisForSelectedRecording`으로 학습 완료를 허용한다. [TitlePersistence](https://github.com/voice-coaching/VC-BE/blob/b48db988e7485943097acaa4c71308ef306175a8/src/main/java/org/example/voice/title/infrastructure/TitlePersistence.java#L42-L70)의 점수 불가 분기는 legacy coachingDocument에 의존한다. 다음 정책은 서비스 설계이며 core의 ACCEPT 의미를 변경하지 않는다.

- legacy 기록은 기존 완료/시험 규칙을 유지한다. 과거 기록을 canonical 기준으로 재채점하지 않는다.
- v4 학습 완료 eligibility는 선택·미삭제·미취소 현재 세대, 작업 COMPLETED, canonical ACCEPT, 검증된 정상 결과 전달을 모두 요구한다. 후보 없음이나 deterministic fallback도 이 조건에서는 **학습 절차 완료**가 가능하지만 발음 정상/합격을 뜻하지 않는다. REJECT/INCONCLUSIVE/FAILED/RETAINED_ONLY는 불가다. 불가 시 기존 complete API의 409 `ANALYSIS_NOT_COMPLETED` envelope를 유지하고 새 조회 API로 상세 사유를 제공한다.
- v4에는 승인된 점수가 없으므로 시험 채점은 허용하지 않는다. 소유/시험/세션/content 관계가 맞는 COMPLETED v4에는 기존 409 `ANALYSIS_SCORE_UNAVAILABLE`을 사용하고, 점수·등급·passed 값을 생성하지 않는다. legacy `coachingDocument != null`을 v4 식별 조건으로 사용하지 않는다.
- `TrainingAnalysisReaderImpl`, `TrainingSessionService`, `TrainingSessionReaderImpl`의 표시용 완료 여부, `TitlePersistence`/`TitleService`를 같은 정책으로 맞춘다. 홈·수료·완료 횟수는 이 gate를 통과한 세션을 따르도록 관련 조회를 점검한다. 녹음 삭제 방지용 `VoiceRecordingReaderImpl.hasCompletedAnalysis`는 학습 성공 판정으로 재사용하지 않고 기존 보존 규칙을 유지한다.

## 6. Scoped ID와 같은 시도의 결속

frozen 식을 그대로 사용한다: `attemptScope = SHA256(compact UTF-8 JSON([core.request_id, core.metadata.analysis_id, coreRawSha256]))`. Backend integer analysisId와 executionId를 이 식 안으로 바꾸어 넣지 않는다. 대신 바깥 association에 request/execution/analysis/recording/content/core UUID/core SHA를 함께 보존한다.

- candidateId는 `<64자리 scope>:candidate-<expectedIndex>`, evidenceId는 `<scope>:phone-<expectedIndex>` 원문이다. v3의 maxLength20·4자리 index를 가져오지 않는다. 실제 frozen schema의 허용 범위와 원본 index membership을 대조한다.
- 같은 candidate에서 expectedRole, roleSemantics, disposition, claimScope, expected/observed phone, repetitionCount, facts, guidance를 모두 보존한다. expectedRole은 G2P role이며 writtenRole로 재라벨하지 않는다.
- `selection.coverage.includedCandidateIds`, 선택 candidate 배열, 결과 `items[].candidate.candidateId`, `items[].expression.candidateId`는 **같은 순서와 개수**여야 한다. guidanceId와 evidenceId 집합·원문 facts도 대조한다. 불일치를 서버 정렬로 고치지 않고 거부한다.
- omittedCandidateIds도 보존하고 원본 전체 P1 관측과 연결한다. GPT 예산 때문에 다시 top-k를 선택하거나 후보 일부를 조용히 삭제하지 않는다.
- DB에서 필요할 때 쓰는 candidate 인덱스는 `(analysis_id, execution_id, candidate_id)`를 키로 삼는다. 짧은 index나 candidateId 하나만으로 사용자/녹음 전역을 연결하지 않는다. 사용자 재시도는 새 requestId와 새 executionId이며, 같은 요청의 전송 재시도는 둘 다 유지한다.
- FE key는 결과 execution과 scoped ID를 함께 사용하고 새 execution 조회 시 이전 화면 상태를 교체한다. 다른 시도의 근거가 섞이는 캐시 키를 금지한다.

내부 연결 wrapper는 동일 raw core·script·binding에 대해 동결 `prepare`의 view를 별도 `selection_bytes`로 보존하도록 확장했다. prepare의 결정론적 재호출은 GPT 재호출이 아니다. prepare 자체가 실패하면 selection을 지어내지 않고 null과 adapter 실패를 유지한다. frozen 소스는 편집하지 않으며 운영 executor 연결은 별도다.

## 7. MFA 위치와 provenance 보존

candidate의 각 fact에 `word`, `wordIndex`, `expectedIndex`, `wordStartS`, `wordEndS`, `phoneStartS`, `phoneEndS`, `localizationStatus`, `timingProvenance=MFA_LOCATION_ONLY`를 원문 그대로 둔다. 초를 정수 ms로 내림해 저장하거나 CTC span으로 바꾸지 않는다. 숫자 파싱·저장에는 원래 JSON 수치 정밀도를 보존하고, lexical 차이를 포함한 원본은 core bytes로 복원한다.

검증 조건은 유한 수, nonnegative, `wordStartS <= phoneStartS <= phoneEndS <= wordEndS`, 실제 prepared audio 길이 안의 위치다. frozen 계약이 허용하는 동일 시작/끝을 v3의 `end > start` 규칙으로 무조건 거절하지 않는다. 없는 위치는 null/원래 localization 상태이며 0으로 채우지 않는다. index는 frozen expected-phone 축, word index는 원래 canonical occurrence 축이다.

alignment 원본에는 모델 ID·historical runtime identity·canonical WAV SHA·transcript SHA·request binding·TextGrid SHA·receipt를 남긴다. MFA receipt의 opaque 내부 worker request ID와 service request ID를 같은 값으로 가정하지 않는다. 직접 비교할 필드와 명시적 association을 구분한다.

시간축은 canonical prepared WAV다. FE가 source 녹음을 재생할 때는 source/prepared SHA와 정규화 시간 매핑이 검증된 경우에만 이동 버튼을 켠다. normalization이 무조건 offset=0이라고 가정하지 않는다. 대응 근거가 없으면 위치를 표시하되 재생 이동은 비활성으로 둔다. 화면 반올림은 표시용일 뿐 저장값을 바꾸지 않는다. MFA 위치만으로 속도·실제 조음 지속시간을 새로 판단하지 않는다.

2026-10-01 구현 보완: private 서비스 `ASSOCIATION` 원본에 `preparedAudio={sha256,frameCount,sampleRate,measurementSource:SERVER_PREPARED_WAV_PCM_FRAMES}`를 필수 nullable 필드로 보존한다. producer가 같은 prepared WAV descriptor에서 SHA·실제 PCM 바이트 수를 확인하며, SHA가 없으면 metadata도 null이다. Backend는 이 인증된 producer 측정과 core/selection의 결속·시간 범위를 독립 대조한다. Backend가 prepared WAV를 다시 디코딩한 것으로 표현하지 않으며 원본 녹음 durationMs로 대체하지 않는다. 이 필드는 아직 미배포인 서비스 association 계약 보완이고 frozen core/S7 schema·prompt는 바꾸지 않는다. 원본 음성 파일을 추가로 B2에 보관하는 변경도 아니다.

## 8. 원본 보존과 callback 크기

영구 파생 근거는 프로젝트 원칙에 따라 **비공개 B2 저장 프로필**을 사용하도록 설계한다. 사용자 녹음 저장소를 B2로 이전하는 계획은 아니다. 실제 bucket/prefix·IAM/key는 배포 전 확정해야 하며 기존 연구자료 공개 경로에 사용자 근거를 올리지 않는다. 현재 원본 core에 없는 영상/음성을 새로 묶어 전송하지 않는다.

2026-09-30 사용자 결정으로 원본 근거 보존기간 및 취소·회원탈퇴 후 삭제 유예기간은 **무기한**으로 확정했다. 자동 물리 삭제는 하지 않되 공개 접근 차단·현재 실행 fencing은 즉시 적용해야 한다. 미확정 orphan도 별도 승인 없는 자동 삭제를 비활성으로 둔다. 기존 사용자 녹음·영상 정책은 변경하지 않는다. 필수 설정명과 미확정 저장 대상은 [RunPod 보관 설정](../../deploy/runpod/README.md#canonical-callback-v4-근거-보관-설정-미활성)에 남긴다. docs는 설정 인계서이며 원본 저장소가 아니다.

보존 객체는 core raw bytes, bridge-result bytes, selection/projection bytes(생성된 경우), binding/association bytes다. manifest는 artifact kind/schema, byte SHA-256, byte size, immutable fileId/version, 현재 execution association을 가진다. 객체 key는 서버가 analysis/execution/digest에서 만들며 임의 endpoint·bucket·local path를 callback에서 받아 fetch하지 않는다. object reference는 public 응답에서 제거한다.

callback raw UTF-8 본문 상한은 우선 기존 **1 MiB**를 유지하고 Python/Java/nginx에서 동일하게 적용한다. full raw core는 본문에 넣지 않는다. 최대3 후보의 facts·coverage·원래 inputValidation도 본문이 너무 커지면 내용을 잘라 정상 결과로 내보내지 않는다. 원본은 보존하고 `status=FAILED`, `failure.code=RESULT_ENVELOPE_TOO_LARGE`, `canonical.representation=RETAINED_ONLY`로 처리한다. 작은 decision·coreStatus·identity·검증된 evidence reference는 유지하고, 큰 inputValidation/selection/coaching 본문은 null로 두되 receipt의 원본에서 모두 복원 가능해야 한다. 이는 core 미생성이나 후보 없음이 아니며 public renderer는 전달 실패로 표시한다. P0에서 이 조합을 별도 schema 분기로 정의하고 요약 필드 자체의 최대 크기도 고정한다. 지원 입력의 실제 크기가 상한을 넘으면 배포 전에 입력 범위 또는 별도 버전의 분할 근거 계약을 확정한다.

artifact 원본 한도는 각 schema와 현재 wrapper의 core 16 MiB 한도를 함께 검토해 정한다. 압축·base64 원본을 무제한 JSON 안에 숨기지 않는다. JCS callback digest, raw core SHA, projection SHA, object metadata digest는 서로 다른 필드와 검사다.

기존 callback client는 최대 10초 요청 예산이므로 원본 다운로드·해시를 DB row lock 안에서 처리하지 않는다. 제안하는 사전 증거 등록 단계는 다음과 같다.

1. RunPod가 비공개 immutable 객체 업로드를 끝낸다.
2. 인증 `POST /api/internal/ai/analyses/{analysisId}/evidence`에 execution·artifact manifest를 등록한다. Backend는 현재 lease/owner/key namespace를 검증하고 202 접수 또는 기존 receipt를 반환한다. 같은 manifest의 중복 등록은 같은 receipt로 수렴한다.
3. Backend가 제한 크기·SHA·원본 schema·association을 검증한 뒤 `VERIFIED`로 저장한다. 인증된 receipt 상태 조회로 확인한다. 이 동안 RunPod heartbeat는 계속 유지한다. 신규 등록 endpoint/status schema와 client의 202 처리도 제어 v1.2에 포함한다.
4. v4 callback은 검증된 `evidenceReceiptId`와 manifest digest를 참조한다. receipt는 저장·검증 상태 식별자이지 요청별 권한 grant/서명이 아니다. callback에서도 현재 execution/owner/deadline을 다시 검사한다.

검증 실패는 의미 오류와 저장소 일시 장애를 구분한다. READY 선언에는 저장소 접근·schema 검증 worker·필수 DB 상태를 포함한다. 이 저장 경로와 실제 시간 예산을 확인하기 전 v4 admission을 열지 않는다. 원본 유실인데 ACK만 성공시키는 구현은 금지한다.

## 9. Callback 트랜잭션과 재전송

수신 순서는 인증 → 크기/UTF-8/중복 JSON key 검사 → schemaVersion별 검증 → JCS digest → identity/증거 receipt 사전 조회 → 짧은 DB 트랜잭션이다. 트랜잭션 안에서는 해당 analysis/execution을 lock하고 owner·활성 세대·취소/삭제·lease/deadline·receipt association을 최종 대조한다.

동일 eventId + 동일 payload digest가 이미 commit된 경우만 200 DUPLICATE다. 기존처럼 commit된 이벤트의 재확인은 lease 만료 후에도 가능하지만 취소/삭제 정책을 우회하지 않는다. 같은 event의 다른 payload, 같은 execution의 다른 최종 event, stale execution은 409로 구분한다. 이벤트 key의 범위는 analysis/execution을 함께 보존하며 전역 event UUID 충돌도 거부한다.

2026-10-01 구현 보완: 10초 callback 예산에서 B2 전체 원본을 다시 읽고 오프라인 검증하지 않는다. 인증된 v4 본문은 `analysis_canonical_callback_inbox`에 원문·raw SHA·JCS SHA·event/execution을 먼저 commit한다. 별도 worker가 VERIFIED receipt의 정확한 객체 version 및 frozen 원본과 callback을 대조한다. 검증 중에는 기존 오류 형식의 503 `DEPENDENCY_UNAVAILABLE` / `Retry-After: 1`이며 성공 ACK나 새 202 계약을 추가하지 않는다. 동일 본문 재전송이 검증 완료를 확인하면 짧은 최종 transaction으로 반영한 후 200 APPLIED, 이미 반영됐으면 200 DUPLICATE다. callback 검증 worker는 AI/GPT를 실행하지 않는다.

원문 고정 재전송: 새 v4 inbox는 JCS digest뿐 아니라 raw SHA와 바이트도 대조한다. JCS가 같더라도 재직렬화로 raw bytes가 달라지면 409 `RESULT_EVENT_CONFLICT`다. 이는 frozen 수치의 반올림 충돌과 원본 바꿔치기를 막기 위한 신규 canonical 내부 전달 규칙이며 기존 v1/v2/v3 callback 처리는 바꾸지 않는다. RunPod durable outbox는 최초 생성한 event/body를 그대로 전송해야 한다.

신규 결과 저장·현재 조회 포인터·evidence 연결·공개 projection 갱신은 한 DB transaction으로 commit한다. 그 뒤에만 200 APPLIED를 응답한다. APPLIED/DUPLICATE의 event/request/execution/analysis 식별자 echo는 유지한다. 전송 실패는 core REJECT/SYSTEM_FAILURE로 재작성하지 않는다.

재전송은 같은 event/body/manifest를 사용한다. GPT/AI 재실행, 새 eventId 생성, workerInstanceId 덮어쓰기로 재전송을 대신하지 않는다. worker 재시작 후에도 ACK 재확인이 필요하면 해당 execution의 immutable 결과 outbox를 제한적으로 복구하는 설계가 필요하다. 만료/취소된 신규 결과 적용은 금지한다. 원격 저장소 장애·검증 진행 중은 제한된 Retry-After와 deadline 내 재시도로 처리한다.

2026-10-01 후속 로컬 구현은 [callback 전송·복구 문서](callback-delivery.md)를 따른다. 원래 worker의 아직 유효한 Backend lease를 heartbeat로 다시 확인한 경우만 같은 bytes 전송을 재개한다. 그 외에는 새 `POST /api/internal/ai/analyses/{analysisId}/result/ack`로 이미 적용된 결과만 확인한다. 이 경로는 새 inbox/결과를 등록·적용하지 않고 현재 visible APPLIED의 `DUPLICATE`만 응답한다. journal/전송 구현은 runtime·startup scheduler에 미연결이며 접수 off는 유지한다.

### 9.1 기존 ingestion의 후처리 보존

[AnalysisResultIngestionService](https://github.com/voice-coaching/VC-BE/blob/b48db988e7485943097acaa4c71308ef306175a8/src/main/java/org/example/voice/analysis/application/AnalysisResultIngestionService.java#L43-L118)는 저장 외에도 캐시 제거·segments 교체·영상 삭제 예약을 담당한다. v4에서 legacy worker 변환을 우회하더라도 이 책임은 없어지지 않는다.

- 첫 APPLIED transaction에 v4 본문·현재 포인터·event digest·기존 조회용 안전 projection·필요한 삭제 outbox를 함께 기록한다. 같은 analysis에 남은 legacy segments는 비우며 MFA를 점수 기반 segment로 변조하지 않는다. DUPLICATE는 후처리를 다시 생성하지 않는다.
- `DETAIL`, `SESSION_RESULT`, `SEGMENTS`와 신규 canonical 조회 캐시를 commit 이후 무효화한다. 무효화 재시도와 장애 시 stale 결과 차단을 설계한다. 초기 canonical 조회는 `Cache-Control: no-store`와 서버 캐시 비활성으로 시작한다. 기존 reader는 v4 및 새 세대의 legacy 재시도에서 과거 완료 캐시를 반환하지 않도록 현재 세대/visibility를 먼저 확인한다.
- 사용자 retry는 현재 포인터·공개 projection을 새 PENDING 세대로 전환하고 구 결과 캐시를 제거한다. 보존된 이전 v4 객체를 새 시도의 결과로 반환하지 않는다. `AnalysisResult.retry/fail/cancel/clearWorkerResult`에 새 포인터의 lifecycle을 명시하며 무조건 삭제하거나 방치하지 않는다.
- session/user 취소·녹음/회원 삭제·선택 녹음 변경·timeout에도 세대와 노출 상태를 점검한다. 취소/삭제는 이미 완료된 canonical 조회도 차단하고 관련 캐시를 제거한다. 분석 실패 원본의 비공개 보존 여부는 별도 retention 정책을 따른다.
- 기존 완료 후 영상 삭제 예약은 v4 COMPLETED에서도 유지한다. NOT_CONNECTED 영상이라고 무기한 남기지 않는다. REJECT/INCONCLUSIVE도 작업 완료이므로 기존 완료 삭제 정책을 따르고, FAILED에서는 기존 실패/TTL 정리를 따른다. B2 분석 근거 객체와 원본 사용자 영상은 다른 수명이며 MFA replay는 허용된 audio 재생 경로를 사용한다.

DB lock 순서는 기존 analysis → outbox 순서를 유지한다. 외부 B2 삭제·캐시 원격 I/O를 긴 row lock 안에서 수행하지 않는다. 취소·삭제 후 비공개 보존 객체가 남아 있다는 이유로 public 조회를 되살리지 않는다.

## 10. Backend 저장 및 공개 조회

기존 `analysis_results`를 작업/녹음 관계의 정본으로 유지하고 v4 근거는 버전 있는 별도 저장 구조로 추가한다. 다음 테이블명은 제안이며 migration 번호는 구현 시 develop의 다음 번호로 정한다.

- `analysis_canonical_results`: analysis/execution 유일 키, request/event/worker/profile/schema, payload digest, core SHA·UUID·manifest identity, core/adapter/generation 상태, 승인된 callback document JSONB, evidence receipt 관계. 재전송은 update로 내용 교체하지 않는다.

2026-10-01 저장 구현에서는 위 제안의 callback document를 `JSON`으로 구체화하고 원문 `BYTEA`·raw SHA를 별도로 유지한다. 동결 diagnostic의 유효한 Unicode escape를 JSONB 저장 제약으로 거부하지 않기 위한 선택이다. PostgreSQL은 JSONB에서 `\u0000`을 거부하고 JSON 입력은 해당 escape를 보관할 수 있다고 명시한다. [PostgreSQL JSON 유형](https://www.postgresql.org/docs/current/datatype-json.html). 내부 상태·식별자는 별도 typed 컬럼을 사용하며 diagnostic JSON의 SQL 변환·공개 반환은 하지 않는다. 원문 바이트와 frozen 계약은 변경하지 않는다.
- `analysis_evidence_receipts`: analysis/execution에 묶인 manifest·검증 상태·객체 식별자·SHA·크기·보존/삭제 상태. manifest digest 기반 중복 방지와 `VERIFIED` 상태의 불변성 보장.
- 기존 analysis row에는 현재 execution의 analysisProfile·요청/예정 결과 version을 admission 때부터 보존하고, commit된 결과 version·canonical 결과 관계를 별도로 명시한다. callback이 없던 timeout도 profile을 잃지 않는다. v3 coaching_document는 과거 결과 reader용으로 유지하며 v4 객체를 같은 Java `AnalysisCoaching` 타입에 끼워 넣지 않는다.

기존 `AnalysisResultResponseDto`에 frozen 객체를 끼워 넣지 않는다. canonical 전체 공개 projection은 아래 신규 조회 API의 `canonicalAnalysis` typed 필드로 제공한다. 원본 reason/stage와 동일한 code를 보존하되 한국어 안내 문장은 검토된 서버 사전으로 매핑한다. 알 수 없는 새 code는 정상으로 처리하지 않고 일반 보류/시스템 안내와 진단 추적을 남긴다. public API에서 inputValidation.evidence/original_error/raw provenance를 그대로 펼치지 않는다.

조회 권한은 recording 소유 관계를 계속 따른다. 녹음/회원 삭제 때 공개 조회와 현재 포인터를 차단하지만, canonical B2 원본은 위 무기한 정책에 따라 자동 물리 삭제하지 않는다. 기존 녹음·영상 삭제 outbox는 유지한다. 미래에 별도 승인된 근거 삭제를 구현할 때는 다른 execution의 참조와 정확한 객체 version을 확인한다. 미commit 업로드·취소 작업의 orphan도 현 정책에서는 보존하며 저장 비용과 접근 상태를 따로 관리한다. 이번 작업에서 객체나 DB를 삭제하지 않는다.

### 10.1 공개 API 호환 표

현재 [AnalysisService.getCompleted](https://github.com/voice-coaching/VC-BE/blob/b48db988e7485943097acaa4c71308ef306175a8/src/main/java/org/example/voice/analysis/application/AnalysisService.java#L18-L24)는 FAILED 상세를 거절한다. 이 메서드를 모든 실패 결과를 반환하도록 완화하지 않고 별도 API를 추가한다. 기존 JWT·사용자 소유권·`ApiResponse` envelope를 유지하며 내부 callback은 기존처럼 `ApiResponse`로 감싸지 않는다.

| API | v4 전환에서의 계약 |
|---|---|
| `POST /api/training-sessions/{sessionId}/analyze` | 기존 body/응답 유지, 2.2절의 선택 header로만 canonical 접수 opt-in |
| `POST /api/training-sessions/{sessionId}/analysis/retry` | 기존 FAILED 전용·횟수 제한·body/응답 유지, 5.1절 identity 적용 |
| `GET /api/training-sessions/{sessionId}/analysis/status` | 기존 status/stage/progress/failureReason 형태 유지. 100%는 처리 완료이지 ACCEPT 판정 아님. failureReason에는 검토된 안전 문구만 제공 |
| `GET /api/training-sessions/{sessionId}/analysis` | 기존 요약 DTO 유지. canonical 상태를 legacy outcome enum에 끼워 넣지 않음 |
| `GET /api/analyses/{analysisId}` | 기존 COMPLETED 전용 조건·오류 유지. v3 응답 그대로. v4 COMPLETED는 기존 shape의 안전 요약만 제공: outcome/점수/STT/legacy evidence/coaching=null, strengths/weaknesses는 빈 배열, summaryFeedback는 서버 안내. 전체 결과는 새 API에서 조회 |
| 신규 `GET /api/v2/analyses/{analysisId}` | canonical 전용 상태·결과 조회. 작업 FAILED의 core 근거와 전달 실패도 수용. 아래 HTTP 규칙 적용 |
| `GET /api/analyses/{analysisId}/segments` | 기존 권한·COMPLETED 조건·페이지 형태 유지. v4는 legacy segments가 없어 빈 페이지 반환. MFA 위치는 신규 canonical 조회의 후보 facts로 제공 |
| `POST /api/analyses/{analysisId}/feedback/regenerate` | v3 동작 유지. v4는 첫 릴리스에서 409 `FEEDBACK_EVIDENCE_UNAVAILABLE`; 후보/selectedPhone을 위조하거나 추가 GPT 호출하지 않음 |
| `POST /api/training-sessions/{sessionId}/complete` | URL/body/응답 유지. legacy는 기존 규칙, v4는 5.2절 학습 완료 gate 적용 |
| `/api/analysis-capabilities`, 녹음 업로드·선택·재생, TTS | 기존 기능 유지. v4 저장소를 사용자 녹음 저장소로 대체하지 않음 |

legacy-shaped v4 summary는 REJECT/INCONCLUSIVE에 입력 재확인/판단 보류를 명시하고 ACCEPT에도 발음 정상·만점을 표현하지 않는다. 이는 제한된 구형 화면용 안내이지 무손실 projection이 아니다. 새 FE는 이 summary만으로 canonical 결과를 표시하지 않는다.

신규 조회 응답의 `data`는 `schemaVersion=voice-coaching.canonical-analysis-view.v1`, analysis/recording/request/execution ID, `jobStatus`, `analysisProfile`, `canonicalAnalysis`, `serviceFailure`, `actions`를 가진다. canonicalAnalysis에는 정식 decision·상태·순서 보존 후보·G2P role·MFA facts·생성/fallback·점수 불가 정보를 allowlist로 제공한다. actions는 `canRetry`, `canRerecord`, `canComplete`, `canRegenerate`와 불가 사유 코드이며 서버가 판정한다. 해당 명령은 실행 시 권한·상태를 다시 검사하므로 조회 당시 true가 처리 권한을 보장하지 않는다. private object key·receipt 상세·original_error는 제외한다.

- 현재 canonical 작업이 PENDING/PROCESSING이면 200, `canonicalAnalysis=null`; pending을 판정 보류 INCONCLUSIVE로 만들지 않는다.
- 현재 canonical 결과가 COMPLETED/FAILED이면 200. 유효 core가 없는 실패는 canonicalAnalysis=null과 serviceFailure를 제공한다. callback 전에 timeout/전달 실패가 난 경우도 저장된 profile로 canonical 작업을 식별한다. 이전 세대의 core를 대신 반환하지 않는다.
- legacy 분석은 404 `CANONICAL_ANALYSIS_NOT_FOUND`로 구분한다. 새 FE는 소유한 결과에 대한 이 코드에서만 기존 조회로 분기하고, 401/403/5xx를 legacy로 오인하지 않는다.
- 존재하지 않거나 다른 사용자 소유, 삭제·취소·노출 금지된 결과는 404 `RESOURCE_NOT_FOUND`로 차단한다. 로그인 누락은 기존 401 정책을 따른다. 일반 사용자가 executionId를 바꾸어 숨겨진 과거 시도를 조회할 수 없게 한다.

현재 [FeedbackRegenerationService](https://github.com/voice-coaching/VC-BE/blob/b48db988e7485943097acaa4c71308ef306175a8/src/main/java/org/example/voice/analysis/application/FeedbackRegenerationService.java#L38-L62)는 legacy selected-phone 필드를 요구한다. 새 FE는 `canRegenerate=false`를 표시하고 새 조회 API로 저장된 표현을 다시 읽는다. 미래의 표현 재생성은 frozen 호출 횟수·후보 불변성·별도 generation identity를 정의한 뒤 다른 계약으로 추가한다.

## 11. 구현 작업 묶음

| 순서 | 담당 경계와 파일 | 완료 조건 |
|---|---|---|
| P0 계약 | AI `docs/contracts/runpod_result_v4.schema.json`, 제어 v1.2·요청 v2, Backend 동일 schema resources, 공개 조회/header 명세 | 필드·null·상태 조합·숫자/문자 길이·reference·오류 code 및 10.1절 API 표 확정, 양쪽 schema digest·package 일치 |
| P1 보존 저장 | AI 신규 evidence store/manifest, Backend receipt API·검증 worker·migration·노출 차단/보관 상태 | 원본 bytes부터 receipt까지 무결성·권한·무기한 수명 정책 구현, 일반 로그/FE 유출 없음. 기존 녹음·영상 삭제 outbox와 분리 |
| P2a 제어와 ingestion | `RunPodContract`, `RunPodAnalysisJobRequest`/`PayloadCodec`/client/dispatcher, `InternalRunPodAnalysisController`, 신규 v4 DTO/domain/validator, `AnalysisRunPodCallbackService` | 요청 v1/v2 dual reader·digest/deadline 유지, legacy worker 변환 우회, 네 축 저장, atomic APPLIED/DUPLICATE |
| P2b 공개 API와 업무 | `AnalysisController`/`AnalysisService`/결과 reader·DTO, 신규 v2 조회 controller, `TrainingAnalysisRequestService`/reader/writer, `TrainingSessionService`/reader, `TitlePersistence`/`TitleService`, `FeedbackRegenerationService`/`AnalysisSegmentService`, `SecurityConfig` | 2.2·5·10.1절 규칙, opt-in·FAILED 조회·FAILED 전용 retry·세대 변경·완료/시험 gate·부가 API 동작 구현 |
| P2c 후처리 | `AnalysisResult` lifecycle, `AnalysisResultIngestionService` 후처리 분리, `JpaAnalysisCancellation`, timeout·삭제 경로와 캐시 | 현재 포인터·노출 차단·legacy segments·캐시·영상 삭제 outbox를 9.1절대로 처리; 구 결과 재노출 없음 |
| P3 AI producer | `canonical_bridge.py`의 selection 보존, 신규 canonical executor/result serializer, `http_models.py`, `http_runtime.py`, `production.py`, readiness/배포 attestation | admitted 입력 → canonical → frozen bridge → 증거 보존 → v4 callback, 실패도 v4; 기존 경로와 선택 분리 |
| P4 FE 소비 | `src/lib/api/types.ts` 및 API client, `src/lib/coaching.ts`의 별도 reader, 신규 canonical view와 `analysis-view.tsx` 분기 | opt-in header·신규 조회·명시적 legacy fallback, 상태·G2P/MFA·fallback·actions 표시, opaque scoped key, 재시도 화면 격리 |
| P5 전환 | Backend admission gate/outbox publisher, RunPod 지원 profile·운영 구성, 담당자 E2E/배포 절차 | 신형 클라이언트의 v4 신규 접수만 점진 활성화, v3 지원 종료 조건 및 원복 확인 |

Backend는 현재 사용자 작업 브랜치를 덮어쓰지 않고 **develop 기반 별도 작업 및 PR**로 인계한다. FE의 배포 브랜치도 구현 직전 확인한다. 이전 조사 이후 변경된 source·schema·migration과 충돌을 먼저 해결한다. 코드와 docs가 같은 계약 revision을 가리키도록 인계한다.

Canonical executor는 frozen launcher가 생성하는 별도 process session·MFA 자식의 소유/종료를 추적해야 한다. 기존 executor process group 하나만 종료하거나 옛 180초 Clova drain만 유지하는 것으로 취소를 구현했다고 보지 않는다. 개별 실행의 정상 close, 제한된 종료, socket/workspace 정리, lease 만료 후 생성·callback 차단, 모델/bridge Python 환경 분리를 함께 설계한다. frozen 원본은 바꾸지 않고 wrapper에서 책임지며, 원본 변경이 불가피하면 별도 freeze 개정으로 분리한다.

## 12. 전환과 원복

1. 계약·저장 정책을 확정하고 Backend 확장 migration, 요청 v1/v2·결과 v3/v4 reader, 신규 조회/후처리/업무 gate를 먼저 배포한다. v4 접수는 닫고 구 요청·결과 조회는 유지한다.
2. FE에 신규 조회·actions와 legacy 분기를 배포한다. CORS header 지원을 준비하되 v4 opt-in 전송은 아직 끈다. 기존 공개 API가 구형 FE에서도 같은 body/envelope로 동작하는지 확인한다.
3. RunPod에 pinned canonical 환경·증거 저장·v4 producer와 요청 v1/v2 처리를 준비한다. 기존 readiness·legacy admission을 유지하고 v4 capability/schema digest·의존성 준비를 별도로 확인한다.
4. 개발자가 아래 호환성/E2E 확인을 마친 후 서버 gate와 새 FE opt-in을 제한적으로 활성화한다. 새 request/execution을 생성할 때만 profile/v4를 선택한다. 같은 녹음을 자동 이중 추론하는 shadow 실행은 기본 단계가 아니다.
5. header 없는 구형 클라이언트는 v3 접수를 계속 제공한다. **신형 FE 배포만으로 구형 탭/클라이언트가 사라졌다고 가정하지 않는다.** 지원 클라이언트 이행 및 별도 호환성 종료 결정 후, 진행 중 v3 작업을 drain하고 신규 v3 writer를 종료한다. 그 전에는 이중 접수를 유지한다. 과거 v3 reader는 데이터 보존기간 동안 유지하며 terminal event를 v4로 바꾸지 않는다.

원복은 **신규 v4 admission 중지 → 진행 작업 종료/취소·fencing 확인 → 새 request/execution에만 legacy profile 선택** 순서다. FE의 신규 요청 opt-in도 끄되 v4 조회 reader는 유지한다. v4를 명시한 요청에는 준비 불가 오류를 반환하며 header를 서버에서 무시해 downgrade하지 않는다. 이미 저장된 v4는 신규 조회 API로 계속 읽는다. Backend/FE를 v4 reader가 전혀 없는 옛 binary로 되돌리는 원복은 금지한다. schema/migration은 expand-first로 유지하고 즉시 drop하지 않는다. 독립 TTS·8082 라우팅은 원복 대상이 아니다.

v4로 처리하다 실패한 같은 execution을 v3 result로 재전송하지 않는다. 사용자 재시도/운영 복구는 5.1절에 따라 새 requestId·executionId와 고정된 profile로 처리한다. 배포 직전 실제 진행 작업·자동 배포 pending·실행 hash·원복 자산은 운영 절차로 다시 확인한다.

## 13. 개발자 수용 기준과 배포 차단 조건

이 표는 **개발자가 직접 수행할 확인 목록**이다. 자동 회귀 테스트·합성 입력·QA 산출물을 이번에 실행하거나 생성하지 않는다.

| 확인 범위 | 수용 조건 |
|---|---|
| 원본 보존 | core/bridge/projection/binding 객체를 다시 읽어 바이트 SHA·크기 일치; JSONB와 구분 |
| 네 상태 | ACCEPT/REJECT/INCONCLUSIVE/SYSTEM_FAILURE 및 core 부재를 분리; non-ACCEPT GPT 0회 |
| 후보 | 1/3개, omitted, 순서 변경·누락·추가·다른 scope·guidance 변조를 거부; 정렬로 보정하지 않음 |
| MFA/G2P | 원본 초 단위 위치·word occurrence·role·receipt 보존; CTC/writtenRole 대체 없음; 재생 시간축 미검증이면 버튼 비활성 |
| 생성 | 정상·refusal·중단·timeout·validator 실패·fallback을 core 상태와 구별; retry0 유지 |
| 저장/전송 | object 미완료·SHA 불일치·cross-execution reference·과대 본문 차단; ACK 유실 후 같은 event의 DUPLICATE |
| 생명주기 | stale callback·같은 event 다른 body·취소/삭제·lease/deadline·worker crash/재시작에서 재활성·유실·고아 자식 방지 |
| 화면/업무 | 네 상태·no coaching·fallback·미보정/null score 표시; 보류를 학습 성공으로 집계하지 않음 |
| 구형 공개 API | v3 요청/응답·동의·오류·JWT 유지. v4 COMPLETED의 legacy-shaped 안내가 정상 발음/만점을 주장하지 않음 |
| 실패 조회 | v4 SYSTEM_FAILURE/통합 실패/timeout을 신규 API로 조회; 기존 상세는 FAILED에 기존 409 유지. 취소/삭제/타인/이전 세대는 근거 재노출 없음 |
| 재시도/재녹음 | FAILED만 retry, 최대3회·새 request/execution; REJECT/INCONCLUSIVE는 새 recording 경로로 회복. 동일 전송 재시도에서 ID/GPT 횟수 불변 |
| 요청 이행 | 저장된 v1 outbox의 digest/claim/heartbeat/deadline 정상 유지. v2 추가 필드의 왕복 보존. 기존 readiness 응답 불변 |
| 학습/시험 | legacy 기존 판정 유지, v4 non-ACCEPT 완료 거절, ACCEPT는 절차 완료만 허용, 점수 없는 시험은 `ANALYSIS_SCORE_UNAVAILABLE` |
| 부가 API | v3 regenerate/segments 유지, v4 regenerate는 명시적 409·GPT 0회, segments는 빈 기존 페이지·MFA는 신규 조회 |
| 후처리 | APPLIED 1회만 삭제 outbox/segments 반영, retry·취소·삭제 때 캐시/현재 포인터 갱신, 영상과 B2 근거 수명 분리 |
| 전환/원복 | header 없는 구형 접수 유지, v4 명시 요청의 silent downgrade 금지, 기존 v4 조회 유지, 새 request/execution만 profile 전환, TTS 영향 없음 |

배포 차단 조건: 양쪽 계약 digest 불일치, 요청 v1/v2 재검증 미지원, 비공개 B2 profile/권한·보존/삭제 정책 미확정, source/prepared 시간축 대응 미확인 상태에서 replay 활성화, pinned bridge 환경 미구성, 자식 종료·workspace 정리 미검증, 실패 조회·재녹음·캐시/삭제 후처리 미수용, FE/업무 집계 미수용, 구형 클라이언트 지원 중 v3 admission 제거, 원복 불가. SLA/전체 deadline은 기존 단일 smoke latency로 확정하지 않고 담당자의 실제 canonical+저장+생성+callback 확인 결과로 정한다.

## 14. 구현 진행 현황

2026-10-01 최신 로컬 후속: Backend receipt/검증과 callback inbox/최종 저장, 요청별 프로필 전달·원자적 outbox/registry 발행, 현재 검증 결과 reader·학습 완료/시험 점수 불가 조건, 별도 canonical 공개 조회 allowlist/actions·non-ACCEPT 재녹음 복구까지 연결했다. 공개 admission은 계속 off이며 실제 FE 소비·복구 흐름 수용·RunPod 실행/전송·원격 배포는 남아 있다. 아래 표는 9/30 기반 구현 시점의 기록이다. 현재 범위는 [실행 기록의 마지막 절](rollout-execution-20261001.md)을 우선한다.

2026-09-30 후속 구현 기록이다. **부분 구현이며 P0~P5 완료나 배포 완료가 아니다.** 운영에는 아직 연결하지 않았다.

| 경계 | 작성한 코드/계약 | 남은 연결 |
|---|---|---|
| 요청 | [request v2 schema](../contracts/runpod_analysis_request_v2.schema.json), Python `http_models/http_api`, Backend DTO·codec·outbox의 v1/v2 분기. v1 원문/digest 보존 | v4 admission 활성화와 실제 canonical 실행 |
| 접수 보호 | Python canonical 요청 `503 NOT_READY`; Backend v4 opt-in `503 ANALYSIS_INTEGRATION_UNAVAILABLE`, 알 수 없거나 중복 header 400. header 없으면 legacy 유지 | gate는 의도적으로 비활성. 준비 기능이 없는 상태에서 환경변수만으로 우회하지 않음 |
| 상태 공개 | Backend 인증 `worker-readiness/v2`는 legacy 지원만 광고, canonical/evidence NOT_READY. 기존 readiness 불변 | receipt worker·DB·실행 readiness 연결 |
| 결과 계약 | [result v4](../contracts/runpod_result_v4.schema.json)와 frozen schema 사본, [canonical_result.py](../../src/voice_coach/backend_analysis/canonical_result.py)의 scoped ID·순서·guidance·MFA·상태/크기 검증과 serializer | Backend typed result ingestion·원본 검증 worker·현재 결과 포인터·callback ACK 연결 |
| 원본 연결 | 동결 source/lock SHA 재대조, `canonical_bridge.py`의 prepare selection bytes 보존 | canonical launcher/process 취소·deadline·정상 종료·별도 pinned Python 환경 |
| 저장 | [canonical_evidence_store.py](../../src/voice_coach/backend_analysis/canonical_evidence_store.py): 전용 비공개 B2, version 지정 읽기·SHA/크기 검증, 명시적 무기한 정책 파싱. [canonical_retention.py](../../src/voice_coach/backend_analysis/canonical_retention.py)는 검증 원본 업로드/manifest 조립 경계 | 실제 bucket/prefix/key, lifecycle 대조, Backend 검증 receipt, durable outbox. 업로드·자동 삭제 수행 안 함 |
| Backend 실행 식별자 | V32 `analysis_canonical_executions` 추가 migration, immutable snapshot 등록·현재 소유자/실행 조회 adapter | migration 미적용. publisher 등록 호출·result/receipt 테이블·공개 API·업무/캐시 후처리 미연결 |
| FE | 엄격한 canonical public reader, `404 CANONICAL_ANALYSIS_NOT_FOUND`에만 legacy fallback, 독립 상태/후보/G2P/MFA 화면 | public wrapper/actions의 Backend 합의·기존 화면과 새 조회 연결·opt-in 미활성. 재생 시간축 미확인으로 replay 비활성 |

Backend는 develop `b48db988e7485943097acaa4c71308ef306175a8`, FE는 dev `d54ceae5065880ccabd8f00ffe83500ab9077dfd`에서 각각 `feat/canonical-callback-v4-20260930` 별도 worktree를 만들었다. 기존 sibling checkout의 사용자 변경은 보존했다. 작업 사본은 PC의 `C:/Users/Public/Documents/ESTsoft/CreatorTemp/canonical-v4-be-20260930`, `canonical-v4-fe-20260930`이며 다른 개발자의 필수 저장소 경로가 아니다. Backend 인계 대상은 develop PR이고 담당자가 검증·배포한다.

이번 원격 접근은 이미 실행 중인 Pod 관리 상태/SSH mapping, S7 코드·schema·lock 및 R launcher 읽기뿐이다. 가져온 adapter·parent/input/output schema SHA가 dependency lock과 일치함을 확인했다. 원본 schema 사본은 숫자 lexical 표기와 줄바꿈까지 유지하며 `.gitattributes`로 Git 줄바꿈 변환을 막는다. 모델/GPT 실행, 운영 재시작, B2 업로드, DB migration 적용, 자동 테스트·품질 QA, commit/push/PR·배포는 수행하지 않았다. 기존 분석·TTS·8082와 frozen 원본은 변경하지 않았다.

확인 범위: Python 3.12 구문 컴파일(8개 파일), Backend `compileJava`·`processResources`(테스트 제외), FE `tsc --noEmit --incremental false`, 신규 schema 6개/참조 133개의 정적 확인, frozen schema 3개의 원본 SHA 일치, AI·Backend 소스 및 Backend package의 schema 6개 바이트 일치, 문서 상대 경로 점검. 로컬 `jsonschema` 의존성이 없어 Python validator의 meta-schema 실행 검사는 수행하지 않았다. SQL migration 적용·실제 API·추론·전달 완주·품질 QA 통과를 뜻하지 않는다.

추가 발견: 기준 checkout의 기존 v1 `utcTimestamp.pattern`에 점(`.`) 앞 이중 역슬래시가 남아 있다. 신규 request v2/control v1.2는 `[.]`로 명시했으며 구 계약은 이 작업에서 임의 수정하지 않았다. 기존 운영 revision의 실제 schema·parser와 별도로 대조해야 한다. 로컬의 과거 Clova factory를 포함해 이 checkout 전체를 운영 위에 덮어쓰면 안 된다.

## 15. 접수 승인과 배포 전 재확인

사용자가 전체 통합·배포와 v4 접수를 명시 승인했다. 기존 무기한 보존 결정도 유지한다. 접수 flag만 변경해 아직 없는 executor/receipt/ingestion 경로로 작업을 보내지는 않는다.

2026-09-30 **23:28:04 KST (14:28:04 UTC)**, 관리 API에서 확인한 `s8hb5b0l5k4qlx` RUNNING 및 현재 SSH mapping을 사용해 읽기 전용 재확인했다.

| 관측 | 실제 확인 범위 |
|---|---|
| 분석 인증 `/health/services` | HTTP 200, ready, executorReady=true, reasonCode=null, OPENAI / gpt-6-astra |
| 실행 revision | `32d98e39767b56697bc7e58fb50e3e9d61432349`; deployment metadata와 health 일치 |
| 실행 경로의 canonical 모듈 | `/opt/intelligentai/app/src/voice_coach/backend_analysis/canonical_*.py` 없음. 로컬 신규 코드는 아직 배포되지 않음 |
| 로컬 설정 | 프로젝트 `.env`에서 `B2_*` / `AI_CANONICAL_*` 설정 이름을 찾지 못함. 비밀값 출력 안 함 |
| 문서상 Pod 설정 경로 | `/root/.config/intelligentai/.env` 없음. source 및 runtime `analysis-http.env`에도 `B2_*` / `AI_CANONICAL_*` 이름 없음 |
| 프로세스 격리 전제 | `/sys/fs/cgroup/cgroup.controllers` 및 `cgroup.kill` 없음. cgroup v2 kill을 사용할 수 있다고 가정하면 안 됨 |

이 health는 기존 legacy 분석 경로의 운영 관측이며 canonical 준비·GPT 생성·품질 QA 성공이 아니다. AWS 실행 revision·TTS·Frontend 운영 상태는 이 재확인에서 조회하지 않았다. 서비스 중지/재시작, DB migration, 설정 주입, B2 변경, v4 활성화는 수행하지 않았다.

전체 활성화의 외부 선행조건:

1. 전용 비공개 B2 bucket/prefix/region/endpoint 및 제한된 S3 application key의 비밀 공급원 지정. 확인한 경로 밖에 이미 설정이 있다면 그 **경로**를 제공받아 확인한다. 키를 채팅이나 docs에 적지 않는다. 연구자료/녹음 credential을 임의 재사용하거나 docs를 근거 저장소로 대체하지 않는다.
2. Backend 배포 권한은 후속 사용자 응답 **“1. 허가한다.”**로 확보했다. 이번 canonical v4 작업에 한해 기존 PR 인계 후 담당자 배포 규칙의 예외로 **직접 AWS Backend·DB 배포가 승인**됐다. 대상·현재 revision·migration·복구 수단 확인은 여전히 필요하며, 운영 승인만으로 미구현/미검증 경로를 활성화하지 않는다.
3. 14절의 미연결 코드 완성과 개발자 수용 확인. 사용자의 v4 접수 승인은 자동 테스트/QA 금지 지침을 해제하지 않으며, 현재 없는 검증 결과를 완료로 기록하지 않는다.

이전 B2 설정 점검에서 `.env`의 `BACKBALZE_MANAGEMENT_API_TOKEN`만 존재하고 key ID가 없어 인증하지 못했다. 이후 사용자가 키 쌍을 제공해 아래와 같이 이 차단 조건을 해소했다. 기존 `.env`를 덮어쓰거나 마스터 키를 문서·소스에 저장하지 않았다.

### 15.1 B2 인증 차단 해소 — 2026-10-01

**00:02:49 KST (2026-09-30 15:02:49 UTC)**에 공식 Native v4 `b2_authorize_account`와 `b2_list_buckets` 호출에 성공했다. 마스터 키는 해당 조회 프로세스에만 사용했고 응답의 인증 토큰·비밀키는 출력하지 않았다.

| 항목 | 관측/구성 구분 |
|---|---|
| B2 endpoint / region | 인증 응답의 `https://s3.us-east-005.backblazeb2.com` / `us-east-005` |
| 대상 버킷 | `speakai2026`, `allPrivate` |
| Lifecycle | 빈 규칙 배열. 이 관측 시점에 자동 만료 규칙 없음 |
| 기본 암호화 | `SSE-B2`, `AES256` |
| Object Lock | 비활성. 무기한 보존 정책을 강제 WORM 보장으로 표현하지 않음 |
| 전용 prefix | `canonical-evidence/v1/` 구성안. 기존 사용자 녹음/연구자료 prefix와 분리하며 아직 업로드하지 않음 |
| 운영 자격증명 | 마스터 Native 인증만 확인. bucket/prefix 제한 uploader·reader 키는 아직 발급/주입하지 않음 |

추가 B2 인증정보 제공 대기는 해소됐다. 이번 확인은 계정 인증·메타데이터 읽기이며 S3 업로드·원본 readback·Backend VERIFIED receipt·전체 통합 성공이 아니다. bucket/객체/lifecycle 수정이나 서비스 배포는 하지 않았다. 남은 서비스 연결 구현은 14절의 미완료 항목을 따른다. [배포 설정](../../deploy/runpod/README.md#canonical-callback-v4-근거-보관-설정-미활성)에 실제 확인값과 미주입 상태를 함께 반영했다.
