# 음성 분석 지연 개선 구현 계획

작성 기준: 2026-10-03 운영 관측, 2026-10-04 계획 구체화. 문서 파일명의 날짜는 측정 기준일이다.

상태: **코드 구현 진행, 전체 계획·운영 수용 미완료**. 2026-10-04 [구현 현황](latency-implementation-20261004.md)과 [v5 인계·archive 구현](handoff-v5-20261004.md)에 코드·컴파일과 미완료 운영 확인을 구분했다. 아래 P3/P4의 최종 계약·테이블·설정은 v5 구현 문서를 따른다. 배포·개발자 수용 완료 기록은 아니다. 기존 운영 계약은 [callback v4 계획](callback-v4-plan.md), 측정 근거는 [구간별 시간](testvideo1-pipeline-timing-20261003.md), [병목 조사](pipeline-bottleneck-analysis-20261003.md)를 따른다. 로컬에 있을 경우 `incident-20261003-capacity-api-audit.md`도 참고한다. 이번 계획은 B2 완료를 사용자 결과의 선행조건으로 두는 기존 설계를 새 opt-in 경로에서 변경한다.

## 1. 목표와 유지 조건

사용자 결과까지 필요한 경로를 **접수 → 음성/모델 준비 → 코어 → GPT·검증 → Backend 영속 인계 → 의미 검증·결과 commit**으로 줄인다. B2 업로드·readback·보존 영수증은 결과 commit 이후 독립 작업으로 수행한다.

- 서버 rubric 계산, H5 검산 응답, unscorable 실패 정책, Seungun 전체 음소 근거, Native/MFA 판정·scoped ID를 유지한다. GPT 응답을 기다리지 않는 점수 조기 확정은 이번 변경에 포함하지 않는다.
- 원본 파일 SHA와 현재 사용자/녹음/선택/시도 관계, 서버 인증, 취소·삭제·deadline 검증을 유지한다. 동의 grant나 요청 서명은 추가하지 않는다.
- 영구 근거 저장소는 B2다. Backend PostgreSQL은 영속 인계와 후처리 복구를 담당한다. RunPod scratch를 영속 queue로 사용하지 않는다.
- 기존 보존 정책 `INDEFINITE`를 유지한다. 새 자동 삭제/TTL은 추가하지 않는다. spool 용량·WAL·백업 비용을 활성화 조건에 포함한다.
- CPU/GPU 장치·dtype·채점표·모델 버전은 우선 유지한다. 준비 비용 제거와 추론 알고리즘 변경을 분리한다.
- 기존 legacy/v4 요청은 기존 완료 계약으로 처리한다. 처리 중인 실행의 profile을 바꾸거나 v4를 자동으로 새 경로로 전환하지 않는다.

초기 성능 수용 목표(보장값이 아닌 구현 후 확인 기준): testvideo1, 무부하·준비된 worker에서 요청→사용자 결과 **90초 이내**, 빈 슬롯에서 접수→실행 **5초 이내**, 코어 완료→영속 인계·검증·결과 공개 **20초 이내**. 부팅의 cold 준비 시간은 별도 보고한다. 여러 사용자 대기 시간은 앞 요청 처리량에 좌우되므로 90초 목표를 그대로 적용하지 않는다.

## 2. 실행 흐름과 병렬 경계

| 구간 | 실행 방식 | 다음 단계가 기다리는 조건 |
|---|---|---|
| 배포/worker 시작 | 코드·가중치 검증, Python import, Native/Seungun 로딩, MFA 기동 | 해당 revision의 준비 완료 후 admission ON |
| 요청 접수 | Backend durable outbox, deadline 고정 | 서버 접수 즉시 기존 202 흐름 반환 |
| 예약·음성 준비 | 기존 claim 이후 journal 예약과 다운로드를 제한적으로 병렬 수행 | 둘 다 성공하고 digest/소유권 검증 후 one-shot producer start |
| 코어 | 상주 worker에 현재 execution 1개 전달 | Native/Seungun/MFA 결과 완료 |
| GPT·사전 검증 | 코어 결과로 GPT 요청; 독립된 core bytes 인계 준비와 겹칠 수 있음 | 최종 응답·rubric 검증 완료. 실패를 성공 점수로 바꾸지 않음 |
| 결과 인계 | 완성된 원본 집합과 결과 projection을 Backend에 영속 저장 | 원자적 RECEIVED와 handoff digest 확인 |
| 슬롯 해제 | 인계 확인 후 현재 요청 scratch/IPC 정리 | 상주 모델은 유지하고 요청별 상태만 제거 |
| Backend 결과 검증 | DB에 보관한 원본으로 offline 검증 | 검증 후 result commit + archive outbox 원자 기록 |
| FE 결과 | 현재 세대의 검증·commit된 점수와 코칭 조회 | B2 상태와 무관하게 기존 완료 조건으로 표시 |
| B2 보존 | Backend durable archive worker | 후처리 상태만 변경; 결과/GPT 재실행 금지 |

`asyncio.gather`는 독립 작업에만 적용한다. 다운로드 실패 시 예약 결과를 조정하고 sibling 작업을 취소·회수한다. 코어보다 음성 다운로드를 늦게 완료시키거나 GPT 결과 없이 검증된 점수를 반환하지 않는다. 요청별 코어 내부 Native/MFA 병렬화는 기존 제한 이유와 자원 경쟁이 있어 초기 범위에서 제외한다.

## 3. 작업 패키지와 완료 조건

### P0 — 공통 계측과 기준선

- AI stage 로그에 UTC 시작·종료, monotonic duration, analysis/request/execution ID, worker revision, 결과 코드 추가. 원문 대본·음성·키·객체 URL은 제외한다.
- Backend에 outbox queue wait, dispatch attempt/code, DB staging, semantic verifier spawn/verify, result commit, archive queue/PUT/readback span 추가.
- `queue_wait`, `cold_prepare`, `core`, `gpt`, `handoff`, `verify`, `commit`, `archive`를 별도 집계한다. 중첩 duration은 합산하지 않는다.
- 측정되지 않은 MFA feature 하위 작업, callback 적용 11.3초의 DB/lock/스케줄러 시간을 이 계측으로 분리한다.
- 완료 조건: 한 execution ID로 양쪽 span 연결 가능, 로그에 비밀값 없음, 사용자 응답 시간과 보존 완료 시간을 각각 산출 가능.

### P1 — 접수와 readiness 결합 제거(기존 v4에도 적용 가능)

**RunPod**

- `HttpRuntime.accept()`의 readiness를 요청 profile별로 분기한다. canonical은 canonical 모델/Backend 계약/녹음 저장소 준비를 검사하고 legacy executor의 생존 여부를 요구하지 않는다.
- canonical 종료 후 legacy를 무조건 부팅하지 않는다. legacy 요청은 기존 경로에 맞춰 별도 준비한다. 두 계열을 동시에 메모리에 유지할지는 자원 한도 확인 뒤 결정한다.
- 용량이 찼다는 429와 의존성/구성 불가 503을 구분한다. 응답·로그에 고정 reasonCode와 Retry-After를 기록한다.

**Backend**

- `runpod_http_429`의 capacity busy는 일반 전송 실패 횟수에 넣지 않는다. `busy_count`, `transport_failure_count`, 마지막 사유를 구분한다.
- 초기 제안 backoff는 1→2→4→5초 상한 및 작은 jitter. 유효한 Retry-After와 전체 deadline을 존중하고, worker endpoint 단위 다음 허용 시각으로 모든 대기 요청의 동시 재시도를 막는다.
- dispatcher는 해당 worker의 가장 오래된 유효 PENDING을 우선한다. 다중 Backend에서도 DB claim/lease 또는 endpoint별 소유권으로 중복 전달을 제한한다. DB lock을 HTTP 호출 동안 잡지 않는다.
- 진짜 5xx/통신 실패는 별도 예산으로 처리하며, 이미 claim된 실행은 submit 응답 유실로 FAILED 처리하지 않는다. 명백한 schema/auth 오류는 capacity로 무한 재시도하지 않는다.
- 요청 deadline은 최초 접수에서 고정하며 재시도 때 연장하지 않는다. deadline 만료는 지정된 terminal reason으로 종료한다.

완료 조건: 분석 68과 같은 연속 429만으로 영구 전송 실패하지 않음; 슬롯이 비면 오래된 요청부터 진행; canonical이 legacy 로딩을 기다리지 않음; 취소·만료된 대기 요청은 실행되지 않음.

### P2 — canonical core/MFA 상주화

- 기존 core v2 디렉터리를 덮어쓰지 않고 새 revision의 실행 패키지를 만든다. H5 정책은 유지하고 source/core manifest 변경분을 양쪽 verifier 등록에 반영한다.
- `CanonicalResidentWorker`(신규 제안)가 Python import, Native/Seungun 모델, MFA host를 한 번 준비한다. 상태는 `STARTING → READY → BUSY → READY`, 오류 시 `UNHEALTHY → RESTARTING`이다.
- 로컬 Unix socket/제한 IPC를 사용한다. 요청은 execution ID·deadline·검증된 입력 경로/digest·script binding, 응답은 같은 ID와 제한 크기의 core bytes/digest다. 원격 임의 경로는 받지 않는다.
- 초기 동시 추론 수는 **1**로 유지한다. 제어 API/heartbeat, 다운로드·GPT/인계, B2 후처리의 수명은 추론 worker와 분리한다. 전체 in-flight queue는 bounded로 둔다.
- P2 단독 배포에서는 기존 execution 슬롯 수명을 유지한다. P3 연결 후에는 manager의 요청 context를 `self.active` 단일 객체에서 execution별로 분리하고, 추론 1개와 GPT/인계 1개까지 겹치는 **최대 2개 in-flight**를 초기값으로 한다. core bytes가 manager에 완전히 전달되고 요청별 core 상태가 정리되면 추론 슬롯만 반환한다. 완료 전 execution의 heartbeat·취소 감시는 별도 유지한다. 후처리 슬롯이 차면 새 코어 작업도 backpressure하며 메모리에 결과를 무제한 쌓지 않는다. 영속 인계 전 결과 유실 시 재추론하지 않는 정책은 동일하다.
- MFA 모델/프로세스는 상주시키되 corpus DB·임시 파일·alignment state는 요청별로 분리하고 정리한다. 다른 사용자의 정렬 상태를 재사용하지 않는다.
- one-shot producer 시작은 worker dispatch 전에 DB에 기록한다. 응답 유실·worker crash는 같은 execution의 재추론 허가가 아니다. 확정된 결과/인계 상태를 먼저 확인하고 불명확하면 실패/수동 복구 정책을 따른다.
- 취소·timeout은 현재 요청과 그 하위 작업을 중단하고, 완전한 회수가 확인되지 않으면 worker를 재시작한다. 회수 전 다음 작업을 받지 않는다.
- 배포 자산 검증은 immutable revision 활성화와 worker 기동에서 수행한다. revision 변경/worker 재시작 시 캐시를 무효화한다. 요청별 audio/script digest 검증은 매번 수행한다.

과도기 대안: 상주 worker 전환 전 MFA 시작과 AI import를 병렬 기동해 필요한 지점에서 합류시킬 수 있다. 다만 최종 목표는 매 요청 import 83초와 MFA 준비 49.7초의 반복 자체를 없애는 것이다.

완료 조건: 연속 요청에서 import/version/MFA bootstrap 반복 없음; 요청 간 원본·시간 정렬·전역 설정 오염 없음; 기존 rubric/판정/후보·MFA 근거의 의미 동등성 확인; crash 후 새 worker readiness 전 접수 차단.

### P3 — 결과 공개와 B2 보존 분리(양쪽 신규 계약)

**버전 선택**

- 새 내부 결과 전달은 `voice-coaching.canonical-handoff.v1` 계약으로 추가한다. 현재 receipt 필수인 callback v4 schema는 수정하지 않는다.
- analyze/retry는 capabilities가 허용한 새 result schema/profile을 명시한 경우에만 새 경로를 사용한다. 제안 식별자는 `voice-coaching.runpod-analysis-result.v5`; 이는 전송·완료 계약의 버전이며 GPT 하네스 H5나 채점표 변경을 뜻하지 않는다.
- 새 공개 projection은 `GET /api/v3/analyses/{analysisId}`에 둔다. 기존 `/api/v2/analyses`와 v4 history 의미는 유지한다. FE는 저장된 analysis profile에 따라 조회한다.
- 최종 식별자 중복 여부와 migration 번호는 구현 시작 시 양쪽 HEAD에서 확인한다. 이 문서의 이름들은 신규 제안이다.

**새 내부 API 제안**

| API | 책임/응답 |
|---|---|
| `POST /api/internal/ai/analyses/{id}/handoffs` | metadata/projection 및 선택적 inline 원본 접수. 전체가 검증·영속화되면 202 RECEIVED, metadata만 있으면 202 STAGING |
| `PUT .../handoffs/{handoffId}/artifacts/{kind}` | 큰 원본 stage. 같은 digest/bytes는 멱등 204, 다른 bytes는 409 |
| `POST .../handoffs/{handoffId}/seal` | 전체 집합과 projection 결속 검증 후 원자적 RECEIVED. 부분 stage는 완료 처리 불가 |
| `GET .../handoffs/{handoffId}` | 접수 여부·digest·검증/결과 상태 조회. UNKNOWN과 단순 미완료를 구분 |

모든 내부 요청은 기존 양방향 서버 인증과 원래 worker/execution binding을 사용한다. caller 제공 URL을 fetch하지 않는다. metadata는 schemaVersion, request/execution/analysis/recording/content/event/worker ID, request digest, core/H5/schema revision, artifact kind/schema/digest/byteSize, projection digest를 포함한다. handoff digest는 정렬된 metadata와 projection의 canonical bytes에서 계산하고 원본 bytes digest와 결속한다.

- 소형 경로: 전체 HTTP JSON 상한 **2 MiB** 안에서 원본을 base64로 함께 보낸다. 문자열 재직렬화 대신 decode한 원본 bytes의 SHA를 검사한다. testvideo1의 약 234KB 원본은 이 경로 대상이다. JSON/base64/원본의 중복 메모리도 제한한다.
- 대형 경로: metadata 생성→artifact별 raw PUT(동시성 초기 **2**)→seal. artifact당 기존 **16 MiB**, 최대 **5개**, 공개 projection 기존 **1 MiB** 한도를 유지한다. 압축 폭탄·무제한 buffering을 허용하지 않는다.
- body 수신/크기/hash 검사 동안 긴 DB transaction을 열지 않는다. 최종 transaction에서 budget과 전체 bytes/identity를 검증·영속화한다. HTTP가 끊기면 같은 handoff ID/digest로 조회·재전송하며 inference/GPT는 반복하지 않는다.
- 같은 execution의 다른 최종 event/digest는 409. 동일 인계 재요청은 같은 handoff와 결과로 수렴한다. 인증 401/403, stale/cancel/충돌 409, 크기 413, 근거 형식 422, 실제 용량/저장소 장애 429/503을 구분한다. 단순 PENDING을 503으로 표현하지 않는다.

**영속 상태와 소유권**

- 신규 제안 `analysis_canonical_handoffs`: IDs, transport revision, metadata/projection raw+digest, state, received/verified/committed timestamps, claim owner/until, attempt/reason. execution 유일 최종 인계 및 event/digest 충돌 제약.
- 신규 제안 `analysis_canonical_handoff_artifacts`: `(handoff_id, kind)` PK, schema/digest/size/raw bytes, stage 상태. 기존 v4 journal row의 업로드 상태를 새 의미로 바꾸지 않는다.
- 신규 제안 `analysis_canonical_archive_jobs`: handoff 유일 FK, state/attempts/next_attempt/claim lease/last code, immutable version별 checkpoint. artifact별 보존 상태는 별도 행 또는 정규화된 child table로 둔다.
- 상태: handoff `STAGING → RECEIVED → VERIFYING → COMMITTED`; 실패 `REJECTED`, 공개 차단 `REVOKED`. archive `PENDING → UPLOADING → VERIFYING → ARCHIVED`, 재시도 `RETRY_WAIT`, 불확실한 PUT `RECONCILE_REQUIRED`.
- **RECEIVED는 결과 성공이 아니다.** 이 transaction이 모든 원본과 검증 작업을 영속 기록한 뒤, execution의 처리 소유권을 Backend로 넘긴다. 이 시점 이후 RunPod heartbeat 종료를 lease timeout으로 오인해 인계를 취소하지 않도록 timeout sweeper·claim 규칙을 함께 바꾼다.
- 최초 handoff 확정 시 원래 lease/owner/deadline/현재 세대를 검사한다. 인계 후 verifier는 Backend job claim과 원래 전체 deadline·취소/삭제 fence를 따른다. 사용자 retry로 새 세대가 생기면 이전 인계는 공개되지 않는다.
- Backend는 DB 원본으로 기존 frozen 의미·schema·동일 시도·rubric·GPT 응답을 검증한다. B2 readback을 결과 의미 검증의 전제에서 제거한다. Python verifier 상주화는 제한된 비밀 없는 프로세스로 별도 수행하거나, 우선 spawn 비용을 계측하고 필요 시 적용한다.
- 검증 후 **canonical result + analysis 완료 + archive outbox + cache effects**를 같은 transaction으로 기록한다. cache 실패는 결과를 잃게 하지 않으며 current-generation read fence로 stale 노출을 막는다.
- 정상 ACCEPT뿐 아니라 검증된 REJECT/INCONCLUSIVE 및 의미상 유효한 FAILED 결과도 기존 표시 정책에 따라 한 번 확정하고, 생성된 근거가 있으면 archive outbox를 남긴다. 코어 생성 전 실패는 별도 명시적 schema 분기로 원인과 identity만 보내며 가짜 core/artifact를 만들지 않는다. handoff 자체가 schema/hash 불일치로 REJECTED이면 사용자 성공 결과를 생성하지 않고 이미 저장된 원본의 비공개 보존·운영 조사 상태를 유지한다.
- 학습 완료/이력/점수 eligibility는 새 profile의 검증·commit 상태를 사용한다. B2 미완료만으로 `canComplete=false`로 만들지 않는다. ACCEPT/REJECT/INCONCLUSIVE와 점수 유효성은 기존 의미대로 구분한다.

Backend 인계 전에 Pod가 죽으면 원본 유실 가능성이 남는다. 이때 저장되지 않은 결과를 재구성했다고 주장하거나 같은 execution으로 GPT를 자동 재실행하지 않는다. 인계 이후에는 Pod 재시작과 무관하게 검증·보존이 복구돼야 한다.

### P4 — 보존 worker와 중복 왕복 제거

- Backend에 B2 writer client/adapter를 추가한다. 현재 read-only 자격증명에 쓰기 권한이 있다고 가정하지 않는다. 기존 canonical prefix에 제한된 별도 writer 키를 주입하며 reader·로그·Git에는 노출하지 않는다.
- archive worker는 result commit 후 durable outbox를 claim하고 bytes를 DB에서 읽는다. 초기 B2 artifact 병렬도 **2**, S3 connection pool은 그에 맞춰 제한한다. 한 artifact의 PUT intent→PUT→version checkpoint→exact-version readback 순서는 유지한다.
- PUT 응답 유실은 확정 실패와 구분한다. 미확정 버전을 무한 재PUT하지 않고 `RECONCILE_REQUIRED`로 남기며 해당 전용 권한의 조사 경로로 조정한다. 원본은 spool에 유지하고 다른 artifact/사용자 결과를 막지 않는다.
- 명확한 일시 실패는 5초부터 최대 5분까지 backoff+jitter. 반복 실패는 운영 경보와 재시도 상태로 남긴다. 보존 실패가 이미 유효한 사용자 점수를 취소하지 않으며 AI/GPT 재실행을 촉발하지 않는다.
- archive job은 inference deadline 이후에도 Backend 보존 정책에 따라 수행한다. 사용자 취소/삭제의 접근 차단과 보존 정책을 구분하고 공개를 복구하지 않는다.
- 새 경로에는 RunPod의 artifact별 33회 journal 호출, receipt polling, 동일 결과 503 재POST가 없다. version/readback 결과는 Backend가 직접 checkpoint한다.
- 기존 v4 경로를 유지하는 동안 `BackendCanonicalJournal`은 한 execution의 동기 HTTP client/connection pool을 재사용하도록 정리할 수 있다. event loop가 다른 `asyncio.run()` 사이에 AsyncClient를 공유하지 않는다.
- receipt/callback 재검증은 immutable artifact digest+버전+검증기 revision을 키로 검증된 bytes를 재사용한다. callback 자체의 projection/association 검증까지 생략하지 않는다.
- B2 ARCHIVED 이후 spool 삭제는 기존 승인된 보존 정책과 별도다. 이 계획에서는 자동 삭제하지 않는다. backlog bytes와 전체 저장 용량을 감시하고 quota가 부족하면 **새 접수 전** backpressure를 걸어 기존 완료 결과를 보호한다.

완료 조건: B2 장애에서도 영속 인계된 유효 결과는 공개 가능; Backend 재시작 후 같은 bytes/version으로 후처리 복구; 중복 결과/무한 PUT 없음; archive backlog가 제한 용량을 넘지 않음.

### P5 — FE 대기·완료와 MFA 남은 병목

**FE**: 기존 `/api/backend` proxy 유지. capabilities와 analysis profile에 따라 신규 결과 client/parser를 선택하고 기존 이력은 기존 parser로 읽는다. 서버 상태가 PENDING/PROCESSING인데 로컬 10분 타이머가 끝났다는 이유로 FAILED라고 표시하지 않는다. 자동 polling 중단 시 같은 analysisId의 ‘결과 다시 확인’을 제공하고 새 analyze POST를 자동 실행하지 않는다. 서버 deadline을 공개 응답에 제공해 대기 기준을 맞추되 클라이언트 시계만으로 서버 실패를 확정하지 않는다. archive 상태를 사용자 완료 대기 조건이나 불필요한 기술 상태 UI로 노출하지 않는다.

**MFA**: 상주화 후에도 요청별 30.8초가 남는지 재측정한다. feature 내부의 corpus import·DB 초기화·model/dictionary 준비·프로세스 실행·음향 특징 계산을 구분한다. immutable model/dictionary만 캐시하고 음성별 feature/정렬은 재계산한다. `NUM_JOBS`, thread, scratch 위치 변경은 같은 음성의 alignment/score 동등성이 확인된 것만 채택한다. 20초 초과 세부 span은 사유와 다음 변경 후보를 남긴다.

## 4. 파일·저장소별 변경 지도

| 저장소/경계 | 기존 수정 대상 | 신규 제안 책임 |
|---|---|---|
| AI 접수 | `http_runtime.py`, `http_settings.py`, `http_api.py` | profile별 readiness, 고정 거절 사유, bounded lane |
| AI 실행 | `canonical_lane.py`, `canonical_executor.py`, `canonical_producer.py`, `canonical_process*.py`, `canonical_lease.py` | resident manager/client, 요청별 취소·정리, 영속 인계 뒤 슬롯 해제 |
| AI 전달 | `canonical_backend_journal.py`, `canonical_backend_delivery.py`, `canonical_result.py`, `canonical_settings.py` | 전용 handoff client/DTO, RECEIVED 조정, v4와 신규 serializer 분리 |
| core 패키지 | 새 revision의 `scripts/run_release.py`, `source/scripts/run_canonical_batch.py`, MFA wrapper | 서버 부팅과 요청 실행 분리; H5/채점 규칙 불변 |
| Backend 접수 | `AnalysisRequestOutbox`, `RunPodAnalysisRequestOutboxDispatcher`, client/properties, timeout sweeper | busy/transport 구분, worker endpoint별 retry gate |
| Backend 결과 | canonical controller/service/verifier/committer/readers/eligibility | handoff service/repository, DB 원본 verifier, 신규 결과 adapter |
| Backend 보존 | evidence settings/reader | writer adapter, archive outbox worker, PUT 조정 |
| FE | `src/lib/analysis-polling.ts`, `src/lib/api/canonical.ts`, `src/lib/canonical-analysis.ts`, 결과/세션 화면 | 신규 profile/parser, deadline·조회 복구, B2와 독립한 완료 |
| 계약/문서 | `docs/contracts/`, 양쪽 API·DB·architecture 문서, rollout 계획 | handoff schema/HTTP 문서·신규 결과 schema, migration·운영 지표 |

신규 module/table/API는 구현 시에만 실제 구조 명세에 완료 상태로 추가한다. 이 계획 작성에서는 기존 dirty 문서나 사용자 파일을 덮어쓰지 않는다.

## 5. 개발자 수용과 성능 확인

저장소 규칙에 따라 자동 회귀 테스트·fixture·브라우저 자동화는 생성/실행하지 않는다. 구현 중 에이전트는 schema 대조·정적 분석·Python/Java/TypeScript 필요한 컴파일을 수행한다. 아래 실제 흐름은 개발자가 직접 검증하고, 요청된 운영 계측은 별도로 수행한다.

| 수동 확인 | 통과 조건 |
|---|---|
| 동일 testvideo1, cold 1회·warm 연속 3회 | cold 준비를 분리; warm 목표 확인; 점수 70.8과 rubric·판정·후보·MFA 의미 동등성 확인 |
| 긴 요청 중 두 번째 요청 | busy 때문에 FAILED가 되지 않고 공정 대기; 불필요 legacy boot 없음 |
| B2 지연/실패 | 결과는 검증·commit 후 공개, archive RETRY_WAIT/조정 상태와 원본 보존 |
| 인계 직후 Pod 중단 | Backend가 결과 검증/보존 계속 수행; 재추론 없음 |
| 인계 응답 유실·중복 seal·다른 digest | 같은 인계 조회/재전송은 멱등, 충돌은 409, 결과 한 번만 commit |
| 인계 전 취소·인계 후 취소·새 녹음 선택 | stale 결과 미노출, task/lease 회수, 이전 세대 공개 복구 없음 |
| Backend 재시작·writer PUT 응답 유실 | claim 복구, exact-version checkpoint 유지, 무한 재PUT 없음 |
| schema/rubric 불일치·GPT unscorable | 성공 점수로 대체하지 않음 |
| FE 대기 한도·502/503·재진입 | 같은 analysisId 조회 복구; 네트워크 문제를 새 분석 실패로 혼동하지 않음 |
| 구 v4/legacy history·TTS | 기존 조회/완료 의미와 독립 TTS 계약 유지 |

최종 성능 보고는 end-to-end, queue wait, ready/cold, core 내부, GPT, handoff/semantic, archive를 분리한다. 소수의 측정으로 p95를 주장하지 않는다. 목표 미달 구간은 span 근거와 함께 남기고 timeout 증가만으로 개선 완료라 하지 않는다.

## 6. PR·배포·롤백 순서

1. **계측 + P1**: AI/Backend 변경을 별도 review 가능한 단위로 구성. Backend 대상 `develop`, FE가 필요한 변경은 `dev`. 기존 schema로 먼저 배포할 수 있는 범위다.
2. **P2 상주 core**: 새 immutable core revision과 source manifest를 빌드. Backend verifier가 새 pin을 인식하도록 먼저 설치한 뒤, Pod의 새 worker를 준비한다. 기존 운영 revision은 보존한다.
3. **P3/P4 Backend 선배포**: additive migration, 신규 API/parser·검증기·archive worker 배치. 신규 profile admission은 OFF. DB 용량·백업, writer 키, nginx body/timeout, claim 복구, Readiness를 확인한다.
4. **RunPod 선연결**: 신규 handoff client와 readiness 협상 배치. Backend 지원 없이 새 schema로 보내지 않는다. v4 in-flight는 원래 계약으로 drain한다.
5. **FE 배포와 수용**: 신규 profile을 이해하는 FE가 실제 배포된 뒤 opt-in 요청을 활성화한다. Git merge만으로 Vercel 배포 성공을 단정하지 않는다. 접근권한 제약이 남으면 실제 배포 자산/개발자 수용으로 확인한다.
6. **제한 활성화**: 테스트 계정부터 신규 profile, 개발자 수용 후 운영 확대. 기능별 switch는 readiness 분리, resident 실행, 신규 handoff admission을 독립 제어하며 active execution에는 고정된 profile을 유지한다.

롤백: 신규 profile의 **새 접수만 OFF** → 처리 중 handoff/검증/보존 job은 신버전 Backend가 계속 처리 → 신규 worker drain 후 이전 런타임으로 복귀. 이미 RECEIVED인 실행을 v4로 재전송하지 않고, 기존 DB column/table이나 보존 원본을 삭제하지 않는다. 새 결과 조회는 이전 FE에서도 필요하면 동작 가능한 호환 배포를 유지하며, 불가능하면 해당 FE를 유지한 채 요청 profile만 v4로 되돌린다. archive 실패는 inference rollback과 독립적으로 복구한다.

## 7. 완료 정의

P1~P5의 코드·계약·문서가 일치하고, 개발자 수용 기록과 실제 배포 revision이 남아야 구현·운영 완료로 판정한다. health/컴파일만 통과한 상태는 성능·품질 검증 완료가 아니다. 계획 작성 당시에는 코드 수정·배포를 수행하지 않았다. 이후 실행 범위와 미완료 항목은 [구현 현황](latency-implementation-20261004.md)에서 관리한다.
