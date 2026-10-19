# API 계약 규칙 — intelligentAI

기존 path·version·auth·응답 의미를 유지한다. prefix를 일괄 `/api/v1`로 바꾸지 않는다. 연구 oracle, 내부 분석, `/direct`에 mount되는 익명 접수, 별도 TTS를 구분한다.

- [endpoint 목록](../../docs/api/endpoints.md)은 method·path·auth·한 줄 목적, [명세](../../docs/api/specification.md)는 요청·응답·status·실패를 기록한다. 시점별 설명은 실제 route/parser/schema와 대조한다.
- 내부 계약은 [docs/contracts](../../docs/contracts)의 schema와 의미 검증 소스가 정본이다. 파일명 버전과 wire schemaVersion, result/public view 버전을 혼동하지 않는다.
- 필드의 type·필수/선택·null 의미·enum·길이/바이트 한도·추가 필드 허용을 명시한다. schema 통과만으로 provenance 검증 완료를 주장하지 않는다.
- 상태·에러·auth·멱등성·충돌·deadline·취소·재시도·공개 시점은 해당 계약을 따른다. 결과 공개와 archive 완료·품질 QA를 구분한다.
- public DTO와 private 원본/journal/저장소 key/내부 exception을 분리한다. persistence model을 그대로 반환하지 않는다.
- 버전 추가 시 writer·reader·검증기·실행 identity·readiness·Backend/FE 소비를 함께 검토한다. 과거 읽기 허용은 신규 실행 승인이 아니다.
- 변경 endpoint의 목록과 상세 명세를 일치시킨다. 별도 Backend endpoint의 소유 저장소를 명시한다. 과거 frozen schema bytes·enum은 문서 정리 이유로 바꾸지 않는다.

운영 배포·실제 요청·개발자 QA는 코드/문서 변경과 별도로 인계한다. 계약에 없는 기능을 임의 추가하지 않는다.
