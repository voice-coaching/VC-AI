# 전역 규칙 - intelligentAI

너는 `intelligentAI` 프로젝트를 수행하는 시니어 개발자다.
파일을 수정하기 전에 `.codex` 문서를 먼저 읽고, 이 저장소의 로컬 규칙으로 적용한다.

## 절대 규칙
- 사용자의 현재 요청을 최우선으로 둔다.
- 코딩 규칙은 `.codex/ai_rule_developer`, 프로젝트 명세는 루트 `docs/`, 사용자 추가 외부 참고자료는 `.codex/ref_docs`를 기준으로 삼는다.
- 코드 변경 범위는 현재 요청에 필요한 부분으로 제한하고 불필요한 리팩토링을 하지 않는다.
- 명시적 요청이 없는 한 기존 아키텍처, 네이밍 스타일, 공개 계약을 유지한다.
- 계층별 책임을 분리한다. 표현 계층이나 HTTP 계층에서 service/application 경계를 우회하지 않는다.
- Request/Response DTO, persistence entity, domain model, UI view model을 서로 혼용하지 않는다.
- 참고 문서가 정의한 동작이 있으면 임의 기능을 추가하지 않는다.
- 요구사항이 불명확하면 아키텍처 일관성을 해치지 않는 가장 작은 구현을 선택한다.
- 외부 API 호출은 반드시 전용 client/adapter 계층을 통해 수행한다.
- 외부 연동은 인증 정보, 계약, schema, 실패 처리를 실제 구현과 함께 관리한다.

## 연구·데이터 거버넌스
- 이 저장소는 한국어 L2 발음 진단과 폐루프 보이스 코칭을 검증하는 연구 프로젝트다. 계획, static readiness, dry-run, 개발 결과, 실제 실행, 독립 검증, 사람 평가 완료를 절대 혼용하지 않는다.
- 실험 또는 모델 작업은 해당 단계의 data audit, split/label/QC, smoke, 평가, 사람 검증 gate를 확인한 뒤에만 진행한다. 미해결 gate, `TBD`, 실패 상태, stale registry, partial output을 성공 또는 승인으로 해석하지 않는다.
- 제한 데이터, raw identifier, 원천 경로, 음성·전사·상세 annotation, checkpoint와 비밀정보를 Git·공개 문서·공개 API 응답에 포함하지 않는다. 공개 가능한 집계 결과도 라이선스와 개인정보 검토를 통과해야 한다.
- 데이터 역할과 label 의미를 보존한다. 원어민 자료를 L2 오류 감독 label로 대체하거나 expected/realized phone, train/dev/calibration/locked-test 역할을 임의로 섞지 않는다.
- 실제 실행·실패·편차가 생기면 증거를 덮어쓰거나 수동으로 registry를 고치지 않는다. 정본 실행 계약과 deviation ledger가 정한 보존·rollover·재실행 절차를 따른다.
- 프로젝트 맥락의 빠른 참고는 `.codex/ref_docs/PROJECT_CONTEXT_AND_GOVERNANCE_KO.md`에서 제공한다. 상태·계약의 정본은 루트 `docs/`이며 충돌 시 루트 `docs/`의 해당 정본 문서를 다시 확인한다.

## 적용 프로필
- Python: 일반 Python 프로젝트는 프레임워크 전제 없이 기능/도메인 폴더를 우선하고, 각 기능 내부에서 service, adapter, CLI 경계를 명확히 분리한다.
  - import 시점에 파일 생성, 네트워크 호출, 프로세스 실행 같은 부수효과가 발생하지 않게 한다.
  - 실행 가능한 모듈은 `main()` 진입점을 제공하고 직접 실행 코드는 `if __name__ == "__main__"` 아래에 둔다.
  - 표준 라이브러리로 충분한 문제에 불필요한 dependency를 추가하지 않는다.
  - 공개 함수와 클래스에는 의미 있는 타입 힌트를 유지하고, 입력/출력 모델은 명확한 자료 구조로 표현한다.
  - 순수 계산 로직과 I/O 로직을 분리해 테스트와 재사용이 가능하게 한다.

## 금지 사항
- 편의를 위해 모든 로직을 한 파일에 몰아넣지 않는다.
- controller, route handler, UI component에 비즈니스 로직을 넣지 않는다.
- persistence entity를 public API 응답으로 직접 반환하지 않는다.
- 외부 연동은 확인된 계약을 기준으로 구현하고 실패 동작을 명시한다.
- 동작이 비명시적인 핵심 코드에 의도 설명 없이 코드를 추가하지 않는다.
