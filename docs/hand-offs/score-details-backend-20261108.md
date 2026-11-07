# Backend 인계: 공개 세부점수 전달

대상은 `VC-BE` develop 기준 `feat/score-details-forwarding-20261108`이다.
Backend 운영 배포 완료를 뜻하지 않는다. 필드·예시·Frontend 경로의 정본은
[Frontend API 명세](../api/frontend-score-details-response.md)다.

## 변경

- `CanonicalAnalysisView.Score`에 optional `ScoreDetails details`를 추가해 공개 projection에서 보존한다.
- ScoreDetails와 각 criterion은 null 점수를 그대로 전달한다. 숫자 0과 null을 구분한다.
- result v4/v6 구조 schema가 details와 부분 평가를 수용한다. 기존 CORE/H5 allowlist,
  공식 검산표·해시·릴리스 검사 게이트를 추가하거나 되살리지 않는다.
- 저장 원문·Direct 이력은 재채점하지 않는다. DB migration은 필요하지 않다.
- 기존 overallScore·validity·reason과 과거 rubricRevision·criteria는 유지한다.

## 검증·반영

정상 숫자·0점·일부 항목 null·details 없는 과거 결과를 확인한다.
실제 내부 request v4 → result v6 → handoff를 Java parser로 읽고 공개 projection에서
details 값이 보존되는지 검사한다. 이는 운영 DB 저장·화면 표시 검증을 대신하지 않는다.

Backend reader 반영 후 AI writer를 반영한다. 서로의 readiness를 기다리는 새 게이트는 만들지 않는다.
Frontend parser·화면 배포는 담당 개발자가 별도로 수행한다.
