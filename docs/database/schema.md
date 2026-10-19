# 저장 경계와 DB 소유권

음성 제품의 PostgreSQL schema와 migration은 별도 Backend 저장소가 소유한다.
이 저장소에 Backend ORM/table을 새로 만들지 않는다. 등록 사용자·녹음 소유 관계,
claim/heartbeat와 canonical journal, 원본 handoff 검증·commit 및 독립 archive
outbox의 실제 table·컬럼·migration은 Backend의 해당 배포 revision으로 확인한다.

내부 신규 분석은 [v6 통합 계약](../canonical-integration/v6-unification.md)에 따라
request v4/result v6/handoff v2를 사용한다. nullable 영상 결속과 association v2의
저장·검증은 Backend 적용 대상이며 이번 소스 변경은 DB migration을 실행하지 않았다.
결과 공개·인계 검증 완료와 B2 archive 완료를 같은 상태로 취급하지 않는다.

AI의 익명 direct는 [JobStore](../../src/voice_coach/direct_analysis/store.py)의
POSIX spool·원자적 JSON 기록을 사용한다. UUID job/audio identity, 원본 WAV와
결과·archive/history 재시도 상태를 저장한다. Backend 정수 ID나 사용자 로그인
관계를 만들지 않으며 direct 이력의 계정 연결은 Backend가 별도 인증으로 처리한다.

연구 oracle API의 무상태 요청은 위 제품 저장 경계와 별개다. 이 저장소의 특허
검색 도구도 별도 SQLite 저장을 사용하며 음성 제품 PostgreSQL과 결합하지 않는다.
원본·미완료 journal·사용자 결과를 계약 통합 때문에 삭제하거나 재작성하지 않는다.
