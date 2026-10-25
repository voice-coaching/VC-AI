# 문서 안내

설명 생성기는 **외부 OpenAI API**를 사용한다. 모델은 실행 경로·관측 시각별로 구분한다. 2026-10-24 문장연습 direct의 Luna low 적용 관측과 후속 개선 범위는 [GPT API 지연 개선 계획](canonical-integration/gpt-api-latency-plan-20261024.md)을 참조한다. 이 문서 편집은 운영 상태 재조회나 내부 Backend 모델 전환을 뜻하지 않는다.

후속 direct 운영은 [GPT-6.1 Sol low/default 전환·실측](canonical-integration/sol61-light-timing-20261026.md)에 기록했다. 해당 관측에서 Sol은 Luna none보다 GPT HTTP 시간이 길었다.
사용자는 답변 품질을 우선해 Sol light(low)를 계속 사용하기로 결정했다.

| 목적 | 문서 |
|---|---|
| 최적화·v6 main 통합과 coordinated 배포 | [범위·정적 확인·전체 Git tree 검증과 기동 복구](canonical-integration/main-pipeline-release-20261026.md) |
| 신규 내부 음성·음성영상 v6 단일 계약 | [v6 통합·readiness·Backend 적용 조건](canonical-integration/v6-unification.md) |
| GPT-6.1 Sol light 전환·실제 API 시간 | [Luna 비교·큐 대기 분리·MFA cache hit와 운영 상태](canonical-integration/sol61-light-timing-20261026.md) |
| 전체 파이프라인 추가 단축 운영 반영 | [실행 worker·PCM·lexicon 재사용, compact Luna 적용과 확인 범위](canonical-integration/pipeline-latency-implementation-20261025.md) |
| 전체 파이프라인 추가 지연 단축 계획 | [사전·worker 재사용, PCM 준비, GPT 축소의 적용 순서와 목표](canonical-integration/pipeline-latency-next-plan-20261025.md) |
| Seungun 전체 코드·서버 최적화·실측 | [J406 자원·Core-row MFA·GOP 병렬 실행과 실제 API 시간](canonical-integration/seungun-server-optimization-20261025.md) |
| MFA disambiguation 중복 연산 제거 | [출력 의미 보존 계획·고정 함수 최적화·운영 및 실측 결과](canonical-integration/mfa-disambiguation-20261025.md) |
| MFA 준비 비용 단축 실행 | [기동 준비 재사용·관측 상한·MFA-only 병렬 후보와 반영 결과](canonical-integration/mfa-prepared-runtime-20261025.md) |
| MFA 시간 개선 계획 | [11.95초 병목·8초 목표·구현 순서와 완료 기준](canonical-integration/mfa-latency-plan-20261025.md) |
| MFA 상세 계측 운영 반영 | [M0 core/H5·direct 선택·복구·정적 확인과 관측 대기](canonical-integration/mfa-latency-implementation-20261025.md) |
| 문장연습 GPT API 시간 50% 단축 계획 | [Luna none·compact 출력·cache 적용 순서와 완료 기준](canonical-integration/gpt-api-latency-plan-20261024.md) |
| 문장연습 Luna none 운영 반영·관측 | [구현 범위·활성 설정·검증 결과와 남은 성능 관측](canonical-integration/gpt-api-latency-implementation-20261024.md) |
| canonical v4에 H5 짧은 총평 적용 | [파생 하네스·검증 계약·배포 인계](canonical-integration/h5-application-20261003.md) |
| 최근 음성 분석 실패와 FE–AWS–RunPod 연결 | [단일 실행 슬롯·429 실패·운영 API 조사](canonical-integration/incident-20261003-capacity-api-audit.md) |
| RunPod 사양에 맞춘 canonical 동시 실행 슬롯 | [Pod 자원 관측·3개 슬롯 선택·활성화 조건](canonical-integration/capacity-sizing-20261004.md) |
| GPT-6 날짜별 시스템 프롬프트·하네스 선택 | [H1–H6 버전 목록·현재 적용·원본 해시](gpt6-prompt-harness-catalog-20261003.md) |
| RunPod lease 갱신 경합 방지 구현·배포 | [lease 판독·오류 진단·운영 확인과 남은 QA](canonical-integration/runpod-lease-hardening-plan-20261003.md) |
| 10/3 분석 53–56 실패·AWS main 배포·JSON 응답 복구 | [journal JSON 결함 조사와 운영 수정 반영](canonical-integration/incident-20261003-journal-wire.md) |
| 현재 RunPod 구성·실측 상태·코드 근거 | [현재 배포 파이프라인 — 10/2 canonical 활성화 포함](runpod-current-pipeline.md) |
| 실행기·부팅·운영 키·제한 v4 접수 활성 | [10/2 운영 배포와 실제 관측·미수행 QA](canonical-integration/rollout-execution-20261002.md#제한-v4-접수-활성화) |
| 새 canonical core·frozen LLM 통합 진행 | [원본 위치·차이 분석·내부 연결 구현·남은 작업](canonical-integration/README.md) |
| 통합 8082 HTTPS 공개·재기동 복원 결과 | [통합 API 배포](runpod_api_gateway_20260930.md) |
| 기동·복원·안전한 변경 | [RunPod 배포 안내](../deploy/runpod/README.md) |
| 자동 배포와 관측 범위 | [GitHub Actions 안내](../deploy/runpod/GITHUB_ACTIONS.md) |
| 운영 HTTP endpoint | [API 명세](api/specification.md), [목록](api/endpoints.md) |
| 현재 분석 계약과 원본 검증 구조 | [계약 목록](contracts/README.md) |
| 입술 분석과 제약 | [영상·입술 분석](video-lip-analysis/README.md) |
| 입술–GPT P0/P1 배포 완료·P2 비활성 | [구현·배포 기록](video-lip-analysis/p0-p1-deployment.md), [목표 설계](video-lip-analysis/gpt6-integration-design.md), [구현 계획](video-lip-analysis/implementation-plan.md) |
| 전문가 참고 사례 | [사례 구성·복원](expert_feedback_rag_20260928.md) |
| 짧은 총평 | [피드백 정책](concise_coaching_feedback_20260929.md) |

## 과거 기록의 취급

[9월 28일 배포 관측](runpod_deployed_pipeline_20260928.md), [9월 30일 최초 조사·후속 변경 이력](runpod_deployed_pipeline_20260930.md), [설정 복원 기록](runpod_private_settings_recovery_20260929.md), 연구 문헌의 당시 코드 설명은 시점이 있는 이력이다. 최신 관측과 다르면 현재 상태를 덮어쓰지 않는다.

Clova의 과거 명령은 [폐기된 배포 안내](../deploy/runpod/README.legacy-clova.md)에 보존한다. 이를 GPT 운영에 적용하지 않는다. JSON schema의 legacy enum/const는 과거 결과를 읽는 계약이므로 문서 정리를 이유로 제거하거나 GPT 이름으로 바꾸지 않는다. 이 정의가 Clova 실행을 허용하는 것은 아니다.

관측·구현·설계 제안·품질 검증을 구분한다. ready 응답은 실제 녹음 처리·GPT 생성·품질 QA나 독립 TTS의 정상 동작을 보장하지 않는다.
