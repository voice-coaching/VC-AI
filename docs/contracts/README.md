# 현재 계약과 과거 호환성 정의

**2026-10-03:** `runpod_result_v4.schema.json`은 기존 S7/H5 null 점수와 새 scored-H5 숫자 점수를 identity별로 구분한다. [채점 구현·실패·배포 계약](../canonical-integration/scoring-implementation-20261003.md)을 참조한다. 기존 null-only 설명은 과거 하네스에 적용한다.

현재 writer는 [GPT-6 HTTP 배포](../runpod-current-pipeline.md)다. JSON 파일은 런타임 parser가 읽는 계약이므로 설명 문서처럼 enum/const를 일괄 치환하지 않는다.

후속 사용자 결정은 callback v3를 전면 개편하여 `voice-coaching.runpod-analysis-result.v4`로 승계하는 것이다. [v4 전환 계획·구현 현황](../canonical-integration/callback-v4-plan.md)은 canonical 상태·scoped ID·MFA와 원본 바이트 보존을 정의한다. **10/2 명시 opt-in v4를 활성화했으며 헤더 없는 기존 API는 legacy다.** 과거 Redis `voice-coaching.analysis-result.v4`와 혼동하지 않는다.

| 파일 | 현재 사용과 과거 정의 |
|---|---|
| [runpod_http_control_v1.schema.json](runpod_http_control_v1.schema.json) | 운영 제어 voice-coaching.runpod-http.v1.1의 접수·상태·취소·callback |
| [runpod_result_v1.schema.json](runpod_result_v1.schema.json) | 이름은 v1이나 과거 v1/v2와 coaching v3 정의가 공존. 현재 완료 코칭은 v3, 코칭 전 실패에는 legacy v1 경로가 남음 |
| [coaching_result_v1.schema.json](coaching_result_v1.schema.json) | 현재 coaching 구조. writer provider는 OPENAI; TEMPLATE는 실패 시 생성 출처이지 별도 운영 모델이 아님 |

## Callback v4 구현 자산 (제한 운영 활성)

10/2 후속: Backend reader·검증·journal/apply와 RunPod 실행기·부팅·writer를 연결하고
명시 opt-in 단독 음성 발음 학습의 접수를 활성화했다. control v1.2 readiness를
READY/NOT_READY 및 executor 진단 정의로 확장했다. 새 SHA는
`7acd63e1a0ff9aa68bd2d86c2c5ec37827df10b85b186efac08c1d338287e7a4`다.
양쪽 runtime의 legacy 포함 9개 계약 SHA를 대조한다. frozen R/S7와 result v4 의미는
불변이며 [실제 범위·미수행 QA](../canonical-integration/rollout-execution-20261002.md#제한-v4-접수-활성화)를 따른다.

| 파일 | 구현 경계 |
|---|---|
| [runpod_analysis_request_v2.schema.json](runpod_analysis_request_v2.schema.json) | 고정 canonical profile/result v4 요청. Python/Backend dual reader, readiness 기반 제한 접수 |
| [runpod_http_control_v1_2.schema.json](runpod_http_control_v1_2.schema.json) | 인증 readiness v2 READY/NOT_READY, canonicalExecutorReadiness 및 private evidence manifest. 기존 v1.1 메시지 변경 없음 |
| [runpod_result_v4.schema.json](runpod_result_v4.schema.json) | private callback preflight/serializer. 상태·scoped ID·G2P·MFA·retained-only 크기 초과 실패. Backend ingestion/apply 운영 활성 |
| [runpod_canonical_parent.schema.json](runpod_canonical_parent.schema.json) | S7 dependency lock의 canonical parent 원본 bytes |
| [runpod_canonical_input.schema.json](runpod_canonical_input.schema.json) | S7 frozen selection/projection 원본 bytes |
| [runpod_canonical_output.schema.json](runpod_canonical_output.schema.json) | S7 frozen GPT expression output 원본 bytes; 전체 dispatch 결과와 구별 |

서비스 v4 schema는 frozen substructure를 참조 범위를 보존하여 포함한다. 파일 존재나 JSON Schema 검증만으로 원본 association·prepared 음성 시간축·Backend VERIFIED receipt가 검증되지는 않는다. [canonical_result.py](../../src/voice_coach/backend_analysis/canonical_result.py)의 의미 대조와 저장소 경계가 별도로 필요하다. private 원문·object key·original_error는 공개 조회용 DTO가 아니다. callback은 1 MiB, manifest는 64 KiB, 원본 객체는 각각 16 MiB 상한이다. 저장 정책은 [RunPod 문서](../../deploy/runpod/README.md#canonical-callback-v4-근거-보관-설정-미활성)의 명시적 무기한 보존을 따른다.

과거 Clova rubric ID, hyperclova const, CLOVA provider enum은 과거 결과 정의와 reader 호환성이다. 존재한다고 provider가 실행 중인 것은 아니다. 현재 overallScore/scoringEvidence는 null이다.

이전 rubric을 GPT 이름으로 바꾸면 같은 schema ID의 의미가 달라지고 과거 결과를 오해한다. 정의 제거는 별도 계약 개정·과거 데이터 reader 검토·Backend 합의 후 진행한다. 신규 schema 추가와 별개로 기존 v1/v2/v3 schema는 변경하지 않았다.

공개 영상 sampleCount=null, referenceStatus=NOT_VALIDATED, correctiveClaimsAllowed=false는 현재 v1 계약이다. P0/P1의 내부 v2에는 유효 표본 수·사용 시각·후보 결속·관찰 정책·statement 참조가 구현·배포됐다. 공개 응답은 기존 allowlist로 투영하므로 새 내부 필드를 추가하지 않는다. [확정 구현](../video-lip-analysis/p0-p1-deployment.md)을 따르며, [개선 설계](../video-lip-analysis/gpt6-integration-design.md)의 가상 예시 전체를 공개 계약으로 취급하지 않는다. P2 개인 교정은 비활성이다.


Opt-in v5: `runpod_analysis_request_v3.schema.json`, `runpod_result_v5.schema.json`, `runpod_canonical_handoff_v1.schema.json`. See [durable handoff](../canonical-integration/handoff-v5-20261004.md). Existing v4 schemas are unchanged.
