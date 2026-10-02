# Canonical callback 내구성·재시작 복구

2026-10-01 로컬 구현. **운영 runtime/factory·scheduler에는 아직 연결하지 않았고 배포하지 않았다.** 이 문서는 전송 코드의 호출 순서와 남은 운영 경계를 설명하며, SQL/HTTP/재시작 수용 결과가 아니다.

## 구현과 호출 순서

**후속 승인으로 운영 방향 변경:** [PostgreSQL journal](backend-journal.md)이 현재 구현 정본이다. 아래 SQLite reserve/outbox/recovery는 앞선 로컬 구현 이력이며 운영 fallback이 아니다. 새 경로는 Backend event 예약·one-shot start·업로드 checkpoint, 기존 durable callback inbox와 server apply worker·같은 transaction의 ACK를 사용한다. 전체 runtime/boot·배포 연결은 아직 미완료다.

- [canonical_journal.py](../../src/voice_coach/backend_analysis/canonical_journal.py): 기존 private SQLite journal에 format v3 `result_deliveries`를 추가한다. Linux 소유 root 디렉터리 0700/파일 0600, DELETE rollback journal, synchronous FULL, 원자적 transaction을 유지한다. `/run`·`/tmp`는 거절한다. 실제 persistent mount·권한·디스크 한도·백업 경계는 배포 시 확인해야 하며 코드의 경로 검사만으로 NFS/볼륨 내구성을 보증하지 않는다.
- [canonical_delivery.py](../../src/voice_coach/backend_analysis/canonical_delivery.py)의 `CanonicalResultOutbox.reserve`: 인증·접수된 request v2 원문과 기존 worker/revision을 고정하고 result event ID를 한 번 예약한다. 동일 실행의 다른 request bytes/worker/revision은 거절한다. event를 새로 만들어 전송 재시도를 대신하지 않는다.
- canonical serializer는 예약한 `ResultIdentity`를 사용한다. 원본 보관·live Backend VERIFIED receipt를 갖춘 기존 serializer/preflight를 건너뛰지 않는다. outbox의 schema 검사는 독립 의미 검증이나 receipt 발급이 아니다.
- `enqueue`: 최대 1 MiB callback 원문, raw SHA, 별도 transport JCS SHA를 원자적으로 저장한다. core-bearing 결과는 journal의 같은 association/manifest/artifact refs/VERIFIED 응답 checkpoint와 대조하며 이미 보관 준비한 core를 pre-core 실패로 숨길 수 없다. 원본이 없는 경우의 pre-core 실패도 별도 고정 bytes로 저장한다.
- [canonical_callback_client.py](../../src/voice_coach/backend_analysis/canonical_callback_client.py)의 `deliver_once`: runtime이 제공하는 현재 worker·active predicate·남은 lease/전체 deadline 안에서 기존 `/result`로 저장 bytes를 그대로 보낸다. JCS로 재직렬화한 bytes를 보내지 않는다. token은 현재 `HttpSettings`에서 클라이언트에만 주입하고 journal·callback에 저장하지 않는다.

로컬 상태는 PREPARING → PENDING → SENDING → ACKNOWLEDGED 또는 STOPPED다. PREPARING은 callback이 아직 없는 상태이며 원본 재생성/AI/GPT 재실행 권한이 아니다. SENDING 전 checkpoint, 임의 UUID claim, 30초 claim 만료, 시도 횟수와 다음 전송 시각을 저장한다. 동일 DB를 사용하는 경쟁 전송은 claim token으로 완료/해제를 구분한다. SQLite I/O는 비동기 호출자에서 별도 thread로 수행하여 heartbeat event loop를 막지 않도록 했다. 디스크 장애를 전송 성공으로 바꾸지 않는다.

## 재시작과 ACK 유실

`candidates`는 재전송 시각이 된 PENDING/만료 SENDING의 execution ID만 제한된 개수로 반환한다. 이력 전체 원문을 출력하지 않는다. scheduler는 각 항목에 `recover_once`를 호출해야 한다.

1. 저장된 동일 worker/request/execution으로 기존 heartbeat를 호출한다. 새 claim, worker ID 교체, AI/GPT 실행은 없다.
2. Backend의 인증된 일치하는 live lease 응답만 새로운 monotonic 전송 budget으로 사용한다. 로컬에 저장한 lease를 신뢰하거나 만료 lease를 부활시키지 않는다. serverTime 대비 기간·최대 90초·원래 deadline을 대조하고 전송 시작 시각 기준 안전 여유를 뺀다.
3. lease가 유효하면 같은 event/bytes를 기존 result endpoint로 재전송한다. Backend 검증 중 503도 원본을 바꾸지 않고 재시도한다.
4. lease 상실/종결 또는 live ownership 없이 확인만 하는 경우는 새 **`POST /api/internal/ai/analyses/{analysisId}/result/ack`**로 전환한다. 이 경로는 같은 callback 원문을 받아 현재 visible한 검증/APPLIED inbox·immutable result·현재 포인터만 대조한다. 결과를 등록/검증/적용하지 않고 `200 DUPLICATE`만 반환한다.
5. 확인 전용으로 전환한 상태는 다시 live 전송으로 바꾸지 않는다. 첫 확인 claim부터 60초의 지속 보관된 확인 window를 사용하며, 확인 실패/만료는 STOPPED·미확인으로 남긴다. 각 HTTP 요청은 최대 10초이고 64 KiB 응답 한도를 갖는다. Retry-After는 최대 60초까지 수용하며 그 이상/비유한 값이면 조기 재시도하지 않고 중단한다.

정상 APPLIED/DUPLICATE ACK의 원문을 저장하기 전에 성공으로 보고하지 않는다. HTTP 중 취소가 와도 이미 받은 인증 ACK는 checkpoint하며, ACK 저장 thread와 취소 정리가 경합할 때 ACKNOWLEDGED를 다시 PENDING으로 바꾸지 않는다. 로컬 ACK는 현재 사용자 공개 권한을 대신하지 않는다. 취소·삭제·선택 변경·새 실행은 Backend의 현재 visibility에서 차단한다.

ACK endpoint는 새 결과 접수가 off인 원복 상황에도 reader 역할을 유지한다. 미등록 inbox는 404, 미적용/미검증은 503이고 성공으로 위장하지 않는다. 상세 wire 계약은 양쪽에 같은 bytes로 유지하는 [control v1.2 문서](../contracts/runpod_http_control_v1_2.md#canonical-callback-delivery-and-ack-only-recovery-local-not-deployed)를 따른다. 기존 v1.1·callback v1~v3와 public FE 계약은 변경하지 않았다. control v1.2에 추가한 `resultAck`/`leaseResponse`는 기존 응답 shape를 명시하며 root readiness는 계속 disabled-only다.

## 아직 필요한 연결·수용

생성자는 네트워크나 background task를 시작하지 않는다. `HttpRuntime.accept`의 canonical 503은 그대로이며, 실제 canonical executor/subreaper·lease/cancel 전체 수명·journal 공급·result reserve/serializer 연결·live 전송 loop·startup recovery scheduling·factory/boot는 후속 구현이다. Runtime은 정상 전송 budget을 현재 lease와 전체 deadline 중 작은 값으로 계산하고 취소를 즉시 반영해야 한다. readiness는 원본 보관·receipt·전송·자식 종료·공개 소비 경로가 모두 준비된 뒤 별도로 연결한다.

이 로컬 v3 journal은 실제 운영 DB를 업그레이드하지 않았다. 적용 시 기존 private spool을 보존하고 이전 코드가 v3 format을 읽을 수 없음을 rollback 절차에 반영한다. 파일/원본/오류 자료를 자동 삭제하거나 AI를 재실행하여 복구하지 않는다. 영구 원본 정본은 B2, 작업/적용 정본은 Backend DB이며 journal은 제한된 운영 spool이다.

개발자 직접 수용: send 전/후 crash, Backend commit 후 ACK 유실, heartbeat 만료/취소/재시도, 동시 claim/늦은 ACK, 변경된 bytes/event/worker 거절, receipt 없는 core 거절, 디스크 오류, 실제 SQLite fsync/마운트, SQL/HTTP wire, callback 대기 중 heartbeat 유지, 원본 비노출. 현재 확인은 Python 구문 컴파일·Java 컴파일/resources·schema 바이트/소스 대조뿐이다.
