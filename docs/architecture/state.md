# 상태 문서 - intelligentAI

중요한 상태 모델과 상태 전이를 이 문서에 기록한다.

## 상태 종류
| State | Owner | Source of Truth | Persisted | Notes |
| --- | --- | --- | --- | --- |
| DiagnosticProfile | domain | API response/schema | no | schema version 1.0 |
| Coaching action | coaching | deterministic policy | no | top-1 evidence constrained |
| Dataset completion | data scripts | complete marker | yes | Git 제외 data 경로 |

## Enum 값
| Enum | Value | Meaning | Terminal | Notes |
| --- | --- | --- | --- | --- |
| AuditStatus | approved | S2 승인 | yes | S2 구현 후 사용 |
| AuditStatus | disagreement | S1/S2 불일치 | no | 전문가 검토 대상 |
| AuditStatus | uncertain | 감사 불확실 | no | 자동 코칭 금지 |
| AuditStatus | not_run | 감사 미실행 | no | oracle 기본값 |
| CoachingAction | coach | 검수된 조언 발행 | yes | confidence gate 통과 |
| CoachingAction | rerecord | 재녹음 요청 | no | 내용 불일치 또는 저신뢰 |
| CoachingAction | expert_review | 전문가 확인 | no | allowlist 밖 오류 |
| CoachingAction | none | 교정 없음 | yes | 근거 오류 없음 |

## 상태 전이 규칙
| From | To | Trigger | Validator | Side Effects |
| --- | --- | --- | --- | --- |
| not_run | approved | S2 감사 통과 | calibration threshold | 코칭 발행 가능 |
| not_run | disagreement | S1/S2 불일치 | auditor policy | 전문가 queue 후보 |
| not_run | uncertain | S2 confidence 부족 | auditor threshold | 자동 코칭 중단 |
