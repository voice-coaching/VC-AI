# 문서 안내

현재 운영 설명 생성기는 **외부 GPT-6 API(`OPENAI / gpt-6-astra`)**다. 로컬 Clova 추론·채점과 Redis worker는 현재 파이프라인의 필수 구성이 아니다.

| 목적 | 문서 |
|---|---|
| 현재 RunPod 구성·실측 상태·코드 근거 | [현재 배포 파이프라인 — 10/2 canonical 활성화 포함](runpod-current-pipeline.md) |
| 실행기·부팅·운영 키·제한 v4 접수 활성 | [10/2 운영 배포와 실제 관측·미수행 QA](canonical-integration/rollout-execution-20261002.md#제한-v4-접수-활성화) |
| 새 canonical core·frozen LLM 통합 진행 | [원본 위치·차이 분석·내부 연결 구현·남은 작업](canonical-integration/README.md) |
| callback v3 → v4 전면 개편 계획 | [canonical 상태·scoped ID·MFA 무손실 보존과 서비스 전환](canonical-integration/callback-v4-plan.md) |
| v4 운영 제한 키·전체 통합·배포 순서 | [역할별 비밀 주입·남은 구현·활성화 gate·원복 계획](canonical-integration/production-rollout-plan.md) |
| 통합 8082 HTTPS 공개·재기동 복원 결과 | [통합 API 배포](runpod_api_gateway_20260930.md) |
| 기동·복원·안전한 변경 | [RunPod 배포 안내](../deploy/runpod/README.md) |
| 자동 배포와 관측 범위 | [GitHub Actions 안내](../deploy/runpod/GITHUB_ACTIONS.md) |
| 운영 HTTP endpoint | [API 명세](api/specification.md), [목록](api/endpoints.md) |
| 과거 provider/점수 정의가 남은 이유 | [계약 버전 안내](contracts/README.md) |
| 입술 분석과 제약 | [영상·입술 분석](video-lip-analysis/README.md) |
| 입술–GPT P0/P1 배포 완료·P2 비활성 | [구현·배포 기록](video-lip-analysis/p0-p1-deployment.md), [목표 설계](video-lip-analysis/gpt6-integration-design.md), [구현 계획](video-lip-analysis/implementation-plan.md) |
| 전문가 참고 사례 | [사례 구성·복원](expert_feedback_rag_20260928.md) |
| 짧은 총평 | [피드백 정책](concise_coaching_feedback_20260929.md) |

## 과거 기록의 취급

[9월 28일 배포 관측](runpod_deployed_pipeline_20260928.md), [9월 30일 최초 조사·후속 변경 이력](runpod_deployed_pipeline_20260930.md), [설정 복원 기록](runpod_private_settings_recovery_20260929.md), 연구 문헌의 당시 코드 설명은 시점이 있는 이력이다. 최신 관측과 다르면 현재 상태를 덮어쓰지 않는다.

Clova의 과거 명령은 [폐기된 배포 안내](../deploy/runpod/README.legacy-clova.md)에 보존한다. 이를 GPT 운영에 적용하지 않는다. JSON schema의 legacy enum/const는 과거 결과를 읽는 계약이므로 문서 정리를 이유로 제거하거나 GPT 이름으로 바꾸지 않는다. 이 정의가 Clova 실행을 허용하는 것은 아니다.

관측·구현·설계 제안·품질 검증을 구분한다. ready 응답은 실제 녹음 처리·GPT 생성·품질 QA나 독립 TTS의 정상 동작을 보장하지 않는다.
