# PostgreSQL 기반 canonical 복구 기록

2026-10-02 KST 업데이트: [Backend D1 운영 배포](rollout-execution-20261002.md)에서
아래 저장 구조를 적용했다. 기존 운영 V32 seed와 충돌한 미적용 번호를 변경하여
**V33 registry / V34 receipt / V35 callback / V36 journal**이다. 11:39~11:41 KST 후속
[활성화 관측](rollout-execution-20261002.md#제한-v4-접수-활성화)에서 reader·검증·journal/apply
worker와 양쪽 v4 접수 ON을 확인했다. staging 예산은 256 MiB, 보존은 INDEFINITE다.
실제 저장·복구 E2E는 미수행이다. 아래 V35는 최초 journal 초안 번호다.

2026-10-01 사용자가 **AWS Backend PostgreSQL로 업로드·callback·ACK 기록을 옮기는 방향을 승인**했다. 저장 경계 선택 대기는 해소됐다. 아래는 승인 후 로컬 구현이며 DB 적용·운영 배포 완료 기록이 아니다.

## 바뀐 책임

| 책임 | 현재 구현 방향 |
|---|---|
| 원본 근거 정본 | 기존 비공개 B2, exact version/SHA 및 Backend 독립 검증 유지 |
| 실행 예약·재실행 차단 | PostgreSQL 고정 request/execution/worker/revision/event + one-shot producer start |
| B2 업로드 복구 | PostgreSQL 전체 artifact metadata·인계 원문 spool·업로드 intent·version/readback checkpoint |
| callback 원문·후속 적용 | 기존 PostgreSQL inbox + 신규 apply queue/worker |
| ACK | 결과 적용 transaction의 APPLIED 원문, 별도 첫 DUPLICATE 응답 원문 |
| RunPod 파일 | private scratch. 파일 유실을 새 추론/PUT 허가로 취급하지 않음 |

이전 SQLite 구현은 삭제하지 않았지만 **운영 fallback으로 선택하지 않는다**. frozen producer는 이제 `BackendCanonicalJournal`을 필수로 받으며 원격 one-shot start가 성공해야 시작한다. 이 덕분에 실행 scratch에 영구 POSIX mount를 요구하지 않는다. private 소유/0700·기존 subprocess 회수 조건은 유지한다. 새 DB 관리자 비밀을 RunPod에 주입하지 않고 기존 내부 Bearer를 사용한다.

## Backend 변경

별도 Backend worktree `feat/canonical-callback-v4-20260930`, base `b48db988e7485943097acaa4c71308ef306175a8`에 작성했다. 기존 다른 변경을 보존했다.

- **V35**: journal, artifact upload journal, ACK journal, callback apply queue를 추가한다. V32–V34가 선행돼야 한다. 고정 identity/원문/완료 ACK에 변경·삭제 방지 trigger를 적용한다. 기존 업무 테이블 backfill/삭제는 없다.
- `CanonicalBackendJournal` / `CanonicalJournalController`: 요청 JCS와 기존 registry, 같은 worker의 현재 lease/deadline·사용자/녹음/선택 visibility를 대조한다. 내부 URL은 `/api/internal/ai/analyses/{analysisId}/journal/{executionId}` 아래로 한정한다. HTTP 동의/grant/서명이나 공개 DTO 필드는 추가하지 않는다.
- `start` 및 `begin`은 한 번만 상태 전이한다. 응답 유실 시 자동 재요청하여 추론/PUT을 다시 허가하지 않는다. 기존 UPLOADING을 PREPARED로 되돌리는 API는 없다.
- 네 개 필수 artifact와 선택 projection을 모두 staging한 뒤에만 PUT을 시작한다. 각 원문은 최대 16 MiB이며 metadata/association 집합은 최대 64 KiB다. B2에 쓰기 전에 전체 원문 인계가 PostgreSQL에 끝나야 한다. 원본 바이트와 SHA를 대조하며 schema/receipt의 독립 의미 검증은 별도다.
- 취소 이후 완료된 업로드의 version/readback은 기존 immutable worker와 사전 upload intent가 맞는 경우 **쓰기 전용 checkpoint**로만 보관한다. 새 업로드·조회·lease 갱신·재실행·공개 접근 권한이 생기지 않는다.
- evidence 등록은 journal의 동일 manifest 원문을 요구한다. callback은 예약 event/revisions 및 보관 manifest와 결속한다. prepare된 core를 없는 pre-core 실패로 숨길 수 없다.
- callback verification 성공과 apply queue 발행을 같은 transaction으로 처리한다. 별도 worker가 저장된 VERIFIED callback을 재사용해 적용하므로 Pod의 다음 POST가 없어도 진행할 수 있다. 현재 lease/visibility/deadline과 receipt는 그대로 검사한다. 30초 작업 claim·최대 16회 retry/backoff는 실행 lease를 연장하지 않는다.
- APPLIED ACK 원문은 결과 적용과 함께 commit한다. GET `.../callback/ack`는 현재 APPLIED inbox·결과·포인터를 재확인한 뒤 DUPLICATE만 반환하며 새 결과를 적용하지 않는다. 이는 **서버가 만들고 보관한 응답**이지 클라이언트 수신 확인 증명은 아니다.

기존 public API·v1~v3 callback·업로드·TTS 계약은 유지했다. v4는 아직 운영에 없던 로컬 구현의 내부 전환이며 접수 gate는 그대로 닫혀 있다. 새 journal이 없는 v4를 SQLite 경로로 낮춰 처리하지 않는다.

## RunPod 코드 연결

- [원격 journal](../../src/voice_coach/backend_analysis/canonical_backend_journal.py): reserve/start/prepare/stage/PUT intent/checkpoint/manifest 및 현재 receipt identity를 내부 HTTP로 전달한다. 프록시/redirect 비활성, TLS 유지, 자동 retry 없음, 호출 전체 10초·응답 64 KiB 한도다.
- [journal port](../../src/voice_coach/backend_analysis/canonical_journal_port.py): 기존 retention/receipt 코드의 SQLite 결합을 분리했다. receipt를 worker가 임의로 VERIFIED로 올리는 API는 없다. Backend receipt가 유일한 권한이다.
- [원격 callback 전달](../../src/voice_coach/backend_analysis/canonical_backend_delivery.py): 살아 있는 lease 내 동일 원문 POST만 재시도한다. 503 자체를 저장/성공 증거로 삼지 않는다. lease 상실 후에는 GET ACK 확인만 하며 claim/heartbeat/AI/PUT을 재실행하지 않는다.
- receipt/journal I/O를 별도 thread에서 수행하여 heartbeat event loop를 막지 않도록 호출을 수정했다. 기존 local outbox client를 새 경로에서 사용하지 않는다.

공유 [journal HTTP 계약](../contracts/runpod_canonical_journal_v1.md)과 [schema](../contracts/runpod_canonical_journal_v1.schema.json)는 AI/Backend에 같은 bytes로 배치했다. frozen R/S7와 기존 callback v4 원본 schema는 수정하지 않았다.

## 운영 전 필요한 일

1. V35 번호·최신 develop/운영 migration 충돌, 운영 JAR provenance, DB 백업/복원과 실제 migration 수용을 확인한다. 이번에는 DB 연결·변경이나 배포를 하지 않았다.
2. `JOURNAL_ENABLED`, `CALLBACK_APPLY_ENABLED`는 새 설정이며 예시에서는 false다. `JOURNAL_STAGING_BUDGET_BYTES`는 반드시 명시해야 하고 16 MiB~16 GiB 범위다. 선언된 artifact byte까지 transaction/advisory lock으로 예약하여 동시 초과를 막는다. **DB 전체/WAL/백업/기타 callback 크기의 상한은 아니므로** 실제 저장 용량을 따로 산정한다.
3. B2는 정본이고 DB raw는 인계 spool이지만, 이번 구현에는 자동 물리 삭제/TTL을 넣지 않았다. 미확정·취소 자료도 보존하며 용량이 차면 신규 artifact 준비를 거절한다. 정상 인계 후 복사본 정리도 검증된 exact B2 version/receipt에 결속된 별도 정책·구현 없이 수행하지 않는다. 이를 무한 용량 또는 장기 원본 중복 저장 설계 완료로 안내하지 않는다.
4. B2 업로드 응답 유실 reconciliation, Pod scratch 유실 전후의 단계별 원본 인계와 기존 worker recovery context, factory/boot·전체 stage orchestration, proxy의 16 MiB 원문 경로 body/timeout, 운영 키 공급·FE 소비 및 개발자 수용은 남아 있다.
5. 인계 spool에 쓰기 **전** 프로세스가 사라진 원문을 복구할 수 있다고 주장하지 않는다. 시작 marker가 남은 미완료 실행은 자동 재생성하지 않으며 현재 Backend 실패/종결 정책과 운영자 확인 대상으로 남긴다.

Python 구문 컴파일·Java 컴파일/resources·공유 schema 정적 대조 범위로 확인했다. SQL/trigger/HTTP/Spring 실제 기동, 프로세스 장애·B2/추론/GPT, 자동 회귀 테스트·fixture·브라우저 QA는 수행하지 않았다. FE 연결·키 주입·운영 배포·v4 활성화·commit/push/PR은 미완료다.
