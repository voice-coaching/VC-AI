# 코드 스타일 규칙 — intelligentAI

인접 소스와 실제 formatter/linter 설정을 먼저 따른다. [pyproject.toml](../../pyproject.toml)에는 pytest 설정이 있으며 formatter/linter 설정은 없다. 문서 작업으로 새 도구·설정을 도입하지 않는다.

- 기존 파일의 들여쓰기·import 그룹·타입·docstring 관례를 유지한다. ‘메서드 간 5칸/블록 간 3칸’ 같은 임의 공백 규칙을 적용하지 않는다.
- 함수·모듈은 도메인 의도와 하나의 책임을 드러내게 한다. 검증·상태 판단·side effect·응답 변환 경계를 유지한다.
- 공개 함수·port·DTO는 모듈 관례에 맞는 명시적 타입을 유지한다. optional/null 의미를 숨기거나 외부 계약을 느슨하게 바꾸지 않는다.
- 프로젝트 용어와 인접 명명 규칙을 따른다. 무관한 rename·전체 format 정리·파일 재배치를 섞지 않는다.
- 주석/docstring은 근거 정책·제약·실패/복구·동시성의 이유를 설명한다. 코드 반복 주석이나 모든 private helper의 의무 주석을 추가하지 않는다.
- 사용자 발화·파일 경로·token을 로그에 넣지 않는다. 닫힌 오류 계약과 private 상세/public 응답 구분을 유지한다.
- import만으로 model/GPU/미디어를 실행하지 않도록 선택 의존성·격리 경계를 보존한다.

변경 시 [문서 규칙](DOCUMENT_RULE.md)에 따라 정본을 갱신한다. QA·자동 회귀 실행/생성 금지는 전역 규칙을 따른다.
