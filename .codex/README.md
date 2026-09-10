# intelligentAI 문서 목차

작업 진입점과 핵심 결정은 [루트 AGENTS.md](../AGENTS.md)다. 아래 문서는 요청에 필요한 것만 읽는다.

| 문서 | 책임 |
|---|---|
| [backend-http.md](backend-http.md) | 양쪽 계약·소스 대조와 변경·인계 절차 |
| [runpod.md](runpod.md) | 운영 대상 확인과 기존 배포 절차 연결 |
| [backblaze.md](backblaze.md) | B2 수집·저장·복원 판단과 수동 API 예제 |
| [env.md](env.md) | 역할별 설정과 프로세스 단위 주입 |

상세 설계·실행 이력은 루트 `docs/`, 기동·복원 절차는 [deploy/runpod](../deploy/runpod/README.md), 실제 동작은 해당 revision의 소스가 정본이다. 안내에 DTO·제안값·코드 상수를 중복 기재하지 않는다.

로컬 관측은 선택 자료인 `.codex/local/state.json`, 과거 이력은 `.codex/local/history.md`에 둔다. 다른 clone에는 없을 수 있다. 없으면 미확인으로 취급하고 현재 작업에 필요한 경우에만 조회한다. 기존 `session-state.md`는 위치 안내만 남긴다.

상태 기록은 자동 갱신되지 않는다. 각 관측의 대상, 원본 시각, source, lastObserved, lastCheck, 코드/배포 revision, evidence를 유지한다. 조회 실패를 이전 성공으로 덮어쓰지 않고 모르는 시각·revision은 null로 둔다. [상세 형식·개정 기록](../docs/codex_document_revision_plan_20260910.md)을 따른다.

문서 편집 시각은 새 원격 관측 시각이 아니다. 사용자 결정은 AGENTS, 설계 제안은 해당 계획, 과거 실행은 이력에 기록한다. 오래된 기록을 실행 예제의 대상 기본값으로 쓰지 않는다.

공유 대상은 루트 AGENTS와 위 안내 5개다. `.codex/local/`, 기존 상태 안내와 나머지 `.codex` 파일은 Git에서 제외한다. 추적 대상 지정은 commit·push·배포 완료를 뜻하지 않는다.

문서 수정 후 관련 링크·형식·공유 범위를 정적으로 확인한다. 로컬 전용 파일 경로는 선택 자료임을 명시하고 필수 링크로 만들지 않는다. 명령 예제는 해당 운영 요청에서만 실행하며 문서 정적 확인의 일부로 실행하지 않는다.
