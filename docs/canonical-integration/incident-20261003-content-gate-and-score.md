# 2026-10-03 15:00–15:13 KST 분석 거절·점수 미제공 조사

작업 유형: 운영 장애 조사·외부 연동 계약 점검·문서화. `.codex` 전역 규칙, Backend HTTP·RunPod 운영 안내를 적용했다. 운영 로그·읽기 전용 DB·배포 소스를 대조했으며 새 분석, 자동 테스트, 설정 변경, 재배포는 수행하지 않았다.

## 결론

이 시간대의 신규 분석 60은 전송·실행·근거 보관·콜백 적용을 마쳤다. 최종 상태는 `COMPLETED / REJECT / INSUFFICIENT_CONTENT`이며 시스템 실행 실패가 아니다. 내용 검증이 거절하여 MFA 실행 전 중단되고 GPT는 호출하지 않았다. 실제로 원고를 덜 읽었는지는 이번 조사만으로 확정할 수 없다.

운영 내용 검증은 사람 평가로 보정되지 않은 개발용 자음 일치율 기준을 사용한다. 이번 결과는 기준에 근소하게 못 미치지만 원고 끝까지 일치한 근거도 있다. 따라서 이 기준이 발음 차이·검출 오차와 미완독을 충분히 구분하는지 재검토해야 한다.

점수 미제공은 별개다. 현재 canonical v4는 점수를 `null`로 제한하며, H5의 GPT 역할은 코칭 문구 생성이다. 성공 결과도 채점하지 않는다. 점수 계산 API가 간헐적으로 실패한 증거가 아니라, 활성 경로에 승인된 채점 계약·계산 구현이 없는 상태다.

## 대상과 식별자

- RunPod: `s8hb5b0l5k4qlx`, SSH `194.68.245.79:22132`.
- AWS: `13.209.184.226`, `alpha-backend.service`, PID `733937`, 배포 JAR `68ecde1b609ee8677c15b3f09ed8c14248797589.jar`.
- 분석/세션/녹음: `60 / 106 / 70`.
- requestId: `a1a792d3-f8e3-4e2d-802e-ceb8d15ee6d1`.
- executionId: `f8abee83-613d-477b-b9fe-f0a0bde346bf`.
- eventId: `98fd9297-5152-463a-9daf-06407fe9d9ce`.
- pipelineRevision: `canonical-R-421fda-H5-20261003-v1`.
- H5 prompt SHA-256: `6f9e8515ae083a62e0500a03e3c2cef073ae78eb696bed8b5857a14b551ff1fd`.
- H5 manifest SHA-256: `9f10296b6944249ded9f5ce2ccfdfa7c53b6e4af670999fe68e9e477e97eaf45`.

로그 원본 시각은 UTC이며 아래 표는 KST로 변환했다. 요청 범위를 오후 3시로 해석했다. 조회는 같은 날 15:15 KST 이후 수행했다.

## 요청 흐름

| KST | 확인 근거 | 결과 |
|---|---|---|
| 15:07:35.796 | `analysis_results.created_at` | 분석 60 생성 |
| 15:07:38 | AWS nginx `/api/training-sessions/106/analyze` | 200 |
| 15:07:41 | AWS nginx `/v1/analysis-jobs`, claim | 202, 200 |
| 15:09:51 | RunPod `canonical_exit.json`, `host_exit_code.json` | 두 프로세스 exit 0 |
| 15:11:26.791 | `analysis_evidence_receipts.verified_at` | VERIFIED |
| 15:11:37–15:12:08 | AWS nginx result callback | 503 총 9회 |
| 15:11:58.499 | callback inbox `verified_at` | 검증 완료 |
| 15:12:11.656 | `analysis_canonical_results.applied_at` | COMPLETED / REJECT 적용 |
| 15:12:14.635 | `analysis_results.analyzed_at` | COMPLETED, failure_code/failure_reason 없음 |
| 15:12:17 | AWS nginx result callback | 최종 200 |
| 15:12:20 | AWS nginx `GET /api/v2/analyses/60` | 200 |

RunPod `executor.log`는 RESERVE → MATERIALIZE → CORE → EXPRESSION → PREFLIGHT → RETAIN → RECEIPT → CALLBACK을 기록했다. `completed.json`도 COMPLETED다. EXPRESSION 단계 진입은 GPT 호출과 다르며 이번 `dispatchAttempts=0`, `generationStatus=NOT_DISPATCHED`, `adapterStatus=GLOBAL_REJECT`다.

콜백 503은 Backend `CanonicalCallbackService`의 PENDING/검증·반영 대기 시 재시도 응답과 일치한다. inbox는 최종 APPLIED이고 거절 reason_code가 없다. 각 503 응답 본문은 access log에 없으므로 개별 내부 코드까지 단정하지 않는다. 이번 최종 결과 손실이나 실행 timeout 근거는 없다.

AWS journald는 해당 구간의 앱 메시지가 0건이었다. 요청이 없었다는 뜻이 아니며 nginx access/error log와 DB 기록으로 보완했다. nginx에는 IPv6 upstream `Network is unreachable`가 있으나 작업 접수는 202, health는 200으로 끝났다. 이 경고를 이번 내용 거절의 원인으로 볼 수 없다.

## 내용 검증의 직접 원인과 한계

배포 core 위치는 `/workspace/intelligentai/releases/canonical_ai_pre_llm_20260928_100247Z_421fda`다. `source/scripts/run_canonical_batch.py:259`의 MFA 진입 경계에서 content decision이 REJECT이면 `invalid_audio`를 발생시킨다. 이 판정은 `source/src/korean_pronunciation_poc/demo_content_evidence.py`의 `measure/route`에서 나온다.

| 지표 | 이번 값 | 적용 기준 |
|---|---:|---:|
| 원고 자음 수 | 35 | — |
| 관측 자음 수 | 32 | — |
| 순서 일치 자음 수 | 24 | — |
| expected_coverage | 24/35 = 68.5714% | 69.6565% 미만이면 incomplete |
| observed_coverage | 24/32 = 75% | 64.0928% 이상이면 INSUFFICIENT_CONTENT 분기 |
| coarse_f1 | 71.6418% | mismatch 기준 61.0444% 미만에는 해당하지 않음 |
| last_matched_expected_progress | 1.0 | 거절 분기에서 사용하지 않음 |
| expected_tail_unmatched | 0 | 거절 분기에서 사용하지 않음 |

설정은 `calibration/demo_content_gate_config_v2.json`, SHA-256 `fd8543fe119128d321c90a1d4989de0d54626b4e545d6fa6443894c61f32ec62`다. 스스로 population을 `CONTROLLED_DEVELOPMENT_FIXTURES_NOT_HUMAN_GOLD`로 명시한다. 측정은 모음을 제외한 자음열의 `SequenceMatcher` 일치이며 전체 음소 오류율이나 사용자 발음 점수가 아니다.

관측값이 코드상 REJECT를 만드는 것은 확인했다. 다만 이 지표는 자음 인식·발음 차이로도 낮아질 수 있고, 이번 끝부분 일치 기록은 단순한 뒷부분 미완독 해석을 뒷받침하지 않는다. 녹음 청취·사람 정답 대조 없이 오탐이나 실제 미완독 어느 쪽도 확정하지 않는다. 임계값을 낮추거나 이번 결과를 ACCEPT로 바꾸는 수정은 하지 않았다.

## 실패 화면 연결

검토한 FE `dev` 기준 `95f9f32` 및 H5 작업본 `380b700`은 다음 흐름을 갖는다.

1. `practice-session.tsx`가 canonical `actions.canComplete=false`이면 `CanonicalResultUnavailable`을 던진다.
2. `practice-error.ts`가 이를 “AI 분석을 완료하지 못했어요”라는 공통 오류 제목으로 표시한다.
3. `canonical-presentation.ts`는 REJECT를 “입력이 분석 조건을 충족하지 못했습니다. 발음 오류라는 뜻은 아닙니다.”로 설명한다.

Backend `CanonicalCompletionEligibility`는 REJECT의 학습 완료를 허용하지 않는다. 이 코드 흐름은 사용자 실패 화면 보고와 일치한다. 운영 FE 배포 SHA와 실제 브라우저 화면은 이번에 확인하지 않았으므로 정확한 화면 렌더링은 소스 기준 판단이다. nginx에는 결과 조회 200이 있고 학습 완료 요청은 없다.

## 점수 API·GPT 하네스 점검

| 계층 | 현재 동작 |
|---|---|
| legacy RunPod HTTP 결과 | 배포 `http_models.py:252`가 `overallScore=None`, `scoringEvidence=None` 전달 |
| canonical H5 adapter | `bridge/adapter.py:150`에서 score validity만 정하고 overallScore는 None |
| canonical callback | `canonical_result.py:374`가 null과 `NO_APPROVED_SCORING_CONTRACT` 생성; 선택 근거가 있으면 validity만 변경 |
| v4 공유 schema | `$defs.score.overallScore`는 `const: null`; 숫자는 계약 위반 |
| GPT H5 system prompt | `TASK_MODE=COACHING`; “점수는 서버 책임이다. 점수·등급을 출력하지 않는다.” |
| GPT 출력 schema | candidateId/guidanceId/explanation/action/practice/selfCheck만 허용; score 필드 없음 |
| Backend `GET /api/v2/analyses/{id}` | `CanonicalPublicProjection`이 callback score를 그대로 투영; 별도 채점 호출 없음 |
| Backend `GET /api/analyses/{id}` | 저장된 점수와 과거 Clova score evidence를 조회; 조회 시 GPT로 채점하지 않음 |
| FE canonical adapter/schema | overallScore를 null로 제한하고 화면 모델의 점수 필드도 null로 설정 |

H5의 실제 요청 설정은 `gpt-6-astra`, low reasoning, `store=false`, `json_object`, 출력 1200 token, timeout 120초, retry 0이다. H5는 기존 S7에 짧은 action 표현 정책을 추가했으며 입력 거절 정책이나 채점 기능을 추가하지 않았다. 프롬프트 서두의 `scoring/active.md` 언급은 이번 호출이 채점 모드라는 뜻이 아니다. 실제 첨부 작업은 COACHING이며 점수 출력이 금지된다.

DB 비교:

- 분석 58·59: `ACCEPT / READY / SCHEMA_AND_STRUCTURAL_SEMANTICS_VALID`, GPT dispatch 1회, 점수 null / INSUFFICIENT_EVIDENCE / NO_APPROVED_SCORING_CONTRACT.
- 분석 60: REJECT, GPT dispatch 0회, 점수 null / NOT_AVAILABLE / NO_APPROVED_SCORING_CONTRACT.
- 현재 DB에서 숫자 점수가 있는 분석은 11(45.80), 15(62.50) 두 건. 생성일은 각각 9/17·9/18이며 generator는 `hyperclova`, rubric은 `clova-phone-rubric-v2/v3`다. 현재 GPT H5가 생성한 점수가 아니다.

따라서 점수가 있는 과거 이력과 없는 현재 결과가 함께 보일 수 있다. 현재 경로에서는 근거가 충분해져도 `NOT_CALIBRATED`와 null이며, 단지 더 오래 녹음하거나 GPT 호출에 성공하는 것으로 숫자 점수가 생기지 않는다.

## 후속 수정 범위 제안

1. 내용 검증: 개발용 자음 일치율의 운영 거절 권한을 재설계한다. 사람 판정 자료로 미완독·다른 원고·발음 차이·검출 오차를 구분하고, 근거가 모호한 경우의 INCONCLUSIVE/후속 분석 정책을 정한다. 동결 core와 이를 검증하는 H5 identity 계약을 함께 개정해야 하며 임계값만 현장에서 덮어쓰지 않는다.
2. 화면: 시스템 FAILED와 입력 REJECT, 판단 보류 INCONCLUSIVE를 서로 다른 제목·설명으로 표시한다. REJECT를 학습 완료로 처리하지 않는 조건은 유지한다.
3. 채점: 버전 있는 평가 항목·계산 방식·보정 근거·평가 불가 규칙을 먼저 정의한다. 이후 AI 계산/설명 역할, 공유 schema, Backend 저장·조회·시험 조건, FE validation/표시를 함께 구현한다. GPT에 임의 점수를 요구하는 prompt 변경만으로 해결되지 않는다.
4. 운영 관측: 검증 대기 503, 최종 callback 적용, 내용 거절을 구분해 기록하고 IPv6 upstream 경고를 별도로 정리한다. 이 항목들은 이번 REJECT의 직접 원인과 구분한다.

이번 변경은 조사 문서뿐이다. 기존 분석 결과·임계값·하네스·운영 프로세스는 변경하지 않았다.
