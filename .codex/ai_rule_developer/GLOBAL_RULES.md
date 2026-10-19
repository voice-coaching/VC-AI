# 전역 규칙 - intelligentAI

너는 `intelligentAI` 프로젝트를 수행하는 시니어 개발자다.
파일을 수정하기 전에 `.codex` 문서를 먼저 읽고, 이 저장소의 로컬 규칙으로 적용한다.

## 절대 규칙
- 상위 지침과 사용자의 현재 요청 → 루트 AGENTS.md → 이 전역 규칙 → 작업별 규칙 순으로 적용한다. 동작은 대상 revision의 소스·schema, 운영은 대상·시각이 있는 관측으로 판단한다.
- 코딩 규칙은 `.codex/ai_rule_developer`, 프로젝트 명세는 루트 `docs/`, 사용자 추가 외부 참고자료는 `.codex/ref_docs`를 기준으로 삼는다.
- 코드 변경 범위는 현재 요청에 필요한 부분으로 제한하고 불필요한 리팩토링을 하지 않는다.
- 명시적 요청이 없는 한 기존 아키텍처, 네이밍 스타일, 공개 계약을 유지한다.
- 계층별 책임을 분리한다. 표현 계층이나 HTTP 계층에서 service/application 경계를 우회하지 않는다.
- Request/Response DTO, persistence entity, domain model, UI view model을 서로 혼용하지 않는다.
- 참고 문서가 정의한 동작이 있으면 임의 기능을 추가하지 않는다.
- 요구사항이 불명확하면 아키텍처 일관성을 해치지 않는 가장 작은 구현을 선택한다.

## 실제 프로젝트와 검증 범위
- 소스는 `src/voice_coach`와 `seungun/src`다. FastAPI는 HTTP 경계이며 `app/features` 템플릿으로 구조를 바꾸지 않는다. Python·선택 의존성은 `pyproject.toml`과 해당 격리 실행 환경을 확인한다.
- 연구 oracle, 내부 canonical 분석, 익명 direct, 영상 근거, 학습·평가 계약을 구분한다. Backend PostgreSQL과 FE는 별도 저장소 책임이다.
- 작업 전 Git 브랜치·HEAD·상태를 확인하고 기존 변경·삭제·untracked 파일을 보존한다. 평가·계획·구현·운영·문서화 범위를 먼저 정한다.
- Seungun 원본 관측·선택값·digest·음소 index·시간 출처를 보존한다. legacy 코칭과 Native/GOP 정책을 혼합하지 않는다. 과거 reader/schema 존재는 신규 실행 승인이 아니다.
- QA는 개발자가 직접 한다. Claude·브라우저 자동화·자동 회귀 테스트 및 관련 설정·산출물을 실행·생성하지 않는다. 기존 CI는 실행 허가가 아니다. 문서 정적 확인·필요한 컴파일·요청된 운영 조회만 범위를 명시해 수행한다.
- 문서·읽기 작업을 위해 GPU를 시작하지 않는다. env·인증 응답 전체나 키를 출력·기록하지 않고 필요한 프로세스에만 비밀값을 주입한다.
- 작업 목차는 `.codex/README.md`, 조사 근거는 `docs/project-survey-20261019.md`다. 변경 파일·근거·남은 개발자 QA·commit/push/PR/배포 여부를 실제 수행 범위로 보고한다.

## 금지 사항
- 편의를 위해 모든 로직을 한 파일에 몰아넣지 않는다.
- controller, route handler, UI component에 비즈니스 로직을 넣지 않는다.
- persistence entity를 public API 응답으로 직접 반환하지 않는다.
- 외부 연동은 확인된 계약을 기준으로 구현하고 실패 동작을 명시한다.
- 동작이 비명시적인 핵심 코드에 의도 설명 없이 코드를 추가하지 않는다.
