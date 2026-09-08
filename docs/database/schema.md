# DB Schema - intelligentAI

- 데이터베이스: 도입하지 않음

현재 oracle/API 골격은 무상태다. learner history, attempt 보존기간, 삭제·철회
요구가 확정되기 전에는 DB를 선택하거나 음성·화자 메타데이터를 저장하지
않는다. 영속화가 필요해지면 개인정보 영향평가와 보존 정책을 먼저 고정한 뒤
이 문서에 migration 가능한 schema를 추가한다.

## 테이블 템플릿
### table_name
- 목적:
- 담당 모듈:
- Soft delete:
- Migration:

| Column | Type | Required | Default | Index | Unique | Description |
| --- | --- | --- | --- | --- | --- | --- |

## 관계
| From | To | Cardinality | Delete Behavior | Notes |
| --- | --- | --- | --- | --- |

## Enum 값
| Table | Column | Value | Meaning |
| --- | --- | --- | --- |
