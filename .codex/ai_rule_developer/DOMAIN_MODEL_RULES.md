# 도메인·상태·저장 규칙 — intelligentAI

이 저장소는 일반 ORM CRUD 앱이 아니다. `domain`, 기능별 domain/models/ports, canonical 원본·journal, direct filesystem store가 공존한다. 음성 제품의 Backend PostgreSQL table/migration은 별도 Backend 저장소가 소유한다. 별도 `src/patent_search/service.py`의 SQLite 저장은 해당 연구 도구의 책임이다.

- Request/Response DTO, domain result, private evidence, journal 문서, persistence entity를 구분한다. Entity/Repository 이름을 맞추기 위한 계층을 만들지 않는다.
- Seungun 원본 관측·legacy 선택값, file digest·음소 index·시간 출처, prepared media·association을 보존한다. 코칭 선택/projection으로 원본 검출을 덮어쓰지 않는다.
- 상태값·전이는 실제 domain/service/schema에서 확인한다. 범용 DRAFT→READY→QUEUED 템플릿을 도입하지 않는다.
- 실행, 결과 공개, Backend 인계·검증, B2 archive, direct 이력 동기화는 다른 상태다. 하나의 COMPLETED로 합치지 않는다.
- null·미측정·UNAVAILABLE·REJECT·FAILED 등은 해당 계약대로 유지한다. 빠진 점수·시간·STT·근거를 채우지 않는다.
- 멱등 키·execution 세대·digest·소유권·취소/삭제 fence를 검증한다. 과거 결과·identity를 신규 실행 승인에 재사용하지 않는다.
- 저장 변경은 retention·spool 예산·atomic write/fsync·단일 writer·재시작/응답 유실 복구 영향을 검토한다. 미완성 기록·영수증을 임의 삭제하지 않는다.

DB 변경은 소유 저장소와 계약을 확인하고 [DB 문서](../../docs/database/schema.md)에 경계를 기록한다. 무상태 oracle 설명을 시스템 전체에 일반화하지 않는다. 사람 연구·locked 데이터 승인 계약은 내부 HTTP grant 제외 결정과 별개다.
