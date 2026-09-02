# 문서화 규칙 - intelligentAI

## 문서 역할

- `.codex/ai_rule_developer`: 로컬 개발 에이전트의 작업 규칙
- `.codex/codex_start_prompt.txt`: 규칙과 프로젝트 문서를 읽게 하는 짧은 시작 지침
- `.codex/ref_docs`: 사용자가 넣는 외부 PRD, 논문, 벤더·아키텍처 참고자료
- `docs/api`: 현재 FastAPI endpoint index와 상세 계약
- `docs/database`: 저장 정책과 DB schema; 현재는 DB 미도입
- `docs/architecture`: 사용자가 유지하도록 요청한 아키텍처 작업 문서
- `docs/video_pronunciation_feedback_production.md`: 현재 영상/Seungun/visual/Clova
  실행 경계와 검증 상태
- `configs`: 실행 가능한 schema, template, 실험·runtime 계약의 정본

프로젝트가 생성한 명세를 `.codex/ref_docs`에 쓰지 않는다. 이 공간의 사용자 자료는
명시적 요청 없이 수정, 이동, 정리 또는 삭제하지 않는다.

## 현재 repository 정책 충돌

`scripts/check_project_constraints.py`는 현재 `.codex/ai_rule_developer`,
`.codex/ref_docs`, `.codex/codex_start_prompt.txt`, `docs/architecture`를 retired
location으로 거부한다. 반면 사용자의 현재 요청은 `.codex` 로컬 규칙을 요구한다.

- 현재 요청에 따라 로컬 `.codex` 문서는 편집할 수 있다.
- 이 예외를 일반 Git 추적 허용으로 확대하지 않는다.
- checker/CI를 통과시키기 위해 문서를 삭제하거나 checker를 몰래 약화하지 않는다.
- 해당 문서를 commit해야 한다는 요청이 오면 먼저 이 정책 충돌을 보고하고,
  checker·tests·문서 위치 정책을 함께 바꿀지 확인한다.

## 근거 기반 작성

- 현재 구현 설명은 관련 코드, tests, config/schema를 확인한 뒤 작성한다.
- 실행 상태·metric·gate 결과는 실제 artifact나 이번 작업의 명령 출력으로
  확인한다.
- `implemented`, `verified`, `failed`, `blocked`, `contract-only`, `historical`을
  구분한다.
- 날짜가 있는 상태 문서는 측정 날짜, 입력 범위, command, exit/status, 제한 사항을
  함께 기록한다.
- adapter, schema, template, synthetic fixture의 존재를 production quality 또는
  실제 데이터 검증으로 표현하지 않는다.
- 외부 자료와 내부 구현이 충돌하면 출처별 사실과 현재 결론을 분리한다.
- 삭제된 과거 문서의 링크를 보고 내용을 추측해 복원하지 않는다.

## 변경과 문서 동기화

다음 변경은 허용된 프로젝트 문서 갱신 필요성을 반드시 검토한다.

- public API path, request/response, auth, status/error
- domain enum, state transition, reason code, schema version
- module ownership, port/adapter, runtime/data flow
- 외부 contract, model/tool revision, timeout/retry/fallback
- 데이터 source, split, rights, privacy, retention/cleanup
- training/evaluation/human/promotion gate와 artifact lineage
- DB 도입, table/column/relation/migration

요청이 `.codex`만으로 제한됐다면 루트 문서를 임의로 고치지 말고 발견한 불일치를
결과에 보고한다.

## API 문서

### `docs/api/endpoints.md`

- method, path, auth, 한 줄 목적만 적는 index다.
- main research API와 G04 loopback review API를 구분한다.
- request/response field나 긴 운영 설명을 넣지 않는다.

### `docs/api/specification.md`

- 각 endpoint의 성격(research-only/loopback/public), auth, params, body, response,
  status/error, side effect, privacy를 기록한다.
- 모든 field의 type, required/optional, null, 범위와 enum을 명시한다.
- `endpoints.md`와 실제 route의 endpoint set을 대조한다.
- Oracle endpoint를 실제 model inference로, loopback token을 production auth로
  설명하지 않는다.

## DB 문서

현재 `docs/database/schema.md`의 정본 상태는 “DB 미도입, 서비스 무상태”다.

- 아키텍처 template의 과거 `mysql` 표기를 실제 구현으로 보지 않는다.
- 영속화 요청 전에는 존재하지 않는 table/entity/repository를 문서화하지 않는다.
- DB 도입 시 목적, owner, 개인정보 최소화, 보존·삭제·철회, transaction,
  migration/rollback을 먼저 기록한다.

## 연구·ML 문서

- dataset ID와 partition, label 의미, speaker split, rights 상태를 명시한다.
- dev/smoke, actual-source, locked test, human audit의 결과를 분리한다.
- metric은 분모, 표본 수, 단위, threshold, confidence/cluster 방법과 함께 기록한다.
- 실패나 `not_evaluable`을 누락하지 않는다.
- `TBD_*` 또는 promotion 금지 상태를 완료로 서술하지 않는다.
- Seungun 상태는 `seungun/README.md`와 실제 gate artifact를 다시 확인한다. 하위
  README의 사라진 루트 문서 링크를 현재 근거로 사용하지 않는다.

## 영상·개인정보 문서

- visual feedback은 Seungun 종속 보충이며 audio 판정을 대체하지 않는다고 명시한다.
- 동의, 제한 workspace, media admission, no-store/loopback, cleanup, failure fallback을
  포함한다.
- 원본 파일명·경로, participant/speaker identifier, token, row-level label, raw
  landmark를 넣지 않는다.
- 로컬 두 영상의 성공을 다양한 기기·조명·화자에 대한 일반화 근거로 확대하지
  않는다.

## 링크와 파일

- Markdown relative link는 실제 존재하는 추적 대상과 현재 정책을 확인한다.
- 제한 artifact나 ignored local file로 연결되는 링크를 공개 문서에 추가하지 않는다.
- 문서 이동·삭제는 `rg`로 inbound reference를 확인한 뒤 수행한다.
- source-pinned config가 문서나 source를 참조하면 digest drift를 함께 검사한다.
- binary PDF/PPTX 내용을 확인하지 못했으면 제목이나 과거 기록만으로 요약하지 않는다.

## 완료 기준

- 변경 문서의 placeholder, 존재하지 않는 경로, 모순된 DB/API 상태를 검색한다.
- 가능한 범위에서 Markdown link 검사와 repository constraint 검사를 실행한다.
- 현재 checker가 로컬 `.codex`를 의도적으로 거부하는 결과는 기존 정책 충돌로
  별도 보고하고, 다른 새 위반과 구분한다.
- 파일별 변경 목적과 검증 결과, 아직 확인하지 못한 사실을 최종 응답에 적는다.
