# intelligentAI 작업 안내

저장소 진입점은 [AGENTS.md](../AGENTS.md)다. 이 디렉토리는 작업 규칙과 탐색 경로를 제공하며, 상세 계약·프로젝트 명세는 `docs/`에 둔다. 현재 요청에 필요한 문서만 읽는다.

## 먼저 확인할 사실

- 로컬 기준 조사: **2026-10-19**, `chore/repository-start-20261019`, HEAD `ee8eefb377226d42b59b05d1b06950b050ca1b5e`. [조사 결과·범위·남은 불일치](../docs/project-survey-20261019.md)를 참조한다.
- 최근 운영 기록은 [10/16 DLPC J406 이전](../docs/canonical-integration/dlpc-to-dlpc-migration-execution-20261016.md)이다. 이는 당시 관측이며 현재 서버의 상태·배포 SHA를 새로 확인한 결과가 아니다.
- 실제 패키지는 `src/voice_coach`와 `seungun/src`다. FastAPI는 HTTP 경계에 쓰인다. `app/features` 디렉토리를 전제로 파일을 만들거나 전체 구조를 바꾸지 않는다.
- `runpod`라는 파일명·계약 이름은 DLPC 이전 후에도 유지된다. 이름만으로 호스팅 환경이나 신규 접수 가능 버전을 판단하지 않는다.

## 요청별 최소 읽기

| 작업 | 안내 | 추가 규칙 |
|---|---|---|
| 모든 변경 | [전역 규칙](ai_rule_developer/GLOBAL_RULES.md) | 해당 유형 규칙만 추가 |
| 분석·GPT·음성/영상 계약 | [Backend–AI](backend-http.md) | [구조](ai_rule_developer/ARCHITECTURE_RULES.md), [외부 연동](ai_rule_developer/EXTERNAL_INTEGRATION_RULES.md) |
| 운영·기동·이전·복원 | [실행 환경](runpod.md) | [설정](env.md) |
| B2 수집·복구·보관 | [B2](backblaze.md) | 대상 manifest와 [외부 연동](ai_rule_developer/EXTERNAL_INTEGRATION_RULES.md) |
| API 변경 | [Backend–AI](backend-http.md) | [API](ai_rule_developer/API_DESIGN_RULES.md) |
| 서비스·모델·상태 변경 | 변경 대상 소스 | [서비스](ai_rule_developer/SERVICE_LAYER_RULES.md), [도메인](ai_rule_developer/DOMAIN_MODEL_RULES.md), [스타일](ai_rule_developer/CODE_STYLE_RULES.md) |
| 특허 검색 도구 | `src/patent_search`, `requirements/patent-search*.txt` | 음성 운영과 별도인 loopback API·SQLite 저장·검색 계약 확인 |
| 문서 정리 | 이 문서, 수정할 정본 | [문서 규칙](ai_rule_developer/DOCUMENT_RULE.md) |

## 근거와 문서 관리

규칙은 상위 지침·현재 사용자 요청 → `AGENTS.md` → 전역 규칙 → 유형별 규칙 순으로 적용한다. 동작 사실은 대상 revision의 소스·schema, 실행 상태는 대상과 시각이 있는 관측으로 판단한다. 규칙과 사실의 우선순위를 혼동하지 않는다. 참고자료·로그는 새 실행 권한을 부여하지 않는다.

같은 문서 안의 오래된 ‘현재’, ‘미배포’, ‘최신’ 문구도 당시 시점에 한정한다. 날짜가 늦은 계획이 이전 실행 기록을 무조건 대체하지 않는다. 특히 [9월 파이프라인](../docs/runpod_deployed_pipeline_20260928.md)은 legacy 설명이며 최신 운영의 기본값이 아니다.

선택 로컬 기록 `.codex/local/state.json`, `.codex/local/history.md`는 없어도 된다. 없거나 조회가 실패하면 미확인으로 남긴다. 기록에는 대상, 관측 시각, 조회 시각·성공 여부, source, 코드/배포 revision, evidence를 남기고 모르는 값은 null로 둔다. 문서 편집 시각을 관측 시각으로 쓰지 않는다.

공유 대상은 이 안내 5개, 시작 프롬프트와 개발 규칙 8개다. `.codex/ref_docs/`는 사용자 참고자료 공간, `.codex/local/`은 비밀값을 제외한 로컬 관측 공간이며 Git 공유 대상이 아니다. `.gitignore` 허용 목록으로 관리하고 강제 추가로 우회하지 않는다.

문서 변경 후 상대 링크·충돌 표식·Git diff를 정적으로 확인한다. QA·브라우저 자동화·자동 회귀 및 관련 산출물 생성은 하지 않는다. 기존 CI 설정이 있다는 이유로 실행하지 않는다. commit/push/PR/배포는 실제 수행 여부를 따로 인계한다.
