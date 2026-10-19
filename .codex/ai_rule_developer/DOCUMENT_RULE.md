# 문서 관리 규칙 — intelligentAI

## 위치

| 위치 | 책임 |
|---|---|
| `AGENTS.md` | 사용자 결정·핵심 경계·진입점 |
| `.codex/README.md`, 유형별 안내 4개 | 관련 소스·계약·관측으로 연결 |
| `.codex/ai_rule_developer/` | 실제 구조에 적용할 작업별 규칙 |
| `.codex/ref_docs/` | 사용자 외부 참고자료. 자동 수정·프로젝트 정본 생성 금지 |
| `.codex/local/` | 선택 로컬 관측·이력. 비밀값 제외, 현재 상태 보장 없음 |
| `docs/` | 프로젝트 명세·계약·설계·실행 관측·조사 |
| `deploy/*/README.md` | 환경별 기동·배포·복구 절차 |

동작은 해당 revision 소스/schema, 운영은 대상·시각·evidence가 있는 관측으로 판단한다. 사용자 결정·설계·구현·배포·실제 생성·품질 QA를 구분한다. 과거 ‘현재/미완료/최신’은 당시 시점에 한정하고 문서 편집 시각을 새 관측으로 쓰지 않는다.

API·enum/null/한도·상태·인증·외부 실패/retry/fallback·구조·저장 경계 변경 시 정본을 함께 갱신한다. 안내에 DTO·hash·상수를 복사하기보다 소스/manifest를 연결한다. 존재하지 않는 문서·API·table을 구현 사실처럼 쓰지 않는다.

`docs/architecture/*`에는 미완성 template가 있다. `app/features`·TBD를 구현으로 인용하지 않는다. 현재 구조 조사 진입점은 [프로젝트 조사](../../docs/project-survey-20261019.md)다. template 완성은 실제 경계·흐름·상태를 소스와 대조한다.

`docs/api/endpoints.md`는 index, `specification.md`는 상세 계약이며 변경 endpoint set을 맞춘다. `docs/database/schema.md`에는 소유권을 구분하고 Backend DB를 이 저장소 ORM처럼 쓰지 않는다. 과거 frozen schema·사고 이력은 보존한다.

## 확인·인계

- 상대 링크·필요한 heading anchor·충돌 표식·placeholder·diff를 정적으로 확인한다. 누락 정본을 추정하지 않는다.
- 전체 tracked Markdown은 `python scripts/check_markdown_links.py`로 검사할 수 있다. heading anchor·외부 URL·ignored/untracked 파일 전체는 검사하지 않으므로 새 규칙은 별도로 확인한다.
- 기존 결함과 이번 변경 결함을 나눈다. 검사를 통과시키려고 삭제된 workflow·없는 계획 파일을 가짜로 만들지 않는다.
- QA·자동 회귀·브라우저 자동화와 관련 산출물을 실행/생성하지 않는다. 문서 검사에 배포·GPU 시작·실제 생성 요청을 포함하지 않는다.
- 변경 파일·확인 근거·미확인/개발자 QA·commit/push/PR/배포 여부를 실제 범위로 인계한다.
- 새 공유 `.codex` 문서는 `.gitignore` 허용 목록으로 관리한다. ref_docs/local/키를 강제 추적하지 않는다. push는 CI·배포를 유발할 수 있으며 문서 정적 검증 범위가 아니다.
