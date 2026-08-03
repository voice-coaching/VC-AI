# 흐름 문서 - intelligentAI

주요 기능 흐름을 단계별로 기록한다.

## Oracle 계약 검증

- Actor: 연구자 또는 통합 테스트
- Entry point: `POST /v1/oracle-attempts`
- Preconditions: 정답 오류와 0–1 범위의 점수
- Steps:
  1. 입력 schema를 검증한다.
  2. `severity × confidence × impact × trainability`로 오류를 정렬한다.
  3. 내용 불일치와 저신뢰 결과를 먼저 차단한다.
  4. allowlist에 있는 top-1 오류만 코칭한다.
  5. `DiagnosticProfile` version 1.0을 반환한다.
- Validation: 시간 구간 순서, evidence 최소 1개, 허용 enum
- Empty state: 오류가 없으면 coaching action은 `none`
- Error state: schema 위반은 HTTP 422
- Permission behavior: 현재 인증 없음, 연구 전용
- Retry or recovery: 입력 수정 후 재요청
- Side effects: 없음
- Related API: `POST /v1/oracle-attempts`
- Related DB tables: 없음
