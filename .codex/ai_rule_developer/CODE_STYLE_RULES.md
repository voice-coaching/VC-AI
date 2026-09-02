# 코드 스타일 규칙 - intelligentAI

## 기준

- Python 3.12 문법과 현재 파일의 스타일을 따른다.
- 루트 `pyproject.toml`은 pytest만 설정한다. 루트 전체에 존재하지 않는 formatter,
  linter 또는 mypy 규칙을 있다고 가정하지 않는다.
- `seungun`은 자체 Ruff(88자), formatter, strict mypy 설정을 가진다. 해당 하위
  프로젝트 수정 시 그 설정을 우선한다.
- 자동 formatter가 없는 루트 코드는 PEP 8 수준의 일반적인 공백을 사용한다.
  임의의 “메서드 간 5칸” 같은 비표준 규칙을 적용하지 않는다.

## Python 파일 구성

- 모듈 첫 docstring은 역할과 중요한 신뢰/실패 경계를 설명한다.
- 프로젝트 코드에서는 `from __future__ import annotations`를 유지한다.
- import는 표준 라이브러리, third-party, local 순으로 그룹화한다.
- 한 파일은 하나의 응집된 계약 또는 유스케이스 영역을 책임진다. 단순 줄 수만으로
  기존 큰 계약 파일을 분해하지 않는다.
- 새 package/directory는 실제 소유권 또는 dependency 경계를 만들 때만 추가한다.
- `__all__`을 사용하는 모듈은 public export 변경을 명시적으로 갱신한다.

## 타입과 모델

- public 함수, service/adapter method, dataclass, Pydantic model과 `Protocol`에는
  명시적 타입을 유지한다.
- `Any`와 `dict[str, Any]`는 JSON/framework/upstream 경계에 한정하고 가능한 빨리
  typed model 또는 `Mapping[str, object]`로 변환한다.
- domain 값은 기존 관례대로 frozen/slots dataclass를 우선한다.
- 상태 문자열은 기존 `StrEnum`, `Enum`, `Literal`을 재사용한다.
- optional field는 `None`의 의미와 상태 조합을 생성 시점에 검증한다.
- 민감한 locator, consent reference, runtime context는 repr에 노출하지 않는다.

## 네이밍

- `attempt`, `prompt`, `phone`, `alignment`, `receipt`, `gate`, `revision`,
  `sha256`처럼 저장소의 도메인 용어를 일관되게 사용한다.
- `expected_phone`과 `observed_candidate`, `score`와 `confidence`, `status`와
  `reason_code`를 이름으로도 구분한다.
- boolean은 `is_*`, `has_*`, `should_*`, `*_allowed`, `*_supported`처럼 조건을
  드러낸다.
- collection은 복수형을 사용하고 단위가 있는 수치는 `_ms`, `_seconds`, `_bytes`,
  `_gib`를 붙인다.
- 기존 stage ID(P6/P8, S1/S2, G04 등)는 계약 용어이므로 임의로 풀어쓰거나 바꾸지
  않는다.

## 함수와 예외

- 함수는 validation, transformation, external call, publish, response mapping 중
  검토 가능한 책임을 갖는다.
- private helper 이름은 `_process`, `_handle`보다 `_validate_snapshot_binding`,
  `_delete_restricted_media`처럼 결과를 드러낸다.
- domain/contract 위반은 구체 `ValueError` subclass 또는 프로젝트 예외로 표현한다.
- 넓은 예외 포착은 최종 fail-closed/fallback/cleanup 경계에서만 허용하고 이유를
  주석과 테스트로 고정한다.
- 보안상 public error에 exception text를 포함하지 않는다. 내부 진단이 필요하면
  민감 정보가 제거된 별도 logging 정책을 사용한다.
- retry, fallback, no-overwrite, atomic replace를 helper 안에 숨기지 않는다.

## 직렬화와 파일

- JSON은 UTF-8, 안정된 schema version, `allow_nan=False`를 사용한다.
- canonical JSON/digest를 계산하는 기존 separator, key sort, encoding 규칙을
  변경하지 않는다.
- `Path`를 사용하되 public JSON에는 basename조차 계약에 필요한 경우만 노출한다.
- 제한 경로는 resolve 문자열 비교만으로 안전하다고 가정하지 않고 기존
  descriptor-relative/path containment 도구를 재사용한다.
- artifact 쓰기는 기존 원자성, file mode, owner, link-count, fsync와 no-overwrite
  정책을 보존한다.

## Subprocess와 CLI

- CLI는 argparse parsing, dependency composition, service 호출, JSON/text rendering만
  담당한다.
- subprocess는 list argv, 명시적 timeout, bounded stdout/stderr, process cleanup을
  사용한다.
- shell escaping으로 민감 경로나 token을 조립하지 않는다.
- 사용자 media path가 민감하면 기존 `--video-fd` 같은 descriptor 입력을 우선한다.
- CLI exit code와 JSON status를 모두 검증한다.

## 주석과 TODO

- 주석은 코드가 보이지 않는 권리, provenance, gate, score semantics, failure policy,
  보안 이유를 설명한다.
- 구현을 그대로 읽어주는 주석은 추가하지 않는다.
- `TODO`는 누락된 근거/계약, 필요한 승인이나 선행 artifact, 완료 판정 조건을
  포함한다.
- 과거 실행 결과를 현재 사실처럼 코드 주석에 고정하지 않는다.

## 테스트 스타일

- 변경된 contract와 실패 경로를 가장 가까운 unit test에 추가한다.
- 실제 제한 데이터, model weight, network가 없어도 base test가 실행 가능해야 한다.
- optional backend test는 import/skip 조건과 검증 범위를 명확히 한다.
- fixture 성공을 production quality로 표현하지 않는다.
- root 변경은 `.venv/bin/python -m pytest` 또는 CI의 명시적 subset을 기준으로 하고,
  `seungun` 변경은 해당 디렉터리의 pytest/Ruff/mypy를 별도로 실행한다.
