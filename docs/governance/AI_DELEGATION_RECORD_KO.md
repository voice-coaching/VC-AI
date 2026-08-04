# AI 위임 기록

최초 작성: 2026-08-04 KST
상태: 실행 중

## 목적

이 문서는 Claude 등 외부 AI에 위임한 준비·계약 감사와 실제 인간 평가를
분리해 기록한다. AI의 문서 검토·validator 제안·실행계획 초안은 인간의
청취, 음성학 전문가 판정, 동의, 참여자 평가 또는 연구 완료를 대체하지
않는다.

## 위임 상태값

- `delegation_requested`: 위임 범위가 요청되었으나 실행 결과가 없음
- `running`: 승인된 세션이 실행 중
- `blocked`: 승인·환경·선행 gate 부족으로 실행되지 않음
- `claude_preparation_completed`: Claude가 준비·계약 감사 결과를 반환함
- `human_completed`: 실제 사람의 원자료·자격·동의·평가·adjudication 근거가 확인된 상태

## DEL-CLAUDE-20260804-OPUS-001

| 항목 | 기록 |
|---|---|
| 위임 주체 | Claude Code 2.1.221 |
| 모델 | `opus` alias, `autocompact=1M` |
| 승인 | 사용자가 내부 프로젝트 문서 전송·검토를 명시적으로 승인 |
| 접근 범위 | `docs/`, `.codex/ref_docs/PROJECT_CONTEXT_AND_GOVERNANCE_KO.md`, 필요한 정적 규칙 |
| 금지 범위 | `data/restricted/`, raw audio, 제한 manifest/locator, 상세 annotation, checkpoint, token, `.env*`, 사람 평가 payload |
| 요청 범위 | S104, G04, D07, S105–S108, B05, S200–S203, C02/C03, D08/R01–R04, E01/E02/E00의 준비·계약·fail-closed 검토 |
| 상태 | `claude_preparation_completed` |
| 실제 사람 평가 | 0건. 인간 청취·전문가 판정·consent·participant evaluation 수행 없음 |
| 파일/실험 변경 | Claude 세션에서 0건 |
| 연구 점수 | tracker 48/100 유지 |

### Opus 검토 결과 요약

- S104 v2가 terminal registry·commit·독립 validator를 통과하기 전 S105 이후 re-pin과 실행을 금지한다.
- G04 30건 재감사는 진단 표본이며 기존 240건 95% gate를 대체하지 않는다.
- D07/D08/C03/R04/S2의 실제 평정·녹음·동의·adjudication은 인간이 수행해야 한다.
- `TBD_PREREGISTRATION`, one-shot authorization, locked/holdout 접근, gate 판정은 AI가 임의로 확정할 수 없다.

이 기록은 Claude의 준비·계약 감사 완료를 뜻할 뿐이며, 인간 평가 완료나 실제 연구 결과의 근거가 아니다.
