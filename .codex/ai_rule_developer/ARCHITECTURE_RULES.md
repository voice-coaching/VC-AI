# 아키텍처 규칙 - intelligentAI

이 프로젝트는 기능 우선 계층형 아키텍처를 따른다. 책임과 의존성 방향을 반드시 분리한다.

[디렉토리 구조]
- `src/<package_name>/<feature>/services`
- `src/<package_name>/<feature>/adapters`
- `src/<package_name>/<feature>/models`
- `src/<package_name>/shared`
- `tests`
- `scripts`

[기능 모듈 배치]
- `login`, `post`, `talk` 같은 기능/도메인 폴더를 바깥 경계로 우선한다.
- 활성 profile에서 사용하는 `controllers`, `services`, `repositories`, `schemas`, `entities`, `components`, `hooks` 같은 계층 폴더는 각 기능 폴더 안에 둔다.
- 여러 기능에서 공유하는 설정, 공통 middleware, 공통 client만 shared/core/infrastructure에 둔다.

[계층 책임]
- Presentation/API 계층은 요청 파싱, 응답 변환, 라우팅, 화면 조립만 담당한다.
- Service/Application 계층은 유스케이스 조합과 비즈니스 판단을 담당한다.
- Repository/Adapter/Infrastructure 계층은 DB 접근과 외부 시스템 접근을 담당한다.
- Entity/Domain model은 저장 또는 도메인 상태를 표현하며 public response 객체로 직접 사용하지 않는다.
- Schema/DTO/View model은 외부 계약을 표현하며 persistence model과 분리한다.

[의존성 방향]
- 의존성은 Presentation/API/UI 계층에서 Service/Application, Repository/Adapter, Infrastructure 방향으로 흐른다.
- 상위 계층은 명시적 인터페이스 또는 좁은 모듈을 통해 하위 계층을 호출한다.
- 하위 계층은 UI, HTTP 응답, request, framework 객체를 상위 계층에서 가져오지 않는다.

[프로필별 아키텍처]

### Python
- 패키지는 기능 또는 도메인 책임 기준으로 나누고 각 기능 폴더 하위에 service, adapter, model 등을 둔다.
- 공통 package는 여러 기능이 공유하는 정책, 설정, utility에만 사용한다.
- CLI 또는 실행 진입점은 입력 파싱과 출력 변환만 담당하고 실제 판단은 service/application 계층에 위임한다.
- 핵심 도메인 함수와 클래스는 파일 시스템, 네트워크, 환경 변수, process args에 직접 의존하지 않게 한다.
- 파일, DB, 외부 API, subprocess 연동은 adapter 또는 infrastructure 모듈 뒤에 둔다.
- 권장 흐름: Entrypoint/CLI -> Application Service -> Domain/Adapter -> External System.

## 외부 연동 계층
- 외부 HTTP/SDK 연동은 client 또는 adapter 모듈 뒤에 둔다.
- Service는 비즈니스 의도를 드러내는 좁은 메서드로 adapter를 호출한다.
- Upstream DTO와 내부 DTO를 분리한다.
