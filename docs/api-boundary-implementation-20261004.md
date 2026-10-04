# API 경계 수정 구현·인계 — 2026-10-04

상태: PR용 소스 구현. 서버 배포·실제 녹음·품질 QA 완료 기록이 아니다.

## 반영 범위

| 계획 항목 | 구현 |
|---|---|
| D1 완료 / D2 재녹음 | BE의 버전별 저장 증명 분리. v5 verified COMMITTED handoff를 사용하며 v4 receipt 규칙 유지. 읽기와 실제 명령에서 같은 정책 사용 |
| D3 결과 표시 | FE가 결과 표시·저장·학습 완료를 분리. 정상 교정문을 장애 원인으로 쓰지 않고 결과를 보존하며 완료 재확인 제공 |
| D4 접수 범위 | versioned capability에 scope/접수 상태 제공. 단독 음성만 지원, 코스/시험/영상은 녹음·업로드 전에 안내. 기존 FE에도 코스의 업로드 전 차단은 있었으며 이를 공통 계약으로 이동하고 시작 단계까지 적용 |
| D5 서버 재시작 | FE 공통 저장 관측기가 동일 실행의 일시 pending을 재확인. identity/로그아웃/취소 fencing 유지, 최대 120초 뒤 수동 재조회 |
| D6 재진입 학습 시간 | 새 분석·재진입·저장 복구·영상 공통 완료 서비스가 서버 선택 녹음의 duration 사용 |
| R1 실행 실패 | BE가 기존 RunPod 작업 status API로 확인한 FAILED를 반영. 외부 HTTP 중 DB 잠금 없음, 짧은 최종 transaction에서 현재 실행/worker/lease/세션을 재검사. spool 또는 handoff가 있으면 인계 책임 우선 |
| R1 인계 복구 | AI가 immutable handoff/artifact를 fsync 후 선공개. 재시작 시 원래 worker와 같은 body/digest로 상태 조회 후 아직 유효한 lease에서 전송만 재개. 모델/GPT 재실행 없음 |
| R2 영상 소비 경로 | 현재 지원 차단 유지. 향후 범위가 열리더라도 공통 결과 대기·저장 관측·서버 duration 완료·재조회 경로 사용 |

## 저장소별 주요 파일

- BE: CanonicalCompletionEligibility, CanonicalRerecordEligibility, CanonicalCallbackDocument, CanonicalCommittedResultReader; CanonicalCapabilitiesData/Service와 readiness controller 응답 매핑; RunPodAnalysisClient와 RunPodExecutionFailureReconciler; CanonicalAnalysisQueryService.
- FE: practice-session.tsx, canonical-persistence.ts, api/analysis-capabilities.ts, canonical-presentation.ts, practice-error.ts, title-exam.ts, routes/lip-practice.tsx.
- AI: canonical_recovery.py, canonical_executor.py, canonical_handoff.py, http_runtime.py.

## 운영/호환성

1. Backend develop, FE dev에 인계한다. AI 저장소는 사용자 요청에 따라 main PR로 인계한다. 병합과 배포는 별도다.
2. Backend capability 응답을 먼저 적용하고 FE를 적용한다. 신 FE가 과거 capability 응답을 만나면 분석 시작을 보류한다. FE 먼저 배포해서 기존 응답을 전체 지원으로 오해하게 하지 않는다.
3. RunPod 복구 코드는 별도 배포다. 기존 status API와 9개 공유 schema는 그대로여서 wire 동시 전환은 필요 없다. 모델·프롬프트·채점표·근거/권한 검증은 변경하지 않는다.
4. Backend의 analysis.runpod.failure-reconcile-interval은 기본 PT15S다. 단일 전용 lane에서 회차당 최대 8개를 순환 조회하므로 예약 worker scheduler를 외부 HTTP로 막지 않는다. 실제 실패 반영 지연은 대기 수·연결 상태에 따라 달라진다.
5. RunPod의 원래 workspace가 남아 있고 handoff.json과 필요한 원본이 완전하며 deadline이 남은 경우만 자동 전송 복구 대상이다. 신규 journal은 handoff-artifacts/의 0600 원본 파일과 최종 handoff.json으로 구성된다. 디렉터리는 0700이다.
6. 이전 작은 inline journal은 원본 digest를 검증해 재사용할 수 있다. 이전 큰 non-inline journal의 원본 부재, 미완성 journal, 손상/다른 요청, workspace 소실은 재구성하지 않는다. 이미 RECEIVED/VERIFYING/COMMITTED이면 서버의 동일 digest 응답을 우선하고, 새 전송은 원래 worker heartbeat가 승인한 유효 lease가 있어야 한다. Backend durable 수신 이후 책임은 Backend다.
7. 실행 실패의 상태 reconciliation은 정확한 request/execution/worker의 FAILED 중 INTERNAL_ERROR/DEPENDENCY_UNAVAILABLE만 사용한다. timeout, unknown, stale, 네트워크 장애를 실행 실패로 추정하지 않는다. 취소/다른 실행/영수증/commit/lease 만료가 있으면 확정하지 않는다.
8. 기존 성공 분석의 세션은 정책 수정 후 정상 조회/complete API로 복구한다. DB 강제 변경, 기존 결과 덮어쓰기, 자동 재채점은 수행하지 않는다.

## 확인 범위와 개발자 인수 항목

로컬 Java compileJava, TypeScript noEmit, 변경 Python의 syntax compile, 문서/차이 정적 확인만 수행한다. 자동 회귀 테스트·브라우저 자동화·실제 녹음/추론·장애 주입은 실행하지 않는다. PR에서 실행되는 기존 저장소 CI 결과도 실제 음성·품질 수용을 대신하지 않는다.

개발자가 확인할 항목:

- ACCEPT/허용 fallback/코칭 후보 없음의 완료, REJECT/INCONCLUSIVE 재녹음, FAILED의 정확한 복구 안내.
- 저장 전 결과 → SAVED → 학습 완료와 B2 지연 분리. 완료 응답 유실 뒤 중복 학습 기록 없이 재확인.
- 저장 중 Backend 재시작·새로고침, 원래 녹음 길이 유지, 로그인/선택/실행 변경 뒤 늦은 응답 차단.
- 코스·시험·영상 및 직접 URL의 사전 안내. 현재 미지원 기능을 이 수정으로 활성화하지 않음.
- core 이후 실패의 조기 분류, 동일 원본 전송 중 RunPod 재시작, ACK 유실 후 서버 receipt 우선, 만료 lease/손상 journal/원본 부재의 안전한 중단.
- 과거 v4 자료 보존, 미검증 결과 완료 차단, schema/verifier/overlay pin 대조와 독립 TTS 상태 확인.
