# 상태 문서 - intelligentAI

> 보존 상태: `historical_snapshot`. 현재 시각 sidecar 아키텍처 정본이 아니다.

> **migration pending(2026-08-10):** G04 관련 state는 진단자료로 보존한다.
> 아래 `G04 passed -> D07/S108` 전이는 현재 구현 snapshot이며 새 정책 전이가
> 아니다. 정본 목표와 P0 이관 기준은
> [`PIPELINE_COMPLETION_PLAN_2026-08-10_KO.md`](../PIPELINE_COMPLETION_PLAN_2026-08-10_KO.md)를
> 따른다.

중요한 상태 모델과 상태 전이를 이 문서에 기록한다.

## 상태 종류
| State | Owner | Source of Truth | Persisted | Notes |
| --- | --- | --- | --- | --- |
| DiagnosticProfile | domain | API response/schema | no | schema version 1.0 |
| Coaching action | coaching | deterministic policy | no | top-1 evidence constrained |
| Dataset completion | data scripts | complete marker | yes | Git 제외 data 경로 |
| PracticePrompt selection | application | R00 sentence card | no | `prompt_version`에 card revision·텍스트 digest·음절 매핑 digest를 고정 |
| TargetLanguage | domain | prompt/spec 계약 | no | 허용값 `ko`/`en`; 현재 활성 catalog는 `ko`만 제공 |
| AttemptStatus | application | `run_attempt` 반환값 | no | terminal 상태만 반환. pipeline은 무상태 |
| FailureCode | application | `AttemptResult.failure_code` | no | 실패 사유를 점수로 대체하지 않음 |
| MetricState | domain | branch/aspect별 상태 | no | `missing`/`low_confidence`/`out_of_distribution`/`backend_unavailable` 구분 |
| GroundedPlanStatus | expression adapter | HyperCLOVA 출력 계약 | no | `rendered`만 결정론적 renderer로 진행, `abstained`는 catalog 폴백 |
| G04 T43 review | evaluation | R1 amended review state | yes | 43건 원인·조치 판정; accuracy 분모 금지 |
| G04 I30 review | evaluation | R1 amended review state | yes | 독립 30건 phone interval 판정 |
| G04 browser session | presentation | URL fragment → session storage | browser session only | token은 서버 로그·state에 저장하지 않음 |
| G04 review form | presentation | 선택 item의 API payload | no | 빈 판정에서 시작; profile 변경은 서버 재기동 |
| G04 amended progress | evaluation | A1 aggregate JSON | yes | D07/S108 공통 진행 계약; 현재 incomplete |

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
| AttemptStatus | coaching_ready | gate·allowlist 통과 | yes | LearnerFeedback 발행 |
| AttemptStatus | completed_no_issue | 근거 있는 오류 없음 | yes | 코호트 한계 notice 부착 |
| AttemptStatus | rerecord_required | audio QC 실패·내용 불일치·저신뢰 | no | 같은 prompt로 재호출 |
| AttemptStatus | uncertain | S1/S2 불일치, 필수 metric 결손, 비보정 profile | yes | 자동 코칭 금지 |
| AttemptStatus | failed_closed | backend·threshold·독립성·계약 불완전 | yes | 0점 대체 금지 |
| FailureCode | target_language_not_supported | `ko`/`en` 밖 타깃 또는 언어 계약 위반 | yes | audio/model 호출 전 차단 |
| GroundedPlanStatus | rendered | 승인 claim을 선택한 검증 가능 plan | yes | 최종 문구는 코드가 catalog 원문으로 렌더링 |
| GroundedPlanStatus | abstained | LLM이 표현 선택을 보류 | yes | 기존 catalog 메시지로 폴백 |

## 상태 전이 규칙
| From | To | Trigger | Validator | Side Effects |
| --- | --- | --- | --- | --- |
| not_run | approved | S2 감사 통과 | calibration threshold | 코칭 발행 가능 |
| not_run | disagreement | S1/S2 불일치 | auditor policy | 전문가 queue 후보 |
| not_run | uncertain | S2 confidence 부족 | auditor threshold | 자동 코칭 중단 |
| (호출) | coaching_ready | gate·allowlist·span 근거 통과 | evaluate_gate + span join | LearnerFeedback 구성 |
| (호출) | rerecord_required | audio QC 실패, content mismatch, 저신뢰 | QC receipt, frozen threshold | 발음 오류 확정 금지 |
| (호출) | uncertain | S1/S2 불일치, 필수 branch 결손, 비보정 profile | 독립성 receipt, threshold provenance | 자동 코칭 중단 |
| (호출) | failed_closed | 타깃 언어가 `ko`/`en` 밖이거나 prompt 언어 계약 불완전 | target-language gate | audio/model 미호출 |
| (호출) | failed_closed | prompt/threshold/backend/독립성 결손 | port 계약 | 점수·조언 미발행 |
| coaching_ready | coaching_ready | grounded plan 검증 성공 | strict JSON + issue/advice/claim identity | 결정론적 message override와 receipt 추가 |
| coaching_ready | coaching_ready | LLM timeout/invalid/abstain | catalog fallback | 기존 catalog 문구 유지, acoustic decision 불변 |
| G04 manifest 준비 | human_review_pending | T43 manifest 43건과 I30 입력 hash 검증 | amended contract | 제한 state는 판정 저장 시에만 생성 |
| human_review_pending | human_review_pending | GUI POST/PUT 판정 저장 | profile validator + atomic write | 완료 수 갱신; 미완료 item으로 이동 가능 |
| human_review_pending | G04 passed | T43 43/43 resolved, I30 30/30 및 phone accuracy≥0.95, attestation 완료 | completion validator | D07 평가 개방 가능 |
| human_review_pending | incomplete/failed | 판정 누락·uncertain·unresolved·낮은 phone accuracy | amended scorer | D07/S108 계속 차단 |

attempt 상태는 단일 `run_attempt` 호출의 terminal 결과이며 서버 세션 저장소는
이 프로젝트 범위 밖이다. 재녹음 이력은 호출자가 append-only로 보관한다.
전이 상세는 [`PRONUNCIATION_FEEDBACK_PIPELINE_KO.md`](PRONUNCIATION_FEEDBACK_PIPELINE_KO.md)
§5와 §13을 따른다.
