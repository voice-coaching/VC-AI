# 전역 작업 규칙 - intelligentAI

## 역할과 목표

이 저장소는 Python 3.12 기반의 **근거 제한형 한국어 L2 음성 코칭 연구
시스템**이다. 저장소 이름은 `intelligentAI`, 배포 패키지 이름은
`voice-coach`, import package는 `voice_coach`다. FastAPI는 여러 실행 경계 중
하나이며, 저장소 전체를 CRUD 웹 서비스나 MySQL 애플리케이션으로 가정하지
않는다.

목표는 요청한 결과를 가장 작은 안전한 변경으로 완성하고, 실제 실행 근거와
계약 상태를 구분해 보고하는 것이다.

## 지침 우선순위

충돌 시 다음 순서를 따른다.

1. 사용자의 현재 요청
2. 이 문서
3. `.codex/ai_rule_developer`의 작업 유형별 규칙
4. 프로젝트 루트 `docs/`의 현재 명세
5. `.codex/ref_docs`의 사용자 관리 참고자료
6. 기타 보조 문서와 과거 기록

현재 동작을 판단할 때는 관련 코드, 테스트, config/schema와 실행 결과를 함께
확인한다. 문서와 구현이 충돌하면 한쪽을 추측으로 정본 처리하지 말고 충돌과
영향을 먼저 밝힌다. 삭제된 과거 문서나 오래된 상태 기록을 복원해 현재 근거로
사용하지 않는다.

## 작업 시작 규약

- 변경 전에 `git status --short --branch`로 기존 수정과 미추적 파일을 확인한다.
- 요청을 조사/설계/구현/실행/검증/문서화 중 어떤 작업인지 분류한다.
- 관련 코드, 테스트, config, 현재 문서를 먼저 읽고 공개 계약과 보호 경계를
  식별한다.
- 기존 사용자 변경은 소유권이 확인되지 않는 한 수정, 삭제, 되돌리기, stage
  또는 commit하지 않는다.
- 요구가 불명확해도 결과를 바꾸지 않는 작은 가정은 명시하고 진행할 수 있다.
  데이터 접근, 외부 실행, 비용, 계약 또는 제품 claim을 바꾸는 가정은 하지
  않는다.

## 요청이 허용하는 행동

- 설명, 조사, 진단, 리뷰 요청: 읽기와 비파괴 검증만 수행하고 구현을 바꾸지
  않는다.
- 수정, 구현, 고침 요청: 요청 범위의 로컬 파일을 수정하고 관련 비파괴 검증을
  실행한다.
- 별도 확인이 필요한 행동: 외부 시스템 쓰기, 대규모 다운로드, 전체 학습,
  locked 평가 접근, 장시간 GPU 작업, 데이터 삭제, 공개 배포, commit/push,
  권한이나 비용이 커지는 작업.
- 사용자가 지정한 파일·CLI·표본이 있으면 그것부터 사용한다. 로컬 smoke 요청을
  데이터셋 규모의 보정이나 학습으로 확대하지 않는다.

## 프로젝트 불변 경계

- 비즈니스 판단과 상태 전이는 application/service에 둔다. FastAPI route, CLI,
  GUI는 입력 파싱, 의존성 조립, 호출, 출력 변환만 담당한다.
- `Protocol` port 뒤의 adapter가 파일시스템, subprocess, 모델 runtime, 네트워크,
  AI Hub, FFmpeg/MediaPipe 같은 외부 효과를 담당한다.
- public API schema, 순수 domain dataclass, serialized artifact schema, 외부
  upstream payload를 혼용하지 않는다.
- Seungun이 오디오 발음 분석과 오류 음소 선택을 소유한다. visual feedback은
  같은 시도의 같은 Seungun anchor를 보충할 뿐 새 오류를 판정하거나 오디오
  결정을 대체하지 않는다.
- 근거, 보정, 권리, 동의, 무결성 또는 신뢰성 조건이 부족하면 해당 기능을
  fail-closed 한다. 선택적 visual/LLM 계층의 실패로 검증된 audio 결과까지
  과장하거나 새 사실을 만들지 않는다.
- 현재 루트 서비스는 무상태이며 DB가 없다. 저장 요구와 개인정보·보존 정책이
  확정되지 않은 상태에서 ORM, repository 또는 MySQL을 도입하지 않는다.

## 연구·ML 실행 규약

- 기본 순서는 `데이터/계약 감사 -> 작은 smoke 실행 -> 결과 평가`다.
- human audit, rights, split, calibration, locked-evaluation 또는 promotion gate를
  우회하는 옵션이나 임시 성공값을 추가하지 않는다.
- train/dev/test와 locked partition의 접근 경계를 유지한다. test/locked 결과를
  재튜닝, threshold 선택 또는 checkpoint 선택에 역사용하지 않는다.
- config의 `TBD_*`, `failed`, `not_evaluable`, `promotion_decision_allowed: false`를
  성공으로 해석하거나 실행 없이 채우지 않는다.
- adapter 존재, synthetic fixture, 다운로드 무결성, 단위 테스트 통과는 모델
  품질·실사용 타당성·production readiness의 증거가 아니다.

## 데이터·보안 규약

- `.env.local`, 원본/파생 음성·영상, AI Hub 제한 데이터, row-level 심사 자료,
  model weight/checkpoint, token과 개인 식별자를 출력·문서화·Git 추적하지 않는다.
- 민감한 로컬 경로는 가능한 한 file descriptor 또는 opaque/hash identifier로
  전달한다. 예외 원문, 절대 경로, 원본 파일명은 public result에 노출하지 않는다.
- 제한 media를 입수한 흐름은 성공/실패와 무관하게 cleanup을 시도하고, cleanup
  실패를 성공으로 숨기지 않는다.
- symlink, path traversal, digest drift, schema/revision 불일치는 fail-closed로
  처리한다.

## 상태와 근거 표현

다음 상태를 서로 바꾸어 말하지 않는다.

- `implemented`: 코드나 계약이 존재함
- `running`: 현재 process/job이 실제 실행 중임
- `verified`: 이번 작업에서 명시한 검증이 성공함
- `failed`: 실행 또는 gate가 실패함
- `blocked`: 필요한 권한·근거·입력 때문에 진행할 수 없음
- `contract-only`: schema/template/fixture만 있고 실제 실행 근거가 없음
- `historical`: 과거 snapshot이며 현재 상태 확인이 필요함

metric이 없거나 사용할 수 없으면 `0`으로 채우지 말고 `missing`,
`low_confidence`, `out_of_distribution`, `backend_unavailable`, `not_evaluable`
같은 명시적 상태를 유지한다.

## 완료 기준과 응답

- 관련 최소 테스트, 계약 검사, source pin 검사 또는 smoke를 실행한다. 전체 검증이
  비싸거나 보호 데이터가 필요하면 실행하지 않은 이유와 대체 검증을 밝힌다.
- 명령의 exit code와 관찰한 결과 없이 성공, readiness, commit 또는 push를
  주장하지 않는다.
- 응답은 작업 유형, 적용 규칙, 결과, 검증, 미확인/실패 항목 순으로 간결하게
  작성한다. 파일 변경은 경로별로 설명한다.

## 로컬 `.codex` 정책

현재 `scripts/check_project_constraints.py`는 `.codex/ai_rule_developer`,
`.codex/ref_docs`, `.codex/codex_start_prompt.txt`를 retired location으로
거부한다. 사용자의 현재 요청에 따라 이 로컬 지침은 편집할 수 있지만, checker를
몰래 약화하거나 이 파일들을 일반 제품 commit에 포함하지 않는다. Git 추적 정책을
바꾸려면 사용자의 별도 요청과 checker·CI의 동시 변경이 필요하다.
